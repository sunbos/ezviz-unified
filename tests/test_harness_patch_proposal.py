import hashlib
import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

STORAGE_REL = "src/ezviz_unified/storage_models.py"
RANGES_REL = "src/ezviz_unified/sub/ranges.py"
INPUT_SHA = "a" * 64
FIXED_ERR = "patch-proposal: validation failed\n"

STORAGE_SOURCE = (
    '"""Storage model fixture."""\n'
    "\n"
    "_SPACES_PAGE_LIMIT = 50\n"
    "_FILES_PAGE_LIMIT = 50\n"
    "\n"
    "\n"
    "def _spaces_query(page_size):\n"
    '    """Clamp domestic storage spaces pagination."""\n'
    "    limit = min(page_size, _SPACES_PAGE_LIMIT)\n"
    '    return {"spaces_page": limit}\n'
    "\n"
    "\n"
    "def _files_query(page_size):\n"
    '    """Clamp storage file pagination."""\n'
    "    limit = min(page_size, _FILES_PAGE_LIMIT)\n"
    '    return {"files_page": limit}\n'
)

RANGES_SOURCE = 'DATA = "abcdefg"\nOVERLAP = "aaaa"\n'

BAD_PATHS = [
    "../src/ezviz_unified/storage_models.py",
    "/abs/x.py",
    "src/ezviz_unified/../storage_models.py",
    "src/ezviz_unified//storage_models.py",
    "src/ezviz_unified/./storage_models.py",
    "src/ezviz_unified/sub/../storage_models.py",
    "src\\ezviz_unified\\storage_models.py",
    "src/ezviz_unified",
    "src/ezviz_unified/storage_models.txt",
    "contracts/reviewed-transport-contracts.json",
    "SRC/ezviz_unified/storage_models.py",
    "~/src/ezviz_unified/storage_models.py",
    "",
    7,
    None,
]


def module():
    assert Path("harness/patch_proposal.py").exists(), "Patch proposal module missing"
    return importlib.import_module("harness.patch_proposal")


