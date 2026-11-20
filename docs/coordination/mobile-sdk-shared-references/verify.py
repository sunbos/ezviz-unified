#!/usr/bin/env python3
"""Verify the frozen mobile SDK documentation review without contacting external services."""

import argparse
import json
import re
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path

SCOPE_PATH = "docs/coordination/mobile-sdk-shared-references/scope.json"
REGISTER_PATH = "docs/capabilities/scope-register.json"
ACCEPTANCE_PATH = "docs/validation/sdk-product-acceptance.json"
REVIEW_PATH = "docs/validation/2026-11-21-mobile-sdk-shared-references/review.json"
GUIDE_PATH = "docs/official-mobile-sdk.md"
BASELINE = "4e08ccca3067e27b9ac63e8e29a392a80a667d73"
REVISION = "2026-11-21-r58-mobile-sdk-shared-references"
PREFIX = "portal-2026-11-17:domestic:"
HELP = "https://open.ys7.com/help/"
REDIRECTS = {
    "5240": ("iOS SDK", "4148"),
    "5241": ("Harmony SDK", "4148"),
    "5242": ("iOS SDK", "37"),
    "5243": ("Harmony SDK", "37"),
    "5244": ("iOS SDK", "4146"),
    "5245": ("Harmony SDK", "4146"),
}
TARGETS = {
    "4148": "official_demo_guide",
    "37": "native_error_reference",
    "4146": "native_troubleshooting_reference",
}
DOCS = tuple(REDIRECTS) + tuple(TARGETS)
GATE = "ACC-MEDIA-OFFICIAL-INTEGRATION-001"
CRITERION = "OTHER-COMPONENT-SCOPES"


class Fail(Exception):
    """A named check failed; text carries only a field name and doc id."""

    def __init__(self, field, doc=None):
        super().__init__(field if doc is None else f"{field} doc={doc}")


def need(cond, field, doc=None):
    if not cond:
        raise Fail(field, doc)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.element_index = 0

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend((self.element_index, v) for k, v in attrs if k == "href")
        self.element_index += 1


def load_archive(snap):
    return {
        "catalog": json.loads((snap / "catalog.json").read_bytes()),
        "menus": json.loads((snap / "menu-memberships.json").read_bytes()),
        "edges": json.loads((snap / "document-reference-edges.json").read_bytes()),
        "raw": {d: (snap / "pages/domestic" / (d + ".source.json")).read_bytes() for d in DOCS},
        "derived": {
            d: (snap / "derived/domestic" / (d + ".structure.json")).read_bytes() for d in DOCS
        },
    }


