"""Multiline shell-aware curl example extraction tests.

Reproduces the ezviz-775 false positive where a legal multi-line curl
command with a single-quoted JSON body was split into two fragments by
the plain ``split("\n")`` logic line splitter. All fixtures are
self-contained synthetic HTML; no network, filesystem, registry or
implementation internals are accessed.
"""

from __future__ import annotations

from tools.documents.http_candidates import extract_http_candidates


def _examples(html: str) -> list[dict]:
    return extract_http_candidates(html)["examples"]


def _only_example(html: str) -> dict:
    examples = _examples(html)
    assert len(examples) == 1
    return examples[0]


# ------------------------------------------------------------------ 775


def test_multiline_curl_with_single_quoted_json_lf() -> None:
    """Doc-775 shape: --data-raw contains a single-quoted JSON body with
    real newlines. The whole command must be parsed as one curl example."""
    curl = (
        "curl --location --request PUT "
        "'https://open.ys7.com/api/v3/otap/prop/{deviceSerial}/Video/1/"
        "DoorLockCtrl/FaceRecognitionVoiceTipSwitch' \\\n"
        "--header 'Content-Type: application/json' \\\n"
        "--header 'accessToken: at.xxxxx' \\\n"
        "--data-raw '{\n"
        '\t"enabled":true\n'
        "}'"
    )
    html = f'<pre><code class="language-bash">{curl}</code></pre>'
    example = _only_example(html)
    assert example["status"] == "example_only"
    assert example["method"] == "PUT"
    assert example["raw_method"] == "PUT"
    assert example["address_kind"] == "absolute"
    assert example["host"] == "open.ys7.com"
    assert (
        example["path"]
        == "/api/v3/otap/prop/{deviceSerial}/Video/1/DoorLockCtrl/FaceRecognitionVoiceTipSwitch"
    )
    assert "unparsed_curl" not in example["reasons"]
    assert "invalid_address" not in example["reasons"]


def test_multiline_curl_with_single_quoted_json_crlf() -> None:
    """CRLF inside a quoted JSON body must also be preserved, not split."""
    curl = (
        "curl --request POST 'https://open.ys7.com/api/crlf' \\\r\n--data-raw '{\r\n\t\"a\":1\r\n}'"
    )
    html = f"<pre>{curl}</pre>"
    example = _only_example(html)
    assert example["method"] == "POST"
    assert example["path"] == "/api/crlf"
    assert "unparsed_curl" not in example["reasons"]


def test_multiline_curl_with_double_quoted_json() -> None:
    """Double-quoted multi-line JSON body is also preserved."""
    curl = (
        'curl --request POST "https://open.ys7.com/api/dq" \\\n'
        '--data-raw "{\n'
        '\\t\\"enabled\\":true\n'
        '}"'
    )
    html = f"<pre>{curl}</pre>"
    example = _only_example(html)
    assert example["method"] == "POST"
    assert example["path"] == "/api/dq"
    assert "unparsed_curl" not in example["reasons"]


def test_multiline_curl_with_backslash_continuations_and_json() -> None:
    """A backslash-newline inside an unquoted region continues the logical
    line; a newline inside quotes does not end it."""
    curl = (
        "curl --location --request PUT \\\n"
        "'https://open.ys7.com/api/v3/otap/prop/{deviceSerial}/Video/1/x' \\\n"
        "--header 'Content-Type: application/json' \\\n"
        "--data-raw '{\n\t\"enabled\":true\n}'"
    )
    html = f"<pre>{curl}</pre>"
    example = _only_example(html)
    assert example["method"] == "PUT"
    assert "templated_path" in example["reasons"]
    assert "unparsed_curl" not in example["reasons"]


# ------------------------------------------------------------------ decoys


def test_curl_word_inside_quoted_json_does_not_spawn_command() -> None:
    """A literal 'curl' inside a quoted argument must not be treated as a
    new command."""
    curl = (
        "curl --request POST 'https://open.ys7.com/api/real' "
        '--data-raw \'{"cmd":"curl -X POST https://decoy.example.com/api"}\''
    )
    html = f"<pre>{curl}</pre>"
    examples = _examples(html)
    assert len(examples) == 1
    ex = examples[0]
    assert ex["method"] == "POST"
    assert ex["path"] == "/api/real"
    # The decoy text stays inside the evidence block only.
    assert ex["raw_address"] == "https://open.ys7.com/api/real"
    assert all("decoy" not in str(e) for e in examples) is False