def sha_path(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def context(tmp_path):
    mod = module()
    repo = tmp_path / "repo"
    storage = repo / STORAGE_REL
    storage.parent.mkdir(parents=True)
    storage.write_text(STORAGE_SOURCE, encoding="utf-8")
    ranges = repo / RANGES_REL
    ranges.parent.mkdir(parents=True)
    ranges.write_text(RANGES_SOURCE, encoding="utf-8")

    def edit(path, old, new, base=None):
        return {
            "path": path,
            "base_sha256": sha_path(repo / path) if base is None else base,
            "old": old,
            "new": new,
        }

    def proposal(edits, input_sha=INPUT_SHA, **extra):
        body = {"schema_version": 1, "input_sha256": input_sha, "edits": edits}
        body.update(extra)
        return body

    return SimpleNamespace(
        mod=mod,
        repo=repo,
        storage=storage,
        ranges=ranges,
        edit=edit,
        proposal=proposal,
        allow=[STORAGE_REL, RANGES_REL],
    )


def prepare(c, proposal, allow=None, **kwargs):
    kwargs.setdefault("input_sha256", INPUT_SHA)
    return c.mod.prepare_proposal(c.repo, proposal, c.allow if allow is None else allow, **kwargs)


def write_proposal(tmp_path, value):
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def cli_args(c, proposal_path, allow=(STORAGE_REL, RANGES_REL), extra=()):
    args = ["--repo", str(c.repo), "--proposal", str(proposal_path)]
    for rel in allow:
        args += ["--allow", rel]
    args += ["--input-sha256", INPUT_SHA]
    args += list(extra)
    return args


def test_exact_replacement_diff_new_sha_and_no_write(context):
    c = context
    edit = c.edit(STORAGE_REL, "_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    report = prepare(c, c.proposal([edit]))
    assert set(report) == {
        "schema_version",
        "evidence",
        "input_sha256",
        "files",
        "has_changes",
        "publication_authorized",
        "patch_applied",
        "real_api_used",
        "limitations",
    }
    assert report["schema_version"] == 1
    assert report["evidence"] == "proposal_shape_hash_and_syntax_only"
    assert report["input_sha256"] == INPUT_SHA
    assert report["has_changes"] is True
    assert report["publication_authorized"] is False
    assert report["patch_applied"] is False
    assert report["real_api_used"] is False
    assert report["limitations"]
    assert len(report["files"]) == 1
    entry = report["files"][0]
    assert set(entry) == {"path", "base_sha256", "new_sha256", "new_text", "diff"}
    expected = STORAGE_SOURCE.replace("_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    assert entry["path"] == STORAGE_REL
    assert entry["base_sha256"] == sha_path(c.storage)
    assert entry["new_sha256"] == sha_text(expected)
    assert entry["new_text"] == expected
    assert "--- a/" + STORAGE_REL in entry["diff"]
    assert "+++ b/" + STORAGE_REL in entry["diff"]
    assert "@@" in entry["diff"]
    assert "-_SPACES_PAGE_LIMIT = 50" in entry["diff"]
    assert "+_SPACES_PAGE_LIMIT = 20" in entry["diff"]
    # The repository file itself is never modified.
    assert c.storage.read_bytes() == STORAGE_SOURCE.encode("utf-8")


def test_adjacent_edits_apply_back_to_front_on_original(context):
    c = context
    # The earlier-range edit is listed after the later-range one on purpose:
    # application must be positional over the original text.
    edits = [
        c.edit(RANGES_REL, "efg", "123"),
        c.edit(RANGES_REL, "abc", "XYZ"),
    ]
    report = prepare(c, c.proposal(edits))
    entry = report["files"][0]
    assert entry["new_text"] == 'DATA = "XYZd123"\nOVERLAP = "aaaa"\n'
    assert "@@" in entry["diff"]
    assert c.ranges.read_bytes() == RANGES_SOURCE.encode("utf-8")


def test_second_edit_cannot_anchor_in_generated_text(context):
    c = context
    edits = [
        c.edit(RANGES_REL, "abc", "zbc"),
        c.edit(RANGES_REL, "zbc", "999"),
    ]
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal(edits))


def test_old_must_occur_exactly_once(context):
    c = context
    # Zero occurrences.
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([c.edit(STORAGE_REL, "NONEXISTENT_NEEDLE", "x")]))
    # Two non-overlapping occurrences in one file.
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([c.edit(STORAGE_REL, "limit = min(page_size", "x")]))
    # Overlapping occurrences: "aa" inside "aaaa" is three matches.
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([c.edit(RANGES_REL, "aa", "b")]))


def test_overlapping_replacement_ranges_rejected(context):
    c = context
    edits = [
        c.edit(RANGES_REL, "bcd", "X"),
        c.edit(RANGES_REL, "def", "Y"),
    ]
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal(edits))
    # Two identical edits collide on the same range as well.
    same = [c.edit(RANGES_REL, "bcd", "X"), c.edit(RANGES_REL, "bcd", "X")]
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal(same))


def test_same_file_edits_need_consistent_base(context):
    c = context
    edits = [
        c.edit(RANGES_REL, "abc", "X"),
        c.edit(RANGES_REL, "efg", "Y", base="0" * 64),
    ]
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal(edits))


def test_base_hash_mismatch(context):
    c = context
    edit = c.edit(
        STORAGE_REL,
        "_SPACES_PAGE_LIMIT = 50",
        "_SPACES_PAGE_LIMIT = 20",
        base="0" * 64,
    )
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([edit]))


def test_operator_input_hash_is_authoritative(context):
    c = context
    # A model echoing a different hash than the operator's is rejected.
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([], input_sha="b" * 64))
    # So is an operator hash that does not match the proposal's echo.
    with pytest.raises(c.mod.ProposalError):
        prepare(
            c,
            c.proposal([], input_sha=INPUT_SHA),
            input_sha256="b" * 64,
        )
    # Malformed operator hashes fail before any comparison is trusted.
    with pytest.raises(c.mod.ProposalError):
        c.mod.prepare_proposal(c.repo, c.proposal([]), c.allow, input_sha256="nope")


