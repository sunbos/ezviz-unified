"""Public (``token_location="none"``) operation conformance via ``wire_eval``.

Drives the real SDK through the real offline evaluator for the overseas
public timezone directory, registered here as ``global.settings.list_timezones``
on a deep copy of the reviewed registry. The contract reuses the existing
doc-477 source identity and binds the real
``Settings.list_timezones`` / ``AsyncSettings.list_timezones`` entry points.

Expected public semantics asserted through the evaluator:

* exactly one business request carrying ``language`` and no ``accessToken``
  anywhere on the wire, and zero authentication requests;
* no token-provider interaction and no replay when the business response
  reports 10002 -- ``AuthenticationError`` must surface even though the
  operation is ``read_only``;
* the business request targets the statically configured overseas API
  origin rather than a token-derived ``areaDomain``.

The real ``cn.token.get`` / ``global.token.get`` cases from
``evals/contracts/cases.json`` are exercised too and must keep evaluating
normally. Negative mutations (a form-injected token, a deliberate replay
after ``AuthenticationError``, and a fabricated ``ApiResponse`` produced
without HTTP) must be rejected and never counted as coverage.
"""

from __future__ import annotations

import copy
import importlib
import json
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = ROOT / "evals" / "contracts" / "cases.json"
CONTRACTS_FILE = ROOT / "contracts" / "reviewed-transport-contracts.json"
BINDINGS_FILE = ROOT / "contracts" / "sdk-bindings.json"

OP_ID = "global.settings.list_timezones"
OP_NAME = "settings.list_timezones"
OP_PATH = "/api/lapp/timezone/list"
DOC_ID = "477"
SCENARIOS = {"success", "auth_fault", "permission_fault", "http_fault"}
MODES = {"sync", "async"}

TIMEZONE_DATA = [
    {"tzCode": "EVAL_TZ_SINGAPORE", "tzValue": "UTC+08:00", "disPlayName": "Eval Singapore"},
    {"tzCode": "EVAL_TZ_SHANGHAI", "tzValue": "UTC+08:00", "disPlayName": "评估时区-上海"},
]


def _evaluate(data):
    assert (ROOT / "harness" / "wire_eval.py").exists(), "wire evaluator missing"
    return importlib.import_module("harness.wire_eval").evaluate(*data, ROOT)


def _load_registry():
    """Deep-copied real registry plus the public list_timezones fixture.

    The doc identity (``source_url`` / ``source_content_sha256``) is taken
    from an existing reviewed doc-477 contract so the appended entries carry
    a real, consistent source fingerprint. Any pre-existing entries for this
    operation are dropped from the deep copy first so the synthetic fixture
    fully controls what this test evaluates; all other real entries are kept.
    """
    contracts = copy.deepcopy(json.loads(CONTRACTS_FILE.read_text()))
    bindings = copy.deepcopy(json.loads(BINDINGS_FILE.read_text()))
    contracts["contracts"] = [
        entry for entry in contracts["contracts"] if entry.get("operation_id") != OP_ID
    ]
    bindings["bindings"] = [
        entry for entry in bindings["bindings"] if entry.get("operation_id") != OP_ID
    ]
    identity = next(
        entry
        for entry in contracts["contracts"]
        if entry["region"] == "overseas" and entry["source_doc_id"] == DOC_ID
    )
    contracts["contracts"].append(
        {
            "operation_id": OP_ID,
            "region": "overseas",
            "source_doc_id": DOC_ID,
            "source_url": identity["source_url"],
            "source_content_sha256": identity["source_content_sha256"],
            "method": "POST",
            "path": OP_PATH,
            "base_url": "{areaDomain}",
            "body_encoding": "form_urlencoded",
            "token_location": "none",
            "parameters": [
                {
                    "name": "language",
                    "location": "body",
                    "type": "String",
                    "required": False,
                    "evidence": "optional language parameter in the doc-477 request table",
                }
            ],
            "success": {
                "code_path": "code",
                "message_path": "msg",
                "data_path": "data",
                "values": ["200"],
                "evidence": "response example",
            },
            "read_only": True,
            "constraints": [
                {
                    "fact": "The public timezone directory request carries no accessToken.",
                    "evidence": "test-defined expectation for this fixture: the operation is evaluated as token-free",
                }
            ],
            "uncertainties": [],
            "transport_evidence": {
                "method": "POST",
                "path": "{areaDomain}" + OP_PATH,
                "encoding": "application/x-www-form-urlencoded",
            },
        }
    )
    bindings["bindings"].append(
        {
            "operation_id": OP_ID,
            "region": "overseas",
            "source_doc_id": DOC_ID,
            "module": "src/ezviz_unified/settings.py",
            "symbols": [
                "Settings.list_timezones",
                "AsyncSettings.list_timezones",
            ],
            "offline_status": "fixture_only",
            "live_status": "not_verified",
            "public_entry": "settings.list_timezones",
        }
    )
    return contracts, bindings


def _case():
    return {
        "operation_id": OP_ID,
        "args": [],
        "kwargs": {"language": "SIMPCN"},
        "fields": {"language": "SIMPCN"},
        "data": copy.deepcopy(TIMEZONE_DATA),
    }


def _real_case(operation_id):
    cases = json.loads(CASES_FILE.read_text())
    matches = [entry for entry in cases["cases"] if entry.get("operation_id") == operation_id]
    assert len(matches) == 1, f"expected exactly one real case for {operation_id}"
    return copy.deepcopy(matches[0])


def _public_inputs():
    contracts, bindings = _load_registry()
    return contracts, bindings, {"schema_version": 1, "cases": [_case()]}


def _rows(report, operation_id):
    return [row for row in report["results"] if row["operation_id"] == operation_id]


