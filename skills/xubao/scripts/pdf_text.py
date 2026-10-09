#!/usr/bin/env python3
"""PDF 抽文字：逐页抽文字层；抽不出或像乱码的页，把页里的图片导出成文件给模型看。标准库零依赖。

用法：
  python pdf_text.py <a.pdf> [<b.pdf> ...] [--images <目录>]

回显按文件、按页：有文字层的页给文字；没有文字层、文字很少或像乱码的页标出来，并列出导出的图片路径
（JPEG 原样导出；无损压缩的灰度、彩色、调色板、黑白图转成 PNG）。导不出的照实说（加密 PDF、
传真编码或 JBIG2 的黑白扫描、没有图片的乱码页），交给模型用别的办法看原件那一页。

不做 OCR，不渲染矢量页面。图片默认导到系统临时目录下新建的一个目录，不往案件目录里写。
退出码：0 跑到底（个别文件读不了也在回显里说）；2 用法错。
"""
import argparse
import base64
import binascii
import os
import re
import struct
import sys
import tempfile
import zlib
from typing import Dict, List, Optional, Tuple

WHITESPACE = b" \t\r\n\x0c\x00"
DELIMITERS = b"()<>[]{}/%"
NUMBER_RE = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
OBJ_RE = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")
MIN_TEXT_CHARS = 30  # 有图片的页，文字少于这么多就当扫描件，把图导出来


class Name(str):
    pass


class Op(str):
    pass


class PdfString(bytes):
    pass


class Ref:
    __slots__ = ("num", "gen")

    def __init__(self, num: int, gen: int) -> None:
        self.num, self.gen = num, gen


class Stream:
    def __init__(self, attrs: dict, raw: bytes, num: Optional[int] = None) -> None:
        self.attrs, self.raw, self.num = attrs, raw, num

    def get(self, key, default=None):
        return self.attrs.get(key, default)


class PdfError(Exception):
    pass


# ---------------------------------------------------------------- 词法与对象

class Lexer:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data, self.pos = data, pos

    def skip_ws(self) -> None:
        data, n = self.data, len(self.data)
        while self.pos < n:
            c = data[self.pos]
            if c in WHITESPACE:
                self.pos += 1
            elif c == 0x25:  # %
                while self.pos < n and data[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def token(self):
        """下一个词：数、Name、PdfString、Op（关键字与 [ ] << >> { }）；到头返回 None。"""
        self.skip_ws()
        data = self.data
        if self.pos >= len(data):
            return None
        c = data[self.pos:self.pos + 1]
        if c == b"/":
            return self._name()
        if c == b"(":
            return self._literal()
        if c == b"<":
            if data[self.pos + 1:self.pos + 2] == b"<":
                self.pos += 2
                return Op("<<")
            return self._hex()
        if c == b">":
            self.pos += 2 if data[self.pos + 1:self.pos + 2] == b">" else 1
            return Op(">>")
        if c in (b"[", b"]", b"{", b"}"):
            self.pos += 1
            return Op(c.decode())
        m = NUMBER_RE.match(data, self.pos)
        if m and (m.end() >= len(data) or data[m.end()] in WHITESPACE + DELIMITERS):
            self.pos = m.end()
            text = m.group(0)
            return float(text) if b"." in text else int(text)
        start = self.pos
        while self.pos < len(data) and data[self.pos] not in WHITESPACE + DELIMITERS:
            self.pos += 1
        if self.pos == start:  # 落单的分隔符，跳过
            self.pos += 1
            return Op(data[start:self.pos].decode("latin-1"))
        return Op(data[start:self.pos].decode("latin-1"))

    def _name(self) -> Name:
        self.pos += 1
        start = self.pos
        data = self.data
        while self.pos < len(data) and data[self.pos] not in WHITESPACE + DELIMITERS:
            self.pos += 1
        raw = data[start:self.pos]
        raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
        return Name(raw.decode("utf-8", "replace"))

    def _literal(self) -> PdfString:
        data = self.data
        self.pos += 1
        depth, out = 1, bytearray()
        escapes = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x62: 0x08, 0x66: 0x0C}
        while self.pos < len(data):
            c = data[self.pos]
            self.pos += 1
            if c == 0x5C:  # 反斜杠
                if self.pos >= len(data):
                    break
                e = data[self.pos]
                self.pos += 1
                if e in escapes:
                    out.append(escapes[e])
                elif 0x30 <= e <= 0x37:
                    digits = bytes([e])
                    while len(digits) < 3 and self.pos < len(data) and 0x30 <= data[self.pos] <= 0x37:
                        digits += data[self.pos:self.pos + 1]
                        self.pos += 1
                    out.append(int(digits, 8) & 0xFF)
                elif e == 0x0D:
                    if data[self.pos:self.pos + 1] == b"\n":
                        self.pos += 1
                elif e == 0x0A:
                    pass
                else:
                    out.append(e)
            elif c == 0x28:
                depth += 1
                out.append(c)
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    break
                out.append(c)
            else:
                out.append(c)
        return PdfString(bytes(out))

    def _hex(self) -> PdfString:
        end = self.data.find(b">", self.pos)
        if end < 0:
            end = len(self.data)
        digits = re.sub(rb"[^0-9A-Fa-f]", b"", self.data[self.pos + 1:end])
        self.pos = end + 1
        if len(digits) % 2:
            digits += b"0"
        return PdfString(binascii.unhexlify(digits))


