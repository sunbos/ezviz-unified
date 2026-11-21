#!/usr/bin/env python3
"""Stage a binary-free HarmonyOS consumer project without running native tools."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_PROBE = "examples/harmony_sdk_build/ProbeAbility.ets"

JSON_FILES = {
    "build-profile.json5": {
        "app": {
            "products": [
                {
                    "name": "default",
                    "runtimeOS": "HarmonyOS",
                    "compatibleSdkVersion": "5.0.0(12)",
                    "targetSdkVersion": "5.0.0(12)",
                    "buildOption": {"strictMode": {"useNormalizedOHMUrl": True}},
                }
            ],
            "buildModeSet": [{"name": "debug"}, {"name": "release"}],
        },
        "modules": [
            {
                "name": "entry",
                "srcPath": "./entry",
                "targets": [{"name": "default", "applyToProducts": ["default"]}],
            }
        ],
    },
    "hvigor/hvigor-config.json5": {"modelVersion": "5.0.0", "dependencies": {}},
    "oh-package.json5": {
        "modelVersion": "5.0.0",
        "name": "ezvizprobe",
        "version": "1.0.0",
        "dependencies": {},
    },
    "AppScope/app.json5": {
        "app": {
            "bundleName": "org.example.ezvizprobe",
            "vendor": "example",
            "versionCode": 1,
            "versionName": "1.0.0",
            "icon": "$media:app_icon",
            "label": "$string:app_name",
        }
    },
    "AppScope/resources/base/element/string.json": {
        "string": [{"name": "app_name", "value": "EZVIZ Probe"}]
    },
    "entry/build-profile.json5": {
        "apiType": "stageMode",
        "buildOption": {"nativeLib": {"filter": {"enableOverride": True}}},
        "targets": [{"name": "default"}],
    },
    "entry/oh-package.json5": {
        "name": "entry",
        "version": "1.0.0",
        "dependencies": {"@ezviz/ezopensdk": "file:./libs/EZOpenSDK.har"},
    },
    "entry/src/main/module.json5": {
        "module": {
            "name": "entry",
            "type": "entry",
            "mainElement": "ProbeAbility",
            "deviceTypes": ["phone"],
            "deliveryWithInstall": True,
            "installationFree": False,
            "pages": "$profile:main_pages",
            "abilities": [
                {
                    "name": "ProbeAbility",
                    "srcEntry": "./ets/probeability/ProbeAbility.ets",
                    "exported": False,
                }
            ],
        }
    },
    "entry/src/main/resources/base/profile/main_pages.json": {"src": ["pages/Index"]},
}

TEXT_FILES = {
    "hvigorfile.ts": "import { appTasks } from '@ohos/hvigor-ohos-plugin';\n"
    "\n"
    "export default {\n"
    "  system: appTasks,\n"
    "  plugins: [],\n"
    "};\n",
    "entry/hvigorfile.ts": "import { hapTasks } from '@ohos/hvigor-ohos-plugin';\n"
    "\n"
    "export default {\n"
    "  system: hapTasks,\n"
    "  plugins: [],\n"
    "};\n",
    "AppScope/resources/base/media/app_icon.svg": '<svg xmlns="http://www.w3.org/2000/svg" '
    'width="40" height="40" viewBox="0 0 40 '
    '40"><circle cx="20" cy="20" r="16" '
    'fill="#4A7DE0"/></svg>\n',
    "entry/src/main/ets/pages/Index.ets": "@Entry\n"
    "@Component\n"
    "struct Index {\n"
    "  build() {\n"
    "    Column() {\n"
    "      Text('Build-only probe')\n"
    "    }\n"
    "  }\n"
    "}\n",
}


def _write_project(stage: Path, probe: Path) -> None:
    """Write the frozen template into ``stage`` and copy the probe source.

    Only template JSON/text entries and the exact bytes of ``probe`` are
    emitted; no vendor, SDK, or other files are read, copied, or fetched.
    """
    stage.mkdir(parents=True, exist_ok=True)
    for relative_path, data in JSON_FILES.items():
        target = stage / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        target.write_bytes(payload.encode("utf-8"))
    for relative_path, text in TEXT_FILES.items():
        target = stage / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not text.endswith("\n"):
            text += "\n"
        target.write_bytes(text.encode("utf-8"))
    probe_target = stage / "entry" / "src" / "main" / "ets" / "probeability" / "ProbeAbility.ets"
    probe_target.parent.mkdir(parents=True, exist_ok=True)
    probe_target.write_bytes(probe.read_bytes())
    (stage / "entry" / "libs").mkdir(parents=True, exist_ok=True)


def stage_consumer(repo: Path, output: Path) -> None:
    """Stage a template-only consumer from ``repo`` at new path ``output``.

    The project is fully written in a temporary sibling directory and then
    published without ever overwriting an existing file, directory, or
    symlink.
    """
    probe = repo / _PROBE
    if not probe.is_file() or probe.resolve() != repo.resolve() / _PROBE:
        raise FileNotFoundError(f"missing probe source: {_PROBE}")
    if os.path.lexists(output):
        raise FileExistsError(f"output path already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = output.parent.resolve()
    final = parent / output.name
    if os.path.lexists(final):
        raise FileExistsError(f"output path already exists: {final}")
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        stage = Path(tmp) / "project"
        _write_project(stage, probe)
        if os.name == "nt":
            # On Windows, renaming onto a nonexistent leaf never overwrites.
            os.rename(stage, final)
        else:
            # Reserve the leaf with an empty directory we own, then atomically
            # swap the completed stage into place.
            final.mkdir(exist_ok=False)
            try:
                os.replace(stage, final)
            except BaseException:
                try:
                    final.rmdir()  # succeeds only while still ours and empty
                except OSError:
                    pass
                raise


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and stage the template-only consumer project."""
    parser = argparse.ArgumentParser(
        description=(
            "Stage a HarmonyOS consumer project generated from a frozen "
            "public template plus the repository probe source. The output is "
            "template files only: it contains no SDK and no native build is "
            "performed or implied."
        )
    )
    parser.add_argument(
        "--repo",
        required=True,
        type=Path,
        help=f"repository root containing {_PROBE}",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new directory to create; must not already exist in any form",
    )
    args = parser.parse_args(argv)
    try:
        stage_consumer(args.repo, args.output)
    except OSError as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