def test_semicolon_inside_quoted_arg_does_not_split_command() -> None:
    """A semicolon inside a quoted --data-raw argument does not start a
    new command."""
    curl = (
        "curl --request POST 'https://open.ys7.com/api/one' "
        '--data-raw \'{"a":"curl https://decoy.example.com/b; curl https://decoy2.example.com/c"}\''
    )
    html = f"<pre>{curl}</pre>"
    examples = _examples(html)
    assert len(examples) == 1
    assert examples[0]["path"] == "/api/one"
    assert examples[0]["raw_address"] == "https://open.ys7.com/api/one"


def test_request_line_inside_quoted_json_ignored() -> None:
    """An HTTP request line embedded inside a quoted JSON value must not
    produce a separate example."""
    curl = (
        "curl --request POST 'https://open.ys7.com/api/outer' "
        '--data-raw \'{"msg":"POST /api/inner HTTP/1.1\\nHost: x"}\''
    )
    html = f"<pre>{curl}</pre>"
    examples = _examples(html)
    assert len(examples) == 1
    assert examples[0]["path"] == "/api/outer"
    assert examples[0]["raw_address"] == "https://open.ys7.com/api/outer"


# ------------------------------------------------------------------ multi-command


def test_two_distinct_curl_commands_on_one_line() -> None:
    """Two real curl commands separated by an unquoted semicolon produce
    two independent examples."""
    html = (
        "<pre>"
        "curl --request GET 'https://open.ys7.com/api/one'; "
        "curl --request POST 'https://open.ys7.com/api/two'"
        "</pre>"
    )
    examples = _examples(html)
    assert len(examples) == 2
    by_path = {e["path"]: e for e in examples}
    assert by_path["/api/one"]["method"] == "GET"
    assert by_path["/api/two"]["method"] == "POST"


def test_curl_and_other_shell_commands_mixed() -> None:
    """Only the curl command becomes an example; other shell commands are
    ignored but not merged into it."""
    html = "<pre>echo start; curl --request PUT 'https://open.ys7.com/api/put'; echo done</pre>"
    examples = _examples(html)
    assert len(examples) == 1
    assert examples[0]["method"] == "PUT"
    assert examples[0]["path"] == "/api/put"


def test_multi_url_curl_command_is_not_split_across_commands() -> None:
    """Multiple positional URLs in a single curl command stay as
    ``multiple_urls``, not split into separate commands."""
    html = (
        "<pre>curl --request POST 'https://open.ys7.com/api/a' 'https://open.ys7.com/api/b'</pre>"
    )
    example = _only_example(html)
    assert example["status"] == "example_only"
    assert "multiple_urls" in example["reasons"]
    assert example["raw_address"] is None


def test_multi_method_curl_command_preserved() -> None:
    """Two -X flags keep multiple_methods visible."""
    html = "<pre>curl --request GET --request POST 'https://open.ys7.com/api/m'</pre>"
    example = _only_example(html)
    assert "multiple_methods" in example["reasons"]
    assert example["method"] is None


# ------------------------------------------------------------------ unbalanced


def test_unbalanced_quote_keeps_unknown() -> None:
    """A missing closing quote makes the command unparsable; we do not
    guess method or path."""
    html = "<pre>curl --request POST 'https://open.ys7.com/api/broken --data-raw '{\"x\":1}'</pre>"
    example = _only_example(html)
    assert "unparsed_curl" in example["reasons"]
    assert example["method"] is None
    assert example["path"] is None


# ------------------------------------------------------------------ request lines


def test_http_request_line_example_preserved() -> None:
    """A bare HTTP request line in a code block still produces an
    example."""
    html = "<pre>POST /api/rl HTTP/1.1\nHost: open.ys7.com\n\n{}</pre>"
    example = _only_example(html)
    assert example["status"] == "example_only"
    assert example["method"] == "POST"
    assert example["path"] == "/api/rl"
    assert "unparsed_curl" not in example["reasons"]


