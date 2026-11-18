"""SP065 read-only per-source budget preflight (326-source first batch).

Computes the projected node cost each frozen source would occupy inside a
future bounded batch packet, reusing the exact SP054 projection (_facts on
BeautifulSoup(html, "html.parser") plus the fixed pending entries) and SP037
node counter. Emits only source_id/status/counts: never fact values, raw
bodies, parameter values, URLs, unknown field names or exception chains.
Every pin/set mismatch rejects globally without writing any report; a pure
projection failure is recorded as fixed status projection_error with null
counts and the run continues. Successful output only means the preflight
finished; it marks nothing reviewed, frozen or complete and performs no
batch splitting. No network, no env/config reads, no upstream code runs.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from docs.coordination.SP037.project_semantic_review import (  # noqa: E402
    ProjError, _archive_read, _is_sha, _loads, _nodes, _sha256)
from docs.coordination.SP054.supplement_evidence import _facts  # noqa: E402

assert _facts  # pinned reuse; never reimplemented here

TASK_ID = "SP065"
PLAN_SHA = ("a2ddc4dfceca53f9aad425f1bb75a3e0"
            "ac367f9200f919cb5651cb5a1612b554")
OUT_REL = "data/coordination/SP065-preflight.json"
BASELINE = "7c4d954c0619152af7df2baa1072d98fce214786"
SUPPLEMENT_SHA = ("e44ca8028e9acd4e87484ed8f705fd"
                  "2159603b2f7f9da4ab94d243d1a140b202")
PROJECTOR_SHA = ("0d3795d2382978e870ab5624c744842"
                 "9449f81d0c012f55f66700867046b484d")
EXPECTED_SOURCES, EXPECTED_OBS = 326, 392
MAX_SOURCES, MAX_NODES, MAX_OUT_BYTES = 9, 4096, 256 * 1024
PACKET_COUNT = 18
SOURCE_KEYS = ("source_id", "region", "doc_id", "raw_file",
               "raw_file_sha256", "content_sha256", "structure_file",
               "structure_sha256")
STATUS = {"fit": "fits_budget", "over": "oversized", "err": "projection_error"}


def _err(reason):
    return ProjError("preflight: " + reason)


def _readable(path):
    try:
        return os.path.isfile(path)
    except (OSError, TypeError, ValueError):
        return False


def _pinned_file(ref, reason):
    """Read a {path, sha256} pin exactly; errors never echo paths/values."""
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or \
            not _is_sha(ref.get("sha256")):
        raise _err(reason)
    raw = Path(ref["path"]).read_bytes() if _readable(ref["path"]) else None
    if raw is None or _sha256(raw) != ref["sha256"]:
        raise _err(reason)
    return _loads(raw, reason)


def _code_pin(relpath, expected):
    """Verify one frozen module pin by fixed repo-relative path (no abs echo)."""
    if not isinstance(relpath, str) or os.path.isabs(relpath):
        raise _err("code pin unavailable")
    raw = Path(_ROOT / relpath).read_bytes() \
        if _readable(_ROOT / relpath) else None
    if raw is None or _sha256(raw) != expected:
        raise _err("code pin unavailable")


def _check_plan(plan):
    if plan.get("schema_version") != 1 or plan.get("task_id") != TASK_ID:
        raise _err("plan identity mismatch")
    if plan.get("baseline_commit") != BASELINE or \
            plan.get("output_relative_path") != OUT_REL or \
            plan.get("max_sources_per_future_batch") != MAX_SOURCES or \
            plan.get("max_nodes_per_future_batch") != MAX_NODES or \
            plan.get("expected_source_count") != EXPECTED_SOURCES or \
            plan.get("expected_observation_count") != EXPECTED_OBS or \
            plan.get("whole_goal_complete") is not False:
        raise _err("plan constants mismatch")
    root = plan.get("archive_root")
    if not isinstance(root, str) or not os.path.isabs(root):
        raise _err("archive root malformed")
    root = Path(root).resolve()
    if not _is_sha(plan.get("archive_documents_sha256")):
        raise _err("documents pin malformed")
    if plan.get("supplement_module_sha256") != SUPPLEMENT_SHA or \
            plan.get("projector_module_sha256") != PROJECTOR_SHA:
        raise _err("module pin mismatch")
    packets = plan.get("prior_packet_pins")
    # Frozen shape: repo-relative shard path -> SHA256, exactly PACKET_COUNT
    # entries. Each path must be a clean relative path that stays inside the
    # repo root; values must be SHA256 pins. Never collapse to a bare list.
    if not isinstance(packets, dict) or len(packets) != PACKET_COUNT:
        raise _err("prior packet pins malformed")
    for relpath, sha in packets.items():
        if not isinstance(relpath, str) or os.path.isabs(relpath) or \
                any(p in ("", ".", "..") for p in relpath.split("/")) or \
                not _is_sha(sha):
            raise _err("prior packet pins malformed")
    sources = plan.get("sources")
    if not isinstance(sources, list) or len(sources) != EXPECTED_SOURCES:
        raise _err("source list malformed")
    ids = [s.get("source_id") if isinstance(s, dict) else None
           for s in sources]
    if len(set(ids)) != EXPECTED_SOURCES or \
            any(not isinstance(i, str) for i in ids):
        raise _err("source list malformed")
    pilot_ids = plan.get("pilot_source_ids")
    if not isinstance(pilot_ids, list) or len(pilot_ids) != 9 or \
            not all(isinstance(i, str) for i in pilot_ids) or \
            len(set(pilot_ids)) != 9 or not set(pilot_ids) <= set(ids):
        raise _err("pilot source set mismatch")
    counts = plan.get("pilot_expected_counts")
    if counts != {"sources": 9, "facts": 78, "pending": 134, "nodes": 2320}:
        raise _err("pilot expected counts mismatch")
    return root, sources, set(pilot_ids), counts, packets


def _verify_source_pins(sources, doc_index):
    """Every frozen source must agree with the documents index exactly."""
    for source in sources:
        for key in SOURCE_KEYS:
            if key == "doc_id":
                if source.get("doc_id") is None:
                    raise _err("source pin malformed")
            elif not isinstance(source.get(key), str):
                raise _err("source pin malformed")
        for key in ("raw_file_sha256", "content_sha256", "structure_sha256"):
            if not _is_sha(source.get(key)):
                raise _err("source pin malformed")
        doc = doc_index.get((source.get("region"), str(source.get("doc_id"))))
        if not isinstance(doc, dict) or \
                doc.get("raw_file") != source["raw_file"] or \
                doc.get("raw_file_sha256") != source["raw_file_sha256"] or \
                doc.get("content_sha256") != source["content_sha256"] or \
                doc.get("structure_file") != source["structure_file"] or \
                doc.get("structure_sha256") != source["structure_sha256"]:
            raise _err("documents index mismatch")


def _reconcile_packets(repo, packet_pins, sources):
    """Read the 18 frozen prior shard packets (repo-relative, SHA-pinned) via
    the existing safe reader and reconcile their source_facts/observation_facts
    against the frozen plan. Any pin/set/identity mismatch rejects globally
    and no report is written. Only fixed pins/counts survive; raw facts are
    never copied out."""
    repo_root = Path(repo).resolve()
    plan_by_id = {}
    for source in sources:
        plan_by_id[source["source_id"]] = source
    seen_sources, seen_obs, obs_total = set(), set(), 0
    for relpath, sha in packet_pins.items():
        raw, _ = _archive_read(repo_root, relpath, sha)
        packet = _loads(raw, "prior packet is not valid JSON")
        if not isinstance(packet, dict):
            raise _err("prior packet malformed")
        source_facts = packet.get("source_facts")
        if not isinstance(source_facts, list):
            raise _err("prior packet malformed")
        for rec in source_facts:
            if not isinstance(rec, dict):
                raise _err("prior packet source malformed")
            sid = rec.get("source_id")
            if not isinstance(sid, str) or sid in seen_sources:
                raise _err("prior packet source identity invalid")
            plan_src = plan_by_id.get(sid)
            if plan_src is None or \
                    rec.get("region") != plan_src.get("region") or \
                    str(rec.get("doc_id")) != str(plan_src.get("doc_id")) or \
                    rec.get("content_sha256") != \
                    plan_src.get("content_sha256") or \
                    rec.get("structure_sha256") != \
                    plan_src.get("structure_sha256"):
                raise _err("prior packet source pin mismatch")
            seen_sources.add(sid)
        obs_facts = packet.get("observation_facts")
        if not isinstance(obs_facts, list):
            raise _err("prior packet malformed")
        for obs in obs_facts:
            if not isinstance(obs, dict):
                raise _err("prior packet observation malformed")
            oid, sid = obs.get("observation_id"), obs.get("source_id")
            if not isinstance(oid, str) or oid in seen_obs:
                raise _err("prior packet observation identity invalid")
            if not isinstance(sid, str) or sid not in plan_by_id:
                raise _err("prior packet observation source invalid")
            seen_obs.add(oid)
            obs_total += 1
    if seen_sources != set(plan_by_id) or obs_total != EXPECTED_OBS or \
            len(seen_obs) != EXPECTED_OBS:
        raise _err("prior packet conservation mismatch")


def _load(root, source):
    """Pin-verified load of one source's archive files; every failure here is
    a pin/integrity mismatch and rejects the whole run (no report)."""
    raw, _ = _archive_read(root, source["raw_file"], source["raw_file_sha256"])
    wrapper = _loads(raw, "raw source is not valid JSON")
    html = wrapper.get("html_content") if isinstance(wrapper, dict) else None
    if not isinstance(html, str) or \
            _sha256(html.encode("utf-8")) != source["content_sha256"]:
        raise _err("content pin mismatch")
    sraw, _ = _archive_read(root, source["structure_file"],
                            source["structure_sha256"])
    structure = _loads(sraw, "structure is not valid JSON")
    if not isinstance(structure, dict):
        raise _err("structure malformed")
    meta = {k: source[k] for k in SOURCE_KEYS}
    return meta, html, structure


def _project(source, html, structure, fields):
    """Pure projection, identical to SP054.run's per-source body."""
    facts, pend = _facts(BeautifulSoup(html, "html.parser"), structure, fields)
    pend.append({"source_id": source["source_id"],
                 "reason": "prose_not_projected"})
    for anchor in [b.get("element_index") for b in
                   (structure.get("code_blocks") or [])
                   if isinstance(b, dict)]:
        pend.append({"source_id": source["source_id"],
                     "element_index": anchor if isinstance(anchor, int)
                     else None,
                     "reason": "code_content_not_projected"})
    for fact in facts:
        fact["source_id"] = source["source_id"]
        fact["fact_id"] = "%s:%s:%s" % (
            source["source_id"], fact["kind"], fact["fact_id"])
    for item in pend:
        item.setdefault("source_id", source["source_id"])
    return facts, pend


