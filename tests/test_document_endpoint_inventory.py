"""I003C independent tests for the archive inventory adapter contract.

Builds real tiny archives (``catalog.json`` + raw source files) under tmp_path
and drives ``tools.documents.endpoint_inventory.build_inventory`` plus the CLI.
Positive integration cases run the true pipeline end to end; no parser mocks.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.documents.endpoint_inventory import InventoryError, build_inventory  # noqa: E402

SUMMARY_KEYS = {
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
}
TOP_KEYS = {
    "schema_version",
    "evidence",
    "summary",
    "documents",
    "unrecovered_reviewed",
    "input_sha256",
    "source_files_sha256",
    "limitations",
}

POST_A = (
    "<h2>A</h2><ul><li>请求地址</li></ul>"
    "<p><code>https://open.ys7.com/api/a</code></p>"
    "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
)
POST_A_EXAMPLE = POST_A + (
    "<ul><li>请求示例</li></ul>"
    "<pre><code>curl --request POST 'https://open.ys7.com/api/a'</code></pre>"
)
GET_B = (
    "<h2>B</h2><ul><li>请求地址</li></ul>"
    "<p><code>/api/b</code></p>"
    "<ul><li>请求方式</li></ul><p><code>GET</code></p>"
)
ADDRESS_ONLY = "<h2>Lonely</h2><ul><li>请求地址</li></ul><p><code>/api/lonely</code></p>"
NO_DECL = '<h2>Guide</h2><p><a href="https://open.ys7.com/help/1">doc link</a></p>'
TEMPLATE_OP = (
    "<h3>T</h3><ul>"
    "<li><p>Request Address</p><p><code>{areaDomain}/api/t</code></p></li>"
    "<li><p>Request Method</p><p><code>POST</code></p></li></ul>"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_raw(root: Path, name: str, html: str, *, doc_id=None, platform=None) -> dict:
    if doc_id is None:
        doc_id = "".join(c for c in Path(name).stem if c.isdigit()) or "1"
    payload = {"doc_id": doc_id, "html_content": html}
    if platform is not None:
        payload["platform"] = platform
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "raw_file": name,
        "raw_file_sha256": sha256_bytes(raw),
        "content_sha256": sha256_bytes(html.encode("utf-8")),
    }


def catalog_entry(
    region, doc_id, *, raw=None, status="archived", title=None, url=None, memberships=None
):
    if url is None:
        segment = "help/en" if region == "overseas" else "help"
        url = f"https://open.ys7.com/{segment}/{doc_id}"
    entry = {
        "region": region,
        "doc_id": doc_id,
        "title": title or f"doc-{doc_id}",
        "source_url": url,
        "status": status,
        "memberships": memberships
        if memberships is not None
        else [{"category_path": ["API", "组"]}],
    }
    if raw:
        entry.update(raw)
    return entry


def write_catalog(root: Path, entries) -> bytes:
    root.mkdir(parents=True, exist_ok=True)
    data = json.dumps(entries, ensure_ascii=False).encode("utf-8")
    (root / "catalog.json").write_bytes(data)
    return data


def write_contracts(base: Path, contracts) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    if isinstance(contracts, list):
        contracts = {"schema_version": 1, "contracts": contracts}
    path = base / "contracts.json"
    path.write_bytes(json.dumps(contracts, ensure_ascii=False).encode("utf-8"))
    return path


def contract(
    operation_id, region, doc_id, *, url=None, content_sha256=None, method="POST", path="/api/a"
):
    if url is None:
        segment = "help/en" if region == "overseas" else "help"
        url = f"https://open.ys7.com/{segment}/{doc_id}"
    return {
        "operation_id": operation_id,
        "region": region,
        "source_doc_id": doc_id,
        "source_url": url,
        "source_content_sha256": content_sha256 or "a" * 64,
        "method": method,
        "path": path,
    }


def run_cli(*args):
    env = {
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PATH": "/usr/bin:/bin",
    }
    return subprocess.run(
        [sys.executable, "-m", "tools.documents.endpoint_inventory", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=env,
        timeout=30,
    )


# ---------------------------------------------------------------- output


def test_output_shape_and_scanned_document_fields(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/2.json", POST_A)
    write_catalog(root, [catalog_entry("domestic", "2", raw=raw, title="t2")])
    out = build_inventory(root)
    assert set(out) == TOP_KEYS
    assert out["schema_version"] == 1
    assert out["evidence"] == "offline_http_declarations_only"
    assert isinstance(out["limitations"], list) and out["limitations"]
    doc = out["documents"][0]
    for key in (
        "region",
        "doc_id",
        "title",
        "source_url",
        "content_sha256",
        "category_paths",
        "status",
        "declarations",
        "examples",
        "warnings",
    ):
        assert key in doc
    assert doc["status"] == "scanned"
    assert doc["region"] == "domestic" and doc["doc_id"] == "2"
    assert doc["title"] == "t2"
    assert doc["source_url"] == "https://open.ys7.com/help/2"
    assert doc["content_sha256"] == raw["content_sha256"]
    assert doc["category_paths"] == [["API", "组"]]
    decl = doc["declarations"][0]
    assert decl["status"] == "declared_pair"
    assert decl["method"] == "POST" and decl["path"] == "/api/a"
    assert "reviewed_operation_ids" in decl and "same_route_operation_ids" in decl


def test_documents_sorted_by_region_then_numeric_doc_id(tmp_path):
    root = tmp_path / "archive"
    write_catalog(
        root,
        [
            catalog_entry("overseas", "3", raw=write_raw(root, "raw/o3.json", GET_B)),
            catalog_entry("domestic", "10", raw=write_raw(root, "raw/d10.json", GET_B)),
            catalog_entry("domestic", "2", raw=write_raw(root, "raw/d2.json", POST_A)),
        ],
    )
    out = build_inventory(root)
    assert [(d["region"], d["doc_id"]) for d in out["documents"]] == [
        ("domestic", "2"),
        ("domestic", "10"),
        ("overseas", "3"),
    ]


def test_same_doc_id_in_two_regions_not_cross_attributed(tmp_path):
    root = tmp_path / "archive"
    write_catalog(
        root,
        [
            catalog_entry(
                "domestic", "9001", raw=write_raw(root, "raw/d.json", POST_A, doc_id="9001")
            ),
            catalog_entry(
                "overseas", "9001", raw=write_raw(root, "raw/o.json", GET_B, doc_id="9001")
            ),
        ],
    )
    out = build_inventory(root)
    docs = {d["region"]: d for d in out["documents"]}
    assert docs["domestic"]["declarations"][0]["path"] == "/api/a"
    assert docs["overseas"]["declarations"][0]["path"] == "/api/b"


def test_summary_exact_keys_and_counts(tmp_path):
    root = tmp_path / "archive"
    write_catalog(
        root,
        [
            catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A_EXAMPLE)),
            catalog_entry("domestic", "2", raw=write_raw(root, "raw/2.json", GET_B)),
            catalog_entry("domestic", "3", raw=write_raw(root, "raw/3.json", NO_DECL)),
            catalog_entry("domestic", "4", raw=write_raw(root, "raw/4.json", ADDRESS_ONLY)),
            catalog_entry("overseas", "5", status="source_missing"),
        ],
    )
    out = build_inventory(root)
    s = out["summary"]
    assert set(s) == SUMMARY_KEYS
    assert s["documents"] == 5
    assert s["archived_documents"] == 4
    assert s["source_unavailable_documents"] == 1
    assert s["documents_with_declarations"] == 3
    assert s["declarations"] == 3
    assert s["paired_declarations"] == 2
    assert s["unresolved_declarations"] == 1
    assert s["declarations"] == s["paired_declarations"] + s["unresolved_declarations"]
    assert s["examples"] == 1
    assert s["matched_reviewed_contracts"] == 0
    assert s["unrecovered_reviewed_contracts"] == 0


def test_source_unavailable_document_retained(tmp_path):
    root = tmp_path / "archive"
    write_catalog(
        root,
        [
            catalog_entry("domestic", "1", status="source_missing"),
        ],
    )
    out = build_inventory(root)
    doc = out["documents"][0]
    assert doc["status"] == "source_unavailable"
    assert doc.get("declarations") in (None, [])


def test_zero_candidate_document_is_scanned_not_empty_claim(tmp_path):
    root = tmp_path / "archive"
    write_catalog(
        root, [catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", NO_DECL))]
    )
    out = build_inventory(root)
    doc = out["documents"][0]
    assert doc["status"] == "scanned"
    assert doc["declarations"] == []


def test_unreferenced_archive_files_not_hashed(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", POST_A)
    (root / "raw" / "ghost.json").write_bytes(b"{}")
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    out = build_inventory(root)
    assert set(out["source_files_sha256"]) == {"raw/1.json"}
    assert out["source_files_sha256"]["raw/1.json"] == raw["raw_file_sha256"]


def test_input_and_source_hashes(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", POST_A)
    catalog_bytes = write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    out = build_inventory(root)
    assert out["input_sha256"]["catalog"] == sha256_bytes(catalog_bytes)
    assert out["input_sha256"].get("contracts") is None


def test_repeatable_output_is_byte_identical(tmp_path):
    root = tmp_path / "archive"
    write_catalog(
        root, [catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A_EXAMPLE))]
    )
    first = build_inventory(root)
    second = build_inventory(root)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# ---------------------------------------------------------------- validation


def test_inventory_error_is_value_error():
    assert issubclass(InventoryError, ValueError)


def test_raw_hash_mismatch_rejected(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", POST_A)
    raw["raw_file_sha256"] = "0" * 64
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises(InventoryError):
        build_inventory(root)


def test_content_hash_mismatch_rejected(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", POST_A)
    raw["content_sha256"] = "0" * 64
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises(InventoryError):
        build_inventory(root)


def test_stale_source_bytes_rejected(tmp_path):
    """Bytes rewritten after hashing must never be extracted."""
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", POST_A)
    (root / "raw" / "1.json").write_bytes(json.dumps({"html_content": GET_B}).encode("utf-8"))
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises((InventoryError, OSError)):
        build_inventory(root)


def test_other_doc_body_rejected(tmp_path):
    """File bytes hash to their declared raw hash but html is another doc's."""
    root = tmp_path / "archive"
    body = json.dumps({"html_content": GET_B}, ensure_ascii=False).encode("utf-8")
    path = root / "raw" / "1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    raw = {
        "raw_file": "raw/1.json",
        "raw_file_sha256": sha256_bytes(body),
        "content_sha256": sha256_bytes(POST_A.encode("utf-8")),
    }
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises(InventoryError):
        build_inventory(root)


