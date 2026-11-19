#!/usr/bin/env python3
"""Offline checker for the frozen imaging source-reconciliation review.

Checks structural evidence -- manual assessments are reviewed separately --
against the frozen portal snapshot plus the repo-side review record, scope
register and historical registry; exits non-zero on the first mismatch.
Stdlib only -- no network, subprocesses, dynamic imports or live endpoints.

    .venv/bin/python docs/coordination/imaging-source-reconciliation/verify.py \
        --repo . --snapshot-dir <frozen-snapshot>
"""

import argparse
import hashlib
import json
import shlex
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

DOC_IDS = [str(n) for n in range(3682, 3690)]
SCOPE_PATH = "docs/coordination/imaging-source-reconciliation/scope.json"
REGISTER_PATH = "docs/capabilities/scope-register.json"
REVIEW_PATH = "docs/validation/2026-11-19-imaging-source-review/review.json"
MODULE = "src/ezviz_unified/imaging.py"
SOURCE_PREFIX = "portal-2026-11-17:domestic:"
BASE_URL = "https://open.ys7.com"
REF_FILES = {
    "contract": ("contracts/reviewed-transport-contracts.json", "contracts"),
    "binding": ("contracts/sdk-bindings.json", "bindings"),
    "wire_case": ("evals/contracts/cases.json", "cases"),
}
PERMISSION = {"GET": "GET", "PUT": "Config"}
PROSE_PERMISSION = {"GET": "设备级GET权限", "PUT": "设备级Config权限"}
READ_ONLY = {"GET": True, "PUT": False}
BODY_ENCODING = {"GET": "unknown", "PUT": "form_urlencoded"}
ASSESSMENT_KEYS = ("source_finding", "example_limit", "verification_boundary")
EVIDENCE_KEYS = (
    "historical_contract_review",
    "scoped_wire_acceptance",
    "most_recent_offline",
    "most_recent_offline_code_commit",
    "source_and_test_pins",
)
EVIDENCE_REFS = {
    "historical_contract_review": "docs/validation/reviews/S011A/contracts-review.json",
    "scoped_wire_acceptance": "docs/validation/sdk-product-acceptance.json",
    "most_recent_offline": "docs/validation/2026-11-19-domestic-image-configuration/offline.json",
}
PIN_FILES = (
    "src/ezviz_unified/imaging.py",
    "tests/test_sdk_imaging.py",
    "contracts/reviewed-transport-contracts.json",
    "evals/contracts/cases.json",
)
FAMILY = {
    "backlight": {"mode_values": ["close", "up", "down", "left", "right", "center"]},
    "denoising": {"mode_values": ["close", "general", "advanced"]},
    "exposure": {},
    "anti_flicker": {"mode_values": ["50Hz", "60Hz"]},
}
REQUIRED_BY_MODE = {"general": ["generalLevel"], "advanced": ["spatialLevel", "temporalLevel"]}


class Fail(Exception):
    """A named check failed; text carries only a field name and doc id."""

    def __init__(self, field, doc=None):
        super().__init__(field if doc is None else f"{field} doc={doc}")


def need(cond, field, doc=None):
    if not cond:
        raise Fail(field, doc)


def load_json(path, field, doc=None):
    try:
        return json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise Fail(field, doc)


def file_sha(path, doc):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        raise Fail("digests.read", doc)


def deref(repo, ref, kind, doc):
    """Resolve a stored '{file}#/{array}/{index}' pointer; no arbitrary reads."""
    want_file, want_array = REF_FILES[kind]
    name, sep, frag = str(ref).partition("#/")
    need(sep and name == want_file, kind + ".file", doc)
    array, _, idx = frag.partition("/")
    need(array == want_array and idx.isdigit(), kind + ".pointer", doc)
    entries = load_json(repo / name, kind + ".json", doc).get(want_array) or []
    need(int(idx) < len(entries), kind + ".index", doc)
    return entries[int(idx)]


