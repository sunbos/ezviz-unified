"""Positive and negative tests for SDK OAuth refresh-token exchange (sync and async)."""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest

from ezviz_unified import DiagnosticRecorder, OAuthRefreshResult
from ezviz_unified.oauth import AsyncOAuth, OAuth

KEY = "synthetic-key+finite"
OLD = "old//refresh:sentinel~02"
OPEN = "open//subject:sentinel~03"
BUNDLE = "bundle//pkg:sentinel~04"
AT = "access//minted:sentinel~05"
RT = "refresh//minted:sentinel~06"
STATE = "state//nonce:sentinel~07"
SENTINELS = (KEY, OLD, OPEN, BUNDLE, AT, RT, STATE)

AUTH = "https://isgpopenauth.ezvizlife.com"
API = "https://isgpopen.ezvizlife.com"
MODES = ("sync", "async")
PHASES = [
    ("call", "started"),
    ("attempt", "started"),
    ("attempt", "succeeded"),
    ("call", "succeeded"),
]


def reply(**data):
    return {
        "code": "200",
        "msg": "ok",
        "data": {"access_token": AT, "refresh_token": RT, **data},
    }


class Tracking(httpx.MockTransport):
    def __init__(self, handler):
        super().__init__(handler)
        self.requests = []
        self.closed = False

    def handle_request(self, request):
        self.requests.append(request)
        return super().handle_request(request)

    async def handle_async_request(self, request):
        self.requests.append(request)
        return await super().handle_async_request(request)

    def close(self):
        self.closed = True
        super().close()

    async def aclose(self):
        self.closed = True
        await super().aclose()


def exchange(mode, transport, recorder=None, **overrides):
    params = {"refresh_token": OLD, "open_id": OPEN, "bundle_id": BUNDLE}
    params.update(overrides)
    cls = OAuth if mode == "sync" else AsyncOAuth
    client = cls(KEY, auth_origin=AUTH, api_origin=API, diagnostic_recorder=recorder)
    call = client.refresh_token(transport=transport, **params)
    return call if mode == "sync" else asyncio.run(call)


def form(request):
    pairs = parse_qsl(request.content.decode(), keep_blank_values=True)
    assert len(pairs) == len({key for key, _ in pairs})
    return dict(pairs)


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    def blocked(self, request):
        raise AssertionError("unmocked sync transport")

    async def ablocked(self, request):
        raise AssertionError("unmocked async transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", ablocked)


@pytest.mark.parametrize("mode", MODES)
def test_refresh_posts_exact_form(mode):
    transport = Tracking(lambda request: httpx.Response(200, json=reply(state="")))
    result = exchange(mode, transport, scope="", state="")
    assert isinstance(result, OAuthRefreshResult)
    assert transport.closed
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert str(request.url) == AUTH + "/oauth/token/refreshToken"
    assert not request.url.params
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    for name in ("authorization", "cookie", "accessToken"):
        assert name not in request.headers
    body = request.content.decode()
    assert "%" in body and OLD not in body
    assert form(request) == {
        "grant_type": "refresh_token",
        "client_id": KEY,
        "open_id": OPEN,
        "bundleId": BUNDLE,
        "refresh_token": OLD,
        "scope": "",
        "state": "",
    }


@pytest.mark.parametrize("mode", MODES)
def test_optionals_omitted_and_result_defaults(mode):
    transport = Tracking(lambda request: httpx.Response(200, json=reply()))
    result = exchange(mode, transport)
    sent = form(transport.requests[0])
    assert "scope" not in sent and "state" not in sent
    assert result.access_token == AT and result.refresh_token == RT
    assert result.expires_in is None
    assert result.open_id is None
    assert result.scope is None
    assert result.state is None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("expires_in", [0, 4_294_967_295])
def test_result_uses_returned_fields(mode, expires_in):
    open_id = "open//echoed:other~08"
    scope = "scope//echoed:other~08"
    payload = reply(expires_in=expires_in, openId=open_id, scope=scope, state=STATE, extra="drop")
    transport = Tracking(lambda request: httpx.Response(200, json=payload))
    result = exchange(mode, transport, scope=f"sent:{scope}", state=STATE)
    assert result.access_token == AT and result.refresh_token == RT
    assert result.expires_in == expires_in
    assert result.open_id == open_id
    assert result.scope == scope
    assert result.state == STATE
    assert not hasattr(result, "extra")


