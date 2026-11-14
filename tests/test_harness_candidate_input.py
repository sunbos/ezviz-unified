import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    assert Path("harness/candidate_input.py").exists(), "Candidate input builder missing"
    return importlib.import_module("harness.candidate_input")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def read_json(path):
    return json.loads(path.read_text())


def sha_path(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def context(tmp_path, monkeypatch):
    mod = module()
    repo = tmp_path / "repo"
    before = tmp_path / "before"
    after = tmp_path / "after"
    devices = repo / "src/ezviz_unified/devices.py"
    devices.parent.mkdir(parents=True)
    devices.write_text("# devices fixture, never executed\n")
    extra = repo / "src/ezviz_unified/extra.py"
    extra.write_text("# extra fixture, never executed\n")

    def archived(root, region, doc_id, html, **kw):
        raw_rel = f"pages/{region}/{doc_id}.source.json"
        write_json(root / raw_rel, {"html_content": html, "format": "fixture"})
        row = {
            "region": region,
            "doc_id": doc_id,
            "source_url": f"https://docs.example.invalid/{doc_id}",
            "status": "archived",
            "raw_file": raw_rel,
            "raw_file_sha256": sha_path(root / raw_rel),
            "content_sha256": sha_text(html),
            "memberships": [],
        }
        row.update(kw)
        return row

    before_rows = [
        archived(before, "domestic", "1", "<p>before one</p>", instruction="PRIVATE_HINT"),
        archived(before, "domestic", "2", "<p>before two</p>"),
        archived(before, "domestic", "4", "<p>before four</p>"),
    ]
    after_rows = [
        archived(after, "domestic", "1", "<p>after one</p>"),
        archived(after, "domestic", "3", "<p>after three</p>", synthetic_fixture=True),
        {
            "region": "domestic",
            "doc_id": "4",
            "source_url": "https://docs.example.invalid/4",
            "status": "source_missing",
            "memberships": [],
        },
    ]
    write_json(before / "catalog.json", before_rows)
    write_json(after / "catalog.json", after_rows)
    write_json(before / "derivation-summary.json", {"snapshot": "before"})
    write_json(after / "derivation-summary.json", {"snapshot": "after"})

    contracts_path = repo / "contracts/reviewed-transport-contracts.json"
    write_json(
        contracts_path,
        {
            "schema_version": 1,
            "contracts": [
                {
                    "operation_id": "cn.alpha",
                    "region": "domestic",
                    "source_doc_id": "1",
                    "source_url": "https://docs.example.invalid/1",
                    "source_content_sha256": "a" * 64,
                },
                {
                    "operation_id": "cn.beta",
                    "region": "domestic",
                    "source_doc_id": "1",
                    "source_url": "https://docs.example.invalid/1",
                    "source_content_sha256": "b" * 64,
                },
                {
                    "operation_id": "cn.gamma",
                    "region": "domestic",
                    "source_doc_id": "2",
                    "source_url": "https://docs.example.invalid/2",
                    "source_content_sha256": "c" * 64,
                },
            ],
        },
    )
    bindings_path = repo / "contracts/sdk-bindings.json"
    write_json(bindings_path, {"schema_version": 1, "bindings": []})
    issues_path = repo / "contracts/source-issues.json"
    write_json(issues_path, {"schema_version": 1, "issues": []})
    cases_path = repo / "evals/contracts/cases.json"
    write_json(cases_path, {"schema_version": 1, "cases": []})

    def op(oid):
        return {
            "operation_id": oid,
            "module": "src/ezviz_unified/devices.py",
            "symbols": ["Devices." + oid],
            "argument_constants": {"scope": "all"},
            "public_entry": "Client.call",
        }

    def task(doc_id, ops):
        return {
            "task_id": f"domestic:{doc_id}",
            "region": "domestic",
            "doc_id": doc_id,
            "changes": ["source_changed"],
            "before": None,
            "after": None,
            "kind": "content_review",
            "review_scope": "bound_operations" if ops else "unmapped_source",
            "operations": ops,
            "source_issues": [
                {"id": f"i{doc_id}", "status": "open", "description": "d", "sdk_handling": "h"}
            ],
            "required_checks": ["semantic_source_review"],
        }

    tasks = [
        task("1", [op("cn.alpha"), op("cn.beta")]),
        task("2", [op("cn.gamma")]),
        task("3", []),
        task("4", []),
    ]

    def source_map():
        sdk = repo / "src/ezviz_unified"
        return {p.relative_to(repo).as_posix(): sha_path(p) for p in sorted(sdk.rglob("*.py"))}

    def review():
        return {
            "schema_version": 1,
            "evidence": "offline_maintenance_review_packet",
            "decision": "review_required",
            "conformance": {
                "evidence": "offline_contract_conformance_only",
                "passed": True,
                "coverage_complete": True,
                "summary": {"contracts": 3},
                "guard_failure": None,
                "failed_operations": [],
            },
            "review_plan": {
                "schema_version": 1,
                "evidence": "document_changes_and_declared_bindings_only",
                "needs_review": True,
                "tasks": tasks,
                "metadata_only_documents": [],
            },
            "inputs_sha256": {
                "before_catalog": sha_path(before / "catalog.json"),
                "before_derivation": sha_path(before / "derivation-summary.json"),
                "after_catalog": sha_path(after / "catalog.json"),
                "after_derivation": sha_path(after / "derivation-summary.json"),
                "contracts": sha_path(contracts_path),
                "bindings": sha_path(bindings_path),
                "issues": sha_path(issues_path),
                "cases": sha_path(cases_path),
            },
            "source_files_sha256": source_map(),
            "publication_authorized": False,
            "candidate_generated": False,
            "patch_applied": False,
            "real_api_used": False,
            "limitations": [],
        }

    calls = []

    def fake_run_review(*args, **kwargs):
        calls.append((args, kwargs))
        return review()

    monkeypatch.setattr(mod, "run_review", fake_run_review)
    return SimpleNamespace(
        mod=mod,
        repo=repo,
        before=before,
        after=after,
        devices=devices,
        extra=extra,
        tasks=tasks,
        calls=calls,
        review=review,
        contracts_path=contracts_path,
        bindings_path=bindings_path,
        issues_path=issues_path,
        cases_path=cases_path,
    )


def build(ctx, ids, **kwargs):
    return ctx.mod.build_candidate_input(ctx.repo, ctx.before, ctx.after, ids, **kwargs)


def test_packet_shape_single_review_call_and_no_writes(context):
    c = context
    original = {
        p: p.read_bytes()
        for root in (c.repo, c.before, c.after)
        for p in root.rglob("*")
        if p.is_file()
    }
    packet = build(c, ["domestic:1"])
    assert len(c.calls) == 1
    args, kwargs = c.calls[0]
    assert args == (c.repo, c.before, c.after)
    assert packet["schema_version"] == 1
    assert packet["evidence"] == "verified_candidate_input_only"
    for flag in (
        "publication_authorized",
        "candidate_generated",
        "patch_applied",
        "real_api_used",
    ):
        assert packet[flag] is False
    assert packet["baseline_conformance"]["passed"] is True
    assert packet["review_inputs_sha256"]["after_catalog"] == sha_path(c.after / "catalog.json")
    assert packet["source_manifest"]["src/ezviz_unified/devices.py"] == sha_path(c.devices)
    assert packet["limitations"]
    assert all(p.read_bytes() == data for p, data in original.items())


def test_same_page_operations_contracts_and_task_fields_kept(context):
    packet = build(context, ["domestic:1"])
    task = packet["tasks"][0]
    assert [o["operation_id"] for o in task["operations"]] == ["cn.alpha", "cn.beta"]
    assert task["operations"][0]["argument_constants"] == {"scope": "all"}
    assert task["operations"][0]["public_entry"] == "Client.call"
    assert task["source_issues"][0]["id"] == "i1"
    assert [c["operation_id"] for c in packet["contracts"]] == ["cn.alpha", "cn.beta"]
    assert [f["path"] for f in packet["source_files"]] == ["src/ezviz_unified/devices.py"]


def test_document_views_hashes_and_no_catalog_leak(context):
    c = context
    packet = build(c, ["domestic:1"])
    entry = packet["documents"][0]
    assert entry["task_id"] == "domestic:1"
    before_view = entry["before"]
    assert before_view["status"] == "archived"
    assert before_view["html_content"] == "<p>before one</p>"
    assert before_view["raw_file_sha256"] == sha_path(c.before / "pages/domestic/1.source.json")
    assert before_view["content_sha256"] == sha_text("<p>before one</p>")
    allowed = {
        "source_url",
        "status",
        "raw_file",
        "raw_file_sha256",
        "content_sha256",
        "html_content",
        "synthetic_fixture",
    }
    assert set(before_view) <= allowed
    assert "PRIVATE_HINT" not in json.dumps(packet["documents"], ensure_ascii=False)


def test_removed_added_and_missing_document_sides(context):
    packet = build(context, ["domestic:2", "domestic:3", "domestic:4"])
    removed, added, missing = packet["documents"]
    assert removed["before"]["html_content"] == "<p>before two</p>"
    assert removed["after"] is None
    assert added["before"] is None
    assert added["after"]["html_content"] == "<p>after three</p>"
    assert added["after"]["synthetic_fixture"] is True
    assert missing["after"]["status"] == "source_missing"
    assert missing["after"]["html_content"] is None
    assert missing["before"]["html_content"] == "<p>before four</p>"


def test_unbound_task_has_no_sdk_files_or_contracts(context):
    packet = build(context, ["domestic:4"])
    assert packet["source_files"] == []
    assert packet["contracts"] == []


def test_extra_module_added_once(context):
    packet = build(
        context,
        ["domestic:1"],
        extra_modules=[
            "src/ezviz_unified/extra.py",
            "src/ezviz_unified/devices.py",
        ],
    )
    paths = [f["path"] for f in packet["source_files"]]
    assert paths == ["src/ezviz_unified/devices.py", "src/ezviz_unified/extra.py"]
    by_path = {f["path"]: f for f in packet["source_files"]}
    assert by_path["src/ezviz_unified/extra.py"]["text"].startswith("# extra fixture")
    assert by_path["src/ezviz_unified/devices.py"]["sha256"] == sha_path(context.devices)


@pytest.mark.parametrize(
    "ids",
    [[], "domestic:1", ["domestic:1", "domestic:1"], [""], [7], [b"domestic:1"]],
)
def test_invalid_task_selectors(context, ids):
    with pytest.raises(context.mod.CandidateInputError):
        build(context, ids)


def test_unknown_task_selector(context):
    with pytest.raises(context.mod.CandidateInputError):
        build(context, ["domestic:99"])


@pytest.mark.parametrize(
    "module_name",
    [
        "src/ezviz_unified/ghost.py",
        "../devices.py",
        "contracts/source-issues.json",
        "src/ezviz_unified",
        "src\\ezviz_unified\\devices.py",
        "/abs.py",
        7,
    ],
)
def test_module_must_be_bound_and_verified(context, module_name):
    with pytest.raises(context.mod.CandidateInputError):
        build(context, ["domestic:1"], extra_modules=[module_name])


@pytest.mark.parametrize(
    "raw_file",
    [
        "../outside.source.json",
        "/tmp/outside.source.json",
        "pages\\evil.source.json",
        "pages/../1.source.json",
        "",
    ],
)
def test_raw_file_path_escape_rejected(context, monkeypatch, raw_file):
    c = context
    catalog = read_json(c.after / "catalog.json")
    catalog[0]["raw_file"] = raw_file
    write_json(c.after / "catalog.json", catalog)
    monkeypatch.setattr(c.mod, "run_review", lambda *a, **k: c.review())
    with pytest.raises(c.mod.CandidateInputError) as exc:
        build(c, ["domestic:1"])
    assert "PRIVATE" not in str(exc.value)


def test_external_symlink_rejected_before_read(context, monkeypatch, tmp_path):
    c = context
    outside = tmp_path / "outside.source.json"
    outside.write_text(json.dumps({"html_content": "PRIVATE_OUTSIDE"}))
    link = c.after / "pages/domestic/evil.source.json"
    link.symlink_to(outside)
    catalog = read_json(c.after / "catalog.json")
    catalog[0]["raw_file"] = "pages/domestic/evil.source.json"
    write_json(c.after / "catalog.json", catalog)
    monkeypatch.setattr(c.mod, "run_review", lambda *a, **k: c.review())
    with pytest.raises(c.mod.CandidateInputError) as exc:
        build(c, ["domestic:1"])
    assert "PRIVATE_OUTSIDE" not in str(exc.value)


def test_raw_body_and_html_hash_verified(context, monkeypatch):
    c = context
    raw = c.after / "pages/domestic/1.source.json"
    raw.write_text(json.dumps({"html_content": "<p>tampered</p>"}))
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])

    raw.write_text(json.dumps({"html_content": "<p>honest</p>"}))
    catalog = read_json(c.after / "catalog.json")
    catalog[0]["raw_file_sha256"] = sha_path(raw)
    write_json(c.after / "catalog.json", catalog)
    monkeypatch.setattr(c.mod, "run_review", lambda *a, **k: c.review())
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


