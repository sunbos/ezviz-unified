"""SP054 bounded supplemental-evidence pilot (synthetic self-check + thin CLI).

Emits only two fact kinds (label_context, field_table) plus anchored pending
entries for the nine SHA-pinned sources of the frozen pilot plan. No network,
no env reads, no SDK, no prose/code/URL/credential export. Real pilot runs are
dispatched separately; --self-check uses synthetic temp inputs only.
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
    ProjError, _archive_read, _is_sha, _loads, _nodes, _sha256, _table)
from tools.documents.http_candidates import (  # noqa: E402
    _ALL_LABELS as _LABELS, _element_label_key)

assert _LABELS  # fixed vocabulary; _element_label_key only returns members

PLAN_SHA = "19b602d88981ff589b7315e1955d04ee70401f58089b072e753b40dfbf9bc255"
OUT_REL = "data/coordination/SP054-evidence-pilot.json"
MAX_SOURCES, MAX_NODES, MAX_OUT_BYTES = 9, 4096, 512 * 1024
OPAQUE = {"table", "pre", "code", "script", "style"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SOURCE_KEYS = ("source_id", "region", "doc_id", "raw_file",
               "raw_file_sha256", "content_sha256", "structure_file",
               "structure_sha256")
CODE_PIN_FILES = (
    "docs/coordination/SP054/supplement_evidence.py",
    "docs/coordination/SP037/project_semantic_review.py",
    "tools/documents/http_candidates.py")
# Raw DOM tags a pinned target of each structure kind may resolve to.
TARGET_TAGS = {"table": frozenset({"table"}),
               "code_block": frozenset({"pre", "code"})}


def _err(reason):
    return ProjError("supplement_evidence: " + reason)


def _scan(soup):
    """Snapshot original id(element)->index, tag and opaque-ancestor flags for
    every element BEFORE any mutation (same numbering as parse_html), then
    decompose script/style and unwrap br. Surviving elements keep their
    parser-original indices; pinned anchors must never be renumbered."""
    original = list(soup.find_all(True))
    index_of = {id(el): i for i, el in enumerate(original)}
    tag_of = {i: el.name for i, el in enumerate(original)}
    opaque_of = {i: el.find_parent(list(OPAQUE)) is not None
                 for i, el in enumerate(original)}
    for element in soup.find_all(["script", "style"]):
        element.decompose()
    for br in soup.find_all("br"):
        br.insert_before("\n")
        br.unwrap()
    return index_of, tag_of, opaque_of


def _targets(structure, pend):
    """Pinned structure.tables + structure.code_blocks -> {index: (kind, dict)}.
    These are the only label_context targets; anything malformed or duplicated
    becomes a numeric-anchored pending instead of a silent guess."""
    targets = {}
    for kind, key in (("table", "tables"), ("code_block", "code_blocks")):
        for item in structure.get(key) or []:
            if not isinstance(item, dict) or \
                    not isinstance(item.get("element_index"), int):
                pend.append({"element_index": None,
                             "reason": "target_index_malformed"})
                continue
            index = item["element_index"]
            if index in targets:
                pend.append({"element_index": index,
                             "reason": "target_index_duplicated"})
                continue
            targets[index] = (kind, item)
    return targets


def _facts(soup, structure, fields):
    """One label_context per pinned structure target (table/code_block) plus
    one field_table per pinned table via the SP037 whitelist. Label carrier
    nodes only update the rolling context and never become facts; ordinary
    p/div/span never become facts; anything inside an opaque ancestor is never
    an external label. Every gap stays a numeric-anchored pending."""
    index_of, tag_of, opaque_of = _scan(soup)
    facts, pend = [], []
    targets = _targets(structure, pend)
    current_key, current_index = None, None
    seen = set()

    def emit_target(index):
        kind, item = targets[index]
        # A pinned index must resolve to a surviving raw DOM element whose
        # original tag belongs to the kind's allowed set BEFORE any fact is
        # emitted; otherwise the supplement is rejected outright, since a
        # pinned structure/anchor contradiction can never be a valid fact.
        if index not in tag_of or tag_of[index] not in TARGET_TAGS[kind]:
            raise _err("pinned target invalid")
        has_context = opaque_of.get(index) is False
        if opaque_of.get(index) is True:
            pend.append({"element_index": index,
                         "reason": "target_inside_opaque"})
        facts.append({"fact_id": "e%d" % index, "kind": "label_context",
                      "element_index": index, "element_kind": kind,
                      "preceding_label_key":
                          current_key if has_context else None,
                      "label_element_index":
                          current_index if has_context else None,
                      "context_only": True})
        if kind == "table":
            entry = _table(item, fields, pend)
            if entry is not None:
                facts.append({"fact_id": "t%d" % index, "kind": "field_table",
                              "projection": entry})

    for element in soup.find_all(True):
        index = index_of.get(id(element))
        if index is None:
            continue
        if index in targets:
            seen.add(index)
            emit_target(index)
            continue
        if element.name in OPAQUE or opaque_of.get(index):
            continue  # opaque node itself or inside one: never a label carrier
        if element.name in HEADINGS:
            key = _element_label_key(element)
            current_key, current_index = (key, index) if key else (None, None)
            continue
        key = _element_label_key(element)
        if key is not None:
            current_key, current_index = key, index
    for index in sorted(set(targets) - seen):
        emit_target(index)
        if opaque_of.get(index) is not True:
            pend.append({"element_index": index,
                         "reason": "target_index_missing"})
    return facts, pend


def _output_target(repo, out_arg):
    """Resolve the prescribed fixed relative output under repo. The caller's
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


