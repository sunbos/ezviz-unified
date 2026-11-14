import asyncio
import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    CloudRecordingDevice,
    EzvizError,
    RegionConfig,
)

START = datetime(2030, 3, 4, 5, 6, 7)
END = datetime(2030, 3, 4, 6, 7, 8)
EARLIER = START - timedelta(seconds=1)
AWARE = datetime(2030, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
MICRO = datetime(2030, 3, 4, 5, 6, 7, microsecond=1)
PLAN_ID = 777001

DEV = CloudRecordingDevice(device_serial="DEVSYNTH1", local_index="1")
DEV2 = CloudRecordingDevice(device_serial="DEVSYNTH2", local_index="2", validate_code="VCD999")
DEV_WIRE = {"deviceSerial": "DEVSYNTH1", "localIndex": "1"}
DEV2_WIRE = {"deviceSerial": "DEVSYNTH2", "localIndex": "2", "validateCode": "VCD999"}

MIN_CREATE = {
    "plan_name": "Eval-计划_A1",
    "space_id": 908172,
    "start_time": START,
    "end_time": END,
    "devices": [DEV],
}
MIN_UPDATE = {
    "plan_name": "Eval_更新-B2",
    "space_id": 908173,
    "start_time": START,
    "end_time": END,
    "devices": [DEV2],
}
MIN_INSTANT = {
    "plan_name": "Eval Instant!!",
    "space_id": 908174,
    "hours": 3,
    "devices": [DEV],
}
NO_END = {key: value for key, value in MIN_CREATE.items() if key != "end_time"}

CREATE_KW = dict(
    MIN_CREATE,
    devices=[DEV, DEV2],
    template_id=424243,
    stream_type=2,
    specified_end_time=True,
    auto_delete=True,
)
UPDATE_KW = dict(MIN_UPDATE, template_id=313131, stream_type=1, specified_end_time=False)
INSTANT_KW = dict(MIN_INSTANT, template_id=515151, stream_type=1)

CASES = [
    (
        "create_one_off_plan",
        (),
        CREATE_KW,
        "POST",
        "/api/service/cloudrecord/video/plan/oneOff",
        "json",
        {
            "planName": "Eval-计划_A1",
            "spaceId": 908172,
            "startTime": "20300304050607",
            "endTime": "20300304060708",
            "devIndexInfos": [DEV_WIRE, DEV2_WIRE],
            "templateId": 424243,
            "streamType": 2,
            "specifiedEndTime": True,
            "autoDelete": True,
        },
    ),
    (
        "update_one_off_plan",
        (PLAN_ID,),
        UPDATE_KW,
        "PUT",
        "/api/service/cloudrecord/video/plan/oneOff",
        "json",
        {
            "oneOffPlanId": PLAN_ID,
            "planName": "Eval_更新-B2",
            "spaceId": 908173,
            "startTime": "20300304050607",
            "endTime": "20300304060708",
            "devIndexInfos": [DEV2_WIRE],
            "templateId": 313131,
            "streamType": 1,
            "specifiedEndTime": False,
        },
    ),
    (
        "stop_one_off_plan",
        (PLAN_ID,),
        {"only_stop_preview": True},
        "POST",
        "/api/service/cloudrecord/video/plan/oneOff/stop",
        "form",
        {"oneOffPlanId": str(PLAN_ID), "onlyStopPreview": "true"},
    ),
    (
        "delete_one_off_plan",
        (PLAN_ID,),
        {},
        "DELETE",
        "/api/service/cloudrecord/video/plan/oneOff",
        "form",
        {"oneOffPlanId": str(PLAN_ID)},
    ),
    (
        "create_instant_plan",
        (),
        INSTANT_KW,
        "POST",
        "/api/service/cloudrecord/video/plan/instant",
        "json",
        {
            "planName": "Eval Instant!!",
            "spaceId": 908174,
            "hours": 3,
            "devIndexInfos": [DEV_WIRE],
            "templateId": 515151,
            "streamType": 1,
        },
    ),
]


def payload(name, data=None):
    if data is None:
        data = {"evalMarker": name}
    if name == "create_instant_plan":
        return {"code": "200", "msg": "eval-ok", "data": data}
    return {"meta": {"code": 200, "message": "eval-ok"}, "data": data}


def error_payload(name, code):
    if name == "create_instant_plan":
        return {"code": code, "msg": "eval-fail", "data": None}
    return {"meta": {"code": code, "message": "eval-fail"}, "data": None}


def _harness(name, region, responses):
    requests = []
    auth_requests = []
    replies = iter(responses) if responses is not None else None

    def auth(req):
        auth_requests.append(req)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": f"at.EVAL_{len(auth_requests)}",
                    "expireTime": 2000000,
                },
            },
        )

    def business(req):
        requests.append(req)
        return httpx.Response(200, json=next(replies) if replies is not None else payload(name))

    options = dict(
        region=region or RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda base_url: httpx.MockTransport(business),
        clock=lambda: 1000,
    )
    return requests, auth_requests, options


