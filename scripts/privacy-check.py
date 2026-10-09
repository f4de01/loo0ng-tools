#!/usr/bin/env python3
"""隐私检查器：替硬边界 1（案件材料永不入本仓库）把机械能判的那一半守住。

照搬律师工作台 loo0ng-skills 的同名脚本，裁掉了跟案件图相关的 main 守门。
Python 标准库、零依赖；只约束开发者，不随 skill 包分发。

三种模式（互斥）：
  --staged            pre-commit 用。扫暂存 diff 的新增行、暂存文件名；
                      Office 文件（docx / xlsx / pptx）拆 zip 扫 XML；其他二进制放行但点名披露。
  --commit-msg FILE   commit-msg 用。扫提交说明（跳过注释行与 scissors 之后的部分）。
  --stdin             发 issue 前过正文：从标准输入读文本，命中则非零退出并列出类别。
  --all               全仓扫描：工作树里所有已跟踪、以及未被忽略的未跟踪文件，
                      扫全文与文件名，装钩子那天跑一次做基线。

五类正则：法院案号（任何类型代字：民初、执保、财保、破等）、案件根之下的路径段（根本身放行）、
11 位手机号、18 位身份证、统一社会信用代码。不拦座机。

命中时输出文件、行号、类别；不输出命中值，不给绕过提示。修法只有改内容。
合成测试件里的案号等值别写成能被命中的形状（比如在运行时由碎片拼出），本检查器不设豁免。

退出码：0 通过；1 命中；2 用法或运行错误。
"""
import html
import io
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ---------------------------------------------------------------- 五类正则

