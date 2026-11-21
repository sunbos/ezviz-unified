#!/usr/bin/env python3
"""Read only the frozen public Harmony SDK archive and its declarations."""

import argparse
import copy
import gzip
import hashlib
import io
import json
import posixpath
import re
import stat
import struct
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

SCOPE = "docs/coordination/native-harmony-sdk-build/scope.json"
MAX_RELEASE = 64 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_METADATA = 2 * 1024 * 1024


class Failure(Exception):
    """Failures expose short known condition names, never untrusted payloads."""


def need(condition, code):
    if not condition:
        raise Failure(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    ok = (
        isinstance(name, str)
        and 0 < len(name) <= 1024
        and not name.startswith("/")
        and "\\" not in name
        and ":" not in name
        and "//" not in name
    )
    ok = ok and all(ord(c) > 31 and not 0x7F <= ord(c) <= 0x9F for c in name)
    if ok and name.endswith("/"):
        name = name[:-1]
        ok = name != "" and not name.endswith("/")
    ok = ok and all(p not in ("", ".", "..") for p in name.split("/"))
    need(ok and posixpath.normpath(name) == name, "archive.path")
    return name


def metadata(raw):
    need(isinstance(raw, (bytes, bytearray)) and len(raw) <= MAX_METADATA, "metadata.size")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        need(False, "metadata.json")
    text = re.sub(
        r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*.*?\*/',
        lambda m: m.group(0) if m.group(0)[0] == '"' else " ",
        text,
        flags=re.S,
    )

    def nodup(pairs):
        out = {}
        for k, v in pairs:
            need(k not in out, "metadata.json")
            out[k] = v
        return out

    try:
        out = json.loads(text, object_pairs_hook=nodup)
    except json.JSONDecodeError:
        need(False, "metadata.json")
    need(isinstance(out, dict), "metadata.type")
    return out


def read_release(comp, path):
    file = Path(path)
    st = file.lstat()
    need(stat.S_ISREG(st.st_mode), "release.file")
    need(st.st_size == comp["zip_bytes"] and st.st_size <= MAX_RELEASE, "release.size")
    with file.open("rb") as fh:
        raw = fh.read(MAX_RELEASE + 1)
    need(len(raw) == comp["zip_bytes"] and sha(raw) == comp["zip_sha256"], "release.hash")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        need(False, "archive.zip")
    by, kind, total, har = {}, {}, 0, None
    with zf:
        try:
            infos = zf.infolist()
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
            need(False, "archive.zip")
        need(len(infos) <= MAX_ENTRIES, "archive.entries")
        for zi in infos:
            wasdir = zi.filename.endswith("/")
            need(zi.orig_filename == zi.filename, "archive.path")
            name = safe_name(zi.filename)
            need(not name.lower().endswith(".hap"), "archive.hap")
            need(name not in by, "archive.path")
            by[name] = zi
            need((zi.flag_bits & 1) == 0, "archive.entry")
            mode = (zi.external_attr >> 16) & 0xFFFF
            if mode:
                need(
                    (stat.S_ISREG(mode) or stat.S_ISDIR(mode)) and stat.S_ISDIR(mode) == wasdir,
                    "archive.entry",
                )
            else:
                need(bool(zi.external_attr & 0x10) == wasdir, "archive.entry")
            kind[name] = "d" if wasdir else "f"
            need(0 <= zi.file_size <= MAX_MEMBER, "archive.member")
            total += zi.file_size
            need(total <= MAX_EXPANDED, "archive.expanded")
        need(
            all(
                kind.get(str(parent)) != "f"
                for name in by
                for parent in PurePosixPath(name).parents
            ),
            "archive.path",
        )
        roots = comp["root_har_members"]
        need(isinstance(roots, (list, tuple)) and len(roots) == 2, "archive.path")
        roots = [safe_name(x) for x in roots]
        need(len(set(roots)) == 2, "archive.path")
        ent = [
            n for n in roots if PurePosixPath(n).parts[-3:] == ("entry", "libs", "EZOpenSDK.har")
        ]
        need(len(ent) == 1, "archive.path")
        for n in roots:
            zi = by.get(n)
            need(zi is not None and kind[n] == "f", "archive.member")
            try:
                payload = zf.read(zi)
            except Exception:
                need(False, "archive.member")
            need(
                len(payload) == comp["root_har_bytes"]
                and sha(payload) == comp["root_har_sha256"]
                and hashlib.md5(payload, usedforsecurity=False).hexdigest()
                == comp["published_har_md5"],
                "release.hash",
            )
            if n == ent[0]:
                har = payload
        mn = str(PurePosixPath(ent[0]).parents[1] / "src/main/module.json5")
        zi = by.get(mn)
        need(zi is not None and kind.get(mn) == "f" and zi.file_size <= MAX_METADATA, "module.json")
        try:
            with zf.open(zi) as fp:
                raw = fp.read(MAX_METADATA + 1)
        except Exception:
            need(False, "module.json")
        mod = metadata(raw).get("module")
        need(isinstance(mod, dict), "module.json")
        req = mod.get("requestPermissions", [])
        need(isinstance(req, list), "module.json")
        names = []
        for p in req:
            need(isinstance(p, dict) and isinstance(p.get("name"), str), "module.json")
            names.append(p["name"])
        need(har is not None, "archive.member")
        return har, names


def tar_files(har_bytes):
    need(isinstance(har_bytes, (bytes, bytearray)) and har_bytes[:2] == b"\x1f\x8b", "archive.gzip")
    try:
        plain = gzip.GzipFile(fileobj=io.BytesIO(har_bytes)).read(MAX_EXPANDED + 1)
    except (OSError, EOFError):
        need(False, "archive.gzip")
    need(len(plain) <= MAX_EXPANDED, "archive.expanded")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(plain), mode="r:")
    except (tarfile.TarError, OSError, EOFError):
        need(False, "archive.tar")
    seen, files, total = set(), {}, 0
    try:
        for index, m in enumerate(archive):
            need(index < MAX_ENTRIES, "archive.entries")
            name = safe_name(m.name)
            need(name == "package" or name.startswith("package/"), "archive.path")
            need(name not in seen, "archive.path")
            seen.add(name)
            ph = getattr(m, "pax_headers", {}) or {}
            need(
                not getattr(m, "issparse", lambda: False)()
                and not any("sparse" in str(k).lower() or str(k) == "SCHILY.realsize" for k in ph),
                "archive.sparse",
            )
            need(m.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE), "archive.entry")
            if m.isdir():
                need(m.size == 0, "archive.member")
            else:
                need(
                    0 <= m.size <= MAX_MEMBER and m.offset_data + m.size <= len(plain),
                    "archive.member",
                )
                total += m.size
                need(total <= MAX_EXPANDED, "archive.expanded")
                files[name] = m
    except BaseException:
        archive.close()
        raise
    need(
        all(str(parent) not in files for name in seen for parent in PurePosixPath(name).parents),
        "archive.path",
    )
    return archive, files


