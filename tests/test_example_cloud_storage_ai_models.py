"""Offline contract tests for ``examples.cloud_storage_ai_models.main``.

The default ``--mode sync|async`` path is fully simulated: it must succeed
with the real config loader forbidden, no environment access and no network —
the canned ``MockTransport`` model list is the only response. Failures print
only a static masked message.
"""

import os

import httpx
import pytest

import examples.cloud_storage_ai_models as cli

USAGE = "usage: python -m examples.cloud_storage_ai_models --mode sync|async\n"
ERRMSG = "error: cloud storage ai models example failed\n"


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


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_success_prints_items(mode, capsys):
    assert cli.main(["--mode", mode]) == 0
    captured = capsys.readouterr()
    assert captured.out == '{"items":1}\n'
    assert captured.err == ""


def test_zero_config_and_no_env(monkeypatch, capsys):
    monkeypatch.setattr(os, "environ", _Env())
    assert cli.main(["--mode", "sync"]) == 0
    assert capsys.readouterr().out == '{"items":1}\n'


@pytest.mark.parametrize("argv", [["-h"], ["--help"]])
def test_help_prints_usage(argv, capsys):
    assert cli.main(argv) == 0
    assert capsys.readouterr().out == USAGE


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode"],
        ["sync"],
        ["--mode", "bogus"],
        ["--mode", "sync", "extra"],
        ["-x", "sync"],
    ],
)
def test_bad_args_print_usage(argv, capsys):
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == USAGE


def test_upstream_error_static_masking(monkeypatch, capsys):
    def bad(request):
        return httpx.Response(
            200,
            json={"code": "5000", "msg": "SENSITIVE-UPSTREAM-DETAIL", "data": None},
        )

    monkeypatch.setattr(cli, "_business_handler", bad)
    assert cli.main(["--mode", "sync"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ERRMSG
    assert "SENSITIVE-UPSTREAM-DETAIL" not in captured.err


def test_non_list_data_is_error(monkeypatch, capsys):
    def wrong_shape(request):
        return httpx.Response(200, json={"code": "200", "msg": "ok", "data": {"unexpected": 1}})

    monkeypatch.setattr(cli, "_business_handler", wrong_shape)
    assert cli.main(["--mode", "async"]) == 1
    assert capsys.readouterr().err == ERRMSG
