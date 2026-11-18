"""Offline tests for ``examples.stream_archive``.

Each test drives ``examples.stream_archive.main(argv)`` — the same code
path as ``python -m examples.stream_archive --mode sync --kind recording``
— while the real ``Client`` / ``AsyncClient`` constructors are wrapped so
all HTTP traffic is served by ``httpx.MockTransport``.  Everything is
synthetic: no live endpoints, no real credentials, no real media URLs.
"""

import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

import examples.stream_archive as example
import ezviz_unified

ENV = {
    "EZVIZ_APP_KEY": "test-app-key",
    "EZVIZ_APP_SECRET": "test-app-secret",
    "EZVIZ_REGION": "domestic",
    "EZVIZ_STREAM_ID": "PRIVATE-ID",
    "EZVIZ_QUERY_DATE": "2024-02-29",
    "EZVIZ_STREAM_START": "2024-02-29 23:00:00",
    "EZVIZ_STREAM_STOP": "2024-03-01 01:00:00",
}

AUTH_BODY = {
    "code": "200",
    "data": {
        "accessToken": "PRIVATETOKEN",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
SOURCE_BODY = {"meta": {"code": 200, "message": "ok"}, "data": {"fileInfo": []}}
INDEX_BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {"fileInfo": {"fileUrl": "https://private.invalid/s?x=secret&z=hidden"}},
}

TOKEN_PATH = "/api/lapp/token/get"
SOURCE_PATH = "/api/service/media/streammanage/stream/file/info"
INDEX_PATH = "/api/service/media/streammanage/stream/index/file"

SECRET_MARKERS = (
    "PRIVATETOKEN",
    "PRIVATE-ID",
    "private.invalid",
    "x=secret",
    "z=hidden",
    "test-app-secret",
)
PRIVATE_ARG = "PRIVATE-ARG-9f8e7d6c5b4a"
USAGE = (
    "usage: python -m examples.stream_archive --mode sync|async --kind recording|snapshot|index\n"
)
FAILURE_LINE = "error: stream archive query failed\n"


def _form(request):
    return parse_qs(request.content.decode("utf-8"))


def _stdout_json(captured):
    text = captured.out.strip()
    assert text, "expected a JSON status line on stdout"
    return json.loads(text)


def _assert_clean(captured):
    blob = captured.out + captured.err
    for marker in SECRET_MARKERS:
        assert marker not in blob
    assert PRIVATE_ARG not in blob
    assert "Traceback" not in captured.out


def _assert_failure(rc, captured):
    """Config/business failures: nonzero rc, empty stdout, fixed stderr line."""
    assert rc != 0
    assert captured.out == ""
    assert captured.err == FAILURE_LINE
    _assert_clean(captured)


@pytest.fixture
def env(monkeypatch):
    for key in [key for key in os.environ if key.startswith("EZVIZ_")]:
        monkeypatch.delenv(key, raising=False)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return ENV


@pytest.fixture
def sdk(monkeypatch):
    """Inject ``httpx.MockTransport`` through the real client constructors."""
    calls = {"auth": [], "business": []}
    state = {"handler": lambda request: httpx.Response(200, json=SOURCE_BODY)}

    def auth(request):
        calls["auth"].append(request)
        return httpx.Response(200, json=AUTH_BODY)

    def business(request):
        calls["business"].append(request)
        return state["handler"](request)

    def passthrough(original):
        def construct(*args, **kwargs):
            return original(
                *args,
                **kwargs,
                auth_transport=httpx.MockTransport(auth),
                transport_factory=lambda *_a, **_k: httpx.MockTransport(business),
            )

        return construct

    sync_client = passthrough(ezviz_unified.Client)
    async_client = passthrough(ezviz_unified.AsyncClient)
    monkeypatch.setattr(ezviz_unified, "Client", sync_client)
    monkeypatch.setattr(ezviz_unified, "AsyncClient", async_client)
    monkeypatch.setattr(example, "Client", sync_client, raising=False)
    monkeypatch.setattr(example, "AsyncClient", async_client, raising=False)

    return SimpleNamespace(
        calls=calls,
        set_business=lambda handler: state.update(handler=handler),
        set_business_json=lambda body, status=200: state.update(
            handler=lambda request: httpx.Response(status, json=body)
        ),
    )


# ---------------------------------------------------------------------------
# Happy paths: real SDK methods over mocked transports, exact paths/forms.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("kind,file_type", [("recording", "1"), ("snapshot", "2")])
def test_stream_source_files(mode, kind, file_type, env, sdk, capsys):
    rc = example.main(["--mode", mode, "--kind", kind])
    captured = capsys.readouterr()

    assert rc == 0
    assert _stdout_json(captured) == {
        "region": "domestic",
        "kind": kind,
        "mode": mode,
        "status": "ok",
    }
    _assert_clean(captured)

    assert len(sdk.calls["auth"]) == 1
    assert len(sdk.calls["business"]) == 1
    request = sdk.calls["business"][0]
    assert request.method == "POST"
    assert request.url.path == SOURCE_PATH
    assert request.headers["accessToken"] == "PRIVATETOKEN"
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    form = _form(request)
    assert form["streamId"] == ["PRIVATE-ID"]
    assert form["fileType"] == [file_type]
    assert form["queryDate"] == ["2024-02-29"]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_stream_index_file(mode, env, sdk, capsys):
    sdk.set_business_json(INDEX_BODY)
    rc = example.main(["--mode", mode, "--kind", "index"])
    captured = capsys.readouterr()

    assert rc == 0
    assert _stdout_json(captured) == {
        "region": "domestic",
        "kind": "index",
        "mode": mode,
        "status": "ok",
    }
    _assert_clean(captured)

    assert len(sdk.calls["auth"]) == 1
    assert len(sdk.calls["business"]) == 1
    request = sdk.calls["business"][0]
    assert request.method == "POST"
    assert request.url.path == INDEX_PATH
    form = _form(request)
    assert form["streamId"] == ["PRIVATE-ID"]
    assert form["startTime"] == ["2024-02-29 23:00:00"]
    assert form["stopTime"] == ["2024-03-01 01:00:00"]


def test_auth_request_form(env, sdk):
    rc = example.main(["--mode", "sync", "--kind", "recording"])
    assert rc == 0
    request = sdk.calls["auth"][0]
    assert request.method == "POST"
    assert request.url.path == TOKEN_PATH
    form = _form(request)
    assert form["appKey"] == ["test-app-key"]
    assert form["appSecret"] == ["test-app-secret"]


# ---------------------------------------------------------------------------
# Optional / required query date handling.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_snapshot_without_date(mode, env, sdk, capsys, monkeypatch):
    monkeypatch.delenv("EZVIZ_QUERY_DATE", raising=False)
    rc = example.main(["--mode", mode, "--kind", "snapshot"])
    captured = capsys.readouterr()

    assert rc == 0
    assert _stdout_json(captured)["status"] == "ok"
    form = _form(sdk.calls["business"][0])
    assert form["fileType"] == ["2"]
    assert "queryDate" not in form


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_snapshot_empty_date_rejected(mode, env, sdk, capsys, monkeypatch):
    # An explicitly blank EZVIZ_QUERY_DATE is not "missing": it reaches SDK
    # validation, which exits 1 before any auth or business request. This is
    # distinct from the missing-date success path above.
    monkeypatch.setenv("EZVIZ_QUERY_DATE", " ")
    rc = example.main(["--mode", mode, "--kind", "snapshot"])
    _assert_failure(rc, capsys.readouterr())
    assert sdk.calls["auth"] == []
    assert sdk.calls["business"] == []


def test_recording_requires_date(env, sdk, capsys, monkeypatch):
    monkeypatch.delenv("EZVIZ_QUERY_DATE", raising=False)
    rc = example.main(["--mode", "sync", "--kind", "recording"])
    _assert_failure(rc, capsys.readouterr())
    assert sdk.calls["business"] == []


@pytest.mark.parametrize("bad", ["2024-02-30", "2024-13-01", "not-a-date"])
def test_recording_bad_date(bad, env, sdk, capsys, monkeypatch):
    monkeypatch.setenv("EZVIZ_QUERY_DATE", bad)
    rc = example.main(["--mode", "sync", "--kind", "recording"])
    _assert_failure(rc, capsys.readouterr())
    assert sdk.calls["business"] == []


@pytest.mark.parametrize(
    "start,stop",
    [
        ("not-a-time", "2024-03-01 01:00:00"),
        ("2024-03-02 00:00:00", "2024-03-01 01:00:00"),
    ],
)
def test_index_bad_window(start, stop, env, sdk, capsys, monkeypatch):
    monkeypatch.setenv("EZVIZ_STREAM_START", start)
    monkeypatch.setenv("EZVIZ_STREAM_STOP", stop)
    rc = example.main(["--mode", "sync", "--kind", "index"])
    _assert_failure(rc, capsys.readouterr())
    assert sdk.calls["business"] == []


# ---------------------------------------------------------------------------
# Error paths: sanitized output, region gating, interruptions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["recording", "snapshot", "index"])
def test_business_error_sanitized(kind, env, sdk, capsys):
    def fail(request):
        return httpx.Response(
            200,
            json={
                "meta": {
                    "code": 500,
                    "message": "retry https://private.invalid/a?x=secret later",
                },
                "data": None,
            },
        )

    sdk.set_business(fail)
    rc = example.main(["--mode", "sync", "--kind", kind])
    captured = capsys.readouterr()
    _assert_failure(rc, captured)
    assert len(sdk.calls["business"]) == 1


def test_overseas_rejected_before_auth(env, sdk, capsys, monkeypatch):
    monkeypatch.setenv("EZVIZ_REGION", "overseas")
    rc = example.main(["--mode", "sync", "--kind", "recording"])
    captured = capsys.readouterr()
    _assert_failure(rc, captured)
    assert sdk.calls["auth"] == []
    assert sdk.calls["business"] == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_keyboard_interrupt_returns_130(mode, env, sdk):
    def interrupt(request):
        raise KeyboardInterrupt

    sdk.set_business(interrupt)
    assert example.main(["--mode", mode, "--kind", "recording"]) == 130


def test_main_returns_int_in_foreground(env, sdk):
    rc = example.main(["--mode", "sync", "--kind", "recording"])
    assert isinstance(rc, int)
    assert rc == 0


# ---------------------------------------------------------------------------
# Argument parsing: --help, missing/invalid flags, no private flags.
# ---------------------------------------------------------------------------


def test_help(capsys):
    assert example.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    assert "--mode" in out
    assert "--kind" in out


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--kind", "recording"],
        ["--mode", "sync"],
    ],
)
def test_missing_required_options(argv, capsys):
    assert example.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "bogus", "--kind", "recording"],
        ["--mode", "sync", "--kind", "bogus"],
    ],
)
def test_invalid_choices(argv, capsys):
    assert example.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE


@pytest.mark.parametrize("flag", ["--url", "--token", "--app-secret", "--stream-id"])
def test_private_flags_rejected(flag, capsys):
    argv = ["--mode", "sync", "--kind", "recording", flag, PRIVATE_ARG]
    assert example.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE
    _assert_clean(captured)
