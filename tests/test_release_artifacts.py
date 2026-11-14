import importlib
import io
import shutil
import stat
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest

NAME = "ezviz_unified"
VERSION = "0.1.0a1"
PREFIX = f"{NAME}-{VERSION}"
METADATA = f"""Metadata-Version: 2.4
Name: ezviz-unified
Version: {VERSION}
Requires-Python: >=3.12
License-Expression: MIT
Description-Content-Type: text/markdown

# Test SDK
"""


def artifacts(tmp_path, *, wheel_extra=None, sdist_extra=None, metadata=METADATA, sdist_omit=()):
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname="ezviz-unified"\nversion="0.1.0a1"\nrequires-python=">=3.12"\nlicense="MIT"\n'
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / f"{PREFIX}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{PREFIX}.dist-info/METADATA", metadata)
        archive.writestr(f"{PREFIX}.dist-info/licenses/LICENSE", "MIT")
        archive.writestr("ezviz_unified/py.typed", "")
        archive.writestr("ezviz_unified/__init__.py", "")
        if wheel_extra:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(wheel_extra, "secret payload must not appear in errors")
    sdist = dist / f"{PREFIX}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name, value in [
            ("PKG-INFO", METADATA),
            ("LICENSE", "MIT"),
            ("src/ezviz_unified/py.typed", ""),
            ("src/ezviz_unified.egg-info/PKG-INFO", METADATA),
            ("tests/conftest.py", ""),
            ("tests/test_offline_policy.py", ""),
            ("tests/test_sdk_example.py", ""),
        ]:
            if name in sdist_omit:
                continue
            member = tarfile.TarInfo(f"{PREFIX}/{name}")
            encoded = value.encode()
            member.size = len(encoded)
            archive.addfile(member, io.BytesIO(encoded))
        if sdist_extra:
            if isinstance(sdist_extra, str):
                sdist_extra = tarfile.TarInfo(f"{PREFIX}/{sdist_extra}")
            archive.addfile(sdist_extra)
    return dist, project


def check(dist, project):
    assert Path("tools/release/check_artifacts.py").exists(), "Artifact checker missing"
    return importlib.import_module("tools.release.check_artifacts").check_artifacts(dist, project)


def test_valid_metadata_does_not_claim_installation_or_runtime_success(tmp_path):
    result = check(*artifacts(tmp_path))
    assert result["name"] == "ezviz-unified" and result["version"] == VERSION
    assert result["validation"] == "metadata_and_contents_only"
    assert {x["kind"] for x in result["artifacts"]} == {"wheel", "sdist"}
    assert all(len(x["sha256"]) == 64 for x in result["artifacts"])


@pytest.mark.parametrize(
    "extra",
    [
        "harness/docdiff.py",
        "data/private.json",
        ".env.production",
        "../outside.py",
        "ezviz_unified/./outside.py",
        "ezviz_unified\\outside.py",
        "ezviz_unified/py.typed",
    ],
)
def test_invalid_wheel_members_are_rejected_without_dumping_content(tmp_path, extra):
    with pytest.raises(ValueError) as caught:
        check(*artifacts(tmp_path, wheel_extra=extra))
    assert "secret payload" not in str(caught.value)


@pytest.mark.parametrize(
    "extra", ["tools/live/verify.py", "tests/test_harness.py", ".env", "../outside.py"]
)
def test_sdist_does_not_include_private_or_maintenance_files(tmp_path, extra):
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, sdist_extra=extra))


def test_archive_links_are_rejected(tmp_path):
    link = zipfile.ZipInfo("ezviz_unified/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, wheel_extra=link))


def test_sdist_hardlink_is_rejected(tmp_path):
    link = tarfile.TarInfo(f"{PREFIX}/link")
    link.type = tarfile.LNKTYPE
    link.linkname = f"{PREFIX}/PKG-INFO"
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, sdist_extra=link))


@pytest.mark.parametrize(
    "altered",
    [
        METADATA.replace(VERSION, "9.9.9"),
        METADATA.replace("MIT", "Apache-2.0"),
        METADATA.replace("Name: ezviz-unified", "Name: ezviz-unified\nName: other-package"),
        METADATA.replace(">=3.12", ">=3.8"),
        METADATA.split("\n\n")[0] + "\n\n",
    ],
)
def test_metadata_disagreement_is_rejected(tmp_path, altered):
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, metadata=altered))


