import asyncio
import json
import logging
from urllib.parse import parse_qsl, quote, quote_plus

import httpx
import pytest
from test_sdk_a1_detectors import HOST_SERIAL, ROUTES, invoke, outcomes, payload
from test_sdk_device_utc import TOKEN

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

# invoke(mode,name,*replies,serial=HOST_SERIAL,detector=DETECTOR_SERIAL,region=None) -> (zero-arg call,recorder,seen{"auth":[],"biz":[]}); payload(name,code="200",data=None,message="ok"); outcomes(recorder,mode,"call"|"attempt",name)->list[str].


def _biz_form(request):
    return dict(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("name", ["list", "bindable_cameras", "linked_cameras"])
def test_read_refreshes_once_on_10002(mode, name):
    call, recorder, seen = invoke(mode, name, payload(name, "10002", None), payload(name, data=[]))
    call()
    assert len(seen["auth"]) == 2
    assert len(seen["biz"]) == 2
    assert outcomes(recorder, mode, "attempt", name) == [
        "started",
        "failed",
        "started",
        "succeeded",
    ]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_two_10002_raise_invalid_token(mode):
    replies = (payload("list", "10002", None), payload("list", "10002", None))
    call, _, seen = invoke(mode, "list", *replies)
    with pytest.raises(AuthenticationError) as excinfo:
        call()
    assert excinfo.value.code == "10002"
    assert len(seen["auth"]) == 2
    assert len(seen["biz"]) == 2


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("code", ["200", "10002"])
@pytest.mark.parametrize("name", ROUTES)
def test_wrong_envelope_is_protocol_error(mode, code, name):
    if name == "list":
        wrong = {"code": code, "msg": "x", "data": []}
    else:
        wrong = {"result": {"code": code, "msg": "x", "data": []}}
    call, _, seen = invoke(mode, name, wrong)
    with pytest.raises(ProtocolError):
        call()
    assert len(seen["auth"]) == 1
    assert len(seen["biz"]) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("name", ROUTES)
def test_numeric_code_scalar_is_api_error(mode, name):
    call, _, seen = invoke(mode, name, payload(name, 200, data=[]))
    with pytest.raises(ApiError) as excinfo:
        call()
    assert type(excinfo.value) is ApiError
    assert excinfo.value.code == 200
    assert len(seen["biz"]) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_http_403_maps_typed_permission_denied(mode):
    call, _, seen = invoke(mode, "list", httpx.Response(403, json=payload("list", "20018")))
    with pytest.raises(PermissionDeniedError) as excinfo:
        call()
    assert type(excinfo.value) is PermissionDeniedError
    assert len(seen["biz"]) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_business_20018_is_generic_api_error(mode):
    call, _, seen = invoke(mode, "list", payload("list", "20018"))
    with pytest.raises(ApiError) as excinfo:
        call()
    assert type(excinfo.value) is ApiError
    assert excinfo.value.code == "20018"
    assert len(seen["biz"]) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_read_timeout_is_transport_error(mode):
    call, _, seen = invoke(mode, "list", httpx.ReadTimeout("slow read"))
    with pytest.raises(TransportError) as excinfo:
        call()
    assert excinfo.value.reason == "timeout"
    assert len(seen["biz"]) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("bad", [None, "", "   ", "\ud800"])
def test_invalid_serial_rejected_before_token(mode, bad):
    call, recorder, seen = invoke(mode, "list", payload("list"), serial=bad)
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == []
    assert seen["biz"] == []
    assert outcomes(recorder, mode, "attempt", "list") == []
    assert outcomes(recorder, mode, "call", "list") == ["started", "failed"]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_domestic_region_rejected_before_token(mode):
    region = RegionConfig.china()
    call, recorder, seen = invoke(mode, "list", payload("list"), region=region)
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == []
    assert seen["biz"] == []
    assert outcomes(recorder, mode, "attempt", "list") == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_linked_requires_detector_and_host(mode):
    call, _, seen = invoke(mode, "linked_cameras", payload("linked_cameras"), detector=None)
    with pytest.raises(ValueError):
        call()
    call, _, seen = invoke(mode, "linked_cameras", payload("linked_cameras"), serial="")
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == []
    assert seen["biz"] == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("serial", ["-9", "A B C", f" {HOST_SERIAL} "])
def test_serial_transmitted_unchanged(mode, serial):
    call, _, seen = invoke(mode, "list", payload("list", data=[]), serial=serial)
    call()
    assert _biz_form(seen["biz"][-1])["deviceSerial"] == serial


def test_async_cancelled_is_not_replayed():
    call, recorder, seen = invoke("async", "list", asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        call()
    assert len(seen["biz"]) == 1
    assert outcomes(recorder, "async", "call", "list") == ["started", "cancelled"]
    assert outcomes(recorder, "async", "attempt", "list") == ["started", "cancelled"]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_linked_selector_fragments_not_leaked(mode, caplog):
    serial = "HOSTSEL  /#?&=+% Z1"
    detector = "A1DET  /#?&=+% Q9"
    echo = " ".join(
        [
            serial,
            quote(serial, safe=""),
            quote_plus(serial, safe=""),
            detector,
            quote(detector, safe=""),
            quote_plus(detector, safe=""),
            "https://media.invalid/PRIVATE_URL_TAIL",
        ]
    )
    call, recorder, seen = invoke(
        mode,
        "linked_cameras",
        payload("linked_cameras", "10031", data=None, message=echo),
        serial=serial,
        detector=detector,
    )
    with caplog.at_level(logging.INFO, logger="httpx"):
        with pytest.raises(PermissionDeniedError) as excinfo:
            call()
    assert type(excinfo.value) is PermissionDeniedError
    assert excinfo.value.http_status == 200
    assert len(seen["auth"]) == 1
    assert len(seen["biz"]) == 1
    form = _biz_form(seen["biz"][0])
    assert form["accessToken"] == TOKEN
    assert form["deviceSerial"] == serial
    assert form["detectorSerial"] == detector
    infos = [r for r in caplog.records if r.name == "httpx" and r.levelno == logging.INFO]
    assert infos
    secrets = {
        serial,
        detector,
        quote(serial, safe=""),
        quote_plus(serial, safe=""),
        quote(detector, safe=""),
        quote_plus(detector, safe=""),
        "https://media.invalid/PRIVATE_URL_TAIL",
        TOKEN,
    }
    rendered = str(excinfo.value.message) + str(excinfo.value) + repr(excinfo.value)
    rendered += json.dumps(recorder.report())
    rendered += "".join(record.getMessage() for record in caplog.records)
    for secret in secrets:
        assert secret not in rendered
