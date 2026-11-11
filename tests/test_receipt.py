# -*- coding: utf-8 -*-
"""Unit tests for scripts/receipt.py (standard library only).

    python -m unittest discover -s tests -v
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("receipt", REPO / "scripts" / "receipt.py")
receipt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(receipt)


class ReceiptTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "project"
        self.cli("init", "--title", "测试任务")

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, command, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = receipt.main([command, "--root", str(self.root), *extra])
        return code, buf.getvalue()

    def add(self, cid, check, *extra):
        return self.cli("add", "--id", cid, "--claim", f"承诺 {cid}", "--check", check, *extra)

    def load_receipt(self):
        return json.loads((self.root / ".receipt" / "receipt.json").read_text(encoding="utf-8"))

    def test_init_creates_contract_and_task(self):
        self.assertTrue((self.root / ".receipt" / "contract.json").is_file())
        self.assertTrue((self.root / "TASK.md").is_file())
        self.assertEqual(len(receipt.load_contract(self.root)["items"]), 0)

    def test_safe_rel_rejects_traversal(self):
        with self.assertRaises(ValueError):
            receipt.safe_rel(self.root, "../outside.txt")
        with self.assertRaises(ValueError):
            receipt.safe_rel(self.root, "a/../../x")

    def test_all_pass(self):
        (self.root / "report.md").write_text("# 报告\n\n## 结论\n\n完成了。\n", encoding="utf-8")
        self.add("c1", "file_exists", "--path", "report.md")
        self.add("c2", "file_contains", "--path", "report.md", "--text", "## 结论")
        self.add("c3", "file_contains", "--path", "report.md", "--text", "待补充", "--absent")
        code, out = self.cli("verify")
        self.assertEqual(code, 0, out)
        data = self.load_receipt()
        self.assertEqual(data["overall"], "PASS")
        self.assertEqual(data["counts"]["PASS"], 3)
        self.assertIn("PASS", (self.root / "RECEIPT.md").read_text(encoding="utf-8"))

    def test_failure_returns_exit_code_1(self):
        self.add("c1", "file_exists", "--path", "missing.md")
        code, out = self.cli("verify")
        self.assertEqual(code, 1)
        self.assertEqual(self.load_receipt()["overall"], "FAIL")
        self.assertIn("FAIL", out)

    def test_placeholder_absent_check(self):
        (self.root / "report.md").write_text("待补充：结论\n", encoding="utf-8")
        self.add("c1", "file_contains", "--path", "report.md", "--text", "待补充", "--absent")
        code, _ = self.cli("verify")
        self.assertEqual(code, 1)
        self.assertEqual(self.load_receipt()["results"][0]["status"], "FAIL")

    def test_command_requires_explicit_authorization(self):
        self.add("c1", "command", "--cmd", "python -c print(1)")
        code, out = self.cli("verify")
        self.assertEqual(code, 0)
        self.assertEqual(self.load_receipt()["results"][0]["status"], "UNVERIFIED")
        self.assertIn("UNVERIFIED", out)
        code, out = self.cli("verify", "--allow-commands")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.load_receipt()["results"][0]["status"], "PASS")

    def test_attest_never_produces_pass(self):
        self.add("c1", "file_exists", "--path", "missing.md")
        self.cli("attest", "--id", "c1", "--note", "我已经生成了文件（AI 自述）")
        claims = receipt.read_jsonl(self.root / ".receipt" / "claims.jsonl")
        self.assertEqual(len(claims), 1)
        self.assertIn("不参与验收判定", claims[0]["weight"])
        code, _ = self.cli("verify")
        self.assertEqual(code, 1)
        self.assertEqual(self.load_receipt()["results"][0]["status"], "FAIL")

    def test_file_hash_and_min_han(self):
        p = self.root / "draft.md"
        p.write_text("这是正文，共若干个汉字。\n", encoding="utf-8")
        digest = receipt.sha256_file(p)
        self.add("c1", "file_hash", "--path", "draft.md", "--hash", digest)
        self.add("c2", "min_han", "--path", "draft.md", "--min", "5")
        code, out = self.cli("verify")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.load_receipt()["overall"], "PASS")

    def test_json_equals_and_count_files(self):
        out = self.root / "out"
        out.mkdir()
        (out / "a.md").write_text("a", encoding="utf-8")
        (out / "b.md").write_text("b", encoding="utf-8")
        (self.root / "state.json").write_text('{"build":{"ok":true}}', encoding="utf-8")
        self.add("c1", "json_equals", "--path", "state.json", "--key", "build.ok", "--value", "True")
        self.add("c2", "count_files", "--pattern", "out/*.md", "--min", "2")
        code, out = self.cli("verify")
        self.assertEqual(code, 0, out)

    def test_handoff_is_self_contained(self):
        (self.root / "report.md").write_text("内容\n", encoding="utf-8")
        self.add("c1", "file_exists", "--path", "report.md")
        self.cli("verify")
        code, out = self.cli("handoff", "--next", "补充测试")
        self.assertEqual(code, 0, out)
        text = (self.root / "HANDOFF.md").read_text(encoding="utf-8")
        for token in ("当前状态", "未决事项", "给下一个助手的指令", "补充测试", "不依赖聊天记录"):
            self.assertIn(token, text)

    def test_resume_detects_stale_evidence(self):
        p = self.root / "report.md"
        p.write_text("第一版\n", encoding="utf-8")
        # 用内容无关的检查项，确保改动后检查仍 PASS，从而单独验证指纹漂移机制
        self.add("c1", "min_han", "--path", "report.md", "--min", "1")
        self.cli("verify")
        self.assertEqual(self.load_receipt()["overall"], "PASS")
        p.write_text("第二版\n", encoding="utf-8")
        code, out = self.cli("resume")
        self.assertNotEqual(code, 0)
        data = self.load_receipt()
        self.assertEqual(data["overall"], "STALE")
        self.assertEqual(data["results"][0]["status"], receipt.STALE)

    def test_duplicate_id_rejected(self):
        self.add("c1", "file_exists", "--path", "a.md")
        with self.assertRaises(SystemExit):
            self.add("c1", "file_exists", "--path", "b.md")

    def test_verify_json_is_machine_readable(self):
        (self.root / "a.md").write_text("x", encoding="utf-8")
        self.add("c1", "file_exists", "--path", "a.md")
        code, out = self.cli("verify", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["overall"], "PASS")


if __name__ == "__main__":
    unittest.main()