def _output_target(repo, out_arg):
    """Resolve the prescribed fixed relative report under repo. The caller's
    --out must match it lexically; every existing ancestor of the target must
    be a real directory (no symlink parent/target escape); the resolved target
    must stay inside the resolved repo and must not already exist."""
    repo_real = Path(repo).resolve()
    target = repo_real.joinpath(*Path(OUT_REL).parts)
    cand = Path(out_arg)
    if cand.is_absolute():
        if Path(os.path.normpath(str(cand))) != target:
            raise _err("output path unauthorized or already exists")
    elif os.path.normpath(str(cand)) != OUT_REL:
        raise _err("output path unauthorized or already exists")
    probe = repo_real
    for part in Path(OUT_REL).parts[:-1]:
        probe = probe / part
        if probe.is_symlink() or \
                (os.path.lexists(probe) and not probe.is_dir()):
            raise _err("output path unauthorized or already exists")
    if Path(os.path.realpath(str(target))) != target or \
            os.path.lexists(target):
        raise _err("output path unauthorized or already exists")
    return target


def run(repo, plan_path, out_arg):
    plan_raw = Path(plan_path).read_bytes() if _readable(plan_path) else None
    if plan_raw is None or _sha256(plan_raw) != PLAN_SHA:
        raise _err("plan pin mismatch")
    plan = _loads(plan_raw, "plan is not valid JSON")
    root, sources, pilot_ids, expected, packet_pins = _check_plan(plan)
    vocab = _pinned_file(plan.get("reviewed_vocabulary"),
                         "vocabulary pin malformed")
    fields = {n for n in (vocab.get("field_names") or [])
              if isinstance(n, str)}
    docs_raw, _ = _archive_read(root, "documents.jsonl",
                                plan["archive_documents_sha256"])
    doc_index = {}
    for line in docs_raw.split(b"\n"):
        if not line.strip():
            continue
        doc = _loads(line, "documents record is not valid JSON")
        key = (doc.get("region"), str(doc.get("doc_id")))
        if key in doc_index:
            raise _err("documents record duplicated")
        doc_index[key] = doc
    _verify_source_pins(sources, doc_index)
    _code_pin("docs/coordination/SP054/supplement_evidence.py", SUPPLEMENT_SHA)
    _code_pin("docs/coordination/SP037/project_semantic_review.py",
              PROJECTOR_SHA)
    # Reconcile the 18 frozen prior shard packets against the plan before any
    # source body is read: source set must equal the frozen 326, each source's
    # region/doc_id/content/structure pins must match, observation_ids unique
    # and exactly EXPECTED_OBS with valid parent source_ids. Any mismatch
    # rejects globally and no report is written.
    _reconcile_packets(repo, packet_pins, sources)
    # Phase 1: pin-verified load of every frozen source. Any failure here is a
    # global integrity mismatch; it rejects the run and no report is written.
    seen_files, loaded = set(), []
    for source in sources:
        for rel in (source["raw_file"], source["structure_file"]):
            if rel in seen_files:
                raise _err("archive file duplicated")
            seen_files.add(rel)
        loaded.append((source,) + _load(root, source))
    # Phase 2: pure per-source projection. A failure after all pins verified is
    # recorded as the fixed projection_error status with null counts; it is
    # never hidden, never counted as projected, and never echoes its reason.
    budgets, counts = [], {v: 0 for v in STATUS.values()}
    pilot = {"sources": 0, "facts": 0, "pending": 0, "nodes": 0}
    for source, meta, html, structure in loaded:
        sid = source["source_id"]
        try:
            facts, pend = _project(source, html, structure, fields)
            nodes = _nodes(meta) + sum(_nodes(f) for f in facts) + \
                sum(_nodes(p) for p in pend)
            label_ctx = sum(1 for f in facts
                            if f.get("kind") == "label_context")
            field_tab = sum(1 for f in facts
                            if f.get("kind") == "field_table")
            status = STATUS["fit"] if nodes <= MAX_NODES else STATUS["over"]
            budgets.append({"source_id": sid, "status": status,
                            "node_count": nodes, "fact_count": len(facts),
                            "pending_count": len(pend),
                            "label_context_count": label_ctx,
                            "field_table_count": field_tab})
            if sid in pilot_ids:
                pilot["sources"] += 1
                pilot["facts"] += len(facts)
                pilot["pending"] += len(pend)
                pilot["nodes"] += nodes
        except Exception:
            status = STATUS["err"]
            budgets.append({"source_id": sid, "status": status,
                            "node_count": None, "fact_count": None,
                            "pending_count": None,
                            "label_context_count": None,
                            "field_table_count": None})
        counts[status] += 1
    if len(budgets) != EXPECTED_SOURCES or \
            sum(counts.values()) != EXPECTED_SOURCES:
        raise _err("conservation failed")
    if pilot != expected:
        raise _err("pilot aggregate mismatch")
    report = {
        "schema_version": 1,
        "task_id": TASK_ID,
        "input_pins": {
            "plan_sha256": PLAN_SHA,
            "archive_documents_sha256": plan["archive_documents_sha256"],
            "reviewed_vocabulary_sha256":
                plan["reviewed_vocabulary"]["sha256"],
            "supplement_module_sha256": SUPPLEMENT_SHA,
            "projector_module_sha256": PROJECTOR_SHA,
            "prior_packet_pins": plan["prior_packet_pins"]},
        "source_budgets": budgets,
        "summary": {
            "source_count": EXPECTED_SOURCES,
            "fits_budget_count": counts[STATUS["fit"]],
            "oversized_count": counts[STATUS["over"]],
            "projection_error_count": counts[STATUS["err"]],
            "budget_nodes_per_future_batch": MAX_NODES,
            "max_sources_per_future_batch": MAX_SOURCES,
            "pilot_aggregate": pilot,
            "full_source_review": False,
            "protocol_freeze": False,
            "goal_complete": False}}
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True,
                     indent=1).encode("utf-8") + b"\n"
    if len(raw) > MAX_OUT_BYTES:
        raise _err("output budget exceeded")
    target = _output_target(repo, out_arg)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sp065-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        # Exclusive create: os.link fails if the target already exists, so the
        # report is written once and never overwritten.
        os.link(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.unlink(tmp)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="preflight",
        description="SP065 read-only per-source budget preflight.")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        for flag in ("repo", "plan", "out"):
            if getattr(args, flag) is None:
                raise _err("--%s is required" % flag)
        counts = run(args.repo, args.plan, args.out)
        sys.stdout.write(
            "sources: %d %s: %d %s: %d %s: %d\n" % (
                sum(counts.values()), STATUS["fit"], counts[STATUS["fit"]],
                STATUS["over"], counts[STATUS["over"]],
                STATUS["err"], counts[STATUS["err"]]))
        return 0
    except ProjError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    except Exception:
        sys.stderr.write("preflight: unexpected failure\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
