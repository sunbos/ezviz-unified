"""Tests for ``harness.maintenance_eval``.

These tests exercise only the deterministic evaluator; no LLM is called.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from harness.maintenance_eval import MaintenanceEvaluationError, evaluate

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MAX_INPUT_BYTES = 2 * 1024 * 1024
_PRIVATE_MARKER = "PRIVATE_MARKER"


def _cases_doc():
    return {
        "schema_version": 1,
        "cases": [
            {
                "case_id": "M001",
                "synthetic": True,
                "region": "domestic",
                "operation_id": "synthetic.device.list",
                "question": "request.pageSize.maximum 是否发生协议变更？",
                "fields": ["request.pageSize.maximum", "request.pageStart.minimum"],
                "before_status": "archived",
                "after_status": "archived",
                "before": [
                    {"id": "b1", "text": "合成旧文档：pageSize 最大值为 50。"},
                    {"id": "b2", "text": "合成旧文档：pageStart 最小值为 0。"},
                ],
                "after": [
                    {"id": "a1", "text": "合成新文档：pageSize 最大值为 20。"},
                    {"id": "a2", "text": "合成新文档：pageStart 最小值为 0。"},
                ],
            },
            {
                "case_id": "M006",
                "synthetic": True,
                "region": "domestic",
                "operation_id": "synthetic.storage.unified",
                "question": "新文档缺失时如何判断 storageMode？",
                "fields": ["request.storageMode.required"],
                "before_status": "archived",
                "after_status": "source_missing",
                "before": [{"id": "b1", "text": "合成旧文档：storageMode 必填。"}],
                "after": [],
            },
        ],
    }


def _expectations_doc():
    return {
        "schema_version": 1,
        "expectations": [
            {
                "case_id": "M001",
                "decision": "contract_change",
                "changes": [
                    {
                        "field": "request.pageSize.maximum",
                        "before": 50,
                        "after": 20,
                        "before_evidence": ["b1"],
                        "after_evidence": ["a1"],
                    }
                ],
                "uncertainties": [],
            },
            {
                "case_id": "M006",
                "decision": "insufficient_evidence",
                "changes": [],
                "uncertainties": ["source_missing"],
            },
        ],
    }


def _answers_doc():
    """Control answers derived from the oracle."""
    return {"schema_version": 1, "answers": copy.deepcopy(_expectations_doc()["expectations"])}


def _result(report, case_id):
    return next(item for item in report["results"] if item["case_id"] == case_id)


def test_correct_answers_pass_without_publication():
    report = evaluate(_cases_doc(), _expectations_doc(), _answers_doc())
    assert report["passed"] is True
    assert report["publication_authorized"] is False
    assert report["patch_applied"] is False
    assert report["real_api_used"] is False
    assert report["evidence"] == "synthetic_maintenance_semantic_evaluation_only"
    assert report["schema_version"] == 1
    assert "inputs_sha256" not in report
    assert report["summary"] == {
        "total_cases": 2,
        "answered_cases": 2,
        "passed_cases": 2,
        "failed_cases": 0,
    }
    for item in report["results"]:
        assert item["passed"] is True
        assert item["problems"] == []


def test_answer_order_is_ignored():
    answers = _answers_doc()
    answers["answers"].reverse()
    report = evaluate(_cases_doc(), _expectations_doc(), answers)
    assert report["passed"] is True


def test_missing_answer_keeps_total_denominator():
    answers = _answers_doc()
    answers["answers"] = answers["answers"][:1]
    report = evaluate(_cases_doc(), _expectations_doc(), answers)
    assert report["passed"] is False
    assert report["summary"] == {
        "total_cases": 2,
        "answered_cases": 1,
        "passed_cases": 1,
        "failed_cases": 1,
    }
    assert _result(report, "M006") == {
        "case_id": "M006",
        "passed": False,
        "problems": ["missing_answer"],
    }


def test_empty_answers_list_scores_all_missing():
    report = evaluate(_cases_doc(), _expectations_doc(), {"schema_version": 1, "answers": []})
    assert report["passed"] is False
    assert report["summary"]["answered_cases"] == 0
    assert report["summary"]["failed_cases"] == 2
    assert all(item["problems"] == ["missing_answer"] for item in report["results"])


def test_underreported_change_scores_as_failure():
    answers = _answers_doc()
    answers["answers"][0] = {
        "case_id": "M001",
        "decision": "no_contract_change",
        "changes": [],
        "uncertainties": [],
    }
    report = evaluate(_cases_doc(), _expectations_doc(), answers)
    assert _result(report, "M001")["problems"] == ["decision_mismatch", "changes_mismatch"]
    assert report["passed"] is False


def test_misreported_change_value_scores_as_failure():
    answers = _answers_doc()
    answers["answers"][0]["changes"][0]["after"] = 30
    report = evaluate(_cases_doc(), _expectations_doc(), answers)
    assert _result(report, "M001")["problems"] == ["changes_mismatch"]


def test_overreported_change_scores_as_failure():
    answers = _answers_doc()
    answers["answers"][0]["changes"].append(
        {
            "field": "request.pageStart.minimum",
            "before": 0,
            "after": 1,
            "before_evidence": ["b2"],
            "after_evidence": ["a2"],
        }
    )
    report = evaluate(_cases_doc(), _expectations_doc(), answers)
    assert _result(report, "M001")["problems"] == ["changes_mismatch"]


def test_extra_evidence_scores_as_failure():
    answers = _answers_doc()
    answers["answers"][0]["changes"][0]["after_evidence"] = ["a2", "a1"]
    report = evaluate(_cases_doc(), _expectations_doc(), answers)
    assert _result(report, "M001")["problems"] == ["changes_mismatch"]


def test_wrong_uncertainty_scores_as_failure():
    answers = _answers_doc()
    answers["answers"][1]["uncertainties"] = ["source_conflict"]
    report = evaluate(_cases_doc(), _expectations_doc(), answers)
    assert _result(report, "M006")["problems"] == ["uncertainties_mismatch"]


def _mini_docs(oracle_before, oracle_after):
    cases = {
        "schema_version": 1,
        "cases": [
            {
                "case_id": "T001",
                "synthetic": True,
                "region": "domestic",
                "operation_id": "synthetic.demo",
                "question": "f 是否变更？",
                "fields": ["f"],
                "before_status": "archived",
                "after_status": "archived",
                "before": [{"id": "b1", "text": "旧值。"}],
                "after": [{"id": "a1", "text": "新值。"}],
            }
        ],
    }
    oracle = {
        "schema_version": 1,
        "expectations": [
            {
                "case_id": "T001",
                "decision": "contract_change",
                "changes": [
                    {
                        "field": "f",
                        "before": oracle_before,
                        "after": oracle_after,
                        "before_evidence": ["b1"],
                        "after_evidence": ["a1"],
                    }
                ],
                "uncertainties": [],
            }
        ],
    }
    return cases, oracle


def _mini_answers(before, after):
    return {
        "schema_version": 1,
        "answers": [
            {
                "case_id": "T001",
                "decision": "contract_change",
                "changes": [
                    {
                        "field": "f",
                        "before": before,
                        "after": after,
                        "before_evidence": ["b1"],
                        "after_evidence": ["a1"],
                    }
                ],
                "uncertainties": [],
            }
        ],
    }


def test_matching_scalars_pass():
    cases, oracle = _mini_docs(1, 200)
    report = evaluate(cases, oracle, _mini_answers(1, 200))
    assert report["passed"] is True


def test_bool_is_not_int_for_scoring():
    cases, oracle = _mini_docs(1, 200)
    report = evaluate(cases, oracle, _mini_answers(True, 200))
    assert _result(report, "T001")["problems"] == ["changes_mismatch"]


def test_string_is_not_int_for_scoring():
    cases, oracle = _mini_docs(1, 200)
    report = evaluate(cases, oracle, _mini_answers(1, "200"))
    assert _result(report, "T001")["problems"] == ["changes_mismatch"]


def test_float_values_are_rejected():
    cases, oracle = _mini_docs(1, 200)
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(cases, oracle, _mini_answers(1, 200.0))
    bad_oracle = _mini_docs(1, 200.5)[1]
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(cases, bad_oracle, _mini_answers(1, 200))


def test_unknown_answer_case_is_rejected():
    answers = _answers_doc()
    answers["answers"].append(
        {
            "case_id": "M999",
            "decision": "no_contract_change",
            "changes": [],
            "uncertainties": [],
        }
    )
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_duplicate_answer_case_is_rejected():
    answers = _answers_doc()
    answers["answers"].append(copy.deepcopy(answers["answers"][0]))
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_extra_answer_fields_are_rejected():
    for key in ("score", "passed", "publish", "confidence"):
        answers = _answers_doc()
        answers["answers"][0][key] = True
        with pytest.raises(MaintenanceEvaluationError):
            evaluate(_cases_doc(), _expectations_doc(), answers)


def test_extra_top_level_fields_are_rejected():
    answers = _answers_doc()
    answers["report"] = {}
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_no_contract_change_carrying_changes_is_rejected():
    answers = _answers_doc()
    answers["answers"][0]["decision"] = "no_contract_change"
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_unknown_uncertainty_is_rejected():
    answers = _answers_doc()
    answers["answers"][1]["uncertainties"] = ["model_guess"]
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_duplicate_uncertainty_is_rejected():
    answers = _answers_doc()
    answers["answers"][1]["uncertainties"] = ["source_missing", "source_missing"]
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_missing_row_reference_is_rejected():
    answers = _answers_doc()
    answers["answers"][0]["changes"][0]["after_evidence"] = ["a9"]
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)

    oracle = _expectations_doc()
    oracle["expectations"][0]["changes"][0]["before_evidence"] = ["b9"]
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), oracle, _answers_doc())


def test_citing_source_missing_side_is_rejected():
    answers = _answers_doc()
    answers["answers"][1] = {
        "case_id": "M006",
        "decision": "contract_change",
        "changes": [
            {
                "field": "request.storageMode.required",
                "before": True,
                "after": False,
                "before_evidence": ["b1"],
                "after_evidence": ["a1"],
            }
        ],
        "uncertainties": [],
    }
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_duplicate_evidence_is_rejected():
    answers = _answers_doc()
    answers["answers"][0]["changes"][0]["before_evidence"] = ["b1", "b1"]
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_duplicate_change_field_is_rejected():
    answers = _answers_doc()
    answers["answers"][0]["changes"].append(copy.deepcopy(answers["answers"][0]["changes"][0]))
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_identical_before_and_after_is_rejected():
    answers = _answers_doc()
    answers["answers"][0]["changes"][0]["after"] = 50
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), answers)


def test_expectations_must_cover_every_case():
    oracle = _expectations_doc()
    oracle["expectations"] = oracle["expectations"][:1]
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), oracle, _answers_doc())


def test_evaluate_does_not_mutate_inputs():
    cases = _cases_doc()
    oracle = _expectations_doc()
    answers = _answers_doc()
    snapshot = (copy.deepcopy(cases), copy.deepcopy(oracle), copy.deepcopy(answers))
    evaluate(cases, oracle, answers)
    assert (cases, oracle, answers) == snapshot


def test_cases_document_validation():
    with pytest.raises(MaintenanceEvaluationError):
        evaluate({}, _expectations_doc(), _answers_doc())

    broken = _cases_doc()
    broken["cases"] = []
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(broken, _expectations_doc(), _answers_doc())

    broken = _cases_doc()
    broken["schema_version"] = True
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(broken, _expectations_doc(), _answers_doc())

    broken = _cases_doc()
    broken["cases"][0]["before"] = []
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(broken, _expectations_doc(), _answers_doc())

    broken = _cases_doc()
    broken["cases"][1]["after"] = [{"id": "a1", "text": "不应出现的行"}]
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(broken, _expectations_doc(), _answers_doc())

    broken = _cases_doc()
    broken["cases"][0]["before"].append({"id": "b1", "text": "重复行号"})
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(broken, _expectations_doc(), _answers_doc())

    broken = _cases_doc()
    broken["cases"].append(copy.deepcopy(broken["cases"][0]))
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(broken, _expectations_doc(), _answers_doc())


def test_answers_document_validation():
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), {})
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), {"schema_version": "1", "answers": []})
    with pytest.raises(MaintenanceEvaluationError):
        evaluate(_cases_doc(), _expectations_doc(), {"schema_version": 1, "answers": {}})


def test_report_does_not_echo_source_text():
    report = evaluate(_cases_doc(), _expectations_doc(), _answers_doc())
    blob = json.dumps(report, ensure_ascii=False)
    assert "旧文档" not in blob
    assert "pageSize" not in blob
    assert "b1" not in blob


def _write_file(directory, name, payload):
    path = directory / name
    data = (
        payload
        if isinstance(payload, bytes)
        else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    path.write_bytes(data)
    return path, data


def _write_all(directory, cases=None, expectations=None, answers=None):
    cases = _cases_doc() if cases is None else cases
    expectations = _expectations_doc() if expectations is None else expectations
    answers = _answers_doc() if answers is None else answers
    cases_path, cases_bytes = _write_file(directory, "cases.json", cases)
    expectations_path, expectations_bytes = _write_file(
        directory, "expectations.json", expectations
    )
    answers_path, answers_bytes = _write_file(directory, "answers.json", answers)
    paths = (cases_path, expectations_path, answers_path)
    blobs = {"cases": cases_bytes, "expectations": expectations_bytes, "answers": answers_bytes}
    return paths, blobs


def _run_cli(cases_path, expectations_path, answers_path):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "harness.maintenance_eval",
            "--cases",
            str(cases_path),
            "--expectations",
            str(expectations_path),
            "--answers",
            str(answers_path),
        ],
        capture_output=True,
        cwd=_REPO_ROOT,
        timeout=120,
    )


def _assert_cli_input_error(proc):
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"maintenance_eval: invalid input\n"


def test_cli_exit_zero_and_input_hashes(tmp_path):
    paths, blobs = _write_all(tmp_path)
    proc = _run_cli(*paths)
    assert proc.returncode == 0
    report = json.loads(proc.stdout.decode("utf-8"))
    assert report["passed"] is True
    assert report["publication_authorized"] is False
    assert report["inputs_sha256"] == {
        name: hashlib.sha256(blobs[name]).hexdigest()
        for name in ("cases", "expectations", "answers")
    }


def test_cli_exit_one_for_failing_answers(tmp_path):
    answers = _answers_doc()
    answers["answers"][0]["changes"][0]["after"] = 30
    paths, _ = _write_all(tmp_path, answers=answers)
    proc = _run_cli(*paths)
    assert proc.returncode == 1
    assert proc.stderr == b""
    report = json.loads(proc.stdout.decode("utf-8"))
    assert report["passed"] is False
    assert report["summary"]["failed_cases"] == 1


def test_cli_rejects_duplicate_json_keys(tmp_path):
    paths, _ = _write_all(tmp_path)
    bad = tmp_path / "answers.json"
    bad.write_bytes(b'{"schema_version": 1, "schema_version": 1, "answers": []}')
    _assert_cli_input_error(_run_cli(paths[0], paths[1], bad))


def test_cli_rejects_non_finite_numbers(tmp_path):
    paths, _ = _write_all(tmp_path)
    bad = tmp_path / "answers.json"
    bad.write_bytes(b'{"schema_version": 1, "answers": [NaN]}')
    _assert_cli_input_error(_run_cli(paths[0], paths[1], bad))


def test_cli_rejects_non_utf8(tmp_path):
    paths, _ = _write_all(tmp_path)
    bad = tmp_path / "answers.json"
    bad.write_bytes(b'{"schema_version": 1, "answers": []\xff\xfe}')
    _assert_cli_input_error(_run_cli(paths[0], paths[1], bad))


def test_cli_rejects_oversized_input(tmp_path):
    paths, _ = _write_all(tmp_path)
    bad = tmp_path / "answers.json"
    bad.write_bytes(b" " * (_MAX_INPUT_BYTES + 1))
    _assert_cli_input_error(_run_cli(paths[0], paths[1], bad))


def test_cli_stderr_does_not_echo_private_marker(tmp_path):
    cases = _cases_doc()
    cases["cases"][0]["before"].append({"id": "b3", "text": _PRIVATE_MARKER})
    answers = _answers_doc()
    answers["answers"].append(
        {
            "case_id": _PRIVATE_MARKER,
            "decision": "no_contract_change",
            "changes": [],
            "uncertainties": [],
        }
    )
    paths, _ = _write_all(tmp_path, cases=cases, answers=answers)
    proc = _run_cli(*paths)
    assert proc.returncode == 2
    assert _PRIVATE_MARKER not in proc.stderr.decode("utf-8", "replace")


def test_cli_stderr_does_not_echo_marker_in_malformed_json(tmp_path):
    paths, _ = _write_all(tmp_path)
    bad = tmp_path / "answers.json"
    bad.write_bytes(
        ('{"schema_version": 1, "answers": ["' + _PRIVATE_MARKER + '"], "answers": []}').encode(
            "utf-8"
        )
    )
    proc = _run_cli(paths[0], paths[1], bad)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert _PRIVATE_MARKER not in proc.stderr.decode("utf-8", "replace")


def _load_real_benchmark_docs():
    with open(
        os.path.join(_REPO_ROOT, "evals", "maintenance", "cases.json"), encoding="utf-8"
    ) as handle:
        cases = json.load(handle)
    with open(
        os.path.join(_REPO_ROOT, "evals", "maintenance", "expectations.json"), encoding="utf-8"
    ) as handle:
        expectations = json.load(handle)
    return cases, expectations


def test_real_benchmark_oracle_control_passes():
    cases, expectations = _load_real_benchmark_docs()
    assert [case["case_id"] for case in cases["cases"]] == [f"M{i:03d}" for i in range(1, 9)]
    assert all(case["synthetic"] is True for case in cases["cases"])
    answers = {"schema_version": 1, "answers": copy.deepcopy(expectations["expectations"])}
    report = evaluate(cases, expectations, answers)
    assert report["passed"] is True
    assert report["summary"] == {
        "total_cases": 8,
        "answered_cases": 8,
        "passed_cases": 8,
        "failed_cases": 0,
    }
    assert report["publication_authorized"] is False
    assert report["patch_applied"] is False
    assert report["real_api_used"] is False


def test_real_benchmark_single_mutation_fails_only_m001():
    cases, expectations = _load_real_benchmark_docs()
    answers = {"schema_version": 1, "answers": copy.deepcopy(expectations["expectations"])}
    answer_m001 = next(item for item in answers["answers"] if item["case_id"] == "M001")
    answer_m001["changes"][0]["after"] = 30
    report = evaluate(cases, expectations, answers)
    assert report["passed"] is False
    assert report["summary"]["passed_cases"] == 7
    assert report["summary"]["failed_cases"] == 1
    assert [item["case_id"] for item in report["results"] if not item["passed"]] == ["M001"]
