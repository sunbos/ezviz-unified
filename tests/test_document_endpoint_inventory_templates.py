"""Independent tests for canonical ``{name}`` path-template source joins.

Synthetic tiny archives exercise the template eligibility rule in
``tools.documents.endpoint_inventory``: a declared pair whose path is a
canonical whole-segment ASCII ``{name}`` template may join a reviewed
contract only by verbatim equality of all six source fields. Templates are
never expanded, instantiated with example device values, or completed from
registry data; malformed templates and the legacy query/fragment/userinfo/@
exclusions still block the join, and ``templated_path`` stays a reason,
never a callable marker.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.documents.endpoint_inventory import build_inventory  # noqa: E402

DOC_ID = "7"
URL = f"https://open.ys7.com/help/{DOC_ID}"
HOST = "https://open.ys7.com"


def decl_html(address: str, method: str = "POST") -> str:
    return (
        "<h2>Op</h2><ul><li>请求地址</li></ul>"
        f"<p><code>{address}</code></p>"
        "<ul><li>请求方式</li></ul>"
        f"<p><code>{method}</code></p>"
    )


def example_only_html(target: str, method: str = "GET") -> str:
    return (
        "<h2>Guide</h2><ul><li>请求示例</li></ul>"
        f"<pre><code>curl --request {method} '{target}'</code></pre>"
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_raw(root: Path, name: str, html: str, doc_id: str = DOC_ID) -> dict:
    payload = {"doc_id": doc_id, "html_content": html}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "raw_file": name,
        "raw_file_sha256": sha256_bytes(raw),
        "content_sha256": sha256_bytes(html.encode("utf-8")),
    }


def write_archive(tmp_path: Path, html: str, *, region="domestic", doc_id=DOC_ID, url=URL):
    root = tmp_path / "archive"
    raw = write_raw(root, f"raw/{doc_id}.json", html, doc_id)
    entry = {
        "region": region,
        "doc_id": doc_id,
        "title": f"doc-{doc_id}",
        "source_url": url,
        "status": "archived",
        "memberships": [{"category_path": ["API"]}],
    }
    entry.update(raw)
    catalog = json.dumps([entry], ensure_ascii=False).encode("utf-8")
    (root / "catalog.json").write_bytes(catalog)
    return root, raw


def write_contracts(tmp_path: Path, rows) -> Path:
    path = tmp_path / "contracts.json"
    payload = json.dumps({"schema_version": 1, "contracts": rows}, ensure_ascii=False)
    path.write_bytes(payload.encode("utf-8"))
    return path


def contract(
    operation_id,
    *,
    raw,
    region="domestic",
    doc_id=DOC_ID,
    url=URL,
    content_sha256=None,
    method="POST",
    path,
):
    return {
        "operation_id": operation_id,
        "region": region,
        "source_doc_id": doc_id,
        "source_url": url,
        "source_content_sha256": (
            content_sha256 if content_sha256 is not None else raw["content_sha256"]
        ),
        "method": method,
        "path": path,
    }


def single_doc(out):
    (doc,) = out["documents"]
    return doc


# ---------------------------------------------------------------- positive


def test_whole_segment_template_joins_verbatim_source(tmp_path):
    path = "/api/v3/otap/prop/{deviceSerial}/Video/1/Switch"
    root, raw = write_archive(tmp_path, decl_html(HOST + path, "GET"))
    cpath = write_contracts(tmp_path, [contract("op-tpl", raw=raw, method="GET", path=path)])

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["status"] == "declared_pair"
    assert decl["path"] == path
    # The template reason is preserved; the join never marks it callable.
    assert "templated_path" in decl["reasons"]
    assert decl["reviewed_operation_ids"] == ["op-tpl"]
    assert decl["same_route_operation_ids"] == ["op-tpl"]
    assert out["summary"]["matched_reviewed_contracts"] == 1
    assert out["unrecovered_reviewed"] == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/{group}/items/{item}",
        "/api/{serial}/sub/{serial}",  # repeated same-name placeholders legal
        "/api/{_x9}/v/{B2}",
    ],
)
def test_multiple_and_repeated_placeholders_join(tmp_path, path):
    root, raw = write_archive(tmp_path, decl_html(HOST + path))
    cpath = write_contracts(tmp_path, [contract("op-multi", raw=raw, path=path)])

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["reviewed_operation_ids"] == ["op-multi"]
    assert out["summary"]["matched_reviewed_contracts"] == 1


def test_template_host_with_literal_path_unchanged(tmp_path):
    """Legacy behavior: a templated host plus literal path already joined."""
    root, raw = write_archive(
        tmp_path,
        decl_html("{areaDomain}/api/t"),
        region="overseas",
        url="https://open.ys7.com/help/en/7",
    )
    cpath = write_contracts(
        tmp_path,
        [
            contract(
                "op-host",
                raw=raw,
                region="overseas",
                url="https://open.ys7.com/help/en/7",
                path="/api/t",
            )
        ],
    )

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["host"] == "{areaDomain}"
    assert decl["path"] == "/api/t"
    assert decl["reviewed_operation_ids"] == ["op-host"]


def test_literal_dot_segments_keep_legacy_behavior(tmp_path):
    """Fixed literal paths are unchanged: '.'/'..' rejection is template-only."""
    path = "/api/../x"
    root, raw = write_archive(tmp_path, decl_html(HOST + path))
    cpath = write_contracts(tmp_path, [contract("op-dot", raw=raw, path=path)])

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["reviewed_operation_ids"] == ["op-dot"]


# ---------------------------------------------------------------- verbatim


@pytest.mark.parametrize(
    "contract_path",
    [
        "/api/{a}/x",  # {a} != {b}: placeholder names compare verbatim
        "/api/{B}/x",  # case is not folded
        "/api/{b}/y",  # literal tail differs
    ],
)
def test_placeholder_names_compare_verbatim(tmp_path, contract_path):
    path = "/api/{b}/x"
    root, raw = write_archive(tmp_path, decl_html(HOST + path))
    cpath = write_contracts(tmp_path, [contract("op-name", raw=raw, path=contract_path)])

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["reviewed_operation_ids"] == []
    assert decl["same_route_operation_ids"] == []
    assert out["unrecovered_reviewed"] == [
        {"operation_id": "op-name", "reason": "no_matching_declaration"}
    ]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("url", "https://open.ys7.com/help/7-old", "source_url_changed"),
        ("content_sha256", "f" * 64, "source_content_changed"),
        ("region", "overseas", "source_document_missing"),
        ("doc_id", "8", "source_document_missing"),
        ("method", "GET", "no_matching_declaration"),
        ("path", "/api/v3/otap/prop/{deviceSerial}/Other", "no_matching_declaration"),
    ],
)
def test_template_join_needs_every_source_field(tmp_path, field, value, reason):
    path = "/api/v3/otap/prop/{deviceSerial}/x"
    root, raw = write_archive(tmp_path, decl_html(HOST + path))
    overrides = {field: value} if field != "path" else {}
    row = contract("op-drift", raw=raw, path=value if field == "path" else path, **overrides)
    cpath = write_contracts(tmp_path, [row])

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["reviewed_operation_ids"] == []
    assert out["unrecovered_reviewed"] == [{"operation_id": "op-drift", "reason": reason}]


# ---------------------------------------------------------------- malformed


MALFORMED_TEMPLATES = [
    "/api/{参数}/x",  # non-ASCII name
    "/api/}name{/x",  # reversed braces
    "/api/{{name}}/x",  # doubled braces
    "/api/prefix{name}/x",  # literal prefix in segment
    "/api/{name}suffix/x",  # literal suffix in segment
    "/api/a{b}c/x",  # embedded placeholder
    "/api/{}/x",  # empty name
    "/api/{name:8}/x",  # format spec expression
    "/api/{name!r}/x",  # conversion expression
    "/api/{a}/./x",  # static dot segment
    "/api/{a}/../x",  # static dotdot segment
    "/api/{a}/%2e/x",  # encoded dot segment
    "/api/{a}/%2E%2E/x",  # encoded dotdot segment
    "/api/{a}/.%2e/x",  # mixed encoded dotdot segment
]


@pytest.mark.parametrize("path", MALFORMED_TEMPLATES)
def test_malformed_template_never_joins(tmp_path, path):
    root, raw = write_archive(tmp_path, decl_html(HOST + path))
    cpath = write_contracts(tmp_path, [contract("op-bad", raw=raw, path=path)])

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["status"] == "declared_pair"
    assert "templated_path" in decl["reasons"]
    assert decl["reviewed_operation_ids"] == []
    # Route hints stay informational even for ineligible templates.
    assert decl["same_route_operation_ids"] == ["op-bad"]
    assert out["unrecovered_reviewed"] == [
        {"operation_id": "op-bad", "reason": "no_matching_declaration"}
    ]


def test_template_with_query_still_blocked(tmp_path):
    path = "/api/{a}/x"
    root, raw = write_archive(tmp_path, decl_html(HOST + path + "?mode=1"))
    cpath = write_contracts(tmp_path, [contract("op-q", raw=raw, path=path)])

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["status"] == "declared_pair"
    assert decl.get("query")
    assert decl["reviewed_operation_ids"] == []
    assert out["unrecovered_reviewed"] == [
        {"operation_id": "op-q", "reason": "no_matching_declaration"}
    ]


# ------------------------------------------------------- no filling-in


def test_instantiated_declaration_does_not_match_template(tmp_path):
    """A concrete serial in the declaration is not the template literal."""
    root, raw = write_archive(tmp_path, decl_html(HOST + "/api/prop/L12345678/x"))
    cpath = write_contracts(
        tmp_path, [contract("op-inst", raw=raw, path="/api/prop/{deviceSerial}/x")]
    )

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["path"] == "/api/prop/L12345678/x"
    assert decl["reviewed_operation_ids"] == []
    assert out["unrecovered_reviewed"] == [
        {"operation_id": "op-inst", "reason": "no_matching_declaration"}
    ]


def test_contract_cannot_instantiate_template(tmp_path):
    """Registry device values never fill a placeholder to force a join."""
    path = "/api/prop/{deviceSerial}/x"
    root, raw = write_archive(tmp_path, decl_html(HOST + path))
    cpath = write_contracts(tmp_path, [contract("op-fill", raw=raw, path="/api/prop/L12345678/x")])

    out = build_inventory(root, contracts_path=cpath)

    decl = single_doc(out)["declarations"][0]
    assert decl["reviewed_operation_ids"] == []
    assert out["unrecovered_reviewed"] == [
        {"operation_id": "op-fill", "reason": "no_matching_declaration"}
    ]


def test_example_only_instantiated_call_cannot_fill(tmp_path):
    """Curl examples with concrete serials stay examples; nothing fills in."""
    root, raw = write_archive(tmp_path, example_only_html(HOST + "/api/prop/L12345678/x", "GET"))
    cpath = write_contracts(
        tmp_path,
        [contract("op-ex", raw=raw, method="GET", path="/api/prop/{deviceSerial}/x")],
    )

    out = build_inventory(root, contracts_path=cpath)

    doc = single_doc(out)
    assert doc["declarations"] == []
    assert doc["examples"][0]["status"] == "example_only"
    assert doc["examples"][0]["path"] == "/api/prop/L12345678/x"
    assert out["unrecovered_reviewed"] == [
        {"operation_id": "op-ex", "reason": "no_matching_declaration"}
    ]
