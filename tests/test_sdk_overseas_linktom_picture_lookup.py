"""Offline coverage of overseas lookup of a previously supplied picture reference."""

import asyncio
import logging
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_device_utc import API_HOST, AUTH_HOST, TOKEN, _client
from test_sdk_snapshots import call, make

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    ResponsePolicy,
    TransportError,
)
from ezviz_unified.transport import AsyncTransport, SyncTransport

OP = "get_by_uuid"
PATH = "/api/lapp/device/uuid/picture"
UUID = "  不规则 +%&=/ MIXED_UUID_PRIVATE  "
REF = " synthetic/path +/REFERENCE_PRIVATE "
MSG = "UPSTREAM_PRIVATE"
MISS = object()
GOOD = {"code": "200", "msg": MSG, "data": {"picUrl": REF, "extension": ["EXT_PRIVATE", None]}}


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def invoke(mode, *steps, capture_uuid=UUID, size=640, region=None):
    replies = iter(steps)

    def business(req):
        step = next(replies)
        if isinstance(step, BaseException):
            raise step
        return step if isinstance(step, httpx.Response) else httpx.Response(200, json=step)

    client, rec, seen = _client(mode, biz=business, region=region)

    def run():
        if mode == "sync":
            with client:
                return client.snapshots.get_by_uuid(capture_uuid, size=size)

        async def execute():
            async with client:
                return await client.snapshots.get_by_uuid(capture_uuid, size=size)

        return asyncio.run(execute())

    return run, rec, seen


def outcomes(rec, mode, kind):
    key = "entrypoint" if kind == "call" else "operation"
    return [
        e["outcome"]
        for e in rec.report()["events"]
        if e["kind"] == kind and e["mode"] == mode and e[key] == "snapshots.get_by_uuid"
    ]


def assert_wire(req, token, uuid, size):
    assert (
        req.method == "POST"
        and req.url.scheme == "https"
        and req.url.host == API_HOST
        and req.url.path == PATH
        and req.url.query == b""
    )
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert set(req.headers).isdisjoint(
        {"accesstoken", "authorization", "uuid", "size", "deviceserial"}
    )
    assert sorted(parse_qsl(req.content.decode(), keep_blank_values=True)) == sorted(
        [("accessToken", token), ("uuid", uuid), ("size", str(size))]
    )


@pytest.mark.parametrize("size", [0, 1, 640, 1280])
@pytest.mark.parametrize("ref", [REF, "", "mock-relative.ext", "https://ref.invalid/REF_PRIVATE"])
def test_lookup(mode, size, ref):
    data = {**GOOD["data"], "picUrl": ref}
    body = {**GOOD, "data": data, "unknown": {"keep": True}, "result": {"code": "10002"}}
    run, rec, seen = invoke(mode, body, size=size)
    res = run()
    assert res.code == "200" and res.raw == body and res.data == data and res.message == "***"
    (auth,), (biz,) = seen["auth"], seen["biz"]
    assert (
        auth.url.host == AUTH_HOST != API_HOST
        and auth.url.scheme == "https"
        and auth.method == "POST"
        and auth.url.path == "/api/lapp/token/get"
    )
    assert_wire(biz, TOKEN, UUID, size)
    assert outcomes(rec, mode, "call") == ["started", "succeeded"] and outcomes(
        rec, mode, "attempt"
    ) == ["started", "succeeded"]


def test_domestic_guard_precedes_validation(mode):
    errors = []
    for uuid, size in ((UUID, 640), (object(), object())):
        run, r, seen = invoke(mode, capture_uuid=uuid, size=size, region=RegionConfig.china())
        with pytest.raises(ValueError) as exc:
            run()
        errors.append(str(exc.value))
        assert seen == {"biz": [], "auth": []} and outcomes(r, mode, "attempt") == []
        assert outcomes(r, mode, "call") == ["started", "failed"]
    assert errors[0] == errors[1]


@pytest.mark.parametrize(
    "uuid,size",
    [
        (None, 640),
        (b"uuid", 640),
        (1, 640),
        ("", 640),
        ("  ", 640),
        ("\ud800", 640),
        (UUID, -1),
        (UUID, 1281),
        (UUID, True),
        (UUID, False),
        (UUID, 640.0),
        (UUID, "640"),
        (UUID, None),
    ],
)
def test_invalid_input(mode, uuid, size):
    run, rec, seen = invoke(mode, capture_uuid=uuid, size=size)
    with pytest.raises(ValueError):
        run()
    assert seen == {"auth": [], "biz": []} and not outcomes(rec, mode, "attempt")