def test_mixed_builds_are_rejected(tmp_path):
    dist, project = artifacts(tmp_path)
    shutil.copyfile(next(dist.glob("*.whl")), dist / "old-9.9.9-py3-none-any.whl")
    with pytest.raises(ValueError):
        check(dist, project)


def test_artifact_filename_must_match_metadata(tmp_path):
    dist, project = artifacts(tmp_path)
    next(dist.glob("*.whl")).rename(dist / "wrong_package-9.9.9-py3-none-any.whl")
    with pytest.raises(ValueError):
        check(dist, project)


@pytest.mark.parametrize(
    "extra",
    [
        "unexpected_module.py",
        "evals/maintenance/private.json",
        "reports/summary.json",
        "AGENTS.md",
        ".github/workflows/ci.yml",
        "other_package/__init__.py",
        "other_package-1.0.dist-info/METADATA",
    ],
)
def test_wheel_rejects_top_level_entries_outside_package_and_dist_info(tmp_path, extra):
    with pytest.raises(ValueError) as caught:
        check(*artifacts(tmp_path, wheel_extra=extra))
    assert "secret payload" not in str(caught.value)


def test_wheel_rejects_unexpected_top_level_directory_member(tmp_path):
    entry = zipfile.ZipInfo("reports/")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFDIR | 0o755) << 16
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, wheel_extra=entry))


@pytest.mark.parametrize("extra", ["ezviz_unified", f"{PREFIX}.dist-info"])
def test_wheel_rejects_top_level_file_members(tmp_path, extra):
    entry = zipfile.ZipInfo(extra)
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | 0o644) << 16
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, wheel_extra=entry))


@pytest.mark.parametrize("extra", ["ezviz_unified", f"{PREFIX}.dist-info"])
def test_wheel_allows_explicit_package_and_dist_info_directories(tmp_path, extra):
    entry = zipfile.ZipInfo(f"{extra}/")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFDIR | 0o755) << 16
    result = check(*artifacts(tmp_path, wheel_extra=entry))
    wheel = next(item for item in result["artifacts"] if item["kind"] == "wheel")
    assert wheel["member_count"] == 5


@pytest.mark.parametrize(
    "extra",
    [
        "evals/maintenance/private.json",
        "reports/summary.json",
        "AGENTS.md",
        "UNKNOWN.txt",
        "src/other_package/__init__.py",
        "src/unexpected_module.py",
        "tests/test_sdk_example/helper.py",
        "README.md/nested.py",
    ],
)
def test_sdist_rejects_entries_outside_the_allowed_layout(tmp_path, extra):
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, sdist_extra=extra))


def test_sdist_rejects_unexpected_root_directory_member(tmp_path):
    directory = tarfile.TarInfo(f"{PREFIX}/evals")
    directory.type = tarfile.DIRTYPE
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, sdist_extra=directory))


@pytest.mark.parametrize("name", ["src/ezviz_unified", "src/ezviz_unified.egg-info"])
def test_sdist_rejects_package_paths_as_files(tmp_path, name):
    entry = tarfile.TarInfo(f"{PREFIX}/{name}")
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, sdist_extra=entry))


@pytest.mark.parametrize("name", ["src/ezviz_unified", "src/ezviz_unified.egg-info"])
def test_sdist_allows_explicit_package_directories(tmp_path, name):
    entry = tarfile.TarInfo(f"{PREFIX}/{name}")
    entry.type = tarfile.DIRTYPE
    result = check(*artifacts(tmp_path, sdist_extra=entry))
    sdist = next(item for item in result["artifacts"] if item["kind"] == "sdist")
    assert sdist["member_count"] == 8


@pytest.mark.parametrize(
    "missing",
    ["tests/conftest.py", "tests/test_offline_policy.py", "tests/test_sdk_example.py"],
)
def test_sdist_missing_required_tests_are_rejected(tmp_path, missing):
    with pytest.raises(ValueError):
        check(*artifacts(tmp_path, sdist_omit={missing}))
