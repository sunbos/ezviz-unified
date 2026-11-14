"""Validate a model-authored patch proposal and render standard diffs.

The preparer checks only the shape, provenance and syntax of a candidate
patch: the strict proposal schema, the operator-supplied input hash, an
explicit operator-supplied allowlist of SDK source paths, pinned base-file
hashes, exact single-occurrence anchored replacements and ``ast.parse`` on
the resulting text. It never executes candidate code, writes files back,
runs tests, calls a model, touches the network or the real API, and never
authorizes publication; it only emits a verified description of what the
proposal *would* change as standard ``difflib.unified_diff`` output.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import math
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

_SDK_PREFIX = "src/ezviz_unified/"
_HEX_DIGITS = frozenset("0123456789abcdef")
_PROPOSAL_KEYS = frozenset({"schema_version", "input_sha256", "edits"})
_EDIT_KEYS = frozenset({"path", "base_sha256", "old", "new"})
_UNPARSEABLE = object()

EVIDENCE = "proposal_shape_hash_and_syntax_only"

LIMITATIONS = [
    "only proposal shape, hash provenance, exact single-occurrence anchoring "
    "and Python syntax are validated; a well-formed proposal is not proof "
    "that the change is logically correct, complete, safe or sufficient",
    "the path allowlist and input_sha256 are supplied by the operator; no "
    "field inside the proposal can widen the allowed scope or self-authorize",
    "the emitted diffs are descriptive only: nothing is written to the "
    "repository, executed or tested by this tool",
    "base hashes witness file contents at read time and at the final "
    "re-check only; the repository must stay stable during validation",
    "no model is called, no patch is applied, no network or real API request "
    "is made, and no publication is authorized",
]


class ProposalError(ValueError):
    """Proposal shape, provenance, path or syntax validation failed."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256(text.encode("utf-8"))


def _is_hex64(value) -> bool:
    return type(value) is str and len(value) == 64 and all(char in _HEX_DIGITS for char in value)


def _string_sequence(value, label):
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ProposalError(f"{label} must be a sequence of unique strings")
    items = list(value)
    if not items:
        raise ProposalError(f"{label} must not be empty")
    seen = set()
    for item in items:
        if type(item) is not str or not item:
            raise ProposalError(f"{label} entries must be non-empty strings")
        if item in seen:
            raise ProposalError(f"{label} entries must be unique")
        seen.add(item)
    return items


