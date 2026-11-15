"""前导段落标题方法资格与非 str 输入的回归测试。"""

from __future__ import annotations

import pytest

from tools.documents.http_candidates import extract_http_candidates


def _declarations(html: str) -> list[dict]:
    return extract_http_candidates(html)["declarations"]


def test_late_paragraph_after_meta_heading_is_not_operation_title() -> None:
    html = (
        "<h2>Request Parameters</h2>"
        "<p>Not an operation title (GET)</p>"
        "<h2>Interface URL</h2>"
        "<p>/api/probe</p>"
    )
    decls = _declarations(html)
    assert len(decls) == 1
    assert decls[0]["status"] == "address_only"
    assert decls[0]["method"] is None
    assert decls[0]["path"] == "/api/probe"
    assert decls[0]["section_index"] is None
    assert "missing_method" in decls[0]["reasons"]


def test_leading_paragraph_before_any_section_provides_title_method() -> None:
    html = "<p>Probe Operation (GET)</p><h2>Interface URL</h2><p>/api/probe</p>"
    decls = _declarations(html)
    assert len(decls) == 1
    assert decls[0]["status"] == "declared_pair"
    assert decls[0]["method"] == "GET"
    assert decls[0]["path"] == "/api/probe"


def test_title_method_does_not_leak_into_next_operation() -> None:
    html = (
        "<p>First Operation (GET)</p>"
        "<h2>Interface URL</h2><p>/api/one</p>"
        "<h2>Second Operation</h2>"
        "<h2>Interface URL</h2><p>/api/two</p>"
    )
    decls = _declarations(html)
    assert [d["status"] for d in decls] == ["declared_pair", "address_only"]
    assert decls[0]["method"] == "GET"
    assert decls[1]["method"] is None
    assert decls[1]["path"] == "/api/two"


@pytest.mark.parametrize("html", [None, b"<p>/api/x</p>", 42])
def test_extract_rejects_non_str_html(html: object) -> None:
    with pytest.raises(ValueError):
        extract_http_candidates(html)