def request_fields(rows, doc):
    """Project the fixed 5-cell request table into the declared field dicts."""
    need(isinstance(rows, list) and len(rows) > 1, "parameters.rows", doc)
    header = rows[0]
    need(
        isinstance(header, list)
        and len(header) == 5
        and all(isinstance(c, dict) and isinstance(c.get("text"), str) for c in header),
        "parameters.header",
        doc,
    )
    out = []
    for row in rows[1:]:
        need(
            isinstance(row, list) and len(row) == 5 and all(isinstance(c, dict) for c in row),
            "parameters.row",
            doc,
        )
        need(row[4].get("text") in ("Y", "N"), "parameters.required", doc)
        out.append(
            {
                "name": row[1]["text"],
                "location": row[0]["text"].lower(),
                "type": row[2]["text"],
                "required": row[4]["text"] == "Y",
            }
        )
    return out


def param_projection(params, doc):
    """Project contract.parameters entries into the same field dicts."""
    need(isinstance(params, list), "contract.parameters", doc)
    out = []
    for p in params:
        need(isinstance(p, dict) and type(p.get("required")) is bool, "contract.parameters", doc)
        out.append(
            {
                "name": p["name"],
                "location": str(p["location"]).lower(),
                "type": p["type"],
                "required": p["required"],
            }
        )
    return out


def response_fields(rows, doc):
    """Rebuild dotted name->type; plain names take the last meta/data root seen."""
    need(isinstance(rows, list) and len(rows) > 1, "response_fields.rows", doc)
    need(
        isinstance(rows[0], list)
        and len(rows[0]) >= 2
        and all(isinstance(c, dict) for c in rows[0]),
        "response_fields.header",
        doc,
    )
    out, prefix = {}, None
    for row in rows[1:]:
        need(
            isinstance(row, list) and len(row) >= 2 and all(isinstance(c, dict) for c in row),
            "response_fields.row",
            doc,
        )
        name = row[0]["text"]
        if not name:
            continue
        if name in ("meta", "data") and "." not in name:
            prefix = name
            key = name
        elif "." in name or prefix is None:
            key = name
        else:
            key = prefix + "." + name
        out[key] = row[1]["text"]
    return out


def error_codes(rows, doc):
    need(isinstance(rows, list) and len(rows) > 1, "errors.rows", doc)
    return [int(r[0]["text"]) for r in rows[1:]]


def curl_facts(text, doc):
    """Parse the archived curl template; header names only, never values."""
    flat = " ".join(ln.rstrip("\r\n").rstrip("\\") for ln in text.splitlines())
    try:
        toks = shlex.split(flat)
    except ValueError:
        raise Fail("sample.request_parse", doc)
    method, headers, query, form = None, [], [], {}
    i = 0
    while i < len(toks):
        tok = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if tok in ("--request", "-X"):
            method, i = nxt, i + 1
        elif tok.startswith("--request="):
            method = tok[10:]
        elif tok in ("--header", "-H"):
            headers.append(nxt.split(":", 1)[0].strip())
            i += 1
        elif tok.startswith("--header="):
            headers.append(tok[9:].split(":", 1)[0].strip())
        elif tok == "--data-urlencode":
            name, _, val = nxt.partition("=")
            form[name] = val
            i += 1
        elif tok.startswith("--data-urlencode="):
            name, _, val = tok[17:].partition("=")
            form[name] = val
        elif tok.startswith("http"):
            query += parse_qsl(urlsplit(tok).query, keep_blank_values=True)
        i += 1
    return method, headers, query, form


