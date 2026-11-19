# tests/test_sdk_alarm_sound_errors.py
import asyncio

import httpx
import pytest
from test_sdk_alarm_sound_mode import read
from test_sdk_device_utc import TOKEN, _client

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)

SERIAL = "SynTH"


@pytest.fixture(params=["sync", "async"])
def mode(request):
    return request.param


def _queue(*responses):
    it = iter(responses)
    return lambda request: next(it)


def _body(code, message="synthetic"):
    return {
        "status": 200,
        "body": {"meta": {"code": code, "message": message}, "data": {"alarmSoundMode": 1}},
    }


def _events(rec, kind="attempt"):
    return [
        e
        for e in rec.report()["events"]
        if e["kind"] == kind
        and (
            (
                kind == "attempt"
                and e["entrypoint"] == "client.request"
                and e["operation"] == "settings.get_alarm_sound_mode"
            )
            or (kind == "call" and e["entrypoint"] == "settings.get_alarm_sound_mode")
        )
    ]


def _outcomes(rec):
    return [e["outcome"] for e in _events(rec)]


def test_meta_10002_refresh_then_success(mode):
    client, rec, seen = _client(
        mode,
        biz=_queue(
            httpx.Response(200, json=_body(10002, "expired")),
            httpx.Response(200, json=_body(200)),
        ),
    )
    read(mode, client, SERIAL)
    assert len(seen["auth"]) == 2 and len(seen["biz"]) == 2
    attempts = _events(rec)
    assert [e["outcome"] for e in attempts] == ["started", "failed", "started", "succeeded"]
    assert {(e["operation"], e["entrypoint"]) for e in attempts} == {
        ("settings.get_alarm_sound_mode", "client.request")
    }
    assert _events(rec, "call")[-1]["outcome"] == "succeeded"


def test_meta_10002_repeated_raises_invalid_token(mode):
    client, rec, seen = _client(mode, biz=lambda r: httpx.Response(200, json=_body(10002)))
    with pytest.raises(AuthenticationError) as exc:
        read(mode, client, SERIAL)
    assert exc.value.invalid_token
    assert len(seen["auth"]) == 2 and len(seen["biz"]) == 2
    assert _outcomes(rec) == ["started", "failed", "started", "failed"]


def test_root_only_envelope_is_protocol_error(mode):
    for payload in ({"code": 200, "data": {}}, {"code": 10002}):
        client, rec, seen = _client(mode, biz=lambda r: httpx.Response(200, json=payload))
        with pytest.raises(ProtocolError):
            read(mode, client, SERIAL)
        assert len(seen["auth"]) == 1 and len(seen["biz"]) == 1
        assert _outcomes(rec) == ["started", "failed"]


def test_string_meta_code_is_api_error(mode):
    client, _, _ = _client(mode, biz=lambda r: httpx.Response(200, json=_body("200")))
    with pytest.raises(ApiError) as exc:
        read(mode, client, SERIAL)
    assert not isinstance(exc.value, ProtocolError)


@pytest.mark.parametrize(
    "status,code,exc", [(403, 10031, PermissionDeniedError), (422, 60020, ApiError)]
)
def test_typed_errors_keep_code_and_status(mode, status, code, exc):
    client, rec, seen = _client(mode, biz=lambda r: httpx.Response(status, json=_body(code)))
    with pytest.raises(exc) as ei:
        read(mode, client, SERIAL)
    assert ei.value.code == code
    assert ei.value.http_status == status
    assert len(seen["biz"]) == 1


def test_timeout_reason_and_no_replay(mode):
    def boom(request):
        raise httpx.ReadTimeout("synthetic", request=request)

    client, rec, seen = _client(mode, biz=boom)
    with pytest.raises(TransportError) as exc:
        read(mode, client, SERIAL)
    assert exc.value.reason == "timeout"
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert _outcomes(rec) == ["started", "failed"]


def test_async_cancellation_propagates_no_retry():
    def cancel(request):
        raise asyncio.CancelledError()

    client, rec, seen = _client("async", biz=cancel)
    with pytest.raises(asyncio.CancelledError):
        read("async", client, SERIAL)
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert _outcomes(rec) == ["started", "cancelled"]
    assert _events(rec, "call")[-1]["outcome"] == "cancelled"


def test_remote_error_text_redacted(mode):
    locator = f"https://upstream.synthetic.example/x?s={SERIAL}&t={TOKEN}"
    client, rec, _ = _client(mode, biz=lambda r: httpx.Response(500, json=_body(10999, locator)))
    with pytest.raises(ApiError) as exc:
        read(mode, client, SERIAL)
    err = exc.value
    for marker in (locator, SERIAL, TOKEN):
        assert marker not in f"{err.message}|{err!s}|{err!r}|{rec.report()!r}"
    assert getattr(err, "raw", None) is None
