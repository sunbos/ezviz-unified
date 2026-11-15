"""从官方 HTML 正文提取 HTTP 接口声明与请求示例候选，供人工审核。

只处理调用方传入的 HTML 字符串：不访问文件、网络、SDK 或登记表，不执行
HTML 脚本。输出是审核候选清单：候选存在不代表端点真实存在或语义完整，
零候选也不代表没有接口。element_index 与 tools.documents.parse_html
的约定一致：在任何改动之前，按所有元素的文档顺序从零编号。
"""

from __future__ import annotations

import re
import shlex
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

SCHEMA_VERSION = 1

_HTTP_METHODS = frozenset(
    {
        "CONNECT",
        "DELETE",
        "GET",
        "HEAD",
        "OPTIONS",
        "PATCH",
        "POST",
        "PUT",
        "TRACE",
    }
)

# 地址/方法标签及元信息小节标签；比较时忽略全部空白（含 nbsp）、尾冒号与大小写。
_ADDRESS_LABEL_TEXTS = (
    "请求地址",
    "请求URL",
    "请求路径",
    "接口URL",
    "Request Address",
    "Request URL",
    "Request Path",
    "Requesting Address",
    "Interface URL",
    "InterfaceURL",
)
# 通用 “URL” 视为弱标签：取值必须带 HTTP/根路径/模板前缀，且单元内
# 存在显式方法或标题方法时才成候选；“POST/（用户地址）” 这类回调
# 写法没有合格前缀，永远不成候选。
_WEAK_ADDRESS_LABEL_TEXTS = ("URL",)
_METHOD_LABEL_TEXTS = (
    "请求方式",
    "请求方法",
    "请求类型",
    "Request Method",
    "Request Type",
    "HTTP Method",
)
_META_LABEL_TEXTS = (
    "接口功能",
    "接口说明",
    "接口信息",
    "请求",
    "请求参数",
    "请求示例",
    "返回数据",
    "返回字段",
    "返回码",
    "返回参数",
    "返回示例",
    "响应",
    "响应参数",
    "响应示例",
    "错误码",
    "调用限制",
    "相关接口",
    "常见问题",
    "注意事项",
    "前提条件",
    "前置条件",
    "操作步骤",
    "后续操作",
    "Function",
    "API Function",
    "Request",
    "Request Parameters",
    "Request Parameter",
    "Requesting Parameters",
    "Header",
    "Query",
    "Body",
    "HTTP Request Message",
    "Request Example",
    "Return Data",
    "Return Filed",
    "Return Field",
    "Return Code",
    "Return Parameters",
    "Return Parameter",
    "Return Example",
    "Returned Code",
    "Common Return Code",
    "Response",
    "Reponse",
    "Response Example",
    "Response Parameters",
    "Error Code",
    "Notes",
    "Note",
    "Prerequisites",
)
# 错位方法来源：这些“参数”标签之后紧跟单个 HTTP 方法 token 时，
# 该 token 按方法证据计入当前单元并记 misplaced_method 警告；
# 正常参数表仍是普通边界，不作为方法。
_METHOD_BEARING_META_TEXTS = (
    "请求参数",
    "Request Parameters",
    "Request Parameter",
    "Requesting Parameters",
)

_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_COLON_RE = re.compile(r"[\s:：]+$")
_SECTION_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*[.．、]?")
_REQUEST_LINE_RE = re.compile(
    r"(CONNECT|DELETE|GET|HEAD|OPTIONS|PATCH|POST|PUT|TRACE)\s+(\S+)\s+HTTP/1\.\d"
)
# 操作标题尾部显式标注的方法：如 “标题（GET）”“Title (POST)”。只认
# 括号内的方法 token，绝不从 Get/Query/Set 等词推断方法。
_TITLE_METHOD_RE = re.compile(
    r"[（(]\s*(CONNECT|DELETE|GET|HEAD|OPTIONS|PATCH|POST|PUT|TRACE)"
    r"\s*[）)]\s*$",
    re.IGNORECASE,
)
# 行内 “标签：取值” 的中英文冒号分隔符；取文本中最早出现的一处切分。
_INLINE_LABEL_SEPARATORS = (":", "：")

# 取参数字段（header/data/form/user/output/timeout/user-agent）：其值
# 不视为目标地址，也不用于推断方法。--request 与 --url 单独处理；
# 未列出的选项不猜取值边界，整条命令判为不可解析，由调用方保留证据。
_CURL_LONG_VALUE_OPTS = frozenset(
    {
        "connect-timeout",
        "data",
        "data-binary",
        "data-raw",
        "data-urlencode",
        "form",
        "header",
        "max-time",
        "output",
        "user",
        "user-agent",
    }
)
_CURL_LONG_FLAG_OPTS = frozenset(
    {
        "compressed",
        "include",
        "insecure",
        "location",
        "show-error",
        "silent",
        "verbose",
    }
)
_CURL_SHORT_VALUE_OPTS = frozenset("AdFHmou")
_CURL_SHORT_FLAG_OPTS = frozenset("sSLkiv")

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BOUNDARY_TAGS = _HEADING_TAGS | {"table", "pre"}
_VALUE_TAGS = frozenset(
    {
        "p",
        "li",
        "dd",
        "dt",
        "td",
        "th",
        "code",
        "span",
        "div",
        "section",
        "blockquote",
    }
)


def _squash(text: str) -> str:
    """去掉全部空白字符（含 nbsp）并小写化，用于标签比较。"""
    return _WHITESPACE_RE.sub("", text).casefold()


def _norm_text(text: str) -> str:
    """整理空白为单个空格，保留大小写与标点。"""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _label_key(text: str) -> str:
    return _squash(_TRAILING_COLON_RE.sub("", text))


_ADDRESS_LABELS = frozenset(_label_key(t) for t in _ADDRESS_LABEL_TEXTS)
_WEAK_ADDRESS_LABELS = frozenset(_label_key(t) for t in _WEAK_ADDRESS_LABEL_TEXTS)
_METHOD_LABELS = frozenset(_label_key(t) for t in _METHOD_LABEL_TEXTS)
_META_LABELS = frozenset(_label_key(t) for t in _META_LABEL_TEXTS)
_METHOD_BEARING_META_LABELS = frozenset(_label_key(t) for t in _METHOD_BEARING_META_TEXTS)
_ALL_LABELS = _ADDRESS_LABELS | _WEAK_ADDRESS_LABELS | _METHOD_LABELS | _META_LABELS


