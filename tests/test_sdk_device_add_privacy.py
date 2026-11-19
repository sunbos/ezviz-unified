"""Privacy regression tests for device-add links operations.

All HTTP traffic is synthetic: Probe wires Client/AsyncClient onto
httpx.MockTransport, and the nested native request uses a second in-process
MockTransport against an .invalid origin.
"""

import asyncio
import json
import logging
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_device_add_links import Probe, reply

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ResponsePolicy,
    decode_response,
)
from ezviz_unified.transport import AsyncTransport, SyncTransport

LINK = "https://example.invalid/a?ticket=SYNTH_LINK_TAIL"
LINK_TAIL = "SYNTH_LINK_TAIL"
NOTE = "syn-note-私-7c"
SERIAL = "SER-syn-55"  # deviceSerial travels as a header: keep it printable ASCII.
CURSOR = "Syn +/私"
CURSOR_FORMS = (CURSOR, "私", "%E7%A7%81", "Syn+%2B%2F", "Syn%20%2B%2F", "Syn+%2B/")

P_CREATE = "/api/service/device/add/tokenUrl"
P_LIST = "/api/service/device/add/tokenUrls"
P_RECORDS = "/api/service/device/add/tokenNote"

ADD_BASE = "https://device-add.invalid"
NESTED_CURSOR = "SynQA+Z/9"
NESTED_PARAMS = {"id": NESTED_CURSOR, "pageSize": 2}
NATIVE_REASON = "NATIVE_REASON_Z7"
SDK_REASON = "SDK_OWNED_REASON_Z7"
ADD_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    success_values=("200",),
    redact_device_add_details=True,
)

UNKNOWN_CODE = "E9-unknown-syn-77"


def _query_str(request):
    query = request.url.query
    return query.decode("utf-8") if isinstance(query, bytes) else query


def _query_params(request):
    return dict(parse_qsl(_query_str(request)))


def _httpx_info_lines(caplog):
    return [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "httpx" and rec.levelno == logging.INFO
    ]


