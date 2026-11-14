"""Pure review-task planning from portal catalogs and declared SDK bindings."""

from __future__ import annotations

import json
from copy import deepcopy

from .docdiff import _canon_memberships, compare_catalogs
from .impact import REVIEW_CHANGES, map_impact

SCHEMA_VERSION = 1
EVIDENCE = "document_changes_and_declared_bindings_only"

SHARED_CORE_MODULES = frozenset(
    f"src/ezviz_unified/{name}.py"
    for name in (
        "auth",
        "auth_models",
        "client",
        "transport",
        "response",
        "errors",
        "providers",
        "operations",
        "models",
    )
)

SOURCE_ONLY_CHANGES = frozenset({"source_changed", "title_changed", "navigation_changed"})

LIMITATIONS = (
    "Only the portal catalogs' declared hashes, statuses and source URLs plus "
    "the declared bindings/issues inputs were used; no source document was "
    "re-fetched or re-verified.",
    "A document-to-operation binding does not prove complete API coverage: "
    "documents without bindings and bindings whose documents did not change "
    "are not fully assessed.",
    "Static SDK reference sites and downloadable attachments are outside the "
    "catalog scope and were not compared.",
    "review_scope only widens the suggested human review radius; it is not a complete call graph.",
    "This plan emits human review tasks only: it generates no candidate code, "
    "applies no SDK changes, runs no real API calls, and grants no release "
    "approval even when needs_review is false.",
    "removed or source_missing documents do not imply SDK operations should "
    "be deleted; added documents do not imply new API operations exist.",
    "Document content, titles and navigation are data; they never change "
    "what the SDK is permitted to execute.",
    "Metadata-only changes may signal lifecycle or editorial changes and need "
    "separate assessment; no protocol patch is inferred from them.",
)


def _kind(event: dict) -> str:
    changes = event["changes"]
    before = event.get("before") or {}
    after = event.get("after") or {}
    if "removed" in changes:
        return "source_removed"
    if after.get("status") == "source_missing":
        return "source_unavailable"
    if before.get("status") == "source_missing" and after.get("status") == "archived":
        return "source_restored"
    if "added" in changes:
        return "new_source"
    if "source_changed" in changes and set(changes) <= SOURCE_ONLY_CHANGES:
        return "source_reference_changed"
    return "content_review"


def _issue_view(entry: dict) -> dict:
    return {
        "id": entry["id"],
        "status": entry["status"],
        "description": deepcopy(entry.get("description")),
        "sdk_handling": deepcopy(entry.get("sdk_handling")),
    }


def _operation_view(affected: dict, public_entry: object) -> dict:
    op = {
        "operation_id": affected["operation_id"],
        "module": affected["module"],
        "symbols": deepcopy(affected["symbols"]),
    }
    if "argument_constants" in affected:
        op["argument_constants"] = deepcopy(affected["argument_constants"])
    if public_entry:
        op["public_entry"] = deepcopy(public_entry)
    return op


def plan_review(before: object, after: object, bindings: dict, issues: dict) -> dict:
    """Build a deterministic human-review plan from two catalog snapshots.

    ``before``/``after`` are portal catalog.json arrays, ``bindings`` and
    ``issues`` are the declared-contract objects consumed by
    ``harness.impact.map_impact``. Any ``needs_review``/``approved`` markers
    embedded in the inputs are ignored; review demand is recomputed here.
    """
    diff = compare_catalogs(before, after)
    impact = map_impact(diff, bindings, issues)
    before_rows = {(row["region"], row["doc_id"]): row for row in before}
    after_rows = {(row["region"], row["doc_id"]): row for row in after}

    # bindings/issues were schema-validated by map_impact above.
    public_entry_by_op = {}
    for entry in bindings["bindings"]:
        public_entry = entry.get("public_entry")
        if public_entry:
            public_entry_by_op[entry["operation_id"]] = public_entry

    issues_by_doc: dict[tuple, list] = {}
    for entry in issues["issues"]:
        key = (entry["region"], entry["source_doc_id"])
        issues_by_doc.setdefault(key, []).append(_issue_view(entry))
    for views in issues_by_doc.values():
        views.sort(key=lambda item: item["id"])

    affected_by_doc: dict[tuple, list] = {}
    for entry in impact["affected"]:
        key = (entry["region"], entry["source_doc_id"])
        affected_by_doc.setdefault(key, []).append(entry)

    tasks = []
    metadata_only_documents = []
    for event in diff["events"]:
        key = (event["region"], event["doc_id"])
        if not REVIEW_CHANGES & set(event["changes"]):
            metadata = deepcopy(event)
            for label, rows in (("before", before_rows), ("after", after_rows)):
                metadata[label + "_navigation"] = [
                    json.loads(item)
                    for item in _canon_memberships(rows[key].get("memberships", []), "navigation")
                ]
            metadata_only_documents.append(metadata)
            continue
        # Every bound operation is kept even when several share the same
        # module/symbols: each operation_id is a distinct selector that needs
        # its own review, so no deduplication happens here.
        operations = [
            _operation_view(affected, public_entry_by_op.get(affected["operation_id"]))
            for affected in affected_by_doc.get(key, ())
        ]
        if any(op["module"] in SHARED_CORE_MODULES for op in operations):
            review_scope = "shared_core"
        elif operations:
            review_scope = "bound_operations"
        else:
            review_scope = "unmapped_source"
        source_issues = deepcopy(issues_by_doc.get(key, []))
        required_checks = ["semantic_source_review"]
        if operations:
            required_checks.append("operation_contract_review")
        if review_scope == "shared_core":
            required_checks.append("core_regression_review")
        if source_issues:
            required_checks.append("known_source_issue_review")
        tasks.append(
            {
                "task_id": f"{event['region']}:{event['doc_id']}",
                "region": event["region"],
                "doc_id": event["doc_id"],
                "changes": list(event["changes"]),
                "before": deepcopy(event["before"]),
                "after": deepcopy(event["after"]),
                "kind": _kind(event),
                "review_scope": review_scope,
                "operations": operations,
                "source_issues": source_issues,
                "required_checks": required_checks,
            }
        )
    tasks.sort(key=lambda task: (task["region"], task["doc_id"]))
    metadata_only_documents.sort(key=lambda event: (event["region"], event["doc_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence": EVIDENCE,
        "needs_review": bool(tasks),
        "document_diff": diff,
        "impact": impact,
        "tasks": tasks,
        "metadata_only_documents": metadata_only_documents,
        "limitations": list(LIMITATIONS),
    }