def test_duplicate_identity_rejected(tmp_path):
    root = tmp_path / "archive"
    write_catalog(
        root,
        [
            catalog_entry("domestic", "1", raw=write_raw(root, "raw/a.json", POST_A)),
            catalog_entry("domestic", "1", raw=write_raw(root, "raw/b.json", GET_B)),
        ],
    )
    with pytest.raises(InventoryError):
        build_inventory(root)


@pytest.mark.parametrize("region", ["intl", "", "DOMESTIC"])
def test_invalid_region_rejected(tmp_path, region):
    root = tmp_path / "archive"
    write_catalog(root, [catalog_entry(region, "1", raw=write_raw(root, "raw/1.json", POST_A))])
    with pytest.raises(InventoryError):
        build_inventory(root)


@pytest.mark.parametrize("doc_id", ["abc", "-1", "1a", ""])
def test_invalid_doc_id_rejected(tmp_path, doc_id):
    root = tmp_path / "archive"
    write_catalog(
        root, [catalog_entry("domestic", doc_id, raw=write_raw(root, "raw/1.json", POST_A))]
    )
    with pytest.raises(InventoryError):
        build_inventory(root)


@pytest.mark.parametrize("raw_file", ["../escape.json", "/abs/escape.json", "raw/../../x.json"])
def test_raw_path_escape_rejected(tmp_path, raw_file):
    root = tmp_path / "archive"
    html = POST_A
    raw = {
        "raw_file": raw_file,
        "raw_file_sha256": "0" * 64,
        "content_sha256": sha256_bytes(html.encode("utf-8")),
    }
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises(InventoryError):
        build_inventory(root)