def check_sample(doc, page, got, method):
    sample = page["sample"]
    # Parse the archived JSON response example; compare declared facts only.
    raw = got["response_example"]["text"]
    start, end = raw.find("{"), raw.rfind("}")
    need(start >= 0 and end > start, "sample.response", doc)
    try:
        body = json.loads(raw[start : end + 1])
    except ValueError:
        raise Fail("sample.response", doc)
    need(isinstance(body, dict), "sample.response", doc)
    meta = body.get("meta")
    need(
        isinstance(meta, dict) and type(meta.get("code")) is int and meta["code"] == 200,
        "sample.code",
        doc,
    )
    need(
        "moreInfo" in meta and meta["moreInfo"] is None and sample["more_info_is_null"] is True,
        "sample.more_info",
        doc,
    )
    need(sorted(body) == sorted(sample["root_response_keys"]), "sample.root_keys", doc)
    data = body.get("data")
    if method == "GET":
        need(isinstance(data, dict), "sample.data", doc)
        have = sorted(data)
    else:
        need("data" not in body or data is None, "sample.data", doc)
        have = []
    need(have == sorted(sample["response_data_fields"]), "sample.data_fields", doc)
    cm, headers, query, form = curl_facts(got["request_example"]["text"], doc)
    need(cm == method, "sample.method", doc)
    url_tok = next(
        (
            t
            for t in shlex.split(
                " ".join(
                    ln.rstrip("\r\n").rstrip("\\")
                    for ln in got["request_example"]["text"].splitlines()
                )
            )
            if t.startswith("http")
        ),
        "",
    )
    parts = urlsplit(url_tok)
    need(
        parts.scheme == "https" and parts.netloc == "open.ys7.com" and parts.path == page["path"],
        "sample.url",
        doc,
    )
    need(sorted(headers) == sorted(sample["headers"]), "sample.headers", doc)
    need(sorted(k for k, _ in query) == sorted(sample["query"]), "sample.query", doc)
    need(sorted(form) == sorted(sample["form"]), "sample.form", doc)
    blank = {k for k, v in query if v == ""} | {k for k, v in form.items() if v == ""}
    need(blank == set(sample["blank_business_fields"]), "sample.blank_fields", doc)
    if "documented_sample_mode" in sample:
        want = sample["documented_sample_mode"]
        need(want == "close", "sample.mode", doc)
        if method == "PUT":
            got_mode = form.get("mode")
        else:
            got_mode = data.get("mode") if isinstance(data, dict) else None
        need(got_mode == want, "sample.mode", doc)


def check_constraints(page, op, doc, texts):
    cons = page["constraints"]
    need(isinstance(cons, dict), "constraints", doc)
    family = op.split(".")[2]
    want = FAMILY[family]
    # Verify declared constraint values BY KEY, and corroborate family terms in
    # the formal description text (not derived from the sample alone).
    desc = " ".join(texts)
    if family == "backlight":
        need(cons["mode_values"] == want["mode_values"], "constraints.mode", doc)
    elif family == "denoising":
        need(cons["mode_values"] == want["mode_values"], "constraints.mode", doc)
        need(cons["level_range_inclusive"] == [1, 100], "constraints.range", doc)
        need("100" in desc, "constraints.range_term", doc)
        if op.endswith(".set"):
            need(cons["required_by_mode"] == REQUIRED_BY_MODE, "constraints.required", doc)
    elif family == "exposure":
        need(cons["exposureTarget_range_inclusive"] == [1, 40000], "constraints.range", doc)
        need(cons["unit"] == "microseconds", "constraints.unit", doc)
        need("40000" in desc, "constraints.range_term", doc)
    elif family == "anti_flicker":
        need(cons["mode_values"] == want["mode_values"], "constraints.mode", doc)
    else:
        raise Fail("constraints.family", doc)
    for token in cons.get("mode_values") or []:
        need(token in desc, "constraints.mode_term", doc)


