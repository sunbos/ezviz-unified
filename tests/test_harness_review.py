import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    assert Path("harness/review.py").exists(), "Maintenance review runner missing"
    return importlib.import_module("harness.review")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def context(tmp_path, monkeypatch):
    mod = module()
    repo = tmp_path / "repo"
    before, after = tmp_path / "before", tmp_path / "after"
    sdk = repo / "src/ezviz_unified/example.py"
    sdk.parent.mkdir(parents=True)
    sdk.write_text("# unit fixture, never executed\n")
    entry = {
        "region": "domestic",
        "doc_id": "1",
        "title": "Source",
        "status": "archived",
        "source_url": "https://docs.example.invalid/1",
        "content_sha256": "a" * 64,
        "memberships": [],
    }
    for root in (before, after):
        write(root / "catalog.json", [entry])
        write(root / "derivation-summary.json", {})
    contract = {
        "operation_id": "cn.test",
        "region": "domestic",
        "source_doc_id": "1",
        "source_url": entry["source_url"],
        "source_content_sha256": entry["content_sha256"],
    }
    write(
        repo / "contracts/reviewed-transport-contracts.json",
        {"schema_version": 1, "contracts": [contract]},
    )
    write(
        repo / "contracts/sdk-bindings.json",
        {
            "schema_version": 1,
            "bindings": [
                {
                    "operation_id": "cn.test",
                    "region": "domestic",
                    "source_doc_id": "1",
                    "module": "src/ezviz_unified/devices.py",
                    "symbols": ["Devices.get"],
                }
            ],
        },
    )
    write(repo / "contracts/source-issues.json", {"schema_version": 1, "issues": []})
    cases = repo / "evals/contracts/cases.json"
    write(cases, {"schema_version": 1, "cases": []})
    order = []
    names = ("reviewed-transport-contracts.json", "sdk-bindings.json", "source-issues.json")

    def verified(root):
        order.append("verify:" + root.name)
        return {
            "passed": True,
            "verification_scope": "unit_fixture",
            "documents": 1,
            "archived_bodies": 1,
            "missing_body_classifications": {},
            "encoding_warning_count": 0,
            "input_catalog_sha256": sha(root / "catalog.json"),
            "derivation_summary_sha256": sha(root / "derivation-summary.json"),
        }

    def audited(root):
        order.append("audit")
        return {
            "registry": {"summary": {"contracts": 1}},
            "code": {"summary": {"bindings_checked": 1}},
            "inputs_sha256": {
                "contracts/" + name: sha(root / "contracts" / name) for name in names
            },
        }

    def evaluated(root, cases_path, *, timeout):
        order.append("eval")
        assert timeout == 20
        return {
            "schema_version": 1,
            "evidence": "offline_contract_conformance_only",
            "passed": True,
            "coverage_complete": True,
            "real_api_used": False,
            "summary": {
                "contracts": 1,
                "cases": 1,
                "executions": 8,
                "passed_executions": 8,
                "failed_executions": 0,
            },
            "results": [{"operation_id": "cn.test", "passed": True} for _ in range(8)],
            "inputs_sha256": {
                "contracts": sha(root / "contracts" / names[0]),
                "bindings": sha(root / "contracts" / names[1]),
                "cases": sha(cases),
            },
            "source_files_sha256": {"src/ezviz_unified/example.py": sha(sdk)},
        }

    monkeypatch.setattr(mod, "verify_snapshot", verified)
    monkeypatch.setattr(mod, "audit_repository", audited)
    monkeypatch.setattr(mod, "run_evaluation", evaluated)
    return SimpleNamespace(
        mod=mod,
        repo=repo,
        before=before,
        after=after,
        sdk=sdk,
        cases=cases,
        order=order,
        verified=verified,
        audited=audited,
        evaluated=evaluated,
    )


def run(ctx):
    return ctx.mod.run_review(ctx.repo, ctx.before, ctx.after, timeout=20)


def test_no_change_report_has_no_publication_permission(context):
    c = context
    original = {p: p.read_bytes() for p in c.repo.rglob("*") if p.is_file()}
    result = run(c)
    assert result["decision"] == "no_document_review"
    assert result["evidence"] == "offline_maintenance_review_packet"
    assert (
        result["publication_authorized"]
        is result["patch_applied"]
        is result["candidate_generated"]
        is False
    )
    assert result["real_api_used"] is False and not result["review_plan"]["tasks"]
    assert not (c.before / "verification-report.json").exists()
    assert not (c.after / "verification-report.json").exists()
    assert all(p.read_bytes() == data for p, data in original.items())
    assert c.order[:2] == ["verify:before", "verify:after"]
    assert c.order.index("eval") > c.order.index("audit")
    assert len(result["inputs_sha256"]) == 8


