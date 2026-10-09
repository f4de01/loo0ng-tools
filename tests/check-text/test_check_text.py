"""scripts/check-text.sh 的脚本层单测（unittest，标准库零依赖）。

运行：python -m unittest discover -s tests/check-text -p 'test_*.py'

守的是名单：已跟踪 + 未被忽略的未跟踪都要扫进来，被 .gitignore 忽略的不扫。
新文件在 git add 之前曾经是隐形的，于是一个文件能在提交前三次校验全绿、提交之后才报错。

本文件里不放破折号 U+2014 的字面字符（它自己也要过 check-text.sh）：
下面的 EM_DASH 由码位拼出来，源码里只出现这个名字。
"""
import os
import pathlib
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-text.sh"

sys.path.insert(0, str(REPO / "tests" / "共用"))
from 临时仓库 import GitRepoMixin  # noqa: E402
from bash import find_bash  # noqa: E402

EM_DASH = chr(0x2014)
BOM = chr(0xFEFF)


BASH, BASH_PATH_DIRS = find_bash()


@unittest.skipIf(BASH is None, "机器上没有 bash")
class CheckTextTests(GitRepoMixin, unittest.TestCase):
    def run_check(self):
        env = dict(self.env)
        if BASH_PATH_DIRS:
            env["PATH"] = os.pathsep.join(BASH_PATH_DIRS + [env["PATH"]])
        proc = subprocess.run(
            [BASH, str(SCRIPT), "."], cwd=self.root, env=env, capture_output=True
        )
        out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
        return proc.returncode, out

    def assertFailed(self, code, out, needle):
        self.assertEqual(code, 1, out)
        self.assertIn(needle, out)

    # ---------------------------------------------------------------- 名单

    def test_clean_repo_passes(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("失败 0", out)

    def test_untracked_em_dash_is_caught_before_and_after_add(self):
        self.write("草稿.md", "一句话" + EM_DASH + "带破折号\n")
        code, out = self.run_check()
        self.assertFailed(code, out, "草稿.md")
        self.assertIn("U+2014", out)
        self.git("add", "草稿.md")
        code, out = self.run_check()
        self.assertFailed(code, out, "草稿.md")

    def test_ignored_file_is_not_scanned(self):
        self.write(".gitignore", "忽略/\n")
        self.write("忽略/脏.md", "一句话" + EM_DASH + "带破折号\n")
        self.write("忽略/脏.ps1", "Write-Output 1\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertNotIn("忽略", out)

    def test_untracked_bom_is_caught(self):
        self.write("带BOM.md", BOM + "正文\n")
        code, out = self.run_check()
        self.assertFailed(code, out, "带BOM.md")
        self.assertIn("BOM", out)

    def test_untracked_ps1_without_bom_is_caught(self):
        self.write("脚本.ps1", "Write-Output 1\n")
        code, out = self.run_check()
        self.assertFailed(code, out, "脚本.ps1")

    def test_ps1_with_bom_passes(self):
        self.write("脚本.ps1", BOM + "Write-Output 1\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_chinese_path_is_not_truncated(self):
        """中文路径全程走 -z：按行读会读到加引号转义后的名字，那种文件被静默跳过。"""
        rel = "文档/深一层/中文名字的稿子.md"
        self.write(rel, "一句话" + EM_DASH + "带破折号\n")
        code, out = self.run_check()
        self.assertFailed(code, out, rel)

    def test_single_untracked_file_keeps_its_name_in_the_report(self):
        """名单只剩一个文件时 grep -c 也要带文件名，否则光一个数字读不出改哪儿。"""
        self.write(".gitignore", "README.md\n")
        self.write("独苗.md", "一句话" + EM_DASH + "带破折号\n")
        code, out = self.run_check()
        self.assertFailed(code, out, "独苗.md")

    def test_untracked_binary_is_skipped(self):
        (self.root / "图.bin").write_bytes(b"\x00\xff" * 100)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