def parse_object(lex: Lexer, allow_refs: bool = True, tok=None):
    """读一个对象；遇到不成对象的关键字原样返回 Op。"""
    if tok is None:
        tok = lex.token()
    if isinstance(tok, Op):
        if tok == "[":
            items = []
            while True:
                t = lex.token()
                if t is None or t == "]":
                    return items
                items.append(parse_object(lex, allow_refs, t))
        if tok == "<<":
            d = {}
            while True:
                t = lex.token()
                if t is None or t == ">>":
                    return d
                if isinstance(t, Name):
                    d[t] = parse_object(lex, allow_refs)
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok == "null":
            return None
        return tok
    if allow_refs and isinstance(tok, int):
        save = lex.pos
        t2 = lex.token()
        if isinstance(t2, int):
            t3 = lex.token()
            if t3 == "R":
                return Ref(tok, t2)
        lex.pos = save
    return tok


# ---------------------------------------------------------------- 过滤器

def as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def flate(data: bytes) -> bytes:
    try:
        return zlib.decompressobj().decompress(data)
    except zlib.error:
        try:
            return zlib.decompressobj(-15).decompress(data[2:])
        except zlib.error as e:
            raise PdfError("Flate 解压失败：%s" % e)


def undo_predictor(data: bytes, parms: dict) -> bytes:
    predictor = parms.get("Predictor", 1) if isinstance(parms, dict) else 1
    if not isinstance(predictor, int) or predictor < 10:
        return data
    colors = parms.get("Colors", 1)
    bpc = parms.get("BitsPerComponent", 8)
    columns = parms.get("Columns", 1)
    bpp = max(1, colors * bpc // 8)
    stride = (columns * colors * bpc + 7) // 8
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    while i < len(data):
        ftype = data[i]
        row = bytearray(data[i + 1:i + 1 + stride])
        row.extend(b"\0" * (stride - len(row)))
        i += 1 + stride
        for x in range(stride):
            a = row[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ftype == 1:
                row[x] = (row[x] + a) & 0xFF
            elif ftype == 2:
                row[x] = (row[x] + b) & 0xFF
            elif ftype == 3:
                row[x] = (row[x] + (a + b) // 2) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[x] = (row[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        out.extend(row)
        prev = row
    return bytes(out)


def lzw(data: bytes, early: int = 1) -> bytes:
    out = bytearray()
    table: List[bytes] = []
    width, bits, nbits, prev = 9, 0, 0, None

    def reset():
        return [bytes([i]) for i in range(256)] + [b"", b""]

    table = reset()
    for byte in data:
        bits = (bits << 8) | byte
        nbits += 8
        while nbits >= width:
            nbits -= width
            code = (bits >> nbits) & ((1 << width) - 1)
            if code == 256:
                table, width, prev = reset(), 9, None
                continue
            if code == 257:
                return bytes(out)
            if code < len(table):
                entry = table[code]
                if prev is not None:
                    table.append(prev + entry[:1])
            elif prev is not None:
                entry = prev + prev[:1]
                table.append(entry)
            else:
                raise PdfError("LZW 数据坏了")
            out.extend(entry)
            prev = entry
            if len(table) + early >= (1 << width) and width < 12:
                width += 1
    return bytes(out)


def run_length(data: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        n = data[i]
        if n == 128:
            break
        if n < 128:
            out.extend(data[i + 1:i + 2 + n])
            i += n + 2
        else:
            out.extend(data[i + 1:i + 2] * (257 - n))
            i += 2
    return bytes(out)


def ascii85(data: bytes) -> bytes:
    body = re.sub(rb"\s", b"", data)
    if body.startswith(b"<~"):
        body = body[2:]
    end = body.find(b"~>")
    if end >= 0:
        body = body[:end]
    return base64.a85decode(body)


IMAGE_FILTERS = ("DCTDecode", "JPXDecode", "CCITTFaxDecode", "JBIG2Decode")
FILTER_ALIASES = {"Fl": "FlateDecode", "AHx": "ASCIIHexDecode", "A85": "ASCII85Decode",
                  "LZW": "LZWDecode", "RL": "RunLengthDecode", "DCT": "DCTDecode", "CCF": "CCITTFaxDecode"}


def decode_stream(pdf: "Pdf", stream: Stream) -> Tuple[bytes, Optional[str]]:
    """解到图像编码为止。返回 (数据, 剩下的图像编码名或 None)。"""
    filters = [FILTER_ALIASES.get(f, f) for f in as_list(pdf.resolve(stream.get("Filter")))]
    parms = [pdf.resolve(p) for p in as_list(pdf.resolve(stream.get("DecodeParms", stream.get("DP"))))]
    data = stream.raw
    for i, f in enumerate(filters):
        p = parms[i] if i < len(parms) and isinstance(parms[i], dict) else {}
        if f in IMAGE_FILTERS:
            return data, f
        if f == "FlateDecode":
            data = undo_predictor(flate(data), p)
        elif f == "LZWDecode":
            data = undo_predictor(lzw(data, p.get("EarlyChange", 1)), p)
        elif f == "ASCIIHexDecode":
            digits = re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0])
            data = binascii.unhexlify(digits + b"0" * (len(digits) % 2))
        elif f == "ASCII85Decode":
            data = ascii85(data)
        elif f == "RunLengthDecode":
            data = run_length(data)
        else:
            raise PdfError("不认得的过滤器 %s" % f)
    return data, None


# ---------------------------------------------------------------- 文件

class Pdf:
    def __init__(self, data: bytes) -> None:
        if not data.startswith(b"%PDF") and b"%PDF" not in data[:1024]:
            raise PdfError("不是 PDF")
        self.data = data
        self.offsets: Dict[int, int] = {}
        for m in OBJ_RE.finditer(data):
            self.offsets[int(m.group(1))] = m.end()  # 增量更新：后出现的覆盖先出现的
        self.cache: Dict[int, object] = {}
        self.in_objstm: Dict[int, Tuple[int, int]] = {}
        self.trailer: dict = {}
        for num in list(self.offsets):
            obj = self.get(num)
            if isinstance(obj, Stream):
                kind = obj.get("Type")
                if kind == "ObjStm":
                    self._index_objstm(num, obj)
                elif kind == "XRef":
                    self.trailer.update(obj.attrs)
        for m in re.finditer(rb"trailer\s*<<", data):
            lex = Lexer(data, m.end() - 2)
            t = parse_object(lex)
            if isinstance(t, dict):
                self.trailer.update(t)

    def _index_objstm(self, num: int, stream: Stream) -> None:
        try:
            body, _ = decode_stream(self, stream)
        except PdfError:
            return
        n, first = stream.get("N", 0), stream.get("First", 0)
        lex = Lexer(body)
        pairs = [lex.token() for _ in range(2 * n)]
        for i in range(0, len(pairs) - 1, 2):
            onum, off = pairs[i], pairs[i + 1]
            if isinstance(onum, int) and isinstance(off, int) and onum not in self.offsets:
                self.in_objstm[onum] = (num, first + off)
        self.cache[("body", num)] = body

    def get(self, num: int):
        if num in self.cache:
            return self.cache[num]
        self.cache[num] = None  # 防自引用死循环
        obj = None
        try:
            if num in self.offsets:
                obj = self._parse_at(num, self.offsets[num])
            elif num in self.in_objstm:
                snum, off = self.in_objstm[num]
                body = self.cache.get(("body", snum))
                if body is not None:
                    obj = parse_object(Lexer(body, off))
        except (PdfError, ValueError, IndexError, RecursionError):
            obj = None
        self.cache[num] = obj
        return obj

    def _parse_at(self, num: int, pos: int):
        lex = Lexer(self.data, pos)
        obj = parse_object(lex)
        if not isinstance(obj, dict):
            return obj
        lex.skip_ws()
        if not self.data.startswith(b"stream", lex.pos):
            return obj
        start = lex.pos + 6
        if self.data[start:start + 2] == b"\r\n":
            start += 2
        elif self.data[start:start + 1] in (b"\n", b"\r"):
            start += 1
        length = obj.get("Length")
        if isinstance(length, Ref):
            length = self.get(length.num) if length.num != num else None
        raw = None
        if isinstance(length, int) and length >= 0:
            tail = self.data[start + length:start + length + 20].lstrip(WHITESPACE)
            if tail.startswith(b"endstream"):
                raw = self.data[start:start + length]
        if raw is None:
            end = self.data.find(b"endstream", start)
            if end < 0:
                raise PdfError("流没有结尾")
            raw = self.data[start:end]
            if raw.endswith(b"\r\n"):
                raw = raw[:-2]
            elif raw.endswith((b"\n", b"\r")):
                raw = raw[:-1]
        return Stream(obj, raw, num)

    def resolve(self, x, depth: int = 0):
        while isinstance(x, Ref) and depth < 32:
            x = self.get(x.num)
            depth += 1
        return x

    def dict_of(self, x) -> dict:
        x = self.resolve(x)
        if isinstance(x, Stream):
            return x.attrs
        return x if isinstance(x, dict) else {}

    @property
    def encrypted(self) -> bool:
        return "Encrypt" in self.trailer

    def root(self) -> dict:
        root = self.dict_of(self.trailer.get("Root"))
        if root.get("Type") == "Catalog" or "Pages" in root:
            return root
        found = {}
        for num in sorted(self.offsets):
            d = self.get(num)
            if isinstance(d, dict) and d.get("Type") == "Catalog":
                found = d
        return found

    def pages(self) -> List[Tuple[dict, dict]]:
        """[(页字典, 继承后的资源字典)]"""
        out: List[Tuple[dict, dict]] = []
        seen = set()

        def walk(node_ref, resources):
            key = node_ref.num if isinstance(node_ref, Ref) else id(node_ref)
            if key in seen:
                return
            seen.add(key)
            node = self.dict_of(node_ref)
            if not node:
                return
            res = self.dict_of(node["Resources"]) if "Resources" in node else resources
            if node.get("Type") == "Pages" or "Kids" in node:
                for kid in as_list(self.resolve(node.get("Kids"))):
                    walk(kid, res)
            else:
                out.append((node, res))

        walk(self.root().get("Pages"), {})
        return out


# ---------------------------------------------------------------- 字体

GLYPH_NAMES = {
    "space": " ", "period": ".", "comma": ",", "colon": ":", "semicolon": ";", "hyphen": "-",
    "parenleft": "(", "parenright": ")", "slash": "/", "percent": "%", "underscore": "_",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9",
}


def glyph_to_char(name: str) -> str:
    if name in GLYPH_NAMES:
        return GLYPH_NAMES[name]
    if len(name) == 1:
        return name
    m = re.fullmatch(r"uni([0-9A-Fa-f]{4})", name)
    if m:
        return chr(int(m.group(1), 16))
    return "�"


def utf16(b: bytes) -> str:
    if len(b) % 2:
        b += b"\0"
    return b.decode("utf-16-be", "replace")


def parse_cmap(data: bytes) -> Tuple[Dict[bytes, str], List[int]]:
    """ToUnicode CMap -> ({编码字节: 文字}, 编码长度表)。"""
    lex = Lexer(data)
    mapping: Dict[bytes, str] = {}
    lengths = set()
    while True:
        tok = lex.token()
        if tok is None:
            break
        if tok == "begincodespacerange":
            while True:
                lo = lex.token()
                if lo is None or lo == "endcodespacerange":
                    break
                lex.token()
                if isinstance(lo, bytes):
                    lengths.add(len(lo))
        elif tok == "beginbfchar":
            while True:
                src = parse_object(lex, False)
                if src is None or src == "endbfchar":
                    break
                dst = parse_object(lex, False)
                if isinstance(src, bytes):
                    mapping[bytes(src)] = utf16(dst) if isinstance(dst, bytes) else glyph_to_char(str(dst))
        elif tok == "beginbfrange":
            while True:
                lo = parse_object(lex, False)
                if lo is None or lo == "endbfrange":
                    break
                hi = parse_object(lex, False)
                dst = parse_object(lex, False)
                if not (isinstance(lo, bytes) and isinstance(hi, bytes)) or not lo:
                    continue
                n = len(lo)
                a, b = int.from_bytes(lo, "big"), int.from_bytes(hi, "big")
                if b < a or b - a > 0xFFFF:
                    continue
                for i, code in enumerate(range(a, b + 1)):
                    key = code.to_bytes(n, "big")
                    if isinstance(dst, list):
                        if i < len(dst) and isinstance(dst[i], bytes):
                            mapping[key] = utf16(dst[i])
                    elif isinstance(dst, bytes) and dst:
                        last = int.from_bytes(dst[-2:], "big") + i if len(dst) >= 2 else dst[-1] + i
                        head = dst[:-2] if len(dst) >= 2 else b""
                        mapping[key] = utf16(head + (last & 0xFFFF).to_bytes(2, "big"))
    if not lengths:
        lengths = {len(k) for k in mapping} or {2}
    return mapping, sorted(lengths)


class Font:
    def __init__(self, pdf: Pdf, fdict: dict) -> None:
        self.composite = fdict.get("Subtype") == "Type0"
        self.cmap: Dict[bytes, str] = {}
        self.lengths = [2] if self.composite else [1]
        self.mode = "latin-1"
        self.differences: Dict[int, str] = {}
        tu = pdf.resolve(fdict.get("ToUnicode"))
        if isinstance(tu, Stream):
            try:
                body, _ = decode_stream(pdf, tu)
                self.cmap, self.lengths = parse_cmap(body)
            except PdfError:
                self.cmap = {}
        enc = pdf.resolve(fdict.get("Encoding"))
        if self.composite:
            name = str(enc) if isinstance(enc, str) else str(pdf.dict_of(enc).get("CMapName", ""))
            if "UCS2" in name or "UTF16" in name:
                self.mode = "utf-16-be"
            elif "UTF8" in name:
                self.mode = "utf-8"
            elif re.search(r"GB|EUC", name) and "Identity" not in name:
                self.mode = "gb18030"
            elif re.search(r"B5|ETen|HKscs|CNS-EUC", name):
                self.mode = "big5"
            elif "RKSJ" in name:
                self.mode = "shift_jis"
            else:
                self.mode = "identity"  # 没有 ToUnicode 的 Identity-H：只有字形号，读不出字
        else:
            base = enc if isinstance(enc, str) else pdf.dict_of(enc).get("BaseEncoding")
            self.mode = {"WinAnsiEncoding": "cp1252", "MacRomanEncoding": "mac_roman"}.get(str(base), "simple")
            if isinstance(enc, dict) or isinstance(pdf.resolve(enc), dict):
                diffs = as_list(pdf.dict_of(enc).get("Differences"))
                code = 0
                for item in diffs:
                    if isinstance(item, int):
                        code = item
                    elif isinstance(item, str):
                        self.differences[code] = glyph_to_char(item)
                        code += 1

    def decode(self, s: bytes) -> str:
        if self.cmap:
            out, i = [], 0
            while i < len(s):
                for n in self.lengths:
                    piece = s[i:i + n]
                    if piece in self.cmap:
                        out.append(self.cmap[piece])
                        i += n
                        break
                else:
                    n = self.lengths[0] if self.composite else 1
                    out.append(self._fallback(s[i:i + n]))
                    i += n
            return "".join(out)
        return self._fallback(s)

    def _fallback(self, s: bytes) -> str:
        if self.mode == "identity":
            return "�" * max(1, len(s) // 2)
        if self.mode in ("utf-16-be", "utf-8", "gb18030", "big5", "shift_jis", "cp1252", "mac_roman"):
            return s.decode(self.mode, "replace")
        if self.differences:
            return "".join(self.differences.get(b, chr(b)) for b in s)
        if any(b >= 0x80 for b in s):  # 不带编码的中文简单字体常是 GBK 字节
            try:
                return s.decode("gb18030")
            except UnicodeDecodeError:
                pass
        return s.decode("latin-1")


# ---------------------------------------------------------------- 内容流

def mat_mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2, c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class PageReader:
    def __init__(self, pdf: Pdf) -> None:
        self.pdf = pdf
        self.fonts: Dict[int, Font] = {}
        self.parts: List[str] = []
        self.images: List[Stream] = []
        self.inline_images = 0
        self.last_y: Optional[float] = None
        self.depth = 0

    def font(self, resources: dict, name: str) -> Optional[Font]:
        ref = self.pdf.dict_of(resources.get("Font")).get(name)
        fdict = self.pdf.dict_of(ref)
        if not fdict:
            return None
        key = ref.num if isinstance(ref, Ref) else id(fdict)
        if key not in self.fonts:
            self.fonts[key] = Font(self.pdf, fdict)
        return self.fonts[key]

    def emit(self, text: str, y: float) -> None:
        if not text:
            return
        if self.last_y is not None and abs(y - self.last_y) > 1.0:
            self.parts.append("\n")
        self.last_y = y
        self.parts.append(text)

    def run(self, content: bytes, resources: dict, ctm=IDENTITY) -> None:
        if self.depth > 8:
            return
        self.depth += 1
        lex = Lexer(content)
        operands: list = []
        stack = []
        font: Optional[Font] = None
        tm = tlm = IDENTITY
        leading = 0.0

        def y_now():
            return mat_mul(tm, ctm)[5]

        def next_line(tx, ty):
            nonlocal tm, tlm
            tlm = mat_mul((1.0, 0.0, 0.0, 1.0, float(tx), float(ty)), tlm)
            tm = tlm

        def show(s):
            if isinstance(s, bytes) and font is not None:
                self.emit(font.decode(s), y_now())

        while True:
            tok = lex.token()
            if tok is None:
                break
            if not isinstance(tok, Op) or tok in ("[", "<<"):
                operands.append(parse_object(lex, False, tok))
                continue
            op = str(tok)
            args, operands = operands, []
            try:
                if op == "BT":
                    tm = tlm = IDENTITY
                elif op == "q":
                    stack.append((ctm, font))
                elif op == "Q":
                    if stack:
                        ctm, font = stack.pop()
                elif op == "cm" and len(args) == 6:
                    ctm = mat_mul(tuple(float(a) for a in args), ctm)
                elif op == "Tf" and len(args) == 2:
                    font = self.font(resources, args[0])
                elif op == "TL" and args:
                    leading = float(args[0])
                elif op == "Td" and len(args) == 2:
                    next_line(*args)
                elif op == "TD" and len(args) == 2:
                    leading = -float(args[1])
                    next_line(*args)
                elif op == "Tm" and len(args) == 6:
                    tm = tlm = tuple(float(a) for a in args)
                elif op == "T*":
                    next_line(0, -leading)
                elif op == "Tj" and args:
                    show(args[-1])
                elif op in ("'", '"') and args:
                    next_line(0, -leading)
                    show(args[-1])
                elif op == "TJ" and args and isinstance(args[-1], list):
                    for item in args[-1]:
                        if isinstance(item, bytes):
                            show(item)
                        elif isinstance(item, (int, float)) and item < -250 and font is not None:
                            self.emit(" ", y_now())
                elif op == "Do" and args:
                    self.do(resources, args[0], ctm)
                elif op == "BI":
                    self.skip_inline_image(lex)
            except (TypeError, ValueError):
                continue
        self.depth -= 1

    def skip_inline_image(self, lex: Lexer) -> None:
        self.inline_images += 1
        m = re.compile(rb"\bID[\s]").search(lex.data, lex.pos)
        if not m:
            lex.pos = len(lex.data)
            return
        end = re.compile(rb"\sEI(?=[\s]|$)").search(lex.data, m.end())
        lex.pos = end.end() if end else len(lex.data)

    def do(self, resources: dict, name: str, ctm) -> None:
        xobj = self.pdf.resolve(self.pdf.dict_of(resources.get("XObject")).get(name))
        if not isinstance(xobj, Stream):
            return
        kind = xobj.get("Subtype")
        if kind == "Image":
            if all(x is not xobj for x in self.images):
                self.images.append(xobj)
        elif kind == "Form":
            try:
                body, _ = decode_stream(self.pdf, xobj)
            except PdfError:
                return
            matrix = xobj.get("Matrix")
            inner = mat_mul(tuple(float(a) for a in matrix), ctm) if isinstance(matrix, list) and len(matrix) == 6 else ctm
            res = self.pdf.dict_of(xobj.get("Resources")) or resources
            self.run(body, res, inner)


def page_content(pdf: Pdf, page: dict) -> bytes:
    parts = []
    for ref in as_list(pdf.resolve(page.get("Contents"))):
        s = pdf.resolve(ref)
        if isinstance(s, Stream):
            body, _ = decode_stream(pdf, s)
            parts.append(body)
    return b"\n".join(parts)


# ---------------------------------------------------------------- 图片导出

def png_bytes(width: int, height: int, bit_depth: int, color_type: int, data: bytes, palette: bytes = b"") -> bytes:
    channels = {0: 1, 2: 3, 3: 1}[color_type]
    stride = (width * channels * bit_depth + 7) // 8
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        row = data[y * stride:(y + 1) * stride]
        rows.extend(row + b"\0" * (stride - len(row)))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0))
    if palette:
        out += chunk(b"PLTE", palette)
    return out + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")


def color_space(pdf: Pdf, cs) -> Tuple[str, object]:
    """-> (Gray/RGB/CMYK/Indexed/未知, 附加信息)"""
    cs = pdf.resolve(cs)
    if isinstance(cs, list) and cs:
        kind = pdf.resolve(cs[0])
        if kind == "ICCBased" and len(cs) > 1:
            n = pdf.dict_of(cs[1]).get("N", 3)
            return {1: "Gray", 3: "RGB", 4: "CMYK"}.get(n, "未知"), None
        if kind == "Indexed" and len(cs) >= 4:
            base, _ = color_space(pdf, cs[1])
            lookup = pdf.resolve(cs[3])
            if isinstance(lookup, Stream):
                lookup, _ = decode_stream(pdf, lookup)
            return "Indexed", (base, bytes(lookup or b""), int(pdf.resolve(cs[2]) or 0))
        if kind in ("CalGray", "CalRGB"):
            return ("Gray" if kind == "CalGray" else "RGB"), None
        return "未知", None
    return {"DeviceGray": "Gray", "G": "Gray", "DeviceRGB": "RGB", "RGB": "RGB",
            "DeviceCMYK": "CMYK", "CMYK": "CMYK"}.get(str(cs), "未知"), None


def export_image(pdf: Pdf, img: Stream, path_stem: str) -> Tuple[Optional[str], str]:
    """导出一张图。返回 (文件路径或 None, 导不出时的原因)。"""
    try:
        data, coded = decode_stream(pdf, img)
    except PdfError as e:
        return None, str(e)
    if coded == "DCTDecode":
        path = path_stem + ".jpg"
    elif coded == "JPXDecode":
        path = path_stem + ".jp2"
    elif coded in ("CCITTFaxDecode", "JBIG2Decode"):
        return None, "图片是%s编码的黑白扫描，脚本转不出" % ("传真" if coded == "CCITTFaxDecode" else " JBIG2 ")
    else:
        w, h = pdf.resolve(img.get("Width")), pdf.resolve(img.get("Height"))
        bpc = pdf.resolve(img.get("BitsPerComponent", 1 if img.get("ImageMask") else 8))
        if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
            return None, "图片尺寸缺失"
        decode = pdf.resolve(img.get("Decode"))
        invert = isinstance(decode, list) and len(decode) >= 2 and decode[0] == 1 and decode[1] == 0
        if img.get("ImageMask"):
            kind, extra, bpc = "Gray", None, 1
        else:
            kind, extra = color_space(pdf, img.get("ColorSpace"))
        if kind == "Gray" and bpc in (1, 2, 4, 8):
            if invert:
                data = bytes(255 - b for b in data)
            png = png_bytes(w, h, bpc, 0, data)
        elif kind == "RGB" and bpc == 8:
            png = png_bytes(w, h, 8, 2, data)
        elif kind == "CMYK" and bpc == 8:
            rgb = bytearray()
            for i in range(0, len(data) - 3, 4):
                c, m, y, k = data[i:i + 4]
                rgb.extend((255 - min(255, c + k), 255 - min(255, m + k), 255 - min(255, y + k)))
            png = png_bytes(w, h, 8, 2, bytes(rgb))
        elif kind == "Indexed" and bpc in (1, 2, 4, 8):
            base, lookup, hival = extra
            if base == "Gray":
                lookup = bytes(v for b in lookup[:hival + 1] for v in (b, b, b))
            elif base != "RGB":
                return None, "调色板底色不是灰度或 RGB"
            png = png_bytes(w, h, bpc, 3, data, lookup[:3 * (hival + 1)])
        else:
            return None, "图片色彩（%s，%s 位）脚本转不出" % (kind, bpc)
        data, path = png, path_stem + ".png"
    with open(path, "wb") as f:
        f.write(data)
    return path, ""


# ---------------------------------------------------------------- 判断与回显

def assess(text: str) -> str:
    """-> 正常 / 无文字层 / 疑似乱码"""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return "无文字层"
    bad = sum(1 for c in chars if c == "�" or 0xE000 <= ord(c) <= 0xF8FF or ord(c) < 0x20 or 0x7F <= ord(c) < 0xA0)
    latin = sum(1 for c in chars if 0xA0 <= ord(c) <= 0x2FF)
    if bad / len(chars) > 0.2 or latin / len(chars) > 0.3:
        return "疑似乱码"
    return "正常"


def tidy(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


class ImageDir:
    def __init__(self, given: Optional[str]) -> None:
        self.given, self.path = given, None

    def get(self) -> str:
        if self.path is None:
            if self.given:
                os.makedirs(self.given, exist_ok=True)
                self.path = self.given
            else:
                self.path = tempfile.mkdtemp(prefix="xubao-")
        return self.path


def report_file(path: str, images: ImageDir) -> List[str]:
    name = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            pdf = Pdf(f.read())
    except (OSError, PdfError) as e:
        return ["# %s" % name, "", "读不了：%s。请用别的办法看原件。" % e, ""]
    if pdf.encrypted:
        return ["# %s" % name, "", "加密 PDF，脚本抽不出。请用别的办法看原件（能直接读 PDF 就直接读；不行就请律师截图）。", ""]
    pages = pdf.pages()
    if not pages:
        return ["# %s" % name, "", "找不到页面，文件可能坏了。请用别的办法看原件。", ""]
    out = ["# %s（共 %d 页）" % (name, len(pages)), ""]
    stem = os.path.splitext(name)[0]
    for no, (page, res) in enumerate(pages, start=1):
        reader = PageReader(pdf)
        try:
            reader.run(page_content(pdf, page), res)
        except (PdfError, RecursionError) as e:
            out += ["## 第 %d 页：内容读不了（%s），请用别的办法看这一页" % (no, e), ""]
            continue
        text = tidy("".join(reader.parts))
        verdict = assess(text)
        meaningful = len([c for c in text if not c.isspace()])
        if verdict == "正常" and (meaningful >= MIN_TEXT_CHARS or not reader.images):
            out += ["## 第 %d 页" % no, "", text, ""]
            continue
        label = verdict if verdict != "正常" else "文字很少"
        files, reasons = [], []
        for i, img in enumerate(reader.images, start=1):
            got, why = export_image(pdf, img, os.path.join(images.get(), "%s-第%d页-%d" % (stem, no, i)))
            if got:
                files.append(got)
            elif why not in reasons:
                reasons.append(why)
        head = "## 第 %d 页：%s" % (no, label)
        if files:
            out += [head + "，导出了 %d 张图，请看图读这一页：" % len(files), ""]
            out += ["- %s" % p for p in files]
        else:
            out += [head + "，没有导出图片，请用别的办法看原件这一页。", ""]
        for why in reasons:
            out.append("- 有图没导出：%s" % why)
        if reader.inline_images:
            out.append("- 另有 %d 张内嵌小图没导出" % reader.inline_images)
        if text and verdict == "正常":
            out += ["", "这一页抽到的少量文字：", "", text]
        out.append("")
    return out


def main(argv: List[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="pdf_text.py", description="逐页抽 PDF 文字层；抽不出的页导出图片")
    parser.add_argument("pdfs", nargs="+", help="PDF 文件")
    parser.add_argument("--images", help="图片导出目录（默认在系统临时目录下新建一个）")
    args = parser.parse_args(argv)
    images = ImageDir(args.images)
    lines: List[str] = []
    for path in args.pdfs:
        lines += report_file(path, images)
    sys.stdout.write("\n".join(lines).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