def _element_label_key(element: Tag) -> str | None:
    """元素是“最内层”标签载体时返回其标签键，否则返回 None。

    ul>li 这类嵌套中，外层元素与内层元素文本可能相同；只取最内层，
    避免同一标签因 DOM 嵌套被重复计数。
    """
    key = _label_key(element.get_text(" ", strip=True))
    if key not in _ALL_LABELS:
        return None
    for child in element.find_all(True):
        if _label_key(child.get_text(" ", strip=True)) in _ALL_LABELS:
            return None
    return key


def _has_label(element: Tag) -> bool:
    if _element_label_key(element) is not None:
        return True
    return any(_element_label_key(child) is not None for child in element.find_all(True))


def _is_meta_heading(title: str) -> bool:
    """标题去除前导编号后命中任一已知标签即视为元信息小节（边界）。"""
    return _SECTION_NUMBER_RE.sub("", _label_key(title)) in _ALL_LABELS


def _method_token(text: str | None) -> str | None:
    """文本恰为单个 HTTP 方法 token 时返回规范化方法，否则返回 None。"""
    if not text:
        return None
    normalized = _norm_text(text).upper()
    return normalized if normalized in _HTTP_METHODS else None


def _weak_url_prefixed(text: str) -> bool:
    """弱 URL 标签的取值须带 HTTP/根路径/模板前缀才允许成候选。"""
    lower = text.lower()
    return lower.startswith(("http://", "https://")) or text.startswith(("/", "{"))


_INLINE_LABEL_TAGS = frozenset({"li", "p"})


def _element_inline_label(element: Tag) -> dict[str, Any] | None:
    """li/p 文本形如 “标签：取值” 的最内层载体返回标签键、取值与取值元素。

    与 _element_label_key 一样只取最内层，避免 ul>li 或 li>p 嵌套把同一
    行内标签重复计数；取值是首个中英文冒号后的全部剩余文本。
    """
    if element.name not in _INLINE_LABEL_TAGS:
        return None
    text = element.get_text(" ", strip=True)
    positions = [
        position
        for position in (text.find(separator) for separator in _INLINE_LABEL_SEPARATORS)
        if position >= 0
    ]
    if not positions:
        return None
    position = min(positions)
    key = _SECTION_NUMBER_RE.sub("", _label_key(text[:position]))
    if key not in _ALL_LABELS:
        return None
    value = _norm_text(text[position + 1 :])
    if not value:
        return None
    for child in element.find_all(True):
        if _element_label_key(child) is not None or _element_inline_label(child) is not None:
            return None
    codes = element.find_all("code")
    carrier = codes[0] if len(codes) == 1 else element
    return {
        "key": key,
        "value": value,
        "label": _norm_text(text[:position]),
        "carrier": carrier,
    }


def _has_inline_label(element: Tag) -> bool:
    """元素自身或任一后代为 li/p 行内标签（“标签：取值”）即视为取值边界。"""
    if _element_inline_label(element) is not None:
        return True
    return any(_element_inline_label(child) is not None for child in element.find_all(True))


def _find_label_value(label: Tag, indices: dict[int, int]) -> dict[str, Any] | None:
    """在标签之后、下一处标签/标题/表格/代码块边界之前找第一个取值元素。"""
    for node in label.next_elements:
        if not isinstance(node, Tag):
            continue
        if node.name in _BOUNDARY_TAGS:
            return None
        if _has_label(node) or _has_inline_label(node):
            return None
        if node.name in _VALUE_TAGS:
            text = _norm_text(node.get_text(" ", strip=True))
            if text:
                return {"text": text, "element_index": indices[id(node)]}
    return None


def _table_label_items(table: Tag, indices: dict[int, int]) -> list[dict[str, Any]]:
    """仅扫描两列键值行：首列为地址/方法标签时取第二列取值。"""
    items: list[dict[str, Any]] = []
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = [cell for cell in row.find_all(["th", "td"]) if cell.find_parent("tr") is row]
        if len(cells) != 2:
            continue
        key = _label_key(cells[0].get_text(" ", strip=True))
        if key in _ADDRESS_LABELS or key in _WEAK_ADDRESS_LABELS:
            kind = "address"
        elif key in _METHOD_LABELS:
            kind = "method"
        else:
            continue
        codes = cells[1].find_all("code")
        carrier = codes[0] if len(codes) == 1 else cells[1]
        value = _norm_text(carrier.get_text(" ", strip=True))
        weak = kind == "address" and key in _WEAK_ADDRESS_LABELS
        if weak and not _weak_url_prefixed(value):
            # 弱 URL 标签取值缺少合格前缀：不成候选。
            continue
        items.append(
            {
                "kind": kind,
                "raw": value or None,
                "label_text": _norm_text(cells[0].get_text(" ", strip=True)),
                "label_index": indices.get(id(cells[0])),
                "value_index": indices.get(id(carrier)),
                "weak": weak,
            }
        )
    return items