def _canonical_sdk_path(value, label):
    """Require a canonical ``src/ezviz_unified/**.py`` relative path."""
    if type(value) is not str or not value:
        raise ProposalError(f"{label} must be a non-empty path string")
    if (
        value.startswith("/")
        or value.startswith("~")
        or "\\" in value
        or Path(value).is_absolute()
        or not value.startswith(_SDK_PREFIX)
        or not value.endswith(".py")
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ProposalError(f"{label} is not a canonical SDK source path")
    return value


def _resolve_root(path, label) -> Path:
    try:
        candidate = Path(path)
    except (TypeError, ValueError):
        candidate = None
    if candidate is None:
        raise ProposalError(f"{label} root is not a valid path")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        resolved = None
    if resolved is None:
        raise ProposalError(f"{label} root does not exist")
    try:
        is_dir = resolved.is_dir()
    except OSError:
        is_dir = False
    if not is_dir:
        raise ProposalError(f"{label} root is not a directory")
    return resolved


def _sdk_source_file(pkg_root: Path, tail: str, label) -> Path:
    """Resolve ``tail`` to a regular file anchored under ``pkg_root``.

    The anchor is resolved once by the caller: directories, missing targets
    and any link whose resolution leaves the anchor are rejected before a
    single byte is read.
    """
    if not tail or "\\" in tail or any(part in ("", ".", "..") for part in tail.split("/")):
        raise ProposalError(f"{label} is not a canonical SDK source path")
    candidate = pkg_root / tail
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        resolved = None
    if resolved is None:
        raise ProposalError(f"{label} does not exist")
    if not resolved.is_relative_to(pkg_root):
        raise ProposalError(f"{label} escapes the SDK source root")
    try:
        mode = os.lstat(candidate).st_mode
    except OSError:
        mode = None
    if mode is None or not stat.S_ISREG(mode):
        raise ProposalError(f"{label} is not a regular file")
    return resolved


def _read_source(pkg_root: Path, tail: str, label) -> bytes:
    resolved = _sdk_source_file(pkg_root, tail, label)
    try:
        data = resolved.read_bytes()
    except OSError:
        data = None
    if data is None:
        raise ProposalError(f"{label} is unreadable")
    return data


def _find_positions(text: str, needle: str) -> list:
    """Start offsets of ``needle``, counting overlapping occurrences.

    ``str.count`` reports only non-overlapping hits; the "exactly once" rule
    must also reject needles like ``aa`` inside ``aaa``. The scan stops at
    two hits because that is already a rejection.
    """
    positions = []
    start = 0
    while len(positions) < 2:
        index = text.find(needle, start)
        if index < 0:
            break
        positions.append(index)
        start = index + 1
    return positions


def _check_edit(edit):
    """Validate one edit's exact field shape and value rules."""
    if not isinstance(edit, dict):
        raise ProposalError("edit must be an object")
    if set(edit) != _EDIT_KEYS:
        raise ProposalError("edit fields must be exactly path, base_sha256, old and new")
    _canonical_sdk_path(edit["path"], "edit path")
    if not _is_hex64(edit["base_sha256"]):
        raise ProposalError("edit base_sha256 must be a lowercase sha256 hex digest")
    if type(edit["old"]) is not str or not edit["old"]:
        raise ProposalError("edit old must be a non-empty string")
    if type(edit["new"]) is not str:
        raise ProposalError("edit new must be a string")
    if edit["new"] == edit["old"]:
        raise ProposalError("edit new must differ from old")
    encodable = True
    try:
        edit["old"].encode("utf-8")
        edit["new"].encode("utf-8")
    except UnicodeError:
        encodable = False
    if not encodable:
        raise ProposalError("edit text must be encodable as UTF-8")


def prepare_proposal(
    repo_root: Path,
    proposal: object,
    allowed_paths: Sequence[str],
    *,
    input_sha256: str,
    max_bytes: int = 2097152,
) -> dict:
    """Validate ``proposal`` against pinned SDK sources; return a report.

    The proposal is data only: ``allowed_paths`` and ``input_sha256`` come
    from the operator and can never be widened or overridden by proposal
    fields. Nothing is written, executed, tested or published.
    """
    if not _is_hex64(input_sha256):
        raise ProposalError("input_sha256 must be a lowercase sha256 hex digest")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProposalError("max_bytes must be a positive integer")
    allow = _string_sequence(allowed_paths, "allowed_paths")
    for rel in allow:
        _canonical_sdk_path(rel, "allowed path")

    if not isinstance(proposal, dict):
        raise ProposalError("proposal must be an object")
    if set(proposal) != _PROPOSAL_KEYS:
        raise ProposalError(
            "proposal fields must be exactly schema_version, input_sha256 and edits"
        )
    if type(proposal["schema_version"]) is not int or proposal["schema_version"] != 1:
        raise ProposalError("proposal schema_version must be the integer 1")
    if type(proposal["input_sha256"]) is not str or proposal["input_sha256"] != input_sha256:
        raise ProposalError("proposal input hash does not match the operator hash")
    edits = proposal["edits"]
    if type(edits) is not list:
        raise ProposalError("proposal edits must be a list")

    allow_set = frozenset(allow)
    by_path = {}
    for edit in edits:
        _check_edit(edit)
        if edit["path"] not in allow_set:
            raise ProposalError("edit path is outside the operator allowlist")
        group = by_path.get(edit["path"])
        if group is None:
            group = {"base": edit["base_sha256"], "edits": []}
            by_path[edit["path"]] = group
        elif group["base"] != edit["base_sha256"]:
            raise ProposalError("edits for one file must pin the same base_sha256")
        group["edits"].append(edit)

    root = _resolve_root(repo_root, "repository")
    # Anchored once: if a parent directory is swapped for a symlink mid-run,
    # resolved targets leave this anchor and are rejected before reading.
    pkg_root = _resolve_root(root / _SDK_PREFIX, "SDK source")
    # The package anchor itself must live inside the repository: a symlinked
    # ``src`` or ``src/ezviz_unified`` resolving outside would otherwise be
    # adopted as the anchor and pass every per-file containment check.
    if not pkg_root.is_relative_to(root):
        raise ProposalError("SDK source root escapes the repository")
    for rel in allow:
        _sdk_source_file(pkg_root, rel[len(_SDK_PREFIX) :], "allowed path")

    files = []
    touched = []
    for path in sorted(by_path):
        group = by_path[path]
        base = group["base"]
        data = _read_source(pkg_root, path[len(_SDK_PREFIX) :], "edit target")
        if _sha256(data) != base:
            raise ProposalError("edit base hash does not match the current file")
        try:
            original = data.decode("utf-8")
        except UnicodeError:
            original = None
        if original is None:
            raise ProposalError("edit target is not UTF-8 text")
        ranges = []
        for edit in group["edits"]:
            positions = _find_positions(original, edit["old"])
            if len(positions) != 1:
                raise ProposalError("edit old text must occur exactly once")
            start = positions[0]
            ranges.append((start, start + len(edit["old"]), edit["new"]))
        ranges.sort(key=lambda item: item[0])
        for prev, nxt in zip(ranges, ranges[1:]):
            if nxt[0] < prev[1]:
                raise ProposalError("edit replacement ranges overlap")
        # Apply back-to-front over the ORIGINAL text: no edit can anchor
        # inside text produced by another edit.
        new_text = original
        for start, end, new in reversed(ranges):
            new_text = new_text[:start] + new + new_text[end:]
        if not new_text.endswith("\n"):
            raise ProposalError("edited file must keep a trailing newline")
        encodable = True
        try:
            new_text.encode("utf-8")
        except UnicodeError:
            encodable = False
        if not encodable:
            raise ProposalError("edited file is not encodable as UTF-8")
        parsed = True
        try:
            ast.parse(new_text, filename=path)
        except (SyntaxError, ValueError):
            parsed = False
        if not parsed:
            raise ProposalError("edited file does not parse as Python")
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile="a/" + path,
                tofile="b/" + path,
            )
        )
        files.append(
            {
                "path": path,
                "base_sha256": base,
                "new_sha256": _sha256_text(new_text),
                "new_text": new_text,
                "diff": diff,
            }
        )
        touched.append((path, base))

    # Re-read every edited source after processing: a drifted file fails the
    # whole report instead of emitting a mixed version.
    for path, base in touched:
        current = _read_source(pkg_root, path[len(_SDK_PREFIX) :], "edited source")
        if _sha256(current) != base:
            raise ProposalError("edited source changed during validation")

    report = {
        "schema_version": 1,
        "evidence": EVIDENCE,
        "input_sha256": input_sha256,
        "files": files,
        "has_changes": any(entry["new_sha256"] != entry["base_sha256"] for entry in files),
        "publication_authorized": False,
        "patch_applied": False,
        "real_api_used": False,
        "limitations": list(LIMITATIONS),
    }
    if len(_serialize_report(report).encode("utf-8")) > max_bytes:
        raise ProposalError("proposal report exceeds max_bytes")
    return report


