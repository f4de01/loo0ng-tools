"""画「续保提醒一图流」docs/一图流.png：从装 skill 到手机上收提醒，单列竖图。

开发机一次性脚本，要 Pillow（pip install pillow）与 Windows 自带的微软雅黑、Consolas；安装链接、响铃点数变了就重画。

运行：python scripts/draw-guide.py docs/一图流.png
"""
import sys
from PIL import Image, ImageDraw, ImageFont

OUT = sys.argv[1]
W = 1500
PAD = 70
INNER = W - 2 * PAD
BG = (248, 249, 251)
INK = (30, 34, 40)
MUTE = (95, 102, 112)
CARD = (255, 255, 255)
LINE = (220, 224, 230)
CODE_BG = (240, 242, 246)
ACCENT = [(0, 122, 255), (52, 199, 89), (255, 149, 0), (175, 82, 222), (255, 59, 48)]

REG = "C:/Windows/Fonts/msyh.ttc"
BOLD = "C:/Windows/Fonts/msyhbd.ttc"
MONO = "C:/Windows/Fonts/consola.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


F_TITLE = font(BOLD, 60)
F_SUB = font(REG, 30)
F_H = font(BOLD, 40)
F_TAG = font(BOLD, 26)
F_B = font(REG, 30)
F_BB = font(BOLD, 30)
F_CODE = font(MONO, 27)
F_CODE_CJK = font(REG, 26)
F_SMALL = font(REG, 25)
F_NUM = font(BOLD, 44)

STAGES = [
    ("电脑上装 skill", "一次", [
        ("t", "机器上要有 Python 3.9 以上，不用另装任何包。"),
        ("c", "npx skills@latest add f4de01/loo0ng-tools -a codex -g"),
        ("t", "用 Claude Code 就把 -a codex 换成 -a claude-code。装完要开新会话。"),
        ("t", "skill 更新了也是再跑一遍这条命令。"),
    ]),
    ("iPhone 上装快捷指令「续保导入」", "一次", [
        ("t", "在 iPhone 上打开安装链接，点「添加快捷指令」："),
        ("c", "https://www.icloud.com/shortcuts/50a09fab8650481d8b1386d25b6901ab"),
        ("t", "提醒事项 App 里新建一个叫「续保」的列表。"),
        ("t", "第一次运行会问能否访问提醒事项，点允许。"),
        ("t", "可选：设置 → 快捷指令 → 高级 → 打开「允许删除而不确认」，重导旧批时少点几次确认。"),
    ]),
    ("电脑上做提醒", "每次", [
        ("t", "把保全告知书、裁定书等 PDF 给 agent，说一句："),
        ("q", "给这几份文书做续保提醒"),
        ("t", "模型读文书、认财产，脚本算日子。回显先是摘要表，再是加粗的警告，最后是一个 JSON 代码块（导入文本）。"),
        ("t", "PDF 同目录会生成「保全期限.md」底账，一项财产一行，可以直接改单元格。"),
        ("t", "届满日文书没写的按类型推算，标题带「推算待核」，核过后改底账重导。"),
        ("t", "只有裁定书时模型会先要告知书；坚持用落款日的，模型会问实际保全了什么财产。"),
    ]),
    ("导进手机", "每次", [
        ("b", "Windows："),
        ("t", "点代码块右上角的复制 → 粘进电脑版微信发给「文件传输助手」 → 手机微信长按这条消息点「复制」 → 点快捷指令「续保导入」。"),
        ("b", "Mac："),
        ("t", "点代码块的复制，iPhone 与 Mac 同一 Apple ID 时通用剪贴板已带到手机，直接点快捷指令「续保导入」。"),
        ("t", "跑完弹通知「新建 N 批，跳过 M 批」。同一段文本导两次不会重复；中途断了再导一次自动补。"),
    ]),
    ("手机上的提醒", "到点响", [
        ("t", "「续保」列表里一批财产 4 条普通提醒：申请截止日前 30 天、前 7 天两档，每档 10:00 与 17:00 各一条。"),
        ("q", "续保｜甲公司诉乙公司｜截止 2027-02-22｜前 7 天"),
        ("t", "「截止」是续保申请书最晚交到法院的那天：届满日前七日，法院要求更早的按更早的。"),
        ("t", "备注里有案号、法院、届满日、财产清单。静音或专注模式下不出声，通知中心事后看得到。"),
        ("b", "递交了续保申请书，就把这一批的几条都勾掉。"),
    ]),
]

TROUBLE = [
    ("请更新快捷指令", "导入文本格式变了，重新点安装链接装新版。"),
    ("不支持子任务", "装的是旧版快捷指令，重新点安装链接装新版再导一次。"),
    ("剪贴板里不是导入文本", "整块复制，从 { 到 } 一个字符都不能少。"),
    ("引号成了中文引号", "别经过会改标点的输入法或聊天软件，走文件传输助手或通用剪贴板。"),
]