def test_request_line_and_curl_in_same_block() -> None:
    """A block may contain both a request line and a curl command."""
    html = (
        "<pre>"
        "POST /api/rl HTTP/1.1\n"
        "Host: open.ys7.com\n"
        "\n"
        "curl --request POST 'https://open.ys7.com/api/rl'"
        "</pre>"
    )
    examples = _examples(html)
    assert len(examples) == 2
    assert all(e["path"] == "/api/rl" for e in examples)


# ------------------------------------------------------------------ safety


def test_json_block_does_not_produce_curl_candidate() -> None:
    """Pure JSON inside a code block never yields a curl example."""
    html = '<pre>{"cmd":"curl -X POST https://open.ys7.com/api/fake"}</pre>'
    assert _examples(html) == []


def test_curl_inside_comment_text_not_candidate() -> None:
    """A comment line mentioning curl must not produce an example."""
    html = "<pre># curl https://open.ys7.com/api/commented</pre>"
    assert _examples(html) == []


def test_continuation_only_block_is_noop() -> None:
    """A block that is only a backslash-newline continuation with no
    command produces nothing."""
    html = "<pre>\\\n--header 'x: y'</pre>"
    assert _examples(html) == []


def test_no_default_method_inferred() -> None:
    """curl without -X/--request keeps method=None; we never guess GET/POST."""
    html = "<pre>curl 'https://open.ys7.com/api/no-method'</pre>"
    example = _only_example(html)
    assert example["method"] is None
    assert "no_explicit_method" in example["reasons"]


def test_unknown_option_still_marks_unparsed() -> None:
    """An unknown option keeps the whole command unparsed."""
    html = "<pre>curl --bogus-option --request PUT 'https://open.ys7.com/api/u'</pre>"
    example = _only_example(html)
    assert "unparsed_curl" in example["reasons"]
    assert example["method"] is None
    assert example["path"] is None


def test_example_stays_example_not_declaration() -> None:
    """A parsed multiline curl example never becomes a declaration."""
    curl = (
        "curl --location --request PUT "
        "'https://open.ys7.com/api/v3/otap/prop/{deviceSerial}/Video/1/x' \\\n"
        "--data-raw '{\n\"a\":1\n}'"
    )
    html = f"<h2>Op</h2><pre>{curl}</pre>"
    out = extract_http_candidates(html)
    assert out["declarations"] == []
    assert len(out["examples"]) == 1
    assert out["examples"][0]["status"] == "example_only"


# ------------------------------------------------------------------ I007


def test_quoted_semicolon_argument_does_not_forge_second_command() -> None:
    """A literal ``';'`` argument to a non-curl command must not split the
    line into fake commands. ``echo ';' curl --request GET ...`` is a
    single echo command: the curl-shaped words are its arguments, not a
    curl example. Splitting must happen on lexically-unquoted separators,
    before quotes are stripped — never on post-shlex token values."""
    html = "<pre>echo ';' curl --request GET https://open.ys7.com/api/fake</pre>"
    assert _examples(html) == []


def test_quoted_semicolon_data_value_is_not_a_command_boundary() -> None:
    """``--data ';'`` is one curl command whose data value happens to be a
    semicolon. The quoted ``;`` is not a separator, and no second command
    may be forged after it."""
    html = "<pre>curl --request POST https://open.ys7.com/api/x --data ';'</pre>"
    example = _only_example(html)
    assert example["method"] == "POST"
    assert example["path"] == "/api/x"
    assert example["raw_address"] == "https://open.ys7.com/api/x"
    assert "unparsed_curl" not in example["reasons"]


def test_double_quoted_semicolon_data_value_is_not_a_boundary() -> None:
    """Same as above with double quotes: ``--data \";\"`` stays a value."""
    html = '<pre>curl --request PUT "https://open.ys7.com/api/dq" --data ";"</pre>'
    example = _only_example(html)
    assert example["method"] == "PUT"
    assert example["path"] == "/api/dq"