@pytest.mark.parametrize("bad", [5, None, True, ["a"], "B" * 64, "a" * 63])
def test_proposal_input_hash_must_echo_operator_value(context, bad):
    with pytest.raises(context.mod.ProposalError):
        prepare(context, context.proposal([], input_sha=bad))


def test_edit_path_must_be_in_allowlist(context):
    c = context
    edit = c.edit(RANGES_REL, "aaaa", "bbbb")
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([edit]), allow=[STORAGE_REL])
    # Canonical but neither allowed nor present is still an allowlist error.
    ghost = {
        "path": "src/ezviz_unified/ghost.py",
        "base_sha256": "0" * 64,
        "old": "x",
        "new": "y",
    }
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([ghost]))


@pytest.mark.parametrize("bad", BAD_PATHS)
def test_allowed_paths_must_be_canonical(context, bad):
    with pytest.raises(context.mod.ProposalError):
        prepare(
            context,
            context.proposal([]),
            allow=[STORAGE_REL, bad],
        )


@pytest.mark.parametrize("bad", BAD_PATHS)
def test_edit_path_must_be_canonical(context, bad):
    edit = {
        "path": bad,
        "base_sha256": "0" * 64,
        "old": "x",
        "new": "y",
    }
    with pytest.raises(context.mod.ProposalError):
        prepare(context, context.proposal([edit]))


@pytest.mark.parametrize(
    "allow",
    [
        [],
        STORAGE_REL,
        [STORAGE_REL, STORAGE_REL],
        [STORAGE_REL, 7],
        [b"src/ezviz_unified/storage_models.py"],
        [" "],
    ],
)
def test_allowlist_sequence_rules(context, allow):
    with pytest.raises(context.mod.ProposalError):
        prepare(context, context.proposal([]), allow=allow)


def test_allowlist_must_be_a_sequence(context):
    c = context
    with pytest.raises(c.mod.ProposalError):
        c.mod.prepare_proposal(c.repo, c.proposal([]), None, input_sha256=INPUT_SHA)
    with pytest.raises(c.mod.ProposalError):
        c.mod.prepare_proposal(c.repo, c.proposal([]), 42, input_sha256=INPUT_SHA)


def test_missing_allowlist_file_rejected(context):
    with pytest.raises(context.mod.ProposalError):
        prepare(
            context,
            context.proposal([]),
            allow=["src/ezviz_unified/ghost.py"],
        )


def test_directory_rejected_before_read(context):
    c = context
    fake = c.repo / "src/ezviz_unified/fakedir.py"
    fake.mkdir()
    rel = "src/ezviz_unified/fakedir.py"
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([]), allow=[rel])
    edit = {"path": rel, "base_sha256": "0" * 64, "old": "x", "new": "y"}
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([edit]), allow=[rel])


def test_external_symlink_rejected_before_read(context, tmp_path, monkeypatch):
    c = context
    outside = tmp_path / "outside.py"
    outside.write_text("PRIVATE_OUTSIDE = True\n")
    link = c.repo / "src/ezviz_unified/leak.py"
    link.symlink_to(outside)
    real_read = Path.read_bytes
    reads = []

    def spy(self, *args, **kwargs):
        reads.append(Path(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spy)
    rel = "src/ezviz_unified/leak.py"
    edit = {"path": rel, "base_sha256": "0" * 64, "old": "x", "new": "y"}
    with pytest.raises(c.mod.ProposalError) as exc:
        prepare(c, c.proposal([edit]), allow=[rel])
    assert "PRIVATE_OUTSIDE" not in str(exc.value)
    # Rejection happens before any file content is read at all.
    assert reads == []


def test_internal_symlink_is_not_a_regular_file(context):
    c = context
    alias = c.repo / "src/ezviz_unified/alias.py"
    alias.symlink_to(c.storage)
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([]), allow=["src/ezviz_unified/alias.py"])


