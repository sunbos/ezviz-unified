"""把静态 SDK 参考采集结果归一化为 derive/verify 可用的快照布局。

新采集脚本（reference_collectors/{domestic,overseas}_sdk.py）把 manifest.json
与原始 HTML 保存在独立的 region 根目录；本工具把该目录离线归一化为含
catalog.json 与页面 wrapper 的快照子树，供现有 derive.py/verify.py 使用。
全程不联网、不执行 HTML；所有写入先进入临时目录，全部校验通过后才发布，
输出目录必须是全新目录，失败不留半成品。

CLI: python -m tools.documents.normalize_sdk_reference \
    --input PATH --output PATH --region domestic|overseas
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

REGIONS = ("domestic", "overseas")
TERMINAL_FAILURES = {
    "http_error",
    "redirect_outside_or_alias",
    "not_html",
    "missing_html_title",
    "network_error",
}
URL_HOST = "open.ys7.com"
ENCODING_WARNING_KIND = "source_invalid_utf8_bytes"
ENCODING_WARNING_POLICY = "replacement for derived text only; original bytes preserved unchanged"
CLI_ERROR_MESSAGE = "normalize-sdk-reference: validation failed"

_RESERVED_OUTPUT_FILES = {"manifest.json", "catalog.json", "README.md"}
_RESERVED_OUTPUT_DIRS = {"pages", "quality"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def document_identity(source_url: str) -> str:
    """公开文档 URL 的局部哈希身份键；仅作归档键，不是匿名化。"""
    return str(int(_sha256(source_url.encode("utf-8"))[:16], 16))


def _strict_json(text: str, description: str):
    def reject_duplicate(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{description}: duplicate JSON object key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"{description}: non-finite JSON number is not allowed")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{description}: non-finite JSON number is not allowed")
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"{description}: invalid JSON") from exc


def _validate_url(url, description: str):
    if not isinstance(url, str) or not url:
        raise ValueError(f"{description}: url must be a non-empty string")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ValueError(f"{description}: url is not parseable") from exc
    if parsed.scheme != "https" or parsed.hostname != URL_HOST:
        raise ValueError(f"{description}: url is not an official static document URL")
    if parsed.port not in (None, 443):
        raise ValueError(f"{description}: url uses a nonstandard port")
    if "@" in parsed.netloc or parsed.username is not None:
        raise ValueError(f"{description}: url must not carry user-info")
    if parsed.fragment or parsed.query:
        raise ValueError(f"{description}: url must not carry fragment or query")
    if not parsed.path.endswith(".html"):
        raise ValueError(f"{description}: url is not a static .html document")
    segments = parsed.path.split("/")
    if any(segment in ("", ".", "..") for segment in segments[1:-1]) or segments[-1] in (
        "",
        ".",
        "..",
    ):
        raise ValueError(f"{description}: url path contains ambiguous segments")


def _load_manifest(source: Path):
    manifest_file = source / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError("input manifest.json is missing")
    manifest_file = manifest_file.resolve()
    if not manifest_file.is_relative_to(source):
        raise ValueError("input manifest.json resolves outside the input root")
    manifest_bytes = manifest_file.read_bytes()
    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("input manifest.json is not valid UTF-8") from exc
    manifest = _strict_json(manifest_text, "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain a JSON object")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError("manifest.json entries must be a list")
    return manifest, manifest_bytes, entries


def _validate_entries(source: Path, entries):
    seen_urls = set()
    seen_paths = set()
    seen_targets = {}
    pending = []
    for index, entry in enumerate(entries):
        description = f"manifest entry #{index}"
        if not isinstance(entry, dict):
            raise ValueError(f"{description}: entry must be an object")
        url = entry.get("url")
        _validate_url(url, description)
        if url in seen_urls:
            raise ValueError(f"{description}: duplicate url in manifest")
        seen_urls.add(url)
        discovered = entry.get("discovered_from")
        if not isinstance(discovered, list) or not all(
            isinstance(item, str) for item in discovered
        ):
            raise ValueError(f"{description}: discovered_from must be a list of source urls")
        status = entry.get("status")
        if status == "ok":
            local_path = entry.get("local_path")
            sha = entry.get("sha256")
            size = entry.get("bytes")
            if not isinstance(local_path, str) or not local_path:
                raise ValueError(f"{description}: ok entry is missing local_path")
            pure = PurePosixPath(local_path)
            if (
                pure.is_absolute()
                or not pure.parts
                or any(part in ("", ".", "..") for part in pure.parts)
            ):
                raise ValueError(f"{description}: local_path escapes the input root")
            # local_path 必须是 canonical POSIX relative spelling：`./`、重复分隔符、
            # 尾部 `/` 等别名拼写在 PurePosixPath 规范化后消失，会让同一登记路径
            # 有多个 manifest 写法；必须在原始字符串上拒绝，而不是先规范化再比较
            if local_path != pure.as_posix():
                raise ValueError(
                    f"{description}: local_path is not a canonical POSIX relative path"
                )
            normalized = str(pure)
            first = pure.parts[0]
            if first in _RESERVED_OUTPUT_DIRS or normalized in _RESERVED_OUTPUT_FILES:
                raise ValueError(f"{description}: local_path collides with reserved output paths")
            if normalized in seen_paths:
                raise ValueError(f"{description}: local_path conflicts with another entry")
            seen_paths.add(normalized)
            if (
                not isinstance(sha, str)
                or len(sha) != 64
                or not all(c in "0123456789abcdef" for c in sha)
            ):
                raise ValueError(f"{description}: ok entry has an invalid sha256")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError(f"{description}: ok entry has an invalid byte count")
            target = (source / Path(*pure.parts)).resolve()
            if not target.is_relative_to(source):
                raise ValueError(f"{description}: local_path resolves outside the input root")
            if not target.is_file():
                raise ValueError(f"{description}: local_path does not exist in the input")
            # dangling或间接符号链接会让多个 local_path 指向同一真实文件，必须拒绝
            if target in seen_targets:
                raise ValueError(f"{description}: local_path resolves to an already claimed file")
            seen_targets[target] = url
            pending.append((url, normalized, target, sha, size))
        elif status in TERMINAL_FAILURES:
            pass
        elif status in ("queued", "running", None):
            raise ValueError(f"{description}: manifest is incomplete; entry has no terminal status")
        else:
            raise ValueError(f"{description}: unknown entry status; not a terminal collector state")
    # 一次性读取并核验全部原始字节；返回已验证字节供发布阶段复用，避免二次读取
    raw_files = {}
    for url, normalized, target, sha, size in pending:
        data = target.read_bytes()
        if len(data) != size:
            raise ValueError("manifest byte count does not match the stored file")
        if _sha256(data) != sha:
            raise ValueError("manifest sha256 does not match the stored file")
        raw_files[url] = (normalized, data)
    return raw_files


def _category_paths(url: str):
    parts = [part for part in PurePosixPath(urlsplit(url).path).parts if part not in ("/",)]
    platform = None
    if len(parts) >= 4 and parts[0] == "doc" and parts[1] in ("zh", "en"):
        platform = parts[2]
    paths = [["静态SDK参考"]]
    if platform:
        paths.append(["静态SDK参考", f"{platform} 平台参考"])
    else:
        paths.append(["静态SDK参考", "通用参考"])
    return paths


def _entry_title(entry, url):
    title = entry.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return PurePosixPath(urlsplit(url).path).name


def _decode_source(raw: bytes, local_path: str):
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        warning = {
            "kind": ENCODING_WARNING_KIND,
            "display_policy": ENCODING_WARNING_POLICY,
            "replacement_characters": text.count("�"),
            "canonical_raw_file": local_path,
            "canonical_raw_sha256": _sha256(raw),
        }
        return text, warning


def _publish(staging: Path, output: Path):
    output.mkdir(parents=True)
    try:
        for item in sorted(staging.iterdir()):
            shutil.move(str(item), output / item.name)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def normalize(input_directory: Path, output_directory: Path, region: str) -> dict:
    """离线归一化一个静态 SDK 参考采集目录；验证失败抛 ValueError。"""
    if region not in REGIONS:
        raise ValueError(f"region must be one of {REGIONS}")
    # 已占用检查必须落在原始路径字符串上：悬空符号链接 resolve 后丢失链接身份，
    # 目标尚不存在会让 exists() 与 lexists() 双双误放行，随后 mkdir 建到链接目标处
    raw_output = Path(output_directory)
    if os.path.lexists(raw_output):
        raise ValueError("output path already exists")
    source = Path(input_directory).resolve()
    output = raw_output.resolve()
    if not source.is_dir():
        raise ValueError("input directory does not exist")
    # resolve 之后目标位置也可能已被占用（例如父级链接指向既有目录），再校验一次
    if os.path.lexists(output):
        raise ValueError("output path already exists")
    if source == output or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("input and output directories must not overlap")
    if source.parent == Path("/") or len(source.parts) < 2:
        raise ValueError("input directory must not be a filesystem root")

    manifest, manifest_bytes, entries = _load_manifest(source)
    # traversal_finished 是采集器声明遍历收尾的显式标记：只有 exact True 才证明
    # manifest 覆盖完整闭包；False/缺失/真值但非布尔都按未完成输入整体拒绝
    if manifest.get("traversal_finished") is not True:
        raise ValueError("input manifest traversal did not finish; refusing partial collection")
    raw_files = _validate_entries(source, entries)

    identities = {}
    for entry in entries:
        doc_id = document_identity(entry["url"])
        if doc_id in identities:
            raise ValueError("manifest urls collide on the derived document identity")
        identities[doc_id] = entry["url"]

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".normalize-{region}-", dir=output.parent
    ) as temporary:
        staging = Path(temporary)
        (staging / "manifest.json").write_bytes(manifest_bytes)
        catalog = []
        archived = 0
        unavailable = 0
        encoding_warnings = []
        for entry in entries:
            url = entry["url"]
            doc_id = document_identity(url)
            row = {
                "region": region,
                "doc_id": doc_id,
                "doc_id_kind": "local_url_hash_identity",
                "source_kind": "static_sdk_reference",
                "source_url": url,
                "title": _entry_title(entry, url),
                "memberships": [{"category_path": path} for path in _category_paths(url)],
                "source_receipt": dict(entry),
            }
            if entry["status"] != "ok":
                row["status"] = "source_unavailable"
                unavailable += 1
                catalog.append(row)
                continue
            local_path, raw = raw_files[url]
            text, warning = _decode_source(raw, local_path)
            wrapper = {
                "doc_id": doc_id,
                "title": row["title"],
                "html_content": text,
                "encoding_warning": warning,
            }
            wrapper_bytes = (json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            wrapper_relative = f"pages/{region}/{doc_id}.source.json"
            wrapper_path = staging / wrapper_relative
            wrapper_path.parent.mkdir(parents=True, exist_ok=True)
            wrapper_path.write_bytes(wrapper_bytes)
            raw_destination = staging / Path(*PurePosixPath(local_path).parts)
            if raw_destination.exists():
                raise ValueError("normalized local_path collides with a wrapper output path")
            raw_destination.parent.mkdir(parents=True, exist_ok=True)
            raw_destination.write_bytes(raw)
            row.update(
                {
                    "status": "archived",
                    "raw_file": wrapper_relative,
                    "raw_file_sha256": _sha256(wrapper_bytes),
                    "content_sha256": _sha256(text.encode("utf-8")),
                    "encoding_warning": warning,
                }
            )
            if warning:
                encoding_warnings.append({"source_url": url, "warning": warning})
            archived += 1
            catalog.append(row)

        catalog.sort(key=lambda row: row["source_url"])
        catalog_bytes = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (staging / "catalog.json").write_bytes(catalog_bytes)
        summary = {
            "schema_version": 1,
            "kind": "static_sdk_reference_normalization",
            "region": region,
            "source_manifest_sha256": _sha256(manifest_bytes),
            "manifest_scope_roots": manifest.get("scope_roots"),
            "manifest_traversal_finished": manifest.get("traversal_finished"),
            "documents": len(catalog),
            "archived_documents": archived,
            "unavailable_documents": unavailable,
            "missing_pages": [
                {"source_url": row["source_url"], "source_status": row["source_receipt"]["status"]}
                for row in catalog
                if row["status"] == "source_unavailable"
            ],
            "encoding_warnings": encoding_warnings,
            "checks": [
                "offline normalization only",
                "original bytes preserved verbatim under the same relative paths",
                "wrapper raw_file_sha256 and content_sha256 cover derived text only",
            ],
            "derived_verified": False,
            "semantic_contract_accuracy_verified": False,
            "live_api_verified": False,
        }
        quality = staging / "quality"
        quality.mkdir(parents=True, exist_ok=True)
        (quality / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        readme = (
            "# 静态 SDK 参考归一化快照\n\n"
            f"- region: `{region}`\n"
            f"- documents: {len(catalog)}（archived {archived} / source_unavailable {unavailable}）\n"
            f"- 来源 manifest SHA-256: `{summary['source_manifest_sha256']}`\n\n"
            "本目录由 `tools.documents.normalize_sdk_reference` 离线生成：原始 HTML "
            "字节按原相对路径保留，`catalog.json` 是派生索引，"
            "`pages/{region}/<doc_id>.source.json` 是 derive/verify 使用的正文 wrapper。"
            "编码告警仅表示派生文本使用 U+FFFD 替换，原字节未修改。\n\n"
            "尚未执行 derive/verify，不代表接口语义或真实 API 正确性。\n"
        )
        (staging / "README.md").write_text(readme, encoding="utf-8")
        _publish(staging, output)
    return summary


def _fail_cli(message: str) -> None:
    """argparse 错误出口统一收敛为固定消息，不回显原始路径或取值。"""
    print(CLI_ERROR_MESSAGE, file=sys.stderr)
    raise SystemExit(2) from None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="离线归一化静态 SDK 参考采集结果为 derive/verify 快照"
    )
    parser.error = _fail_cli  # type: ignore[method-assign]
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region", choices=REGIONS, required=True)
    args = parser.parse_args()
    try:
        summary = normalize(args.input, args.output, args.region)
    except (OSError, ValueError):
        print(CLI_ERROR_MESSAGE, file=sys.stderr)
        raise SystemExit(2) from None
    print(
        json.dumps(
            {
                "region": summary["region"],
                "documents": summary["documents"],
                "archived_documents": summary["archived_documents"],
                "unavailable_documents": summary["unavailable_documents"],
                "encoding_warnings": len(summary["encoding_warnings"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