def test_escaped_semicolon_is_not_a_command_boundary() -> None:
    """An unquoted but backslash-escaped ``\\;`` is a literal character in
    the data value, never a command boundary. The probe command is a
    trivial ``;``-less invocation: if ``\\;`` split commands, the empty
    tail would forge a second parse attempt."""
    html = "<pre>curl --request POST https://open.ys7.com/api/e --data \\; echo</pre>"
    example = _only_example(html)
    assert example["method"] == "POST"
    assert example["path"] == "/api/e"


def test_real_separator_still_splits_after_quoted_semicolon() -> None:
    """``--data ';' ; curl ...`` : the quoted ``;`` is data, the unquoted
    ``;`` is a genuine boundary producing a second curl example."""
    html = (
        "<pre>"
        "curl --request POST https://open.ys7.com/api/first --data ';' ; "
        "curl --request GET https://open.ys7.com/api/second"
        "</pre>"
    )
    examples = _examples(html)
    assert len(examples) == 2
    by_path = {e["path"]: e for e in examples}
    assert by_path["/api/first"]["method"] == "POST"
    assert by_path["/api/second"]["method"] == "GET"


def test_unquoted_escaped_quote_does_not_open_quote_state() -> None:
    """``\\'`` outside quotes is a literal apostrophe, not a quote start.
    The second line is an independent curl command, not swallowed into an
    unterminated quote of the first."""
    html = (
        "<pre>"
        "curl --request GET 'https://open.ys7.com/api/q' --data \\'\n"
        "curl --request POST 'https://open.ys7.com/api/q2'"
        "</pre>"
    )
    examples = _examples(html)
    assert len(examples) == 2
    by_path = {e["path"]: e for e in examples}
    assert by_path["/api/q"]["method"] == "GET"
    assert by_path["/api/q2"]["method"] == "POST"


def test_unquoted_escaped_double_quote_does_not_open_quote_state() -> None:
    """``\\"`` outside quotes is a literal character; the following line
    remains an independent curl command."""
    html = (
        "<pre>"
        'curl --request GET "https://open.ys7.com/api/d" --data \\"\n'
        "curl --request DELETE 'https://open.ys7.com/api/d2'"
        "</pre>"
    )
    examples = _examples(html)
    assert len(examples) == 2
    by_path = {e["path"]: e for e in examples}
    assert by_path["/api/d"]["method"] == "GET"
    assert by_path["/api/d2"]["method"] == "DELETE"


def test_backslash_continuation_joins_without_extra_space() -> None:
    """Shell removes ``\\<newline>`` verbatim: ``G`` + continuation + ``ET``
    forms ``GET``, not ``G ET``."""
    curl = "curl --request G\\\nET https://open.ys7.com/api/join"
    html = f"<pre>{curl}</pre>"
    example = _only_example(html)
    assert example["method"] == "GET"
    assert example["path"] == "/api/join"
    assert "unparsed_curl" not in example["reasons"]


def test_backslash_continuation_inside_double_quotes_joins_tightly() -> None:
    """Inside double quotes, ``\\<newline>`` is also removed verbatim:
    ``\"/api/`` + continuation + ``part\"`` yields ``/api/part``."""
    curl = 'curl --request GET "https://open.ys7.com/api/\\\npart"'
    html = f"<pre>{curl}</pre>"
    example = _only_example(html)
    assert example["method"] == "GET"
    assert example["path"] == "/api/part"
    assert "unparsed_curl" not in example["reasons"]


def test_backslash_newline_inside_single_quotes_is_literal() -> None:
    """Inside single quotes nothing is escaped: the backslash-newline
    stays in the value, producing internal whitespace — the address is
    invalid, never silently repaired into a clean URL."""
    curl = "curl --request GET 'https://open.ys7.com/api/\\\npart'"
    html = f"<pre>{curl}</pre>"
    example = _only_example(html)
    assert example["method"] == "GET"
    assert example["address_kind"] == "invalid"
    assert example["path"] is None
    assert "invalid_address" in example["reasons"]


def test_curl_inside_heredoc_body_is_not_an_example() -> None:
    """A here-doc body is data consumed by the command, not a command
    stream: a ``curl`` line inside it must not become an example."""
    html = "<pre>cat &lt;&lt;'EOF'\ncurl https://open.ys7.com/api/heredoc\nEOF</pre>"
    assert _examples(html) == []


