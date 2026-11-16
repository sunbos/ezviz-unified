"""Tests for harness.product_matrix (offline matrix over synthetic fixtures).

Fixtures build minimal but real registry inputs plus synthetic snapshot
manifests under tmp_path. No local data directory, credential, or network
is touched; audit_repository is monkeypatched with explicit results where a
full minimal repo is unnecessary.

Schema 2 boundary: the report is an explicit per-type whitelist projection.
Tests assert omissions (no raw locator, host, path, title, id, reason text
anywhere in the serialized report) rather than the presence of sanitized
derivatives — a sanitized copy would still be a republication of raw data.
"""

import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64

# Keys that must never appear on a published source row.
_FORBIDDEN_SOURCE_KEYS = {
    "url",
    "url_markers",
    "source_url",
    "title",
    "raw_file",
    "raw_file_sha256",
    "missing_evidence",
    "local_path",
    "file",
    "discovered_from",
    "asset_id",
    "reuse_candidate",
    "body_available",
    "decision_reason",
    "decision_evidence_refs",
    "decision_recorded",
}
# Keys that must never appear on a published observation row.
_FORBIDDEN_OBSERVATION_KEYS = {
    "path",
    "host",
    "raw_address",
    "raw_method",
    "query",
    "fragment",
    "address_kind",
    "section_title",
    "evidence",
    "reasons",
    "inventory_url",
    "reviewed_operation_ids",
    "related_example_ordinals",
    "future_parser_field",
    "future_example_field",
    "provisional_url",
}
# Keys that must never appear on a published capability row.
_FORBIDDEN_CAPABILITY_KEYS = {
    "source_url",
    "source_url_markers",
    "url",
    "url_markers",
}
_FORBIDDEN_UNRESOLVED_KEYS = {
    "code",
    "warning",
    "section_title",
    "path",
    "url",
    "source_url",
}
_SOURCE_KIND_STATUSES = {
    "portal_document": {
        "archived",
        "source_missing",
        "source_empty",
        "platform_mismatch",
        "acquisition_failed",
        "inventory_only",
        "unknown",
    },
    "sdk_reference": {
        "ok",
        "http_error",
        "redirect_outside_or_alias",
        "not_html",
        "missing_html_title",
        "network_error",
        "queued",
        "unknown",
    },
    "asset": {
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
        "unknown",
    },
}
_HTTP_METHODS = {
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
_DISPOSITIONS = {
    "implement",
    "reuse_official_component",
    "not_an_api",
    "duplicate",
    "deprecated",
    "phase_2",
    "needs_review",
    "blocked_source",
}
_REASON_CODES = {
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
    "unknown_reason",
}


def module():
    assert Path("harness/product_matrix.py").exists(), "product matrix tool missing"
    return importlib.import_module("harness.product_matrix")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, payload) -> bytes:
    if isinstance(payload, bytes):
        path.write_bytes(payload)
        return payload
    raw = json.dumps(payload, ensure_ascii=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _registry_files(root: Path, contracts, bindings, issues=None):
    folder = root / "contracts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reviewed-transport-contracts.json").write_text(
        json.dumps({"schema_version": 1, "contracts": contracts})
    )
    (folder / "sdk-bindings.json").write_text(
        json.dumps({"schema_version": 1, "bindings": bindings})
    )
    (folder / "source-issues.json").write_text(
        json.dumps({"schema_version": 1, "issues": issues or []})
    )


def _audit_stub(root: Path):
    folder = root / "contracts"
    return {
        "schema_version": 1,
        "evidence": "registry_and_static_ast_only",
        "registry": {
            "summary": {
                "contracts": 1,
                "bindings": 1,
                "source_documents": 1,
                "source_issues": 0,
            }
        },
        "code": {"summary": {"bindings_checked": 1, "symbols_checked": 2}},
        "inputs_sha256": {
            name: _sha((folder / name.rsplit("/", 1)[-1]).read_bytes())
            for name in (
                "contracts/reviewed-transport-contracts.json",
                "contracts/sdk-bindings.json",
                "contracts/source-issues.json",
            )
        },
    }


def _contract(
    operation_id="cn.demo.op",
    doc_id="10",
    region="domestic",
    url="https://open.ys7.com/help/10",
    sha=SHA_A,
    method="POST",
    path="/api/lapp/demo/op",
):
    return {
        "operation_id": operation_id,
        "region": region,
        "source_doc_id": doc_id,
        "source_url": url,
        "source_content_sha256": sha,
        "method": method,
        "path": path,
        "read_only": True,
    }


def _binding(operation_id="cn.demo.op", doc_id="10", region="domestic"):
    return {
        "operation_id": operation_id,
        "region": region,
        "source_doc_id": doc_id,
        "module": "src/ezviz_unified/demo.py",
        "symbols": ["Demo.op", "AsyncDemo.op"],
        "offline_status": "passed",
        "live_status": "not_verified",
        "public_entry": "demo.op",
    }


def _catalog_row(
    doc_id="10",
    region="domestic",
    status="archived",
    url="https://open.ys7.com/help/10",
    sha=SHA_A,
    raw_file="pages/domestic/10.source.json",
    raw_sha=SHA_B,
    title="Demo",
    memberships=None,
):
    row = {
        "region": region,
        "doc_id": doc_id,
        "source_url": url,
        "title": title,
        "status": status,
        "content_sha256": sha,
        "memberships": memberships
        if memberships is not None
        else [{"category_path": ["API", "Demo"]}],
        "missing_evidence": None,
    }
    if raw_file is not None:
        row["raw_file"] = raw_file
        row["raw_file_sha256"] = raw_sha
    return row


def _declaration(
    ordinal=0,
    status="declared_pair",
    method="POST",
    path="/api/lapp/demo/op",
    host="open.ys7.com",
    raw="https://open.ys7.com/api/lapp/demo/op",
    section_index=5,
    element_index=9,
    query="",
    fragment="",
    reasons=None,
    reviewed=None,
    same_route=None,
):
    # Mirrors the real extract_http_candidates declaration schema: the
    # relation to examples lives on the example side
    # (related_declaration_ordinals), never as a reverse field here.
    return {
        "ordinal": ordinal,
        "status": status,
        "method": method,
        "raw_method": method,
        "raw_address": raw,
        "address_kind": "absolute",
        "host": host,
        "path": path,
        "query": query,
        "fragment": fragment,
        "section_title": "1. Demo",
        "section_index": section_index,
        "evidence": [
            {"role": "address_label", "element_index": element_index, "text": "请求地址"},
            {"role": "address_value", "element_index": element_index + 1, "text": raw},
        ],
        "reasons": reasons or [],
        "reviewed_operation_ids": reviewed or [],
        "same_route_operation_ids": same_route or [],
    }


def _example(
    ordinal=0,
    method="POST",
    path="/api/lapp/demo/op",
    related=None,
    agrees=True,
):
    # Mirrors the real example schema: related_declaration_ordinals are
    # same-section declaration ordinals, agrees_with_declaration may be
    # True/False/None; neither is evidence of reviewed support.
    return {
        "ordinal": ordinal,
        "status": "example_only",
        "method": method,
        "raw_method": method,
        "raw_address": f"curl -X {method} 'https://open.ys7.com{path}?accessToken=SECRET'",
        "address_kind": "absolute",
        "host": "open.ys7.com",
        "path": path,
        "query": "accessToken=SECRET&appKey=hidden",
        "fragment": "",
        "section_title": "2. Example",
        "section_index": 6,
        "evidence": [
            {"role": "code_block", "element_index": 12, "text": "curl -X POST secret-body"}
        ],
        "reasons": [],
        "related_declaration_ordinals": related if related is not None else [0],
        "agrees_with_declaration": agrees,
    }


def _inventory_doc(
    doc_id="10",
    region="domestic",
    url="https://open.ys7.com/help/10",
    sha=SHA_A,
    declarations=None,
    examples=None,
    warnings=None,
):
    return {
        "region": region,
        "doc_id": doc_id,
        "source_url": url,
        "content_sha256": sha,
        "category_paths": [["API", "Demo"]],
        "status": "scanned",
        "declarations": declarations if declarations is not None else [_declaration()],
        "examples": examples if examples is not None else [_example()],
        "warnings": warnings if warnings is not None else [],
    }


def _snapshot(
    snapshot_id, kind, root, manifest_bytes, inventory=None, inventory_bytes=None, region=None
):
    snap = {
        "id": snapshot_id,
        "kind": kind,
        "root": str(root),
        "manifest_sha256": _sha(manifest_bytes),
    }
    if inventory is not None:
        snap["inventory"] = str(inventory)
        snap["inventory_sha256"] = _sha(inventory_bytes)
    if region is not None:
        snap["region"] = region
    return snap


def _portal_fixture(tmp_path: Path, catalog, inventory_docs=None):
    # Each call gets its own snapshot root / inventory path so a test can
    # build several portal snapshots under one tmp_path without collisions.
    # The first call keeps the historical portal-snap/inventory.json names.
    index = 0
    while True:
        suffix = "" if index == 0 else f"-{index}"
        root = tmp_path / f"portal-snap{suffix}"
        inventory = tmp_path / f"inventory{suffix}.json"
        if not root.exists() and not inventory.exists():
            break
        index += 1
    root.mkdir()
    for row in catalog:
        raw_file = row.get("raw_file")
        if row.get("status") == "archived" and raw_file:
            target = root / raw_file
            target.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(
                {
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "html_content": f"<html>{row['doc_id']}</html>",
                    "platform": "doc_open" if row["region"] == "domestic" else "doc_open_en",
                },
                ensure_ascii=False,
            ).encode()
            target.write_bytes(body)
            # Pin the declared sha to the bytes actually written.
            row["raw_file_sha256"] = _sha(body)
    manifest_bytes = _write(root / "catalog.json", catalog)
    inventory_bytes = None
    if inventory_docs is None:
        inventory = None
    else:
        inventory_bytes = _write(inventory, {"documents": inventory_docs})
    return _snapshot(
        "snap-portal",
        "portal",
        root,
        manifest_bytes,
        inventory=inventory,
        inventory_bytes=inventory_bytes,
    )


def _reference_fixture(tmp_path: Path, entries, files=None):
    root = tmp_path / "sdk-ref"
    root.mkdir()
    manifest_bytes = _write(root / "manifest.json", {"entries": entries})
    for relative, payload in (files or {}).items():
        _write(root / relative, payload)
    return _snapshot("snap-sdkref", "sdk_reference", root, manifest_bytes, region="domestic")


def _assets_fixture(tmp_path: Path, entries, files=None):
    root = tmp_path / "assets-snap"
    root.mkdir()
    lines = b"".join(json.dumps(e, ensure_ascii=False).encode() + b"\n" for e in entries)
    _write(root / "manifest.jsonl", lines)
    for relative, payload in (files or {}).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _write(target, payload)
    return _snapshot("snap-assets", "assets", root, lines)


def _manifest(tmp_path: Path, snapshots) -> Path:
    path = tmp_path / "matrix-manifest.json"
    _write(path, {"schema_version": 1, "snapshots": snapshots})
    return path


def _repo(tmp_path: Path, contracts=None, bindings=None):
    root = tmp_path / "repo"
    # exist_ok so a test may build several matrices over one tmp_path.
    root.mkdir(exist_ok=True)
    _registry_files(root, contracts or [_contract()], bindings or [_binding()])
    return root


