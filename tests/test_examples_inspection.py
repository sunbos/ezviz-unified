import asyncio
import importlib
import inspect
import json
from pathlib import Path

import httpx
import pytest

from ezviz_unified import ApiError, AsyncClient, Client

_ENV_NAMES = (
    "EZVIZ_APP_KEY",
    "EZVIZ_APP_SECRET",
    "EZVIZ_REGION",
    "EZVIZ_AUTH_BASE_URL",
    "EZVIZ_DEVICE_SERIAL",
    "EZVIZ_CHANNEL",
)

_SUMMARY_KEYS = {"region", "all_succeeded", "results"}
_RESULT_KEYS = {"operation", "status", "error_type", "code", "http_status"}


def example(kind):
    path = Path(__file__).parents[1] / "examples" / f"inspection_{kind}.py"
    assert path.is_file(), "Runnable inspection example missing"
    return importlib.import_module(f"examples.inspection_{kind}")


def _run(module):
    returned = module.main()
    return asyncio.run(returned) if inspect.isawaitable(returned) else returned


def _set_valid_env(monkeypatch, scope, serial="CS99999", channel=None):
    monkeypatch.setenv("EZVIZ_APP_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATE_SECRET")
    monkeypatch.setenv("EZVIZ_REGION", scope)
    if serial is None:
        monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    if channel is None:
        monkeypatch.delenv("EZVIZ_CHANNEL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_CHANNEL", channel)
    if scope == "overseas":
        monkeypatch.setenv("EZVIZ_AUTH_BASE_URL", "https://isgpopen.ezvizlife.com")
    else:
        monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)


class _SyncClientDouble:
    """Purpose mock for Client; records construction and performs no I/O."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


class _AsyncClientDouble:
    """Purpose mock for AsyncClient; records construction and performs no I/O."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


class _StubInspection:
    """Mimics the DeviceInspection surface consumed by the examples."""

    def __init__(self, all_succeeded):
        self.all_succeeded = all_succeeded

    def public_summary(self):
        status = "ok" if self.all_succeeded else "failed"
        return {
            "region": "domestic",
            "all_succeeded": self.all_succeeded,
            "results": [{"operation": "stub.call", "status": status}],
        }


def _make_helper(kind, fn):
    """Wrap a recording callable as a sync or async inspection helper."""
    if kind == "sync":

        def helper(client, device_serial, *, channel=1):
            return fn(client, device_serial, channel=channel)

    else:

        async def helper(client, device_serial, *, channel=1):
            return fn(client, device_serial, channel=channel)

    return helper


