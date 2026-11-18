"""Offline tests for ``examples.hls_preview`` (ACC-MEDIA-HLS-H264-LOCAL-001).

Synthetic MockTransport only: no platform reach, real credentials, real media
URLs, browsers, sockets, ffmpeg or checked-in official component bytes.
"""

import http.server
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import httpx
import pytest

from examples import hls_preview as demo
from ezviz_unified import Client, RegionConfig

TOKEN_PATH = "/api/lapp/token/get"
ADDRESS_PATH = "/api/lapp/v2/live/address/get"
LOOPBACK_URL = "http://127.0.0.1:43123/media/preview.m3u8"
HTTPS_URL = "https://media.example.invalid/hls/preview.m3u8"
PRIVATE = "PRIVATE-9f4b7c-marker"
EXTENSIONLESS_URL = f"https://media.example.invalid/{PRIVATE}"
ASSETS_DIR = Path(demo.__file__).resolve().parent / "hls_preview_assets"
MISSING = object()


def _client(payload, calls, requests):
    """Real SDK Client whose only HTTP layer is the synthetic handler.

    Returns the real domestic envelope ``{code, msg, data}``; ``data`` keeps
    the documented ``ret``/``url`` fields that ``playback.create`` unwraps.
    """

    def handler(request):
        calls.append((request.method, request.url.path))
        requests.append(request)
        if request.url.path == TOKEN_PATH:
            data = {
                "accessToken": "TOKEN",
                "expireTime": 9999999999999,
                "areaDomain": "https://open.ys7.com",
            }
        else:
            data = {"ret": 200} if payload is MISSING else {"ret": 200, "url": payload}
        return httpx.Response(200, json={"code": "200", "msg": "ok", "data": data})

    return Client(
        "SYNTHKEY",
        "SYNTHSECRET",
        region=RegionConfig.china(),
        auth_transport=httpx.MockTransport(handler),
        transport_factory=lambda origin: httpx.MockTransport(handler),
    )


def _strict_form(body):
    """Decode a urlencoded form without collapsing or dropping anything."""
    return urllib.parse.parse_qs(body.decode(), strict_parsing=True, keep_blank_values=True)


@pytest.mark.parametrize("media_url", [LOOPBACK_URL, HTTPS_URL, EXTENSIONLESS_URL])
def test_preview_url_posts_exact_form_and_returns_url(media_url):
    calls, requests = [], []
    with _client(media_url, calls, requests) as client:
        result = demo.preview_url(client, "SYNTHCAM", channel=1)
    assert result == media_url  # not hardcoded; whatever the SDK returned
    assert calls == [("POST", TOKEN_PATH), ("POST", ADDRESS_PATH)]

    # Every request targets the verified domestic origin, POST only, no query,
    # no fragment, and a single exact urlencoded form body.
    for request in requests:
        url = request.url
        assert (url.scheme, url.host, 443 if url.port is None else url.port) == (
            "https",
            "open.ys7.com",
            443,
        )
        assert url.query == b"" and not url.fragment
        assert not url.username and not url.password
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"

    # Exact decoded forms; no duplicate keys and no extra or missing fields.
    assert _strict_form(requests[0].content) == {
        "appKey": ["SYNTHKEY"],
        "appSecret": ["SYNTHSECRET"],
    }
    assert _strict_form(requests[1].content) == {
        "accessToken": ["TOKEN"],
        "deviceSerial": ["SYNTHCAM"],
        "channelNo": ["1"],
        "protocol": ["2"],
        "type": ["1"],
    }


@pytest.mark.parametrize("payload", [MISSING, None, 777, ["nested"]])
def test_preview_url_rejects_bad_url_sanitized(payload):
    calls, requests = [], []
    with _client(payload, calls, requests) as client:
        with pytest.raises(ValueError) as excinfo:
            demo.preview_url(client, "SYNTHCAM", channel=1)
    # single issuance attempt only; no refetch on bad data
    assert calls == [("POST", TOKEN_PATH), ("POST", ADDRESS_PATH)]
    if payload not in (MISSING, None):
        assert repr(payload) not in str(excinfo.value)  # sanitized message


@pytest.mark.parametrize(
    "url",
    [
        f"https://user:{PRIVATE}@media.example.invalid/hls/preview.m3u8",  # credentialed
        f"https://media.example.invalid:bad/{PRIVATE}",  # malformed port
        f"ftp://media.example.invalid/{PRIVATE}",  # non-HTTP scheme
        f"//media.example.invalid/{PRIVATE}",  # scheme-relative, no scheme
    ],
)
def test_preview_url_rejects_unusable_url_without_echo(url):
    calls, requests = [], []
    with _client(url, calls, requests) as client:
        with pytest.raises(ValueError) as excinfo:
            demo.preview_url(client, "SYNTHCAM", channel=1)
    # each case is a clean, single issuance; the raw payload is never echoed
    assert calls == [("POST", TOKEN_PATH), ("POST", ADDRESS_PATH)]
    assert PRIVATE not in str(excinfo.value)