def check_artifacts(ctx, doc, spage, page, op, method, html_sha):
    refs_map = page["refs"]
    case_id = page["acceptance_case_id"]
    need(case_id == spage["case_id"], "page.case_id", doc)

    contract = deref(ctx["repo"], refs_map["contract"], "contract", doc)
    need(contract["operation_id"] == op, "contract.op", doc)
    need(str(contract["source_doc_id"]) == doc, "contract.doc_id", doc)
    need(contract["source_url"] == page["source_url"], "contract.url", doc)
    need(contract["source_content_sha256"] == html_sha, "contract.source_sha", doc)
    need(contract["region"] == "domestic", "contract.region", doc)
    need(contract["method"] == method, "contract.method", doc)
    need(contract["path"] == page["path"], "contract.path", doc)
    need(contract["token_location"] == "header", "contract.token_location", doc)
    need(contract["read_only"] is READ_ONLY[method], "contract.read_only", doc)
    need(contract["body_encoding"] == BODY_ENCODING[method], "contract.body_encoding", doc)
    success = contract["success"]
    need(
        success["code_path"] == "meta.code"
        and success["values"] == [200]
        and success["message_path"] == "meta.message"
        and success["data_path"] == "data",
        "contract.success",
        doc,
    )
    # The contract's own parameter projection must match the page's field list.
    need(
        param_projection(contract["parameters"], doc) == page["request_fields"],
        "contract.parameters",
        doc,
    )

    binding = deref(ctx["repo"], refs_map["binding"], "binding", doc)
    need(binding["operation_id"] == op, "binding.op", doc)
    need(str(binding["source_doc_id"]) == doc, "binding.doc_id", doc)
    need(binding["region"] == "domestic", "binding.region", doc)
    need(binding["module"] == MODULE, "binding.module", doc)
    need(sorted(binding["symbols"]) == sorted(page["symbols"]), "binding.symbols", doc)
    need(binding["public_entry"] == page["sdk_entry"], "binding.entry", doc)
    need(binding.get("argument_constants", {}) == page["argument_constants"], "binding.args", doc)
    need(binding["offline_status"] == "passed", "binding.offline", doc)
    need(binding["live_status"] == "not_verified", "binding.live", doc)

    case = deref(ctx["repo"], refs_map["wire_case"], "wire_case", doc)
    need(case["operation_id"] == op, "wire.op", doc)
    need(
        isinstance(case.get("args"), list)
        and isinstance(case.get("kwargs"), dict)
        and isinstance(case.get("fields"), dict),
        "wire.shape",
        doc,
    )

    # Acceptance case is a separate record looked up by case_id.
    acc = [c for c in ctx["acceptance"]["cases"] if c.get("case_id") == case_id]
    need(len(acc) == 1, "acceptance.case", doc)
    acc = acc[0]
    need(acc["result"] == "recorded_evidence", "acceptance.result", doc)
    need(acc["execution_outcome"] == "passed", "acceptance.outcome", doc)
    need(acc["operation_ids"] == [op], "acceptance.ops", doc)
    modes = acc["wire_eval_scope"]["modes"]
    need("sync" in modes and "async" in modes, "acceptance.modes", doc)
    for ref in (refs_map["contract"], refs_map["binding"], refs_map["wire_case"]):
        need(
            ref in (acc["source_refs"] + acc["code_artifact_evidence_refs"]), "acceptance.refs", doc
        )


def check_register(ctx, doc, page, op, method, html_sha):
    reg, record = ctx["register"], ctx["review_file"]
    sref, cid = SOURCE_PREFIX + doc, "contract:" + op
    srevs = [r for r in reg["source_reviews"] if r.get("source_ref") == sref]
    need(len(srevs) == 1, "register.source", doc)
    srev = srevs[0]
    need(srev["review_state"] == "reviewed", "register.source_state", doc)
    need(srev["content_sha256"] == html_sha, "register.source_sha", doc)
    need(srev["review_record_ref"] == record, "register.source_record", doc)
    need(srev["resolution_refs"] == [cid], "register.source_resolution", doc)
    refs = page["observation_refs"]
    need(refs == [sref + ":declaration:0", sref + ":example:0"], "page.observation_refs", doc)
    obs = [o for o in reg["observation_reviews"] if o.get("source_ref") == sref]
    need(
        len(obs) == 2 and {o["observation_ref"] for o in obs} == set(refs),
        "register.observations",
        doc,
    )
    want_ptr = record + "#/pages/" + doc
    for o in obs:
        need(o["review_state"] == "reviewed", "register.observation_state", doc)
        need(o["review_record_ref"] == record, "register.observation_record", doc)
        need(o["resolution_refs"] == [want_ptr], "register.observation_resolution", doc)
        need(o["capability_refs"] == [cid], "register.observation_capability", doc)
        need(o["observation_ref"].split(":")[-2] == o["kind"], "register.observation_kind", doc)
    caps = [c for c in reg["capabilities"] if c.get("capability_id") == cid]
    need(len(caps) == 1, "register.capability", doc)
    cap = caps[0]
    need(cap["review"] == "ratified", "register.capability_review", doc)
    need(set(refs) <= set(cap["observation_refs"]), "register.capability_obs", doc)
    need(page["acceptance_case_id"] in cap["acceptance_case_refs"], "register.capability_case", doc)
    srcs = cap["source_refs"]
    need(
        any(s.get("source_id") == sref and s.get("content_sha256") == html_sha for s in srcs),
        "register.capability_source",
        doc,
    )
    impl = cap["implementation"]
    need(impl["binding_record_present"] is True, "register.capability_binding", doc)
    need(impl["live_checked_this_round"] is False, "register.capability_live", doc)

    hist = [s for s in ctx["s011a"]["sources"] if str(s.get("doc_id")) == doc]
    need(
        len(hist) == 1
        and hist[0]["content_sha256"] == html_sha
        and hist[0]["raw_file_sha256"] == page["digests"]["source_file_sha256"],
        "historical.source_sha",
        doc,
    )