def _templates_balanced(text: str) -> bool:
    """花括号成对且不交叉才算平衡，用于 host 模板校验。"""
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _parse_address(raw: str | None) -> dict[str, Any]:
    """分类地址原文：absolute / root_relative / template / invalid。

    不补 host、不按 host 推 region、不解码、不改大小写、不去尾斜杠、
    不去反引号，不做任何 URI 修复；query/fragment 原样保留。内部空白
    （多值或方法粘连）、不平衡的 host 模板、非法端口、缺失或非斜杠
    开头的 path 一律判 invalid。fragment、userinfo、空或不可解析地址
    标记为不可关联（pairable=False），不得成为 declared_pair。
    """
    result: dict[str, Any] = {
        "kind": "invalid",
        "host": None,
        "path": None,
        "query": "",
        "fragment": "",
        "reasons": [],
        "pairable": False,
    }
    if raw is None or not raw.strip():
        result["reasons"].append("invalid_address")
        return result
    text = raw.strip()
    if _WHITESPACE_RE.search(text):
        # 内部空白意味着多值或与其他内容粘连，不拆分不修复。
        result["reasons"].append("invalid_address")
        return result
    if '"' in text:
        # 地址原文中的 ``"`` 是引号残留（URI 中本不可能出现）：只解析
        # ``"`` 之前的可解析部分，raw 原文不改写、不剥离。
        text = text.split('"', 1)[0]
        if not text:
            result["reasons"].append("invalid_address")
            return result
    if "`" in text:
        # 地址原文残留反引号是源文档问题：保留原文、判为不可用，
        # 不剥离不修复。
        result["reasons"].append("invalid_address")
        result["reasons"].append("address_backtick")
        return result
    lower = text.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        try:
            parts = urlsplit(text)
            # 读取 port 触发 urllib 的端口合法性校验。
            _ = parts.port
        except ValueError:
            result["reasons"].append("invalid_address")
            return result
        if (
            not parts.netloc
            or not parts.path
            or not parts.path.startswith("/")
            or not _templates_balanced(parts.netloc)
        ):
            result["reasons"].append("invalid_address")
            return result
        result["kind"] = "absolute"
        result["host"] = parts.netloc
        result["path"] = parts.path
        result["query"] = parts.query
        result["fragment"] = parts.fragment
        result["pairable"] = True
        if "@" in parts.netloc:
            result["reasons"].append("address_userinfo")
            result["pairable"] = False
        if "{" in parts.netloc or "}" in parts.netloc:
            result["reasons"].append("templated_host")
        if "{" in parts.path or "}" in parts.path:
            result["reasons"].append("templated_path")
    elif text.startswith("{"):
        body = text
        if "#" in body:
            body, result["fragment"] = body.split("#", 1)
        if "?" in body:
            body, result["query"] = body.split("?", 1)
        head, separator, rest = body.partition("/")
        if not separator or not _templates_balanced(head):
            # 缺少斜杠路径，或 host 模板花括号不完整。
            result["reasons"].append("invalid_address")
            return result
        result["kind"] = "template"
        result["host"] = head or None
        result["path"] = "/" + rest
        result["reasons"].append("templated_host")
        if "{" in result["path"] or "}" in result["path"]:
            result["reasons"].append("templated_path")
        result["pairable"] = True
    elif text.startswith("/"):
        try:
            parts = urlsplit(text)
        except ValueError:
            parts = None
        if parts is None or parts.netloc or not parts.path:
            # 协议相对地址（//host/path）不视为根路径，保持 invalid 可见。
            result["reasons"].append("invalid_address")
            return result
        result["kind"] = "root_relative"
        result["path"] = parts.path
        result["query"] = parts.query
        result["fragment"] = parts.fragment
        result["pairable"] = True
        if "{" in parts.path or "}" in parts.path:
            result["reasons"].append("templated_path")
    else:
        result["reasons"].append("invalid_address")
    if result["fragment"]:
        result["reasons"].append("address_fragment")
        result["pairable"] = False
    return result


def _base_candidate(section_title: str, section_index: int | None) -> dict[str, Any]:
    return {
        "ordinal": 0,
        "status": "",
        "method": None,
        "raw_method": None,
        "raw_address": None,
        "address_kind": "invalid",
        "host": None,
        "path": None,
        "query": "",
        "fragment": "",
        "section_title": section_title,
        "section_index": section_index,
        "evidence": [],
        "reasons": [],
    }


def _item_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in items:
        role = "address" if item["kind"] == "address" else "method"
        evidence.append(
            {
                "role": role + "_label",
                "element_index": item["label_index"],
                "text": item["label_text"],
            }
        )
        if item["value_index"] is not None:
            evidence.append(
                {
                    "role": role + "_value",
                    "element_index": item["value_index"],
                    "text": item["raw"],
                }
            )
    return evidence


def _dedupe(items: list[dict[str, Any]], key) -> list[list[dict[str, Any]]]:
    """按 key 分组，保持文档顺序；完全相同的标签取值不重复计数。"""
    groups: dict[Any, list[dict[str, Any]]] = {}
    order: list[Any] = []
    for item in items:
        k = key(item)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(item)
    return [groups[k] for k in order]


def _resolve_unit(
    items: list[dict[str, Any]],
    section_title: str,
    section_index: int | None,
    declarations: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    title_method: str | None = None,
    title_method_evidence: dict[str, Any] | None = None,
) -> None:
    """把一个连续声明单元解析为至多一条声明；不做跨单元或序号配对。"""
    addresses = [i for i in items if i["kind"] == "address"]
    methods = [i for i in items if i["kind"] == "method"]
    strong_addresses = [i for i in addresses if not i.get("weak")]
    weak_addresses = [i for i in addresses if i.get("weak")]
    if strong_addresses:
        # 通用 URL 弱标签让位于具名地址标签，不重复计数。
        addresses = strong_addresses
    elif weak_addresses and (methods or title_method):
        # 弱 URL 标签只在有显式方法或标题方法配对时才成候选。
        addresses = weak_addresses
    elif weak_addresses:
        addresses = []
    if not addresses:
        # 孤立方法不得推断地址。
        for item in methods:
            warnings.append(
                {
                    "code": "orphan_method",
                    "element_index": item["label_index"],
                }
            )
        return
    kept = {id(item) for item in addresses}
    evidence_items = [item for item in items if item["kind"] == "method" or id(item) in kept]
    declaration = _base_candidate(section_title, section_index)
    if title_method_evidence is not None:
        declaration["evidence"].append(title_method_evidence)
    declaration["evidence"].extend(_item_evidence(evidence_items))
    address_groups = _dedupe(addresses, key=lambda i: i["raw"])
    method_groups = _dedupe(methods, key=lambda i: i["raw"].upper() if i["raw"] else None)
    label_methods = {group[0]["raw"].upper() for group in method_groups if group[0]["raw"]}
    if len(address_groups) > 1:
        declaration["status"] = "ambiguous"
        declaration["reasons"] = ["multiple_addresses"]
    else:
        address_item = address_groups[0][0]
        parsed = _parse_address(address_item["raw"])
        declaration["raw_address"] = address_item["raw"]
        declaration["address_kind"] = parsed["kind"]
        declaration["host"] = parsed["host"]
        declaration["path"] = parsed["path"]
        declaration["query"] = parsed["query"]
        declaration["fragment"] = parsed["fragment"]
        reasons = list(parsed["reasons"])
        label_raw = next((group[0]["raw"] for group in method_groups if group[0]["raw"]), None)
        if not method_groups and not title_method:
            declaration["status"] = "address_only"
            declaration["reasons"] = ["missing_method"] + reasons
        elif len(method_groups) > 1:
            declaration["status"] = "ambiguous"
            declaration["reasons"] = ["multiple_methods"] + reasons
        elif title_method is not None and label_methods and title_method not in label_methods:
            # 标题方法与标签方法冲突：保持不明确，不猜胜者。
            declaration["status"] = "ambiguous"
            declaration["reasons"] = ["conflicting_methods"] + reasons
        else:
            declaration["raw_method"] = label_raw if label_raw else title_method
            normalized = label_raw.upper() if label_raw else (title_method or "")
            if normalized not in _HTTP_METHODS:
                # 未知方法或多值方法保留 raw，标 ambiguous，不猜首项。
                declaration["status"] = "ambiguous"
                declaration["reasons"] = ["unknown_method"] + reasons
            elif not parsed["pairable"]:
                declaration["status"] = "ambiguous"
                declaration["method"] = normalized
                declaration["reasons"] = reasons
            else:
                declaration["status"] = "declared_pair"
                declaration["method"] = normalized
                declaration["reasons"] = reasons
    declaration["ordinal"] = len(declarations)
    declarations.append(declaration)