CASE_ROOT = r"D:\Claude\Data\Cases"
OFFICE_EXTS = (".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".dotx", ".xltx", ".potx")

_CJK = r"\u4e00-\u9fff"

RE_CASE_NUMBER = re.compile(
    r"[（(]\s*(?:19|20)\d{2}\s*[）)]\s*"  # （2024）
    r"[" + _CJK + r"]{1,4}\d{0,4}"  # 法院简称：粤03 / 沪0115 / 最高法
    r"[" + _CJK + r"]{1,6}"  # 类型代字：民初 / 执保 / 财保 / 破申 / 执恢
    r"\d{1,6}(?:-\d+)?\s*号"
)
RE_CASE_PATH = re.compile(
    re.escape(CASE_ROOT).replace(r"\\", r"[\\/]+")
    + r"[\\/]+"  # 根之后的分隔符
    + r"(?![\s\"'`<>|,;，。；、）」』】)\]\\/])\S",  # 再跟一个路径字符（不是又一个分隔符）才算根之下
    re.IGNORECASE,
)
RE_MOBILE = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")
RE_ID_CARD = re.compile(
    r"(?<![0-9A-Za-z])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9A-Za-z])"
)
RE_USCC = re.compile(r"(?<![0-9A-Za-z])[159Y][1239]\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Za-z])")

_USCC_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def uscc_check_ok(code: str) -> bool:
    """GB 32100-2015 的 mod 31 校验位。"""
    try:
        total = sum(_USCC_ALPHABET.index(c) * w for c, w in zip(code[:17], _USCC_WEIGHTS))
    except ValueError:
        return False
    expect = (31 - total % 31) % 31
    return _USCC_ALPHABET[expect] == code[17]


# 顺序即优先级：先命中的类别占住那段文字，后面的类别不再重复报（身份证不再当信用代码报）。
PATTERNS = (
    ("法院案号", RE_CASE_NUMBER, None),
    ("案件目录路径", RE_CASE_PATH, None),
    ("身份证号", RE_ID_CARD, None),
    ("统一社会信用代码", RE_USCC, uscc_check_ok),
    ("手机号", RE_MOBILE, None),
)


@dataclass(frozen=True)
class Hit:
    line: int  # 从 1 起；文件名命中记 0
    category: str
    where: str = ""  # 文件路径（stdin / commit message 模式为空或固定标签）


def find_hits_in_line(line: str) -> List[str]:
    """一行里命中的类别，按出现顺序去重；同一段文字只算一次。"""
    taken: List[Tuple[int, int]] = []
    found: List[str] = []
    for category, regex, validator in PATTERNS:
        for m in regex.finditer(line):
            if validator is not None and not validator(m.group(0)):
                continue
            span = m.span()
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            taken.append(span)
            if category not in found:
                found.append(category)
    return found


def find_hits(text: str, where: str = "") -> List[Hit]:
    hits: List[Hit] = []
    for no, line in enumerate(text.splitlines(), start=1):
        for category in find_hits_in_line(line):
            hits.append(Hit(no, category, where))
    return hits


# ---------------------------------------------------------------- 内容分类与 Office

def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


def is_office(path: str) -> bool:
    return path.lower().endswith(OFFICE_EXTS)


_RE_PARA_END = re.compile(r"</(?:w:p|a:p|text:p|row|si|w:tr)>")
_RE_TAG = re.compile(r"<[^>]+>")


def office_xml_text(xml: str) -> str:
    """把一个 XML 成员压成按段落分行的纯文本：段落结束换行，去标签，解实体。"""
    return html.unescape(_RE_TAG.sub("", _RE_PARA_END.sub("\n", xml)))


def scan_office(path: str, data: bytes) -> Optional[List[Hit]]:
    """拆 zip 扫每个 XML 成员；不是合法 zip 时返回 None（交给披露）。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return None
    hits: List[Hit] = []
    for member in names:
        if not member.lower().endswith((".xml", ".rels")):
            continue
        try:
            raw = zf.read(member)
        except (zipfile.BadZipFile, OSError, RuntimeError):
            return None
        text = office_xml_text(raw.decode("utf-8", "replace"))
        hits.extend(find_hits(text, where=path + "!" + member))
    return hits


def scan_blob(path: str, data: bytes, added_only_lines: Optional[List[Tuple[int, str]]] = None):
    """返回 (hits, disclosed)。文本文件按给定的新增行扫；未给时扫全文。"""
    if is_office(path):
        hits = scan_office(path, data)
        if hits is None:
            return [], True
        return hits, False
    if is_binary(data):
        return [], True
    if added_only_lines is None:
        return find_hits(data.decode("utf-8", "replace"), where=path), False
    hits = [
        Hit(no, category, path)
        for no, line in added_only_lines
        for category in find_hits_in_line(line)
    ]
    return hits, False


# ---------------------------------------------------------------- git

def git(*args: str, check: bool = True) -> bytes:
    proc = subprocess.run(
        ["git", "--literal-pathspecs", "-c", "core.quotepath=false", *args], capture_output=True
    )  # --literal-pathspecs：文件名里的 [ * ? 不当通配符
    if check and proc.returncode != 0:
        raise RuntimeError("git %s 失败：%s" % (" ".join(args), proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout


def staged_entries() -> List[Tuple[str, str]]:
    """暂存改动：[(状态字母, 路径)]，改名/复制取新路径。"""
    out = git("diff", "--cached", "--name-status", "-z")
    fields = [f.decode("utf-8", "replace") for f in out.split(b"\0") if f]
    entries: List[Tuple[str, str]] = []
    i = 0
    while i < len(fields):
        status = fields[i]
        if status[0] in "RC":
            entries.append((status[0], fields[i + 2]))
            i += 3
        else:
            entries.append((status[0], fields[i + 1]))
            i += 2
    return entries


_RE_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def staged_added_lines(path: str) -> List[Tuple[int, str]]:
    """暂存 diff 里该文件的新增行：[(新文件行号, 行文本)]。"""
    out = git("diff", "--cached", "--no-color", "--no-ext-diff", "--unified=0", "--", path)
    lines: List[Tuple[int, str]] = []
    current = 0
    in_hunk = False  # ---/+++ 文件头只在第一个 hunk 之前；之后以 ++ 开头的内容行也要扫
    for raw in out.split(b"\n"):
        line = raw.decode("utf-8", "replace")
        m = _RE_HUNK.match(line)
        if m:
            current = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            lines.append((current, line[1:]))
            current += 1
    return lines


def staged_blob(path: str) -> bytes:
    return git("cat-file", "blob", ":0:" + path)


# ---------------------------------------------------------------- 报告

class Report:
    def __init__(self) -> None:
        self.hits: List[Hit] = []
        self.disclosed: List[str] = []

    @property
    def rejected(self) -> bool:
        return bool(self.hits)

    def render(self, mode_label: str) -> str:
        out: List[str] = []
        if self.hits:
            out.append("隐私检查：拒绝（%s）" % mode_label)
            for h in self.hits:
                loc = h.where or "-"
                if h.line == 0:
                    out.append("  %s:文件名  %s" % (loc, h.category))
                else:
                    out.append("  %s:%d  %s" % (loc, h.line, h.category))
        if self.disclosed:
            out.append("披露：以下二进制文件未扫描")
            for p in self.disclosed:
                out.append("  " + p)
        return "\n".join(out)


# ---------------------------------------------------------------- 四种模式

def run_staged() -> Report:
    report = Report()
    for status, path in staged_entries():
        if status == "D":
            continue
        for category in find_hits_in_line(path):
            report.hits.append(Hit(0, category, path))
        data = staged_blob(path)
        added = None if is_office(path) or is_binary(data) else staged_added_lines(path)
        hits, disclosed = scan_blob(path, data, added)
        report.hits.extend(hits)
        if disclosed:
            report.disclosed.append(path)
    return report


def run_all() -> Report:
    """说了全仓就要真是全仓：已跟踪 + 未被忽略的未跟踪。

    只看已跟踪的话，一个从没被 git add 过的文件里有案件内容，扫不到却报「未命中」。
    --exclude-standard 不能省，否则 node_modules/、__pycache__/、.scratch/ 全被扫进来。
    两个名单互斥，不必去重。
    """
    report = Report()
    out = git("ls-files", "-z") + git("ls-files", "-z", "--others", "--exclude-standard")
    for raw in out.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", "replace")
        for category in find_hits_in_line(path):
            report.hits.append(Hit(0, category, path))
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue  # 目录（子模块）或已删除的已跟踪文件
        hits, disclosed = scan_blob(path, data)
        report.hits.extend(hits)
        if disclosed:
            report.disclosed.append(path)
    return report


def commit_message_body(text: str, comment_char: str = "#") -> str:
    """去掉注释行；scissors 之后的整段（verbose 模式附的 diff）不算说明。"""
    kept: List[str] = []
    scissors = comment_char + " ------------------------ >8 ------------------------"
    for line in text.splitlines():
        if line.startswith(scissors):
            break
        if line.startswith(comment_char):
            kept.append("")  # 保住行号
            continue
        kept.append(line)
    return "\n".join(kept)


def run_commit_msg(path: str) -> Report:
    report = Report()
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", "replace")
    comment_char = git("config", "--get", "core.commentchar", check=False).decode("utf-8", "replace").strip() or "#"
    if comment_char == "auto":
        comment_char = "#"
    report.hits = find_hits(commit_message_body(text, comment_char), where="commit message")
    return report


def run_stdin() -> Report:
    report = Report()
    text = sys.stdin.buffer.read().decode("utf-8", "replace")
    report.hits = find_hits(text, where="stdin")
    return report


USAGE = "用法：privacy-check.py (--staged | --commit-msg FILE | --stdin | --all)"


def main(argv: List[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if len(argv) == 1 and argv[0] == "--staged":
        mode, label = run_staged, "暂存改动"
    elif len(argv) == 2 and argv[0] == "--commit-msg":
        mode, label = (lambda: run_commit_msg(argv[1])), "commit message"
    elif len(argv) == 1 and argv[0] == "--stdin":
        mode, label = run_stdin, "stdin"
    elif len(argv) == 1 and argv[0] == "--all":
        mode, label = run_all, "全仓扫描"
    else:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        if mode in (run_staged, run_all):
            top = git("rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
            os.chdir(top)
        report = mode()
    except RuntimeError as e:
        print("隐私检查无法运行：%s" % e, file=sys.stderr)
        return 2
    text = report.render(label)
    if text:
        print(text)
    if report.rejected:
        return 1
    if mode is run_all:
        print("隐私检查：全仓扫描未命中")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