def _build(tmp_path, monkeypatch, snapshots, contracts=None, bindings=None):
    mod = module()
    repo = _repo(tmp_path, contracts, bindings)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    manifest = _manifest(tmp_path, snapshots)
    return mod.build_matrix(repo, manifest)


# ---------------------------------------------------------------------------
# Frozen schema-2 field whitelists, declared independently of the
# implementation: every published dict must equal its required set plus the
# conditional optional keys below. No implementation constant is imported.
# ---------------------------------------------------------------------------
_TOP_LEVEL_KEYS = {
    "schema_version",
    "input_manifest_schema_version",
    "evidence",
    "publication_profile",
    "product_complete",
    "denominator_defined",
    "input_hashes",
    "source_sets",
    "sources",
    "observations",
    "capabilities",
    "unresolved",
    "registry_audit",
    "summary",
    "disclaimer",
    "limitations",
}
_SOURCE_BASE_KEYS = {
    "source_id",
    "snapshot_id",
    "record_index",
    "kind",
    "region",
    "doc_id",
    "canonical_document_url",
    "status",
    "content_sha256",
    "category_paths",
    "disposition",
    "operation_ids",
    "observation_ordinals",
    "evidence_refs",
    "unresolved",
}
_SOURCE_KIND_EXTRA_KEYS = {
    "portal_document": frozenset(),
    "sdk_reference": frozenset({"discovery_reference_count"}),
    "asset": frozenset({"asset_category", "reference_count", "references"}),
}
# Legal optional decision fields, merged in only when the condition holds.
_SOURCE_OPTIONAL_KEYS = {
    "decision_reference",
    "decision_capability_id",
    "has_external_capability_ref",
}
_OBSERVATION_KEYS = {
    "observation_id",
    "source_id",
    "kind",
    "ordinal",
    "status",
    "method",
    "section_index",
    "element_indices",
    "has_query",
    "has_fragment",
    "reason_codes",
    "reason_count",
    "same_route_hints",
    "related_declaration_ordinals",
    "agrees_with_declaration",
    "exact_operation_ids",
    "inventory_content_sha256",
}
_CAPABILITY_PROVISIONAL_KEYS = {
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
}
_CAPABILITY_REVIEWED_KEYS = _CAPABILITY_PROVISIONAL_KEYS - {"linked_op_ids"} | {
    "canonical_document_url",
    "path",
    "related_example_ids",
}
_CAPABILITY_KEYS_BY_KIND = {
    "reviewed_contract": _CAPABILITY_REVIEWED_KEYS,
    "provisional_observation": _CAPABILITY_PROVISIONAL_KEYS,
    "unobserved_source": _CAPABILITY_PROVISIONAL_KEYS,
}
_UNRESOLVED_BASE_KEYS = {"kind", "reason"}
_UNRESOLVED_OPTIONAL_KEYS = {
    "source_id",
    "capability_id",
    "operation_id",
    "observation_id",
    "element_index",
}
_REGISTRY_AUDIT_KEYS = {
    "evidence",
    "registry_summary",
    "code_summary",
    "inputs_sha256",
}
_REGISTRY_SUMMARY_KEYS = {"contracts", "bindings", "source_documents", "source_issues"}
_CODE_SUMMARY_KEYS = {"bindings_checked", "symbols_checked"}
_AUDIT_INPUT_KEYS = {
    "contracts/reviewed-transport-contracts.json",
    "contracts/sdk-bindings.json",
    "contracts/source-issues.json",
}
_SOURCE_SET_KEYS = {
    "snapshot_id",
    "kind",
    "region",
    "manifest_sha256",
    "inventory_sha256",
    "source_count",
    "observation_count",
}
_SUMMARY_KEYS = {
    "source_count",
    "observation_count",
    "reviewed_contract_count",
    "provisional_capability_count",
    "unresolved_count",
    "unresolved_reviewed_contracts",
    "sources_by_kind",
    "sources_by_status",
    "sources_by_disposition",
    "observations_by_kind",
    "decisions_applied",
    "counts_are_not_api_totals",
}
_REFERENCE_KEYS = {"reference_index", "reference_type", "evidence_ref"}
_DECISION_REFERENCE_KEYS = {
    "decisions_sha256",
    "record_index",
    "reason_present",
    "evidence_ref_count",
}


def _assert_public_schema(report):
    """Per-type whitelist shape + graph-reference integrity assertions.

    Every published dict must equal its independently-declared field set —
    a silently added key fails the equality, and the legacy forbidden-key
    canaries stay as negative checks. Cross-entity ids must resolve inside
    the public id space, proving no internal/locator-derived id is
    reachable from the report.
    """
    assert set(report) == _TOP_LEVEL_KEYS
    assert report["schema_version"] == 2
    assert report["input_manifest_schema_version"] == 1
    assert report["publication_profile"] == "reviewed_metadata_only"
    assert report["evidence"] == "source_and_registry_matrix_only"
    assert report["product_complete"] is False
    assert report["denominator_defined"] is False
    # Nested audit object: only the fixed evidence token, the approved
    # count summaries, and the three checked input hashes.
    audit = report["registry_audit"]
    assert set(audit) == _REGISTRY_AUDIT_KEYS
    assert audit["evidence"] == "registry_and_static_ast_only"
    assert set(audit["registry_summary"]) == _REGISTRY_SUMMARY_KEYS
    assert all(value is None or type(value) is int for value in audit["registry_summary"].values())
    assert set(audit["code_summary"]) == _CODE_SUMMARY_KEYS
    assert all(value is None or type(value) is int for value in audit["code_summary"].values())
    assert set(audit["inputs_sha256"]) == _AUDIT_INPUT_KEYS
    for source_set in report["source_sets"]:
        assert set(source_set) == _SOURCE_SET_KEYS
    assert set(report["summary"]) == _SUMMARY_KEYS
    source_ids = set()
    obs_ids = set()
    capability_ids = set()
    operation_ids = {
        c["operation_id"]
        for c in report["capabilities"]
        if c["capability_kind"] == "reviewed_contract"
    }
    for source in report["sources"]:
        assert _FORBIDDEN_SOURCE_KEYS.isdisjoint(source), source
        assert source["kind"] in _SOURCE_KIND_EXTRA_KEYS
        expected = _SOURCE_BASE_KEYS | _SOURCE_KIND_EXTRA_KEYS[source["kind"]]
        extra = set(source) - expected
        # Required-key containment: every declared base/per-kind key must be
        # present (static kind label only — never the live source payload).
        assert expected <= set(source), (source["kind"], sorted(expected - set(source)))
        assert extra <= _SOURCE_OPTIONAL_KEYS, source
        # Optional decision fields only ride along with a decision pointer;
        # the visible/external capability markers stay mutually exclusive.
        if extra:
            assert "decision_reference" in source
            assert not (
                "decision_capability_id" in source and "has_external_capability_ref" in source
            )
        assert source["status"] in _SOURCE_KIND_STATUSES[source["kind"]]
        assert source["disposition"] in _DISPOSITIONS
        assert (
            source["source_id"]
            == f"{source['snapshot_id']}:" + source["source_id"].split(":", 1)[1]
        )
        url = source["canonical_document_url"]
        if source["kind"] == "portal_document":
            assert url is not None and url.endswith(f"/{source['doc_id']}")
            assert source["doc_id"].isdigit() and source["doc_id"].isascii()
            assert source["region"] in {"domestic", "overseas"}
        else:
            assert url is None
            tail = source["source_id"].split(":", 1)[1]
            expected_prefix = "sdk_reference:" if source["kind"] == "sdk_reference" else "asset:"
            assert tail == f"{expected_prefix}{source['record_index']}"
        if source["kind"] == "asset":
            assert source["asset_category"] in {"image", "document", "unknown"}
            assert source["reference_count"] == len(source["references"])
            for reference in source["references"]:
                assert set(reference) == _REFERENCE_KEYS
                assert reference["reference_type"] in {
                    "link",
                    "image",
                    "embedded_resource",
                    "unknown",
                }
        if source["kind"] == "sdk_reference":
            assert type(source["discovery_reference_count"]) is int
        # category_paths only ever carries catalog-verified memberships:
        # an inventory_only source was never catalog-confirmed, so the
        # public slot must be empty even when the inventory recorded paths.
        if source["status"] == "inventory_only":
            assert source["category_paths"] == []
        for ref in source["evidence_refs"]:
            assert ref.startswith(f"{source['snapshot_id']}/")
            assert not any(ch in ref for ch in "?:#@") or "#" in ref
        for op in source["operation_ids"]:
            assert op in operation_ids
        for item in source["observation_ordinals"]:
            assert set(item) == {"kind", "ordinal"}
        for reason in source["unresolved"]:
            assert isinstance(reason, str) and ":" not in reason
        if "decision_reference" in source:
            ref = source["decision_reference"]
            assert set(ref) == _DECISION_REFERENCE_KEYS
            assert ref["decisions_sha256"] == report["input_hashes"]["decisions"]
            assert ref["reason_present"] is True
            assert ref["evidence_ref_count"] >= 1
        source_ids.add(source["source_id"])
    for obs in report["observations"]:
        assert set(obs) == _OBSERVATION_KEYS, obs
        assert _FORBIDDEN_OBSERVATION_KEYS.isdisjoint(obs), obs
        assert obs["source_id"] in source_ids
        assert obs["kind"] in {"declaration", "example"}
        assert obs["status"] in {
            "declared_pair",
            "ambiguous",
            "address_only",
            "example_only",
            "unknown",
        }
        assert obs["method"] is None or obs["method"] in _HTTP_METHODS
        assert set(obs["reason_codes"]) <= _REASON_CODES
        assert obs["reason_count"] >= len(obs["reason_codes"]) - (
            1 if "unknown_reason" in obs["reason_codes"] else 0
        )
        for op in obs["same_route_hints"] + obs["exact_operation_ids"]:
            assert op in operation_ids
        obs_ids.add(obs["observation_id"])
    for cap in report["capabilities"]:
        assert cap["capability_kind"] in _CAPABILITY_KEYS_BY_KIND
        assert set(cap) == _CAPABILITY_KEYS_BY_KIND[cap["capability_kind"]], cap
        assert _FORBIDDEN_CAPABILITY_KEYS.isdisjoint(cap), cap
        for sid in cap["linked_source_ids"]:
            assert sid in source_ids
        for oid in cap["linked_observation_ids"]:
            assert oid in obs_ids
        capability_ids.add(cap["capability_id"])
        if cap["capability_kind"] == "reviewed_contract":
            # Only a reviewed contract may republish a registry path and a
            # constructed canonical document locator.
            assert isinstance(cap["path"], str) and cap["path"]
            url = cap["canonical_document_url"]
            assert url is None or url.endswith(f"/{cap['source_doc_id']}")
            assert cap["provisional"] is False
            for oid in cap["related_example_ids"]:
                assert oid in obs_ids
        else:
            assert "path" not in cap
            assert "canonical_document_url" not in cap
            assert cap["provisional"] is True
    for source in report["sources"]:
        if "decision_capability_id" in source:
            assert source["decision_capability_id"] in capability_ids
    for item in report["unresolved"]:
        assert _UNRESOLVED_BASE_KEYS <= set(item), item
        assert set(item) <= (_UNRESOLVED_BASE_KEYS | _UNRESOLVED_OPTIONAL_KEYS), item
        assert _FORBIDDEN_UNRESOLVED_KEYS.isdisjoint(item), item
        assert item["kind"] in {
            "reviewed_contract_source",
            "source",
            "disposition",
            "observation",
        }
        if item["kind"] in {"source", "disposition"}:
            assert "source_id" in item
        if item["kind"] == "observation":
            assert {"observation_id", "source_id"} <= set(item)
        if item["kind"] == "reviewed_contract_source":
            assert {"capability_id", "operation_id"} <= set(item)
        if "source_id" in item:
            assert item["source_id"] in source_ids
        if "capability_id" in item:
            assert item["capability_id"] in capability_ids
        if "observation_id" in item:
            assert item["observation_id"] in obs_ids
        if "operation_id" in item:
            assert item["operation_id"] in operation_ids


