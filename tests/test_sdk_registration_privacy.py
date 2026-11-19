import json
import logging
from urllib.parse import quote, quote_plus

import httpx
import pytest
from test_sdk_third_party_protocols import Probe

from ezviz_unified import ApiError, PermissionDeniedError, ResponsePolicy
from ezviz_unified.response import decode_response

MODES = pytest.mark.parametrize("mode", ["sync", "async"])


def _report_text(recorder):
    report = recorder.report()
    return report if isinstance(report, str) else json.dumps(report, ensure_ascii=False)


@MODES
def test_apply_device_denied_redacts_credentials(mode):
    password, username, code = "FakePass9", "FakeUser9", "设备 F/7"
    probe = Probe(
        {
            "meta": {
                "code": 10031,
                "message": f"denied {password} and {username} {quote(code, safe='')}",
            }
        }
    )
    with pytest.raises(PermissionDeniedError) as excinfo:
        probe.call(
            mode,
            "apply_device",
            "tiandy",
            "Example",
            device_password=password,
            device_username=username,
            device_code=code,
        )
    assert len(probe.auth) == len(probe.biz) == 1
    request = probe.biz[0]
    assert not request.url.query
    body = request.content.decode()
    assert "ehicle" not in body
    for value in (password, username, code):
        assert quote_plus(value) in body
    err = excinfo.value
    assert err.code == 10031
    hidden = (
        password,
        username,
        code,
        quote(code, safe=""),
        quote_plus(code),
        quote_plus(password),
        json.dumps(code)[1:-1],
    )
    for rendered in (str(err), repr(err), _report_text(probe.recorder)):
        for marker in hidden:
            assert marker not in rendered


@MODES
def test_apply_batch_denied_redacts_echoed_csv(mode):
    codes, plates = ["C A/1", "C B/2"], ["测A12345", "试B12345"]
    whole = ",".join(codes + plates)
    echo = " ".join(codes + plates + [whole, quote(whole, safe=""), quote_plus(whole)])
    probe = Probe({"meta": {"code": 10029, "message": "fail " + echo}})
    with pytest.raises(ApiError) as excinfo:
        probe.call(
            mode,
            "apply_batch",
            "jt808",
            device_codes=codes,
            vehicle_plates=plates,
            vehicle_colors=[1, 2],
        )
    err = excinfo.value
    request = probe.biz[0]
    assert not request.url.query
    body = request.content.decode()
    positions = [body.index(quote_plus(value)) for value in codes + plates]
    assert positions == sorted(positions)
    markers = (
        codes
        + plates
        + [
            ",".join(codes),
            ",".join(plates),
            whole,
            quote(whole, safe=""),
            quote_plus(whole),
            json.dumps(whole)[1:-1],
        ]
    )
    for rendered in (str(err), str(err.message), repr(err), _report_text(probe.recorder)):
        for marker in markers:
            assert marker not in rendered


@MODES
def test_list_devices_redacts_only_returned_credentials(mode):
    novel = "LIC-新/7-UNIQUE"
    probe = Probe(
        {
            "meta": {"code": 200, "message": "ok " + novel},
            "data": {"data": [{"deviceLicense": novel, "future": True}], "nextIndex": None},
        }
    )
    resp = probe.call(mode, "list_devices", "dhreg")
    assert novel not in resp.message
    assert novel not in repr(resp)
    assert novel not in _report_text(probe.recorder)
    assert novel in resp.raw["meta"]["message"]
    assert resp.raw["data"]["data"][0]["deviceLicense"] == novel
    assert resp.data["data"][0]["deviceLicense"] == novel
    payload = {
        "meta": {"code": novel, "message": "bad " + novel + "\ud800"},
        "data": {"data": [{"deviceLicense": novel}]},
    }
    failing = Probe(
        httpx.Response(
            400,
            content=json.dumps(payload, ensure_ascii=True).encode("ascii"),
            headers={"Content-Type": "application/json"},
        )
    )
    with pytest.raises(ApiError) as excinfo:
        failing.call(mode, "list_devices", "dhreg")
    err = excinfo.value
    assert err.http_status == 400
    for rendered in (str(err.code), str(err), repr(err), _report_text(failing.recorder)):
        assert novel not in rendered


@MODES
def test_httpx_info_reason_owned_vs_unowned(mode, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    probe = Probe(
        httpx.Response(
            200,
            json={
                "meta": {"code": 200},
                "data": {
                    "productKey": "SYNTHPROJECT",
                    "createTime": 7,
                    "dasDomain": "nohost.invalid",
                    "dasPort": 7,
                },
            },
            extensions={"reason_phrase": b"ONLY_REASON_SENTINEL"},
        )
    )
    probe.call(mode, "get_project", "dhreg")
    owned = [
        r.getMessage()
        for r in caplog.records
        if r.name == "httpx"
        and r.levelno == logging.INFO
        and "/api/service/thirdproto/" in r.getMessage()
        and "/project" in r.getMessage()
    ]
    assert len(owned) == 1
    assert probe.biz[0].url.path == "/api/service/thirdproto/dhreg/project"
    assert all("200" in line and "ONLY_REASON_SENTINEL" not in line for line in owned)
    caplog.clear()
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, extensions={"reason_phrase": b"ONLY_REASON_SENTINEL"})
    )
    with httpx.Client(transport=transport) as ordinary:
        ordinary.get("https://ordinary.invalid/free")
    free = [
        r.getMessage()
        for r in caplog.records
        if r.name == "httpx"
        and r.levelno == logging.INFO
        and "ordinary.invalid/free" in r.getMessage()
    ]
    assert free and any("ONLY_REASON_SENTINEL" in line for line in free)


def test_decode_response_registration_redaction_opt_in():
    key = "SYNTH-KEY-乙/3"
    payload = {"meta": {"code": 200, "message": "ok " + key}, "data": {"productKey": key}}
    shared = dict(
        code_path="meta.code", message_path="meta.message", data_path="data", success_values=(200,)
    )
    plain = decode_response(
        payload, policy=ResponsePolicy(**shared), http_status=200, operation="probe"
    )
    clean = decode_response(
        payload,
        policy=ResponsePolicy(redact_registration_fields=True, **shared),
        http_status=200,
        operation="probe",
    )
    assert key in plain.message
    assert key not in clean.message
    assert plain.raw["meta"]["message"] == clean.raw["meta"]["message"] == "ok " + key
    assert plain.data == clean.data == {"productKey": key}
