"""Whole-JSON-body root-type conformance tests for the wire evaluator.

Extends the whole-body descriptor beyond objects: any declared JSON root
type (object/array/string/integer/number/boolean/null) may be the request
body, and the descriptor ``required`` may be an explicit ``null`` paired
with ``required_uncertainty``. A case ``json_body: null`` is a deliberate
literal ``b'null'`` body — distinct from an absent ``json_body`` key, which
stays a case defect. As before, the expected wire is built only from the
contract and case inputs; a tampered body, a null swapped for an empty
body, or a wrong root type fails every grid cell.
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
OP_ID = "synthetic.json_root.set"
AUTH_PATH = "/api/synthetic/token/get"
OP_PATH = "/api/synthetic/json-root/set"
ORIGIN = "https://open.ys7.com"
TOKEN_FIELD = "accessToken"
FIRST_TOKEN = "at.EVAL_TOKEN_1"
TRACE_ID = "trace-synthetic-root"
SHA = "d" * 64
FAULT_AUTH = 10002
FAULT_PERMISSION = 10031
GOOD_HEADERS = {"X-Trace-Id": TRACE_ID, TOKEN_FIELD: FIRST_TOKEN}
GOOD_PARAMS = {"clientType": "sdk"}


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


def root_contract(body_type="object", required=True):
    spec = {
        "type": body_type,
        "required": required,
        "evidence": "synthetic descriptor for harness unit tests",
    }
    if required is None:
        spec["required_uncertainty"] = "synthetic: source does not state requiredness"
    return {
        "operation_id": OP_ID,
        "region": "domestic",
        "source_doc_id": "SYN-ROOT",
        "source_url": "https://open.ys7.com/help/synthetic-root",
        "source_content_sha256": SHA,
        "method": "PUT",
        "path": OP_PATH,
        "read_only": False,
        "body_encoding": "json",
        "token_location": "header",
        "request_body": spec,
        "parameters": [
            {
                "name": "X-Trace-Id",
                "location": "header",
                "type": "String",
                "required": True,
            },
            {
                "name": "clientType",
                "location": "query",
                "type": "String",
                "required": True,
                "constant": "sdk",
            },
            {
                "name": TOKEN_FIELD,
                "location": "header",
                "type": "String",
                "required": True,
            },
        ],
        "success": {
            "code_path": "code",
            "message_path": "message",
            "data_path": "data",
            "values": [200],
        },
    }


def root_case(body):
    return {
        "operation_id": OP_ID,
        "kwargs": {"payload": body},
        "fields": {"X-Trace-Id": TRACE_ID},
        "json_body": body,
        "data": {"marker": "synthetic", "index": 1},
    }


def bindings_doc():
    return {
        "schema_version": 1,
        "bindings": [
            {
                "operation_id": OP_ID,
                "region": "domestic",
                "source_doc_id": "SYN-ROOT",
                "module": "synthetic/root.py",
                "symbols": ["Synthetic.root_set"],
                "offline_status": "synthetic",
                "live_status": "not_verified",
                "public_entry": "synthetic_root",
            }
        ],
    }


def eval_inputs(case=None, contract=None):
    return (
        {"schema_version": 1, "contracts": [auth_contract(), contract or root_contract()]},
        bindings_doc(),
        {"schema_version": 1, "cases": [case if case is not None else root_case({"k": 1})]},
    )


# ---------------------------------------------------------------------------
# Synthetic client substituted for Client/AsyncClient under test
# ---------------------------------------------------------------------------

_UNSET = object()


def _synthetic_operation(read_only=False):
    return types.SimpleNamespace(
        method="PUT",
        path=OP_PATH,
        token_location="header",
        read_only=read_only,
        regions=frozenset({"domestic"}),
    )


def fake_client_classes(tamper=None):
    """Synthetic client pair honouring the whole-body contract.

    ``json=_UNSET`` means no body is emitted at all (Content-Type may still
    be absent); any other value -- including ``None`` -- is serialized as a
    JSON document, so a literal ``null`` body is distinguishable from a
    missing one on the wire.
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
            with httpx.Client(transport=self._auth_transport) as client:
                response = client.post(
                    ORIGIN + AUTH_PATH,
                    data={"appKey": self._app_key, "appSecret": self._app_secret},
                )
            return response.json()["data"]["accessToken"]

        def _send(self, operation, token, json_value):
            headers = {"X-Trace-Id": TRACE_ID, TOKEN_FIELD: token}
            params = {"clientType": "sdk"}
            body = json_value
            send_body = body is not _UNSET
            if tamper == "null_to_empty" and body is None:
                send_body = False
            elif tamper == "root_to_object":
                body = {"wrapped": body}
            elif tamper == "root_to_zero" and body is None:
                body = 0
            elif tamper == "bool_as_int" and isinstance(body, bool):
                body = int(body)
            elif tamper == "int_as_bool" and isinstance(body, int) and not isinstance(body, bool):
                body = bool(body)
            elif tamper == "string_as_number" and isinstance(body, str):
                body = 1
            request_kwargs = {"params": params, "headers": headers}
            if send_body:
                request_kwargs["content"] = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            if tamper == "drop_content_type":
                headers.pop("Content-Type", None)
            with httpx.Client(transport=self._transport_factory(ORIGIN)) as client:
                return client.put(ORIGIN + operation.path, **request_kwargs)

        def _flow(self, operation, payload):
            token = self._get_token()
            response = self._send(operation, token, payload)
            if response.status_code != 200:
                raise make_error(api_error_cls, 500)
            envelope = response.json()
            code = envelope["code"]
            if code == FAULT_AUTH:
                raise make_error(auth_error_cls, 200, invalid_token=True)
            if code == FAULT_PERMISSION:
                raise make_error(permission_error_cls, 200)
            return make_response(response.status_code, envelope)

        def _operation(self):
            return _synthetic_operation()

    class SyncClient(_Base):
        def request(self, operation, **kwargs):
            return self._flow(operation, kwargs["payload"])

        def synthetic_root(self, payload=_UNSET):
            return self.request(self._operation(), payload=payload)

        def close(self):
            return None

    class AsyncClient(_Base):
        async def request(self, operation, **kwargs):
            return self._flow(operation, kwargs["payload"])

        async def synthetic_root(self, payload=_UNSET):
            return await self.request(self._operation(), payload=payload)

        async def aclose(self):
            return None

    return SyncClient, AsyncClient