def test_missing_raw_file_rejected(tmp_path):
    root = tmp_path / "archive"
    raw = {
        "raw_file": "raw/ghost.json",
        "raw_file_sha256": "0" * 64,
        "content_sha256": "0" * 64,
    }
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises((InventoryError, OSError)):
        build_inventory(root)


@pytest.mark.parametrize("missing", ["raw_file", "raw_file_sha256", "content_sha256"])
def test_archived_entry_missing_raw_fields_rejected(tmp_path, missing):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", POST_A)
    del raw[missing]
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises(InventoryError):
        build_inventory(root)


def test_catalog_not_a_list_rejected(tmp_path):
    root = tmp_path / "archive"
    root.mkdir(parents=True)
    (root / "catalog.json").write_bytes(b'{"entries": []}')
    with pytest.raises((InventoryError, ValueError)):
        build_inventory(root)


def test_catalog_invalid_json_rejected(tmp_path):
    root = tmp_path / "archive"
    root.mkdir(parents=True)
    (root / "catalog.json").write_bytes(b"not json {")
    with pytest.raises((InventoryError, ValueError)):
        build_inventory(root)


@pytest.mark.parametrize(
    "missing", ["region", "doc_id", "title", "source_url", "status", "memberships"]
)
def test_catalog_entry_missing_required_field_rejected(tmp_path, missing):
    root = tmp_path / "archive"
    entry = catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A))
    del entry[missing]
    write_catalog(root, [entry])
    with pytest.raises(InventoryError):
        build_inventory(root)


