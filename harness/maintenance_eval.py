"""Deterministic evaluator for the synthetic maintenance semantic benchmark.

Scores candidate ``answers`` against a fixed ``expectations`` oracle over the
synthetic maintenance ``cases``. The evaluator is a pure function plus a
small read-only CLI: it never executes candidate code, never calls a model
or an SDK, never writes files, and never mutates its inputs. Reports never
echo source material and never authorise publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

SCHEMA_VERSION = 1
EVIDENCE_TAG = "synthetic_maintenance_semantic_evaluation_only"
MAX_INPUT_BYTES = 2 * 1024 * 1024

_CASES_ERROR = "invalid cases document"
_EXPECTATIONS_ERROR = "invalid expectations document"
_ANSWERS_ERROR = "invalid answers document"
_CLI_ERROR_LINE = "maintenance_eval: invalid input\n"

_REGIONS = frozenset({"domestic", "overseas"})
_SOURCE_STATUSES = frozenset({"archived", "source_missing"})
_DECISIONS = frozenset({"no_contract_change", "contract_change", "insufficient_evidence"})
_UNCERTAINTY_KINDS = frozenset({"source_missing", "source_conflict", "region_unverified"})

_TOP_CASES_KEYS = frozenset({"schema_version", "cases"})
_TOP_EXPECTATIONS_KEYS = frozenset({"schema_version", "expectations"})
_TOP_ANSWERS_KEYS = frozenset({"schema_version", "answers"})
_CASE_KEYS = frozenset(
    {
        "case_id",
        "synthetic",
        "region",
        "operation_id",
        "question",
        "fields",
        "before_status",
        "after_status",
        "before",
        "after",
    }
)
_ENTRY_KEYS = frozenset({"case_id", "decision", "changes", "uncertainties"})
_CHANGE_KEYS = frozenset({"field", "before", "after", "before_evidence", "after_evidence"})
_ROW_KEYS = frozenset({"id", "text"})


class MaintenanceEvaluationError(ValueError):
    """Raised when evaluator input fails strict protocol validation."""


def _require(condition, message):
    if not condition:
        raise MaintenanceEvaluationError(message)


def _non_empty_str(value):
    return type(value) is str and len(value) > 0


def _version_one(value):
    return type(value) is int and value == SCHEMA_VERSION


def _member(value, allowed):
    return type(value) is str and value in allowed


def _allowed_scalar(value):
    return value is None or type(value) in (str, int, bool)


def _scalar_identity(value):
    return (type(value), value)


def _row_id_set(rows, message):
    ids = set()
    for row in rows:
        _require(type(row) is dict and set(row) == _ROW_KEYS, message)
        _require(_non_empty_str(row["id"]) and _non_empty_str(row["text"]), message)
        _require(row["id"] not in ids, message)
        ids.add(row["id"])
    return ids


def _validate_cases(document):
    message = _CASES_ERROR
    _require(type(document) is dict and set(document) == _TOP_CASES_KEYS, message)
    _require(_version_one(document["schema_version"]), message)
    raw_cases = document["cases"]
    _require(type(raw_cases) is list and len(raw_cases) > 0, message)
    order = []
    info = {}
    for case in raw_cases:
        _require(type(case) is dict and set(case) == _CASE_KEYS, message)
        case_id = case["case_id"]
        _require(_non_empty_str(case_id) and case_id not in info, message)
        _require(case["synthetic"] is True, message)
        _require(_member(case["region"], _REGIONS), message)
        _require(_non_empty_str(case["operation_id"]), message)
        _require(_non_empty_str(case["question"]), message)
        fields = case["fields"]
        _require(type(fields) is list and len(fields) > 0, message)
        _require(all(_non_empty_str(item) for item in fields), message)
        _require(len(set(fields)) == len(fields), message)
        _require(_member(case["before_status"], _SOURCE_STATUSES), message)
        _require(_member(case["after_status"], _SOURCE_STATUSES), message)
        side_ids = {}
        for side, status_key in (("before", "before_status"), ("after", "after_status")):
            rows = case[side]
            _require(type(rows) is list, message)
            if case[status_key] == "archived":
                _require(len(rows) > 0, message)
            else:
                _require(len(rows) == 0, message)
            side_ids[side] = _row_id_set(rows, message)
        info[case_id] = {
            "fields": fields,
            "before_ids": side_ids["before"],
            "after_ids": side_ids["after"],
        }
        order.append(case_id)
    return order, info


def _validate_change(change, info, message):
    _require(type(change) is dict and set(change) == _CHANGE_KEYS, message)
    _require(_member(change["field"], info["fields"]), message)
    _require(_allowed_scalar(change["before"]) and _allowed_scalar(change["after"]), message)
    _require(_scalar_identity(change["before"]) != _scalar_identity(change["after"]), message)
    for key, ids in (
        ("before_evidence", info["before_ids"]),
        ("after_evidence", info["after_ids"]),
    ):
        evidence = change[key]
        _require(type(evidence) is list and len(evidence) > 0, message)
        _require(all(_non_empty_str(item) for item in evidence), message)
        _require(len(set(evidence)) == len(evidence), message)
        _require(all(item in ids for item in evidence), message)


def _validate_entry(entry, info, message):
    _require(type(entry) is dict and set(entry) == _ENTRY_KEYS, message)
    _require(_member(entry["decision"], _DECISIONS), message)
    changes = entry["changes"]
    _require(type(changes) is list, message)
    seen_fields = set()
    for change in changes:
        _validate_change(change, info, message)
        _require(change["field"] not in seen_fields, message)
        seen_fields.add(change["field"])
    uncertainties = entry["uncertainties"]
    _require(type(uncertainties) is list, message)
    _require(all(_member(item, _UNCERTAINTY_KINDS) for item in uncertainties), message)
    _require(len(set(uncertainties)) == len(uncertainties), message)
    decision = entry["decision"]
    if decision == "no_contract_change":
        _require(len(changes) == 0 and len(uncertainties) == 0, message)
    elif decision == "contract_change":
        _require(len(changes) > 0 and len(uncertainties) == 0, message)
    else:
        _require(len(changes) == 0 and len(uncertainties) > 0, message)


def _validate_entries(document, info_map, top_keys, list_key, message, require_all):
    _require(type(document) is dict and set(document) == top_keys, message)
    _require(_version_one(document["schema_version"]), message)
    entries = document[list_key]
    _require(type(entries) is list, message)
    if require_all:
        _require(len(entries) > 0, message)
    seen = set()
    parsed = {}
    for entry in entries:
        _require(type(entry) is dict, message)
        case_id = entry.get("case_id")
        _require(type(case_id) is str and case_id in info_map and case_id not in seen, message)
        seen.add(case_id)
        _validate_entry(entry, info_map[case_id], message)
        parsed[case_id] = entry
    if require_all:
        _require(seen == set(info_map), message)
    return parsed


def _changes_signature(changes):
    return {
        (
            change["field"],
            _scalar_identity(change["before"]),
            _scalar_identity(change["after"]),
            frozenset(change["before_evidence"]),
            frozenset(change["after_evidence"]),
        )
        for change in changes
    }


def evaluate(cases, expectations, answers):
    """Score candidate answers against the oracle without mutating inputs."""
    order, info_map = _validate_cases(cases)
    oracle = _validate_entries(
        expectations,
        info_map,
        _TOP_EXPECTATIONS_KEYS,
        "expectations",
        _EXPECTATIONS_ERROR,
        require_all=True,
    )
    submitted = _validate_entries(
        answers,
        info_map,
        _TOP_ANSWERS_KEYS,
        "answers",
        _ANSWERS_ERROR,
        require_all=False,
    )

    results = []
    answered_cases = 0
    passed_cases = 0
    for case_id in order:
        expected = oracle[case_id]
        candidate = submitted.get(case_id)
        problems = []
        if candidate is None:
            problems.append("missing_answer")
        else:
            answered_cases += 1
            if candidate["decision"] != expected["decision"]:
                problems.append("decision_mismatch")
            if _changes_signature(candidate["changes"]) != _changes_signature(expected["changes"]):
                problems.append("changes_mismatch")
            if set(candidate["uncertainties"]) != set(expected["uncertainties"]):
                problems.append("uncertainties_mismatch")
        if not problems:
            passed_cases += 1
        results.append({"case_id": case_id, "passed": not problems, "problems": problems})

    total_cases = len(results)
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence": EVIDENCE_TAG,
        "passed": passed_cases == total_cases,
        "summary": {
            "total_cases": total_cases,
            "answered_cases": answered_cases,
            "passed_cases": passed_cases,
            "failed_cases": total_cases - passed_cases,
        },
        "results": results,
        "publication_authorized": False,
        "patch_applied": False,
        "real_api_used": False,
    }


def _reject_constant(_value):
    raise MaintenanceEvaluationError("non-finite number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MaintenanceEvaluationError("duplicate key")
        result[key] = value
    return result


def _read_input(path):
    with open(path, "rb") as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise MaintenanceEvaluationError("input too large")
    parsed = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    return raw, parsed


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="harness.maintenance_eval",
        description="Score synthetic maintenance answers against a fixed oracle.",
    )
    parser.add_argument("--cases", required=True, help="path to the cases JSON file")
    parser.add_argument("--expectations", required=True, help="path to the expectations JSON file")
    parser.add_argument("--answers", required=True, help="path to the candidate answers JSON file")
    args = parser.parse_args(argv)

    try:
        documents = {}
        digests = {}
        for name in ("cases", "expectations", "answers"):
            raw, parsed = _read_input(getattr(args, name))
            documents[name] = parsed
            digests[name] = hashlib.sha256(raw).hexdigest()
        report = evaluate(documents["cases"], documents["expectations"], documents["answers"])
    except (OSError, ValueError, RecursionError):
        sys.stderr.write(_CLI_ERROR_LINE)
        return 2

    report["inputs_sha256"] = digests
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