def check_archive(state):
    scope, arc = state["scope"], state["archive"]
    pages = scope["pages"]
    need(set(pages) == set(DOCS), "scope.pages")

    def local(rows):
        return [r for r in rows if r.get("region") == "domestic" and r.get("doc_id") in DOCS]

    structures = {}
    for doc in DOCS:
        page = pages[doc]
        rows = [r for r in local(arc["catalog"]) if r["doc_id"] == doc]
        mems = [m for m in local(arc["menus"]) if m["doc_id"] == doc]
        need(len(rows) == 1, "catalog.row", doc)
        need(len(mems) == 1, "menus.row", doc)
        row, mem = rows[0], mems[0]
        raw = arc["raw"][doc]
        digest = sha256(raw).hexdigest()
        need(digest == page["raw_file_sha256"], "raw_file_sha256", doc)
        need(digest == row["raw_file_sha256"], "catalog.raw_file_sha256", doc)
        src = json.loads(raw)
        need(src.get("doc_id") == doc, "raw.doc_id", doc)
        h = sha256(src["html_content"].encode()).hexdigest()
        need(h == page["html_sha256"], "html_sha256", doc)
        need(h == row["content_sha256"], "content_sha256", doc)
        need(row.get("status") == "archived", "status", doc)
        need(row["source_url"] == page["source_url"] == HELP + doc, "source_url", doc)
        need(
            row["raw_file"] == page["raw_file"] == f"pages/domestic/{doc}.source.json",
            "raw_file",
            doc,
        )
        need(
            page["structure_file"] == f"derived/domestic/{doc}.structure.json",
            "structure_file",
            doc,
        )
        need(page["source_ref"] == PREFIX + doc, "source_ref", doc)
        derived = arc["derived"][doc]
        need(sha256(derived).hexdigest() == page["structure_sha256"], "structure_sha256", doc)
        struct = structures[doc] = json.loads(derived)
        need(page["menu_path"] == mem["category_path"], "menu_path", doc)
        platform = REDIRECTS[doc][0] if doc in REDIRECTS else "Android SDK"
        need(page["menu_path"][1:2] == [platform], "menu_path.platform", doc)
        need(row.get("memberships") == [mem], "memberships", doc)
        if doc in REDIRECTS:
            target = REDIRECTS[doc][1]
            need(page["target"] == target, "target", doc)
            need(page["kind"] == "document_redirect", "kind", doc)
            need(
                type(page.get("link_element")) is int and page["link_element"] == 1,
                "link_element",
                doc,
            )
            links = Links()
            links.feed(src["html_content"])
            links.close()
            need(links.hrefs == [(1, HELP + target)], "hrefs", doc)
            need(
                struct.get("links")
                == [{"url": HELP + target, "element_index": 1, "text": "点击跳转"}],
                "links",
                doc,
            )
            out = [
                e
                for e in arc["edges"]
                if e.get("from_region") == "domestic" and e.get("from_doc_id") == doc
            ]
            need(
                len(out) == 1
                and out[0].get("to_region") == "domestic"
                and out[0].get("to_doc_id") == target,
                "edges",
                doc,
            )
        elif doc in TARGETS:
            need(page["kind"] == TARGETS[doc], "kind", doc)
    return structures


def rows_at(structure, index):
    hits = [t for t in structure.get("tables") or [] if t.get("element_index") == index]
    need(len(hits) == 1, "table.E%d" % index)
    return [[str(c.get("text", "")) for c in row] for row in hits[0].get("rows") or []]


def _links_to(structure, doc):
    return sorted(link["element_index"] for link in structure["links"] if link["url"] == HELP + doc)


