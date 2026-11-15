"""Edge tests for extract_http_candidates using inline synthetic HTML only.

Covers the public contract of tools.documents.http_candidates; no network,
filesystem, or subprocess access, and no private helpers are referenced.
"""

from tools.documents.http_candidates import extract_http_candidates


def _decls(html):
    return extract_http_candidates(html)["declarations"]


def _only(html):
    decls = _decls(html)
    assert len(decls) == 1
    return decls[0]


def test_result_shape_and_declaration_fields():
    out = extract_http_candidates("<p>nothing</p>")
    assert out["schema_version"] == 1
    for key in ("declarations", "examples", "warnings"):
        assert isinstance(out[key], list)
    d = _only("<p>Request Address: https://s.example.com/x?a=1#f</p><p>Request Method: GET</p>")
    assert {
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
    } <= set(d)
    assert (d["host"], d["path"], d["query"], d["fragment"]) == ("s.example.com", "/x", "a=1", "f")
    for e in d["evidence"]:
        assert {"role", "element_index", "text"} <= set(e)


def test_second_address_pairs_first_stays_address_only():
    decls = _decls(
        "<h2>Op</h2>"
        "<p>Request Address: https://a.example.com/a</p>"
        "<p>Request Address: https://b.example.com/b</p>"
        "<p>Request Method: POST</p>"
    )
    assert len(decls) == 2
    assert (decls[0]["host"], decls[0]["status"], decls[0]["method"]) == (
        "a.example.com",
        "address_only",
        None,
    )
    assert (decls[1]["host"], decls[1]["status"], decls[1]["method"]) == (
        "b.example.com",
        "declared_pair",
        "POST",
    )


def test_h3_labels_produce_pair():
    d = _only(
        "<h3>Request Address</h3><p>https://h3.example.com/do</p><h3>Request Method</h3><p>POST</p>"
    )
    assert (d["status"], d["method"], d["host"]) == ("declared_pair", "POST", "h3.example.com")


def test_example_headings_infer_method():
    d = _only("<h3>Example（GET）</h3><p>Interface URL:</p><p>https://root.example.com/</p>")
    assert (d["status"], d["method"], d["path"]) == ("declared_pair", "GET", "/")
    d = _only("<p>Example (PUT)</p><p>Interface URL: https://put.example.com/x</p>")
    assert (d["status"], d["method"]) == ("declared_pair", "PUT")


def test_word_get_in_title_does_not_infer_method():
    d = _only("<h2>Get Device List</h2><p>Request Address: https://g.example.com/list</p>")
    assert (d["status"], d["method"]) == ("address_only", None)


def test_chinese_inline_li_parsed_once():
    d = _only(
        "<ul><li>请求地址：<code>https://cn.example.com/api</code></li><li>请求方式：POST</li></ul>"
    )
    assert (d["status"], d["method"], d["host"]) == ("declared_pair", "POST", "cn.example.com")


def test_generic_url_label_and_webhook_placeholder():
    d = _only("<p>URL: https://weak.example.com/w</p><p>Request Type: POST</p>")
    assert (d["status"], d["method"]) == ("declared_pair", "POST")
    html = "<p>Webhook: <code>POST https://hook.example.com/cb</code>（用户地址）</p>"
    assert _decls(html) == []


def test_requesting_parameters_post_is_method_with_warning():
    out = extract_http_candidates(
        "<p>Requesting Address: https://ra.example.com/x</p><p>Requesting Parameters: POST</p>"
    )
    d = out["declarations"][0]
    assert (d["status"], d["method"]) == ("declared_pair", "POST")
    assert out["warnings"]


def test_parameter_table_is_barrier_not_method():
    d = _only(
        "<p>Request Address: https://b.example.com/y</p>"
        "<table><tr><td>mode</td><td>POST</td></tr></table>"
    )
    assert (d["status"], d["method"]) == ("address_only", None)


