"""Offline wire and validation tests for the domestic cloud capture facades.

``CloudCapture`` and ``AsyncCloudCapture`` are exercised directly against real
``Client``/``AsyncClient`` instances whose transports are
``httpx.MockTransport`` objects with fully synthetic authentication. Nothing
reaches the network and no pytest-asyncio plugin is required.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    RegionConfig,
)
from ezviz_unified.cloud_capture import AsyncCloudCapture, CloudCapture

APP_KEY = "TEST_APP_KEY"
APP_SECRET = "TEST_APP_SECRET"
TOKEN = "TEST_TOKEN"
SERIAL = "TESTSERIAL123"
TASK_ID = "TASK_ABC_123"

INTERVAL_PATH = "/api/v3/open/cloud/video/frame/interval/start"
TIMING_PATH = "/api/v3/open/cloud/video/frame/timing/start"
TASK_PATH = "/api/v3/open/cloud/task/"

AUTH_BODY = {"code": "200", "data": {"accessToken": TOKEN, "expireTime": 2000000}}
SUCCESS_BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {"taskId": TASK_ID, "customField": "preserved"},
    "extraField": "kept",
}

BAD_SERIALS = (None, 123, b"SERIAL", "", "   ", "SER\ud800")
BAD_CHANNELS = (None, True, False, "1", 1.5)
BAD_REC_TYPES = (None, "", "LOCAL", "Cloud", "recording", 1, True)
BAD_INTERVALS = (None, 0, -1, True, "10", 1.5)
BAD_FRAME_MODELS = (True, -1, 3, "1", 0.5, 1.0)
BAD_INTERVAL_PROTOS = ("onvif", "GB28181", "rtsp", 0, True)
BAD_STREAM_TYPES = ("", "3", "main", 1, True)
BAD_INTERVAL_SPACE_IDS = (0, -1, True, "2", 1.5)
BAD_INTERVAL_PROJECT_IDS = ("", "   ", "P" * 33, "has-dash", "中文项目", "P\ud800", 123)
BAD_VALIDATE_CODES = ("", "   ", "C\ud800", 7)
BAD_TASK_IDS = (None, "", "   ", "T" * 33, "has-dash", "中文", "T\ud800", 123)
BAD_TIMING_PROJECT_IDS = ("", "   ", "P\ud800", 123)
BAD_TIMING_SPACE_IDS = (True, False, 1.0, "3")
BAD_POINT_SETS = (
    "20240203040506",
    b"20240203040506",
    (item for item in ()),
    [],
    (),
    [None],
    ["20240203040506"],
    [date(2024, 2, 3)],
    [datetime(2024, 2, 3, 4, 5, 6, 500)],
    [datetime(2024, 2, 3, 4, 5, 6, tzinfo=timezone.utc)],
)


def _region(label: str) -> RegionConfig:
    """Return the real RegionConfig for ``label`` (``domestic``/``overseas``)."""
    if label == "domestic":
        return RegionConfig.china()
    return RegionConfig.overseas()


class _Harness:
    """Synthetic auth and business transports wrapped around a real client."""

    def __init__(self, *, region="domestic", responder=None):
        self.region = region
        self.responder = responder or (lambda request: httpx.Response(200, json=dict(SUCCESS_BODY)))
        self.auth_calls = []
        self.business_calls = []
        self.factory_calls = []

    def _handle_auth(self, request):
        self.auth_calls.append(request)
        return httpx.Response(200, json=dict(AUTH_BODY))

    def _handle_business(self, request):
        self.business_calls.append(request)
        return self.responder(request)

    def _factory(self, origin):
        self.factory_calls.append(origin)
        return httpx.MockTransport(self._handle_business)

    def _kwargs(self):
        return {
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "region": _region(self.region),
            "clock": lambda: 1000.0,
            "auth_transport": httpx.MockTransport(self._handle_auth),
            "transport_factory": self._factory,
        }

    def sync_client(self):
        return Client(**self._kwargs())

    def async_client(self):
        return AsyncClient(**self._kwargs())


def _drive_sync(harness, method, *args, **kwargs):
    client = harness.sync_client()
    facade = CloudCapture(client)
    try:
        return getattr(facade, method)(*args, **kwargs)
    finally:
        client.close()


def _drive_async(harness, method, *args, **kwargs):
    client = harness.async_client()
    facade = AsyncCloudCapture(client)

    async def run():
        try:
            return await getattr(facade, method)(*args, **kwargs)
        finally:
            await client.aclose()

    return asyncio.run(run())


def _interval_kwargs(**overrides):
    kwargs = {
        "channel": 1,
        "rec_type": "cloud",
        "start_time": datetime(2024, 1, 2, 3, 4, 5),
        "end_time": datetime(2024, 1, 2, 4, 4, 5),
        "frame_interval": 10,
        "project_id": "PROJ_A",
    }
    kwargs.update(overrides)
    return kwargs


def _timing_kwargs(**overrides):
    kwargs = {
        "channel": 1,
        "rec_type": "local",
        "timing_points": (
            datetime(2024, 2, 3, 4, 5, 6),
            datetime(2024, 2, 3, 6, 5, 6),
        ),
        "project_id": "PROJ_A",
    }
    kwargs.update(overrides)
    return kwargs


def _form(request):
    return urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _query(request):
    return urllib.parse.parse_qs(request.url.query.decode("utf-8"), keep_blank_values=True)


def _post_form(request, path, expected, absent=()):
    assert request.method == "POST"
    assert request.url.path == path
    assert not request.url.query
    assert "accessToken" not in request.headers
    assert "authorization" not in request.headers
    content_type = request.headers.get("content-type", "")
    assert content_type == "application/x-www-form-urlencoded"
    form = _form(request)
    assert "channel" not in form
    wanted = {key: [value] for key, value in expected.items()}
    wanted["accessToken"] = [TOKEN]
    assert form == wanted
    for key in absent:
        assert key not in form, key
    return form


def _assert_response(response):
    assert response.http_status == 200
    assert response.code == 200
    assert response.message == "ok"
    assert response.data == SUCCESS_BODY["data"]
    # Unknown fields inside ``data`` and on the raw envelope are preserved.
    assert response.data["customField"] == "preserved"
    assert response.raw == SUCCESS_BODY
    assert response.raw["extraField"] == "kept"


def _assert_no_io(harness):
    assert harness.factory_calls == []
    assert harness.auth_calls == []
    assert harness.business_calls == []


def _meta_body(code, message="failed"):
    return {"meta": {"code": code, "message": message}, "data": {}}


def test_interval_task_wire_sync():
    harness = _Harness()
    response = _drive_sync(harness, "create_interval_task", SERIAL, **_interval_kwargs())
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    _post_form(
        harness.business_calls[0],
        INTERVAL_PATH,
        {
            "deviceSerial": SERIAL,
            "channelNo": "1",
            "recType": "cloud",
            "startTime": "20240102030405",
            "endTime": "20240102040405",
            "frameInterval": "10",
            "projectId": "PROJ_A",
        },
        absent=(
            "resultSpaceId",
            "frameModel",
            "devProto",
            "streamType",
            "validateCode",
        ),
    )
    _assert_response(response)


def test_interval_task_wire_async():
    harness = _Harness()
    response = _drive_async(
        harness,
        "create_interval_task",
        SERIAL,
        **_interval_kwargs(project_id=None, result_space_id=7),
    )
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    _post_form(
        harness.business_calls[0],
        INTERVAL_PATH,
        {
            "deviceSerial": SERIAL,
            "channelNo": "1",
            "recType": "cloud",
            "startTime": "20240102030405",
            "endTime": "20240102040405",
            "frameInterval": "10",
            "resultSpaceId": "7",
        },
        absent=(
            "projectId",
            "frameModel",
            "devProto",
            "streamType",
            "validateCode",
        ),
    )
    _assert_response(response)


def test_interval_task_sends_all_options():
    harness = _Harness()
    response = _drive_sync(
        harness,
        "create_interval_task",
        SERIAL,
        **_interval_kwargs(
            project_id="PROJ_B",
            result_space_id=3,
            frame_model=2,
            dev_proto="jt808",
            stream_type="2",
            validate_code="CODE9",
        ),
    )
    _post_form(
        harness.business_calls[0],
        INTERVAL_PATH,
        {
            "deviceSerial": SERIAL,
            "channelNo": "1",
            "recType": "cloud",
            "startTime": "20240102030405",
            "endTime": "20240102040405",
            "frameInterval": "10",
            "projectId": "PROJ_B",
            "resultSpaceId": "3",
            "frameModel": "2",
            "devProto": "jt808",
            "streamType": "2",
            "validateCode": "CODE9",
        },
    )
    _assert_response(response)


def test_interval_task_both_selectors_sent():
    harness = _Harness()
    _drive_sync(
        harness,
        "create_interval_task",
        SERIAL,
        **_interval_kwargs(project_id="PROJ_C", result_space_id=4),
    )
    form = _form(harness.business_calls[0])
    assert form.get("projectId") == ["PROJ_C"]
    assert form.get("resultSpaceId") == ["4"]


@pytest.mark.parametrize("proto", ["", "gb28181", "jt808"])
def test_interval_task_dev_proto_values(proto):
    harness = _Harness()
    _drive_sync(
        harness,
        "create_interval_task",
        SERIAL,
        **_interval_kwargs(dev_proto=proto),
    )
    form = _form(harness.business_calls[0])
    assert form.get("devProto") == [proto]


def test_interval_task_equal_times_are_sent():
    harness = _Harness()
    moment = datetime(2024, 5, 6, 7, 8, 9)
    _drive_sync(
        harness,
        "create_interval_task",
        SERIAL,
        **_interval_kwargs(start_time=moment, end_time=moment),
    )
    form = _form(harness.business_calls[0])
    assert form.get("startTime") == ["20240506070809"]
    assert form.get("endTime") == ["20240506070809"]


@pytest.mark.parametrize(
    "moment,stamp",
    [
        (datetime(1, 1, 2, 3, 4, 5), "00010102030405"),
        (datetime(999, 12, 31, 23, 59, 59), "09991231235959"),
    ],
)
def test_interval_task_year_is_four_digits(moment, stamp):
    harness = _Harness()
    _drive_sync(
        harness,
        "create_interval_task",
        SERIAL,
        **_interval_kwargs(start_time=moment, end_time=moment),
    )
    form = _form(harness.business_calls[0])
    assert form.get("startTime") == [stamp]
    assert form.get("endTime") == [stamp]


@pytest.mark.parametrize("channel", [0, -3, 99])
def test_interval_task_channel_sent_without_sign_bound(channel):
    harness = _Harness()
    _drive_sync(
        harness,
        "create_interval_task",
        SERIAL,
        **_interval_kwargs(channel=channel),
    )
    form = _form(harness.business_calls[0])
    assert form.get("channelNo") == [str(channel)]
    assert "channel" not in form


def test_interval_task_project_id_boundary():
    harness = _Harness()
    pid = "P" * 32
    _drive_sync(
        harness,
        "create_interval_task",
        SERIAL,
        **_interval_kwargs(project_id=pid),
    )
    form = _form(harness.business_calls[0])
    assert form.get("projectId") == [pid]


@pytest.mark.parametrize("value", BAD_SERIALS)
def test_interval_task_bad_device_serial(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="deviceSerial"):
        _drive_sync(harness, "create_interval_task", value, **_interval_kwargs())
    _assert_no_io(harness)


def test_interval_task_utf8_error_hides_input():
    harness = _Harness()
    bad = "SER\ud800IAL"
    with pytest.raises(ValueError) as excinfo:
        _drive_sync(harness, "create_interval_task", bad, **_interval_kwargs())
    message = str(excinfo.value)
    assert "UTF-8" in message
    assert bad not in message
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_CHANNELS)
def test_interval_task_bad_channel(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="channel"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(channel=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_REC_TYPES)
def test_interval_task_bad_rec_type(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="recType"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(rec_type=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_INTERVALS)
def test_interval_task_bad_frame_interval(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="frameInterval"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(frame_interval=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize(
    "field,value",
    [
        ("start_time", "20240102030405"),
        ("start_time", 1700000000),
        ("start_time", date(2024, 1, 2)),
        ("start_time", datetime(2024, 1, 2, 3, 4, 5, 600)),
        ("start_time", datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
        ("end_time", "20240102040405"),
        ("end_time", datetime(2024, 1, 2, 4, 4, 5, 999)),
        (
            "end_time",
            datetime(2024, 1, 2, 4, 4, 5, tzinfo=timezone(timedelta(hours=8))),
        ),
    ],
)
def test_interval_task_bad_times(field, value):
    harness = _Harness()
    marker = "startTime" if field == "start_time" else "endTime"
    with pytest.raises(ValueError, match=marker):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(**{field: value}),
        )
    _assert_no_io(harness)


def test_interval_task_reversed_times_rejected():
    harness = _Harness()
    with pytest.raises(ValueError, match="earlier"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(
                start_time=datetime(2024, 1, 2, 5, 0, 0),
                end_time=datetime(2024, 1, 2, 4, 0, 0),
            ),
        )
    _assert_no_io(harness)


def test_interval_task_requires_a_selector():
    harness = _Harness()
    with pytest.raises(ValueError, match="at least one of projectId"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(project_id=None, result_space_id=None),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_INTERVAL_PROJECT_IDS)
def test_interval_task_bad_project_id(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="projectId"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(project_id=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_INTERVAL_SPACE_IDS)
def test_interval_task_bad_result_space_id(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="resultSpaceId"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(result_space_id=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_FRAME_MODELS)
def test_interval_task_bad_frame_model(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="frameModel"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(frame_model=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_INTERVAL_PROTOS)
def test_interval_task_bad_dev_proto(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="devProto"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(dev_proto=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_STREAM_TYPES)
def test_interval_task_bad_stream_type(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="streamType"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(stream_type=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_VALIDATE_CODES)
def test_interval_task_bad_validate_code(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="validateCode"):
        _drive_sync(
            harness,
            "create_interval_task",
            SERIAL,
            **_interval_kwargs(validate_code=value),
        )
    _assert_no_io(harness)


def test_timing_task_wire_sync():
    harness = _Harness()
    response = _drive_sync(harness, "create_timing_task", SERIAL, **_timing_kwargs())
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    _post_form(
        harness.business_calls[0],
        TIMING_PATH,
        {
            "deviceSerial": SERIAL,
            "channelNo": "1",
            "recType": "local",
            "timingPoints": "20240203040506,20240203060506",
            "projectId": "PROJ_A",
        },
        absent=(
            "resultSpaceId",
            "frameModel",
            "devProto",
            "streamType",
            "validateCode",
            "startTime",
            "endTime",
            "frameInterval",
        ),
    )
    _assert_response(response)


def test_timing_task_wire_async():
    harness = _Harness()
    response = _drive_async(harness, "create_timing_task", SERIAL, **_timing_kwargs())
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    _post_form(
        harness.business_calls[0],
        TIMING_PATH,
        {
            "deviceSerial": SERIAL,
            "channelNo": "1",
            "recType": "local",
            "timingPoints": "20240203040506,20240203060506",
            "projectId": "PROJ_A",
        },
        absent=(
            "resultSpaceId",
            "frameModel",
            "devProto",
            "streamType",
            "validateCode",
        ),
    )
    _assert_response(response)


def test_timing_task_omits_absent_options():
    harness = _Harness()
    _drive_sync(
        harness,
        "create_timing_task",
        SERIAL,
        **_timing_kwargs(project_id=None, result_space_id=8),
    )
    _post_form(
        harness.business_calls[0],
        TIMING_PATH,
        {
            "deviceSerial": SERIAL,
            "channelNo": "1",
            "recType": "local",
            "timingPoints": "20240203040506,20240203060506",
            "resultSpaceId": "8",
        },
        absent=(
            "projectId",
            "frameModel",
            "devProto",
            "streamType",
            "validateCode",
        ),
    )


def test_timing_task_preserves_order_and_duplicates():
    harness = _Harness()
    points = [
        datetime(2024, 3, 1, 0, 0, 2),
        datetime(2024, 3, 1, 0, 0, 1),
        datetime(2024, 3, 1, 0, 0, 2),
    ]
    _drive_sync(
        harness,
        "create_timing_task",
        SERIAL,
        **_timing_kwargs(timing_points=points),
    )
    form = _form(harness.business_calls[0])
    assert form.get("timingPoints") == ["20240301000002,20240301000001,20240301000002"]


class _MutatingHarness(_Harness):
    """Harness whose real auth transport callback mutates ``points`` first."""

    def __init__(self, points):
        super().__init__()
        self.points = points

    def _handle_auth(self, request):
        self.points.clear()
        self.points.append(datetime(2024, 12, 31, 23, 59, 59))
        return super()._handle_auth(request)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_timing_task_points_snapshotted_before_auth(mode):
    points = [
        datetime(2024, 3, 1, 0, 0, 1),
        datetime(2024, 3, 1, 0, 0, 2),
    ]
    harness = _MutatingHarness(points)
    kwargs = _timing_kwargs(timing_points=points)
    if mode == "sync":
        _drive_sync(harness, "create_timing_task", SERIAL, **kwargs)
    else:
        _drive_async(harness, "create_timing_task", SERIAL, **kwargs)
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    # The auth callback really did mutate the caller's list.
    assert points == [datetime(2024, 12, 31, 23, 59, 59)]
    # The wire still carries the validated snapshot taken at call time.
    form = _form(harness.business_calls[0])
    assert form.get("timingPoints") == ["20240301000001,20240301000002"]


def test_timing_task_single_point():
    harness = _Harness()
    _drive_sync(
        harness,
        "create_timing_task",
        SERIAL,
        **_timing_kwargs(timing_points=[datetime(2024, 3, 1, 0, 0, 1)]),
    )
    form = _form(harness.business_calls[0])
    assert form.get("timingPoints") == ["20240301000001"]


def test_timing_task_unicode_project_id_sent():
    harness = _Harness()
    pid = "项目标识_" + "字" * 40
    _drive_sync(
        harness,
        "create_timing_task",
        SERIAL,
        **_timing_kwargs(project_id=pid),
    )
    form = _form(harness.business_calls[0])
    assert form.get("projectId") == [pid]


def test_timing_task_long_project_id_sent():
    harness = _Harness()
    pid = "P" * 64
    _drive_sync(
        harness,
        "create_timing_task",
        SERIAL,
        **_timing_kwargs(project_id=pid),
    )
    form = _form(harness.business_calls[0])
    assert form.get("projectId") == [pid]


@pytest.mark.parametrize("space", [0, -7, 123456])
def test_timing_task_result_space_id_sent_unbounded(space):
    harness = _Harness()
    _drive_sync(
        harness,
        "create_timing_task",
        SERIAL,
        **_timing_kwargs(project_id=None, result_space_id=space),
    )
    form = _form(harness.business_calls[0])
    assert form.get("resultSpaceId") == [str(space)]
    assert "projectId" not in form


@pytest.mark.parametrize("value", BAD_TIMING_SPACE_IDS)
def test_timing_task_bad_result_space_id(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="resultSpaceId"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(project_id=None, result_space_id=value),
        )
    _assert_no_io(harness)


def test_timing_task_frame_model_2_sent():
    harness = _Harness()
    _drive_sync(
        harness,
        "create_timing_task",
        SERIAL,
        **_timing_kwargs(frame_model=2),
    )
    form = _form(harness.business_calls[0])
    assert form.get("frameModel") == ["2"]


@pytest.mark.parametrize("proto", ["", "gb28181"])
def test_timing_task_dev_proto_values(proto):
    harness = _Harness()
    _drive_sync(
        harness,
        "create_timing_task",
        SERIAL,
        **_timing_kwargs(dev_proto=proto),
    )
    form = _form(harness.business_calls[0])
    assert form.get("devProto") == [proto]


def test_timing_task_rejects_jt808():
    harness = _Harness()
    with pytest.raises(ValueError, match="devProto"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(dev_proto="jt808"),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_POINT_SETS)
def test_timing_task_bad_points(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="timingPoints"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(timing_points=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_TIMING_PROJECT_IDS)
def test_timing_task_bad_project_id(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="projectId"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(project_id=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_CHANNELS)
def test_timing_task_bad_channel(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="channel"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(channel=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_REC_TYPES)
def test_timing_task_bad_rec_type(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="recType"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(rec_type=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_FRAME_MODELS)
def test_timing_task_bad_frame_model(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="frameModel"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(frame_model=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_STREAM_TYPES)
def test_timing_task_bad_stream_type(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="streamType"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(stream_type=value),
        )
    _assert_no_io(harness)


@pytest.mark.parametrize("value", BAD_VALIDATE_CODES)
def test_timing_task_bad_validate_code(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="validateCode"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(validate_code=value),
        )
    _assert_no_io(harness)


def test_timing_task_requires_a_selector():
    harness = _Harness()
    with pytest.raises(ValueError, match="at least one of projectId"):
        _drive_sync(
            harness,
            "create_timing_task",
            SERIAL,
            **_timing_kwargs(project_id=None, result_space_id=None),
        )
    _assert_no_io(harness)


def test_task_wire_sync():
    harness = _Harness()
    response = _drive_sync(harness, "task", TASK_ID)
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    request = harness.business_calls[0]
    assert request.method == "GET"
    assert request.url.path == TASK_PATH + TASK_ID
    assert request.content == b""
    assert not request.headers.get("content-type")
    assert "accessToken" not in request.headers
    assert "authorization" not in request.headers
    assert _query(request) == {"accessToken": [TOKEN]}
    _assert_response(response)


def test_task_wire_async():
    harness = _Harness()
    response = _drive_async(harness, "task", TASK_ID)
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    request = harness.business_calls[0]
    assert request.method == "GET"
    assert request.url.path == TASK_PATH + TASK_ID
    assert request.content == b""
    assert not request.headers.get("content-type")
    assert "accessToken" not in request.headers
    assert "authorization" not in request.headers
    assert _query(request) == {"accessToken": [TOKEN]}
    _assert_response(response)


@pytest.mark.parametrize("value", BAD_TASK_IDS)
def test_task_bad_task_id(value):
    harness = _Harness()
    with pytest.raises(ValueError, match="taskId"):
        _drive_sync(harness, "task", value)
    _assert_no_io(harness)


def test_task_id_boundary_32():
    harness = _Harness()
    task_id = "T" * 32
    _drive_sync(harness, "task", task_id)
    request = harness.business_calls[0]
    assert request.url.path == TASK_PATH + task_id


def _region_calls():
    return (
        ("create_interval_task", (SERIAL,), _interval_kwargs()),
        ("create_timing_task", (SERIAL,), _timing_kwargs()),
        ("task", (TASK_ID,), {}),
    )


@pytest.mark.parametrize("method,args,kwargs", _region_calls())
def test_sync_region_guard(method, args, kwargs):
    harness = _Harness(region="overseas")
    with pytest.raises(ValueError, match="domestic"):
        _drive_sync(harness, method, *args, **kwargs)
    _assert_no_io(harness)


@pytest.mark.parametrize("method,args,kwargs", _region_calls())
def test_async_region_guard(method, args, kwargs):
    harness = _Harness(region="overseas")
    with pytest.raises(ValueError, match="domestic"):
        _drive_async(harness, method, *args, **kwargs)
    _assert_no_io(harness)


def test_region_guard_precedes_parameter_validation():
    harness = _Harness(region="overseas")
    with pytest.raises(ValueError, match="domestic"):
        _drive_sync(
            harness,
            "create_interval_task",
            "",
            **_interval_kwargs(channel="bogus"),
        )
    _assert_no_io(harness)


def test_async_validation_failure_has_no_io():
    harness = _Harness()
    with pytest.raises(ValueError, match="deviceSerial"):
        _drive_async(harness, "create_timing_task", "  ", **_timing_kwargs())
    _assert_no_io(harness)


def test_create_interval_10002_raises_without_replay():
    harness = _Harness(responder=lambda request: httpx.Response(200, json=_meta_body(10002)))
    with pytest.raises(AuthenticationError):
        _drive_sync(harness, "create_interval_task", SERIAL, **_interval_kwargs())
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1


def test_create_timing_10002_raises_without_replay():
    harness = _Harness(responder=lambda request: httpx.Response(200, json=_meta_body(10002)))
    with pytest.raises(AuthenticationError):
        _drive_sync(harness, "create_timing_task", SERIAL, **_timing_kwargs())
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1


def test_task_recovers_once_from_10002():
    script = [
        httpx.Response(200, json=_meta_body(10002, "expired")),
        httpx.Response(200, json=dict(SUCCESS_BODY)),
    ]
    harness = _Harness(responder=lambda request: script.pop(0))
    response = _drive_sync(harness, "task", TASK_ID)
    _assert_response(response)
    assert script == []
    assert len(harness.auth_calls) == 2
    assert len(harness.business_calls) == 2
    for request in harness.business_calls:
        assert request.method == "GET"
        assert request.url.path == TASK_PATH + TASK_ID


def test_string_code_200_is_api_error():
    body = {"meta": {"code": "200", "message": "ok"}, "data": {}}
    harness = _Harness(responder=lambda request: httpx.Response(200, json=body))
    with pytest.raises(ApiError):
        _drive_sync(harness, "task", TASK_ID)
    assert len(harness.business_calls) == 1


def test_code_50001_is_api_error():
    harness = _Harness(responder=lambda request: httpx.Response(200, json=_meta_body(50001)))
    with pytest.raises(ApiError) as excinfo:
        _drive_sync(harness, "task", TASK_ID)
    assert not isinstance(excinfo.value, AuthenticationError)
    assert len(harness.business_calls) == 1


def test_http_503_is_api_error():
    harness = _Harness(responder=lambda request: httpx.Response(503, text="upstream down"))
    with pytest.raises(ApiError) as excinfo:
        _drive_sync(harness, "task", TASK_ID)
    assert excinfo.value.http_status == 503
    assert len(harness.business_calls) == 1