def check_facts(state, structures):
    facts, reqs = state["review"]["facts"], state["scope"]["fact_requirements"]
    need(set(facts) == set(FACT_PINS), "facts.topics")
    for topic, digest in FACT_PINS.items():
        need(
            set(reqs.get(topic) or ()) == set(facts.get(topic) or {}),
            "facts." + topic + ".keys",
        )
        encoded = json.dumps(facts[topic], sort_keys=True).encode("utf-8")
        need(sha256(encoded).hexdigest() == digest, "facts." + topic)
    demo, errs, faq = structures["4148"], structures["37"], structures["4146"]
    dt = demo.get("text") or ""
    need(
        rows_at(demo, 8)[1:]
        == [
            ["Android", "不区分，登录页选择服务器区域即可", "官网", "官网", ".apk"],
            ["iOS", "区分国内海外", "官网", "涉及证书，由开发者使用自己公司证书进行打包", ".ipa"],
            ["Harmony", "仅国内版本", "官网", "涉及证书，由开发者使用自己公司证书进行打包", ".hap"],
        ],
        "demo.E8.rows",
    )
    need("Android/iOS/Harmony 三端均不区分国内/海外版本" in dt, "demo.sdk_decl")
    need(
        [r[0] for r in rows_at(demo, 47)[1:] if len(r) > 1 and r[1] == "是"]
        == facts["demo"]["login_fields"],
        "demo.E47.required",
    )
    r90 = rows_at(demo, 90)
    need(r90 and r90[0][:3] == ["区域", "ApiUrl", "AuthUrl"] and len(r90) > 1, "demo.E90")
    need("EZOpenSDK.getDeviceList" in dt, "demo.getDeviceList")
    hd = {x.get("element_index"): x.get("text", "") for x in demo.get("headings") or []}
    for i, p in ((126, "Android"), (134, "iOS"), (142, "Harmony")):
        need(p in hd.get(i, ""), "demo.h%d" % i)
    need("Android" in hd.get(151, "") and "日志获取" in hd.get(151, ""), "demo.h151")
    need("iOS" in hd.get(160, "") and "日志获取" in hd.get(160, ""), "demo.h160")
    et = errs.get("text") or ""
    need("Android、iOS、Harmony、Mac" in et and "iOS 负数错误码" in et, "errors.text")
    r5 = rows_at(errs, 5)
    need(r5 and r5[0][:3] == ["错误码", "释义", "原因或解决方案"], "errors.E5.header")
    body = r5[1:]
    need(len(body) == 819 and all(len(r) >= 3 for r in body), "errors.E5.shape")
    codes = [r[0].strip() for r in body]
    need(len(set(codes)) == 819, "errors.E5.distinct")
    need(
        all(
            c.isascii() and c.isdigit() and int(c) > 0 and r[1].strip() for c, r in zip(codes, body)
        ),
        "errors.E5.cells",
    )
    meanings = dict(zip(codes, (r[1].strip() for r in body)))
    need(
        {k: meanings.get(k) for k in ("110013", "340402")}
        == facts["errors"]["numeric_lookup_examples"],
        "errors.E5.lookup",
    )
    ft = faq.get("text") or ""
    hf = {x.get("element_index"): x.get("text", "") for x in faq.get("headings") or []}
    for i, s in (
        (1, "预览"),
        (15, "回放"),
        (23, "对讲"),
        (32, "配网"),
        (42, "tkToken"),
        (53, "反馈"),
    ):
        need(s in hf.get(i, ""), "faq.h%d" % i)
    need(all(m in ft for m in facts["faq"]["native_methods"]), "faq.native_methods")
    need(
        "创建工单" in ft
        and "功能使用的是哪种类型的tkToken" in ft
        and "通道" in ft
        and "android、iOS、Harmony" in ft,
        "faq.text",
    )
    need(_links_to(faq, "37") == [5, 22, 27, 35, 57], "faq.links.37")
    need(_links_to(faq, "4148") == [73], "faq.links.4148")


# Frozen canonical JSON digests; independent source-level semantic checks follow.
FACT_PINS = {
    "demo": "3fe6008098aaab936190447564d74a02c31c985b3eb69e0218a4e9a0de2749ad",
    "errors": "c258502cce243bfe91618a65db50bcb09acec84f81e43151e0b63b49292d54af",
    "faq": "1e7f319d933706742c71be32d68e10a97145358d99f426cfff101e20db3200b0",
}


