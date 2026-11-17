"""SP066 explicit root-file archiver (public verified Git blob metadata).

Archives exactly the 11 pinned repository root files named by a SHA-pinned
plan: one bounded HTTPS GET per file to api.github.com git-blob endpoints,
strict base64 decode, byte-length and git-blob SHA-1 recomputation, byte
SHA-256 object naming, then a local manifest. It never prints bodies, headers,
locators or exception chains; per-file failures get fixed statuses (plus an
optional integer HTTP code) and the run exits non-zero with
complete_for_selected_root_files=false. It claims no whole-repository review,
no license review, no runtime adoption, no integration verification, and no
completion for unexamined root entries or repositories without a current
root-file hit. No upstream source executes.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TASK_ID = "SP066-ROOT-FILES"
BASELINE = "7c4d954c0619152af7df2baa1072d98fce214786"
PLAN_SHA = ("028ff7abed0cf340d412024e57e74cf7025aaac"
            "ba578012f40c9720e3382ff15")
OUT_ROOT = "data/official-components/2026-11-17/remaining-repository-root-files"
FILE_COUNT, TOTAL_BYTES = 11, 54090
MAX_REQUESTS, MAX_RESPONSE, TIMEOUT = 11, 131072, 30
ALLOWED_REPOS = frozenset({
    "Ezviz-OpenBiz/ezviz-openplatform-sdk", "Ezviz-OpenBiz/OpenCodeSnippets",
    "Ezviz-OpenBiz/EzOtaSDK-Android", "Ezviz-OpenBiz/UniEzOpenSDK",
    "Ezviz-OpenBiz/WeChat-miniApp", "Ezviz-OpenBiz/ertc-web-demo"})
ALLOWED_PATHS = frozenset({"README.md", "LICENSE", "pom.xml", "package.json"})


class PlanError(Exception):
    pass


def _err(reason):
    return PlanError("archive_roots: " + reason)


def _is_sha(v, n):
    return isinstance(v, str) and len(v) == n and \
        all(c in "0123456789abcdef" for c in v)


def _check_plan(plan):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or \
            plan.get("task_id") != TASK_ID or \
            plan.get("baseline_commit") != BASELINE or \
            not _is_sha(plan.get("source_plan_sha256"), 64) or \
            plan.get("output_root") != OUT_ROOT or \
            plan.get("max_requests") != MAX_REQUESTS or \
            plan.get("max_response_bytes") != MAX_RESPONSE or \
            plan.get("expected_total_bytes") != TOTAL_BYTES or \
            plan.get("whole_repository_reviewed") is not False or \
            plan.get("runtime_adoption") is not False or \
            plan.get("integration_verified") is not False:
        raise _err("plan constants mismatch")
    files = plan.get("files")
    if not isinstance(files, list) or len(files) != FILE_COUNT:
        raise _err("file list malformed")
    seen, total = set(), 0
    for f in files:
        if not isinstance(f, dict) or \
                f.get("repository") not in ALLOWED_REPOS or \
                f.get("repository_path") not in ALLOWED_PATHS or \
                not _is_sha(f.get("commit_sha"), 40) or \
                not _is_sha(f.get("tree_sha"), 40) or \
                not _is_sha(f.get("git_blob_sha"), 40) or \
                f.get("git_object_type") != "blob" or \
                f.get("git_mode") != "100644" or \
                f.get("acquisition_status") != "not_performed_by_SP066" or \
                f.get("content_review_status") != "pending" or \
                not isinstance(f.get("material_role"), str) or \
                not isinstance(f.get("expected_bytes"), int) or \
                not 0 <= f["expected_bytes"] <= MAX_RESPONSE:
            raise _err("file entry malformed")
        key = (f["repository"], f["commit_sha"], f["repository_path"])
        if key in seen:
            raise _err("file entry duplicated")
        seen.add(key)
        total += f["expected_bytes"]
    if total != TOTAL_BYTES:
        raise _err("file list malformed")
    return files


def _output_dir(repo):
    """Exclusive create of the fixed output_root under repo; no symlink in any
    parent or the target, and the resolved target must stay inside repo."""
    repo_real = Path(repo).resolve()
    target = repo_real.joinpath(*Path(OUT_ROOT).parts)
    probe = repo_real
    for part in Path(OUT_ROOT).parts[:-1]:
        probe = probe / part
        if probe.is_symlink() or \
                (os.path.lexists(probe) and not probe.is_dir()):
            raise _err("output root unauthorized")
    if os.path.lexists(target) or \
            Path(os.path.realpath(str(target))) != target:
        raise _err("output root unauthorized")
    target.mkdir(parents=True)
    (target / "objects").mkdir()
    return target


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(repo, blob):
    """One bounded HTTPS request; (bytes, None) or (None, fixed status plus an
    optional integer HTTP code). No detail about a failure ever leaves."""
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/git/blobs/%s" % (repo, blob),
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "sp066-archive-roots"})
    try:
        with urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect).open(
                req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None, "http_status", resp.status
            payload = resp.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            exc.close()
        except Exception:
            pass
        return None, "http_status", code
    except Exception:
        return None, "network_error", None
    if len(payload) > MAX_RESPONSE:
        return None, "response_too_large", None
    if not isinstance(payload, bytes):
        return None, "network_error", None
    return payload, None, None


def _decode_verify(entry, payload):
    """Local-only parse, strict decode and hash recomputation; returns
    (raw, byte_sha256) or (None, fixed status)."""
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None, "response_malformed"
    if not isinstance(doc, dict) or doc.get("encoding") != "base64" or \
            doc.get("size") != entry["expected_bytes"] or \
            doc.get("sha") != entry["git_blob_sha"] or \
            not isinstance(doc.get("content"), str):
        return None, "response_mismatch"
    folded = doc["content"].replace("\r", "").replace("\n", "")
    try:
        raw = base64.b64decode(folded, validate=True)
    except (binascii.Error, ValueError):
        return None, "decode_failed"
    if len(raw) != entry["expected_bytes"]:
        return None, "size_mismatch"
    if hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest() \
            != entry["git_blob_sha"]:
        return None, "blob_sha_mismatch"
    return raw, hashlib.sha256(raw).hexdigest()


def _store(objects_dir, byte_sha, raw):
    """Write objects/{sha256}.blob once; an existing object is re-verified by
    size and hash and never overwritten."""
    target = objects_dir / ("%s.blob" % byte_sha)
    if os.path.lexists(target):
        if target.is_symlink() or not target.is_file():
            raise _err("object path unsafe")
        existing = target.read_bytes()
        if len(existing) != len(raw) or \
                hashlib.sha256(existing).hexdigest() != byte_sha:
            raise _err("object collision")
        return "objects/%s.blob" % byte_sha
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
    except BaseException:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise
    return "objects/%s.blob" % byte_sha


def run(repo, plan_path):
    try:
        plan_raw = Path(plan_path).read_bytes()
        plan = json.loads(plan_raw)
    except (OSError, ValueError, UnicodeDecodeError):
        raise _err("plan unreadable")
    if hashlib.sha256(plan_raw).hexdigest() != PLAN_SHA:
        raise _err("plan pin mismatch")
    files = _check_plan(plan)
    objects_dir = _output_dir(repo) / "objects"
    entries = [{"repository": f["repository"], "commit_sha": f["commit_sha"],
                "repository_path": f["repository_path"],
                "git_blob_sha": f["git_blob_sha"],
                "expected_bytes": f["expected_bytes"], "byte_sha256": None,
                "output_relative_object_path": None, "http_status": None,
                "status": "not_attempted"} for f in files]
    for rec, f in zip(entries, files):
        try:
            payload, status, code = _fetch(
                f["repository"], f["git_blob_sha"])
            rec["http_status"] = code
            if status is None:
                raw, status_or_sha = _decode_verify(f, payload)
                if raw is None:
                    status = status_or_sha
                else:
                    try:
                        rec["output_relative_object_path"] = _store(
                            objects_dir, status_or_sha, raw)
                        rec["byte_sha256"], rec["status"] = \
                            status_or_sha, "archived_verified"
                    except (PlanError, OSError):
                        status = "object_store_failed"
            if rec["status"] == "not_attempted":
                rec["status"] = status \
                    if isinstance(status, str) else "internal_error"
        except Exception:
            rec["status"] = "internal_error"
            break
    failures = sum(1 for r in entries if r["status"] != "archived_verified")
    manifest = {"schema_version": 1, "task_id": TASK_ID,
                "baseline_commit": BASELINE, "source_plan_sha256": PLAN_SHA,
                "plan_document_sha256": plan["source_plan_sha256"],
                "output_root": OUT_ROOT, "files": entries, "summary": {
                    "selected_file_count": FILE_COUNT,
                    "archived_verified_count": FILE_COUNT - failures,
                    "failed_count": failures,
                    "requests_made_upper_bound": MAX_REQUESTS,
                    "complete_for_selected_root_files": failures == 0,
                    "unexamined_root_entries_reviewed": False,
                    "repositories_without_root_hits_reviewed": False,
                    "whole_repository_reviewed": False,
                    "license_review_complete": False,
                    "runtime_adoption": False,
                    "integration_verified": False}}
    raw = json.dumps(manifest, sort_keys=True, indent=1).encode() + b"\n"
    try:
        fd = os.open(str(objects_dir.parent / "manifest.json"),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
    except OSError:
        raise _err("manifest store failed")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="archive_roots", description="SP066 root-file archiver.")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.repo is None or args.plan is None:
            raise _err("--repo and --plan are required")
        failures = run(args.repo, args.plan)
        sys.stdout.write("verified: %d failed: %d\n"
                         % (FILE_COUNT - failures, failures))
        return 0 if failures == 0 else 1
    except PlanError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    except Exception:
        sys.stderr.write("archive_roots: unexpected failure\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
