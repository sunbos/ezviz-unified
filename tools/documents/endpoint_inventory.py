"""Offline HTTP declaration inventory over the verified document archive.

Reads and pins every archived raw JSON source itself (strict JSON, hash,
doc_id, and platform checks), then reuses ``tools.documents.derive._verify_sources``
as an independent consistency check whose returned ``(region, doc_id) ->
html`` map must equal the pinned HTML strings. Only after both verifications
succeed does it run the fixed ``extract_http_candidates`` parser and join
declarations to reviewed transport contracts through exact source fields.
The report feeds the endpoint audit checklist; candidate and link counts
are not API totals, support statements, or SDK coverage.

Run:
    python -m tools.documents.endpoint_inventory --input ARCHIVE [--contracts FILE] [--summary-only]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

if __package__:
    from .derive import _verify_sources
    from .http_candidates import extract_http_candidates
else:
    from derive import _verify_sources
    from http_candidates import extract_http_candidates

SCHEMA_VERSION = 1
EVIDENCE = "offline_http_declarations_only"
FAILURE_LINE = "endpoint-inventory: validation failed\n"

ARCHIVED = "archived"
# Exact status vocabulary emitted by collect_current.py. Anything else is
# rejected instead of being silently scanned or dropped.
UNAVAILABLE_STATUSES = frozenset(
    {
        "source_missing",
        "source_empty",
        "platform_mismatch",
        "acquisition_failed",
    }
)

# Catalog regions and the platform value each region's raw JSON source must
# declare. Region (domestic/overseas) and platform (doc_open/doc_open_en)
# are different fields and are never compared to each other directly.
REGION_PLATFORM = {"domestic": "doc_open", "overseas": "doc_open_en"}

# Canonical nine-method whitelist shared with the parser.
HTTP_METHODS = frozenset(
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

_DIGITS = re.compile(r"[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MISSING = object()

LIMITATIONS = [
    "declarations and examples are offline HTML observations; a candidate does not prove an endpoint exists, is complete, or is supported",
    "counts are document/candidate/link counts, not API totals or SDK coverage",
    "a document with zero declarations may still describe interfaces this parser did not observe",
    "static SDK references, attachments, and files not listed in catalog.json are outside the scanned corpus",
    "links are exact source joins over declared pairs only; declarations with fragments, queries, userinfo, or path templates are ineligible and registry data never completes a declaration",
    "same_route_operation_ids values are informational route hints only, not reviewed support",
    "unrecovered reviewed contracts are listed with explicit reasons instead of blocking the scan, and no candidate rows are fabricated",
    "the final hash recheck detects input drift during the run; it does not provide an atomic snapshot or OS-level isolation",
    "matching hashes tie output to input bytes but do not prove official documentation content is correct",
]

SUMMARY_KEYS = (
    "documents",
    "archived_documents",
    "source_unavailable_documents",
    "documents_with_declarations",
    "declarations",
    "paired_declarations",
    "unresolved_declarations",
    "examples",
    "matched_reviewed_contracts",
    "unrecovered_reviewed_contracts",
)

# The summary-only payload omits the per-file source hash map; the full
# report keeps it under source_files_sha256.
SUMMARY_ONLY_KEYS = (
    "schema_version",
    "evidence",
    "summary",
    "unrecovered_reviewed",
    "input_sha256",
    "limitations",
)

CONTRACT_FIELDS = (
    "region",
    "source_doc_id",
    "source_url",
    "source_content_sha256",
    "method",
    "path",
)


class InventoryError(ValueError):
    """Fixed validation failure for the endpoint inventory.

    Messages are fixed strings containing no raw input values, and failures
    never chain underlying JSON/Unicode/OS exceptions as cause or context.
    """


class _RejectedJSON(Exception):
    """Sentinel raised by JSON hooks; converted to a fixed InventoryError."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read(path, label):
    try:
        data = path.read_bytes()
    except OSError:
        data = None
    if data is None:
        raise InventoryError(f"{label}: unreadable")
    return data


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _RejectedJSON()
        result[key] = value
    return result


