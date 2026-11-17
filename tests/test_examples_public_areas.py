"""Offline tests for ``examples.public_areas``.

The example must be importable and runnable without any network: import,
``--help``/bad-arg parsing issue no request; the one directory call
happens only through the injected client factory. All clients are
replaced by offline doubles, so this file never touches a real socket or
a real credential.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[1] / "examples" / "public_areas.py"
    assert path.is_file(), "examples/public_areas.py missing"
    return importlib.import_module("examples.public_areas")


def _reload():
    module = _module()
    return importlib.reload(module)


class _StubResponse:
    """Minimal ApiResponse-shaped double carrying synthetic directory data."""

    def __init__(self, data):
        self.data = data


_ITEMS = [
    {"id": 90001, "name": "评测地区甲", "region": "EVAL-1", "telephoneCode": 9001},
    {
        "id": 90002,
        "name": "Eval Beta",
        "region": "EVAL-2",
        "telephoneCode": 9002,
        "extra": "kept",
    },
]


class _SyncClientDouble:
    created = 0
    calls = 0
    exits = 0

    def __init__(self, *args, **kwargs):
        type(self).created += 1
        self.kwargs = kwargs
        self.settings = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        type(self).exits += 1
        return False

    def list_areas(self):
        type(self).calls += 1
        return _StubResponse(list(_ITEMS))


class _AsyncClientDouble:
    created = 0
    calls = 0
    exits = 0

    def __init__(self, *args, **kwargs):
        type(self).created += 1
        self.kwargs = kwargs
        self.settings = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        type(self).exits += 1
        return False

    async def list_areas(self):
        type(self).calls += 1
        return _StubResponse(list(_ITEMS))


class _FailingSyncDouble(_SyncClientDouble):
    def list_areas(self):
        type(self).calls += 1
        from ezviz_unified import ApiError

        raise ApiError("PRIVATE_SENTINEL detail", code="10017", http_status=200)


class _FailingAsyncDouble(_AsyncClientDouble):
    async def list_areas(self):
        type(self).calls += 1
        from ezviz_unified import ApiError

        raise ApiError("PRIVATE_SENTINEL detail", code="10017", http_status=200)


def _reset(*classes):
    for cls in classes:
        cls.created = 0
        cls.calls = 0
        cls.exits = 0


def test_import_has_no_side_effects(capsys, monkeypatch):
    """Importing/reloading performs no request, no env read, no output."""
    for name in ("EZVIZ_APP_KEY", "EZVIZ_APP_SECRET", "EZVIZ_REGION"):
        monkeypatch.delenv(name, raising=False)
    module = _reload()
    assert callable(module.main)
    assert callable(module.run_cli)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_help_and_bad_args_issue_no_request(capsys, monkeypatch):
    module = _module()
    _reset(_SyncClientDouble, _AsyncClientDouble)

    def forbidden(*args, **kwargs):
        pytest.fail("a client must not be constructed for help/bad args")

    monkeypatch.setattr(module, "Client", forbidden)
    monkeypatch.setattr(module, "AsyncClient", forbidden)
    assert module.main(["--help"]) == 0
    assert module.main(["--bogus"]) == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.out
    assert "usage:" in captured.err


def test_sync_main_uses_public_origin_and_prints_bounded_summary(monkeypatch, capsys, tmp_path):
    module = _module()
    _reset(_SyncClientDouble)
    seen = {}
    original_init = _SyncClientDouble.__init__

    def init(self, *args, **kwargs):
        seen["kwargs"] = kwargs
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(_SyncClientDouble, "__init__", init)
    monkeypatch.setattr(module, "Client", _SyncClientDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main([]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report == {"status": "ok", "items": 2}
    # The summary never echoes real names/rows — only the item count.
    assert "Eval Beta" not in captured.out
    assert "extra" not in captured.out
    assert captured.err == ""
    # Exactly one call, clean context exit.
    assert _SyncClientDouble.calls == 1
    assert _SyncClientDouble.exits == 1
    # Anonymous public client pinned to the documented origin only.
    assert seen["kwargs"]["anonymous"] is True
    region = seen["kwargs"]["region"]
    assert region.scope == "overseas"
    assert region.api_base_url == "https://open.ezvizlife.com"
    # No files are written.
    assert list(tmp_path.iterdir()) == []


def test_async_main_uses_public_origin_and_prints_bounded_summary(monkeypatch, capsys, tmp_path):
    module = _module()
    _reset(_AsyncClientDouble)
    seen = {}
    original_init = _AsyncClientDouble.__init__

    def init(self, *args, **kwargs):
        seen["kwargs"] = kwargs
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(_AsyncClientDouble, "__init__", init)
    monkeypatch.setattr(module, "AsyncClient", _AsyncClientDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main(["--async"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report == {"status": "ok", "items": 2}
    assert captured.err == ""
    assert _AsyncClientDouble.calls == 1
    assert _AsyncClientDouble.exits == 1
    assert seen["kwargs"]["anonymous"] is True
    assert seen["kwargs"]["region"].api_base_url == "https://open.ezvizlife.com"
    assert list(tmp_path.iterdir()) == []


def test_summary_rejects_non_list_data_sync(monkeypatch, capsys, tmp_path):
    """Non-list data gets fixed unexpected_data_type, exit 1, no leak."""
    module = _module()
    _reset(_SyncClientDouble)

    class _NonListDouble(_SyncClientDouble):
        def list_areas(self):
            type(self).calls += 1
            return _StubResponse({"secret": "PRIVATE_SENTINEL"})

    monkeypatch.setattr(module, "Client", _NonListDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main([]) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report == {"status": "unexpected_data_type", "items": None}
    assert "PRIVATE_SENTINEL" not in captured.out
    assert "PRIVATE_SENTINEL" not in captured.err
    assert _NonListDouble.calls == 1
    assert _NonListDouble.exits == 1


def test_summary_rejects_non_list_data_async(monkeypatch, capsys, tmp_path):
    module = _module()
    _reset(_AsyncClientDouble)

    class _NonListDouble(_AsyncClientDouble):
        async def list_areas(self):
            type(self).calls += 1
            return _StubResponse({"secret": "PRIVATE_SENTINEL"})

    monkeypatch.setattr(module, "AsyncClient", _NonListDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main(["--async"]) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report == {"status": "unexpected_data_type", "items": None}
    assert "PRIVATE_SENTINEL" not in captured.out
    assert "PRIVATE_SENTINEL" not in captured.err
    assert _NonListDouble.calls == 1
    assert _NonListDouble.exits == 1


def test_summary_ignores_unknown_keys_sync(monkeypatch, capsys, tmp_path):
    """Unknown keys, long keys, and canary values never reach stdout."""
    module = _module()
    _reset(_SyncClientDouble)
    canary = "CANARY_" + "x" * 300
    items = [
        {
            "id": 1,
            "name": "A",
            "region": "R",
            "telephoneCode": 1,
            "unknown_key_" + "k" * 200: canary,
            "another_unknown": {"nested": canary},
        }
        for _ in range(50)
    ]

    class _BigDouble(_SyncClientDouble):
        def list_areas(self):
            type(self).calls += 1
            return _StubResponse(items)

    monkeypatch.setattr(module, "Client", _BigDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main([]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report == {"status": "ok", "items": 50}
    assert canary not in captured.out
    assert "unknown_key" not in captured.out
    assert "another_unknown" not in captured.out
    assert _BigDouble.calls == 1


def test_summary_ignores_unknown_keys_async(monkeypatch, capsys, tmp_path):
    module = _module()
    _reset(_AsyncClientDouble)
    canary = "CANARY_" + "y" * 300
    items = [
        {
            "id": 2,
            "name": "B",
            "region": "S",
            "telephoneCode": 2,
            "long_key_" + "z" * 250: canary,
        }
        for _ in range(30)
    ]

    class _BigDouble(_AsyncClientDouble):
        async def list_areas(self):
            type(self).calls += 1
            return _StubResponse(items)

    monkeypatch.setattr(module, "AsyncClient", _BigDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main(["--async"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report == {"status": "ok", "items": 30}
    assert canary not in captured.out
    assert "long_key" not in captured.out
    assert _BigDouble.calls == 1


def test_empty_list_returns_ok_zero_sync(monkeypatch, capsys, tmp_path):
    module = _module()
    _reset(_SyncClientDouble)

    class _EmptyDouble(_SyncClientDouble):
        def list_areas(self):
            type(self).calls += 1
            return _StubResponse([])

    monkeypatch.setattr(module, "Client", _EmptyDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main([]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report == {"status": "ok", "items": 0}
    assert captured.err == ""
    assert _EmptyDouble.calls == 1
    assert _EmptyDouble.exits == 1


def test_empty_list_returns_ok_zero_async(monkeypatch, capsys, tmp_path):
    module = _module()
    _reset(_AsyncClientDouble)

    class _EmptyDouble(_AsyncClientDouble):
        async def list_areas(self):
            type(self).calls += 1
            return _StubResponse([])

    monkeypatch.setattr(module, "AsyncClient", _EmptyDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main(["--async"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report == {"status": "ok", "items": 0}
    assert captured.err == ""
    assert _EmptyDouble.calls == 1
    assert _EmptyDouble.exits == 1


def test_sdk_failure_is_redacted_sync(monkeypatch, capsys, tmp_path):
    module = _module()
    _reset(_FailingSyncDouble)
    monkeypatch.setattr(module, "Client", _FailingSyncDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    # Fixed failure text only; no class name, no message, no traceback.
    assert captured.err.strip() == "public areas example failed."
    assert "ApiError" not in captured.err
    assert "PRIVATE_SENTINEL" not in captured.err
    assert "Traceback" not in captured.err
    assert _FailingSyncDouble.calls == 1
    assert _FailingSyncDouble.exits == 1
    assert list(tmp_path.iterdir()) == []


def test_sdk_failure_is_redacted_async(monkeypatch, capsys, tmp_path):
    module = _module()
    _reset(_FailingAsyncDouble)
    monkeypatch.setattr(module, "AsyncClient", _FailingAsyncDouble)
    monkeypatch.chdir(tmp_path)

    assert module.main(["--async"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "public areas example failed."
    assert "ApiError" not in captured.err
    assert "PRIVATE_SENTINEL" not in captured.err
    assert "Traceback" not in captured.err
    assert _FailingAsyncDouble.calls == 1
    assert _FailingAsyncDouble.exits == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("raised", "exit_code"),
    [
        (KeyboardInterrupt(), 130),
        (asyncio.CancelledError(), 130),
        (RuntimeError("PRIVATE_SENTINEL"), 1),
        (ValueError("PRIVATE_SENTINEL"), 1),
    ],
)
def test_run_cli_collapses_boundary_exceptions(monkeypatch, capsys, raised, exit_code):
    module = _module()

    def boom():
        raise raised

    monkeypatch.setattr(module, "main", boom)
    assert module.run_cli() == exit_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PRIVATE_SENTINEL" not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "raised",
    [SystemExit(99), GeneratorExit()],
)
def test_run_cli_propagates_system_exit_and_generator_exit(monkeypatch, raised):
    """SystemExit and GeneratorExit must propagate, not be collapsed."""
    module = _module()

    def boom():
        raise raised

    monkeypatch.setattr(module, "main", boom)
    with pytest.raises(type(raised)):
        module.run_cli()


def test_run_cli_sync_path_issues_one_request(monkeypatch, capsys):
    """run_cli with default argv drives the sync path: one call, one exit."""
    module = _module()
    _reset(_SyncClientDouble)
    monkeypatch.setattr(module, "Client", _SyncClientDouble)
    monkeypatch.setattr(sys, "argv", ["examples.public_areas"])

    assert module.run_cli() == 0
    assert _SyncClientDouble.created == 1
    assert _SyncClientDouble.calls == 1
    assert _SyncClientDouble.exits == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "ok", "items": 2}


def test_run_cli_async_path_issues_one_request(monkeypatch, capsys):
    """run_cli --async drives the async path: one call, one exit."""
    module = _module()
    _reset(_AsyncClientDouble)
    monkeypatch.setattr(module, "AsyncClient", _AsyncClientDouble)
    monkeypatch.setattr(sys, "argv", ["examples.public_areas", "--async"])

    assert module.run_cli() == 0
    assert _AsyncClientDouble.created == 1
    assert _AsyncClientDouble.calls == 1
    assert _AsyncClientDouble.exits == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "ok", "items": 2}


def test_run_cli_import_and_help_issue_no_request(monkeypatch, capsys):
    """run_cli with --help does not construct a client or issue a request."""
    module = _module()
    _reset(_SyncClientDouble, _AsyncClientDouble)
    monkeypatch.setattr(sys, "argv", ["examples.public_areas", "--help"])

    assert module.run_cli() == 0
    assert _SyncClientDouble.created == 0
    assert _AsyncClientDouble.created == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out


def test_run_cli_bad_args_issue_no_request(monkeypatch, capsys):
    """run_cli with invalid args does not construct a client."""
    module = _module()
    _reset(_SyncClientDouble, _AsyncClientDouble)
    monkeypatch.setattr(sys, "argv", ["examples.public_areas", "--bogus"])

    assert module.run_cli() == 2
    assert _SyncClientDouble.created == 0
    assert _AsyncClientDouble.created == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.err
