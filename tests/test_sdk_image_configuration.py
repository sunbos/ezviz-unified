"""Synthetic wire tests for domestic imaging configuration (1735/1736/3679/3681)."""

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ImageSetting,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
)

AUTH_PAYLOAD = {
    "code": "200",
    "data": {
        "accessToken": "SYNTH_TOKEN",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
GET_MODE_PATH = "/api/v3/device/display/mode/get"
SET_MODE_PATH = "/api/v3/device/display/mode/set"
IMAGE_PARAMS_PATH = "/api/v3/device/video/image/params"
WHITE_BALANCE_PATH = "/api/v3/device/video/white/balance"
MODES = pytest.mark.parametrize("mode", ["sync", "async"])
FIELD = {"gamma_correction": "gammaCorrection"}
MANUAL = dict(
    image_style="manual",
    gamma_correction=1,
    gain=2,
    brightness=3,
    contrast=4,
    saturation=5,
    sharpness=6,
)
IMAGE_KW = dict(image_style="standard", gamma_correction=1, gain=1)
IMAGE_REJECTS = (
    [
        {**MANUAL, "image_style": b}
        for b in (
            "saturation",
            "manul",
            "saturatio",
            "bogus",
            "",
            "STANDARD",
            1,
            1.5,
            True,
            None,
            ["standard"],
        )
    ]
    + [
        {**MANUAL, f: b}
        for f, b in (
            ("gamma_correction", True),
            ("gamma_correction", "1"),
            ("gamma_correction", 1.5),
            ("gamma_correction", -1),
            ("gamma_correction", 10),
            ("gamma_correction", 255),
            ("gain", True),
            ("gain", "1"),
            ("gain", 1.5),
            ("gain", -1),
            ("gain", 101),
            ("gain", 255),
            ("brightness", True),
            ("brightness", "0"),
            ("brightness", -1),
            ("brightness", 101),
            ("brightness", 255),
            ("contrast", True),
            ("contrast", "0"),
            ("contrast", -1),
            ("contrast", 101),
            ("contrast", 255),
            ("saturation", True),
            ("saturation", "0"),
            ("saturation", -1),
            ("saturation", 101),
            ("saturation", 255),
            ("sharpness", True),
            ("sharpness", "0"),
            ("sharpness", -1),
            ("sharpness", 101),
            ("sharpness", 255),
            ("sharpness", 999),
        )
    ]
    + [{**MANUAL, f: None} for f in ("brightness", "contrast", "saturation", "sharpness")]
)
IMAGE_MISSING_KWARGS = [
    {"image_style": "standard", "gain": 1},
    {"image_style": "standard", "gamma_correction": 1},
]
WB_REJECTS = [
    ("manul", {}),
    ("MANUAL", {}),
    ("", {}),
    (1, {}),
    (True, {}),
    (None, {}),
    ("manual", {}),
    ("manual", {"red": 0}),
    ("manual", {"blue": 0}),
    ("manual", {"red": None, "blue": 0}),
    ("manual", {"red": 0, "blue": None}),
    ("manual", {"red": True, "blue": 0}),
    ("manual", {"red": "0", "blue": 0}),
    ("manual", {"red": 256, "blue": 0}),
    ("manual", {"red": 0, "blue": -1}),
    ("auto", {"red": True}),
    ("lock", {"red": 999}),
]
WRITES = [
    (
        "set_display_mode",
        ("SER-1735", "1"),
        {},
        "imaging.set_display_mode",
        "imaging.display_mode.set",
    ),
    (
        "set_image_params",
        ("SER-3679",),
        IMAGE_KW,
        "imaging.set_image_params",
        "imaging.image_params.set",
    ),
    (
        "set_white_balance",
        ("SER-3681", "auto"),
        {},
        "imaging.set_white_balance",
        "imaging.white_balance.set",
    ),
]
READS = [
    ("get_display_mode", ("SER-1736",), {}, "imaging.get_display_mode", "imaging.display_mode.get"),
    (
        "get",
        ("SER-LEGACY", ImageSetting.IMAGE_PARAMS),
        {},
        "imaging.get",
        "imaging.image_params.get",
    ),
    (
        "get",
        ("SER-LEGACY", ImageSetting.WHITE_BALANCE),
        {},
        "imaging.get",
        "imaging.white_balance.get",
    ),
]
LEGACY_IMAGE_PARAMS_ACK = {
    "meta": {"code": 200, "message": "ok", "moreInfo": None},
    "data": {"saturatio": 66, "manul": "on", "extra": [1]},
}
LEGACY_WHITE_BALANCE_ACK = {
    "meta": {"code": 200, "message": "ok", "moreInfo": None},
    "data": {"mode": "manul", "extra": {"u": 1}},
}
EXPIRED = [
    {"code": "10002", "msg": "expired"},
    {"meta": {"code": 10002, "message": "expired", "moreInfo": None}},
    {"meta": {"code": 10002, "message": "expired", "moreInfo": None}},
]
DENIED = [
    {"code": "10031", "msg": "denied"},
    {"meta": {"code": 10031, "message": "denied", "moreInfo": None}},
    {"meta": {"code": 10031, "message": "denied", "moreInfo": None}},
]
BUSINESS_CODE_CASES = [
    ("set_display_mode", ("SER-1735", "1"), {}, {"code": 200, "msg": "ok"}, 200),
    (
        "get_display_mode",
        ("SER-1736",),
        {},
        {"code": 200, "msg": "ok", "data": {"valueInfo": {"mode": 1}}},
        200,
    ),
    (
        "set_image_params",
        ("SER-3679",),
        IMAGE_KW,
        {"meta": {"code": "200", "message": "ok"}},
        "200",
    ),
    (
        "set_white_balance",
        ("SER-3681", "auto"),
        {},
        {"meta": {"code": "200", "message": "ok"}},
        "200",
    ),
]
PROTOCOL_CODE_CASES = [
    ("set_display_mode", ("SER-1735", "1"), {}, {"msg": "ok"}),
    ("set_display_mode", ("SER-1735", "1"), {}, {"code": 200.0, "msg": "ok"}),
    (
        "get_display_mode",
        ("SER-1736",),
        {},
        {"code": "200", "msg": "ok", "data": {"valueInfo": {"mode": True}}},
    ),
    ("set_image_params", ("SER-3679",), IMAGE_KW, {"meta": {"message": "ok"}}),
    ("set_image_params", ("SER-3679",), IMAGE_KW, {"meta": {"code": 200.0, "message": "ok"}}),
    ("set_white_balance", ("SER-3681", "auto"), {}, {"meta": {"code": 200.0, "message": "ok"}}),
    ("set_white_balance", ("SER-3681", "auto"), {}, {"code": "200", "msg": "ok"}),
]


def _build(mode, biz, region=None):
    rec = DiagnosticRecorder()
    wire = {"auth": [], "biz": []}

    def auth(request):
        wire["auth"].append(request)
        return httpx.Response(200, json=AUTH_PAYLOAD)

    def business(request):
        wire["biz"].append(request)
        return biz(request)

    cls = Client if mode == "sync" else AsyncClient
    client = cls(
        "mock-key",
        "mock-secret",
        region=region or RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(business),
        diagnostic_recorder=rec,
    )
    return client, wire, rec


def _invoke(mode, client, name, *args, **kwargs):
    if mode == "sync":
        with client as opened:
            return getattr(opened.imaging, name)(*args, **kwargs)

    async def main():
        async with client as opened:
            return await getattr(opened.imaging, name)(*args, **kwargs)

    return asyncio.run(main())


def _events(rec, entrypoint, mode, *, kind="call"):
    return [
        e
        for e in rec.report()["events"]
        if e.get("kind") == kind and e.get("entrypoint") == entrypoint and e.get("mode") == mode
    ]


def _assert_attempts(rec, mode, operation):
    attempts = [
        e
        for e in _events(rec, "client.request", mode, kind="attempt")
        if e["operation"] == operation
    ]
    assert len(attempts) == 2
    assert attempts[0]["outcome"] == "started" and attempts[1]["outcome"] in ("succeeded", "failed")
    assert attempts[0]["span_id"] == attempts[1]["span_id"]


def _diag(rec, entrypoint, mode, outcomes, operation, *forbidden):
    events = _events(rec, entrypoint, mode)
    assert [e["outcome"] for e in events] == outcomes
    # Public call scopes omit the concrete Operation by convention; the exact
    # operation is proven on the matching attempt events instead.
    assert {e.get("operation") for e in events} == {"other"}
    _assert_attempts(rec, mode, operation)
    blob = json.dumps(rec.report(), ensure_ascii=False)
    for token in forbidden:
        assert token not in blob and json.dumps(token, ensure_ascii=False)[1:-1] not in blob


def _diag_failure(rec, entrypoint, mode, operation):
    assert [e["outcome"] for e in _events(rec, entrypoint, mode)] == ["started", "failed"]
    _assert_attempts(rec, mode, operation)


def _form(request):
    return parse_qs(request.content.decode("utf-8"))


@MODES
@pytest.mark.parametrize("value", ["1", "2", "3"])
def test_1735_posts_header_token_serial_and_mode_only_form(mode, value):
    payload = {"code": "200", "msg": "ok"}
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=payload))
    resp = _invoke(mode, client, "set_display_mode", "SER-1735", value)
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    req = wire["biz"][0]
    assert req.method == "POST" and req.url.path == SET_MODE_PATH and req.url.query == b""
    assert req.headers["accessToken"] == "SYNTH_TOKEN" and req.headers["deviceSerial"] == "SER-1735"
    assert _form(req) == {"mode": [value]}
    assert resp.code == "200" and resp.message == "ok" and resp.raw == payload
    assert resp.operation == "imaging.display_mode.set"
    _diag(
        rec,
        "imaging.set_display_mode",
        mode,
        ["started", "succeeded"],
        "imaging.display_mode.set",
        "mock-secret",
        "SER-1735",
    )


