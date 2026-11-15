"""Focused edge-case tests for tools.documents.endpoint_inventory.build_inventory."""

import hashlib
import json
from pathlib import Path

import pytest

from tools.documents import endpoint_inventory as ei
from tools.documents.endpoint_inventory import InventoryError, build_inventory

DOC_ID = "1"
SOURCE_URL = "https://open.ys7.com/help/1"
HTML = (
    "<h2>One</h2>"
    "<ul><li>请求地址</li></ul><p><code>/api/a</code></p>"
    "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_archive(
    tmp_path: Path,
    *,
    raw_doc_id: str = DOC_ID,
    platform="doc_open",
    include_platform: bool = True,
    entry_overrides=None,
) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    raw = {"doc_id": raw_doc_id, "html_content": HTML}
    if include_platform:
        raw["platform"] = platform
    raw_bytes = json.dumps(raw).encode("utf-8")
    (archive / "page.json").write_bytes(raw_bytes)
    entry = {
        "region": "domestic",
        "doc_id": DOC_ID,
        "title": "One",
        "source_url": SOURCE_URL,
        "status": "archived",
        "memberships": [],
        "raw_file": "page.json",
        "raw_file_sha256": _sha(raw_bytes),
        "content_sha256": _sha(HTML.encode("utf-8")),
    }
    if entry_overrides:
        entry.update(entry_overrides)
    (archive / "catalog.json").write_text(json.dumps([entry]), encoding="utf-8")
    return archive


def _make_contracts(tmp_path: Path) -> Path:
    payload = {
        "schema_version": 1,
        "contracts": [
            {
                "operation_id": "cn.a",
                "region": "domestic",
                "source_doc_id": DOC_ID,
                "source_url": SOURCE_URL,
                "source_content_sha256": _sha(HTML.encode("utf-8")),
                "method": "POST",
                "path": "/api/a",
            }
        ],
    }
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_baseline_matches_reviewed_contract(tmp_path):
    result = build_inventory(_make_archive(tmp_path), contracts_path=_make_contracts(tmp_path))
    assert result["evidence"] == "offline_http_declarations_only"
    documents = result["documents"]
    assert len(documents) == 1
    declarations = documents[0]["declarations"]
    assert len(declarations) == 1
    assert declarations[0]["reviewed_operation_ids"] == ["cn.a"]
    assert result["summary"]["matched_reviewed_contracts"] == 1


def test_raw_doc_id_mismatch_rejected(tmp_path):
    archive = _make_archive(tmp_path, raw_doc_id="2")
    with pytest.raises(InventoryError):
        build_inventory(archive)


def test_raw_foreign_platform_rejected(tmp_path):
    archive = _make_archive(tmp_path, platform="doc_open_en")
    with pytest.raises(InventoryError):
        build_inventory(archive)


@pytest.mark.parametrize("platform, include_platform", [(None, True), (None, False)])
def test_raw_platform_null_or_missing_allowed(tmp_path, platform, include_platform):
    archive = _make_archive(tmp_path, platform=platform, include_platform=include_platform)
    result = build_inventory(archive)
    assert result["evidence"] == "offline_http_declarations_only"
    assert len(result["documents"]) == 1


def test_catalog_symlink_outside_archive_rejected(tmp_path):
    archive = _make_archive(tmp_path)
    outside = tmp_path / "outside_catalog.json"
    outside.write_bytes((archive / "catalog.json").read_bytes())
    (archive / "catalog.json").unlink()
    (archive / "catalog.json").symlink_to(outside)
    with pytest.raises(InventoryError):
        build_inventory(archive)


def test_raw_symlink_outside_archive_rejected(tmp_path):
    archive = _make_archive(tmp_path)
    outside = tmp_path / "outside_page.json"
    outside.write_bytes((archive / "page.json").read_bytes())
    (archive / "page.json").unlink()
    (archive / "page.json").symlink_to(outside)
    with pytest.raises(InventoryError):
        build_inventory(archive)


@pytest.mark.parametrize("field, value", [("region", []), ("status", {})])
def test_corrupt_catalog_field_type_raises_with_clean_chain(tmp_path, field, value):
    archive = _make_archive(tmp_path, entry_overrides={field: value})
    with pytest.raises(InventoryError) as excinfo:
        build_inventory(archive)
    exc = excinfo.value
    assert exc.__cause__ is None and exc.__context__ is None


def test_post_parse_raw_file_mutation_detected(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    raw_path = archive / "page.json"
    original = ei.extract_http_candidates
    calls = []

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        if not calls:
            tampered = json.loads(raw_path.read_text(encoding="utf-8"))
            tampered["html_content"] = "<p>tampered</p>"
            raw_path.write_text(json.dumps(tampered), encoding="utf-8")
        calls.append(result)
        return result

    monkeypatch.setattr(ei, "extract_http_candidates", wrapper)
    with pytest.raises(InventoryError):
        build_inventory(archive)
    assert calls


def test_post_parse_catalog_mutation_detected(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    catalog_path = archive / "catalog.json"
    original = ei.extract_http_candidates
    calls = []

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        if not calls:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog[0]["title"] = "Mutated"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        calls.append(result)
        return result

    monkeypatch.setattr(ei, "extract_http_candidates", wrapper)
    with pytest.raises(InventoryError):
        build_inventory(archive)
    assert calls
