"""Independent acceptance oracle for maintenance repair case R001.

Synthetic contract change: domestic ``cn.storage.spaces`` ``pageSize``
upper bound 50 -> 20; every other SDK behaviour must stay identical.

The trusted coordinator runs this module inside an isolated copy of the
repository, e.g.::

    python -m pytest evals/maintenance/repair_cases/page_size_limit/oracle.py

The SDK under test is whichever ``ezviz_unified`` the runner's environment
resolves; there is deliberately no ``sys.path`` fallback, so this module
can never silently import the original repository. SDK imports are lazy so
the static helpers stay importable without the SDK installed.

This file is never shown to the candidate model and the candidate may not
modify it. Any failure raised here is final: a candidate self-report of
"passed" cannot override it.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta

import httpx
import pytest


def _sdk():
    """Return the SDK selected by the runner's environment (isolated copy)."""
    from ezviz_unified import AsyncClient, Client, RegionConfig
    from ezviz_unified.storage_models import StorageFileQuery

    return AsyncClient, Client, RegionConfig, StorageFileQuery


class OracleError(Exception):
    """Acceptance failure; a candidate self-report cannot override it."""


class ScopeViolation(OracleError):
    """The candidate diff is not exactly the permitted R001 edit."""


# ---------------------------------------------------------------------------
# 1. Static scope validation: build the expected AST from the reference and
#    compare structural dumps. Sources are parsed, never executed.
# ---------------------------------------------------------------------------

_TARGET_FUNCTION = "_spaces_query"
_OLD_BOUND = 50
_NEW_BOUND = 20
_OLD_MESSAGE = "page_size must be within 1..50"
_NEW_MESSAGE = "page_size must be within 1..20"


def _is_int(node: ast.AST, value: int) -> bool:
    """Strict ``int`` literal check; ``bool`` and ``float`` never match."""
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value == value


def _expected_tree(before_source: str) -> ast.Module:
    """Parse the reference and apply the single permitted mutation in-AST.

    Requires exactly one top-level ``_spaces_query`` containing exactly one
    ``1 <= size <= 50`` chain — a strict ``int`` ``1``, the name ``size``,
    two ``<=`` operators and a strict ``int`` ``50``; no other shape
    qualifies — and exactly one ``ValueError`` argument
    ``"page_size must be within 1..50"``; both are rewritten to the new
    contract. Missing or ambiguous anchors raise :class:`ScopeViolation`,
    which also means an unmodified candidate can never pass.
    """
    try:
        tree = ast.parse(before_source)
    except SyntaxError:
        tree = None
    if tree is None:
        raise ScopeViolation("reference source does not parse")

    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == _TARGET_FUNCTION
    ]
    if len(functions) != 1:
        raise ScopeViolation(
            f"reference must contain exactly one top-level {_TARGET_FUNCTION}, "
            f"found {len(functions)}"
        )
    function = functions[0]

    bounds = [
        node.comparators[-1]
        for node in ast.walk(function)
        if isinstance(node, ast.Compare)
        and _is_int(node.left, 1)
        and len(node.ops) == 2
        and len(node.comparators) == 2
        and all(isinstance(op, ast.LtE) for op in node.ops)
        and isinstance(node.comparators[0], ast.Name)
        and node.comparators[0].id == "size"
        and _is_int(node.comparators[1], _OLD_BOUND)
    ]
    if len(bounds) != 1:
        raise ScopeViolation(
            f"reference must contain exactly one '1 <= size <= {_OLD_BOUND}' "
            f"<= chain inside {_TARGET_FUNCTION}, found {len(bounds)}"
        )
    messages = [
        arg
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ValueError"
        for arg in node.args
        if isinstance(arg, ast.Constant) and type(arg.value) is str and arg.value == _OLD_MESSAGE
    ]
    if len(messages) != 1:
        raise ScopeViolation(
            f"reference must contain exactly one {_OLD_MESSAGE!r} ValueError "
            f"argument inside {_TARGET_FUNCTION}, found {len(messages)}"
        )

    bounds[0].value = _NEW_BOUND
    messages[0].value = _NEW_MESSAGE
    return tree