@MODES
@pytest.mark.parametrize(
    "serial,bad",
    [
        ("SER-1735", 1),
        ("SER-1735", True),
        ("SER-1735", "manual"),
        ("SER-1735", "0"),
        ("SER-1735", "4"),
        ("SER-1735", ""),
        ("SER-1735", None),
        ("SER-1735", 1.5),
        ("SER\r\nX", "1"),
        ("SER\tX", "1"),
        ("SER中", "1"),
        (" SER", "1"),
        ("SER ", "1"),
        ("SER\x7f", "1"),
        ("", "1"),
        ("   ", "1"),
    ],
)
def test_1735_rejects_bad_mode_or_header_serial(mode, serial, bad):
    client, wire, _ = _build(mode, lambda req: httpx.Response(500))
    with pytest.raises(ValueError):
        _invoke(mode, client, "set_display_mode", serial, bad)
    assert wire == {"auth": [], "biz": []}


@MODES
@pytest.mark.parametrize(
    "name,args,kwargs",
    [
        ("set_display_mode", ("SER-1735", "1"), {}),
        ("get_display_mode", ("SER-1736",), {}),
        ("set_image_params", ("SER-3679",), IMAGE_KW),
        ("set_white_balance", ("SER-3681", "auto"), {}),
        ("get", ("SER-LEGACY", ImageSetting.IMAGE_PARAMS), {}),
        ("get", ("SER-LEGACY", ImageSetting.WHITE_BALANCE), {}),
    ],
)
def test_rejects_overseas_region_before_auth(mode, name, args, kwargs):
    client, wire, _ = _build(mode, lambda req: httpx.Response(500), region=RegionConfig.overseas())
    with pytest.raises(ValueError):
        _invoke(mode, client, name, *args, **kwargs)
    assert wire == {"auth": [], "biz": []}


