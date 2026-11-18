"""Synthetic boundary tests for the frozen example-CLI guard and verifier output."""

import json
import runpy

import httpx
import pytest

from tools.live import _cli_transport as t
from tools.live import verify_example_clis as v
from tools.live.verify_diagnostics import _BoundaryRejection

_AUTH_URL = "https://open.ys7.com/api/lapp/token/get"
_AUTH_DATA = {"appKey": "TEST", "appSecret": "TEST"}
_BAD = [
    pytest.param(
        lambda: httpx.Request("POST", _AUTH_URL, data={**_AUTH_DATA, "x": "1"}), id="extra-field"
    ),
    pytest.param(
        lambda: httpx.Request(
            "POST", "https://evil.example.com/api/lapp/token/get", data=dict(_AUTH_DATA)
        ),
        id="wrong-host",
    ),
    pytest.param(
        lambda: httpx.Request(
            "POST", "https://open.ys7.com/api/lapp/token/other", data=dict(_AUTH_DATA)
        ),
        id="unknown-path",
    ),
    pytest.param(
        lambda: httpx.Request("POST", _AUTH_URL + "?debug=1", data=dict(_AUTH_DATA)), id="query"
    ),
    pytest.param(
        lambda: httpx.Request(
            "POST", _AUTH_URL, data=dict(_AUTH_DATA), headers={"Authorization": "Bearer x"}
        ),
        id="auth-header",
    ),
    pytest.param(lambda: httpx.Request("GET", _AUTH_URL), id="wrong-method"),
    pytest.param(
        lambda: httpx.Request("POST", _AUTH_URL, content=iter([b"appKey=TEST&appSecret=TEST"])),
        id="streaming",
    ),
]


def _auth() -> httpx.Request:
    return httpx.Request("POST", _AUTH_URL, data=dict(_AUTH_DATA))


@pytest.mark.parametrize("build", _BAD)
def test_rejected_admission_latches(build):
    guard = t.ProfileGuard("examples.devices_sync", None)
    with pytest.raises(_BoundaryRejection):
        guard.admit(build())
    assert guard.rejected and not guard.complete and guard.labels == []
    with pytest.raises(_BoundaryRejection):
        guard.admit(_auth())
    assert guard.labels == []


def test_duplicate_auth_refused():
    guard = t.ProfileGuard("examples.devices_sync", None)
    guard.admit(_auth())
    assert guard.labels == ["auth"] and not guard.rejected and not guard.complete
    with pytest.raises(_BoundaryRejection):
        guard.admit(_auth())
    assert guard.rejected and guard.labels == ["auth"]


def test_inspection_foreign_serial_refused():
    guard = t.ProfileGuard("examples.inspection_sync", "PRIVATESERIAL")
    guard.admit(_auth())
    info = httpx.Request(
        "POST",
        "https://open.ys7.com/api/lapp/device/info",
        data={"accessToken": "TEST", "deviceSerial": "OTHER"},
    )
    with pytest.raises(_BoundaryRejection):
        guard.admit(info)
    assert guard.rejected and guard.labels == ["auth"]


def _verify_with_corrupt_exit(monkeypatch, capsys, noise):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        data = (
            {
                "accessToken": "TEST",
                "expireTime": 9999999999999,
                "areaDomain": "https://open.ys7.com",
            }
            if request.url.path.endswith("/token/get")
            else []
        )
        return httpx.Response(200, json={"code": "200", "data": data})

    monkeypatch.setattr(t, "_sync_delegate", lambda: httpx.MockTransport(handler))
    monkeypatch.setattr(t, "_async_delegate", lambda: httpx.MockTransport(handler))
    original = runpy.run_module

    def spy(module, *args, **kwargs):
        try:
            return original(module, *args, **kwargs)
        except SystemExit:
            print(noise)
            raise

    monkeypatch.setattr(runpy, "run_module", spy)
    receipt = v.verify({"app_key": "PRIVATEKEY", "app_secret": "PRIVATESECRET"})
    return receipt, paths, capsys.readouterr()


@pytest.mark.parametrize(
    "noise",
    [
        "PRIVATENOISE" + "x" * 65525,
        json.dumps({"private": "PRIVATEDOC"}),
    ],
    ids=["oversized-line", "second-json"],
)
def test_corrupt_cli_output_fails_closed(monkeypatch, capsys, noise):
    receipt, paths, captured = _verify_with_corrupt_exit(monkeypatch, capsys, noise)
    assert receipt["http_requests"] == len(paths) == 2
    assert receipt["outcome"] == "failed"
    assert receipt["profiles"][0]["result"] == "failed"
    assert [r["result"] for r in receipt["profiles"][1:]] == ["skipped_after_failure"] * 5
    leaked = captured.out + captured.err + json.dumps(receipt)
    assert not any(
        m in leaked for m in ("PRIVATENOISE", "PRIVATEDOC", "PRIVATEKEY", "PRIVATESECRET")
    )


@pytest.mark.parametrize("argv", [["--bad", "PRIVATE"], ["--prompt", "PRIVATE"], ["PRIVATE"]])
def test_main_rejects_unknown_argv(capsys, argv):
    assert v.main(argv) == 2
    out, err = capsys.readouterr()
    assert out == "" and "--prompt" in err and "PRIVATE" not in err


_AUTH_URL_PATH = "/api/lapp/token/get"
_LIST_URL = "/api/lapp/device/list"
_INFO_URL = "/api/lapp/device/info"
_CHANNELS_URL = "/api/lapp/device/camera/list"
_STATUS_URL = "/api/lapp/device/status/get"
_CAPACITY_URL = "/api/lapp/device/capacity"
_VERSION_URL = "/api/lapp/device/version/info"
_TOKEN_DATA = {
    "accessToken": "TEST",
    "expireTime": 9999999999999,
    "areaDomain": "https://open.ys7.com",
}