def check_records(state):
    scope, review, reg = state["scope"], state["review"], state["registry"]
    need(scope.get("schema_version") == 1, "scope.schema")
    need(scope.get("baseline_commit") == BASELINE, "scope.baseline")
    need(scope.get("scope_revision") == REVISION, "scope.revision")
    need(scope.get("review") == REVIEW_PATH, "scope.review_path")
    need(scope.get("guide") == GUIDE_PATH, "scope.guide_path")
    need(review.get("schema_version") == 1, "review.schema")
    need(
        review.get("scope_ref") == SCOPE_PATH and review.get("guide_ref") == GUIDE_PATH,
        "review.refs",
    )
    pin = (reg.get("inputs") or {}).get("mobile_sdk_shared_references_review") or {}
    need(
        pin.get("path") == REVIEW_PATH
        and pin.get("schema") == 1
        and pin.get("sha256") == state["review_sha"],
        "register.pin",
    )
    rows = review["sources"]
    need(set(rows) == set(DOCS), "review.sources")
    rt = review.get("runtime_evidence") or {}
    rk = (
        "native_builds",
        "native_runs",
        "device_requests",
        "logs_collected",
        "uploads",
        "python_operations_added",
    )
    need(
        set(rt) == set(rk) and all(type(rt[k]) is int and rt[k] == 0 for k in rk), "review.runtime"
    )
    indices = {
        r["doc_id"]: i
        for i, r in enumerate(state["archive"]["catalog"])
        if r["region"] == "domestic" and r["doc_id"] in DOCS
    }
    srows = reg.get("source_reviews") or []
    known = {
        "37": {2, 3, 4, 5, 205, 1080},
        "4148": {8, 47, 90, 126, 134, 142, 151, 160},
        "4146": {1, 15, 23, 32, 42, 53, 73},
    }
    want = {"4148": ("版本", "签名", "getDeviceList"), "4146": ("预览", "回放", "tkToken", "版本")}
    for doc in DOCS:
        row = rows.get(doc) or {}
        page = (scope.get("pages") or {}).get(doc) or {}
        for k in ("source_ref", "kind", "menu_path", "html_sha256"):
            need(row.get(k) == page.get(k), "source." + k, doc)
        need(
            row.get("catalog_ref") == f"portal-2026-11-17/catalog.json#record/{indices[doc]}",
            "source.catalog_ref",
            doc,
        )
        sr = [r for r in srows if r.get("source_ref") == row.get("source_ref")]
        need(len(sr) == 1, "register.unique", doc)
        sr = sr[0]
        need(sr.get("content_sha256") == page.get("html_sha256"), "register.content", doc)
        need(
            sr.get("owner_queue") == "batch_3_A" and sr.get("requirement") == "required_review",
            "register.queue",
            doc,
        )
        need(
            sr.get("review_record_ref") == REVIEW_PATH
            and sr.get("resolution_refs") == [f"{REVIEW_PATH}#/sources/{doc}"],
            "register.refs",
            doc,
        )
        rem = row.get("remaining")
        need(sr.get("unknowns") == rem, "register.unknowns", doc)
        need(
            type(row.get("assessment")) is str and row["assessment"].strip(),
            "source.assessment",
            doc,
        )
        need(
            isinstance(rem, list) and all(type(s) is str and s.strip() for s in rem),
            "source.remaining",
            doc,
        )
        el = row.get("elements")
        ok = isinstance(el, list) and bool(el) and all(type(e) is int and e >= 0 for e in el)
        need(ok and len(set(el)) == len(el), "source.elements", doc)
        if doc in REDIRECTS:
            need(
                el == [1]
                and row.get("target") == REDIRECTS[doc][1]
                and type(row.get("link_element")) is int
                and row["link_element"] == 1,
                "source.alias",
                doc,
            )
        if doc in known:
            need(ok and known[doc].issubset(el), "source.known", doc)
        exp = "in_review" if doc in want else "reviewed"
        need(page.get("final_review_state") == exp, "page.state", doc)
        need(row.get("review_state") == exp, "review.state", doc)
        need(sr.get("review_state") == exp, "register.state", doc)
        if doc in want:
            txt = "\n".join(s for s in rem if type(s) is str) if isinstance(rem, list) else ""
            for k in want[doc]:
                need(k in txt, "review.remaining", doc)
        else:
            need(not rem, "review.remaining", doc)
    gate = [c for c in state["cases"] if c.get("case_id") == GATE]
    need(len(gate) == 1, "gate.unique")
    ca = (gate[0] if gate else {}).get("criterion_assessment") or {}
    need(CRITERION in ca, "gate.criterion")
    if reg.get("scope_revision") == REVISION and gate:
        need(gate[0].get("result") == "awaiting_design", "gate.result")
        need(ca.get(CRITERION) == "pending", "gate.criterion")
    g = state["guide"]
    need(f"({GUIDE_PATH})" in state["readme"], "readme.guide")
    for t in TARGETS:
        need(f"({HELP}{t})" in g, "guide.target", t)
    need(f"({'/'.join(REVIEW_PATH.split('/')[-3:])})" in g, "guide.review")
    for k in (
        "Android",
        "iOS",
        "Harmony",
        "APK",
        "IPA",
        "HAP",
        "110013",
        "340402",
        "AppKey",
        "AccessToken",
        "getDeviceList",
        "setPlayVerifyCode",
        "setPlaybackRate",
        "tkToken",
        "工单",
        "脱敏",
    ):
        need(k in g, "guide.keyword")


