"""Build an offline maintenance review packet from two portal snapshots.

The review resolves and hashes the snapshot manifests, independently
verifies both archives, re-audits the reviewed registry, confirms the
document-to-contract link baseline against the earlier snapshot, plans the
document review tasks, and re-runs the synthetic SDK contract baseline.
It never generates candidates, modifies SDK sources or snapshots, touches
the network or the real API, or authorizes publication; it only packages
evidence that already exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from tools.documents.verify import verify as verify_snapshot

from .audit import _load, audit_repository
from .contract_eval import _source_snapshot, run_evaluation
from .review_plan import plan_review
from .source_inventory import inventory

_CATALOG = "catalog.json"
_DERIVATION = "derivation-summary.json"
_CONTRACTS = "contracts/reviewed-transport-contracts.json"
_BINDINGS = "contracts/sdk-bindings.json"
_ISSUES = "contracts/source-issues.json"

EVIDENCE = "offline_maintenance_review_packet"

LIMITATIONS = [
    "static SDK reference pages and attachments inside the snapshots are not re-compared",
    "snapshot integrity only proves the archived evidence chain is consistent; it does not "
    "prove the live portal has no missing documents or that document semantics are correct",
    "a passing baseline only covers reviewed SDK contracts against synthetic transports; it "
    "does not prove any future candidate is correct, is not a model evaluation score, and "
    "grants no release permission",
    "the full pytest suite is not run by this review",
    "snapshot directories must remain unchanged after this review; any later use of the "
    "archived documents must re-verify them against the recorded hashes",
    "no candidate is generated, no SDK or snapshot file is modified, no network or real API "
    "request is made, and nothing is published",
]


class ReviewError(ValueError):
    """Snapshot, registry, or evaluation validation failed."""


def _stage(label, func, *args, **kwargs):
    """Run an audited helper, mapping any failure to ReviewError."""
    try:
        return func(*args, **kwargs)
    except Exception:
        pass
    raise ReviewError(f"{label} failed")


def _resolve_root(path, label):
    try:
        root = Path(path).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ReviewError(f"{label} root cannot be resolved") from None
    if not root.is_dir():
        raise ReviewError(f"{label} root is not a directory")
    return root


def _read_snapshot_file(root, name, label):
    try:
        target = (root / name).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ReviewError(f"{label} {name} is missing") from None
    if not target.is_relative_to(root) or not target.is_file():
        raise ReviewError(f"{label} {name} escapes the snapshot root")
    try:
        raw = target.read_bytes()
    except OSError:
        raise ReviewError(f"{label} {name} is unreadable") from None
    try:
        parsed = json.loads(raw)
    except (UnicodeError, ValueError):
        raise ReviewError(f"{label} {name} is not valid JSON") from None
    return parsed, hashlib.sha256(raw).hexdigest()


def _snapshot_inputs(root, label):
    catalog, catalog_sha = _read_snapshot_file(root, _CATALOG, label)
    _, derivation_sha = _read_snapshot_file(root, _DERIVATION, label)
    return catalog, {_CATALOG: catalog_sha, _DERIVATION: derivation_sha}


def _verified_snapshot(root, hashes, label):
    report = _stage(f"{label} snapshot verification", verify_snapshot, root)
    if not isinstance(report, dict) or report.get("passed") is not True:
        raise ReviewError(f"{label} snapshot verification failed")
    if report.get("input_catalog_sha256") != hashes[_CATALOG]:
        raise ReviewError(f"{label} {_CATALOG} changed during verification")
    if report.get("derivation_summary_sha256") != hashes[_DERIVATION]:
        raise ReviewError(f"{label} {_DERIVATION} changed during verification")
    return report


def _integrity(report):
    return {
        "passed": True,
        "scope_coverage": report.get("scope_coverage"),
        "documents": report.get("documents"),
        "archived_bodies": report.get("archived_bodies"),
        "all_discovered_bodies_available": report.get("all_discovered_bodies_available"),
        "missing_body_classifications": dict(report.get("missing_body_classifications") or {}),
        "encoding_warning_count": report.get("encoding_warning_count"),
        "reference_edges_checked": report.get("reference_edges_checked"),
    }


def _cases_target(root, cases_path):
    try:
        source = Path(cases_path)
        source = (source if source.is_absolute() else root / source).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ReviewError("cases path cannot be resolved") from None
    if not source.is_relative_to(root):
        raise ReviewError("cases must be inside the repository")
    return source, source.relative_to(root).as_posix()


def _audit_consistent(audit, digests):
    recorded = audit.get("inputs_sha256") if isinstance(audit, dict) else None
    if not isinstance(recorded, dict):
        raise ReviewError("audit report lacks input hashes")
    for relative, digest in digests.items():
        if recorded.get(relative) != digest:
            raise ReviewError(f"{relative} diverged during the audit")


def _confirm_evaluation(evaluation, contracts_sha, bindings_sha, cases_sha, source_files):
    if not isinstance(evaluation, dict):
        raise ReviewError("invalid evaluation report")
    if (
        evaluation.get("evidence") != "offline_contract_conformance_only"
        or evaluation.get("real_api_used") is not False
    ):
        raise ReviewError("evaluation is not an offline contract baseline")
    inputs = evaluation.get("inputs_sha256")
    if not isinstance(inputs, dict):
        raise ReviewError("evaluation report lacks input hashes")
    if (
        inputs.get("contracts") != contracts_sha
        or inputs.get("bindings") != bindings_sha
        or inputs.get("cases") != cases_sha
    ):
        raise ReviewError("evaluation inputs diverge from the audited registry")
    if evaluation.get("source_files_sha256") != source_files:
        raise ReviewError("evaluation ran against a different SDK source set")


def _require_unchanged(root, relative, digest):
    _, current = _stage("registry reload", _load, root, relative)
    if current != digest:
        raise ReviewError(f"{relative} changed during the review")


def _snapshot_unchanged(root, hashes, label):
    _, catalog_sha = _read_snapshot_file(root, _CATALOG, label)
    _, derivation_sha = _read_snapshot_file(root, _DERIVATION, label)
    if catalog_sha != hashes[_CATALOG] or derivation_sha != hashes[_DERIVATION]:
        raise ReviewError(f"{label} snapshot changed during the review")


def _conformance(evaluation):
    failed = sorted(
        {
            row["operation_id"]
            for row in evaluation.get("results") or ()
            if isinstance(row, dict)
            and row.get("passed") is False
            and isinstance(row.get("operation_id"), str)
        }
    )
    return {
        "evidence": evaluation.get("evidence"),
        "passed": evaluation.get("passed"),
        "coverage_complete": evaluation.get("coverage_complete"),
        "summary": evaluation.get("summary"),
        "guard_failure": evaluation.get("guard_failure"),
        "failed_operations": failed,
    }


def run_review(
    repo_root: Path,
    before_dir: Path,
    after_dir: Path,
    *,
    cases_path: Path = Path("evals/contracts/cases.json"),
    timeout: float = 90.0,
) -> dict:
    root = _resolve_root(repo_root, "repository")
    before_root = _resolve_root(before_dir, "before snapshot")
    after_root = _resolve_root(after_dir, "after snapshot")

    # Step 1: read and hash both snapshot manifests inside their roots.
    before_catalog, before_hashes = _snapshot_inputs(before_root, "before")
    after_catalog, after_hashes = _snapshot_inputs(after_root, "after")

    # Step 2: independently verify both archives before any SDK evaluation.
    before_report = _verified_snapshot(before_root, before_hashes, "before")
    after_report = _verified_snapshot(after_root, after_hashes, "after")

    # Step 3: pin SDK sources, re-audit the registry, and confirm that the
    # recorded contract links still match the earlier snapshot exactly.
    source_files = _stage("SDK source snapshot", _source_snapshot, root)
    audit = _stage("registry audit", audit_repository, root)
    cases_file, cases_rel = _cases_target(root, cases_path)
    contracts, contracts_sha = _stage("contracts registry", _load, root, _CONTRACTS)
    bindings, bindings_sha = _stage("bindings registry", _load, root, _BINDINGS)
    issues, issues_sha = _stage("issues registry", _load, root, _ISSUES)
    _audit_consistent(
        audit,
        {_CONTRACTS: contracts_sha, _BINDINGS: bindings_sha, _ISSUES: issues_sha},
    )
    _, cases_sha = _stage("evaluation cases", _load, root, cases_rel)
    links = _stage("source inventory", inventory, before_catalog, contracts)

    # Step 4: build the review task packet and always run the full baseline.
    plan = _stage("review planning", plan_review, before_catalog, after_catalog, bindings, issues)
    if not isinstance(plan, dict) or type(plan.get("needs_review")) is not bool:
        raise ReviewError("invalid review plan")
    evaluation = _stage("contract evaluation", run_evaluation, root, cases_file, timeout=timeout)

    # Step 5: refuse mixed-version reports; every pinned input must be stable.
    _confirm_evaluation(evaluation, contracts_sha, bindings_sha, cases_sha, source_files)
    for relative, digest in (
        (_CONTRACTS, contracts_sha),
        (_BINDINGS, bindings_sha),
        (_ISSUES, issues_sha),
        (cases_rel, cases_sha),
    ):
        _require_unchanged(root, relative, digest)
    _snapshot_unchanged(before_root, before_hashes, "before")
    _snapshot_unchanged(after_root, after_hashes, "after")
    if _stage("SDK source snapshot", _source_snapshot, root) != source_files:
        raise ReviewError("SDK source changed during the review")

    conformance = _conformance(evaluation)
    if conformance["passed"] is not True or conformance["coverage_complete"] is not True:
        decision = "baseline_failed"
    elif plan["needs_review"] is True:
        decision = "review_required"
    elif plan["metadata_only_documents"]:
        decision = "metadata_only"
    else:
        decision = "no_document_review"

    return {
        "schema_version": 1,
        "evidence": EVIDENCE,
        "decision": decision,
        "snapshot_integrity": {
            "before": _integrity(before_report),
            "after": _integrity(after_report),
        },
        "baseline_source_links": links["summary"],
        "registry_audit": {
            "evidence": audit.get("evidence"),
            "registry": audit.get("registry"),
            "code": {"summary": audit.get("code", {}).get("summary")},
        },
        "conformance": conformance,
        "review_plan": plan,
        "inputs_sha256": {
            "before_catalog": before_hashes[_CATALOG],
            "before_derivation": before_hashes[_DERIVATION],
            "after_catalog": after_hashes[_CATALOG],
            "after_derivation": after_hashes[_DERIVATION],
            "contracts": contracts_sha,
            "bindings": bindings_sha,
            "issues": issues_sha,
            "cases": cases_sha,
        },
        "source_files_sha256": source_files,
        "publication_authorized": False,
        "candidate_generated": False,
        "patch_applied": False,
        "real_api_used": False,
        "limitations": list(LIMITATIONS),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="harness.review", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=Path("evals/contracts/cases.json"))
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args(argv)
    try:
        packet = run_review(
            args.repo,
            args.before,
            args.after,
            cases_path=args.cases,
            timeout=args.timeout,
        )
    except Exception:
        sys.stderr.write("harness-review: source, registry or evaluation validation failed\n")
        return 2
    sys.stdout.write(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    return 1 if packet["decision"] == "baseline_failed" else 0


if __name__ == "__main__":
    sys.exit(main())