def _apply_address(candidate: dict[str, Any], raw: str | None) -> None:
    parsed = _parse_address(raw)
    candidate["raw_address"] = raw
    candidate["address_kind"] = parsed["kind"]
    candidate["host"] = parsed["host"]
    candidate["path"] = parsed["path"]
    candidate["query"] = parsed["query"]
    candidate["fragment"] = parsed["fragment"]
    candidate["reasons"].extend(parsed["reasons"])


def _example_candidate(
    section_title: str,
    section_index: int | None,
    block_index: int | None,
    text: str,
) -> dict[str, Any]:
    example = _base_candidate(section_title, section_index)
    example["status"] = "example_only"
    example["evidence"] = [
        {
            "role": "code_block",
            "element_index": block_index,
            "text": text,
        }
    ]
    example["related_declaration_ordinals"] = []
    example["agrees_with_declaration"] = None
    return example


def _looks_like_curl_target(token: str) -> bool:
    """仅当位置参数形如 http(s) 地址、根路径或 host 模板时才视为目标。"""
    value = token.strip()
    lower = value.lower()
    return (
        lower.startswith(("http://", "https://")) or value.startswith("/") or value.startswith("{")
    )


def _parse_curl_args(
    args: list[str],
) -> tuple[list[str], list[str], str | None]:
    """收集 curl 的 -X/--request 方法与 --url/位置目标；不执行、不推断。

    白名单取参数选项的值被跳过；任何未列出的选项都使整条命令判为不可
    解析：提前返回未知选项本身，由调用方标记候选、保留证据，方法与
    目标一律不接受，不猜未知选项是否消耗紧随的参数。
    """
    methods: list[str] = []
    targets: list[str] = []
    positional_only = False
    index = 0
    while index < len(args):
        token = args[index]
        if positional_only or token == "-":
            if _looks_like_curl_target(token):
                targets.append(token)
            index += 1
            continue
        if token == "--":
            positional_only = True
            index += 1
            continue
        if token.startswith("--"):
            name, separator, attached = token[2:].partition("=")
            if name == "request":
                if separator:
                    methods.append(attached)
                elif index + 1 < len(args):
                    index += 1
                    methods.append(args[index])
            elif name == "url":
                if separator:
                    targets.append(attached)
                elif index + 1 < len(args):
                    index += 1
                    targets.append(args[index])
            elif name in _CURL_LONG_FLAG_OPTS:
                pass  # 纯开关选项，无取值
            elif name in _CURL_LONG_VALUE_OPTS:
                if not separator and index + 1 < len(args):
                    index += 1  # 吞掉独立的取值参数
            else:
                # 未知长选项：不猜取值边界，整条命令不可解析。
                return methods, targets, token
            index += 1
            continue
        if token.startswith("-"):
            # 短选项簇：逐字符扫描；取参数字符吞掉其余部分作为取值，
            # 其余部分为空时吞掉下一个 token；未知字符判不可解析。
            chars = token[1:]
            position = 0
            while position < len(chars):
                char = chars[position]
                if char == "X":
                    rest = chars[position + 1 :]
                    if rest:
                        methods.append(rest)
                    elif index + 1 < len(args):
                        index += 1
                        methods.append(args[index])
                    break
                if char in _CURL_SHORT_FLAG_OPTS:
                    position += 1
                    continue
                if char in _CURL_SHORT_VALUE_OPTS:
                    if not chars[position + 1 :] and index + 1 < len(args):
                        index += 1
                    break
                # 未知短选项字符：不猜取值边界，整条命令不可解析。
                return methods, targets, "-" + char
            index += 1
            continue
        if _looks_like_curl_target(token):
            targets.append(token)
        index += 1
    return methods, targets, None


def _curl_example(
    args: list[str],
    section_title: str,
    section_index: int | None,
    block_index: int | None,
    text: str,
) -> dict[str, Any]:
    example = _example_candidate(section_title, section_index, block_index, text)
    methods, targets, unknown = _parse_curl_args(args)
    if unknown is not None:
        # 未知选项不猜取值边界：方法与目标均不接受，候选标记不可解析；
        # 代码块原文仍在 evidence 中，供人工核对。
        example["reasons"].append("unparsed_curl")
        _apply_address(example, None)
        return example
    if not methods:
        example["reasons"].append("no_explicit_method")
    elif len({method.upper() for method in methods}) > 1:
        # 方法冲突不猜胜者：method 与 raw_method 均保持空。
        example["reasons"].append("multiple_methods")
    else:
        example["raw_method"] = methods[0]
        normalized = methods[0].upper()
        if normalized in _HTTP_METHODS:
            example["method"] = normalized
        else:
            example["reasons"].append("unknown_method")
    distinct_targets = list(dict.fromkeys(targets))
    if len(distinct_targets) > 1:
        # 多个 URL 目标不取首个：保留多值可见，地址留空。
        _apply_address(example, None)
        example["reasons"].append("multiple_urls")
    else:
        _apply_address(example, distinct_targets[0] if distinct_targets else None)
    return example


# POSIX shell 引号/转义状态（贯穿分块与切段的同一状态机）。
_QUOTE_NONE = 0
_QUOTE_SINGLE = 1
_QUOTE_DOUBLE = 2

# 字符在逻辑行内的角色：未引用字面、引号内字面、或结构（here-doc
# 正文、命令替换/反引号/控制结构等不支持语法，整段保守拒绝）。
_MARK_LITERAL = 0
_MARK_QUOTED = 1
_MARK_STRUCTURAL = 2

# 双引号内允许反斜杠转义的字符（POSIX shell 语义）：\$ \` \" \\ \<newline>。
_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')

# 未引用 here-doc 定界符：<<[-] [\'\"]?WORD[\'\"]?。
_HEREDOC_MARKER_RE = re.compile(r"<<-?\s*(?P<q>['\"]?)(?P<word>[^\s'\";|&<>]+)(?P=q)")