def test_raw_body_without_html_string_rejected(context, monkeypatch):
    c = context
    raw = c.after / "pages/domestic/1.source.json"
    raw.write_text(json.dumps({"html_content": 5}))
    catalog = read_json(c.after / "catalog.json")
    catalog[0]["raw_file_sha256"] = sha_path(raw)
    write_json(c.after / "catalog.json", catalog)
    monkeypatch.setattr(c.mod, "run_review", lambda *a, **k: c.review())
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


@pytest.mark.parametrize(
    "fault",
    [
        "schema_true",
        "schema_float",
        "evidence",
        "decision",
        "conformance_evidence",
        "passed_int",
        "coverage_str",
        "guard",
        "guard_false",
        "guard_zero",
        "guard_empty",
        "guard_missing",
        "flag_int",
        "flag_true",
        "raise",
    ],
)
def test_review_baseline_cannot_be_forged(context, monkeypatch, fault):
    c = context

    def forged(*args, **kwargs):
        if fault == "raise":
            raise RuntimeError("PRIVATE_REVIEW_DETAIL")
        report = c.review()
        if fault == "schema_true":
            report["schema_version"] = True
        elif fault == "schema_float":
            report["schema_version"] = 1.0
        elif fault == "evidence":
            report["evidence"] = "offline_candidate_packet"
        elif fault == "decision":
            report["decision"] = "no_document_review"
        elif fault == "conformance_evidence":
            report["conformance"]["evidence"] = "offline_candidate_packet"
        elif fault == "passed_int":
            report["conformance"]["passed"] = 1
        elif fault == "coverage_str":
            report["conformance"]["coverage_complete"] = "true"
        elif fault == "guard":
            report["conformance"]["guard_failure"] = {"detail": "x"}
        elif fault == "guard_false":
            report["conformance"]["guard_failure"] = False
        elif fault == "guard_zero":
            report["conformance"]["guard_failure"] = 0
        elif fault == "guard_empty":
            report["conformance"]["guard_failure"] = ""
        elif fault == "guard_missing":
            del report["conformance"]["guard_failure"]
        elif fault == "flag_int":
            report["publication_authorized"] = 0
        elif fault == "flag_true":
            report["real_api_used"] = True
        return report

    monkeypatch.setattr(c.mod, "run_review", forged)
    with pytest.raises(c.mod.CandidateInputError) as exc:
        build(c, ["domestic:1"])
    assert "PRIVATE_REVIEW_DETAIL" not in str(exc.value)


