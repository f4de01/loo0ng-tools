"""生成 preservation-reminder 的合成测试件：一份虚构的财产保全告知书，文字版与扫描版各一。

开发机一次性脚本，要 PyMuPDF（pip install pymupdf）；生成的两份 PDF 入库，测试只读它们、只用标准库。
内容全部虚构，不取材于任何真件。案号在运行时由碎片拼出：本文件自己也要过隐私钩子。

  文字版：嵌开源字体 Droid Sans Fallback 的子集，Type0 / Identity-H + ToUnicode，压缩流，真实文书最常见的形状。
  扫描版：把文字版第一页按 150 dpi 渲染成灰度 JPEG，包成只有一张图的 PDF（DCTDecode），没有文字层。

运行：python tests/preservation-reminder/fixtures/make_fixtures.py
"""
import pathlib

import pymupdf

HERE = pathlib.Path(__file__).resolve().parent
TEXT_PDF = HERE / "告知书-文字版.pdf"
SCAN_PDF = HERE / "告知书-扫描版.pdf"


def j(*parts):
    return "".join(parts)


CASE_NO = j("（2026）", "测01", "执保", "1号")
MERITS_NO = j("（2026）", "测0101", "民初", "1号")

LINES = [
    ("center", 16, "测试市测试区人民法院"),
    ("center", 20, "财产保全告知书"),
    ("right", 12, CASE_NO),
    ("left", 12, ""),
    ("left", 12, "甲测试有限公司："),
    ("para", 12, "你单位与乙测试有限公司买卖合同纠纷一案，本院依你单位申请，作出" + MERITS_NO
     + "民事裁定书，已对被申请人乙测试有限公司的下列财产采取保全措施，现将有关事项告知如下："),
    ("para", 12, "一、冻结被申请人乙测试有限公司在丙测试银行测试支行开立的账号为 6222 0000 0000 0000 的银行存款"
     "人民币100000元，冻结期限一年，自2026年3月2日起至2027年3月1日止。"),
    ("para", 12, "二、查封被申请人乙测试有限公司名下位于测试市测试区测试路1号的房产，查封期限三年，"
     "自2026年3月3日起至2029年3月2日止。"),
    ("para", 12, "三、扣押被申请人乙测试有限公司名下车牌号为测A00000的小型普通客车一辆，扣押日期为2026年3月5日。"),
    ("para", 12, "申请保全人申请续行财产保全的，应当在保全期限届满七日前向本院提出；逾期申请或者不申请的，"
     "自行承担不能续行保全的法律后果。"),
    ("para", 12, "特此告知。"),
    ("right", 12, "二〇二六年三月六日"),
    ("left", 12, ""),
    ("left", 10, "（本件为合成测试件，内容全部虚构）"),
]

PAGE_W, PAGE_H, MARGIN = 595, 842, 72


def wrap(text, size):
    per_line = int((PAGE_W - 2 * MARGIN) // size)
    first = per_line - 2
    out, rest = [text[:first]], text[first:]
    while rest:
        out.append(rest[:per_line])
        rest = rest[per_line:]
    return out


def make_text_pdf():
    font = pymupdf.Font("cjk")
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    tw = pymupdf.TextWriter(page.rect)
    y = MARGIN + 20
    for kind, size, text in LINES:
        if kind == "para":
            for i, line in enumerate(wrap(text, size)):
                x = MARGIN + (2 * size if i == 0 else 0)
                tw.append((x, y), line, font=font, fontsize=size)
                y += size * 1.8
            continue
        width = font.text_length(text, fontsize=size)
        x = {"center": (PAGE_W - width) / 2, "right": PAGE_W - MARGIN - width}.get(kind, MARGIN)
        if text:
            tw.append((x, y), text, font=font, fontsize=size)
        y += size * 1.8
    tw.write_text(page)
    doc.subset_fonts()
    doc.save(TEXT_PDF, garbage=4, deflate=True)
    doc.close()


def make_scan_pdf():
    src = pymupdf.open(TEXT_PDF)
    pix = src[0].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    jpeg = pix.tobytes("jpeg", jpg_quality=70)
    src.close()
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_image(page.rect, stream=jpeg)
    doc.save(SCAN_PDF, garbage=4, deflate=True)
    doc.close()


if __name__ == "__main__":
    make_text_pdf()
    make_scan_pdf()
    print("已生成：%s、%s" % (TEXT_PDF.name, SCAN_PDF.name))