# 字面区域中本身即构成命令边界的字符；&& || 单独处理。
_COMMAND_SEPARATORS = frozenset(";|&")

# 在命令段内一旦出现即把整段判为结构（不从中提取示例）的字符。
# ``$`` 单独处理：``$``/``$(`` 是替换或提示符，``user@host$`` 是合法提示符。
_STRUCTURAL_CHARS = frozenset("`(){}!<>")


def _scan_logical_lines(text: str) -> list[tuple[str, list[int]]]:
    """把代码块按引号/续行切为逻辑行，并为每个字符标注字面/引号/结构。

    结构识别是“最小保守”原则：不配对嵌套、不展开结构内容，一旦在
    未引用区域发现不支持语法（here-doc 标记、``$(``/`` ` `` 替换、
    命令组 ``( ) { }``、重定向 ``<`` ``>``、命令位置上的控制关键字），
    只把包含它的命令段标为 ``_MARK_STRUCTURAL``，由调用方整体拒绝，
    绝不从结构内部制造 curl/HTTP 示例。
    """
    out: list[str] = []
    marks: list[int] = []
    line_marks: list[int] = []  # 每个字符所属逻辑行序号
    in_quote = _QUOTE_NONE
    escaped = False
    line_index = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_quote == _QUOTE_SINGLE:
            if char == "\n" or char == "\r":
                # 单引号内换行是字面值，保留在行内。
                if char == "\r" and index + 1 < length and text[index + 1] == "\n":
                    index += 1
                    out.append("\r")
                    marks.append(_MARK_QUOTED)
                    line_marks.append(line_index)
                out.append("\n")
                marks.append(_MARK_QUOTED)
                line_marks.append(line_index)
            else:
                out.append(char)
                marks.append(_MARK_QUOTED)
                line_marks.append(line_index)
                if char == "'":
                    in_quote = _QUOTE_NONE
        elif in_quote == _QUOTE_DOUBLE:
            if escaped:
                if char == "\n" or char == "\r":
                    # 双引号内续行：\<newline> 原样删除，不产字符。
                    if char == "\r" and index + 1 < length and text[index + 1] == "\n":
                        index += 1
                elif char in _DOUBLE_QUOTE_ESCAPABLE:
                    # 双引号内可转义序列 ``\X``：重编码为 ``\\`` + ``\X``，
                    # 使下游 shlex 只解码一次后 token 仍保留原始 ``\X``
                    # 字面（源证据不改写）；``\"`` 不会成为引号边界。
                    for piece in ("\\", "\\", "\\", char):
                        out.append(piece)
                        marks.append(_MARK_QUOTED)
                        line_marks.append(line_index)
                else:
                    # 双引号内不可转义序列 ``\X`` 在 shell 中按字面保留；
                    # ``\X`` 原样输出，shlex 解码一次后仍是 ``\X``。
                    out.append("\\")
                    marks.append(_MARK_QUOTED)
                    line_marks.append(line_index)
                    out.append(char)
                    marks.append(_MARK_QUOTED)
                    line_marks.append(line_index)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_quote = _QUOTE_NONE
                out.append(char)
                marks.append(_MARK_QUOTED)
                line_marks.append(line_index)
            elif char == "\n" or char == "\r":
                # 双引号内换行是字面值，保留在行内。
                if char == "\r" and index + 1 < length and text[index + 1] == "\n":
                    index += 1
                    out.append("\r")
                    marks.append(_MARK_QUOTED)
                    line_marks.append(line_index)
                out.append("\n")
                marks.append(_MARK_QUOTED)
                line_marks.append(line_index)
            else:
                out.append(char)
                marks.append(_MARK_QUOTED)
                line_marks.append(line_index)
        else:
            if char == "'":
                in_quote = _QUOTE_SINGLE
                out.append(char)
                marks.append(_MARK_LITERAL)
                line_marks.append(line_index)
            elif char == '"':
                in_quote = _QUOTE_DOUBLE
                out.append(char)
                marks.append(_MARK_LITERAL)
                line_marks.append(line_index)
            elif char == "\\" and index + 1 < length:
                nxt = text[index + 1]
                if nxt == "\n" or nxt == "\r":
                    # 未引用区域续行：按 shell 语义原样删除，不加空格。
                    index += 1
                    if nxt == "\r" and index + 1 < length and text[index + 1] == "\n":
                        index += 1
                else:
                    # 转义字符（\' \" \; \\ \$ \` 等）：两字都是字面值，
                    # 不改变 quote 状态、不构成命令边界；标 QUOTED 避免
                    # 被当作字面分隔符或结构起点。
                    out.append(char)
                    marks.append(_MARK_QUOTED)
                    line_marks.append(line_index)
                    index += 1
                    out.append(nxt)
                    marks.append(_MARK_QUOTED)
                    line_marks.append(line_index)
            elif char == "\n" or char == "\r":
                # 字面换行：逻辑行切分点。
                if char == "\r" and index + 1 < length and text[index + 1] == "\n":
                    index += 1
                    out.append("\r")
                    marks.append(_MARK_LITERAL)
                    line_marks.append(line_index)
                out.append("\n")
                marks.append(_MARK_LITERAL)
                line_marks.append(line_index)
                line_index += 1
            else:
                out.append(char)
                marks.append(_MARK_LITERAL)
                line_marks.append(line_index)
        index += 1
    if escaped:
        out.append("\\")
        marks.append(_MARK_LITERAL)
        line_marks.append(line_index)

    # 切分逻辑行并记录行内结构标记（字面换行/引号内换行已处理）。
    lines: list[tuple[str, list[int]]] = []
    current: list[str] = []
    current_marks: list[int] = []
    seen = -1
    for position, mark in enumerate(line_marks):
        if mark != seen and current:
            lines.append(("".join(current), current_marks))
            current = []
            current_marks = []
        seen = mark
        current.append(out[position])
        current_marks.append(marks[position])
    if current:
        lines.append(("".join(current), current_marks))

    # 对每个逻辑行做最小结构标注：不配对、不展开、不解释嵌套，
    # 仅保证一旦发现不支持语法，整段标结构后绝不进入。控制关键字
    # 是跨行结构：一旦出现，同一 pre 块内其后所有逻辑行全部保守
    # 标为结构（不做配对，也不尝试恢复）。
    scanned: list[tuple[str, list[int]]] = []
    poisoned = False
    for line, mark in lines:
        new_marks, has_control = _scan_line_structures(line, mark)
        if poisoned:
            new_marks = [_MARK_STRUCTURAL] * len(line)
        elif has_control:
            poisoned = True
        scanned.append((line, new_marks))

    # 跨行结构合并：here-doc 标记行使后续物理行成为结构正文；
    # 凡以结构标记开头的物理行，追加到最近一条含结构段的逻辑行，
    # 绝不冒充独立命令。
    result: list[tuple[str, list[int]]] = []
    i = 0
    while i < len(scanned):
        line, mark = scanned[i]
        if mark and mark[0] == _MARK_STRUCTURAL and result:
            prev_line, prev_mark = result[-1]
            result[-1] = (
                prev_line + "\n" + line,
                prev_mark + [_MARK_STRUCTURAL] + list(mark),
            )
            i += 1
            continue
        # 若本行含 here-doc 标记（``<<`` 且结构标记），把后续所有物理
        # 行并回本行：here-doc 正文与定界符都是数据，不是命令。
        heredoc_start = line.find("<<")
        if heredoc_start >= 0 and any(
            m == _MARK_STRUCTURAL for m in mark[heredoc_start : heredoc_start + 2]
        ):
            merged_line = line
            merged_mark = list(mark)
            i += 1
            while i < len(scanned):
                next_line, next_mark = scanned[i]
                merged_line += "\n" + next_line
                merged_mark += [_MARK_STRUCTURAL] + list(next_mark)
                i += 1
            result.append((merged_line, merged_mark))
            continue
        result.append((line, mark))
        i += 1
    return result


