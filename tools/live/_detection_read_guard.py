"""Admit the fixed, bounded sequence of domestic read requests before transport."""

from __future__ import annotations

from threading import Lock

import httpx

from tools.live.verify_diagnostics import _BoundaryRejection, _parsed_form_fields, _url_origin_ok
from tools.live.verify_diagnostics_async import _request_shape_ok

_STEPS = (
    ("sync", "auth", "POST", "/api/lapp/token/get"),
    ("sync", "list", "POST", "/api/lapp/device/list"),
    ("sync", "scene", "POST", "/api/lapp/device/intelligence/detection/switch/status"),
    ("sync", "alarm", "GET", "/api/v3/device/alarm/detect/switch/get"),
    ("sync", "advanced", "GET", "/api/v3/das/device/detect/switch/get"),
    ("async", "auth", "POST", "/api/lapp/token/get"),
    ("async", "scene", "POST", "/api/lapp/device/intelligence/detection/switch/status"),
    ("async", "alarm", "GET", "/api/v3/device/alarm/detect/switch/get"),
    ("async", "advanced", "GET", "/api/v3/das/device/detect/switch/get"),
)


def _frame(request, method, path):
    if request.method != method.upper():
        return False
    url = request.url
    if not _url_origin_ok(url, root_only=False):
        return False
    if url.query or url.fragment or "#" in str(url):
        return False
    try:
        want = path.encode("ascii")
    except UnicodeEncodeError:
        return False
    if url.raw_path != want:
        return False
    if set(request.extensions or {}) - {"timeout"}:
        return False
    if request.headers.get("host") not in ("open.ys7.com", "open.ys7.com:443"):
        return False
    names = [k.decode("latin-1").lower() for k, _ in request.headers.raw]
    if len(names) != len(set(names)):
        return False
    allowed = {"host", "accept", "accept-encoding", "connection", "user-agent", "content-length"}
    if method.upper() == "POST":
        allowed.add("content-type")
    else:
        allowed |= {"accesstoken", "deviceserial"}
    if any(n not in allowed for n in names):
        return False
    if any(any(byte < 32 or byte > 126 for byte in value) for _, value in request.headers.raw):
        return False
    connection = request.headers.get("connection")
    if connection is not None and connection.lower() not in ("keep-alive", "close"):
        return False
    if type(request.stream) is not httpx.ByteStream:
        return False
    try:
        sent = b"".join(request.stream)
        body = request.content
    except Exception:
        return False
    if sent != body:
        return False
    if method.upper() == "POST":
        try:
            if not _request_shape_ok(request):
                return False
        except _BoundaryRejection:
            return False
        if request.headers.get("content-type") != "application/x-www-form-urlencoded":
            return False
        return request.headers.get("content-length") == str(len(body))
    if body or "content-type" in request.headers:
        return False
    return request.headers.get("content-length") in (None, "0")


class DetectionReadGuard:
    """Admits only the fixed nine-step read-only detection request sequence."""

    _MODES = ("sync", "async")
    _DENIED = "request rejected by detection read guard"

    def __init__(self, app_key, app_secret):
        if not (
            isinstance(app_key, str)
            and isinstance(app_secret, str)
            and bool(app_key)
            and bool(app_secret)
        ):
            self._reject()
        self._app_key, self._app_secret = app_key, app_secret
        self._lock = Lock()
        self._spend, self._latched, self._device = 0, False, None
        self._tokens = {mode: None for mode in self._MODES}

    @staticmethod
    def _safe(value):
        return (
            isinstance(value, str)
            and bool(value)
            and value.isascii()
            and value.isprintable()
            and value == value.strip()
        )

    spent = property(lambda self: self._spend)
    denied = property(lambda self: self._latched)

    def stop(self):
        with self._lock:
            self._latched = True

    def for_mode(self, mode):
        with self._lock:
            if self._latched or not isinstance(mode, str) or mode not in self._MODES:
                self._reject()
            return _DetectionModeAdapter(self, mode)

    def bind_token(self, mode, token_value):
        with self._lock:
            if (
                self._latched
                or not self._safe(token_value)
                or not isinstance(mode, str)
                or mode not in self._MODES
                or self._spend not in (1, 6)
                or _STEPS[self._spend - 1][0] != mode
                or self._tokens[mode] is not None
            ):
                self._reject()
            self._tokens[mode] = token_value

    def bind_device(self, serial):
        with self._lock:
            if (
                self._latched
                or not self._safe(serial)
                or serial != serial.upper()
                or self._spend != 2
                or self._device is not None
            ):
                self._reject()
            self._device = serial

    def _reject(self):
        self._latched = True
        raise _BoundaryRejection(self._DENIED)

    def _admit(self, request, mode):
        with self._lock:
            if self._latched or self._spend >= len(_STEPS):
                self._reject()
            want, kind, method, path = _STEPS[self._spend]
            try:
                ok = (
                    mode == want
                    and bool(_frame(request, method, path))
                    and self._verify(request, kind, mode)
                )
            except Exception:
                ok = False
            if not ok:
                self._reject()
            self._spend += 1

    def _verify(self, request, kind, mode):
        token = self._tokens.get(mode)
        if kind == "auth":
            return self._form(request) == {
                "appKey": [self._app_key],
                "appSecret": [self._app_secret],
            }
        if not token:
            return False
        if kind == "list":
            return self._form(request) == {
                "accessToken": [token],
                "pageStart": ["0"],
                "pageSize": ["1"],
            }
        if not self._device:
            return False
        if kind == "scene":
            return self._form(request) == {
                "accessToken": [token],
                "deviceSerial": [self._device],
            }
        return (
            request.headers.get("deviceSerial") == self._device
            and request.headers.get("accessToken") == token
        )

    @staticmethod
    def _form(request):
        try:
            return _parsed_form_fields(request)
        except Exception:
            return {}


class _DetectionModeAdapter:
    __slots__ = ("_guard", "_mode")

    def __init__(self, guard, mode):
        self._guard, self._mode = guard, mode

    def admit(self, request):
        self._guard._admit(request, self._mode)