def inspect_packages(comp, root):
    pins = comp["packages"]
    need(len(pins) == 7 and len(comp["nested_edges"]) == 8, "graph.shape")
    edge_pins = {}
    for edge in comp["nested_edges"]:
        key = (edge["parent_sha256"], safe_name(edge["path"]))
        need(key not in edge_pins and edge["child_sha256"] in pins, "graph.edge")
        edge_pins[key] = edge
    need(comp["root_har_sha256"] in pins, "graph.root")
    state = {
        "packages": {},
        "edges": [],
        "decls": {},
        "native": [],
        "abc_counts": {},
        "license": {},
    }
    adjacency, seen = {}, set()

    def visit(payload, chain):
        digest = sha(bytes(payload))
        need(digest in pins, "package.unknown")
        need(digest not in chain, "graph.cycle")
        need(len(chain) < 8, "graph.depth")
        if digest in seen:
            return digest
        seen.add(digest)
        pin = pins[digest]
        need(len(payload) <= MAX_MEMBER, "package.size")
        tar, members = tar_files(payload)
        try:

            def read(path, limit):
                info = members.get(path)
                need(info is not None and info.size <= limit, "package.member:" + path)
                data = tar.extractfile(info).read(limit + 1)
                need(len(data) == info.size, "package.member:" + path)
                return data

            state["abc_counts"][digest] = sum(path.endswith(".abc") for path in members)
            if digest == comp["root_har_sha256"]:
                license_bytes = read("package/LICENSE", MAX_METADATA)
                state["license"] = {
                    "member": "package/LICENSE",
                    "bytes": len(license_bytes),
                    "sha256": sha(license_bytes),
                }
            pkg_raw = read("package/oh-package.json5", MAX_METADATA)
            mod_raw = read("package/src/main/module.json", MAX_METADATA)
            need(sha(pkg_raw) == pin["root_package_metadata_sha256"], "package.metadata")
            need(sha(mod_raw) == pin["module_sha256"], "package.metadata")
            pkg = metadata(pkg_raw)
            mod = metadata(mod_raw).get("module")
            need(isinstance(mod, dict), "package.metadata")
            types = pkg.get("types")
            sdk = pkg.get("compatibleSdkVersion")
            perms = [p.get("name") for p in mod.get("requestPermissions", [])]
            need(
                pkg.get("name") == pin["name"] and pkg.get("version") == pin["version"],
                "package.metadata",
            )
            need(
                types == pin["type_entry"] and sdk == pin["compatibleSdkVersion"],
                "package.metadata",
            )
            need(perms == pin["permissions"], "package.metadata")
            if types is not None:
                need("package/" + safe_name(types) in members, "package.types")
            deps = pkg.get("dependencies") or {}
            need(
                isinstance(deps, dict) and sorted(deps) == sorted(pin["declared_dependency_names"]),
                "package.metadata",
            )
            har_targets = set()
            for alias, ref in deps.items():
                need(isinstance(ref, str) and ref.startswith("file:./"), "graph.edge:" + alias)
                target = "package/" + safe_name(ref.removeprefix("file:./"))
                if not target.endswith(".har"):
                    descriptor = target + "/oh-package.json5"
                    need(descriptor in members, "graph.platform")
                    need(
                        metadata(read(descriptor, MAX_METADATA)).get("name") == alias,
                        "graph.platform",
                    )
                    continue
                har_targets.add(target)
                edge, info = edge_pins.get((digest, target)), members.get(target)
                need(edge is not None and info is not None, "graph.edge:" + target)
                need(info.size == edge["bytes"], "graph.edge:" + target)
                data = read(target, MAX_MEMBER)
                need(sha(data) == edge["child_sha256"], "graph.edge:" + target)
                child = visit(data, chain + [digest])
                need(state["packages"][child]["name"] == alias, "graph.edge:" + alias)
                adjacency.setdefault(digest, []).append(child)
                state["edges"].append(
                    {
                        "parent_sha256": digest,
                        "path": target,
                        "child_sha256": child,
                        "bytes": info.size,
                    }
                )
            need({p for p in members if p.endswith(".har")} == har_targets, "graph.edge.hidden")
            abi_dirs, elf_total = set(), 0
            for path in sorted(members):
                info = members[path]
                if path.endswith((".d.ets", ".d.ts")):
                    state["decls"][(digest, safe_name(path))] = read(path, MAX_METADATA)
                parts = path.split("/")
                if len(parts) < 4 or parts[:2] != ["package", "libs"]:
                    continue
                need(info.size <= MAX_MEMBER, "native.size:" + path)
                stream = tar.extractfile(info)
                head = stream.read(20)
                if head[:4] != b"\x7fELF":
                    continue
                need(info.size >= 20 and head[4] == 2 and head[5] == 1, "native.elf:" + path)
                e_type, e_machine = struct.unpack_from("<HH", head, 16)
                need(e_type == 3 and e_machine == 183, "native.elf:" + path)
                raw = head + stream.read(MAX_MEMBER)
                need(len(raw) == info.size, "native.size:" + path)
                abi_dirs.add(parts[2])
                elf_total += 1
                state["native"].append((pkg["name"], path, sha(raw), info.size))
            need(elf_total == pin["elf_library_count"], "native.count")
            need(sorted(abi_dirs) == sorted(pin["native_abi_dirs"]), "native.abi")
            state["packages"][digest] = {
                "name": pkg.get("name"),
                "version": pkg.get("version"),
                "root_package_metadata_sha256": sha(pkg_raw),
                "type_entry": types,
                "compatibleSdkVersion": sdk,
                "module_sha256": sha(mod_raw),
                "declared_dependency_names": sorted(deps),
                "permissions": perms,
                "elf_library_count": elf_total,
                "native_abi_dirs": sorted(abi_dirs),
            }
        finally:
            tar.close()
        return digest

    need(isinstance(root, bytes), "graph.root")
    blob = root
    need(sha(bytes(blob)) == comp["root_har_sha256"], "graph.root")
    visit(blob, [])
    need(seen == set(pins), "graph.coverage")
    root_pkg = state["packages"][comp["root_har_sha256"]]
    need(
        root_pkg["name"] == comp["root_package"]
        and root_pkg["version"] == comp["version"]
        and root_pkg["compatibleSdkVersion"] == comp["compatible_sdk"],
        "graph.coverage",
    )
    need(
        sorted(state["edges"], key=lambda e: (e["parent_sha256"], e["path"]))
        == sorted(comp["nested_edges"], key=lambda e: (e["parent_sha256"], e["path"])),
        "graph.edge",
    )
    total, budget = [0], [32]

    def count(node, depth):
        need(depth <= 8 and budget[0] > 0, "graph.nodes")
        budget[0] -= 1
        total[0] += 1
        for child in adjacency.get(node, []):
            count(child, depth + 1)

    count(comp["root_har_sha256"], 0)
    state["physical_hars"] = total[0]
    need(total[0] == len(comp["nested_edges"]) + 1, "graph.nodes")
    return state


