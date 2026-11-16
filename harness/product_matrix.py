"""Offline official capability/source matrix builder (repo tool, not SDK).

Reads a caller-supplied snapshot manifest plus the reviewed registry
(reused through :func:`harness.audit.audit_repository`) and produces the
accountability matrix described in
``docs/plans/2026-11-17-source-ledger-publication-boundary.md``.

Evidence is limited to recorded source snapshots and the audited registry:
no URL is fetched, no device is contacted, no SDK module is imported or
executed, and no input file is modified. Output is a review aid only — it
never claims live verification, product completeness, or an API total.

Publication boundary (schema 2): the returned/stdout/file report is an
explicit per-type whitelist projection. Raw recorded locators (URLs,
paths, hosts, section titles, discovered_from, asset ids, file names,
reference locators, free decision text) stay internal for the exact join
and never reach the report; portal sources publish only a canonical
document URL constructed from the reviewed region + numeric doc_id
identity. There is no raw-output mode.

CLI:
    python -m harness.product_matrix --repo PATH --manifest PATH \
        [--decisions PATH] [--output PATH] [--summary-only]
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from .audit import AuditInputError, audit_repository
from .code_audit import CodeAuditError
from .registry import RegistryError

# Output report schema. Input schemas are separate constants and stay at 1:
# the frozen manifest/decisions files are unchanged by this revision.
SCHEMA_VERSION = 2
INPUT_MANIFEST_SCHEMA_VERSION = 1
INPUT_DECISIONS_SCHEMA_VERSION = 1
PUBLICATION_PROFILE = "reviewed_metadata_only"
EVIDENCE = "source_and_registry_matrix_only"

DISCLAIMER = (
    "Source accountability matrix over recorded snapshots and the audited "
    "registry only; not an API total, support rate, live verification, or "
    "release-readiness statement."
)
LIMITATIONS = [
    "counts are source/observation/contract counts, not API totals or coverage",
    "offline_status/live_status replay the audited binding records verbatim; this run performs no live verification",
    "implementation_status=ast_verified only proves declared symbols resolve in the SDK AST, not runtime behavior",
    "observations are projected declarations/examples from an HTTP inventory; a declaration does not prove an endpoint exists or is supported",
    "same_route hints are review leads, never evidence of reviewed support",
    "example related_declaration_ordinals/agrees flags are parser hints only; they never promote an example into exact_operation_ids or reviewed support",
    "provisional capabilities mark unreviewed observations; they are not implementations and not part of any denominator",
    "a decision records a reviewer disposition only; it does not prove code, tests, or review happened",
    "manifest-declared hashes tie output to input bytes but do not prove official documentation content is correct",
    "published source locators are canonical document URLs rebuilt from the reviewed region/numeric-doc_id portal identity plus reviewed-registry constant paths; raw recorded URLs, query values, fragments, userinfo, paths, hosts, section titles, asset identifiers, discovered/reference locators, and reviewer free text stay inside the internal evidence records and are never published — no field is anonymized by hashing",
]

_SNAPSHOT_KINDS = frozenset({"portal", "sdk_reference", "assets"})
_MANIFEST_NAMES = {
    "portal": "catalog.json",
    "sdk_reference": "manifest.json",
    "assets": "manifest.jsonl",
}
_DISPOSITIONS = frozenset(
    {
        "implement",
        "reuse_official_component",
        "not_an_api",
        "duplicate",
        "deprecated",
        "phase_2",
        "needs_review",
        "blocked_source",
    }
)
_REGISTRY_INPUTS = (
    "contracts/reviewed-transport-contracts.json",
    "contracts/sdk-bindings.json",
    "contracts/source-issues.json",
)
_CONTRACTS_INPUT = _REGISTRY_INPUTS[0]
_BINDINGS_INPUT = _REGISTRY_INPUTS[1]

_MAX_JSON_BYTES = 256 * 1024 * 1024
_MAX_JSONL_LINES = 500_000
_MAX_SNAPSHOTS = 10_000
_MAX_ROWS = 1_000_000
_MAX_LIST = 1_000_000
_MAX_TEXT = 2 * 1024 * 1024
_MAX_ID = 4096
_MAX_CAPABILITY_ID = 8192
_MAX_REASON = 64 * 1024
_MAX_REASONS = 1000
_MAX_WARNINGS = 1000
_MAX_EVIDENCE_ITEMS = 1000
_MAX_EVIDENCE_REFS = 1000
_MAX_DECISIONS = 1_000_000
_MAX_REFERENCES = 10_000
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DOC_ID_RE = re.compile(r"[0-9]+")

# Portal identity is the only publishable source locator identity: a
# reviewed region label plus an ASCII numeric doc_id. Anything else is a
# hard input failure, never an anonymized or degraded publication.
_PORTAL_REGIONS = frozenset({"domestic", "overseas"})
_PORTAL_URL_PREFIX = {
    "domestic": "https://open.ys7.com/help/",
    "overseas": "https://open.ys7.com/help/en/",
}

# Fixed publication vocabularies. Values outside each known set collapse
# to the listed fallback token; the raw string is never republished.
_PORTAL_SOURCE_STATUSES = frozenset(
    {
        "archived",
        "source_missing",
        "source_empty",
        "platform_mismatch",
        "acquisition_failed",
        "inventory_only",
    }
)
_SDK_REFERENCE_STATUSES = frozenset(
    {
        "ok",
        "http_error",
        "redirect_outside_or_alias",
        "not_html",
        "missing_html_title",
        "network_error",
        "queued",
    }
)
_ASSET_STATUSES = frozenset(
    {
        "ok",
        "already_archived_document",
        "size_limit",
        "source_invalid_document",
        "source_mime_mismatch",
        "excluded_sensitive_query",
        "excluded_url_policy",
        "excluded_non_document_media",
        "excluded_sdk_binary",
        "excluded_placeholder",
        "source_http_error",
        "source_redirect_limit",
        "network_error",
    }
)
_OBSERVATION_STATUSES = frozenset({"declared_pair", "ambiguous", "address_only", "example_only"})
_HTTP_METHODS = frozenset(
    {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
        "TRACE",
        "CONNECT",
    }
)
_REASON_CODES = frozenset(
    {
        "address_backtick",
        "conflicting_methods",
        "invalid_address",
        "missing_method",
        "multiple_methods",
        "multiple_urls",
        "no_explicit_method",
        "templated_host",
        "templated_path",
        "unknown_method",
        "unparsed_curl",
    }
)
_ASSET_CATEGORIES = frozenset({"image", "document"})
_REFERENCE_TYPES = frozenset({"link", "image", "embedded_resource"})
_UNRESOLVED_REASONS = frozenset(
    {
        "source_body_unavailable",
        "snapshot_body_unverified",
        "inventory_identity_mismatch",
        "inventory_document_not_in_catalog",
        "asset_body_unavailable_or_excluded",
        "official_reuse_candidate_pending_review",
        "inventory_warning",
        "needs_review",
        "blocked_source",
        "contract_source_document_absent",
        "contract_source_identity_mismatch",
        "contract_declaration_not_recovered",
        "binding_record_missing",
    }
)
_UNRESOLVED_KINDS = frozenset({"reviewed_contract_source", "source", "disposition", "observation"})


class ProductMatrixError(ValueError):
    """Fixed validation failure; messages never echo raw input values."""


class _RejectedJSON(Exception):
    """Sentinel raised by JSON hooks; converted to a fixed failure."""


def _err(reason: str) -> ProductMatrixError:
    return ProductMatrixError(f"product_matrix: {reason}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_duplicates(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _RejectedJSON()
        result[key] = value
    return result


def _reject_constant(_name: str) -> None:
    raise _RejectedJSON()


def _checked_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _RejectedJSON()
    return value


def _strict_json(raw: bytes, reason: str) -> Any:
    if len(raw) > _MAX_JSON_BYTES:
        raise _err("input too large")
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        raise _err(reason) from None
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_checked_float,
        )
    except (ValueError, _RejectedJSON, RecursionError, MemoryError):
        raise _err(reason) from None


def _resolve_dir(value: Any, reason: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise _err(reason)
    try:
        path = Path(value)
        if not path.is_absolute():
            return None
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_dir():
        return None
    return resolved


def _resolve_file(path: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _resolve_inside(root: Path, value: Any) -> Path | None:
    """Resolve a manifest-declared relative path to a file inside root."""
    if not isinstance(value, str) or not value:
        return None
    try:
        candidate = Path(value)
        if candidate.is_absolute():
            return None
        resolved = (root / candidate).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        if resolved == root or not resolved.is_relative_to(root):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_bool(value: Any) -> bool:
    return type(value) is bool


def _is_index(value: Any) -> bool:
    return type(value) is int and value >= 0


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value and len(value) <= _MAX_TEXT:
        return value
    return None


def _identifier(value: Any) -> str | None:
    if isinstance(value, str) and value and len(value) <= _MAX_ID:
        return value
    return None


def _portal_identity(region: Any, doc_id: Any) -> tuple[str, str] | None:
    """Return the reviewed portal identity or None (caller decides fail/null)."""
    if region in _PORTAL_REGIONS and isinstance(doc_id, str) and _DOC_ID_RE.fullmatch(doc_id):
        return region, doc_id
    return None


def _canonical_document_url(region: Any, doc_id: Any) -> str | None:
    """Portal locator rebuilt from reviewed identity parts only.

    The URL is constructed from the fixed per-region prefix plus the ASCII
    numeric doc_id — never derived from, compared with, or grafted onto a
    recorded raw URL.
    """
    identity = _portal_identity(region, doc_id)
    if identity is None:
        return None
    return _PORTAL_URL_PREFIX[identity[0]] + identity[1]


def _load_snapshot_file(root: Path, name: str, expected_sha: str, kind: str) -> bytes:
    path = _resolve_inside(root, name)
    if path is None:
        raise _err(f"{kind} manifest file is missing or outside the snapshot root")
    raw = _read(path)
    if raw is None:
        raise _err(f"{kind} manifest file is unreadable")
    if _sha256(raw) != expected_sha:
        raise _err(f"{kind} manifest sha256 mismatch")
    return raw


def _load_manifest(manifest_path: Path) -> dict:
    raw = _read(manifest_path)
    if raw is None:
        raise _err("manifest is unreadable")
    data = _strict_json(raw, "manifest must be strict JSON")
    if not isinstance(data, dict) or data.get("schema_version") != INPUT_MANIFEST_SCHEMA_VERSION:
        raise _err("manifest must be a schema_version 1 object")
    snapshots = data.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots or len(snapshots) > _MAX_SNAPSHOTS:
        raise _err("manifest snapshots must be a non-empty list")
    return data


def _check_snapshots(manifest: dict) -> list[dict]:
    seen_ids = set()
    resolved = []
    for raw_snap in manifest["snapshots"]:
        if not isinstance(raw_snap, dict):
            raise _err("snapshot entries must be objects")
        snap_id = _identifier(raw_snap.get("id"))
        if snap_id is None or snap_id in seen_ids:
            raise _err("snapshot id must be a unique non-empty string")
        seen_ids.add(snap_id)
        kind = raw_snap.get("kind")
        if kind not in _SNAPSHOT_KINDS:
            raise _err("snapshot kind must be portal, sdk_reference or assets")
        root = _resolve_dir(raw_snap.get("root"), "snapshot root must be a directory")
        if root is None:
            raise _err("snapshot root must be a resolvable absolute directory")
        manifest_sha = raw_snap.get("manifest_sha256")
        if not _is_sha256(manifest_sha):
            raise _err("snapshot manifest_sha256 must be 64 lowercase hex")
        region = raw_snap.get("region")
        if region is not None and not isinstance(region, str):
            raise _err("snapshot region must be a string or absent")
        inventory = raw_snap.get("inventory")
        inventory_sha = raw_snap.get("inventory_sha256")
        if (inventory is None) != (inventory_sha is None):
            raise _err("inventory path and inventory_sha256 must appear together")
        if inventory_sha is not None and not _is_sha256(inventory_sha):
            raise _err("inventory_sha256 must be 64 lowercase hex")
        if inventory is not None and kind != "portal":
            raise _err("only portal snapshots may declare an HTTP inventory")
        inventory_path = _resolve_file(Path(inventory)) if inventory is not None else None
        if inventory is not None and inventory_path is None:
            raise _err("inventory must be a resolvable absolute file")
        resolved.append(
            {
                "id": snap_id,
                "kind": kind,
                "root": root,
                "manifest_sha256": manifest_sha,
                "inventory": inventory_path,
                "inventory_sha256": inventory_sha,
                "region": region,
            }
        )
    return resolved


def _load_portal_inputs(snapshot: dict) -> tuple[list, dict | None]:
    catalog_raw = _load_snapshot_file(
        snapshot["root"], _MANIFEST_NAMES["portal"], snapshot["manifest_sha256"], "portal"
    )
    catalog = _strict_json(catalog_raw, "portal catalog must be strict JSON")
    if not isinstance(catalog, list) or len(catalog) > _MAX_ROWS:
        raise _err("portal catalog must be a JSON array")
    inventory = None
    if snapshot["inventory"] is not None:
        raw = _read(snapshot["inventory"])
        if raw is None or _sha256(raw) != snapshot["inventory_sha256"]:
            raise _err("inventory bytes do not match the declared sha256")
        inventory = _strict_json(raw, "inventory must be strict JSON")
        if not isinstance(inventory, dict) or not isinstance(inventory.get("documents"), list):
            raise _err("inventory must be an object with a documents list")
        if len(inventory["documents"]) > _MAX_ROWS:
            raise _err("inventory documents list is too large")
    return catalog, inventory


def _load_reference_entries(snapshot: dict) -> list:
    raw = _load_snapshot_file(
        snapshot["root"],
        _MANIFEST_NAMES["sdk_reference"],
        snapshot["manifest_sha256"],
        "sdk_reference",
    )
    data = _strict_json(raw, "sdk_reference manifest must be strict JSON")
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise _err("sdk_reference manifest must be an object with an entries list")
    if len(data["entries"]) > _MAX_ROWS:
        raise _err("sdk_reference entries list is too large")
    return data["entries"]


def _load_asset_entries(snapshot: dict) -> list:
    raw = _load_snapshot_file(
        snapshot["root"], _MANIFEST_NAMES["assets"], snapshot["manifest_sha256"], "assets"
    )
    entries = []
    for line in raw.split(b"\n"):
        if len(entries) >= _MAX_JSONL_LINES:
            raise _err("assets manifest exceeds the line limit")
        if not line.strip():
            continue
        entry = _strict_json(line, "assets manifest must contain strict JSON objects")
        if not isinstance(entry, dict):
            raise _err("assets manifest lines must be JSON objects")
        entries.append(entry)
    return entries


def _category_paths_from_memberships(memberships: Any) -> list[list[str]] | None:
    if memberships is None:
        return []
    if not isinstance(memberships, list) or len(memberships) > _MAX_LIST:
        return None
    paths = set()
    for item in memberships:
        if not isinstance(item, dict):
            return None
        path = item.get("category_path")
        if (
            not isinstance(path, list)
            or not path
            or len(path) > _MAX_LIST
            or not all(isinstance(part, str) and part and len(part) <= _MAX_TEXT for part in path)
        ):
            return None
        paths.add(tuple(path))
    return [list(path) for path in sorted(paths)]


def _category_paths_list(value: Any) -> list[list[str]] | None:
    if not isinstance(value, list) or len(value) > _MAX_LIST:
        return None
    paths = set()
    for path in value:
        if (
            not isinstance(path, list)
            or not path
            or len(path) > _MAX_LIST
            or not all(isinstance(part, str) and part and len(part) <= _MAX_TEXT for part in path)
        ):
            return None
        paths.add(tuple(path))
    return [list(item) for item in sorted(paths)]


def _string_list(value: Any, *, allow_empty: bool, limit: int = _MAX_LIST) -> list[str] | None:
    if not isinstance(value, list) or len(value) > limit:
        return None
    out = []
    for item in value:
        if not isinstance(item, str) or len(item) > _MAX_TEXT or (not allow_empty and not item):
            return None
        out.append(item)
    return out


def _project_portal_catalog(catalog: list, root: Path) -> tuple[list[dict], dict]:
    """Project catalog rows; returns (records, identity_map).

    Records keep the raw recorded fields needed by the internal six-field
    join and pin checks. None of them is a public field; the published
    shape is built separately by :func:`_public_source`.
    """
    records = []
    identities = {}
    for i, row in enumerate(catalog):
        if not isinstance(row, dict):
            raise _err("portal catalog rows must be objects")
        if len(row) > _MAX_LIST:
            raise _err("portal catalog row is too large")
        region = row.get("region")
        doc_id = row.get("doc_id")
        key = _portal_identity(region, doc_id)
        if key is None:
            # A portal source whose identity is not a reviewed portal
            # identity cannot be published without leaking how it was
            # addressed; the raw value is never echoed in the error.
            raise _err("portal catalog identity is not a reviewed portal identity")
        if key in identities:
            raise _err("duplicate catalog identity")
        status = row.get("status")
        if not isinstance(status, str) or not status:
            raise _err("portal catalog status must be a non-empty string")
        source_url = row.get("source_url")
        if not isinstance(source_url, str) or not source_url or len(source_url) > _MAX_TEXT:
            raise _err("portal catalog source_url must be a non-empty string")
        sha = row.get("content_sha256")
        if sha is not None and not _is_sha256(sha):
            raise _err("portal catalog content_sha256 must be 64 lowercase hex or null")
        category_paths = _category_paths_from_memberships(row.get("memberships"))
        if category_paths is None:
            raise _err("portal catalog memberships are malformed")
        raw_file = row.get("raw_file")
        raw_file_sha = row.get("raw_file_sha256")
        if (raw_file is None) != (raw_file_sha is None):
            raise _err("raw_file and raw_file_sha256 must appear together")
        if raw_file_sha is not None and not _is_sha256(raw_file_sha):
            raise _err("raw_file_sha256 must be 64 lowercase hex")
        body_declared = status == "archived" and sha is not None and raw_file is not None
        body_available = False
        if body_declared:
            resolved = _resolve_inside(root, raw_file)
            if resolved is not None:
                raw = _read(resolved)
                if raw is None or _sha256(raw) != raw_file_sha:
                    raise _err("portal raw_file hash mismatch")
                body_available = True
            else:
                # Archived raw_file must stay inside the declared root: an
                # absolute path or an escape is a hard error; a missing file
                # inside the root keeps the row as a blocked source.
                candidate = None
                try:
                    rel = Path(raw_file)
                    if not rel.is_absolute():
                        candidate = (root / rel).resolve()
                except (OSError, RuntimeError, ValueError):
                    candidate = None
                if candidate is None or not candidate.is_relative_to(root):
                    raise _err("portal raw_file escapes the snapshot root")
        record = {
            "key": key,
            "region": region,
            "doc_id": doc_id,
            "record_index": i,
            "source_url": source_url,
            "status": status,
            "content_sha256": sha,
            "category_paths": category_paths,
            "body_available": body_available,
        }
        records.append(record)
        identities[key] = record
    return records, identities


def _reasons(value: Any) -> list[str] | None:
    reasons = _string_list(value, allow_empty=False, limit=_MAX_REASONS)
    if reasons is None:
        return None
    return sorted(set(reasons))


def _project_observation(item: Any, kind: str, doc_key: tuple[str, str]) -> dict | None:
    """Internal whitelist projection of one declaration/example.

    The projected dict keeps raw parser fields (method/path/host/reason
    strings) that the six-field join and counts need. None of these raw
    values is a public field: :func:`_public_observation` re-maps the row
    through the fixed vocabularies before publication. ``None`` is
    returned when the row is malformed.
    """
    if not isinstance(item, dict):
        return None
    status = item.get("status")
    ordinal = item.get("ordinal")
    if not isinstance(status, str) or not status or not _is_index(ordinal):
        return None
    section_index = item.get("section_index")
    if section_index is not None and not _is_index(section_index):
        return None
    evidence = item.get("evidence")
    element_indices = set()
    if evidence is not None:
        if not isinstance(evidence, list) or len(evidence) > _MAX_EVIDENCE_ITEMS:
            return None
        for entry in evidence:
            if not isinstance(entry, dict):
                return None
            index = entry.get("element_index")
            if index is not None and not _is_index(index):
                return None
            if index is not None:
                element_indices.add(index)
            # role/text (and any parser-added extra keys) are deliberately not
            # projected: raw evidence text may carry example payloads.
    method = item.get("method")
    path = item.get("path")
    host = item.get("host")
    if method is not None and _text(method) is None:
        return None
    if path is not None and _text(path) is None:
        return None
    if host is not None and _text(host) is None:
        return None
    for field in ("query", "fragment"):
        value = item.get(field)
        if value is not None and not isinstance(value, str):
            return None
    reasons = item.get("reasons")
    if reasons is not None and _reasons(reasons) is None:
        return None
    same_route = item.get("same_route_operation_ids")
    if same_route is not None and _string_list(same_route, allow_empty=False) is None:
        return None
    # The real parser relation lives on examples as
    # ``related_declaration_ordinals``; a fabricated reverse field
    # ``related_example_ordinals`` on declarations is simply unknown input —
    # like any other unlisted key it is safely ignored below, never projected.
    related_decl = item.get("related_declaration_ordinals")
    if related_decl is not None and (
        not isinstance(related_decl, list)
        or len(related_decl) > _MAX_LIST
        or not all(_is_index(value) for value in related_decl)
    ):
        return None
    agrees = item.get("agrees_with_declaration")
    if kind == "example" and agrees is not None and type(agrees) is not bool:
        return None
    # Whitelist projection, not schema lockdown: keys outside the projected
    # set are ignored so a legitimate future parser field never drops an
    # otherwise valid observation. They can neither reach the report nor
    # influence the exact join, which only reads whitelisted fields.
    return {
        "doc_key": doc_key,
        "kind": kind,
        "ordinal": ordinal,
        "status": status,
        "method": method,
        "path": path,
        "host": host,
        "has_query": bool(item.get("query")),
        "has_fragment": bool(item.get("fragment")),
        "section_index": section_index,
        "element_indices": sorted(element_indices),
        "reasons": sorted(set(reasons)) if isinstance(reasons, list) else [],
        "same_route_hints": sorted(set(same_route)) if isinstance(same_route, list) else [],
        "related_declaration_ordinals": sorted(set(related_decl))
        if kind == "example" and isinstance(related_decl, list)
        else [],
        "agrees_with_declaration": agrees if kind == "example" and type(agrees) is bool else None,
    }


def _warnings(value: Any) -> list[dict] | None:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > _MAX_WARNINGS:
        return None
    out = []
    for item in value:
        if not isinstance(item, dict):
            return None
        code = item.get("code")
        index = item.get("element_index")
        if not isinstance(code, str) or not code or len(code) > _MAX_ID:
            return None
        if index is not None and not _is_index(index):
            return None
        # Extra keys are ignored, never projected (same whitelist policy as
        # observations): a new warning field must not drop the document.
        out.append({"code": code, "element_index": index})
    return out


def _project_inventory(inventory: dict) -> dict:
    """Project inventory documents keyed by (region, doc_id); internal use."""
    documents = {}
    for doc_index, raw_doc in enumerate(inventory["documents"]):
        if not isinstance(raw_doc, dict):
            raise _err("inventory documents must be objects")
        key = _portal_identity(raw_doc.get("region"), raw_doc.get("doc_id"))
        if key is None:
            raise _err("inventory document identity is not a reviewed portal identity")
        if key in documents:
            raise _err("duplicate inventory document identity")
        source_url = raw_doc.get("source_url")
        if not isinstance(source_url, str) or not source_url or len(source_url) > _MAX_TEXT:
            raise _err("inventory document source_url must be a non-empty string")
        sha = raw_doc.get("content_sha256")
        if sha is not None and not _is_sha256(sha):
            raise _err("inventory document content_sha256 must be 64 lowercase hex or null")
        status = raw_doc.get("status")
        if not isinstance(status, str) or not status:
            raise _err("inventory document status must be a non-empty string")
        category_paths = raw_doc.get("category_paths")
        if category_paths is not None and _category_paths_list(category_paths) is None:
            raise _err("inventory document category_paths are malformed")
        declarations = raw_doc.get("declarations")
        examples = raw_doc.get("examples")
        warnings = raw_doc.get("warnings")
        if declarations is not None and not isinstance(declarations, list):
            raise _err("inventory declarations must be a list")
        if examples is not None and not isinstance(examples, list):
            raise _err("inventory examples must be a list")
        warning_rows = _warnings(warnings)
        if warning_rows is None:
            raise _err("inventory warnings are malformed")
        ordinals = set()
        obs = []
        for kind, rows in (("declaration", declarations), ("example", examples)):
            if rows is not None and len(rows) > _MAX_LIST:
                raise _err("inventory observation list is too large")
            for item in rows or []:
                projected = _project_observation(item, kind, key)
                if projected is None:
                    # Hard failure: silently skipping an observation would let
                    # the projection look complete while rows were dropped.
                    raise _err("inventory observation is malformed")
                tag = (projected["kind"], projected["ordinal"])
                if tag in ordinals:
                    raise _err("duplicate inventory observation identity")
                ordinals.add(tag)
                # Pin the observation to the inventory identity it was
                # actually parsed from; the exact join later requires this
                # pin to match the catalog-declared identity.
                projected["inventory_url"] = source_url
                projected["inventory_content_sha256"] = sha
                obs.append(projected)
        documents[key] = {
            "key": key,
            "record_index": doc_index,
            "source_url": source_url,
            "content_sha256": sha,
            "status": status,
            "category_paths": _category_paths_list(category_paths) or [],
            "observations": obs,
            "warnings": warning_rows,
        }
    return documents


def _project_reference_entries(entries: list, root: Path) -> list[dict]:
    records = []
    seen_urls = set()
    for i, row in enumerate(entries):
        if not isinstance(row, dict):
            raise _err("sdk_reference entries must be objects")
        url = row.get("url")
        if not isinstance(url, str) or not url or len(url) > _MAX_TEXT:
            raise _err("sdk_reference entry url must be a non-empty string")
        status = row.get("status")
        if not isinstance(status, str) or not status or len(status) > _MAX_ID:
            raise _err("sdk_reference entry status must be a non-empty string")
        sha = row.get("sha256")
        if sha is not None and not _is_sha256(sha):
            raise _err("sdk_reference entry sha256 must be 64 lowercase hex or null")
        local_path = row.get("local_path")
        if local_path is not None and not isinstance(local_path, str):
            raise _err("sdk_reference entry local_path must be a string or null")
        discovered = _string_list(
            row.get("discovered_from"), allow_empty=False, limit=_MAX_REFERENCES
        )
        if discovered is None:
            raise _err("sdk_reference entry discovered_from must be a string list")
        body = False
        if status == "ok" and local_path is not None and sha is not None:
            resolved = _resolve_inside(root, local_path)
            if resolved is None:
                # Same policy as portal raw_file: absolute paths and escapes
                # are a hard error; a missing file inside the root keeps the
                # entry as a blocked source.
                candidate = None
                try:
                    rel = Path(local_path)
                    if not rel.is_absolute():
                        candidate = (root / rel).resolve()
                except (OSError, RuntimeError, ValueError):
                    candidate = None
                if candidate is None or not candidate.is_relative_to(root):
                    raise _err("sdk_reference local_path escapes the snapshot root")
            else:
                raw = _read(resolved)
                if raw is None or _sha256(raw) != sha:
                    raise _err("sdk_reference local file hash mismatch")
                body = True
        if url in seen_urls:
            raise _err("duplicate sdk_reference entry url")
        seen_urls.add(url)
        records.append(
            {
                # record_index is the zero-based manifest entries position,
                # fixed before any projection/dedup — the public identity is
                # this ordinal, never a token derived from the raw URL.
                "record_index": i,
                "status": status,
                "sha256": sha,
                "discovery_reference_count": len(discovered),
                "body_available": body,
            }
        )
    return records


def _project_asset_entries(entries: list, root: Path) -> list[dict]:
    records = []
    seen_ids = set()
    for record_index, row in enumerate(entries):
        asset_id = _identifier(row.get("asset_id"))
        url = row.get("url")
        if url is not None and (not isinstance(url, str) or not url or len(url) > _MAX_TEXT):
            raise _err("assets entry url must be a non-empty string or null")
        if asset_id is None:
            if isinstance(url, str) and url:
                asset_id = "url:" + url
            else:
                # An unidentifiable asset or a non-HTTP locator with no
                # declared id cannot mint a stable source identity.
                raise _err("assets entries require asset_id or url strings")
        if asset_id in seen_ids:
            raise _err("duplicate assets entry identity")
        seen_ids.add(asset_id)
        status = row.get("status")
        if not isinstance(status, str) or not status or len(status) > _MAX_ID:
            raise _err("assets entry status must be a non-empty string")
        sha = row.get("sha256")
        if sha is not None and not _is_sha256(sha):
            raise _err("assets entry sha256 must be 64 lowercase hex or null")
        file_value = row.get("file")
        if file_value is not None and not isinstance(file_value, str):
            raise _err("assets entry file must be a string or null")
        references = row.get("references")
        if references is not None:
            if not isinstance(references, list) or len(references) > _MAX_REFERENCES:
                raise _err("assets entry references are malformed")
            ref_rows = []
            for ref in references:
                if not isinstance(ref, dict):
                    raise _err("assets entry references must be objects")
                ref_url = ref.get("url")
                if ref_url is not None and (
                    not isinstance(ref_url, str) or not ref_url or len(ref_url) > _MAX_TEXT
                ):
                    # Recorded reference locators stay bounded as raw input
                    # even though they are never projected publicly.
                    raise _err("assets entry reference url must be a non-empty string or null")
                ref_type = ref.get("reference_type")
                ref_rows.append(
                    {
                        "reference_type": ref_type
                        if isinstance(ref_type, str) and ref_type in _REFERENCE_TYPES
                        else "unknown"
                    }
                )
        else:
            ref_rows = []
        body = False
        if file_value is not None and sha is not None:
            resolved = _resolve_inside(root, file_value)
            if resolved is None:
                # Declared asset files must stay inside the declared root: an
                # absolute path or an escape is a hard error; a missing file
                # inside the root keeps the row as a blocked source.
                candidate = None
                try:
                    rel = Path(file_value)
                    if not rel.is_absolute():
                        candidate = (root / rel).resolve()
                except (OSError, RuntimeError, ValueError):
                    candidate = None
                if candidate is None or not candidate.is_relative_to(root):
                    raise _err("assets file escapes the snapshot root")
            else:
                raw = _read(resolved)
                if raw is None:
                    raise _err("assets file is unreadable")
                if _sha256(raw) != sha:
                    # A declared file that exists but hashes differently is
                    # tampered input, not a missing body: fail like the other
                    # snapshot kinds instead of degrading to blocked_source.
                    raise _err("assets file sha256 mismatch")
                body = True
        records.append(
            {
                # record_index is the zero-based non-empty JSONL record
                # ordinal, fixed before projection/dedup — the public
                # identity is this ordinal, never the recorded asset_id/url.
                "record_index": record_index,
                "status": status,
                "sha256": sha,
                "category": row.get("category")
                if isinstance(row.get("category"), str) and row["category"] in _ASSET_CATEGORIES
                else "unknown",
                "reference_types": ref_rows,
                "body_available": body,
            }
        )
    return records


def _load_registry(repo_root: Path) -> tuple[dict, dict, dict, dict]:
    """Run the audited registry/code audit and project contract/binding rows."""
    try:
        root = repo_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise _err("repository root cannot be resolved") from None
    if not root.is_dir():
        raise _err("repository root is not a directory")
    try:
        report = audit_repository(root)
    except (AuditInputError, RegistryError, CodeAuditError):
        raise _err("repository audit failed") from None
    audit_hash = report.get("inputs_sha256", {}).get(_CONTRACTS_INPUT)
    contracts_path = _resolve_inside(root, _CONTRACTS_INPUT)
    if contracts_path is None:
        raise _err("registry contracts input is missing")
    contracts_raw = _read(contracts_path)
    if contracts_raw is None or not _is_sha256(audit_hash) or _sha256(contracts_raw) != audit_hash:
        raise _err("registry contracts input drifted from the audit")
    contracts_data = _strict_json(contracts_raw, "registry contracts must be strict JSON")
    if not isinstance(contracts_data, dict) or not isinstance(
        contracts_data.get("contracts"), list
    ):
        raise _err("registry contracts must be an object with a contracts list")
    bindings_path = _resolve_inside(root, _BINDINGS_INPUT)
    bindings_raw = _read(bindings_path) if bindings_path is not None else None
    bindings_hash = report.get("inputs_sha256", {}).get(_BINDINGS_INPUT)
    if bindings_raw is None or _sha256(bindings_raw) != bindings_hash:
        raise _err("registry bindings input drifted from the audit")
    bindings_data = _strict_json(bindings_raw, "registry bindings must be strict JSON")
    if not isinstance(bindings_data, dict) or not isinstance(bindings_data.get("bindings"), list):
        raise _err("registry bindings must be an object with a bindings list")

    contracts = []
    for row in contracts_data["contracts"]:
        if not isinstance(row, dict):
            raise _err("registry contract rows must be objects")
        operation_id = _identifier(row.get("operation_id"))
        region = _identifier(row.get("region"))
        doc_id = _identifier(row.get("source_doc_id"))
        url = _text(row.get("source_url"))
        sha = row.get("source_content_sha256")
        method = _identifier(row.get("method"))
        path = _text(row.get("path"))
        if (
            operation_id is None
            or region is None
            or doc_id is None
            or url is None
            or not _is_sha256(sha)
            or method is None
            or path is None
        ):
            raise _err("registry contract rows are missing required identity fields")
        contracts.append(
            {
                "operation_id": operation_id,
                "region": region,
                "doc_id": doc_id,
                "source_url": url,
                "content_sha256": sha,
                "method": method,
                "path": path,
            }
        )
    bindings = {}
    for row in bindings_data["bindings"]:
        if not isinstance(row, dict):
            raise _err("registry binding rows must be objects")
        operation_id = _identifier(row.get("operation_id"))
        module = _text(row.get("module"))
        symbols = _string_list(row.get("symbols"), allow_empty=False)
        offline = _identifier(row.get("offline_status"))
        live = _identifier(row.get("live_status"))
        if (
            operation_id is None
            or module is None
            or symbols is None
            or offline is None
            or live is None
        ):
            raise _err("registry binding rows are missing required fields")
        public_entry = row.get("public_entry")
        bindings[operation_id] = {
            "module": module,
            "symbols": symbols,
            "offline_status": offline,
            "live_status": live,
            "public_entry": public_entry
            if isinstance(public_entry, str) and public_entry and len(public_entry) <= _MAX_ID
            else None,
            "has_live_evidence": isinstance(row.get("live_evidence"), str)
            and bool(row["live_evidence"]),
        }
    return report, contracts, bindings, bindings_data


def _load_decisions(decisions_path: Path) -> tuple[dict, bytes]:
    raw = _read(decisions_path)
    if raw is None:
        raise _err("decisions file is unreadable")
    data = _strict_json(raw, "decisions must be strict JSON")
    if not isinstance(data, dict) or data.get("schema_version") != INPUT_DECISIONS_SCHEMA_VERSION:
        raise _err("decisions must be a schema_version 1 object")
    rows = data.get("decisions")
    if not isinstance(rows, list) or len(rows) > _MAX_DECISIONS:
        raise _err("decisions must be a list")
    return data, raw


def _check_decisions(data: dict, sources: dict) -> dict:
    """Validate reviewer decisions against known sources; returns overrides.

    Decision rows reference the public source_id vocabulary. There is no
    legacy locator-based alias: an id written for the retired schema-1
    projection simply does not resolve and fails as an unknown source_id.
    """
    seen = set()
    overrides = {}
    for record_index, row in enumerate(data["decisions"]):
        if not isinstance(row, dict):
            raise _err("decision rows must be objects")
        source_id = _identifier(row.get("source_id"))
        if source_id is None or source_id in seen:
            raise _err("decision source_id must be a unique non-empty string")
        seen.add(source_id)
        source = sources.get(source_id)
        if source is None:
            raise _err("decision references an unknown source_id")
        pin = row.get("content_sha256")
        if pin is not None and not _is_sha256(pin):
            raise _err("decision content_sha256 must be 64 lowercase hex or null")
        if pin != source["content_sha256"]:
            raise _err("decision content pin does not match the source")
        disposition = row.get("disposition")
        if disposition not in _DISPOSITIONS:
            raise _err("decision disposition is not in the accepted set")
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > _MAX_REASON:
            raise _err("decision reason must be a non-empty string")
        evidence_refs = _string_list(
            row.get("evidence_refs"), allow_empty=False, limit=_MAX_EVIDENCE_REFS
        )
        if not evidence_refs:
            raise _err("decision evidence_refs must be a non-empty string list")
        capability_id = row.get("capability_id")
        if capability_id is not None and not (
            isinstance(capability_id, str) and 0 < len(capability_id) <= _MAX_CAPABILITY_ID
        ):
            raise _err("decision capability_id must be a non-empty string or null")
        if set(row) - {
            "source_id",
            "content_sha256",
            "disposition",
            "reason",
            "evidence_refs",
            "capability_id",
        }:
            raise _err("decision rows contain unsupported keys")
        overrides[source_id] = {
            "disposition": disposition,
            "capability_id": capability_id,
            "decision_reference": {
                "decisions_sha256": None,  # filled by the caller with the file hash
                "record_index": record_index,
                "reason_present": True,
                "evidence_ref_count": len(set(evidence_refs)),
            },
        }
    return overrides


# ---------------------------------------------------------------------------
# Public whitelist projection (schema 2)
#
# Every report entity is built field-by-field below. Internal dicts are
# never copied and popped: a new internal field can never appear in the
# published report unless a line here explicitly publishes it.
# ---------------------------------------------------------------------------


def _public_status(status: Any, known: frozenset) -> str:
    return status if isinstance(status, str) and status in known else "unknown"


def _public_reference(ref: Any, fallback_index: int) -> dict | None:
    """Explicit per-field projection of one recorded asset reference."""
    if not isinstance(ref, dict):
        return None
    index = ref.get("reference_index")
    evidence_ref = ref.get("evidence_ref")
    return {
        "reference_index": index if _is_index(index) else fallback_index,
        "reference_type": ref.get("reference_type")
        if ref.get("reference_type") in _REFERENCE_TYPES
        else "unknown",
        "evidence_ref": evidence_ref if isinstance(evidence_ref, str) else "",
    }


def _public_source(source: dict, known_statuses: frozenset) -> dict:
    out = {
        "source_id": source["source_id"],
        "snapshot_id": source["snapshot_id"],
        "record_index": source["record_index"],
        "kind": source["kind"],
        "region": source["region"],
        "doc_id": source["doc_id"],
        "canonical_document_url": source["canonical_document_url"],
        "status": _public_status(source["status"], known_statuses),
        "content_sha256": source["content_sha256"],
        # category_paths is only a catalog-verified membership: an
        # inventory_only row keeps its recorded paths internally but they
        # were never confirmed by the catalog, so the public slot is empty.
        "category_paths": source["category_paths"]
        if source.get("_category_paths_verified")
        else [],
        "disposition": source["disposition"]
        if source["disposition"] in _DISPOSITIONS
        else "needs_review",
        "operation_ids": source["operation_ids"],
        "observation_ordinals": source["observation_ordinals"],
        "evidence_refs": source["evidence_refs"],
        "unresolved": source["unresolved_public"],
    }
    # Per-kind extras are picked field-by-field: an internal _extra dict is
    # never copied wholesale, so a new internal field cannot leak out.
    extra = source.get("_extra")
    if not isinstance(extra, dict):
        extra = {}
    if source["kind"] == "sdk_reference":
        count = extra.get("discovery_reference_count")
        out["discovery_reference_count"] = count if _is_index(count) else 0
    elif source["kind"] == "asset":
        category = extra.get("asset_category")
        out["asset_category"] = category if category in _ASSET_CATEGORIES else "unknown"
        raw_refs = extra.get("references")
        references = []
        if isinstance(raw_refs, list):
            for ref in raw_refs:
                projected = _public_reference(ref, len(references))
                if projected is not None:
                    references.append(projected)
        count = extra.get("reference_count")
        out["reference_count"] = count if _is_index(count) else len(references)
        out["references"] = references
    if source.get("decision_reference") is not None:
        out["decision_reference"] = source["decision_reference"]
        capability_id = source.get("decision_capability_id")
        if capability_id is not None:
            if capability_id in source.get("public_capability_ids", ()):
                out["decision_capability_id"] = capability_id
            else:
                # The decision referenced a capability outside the public
                # set: publish only that a reference exists, not its value.
                out["has_external_capability_ref"] = True
    return out


def _public_observation(obs: dict, registry_operation_ids: frozenset) -> dict:
    reasons = obs["reasons"]
    known = [code for code in reasons if code in _REASON_CODES]
    if len(known) != len(reasons):
        known.append("unknown_reason")
    out = {
        "observation_id": obs["observation_id"],
        "source_id": obs["source_id"],
        "kind": obs["kind"],
        "ordinal": obs["ordinal"],
        "status": _public_status(obs["status"], _OBSERVATION_STATUSES),
        "method": obs["method"] if obs["method"] in _HTTP_METHODS else None,
        "section_index": obs["section_index"],
        "element_indices": obs["element_indices"],
        "has_query": obs["has_query"],
        "has_fragment": obs["has_fragment"],
        "reason_codes": sorted(set(known)),
        "reason_count": len(reasons),
        # Route hints count only ids that exist in the reviewed registry;
        # an unregistered hint is not publishable identity information.
        "same_route_hints": sorted(
            hint for hint in obs["same_route_hints"] if hint in registry_operation_ids
        ),
        "related_declaration_ordinals": obs["related_declaration_ordinals"],
        "agrees_with_declaration": obs["agrees_with_declaration"],
        "exact_operation_ids": sorted(
            op for op in obs["exact_operation_ids"] if op in registry_operation_ids
        ),
        "inventory_content_sha256": obs["inventory_content_sha256"],
    }
    return out


def _unresolved_reason(reason: Any) -> str:
    """Map an internal unresolved marker to its controlled public reason."""
    if isinstance(reason, str):
        if reason in _UNRESOLVED_REASONS:
            return reason
        if reason.startswith("inventory_warning"):
            return "inventory_warning"
    return "unclassified_review_issue"


# Fixed per-kind public capability field whitelists. reviewed_contract is
# the only kind that may republish the reviewed-registry constant path and
# the constructed canonical document locator; any other kind (including an
# unrecognized future kind) falls back to the locator-free set.
_CAPABILITY_KIND_FIELDS = {
    "reviewed_contract": (
        "capability_id",
        "capability_kind",
        "operation_id",
        "region",
        "source_doc_id",
        "canonical_document_url",
        "source_content_sha256",
        "method",
        "path",
        "module",
        "symbols",
        "public_entry",
        "implementation_status",
        "offline_status",
        "offline_evidence",
        "live_status",
        "live_evidence",
        "source_review_status",
        "source_review_issues",
        "linked_source_ids",
        "linked_observation_ids",
        "related_example_ids",
        "disposition",
        "provisional",
    ),
    "provisional_observation": (
        "capability_id",
        "capability_kind",
        "operation_id",
        "region",
        "source_doc_id",
        "source_content_sha256",
        "method",
        "module",
        "symbols",
        "public_entry",
        "implementation_status",
        "offline_status",
        "offline_evidence",
        "live_status",
        "live_evidence",
        "source_review_status",
        "source_review_issues",
        "linked_source_ids",
        "linked_observation_ids",
        "linked_op_ids",
        "disposition",
        "provisional",
    ),
}
_CAPABILITY_KIND_FIELDS["unobserved_source"] = _CAPABILITY_KIND_FIELDS["provisional_observation"]


def _public_capability(capability: dict) -> dict:
    """Rebuild one capability through its per-kind field whitelist."""
    fields = _CAPABILITY_KIND_FIELDS.get(
        capability.get("capability_kind"), _CAPABILITY_KIND_FIELDS["provisional_observation"]
    )
    return {key: capability[key] for key in fields if key in capability}


# The nested audit object is republished only as the already-approved
# counts/static identifier/input hashes below — never as a pass-through of
# a future audit object shape.
_REGISTRY_SUMMARY_KEYS = ("contracts", "bindings", "source_documents", "source_issues")
_CODE_SUMMARY_KEYS = ("bindings_checked", "symbols_checked")
_AUDIT_EVIDENCE = "registry_and_static_ast_only"


def _audit_count(summary: Any, key: str) -> int | None:
    value = summary.get(key) if isinstance(summary, dict) else None
    return value if _is_index(value) else None


def _public_registry_audit(audit_report: dict) -> dict:
    registry = audit_report.get("registry")
    code = audit_report.get("code")
    registry_summary = registry.get("summary") if isinstance(registry, dict) else None
    code_summary = code.get("summary") if isinstance(code, dict) else None
    inputs = audit_report.get("inputs_sha256")
    if not isinstance(inputs, dict):
        inputs = {}
    return {
        "evidence": _AUDIT_EVIDENCE,
        "registry_summary": {
            key: _audit_count(registry_summary, key) for key in _REGISTRY_SUMMARY_KEYS
        },
        "code_summary": {key: _audit_count(code_summary, key) for key in _CODE_SUMMARY_KEYS},
        "inputs_sha256": {
            name: inputs.get(name) if _is_sha256(inputs.get(name)) else None
            for name in _REGISTRY_INPUTS
        },
    }


def build_matrix(repo_root: Path, manifest_path: Path, decisions_path: Path | None = None) -> dict:
    """Build the official source/capability matrix; raises ProductMatrixError.

    Reads only the explicit snapshot roots, the optional inventory file, the
    audited registry inputs, and SDK ``.py`` files via the reused AST audit.
    Deterministic for identical input bytes; never writes or mutates inputs.
    The returned report is the schema-2 public projection; there is no raw
    output mode.
    """
    manifest_file = _resolve_file(Path(manifest_path))
    if manifest_file is None:
        raise _err("manifest path must be a resolvable file")
    manifest = _load_manifest(manifest_file)
    snapshots = _check_snapshots(manifest)

    audit_report, contracts, bindings, _bindings_data = _load_registry(repo_root)

    input_hashes: dict[str, str] = {"manifest": _sha256(_read(manifest_file) or b"")}
    source_sets = []
    sources: dict[str, dict] = {}
    observations = []
    unresolved = []

    for snap in snapshots:
        set_key = snap["id"]
        input_hashes[f"snapshot:{set_key}"] = snap["manifest_sha256"]
        if snap["inventory_sha256"] is not None:
            input_hashes[f"snapshot:{set_key}:inventory"] = snap["inventory_sha256"]
        source_set = {
            "snapshot_id": set_key,
            "kind": snap["kind"],
            "region": snap["region"],
            "manifest_sha256": snap["manifest_sha256"],
            "inventory_sha256": snap["inventory_sha256"],
            "source_count": 0,
            "observation_count": 0,
        }
        source_sets.append(source_set)

        if snap["kind"] == "portal":
            catalog, inventory = _load_portal_inputs(snap)
            records, _identities = _project_portal_catalog(catalog, snap["root"])
            inv_docs = _project_inventory(inventory) if inventory is not None else {}
            for record in records:
                region, doc_id = record["key"]
                source_id = f"{set_key}:{region}:{doc_id}"
                if source_id in sources:
                    raise _err("snapshot source identities collide")
                body = record["body_available"]
                inv = inv_docs.get(record["key"])
                source = {
                    "source_id": source_id,
                    "snapshot_id": set_key,
                    "record_index": record["record_index"],
                    "kind": "portal_document",
                    "region": region,
                    "doc_id": doc_id,
                    "canonical_document_url": _canonical_document_url(region, doc_id),
                    "status": record["status"],
                    "content_sha256": record["content_sha256"],
                    "category_paths": record["category_paths"],
                    "disposition": "needs_review" if body else "blocked_source",
                    "operation_ids": [],
                    "observation_ordinals": [],
                    "evidence_refs": [f"{set_key}/catalog.json#record/{record['record_index']}"],
                    "unresolved": [],
                    "_warning_refs": [],
                    "_blocked": not body,
                    # Membership paths came from the catalog itself: the one
                    # verified category assignment the report may publish.
                    "_category_paths_verified": True,
                }
                if not body:
                    source["unresolved"].append("source_body_unavailable")
                elif record["status"] != "archived":
                    source["unresolved"].append("snapshot_body_unverified")
                # The exact join needs the catalog-pinned identity; the raw
                # (unpublished) pinned URL stays inside the internal key.
                join_identity = (
                    region,
                    doc_id,
                    record["source_url"],
                    record["content_sha256"],
                )
                if inv is not None:
                    inventory_matches_catalog = (
                        inv["source_url"] == record["source_url"]
                        and inv["content_sha256"] == record["content_sha256"]
                    )
                    if not inventory_matches_catalog:
                        source["unresolved"].append("inventory_identity_mismatch")
                    for obs in inv["observations"]:
                        obs_id = f"{source_id}:{obs['kind']}:{obs['ordinal']}"
                        obs_out = {
                            "observation_id": obs_id,
                            "source_id": source_id,
                            "kind": obs["kind"],
                            "ordinal": obs["ordinal"],
                            "status": obs["status"],
                            "method": obs["method"],
                            "path": obs["path"],
                            "has_query": obs["has_query"],
                            "has_fragment": obs["has_fragment"],
                            "section_index": obs["section_index"],
                            "element_indices": obs["element_indices"],
                            "reasons": obs["reasons"],
                            "same_route_hints": obs["same_route_hints"],
                            "related_declaration_ordinals": obs["related_declaration_ordinals"],
                            "agrees_with_declaration": obs["agrees_with_declaration"],
                            "inventory_url": obs["inventory_url"],
                            "inventory_content_sha256": obs["inventory_content_sha256"],
                            "exact_operation_ids": [],
                        }
                        # Eligibility for the six-field join requires the
                        # inventory pin to equal the catalog pin, a usable
                        # archived body, and the observation's own inventory
                        # identity to match what was pinned for this source.
                        obs_out["_join_key"] = (
                            join_identity + (obs["method"], obs["path"])
                            if inventory_matches_catalog
                            and body
                            and obs["inventory_url"] == record["source_url"]
                            and obs["inventory_content_sha256"] == record["content_sha256"]
                            else None
                        )
                        observations.append(obs_out)
                        source["observation_ordinals"].append(
                            {"kind": obs["kind"], "ordinal": obs["ordinal"]}
                        )
                    for warning in inv["warnings"]:
                        source["unresolved"].append("inventory_warning")
                        source["_warning_refs"].append(warning["element_index"])
                source["observation_ordinals"].sort(
                    key=lambda item: (item["kind"], item["ordinal"])
                )
                source["_join_identity"] = join_identity
                sources[source_id] = source
                source_set["source_count"] += 1
                source_set["observation_count"] += len(source["observation_ordinals"])
            # Inventory documents without a catalog row stay visible as
            # blocked review items instead of disappearing.
            for key, inv in inv_docs.items():
                if key in _identities:
                    continue
                region, doc_id = key
                source_id = f"{set_key}:{region}:{doc_id}"
                if source_id in sources:
                    raise _err("snapshot source identities collide")
                source = {
                    "source_id": source_id,
                    "snapshot_id": set_key,
                    "record_index": inv["record_index"],
                    "kind": "portal_document",
                    "region": region,
                    "doc_id": doc_id,
                    "canonical_document_url": _canonical_document_url(region, doc_id),
                    "status": "inventory_only",
                    "content_sha256": inv["content_sha256"],
                    "category_paths": inv["category_paths"],
                    "disposition": "blocked_source",
                    "operation_ids": [],
                    "observation_ordinals": [],
                    "evidence_refs": [f"{set_key}/inventory#record/{inv['record_index']}"],
                    "unresolved": ["inventory_document_not_in_catalog"],
                    "_warning_refs": [],
                    "_blocked": True,
                    # Inventory-only paths are not catalog-verified; the
                    # recorded values stay internal and the public slot is
                    # left empty by _public_source.
                    "_category_paths_verified": False,
                }
                for obs in inv["observations"]:
                    obs_id = f"{source_id}:{obs['kind']}:{obs['ordinal']}"
                    obs_out = {
                        "observation_id": obs_id,
                        "source_id": source_id,
                        "kind": obs["kind"],
                        "ordinal": obs["ordinal"],
                        "status": obs["status"],
                        "method": obs["method"],
                        "path": obs["path"],
                        "has_query": obs["has_query"],
                        "has_fragment": obs["has_fragment"],
                        "section_index": obs["section_index"],
                        "element_indices": obs["element_indices"],
                        "reasons": obs["reasons"],
                        "same_route_hints": obs["same_route_hints"],
                        "related_declaration_ordinals": obs["related_declaration_ordinals"],
                        "agrees_with_declaration": obs["agrees_with_declaration"],
                        "inventory_url": obs["inventory_url"],
                        "inventory_content_sha256": obs["inventory_content_sha256"],
                        "exact_operation_ids": [],
                        # inventory_only observations can never earn an exact
                        # match: there is no catalog-pinned identity to graft
                        # onto and none may be fabricated.
                        "_join_key": None,
                    }
                    observations.append(obs_out)
                    source["observation_ordinals"].append(
                        {"kind": obs["kind"], "ordinal": obs["ordinal"]}
                    )
                source["observation_ordinals"].sort(
                    key=lambda item: (item["kind"], item["ordinal"])
                )
                # The inventory pin doubles as the review-link identity so
                # contracts can still flag source_identity_mismatch/absent;
                # observations stay ineligible for the exact join.
                source["_join_identity"] = (
                    region,
                    doc_id,
                    inv["source_url"],
                    inv["content_sha256"],
                )
                sources[source_id] = source
                source_set["source_count"] += 1
                source_set["observation_count"] += len(source["observation_ordinals"])
        elif snap["kind"] == "sdk_reference":
            entries = _project_reference_entries(_load_reference_entries(snap), snap["root"])
            for record in entries:
                source_id = f"{set_key}:sdk_reference:{record['record_index']}"
                if source_id in sources:
                    raise _err("snapshot source identities collide")
                source = {
                    "source_id": source_id,
                    "snapshot_id": set_key,
                    "record_index": record["record_index"],
                    "kind": "sdk_reference",
                    "region": snap["region"],
                    "doc_id": None,
                    "canonical_document_url": None,
                    "status": record["status"],
                    "content_sha256": record["sha256"],
                    "category_paths": [],
                    "disposition": "needs_review" if record["body_available"] else "blocked_source",
                    "operation_ids": [],
                    "observation_ordinals": [],
                    "evidence_refs": [f"{set_key}/manifest.json#entries/{record['record_index']}"],
                    "unresolved": [],
                    "_warning_refs": [],
                    "_blocked": not record["body_available"],
                    "_extra": {
                        "discovery_reference_count": record["discovery_reference_count"],
                    },
                }
                if not record["body_available"]:
                    source["unresolved"].append("source_body_unavailable")
                else:
                    source["unresolved"].append("official_reuse_candidate_pending_review")
                sources[source_id] = source
                source_set["source_count"] += 1
        else:
            records = _project_asset_entries(_load_asset_entries(snap), snap["root"])
            for record in records:
                source_id = f"{set_key}:asset:{record['record_index']}"
                if source_id in sources:
                    raise _err("snapshot source identities collide")
                references = []
                for ref_index, ref in enumerate(record["reference_types"]):
                    references.append(
                        {
                            "reference_index": ref_index,
                            "reference_type": ref["reference_type"],
                            "evidence_ref": (
                                f"{set_key}/manifest.jsonl#record/"
                                f"{record['record_index']}/references/{ref_index}"
                            ),
                        }
                    )
                source = {
                    "source_id": source_id,
                    "snapshot_id": set_key,
                    "record_index": record["record_index"],
                    "kind": "asset",
                    "region": snap["region"],
                    "doc_id": None,
                    "canonical_document_url": None,
                    "status": record["status"],
                    "content_sha256": record["sha256"],
                    "category_paths": [],
                    "disposition": "needs_review" if record["body_available"] else "blocked_source",
                    "operation_ids": [],
                    "observation_ordinals": [],
                    "evidence_refs": [f"{set_key}/manifest.jsonl#record/{record['record_index']}"],
                    "unresolved": [],
                    "_warning_refs": [],
                    "_blocked": not record["body_available"],
                    "_extra": {
                        "asset_category": record["category"],
                        "reference_count": len(references),
                        "references": references,
                    },
                }
                if not record["body_available"]:
                    source["unresolved"].append("asset_body_unavailable_or_excluded")
                sources[source_id] = source
                source_set["source_count"] += 1

    # Exact six-field join: observations to reviewed contracts, per source doc.
    strict_index: dict[tuple, list[str]] = {}
    for contract in contracts:
        strict_key = (
            contract["region"],
            contract["doc_id"],
            contract["source_url"],
            contract["content_sha256"],
            contract["method"],
            contract["path"],
        )
        strict_index.setdefault(strict_key, []).append(contract["operation_id"])

    for obs in observations:
        if obs["kind"] != "declaration" or obs["status"] != "declared_pair":
            continue
        if obs["method"] is None or obs["path"] is None:
            continue
        if obs["has_query"] or obs["has_fragment"]:
            continue
        join_key = obs.get("_join_key")
        if join_key is None:
            # The inventory identity pin did not match the catalog identity,
            # the body was not usable, or the observation is inventory_only.
            continue
        source = sources[obs["source_id"]]
        matched = sorted(strict_index.get(join_key, ()))
        if matched:
            obs["exact_operation_ids"] = matched
            existing = set(source["operation_ids"])
            for operation_id in matched:
                if operation_id not in existing:
                    source["operation_ids"].append(operation_id)
                    existing.add(operation_id)
    for obs in observations:
        obs.pop("_join_key", None)
    for source in sources.values():
        source["operation_ids"].sort()

    registry_operation_ids = frozenset(
        contract["operation_id"] for contract in contracts
    ) | frozenset(bindings)

    # Capabilities: one contract capability per reviewed contract.
    # Fixed indexes built once — no per-contract scan over all observations:
    #   exact_obs_by_op       operation_id -> exact-matched declaration obs
    #   decl_by_source_ordinal (source_id, ordinal) -> declaration obs
    #   related_examples_by_op operation_id -> agreeing example obs (hints)
    exact_obs_by_op: dict[str, list[dict]] = {}
    decl_by_source_ordinal: dict[tuple[str, int], dict] = {}
    for obs in observations:
        if obs["exact_operation_ids"]:
            for op in obs["exact_operation_ids"]:
                exact_obs_by_op.setdefault(op, []).append(obs)
        if obs["kind"] == "declaration":
            decl_by_source_ordinal[(obs["source_id"], obs["ordinal"])] = obs
    related_examples_by_op: dict[str, list[dict]] = {}
    for obs in observations:
        if obs["kind"] != "example" or obs["agrees_with_declaration"] is not True:
            continue
        related_ops = set()
        for ordinal in obs["related_declaration_ordinals"]:
            decl = decl_by_source_ordinal.get((obs["source_id"], ordinal))
            if decl is not None:
                related_ops.update(decl["exact_operation_ids"])
        for op in related_ops:
            related_examples_by_op.setdefault(op, []).append(obs)
    matched_operations = set(exact_obs_by_op)
    sources_by_doc: dict[tuple[str, str, str, str], list[str]] = {}
    doc_region_sources: dict[tuple[str, str], list[str]] = {}
    for source in sources.values():
        if source["kind"] != "portal_document":
            continue
        identity = source.get("_join_identity")
        if identity is None:
            continue
        key = (identity[0], identity[1], identity[2], identity[3] or "")
        sources_by_doc.setdefault(key, []).append(source["source_id"])
        doc_region_sources.setdefault((identity[0], identity[1]), []).append(source["source_id"])

    capabilities = []
    for contract in contracts:
        op = contract["operation_id"]
        binding = bindings.get(op)
        capability_id = f"contract:{op}"
        issues = []
        linked_source_ids = []
        doc_key = (contract["region"], contract["doc_id"])
        identity_key = (
            contract["region"],
            contract["doc_id"],
            contract["source_url"],
            contract["content_sha256"],
        )
        candidate_sources = sources_by_doc.get(identity_key, [])
        if not candidate_sources:
            region_doc_sources = doc_region_sources.get(doc_key, [])
            if not region_doc_sources:
                issues.append("contract_source_document_absent")
            else:
                issues.append("contract_source_identity_mismatch")
                linked_source_ids = sorted(region_doc_sources)
        else:
            linked_source_ids = sorted(candidate_sources)
            candidate_set = set(candidate_sources)
            recovered = any(
                obs["source_id"] in candidate_set for obs in exact_obs_by_op.get(op, ())
            )
            if not recovered and op not in matched_operations:
                issues.append("contract_declaration_not_recovered")
        if binding is None:
            issues.append("binding_record_missing")
            module = None
            symbols = []
            public_entry = None
            offline_status = None
            live_status = None
            has_live_evidence = False
        else:
            module = binding["module"]
            symbols = binding["symbols"]
            public_entry = binding["public_entry"]
            offline_status = binding["offline_status"]
            live_status = binding["live_status"]
            has_live_evidence = binding["has_live_evidence"]
        capabilities.append(
            {
                "capability_id": capability_id,
                "capability_kind": "reviewed_contract",
                "operation_id": op,
                "region": contract["region"],
                "source_doc_id": contract["doc_id"],
                # The published locator is rebuilt from the reviewed
                # registry identity (region + numeric doc_id) only; the raw
                # pinned URL stays inside the internal join identity. A
                # non-portal registry identity publishes no locator.
                "canonical_document_url": _canonical_document_url(
                    contract["region"], contract["doc_id"]
                ),
                "source_content_sha256": contract["content_sha256"],
                "method": contract["method"],
                # The reviewed registry path is an approved constant, the
                # only raw locator string the report may republish.
                "path": contract["path"],
                "module": module,
                "symbols": symbols,
                "public_entry": public_entry,
                "implementation_status": "ast_verified" if binding is not None else "unknown",
                "offline_status": offline_status,
                "offline_evidence": "recorded_evidence" if offline_status is not None else None,
                "live_status": live_status,
                "live_evidence": "recorded_evidence" if has_live_evidence else None,
                "source_review_status": "issues" if issues else "aligned",
                "source_review_issues": issues,
                "linked_source_ids": linked_source_ids,
                # Exact-join support only: declaration observations whose own
                # six-field pin recovered this contract. Parser example hints
                # never appear here; they are review leads under
                # related_example_ids below and cannot mark support.
                "linked_observation_ids": sorted(
                    obs["observation_id"] for obs in exact_obs_by_op.get(op, ())
                ),
                # Review hint only: an example that the parser marked as
                # agreeing with a same-source related declaration that itself
                # recovered this contract. Listed separately so it can never
                # be read as declaration support or an exact match.
                "related_example_ids": sorted(
                    obs["observation_id"] for obs in related_examples_by_op.get(op, ())
                ),
                "disposition": "implement",
                "provisional": False,
            }
        )
        for issue in issues:
            unresolved.append(
                {
                    "kind": "reviewed_contract_source",
                    "capability_id": capability_id,
                    "operation_id": op,
                    "reason": issue,
                }
            )

    # Provisional capabilities: every unregistered observation keeps its own
    # review-visible provisional capability; nothing is silently merged.
    matched_obs_ids = {obs["observation_id"] for obs in observations if obs["exact_operation_ids"]}
    provisional_count = 0
    for obs in observations:
        if obs["observation_id"] in matched_obs_ids:
            continue
        source = sources[obs["source_id"]]
        provisional_count += 1
        blocked = source["disposition"] == "blocked_source"
        capability_id = f"provisional:{obs['observation_id']}"
        capabilities.append(
            {
                "capability_id": capability_id,
                "capability_kind": "provisional_observation",
                "operation_id": None,
                "region": source["region"],
                "source_doc_id": source["doc_id"],
                "source_content_sha256": source["content_sha256"],
                # Observation methods are projected through the same fixed
                # vocabulary used on the observation row itself.
                "method": obs["method"] if obs["method"] in _HTTP_METHODS else None,
                "module": None,
                "symbols": [],
                "public_entry": None,
                "implementation_status": "missing",
                "offline_status": None,
                "offline_evidence": None,
                "live_status": None,
                "live_evidence": None,
                "source_review_status": "unreviewed",
                "source_review_issues": [],
                "linked_source_ids": [obs["source_id"]],
                "linked_observation_ids": [obs["observation_id"]],
                "linked_op_ids": [],
                "disposition": "blocked_source" if blocked else "needs_review",
                "provisional": True,
            }
        )

    # Review items for sources without any observation.
    for source in sources.values():
        if source["observation_ordinals"]:
            continue
        if source["kind"] == "portal_document" and source["disposition"] == "blocked_source":
            continue  # already visible as blocked
        capability_id = f"review:{source['source_id']}"
        capabilities.append(
            {
                "capability_id": capability_id,
                "capability_kind": "unobserved_source",
                "operation_id": None,
                "region": source["region"],
                "source_doc_id": source["doc_id"],
                "source_content_sha256": source["content_sha256"],
                "method": None,
                "module": None,
                "symbols": [],
                "public_entry": None,
                "implementation_status": "not_applicable",
                "offline_status": None,
                "offline_evidence": None,
                "live_status": None,
                "live_evidence": None,
                "source_review_status": "unreviewed",
                "source_review_issues": [
                    _unresolved_reason(issue) for issue in source["unresolved"]
                ],
                "linked_source_ids": [source["source_id"]],
                "linked_observation_ids": [],
                "linked_op_ids": [],
                "disposition": source["disposition"],
                "provisional": True,
            }
        )

    # Apply reviewer dispositions (validated, no completion claims).
    decisions_hash = None
    decision_overrides = {}
    if decisions_path is not None:
        decisions_file = _resolve_file(Path(decisions_path))
        if decisions_file is None:
            raise _err("decisions path must be a resolvable file")
        decisions_data, decisions_raw = _load_decisions(decisions_file)
        decisions_hash = _sha256(decisions_raw)
        decision_overrides = _check_decisions(decisions_data, sources)
        input_hashes["decisions"] = decisions_hash
    public_capability_ids = {cap["capability_id"] for cap in capabilities}
    for source_id, override in decision_overrides.items():
        source = sources[source_id]
        source["disposition"] = override["disposition"]
        source["decision_capability_id"] = override["capability_id"]
        reference = dict(override["decision_reference"])
        reference["decisions_sha256"] = decisions_hash
        source["decision_reference"] = reference
        source["public_capability_ids"] = public_capability_ids

    # Public per-source unresolved markers: controlled codes only; warning
    # detail is a fixed marker (the element pointer lives on the unresolved
    # list entry, not on this summary list).
    for source in sources.values():
        source["unresolved_public"] = [_unresolved_reason(issue) for issue in source["unresolved"]]

    for source in sources.values():
        warning_iter = iter(source["_warning_refs"])
        for issue in source["unresolved"]:
            if issue == "inventory_warning":
                unresolved.append(
                    {
                        "kind": "source",
                        "source_id": source["source_id"],
                        "reason": "inventory_warning",
                        "element_index": next(warning_iter, None),
                    }
                )
            else:
                unresolved.append(
                    {
                        "kind": "source",
                        "source_id": source["source_id"],
                        "reason": _unresolved_reason(issue),
                    }
                )
        if source["disposition"] in ("needs_review", "blocked_source"):
            unresolved.append(
                {
                    "kind": "disposition",
                    "source_id": source["source_id"],
                    "reason": source["disposition"],
                }
            )

    # Every still-unreviewed or blocked provisional observation keeps its own
    # unresolved entry; a page-level disposition decision must not hide them.
    # The reason mirrors the provisional capability's own disposition, which
    # is derived from body availability, not the reviewed source override.
    for obs in observations:
        if obs["observation_id"] in matched_obs_ids:
            continue
        source = sources[obs["source_id"]]
        reason = "blocked_source" if source["_blocked"] else "needs_review"
        unresolved.append(
            {
                "kind": "observation",
                "observation_id": obs["observation_id"],
                "source_id": obs["source_id"],
                "reason": reason,
            }
        )

    # ---- Public projection ---------------------------------------------
    # Rebuild every entity list through the per-type whitelist; the internal
    # records above are never copied into the report.
    status_vocab = {
        "portal_document": _PORTAL_SOURCE_STATUSES,
        "sdk_reference": _SDK_REFERENCE_STATUSES,
        "asset": _ASSET_STATUSES,
    }
    public_sources = [
        _public_source(sources[key], status_vocab[sources[key]["kind"]]) for key in sorted(sources)
    ]
    public_observations = sorted(
        (_public_observation(obs, registry_operation_ids) for obs in observations),
        key=lambda item: item["observation_id"],
    )
    public_capabilities = sorted(
        (_public_capability(cap) for cap in capabilities),
        key=lambda item: item["capability_id"],
    )
    public_unresolved = []
    for item in unresolved:
        if item["kind"] not in _UNRESOLVED_KINDS:
            kind = "unclassified_review_issue"
        else:
            kind = item["kind"]
        entry = {"kind": kind}
        for field in ("source_id", "capability_id", "operation_id", "observation_id"):
            if item.get(field) is not None:
                entry[field] = item[field]
        entry["reason"] = _unresolved_reason(item["reason"])
        if item.get("element_index") is not None:
            entry["element_index"] = item["element_index"]
        public_unresolved.append(entry)
    public_unresolved.sort(
        key=lambda item: (
            item["kind"],
            item.get("source_id") or "",
            item.get("capability_id") or "",
            item["reason"],
            item.get("element_index") if item.get("element_index") is not None else -1,
        )
    )

    kind_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    disposition_counts: dict[str, int] = {}
    for source in public_sources:
        kind_counts[source["kind"]] = kind_counts.get(source["kind"], 0) + 1
        status_counts[source["status"]] = status_counts.get(source["status"], 0) + 1
        disposition_counts[source["disposition"]] = (
            disposition_counts.get(source["disposition"], 0) + 1
        )
    observation_kind_counts: dict[str, int] = {}
    for obs in public_observations:
        observation_kind_counts[obs["kind"]] = observation_kind_counts.get(obs["kind"], 0) + 1

    summary = {
        "source_count": len(public_sources),
        "observation_count": len(public_observations),
        "reviewed_contract_count": len(contracts),
        "provisional_capability_count": provisional_count
        + sum(1 for c in public_capabilities if c["capability_kind"] == "unobserved_source"),
        "unresolved_count": len(public_unresolved),
        "unresolved_reviewed_contracts": sum(
            1
            for c in public_capabilities
            if c["capability_kind"] == "reviewed_contract" and c["source_review_issues"]
        ),
        # Distributions are rebuilt from the published vocabulary-filtered
        # fields, so a raw status/kind string can never become a summary key.
        "sources_by_kind": dict(sorted(kind_counts.items())),
        "sources_by_status": dict(sorted(status_counts.items())),
        "sources_by_disposition": dict(sorted(disposition_counts.items())),
        "observations_by_kind": dict(sorted(observation_kind_counts.items())),
        "decisions_applied": len(decision_overrides),
        "counts_are_not_api_totals": True,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "input_manifest_schema_version": INPUT_MANIFEST_SCHEMA_VERSION,
        "evidence": EVIDENCE,
        "publication_profile": PUBLICATION_PROFILE,
        "product_complete": False,
        "denominator_defined": False,
        "input_hashes": dict(sorted(input_hashes.items())),
        "source_sets": source_sets,
        "sources": public_sources,
        "observations": public_observations,
        "capabilities": public_capabilities,
        "unresolved": public_unresolved,
        # Only the audited static identifier, the approved count summaries,
        # and the three checked input hashes are republished — never a
        # whole future audit object.
        "registry_audit": _public_registry_audit(audit_report),
        "summary": summary,
        "disclaimer": DISCLAIMER,
        "limitations": LIMITATIONS,
    }


def _check_output_path(output: Path, snapshots: list[dict]) -> None:
    """Reject any output that is not a brand-new path outside snapshot roots.

    Every existing filesystem object is refused — regular files, directories,
    and symlinks alike — so no input (manifest, decisions, registry contract,
    SDK source, archived body, inventory, or a previously written report) can
    be replaced. Hardlinked inputs share an inode with an existing path and
    are covered by the same lexists check.
    """
    try:
        resolved = output.resolve()
    except (OSError, RuntimeError):
        raise _err("output path cannot be resolved") from None
    # lexists covers symlinks (including dangling ones) at both the spelled
    # path and its resolved target; hardlinks share an inode with an existing
    # name and are caught by the same existence check.
    if os.path.lexists(output) or os.path.lexists(resolved):
        raise _err("output path already exists")
    for snap in snapshots:
        root = snap["root"]
        try:
            inside = resolved.is_relative_to(root)
        except (OSError, RuntimeError, ValueError):
            inside = False
        if inside:
            raise _err("output must not be written inside a snapshot root")


def _write_output(output: Path, text: str) -> None:
    """Publish via a same-directory temp file plus a non-overwriting link.

    ``os.link`` fails with ``EEXIST`` if the target appeared after the check,
    so a racing path can never be clobbered; the temp file is always removed.
    """
    tmp = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=".product-matrix-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp = Path(handle.name)
            handle.write(text)
        try:
            os.link(tmp, output)
        except FileExistsError:
            raise _err("output path already exists") from None
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise _err("output path already exists") from None
            raise _err("cannot write the output report") from None
    except ProductMatrixError:
        raise
    except OSError:
        raise _err("cannot write the output report") from None
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


_SUMMARY_KEYS = (
    "schema_version",
    "input_manifest_schema_version",
    "evidence",
    "publication_profile",
    "product_complete",
    "denominator_defined",
    "input_hashes",
    "source_sets",
    "summary",
    "registry_audit",
    "disclaimer",
    "limitations",
)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ProductMatrixError(f"product_matrix: {message}")


def main(argv=None) -> int:
    parser = _Parser(prog="harness.product_matrix", description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--decisions", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--summary-only", action="store_true")
    try:
        args = parser.parse_args(argv)
        report = build_matrix(args.repo, args.manifest, args.decisions)
        if args.summary_only:
            report = {key: report[key] for key in _SUMMARY_KEYS}
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output is not None:
            # Re-resolve snapshot roots for the root-containment check; the
            # build already validated the manifest bytes that were read.
            manifest_file = _resolve_file(Path(args.manifest))
            if manifest_file is None:
                raise _err("manifest path must be a resolvable file")
            manifest = _load_manifest(manifest_file)
            snapshots = _check_snapshots(manifest)
            _check_output_path(args.output, snapshots)
            _write_output(args.output, text)
            return 0
        sys.stdout.write(text)
        return 0
    except ProductMatrixError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except Exception:
        sys.stderr.write("product_matrix: unexpected failure\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