def check_page(ctx, doc):
    spage = ctx["scope"]["pages"].get(doc)
    page = ctx["review"]["pages"].get(doc)
    need(isinstance(spage, dict), "scope.page", doc)
    need(isinstance(page, dict), "review.page", doc)
    op = spage["operation_id"]
    need(page["operation_id"] == op, "operation_id", doc)
    need(page["source_url"] == spage["source_url"], "source_url", doc)
    need(isinstance(page["title"], str) and page["title"], "title", doc)
    method = page["method"]
    need(method in PERMISSION, "method", doc)

    src = ctx["snap"] / "pages" / "domestic" / (doc + ".source.json")
    stc = ctx["snap"] / "derived" / "domestic" / (doc + ".structure.json")
    dig = page["digests"]
    need(file_sha(src, doc) == dig["source_file_sha256"], "digests.source_file_sha256", doc)
    need(file_sha(stc, doc) == dig["structure_sha256"], "digests.structure_sha256", doc)
    source = load_json(src, "source", doc)
    need(str(source["doc_id"]) == doc, "source.doc_id", doc)
    need(source["title"] == page["title"], "source.title", doc)
    need(source["platform"] == "doc_open", "source.platform", doc)
    html = source["html_content"]
    need(isinstance(html, str) and html, "source.html_content", doc)
    html_sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
    need(html_sha == dig["html_sha256"], "digests.html_sha256", doc)

    struct = load_json(stc, "structure", doc)
    text = struct["text"]
    need(isinstance(text, str), "structure.text", doc)
    need(
        struct["links"] == [] and struct["images"] == [] and struct["embedded_resources"] == [],
        "structure.embedded",
        doc,
    )
    headings = struct["headings"]
    tables = struct["tables"]
    code_blocks = struct["code_blocks"]
    need(len(headings) == 1 and len(tables) == 3 and len(code_blocks) == 4, "structure.counts", doc)

    # Positional mapping: one heading, three tables, four code blocks.
    got = {
        "heading": headings[0],
        "address": code_blocks[0],
        "method": code_blocks[1],
        "request_example": code_blocks[2],
        "response_example": code_blocks[3],
        "parameters": tables[0],
        "response_fields": tables[1],
        "errors": tables[2],
    }
    ptrs = page["elements"]
    for key, el in got.items():
        need(el["element_index"] == ptrs[key], "elements." + key, doc)
    need(headings[0]["text"] == page["title"], "elements.heading", doc)
    need(got["address"]["text"].strip() == BASE_URL + page["path"], "elements.address", doc)
    need(got["method"]["text"].strip() == method, "elements.method", doc)

    # Function/prose section before 请求地址 carries capability/permission terms.
    flat = "".join(text.split())
    cut = flat.find("请求地址")
    need(cut > 0, "prose.anchor", doc)
    prose = flat[:cut]
    need("support_image_param" in prose, "prose.capability", doc)
    need("托管/子账号" in prose or "托管" in prose and "子账号" in prose, "prose.roles", doc)
    need(PROSE_PERMISSION[method] in prose, "prose.permission", doc)
    need(page["capability"] == "support_image_param", "page.capability", doc)
    need(page["permission"] == PERMISSION[method], "page.permission", doc)
    need(page["managed_and_subaccounts"] is True, "page.roles", doc)

    # Fixed-cell table projections must match the declared field lists exactly.
    have_fields = request_fields(got["parameters"]["rows"], doc)
    need(have_fields == page["request_fields"], "request_fields", doc)
    mapping = response_fields(got["response_fields"]["rows"], doc)
    need(mapping == page["response_fields"], "response_fields", doc)
    need(error_codes(got["errors"]["rows"], doc) == page["error_codes"], "error_codes", doc)

    # Family terms must also be corroborated in formal description text.
    desc_texts = [
        r[3]["text"]
        for r in got["parameters"]["rows"][1:]
        if len(r) >= 4 and isinstance(r[3], dict)
    ]
    desc_texts += [
        r[2]["text"]
        for r in got["response_fields"]["rows"][1:]
        if len(r) >= 3 and isinstance(r[2], dict)
    ]
    check_constraints(page, op, doc, desc_texts + [prose])
    check_sample(doc, page, got, method)
    assessment = page["assessment"]
    need(
        sorted(assessment) == sorted(ASSESSMENT_KEYS)
        and all(isinstance(assessment[k], str) and assessment[k] for k in ASSESSMENT_KEYS),
        "assessment",
        doc,
    )
    need(page["offline_status"] == "passed", "page.offline", doc)
    need(page["live_status"] == "not_verified", "page.live", doc)
    check_artifacts(ctx, doc, spage, page, op, method, html_sha)
    check_register(ctx, doc, page, op, method, html_sha)