@MODES
@pytest.mark.parametrize("value", [1, "2", "", 7, "vivid"])
def test_1736_get_header_only_passthrough_unknowns(mode, value):
    payload = {
        "code": "200",
        "msg": "ok",
        "data": {"valueInfo": {"mode": value, "canary": "RAW-CANARY"}, "extra": {"u": 1}},
    }
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=payload))
    resp = _invoke(mode, client, "get_display_mode", "SER-1736")
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    req = wire["biz"][0]
    assert req.method == "GET" and req.url.path == GET_MODE_PATH
    assert req.content == b"" and req.url.query == b""
    assert req.headers["accessToken"] == "SYNTH_TOKEN" and req.headers["deviceSerial"] == "SER-1736"
    assert resp.code == "200" and resp.message == "ok" and resp.raw == payload
    assert resp.data["valueInfo"]["mode"] == value and type(resp.data["valueInfo"]["mode"]) is type(
        value
    )
    assert resp.data["extra"] == {"u": 1} and resp.data["valueInfo"]["canary"] == "RAW-CANARY"
    assert resp.operation == "imaging.display_mode.get" and "imaging.display_mode.get" in repr(resp)
    _diag(
        rec,
        "imaging.get_display_mode",
        mode,
        ["started", "succeeded"],
        "imaging.display_mode.get",
        "mock-secret",
        "SER-1736",
        "RAW-CANARY",
    )