@pytest.mark.parametrize("memberships", ["x", [{"category_path": "notalist"}], [1]])
def test_invalid_memberships_rejected(tmp_path, memberships):
    root = tmp_path / "archive"
    entry = catalog_entry(
        "domestic", "1", raw=write_raw(root, "raw/1.json", POST_A), memberships=memberships
    )
    write_catalog(root, [entry])
    with pytest.raises(InventoryError):
        build_inventory(root)


def test_raw_file_not_json_rejected(tmp_path):
    root = tmp_path / "archive"
    path = root / "raw" / "1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{broken")
    raw = {
        "raw_file": "raw/1.json",
        "raw_file_sha256": sha256_bytes(b"{broken"),
        "content_sha256": "0" * 64,
    }
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises((InventoryError, ValueError)):
        build_inventory(root)


def test_html_content_not_string_rejected(tmp_path):
    root = tmp_path / "archive"
    body = json.dumps({"html_content": 123}).encode("utf-8")
    path = root / "raw" / "1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    raw = {
        "raw_file": "raw/1.json",
        "raw_file_sha256": sha256_bytes(body),
        "content_sha256": sha256_bytes(b"123"),
    }
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    with pytest.raises((InventoryError, ValueError)):
        build_inventory(root)


def test_unknown_status_rejected(tmp_path):
    root = tmp_path / "archive"
    write_catalog(root, [catalog_entry("domestic", "1", status="vanished")])
    with pytest.raises(InventoryError):
        build_inventory(root)


def test_no_partial_output_when_any_source_bad(tmp_path):
    root = tmp_path / "archive"
    good = write_raw(root, "raw/1.json", POST_A)
    bad = write_raw(root, "raw/2.json", GET_B)
    bad["content_sha256"] = "0" * 64
    write_catalog(
        root,
        [
            catalog_entry("domestic", "1", raw=good),
            catalog_entry("domestic", "2", raw=bad),
        ],
    )
    with pytest.raises(InventoryError):
        build_inventory(root)


# ---------------------------------------------------------------- contracts


def test_exact_contract_match_links_operation_id(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/2.json", POST_A)
    write_catalog(root, [catalog_entry("domestic", "2", raw=raw)])
    cpath = write_contracts(
        tmp_path,
        [
            contract(
                "op-1",
                "domestic",
                "2",
                content_sha256=raw["content_sha256"],
                method="POST",
                path="/api/a",
            ),
        ],
    )
    out = build_inventory(root, contracts_path=cpath)
    decl = out["documents"][0]["declarations"][0]
    assert decl["reviewed_operation_ids"] == ["op-1"]
    assert out["summary"]["matched_reviewed_contracts"] == 1
    assert out["summary"]["unrecovered_reviewed_contracts"] == 0
    assert out["unrecovered_reviewed"] == []
    assert out["input_sha256"]["contracts"] == sha256_bytes(cpath.read_bytes())


def test_same_route_links_but_does_not_replace_source_match(tmp_path):
    root = tmp_path / "archive"
    raw2 = write_raw(root, "raw/2.json", POST_A)
    raw9 = write_raw(root, "raw/9.json", POST_A)
    write_catalog(
        root,
        [
            catalog_entry("domestic", "2", raw=raw2),
            catalog_entry("domestic", "9", raw=raw9),
        ],
    )
    cpath = write_contracts(
        tmp_path,
        [
            contract(
                "op-b",
                "domestic",
                "2",
                content_sha256=raw2["content_sha256"],
                method="POST",
                path="/api/a",
            ),
        ],
    )
    out = build_inventory(root, contracts_path=cpath)
    docs = {d["doc_id"]: d for d in out["documents"]}
    assert docs["2"]["declarations"][0]["reviewed_operation_ids"] == ["op-b"]
    other = docs["9"]["declarations"][0]
    assert other["reviewed_operation_ids"] == []
    assert "op-b" in other["same_route_operation_ids"]


def test_unrecovered_contract_when_source_absent(tmp_path):
    root = tmp_path / "archive"
    write_catalog(root, [catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A))])
    cpath = write_contracts(
        tmp_path,
        [
            contract(
                "op-gone", "domestic", "9999", content_sha256="0" * 64, method="POST", path="/api/x"
            ),
        ],
    )
    out = build_inventory(root, contracts_path=cpath)
    ids = {u["operation_id"] for u in out["unrecovered_reviewed"]}
    assert "op-gone" in ids
    for u in out["unrecovered_reviewed"]:
        assert isinstance(u.get("reason"), str) and u["reason"]
    assert out["summary"]["unrecovered_reviewed_contracts"] >= 1


