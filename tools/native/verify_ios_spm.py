#!/usr/bin/env python3
"""Build and inspect a pinned, unsigned iOS SwiftPM consumer without running it."""

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

SCOPE = "docs/coordination/native-ios-initialization/scope.json"
MODULE = "EZOpenSDKFramework"
SCHEME = "NativeSDKProbe"
INSTALL_NAME = "@rpath/EZOpenSDKFramework.framework/EZOpenSDKFramework"
SLICE = "ios-arm64"
SYMBOLS = ("EZOpenSDK", "EZPlayer")


class Failure(Exception):
    """Safe, fixed check label for public diagnostics."""


class XcodeFailure(Failure):
    def __init__(self, phase, exit_code, category):
        self.exit_code = exit_code
        self.category = category
        super().__init__(f"{phase}:{category}:exit={exit_code}")


def require(condition, label):
    if not condition:
        raise Failure(label)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def output(args, *, cwd=None, env=None, timeout=120):
    result = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, errors="replace", timeout=timeout
    )
    require(result.returncode == 0, "command." + Path(str(args[0])).name)
    return result.stdout


def checkout_inputs(scope, checkout, zip_path, temp):
    component = scope["component"]
    pinned = component["commit"]
    require(
        output(["/usr/bin/git", "-C", str(checkout), "rev-parse", "HEAD"]).strip() == pinned,
        "checkout_commit",
    )
    require(
        output(["/usr/bin/git", "-C", str(checkout), "rev-parse", "HEAD^{tree}"]).strip()
        == component["git_tree"],
        "checkout_tree",
    )
    archive = temp / "checkout.tar"
    output(
        ["/usr/bin/git", "archive", "--format=tar", "--output", str(archive), pinned], cwd=checkout
    )
    require(scope["acceptance"]["source_checkout_relative"] == "Vendor/SDK-iOS", "vendor_path")
    vendor = temp / "Vendor/SDK-iOS"
    vendor.mkdir(parents=True)
    with tarfile.open(archive) as exported:
        exported.extractall(vendor, filter="data")
    manifest = vendor / "Package.swift"
    require(manifest.is_file() and sha(manifest) == component["manifest_sha256"], "manifest_sha256")
    require(
        sha(vendor / "Sources/EZOpenSDKWrapper/EZOpenSDKWrapper.swift")
        == component["wrapper_sha256"],
        "wrapper_sha256",
    )
    manifest_text = manifest.read_text(encoding="utf-8")
    require(
        component["zip_url"] in manifest_text
        and component["zip_sha256"] in manifest_text
        and ".binaryTarget(" in manifest_text,
        "manifest_target",
    )
    for rel, resource in component["resources"].items():
        target = vendor / rel
        require(target.resolve().is_relative_to(vendor.resolve()), "resource_path")
        require(Path(rel).is_relative_to("dist/EZOpenSDK/dynamicSDK"), "resource_path")
        require(target.is_file() and not target.is_symlink(), "resource_kind")
        require(
            target.stat().st_size == resource["bytes"] and sha(target) == resource["sha256"],
            "resource_sha256",
        )
    require(
        zip_path.stat().st_size == component["zip_bytes"]
        and sha(zip_path) == component["zip_sha256"],
        "zip_sha256",
    )
    prefix = MODULE + ".xcframework/"
    framework = prefix + SLICE + "/" + MODULE + ".framework/"
    with zipfile.ZipFile(zip_path) as bundle:
        require(
            {prefix + "Info.plist", framework + "Info.plist", framework + MODULE}
            <= set(bundle.namelist()),
            "zip_members",
        )
        info = bundle.read(prefix + "Info.plist")
        require(
            hashlib.sha256(info).hexdigest() == component["xcframework_info_sha256"],
            "xcframework_info_sha256",
        )
        libraries = plistlib.loads(info).get("AvailableLibraries", [])
        require(
            len(libraries) == 1 and libraries[0].get("LibraryIdentifier") == SLICE,
            "xcframework_slice",
        )
        require(
            libraries[0].get("SupportedArchitectures") == ["arm64"], "xcframework_architectures"
        )
        require(libraries[0].get("SupportedPlatform") == "ios", "xcframework_platform")
        require("SupportedPlatformVariant" not in libraries[0], "xcframework_variant")
        framework_info = bundle.read(framework + "Info.plist")
        require(
            hashlib.sha256(framework_info).hexdigest() == component["framework_info_sha256"],
            "framework_info_sha256",
        )
        require(
            plistlib.loads(framework_info).get("CFBundleExecutable") == MODULE,
            "framework_executable",
        )
        require(
            hashlib.sha256(bundle.read(framework + MODULE)).hexdigest()
            == component["binary_sha256"],
            "binary_sha256",
        )
    return vendor


