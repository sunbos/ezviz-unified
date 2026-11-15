"""Whole-JSON-body contract evaluation tests (H014).

A synthetic client pair is substituted for ``Client``/``AsyncClient`` via
``monkeypatch`` so the tests can drive ``evaluate()`` end to end.
Everything under test -- MockTransport request capture, wire comparison,
request counting, dispatch verification and result classification --
remains the real evaluator machinery: a tampered body, header or token
still fails, so a passing grid is earned rather than self-reported. The
synthetic contracts and cases in this file never enter the real registry
or coverage statistics.
"""

from __future__ import annotations

import copy
import importlib
import json
import types
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]

AUTH_ID = "cn.token.get"
OP_ID = "synthetic.whole_body.set"
AUTH_PATH = "/api/synthetic/token/get"
OP_PATH = "/api/synthetic/whole-body/set"
ORIGIN = "https://open.ys7.com"
TOKEN_FIELD = "accessToken"
FIRST_TOKEN = "at.EVAL_TOKEN_1"
TRACE_ID = "trace-synthetic-1"
SHA = "d" * 64
# Fixed fault digits emitted by the evaluator's mock handlers.
FAULT_AUTH = 10002
FAULT_PERMISSION = 10031
GOOD_HEADERS = {"X-Trace-Id": TRACE_ID, TOKEN_FIELD: FIRST_TOKEN}
GOOD_PARAMS = {"channel": "eval", "clientType": "sdk"}


def wire_eval():
    assert (ROOT / "harness/wire_eval.py").exists(), "wire evaluator missing"
    return importlib.import_module("harness.wire_eval")


def registry():
    assert (ROOT / "harness/registry.py").exists(), "registry auditor missing"
    return importlib.import_module("harness.registry")


def evaluate(data):
    return wire_eval().evaluate(*data, ROOT)


# ---------------------------------------------------------------------------
# Synthetic contract / binding / case documents (tool unit tests only)
# ---------------------------------------------------------------------------


def auth_contract():
    return {
        "operation_id": AUTH_ID,
        "region": "domestic",
        "source_doc_id": "SYN-AUTH",
        "source_url": "https://open.ys7.com/help/synthetic-auth",
        "source_content_sha256": SHA,
        "method": "POST",
        "path": AUTH_PATH,
        "base_url": ORIGIN,
        "read_only": True,
        "body_encoding": "form_urlencoded",
        "token_location": "none",
        "parameters": [],
        "success": {
            "code_path": "code",
            "message_path": "message",
            "data_path": "data",
            "values": [200],
        },
    }


def whole_body_contract(
    token_location="header", read_only=False, body_type="object", required=True
):
    parameters = [
        {
            "name": "X-Trace-Id",
            "location": "header",
            "type": "String",
            "required": True,
        },
        {
            "name": "channel",
            "location": "query",
            "type": "String",
            "required": False,
        },
        {
            "name": "clientType",
            "location": "query",
            "type": "String",
            "required": True,
            "constant": "sdk",
        },
    ]
    if token_location != "none":
        parameters.append(
            {
                "name": TOKEN_FIELD,
                "location": token_location,
                "type": "String",
                "required": True,
            }
        )
    return {
        "operation_id": OP_ID,
        "region": "domestic",
        "source_doc_id": "SYN-WB",
        "source_url": "https://open.ys7.com/help/synthetic-whole-body",
        "source_content_sha256": SHA,
        "method": "POST",
        "path": OP_PATH,
        "read_only": read_only,
        "body_encoding": "json",
        "token_location": token_location,
        "request_body": {
            "type": body_type,
            "required": required,
            "evidence": "synthetic descriptor for harness unit tests",
        },
        "parameters": parameters,
        "success": {
            "code_path": "code",
            "message_path": "message",
            "data_path": "data",
            "values": [200],
        },
    }


def body_template():
    """A body exercising every JSON shape: nested objects, ordered arrays,
    empty containers, nulls, booleans, ints, floats -- plus a nested
    ``accessToken`` key that is product data, not the managed credential."""
    return {
        "deviceSerial": {"$ref": "device_serial"},
        "enabled": True,
        "level": 2,
        "ratio": 1.5,
        "label": None,
        "tags": ["alpha", "beta"],
        "schedule": {
            "weekdays": [1, 2, 3],
            "empty": {},
            "nestedNull": None,
            "accessToken": "product-owned-attribute",
        },
        "emptyList": [],
        "emptyObject": {},
    }


