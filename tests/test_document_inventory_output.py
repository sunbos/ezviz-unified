"""SP006 tests for the endpoint_inventory ``--output`` file interface.

Builds real tiny archives under tmp_path and drives the CLI through
subprocess so the receipt/stdout contract is observed end to end. No
parser or build_inventory mocks; helpers mirror the synthetic archive
pattern without importing sibling test modules.
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

from tools.documents.endpoint_inventory import FAILURE_LINE  # noqa: E402

SUMMARY_ONLY_KEYS = {
    "schema_version",
    "evidence",
    "summary",
    "unrecovered_reviewed",
    "input_sha256",
    "limitations",
}
RECEIPT_KEYS = {"schema_version", "evidence", "report_sha256", "summary"}

CANARY = "CANARY_TOKEN_9f3e2a"
POST_A = (
    "<h2>A</h2><ul><li>请求地址</li></ul>"
    "<p><code>https://open.ys7.com/api/a</code></p>"
    "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
    f"<p>{CANARY}</p>"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_raw(root: Path, name: str, html: str, *, doc_id="1") -> dict:
    raw = json.dumps({"doc_id": doc_id, "html_content": html}, ensure_ascii=False).encode("utf-8")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "raw_file": name,
        "raw_file_sha256": sha256_bytes(raw),
        "content_sha256": sha256_bytes(html.encode("utf-8")),
    }


def catalog_entry(region, doc_id, *, raw=None, status="archived"):
    entry = {
        "region": region,
        "doc_id": doc_id,
        "title": f"doc-{doc_id}",
        "source_url": f"https://open.ys7.com/help/{doc_id}",
        "status": status,
        "memberships": [{"category_path": ["API", "组"]}],
    }
    if raw:
        entry.update(raw)
    return entry


def write_catalog(root: Path, entries) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "catalog.json").write_bytes(json.dumps(entries, ensure_ascii=False).encode("utf-8"))


def make_archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    write_catalog(root, [catalog_entry("domestic", "1", raw=write_raw(root, "raw/1.json", POST_A))])
    return root


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


def no_stray_tempfiles(directory: Path) -> bool:
    return not any(directory.rglob(".endpoint-inventory-*"))


# ---------------------------------------------------------------- success


def test_output_writes_full_report_and_small_receipt(tmp_path):
    root = make_archive(tmp_path)
    target = tmp_path / "out" / "report.json"
    result = run_cli("--input", str(root), "--output", str(target))
    assert result.returncode == 0
    assert result.stderr == ""
    # The file carries the complete report payload.
    written = target.read_bytes()
    report = json.loads(written.decode("utf-8"))
    assert report["schema_version"] == 1
    assert report["evidence"] == "offline_http_declarations_only"
    assert report["documents"][0]["declarations"][0]["path"] == "/api/a"
    # The receipt is small, fixed, and never echoes the path or body.
    receipt = json.loads(result.stdout)
    assert set(receipt) == RECEIPT_KEYS
    assert receipt["schema_version"] == 1
    assert receipt["evidence"] == "offline_http_declarations_only"
    assert receipt["report_sha256"] == sha256_bytes(written)
    assert receipt["summary"] == report["summary"]
    assert "documents" not in receipt
    # No raw payload content or path leaks into stdout.
    assert CANARY not in result.stdout
    assert str(target) not in result.stdout
    assert "/api/a" not in result.stdout
    assert result.stdout.endswith("\n")


def test_output_summary_only_writes_selected_block(tmp_path):
    root = make_archive(tmp_path)
    target = tmp_path / "summary.json"
    result = run_cli("--input", str(root), "--summary-only", "--output", str(target))
    assert result.returncode == 0
    report = json.loads(target.read_text(encoding="utf-8"))
    assert set(report) == SUMMARY_ONLY_KEYS
    assert "documents" not in report
    assert CANARY not in json.dumps(report)
    # The receipt still carries the real pre-selection summary.
    receipt = json.loads(result.stdout)
    assert receipt["summary"] == report["summary"]
    assert receipt["report_sha256"] == sha256_bytes(target.read_bytes())


def test_output_creates_missing_parent_directories(tmp_path):
    root = make_archive(tmp_path)
    target = tmp_path / "deep" / "nested" / "report.json"
    result = run_cli("--input", str(root), "--output", str(target))
    assert result.returncode == 0
    assert json.loads(target.read_bytes())["schema_version"] == 1


def test_default_stdout_behavior_unchanged(tmp_path):
    root = make_archive(tmp_path)
    result = run_cli("--input", str(root))
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["documents"][0]["declarations"][0]["path"] == "/api/a"
    assert "report_sha256" not in out
    result_summary = run_cli("--input", str(root), "--summary-only")
    assert result_summary.returncode == 0
    assert set(json.loads(result_summary.stdout)) == SUMMARY_ONLY_KEYS


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "make_target",
    [
        pytest.param(lambda p: p.write_bytes(b"old"), id="existing-file"),
        pytest.param(lambda p: (p.mkdir(), (p / "x").write_bytes(b"y")), id="nonempty-dir"),
        pytest.param(lambda p: p.symlink_to("elsewhere.json"), id="dangling-symlink"),
    ],
)
def test_output_refuses_existing_paths_and_changes_nothing(tmp_path, make_target):
    root = make_archive(tmp_path)
    target = tmp_path / "report.json"
    make_target(target)
    before = target.read_bytes() if target.is_file() else None
    result = run_cli("--input", str(root), "--output", str(target))
    assert result.returncode == 2
    assert result.stderr == FAILURE_LINE
    assert result.stdout == ""
    if before is not None:
        assert target.read_bytes() == before
    assert no_stray_tempfiles(tmp_path)


def test_output_refuses_symlink_to_existing_file(tmp_path):
    root = make_archive(tmp_path)
    real = tmp_path / "real.json"
    real.write_bytes(b"precious")
    target = tmp_path / "report.json"
    target.symlink_to(real)
    result = run_cli("--input", str(root), "--output", str(target))
    assert result.returncode == 2
    assert result.stderr == FAILURE_LINE
    assert real.read_bytes() == b"precious"
    assert target.is_symlink()


def test_output_refuses_registry_contracts_file(tmp_path):
    root = make_archive(tmp_path)
    contracts = tmp_path / "contracts.json"
    contracts.write_bytes(b'{"schema_version": 1, "contracts": []}')
    result = run_cli(
        "--input", str(root), "--contracts", str(contracts), "--output", str(contracts)
    )
    assert result.returncode == 2
    assert result.stderr == FAILURE_LINE
    assert contracts.read_bytes() == b'{"schema_version": 1, "contracts": []}'


@pytest.mark.parametrize(
    "target_name",
    [
        pytest.param("report.json", id="new-file-in-archive"),
        pytest.param("sub/report.json", id="new-dir-in-archive"),
    ],
)
def test_output_inside_archive_rejected(tmp_path, target_name):
    root = make_archive(tmp_path)
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    target = root / target_name
    result = run_cli("--input", str(root), "--output", str(target))
    assert result.returncode == 2
    assert result.stderr == FAILURE_LINE
    assert result.stdout == ""
    after = sorted(p.relative_to(root) for p in root.rglob("*"))
    assert before == after


def test_output_catalog_path_itself_rejected(tmp_path):
    root = make_archive(tmp_path)
    catalog = (root / "catalog.json").read_bytes()
    result = run_cli("--input", str(root), "--output", str(root / "catalog.json"))
    assert result.returncode == 2
    assert (root / "catalog.json").read_bytes() == catalog


def test_invalid_input_writes_no_report(tmp_path):
    root = tmp_path / "archive"
    raw = write_raw(root, "raw/1.json", POST_A)
    raw["raw_file_sha256"] = "0" * 64
    write_catalog(root, [catalog_entry("domestic", "1", raw=raw)])
    target = tmp_path / "report.json"
    result = run_cli("--input", str(root), "--output", str(target))
    assert result.returncode == 2
    assert result.stderr == FAILURE_LINE
    assert not target.exists()
    assert no_stray_tempfiles(tmp_path)


def test_unwritable_parent_cleans_up(tmp_path):
    root = make_archive(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a dir")
    target = blocker / "report.json"
    result = run_cli("--input", str(root), "--output", str(target))
    assert result.returncode == 2
    assert result.stderr == FAILURE_LINE
    assert result.stdout == ""
    assert not target.exists()
    assert blocker.read_bytes() == b"not a dir"
    assert no_stray_tempfiles(tmp_path)
