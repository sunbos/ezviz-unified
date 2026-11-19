"""Contract tests for the domestic device OSD facade.

Covered operations (domestic China region only):

* 1721 ``osd.get_name``   -- GET  /api/v3/device/osd            (header token)
* 725  ``osd.set_name``   -- POST /api/lapp/device/update/osd/name (form token)
* 5201 ``osd.set_lines``  -- POST /api/v3/device/osd/multi/set  (header token)

Envelope policy is per operation: ``get_name``/``set_name`` use the flat
``{"code": "200", "msg": ...}`` envelope (top-level string code) while
``set_lines`` uses ``{"meta": {"code": 200, ...}}`` (integer code).  Only
``get_name`` returns a ``data`` member, which must contain a string ``osd``.

All traffic is mocked at the httpx transport layer; no live endpoint is
contacted and only synthetic fixtures are used.  Every scenario runs against
both the sync and the async client through the ``mode`` fixture and the
shared :func:`_drive` helper.  Tests never import the examples or harness
packages and never read repository docs.
"""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

TOKEN = "SYNTH_TOKEN"
HOST = "open.ys7.com"

SERIAL_UNICODE = "cam摄像机·01ab"
SERIAL_UNICODE_UPPER = "CAM摄像机·01AB"
SERIAL_MIXED = "cAm设备01X"

OSD_TEXT = "大厅 · 前台 ✓"
# 'ß' uppercases to 'SS' under str.upper(): a canary that OSD text must
# never be normalised, even though a serial's ASCII letters already are.
OSD_NAME_SHARP_S = "Große Halle ß"
# >50 UTF-8 bytes, keeps leading/trailing whitespace, punctuation, unicode.
OSD_LONG = "  前台接待 · 摄像头/A-01 — 入口(东侧), 2F：温度 26°C、湿度 45% ✓  "
OSD_LINES = "大厅, 二层 ,B1·车库"
SECRET_NAME = " 内部代号 ※ 雪绒花-99 ✓ "
SECRET_LINES = "机密A, 绝密B ※,Ω"

BAD_TYPE_VALUES = [None, 0, 7, True, b"x", 1.5]
BAD_TEXT_VALUES = ["", "   ", "\ud800"]

GET_NAME_PATH = "/api/v3/device/osd"
SET_NAME_PATH = "/api/lapp/device/update/osd/name"
SET_LINES_PATH = "/api/v3/device/osd/multi/set"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_auth():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": TOKEN,
                    "expireTime": 9999999999999,
                    "areaDomain": "https://open.ys7.com",
                },
            },
        )

    handler.calls = calls
    return handler


def _make_biz(payload):
    calls = []

    def handler(request):
        calls.append(request)
        body = payload(request) if callable(payload) else payload
        return httpx.Response(200, json=body)

    handler.calls = calls
    return handler


def _client(cls, auth, biz, rec, region=None):
    return cls(
        "mock-key",
        "mock-secret",
        region=region or RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(biz),
        diagnostic_recorder=rec,
    )


def _query(request):
    return parse_qs(urlsplit(str(request.url)).query, keep_blank_values=True)


def _form(request):
    return parse_qs(request.content.decode(), keep_blank_values=True)


def _report_text(rec):
    return json.dumps(rec.report(), ensure_ascii=False)


def _attempt_operations(rec):
    # Each real request attempt emits two lifecycle records (a "started"
    # event and one terminal event); count only the start of each attempt.
    return [
        e["operation"]
        for e in rec.report()["events"]
        if e.get("kind") == "attempt" and e.get("outcome") == "started"
    ]


def _retry_events(rec):
    return [e for e in rec.report()["events"] if e.get("kind") == "retry"]


def _assert_call_events(rec, entrypoint, mode, outcomes):
    """Compare exact call outcomes for one entrypoint/mode only.

    A closed client may emit additional events, so the flat event list is
    filtered by kind/entrypoint/mode before outcomes are compared.
    """
    calls = [
        e
        for e in rec.report()["events"]
        if e.get("kind") == "call" and e.get("entrypoint") == entrypoint and e.get("mode") == mode
    ]
    assert [e["outcome"] for e in calls] == outcomes


