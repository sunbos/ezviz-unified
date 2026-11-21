"""Offline request-boundary and lifecycle tests. No real settings are used."""

import inspect
import json
import logging
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest
from tools.live._detection_read_guard import DetectionReadGuard

from tools.live.verify_detection_reads import verify
from tools.live.verify_diagnostics import _BoundaryRejection

ORIGIN = "https://open.ys7.com"
AUTH = "/api/lapp/token/get"
LIST = "/api/lapp/device/list"
SCENE = "/api/lapp/device/intelligence/detection/switch/status"
ALARM = "/api/v3/device/alarm/detect/switch/get"
ADVANCED = "/api/v3/das/device/detect/switch/get"
KEY = "SYNTH-KEY-ONLY"
SECRET = "SYNTH-SECRET-ONLY"
SERIAL = "SYNTH-DEVICE-ONLY"
EXTRA_DEVICE = "SYNTH-EXTRA-ONLY"
TOKENS = {"sync": "SYNTH-TOKEN-ONE", "async": "SYNTH-TOKEN-TWO"}
UNKNOWN_INT = 82974103
RAW = "SYNTH-PRIVATE-REPLY"
PHASES = (
    ("sync", AUTH),
    ("sync", LIST),
    ("sync", SCENE),
    ("sync", ALARM),
    ("sync", ADVANCED),
    ("async", AUTH),
    ("async", SCENE),
    ("async", ALARM),
    ("async", ADVANCED),
)
OPS = {
    SCENE: ("scene_switch", "int_enable"),
    ALARM: ("alarm_mode", "int_mode"),
    ADVANCED: ("advanced_type", "int_mode"),
}
OMIT = object()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("external transport is forbidden in offline tests")

    for path in (
        "socket.socket.connect",
        "socket.socket.connect_ex",
        "socket.create_connection",
        "socket.getaddrinfo",
        "httpx.HTTPTransport.handle_request",
        "httpx.AsyncHTTPTransport.handle_async_request",
    ):
        monkeypatch.setattr(path, deny)


def row(mode, path, ok=True):
    operation, shape = OPS[path]
    return {
        "mode": mode,
        "operation": operation,
        "outcome": "passed" if ok else "failed",
        "shape": shape if ok else "unverified",
    }


def receipt(outcome, count, profiles, sync_closed, async_closed):
    return {
        "schema_version": 1,
        "scope": "domestic_detection_reads",
        "outcome": outcome,
        "http_requests": count,
        "sync_closed": sync_closed,
        "async_closed": async_closed,
        "profiles": profiles,
    }


class Rig:
    def __init__(
        self, *, rows=OMIT, fail_at=None, failure=None, close_error=None, same_token=False
    ):
        self.rows = [{"deviceSerial": SERIAL, "extra": RAW}] if rows is OMIT else rows
        self.fail_at = fail_at
        self.failure = failure
        self.close_error = close_error
        self.tokens = dict(TOKENS)
        if same_token:
            self.tokens["async"] = self.tokens["sync"]
        self.phases = []
        self.factories = []
        self.closed = []

    def handler(self, mode, request):
        index = len(self.phases)
        self.phases.append((mode, request.url.path))
        assert index < len(PHASES) and self.phases[-1] == PHASES[index]
        assert str(request.url) == ORIGIN + request.url.path
        path = request.url.path
        if path == AUTH:
            assert request.method == "POST" and sorted(
                parse_qsl(request.content.decode(), keep_blank_values=True)
            ) == sorted([("appKey", KEY), ("appSecret", SECRET)])
            body = {
                "code": "200",
                "data": {"accessToken": self.tokens[mode], "expireTime": 4_102_444_800_000},
            }
        elif path in (LIST, SCENE):
            assert request.method == "POST"
            expected = [("accessToken", self.tokens[mode])]
            expected += (
                [("pageStart", "0"), ("pageSize", "1")]
                if path == LIST
                else [("deviceSerial", SERIAL)]
            )
            assert sorted(parse_qsl(request.content.decode(), keep_blank_values=True)) == sorted(
                expected
            )
            body = {
                "code": "200",
                "msg": RAW,
                "data": self.rows if path == LIST else {"enable": UNKNOWN_INT},
            }
        else:
            assert (
                request.method == "GET"
                and request.content == b""
                and "content-type" not in request.headers
            )
            assert request.headers.get_list("accessToken") == [
                self.tokens[mode]
            ] and request.headers.get_list("deviceSerial") == [SERIAL]
            body = {"code": "200", "data": {"valueInfo": {"type": UNKNOWN_INT, "private": RAW}}}
        if self.fail_at == index + 1:
            if isinstance(self.failure, BaseException):
                raise self.failure
            return httpx.Response(200, json=self.failure)
        return httpx.Response(200, json=body)

    def factory(self, mode):
        rig = self

        def new():
            self.factories.append(mode)

            class Transport(httpx.MockTransport):
                def finish(self):
                    rig.closed.append((mode, id(self)))
                    if rig.close_error == mode and getattr(self, "used_business", False):
                        raise RuntimeError(RAW)

                def close(self):
                    super().close()
                    self.finish()

                async def aclose(self):
                    await super().aclose()
                    self.finish()

            transport = Transport(lambda req: dispatch(req))

            def dispatch(req):
                if req.url.path != AUTH:
                    transport.used_business = True
                return self.handler(mode, req)

            return transport

        return new

    def run(self):
        return verify(
            KEY, SECRET, sync_factory=self.factory("sync"), async_factory=self.factory("async")
        )