def _scan_line_structures(line: str, mark: list[int]) -> tuple[list[int], bool]:
    """对单行做最小结构标注：不支持语法出现时整段标记。

    返回 ``(与原 ``mark`` 等长的新掩码, 是否发现命令位置上的控制关键字)``。
    仅做三件事：
    1. 把 here-doc 标记 ``<<[-] delim`` 标为结构；
    2. 把 ``$(``/`` ` `` 替换、``( ) { }`` 命令组、``<`` ``>`` 重定向、
       命令位置上的控制关键字标为结构；
    3. 保证 ``while`` 循环每轮都推进 ``index`` 或结束。

    控制关键字是跨行结构：本函数只上报 ``has_control``，由调用方把同一
    pre 块内其后的逻辑行一并保守标为结构；这里不做配对、不做解释。
    """
    marks = list(mark)
    length = len(line)
    index = 0
    command_start = True  # 位于段首（命令位置），用于关键字与 $ 提示符
    has_control = False
    while index < length:
        if marks[index] != _MARK_LITERAL:
            index += 1
            continue
        char = line[index]
        if char == "\n" or char in _COMMAND_SEPARATORS:
            command_start = True
            index += 1
            continue
        if char.isspace():
            index += 1
            continue
        # 非空白字面字符：识别结构标记，然后推进到本 token 末尾。
        if char == "#" and (command_start or line[index - 1].isspace()):
            # ``#`` 在命令位置或空白后是行注释开始，标到行尾。
            # URI 中的 ``#``（如 ``/api#frag``）前面是 ``/``，不是注释。
            end = line.find("\n", index)
            end = length if end < 0 else end
            for position in range(index, end):
                marks[position] = _MARK_STRUCTURAL
            index = end
            continue
        if char == "<" and index + 1 < length and line[index + 1] == "<":
            match = _HEREDOC_MARKER_RE.match(line, index)
            if match:
                for position in range(index, match.end()):
                    marks[position] = _MARK_STRUCTURAL
                # here-doc 正文/定界符行都是结构；标到本逻辑行尾。
                for position in range(index, length):
                    marks[position] = _MARK_STRUCTURAL
                index = length
                continue
        if char == "$" and index + 1 < length and line[index + 1] == "(":
            # ``$(`` 是命令替换：标结构。普通 ``$``/``user@host$`` 提示符
            # 保持字面，交给下游 ``endswith("$")`` 提示符剥离处理。
            marks[index] = _MARK_STRUCTURAL
            index += 1
            continue
        if char in "`()!<>":
            marks[index] = _MARK_STRUCTURAL
            index += 1
            command_start = False
            continue
        # 普通字面 token：推进到 token 末尾（未引用字符），看是否控制关键字。
        # ``{``/``}`` 不作为 token 边界：URI 中的 ``{name}`` 是合法模板，
        # 不是 ``{ cmd; }`` 命令组。
        start = index
        while index < length:
            if marks[index] != _MARK_LITERAL:
                break
            c = line[index]
            if c.isspace() or c in _COMMAND_SEPARATORS or c in "`()!<>":
                break
            index += 1
        word = line[start:index]
        # 只有当 ``{``/``}`` 是独立语法 token（前后是空白/分隔符/行首）
        # 时才按命令组处理；URI 中的 ``{name}`` 是 token 的一部分。
        if word in ("{", "}"):
            for position in range(start, index):
                marks[position] = _MARK_STRUCTURAL
            command_start = False
            continue
        if command_start and word in {
            "if",
            "then",
            "else",
            "elif",
            "fi",
            "for",
            "while",
            "until",
            "do",
            "done",
            "case",
            "esac",
            "select",
            "time",
            "coproc",
        }:
            # 控制结构：整段保守拒绝，不配对、不进入；并上报给调用方，
            # 让同一 pre 块内其后的逻辑行一并按结构处理。
            has_control = True
            for position in range(start, length):
                marks[position] = _MARK_STRUCTURAL
            index = length
        if word:
            command_start = False
    return marks, has_control


def _split_commands(line: str, mark: list[int]) -> tuple[list[str], list[bool]]:
    """按掩码把逻辑行切成命令段，并标记各段是否含不支持的结构。

    仅在字面区域识别命令边界（``;``、``&&``、``||``、``&``、``|``）；
    结构字符、注释、here-doc 标记都已由掩码标出，不再按 token 值猜
    边界。返回 ``(段文本列表, 各段是否含结构标记)``；含结构标记的段
    不提取示例，由调用方保留为不可解析证据。
    """
    commands: list[str] = []
    flags: list[bool] = []
    current: list[str] = []
    current_structural = False
    index = 0
    length = len(line)

    def flush() -> None:
        nonlocal current, current_structural
        text = "".join(current).strip()
        if text:
            commands.append(text)
            flags.append(current_structural)
        current = []
        current_structural = False

    while index < length:
        char = line[index]
        if mark[index] != _MARK_LITERAL:
            current.append(char)
            if mark[index] == _MARK_STRUCTURAL:
                current_structural = True
            index += 1
            continue
        if char == ";":
            flush()
            index += 1
            continue
        if char in "&|":
            flush()
            index += 2 if index + 1 < length and line[index + 1] == char else 1
            continue
        current.append(char)
        index += 1
    flush()
    return commands, flags


