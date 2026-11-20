"""Play a locally generated H.264 FLV with the pinned official EzuikitFlv UMD.

Usage: python -m examples.flv_preview --archive PATH [--port N]

The loopback address is issued through Client.playback.create over two
synthetic overseas MockTransport responses. FFmpeg generates the finite clip.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from examples.hls_preview import _HEADERS
from ezviz_unified import Client, PlaybackOptions, RegionConfig

ARCHIVE_SIZE = 1192055
ARCHIVE_SHA256 = "4f5d247f1a1c0dbeec439c4fcde1bba5067f5a55d16d9f7368a0b9ca570601fc"
VENDOR_FILES = {
    "package/index.js": "/vendor/index.js",
    "package/style.css": "/vendor/style.css",
    "package/decoder.js": "/vendor/decoder.js",
    "package/decoder.wasm": "/vendor/decoder.wasm",
}
DEVICE_SERIAL = "SYNTHSER01"
MEDIA_PATH = "/media/preview.flv"
FAILURE = "flv-preview: startup failed"

_TYPES = {
    "/": "text/html; charset=utf-8",
    "/player.html": "text/html; charset=utf-8",
    "/preview.js": "text/javascript; charset=utf-8",
    "/preview.json": "application/json; charset=utf-8",
    "/vendor/index.js": "text/javascript; charset=utf-8",
    "/vendor/style.css": "text/css; charset=utf-8",
    "/vendor/decoder.js": "text/javascript; charset=utf-8",
    "/vendor/decoder.wasm": "application/wasm",
    MEDIA_PATH: "video/x-flv",
}


def demo_address(origin: str) -> str:
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", origin)
    if match is None or int(match.group(1)) > 65535:
        raise ValueError("invalid loopback origin")
    expected = origin + MEDIA_PATH
    calls = []

    def checked_form(request, exact_url, fields):
        if request.method != "POST" or str(request.url) != exact_url:
            raise AssertionError("unexpected request line")
        content_type = request.headers.get("content-type", "")
        if content_type.split(";")[0].strip() != "application/x-www-form-urlencoded":
            raise AssertionError("expected form content type")
        parsed = urllib.parse.parse_qs(
            request.content.decode(), keep_blank_values=True, strict_parsing=True
        )
        if parsed != {name: [value] for name, value in fields.items()}:
            raise AssertionError("unexpected form fields")

    def auth(request):
        if calls:
            raise AssertionError("auth must run first")
        checked_form(
            request,
            "https://open.ezvizlife.com/api/lapp/token/get",
            {"appKey": "SYNTHKEY", "appSecret": "SYNTHSECRET"},
        )
        calls.append("auth")
        return httpx.Response(
            200,
            json={
                "code": "200",
                "msg": "ok",
                "data": {
                    "accessToken": "SYNTH_TOKEN",
                    "expireTime": 9999999999999,
                    "areaDomain": "https://iusopen.ezvizlife.com",
                },
            },
        )

    def biz(request):
        if calls != ["auth"]:
            raise AssertionError("business must follow auth")
        checked_form(
            request,
            "https://iusopen.ezvizlife.com/api/lapp/live/address/get",
            {
                "accessToken": "SYNTH_TOKEN",
                "deviceSerial": DEVICE_SERIAL,
                "channelNo": "1",
                "protocol": "4",
                "type": "1",
            },
        )
        calls.append("biz")
        return httpx.Response(
            200,
            json={
                "code": "200",
                "msg": "ok",
                "data": {
                    "id": "synthetic-id",
                    "url": expected,
                    "expireTime": "2099-01-01 00:00:00",
                },
            },
        )

    with Client(
        "SYNTHKEY",
        "SYNTHSECRET",
        region=RegionConfig.overseas(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(biz),
    ) as client:
        response = client.playback.create(
            DEVICE_SERIAL, PlaybackOptions(channel=1, protocol=4, kind=1)
        )
        data = response.data
        if not isinstance(data, Mapping) or data.get("url") != expected:
            raise AssertionError("unexpected playback data")
        if calls != ["auth", "biz"]:
            raise AssertionError("unexpected request sequence")
        return data["url"]


def _generate_media(directory) -> bytes:
    filter_graph = (
        "color=red:s=320x180:r=12,"
        "drawbox=c=lime:t=fill:enable='between(mod(t,3),1,2)',"
        "drawbox=c=blue:t=fill:enable='gte(mod(t,3),2)'"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            filter_graph,
            "-t",
            "12",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "12",
            "-bf",
            "0",
            "-f",
            "flv",
            "preview.flv",
        ],
        cwd=directory,
        timeout=60,
        capture_output=True,
        check=True,
    )
    payload = Path(directory, "preview.flv").read_bytes()
    if len(payload) < 10 or payload[:9] != b"FLV\x01\x01\x00\x00\x00\x09":
        raise RuntimeError("ffmpeg did not produce a video-only FLV")
    return payload


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


def _headers_for(target: str) -> dict[str, str]:
    headers = dict(_HEADERS)
    csp = headers["Content-Security-Policy"]
    if target == "/":
        headers["Content-Security-Policy"] = csp + "; frame-src 'self'"
    elif target == "/player.html":
        headers["Content-Security-Policy"] = csp.replace(
            "frame-ancestors 'none'", "frame-ancestors 'self'"
        )
    return headers


class _Handler(BaseHTTPRequestHandler):
    server_version = "LocalFLVPreview"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def _serve(self, head: bool):
        # BaseHTTPRequestHandler normalizes a leading // in self.path.
        target = self.requestline.split()[1]
        body = self.server.routes.get(target)
        if self.headers.get_all("Host") != [self.server.host] or body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            target = ""
            body = b""
        else:
            self.send_response(200)
            self.send_header("Content-Type", _TYPES[target])
            self.send_header("Content-Length", str(len(body)))
        for key, value in _headers_for(target).items():
            self.send_header(key, value)
        self.end_headers()
        if body and not head:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise argparse.ArgumentError(None, "invalid arguments")


def main(argv=None) -> int:
    parser = _Parser(prog="examples.flv_preview", description=__doc__, exit_on_error=False)
    parser.add_argument(
        "--archive", type=Path, required=True, help="pinned ezuikit-flv .tgz archive"
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

    try:
        assets = Path(__file__).with_name("flv_preview_assets")
        index = (assets / "index.html").read_bytes()
        script = (assets / "preview.js").read_bytes()
        vendor = load_vendor(args.archive)  # read pinned archive before invoking FFmpeg
        with tempfile.TemporaryDirectory(prefix="flv-preview-") as tmp:
            media = _generate_media(tmp)
            with ThreadingHTTPServer(("127.0.0.1", args.port), _Handler) as server:
                port = server.server_address[1]
                origin = f"http://127.0.0.1:{port}"
                url = demo_address(origin)
                server.host = f"127.0.0.1:{port}"
                server.routes = {
                    "/": index,
                    "/player.html": (assets / "player.html").read_bytes(),
                    "/preview.js": script,
                    "/preview.json": json.dumps({"url": url}, separators=(",", ":")).encode(
                        "utf-8"
                    ),
                    **vendor,
                    MEDIA_PATH: media,
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