def test_full_fixture_all_sources_preserved(tmp_path, monkeypatch):
    """Every catalog/static/asset row keeps a unique source; no silent drop."""
    catalog = [
        _catalog_row(),
        _catalog_row(
            doc_id="11",
            url="https://open.ys7.com/help/11",
            status="source_missing",
            sha=None,
            raw_file=None,
            raw_sha=None,
            memberships=[],
        ),
        _catalog_row(
            doc_id="12",
            url="https://open.ys7.com/help/12",
            sha=SHA_C,
            raw_file="pages/domestic/12.source.json",
            raw_sha=SHA_D,
            memberships=[{"category_path": ["Guide"]}],
        ),
    ]
    docs = [
        _inventory_doc(),
        _inventory_doc(
            doc_id="12", url="https://open.ys7.com/help/12", sha=SHA_C, declarations=[], examples=[]
        ),
    ]
    portal = _portal_fixture(tmp_path, catalog, docs)
    entries = [
        {
            "url": "https://open.ys7.com/doc/zh/sdk/a.html",
            "status": "ok",
            "local_path": "a.html",
            "sha256": _sha(b"<html>a</html>"),
            "discovered_from": ["seed"],
        },
        {
            "url": "https://open.ys7.com/doc/zh/sdk/b.html",
            "status": "http_error",
            "local_path": None,
            "sha256": None,
            "discovered_from": ["a.html"],
        },
    ]
    sdkref = _reference_fixture(tmp_path, entries, files={"a.html": b"<html>a</html>"})
    assets = _assets_fixture(
        tmp_path,
        [
            {
                "asset_id": "aa",
                "url": "https://img.ys7.com/x.pdf",
                "status": "ok",
                "file": "blobs/aa.bin",
                "sha256": _sha(b"pdf-bytes"),
                "category": "document",
                "references": [{"url": "https://open.ys7.com/help/10", "reference_type": "link"}],
            },
            {
                "asset_id": "bb",
                "url": "https://img.ys7.com/y.zip",
                "status": "excluded_sdk_binary",
                "file": None,
                "sha256": None,
                "category": "document",
                "references": [],
            },
        ],
        files={"blobs/aa.bin": b"pdf-bytes"},
    )
    report = _build(tmp_path, monkeypatch, [portal, sdkref, assets])

    assert report["schema_version"] == 2
    assert report["input_manifest_schema_version"] == 1
    assert report["publication_profile"] == "reviewed_metadata_only"
    assert report["evidence"] == "source_and_registry_matrix_only"
    assert report["product_complete"] is False
    assert report["denominator_defined"] is False
    assert report["summary"]["source_count"] == 3 + 2 + 2
    assert report["summary"]["reviewed_contract_count"] == 1
    kinds = {s["source_id"]: s for s in report["sources"]}
    assert len(kinds) == 7
    # Source ids are ordinal-based public identities, never locator tokens.
    assert "snap-portal:domestic:11" in kinds
    assert "snap-sdkref:sdk_reference:0" in kinds
    assert "snap-sdkref:sdk_reference:1" in kinds
    assert "snap-assets:asset:0" in kinds
    # Missing body stays blocked; present-but-unreviewed stays needs_review.
    missing = next(s for s in report["sources"] if s.get("doc_id") == "11")
    assert missing["disposition"] == "blocked_source"
    assert missing["canonical_document_url"] == "https://open.ys7.com/help/11"
    ref_ok = next(
        s for s in report["sources"] if s["kind"] == "sdk_reference" and s["status"] == "ok"
    )
    assert ref_ok["disposition"] == "needs_review"
    assert ref_ok["discovery_reference_count"] == 1
    excluded = next(
        s
        for s in report["sources"]
        if s["kind"] == "asset" and s["status"] == "excluded_sdk_binary"
    )
    assert excluded["disposition"] == "blocked_source"
    asset_ok = next(s for s in report["sources"] if s["kind"] == "asset" and s["status"] == "ok")
    assert asset_ok["asset_category"] == "document"
    assert asset_ok["reference_count"] == 1
    assert asset_ok["references"] == [
        {
            "reference_index": 0,
            "reference_type": "link",
            "evidence_ref": "snap-assets/manifest.jsonl#record/0/references/0",
        }
    ]
    # Capability of the reviewed contract keeps binding evidence layering.
    cap = next(c for c in report["capabilities"] if c["capability_id"] == "contract:cn.demo.op")
    assert cap["implementation_status"] == "ast_verified"
    assert cap["offline_status"] == "passed"
    assert cap["offline_evidence"] == "recorded_evidence"
    assert cap["live_status"] == "not_verified"
    assert cap["live_evidence"] is None
    assert cap["path"] == "/api/lapp/demo/op"
    assert cap["canonical_document_url"] == "https://open.ys7.com/help/10"
    # No percentage/total claims anywhere.
    payload = json.dumps(report)
    assert "percent" not in payload and "coverage" not in report["summary"]
    assert report["summary"]["counts_are_not_api_totals"] is True
    _assert_public_schema(report)


def test_same_route_is_never_exact_and_duplicate_attribution(tmp_path, monkeypatch):
    """same_route hints stay hints; a 2nd snapshot of same doc adds no ops."""
    contract = _contract()
    catalog = [_catalog_row()]
    # Declaration deliberately carries a wrong reviewed id plus a same-route
    # hint; the matrix must recompute the exact join itself.
    decl = _declaration(reviewed=["zzz.bogus"], same_route=["cn.demo.op", "zzz.bogus"])
    docs = [_inventory_doc(declarations=[decl])]
    snap1 = _portal_fixture(tmp_path, catalog, docs)
    # A second snapshot re-declaring the same document identity.
    snap2 = dict(snap1)
    snap2["id"] = "snap-portal-dup"
    report = _build(tmp_path, monkeypatch, [snap1, snap2], contracts=[contract])

    sources = [s for s in report["sources"] if s["kind"] == "portal_document"]
    assert len(sources) == 2
    # Both snapshots independently join the same reviewed contract.
    assert all(s["operation_ids"] == ["cn.demo.op"] for s in sources)
    # Denominator never inflates: still exactly one reviewed contract.
    assert report["summary"]["reviewed_contract_count"] == 1
    assert sum(1 for c in report["capabilities"] if not c["provisional"]) == 1
    for obs in report["observations"]:
        if obs["kind"] == "declaration":
            assert obs["exact_operation_ids"] == ["cn.demo.op"]
            # Hints are limited to ids present in the reviewed registry;
            # the fabricated zzz.bogus id is filtered out, never published.
            assert obs["same_route_hints"] == ["cn.demo.op"]
    _assert_public_schema(report)


def test_same_route_never_promoted_to_exact_match(tmp_path, monkeypatch):
    """A declaration sharing only method+path on a different doc must not link."""
    contract = _contract(doc_id="99", url="https://open.ys7.com/help/99", sha=SHA_D)
    catalog = [_catalog_row(doc_id="10", url="https://open.ys7.com/help/10", sha=SHA_A)]
    decl = _declaration(same_route=["cn.demo.op"])
    docs = [_inventory_doc(declarations=[decl])]
    portal = _portal_fixture(tmp_path, catalog, docs)
    report = _build(tmp_path, monkeypatch, [portal], contracts=[contract])
    obs = next(o for o in report["observations"] if o["kind"] == "declaration")
    assert obs["exact_operation_ids"] == []
    source = report["sources"][0]
    assert source["operation_ids"] == []
    cap = next(c for c in report["capabilities"] if c["capability_id"] == "contract:cn.demo.op")
    assert "contract_source_document_absent" in cap["source_review_issues"]
    _assert_public_schema(report)


def test_no_declaration_sdk_reference_and_assets_still_disposed(tmp_path, monkeypatch):
    """Docs with zero observations, sdk refs and assets still get dispositions."""
    catalog = [_catalog_row()]
    docs = [_inventory_doc(declarations=[], examples=[])]
    portal = _portal_fixture(tmp_path, catalog, docs)
    sdkref = _reference_fixture(
        tmp_path,
        [
            {
                "url": "https://open.ys7.com/doc/zh/sdk/s.html",
                "status": "ok",
                "local_path": "s.html",
                "sha256": _sha(b"<html>s</html>"),
                "discovered_from": ["seed"],
            },
        ],
        files={"s.html": b"<html>s</html>"},
    )
    report = _build(tmp_path, monkeypatch, [portal, sdkref])
    kinds = {c["capability_kind"] for c in report["capabilities"]}
    assert "unobserved_source" in kinds
    assert "provisional_observation" not in kinds
    review_caps = [c for c in report["capabilities"] if c["capability_kind"] == "unobserved_source"]
    assert len(review_caps) == 2  # the portal doc + the sdk_reference page
    assert all(c["linked_observation_ids"] == [] for c in review_caps)
    _assert_public_schema(report)


def test_sensitive_query_and_curl_values_never_emitted(tmp_path, monkeypatch):
    catalog = [_catalog_row()]
    decl = _declaration(query="accessToken=abc123&secret=zzz")
    ex = _example()
    docs = [_inventory_doc(declarations=[decl], examples=[ex])]
    portal = _portal_fixture(tmp_path, catalog, docs)
    report = _build(tmp_path, monkeypatch, [portal])
    payload = json.dumps(report, ensure_ascii=False)
    assert "abc123" not in payload
    assert "SECRET" not in payload
    assert "appKey=hidden" not in payload
    assert "curl -X" not in payload
    for obs in report["observations"]:
        assert "query" not in obs and "raw_address" not in obs
        assert "evidence" not in obs
    assert any(o["has_query"] for o in report["observations"])
    _assert_public_schema(report)


def test_contract_kept_when_declaration_not_recovered(tmp_path, monkeypatch):
    """Existing implementations stay even when the declaration is absent."""
    catalog = [_catalog_row()]  # doc exists, but no matching declaration row
    docs = [_inventory_doc(declarations=[], examples=[])]
    portal = _portal_fixture(tmp_path, catalog, docs)
    report = _build(tmp_path, monkeypatch, [portal])
    cap = next(c for c in report["capabilities"] if c["capability_id"] == "contract:cn.demo.op")
    assert cap["implementation_status"] == "ast_verified"
    assert "contract_declaration_not_recovered" in cap["source_review_issues"]
    assert any(u.get("operation_id") == "cn.demo.op" for u in report["unresolved"])
    _assert_public_schema(report)


