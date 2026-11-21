"""Subprocess checks for tools/native/stage_harmony_consumer.py (structural only)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "tools" / "native" / "stage_harmony_consumer.py"
PROBE_REL = Path("examples") / "harmony_sdk_build" / "ProbeAbility.ets"
PROBE = ROOT / PROBE_REL
METHODS = (
    ".setAccessToken(",
    ".getDeviceList(",
    ".setPlayVerifyCode(",
    ".seekPlayback(",
    ".setPlaybackRate(",
)


def load_meta(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def stage(tmp_path):
    repo = tmp_path / "repo"
    probe = repo / PROBE_REL
    probe.parent.mkdir(parents=True)
    shutil.copy2(PROBE, probe)

    def invoke(out):
        return subprocess.run(
            [sys.executable, str(STAGER), "--repo", str(repo), "--output", str(out)],
            cwd=tmp_path,
            env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=120,
        )

    return invoke


def test_cli_generates_expected_tree(stage, tmp_path):
    out = tmp_path / "consumer"
    res = stage(out)
    assert res.returncode == 0, res.stderr

    pkg = load_meta(out / "entry" / "oh-package.json5")
    assert pkg["dependencies"] == {"@ezviz/ezopensdk": "file:./libs/EZOpenSDK.har"}
    libs = out / "entry" / "libs"
    assert libs.is_dir() and not any(libs.iterdir())
    assert not [p for p in out.rglob("*") if p.suffix in (".har", ".hap")]

    app = load_meta(out / "build-profile.json5")["app"]
    product = app["products"][0]
    assert product["compatibleSdkVersion"] == product["targetSdkVersion"] == "5.0.0(12)"
    assert product["runtimeOS"] == "HarmonyOS"
    assert product["buildOption"]["strictMode"]["useNormalizedOHMUrl"] is True
    assert not any("signingConfig" in k for node in (app, product) for k in node)

    entry = load_meta(out / "entry" / "build-profile.json5")
    assert entry["apiType"] == "stageMode"
    native = entry["buildOption"]["nativeLib"]
    assert native["filter"]["enableOverride"] is True

    main_dir = out / "entry" / "src" / "main"
    module = load_meta(main_dir / "module.json5")["module"]
    (ability,) = module["abilities"]
    assert module["mainElement"] == ability["name"] == "ProbeAbility"
    assert not ability.get("exported") and not module.get("requestPermissions")

    src = (main_dir / ability["srcEntry"]).resolve()
    assert src.suffix == ".ets" and main_dir.resolve() in src.parents
    assert src.read_bytes() == PROBE.read_bytes()
    text = src.read_text(encoding="utf-8")
    for needle in METHODS:
        assert needle in text


def test_cli_rerun_fails_and_preserves(stage, tmp_path):
    out = tmp_path / "consumer"
    assert stage(out).returncode == 0
    (out / "sentinel.keep").write_text("mine")
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert stage(out).returncode != 0
    after = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert after == before and (out / "sentinel.keep").read_text() == "mine"


def test_cli_rejects_occupied_and_symlinked_output(stage, tmp_path):
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "sentinel.txt").write_text("stay")
    assert stage(occupied).returncode != 0
    assert (occupied / "sentinel.txt").read_text() == "stay"

    missing = tmp_path / "no-such-target"
    link = tmp_path / "linked-out"
    link.symlink_to(missing)
    assert stage(link).returncode != 0
    assert link.is_symlink() and not missing.exists()