def _install_fake(monkeypatch, tamper=None):
    import ezviz_unified as package

    sync_cls, async_cls = fake_client_classes(tamper)
    monkeypatch.setattr(package, "Client", sync_cls)
    monkeypatch.setattr(package, "AsyncClient", async_cls)


# ---------------------------------------------------------------------------
# Positive grids: every declared JSON root type evaluates cleanly
# ---------------------------------------------------------------------------


ROOT_BODIES = [
    ("object", {"k": 1, "n": None, "l": [True]}),
    ("object", {}),
    ("array", [1, "two", None, {"x": False}]),
    ("array", []),
    ("string", "literal-body"),
    ("string", ""),
    ("integer", 42),
    ("integer", 0),
    ("number", 2.5),
    ("number", 0.0),
    ("boolean", True),
    ("boolean", False),
    ("null", None),
]


@pytest.mark.parametrize(
    "body_type,body", ROOT_BODIES, ids=[f"{t}-{i}" for i, (t, _) in enumerate(ROOT_BODIES)]
)
def test_json_root_grid_passes_for_declared_type(monkeypatch, body_type, body):
    _install_fake(monkeypatch)
    data = eval_inputs(case=root_case(body), contract=root_contract(body_type))
    before = copy.deepcopy(data)
    report = evaluate(data)
    assert data == before, "evaluate() must not mutate its inputs"
    assert report["passed"], json.dumps(report["results"], indent=2)
    assert report["summary"]["executions"] == 8
    assert all(row["passed"] for row in report["results"])
    assert report["covered_operation_ids"] == [OP_ID]


def test_null_root_is_a_real_body_not_absent(monkeypatch):
    """json_body: null must produce the literal ``b'null'`` body with a JSON
    content type — never an empty request body."""
    _install_fake(monkeypatch)
    report = evaluate(eval_inputs(case=root_case(None), contract=root_contract("null")))
    assert report["passed"], json.dumps(report["results"], indent=2)


def test_required_null_descriptor_with_uncertainty_evaluates(monkeypatch):
    """required: null + required_uncertainty stays evaluable; cases still
    must provide the expected json_body explicitly."""
    _install_fake(monkeypatch)
    contract = root_contract("object", required=None)
    report = evaluate(eval_inputs(case=root_case({"k": 1}), contract=contract))
    assert report["passed"], json.dumps(report["results"], indent=2)


# ---------------------------------------------------------------------------
# Negative wire mutations: wrong root, null/empty confusion, type swaps
# ---------------------------------------------------------------------------


