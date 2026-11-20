#!/usr/bin/env python3
"""Build and audit an unsigned Android SDK consumer without installing or running it."""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from android_probe_bytecode import BytecodeCheckError, check_bytecode

SCOPE = "docs/coordination/native-android-build/scope.json"
PREFIX = "ANDROID_SDK_BUILD_RESULT="
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


class Failure(Exception):
    """Machine-readable diagnostics must never include raw external tool output."""


def need(condition, check, detail=None):
    if not condition:
        raise Failure(check)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(argv, env, *, cwd=None, timeout=180):
    return subprocess.run(
        [str(x) for x in argv],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=timeout,
    )


def _report_file(entry, root, name, sha, size):
    need(isinstance(entry, dict), "INPUT_ENTRY")
    raw = Path(str(entry.get("file") or ""))
    path = raw.resolve()
    ok = raw.is_absolute() and path.is_file() and not raw.is_symlink() and root in path.parents
    need(ok and entry.get("filename") == name and path.name == name, "INPUT_FILE")
    need(
        isinstance(size, int) and entry.get("bytes") == size and path.stat().st_size == size,
        "INPUT_SIZE",
    )
    need(entry.get("sha256") == sha and file_sha(path) == sha, "INPUT_SHA")
    return path


def resolved_inputs(comp, project, cache):
    report_path = Path(project) / "app" / "build" / "reports" / "ezviz-inputs.json"
    need(report_path.is_file(), "INPUT_REPORT")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        need(False, "INPUT_JSON")
    need(isinstance(report, dict) and report.get("schema_version") == 1, "INPUT_SCHEMA")
    need(report.get("configuration") == "releaseRuntimeClasspath", "INPUT_CONFIG")
    artifacts, pom, jars = report.get("artifacts"), report.get("pom"), comp["runtime_jars"]
    need(isinstance(artifacts, dict) and isinstance(pom, dict) and len(jars) == 3, "INPUT_SHAPE")
    need(set(artifacts) == {comp["coordinate"], *jars} and len(artifacts) == 4, "INPUT_SET")
    root, paths, shas = Path(cache).resolve(), {}, {}
    for gav in sorted(artifacts):
        parts = gav.split(":")
        entry = artifacts[gav]
        need(len(parts) == 3 and isinstance(entry, dict), "INPUT_GAV")
        is_aar = gav == comp["coordinate"]
        need(entry.get("type") == ("aar" if is_aar else "jar"), "INPUT_TYPE")
        sha = comp["aar_sha256"] if is_aar else jars[gav]["sha256"]
        size = comp["aar_bytes"] if is_aar else jars[gav]["bytes"]
        name = "%s-%s.%s" % (parts[1], parts[2], "aar" if is_aar else "jar")
        paths[gav], shas[gav] = _report_file(entry, root, name, sha, size), sha
    group, aid, ver = comp["coordinate"].split(":")
    pom_path = _report_file(
        pom, root, "%s-%s.pom" % (aid, ver), comp["pom_sha256"], pom.get("bytes")
    )
    m = "{http://maven.apache.org/POM/4.0.0}"
    try:
        pel = ET.parse(pom_path).getroot()
    except ET.ParseError:
        need(False, "POM_XML")

    def txt(tag):
        return (pel.findtext(m + tag) or "").strip()

    need(pel.tag == m + "project", "POM_ROOT")
    need(
        (txt("groupId"), txt("artifactId"), txt("version"), txt("packaging"))
        == (group, aid, ver, "aar"),
        "POM_GAV",
    )
    deps = pel.find(m + "dependencies")
    need(deps is None or not deps.findall(m + "dependency"), "POM_DEPS")
    jar_gavs = sorted(jars)
    meta = {
        "configuration": "releaseRuntimeClasspath",
        "aar_sha256": shas[comp["coordinate"]],
        "pom_sha256": comp["pom_sha256"],
        "runtime_jar_sha256": {g: shas[g] for g in jar_gavs},
    }
    return paths[comp["coordinate"]], [paths[g] for g in jar_gavs], meta


