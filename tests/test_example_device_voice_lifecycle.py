"""Offline lifecycle tests for ``examples.device_voice_lifecycle``."""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import redirect_stderr, redirect_stdout

import httpx
import pytest

import examples.device_voice_lifecycle as demo

USAGE = "usage: python -m examples.device_voice_lifecycle --mode sync|async"
ERR = "error: simulated device voice workflow failed"
CANCEL = "interrupted: simulated device voice workflow cancelled"
ALT_VOICE_ID = 37  # differs from the demo's issued id on purpose
VOICE_PATH = "/api/route/voice/v3/devices/voices"
AUTH_PATH = "/api/lapp/token/get"
DOMESTIC_ORIGIN = "https://open.ys7.com"
MODES = ("sync", "async")


def _origin(req):
    """Bare scheme://host origin; the documented endpoints use no port."""
    return f"{req.url.scheme}://{req.url.host}"


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = demo.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def _kind(req):
    if req.url.path == AUTH_PATH:
        return "auth"
    if req.url.path == VOICE_PATH:
        return f"voice:{req.method}"
    return f"other:{req.method}:{req.url.path}"


@pytest.fixture
def wire(monkeypatch):
    """Log every MockTransport request; forbid any real transport.

    ``override``/``fail`` let tests substitute the create payload or turn an
    operation into a business error while staying on the in-memory mocks.
    """
    rec = {"requests": [], "override": None, "fail": set(), "issued": None}
    orig_sync = httpx.MockTransport.handle_request
    orig_async = httpx.MockTransport.handle_async_request

    def _map(req, resp):
        kind = _kind(req)
        if kind in rec["fail"]:
            return httpx.Response(
                200, json={"meta": {"code": 50001, "message": "boom"}}, request=req
            )
        if kind == "voice:POST" and resp.status_code == 200:
            if rec["override"] == "alt_id":
                rec["issued"] = ALT_VOICE_ID
                return httpx.Response(
                    200,
                    json={
                        "meta": {"code": 200, "message": "ok"},
                        "voiceInfo": {"voiceId": ALT_VOICE_ID},
                    },
                    request=req,
                )
            rec["issued"] = resp.json()["voiceInfo"]["voiceId"]
            if rec["override"] == "malformed":
                # meta ok but a non-integer voiceId violates the contract;
                # the SDK must reject the envelope as a protocol error.
                return httpx.Response(
                    200,
                    json={
                        "meta": {"code": 200, "message": "ok"},
                        "voiceInfo": {"voiceId": True},
                    },
                    request=req,
                )
        if kind == "voice:DELETE" and rec["issued"] is not None:
            # The fixture acts as one coherent fake server: whatever id the
            # simulated create response issued (the demo's own, or the
            # alternate under the "alt_id" override — which the demo's
            # built-in DELETE mock would wrongly reject) is the id this
            # mock accepts. Assert the full documented request shape —
            # HTTPS open.ys7.com origin, exactly the five query keys with
            # the same serial/name/url as the scripted material — before
            # acknowledging with a meta-only 200.
            assert _origin(req) == DOMESTIC_ORIGIN
            items = req.url.params.multi_items()
            assert len(items) == 5 and {key for key, _ in items} == {
                "accessToken",
                "deviceSerial",
                "voiceName",
                "voiceUrl",
                "voiceId",
            }
            params = req.url.params
            assert params["accessToken"] == demo._ACCESS_TOKEN
            assert params["deviceSerial"] == demo._DEVICE_SERIAL
            assert params["voiceName"] == demo._VOICE_NAME
            assert params["voiceUrl"] == demo._VOICE_URL
            assert params["voiceId"] == str(rec["issued"])
            return httpx.Response(
                200,
                json={"meta": {"code": 200, "message": "ok"}},
                request=req,
            )
        return resp

    def sync_wrap(self, req):
        rec["requests"].append(req)
        return _map(req, orig_sync(self, req))

    async def async_wrap(self, req):
        rec["requests"].append(req)
        return _map(req, await orig_async(self, req))

    def no_real(self, req):
        raise AssertionError("real HTTP transport must not be used")

    async def no_real_async(self, req):
        raise AssertionError("real async HTTP transport must not be used")

    monkeypatch.setattr(httpx.MockTransport, "handle_request", sync_wrap)
    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", async_wrap)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_real)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_real_async)
    return rec