def test_known_exceptions_remain_flagged(tmp_path, monkeypatch):
    """The two recorded declaration-recovery exceptions stay visible."""
    contracts = [
        _contract(
            "global.playback.invalidate",
            doc_id="534",
            region="overseas",
            url="https://open.ys7.com/help/en/534",
            sha=SHA_C,
            path="/api/lapp/live/address/disable",
        ),
        _contract(
            "global.security.update_password",
            doc_id="2868",
            region="overseas",
            url="https://open.ys7.com/help/en/2868",
            sha=SHA_D,
            path="/api/lapp/device/password/update",
        ),
    ]
    bindings = [
        _binding("global.playback.invalidate", doc_id="534", region="overseas"),
        _binding("global.security.update_password", doc_id="2868", region="overseas"),
    ]
    catalog = [
        _catalog_row(
            doc_id="534",
            region="overseas",
            url="https://open.ys7.com/help/en/534",
            sha=SHA_C,
            raw_file="pages/overseas/534.source.json",
        ),
        _catalog_row(
            doc_id="2868",
            region="overseas",
            url="https://open.ys7.com/help/en/2868",
            sha=SHA_D,
            raw_file="pages/overseas/2868.source.json",
        ),
    ]
    docs = [
        _inventory_doc(
            doc_id="534",
            region="overseas",
            url="https://open.ys7.com/help/en/534",
            sha=SHA_C,
            declarations=[],
            examples=[],
        ),
        _inventory_doc(
            doc_id="2868",
            region="overseas",
            url="https://open.ys7.com/help/en/2868",
            sha=SHA_D,
            declarations=[],
            examples=[],
        ),
    ]
    portal = _portal_fixture(tmp_path, catalog, docs)
    report = _build(tmp_path, monkeypatch, [portal], contracts=contracts, bindings=bindings)
    flagged = {
        c["operation_id"]
        for c in report["capabilities"]
        if "contract_declaration_not_recovered" in c["source_review_issues"]
    }
    assert flagged == {"global.playback.invalidate", "global.security.update_password"}
    assert report["summary"]["unresolved_reviewed_contracts"] == 2
    overseas = {
        c["source_doc_id"]: c["canonical_document_url"]
        for c in report["capabilities"]
        if c["capability_kind"] == "reviewed_contract"
    }
    assert overseas["534"] == "https://open.ys7.com/help/en/534"
    _assert_public_schema(report)


def test_manifest_sha_mismatch_rejected(tmp_path, monkeypatch):
    catalog = [_catalog_row()]
    portal = _portal_fixture(tmp_path, catalog, [])
    portal["manifest_sha256"] = "0" * 64
    manifest = _manifest(tmp_path, [portal])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest)


def test_reference_local_path_escape_rejected(tmp_path, monkeypatch):
    outside = tmp_path / "outside.html"
    outside.write_bytes(b"<html>out</html>")
    entries = [
        {
            "url": "https://open.ys7.com/doc/zh/sdk/x.html",
            "status": "ok",
            "local_path": "../outside.html",
            "sha256": _sha(b"<html>out</html>"),
            "discovered_from": ["seed"],
        },
    ]
    sdkref = _reference_fixture(tmp_path, entries)
    manifest = _manifest(tmp_path, [sdkref])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest)


def test_duplicate_json_keys_rejected(tmp_path, monkeypatch):
    root = tmp_path / "portal-snap"
    root.mkdir()
    raw = (
        b'[{"region":"domestic","region":"overseas","doc_id":"1","source_url":"u","status":"archived","content_sha256":"'
        + SHA_A.encode()
        + b'"}]'
    )
    _write(root / "catalog.json", raw)
    snap = _snapshot("snap-portal", "portal", root, raw)
    manifest = _manifest(tmp_path, [snap])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest)


def test_nan_and_invalid_json_rejected(tmp_path, monkeypatch):
    root = tmp_path / "portal-snap"
    root.mkdir()
    raw = b'[{"region":NaN}]'
    _write(root / "catalog.json", raw)
    snap = _snapshot("snap-portal", "portal", root, raw)
    manifest = _manifest(tmp_path, [snap])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest)


def _decision(
    source_id, pin, disposition="implement", reason="reviewed", refs=("docs/x",), capability_id=None
):
    row = {
        "source_id": source_id,
        "content_sha256": pin,
        "disposition": disposition,
        "reason": reason,
        "evidence_refs": list(refs),
    }
    if capability_id is not None:
        row["capability_id"] = capability_id
    return row


def test_decisions_applied_and_stale_pin_rejected(tmp_path, monkeypatch):
    catalog = [_catalog_row()]
    portal = _portal_fixture(tmp_path, catalog, [_inventory_doc()])
    manifest = _manifest(tmp_path, [portal])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    report = mod.build_matrix(repo, manifest)
    source_id = report["sources"][0]["source_id"]

    decisions = tmp_path / "decisions.json"
    _write(
        decisions,
        {
            "schema_version": 1,
            "decisions": [
                _decision(
                    source_id,
                    SHA_A,
                    disposition="not_an_api",
                    reason="guide page",
                    refs=["evidence/1"],
                ),
            ],
        },
    )
    decided = mod.build_matrix(repo, manifest, decisions)
    assert decided["sources"][0]["disposition"] == "not_an_api"
    assert decided["summary"]["decisions_applied"] == 1
    _assert_public_schema(decided)

    stale = tmp_path / "stale.json"
    _write(
        stale,
        {
            "schema_version": 1,
            "decisions": [
                _decision(source_id, SHA_B, disposition="implement"),
            ],
        },
    )
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest, stale)

    unknown = tmp_path / "unknown.json"
    _write(
        unknown,
        {
            "schema_version": 1,
            "decisions": [
                _decision("snap-portal:domestic:nope", SHA_A),
            ],
        },
    )
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest, unknown)

    duplicate = tmp_path / "dup.json"
    row = _decision(source_id, SHA_A)
    _write(duplicate, {"schema_version": 1, "decisions": [row, dict(row)]})
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest, duplicate)


def test_decision_reference_is_a_pointer_not_free_text(tmp_path, monkeypatch):
    """schema 2 publishes only a traceable pointer for each decision.

    The reviewer reason text and evidence locator strings stay in the
    decisions file; the report carries the decisions input hash, the row
    ordinal, a reason presence flag, and the ref count — enough to locate
    the rationale without republishing it.
    """
    secret_reason = "guide page mentioning device {{DEVICE_d3m0id}} and token {{TOKEN_aa99}}"
    catalog = [_catalog_row()]
    portal = _portal_fixture(tmp_path, catalog, [_inventory_doc()])
    manifest = _manifest(tmp_path, [portal])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    report = mod.build_matrix(repo, manifest)
    source_id = report["sources"][0]["source_id"]

    decisions = tmp_path / "decisions.json"
    decisions_bytes = _write(
        decisions,
        {
            "schema_version": 1,
            "decisions": [
                _decision(
                    source_id,
                    SHA_A,
                    disposition="not_an_api",
                    reason=secret_reason,
                    refs=["evidence/1", "evidence/2"],
                ),
            ],
        },
    )
    decided = mod.build_matrix(repo, manifest, decisions)
    source = decided["sources"][0]
    assert source["disposition"] == "not_an_api"
    assert "decision_reason" not in source
    assert "decision_evidence_refs" not in source
    ref = source["decision_reference"]
    assert ref["decisions_sha256"] == _sha(decisions_bytes)
    assert ref["record_index"] == 0
    assert ref["reason_present"] is True
    assert ref["evidence_ref_count"] == 2
    payload = json.dumps(decided, ensure_ascii=False)
    assert "DEVICE_d3m0id" not in payload and "evidence/1" not in payload
    _assert_public_schema(decided)


def test_decision_capability_ref_visibility(tmp_path, monkeypatch):
    """A decision capability_id publishes only when it names a public
    capability; any other value becomes a bare has_external_capability_ref."""
    catalog = [_catalog_row()]
    portal = _portal_fixture(tmp_path, catalog, [_inventory_doc()])
    manifest = _manifest(tmp_path, [portal])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    report = mod.build_matrix(repo, manifest)
    source_id = report["sources"][0]["source_id"]

    visible = tmp_path / "visible.json"
    _write(
        visible,
        {
            "schema_version": 1,
            "decisions": [
                _decision(source_id, SHA_A, capability_id="contract:cn.demo.op"),
            ],
        },
    )
    decided = mod.build_matrix(repo, manifest, visible)
    source = decided["sources"][0]
    assert source["decision_capability_id"] == "contract:cn.demo.op"
    assert "has_external_capability_ref" not in source
    _assert_public_schema(decided)

    external = tmp_path / "external.json"
    _write(
        external,
        {
            "schema_version": 1,
            "decisions": [
                _decision(source_id, SHA_A, capability_id="schema1:domestic:10:legacy"),
            ],
        },
    )
    decided2 = mod.build_matrix(repo, manifest, external)
    source2 = decided2["sources"][0]
    assert "decision_capability_id" not in source2
    assert source2["has_external_capability_ref"] is True
    payload = json.dumps(decided2, ensure_ascii=False)
    assert "schema1:domestic:10:legacy" not in payload
    _assert_public_schema(decided2)


def test_decision_reason_and_refs_required(tmp_path, monkeypatch):
    catalog = [_catalog_row()]
    portal = _portal_fixture(tmp_path, catalog, [_inventory_doc()])
    manifest = _manifest(tmp_path, [portal])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    report = mod.build_matrix(repo, manifest)
    source_id = report["sources"][0]["source_id"]
    for bad in (
        _decision(source_id, SHA_A, reason=""),
        _decision(source_id, SHA_A, refs=()),
        _decision(source_id, SHA_A, disposition="shipit"),
    ):
        path = tmp_path / "bad-decisions.json"
        _write(path, {"schema_version": 1, "decisions": [bad]})
        with pytest.raises(module().ProductMatrixError):
            mod.build_matrix(repo, manifest, path)


def test_error_messages_never_echo_raw_content(tmp_path, monkeypatch, capsys):
    mod = module()
    repo = tmp_path / "repo"
    _registry_files(repo, [_contract()], [_binding()])
    secret = "PRIVATE-TOKEN-9f8e7d"
    root = tmp_path / "portal-snap"
    root.mkdir()
    raw = json.dumps([{"region": secret}]).encode()
    _write(root / "catalog.json", raw)
    snap = _snapshot("snap-portal", "portal", root, raw)
    manifest = _manifest(tmp_path, [snap])
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    with pytest.raises(module().ProductMatrixError) as excinfo:
        mod.build_matrix(repo, manifest)
    assert secret not in str(excinfo.value)
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest)])
    captured = capsys.readouterr()
    assert rc == 2 and secret not in captured.err and secret not in captured.out


