"""Path-template parameter conformance tests for the wire evaluator (P003B).

A synthetic client pair is substituted for ``Client``/``AsyncClient`` via
``monkeypatch``; every request byte is still produced and captured by real
``httpx.MockTransport`` machinery. The expected path is rendered by the
evaluator's own percent-encoder from the contract template and case values
— never by SDK code — and compared against the raw (encoded) path of the
captured request, so an encoded separator can never pass as a real path
boundary. The synthetic contracts and cases in this file never enter the
real registry or coverage statistics.
"""

from __future__ import annotations

import copy
import importlib
import json
import types
from pathlib import Path
from urllib.parse import quote_from_bytes

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]

AUTH_ID = "cn.token.get"
OP_ID = "cn.synthetic.voice_tip.get"
AUTH_PATH = "/api/synthetic/token/get"
OP_TEMPLATE = "/api/v3/otap/prop/{deviceSerial}/Video/1/DoorLockCtrl/Switch"
OP_PLAIN_PATH = "/api/v3/otap/prop/EV1234567/Video/1/DoorLockCtrl/Switch"
ORIGIN = "https://open.ys7.com"
TOKEN_FIELD = "accessToken"
FIRST_TOKEN = "at.EVAL_TOKEN_1"
TRACE_ID = "trace-synthetic-path"
SHA = "d" * 64
FAULT_AUTH = 10002
FAULT_PERMISSION = 10031
# A serial that exercises real percent-encoding: UTF-8 bytes, a raw '%',
# and characters outside the unreserved set.
WEIRD_SERIAL = "EV 12%é"
WEIRD_SEGMENT = quote_from_bytes(WEIRD_SERIAL.encode("utf-8"), safe="")
# A serial made of sub-delims/'@' that httpx keeps verbatim in a raw path but
# the encoder must escape, so an unencoded wire emission is distinguishable.
RESERVED_SERIAL = "EV+12;@="
RESERVED_SEGMENT = quote_from_bytes(RESERVED_SERIAL.encode("utf-8"), safe="")
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


