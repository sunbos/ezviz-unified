import copy
import importlib
from pathlib import Path

import pytest


def module():
    assert Path("harness/review_plan.py").exists(), "Review planner missing"
    return importlib.import_module("harness.review_plan")


def row(doc="1", *, region="domestic", sha="a", status="archived"):
    return {
        "region": region,
        "doc_id": doc,
        "title": "Source",
        "status": status,
        "source_url": f"https://docs.example.invalid/{region}/{doc}",
        "content_sha256": sha * 64 if status == "archived" else None,
        "memberships": [{"category_path": ["API"]}],
    }


def inputs():
    before = [row(), row(region="overseas")]
    after = [row(sha="b"), row(region="overseas")]
    bindings = {
        "schema_version": 1,
        "bindings": [
            {
                "operation_id": "cn.first",
                "region": "domestic",
                "source_doc_id": "1",
                "module": "src/ezviz_unified/devices.py",
                "symbols": ["Devices.get"],
                "public_entry": "devices.get",
                "argument_constants": {"channel": 0},
            },
            {
                "operation_id": "cn.second",
                "region": "domestic",
                "source_doc_id": "1",
                "module": "src/ezviz_unified/devices.py",
                "symbols": ["Devices.get"],
                "public_entry": "devices.get",
                "argument_constants": {"channel": None},
            },
            {
                "operation_id": "global.first",
                "region": "overseas",
                "source_doc_id": "1",
                "module": "src/ezviz_unified/devices.py",
                "symbols": ["Devices.get"],
            },
        ],
    }
    issues = {
        "schema_version": 1,
        "issues": [
            {
                "id": "known",
                "region": "domestic",
                "source_doc_id": "1",
                "status": "observed_difference",
                "description": "Known source difference",
                "sdk_handling": "Explicit policy",
            },
            {
                "id": "pending",
                "region": "domestic",
                "source_doc_id": "1",
                "status": "unresolved_source_semantics",
                "description": "Unresolved field",
                "sdk_handling": "Preserve raw",
            },
        ],
    }
    return before, after, bindings, issues


def test_one_task_preserves_all_overloaded_operations_and_related_issues():
    values = inputs()
    saved = copy.deepcopy(values)
    report = module().plan_review(*values)
    assert values == saved
    assert report["needs_review"] and len(report["tasks"]) == 1
    task = report["tasks"][0]
    assert task["task_id"] == "domestic:1" and task["kind"] == "content_review"
    assert task["review_scope"] == "bound_operations"
    assert [x["operation_id"] for x in task["operations"]] == ["cn.first", "cn.second"]
    assert [x["argument_constants"]["channel"] for x in task["operations"]] == [0, None]
    assert [x["id"] for x in task["source_issues"]] == ["known", "pending"]
    assert task["operations"][0]["public_entry"] == "devices.get"
    assert "known_source_issue_review" in task["required_checks"]
    task["operations"][0]["symbols"].append("Changed.get")
    task["before"]["title"] = "Changed"
    assert values == saved


def test_shared_auth_change_requires_broader_review_not_claimed_call_graph():
    values = inputs()
    values[2]["bindings"][0].update(
        module="src/ezviz_unified/auth.py", symbols=["TokenManager.get_token"]
    )
    report = module().plan_review(*values)
    assert report["tasks"][0]["review_scope"] == "shared_core"
    assert "core_regression_review" in report["tasks"][0]["required_checks"]
    assert "safe_to_publish" not in report


def test_metadata_only_changes_do_not_invent_code_work():
    before, _, bindings, issues = inputs()
    after = copy.deepcopy(before)
    after[0]["title"] = "New title"
    after[0]["memberships"] = [{"category_path": ["New navigation"]}]
    report = module().plan_review(before, after, bindings, issues)
    assert not report["needs_review"] and not report["tasks"]
    assert len(report["metadata_only_documents"]) == 1


@pytest.mark.parametrize(
    "old,new,kind",
    [
        ([row()], [], "source_removed"),
        ([row()], [row(status="source_missing")], "source_unavailable"),
        ([row(status="source_missing")], [row()], "source_restored"),
        ([], [row()], "new_source"),
    ],
)
def test_source_states_produce_review_tasks_without_asserting_support(old, new, kind):
    report = module().plan_review(
        old, new, {"schema_version": 1, "bindings": []}, {"schema_version": 1, "issues": []}
    )
    task = report["tasks"][0]
    assert task["kind"] == kind and task["review_scope"] == "unmapped_source"
    assert not task["operations"]


def test_source_url_change_is_separate_from_content_change():
    before, _, bindings, issues = inputs()
    after = copy.deepcopy(before)
    after[0]["source_url"] = "https://docs.example.invalid/relocated/1"
    assert (
        module().plan_review(before, after, bindings, issues)["tasks"][0]["kind"]
        == "source_reference_changed"
    )


def test_unsupported_catalog_status_is_rejected():
    with pytest.raises(ValueError):
        module().plan_review(
            [row()],
            [row(status="source_empty")],
            {"schema_version": 1, "bindings": []},
            {"schema_version": 1, "issues": []},
        )


def test_result_is_deterministic_and_input_flags_have_no_authority():
    values = inputs()
    expected = module().plan_review(*values)
    for items in values[:2]:
        items.reverse()
        for item in items:
            item.update(needs_review=False, approved=True, safe_to_publish=True)
    values[2]["bindings"].reverse()
    values[3]["issues"].reverse()
    assert module().plan_review(*values) == expected
