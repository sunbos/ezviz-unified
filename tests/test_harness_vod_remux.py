"""Harness-level pinning tests for the VOD remux transport contracts.

Reads the published contract/case JSON directly so an accidental edit to the
expected contract cannot keep the suite green.  No SDK import, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(rel: str):
    return json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))


_CONTRACTS = _load("contracts/reviewed-transport-contracts.json")["contracts"]
_BINDINGS = _load("contracts/sdk-bindings.json")["bindings"]
_CASES = _load("evals/contracts/cases.json")["cases"]
_ADMISSION = _load("docs/coordination/SP169/admission.json")

VOD_OPS = (
    "cn.vod.create_remux_task",
    "cn.vod.task",
    "cn.vod.tasks",
    "cn.vod.recording_urls",
)


def _contract(op_id: str) -> dict:
    for c in _CONTRACTS:
        if c.get("operation_id") == op_id:
            return c
    raise AssertionError(f"contract missing for {op_id}")


def _cases(op_id: str) -> list:
    return [c for c in _CASES if c.get("operation_id") == op_id]


def _param(contract: dict, name: str) -> dict:
    for p in contract["parameters"]:
        if p["name"] == name:
            return p
    raise AssertionError(f"{contract['operation_id']} param {name} missing")


def _binding(op_id: str) -> dict:
    for b in _BINDINGS:
        if b.get("operation_id") == op_id:
            return b
    raise AssertionError(f"binding missing for {op_id}")


# Expected wire facts, frozen from docs/coordination/SP169/admission.json
# (G1-admitted source review), NOT derived from the contract under test.
EXPECTED = {
    "cn.vod.create_remux_task": {
        "method": "POST",
        "path": "/api/service/open/vod/media/trans/code",
        "doc": "3971",
        "sha": "4a292560130c343f7ca3f89a33da2c3cbf702dab9f60a97aa529d3bb5e609266",
        "read_only": False,
        "biz_headers": ("deviceSerial", "localIndex"),
    },
    "cn.vod.task": {
        "method": "GET",
        "path": "/api/service/open/vod/task",
        "doc": "3972",
        "sha": "4ca347aedda382ddfa5b055452763ca70c131c7a4d3e4714f10c81b9549073f8",
        "read_only": True,
        "biz_headers": (),
    },
    "cn.vod.tasks": {
        "method": "GET",
        "path": "/api/service/open/vod/tasks",
        "doc": "4941",
        "sha": "8b8725427c2b7b0756e07ddbbdf07d2ccb0f243966cc673e276675916b9842c3",
        "read_only": True,
        "biz_headers": (),
    },
    "cn.vod.recording_urls": {
        "method": "POST",
        "path": "/api/service/open/vod/file/address/hls",
        "doc": "3973",
        "sha": "45d8dd262d69fc1a0dc2d904cf1d4442824458f3959bc99562e85d787301610d",
        # SDK replay-guard policy per SP169; the source did not declare this a write.
        "read_only": False,
        "biz_headers": ("deviceSerial", "localIndex"),
    },
}


@pytest.mark.parametrize("op_id", list(EXPECTED))
def test_wire_contract_matches_frozen_expectation(op_id):
    c, exp = _contract(op_id), EXPECTED[op_id]
    assert c["method"] == exp["method"]
    assert c["path"] == exp["path"]
    assert c["source_doc_id"] == exp["doc"]
    assert c["source_content_sha256"] == exp["sha"]
    assert c["token_location"] == "header"
    assert c["read_only"] is exp["read_only"]
    tok = _param(c, "accessToken")
    assert tok["location"] == "header" and tok["required"] is True
    headers = [p["name"] for p in c["parameters"] if p["location"] == "header"]
    assert sorted(headers) == sorted(("accessToken",) + exp["biz_headers"])
    success = c["success"]
    assert success["code_path"] == "meta.code"
    assert success["message_path"] == "meta.message"
    assert success["data_path"] == "data"
    assert success["values"] == [200]


def test_create_remux_task_conditionals():
    c = _contract("cn.vod.create_remux_task")
    assert _param(c, "spaceId")["required"] is False
    facts = " ".join(x["fact"] for x in c["constraints"])
    assert "busType=7" in facts and "requires spaceId" in facts
    assert _param(c, "format")["required"] is False
    assert _param(c, "busType")["required"] is True


def test_recording_urls_format_required():
    c = _contract("cn.vod.recording_urls")
    assert _param(c, "format")["required"] is True


def test_single_case_headers_and_token_injection():
    for op_id in EXPECTED:
        cases = _cases(op_id)
        assert len(cases) == 1
        case = cases[0]
        assert "accessToken" not in case["fields"]
        if op_id in ("cn.vod.create_remux_task", "cn.vod.recording_urls"):
            assert case["args"] == [{"$ref": "device_serial"}]
            assert isinstance(case["kwargs"]["local_index"], str)
            assert "deviceSerial" in case["fields"]
            assert case["fields"]["deviceSerial"] == {"$ref": "device_serial"}
            assert case["fields"]["localIndex"] == case["kwargs"]["local_index"]


def test_datetime_wire_format_naive_whole_seconds():
    for op_id in ("cn.vod.create_remux_task", "cn.vod.recording_urls"):
        case = _cases(op_id)[0]
        for kw, wire in (("start_time", "startTime"), ("end_time", "endTime")):
            arg = case["kwargs"][kw]
            assert arg.get("$type") == "datetime"
            value = arg["value"]
            # naive ISO with whole seconds: no offset, no sub-second part
            assert "+" not in value and "Z" not in value and "." not in value
            date_part, time_part = value.split("T")
            assert len(date_part) == 10 and len(time_part) == 8
            # wire field matches the source format yyyy-MM-dd HH:mm:ss
            assert case["fields"][wire] == f"{date_part} {time_part}"


def test_tasks_single_side_time_is_plain_str():
    case = _cases("cn.vod.tasks")[0]
    assert isinstance(case["kwargs"]["start_time"], str)
    assert "end_time" not in case["kwargs"]
    assert case["fields"]["startTime"] == case["kwargs"]["start_time"]
    assert "endTime" not in case["fields"]


def test_task_detail_uses_detail_encoding_not_paging():
    data = _cases("cn.vod.task")[0]["data"]
    assert data["taskType"] == 1
    assert data["taskSubType"] in (80, 81, 82)
    assert "result" not in data


def test_tasks_uses_paging_encoding_not_detail():
    case = _cases("cn.vod.tasks")[0]
    assert case["args"] == [8] and case["fields"]["taskType"] == 8
    data = case["data"]
    assert isinstance(data["result"], list)
    item_types = {item["taskType"] for item in data["result"]}
    assert item_types <= {7, 8}
    for item in data["result"]:
        assert "taskSubType" not in item


def test_recording_urls_response_array_preserves_source_fields():
    data = _cases("cn.vod.recording_urls")[0]["data"]
    assert isinstance(data, list)
    for item in data:
        for key in ("hlsAddress", "stopTime", "expireTime"):
            assert key in item
        assert "mp4Address" not in item
        assert "fileNodeId" not in item


def test_bindings_recorded_but_not_claimed_verified():
    # Presence/schema check only: not_verified is acceptable; a fabricated
    # "passed" must not appear while no wire_eval run backs it.
    for op_id in EXPECTED:
        b = _binding(op_id)
        assert b["offline_status"] == "not_verified"
        assert b["live_status"] == "not_verified"
