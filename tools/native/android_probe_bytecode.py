"""Verify Android consumer calls and defined classes without invoking the SDK."""

import hashlib
import os
import re
import subprocess
import tempfile
import zipfile
from collections import Counter
from pathlib import Path


class BytecodeCheckError(Exception):
    """Fixed diagnostics for bytecode and isolated compiler checks."""


def _javap_invocations(text, owners):
    pattern = re.compile(
        r"^\s*\d+:\s*invoke(?:virtual|interface)\s+#\d+(?:\s*,\s*\d+)?"
        r"\s*//\s*(?:Interface)?Method\s+"
        r'([\w/$]+)\.("?)([\w$<>]+)\2:(\S+)',
        re.MULTILINE,
    )
    return [
        (m.group(1), m.group(3), m.group(4)) for m in pattern.finditer(text) if m.group(1) in owners
    ]


def _smali_invocations(text, owners):
    pattern = re.compile(
        r"^\s*(?:(?:0x)?[0-9a-fA-F]+:\s*)?"
        r"invoke-(?:virtual|interface)(?:/range)?\s*"
        r"\{[^}]*\}\s*,\s*"
        r"(L[\w/$]+;)->([^\s(]+)\(([^)]*)\)"
        r"(\[*[VZBSCIJFD]|\[*L[\w/$]+;)"
        r"(?![\w/$;\[])",
        re.MULTILINE,
    )
    results = []
    for m in pattern.finditer(text):
        owner = m.group(1)[1:-1]
        if owner in owners:
            results.append((owner, m.group(2), "(" + m.group(3) + ")" + m.group(4)))
    return results


def _dex_definitions(text, wanted_classes, wanted_methods):
    defined_classes = set()
    defined_methods = set()
    current_class = None
    class_wanted = False
    in_method_section = False
    pending_name = None
    pending_type = None
    for line in text.splitlines():
        m = re.match(r"^\s*Class descriptor\s*:\s*'([^']+)'", line)
        if m:
            desc = m.group(1)
            if desc.startswith("L") and desc.endswith(";"):
                desc = desc[1:-1]
            current_class = desc
            class_wanted = desc in wanted_classes
            if class_wanted:
                defined_classes.add(desc)
            in_method_section = False
            pending_name = pending_type = None
            continue
        if current_class is None:
            continue
        if re.match(r"^  \S.*-\s*$", line):
            head = line.strip()
            in_method_section = head.startswith("Direct methods") or head.startswith(
                "Virtual methods"
            )
            pending_name = pending_type = None
            continue
        if re.match(r"^\s+#\d+\s*:", line):
            pending_name = pending_type = None
            continue
        if not (class_wanted and in_method_section):
            continue
        m = re.match(r"^\s+name\s*:\s*'(.*)'\s*$", line)
        if m:
            pending_name = m.group(1)
            pending_type = None
            continue
        m = re.match(r"^\s+type\s*:\s*'(.*)'\s*$", line)
        if m:
            pending_type = m.group(1)
            continue
        m = re.match(r"^\s+access\s*:\s*0x([0-9a-fA-F]+)", line)
        if m:
            if (
                pending_name is not None
                and pending_type is not None
                and int(m.group(1), 16) & 0x0001
            ):
                entry = (current_class, pending_name, pending_type)
                if entry in wanted_methods:
                    defined_methods.add(entry)
            pending_name = pending_type = None
    return defined_classes, defined_methods


def _run(argv, env, cwd):
    return subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=180,
    )


def _compile_controls(tools, env, work, source, jars):
    try:
        javac = str(tools["javac"])
        classpath = os.pathsep.join([str(tools["android_jar"]), *(str(j) for j in jars)])
    except KeyError:
        raise BytecodeCheckError("tools_missing_javac") from None
    try:
        original = Path(source).read_text(encoding="utf-8")
    except OSError:
        raise BytecodeCheckError("probe_source_unreadable") from None
    token = "sdk.setServerUrl("
    if original.count(token) != 1:
        raise BytecodeCheckError("probe_source_call_site_not_unique")
    pos_dir = work / "control-positive"
    neg_dir = work / "control-negative"
    neg_src = work / "control-negative-src" / Path(source).name
    try:
        pos_dir.mkdir()
        neg_dir.mkdir()
        neg_src.parent.mkdir()
        neg_src.write_text(
            original.replace(token, "sdk.missingEzvizProbeMethod(", 1),
            encoding="utf-8",
        )
    except OSError:
        raise BytecodeCheckError("control_setup_failed") from None
    common = [
        javac,
        "-proc:none",
        "-encoding",
        "UTF-8",
        "-XDrawDiagnostics",
        "-source",
        "8",
        "-target",
        "8",
        "-classpath",
        classpath,
    ]
    try:
        positive = _run(common + ["-d", str(pos_dir), str(source)], env, work)
    except (OSError, subprocess.SubprocessError):
        raise BytecodeCheckError("javac_positive_exec_failed") from None
    if positive.returncode != 0:
        raise BytecodeCheckError("javac_positive_failed")
    try:
        negative = _run(common + ["-d", str(neg_dir), str(neg_src)], env, work)
    except (OSError, subprocess.SubprocessError):
        raise BytecodeCheckError("javac_negative_exec_failed") from None
    if negative.returncode == 0:
        raise BytecodeCheckError("javac_negative_succeeded")
    diagnostics = (negative.stdout or "") + "\n" + (negative.stderr or "")
    if not any(
        "compiler.err.cant.resolve.location" in line
        and "kindname.method, missingEzvizProbeMethod," in line
        for line in diagnostics.splitlines()
    ):
        raise BytecodeCheckError("javac_negative_diagnostic_missing")
    return positive.returncode, negative.returncode