def _code_pins():
    """Deterministic SHA-256 of the three known code files, keyed by their
    fixed repo-relative paths; never machine absolute paths."""
    pins = {}
    for rel in CODE_PIN_FILES:
        raw = Path(_ROOT / rel).read_bytes() if _readable(_ROOT / rel) else None
        if raw is None:
            raise _err("code pin unavailable")
        pins[rel] = _sha256(raw)
    return pins


def run(repo, plan_path, out_arg):
    plan_raw = Path(plan_path).read_bytes() if _readable(plan_path) else None
    if plan_raw is None or _sha256(plan_raw) != PLAN_SHA:
        raise _err("plan pin mismatch")
    plan = _loads(plan_raw, "plan is not valid JSON")
    if plan.get("schema_version") != 1 or plan.get("task_id") != "SP054" or \
            plan.get("max_sources") != MAX_SOURCES or \
            plan.get("max_nodes") != MAX_NODES or \
            plan.get("output_relative_path") != OUT_REL:
        raise _err("plan identity mismatch")
    docs_sha = plan.get("archive_documents_sha256")
    if not _is_sha(docs_sha):
        raise _err("documents pin malformed")
    root = plan.get("archive_root")
    if not isinstance(root, str) or not os.path.isabs(root):
        raise _err("archive root malformed")
    root = Path(root).resolve()
    vocab_ref = plan.get("reviewed_vocabulary") or {}
    if not isinstance(vocab_ref.get("path"), str) or \
            not _is_sha(vocab_ref.get("sha256")):
        raise _err("vocabulary pin malformed")
    vocab_raw = Path(vocab_ref["path"]).read_bytes() \
        if _readable(vocab_ref["path"]) else None
    if vocab_raw is None or _sha256(vocab_raw) != vocab_ref["sha256"]:
        raise _err("vocabulary pin mismatch")
    fields = {n for n in (_loads(vocab_raw, "vocabulary invalid")
                          .get("field_names") or []) if isinstance(n, str)}
    sources = plan.get("sources")
    if not isinstance(sources, list) or not sources or \
            len(sources) > MAX_SOURCES or \
            len({s.get("source_id") for s in sources}) != len(sources):
        raise _err("source list malformed")
    # Verify the pinned documents.jsonl via the archive reader before any
    # source is processed; every source pin must agree with the index exactly.
    docs_raw, _ = _archive_read(root, "documents.jsonl", docs_sha)
    doc_index = {}
    for line in docs_raw.split(b"\n"):
        if not line.strip():
            continue
        doc = _loads(line, "documents record is not valid JSON")
        key = (doc.get("region"), str(doc.get("doc_id")))
        if key in doc_index:
            raise _err("documents record duplicated")
        doc_index[key] = doc
    for source in sources:
        if not isinstance(source, dict):
            raise _err("source entry malformed")
        for key in SOURCE_KEYS:
            if key == "doc_id":
                if source.get("doc_id") is None:
                    raise _err("source pin malformed")
            elif not isinstance(source.get(key), str):
                raise _err("source pin malformed")
        for key in ("raw_file_sha256", "content_sha256", "structure_sha256"):
            if not _is_sha(source.get(key)):
                raise _err("source pin malformed")
        doc = doc_index.get((source.get("region"),
                             str(source.get("doc_id"))))
        if not isinstance(doc, dict) or \
                doc.get("raw_file") != source["raw_file"] or \
                doc.get("raw_file_sha256") != source["raw_file_sha256"] or \
                doc.get("content_sha256") != source["content_sha256"] or \
                doc.get("structure_file") != source["structure_file"] or \
                doc.get("structure_sha256") != source["structure_sha256"]:
            raise _err("documents index mismatch")
    code_pins = _code_pins()
    out_sources, out_facts, out_pend, seen_files = [], [], [], set()
    total_nodes = 0
    for source in sources:
        for rel in (source["raw_file"], source["structure_file"]):
            if rel in seen_files:
                raise _err("archive file duplicated")
            seen_files.add(rel)
        raw, _ = _archive_read(root, source["raw_file"],
                               source["raw_file_sha256"])
        wrapper = _loads(raw, "raw source is not valid JSON")
        html = wrapper.get("html_content") if isinstance(wrapper, dict) \
            else None
        if not isinstance(html, str) or \
                _sha256(html.encode("utf-8")) != source["content_sha256"]:
            raise _err("content pin mismatch")
        sraw, _ = _archive_read(root, source["structure_file"],
                                source["structure_sha256"])
        structure = _loads(sraw, "structure is not valid JSON")
        if not isinstance(structure, dict):
            raise _err("structure malformed")
        soup = BeautifulSoup(html, "html.parser")
        facts, pend = _facts(soup, structure, fields)
        code_anchors = [b.get("element_index") for b in
                        (structure.get("code_blocks") or [])
                        if isinstance(b, dict)]
        pend.append({"source_id": source["source_id"],
                     "reason": "prose_not_projected"})
        for anchor in code_anchors:
            pend.append({"source_id": source["source_id"],
                         "element_index": anchor
                         if isinstance(anchor, int) else None,
                         "reason": "code_content_not_projected"})
        for fact in facts:
            fact["source_id"] = source["source_id"]
            fact["fact_id"] = "%s:%s:%s" % (
                source["source_id"], fact["kind"], fact["fact_id"])
        for item in pend:
            item.setdefault("source_id", source["source_id"])
        out_sources.append({k: source[k] for k in SOURCE_KEYS})
        total_nodes += _nodes(out_sources[-1]) + \
            sum(_nodes(f) for f in facts) + sum(_nodes(p) for p in pend)
        if total_nodes > MAX_NODES:
            raise _err("node budget exceeded")
        out_facts.extend(facts)
        out_pend.extend(pend)
    if len(out_sources) != len(sources):
        raise _err("conservation failed")
    packet = {
        "schema_version": 1,
        "input_pins": {"plan_sha256": PLAN_SHA,
                       "archive_documents_sha256": docs_sha,
                       "reviewed_vocabulary_sha256": vocab_ref["sha256"]},
        "sources": out_sources,
        "facts": out_facts,
        "pending": out_pend,
        "receipt": {"source_count": len(out_sources),
                    "fact_count": len(out_facts),
                    "pending_count": len(out_pend),
                    "total_nodes": total_nodes,
                    "code_sha256": code_pins,
                    "full_source_review": False,
                    "protocol_freeze": False,
                    "raw_examples_exported": False}}
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True,
                     indent=1).encode("utf-8") + b"\n"
    if len(raw) > MAX_OUT_BYTES:
        raise _err("output budget exceeded")
    target = _output_target(repo, out_arg)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sp054-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(out_sources), len(out_facts), len(out_pend)