NULL_WIRE_TAMPERS = [
    "null_to_empty",
    "root_to_object",
    "root_to_zero",
    "drop_content_type",
]


@pytest.mark.parametrize("tamper", NULL_WIRE_TAMPERS)
def test_json_null_root_tampering_fails_every_cell(monkeypatch, tamper):
    """On a null-root contract every cell must fail when the wire carries an
    empty body, a wrapped object, a swapped value or a missing Content-Type."""
    _install_fake(monkeypatch, tamper=tamper)
    report = evaluate(eval_inputs(case=root_case(None), contract=root_contract("null")))
    assert not report["passed"]
    assert report["summary"]["passed_executions"] == 0
    assert {row["problem"] for row in report["results"]} == {"request_mismatch"}
    assert OP_ID not in report["covered_operation_ids"]


@pytest.mark.parametrize(
    "declared,body,tamper",
    [
        ("boolean", True, "bool_as_int"),
        ("boolean", False, "bool_as_int"),
        ("integer", 7, "int_as_bool"),
        ("string", "text", "string_as_number"),
    ],
)
def test_json_scalar_root_type_swap_fails(monkeypatch, declared, body, tamper):
    """bool→int, int→bool and str→number swaps must fail strict equality."""
    _install_fake(monkeypatch, tamper=tamper)
    report = evaluate(eval_inputs(case=root_case(body), contract=root_contract(declared)))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"request_mismatch"}


@pytest.mark.parametrize(
    "declared,body",
    [
        ("object", {"k": 1}),
        ("array", [1, 2]),
        ("string", "text"),
        ("integer", 7),
        ("number", 1.5),
        ("boolean", True),
        ("null", None),
    ],
)
def test_json_root_wrapped_in_object_fails(monkeypatch, declared, body):
    """Wrapping any declared root in an object must fail the strict compare."""
    _install_fake(monkeypatch, tamper="root_to_object")
    report = evaluate(eval_inputs(case=root_case(body), contract=root_contract(declared)))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"request_mismatch"}


# ---------------------------------------------------------------------------
# Case-level rejections
# ---------------------------------------------------------------------------


ROOT_CASE_REJECTS = [
    ("missing_json_body", "case_invalid"),
    ("non_json_scalar", "case_invalid"),
    ("nan", "case_invalid"),
    ("negative_infinity", "case_invalid"),
    ("non_string_object_key", "case_invalid"),
    ("decoded_non_json", "case_invalid"),
    ("top_level_token_object", "managed_token_field"),
]


def mutate_root_case(case, kind):
    if kind == "missing_json_body":
        case.pop("json_body")
    elif kind == "non_json_scalar":
        case["json_body"] = b"bytes"
    elif kind == "nan":
        case["json_body"] = float("nan")
    elif kind == "negative_infinity":
        case["json_body"] = float("-inf")
    elif kind == "non_string_object_key":
        case["json_body"] = {1: "x"}
    elif kind == "decoded_non_json":
        case["json_body"] = {"$type": "datetime", "value": "2026-01-01T00:00:00"}
    elif kind == "top_level_token_object":
        case["json_body"] = {"accessToken": "smuggled", "k": 1}
    else:
        raise AssertionError(f"unknown case mutation {kind}")


@pytest.mark.parametrize("kind,problem", ROOT_CASE_REJECTS)
def test_root_case_rejections_emit_fixed_problem(kind, problem):
    body_type = "object" if kind in ("non_string_object_key", "top_level_token_object") else "null"
    body = {"a": 1} if body_type == "object" else None
    case = root_case(body)
    mutate_root_case(case, kind)
    report = evaluate(eval_inputs(case=case, contract=root_contract(body_type)))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {problem}


def test_explicit_null_json_body_is_distinct_from_missing_key(monkeypatch):
    """``json_body: null`` on a ``null`` contract passes the grid; the same
    contract with the key absent is a case defect. The two spellings must
    never be conflated."""
    _install_fake(monkeypatch)
    present = evaluate(eval_inputs(case=root_case(None), contract=root_contract("null")))
    assert present["passed"], json.dumps(present["results"], indent=2)
    case = root_case(None)
    case.pop("json_body")
    report = evaluate(eval_inputs(case=case, contract=root_contract("null")))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"case_invalid"}


