"""skills/xubao/scripts/pdf_text.py 的测试（unittest，标准库零依赖）。

运行：python -m unittest discover -s tests/xubao -p "test_*.py"

两份合成件（fixtures/，由 fixtures/make_fixtures.py 生成）走真实形状；别的分支用下面现拼的小 PDF。
案号由碎片拼出：本文件自己也要过隐私钩子。
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zlib

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills" / "xubao" / "scripts" / "pdf_text.py"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def j(*parts):
    return "".join(parts)


def text_of(raw):
    return raw.decode("utf-8").replace("\r\n", "\n")


def build_pdf(objects, trailer_extra=b""):
    """objects: [对象体 bytes]，第 i 个是 i+1 号对象；1 号须是 Catalog。带 xref，便于别的阅读器也能开。"""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R %s>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, trailer_extra, xref)
    return bytes(out)


def stream(dict_body, data, flate=False):
    if flate:
        data = zlib.compress(data)
        dict_body += b" /Filter /FlateDecode"
    return b"<< %s /Length %d >>\nstream\n" % (dict_body, len(data)) + data + b"\nendstream"


def one_page(content, resources, extra_objects=()):
    """1 Catalog，2 Pages，3 Page，4 内容流，5 起是 extra_objects。"""
    return [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources " + resources + b" /Contents 4 0 R >>",
        stream(b"", content, flate=True),
        *extra_objects,
    ]


def ucs2_hex(text):
    return b"<" + text.encode("utf-16-be").hex().upper().encode() + b">"


class PdfCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.images = self.dir / "图"

    def tearDown(self):
        self._tmp.cleanup()

    def run_on(self, *paths):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, paths), "--images", str(self.images)], capture_output=True)
        self.assertEqual(proc.returncode, 0, text_of(proc.stderr))
        return text_of(proc.stdout)

    def write_pdf(self, name, data):
        p = self.dir / name
        p.write_bytes(data)
        return p


class FixtureTests(PdfCase):
    def test_text_version_reads_key_facts(self):
        out = self.run_on(FIXTURES / "告知书-文字版.pdf")
        self.assertIn("## 第 1 页\n", out)
        flat = "".join(out.split())
        for fact in ("财产保全告知书", j("（2026）", "测01", "执保", "1号"), "测试市测试区人民法院",
                     "自2026年3月2日起至2027年3月1日止", "自2026年3月3日起至2029年3月2日止",
                     "扣押日期为2026年3月5日", "测A00000"):
            self.assertIn(fact, flat)
        self.assertNotIn("�", out)
        self.assertFalse(self.images.exists(), "有文字层的页不导图")

    def test_scan_version_exports_jpeg(self):
        out = self.run_on(FIXTURES / "告知书-扫描版.pdf")
        self.assertIn("## 第 1 页：无文字层，导出了 1 张图", out)
        jpgs = list(self.images.glob("*.jpg"))
        self.assertEqual(len(jpgs), 1)
        self.assertTrue(jpgs[0].read_bytes().startswith(b"\xff\xd8"))
        self.assertIn(str(jpgs[0].name), out)


class BranchTests(PdfCase):
    def test_ucs2_cmap_without_tounicode(self):
        font = b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H >>"
        content = b"BT /F1 12 Tf 72 700 Td " + ucs2_hex("冻结期限一年") + b" Tj 0 -20 Td " + ucs2_hex("至2027年3月1日止") + b" Tj ET"
        out = self.run_on(self.write_pdf("a.pdf", build_pdf(one_page(content, b"<< /Font << /F1 5 0 R >> >>", [font]))))
        self.assertIn("冻结期限一年\n至2027年3月1日止", out)

    def test_identity_without_tounicode_is_garbled(self):
        font = b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H >>"
        content = b"BT /F1 12 Tf 72 700 Td <0012003400560078009A00BC00DE00F0> Tj ET"
        out = self.run_on(self.write_pdf("b.pdf", build_pdf(one_page(content, b"<< /Font << /F1 5 0 R >> >>", [font]))))
        self.assertIn("疑似乱码，没有导出图片，请用别的办法看原件这一页", out)

    def test_tounicode_bfrange_and_bfchar(self):
        cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
                b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
                b"1 beginbfchar <0001> <4FDD> endbfchar\n"
                b"1 beginbfrange <0002> <0003> <5168> endbfrange\n"
                b"1 beginbfrange <0010> <0011> [<544A> <77E5>] endbfrange\n"
                b"endcmap end end")
        font = b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H /ToUnicode 6 0 R >>"
        content = b"BT /F1 12 Tf 72 700 Td [<0001> -100 <00020003> -400 <00100011>] TJ ET"
        objs = one_page(content, b"<< /Font << /F1 5 0 R >> >>", [font, stream(b"", cmap, flate=True)])
        out = self.run_on(self.write_pdf("c.pdf", build_pdf(objs)))
        self.assertIn("保全兩 告知", out, "bfchar、bfrange 两种写法；大于 250 的间距补空格")

    def test_flate_gray_image_becomes_png(self):
        w, h = 4, 2
        pixels = bytes([0, 64, 128, 255] * h)
        img = stream(b"/Type /XObject /Subtype /Image /Width 4 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8",
                     pixels, flate=True)
        content = b"q 595 0 0 842 0 0 cm /Im1 Do Q"
        objs = one_page(content, b"<< /XObject << /Im1 5 0 R >> >>", [img])
        out = self.run_on(self.write_pdf("d.pdf", build_pdf(objs)))
        self.assertIn("无文字层，导出了 1 张图", out)
        png = next(self.images.glob("*.png")).read_bytes()
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(png[16:24], (w).to_bytes(4, "big") + (h).to_bytes(4, "big"))

    def test_ccitt_image_is_reported_not_exported(self):
        img = stream(b"/Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceGray "
                     b"/BitsPerComponent 1 /Filter /CCITTFaxDecode", b"\x00" * 8)
        objs = one_page(b"/Im1 Do", b"<< /XObject << /Im1 5 0 R >> >>", [img])
        out = self.run_on(self.write_pdf("e.pdf", build_pdf(objs)))
        self.assertIn("传真编码", out)
        self.assertIn("请用别的办法看原件这一页", out)

    def test_encrypted_is_reported(self):
        objs = one_page(b"", b"<< >>", [b"<< /Filter /Standard /V 2 /R 3 >>"])
        out = self.run_on(self.write_pdf("f.pdf", build_pdf(objs, b"/Encrypt 5 0 R ")))
        self.assertIn("加密 PDF，脚本抽不出", out)

    def test_not_a_pdf(self):
        out = self.run_on(self.write_pdf("g.pdf", b"hello"))
        self.assertIn("读不了：不是 PDF", out)

    def test_several_files_in_one_run(self):
        out = self.run_on(FIXTURES / "告知书-文字版.pdf", self.write_pdf("g.pdf", b"hello"))
        self.assertIn("# 告知书-文字版.pdf（共 1 页）", out)
        self.assertIn("# g.pdf", out)


if __name__ == "__main__":
    unittest.main()
