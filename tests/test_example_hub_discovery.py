"""Offline MockTransport tests for examples/hub_discovery.py.

Every test drives real ``Client``/``AsyncClient`` instances; only the
HTTP transport layer is mocked -- hubs business methods are never
patched. All configuration comes from synthetic env vars.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import urllib.parse

import httpx
import pytest

import examples.hub_discovery as hd
from examples.hub_discovery import run_async, run_sync
from ezviz_unified import ApiError, AsyncClient, Client, RegionConfig

HUB = "HUB_TEST1"
SUB = "SUB_TEST1"

_LIST_PATH = "/api/lapp/hub/device/sub/list"
_INFO_PATH = "/api/lapp/hub/device/sub/info"
_CHANNEL_PATH = "/api/lapp/hub/device/camera/list"

_TOKEN_BODY = {
    "code": "200",
    "msg": "ok",
    "data": {"accessToken": "TEST_TOKEN", "expireTime": 2000000},
}
_LIST_DATA = [{"deviceSerial": SUB, "deviceName": "sub"}]
_INFO_DATA = {"deviceSerial": SUB, "deviceType": "test"}
_CHANNEL_DATA = [{"channelNo": 1, "channelName": "front"}]


def _lapp(data):
    return httpx.Response(200, json={"code": "200", "msg": "ok", "data": data})


def make_handler(
    recorded,
    *,
    list_data=None,
    info_data=None,
    channel_data=None,
    fail_suffix=None,
    fail_status=None,
    fail_code=None,
):
    """Route requests: token endpoint, optional failure, then the three ops."""
    list_data = _LIST_DATA if list_data is None else list_data
    info_data = _INFO_DATA if info_data is None else info_data
    channel_data = _CHANNEL_DATA if channel_data is None else channel_data

    def handler(request):
        recorded.append(request)
        path = request.url.path
        if "token" in path:
            return httpx.Response(200, json=_TOKEN_BODY)
        if fail_suffix is not None and path.endswith(fail_suffix):
            if fail_status is not None:
                return httpx.Response(
                    fail_status,
                    text="LEAK HUB_TEST1 APP_SECRET_TEST https://leak.invalid",
                )
            return httpx.Response(
                200, json={"code": fail_code, "msg": "LEAK HUB_TEST1", "data": None}
            )
        if path == _LIST_PATH:
            return _lapp(list_data)
        if path == _INFO_PATH:
            return _lapp(info_data)
        if path == _CHANNEL_PATH:
            return _lapp(channel_data)
        raise AssertionError(f"unexpected request path: {path}")

    return handler


def _client(cls, recorded, handler, *, region=None):
    transport = httpx.MockTransport(handler)
    return cls(
        app_key="APP_KEY_TEST",
        app_secret="APP_SECRET_TEST",
        region=region if region is not None else RegionConfig.china(),
        auth_transport=transport,
        transport_factory=lambda origin: transport,
        clock=lambda: 1000,
    )


def _form(request):
    return urllib.parse.parse_qs(request.content.decode("utf-8"))


def _check_op(request, path, serial):
    assert request.method == "POST"
    assert request.url.path == path
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    form = _form(request)
    assert form.get("accessToken") == ["TEST_TOKEN"]
    assert form.get("deviceSerial") == [serial]


def _set_env(monkeypatch, **overrides):
    values = {
        "EZVIZ_APP_KEY": "APP_KEY_TEST",
        "EZVIZ_APP_SECRET": "APP_SECRET_TEST",
        "EZVIZ_REGION": "domestic",
        "EZVIZ_HUB_SERIAL": HUB,
        "EZVIZ_SUBDEVICE_SERIAL": SUB,
    }
    values.update(overrides)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _install(monkeypatch, recorded, handler, mode):
    cls = Client if mode == "sync" else AsyncClient
    client = _client(cls, recorded, handler)
    monkeypatch.setattr(hd, cls.__name__, lambda **kwargs: client)
    return client


def test_run_sync_wire_order_and_forms():
    recorded = []
    client = _client(Client, recorded, make_handler(recorded))
    assert recorded == []  # construction performs no I/O
    with client:
        listed, info, channels = run_sync(client, HUB, SUB)
    assert len(recorded) == 4  # auth + exactly three calls; close added none
    assert "token" in recorded[0].url.path
    _check_op(recorded[1], _LIST_PATH, HUB)
    _check_op(recorded[2], _INFO_PATH, SUB)
    _check_op(recorded[3], _CHANNEL_PATH, SUB)
    assert isinstance(listed.data, list) and len(listed.data) == 1
    assert listed.data[0]["deviceSerial"] == SUB
    assert isinstance(info.data, dict) and info.data["deviceSerial"] == SUB
    assert isinstance(channels.data, list) and len(channels.data) == 1


def test_run_async_wire_order_and_forms():
    recorded = []
    client = _client(AsyncClient, recorded, make_handler(recorded))
    assert recorded == []

    async def drive():
        async with client:
            return await run_async(client, HUB, SUB)

    listed, info, channels = asyncio.run(drive())
    assert len(recorded) == 4
    assert "token" in recorded[0].url.path
    _check_op(recorded[1], _LIST_PATH, HUB)
    _check_op(recorded[2], _INFO_PATH, SUB)
    _check_op(recorded[3], _CHANNEL_PATH, SUB)
    assert listed.data[0]["deviceSerial"] == SUB
    assert info.data["deviceSerial"] == SUB
    assert len(channels.data) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "hub, sub",
    [
        (HUB, "sub_test1"),  # lowercase child
        ("hub_test1", SUB),  # lowercase parent
        (HUB, ""),  # empty child
        ("", SUB),  # empty parent
        (HUB, "   "),  # space-only child
        (HUB, "\t\n"),  # tab/newline-only child
        ("   ", SUB),  # space-only parent
        ("\t\n", SUB),  # tab/newline-only parent
        (HUB, "SUB_\ud800"),  # not UTF-8 encodable
    ],
    ids=[
        "lower-child",
        "lower-parent",
        "empty-child",
        "empty-parent",
        "spaces-child",
        "tabnl-child",
        "spaces-parent",
        "tabnl-parent",
        "non-utf8",
    ],
)
def test_rejects_bad_serials_without_requests(mode, hub, sub):
    recorded = []
    cls = Client if mode == "sync" else AsyncClient
    client = _client(cls, recorded, make_handler(recorded))
    if mode == "sync":
        with client:
            with pytest.raises(ValueError) as excinfo:
                run_sync(client, hub, sub)
    else:

        async def drive():
            async with client:
                await run_async(client, hub, sub)

        with pytest.raises(ValueError) as excinfo:
            asyncio.run(drive())
    # Rejected by validation: no auth, no factory call, no business request.
    assert recorded == []
    for raw in (hub, sub):
        if raw.strip() and raw.isascii():
            assert raw not in str(excinfo.value)


def test_run_sync_rejects_non_domestic_region():
    recorded = []
    client = _client(Client, recorded, make_handler(recorded), region=RegionConfig.overseas())
    with client:
        with pytest.raises(ValueError):
            run_sync(client, HUB, SUB)
    assert recorded == []


def test_sdk_error_stops_chain_sync():
    recorded = []
    handler = make_handler(recorded, fail_suffix="/sub/info", fail_code="50001")
    client = _client(Client, recorded, handler)
    with client:
        with pytest.raises(ApiError):
            run_sync(client, HUB, SUB)
    assert len(recorded) == 3  # auth + list + failing info; channels skipped
    assert "token" in recorded[0].url.path
    _check_op(recorded[1], _LIST_PATH, HUB)
    _check_op(recorded[2], _INFO_PATH, SUB)


def test_sdk_error_stops_chain_async():
    recorded = []
    handler = make_handler(recorded, fail_suffix="/sub/info", fail_status=503)
    client = _client(AsyncClient, recorded, handler)

    async def drive():
        async with client:
            await run_async(client, HUB, SUB)

    with pytest.raises(ApiError):
        asyncio.run(drive())
    assert len(recorded) == 3
    assert recorded[1].url.path == _LIST_PATH
    assert recorded[2].url.path == _INFO_PATH


def test_cli_sync_success(monkeypatch, capsys):
    _set_env(monkeypatch)
    recorded = []
    _install(monkeypatch, recorded, make_handler(recorded), "sync")
    assert hd.main(["--mode", "sync"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out) == {
        "status": "ok",
        "mode": "sync",
        "subdevice_count": 1,
        "channel_count": 1,
        "identity_consistency": "matched",
    }
    assert HUB not in out and SUB not in out and "TEST_TOKEN" not in out
    assert len(recorded) == 4
    assert "token" in recorded[0].url.path
    _check_op(recorded[1], _LIST_PATH, HUB)
    _check_op(recorded[2], _INFO_PATH, SUB)
    _check_op(recorded[3], _CHANNEL_PATH, SUB)


def test_cli_async_success(monkeypatch, capsys):
    _set_env(monkeypatch)
    recorded = []
    _install(monkeypatch, recorded, make_handler(recorded), "async")
    assert hd.main(["--mode", "async"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out) == {
        "status": "ok",
        "mode": "async",
        "subdevice_count": 1,
        "channel_count": 1,
        "identity_consistency": "matched",
    }
    assert len(recorded) == 4
    _check_op(recorded[1], _LIST_PATH, HUB)
    _check_op(recorded[2], _INFO_PATH, SUB)
    _check_op(recorded[3], _CHANNEL_PATH, SUB)


@pytest.mark.parametrize(
    "missing",
    ["EZVIZ_APP_KEY", "EZVIZ_APP_SECRET", "EZVIZ_HUB_SERIAL", "EZVIZ_SUBDEVICE_SERIAL"],
)
def test_cli_missing_config(monkeypatch, capsys, missing):
    _set_env(monkeypatch, **{missing: None})
    calls = []
    monkeypatch.setattr(hd, "Client", lambda **kwargs: calls.append(kwargs))
    assert hd.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "configuration_error"}
    assert calls == []  # client is never constructed on config failure


def test_cli_overseas_region_rejected_before_client(monkeypatch, capsys):
    _set_env(monkeypatch, EZVIZ_REGION="overseas")
    calls = []
    monkeypatch.setattr(hd, "Client", lambda **kwargs: calls.append(kwargs))
    assert hd.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "configuration_error"}
    assert calls == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "SENTINEL_BAD_MODE"],
        ["--mode", "sync", "--SENTINEL_EXTRA"],
        ["--SENTINEL_ONLY"],
        [],
    ],
    ids=["bad-choice", "unknown-flag", "unknown-only", "missing-mode"],
)
def test_cli_invalid_argv_never_echoes(capsys, argv):
    # No env is set here: argv errors must be reported before any env read.
    assert hd.main(argv) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "input_error"}
    assert "SENTINEL" not in err


def test_cli_lowercase_serial_env_is_input_error(monkeypatch, capsys):
    _set_env(monkeypatch, EZVIZ_SUBDEVICE_SERIAL="sub_low")
    recorded = []
    _install(monkeypatch, recorded, make_handler(recorded), "sync")
    assert hd.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "input_error"}
    assert "sub_low" not in err
    assert recorded == []  # rejected before the first request


def test_cli_sdk_error_is_redacted(monkeypatch, capsys):
    _set_env(monkeypatch)
    recorded = []
    handler = make_handler(recorded, fail_suffix="/sub/info", fail_code="50001")
    _install(monkeypatch, recorded, handler, "sync")
    assert hd.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "sdk_error"}
    for leaked in ("LEAK", "50001", "APP_SECRET_TEST", HUB, SUB, "http", "/"):
        assert leaked not in err
    assert len(recorded) == 3


_TARGET_ROW = {"deviceSerial": SUB}
_SIBLING_ROW = {"deviceSerial": "SUB_DIFF9"}
_NOSTR_ROW = {"deviceSerial": 7}
_NONDESC_ROW = "SUB_BARE"


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "handler_kwargs, expected",
    [
        # Detail serial is a different string -> not_matched (list irrelevant).
        ({"info_data": {"deviceSerial": "SUB_DIFF9"}}, "not_matched"),
        # Detail missing or non-str -> unknown, even with a matching row.
        ({"info_data": {"deviceType": "x"}}, "unknown"),
        ({"info_data": {"deviceSerial": 7}}, "unknown"),
        # Detail matches and at least one list row targets the child -> matched;
        # siblings before/after the target or incomplete rows are irrelevant.
        (
            {"list_data": [_SIBLING_ROW, _TARGET_ROW]},
            "matched",
        ),
        (
            {"list_data": [_TARGET_ROW, _SIBLING_ROW]},
            "matched",
        ),
        (
            {"list_data": [_SIBLING_ROW, _TARGET_ROW, _NOSTR_ROW, _NONDESC_ROW]},
            "matched",
        ),
        # Detail matches, no target row, non-empty list of complete str rows
        # -> not_matched.
        ({"list_data": [_SIBLING_ROW]}, "not_matched"),
        (
            {"list_data": [_SIBLING_ROW, {"deviceSerial": "SUB_DIFF8"}]},
            "not_matched",
        ),
        # Empty list has no target row; matching detail cannot prove presence.
        ({"list_data": []}, "unknown"),
        # Unknown rows degrade the verdict only when no target was found.
        (
            {"list_data": [_SIBLING_ROW, _NOSTR_ROW]},
            "unknown",
        ),
        (
            {"list_data": [_NONDESC_ROW]},
            "unknown",
        ),
        ({"list_data": [_NOSTR_ROW]}, "unknown"),
    ],
    ids=[
        "info-diff",
        "info-missing",
        "info-nonstr",
        "sibling-before-target",
        "sibling-after-target",
        "siblings-and-unknown-rows-with-target",
        "sibling-only",
        "two-siblings",
        "empty-list",
        "unknown-row-without-target",
        "nondesc-row-without-target",
        "nonstr-row-without-target",
    ],
)
def test_cli_identity_consistency(monkeypatch, capsys, mode, handler_kwargs, expected):
    _set_env(monkeypatch)
    recorded = []
    _install(monkeypatch, recorded, make_handler(recorded, **handler_kwargs), mode)
    assert hd.main(["--mode", mode]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["identity_consistency"] == expected
    expected_list = handler_kwargs.get("list_data", _LIST_DATA)
    assert payload["subdevice_count"] == len(expected_list)  # count only, no totals
    assert HUB not in out and SUB not in out


@pytest.mark.parametrize(
    "handler_kwargs",
    [
        {"list_data": {"deviceSerial": SUB}},
        {"info_data": [{"deviceSerial": SUB}]},
        {"channel_data": {"channelNo": 1}},
    ],
    ids=["list-not-array", "info-not-object", "channels-not-array"],
)
def test_cli_unexpected_response_shapes(monkeypatch, capsys, handler_kwargs):
    _set_env(monkeypatch)
    recorded = []
    _install(monkeypatch, recorded, make_handler(recorded, **handler_kwargs), "sync")
    assert hd.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "unexpected_response"}


def test_help_and_import_do_no_env_reads_or_network(monkeypatch, capsys):
    real_get = os.environ.get

    def guarded_get(key, default=None):
        if str(key).startswith("EZVIZ_"):
            raise AssertionError("environment read during import/help")
        return real_get(key, default)

    def no_socket(*args, **kwargs):
        raise AssertionError("network access during import/help")

    monkeypatch.setattr(os.environ, "get", guarded_get)
    monkeypatch.setattr(socket, "socket", no_socket)
    importlib.reload(hd)  # importing the module must not read env or do I/O
    with pytest.raises(SystemExit) as excinfo:
        hd.main(["--help"])
    assert excinfo.value.code == 0
    out, err = capsys.readouterr()
    assert "python -m examples.hub_discovery" in out
    assert "EZVIZ_" not in out
