"""Harness-level pinning tests for the VOD remux transport contracts.

Reads the published contract/case JSON directly so an accidental edit to the
expected contract cannot keep the suite green.  The offline-evidence helpers
below check public evidence record consistency only: they assert the frozen
acceptance rows and checks.json agree with each other and carry the required
shape, not that any recorded tool_result actually ran on this machine or that
a recorded commit sha proves a real commit; empirical verification is the job
of the root review and CI.  No SDK import, no network.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(rel: str):
    return json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))


_CONTRACTS = _load("contracts/reviewed-transport-contracts.json")["contracts"]
_BINDINGS = _load("contracts/sdk-bindings.json")["bindings"]
_CASES = _load("evals/contracts/cases.json")["cases"]
_ADMISSION = _load("docs/coordination/SP169/admission.json")
_ACCEPTANCE = _load("docs/validation/sdk-product-acceptance.json")["cases"]
_SCOPE_INPUTS = _load("docs/capabilities/scope-register.json")["inputs"]
_CHECKS_PATH = "docs/validation/2026-11-18-vod-remux/checks.json"
_CHECKS = _load(_CHECKS_PATH)
_CHECKS_RAW_BYTES = (REPO_ROOT / _CHECKS_PATH).read_bytes()

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


def _acceptance_case(case_id: str) -> dict:
    for c in _ACCEPTANCE:
        if c.get("case_id") == case_id:
            return c
    raise AssertionError(f"acceptance case missing for {case_id}")


_OFFLINE_CASE_ID = {
    "cn.vod.create_remux_task": "ACC-CANDIDATE-CN-3971-D0-OFFLINE-001",
    "cn.vod.task": "ACC-CANDIDATE-CN-3972-D0-OFFLINE-001",
    "cn.vod.tasks": "ACC-CANDIDATE-CN-4941-D0-OFFLINE-001",
    "cn.vod.recording_urls": "ACC-CANDIDATE-CN-3973-D0-OFFLINE-001",
}

_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT40 = re.compile(r"^[0-9a-f]{40}$")

# Frozen OFFLINE criterion keys for the VOD remux cohort (identical across all
# four acceptance rows).  ``met`` count alone is not evidence: a row that
# invents different key names must not pass.
_OFFLINE_CRITERIA = frozenset(
    (
        "exact_sync_async_wire_and_header_token",
        "required_conditional_enum_none_default_validation",
        "domestic_and_header_utf8_time_validation_before_auth",
        "response_raw_unknown_fields_and_exception_identity",
        "frozen_per_operation_replay_policy",
        "diagnostic_on_off_scope_wire_order_counts",
        "known_request_values_redacted_without_code_collision",
        "closed_timeout_async_cancellation_preserved",
        "registry_binding_wire_and_exact_diagnostic_counts",
    )
)


def _is_sha64(value) -> bool:
    return isinstance(value, str) and bool(_SHA64.match(value))


def _check_offline_case_evidence(case: dict, checks: dict) -> None:
    """Assert the frozen OFFLINE acceptance case and its backing checks.json.

    ``case`` is one sdk-product-acceptance.json row; ``checks`` is the loaded
    checks.json it references.  Helpers take deepcopy-able data so minimal
    counterexamples can be validated without touching disk fixtures.
    """
    # recorded_evidence / passed outcome
    assert case["result"] == "recorded_evidence"
    assert case["execution_outcome"] == "passed"
    assert case["blockers"] == []

    # all 9 frozen criteria present and met; key set must match exactly, not
    # just len==9 + all met
    criteria = case["criterion_assessment"]
    assert set(criteria) == _OFFLINE_CRITERIA
    assert set(criteria.values()) == {"met"}

    # case is registered as accepted in checks.json
    assert case["case_id"] in checks["accepted_offline_cases"]

    # verified commit matches the checks code_commit and is a real 40-char sha
    commit = case["verified_code_commit"]
    assert commit == checks["code_commit"]
    assert isinstance(commit, str) and _COMMIT40.match(commit)

    # evidence ref points at the checks report
    assert case["execution_evidence_refs"] == [_CHECKS_PATH]


# The one exact full-regression command as actually recorded publicly; a
# targeted pytest invocation must not be mistaken for it via a substring.
_FULL_PYTEST_COMMAND = ".venv/bin/python -m pytest -q --tb=short --maxfail=5"


def _check_checks_commands(checks: dict) -> None:
    """checks.json must carry consistent successful command evidence.

    For each required tool kind, at least one single entry must itself match
    that kind's command *and* be a clean success (result == "passed",
    actual_tool_result_is_error is False, artifact_sha256 is a legal sha256);
    a failed or errored entry that merely mentions the command does not count.
    The full-pytest kind requires the exact recorded command so a targeted
    pytest run cannot satisfy it.  Entries that are not required-kind matches
    (e.g. the formatter's completed_format_before_full_regression) are not
    forced to "passed", but every entry still needs is_error False and a
    legal sha.
    """

    def _clean_success(entry: dict) -> bool:
        return (
            entry["result"] == "passed"
            and entry["actual_tool_result_is_error"] is False
            and _is_sha64(entry.get("artifact_sha256"))
        )

    def _require(pred, label: str) -> None:
        for entry in checks["checks"]:
            if pred(entry["command"]) and _clean_success(entry):
                return
        raise AssertionError(f"missing successful command evidence: {label}")

    def _require_tokens(pred, label: str) -> None:
        # token-prefix identity over command.split(); never substring matching
        _require(lambda cmd: pred(cmd.split()), label)

    _require(
        lambda cmd: cmd == _FULL_PYTEST_COMMAND,
        f"full pytest run ({_FULL_PYTEST_COMMAND})",
    )
    _require_tokens(
        lambda tok: (
            tok[:3] == [".venv/bin/python", "-m", "harness.contract_eval"]
            and "--require-all" in tok
        ),
        "wire eval",
    )
    _require_tokens(
        lambda tok: (
            tok[:3] == [".venv/bin/python", "-m", "pytest"]
            and "tests/test_sdk_vod_remux_behaviour.py" in tok
        ),
        "behaviour tests",
    )
    _require_tokens(
        lambda tok: tok[:3] == [".venv/bin/python", "-m", "mypy"] and "src/ezviz_unified" in tok,
        "mypy",
    )
    _require_tokens(
        lambda tok: tok[:4] == [".venv/bin/python", "-m", "ruff", "check"],
        "lint",
    )

    for entry in checks["checks"]:
        # every recorded check did not error at the tool level
        assert entry["actual_tool_result_is_error"] is False
        # artifact pins are legal sha256
        assert _is_sha64(entry["artifact_sha256"]), entry["local_execution_artifact_id"]


def _check_checks_required_pins(checks: dict) -> None:
    """SDK module / test pins inside checks.json tested_file_sha256."""
    required = (
        "src/ezviz_unified/vod.py",
        "src/ezviz_unified/errors.py",
        "src/ezviz_unified/_diagnostic_registry.py",
        "tests/test_sdk_vod_remux.py",  # new SDK wire tests
        "tests/test_sdk_vod_remux_behaviour.py",  # behaviour tests
    )
    pins = checks["tested_file_sha256"]
    for rel in required:
        assert rel in pins, f"required pin missing: {rel}"
        assert _is_sha64(pins[rel]), rel


def _check_scope_register_freezes_checks(scope_inputs: dict) -> None:
    """scope-register must register the exact current checks.json bytes."""
    entry = scope_inputs["vod_remux_offline_acceptance"]
    assert entry["path"] == _CHECKS_PATH
    assert entry["sha256"] == hashlib.sha256(_CHECKS_RAW_BYTES).hexdigest()


def test_bindings_recorded_but_not_claimed_verified():
    # Semantic inheritance: the four bindings are now offline-verified, so this
    # test no longer asserts a blanket not_verified.  Instead it requires either
    # the historical admission state (not_verified) or a passed status that is
    # fully backed by the frozen public evidence chain.  live_status is still
    # not_verified for all four ops.
    for op_id in EXPECTED:
        b = _binding(op_id)
        # live is never claimed verified for this cohort
        assert b["live_status"] == "not_verified"

        status = b["offline_status"]
        assert status in ("not_verified", "passed")
        if status == "not_verified":
            # acceptable historical/admission state before offline acceptance
            continue

        # offline_status == "passed" must be backed by the frozen evidence
        case = _acceptance_case(_OFFLINE_CASE_ID[op_id])
        _check_offline_case_evidence(case, _CHECKS)
        _check_checks_commands(_CHECKS)
        _check_checks_required_pins(_CHECKS)
        _check_scope_register_freezes_checks(_SCOPE_INPUTS)


def _minimal_passed_case(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "result": "recorded_evidence",
        "execution_outcome": "passed",
        "blockers": [],
        "criterion_assessment": {key: "met" for key in _OFFLINE_CRITERIA},
        "verified_code_commit": "fce298b34911eac89e6c5813b203c5b267e80095",
        "execution_evidence_refs": [_CHECKS_PATH],
    }


def _minimal_checks() -> dict:
    return {
        "code_commit": "fce298b34911eac89e6c5813b203c5b267e80095",
        "accepted_offline_cases": list(_OFFLINE_CASE_ID.values()),
        "checks": [
            {
                "command": _FULL_PYTEST_COMMAND,
                "result": "passed",
                "actual_tool_result_is_error": False,
                "local_execution_artifact_id": "X",
                "artifact_sha256": "a" * 64,
            },
            {
                "command": ".venv/bin/python -m harness.contract_eval --repo . --require-all",
                "result": "passed",
                "actual_tool_result_is_error": False,
                "local_execution_artifact_id": "Y",
                "artifact_sha256": "b" * 64,
            },
            {
                "command": ".venv/bin/python -m pytest tests/test_sdk_vod_remux_behaviour.py",
                "result": "passed",
                "actual_tool_result_is_error": False,
                "local_execution_artifact_id": "Z",
                "artifact_sha256": "c" * 64,
            },
            {
                "command": ".venv/bin/python -m mypy src/ezviz_unified",
                "result": "passed",
                "actual_tool_result_is_error": False,
                "local_execution_artifact_id": "M",
                "artifact_sha256": "d" * 64,
            },
            {
                "command": ".venv/bin/python -m ruff check src/ezviz_unified",
                "result": "passed",
                "actual_tool_result_is_error": False,
                "local_execution_artifact_id": "L",
                "artifact_sha256": "e" * 64,
            },
        ],
        "tested_file_sha256": {
            "src/ezviz_unified/vod.py": "1" * 64,
            "src/ezviz_unified/errors.py": "2" * 64,
            "src/ezviz_unified/_diagnostic_registry.py": "3" * 64,
            "tests/test_sdk_vod_remux.py": "4" * 64,
            "tests/test_sdk_vod_remux_behaviour.py": "5" * 64,
        },
    }


def test_offline_passed_requires_real_evidence_minimal_counterexamples():
    # A fabricated "passed" must not slip through: mutate minimal valid data
    # with deepcopy and confirm each corruption is rejected.
    case_id = _OFFLINE_CASE_ID["cn.vod.create_remux_task"]

    # baseline: valid minimal evidence passes each helper
    good_case = _minimal_passed_case(case_id)
    good_checks = _minimal_checks()
    _check_offline_case_evidence(good_case, good_checks)
    _check_checks_commands(good_checks)
    _check_checks_required_pins(good_checks)

    # passed but case not in accepted_offline_cases
    bad_checks = copy.deepcopy(good_checks)
    bad_checks["accepted_offline_cases"] = []
    with pytest.raises(AssertionError):
        _check_offline_case_evidence(copy.deepcopy(good_case), bad_checks)

    # verified_code_commit does not match checks.code_commit
    bad_case = copy.deepcopy(good_case)
    bad_case["verified_code_commit"] = "0" * 40
    with pytest.raises(AssertionError):
        _check_offline_case_evidence(bad_case, copy.deepcopy(good_checks))

    # a criterion still pending
    bad_case = copy.deepcopy(good_case)
    criteria = bad_case["criterion_assessment"]
    criteria["frozen_per_operation_replay_policy"] = "pending"
    with pytest.raises(AssertionError):
        _check_offline_case_evidence(bad_case, copy.deepcopy(good_checks))

    # a frozen criterion key swapped for an invented one keeps len==9 all-met,
    # but the key set no longer matches the frozen criteria
    bad_case = copy.deepcopy(good_case)
    criteria = bad_case["criterion_assessment"]
    criteria["invented_criterion_key"] = criteria.pop("exact_sync_async_wire_and_header_token")
    with pytest.raises(AssertionError):
        _check_offline_case_evidence(bad_case, copy.deepcopy(good_checks))

    # a recorded check errored at the tool level
    bad_checks = copy.deepcopy(good_checks)
    bad_checks["checks"][0]["actual_tool_result_is_error"] = True
    with pytest.raises(AssertionError):
        _check_checks_commands(bad_checks)

    # a required-kind entry with a failed result does not satisfy that kind,
    # even though is_error stays False
    bad_checks = copy.deepcopy(good_checks)
    bad_checks["checks"][0]["result"] = "failed"
    with pytest.raises(AssertionError):
        _check_checks_commands(bad_checks)

    # only targeted pytest evidence: no exact full-regression command
    bad_checks = copy.deepcopy(good_checks)
    bad_checks["checks"][0]["command"] = (
        ".venv/bin/python -m pytest -q --tb=short --maxfail=5 tests/test_sdk_vod_remux.py"
    )
    with pytest.raises(AssertionError):
        _check_checks_commands(bad_checks)

    # a required pin missing
    bad_checks = copy.deepcopy(good_checks)
    del bad_checks["tested_file_sha256"]["src/ezviz_unified/vod.py"]
    with pytest.raises(AssertionError):
        _check_checks_required_pins(bad_checks)

    # the behaviour file path glued onto the still-successful ruff command
    # must not count as behaviour evidence once the real behaviour pytest
    # record is deleted: kind identity is by command tokens, not substring
    bad_checks = copy.deepcopy(good_checks)
    del bad_checks["checks"][2]  # the real behaviour pytest record
    bad_checks["checks"][-1]["command"] += " tests/test_sdk_vod_remux_behaviour.py"
    with pytest.raises(AssertionError):
        _check_checks_commands(bad_checks)
