"""Offline contract tests for ``examples.flv_preview``."""

import hashlib
import importlib
import io
import json
import tarfile
import urllib.parse
from pathlib import Path

import httpx
import pytest

ORIGIN = "http://127.0.0.1:49321"
MEDIA_URL = ORIGIN + "/media/preview.flv"
VENDOR = ("index.js", "style.css", "decoder.js", "decoder.wasm")
ROUTES = {"/", "/player.html", "/preview.js", "/preview.json", "/media/preview.flv"} | {
    f"/vendor/{name}" for name in VENDOR
}


@pytest.fixture()
def flv():
    try:
        return importlib.import_module("examples.flv_preview")
    except ImportError as exc:
        pytest.fail(f"examples.flv_preview is required: {exc}")


def _entries():
    return [(f"package/{n}", f"bytes:{n}".encode(), tarfile.REGTYPE) for n in VENDOR]


def _tgz(path, entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, payload, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            else:
                info.linkname = "x"
                tar.addfile(info)
    blob = buffer.getvalue()
    path.write_bytes(blob)
    return blob


def _pin(monkeypatch, flv, blob):
    monkeypatch.setattr(flv, "ARCHIVE_SIZE", len(blob))
    monkeypatch.setattr(flv, "ARCHIVE_SHA256", hashlib.sha256(blob).hexdigest())


def _form(request):
    assert request.method == "POST"
    assert request.url.scheme == "https"
    assert not request.url.query
    ctype = request.headers.get("content-type", "")
    assert ctype.split(";")[0].strip() == "application/x-www-form-urlencoded"
    pairs = urllib.parse.parse_qsl(request.read().decode(), keep_blank_values=True)
    assert len(pairs) == len({name for name, _ in pairs})
    return dict(pairs)


def _deep(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _deep(value, key)
            if found is not None:
                return found
    return None


def test_demo_address_posts(flv, monkeypatch):
    requests, responses = [], []
    transport = httpx.MockTransport

    class Recorder(transport):
        def __init__(self, handler):
            def wrapped(request):
                requests.append(request)
                response = handler(request)
                responses.append(response)
                return response

            super().__init__(wrapped)

    monkeypatch.setattr(httpx, "MockTransport", Recorder)
    monkeypatch.setattr(flv, "MockTransport", Recorder, raising=False)
    url = flv.demo_address(ORIGIN)
    assert url == MEDIA_URL
    assert len(requests) == 2
    auth, business = requests
    assert auth.url.host == "open.ezvizlife.com"
    assert auth.url.path == "/api/lapp/token/get"
    auth_form = _form(auth)
    assert set(auth_form) == {"appKey", "appSecret"}
    assert all(auth_form.values())
    assert business.url.host == "iusopen.ezvizlife.com"
    assert business.url.path == "/api/lapp/live/address/get"
    form = _form(business)
    assert set(form) == {"accessToken", "deviceSerial", "channelNo", "protocol", "type"}
    assert (form["channelNo"], form["protocol"], form["type"]) == ("1", "4", "1")
    assert form["deviceSerial"]
    auth_json = json.loads(responses[0].read())
    token = _deep(auth_json, "accessToken")
    assert token and form["accessToken"] == token
    domain = _deep(auth_json, "areaDomain")
    assert domain and business.url.host == urllib.parse.urlsplit(domain).hostname
    assert _deep(json.loads(responses[1].read()), "url") == url


@pytest.mark.parametrize(
    "origin",
    [
        "https://127.0.0.1:49321",
        "http://localhost:49321",
        "http://127.0.0.1",
        "http://127.0.0.1:49321/media/preview.flv",
        "http://127.0.0.1:49321?x=1",
        "http://127.0.0.1:49321#frag",
        "http://user:pw@127.0.0.1:49321",
        "http://127.0.0.1:99999",
        "127.0.0.1:49321",
        "junk",
    ],
)
def test_demo_address_rejects(flv, origin):
    with pytest.raises(Exception) as err:
        flv.demo_address(origin)
    assert origin not in str(err.value)


def test_load_vendor(flv, tmp_path, monkeypatch):
    archive = tmp_path / "vendor.tgz"
    entries = _entries()
    entries[1] = ("./package/style.css", b"bytes:style.css", tarfile.REGTYPE)
    entries += [
        ("package/ignored.txt", b"x", tarfile.REGTYPE),
        ("unrelated/dir/file.bin", b"x", tarfile.REGTYPE),
    ]
    _pin(monkeypatch, flv, _tgz(archive, entries))
    vendor = flv.load_vendor(archive)
    assert vendor == {f"/vendor/{n}": f"bytes:{n}".encode() for n in VENDOR}
    assert [p.name for p in tmp_path.iterdir()] == ["vendor.tgz"]


def test_load_vendor_rejects(flv, tmp_path, monkeypatch):
    variants = [
        _entries()[:3],
        _entries()[:3] + [("package/decoder.wasm", None, tarfile.SYMTYPE)],
        _entries() + [("package/index.js", b"dupe", tarfile.REGTYPE)],
    ]
    for index, entries in enumerate(variants):
        archive = tmp_path / f"bad{index}.tgz"
        _pin(monkeypatch, flv, _tgz(archive, entries))
        with pytest.raises(Exception):
            flv.load_vendor(archive)


def test_load_vendor_preflight(flv, tmp_path, monkeypatch):
    archive = tmp_path / "v.tgz"
    archive.write_bytes(b"short")
    monkeypatch.setattr(flv, "ARCHIVE_SIZE", 64)
    with pytest.raises(Exception):
        flv.load_vendor(archive)
    monkeypatch.setattr(flv, "ARCHIVE_SIZE", 5)
    monkeypatch.setattr(flv, "ARCHIVE_SHA256", "0" * 64)
    with pytest.raises(Exception):
        flv.load_vendor(archive)
    with pytest.raises(Exception):
        flv.load_vendor(tmp_path / "absent.tgz")


def test_main_failures(flv, tmp_path, capsys):
    archive = tmp_path / "v.tgz"
    archive.write_bytes(b"blob")
    bad = [
        ["--bogus"],
        ["--archive", str(archive), "--port", "notaport"],
        ["--archive", str(tmp_path / "absent.tgz")],
    ]
    for argv in bad:
        assert flv.main(list(argv)) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "flv-preview: startup failed\n"


def test_main_serves(flv, tmp_path, monkeypatch, capsys):
    archive = tmp_path / "vendor.tgz"
    archive.write_bytes(b"blob")
    servers = []

    class FakeServer:
        def __init__(self, address, handler):
            self.address = address
            self.server_address = address
            self.server_port = address[1]
            self.handler = handler
            servers.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.server_close()

        def serve_forever(self, *args, **kwargs):
            pass

        def shutdown(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(flv, "ThreadingHTTPServer", FakeServer, raising=False)
    vendor = {f"/vendor/{n}": f"blob:{n}".encode() for n in VENDOR}
    monkeypatch.setattr(flv, "load_vendor", lambda path: vendor)
    used = {}

    def fake_media(directory):
        used["dir"] = Path(directory)
        return b"FLV\x01synthetic"

    monkeypatch.setattr(flv, "_generate_media", fake_media)
    monkeypatch.setattr(flv, "demo_address", lambda origin: f"{origin}/media/preview.flv")
    assert flv.main(["--archive", str(archive), "--port", "49321"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ORIGIN + "\n"
    assert captured.err == ""
    server = servers[0]
    assert server.address == ("127.0.0.1", 49321)
    assert server.handler is flv._Handler
    assert "127.0.0.1" in str(server.host) and "49321" in str(server.host)
    assert set(server.routes) == ROUTES
    assert all(isinstance(body, bytes) for body in server.routes.values())
    assets = Path(flv.__file__).resolve().parent / "flv_preview_assets"
    assert server.routes["/"] == (assets / "index.html").read_bytes()
    assert server.routes["/player.html"] == (assets / "player.html").read_bytes()
    assert server.routes["/preview.js"] == (assets / "preview.js").read_bytes()
    assert server.routes["/media/preview.flv"] == b"FLV\x01synthetic"
    assert server.routes["/vendor/decoder.wasm"] == b"blob:decoder.wasm"
    assert json.loads(server.routes["/preview.json"]) == {"url": MEDIA_URL}
    assert not used["dir"].exists()


@pytest.mark.parametrize(
    "path,status,role,host_ok",
    [
        ("/", 200, "parent", True),
        ("/player.html", 200, "child", True),
        ("/preview.js", 200, "asset", True),
        ("//player.html", 404, "other", True),
        ("/player.html?x=1", 404, "other", True),
        ("/player.html", 404, "other", False),
    ],
)
def test_frame_policy(flv, path, status, role, host_ok):
    from email.message import Message
    from types import SimpleNamespace

    routes = {
        "/": b"<h1>p</h1>",
        "/player.html": b"<h1>c</h1>",
        "/preview.js": b"js",
    }
    handler = object.__new__(flv._Handler)
    handler.server = SimpleNamespace(host="t.local", routes=routes)
    handler.headers = Message()
    handler.headers["Host"] = "t.local" if host_ok else "evil.test"
    handler.requestline = f"GET {path} HTTP/1.1"
    if path.startswith("//"):
        handler.path = "/" + path.lstrip("/")
    else:
        handler.path = path
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    statuses = []
    headers = {}
    handler.wfile = io.BytesIO()

    def send_response(code, *args):
        statuses.append(code)

    def end_headers():
        return None

    handler.send_response = send_response
    handler.send_header = headers.__setitem__
    handler.end_headers = end_headers

    handler._serve(head=False)

    body = handler.wfile.getvalue()
    assert statuses == [status]
    if status == 200:
        assert body == routes[path]
    else:
        assert body == b""
    if not host_ok or "?" in path or path.startswith("//"):
        assert body == b""

    directives = {}
    for directive in headers["Content-Security-Policy"].split(";"):
        parts = directive.split()
        if parts:
            directives[parts[0]] = parts[1:]

    if role == "child":
        assert directives["frame-ancestors"] == ["'self'"]
        assert headers["Content-Type"].startswith("text/html")
    else:
        assert directives["frame-ancestors"] == ["'none'"]
    if role == "parent":
        assert directives["frame-src"] == ["'self'"]
    else:
        assert directives.get("frame-src") in (None, ["'none'"])
    assert directives["connect-src"] == ["'self'"]
    assert "blob:" in directives["worker-src"]
    assert directives["default-src"] == ["'none'"]