def _serialize_report(report) -> str:
    """Return the exact stdout text for ``report``.

    The size check must measure real stdout bytes: indented UTF-8 JSON plus
    a trailing newline — the same serialization ``main`` emits. Encoding is
    verified here so lone surrogates fail with a fixed error instead of a
    later partial write.
    """
    try:
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        text.encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        text = None
    if text is None:
        raise ProposalError("proposal report is not JSON serializable")
    return text


def _dedup_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ProposalError("duplicate object key")
        obj[key] = value
    return obj


def _reject_constant(_value):
    raise ProposalError("non-finite number")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ProposalError("non-finite number")
    return result


def _load_proposal(path, max_bytes: int):
    """Read a proposal file with a hard byte cap and strict JSON parsing."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProposalError("max_bytes must be a positive integer")
    try:
        candidate = Path(path)
    except (TypeError, ValueError):
        candidate = None
    if candidate is None:
        raise ProposalError("proposal path is invalid")
    try:
        with candidate.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        raw = None
    if raw is None:
        raise ProposalError("proposal file is unreadable")
    if len(raw) > max_bytes:
        raise ProposalError("proposal file exceeds the byte budget")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        text = None
    if text is None:
        raise ProposalError("proposal file is not valid UTF-8")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_dedup_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except ValueError:
        value = _UNPARSEABLE
    if value is _UNPARSEABLE:
        raise ProposalError("proposal file is not strict JSON")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="harness.patch_proposal", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--proposal", type=Path, default=None)
    parser.add_argument("--allow", action="append", dest="allow", default=None)
    parser.add_argument("--input-sha256", dest="input_sha256", default=None)
    parser.add_argument("--max-bytes", default="2097152")
    args = parser.parse_args(argv)
    try:
        if args.proposal is None:
            raise ProposalError("a proposal file is required")
        if args.input_sha256 is None:
            raise ProposalError("an input sha256 is required")
        max_bytes = int(args.max_bytes, 10)
        proposal = _load_proposal(args.proposal, max_bytes)
        report = prepare_proposal(
            args.repo,
            proposal,
            tuple(args.allow) if args.allow is not None else (),
            input_sha256=args.input_sha256,
            max_bytes=max_bytes,
        )
        text = _serialize_report(report)
        if len(text.encode("utf-8")) > max_bytes:
            raise ProposalError("proposal report exceeds max_bytes")
    except Exception:
        sys.stderr.write("patch-proposal: validation failed\n")
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