def test_cli_writes_output_without_overwriting_inputs(tmp_path, monkeypatch, capsys):
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    portal = _portal_fixture(tmp_path, [_catalog_row()], [_inventory_doc()])
    manifest = _manifest(tmp_path, [portal])
    out = tmp_path / "report.json"
    rc = mod.main(
        [
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["evidence"] == "source_and_registry_matrix_only"
    assert report["schema_version"] == 2
    # Inputs unchanged.
    before = json.loads(manifest.read_text())
    assert before["schema_version"] == 1
    # --output must refuse to overwrite the manifest input.
    rc = mod.main(
        [
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--output",
            str(manifest),
        ]
    )
    assert rc == 2
    assert json.loads(manifest.read_text()) == before
    # --summary-only keeps only the fixed summary keys.
    rc = mod.main(
        [
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--summary-only",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert "sources" not in summary and "observations" not in summary
    assert summary["product_complete"] is False
    assert summary["publication_profile"] == "reviewed_metadata_only"


def test_build_matrix_no_decisions_still_produces_pending_matrix(tmp_path, monkeypatch):
    portal = _portal_fixture(tmp_path, [_catalog_row()], [_inventory_doc()])
    repo = _repo(tmp_path)
    mod = module()
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    manifest = _manifest(tmp_path, [portal])
    report = mod.build_matrix(repo, manifest, None)
    assert report["summary"]["decisions_applied"] == 0
    assert report["sources"][0]["disposition"] == "needs_review"
    _assert_public_schema(report)


def test_missing_manifest_inventory_and_dir_rejected(tmp_path, monkeypatch):
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, tmp_path / "nope.json")
    # inventory path present without sha must be rejected
    root = tmp_path / "portal-snap"
    root.mkdir()
    manifest_bytes = _write(root / "catalog.json", [_catalog_row()])
    snap = _snapshot("s", "portal", root, manifest_bytes)
    snap["inventory"] = str(tmp_path / "inv.json")
    manifest = _manifest(tmp_path, [snap])
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest)
    # relative snapshot root rejected
    snap = _snapshot("s2", "portal", root, manifest_bytes)
    snap["root"] = "relative/path"
    manifest = _manifest(tmp_path, [snap])
    with pytest.raises(module().ProductMatrixError):
        mod.build_matrix(repo, manifest)


def test_multiple_observations_on_one_page_not_swallowed(tmp_path, monkeypatch):
    """One implemented declaration must not absorb the page's other items."""
    catalog = [_catalog_row()]
    decls = [
        _declaration(ordinal=0, path="/api/lapp/demo/op"),
        _declaration(
            ordinal=1, path="/api/lapp/unknown/ep", raw="https://open.ys7.com/api/lapp/unknown/ep"
        ),
        _declaration(
            ordinal=2,
            status="address_only",
            method=None,
            path="/api/lapp/only/addr",
            raw="https://open.ys7.com/api/lapp/only/addr",
            reasons=["missing_method"],
        ),
    ]
    docs = [_inventory_doc(declarations=decls)]
    portal = _portal_fixture(tmp_path, catalog, docs)
    report = _build(tmp_path, monkeypatch, [portal])
    provisional = [
        c for c in report["capabilities"] if c["capability_kind"] == "provisional_observation"
    ]
    # The two unregistered declarations plus the unregistered example each
    # keep their own review-visible provisional capability.
    assert len(provisional) == 3
    assert all(c["implementation_status"] == "missing" for c in provisional)
    assert all(c["operation_id"] is None for c in provisional)
    matched = [o for o in report["observations"] if o["exact_operation_ids"]]
    assert len(matched) == 1
    _assert_public_schema(report)


def test_inputs_are_not_mutated(tmp_path, monkeypatch):
    catalog = [_catalog_row()]
    docs = [_inventory_doc()]
    portal = _portal_fixture(tmp_path, catalog, docs)
    repo = _repo(tmp_path)
    mod = module()
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    manifest = _manifest(tmp_path, [portal])
    before = (tmp_path / "portal-snap" / "catalog.json").read_bytes()
    inv_before = (tmp_path / "inventory.json").read_bytes()
    repo_before = (repo / "contracts" / "reviewed-transport-contracts.json").read_bytes()
    mod.build_matrix(repo, manifest)
    assert (tmp_path / "portal-snap" / "catalog.json").read_bytes() == before
    assert (tmp_path / "inventory.json").read_bytes() == inv_before
    assert (repo / "contracts" / "reviewed-transport-contracts.json").read_bytes() == repo_before


def test_inventory_only_document_kept_as_blocked(tmp_path, monkeypatch):
    """An inventory doc absent from the catalog stays visible, not dropped."""
    catalog = [_catalog_row()]
    docs = [
        _inventory_doc(),
        _inventory_doc(doc_id="77", url="https://open.ys7.com/help/77", sha=SHA_D),
    ]
    portal = _portal_fixture(tmp_path, catalog, docs)
    report = _build(tmp_path, monkeypatch, [portal])
    extra = next(s for s in report["sources"] if s.get("doc_id") == "77")
    assert extra["status"] == "inventory_only"
    assert extra["disposition"] == "blocked_source"
    assert "inventory_document_not_in_catalog" in extra["unresolved"]
    assert extra["record_index"] == 1
    assert extra["evidence_refs"] == ["snap-portal/inventory#record/1"]
    # The inventory recorded category paths, but they were never confirmed
    # by the catalog: the public slot stays empty.
    assert extra["category_paths"] == []
    # Its observations are preserved too.
    assert any(
        o["source_id"] == extra["source_id"] and o["kind"] == "declaration"
        for o in report["observations"]
    )
    _assert_public_schema(report)


def test_output_inside_snapshot_root_rejected(tmp_path, monkeypatch, capsys):
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    portal = _portal_fixture(tmp_path, [_catalog_row()], [_inventory_doc()])
    manifest = _manifest(tmp_path, [portal])
    out = tmp_path / "portal-snap" / "report.json"
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest), "--output", str(out)])
    captured = capsys.readouterr()
    assert rc == 2 and not out.exists()
    assert "snapshot root" in captured.err


def test_cli_output_refuses_every_existing_path(tmp_path, monkeypatch, capsys):
    """--output must refuse any pre-existing path, not just known inputs."""
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    portal = _portal_fixture(tmp_path, [_catalog_row()], [_inventory_doc()])
    manifest = _manifest(tmp_path, [portal])

    existing_report = tmp_path / "report.json"
    existing_report.write_text("{}")
    rc = mod.main(
        ["--repo", str(repo), "--manifest", str(manifest), "--output", str(existing_report)]
    )
    assert rc == 2 and existing_report.read_text() == "{}"

    # A registry contract input path is an input file and must be refused.
    contracts = repo / "contracts" / "reviewed-transport-contracts.json"
    before = contracts.read_bytes()
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest), "--output", str(contracts)])
    assert rc == 2 and contracts.read_bytes() == before

    # A hardlink to the manifest is still an existing path -> refused.
    hardlink = tmp_path / "hardlink.json"
    os.link(manifest, hardlink)
    manifest_before = manifest.read_bytes()
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest), "--output", str(hardlink)])
    assert rc == 2 and manifest.read_bytes() == manifest_before

    # A symlink pointing at the manifest resolves to an existing file.
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(manifest)
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest), "--output", str(symlink)])
    assert rc == 2 and manifest.read_bytes() == manifest_before

    # A dangling symlink is still an existing path (lexists) -> refused.
    dangling = tmp_path / "dangling.json"
    dangling.symlink_to(tmp_path / "nonexistent-target")
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest), "--output", str(dangling)])
    assert rc == 2 and dangling.is_symlink()

    # An SDK source file path must also be refused.
    sdk_src = repo / "src" / "demo.py"
    sdk_src.parent.mkdir(parents=True, exist_ok=True)
    sdk_src.write_text("# sdk source\n")
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest), "--output", str(sdk_src)])
    assert rc == 2 and sdk_src.read_text() == "# sdk source\n"

    # A brand-new path still succeeds.
    fresh = tmp_path / "out" / "matrix.json"
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest), "--output", str(fresh)])
    assert rc == 0 and json.loads(fresh.read_text())["evidence"]


def test_output_protection_independent_of_build_failure(tmp_path, monkeypatch):
    """A failing build must not be mistaken for output-path protection."""
    mod = module()
    repo = _repo(tmp_path)
    # Do NOT stub the audit -> audit_repository fails on the synthetic repo,
    # so the run aborts before --output handling. That is a build failure,
    # not file protection; the check belongs to a passing build.
    portal = _portal_fixture(tmp_path, [_catalog_row()], [_inventory_doc()])
    manifest = _manifest(tmp_path, [portal])
    out = tmp_path / "report.json"
    rc = mod.main(["--repo", str(repo), "--manifest", str(manifest), "--output", str(out)])
    assert rc == 2 and not out.exists()


def test_inventory_identity_mismatch_blocks_exact_join(tmp_path, monkeypatch):
    """A shifted inventory URL/SHA must not graft catalog identity onto obs."""
    catalog = [_catalog_row()]
    # Inventory claims a different URL and content hash than the catalog pin.
    docs = [_inventory_doc(url="https://open.ys7.com/help/10-older", sha=SHA_D)]
    portal = _portal_fixture(tmp_path, catalog, docs)
    report = _build(tmp_path, monkeypatch, [portal])
    source = report["sources"][0]
    assert "inventory_identity_mismatch" in source["unresolved"]
    assert source["operation_ids"] == []
    decl = next(o for o in report["observations"] if o["kind"] == "declaration")
    assert decl["exact_operation_ids"] == []
    # The observation's own inventory identity is never published, but its
    # content pin stays as an integrity binding.
    assert "inventory_url" not in decl
    assert decl["inventory_content_sha256"] == SHA_D
    _assert_public_schema(report)


def test_missing_body_blocks_exact_join(tmp_path, monkeypatch):
    """An unverifiable body (no raw_file) must not earn exact matches."""
    catalog = [_catalog_row(status="source_missing", sha=None, raw_file=None, raw_sha=None)]
    docs = [_inventory_doc()]
    portal = _portal_fixture(tmp_path, catalog, docs)
    report = _build(tmp_path, monkeypatch, [portal])
    source = report["sources"][0]
    assert source["disposition"] == "blocked_source"
    decl = next(o for o in report["observations"] if o["kind"] == "declaration")
    assert decl["exact_operation_ids"] == []
    _assert_public_schema(report)


def test_inventory_only_never_exact(tmp_path, monkeypatch):
    """inventory_only observations cannot receive exact proof even on a
    contract-matching route."""
    catalog = [_catalog_row(doc_id="10", url="https://open.ys7.com/help/10")]
    docs = [
        _inventory_doc(),
        _inventory_doc(doc_id="99", url="https://open.ys7.com/help/99", sha=SHA_D),
    ]
    portal = _portal_fixture(tmp_path, catalog, docs)
    # Contract pinned to the inventory_only doc identity.
    contract = _contract(doc_id="99", url="https://open.ys7.com/help/99", sha=SHA_D)
    report = _build(tmp_path, monkeypatch, [portal], contracts=[contract])
    extra = next(s for s in report["sources"] if s.get("doc_id") == "99")
    decl = next(
        o
        for o in report["observations"]
        if o["source_id"] == extra["source_id"] and o["kind"] == "declaration"
    )
    assert decl["exact_operation_ids"] == []
    assert extra["operation_ids"] == []
    _assert_public_schema(report)


def test_asset_hash_mismatch_rejected(tmp_path, monkeypatch):
    """A present-but-tampered asset file is a hard error, not blocked_source."""
    assets = _assets_fixture(
        tmp_path,
        [
            {
                "asset_id": "aa",
                "url": "https://img.ys7.com/x.pdf",
                "status": "ok",
                "file": "blobs/aa.bin",
                "sha256": _sha(b"expected-bytes"),
            }
        ],
        files={"blobs/aa.bin": b"tampered-bytes"},
    )
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    manifest = _manifest(tmp_path, [assets])
    with pytest.raises(mod.ProductMatrixError):
        mod.build_matrix(repo, manifest)