def test_curl_inside_dollar_command_substitution_is_not_an_example() -> None:
    """``$(...)`` nests a new command context whose words are arguments,
    not top-level commands; a nested curl must not be reported."""
    html = "<pre>echo $(curl https://open.ys7.com/api/sub)</pre>"
    assert _examples(html) == []


def test_block_with_unsupported_syntax_does_not_forge_inner_examples() -> None:
    """When a block mixes constructs we cannot reliably parse, we stay
    conservative: a curl-looking line inside an unparsed block is not
    harvested as a usable example."""
    html = (
        "<pre>result=$(curl https://open.ys7.com/api/inner)\n"
        "curl --request GET 'https://open.ys7.com/api/outer'</pre>"
    )
    examples = _examples(html)
    assert len(examples) == 1
    assert examples[0]["path"] == "/api/outer"


def test_backtick_substitution_curl_is_not_an_example() -> None:
    """``echo `curl ...``` : the backtick substitution is part of echo's
    arguments, not a standalone curl example."""
    html = "<pre>echo `curl https://open.ys7.com/api/tick`</pre>"
    assert _examples(html) == []


def test_control_structure_does_not_forge_examples() -> None:
    """An if/for/while control block is not a plain command list; its
    inner curl lines are not harvested as reliable examples."""
    html = "<pre>if true; then\ncurl https://open.ys7.com/api/if\nfi</pre>"
    assert _examples(html) == []


def test_single_quoted_dollar_substitution_is_literal_not_executed() -> None:
    """``'$(curl ...)'`` inside single quotes is literal text: no command
    is run and no inner example is forged; the outer curl parses normally."""
    html = (
        "<pre>curl --request POST 'https://open.ys7.com/api/lit' "
        "--data '$(curl https://decoy.example.com/x)'</pre>"
    )
    examples = _examples(html)
    assert len(examples) == 1
    assert examples[0]["path"] == "/api/lit"
    assert examples[0]["raw_address"] == "https://open.ys7.com/api/lit"


def test_single_quoted_backtick_is_literal_not_executed() -> None:
    """``'`curl ...`'`` inside single quotes is literal text; the outer
    curl parses normally and no decoy example appears."""
    html = (
        "<pre>curl --request POST 'https://open.ys7.com/api/lit2' "
        "--data '`curl https://decoy.example.com/y`'</pre>"
    )
    examples = _examples(html)
    assert len(examples) == 1
    assert examples[0]["path"] == "/api/lit2"


def test_http_request_line_with_query_is_not_shell_split() -> None:
    """``GET /api?a=1&b=2 HTTP/1.1`` is recognised by HTTP syntax first;
    its ``&`` is part of the query, never a shell command separator."""
    html = "<pre>GET /api?a=1&b=2 HTTP/1.1\nHost: open.ys7.com</pre>"
    example = _only_example(html)
    assert example["method"] == "GET"
    assert example["path"] == "/api"
    assert example["query"] == "a=1&b=2"
    assert "unparsed_curl" not in example["reasons"]


# ------------------------------------------------------------------ I008


import subprocess  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def _run_in_subprocess(html: str, timeout: int = 5) -> subprocess.CompletedProcess:
    """Run the parser in a fresh subprocess with an explicit timeout."""
    script = (
        "import sys, json\n"
        "from tools.documents.http_candidates import extract_http_candidates\n"
        "html = sys.stdin.read()\n"
        "out = extract_http_candidates(html)\n"
        "json.dumps(out)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        input=html,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(_REPO_ROOT),
        env={"PYTHONPATH": str(_REPO_ROOT), "PATH": "/usr/bin:/bin"},
    )


@pytest.mark.parametrize(
    "html",
    [
        "<pre>$</pre>",
        "<pre>$ curl --request GET https://h/api/x</pre>",
        "<pre>user@host$ curl --request GET https://h/api/y</pre>",
        "<pre>curl --request GET https://h/api/z > /dev/null</pre>",
        "<pre>if true; then\ncurl https://h/api/if > /dev/null\nfi</pre>",
        "<pre>cat <<'EOF'\ncurl https://h/api/heredoc\nEOF</pre>",
        "<pre>echo $(curl https://h/api/sub)</pre>",
    ],
    ids=[
        "single-dollar",
        "dollar-prompt-curl",
        "host-dollar-prompt",
        "redirect",
        "control-with-redirect",
        "heredoc",
        "command-substitution",
    ],
)
def test_parser_terminates_on_edge_cases(html: str) -> None:
    """Edge cases that used to hang the scanner must terminate within 5 s."""
    result = _run_in_subprocess(html)
    assert result.returncode == 0