def validate_scope(before_source: str, after_source: str) -> None:
    """Accept only the exact R001 edit and nothing else.

    The reference is parsed and the two permitted literals are rewritten in
    its AST (the ``50`` upper bound of the ``1 <= size <= 50`` chain ->
    ``20``, and the ``"page_size must be within 1..50"`` ``ValueError``
    argument -> ``"...1..20"``, each exactly once inside the single
    top-level ``_spaces_query``). The candidate is accepted iff
    ``ast.dump(..., include_attributes=False)`` of that expected tree
    equals the candidate's dump.

    Structural dump equality — not Python value equality — is what rejects
    collateral ``50`` -> ``50.0``/``True`` edits (``_DOWNLOAD_MAX_IDS`` and
    friends), ``kind`` or node-type changes, moved or duplicated
    definitions, new imports or helpers, edits in
    ``StorageFileQuery.__post_init__`` or anywhere else, and missing or
    ambiguous anchors. No global string replacement is used and the
    sources are never executed. Returns ``None`` or raises
    :class:`ScopeViolation`.
    """
    expected = _expected_tree(before_source)
    try:
        candidate = ast.parse(after_source)
    except SyntaxError:
        candidate = None
    if candidate is None:
        raise ScopeViolation("candidate source does not parse")

    expected_dump = ast.dump(expected, include_attributes=False)
    candidate_dump = ast.dump(candidate, include_attributes=False)
    if candidate_dump != expected_dump:
        raise ScopeViolation("candidate differs from the single permitted R001 edit")


# ---------------------------------------------------------------------------
# 2. Coordinator-side regression adaptation (never candidate output).
# ---------------------------------------------------------------------------

_REGRESSION_TEST = "test_documented_inclusive_bounds_and_no_extra_batch_limit"


def _byte_offset(lines: list[bytes], lineno: int, col_offset: int) -> int:
    """Absolute byte offset; AST ``col_offset`` values are UTF-8 bytes."""
    return sum(len(line) for line in lines[: lineno - 1]) + col_offset