@pytest.mark.parametrize("same_token", [False, True])
def test_success(same_token, capsys, caplog):
    rig = Rig(same_token=same_token)
    with caplog.at_level("INFO"):
        actual = rig.run()

    assert actual == receipt(
        "passed",
        9,
        [row(m, p) for m, p in PHASES if p in OPS],
        True,
        True,
    )
    assert rig.phases == list(PHASES)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    haystack = json.dumps(actual) + captured.out + captured.err + caplog.text
    for marker in (
        KEY,
        SECRET,
        SERIAL,
        EXTRA_DEVICE,
        RAW,
        str(UNKNOWN_INT),
        *rig.tokens.values(),
    ):
        assert marker not in haystack

    assert {mode for mode, _ in rig.closed} == {"sync", "async"}


def test_no_target():
    rig = Rig(rows=[])

    assert rig.run() == receipt("no_target", 2, [], True, False)
    assert rig.phases == list(PHASES[:2])
    assert "async" not in rig.factories


@pytest.mark.parametrize(
    "rows",
    [
        [{"deviceSerial": SERIAL}, {"deviceSerial": EXTRA_DEVICE}],
        [{}],
        [{"deviceSerial": None}],
        [{"deviceSerial": ""}],
    ],
)
def test_bad_list(rows):
    rig = Rig(rows=rows)
    assert rig.run() == receipt("failed", 2, [], True, False)
    assert rig.phases == list(PHASES[:2])
    assert "async" not in rig.factories


@pytest.mark.parametrize(
    "fail_at,failure",
    [
        (1, {"code": "10002", "msg": RAW}),
        (2, {"code": "10002", "msg": RAW}),
        (3, {"code": "10002", "msg": RAW}),
        (5, {"code": "200", "data": {"valueInfo": {"type": "BOOL"}}}),
        (6, {"code": "10002", "msg": RAW}),
        (7, {"code": "10002", "msg": RAW}),
        (8, httpx.ReadTimeout(RAW)),
        (9, {"code": "200", "data": {"valueInfo": {"type": "BOOL"}}}),
    ],
)
def test_run_failure(fail_at, failure):
    rig = Rig(fail_at=fail_at, failure=failure)
    mode, path = PHASES[fail_at - 1]
    expected_rows = [row(m, p) for m, p in PHASES[: fail_at - 1] if p in OPS]
    if path in OPS:
        expected_rows.append(row(mode, path, False))
    assert rig.run() == receipt("failed", fail_at, expected_rows, fail_at >= 2, fail_at >= 7)
    assert rig.phases == list(PHASES[:fail_at])
    if fail_at < 6:
        assert "async" not in rig.factories


def req_for(
    index,
    *,
    field=None,
    value=None,
    append_field=None,
    header=None,
    append_header=None,
    drop_header=None,
    url=None,
    body=None,
    method=None,
):
    mode, path = PHASES[index]
    token = TOKENS[mode]
    headers = []
    fields = []
    if path == AUTH:
        fields = [("appKey", KEY), ("appSecret", SECRET)]
    elif path == LIST:
        fields = [("accessToken", token), ("pageStart", "0"), ("pageSize", "1")]
    elif path == SCENE:
        fields = [("accessToken", token), ("deviceSerial", SERIAL)]
    else:
        headers = [("accessToken", token), ("deviceSerial", SERIAL)]
    if fields:
        headers.append(("Content-Type", "application/x-www-form-urlencoded"))
    if field is not None:
        fields = [(k, value if k == field else v) for k, v in fields]
    if append_field:
        fields.append(append_field)
    if header:
        headers = [(k, v) for k, v in headers if k.lower() != header[0].lower()]
        headers.append(header)
    if append_header:
        headers.append(append_header)
    if drop_header:
        headers = [(k, v) for k, v in headers if k.lower() != drop_header.lower()]
    if body is None:
        body = urlencode(fields).encode() if fields else b""
    return httpx.Request(
        method or ("POST" if path in (AUTH, LIST, SCENE) else "GET"),
        url or ORIGIN + path,
        headers=headers,
        content=body,
    )


def guard_after(count):
    guard = DetectionReadGuard(KEY, SECRET)
    for idx in range(count):
        mode, path = PHASES[idx]
        guard.for_mode(mode).admit(req_for(idx))
        if path == AUTH:
            guard.bind_token(mode, TOKENS[mode])
        elif path == LIST:
            guard.bind_device(SERIAL)
    return guard