def seed_cache(component, zip_path, source_cache, cache):
    artifacts = cache / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]", "_", component["zip_url"])
    if source_cache is not None:
        source = source_cache / "artifacts" / key
        require(source.is_file() and not source.is_symlink(), "cache_source_kind")
        require(
            source.stat().st_size == component["zip_bytes"]
            and sha(source) == component["zip_sha256"],
            "cache_source_sha256",
        )
    else:
        source = zip_path
        require(
            source.stat().st_size == component["zip_bytes"]
            and sha(source) == component["zip_sha256"],
            "cache_zip_sha256",
        )
    destination = artifacts / key
    shutil.copyfile(source, destination)
    return destination


def _xcode(phase, args, env, temp):
    try:
        proc = subprocess.run(
            args,
            env=env,
            stdin=subprocess.DEVNULL,
            cwd=temp,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise Failure(phase + ": timed out")
    if proc.returncode == 0:
        return
    output = proc.stdout or ""
    category = "xcode_build_failed"
    if phase == "generic_build" and re.search(
        r"(?im)^xcodebuild: error:[^\n]*unable to find a destination matching",
        output,
    ):
        category = "generic_destination_ineligible"
    elif phase == "resolvePackageDependencies":
        category = "package_resolution_failed"
    elif "undefined symbols" in output.lower() or "linker command failed" in output.lower():
        category = "link_failed"
    elif "error:" in output.lower():
        category = "compile_failed"
    raise XcodeFailure(phase, proc.returncode, category)


def build_consumer(scope, repo, temp, cache):
    acc, comp = scope["acceptance"], scope["component"]
    src, pbx = repo / acc["source"], repo / acc["project"]
    require(src.is_file() and pbx.is_file(), "consumer: acceptance inputs missing")
    proj = temp / "NativeSDKProbe.xcodeproj"
    proj.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, temp / "Probe.m")
    shutil.copyfile(pbx, proj / "project.pbxproj")
    box = {name: temp / name for name in ("Tmp", "User", "Cache", "Config")}
    for path in list(box.values()) + [box["Cache"] / "clang"]:
        path.mkdir(parents=True, exist_ok=True)
    netrc = box["User"] / "netrc"
    netrc.write_text("")
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LC_ALL": "C",
        "CFFIXED_USER_HOME": str(box["User"]),
        "TMPDIR": str(box["Tmp"]) + "/",
        "XDG_CACHE_HOME": str(box["Cache"]),
        "XDG_CONFIG_HOME": str(box["Config"]),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "NETRC": str(netrc),
    }
    if os.environ.get("DEVELOPER_DIR"):
        env["DEVELOPER_DIR"] = os.environ["DEVELOPER_DIR"]
    data, pkgs = temp / "DerivedData", temp / "SourcePackages"
    base = [
        "xcrun",
        "xcodebuild",
        "-project",
        str(proj),
        "-scheme",
        SCHEME,
        "-configuration",
        "Debug",
        "-sdk",
        "iphoneos",
        "-destination",
        "generic/platform=iOS",
        "-derivedDataPath",
        str(data),
        "-clonedSourcePackagesDirPath",
        str(pkgs),
        "-packageCachePath",
        str(cache),
        "-scmProvider",
        "system",
        "-packageAuthorizationProvider",
        "netrc",
        "-skipPackageUpdates",
    ]
    common = [
        "-clonedSourcePackagesDirPath",
        str(pkgs),
        "-packageCachePath",
        str(cache),
        "-scmProvider",
        "system",
        "-packageAuthorizationProvider",
        "netrc",
        "-skipPackageUpdates",
    ]
    resolve_cmd = [
        "xcrun",
        "xcodebuild",
        "-project",
        str(proj),
        "-scheme",
        SCHEME,
        "-configuration",
        "Debug",
        "-sdk",
        "iphoneos",
        "-derivedDataPath",
        str(data),
        *common,
        "-resolvePackageDependencies",
    ]
    _xcode("resolvePackageDependencies", resolve_cmd, env, temp)
    flags = [
        "ARCHS=arm64",
        "IPHONEOS_DEPLOYMENT_TARGET=13.0",
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
        "CODE_SIGN_IDENTITY=",
        "ONLY_ACTIVE_ARCH=NO",
        "COMPILER_INDEX_STORE_ENABLE=NO",
        "CLANG_MODULE_CACHE_PATH=" + str(box["Cache"] / "clang"),
    ]
    products = data / "Build/Products"
    intermediates = data / "Build/Intermediates.noindex"
    try:
        _xcode("generic_build", base + ["build"] + flags, env, temp)
        route = {"selection": "scheme_generic_ios", "generic_exit": 0}
    except XcodeFailure as exc:
        if exc.category != "generic_destination_ineligible":
            raise
        products = temp / "TargetBuild/Products"
        intermediates = temp / "TargetBuild/Intermediates"
        targetcmd = [
            "xcrun",
            "xcodebuild",
            "-project",
            str(proj),
            "-target",
            SCHEME,
            "-sdk",
            "iphoneos",
            "-configuration",
            "Debug",
            *common,
            "SYMROOT=" + str(products),
            "OBJROOT=" + str(intermediates),
            "DSTROOT=" + str(temp / "TargetBuild/Install"),
            "SHARED_PRECOMPS_DIR=" + str(temp / "Cache/PrecompiledHeaders"),
            "SWIFT_MODULE_CACHE_PATH=" + str(temp / "Cache/SwiftModules"),
            *flags,
            "build",
        ]
        _xcode("device_target_build", targetcmd, env, temp)
        route = {
            "selection": "xcode_target_iphoneos",
            "generic_exit": exc.exit_code,
            "generic_reason": "generic_destination_ineligible",
        }
    state = json.loads((pkgs / "workspace-state.json").read_text())
    arts = (state.get("object") or {}).get("artifacts")
    require(isinstance(arts, list), "verify: artifacts missing")
    found = [a for a in arts if a.get("targetName") == MODULE]
    require(len(found) == 1, "verify: expected one %s artifact" % MODULE)
    art = found[0]
    want = {"type": "remote", "url": comp["zip_url"], "checksum": comp["zip_sha256"]}
    require(art.get("source") == want, "verify: artifact source mismatch")
    apath = Path(art.get("path") or "")
    apath = (apath if apath.is_absolute() else pkgs / apath).resolve()
    require(
        (pkgs / "artifacts").resolve() in apath.parents and MODULE + ".xcframework" in str(apath),
        "verify: artifact outside artifacts dir",
    )
    binary = apath / SLICE / (MODULE + ".framework") / MODULE
    require(
        binary.is_file() and sha(binary) == comp["binary_sha256"],
        "verify: framework binary sha256 mismatch",
    )
    app = products / "Debug-iphoneos" / (SCHEME + ".app")
    require((app / SCHEME).is_file(), "verify: missing built executable")
    objs = [
        o
        for o in intermediates.rglob("Probe.o")
        if o.parent.name == "arm64" and o.parent.parent.name == "Objects-normal"
    ]
    require(len(objs) == 1, "verify: expected one Probe.o")
    return app, objs[0], apath, env, route