def test_decision_does_not_hide_unresolved_obs(tmp_path, monkeypatch):
    """A page-level decision must not hide an unresolved provisional
    observation on the same page, and no decision free text is republished."""
    catalog = [_catalog_row()]
    decls = [
        _declaration(ordinal=0, path="/api/lapp/demo/op"),
        _declaration(
            ordinal=1, path="/api/lapp/unknown/ep", raw="https://open.ys7.com/api/lapp/unknown/ep"
        ),
    ]
    docs = [_inventory_doc(declarations=decls)]
    portal = _portal_fixture(tmp_path, catalog, docs)
    manifest = _manifest(tmp_path, [portal])
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    report = mod.build_matrix(repo, manifest)
    source_id = report["sources"][0]["source_id"]

    decisions = tmp_path / "decisions.json"
    _write(
        decisions,
        {
            "schema_version": 1,
            "decisions": [
                _decision(
                    source_id,
                    SHA_A,
                    disposition="not_an_api",
                    reason="guide page, not a transport API",
                    refs=["contracts/reviewed-transport-contracts.json"],
                )
            ],
        },
    )
    decided = mod.build_matrix(repo, manifest, decisions)
    source = decided["sources"][0]
    assert source["disposition"] == "not_an_api"
    assert source["decision_reference"]["reason_present"] is True
    assert source["decision_reference"]["evidence_ref_count"] == 1
    # The unknown observation stays an unresolved review item.
    obs_unresolved = [u for u in decided["unresolved"] if u["kind"] == "observation"]
    assert obs_unresolved, "unreviewed observation vanished after page decision"
    assert decided["summary"]["unresolved_count"] > 0
    provisional = [
        c for c in decided["capabilities"] if c["capability_kind"] == "provisional_observation"
    ]
    assert provisional and all(c["disposition"] == "needs_review" for c in provisional)
    # Reviewer free text stays in the decisions file only: no decision slot
    # on the report carries the reason or the ref list.
    payload = json.dumps(decided, ensure_ascii=False)
    assert "guide page" not in payload
    assert "decision_reason" not in payload and "decision_evidence_refs" not in payload
    _assert_public_schema(decided)


def test_malformed_or_duplicate_observation_hard_fails(tmp_path, monkeypatch):
    """Invalid/duplicate observations fail loudly instead of dropping rows."""
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    # Malformed observation (non-dict).
    catalog = [_catalog_row()]
    docs = [_inventory_doc(declarations=[{"ordinal": 0, "status": 5}])]
    portal = _portal_fixture(tmp_path, catalog, docs)
    manifest = _manifest(tmp_path, [portal])
    with pytest.raises(mod.ProductMatrixError):
        mod.build_matrix(repo, manifest)
    # Duplicate (kind, ordinal).
    dup = _declaration(ordinal=0)
    docs = [_inventory_doc(declarations=[dup, dict(dup)])]
    portal = _portal_fixture(tmp_path, catalog, docs)
    manifest = _manifest(tmp_path, [portal])
    with pytest.raises(mod.ProductMatrixError):
        mod.build_matrix(repo, manifest)


def test_unknown_observation_fields_safely_ignored(tmp_path, monkeypatch):
    """A future parser field never drops a valid observation — and the
    unprojected value can neither reach the report nor buy an exact match."""
    module()
    _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    canary = "{{CANARY_f3i3l3d5}}"
    decl = _declaration()
    # Fabricated parser additions: an op-looking string, a credentialed URL
    # and a fabricated reverse relation — none are real schema, all must be
    # ignored rather than fatal or projected.
    decl["future_parser_field"] = canary
    decl["reviewed_operation_ids"] = ["zzz.bogus", "cn.demo.op"]
    decl["related_example_ordinals"] = [0]
    decl["evidence"][0]["future_role_text"] = canary
    decl["provisional_url"] = f"https://evil.example/{canary}?leak=yes"
    example = _example()
    example["future_example_field"] = canary
    docs = [_inventory_doc(declarations=[decl], examples=[example])]
    # Inventory warning/doc-level extra fields are likewise ignored.
    docs[0]["warnings"] = [{"code": "new_warning", "element_index": 3, "note": canary}]
    docs[0]["future_doc_field"] = canary
    portal = _portal_fixture(tmp_path, [_catalog_row()], docs)
    report = _build(tmp_path, monkeypatch, [portal], contracts=[_contract()])
    payload = json.dumps(report, ensure_ascii=False)
    assert canary not in payload
    # The whole observation row is preserved via its whitelist projection —
    # unknown fields never dropped the row.
    decl_obs = next(o for o in report["observations"] if o["kind"] == "declaration")
    ex_obs = next(o for o in report["observations"] if o["kind"] == "example")
    for obs in (decl_obs, ex_obs):
        assert "future_parser_field" not in obs
        assert "future_example_field" not in obs
        assert "provisional_url" not in obs
        assert "related_example_ordinals" not in obs
        assert "reviewed_operation_ids" not in obs
        assert "future_role_text" not in json.dumps(obs)
    # The real six-field join still recovered the contract — unknown fields
    # neither blocked it nor were needed; the forged reviewed ids only
    # matched because method+path genuinely agree, never by attribution.
    assert decl_obs["exact_operation_ids"] == ["cn.demo.op"]
    # The warning code text is reduced to the fixed marker plus its numeric
    # element pointer; "new_warning" itself never publishes.
    warning_entries = [u for u in report["unresolved"] if u["reason"] == "inventory_warning"]
    assert warning_entries == [
        {
            "kind": "source",
            "source_id": report["sources"][0]["source_id"],
            "reason": "inventory_warning",
            "element_index": 3,
        }
    ]
    assert "new_warning" not in payload
    # An unknown field can NOT promote a non-matching declaration: the
    # unrelated doc's declaration keeps forged op ids yet stays unmatched.
    other = _declaration(
        ordinal=0, path="/api/lapp/other/ep", raw="https://open.ys7.com/api/lapp/other/ep"
    )
    other["reviewed_operation_ids"] = ["cn.demo.op"]
    other["future_parser_field"] = canary
    docs2 = [
        _inventory_doc(
            doc_id="11",
            url="https://open.ys7.com/help/11",
            sha=SHA_C,
            declarations=[other],
            examples=[],
        )
    ]
    portal2 = _portal_fixture(
        tmp_path,
        [
            _catalog_row(
                doc_id="11",
                url="https://open.ys7.com/help/11",
                sha=SHA_C,
                raw_file="pages/domestic/11.source.json",
            )
        ],
        docs2,
    )
    report2 = _build(tmp_path, monkeypatch, [portal2], contracts=[_contract()])
    obs2 = next(o for o in report2["observations"] if o["kind"] == "declaration")
    assert obs2["exact_operation_ids"] == []
    assert canary not in json.dumps(report2, ensure_ascii=False)
    _assert_public_schema(report2)


def test_recorded_locators_and_ids_never_republished(tmp_path, monkeypatch):
    """No recorded locator, id, or payload reaches the serialized report.

    Covers every public slot that schema 1 populated from raw input: portal
    catalog/inventory pins (with userinfo/query/fragment), sdk_reference
    url/discovered_from, asset url/references[].url, asset_id, and a signed
    reviewed-contract source_url pin. The report must contain none of those
    values — not even sanitized or hashed derivatives — while still keeping
    every source/observation/capability row and the same counts.
    """
    signed = "https://oss.example.com/a/b.pdf?OSSAccessKeyId={{CANARY_a1b2c3d4}}&Signature={{CANARY_e5f6a7b8}}#frag"
    proto_relative = "//img.ys7.com/share/att.pdf?token={{CANARY_p0q1r2s3}}#top"
    relative = "/doc/zh/sdk/ref/down.docx?fileKey={{CANARY_r4s5t6u7}}&Signature={{CANARY_u8v9w0x1}}"
    unsupported = "data:application/pdf;base64,{{CANARY_y2z3a4b5}}=="
    seed = "seed"
    assets = _assets_fixture(
        tmp_path,
        [
            {
                "asset_id": "{{ASSET_a1b2c3}}",
                "url": signed,
                "status": "ok",
                "file": "blobs/s.bin",
                "sha256": _sha(b"blob"),
                "references": [
                    {"url": signed, "reference_type": "link"},
                    {"url": proto_relative, "reference_type": "image"},
                    {"url": relative, "reference_type": "link"},
                    {"url": unsupported, "reference_type": "embedded_resource"},
                    {"url": seed, "reference_type": "weird_type"},
                ],
            },
            {
                "asset_id": "{{ASSET_d4e5f6}}",
                "url": unsupported,
                "status": "excluded_placeholder",
                "file": None,
                "sha256": None,
                "category": "image",
                "references": [],
            },
        ],
        files={"blobs/s.bin": b"blob"},
    )
    entries = [
        {
            "url": "https://open.ys7.com/doc/x.html?appKey={{CANARY_c9d0e1f2}}&fileKey=k",
            "status": "ok",
            "local_path": "x.html",
            "sha256": _sha(b"<html>x</html>"),
            "discovered_from": [signed, proto_relative, relative, unsupported, seed],
        }
    ]
    sdkref = _reference_fixture(tmp_path, entries, files={"x.html": b"<html>x</html>"})
    # Portal catalog + inventory pin carrying userinfo + query + fragment.
    portal_url = (
        "https://user:{{CANARY_u1s2e3r4}}@open.ys7.com/help/10?token={{CANARY_t5o6k7e8}}#sec"
    )
    catalog = [_catalog_row(url=portal_url)]
    docs = [_inventory_doc(url=portal_url)]
    portal = _portal_fixture(tmp_path, catalog, docs)
    # Reviewed contract pinned to a signed URL: the raw pin keeps the join
    # honest but the published capability carries only the constructed
    # canonical locator and the reviewed registry path.
    contract_url = "https://open.ys7.com/help/10?appKey={{CANARY_k9e0y1s2}}#frag"
    contract = _contract(url=contract_url)
    report = _build(tmp_path, monkeypatch, [assets, sdkref, portal], contracts=[contract])
    payload = json.dumps(report, ensure_ascii=False)
    for canary in (
        "CANARY_a1b2c3d4",
        "CANARY_e5f6a7b8",
        "CANARY_c9d0e1f2",
        "CANARY_p0q1r2s3",
        "CANARY_r4s5t6u7",
        "CANARY_u8v9w0x1",
        "CANARY_y2z3a4b5",
        "CANARY_u1s2e3r4",
        "CANARY_t5o6k7e8",
        "CANARY_k9e0y1s2",
        "ASSET_a1b2c3",
        "ASSET_d4e5f6",
    ):
        assert canary not in payload
    for marker in (
        "OSSAccessKeyId",
        "Signature=",
        "appKey=",
        "fileKey=",
        "token=",
        "#frag",
        "#sec",
        "#top",
        "base64",
        "user:",
        "x.html",
        "down.docx",
        "att.pdf",
        "b.pdf",
        "seed",
    ):
        assert marker not in payload
    # Rows are all preserved — omission is a boundary, not a drop.
    assert report["summary"]["source_count"] == 4
    asset = next(s for s in report["sources"] if s["source_id"] == "snap-assets:asset:0")
    assert asset["status"] == "ok"
    assert asset["reference_count"] == 5
    assert [r["reference_index"] for r in asset["references"]] == [0, 1, 2, 3, 4]
    assert [r["reference_type"] for r in asset["references"]] == [
        "link",
        "image",
        "link",
        "embedded_resource",
        "unknown",
    ]
    data_asset = next(s for s in report["sources"] if s["source_id"] == "snap-assets:asset:1")
    assert data_asset["status"] == "excluded_placeholder"
    assert data_asset["asset_category"] == "image"
    ref = next(s for s in report["sources"] if s["kind"] == "sdk_reference")
    assert ref["status"] == "ok"
    assert ref["discovery_reference_count"] == 5
    portal_source = next(s for s in report["sources"] if s["kind"] == "portal_document")
    assert portal_source["canonical_document_url"] == "https://open.ys7.com/help/10"
    obs = next(o for o in report["observations"] if o["kind"] == "declaration")
    assert obs["inventory_content_sha256"] == SHA_A
    cap = next(c for c in report["capabilities"] if c["capability_id"] == "contract:cn.demo.op")
    assert cap["canonical_document_url"] == "https://open.ys7.com/help/10"
    assert cap["path"] == "/api/lapp/demo/op"
    _assert_public_schema(report)


