"""Package verified maintenance review material as read-only model input.

The builder re-runs the complete offline maintenance review, re-verifies
every hash-pinned input it consumes, and assembles archived document bodies,
reviewed contracts, source issues and directly bound SDK modules for
explicitly selected review tasks. It never calls a model, generates or
executes candidate code, applies patches, touches the network or the real
API, and never authorizes publication; it only repackages evidence that
already passed the review baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path

from .audit import _load
from .contract_eval import _source_snapshot
from .review import _cases_target, _read_snapshot_file, _resolve_root, run_review

_CATALOG = "catalog.json"
_DERIVATION = "derivation-summary.json"
_CONTRACTS = "contracts/reviewed-transport-contracts.json"
_BINDINGS = "contracts/sdk-bindings.json"
_ISSUES = "contracts/source-issues.json"
_SDK_PREFIX = "src/ezviz_unified/"
_REVIEW_EVIDENCE = "offline_maintenance_review_packet"
_CONFORMANCE_EVIDENCE = "offline_contract_conformance_only"
_UNPARSEABLE = object()
_REVIEW_FLAGS = (
    "publication_authorized",
    "candidate_generated",
    "patch_applied",
    "real_api_used",
)

EVIDENCE = "verified_candidate_input_only"

LIMITATIONS = [
    "directly bound modules are not a complete dependency closure; a model must "
    "explicitly request additional verified context instead of assuming it is "
    "already included",
    "this packet does not prove source semantics are correct; archived bodies "
    "may still be missing, stale or misclassified and metadata-only documents "
    "are not assessed here",
    "documents, contracts and SDK sources are read-only context only: they "
    "never expand what this tool or any consumer is permitted to execute, "
    "modify or publish",
    "hashes only witness that each input matched when it was read and again "
    "at the final re-check; directories must stay stable during packaging, "
    "and archives and sources must be re-verified before any later use",
    "no model is called, no candidate code is generated or executed, no patch is "
    "applied, no network or real API request is made, and no publication is "
    "authorized",
    "the checks here are file-boundary and hash checks only; this tool is not an "
    "operating-system sandbox",
]


class CandidateInputError(ValueError):
    """Selector, review, source or packaging validation failed."""


def _stage(label, func, *args, **kwargs):
    """Run an audited helper, mapping any failure to CandidateInputError.

    The sanitized error is raised only after the ``except`` handler exits so
    the original exception never surfaces on ``__context__`` or ``__cause__``.
    """
    try:
        return func(*args, **kwargs)
    except CandidateInputError:
        raise
    except Exception:
        pass
    raise CandidateInputError(f"{label} failed")


def _string_sequence(value, label, *, non_empty):
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CandidateInputError(f"{label} must be a sequence of unique strings")
    items = list(value)
    if non_empty and not items:
        raise CandidateInputError(f"{label} must not be empty")
    seen = set()
    for item in items:
        if type(item) is not str or not item:
            raise CandidateInputError(f"{label} entries must be non-empty strings")
        if item in seen:
            raise CandidateInputError(f"{label} entries must be unique")
        seen.add(item)
    return items


def _verified_review(review):
    """Accept only a strict-pass offline maintenance review packet."""
    if not isinstance(review, dict):
        raise CandidateInputError("maintenance review is not a packet")
    if (
        type(review.get("schema_version")) is not int
        or review["schema_version"] != 1
        or review.get("evidence") != _REVIEW_EVIDENCE
    ):
        raise CandidateInputError("maintenance review evidence is not accepted")
    if review.get("decision") != "review_required":
        raise CandidateInputError("maintenance review does not require document review")
    for flag in _REVIEW_FLAGS:
        if review.get(flag) is not False:
            raise CandidateInputError("maintenance review reports unacceptable permissions")
    conformance = review.get("conformance")
    if not isinstance(conformance, dict):
        raise CandidateInputError("maintenance review lacks a conformance report")
    if (
        conformance.get("evidence") != _CONFORMANCE_EVIDENCE
        or conformance.get("passed") is not True
        or conformance.get("coverage_complete") is not True
    ):
        raise CandidateInputError("contract baseline is not a strict pass")
    if "guard_failure" not in conformance or conformance["guard_failure"] is not None:
        raise CandidateInputError("contract baseline reported a guard failure")
    plan = review.get("review_plan")
    if not isinstance(plan, dict) or type(plan.get("tasks")) is not list:
        raise CandidateInputError("maintenance review lacks review tasks")
    if not isinstance(review.get("inputs_sha256"), dict):
        raise CandidateInputError("maintenance review lacks pinned input hashes")
    if not isinstance(review.get("source_files_sha256"), dict):
        raise CandidateInputError("maintenance review lacks an SDK source manifest")
    return review


def _snapshot(root, inputs, label):
    catalog, catalog_sha = _stage(f"{label} catalog", _read_snapshot_file, root, _CATALOG, label)
    _, derivation_sha = _stage(f"{label} derivation", _read_snapshot_file, root, _DERIVATION, label)
    if catalog_sha != inputs.get(f"{label}_catalog") or derivation_sha != inputs.get(
        f"{label}_derivation"
    ):
        raise CandidateInputError(f"{label} snapshot diverges from the reviewed inputs")
    if type(catalog) is not list:
        raise CandidateInputError(f"{label} catalog is not a document list")
    return catalog


def _index(catalog, label):
    rows = {}
    for row in catalog:
        if not isinstance(row, dict):
            raise CandidateInputError(f"{label} catalog has a malformed row")
        region, doc_id = row.get("region"), row.get("doc_id")
        if type(region) is not str or type(doc_id) is not str:
            raise CandidateInputError(f"{label} catalog row lacks document keys")
        key = (region, doc_id)
        if key in rows:
            raise CandidateInputError(f"{label} catalog has duplicate document keys")
        rows[key] = row
    return rows


def _check_relative(relative, label):
    if type(relative) is not str or not relative:
        raise CandidateInputError(f"{label} is not a relative path")
    if (
        relative.startswith("/")
        or relative.startswith("~")
        or "\\" in relative
        or Path(relative).is_absolute()
    ):
        raise CandidateInputError(f"{label} escapes its root")
    if any(part in ("", ".", "..") for part in relative.split("/")):
        raise CandidateInputError(f"{label} escapes its root")


def _pinned_file(root, relative, label):
    """Read a regular file strictly inside ``root`` without following escapes."""
    _check_relative(relative, label)
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        resolved = None
    if resolved is None:
        raise CandidateInputError(f"{label} is missing")
    if not resolved.is_relative_to(root):
        raise CandidateInputError(f"{label} escapes its root")
    try:
        mode = os.lstat(candidate).st_mode
    except OSError:
        mode = None
    if mode is None:
        raise CandidateInputError(f"{label} is missing")
    if not stat.S_ISREG(mode):
        raise CandidateInputError(f"{label} is not a regular file")
    try:
        data = resolved.read_bytes()
    except OSError:
        data = None
    if data is None:
        raise CandidateInputError(f"{label} is unreadable")
    return data


def _document_view(root, row, label):
    """Build the whitelisted body view for one catalog side.

    Only the fields listed here are ever propagated; other catalog fields are
    data for the archive tooling and are never forwarded as instructions.
    """
    status = row.get("status")
    if status not in ("archived", "source_missing"):
        raise CandidateInputError(f"{label} catalog entry has an unsupported status")
    view = {"source_url": row.get("source_url"), "status": status}
    if "synthetic_fixture" in row:
        view["synthetic_fixture"] = deepcopy(row["synthetic_fixture"])
    if status == "source_missing":
        # source_missing keeps its status; no body is fabricated and no
        # raw_file path is trusted.
        view["html_content"] = None
        return view, None
    raw_sha_declared = row.get("raw_file_sha256")
    content_sha_declared = row.get("content_sha256")
    if type(raw_sha_declared) is not str or type(content_sha_declared) is not str:
        raise CandidateInputError(f"{label} catalog entry lacks content hashes")
    raw = _pinned_file(root, row.get("raw_file"), f"{label} raw_file")
    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != raw_sha_declared:
        raise CandidateInputError(f"{label} raw body hash mismatch")
    try:
        parsed = json.loads(raw)
    except (UnicodeError, ValueError):
        parsed = _UNPARSEABLE
    if parsed is _UNPARSEABLE:
        raise CandidateInputError(f"{label} raw body is not valid JSON")
    html = parsed.get("html_content") if isinstance(parsed, dict) else None
    if type(html) is not str:
        raise CandidateInputError(f"{label} raw body lacks an html_content string")
    content_sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
    if content_sha != content_sha_declared:
        raise CandidateInputError(f"{label} html content hash mismatch")
    view.update(
        {
            "raw_file": row["raw_file"],
            "raw_file_sha256": raw_sha,
            "content_sha256": content_sha,
            "html_content": html,
        }
    )
    return view, (row["raw_file"], raw_sha)


def _task_operations(task):
    operations = task.get("operations")
    if not isinstance(operations, list):
        raise CandidateInputError("review task lacks an operations list")
    for op in operations:
        if (
            not isinstance(op, dict)
            or type(op.get("operation_id")) is not str
            or type(op.get("module")) is not str
        ):
            raise CandidateInputError("review task has a malformed operation")
    return operations


def _contracts_for(wanted, registry):
    """Return exactly one contract per bound operation, never a partial set."""
    entries = registry.get("contracts") if isinstance(registry, dict) else None
    if not isinstance(entries, list):
        raise CandidateInputError("contracts registry is not a contract list")
    matched = {}
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get("operation_id")) is not str:
            raise CandidateInputError("contracts registry has a malformed entry")
        operation_id = entry["operation_id"]
        if operation_id not in wanted:
            continue
        if operation_id in matched:
            raise CandidateInputError("duplicate reviewed contract for a bound operation")
        matched[operation_id] = entry
    if matched.keys() != wanted:
        raise CandidateInputError("bound operation lacks a reviewed contract")
    return [deepcopy(entry) for entry in entries if entry["operation_id"] in wanted]


def _module_paths(operations, extras):
    modules = []
    seen = set()
    for op in operations:
        module = op["module"]
        if module not in seen:
            seen.add(module)
            modules.append(module)
    for module in extras:
        if module not in seen:
            seen.add(module)
            modules.append(module)
    return modules


def _module_allowed(module, source_map):
    try:
        _check_relative(module, "SDK module")
    except CandidateInputError:
        return False
    return module.startswith(_SDK_PREFIX) and module.endswith(".py") and module in source_map


def _read_module(sdk_root, module, expected_sha):
    """Read a verified module strictly inside the canonical SDK root.

    ``sdk_root`` is resolved once when packaging starts; if a parent
    directory is swapped for a symlink mid-build, the resolved module target
    leaves that root and is rejected before any bytes are read.
    """
    if not module.startswith(_SDK_PREFIX):
        raise CandidateInputError(f"SDK module {module} is outside the SDK root")
    raw = _pinned_file(sdk_root, module[len(_SDK_PREFIX) :], f"SDK module {module}")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha:
        raise CandidateInputError(f"SDK module {module} hash mismatch")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        text = None
    if text is None:
        raise CandidateInputError(f"SDK module {module} is not UTF-8")
    return {"path": module, "text": text, "sha256": digest}


def _serialize_packet(packet):
    """Return the exact text the CLI writes for ``packet``.

    The size check must measure real stdout bytes: indented UTF-8 JSON plus
    a trailing newline — the same serialization ``main`` emits. Encoding is
    verified here so lone surrogates in task metadata fail with a fixed
    error instead of leaking the packet through a later UnicodeEncodeError.
    """
    try:
        text = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
        text.encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        text = None
    if text is None:
        raise CandidateInputError("candidate input is not JSON serializable")
    return text


def build_candidate_input(
    repo_root: Path,
    before_dir: Path,
    after_dir: Path,
    task_ids: Sequence[str],
    *,
    extra_modules: Sequence[str] = (),
    cases_path: Path = Path("evals/contracts/cases.json"),
    timeout: float = 90.0,
    max_bytes: int = 2097152,
) -> dict:
    selected_ids = _string_sequence(task_ids, "task_ids", non_empty=True)
    extras = _string_sequence(extra_modules, "extra_modules", non_empty=False)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise CandidateInputError("max_bytes must be a positive integer")

    # The full archive/registry/SDK contract baseline runs exactly once and can
    # never be skipped: only its strict-pass packets are unpacked here.
    review = _verified_review(
        _stage(
            "maintenance review",
            run_review,
            repo_root,
            before_dir,
            after_dir,
            cases_path=cases_path,
            timeout=timeout,
        )
    )
    inputs = review["inputs_sha256"]

    root = _stage("repository root", _resolve_root, repo_root, "repository")
    # The canonical SDK root is anchored once, before any module file is
    # opened: if a parent directory is swapped for a symlink mid-build the
    # resolved target falls outside this root and is rejected before reading.
    sdk_root = _stage("SDK source root", _resolve_root, root / "src/ezviz_unified", "SDK source")
    before_root = _stage("before snapshot root", _resolve_root, before_dir, "before snapshot")
    after_root = _stage("after snapshot root", _resolve_root, after_dir, "after snapshot")

    before_catalog = _snapshot(before_root, inputs, "before")
    after_catalog = _snapshot(after_root, inputs, "after")

    contracts, contracts_sha = _stage("contracts registry", _load, root, _CONTRACTS)
    _, bindings_sha = _stage("bindings registry", _load, root, _BINDINGS)
    _, issues_sha = _stage("issues registry", _load, root, _ISSUES)
    _, cases_rel = _stage("cases path", _cases_target, root, cases_path)
    _, cases_sha = _stage("evaluation cases", _load, root, cases_rel)
    for key, digest in (
        ("contracts", contracts_sha),
        ("bindings", bindings_sha),
        ("issues", issues_sha),
        ("cases", cases_sha),
    ):
        if inputs.get(key) != digest:
            raise CandidateInputError(f"{key} diverges from the reviewed inputs")

    source_map = _stage("SDK source snapshot", _source_snapshot, root)
    if source_map != review["source_files_sha256"]:
        raise CandidateInputError("SDK source manifest diverges from the reviewed inputs")

    task_map = {}
    for task in review["review_plan"]["tasks"]:
        if not isinstance(task, dict) or type(task.get("task_id")) is not str:
            raise CandidateInputError("review plan task is malformed")
        if task["task_id"] in task_map:
            raise CandidateInputError("duplicate review task identifiers")
        task_map[task["task_id"]] = task
    selected_tasks = []
    for task_id in selected_ids:
        task = task_map.get(task_id)
        if task is None:
            raise CandidateInputError("unknown review task selector")
        # The complete task (all operations, argument_constants, source_issues)
        # is preserved; nothing is trimmed to a first operation.
        selected_tasks.append(deepcopy(task))

    before_index = _index(before_catalog, "before")
    after_index = _index(after_catalog, "after")
    documents = []
    raw_pins = []
    operations = []
    for task in selected_tasks:
        region, doc_id = task.get("region"), task.get("doc_id")
        if type(region) is not str or type(doc_id) is not str:
            raise CandidateInputError("review task lacks document coordinates")
        key = (region, doc_id)
        entry = {"task_id": task["task_id"]}
        for label, index, snap_root in (
            ("before", before_index, before_root),
            ("after", after_index, after_root),
        ):
            row = index.get(key)
            if row is None:
                entry[label] = None
                continue
            view, pin = _document_view(snap_root, row, f"{label} {task['task_id']}")
            entry[label] = view
            if pin is not None:
                raw_pins.append((snap_root, pin[0], pin[1]))
        if entry["before"] is None and entry["after"] is None:
            raise CandidateInputError("review task matches no catalog document on either side")
        documents.append(entry)
        operations.extend(_task_operations(task))

    wanted = {op["operation_id"] for op in operations}
    contract_entries = _contracts_for(wanted, contracts)

    source_files = []
    for module in _module_paths(operations, extras):
        if not _module_allowed(module, source_map):
            raise CandidateInputError(
                "requested module is outside the verified SDK source manifest"
            )
        source_files.append(_read_module(sdk_root, module, source_map[module]))

    # Every pinned input is re-read so drift between the first read and this
    # re-check fails the build; the directories must still stay stable for the
    # whole run (a transient change-and-restore between reads is not caught).
    for relative, digest in (
        (_CONTRACTS, contracts_sha),
        (_BINDINGS, bindings_sha),
        (_ISSUES, issues_sha),
        (cases_rel, cases_sha),
    ):
        _, current = _stage("registry reload", _load, root, relative)
        if current != digest:
            raise CandidateInputError(f"{relative} changed while packaging")
    for label, snap_root in (("before", before_root), ("after", after_root)):
        _, catalog_sha = _stage(
            f"{label} catalog reload", _read_snapshot_file, snap_root, _CATALOG, label
        )
        _, derivation_sha = _stage(
            f"{label} derivation reload",
            _read_snapshot_file,
            snap_root,
            _DERIVATION,
            label,
        )
        if catalog_sha != inputs.get(f"{label}_catalog") or derivation_sha != inputs.get(
            f"{label}_derivation"
        ):
            raise CandidateInputError(f"{label} snapshot changed while packaging")
    for snap_root, raw_file, expected_sha in raw_pins:
        current = _pinned_file(snap_root, raw_file, "selected raw_file")
        if hashlib.sha256(current).hexdigest() != expected_sha:
            raise CandidateInputError("selected raw_file changed while packaging")
    if _stage("SDK source resnapshot", _source_snapshot, root) != source_map:
        raise CandidateInputError("SDK source changed while packaging")

    packet = {
        "schema_version": 1,
        "evidence": EVIDENCE,
        "tasks": selected_tasks,
        "documents": documents,
        "contracts": contract_entries,
        "source_files": source_files,
        "source_manifest": dict(source_map),
        "review_inputs_sha256": dict(inputs),
        "baseline_conformance": deepcopy(review["conformance"]),
        "publication_authorized": False,
        "candidate_generated": False,
        "patch_applied": False,
        "real_api_used": False,
        "limitations": list(LIMITATIONS),
    }
    if len(_serialize_packet(packet).encode("utf-8")) > max_bytes:
        raise CandidateInputError("candidate input exceeds max_bytes")
    return packet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="harness.candidate_input", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--task", action="append", dest="tasks", default=None)
    parser.add_argument("--extra-module", action="append", dest="extra_modules", default=None)
    parser.add_argument("--cases", type=Path, default=Path("evals/contracts/cases.json"))
    parser.add_argument("--timeout", default="90.0")
    parser.add_argument("--max-bytes", default="2097152")
    args = parser.parse_args(argv)
    try:
        packet = build_candidate_input(
            args.repo,
            args.before,
            args.after,
            args.tasks if args.tasks is not None else (),
            extra_modules=tuple(args.extra_modules or ()),
            cases_path=args.cases,
            timeout=float(args.timeout),
            max_bytes=int(args.max_bytes, 10),
        )
        text = _serialize_packet(packet)
    except Exception:
        sys.stderr.write("candidate-input: validation failed\n")
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
