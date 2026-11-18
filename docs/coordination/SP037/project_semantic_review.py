"""One-shot SP037 whitelist projection of semantic-review packet inputs.

Reads the SHA-pinned plan + its seven pinned inputs, filters the real full
ledger (4051 sources / 3019 observations) to the 326 routing-selected sources,
verifies the six pinned archive files plus each documents.jsonl record's own
structure_sha256, and emits bounded shard packets (one JSON object each, <=32
sources, <=4096 fact nodes) under --repo/.../SP037-semantic-review-packets.
No SDK, network, env reads, raw bodies or re-derivation. --self-check uses
synthetic temp inputs only; it is not the real projection acceptance.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

OUT_REL = "data/coordination/SP037-semantic-review-packets"
PLAN_SHA = "5d6ab799d905e90d188f82645c309a9e761d7a8b3ba6ae1bbdabab2af36f3f99"
REFS = ("routing", "source_inputs", "reviewed_vocabulary", "ledger",
        "registry", "voice_review", "acceptance_criteria")
EXPECTED_LANES = {"A": 128, "B": 45, "C": 153}
EXPECTED_SOURCES, EXPECTED_OBS = 326, 392
EXPECTED_LEDGER = (4051, 3019)          # full library: sources, observations
EXPECTED_FIELDS, EXPECTED_ROUTES, EXPECTED_VOICE_OBS = 140, 204, 10
EXPECTED_VOICE = {"portal-2026-11-17:domestic:%d" % d
                  for d in (1259, 1263, 1273, 1277, 1281)}
MAX_SOURCES, MAX_NODES, MIN_SHARDS = 32, 4096, 11
EXAMPLE_BYTES, SHAPE_DEPTH, SHAPE_NODES = 65536, 48, 512
METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
TYPE_BY_LOWER = {t.lower(): t for t in (
    "String Int Integer Long Boolean Bool Object Array Number Float Double "
    "File Map Void").split()}
REQUIRED = {"y": True, "yes": True, "true": True, "required": True,
            "1": True, "是": True, "必选": True, "必填": True,
            "n": False, "no": False, "false": False, "optional": False,
            "0": False, "否": False, "可选": False}
LOCATIONS = {"query", "path", "body", "header", "form", "cookie"}
HEADERS = {"name": {"name", "参数名", "参数名称", "字段名", "field", "参数"},
           "type": {"type", "类型", "数据类型"},
           "required": {"required", "是否必选", "必选", "必填", "是否必填"},
           "location": {"location", "参数位置", "位置"}}
SOURCE_KEYS = {"source_id", "snapshot_id", "record_index", "kind", "region",
               "doc_id", "canonical_document_url", "status", "content_sha256",
               "category_paths", "disposition", "operation_ids",
               "observation_ordinals", "evidence_refs", "unresolved"}
OBS_KEYS = {"observation_id", "source_id", "kind", "ordinal", "status",
            "method", "section_index", "element_indices", "has_query",
            "has_fragment", "reason_codes", "reason_count", "same_route_hints",
            "related_declaration_ordinals", "agrees_with_declaration",
            "exact_operation_ids", "inventory_content_sha256"}
RULES = {"type": (lambda v: v.lower() in TYPE_BY_LOWER,
                  lambda v: TYPE_BY_LOWER.get(v.lower()), "unreviewed_type"),
         "required": (lambda v: v.lower() in REQUIRED,
                      lambda v: REQUIRED.get(v.lower()),
                      "unreviewed_required"),
         "location": (lambda v: v.lower() in LOCATIONS, lambda v: v.lower(),
                      "unreviewed_location")}


class ProjError(ValueError):
    """Fixed failure; never echoes raw values, paths or exception chains."""


def _err(reason):
    return ProjError("project_semantic_review: " + reason)


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _is_sha(v):
    return isinstance(v, str) and len(v) == 64 and \
        all(c in "0123456789abcdef" for c in v)


def _read(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        raise _err("input cannot be read") from None


def _loads(raw, reason):
    """Strict JSON; pinned inputs carry no node budget (already pin-bounded)."""
    def no_dupes(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ProjError("dup")
            out[key] = value
        return out

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=no_dupes,
                          parse_constant=lambda v: (_ for _ in ()).throw(
                              ProjError("nonfinite")))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _err(reason) from None


def _nodes(node):
    if isinstance(node, dict):
        return 1 + sum(_nodes(v) for v in node.values())
    return 1 + sum(_nodes(v) for v in node) if isinstance(node, list) else 1


def _whitelist(record, allowed):
    if not isinstance(record, dict):
        raise _err("ledger record malformed")
    out = {k: record[k] for k in sorted(allowed) if k in record}
    if len(record) > len(out):
        out["unprojected_key_count"] = len(record) - len(out)
    return out


def _archive_read(root, relpath, expected_sha):
    """Read one file inside source_archive_root; errors never echo paths."""
    if not isinstance(relpath, str) or os.path.isabs(relpath) or \
            any(p in ("", ".", "..") for p in relpath.split("/")):
        raise _err("archive relative path unsafe")
    if not _is_sha(expected_sha):
        raise _err("archive pin missing")
    target = (root / relpath).resolve()
    if not str(target).startswith(str(root) + os.sep):
        raise _err("archive path escapes root")
    raw = _read(target)
    if _sha256(raw) != expected_sha:
        raise _err("archive pin mismatch")
    return raw, target


def _shape(node, fields, st, depth=0):
    """Value-free type topology; truncation sets st['partial'], keeps content.
    Keys outside the reviewed vocabulary are never echoed; st['withheld']
    counts them (nested objects/arrays included) for anchored pending."""
    st["n"] += 1
    if depth > SHAPE_DEPTH or st["n"] > SHAPE_NODES:
        st["partial"] = True
        return {"type": "omitted"}
    if node is None or isinstance(node, bool):
        out = {"type": "null" if node is None else "boolean"}
    elif isinstance(node, (int, float)):
        out = {"type": "number"}
    elif isinstance(node, str):
        out = {"type": "string"}
    elif isinstance(node, list):
        seen = {}
        for item in node:
            s = _shape(item, fields, st, depth + 1)
            seen[json.dumps(s, sort_keys=True)] = s
        out = {"type": "array", "items": [seen[k] for k in sorted(seen)]}
    elif isinstance(node, dict):
        projected = {k: _shape(node[k], fields, st, depth + 1)
                     for k in sorted(node) if k in fields}
        out = {"type": "object", "fields": projected}
        if len(node) > len(projected):
            withheld = len(node) - len(projected)
            out["unprojected_key_count"] = withheld
            st["withheld"] = st.get("withheld", 0) + withheld
    else:
        return {"type": "unknown"}
    if st["partial"]:
        out["partial"] = True
    return out


def _table(table, fields, pend):
    """Whitelist one field table; every gap yields an anchored pending item."""
    element, rows = table.get("element_index"), table.get("rows")

    def gap(reason, row=None, column=None):
        item = {"element_index": element if isinstance(element, int) else None,
                "reason": reason}
        if row is not None:
            item["row"] = row
        if column is not None:
            item["column"] = column
        pend.append(item)

    if not isinstance(rows, list) or not rows or \
            not all(isinstance(r, list) for r in rows):
        gap("table_layout_unsupported")
        return None
    cols = {}
    for ci, cell in enumerate(rows[0]):
        if not isinstance(cell, dict) or cell.get("kind") != "th" or \
                cell.get("rowspan", 1) != 1 or cell.get("colspan", 1) != 1 \
                or not isinstance(cell.get("text"), str):
            gap("table_layout_unsupported", 0, ci)
            return None
        for slot, aliases in HEADERS.items():
            if cell["text"].strip().lower() in aliases and slot not in cols:
                cols[slot] = ci
    if "name" not in cols:
        gap("table_layout_unsupported")
        return None
    out_rows = []
    for ri, row in enumerate(rows[1:], 1):
        rec = {"row": ri, "name": None, "type": None,
               "required": None, "location": None}
        for slot, ci in cols.items():
            cell = row[ci] if ci < len(row) else None
            if not isinstance(cell, dict) or cell.get("kind") != "td" or \
                    cell.get("rowspan", 1) != 1 or \
                    cell.get("colspan", 1) != 1 or \
                    not isinstance(cell.get("text"), str):
                gap("table_cell_merged_or_missing", ri, ci)
                continue
            value = cell["text"].strip()
            if not value:
                continue
            if slot == "name":
                # Field names pass only via the reviewed vocabulary itself;
                # anything else is unreviewed and never echoed.
                if value in fields:
                    rec["name"] = value
                else:
                    gap("unreviewed_field_name", ri, ci)
                continue
            ok_fn, map_fn, why = RULES[slot]
            if ok_fn(value):
                rec[slot] = map_fn(value)
            else:
                gap(why, ri, ci)
        out_rows.append(rec)
    return {"kind": "field_table", "role": "unknown",
            "element_index": element if isinstance(element, int) else None,
            "rows": out_rows}


def _source_packet(source, doc, structure, obs_facts, fields, route_facts):
    """Project one source; returns (fact, anchored pending entries, cost)."""
    sid = source["source_id"]
    fact, pend = _whitelist(source, SOURCE_KEYS), []
    if doc is None:
        pend.append({"reason": "documents_index_missing"})
    else:
        if _is_sha(doc.get("structure_sha256")):
            fact["structure_sha256"] = doc["structure_sha256"]
        if structure is None:
            pend.append({"reason": "structure_index_missing"})
    if structure is not None and not isinstance(structure, dict):
        structure = None
        pend.append({"reason": "structure_malformed"})
    facts = []
    if isinstance(structure, dict):
        extra = len(set(structure) - {"tables", "code_blocks"})
        if extra:
            fact["structure_unprojected_key_count"] = extra
        for table in structure.get("tables") or []:
            entry = _table(table, fields, pend) if isinstance(table, dict) \
                else None
            if entry is not None:
                facts.append(entry)
            elif not isinstance(table, dict):
                pend.append({"reason": "table_layout_unsupported"})
        for block in structure.get("code_blocks") or []:
            block = block if isinstance(block, dict) else {}
            element = block.get("element_index")
            element = element if isinstance(element, int) else None
            lang, text = block.get("language"), block.get("text")
            entry = {"kind": "code_block", "element_index": element,
                     "language": "json" if lang == "json" else "other"}
            why = None
            if lang != "json" or not isinstance(text, str):
                why = "non_json_example"
            elif len(text.encode("utf-8")) > EXAMPLE_BYTES:
                entry["omitted"], why = True, "example_too_large"
            else:
                try:
                    st = {"n": 0, "partial": False, "withheld": 0}
                    entry["shape"] = _shape(
                        _loads(text.encode("utf-8"),
                               "example is not strict JSON"), fields, st)
                    entry["json_valid"] = True
                    if st["withheld"]:
                        entry["withheld_key_count"] = st["withheld"]
                        pend.append({"element_index": element,
                                     "reason": "json_keys_withheld",
                                     "withheld_key_count": st["withheld"]})
                    if st["partial"]:
                        entry["shape_partial"] = True
                        why = "shape_budget_exceeded"
                except ProjError:
                    entry["json_valid"] = False
                    why = "non_strict_json_example"
            if why:
                pend.append({"element_index": element, "reason": why})
            facts.append(entry)
    fact["structure_facts"] = facts
    routes = []
    for op in source.get("operation_ids") or []:
        ref = route_facts.get(op)
        verified = isinstance(ref, dict) and \
            ref.get("region") == source.get("region") and \
            str(ref.get("source_doc_id")) == str(source.get("doc_id")) and \
            ref.get("source_content_sha256") == source.get("content_sha256")
        if not (isinstance(op, str) and verified):
            pend.append({"reason": "route_fact_unverified"
                         if isinstance(op, str) else
                         "operation_id_malformed",
                         "operation_id": op if isinstance(op, str) else "?"})
            routes.append({"operation_id": op
                           if isinstance(op, str) else "?"})
            continue
        method = ref.get("method")
        routes.append({"operation_id": op, "region": ref.get("region"),
                       "path": ref.get("path")
                       if isinstance(ref.get("path"), str) else None,
                       "method": method if method in METHODS else None})
        if method not in METHODS:
            pend.append({"reason": "route_method_unknown",
                         "operation_id": op})
    fact["route_refs"] = routes
    if source.get("unresolved"):
        pend.append({"reason": "ledger_unresolved",
                     "detail_count": len(source["unresolved"])})
    for item in pend:
        item.setdefault("source_id", sid)
    fact["projection_complete"] = not pend
    cost = _nodes(fact) + sum(_nodes(o) for o in obs_facts) + \
        sum(_nodes(p) for p in pend)
    if cost > MAX_NODES:
        fact = {"source_id": sid, "region": source.get("region"),
                "doc_id": source.get("doc_id"),
                "record_index": source.get("record_index"),
                "content_sha256": source.get("content_sha256"),
                "structure_sha256": fact.get("structure_sha256"),
                "observation_ids": [o["observation_id"] for o in obs_facts],
                "omitted_fact_nodes": cost, "pending_split": True,
                "projection_complete": False}
        pend = [{"reason": "source_fact_over_budget", "source_id": sid,
                 "omitted_pending_count": len(pend),
                 "pending_split": True}]
        cost = _nodes(fact) + sum(_nodes(o) for o in obs_facts) + \
            sum(_nodes(p) for p in pend)
    return fact, pend, cost


def _lane_map(groups, sources):
    """Index all records by unique source_id, then resolve each routing
    'region:doc_id' member to its canonical portal source_id exactly; the
    selected record must be a portal_document whose region/doc_id match.
    Non-portal records (doc_id may be null) never collide."""
    by_id = {}
    for source in sources:
        if not isinstance(source, dict):
            raise _err("ledger source malformed")
        sid = source.get("source_id")
        if not isinstance(sid, str) or sid in by_id:
            raise _err("ledger source identity invalid")
        by_id[sid] = source
    lane_map, selected = {}, {}
    for lane, members in groups.items():
        if not isinstance(members, list):
            raise _err("routing lane malformed")
        for member in members:
            if not isinstance(member, str) or ":" not in member:
                raise _err("routing member malformed")
            region, doc_id = member.split(":", 1)
            source = by_id.get("portal-2026-11-17:%s:%s" % (region, doc_id))
            if source is None or source.get("kind") != "portal_document" \
                    or source.get("region") != region \
                    or str(source.get("doc_id")) != doc_id:
                raise _err("routing member missing from ledger")
            sid = source["source_id"]
            if sid in lane_map:
                raise _err("routing member duplicated")
            lane_map[sid] = lane
            selected[sid] = source
    return lane_map, selected


def run(repo, plan_path, out_arg):
    loaded = [(str(plan_path), PLAN_SHA)]
    plan_raw = _read(plan_path)
    if _sha256(plan_raw) != PLAN_SHA:
        raise _err("plan pin mismatch")
    plan = _loads(plan_raw, "plan is not valid JSON")
    if plan.get("schema_version") != 1 or plan.get("task_id") != "SP037":
        raise _err("plan identity mismatch")
    shas, data = {}, {}
    for name in REFS:
        entry = (plan.get("input_refs") or {}).get(name) or {}
        path, sha = entry.get("path"), entry.get("sha256")
        if not isinstance(path, str) or not os.path.isabs(path) or \
                not _is_sha(sha):
            raise _err("pinned input malformed")
        raw = _read(path)
        if _sha256(raw) != sha:
            raise _err("input pin mismatch for " + name)
        loaded.append((path, sha))
        shas[name] = sha
        data[name] = _loads(raw, "input " + name + " is not valid JSON")
    ledger, vocab = data["ledger"], data["reviewed_vocabulary"]
    if ledger.get("schema_version") != 2 or \
            ledger.get("publication_profile") != "reviewed_metadata_only":
        raise _err("ledger profile mismatch")
    if vocab.get("registry_sha256") != shas["registry"]:
        raise _err("registry pin does not match vocabulary")
    if data["acceptance_criteria"].get("baseline_commit") != \
            plan.get("baseline_commit"):
        raise _err("acceptance baseline mismatch")
    groups = data["routing"].get("groups")
    if not isinstance(groups, dict) or not set(groups) <= set(EXPECTED_LANES)\
            or any(not isinstance(groups.get(lane), list) or
                   len(groups[lane]) != count
                   for lane, count in EXPECTED_LANES.items()):
        raise _err("routing lane membership mismatch")
    sources, observations = ledger.get("sources"), ledger.get("observations")
    if not isinstance(sources, list) or not isinstance(observations, list) \
            or (len(sources), len(observations)) != EXPECTED_LEDGER:
        raise _err("ledger is not the pinned full library")
    lane_map, selected = _lane_map(groups, sources)
    if len(lane_map) != EXPECTED_SOURCES:
        raise _err("routing identity union mismatch")
    obs_by_source, selected_obs, seen_obs = {}, set(), set()
    for obs in observations:
        if not isinstance(obs, dict):
            raise _err("ledger observation malformed")
        oid, sid = obs.get("observation_id"), obs.get("source_id")
        if not isinstance(oid, str) or oid in seen_obs:
            raise _err("ledger observation identity invalid")
        seen_obs.add(oid)
        if sid in lane_map:
            selected_obs.add(oid)
            obs_by_source.setdefault(sid, []).append(obs)
    if len(selected_obs) != EXPECTED_OBS:
        raise _err("selected observation count mismatch")
    root = plan.get("source_archive_root")
    if not isinstance(root, str) or not os.path.isabs(root):
        raise _err("source_archive_root malformed")
    root = Path(root).resolve()
    pins = data["source_inputs"].get("pins")
    if not isinstance(pins, dict):
        raise _err("source input pins missing")
    docs_raw, doc_index = None, {}
    for relpath, sha in sorted(pins.items()):
        raw, target = _archive_read(root, relpath, sha)
        loaded.append((str(target), sha))
        if relpath == "documents.jsonl":
            docs_raw = raw
    if docs_raw is None:
        raise _err("documents.jsonl not pinned")
    for line in docs_raw.split(b"\n"):
        if not line.strip():
            continue
        doc = _loads(line, "documents record is not valid JSON")
        key = (doc.get("region"), str(doc.get("doc_id")))
        if key in doc_index:
            raise _err("documents record duplicated")
        doc_index[key] = doc
    fields = {n for n in vocab.get("field_names") or [] if isinstance(n, str)}
    route_facts = {r["operation_id"]: r for r in
                   vocab.get("route_facts") or []
                   if isinstance(r, dict) and isinstance(
                       r.get("operation_id"), str)}
    if len(fields) != EXPECTED_FIELDS or len(route_facts) != EXPECTED_ROUTES:
        raise _err("reviewed vocabulary counts mismatch")
    voice_ids, voice_obs = set(), set()
    scope = data["voice_review"].get("scope") or {}
    for src in scope.get("sources") or []:
        sid = src.get("source_id") if isinstance(src, dict) else None
        if sid in EXPECTED_VOICE:
            if sid not in selected or src.get("content_sha256") != \
                    selected[sid].get("content_sha256"):
                raise _err("voice review source pin mismatch")
            voice_ids.add(sid)
    for rev in scope.get("observation_reviews") or []:
        ref = rev.get("observation_ref") if isinstance(rev, dict) else None
        if isinstance(ref, str):
            voice_obs.add(ref)
    if voice_ids != EXPECTED_VOICE or len(voice_obs) != EXPECTED_VOICE_OBS \
            or not voice_obs <= selected_obs:
        raise _err("voice review scope mismatch")
    lane_sources = {lane: [] for lane in EXPECTED_LANES}
    for sid, lane in lane_map.items():
        lane_sources[lane].append(selected[sid])
    try:
        for members in lane_sources.values():
            members.sort(key=lambda s: int(s["doc_id"]))
    except (KeyError, TypeError, ValueError):
        raise _err("source doc_id is not numeric") from None
    shards, emitted_obs = [], set()
    for lane in EXPECTED_LANES:
        shard = None
        for source in lane_sources[lane]:
            sid = source["source_id"]
            doc = doc_index.get((source.get("region"),
                                 str(source.get("doc_id"))))
            structure = None
            if doc is not None:
                if doc.get("content_sha256") != source.get("content_sha256"):
                    raise _err("source content pin mismatch")
                sfile, ssha = doc.get("structure_file"), \
                    doc.get("structure_sha256")
                if isinstance(sfile, str) and _is_sha(ssha):
                    raw, target = _archive_read(root, sfile, ssha)
                    loaded.append((str(target), ssha))
                    structure = _loads(raw, "structure JSON invalid")
            obs_facts = [_whitelist(o, OBS_KEYS)
                         for o in obs_by_source.get(sid, [])]
            fact, pend, cost = _source_packet(
                source, doc, structure, obs_facts, fields, route_facts)
            if shard is None or shard["count"] >= MAX_SOURCES \
                    or shard["cost"] + cost > MAX_NODES:
                shard = {"lane": lane, "sources": [], "obs": [], "pend": [],
                         "count": 0, "cost": 0, "over": False}
                shards.append(shard)
            if cost > MAX_NODES:
                shard["over"] = True
            shard["sources"].append(fact)
            shard["obs"].extend(obs_facts)
            shard["pend"].extend(pend)
            shard["count"] += 1
            shard["cost"] += cost
            emitted_obs.update(o["observation_id"] for o in obs_facts)
    if len(shards) < MIN_SHARDS or emitted_obs != selected_obs or \
            sum(s["count"] for s in shards) != EXPECTED_SOURCES:
        raise _err("conservation or shard minimum failed")
    for path, sha in loaded:
        if _sha256(_read(path)) != sha:
            raise _err("input mutated during projection")
    out_root = Path(out_arg)
    if not out_root.is_absolute():
        out_root = Path(repo) / out_root
    authorized = (Path(repo).resolve() / OUT_REL).resolve()
    if out_root.resolve() != authorized or os.path.lexists(authorized):
        raise _err("output root unauthorized or already exists")
    authorized.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".sp037-", dir=str(authorized.parent)))
    files = []
    try:
        for index, shard in enumerate(shards):
            packet = {
                "input_pins": {"plan": PLAN_SHA,
                               "inputs": {n: shas[n] for n in REFS}},
                "lane_shard": {"lane": shard["lane"], "shard_index": index,
                               "shard_count": len(shards),
                               "source_count": shard["count"]},
                "source_facts": shard["sources"],
                "observation_facts": shard["obs"],
                "reviewed_refs": {
                    "voice_review_source_ids": sorted(voice_ids),
                    "voice_review_observation_count": len(voice_obs)},
                "pending_inputs": shard["pend"],
                "conservation_receipt": {
                    "lane": shard["lane"], "shard_index": index,
                    "source_count": shard["count"],
                    "observation_count": len(shard["obs"]),
                    "fact_nodes": shard["cost"],
                    "pending_count": len(shard["pend"]),
                    "projection_complete": all(
                        s.get("projection_complete")
                        for s in shard["sources"]),
                    "status": "unreviewed_evidence"}}
            if shard["over"]:
                packet["lane_shard"]["anchor_over_budget"] = True
            raw = json.dumps(packet, ensure_ascii=False, indent=1,
                             sort_keys=True).encode("utf-8") + b"\n"
            name = "shard-%03d.json" % index
            with open(tmp / name, "xb") as handle:
                handle.write(raw)
            entry = {"file": name, "sha256": _sha256(raw),
                     "lane": shard["lane"], "shard_index": index,
                     "source_count": shard["count"],
                     "observation_count": len(shard["obs"]),
                     "fact_nodes": shard["cost"],
                     "pending_count": len(shard["pend"]),
                     "incomplete_sources": sum(
                         1 for s in shard["sources"]
                         if not s.get("projection_complete"))}
            if shard["over"]:
                entry["anchor_over_budget"] = True
            files.append(entry)
        receipt = {"conservation_receipt": {
            "plan_sha256": PLAN_SHA, "shards": len(shards),
            "sources": sum(s["count"] for s in shards),
            "observations": len(emitted_obs),
            "pending_count": sum(len(s["pend"]) for s in shards),
            "voice_review_sources": len(voice_ids),
            "source_reviewed": False, "api_supported": False,
            "real_verified": False, "product_complete": False,
            "publication_authorized": False, "files": files}}
        raw = json.dumps(receipt, ensure_ascii=False, indent=1,
                         sort_keys=True).encode("utf-8") + b"\n"
        with open(tmp / "receipt.json", "xb") as handle:
            handle.write(raw)
        os.replace(tmp, authorized)
    except BaseException:
        for path in sorted(tmp.rglob("*"), reverse=True):
            try:
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass
        raise
    return len(files) + 1


def _self_check():
    """Synthetic temp inputs only; never touches real sources/SDK/network."""
    vocab = {"accessToken", "deviceSerial", "code", "msg", "data"}
    canary, res = "SYNTHETIC_SECRET_DO_NOT_EXPORT", {}

    def cell(kind, text, rs=1, cs=1):
        return {"kind": kind, "text": text, "rowspan": rs, "colspan": cs}

    for i, bad in enumerate(
            ('{"a":1,"a":2}', '{"a":NaN}', '{"a":1} junk', "curl -x")):
        try:
            _loads(bad.encode(), "strict")
            res["strict%d" % i] = False
        except ProjError:
            res["strict%d" % i] = True
    st = {"n": 0, "partial": False, "withheld": 0}
    sh = _shape({"code": canary,
                 "data": {canary: True, "deviceSerial": canary}}, vocab, st)
    res["canary_shape"] = canary not in json.dumps(sh)
    res["dynkey"] = sh["fields"]["data"]["unprojected_key_count"] == 1 and \
        st["withheld"] == 1
    st = {"n": 0, "partial": False, "withheld": 0}
    res["hetero_array"] = len(_shape(
        [{"code": 1}, {"deviceSerial": "x"}, 7], vocab, st)["items"]) == 3
    st = {"n": 0, "partial": False, "withheld": 0}
    nsh = _shape({"data": [{canary: 1}, {"code": {canary: 2}}],
                  "msg": canary}, vocab, st)
    res["nested_withheld"] = st["withheld"] == 2 and \
        canary not in json.dumps(nsh)
    st = {"n": 0, "partial": False, "withheld": 0}
    osh = _shape({"data": {canary: [1, {canary: 2}]}, "code": "x"},
                 vocab, st)
    res["omitted_subtree"] = st["withheld"] == 1 and \
        osh["fields"]["data"]["unprojected_key_count"] == 1 and \
        canary not in json.dumps(osh)
    deep = []
    cur = deep
    for _ in range(200):
        cur.append([])
        cur = cur[0]
    cur.append(1)
    st = {"n": 0, "partial": False}
    dsh = _shape(deep, vocab, st)
    res["partial"] = st["partial"] and dsh.get("partial") is True and \
        dsh["items"][0]["type"] == "array"
    res["whitelist"] = _whitelist({"a": 1, "b": 2}, {"a"}) == \
        {"a": 1, "unprojected_key_count": 1}
    table = {"element_index": 7, "rows": [
        [cell("th", h) for h in
         ("Name", "Type", "Required", "Location", "Description")],
        [cell("td", t) for t in
         ("accessToken", "String", "Y", "Query", canary)],
        [cell("td", t) for t in ("mystery", "Weird", "maybe", "Moon", canary)]]}
    pend = []
    ent = _table(table, vocab, pend)
    res["table_canary"] = canary not in json.dumps(ent)
    res["table_row"] = (ent["rows"][0]["name"], ent["rows"][0]["required"],
                        ent["rows"][0]["location"]) == \
        ("accessToken", True, "query")
    res["table_bad"] = ent["rows"][1]["required"] is None and \
        {"unreviewed_field_name", "unreviewed_type", "unreviewed_required",
         "unreviewed_location"} <= {p["reason"] for p in pend} and \
        all("row" in p and "column" in p for p in pend)
    res["merged_table"] = _table(
        {"element_index": 8,
         "rows": [[cell("th", "Name"), cell("th", "T", rs=2)]]},
        vocab, []) is None
    pend_cn = []
    ent_cn = _table({"element_index": 9, "rows": [
        [cell("th", "字段名"), cell("th", "类型"), cell("th", "是否必选")],
        [cell("td", "accessToken"), cell("td", "String"), cell("td", "是")],
        [cell("td", "unreviewed_field"), cell("td", "Mystery"),
         cell("td", "也许")]]}, vocab, pend_cn)
    res["cn_field_name_header"] = ent_cn is not None and \
        (ent_cn["rows"][0]["name"], ent_cn["rows"][0]["type"],
         ent_cn["rows"][0]["required"]) == ("accessToken", "String", True) and \
        ent_cn["rows"][1]["name"] is None and \
        {"unreviewed_field_name", "unreviewed_type",
         "unreviewed_required"} <= {p["reason"] for p in pend_cn}
    src = {"source_id": "portal-2026-11-17:domestic:1", "region": "domestic",
           "doc_id": "1", "record_index": 0, "kind": "portal_document",
           "status": "archived", "content_sha256": "a" * 64,
           "operation_ids": ["op.x"], "unresolved": []}
    obs = [_whitelist(
        {"observation_id": src["source_id"] + ":declaration:0",
         "source_id": src["source_id"], "kind": "declaration", "ordinal": 0,
         "status": "declared_pair", "method": "GET"}, OBS_KEYS)]
    doc = {"region": "domestic", "doc_id": "1", "content_sha256": "a" * 64,
           "structure_file": "derived/domestic/1.structure.json",
           "structure_sha256": "b" * 64}
    rf = {"op.x": {"operation_id": "op.x", "region": "domestic",
                   "source_doc_id": "1", "method": "POST", "path": "/x",
                   "source_content_sha256": "c" * 64}}
    fact, pend, _ = _source_packet(src, doc, None, obs, vocab, rf)
    res["unverified_route"] = fact["route_refs"][0] == \
        {"operation_id": "op.x"} and any(
            p["reason"] == "route_fact_unverified" for p in pend)
    res["missing_structure"] = fact["projection_complete"] is False and \
        any(p["reason"] == "structure_index_missing" for p in pend)
    rf["op.x"]["source_content_sha256"] = "a" * 64
    small = {"tables": [], "code_blocks": [
        {"element_index": 2, "language": "json",
         "text": '{"code":"' + canary + '","deviceSerial":1}'}]}
    fact3, _p3, _ = _source_packet(src, doc, small, obs, vocab, rf)
    res["verified_route"] = fact3["route_refs"][0]["path"] == "/x" and \
        fact3["route_refs"][0]["method"] == "POST" and \
        fact3["projection_complete"]
    res["example_shape"] = canary not in json.dumps(fact3) and \
        fact3["structure_facts"][0]["shape"]["fields"]["code"]["type"] == \
        "string"
    big = {"tables": [{"element_index": 1, "rows": [
        [cell("th", "Name"), cell("th", "Type")]] + [
            [cell("td", "accessToken"), cell("td", "String")]
            for _ in range(1000)]}]}
    cost_probe = _nodes(_table(big["tables"][0], vocab, []))
    fact5, _p5, _ = _source_packet(src, doc, big, obs, vocab, rf)
    res["slim_split"] = cost_probe > MAX_NODES and \
        fact5.get("pending_split") is True and \
        fact5["projection_complete"] is False and \
        fact5["structure_sha256"] == "b" * 64 and \
        "rows" not in json.dumps(fact5) and fact5["observation_ids"] == \
        [src["source_id"] + ":declaration:0"]
    lane_map, _sel = _lane_map(
        {"A": ["domestic:1"], "C": ["overseas:2"]},
        [{"source_id": "portal-2026-11-17:domestic:1", "region": "domestic",
          "doc_id": "1", "kind": "portal_document"},
         {"source_id": "portal-2026-11-17:overseas:2", "region": "overseas",
          "doc_id": "2", "kind": "portal_document"},
         {"source_id": "sdk:0", "region": None, "doc_id": None,
          "kind": "sdk_reference"},
         {"source_id": "sdk:1", "region": None, "doc_id": None,
          "kind": "sdk_reference"},
         {"source_id": "sdk:2", "region": "domestic", "doc_id": None,
          "kind": "sdk_reference"},
         {"source_id": "asset:0", "region": None, "doc_id": None,
          "kind": "asset"}])
    res["routing_map"] = lane_map == {
        "portal-2026-11-17:domestic:1": "A",
        "portal-2026-11-17:overseas:2": "C"}
    try:
        _lane_map({"A": ["domestic:404"]}, [])
        res["missing_member"] = False
    except ProjError:
        res["missing_member"] = True
    unknown_json = '{"code":"' + canary + '","' + canary + '":1,"data":' \
        '[{"' + canary + '":2},{"deviceSerial":3}]}'
    struct_unknown = {"tables": [], "code_blocks": [
        {"element_index": 4, "language": "json", "text": unknown_json}]}
    fact_u, pend_u, _ = _source_packet(src, doc, struct_unknown, obs, vocab,
                                       rf)
    res["withheld_pending"] = fact_u["projection_complete"] is False and \
        canary not in json.dumps(fact_u) and \
        canary not in json.dumps(pend_u) and \
        any(p["reason"] == "json_keys_withheld" and
            p.get("element_index") == 4 and
            p.get("withheld_key_count") == 2 for p in pend_u)
    struct_many = {"tables": [], "code_blocks": [
        {"element_index": i, "language": "json",
         "text": '{"code":1,"k%d":2}' % i} for i in range(1500)]}
    fact_m, pend_m, cost_m = _source_packet(src, doc, struct_many, obs, vocab,
                                          rf)
    res["over_budget_pending"] = cost_m <= MAX_NODES and \
        fact_m["projection_complete"] is False and \
        fact_m.get("pending_split") is True and len(pend_m) == 1 and \
        pend_m[0].get("omitted_pending_count") >= 1 and \
        canary not in json.dumps(fact_m) and \
        fact_m["observation_ids"] == [src["source_id"] + ":declaration:0"]
    res["stub_keeps_pins"] = fact_m["structure_sha256"] == "b" * 64 and \
        fact_m["content_sha256"] == "a" * 64
    struct_names = {"tables": [{"element_index": 1, "rows": [
        [cell("th", "Name")]] + [
            [cell("td", "zz_unknown_field_%d" % i)]
            for i in range(1000)]}]}
    fact_n, pend_n, cost_n = _source_packet(src, doc, struct_names, obs,
                                          vocab, rf)
    res["unknown_names_split"] = cost_n <= MAX_NODES and \
        fact_n["projection_complete"] is False and \
        fact_n.get("pending_split") is True and len(pend_n) == 1 and \
        pend_n[0]["reason"] == "source_fact_over_budget" and \
        pend_n[0]["omitted_pending_count"] == 1000 and \
        "zz_unknown_field_" not in json.dumps(fact_n) and \
        "zz_unknown_field_" not in json.dumps(pend_n) and \
        fact_n["content_sha256"] == "a" * 64 and \
        fact_n["structure_sha256"] == "b" * 64 and \
        fact_n["observation_ids"] == [src["source_id"] + ":declaration:0"]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        (root / "ok.json").write_bytes(b'{"a":1}')
        good = _sha256(b'{"a":1}')
        try:
            _archive_read(root, "ok.json", good)
            res["archive_ok"] = True
        except ProjError:
            res["archive_ok"] = False
        for rel, sha in (("../ok.json", good), ("ok.json", "0" * 64),
                         ("ok.json", "bad"), ("../" + canary, good)):
            try:
                _archive_read(root, rel, sha)
                res["reject_" + rel[:3]] = False
            except ProjError as exc:
                res["reject_" + rel[:3]] = \
                    canary not in str(exc) and rel not in str(exc)
    failed = [name for name, ok in res.items() if not ok]
    for name in failed:
        sys.stderr.write("self-check failed: %s\n" % name)
    return len(res) - len(failed), len(res)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="project_semantic_review",
        description="One-shot SP037 whitelist projection (evidence only).")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_check:
            passed, total = _self_check()
            sys.stdout.write("self-check: %d/%d (synthetic temp inputs only)\n"
                             % (passed, total))
            return 0 if passed == total else 2
        for flag in ("repo", "plan", "out"):
            if getattr(args, flag) is None:
                raise _err("--%s is required" % flag)
        written = run(args.repo, args.plan, args.out)
        sys.stdout.write("packets written: %d\n" % written)
        return 0
    except ProjError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    except Exception:
        sys.stderr.write("project_semantic_review: unexpected failure\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