@MODES
@pytest.mark.parametrize(
    "data",
    [
        {"valueInfo": {"mode": True}},
        {"valueInfo": {"mode": 1.5}},
        {"valueInfo": {"mode": ["1"]}},
        {"valueInfo": {"mode": {"x": 1}}},
        {"valueInfo": {"mode": None}},
        {"valueInfo": {}},
        {"valueInfo": "manual"},
        {},
        {"valueInfo": None},
        {"noValueInfo": {}},
    ],
)
def test_1736_rejects_bad_value_info(mode, data):
    payload = {"code": "200", "msg": "ok", "data": data}
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(ProtocolError):
        _invoke(mode, client, "get_display_mode", "SER-1736")
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    assert [e["outcome"] for e in _events(rec, "imaging.get_display_mode", mode)] == [
        "started",
        "failed",
    ]


@MODES
@pytest.mark.parametrize(
    "style,kwargs",
    [
        ("standard", {"gamma_correction": 0, "gain": 100}),
        ("soft", {"gamma_correction": 1, "gain": 2, "brightness": 0}),
        ("gorgeous", {"gamma_correction": 1, "gain": 2, "saturation": 0}),
        (
            "manual",
            {
                "gamma_correction": 9,
                "gain": 0,
                "brightness": 0,
                "contrast": 100,
                "saturation": 0,
                "sharpness": 100,
            },
        ),
    ],
)
@pytest.mark.parametrize(
    "ack",
    [
        {"meta": {"code": 200, "message": "ok", "moreInfo": None}},
        {
            "meta": {
                "code": 200,
                "message": "ok",
                "moreInfo": {"ticket": "T-1", "canary": "RAW-CANARY"},
            }
        },
    ],
)
def test_3679_put_form_token_header_only_and_meta_ack(mode, style, kwargs, ack):
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=ack))
    serial = "SER-中3679"
    resp = _invoke(mode, client, "set_image_params", serial, image_style=style, **kwargs)
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    req = wire["biz"][0]
    assert req.method == "PUT" and req.url.path == IMAGE_PARAMS_PATH and req.url.query == b""
    assert req.headers["accessToken"] == "SYNTH_TOKEN" and "deviceSerial" not in req.headers
    expected = {"deviceSerial": [serial], "imageStyle": [style]}
    expected.update({FIELD.get(k, k): [str(v)] for k, v in kwargs.items()})
    assert _form(req) == expected
    body = req.content.decode("utf-8")
    assert ("saturation" in body) == ("saturation" in kwargs) and "saturatio=" not in body
    assert resp.code == 200 and resp.message == "ok" and resp.raw == ack
    assert (
        resp.data is None
        and "data" not in resp.raw
        and resp.operation == "imaging.image_params.set"
    )
    _diag(
        rec,
        "imaging.set_image_params",
        mode,
        ["started", "succeeded"],
        "imaging.image_params.set",
        "mock-secret",
        serial,
        "RAW-CANARY",
    )


@MODES
@pytest.mark.parametrize("kwargs", IMAGE_REJECTS)
def test_3679_rejects_bad_style_scalars_and_missing_manual_fields(mode, kwargs):
    client, wire, _ = _build(mode, lambda req: httpx.Response(500))
    with pytest.raises(ValueError):
        _invoke(mode, client, "set_image_params", "SER-3679", **kwargs)
    assert wire == {"auth": [], "biz": []}