def aar_inputs(comp, aar, temp):
    out = Path(temp) / "vendor-jars"
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(aar) as zf:
        names = zf.namelist()
        need(len(names) == len(set(names)), "AAR_DUP_MEMBER")
        need({"classes.jar", "R.txt", "AndroidManifest.xml"} <= set(names), "AAR_LAYOUT")
        libs = sorted(
            n
            for n in names
            if n.startswith("libs/")
            and "/" not in n[5:]
            and n[5:].endswith(".jar")
            and len(n[5:]) > 4
        )
        need(len(libs) == comp["embedded_jars"], "AAR_LIBS_COUNT")
        need(digest(zf.read("classes.jar")) == comp["classes_jar_sha256"], "AAR_CLASSES_SHA")
        need(digest(zf.read("R.txt")) == comp["r_txt_sha256"], "AAR_RTXT_SHA")
        try:
            manifest = ET.fromstring(zf.read("AndroidManifest.xml"))
        except ET.ParseError:
            need(False, "AAR_MANIFEST_XML")
        need(manifest.tag == "manifest", "AAR_MANIFEST_ROOT")
        sdk = manifest.find("uses-sdk")
        key = "{%s}minSdkVersion" % ANDROID_NS.strip("{}")
        need(sdk is not None and sdk.get(key) == str(comp["minimum_android_api"]), "AAR_MIN_SDK")
        banned = (
            "uses-permission",
            "uses-permission-sdk-23",
            "activity",
            "activity-alias",
            "service",
            "receiver",
            "provider",
        )
        need(not any(el.tag in banned for el in manifest.iter()), "AAR_MANIFEST_COMPONENTS")
        out_res, seen, written = out.resolve(), set(), []
        for member in ("classes.jar", *libs):
            info = zf.getinfo(member)
            need(info.filename == member and not info.is_dir(), "AAR_MEMBER")
            base = member if member == "classes.jar" else member[5:]
            need(
                base.endswith(".jar")
                and not base.startswith(".")
                and "\\" not in base
                and base not in seen,
                "AAR_MEMBER_NAME",
            )
            target = out / base
            need(target.resolve().parent == out_res and not target.exists(), "AAR_MEMBER_PATH")
            seen.add(base)
            target.write_bytes(zf.read(member))
            written.append(target)
    with zipfile.ZipFile(written[0]) as cj:
        cnames = cj.namelist()
        need(len(cnames) == len(set(cnames)), "CLASSES_DUP_MEMBER")
        entries = set(cnames)
        for owner in {row["owner"] for row in comp["symbols"]}:
            member = owner.replace(".", "/") + ".class"
            need(member in entries, "CLASS_OWNER_MISSING")
            with cj.open(member) as fh:
                head = fh.read(8)
            need(len(head) == 8 and head[:4] == b"\xca\xfe\xba\xbe", "CLASS_MAGIC")
            need(struct.unpack(">H", head[6:8])[0] == comp["classes_major_version"], "CLASS_MAJOR")
    return written