@pytest.mark.parametrize("mode", MODES)
def test_redaction_and_recorder_report(mode):
    recorder = DiagnosticRecorder()
    transport = Tracking(lambda request: httpx.Response(200, json=reply(state=STATE)))
    result = exchange(mode, transport, recorder=recorder, state=STATE)
    text = recorder.to_json()
    found = recorder.report()["events"]
    assert [(e["kind"], e["outcome"]) for e in found] == PHASES
    assert found[0]["entrypoint"] == "oauth.refresh_token"
    assert found[1]["operation"] == "global.oauth.refresh_token"
    assert found[2]["operation"] == "global.oauth.refresh_token"
    blob = str(result) + repr(result) + text
    for marker in SENTINELS:
        assert marker not in blob


@pytest.mark.parametrize("code", ("synthetic-unknown-code", 918273645), ids=("str", "int"))
def test_decode_response_redacts_opted_in_unknown_code(code):
    from ezviz_unified import ResponsePolicy, decode_response
    from ezviz_unified.errors import REDACTED

    payload = {"code": code, "msg": "synthetic message", "data": {"echo": "synthetic"}}

    policy = ResponsePolicy(success_values=(code,), redact_oauth_details=True)
    result = decode_response(payload, policy=policy)
    assert result.code == REDACTED
    assert str(code) not in repr(result)
    assert result.raw == payload
    assert result.data == payload["data"]

    plain = decode_response(payload, policy=ResponsePolicy(success_values=(code,)))
    assert plain.code == code
    assert type(plain.code) is type(code)
    assert plain.raw == payload
    assert plain.data == payload["data"]

    with pytest.raises(ValueError):
        ResponsePolicy(redact_oauth_details="yes")


@pytest.mark.parametrize("mode", ("operation", "code_path"))
def test_decode_response_redacts_caller_supplied_secret(mode):
    from ezviz_unified import ProtocolError, ResponsePolicy, decode_response

    marker = "synthetic-secret-marker-7f3a9c2e"
    if mode == "operation":
        policy = ResponsePolicy(redact_oauth_details=True)
        operation = marker
    else:
        policy = ResponsePolicy(code_path=marker, redact_oauth_details=True)
        operation = "synthetic-benign-operation"

    with pytest.raises(ProtocolError) as excinfo:
        decode_response(
            {},
            operation=operation,
            secrets=(marker,),
            policy=policy,
        )

    error = excinfo.value
    assert marker not in (error.message or "")
    assert marker not in (error.operation or "")
    assert marker not in str(error)
    assert marker not in repr(error)


MARKER = "MK-secret-marker"


def _fail_body(code):
    return {"code": code, "msg": MARKER, "data": {"access_token": AT, "refresh_token": RT}}


def _assert_masked(exc, recorder, *markers):
    blob = recorder.to_json() + str(exc) + repr(exc) + repr(vars(exc))
    for linked in (exc.__cause__, exc.__context__):
        if linked is not None:
            blob += str(linked) + repr(linked) + repr(vars(linked))
    for marker in (MARKER, AT, RT, *markers):
        assert marker not in blob


