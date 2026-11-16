import asyncio
import importlib
import inspect
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import AsyncClient, Client, DiagnosticRecorder

_EVENT_KINDS = {
    "call",
    "authentication",
    "transport_setup",
    "attempt",
    "retry",
    "close",
    "cleanup",
    "inspection",
    "application",
}
_EVENT_OUTCOMES = {
    "started",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "scheduled",
}


def example(kind):
    path = Path(__file__).parents[1] / "examples" / f"diagnostics_{kind}.py"
    assert path.is_file(), "Runnable diagnostics example missing"
    return importlib.import_module(f"examples.diagnostics_{kind}")


def _run(module):
    returned = module.main()
    return asyncio.run(returned) if inspect.isawaitable(returned) else returned


def _set_env(monkeypatch, scope):
    monkeypatch.setenv("EZVIZ_APP_KEY", "PRIVATE_APP_KEY")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATE_APP_SECRET")
    monkeypatch.setenv("EZVIZ_REGION", scope)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    if scope == "overseas":
        monkeypatch.setenv("EZVIZ_AUTH_BASE_URL", "https://isgpopen.ezvizlife.com")


def _transports(scope, result):
    calls = []
    auth_host = "open.ys7.com" if scope == "domestic" else "isgpopen.ezvizlife.com"
    api_host = "open.ys7.com" if scope == "domestic" else "isgpopen.ezviz.com"

    def auth(request):
        calls.append("auth")
        assert request.url.host == auth_host
        if result == "auth_error":
            return httpx.Response(200, json={"code": "10017", "msg": "PRIVATE_ERROR"})
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
        calls.append("list")
        if result == "boom":
            raise RuntimeError("PRIVATE_BUG")
        assert request.url.host == api_host
        assert request.method == "POST" and request.url.path == "/api/lapp/device/list"
        data = parse_qs(request.content.decode())
        assert data == {
            "accessToken": ["PRIVATE_TOKEN"],
            "pageStart": ["0"],
            "pageSize": ["1"],
        }
        return httpx.Response(200, json={"code": "200", "data": []})

    return calls, auth, business


def _patch_client(monkeypatch, module, kind, auth, business, seen):
    cls = Client if kind == "sync" else AsyncClient

    def factory(*args, **kwargs):
        seen["kwargs"] = dict(kwargs)
        return cls(
            *args,
            **kwargs,
            auth_transport=httpx.MockTransport(auth),
            transport_factory=lambda origin: httpx.MockTransport(business),
        )

    monkeypatch.setattr(module, cls.__name__, factory)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_importing_example_has_no_side_effects(monkeypatch, capsys, kind):
    for name in (
        "EZVIZ_APP_KEY",
        "EZVIZ_APP_SECRET",
        "EZVIZ_REGION",
        "EZVIZ_AUTH_BASE_URL",
        "EZVIZ_DEVICE_SERIAL",
        "EZVIZ_CHANNEL",
    ):
        monkeypatch.delenv(name, raising=False)
    module = example(kind)
    importlib.reload(module)
    assert callable(module.main)
    assert callable(module.run_cli)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("scope", ["domestic", "overseas"])
def test_success_exports_report_without_secrets_or_files(
    monkeypatch, capsys, tmp_path, kind, scope
):
    module = example(kind)
    _set_env(monkeypatch, scope)
    calls, auth, business = _transports(scope, "device")
    seen = {}
    _patch_client(monkeypatch, module, kind, auth, business, seen)
    monkeypatch.chdir(tmp_path)

    assert _run(module) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["schema_version"] == 1
    assert report["scope"] == "local_sdk_diagnostics"
    assert report["max_events"] == 256
    assert report["dropped_events"] == 0
    assert report["recording_errors"] == 0
    events = report["events"]
    assert isinstance(events, list) and events
    for event in events:
        assert event.get("kind") in _EVENT_KINDS
        assert event.get("outcome") in _EVENT_OUTCOMES
    assert any(event.get("region") == scope for event in events)
    assert calls == ["auth", "list"]
    assert isinstance(seen["kwargs"]["diagnostic_recorder"], DiagnosticRecorder)
    assert "PRIVATE" not in captured.out + captured.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_sdk_failure_still_exports_report(monkeypatch, capsys, tmp_path, kind):
    module = example(kind)
    _set_env(monkeypatch, "domestic")
    calls, auth, business = _transports("domestic", "auth_error")
    _patch_client(monkeypatch, module, kind, auth, business, {})
    monkeypatch.chdir(tmp_path)

    assert _run(module) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["schema_version"] == 1
    assert report["scope"] == "local_sdk_diagnostics"
    assert any(event.get("outcome") == "failed" for event in report["events"])
    assert captured.err == "SDK diagnostics example failed; inspect the JSON report.\n"
    assert calls == ["auth"]
    assert "PRIVATE" not in captured.out + captured.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_missing_config_is_captured_without_creating_client(monkeypatch, capsys, tmp_path, kind):
    module = example(kind)
    _set_env(monkeypatch, "domestic")
    monkeypatch.delenv("EZVIZ_APP_KEY")

    def forbidden(*args, **kwargs):
        pytest.fail("Client created before configuration validation")

    monkeypatch.setattr(module, "Client" if kind == "sync" else "AsyncClient", forbidden)
    monkeypatch.chdir(tmp_path)

    assert _run(module) == 1
    captured = capsys.readouterr()
    assert captured.err == "SDK diagnostics example failed; inspect the JSON report.\n"
    report = json.loads(captured.out)
    assert any(
        event.get("kind") == "application" or event.get("error_family") == "application"
        for event in report["events"]
    )
    assert "PRIVATE" not in captured.out + captured.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_unexpected_error_propagates_and_report_is_still_exported(
    monkeypatch, capsys, tmp_path, kind
):
    module = example(kind)
    _set_env(monkeypatch, "domestic")
    calls, auth, business = _transports("domestic", "boom")
    _patch_client(monkeypatch, module, kind, auth, business, {})
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError):
        _run(module)
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["schema_version"] == 1
    assert calls == ["auth", "list"]
    assert "PRIVATE" not in captured.out + captured.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    ("raised", "exit_code", "stderr"),
    [
        pytest.param(
            RuntimeError("PRIVATE_SENTINEL"),
            1,
            "SDK diagnostics example failed; inspect the JSON report.\n",
            id="unexpected_error",
        ),
        pytest.param(
            BaseException("PRIVATE_SENTINEL"),
            1,
            "SDK diagnostics example failed; inspect the JSON report.\n",
            id="unknown_base_exception",
        ),
        pytest.param(
            SystemExit("PRIVATE_SENTINEL"),
            1,
            "SDK diagnostics example failed; inspect the JSON report.\n",
            id="system_exit",
        ),
        pytest.param(
            KeyboardInterrupt("PRIVATE_SENTINEL"),
            130,
            "SDK diagnostics example interrupted; inspect the JSON report.\n",
            id="keyboard_interrupt",
        ),
        pytest.param(
            asyncio.CancelledError("PRIVATE_SENTINEL"),
            130,
            "SDK diagnostics example interrupted; inspect the JSON report.\n",
            id="cancelled_error",
        ),
    ],
)
def test_run_cli_collapses_boundary_exceptions(
    monkeypatch, capsys, kind, raised, exit_code, stderr
):
    module = example(kind)

    def boom():
        raise raised

    monkeypatch.setattr(module, "main", boom)

    assert module.run_cli() == exit_code
    captured = capsys.readouterr()
    assert captured.err == stderr
    assert captured.out == ""
    assert "PRIVATE" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