def prepare_tools(scope, sdk, jdk, gradle, temp):
    tc = scope["toolchain"]
    sdk, jdk, temp = Path(sdk), Path(jdk), Path(temp)
    for d in ("dependencies", "android-user", "xdg-cache", "tmp"):
        (temp / d).mkdir(parents=True, exist_ok=True)
        os.chmod(temp / d, 0o700)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "LC_ALL": "C",
        "JAVA_HOME": str(jdk),
        "ANDROID_HOME": str(sdk),
        "ANDROID_SDK_ROOT": str(sdk),
        "GRADLE_USER_HOME": str(temp / "dependencies"),
        "ANDROID_USER_HOME": str(temp / "android-user"),
        "XDG_CACHE_HOME": str(temp / "xdg-cache"),
        "TMPDIR": str(temp / "tmp"),
    }
    g = Path(gradle)
    bt = sdk / "build-tools" / str(tc["build_tools"])
    tools = {
        "gradle": g if g.is_file() else g / "bin" / "gradle",
        "javac": jdk / "bin" / "javac",
        "javap": jdk / "bin" / "javap",
        "android_jar": sdk / "platforms" / f"android-{tc['compile_sdk']}" / "android.jar",
        "aapt2": bt / "aapt2",
        "apksigner": bt / "apksigner",
        "dexdump": bt / "dexdump",
        "apkanalyzer": sdk / "cmdline-tools" / "latest" / "bin" / "apkanalyzer",
    }
    if not tools["apkanalyzer"].is_file():
        alt = sorted((sdk / "cmdline-tools").glob("*/bin/apkanalyzer"))
        need(alt, "toolchain.missing.apkanalyzer")
        tools["apkanalyzer"] = alt[-1]
    need(tools["android_jar"].is_file(), "toolchain.missing.android_35_platform")
    java = jdk / "bin" / "java"
    tool_map = {
        "java": java,
        **{
            name: tools[name]
            for name in ("gradle", "javac", "javap", "aapt2", "apksigner", "dexdump", "apkanalyzer")
        },
    }
    for name, path in tool_map.items():
        need(Path(path).is_file() and os.access(path, os.X_OK), "toolchain.missing." + name)
    r = command([str(java), "-version"], env, cwd=temp, timeout=60)
    m = re.search(r'version "(\d+)', f"{r.stdout or ''}{r.stderr or ''}")
    need(r.returncode == 0 and m and m.group(1) == str(tc["jdk_major"]), "jdk_version_mismatch")
    r = command([str(tools["gradle"]), "--version"], env, cwd=temp, timeout=120)
    ok = re.search(rf"^Gradle {re.escape(str(tc['gradle']))}\s*$", r.stdout or "", re.M)
    need(r.returncode == 0 and ok, "gradle_version_mismatch")
    return env, {k: str(v) for k, v in tools.items()}


def _gradle_failure(phase, res, gavs):
    text = f"{res.stdout or ''}{res.stderr or ''}"
    low = text.lower()
    if any(
        s in low for s in ("could not resolve", "could not find", "could not get", "could not head")
    ):
        cause = "dependency_resolution"
    elif any(
        s in low
        for s in ("sdk location", "platform sdk", "android.jar", "build-tools", "cmdline-tools")
    ):
        cause = "sdk_component_missing"
    elif "error:" in low or "javac" in low or "compilation failed" in low:
        cause = "compiler"
    elif any(s in low for s in ("aapt", "d8", "r8", "dex", "packag", "apk")):
        cause = "native_packaging"
    else:
        cause = "task_execution"
    gav = next((g for g in sorted(gavs) if g in text or g.rsplit(":", 1)[0] in text), "")
    tag = f" gav={gav}" if cause == "dependency_resolution" and gav else ""
    return f"gradle_{phase} rc={res.returncode} cause={cause}{tag}"


def assemble(scope, repo, temp, env, tools):
    acc = scope["acceptance"]
    repo, temp, root = Path(repo), Path(temp), Path(acc["project"])
    need(not root.is_absolute() and ".." not in root.parts, "acceptance_path_invalid")
    project = temp / "project"
    for key in ("settings", "app_build", "manifest", "probe"):
        rel = Path(acc[key])
        need(
            not rel.is_absolute() and ".." not in rel.parts and root in rel.parents,
            "acceptance_path_invalid",
        )
        need((repo / rel).is_file(), "acceptance_input_missing")
        dst = project / rel.relative_to(root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / rel, dst)
    need(sum(1 for p in project.rglob("*") if p.is_file()) == 4, "acceptance_file_count")
    gavs = {
        scope["component"]["coordinate"],
        *scope["component"]["runtime_jars"],
        "com.android.tools.build:gradle",
    }
    base = [
        tools["gradle"],
        "--no-daemon",
        "--max-workers=2",
        "--console=plain",
        "--no-build-cache",
        "--project-cache-dir",
        str(temp / "project-cache"),
        "-Pandroid.builder.sdkDownload=false",
    ]
    res = command(base + [":app:assembleRelease"], env, cwd=project, timeout=840)
    if res.returncode != 0:
        raise Failure(_gradle_failure("online", res, gavs))
    need((project / "app/build/reports/ezviz-inputs.json").is_file(), "inputs_report_missing")
    need(any((project / "app/build/outputs/apk/release").glob("*.apk")), "release_apk_missing")
    for d in (
        project / "app" / "build",
        project / "build",
        project / ".gradle",
        temp / "project-cache",
    ):
        if d.exists():
            shutil.rmtree(d)
    res = command(
        base + ["--offline", "--rerun-tasks", ":app:assembleRelease"], env, cwd=project, timeout=840
    )
    if res.returncode != 0:
        raise Failure(_gradle_failure("offline", res, gavs))
    apks = list((project / "app/build/outputs/apk/release").glob("*.apk"))
    need(len(apks) == 1 and "unsigned" in apks[0].name.lower(), "release_apk_unsigned_missing")
    probes = list((project / "app/build/intermediates/javac").rglob("NativeSDKProbe.class"))
    need(len(probes) == 1, "probe_class_not_unique")
    return project, apks[0], probes[0]