def test_json_body_key_is_rejected_without_descriptor():
    """Any ``json_body`` key — including an explicit ``null`` — is illegal on
    a contract that declares no ``request_body``."""
    contract = root_contract("object")
    contract.pop("request_body")
    contract["parameters"].append(
        {"name": "namedBody", "location": "body", "type": "String", "required": False}
    )
    for body in (None, {"k": 1}):
        case = root_case(body)
        report = evaluate(eval_inputs(case=case, contract=contract))
        assert not report["passed"]
        assert {row["problem"] for row in report["results"]} == {"case_invalid"}


def test_null_root_rejects_surrogate_and_nonfinite_inside():
    """Case values that decode to non-JSON scalars stay ``case_invalid`` on
    every root type — a null-root case is no exception."""
    case = root_case(None)
    case["json_body"] = {"$ref": "does_not_exist"}
    report = evaluate(eval_inputs(case=case, contract=root_contract("null")))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"case_invalid"}


# ---------------------------------------------------------------------------
# Contract-level rejections
# ---------------------------------------------------------------------------


ROOT_CONTRACT_REJECTS = [
    "unknown_type",
    "required_missing",
    "required_scalar",
    "required_null_no_reason",
    "required_null_blank_reason",
    "extra_key",
    "form_encoding",
    "json_token_location",
    "form_token_location",
    "body_param_coexists",
]


def mutate_root_contract(contract, kind):
    if kind == "unknown_type":
        contract["request_body"]["type"] = "mystery"
    elif kind == "required_missing":
        contract["request_body"].pop("required")
    elif kind == "required_scalar":
        contract["request_body"]["required"] = "yes"
    elif kind == "required_null_no_reason":
        contract["request_body"]["required"] = None
    elif kind == "required_null_blank_reason":
        contract["request_body"]["required"] = None
        contract["request_body"]["required_uncertainty"] = "  "
    elif kind == "extra_key":
        contract["request_body"]["schema"] = "openapi"
    elif kind == "form_encoding":
        contract["body_encoding"] = "form_urlencoded"
    elif kind == "json_token_location":
        contract["token_location"] = "json"
    elif kind == "form_token_location":
        contract["token_location"] = "form"
        contract["body_encoding"] = "form_urlencoded"
    elif kind == "body_param_coexists":
        contract["parameters"].append(
            {
                "name": "namedBody",
                "location": "body",
                "type": "String",
                "required": False,
            }
        )
    else:
        raise AssertionError(f"unknown contract mutation {kind}")


@pytest.mark.parametrize("kind", ROOT_CONTRACT_REJECTS)
def test_root_contract_rejections_emit_contract_invalid(kind):
    contract = root_contract("null")
    mutate_root_contract(contract, kind)
    report = evaluate(eval_inputs(case=root_case(None), contract=contract))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"contract_invalid"}


# ---------------------------------------------------------------------------
# Direct _check_business_request coverage: raw body byte comparisons
# ---------------------------------------------------------------------------


def send_put(content=None, json_body=_UNSET, headers=None, params=None):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"code": 200})

    kwargs = {}
    if json_body is not _UNSET:
        kwargs["content"] = json.dumps(json_body).encode("utf-8")
    if content is not None:
        kwargs["content"] = content
    merged = dict(headers or {})
    if "content" in kwargs or "json" in kwargs:
        merged.setdefault("Content-Type", "application/json")
    if merged:
        kwargs["headers"] = merged
    if params:
        kwargs["params"] = params
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        client.put(ORIGIN + OP_PATH, **kwargs)
    assert len(captured) == 1
    return captured[0]


def expected_wire(contract, body):
    expected = wire_eval()._expected_wire(
        contract,
        {"X-Trace-Id": TRACE_ID},
        [],
        FIRST_TOKEN,
        body,
    )
    expected["origin"] = ORIGIN
    expected["path"] = OP_PATH
    return expected


def test_business_check_accepts_literal_null_body():
    mod = wire_eval()
    contract = root_contract("null")
    expected = expected_wire(contract, None)
    request = send_put(json_body=None, headers=dict(GOOD_HEADERS), params=dict(GOOD_PARAMS))
    assert request.content == b"null"
    assert mod._check_business_request(request, contract, expected) is None


NULL_WHITESPACE_VARIANTS = [b"null ", b" \nnull\t", b"\tnull\n "]