def plain_body():
    body = body_template()
    body["deviceSerial"] = "EV1234567"
    return body


_OMITTED = object()


def whole_body_case(body=_OMITTED):
    """Case whose json_body is the object template, or a caller-supplied root.

    Passing ``body=[]`` (or any explicit value, including ``None``) overrides
    the template; the omitted-argument default keeps the original object
    shape.
    """
    payload = body_template() if body is _OMITTED else body
    return {
        "operation_id": OP_ID,
        "kwargs": {"payload": copy.deepcopy(payload)},
        "fields": {"X-Trace-Id": TRACE_ID, "channel": "eval"},
        "json_body": copy.deepcopy(payload),
        "data": {"marker": "synthetic", "index": 1},
    }


def bindings_doc():
    return {
        "schema_version": 1,
        "bindings": [
            {
                "operation_id": OP_ID,
                "region": "domestic",
                "source_doc_id": "SYN-WB",
                "module": "synthetic/whole_body.py",
                "symbols": ["Synthetic.whole_body"],
                "offline_status": "synthetic",
                "live_status": "not_verified",
                "public_entry": "synthetic_whole_body",
            }
        ],
    }


def eval_inputs(token_location="header", read_only=False, case=None, contract=None):
    biz = contract if contract is not None else whole_body_contract(token_location, read_only)
    return (
        {"schema_version": 1, "contracts": [auth_contract(), biz]},
        bindings_doc(),
        {"schema_version": 1, "cases": [case if case is not None else whole_body_case()]},
    )


# ---------------------------------------------------------------------------
# Synthetic client substituted for Client/AsyncClient under test
# ---------------------------------------------------------------------------


def _synthetic_operation(token_location, read_only):
    return types.SimpleNamespace(
        method="POST",
        path=OP_PATH,
        token_location=token_location,
        read_only=read_only,
        regions=frozenset({"domestic"}),
    )


