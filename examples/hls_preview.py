"""Offline local preview of the pinned official @ezuikit/player-hls bundle.

Serves a minimal page bound to 127.0.0.1 that plays a locally generated,
finite 320x180 HLS fixture via the real UMD ``window.HlsPlayer``. The
default ``--codec h264`` keeps the H.264 clip on ``/media/preview.m3u8``;
``--codec h265`` generates an HEVC clip served at ``/media/hevc.m3u8``,
which the page always plays through the official soft-decoder path.
The preview address is issued through the real domestic
``Client.playback.create`` path over a strict ``httpx.MockTransport`` --
no credentials, devices, real media URLs, or network access are used.

Usage: python -m examples.hls_preview --archive PATH [--codec h264|h265] [--port N]
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
CODECS = {"h264": "/media/preview.m3u8", "h265": "/media/hevc.m3u8"}

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
    "/media/hevc.m3u8": "application/vnd.apple.mpegurl",
}


def preview_url(
    client: Client, device_serial: str, *, channel: int = 1, codec: str = "h264"
) -> str:
    """Issue a domestic HLS preview address; return its ``data.url`` string."""
    if codec == "h264":
        # Default wire form is unchanged: the optional fields stay omitted.
        options = PlaybackOptions(channel=channel, protocol=2, kind=1)
    elif codec == "h265":
        options = PlaybackOptions(
            channel=channel,
            protocol=2,
            kind=1,
            support_h265=True,
            container_format=0,
        )
    else:
        raise ValueError("codec must be 'h264' or 'h265'")
    response = client.playback.create(device_serial, options)
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


def demo_address(origin: str, *, codec: str = "h264") -> str:
    """Return origin + the codec's media path as issued by the real SDK client."""
    expected = origin + CODECS[codec]
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
        expected_form = {
            "accessToken": ["SYNTH_TOKEN"],
            "deviceSerial": [DEVICE_SERIAL],
            "channelNo": ["1"],
            "protocol": ["2"],
            "type": ["1"],
        }
        if codec == "h265":
            expected_form["supportH265"] = ["1"]
            expected_form["containerFormat"] = ["0"]
        if form != expected_form:
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
        url = preview_url(client, DEVICE_SERIAL, codec=codec)
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


def _generate_media(tmp: str, *, codec: str = "h264") -> dict[str, bytes]:
    """Generate the finite HLS fixture; return only the selected codec's routes."""
    if codec == "h265":
        # 12s loop: left quarter is a fixed white reference; the rest cycles
        # red -> green -> blue every second so the verifier can match color
        # and media time. FFmpeg takes the whole graph as one argument.
        lavfi = (
            "color=c=red:s=320x180:r=15,"
            "drawbox=color=lime:t=fill:enable='between(mod(t,3),1,2)',"
            "drawbox=color=blue:t=fill:enable='gte(mod(t,3),2)',"
            "drawbox=x=0:y=0:w=iw/4:h=ih:color=white:t=fill"
        )
        extra = [
            "-i",
            lavfi,
            "-t",
            "12",
            "-an",
            "-c:v",
            "libx265",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-x265-params",
            "pools=1:frame-threads=1:bframes=0:scenecut=0:keyint=15:"
            "min-keyint=15:repeat-headers=1:log-level=error",
        ]
    else:
        extra = [
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
        ]
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        *extra,
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
    # Serve the generated playlist only at the codec's fixed public path:
    # /media/preview.m3u8 for h264, /media/hevc.m3u8 for h265.
    if not playlist.startswith(b"#EXTM3U"):
        raise RuntimeError("generated playlist is not usable")
    routes[CODECS[codec]] = playlist
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
        "--codec",
        choices=sorted(CODECS),
        default="h264",
        help="fixture codec: h264 serves /media/preview.m3u8 (default); "
        "h265 serves /media/hevc.m3u8",
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
            # The default h264 path keeps the original one-argument calls so
            # injected test doubles that only accept (tmp)/(origin) still work.
            if args.codec == "h264":
                media = _generate_media(tmp)
            else:
                media = _generate_media(tmp, codec=args.codec)
            with ThreadingHTTPServer(("127.0.0.1", args.port), _Handler) as server:
                port = server.server_address[1]
                origin = f"http://127.0.0.1:{port}"
                if args.codec == "h264":
                    url = demo_address(origin)  # real SDK client over MockTransport
                else:
                    url = demo_address(origin, codec=args.codec)
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
