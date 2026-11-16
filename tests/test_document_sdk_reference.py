"""静态 SDK 参考归一化桥接的离线回归；fixture 全为合成，不依赖本地采集数据。"""

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HTML_ANDROID = (
    "<!doctype html><html><head><title>概述</title></head><body>"
    "<h1>SDK 概述</h1><table><tr><th>字段</th><td>值</td></tr></table>"
    '<pre><code class="language-java">init();</code></pre>'
    '<a href="getting-started.html">下一页</a><img src="logo.png">'
    "</body></html>"
).encode()

HTML_GETTING_STARTED = (
    "<!doctype html><html><head><title>快速接入</title></head><body>"
    "<h2>快速接入</h2><p>步骤说明</p>"
    '<a href="overview-summary.html">返回</a>'
    '<iframe src="sample-video.html"></iframe>'
    "</body></html>"
).encode()

HTML_OVERSEAS = (
    "<!doctype html><html><head><title>Overview</title></head><body>"
    "<h1>Getting started</h1><p>Install the SDK.</p></body></html>"
).encode()


def load_normalizer():
    try:
        from tools.documents import normalize_sdk_reference
    except ImportError:
        try:
            from tools.documents import normalize_sdk_reference
        except ImportError:
            normalize_sdk_reference = None
    return normalize_sdk_reference


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def doc_id_for(url):
    return str(int(sha256(url.encode("utf-8"))[:16], 16))


class SdkReferenceNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.module = load_normalizer()
        self.assertIsNotNone(
            self.module, "归一化模块 tools.documents.normalize_sdk_reference 未实现"
        )

    def catalog(self, output):
        return json.loads((output / "catalog.json").read_text())

    def seed_domestic(self, tmp_path, entries):
        source = tmp_path / "raw-domestic"
        files = {
            "android/overview-summary.html": HTML_ANDROID,
            "android/getting-started.html": HTML_GETTING_STARTED,
        }
        return self.write_source(source, files, entries)

    def write_source(self, source, files, entries):
        enriched = []
        for entry in entries:
            record = dict(entry)
            local_path = record.get("local_path")
            if local_path in files:
                record.setdefault("bytes", len(files[local_path]))
                record.setdefault("sha256", sha256(files[local_path]))
            record.setdefault("discovered_from", ["https://open.ys7.com/doc/zh/android/"])
            enriched.append(record)
        manifest = {
            "scope_roots": ["/doc/zh/android/", "/doc/zh/ios/", "/doc/zh/pc/"],
            "traversal_finished": True,
            "entries": enriched,
        }
        source.mkdir(parents=True)
        for relative, body in files.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        return source

    def run_normalize(self, source, output, region="domestic"):
        return self.module.normalize(source, output, region)

    def test_archived_and_failed_entries_are_both_normalized(self):
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
                {
                    "url": "https://open.ys7.com/doc/zh/android/missing.html",
                    "status": "http_error",
                    "local_path": None,
                },
            ],
        )
        output = source.parent / "out"
        summary = self.run_normalize(source, output)
        self.assertEqual(summary["archived_documents"], 1)
        self.assertEqual(summary["unavailable_documents"], 1)
        catalog = self.catalog(output)
        self.assertEqual({row["status"] for row in catalog}, {"archived", "source_unavailable"})
        failed = next(row for row in catalog if row["status"] == "source_unavailable")
        self.assertEqual(failed["source_url"], "https://open.ys7.com/doc/zh/android/missing.html")
        self.assertNotIn("raw_file", failed)
        self.assertNotIn("content_sha256", failed)

    def test_archived_bytes_and_manifest_are_preserved_verbatim(self):
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
            ],
        )
        output = source.parent / "out"
        self.run_normalize(source, output)
        self.assertEqual((output / "android/overview-summary.html").read_bytes(), HTML_ANDROID)
        self.assertEqual(
            (output / "manifest.json").read_bytes(), (source / "manifest.json").read_bytes()
        )
        row = self.catalog(output)[0]
        wrapper = output / row["raw_file"]
        self.assertEqual(sha256(wrapper.read_bytes()), row["raw_file_sha256"])
        payload = json.loads(wrapper.read_text())
        self.assertEqual(payload["html_content"], HTML_ANDROID.decode("utf-8"))
        self.assertEqual(sha256(payload["html_content"].encode("utf-8")), row["content_sha256"])
        self.assertEqual(row["doc_id"], doc_id_for(row["source_url"]))
        self.assertEqual(row["doc_id_kind"], "local_url_hash_identity")
        self.assertEqual(row["source_kind"], "static_sdk_reference")
        self.assertTrue(str(row["raw_file"]).endswith(f"{row['doc_id']}.source.json"))
        self.assertIsNone(row["encoding_warning"])

    def test_invalid_utf8_bytes_preserved_with_explicit_warning(self):
        broken = HTML_ANDROID.replace(
            "概述".encode(), "概\udcd8".encode("utf-8", errors="surrogatepass")[:1] + b"\xff\xfe"
        )
        source = self.write_source(
            self.id_fixture() / "raw-domestic",
            {
                "android/bad-encoding.html": broken,
            },
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/bad-encoding.html",
                    "status": "ok",
                    "local_path": "android/bad-encoding.html",
                },
            ],
        )
        output = source.parent / "out"
        self.run_normalize(source, output)
        self.assertEqual((output / "android/bad-encoding.html").read_bytes(), broken)
        row = self.catalog(output)[0]
        warning = row["encoding_warning"]
        self.assertEqual(warning["kind"], "source_invalid_utf8_bytes")
        self.assertEqual(warning["canonical_raw_file"], "android/bad-encoding.html")
        self.assertEqual(warning["canonical_raw_sha256"], sha256(broken))
        payload = json.loads((output / row["raw_file"]).read_bytes())
        self.assertEqual(payload["html_content"].count("�"), warning["replacement_characters"])
        self.assertGreater(warning["replacement_characters"], 0)
        self.assertEqual(sha256(payload["html_content"].encode("utf-8")), row["content_sha256"])

    def id_fixture(self):
        import tempfile

        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        return Path(handle.name)

    def assert_rejected(
        self, source, region="domestic", output=None, pattern=None, preexisting=False
    ):
        output = output or source.parent / "out"
        with self.assertRaises(ValueError) as caught:
            self.run_normalize(source, output, region)
        if pattern:
            self.assertRegex(str(caught.exception), pattern)
        if preexisting:
            self.assertEqual([p for p in output.iterdir()], [], "已有空输出目录不得被填入内容")
        else:
            self.assertFalse(output.exists(), "失败输入不得留下输出目录")

    def test_unfinished_traversal_flag_rejected(self):
        # traversal_finished 是采集器声明遍历收尾的显式标记：只有 exact True 才视为
        # 完整采集；False/缺失/null/真值非布尔（如 1）都按未完成输入整体拒绝，
        # 不得仅凭全部已登记条目为终态就放行
        for label, mutate in (
            ("false", lambda manifest: manifest.__setitem__("traversal_finished", False)),
            ("truthy_int", lambda manifest: manifest.__setitem__("traversal_finished", 1)),
            ("null", lambda manifest: manifest.__setitem__("traversal_finished", None)),
            ("missing", lambda manifest: manifest.pop("traversal_finished")),
        ):
            with self.subTest(flag=label):
                fixture = self.id_fixture()
                source = self.seed_domestic(
                    fixture,
                    [
                        {
                            "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                            "status": "ok",
                            "local_path": "android/overview-summary.html",
                        },
                    ],
                )
                manifest = json.loads((source / "manifest.json").read_text())
                mutate(manifest)
                (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
                self.assert_rejected(source)
                self.assertEqual(
                    (source / "android/overview-summary.html").read_bytes(), HTML_ANDROID
                )

    def test_incomplete_status_rejects_manifest(self):
        for status in ("queued", "running", "pending"):
            with self.subTest(status=status):
                source = self.seed_domestic(
                    self.id_fixture(),
                    [
                        {
                            "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                            "status": "ok",
                            "local_path": "android/overview-summary.html",
                        },
                        {
                            "url": "https://open.ys7.com/doc/zh/android/pending.html",
                            "status": status,
                            "local_path": None,
                        },
                    ],
                )
                self.assert_rejected(source, pattern="incomplete|unfinished|terminal")

    def test_unknown_status_rejects_manifest(self):
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/weird.html",
                    "status": "partially_saved",
                    "local_path": "android/weird.html",
                },
            ],
        )
        self.assert_rejected(source)

    def test_duplicate_url_rejected(self):
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/getting-started.html",
                },
            ],
        )
        self.assert_rejected(source, pattern="duplicate|Duplicate")

    def test_sha256_and_bytes_mismatch_rejected(self):
        for field, value in (("sha256", "0" * 64), ("bytes", 1)):
            with self.subTest(field=field):
                source = self.seed_domestic(
                    self.id_fixture(),
                    [
                        {
                            "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                            "status": "ok",
                            "local_path": "android/overview-summary.html",
                            field: value,
                        },
                    ],
                )
                self.assert_rejected(source)

    def test_local_path_escape_and_duplicates_rejected(self):
        for local_path in ("../outside.html", "/etc/passwd", "android/../../out.html"):
            with self.subTest(local_path=local_path):
                source = self.seed_domestic(
                    self.id_fixture(),
                    [
                        {
                            "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                            "status": "ok",
                            "local_path": local_path,
                        },
                    ],
                )
                self.assert_rejected(source)
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
                {
                    "url": "https://open.ys7.com/doc/zh/android/second.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
            ],
        )
        self.assert_rejected(source)

    def test_aliased_local_path_destination_rejected(self):
        # ./ 与重复分隔符在 PurePosixPath 规范化后消失；别名目的地必须按规范化键拒绝
        for alias in (
            "android/./getting-started.html",
            "android//getting-started.html",
        ):
            with self.subTest(alias=alias):
                source = self.seed_domestic(
                    self.id_fixture(),
                    [
                        {
                            "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                            "status": "ok",
                            "local_path": "android/overview-summary.html",
                        },
                        {
                            "url": "https://open.ys7.com/doc/zh/android/getting-started.html",
                            "status": "ok",
                            "local_path": alias,
                            "bytes": len(HTML_GETTING_STARTED),
                            "sha256": sha256(HTML_GETTING_STARTED),
                        },
                    ],
                )
                self.assert_rejected(source)
                self.assertEqual(
                    (source / "android/getting-started.html").read_bytes(),
                    HTML_GETTING_STARTED,
                )

    def test_symlinked_local_path_resolving_to_same_file_rejected(self):
        fixture = self.id_fixture()
        source = self.seed_domestic(
            fixture,
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
                {
                    "url": "https://open.ys7.com/doc/zh/android/linked.html",
                    "status": "ok",
                    "local_path": "android/linked.html",
                    "bytes": len(HTML_ANDROID),
                    "sha256": sha256(HTML_ANDROID),
                },
            ],
        )
        os.symlink("overview-summary.html", source / "android/linked.html")
        self.assert_rejected(source)
        self.assertEqual((source / "android/overview-summary.html").read_bytes(), HTML_ANDROID)

    def test_manifest_symlink_escaping_input_root_rejected(self):
        fixture = self.id_fixture()
        outside = fixture / "outside"
        outside.mkdir()
        outside_manifest = {
            "entries": [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                    "bytes": len(HTML_ANDROID),
                    "sha256": sha256(HTML_ANDROID),
                    "discovered_from": [],
                }
            ]
        }
        outside_manifest_bytes = json.dumps(outside_manifest).encode()
        (outside / "manifest.json").write_bytes(outside_manifest_bytes)
        source = fixture / "raw"
        (source / "android").mkdir(parents=True)
        (source / "android/overview-summary.html").write_bytes(HTML_ANDROID)
        os.symlink(outside / "manifest.json", source / "manifest.json")
        self.assert_rejected(source)
        self.assertEqual((outside / "manifest.json").read_bytes(), outside_manifest_bytes)
        self.assertEqual((source / "android/overview-summary.html").read_bytes(), HTML_ANDROID)

    def test_dangling_symlink_output_rejected_and_untouched(self):
        fixture = self.id_fixture()
        source = self.seed_domestic(
            fixture,
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
            ],
        )
        missing_target = fixture / "not-created"
        output = fixture / "out"
        os.symlink(missing_target, output)
        self.assert_rejected(source)
        self.assertTrue(output.is_symlink())
        self.assertFalse(missing_target.exists())
        self.assertEqual((source / "android/overview-summary.html").read_bytes(), HTML_ANDROID)

    def test_verified_bytes_reused_if_source_changes_mid_run(self):
        # 验证后发布前源文件被改写时，只能发布首次已验证字节，不得二次读取
        fixture = self.id_fixture()
        source = self.seed_domestic(
            fixture,
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
            ],
        )
        tampered = b"<html><body>tampered after validation</body></html>"
        module = self.module
        original_decode = module._decode_source

        def mutating_decode(raw, local_path):
            (source / "android/overview-summary.html").write_bytes(tampered)
            return original_decode(raw, local_path)

        import unittest.mock

        with unittest.mock.patch.object(module, "_decode_source", side_effect=mutating_decode):
            summary = self.run_normalize(source, source.parent / "out")
        self.assertEqual(summary["archived_documents"], 1)
        output = source.parent / "out"
        self.assertEqual((output / "android/overview-summary.html").read_bytes(), HTML_ANDROID)
        row = self.catalog(output)[0]
        self.assertEqual(row["source_receipt"]["sha256"], sha256(HTML_ANDROID))

    def test_existing_output_and_overlapping_paths_rejected(self):
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
            ],
        )
        output = source.parent / "out"
        output.mkdir()
        self.assert_rejected(source, output=output, preexisting=True)
        nested = self.assertRaises(ValueError)
        with nested:
            self.run_normalize(source, source / "inside")
        self.assertFalse((source / "inside").exists())
        with self.assertRaises(ValueError):
            self.run_normalize(source, source)

    def test_unexpected_urls_rejected(self):
        urls = [
            "https://open.ys7.com/doc/zh/android/overview.html#section",
            "https://open.ys7.com/doc/zh/android/overview.html?x=1",
            "https://user@open.ys7.com/doc/zh/android/overview.html",
            "https://other.example/doc/zh/android/overview.html",
            "https://open.ys7.com/help/81",
            "https://open.ys7.com/doc/zh/android/guide",
        ]
        for url in urls:
            with self.subTest(url=url):
                source = self.seed_domestic(
                    self.id_fixture(),
                    [
                        {"url": url, "status": "ok", "local_path": "android/overview-summary.html"},
                    ],
                )
                self.assert_rejected(source)

    def test_malformed_json_and_duplicate_keys_rejected(self):
        for payload in (
            b'{"entries": []} trailing',
            b'{"entries": [], "entries": []}',
            b'{"entries": [NaN]}',
            b'{"entries": [], "note": 1e400}',
        ):
            with self.subTest(payload=payload[:20]):
                fixture = self.id_fixture()
                broken = fixture / "raw"
                broken.mkdir()
                (broken / "manifest.json").write_bytes(payload)
                (broken / "android/overview-summary.html").parent.mkdir(parents=True, exist_ok=True)
                (broken / "android/overview-summary.html").write_bytes(HTML_ANDROID)
                with self.assertRaises(ValueError):
                    self.run_normalize(broken, fixture / "out")
                self.assertFalse((fixture / "out").exists())

    def test_doc_id_collision_is_detected(self):
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
            ],
        )
        module = self.module
        original = module.document_identity
        calls = []

        def colliding(url):
            calls.append(url)
            return (
                doc_id_for("https://open.ys7.com/doc/zh/android/overview-summary.html")
                if len(calls) > 1
                else original(url)
            )

        import unittest.mock

        with unittest.mock.patch.object(module, "document_identity", side_effect=colliding):
            manifest = json.loads((source / "manifest.json").read_text())
            manifest["entries"].append(
                {
                    "url": "https://open.ys7.com/doc/zh/android/getting-started.html",
                    "status": "ok",
                    "local_path": "android/getting-started.html",
                    "bytes": len(HTML_GETTING_STARTED),
                    "sha256": sha256(HTML_GETTING_STARTED),
                    "discovered_from": [],
                }
            )
            (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
            with self.assertRaises(ValueError):
                self.run_normalize(source, source.parent / "out")
        self.assertFalse((source.parent / "out").exists())

    def test_wrapper_path_collision_with_raw_files_rejected(self):
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": f"pages/domestic/{doc_id_for('https://open.ys7.com/doc/zh/android/overview-summary.html')}.source.json",
                },
            ],
        )
        self.assert_rejected(source)

    def test_invalid_region_rejected(self):
        source = self.seed_domestic(
            self.id_fixture(),
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
            ],
        )
        with self.assertRaises(ValueError):
            self.module.normalize(source, source.parent / "out", "cn")

    def test_normalize_derive_verify_integration(self):
        fixture = self.id_fixture()
        source = self.write_source(
            fixture / "raw-overseas",
            {
                "android/overview-summary.html": HTML_OVERSEAS,
            },
            [
                {
                    "url": "https://open.ys7.com/doc/en/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                    "title": "Overseas overview",
                },
            ],
        )
        output = fixture / "snapshot" / "sdk-reference" / "overseas"
        summary = self.module.normalize(source, output, "overseas")
        self.assertEqual(summary["archived_documents"], 1)

        from tools.documents.derive import derive
        from tools.documents.verify import verify

        derivation = derive(output)
        self.assertEqual(derivation["documents"], 1)
        self.assertEqual(derivation["derived_documents"], 1)
        report = verify(output)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["scope_coverage"]["kind"], "static_sdk_reference")
        self.assertEqual(report["archived_bodies"], 1)

    def test_cli_success_and_error_contract(self):
        fixture = self.id_fixture()
        source = self.seed_domestic(
            fixture,
            [
                {
                    "url": "https://open.ys7.com/doc/zh/android/overview-summary.html",
                    "status": "ok",
                    "local_path": "android/overview-summary.html",
                },
            ],
        )
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(repo))
        output = fixture / "cli-out"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.documents.normalize_sdk_reference",
                "--input",
                str(source),
                "--output",
                str(output),
                "--region",
                "domestic",
            ],
            capture_output=True,
            text=True,
            cwd=repo,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["documents"], 1)
        fixed_error = "normalize-sdk-reference: validation failed"
        bad = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.documents.normalize_sdk_reference",
                "--input",
                str(source),
                "--output",
                str(output),
                "--region",
                "domestic",
            ],
            capture_output=True,
            text=True,
            cwd=repo,
            env=env,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stderr.strip(), fixed_error)
        self.assertNotIn("Traceback", bad.stderr)
        # 输出路径或非法取值不得回显到 stderr
        self.assertNotIn(str(output), bad.stderr)
        self.assertNotIn(str(source), bad.stderr)
        # argparse 层错误（非法 region / 缺参）同样收敛为固定消息
        for argv in (
            [
                "--input",
                str(source),
                "--output",
                str(fixture / "other-out"),
                "--region",
                "cn",
            ],
            ["--input", str(source)],
        ):
            bad_args = subprocess.run(
                [sys.executable, "-m", "tools.documents.normalize_sdk_reference", *argv],
                capture_output=True,
                text=True,
                cwd=repo,
                env=env,
            )
            self.assertEqual(bad_args.returncode, 2, argv)
            self.assertEqual(bad_args.stderr.strip(), fixed_error, argv)
            self.assertNotIn("Traceback", bad_args.stderr)
            self.assertNotIn("cn", bad_args.stderr)


if __name__ == "__main__":
    unittest.main()