def _kinds(rec):
    return [_kind(r) for r in rec["requests"]]


def _voice(rec, method):
    return [r for r in rec["requests"] if _kind(r) == f"voice:{method}"]


@pytest.mark.parametrize("mode", MODES)
def test_ok(wire, mode, monkeypatch):
    monkeypatch.setenv("EZVIZ_REGION", "overseas")
    monkeypatch.setenv("EZVIZ_APP_KEY", "bogus")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "bogus")
    rc, out, err = _run(["--mode", mode])
    assert rc == 0 and err == ""
    assert json.loads(out) == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "simulation": True,
    }
    assert _kinds(wire) == ["auth", "voice:POST", "voice:DELETE"]
    post, delete = _voice(wire, "POST")[0], _voice(wire, "DELETE")[0]
    # Both write operations hit the documented domestic HTTPS origin;
    # the material's voiceUrl is never fetched.
    assert _origin(post) == _origin(delete) == DOMESTIC_ORIGIN


@pytest.mark.parametrize("mode", MODES)
def test_delete_uses_returned_id(wire, mode):
    """The alternate fake server issues ALT_VOICE_ID; the SDK's DELETE must
    send exactly that id — the fixture acknowledges only after asserting it."""
    wire["override"] = "alt_id"
    rc, _, _ = _run(["--mode", mode])
    assert rc == 0
    # Exactly one attempt per operation, in create-then-delete order, all on
    # the in-memory mocks (real transports raise in the fixture itself).
    assert _kinds(wire) == ["auth", "voice:POST", "voice:DELETE"]
    post, delete = _voice(wire, "POST")[0], _voice(wire, "DELETE")[0]
    assert delete.url.params["voiceId"] == str(ALT_VOICE_ID)
    for key in ("deviceSerial", "voiceName", "voiceUrl"):
        assert delete.url.params[key] == post.url.params[key]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("breakage", ("fail", "malformed"))
def test_create_failure_no_delete(wire, mode, breakage):
    if breakage == "fail":
        wire["fail"].add("voice:POST")
    else:
        wire["override"] = "malformed"
    rc, out, err = _run(["--mode", mode])
    assert rc == 1 and out == "" and err.strip() == ERR
    # A failed create issues no id, so no DELETE is ever attempted.
    assert _kinds(wire) == ["auth", "voice:POST"]


@pytest.mark.parametrize("mode", MODES)
def test_delete_failure(wire, mode):
    wire["fail"].add("voice:DELETE")
    rc, out, err = _run(["--mode", mode])
    assert rc == 1 and out == "" and err.strip() == ERR
    # exactly one attempt per operation; no retries
    assert _kinds(wire) == ["auth", "voice:POST", "voice:DELETE"]


@pytest.mark.parametrize("flag", ("-h", "--help"))
def test_help(flag):
    rc, out, err = _run([flag])
    assert rc == 0 and out.strip() == USAGE and err == ""


@pytest.mark.parametrize(
    "argv",
    [[], ["--mode"], ["--mode", "live"], ["--live"], ["--mode", "sync", "x"], ["-m", "sync"]],
)
def test_bad_args(argv):
    rc, out, err = _run(argv)
    assert rc == 1 and out == "" and err.strip() == USAGE


@pytest.mark.parametrize("mode", MODES)
def test_interrupted(wire, mode, monkeypatch):
    exc = KeyboardInterrupt if mode == "sync" else asyncio.CancelledError
    orig_sync = httpx.MockTransport.handle_request
    orig_async = httpx.MockTransport.handle_async_request

    def boom_sync(self, req):
        if _kind(req) == "voice:DELETE":
            raise exc()
        return orig_sync(self, req)

    async def boom_async(self, req):
        if _kind(req) == "voice:DELETE":
            raise exc()
        return await orig_async(self, req)

    monkeypatch.setattr(httpx.MockTransport, "handle_request", boom_sync)
    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", boom_async)
    rc, out, err = _run(["--mode", mode])
    assert rc == 130 and out == "" and err.strip() == CANCEL
