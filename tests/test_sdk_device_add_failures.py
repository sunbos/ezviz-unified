import asyncio

import httpx
import pytest
from test_sdk_device_add_links import Probe, outcomes, reply

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("short,pos", [("create", (3,)), ("records", ()), ("list", ())])
def test_overseas_region_rejected(mode, short, pos):
    probe = Probe()
    with pytest.raises(ValueError):
        probe.call(mode, short, *pos, region=RegionConfig.overseas())
    assert probe.auth == []
    assert probe.biz == []
    assert outcomes(probe.recorder, mode, "call", short) == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", short) == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("days", [0, 8, True, "3", 2.5])
def test_create_expire_days_rejected(mode, days):
    probe = Probe()
    with pytest.raises(ValueError):
        probe.call(mode, "create", days)
    assert probe.auth == []
    assert probe.biz == []
    assert outcomes(probe.recorder, mode, "call", "create") == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", "create") == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("note", [7, "n" * 257, "\ud800"])
def test_create_note_rejected(mode, note):
    probe = Probe()
    with pytest.raises(ValueError):
        probe.call(mode, "create", 3, note=note)
    assert probe.auth == []
    assert probe.biz == []
    assert outcomes(probe.recorder, mode, "call", "create") == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", "create") == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("serial", ["S\rN", "S\nN", "S\x7fN", "SéN", " SN", "SN "])
def test_records_serial_header_rejected(mode, serial):
    probe = Probe()
    with pytest.raises(ValueError):
        probe.call(mode, "records", device_serial=serial)
    assert probe.auth == []
    assert probe.biz == []
    assert outcomes(probe.recorder, mode, "call", "records") == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", "records") == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("short", ["records", "list"])
@pytest.mark.parametrize(
    "kw",
    [
        {"cursor": ""},
        {"cursor": 9},
        {"cursor": "\ud800"},
        {"page_size": 0},
        {"page_size": 51},
        {"page_size": True},
    ],
)
def test_get_arguments_rejected(mode, short, kw):
    probe = Probe()
    with pytest.raises(ValueError):
        probe.call(mode, short, **kw)
    assert probe.auth == []
    assert probe.biz == []
    assert outcomes(probe.recorder, mode, "call", short) == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", short) == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("which", ["token", "denied", "http403", "timeout"])
def test_create_errors_not_replayed(mode, which):
    if which == "token":
        step, expected = reply({}, code=10002), AuthenticationError
    elif which == "denied":
        step, expected = reply({}, code=10031), PermissionDeniedError
    elif which == "http403":
        step, expected = httpx.Response(403, json={}), PermissionDeniedError
    else:
        step, expected = httpx.ReadTimeout("boom"), TransportError
    probe = Probe(step)
    with pytest.raises(expected) as excinfo:
        probe.call(mode, "create", 3)
    if which == "token":
        assert excinfo.value.invalid_token
        assert excinfo.value.code == 10002
    elif which == "denied":
        assert excinfo.value.code == 10031
    elif which == "http403":
        assert excinfo.value.http_status == 403
    else:
        assert excinfo.value.reason == "timeout"
    assert len(probe.auth) == 1
    assert len(probe.biz) == 1
    assert outcomes(probe.recorder, mode, "retry", "create") == []
    assert outcomes(probe.recorder, mode, "call", "create") == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", "create") == ["started", "failed"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("short", ["records", "list"])
def test_get_refreshes_once_then_succeeds(mode, short):
    probe = Probe(reply({}, code=10002), reply([]))
    result = probe.call(mode, short)
    assert result.data == []
    assert len(probe.auth) == 2
    assert len(probe.biz) == 2
    assert [request.headers.get_list("accessToken") for request in probe.biz] == [
        ["TOK1"],
        ["TOK2"],
    ]
    assert outcomes(probe.recorder, mode, "retry", short) == ["scheduled"]
    assert outcomes(probe.recorder, mode, "call", short) == ["started", "succeeded"]
    assert outcomes(probe.recorder, mode, "attempt", short) == [
        "started",
        "failed",
        "started",
        "succeeded",
    ]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("short", ["records", "list"])
def test_get_repeated_invalid_token_stops(mode, short):
    probe = Probe(reply({}, code=10002), reply({}, code=10002))
    with pytest.raises(AuthenticationError) as excinfo:
        probe.call(mode, short)
    assert excinfo.value.invalid_token
    assert excinfo.value.code == 10002
    assert len(probe.auth) == 2
    assert len(probe.biz) == 2
    assert outcomes(probe.recorder, mode, "retry", short) == ["scheduled"]
    assert outcomes(probe.recorder, mode, "call", short) == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", short) == [
        "started",
        "failed",
        "started",
        "failed",
    ]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("short", ["records", "list"])
@pytest.mark.parametrize("which", ["denied", "timeout"])
def test_get_errors_single_attempt(mode, short, which):
    if which == "denied":
        step, expected = reply({}, code=10031), PermissionDeniedError
    else:
        step, expected = httpx.ReadTimeout("boom"), TransportError
    probe = Probe(step)
    with pytest.raises(expected) as excinfo:
        probe.call(mode, short)
    if which == "denied":
        assert excinfo.value.code == 10031
    else:
        assert excinfo.value.reason == "timeout"
    assert len(probe.auth) == 1
    assert len(probe.biz) == 1
    assert outcomes(probe.recorder, mode, "retry", short) == []
    assert outcomes(probe.recorder, mode, "call", short) == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", short) == ["started", "failed"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("short,pos", [("create", (3,)), ("records", ()), ("list", ())])
def test_string_success_code_is_api_error(mode, short, pos):
    probe = Probe(reply({}, code="200"))
    with pytest.raises(ApiError) as excinfo:
        probe.call(mode, short, *pos)
    assert excinfo.value.code == "200"
    assert type(excinfo.value) is ApiError
    assert len(probe.auth) == 1
    assert len(probe.biz) == 1
    assert outcomes(probe.recorder, mode, "call", short) == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "attempt", short) == ["started", "failed"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("short,pos", [("create", (3,)), ("records", ()), ("list", ())])
def test_missing_meta_code_is_protocol_error(mode, short, pos):
    probe = Probe({"meta": {"message": "ok", "moreInfo": None}, "data": []})
    with pytest.raises(ProtocolError):
        probe.call(mode, short, *pos)
    assert len(probe.auth) == 1
    assert len(probe.biz) == 1
    assert outcomes(probe.recorder, mode, "attempt", short) == ["started", "failed"]
    assert outcomes(probe.recorder, mode, "call", short) == ["started", "failed"]


def test_async_create_issue_cancelled():
    probe = Probe(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        probe.call("async", "create", 3)
    assert len(probe.auth) == 1
    assert len(probe.biz) == 1
    assert outcomes(probe.recorder, "async", "retry", "create") == []
    assert outcomes(probe.recorder, "async", "attempt", "create") == [
        "started",
        "cancelled",
    ]
    assert outcomes(probe.recorder, "async", "call", "create") == ["started", "cancelled"]
