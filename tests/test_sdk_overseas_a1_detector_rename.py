"""Offline wire and response-policy coverage for the overseas A1 rename endpoint."""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_a1_detectors import API_HOST, AUTH_HOST, TOKEN, _client, outcomes
from test_sdk_a1_detectors import invoke as invoke_read
from test_sdk_client import close, resolve

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    ResponsePolicy,
    TransportError,
    decode_response,
)

OP = "rename"
PATH = "/api/lapp/detector/name/change"
PARENT = " 001Parent私 +PARENT_PRIVATE&/ "
CHILD = "002child 子+CHILD_PRIVATE&/ "
NAME = "  New 名+NAME_PRIVATE&/ "
ACK = {"code": "200", "msg": "UPSTREAM_PRIVATE"}
MISS = object()


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def rename(mode, client, *args, **kwargs):
    if mode == "async":

        async def go():
            async with client:
                return await client.detectors.rename(*args, **kwargs)

        return asyncio.run(go())
    with client:
        return client.detectors.rename(*args, **kwargs)


def invoke(mode, *replies, args=(PARENT, CHILD), kwargs=None, region=None):
    steps = iter(replies)

    def business(request):
        step = next(steps)
        if isinstance(step, BaseException):
            raise step
        return step if isinstance(step, httpx.Response) else httpx.Response(200, json=step)

    client, recorder, seen = _client(mode, biz=business, region=region)
    return (
        lambda: rename(mode, client, *args, **({"new_name": NAME} if kwargs is None else kwargs)),
        recorder,
        seen,
    )


@pytest.mark.parametrize(
    "parent,child,name",
    [
        (PARENT, CHILD, NAME),
        ("0001", "0002", "a" * 49),
        (" P1 ", " C2 ", "界" * 49),
        ("MiXeD", "zero-01", " +%= &/新名 "),
    ],
)
def test_wire(mode, parent, child, name):
    call, rec, seen = invoke(mode, ACK, args=(parent, child), kwargs={"new_name": name})
    result = call()
    assert (
        result.code == "200"
        and result.raw == ACK
        and result.data is None
        and result.message == "***"
    )
    (auth,), (req,) = seen["auth"], seen["biz"]
    assert auth.url.scheme == req.url.scheme == "https"
    assert auth.url.host == AUTH_HOST and req.url.host == API_HOST and AUTH_HOST != API_HOST
    assert auth.url.path == "/api/lapp/token/get" and auth.method == "POST"
    assert req.url.path == PATH and req.method == "POST" and not req.url.query
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert set(req.headers).isdisjoint(
        {"accesstoken", "deviceserial", "detectorserial", "newname", "authorization"}
    )
    assert sorted(parse_qsl(req.content.decode(), keep_blank_values=True)) == sorted(
        [
            ("accessToken", TOKEN),
            ("deviceSerial", parent),
            ("detectorSerial", child),
            ("newName", name),
        ]
    )


@pytest.mark.parametrize("data", [MISS, None, {}, {"backend": "DATA_PRIVATE", "nested": [None, 1]}])
def test_ack(mode, data):
    body = {**ACK, "result": {"code": "10002", "data": "wrong"}, "future": {"value": 0}}
    if data is not MISS:
        body["data"] = data
    call, _, seen = invoke(mode, body)
    resp = call()
    assert resp.raw == body and resp.code == "200" and resp.data == (None if data is MISS else data)
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize(
    "body,error",
    [
        ({"result": {"code": "200"}}, ProtocolError),
        ({"meta": {"code": 200}}, ProtocolError),
        ({"code": 200}, ApiError),
        ({"code": True}, ProtocolError),
        ({"code": 200.0}, ProtocolError),
    ],
)
def test_reject_bad_envelope(mode, body, error):
    call, _, seen = invoke(mode, body)
    with pytest.raises(error) as exc:
        call()
    assert type(exc.value) is error and len(seen["auth"]) == len(seen["biz"]) == 1


def test_region_precedes_inputs(mode):
    for args, kwargs in [
        ((PARENT, CHILD), {"new_name": NAME}),
        ((object(), object()), {"new_name": object()}),
    ]:
        call, r, seen = invoke(mode, region=RegionConfig.china(), args=args, kwargs=kwargs)
        with pytest.raises(ValueError, match="only supported in the overseas region"):
            call()
        assert seen == {"auth": [], "biz": []} and outcomes(r, mode, "attempt", OP) == []
        assert outcomes(r, mode, "call", OP) == ["started", "failed"]


@pytest.mark.parametrize("which", ["parent", "child"])
@pytest.mark.parametrize("bad", [None, 7, b"id", "", "   ", "\ud800"])
def test_bad_id(mode, which, bad):
    args = (bad, CHILD) if which == "parent" else (PARENT, bad)
    call, r, seen = invoke(mode, args=args)
    with pytest.raises(ValueError):
        call()
    assert seen == {"auth": [], "biz": []} and outcomes(r, mode, "attempt", OP) == []