def check_bytecode(scope, apk, probe_class, jar_classpath, tools, env, temp, probe_source):
    try:
        symbols = scope["component"]["symbols"]
        consumer = scope["acceptance"]["probe_class"]
        expected = [(s["owner"], s["name"], s["descriptor"]) for s in symbols]
    except (KeyError, TypeError):
        raise BytecodeCheckError("scope_malformed") from None
    if len(expected) != 9 or len(set(expected)) != 9:
        raise BytecodeCheckError("unexpected_symbol_set")
    owners = {owner for owner, _, _ in expected}
    if len(owners) != 2:
        raise BytecodeCheckError("unexpected_owner_set")
    if (
        consumer != "org.example.ezvizprobe.NativeSDKProbe"
        or Path(probe_class).name != "NativeSDKProbe.class"
    ):
        raise BytecodeCheckError("unexpected_probe_class")
    consumer_slash = consumer.replace(".", "/")
    try:
        javap = str(tools["javap"])
        apkanalyzer = str(tools["apkanalyzer"])
        dexdump = str(tools["dexdump"])
    except KeyError:
        raise BytecodeCheckError("tools_missing") from None
    vendor = owners | {"com/videogo/openapi/EZGlobalSDK"}
    deps = {"okhttp3/OkHttpClient", "com/google/gson/Gson", "okio/Buffer"}
    wanted_classes = vendor | deps | {consumer_slash}
    wanted_methods = set(expected)

    with tempfile.TemporaryDirectory(dir=str(temp)) as work_name:
        work = Path(work_name)
        pos_rc, neg_rc = _compile_controls(tools, env, work, probe_source, jar_classpath)

        try:
            probe_sha = hashlib.sha256(probe_class.read_bytes()).hexdigest()
        except OSError:
            raise BytecodeCheckError("probe_class_unreadable") from None

        try:
            javap_out = _run([javap, "-c", "-s", "-p", str(probe_class)], env, work)
        except (OSError, subprocess.SubprocessError):
            raise BytecodeCheckError("javap_exec_failed") from None
        if javap_out.returncode != 0 or not javap_out.stdout:
            raise BytecodeCheckError("javap_failed")
        class_inv = _javap_invocations(javap_out.stdout, owners)
        if Counter(class_inv) != Counter(expected):
            raise BytecodeCheckError("class_invocations_mismatch")

        try:
            dex_code = _run(
                [apkanalyzer, "dex", "code", "--class", consumer, str(apk)],
                env,
                work,
            )
        except (OSError, subprocess.SubprocessError):
            raise BytecodeCheckError("apkanalyzer_exec_failed") from None
        if dex_code.returncode != 0 or not dex_code.stdout:
            raise BytecodeCheckError("apkanalyzer_failed")
        dex_inv = _smali_invocations(dex_code.stdout, owners)
        if Counter(dex_inv) != Counter(expected):
            raise BytecodeCheckError("dex_invocations_mismatch")

        defined_classes, defined_methods, dex_files = set(), set(), []
        try:
            archive = zipfile.ZipFile(apk)
        except (OSError, zipfile.BadZipFile):
            raise BytecodeCheckError("apk_open_failed") from None
        with archive:
            dex_names = [n for n in archive.namelist() if re.fullmatch(r"classes\d*\.dex", n)]
            if set(dex_names) != {"classes.dex"} | {
                f"classes{i}.dex" for i in range(2, len(dex_names) + 1)
            } or len(set(dex_names)) != len(dex_names):
                raise BytecodeCheckError("dex_entries_invalid")
            for name in sorted(dex_names):
                try:
                    data = archive.read(name)
                except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
                    raise BytecodeCheckError("dex_read_failed") from None
                dex_path = work / name
                try:
                    dex_path.write_bytes(data)
                except OSError:
                    raise BytecodeCheckError("dex_write_failed") from None
                try:
                    dump = _run([dexdump, str(dex_path)], env, work)
                except (OSError, subprocess.SubprocessError):
                    raise BytecodeCheckError("dexdump_exec_failed") from None
                if dump.returncode != 0 or not dump.stdout:
                    raise BytecodeCheckError("dexdump_failed")
                classes, methods = _dex_definitions(dump.stdout, wanted_classes, wanted_methods)
                if not defined_classes.isdisjoint(classes):
                    raise BytecodeCheckError("duplicate_class_definitions")
                defined_classes |= classes
                defined_methods |= methods
                dex_files.append({"name": name, "sha256": hashlib.sha256(data).hexdigest()})

        if not wanted_classes <= defined_classes:
            raise BytecodeCheckError("missing_class_definitions")
        if not wanted_methods <= defined_methods:
            raise BytecodeCheckError("missing_method_definitions")

        return {
            "probe_class_sha256": probe_sha,
            "class_invocations": [
                {"owner": o, "name": n, "descriptor": d} for o, n, d in sorted(class_inv)
            ],
            "dex_invocations": [
                {"owner": o, "name": n, "descriptor": d} for o, n, d in sorted(dex_inv)
            ],
            "vendor_method_definitions": [
                {"owner": o, "name": n, "descriptor": d} for o, n, d in sorted(defined_methods)
            ],
            "vendor_classes": sorted(vendor),
            "dependency_classes": sorted(deps),
            "dex_files": dex_files,
            "java_positive": {"exit_code": pos_rc},
            "invalid_java_method": {
                "rejected": True,
                "exit_code": neg_rc,
                "reason": "cannot_find_symbol_for_negative_control",
            },
        }
