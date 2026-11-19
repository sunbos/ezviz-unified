"""Contract tests for the domestic device voice provision/delete writes.

Synthetic data only: real Client/AsyncClient mounted on fixed
httpx.MockTransport handlers; no live endpoints, credentials or media URLs.
"""

import asyncio
import json
import logging
import sys
from urllib.parse import quote, quote_plus

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

PATH = "/api/route/voice/v3/devices/voices"
APP_KEY = "SYNTHKEY"
APP_SECRET = "SYNTHSECRET"
ACCESS_TOKEN = "SYNTH_TOKEN"
DEVICE_SERIAL = " C-AbC_01324 "
VOICE_NAME = "  晚安 Voice_é "
VOICE_URL = "https://media.invalid/material/晚 安 voice.mp3?sig=SYN-SIG&e=1"
# Rare synthetic decimals: distinct enough that a substring match in any
# rendered surface is a genuine leak, not an accidental digit collision.
CREATE_VOICE_ID = 730051184201
DELETE_VOICE_ID = 460094820713
AUTH_BODY = {
    "code": "200",
    "data": {
        "accessToken": ACCESS_TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
CLIENTS = pytest.mark.parametrize("client_cls", [Client, AsyncClient])
METHODS = {"create": "POST", "delete": "DELETE"}


def _session(client_cls, business, region=None):
    recorder = DiagnosticRecorder()
    calls = {"auth": [], "biz": []}

    def auth(request):
        calls["auth"].append(request)
        return httpx.Response(200, json=AUTH_BODY)

    def biz(request):
        calls["biz"].append(request)
        return business(request)

    client = client_cls(
        APP_KEY,
        APP_SECRET,
        region=region or RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(biz),
        diagnostic_recorder=recorder,
    )
    return client, recorder, calls


def _mode(client_cls):
    return "sync" if client_cls is Client else "async"


def _invoke(client, client_cls, operation, *args, **kwargs):
    """Issue one operation inside the client's own context, then close it.

    Each mock test allocates a fresh open client; entering its context here
    guarantees cleanup while the client stays open for the duration of the
    call, so a rejection is still a genuine pre-auth validation error (raised
    before any credential or transport use) rather than a closed-client guard.
    """
    if client_cls is Client:
        with client:
            return getattr(client.device_voice, operation)(*args, **kwargs)

    async def run():
        async with client:
            return await getattr(client.device_voice, operation)(*args, **kwargs)

    return asyncio.run(run())


def _events(recorder, kind=None):
    events = recorder.report()["events"]
    return [e for e in events if kind is None or e.get("kind") == kind]


def _call_events(recorder, entrypoint, mode):
    return [
        e
        for e in _events(recorder, "call")
        if e.get("entrypoint") == entrypoint and e.get("mode") == mode
    ]


def _attempt_events(recorder, mode, operation=None):
    events = [
        e
        for e in _events(recorder, "attempt")
        if e.get("entrypoint") == "client.request" and e.get("mode") == mode
    ]
    if operation is not None:
        events = [e for e in events if e.get("operation") == operation]
    return events


def _assert_call_scope(recorder, entrypoint, mode, outcomes):
    events = _call_events(recorder, entrypoint, mode)
    assert [e.get("outcome") for e in events] == outcomes


def _assert_attempts(recorder, operation, mode, outcomes):
    events = _attempt_events(recorder, mode, operation)
    assert [e.get("outcome") for e in events] == outcomes
    # A single method call selects only its own operation; no other operation
    # may emit an attempt under this recorder.
    assert {e.get("operation") for e in _attempt_events(recorder, mode)} == {operation}


def _wire(request, method, params):
    assert request.method == method and request.url.path == PATH
    assert request.url.scheme == "https" and request.url.host == "open.ys7.com"
    assert sorted(request.url.params.multi_items()) == sorted(params.items())
    assert "authorization" not in request.headers
    assert "content-type" not in request.headers
    assert request.read() == b""
    assert all(ACCESS_TOKEN not in value for value in request.headers.values())


def _created(voice_id):
    return {
        "meta": {"code": 200, "message": "Provisioned", "requestId": "req-synth"},
        "voiceInfo": {"voiceId": voice_id, "mystery": "marker"},
        "data": {"decoy": True},
        "voiceInfos": [{"voiceId": 424242}],
    }


def _args(operation):
    return (DEVICE_SERIAL,) if operation == "create" else (DEVICE_SERIAL, DELETE_VOICE_ID)


def _unused(request):
    pytest.fail("unexpected HTTP request during preauth validation")


@CLIENTS
@pytest.mark.parametrize("voice_id", [0, -7, CREATE_VOICE_ID])
def test_create_returns_top_level_voice_info(client_cls, voice_id):
    payload = _created(voice_id)
    client, recorder, calls = _session(client_cls, lambda req: httpx.Response(200, json=payload))
    mode = _mode(client_cls)
    resp = _invoke(
        client, client_cls, "create", DEVICE_SERIAL, voice_name=VOICE_NAME, voice_url=VOICE_URL
    )
    assert len(calls["auth"]) == 1 and len(calls["biz"]) == 1
    _wire(
        calls["biz"][0],
        "POST",
        {
            "accessToken": ACCESS_TOKEN,
            "deviceSerial": DEVICE_SERIAL,
            "voiceName": VOICE_NAME,
            "voiceUrl": VOICE_URL,
        },
    )
    assert (resp.operation, resp.code, resp.message) == ("device_voice.create", 200, "Provisioned")
    assert resp.data == payload["voiceInfo"] and resp.data["voiceId"] == voice_id
    assert resp.raw == payload
    _assert_call_scope(recorder, "device_voice.create", mode, ["started", "succeeded"])
    _assert_attempts(recorder, "device_voice.create", mode, ["started", "succeeded"])


@CLIENTS
@pytest.mark.parametrize("voice_id", [0, -9, DELETE_VOICE_ID])
def test_delete_accepts_meta_only_receipt(client_cls, voice_id):
    payload = {"meta": {"code": 200, "message": "Removed"}, "ext": {"arbitrary": [1, {"x": "y"}]}}
    client, recorder, calls = _session(client_cls, lambda req: httpx.Response(200, json=payload))
    mode = _mode(client_cls)
    resp = _invoke(
        client,
        client_cls,
        "delete",
        DEVICE_SERIAL,
        voice_id,
        voice_name=VOICE_NAME,
        voice_url=VOICE_URL,
    )
    assert len(calls["auth"]) == 1 and len(calls["biz"]) == 1
    _wire(
        calls["biz"][0],
        "DELETE",
        {
            "accessToken": ACCESS_TOKEN,
            "deviceSerial": DEVICE_SERIAL,
            "voiceName": VOICE_NAME,
            "voiceUrl": VOICE_URL,
            "voiceId": str(voice_id),
        },
    )
    assert (resp.operation, resp.code) == ("device_voice.delete", 200)
    assert resp.data is None and "data" not in resp.raw and resp.raw == payload
    _assert_call_scope(recorder, "device_voice.delete", mode, ["started", "succeeded"])
    _assert_attempts(recorder, "device_voice.delete", mode, ["started", "succeeded"])


@CLIENTS
@pytest.mark.parametrize(
    "receipt",
    [
        {},
        {"voiceInfo": None},
        {"voiceInfo": ["decoy"]},
        {"voiceInfo": {}},
        {"voiceInfo": {"voiceId": True}},
        {"voiceInfo": {"voiceId": "7"}},
        {"voiceInfo": {"voiceId": None}},
        {"voiceInfo": {"voiceId": 7.0}},
    ],
)
def test_create_voice_info_postcondition(client_cls, receipt):
    payload = {"meta": {"code": 200, "message": "ok"}, **receipt}
    client, recorder, calls = _session(client_cls, lambda req: httpx.Response(200, json=payload))
    mode = _mode(client_cls)
    with pytest.raises(ProtocolError) as info:
        _invoke(
            client, client_cls, "create", DEVICE_SERIAL, voice_name=VOICE_NAME, voice_url=VOICE_URL
        )
    assert info.value.operation == "device_voice.create"
    assert len(calls["auth"]) == 1 and len(calls["biz"]) == 1
    # The remote attempt succeeded at the envelope layer; only the post-call
    # voiceInfo postcondition failed, so the call fails while the attempt stands.
    _assert_call_scope(recorder, "device_voice.create", mode, ["started", "failed"])
    _assert_attempts(recorder, "device_voice.create", mode, ["started", "succeeded"])


@CLIENTS
@pytest.mark.parametrize("operation", ["create", "delete"])
@pytest.mark.parametrize(
    "payload,exc",
    [
        ({"meta": {"code": "200", "message": "ok"}}, ApiError),
        ({"meta": {}}, ProtocolError),
        ({"meta": {"code": 200.0}}, ProtocolError),
        ({"meta": {"code": True}}, ProtocolError),
        ("<html/>", ProtocolError),
        ({"meta": {"code": 10002, "message": "bad token"}}, AuthenticationError),
        ({"meta": {"code": 10031, "message": "forbidden"}}, PermissionDeniedError),
        ("RAISE_CONNECT_TIMEOUT", TransportError),
        ("RAISE_READ_TIMEOUT", TransportError),
    ],
)
def test_remote_failures(client_cls, operation, payload, exc):
    def business(request):
        if payload == "RAISE_CONNECT_TIMEOUT":
            raise httpx.ConnectTimeout("synthetic connect timeout")
        if payload == "RAISE_READ_TIMEOUT":
            # The request is fully consumed first, then the read stalls:
            # distinct phase from the connect timeout above.
            request.read()
            raise httpx.ReadTimeout("synthetic read timeout")
        if isinstance(payload, str):
            return httpx.Response(200, content=payload.encode())
        return httpx.Response(200, json=payload)

    client, recorder, calls = _session(client_cls, business)
    mode = _mode(client_cls)
    with pytest.raises(exc) as info:
        _invoke(
            client,
            client_cls,
            operation,
            *_args(operation),
            voice_name=VOICE_NAME,
            voice_url=VOICE_URL,
        )
    entrypoint = f"device_voice.{operation}"
    assert info.value.operation == entrypoint
    if exc is AuthenticationError:
        assert info.value.code == 10002
    if exc is PermissionDeniedError:
        # Real permission classification: platform code 10031 maps to the
        # PermissionDeniedError subclass (an ApiError), not a generic failure.
        assert type(info.value) is PermissionDeniedError
        assert info.value.code == 10031
    # Exactly one auth + one business request; a timeout is a single failed
    # attempt that is never retried (kind="retry"/"scheduled"), so exactly one
    # attempt event fires for both timeout cases.
    assert len(calls["auth"]) == 1 and len(calls["biz"]) == 1
    _assert_call_scope(recorder, entrypoint, mode, ["started", "failed"])
    _assert_attempts(recorder, entrypoint, mode, ["started", "failed"])
    assert not _events(recorder, "retry")


@CLIENTS
@pytest.mark.parametrize(
    "serial,name,url",
    [
        ("", VOICE_NAME, VOICE_URL),
        ("   ", VOICE_NAME, VOICE_URL),
        (None, VOICE_NAME, VOICE_URL),
        (b"raw", VOICE_NAME, VOICE_URL),
        ("ser\ud800", VOICE_NAME, VOICE_URL),
        (DEVICE_SERIAL, "", VOICE_URL),
        (DEVICE_SERIAL, "\t \n", VOICE_URL),
        (DEVICE_SERIAL, 7, VOICE_URL),
        (DEVICE_SERIAL, "na\udfffme", VOICE_URL),
        (DEVICE_SERIAL, VOICE_NAME, ""),
        (DEVICE_SERIAL, VOICE_NAME, " \t"),
        (DEVICE_SERIAL, VOICE_NAME, object()),
        (DEVICE_SERIAL, VOICE_NAME, "https://x.invalid/\ud800"),
    ],
)
def test_invalid_fields_rejected_preauth(client_cls, serial, name, url):
    # Each operation gets a brand-new client so a rejection is proven to be a
    # genuine pre-auth validation error, not a stale/closed-client guard.
    for operation in ("create", "delete"):
        client, _, calls = _session(client_cls, _unused)
        with pytest.raises(ValueError):
            _invoke(
                client,
                client_cls,
                operation,
                serial,
                *_args(operation)[1:],
                voice_name=name,
                voice_url=url,
            )
        assert calls["auth"] == [] and calls["biz"] == []


@CLIENTS
def test_preauth_rejections(client_cls):
    def fresh(region=None):
        return _session(client_cls, _unused, region=region)

    for operation in ("create", "delete"):
        # Overseas scope is rejected before any credential/transport use.
        overseas, _, ocalls = fresh(region=RegionConfig.overseas())
        with pytest.raises(ValueError):
            _invoke(
                overseas,
                client_cls,
                operation,
                *_args(operation),
                voice_name=VOICE_NAME,
                voice_url=VOICE_URL,
            )
        assert ocalls["auth"] == [] and ocalls["biz"] == []
    for voice_id in (True, "7", 1.5):
        client, _, calls = fresh()
        with pytest.raises(ValueError):
            _invoke(
                client,
                client_cls,
                "delete",
                DEVICE_SERIAL,
                voice_id,
                voice_name=VOICE_NAME,
                voice_url=VOICE_URL,
            )
        assert calls["auth"] == [] and calls["biz"] == []
    # Decimal string-conversion overflow: a genuine int whose digit count
    # exceeds the interpreter limit raises ValueError while encoding voiceId,
    # before authentication. When the interpreter limit is disabled (returns
    # 0) the same id stays encodable, so exercise it as a large valid id and
    # confirm it is still emitted verbatim instead of failing validation.
    limit = sys.get_int_max_str_digits()
    if limit:
        overflow_id = 10**limit
        client, _, calls = fresh()
        with pytest.raises(ValueError):
            _invoke(
                client,
                client_cls,
                "delete",
                DEVICE_SERIAL,
                overflow_id,
                voice_name=VOICE_NAME,
                voice_url=VOICE_URL,
            )
        assert calls["auth"] == [] and calls["biz"] == []
    else:
        large_id = DELETE_VOICE_ID * 10**20
        ack = {"meta": {"code": 200, "message": "Removed"}}
        client, _, calls = _session(client_cls, lambda req: httpx.Response(200, json=ack))
        _invoke(
            client,
            client_cls,
            "delete",
            DEVICE_SERIAL,
            large_id,
            voice_name=VOICE_NAME,
            voice_url=VOICE_URL,
        )
        assert (
            calls["biz"] and ("voiceId", str(large_id)) in calls["biz"][0].url.params.multi_items()
        )
    # Signature/arity TypeErrors are also pre-auth (Python binding fails before
    # the SDK authenticates): positional voice material, and missing required
    # voice_id on delete.
    client, _, calls = fresh()
    with pytest.raises(TypeError):
        _invoke(client, client_cls, "create", DEVICE_SERIAL, VOICE_NAME, VOICE_URL)
    assert calls["auth"] == [] and calls["biz"] == []
    client, _, calls = fresh()
    with pytest.raises(TypeError):
        _invoke(client, client_cls, "create", DEVICE_SERIAL)
    assert calls["auth"] == [] and calls["biz"] == []
    client, _, calls = fresh()
    with pytest.raises(TypeError):
        _invoke(
            client, client_cls, "delete", DEVICE_SERIAL, voice_name=VOICE_NAME, voice_url=VOICE_URL
        )
    assert calls["auth"] == [] and calls["biz"] == []


@CLIENTS
@pytest.mark.parametrize("operation", ["create", "delete"])
def test_no_material_or_credential_leakage(client_cls, operation, caplog):
    caplog.set_level(logging.INFO, logger="httpx")

    # Success path: raw response is preserved for the application, but no
    # diagnostic/log/repr surface may carry material or credentials.
    ok_payload = (
        _created(CREATE_VOICE_ID)
        if operation == "create"
        else {"meta": {"code": 200, "message": "Removed"}}
    )
    client, recorder, calls = _session(client_cls, lambda req: httpx.Response(200, json=ok_payload))
    resp = _invoke(
        client, client_cls, operation, *_args(operation), voice_name=VOICE_NAME, voice_url=VOICE_URL
    )
    assert resp.raw == ok_payload and calls["biz"]
    _wire(
        calls["biz"][0],
        METHODS[operation],
        {
            "accessToken": ACCESS_TOKEN,
            "deviceSerial": DEVICE_SERIAL,
            "voiceName": VOICE_NAME,
            "voiceUrl": VOICE_URL,
            **({"voiceId": str(DELETE_VOICE_ID)} if operation == "delete" else {}),
        },
    )
    # Non-vacuous logging surface: at least one httpx record was emitted during
    # the run, so a canary hit in caplog.text would be a genuine observed leak.
    assert any(record.name == "httpx" for record in caplog.records)
    clean = _haystack(recorder, client, resp=resp, extra=caplog.text)
    _assert_no_canaries(clean)

    # Remote error echo: a platform failure that echoes our own labelled
    # material back must be redacted out of the rendered exception. For delete
    # the selected voiceId is a URL/query locator, so it is echoed only there.
    echo = (
        f"denied accessToken={ACCESS_TOKEN}&appKey={APP_KEY}"
        f"&appSecret={APP_SECRET}&deviceSerial={DEVICE_SERIAL.strip()}"
        f"&voiceName={quote_plus(VOICE_NAME)}&voiceUrl={VOICE_URL}"
    )
    if operation == "delete":
        echo += f"&voiceId={quote_plus(str(DELETE_VOICE_ID))}"
    client, recorder, calls = _session(
        client_cls, lambda req: httpx.Response(200, json={"meta": {"code": 10031, "message": echo}})
    )
    with pytest.raises(PermissionDeniedError) as info:
        _invoke(
            client,
            client_cls,
            operation,
            *_args(operation),
            voice_name=VOICE_NAME,
            voice_url=VOICE_URL,
        )
    dirty = _haystack(recorder, client, exc=info.value, extra=caplog.text)
    _assert_no_canaries(dirty)


def _haystack(recorder, client, *, resp=None, exc=None, extra=""):
    parts = [json.dumps(recorder.report()), repr(client), repr(client.device_voice), extra]
    if resp is not None:
        parts.append(repr(resp))
    if exc is not None:
        parts.append(str(exc))
        parts.append(repr(exc))
    return "".join(parts)


def _canaries():
    ids = (str(CREATE_VOICE_ID), str(DELETE_VOICE_ID))
    secrets = (ACCESS_TOKEN, APP_KEY, APP_SECRET, DEVICE_SERIAL, VOICE_NAME, VOICE_URL, *ids)
    needles = set()
    for secret in secrets:
        needles.update(
            {
                secret,
                json.dumps(secret)[1:-1],
                quote(secret),
                quote(secret, safe=""),
                quote(secret, safe="").lower(),
                quote_plus(secret),
            }
        )
    # Partial-locator tail: a unique ASCII signature so that leaking even a
    # fragment of the material URL is caught.
    needles.add("SYN-SIG")
    return needles


def _assert_no_canaries(haystack):
    assert all(needle not in haystack for needle in _canaries())
