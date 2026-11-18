"""Offline local preview of the pinned official @ezuikit/player-hls bundle.

Serves a minimal page bound to 127.0.0.1 that plays a locally generated,
finite H.264 320x180 HLS fixture via the real UMD ``window.HlsPlayer``.
The preview address is issued through the real domestic
``Client.playback.create`` path over a strict ``httpx.MockTransport`` --
no credentials, devices, real media URLs, or network access are used.

Usage: python -m examples.hls_preview --archive PATH [--port N]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from ezviz_unified import Client, PlaybackOptions, RegionConfig

ARCHIVE_SIZE = 1182065
ARCHIVE_SHA256 = "8f7de1d2c96d310025e4d72a1221176d2584f5c98b6fc858dfd9daccca5087c9"
VENDOR_FILES = {  # pinned regular archive member -> absolute HTTP path
    "package/dist/index.umd.js": "/vendor/index.umd.js",
    "package/dist/style/css.css": "/vendor/style/css.css",
    "package/dist/decoder.worker.js": "/vendor/decoder.worker.js",
    "package/dist/decoder.wasm": "/vendor/decoder.wasm",
}
DEVICE_SERIAL = "SYNTHSER01"
FAILURE = "hls-preview: startup failed"

_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "media-src 'self' blob:; worker-src 'self' blob:; "
        "img-src 'self' data: blob:; font-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'none'"
    ),
}
_TYPES = {
    "/": "text/html; charset=utf-8",
    "/preview.js": "text/javascript; charset=utf-8",
    "/preview.json": "application/json; charset=utf-8",
    "/vendor/index.umd.js": "text/javascript; charset=utf-8",
    "/vendor/style/css.css": "text/css; charset=utf-8",
    "/vendor/decoder.worker.js": "text/javascript; charset=utf-8",
    "/vendor/decoder.wasm": "application/wasm",
    "/media/preview.m3u8": "application/vnd.apple.mpegurl",
}


def preview_url(client: Client, device_serial: str, *, channel: int = 1) -> str:
    """Issue a domestic HLS preview address; return its ``data.url`` string."""
    response = client.playback.create(
        device_serial, PlaybackOptions(channel=channel, protocol=2, kind=1)
    )
    data = response.data
    url = data.get("url") if isinstance(data, Mapping) else None
    if (
        not isinstance(url, str)
        or not url
        or any(ord(ch) < 0x20 or ch == "\x7f" or ch.isspace() for ch in url)
    ):
        raise ValueError("playback.create response is missing a usable data.url")
    try:
        parts = urllib.parse.urlsplit(url)
        parts.port  # force lazy port parsing errors now
        host = parts.hostname
    except ValueError:
        raise ValueError("playback.create response is missing a usable data.url") from None
    if (
        parts.scheme.lower() not in ("http", "https")
        or host is None
        or not host
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("playback.create response is missing a usable data.url")
    return url


def demo_address(origin: str) -> str:
    """Return origin + /media/preview.m3u8 as issued by the real SDK client."""
    expected = origin + "/media/preview.m3u8"
    calls = {"auth": 0, "biz": 0}

    def checked_form(request: httpx.Request, path: str) -> dict[str, list[str]]:
        url = request.url
        if (
            request.method != "POST"
            or url.scheme != "https"
            or url.host != "open.ys7.com"
            or url.port not in (None, 443)
            or url.username
            or url.password
            or url.path != path
            or url.query != b""
            or url.fragment
        ):
            raise RuntimeError("unexpected SDK request")
        try:
            body = request.content.decode("utf-8")
        except UnicodeDecodeError:
            raise RuntimeError("unexpected SDK request body") from None
        try:
            return urllib.parse.parse_qs(body, strict_parsing=True, keep_blank_values=True)
        except ValueError:
            raise RuntimeError("unexpected SDK request body") from None

    def auth(request: httpx.Request) -> httpx.Response:
        calls["auth"] += 1
        form = checked_form(request, "/api/lapp/token/get")
        if form != {"appKey": ["SYNTHKEY"], "appSecret": ["SYNTHSECRET"]}:
            raise RuntimeError("unexpected auth form fields")
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": "SYNTH_TOKEN",
                    "expireTime": 9999999999999,
                    "areaDomain": "https://open.ys7.com",
                },
            },
        )

    def biz(request: httpx.Request) -> httpx.Response:
        calls["biz"] += 1
        form = checked_form(request, "/api/lapp/v2/live/address/get")
        if form != {
            "accessToken": ["SYNTH_TOKEN"],
            "deviceSerial": [DEVICE_SERIAL],
            "channelNo": ["1"],
            "protocol": ["2"],
            "type": ["1"],
        }:
            raise RuntimeError("unexpected biz form fields")
        return httpx.Response(200, json={"code": "200", "data": {"ret": 200, "url": expected}})

    client = Client(
        "SYNTHKEY",
        "SYNTHSECRET",
        region=RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(biz),
    )
    try:
        url = preview_url(client, DEVICE_SERIAL)
    finally:
        client.close()
    if url != expected:
        raise RuntimeError("playback.create returned an unexpected address")
    if calls != {"auth": 1, "biz": 1}:
        raise RuntimeError("unexpected request count")
    return url


def load_vendor(archive: Path) -> dict[str, bytes]:
    """Read only the allowlisted regular members of the pinned official archive."""
    path = Path(archive)
    try:
        if not path.is_file() or path.stat().st_size != ARCHIVE_SIZE:
            raise ValueError("archive does not match the pinned official component")
        with path.open("rb") as handle:
            raw = handle.read(ARCHIVE_SIZE + 1)
    except OSError as exc:
        raise ValueError("archive does not match the pinned official component") from exc
    if len(raw) != ARCHIVE_SIZE or hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("archive does not match the pinned official component")
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        for member in tar.getmembers():
            name = member.name[2:] if member.name.startswith("./") else member.name
            http_path = VENDOR_FILES.get(name)
            if http_path is None:
                continue
            if not member.isfile() or http_path in found:
                raise ValueError("pinned archive member is not a regular file")
            payload = tar.extractfile(member)
            if payload is None:
                raise ValueError("pinned archive member is not a regular file")
            found[http_path] = payload.read()
    if len(found) != len(VENDOR_FILES):
        raise ValueError("pinned archive is missing required members")
    return found


def _generate_media(tmp: str) -> dict[str, bytes]:
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=24",
        "-t",
        "11",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "24",
        "-sc_threshold",
        "0",
        "-hls_time",
        "1",
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        "segment-%03d.ts",
        "preview.m3u8",
    ]
    try:
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("ffmpeg HLS fixture generation failed") from exc
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg HLS fixture generation failed")
    playlist = Path(tmp, "preview.m3u8").read_bytes()
    if b"#EXT-X-ENDLIST" not in playlist:
        raise RuntimeError("generated playlist is not finite")
    routes = {"/media/" + p.name: p.read_bytes() for p in sorted(Path(tmp).glob("segment-*.ts"))}
    if not routes:
        raise RuntimeError("ffmpeg produced no segments")
    for line in playlist.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "/media/" + line not in routes:
            raise RuntimeError("playlist references a segment that is not served")
    routes["/media/preview.m3u8"] = playlist
    return routes


class _Handler(BaseHTTPRequestHandler):
    server_version = "LocalHLSPreview"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def _serve(self, head: bool):
        body = self.server.routes.get(self.path)  # exact match: no query, traversal, or fallback
        if self.headers.get("Host") != self.server.host or body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            body = b""
        else:
            self.send_response(200)
            ctype = _TYPES.get(self.path)
            if ctype is None and self.path.startswith("/media/") and self.path.endswith(".ts"):
                ctype = "video/mp2t"
            self.send_header("Content-Type", ctype or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
        for key, value in _HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        if body and not head:
            self.wfile.write(body)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="examples.hls_preview", description=__doc__, exit_on_error=False
    )
    parser.add_argument(
        "--archive", type=Path, required=True, help="pinned player-hls .tgz archive"
    )
    parser.add_argument(
        "--port", type=int, default=0, help="port to bind on 127.0.0.1 (0 = ephemeral)"
    )
    try:
        args = parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit) as exc:  # fixed failure; never echo argv
        if isinstance(exc, SystemExit) and exc.code == 0:
            return 0
        print(FAILURE, file=sys.stderr)
        return 1
    if not 0 <= args.port <= 65535:
        print(FAILURE, file=sys.stderr)
        return 1

    def or_die(factory) -> bytes:
        try:
            return factory()
        except OSError as exc:
            raise RuntimeError("missing bundled preview asset") from exc

    try:
        assets = Path(__file__).with_name("hls_preview_assets")
        index = or_die(lambda: (assets / "index.html").read_bytes())
        script = or_die(lambda: (assets / "preview.js").read_bytes())
        vendor = load_vendor(args.archive)  # read pinned archive before invoking FFmpeg
        with tempfile.TemporaryDirectory(prefix="hls-preview-") as tmp:
            media = _generate_media(tmp)
            with ThreadingHTTPServer(("127.0.0.1", args.port), _Handler) as server:
                port = server.server_address[1]
                origin = f"http://127.0.0.1:{port}"
                url = demo_address(origin)  # real SDK client over strict MockTransport
                server.host = f"127.0.0.1:{port}"
                server.routes = {
                    "/": index,
                    "/preview.js": script,
                    "/preview.json": json.dumps({"url": url}, separators=(",", ":")).encode(
                        "utf-8"
                    ),
                    **vendor,
                    **media,
                }
                print(origin, flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
    except Exception:
        print(FAILURE, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