def adapt_regression_test(source_text: str) -> str:
    """Return the test text with the ``spaces`` positive case at the new
    contract, byte-identical elsewhere.

    Inside ``test_documented_inclusive_bounds_and_no_extra_batch_limit``
    the ``execute(kind, "spaces", kwargs={..., "page_size": 50, ...})``
    literal is the single positive example and becomes ``20``. Every other
    ``50`` (file-node-id lists, the ``download_urls`` limit,
    ``StorageFileQuery`` bounds, the invalid-input matrix) is preserved
    byte-for-byte and no test is removed. Offsets are computed on the
    UTF-8 encoding because AST columns are byte offsets, not character
    indexes — non-ASCII text earlier in the file must not shift the
    rewrite. A missing or ambiguous anchor raises :class:`OracleError`.
    The adapted text is returned, never written; the coordinator applies
    it to the isolated copy only.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError as exc:
        raise OracleError(f"regression test source does not parse: {exc}") from exc

    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == _REGRESSION_TEST
    ]
    if len(functions) != 1:
        raise OracleError(
            f"expected exactly one top-level {_REGRESSION_TEST} "
            f"in supplied source, found {len(functions)}"
        )
    function = functions[0]

    anchors = [
        value
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "spaces"
        for keyword in node.keywords
        if keyword.arg == "kwargs" and isinstance(keyword.value, ast.Dict)
        for key, value in zip(keyword.value.keys, keyword.value.values)
        if isinstance(key, ast.Constant) and key.value == "page_size" and _is_int(value, _OLD_BOUND)
    ]
    if len(anchors) != 1:
        raise OracleError(
            f"expected exactly one spaces 'page_size': {_OLD_BOUND} literal "
            f"inside {_REGRESSION_TEST}, found {len(anchors)}"
        )

    literal = anchors[0]
    source_bytes = source_text.encode("utf-8")
    lines = source_bytes.splitlines(keepends=True)
    start = _byte_offset(lines, literal.lineno, literal.col_offset)
    end = _byte_offset(lines, literal.end_lineno, literal.end_col_offset)
    if source_bytes[start:end] != str(_OLD_BOUND).encode("utf-8"):
        raise OracleError(
            f"anchor literal bytes are {source_bytes[start:end]!r}, "
            f"expected {str(_OLD_BOUND).encode('utf-8')!r}"
        )
    return (source_bytes[:start] + str(_NEW_BOUND).encode("utf-8") + source_bytes[end:]).decode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# 3. Runtime acceptance tests. Collected only via explicit-path invocation:
#    python -m pytest evals/maintenance/repair_cases/page_size_limit/oracle.py
#    Real Client/AsyncClient over httpx.MockTransport with the same
#    constructor signature as tests/test_sdk_storage.py; all data is
#    synthetic; no constructor is bypassed and no real API is contacted.
# ---------------------------------------------------------------------------

_START = datetime(2026, 1, 2, 3, 4, 5)
_END = _START + timedelta(hours=1)
_SPACES_DATA = {"result": [], "lastId": 42, "hasNext": True}
_DOWNLOAD_DATA = {
    "urls": {"EVAL_NODE_00": "https://files.example.invalid/eval?sig=SYNTHETIC"},
    "expireTime": 2000123,
}


def _call_storage(kind, name, requests, authentication, *args, data=None, **kwargs):
    """Drive a real sync/async client over MockTransport; return the result."""
    AsyncClient, Client, RegionConfig, _ = _sdk()

    def auth(request):
        authentication.append(request)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": f"at.EVAL_{len(authentication)}",
                    "expireTime": 2000000,
                },
            },
        )

    def business(request):
        requests.append(request)
        return httpx.Response(200, json={"meta": {"code": 200, "message": "ok"}, "data": data})

    options = dict(
        region=RegionConfig.china(),
        clock=lambda: 1000,
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(business),
    )
    if kind == "sync":
        with Client("EVAL_KEY", "EVAL_SECRET", **options) as client:
            return getattr(client.storage, name)(*args, **kwargs)

    async def run():
        async with AsyncClient("EVAL_KEY", "EVAL_SECRET", **options) as client:
            return await getattr(client.storage, name)(*args, **kwargs)

    return asyncio.run(run())


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_spaces_page_size_twenty_reaches_the_wire(kind):
    requests, authentication = [], []
    _call_storage(
        kind,
        "spaces",
        requests,
        authentication,
        start_time=_START,
        end_time=_END,
        page_size=20,
        data=_SPACES_DATA,
    )
    assert len(requests) == len(authentication) == 1
    assert requests[0].url.params["pageSize"] == "20"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_spaces_page_size_one_still_accepted(kind):
    requests, authentication = [], []
    _call_storage(
        kind,
        "spaces",
        requests,
        authentication,
        start_time=_START,
        end_time=_END,
        page_size=1,
        data=_SPACES_DATA,
    )
    assert requests[0].url.params["pageSize"] == "1"


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("page_size", [0, 21, 50])
def test_spaces_out_of_contract_page_size_rejected_before_any_request(kind, page_size):
    requests, authentication = [], []
    with pytest.raises(ValueError):
        _call_storage(
            kind,
            "spaces",
            requests,
            authentication,
            start_time=_START,
            end_time=_END,
            page_size=page_size,
        )
    assert requests == [] and authentication == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_spaces_default_still_omits_page_size(kind):
    requests, authentication = [], []
    _call_storage(
        kind,
        "spaces",
        requests,
        authentication,
        start_time=_START,
        end_time=_END,
        data=_SPACES_DATA,
    )
    assert len(requests) == 1
    assert "pageSize" not in requests[0].url.params


def test_storage_file_query_keeps_its_documented_fifty_bound():
    _, _, _, StorageFileQuery = _sdk()
    query = StorageFileQuery(file_node_ids=["EVAL_NODE"], page_size=50)
    assert query.to_form()["pageSize"] == 50
    with pytest.raises(ValueError):
        StorageFileQuery(file_node_ids=["EVAL_NODE"], page_size=51)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_download_urls_still_accepts_fifty_node_ids(kind):
    ids = [f"EVAL_NODE_{index:02d}" for index in range(50)]
    requests, authentication = [], []
    _call_storage(kind, "download_urls", requests, authentication, ids, data=_DOWNLOAD_DATA)
    assert len(requests) == 1
    assert len(requests[0].url.params["fileNodeIds"].split(",")) == 50

    rejected_requests, rejected_auth = [], []
    with pytest.raises(ValueError):
        _call_storage(
            kind,
            "download_urls",
            rejected_requests,
            rejected_auth,
            ids + ["EVAL_NODE_50"],
        )
    assert rejected_requests == [] and rejected_auth == []