def path_contract(read_only=True):
    return {
        "operation_id": OP_ID,
        "region": "domestic",
        "source_doc_id": "SYN-PATH",
        "source_url": "https://open.ys7.com/help/synthetic-path",
        "source_content_sha256": SHA,
        "method": "GET",
        "path": OP_TEMPLATE,
        "read_only": read_only,
        "body_encoding": "json",
        "token_location": "header",
        "parameters": [
            {
                "name": "deviceSerial",
                "location": "path",
                "type": "String",
                "required": True,
            },
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


def path_case(serial={"$ref": "device_serial"}):
    """A case whose call argument and expected path segment stay consistent.

    The synthetic client sends ``args[0]`` as the device serial, so the same
    raw ``serial`` value must fill both ``args`` and ``path_fields`` — the
    evaluator decodes them through the same context and renders the expected
    path independently.
    """
    return {
        "operation_id": OP_ID,
        "args": [serial],
        "path_fields": {"deviceSerial": serial},
        "fields": {"X-Trace-Id": TRACE_ID},
        "data": {"marker": "synthetic", "index": 1},
    }


def bindings_doc():
    return {
        "schema_version": 1,
        "bindings": [
            {
                "operation_id": OP_ID,
                "region": "domestic",
                "source_doc_id": "SYN-PATH",
                "module": "synthetic/path.py",
                "symbols": ["Synthetic.voice_tip"],
                "offline_status": "synthetic",
                "live_status": "not_verified",
                "public_entry": "synthetic_voice_tip",
            }
        ],
    }


def eval_inputs(case=None, contract=None, read_only=True):
    return (
        {
            "schema_version": 1,
            "contracts": [
                auth_contract(),
                contract if contract is not None else path_contract(read_only),
            ],
        },
        bindings_doc(),
        {"schema_version": 1, "cases": [case if case is not None else path_case()]},
    )


def expected_serial_for(case):
    """The serial the fake client must place on the wire for a case."""
    fields = case["path_fields"]["deviceSerial"]
    return "EV1234567" if fields == {"$ref": "device_serial"} else fields


# ---------------------------------------------------------------------------
# Synthetic client substituted for Client/AsyncClient under test
# ---------------------------------------------------------------------------


def _synthetic_operation(read_only, path=OP_TEMPLATE):
    return types.SimpleNamespace(
        method="GET",
        path=path,  # the unexpanded template, exactly like Operation.path
        token_location="header",
        read_only=read_only,
        regions=frozenset({"domestic"}),
    )


def fake_client_classes(read_only=True, tamper=None, op_path=OP_TEMPLATE):
    """Build synthetic Client/AsyncClient classes for evaluate().

    ``tamper`` introduces exactly one deliberate wire deviation per run;
    without it the emitted request is contract-correct in every scenario
    and mode, including the independently expected percent-encoded path.
    ``op_path`` lets a fixed-path contract see a matching Operation.path.
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

        def _send(self, operation, token, serial):
            headers = {"X-Trace-Id": TRACE_ID, TOKEN_FIELD: token}
            params = {"clientType": "sdk"}
            segment = quote_from_bytes(serial.encode("utf-8"), safe="")
            if tamper == "unencoded_value":
                segment = serial
            elif tamper == "double_encoded":
                segment = quote_from_bytes(segment.encode("utf-8"), safe="")
            elif tamper == "swapped_slash":
                segment = serial.replace("EV", "E/V")
            elif tamper == "dot_segments":
                segment = serial + "/.."
            elif tamper == "lower_template":
                segment = segment.lower()
            path = operation.path.replace("{deviceSerial}", segment)
            if tamper == "unexpanded_template":
                path = operation.path
            elif tamper == "missing_segment":
                path = path.replace("/DoorLockCtrl", "")
            with httpx.Client(transport=self._transport_factory(ORIGIN)) as client:
                return client.get(ORIGIN + path, params=params, headers=headers)

        def _flow(self, operation, serial):
            token = self._get_token()
            response = self._send(operation, token, serial)
            if response.status_code != 200:
                raise make_error(api_error_cls, 500)
            envelope = response.json()
            code = envelope["code"]
            if code == FAULT_AUTH:
                if not operation.read_only:
                    raise make_error(auth_error_cls, 200, invalid_token=True)
                token = self._get_token()
                response = self._send(operation, token, serial)
                if response.status_code != 200:
                    raise make_error(api_error_cls, 500)
                envelope = response.json()
            elif code == FAULT_PERMISSION:
                raise make_error(permission_error_cls, 200)
            return make_response(response.status_code, envelope)

        def _operation(self):
            return _synthetic_operation(read_only, op_path)

    class SyncClient(_Base):
        def request(self, operation, **kwargs):
            return self._flow(operation, kwargs["serial"])

        def synthetic_voice_tip(self, serial):
            return self.request(self._operation(), serial=serial)

        def close(self):
            return None

    class AsyncClient(_Base):
        async def request(self, operation, **kwargs):
            return self._flow(operation, kwargs["serial"])

        async def synthetic_voice_tip(self, serial):
            return await self.request(self._operation(), serial=serial)

        async def aclose(self):
            return None

    return SyncClient, AsyncClient


def _install_fake(monkeypatch, read_only=True, tamper=None, op_path=OP_TEMPLATE):
    import ezviz_unified as package

    sync_cls, async_cls = fake_client_classes(read_only, tamper, op_path)
    monkeypatch.setattr(package, "Client", sync_cls)
    monkeypatch.setattr(package, "AsyncClient", async_cls)


# ---------------------------------------------------------------------------
# Positive grids: encoded path is verified, not just reported
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("read_only", [True, False])
def test_path_template_grid_passes_with_encoded_serial(monkeypatch, read_only):
    _install_fake(monkeypatch, read_only)
    data = eval_inputs(read_only=read_only)
    before = copy.deepcopy(data)
    report = evaluate(data)
    assert data == before, "evaluate() must not mutate its inputs"
    assert report["evidence"] == "offline_contract_conformance_only"
    assert report["passed"], json.dumps(report["results"], indent=2)
    assert report["summary"]["executions"] == 8
    assert all(row["passed"] for row in report["results"])
    assert report["covered_operation_ids"] == [OP_ID]


def test_encoded_serial_in_path_fields_passes_and_escapes(monkeypatch):
    """A value needing real percent-encoding must render identically in the
    oracle path and on the wire: ``EV 12%é`` becomes ``EV%2012%25%C3%A9``."""
    _install_fake(monkeypatch)
    case = path_case(serial=WEIRD_SERIAL)
    report = evaluate(eval_inputs(case=case))
    assert report["passed"], json.dumps(report["results"], indent=2)


def test_same_field_name_as_header_is_independent(monkeypatch):
    """A contract header parameter may share the placeholder name; the case
    keeps the two buckets independent (fields vs path_fields)."""
    _install_fake(monkeypatch)
    contract = path_contract()
    contract["parameters"].append(
        {
            "name": "deviceSerial",
            "location": "header",
            "type": "String",
            "required": True,
        }
    )
    case = path_case()
    case["fields"]["deviceSerial"] = "header-shadow-value"
    report = evaluate(eval_inputs(case=case, contract=contract))
    # The synthetic client does not send a deviceSerial header, so the
    # oracle must fail the run rather than conflate the two buckets.
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"request_mismatch"}


# ---------------------------------------------------------------------------
# Negative wire mutations: every cell must fail with request_mismatch
# ---------------------------------------------------------------------------


WIRE_TAMPERS = [
    "unencoded_value",
    "double_encoded",
    "swapped_slash",
    "dot_segments",
    "lower_template",
    "unexpanded_template",
    "missing_segment",
]


@pytest.mark.parametrize("tamper", WIRE_TAMPERS)
def test_path_wire_tampering_fails_every_cell(monkeypatch, tamper):
    """Each tamper must change real wire bytes: the serial carries sub-delims
    and ``@`` so that skipping or repeating the encoder produces a raw path
    distinct from the single-encoding expectation."""
    _install_fake(monkeypatch, tamper=tamper)
    report = evaluate(eval_inputs(case=path_case(serial=RESERVED_SERIAL)))
    assert not report["passed"]
    assert report["summary"]["passed_executions"] == 0
    assert report["summary"]["failed_executions"] == 8
    assert {row["problem"] for row in report["results"]} == {"request_mismatch"}
    assert OP_ID not in report["covered_operation_ids"]


def test_raw_percent_encodes_exactly_once(monkeypatch):
    """A literal ``%`` is data: ``a%2Fb`` must encode to ``a%252Fb`` — never
    decoded into a separator first, never left as a second decoding layer."""
    _install_fake(monkeypatch)
    case = path_case(serial="a%2Fb")
    report = evaluate(eval_inputs(case=case))
    assert report["passed"], json.dumps(report["results"], indent=2)
    mod = wire_eval()
    assert mod._render_expected_path(OP_TEMPLATE, {"deviceSerial": "a%2Fb"}) == (
        OP_TEMPLATE.replace("{deviceSerial}", "a%252Fb")
    )


def test_surrounding_whitespace_is_preserved_and_encoded(monkeypatch):
    """``" EV 12 "`` is not blank: the whitespace is data, encoded to
    ``%20EV%2012%20`` verbatim on the wire."""
    _install_fake(monkeypatch)
    case = path_case(serial=" EV 12 ")
    report = evaluate(eval_inputs(case=case))
    assert report["passed"], json.dumps(report["results"], indent=2)


def test_encode_path_segment_rejections_carry_no_chain():
    """A lone surrogate must surface a bare ``_Reject``: the
    ``UnicodeEncodeError`` (which embeds the raw sensitive value) may not be
    retained on ``__context__`` or ``__cause__``."""
    mod = wire_eval()
    try:
        mod._encode_path_segment("PRIVATE\ud800MARKER")
    except mod._Reject as exc:
        assert exc.__context__ is None
        assert exc.__cause__ is None
        assert "PRIVATE" not in str(exc)
        assert "MARKER" not in str(exc)
    else:
        raise AssertionError("lone surrogate must be rejected")


def test_encode_path_segment_rules_match_contract():
    """Direct oracle checks: non-strings, blanks, dot segments, raw
    separators and control characters are all fixed ``case_invalid``
    rejections; ordinary values encode once with an empty safe set."""
    mod = wire_eval()
    for bad in (7, None, "", "   ", ".", "..", "a/b", "a\\b", "x\x00y", "x\x9fy"):
        with pytest.raises(mod._Reject):
            mod._encode_path_segment(bad)
    assert mod._encode_path_segment("EV1234567") == "EV1234567"
    assert mod._encode_path_segment("a%2Fb") == "a%252Fb"
    assert mod._encode_path_segment(" EV ") == "%20EV%20"


# ---------------------------------------------------------------------------
# Case-level rejections (fixed problem codes, grids emitted per case)
# ---------------------------------------------------------------------------


PATH_CASE_REJECTS = [
    ("extra_key", "case_invalid"),
    ("missing_key", "case_invalid"),
    ("wrong_placeholder", "case_invalid"),
    ("non_string_value", "case_invalid"),
    ("empty_value", "case_invalid"),
    ("whitespace_value", "case_invalid"),
    ("dot_value", "case_invalid"),
    ("dotdot_value", "case_invalid"),
    ("slash_value", "case_invalid"),
    ("backslash_value", "case_invalid"),
    ("control_value", "case_invalid"),
    ("c1_control_value", "case_invalid"),
    ("del_value", "case_invalid"),
    ("lone_surrogate", "case_invalid"),
    ("non_dict", "case_invalid"),
    ("non_string_keys", "case_invalid"),
]


def mutate_path_case(case, kind):
    if kind == "extra_key":
        case["path_fields"]["rogue"] = "x"
    elif kind == "missing_key":
        case["path_fields"] = {}
    elif kind == "wrong_placeholder":
        case["path_fields"] = {"serial": "EV1234567"}
    elif kind == "non_string_value":
        case["path_fields"]["deviceSerial"] = 7
    elif kind == "empty_value":
        case["path_fields"]["deviceSerial"] = ""
    elif kind == "whitespace_value":
        # Only an all-whitespace value is blank; surrounding whitespace on a
        # non-blank value is preserved and encoded instead.
        case["path_fields"]["deviceSerial"] = " \t "
    elif kind == "c1_control_value":
        case["path_fields"]["deviceSerial"] = "a\x85b"
    elif kind == "del_value":
        case["path_fields"]["deviceSerial"] = "a\x7fb"
    elif kind == "lone_surrogate":
        case["path_fields"]["deviceSerial"] = "a\ud800b"
    elif kind == "dot_value":
        case["path_fields"]["deviceSerial"] = "."
    elif kind == "dotdot_value":
        case["path_fields"]["deviceSerial"] = ".."
    elif kind == "slash_value":
        case["path_fields"]["deviceSerial"] = "a/b"
    elif kind == "backslash_value":
        case["path_fields"]["deviceSerial"] = "a\\b"
    elif kind == "control_value":
        case["path_fields"]["deviceSerial"] = "a\nb"
    elif kind == "non_dict":
        case["path_fields"] = ["deviceSerial"]
    elif kind == "non_string_keys":
        case["path_fields"] = {7: "x"}
    else:
        raise AssertionError(f"unknown case mutation {kind}")


@pytest.mark.parametrize("kind,problem", PATH_CASE_REJECTS)
def test_path_case_rejections_emit_fixed_problem(kind, problem):
    case = path_case()
    mutate_path_case(case, kind)
    report = evaluate(eval_inputs(case=case))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {problem}


# ---------------------------------------------------------------------------
# Contract-level rejections (template / parameter inconsistencies)
# ---------------------------------------------------------------------------


PATH_CONTRACT_REJECTS = [
    "param_without_placeholder",
    "placeholder_without_param",
    "path_param_not_required",
    "access_token_placeholder",
    "access_token_path_param",
    "nested_braces",
    "partial_segment",
    "unbalanced_open",
    "unbalanced_close",
    "empty_placeholder",
    "expression_placeholder",
    "static_dot_segment",
]


def mutate_path_contract(contract, kind):
    if kind == "param_without_placeholder":
        contract["parameters"].append(
            {"name": "extra", "location": "path", "type": "String", "required": True}
        )
    elif kind == "placeholder_without_param":
        contract["parameters"] = [p for p in contract["parameters"] if p.get("location") != "path"]
    elif kind == "path_param_not_required":
        for p in contract["parameters"]:
            if p.get("location") == "path":
                p["required"] = False
    elif kind == "access_token_placeholder":
        contract["path"] = "/api/x/{accessToken}/y"
        for p in contract["parameters"]:
            if p.get("location") == "path":
                p["name"] = "accessToken"
    elif kind == "access_token_path_param":
        for p in contract["parameters"]:
            if p.get("location") == "path":
                p["name"] = "accessToken"
        contract["path"] = "/api/x/{other}/y"
    elif kind == "nested_braces":
        contract["path"] = "/api/x/{{deviceSerial}}/y"
    elif kind == "partial_segment":
        contract["path"] = "/api/x/dev-{deviceSerial}/y"
    elif kind == "unbalanced_open":
        contract["path"] = "/api/x/{deviceSerial/y"
    elif kind == "unbalanced_close":
        contract["path"] = "/api/x/deviceSerial}/y"
    elif kind == "empty_placeholder":
        contract["path"] = "/api/x/{}/y"
    elif kind == "expression_placeholder":
        contract["path"] = "/api/x/{deviceSerial[0]}/y"
    elif kind == "static_dot_segment":
        contract["path"] = "/api/x/../y"
    else:
        raise AssertionError(f"unknown contract mutation {kind}")


@pytest.mark.parametrize("kind", PATH_CONTRACT_REJECTS)
def test_path_contract_rejections_emit_contract_invalid(kind):
    contract = path_contract()
    mutate_path_contract(contract, kind)
    report = evaluate(eval_inputs(contract=contract))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"contract_invalid"}


def test_fixed_path_contracts_without_placeholders_stay_supported(monkeypatch):
    """A template-free contract keeps evaluating exactly as before."""
    _install_fake(monkeypatch, op_path=OP_PLAIN_PATH)
    contract = path_contract()
    contract["path"] = OP_PLAIN_PATH
    contract["parameters"] = [p for p in contract["parameters"] if p.get("location") != "path"]
    case = path_case()
    case.pop("path_fields")
    report = evaluate(eval_inputs(case=case, contract=contract))
    assert report["passed"], json.dumps(report["results"], indent=2)


def test_repeated_placeholder_name_renders_both_segments():
    """A template may repeat one placeholder name; a single required path
    parameter backs every occurrence and each segment renders the same
    independently encoded value."""
    mod = wire_eval()
    contract = path_contract()
    contract["path"] = "/api/v3/otap/{deviceSerial}/mirror/{deviceSerial}"
    mod._check_path_template(contract)  # one path param still matches the set
    rendered = mod._render_expected_path(contract["path"], {"deviceSerial": "a%2Fb"})
    assert rendered == "/api/v3/otap/a%252Fb/mirror/a%252Fb"
    # The registry auditor applies the same placeholder-set rule.
    docs = audit_inputs()
    docs[0]["contracts"][1]["path"] = contract["path"]
    registry().audit_registry(*docs)


def test_wire_eval_rejects_repeated_placeholder_without_param():
    """The repeated name still requires the path parameter to exist."""
    contract = path_contract()
    contract["path"] = "/api/v3/otap/{deviceSerial}/mirror/{other}"
    with pytest.raises(wire_eval()._Reject):
        wire_eval()._check_path_template(contract)


# ---------------------------------------------------------------------------
# Registry structural audit coverage for path templates
# ---------------------------------------------------------------------------


def audit_inputs():
    contracts = {"schema_version": 1, "contracts": [auth_contract(), path_contract()]}
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
                "source_doc_id": "SYN-PATH",
                "module": "synthetic/path.py",
                "symbols": ["Synthetic.voice_tip"],
                "offline_status": "synthetic",
                "live_status": "not_verified",
            },
        ],
    }
    issues = {"schema_version": 1, "issues": []}
    return contracts, bindings, issues


def test_registry_accepts_path_template_contract():
    report = registry().audit_registry(*audit_inputs())
    assert report["summary"]["contracts"] == 2


@pytest.mark.parametrize("kind", PATH_CONTRACT_REJECTS)
def test_registry_rejects_illegal_path_template_combinations(kind):
    docs = audit_inputs()
    mutate_path_contract(docs[0]["contracts"][1], kind)
    with pytest.raises(registry().RegistryError):
        registry().audit_registry(*docs)


def test_registry_accepts_placeholder_name_shared_with_query():
    """A query parameter may share the placeholder name: ``(location, name)``
    pairs stay distinct buckets, exactly like the independent header case.
    The audit must accept the combination rather than conflate locations."""
    docs = audit_inputs()
    docs[0]["contracts"][1]["parameters"].append(
        {
            "name": "deviceSerial",
            "location": "query",
            "type": "String",
            "required": True,
        }
    )
    registry().audit_registry(*docs)


def test_evaluator_keeps_placeholder_name_buckets_independent(monkeypatch):
    """The evaluator verifies path_fields and a same-named query parameter
    separately: the case supplies the query bucket through ``fields`` while
    the fake client only fills the path bucket, so the query value on the
    wire is missing and must fail as a request mismatch — the path value
    never satisfies the query bucket."""
    _install_fake(monkeypatch)
    contract = path_contract()
    contract["parameters"].append(
        {
            "name": "deviceSerial",
            "location": "query",
            "type": "String",
            "required": True,
        }
    )
    case = path_case()
    case["fields"]["deviceSerial"] = "EV1234567"
    report = evaluate(eval_inputs(case=case, contract=contract))
    assert not report["passed"]
    assert {row["problem"] for row in report["results"]} == {"request_mismatch"}


# ---------------------------------------------------------------------------
# Direct raw-path oracle checks via real MockTransport requests
# ---------------------------------------------------------------------------


def send_get(path, headers=None, params=None):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"code": 200})

    kwargs = {}
    if headers:
        kwargs["headers"] = headers
    if params:
        kwargs["params"] = params
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        client.get(ORIGIN + path, **kwargs)
    assert len(captured) == 1
    return captured[0]


def expected_wire_for(contract, path):
    expected = wire_eval()._expected_wire(
        contract,
        {"X-Trace-Id": TRACE_ID},
        [],
        FIRST_TOKEN,
        None,
    )
    expected["origin"] = ORIGIN
    expected["path"] = path
    return expected


def test_oracle_compares_raw_encoded_path_bytes():
    mod = wire_eval()
    contract = path_contract()
    rendered = OP_TEMPLATE.replace("{deviceSerial}", WEIRD_SEGMENT)
    expected = expected_wire_for(contract, rendered)
    request = send_get(rendered, headers=dict(GOOD_HEADERS), params=dict(GOOD_PARAMS))
    assert mod._check_business_request(request, contract, expected) is None


def test_oracle_rejects_decoded_path_view_confusion():
    """Comparing against the decoded url.path would hide encoding errors;
    the oracle must reject a request whose decoded path matches but whose
    raw path carries an unencoded value."""
    mod = wire_eval()
    contract = path_contract()
    rendered = OP_TEMPLATE.replace("{deviceSerial}", WEIRD_SEGMENT)
    expected = expected_wire_for(contract, rendered)
    # httpx normalizes/decodes in url.path; a request built from the raw
    # unencoded serial must still mismatch because raw bytes differ.
    request = send_get(
        OP_TEMPLATE.replace("{deviceSerial}", "EV%2012%25%C3%A9".lower()),
        headers=dict(GOOD_HEADERS),
        params=dict(GOOD_PARAMS),
    )
    assert mod._check_business_request(request, contract, expected) == "request_mismatch"


def test_oracle_encoded_slash_never_splits_segments():
    mod = wire_eval()
    contract = path_contract()
    rendered = OP_TEMPLATE.replace("{deviceSerial}", "a%2Fb")
    expected = expected_wire_for(contract, rendered)
    good = send_get(rendered, headers=dict(GOOD_HEADERS), params=dict(GOOD_PARAMS))
    assert mod._check_business_request(good, contract, expected) is None
    # A request with a real extra segment (a/b unescaped) must not match the
    # same expected path: separators are structural, never data.
    bad = send_get(
        OP_TEMPLATE.replace("{deviceSerial}", "a/b"),
        headers=dict(GOOD_HEADERS),
        params=dict(GOOD_PARAMS),
    )
    assert mod._check_business_request(bad, contract, expected) == "request_mismatch"


def test_oracle_rejects_query_only_path_evasion():
    """Extra query pairs smuggled into the URL are never ignored: the parsed
    query must equal the contract expectation exactly."""
    mod = wire_eval()
    contract = path_contract()
    rendered = OP_TEMPLATE.replace("{deviceSerial}", "EV1234567")
    expected = expected_wire_for(contract, rendered)
    request = send_get(
        rendered + "?clientType=sdk&deviceSerial=forged",
        headers=dict(GOOD_HEADERS),
        params={},
    )
    assert mod._check_business_request(request, contract, expected) == "request_mismatch"


def test_oracle_rejects_missing_segment_and_extra_segment():
    """The rendered path must match byte-for-byte: a dropped static segment
    or an extra real separator segment is a mismatch, not a prefix match."""
    mod = wire_eval()
    contract = path_contract()
    rendered = OP_TEMPLATE.replace("{deviceSerial}", "EV1234567")
    expected = expected_wire_for(contract, rendered)
    good = send_get(rendered, headers=dict(GOOD_HEADERS), params=dict(GOOD_PARAMS))
    assert mod._check_business_request(good, contract, expected) is None
    missing = send_get(
        rendered.replace("/DoorLockCtrl", ""),
        headers=dict(GOOD_HEADERS),
        params=dict(GOOD_PARAMS),
    )
    assert mod._check_business_request(missing, contract, expected) == "request_mismatch"
    extra = send_get(
        rendered + "/extra",
        headers=dict(GOOD_HEADERS),
        params=dict(GOOD_PARAMS),
    )
    assert mod._check_business_request(extra, contract, expected) == "request_mismatch"


# ---------------------------------------------------------------------------
# Content-Type header parameters: mandatory constant on a bodyless request
# ---------------------------------------------------------------------------


def ct_contract():
    """A GET whose contract declares a required Content-Type header constant
    even though no request body travels (body_encoding ``unknown``)."""
    contract = path_contract()
    contract["body_encoding"] = "unknown"
    contract["parameters"].append(
        {
            "name": "Content-Type",
            "location": "header",
            "type": "String",
            "required": True,
            "constant": "application/json",
        }
    )
    return contract


def ct_expected(contract, rendered):
    expected = wire_eval()._expected_wire(
        contract,
        {"X-Trace-Id": TRACE_ID},
        [],
        FIRST_TOKEN,
        None,
    )
    expected["origin"] = ORIGIN
    expected["path"] = rendered
    expected["ct_specs"] = [(True, True, "application/json")]
    return expected


def send_get_with_ct(headers):
    rendered = OP_TEMPLATE.replace("{deviceSerial}", "EV1234567")
    return send_get(rendered, headers=headers, params=dict(GOOD_PARAMS))


def test_oracle_accepts_required_content_type_without_body():
    mod = wire_eval()
    contract = ct_contract()
    rendered = OP_TEMPLATE.replace("{deviceSerial}", "EV1234567")
    expected = ct_expected(contract, rendered)
    request = send_get_with_ct({**GOOD_HEADERS, "Content-Type": "application/json"})
    assert mod._check_business_request(request, contract, expected) is None


@pytest.mark.parametrize(
    "headers",
    [
        dict(GOOD_HEADERS),  # required header missing entirely
        {**GOOD_HEADERS, "Content-Type": "text/plain"},  # wrong constant value
    ],
    ids=["missing", "wrong_value"],
)
def test_oracle_rejects_bad_required_content_type_without_body(headers):
    mod = wire_eval()
    contract = ct_contract()
    rendered = OP_TEMPLATE.replace("{deviceSerial}", "EV1234567")
    expected = ct_expected(contract, rendered)
    request = send_get_with_ct(headers)
    assert mod._check_business_request(request, contract, expected) == "request_mismatch"


def test_oracle_rejects_duplicate_content_type_headers():
    """Two raw Content-Type occurrences are a mismatch even when one equals
    the declared constant."""
    mod = wire_eval()
    contract = ct_contract()
    rendered = OP_TEMPLATE.replace("{deviceSerial}", "EV1234567")
    expected = ct_expected(contract, rendered)
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"code": 200})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        client.get(
            ORIGIN + rendered,
            params=dict(GOOD_PARAMS),
            headers=[
                ("X-Trace-Id", TRACE_ID),
                ("accessToken", FIRST_TOKEN),
                ("Content-Type", "application/json"),
                ("Content-Type", "application/json"),
            ],
        )
    assert len(captured) == 1
    assert mod._check_business_request(captured[0], contract, expected) == "request_mismatch"
