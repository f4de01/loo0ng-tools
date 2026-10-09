#!/usr/bin/env python3
"""生成快捷指令「续保导入」的 .shortcut 文件；在 Mac 上可顺手签名。开发者工具，不随 skill 包分发。

为什么有它：手搭要在 iPhone 上一个个点几十个动作。这里按导入文本的格式把动作写成 plist；未签名的文件
iOS 15 起导入不了，要在 Mac 上用系统自带的 shortcuts sign 签一次，签好的文件隔空投送或经 iCloud 云盘
到 iPhone 上打开即可添加，再从手机分享出 iCloud 链接。

手边没有 Mac 时用 --hubsign：交 RoutineHub 的远程签名服务 HubSign。它只认 Cherri 编译器的请求
（按 User-Agent 放行），这里借用 Cherri 的标识，是开发者知情后的决定；发过去的只有动作逻辑，不含案件信息。

动作参数照 Apple ToolKit 导出的参数表与公开的快捷指令样本写，Apple 没有文档；改了要在真机上走一遍
docs/快捷指令搭建.md 的「试跑」。逻辑与导入文本格式以 skills/xubao/FORMAT.md 为准：

  1. 读剪贴板，取词典；取不到 format 就提示「剪贴板里不是导入文本」，不是 1 就提示「请更新快捷指令」。
  2. 找出标题以「续保｜」开头的全部提醒，逐条读备注最后一行的识别码：
     在 void 里的收进待删，其余收进已有。待删的一次删掉（系统会确认一次）。
  3. 逐批：识别码在已有里就跳过；不在就按 alerts 每个时点新建一条提醒（列表取导入文本的 list）。
  4. 通知新建几批、跳过几批。

查重不靠「查找提醒事项」按备注过滤：参数表里提醒事项能过滤的属性没有备注，读备注只能靠「获取提醒事项的详细信息」。

用法：
  python scripts/build-shortcut.py [--out <目录>] [--sign]
默认写到 .scratch/（已忽略）：续保导入-未签名.shortcut；在 Mac 上给 --sign 再写 续保导入.shortcut（已签名）。
不在 Mac 上：给 --hubsign 远程签名；或把未签名文件拷到任意一台 Mac（登录 Apple ID）上执行
  shortcuts sign --mode anyone --input 续保导入-未签名.shortcut --output 续保导入.shortcut
退出码：0 成功；1 签名失败；2 用法错。
"""
import argparse
import json
import pathlib
import plistlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

NAME = "续保导入"
FORMAT_VERSION = "1"
TITLE_PREFIX = "续保｜"
ID_PREFIX = "续保识别码："
HUBSIGN_URL = "https://hubsign.routinehub.services/sign"
HUBSIGN_AGENT = "cherri/loo0ng-tools"  # HubSign 只放行 Cherri 的请求，见文件开头
REPO = pathlib.Path(__file__).resolve().parents[1]
OBJ = "￼"  # 文本里插变量的占位字符


# ---------------------------------------------------------------- 变量与文本

def output(uid, name):
    """某个动作的输出。"""
    return {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}


def named(name):
    return {"Type": "Variable", "VariableName": name}


def dict_value(ref, key):
    """引用后接「词典值」：词典[key]。"""
    return dict(ref, Aggrandizements=[{"Type": "WFDictionaryValueVariableAggrandizement", "DictionaryKey": key}])


def attach(ref):
    return {"Value": ref, "WFSerializationType": "WFTextTokenAttachment"}


def text(*parts):
    """文本，parts 里字符串原样、dict 是变量引用。"""
    s, ranges = "", {}
    for p in parts:
        if isinstance(p, str):
            s += p
        else:
            ranges["{%d, 1}" % utf16_len(s)] = p
            s += OBJ
    return {"Value": {"string": s, "attachmentsByRange": ranges}, "WFSerializationType": "WFTextTokenString"}


def utf16_len(s):
    return len(s.encode("utf-16-le")) // 2


def cond_input(ref):
    return {"Type": "Variable", "Variable": attach(ref)}


# ---------------------------------------------------------------- 动作

