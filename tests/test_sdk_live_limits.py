"""Contract tests for the ``live_limits`` facade on the real clients.

All traffic is stubbed with ``httpx.MockTransport``; no real endpoint,
credential, device or permission is involved. The facade mounts three
entries on one path, split by HTTP method: ``get`` (GET, query only),
``set`` (POST form) and ``delete`` (DELETE form). ``liveType`` is the
frozen, case-sensitive set ``VTDU``/``RTMP`` and the only success code is
the official integer ``200``; a literal ``0`` stays an ordinary ApiError.
"""

import asyncio
import sys
from urllib.parse import parse_qsl

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

HOST = "open.ys7.com"
PATH = "/api/v3/mall/online/user/limit/live"
TOKEN = "SYNTH_TOKEN"
AUTH_JSON = {
    "code": "200",
    "data": {
        "accessToken": TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
MODES = ("sync", "async")
META_OK = {"code": 200, "message": "ok"}
OPS = ("get", "set", "delete")
OP_KWARGS = {
    "get": {},
    "set": {"limit_type": 0, "limit_number": 5},
    "delete": {},
}


async def _call_async(client, op, args, kwargs):
    async with client as c:
        return await getattr(c.live_limits, op)(*args, **kwargs)


def run(mode, op, args, kwargs, payload, *, region=None):
    """Drive one real client call with fully mocked transports.

    ``payload["json"]`` is the business response body. ``counts``,
    ``requests`` (business traffic) and ``rec`` are written back into
    ``payload`` so they stay inspectable even when the call raises.
    ``region`` defaults to the domestic configuration and is passed
    through to the constructed client unchanged.
    """
    counts = payload["counts"] = {"auth": 0, "biz": 0}
    requests = payload["requests"] = []
    rec = payload["rec"] = DiagnosticRecorder()

    def auth(_request):
        counts["auth"] += 1
        return httpx.Response(200, json=AUTH_JSON)

    def business(request):
        counts["biz"] += 1
        requests.append(request)
        return httpx.Response(200, json=payload["json"])

    cls = Client if mode == "sync" else AsyncClient
    client = cls(
        "mock-key",
        "mock-secret",
        region=region or RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(business),
        diagnostic_recorder=rec,
    )
    if mode == "sync":
        with client as c:
            return getattr(c.live_limits, op)(*args, **kwargs)
    return asyncio.run(_call_async(client, op, args, kwargs))


def outcomes(rec, op, mode):
    return [
        event.get("outcome")
        for event in rec.report()["events"]
        if event.get("kind") == "call"
        and event.get("entrypoint") == "live_limits." + op
        and event.get("mode") == mode
    ]


def attempts(rec, operation_name, mode):
    return [
        event
        for event in rec.report()["events"]
        if event.get("kind") == "attempt"
        and event.get("operation") == operation_name
        and event.get("mode") == mode
    ]


def check_wire(request, method, fields):
    """Pinned wire shape: token in header only; GET=query, writes=form."""
    assert request.method == method
    assert request.url.host == HOST
    assert request.url.path == PATH
    assert request.headers["accessToken"] == TOKEN
    query = request.url.query.decode()
    body = request.content.decode()
    assert TOKEN not in query
    assert TOKEN not in body
    if method == "GET":
        assert body == ""
        assert "content-type" not in request.headers
        assert dict(parse_qsl(query)) == fields
    else:
        assert query == ""
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert dict(parse_qsl(body)) == fields


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("live_type", ("VTDU", "RTMP"))
def test_get_returns_rule_list(mode, live_type):
    rule = {"limitType": 1, "limitNumber": 4, "vendorField": "kept"}
    payload = {"json": {"meta": META_OK, "data": [rule], "traceId": "t-1"}}
    resp = run(mode, "get", (live_type,), {}, payload)
    assert resp.operation == "live_limits.get"
    assert resp.code == 200 and type(resp.code) is int
    assert resp.message == "ok"
    assert resp.data == [rule]
    assert resp.raw["data"] == [rule]
    assert resp.raw["traceId"] == "t-1"
    assert payload["counts"] == {"auth": 1, "biz": 1}
    check_wire(payload["requests"][0], "GET", {"liveType": live_type})
    assert outcomes(payload["rec"], "get", mode) == ["started", "succeeded"]
    assert attempts(payload["rec"], "live_limits.get", mode)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("data", ([], [[]]))
def test_get_array_shapes_preserved(mode, data):
    payload = {"json": {"meta": META_OK, "data": data}}
    resp = run(mode, "get", ("VTDU",), {}, payload)
    assert resp.data == data
    assert resp.raw["data"] == data


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("live_type", ("VTDU", "RTMP"))
@pytest.mark.parametrize("limit_type,limit_number", ((0, 0), (1, -3), (1, 7)))
def test_set_sends_exact_form(mode, live_type, limit_type, limit_number):
    payload = {"json": {"meta": META_OK, "page": "x"}}
    resp = run(
        mode,
        "set",
        (live_type,),
        {"limit_type": limit_type, "limit_number": limit_number},
        payload,
    )
    assert resp.operation == "live_limits.set"
    assert resp.code == 200 and type(resp.code) is int
    assert resp.data is None
    assert "data" not in resp.raw
    assert resp.raw["page"] == "x"
    check_wire(
        payload["requests"][0],
        "POST",
        {
            "liveType": live_type,
            "limitType": str(limit_type),
            "limitNumber": str(limit_number),
        },
    )
    assert outcomes(payload["rec"], "set", mode) == ["started", "succeeded"]
    assert attempts(payload["rec"], "live_limits.set", mode)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("live_type", ("VTDU", "RTMP"))
def test_delete_sends_only_live_type(mode, live_type):
    payload = {"json": {"meta": META_OK}}
    resp = run(mode, "delete", (live_type,), {}, payload)
    assert resp.operation == "live_limits.delete"
    assert resp.data is None and "data" not in resp.raw
    req = payload["requests"][0]
    check_wire(req, "DELETE", {"liveType": live_type})
    body = req.content.decode()
    assert "limitType" not in body and "limitNumber" not in body
    assert payload["counts"] == {"auth": 1, "biz": 1}
    assert outcomes(payload["rec"], "delete", mode) == ["started", "succeeded"]
    assert attempts(payload["rec"], "live_limits.delete", mode)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("data", ({"n": 1}, "oops", 5))
def test_get_non_list_root_is_protocol_error(mode, data):
    payload = {"json": {"meta": META_OK, "data": data}}
    with pytest.raises(ProtocolError):
        run(mode, "get", ("VTDU",), {}, payload)
    assert payload["counts"]["biz"] == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("op", OPS)
def test_code_zero_is_api_error(mode, op):
    payload = {"json": {"meta": {"code": 0, "message": "ok"}, "data": [[]]}}
    with pytest.raises(ApiError) as err:
        run(mode, op, ("VTDU",), OP_KWARGS[op], payload)
    assert type(err.value) is ApiError
    assert err.value.code == 0
    assert err.value.operation == "live_limits." + op
    assert payload["counts"] == {"auth": 1, "biz": 1}
    assert outcomes(payload["rec"], op, mode) == ["started", "failed"]


@pytest.mark.parametrize("mode", MODES)
def test_string_200_is_api_error(mode):
    payload = {"json": {"meta": {"code": "200", "message": "ok"}, "data": []}}
    with pytest.raises(ApiError) as err:
        run(mode, "get", ("VTDU",), {}, payload)
    assert err.value.code == "200"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "doc",
    (
        {},
        {"meta": "oops"},
        {"meta": {"message": "ok"}},
        {"meta": {"code": True, "message": "ok"}},
    ),
)
def test_malformed_meta_is_protocol_error(mode, doc):
    payload = {"json": doc}
    with pytest.raises(ProtocolError):
        run(mode, "get", ("VTDU",), {}, payload)
    assert payload["counts"]["biz"] == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("op", ("get", "set", "delete"))
@pytest.mark.parametrize("code", (1818, 10005))
def test_business_error_codes_propagate(mode, op, code):
    kwargs = {"limit_type": 0, "limit_number": 5} if op == "set" else {}
    payload = {"json": {"meta": {"code": code, "message": "boom"}, "data": []}}
    with pytest.raises(ApiError) as err:
        run(mode, op, ("VTDU",), kwargs, payload)
    assert err.value.code == code
    assert payload["counts"] == {"auth": 1, "biz": 1}
    assert outcomes(payload["rec"], op, mode) == ["started", "failed"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "op,kwargs",
    (("set", {"limit_type": 0, "limit_number": 5}), ("delete", {})),
)
def test_auth_error_has_no_retries(mode, op, kwargs):
    payload = {"json": {"meta": {"code": 10002, "message": "bad token"}}}
    with pytest.raises(AuthenticationError) as err:
        run(mode, op, ("VTDU",), kwargs, payload)
    assert err.value.code == 10002
    assert err.value.operation == "live_limits." + op
    assert payload["counts"] == {"auth": 1, "biz": 1}
    events = payload["rec"].report()["events"]
    assert not any(event.get("kind") == "retry" for event in events)
    assert outcomes(payload["rec"], op, mode) == ["started", "failed"]


@pytest.mark.parametrize("mode", MODES)
def test_error_repr_and_report_hide_payload_fields(mode):
    payload = {
        "json": {
            "meta": {
                "code": 1818,
                "message": "CANARY-MSG-9f27 rejected accessToken=SYNTH_TOKEN",
                "trace": "CANARY-FIELD-51",
            },
            "canaryCount": 424242,
        }
    }
    with pytest.raises(ApiError) as err:
        run(mode, "set", ("VTDU",), {"limit_type": 1, "limit_number": 2}, payload)
    err_value = err.value
    assert err_value.code == 1818
    assert err_value.operation == "live_limits.set"
    # Harmless remote message text survives; credential material inside it is
    # redacted, while payload fields stay off the error entirely.
    assert "CANARY-MSG-9f27" in err_value.message
    assert "CANARY-MSG-9f27" in repr(err_value)
    rendered = str(err_value) + repr(err_value)
    assert TOKEN not in rendered
    for canary in ("CANARY-FIELD-51", "424242"):
        assert canary not in rendered
    # The standalone diagnostic report projects none of the payload canaries.
    report_blob = repr(payload["rec"].report())
    for canary in ("CANARY-MSG-9f27", "CANARY-FIELD-51", "424242", TOKEN):
        assert canary not in report_blob
    assert outcomes(payload["rec"], "set", mode) == ["started", "failed"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("op", ("get", "set", "delete"))
@pytest.mark.parametrize("bad", ("vtdu", "HLS", "", "FLV", "EU", None, 7))
def test_invalid_live_type_rejected(mode, op, bad):
    kwargs = {"limit_type": 0, "limit_number": 5} if op == "set" else {}
    payload = {"json": {"meta": META_OK, "data": []}}
    with pytest.raises(ValueError):
        run(mode, op, (bad,), kwargs, payload)
    assert payload["counts"] == {"auth": 0, "biz": 0}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("bad", (True, 2, -1, "0", 1.0))
def test_invalid_limit_type_rejected(mode, bad):
    payload = {"json": {"meta": META_OK}}
    with pytest.raises(ValueError):
        run(mode, "set", ("VTDU",), {"limit_type": bad, "limit_number": 5}, payload)
    assert payload["counts"] == {"auth": 0, "biz": 0}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("bad", (True, 1.5, "5", None))
def test_invalid_limit_number_rejected(mode, bad):
    payload = {"json": {"meta": META_OK}}
    with pytest.raises(ValueError):
        run(mode, "set", ("VTDU",), {"limit_type": 0, "limit_number": bad}, payload)
    assert payload["counts"] == {"auth": 0, "biz": 0}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "op,args,kwargs",
    (
        ("set", ("VTDU",), {}),
        ("set", ("VTDU",), {"limit_type": 0}),
        ("set", ("VTDU", 0, 5), {}),
        ("set", ("VTDU",), {"limit_type": 0, "limit_number": 5, "bogus": 1}),
        ("get", (), {}),
        ("delete", ("VTDU", "extra"), {}),
    ),
)
def test_signature_errors_are_typeerror(mode, op, args, kwargs):
    payload = {"json": {"meta": META_OK, "data": []}}
    with pytest.raises(TypeError):
        run(mode, op, args, kwargs, payload)
    assert payload["counts"] == {"auth": 0, "biz": 0}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("op", OPS)
def test_overseas_region_rejected_before_any_io(mode, op):
    """Every entrypoint is domestic-only: exact ValueError, zero traffic."""
    payload = {"json": {"meta": META_OK, "data": []}}
    with pytest.raises(ValueError) as err:
        run(
            mode,
            op,
            ("VTDU",),
            OP_KWARGS[op],
            payload,
            region=RegionConfig.overseas(),
        )
    assert str(err.value) == "live concurrency limit operations require the domestic region"
    assert payload["counts"] == {"auth": 0, "biz": 0}
    assert payload["requests"] == []
    assert outcomes(payload["rec"], op, mode) == ["started", "failed"]


@pytest.mark.parametrize("mode", MODES)
def test_limit_number_above_interpreter_encoding_limit_rejected(mode):
    """The first int that the interpreter cannot str() must fail before I/O."""
    limit = sys.get_int_max_str_digits()
    payload = {"json": {"meta": META_OK}}
    if limit == 0:
        # With the digit limit disabled no int is unencodable, so the
        # original 4301-digit magnitude round-trips onto the wire instead.
        resp = run(
            mode,
            "set",
            ("VTDU",),
            {"limit_type": 1, "limit_number": 10**4300},
            payload,
        )
        assert resp.code == 200
        check_wire(
            payload["requests"][0],
            "POST",
            {
                "liveType": "VTDU",
                "limitType": "1",
                "limitNumber": "1" + "0" * 4300,
            },
        )
        assert payload["counts"] == {"auth": 1, "biz": 1}
        assert outcomes(payload["rec"], "set", mode) == ["started", "succeeded"]
        return
    with pytest.raises(ValueError):
        run(
            mode,
            "set",
            ("VTDU",),
            {"limit_type": 1, "limit_number": 10**limit},
            payload,
        )
    assert payload["counts"] == {"auth": 0, "biz": 0}
    assert payload["requests"] == []
    assert outcomes(payload["rec"], "set", mode) == ["started", "failed"]


@pytest.mark.parametrize("mode", MODES)
def test_limit_number_huge_int_reaches_form_uncapped(mode):
    """With no SDK range cap, the biggest str()-able int reaches the form."""
    payload = {"json": {"meta": META_OK}}
    if limit := sys.get_int_max_str_digits():
        exponent = min(4300, limit - 1)
    else:
        exponent = 4300
    huge = 10**exponent
    encoded = "1" + "0" * exponent
    resp = run(
        mode,
        "set",
        ("VTDU",),
        {"limit_type": 1, "limit_number": huge},
        payload,
    )
    assert resp.code == 200
    check_wire(
        payload["requests"][0],
        "POST",
        {
            "liveType": "VTDU",
            "limitType": "1",
            "limitNumber": encoded,
        },
    )
    assert payload["counts"] == {"auth": 1, "biz": 1}


@pytest.mark.parametrize("mode", MODES)
def test_success_repr_and_report_hide_raw_payload_fields(mode):
    """Raw extras stay on ApiResponse.raw but never reach repr or report."""
    rule = {"limitType": 1, "limitNumber": 4, "rawCanary": "CANARY-RAW-77"}
    payload = {"json": {"meta": META_OK, "data": [rule], "extraCanary": "CANARY-EXTRA-64"}}
    resp = run(mode, "get", ("VTDU",), {}, payload)
    assert resp.code == 200
    assert resp.raw["extraCanary"] == "CANARY-EXTRA-64"
    assert resp.data[0]["rawCanary"] == "CANARY-RAW-77"
    rendered = repr(resp)
    report_blob = repr(payload["rec"].report())
    for canary in ("CANARY-RAW-77", "CANARY-EXTRA-64"):
        assert canary not in rendered
        assert canary not in report_blob