def _assert_private(rec, resp, *values):
    """User material stays out of ``repr(resp)`` and the diagnostic report."""
    report = _report_text(rec)
    for value in values:
        assert value not in repr(resp)
        assert value not in report


@pytest.fixture(params=["sync", "async"])
def mode(request):
    """Run each scenario through both the sync and the async client."""
    return request.param


class _Blocking:
    """Synchronous view of an ``AsyncClient`` ``osd`` facade.

    Each attribute access returns a plain callable which drives the
    underlying coroutine to completion on the session event loop, so a
    scenario body can be written once for both client modes.
    """

    def __init__(self, osd, run):
        self._osd = osd
        self._run = run

    def __getattr__(self, name):
        method = getattr(self._osd, name)

        def invoke(*args, **kwargs):
            return self._run(method(*args, **kwargs))

        return invoke


def _drive(mode, auth, biz, rec, body, region=None):
    """Run ``body(osd)`` against an opened client in the requested ``mode``.

    ``body`` receives ``client.osd`` itself for the sync client, or a
    :class:`_Blocking` wrapper for the async client; either way an
    ``osd.<method>(...)`` expression yields the finished result.  Whatever
    ``body`` returns is passed back to the caller.
    """
    if mode == "sync":
        with _client(Client, auth, biz, rec, region=region) as client:
            return body(client.osd)

    loop = asyncio.new_event_loop()
    try:

        async def open_client():
            client = _client(AsyncClient, auth, biz, rec, region=region)
            await client.__aenter__()
            return client

        client = loop.run_until_complete(open_client())
        try:
            return body(_Blocking(client.osd, loop.run_until_complete))
        finally:
            loop.run_until_complete(client.__aexit__(None, None, None))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# 1721 get_name
# ---------------------------------------------------------------------------


def test_get_name_wire_flat_envelope_and_raw_passthrough(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz(
        {
            "code": "200",
            "msg": "OK",
            "data": {
                "deviceSerial": SERIAL_UNICODE,
                "osd": OSD_TEXT,
                "vendorField": {"n": 1},
            },
            "unmanaged": "keep",
        }
    )
    resp = _drive(mode, auth, biz, rec, lambda osd: osd.get_name(SERIAL_UNICODE))

    # 1 auth + 1 business request, counted on the mock transports.
    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    req = biz.calls[0]
    assert req.method == "GET"
    assert req.url.scheme == "https"
    assert req.url.host == HOST
    assert req.url.path == GET_NAME_PATH
    # deviceSerial verbatim (mixed unicode), token only in the header.
    assert _query(req) == {"deviceSerial": [SERIAL_UNICODE]}
    assert req.headers.get("accessToken") == TOKEN
    assert req.content == b""

    assert isinstance(resp, ApiResponse)
    assert resp.operation == "osd.get_name"
    assert resp.code == "200"
    assert resp.message == "OK"
    assert resp.data["osd"] == OSD_TEXT
    assert resp.data["vendorField"] == {"n": 1}
    assert resp.raw["data"]["deviceSerial"] == SERIAL_UNICODE
    assert resp.raw["unmanaged"] == "keep"

    _assert_call_events(rec, "osd.get_name", mode, ["started", "succeeded"])
    assert "osd.get_name" in _attempt_operations(rec)

    _assert_private(rec, resp, SERIAL_UNICODE, OSD_TEXT)


@pytest.mark.parametrize(
    "serial,expected",
    [("dev001", ""), ("dev002", OSD_TEXT)],
    ids=["empty-osd-string", "normal-osd-string"],
)
def test_get_name_osd_value_variants(mode, serial, expected):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz(
        {
            "code": "200",
            "msg": "OK",
            "data": {"deviceSerial": serial, "osd": expected},
        }
    )
    resp = _drive(mode, auth, biz, rec, lambda osd: osd.get_name(serial))

    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    req = biz.calls[0]
    assert req.method == "GET"
    assert req.url.path == GET_NAME_PATH
    assert _query(req) == {"deviceSerial": [serial]}
    assert req.headers.get("accessToken") == TOKEN
    assert req.content == b""

    assert isinstance(resp, ApiResponse)
    # An explicit empty string is a real value, distinct from a missing key.
    assert resp.data["osd"] == expected

    _assert_call_events(rec, "osd.get_name", mode, ["started", "succeeded"])
    assert "osd.get_name" in _attempt_operations(rec)


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "200", "msg": "OK"},
        {"code": "200", "msg": "OK", "data": {}},
        {"code": "200", "msg": "OK", "data": "not-a-dict"},
        {"code": "200", "msg": "OK", "data": {"osd": None}},
        {"code": "200", "msg": "OK", "data": {"osd": 42}},
        {"code": "200", "msg": "OK", "data": {"osd": ["a"]}},
        # Meta-style success is not accepted for this operation.
        {"meta": {"code": 200, "message": "OK"}, "data": {"osd": "x"}},
    ],
    ids=[
        "no-data",
        "no-osd",
        "data-not-dict",
        "osd-none",
        "osd-int",
        "osd-list",
        "meta-only-envelope",
    ],
)
def test_get_name_malformed_envelope_raises_protocol_error(mode, payload):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz(payload)
    with pytest.raises(ProtocolError):
        _drive(mode, auth, biz, rec, lambda osd: osd.get_name("dev0c1"))

    # A protocol failure is not retried.
    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    _assert_call_events(rec, "osd.get_name", mode, ["started", "failed"])
    assert _attempt_operations(rec).count("osd.get_name") == 1