class Builder:
    def __init__(self):
        self.actions = []

    def add(self, ident, params=None, uid=None):
        params = dict(params or {})
        if uid:
            params["UUID"] = uid
        self.actions.append({"WFWorkflowActionIdentifier": "is.workflow.actions." + ident,
                             "WFWorkflowActionParameters": params})
        return uid

    def act(self, ident, params=None):
        """带输出的动作，返回它的 UUID。"""
        return self.add(ident, params, str(uuid.uuid4()).upper())

    # 控制流：返回分组号，调 otherwise / end 收尾
    def if_(self, ref, condition, string=None):
        group = str(uuid.uuid4()).upper()
        params = {"GroupingIdentifier": group, "WFControlFlowMode": 0, "WFCondition": condition,
                  "WFInput": cond_input(ref)}
        if string is not None:
            params["WFConditionalActionString"] = string
        self.add("conditional", params)
        return group

    def otherwise(self, group):
        self.add("conditional", {"GroupingIdentifier": group, "WFControlFlowMode": 1})

    def end_if(self, group):
        self.act("conditional", {"GroupingIdentifier": group, "WFControlFlowMode": 2})

    def repeat_each(self, ref, depth=1):
        """返回 (分组号, 本层的重复项目)。照 Apple 导出的写法按名字引用：外层「Repeat Item」，内一层「Repeat Item 2」。"""
        group = str(uuid.uuid4()).upper()
        self.add("repeat.each", {"GroupingIdentifier": group, "WFControlFlowMode": 0, "WFInput": attach(ref)},
                 str(uuid.uuid4()).upper())
        return group, named("Repeat Item" if depth == 1 else "Repeat Item %d" % depth)

    def end_repeat(self, group):
        self.act("repeat.each", {"GroupingIdentifier": group, "WFControlFlowMode": 2})

    def alert_and_stop(self, message):
        self.add("alert", {"WFAlertActionTitle": NAME, "WFAlertActionMessage": message,
                           "WFAlertActionCancelButtonShown": False})
        self.add("exit")

    def comment(self, words):
        self.add("comment", {"WFCommentActionText": words})

    def set_var(self, name, ref):
        self.add("setvariable", {"WFVariableName": name, "WFInput": attach(ref)})

    def append_var(self, name, ref):
        self.add("appendvariable", {"WFVariableName": name, "WFInput": attach(ref)})

    def get_key(self, ref, key):
        return self.act("getvalueforkey", {"WFGetDictionaryValueType": "Value", "WFDictionaryKey": key,
                                           "WFInput": attach(ref)})


IF_CONTAINS, IF_IS_NOT, IF_BEGINS, IF_HAS_VALUE, IF_NO_VALUE = 99, 5, 8, 100, 101


def build_actions():
    b = Builder()

    # 1. 读剪贴板，取词典，守格式号
    b.comment("续保导入：读剪贴板里的导入文本（由续保提醒 skill 生成），建进提醒事项。\n1. 取词典，格式号不是 1 就停。")
    clip = b.act("getclipboard")
    dic = b.act("detect.dictionary", {"WFInput": attach(output(clip, "Clipboard"))})
    D = output(dic, "Dictionary")
    fmt = b.get_key(D, "format")
    fmt_text = b.act("gettext", {"WFTextActionText": text(output(fmt, "Dictionary Value"))})  # 数字 1 转成文本再比
    FMT = output(fmt_text, "Text")
    g = b.if_(FMT, IF_NO_VALUE)
    b.alert_and_stop("剪贴板里不是导入文本。请回到电脑上整块复制导入文本，再点一次。")
    b.end_if(g)
    g = b.if_(FMT, IF_IS_NOT, FORMAT_VERSION)
    b.alert_and_stop("请更新快捷指令：导入文本的格式变了，重新点安装链接装新版。")
    b.end_if(g)

    # 2. 作废识别码合成一段文本，扫一遍「续保｜」开头的提醒
    b.comment("2. 扫一遍标题以「续保｜」开头的提醒，读备注最后一行的识别码：在 void 里的删掉，其余记为已有。")
    void = b.get_key(D, "void")
    void_text = b.act("text.combine", {"text": attach(output(void, "Dictionary Value")), "WFTextSeparator": "New Lines"})
    found = b.act("filter.reminders", {"WFContentItemFilter": {
        "Value": {
            "WFActionParameterFilterPrefix": 1,
            "WFContentPredicateBoundedDate": False,
            "WFActionParameterFilterTemplates": [{
                "Operator": 8, "Property": "Title", "Removable": True,
                "Values": {"String": TITLE_PREFIX, "Unit": 4},
            }],
        },
        "WFSerializationType": "WFContentPredicateTableTemplate",
    }})
    loop, reminder = b.repeat_each(output(found, "Reminders"))
    notes = b.act("properties.reminders", {"WFContentItemPropertyName": "Notes", "WFInput": attach(reminder)})
    lines = b.act("text.split", {"text": attach(output(notes, "Notes")), "WFTextSeparator": "New Lines"})
    last = b.act("getitemfromlist", {"WFItemSpecifier": "Last Item", "WFInput": attach(output(lines, "Split Text"))})
    LAST = output(last, "Item from List")
    g1 = b.if_(LAST, IF_BEGINS, ID_PREFIX)
    g2 = b.if_(output(void_text, "Combined Text"), IF_CONTAINS, text(LAST))
    b.append_var("待删", reminder)
    b.otherwise(g2)
    b.append_var("已有", LAST)
    b.end_if(g2)
    b.end_if(g1)
    b.end_repeat(loop)

    g = b.if_(named("待删"), IF_HAS_VALUE)
    b.add("removereminders", {"WFInputReminders": attach(named("待删"))})
    b.end_if(g)
    have = b.act("text.combine", {"text": attach(named("已有")), "WFTextSeparator": "New Lines"})
    HAVE = output(have, "Combined Text")

    # 3. 逐批新建
    b.comment("3. 逐批：识别码已有就跳过；没有就按 alerts 每个时点建一条提醒。")
    batches = b.get_key(D, "batches")
    outer, batch = b.repeat_each(output(batches, "Dictionary Value"))
    bid = b.get_key(batch, "id")
    title = b.get_key(batch, "title")
    note = b.get_key(batch, "notes")
    alerts = b.get_key(batch, "alerts")
    ID, TITLE, NOTE = (output(u, "Dictionary Value") for u in (bid, title, note))
    g = b.if_(HAVE, IF_CONTAINS, text(ID))
    b.append_var("跳过", TITLE)
    b.otherwise(g)
    inner, moment = b.repeat_each(output(alerts, "Dictionary Value"), depth=2)
    when = b.act("detect.date", {"WFInput": attach(moment)})
    b.act("addnewreminder", {
        "WFCalendarItemTitle": text(TITLE),
        "WFCalendarDescriptor": attach(dict_value(D, "list")),
        "WFAlertEnabled": "Alert",
        "WFAlertCondition": "At Time",
        # 日期格子是可输入文字的格子，要写成「文本里插变量」；直接挂变量，真机报「提供的提醒时间无效」
        "WFAlertCustomTime": text(output(when, "Dates")),
        "WFCalendarItemNotes": text(NOTE),
    })
    b.end_repeat(inner)
    b.append_var("新建", TITLE)
    b.end_if(g)
    b.end_repeat(outer)

    # 4. 通知
    b.comment("4. 通知新建几批、跳过几批。")
    # 「计数」的输入键：参数表写 Input，旧导出写 WFInput，两个都给
    made = b.act("count", {"WFCountType": "Items", "Input": attach(named("新建")), "WFInput": attach(named("新建"))})
    skipped = b.act("count", {"WFCountType": "Items", "Input": attach(named("跳过")), "WFInput": attach(named("跳过"))})
    b.add("notification", {"WFNotificationActionTitle": NAME, "WFNotificationActionBody": text(
        "新建 ", output(made, "Count"), " 批，跳过 ", output(skipped, "Count"), " 批（已有）")})
    return b.actions