def test_source_change_produces_tasks_without_modifying_sdk(context):
    c = context
    doc = json.loads((c.after / "catalog.json").read_text())
    doc[0]["content_sha256"] = "b" * 64
    write(c.after / "catalog.json", doc)
    result = run(c)
    assert result["decision"] == "review_required"
    assert result["review_plan"]["tasks"][0]["operations"][0]["operation_id"] == "cn.test"
    assert result["conformance"]["passed"] is True


def test_metadata_change_is_not_reported_as_no_document_review(context):
    c = context
    doc = json.loads((c.after / "catalog.json").read_text())
    doc[0]["title"] = "Source (deprecated)"
    doc[0]["memberships"] = [{"category_path": ["Deprecated"]}]
    write(c.after / "catalog.json", doc)
    result = run(c)
    assert result["decision"] == "metadata_only"
    assert not result["review_plan"]["tasks"]
    metadata = result["review_plan"]["metadata_only_documents"][0]
    assert metadata["after_navigation"] == [{"category_path": ["Deprecated"]}]


@pytest.mark.parametrize("fault", ["failed", "wrong_hash", "exception", "not_boolean"])
def test_source_verification_failure_precedes_any_sdk_execution(context, monkeypatch, fault):
    c = context

    def bad(root):
        if fault == "exception":
            raise ValueError("PRIVATE_SOURCE_VALUE")
        report = c.verified(root)
        if fault == "failed":
            report.update(passed=False, errors=[{"detail": "PRIVATE_SOURCE_VALUE"}])
        elif fault == "not_boolean":
            report["passed"] = "true"
        else:
            report["input_catalog_sha256"] = "b" * 64
        return report

    monkeypatch.setattr(c.mod, "verify_snapshot", bad)
    with pytest.raises(c.mod.ReviewError) as exc:
        run(c)
    assert "eval" not in c.order and "PRIVATE_SOURCE_VALUE" not in str(exc.value)
    assert exc.value.__context__ is None


def test_registered_baseline_must_match_before_snapshot(context):
    c = context
    doc = json.loads((c.before / "catalog.json").read_text())
    doc[0]["content_sha256"] = "b" * 64
    write(c.before / "catalog.json", doc)
    with pytest.raises(c.mod.ReviewError):
        run(c)
    assert "eval" not in c.order


@pytest.mark.parametrize("flag", ["passed", "coverage_complete"])
def test_eval_failure_or_incomplete_coverage_is_not_no_change_success(context, monkeypatch, flag):
    c = context

    def failed(*args, **kwargs):
        result = c.evaluated(*args, **kwargs)
        result[flag] = False
        if flag == "passed":
            result["results"][0]["passed"] = False
        return result

    monkeypatch.setattr(c.mod, "run_evaluation", failed)
    result = run(c)
    assert result["decision"] == "baseline_failed"
    assert result["publication_authorized"] is False


@pytest.mark.parametrize(
    "target", ["contracts", "bindings", "issues", "cases", "catalog", "derivation", "sdk"]
)
def test_input_or_sdk_change_during_evaluation_invalidates_report(context, monkeypatch, target):
    c = context
    targets = {
        "contracts": c.repo / "contracts/reviewed-transport-contracts.json",
        "bindings": c.repo / "contracts/sdk-bindings.json",
        "issues": c.repo / "contracts/source-issues.json",
        "cases": c.cases,
        "catalog": c.after / "catalog.json",
        "derivation": c.before / "derivation-summary.json",
        "sdk": c.sdk,
    }

    def changed(*args, **kwargs):
        result = c.evaluated(*args, **kwargs)
        path = targets[target]
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(c.mod, "run_evaluation", changed)
    with pytest.raises(c.mod.ReviewError):
        run(c)


def test_report_cannot_claim_offline_execution_for_wrong_evidence(context, monkeypatch):
    c = context

    def wrong(*args, **kwargs):
        result = c.evaluated(*args, **kwargs)
        result.update(evidence="other_execution", real_api_used=True)
        return result

    monkeypatch.setattr(c.mod, "run_evaluation", wrong)
    with pytest.raises(c.mod.ReviewError):
        run(c)


def test_cli_failure_does_not_emit_source_contents(context, monkeypatch, capsys):
    c = context

    def fail(*args, **kwargs):
        raise c.mod.ReviewError("PRIVATE_SOURCE_VALUE")

    monkeypatch.setattr(c.mod, "run_review", fail)
    status = c.mod.main(["--repo", str(c.repo), "--before", str(c.before), "--after", str(c.after)])
    output = capsys.readouterr()
    assert status == 2 and not output.out
    assert "PRIVATE_SOURCE_VALUE" not in output.err and "Traceback" not in output.err