def test_get_name_integer_top_level_code_is_api_error(mode):
    # Success is the string "200"; an integer top-level code is a parsed
    # non-success business code, not a malformed envelope.
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": 200, "msg": "OK", "data": {"osd": "x"}})

    def body(osd):
        with pytest.raises(ApiError) as excinfo:
            osd.get_name("dev0c2")
        return excinfo.value

    exc = _drive(mode, auth, biz, rec, body)

    # The numeric code is preserved verbatim on the error.
    assert exc.code == 200
    assert exc.operation == "osd.get_name"
    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    _assert_call_events(rec, "osd.get_name", mode, ["started", "failed"])
    assert _attempt_operations(rec).count("osd.get_name") == 1


@pytest.mark.parametrize("bad", BAD_TYPE_VALUES + BAD_TEXT_VALUES)
def test_get_name_rejects_invalid_serial(mode, bad):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK", "data": {"osd": "x"}})
    with pytest.raises(ValueError):
        _drive(mode, auth, biz, rec, lambda osd: osd.get_name(bad))
    assert auth.calls == []
    assert biz.calls == []


def test_get_name_signature_has_no_channel_and_requires_serial(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK", "data": {"osd": "x"}})

    def body(osd):
        with pytest.raises(TypeError):
            osd.get_name()
        with pytest.raises(TypeError):
            osd.get_name("dev", "1")
        # Device GET takes no channel parameter at all.
        with pytest.raises(TypeError):
            osd.get_name("dev", channel_no="1")

    _drive(mode, auth, biz, rec, body)
    assert auth.calls == []
    assert biz.calls == []


# ---------------------------------------------------------------------------
# 725 set_name
# ---------------------------------------------------------------------------


def test_set_name_wire_form_token_upper_serial_and_verbatim_name(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK", "unmanaged": "x"})
    resp = _drive(
        mode,
        auth,
        biz,
        rec,
        lambda osd: osd.set_name(SERIAL_UNICODE_UPPER, OSD_LONG),
    )

    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    req = biz.calls[0]
    assert req.method == "POST"
    assert req.url.scheme == "https"
    assert req.url.host == HOST
    assert req.url.path == SET_NAME_PATH
    assert _query(req) == {}
    # Token travels in the form body, not the header.
    assert "accessToken" not in req.headers
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    body = _form(req)
    assert body["accessToken"] == [TOKEN]
    assert body["deviceSerial"] == [SERIAL_UNICODE_UPPER]
    assert body["osdName"] == [OSD_LONG]
    assert len(OSD_LONG.encode("utf-8")) > 50
    # channel_no=None -> channelNo omitted entirely.
    assert "channelNo" not in body

    # Official acknowledgement without a data member.
    assert isinstance(resp, ApiResponse)
    assert resp.operation == "osd.set_name"
    assert resp.code == "200"
    assert resp.message == "OK"
    assert "data" not in resp.raw
    assert resp.data is None
    assert resp.raw["unmanaged"] == "x"

    _assert_call_events(rec, "osd.set_name", mode, ["started", "succeeded"])
    assert "osd.set_name" in _attempt_operations(rec)

    _assert_private(rec, resp, OSD_LONG, SERIAL_UNICODE, SERIAL_UNICODE_UPPER)


def test_set_name_verbatim_non_ascii_name_never_normalised(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK"})
    resp = _drive(
        mode,
        auth,
        biz,
        rec,
        lambda osd: osd.set_name("DEV0A0", OSD_NAME_SHARP_S),
    )

    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    body = _form(biz.calls[0])
    # 'ß' must reach the wire verbatim; an upper() pass would send 'SS'.
    assert body["deviceSerial"] == ["DEV0A0"]
    assert body["osdName"] == [OSD_NAME_SHARP_S]

    assert isinstance(resp, ApiResponse)
    assert resp.operation == "osd.set_name"
    assert resp.data is None
    _assert_call_events(rec, "osd.set_name", mode, ["started", "succeeded"])
    assert _attempt_operations(rec).count("osd.set_name") == 1


def test_set_name_lowercase_serial_rejected_before_any_io(mode):
    # Domestic docs require serial letters already uppercase; the same
    # value that succeeds as get_name input is rejected by 725 verbatim.
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK"})
    with pytest.raises(ValueError):
        _drive(
            mode,
            auth,
            biz,
            rec,
            lambda osd: osd.set_name(SERIAL_UNICODE, "valid"),
        )
    assert auth.calls == []
    assert biz.calls == []


def test_set_name_explicit_channel_string_is_unchanged(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK"})
    resp = _drive(
        mode,
        auth,
        biz,
        rec,
        lambda osd: osd.set_name("DEV0A1", " 门厅 · 01 ", channel_no="01"),
    )

    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    req = biz.calls[0]
    assert req.method == "POST"
    assert req.url.path == SET_NAME_PATH
    body = _form(req)
    assert body["accessToken"] == [TOKEN]
    assert body["deviceSerial"] == ["DEV0A1"]
    assert body["osdName"] == [" 门厅 · 01 "]
    # Channel is sent verbatim, never normalised to an int.
    assert body["channelNo"] == ["01"]

    assert isinstance(resp, ApiResponse)
    assert resp.data is None
    assert "data" not in resp.raw

    _assert_call_events(rec, "osd.set_name", mode, ["started", "succeeded"])
    assert "osd.set_name" in _attempt_operations(rec)


def test_set_name_credential_expiry_is_authentication_error_no_retry(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "10002", "msg": "invalid accessToken"})

    def body(osd):
        with pytest.raises(AuthenticationError) as excinfo:
            osd.set_name("DEV0A2", "名称")
        return excinfo.value

    exc = _drive(mode, auth, biz, rec, body)

    assert exc.code == "10002"
    assert exc.operation == "osd.set_name"
    # Exactly one auth + one business request; no retry attempt is recorded.
    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    _assert_call_events(rec, "osd.set_name", mode, ["started", "failed"])
    assert _attempt_operations(rec).count("osd.set_name") == 1
    assert _retry_events(rec) == []


def test_set_name_server_echo_does_not_leak_osd_name(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    # The secret is echoed as a business code value and inside a message.
    biz = _make_biz(
        {
            "code": "10017",
            "msg": 'illegal osdName "' + SECRET_NAME + '" rejected',
            "data": {"echo": SECRET_NAME},
        }
    )

    def body(osd):
        with pytest.raises(ApiError) as excinfo:
            osd.set_name("DEV0A3", SECRET_NAME)
        return excinfo.value

    exc = _drive(mode, auth, biz, rec, body)

    assert SECRET_NAME not in str(exc)
    assert SECRET_NAME not in repr(exc)
    assert SECRET_NAME not in _report_text(rec)
    assert exc.code == "10017"
    assert exc.operation == "osd.set_name"
    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    _assert_call_events(rec, "osd.set_name", mode, ["started", "failed"])


def test_set_name_integer_code_in_envelope_is_api_error(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    # Success requires the string "200"; an integer code is not success.
    biz = _make_biz({"code": 200, "msg": "OK"})

    def body(osd):
        with pytest.raises(ApiError) as excinfo:
            osd.set_name("DEV0A4", "名称")
        return excinfo.value

    exc = _drive(mode, auth, biz, rec, body)

    # The numeric business code is preserved verbatim on the error.
    assert exc.code == 200
    assert exc.operation == "osd.set_name"
    assert len(biz.calls) == 1
    _assert_call_events(rec, "osd.set_name", mode, ["started", "failed"])


@pytest.mark.parametrize(
    "kwargs",
    [
        # Wrong types...
        dict(device_serial=None, osd_name="n"),
        dict(device_serial=7, osd_name="n"),
        dict(device_serial=True, osd_name="n"),
        dict(device_serial="DEV", osd_name=None),
        dict(device_serial="DEV", osd_name=2),
        dict(device_serial="DEV", osd_name=False),
        dict(device_serial="DEV", osd_name="n", channel_no=1),
        dict(device_serial="DEV", osd_name="n", channel_no=False),
        # ...and blank / surrogate strings all fail before any I/O.
        dict(device_serial="", osd_name="n"),
        dict(device_serial="   ", osd_name="n"),
        dict(device_serial="\ud800", osd_name="n"),
        dict(device_serial="DEV", osd_name=""),
        dict(device_serial="DEV", osd_name="   "),
        dict(device_serial="DEV", osd_name="\ud800"),
        dict(device_serial="DEV", osd_name="n", channel_no=""),
        dict(device_serial="DEV", osd_name="n", channel_no="   "),
        dict(device_serial="DEV", osd_name="n", channel_no="\ud800"),
    ],
)
def test_set_name_rejects_invalid_arguments(mode, kwargs):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK"})
    with pytest.raises(ValueError):
        _drive(mode, auth, biz, rec, lambda osd: osd.set_name(**kwargs))
    assert auth.calls == []
    assert biz.calls == []


def test_set_name_requires_osd_name_argument(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK"})

    def body(osd):
        with pytest.raises(TypeError):
            osd.set_name("DEV")

    _drive(mode, auth, biz, rec, body)
    assert auth.calls == []
    assert biz.calls == []


# ---------------------------------------------------------------------------
# 5201 set_lines
# ---------------------------------------------------------------------------


def test_set_lines_wire_meta_envelope_and_raw_comma_names(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"meta": {"code": 200, "message": "OK"}, "unmanaged": "k"})
    resp = _drive(
        mode,
        auth,
        biz,
        rec,
        lambda osd: osd.set_lines(SERIAL_MIXED, osd_names=OSD_LINES),
    )

    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    req = biz.calls[0]
    assert req.method == "POST"
    assert req.url.scheme == "https"
    assert req.url.host == HOST
    assert req.url.path == SET_LINES_PATH
    assert _query(req) == {}
    # Token travels in the header; the form body only carries fields.
    assert req.headers.get("accessToken") == TOKEN
    body = _form(req)
    assert "accessToken" not in body
    # Mixed-case serial is sent unchanged; the raw comma string is preserved.
    assert body["deviceSerial"] == [SERIAL_MIXED]
    assert body["osdNames"] == [OSD_LINES]
    assert "channelNo" not in body

    assert isinstance(resp, ApiResponse)
    assert resp.operation == "osd.set_lines"
    assert resp.code == 200
    assert resp.message == "OK"
    assert "data" not in resp.raw
    assert resp.data is None
    assert resp.raw["unmanaged"] == "k"

    _assert_call_events(rec, "osd.set_lines", mode, ["started", "succeeded"])
    assert "osd.set_lines" in _attempt_operations(rec)

    _assert_private(rec, resp, OSD_LINES, SERIAL_MIXED)


def test_set_lines_none_omits_empty_sends_blank_and_channel_kept(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"meta": {"code": 200, "message": "OK"}})

    def body(osd):
        first = osd.set_lines("mIxEd01", osd_names=None, channel_no="01")
        second = osd.set_lines("mIxEd02", osd_names="")
        return first, second

    first, second = _drive(mode, auth, biz, rec, body)

    assert len(biz.calls) == 2

    req1 = biz.calls[0]
    assert req1.method == "POST"
    assert req1.url.path == SET_LINES_PATH
    assert req1.headers.get("accessToken") == TOKEN
    body1 = _form(req1)
    assert body1["deviceSerial"] == ["mIxEd01"]
    # osd_names=None omits the field entirely.
    assert "osdNames" not in body1
    assert body1["channelNo"] == ["01"]
    assert "accessToken" not in body1

    req2 = biz.calls[1]
    assert req2.headers.get("accessToken") == TOKEN
    body2 = _form(req2)
    assert body2["deviceSerial"] == ["mIxEd02"]
    # Explicit empty string is sent as a real (blank) form value.
    assert body2["osdNames"] == [""]
    assert "channelNo" not in body2

    assert first.data is None and second.data is None
    assert "data" not in first.raw and "data" not in second.raw

    _assert_call_events(
        rec,
        "osd.set_lines",
        mode,
        ["started", "succeeded", "started", "succeeded"],
    )
    assert _attempt_operations(rec).count("osd.set_lines") == 2


def test_set_lines_credential_expiry_is_authentication_error_no_retry(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"meta": {"code": 10002, "message": "invalid accessToken"}})

    def body(osd):
        with pytest.raises(AuthenticationError) as excinfo:
            osd.set_lines("dev0b1", osd_names="名称")
        return excinfo.value

    exc = _drive(mode, auth, biz, rec, body)

    assert exc.code == 10002
    assert exc.operation == "osd.set_lines"
    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    _assert_call_events(rec, "osd.set_lines", mode, ["started", "failed"])
    assert _attempt_operations(rec).count("osd.set_lines") == 1
    assert _retry_events(rec) == []


def test_set_lines_server_echo_does_not_leak_osd_names(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz(
        {
            "meta": {
                "code": 10017,
                "message": 'bad osdNames "' + SECRET_LINES + '" rejected',
            },
            "data": {"echo": SECRET_LINES},
        }
    )

    def body(osd):
        with pytest.raises(ApiError) as excinfo:
            osd.set_lines("dev0b2", osd_names=SECRET_LINES)
        return excinfo.value

    exc = _drive(mode, auth, biz, rec, body)

    assert SECRET_LINES not in str(exc)
    assert SECRET_LINES not in repr(exc)
    assert SECRET_LINES not in _report_text(rec)
    assert exc.code == 10017
    assert exc.operation == "osd.set_lines"
    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    _assert_call_events(rec, "osd.set_lines", mode, ["started", "failed"])


def test_set_lines_string_code_in_meta_is_api_error(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    # meta.code must be the integer 200; a string "200" is not success.
    biz = _make_biz({"meta": {"code": "200", "message": "OK"}})

    def body(osd):
        with pytest.raises(ApiError) as excinfo:
            osd.set_lines("dev0b3", osd_names="名称")
        return excinfo.value

    exc = _drive(mode, auth, biz, rec, body)

    assert exc.operation == "osd.set_lines"
    assert len(biz.calls) == 1
    _assert_call_events(rec, "osd.set_lines", mode, ["started", "failed"])


def _echo_code_payload(operation, code, message):
    # Wrap the caller-supplied values in the business-code and message
    # positions of each operation's own envelope shape.
    if operation == "set_name":
        return {"code": code, "msg": message}
    return {"meta": {"code": code, "message": message}}


@pytest.mark.parametrize("labelled", [False, True], ids=["unlabelled-message", "labelled-message"])
@pytest.mark.parametrize("operation", ["set_name", "set_lines"])
def test_write_server_echo_of_supplied_text_never_leaks(mode, operation, labelled):
    # The server echoes the caller's own multiword unicode text back: raw
    # as the business code itself, and inside the message with or without
    # a field label.  The supplied value is a registered request secret,
    # so it must be scrubbed from the error's code, str/repr and the flat
    # diagnostic report while the adjacent benign text survives.
    secret = " 机密 代号 ※ 雪绒花-99 ✓ "
    benign = "rejected"
    field = "osdName" if operation == "set_name" else "osdNames"
    message = f'{field} "{secret}" {benign}' if labelled else f"{secret} {benign}"
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz(_echo_code_payload(operation, secret, message))

    def body(osd):
        with pytest.raises(ApiError) as excinfo:
            if operation == "set_name":
                osd.set_name("DEV0A5", secret)
            else:
                osd.set_lines("dev0b4", osd_names=secret)
        return excinfo.value

    exc = _drive(mode, auth, biz, rec, body)

    assert secret not in str(exc)
    assert secret not in repr(exc)
    if exc.code is not None:
        assert secret not in str(exc.code)
    assert secret not in _report_text(rec)
    # The adjacent benign text is not redacted away with the secret.
    assert benign in str(exc)
    assert exc.operation == f"osd.{operation}"
    assert len(auth.calls) == 1
    assert len(biz.calls) == 1
    _assert_call_events(rec, f"osd.{operation}", mode, ["started", "failed"])
    assert _attempt_operations(rec).count(f"osd.{operation}") == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        # Wrong types...
        dict(device_serial=None, osd_names="n"),
        dict(device_serial=3, osd_names="n"),
        dict(device_serial="dev", osd_names=1),
        dict(device_serial="dev", osd_names=False),
        dict(device_serial="dev", osd_names=b"n"),
        dict(device_serial="dev", osd_names="n", channel_no=0),
        dict(device_serial="dev", osd_names="n", channel_no=True),
        # ...and blank / surrogate strings all fail before any I/O.
        dict(device_serial="", osd_names="n"),
        dict(device_serial="  ", osd_names="n"),
        dict(device_serial="\ud800", osd_names="n"),
        dict(device_serial="dev", osd_names="\ud800"),
        dict(device_serial="dev", osd_names="n", channel_no=""),
        dict(device_serial="dev", osd_names="n", channel_no="\ud800"),
    ],
)
def test_set_lines_rejects_invalid_arguments(mode, kwargs):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"meta": {"code": 200, "message": "OK"}})
    with pytest.raises(ValueError):
        _drive(mode, auth, biz, rec, lambda osd: osd.set_lines(**kwargs))
    assert auth.calls == []
    assert biz.calls == []


def test_set_lines_requires_osd_names_keyword_argument(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"meta": {"code": 200, "message": "OK"}})

    def body(osd):
        with pytest.raises(TypeError):
            osd.set_lines("dev")
        with pytest.raises(TypeError):
            osd.set_lines("dev", "名称")

    _drive(mode, auth, biz, rec, body)
    assert auth.calls == []
    assert biz.calls == []


# ---------------------------------------------------------------------------
# region gate
# ---------------------------------------------------------------------------


def test_overseas_region_rejects_all_osd_methods_before_any_io(mode):
    rec = DiagnosticRecorder()
    auth = _make_auth()
    biz = _make_biz({"code": "200", "msg": "OK"})

    def body(osd):
        with pytest.raises(ValueError):
            osd.get_name("dev")
        with pytest.raises(ValueError):
            osd.set_name("dev", "名称")
        with pytest.raises(ValueError):
            osd.set_lines("dev", osd_names="名称")

    _drive(mode, auth, biz, rec, body, region=RegionConfig.overseas())
    assert auth.calls == []
    assert biz.calls == []