def build_workflow():
    return {
        "WFWorkflowName": NAME,
        "WFWorkflowClientVersion": "2700.0.4",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 59511},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFQuickActionSurfaces": [],
        "WFWorkflowActions": build_actions(),
    }


def sign(unsigned: pathlib.Path, signed: pathlib.Path) -> None:
    """用 Mac 自带的 shortcuts sign 签名：anyone 模式，谁拿到都能导入。"""
    if shutil.which("shortcuts") is None:
        raise RuntimeError("这台机器没有 shortcuts 命令，签名要在 Mac 上做（见本文件开头的说明）")
    proc = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(unsigned), "--output", str(signed)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not signed.exists():
        raise RuntimeError("shortcuts sign 失败：%s" % (proc.stderr.strip() or proc.returncode))


def hubsign(unsigned: pathlib.Path, signed: pathlib.Path) -> None:
    body = json.dumps({"shortcutName": NAME, "shortcut": unsigned.read_text(encoding="utf-8")}).encode("utf-8")
    req = urllib.request.Request(HUBSIGN_URL, data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": HUBSIGN_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except urllib.error.URLError as e:
        raise RuntimeError("连不上 HubSign：%s" % e)
    if not data.startswith(b"AEA1"):  # 签好的快捷指令是 Apple 加密归档，以 AEA1 开头
        raise RuntimeError("HubSign 回的不是签好的快捷指令（%d 字节），服务可能改了规矩或暂停了" % len(data))
    signed.write_bytes(data)


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="build-shortcut.py", description="生成快捷指令「续保导入」")
    parser.add_argument("--out", default=str(REPO / ".scratch"), help="输出目录（默认 .scratch/）")
    how = parser.add_mutually_exclusive_group()
    how.add_argument("--sign", action="store_true", help="在 Mac 上用 shortcuts sign 签名")
    how.add_argument("--hubsign", action="store_true", help="交 HubSign 远程签名（借用 Cherri 的标识）")
    args = parser.parse_args(argv)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    xml = plistlib.dumps(build_workflow(), fmt=plistlib.FMT_XML)
    unsigned = out / ("%s-未签名.shortcut" % NAME)
    unsigned.write_bytes(xml)
    print("未签名：%s（%d 个动作）" % (unsigned, xml.count(b"<key>WFWorkflowActionIdentifier</key>")))
    if args.sign or args.hubsign:
        path = out / ("%s.shortcut" % NAME)
        try:
            (sign if args.sign else hubsign)(unsigned, path)
        except (RuntimeError, OSError) as e:
            print("签名失败：%s" % e, file=sys.stderr)
            return 1
        print("已签名：%s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