def fake_client_classes(token_location="header", read_only=False, tamper=None):
    """Build synthetic Client/AsyncClient classes for evaluate().

    Both consume exactly the kwargs the evaluator injects: ``auth_transport``
    (a MockTransport) serves the token call and ``transport_factory`` builds
    the business-request transport, so every byte on the wire is produced by
    the same machinery the real SDK uses. ``tamper`` introduces exactly one
    deliberate wire deviation per run; without it the emitted request is
    contract-correct in every scenario and mode.
    """
    import ezviz_unified as package

    api_response_cls = package.ApiResponse
    auth_error_cls = package.AuthenticationError
    permission_error_cls = package.PermissionDeniedError
    api_error_cls = package.ApiError

    def make_error(cls, http_status, invalid_token=False):
        if invalid_token:
            code = 10002
        elif cls is permission_error_cls:
            code = 10031
        else:
            code = None
        message = f"{cls.__name__}(code={code}, http_status={http_status})"
        return cls(message, code=code, http_status=http_status)

    def make_response(http_status, payload):
        return api_response_cls(
            http_status=http_status,
            code=payload["code"],
            message=payload["message"],
            data=payload["data"],
            raw=payload,
        )

    class _Base:
        def __init__(self, app_key, app_secret, **kwargs):
            self._app_key = app_key
            self._app_secret = app_secret
            self._auth_transport = kwargs["auth_transport"]
            self._transport_factory = kwargs["transport_factory"]

        def _get_token(self):
            if token_location == "none":
                # Public operations carry no managed credential: no auth call.
                return None
            with httpx.Client(transport=self._auth_transport) as client:
                response = client.post(
                    ORIGIN + AUTH_PATH,
                    data={"appKey": self._app_key, "appSecret": self._app_secret},
                )
            return response.json()["data"]["accessToken"]

        def _tampered_body(self, payload):
            body = copy.deepcopy(payload)
            if tamper == "bool_as_int":
                body["enabled"] = 1
            elif tamper == "int_as_float":
                body["level"] = 2.0
            elif tamper == "array_order":
                body["tags"] = list(reversed(body["tags"]))
            elif tamper == "nested_order":
                body["schedule"]["weekdays"] = [3, 2, 1]
            elif tamper == "null_as_zero":
                body["schedule"]["nestedNull"] = 0
            elif tamper == "drop_key":
                body.pop("label")
            elif tamper == "extra_key":
                body["rogue"] = True
            return body

        def _send(self, operation, token, payload):
            headers = {"X-Trace-Id": TRACE_ID}
            params = {"channel": "eval", "clientType": "sdk"}
            if operation.token_location == "header":
                headers[TOKEN_FIELD] = token
            elif operation.token_location == "query":
                params[TOKEN_FIELD] = token
            # token_location == "none": no managed credential is injected.
            body = self._tampered_body(payload)
            path = operation.path
            if tamper == "wrong_path":
                path = "/api/synthetic/elsewhere"
            elif tamper == "missing_header":
                headers.pop("X-Trace-Id")
            elif tamper == "wrong_header":
                headers["X-Trace-Id"] = "forged"
            elif tamper == "extra_header":
                headers["X-Rogue"] = "1"
            elif tamper == "token_in_body":
                headers.pop(TOKEN_FIELD, None)
                params.pop(TOKEN_FIELD, None)
                body[TOKEN_FIELD] = token
            with httpx.Client(transport=self._transport_factory(ORIGIN)) as client:
                if tamper == "form_body":
                    return client.post(
                        ORIGIN + path, params=params, data={"flat": "1"}, headers=headers
                    )
                return client.post(ORIGIN + path, params=params, json=body, headers=headers)

        def _flow(self, operation, payload):
            token = self._get_token()
            response = self._send(operation, token, payload)
            if response.status_code != 200:
                raise make_error(api_error_cls, 500)
            envelope = response.json()
            code = envelope["code"]
            if code == FAULT_AUTH:
                if operation.token_location == "none" or not operation.read_only:
                    # Public operations have no managed credential, so there is
                    # nothing to refresh; write operations must not replay.
                    raise make_error(auth_error_cls, 200, invalid_token=True)
                token = self._get_token()
                response = self._send(operation, token, payload)
                if response.status_code != 200:
                    raise make_error(api_error_cls, 500)
                envelope = response.json()
            elif code == FAULT_PERMISSION:
                raise make_error(permission_error_cls, 200)
            return make_response(response.status_code, envelope)

        def _operation(self):
            return _synthetic_operation(token_location, read_only)

    class SyncClient(_Base):
        def request(self, operation, payload=None):
            return self._flow(operation, payload)

        def synthetic_whole_body(self, payload):
            return self.request(self._operation(), payload=payload)

        def close(self):
            return None

    class AsyncClient(_Base):
        async def request(self, operation, payload=None):
            return self._flow(operation, payload)

        async def synthetic_whole_body(self, payload):
            return await self.request(self._operation(), payload=payload)

        async def aclose(self):
            return None

    return SyncClient, AsyncClient


def _install_fake(monkeypatch, token_location="header", read_only=False, tamper=None):
    import ezviz_unified as package

    sync_cls, async_cls = fake_client_classes(token_location, read_only, tamper)
    monkeypatch.setattr(package, "Client", sync_cls)
    monkeypatch.setattr(package, "AsyncClient", async_cls)


# ---------------------------------------------------------------------------
# End-to-end evaluate() coverage: pass grids and wire-mismatch grids
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token_location", ["header", "query", "none"])
@pytest.mark.parametrize("read_only", [False, True])
def test_whole_body_grid_passes_with_real_wire_checks(monkeypatch, token_location, read_only):
    _install_fake(monkeypatch, token_location, read_only)
    data = eval_inputs(token_location, read_only)
    before = copy.deepcopy(data)
    report = evaluate(data)
    assert data == before, "evaluate() must not mutate its inputs"
    assert report["evidence"] == "offline_contract_conformance_only"
    assert report["passed"], json.dumps(report["results"], indent=2)
    assert report["summary"]["executions"] == 8
    assert report["summary"]["passed_executions"] == 8
    assert all(row["passed"] for row in report["results"])
    assert report["covered_operation_ids"] == [OP_ID]
    assert report["missing_operation_ids"] == [AUTH_ID]
    assert not report["coverage_complete"]