@pytest.mark.parametrize(
    "args,kw", [((), {}), ((640,), {}), ((), {"width": 640}), ((), {"size": 640, "channel": 1})]
)
def test_public_signature(mode, args, kw):
    facade, rec = make(mode, "overseas")
    with pytest.raises(TypeError):
        call(mode, facade, OP, *args, serial=UUID, **kw)
    assert rec.calls == []


BAD_DATA = [
    MISS,
    None,
    ["unexpected"],
    {},
    {"pictureUrl": "unexpected"},
    {"data": {"picUrl": "wrong-level"}},
    {"picUrl": None},
    {"picUrl": False},
    {"picUrl": 1},
    {"picUrl": 0.0},
    {"picUrl": []},
    {"picUrl": {}},
]


@pytest.mark.parametrize("data", BAD_DATA)
def test_invalid_data(mode, data):
    reply = {"code": "200", "msg": MSG}
    if data is not MISS:
        reply["data"] = data
    run, rec, seen = invoke(mode, reply)
    with pytest.raises(ProtocolError):
        run()
    assert len(seen["auth"]) == len(seen["biz"]) == 1 and not outcomes(rec, mode, "retry")


@pytest.mark.parametrize(
    "reply,expected",
    [
        ({"result": {"code": "200"}}, ProtocolError),
        ({"body": {"meta": {"code": 200}}}, ProtocolError),
        ({"code": True}, ProtocolError),
        ({"code": 200.0}, ProtocolError),
        ({"code": 200}, ApiError),
    ],
)
def test_wrong_envelope(mode, reply, expected):
    run, _, seen = invoke(mode, {**reply, "data": {"picUrl": REF}})
    with pytest.raises(expected) as exc:
        run()
    assert type(exc.value) is expected and len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("expired_code", [10002, "10002"])
@pytest.mark.parametrize("exhausted", [False, True])
def test_bounded_refresh(mode, exhausted, expired_code, monkeypatch):
    values = iter(("TOKEN1_PRIVATE", "TOKEN2_PRIVATE"))

    def auth_ok(request):
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": next(values),
                    "expireTime": 9999999999999,
                    "areaDomain": "https://" + API_HOST,
                },
            },
        )

    monkeypatch.setattr("test_sdk_device_utc._auth_ok", auth_ok)
    stale = {"code": expired_code, "msg": MSG}
    run, rec, seen = invoke(mode, stale, stale if exhausted else GOOD)
    if exhausted:
        with pytest.raises(AuthenticationError) as exc:
            run()
        assert exc.value.code == expired_code and type(exc.value.code) is type(expired_code)
    else:
        assert run().raw == GOOD
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    for req, token in zip(seen["biz"], ("TOKEN1_PRIVATE", "TOKEN2_PRIVATE"), strict=True):
        assert_wire(req, token, UUID, 640)
    assert outcomes(rec, mode, "retry") == ["scheduled"]


VISIBLE = (
    "10001",
    "10005",
    "20301",
    "20302",
    "20303",
    "49999",
    "400",
    "403",
    "404",
    "500",
    "10031",
)


@pytest.mark.parametrize("code", [typed(x) for x in VISIBLE for typed in (str, int)] + [200])
def test_documented_errors(mode, code):
    run, rec, seen = invoke(mode, {"code": code, "msg": MSG})
    expected = PermissionDeniedError if str(code) == "10031" else ApiError
    with pytest.raises(expected) as exc:
        run()
    assert (
        type(exc.value) is expected
        and exc.value.code == code
        and type(exc.value.code) is type(code)
    )
    assert (
        exc.value.message == "***"
        and len(seen["auth"]) == len(seen["biz"]) == 1
        and not outcomes(rec, mode, "retry")
    )


@pytest.mark.parametrize(
    "http_status,code,expected",
    [
        (200, "UNTRUSTED_CODE", ApiError),
        (200, 93174625, ApiError),
        (403, "UNTRUSTED_CODE", PermissionDeniedError),
        (503, MISS, ApiError),
    ],
)
def test_private_errors(mode, http_status, code, expected, caplog):
    body = {"msg": MSG + UUID + REF, "data": {"picUrl": REF}}
    if code is not MISS:
        body["code"] = code
    run, rec, seen = invoke(
        mode,
        httpx.Response(
            http_status,
            json=body,
            extensions={"http_version": b"VERSION_PRIVATE", "reason_phrase": b"REASON_PRIVATE"},
        ),
    )
    with caplog.at_level("INFO", logger="httpx"), pytest.raises(expected) as exc:
        run()
    assert (
        type(exc.value) is expected
        and exc.value.http_status == http_status
        and exc.value.message == "***"
    )
    assert exc.value.code == (None if code is MISS else "***")
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    visible = " ".join((str(exc.value), repr(exc.value), repr(rec.report()), caplog.text))
    for marker in (
        "UUID_PRIVATE",
        "REFERENCE_PRIVATE",
        MSG,
        TOKEN,
        "VERSION_PRIVATE",
        "REASON_PRIVATE",
        "UNTRUSTED_CODE",
        "93174625",
    ):
        assert marker not in visible