NATIVE_INTAKE = "docs/coordination/mobile-sdk-shared-references/native-symbol-intake.json"
NATIVE_FREEZE = "dc09630562a00ccd4ab3df8ab2f1a24ac1ac2414"
NATIVE_PIN = "e4efd71519184265a43412de51476117d26904110011a1dc11ed1f8f7a1eaafd"
NATIVE_SITES = {
    ("37", 2899): "setAccessToken",
    ("37", 1067): "setPlayVerifyCode",
    ("37", 929): "setServerUrl",
    ("37", 1071): "setSurfaceHold",
    ("4146", 17): "setPlaybackRate",
    ("4146", 19): "seekPlayback",
    ("4146", 7): "setPlayVerifyCode",
    ("4148", 82): "getDeviceList",
}


class DirectText(HTMLParser):
    """Collect direct character data of wanted zero-based start-tag indices."""

    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, wanted):
        super().__init__(convert_charrefs=True)
        self.wanted = set(wanted)
        self.stack = []
        self.index = 0
        self.parts = {}

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append((tag, self.index))
        self.index += 1

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        for pos in range(len(self.stack) - 1, -1, -1):
            if self.stack[pos][0] == tag:
                del self.stack[pos:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][1] in self.wanted:
            self.parts.setdefault(self.stack[-1][1], []).append(data)


def check_native_observations(state):
    intake = state["native"]
    record = state["registry"]["inputs"].get("native_symbol_intake") or {}
    need(state["native_sha"] == NATIVE_PIN == record.get("sha256"), "native.sha")
    need(
        record.get("path") == NATIVE_INTAKE
        and type(record.get("schema")) is int
        and record["schema"] == 1,
        "native.registry",
    )
    need(intake.get("schema_version") == 1, "native.schema_version")
    need(intake.get("input_commit") == NATIVE_FREEZE, "native.input_commit")
    need(intake.get("scope_ref") == SCOPE_PATH, "native.scope_ref")
    observations = intake.get("observations")
    expected = {f"manual:domestic:{d}:e{i}": s for (d, i), s in NATIVE_SITES.items()}
    need(
        isinstance(observations, dict) and set(observations) == set(expected), "native.observations"
    )
    docs = {}
    for (doc, index), symbol in NATIVE_SITES.items():
        docs.setdefault(doc, {})[index] = symbol
    grouped = {}
    for doc in sorted(docs):
        raw = state["archive"]["raw"].get(doc)
        need(bool(raw), "native.raw", doc)
        parser = DirectText(docs[doc])
        parser.feed(json.loads(raw)["html_content"])
        parser.close()
        page_sha = (state["scope"]["pages"].get(doc) or {}).get("html_sha256")
        for index, symbol in docs[doc].items():
            key = f"manual:domestic:{doc}:e{index}"
            obs = observations[key]
            candidate = "candidate:domestic:native-ref:" + symbol
            need(obs.get("source_ref") == PREFIX + doc, "native.source_ref", doc)
            need(obs.get("html_sha256") == page_sha, "native.html_sha256", doc)
            need(
                type(obs.get("element_index")) is int and obs.get("element_index") == index,
                "native.element",
                doc,
            )
            need(obs.get("symbol") == symbol, "native.symbol", doc)
            need(obs.get("candidate_id") == candidate, "native.candidate", doc)
            need(isinstance(obs.get("role"), str) and bool(obs["role"].strip()), "native.role", doc)
            text = " ".join(parser.parts.get(index, []))
            need(
                re.search(r"(?<![A-Za-z0-9_])" + re.escape(symbol) + r"(?![A-Za-z0-9_])", text)
                is not None,
                "native.text",
                doc,
            )
            grouped.setdefault(candidate, []).append(key)
    return {candidate: sorted(keys) for candidate, keys in sorted(grouped.items())}