def public_types(comp, state):
    order = ("EZOpenSDK", "EZPlayer", "EZPlaybackRate")
    names = ("@ezviz/ezopensdk", "ezplay")

    def ident(n, call=False):
        return re.compile(
            r"(?<![A-Za-z0-9_$])" + re.escape(n) + r"(?![A-Za-z0-9_$])" + (r"\s*\(" if call else "")
        )

    pub = {n: ident(n) for n in order}

    def fail(label):
        raise Failure(label)

    def lines(data):
        s = re.sub(
            r"/\*.*?\*/",
            lambda m: "\n" * m.group(0).count("\n"),
            data.decode("utf-8", "replace"),
            flags=re.S,
        )
        return [x.split("//", 1)[0].strip() for x in s.splitlines()]

    decls = state["decls"]
    if len(decls) != 176:
        fail("declarations.line")
    need(len(comp["public_symbols"]) == 5, "declarations.line")
    need(
        {row["symbol"] for row in comp["public_symbols"]}
        == {
            "setAccessToken",
            "getDeviceList",
            "setPlayVerifyCode",
            "seekPlayback",
            "setPlaybackRate",
        },
        "declarations.line",
    )
    need(len(comp["not_located"]) == 2, "declarations.line")
    need(set(comp["not_located"]) == {"setServerUrl", "setSurfaceHold"}, "declarations.line")
    for rec in comp["public_symbols"]:
        data = decls.get((rec["package_sha256"], rec["file"]))
        if (
            data is None
            or comp["packages"].get(rec["package_sha256"], {}).get("name") != rec["package_name"]
            or hashlib.sha256(data).hexdigest() != rec["file_sha256"]
        ):
            fail("declarations.line")
        got = lines(data)
        if (
            rec["line"] < 1
            or rec["line"] > len(got)
            or got[rec["line"] - 1] != rec["declaration"].strip()
            or not ident(rec["symbol"], True).search(got[rec["line"] - 1])
        ):
            fail("declarations.line")
    missing = [ident(n, True) for n in comp["not_located"]]
    if any(p.search(s) for data in decls.values() for s in lines(data) for p in missing):
        fail("declarations.line")
    by_name = {}
    for sha, meta in comp["packages"].items():
        if meta.get("name") in names:
            by_name.setdefault(meta["name"], []).append(sha)
    expected = {
        names[0]: [
            (3, "import", order[:1], "./src/main/ets/PublicHeader/EZOpenSDK"),
            (5, "import", order[1:], "ezplay"),
            (6, "export", order, None),
        ],
        names[1]: [
            (6, "import", ("EZPlayer",), "./src/main/ets/EZPlayer/EZPlayer"),
            (38, "import", ("EZPlaybackRate",), "./src/main/ets/EZPlayer/EZPlayerInternal"),
            (39, "export", order[1:], None),
        ],
    }
    actual = {}
    for name in names:
        shas = by_name.get(name, [])
        data = decls.get((shas[0], "package/Index.d.ets")) if len(shas) == 1 else None
        if data is None:
            fail("declarations.exports")
        rows = []
        for i, s in enumerate(lines(data), 1):
            kind = (
                "import"
                if re.match(r"import\b", s)
                else "export"
                if re.match(r"export\b", s)
                else None
            )
            if kind is None:
                continue
            body, src = s[len(kind) :].strip(), None
            if " from " in body:
                body, src = body.split(" from ", 1)
                src = src.strip().rstrip(";").strip().strip("'\"")
            syms = [n for n in order if pub[n].search(body)]
            if syms:
                rows.append({"line": i, "kind": kind, "symbols": syms, "from": src})
        if rows != [
            {"line": lineno, "kind": k, "symbols": list(s), "from": f}
            for lineno, k, s, f in expected[name]
        ]:
            fail("declarations.exports")
        actual[name] = {
            "file": "package/Index.d.ets",
            "sha256": hashlib.sha256(data).hexdigest(),
            "links": rows,
        }
    return {
        "reexports": actual,
        "declarations": comp["public_symbols"],
        "not_located": comp["not_located"],
    }