def test_none_location_sends_no_auth_and_never_replays_on_auth_fault():
    """token_location="none" is a public operation: zero auth requests and
    no managed credential on the wire; a 10002 envelope raises
    AuthenticationError immediately -- even when read_only -- instead of
    triggering a token refresh/replay."""
    import ezviz_unified as package

    sync_cls, _ = fake_client_classes("none", read_only=True)
    auth_calls = []
    business_calls = []

    def auth_handler(request):
        auth_calls.append(request)
        return httpx.Response(200, json={"code": 200, "message": "ok", "data": {}})

    ok = {"code": 200, "message": "ok", "data": {}}
    fault = {"code": FAULT_AUTH, "message": "fault", "data": None}
    responses = iter([httpx.Response(200, json=ok), httpx.Response(200, json=fault)])

    def business_handler(request):
        business_calls.append(request)
        return next(responses)

    def make_client():
        return sync_cls(
            "key",
            "secret",
            auth_transport=httpx.MockTransport(auth_handler),
            transport_factory=lambda origin: httpx.MockTransport(business_handler),
        )

    operation = make_client()._operation()
    assert operation.token_location == "none"

    client = make_client()
    client.request(operation, payload={})
    assert len(business_calls) == 1
    request = business_calls[0]
    assert TOKEN_FIELD not in request.headers
    assert TOKEN_FIELD not in str(request.url.query)
    assert auth_calls == []

    client = make_client()
    with pytest.raises(package.AuthenticationError):
        client.request(operation, payload={})
    assert len(business_calls) == 2  # the fault cell only: no replay
    assert auth_calls == []


WIRE_TAMPERS = [
    "bool_as_int",
    "int_as_float",
    "array_order",
    "nested_order",
    "null_as_zero",
    "drop_key",
    "extra_key",
    "token_in_body",
    "form_body",
    "missing_header",
    "wrong_header",
    "extra_header",
    "wrong_path",
]


@pytest.mark.parametrize("tamper", WIRE_TAMPERS)
def test_wire_tampering_fails_every_cell(monkeypatch, tamper):
    _install_fake(monkeypatch, tamper=tamper)
    report = evaluate(eval_inputs())
    assert not report["passed"]
    assert report["summary"]["passed_executions"] == 0
    assert report["summary"]["failed_executions"] == 8
    assert {row["problem"] for row in report["results"]} == {"request_mismatch"}
    assert OP_ID not in report["covered_operation_ids"]


# ---------------------------------------------------------------------------
# Case-level rejections (fixed problem codes, grids emitted per case)
# ---------------------------------------------------------------------------


CASE_REJECTS = [
    ("missing", "case_invalid"),
    ("null", "case_invalid"),
    ("array", "case_invalid"),
    ("scalar", "case_invalid"),
    ("decoded_scalar", "case_invalid"),
    ("non_string_key", "case_invalid"),
    ("nan", "case_invalid"),
    ("infinite", "case_invalid"),
    ("datetime_tag", "case_invalid"),
    ("top_level_token", "managed_token_field"),
    ("top_level_token_pascal", "managed_token_field"),
    ("top_level_token_upper", "managed_token_field"),
    ("json_fields_present", "case_invalid"),
    ("unknown_field", "unknown_field"),
    ("missing_required_header", "missing_required_field"),
    ("token_in_fields", "managed_token_field"),
    ("null_body_for_array", "case_invalid"),
    ("object_body_for_null", "case_invalid"),
    ("scalar_for_array", "case_invalid"),
]


def mutate_case(case, kind):
    if kind == "missing":
        case.pop("json_body")
    elif kind == "null":
        case["json_body"] = None
    elif kind == "array":
        case["json_body"] = [1, 2]
    elif kind == "scalar":
        case["json_body"] = "text"
    elif kind == "decoded_scalar":
        case["json_body"] = {"$ref": "device_serial"}
    elif kind == "non_string_key":
        case["json_body"] = {1: "x"}
    elif kind == "nan":
        case["json_body"] = {"value": float("nan")}
    elif kind == "infinite":
        case["json_body"] = {"value": float("inf")}
    elif kind == "datetime_tag":
        case["json_body"] = {"when": {"$type": "datetime", "value": "2026-01-01T00:00:00"}}
    elif kind == "top_level_token":
        case["json_body"][TOKEN_FIELD] = "smuggled"
    elif kind == "top_level_token_pascal":
        case["json_body"]["AccessToken"] = "smuggled"
    elif kind == "top_level_token_upper":
        case["json_body"]["ACCESSTOKEN"] = "smuggled"
    elif kind == "json_fields_present":
        case["json_fields"] = ["channel"]
    elif kind == "unknown_field":
        case["fields"]["bogus"] = "1"
    elif kind == "missing_required_header":
        case["fields"].pop("X-Trace-Id")
    elif kind == "token_in_fields":
        case["fields"][TOKEN_FIELD] = "smuggled"
    elif kind in ("null_body_for_array", "object_body_for_null", "scalar_for_array"):
        raise AssertionError(f"{kind} is handled by a contract-scoped test")
    else:
        raise AssertionError(f"unknown case mutation {kind}")