def checked_member(source_bytes, packaged_bytes, label):
    """Require identical zip member bytes (same length and SHA-256)."""
    need(len(source_bytes) == len(packaged_bytes), "apk.member_digest", label)
    need(digest(source_bytes) == digest(packaged_bytes), "apk.member_digest", label)


def check_manifest(xml, application_id, minimum, target):
    """Validate the decoded APK manifest emitted by `apkanalyzer manifest print`."""
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    root = ET.fromstring(xml)
    need(root.tag.rsplit("}", 1)[-1] == "manifest", "apk.manifest", "root")
    need(root.get("package") == application_id, "apk.manifest", "package")
    sdk = [e for e in root if e.tag.rsplit("}", 1)[-1] == "uses-sdk"]
    need(len(sdk) == 1, "apk.manifest", "uses-sdk")
    need(sdk[0].get(ANDROID_NS + "minSdkVersion") == str(minimum), "apk.manifest", "minSdkVersion")
    need(
        sdk[0].get(ANDROID_NS + "targetSdkVersion") == str(target),
        "apk.manifest",
        "targetSdkVersion",
    )
    names = [e.tag.rsplit("}", 1)[-1] for e in root.iter()]
    perms = [
        n
        for n in names
        if n == "uses-permission"
        or n.startswith("uses-permission-sdk")
        or n in ("permission", "permission-group", "permission-tree")
    ]
    need(not perms, "apk.permissions", "declared")
    need(names.count("application") == 1, "apk.manifest", "application")
    comps = [
        n
        for n in names
        if n in ("activity", "activity-alias", "receiver", "provider", "service", "instrumentation")
    ]
    need(not comps, "apk.components", "declared")
    return {
        "min_sdk": int(minimum),
        "target_sdk": int(target),
        "application_id": application_id,
        "permissions": 0,
        "components": 0,
    }