def _spy_source_reads(monkeypatch):
    """Patch ``Path.read_bytes`` and return the list of read paths."""
    real_read = Path.read_bytes
    reads = []

    def spy(self, *args, **kwargs):
        reads.append(Path(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spy)
    return reads


def _escape_proposal():
    """Proposal aimed at the storage path inside an escaped package root."""
    return {
        "schema_version": 1,
        "input_sha256": INPUT_SHA,
        "edits": [
            {
                "path": STORAGE_REL,
                "base_sha256": "0" * 64,
                "old": "PRIVATE_EXTERNAL",
                "new": "x",
            }
        ],
    }


def test_pkg_root_symlink_escape_rejected_before_read(tmp_path, monkeypatch):
    mod = module()
    # ``src/ezviz_unified`` itself links to an external package directory.
    outside = tmp_path / "pkg_outside"
    outside.mkdir()
    secret = outside / "storage_models.py"
    secret.write_text("PRIVATE_EXTERNAL = True\n")
    repo = tmp_path / "pkg_link_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "ezviz_unified").symlink_to(outside, target_is_directory=True)
    reads = _spy_source_reads(monkeypatch)
    with pytest.raises(mod.ProposalError) as exc:
        mod.prepare_proposal(repo, _escape_proposal(), [STORAGE_REL], input_sha256=INPUT_SHA)
    assert "PRIVATE" not in str(exc.value)
    # Rejected before a single byte of source content is read.
    assert reads == []
    assert secret.read_bytes() == b"PRIVATE_EXTERNAL = True\n"


def test_src_parent_symlink_escape_rejected_before_read(tmp_path, monkeypatch):
    mod = module()
    # ``src`` links to an external tree that contains ``ezviz_unified/``.
    outside = tmp_path / "src_outside"
    pkg = outside / "ezviz_unified"
    pkg.mkdir(parents=True)
    secret = pkg / "storage_models.py"
    secret.write_text("PRIVATE_EXTERNAL = True\n")
    repo = tmp_path / "src_link_repo"
    repo.mkdir()
    (repo / "src").symlink_to(outside, target_is_directory=True)
    reads = _spy_source_reads(monkeypatch)
    with pytest.raises(mod.ProposalError) as exc:
        mod.prepare_proposal(repo, _escape_proposal(), [STORAGE_REL], input_sha256=INPUT_SHA)
    assert "PRIVATE" not in str(exc.value)
    assert reads == []
    assert secret.read_bytes() == b"PRIVATE_EXTERNAL = True\n"


def test_repo_root_symlink_alias_resolves_canonically(context, tmp_path):
    c = context
    # A symlinked path to the repository resolves to the real root; the SDK
    # package then sits inside it and remains valid.
    alias = tmp_path / "repo_alias"
    alias.symlink_to(c.repo, target_is_directory=True)
    edit = c.edit(STORAGE_REL, "_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    report = c.mod.prepare_proposal(alias, c.proposal([edit]), c.allow, input_sha256=INPUT_SHA)
    assert report["files"][0]["path"] == STORAGE_REL
    assert report["has_changes"] is True


def test_pkg_root_internal_symlink_stays_inside_repo(context):
    c = context
    # A package anchor resolving to another directory inside the repository
    # is legitimate and keeps working.
    real = c.repo / "internal_pkg"
    real.mkdir()
    target = real / "storage_models.py"
    target.write_text(STORAGE_SOURCE, encoding="utf-8")
    shutil.rmtree(c.repo / "src" / "ezviz_unified")
    (c.repo / "src" / "ezviz_unified").symlink_to(real, target_is_directory=True)
    edit = c.edit(STORAGE_REL, "_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    report = prepare(c, c.proposal([edit]), allow=[STORAGE_REL])
    assert report["files"][0]["path"] == STORAGE_REL
    assert report["files"][0]["base_sha256"] == sha_path(target)


def test_syntax_error_rejected_with_clean_chain(context):
    c = context
    edit = c.edit(
        STORAGE_REL,
        "def _spaces_query(page_size):",
        "def broken(:",
    )
    with pytest.raises(c.mod.ProposalError) as exc:
        prepare(c, c.proposal([edit]))
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_trailing_newline_required(context):
    c = context
    edit = c.edit(STORAGE_REL, '"files_page": limit}\n', '"files_page": limit}')
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([edit]))


def test_empty_new_is_a_valid_deletion(context):
    c = context
    edit = c.edit(RANGES_REL, 'OVERLAP = "aaaa"\n', "")
    report = prepare(c, c.proposal([edit]))
    assert report["files"][0]["new_text"] == 'DATA = "abcdefg"\n'


@pytest.mark.parametrize(
    "extra",
    [
        {"score": 0.9},
        {"passed": True},
        {"approved": True},
        {"publication_authorized": True},
        {"real_api_used": False},
        {"permissions": ["*"]},
        {"operator_override": {"allowed_paths": ["*"]}},
    ],
)
def test_proposal_extra_fields_rejected(context, extra):
    with pytest.raises(context.mod.ProposalError):
        prepare(context, context.proposal([], **extra))


def test_edit_fields_are_exact(context):
    c = context
    good = c.edit(STORAGE_REL, "_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    variants = [
        dict(good, score=0.5),
        dict(good, publication_authorized=True),
        dict(good, approved=True),
        {k: v for k, v in good.items() if k != "new"},
        {k: v for k, v in good.items() if k != "base_sha256"},
        {k: v for k, v in good.items() if k != "path"},
    ]
    for bad in variants:
        with pytest.raises(c.mod.ProposalError):
            prepare(c, c.proposal([bad]))


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 2, 0, None])
def test_schema_version_is_strictly_int_one(context, version):
    c = context
    proposal = c.proposal([])
    proposal["schema_version"] = version
    with pytest.raises(c.mod.ProposalError):
        prepare(c, proposal)