def test_registry_change_mid_packaging_fails(context, monkeypatch):
    c = context
    real_load = c.mod._load
    calls = {"n": 0}

    def wrapped(root, relative):
        calls["n"] += 1
        if calls["n"] == 5:
            c.issues_path.write_bytes(c.issues_path.read_bytes() + b" ")
        return real_load(root, relative)

    monkeypatch.setattr(c.mod, "_load", wrapped)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


def test_snapshot_index_change_mid_packaging_fails(context, monkeypatch):
    c = context
    real_rsf = c.mod._read_snapshot_file
    calls = {"n": 0}

    def wrapped(root, name, label):
        calls["n"] += 1
        if calls["n"] == 5:
            target = c.after / "catalog.json"
            target.write_bytes(target.read_bytes() + b" ")
        return real_rsf(root, name, label)

    monkeypatch.setattr(c.mod, "_read_snapshot_file", wrapped)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


def test_sdk_change_mid_packaging_fails(context, monkeypatch):
    c = context
    real_snapshot = c.mod._source_snapshot
    calls = {"n": 0}

    def wrapped(root):
        calls["n"] += 1
        if calls["n"] == 2:
            c.devices.write_text(c.devices.read_text() + "# tampered\n")
        return real_snapshot(root)

    monkeypatch.setattr(c.mod, "_source_snapshot", wrapped)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