def test_raw_success_and_hidden_representations(mode, caplog):
    run, rec, _ = invoke(
        mode,
        httpx.Response(
            200,
            json=GOOD,
            extensions={"http_version": b"VERSION_PRIVATE", "reason_phrase": b"REASON_PRIVATE"},
        ),
    )
    with caplog.at_level("INFO", logger="httpx"):
        res = run()
    assert res.raw == GOOD and res.data == GOOD["data"] and res.message == "***"
    shown = " ".join((str(res), repr(res), res.message, repr(rec.report()), caplog.text))
    for marker in (
        "UUID_PRIVATE",
        "REFERENCE_PRIVATE",
        MSG,
        TOKEN,
        "VERSION_PRIVATE",
        "REASON_PRIVATE",
        "EXT_PRIVATE",
    ):
        assert marker not in shown
    assert any(
        '"HTTP 200 ***"' in rec.getMessage() for rec in caplog.records if rec.name == "httpx"
    )


def test_timeout(mode):
    run, rec, seen = invoke(mode, httpx.ReadTimeout("TIMEOUT_PRIVATE"))
    with pytest.raises(TransportError) as exc:
        run()
    assert exc.value.reason == "timeout" and len(seen["auth"]) == len(seen["biz"]) == 1


def test_cancellation_propagates():
    run, rec, seen = invoke("async", asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        run()
    assert len(seen["auth"]) == len(seen["biz"]) == 1 and outcomes(rec, "async", "call") == [
        "started",
        "cancelled",
    ]


def log_reply(version, reason):
    return httpx.Response(
        200,
        json={"code": "200"},
        extensions={"http_version": version.encode(), "reason_phrase": reason.encode()},
    )


def transport_case(mode, policy, business):
    if mode == "sync":
        with SyncTransport(
            "https://fixture.invalid", transport=httpx.MockTransport(business)
        ) as sdk:
            return sdk.request("GET", "/owned", policy=policy)

    async def run():
        async with AsyncTransport(
            "https://fixture.invalid", transport=httpx.MockTransport(business)
        ) as sdk:
            return await sdk.request("GET", "/owned", policy=policy)

    return asyncio.run(run())


@pytest.mark.parametrize("version", ["HTTP/1.0", "HTTP/1.1", "HTTP/2", "HTTP/3", "VERSION_PRIVATE"])
def test_sdk_owned_version(mode, version, caplog):
    policy = ResponsePolicy(redact_device_add_details=True, redact_media_urls=True)
    with caplog.at_level(logging.INFO, logger="httpx"):
        transport_case(mode, policy, lambda req: log_reply(version, "REASON_PRIVATE"))
    lines = [
        r.getMessage() for r in caplog.records if r.name == "httpx" and r.levelno == logging.INFO
    ]
    (line,) = lines
    expected = "HTTP" if version == "VERSION_PRIVATE" else version
    assert f'"{expected} 200 ***"' in line and "REASON_PRIVATE" not in line
    if version == "VERSION_PRIVATE":
        assert version not in line


@pytest.mark.parametrize(
    "policy,reason_shown",
    [
        (None, True),
        (ResponsePolicy(redact_device_add_details=True), False),
        (ResponsePolicy(redact_media_urls=True), True),
    ],
)
def test_other_policies_keep_version(mode, policy, reason_shown, caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        transport_case(mode, policy, lambda req: log_reply("OTHER_VERSION", "OTHER_REASON"))
    (line,) = [
        r.getMessage() for r in caplog.records if r.name == "httpx" and r.levelno == logging.INFO
    ]
    assert "OTHER_VERSION" in line and ("OTHER_REASON" in line) == reason_shown


def test_same_url_native_client_preserves_version(mode, caplog):
    def outer(req):
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: log_reply("NATIVE_VERSION", "NATIVE_REASON")),
            trust_env=False,
        ) as raw:
            raw.get(str(req.url))
        return log_reply("OWNED_VERSION_PRIVATE", "OWNED_REASON_PRIVATE")

    with caplog.at_level(logging.INFO, logger="httpx"):
        transport_case(
            mode, ResponsePolicy(redact_device_add_details=True, redact_media_urls=True), outer
        )
    lines = [
        r.getMessage() for r in caplog.records if r.name == "httpx" and r.levelno == logging.INFO
    ]
    assert len(lines) == 2 and any(
        "NATIVE_VERSION" in line and "NATIVE_REASON" in line for line in lines
    )
    assert "OWNED_VERSION_PRIVATE" not in caplog.text and "OWNED_REASON_PRIVATE" not in caplog.text