# Kinds whose expectation depends on a non-default request_body.type: the
# mutation builds a legal JSON root, just not the declared one.
CASE_REJECT_BODY_TYPES = {
    "null_body_for_array": ("array", None),
    "object_body_for_null": ("null", {"k": 1}),
    "scalar_for_array": ("array", "text"),
}


@pytest.mark.parametrize("kind,problem", CASE_REJECTS)
def test_case_level_rejections_emit_fixed_problem(kind, problem):
    if kind in CASE_REJECT_BODY_TYPES:
        body_type, body = CASE_REJECT_BODY_TYPES[kind]
        contract = whole_body_contract(body_type=body_type)
        case = whole_body_case(body)
        if kind == "null_body_for_array":
            # The explicit None must survive the fixture: json_body really is
            # a null root, not the default object template.
            assert case["json_body"] is None
            assert case["kwargs"]["payload"] is None
    else:
        contract = None
        case = whole_body_case()
        mutate_case(case, kind)
    report = evaluate(eval_inputs(case=case, contract=contract))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {problem}


# ---------------------------------------------------------------------------
# Contract-level rejections (illegal descriptor/schema combinations)
# ---------------------------------------------------------------------------


CONTRACT_REJECTS = [
    "form_encoding",
    "json_token",
    "form_token",
    "body_param",
    "spec_null",
    "spec_string",
    "spec_type_binary",
    "spec_required_string",
    "spec_extra_key",
    "spec_missing_evidence",
]


def mutate_contract(contract, kind):
    if kind == "form_encoding":
        contract["body_encoding"] = "form_urlencoded"
    elif kind == "json_token":
        contract["token_location"] = "json"
    elif kind == "form_token":
        contract["token_location"] = "form"
        contract["body_encoding"] = "form_urlencoded"
    elif kind == "body_param":
        contract["parameters"].append(
            {
                "name": "namedBodyField",
                "location": "body",
                "type": "String",
                "required": False,
            }
        )
    elif kind == "spec_null":
        contract["request_body"] = None
    elif kind == "spec_string":
        contract["request_body"] = "object"
    elif kind == "spec_type_binary":
        contract["request_body"]["type"] = "binary"
    elif kind == "spec_required_string":
        contract["request_body"]["required"] = "false"
    elif kind == "spec_extra_key":
        contract["request_body"]["schema"] = "openapi-style"
    elif kind == "spec_missing_evidence":
        contract["request_body"].pop("evidence")
    else:
        raise AssertionError(f"unknown contract mutation {kind}")


@pytest.mark.parametrize("kind", CONTRACT_REJECTS)
def test_contract_level_rejections_emit_contract_invalid(kind):
    contract = whole_body_contract()
    mutate_contract(contract, kind)
    report = evaluate(eval_inputs(contract=contract))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"contract_invalid"}


def test_array_root_descriptor_evaluates_cleanly(monkeypatch):
    """type "array" is an approved root: a matching case passes the whole
    grid through the real wire-checking machinery."""
    _install_fake(monkeypatch)
    contract = whole_body_contract(body_type="array")
    case = whole_body_case(body=["alpha", {"k": 1}, None, True])
    report = evaluate(eval_inputs(case=case, contract=contract))
    assert report["passed"], json.dumps(report["results"], indent=2)
    assert report["summary"]["executions"] == 8


def test_array_descriptor_with_object_case_is_case_invalid_not_contract_invalid():
    """A legal ``type: "array"`` descriptor paired with a dict json_body is
    a case-level root mismatch (case_invalid), never a contract defect."""
    contract = whole_body_contract(body_type="array")
    report = evaluate(eval_inputs(contract=contract))  # default case body is a dict
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"case_invalid"}


def test_required_false_descriptor_evaluates_cleanly(monkeypatch):
    """required: false is approved for the descriptor; the whole grid still
    passes with an explicitly supplied body."""
    _install_fake(monkeypatch)
    contract = whole_body_contract(required=False)
    report = evaluate(eval_inputs(contract=contract))
    assert report["passed"], json.dumps(report["results"], indent=2)
    assert report["summary"]["executions"] == 8


