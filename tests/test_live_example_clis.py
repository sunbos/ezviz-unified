import json
import os
from urllib.parse import parse_qs

import httpx
import pytest

from tools.live import _cli_transport as t
from tools.live import verify_example_clis as v

AUTH = "/api/lapp/token/get"
DEVICE_LIST = "/api/lapp/device/list"
DEVICE_INFO = "/api/lapp/device/info"
CHANNELS = "/api/lapp/device/camera/list"
STATUS = "/api/lapp/device/status/get"
CAPABILITIES = "/api/lapp/device/capacity"
VERSION = "/api/lapp/device/version/info"
MODULES = (
    "examples.devices_sync",
    "examples.devices_async",
    "examples.inspection_sync",
    "examples.inspection_async",
    "examples.diagnostics_sync",
    "examples.diagnostics_async",
)
FULL = [
    AUTH,
    DEVICE_LIST,
    AUTH,
    DEVICE_LIST,
    AUTH,
    DEVICE_INFO,
    CHANNELS,
    STATUS,
    CAPABILITIES,
    VERSION,
    AUTH,
    DEVICE_INFO,
    CHANNELS,
    STATUS,
    CAPABILITIES,
    VERSION,
    AUTH,
    DEVICE_LIST,
    AUTH,
    DEVICE_LIST,
]
ROW = {"module", "exit_code", "http_requests", "result", "boundary_rejected"}
TOP = {"schema_version", "scope", "outcome", "http_requests", "profiles"}
SECRETS = ("PRIVATEKEY", "PRIVATESECRET", "PRIVATETOKEN", "PRIVATESERIAL", "PRIVATEFAIL")


def _run(monkeypatch, rows, bad=()):
    paths, forms = [], {}

    def handle(r):
        p = r.url.path
        paths.append(p)
        if p not in (AUTH, DEVICE_LIST):
            forms.setdefault(p, []).append(parse_qs(r.content.decode() or r.url.query))
        if p in bad:
            return httpx.Response(200, json={"code": "20002", "msg": "PRIVATEFAIL"})
        tok = {
            "accessToken": "PRIVATETOKEN",
            "expireTime": 9999999999999,
            "areaDomain": "https://open.ys7.com",
        }
        page = rows if paths.count(DEVICE_LIST) == 1 else [{"deviceSerial": "DIFFERENT"}]
        data = tok if p == AUTH else page if p == DEVICE_LIST else {}
        return httpx.Response(200, json={"code": "200", "data": data})

    monkeypatch.setattr(t, "_sync_delegate", lambda: httpx.MockTransport(handle))
    monkeypatch.setattr(t, "_async_delegate", lambda: httpx.MockTransport(handle))
    monkeypatch.setenv("EZVIZ_CHANNEL", "9")
    monkeypatch.setenv("EZVIZ_AUTH_BASE_URL", "https://example.invalid")
    return v.verify({"app_key": "PRIVATEKEY", "app_secret": "PRIVATESECRET"}), paths, forms


def _posts(forms):
    want = {STATUS: "channel", CAPABILITIES: "channelNo"}
    for p in (DEVICE_INFO, CHANNELS, STATUS, CAPABILITIES, VERSION):
        for f in forms.get(p, ()):
            assert f["deviceSerial"] == ["PRIVATESERIAL"]
            if p in want:
                assert f[want[p]] == ["1"]


def _check(r, outcome, paths, capsys):
    assert set(r) == TOP and r["outcome"] == outcome and r["http_requests"] == len(paths)
    assert [p["module"] for p in r["profiles"]] == list(MODULES)
    assert all(set(p) == ROW for p in r["profiles"])
    cap = capsys.readouterr()
    blob = json.dumps(r) + cap.out + cap.err
    assert all(s not in blob for s in SECRETS)
    assert os.environ["EZVIZ_CHANNEL"] == "9"
    assert os.environ["EZVIZ_AUTH_BASE_URL"] == "https://example.invalid"


def test_full_batch_passes(monkeypatch, capsys):
    r, paths, forms = _run(monkeypatch, [{"deviceSerial": "PRIVATESERIAL"}])
    assert paths == FULL
    _check(r, "passed", paths, capsys)
    got = [(p["result"], p["http_requests"], p["exit_code"]) for p in r["profiles"]]
    assert got == [("passed", 2, 0)] * 2 + [("passed", 6, 0)] * 2 + [("passed", 2, 0)] * 2
    _posts(forms)


@pytest.mark.parametrize("rows", [[], [{}]], ids=("empty", "missing"))
def test_no_target_not_verified(monkeypatch, capsys, rows):
    r, paths, forms = _run(monkeypatch, rows)
    assert paths == [AUTH, DEVICE_LIST] * 4 and forms == {}
    _check(r, "not_verified", paths, capsys)
    got = [(p["result"], p["http_requests"]) for p in r["profiles"]]
    assert got == [("passed", 2)] * 2 + [("skipped_no_target", 0)] * 2 + [("passed", 2)] * 2


def test_auth_failure_aborts(monkeypatch, capsys):
    r, paths, _ = _run(monkeypatch, [{"deviceSerial": "PRIVATESERIAL"}], bad={AUTH})
    assert paths == [AUTH]
    _check(r, "failed", paths, capsys)
    got = [(p["result"], p["http_requests"]) for p in r["profiles"]]
    assert got == [("failed", 1)] + [("skipped_after_failure", 0)] * 5


def test_partial_inspection_failure(monkeypatch, capsys):
    r, paths, forms = _run(monkeypatch, [{"deviceSerial": "PRIVATESERIAL"}], bad={DEVICE_INFO})
    assert paths == FULL[:10]
    _check(r, "failed", paths, capsys)
    got = [(p["result"], p["http_requests"]) for p in r["profiles"]]
    assert got == [("passed", 2), ("passed", 2), ("failed", 6)] + [("skipped_after_failure", 0)] * 3
    assert r["profiles"][2]["exit_code"] == 2
    _posts(forms)