@MODES
@pytest.mark.parametrize("kwargs", IMAGE_MISSING_KWARGS)
def test_3679_missing_required_kwarg_is_type_error(mode, kwargs):
    client, wire, _ = _build(mode, lambda req: httpx.Response(500))
    with pytest.raises(TypeError):
        _invoke(mode, client, "set_image_params", "SER-3679", **kwargs)
    assert wire == {"auth": [], "biz": []}


@MODES
@pytest.mark.parametrize(
    "field,bad,kwargs",
    [
        ("gamma_correction", 10, {"gain": 1}),
        ("gamma_correction", -1, {"gain": 1}),
        ("gain", 101, {"gamma_correction": 1}),
        ("gain", -1, {"gamma_correction": 1}),
    ],
)
def test_3679_rejects_boundary_values(mode, field, bad, kwargs):
    client, wire, _ = _build(mode, lambda req: httpx.Response(500))
    with pytest.raises(ValueError):
        _invoke(
            mode,
            client,
            "set_image_params",
            "SER-3679",
            image_style="standard",
            **{field: bad},
            **kwargs,
        )
    assert wire == {"auth": [], "biz": []}


@MODES
@pytest.mark.parametrize("serial", ["", "   ", "SER\udcffX"])
@pytest.mark.parametrize(
    "name,args,kwargs", [("set_image_params", (), IMAGE_KW), ("set_white_balance", ("auto",), {})]
)
def test_put_rejects_bad_serial_before_auth(mode, serial, name, args, kwargs):
    client, wire, _ = _build(mode, lambda req: httpx.Response(500))
    with pytest.raises(ValueError):
        _invoke(mode, client, name, serial, *args, **kwargs)
    assert wire == {"auth": [], "biz": []}


@MODES
@pytest.mark.parametrize(
    "serial",
    ["SER\r\nX", "SER\tX", "SER中", " SER", "SER ", "SER\x7f", "", "   ", "SER\udcffX", 1, None],
)
@pytest.mark.parametrize("name,args", [("set_display_mode", ("1",)), ("get_display_mode", ())])
def test_display_rejects_bad_header_serial_before_auth(mode, serial, name, args):
    client, wire, _ = _build(mode, lambda req: httpx.Response(500))
    with pytest.raises(ValueError):
        _invoke(mode, client, name, serial, *args)
    assert wire == {"auth": [], "biz": []}


@MODES
@pytest.mark.parametrize(
    "wb_mode,kwargs,extras",
    [
        ("auto", {}, {}),
        ("auto", {"red": 0, "blue": 255}, {"whiteBalanceRed": ["0"], "whiteBalanceBlue": ["255"]}),
        ("lock", {}, {}),
        ("lock", {"red": 0, "blue": 255}, {"whiteBalanceRed": ["0"], "whiteBalanceBlue": ["255"]}),
        (
            "manual",
            {"red": 0, "blue": 255},
            {"whiteBalanceRed": ["0"], "whiteBalanceBlue": ["255"]},
        ),
    ],
)
def test_3681_put_form_and_meta_ack(mode, wb_mode, kwargs, extras):
    ack = {"meta": {"code": 200, "message": "ok", "moreInfo": {"ticket": "T-2"}}}
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=ack))
    serial = "SER-中3681"
    resp = _invoke(mode, client, "set_white_balance", serial, wb_mode, **kwargs)
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    req = wire["biz"][0]
    assert req.method == "PUT" and req.url.path == WHITE_BALANCE_PATH and req.url.query == b""
    assert req.headers["accessToken"] == "SYNTH_TOKEN" and "deviceSerial" not in req.headers
    assert _form(req) == {"deviceSerial": [serial], "mode": [wb_mode], **extras}
    body = req.content.decode("utf-8")
    assert "red=" not in body and "blue=" not in body
    assert resp.code == 200 and resp.message == "ok" and resp.raw == ack
    assert resp.data is None and resp.operation == "imaging.white_balance.set"
    _diag(
        rec,
        "imaging.set_white_balance",
        mode,
        ["started", "succeeded"],
        "imaging.white_balance.set",
        "mock-secret",
        serial,
    )


