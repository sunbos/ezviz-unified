"""SP068 fixed-manifest supplemental batch exporter (326-source cohort).

Re-exports the frozen supplemental projection into the 43 bounded batch
packets named by the SHA-pinned master manifest under its fixed output_root.
All inputs verify before any write (master, pinned SP065 preflight
plan/result, vocabulary, code modules, documents index, every source pin and
all 18 prior shards); per-source meta/facts/pending come only from reused
SP065._load/_project and must equal the accepted preflight row. Batches stay
<=9 sources/<=4096 nodes; the nine pilot sources must reproduce the accepted
SP054 pilot packet record-for-record. stdout carries counts/fixed reasons
only: no bodies, locators, values or exception chains. No network/env/config
reads; failure leaves prior valid batches plus not_attempted records, exits
non-zero and never claims completeness.
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

from docs.coordination.SP037.project_semantic_review import (  # noqa: E402
    ProjError, _archive_read, _is_sha, _loads, _nodes, _sha256)
from docs.coordination.SP065 import preflight as _pf  # noqa: E402

assert _pf._project and _pf._load and _pf._reconcile_packets  # pinned reuse

TASK_ID = "SP068"
MANIFEST_SHA = ("e5174ee8c0e7c94135d0a9a459e7a6be26674cd"
                "66547b1e876f87ab7306a2427")
PILOT_REL = "data/coordination/SP054-evidence-pilot.json"
PILOT_SHA = ("e2128775ecf5813928151c6102e58a2e123a28d7"
             "3652e1800749148b0165e660")
BATCH_COUNT, MAX_OUT_BYTES = 43, 512 * 1024
BATCH_KEYS = {"batch_id", "source_ids", "observation_ids", "fact_nodes",
              "expected_fact_count", "expected_pending_count"}
NO_CLOSE = {"full_source_review": False, "protocol_freeze": False,
            "goal_complete": False}


def _err(reason):
    return ProjError("export_batches: " + reason)


def _readable(path):
    try:
        return os.path.isfile(path)
    except (OSError, TypeError, ValueError):
        return False


def _str_list(v, lo, hi):
    return isinstance(v, list) and lo <= len(v) <= hi and \
        all(isinstance(x, str) for x in v) and len(set(v)) == len(v)


def _check_master(m):
    const = {"schema_version": 1, "task_id": TASK_ID,
             "baseline_commit": _pf.BASELINE,
             "sources": _pf.EXPECTED_SOURCES,
             "observations": _pf.EXPECTED_OBS,
             "max_sources_per_batch": _pf.MAX_SOURCES,
             "max_fact_nodes_per_batch": _pf.MAX_NODES}
    flags = {"prior_evidence_is_immutable": True,
             "full_scope_semantics_complete": False, "goal_complete": False}
    if not isinstance(m, dict) or \
            any(m.get(k) != v for k, v in const.items()) or \
            any(m.get(k) is not v for k, v in flags.items()):
        raise _err("master constants mismatch")
    out = m.get("output_root")
    if not isinstance(out, str) or os.path.isabs(out) or \
            any(p in ("", ".", "..") for p in out.split("/")):
        raise _err("output root malformed")
    pins = m.get("module_pins")
    if not isinstance(pins, dict) or len(pins) != 3 or \
            any(not isinstance(k, str) or os.path.isabs(k) or
                not _is_sha(v) for k, v in pins.items()):
        raise _err("module pins malformed")
    batches = m.get("batches")
    if not isinstance(batches, list) or len(batches) != BATCH_COUNT:
        raise _err("batch list malformed")
    for b in batches:
        bid = b.get("batch_id") if isinstance(b, dict) else None
        if not isinstance(bid, str) or set(b) != BATCH_KEYS or \
                bid in ("", ".", "..") or "/" in bid or os.sep in bid or \
                not _str_list(b["source_ids"], 1, _pf.MAX_SOURCES) or \
                not _str_list(b["observation_ids"], 0, _pf.EXPECTED_OBS) or \
                not isinstance(b["fact_nodes"], int) or \
                not isinstance(b["expected_fact_count"], int) or \
                not isinstance(b["expected_pending_count"], int):
            raise _err("batch entry malformed")
    if len({b["batch_id"] for b in batches}) != BATCH_COUNT:
        raise _err("batch list malformed")
    return out, pins, batches


def _output_dir(repo, rel):
    """Exclusive create of fixed output_root under repo; no symlink parent or
    target escape; resolved target must stay inside repo."""
    repo_real = Path(repo).resolve()
    target = repo_real.joinpath(*Path(rel).parts)
    probe = repo_real
    for part in Path(rel).parts[:-1]:
        probe = probe / part
        if probe.is_symlink() or \
                (os.path.lexists(probe) and not probe.is_dir()):
            raise _err("output root unauthorized")
    if os.path.lexists(target) or \
            Path(os.path.realpath(str(target))) != target:
        raise _err("output root unauthorized")
    target.mkdir(parents=True)
    return target


def _exclusive(path, raw):
    """Write once via temp+hard link: os.link fails if the target exists."""
    fd, tmp = tempfile.mkstemp(prefix=".sp068-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.link(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.unlink(tmp)


def _obs_index(repo, packet_pins):
    """Pin-verified prior packets -> {source_id: [observation_id]}; runs only
    after _reconcile_packets globally validated identity/uniqueness."""
    repo_root = Path(repo).resolve()
    obs = {}
    for relpath, sha in packet_pins.items():
        raw, _ = _archive_read(repo_root, relpath, sha)
        packet = _loads(raw, "prior packet is not valid JSON")
        for rec in packet.get("observation_facts") or []:
            obs.setdefault(rec["source_id"], []).append(rec["observation_id"])
    return obs


def _canon(records):
    """Canonical record multiset (sorted per-record strict JSON)."""
    return sorted(json.dumps(r, ensure_ascii=False, sort_keys=True)
                  for r in records)


def run(repo, plan_path):
    raw = Path(plan_path).read_bytes() if _readable(plan_path) else None
    if raw is None or _sha256(raw) != MANIFEST_SHA:
        raise _err("master pin mismatch")
    master = _loads(raw, "master is not valid JSON")
    out_rel, module_pins, batches = _check_master(master)
    for rel, sha in module_pins.items():
        _pf._code_pin(rel, sha)
    plan = _pf._pinned_file(master.get("preflight_plan"),
                            "preflight plan pin malformed")
    preflight = _pf._pinned_file(master.get("preflight_result"),
                                 "preflight result pin malformed")
    root, sources, pilot_ids, _exp, packet_pins = _pf._check_plan(plan)
    vocab = _pf._pinned_file(plan.get("reviewed_vocabulary"),
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
    _pf._verify_source_pins(sources, doc_index)
    _pf._reconcile_packets(repo, packet_pins, sources)
    obs_by_source = _obs_index(repo, packet_pins)
    by_id = {s["source_id"]: s for s in sources}
    rows = preflight.get("source_budgets")
    if not isinstance(rows, list) or len(rows) != _pf.EXPECTED_SOURCES or \
            any(not isinstance(r, dict) or
                not isinstance(r.get("source_id"), str) for r in rows):
        raise _err("preflight result malformed")
    budgets = {r["source_id"]: r for r in rows}
    if len(budgets) != _pf.EXPECTED_SOURCES or set(budgets) != set(by_id):
        raise _err("preflight result source set mismatch")
    # Declared batches must exactly cover 326 sources; each batch's declared
    # observations must equal its own sources' real observations.
    covered, covered_obs = set(), set()
    for b in batches:
        actual = set()
        for sid in b["source_ids"]:
            if sid not in by_id or sid in covered:
                raise _err("batch source coverage invalid")
            covered.add(sid)
            actual.update(obs_by_source.get(sid, []))
        if set(b["observation_ids"]) != actual:
            raise _err("batch observation coverage invalid")
        covered_obs.update(b["observation_ids"])
    if covered != set(by_id) or len(covered_obs) != _pf.EXPECTED_OBS:
        raise _err("batch coverage conservation failed")
    pfile = Path(repo).resolve().joinpath(PILOT_REL)
    pilot_raw = pfile.read_bytes() if _readable(pfile) else None
    if pilot_raw is None or _sha256(pilot_raw) != PILOT_SHA:
        raise _err("pilot pin mismatch")
    pilot = _loads(pilot_raw, "pilot is not valid JSON")
    out_dir = _output_dir(repo, out_rel)
    input_pins = {
        "master_sha256": MANIFEST_SHA,
        "preflight_plan_sha256": master["preflight_plan"]["sha256"],
        "preflight_result_sha256": master["preflight_result"]["sha256"],
        "archive_documents_sha256": plan["archive_documents_sha256"],
        "reviewed_vocabulary_sha256": plan["reviewed_vocabulary"]["sha256"],
        "module_pins": dict(module_pins),
        "prior_packet_pins": dict(packet_pins),
        "pilot_evidence_sha256": PILOT_SHA}
    entries = [{"batch_id": b["batch_id"], "status": "not_attempted"}
               for b in batches]
    pilot_sources, pilot_facts, pilot_pend = [], [], []
    totals = {"sources": 0, "observations": 0, "facts": 0, "pending": 0}
    for rec, b in zip(entries, batches):
        try:
            b_sources, b_facts, b_pend, nodes = [], [], [], 0
            for sid in b["source_ids"]:
                meta, html, structure = _pf._load(root, by_id[sid])
                facts, pend = _pf._project(by_id[sid], html, structure, fields)
                row = budgets[sid]
                cost = _nodes(meta) + sum(_nodes(f) for f in facts) + \
                    sum(_nodes(p) for p in pend)
                if row.get("node_count") != cost or \
                        row.get("fact_count") != len(facts) or \
                        row.get("pending_count") != len(pend) or \
                        row.get("status") != "fits_budget" or \
                        row.get("label_context_count") != sum(1 for f in facts if f.get("kind") == "label_context") or \
                        row.get("field_table_count") != sum(1 for f in facts if f.get("kind") == "field_table"):
                    raise _err("source projection mismatch")
                nodes += cost
                b_sources.append(meta)
                b_facts.extend(facts)
                b_pend.extend(pend)
                if sid in pilot_ids:
                    pilot_sources.append(meta)
                    pilot_facts.extend(facts)
                    pilot_pend.extend(pend)
            if nodes > _pf.MAX_NODES or nodes != b["fact_nodes"] or \
                    len(b_facts) != b["expected_fact_count"] or \
                    len(b_pend) != b["expected_pending_count"]:
                raise _err("batch budget or count mismatch")
            receipt = dict(NO_CLOSE, source_count=len(b_sources),
                           observation_count=len(b["observation_ids"]),
                           fact_count=len(b_facts),
                           pending_count=len(b_pend), node_count=nodes)
            packet = {"schema_version": 1, "batch_id": b["batch_id"],
                      "input_pins": input_pins, "sources": b_sources,
                      "facts": b_facts, "pending": b_pend,
                      "observation_ids": sorted(set(b["observation_ids"])),
                      "receipt": receipt}
            out = json.dumps(packet, ensure_ascii=False, sort_keys=True,
                             indent=1).encode("utf-8") + b"\n"
            if len(out) > MAX_OUT_BYTES:
                raise _err("output budget exceeded")
            name = b["batch_id"] + ".json"
            _exclusive(out_dir / name, out)
            rec.update({"status": "exported", "file": name,
                        "sha256": _sha256(out),
                        "source_count": receipt["source_count"],
                        "observation_count": receipt["observation_count"],
                        "fact_count": len(b_facts),
                        "pending_count": len(b_pend), "node_count": nodes})
            for k, v in (("sources", len(b_sources)),
                         ("observations", len(b["observation_ids"])),
                         ("facts", len(b_facts)),
                         ("pending", len(b_pend))):
                totals[k] += v
        except ProjError as exc:
            rec.update({"status": "failed",
                        "reason": str(exc).split(": ", 1)[-1]})
        except Exception:
            rec.update({"status": "failed", "reason": "batch export failed"})
        if rec["status"] != "exported":
            break
    exported = sum(1 for r in entries if r["status"] == "exported")
    conserved = exported == BATCH_COUNT and \
        totals["sources"] == _pf.EXPECTED_SOURCES and \
        totals["observations"] == _pf.EXPECTED_OBS
    pilot_ok = conserved and \
        pilot_ids == {s["source_id"] for s in pilot_sources} and \
        _canon(pilot_sources) == _canon(pilot.get("sources") or []) and \
        _canon(pilot_facts) == _canon(pilot.get("facts") or []) and \
        _canon(pilot_pend) == _canon(pilot.get("pending") or [])
    complete = conserved and pilot_ok
    count = lambda st: sum(1 for r in entries if r["status"] == st)
    manifest = {"schema_version": 1, "task_id": TASK_ID,
                "master_sha256": MANIFEST_SHA, "output_root": out_rel,
                "batches": entries,
                "summary": {"batch_count": BATCH_COUNT,
                            "exported_count": exported,
                            "failed_count": count("failed"),
                            "not_attempted_count": count("not_attempted"),
                            "sources_exported": totals["sources"],
                            "observations_exported": totals["observations"],
                            "pilot_equivalence": bool(pilot_ok),
                            "complete_for_selected_projection": complete,
                            "semantic_complete": False, "sdk_complete": False,
                            **NO_CLOSE}}
    out = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                     indent=1).encode("utf-8") + b"\n"
    try:
        _exclusive(out_dir / "manifest.json", out)
    except Exception:
        raise _err("manifest store failed")
    return complete, exported


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="export_batches",
        description="SP068 fixed-manifest supplemental batch exporter.")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args(argv)
    try:
        for flag in ("repo", "plan"):
            if getattr(args, flag) is None:
                raise _err("--%s is required" % flag)
        complete, exported = run(args.repo, args.plan)
        sys.stdout.write("batches exported: %d/%d complete: %s\n"
                         % (exported, BATCH_COUNT,
                            "true" if complete else "false"))
        return 0 if complete else 2
    except ProjError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    except Exception:
        sys.stderr.write("export_batches: unexpected failure\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