def check_native(state):
    grouped = check_native_observations(state)
    cands = state["native"].get("candidates", {})
    need(set(cands) == set(grouped), "native.candidates")
    for cid, c in cands.items():
        need(c.get("symbol") == cid.rsplit(":", 1)[-1], "native.symbol")
        refs = c.get("observation_refs") or []
        need(
            len(refs) == len(set(refs)) and sorted(refs) == sorted(grouped[cid]),
            "native.observation_refs",
        )
        need(isinstance(c.get("scenario"), str) and c["scenario"].strip() != "", "native.scenario")
        unk = c.get("unknowns")
        need(
            isinstance(unk, list)
            and len(unk) > 0
            and all(isinstance(u, str) and u.strip() for u in unk),
            "native.unknowns",
        )
    if state["registry"]["scope_revision"] != REVISION:
        return  # later revisions may admit candidates independently
    rows = [
        r
        for r in state["registry"].get("capabilities", [])
        if (r.get("origin_record_ref") or {}).get("input") == "native_symbol_intake"
    ]
    need(sorted(str(r.get("capability_id")) for r in rows) == sorted(cands), "native.projection")
    fixed = {
        "nature": "integration",
        "capability_kind": "native_symbol_review_candidate",
        "requirement": "pending_scope_review",
        "review": "candidate",
        "disposition": "needs_review",
    }
    for r in rows:
        cid = r.get("capability_id")
        c = cands.get(cid, {})
        need((r.get("origin_record_ref") or {}).get("entity_id") == cid, "native.entity_id")
        need(
            all(r.get(k) == v for k, v in fixed.items())
            and r.get("region_scope") == "domestic"
            and r.get("scenario") == c["scenario"]
            and r.get("gaps") == c["unknowns"],
            "native.fields",
        )
        need(r.get("protocol_ref") is None, "native.protocol_ref")
        refs = c.get("observation_refs") or []
        need(sorted(r.get("observation_refs") or []) == sorted(refs), "native.row_observation_refs")
        obs = [state["native"]["observations"][k] for k in refs]
        want = sorted(
            (o["source_ref"], o["html_sha256"], ["e" + str(o["element_index"])]) for o in obs
        )
        actual = sorted(
            (s["source_id"], s["content_sha256"], s["element_pointers"]) for s in r["source_refs"]
        )
        need(actual == want, "native.source_refs")
        sdk = r.get("sdk_entry") or {}
        need(
            sdk.get("public_entry") is None
            and sdk.get("facade") is None
            and sdk.get("status") == "not_proposed"
            and r.get("acceptance_case_refs") == [],
            "native.sdk_entry",
        )
        i = r.get("implementation") or {}
        need(
            i.get("status") == "unknown"
            and (i.get("evidence") or {}).get("kind") == "manual_native_symbol_observation"
            and i.get("binding_record_present") is False
            and i.get("ast_checked_this_round") is False
            and i.get("offline_checked_this_round") is False
            and i.get("live_checked_this_round") is False,
            "native.implementation",
        )
    ids = set(cands) | {c.get("symbol") for c in cands.values()}
    docs = {"37", "4146", "4148"}
    need(
        not any(
            b.get("region") == "domestic"
            and str(b.get("source_doc_id")) in docs
            or str(b.get("operation_id")) in ids
            for b in state["bindings"]
        ),
        "native.bindings",
    )
    good = ("recorded_evidence", "passed", "met", "verified")
    need(
        not any(
            set(cs.get("capability_ids") or []) & set(cands) and cs.get("result") in good
            for cs in state["cases"]
        ),
        "native.cases",
    )


def native_mutations(state):
    muts = [
        (
            "native-element",
            "native.element doc=37",
            [(state["native"]["observations"]["manual:domestic:37:e2899"], "element_index", 2898)],
        )
    ]
    if state["registry"].get("scope_revision") == REVISION:
        gid = "candidate:domestic:native-ref:getDeviceList"
        row = next(r for r in state["registry"]["capabilities"] if r["capability_id"] == gid)
        muts.append(
            (
                "native-implementation",
                "native.implementation",
                [(row["implementation"], "status", "implemented")],
            )
        )
    return muts