def test_empty_proposal_is_valid_no_changes(context):
    report = prepare(context, context.proposal([]))
    assert report["files"] == []
    assert report["has_changes"] is False
    assert report["evidence"] == "proposal_shape_hash_and_syntax_only"


@pytest.mark.parametrize(
    "bad",
    [None, [], "x", 42, {"schema_version": 1, "input_sha256": INPUT_SHA}],
)
def test_malformed_proposals(context, bad):
    with pytest.raises(context.mod.ProposalError):
        prepare(context, bad)


@pytest.mark.parametrize("edits", ["x", {"path": STORAGE_REL}, 7, None])
def test_edits_must_be_a_list(context, edits):
    with pytest.raises(context.mod.ProposalError):
        prepare(context, context.proposal(edits))


@pytest.mark.parametrize(
    "bad_edit",
    [
        "x",
        [],
        {"path": STORAGE_REL, "base_sha256": "0" * 64, "old": "", "new": "y"},
        {"path": STORAGE_REL, "base_sha256": "0" * 64, "old": "x", "new": "x"},
        {"path": STORAGE_REL, "base_sha256": "0" * 64, "old": "x", "new": 5},
        {"path": STORAGE_REL, "base_sha256": "0" * 64, "old": 5, "new": "y"},
        {
            "path": STORAGE_REL,
            "base_sha256": "AB" * 32,
            "old": "x",
            "new": "y",
        },
        {"path": STORAGE_REL, "base_sha256": "0" * 63, "old": "x", "new": "y"},
        {"path": STORAGE_REL, "base_sha256": "g" * 64, "old": "x", "new": "y"},
        {"path": STORAGE_REL, "base_sha256": 0, "old": "x", "new": "y"},
    ],
)
def test_malformed_edits(context, bad_edit):
    with pytest.raises(context.mod.ProposalError):
        prepare(context, context.proposal([bad_edit]))