def _no_transport(*_args, **_kwargs):
    raise AssertionError("real delegate transport must not be constructed")


@pytest.mark.parametrize("factory", [t._sync_delegate, t._async_delegate], ids=["sync", "async"])
def test_tls_keylog_blocks_delegate_creation(monkeypatch, tmp_path, factory):
    log = tmp_path / "private-ssl.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(log))
    monkeypatch.setattr(httpx, "HTTPTransport", _no_transport)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", _no_transport)
    with pytest.raises(_BoundaryRejection) as excinfo:
        factory()
    assert not log.exists()
    text = str(excinfo.value) + repr(excinfo.value)
    assert "private-ssl.log" not in text and str(tmp_path) not in text


def test_tls_keylog_verify_fails_closed(monkeypatch, tmp_path, capsys):
    log = tmp_path / "private-ssl.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(log))
    monkeypatch.setattr(httpx, "HTTPTransport", _no_transport)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", _no_transport)
    receipt = v.verify({"app_key": "PRIVATEKEY", "app_secret": "PRIVATESECRET"})
    assert receipt["outcome"] == "failed" and receipt["http_requests"] == 0
    assert receipt["profiles"][0]["result"] == "failed"
    assert [row["result"] for row in receipt["profiles"][1:]] == ["skipped_after_failure"] * 5
    assert not log.exists()
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err + json.dumps(receipt)


def _synthetic_transport(monkeypatch, inject_at=None):
    """Mock both shared delegates; optionally poison one device/info reply.

    Returns ``(paths, cookie_seen)`` where ``cookie_seen`` latches if any
    Cookie header ever reaches the delegate handler. The ``inject_at``
    index selects which ``/device/info`` response carries Set-Cookie.
    """
    paths: list[str] = []
    seen = {"cookie": False, "info": 0}

    def handler(request):
        p = request.url.path
        paths.append(p)
        seen["cookie"] = seen["cookie"] or "cookie" in request.headers
        if p.endswith("/device/info"):
            seen["info"] += 1
        headers = {}
        if p.endswith("/device/info") and seen["info"] == inject_at:
            headers["Set-Cookie"] = "s=PRIVATE"
        data = (
            dict(_TOKEN_DATA)
            if p.endswith("/token/get")
            else ([{"deviceSerial": "PRIVATESERIAL"}] if p.endswith("/device/list") else {})
        )
        return httpx.Response(200, json={"code": "200", "data": data}, headers=headers)

    monkeypatch.setattr(t, "_sync_delegate", lambda: httpx.MockTransport(handler))
    monkeypatch.setattr(t, "_async_delegate", lambda: httpx.MockTransport(handler))
    return paths, seen


@pytest.mark.parametrize(
    "which, expected_paths, failing",
    [
        pytest.param(
            1,
            [_AUTH_URL_PATH, _LIST_URL, _AUTH_URL_PATH, _LIST_URL, _AUTH_URL_PATH, _INFO_URL],
            2,
            id="first-inspection",
        ),
        pytest.param(
            2,
            [
                _AUTH_URL_PATH,
                _LIST_URL,
                _AUTH_URL_PATH,
                _LIST_URL,
                _AUTH_URL_PATH,
                _INFO_URL,
                _CHANNELS_URL,
                _STATUS_URL,
                _CAPACITY_URL,
                _VERSION_URL,
                _AUTH_URL_PATH,
                _INFO_URL,
            ],
            3,
            id="second-inspection",
        ),
    ],
)
def test_set_cookie_response_rejects_next_request(
    monkeypatch, capsys, which, expected_paths, failing
):
    paths, seen = _synthetic_transport(monkeypatch, inject_at=which)
    receipt = v.verify({"app_key": "PRIVATEKEY", "app_secret": "PRIVATESECRET"})
    assert seen["cookie"] is False
    assert paths == expected_paths
    assert receipt["outcome"] == "failed"
    assert receipt["http_requests"] == len(paths)
    results = [row["result"] for row in receipt["profiles"]]
    assert results == ["passed"] * failing + ["failed"] + ["skipped_after_failure"] * (5 - failing)
    row = receipt["profiles"][failing]
    assert row["boundary_rejected"] is True and row["http_requests"] == 2
    captured = capsys.readouterr()
    leaked = captured.out + captured.err + json.dumps(receipt)
    assert "PRIVATE" not in leaked


def test_prompt_ctrl_c_is_130_without_echo(monkeypatch, capsys):
    def _interrupt():
        raise KeyboardInterrupt("PRIVATE")

    monkeypatch.setattr("tools.live.verify_diagnostics_async._read_credentials", _interrupt)
    assert v.main(["--prompt"]) == 130
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err


def test_first_module_interrupted_exit_code(monkeypatch, capsys):
    paths, _seen = _synthetic_transport(monkeypatch)
    original = runpy.run_module

    def spy(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except SystemExit:
            raise SystemExit(130)

    monkeypatch.setattr(runpy, "run_module", spy)
    receipt = v.verify({"app_key": "PRIVATEKEY", "app_secret": "PRIVATESECRET"})
    assert paths == [_AUTH_URL_PATH, _LIST_URL]
    assert receipt["outcome"] == "failed" and receipt["http_requests"] == 2
    first = receipt["profiles"][0]
    assert first["result"] == "failed" and first["exit_code"] == 130
    assert first["http_requests"] == 2
    assert [row["result"] for row in receipt["profiles"][1:]] == ["skipped_after_failure"] * 5
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err + json.dumps(receipt)
