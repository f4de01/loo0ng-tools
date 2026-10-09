"""AGENTS.md 里 skill 约定的机械那一半（unittest，标准库零依赖）。

运行：python -m unittest discover -s tests/skills -p "test_*.py"

- 布局：skills/<name>/ 的 name 只用小写字母、数字、连字符；SKILL.md 不带 BOM，frontmatter 的 name 与目录同名；
  根 README.md 链接到每一件的 SKILL.md。
- 说明随包自足：skill 目录内不出现 ADR 号、issue 号、版本号，Markdown 链接不指向 skill 目录之外。
- 随包脚本跑得动 Python 3.9：律师机上的 python3 可能就是 3.9。按 3.9 的语法解析，拦得住 match 之类
  3.10 才有的写法；Path.read_text / write_text 的 newline= 关键字 3.10 才有，单拦。
"""
import ast
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SKILLS = sorted(p for p in (REPO / "skills").iterdir() if p.is_dir())
TEXT_SUFFIXES = (".md", ".py", ".yaml", ".yml", ".json", ".txt")

FORBIDDEN = (
    ("ADR 号", re.compile(r"ADR[-\s]?\d")),
    ("issue 号", re.compile(r"(?<![\w&#])#\d+\b")),
    ("版本号", re.compile(r"(?<![\w.])v\d+\.\d+")),
)
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def skill_texts(skill):
    for p in sorted(skill.rglob("*")):
        if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts:
            yield p


class LayoutTests(unittest.TestCase):
    def test_there_is_at_least_one_skill(self):
        self.assertTrue(SKILLS)

    def test_skill_names_and_frontmatter(self):
        for skill in SKILLS:
            self.assertRegex(skill.name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
            raw = (skill / "SKILL.md").read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "%s 的 SKILL.md 带了 BOM" % skill.name)
            head = raw.decode("utf-8").split("---", 2)[1]
            self.assertRegex(head, r"(?m)^name: %s$" % re.escape(skill.name))
            self.assertRegex(head, r"(?m)^description: \S")

    def test_readme_links_every_skill(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for skill in SKILLS:
            self.assertIn("(skills/%s/SKILL.md)" % skill.name, readme)


class SelfContainedTests(unittest.TestCase):
    def test_no_adr_issue_or_version_numbers(self):
        for skill in SKILLS:
            for p in skill_texts(skill):
                text = p.read_text(encoding="utf-8")
                for label, regex in FORBIDDEN:
                    m = regex.search(text)
                    self.assertIsNone(m, "%s 里出现了%s：%s" % (p.relative_to(REPO), label, m and m.group(0)))

    def test_links_stay_inside_skill(self):
        for skill in SKILLS:
            for p in skill.rglob("*.md"):
                for target in LINK_RE.findall(p.read_text(encoding="utf-8")):
                    if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                        continue
                    resolved = (p.parent / target.split("#")[0]).resolve()
                    self.assertTrue(str(resolved).startswith(str(skill.resolve())),
                                    "%s 链出了 skill 目录：%s" % (p.relative_to(REPO), target))
                    self.assertTrue(resolved.exists(), "%s 的链接指向不存在的文件：%s" % (p.relative_to(REPO), target))


class PythonFloorTests(unittest.TestCase):
    def shipped(self):
        return sorted((REPO / "skills").glob("*/scripts/*.py"))

    def test_parses_as_python_39(self):
        for p in self.shipped():
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p), feature_version=(3, 9))

    def test_no_newline_kwarg_on_path_text_methods(self):
        for p in self.shipped():
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("read_text", "write_text")
                        and any(k.arg == "newline" for k in node.keywords)):
                    self.fail("%s:%d 用了 3.10 才有的 newline= 关键字" % (p.relative_to(REPO), node.lineno))


if __name__ == "__main__":
    unittest.main()