def _readable(path):
    try:
        return os.path.isfile(path)
    except (OSError, TypeError, ValueError):
        return False


def _cell(kind, text):
    return {"kind": kind, "text": text, "rowspan": 1, "colspan": 1}


def _self_check():
    """Synthetic temp inputs only; never touches real archive/plan/env."""
    vocab = {"accessToken", "deviceSerial", "code", "msg", "data"}
    canary = "SYNTHETIC_SECRET_DO_NOT_EXPORT"
    res = {}

    def raw_soup(html):
        return BeautifulSoup(html, "html.parser")

    def index_of(html, name, nth=0):
        return [i for i, e in enumerate(raw_soup(html).find_all(True))
                if e.name == name][nth]

    field_table = {"element_index": None, "rows": [
        [_cell("th", "字段名"), _cell("th", "类型")],
        [_cell("td", "accessToken"), _cell("td", "String")],
        [_cell("td", "返回数据"), _cell("td", "String")]]}

    # A1 counterexample: ordinary p/div are never label_context targets; the
    # one pinned code_block gets exactly one label_context of kind code_block.
    html = "<h2>返回数据</h2><p>note</p><div>d</div><pre>code</pre>"
    c_idx = index_of(html, "pre")
    facts, pend = _facts(raw_soup(html), {"tables": [], "code_blocks": [
        {"element_index": c_idx, "language": "json", "text": "{}"}]}, vocab)
    contexts = [f for f in facts if f["kind"] == "label_context"]
    res["targets_only_pinned"] = \
        [(f["element_index"], f["element_kind"]) for f in contexts] == \
        [(c_idx, "code_block")]
    res["no_plain_p_facts"] = all(
        f["element_kind"] in ("table", "code_block") for f in contexts) and \
        len(contexts) == 1
    res["context_kind_enum"] = all(
        f["element_kind"] in ("table", "code_block") for f in contexts)

    # A2 counterexample: script/style/br before the table shift the mutated
    # DOM; pinned indices must still resolve on the original numbering.
    html = ('<script><p>返回码</p></script><h2>请求参数</h2><p>a</p><br>'
            '<style>x</style><table><tr><th>字段名</th><th>类型</th></tr>'
            '<tr><td>accessToken</td><td>String</td></tr></table>')
    t_idx, h_idx = index_of(html, "table"), index_of(html, "h2")
    table = dict(field_table, element_index=t_idx,
                 rows=field_table["rows"][:2])
    facts, pend = _facts(raw_soup(html), {"tables": [table]}, vocab)
    tctx = [f for f in facts if f["kind"] == "label_context" and
            f["element_kind"] == "table"]
    res["original_index_table"] = len(tctx) == 1 and \
        tctx[0]["element_index"] == t_idx and \
        tctx[0]["preceding_label_key"] == "请求参数" and \
        tctx[0]["label_element_index"] == h_idx and \
        any(f["kind"] == "field_table" for f in facts)

    # Known labels anchor later targets; an unknown heading resets context.
    html = ("<h2>请求参数</h2><p>x</p><h2>返回数据</h2><pre>c1</pre>"
            "<h2>未登记标题</h2><pre>c2</pre>")
    c1, c2 = index_of(html, "pre", 0), index_of(html, "pre", 1)
    facts, pend = _facts(raw_soup(html), {"tables": [], "code_blocks": [
        {"element_index": c1, "language": "", "text": "c1"},
        {"element_index": c2, "language": "", "text": "c2"}]}, vocab)
    contexts = sorted((f for f in facts if f["kind"] == "label_context"),
                      key=lambda f: f["element_index"])
    res["known_labels"] = len(contexts) == 2 and \
        contexts[0]["preceding_label_key"] == "返回数据" and \
        contexts[0]["element_kind"] == "code_block"
    res["unknown_heading_resets"] = \
        contexts[1]["preceding_label_key"] is None and \
        contexts[1]["label_element_index"] is None
    res["codes_context_only"] = not any(
        "success" in json.dumps(f) or "version" in json.dumps(f)
        for f in facts)

    # Labels inside pre/table/script never update outside context; a pinned
    # outer code_block after them still sees the earlier external label.
    html = ("<h2>请求参数</h2><pre><p>返回数据</p></pre>"
            "<table><tr><th>字段名</th><th>类型</th></tr>"
            "<tr><td>accessToken</td><td>String</td></tr>"
            "<tr><td>返回数据</td><td>String</td></tr></table>"
            "<script><p>返回码</p></script><pre>real</pre>")
    t_idx, p_idx = index_of(html, "table"), index_of(html, "pre", 1)
    table = dict(field_table, element_index=t_idx)
    facts, pend = _facts(raw_soup(html), {"tables": [table], "code_blocks": [
        {"element_index": p_idx, "language": "", "text": "real"}]}, vocab)
    contexts = {f["element_kind"]: f for f in facts
                if f["kind"] == "label_context"}
    res["opaque_no_context"] = \
        contexts["code_block"]["preceding_label_key"] == "请求参数" and \
        contexts["table"]["preceding_label_key"] == "请求参数"
    res["no_label_inside_table"] = all(
        f["element_kind"] in ("table", "code_block") for f in facts
        if f["kind"] == "label_context") and all(
        f["element_index"] in (t_idx, p_idx) for f in facts
        if f["kind"] == "label_context")
    tables = [f for f in facts if f["kind"] == "field_table"]
    res["table_fact"] = len(tables) == 1 and \
        tables[0]["projection"]["rows"][0]["name"] == "accessToken" and \
        tables[0]["projection"]["rows"][1]["name"] is None and \
        canary not in json.dumps(facts) and \
        any(p["reason"] == "unreviewed_field_name" for p in pend)
    res["fact_ids_unique"] = len({f["fact_id"] for f in facts}) == len(facts)

    # A pinned target nested inside an opaque ancestor keeps its fact but the
    # context is null and the nesting is an anchored pending, not a guess.
    html = "<h2>返回数据</h2><pre><code>x</code></pre><table></table>"
    inner, outer = index_of(html, "code"), index_of(html, "table", 0)
    facts, pend = _facts(raw_soup(html), {"tables": [
        {"element_index": outer, "rows": []}], "code_blocks": [
        {"element_index": inner, "language": "", "text": "x"}]}, vocab)
    inner_ctx = [f for f in facts if f["kind"] == "label_context"
                 and f["element_index"] == inner]
    res["nested_target_pending"] = len(inner_ctx) == 1 and \
        inner_ctx[0]["element_kind"] == "code_block" and \
        inner_ctx[0]["preceding_label_key"] is None and \
        inner_ctx[0]["label_element_index"] is None and \
        any(p["reason"] == "target_inside_opaque" and
            p.get("element_index") == inner for p in pend)
    # A pinned index whose raw DOM tag does not match its kind is rejected.
    p_idx = index_of(html, "pre")
    valid_rows = [
        [{"kind": "th", "text": "字段名"},
         {"kind": "th", "text": "类型"}],
        [{"kind": "td", "text": "code"},
         {"kind": "td", "text": "String"}],
    ]
    try:
        _facts(raw_soup(html), {"tables": [
            {"element_index": p_idx, "rows": valid_rows}],
            "code_blocks": []}, vocab)
    except ProjError as exc:
        res["tag_mismatch_rejected"] = (
            str(exc) == "supplement_evidence: pinned target invalid")
    else:
        res["tag_mismatch_rejected"] = False
    assert res["tag_mismatch_rejected"]
    # A pinned index beyond the DOM is rejected by the same guard.
    try:
        _facts(raw_soup("<p>x</p>"), {"tables": [
            {"element_index": 99, "rows": []}]}, vocab)
    except ProjError as exc:
        res["missing_target_rejected"] = (
            str(exc) == "supplement_evidence: pinned target invalid")
    else:
        res["missing_target_rejected"] = False
    assert res["missing_target_rejected"]
    res["prose_pending"] = True  # set by run(); checked below via plan path
    # run() end-to-end on a synthetic archive
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        (root / "pages/domestic").mkdir(parents=True)
        (root / "derived/domestic").mkdir(parents=True)
        html = ("<h2>请求参数</h2><table><tr><th>字段名</th><th>类型</th></tr>"
                "<tr><td>accessToken</td><td>String</td></tr></table>"
                "<h2>返回数据</h2><pre>{\"code\":0}</pre><p>%s</p>" % canary)
        wrapper = json.dumps({"html_content": html},
                             ensure_ascii=False).encode("utf-8")
        (root / "pages/domestic/1.source.json").write_bytes(wrapper)
        t_idx, c_idx = index_of(html, "table"), index_of(html, "pre")
        structure = json.dumps({"tables": [
            dict(field_table, element_index=t_idx,
                 rows=field_table["rows"][:2])],
            "code_blocks": [{"element_index": c_idx, "language": "json",
                             "text": "{\"code\":0}"}]}).encode("utf-8")
        (root / "derived/domestic/1.structure.json").write_bytes(structure)
        source = {"source_id": "portal-2026-11-17:domestic:1",
                  "region": "domestic", "doc_id": "1",
                  "raw_file": "pages/domestic/1.source.json",
                  "raw_file_sha256": _sha256(wrapper),
                  "content_sha256": _sha256(html.encode("utf-8")),
                  "structure_file": "derived/domestic/1.structure.json",
                  "structure_sha256": _sha256(structure)}
        doc_record = {"region": "domestic", "doc_id": "1",
                      "raw_file": source["raw_file"],
                      "raw_file_sha256": source["raw_file_sha256"],
                      "content_sha256": source["content_sha256"],
                      "structure_file": source["structure_file"],
                      "structure_sha256": source["structure_sha256"]}
        docs_raw = (json.dumps(doc_record, ensure_ascii=False) +
                    "\n").encode("utf-8")
        (root / "documents.jsonl").write_bytes(docs_raw)
        vocab_path = root / "vocab.json"
        vocab_path.write_bytes(json.dumps(
            {"field_names": sorted(vocab)}).encode("utf-8"))
        plan = {"schema_version": 1, "task_id": "SP054",
                "baseline_commit": "0" * 40, "archive_root": str(root),
                "archive_documents_sha256": _sha256(docs_raw),
                "reviewed_vocabulary": {"path": str(vocab_path),
                                        "sha256": _sha256(
                                            vocab_path.read_bytes())},
                "sources": [source], "max_sources": 9, "max_nodes": 4096,
                "output_relative_path": OUT_REL}
        self_mod = sys.modules[__name__]
        plan_path = root / "plan.json"
        good = json.loads(json.dumps(plan))
        plan_path.write_bytes(json.dumps(good).encode("utf-8"))
        old = self_mod.PLAN_SHA
        self_mod.PLAN_SHA = _sha256(plan_path.read_bytes())
        try:
            out = root / "repo" / OUT_REL
            n_s, n_f, n_p = run(root / "repo", plan_path,
                                root / "repo" / OUT_REL)
            packet = json.loads(out.read_bytes())
            res["e2e_counts"] = (n_s, n_p >= 2) == (1, True) and n_f >= 3
            res["e2e_keys"] = set(packet) == {
                "schema_version", "input_pins", "sources", "facts",
                "pending", "receipt"}
            res["e2e_no_canary"] = canary not in json.dumps(
                packet, ensure_ascii=False)
            res["e2e_contexts"] = sorted(
                (f["element_kind"], f["element_index"]) for f in
                packet["facts"] if f["kind"] == "label_context") == \
                [("code_block", c_idx), ("table", t_idx)]
            res["e2e_pendings"] = any(
                p["reason"] == "prose_not_projected" for p in
                packet["pending"]) and any(
                p["reason"] == "code_content_not_projected" for p in
                packet["pending"])
            res["e2e_receipt"] = packet["receipt"]["full_source_review"] \
                is False and packet["receipt"]["protocol_freeze"] is False
            pins = packet["receipt"].get("code_sha256") or {}
            res["e2e_code_pins"] = set(pins) == set(CODE_PIN_FILES) and \
                all(_is_sha(v) for v in pins.values()) and \
                all(not os.path.isabs(k) for k in pins) and \
                str(root) not in json.dumps(packet)
            res["e2e_docs_verified"] = packet["input_pins"][
                "archive_documents_sha256"] == _sha256(docs_raw)
            bad_docs = (json.dumps(dict(doc_record, content_sha256="2" * 64),
                                   ensure_ascii=False) + "\n").encode("utf-8")
            (root / "bad_documents.jsonl").write_bytes(bad_docs)
            for name, bad in (
                    ("pin", dict(plan, sources=[dict(
                        source, content_sha256="1" * 64)])),
                    ("escape", dict(plan, sources=[dict(
                        source, raw_file="../escape.source.json")])),
                    ("dup", dict(plan, sources=[source, dict(
                        source, source_id="x")])),
                    ("budget", dict(plan, max_nodes=1)),
                    ("docspin", dict(plan,
                                     archive_documents_sha256="3" * 64)),
                    ("docidx", dict(plan,
                                    archive_documents_sha256=_sha256(bad_docs),
                                    sources=[dict(
                                        source,
                                        content_sha256="2" * 64)]))):
                bad_path = root / ("bad_%s.json" % name)
                bad_path.write_bytes(json.dumps(bad).encode("utf-8"))
                self_mod.PLAN_SHA = _sha256(bad_path.read_bytes())
                try:
                    run(root / "repo2", bad_path, root / "repo2" / OUT_REL)
                    res["reject_" + name] = False
                except ProjError as exc:
                    res["reject_" + name] = canary not in str(exc) and \
                        not (root / "repo2" / OUT_REL).exists()
            res["reject_plan"] = False
            try:
                run(root / "r", root / "nonexistent.json",
                    root / "r" / OUT_REL)
            except ProjError:
                res["reject_plan"] = True
            # A4: lexical mismatch, pre-existing output, and a symlinked
            # output parent escaping the repo are all rejected before write.
            res["reject_outarg"] = False
            try:
                run(root / "repo3", plan_path, root / "repo3" / "other.json")
            except ProjError:
                res["reject_outarg"] = not (
                    root / "repo3" / "other.json").exists()
            self_mod.PLAN_SHA = _sha256(plan_path.read_bytes())
            repo4 = root / "repo4"
            (repo4 / "data/coordination").mkdir(parents=True)
            (repo4 / OUT_REL).write_bytes(b"{}")
            res["reject_exists"] = False
            try:
                run(repo4, plan_path, repo4 / OUT_REL)
            except ProjError:
                res["reject_exists"] = True
            repo5 = root / "repo5"
            repo5.mkdir()
            outside = root / "elsewhere"
            outside.mkdir()
            os.symlink(str(outside), str(repo5 / "data"))
            res["reject_symlink_parent"] = False
            try:
                run(repo5, plan_path, repo5 / OUT_REL)
            except ProjError:
                res["reject_symlink_parent"] = not os.path.lexists(
                    outside / "coordination" / Path(OUT_REL).name)
            self_mod.PLAN_SHA = old
        finally:
            self_mod.PLAN_SHA = old
    failed = [k for k, v in res.items() if not v]
    for name in failed:
        sys.stderr.write("self-check failed: %s\n" % name)
    return len(res) - len(failed), len(res)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="supplement_evidence",
        description="SP054 bounded supplemental-evidence pilot.")
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
        n_s, n_f, n_p = run(args.repo, args.plan, args.out)
        sys.stdout.write("sources: %d facts: %d pending: %d\n"
                         % (n_s, n_f, n_p))
        return 0
    except ProjError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    except Exception:
        sys.stderr.write("supplement_evidence: unexpected failure\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
