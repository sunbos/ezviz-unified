"""Trusted regression tests for the R001 repair-oracle helpers.

Exercises ``validate_scope`` and ``adapt_regression_test`` from
``evals/maintenance/repair_cases/page_size_limit/oracle.py`` against the
real ``src/ezviz_unified/storage_models.py`` and
``tests/test_sdk_storage.py``. Part of the default suite, never shown to
the candidate model, and importable without an SDK install — the oracle
keeps its SDK imports lazy.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "repair_oracle_r001",
    ROOT / "evals" / "maintenance" / "repair_cases" / "page_size_limit" / "oracle.py",
)
oracle = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oracle)

BEFORE = (ROOT / "src" / "ezviz_unified" / "storage_models.py").read_text(encoding="utf-8")
REGRESSION = (ROOT / "tests" / "test_sdk_storage.py").read_text(encoding="utf-8")

BOUND_OLD = "if not 1 <= size <= 50:"
BOUND_NEW = "if not 1 <= size <= 20:"
FILE_BOUND = "if not 1 <= page_size <= 50:"
FILE_BOUND_NEW = "if not 1 <= page_size <= 20:"
MESSAGE_OLD = '"page_size must be within 1..50"'
MESSAGE_NEW = '"page_size must be within 1..20"'
RAISE_OLD = 'raise ValueError("page_size must be within 1..50")'


def accepted_candidate():
    """The single permitted patch: bound and message, once each, in _spaces_query."""
    assert BEFORE.count(BOUND_OLD) == 1
    candidate = BEFORE.replace(BOUND_OLD, BOUND_NEW, 1).replace(MESSAGE_OLD, MESSAGE_NEW, 1)
    assert candidate != BEFORE
    return candidate


def _replace_last(text, old, new):
    head, marker, tail = text.rpartition(old)
    assert marker, f"{old!r} not found"
    return head + new + tail


# --- validate_scope ----------------------------------------------------------


def test_scope_accepts_the_exact_contract_change():
    assert oracle.validate_scope(BEFORE, accepted_candidate()) is None


def test_scope_rejects_unchanged_candidate():
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, BEFORE)


def test_scope_rejects_unparseable_sources():
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope("def broken(:\n", BEFORE)
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, "def broken(:\n")


@pytest.mark.parametrize(
    "old,new",
    [
        # Value-equal but type-different collateral edits — the bug class that
        # motivated structural dumps: 50 == 50.0, 0 == False, 1 == True.
        ("_DOWNLOAD_MAX_IDS = 50", "_DOWNLOAD_MAX_IDS = 50.0"),
        ("sort_rule not in (0, 1)", "sort_rule not in (False, True)"),
        ("30 <= seconds <= 604800", "30 <= seconds <= 604800.0"),
    ],
)
def test_scope_rejects_collateral_value_equal_edits(old, new):
    assert old in BEFORE
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, accepted_candidate().replace(old, new, 1))


def test_scope_rejects_float_in_the_allowed_slot():
    candidate = BEFORE.replace(BOUND_OLD, "if not 1 <= size <= 20.0:", 1).replace(
        MESSAGE_OLD, MESSAGE_NEW, 1
    )
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, candidate)


def test_scope_rejects_wrong_missing_or_retyped_message():
    # Wrong replacement text.
    candidate = BEFORE.replace(BOUND_OLD, BOUND_NEW, 1).replace(
        MESSAGE_OLD, '"page_size must be within 1..25"', 1
    )
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, candidate)
    # Message left unchanged while the bound moves.
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, BEFORE.replace(BOUND_OLD, BOUND_NEW, 1))
    # Same words through a different node type (f-string -> JoinedStr).
    candidate = BEFORE.replace(BOUND_OLD, BOUND_NEW, 1).replace(
        RAISE_OLD, 'raise ValueError(f"page_size must be within 1..20")', 1
    )
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, candidate)


def test_scope_rejects_edits_outside_spaces_query():
    # The same-looking bound+message changed only in StorageFileQuery.__post_init__.
    candidate = _replace_last(
        BEFORE.replace(FILE_BOUND, FILE_BOUND_NEW, 1), MESSAGE_OLD, MESSAGE_NEW
    )
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, candidate)
    # The required edit plus an extra bound change elsewhere.
    candidate = accepted_candidate().replace(FILE_BOUND, FILE_BOUND_NEW, 1)
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, candidate)


def test_scope_rejects_global_string_replacement():
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(BEFORE, BEFORE.replace("50", "20"))


def test_scope_rejects_new_imports_and_helpers():
    for candidate in (
        "import os\n" + accepted_candidate(),
        accepted_candidate() + "\n\ndef _helper():\n    return 20\n",
        accepted_candidate().replace("import unicodedata", "import unicodedata\nimport base64", 1),
    ):
        with pytest.raises(oracle.ScopeViolation):
            oracle.validate_scope(BEFORE, candidate)


def test_scope_rejects_missing_or_ambiguous_reference_targets():
    renamed = BEFORE.replace("def _spaces_query(", "def _spaces_query_legacy(", 1)
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(renamed, accepted_candidate())

    no_bound = 'def _spaces_query():\n    raise ValueError("page_size must be within 1..50")\n'
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(no_bound, no_bound)

    ambiguous = (
        "def _spaces_query():\n"
        "    if not 1 <= a <= 50:\n"
        '        raise ValueError("page_size must be within 1..50")\n'
        "    if not 1 <= b <= 50:\n"
        '        raise ValueError("other")\n'
    )
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(ambiguous, ambiguous)


@pytest.mark.parametrize(
    "chain",
    [
        "2 <= size <= 50",  # wrong lower-bound literal
        "True <= size <= 50",  # value-equal but not a strict int
        "1 <= page_size <= 50",  # wrong middle operand
        "1 <= size < 50",  # not a pure <= chain
        "1 <= size <= 50 <= 100",  # extra comparator
        "1 <= size <= 50.0",  # float upper bound
        "1 <= size <= 60",  # wrong bound literal
    ],
)
def test_scope_rejects_wrong_shape_reference_bound(chain):
    reference = (
        "def _spaces_query():\n"
        f"    if not {chain}:\n"
        '        raise ValueError("page_size must be within 1..50")\n'
    )
    with pytest.raises(oracle.ScopeViolation):
        oracle.validate_scope(reference, reference)


def test_scope_parse_failures_have_no_context_and_leak_no_source_text():
    bad_source = "def broken(:\n    page_size = 50\n"
    for before, after in ((bad_source, BEFORE), (BEFORE, bad_source)):
        with pytest.raises(oracle.ScopeViolation) as caught:
            oracle.validate_scope(before, after)
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None
        message = str(caught.value)
        assert "broken" not in message and "page_size" not in message


def test_scope_mismatch_error_is_generic_and_leaks_no_candidate_text():
    assert FILE_BOUND in BEFORE
    candidate = BEFORE.replace(FILE_BOUND, FILE_BOUND_NEW, 1)
    with pytest.raises(oracle.ScopeViolation) as caught:
        oracle.validate_scope(BEFORE, candidate)
    assert caught.value.__context__ is None
    message = str(caught.value)
    for marker in ("page_size", "StorageFileQuery", "__post_init__", "1.."):
        assert marker not in message


# --- adapt_regression_test ----------------------------------------------------


def test_adapter_rewrites_only_the_spaces_page_size_literal():
    adapted = oracle.adapt_regression_test(REGRESSION)
    assert adapted != REGRESSION
    before_bytes = REGRESSION.encode("utf-8")
    after_bytes = adapted.encode("utf-8")
    assert len(before_bytes) == len(after_bytes)
    changed = [
        index for index, pair in enumerate(zip(before_bytes, after_bytes)) if pair[0] != pair[1]
    ]
    # '5' -> '2' inside the spaces "page_size": 50 literal; nothing else moves.
    assert len(changed) == 1
    assert after_bytes[changed[0]] == ord("2")
    assert '"page_size": 50' not in adapted and '"page_size": 20,' in adapted
    ast.parse(adapted)
    for kept in ('"page_size": 51', "range(51)", '["N"] * 51', "at.EVAL_"):
        assert kept in adapted

    def function_names(text):
        return {
            node.name
            for node in ast.walk(ast.parse(text))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    assert function_names(adapted) == function_names(REGRESSION)


def test_adapter_is_byte_exact_when_utf8_precedes_the_literal():
    prefix = "# 前置注释：汉字与 emoji 🙂 仅用于偏移测试\n"
    adapted = oracle.adapt_regression_test(prefix + REGRESSION)
    assert adapted == prefix + oracle.adapt_regression_test(REGRESSION)


def test_adapter_uses_byte_offsets_for_nonascii_on_the_anchor_line():
    # Non-ASCII text sits on the same line as, and before, the anchor
    # literal: a character-index rewrite would land on the wrong bytes.
    source = (
        "def test_documented_inclusive_bounds_and_no_extra_batch_limit(kind):\n"
        '    execute(kind, "spaces", kwargs={"备注": "汉字 🙂", "page_size": 50})\n'
    )
    adapted = oracle.adapt_regression_test(source)
    assert adapted == (
        "def test_documented_inclusive_bounds_and_no_extra_batch_limit(kind):\n"
        '    execute(kind, "spaces", kwargs={"备注": "汉字 🙂", "page_size": 20})\n'
    )
    ast.parse(adapted)


def test_adapter_rejects_duplicate_target_functions():
    function = (
        "def test_documented_inclusive_bounds_and_no_extra_batch_limit(kind):\n"
        '    execute(kind, "spaces", kwargs={"page_size": 50})\n'
    )
    with pytest.raises(oracle.OracleError):
        oracle.adapt_regression_test(function + function)


def test_adapter_rejects_missing_or_ambiguous_anchors():
    with pytest.raises(oracle.OracleError):
        oracle.adapt_regression_test("def test_something_else():\n    pass\n")
    function = "def test_documented_inclusive_bounds_and_no_extra_batch_limit(kind):\n"
    with pytest.raises(oracle.OracleError):
        oracle.adapt_regression_test(
            function + '    execute(kind, "spaces", kwargs={"page_size": 20})\n'
        )
    with pytest.raises(oracle.OracleError):
        oracle.adapt_regression_test(
            function
            + '    execute(kind, "spaces", kwargs={"page_size": 50})\n'
            + '    execute(kind, "spaces", kwargs={"page_size": 50})\n'
        )


def test_adapter_returns_text_and_never_writes():
    adapted = oracle.adapt_regression_test(REGRESSION)
    assert isinstance(adapted, str) and adapted != REGRESSION
    assert (ROOT / "tests" / "test_sdk_storage.py").read_text(encoding="utf-8") == REGRESSION