def load_state(repo, snap):
    review_bytes = (repo / REVIEW_PATH).read_bytes()
    native_bytes = (repo / NATIVE_INTAKE).read_bytes()
    acceptance = json.loads((repo / ACCEPTANCE_PATH).read_text(encoding="utf-8"))
    return {
        "scope": json.loads((repo / SCOPE_PATH).read_text(encoding="utf-8")),
        "review": json.loads(review_bytes.decode("utf-8")),
        "registry": json.loads((repo / REGISTER_PATH).read_text(encoding="utf-8")),
        "cases": acceptance["cases"],
        "guide": (repo / GUIDE_PATH).read_text(encoding="utf-8"),
        "readme": (repo / "README.md").read_text(encoding="utf-8"),
        "review_sha": sha256(review_bytes).hexdigest(),
        "archive": load_archive(snap),
        "native": json.loads(native_bytes),
        "native_sha": sha256(native_bytes).hexdigest(),
        "bindings": json.loads((repo / "contracts/sdk-bindings.json").read_bytes())["bindings"],
    }


def verify(state):
    structures = check_archive(state)
    check_facts(state, structures)
    check_records(state)
    check_native(state)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="verify.py")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        state = load_state(args.repo, args.snapshot_dir)
        verify(state)
        report = {
            "passed": True,
            "scope_revision": REVISION,
            "scope_baseline": BASELINE,
            "verified_pages": list(DOCS),
            "navigation_edges": [[src, target] for src, (_, target) in REDIRECTS.items()],
            "runtime_evidence": state["review"]["runtime_evidence"],
            "native_observations": sorted(state["native"]["observations"]),
            "native_candidates": sorted(state["native"]["candidates"]),
        }
        if args.self_test:
            report["mutations"] = self_test(state)
    except Fail as exc:
        report = {"passed": False, "check": str(exc)}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        report = {"passed": False, "check": "input.invalid"}
    print(json.dumps(report, separators=(",", ":")))
    return 0 if report["passed"] else 1


def self_test(state):
    archive, review, registry = state["archive"], state["review"], state["registry"]

    edge = next(
        r for r in archive["edges"] if r["from_region"] == "domestic" and r["from_doc_id"] == "5240"
    )
    menus = {
        r["doc_id"]: r
        for r in archive["menus"]
        if r["region"] == "domestic" and r["doc_id"] in ("5240", "5241")
    }
    ios, harm = menus["5240"], menus["5241"]
    src = next(r for r in registry["source_reviews"] if r["source_ref"] == PREFIX + "4148")
    tests = [
        ("edges", "edges doc=5240", [(edge, "to_doc_id", "37")]),
        (
            "menus",
            "menu_path doc=5240",
            [
                (ios, "category_path", harm["category_path"]),
                (harm, "category_path", ios["category_path"]),
            ],
        ),
        ("facts", "facts.demo", [(review["facts"]["demo"], "sdk_region_version", "domestic_only")]),
        ("runtime", "review.runtime", [(review["runtime_evidence"], "native_runs", 1)]),
        ("register", "register.state doc=4148", [(src, "review_state", "reviewed")]),
    ]
    alias = review["sources"]["5240"]
    tests.append(("alias", "source.alias doc=5240", [(alias, "target", "37")]))
    if registry["scope_revision"] == REVISION:
        gate = next(c for c in state["cases"] if c["case_id"] == GATE)
        tests.append(("gate", "gate.criterion", [(gate["criterion_assessment"], CRITERION, "met")]))
    tests.extend(native_mutations(state))
    passed = []
    for name, want, edits in tests:
        saved = [(rec, key, rec[key]) for rec, key, _ in edits]
        try:
            for rec, key, new in edits:
                rec[key] = new
            outcome = None
            try:
                verify(state)
            except Fail as err:
                outcome = str(err)
            need(outcome == want, "self-test." + name)
        finally:
            for rec, key, old in saved:
                rec[key] = old
        passed.append({"mutation": name, "rejected_by": want})
    return passed


if __name__ == "__main__":
    raise SystemExit(main())