def test_selected_raw_file_change_mid_packaging_fails(context, monkeypatch):
    c = context
    real_pinned = c.mod._pinned_file
    calls = {"n": 0}

    def wrapped(root, relative, label):
        calls["n"] += 1
        if calls["n"] == 3:
            target = c.before / "pages/domestic/1.source.json"
            target.write_text(json.dumps({"html_content": "<p>changed</p>"}))
        return real_pinned(root, relative, label)

    monkeypatch.setattr(c.mod, "_pinned_file", wrapped)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


def test_packet_size_limit_against_real_stdout_bytes(context, capsys):
    c = context
    base_args = [
        "--repo",
        str(c.repo),
        "--before",
        str(c.before),
        "--after",
        str(c.after),
        "--task",
        "domestic:1",
    ]
    status = c.mod.main(base_args)
    out = capsys.readouterr()
    assert status == 0 and out.err == ""
    emitted = out.out.encode("utf-8")
    limit = len(emitted)
    assert limit > 0

    # Exactly the emitted byte count still passes.
    status = c.mod.main(base_args + ["--max-bytes", str(limit)])
    out = capsys.readouterr()
    assert status == 0
    assert out.out.encode("utf-8") == emitted

    # One byte short of the real stdout output rejects with empty stdout.
    status = c.mod.main(base_args + ["--max-bytes", str(limit - 1)])
    out = capsys.readouterr()
    assert status == 2
    assert out.out == ""
    assert out.err == "candidate-input: validation failed\n"