def inspect_product(scope, app, obj, artifact, env):
    X = "/usr/bin/xcrun"
    app, obj, artifact = Path(app), Path(obj), Path(artifact)
    fw = artifact / SLICE / (MODULE + ".framework")
    exe = [app / SCHEME, app / "Frameworks" / (MODULE + ".framework") / MODULE, fw / MODULE]

    def xrun(*args):
        return output([X, *map(str, args)], env=env, timeout=120)

    meta = []
    for p in exe:
        require(xrun("lipo", "-archs", p).split() == ["arm64"], "arch")
        v = xrun("vtool", "-show-build", p)
        pl, mn = re.findall(r"platform\s+(\S+)", v), re.findall(r"minos\s+(\S+)", v)
        require(pl and set(pl) == {"IOS"} and mn and set(mn) == {"13.0"}, "build-version")
        meta.append(("arm64", pl[0], mn[0]))

    deps = [
        line.strip().split(" (")[0]
        for line in xrun("otool", "-L", exe[0]).splitlines()
        if "weak" not in line
    ]
    require(INSTALL_NAME in deps, "install-name dependency")
    ol = xrun("otool", "-l", exe[0])
    require("LC_CODE_SIGNATURE" not in ol, "unsigned")
    blk = [b for b in ol.split("Load command") if "name " + INSTALL_NAME in b]
    require(
        blk
        and all(
            "LC_LOAD_WEAK_DYLIB" not in b and re.search(r"cmd\s+LC_LOAD_DYLIB\b", b) for b in blk
        ),
        "load-dylib",
    )
    h = xrun("otool", "-hv", exe[0])
    require(re.search(r"\bEXECUTE\b", h) and re.search(r"\bPIE\b", h), "pie-executable")

    def undef(p):
        n = xrun("nm", "-m", p)
        return {
            s
            for s in SYMBOLS
            if any(
                "(undefined) external" in line
                and "weak" not in line
                and re.search(re.escape("_OBJC_CLASS_$_" + s) + r"(\s|$)", line)
                and (p == obj or "(from EZOpenSDKFramework)" in line)
                for line in n.splitlines()
            )
        }

    require(undef(exe[0]) == set(SYMBOLS) and undef(obj) == set(SYMBOLS), "sdk-class-refs")

    def uid(p):
        m = re.search(r"UUID:\s*([0-9A-Fa-f-]+)\s*\(arm64\)", xrun("dwarfdump", "--uuid", p))
        require(m, "uuid")
        return m.group(1)

    def ident(p):
        require(
            INSTALL_NAME in [line.strip() for line in xrun("otool", "-D", p).splitlines()],
            "id-dylib",
        )
        return INSTALL_NAME

    ue, uo = uid(exe[1]), uid(exe[2])
    ie, io_ = ident(exe[1]), ident(exe[2])
    se, so = sha(exe[1]), sha(exe[2])
    require(se == so or (ue == uo and meta[1] == meta[2] and ie == io_), "vendor-original")

    stubs = set(xrun("nm", "-u", obj).splitlines())
    sels = []
    for e in scope["ios_header_symbols"].values():
        selector = e["selector"]
        require("_objc_msgSend$" + selector in stubs, "selector-dispatch")
        hp = fw / "Headers" / e["header"]
        require(sha(hp) == e["header_sha256"], "header-sha")
        lines = hp.read_text(encoding="utf-8").splitlines()
        declaration = " ".join(lines[e["line"] - 1 :]).split(";", 1)[0].strip()
        labels = re.findall(r"([A-Za-z_][A-Za-z0-9_]*):", declaration)
        require(
            declaration.startswith(e["dispatch"])
            and "".join(label + ":" for label in labels) == selector,
            "header-decl",
        )
        sels.append(selector)

    return {
        "architecture": "arm64",
        "platform": "IOS",
        "minos": "13.0",
        "install_name": INSTALL_NAME,
        "sdk_class_refs": ["_OBJC_CLASS_$_" + s for s in SYMBOLS],
        "selectors": sels,
        "app_sha256": sha(exe[0]),
        "consumer_sha256": sha(obj),
        "embedded_sha256": se,
        "original_sha256": so,
        "uuid": ue,
        "signed": False,
        "simulator_slice": False,
    }


