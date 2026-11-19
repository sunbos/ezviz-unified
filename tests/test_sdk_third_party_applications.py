"""Contract tests for the third-party protocol license mutation APIs.

Covers the four POST endpoints under ``/api/service/thirdproto`` —
project open (5364), single license apply (5366), batch license apply
(5367) and license edit (5370) — in both sync and async modes.

Every value used here is a synthetic sentinel.  No real device serials,
plates, credentials or network resources are involved, and no attempt is
made to poll tasks or dump secrets.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from test_sdk_third_party_protocols import Probe

from ezviz_unified import RegionConfig
from ezviz_unified.errors import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)

_MODES = ("sync", "async")
_BASE = "/api/service/thirdproto"


def _ok(data):
    return {"meta": {"code": 200, "message": "ok"}, "data": data}


def _form(req: httpx.Request) -> list[tuple[str, str]]:
    """Sorted (name, value) form pairs; asserts the query stays empty."""
    assert req.method == "POST"
    assert not req.url.query, "business parameters must stay in the form body"
    return sorted(httpx.QueryParams(req.content.decode("utf-8")).multi_items())


def _posted(req: httpx.Request, path: str) -> None:
    assert req.url.scheme == "https"
    assert req.url.host == "open.ys7.com"
    assert req.url.path == path
    assert req.headers.get_list("content-type") == ["application/x-www-form-urlencoded"]
    assert req.headers.get_list("accessToken") == ["TOK1"], "token must travel as a header"
    assert "TOK" not in str(req.url)


def _calls(p: Probe, name: str, mode: str) -> list:
    return [
        e["outcome"]
        for e in p.recorder.report()["events"]
        if e.get("kind") == "call"
        and e.get("entrypoint") == f"third_party_protocols.{name}"
        and e.get("mode") == mode
    ]


def _attempts(p: Probe, name: str) -> list:
    return [
        e
        for e in p.recorder.report()["events"]
        if e.get("kind") == "attempt"
        and e.get("entrypoint") == "client.request"
        and e.get("operation") == f"third_party_protocols.{name}"
    ]


# ---------------------------------------------------------------- open (5364)


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("protocol", ["dhreg", "huawei", "jt808", "tiandy"])
def test_open_project_posts_empty_form(mode, protocol):
    p = Probe(_ok({"createTime": 7, "productKey": "SYNTH-PRODUCT"}))
    resp = p.call(mode, "open_project", protocol)
    assert resp.code == 200
    (req,) = p.biz
    _posted(req, f"{_BASE}/{protocol}/project")
    assert req.content == b""  # explicit empty form, not a missing body
    assert _form(req) == []
    assert _calls(p, "open_project", mode) == ["started", "succeeded"]
    assert [e["outcome"] for e in _attempts(p, "open_project")] == ["started", "succeeded"]


# ------------------------------------------------------- single apply (5366)


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("channels", [0, 256])
def test_apply_device_tiandy_full_form(mode, channels):
    p = Probe(_ok({"deviceSerial": "SN-TIANDY-0001", "serial": "SN-TIANDY-0001"}))
    resp = p.call(
        mode,
        "apply_device",
        "tiandy",
        "边缘网关·甲",
        device_password="Xy9Secr3t",
        device_username="SentUser42",
        channel_count=channels,
        device_code="SYNTH-CODE-01",
    )
    assert resp.code == 200
    assert resp.data["deviceSerial"] == "SN-TIANDY-0001"  # opaque serial
    (req,) = p.biz
    _posted(req, f"{_BASE}/tiandy/deviceLicense/apply")
    assert _form(req) == sorted(
        [
            ("deviceName", "边缘网关·甲"),
            ("deviceLicense", "Xy9Secr3t"),
            ("deviceUserName", "SentUser42"),
            ("channelCount", str(channels)),
            ("deviceCode", "SYNTH-CODE-01"),
        ]
    )
    assert "deviceLicense=Xy9Secr3t" in req.content.decode("utf-8")
    assert _calls(p, "apply_device", mode) == ["started", "succeeded"]


@pytest.mark.parametrize("mode", _MODES)
def test_apply_device_tiandy_minimal_omits_optionals(mode):
    p = Probe(_ok({"deviceSerial": "SN-TIANDY-0002", "serial": "SN-TIANDY-0002"}))
    resp = p.call(
        mode,
        "apply_device",
        "tiandy",
        "终端二",
        device_password="Pass1234",
        device_username="User1234",
    )
    assert resp.code == 200
    (req,) = p.biz
    assert _form(req) == sorted(
        [
            ("deviceName", "终端二"),
            ("deviceLicense", "Pass1234"),
            ("deviceUserName", "User1234"),
        ]
    )


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("plate,color", [("测A12345", 0), ("测B123456", 9)])
def test_apply_device_jt808_vehicle_form(mode, plate, color):
    p = Probe(_ok({"deviceSerial": "SN-JT-0001", "serial": "SN-JT-0001"}))
    resp = p.call(
        mode,
        "apply_device",
        "jt808",
        "冷链车-07",
        vehicle_plate=plate,
        vehicle_color=color,
    )
    assert resp.code == 200
    (req,) = p.biz
    _posted(req, f"{_BASE}/jt808/deviceLicense/apply")
    assert _form(req) == sorted(
        [
            ("deviceName", "冷链车-07"),
            ("vehicleLicensePlate", plate),
            ("vehicleLicenseColor", str(color)),  # wire string, 0 included
        ]
    )


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"vehicle_plate": "测A12345"},
        {"vehicle_color": 1},
        {"vehicle_plate": "测A12345", "vehicle_color": 0, "device_password": "Abcd1234"},
        {"vehicle_plate": "测A12345", "vehicle_color": 0, "device_username": "User1234"},
    ],
    ids=["missing_both", "missing_color", "missing_plate", "password", "username"],
)
def test_apply_device_jt808_rejected_pre_auth(mode, kwargs):
    p = Probe()
    with pytest.raises(ValueError):
        p.call(mode, "apply_device", "jt808", "冷链车", **kwargs)
    assert p.auth == [] and p.biz == []


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "field,value",
    [
        ("channel_count", 257),
        ("channel_count", -1),
        ("channel_count", True),
        ("device_password", "ab"),
        ("device_password", "bad pass!"),
        ("device_password", "密码1234"),
        ("device_username", "u" * 21),
        ("device_username", "bad-name"),
    ],
)
def test_apply_device_field_validation_pre_auth(mode, field, value):
    p = Probe()
    with pytest.raises(ValueError):
        p.call(mode, "apply_device", "tiandy", "边缘网关", **{field: value})
    assert p.auth == [] and p.biz == []


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("bad", ["", "x" * 51, "lone\udcff", 1234, b"bytes"])
def test_apply_device_name_validation(mode, bad):
    p = Probe()
    with pytest.raises(ValueError):
        p.call(mode, "apply_device", "tiandy", bad)
    assert p.auth == [] and p.biz == []


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("plate", ["测112345", "测A1234", "测A1234567", "测中12345"])
def test_apply_device_jt808_plate_validation(mode, plate):
    p = Probe()
    with pytest.raises(ValueError):
        p.call(
            mode,
            "apply_device",
            "jt808",
            "冷链车",
            vehicle_plate=plate,
            vehicle_color=0,
        )
    assert p.auth == [] and p.biz == []


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("color", [-1, 10, True])
def test_apply_device_jt808_color_validation(mode, color):
    p = Probe()
    with pytest.raises(ValueError):
        p.call(
            mode,
            "apply_device",
            "jt808",
            "冷链车",
            vehicle_plate="测A12345",
            vehicle_color=color,
        )
    assert p.auth == [] and p.biz == []


# -------------------------------------------------------- batch apply (5367)


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("count", [1, 300])
def test_apply_batch_count_form(mode, count):
    p = Probe(_ok({"taskId": 48151623}))
    resp = p.call(
        mode,
        "apply_batch",
        "tiandy",
        device_count=count,
        device_username="BatchUser9",
        device_password="Btch1234",
        channel_count=0,
    )
    assert resp.code == 200
    assert resp.data["taskId"] == 48151623
    assert isinstance(resp.data["taskId"], int)  # genuine int, not str
    (req,) = p.biz
    _posted(req, f"{_BASE}/tiandy/licenses/apply")
    assert _form(req) == sorted(
        [
            ("deviceCount", str(count)),
            ("deviceLicense", "Btch1234"),
            ("deviceUserName", "BatchUser9"),
            ("channelCount", "0"),
        ]
    )


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("cred", ["omit", "empty"])
def test_apply_batch_codes_csv_and_generated_credentials(mode, cred):
    codes = ["SN-ORD-2", "SN-ORD-1", "SN-ORD-3"]
    kwargs = {} if cred == "omit" else {"device_password": "", "device_username": ""}
    p = Probe(_ok({"taskId": 77}))
    resp = p.call(mode, "apply_batch", "tiandy", device_codes=codes, **kwargs)
    assert resp.code == 200
    (req,) = p.biz
    _posted(req, f"{_BASE}/tiandy/licenses/apply")
    # ordered CSV, never a python repr; platform-generated credentials send
    # nothing; a code list must not be accompanied by deviceCount.
    expected = [("deviceCode", "SN-ORD-2,SN-ORD-1,SN-ORD-3")]
    if cred == "empty":
        expected += [("deviceUserName", ""), ("deviceLicense", "")]
    assert sorted(_form(req)) == sorted(expected)


@pytest.mark.parametrize("mode", _MODES)
def test_apply_batch_jt808_vehicle_csv(mode):
    p = Probe(_ok({"taskId": 9001}))
    resp = p.call(
        mode,
        "apply_batch",
        "jt808",
        device_count=2,
        vehicle_plates=["测A12345", "沪B123456"],
        vehicle_colors=[0, 9],
    )
    assert resp.code == 200
    (req,) = p.biz
    _posted(req, f"{_BASE}/jt808/licenses/apply")
    assert _form(req) == sorted(
        [
            ("deviceCount", "2"),
            ("vehicleLicensePlate", "测A12345,沪B123456"),
            ("vehicleLicenseColor", "0,9"),
        ]
    )


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"device_count": 0},
        {"device_count": True},
        {"device_count": 301},
        {"device_codes": "SN1,SN2"},
        {"device_codes": b"SN1"},
        {"device_codes": ["SN,1", "SN2"]},
        {"device_codes": ["SN1", ""]},
        {"device_codes": ["SN1", 42]},
    ],
    ids=[
        "neither",
        "zero",
        "bool_count",
        "above_batch_limit",
        "raw_str",
        "bytes",
        "comma_member",
        "blank_member",
        "int_member",
    ],
)
def test_apply_batch_rejects_bad_args(mode, kwargs):
    p = Probe()
    with pytest.raises(ValueError):
        p.call(mode, "apply_batch", "tiandy", **kwargs)
    assert p.auth == [] and p.biz == []


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "extra",
    [
        {"vehicle_plates": "测A12345"},
        {"vehicle_colors": "0,9"},
        {"vehicle_colors": [0, 10]},
        {"vehicle_colors": [True, 9]},
        {"vehicle_plates": ["测A12345", "沪B12345"], "vehicle_colors": [1]},
        {"vehicle_plates": ["测A12345"], "vehicle_colors": [1, 2]},
    ],
    ids=[
        "raw_plates",
        "raw_colors",
        "color_range",
        "color_bool",
        "colors_short",
        "plates_short",
    ],
)
def test_apply_batch_jt808_rejects_bad_sequences(mode, extra):
    p = Probe()
    with pytest.raises(ValueError):
        p.call(mode, "apply_batch", "jt808", device_count=2, **extra)
    assert p.auth == [] and p.biz == []


# ------------------------------------------------------------- edit (5370)


@pytest.mark.parametrize("mode", _MODES)
def test_edit_device_serial_only(mode):
    p = Probe(_ok(None))
    resp = p.call(mode, "edit_device", "tiandy", "SN-FROZEN-7")
    assert resp.code == 200
    assert resp.data is None  # ack carries data:null
    (req,) = p.biz
    _posted(req, f"{_BASE}/tiandy/licenses/edit")
    assert _form(req) == [("deviceSerial", "SN-FROZEN-7")]


@pytest.mark.parametrize("mode", _MODES)
def test_edit_device_long_name_and_new_credentials(mode):
    name = "海外节点-" + "长" * 60  # > 50 chars is allowed on this route
    p = Probe(_ok(None))
    resp = p.call(
        mode,
        "edit_device",
        "huawei",
        "SN-HW-9",
        device_name=name,
        device_password="NwPass99",
        device_username="NwUser77",
    )
    assert resp.code == 200
    (req,) = p.biz
    _posted(req, f"{_BASE}/huawei/licenses/edit")
    assert _form(req) == sorted(
        [
            ("deviceSerial", "SN-HW-9"),
            ("deviceName", name),
            ("deviceLicense", "NwPass99"),
            ("deviceUserName", "NwUser77"),
        ]
    )
    # supplied credentials are posted verbatim; the SDK never auto-fetches them
    assert "deviceLicense=NwPass99" in req.content.decode("utf-8")


@pytest.mark.parametrize("mode", _MODES)
def test_edit_device_jt808_color_wire_string(mode):
    p = Probe(_ok(None))
    p.call(
        mode,
        "edit_device",
        "jt808",
        "JT-SERIAL-0",
        vehicle_plate="测C12345",
        vehicle_color=0,
    )
    (req,) = p.biz
    _posted(req, f"{_BASE}/jt808/licenses/edit")
    assert _form(req) == sorted(
        [
            ("deviceSerial", "JT-SERIAL-0"),
            ("vehicleLicensePlate", "测C12345"),
            ("vehicleLicenseColor", "0"),
        ]
    )
    assert "vehicleLicenseColor=0" in req.content.decode("utf-8")


# -------------------------------------------------- failure / fallback paths


def _http(status: int) -> httpx.Response:
    return httpx.Response(
        status,
        json={"meta": {"code": status, "message": "synthetic"}, "data": None},
    )


_FAILURES = [
    ("timeout", httpx.ReadTimeout("stalled"), TransportError),
    ("closed", httpx.CloseError("dropped"), TransportError),
    (
        "code10002",
        {"meta": {"code": 10002, "message": "expired"}, "data": None},
        AuthenticationError,
    ),
    (
        "code10031",
        {"meta": {"code": 10031, "message": "denied"}, "data": None},
        PermissionDeniedError,
    ),
    ("http403", 403, PermissionDeniedError),
    ("http429", 429, ApiError),
    (
        "bad_shape",
        {"meta": {"code": 200, "message": "ok"}, "data": ["not", "an", "object"]},
        ProtocolError,
    ),
    ("code_str", {"meta": {"code": "200", "message": "ok"}, "data": {}}, ApiError),
]


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "step,exc",
    [item[1:] for item in _FAILURES],
    ids=[item[0] for item in _FAILURES],
)
def test_apply_device_never_falls_back(mode, step, exc):
    """A failed POST is terminal: one attempt, no retry, no query/route swap."""
    p = Probe(_http(step) if type(step) is int else step)
    with pytest.raises(exc) as ei:
        p.call(
            mode,
            "apply_device",
            "tiandy",
            "边缘网关·甲",
            device_password="Xy9Secr3t",
            device_username="SentUser42",
            channel_count=0,
            device_code="SYNTH-CODE-01",
        )
    if exc is ProtocolError:
        assert ei.value.code is None
    (req,) = p.biz
    _posted(req, f"{_BASE}/tiandy/deviceLicense/apply")
    assert ("deviceLicense", "Xy9Secr3t") in _form(req)
    assert [e["outcome"] for e in _attempts(p, "apply_device")] == [
        "started",
        "succeeded" if exc is ProtocolError else "failed",
    ]
    assert _calls(p, "apply_device", mode) == ["started", "failed"]


def test_apply_batch_cancelled_is_terminal():
    p = Probe(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        p.call("async", "apply_batch", "tiandy", device_count=3)
    assert len(p.biz) == 1  # attempted once, never retried
    assert [e["outcome"] for e in _attempts(p, "apply_batch")] == ["started", "cancelled"]
    assert _calls(p, "apply_batch", "async") == ["started", "cancelled"]


@pytest.mark.parametrize("mode", _MODES)
def test_foreign_region_rejected_before_any_io(mode):
    p = Probe()
    with pytest.raises(ValueError):
        p.call(
            mode,
            "apply_device",
            "tiandy",
            "边缘网关",
            device_password="Pass1234",
            device_username="User1234",
            region=RegionConfig.overseas(),
        )
    assert p.auth == [] and p.biz == []
