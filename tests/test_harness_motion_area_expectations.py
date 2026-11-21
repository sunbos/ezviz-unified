import itertools
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ezviz_unified.detectors import Detectors
from ezviz_unified.devices import Devices
from ezviz_unified.recordings import Recordings
from ezviz_unified.settings import Settings
from ezviz_unified.snapshots import Snapshots
from ezviz_unified.third_party_protocols import ThirdPartyProtocols
from ezviz_unified.video import Video
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
    "cn.video.get_video_codec",
    "cn.devices.list_by_id",
    "cn.recordings.by_time",
    "cn.recordings.by_text_time",
    "cn.settings.get_working_mode_plans",
    "cn.settings.set_huashu_4g_working_mode_plan",
    "cn.third_party_protocols.list_gb_licenses",
    "cn.settings.set_motion_tracking",
    "cn.settings.get_motion_tracking",
    "cn.settings.get_human_tracking",
    "cn.settings.set_human_tracking",
    "cn.settings.get_intelligent_detection_switch",
    "cn.settings.set_intelligent_detection_switch",
    "cn.settings.select_alarm_detection_mode",
    "cn.settings.get_alarm_detection_mode",
    "global.detectors.rename",
    "cn.settings.get_advanced_alarm_detection_type",
    "cn.settings.get_channel_light_linkage",
    "global.snapshots.get_by_uuid",
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
    "cn.video.get_video_codec": (Video, "get_video_codec"),
    "cn.devices.list_by_id": (Devices, "list_by_id"),
    "cn.recordings.by_time": (Recordings, "by_time"),
    "cn.recordings.by_text_time": (Recordings, "by_text_time"),
    "cn.settings.get_working_mode_plans": (Settings, "get_working_mode_plans"),
    "cn.settings.set_huashu_4g_working_mode_plan": (Settings, "set_huashu_4g_working_mode_plan"),
    "cn.third_party_protocols.list_gb_licenses": (ThirdPartyProtocols, "list_gb_licenses"),
    "cn.settings.set_motion_tracking": (Settings, "set_motion_tracking"),
    "cn.settings.get_motion_tracking": (Settings, "get_motion_tracking"),
    "cn.settings.get_human_tracking": (Settings, "get_human_tracking"),
    "cn.settings.set_human_tracking": (Settings, "set_human_tracking"),
    "cn.settings.get_intelligent_detection_switch": (Settings, "get_intelligent_detection_switch"),
    "cn.settings.set_intelligent_detection_switch": (Settings, "set_intelligent_detection_switch"),
    "cn.settings.select_alarm_detection_mode": (Settings, "select_alarm_detection_mode"),
    "cn.settings.get_alarm_detection_mode": (Settings, "get_alarm_detection_mode"),
    "global.detectors.rename": (Detectors, "rename"),
    "cn.settings.get_advanced_alarm_detection_type": (
        Settings,
        "get_advanced_alarm_detection_type",
    ),
    "cn.settings.get_channel_light_linkage": (Settings, "get_channel_light_linkage"),
    "global.snapshots.get_by_uuid": (Snapshots, "get_by_uuid"),
}

READ_ONLY_OPERATION_IDS = {
    "cn.settings.get_motion_area",
    "cn.settings.get_motion_sensitivity",
    "cn.settings.get_detection_sensitivity",
    "cn.video.get_video_codec",
    "cn.devices.get",
    "cn.devices.list_by_id",
    "cn.recordings.by_time",
    "cn.recordings.by_text_time",
    "cn.settings.get_working_mode_plans",
    "cn.third_party_protocols.list_gb_licenses",
    "cn.settings.get_motion_tracking",
    "cn.settings.get_human_tracking",
    "cn.settings.get_intelligent_detection_switch",
    "cn.settings.get_alarm_detection_mode",
    "cn.settings.get_advanced_alarm_detection_type",
    "cn.settings.get_channel_light_linkage",
    "global.snapshots.get_by_uuid",
}

MUTATIONS = {
    "leak": lambda response: replace(response, message="contract-eval-ok"),
    "raw": lambda response: replace(response, raw={}),
    "data": lambda response: replace(response, data={}),
    "mask": lambda response: replace(response, message="***"),
    "drop_page": lambda response: replace(
        response, raw={k: v for k, v in response.raw.items() if k != "page"}
    ),
    "corrupt_page": lambda response: replace(
        response,
        raw={**response.raw, "page": {**response.raw["page"], "nextId": "contract-eval-corrupt"}},
    ),
}

PARAMS = [
    *itertools.product(REDACTED_OPERATION_IDS, ("leak", "raw", "data")),
    ("cn.devices.get", "mask"),
    ("cn.devices.list_by_id", "drop_page"),
    ("cn.devices.list_by_id", "corrupt_page"),
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