@pytest.mark.parametrize("port", [43123, 8000])
def test_demo_address_returns_exact_loopback_url(port):
    base = f"http://127.0.0.1:{port}"
    assert demo.demo_address(base) == f"{base}/media/preview.m3u8"


def _snapshot(root):
    return None if not root.exists() else sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.parametrize("kind", ["missing", "truncated", "wrong-digest"])
def test_load_vendor_rejects_bad_archive(tmp_path, monkeypatch, kind):
    def boom(*args, **kwargs):
        raise AssertionError("load_vendor must not launch commands")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)
    archive = tmp_path / "player-hls-2.0.0.tgz"
    if kind == "truncated":
        # gzip magic but far below the pinned size: rejected before any parse
        archive.write_bytes(b"\x1f\x8b\x08truncated")
    elif kind == "wrong-digest":
        # exactly the pinned size but wrong sha256: rejected on the digest check
        archive.write_bytes(bytes(demo.ARCHIVE_SIZE))
    before = _snapshot(tmp_path)  # capture the whole workspace, incl. archive bytes
    with pytest.raises(ValueError):
        demo.load_vendor(archive)
    # refusal is pure: no command ran (monkeypatch would have raised) and the
    # workspace is untouched beyond the fixture bytes we wrote ourselves
    assert _snapshot(tmp_path) == before


def test_main_serves_exact_html_and_js_bytes(tmp_path, monkeypatch):
    """Startup mounts the exact ``index.html``/``preview.js`` bytes from disk.

    Only the real seams are replaced: the bound ``ThreadingHTTPServer`` is
    captured and stopped instead of serving sockets, the ffmpeg fixture and
    the pinned-archive read are in-memory test doubles, and the temporary
    workspace is ``tmp_path`` so nothing outside it can be touched.
    """
    index = (ASSETS_DIR / "index.html").read_bytes()
    script = (ASSETS_DIR / "preview.js").read_bytes()
    archive = tmp_path / "player-hls-2.0.0.tgz"
    archive.write_bytes(b"fixture")  # content is irrelevant; load_vendor is doubled

    vendor = {
        "/vendor/index.umd.js": b"V-umd",
        "/vendor/style/css.css": b"V-css",
        "/vendor/decoder.worker.js": b"V-worker",
        "/vendor/decoder.wasm": b"V-wasm",
    }
    media = {"/media/preview.m3u8": b"#EXTM3U\n#EXT-X-ENDLIST\n", "/media/segment-000.ts": b"SEG"}
    monkeypatch.setattr(demo, "load_vendor", lambda path: dict(vendor))
    monkeypatch.setattr(demo, "_generate_media", lambda tmp: dict(media))

    captured = {}
    real_tempdir = demo.tempfile.TemporaryDirectory

    class Server(http.server.HTTPServer):  # stands in for ThreadingHTTPServer
        def __init__(self, address, handler):
            captured["address"] = address
            captured["handler"] = handler
            captured["server"] = self
            self.server_address = ("127.0.0.1", 49999)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def serve_forever(self):
            raise KeyboardInterrupt  # unblock main() without any sockets

    monkeypatch.setattr(demo, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(
        demo.tempfile, "TemporaryDirectory", lambda *a, **k: real_tempdir(dir=tmp_path)
    )

    before = _snapshot(ASSETS_DIR)
    assert demo.main(["--archive", str(archive), "--port", "0"]) == 0

    inst = captured["server"]
    routes = inst.routes
    assert routes["/"] == index  # exact html bytes, not a constant
    assert routes["/preview.js"] == script  # exact js bytes
    assert routes["/vendor/index.umd.js"] == vendor["/vendor/index.umd.js"]
    assert routes["/media/preview.m3u8"] == media["/media/preview.m3u8"]
    body = json.loads(routes["/preview.json"])
    assert body == {"url": "http://127.0.0.1:49999/media/preview.m3u8"}
    assert captured["address"] == ("127.0.0.1", 0)  # bound to loopback only
    assert captured["handler"] is demo._Handler
    assert inst.host == "127.0.0.1:49999"
    assert _snapshot(ASSETS_DIR) == before
    # nothing but our fixture archive and the (auto-removed) workdir under tmp_path
    leftovers = [p for p in tmp_path.rglob("*") if p.name != archive.name]
    assert leftovers == []


def test_cli_help_is_offline_and_mentions_archive():
    root = Path(demo.__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "examples.hls_preview", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "--archive" in proc.stdout
    assert "--port" in proc.stdout
