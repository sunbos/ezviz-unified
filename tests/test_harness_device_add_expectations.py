"""Offline wire-eval expectations for the admitted device-add operations."""

import copy
import importlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DEVICE_ADD = (
    "cn.device_add_links.create",
    "cn.device_add_links.records",
    "cn.device_add_links.list",
)
READ_ONLY = ("cn.device_add_links.records", "cn.device_add_links.list")
CONTROL = "cn.devices.get"
MODES = ("sync", "async")
REDACTED = "***"


def _wire_eval():
    return importlib.import_module("harness.wire_eval")


def _evaluate(*operation_ids):
    contracts = json.loads((ROOT / "contracts/reviewed-transport-contracts.json").read_text())
    bindings = json.loads((ROOT / "contracts/sdk-bindings.json").read_text())
    cases = json.loads((ROOT / "evals/contracts/cases.json").read_text())
    cases["cases"] = [c for c in cases["cases"] if c["operation_id"] in operation_ids]
    return _wire_eval().evaluate(contracts, bindings, cases, ROOT)


def _patch_response(monkeypatch, operation_ids, mutate, scenario="success"):
    wire_eval = _wire_eval()
    original = wire_eval._execute_cell

    async def wrapper(**kwargs):
        kind, outcome, queues, recorded = await original(**kwargs)
        if (
            kind == "response"
            and kwargs["scenario"] == scenario
            and kwargs["contract"]["operation_id"] in operation_ids
        ):
            outcome = mutate(outcome)
        return kind, outcome, queues, recorded

    monkeypatch.setattr(wire_eval, "_execute_cell", wrapper)


def _assert_only_success_mismatches(report, operation_ids, scenario="success"):
    failed = [r for r in report["results"] if not r["passed"]]
    assert not report["passed"]
    assert {(r["operation_id"], r["scenario"], r["mode"]) for r in failed} == {
        (op, scenario, mode) for op in operation_ids for mode in MODES
    }
    assert {r["problem"] for r in failed} == {"response_mismatch"}


def test_device_add_successes_pass_with_redacted_public_message():
    report = _evaluate(*DEVICE_ADD)
    assert report["passed"] and report["summary"]["failed_executions"] == 0
    assert report["coverage_complete"] is False
    assert set(report["covered_operation_ids"]) == set(DEVICE_ADD)
    assert {
        (r["operation_id"], r["mode"]) for r in report["results"] if r["scenario"] == "success"
    } == {(op, mode) for op in DEVICE_ADD for mode in MODES}
    assert {
        (r["operation_id"], r["mode"])
        for r in report["results"]
        if r["scenario"] == "auth_fault" and r["passed"] and r["operation_id"] in READ_ONLY
    } == {(op, mode) for op in READ_ONLY for mode in MODES}
    assert report["evidence"] == "offline_contract_conformance_only"


@pytest.mark.parametrize(
    "scenario,operation_ids",
    [("success", DEVICE_ADD), ("auth_fault", READ_ONLY)],
)
def test_leaked_raw_message_fails_every_device_add_success(monkeypatch, scenario, operation_ids):
    _patch_response(
        monkeypatch,
        operation_ids,
        lambda outcome: replace(outcome, message=outcome.raw["meta"]["message"]),
        scenario=scenario,
    )
    _assert_only_success_mismatches(_evaluate(*DEVICE_ADD), operation_ids, scenario)


def test_devices_get_control_passes():
    report = _evaluate(CONTROL)
    assert report["passed"] and report["summary"]["failed_executions"] == 0
    assert report["covered_operation_ids"] == [CONTROL]


def test_unrelated_operation_masked_message_fails(monkeypatch):
    _patch_response(monkeypatch, (CONTROL,), lambda o: replace(o, message=REDACTED))
    _assert_only_success_mismatches(_evaluate(CONTROL), (CONTROL,))


@pytest.mark.parametrize("field", ["raw", "data"])
def test_success_payload_integrity_checked_beyond_public_fields(monkeypatch, field):
    target = ("cn.device_add_links.records",)

    def mutate(outcome):
        if field == "raw":
            raw = copy.deepcopy(outcome.raw)
            raw["meta"]["message"] = "tampered-upstream"
            return replace(outcome, raw=raw)
        return replace(outcome, data={"tampered": True})

    _patch_response(monkeypatch, target, mutate)
    _assert_only_success_mismatches(_evaluate(*DEVICE_ADD), target)