@pytest.mark.parametrize("bad", [True, 0, -5, 1.5, "4096"])
def test_max_bytes_must_be_strict_positive_int(context, bad):
    with pytest.raises(context.mod.CandidateInputError):
        build(context, ["domestic:1"], max_bytes=bad)


def test_cli_success_emits_complete_json(context, capsys):
    c = context
    status = c.mod.main(
        [
            "--repo",
            str(c.repo),
            "--before",
            str(c.before),
            "--after",
            str(c.after),
            "--task",
            "domestic:1",
            "--extra-module",
            "src/ezviz_unified/extra.py",
            "--timeout",
            "30",
            "--max-bytes",
            "2097152",
        ]
    )
    out = capsys.readouterr()
    assert status == 0 and out.err == ""
    packet = json.loads(out.out)
    assert packet["evidence"] == "verified_candidate_input_only"
    assert [f["path"] for f in packet["source_files"]] == [
        "src/ezviz_unified/devices.py",
        "src/ezviz_unified/extra.py",
    ]


def test_cli_failure_is_fixed_message_only(context, monkeypatch, capsys):
    c = context

    def boom(*args, **kwargs):
        raise RuntimeError("PRIVATE_SECRET")

    monkeypatch.setattr(c.mod, "run_review", boom)
    status = c.mod.main(
        [
            "--repo",
            str(c.repo),
            "--before",
            str(c.before),
            "--after",
            str(c.after),
            "--task",
            "domestic:1",
        ]
    )
    out = capsys.readouterr()
    assert status == 2
    assert out.out == ""
    assert out.err == "candidate-input: validation failed\n"
    assert "PRIVATE_SECRET" not in out.err


def test_cli_requires_at_least_one_task(context, capsys):
    status = context.mod.main(
        [
            "--repo",
            str(context.repo),
            "--before",
            str(context.before),
            "--after",
            str(context.after),
        ]
    )
    out = capsys.readouterr()
    assert status == 2
    assert out.out == ""
    assert out.err == "candidate-input: validation failed\n"