def _invoke(kind, name, args, kwargs, options):
    if kind == "sync":
        with Client("EVAL_KEY", "EVAL_SECRET", **options) as client:
            return getattr(client.cloud_recording, name)(*args, **kwargs)

    async def run():
        async with AsyncClient("EVAL_KEY", "EVAL_SECRET", **options) as client:
            return await getattr(client.cloud_recording, name)(*args, **kwargs)

    return asyncio.run(run())


def execute(kind, case, *, region=None, responses=None):
    name, args, kwargs, *_ = case
    requests, auth_requests, options = _harness(name, region, responses)
    try:
        result = _invoke(kind, name, args, kwargs, options)
    except ValueError:
        assert not requests and not auth_requests
        raise
    return result, requests, auth_requests


def execute_error(kind, case, *, region=None, responses=None):
    name, args, kwargs, *_ = case
    requests, auth_requests, options = _harness(name, region, responses)
    with pytest.raises(Exception) as info:
        _invoke(kind, name, args, kwargs, options)
    return info.value, requests, auth_requests


def form_fields(request):
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("case", CASES, ids=lambda c: c[0])
def test_http_method_path_headers_and_payload(kind, case):
    name, _, _, method, path, encoding, wire = case
    result, requests, auth = execute(kind, case)
    assert len(auth) == len(requests) == 1
    request = requests[0]
    assert request.method == method
    assert request.url.path == path
    assert request.url.host == "open.ys7.com"
    assert request.headers["accessToken"] == "at.EVAL_1"
    assert not request.url.query
    if encoding == "json":
        assert request.headers["Content-Type"].startswith("application/json")
        actual = json.loads(request.content)
    else:
        assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
        actual = form_fields(request)
    assert actual == wire
    assert "accessToken" not in actual
    assert result.raw == payload(name)
    assert result.data == payload(name)["data"]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("case", CASES, ids=lambda c: c[0])