def test_long_recorded_reference_locator_still_counts(tmp_path, monkeypatch):
    """A recorded reference over _MAX_ID but under _MAX_TEXT stays counted.

    Real archived manifests carry data: embeds tens of kilobytes long; the
    raw-locator input bound is the same _MAX_TEXT the URL entry uses, not
    the identifier bound. The asset keeps its reference count and neither
    the payload nor a hash of it appears anywhere in the report.
    """
    canary = "{{CANARY_l0ngloc}}"
    long_data = f"data:image/png;base64,{canary}" + "A" * 9000
    assert 4096 < len(long_data) < 2 * 1024 * 1024
    ok_data = f"data:image/gif;base64,{canary}x"
    assets = _assets_fixture(
        tmp_path,
        [
            {
                "asset_id": "longref",
                "url": "https://img.ys7.com/z.pdf",
                "status": "ok",
                "file": "blobs/z.bin",
                "sha256": _sha(b"blob-z"),
                "category": "document",
                "references": [
                    {"url": long_data, "reference_type": "embedded_resource", "reference_line": 7},
                    {"url": "https://open.ys7.com/help/10?token=x", "reference_type": "link"},
                ],
            },
            {
                "asset_id": "ref2",
                "url": "https://img.ys7.com/w.pdf",
                "status": "ok",
                "file": None,
                "sha256": None,
                "references": [{"url": ok_data, "reference_type": "embedded_resource"}],
            },
        ],
        files={"blobs/z.bin": b"blob-z"},
    )
    report = _build(tmp_path, monkeypatch, [assets])
    source = next(s for s in report["sources"] if s["source_id"].endswith(":asset:0"))
    # Both references survive the projection; counts stay exact.
    assert source["reference_count"] == 2 and len(source["references"]) == 2
    assert source["references"][0]["reference_type"] == "embedded_resource"
    assert "reference_line" not in source["references"][0]
    # Public evidence of an available body: the archived file verified, its
    # sha pinned, the disposition kept needs_review and no body-unavailable
    # unresolved marker — body_available itself stays internal.
    assert source["status"] == "ok"
    assert source["content_sha256"] == _sha(b"blob-z")
    assert source["disposition"] == "needs_review"
    assert "asset_body_unavailable_or_excluded" not in source["unresolved"]
    payload = json.dumps(report, ensure_ascii=False)
    assert canary not in payload and "base64" not in payload
    # A payload-sized value is not anonymized by hashing it into the report.
    assert _sha(long_data.encode()) not in payload
    assert report["summary"]["source_count"] == 2
    assert report["summary"]["sources_by_kind"] == {"asset": 2}
    _assert_public_schema(report)


@pytest.mark.parametrize(
    "kind",
    ["over_limit", "non_string"],
    ids=["over-limit-locator", "non-string-locator"],
)
def test_rejected_reference_locator_fails_build(tmp_path, monkeypatch, kind):
    """Each refused reference locator fails the build on its own fixture.

    Every case owns an independent tmp_path/repo/manifest lifecycle so the
    ProductMatrixError can only come from the refused locator itself, never
    from a reused assets-snap root colliding with a previous fixture.
    """
    if kind == "over_limit":
        # Raw locator above the _MAX_TEXT input bound.
        bad_url = "data:image/png;base64," + "{{CANARY_t0oBig}}" + ("B" * (2 * 1024 * 1024 + 16))
    else:
        bad_url = 12345
    assets = _assets_fixture(
        tmp_path,
        [
            {
                "asset_id": "badref",
                "url": "https://img.ys7.com/b.pdf",
                "status": "ok",
                "references": [{"url": bad_url, "reference_type": "link"}],
            }
        ],
    )
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    manifest = _manifest(tmp_path, [assets])
    with pytest.raises(mod.ProductMatrixError):
        mod.build_matrix(repo, manifest)


def test_oversized_reference_text_label_is_just_a_count(tmp_path, monkeypatch):
    """A component-free reference label above _MAX_ID is no longer
    republished verbatim, so it stays an ordinary counted reference."""
    assets = _assets_fixture(
        tmp_path,
        [
            {
                "asset_id": "seeded",
                "url": "https://img.ys7.com/b.pdf",
                "status": "ok",
                "references": [{"url": "seed-" + "s" * 9000, "reference_type": "link"}],
            }
        ],
    )
    report = _build(tmp_path, monkeypatch, [assets])
    source = report["sources"][0]
    assert source["reference_count"] == 1
    payload = json.dumps(report, ensure_ascii=False)
    assert "seed-" not in payload
    _assert_public_schema(report)


def test_portal_identity_hard_fails_without_echo(tmp_path, monkeypatch):
    """A non-reviewed portal identity (bad region or non-numeric doc_id)
    fails the build; the offending value is never echoed."""
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    for row in (
        _catalog_row(doc_id="abc-def"),
        _catalog_row(region="{{REGION_xx99zz}}"),
        _catalog_row(doc_id="１２３"),  # non-ASCII digits are not portal identity
    ):
        root = tmp_path / f"psnap-{row['doc_id']}-{row['region']}"
        root.mkdir(exist_ok=True)
        manifest_bytes = _write(root / "catalog.json", [row])
        snap = _snapshot("snap-bad", "portal", root, manifest_bytes)
        manifest = _manifest(tmp_path, [snap])
        with pytest.raises(mod.ProductMatrixError) as excinfo:
            mod.build_matrix(repo, manifest)
        assert row["doc_id"] not in str(excinfo.value)


def test_canary_covers_every_public_slot(tmp_path, monkeypatch):
    """Synthetic canaries across path, host, title, URL parts, asset ids,
    discovered/reference locators, derived ids, evidence refs and summary
    keys — checked against the complete serialized report."""
    canary = "{{CANARY_x9y8z7w6}}"
    portal_url = f"https://{canary}.example.com/help/10?pin={canary}#frag"
    catalog = [_catalog_row(url=portal_url, title=f"title-{canary}")]
    decl = _declaration(
        raw=f"https://{canary}.example.com/api/x",
        host=f"{canary}.example.com",
        path=f"/api/{canary}/op",
        reasons=["templated_path"],
    )
    decl["section_title"] = f"section-{canary}"
    ex = _example(path=f"/api/{canary}/op")
    docs = [_inventory_doc(url=portal_url, declarations=[decl], examples=[ex])]
    portal = _portal_fixture(tmp_path, catalog, docs)
    sdkref = _reference_fixture(
        tmp_path,
        [
            {
                "url": f"https://{canary}.example.com/doc/x.html",
                "status": "ok",
                "local_path": "x.html",
                "sha256": _sha(b"<html>x</html>"),
                "discovered_from": [f"https://{canary}.example.com/seed"],
            }
        ],
        files={"x.html": b"<html>x</html>"},
    )
    assets = _assets_fixture(
        tmp_path,
        [
            {
                "asset_id": f"asset-{canary}",
                "url": f"https://{canary}.example.com/a.pdf",
                "status": "ok",
                "file": None,
                "sha256": None,
                "references": [
                    {"url": f"https://{canary}.example.com/ref", "reference_type": "link"},
                    {"reference_file": f"docs/{canary}.md", "reference_line": 4},
                ],
            }
        ],
    )
    report = _build(tmp_path, monkeypatch, [portal, sdkref, assets])
    payload = json.dumps(report, ensure_ascii=False)
    assert canary not in payload
    # No public slot may be built from the canary-bearing raw fields.
    for source in report["sources"]:
        assert canary not in json.dumps(source, ensure_ascii=False)
        assert all(canary not in ref for ref in source["evidence_refs"])
    for obs in report["observations"]:
        assert canary not in json.dumps(obs, ensure_ascii=False)
    for cap in report["capabilities"]:
        assert canary not in json.dumps(cap, ensure_ascii=False)
    # The provisional capability for the canary observation is derived only
    # from public source/observation ids.
    prov = [c for c in report["capabilities"] if c["capability_kind"] == "provisional_observation"]
    assert prov and all(canary not in c["capability_id"] for c in prov)
    _assert_public_schema(report)


def test_schema1_ids_cannot_guess_migration(tmp_path, monkeypatch):
    """Old locator-token source ids and non-portal schema-1 decision ids do
    not resolve: only the new ordinal public ids are valid references."""
    catalog = [_catalog_row()]
    docs = [_inventory_doc()]
    portal = _portal_fixture(tmp_path, catalog, docs)
    sdkref = _reference_fixture(
        tmp_path,
        [
            {
                "url": "https://open.ys7.com/doc/x.html",
                "status": "ok",
                "local_path": "x.html",
                "sha256": _sha(b"<html>x</html>"),
                "discovered_from": ["seed"],
            }
        ],
        files={"x.html": b"<html>x</html>"},
    )
    mod = module()
    repo = _repo(tmp_path)
    monkeypatch.setattr("harness.product_matrix.audit_repository", _audit_stub)
    manifest = _manifest(tmp_path, [portal, sdkref])
    report = mod.build_matrix(repo, manifest)
    assert "snap-sdkref:sdk_reference:0" in {s["source_id"] for s in report["sources"]}
    # A schema-1 style locator token is not a valid decision source_id.
    for legacy in (
        "snap-portal:https_open.ys7.com_help_10",
        "snap-sdkref:https___open.ys7.com_doc_x.html",
        "snap-sdkref:entry:0",
    ):
        decisions = tmp_path / "legacy.json"
        _write(decisions, {"schema_version": 1, "decisions": [_decision(legacy, SHA_A)]})
        with pytest.raises(mod.ProductMatrixError):
            mod.build_matrix(repo, manifest, decisions)


def test_cli_module_invocation_end_to_end(tmp_path):
    """python -m harness.product_matrix runs the real audit against this repo."""
    catalog = [_catalog_row()]
    docs = [_inventory_doc()]
    root = tmp_path / "portal-snap"
    root.mkdir()
    for row in catalog:
        raw_file = row.get("raw_file")
        if row.get("status") == "archived" and raw_file:
            target = root / raw_file
            target.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(
                {
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "html_content": f"<html>{row['doc_id']}</html>",
                    "platform": "doc_open",
                },
                ensure_ascii=False,
            ).encode()
            target.write_bytes(body)
            row["raw_file_sha256"] = _sha(body)
    manifest_bytes = _write(root / "catalog.json", catalog)
    inventory = tmp_path / "inventory.json"
    inventory_bytes = _write(inventory, {"documents": docs})
    snap = _snapshot(
        "snap-portal",
        "portal",
        root,
        manifest_bytes,
        inventory=inventory,
        inventory_bytes=inventory_bytes,
    )
    manifest = _manifest(tmp_path, [snap])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness.product_matrix",
            "--repo",
            ".",
            "--manifest",
            str(manifest),
            "--summary-only",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["evidence"] == "source_and_registry_matrix_only"
    assert report["schema_version"] == 2
    assert report["publication_profile"] == "reviewed_metadata_only"
    assert report["product_complete"] is False
    # Real registry audit ran against the worktree contracts.
    assert report["registry_audit"]["evidence"] == "registry_and_static_ast_only"
    assert report["summary"]["reviewed_contract_count"] >= 200