@MODES
@pytest.mark.parametrize("wb_mode,kwargs", WB_REJECTS)
def test_3681_rejects(mode, wb_mode, kwargs):
    client, wire, _ = _build(mode, lambda req: httpx.Response(500))
    with pytest.raises(ValueError):
        _invoke(mode, client, "set_white_balance", "SER-3681", wb_mode, **kwargs)
    assert wire == {"auth": [], "biz": []}


@MODES
@pytest.mark.parametrize("write,payload", list(zip(WRITES, EXPIRED)))
def test_10002_raises_authentication_error_without_retry(mode, write, payload):
    name, args, kwargs, entrypoint, operation = write
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(AuthenticationError) as caught:
        _invoke(mode, client, name, *args, **kwargs)
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    expected_code = "10002" if name == "set_display_mode" else 10002
    assert caught.value.code == expected_code and caught.value.operation == operation
    assert not [e for e in rec.report()["events"] if e.get("kind") == "retry"]
    _diag(rec, entrypoint, mode, ["started", "failed"], operation, "mock-secret")


@MODES
@pytest.mark.parametrize("name,args,kwargs,payload,expected_code", BUSINESS_CODE_CASES)
def test_scalar_nonmatching_code_is_ordinary_api_error(
    mode, name, args, kwargs, payload, expected_code
):
    _, _, _, _, operation = next(w for w in WRITES + READS if w[0] == name)
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(ApiError) as caught:
        _invoke(mode, client, name, *args, **kwargs)
    error = caught.value
    assert type(error) is ApiError
    assert error.code == expected_code and error.operation == operation
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    _diag_failure(rec, next(w[3] for w in WRITES + READS if w[0] == name), mode, operation)


@MODES
@pytest.mark.parametrize("name,args,kwargs,payload", PROTOCOL_CODE_CASES)
def test_missing_or_nonscalar_code_is_protocol_error(mode, name, args, kwargs, payload):
    _, _, _, entrypoint, operation = next(w for w in WRITES + READS if w[0] == name)
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(ProtocolError):
        _invoke(mode, client, name, *args, **kwargs)
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    _diag_failure(rec, entrypoint, mode, operation)


@MODES
@pytest.mark.parametrize("write,payload", list(zip(WRITES, DENIED)))
def test_10031_raises_permission_denied(mode, write, payload):
    name, args, kwargs, entrypoint, operation = write
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(PermissionDeniedError) as caught:
        _invoke(mode, client, name, *args, **kwargs)
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    expected_code = "10031" if name == "set_display_mode" else 10031
    assert caught.value.code == expected_code and caught.value.operation == operation
    _diag(rec, entrypoint, mode, ["started", "failed"], operation, "mock-secret")


@MODES
@pytest.mark.parametrize(
    "setting,payload,path",
    [
        (ImageSetting.IMAGE_PARAMS, LEGACY_IMAGE_PARAMS_ACK, IMAGE_PARAMS_PATH),
        (ImageSetting.WHITE_BALANCE, LEGACY_WHITE_BALANCE_ACK, WHITE_BALANCE_PATH),
    ],
)
def test_legacy_imaging_get_query_only_never_write_form(mode, setting, payload, path):
    client, wire, rec = _build(mode, lambda req: httpx.Response(200, json=payload))
    resp = _invoke(mode, client, "get", "SER-LEGACY", setting)
    assert len(wire["auth"]) == 1 and len(wire["biz"]) == 1
    req = wire["biz"][0]
    assert req.method == "GET" and req.url.path == path
    assert req.content == b"" and req.headers.get("deviceSerial") is None
    assert parse_qs(req.url.query.decode("utf-8")) == {"deviceSerial": ["SER-LEGACY"]}
    assert req.headers["accessToken"] == "SYNTH_TOKEN"
    assert resp.code == 200 and resp.raw == payload and resp.data == payload["data"]
    assert resp.operation == f"imaging.{setting.value}.get"
    if setting is ImageSetting.IMAGE_PARAMS:
        assert resp.raw["data"]["saturatio"] == 66 and "saturation" not in resp.raw["data"]
        assert resp.raw["data"]["manul"] == "on"
    else:
        assert resp.raw["data"]["mode"] == "manul"
    _diag(
        rec,
        "imaging.get",
        mode,
        ["started", "succeeded"],
        f"imaging.{setting.value}.get",
        "mock-secret",
        "SER-LEGACY",
    )
