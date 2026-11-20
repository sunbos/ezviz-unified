import copy
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from harness import contract_eval, wire_eval

ROOT = Path(__file__).resolve().parents[1]
OPERATION_ID = "global.oauth.refresh_token"
OTHER_TRUSTED_ORIGIN = "api.ezvizlife.com"
MARKER = "FIXED_MARKER_MUST_NOT_LEAK"


def evaluate(data):
    return wire_eval.evaluate(*data, ROOT)


def inputs(operation_id=OPERATION_ID):
    contracts = json.loads((ROOT / "contracts/reviewed-transport-contracts.json").read_text())
    bindings = json.loads((ROOT / "contracts/sdk-bindings.json").read_text())
    cases = json.loads((ROOT / "evals/contracts/cases.json").read_text())
    cases["cases"] = [c for c in cases["cases"] if c["operation_id"] == operation_id]
    assert len(cases["cases"]) == 1
    return contracts, bindings, cases


@pytest.mark.parametrize("edit", ["missing", "legacy", "false_complete"])
def test_validate_report_rejects_corruption(edit):
    contracts, _, cases = data = inputs()
    report = evaluate(data)
    contract_eval._validate_report(report, contracts, cases)
    corrupted = copy.deepcopy(report)
    if edit == "missing":
        corrupted["results"].pop()
    elif edit == "legacy":
        row = next(r for r in corrupted["results"] if r["scenario"] == "refresh_expired")
        row["scenario"] = "auth_fault"
    else:
        corrupted["coverage_complete"] = True
    with pytest.raises(contract_eval.EvaluationError):
        contract_eval._validate_report(corrupted, contracts, cases)


def test_require_all_rejects_missing_refresh(monkeypatch):
    report = evaluate(inputs("global.token.get"))
    assert report["passed"]
    assert OPERATION_ID in report["missing_operation_ids"]
    monkeypatch.setattr(contract_eval, "run_evaluation", lambda *a, **k: report)
    assert contract_eval.main(["--require-all"]) == 1
    assert contract_eval.main([]) == 0


def failed_cells(report):
    return {
        (row["mode"], row["scenario"], row["problem"])
        for row in report["results"]
        if not row["passed"]
    }


def test_canonical_refresh_case_passes_all_eight_named_rows():
    data = inputs()
    original = copy.deepcopy(data)
    report = evaluate(data)
    assert data == original
    assert report["passed"] and not report["coverage_complete"]
    assert report["covered_operation_ids"] == [OPERATION_ID]
    assert report["summary"]["executions"] == 8
    assert report["summary"]["failed_executions"] == 0
    assert len(report["results"]) == 8
    assert {(r["mode"], r["scenario"]) for r in report["results"]} == {
        (mode, scenario)
        for mode in ("sync", "async")
        for scenario in ("success", "refresh_expired", "binding_mismatch", "http_fault")
    }
    assert all(r["passed"] for r in report["results"])
    assert "safe_to_publish" not in report


def test_refresh_lane_needs_no_external_auth_contract_or_binding():
    contracts, bindings, cases = inputs()
    contracts["contracts"] = [
        c for c in contracts["contracts"] if c["operation_id"] == OPERATION_ID
    ]
    bindings["bindings"] = [b for b in bindings["bindings"] if b["operation_id"] == OPERATION_ID]
    report = evaluate((contracts, bindings, cases))
    assert report["passed"] and report["coverage_complete"]
    assert report["covered_operation_ids"] == [OPERATION_ID]


def test_fixed_markers_never_appear_in_failing_report():
    data = inputs()
    data[2]["cases"][0]["fields"]["open_id"] = MARKER
    report = evaluate(data)
    assert not report["passed"] and not report["covered_operation_ids"]
    assert MARKER not in json.dumps(report)
    assert {r["problem"] for r in report["results"]} == {"request_mismatch"}


def test_sync_fake_success_without_transport_request_is_not_coverage(monkeypatch):
    from ezviz_unified.oauth import OAuth, OAuthRefreshResult

    monkeypatch.setattr(
        OAuth,
        "refresh_token",
        lambda self, *args, **kwargs: OAuthRefreshResult(
            access_token="FAKE_ACCESS_TOKEN",
            refresh_token="FAKE_REFRESH_TOKEN",
            open_id=None,
            expires_in=7200,
            scope=None,
            state=None,
        ),
    )
    report = evaluate(inputs())
    assert not report["passed"]
    assert OPERATION_ID not in report["covered_operation_ids"]
    assert all(
        r["problem"] == "request_count_mismatch" for r in report["results"] if r["mode"] == "sync"
    )


@pytest.mark.parametrize("mutation", ["other_origin", "query_field"])
def test_changed_request_url_fails_wire_check(monkeypatch, mutation):
    original_send = httpx.Client.send

    def send(self, request, *args, **kwargs):
        if mutation == "other_origin":
            request.url = request.url.copy_with(host=OTHER_TRUSTED_ORIGIN)
        else:
            request.url = request.url.copy_set_param("openId", "LEGACY_OPEN_ID")
        return original_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    report = evaluate(inputs())
    assert not report["passed"] and OPERATION_ID not in report["covered_operation_ids"]
    assert {r["problem"] for r in report["results"] if r["mode"] == "sync"} == {"request_mismatch"}


def test_replayed_request_is_detected(monkeypatch):
    original_send = httpx.Client.send

    def send(self, request, *args, **kwargs):
        original_send(self, request, *args, **kwargs)
        return original_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    report = evaluate(inputs())
    assert not report["passed"]
    assert {r["problem"] for r in report["results"] if r["mode"] == "sync"} == {
        "request_limit_exceeded"
    }


@pytest.mark.parametrize("mutation", ["wrong_dto", "wrong_token"])
def test_incorrect_success_fails_even_when_wire_succeeds(monkeypatch, mutation):
    from ezviz_unified import ApiResponse, OAuth

    original = OAuth.refresh_token

    def drift(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if mutation == "wrong_dto":
            return ApiResponse(200, "200", data={"access_token": result.access_token})
        return replace(result, access_token="ROTATED_DIFFERENT_TOKEN")

    monkeypatch.setattr(OAuth, "refresh_token", drift)
    assert failed_cells(evaluate(inputs())) == {("sync", "success", "response_mismatch")}


def test_wrong_typed_dto_field_fails_even_when_wire_succeeds(monkeypatch):
    from ezviz_unified.oauth import AsyncOAuth

    original = AsyncOAuth.refresh_token

    async def drift(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)  # real wire exchange
        return replace(result, expires_in=str(result.expires_in))

    monkeypatch.setattr(AsyncOAuth, "refresh_token", drift)
    assert failed_cells(evaluate(inputs())) == {("async", "success", "response_mismatch")}


def test_fault_code_remap_yields_wrong_mapped_exception(monkeypatch):
    original_send = httpx.Client.send

    def send(self, request, *args, **kwargs):
        response = original_send(self, request, *args, **kwargs)
        if response.status_code == 200:
            payload = response.json()
            if payload.get("code") == "70004":
                payload["code"] = "70005"
                return httpx.Response(200, json=payload, request=request)
        return response

    monkeypatch.setattr(httpx.Client, "send", send)
    assert failed_cells(evaluate(inputs())) == {("sync", "refresh_expired", "wrong_exception_type")}