def test_title_and_explicit_method_conflict_is_ambiguous():
    d = _only(
        "<h2>Example（GET）</h2>"
        "<p>Request Address: https://amb.example.com/z</p>"
        "<p>Request Method: POST</p>"
    )
    assert d["status"] == "ambiguous"


def test_method_not_inherited_by_next_operation():
    decls = {
        d["host"]: d
        for d in _decls(
            "<h2>Example One（GET）</h2>"
            "<p>Request Address: https://one.example.com/a</p>"
            "<h2>Two</h2><p>Request Address: https://two.example.com/b</p>"
        )
    }
    assert decls["one.example.com"]["method"] == "GET"
    assert (decls["two.example.com"]["status"], decls["two.example.com"]["method"]) == (
        "address_only",
        None,
    )


def test_stray_backticks_preserved_raw_and_ambiguous():
    d = _only("<p>Request Address: `https://tick.example.com/t`</p><p>Request Method: `POST`</p>")
    assert d["status"] == "ambiguous"
    assert "`https://tick.example.com/t`" in d["raw_address"]
    assert d["raw_method"] is not None and "`POST`" in d["raw_method"]


def test_curl_selects_real_url_not_body_decoy():
    out = extract_http_candidates(
        "<pre>curl -XPOST "
        '--data \'{"url":"https://decoy.example.com/api"}\' '
        "https://real.example.com/api</pre>"
    )
    assert out["declarations"] == []
    assert len(out["examples"]) == 1
    ex = out["examples"][0]
    assert ex["status"] == "example_only"
    assert (ex["host"], ex["path"], ex["method"]) == ("real.example.com", "/api", "POST")


def test_quoted_curl_is_not_a_command():
    out = extract_http_candidates('<pre>s = "curl -XPOST https://q.example.com/x";</pre>')
    assert out["declarations"] == []
    assert out["examples"] == []


def test_curl_unknown_option_rejected():
    out = extract_http_candidates("<pre>curl --bogus -X POST https://unk.example.com/u</pre>")
    assert out["declarations"] == []
    assert len(out["examples"]) == 1
    ex = out["examples"][0]
    assert ex["method"] is None and ex["path"] is None
    assert any("unparsed" in r for r in ex["reasons"])


def test_curl_without_method_has_none_and_no_agreement():
    out = extract_http_candidates(
        "<h2>Op</h2><pre>curl https://nm.example.com/n</pre>"
        "<h3>Example</h3><pre>curl -X GET https://nm.example.com/n</pre>"
    )
    assert out["declarations"] == []
    assert len(out["examples"]) == 2
    assert all(e["status"] == "example_only" for e in out["examples"])
    assert out["examples"][0]["method"] is None
    assert out["examples"][1]["method"] == "GET"
    assert all(e["agrees_with_declaration"] is None for e in out["examples"])


def test_userinfo_fragment_gives_no_positive_corroboration():
    out = extract_http_candidates(
        "<p>Request Address: https://user:pw@uf.example.com/p#frag</p>"
        "<p>Request Method: POST</p>"
        "<h3>Request Example</h3>"
        "<pre>POST https://uf.example.com/p HTTP/1.1</pre>"
    )
    d = out["declarations"][0]
    assert "user:pw@uf.example.com" in d["raw_address"]
    assert (d["status"], d["path"], d["fragment"], d["method"]) == (
        "ambiguous",
        "/p",
        "frag",
        "POST",
    )
    assert out["examples"]
    assert all(e["agrees_with_declaration"] is not True for e in out["examples"])


def test_evidence_element_index_uses_original_dom():
    base = "<p>Request Address: https://ev.example.com/e</p><p>Request Method: GET</p>"
    junk = "<script>s=1</script><style>.a{}</style>"

    def index(decl, needle):
        hits = [e["element_index"] for e in decl["evidence"] if needle in (e["text"] or "")]
        assert hits
        return hits[0]

    d1, d2 = _only(base), _only(junk + base)
    assert index(d2, "ev.example.com") - index(d1, "ev.example.com") == 2
    assert index(d2, "GET") - index(d1, "GET") == 2