_CASES = [
    (code, kind)
    for kind, codes in {
        "auth": ("70003", 70003, "70004", 70004),
        "perm": ("10023", 10023, "70005", 70005, "70006", 70006),
        "api": ("10001", 10001, "10017", 10017, "49999", 49999),
    }.items()
    for code in codes
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("code,kind", _CASES)
def test_refresh_known_code_mapping(mode, code, kind):
    from ezviz_unified import ApiError, AuthenticationError, PermissionDeniedError

    want = {"auth": AuthenticationError, "perm": PermissionDeniedError}.get(kind, ApiError)
    rec = DiagnosticRecorder()
    transport = Tracking(lambda req: httpx.Response(200, json=_fail_body(code)))
    with pytest.raises(want) as err:
        exchange(mode, transport, recorder=rec)
    assert type(err.value) is want
    assert err.value.code == code and type(err.value.code) is type(code)
    assert len(transport.requests) == 1 and transport.closed
    _assert_masked(err.value, rec)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("code", ["bogus-code", 4242])
def test_refresh_unknown_code_masked(mode, code):
    from ezviz_unified import ApiError

    rec = DiagnosticRecorder()
    transport = Tracking(lambda req: httpx.Response(200, json=_fail_body(code)))
    with pytest.raises(ApiError) as err:
        exchange(mode, transport, recorder=rec)
    assert type(err.value) is ApiError
    assert len(transport.requests) == 1 and transport.closed
    _assert_masked(err.value, rec, str(code))


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", [401, 403])
def test_refresh_http_status_priority(mode, status):
    from ezviz_unified import AuthenticationError, PermissionDeniedError

    want = AuthenticationError if status == 401 else PermissionDeniedError
    transport = Tracking(
        lambda req: httpx.Response(status, json=_fail_body(10023 if status == 401 else 70003))
    )
    with pytest.raises(want):
        exchange(mode, transport)
    assert len(transport.requests) == 1 and transport.closed


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", [307, 503])
def test_refresh_never_follows_or_overrides(mode, status):
    from ezviz_unified import ApiError

    headers = {"location": "https://example.test/next"} if status == 307 else None
    transport = Tracking(lambda req: httpx.Response(status, json=reply(), headers=headers))
    with pytest.raises(ApiError):
        exchange(mode, transport)
    assert len(transport.requests) == 1 and transport.closed


@pytest.mark.parametrize("mode", MODES)
def test_refresh_timeout_has_no_context(mode):
    from ezviz_unified import TransportError

    sentinel = "sentinel-timeout-boom"

    def handler(req):
        raise httpx.TimeoutException(sentinel)

    transport = Tracking(handler)
    with pytest.raises(TransportError) as err:
        exchange(mode, transport)
    assert sentinel not in repr(err.value.__cause__)
    assert sentinel not in repr(err.value.__context__)
    assert len(transport.requests) == 1 and transport.closed


def _hit(mode, env, **kw):
    from ezviz_unified.errors import ProtocolError

    t = Tracking(lambda r: httpx.Response(200, json=env))
    with pytest.raises(ProtocolError):
        exchange(mode, t, **kw)
    assert len(t.requests) == 1 and t.closed


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "raw",
    [
        b"{x",
        b"[]",
        b"{}",
        b'"s"',
        b"7",
        b'{"code":"200","data":"x"}',
        b'{"code":"200","data":[]}',
    ],
)
def test_wire_shape_rejected(mode, raw):
    from ezviz_unified.errors import ProtocolError

    t = Tracking(lambda r: httpx.Response(200, content=raw))
    with pytest.raises(ProtocolError):
        exchange(mode, t)
    assert len(t.requests) == 1 and t.closed


@pytest.mark.parametrize("mode", MODES)
def test_wire_numeric_code_rejected(mode):
    from ezviz_unified.errors import ApiError, ProtocolError

    env = reply()
    env["code"] = 200
    t = Tracking(lambda r: httpx.Response(200, json=env))
    with pytest.raises((ApiError, ProtocolError)):
        exchange(mode, t)
    assert len(t.requests) == 1 and t.closed


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("key", ["access_token", "refresh_token"])
def test_wire_token_missing(mode, key):
    env = reply()
    del env["data"][key]
    _hit(mode, env)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "key,val",
    [
        ("access_token", ""),
        ("refresh_token", " "),
        ("access_token", 5),
        ("refresh_token", None),
        ("access_token", True),
        ("refresh_token", []),
    ],
)
def test_wire_token_rejected(mode, key, val):
    env = reply()
    env["data"][key] = val
    _hit(mode, env)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "key,val",
    [
        ("openId", 1),
        ("scope", []),
        ("state", {}),
        ("expires_in", -1),
        ("expires_in", True),
        ("expires_in", "3"),
    ],
)
def test_wire_optional_rejected(mode, key, val):
    _hit(mode, reply(**{key: val}))


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("val", ["DROP", "nope"])
def test_wire_state_mismatch(mode, val):
    env = reply()
    env["data"].pop("state", None)
    if val != "DROP":
        env["data"]["state"] = val
    _hit(mode, env, state="expected")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "key,val",
    [
        ("open_id", ""),
        ("open_id", 3),
        ("open_id", "a\x00b"),
        ("open_id", "\ud800"),
        ("bundle_id", ""),
        ("bundle_id", []),
        ("bundle_id", "c\x1f"),
        ("bundle_id", "\udfff"),
        ("refresh_token", ""),
        ("refresh_token", " "),
        ("refresh_token", 5),
        ("refresh_token", []),
        ("scope", 1),
        ("scope", "s\x1ft"),
        ("state", []),
        ("state", "\ud800"),
        ("timeout", 0),
        ("timeout", -1),
        ("timeout", float("nan")),
        ("timeout", float("inf")),
        ("timeout", True),
    ],
)
def test_args_rejected_presend(mode, key, val):
    t = Tracking(lambda r: httpx.Response(200, json=reply()))
    with pytest.raises((TypeError, ValueError)):
        exchange(mode, t, **{key: val})
    assert t.requests == []