def test_overseas_rejected_before_any_network(kind, case):
    exc, requests, auth = execute_error(kind, case, region=RegionConfig.overseas())
    assert isinstance(exc, ValueError)
    assert not requests and not auth


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_none_optionals_are_omitted(kind):
    _, requests, _ = execute(kind, ("create_one_off_plan", (), MIN_CREATE))
    body = json.loads(requests[0].content)
    assert set(body) == {"planName", "spaceId", "startTime", "endTime", "devIndexInfos"}
    assert "validateCode" not in body["devIndexInfos"][0]

    # DEV2 carries validate_code, so this call uses DEV to assert omission;
    # the full UPDATE_KW case still covers sending DEV2's validateCode.
    _, requests, _ = execute(
        kind,
        ("update_one_off_plan", (PLAN_ID,), {**MIN_UPDATE, "devices": [DEV]}),
    )
    body = json.loads(requests[0].content)
    assert set(body) == {
        "oneOffPlanId",
        "planName",
        "spaceId",
        "startTime",
        "endTime",
        "devIndexInfos",
    }
    assert "validateCode" not in body["devIndexInfos"][0]

    _, requests, _ = execute(kind, ("create_instant_plan", (), MIN_INSTANT))
    body = json.loads(requests[0].content)
    assert set(body) == {"planName", "spaceId", "hours", "devIndexInfos"}

    _, requests, _ = execute(kind, ("stop_one_off_plan", (PLAN_ID,), {}))
    assert form_fields(requests[0]) == {"oneOffPlanId": str(PLAN_ID)}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_explicit_bool_values_are_preserved(kind):
    _, requests, _ = execute(
        kind,
        (
            "create_one_off_plan",
            (),
            {**MIN_CREATE, "specified_end_time": True, "auto_delete": False},
        ),
    )
    body = json.loads(requests[0].content)
    assert body["specifiedEndTime"] is True and body["autoDelete"] is False

    _, requests, _ = execute(
        kind,
        (
            "update_one_off_plan",
            (PLAN_ID,),
            {**MIN_UPDATE, "specified_end_time": False},
        ),
    )
    assert json.loads(requests[0].content)["specifiedEndTime"] is False

    for flag, text in [(True, "true"), (False, "false")]:
        _, requests, _ = execute(
            kind, ("stop_one_off_plan", (PLAN_ID,), {"only_stop_preview": flag})
        )
        assert form_fields(requests[0])["onlyStopPreview"] == text


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "bad",
    [
        {"meta": {"code": "200", "message": "eval-ok"}, "data": None},
        {"code": 200, "data": None},
        {"meta": {"code": 201, "message": "eval-ok"}, "data": None},
    ],
    ids=["code-as-string", "wrong-layer", "non-200"],
)
def test_meta_writes_require_integer_meta_code_200(kind, bad):
    exc, requests, _ = execute_error(kind, ("stop_one_off_plan", (PLAN_ID,), {}), responses=[bad])
    assert isinstance(exc, EzvizError)
    assert len(requests) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "bad",
    [
        {"code": 200, "data": None},
        {"meta": {"code": 200, "message": "eval-ok"}, "data": None},
        {"code": "201", "data": None},
    ],
    ids=["code-as-int", "wrong-layer", "non-200"],
)
def test_instant_requires_top_level_string_code_200(kind, bad):
    exc, requests, _ = execute_error(
        kind, ("create_instant_plan", (), MIN_INSTANT), responses=[bad]
    )
    assert isinstance(exc, EzvizError)
    assert len(requests) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("name", [case[0] for case in CASES])
def test_write_not_replayed_on_invalid_token(kind, name):
    case = next(item for item in CASES if item[0] == name)
    exc, requests, _ = execute_error(kind, case, responses=[error_payload(name, 10002)])
    assert isinstance(exc, AuthenticationError)
    assert len(requests) == 1