REJECTED = [
    (0, {"field": "appSecret", "value": "different"}),
    (0, {"append_field": ("appKey", KEY)}),
    (0, {"append_field": ("other", "x")}),
    (0, {"drop_header": "Content-Type"}),
    (1, {"field": "pageSize", "value": "2"}),
    (1, {"field": "pageStart", "value": "1"}),
    (1, {"field": "accessToken", "value": "different"}),
    (1, {"url": ORIGIN + SCENE}),
    (2, {"field": "deviceSerial", "value": EXTRA_DEVICE}),
    (2, {"append_field": ("type", "synthetic")}),
    (2, {"append_field": ("deviceSerial", SERIAL)}),
    (2, {"header": ("Content-Type", "application/json")}),
    (3, {"body": b"x"}),
    (3, {"header": ("Content-Type", "application/json")}),
    (3, {"append_header": ("accessToken", TOKENS["sync"])}),
    (3, {"append_header": ("deviceSerial", SERIAL)}),
    (3, {"append_header": ("X-Unused", "x")}),
    (3, {"header": ("accessToken", TOKENS["async"])}),
    (3, {"header": ("deviceSerial", EXTRA_DEVICE)}),
    (3, {"method": "POST"}),
    (3, {"url": ORIGIN + ALARM + "?extra=1"}),
    (3, {"url": ORIGIN + ALARM.replace("/api/", "/%61pi/", 1)}),
    (4, {"url": "https://example.invalid" + ADVANCED}),
    (4, {"header": ("Host", "example.invalid")}),
    (4, {"url": ORIGIN + ALARM}),
    (5, {"field": "appKey", "value": "different"}),
    (6, {"field": "accessToken", "value": TOKENS["sync"]}),
    (7, {"header": ("deviceSerial", EXTRA_DEVICE)}),
    (8, {"url": ORIGIN + SCENE}),
]


@pytest.mark.parametrize("index,overrides", REJECTED)
def test_guard_refuses_request_and_stays_denied(index, overrides):
    guard = guard_after(index)
    mode, _ = PHASES[index]
    view = guard.for_mode(mode)
    assert guard.spent == index and not guard.denied
    with pytest.raises(_BoundaryRejection):
        view.admit(req_for(index, **overrides))
    assert guard.spent == index and guard.denied
    with pytest.raises(_BoundaryRejection):
        view.admit(req_for(index))
    assert guard.spent == index and guard.denied


def test_maximum_and_stop():
    guard = guard_after(9)
    assert guard.spent == 9 and not guard.denied
    with pytest.raises(_BoundaryRejection):
        guard.for_mode("async").admit(req_for(8))
    assert guard.spent == 9 and guard.denied
    guard = guard_after(2)
    guard.stop()
    with pytest.raises(_BoundaryRejection):
        guard.for_mode("sync").admit(req_for(2))
    assert guard.spent == 2 and guard.denied


@pytest.mark.parametrize(
    "position,wrong_mode", [(0, "async"), (2, "async"), (5, "sync"), (7, "sync")]
)
def test_guard_requires_correct_mode(position, wrong_mode):
    guard = guard_after(position)
    with pytest.raises(_BoundaryRejection):
        guard.for_mode(wrong_mode).admit(req_for(position))
    assert guard.denied and guard.spent == position


@pytest.mark.parametrize("close_mode", ["sync", "async"])
def test_close_failure(close_mode):
    rig = Rig(close_error=close_mode)
    result = rig.run()
    expected_phases = list(PHASES[:5] if close_mode == "sync" else PHASES)
    assert rig.phases == expected_phases
    if close_mode == "sync":
        assert "async" not in rig.factories
    else:
        assert "sync" in rig.factories
        assert "async" in rig.factories
    profiles = [row(mode, path) for mode, path in rig.phases if path in OPS]
    assert len(profiles) == (3 if close_mode == "sync" else 6)
    assert result == receipt(
        "failed",
        len(expected_phases),
        profiles,
        close_mode == "async",
        False,
    )


def test_verify_requires_transports():
    for name in ("sync_factory", "async_factory"):
        p = inspect.signature(verify).parameters[name]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        verify(KEY, SECRET)


def test_failure_privacy(capsys, caplog):
    rig = Rig(
        fail_at=4,
        failure=httpx.ReadTimeout(" ".join((RAW, KEY, SECRET, SERIAL, TOKENS["sync"]))),
    )
    with caplog.at_level(logging.INFO):
        result = rig.run()
    assert result == receipt(
        "failed",
        4,
        [row("sync", SCENE), row("sync", ALARM, False)],
        True,
        False,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    surface = json.dumps(result) + caplog.text
    for sensitive in (KEY, SECRET, SERIAL, TOKENS["sync"], RAW, str(UNKNOWN_INT)):
        assert sensitive not in surface