def native_inventory(comp, state):
    obs = comp["native_merge_observations"]
    adj = {}
    for e in state["edges"]:
        adj.setdefault(e["parent_sha256"], []).append(e)
    rows, physical_locations = [], {}

    def walk(digest, label):
        meta = state["packages"][digest]
        physical_locations[label] = {
            "package": meta["name"],
            "sha256": digest,
            "version": meta["version"],
            "requested_permissions": meta["permissions"],
        }
        for name, path, fsha, size in state["native"]:
            if name != meta["name"]:
                continue
            rows.append(
                {
                    "package": name,
                    "package_sha256": digest,
                    "physical_har": label,
                    "path": path,
                    "basename": PurePosixPath(path).name,
                    "bytes": size,
                    "sha256": fsha,
                    "elf_class": 2,
                    "machine": 183,
                }
            )
        for e in sorted(adj.get(digest, []), key=lambda e: e["path"]):
            walk(e["child_sha256"], label + "!" + e["path"])

    walk(comp["root_har_sha256"], "EZOpenSDK.har")
    need(len(physical_locations) == 9, "native.locations")

    def metric(records):
        return {
            "occurrences": len(records),
            "unique_names": len({record["basename"] for record in records}),
            "unique_contents": len({record["sha256"] for record in records}),
        }

    plain = [r for r in rows if r["basename"].endswith(".so")]
    p, a = metric(plain), metric(rows)
    need(
        (p["occurrences"], p["unique_names"], p["unique_contents"])
        == (
            obs["unversioned_so_occurrences"],
            obs["unversioned_so_basenames"],
            obs["unversioned_so_content_digests"],
        ),
        "native.counts.plain_so",
    )
    need(
        (a["occurrences"], a["unique_names"], a["unique_contents"])
        == (obs["all_elf_occurrences"], obs["all_elf_basenames"], obs["all_elf_content_digests"]),
        "native.counts.all_elf",
    )
    groups = {}
    for r in plain:
        groups.setdefault(r["basename"], []).append(r)
    conflicts = sorted(
        n for n, rs in groups.items() if len(rs) > 1 and len({r["sha256"] for r in rs}) > 1
    )
    identical = sorted(
        n for n, rs in groups.items() if len(rs) > 1 and len({r["sha256"] for r in rs}) == 1
    )
    need(conflicts == sorted(obs["conflicting_unversioned_names"]), "native.conflicts.conflicting")
    need(
        identical == sorted(obs["same_content_duplicate_unversioned_names"]),
        "native.conflicts.identical",
    )
    variants = {
        n: sorted(rs, key=lambda r: (r["physical_har"], r["path"]))
        for n, rs in sorted(groups.items())
        if len(rs) > 1
    }
    # Same-name variants are inventoried, not resolved: which source a real hvigor build would link
    # into the final .hap stays obs["selected_output_sources"] == "unknown_until_real_hvigor_build".
    need(obs["selected_output_sources"] == "unknown_until_real_hvigor_build", "native.selection")
    result = {
        "plain_so": p,
        "all_elf": a,
        "same_name_conflicts": conflicts,
        "same_name_identical": identical,
        "same_name_variants": variants,
        "version_suffix_files": sorted(
            {r["basename"] for r in rows if not r["basename"].endswith(".so")}
        ),
        "final_hap_selection_verified": False,
    }
    abc_by_location = {
        location: state["abc_counts"][v["sha256"]] for location, v in physical_locations.items()
    }
    return result, physical_locations, abc_by_location