def main():
    ap = argparse.ArgumentParser(description="Verify the frozen imaging source review record.")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--review-file")
    args = ap.parse_args()
    repo = Path(args.repo)
    try:
        scope = load_json(repo / SCOPE_PATH, "scope")
        need(scope.get("schema_version") == 1, "scope.schema_version")
        need(scope.get("review_file") == REVIEW_PATH, "scope.review_file")
        record = scope["review_file"]
        review = load_json(Path(args.review_file) if args.review_file else repo / record, "review")
        need(review.get("schema_version") == 1, "review.schema_version")
        need(
            review.get("baseline_commit") == scope.get("baseline_commit"), "review.baseline_commit"
        )
        need(review.get("snapshot") == scope.get("snapshot"), "review.snapshot")
        need(review.get("scope_ref") == SCOPE_PATH, "review.scope_ref")
        need(review.get("live_executed") is False, "review.live_executed")
        need(set(scope.get("pages") or {}) == set(DOC_IDS), "scope.pages")
        need(set(review.get("pages") or {}) == set(DOC_IDS), "review.pages")
        shared = review.get("shared_limits")
        need(
            isinstance(shared, list)
            and len(shared) == 4
            and all(isinstance(s, str) and s for s in shared),
            "review.shared_limits",
        )

        ev = review.get("evidence_refs")
        need(
            isinstance(ev, dict) and sorted(ev) == sorted(EVIDENCE_KEYS),
            "review.evidence_refs",
            None,
        )
        for k, want_path in EVIDENCE_REFS.items():
            need(ev[k] == want_path, "review.evidence_refs", None)
        pins = ev["source_and_test_pins"]
        need(
            isinstance(pins, dict) and sorted(pins) == sorted(PIN_FILES),
            "review.evidence_refs",
            None,
        )

        ctx = {
            "repo": repo,
            "snap": Path(args.snapshot_dir),
            "scope": scope,
            "review_file": record,
            "review": review,
            "register": load_json(repo / REGISTER_PATH, "register"),
            "s011a": load_json(repo / ev["historical_contract_review"], "historical"),
            "acceptance": load_json(repo / ev["scoped_wire_acceptance"], "acceptance"),
        }
        # Semantic checks first -- a corrected/resealed hash must not mask faults.
        for doc in DOC_IDS:
            check_page(ctx, doc)

        # Receipt pins: code_commit and per-file sha256 come from that receipt.
        offline = load_json(repo / ev["most_recent_offline"], "offline")
        need(
            ev["most_recent_offline_code_commit"] == offline.get("code_commit"),
            "evidence.code_commit",
            None,
        )
        receipt_pins = offline.get("source_test_sha256") or {}
        for f in PIN_FILES:
            need(pins[f] == receipt_pins.get(f), "evidence.pins", None)
        inp = (ctx["register"].get("inputs") or {}).get("imaging_source_reconciliation") or {}
        need(inp.get("path") == record, "register.input_path", None)
        review_path = Path(args.review_file) if args.review_file else repo / record
        need(file_sha(review_path, None) == inp.get("sha256"), "register.input_sha", None)
    except Fail as exc:
        print("verify: FAIL " + str(exc), file=sys.stderr)
        return 1
    except (KeyError, TypeError, IndexError, OSError, ValueError):
        print("verify: FAIL malformed", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "reviewed_sources": 8,
                "reviewed_observations": 16,
                "reused_offline_cases": 8,
                "live_checks": 0,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