def _targets_timezone(operation):
    return (
        getattr(operation, "name", None) == OP_NAME or getattr(operation, "path", None) == OP_PATH
    )


# ---------------------------------------------------------------------------
# Positive integration: real SDK, real evaluator, real registry shape
# ---------------------------------------------------------------------------


def test_real_token_cases_keep_evaluating_normally():
    """The reviewed auth cases must still pass on the extended registry."""
    contracts, bindings = _load_registry()
    cases = {
        "schema_version": 1,
        "cases": [_case(), _real_case("cn.token.get"), _real_case("global.token.get")],
    }
    report = _evaluate((contracts, bindings, cases))
    for operation_id in ("cn.token.get", "global.token.get"):
        rows = _rows(report, operation_id)
        assert len(rows) == 8
        assert all(row["passed"] for row in rows), [
            (row["mode"], row["scenario"], row["problem"]) for row in rows if not row["passed"]
        ]
        assert operation_id in report["covered_operation_ids"]


def test_public_timezone_grid_passes_with_real_sdk():
    """All 8 cells (sync/async x 4 scenarios) of the public operation.

    Semantics asserted through the evaluator: one tokenless business
    request to the static overseas origin, zero auth requests, and a
    surfaced AuthenticationError without replay on the 10002 scenario.
    """
    data = _public_inputs()
    original = copy.deepcopy(data)
    report = _evaluate(data)
    assert data == original, "evaluate() must not mutate its inputs"
    rows = _rows(report, OP_ID)
    assert len(rows) == 8
    assert {row["mode"] for row in rows} == MODES
    assert {row["scenario"] for row in rows} == SCENARIOS
    failures = [(row["mode"], row["scenario"], row["problem"]) for row in rows if not row["passed"]]
    assert all(row["passed"] for row in rows), failures
    assert report["covered_operation_ids"] == [OP_ID]
    assert report["passed"]
    assert report["evidence"] == "offline_contract_conformance_only"


# ---------------------------------------------------------------------------
# Negative mutations: bad SDK behaviour must be rejected, never covered
# ---------------------------------------------------------------------------


def test_operation_mutated_to_form_token_is_rejected(monkeypatch):
    """Injecting accessToken into the form of a public op must fail the wire
    check (or trip the zero-auth request bound once counting exists)."""
    from ezviz_unified import AsyncClient, Client

    sync_request = Client.request
    async_request = AsyncClient.request

    def _mutate(operation):
        if _targets_timezone(operation):
            return replace(operation, token_location="form")
        return operation

    def patched_sync(self, operation, **kwargs):
        return sync_request(self, _mutate(operation), **kwargs)

    async def patched_async(self, operation, **kwargs):
        return await async_request(self, _mutate(operation), **kwargs)

    monkeypatch.setattr(Client, "request", patched_sync)
    monkeypatch.setattr(AsyncClient, "request", patched_async)

    report = _evaluate(_public_inputs())
    rows = _rows(report, OP_ID)
    assert not report["passed"]
    assert OP_ID not in report["covered_operation_ids"]
    assert len(rows) == 8
    assert all(not row["passed"] for row in rows)
    assert {row["problem"] for row in rows} <= {
        "request_mismatch",
        "request_limit_exceeded",
        "operation_mismatch",
    }


def test_replay_after_authentication_error_exceeds_request_limit(monkeypatch):
    """A facade that swallows AuthenticationError and re-issues the same
    operation must hit the bounded request limit on the auth_fault cells."""
    from ezviz_unified import AsyncClient, AuthenticationError, Client

    sync_request = Client.request
    async_request = AsyncClient.request

    def patched_sync(self, operation, **kwargs):
        try:
            return sync_request(self, operation, **kwargs)
        except AuthenticationError:
            if not _targets_timezone(operation):
                raise
            return sync_request(self, operation, **kwargs)

    async def patched_async(self, operation, **kwargs):
        try:
            return await async_request(self, operation, **kwargs)
        except AuthenticationError:
            if not _targets_timezone(operation):
                raise
            return await async_request(self, operation, **kwargs)

    monkeypatch.setattr(Client, "request", patched_sync)
    monkeypatch.setattr(AsyncClient, "request", patched_async)

    report = _evaluate(_public_inputs())
    rows = _rows(report, OP_ID)
    assert not report["passed"]
    assert OP_ID not in report["covered_operation_ids"]
    auth_rows = [row for row in rows if row["scenario"] == "auth_fault"]
    assert len(auth_rows) == 2
    assert {row["problem"] for row in auth_rows} == {"request_limit_exceeded"}
    other_rows = [row for row in rows if row["scenario"] != "auth_fault"]
    assert all(row["passed"] for row in other_rows), [
        (row["mode"], row["scenario"], row["problem"]) for row in other_rows if not row["passed"]
    ]


def test_faked_response_without_http_is_not_coverage(monkeypatch):
    """A facade returning a fabricated ApiResponse without any HTTP request
    must fail dispatch verification rather than count as coverage."""
    from ezviz_unified import ApiResponse
    from ezviz_unified.settings import AsyncSettings, Settings

    fake = ApiResponse(200, "200", data=copy.deepcopy(TIMEZONE_DATA))

    def fake_sync(self, *, language=None):
        return fake

    async def fake_async(self, *, language=None):
        return fake

    monkeypatch.setattr(Settings, "list_timezones", fake_sync)
    monkeypatch.setattr(AsyncSettings, "list_timezones", fake_async)

    report = _evaluate(_public_inputs())
    rows = _rows(report, OP_ID)
    assert not report["passed"]
    assert OP_ID not in report["covered_operation_ids"]
    assert len(rows) == 8
    assert {row["problem"] for row in rows} == {"operation_dispatch"}