def check_review(scope, review, state, public, native, locations, abc, demo):
    c = scope["component"]
    need(review["schema_version"] == 1, "review.meta")
    need(
        review["scope_ref"] == SCOPE and review["guide_ref"] == scope["acceptance"]["guide"],
        "review.meta",
    )
    rel = review["release"]
    need(
        set(rel)
        == {
            "catalog",
            "zip_url",
            "version",
            "zip_sha256",
            "zip_bytes",
            "root_har_members",
            "root_har_bytes",
            "root_har_sha256",
            "published_har_md5",
            "root_package",
            "compatible_sdk",
        }
        and all(rel[k] == c[k] for k in rel),
        "review.release",
    )
    need(set(review["sources"]) == {s["doc_id"] for s in scope["sources"]}, "review.sources")
    for s in scope["sources"]:
        r = review["sources"].get(s["doc_id"], {})
        ok = r.get("source_ref") == s["source_ref"] and r.get("source_url") == s["source_url"]
        ok = ok and r.get("html_sha256") == s["html_sha256"]
        ok = ok and r.get("structure_sha256") == s["structure_sha256"]
        ok = ok and r.get("menu_path") == s["menu"][0]
        need(ok, "review.sources")
    g = review["package_graph"]
    pkgs = state["packages"]

    def edge_key(edge):
        return edge["parent_sha256"], edge["path"]

    need(g["packages"] == pkgs and g["unique_packages"] == len(pkgs) == 7, "review.graph")
    need(
        sorted(g["nested_edges"], key=edge_key) == sorted(state["edges"], key=edge_key)
        and len(g["nested_edges"]) == len(state["edges"]) == 8,
        "review.graph",
    )
    need(g["physical_hars"] == state["physical_hars"] == len(locations) == 9, "review.graph")
    need(g["physical_locations"] == locations, "review.graph")
    need(g["shipped_abc_per_physical_har"] == abc, "review.graph")
    need(g["shipped_abc_physical_occurrences"] == sum(abc.values()) == 8, "review.graph")
    need(
        g["shipped_abc_unique_packages"]
        == len({locations[h]["package"] for h, n in abc.items() if n})
        == 6,
        "review.graph",
    )
    pt = review["public_types"]
    need(
        all(pt[k] == public[k] for k in ("reexports", "declarations", "not_located")),
        "review.public",
    )
    need(pt["declaration_files_scanned"] == len(state["decls"]), "review.public")
    nl = review["native_libraries"]
    need(set(nl) == set(native), "review.native")

    def rk(r):
        return r["physical_har"], r["path"]

    for k, v in native.items():
        if k == "same_name_variants":
            exp = {n: sorted(rs, key=rk) for n, rs in v.items()}
            got = {n: sorted(rs, key=rk) for n, rs in nl[k].items()}
            need(got == exp, "review.native")
        elif k == "final_hap_selection_verified":
            need(nl[k] is False, "review.native")
        else:
            need(nl[k] == v, "review.native")
    pm = review["permissions"]
    root = state["packages"][c["root_har_sha256"]]["permissions"]
    need(pm["root"] == [] and root == [], "review.permissions")
    exp = {}
    for m in locations.values():
        if m["sha256"] != c["root_har_sha256"]:
            exp.setdefault(m["package"], set()).update(m["requested_permissions"])
    nb = pm["nested_by_package"]
    need(len(exp) == 6 and set(nb) == set(exp), "review.permissions")
    need(
        all(len(nb[p]) == len(set(nb[p])) and set(nb[p]) == ps for p, ps in exp.items()),
        "review.permissions",
    )
    need(pm["demo"] == demo and len(demo) == 11, "review.permissions")
    grp = review["sources"]["4190"]["permission_groups_by_element"]
    need(set(grp) == {"5", "9", "13", "17", "21"}, "review.permissions")
    need(
        grp
        == {
            "5": ["ohos.permission.STORE_PERSISTENT_DATA"],
            "9": ["ohos.permission.WRITE_IMAGEVIDEO", "ohos.permission.READ_IMAGEVIDEO"],
            "13": ["ohos.permission.MICROPHONE"],
            "17": ["ohos.permission.CAMERA"],
            "21": ["ohos.permission.SET_WIFI_INFO", "ohos.permission.GET_NETWORK_INFO"],
        },
        "review.permissions",
    )
    doc = pm["documented_4190"]
    need(
        sorted({x for v in grp.values() for x in v}) == sorted(doc) and len(doc) == 7,
        "review.permissions",
    )
    need(pm["additional_demo_vs_4190"] == sorted(set(demo) - set(doc)), "review.permissions")
    need(pm["runtime_grants_verified"] is False, "review.permissions")
    le, lic = review["license_evidence"], state["license"]
    need(
        le["member"] == lic["member"]
        and le["bytes"] == lic["bytes"]
        and le["sha256"] == lic["sha256"],
        "review.license",
    )
    need(
        le["commercial_agreement_confirmed"] is False
        and le["huawei_tool_terms_confirmed"] is False,
        "review.license",
    )
    need(le["accepted_terms_this_review"] is False, "review.license")
    rt = review["runtime_evidence"]
    need(
        all(
            rt[k] is False
            for k in ("consumer_compiled", "assembled_hap", "signed", "installed", "executed")
        ),
        "review.runtime",
    )
    need(
        all(
            type(rt[k]) is int and rt[k] == 0
            for k in ("device_requests", "media_requests", "ezcontrol_executions")
        ),
        "review.runtime",
    )


