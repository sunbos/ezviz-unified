"""I003C independent tests for the HTTP candidate parser contract.

Exercises only the fixed public entry point
``tools.documents.http_candidates.extract_http_candidates`` with small
self-contained synthetic HTML fixtures. No files, network, registry or
implementation internals are accessed and nothing is mocked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.documents.http_candidates import extract_http_candidates  # noqa: E402

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
DECLARATION_KEYS = {
    "ordinal",
    "status",
    "method",
    "raw_method",
    "raw_address",
    "address_kind",
    "host",
    "path",
    "query",
    "fragment",
    "section_title",
    "section_index",
    "evidence",
    "reasons",
}
EXAMPLE_KEYS = DECLARATION_KEYS | {"related_declaration_ordinals", "agrees_with_declaration"}
ADDRESS_KINDS = {"absolute", "root_relative", "template", "invalid"}
DECLARED_STATUSES = {"declared_pair", "address_only", "ambiguous"}


def scan(html: str) -> dict:
    return extract_http_candidates(html)


def declarations_of(html: str) -> list:
    return scan(html)["declarations"]


def single_declaration(html: str) -> dict:
    decls = declarations_of(html)
    assert len(decls) == 1
    return decls[0]


def assert_declaration_shape(record: dict) -> None:
    assert set(record) == DECLARATION_KEYS
    assert isinstance(record["ordinal"], int) and record["ordinal"] >= 0
    assert record["status"] in DECLARED_STATUSES
    assert record["method"] is None or record["method"] in METHODS
    assert record["raw_method"] is None or isinstance(record["raw_method"], str)
    assert record["raw_address"] is None or isinstance(record["raw_address"], str)
    assert record["address_kind"] is None or record["address_kind"] in ADDRESS_KINDS
    assert record["host"] is None or isinstance(record["host"], str)
    assert record["path"] is None or isinstance(record["path"], str)
    assert isinstance(record["query"], str)
    assert isinstance(record["fragment"], str)
    assert isinstance(record["section_title"], str)
    assert record["section_index"] is None or isinstance(record["section_index"], int)
    assert isinstance(record["evidence"], list)
    for item in record["evidence"]:
        assert isinstance(item, dict)
        assert isinstance(item.get("element_index"), int)
        assert isinstance(item.get("role"), str)
        assert isinstance(item.get("text"), str)
    assert isinstance(record["reasons"], list)
    assert all(isinstance(reason, str) for reason in record["reasons"])


def assert_example_shape(record: dict) -> None:
    assert set(record) == EXAMPLE_KEYS
    assert record["status"] == "example_only"
    assert isinstance(record["related_declaration_ordinals"], list)
    assert all(isinstance(o, int) for o in record["related_declaration_ordinals"])
    assert record["agrees_with_declaration"] in (True, False, None)


def cn_op(address, method, heading="接口", middle=""):
    """Chinese ``<ul><li>label</li></ul>`` + following value pattern."""
    parts = [f"<h2>{heading}</h2>"]
    if address is not None:
        parts.append("<ul><li>请求地址</li></ul>")
        parts.append(f"<p><code>{address}</code></p>")
    if middle:
        parts.append(middle)
    if method is not None:
        parts.append("<ul><li>请求方式</li></ul>")
        parts.append(f"<p><code>{method}</code></p>")
    return "".join(parts)


def en_op(
    address,
    method,
    heading="1.1 Operation",
    addr_label="Request Address",
    method_label="Request Method",
):
    """Overseas h3 operation domain with nested li-p label/value pairs."""
    return (
        f"<h3>{heading}</h3><ul>"
        f"<li><p>{addr_label}</p><p><code>{address}</code></p></li>"
        f"<li><p>{method_label}</p><p><code>{method}</code></p></li>"
        f"</ul>"
    )


# ---------------------------------------------------------------- schema


def test_top_level_schema():
    out = scan("<p>没有任何接口声明</p>")
    assert set(out) == {"schema_version", "declarations", "examples", "warnings"}
    assert out["schema_version"] == 1
    assert out["declarations"] == []
    assert out["examples"] == []
    assert isinstance(out["warnings"], list)


def test_declaration_and_example_shapes():
    html = cn_op("https://h/api/t", "POST") + (
        "<ul><li>请求示例</li></ul><pre><code>curl --request POST 'https://h/api/t'</code></pre>"
    )
    out = scan(html)
    for d in out["declarations"]:
        assert_declaration_shape(d)
    for e in out["examples"]:
        assert_example_shape(e)


# ---------------------------------------------------------------- patterns


def test_chinese_label_value_pair_absolute():
    d = single_declaration(cn_op("https://open.ys7.com/api/lapp/token/get", "POST"))
    assert d["status"] == "declared_pair"
    assert d["method"] == "POST" and d["raw_method"] == "POST"
    assert d["raw_address"] == "https://open.ys7.com/api/lapp/token/get"
    assert d["address_kind"] == "absolute"
    assert d["host"] == "open.ys7.com"
    assert d["path"] == "/api/lapp/token/get"
    assert d["query"] == "" and d["fragment"] == ""
    assert d["ordinal"] == 0
    assert d["evidence"]


def test_interface_info_table_method_first_pairs():
    html = (
        "<h2>创建即时录制计划</h2><h2>2. 接口信息</h2>"
        "<table><tbody>"
        "<tr><td>请求方法</td><td><code>POST</code></td></tr>"
        "<tr><td>请求路径</td><td><code>/api/service/cloudrecord/video/plan/instant</code></td></tr>"
        "<tr><td>内容类型</td><td><code>application/json</code></td></tr>"
        "<tr><td>鉴权方式</td><td>accessToken（Header）</td></tr>"
        "</tbody></table>"
    )
    d = single_declaration(html)
    assert d["status"] == "declared_pair"
    assert d["method"] == "POST"
    assert d["address_kind"] == "root_relative"
    assert d["host"] is None
    assert d["path"] == "/api/service/cloudrecord/video/plan/instant"


def test_overseas_nested_li_p_template_host():
    html = (
        '<h3>1.1Create Sub-Account<a id="account-api1"></a></h3><ul>'
        "<li><p>Function</p><p>create</p></li>"
        "<li><p>Request Address</p><p><code>{areaDomain}/api/lapp/ram/account/create</code></p></li>"
        "<li><p>Request Method</p><p><code>POST</code></p></li>"
        "<li><p>Request Parameters</p></li></ul>"
    )
    d = single_declaration(html)
    assert d["status"] == "declared_pair"
    assert d["address_kind"] == "template"
    assert d["host"] == "{areaDomain}"
    assert d["path"] == "/api/lapp/ram/account/create"
    assert d["method"] == "POST"
    assert "Create Sub-Account" in d["section_title"]


def test_all_three_declaration_patterns_in_one_document():
    html = (
        cn_op("https://h/api/cn", "GET", heading="操作一")
        + "<h2>表格操作</h2><h3>接口信息</h3><table><tbody>"
        "<tr><td>请求路径</td><td><code>/api/tbl</code></td></tr>"
        "<tr><td>请求方法</td><td><code>POST</code></td></tr></tbody></table>"
        + en_op("{areaDomain}/api/en", "PUT", heading="3.3 Third")
    )
    decls = declarations_of(html)
    assert [d["ordinal"] for d in decls] == [0, 1, 2]
    by_path = {d["path"]: d for d in decls}
    assert by_path["/api/cn"]["method"] == "GET"
    assert by_path["/api/tbl"]["method"] == "POST"
    assert by_path["/api/en"]["method"] == "PUT"
    assert by_path["/api/en"]["host"] == "{areaDomain}"


# ---------------------------------------------------------------- labels


@pytest.mark.parametrize("addr_label", ["请求地址", "请求URL", " 请求地址 ", "请求地址："])
def test_chinese_label_synonyms(addr_label):
    html = (
        f"<h2>op</h2><ul><li>{addr_label}</li></ul>"
        "<p><code>/api/s</code></p>"
        "<ul><li>请求方式</li></ul><p><code>GET</code></p>"
    )
    d = single_declaration(html)
    assert d["status"] == "declared_pair" and d["method"] == "GET" and d["path"] == "/api/s"


@pytest.mark.parametrize(
    "addr_label,method_label",
    [
        ("Request Address", "Request Method"),
        ("Request URL", "HTTP Method"),
        ("Request Path", "Request Type"),
        ("request address:", "request method"),
        ("Request Address：", "Request Method："),
    ],
)
def test_english_label_synonyms_nested_li_p(addr_label, method_label):
    html = en_op("{areaDomain}/api/s", "POST", addr_label=addr_label, method_label=method_label)
    d = single_declaration(html)
    assert d["status"] == "declared_pair" and d["method"] == "POST" and d["path"] == "/api/s"


def test_label_ignores_nbsp():
    html = (
        "<h2>op</h2><ul><li>请求地址&nbsp;</li></ul>"
        "<p>&nbsp;&nbsp;<code>https://h/api/n</code></p>"
        "<ul><li>&nbsp;请求方式&nbsp;</li></ul><p><code>PUT</code></p>"
    )
    d = single_declaration(html)
    assert d["status"] == "declared_pair" and d["method"] == "PUT"


# ---------------------------------------------------------------- pairing


def test_missing_method_does_not_leak_from_neighbours():
    html = (
        cn_op("https://h/s/a", "POST", heading="A")
        + cn_op("https://h/s/b", None, heading="B")
        + cn_op("https://h/s/c", "DELETE", heading="C")
    )
    decls = declarations_of(html)
    assert [d["ordinal"] for d in decls] == [0, 1, 2]
    by_path = {d["path"]: d for d in decls}
    assert set(by_path) == {"/s/a", "/s/b", "/s/c"}
    assert by_path["/s/a"]["method"] == "POST" and by_path["/s/a"]["status"] == "declared_pair"
    assert by_path["/s/c"]["method"] == "DELETE" and by_path["/s/c"]["status"] == "declared_pair"
    b = by_path["/s/b"]
    assert b["status"] == "address_only"
    assert b["method"] is None and b["raw_method"] is None


def test_method_after_parameter_boundary_is_orphan():
    html = (
        "<h2>Op</h2>"
        "<ul><li>请求地址</li></ul><p><code>/api/orphan</code></p>"
        "<ul><li>请求参数</li></ul><table><tbody><tr><td>p</td></tr></tbody></table>"
        "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
    )
    out = scan(html)
    assert len(out["declarations"]) == 1
    d = out["declarations"][0]
    assert d["status"] == "address_only" and d["method"] is None
    assert any(w.get("code") == "orphan_method" for w in out["warnings"])


def test_orphan_method_never_infers_address():
    html = "<h2>Op</h2><ul><li>请求方式</li></ul><p><code>POST</code></p>"
    out = scan(html)
    for d in out["declarations"]:
        assert d["raw_address"] is not None
        assert d["status"] != "declared_pair"
    assert any(w.get("code") == "orphan_method" for w in out["warnings"])
    for w in out["warnings"]:
        assert "code" in w and "element_index" in w


def test_conflicting_methods_mark_ambiguous_not_first_wins():
    html = (
        "<h2>Op</h2>"
        "<ul><li>请求地址</li></ul><p><code>/api/m</code></p>"
        "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
        "<ul><li>请求方式</li></ul><p><code>PUT</code></p>"
    )
    d = single_declaration(html)
    assert d["status"] == "ambiguous"
    assert d["method"] is None
    assert d["raw_address"] == "/api/m"


@pytest.mark.parametrize("raw", ["GET,POST", "GET/POST", "GET、POST"])
def test_multi_method_value_marked_ambiguous(raw):
    d = single_declaration(cn_op("/api/mm", raw))
    assert d["status"] == "ambiguous"
    assert d["method"] is None
    assert d["raw_method"] == raw


def test_unknown_method_preserved_as_ambiguous():
    d = single_declaration(cn_op("/api/u", "FETCH"))
    assert d["status"] == "ambiguous"
    assert d["method"] is None
    assert d["raw_method"] == "FETCH"
    assert d["path"] == "/api/u"


def test_lowercase_method_normalized_but_raw_kept():
    d = single_declaration(cn_op("/api/lc", "post"))
    assert d["method"] == "POST"
    assert d["raw_method"] == "post"


def test_method_before_address_single_unit_pairs():
    html = (
        "<h2>Op</h2>"
        "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
        "<ul><li>请求地址</li></ul><p><code>/api/mfirst</code></p>"
    )
    d = single_declaration(html)
    assert d["status"] == "declared_pair"
    assert d["method"] == "POST" and d["path"] == "/api/mfirst"


def test_method_before_address_blocked_by_boundary():
    html = (
        "<h2>Op</h2>"
        "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
        "<ul><li>请求参数</li></ul><p>x</p>"
        "<ul><li>请求地址</li></ul><p><code>/api/late</code></p>"
    )
    out = scan(html)
    assert len(out["declarations"]) == 1
    d = out["declarations"][0]
    assert d["status"] == "address_only" and d["method"] is None
    assert any(w.get("code") == "orphan_method" for w in out["warnings"])


@pytest.mark.parametrize("m", sorted(METHODS))
def test_all_whitelisted_methods_pair(m):
    d = single_declaration(cn_op("/api/w", m))
    assert d["status"] == "declared_pair" and d["method"] == m


# ---------------------------------------------------------------- multi-op


def test_multi_operation_document_ordinals():
    html = cn_op("https://h/api/one", "GET", heading="Alpha") + cn_op(
        "https://h/api/two", "POST", heading="Beta"
    )
    decls = declarations_of(html)
    assert len(decls) == 2
    assert [d["ordinal"] for d in decls] == [0, 1]
    assert "Alpha" in decls[0]["section_title"] and "Beta" in decls[1]["section_title"]
    if decls[0]["section_index"] is not None and decls[1]["section_index"] is not None:
        assert decls[0]["section_index"] <= decls[1]["section_index"]


def test_same_route_in_two_domains_keeps_both_occurrences():
    html = cn_op("/api/dup", "POST", heading="Op1") + cn_op("/api/dup", "POST", heading="Op2")
    decls = declarations_of(html)
    assert len(decls) == 2
    assert all(d["status"] == "declared_pair" for d in decls)
    assert all(d["path"] == "/api/dup" and d["method"] == "POST" for d in decls)


def test_same_path_two_methods_not_merged():
    html = cn_op("https://h/api/p", "GET", heading="Query") + cn_op(
        "https://h/api/p", "PUT", heading="Update"
    )
    decls = declarations_of(html)
    assert len(decls) == 2
    methods = sorted(d["method"] for d in decls)
    assert methods == ["GET", "PUT"]
    assert all(d["method"] in METHODS for d in decls)


def test_duplicate_dom_label_pairs_not_double_counted():
    html = (
        "<h3>Op</h3><ul>"
        "<li><p>Request Address</p><p><code>{areaDomain}/api/nd</code></p></li>"
        "<li><p>Request Address</p><p><code>{areaDomain}/api/nd</code></p></li>"
        "<li><p>Request Method</p><p><code>POST</code></p></li></ul>"
    )
    decls = declarations_of(html)
    assert len(decls) == 1
    assert decls[0]["status"] == "declared_pair"


# ---------------------------------------------------------------- addresses


def test_address_kinds_template_relative_absolute():
    html = (
        cn_op("{areaDomain}/api/t", "POST", heading="T")
        + cn_op("/api/u", "POST", heading="U")
        + cn_op("https://api.example.com/api/v", "POST", heading="V")
    )
    decls = declarations_of(html)
    kinds = {d["path"]: (d["address_kind"], d["host"]) for d in decls}
    assert kinds["/api/t"] == ("template", "{areaDomain}")
    assert kinds["/api/u"] == ("root_relative", None)
    assert kinds["/api/v"] == ("absolute", "api.example.com")


def test_root_relative_not_prefixed_with_host():
    d = single_declaration(cn_op("/api/u", "POST"))
    assert d["address_kind"] == "root_relative"
    assert d["host"] is None and d["path"] == "/api/u"


def test_query_preserved_and_pair_still_valid():
    d = single_declaration(cn_op("https://h/api/q?accessToken=x&p=1", "POST"))
    assert d["status"] == "declared_pair"
    assert d["path"] == "/api/q"
    assert d["query"] == "accessToken=x&p=1"
    assert d["fragment"] == ""
    assert "?" not in d["path"] and "#" not in d["path"]


def test_fragment_disqualifies_declared_pair():
    d = single_declaration(cn_op("https://h/api/f?x=1#frag", "POST"))
    assert d["status"] != "declared_pair"
    assert d["path"] == "/api/f"
    assert d["query"] == "x=1"
    assert d["fragment"] == "frag"
    assert "#" not in d["path"] and "?" not in d["path"]


def test_userinfo_disqualifies_declared_pair():
    d = single_declaration(cn_op("https://user:pw@h/api/u", "POST"))
    assert d["status"] != "declared_pair"
    assert "@" not in (d["path"] or "")


def test_empty_address_not_paired():
    html = (
        "<h2>Op</h2><ul><li>请求地址</li></ul><p><code>   </code></p>"
        "<ul><li>请求方式</li></ul><p><code>POST</code></p>"
    )
    out = scan(html)
    assert not any(d["status"] == "declared_pair" for d in out["declarations"])


@pytest.mark.parametrize("raw", ["ftp://h/x", "ezopen://device/1", "ws://h/x", "notaurl"])
def test_non_http_or_invalid_address(raw):
    d = single_declaration(cn_op(raw, "POST"))
    assert d["status"] != "declared_pair"
    assert d["raw_address"] == raw


def test_html_entity_unescaped_once_in_query():
    d = single_declaration(cn_op("https://h/api/e?a=1&amp;b=2", "POST"))
    assert d["query"] == "a=1&b=2"
    assert "&amp;" not in (d["raw_address"] or "")


def test_percent_encoding_not_decoded():
    d = single_declaration(cn_op("https://h/api/p%2Fx", "POST"))
    assert d["path"] == "/api/p%2Fx"


def test_path_case_and_trailing_slash_preserved():
    d = single_declaration(cn_op("https://h/Api/Camel/", "POST"))
    assert d["path"] == "/Api/Camel/"


def test_path_template_preserved_with_reason():
    d = single_declaration(cn_op("https://h/api/devices/{deviceSerial}/x", "POST"))
    assert d["path"] == "/api/devices/{deviceSerial}/x"
    assert "templated_path" in d["reasons"]


# ---------------------------------------------------------------- examples


def test_identical_curl_example_links_and_agrees():
    html = cn_op("https://h/api/t", "POST") + (
        "<ul><li>请求示例</li></ul>"
        "<pre><code class=\"language-bash\">curl --location --request POST 'https://h/api/t'</code></pre>"
    )
    out = scan(html)
    assert len(out["declarations"]) == 1
    assert len(out["examples"]) == 1
    ex = out["examples"][0]
    assert_example_shape(ex)
    assert ex["method"] == "POST" and ex["path"] == "/api/t"
    assert ex["related_declaration_ordinals"] == [0]
    assert ex["agrees_with_declaration"] is True


def test_example_never_upgrades_to_declaration():
    html = (
        "<h2>Op</h2><ul><li>请求示例</li></ul>"
        "<pre><code>curl --request POST 'https://h/api/only'</code></pre>"
    )
    out = scan(html)
    assert out["declarations"] == []
    assert len(out["examples"]) == 1
    ex = out["examples"][0]
    assert ex["status"] == "example_only"
    assert ex["related_declaration_ordinals"] == []
    assert ex["agrees_with_declaration"] is None
    assert ex["method"] == "POST" and ex["path"] == "/api/only"


def test_example_method_extraction_variants():
    html = cn_op("https://h/api/base", "POST") + (
        "<ul><li>请求示例</li></ul>"
        "<pre><code>curl -X PUT 'https://h/api/e1'</code></pre>"
        "<pre><code>curl --request DELETE 'https://h/api/e2'</code></pre>"
        "<pre><code>curl --location 'https://h/api/e3'</code></pre>"
        "<pre><code>PATCH /api/e4 HTTP/1.1\nHost: h</code></pre>"
        "<pre><code>GET /api/not-an-example</code></pre>"
    )
    out = scan(html)
    by_path = {e["path"]: e for e in out["examples"]}
    assert by_path["/api/e1"]["method"] == "PUT"
    assert by_path["/api/e2"]["method"] == "DELETE"
    assert by_path["/api/e3"]["method"] is None
    assert by_path["/api/e4"]["method"] == "PATCH"
    assert "/api/not-an-example" not in by_path


def test_example_mismatch_does_not_override_declaration():
    """Modelled on the doc-485 trap: declared /account/list, example prints /account/get."""
    html = (
        '<h3>1.2 Get<a id="a2"></a></h3><ul>'
        "<li><p>Request Address</p><p><code>{areaDomain}/api/account/get</code></p></li>"
        "<li><p>Request Method</p><p><code>POST</code></p></li></ul>"
        "<ul><li>HTTP Request Message</li></ul>"
        "<pre><code>POST /api/account/get HTTP/1.1\nHost: isgpopen.ezvizlife.com</code></pre>"
        '<h3>1.3 List<a id="a3"></a></h3><ul>'
        "<li><p>Request Address</p><p><code>{areaDomain}/api/account/list</code></p></li>"
        "<li><p>Request Method</p><p><code>POST</code></p></li></ul>"
        "<ul><li>HTTP Request Message</li></ul>"
        "<pre><code>POST /api/account/get HTTP/1.1\nHost: isgpopen.ezvizlife.com</code></pre>"
    )
    out = scan(html)
    decls = out["declarations"]
    assert [d["path"] for d in decls] == ["/api/account/get", "/api/account/list"]
    assert all(d["host"] == "{areaDomain}" for d in decls)
    assert all(d["status"] == "declared_pair" for d in decls)
    exs = out["examples"]
    assert len(exs) == 2
    assert exs[0]["related_declaration_ordinals"] == [0]
    assert exs[0]["agrees_with_declaration"] is True
    assert exs[1]["related_declaration_ordinals"] == [1]
    assert exs[1]["agrees_with_declaration"] is False
    get_paths = [d["path"] for d in decls].count("/api/account/get")
    assert get_paths == 1


# ---------------------------------------------------------------- exclusions


def test_non_endpoint_material_produces_nothing():
    html = (
        "<h2>配置向导</h2>"
        '<p><a href="https://open.ys7.com/help/81">accessToken获取接口</a>'
        '<a href="https://resource.eziot.com/demo.zip">下载demo</a>'
        '<a href="#account-api1">锚点</a>'
        '<img src="https://img.ys7.com/a.png"></p>'
        '<pre><code class="language-java">@RequestMapping(value = "/webhook")</code></pre>'
        '<pre><code class="language-json">{"Resource":["dev:469631729","cam:544229080:1"],'
        '"picUrl":"https://i.ys7.com/x.jpeg"}</code></pre>'
        "<p>POST/（用户的Webhook回调地址）</p>"
    )
    out = scan(html)
    assert out["declarations"] == []
    assert out["examples"] == []


def test_response_json_url_is_not_a_candidate():
    html = cn_op("https://h/api/real", "POST") + (
        "<ul><li>返回数据</li></ul>"
        '<pre><code class="language-json">{"data":{"url":"https://h/api/resp"}}</code></pre>'
    )
    out = scan(html)
    assert [d["path"] for d in out["declarations"]] == ["/api/real"]
    assert out["examples"] == []


def test_webhook_placeholder_doc_has_no_candidates():
    html = (
        "<h2>步骤三：测试消息接收是否成功示例</h2><h3><strong>URL</strong></h3>"
        "<p>POST/（用户的Webhook回调地址）</p>"
        "<h3><strong>请求参数</strong></h3>"
        "<table><tbody><tr><td>t</td></tr></tbody></table>"
        "<h3><strong>响应示例</strong></h3><p>OK</p>"
    )
    out = scan(html)
    assert out["declarations"] == []
    assert out["examples"] == []


def test_script_and_style_are_ignored():
    html = (
        "<script>var u='https://evil.example/api/x';var m='POST';</script>"
        "<style>.a::after{content:'/api/y'}</style>" + cn_op("https://h/api/good", "GET")
    )
    out = scan(html)
    assert [d["path"] for d in out["declarations"]] == ["/api/good"]
    blob = json.dumps(out, ensure_ascii=False)
    assert "evil.example" not in blob and "/api/y" not in blob


def test_boilerplate_only_page_yields_no_candidates():
    html = (
        "<h2>通用说明</h2>"
        "<table><thead><tr><th>Returned Code</th><th>Description</th></tr></thead>"
        "<tbody><tr><td>200</td><td>Operation succeed</td></tr></tbody></table>"
        "<ul><li>Request Parameters</li></ul><p>see above</p>"
        '<p><a href="/help/en/486">nav</a><a href="#top">top</a></p>'
    )
    out = scan(html)
    assert out["declarations"] == []


# ---------------------------------------------------------------- misc


def test_extraction_is_deterministic():
    html = cn_op("https://h/api/x?a=1", "POST") + cn_op("/api/y", "GET", heading="B")
    first = scan(html)
    second = scan(html)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_record_ordinals_are_zero_based_document_order():
    html = "".join(cn_op(f"/api/o{i}", "GET", heading=f"op-{i}") for i in range(4))
    decls = declarations_of(html)
    assert [d["ordinal"] for d in decls] == [0, 1, 2, 3]
    assert all(d["status"] == "declared_pair" for d in decls)
