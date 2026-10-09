"""scripts/privacy-check.py 的脚本层单测（unittest，标准库零依赖）。

运行：python -m unittest tests/privacy-check/test_privacy_check.py

命中样例全部是合成值，且在运行时由碎片拼出：本文件自己也要过隐私钩子，
源码里不能出现任何能被五类正则命中的字面量（钩子不设豁免）。
"""
import importlib.util
import pathlib
import subprocess
import sys
import unittest
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "privacy-check.py"

sys.path.insert(0, str(REPO / "tests" / "共用"))
from 临时仓库 import GitRepoMixin  # noqa: E402


def load_module():
    spec = importlib.util.spec_from_file_location("privacy_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def j(*parts):
    """把碎片拼成一个命中值，源码里不出现完整值。"""
    return "".join(parts)


# 五类命中样例（合成值）与近似正常值
CASE_NO_HIT = j("（2099）", "测01", "破", "1号")
CASE_NO_HIT_HALF = j("(2099)", "测0101", "破申", "12号")
CASE_NO_HIT_ZHONG = j("（2098）", "测01", "破终", "3号")
CASE_NO_HIT_EXEC = j("（2099）", "测01", "执保", "12号")
CASE_NO_HIT_CIVIL = j("(2099)", "测0101", "民初", "123号")
CASE_NO_HIT_SUPREME = j("（2099）", "最高法", "民申", "1号")
CASE_NO_MISS = "2099年第1号"
CASE_NO_MISS_WORD = "破产管理人第1号公告"

CASE_ROOT = "D:\\Claude\\Data\\Cases\\"
CASE_PATH_HIT = j(CASE_ROOT, "甲公司", "\\材料")
CASE_PATH_HIT_SLASH = j("D:/Claude/Data/Cases/", "甲公司")
CASE_PATH_MISS_ROOT = CASE_ROOT
CASE_PATH_MISS_MD = "`" + CASE_ROOT + "`"
CASE_PATH_MISS_NOSLASH = "D:\\Claude\\Data\\Cases"

MOBILE_HIT = j("138", "0013", "8000")
MOBILE_HIT_19 = j("199", "0000", "0000")
MOBILE_MISS_LANDLINE = "0755-12345678"
MOBILE_MISS_SHORT = j("138", "0013", "800")
MOBILE_MISS_LONG = j("138", "0013", "80001")

ID_HIT = j("110101", "19900101", "123", "X")
ID_HIT_DIGIT = j("440300", "20000229", "0011")
ID_MISS_17 = j("110101", "19900101", "123")
ID_MISS_MONTH = j("110101", "19901301", "1234")

USCC_HIT = j("91350100", "M000100Y", "43")  # GB 32100-2015 的示例码
USCC_MISS_CHECK = j("91350100", "M000100Y", "44")
USCC_MISS_17 = j("91350100", "M000100Y", "4")

XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def run_check(args, cwd, stdin_text=None):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        input=stdin_text.encode("utf-8") if stdin_text is not None else None,
        capture_output=True,
    )
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    return proc.returncode, out


def make_docx(path, paragraphs):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", XML_HEAD + '<Types xmlns="%s"></Types>' % CT_NS)
        body = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % p for p in paragraphs)
        z.writestr(
            "word/document.xml",
            XML_HEAD + '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W_NS, body),
        )


class PatternTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pc = load_module()

    def cats(self, text):
        return sorted({h.category for h in self.pc.find_hits(text)})

    def test_case_number_hit_and_miss(self):
        for s in (CASE_NO_HIT, CASE_NO_HIT_HALF, CASE_NO_HIT_ZHONG,
                  CASE_NO_HIT_EXEC, CASE_NO_HIT_CIVIL, CASE_NO_HIT_SUPREME):
            self.assertEqual(self.cats("案号 " + s + " 见附件"), ["法院案号"], s)
        for s in (CASE_NO_MISS, CASE_NO_MISS_WORD):
            self.assertEqual(self.cats(s), [], s)

    def test_case_path_hit_and_miss(self):
        for s in (CASE_PATH_HIT, CASE_PATH_HIT_SLASH):
            self.assertEqual(self.cats("放在 " + s), ["案件目录路径"], s)
        for s in (CASE_PATH_MISS_ROOT, CASE_PATH_MISS_MD, CASE_PATH_MISS_NOSLASH):
            self.assertEqual(self.cats("案件工作区（" + s + "）之外"), [], s)
        self.assertEqual(self.cats(CASE_PATH_MISS_ROOT), [])

    def test_mobile_hit_and_miss(self):
        for s in (MOBILE_HIT, MOBILE_HIT_19):
            self.assertEqual(self.cats("电话" + s + "。"), ["手机号"], s)
            self.assertEqual(self.cats("tel: " + s), ["手机号"], s)
        for s in (MOBILE_MISS_LANDLINE, MOBILE_MISS_SHORT, MOBILE_MISS_LONG):
            self.assertEqual(self.cats("电话 " + s), [], s)

    def test_id_hit_and_miss(self):
        for s in (ID_HIT, ID_HIT_DIGIT):
            self.assertEqual(self.cats("身份证 " + s), ["身份证号"], s)
        for s in (ID_MISS_17, ID_MISS_MONTH):
            self.assertNotIn("身份证号", self.cats("编号 " + s), s)

    def test_uscc_hit_and_miss(self):
        self.assertEqual(self.cats("信用代码：" + USCC_HIT), ["统一社会信用代码"])
        for s in (USCC_MISS_CHECK, USCC_MISS_17):
            self.assertEqual(self.cats("信用代码：" + s), [], s)

    def test_id_is_not_double_reported_as_uscc(self):
        self.assertEqual(self.cats(ID_HIT_DIGIT), ["身份证号"])

    def test_line_numbers(self):
        text = "第一行\n第二行 " + MOBILE_HIT + "\n第三行\n" + CASE_NO_HIT + "\n"
        hits = self.pc.find_hits(text)
        self.assertEqual([(h.line, h.category) for h in hits], [(2, "手机号"), (4, "法院案号")])

    def test_this_test_file_and_script_are_clean(self):
        for p in (pathlib.Path(__file__), SCRIPT):
            text = p.read_text(encoding="utf-8")
            self.assertEqual(self.pc.find_hits(text), [], str(p))


class RepoCase(GitRepoMixin):
    """临时仓库来自 tests/共用/临时仓库.py，这里只加本脚本的两件事。"""

    def check(self, *args, stdin_text=None):
        return run_check(args, cwd=self.root, stdin_text=stdin_text)

    def assertRejected(self, code, out):
        self.assertEqual(code, 1, out)
        self.assertNotIn("no-verify", out)
        self.assertNotIn("绕过", out)


class StagedTests(RepoCase, unittest.TestCase):
    def test_no_staged_changes_passes(self):
        code, out = self.check("--staged")
        self.assertEqual(code, 0, out)

    def test_text_file_with_hit_is_rejected_with_file_line_category(self):
        self.write("docs/笔记.md", "标题\n\n联系 " + MOBILE_HIT + "\n")
        self.git("add", "docs/笔记.md")
        code, out = self.check("--staged")
        self.assertRejected(code, out)
        self.assertIn("docs/笔记.md:3", out)
        self.assertIn("手机号", out)

    def test_only_added_lines_are_scanned(self):
        self.write("a.md", "旧行 " + MOBILE_HIT + "\n")
        self.git("add", "a.md")
        self.git("commit", "-q", "-m", "seed")  # 临时仓库没装钩子
        self.write("a.md", "旧行 " + MOBILE_HIT + "\n新增干净一行\n")
        self.git("add", "a.md")
        code, out = self.check("--staged")
        self.assertEqual(code, 0, out)
        self.write("a.md", "旧行 " + MOBILE_HIT + "\n新增干净一行\n新增 " + CASE_NO_HIT + "\n")
        self.git("add", "a.md")
        code, out = self.check("--staged")
        self.assertRejected(code, out)
        self.assertIn("a.md:3", out)
        self.assertIn("法院案号", out)

    def test_added_line_starting_with_plus_signs_is_still_scanned(self):
        self.write("c.md", "++ 备注 " + MOBILE_HIT + "\n")
        self.git("add", "c.md")
        code, out = self.check("--staged")
        self.assertRejected(code, out)
        self.assertIn("c.md:1", out)

    def test_filename_with_hit_is_rejected(self):
        name = "docs/" + MOBILE_HIT + ".md"
        self.write(name, "干净内容\n")
        self.git("add", name)
        code, out = self.check("--staged")
        self.assertRejected(code, out)
        self.assertIn(name, out)
        self.assertIn("文件名", out)
        self.assertIn("手机号", out)

    def test_docx_with_hit_is_rejected(self):
        p = self.root / "docs" / "样例.docx"
        p.parent.mkdir(parents=True, exist_ok=True)
        make_docx(p, ["第一段", "案号 " + CASE_NO_HIT])
        self.git("add", "docs/样例.docx")
        code, out = self.check("--staged")
        self.assertRejected(code, out)
        self.assertIn("docs/样例.docx", out)
        self.assertIn("word/document.xml", out)
        self.assertIn("法院案号", out)

    def test_clean_docx_passes_without_disclosure(self):
        p = self.root / "docs" / "干净.docx"
        p.parent.mkdir(parents=True, exist_ok=True)
        make_docx(p, ["第一段", "第二段"])
        self.git("add", "docs/干净.docx")
        code, out = self.check("--staged")
        self.assertEqual(code, 0, out)
        self.assertNotIn("披露", out)

    def test_other_binary_passes_but_is_disclosed(self):
        p = self.root / "docs" / "图.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(64))
        self.git("add", "docs/图.png")
        code, out = self.check("--staged")
        self.assertEqual(code, 0, out)
        self.assertIn("披露", out)
        self.assertIn("docs/图.png", out)

    def test_docx_that_is_not_a_zip_is_disclosed(self):
        p = self.root / "docs" / "坏.docx"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x01\x02not a zip" + bytes(32))
        self.git("add", "docs/坏.docx")
        code, out = self.check("--staged")
        self.assertEqual(code, 0, out)
        self.assertIn("披露", out)
        self.assertIn("docs/坏.docx", out)

    def test_deleted_file_is_not_scanned(self):
        self.write("b.md", "x " + MOBILE_HIT + "\n")
        self.git("add", "b.md")
        self.git("commit", "-q", "-m", "seed")
        self.git("rm", "-q", "b.md")
        code, out = self.check("--staged")
        self.assertEqual(code, 0, out)


