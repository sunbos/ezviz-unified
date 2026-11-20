import itertools
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ezviz_unified.devices import Devices
from ezviz_unified.settings import Settings
from harness.wire_eval import evaluate

ROOT = Path(__file__).resolve().parents[1]

MODES = ("sync", "async")
SCENARIOS = ("success", "auth_fault", "permission_fault", "http_fault")

REDACTED_OPERATION_IDS = (
    "cn.settings.get_motion_area",
    "cn.settings.set_motion_area",
    "global.settings.set_motion_area",
    "cn.settings.get_motion_sensitivity",
    "cn.settings.set_motion_sensitivity",
    "cn.settings.get_detection_sensitivity",
    "cn.settings.set_detection_sensitivity",
)

FACADES = {
    "cn.settings.get_motion_area": (Settings, "get_motion_area"),
    "cn.settings.set_motion_area": (Settings, "set_motion_area"),
    "global.settings.set_motion_area": (Settings, "set_motion_area"),
    "cn.settings.get_motion_sensitivity": (Settings, "get_motion_sensitivity"),
    "cn.settings.set_motion_sensitivity": (Settings, "set_motion_sensitivity"),
    "cn.settings.get_detection_sensitivity": (Settings, "get_detection_sensitivity"),
    "cn.settings.set_detection_sensitivity": (Settings, "set_detection_sensitivity"),
    "cn.devices.get": (Devices, "get"),
}

READ_ONLY_OPERATION_IDS = {
    "cn.settings.get_motion_area",
    "cn.settings.get_motion_sensitivity",
    "cn.settings.get_detection_sensitivity",
    "cn.devices.get",
}

MUTATIONS = {
    "leak": lambda response: replace(response, message="contract-eval-ok"),
    "raw": lambda response: replace(response, raw={}),
    "data": lambda response: replace(response, data={}),
    "mask": lambda response: replace(response, message="***"),
}

PARAMS = [
    *itertools.product(REDACTED_OPERATION_IDS, ("leak", "raw", "data")),
    ("cn.devices.get", "mask"),
]


def _inputs(operation_id):
    contracts = json.loads((ROOT / "contracts/reviewed-transport-contracts.json").read_text())
    bindings = json.loads((ROOT / "contracts/sdk-bindings.json").read_text())
    rows = json.loads((ROOT / "evals/contracts/cases.json").read_text())["cases"]
    selected = next(row for row in rows if row["operation_id"] == operation_id)
    return contracts, bindings, {"schema_version": 1, "cases": [selected]}


@pytest.mark.parametrize("operation_id,mutation", PARAMS)
def test_mutated_sync_facade_response_is_rejected(monkeypatch, operation_id, mutation):
    contracts, bindings, cases = _inputs(operation_id)
    facade, method_name = FACADES[operation_id]
    real = getattr(facade, method_name)
    mutate = MUTATIONS[mutation]

    def wrapper(*args, **kwargs):
        return mutate(real(*args, **kwargs))

    monkeypatch.setattr(facade, method_name, wrapper)

    report = evaluate(contracts, bindings, cases, ROOT)

    results = report["results"]
    assert len(results) == 8
    assert report["summary"]["executions"] == 8
    assert {(r["mode"], r["scenario"]) for r in results} == {
        (mode, scenario) for mode in MODES for scenario in SCENARIOS
    }

    expected_failures = {("sync", "success")}
    if operation_id in READ_ONLY_OPERATION_IDS:
        expected_failures.add(("sync", "auth_fault"))

    failures = {(r["mode"], r["scenario"]): r["problem"] for r in results if not r["passed"]}
    assert set(failures) == expected_failures
    assert set(failures.values()) == {"response_mismatch"}
    assert not report["passed"]