BAD_CASES = [
    ("create_one_off_plan", (), {**MIN_CREATE, "plan_name": ""}),
    ("create_one_off_plan", (), {**MIN_CREATE, "plan_name": "x" * 33}),
    ("create_one_off_plan", (), {**MIN_CREATE, "plan_name": "bad!name"}),
    ("create_one_off_plan", (), {**MIN_CREATE, "plan_name": "with space"}),
    ("create_one_off_plan", (), {**MIN_CREATE, "plan_name": 12345}),
    ("create_one_off_plan", (), {**MIN_CREATE, "space_id": True}),
    ("create_one_off_plan", (), {**MIN_CREATE, "space_id": "908172"}),
    ("create_one_off_plan", (), {**MIN_CREATE, "start_time": AWARE}),
    ("create_one_off_plan", (), {**MIN_CREATE, "start_time": MICRO}),
    ("create_one_off_plan", (), {**MIN_CREATE, "start_time": "20300304050607"}),
    ("create_one_off_plan", (), {**MIN_CREATE, "end_time": AWARE}),
    ("create_one_off_plan", (), {**MIN_CREATE, "template_id": False}),
    ("create_one_off_plan", (), {**MIN_CREATE, "stream_type": 0}),
    ("create_one_off_plan", (), {**MIN_CREATE, "stream_type": 3}),
    ("create_one_off_plan", (), {**MIN_CREATE, "stream_type": True}),
    ("create_one_off_plan", (), {**MIN_CREATE, "specified_end_time": "yes"}),
    ("create_one_off_plan", (), {**MIN_CREATE, "specified_end_time": 1}),
    ("create_one_off_plan", (), {**MIN_CREATE, "auto_delete": 0}),
    ("create_one_off_plan", (), {**MIN_CREATE, "auto_delete": "no"}),
    ("create_one_off_plan", (), {**MIN_CREATE, "devices": DEV}),
    ("create_one_off_plan", (), {**MIN_CREATE, "devices": "DEVSYNTH1"}),
    ("create_one_off_plan", (), {**MIN_CREATE, "devices": b"DEVSYNTH1"}),
    ("create_one_off_plan", (), {**MIN_CREATE, "devices": [DEV, "DEVSYNTH2"]}),
    (
        "create_one_off_plan",
        (),
        {**MIN_CREATE, "devices": (item for item in [DEV])},
    ),
    ("create_one_off_plan", (), {**MIN_CREATE, "devices": []}),
    ("create_one_off_plan", (), {**MIN_CREATE, "devices": [DEV] * 1001}),
    ("update_one_off_plan", (True,), MIN_UPDATE),
    ("update_one_off_plan", ("777001",), MIN_UPDATE),
    ("update_one_off_plan", (PLAN_ID,), {**MIN_UPDATE, "plan_name": "bad?name"}),
    ("update_one_off_plan", (PLAN_ID,), {**MIN_UPDATE, "end_time": None}),
    (
        "update_one_off_plan",
        (PLAN_ID,),
        {**MIN_UPDATE, "specified_end_time": False, "end_time": None},
    ),
    ("update_one_off_plan", (PLAN_ID,), {**MIN_UPDATE, "specified_end_time": 0}),
    ("update_one_off_plan", (PLAN_ID,), {**MIN_UPDATE, "devices": []}),
    ("update_one_off_plan", (PLAN_ID,), {**MIN_UPDATE, "devices": [DEV] * 1001}),
    ("stop_one_off_plan", (None,), {}),
    ("stop_one_off_plan", (PLAN_ID,), {"only_stop_preview": 0}),
    ("stop_one_off_plan", (PLAN_ID,), {"only_stop_preview": "false"}),
    ("delete_one_off_plan", (True,), {}),
    ("delete_one_off_plan", ("777001",), {}),
    ("create_instant_plan", (), {**MIN_INSTANT, "hours": 0}),
    ("create_instant_plan", (), {**MIN_INSTANT, "hours": -3}),
    ("create_instant_plan", (), {**MIN_INSTANT, "hours": True}),
    ("create_instant_plan", (), {**MIN_INSTANT, "hours": "3"}),
    ("create_instant_plan", (), {**MIN_INSTANT, "plan_name": ""}),
    ("create_instant_plan", (), {**MIN_INSTANT, "plan_name": 12345}),
    ("create_instant_plan", (), {**MIN_INSTANT, "devices": "DEVSYNTH1"}),
    ("create_instant_plan", (), {**MIN_INSTANT, "devices": [DEV, 5]}),
    ("create_instant_plan", (), {**MIN_INSTANT, "template_id": True}),
    ("create_instant_plan", (), {**MIN_INSTANT, "stream_type": 5}),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("case", BAD_CASES, ids=lambda c: c[0])
def test_invalid_inputs_rejected_before_auth(kind, case):
    exc, requests, auth = execute_error(kind, case)
    assert isinstance(exc, ValueError)
    assert not requests and not auth


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_update_end_time_is_a_required_argument(kind):
    kwargs = {key: value for key, value in MIN_UPDATE.items() if key != "end_time"}
    exc, requests, auth = execute_error(kind, ("update_one_off_plan", (PLAN_ID,), kwargs))
    assert isinstance(exc, TypeError)
    assert not requests and not auth


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_create_end_time_rules(kind):
    # explicit False lets the plan omit end_time and still sends the flag
    _, requests, _ = execute(
        kind, ("create_one_off_plan", (), {**NO_END, "specified_end_time": False})
    )
    body = json.loads(requests[0].content)
    assert "endTime" not in body and body["specifiedEndTime"] is False

    # platform default (flag omitted) still requires end_time; flag is not sent
    exc, requests, auth = execute_error(kind, ("create_one_off_plan", (), NO_END))
    assert isinstance(exc, ValueError) and not requests and not auth

    exc, requests, auth = execute_error(
        kind, ("create_one_off_plan", (), {**NO_END, "specified_end_time": True})
    )
    assert isinstance(exc, ValueError) and not requests and not auth

    # explicit False together with an end_time sends both
    _, requests, _ = execute(
        kind,
        ("create_one_off_plan", (), {**MIN_CREATE, "specified_end_time": False}),
    )
    body = json.loads(requests[0].content)
    assert body["specifiedEndTime"] is False
    assert body["endTime"] == "20300304060708"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_end_time_must_not_precede_start(kind):
    for name, args, kwargs in [
        ("create_one_off_plan", (), {**MIN_CREATE, "end_time": EARLIER}),
        ("update_one_off_plan", (PLAN_ID,), {**MIN_UPDATE, "end_time": EARLIER}),
    ]:
        exc, requests, auth = execute_error(kind, (name, args, kwargs))
        assert isinstance(exc, ValueError) and not requests and not auth

    # equal start/end is accepted
    _, requests, _ = execute(kind, ("create_one_off_plan", (), {**MIN_CREATE, "end_time": START}))
    assert json.loads(requests[0].content)["endTime"] == "20300304050607"


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("count", [1, 1000])
@pytest.mark.parametrize(
    "name,args,base",
    [
        ("create_one_off_plan", (), MIN_CREATE),
        ("update_one_off_plan", (PLAN_ID,), MIN_UPDATE),
    ],
)
def test_one_off_device_bounds_accepted(kind, name, args, base, count):
    devices = [
        CloudRecordingDevice(device_serial=f"D{index:05}", local_index="1")
        for index in range(count)
    ]
    _, requests, _ = execute(kind, (name, args, {**base, "devices": devices}))
    assert len(json.loads(requests[0].content)["devIndexInfos"]) == count


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("count", [0, 1001])
@pytest.mark.parametrize(
    "name,args,base",
    [
        ("create_one_off_plan", (), MIN_CREATE),
        ("update_one_off_plan", (PLAN_ID,), MIN_UPDATE),
    ],
)
def test_one_off_device_bounds_rejected(kind, name, args, base, count):
    devices = [
        CloudRecordingDevice(device_serial=f"D{index:05}", local_index="1")
        for index in range(count)
    ]
    exc, requests, auth = execute_error(kind, (name, args, {**base, "devices": devices}))
    assert isinstance(exc, ValueError)
    assert not requests and not auth


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("count", [0, 1500])
def test_instant_device_count_not_bounded(kind, count):
    devices = [
        CloudRecordingDevice(device_serial=f"D{index:05}", local_index="1")
        for index in range(count)
    ]
    _, requests, _ = execute(kind, ("create_instant_plan", (), {**MIN_INSTANT, "devices": devices}))
    assert len(json.loads(requests[0].content)["devIndexInfos"]) == count


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_instant_hours_only_requires_positive(kind):
    _, requests, _ = execute(kind, ("create_instant_plan", (), {**MIN_INSTANT, "hours": 72}))
    assert json.loads(requests[0].content)["hours"] == 72


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_plan_name_boundaries(kind):
    name = "计" * 32
    _, requests, _ = execute(kind, ("create_one_off_plan", (), {**MIN_CREATE, "plan_name": name}))
    assert json.loads(requests[0].content)["planName"] == name

    name = "A-1_b" * 6 + "AA"  # exactly 32 allowed characters
    _, requests, _ = execute(
        kind, ("update_one_off_plan", (PLAN_ID,), {**MIN_UPDATE, "plan_name": name})
    )
    assert json.loads(requests[0].content)["planName"] == name


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_caller_devices_sequence_is_not_mutated(kind):
    devices = [
        DEV,
        DEV2,
        CloudRecordingDevice(device_serial="DEVSYNTH3", local_index="7"),
    ]
    snapshot = list(devices)
    _, requests, _ = execute(kind, ("create_one_off_plan", (), {**MIN_CREATE, "devices": devices}))
    assert devices == snapshot
    assert all(left is right for left, right in zip(devices, snapshot))
    assert len(json.loads(requests[0].content)["devIndexInfos"]) == 3


@pytest.mark.parametrize(
    "kwargs,sentinel",
    [
        ({"device_serial": "", "local_index": "1"}, None),
        ({"device_serial": "   ", "local_index": "1"}, None),
        ({"device_serial": "DEVSYNTH", "local_index": ""}, None),
        (
            {"device_serial": "DEVSYNTH", "local_index": "1", "validate_code": ""},
            None,
        ),
        (
            {
                "device_serial": "DEVSYNTH",
                "local_index": "1",
                "validate_code": 98765,
            },
            "98765",
        ),
        ({"device_serial": 99887, "local_index": "1"}, "99887"),
        ({"device_serial": "DEVSYNTH", "local_index": 77665}, "77665"),
    ],
)
def test_dto_rejects_invalid_fields(kwargs, sentinel):
    with pytest.raises(ValueError) as info:
        CloudRecordingDevice(**kwargs)
    if sentinel is not None:
        assert sentinel not in str(info.value)


def test_dto_repr_hides_serial_and_validate_code():
    rendered = repr(DEV2)
    assert "DEVSYNTH2" not in rendered
    assert "VCD999" not in rendered
    assert "local_index" in rendered


def test_plan_name_and_device_fields_redacted_in_errors():
    err = ApiError(
        "failed planName=Secr3t-计划&deviceSerial=DEVSYNTH9&validateCode=VCD000",
        operation="cloud_recording.create_one_off_plan",
    )
    for rendered in (str(err), repr(err)):
        assert "Secr3t" not in rendered
        assert "DEVSYNTH9" not in rendered
        assert "VCD000" not in rendered
    assert "planName=***" in str(err)


class _ShiftyDevices(Sequence[CloudRecordingDevice]):
    """Sequence yielding [DEV] on the first iteration and [DEV2] later."""

    def __init__(self):
        self.iterations = 0

    def __len__(self):
        return 1

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return DEV

    def __iter__(self):
        self.iterations += 1
        return iter([DEV] if self.iterations == 1 else [DEV2])


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_devices_snapshot_is_iterated_once(kind):
    shifty = _ShiftyDevices()
    _, requests, _ = execute(kind, ("create_one_off_plan", (), {**MIN_CREATE, "devices": shifty}))
    assert shifty.iterations == 1
    assert json.loads(requests[0].content)["devIndexInfos"] == [DEV_WIRE]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_instant_plan_rejects_validate_code_before_auth(kind):
    exc, requests, auth = execute_error(
        kind,
        ("create_instant_plan", (), {**MIN_INSTANT, "devices": [DEV, DEV2]}),
    )
    assert isinstance(exc, ValueError)
    assert not requests and not auth
    assert "VCD999" not in str(exc)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_error_message_echoing_wire_values_is_redacted(kind):
    echoed = ["Eval-计划_A1", "DEVSYNTH1", "DEVSYNTH2", "VCD999"]
    bad = {
        "meta": {"code": 10017, "message": "rejected: " + " / ".join(echoed)},
        "data": None,
    }
    exc, requests, _ = execute_error(kind, ("create_one_off_plan", (), CREATE_KW), responses=[bad])
    assert isinstance(exc, EzvizError)
    for rendered in (str(exc), repr(exc)):
        for value in echoed:
            assert value not in rendered
    wire = json.loads(requests[0].content)
    assert wire["planName"] == "Eval-计划_A1"
    assert wire["devIndexInfos"] == [DEV_WIRE, DEV2_WIRE]