class CommitMsgTests(RepoCase, unittest.TestCase):
    def test_message_with_hit_is_rejected(self):
        p = self.write("MSG", "修正材料路径\n\n路径 " + CASE_PATH_HIT + "\n")
        code, out = self.check("--commit-msg", str(p))
        self.assertRejected(code, out)
        self.assertIn("commit message", out)
        self.assertIn("案件目录路径", out)

    def test_comment_lines_are_ignored(self):
        p = self.write("MSG", "干净标题\n# 注释里的 " + MOBILE_HIT + "\n")
        code, out = self.check("--commit-msg", str(p))
        self.assertEqual(code, 0, out)

    def test_scissors_tail_is_ignored(self):
        p = self.write(
            "MSG",
            "干净标题\n# ------------------------ >8 ------------------------\n+ " + MOBILE_HIT + "\n",
        )
        code, out = self.check("--commit-msg", str(p))
        self.assertEqual(code, 0, out)


class StdinTests(unittest.TestCase):
    def test_stdin_with_hit_lists_category_and_line(self):
        code, out = run_check(["--stdin"], cwd=REPO, stdin_text="正文\n信用代码 " + USCC_HIT + "\n")
        self.assertEqual(code, 1, out)
        self.assertIn("统一社会信用代码", out)
        self.assertIn(":2", out)

    def test_clean_stdin_passes(self):
        code, out = run_check(["--stdin"], cwd=REPO, stdin_text="只有正常文字，案件根 " + CASE_ROOT + " 不算。\n")
        self.assertEqual(code, 0, out)


class AllTests(RepoCase, unittest.TestCase):
    def test_all_scans_tracked_files_and_names(self):
        self.write("docs/a.md", "干净\n")
        self.write("docs/b.md", "一\n二 " + ID_HIT + "\n")
        self.git("add", "docs")
        self.git("commit", "-q", "-m", "seed")
        code, out = self.check("--all")
        self.assertRejected(code, out)
        self.assertIn("docs/b.md:2", out)
        self.assertIn("身份证号", out)

    def test_all_on_clean_repo_passes(self):
        code, out = self.check("--all")
        self.assertEqual(code, 0, out)

    def test_all_reports_binary(self):
        p = self.root / "img.bin"
        p.write_bytes(b"\x00\xff" * 100)
        self.git("add", "img.bin")
        self.git("commit", "-q", "-m", "bin")
        code, out = self.check("--all")
        self.assertEqual(code, 0, out)
        self.assertIn("披露", out)
        self.assertIn("img.bin", out)

    def test_all_sees_untracked_files(self):
        """说了全仓就要真是全仓：没被 git add 过的文件也扫。"""
        self.write("未跟踪.md", "一\n二 " + MOBILE_HIT + "\n")
        code, out = self.check("--all")
        self.assertRejected(code, out)
        self.assertIn("未跟踪.md:2", out)
        self.assertIn("手机号", out)

    def test_all_skips_ignored_files(self):
        self.write(".gitignore", "忽略/\n")
        self.write("忽略/脏.md", MOBILE_HIT + "\n")
        code, out = self.check("--all")
        self.assertEqual(code, 0, out)
        self.assertNotIn("忽略", out)


class UsageTests(unittest.TestCase):
    def test_no_mode_is_usage_error(self):
        code, out = run_check([], cwd=REPO)
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