def check_resources(component, root):
    pins = component["resources"]
    require(isinstance(pins, dict) and pins, "resource.pins")
    prefix = Path("dist/EZOpenSDK/dynamicSDK")
    root = Path(root)
    root_resolved = root.resolve()
    observed = {}
    groups = {}
    for original, pin in pins.items():
        original = str(original)
        source = Path(original)
        require(not source.is_absolute(), "resource.path:" + original)
        try:
            rel = source.relative_to(prefix)
        except ValueError:
            rel = None
        require(
            rel is not None and bool(rel.parts) and ".." not in rel.parts,
            "resource.path:" + original,
        )
        installed = root / rel
        require(not installed.is_symlink(), "resource.link:" + rel.as_posix())
        try:
            installed.resolve().relative_to(root_resolved)
        except ValueError:
            require(False, "resource.link:" + rel.as_posix())
        require(installed.is_file(), "resource.missing:" + rel.as_posix())
        require(
            installed.stat().st_size == int(pin["bytes"]),
            "resource.size:" + rel.as_posix(),
        )
        digest = sha(installed)
        require(
            digest == str(pin["sha256"]).lower(),
            "resource.digest:" + rel.as_posix(),
        )
        observed[rel.as_posix()] = digest
        groups.setdefault(rel.parts[0], set()).add(rel)
    require(len(observed) == int(component["resource_files"]), "resource.count")
    require(len(groups) == int(component["resource_groups"]), "resource.groups")
    for first in sorted(groups):
        if not first.endswith(".bundle"):
            continue
        bundle_root = root / first
        require(
            bundle_root.is_dir() and not bundle_root.is_symlink(),
            "resource.bundle:" + first,
        )
        actual = set()
        for node in bundle_root.rglob("*"):
            if node.is_symlink():
                try:
                    node.resolve().relative_to(root_resolved)
                except ValueError:
                    require(
                        False,
                        "resource.link:" + node.relative_to(root).as_posix(),
                    )
                continue
            if node.is_file():
                actual.add(node.relative_to(bundle_root).as_posix())
        expected = {entry.relative_to(first).as_posix() for entry in groups[first]}
        require(actual == expected, "resource.bundle:" + first)
    return dict(sorted(observed.items()))