BAD_NAMES = [
    None,
    7,
    4.2,
    b"A1-detector",
    "",
    " ",
    "   ",
    " " * 49,
    "A" * 50,
    chr(0x754C) * 50,
    "\ud800",
    "\udfff",
] + ["x" + chr(c) + "y" for c in list(range(32)) + [127]]


@pytest.mark.parametrize("bad", BAD_NAMES)
def test_bad_name(mode, bad):
    call, r, seen = invoke(mode, kwargs={"new_name": bad})
    with pytest.raises(ValueError):
        call()
    assert seen == {"auth": [], "biz": []} and outcomes(r, mode, "attempt", OP) == []


@pytest.mark.parametrize(
    "args,kw",
    [
        ((PARENT, CHILD), {}),
        ((PARENT, CHILD, NAME), {}),
        ((PARENT,), {"new_name": NAME}),
        ((PARENT, CHILD), {"name": NAME}),
    ],
)
def test_signature(mode, args, kw):
    call, _, seen = invoke(mode, args=args, kwargs=kw)
    with pytest.raises(TypeError):
        call()
    assert seen == {"auth": [], "biz": []}


def test_timeout(mode):
    call, rec, seen = invoke(mode, httpx.ReadTimeout("PRIVATE_TIMEOUT"))
    with pytest.raises(TransportError) as exc:
        call()
    assert exc.value.reason == "timeout" and "PRIVATE_TIMEOUT" not in str(exc.value)
    assert len(seen["auth"]) == len(seen["biz"]) == 1 and not outcomes(rec, mode, "retry", OP)


