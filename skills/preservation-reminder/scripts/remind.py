#!/usr/bin/env python3
"""续保提醒 CLI：把模型读文书写出的财产行并进保全底账，推算缺失的届满日，算续保提醒时点，拼导入文本。

标准库零依赖，Python 3.9 起可跑。读文书、认财产、判断届满日是不是文书写明的，归模型；
本脚本只做确定性的那一半：合并去重、按类型推算、倒推提醒时点、拼导入文本。

用法：
  python remind.py merge  --ledger <保全期限.md> --rows <财产行.json> [--now "YYYY-MM-DD HH:MM"]
  python remind.py export --ledger <保全期限.md> [--case <案号> ...] [--replace] [--now ...]

merge   底账不在就新建；并入财产行，写回底账；回显摘要与「本次新增或变动的批」的导入文本。
export  不并新行，只把底账里缺的届满日推算补上；回显筛出的批（不给 --case 就是全部）。
        --replace 让手机先删掉同识别码的旧提醒再建，律师手改过当事人、财产之类之后用。

提醒时点一律从申请截止日（届满日前七日）倒推：截止日前 30 天、前 7 天两档，每档 10:00、12:00、17:00。

退出码：0 跑到底；1 输入或底账不合法，整条拒绝，底账一字不动；2 用法错。
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys
import unicodedata
from typing import Dict, List, Optional, Tuple

FORMAT_VERSION = 1
REMINDER_LIST = "续保"
FILING_LEAD_DAYS = 7  # 保全规定第十八条：届满七日前提出续行申请
TIER_DAYS = (30, 7)  # 截止日前 30 天、前 7 天
TIER_TIMES = ((10, 0), (12, 0), (17, 0))

SOURCE_STATED = "文书写明"
SOURCE_INFERRED = "推算"

COLUMNS = ("案号", "法院", "申请人", "被申请人", "财产", "类型", "实施日", "期限", "届满日", "到期日来源", "备注")
ROW_KEYS = ("案号", "法院", "申请人", "被申请人", "财产", "类型", "实施日", "期限", "届满日")
REQUIRED_ROW_KEYS = ("案号", "财产", "类型")

# 类型 -> (月数, 依据, 回显时要注明的话)
PERIODS = {
    "银行存款": (12, "民诉法解释第四百八十五条", ""),
    "动产": (24, "民诉法解释第四百八十五条", ""),
    "不动产": (36, "民诉法解释第四百八十五条", ""),
    "其他财产权": (36, "民诉法解释第四百八十五条", ""),
    "证券": (24, "法发〔2008〕4号第十二条", "证券按 2 年推算；与解释「其他财产权 3 年」的关系实务有争议，请向法院核实"),
    "证券交易结算资金": (6, "法发〔2008〕4号第十二条", "证券交易结算资金按 6 个月推算；与解释「银行存款 1 年」的关系实务有争议，请向法院核实"),
}
ALIASES = {
    "存款": "银行存款",
    "机动车": "动产",
    "车辆": "动产",
    "房产": "不动产",
    "土地使用权": "不动产",
    "股权": "其他财产权",
    "股票": "证券",
}
ALIAS_NOTES = {
    "机动车": "机动车按动产推算是推断，无专门条文",
    "车辆": "机动车按动产推算是推断，无专门条文",
}

LEDGER_PREAMBLE = (
    "# 保全期限",
    "",
    "保全底账：一项财产一行，续保提醒由它导出。可以直接改单元格，下次运行时读回：",
    "",
    "- 推算出的届满日核对确认后，把「到期日来源」改成「文书写明」，提醒标题就不再带「推算待核」。",
    "- 要按实施日重新推算，清空这一行的「届满日」与「到期日来源」。",
    "- 「期限」只填文书写明的期限（如 1年、6个月）；空着就按类型推算。",
    "- 不要改表头、不要在单元格里写竖线。",
    "",
)

DATE_RE = re.compile(r"^\s*(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?\s*$")
PERIOD_RE = re.compile(r"^\s*(\d{1,3})\s*(年|个月|月)\s*$")


class Refuse(Exception):
    """输入或底账不合法：整条拒绝。"""


# ---------------------------------------------------------------- 日期

def parse_date(text: str, where: str) -> Optional[_dt.date]:
    if text is None or not str(text).strip():
        return None
    m = DATE_RE.match(str(text))
    if not m:
        raise Refuse("%s：日期「%s」认不出，写成 YYYY-MM-DD" % (where, text))
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise Refuse("%s：日期「%s」不存在" % (where, text))


def parse_period(text: str, where: str) -> Optional[int]:
    """期限 -> 月数。"""
    if not text or not text.strip():
        return None
    m = PERIOD_RE.match(text)
    if not m or int(m.group(1)) == 0:
        raise Refuse("%s：期限「%s」认不出，写成 1年 或 6个月" % (where, text))
    n = int(m.group(1))
    return n * 12 if m.group(2) == "年" else n


def format_period(months: int) -> str:
    return "%d年" % (months // 12) if months % 12 == 0 else "%d个月" % months


def add_months(day: _dt.date, months: int) -> _dt.date:
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    nxt = _dt.date(year + (month == 12), month % 12 + 1, 1)
    last = (nxt - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(day.day, last))


def infer_expiry(start: _dt.date, months: int) -> _dt.date:
    """实施日当天计入：自实施日起 N 个月，到对应日的前一日届满。两种算法里取较早的一种，宁早勿晚。"""
    return add_months(start, months) - _dt.timedelta(days=1)


def filing_deadline(expiry: _dt.date) -> _dt.date:
    return expiry - _dt.timedelta(days=FILING_LEAD_DAYS)


# ---------------------------------------------------------------- 行

def clean_cell(value: str) -> str:
    """单元格里不许有竖线和换行：竖线换全角，换行换空格。"""
    return " ".join(str(value).replace("|", "｜").split())


def normalize_key(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def normalize_type(text: str, where: str) -> Tuple[str, str]:
    """返回 (规范类型, 别名带出的备注)。空串原样放行。"""
    t = text.strip()
    if not t:
        return "", ""
    if t in PERIODS:
        return t, ""
    if t in ALIASES:
        return ALIASES[t], ALIAS_NOTES.get(t, "")
    raise Refuse("%s：类型「%s」不认得，只能是 %s" % (where, text, "、".join(PERIODS)))


def blank_row() -> Dict[str, str]:
    return {c: "" for c in COLUMNS}


def row_key(row: Dict[str, str]) -> Tuple[str, str]:
    return normalize_key(row["案号"]), normalize_key(row["财产"])


def add_note(row: Dict[str, str], note: str) -> None:
    if note and note not in row["备注"]:
        row["备注"] = clean_cell((row["备注"] + "；" + note).strip("；"))


def check_row_cells(row: Dict[str, str], where: str) -> None:
    """把日期、期限、类型规范化；不合法即拒。"""
    for col in ("实施日", "届满日"):
        d = parse_date(row[col], "%s「%s」" % (where, col))
        row[col] = d.isoformat() if d else ""
    months = parse_period(row["期限"], "%s「期限」" % where)
    row["期限"] = format_period(months) if months else ""
    row["类型"], _ = normalize_type(row["类型"], "%s「类型」" % where)
    if row["到期日来源"] not in ("", SOURCE_STATED, SOURCE_INFERRED):
        raise Refuse("%s：到期日来源只能是「%s」或「%s」" % (where, SOURCE_STATED, SOURCE_INFERRED))
    if row["届满日"] and not row["到期日来源"]:
        row["到期日来源"] = SOURCE_STATED  # 律师手填了届满日、没填来源：按文书写明看
    if not row["届满日"]:
        row["到期日来源"] = ""


def infer_if_blank(row: Dict[str, str]) -> Optional[str]:
    """届满日空着而实施日与（期限或类型）在：推算补上，返回推算说明；否则返回 None。"""
    if row["届满日"] or not row["实施日"]:
        return None
    months = parse_period(row["期限"], "期限")
    basis = "文书所写期限 %s" % row["期限"] if months else ""
    if not months:
        if not row["类型"]:
            return None
        months, law, _ = PERIODS[row["类型"]]
        basis = "%s %s（%s）" % (row["类型"], format_period(months), law)
    start = _dt.date.fromisoformat(row["实施日"])
    row["届满日"] = infer_expiry(start, months).isoformat()
    row["到期日来源"] = SOURCE_INFERRED
    return "按%s从实施日推算" % basis


# ---------------------------------------------------------------- 底账读写

def parse_table_line(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def is_separator(cells: List[str]) -> bool:
    return all(re.fullmatch(r":?-{1,}:?", c) for c in cells) and bool(cells)


def read_ledger(path: str) -> Tuple[List[str], List[Dict[str, str]], List[str]]:
    """返回 (表前的行, 财产行, 表后的行)。文件不在就返回空底账。"""
    if not os.path.exists(path):
        return list(LEDGER_PREAMBLE), [], []
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|") and "案号" in line and "财产" in line:
            start = i
            break
    if start is None:
        raise Refuse("底账 %s 里找不到带「案号」「财产」表头的表" % path)
    header = parse_table_line(lines[start])
    if tuple(header) != COLUMNS:
        raise Refuse("底账表头被改过，应为：| %s |" % " | ".join(COLUMNS))
    end = start + 1
    if end < len(lines) and is_separator(parse_table_line(lines[end])):
        end += 1
    rows: List[Dict[str, str]] = []
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        cells = parse_table_line(lines[end])
        where = "底账第 %d 行" % (end + 1)
        if len(cells) != len(COLUMNS):
            raise Refuse("%s：应有 %d 格，实有 %d 格（单元格里别写竖线）" % (where, len(COLUMNS), len(cells)))
        row = dict(zip(COLUMNS, cells))
        if not row["案号"] or not row["财产"]:
            raise Refuse("%s：案号与财产不能空" % where)
        check_row_cells(row, where)
        rows.append(row)
        end += 1
    return lines[:start], rows, lines[end:]


def render_ledger(before: List[str], rows: List[Dict[str, str]], after: List[str]) -> str:
    out = list(before)
    out.append("| %s |" % " | ".join(COLUMNS))
    out.append("|" + "---|" * len(COLUMNS))
    for row in rows:
        out.append("| %s |" % " | ".join(clean_cell(row[c]) for c in COLUMNS))
    out.extend(after)
    return "\n".join(out) + "\n"


def write_ledger(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 财产行输入

def load_rows(path: str) -> List[Dict[str, str]]:
    try:
        if path == "-":
            raw = sys.stdin.buffer.read().decode("utf-8-sig")
        else:
            with open(path, "r", encoding="utf-8-sig") as f:
                raw = f.read()
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise Refuse("财产行文件读不了：%s" % e)
    if not isinstance(data, list) or not data:
        raise Refuse("财产行文件应是非空的 JSON 数组，一项财产一个对象")
    rows = []
    for i, item in enumerate(data, start=1):
        where = "财产行第 %d 条" % i
        if not isinstance(item, dict):
            raise Refuse("%s：应是对象" % where)
        unknown = [k for k in item if k not in ROW_KEYS]
        if unknown:
            raise Refuse("%s：不认得的字段 %s，只认 %s" % (where, "、".join(unknown), "、".join(ROW_KEYS)))
        for k, v in item.items():
            if not isinstance(v, str):
                raise Refuse("%s：「%s」应是字符串" % (where, k))
        missing = [k for k in REQUIRED_ROW_KEYS if not item.get(k, "").strip()]
        if missing:
            raise Refuse("%s：缺 %s" % (where, "、".join(missing)))
        row = blank_row()
        for k in ROW_KEYS:
            row[k] = clean_cell(item.get(k, ""))
        row["类型"], alias_note = normalize_type(row["类型"], "%s「类型」" % where)
        add_note(row, alias_note)
        if row["届满日"]:
            row["到期日来源"] = SOURCE_STATED
        check_row_cells(row, where)
        rows.append(row)
    return rows


# ---------------------------------------------------------------- 合并

def merge_one(existing: Dict[str, str], incoming: Dict[str, str]) -> Optional[str]:
    """把一条新行并进底账里的同一件财产。返回给律师看的一句话（没有可说的返回 None）。

    规矩：律师手改过的不被同一份证据覆盖；只有新证据（更晚的届满日、文书写明压过推算）才改日期。
    """
    for col in ("法院", "申请人", "被申请人", "类型", "期限"):
        if not existing[col] and incoming[col]:
            existing[col] = incoming[col]
    add_note(existing, incoming["备注"])

    new_date, old_date = incoming["届满日"], existing["届满日"]
    new_src, old_src = incoming["到期日来源"], existing["到期日来源"]
    if not new_date:
        if not existing["实施日"]:
            existing["实施日"] = incoming["实施日"]
        elif incoming["实施日"] and incoming["实施日"] != existing["实施日"] and old_date:
            # 推算日永不压过底账里已有的日期；是不是续行，律师看了再定
            return ("文书另有实施日 %s，底账没改；若是续行，清空这一行的届满日与到期日来源后重跑"
                    % incoming["实施日"])
        return None
    if not old_date:
        existing["届满日"], existing["到期日来源"] = new_date, new_src
        if incoming["实施日"]:
            existing["实施日"] = incoming["实施日"]
        return None
    if new_date == old_date:
        if new_src == SOURCE_STATED and old_src == SOURCE_INFERRED:
            existing["到期日来源"] = SOURCE_STATED
            return "推算日已由文书确认"
        return None
    if new_date > old_date:
        add_note(existing, "原届满日 %s" % old_date)
        existing["届满日"], existing["到期日来源"] = new_date, new_src
        if incoming["实施日"]:
            existing["实施日"] = incoming["实施日"]
        return "届满日后移：%s → %s" % (old_date, new_date)
    if old_src == SOURCE_INFERRED:
        add_note(existing, "原推算届满日 %s" % old_date)
        existing["届满日"], existing["到期日来源"] = new_date, new_src
        return "文书写明的届满日 %s 早于原推算日 %s，以文书为准" % (new_date, old_date)
    return "文书届满日 %s 早于底账的 %s，看作旧文书，底账不改" % (new_date, old_date)


def merge(rows: List[Dict[str, str]], incoming: List[Dict[str, str]]) -> List[str]:
    notes: List[str] = []
    index = {row_key(r): r for r in rows}
    for new in incoming:
        key = row_key(new)
        if key in index:
            said = merge_one(index[key], new)
            if said:
                notes.append("%s %s：%s" % (new["案号"], new["财产"], said))
        else:
            rows.append(new)
            index[key] = new
    return notes


def infer_all(rows: List[Dict[str, str]]) -> List[str]:
    notes = []
    for row in rows:
        basis = infer_if_blank(row)
        if basis:
            add_note(row, basis)
            said = "%s %s：%s，届满日 %s" % (row["案号"], row["财产"], basis, row["届满日"])
            alias = next((n for n in set(ALIAS_NOTES.values()) if n in row["备注"]), "")
            notes.append(said + ("（%s）" % alias if alias and not row["期限"] else ""))
    return notes


# ---------------------------------------------------------------- 批与提醒

def batch_key(row: Dict[str, str]) -> Tuple[str, str]:
    return row["案号"], row["届满日"]


def group_batches(rows: List[Dict[str, str]]) -> Dict[Tuple[str, str], List[Dict[str, str]]]:
    batches: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for row in rows:
        if row["届满日"]:
            batches.setdefault(batch_key(row), []).append(row)
    return batches


def identifier(key: Tuple[str, str]) -> str:
    return "续保识别码：%s@%s" % key


def first(rows: List[Dict[str, str]], col: str) -> str:
    return next((r[col] for r in rows if r[col]), "")


def is_inferred(rows: List[Dict[str, str]]) -> bool:
    return any(r["到期日来源"] == SOURCE_INFERRED for r in rows)


def parties(rows: List[Dict[str, str]]) -> str:
    a, b = first(rows, "申请人"), first(rows, "被申请人")
    if a and b:
        return "%s诉%s" % (a, b)
    return a or b or rows[0]["案号"]


def title_of(key: Tuple[str, str], rows: List[Dict[str, str]]) -> str:
    deadline = filing_deadline(_dt.date.fromisoformat(key[1]))
    t = "续保｜%s｜截止 %s" % (parties(rows), deadline.isoformat())
    return t + "｜推算待核" if is_inferred(rows) else t


def notes_of(key: Tuple[str, str], rows: List[Dict[str, str]]) -> str:
    expiry = _dt.date.fromisoformat(key[1])
    src = SOURCE_INFERRED + "，待核" if is_inferred(rows) else SOURCE_STATED
    lines = [
        "保全案号：%s" % key[0],
        "法院：%s" % (first(rows, "法院") or "（未写）"),
        "届满日：%s（%s）" % (key[1], src),
        "申请截止日：%s（届满日前七日）" % filing_deadline(expiry).isoformat(),
        "财产：",
    ]
    for i, r in enumerate(rows, start=1):
        extra = "，实施日 %s" % r["实施日"] if r["实施日"] else ""
        lines.append("%d. %s（%s%s）" % (i, r["财产"], r["类型"] or "类型未写", extra))
    lines.append(identifier(key))
    return "\n".join(lines)


def schedule(expiry: _dt.date, now: _dt.datetime) -> Tuple[str, List[_dt.datetime], int]:
    """返回 (状态, 提醒时点, 跳过的时点数)。状态：正常 / 紧急 / 已过期限。"""
    deadline = filing_deadline(expiry)
    if now.date() > deadline:
        return "已过期限", [], 0
    points = [
        _dt.datetime.combine(deadline - _dt.timedelta(days=d), _dt.time(h, m))
        for d in TIER_DAYS
        for h, m in TIER_TIMES
    ]
    future = [p for p in points if p > now]
    if future:
        return "正常", future, len(points) - len(future)
    today = [_dt.datetime.combine(now.date(), _dt.time(h, m)) for h, m in TIER_TIMES]
    later = [p for p in today if p > now]
    if later:
        return "紧急", [later[0]], len(points)
    soon = now + _dt.timedelta(minutes=15)
    soon = soon.replace(second=0, microsecond=0) + _dt.timedelta(minutes=(5 - soon.minute % 5) % 5)
    return "紧急", [soon], len(points)


# ---------------------------------------------------------------- 回显

def md_cell(text: str) -> str:
    return clean_cell(text) or "-"


def render(rows: List[Dict[str, str]], selected: List[Tuple[str, str]], void: List[str],
           notes: List[str], now: _dt.datetime, ledger: str, wrote: bool) -> str:
    batches = group_batches(rows)
    out: List[str] = []
    payload_batches = []
    urgent: List[str] = []
    expired: List[str] = []
    controversy: List[str] = []

    if wrote:
        out.append("保全底账已写：%s" % ledger)
    else:
        out.append("保全底账：%s（未改动）" % ledger)
    out.append("")

    if selected:
        out.append("| 案号 | 当事人 | 财产 | 届满日 | 申请截止日 | 来源 | 提醒 | 状态 |")
        out.append("|---|---|---|---|---|---|---|---|")
    for key in selected:
        brows = batches[key]
        expiry = _dt.date.fromisoformat(key[1])
        status, points, skipped = schedule(expiry, now)
        src = "推算待核" if is_inferred(brows) else SOURCE_STATED
        if status == "已过期限":
            shown = "不建"
            expired.append(key)
        else:
            shown = "%d 条，首条 %s" % (len(points), points[0].strftime("%Y-%m-%d %H:%M"))
            if skipped and status == "正常":
                shown += "（%d 条已过，跳过）" % skipped
            if status == "紧急":
                urgent.append((key, points[0]))
            payload_batches.append({
                "id": identifier(key),
                "title": title_of(key, brows),
                "notes": notes_of(key, brows),
                "alerts": [p.strftime("%Y-%m-%d %H:%M") for p in points],
            })
        out.append("| %s |" % " | ".join(md_cell(x) for x in (
            key[0], parties(brows), "；".join(r["财产"] for r in brows), key[1],
            filing_deadline(expiry).isoformat(), src, shown, status)))
        for r in brows:
            if r["到期日来源"] == SOURCE_INFERRED and not r["期限"] and r["类型"] in PERIODS:
                say = PERIODS[r["类型"]][2]
                if say and say not in controversy:
                    controversy.append(say)
    if not selected:
        out.append("没有新增或变动的批。")
    out.append("")

    for key, when in urgent:
        out.append("**紧急：%s 截止 %s，前 30 天、前 7 天两档时点都已过去，只建一条 %s 的提醒；请今天就办续保。**"
                   % (key[0], filing_deadline(_dt.date.fromisoformat(key[1])).isoformat(),
                      when.strftime("%Y-%m-%d %H:%M")))
    for key in expired:
        out.append("**已过法定申请期限：%s 届满日 %s，申请截止日 %s 已过，不建提醒。请律师自行判断（如问法院可否依职权续行）。**"
                   % (key[0], key[1], filing_deadline(_dt.date.fromisoformat(key[1])).isoformat()))
    missing = [r for r in rows if not r["届满日"]]
    if missing:
        out.append("缺日期，未出提醒（文书里既无届满日也无实施日，或缺类型），请律师给日期：")
        for r in missing:
            out.append("- %s %s" % (r["案号"], r["财产"]))
    if notes:
        out.append("说明：")
        out.extend("- " + n for n in notes)
    for say in controversy:
        out.append("注意：" + say + "。")
    if void:
        out.append("手机上会先删掉这些识别码的旧提醒：%s" % "、".join(v.split("：", 1)[1] for v in void))

    if payload_batches or void:
        payload = {"format": FORMAT_VERSION, "list": REMINDER_LIST, "void": void, "batches": payload_batches}
        out.append("")
        out.append("导入文本（整块复制，到 iPhone 上点快捷指令「续保导入」）：")
        out.append("")
        out.append("```json")
        out.append(json.dumps(payload, ensure_ascii=False, indent=1))
        out.append("```")
    else:
        out.append("")
        out.append("没有要导入手机的提醒。")
    return "\n".join(x for x in out).rstrip() + "\n"


# ---------------------------------------------------------------- 命令

# 进得了提醒标题与备注的列；只改了「期限」「备注」不算这一批变动
REMINDER_COLUMNS = ("案号", "法院", "申请人", "被申请人", "财产", "类型", "实施日", "届满日", "到期日来源")


def snapshot(rows: List[Dict[str, str]], cols=COLUMNS) -> Dict[Tuple[str, str], Tuple[str, ...]]:
    return {row_key(r): tuple(r[c] for c in cols) for r in rows}


def cmd_merge(args) -> str:
    incoming = load_rows(args.rows)
    before, rows, after = read_ledger(args.ledger)
    old_batches = set(group_batches(rows))
    old = snapshot(rows)
    old_seen = snapshot(rows, REMINDER_COLUMNS)
    old_rows = {row_key(r): dict(r) for r in rows}
    notes = merge(rows, incoming)
    notes += infer_all(rows)
    new_seen = snapshot(rows, REMINDER_COLUMNS)

    # 变动的行牵动它前后两个批；还在的批出导入文本，原先就有的批先在手机上删旧（重建或作废）
    touched: List[Tuple[str, str]] = []
    for r in rows:
        k = row_key(r)
        if old_seen.get(k) == new_seen[k]:
            continue
        prev = old_rows.get(k)
        for key in ((prev and prev["届满日"] and batch_key(prev)), r["届满日"] and batch_key(r)):
            if key and key not in touched:
                touched.append(key)
    new_batches = group_batches(rows)
    changed = [k for k in touched if k in new_batches]
    void = [identifier(k) for k in touched if k in old_batches]
    wrote = old != snapshot(rows) or not os.path.exists(args.ledger)
    if wrote:
        write_ledger(args.ledger, render_ledger(before, rows, after))
    return render(rows, changed, void, notes, args.now, args.ledger, wrote)


def cmd_export(args) -> str:
    if not os.path.exists(args.ledger):
        raise Refuse("底账 %s 不在" % args.ledger)
    before, rows, after = read_ledger(args.ledger)
    wanted = {normalize_key(c) for c in (args.case or [])}
    unknown = wanted - {normalize_key(r["案号"]) for r in rows}
    if unknown:
        raise Refuse("底账里没有这些案号：%s" % "、".join(sorted(unknown)))
    old = snapshot(rows)
    notes = infer_all(rows)
    wrote = old != snapshot(rows)
    if wrote:
        write_ledger(args.ledger, render_ledger(before, rows, after))
    selected = [k for k in group_batches(rows) if not wanted or normalize_key(k[0]) in wanted]
    if wanted:
        rows = [r for r in rows if normalize_key(r["案号"]) in wanted]
    void = [identifier(k) for k in selected] if args.replace else []
    return render(rows, selected, void, notes, args.now, args.ledger, wrote)


def parse_now(text: Optional[str]) -> _dt.datetime:
    if not text:
        return _dt.datetime.now().replace(second=0, microsecond=0)
    try:
        return _dt.datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise argparse.ArgumentTypeError("--now 写成 \"YYYY-MM-DD HH:MM\"")


def main(argv: List[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="remind.py", description="续保提醒：并底账、推算届满日、拼导入文本")
    sub = parser.add_subparsers(dest="command", required=True)
    p_merge = sub.add_parser("merge", help="并入财产行并导出本次新增或变动的批")
    p_merge.add_argument("--ledger", required=True, help="保全底账路径（输入 PDF 同目录的 保全期限.md）")
    p_merge.add_argument("--rows", required=True, help="财产行 JSON 文件路径；- 表示标准输入")
    p_merge.add_argument("--now", type=parse_now, default=None, help="当前时刻，测试用")
    p_export = sub.add_parser("export", help="从底账重新导出")
    p_export.add_argument("--ledger", required=True, help="保全底账路径")
    p_export.add_argument("--case", action="append", help="只导这个案号，可给多次")
    p_export.add_argument("--replace", action="store_true", help="手机上先删同识别码的旧提醒再建")
    p_export.add_argument("--now", type=parse_now, default=None, help="当前时刻，测试用")
    args = parser.parse_args(argv)
    if args.now is None:
        args.now = parse_now(None)
    try:
        text = cmd_merge(args) if args.command == "merge" else cmd_export(args)
    except Refuse as e:
        print("拒绝：%s（底账一字未动）" % e, file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