def _patch_client_and_helper(module, kind, monkeypatch, helper):
    created = []
    double = _SyncClientDouble if kind == "sync" else _AsyncClientDouble

    def ctor(*args, **kwargs):
        client = double(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(module, "Client" if kind == "sync" else "AsyncClient", ctor)
    monkeypatch.setattr(
        module,
        "inspect_device" if kind == "sync" else "inspect_device_async",
        helper,
    )
    return created


@pytest.mark.parametrize(
    "mod_name",
    ["examples._inspection", "examples.inspection_sync", "examples.inspection_async"],
)
def test_import_reads_no_configuration(monkeypatch, mod_name):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    module = importlib.import_module(mod_name)
    importlib.reload(module)


def test_only_async_main_is_a_coroutine_function():
    assert inspect.iscoroutinefunction(example("async").main)
    assert not inspect.iscoroutinefunction(example("sync").main)


def test_asyncio_run_only_runs_under_main_guard():
    source = (Path(__file__).parents[1] / "examples" / "inspection_async.py").read_text(
        encoding="utf-8"
    )
    head, sep, tail = source.partition('if __name__ == "__main__":')
    assert sep, "Missing __main__ guard"
    assert "asyncio.run" not in head
    assert tail.count("asyncio.run") == 1


def test_sync_example_never_uses_asyncio_run():
    source = (Path(__file__).parents[1] / "examples" / "inspection_sync.py").read_text(
        encoding="utf-8"
    )
    assert "asyncio.run" not in source


@pytest.mark.parametrize(
    ("channel", "expected"),
    [(None, 1), ("0", 0), ("2", 2), ("+3", 3), ("-7", -7), ("007", 7)],
)
def test_load_target_channel_parsing_and_default(monkeypatch, channel, expected):
    common = importlib.import_module("examples._inspection")
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", "CS99999")
    if channel is None:
        monkeypatch.delenv("EZVIZ_CHANNEL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_CHANNEL", channel)
    target = common.load_target("domestic")
    assert target.device_serial == "CS99999"
    assert target.channel == expected


@pytest.mark.parametrize(
    "channel",
    [
        "",
        "   ",
        "true",
        "False",
        "1.5",
        "0x10",
        "1_0",
        "\uff11\uff12",
        "+",
        "-",
        " 2",
        "2 ",
        "e5",
    ],
)
def test_load_target_rejects_non_decimal_channel(monkeypatch, channel):
    common = importlib.import_module("examples._inspection")
    errors = importlib.import_module("examples._common")
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", "CS99999")
    monkeypatch.setenv("EZVIZ_CHANNEL", channel)
    with pytest.raises(errors.ConfigError) as excinfo:
        common.load_target("domestic")
    assert excinfo.value.field == "EZVIZ_CHANNEL"


@pytest.mark.parametrize("serial", [None, "", "   "])
def test_load_target_requires_nonblank_serial(monkeypatch, serial):
    common = importlib.import_module("examples._inspection")
    errors = importlib.import_module("examples._common")
    if serial is None:
        monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    with pytest.raises(errors.ConfigError) as excinfo:
        common.load_target("domestic")
    assert excinfo.value.field == "EZVIZ_DEVICE_SERIAL"


@pytest.mark.parametrize("serial", ["CS99999", " CS99999 ", "9-9_Z"])
def test_load_target_preserves_serial_verbatim(monkeypatch, serial):
    common = importlib.import_module("examples._inspection")
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    monkeypatch.delenv("EZVIZ_CHANNEL", raising=False)
    assert common.load_target("domestic").device_serial == serial


@pytest.mark.parametrize("serial", ["cs99999", "CS9999a", "abc"])
def test_load_target_rejects_lowercase_serial_for_domestic(monkeypatch, serial):
    common = importlib.import_module("examples._inspection")
    errors = importlib.import_module("examples._common")
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    with pytest.raises(errors.ConfigError) as excinfo:
        common.load_target("domestic")
    assert excinfo.value.field == "EZVIZ_DEVICE_SERIAL"


def test_load_target_allows_lowercase_serial_for_overseas(monkeypatch):
    common = importlib.import_module("examples._inspection")
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", "cs99999")
    monkeypatch.delenv("EZVIZ_CHANNEL", raising=False)
    assert common.load_target("overseas").device_serial == "cs99999"


def test_inspection_target_repr_hides_serial(monkeypatch):
    common = importlib.import_module("examples._inspection")
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", "CS99999")
    monkeypatch.delenv("EZVIZ_CHANNEL", raising=False)
    rendered = repr(common.load_target("domestic"))
    assert "CS99999" not in rendered
    assert "channel=1" in rendered


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(("channel_env", "expected"), [(None, 1), ("2", 2), ("+3", 3), ("-1", -1)])
def test_serial_and_channel_reach_helper_verbatim_inside_client_context(
    monkeypatch, capsys, kind, channel_env, expected
):
    module = example(kind)
    _set_valid_env(monkeypatch, "domestic", serial="CS99999", channel=channel_env)
    seen = {}

    def record(client, device_serial, *, channel):
        seen.update(client=client, serial=device_serial, channel=channel)
        return _StubInspection(all_succeeded=True)

    created = _patch_client_and_helper(module, kind, monkeypatch, _make_helper(kind, record))
    code = _run(module)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "region": "domestic",
        "all_succeeded": True,
        "results": [{"operation": "stub.call", "status": "ok"}],
    }
    assert seen["serial"] == "CS99999"
    assert seen["channel"] == expected
    assert seen["client"] is created[0]
    assert created[0].args == ("PRIVATE_KEY", "PRIVATE_SECRET")
    assert "region" in created[0].kwargs
    assert created[0].closed is True


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_overseas_lowercase_serial_passes_verbatim(monkeypatch, capsys, kind):
    module = example(kind)
    _set_valid_env(monkeypatch, "overseas", serial="cs-9999x")
    seen = {}

    def record(client, device_serial, *, channel):
        seen["serial"] = device_serial
        return _StubInspection(all_succeeded=True)

    _patch_client_and_helper(module, kind, monkeypatch, _make_helper(kind, record))
    assert _run(module) == 0
    assert seen["serial"] == "cs-9999x"
    captured = capsys.readouterr()
    assert "cs-9999x" not in captured.out + captured.err


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(("succeeded", "expected_code"), [(True, 0), (False, 2)])
def test_exit_code_maps_all_succeeded_and_keeps_summary_on_stdout(
    monkeypatch, capsys, kind, succeeded, expected_code
):
    module = example(kind)
    _set_valid_env(monkeypatch, "domestic")

    def record(client, device_serial, *, channel):
        return _StubInspection(all_succeeded=succeeded)

    _patch_client_and_helper(module, kind, monkeypatch, _make_helper(kind, record))
    code = _run(module)
    captured = capsys.readouterr()
    assert code == expected_code
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["all_succeeded"] is succeeded
    assert "CS99999" not in captured.out


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_sdk_error_exits_one_with_redacted_stderr(monkeypatch, capsys, kind):
    module = example(kind)
    _set_valid_env(monkeypatch, "domestic")

    def record(client, device_serial, *, channel):
        raise ApiError("PRIVATE_MESSAGE", code="10001")

    created = _patch_client_and_helper(module, kind, monkeypatch, _make_helper(kind, record))
    assert _run(module) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "failed",
        "error_type": "ApiError",
        "code": "10001",
    }
    assert "PRIVATE" not in captured.err
    assert created[0].closed is True


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_programmer_errors_propagate_without_output(monkeypatch, capsys, kind):
    module = example(kind)
    _set_valid_env(monkeypatch, "domestic")

    def record(client, device_serial, *, channel):
        raise RuntimeError("PRIVATE_BOOM")

    created = _patch_client_and_helper(module, kind, monkeypatch, _make_helper(kind, record))
    with pytest.raises(RuntimeError, match="PRIVATE_BOOM"):
        _run(module)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert created[0].closed is True


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    ("env_patch", "field"),
    [
        ({"EZVIZ_APP_KEY": None}, "EZVIZ_APP_KEY"),
        (
            {"EZVIZ_APP_KEY": None, "EZVIZ_DEVICE_SERIAL": None},
            "EZVIZ_APP_KEY",
        ),
        ({"EZVIZ_DEVICE_SERIAL": None}, "EZVIZ_DEVICE_SERIAL"),
        ({"EZVIZ_DEVICE_SERIAL": ""}, "EZVIZ_DEVICE_SERIAL"),
        ({"EZVIZ_DEVICE_SERIAL": "   "}, "EZVIZ_DEVICE_SERIAL"),
        ({"EZVIZ_DEVICE_SERIAL": "cs99999"}, "EZVIZ_DEVICE_SERIAL"),
        ({"EZVIZ_CHANNEL": ""}, "EZVIZ_CHANNEL"),
        ({"EZVIZ_CHANNEL": "   "}, "EZVIZ_CHANNEL"),
        ({"EZVIZ_CHANNEL": "true"}, "EZVIZ_CHANNEL"),
        ({"EZVIZ_CHANNEL": "1.5"}, "EZVIZ_CHANNEL"),
        ({"EZVIZ_CHANNEL": "1_0"}, "EZVIZ_CHANNEL"),
        ({"EZVIZ_CHANNEL": "\uff11\uff12"}, "EZVIZ_CHANNEL"),
        ({"EZVIZ_CHANNEL": "+"}, "EZVIZ_CHANNEL"),
        ({"EZVIZ_CHANNEL": " 2"}, "EZVIZ_CHANNEL"),
        ({"EZVIZ_CHANNEL": "2 "}, "EZVIZ_CHANNEL"),
    ],
)
def test_invalid_env_fails_before_client_creation(monkeypatch, capsys, kind, env_patch, field):
    module = example(kind)
    _set_valid_env(monkeypatch, "domestic")
    for name, value in env_patch.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    def forbidden(*args, **kwargs):
        pytest.fail("Client created before configuration validation")

    monkeypatch.setattr(module, "Client" if kind == "sync" else "AsyncClient", forbidden)
    code = _run(module)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "failed",
        "error_type": "config_error",
        "field": field,
    }
    assert "PRIVATE" not in captured.err


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_overlong_channel_digits_fail_before_client_creation(monkeypatch, capsys, kind):
    module = example(kind)
    _set_valid_env(monkeypatch, "domestic")
    monkeypatch.setenv("EZVIZ_CHANNEL", "9" * 5000)

    def forbidden(*args, **kwargs):
        pytest.fail("Client created before configuration validation")

    monkeypatch.setattr(module, "Client" if kind == "sync" else "AsyncClient", forbidden)
    code = _run(module)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "failed",
        "error_type": "config_error",
        "field": "EZVIZ_CHANNEL",
    }
    assert "PRIVATE" not in captured.err


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("scope", ["domestic", "overseas"])
@pytest.mark.parametrize("outcome", ["ok", "partial", "fatal"])
def test_real_client_runs_five_calls_and_prints_public_summary(
    monkeypatch, capsys, kind, scope, outcome
):
    module = example(kind)
    _set_valid_env(monkeypatch, scope)
    auth_host = "open.ys7.com" if scope == "domestic" else "isgpopen.ezvizlife.com"
    api_host = "open.ys7.com" if scope == "domestic" else "isgpopen.ezviz.com"
    business_paths = []

    def auth(request):
        assert request.url.host == auth_host
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": "PRIVATE_TOKEN",
                    "expireTime": 9_999_999_999_999,
                    "areaDomain": f"https://{api_host}",
                },
            },
        )

    def business(request):
        business_paths.append(request.url.path)
        assert request.url.host == api_host
        if outcome == "fatal":
            return httpx.Response(200, json={"code": "10002", "msg": "PRIVATE_MSG"})
        if outcome == "partial" and len(business_paths) == 4:
            return httpx.Response(200, json={"code": "10001", "msg": "PRIVATE_MSG"})
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {"deviceSerial": "CS99999", "deviceName": "PRIVATE_NAME"},
            },
        )

    cls = Client if kind == "sync" else AsyncClient
    monkeypatch.setattr(
        module,
        cls.__name__,
        lambda *args, **kwargs: cls(
            *args,
            **kwargs,
            auth_transport=httpx.MockTransport(auth),
            transport_factory=lambda origin: httpx.MockTransport(business),
        ),
    )
    code = _run(module)
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    assert "CS99999" not in captured.out
    assert "/api/lapp/device/list" not in business_paths
    if outcome == "fatal":
        assert code == 1
        assert captured.out == ""
        report = json.loads(captured.err)
        assert report["status"] == "failed"
        assert report["error_type"] == "AuthenticationError"
        assert len(business_paths) < 5
        return
    assert captured.err == ""
    report = json.loads(captured.out)
    assert set(report) == _SUMMARY_KEYS
    assert isinstance(report["region"], str) and report["region"]
    assert all(set(entry) <= _RESULT_KEYS for entry in report["results"])
    operations = [entry["operation"] for entry in report["results"]]
    assert all(isinstance(op, str) and op for op in operations)
    statuses = [entry["status"] for entry in report["results"]]
    if outcome == "ok":
        assert code == 0
        assert report["all_succeeded"] is True
        assert statuses == ["ok"] * 5
        assert len(set(operations)) == 5
        assert len(business_paths) == 5
    else:
        assert code == 2
        assert report["all_succeeded"] is False
        assert statuses == ["ok", "ok", "ok", "failed", "ok"]
        assert report["results"][3].get("error_type")
        assert len(business_paths) == 5