def test_unrecovered_contract_on_url_or_hash_drift(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/2.json", POST_A)
    write_catalog(root, [catalog_entry("domestic", "2", raw=raw)])
    cpath = write_contracts(
        tmp_path,
        [
            contract(
                "op-url-drift",
                "domestic",
                "2",
                url="https://open.ys7.com/help/2-old",
                content_sha256=raw["content_sha256"],
                method="POST",
                path="/api/a",
            ),
            contract(
                "op-hash-drift",
                "domestic",
                "2",
                content_sha256="f" * 64,
                method="POST",
                path="/api/a",
            ),
        ],
    )
    out = build_inventory(root, contracts_path=cpath)
    ids = {u["operation_id"] for u in out["unrecovered_reviewed"]}
    assert {"op-url-drift", "op-hash-drift"} <= ids
    decl = out["documents"][0]["declarations"][0]
    assert decl["reviewed_operation_ids"] == []


def test_registry_cannot_complete_template_or_ambiguous_routes(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", TEMPLATE_OP)
    write_catalog(
        root, [catalog_entry("overseas", "1", raw=raw, url="https://open.ys7.com/help/en/1")]
    )
    cpath = write_contracts(
        tmp_path,
        [
            contract(
                "op-t",
                "overseas",
                "1",
                url="https://open.ys7.com/help/en/1",
                content_sha256=raw["content_sha256"],
                method="POST",
                path="/api/other",
            ),
        ],
    )
    out = build_inventory(root, contracts_path=cpath)
    decl = out["documents"][0]["declarations"][0]
    assert decl["reviewed_operation_ids"] == []
    assert decl["path"] == "/api/t" and decl["host"] == "{areaDomain}"


def test_bad_contracts_rejected(tmp_path):
    root = tmp_path / "archive"
    write_catalog(root, [catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A))])
    for contracts in (
        {"ops": []},
        [contract("dup", "domestic", "1"), contract("dup", "domestic", "1")],
        [{"region": "domestic", "doc_id": "1", "method": "POST", "path": "/api/a"}],
    ):
        cpath = write_contracts(tmp_path, contracts)
        with pytest.raises((InventoryError, ValueError)):
            build_inventory(root, contracts_path=cpath)


def test_no_contracts_means_zero_match_counts(tmp_path):
    root = tmp_path / "archive"
    write_catalog(root, [catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A))])
    out = build_inventory(root)
    assert out["summary"]["matched_reviewed_contracts"] == 0
    assert out["summary"]["unrecovered_reviewed_contracts"] == 0
    assert out["unrecovered_reviewed"] == []


# ---------------------------------------------------------------- CLI


def test_cli_success_exit_zero_json_with_newline(tmp_path):
    root = tmp_path / "archive"
    write_catalog(root, [catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A))])
    result = run_cli("--input", str(root))
    assert result.returncode == 0
    assert result.stdout.endswith("\n")
    out = json.loads(result.stdout)
    assert out["schema_version"] == 1
    assert set(out) == TOP_KEYS


def test_cli_summary_only_shape(tmp_path):
    root = tmp_path / "archive"
    write_catalog(root, [catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A))])
    result = run_cli("--input", str(root), "--summary-only")
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert "documents" not in out
    assert set(out) == {
        "schema_version",
        "evidence",
        "summary",
        "unrecovered_reviewed",
        "input_sha256",
        "limitations",
    }
    blob = json.dumps(out)
    assert "/api/a" not in blob


