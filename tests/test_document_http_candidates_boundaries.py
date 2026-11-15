"""行内“标签：取值”不得被吞作前一独立标签取值的边界回归测试。"""

from __future__ import annotations

from tools.documents.http_candidates import extract_http_candidates


def _assert_inline_label_boundary(html: str) -> None:
    result = extract_http_candidates(html)
    addresses = [declaration.get("raw_address") for declaration in result["declarations"]]
    assert "URL：https://h/a" not in addresses
    assert all(not (address or "").startswith("URL：") for address in addresses)
    assert "https://h/a" in addresses
    assert "missing_label_value" in str(result)


def test_li_inline_label_stops_previous_label_value() -> None:
    _assert_inline_label_boundary(
        "<p>请求地址</p><ul><li>URL：https://h/a</li><li>请求方式：GET</li></ul>"
    )


def test_p_inline_label_stops_previous_label_value() -> None:
    _assert_inline_label_boundary("<p>请求地址</p><p>URL：https://h/a</p><p>请求方式：GET</p>")


def test_div_wrapped_inline_labels_stop_previous_label_value() -> None:
    _assert_inline_label_boundary(
        "<p>请求地址</p><div><ul><li>URL：https://h/a</li><li>请求方式：GET</li></ul></div>"
    )