def test_non_utf8_source_rejected(context):
    c = context
    blob = c.repo / "src/ezviz_unified/blob.py"
    blob.write_bytes(b"x = \xff\xfe\n")
    edit = {
        "path": "src/ezviz_unified/blob.py",
        "base_sha256": sha_path(blob),
        "old": "x",
        "new": "y",
    }
    with pytest.raises(c.mod.ProposalError):
        prepare(
            c,
            c.proposal([edit]),
            allow=["src/ezviz_unified/blob.py"],
        )


def test_source_change_mid_validation_fails(context, monkeypatch):
    c = context
    edit = c.edit(STORAGE_REL, "_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    real_read = Path.read_bytes
    calls = {"n": 0}

    def spy(self, *args, **kwargs):
        data = real_read(self, *args, **kwargs)
        if self.name == "storage_models.py":
            calls["n"] += 1
            if calls["n"] == 1:
                self.write_bytes(data + b"# drift\n")
        return data

    monkeypatch.setattr(Path, "read_bytes", spy)
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([edit]))


def test_report_size_budget_matches_real_serialization(context):
    c = context
    edit = c.edit(RANGES_REL, "aaaa", "bbbb")
    report = prepare(c, c.proposal([edit]))
    emitted = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    again = prepare(c, c.proposal([edit]), max_bytes=len(emitted))
    assert again == report
    with pytest.raises(c.mod.ProposalError):
        prepare(c, c.proposal([edit]), max_bytes=len(emitted) - 1)


@pytest.mark.parametrize("bad", [True, 0, -1, 1.5, "4096", None])
def test_max_bytes_is_strict_positive_int(context, bad):
    with pytest.raises(context.mod.ProposalError):
        prepare(context, context.proposal([]), max_bytes=bad)


def test_package_subdirectory_paths_allowed(context):
    c = context
    report = prepare(c, c.proposal([c.edit(RANGES_REL, "aaaa", "bbbb")]))
    assert report["files"][0]["path"] == RANGES_REL
    assert report["files"][0]["new_text"] == 'DATA = "abcdefg"\nOVERLAP = "bbbb"\n'


def test_multi_file_proposal(context):
    c = context
    edits = [
        c.edit(STORAGE_REL, "_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20"),
        c.edit(RANGES_REL, "aaaa", "bbbb"),
    ]
    report = prepare(c, c.proposal(edits))
    assert [f["path"] for f in report["files"]] == sorted([STORAGE_REL, RANGES_REL])
    by_path = {f["path"]: f for f in report["files"]}
    assert by_path[RANGES_REL]["new_text"] == 'DATA = "abcdefg"\nOVERLAP = "bbbb"\n'
    assert by_path[STORAGE_REL]["new_text"].count("_SPACES_PAGE_LIMIT = 20") == 1
    assert report["has_changes"] is True


