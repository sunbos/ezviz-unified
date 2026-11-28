"""Offline contract tests for ``examples.cloud_project.main`` (synthetic env only).

The default ``--mode sync|async`` path is fully simulated: it must succeed
with the config loader forbidden, no ``EZVIZ_*`` environment access and no
network — the canned ``MockTransport`` project object is the only response.
"""

import json
import os

import httpx
import pytest

import examples._common as common
import examples.cloud_project as cli

USAGE = "usage: python -m examples.cloud_project --mode sync|async\n"
ERRMSG = "error: cloud project example failed\n"


@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    def spy(*args, **kwargs):
        pytest.fail("forbidden external call")

    for target in (
        "httpx.HTTPTransport.handle_request",
        "httpx.AsyncHTTPTransport.handle_async_request",
        "socket.create_connection",
        "webbrowser.open",
        "webbrowser.open_new",
        "webbrowser.open_new_tab",
    ):
        monkeypatch.setattr(target, spy)
    monkeypatch.setattr(common, "load_config", _dead, raising=False)


def _dead(*args, **kwargs):
    raise AssertionError("load_config must not run")


class _Env(dict):
    """os.environ replacement that traps EZVIZ_ reads and enumeration."""

    def _trap(self, key):
        assert not str(key).startswith("EZVIZ_")

    def get(self, key, default=None):
        self._trap(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._trap(key)
        return super().__getitem__(key)

    def _bulk(self, *args, **kwargs):
        raise AssertionError("unexpected environment enumeration")

    __iter__ = keys = items = values = copy = _bulk

    def __contains__(self, key):
        self._trap(key)
        return super().__contains__(key)


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_main_success(mode, capsys):
    assert cli.main(["--mode", mode]) == 0
    captured = capsys.readouterr()
    assert captured.out == '{"items":1}\n'
    assert json.loads(captured.out) == {"items": 1}
    assert captured.err == ""


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode"],
        ["--mode", "bogus"],
        ["--mode", "sync", "extra"],
        ["sync"],
        ["--mode", "SYNC"],
        ["--other", "sync"],
    ],
)
def test_bad_arguments(argv, capsys):
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help(flag, capsys):
    assert cli.main([flag]) == 0
    captured = capsys.readouterr()
    assert captured.out == USAGE
    assert captured.err == ""


def test_business_error_masks_details(monkeypatch, capsys):
    def handler(request):
        return httpx.Response(500, json={"meta": {"code": 500, "message": "inner detail"}})

    monkeypatch.setattr(cli, "_business_handler", handler)
    assert cli.main(["--mode", "async"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERRMSG


def test_transport_failure_masks_details(monkeypatch, capsys):
    def boom(request):
        raise httpx.ConnectError("sensitive upstream detail", request=request)

    monkeypatch.setattr(cli, "_business_handler", boom)
    assert cli.main(["--mode", "sync"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERRMSG


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_no_env_reads(mode, monkeypatch, capsys):
    monkeypatch.setattr(os, "environ", _Env(os.environ))
    assert cli.main(["--mode", mode]) == 0
    assert capsys.readouterr().out == '{"items":1}\n'


def test_no_config_loader(monkeypatch, capsys):
    monkeypatch.setattr(common, "load_config", _dead, raising=False)
    assert cli.main(["--mode", "sync"]) == 0
    assert capsys.readouterr().out == '{"items":1}\n'