@pytest.mark.parametrize("raw", NULL_WHITESPACE_VARIANTS)
def test_business_check_accepts_whitespace_padded_null_body(raw):
    """Standard JSON allows insignificant whitespace around the null literal:
    ``b"null "``/``b" \\nnull\\t"`` parse to the same semantic ``None`` and
    must pass the wire oracle."""
    mod = wire_eval()
    contract = root_contract("null")
    expected = expected_wire(contract, None)
    request = send_put(content=raw, headers=dict(GOOD_HEADERS), params=dict(GOOD_PARAMS))
    assert mod._check_business_request(request, contract, expected) is None


def test_business_check_rejects_empty_body_where_null_expected():
    mod = wire_eval()
    contract = root_contract("null")
    expected = expected_wire(contract, None)
    request = send_put(content=b"", headers=dict(GOOD_HEADERS), params=dict(GOOD_PARAMS))
    assert mod._check_business_request(request, contract, expected) == "request_mismatch"


def test_business_check_rejects_null_where_empty_body_expected():
    """A contract without request_body keeps the legacy empty-body rule; a
    literal ``b'null'`` body must fail, not pass as falsy."""
    mod = wire_eval()
    contract = root_contract("object")
    contract.pop("request_body")
    contract["parameters"].append(
        {"name": "namedBody", "location": "body", "type": "String", "required": False}
    )
    expected = mod._expected_wire(
        contract,
        {"X-Trace-Id": TRACE_ID, "namedBody": "v"},
        [],
        FIRST_TOKEN,
        None,
    )
    expected["origin"] = ORIGIN
    expected["path"] = OP_PATH
    request = send_put(
        content=b"null",
        headers={**GOOD_HEADERS, "Content-Type": "application/json"},
        params=dict(GOOD_PARAMS),
    )
    assert mod._check_business_request(request, contract, expected) == "request_mismatch"


BODY_ROOT_TAMPERS = [
    (None, b""),
    (None, b"0"),
    (None, b"false"),
    (None, b'""'),
    (None, b'"null"'),
    (None, b"{}"),
    (None, b"NULL"),
    (None, b"nulll"),
    ("null", b"null"),
    (0, b"0.0"),
    (0, b"false"),
    (True, b"1"),
    ("text", b'"other"'),
    ([1, 2], b"[2, 1]"),
    ({"a": 1}, b'{"a": 1, "a": 2}'),
]


def declared_type_of(body):
    if body is None:
        return "null"
    if isinstance(body, bool):
        return "boolean"
    if isinstance(body, int):
        return "integer"
    if isinstance(body, float):
        return "number"
    if isinstance(body, str):
        return "string"
    if isinstance(body, list):
        return "array"
    return "object"


@pytest.mark.parametrize("expected_body,raw", BODY_ROOT_TAMPERS)
def test_business_check_detects_root_tampering(expected_body, raw):
    mod = wire_eval()
    contract = root_contract(declared_type_of(expected_body))
    expected = expected_wire(contract, expected_body)
    request = send_put(content=raw, headers=dict(GOOD_HEADERS), params=dict(GOOD_PARAMS))
    assert mod._check_business_request(request, contract, expected) == "request_mismatch"


# ---------------------------------------------------------------------------
# Registry structural audit coverage
# ---------------------------------------------------------------------------


def audit_inputs(contract):
    contracts = {"schema_version": 1, "contracts": [auth_contract(), contract]}
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
                "source_doc_id": "SYN-ROOT",
                "module": "synthetic/root.py",
                "symbols": ["Synthetic.root_set"],
                "offline_status": "synthetic",
                "live_status": "not_verified",
            },
        ],
    }
    issues = {"schema_version": 1, "issues": []}
    return contracts, bindings, issues


@pytest.mark.parametrize("body_type", sorted({t for t, _ in ROOT_BODIES}))
def test_registry_accepts_every_json_root_type(body_type):
    registry().audit_registry(*audit_inputs(root_contract(body_type)))


def test_registry_accepts_required_null_with_uncertainty():
    registry().audit_registry(*audit_inputs(root_contract("object", required=None)))


@pytest.mark.parametrize("kind", ROOT_CONTRACT_REJECTS)
def test_registry_rejects_illegal_root_descriptor(kind):
    docs = audit_inputs(root_contract("null"))
    mutate_root_contract(docs[0]["contracts"][1], kind)
    with pytest.raises(registry().RegistryError):
        registry().audit_registry(*docs)


def test_registry_rejects_token_in_null_body_json_location():
    docs = audit_inputs(root_contract("null"))
    docs[0]["contracts"][1]["token_location"] = "json"
    with pytest.raises(registry().RegistryError):
        registry().audit_registry(*docs)