def negative_controls(scope, temp, app, artifact, env):
    component = scope["component"]
    prefix = Path("dist/EZOpenSDK/dynamicSDK")
    app = Path(app)
    temp = Path(temp)
    temp.mkdir(parents=True, exist_ok=True)
    relatives = sorted(
        Path(str(original)).relative_to(prefix).as_posix() for original in component["resources"]
    )
    require(relatives, "negative.resources")
    changed = temp / "ChangedResources"
    changed.mkdir(parents=True, exist_ok=True)
    os.chmod(changed, 0o700)
    for rel in relatives:
        source = app / rel
        require(
            source.is_file() and not source.is_symlink(),
            "negative.resource.missing:" + rel,
        )
        target = changed / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    bad_relative = relatives[0]
    victim = changed / bad_relative
    blob = bytearray(victim.read_bytes())
    require(len(blob) > 0, "negative.resource.empty")
    blob[0] ^= 0xFF
    victim.write_bytes(bytes(blob))
    require(victim.stat().st_size == len(blob), "negative.resource.size")
    expected_reason = "resource.digest:" + bad_relative
    try:
        check_resources(component, changed)
    except Failure as exc:
        detail = exc.args[0] if exc.args else str(exc)
        require(detail == expected_reason, "negative.resource.reason")
    else:
        require(False, "negative.resource.accepted")

    sdk = output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"], env=env).strip()
    require(bool(sdk), "negative.sdk")
    module_cache = temp / "ModuleCache"
    module_cache.mkdir(parents=True, exist_ok=True)
    command = [
        "/usr/bin/xcrun",
        "--sdk",
        "iphoneos",
        "clang",
        "-x",
        "objective-c",
        "-target",
        "arm64-apple-ios13.0",
        "-isysroot",
        sdk,
        "-fobjc-arc",
        "-fblocks",
        "-fmodules",
        "-fmodules-cache-path=" + str(module_cache),
        "-Werror=objc-method-access",
        "-Werror=incompatible-function-pointer-types",
        "-F",
        str(Path(artifact) / SLICE),
        "-fsyntax-only",
    ]

    def _compile(path):
        return subprocess.run(
            command + [str(path)],
            env=env,
            timeout=120,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    probe = temp / "Probe.m"
    require(probe.is_file(), "negative.probe.missing")
    baseline = _compile(probe)
    require(baseline.returncode == 0, "negative.probe.baseline")
    probe_text = probe.read_text(encoding="utf-8")
    require(probe_text.count("setPlayVerifyCode:") == 1, "negative.probe.selector")
    invalid = temp / "InvalidSelector.m"
    invalid.write_text(probe_text.replace("setPlayVerifyCode:", "sp538RejectedSelector:"))
    bad = _compile(invalid)
    require(bad.returncode != 0, "negative.compiler.accepted")
    diagnostics = [line for line in (bad.stderr or "").splitlines() if "error:" in line]
    require(
        any(
            "sp538RejectedSelector" in line and ("selector" in line or "not found" in line)
            for line in diagnostics
        ),
        "negative.compiler.diagnostic",
    )
    return {
        "resource": {
            "reason": expected_reason,
            "relative": bad_relative,
            "sources": len(relatives),
        },
        "compiler": {
            "reason": "unknown_objc_selector",
            "exit": bad.returncode,
            "sources": len([probe, invalid]),
        },
    }


def _emit(payload):
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _success(component, temp, link, resources, controls, route, env):
    temp = Path(temp)
    versions = output(["/usr/bin/xcodebuild", "-version"], env=env).splitlines()
    require(len(versions) >= 2, "xcode_version")
    require(re.fullmatch(r"Xcode [0-9.]+", versions[0]), "xcode_version")
    require(re.fullmatch(r"Build version [A-Za-z0-9.]+", versions[1]), "xcode_build")
    xcode_version = versions[0]
    xcode_build = versions[1].split()[-1]
    sdk_version = output(
        ["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-version"], env=env
    ).strip()
    require(re.fullmatch(r"[0-9.]+", sdk_version), "sdk_version")
    groups = len({Path(p).parts[0] for p in resources})
    return {
        "passed": True,
        "component": {
            "tag": component["tag"],
            "commit": component["commit"],
            "git_tree": component["git_tree"],
            "manifest_sha256": component["manifest_sha256"],
            "wrapper_sha256": component["wrapper_sha256"],
            "zip_sha256": component["zip_sha256"],
            "remote_url": component["zip_url"],
            "remote_checksum": component["zip_sha256"],
            "release_archive_sha": component["zip_sha256"],
            "binary_sha256": component["binary_sha256"],
            "observed_slice": "ios-arm64",
            "swiftpm_remote_binary_resolved": True,
        },
        "consumer": {
            "probe_sha256": sha(temp / "Probe.m"),
            "project_sha256": sha(temp / (SCHEME + ".xcodeproj") / "project.pbxproj"),
            "target": SCHEME,
            "xcode_version": xcode_version,
            "xcode_build": xcode_build,
            "iphoneos_sdk": sdk_version,
        },
        "build_route": route,
        "link": link,
        "resources": {
            "groups": groups,
            "files": len(resources),
            "sha256_by_path": resources,
        },
        "negative_controls": controls,
        "signed": False,
        "installed": False,
        "executed": False,
        "simulator_slice": False,
        "simulator_run": False,
        "device_requests": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="verify_ios_spm",
        description="Offline verifier for the iOS SwiftPM binary-target consumer build.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Repository root containing the scope manifest.",
    )
    parser.add_argument(
        "--source-checkout",
        type=Path,
        required=True,
        help="Local checkout of the SDK source component.",
    )
    parser.add_argument(
        "--xcframework-archive",
        type=Path,
        required=True,
        help="Downloaded XCFramework zip archive.",
    )
    parser.add_argument(
        "--package-cache",
        type=Path,
        default=None,
        help="Optional pre-seeded SwiftPM package cache.",
    )
    args = parser.parse_args(argv)

    try:
        scope = json.loads((args.repo / SCOPE).read_text(encoding="utf-8"))
        require(scope["cohort_id"] == "NATIVE-IOS-SPM-534-001", "scope_id")
        with tempfile.TemporaryDirectory(prefix="ezviz-ios-spm-") as handle:
            temp = Path(handle).resolve()
            checkout_inputs(scope, args.source_checkout, args.xcframework_archive, temp)
            component = scope["component"]
            cache_dir = temp / "PackageCache"
            seed_cache(component, args.xcframework_archive, args.package_cache, cache_dir)
            app, obj, artifact, env, route = build_consumer(
                scope, args.repo.resolve(), temp, cache_dir
            )
            link = inspect_product(scope, app, obj, artifact, env)
            resources = check_resources(component, app)
            controls = negative_controls(scope, temp, app, artifact, env)
            result = _success(component, temp, link, resources, controls, route, env)
    except Failure as exc:
        _emit({"passed": False, "check": str(exc), "rc": 1})
        return 1
    except subprocess.TimeoutExpired:
        _emit({"passed": False, "check": "subprocess_timeout", "diagnostics": {}, "rc": 1})
        return 1
    except (
        OSError,
        KeyError,
        ValueError,
        IndexError,
        TypeError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ):
        _emit({"passed": False, "check": "invalid_or_missing_input", "diagnostics": {}, "rc": 1})
        return 1
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