def _owned_lines(caplog, request, path, extra_bad=()):
    """SDK-owned httpx INFO lines for *path*: origin/path visible, no query."""
    origin_path = f"{request.url.scheme}://{request.url.host}{request.url.path}"
    lines = [m for m in _httpx_info_lines(caplog) if path in m]
    assert lines, "expected an SDK-owned httpx INFO line"
    for line in lines:
        assert "GET" in line
        assert origin_path in line
        assert "***" in line
        for bad in ("?", "id=", "pageSize", "deviceSerial") + CURSOR_FORMS + tuple(extra_bad):
            assert bad not in line
    return lines


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "name,pos,kwargs,path,data",
    [
        (
            "create",
            (7,),
            {"note": NOTE},
            P_CREATE,
            {"url": LINK, "note": NOTE, "capability": {"location": "cap-syn-loc"}},
        ),
        (
            "list",
            (),
            {"cursor": CURSOR, "page_size": 4},
            P_LIST,
            [{"id": "r1", "url": LINK, "note": NOTE, "deviceSerial": SERIAL}],
        ),
        (
            "records",
            (),
            {"device_serial": SERIAL},
            P_RECORDS,
            [{"id": 91, "url": LINK, "note": NOTE, "deviceSerial": SERIAL}],
        ),
    ],
    ids=["create", "list", "records"],
)
def test_device_add_links_success_message_masked(mode, name, pos, kwargs, path, data):
    remote_msg = f"echo {NOTE} {LINK} {CURSOR} cap-syn-loc"
    probe = Probe(reply(data, message=remote_msg))
    result = probe.call(mode, name, *pos, **kwargs)
    assert result.http_status == 200
    assert result.code == 200
    assert result.message == "***"
    assert result.data == data
    req = probe.biz[-1]
    assert req.url.path == path
    assert req.headers["accessToken"].startswith("TOK")
    if name == "create":
        assert req.method == "POST"
        assert req.headers["content-type"].startswith("application/json")
        body = json.loads(req.content)
        assert body["expireTime"] == 7 and body["note"] == NOTE
    else:
        assert req.method == "GET"
        params = _query_params(req)
        if name == "list":
            assert params == {"id": CURSOR, "pageSize": "4"}
        else:
            assert req.headers["deviceSerial"] == SERIAL
            assert "deviceSerial" not in params
            assert params.get("pageSize") == "10"
            assert SERIAL not in _query_str(req)
    assert result.raw == reply(data, message=remote_msg)
    assert result.raw["meta"]["message"] == remote_msg
    assert result.raw["data"] == data
    if name == "create":
        assert result.raw["data"]["capability"]["location"] == "cap-syn-loc"
    else:
        row = result.raw["data"][0]
        assert type(row["id"]) is type(data[0]["id"])
        assert row["id"] == data[0]["id"]
        assert row["deviceSerial"] == data[0]["deviceSerial"] == SERIAL
        assert row["url"] == data[0]["url"]
        assert LINK_TAIL in row["url"]
        assert row["note"] == data[0]["note"] == NOTE
    report = json.dumps(probe.recorder.report(), ensure_ascii=False, default=str)
    for text in (repr(result), str(result), report):
        for secret in (NOTE, LINK_TAIL, SERIAL, CURSOR, "cap-syn-loc"):
            assert secret not in text


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("message", ["", None])
def test_device_add_links_empty_message_unmasked(mode, message):
    probe = Probe(reply({"url": LINK}, message=message))
    result = probe.call(mode, "create", 7)
    assert result.message == ""
    assert result.data == {"url": LINK}
    req = probe.biz[-1]
    assert req.method == "POST" and req.url.path == P_CREATE


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_device_add_links_http400_string_code_masked(mode):
    remote_code = f"E4-{NOTE}-{LINK}"
    probe = Probe(httpx.Response(400, json=reply(None, code=remote_code, message=f"deny {NOTE}")))
    with pytest.raises(ApiError) as excinfo:
        probe.call(mode, "create", 5, note=NOTE)
    err = excinfo.value
    assert err.code == "***"
    assert err.message == "***"
    for text in (err.message or "", str(err), repr(err), repr(probe.recorder.report())):
        for secret in (remote_code, NOTE, LINK, LINK_TAIL):
            assert secret not in text


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_device_add_links_int_business_code_preserved(mode):
    probe = Probe(reply(None, code=10031, message=f"deny {NOTE} {LINK}"))
    with pytest.raises(PermissionDeniedError) as excinfo:
        probe.call(mode, "records", device_serial=SERIAL)
    err = excinfo.value
    assert type(err.code) is int and err.code == 10031
    req = probe.biz[-1]
    assert req.method == "GET" and req.url.path == P_RECORDS
    assert req.headers["deviceSerial"] == SERIAL
    params = _query_params(req)
    assert "deviceSerial" not in params and set(params) <= {"id", "pageSize"}
    assert SERIAL not in _query_str(req)
    for text in (err.message or "", str(err), repr(err), repr(probe.recorder.report())):
        for secret in (NOTE, LINK_TAIL, SERIAL):
            assert secret not in text


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_device_add_links_invalid_token_no_replay(mode):
    probe = Probe(reply(None, code="10002", message=f"stale {NOTE} {LINK}"))
    with pytest.raises(AuthenticationError) as excinfo:
        probe.call(mode, "create", 1, note=NOTE)
    err = excinfo.value
    assert err.code == "10002"
    assert err.invalid_token is True
    assert len(probe.biz) == 1
    for text in (err.message or "", str(err), repr(err), repr(probe.recorder.report())):
        for secret in (NOTE, LINK_TAIL):
            assert secret not in text


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_device_add_links_short_cursor_keeps_known_code(mode):
    probe = Probe(reply(None, code="10031", message="x"))
    with pytest.raises(PermissionDeniedError) as excinfo:
        probe.call(mode, "list", cursor="1", page_size=1)
    assert excinfo.value.code == "10031"
    assert _query_params(probe.biz[-1]) == {"id": "1", "pageSize": "1"}


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_device_add_links_get_request_and_owned_log(mode, caplog):
    probe = Probe(reply([{"id": "r1", "url": LINK, "note": NOTE}], message="ok"))
    with caplog.at_level(logging.INFO):
        probe.call(mode, "list", cursor=CURSOR, page_size=2)
    req = probe.biz[-1]
    assert req.method == "GET" and req.url.path == P_LIST
    assert _query_params(req) == {"id": CURSOR, "pageSize": "2"}
    owned = _owned_lines(caplog, req, P_LIST)
    for line in owned:
        assert "200" in line
    for line in _httpx_info_lines(caplog):
        for bad in CURSOR_FORMS:
            assert bad not in line


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "name,kwargs,path,status",
    [
        ("list", {"cursor": CURSOR, "page_size": 2}, P_LIST, 400),
        ("records", {"device_serial": SERIAL, "cursor": CURSOR}, P_RECORDS, 404),
    ],
    ids=["tokenUrls", "tokenNote"],
)
def test_owned_info_line_has_status_without_query_or_reason(
    mode, caplog, name, kwargs, path, status
):
    probe = Probe(
        httpx.Response(
            status,
            json=reply(None, code=str(status), message=f"r {NOTE}"),
            extensions={"reason_phrase": b"ONLY_REMOTE_REASON"},
        )
    )
    with caplog.at_level(logging.INFO):
        with pytest.raises(ApiError) as excinfo:
            probe.call(mode, name, **kwargs)
    assert excinfo.value.code == str(status)
    req = probe.biz[-1]
    assert req.method == "GET" and req.url.path == path
    params = _query_params(req)
    if name == "records":
        assert req.headers["deviceSerial"] == SERIAL
        assert "deviceSerial" not in params and set(params) <= {"id", "pageSize"}
        assert params.get("id") == CURSOR and params.get("pageSize") == "10"
        assert SERIAL not in _query_str(req)
        extra = (SERIAL, "ONLY_REMOTE_REASON", NOTE)
    else:
        assert params == {"id": CURSOR, "pageSize": "2"}
        extra = ("ONLY_REMOTE_REASON", NOTE)
    owned = _owned_lines(caplog, req, path, extra_bad=extra)
    for line in owned:
        assert str(status) in line
    report = repr(probe.recorder.report())
    for text in (excinfo.value.message or "", str(excinfo.value), repr(excinfo.value), report):
        for secret in ("ONLY_REMOTE_REASON", NOTE, LINK_TAIL):
            assert secret not in text