def _extract_block_examples(
    pre: Tag,
    indices: dict[int, int],
    section_title: str,
    section_index: int | None,
) -> list[dict[str, Any]]:
    """仅从 pre 代码块提取示例：行首 curl 命令与严格 METHOD target HTTP/1.x 行。

    命令边界在去引号前、仍保留 quote/escape 出处的阶段区分；含
    here-doc/命令替换/控制结构/重定向/花括号命令组的段保守保留为
    ``unparsed_curl`` 证据，不从内部虚构可用示例。URI 中的 ``{name}``
    模板是合法目标，不据此拒绝。
    """
    text = pre.get_text()
    block_index = indices.get(id(pre))
    lines = _scan_logical_lines(text)
    # 证据文本用原始 ``pre`` 文本，不用去引号后的解析视图。
    evidence_text = _norm_text(text)
    out: list[dict[str, Any]] = []
    for line, mark in lines:
        if not line.strip():
            continue
        # HTTP request-line 先按 HTTP 语法识别：整行是
        # ``METHOD target HTTP/1.x`` 时，target 里的 ``&``/``;`` 等是
        # query 字面值，不是 shell 命令边界。仅当整行（去首尾空白）无
        # 结构掩码时成立，避免结构段里的同形文本冒充请求行。
        stripped_line = line.strip()
        lead = len(line) - len(line.lstrip())
        request = _REQUEST_LINE_RE.fullmatch(stripped_line)
        if request and all(
            char_mark == _MARK_LITERAL for char_mark in mark[lead : lead + len(stripped_line)]
        ):
            example = _example_candidate(section_title, section_index, block_index, stripped_line)
            example["method"] = request.group(1)
            example["raw_method"] = request.group(1)
            _apply_address(example, request.group(2))
            out.append(example)
            continue
        commands, flags = _split_commands(line, mark)
        for command, has_structural in zip(commands, flags, strict=True):
            stripped = command.strip()
            if not stripped:
                continue
            if has_structural:
                # 含 here-doc/替换/控制结构/重定向/花括号组的段：
                # 不从中提取示例；若段首是 curl 则保留一条不可解析
                # 证据，其余结构段静默。
                head = stripped.split(None, 1)[0] if stripped else ""
                if head.rsplit("/", 1)[-1] == "curl":
                    example = _example_candidate(
                        section_title, section_index, block_index, evidence_text
                    )
                    example["reasons"].append("unparsed_curl")
                    _apply_address(example, None)
                    out.append(example)
                continue
            try:
                tokens = shlex.split(stripped)
            except ValueError:
                tokens = None
            if tokens is None:
                # 引号不配平等无法分词：仅当行首（可含 $ 提示符）是 curl
                # 时给出不可解析候选，不从残缺文本猜测方法或地址。
                probe = stripped[1:].lstrip() if stripped.startswith("$") else stripped
                head = probe.split(None, 1)[0] if probe else ""
                if head.rsplit("/", 1)[-1] == "curl":
                    example = _example_candidate(
                        section_title, section_index, block_index, evidence_text
                    )
                    example["reasons"].append("unparsed_curl")
                    _apply_address(example, None)
                    out.append(example)
                continue
            if tokens and tokens[0].endswith("$"):
                # 可选 shell 提示符（$、user@host$ 之类），仅出现在段首。
                tokens = tokens[1:]
            if not tokens:
                continue
            if tokens[0].rsplit("/", 1)[-1] != "curl":
                # 忽略嵌于字符串/其他文本等非命令起始位置的 curl。
                continue
            out.append(
                _curl_example(tokens[1:], section_title, section_index, block_index, evidence_text)
            )
    return out