def test_contracts_cover_each_bound_operation_exactly_once(context):
    c = context
    registry = read_json(c.contracts_path)

    # A bound operation with no reviewed contract must fail the whole build.
    missing = dict(registry)
    missing["contracts"] = [
        entry for entry in registry["contracts"] if entry["operation_id"] != "cn.beta"
    ]
    write_json(c.contracts_path, missing)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])

    # A duplicate contract for a bound operation fails too — never partial.
    duplicated = dict(registry)
    duplicated["contracts"] = registry["contracts"] + [dict(registry["contracts"][0])]
    write_json(c.contracts_path, duplicated)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


def test_duplicate_review_task_ids_rejected(context, monkeypatch):
    c = context

    def forged(*args, **kwargs):
        report = c.review()
        report["review_plan"]["tasks"].append(dict(report["review_plan"]["tasks"][0]))
        return report

    monkeypatch.setattr(c.mod, "run_review", forged)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


@pytest.mark.parametrize(
    "row",
    [
        "junk",
        {"region": "domestic"},
        {"region": 7, "doc_id": "9"},
    ],
)
def test_malformed_catalog_rows_rejected(context, row):
    c = context
    catalog = read_json(c.after / "catalog.json")
    catalog.append(row)
    write_json(c.after / "catalog.json", catalog)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


def test_unknown_document_status_rejected(context):
    c = context
    catalog = read_json(c.after / "catalog.json")
    catalog[0]["status"] = "pending_review"
    write_json(c.after / "catalog.json", catalog)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])


def test_task_absent_from_both_catalog_sides_fails(context, monkeypatch):
    c = context

    def forged(*args, **kwargs):
        report = c.review()
        ghost = dict(report["review_plan"]["tasks"][0])
        ghost["task_id"] = "domestic:ghost"
        ghost["doc_id"] = "ghost"
        ghost["operations"] = []
        report["review_plan"]["tasks"].append(ghost)
        return report

    monkeypatch.setattr(c.mod, "run_review", forged)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:ghost"])


def test_stage_failure_leaves_no_exception_chain(context, monkeypatch):
    c = context

    def boom(*args, **kwargs):
        raise RuntimeError("PRIVATE_MARKER")

    monkeypatch.setattr(c.mod, "run_review", boom)
    with pytest.raises(c.mod.CandidateInputError) as exc:
        build(c, ["domestic:1"])
    assert "PRIVATE_MARKER" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_bad_raw_body_leaves_no_exception_chain(context):
    c = context
    raw = c.after / "pages/domestic/1.source.json"
    raw.write_text('{"html_content": "PRIVATE_MARKER"')  # truncated JSON
    catalog = read_json(c.after / "catalog.json")
    catalog[0]["raw_file_sha256"] = sha_path(raw)
    write_json(c.after / "catalog.json", catalog)
    with pytest.raises(c.mod.CandidateInputError) as exc:
        build(c, ["domestic:1"])
    assert "PRIVATE_MARKER" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_sdk_dir_symlink_swap_rejected_before_read(context, monkeypatch):
    c = context
    # Repo-internal but non-SDK directory prepared as the swap target.
    decoy = c.repo / "decoy_sdk"
    decoy.mkdir()
    decoy_module = decoy / "devices.py"
    decoy_module.write_text("# decoy module; must never be read\n")

    real_read_bytes = Path.read_bytes
    reads = []

    def spy(self, *args, **kwargs):
        reads.append(Path(self))
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spy)

    real_snapshot = c.mod._source_snapshot
    calls = {"n": 0}

    def wrapped(root):
        result = real_snapshot(root)
        calls["n"] += 1
        if calls["n"] == 1:
            sdk_dir = c.repo / "src" / "ezviz_unified"
            sdk_dir.rename(c.repo / "src" / "ezviz_unified_real")
            sdk_dir.symlink_to(decoy, target_is_directory=True)
        return result

    monkeypatch.setattr(c.mod, "_source_snapshot", wrapped)
    with pytest.raises(c.mod.CandidateInputError):
        build(c, ["domestic:1"])
    assert all(p != decoy_module for p in reads)
    assert all(decoy not in p.parents for p in reads)