def test_case_json_body_is_forbidden_without_descriptor():
    contract = whole_body_contract()
    contract.pop("request_body")
    contract["parameters"].append(
        {
            "name": "namedBodyField",
            "location": "body",
            "type": "String",
            "required": False,
        }
    )
    report = evaluate(eval_inputs(contract=contract))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"case_invalid"}


# ---------------------------------------------------------------------------
# Direct _check_business_request coverage via real MockTransport requests
# ---------------------------------------------------------------------------


def send_business_request(
    *, url=ORIGIN + OP_PATH, json_body=None, content=None, headers=None, params=None
):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"code": 200})

    kwargs = {}
    if json_body is not None:
        kwargs["json"] = json_body
    if content is not None:
        kwargs["content"] = content
    if headers:
        kwargs["headers"] = headers
    if params:
        kwargs["params"] = params
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        client.post(url, **kwargs)
    assert len(captured) == 1
    return captured[0]


def expected_wire(contract, body, token=FIRST_TOKEN):
    expected = wire_eval()._expected_wire(
        contract,
        {"X-Trace-Id": TRACE_ID, "channel": "eval"},
        [],
        token,
        body,
    )
    expected["origin"] = ORIGIN
    return expected


def test_business_request_check_accepts_exact_whole_body():
    mod = wire_eval()
    contract = whole_body_contract("header")
    expected = expected_wire(contract, plain_body())
    request = send_business_request(
        json_body=plain_body(),
        headers=dict(GOOD_HEADERS),
        params=dict(GOOD_PARAMS),
    )
    assert mod._check_business_request(request, contract, expected) is None


def test_business_request_check_accepts_query_token_variant():
    mod = wire_eval()
    contract = whole_body_contract("query")
    expected = expected_wire(contract, plain_body())
    request = send_business_request(
        json_body=plain_body(),
        headers={"X-Trace-Id": TRACE_ID},
        params={**GOOD_PARAMS, TOKEN_FIELD: FIRST_TOKEN},
    )
    assert mod._check_business_request(request, contract, expected) is None


BODY_TAMPERS = [
    "bool_as_int",
    "int_as_float",
    "array_order",
    "nested_order",
    "null_as_zero",
    "drop_key",
    "extra_key",
    "list_body",
]


def tamper_body(body, kind):
    if kind == "bool_as_int":
        body["enabled"] = 1
    elif kind == "int_as_float":
        body["level"] = 2.0
    elif kind == "array_order":
        body["tags"] = list(reversed(body["tags"]))
    elif kind == "nested_order":
        body["schedule"]["weekdays"] = [3, 2, 1]
    elif kind == "null_as_zero":
        body["schedule"]["nestedNull"] = 0
    elif kind == "drop_key":
        body.pop("label")
    elif kind == "extra_key":
        body["rogue"] = True
    elif kind == "list_body":
        return ["not", "an", "object"]
    else:
        raise AssertionError(f"unknown body tamper {kind}")
    return body


@pytest.mark.parametrize("kind", BODY_TAMPERS)
def test_business_request_check_detects_recursive_type_tampering(kind):
    """bool vs int, int vs float, array order, nested/null/key tampering and
    non-object bodies must all fail the strict recursive comparison."""
    mod = wire_eval()
    contract = whole_body_contract("header")
    expected = expected_wire(contract, plain_body())
    request = send_business_request(
        json_body=tamper_body(plain_body(), kind),
        headers=dict(GOOD_HEADERS),
        params=dict(GOOD_PARAMS),
    )
    assert mod._check_business_request(request, contract, expected) == "request_mismatch"


def test_business_request_check_rejects_header_token_and_envelope_tampering():
    mod = wire_eval()
    contract = whole_body_contract("header")
    expected = expected_wire(contract, plain_body())

    def check(**send):
        request = send_business_request(json_body=plain_body(), **send)
        return mod._check_business_request(request, contract, expected)

    assert check(headers=dict(GOOD_HEADERS), params=dict(GOOD_PARAMS)) is None
    assert (
        check(headers={**GOOD_HEADERS, "X-Trace-Id": "forged"}, params=dict(GOOD_PARAMS))
        == "request_mismatch"
    )
    assert check(headers={"X-Trace-Id": TRACE_ID}, params=dict(GOOD_PARAMS)) == "request_mismatch"
    assert (
        check(headers={**GOOD_HEADERS, "X-Rogue": "1"}, params=dict(GOOD_PARAMS))
        == "request_mismatch"
    )
    assert (
        check(
            headers={"X-Trace-Id": TRACE_ID},
            params={**GOOD_PARAMS, TOKEN_FIELD: FIRST_TOKEN},
        )
        == "request_mismatch"
    )
    assert check(headers=dict(GOOD_HEADERS), params={"channel": "eval"}) == "request_mismatch"
    assert (
        check(
            url=ORIGIN + "/api/synthetic/elsewhere",
            headers=dict(GOOD_HEADERS),
            params=dict(GOOD_PARAMS),
        )
        == "request_mismatch"
    )