def inspect_archive(scope, aar, apk, tools, env):
    """Verify the unsigned APK was rebuilt faithfully from the AAR (stdlib only)."""
    comp, acc = scope["component"], scope["acceptance"]
    appid, abis = acc["application_id"], comp["abis"]
    min_sdk, target_sdk = comp["minimum_android_api"], scope["toolchain"]["target_sdk"]
    v1sig = re.compile(r"META-INF/[^/]+\.(?:RSA|DSA|EC|SF)$", re.I)
    with zipfile.ZipFile(aar) as aar_zip, zipfile.ZipFile(apk) as apk_zip:
        for tag, zf in (("aar", aar_zip), ("apk", apk_zip)):
            names = zf.namelist()
            need(len(names) == len(set(names)), tag + ".archive", "duplicate")
            need(zf.testzip() is None, tag + ".archive", "crc")
            need(not any(v1sig.search(n) for n in names), tag + ".signature", "v1")
        with open(apk, "rb") as fh:
            fh.seek(max(0, apk_zip.start_dir - 16))
            need(fh.read(16) != b"APK Sig Block 42", "apk.signature", "v2")
        proc = command([tools["apksigner"], "verify", "--verbose", "--print-certs", str(apk)], env)
        out = (proc.stdout or "") + (proc.stderr or "")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        need(
            proc.returncode != 0
            and re.search(
                r"Missing META-INF/MANIFEST\.MF|No signatures|No signers|not signed|No[^\n]*signature",
                out,
            ),
            "apk.signature",
            "unsigned",
        )
        proc = command([tools["apkanalyzer"], "manifest", "print", str(apk)], env)
        need(proc.returncode == 0, "apk.manifest", "exit")
        xml_text = proc.stdout or ""
        manifest = check_manifest(xml_text, appid, min_sdk, target_sdk)
        aar_names, apk_names = aar_zip.namelist(), apk_zip.namelist()
        apk_set = set(apk_names)
        need(
            {n.split("/")[1] for n in aar_names if n.startswith("jni/") and n.endswith(".so")}
            == {n.split("/")[1] for n in apk_names if n.startswith("lib/") and n.endswith(".so")}
            == set(abis),
            "apk.abi",
            "set",
        )
        report = {
            "apk_sha256": file_sha(Path(apk)),
            "abis": {},
            "manifest": manifest,
            "signed": False,
        }
        for abi, spec in sorted(abis.items()):
            aar_lib = {
                n.removeprefix("jni/" + abi + "/"): n
                for n in aar_names
                if n.startswith("jni/" + abi + "/") and n.endswith(".so")
            }
            apk_lib = {
                n.removeprefix("lib/" + abi + "/"): n
                for n in apk_names
                if n.startswith("lib/" + abi + "/") and n.endswith(".so")
            }
            need(
                len(aar_lib) == len(apk_lib) == spec["libraries"] and set(aar_lib) == set(apk_lib),
                "apk.abi",
                abi,
            )
            pairs = []
            for lib in sorted(aar_lib):
                src = aar_zip.read(aar_lib[lib])
                checked_member(src, apk_zip.read(apk_lib[lib]), "lib/" + abi + "/" + lib)
                need(
                    src[:4] == b"\x7fELF" and src[4] == spec["elf_bits"] // 32 and src[5] == 1,
                    "apk.elf",
                    lib,
                )
                need(struct.unpack_from("<HH", src, 16) == (3, spec["machine"]), "apk.elf", lib)
                pairs.append((lib, digest(src)))
            report["abis"][abi] = {
                "files": len(pairs),
                "elf_bits": spec["elf_bits"],
                "elf_machine": spec["machine"],
                "sha256_manifest": digest("".join(n + ":" + s + ";" for n, s in pairs).encode()),
            }
        aar_assets = {
            n: aar_zip.read(n) for n in aar_names if n.startswith("assets/") and not n.endswith("/")
        }
        need(len(aar_assets) == int(comp.get("assets", 58)), "aar.assets", "count")
        allow = re.compile(r"assets/dexopt/[^/]+\.profm?$")
        pairs = []
        for name, src in sorted(aar_assets.items()):
            need(name in apk_set, "apk.assets", "missing")
            checked_member(src, apk_zip.read(name), name)
            pairs.append((name, digest(src)))
        extra = [
            n
            for n in apk_names
            if n.startswith("assets/") and not n.endswith("/") and n not in aar_assets
        ]
        need(all(allow.match(n) for n in extra), "apk.assets", "extra")
        report["assets_sha256"] = digest("".join(n + ":" + s + ";" for n, s in pairs).encode())
        report["asset_count"] = len(aar_assets)
        report["resource_sources"] = len(
            [n for n in aar_names if n.startswith("res/") and not n.endswith("/")]
        )
        need(report["resource_sources"] == comp["resource_sources"], "aar.res", "count")
        dex_shas = {
            n: digest(apk_zip.read(n))
            for n in sorted({n for n in apk_names if re.fullmatch(r"classes\d*\.dex", n)})
        }
        need("classes.dex" in dex_shas, "apk.dex", "classes.dex")
        ctrl = sorted(aar_assets.items())[0] if aar_assets else None
        if ctrl is None:
            abi0 = sorted(abis)[0]
            n0 = sorted(
                n for n in aar_names if n.startswith("jni/" + abi0 + "/") and n.endswith(".so")
            )[0]
            ctrl = (n0, aar_zip.read(n0))
        bad = bytearray(ctrl[1])
        bad[0] ^= 0xFF
        try:
            checked_member(ctrl[1], bytes(bad), "negative-control")
        except Failure as exc:
            need(str(exc) == "apk.member_digest", "apk.negative_control", "member")
        else:
            need(False, "apk.negative_control", "member")
        root2 = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
        root2.append(
            ET.Element("uses-permission", {ANDROID_NS + "name": "android.permission.CAMERA"})
        )
        try:
            check_manifest(ET.tostring(root2), appid, min_sdk, target_sdk)
        except Failure as exc:
            need(str(exc) == "apk.permissions", "apk.negative_control", "permissions")
        else:
            need(False, "apk.negative_control", "permissions")
    report["negative_controls"] = {
        "same_size_member": {"rejected": True, "reason": "apk.member_digest", "member": ctrl[0]},
        "injected_permission": {"rejected": True, "reason": "apk.permissions"},
    }
    return report, dex_shas