def test_dollar_prompt_curl_extracts() -> None:
    """``$ curl ...`` prompt line parses the curl command."""
    example = _only_example("<pre>$ curl --request GET https://h/api/x</pre>")
    assert example["method"] == "GET"
    assert example["path"] == "/api/x"


def test_host_dollar_prompt_curl_extracts() -> None:
    """``user@host$ curl ...`` prompt line parses the curl command."""
    example = _only_example("<pre>user@host$ curl --request GET https://h/api/y</pre>")
    assert example["method"] == "GET"
    assert example["path"] == "/api/y"


def test_unquoted_template_path_extracted() -> None:
    """``{name}`` inside a URI is a template placeholder, not a shell group."""
    example = _only_example("<pre>curl --request GET /api/prop/{deviceSerial}/value</pre>")
    assert example["status"] == "example_only"
    assert example["method"] == "GET"
    assert example["path"] == "/api/prop/{deviceSerial}/value"
    assert "templated_path" in example["reasons"]
    assert "unparsed_curl" not in example["reasons"]


def test_unquoted_template_host_extracted() -> None:
    """``{areaDomain}`` host template is a legal target, not rejected."""
    example = _only_example("<pre>curl --request GET {areaDomain}/api/list</pre>")
    assert example["status"] == "example_only"
    assert example["method"] == "GET"
    assert example["host"] == "{areaDomain}"
    assert example["path"] == "/api/list"
    assert "templated_host" in example["reasons"]
    assert "unparsed_curl" not in example["reasons"]


def test_escaped_double_quote_in_url_preserved() -> None:
    """``\\\"`` inside a URL stays as a literal character in the raw value."""
    example = _only_example('<pre>curl --request GET "https://h/api/quote\\"x"</pre>')
    assert '\\"' in example["raw_address"]
    assert example["path"] == "/api/quote\\"


def test_escaped_double_quote_in_normal_argument() -> None:
    """``--data \\"k\\"`` is a literal escaped value, not a quote opener."""
    example = _only_example('<pre>curl --request POST https://h/api/eq --data \\"k\\"</pre>')
    assert example["method"] == "POST"
    assert example["path"] == "/api/eq"


def test_full_json_block_with_curl_word_no_example() -> None:
    """A complete JSON code block containing a curl string never yields
    an example."""
    html = '<pre>{"cmd":"curl -X POST https://h/api/fake"}</pre>'
    assert _examples(html) == []


def test_curl_in_comment_line_no_example() -> None:
    """A comment line containing ``curl`` produces no example."""
    html = "<pre># curl https://h/api/comment</pre>"
    assert _examples(html) == []


def test_heredoc_body_curl_not_example() -> None:
    """A curl line inside a here-doc body is data, not a command."""
    html = "<pre>cat <<'EOF'\ncurl https://h/api/heredoc\nEOF</pre>"
    assert _examples(html) == []


def test_substitution_curl_not_example() -> None:
    """A curl inside ``$(...)`` is an argument, not a command."""
    html = "<pre>echo $(curl https://h/api/sub)</pre>"
    assert _examples(html) == []


def test_control_structure_curl_not_example() -> None:
    """A curl inside an ``if`` block is not harvested."""
    html = "<pre>if true; then\ncurl https://h/api/if\nfi</pre>"
    assert _examples(html) == []


def test_command_group_braces_in_uri_not_rejected() -> None:
    """``{name}`` in a URI is a template, not a shell ``{ }`` command group."""
    example = _only_example("<pre>curl --request GET /api/{serial}/info</pre>")
    assert example["method"] == "GET"
    assert example["path"] == "/api/{serial}/info"
    assert "unparsed_curl" not in example["reasons"]