def test_business_request_check_rejects_non_json_encoding():
    mod = wire_eval()
    contract = whole_body_contract("header")
    expected = expected_wire(contract, plain_body())
    request = send_business_request(
        content=b"flat=1",
        headers={
            **GOOD_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        params=dict(GOOD_PARAMS),
    )
    assert mod._check_business_request(request, contract, expected) == "request_mismatch"


# ---------------------------------------------------------------------------
# Registry structural audit coverage for the request_body descriptor
# ---------------------------------------------------------------------------


def audit_inputs():
    contracts = {"schema_version": 1, "contracts": [auth_contract(), whole_body_contract()]}
    bindings = {
        "schema_version": 1,
        "bindings": [
            {
                "operation_id": AUTH_ID,
                "region": "domestic",
                "source_doc_id": "SYN-AUTH",
                "module": "synthetic/auth.py",
                "symbols": ["Synthetic.auth"],
                "offline_status": "synthetic",
                "live_status": "not_verified",
            },
            {
                "operation_id": OP_ID,
                "region": "domestic",
                "source_doc_id": "SYN-WB",
                "module": "synthetic/whole_body.py",
                "symbols": ["Synthetic.whole_body"],
                "offline_status": "synthetic",
                "live_status": "not_verified",
            },
        ],
    }
    issues = {"schema_version": 1, "issues": []}
    return contracts, bindings, issues


def test_registry_accepts_whole_body_descriptor():
    report = registry().audit_registry(*audit_inputs())
    assert report["summary"]["contracts"] == 2
    assert report["summary"]["bindings"] == 2
    assert report["regions"] == {"domestic": 2}


def test_registry_json_contract_without_descriptor_stays_valid():
    docs = audit_inputs()
    docs[0]["contracts"][1].pop("request_body")
    registry().audit_registry(*docs)  # must not raise: the field is optional


@pytest.mark.parametrize("body_type", ["array", "string", "integer", "number", "boolean", "null"])
def test_registry_accepts_non_object_root_types(body_type):
    docs = audit_inputs()
    docs[0]["contracts"][1]["request_body"]["type"] = body_type
    registry().audit_registry(*docs)


@pytest.mark.parametrize("required", [False, None])
def test_registry_accepts_required_false_and_null(required):
    docs = audit_inputs()
    spec = docs[0]["contracts"][1]["request_body"]
    spec["required"] = required
    if required is None:
        spec["required_uncertainty"] = "synthetic: source does not state requiredness"
    registry().audit_registry(*docs)


@pytest.mark.parametrize("kind", CONTRACT_REJECTS)
def test_registry_rejects_illegal_request_body_combinations(kind):
    docs = audit_inputs()
    mutate_contract(docs[0]["contracts"][1], kind)
    with pytest.raises(registry().RegistryError):
        registry().audit_registry(*docs)


# ---------------------------------------------------------------------------
# Legacy regression: real contracts unchanged, json_body forbidden there
# ---------------------------------------------------------------------------


def test_real_contracts_reject_json_body_and_keep_legacy_behaviour():
    contracts = json.loads((ROOT / "contracts/reviewed-transport-contracts.json").read_text())
    bindings = json.loads((ROOT / "contracts/sdk-bindings.json").read_text())
    base_case = {
        "operation_id": "cn.devices.get",
        "args": [{"$ref": "device_serial"}],
        "fields": {"deviceSerial": {"$ref": "device_serial"}},
        "data": {"marker": "fixture"},
    }
    good = {"schema_version": 1, "cases": [copy.deepcopy(base_case)]}
    assert evaluate((contracts, bindings, good))["passed"]

    bad_case = copy.deepcopy(base_case)
    bad_case["json_body"] = {"deviceSerial": "EV1234567"}
    bad = {"schema_version": 1, "cases": [bad_case]}
    report = evaluate((contracts, bindings, bad))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"case_invalid"}