def self_test(scope, review, root, demo, state, public, native, locations, abc):
    def test(name, expected, call):
        try:
            call()
        except Failure as error:
            if str(error).partition(":")[0] == expected:
                return {"mutation": name, "rejected_by": expected}
        raise Failure("self-test." + name)

    def tar_bytes(build):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            build(archive)
        return buffer.getvalue()

    def symlink_entry(archive):
        info = tarfile.TarInfo("package/unsafe")
        info.type = tarfile.SYMTYPE
        info.linkname = "../escape"
        archive.addfile(info)

    def sparse_entry(archive):
        info = tarfile.TarInfo("package/sample")
        info.size = 0
        info.pax_headers = {"SCHILY.realsize": "1"}
        archive.addfile(info)

    def bad_edge():
        comp = copy.deepcopy(scope["component"])
        comp["nested_edges"][0]["child_sha256"] = comp["root_har_sha256"]
        return inspect_packages(comp, root)

    def bad_line():
        comp = copy.deepcopy(scope["component"])
        comp["public_symbols"][0]["line"] += 1
        return public_types(comp, state)

    def bad_count():
        comp = copy.deepcopy(scope["component"])
        comp["native_merge_observations"]["all_elf_occurrences"] -= 1
        return native_inventory(comp, state)

    def bad_runtime():
        bad = copy.deepcopy(review)
        bad["runtime_evidence"]["assembled_hap"] = True
        return check_review(scope, bad, state, public, native, locations, abc, demo)

    return [
        test("unsafe.path", "archive.path", lambda: safe_name("package/../escape")),
        test("symlink.entry", "archive.entry", lambda: tar_files(tar_bytes(symlink_entry))),
        test("sparse.entry", "archive.sparse", lambda: tar_files(tar_bytes(sparse_entry))),
        test("edge.pin", "graph.edge", bad_edge),
        test("decl.line", "declarations.line", bad_line),
        test("elf.count", "native.counts.all_elf", bad_count),
        test("runtime.flag", "review.runtime", bad_runtime),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--release-zip", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        scope = json.loads((args.repo / SCOPE).read_bytes())
        need(scope.get("cohort_id") == "NATIVE-HARMONY-HAR-190-001", "scope.id")
        review = json.loads((args.repo / scope["acceptance"]["source_review"]).read_bytes())
        comp = scope["component"]
        root, demo = read_release(comp, args.release_zip)
        state = inspect_packages(comp, root)
        public = public_types(comp, state)
        native, locations, abc = native_inventory(comp, state)
        check_review(scope, review, state, public, native, locations, abc, demo)
        report = {
            "passed": True,
            "release_sha256": comp["zip_sha256"],
            "version": comp["version"],
            "root_har_sha256": comp["root_har_sha256"],
            "packages": sorted(x["name"] for x in state["packages"].values()),
            "dependency_edges": len(state["edges"]),
            "physical_hars": state["physical_hars"],
            "declarations": [row["symbol"] for row in public["declarations"]],
            "declaration_files": len(state["decls"]),
            "not_located": public["not_located"],
            "plain_so": native["plain_so"],
            "all_elf": native["all_elf"],
            "native_conflicts": native["same_name_conflicts"],
            "identical_duplicate_names": native["same_name_identical"],
            "demo_permission_count": len(demo),
            "shipped_abc_physical_occurrences": sum(abc.values()),
            "native_builds": 0,
            "app_runs": 0,
            "device_calls": 0,
            "hap_verified": False,
            "final_hap_library_selection_verified": False,
            "dependencies": [
                [
                    state["packages"][e["parent_sha256"]]["name"],
                    e["path"],
                    state["packages"][e["child_sha256"]]["name"],
                ]
                for e in sorted(state["edges"], key=lambda e: (e["parent_sha256"], e["path"]))
            ],
            "commercial_terms_accepted": False,
            "shipped_abc_unique_packages": sum(count > 0 for count in state["abc_counts"].values()),
        }
        if args.self_test:
            report["mutations"] = self_test(
                scope, review, root, demo, state, public, native, locations, abc
            )
    except Failure as exc:
        report = {"passed": False, "check": str(exc).partition(":")[0]}
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        EOFError,
        tarfile.TarError,
        zipfile.BadZipFile,
        struct.error,
        RuntimeError,
    ):
        report = {"passed": False, "check": "input.invalid"}
    print(json.dumps(report, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