def test_cancel():
    call, rec, seen = invoke("async", asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert outcomes(rec, "async", "call", OP) == ["started", "cancelled"]
    assert outcomes(rec, "async", "attempt", OP) == ["started", "cancelled"]


def test_closed(mode):
    async def scenario():
        client, _, seen = _client(mode, biz=lambda req: pytest.fail("unexpected business call"))
        await close(client)
        await close(client)
        with pytest.raises(TransportError, match="closed"):
            await resolve(client.detectors.rename(PARENT, CHILD, new_name=NAME))
        assert seen == {"auth": [], "biz": []}

    asyncio.run(scenario())


DOCUMENTED_ERRORS = (
    "10001",
    "10002",
    "10005",
    "20002",
    "20006",
    "20007",
    "20008",
    "20014",
    "20018",
    "49999",
)
COMMON_ERRORS = ("400", "403", "404", "500", "10031")


@pytest.mark.parametrize(
    "code", [typed(v) for v in (*DOCUMENTED_ERRORS, *COMMON_ERRORS) for typed in (str, int)] + [200]
)
def test_known_business_codes(mode, code):
    call, r, seen = invoke(mode, {"code": code, "msg": "PRIVATE_MESSAGE"})
    expected = (
        AuthenticationError
        if str(code) == "10002"
        else PermissionDeniedError
        if str(code) == "10031"
        else ApiError
    )
    with pytest.raises(expected) as exc:
        call()
    err = exc.value
    assert type(err) is expected and err.http_status == 200 and err.message == "***"
    assert err.code == code and type(err.code) is type(code)
    assert len(seen["auth"]) == len(seen["biz"]) == 1 and not outcomes(r, mode, "retry", OP)
    for event in r.report()["events"]:
        assert "code" not in event and "business_code" not in event


@pytest.mark.parametrize(
    "http_status,code,expected",
    [
        (403, MISS, PermissionDeniedError),
        (401, MISS, AuthenticationError),
        (503, MISS, ApiError),
        (403, "PRIVATE_CODE", PermissionDeniedError),
        (401, 987654321, AuthenticationError),
        (200, "PRIVATE_CODE", ApiError),
        (200, 987654321, ApiError),
        (503, 987654321, ApiError),
        (200, "60022", ApiError),
        (200, 60022, ApiError),
    ],
)
def test_redacted_failure(mode, http_status, code, expected, caplog):
    body = {
        "msg": "PRIVATE_MESSAGE " + PARENT + CHILD + NAME + " " + TOKEN,
        "data": {"name": "ECHO_PRIVATE"},
    }
    if code is not MISS:
        body["code"] = code
    wire = httpx.Response(http_status, json=body, extensions={"reason_phrase": b"PRIVATE_REASON"})
    call, r, seen = invoke(mode, wire)
    with caplog.at_level("INFO", logger="httpx"), pytest.raises(expected) as exc:
        call()
    err = exc.value
    assert type(err) is expected and err.http_status == http_status and err.message == "***"
    assert err.code == (None if code is MISS else "***")
    assert len(seen["auth"]) == len(seen["biz"]) == 1 and not outcomes(r, mode, "retry", OP)
    hits = [
        record.getMessage()
        for record in caplog.records
        if record.name == "httpx" and PATH in record.getMessage()
    ]
    assert len(hits) == 1 and "?" not in hits[0]
    blob = " ".join((str(err), repr(err), repr(r.report()), caplog.text))
    for marker in (
        TOKEN,
        "PARENT_PRIVATE",
        "CHILD_PRIVATE",
        "NAME_PRIVATE",
        "ECHO_PRIVATE",
        "PRIVATE_MESSAGE",
        "PRIVATE_CODE",
        "987654321",
        "60022",
        "PRIVATE_REASON",
    ):
        assert marker not in blob


def test_sensitive_success(mode, caplog):
    body = {**ACK, "data": {"private": "ECHO_PRIVATE"}}
    wire = httpx.Response(200, json=body, extensions={"reason_phrase": b"PRIVATE_REASON"})
    call, r, _ = invoke(mode, wire)
    with caplog.at_level("INFO", logger="httpx"):
        result = call()
    assert result.raw == body and result.data == body["data"] and result.message == "***"
    shown = repr(result) + " " + str(result) + " " + repr(r.report()) + " " + caplog.text
    for marker in (
        TOKEN,
        "PARENT_PRIVATE",
        "CHILD_PRIVATE",
        "NAME_PRIVATE",
        "ECHO_PRIVATE",
        "UPSTREAM_PRIVATE",
        "PRIVATE_REASON",
    ):
        assert marker not in shown


@pytest.mark.parametrize("name", ["list", "bindable_cameras", "linked_cameras"])
def test_existing_readers(mode, name):
    rows = [{"location": NAME, "detectorSerial": CHILD, "extra": ["KEEP"]}]

    def envelope(code, data):
        body = {"code": code, "msg": "old", "data": data}
        return {"result": body} if name == "list" else body

    call, r, seen = invoke_read(mode, name, envelope("10002", None), envelope("200", rows))
    result = call()
    assert result.data == rows and result.raw == envelope("200", rows)
    assert len(seen["biz"]) == len(seen["auth"]) == 2 and outcomes(r, mode, "retry", name) == [
        "scheduled"
    ]
    call, r, seen = invoke_read(mode, name, envelope(987654321, None))
    with pytest.raises(ApiError) as exc:
        call()
    assert exc.value.code == 987654321 and len(seen["auth"]) == len(seen["biz"]) == 1


def test_exposed_business_codes_normalization():
    assert ResponsePolicy().exposed_business_codes is None
    assert ResponsePolicy(exposed_business_codes=None).exposed_business_codes is None
    values = [17, "17"]
    policy = ResponsePolicy(exposed_business_codes=values)
    assert policy.exposed_business_codes == (17, "17")
    values.append("mutated")
    assert policy.exposed_business_codes == (17, "17")
    assert ResponsePolicy(
        exposed_business_codes=(c for c in (3, "x", 9))
    ).exposed_business_codes == (3, "x", 9)


def test_exposed_business_codes_rejected_inputs():
    for bad in ([], [True], [1.5], [None], [{"k": 1}], [["x"]], "17", b"17"):
        with pytest.raises(ValueError):
            ResponsePolicy(exposed_business_codes=bad)


def test_decode_response_business_error_code_handling():
    def code_of(raw, policy=None):
        with pytest.raises(ApiError) as exc:
            decode_response({"code": raw, "msg": "m", "data": {}}, policy=policy)
        return exc.value.code

    assert code_of(7) == 7
    assert isinstance(code_of(7), int)
    assert code_of("BIZ_ERR") == "BIZ_ERR"
    assert code_of(7) == code_of(7, policy=ResponsePolicy())
    redact = ResponsePolicy(redact_device_add_details=True)
    assert code_of(9, policy=redact) == 9
    assert code_of("BIZ_ERR", policy=redact) != "BIZ_ERR"


@pytest.mark.parametrize(
    "option,url",
    [
        ("redact_message_urls", "https://code.invalid/PRIVATE_URL_TAIL"),
        ("redact_media_urls", "rtmp://code.invalid/PRIVATE_URL_TAIL"),
    ],
)
def test_overseas_a1_code_url_redaction(option, url):
    for allowlist in (None, (url, 20018, "20018")):
        policy = ResponsePolicy(**{option: True}, exposed_business_codes=allowlist)
        with pytest.raises(ApiError) as exc_info:
            decode_response({"code": url, "msg": "denied"}, policy=policy)
        error = exc_info.value
        assert error.code == "***"
        assert "PRIVATE_URL_TAIL" not in str(error)
        assert "PRIVATE_URL_TAIL" not in repr(error)
        if allowlist is None:
            continue
        for expected in (20018, "20018"):
            with pytest.raises(ApiError) as exc_info:
                decode_response({"code": expected, "msg": "denied"}, policy=policy)
            assert exc_info.value.code == expected
            assert type(exc_info.value.code) is type(expected)