def _real_inventory_doc(tmp_path: Path, doc_id, region, url, html):
    """Run the real extract/build pipeline over one archived HTML page.

    Writes a mini archive (catalog.json + raw wrapper) under tmp_path and
    returns the single inventory document row produced by
    tools.documents.endpoint_inventory.build_inventory — real parser output,
    not a hand-shaped fixture.
    """
    endpoint_inventory = importlib.import_module("tools.documents.endpoint_inventory")
    archive = tmp_path / f"archive-{doc_id}"
    archive.mkdir()
    body = json.dumps(
        {
            "doc_id": doc_id,
            "title": f"doc-{doc_id}",
            "html_content": html,
            "platform": "doc_open" if region == "domestic" else "doc_open_en",
        },
        ensure_ascii=False,
    ).encode()
    raw_name = f"raw/{doc_id}.json"
    target = archive / raw_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    content_sha = _sha(html.encode("utf-8"))
    catalog_row = {
        "region": region,
        "doc_id": doc_id,
        "title": f"doc-{doc_id}",
        "source_url": url,
        "status": "archived",
        "memberships": [{"category_path": ["API", "Demo"]}],
        "raw_file": raw_name,
        "raw_file_sha256": _sha(body),
        "content_sha256": content_sha,
    }
    _write(archive / "catalog.json", [catalog_row])
    inventory = endpoint_inventory.build_inventory(archive)
    assert len(inventory["documents"]) == 1
    doc = inventory["documents"][0]
    assert doc["source_url"] == url and doc["content_sha256"] == content_sha
    return doc


def test_real_parser_relations_are_hints_not_support(tmp_path, monkeypatch):
    """build_inventory output keeps example→declaration as a review hint.

    The real parser attaches ``related_declaration_ordinals`` on examples;
    the matrix must project that relation, list agreeing examples under
    ``related_example_ids``, and never count them as exact/recovered
    support. A disagreeing or unrelated example stays provisional.
    """
    html = (
        "<h2>Demo op</h2>"
        "<ul><li>请求地址</li></ul>"
        "<p><code>https://open.ys7.com/api/lapp/demo/op</code></p>"
        "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
        "<ul><li>请求示例</li></ul>"
        "<pre><code>curl --request POST 'https://open.ys7.com/api/lapp/demo/op'</code></pre>"
        "<h2>Other op</h2>"
        "<ul><li>请求地址</li></ul>"
        "<p><code>https://open.ys7.com/api/lapp/other/ep</code></p>"
        "<ul><li>请求方式</li></ul><p><code>GET</code></p>"
        "<ul><li>请求示例</li></ul>"
        "<pre><code>curl --request DELETE 'https://open.ys7.com/api/lapp/unrelated/x'</code></pre>"
    )
    url = "https://open.ys7.com/help/10"
    inv_doc = _real_inventory_doc(tmp_path, "10", "domestic", url, html)
    decls = inv_doc["declarations"]
    exs = inv_doc["examples"]
    assert len(decls) == 2 and len(exs) == 2
    # Real relation shape: only the example side carries ordinals.
    assert all("related_declaration_ordinals" in e for e in exs)
    assert all("related_example_ordinals" not in d for d in decls)
    # Real parser agreement: first example agrees, second disagrees.
    assert exs[0]["agrees_with_declaration"] is True
    assert exs[1]["agrees_with_declaration"] is False

    # The catalog pin must equal the inventory-derived content sha so the
    # six-field join is eligible; raw_file holds the real archived wrapper.
    content_sha = inv_doc["content_sha256"]
    row = _catalog_row(
        doc_id="10",
        url=url,
        sha=content_sha,
        raw_file="pages/domestic/10.source.json",
    )
    root = tmp_path / "portal-snap"
    root.mkdir()
    wrapper = json.dumps(
        {
            "doc_id": "10",
            "title": "doc-10",
            "html_content": html,
            "platform": "doc_open",
        },
        ensure_ascii=False,
    ).encode()
    target = root / row["raw_file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(wrapper)
    row["raw_file_sha256"] = _sha(wrapper)
    manifest_bytes = _write(root / "catalog.json", [row])
    inventory = tmp_path / "inventory.json"
    inventory_bytes = _write(inventory, {"documents": [inv_doc]})
    portal = _snapshot(
        "snap-portal",
        "portal",
        root,
        manifest_bytes,
        inventory=inventory,
        inventory_bytes=inventory_bytes,
    )
    contract = _contract(
        doc_id="10",
        url=url,
        sha=content_sha,
        method="POST",
        path="/api/lapp/demo/op",
    )
    report = _build(tmp_path, monkeypatch, [portal], contracts=[contract])

    cap = next(c for c in report["capabilities"] if c["capability_id"] == "contract:cn.demo.op")
    decl_obs = {
        o["observation_id"]: o for o in report["observations"] if o["kind"] == "declaration"
    }
    ex_obs = {o["observation_id"]: o for o in report["observations"] if o["kind"] == "example"}
    # Only the matching declared_pair recovered the contract.
    matched = [o for o in decl_obs.values() if o["exact_operation_ids"]]
    assert len(matched) == 1 and matched[0]["method"] == "POST"
    assert cap["linked_observation_ids"] == [matched[0]["observation_id"]]
    # The agreeing example is a separate hint on the same source, never in
    # the linked (exact) list.
    assert cap["related_example_ids"] == [
        o["observation_id"] for o in ex_obs.values() if o["agrees_with_declaration"] is True
    ]
    disagree = [o for o in ex_obs.values() if o["agrees_with_declaration"] is False]
    assert disagree and disagree[0]["observation_id"] not in cap["related_example_ids"]
    # No example gained exact ids; both stay provisional/unmatched.
    assert all(o["exact_operation_ids"] == [] for o in ex_obs.values())
    _assert_public_schema(report)


def test_registry_audit_extra_keys_never_reach_report(tmp_path, monkeypatch):
    """Synthetic extra keys inside the audit object are dropped at the
    registry_audit projection — the publisher boundary, not the constants."""
    canary = "{{CANARY_aud1txtra}}"
    catalog = [_catalog_row()]
    docs = [_inventory_doc()]
    portal = _portal_fixture(tmp_path, catalog, docs)

    def bloated_audit(root: Path):
        report = _audit_stub(root)
        report["registry"]["summary"]["future_registry_metric"] = canary
        report["registry"]["summary"]["regions"] = {"domestic": 1}
        report["registry"]["extra_block"] = {"leak": canary}
        report["code"]["summary"]["files_checked"] = canary
        report["code"]["source_files_sha256"] = {"src/secret.py": SHA_B}
        report["inputs_sha256"]["contracts/coverage.json"] = SHA_B
        report["inputs_sha256"]["future_input"] = canary
        report["unexpected_top"] = canary
        report["evidence"] = canary  # a drifted token must not pass through
        return report

    repo = _repo(tmp_path)
    mod = module()
    monkeypatch.setattr("harness.product_matrix.audit_repository", bloated_audit)
    manifest = _manifest(tmp_path, [portal])
    report = mod.build_matrix(repo, manifest)
    audit = report["registry_audit"]
    assert set(audit) == _REGISTRY_AUDIT_KEYS
    assert audit["evidence"] == "registry_and_static_ast_only"
    assert set(audit["registry_summary"]) == _REGISTRY_SUMMARY_KEYS
    assert set(audit["code_summary"]) == _CODE_SUMMARY_KEYS
    assert set(audit["inputs_sha256"]) == _AUDIT_INPUT_KEYS
    payload = json.dumps(report, ensure_ascii=False)
    assert canary not in payload
    assert "coverage.json" not in payload and "files_checked" not in payload
    _assert_public_schema(report)


def test_source_and_capability_internal_keys_never_published():
    """Extra internal fields on a source _extra block, a source row, and a
    capability dict must not survive the real per-kind publisher functions."""
    mod = module()
    canary = "{{CANARY_int3rnal}}"
    internal_source = {
        "source_id": "snap:asset:0",
        "snapshot_id": "snap",
        "record_index": 0,
        "kind": "asset",
        "region": None,
        "doc_id": None,
        "canonical_document_url": None,
        "status": "ok",
        "content_sha256": SHA_A,
        "category_paths": [["recorded", "but", "unverified"]],
        "_category_paths_verified": False,
        "disposition": "needs_review",
        "operation_ids": [],
        "observation_ordinals": [],
        "evidence_refs": ["snap/manifest.jsonl#record/0"],
        "unresolved_public": [],
        "body_available": True,
        "_blocked": False,
        "_warning_refs": [canary],
        "_extra": {
            "asset_category": "document",
            "reference_count": 2,
            "references": [
                {
                    "reference_index": 0,
                    "reference_type": "link",
                    "evidence_ref": "snap/manifest.jsonl#record/0/references/0",
                    "url": f"https://{canary}.example.com/ref",
                    "reference_line": 7,
                },
                canary,  # non-dict entries never pass through
            ],
            "asset_id": canary,
            "raw_url": canary,
            "downloaded_file": canary,
        },
        "decision_reason": canary,
        "future_source_field": canary,
    }
    out = mod._public_source(internal_source, mod._ASSET_STATUSES)
    assert set(out) == _SOURCE_BASE_KEYS | {
        "asset_category",
        "reference_count",
        "references",
    }
    assert out["category_paths"] == []  # unverified recorded paths stay out
    assert out["asset_category"] == "document"
    assert out["references"] == [
        {
            "reference_index": 0,
            "reference_type": "link",
            "evidence_ref": "snap/manifest.jsonl#record/0/references/0",
        }
    ]
    assert canary not in json.dumps(out, ensure_ascii=False)
    # Catalog-verified paths do publish.
    internal_source["_category_paths_verified"] = True
    out2 = mod._public_source(internal_source, mod._ASSET_STATUSES)
    assert out2["category_paths"] == [["recorded", "but", "unverified"]]
    # An internal capability carrying stray fields keeps only its whitelist.
    internal_cap = {
        "capability_id": "contract:op",
        "capability_kind": "reviewed_contract",
        "operation_id": "op",
        "region": "domestic",
        "source_doc_id": "10",
        "canonical_document_url": "https://open.ys7.com/help/10",
        "source_content_sha256": SHA_A,
        "method": "POST",
        "path": "/api/lapp/demo/op",
        "module": "src/demo.py",
        "symbols": ["Demo.op"],
        "public_entry": "demo.op",
        "implementation_status": "ast_verified",
        "offline_status": "passed",
        "offline_evidence": "recorded_evidence",
        "live_status": "not_verified",
        "live_evidence": None,
        "source_review_status": "aligned",
        "source_review_issues": [],
        "linked_source_ids": ["snap:domestic:10"],
        "linked_observation_ids": [],
        "related_example_ids": [],
        "disposition": "implement",
        "provisional": False,
        "source_url": f"https://{canary}.example.com/help/10",
        "_join_identity": (canary,),
        "future_capability_field": canary,
    }
    cap_out = mod._public_capability(internal_cap)
    assert set(cap_out) == _CAPABILITY_REVIEWED_KEYS
    assert canary not in json.dumps(cap_out, ensure_ascii=False)
    # An unrecognized capability kind falls back to the locator-free set.
    odd = dict(internal_cap)
    odd["capability_kind"] = "future_kind"
    odd_out = mod._public_capability(odd)
    assert "path" not in odd_out and "canonical_document_url" not in odd_out
    assert canary not in json.dumps(odd_out, ensure_ascii=False)