def tokens(text):
    """中文逐字可断，ASCII 连成一段不断（时刻、命令、网址不拆）。"""
    out, cur = [], ""
    for ch in text:
        if ord(ch) < 128 and ch != " ":
            cur += ch
        else:
            if cur:
                out.append(cur)
                cur = ""
            out.append(ch)
    if cur:
        out.append(cur)
    return out


def wrap(draw, text, f, width):
    lines, cur = [], ""
    for tok in tokens(text):
        if cur and draw.textlength(cur + tok, font=f) > width:
            lines.append(cur.rstrip())
            cur = tok.lstrip()
        else:
            cur += tok
    if cur:
        lines.append(cur)
    return lines


def measure():
    """先在一张临时画布上算总高度。"""
    img = Image.new("RGB", (W, 100))
    d = ImageDraw.Draw(img)
    return d


def draw_item(d, kind, text, x, y, width, color, real=True):
    if kind == "c":
        h = 56
        if real:
            d.rounded_rectangle([x, y, x + width, y + h], radius=10, fill=CODE_BG)
            f = F_CODE if all(ord(ch) < 128 for ch in text) else F_CODE_CJK
            d.text((x + 18, y + 12), text, font=f, fill=INK)
        return h + 14
    if kind == "q":
        h = 56
        if real:
            d.rounded_rectangle([x, y, x + width, y + h], radius=10, fill=CODE_BG)
            d.text((x + 18, y + 11), text, font=F_B, fill=INK)
        return h + 14
    f = F_BB if kind == "b" else F_B
    lines = wrap(d, text, f, width - 36)
    lh = 44
    if real:
        d.ellipse([x + 4, y + 17, x + 14, y + 27], fill=color)
        for i, line in enumerate(lines):
            d.text((x + 30, y + i * lh), line, font=f, fill=INK)
    return len(lines) * lh + 8


def render(real, img=None):
    d = ImageDraw.Draw(img) if img is not None else measure()
    y = PAD
    if real:
        d.text((PAD, y), "续保提醒一图流", font=F_TITLE, fill=INK)
        d.text((PAD, y + 80), "律师工具箱 loo0ng-tools · xubao：从装 skill 到手机上到点响铃", font=F_SUB, fill=MUTE)
    y += 150
    for i, (title, tag, items) in enumerate(STAGES):
        color = ACCENT[i]
        # 先量卡片高度
        cy = 110
        for kind, text in items:
            cy += draw_item(d, kind, text, 0, 0, INNER - 2 * 40, color, real=False)
        card_h = cy + 20
        if real:
            d.rounded_rectangle([PAD, y, PAD + INNER, y + card_h], radius=24, fill=CARD, outline=LINE, width=2)
            d.rounded_rectangle([PAD, y, PAD + 14, y + card_h], radius=7, fill=color)
            d.ellipse([PAD + 40, y + 28, PAD + 104, y + 92], fill=color)
            num = str(i + 1)
            tw = d.textlength(num, font=F_NUM)
            d.text((PAD + 72 - tw / 2, y + 32), num, font=F_NUM, fill=(255, 255, 255))
            d.text((PAD + 130, y + 36), title, font=F_H, fill=INK)
            ttw = d.textlength(tag, font=F_TAG)
            d.rounded_rectangle([PAD + INNER - 60 - ttw, y + 40, PAD + INNER - 30, y + 84], radius=22, fill=color)
            d.text((PAD + INNER - 45 - ttw, y + 46), tag, font=F_TAG, fill=(255, 255, 255))
        iy = y + 110
        for kind, text in items:
            iy += draw_item(d, kind, text, PAD + 40, iy, INNER - 2 * 40, color, real=real)
        y += card_h
        if i < len(STAGES) - 1:
            if real:
                cx = PAD + 72
                d.line([cx, y + 8, cx, y + 44], fill=MUTE, width=5)
                d.polygon([(cx - 14, y + 40), (cx + 14, y + 40), (cx, y + 58)], fill=MUTE)
            y += 66
        else:
            y += 40
    # 排错条
    if real:
        d.text((PAD, y), "快捷指令报错时", font=F_H, fill=INK)
    y += 66
    for k, v in TROUBLE:
        lines = wrap(d, v, F_SMALL, INNER - 420)
        h = max(48, len(lines) * 34 + 14)
        if real:
            d.rounded_rectangle([PAD, y, PAD + INNER, y + h], radius=12, fill=CARD, outline=LINE, width=2)
            d.text((PAD + 20, y + 9), k, font=F_BB, fill=ACCENT[4])
            for j, line in enumerate(lines):
                d.text((PAD + 400, y + 10 + j * 34), line, font=F_SMALL, fill=INK)
        y += h + 12
    y += 30
    if real:
        d.text((PAD, y), "仓库：github.com/f4de01/loo0ng-tools    详细说明：skills/xubao/导入手机.md", font=F_SMALL, fill=MUTE)
    y += 40 + PAD
    return y


H = render(False)
img = Image.new("RGB", (W, H), BG)
render(True, img)
img.save(OUT, optimize=True)
print("已生成", OUT, img.size)
