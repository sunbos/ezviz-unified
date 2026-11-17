"""Harness-side conformance for the frozen public area directory contract.

Unlike ``test_harness_public_operations.py`` — which appends a synthetic
fixture contract for the timezone directory — this module evaluates the
*real* frozen ``global.settings.list_areas`` entry in
``contracts/reviewed-transport-contracts.json`` plus its real binding and
its real synthetic case in ``evals/contracts/cases.json``. The evaluator
is the real ``harness.wire_eval``; nothing here stubs or mutates the
registry.

Asserted through the evaluator's 8-cell grid (sync/async x
success/auth_fault/permission_fault/http_fault):

* exactly one GET ``/api/lapp/area/list`` business request with an empty
  query, an empty body, and no ``accessToken`` anywhere on the wire;
* zero authentication requests — the operation never touches the
  developer credential pair the evaluator injects;
* no replay after a business 10002: ``AuthenticationError`` surfaces
  because a public operation is excluded from the retry-refresh branch;
* the request targets the evaluator's configured overseas origin rather
  than a token-derived ``areaDomain``;
* the operation spy sees ``token_location="none"`` / ``read_only=True``
  on the real ``settings.list_areas`` operation.

Also covered here: the frozen registry entries for this operation are
internally consistent, and the diagnostic manifest plus the SDK registry
carry the new entrypoint and both operation names.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = ROOT / "evals" / "contracts" / "cases.json"
CONTRACTS_FILE = ROOT / "contracts" / "reviewed-transport-contracts.json"
BINDINGS_FILE = ROOT / "contracts" / "sdk-bindings.json"
ISSUES_FILE = ROOT / "contracts" / "source-issues.json"
COVERAGE_FILE = ROOT / "docs" / "diagnostics-v1-coverage.json"

OP_ID = "global.settings.list_areas"
OP_NAME = "settings.list_areas"
OP_PATH = "/api/lapp/area/list"
DOC_ID = "469"
SCENARIOS = {"success", "auth_fault", "permission_fault", "http_fault"}
MODES = {"sync", "async"}

CONTRACTS = json.loads(CONTRACTS_FILE.read_text())
BINDINGS = json.loads(BINDINGS_FILE.read_text())
ISSUES = json.loads(ISSUES_FILE.read_text())
CASES = json.loads(CASES_FILE.read_text())
COVERAGE = json.loads(COVERAGE_FILE.read_text())


def _contract():
    matches = [entry for entry in CONTRACTS["contracts"] if entry.get("operation_id") == OP_ID]
    assert len(matches) == 1, f"expected exactly one frozen contract for {OP_ID}"
    return matches[0]


def _binding():
    matches = [entry for entry in BINDINGS["bindings"] if entry.get("operation_id") == OP_ID]
    assert len(matches) == 1, f"expected exactly one frozen binding for {OP_ID}"
    return matches[0]


def _case():
    matches = [c for c in CASES["cases"] if c.get("operation_id") == OP_ID]
    assert len(matches) == 1, f"expected exactly one eval case for {OP_ID}"
    return matches[0]


def _evaluate(contracts, bindings, cases):
    assert (ROOT / "harness" / "wire_eval.py").exists(), "wire evaluator missing"
    return importlib.import_module("harness.wire_eval").evaluate(contracts, bindings, cases, ROOT)


def _only_case_report():
    """Run the evaluator against the full real registry, this case only."""
    cases = {"schema_version": CASES["schema_version"], "cases": [_case()]}
    return _evaluate(CONTRACTS, BINDINGS, cases)


def _rows(report, operation_id):
    return [row for row in report["results"] if row["operation_id"] == operation_id]


# ---------------------------------------------------------------------------
# Frozen registry consistency
# ---------------------------------------------------------------------------


def test_frozen_contract_shape():
    contract = _contract()
    assert contract["region"] == "overseas"
    assert contract["source_doc_id"] == DOC_ID
    assert contract["source_url"] == "https://open.ys7.com/help/en/469"
    assert contract["method"] == "GET"
    assert contract["path"] == OP_PATH
    assert contract["token_location"] == "none"
    assert contract["body_encoding"] == "unknown"
    assert contract["read_only"] is True
    assert contract["parameters"] == []
    assert contract["success"]["values"] == ["200"]
    # A token-free public GET must never declare a managed accessToken field.
    names = [p.get("name") for p in contract["parameters"] if isinstance(p, dict)]
    assert "accessToken" not in names
    # The no-query/no-body and GET-is-an-SDK-choice constraints are recorded.
    facts = " ".join(item["fact"] for item in contract["constraints"])
    assert "no query" in facts and "GET" in facts


def test_frozen_binding_shape():
    binding = _binding()
    assert binding["region"] == "overseas"
    assert binding["source_doc_id"] == DOC_ID
    assert binding["module"] == "src/ezviz_unified/settings.py"
    assert binding["symbols"] == [
        "Settings.list_areas",
        "AsyncSettings.list_areas",
    ]
    assert binding["public_entry"] == OP_NAME


def test_binding_status_values_are_registered_and_consistent():
    """Status fields stay within the registry vocabulary and evidence stays
    honest: ``live_evidence`` is required exactly when a live result is
    claimed, and a candidate binding is never allowed to invent a live path
    the register does not already document. This checks the *rules* of the
    status model, not a frozen point-in-time value, so a legitimate later
    verification does not break the test."""
    allowed_offline = {"pending", "passed", "failed"}
    allowed_live_prefixes = (
        "not_verified",
        "authentication_succeeded",
        "single_device_readonly_response_checked",
        "empty_list_succeeded",
        "public_timezone_catalog_succeeded",
        "public_area_catalog_succeeded",
        "request_failed",
    )
    binding = _binding()
    assert binding["offline_status"] in allowed_offline
    live = binding["live_status"]
    assert any(live == p or live.startswith(p) for p in allowed_live_prefixes), (
        f"unregistered live_status {live!r} for {OP_ID}"
    )
    if live == "not_verified":
        # Nothing claimed live yet → no evidence pointer may exist.
        assert "live_evidence" not in binding
    else:
        # A claimed live result must point at a recorded evidence file.
        evidence = binding.get("live_evidence")
        assert isinstance(evidence, str) and evidence.startswith("docs/validation/"), (
            f"live_status {live!r} lacks a live_evidence path"
        )
        # A live *success* for this directory may only take the documented
        # public-area-catalog form (mirroring the timezone sibling); a live
        # failure keeps the registered generic vocabulary and still needs
        # evidence.
        if "succeeded" in live:
            assert live.startswith("public_area_catalog_succeeded"), (
                f"live_status {live!r} is not the documented public area catalog vocabulary"
            )


def test_source_issues_registered():
    ids = {issue["id"] for issue in ISSUES["issues"]}
    assert f"{OP_ID}.post_variant_undocumented" in ids
    assert f"{OP_ID}.get_method_is_sdk_choice" in ids
    matched = [issue for issue in ISSUES["issues"] if issue["id"].startswith(f"{OP_ID}.")]
    for issue in matched:
        assert issue["region"] == "overseas"
        assert issue["source_doc_id"] == DOC_ID
        assert issue["source_url"] == _contract()["source_url"]
        assert issue["source_content_sha256"] == _contract()["source_content_sha256"]


def test_eval_case_shape():
    case = _case()
    assert case["args"] == []
    assert case["kwargs"] == {}
    assert case["fields"] == {}
    assert "json_body" not in case
    assert isinstance(case["data"], list)
    # Synthetic rows only; documented field names stay present but the
    # fixture also carries extra fields that must survive verbatim.
    for item in case["data"]:
        assert {"id", "name", "region", "telephoneCode"} <= set(item)


# ---------------------------------------------------------------------------
# Diagnostic manifest coverage
# ---------------------------------------------------------------------------


def test_diagnostic_manifest_covers_entrypoint_and_operation():
    assert OP_NAME in COVERAGE["business_entrypoints"]
    assert OP_NAME in COVERAGE["operation_names"]
    assert OP_ID in COVERAGE["operation_names"]
    from ezviz_unified._diagnostic_registry import ENTRYPOINTS, OPERATIONS

    assert OP_NAME in ENTRYPOINTS
    assert {OP_NAME, OP_ID} <= OPERATIONS


# ---------------------------------------------------------------------------
# Real evaluator: the frozen contract through the real SDK
# ---------------------------------------------------------------------------


def test_public_area_grid_passes_with_real_sdk():
    """All 8 cells (sync/async x 4 scenarios) on the frozen contract."""
    report = _only_case_report()
    rows = _rows(report, OP_ID)
    assert len(rows) == 8
    assert {row["mode"] for row in rows} == MODES
    assert {row["scenario"] for row in rows} == SCENARIOS
    failures = [(row["mode"], row["scenario"], row["problem"]) for row in rows if not row["passed"]]
    assert all(row["passed"] for row in rows), failures
    assert OP_ID in report["covered_operation_ids"]
    assert report["evidence"] == "offline_contract_conformance_only"


def test_full_case_file_keeps_evaluating():
    """The added case must not break evaluation of the existing corpus."""
    report = _evaluate(CONTRACTS, BINDINGS, CASES)
    rows = _rows(report, OP_ID)
    assert len(rows) == 8
    assert all(row["passed"] for row in rows), [
        (row["mode"], row["scenario"], row["problem"]) for row in rows if not row["passed"]
    ]
    assert OP_ID in report["covered_operation_ids"]
    # Previously covered operations must still be covered; nothing regresses.
    assert "global.settings.list_timezones" in report["covered_operation_ids"]
    assert "global.token.get" in report["covered_operation_ids"]