def extract_http_candidates(html: str) -> dict[str, Any]:
    """提取 HTTP 声明与示例候选；不判断官方内容或真实 API 正确性。"""
    if not isinstance(html, str):
        raise ValueError(f"html must be a str, got {type(html).__name__}")
    soup = BeautifulSoup(html, "html.parser")
    indices = {id(element): index for index, element in enumerate(soup.find_all(True))}
    for element in soup.find_all(["script", "style"]):
        element.decompose()
    for line_break in soup.find_all("br"):
        # 与 parse_html 一致：br 可能带恢复出的子节点，不删除子树。
        line_break.insert_before("\n")
        line_break.unwrap()

    declarations: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    section_title = ""
    section_index: int | None = None
    # 前导段落资格：仅当首个标题、表格、代码块或声明/元信息标签尚未
    # 出现时，独立段落尾部的显式 (VERB) 标注才可充当操作标题。
    prelude_eligible = True
    # 当前操作域的标题方法：操作标题（或前导窗口内的独立段落）尾部显式
    # (VERB) 标注提供的方法证据，跨 URL/Request/Header/Response/Reponse
    # 等元信息小节保持有效，新操作域开启时重置。
    title_method: str | None = None
    title_method_evidence: dict[str, Any] | None = None
    unit: list[dict[str, Any]] = []

    def flush_unit() -> None:
        if unit:
            _resolve_unit(
                unit,
                section_title,
                section_index,
                declarations,
                warnings,
                title_method,
                title_method_evidence,
            )
            unit.clear()

    for element in soup.find_all(True):
        if element.find_parent("table") is not None or element.find_parent("pre") is not None:
            continue
        key = _element_label_key(element)
        if element.name in _HEADING_TAGS:
            prelude_eligible = False
            title = _norm_text(element.get_text(" ", strip=True))
            if key is None:
                if _has_label(element):
                    # 内层标签载体稍后按标签处理，标题本身不再是边界。
                    continue
                numbered = _SECTION_NUMBER_RE.sub("", _label_key(title))
                if (
                    numbered in _ADDRESS_LABELS
                    or numbered in _WEAK_ADDRESS_LABELS
                    or numbered in _METHOD_LABELS
                ):
                    # 地址/方法标题按标签处理，保留当前操作域。
                    key = numbered
            if key is None or key in _META_LABELS:
                flush_unit()
                if not _is_meta_heading(title):
                    # 非元信息标题开启新的操作域并重置标题方法；标题
                    # 尾部显式 (VERB) 标注提供方法证据。
                    section_title = title
                    section_index = indices[id(element)]
                    match = _TITLE_METHOD_RE.search(title)
                    title_method = match.group(1).upper() if match else None
                    title_method_evidence = (
                        {
                            "role": "title_method",
                            "element_index": indices[id(element)],
                            "text": title,
                        }
                        if match
                        else None
                    )
                continue
        elif element.name == "table":
            prelude_eligible = False
            flush_unit()
            # 表格边界自成单元：不与表外标签跨边界配对。
            items = _table_label_items(element, indices)
            if items:
                _resolve_unit(
                    items,
                    section_title,
                    section_index,
                    declarations,
                    warnings,
                    title_method,
                    title_method_evidence,
                )
            continue
        elif element.name == "pre":
            prelude_eligible = False
            flush_unit()
            for example in _extract_block_examples(element, indices, section_title, section_index):
                example["ordinal"] = len(examples)
                examples.append(example)
            continue
        elif key is None:
            inline = _element_inline_label(element)
            if inline is not None:
                # 行内 “标签：取值” 本身是声明/元信息标签，前导窗口关闭。
                prelude_eligible = False
            elif (
                element.name == "p"
                and prelude_eligible
                and element.find_parent("blockquote") is None
            ):
                # 前导窗口内、不在表格/代码块/引用内的独立段落，尾部带
                # 显式 (VERB) 标注时按操作标题处理并提供方法证据。
                title_text = _norm_text(element.get_text(" ", strip=True))
                match = _TITLE_METHOD_RE.search(title_text)
                if match:
                    flush_unit()
                    section_title = title_text
                    section_index = indices[id(element)]
                    title_method = match.group(1).upper()
                    title_method_evidence = {
                        "role": "title_method",
                        "element_index": indices[id(element)],
                        "text": title_text,
                    }
                    prelude_eligible = False
                    continue
            if inline is None:
                continue
            # 行内 “标签：取值” 与独立标签元素走同一套边界/配对语义，
            # 只在最内层载体上识别一次，不重复统计父/子元素。
            key = inline["key"]
            if key in _META_LABELS:
                if key in _METHOD_BEARING_META_LABELS:
                    token = _method_token(inline["value"])
                    if token is not None:
                        unit.append(
                            {
                                "kind": "method",
                                "raw": token,
                                "label_text": inline["label"],
                                "label_index": indices[id(element)],
                                "value_index": indices.get(id(inline["carrier"])),
                                "weak": False,
                            }
                        )
                        warnings.append(
                            {
                                "code": "misplaced_method",
                                "element_index": indices[id(element)],
                            }
                        )
                flush_unit()
                continue
            weak = key in _WEAK_ADDRESS_LABELS
            raw = inline["value"]
            if weak and not _weak_url_prefixed(raw):
                continue
            is_address = key in _ADDRESS_LABELS or weak
            if is_address and any(
                item["kind"] == "address" and item["raw"] != raw for item in unit
            ):
                # 新的不同地址标签结束当前待配对单元。
                flush_unit()
            unit.append(
                {
                    "kind": "address" if is_address else "method",
                    "raw": raw,
                    "label_text": inline["label"],
                    "label_index": indices[id(element)],
                    "value_index": indices.get(id(inline["carrier"])),
                    "weak": weak,
                }
            )
            continue
        prelude_eligible = False
        if key in _ADDRESS_LABELS or key in _WEAK_ADDRESS_LABELS or key in _METHOD_LABELS:
            weak = key in _WEAK_ADDRESS_LABELS
            value = _find_label_value(element, indices)
            raw = value["text"] if value else None
            if weak and (raw is None or not _weak_url_prefixed(raw)):
                # 弱 URL 标签取值缺少合格前缀：不成候选。
                if value is None:
                    warnings.append(
                        {
                            "code": "missing_label_value",
                            "element_index": indices[id(element)],
                        }
                    )
                continue
            is_address = key in _ADDRESS_LABELS or weak
            if is_address and any(
                item["kind"] == "address" and item["raw"] != raw for item in unit
            ):
                # 新的不同地址标签结束当前待配对单元。
                flush_unit()
            unit.append(
                {
                    "kind": "address" if is_address else "method",
                    "raw": raw,
                    "label_text": _norm_text(element.get_text(" ", strip=True)),
                    "label_index": indices[id(element)],
                    "value_index": value["element_index"] if value else None,
                    "weak": weak,
                }
            )
            if value is None:
                warnings.append(
                    {
                        "code": "missing_label_value",
                        "element_index": indices[id(element)],
                    }
                )
        else:
            # 其余元信息标签为子节边界；“参数”标签之后紧跟单个 HTTP
            # 方法 token 时按错位方法计入当前单元并记 misplaced_method。
            if key in _METHOD_BEARING_META_LABELS:
                value = _find_label_value(element, indices)
                token = _method_token(value["text"]) if value else None
                if token is not None:
                    unit.append(
                        {
                            "kind": "method",
                            "raw": token,
                            "label_text": _norm_text(element.get_text(" ", strip=True)),
                            "label_index": indices[id(element)],
                            "value_index": value["element_index"],
                            "weak": False,
                        }
                    )
                    warnings.append(
                        {
                            "code": "misplaced_method",
                            "element_index": indices[id(element)],
                        }
                    )
            flush_unit()
    flush_unit()

    for example in examples:
        same_domain = [d for d in declarations if d["section_index"] == example["section_index"]]
        # 同操作域声明全部保留为关联候选，即使 path 不匹配。
        example["related_declaration_ordinals"] = [d["ordinal"] for d in same_domain]
        explicit_pairs = [d for d in same_domain if d["status"] == "declared_pair"]
        ambiguous_source = (
            example["method"] is None
            or example["path"] is None
            or "unknown_method" in example["reasons"]
            or "multiple_methods" in example["reasons"]
        )
        if not same_domain or ambiguous_source or not explicit_pairs:
            # 无同域声明、方法/路径未知或比对来源不明确：无法判断。
            example["agrees_with_declaration"] = None
        elif any(
            d["method"] == example["method"] and d["path"] == example["path"]
            for d in explicit_pairs
        ):
            # 方法与路径均显式一致才算同意。
            example["agrees_with_declaration"] = True
        else:
            # 存在显式声明但方法或路径冲突：保持可见，不覆盖。
            example["agrees_with_declaration"] = False

    return {
        "schema_version": SCHEMA_VERSION,
        "declarations": declarations,
        "examples": examples,
        "warnings": warnings,
    }