def test_unrelated_native_httpx_info_keeps_query_and_reason(caplog):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                418, json={"e": 1}, extensions={"reason_phrase": b"DIRECT_REASON_ZZ"}
            )
        )
    )
    with caplog.at_level(logging.INFO):
        client.get("https://direct.invalid/x?id=DIRECTID9")
    client.close()
    lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name.startswith("httpx") and rec.levelno == logging.INFO
    ]
    assert any("id=DIRECTID9" in line and "DIRECT_REASON_ZZ" in line for line in lines)


def test_sdk_transport_default_policy_keeps_query_and_reason(caplog):
    policy = ResponsePolicy(
        code_path="meta.code",
        message_path="meta.message",
        success_values=("200",),
    )

    def business(req):
        return httpx.Response(
            200,
            json={"meta": {"code": "200", "message": "ok"}, "data": []},
            extensions={"reason_phrase": b"PLAIN_REASON_ZZ"},
        )

    with caplog.at_level(logging.INFO):
        with SyncTransport(ADD_BASE, transport=httpx.MockTransport(business)) as sdk:
            sdk.request("GET", P_LIST, params={"id": "PLAINQ9", "pageSize": 3}, policy=policy)
    lines = [m for m in _httpx_info_lines(caplog) if P_LIST in m]
    assert any(
        "id=PLAINQ9" in line and "pageSize=3" in line and "PLAIN_REASON_ZZ" in line
        for line in lines
    )


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_nested_native_request_same_text_scoped(caplog, mode):
    """A native GET to the identical URL text inside an active device-add
    policy stays fully visible; only the SDK-owned line is redacted."""
    seen = {}

    def origin(incoming):
        seen["origin_calls"] = seen.get("origin_calls", 0) + 1
        seen["native_in"] = incoming
        return httpx.Response(
            201,
            json={"ok": True},
            extensions={"reason_phrase": NATIVE_REASON.encode()},
        )

    def sdk_reply():
        return httpx.Response(
            200,
            json={"meta": {"code": "200", "message": "owned ok"}, "data": []},
            extensions={"reason_phrase": SDK_REASON.encode()},
        )

    def business_sync(owned):
        seen["owned"] = owned
        with httpx.Client(transport=httpx.MockTransport(origin)) as raw:
            seen["raw"] = raw
            seen["native"] = raw.get(str(owned.url)).request
        return sdk_reply()

    async def business_async(owned):
        seen["owned"] = owned
        async with httpx.AsyncClient(transport=httpx.MockTransport(origin)) as raw:
            seen["raw"] = raw
            seen["native"] = (await raw.get(str(owned.url))).request
        return sdk_reply()

    expected_query = str(httpx.QueryParams(NESTED_PARAMS))
    with caplog.at_level(logging.INFO):
        if mode == "sync":
            with SyncTransport(ADD_BASE, transport=httpx.MockTransport(business_sync)) as sdk:
                resp = sdk.request("GET", P_LIST, params=dict(NESTED_PARAMS), policy=ADD_POLICY)
        else:

            async def runner():
                async with AsyncTransport(
                    ADD_BASE, transport=httpx.MockTransport(business_async)
                ) as sdk:
                    return await sdk.request(
                        "GET", P_LIST, params=dict(NESTED_PARAMS), policy=ADD_POLICY
                    )

            resp = asyncio.run(runner())

    assert resp.code == "200" and resp.data == []
    assert seen["origin_calls"] == 1
    assert seen["raw"].is_closed
    owned_req, native_req = seen["owned"], seen["native"]
    assert native_req is not owned_req and seen["native_in"] is native_req
    assert native_req.url is not owned_req.url
    assert str(native_req.url) == str(owned_req.url)
    assert str(owned_req.url) == f"{ADD_BASE}{P_LIST}?{expected_query}"
    assert _query_params(native_req) == {"id": NESTED_CURSOR, "pageSize": "2"}

    lines = [m for m in _httpx_info_lines(caplog) if P_LIST in m]
    assert len(lines) == 2
    native_lines = [m for m in lines if NATIVE_REASON in m]
    owned_lines = [m for m in lines if "***" in m]
    assert len(native_lines) == 1 and len(owned_lines) == 1
    native_line, owned_line = native_lines[0], owned_lines[0]
    assert f"GET {ADD_BASE}{P_LIST}?{expected_query}" in native_line
    assert "201" in native_line and "***" not in native_line
    assert f"GET {ADD_BASE}{P_LIST}" in owned_line
    assert "200" in owned_line and "201" not in owned_line
    for bad in ("?", "id=", "pageSize", "SynQA", expected_query, NATIVE_REASON, SDK_REASON):
        assert bad not in owned_line
    assert SDK_REASON not in caplog.text