def test_cli_success(context, capsys, tmp_path):
    c = context
    edit = c.edit(STORAGE_REL, "_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    pf = write_proposal(tmp_path, c.proposal([edit]))
    status = c.mod.main(cli_args(c, pf))
    out = capsys.readouterr()
    assert status == 0 and out.err == ""
    report = json.loads(out.out)
    assert report["evidence"] == "proposal_shape_hash_and_syntax_only"
    expected = STORAGE_SOURCE.replace("_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    assert report["files"][0]["new_sha256"] == sha_text(expected)


def test_cli_max_bytes_counts_real_stdout(context, capsys, tmp_path):
    c = context
    edit = c.edit(STORAGE_REL, "_SPACES_PAGE_LIMIT = 50", "_SPACES_PAGE_LIMIT = 20")
    pf = write_proposal(tmp_path, c.proposal([edit]))
    base = cli_args(c, pf)
    assert c.mod.main(base) == 0
    emitted = capsys.readouterr().out.encode("utf-8")
    assert len(emitted) > 0
    limit = len(emitted)
    assert c.mod.main(base + ["--max-bytes", str(limit)]) == 0
    out = capsys.readouterr()
    assert out.out.encode("utf-8") == emitted
    assert c.mod.main(base + ["--max-bytes", str(limit - 1)]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR


def test_cli_proposal_file_byte_cap(context, capsys, tmp_path):
    c = context
    # Trailing whitespace stays valid JSON while inflating the file.
    payload = json.dumps(c.proposal([])) + " " * 4096
    pf = tmp_path / "proposal.json"
    pf.write_text(payload, encoding="utf-8")
    size = pf.stat().st_size
    base = cli_args(c, pf)
    # A file exactly at the cap loads (limit+1 probing) and the smaller
    # report still fits.
    assert c.mod.main(base + ["--max-bytes", str(size)]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert c.mod.main(base + ["--max-bytes", str(size - 1)]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version": 1, "schema_version": 1, "input_sha256": "'
        + INPUT_SHA
        + '", "edits": []}',
        '{"schema_version": NaN, "input_sha256": "' + INPUT_SHA + '", "edits": []}',
        '{"schema_version": 1e400, "input_sha256": "' + INPUT_SHA + '", "edits": []}',
        '{"schema_version": 1, "input_sha256": "' + INPUT_SHA + '", "edits": [],}',
        '{"schema_version": 1, "input_sha256": ',
    ],
)
def test_cli_strict_json(context, capsys, tmp_path, raw):
    pf = tmp_path / "proposal.json"
    pf.write_text(raw, encoding="utf-8")
    assert context.mod.main(cli_args(context, pf)) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR


def test_cli_bad_utf8_rejected(context, capsys, tmp_path):
    pf = tmp_path / "proposal.json"
    pf.write_bytes(b'{"schema_version": 1\xff}')
    assert context.mod.main(cli_args(context, pf)) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR


def test_cli_error_is_fixed_and_never_leaks(context, capsys, tmp_path):
    c = context
    edit = c.edit(STORAGE_REL, "PRIVATE_NEEDLE", "x")
    pf = write_proposal(tmp_path, c.proposal([edit]))
    assert c.mod.main(cli_args(c, pf)) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR
    assert "PRIVATE" not in out.err

    pf.write_text('{"edits": "PRIVATE_BLOB",,,}', encoding="utf-8")
    assert c.mod.main(cli_args(c, pf)) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR
    assert "PRIVATE" not in out.err


def test_cli_missing_args_all_fail_fixed(context, capsys, tmp_path):
    c = context
    pf = write_proposal(tmp_path, c.proposal([]))
    cases = [
        # no --allow
        [
            "--repo",
            str(c.repo),
            "--proposal",
            str(pf),
            "--input-sha256",
            INPUT_SHA,
        ],
        # no --proposal
        [
            "--repo",
            str(c.repo),
            "--allow",
            STORAGE_REL,
            "--input-sha256",
            INPUT_SHA,
        ],
        # no --input-sha256
        [
            "--repo",
            str(c.repo),
            "--proposal",
            str(pf),
            "--allow",
            STORAGE_REL,
        ],
    ]
    for args in cases:
        assert c.mod.main(args) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR * 3


def test_cli_bad_max_bytes_string(context, capsys, tmp_path):
    c = context
    pf = write_proposal(tmp_path, c.proposal([]))
    assert c.mod.main(cli_args(c, pf, extra=["--max-bytes", "abc"])) == 2
    assert c.mod.main(cli_args(c, pf, extra=["--max-bytes", "0"])) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR * 2


def test_cli_empty_edits_exit_zero(context, capsys, tmp_path):
    c = context
    pf = write_proposal(tmp_path, c.proposal([]))
    assert c.mod.main(cli_args(c, pf)) == 0
    out = capsys.readouterr()
    assert out.err == ""
    report = json.loads(out.out)
    assert report["files"] == []
    assert report["has_changes"] is False
    assert report["publication_authorized"] is False


def test_cli_deep_json_recursion_fixed_error(context, capsys, tmp_path):
    # Deeply nested JSON raises RecursionError inside json.loads; the broad
    # CLI catch must still emit the fixed error line and nothing else.
    pf = tmp_path / "proposal.json"
    pf.write_text("[" * 50000 + "]" * 50000, encoding="utf-8")
    assert context.mod.main(cli_args(context, pf)) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == FIXED_ERR