def inspect_resources(comp, aar, apk, tools, env):
    with zipfile.ZipFile(str(aar)) as zf:
        rtxt = zf.read("R.txt")
        base = {
            e.get("name"): e.text
            for e in ET.fromstring(zf.read("res/values/values.xml")).iter("string")
        }
        zh = {
            e.get("name"): e.text
            for e in ET.fromstring(zf.read("res/values-zh/values-zh.xml")).iter("string")
        }
    need(digest(rtxt) == comp["r_txt_sha256"], "RTXT_SHA256_MISMATCH")
    decls = re.findall(r"int\s+(\w+)\s+(\w+)\s+0x[0-9a-fA-F]+", rtxt.decode("utf-8", "replace"))
    pairs, counts = set(decls), {}
    need(len(pairs) == 95, "RTXT_RESOURCE_COUNT")
    for t, _ in decls:
        counts[t] = counts.get(t, 0) + 1
    diff = sorted(n for n in set(base) & set(zh) if base[n] != zh[n])
    need("ez_ai_cloud_default_title" in diff, "AAR_LOCALE_VALUES_ABSENT")
    rep = "ez_ai_cloud_default_title"
    proc = command([tools["aapt2"], "dump", "resources", str(apk)], env)
    need(proc.returncode == 0, "AAPT2_DUMP_RESOURCES")
    res_re = re.compile(r"^\s+resource\s+0x[0-9a-fA-F]+\s+(?:[\w.]+:)?(\w+)/(\w+)")
    cfg_re = re.compile(r"^\s+\(([^)]*)\)")
    seen, confs, cur = set(), {}, None
    for line in proc.stdout.splitlines():
        m = res_re.match(line)
        if m:
            seen.add(m.groups())
            cur = "%s/%s" % m.groups()
            confs.setdefault(cur, set())
        elif cur:
            c = cfg_re.match(line)
            if c:
                confs[cur].add(c.group(1))
    need(pairs <= seen, "APK_RESOURCES_INCOMPLETE")
    rcfg = confs.get("string/" + rep, set())
    need(
        "" in rcfg and any(c == "zh" or c.startswith("zh-") or c.startswith("b+zh") for c in rcfg),
        "APK_ZH_CONFIG_ABSENT",
    )
    return {
        "type_counts": counts,
        "symbols": len(pairs),
        "symbol_set_sha256": digest("\n".join(sorted("%s/%s" % p for p in pairs)).encode()),
        "localized_example": rep,
        "configurations": sorted(c or "default" for c in rcfg),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--gradle", type=Path, required=True)
    parser.add_argument("--android-sdk", type=Path, required=True)
    parser.add_argument("--jdk", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(args=argv)
    try:
        repo = args.repo.resolve()
        scope = json.loads((repo / SCOPE).read_text(encoding="utf-8"))
        need(scope.get("cohort_id") == "NATIVE-ANDROID-AAR-534-001", "scope_id")
        with tempfile.TemporaryDirectory(prefix="ezviz-android-build-") as folder:
            temp = Path(folder).resolve()
            env, tools = prepare_tools(
                scope, args.android_sdk.resolve(), args.jdk.resolve(), args.gradle.resolve(), temp
            )
            project, apk, probe_class = assemble(scope, repo, temp, env, tools)
            comp = scope["component"]
            aar, deps, dep_summary = resolved_inputs(comp, project, Path(env["GRADLE_USER_HOME"]))
            jars = aar_inputs(comp, aar, temp)
            archive_report, dex_shas = inspect_archive(scope, aar, apk, tools, env)
            resources = inspect_resources(comp, aar, apk, tools, env)
            bytecode = check_bytecode(
                scope,
                apk,
                probe_class,
                jars + deps,
                tools,
                env,
                temp,
                repo / scope["acceptance"]["probe"],
            )
            result = {
                "passed": True,
                "toolchain": {
                    k: scope["toolchain"][k]
                    for k in (
                        "gradle",
                        "agp",
                        "jdk_major",
                        "compile_sdk",
                        "target_sdk",
                        "build_tools",
                    )
                },
                "coordinate": comp["coordinate"],
                "dependencies": dep_summary,
                "consumer": {
                    "input_sha256": {
                        k: file_sha(repo / scope["acceptance"][k])
                        for k in ("settings", "app_build", "manifest", "probe")
                    },
                    "apk_sha256": file_sha(apk),
                    "apk_bytes": apk.stat().st_size,
                },
                "dex_sha256": dex_shas,
                "archive": archive_report,
                "resources": resources,
                "bytecode": bytecode,
                "build_passes": ["online_assembleRelease", "offline_rerun_assembleRelease"],
                "signed": False,
                "installed": False,
                "executed": False,
                "emulator_runs": 0,
                "device_requests": 0,
            }
            runner = {}
            for key in (
                "GITHUB_SHA",
                "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT",
                "ImageOS",
                "ImageVersion",
            ):
                value = os.environ.get(key)
                if value and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value):
                    runner[key] = value
            proc = command(["git", "-C", repo, "rev-parse", "HEAD"], env)
            head = proc.stdout.strip() if proc.returncode == 0 else ""
            if re.fullmatch(r"[0-9a-f]{40}", head):
                runner["checkout"] = head
            result["runner"] = runner
            agp = scope["toolchain"]["agp"]
            cache = (
                Path(env["GRADLE_USER_HOME"])
                / "caches/modules-2/files-2.1/com.android.tools.build/gradle"
                / agp
            )
            matches = list(cache.glob("*/gradle-" + agp + ".jar"))
            need(len(matches) == 1, "agp_jar_missing")
            result["toolchain"]["agp_runtime_jar_sha256"] = file_sha(matches[0])
            release = (Path(args.jdk) / "release").read_text(errors="replace")
            version_match = re.search(r'^JAVA_VERSION="([0-9A-Za-z.+_-]{1,64})"', release, re.M)
            need(
                version_match is not None and version_match.group(1).split(".")[0] == "17",
                "jdk_version_mismatch",
            )
            result["toolchain"]["java_version"] = version_match.group(1)
    except (Failure, BytecodeCheckError) as exc:
        result = {"passed": False, "check": str(exc)}
    except subprocess.TimeoutExpired:
        result = {"passed": False, "check": "tool_timeout"}
    except (
        OSError,
        KeyError,
        ValueError,
        IndexError,
        TypeError,
        zipfile.BadZipFile,
        ET.ParseError,
    ):
        result = {"passed": False, "check": "invalid_input_or_tool_result"}
    if args.report:
        try:
            args.report.write_text(
                json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            result = {"passed": False, "check": "report_write"}
    print(PREFIX + json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