def test_cli_validation_failure_fixed_stderr(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", POST_A)
    raw["raw_file_sha256"] = "0" * 64
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    result = run_cli("--input", str(root))
    assert result.returncode == 2
    assert result.stderr == "endpoint-inventory: validation failed\n"
    assert result.stdout == ""


def test_cli_output_deterministic_across_runs(tmp_path):
    root = tmp_path / "archive"
    write_catalog(
        root,
        [
            catalog_entry("domestic", "2", raw=write_raw(root, "raw/2.json", POST_A_EXAMPLE)),
            catalog_entry(
                "overseas",
                "1",
                raw=write_raw(root, "raw/1.json", TEMPLATE_OP),
                url="https://open.ys7.com/help/en/1",
            ),
        ],
    )
    first = run_cli("--input", str(root))
    second = run_cli("--input", str(root))
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout


def test_cli_contracts_flag(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/2.json", POST_A)
    write_catalog(root, [catalog_entry("domestic", "2", raw=raw)])
    cpath = write_contracts(
        tmp_path,
        [
            contract(
                "op-1",
                "domestic",
                "2",
                content_sha256=raw["content_sha256"],
                method="POST",
                path="/api/a",
            ),
        ],
    )
    result = run_cli("--input", str(root), "--contracts", str(cpath))
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["summary"]["matched_reviewed_contracts"] == 1


POST_A_QUERY = (
    "<h2>A</h2><ul><li>请求地址</li></ul>"
    "<p><code>/api/a?mode=special</code></p>"
    "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
)
POST_A_FRAGMENT = (
    "<h2>A</h2><ul><li>请求地址</li></ul>"
    "<p><code>/api/a#frag</code></p>"
    "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
)


@pytest.mark.parametrize(
    "html,blocked_field,expected_status",
    [
        (POST_A_QUERY, "query", "declared_pair"),
        (POST_A_FRAGMENT, "fragment", "ambiguous"),
    ],
    ids=["query", "fragment"],
)
def test_declared_query_or_fragment_blocks_exact_join(
    tmp_path, html, blocked_field, expected_status
):
    archive = tmp_path / "archive"
    raw = write_raw(archive, "901.json", html, doc_id="901")
    write_catalog(archive, [catalog_entry("domestic", "901", raw=raw)])
    contracts_path = write_contracts(
        tmp_path / "registry",
        [
            contract("op-a", "domestic", "901", content_sha256=raw["content_sha256"]),
        ],
    )

    inventory = build_inventory(archive, contracts_path=contracts_path)

    decl = inventory["documents"][0]["declarations"][0]
    assert decl["status"] == expected_status
    assert decl["method"] == "POST"
    assert decl["path"] == "/api/a"
    assert decl.get(blocked_field)
    # The contract matches every strict key component (region, doc, url,
    # content hash, method, path), yet a non-empty query/fragment must bar
    # it from the exact join.
    assert decl["reviewed_operation_ids"] == []
    # Same-route contracts stay visible as informational hints only.
    assert decl["same_route_operation_ids"] == ["op-a"]
    assert inventory["summary"]["matched_reviewed_contracts"] == 0
    assert inventory["summary"]["unrecovered_reviewed_contracts"] == 1
    assert [r["operation_id"] for r in inventory["unrecovered_reviewed"]] == ["op-a"]


@pytest.mark.parametrize("html", [POST_A, POST_A_EXAMPLE], ids=["plain", "with-example"])
def test_declared_plain_path_still_exact_joins(tmp_path, html):
    archive = tmp_path / "archive"
    raw = write_raw(archive, "902.json", html, doc_id="902")
    write_catalog(archive, [catalog_entry("domestic", "902", raw=raw)])
    contracts_path = write_contracts(
        tmp_path / "registry",
        [
            contract("op-a", "domestic", "902", content_sha256=raw["content_sha256"]),
        ],
    )

    inventory = build_inventory(archive, contracts_path=contracts_path)

    decl = inventory["documents"][0]["declarations"][0]
    assert decl["status"] == "declared_pair"
    assert decl["path"] == "/api/a"
    assert decl["reviewed_operation_ids"] == ["op-a"]
    assert decl["same_route_operation_ids"] == ["op-a"]
    assert inventory["summary"]["matched_reviewed_contracts"] == 1
    assert inventory["summary"]["unrecovered_reviewed_contracts"] == 0
    assert [r["operation_id"] for r in inventory["unrecovered_reviewed"]] == []