def test_decode_response_unknown_success_code_masked_when_opted_in():
    private_msg = f"hidden {NOTE} {LINK}"
    payload = {
        "code": UNKNOWN_CODE,
        "msg": private_msg,
        "data": {"token": "PRIVTOKEN9", "url": LINK, "note": NOTE},
    }
    policy = ResponsePolicy(success_values=(UNKNOWN_CODE,), redact_device_add_details=True)
    result = decode_response(payload, policy=policy)
    assert result.code == "***"
    assert result.message == "***"
    assert result.data == payload["data"]
    assert result.raw == payload
    assert result.raw["code"] == UNKNOWN_CODE
    assert result.raw["msg"] == private_msg
    shown = repr(result)
    for secret in (UNKNOWN_CODE, private_msg, "PRIVTOKEN9", LINK, NOTE):
        assert secret not in shown


def test_decode_response_unknown_code_opt_out_passthrough():
    private_msg = f"hidden {NOTE} {LINK}"
    policy = ResponsePolicy(success_values=(UNKNOWN_CODE,))
    result = decode_response(
        {"code": UNKNOWN_CODE, "msg": private_msg, "data": {"k": 1}},
        policy=policy,
    )
    assert result.code == UNKNOWN_CODE
    assert result.message == private_msg
    assert result.data == {"k": 1}


def test_decode_response_unrelated_default_passthrough():
    sample = f"remote {NOTE} {LINK} ONLY_REMOTE_REASON"
    result = decode_response(
        {"code": "200", "msg": sample, "data": {"k": NOTE}},
        policy=ResponsePolicy(success_values=("200",)),
    )
    assert result.code == "200"
    assert result.message == sample
    assert result.data == {"k": NOTE}