def _reject_constant(_name):
    raise _RejectedJSON()


def _checked_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise _RejectedJSON()
    return value


def _load_json(raw, label):
    """Strict UTF-8 JSON; rejects duplicate keys and non-finite numbers."""
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        text = None
    if text is None:
        raise InventoryError(f"{label}: invalid UTF-8")
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_checked_float,
        )
    except (ValueError, _RejectedJSON, RecursionError):
        parsed = _MISSING
    if parsed is _MISSING:
        raise InventoryError(f"{label}: invalid JSON")
    return parsed


def _resolve_inside(root, relative, label):
    """Resolve a path to a regular file inside root, rejecting escapes."""
    try:
        candidate = Path(relative)
        if candidate.is_absolute():
            resolved = None
        else:
            resolved = (root / candidate).resolve()
            if resolved == root or not resolved.is_relative_to(root) or not resolved.is_file():
                resolved = None
    except (OSError, RuntimeError, TypeError, ValueError):
        resolved = None
    if resolved is None:
        raise InventoryError(f"{label}: not a regular file inside the input directory")
    return resolved


def _numeric(value):
    """ASCII non-negative digit doc_id: no bool, negatives, or non-ASCII digits."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, str) and _DIGITS.fullmatch(value) is not None


def _sha256_hex(value):
    """Lowercase 64-hex digest string only."""
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _check_entry(entry):
    """Catalog entry validation using the real collect_current vocabulary."""
    if not isinstance(entry, dict):
        raise InventoryError("catalog entry must be an object")
    region = entry.get("region")
    if not isinstance(region, str) or region not in REGION_PLATFORM:
        raise InventoryError("catalog entry has invalid region")
    if not _numeric(entry.get("doc_id")):
        raise InventoryError("catalog entry doc_id must be numeric")
    status = entry.get("status")
    if not isinstance(status, str) or (status != ARCHIVED and status not in UNAVAILABLE_STATUSES):
        raise InventoryError("unknown catalog status")
    if not isinstance(entry.get("title"), str):
        raise InventoryError("catalog entry title must be a string")
    if not isinstance(entry.get("source_url"), str):
        raise InventoryError("catalog entry source_url must be a string")
    if not isinstance(entry.get("memberships"), list):
        raise InventoryError("catalog entry memberships must be a list")
    if status == ARCHIVED:
        for name in ("raw_file", "raw_file_sha256", "content_sha256"):
            if not isinstance(entry.get(name), str) or not entry.get(name):
                raise InventoryError("archived catalog entry is missing a required field")


def _category_paths(entry):
    paths = set()
    for membership in entry["memberships"]:
        if not isinstance(membership, dict):
            raise InventoryError("invalid catalog membership")
        path = membership.get("category_path")
        if not isinstance(path, list) or not all(isinstance(part, str) and part for part in path):
            raise InventoryError("invalid category_path")
        paths.add(tuple(path))
    return [list(path) for path in sorted(paths)]


def _pin_source(root, entry):
    """Read, validate, and pin one archived raw JSON source's HTML string.

    Performs the checks ``derive._verify_sources`` alone does not cover for
    this tool's report: strict JSON decoding, source doc_id equal to the
    catalog doc_id, and source platform equal to the region's expected
    platform. The raw source platform field is optional: a missing or null
    value is legal and matches the collector's real schema, but when it is
    present and non-null it must equal the expected platform. Returns the
    verified ``html_content`` string.
    """
    key = (entry["region"], str(entry["doc_id"]))
    resolved = _resolve_inside(root, Path(entry["raw_file"]), "raw source")
    raw = _read(resolved, "raw source")
    if _sha256(raw) != entry["raw_file_sha256"]:
        raise InventoryError("raw source hash does not match catalog entry")
    source = _load_json(raw, "raw source")
    if not isinstance(source, dict):
        raise InventoryError("raw source must be a JSON object")
    doc_id = source.get("doc_id", _MISSING)
    if doc_id is _MISSING or isinstance(doc_id, bool) or str(doc_id) != key[1]:
        raise InventoryError("source doc_id does not match catalog entry")
    platform = source.get("platform")
    if platform is not None and platform != REGION_PLATFORM[key[0]]:
        raise InventoryError("source platform does not match catalog entry")
    html = source.get("html_content")
    if not isinstance(html, str):
        raise InventoryError("raw source has no string html_content")
    try:
        digest = _sha256(html.encode("utf-8"))
    except UnicodeEncodeError:
        digest = None
    if digest != entry["content_sha256"]:
        raise InventoryError("source content hash does not match catalog entry")
    return html


def _contracts(data):
    if not isinstance(data, dict):
        raise InventoryError("contracts: top level must be an object")
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != SCHEMA_VERSION:
        raise InventoryError("contracts: unsupported schema_version")
    rows = data.get("contracts")
    if not isinstance(rows, list):
        raise InventoryError("contracts: missing contracts list")
    seen = set()
    contracts = []
    for row in rows:
        if not isinstance(row, dict):
            raise InventoryError("contracts: row must be an object")
        operation_id = row.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise InventoryError("contracts: invalid operation_id")
        if operation_id in seen:
            raise InventoryError("contracts: duplicate operation_id")
        seen.add(operation_id)
        contract = {"operation_id": operation_id}
        region = row.get("region")
        if not isinstance(region, str) or region not in REGION_PLATFORM:
            raise InventoryError("contracts: invalid region")
        contract["region"] = region
        doc_id = row.get("source_doc_id")
        if not _numeric(doc_id):
            raise InventoryError("contracts: invalid source_doc_id")
        contract["source_doc_id"] = str(doc_id)
        url = row.get("source_url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise InventoryError("contracts: invalid source_url")
        contract["source_url"] = url
        if not _sha256_hex(row.get("source_content_sha256")):
            raise InventoryError("contracts: invalid source_content_sha256")
        contract["source_content_sha256"] = row["source_content_sha256"]
        method = row.get("method")
        if not isinstance(method, str) or method not in HTTP_METHODS:
            raise InventoryError("contracts: invalid method")
        contract["method"] = method
        path = row.get("path")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or "://" in path
            or any(ch in path for ch in "?#@")
            or any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in path)
        ):
            raise InventoryError("contracts: invalid path")
        contract["path"] = path
        contracts.append(contract)
    return contracts


def _parse(content):
    try:
        parsed = extract_http_candidates(content)
    except Exception:
        parsed = _MISSING
    if not isinstance(parsed, dict):
        raise InventoryError("parser result must be an object")
    return parsed


def _source_reason(contract, records):
    """Why a reviewed contract source cannot be recovered, or None."""
    record = records.get((contract["region"], contract["source_doc_id"]))
    if record is None:
        return "source_document_missing"
    if record["status"] != ARCHIVED:
        return "source_document_unavailable"
    if record["source_url"] != contract["source_url"]:
        return "source_url_changed"
    if record.get("content_sha256") != contract["source_content_sha256"]:
        return "source_content_changed"
    return None


def _exact_eligible(path):
    # A templated host with a literal path (e.g. overseas 485) stays
    # eligible; path templates, queries, fragments, and userinfo can never
    # join exactly.
    return not any(ch in path for ch in "{}?#@")


def _linked(declaration, entry, doc_id, strict_index, route_index):
    """Exact-join operation ids for one declaration; never fills in routes."""
    reviewed = []
    same_route = []
    method = declaration.get("method")
    path = declaration.get("path")
    if isinstance(method, str) and method and isinstance(path, str) and path:
        # Informational route hints only; not evidence of review.
        same_route = sorted(route_index.get((entry["region"], method, path), ()))
        if (
            declaration.get("status") == "declared_pair"
            and _exact_eligible(path)
            # The parser strips query/fragment into separate fields, so a
            # declared address carrying either can never join exactly.
            and not declaration.get("query")
            and not declaration.get("fragment")
        ):
            key = (
                entry["region"],
                doc_id,
                entry["source_url"],
                entry["content_sha256"],
                method,
                path,
            )
            reviewed = sorted(strict_index.get(key, ()))
    return reviewed, same_route


def _recheck(root, catalog_sha256, source_files, contracts_path, contracts_sha256):
    """Re-resolve, re-read and re-hash every input used, rejecting drift."""
    catalog_path = _resolve_inside(root, Path("catalog.json"), "catalog.json")
    if _sha256(_read(catalog_path, "catalog.json")) != catalog_sha256:
        raise InventoryError("catalog.json changed during the run")
    for relative, expected in source_files.items():
        resolved = _resolve_inside(root, Path(relative), "raw source")
        if _sha256(_read(resolved, "raw source")) != expected:
            raise InventoryError("raw source changed during the run")
    if contracts_path is not None:
        if _sha256(_read(contracts_path, "contracts")) != contracts_sha256:
            raise InventoryError("contracts file changed during the run")


def build_inventory(archive: Path, *, contracts_path: Path | None = None) -> dict:
    """Verify the archive, scan declarations, and join reviewed contracts.

    Raises InventoryError on any validation problem and never returns a
    partial report. Output is deterministic for identical input bytes.
    """
    root = Path(archive).resolve()
    catalog_path = _resolve_inside(root, Path("catalog.json"), "catalog.json")
    catalog_bytes = _read(catalog_path, "catalog.json")
    catalog_sha256 = _sha256(catalog_bytes)
    catalog = _load_json(catalog_bytes, "catalog.json")
    if not isinstance(catalog, list):
        raise InventoryError("catalog.json: top level must be a list")

    records = {}
    category_paths = {}
    raw_files = {}
    for entry in catalog:
        _check_entry(entry)
        key = (entry["region"], str(entry["doc_id"]))
        if key in records:
            raise InventoryError("duplicate catalog entry")
        records[key] = entry
        category_paths[key] = _category_paths(entry)
        if entry["status"] == ARCHIVED:
            raw_files[entry["raw_file"]] = entry["raw_file_sha256"]
    source_files = dict(sorted(raw_files.items()))

    # Pin every archived document's HTML from its raw JSON source (strict
    # JSON, raw_file_sha256, doc_id, platform, content_sha256) before any
    # parsing; a single failure aborts the run.
    pinned = {}
    for entry in catalog:
        if entry["status"] == ARCHIVED:
            key = (entry["region"], str(entry["doc_id"]))
            pinned[key] = _pin_source(root, entry)

    # Independent consistency check: derive's verifier must return exactly
    # the same (region, doc_id) -> HTML string map we pinned.
    try:
        contents = _verify_sources(root, catalog)
    except Exception:
        contents = _MISSING
    if contents is _MISSING or not isinstance(contents, dict) or contents != pinned:
        raise InventoryError("source verification failed")

    contracts = []
    contracts_sha256 = None
    contracts_file = None
    if contracts_path is not None:
        contracts_file = Path(contracts_path)
        contracts_bytes = _read(contracts_file, "contracts")
        contracts_sha256 = _sha256(contracts_bytes)
        contracts = _contracts(_load_json(contracts_bytes, "contracts"))

    strict_index = {}
    route_index = {}
    for contract in contracts:
        strict_key = tuple(contract[name] for name in CONTRACT_FIELDS)
        strict_index.setdefault(strict_key, []).append(contract["operation_id"])
        route_key = (contract["region"], contract["method"], contract["path"])
        route_index.setdefault(route_key, []).append(contract["operation_id"])

    summary = dict.fromkeys(SUMMARY_KEYS, 0)
    documents = []
    matched_operations = set()
    ordered = sorted(catalog, key=lambda item: (item["region"], int(str(item["doc_id"]))))
    for entry in ordered:
        key = (entry["region"], str(entry["doc_id"]))
        document = {
            "region": entry["region"],
            "doc_id": entry["doc_id"],
            "title": entry["title"],
            "source_url": entry["source_url"],
            "content_sha256": entry.get("content_sha256"),
            "category_paths": category_paths[key],
            "status": ("scanned" if entry["status"] == ARCHIVED else "source_unavailable"),
            "declarations": [],
            "examples": [],
            "warnings": [],
        }
        documents.append(document)
        summary["documents"] += 1
        if entry["status"] != ARCHIVED:
            summary["source_unavailable_documents"] += 1
            continue
        summary["archived_documents"] += 1
        html = pinned[key]
        parsed = _parse(html)
        for field in ("declarations", "examples", "warnings"):
            if not isinstance(parsed.get(field), list):
                raise InventoryError(f"parser result missing {field} list")
        for declaration in parsed["declarations"]:
            if not isinstance(declaration, dict):
                raise InventoryError("declaration rows must be objects")
            row = dict(declaration)
            reviewed, same_route = _linked(declaration, entry, key[1], strict_index, route_index)
            row["reviewed_operation_ids"] = reviewed
            row["same_route_operation_ids"] = same_route
            matched_operations.update(reviewed)
            document["declarations"].append(row)
            summary["declarations"] += 1
            if declaration.get("status") == "declared_pair":
                summary["paired_declarations"] += 1
        if document["declarations"]:
            summary["documents_with_declarations"] += 1
        summary["examples"] += len(parsed["examples"])
        document["examples"] = parsed["examples"]
        document["warnings"] = parsed["warnings"]
    summary["unresolved_declarations"] = summary["declarations"] - summary["paired_declarations"]

    unrecovered = []
    for contract in contracts:
        reason = _source_reason(contract, records)
        if reason is None and contract["operation_id"] not in matched_operations:
            reason = "no_matching_declaration"
        if reason is None:
            summary["matched_reviewed_contracts"] += 1
        else:
            unrecovered.append({"operation_id": contract["operation_id"], "reason": reason})
    unrecovered.sort(key=lambda item: item["operation_id"])
    summary["unrecovered_reviewed_contracts"] = len(unrecovered)

    _recheck(root, catalog_sha256, source_files, contracts_file, contracts_sha256)

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence": EVIDENCE,
        "summary": summary,
        "documents": documents,
        "unrecovered_reviewed": unrecovered,
        "input_sha256": {
            "catalog": catalog_sha256,
            "contracts": contracts_sha256,
        },
        "source_files_sha256": source_files,
        "limitations": LIMITATIONS,
    }


class _Parser(argparse.ArgumentParser):
    """Argparse variant whose parse errors become InventoryError."""

    def error(self, message):
        raise InventoryError(message)


def main(argv=None) -> int:
    parser = _Parser(
        prog="tools.documents.endpoint_inventory",
        description="Offline HTTP declaration inventory over a verified archive.",
    )
    parser.add_argument(
        "--input", required=True, type=Path, help="archive directory containing catalog.json"
    )
    parser.add_argument(
        "--contracts", type=Path, default=None, help="optional reviewed transport contracts JSON"
    )
    parser.add_argument(
        "--summary-only", action="store_true", help="emit only the fixed summary block"
    )
    try:
        args = parser.parse_args(argv)
        result = build_inventory(args.input, contracts_path=args.contracts)
        if args.summary_only:
            result = {key: result[key] for key in SUMMARY_ONLY_KEYS}
        # Serialization and UTF-8 encoding stay inside the guard so
        # unencodable metadata can never print a traceback.
        payload = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        sys.stdout.buffer.write(payload)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        sys.stderr.write(FAILURE_LINE)
        return 2
    except Exception:
        sys.stderr.write(FAILURE_LINE)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
