"""TrueType/OpenType font programs and PDF font maps, in pure Python (M7).

Used by page-text editing to decide whether a font embedded in a PDF can write new text
(:mod:`pdfeditor.core.fontread` builds an :class:`EmbeddedFont` from a font object) and to
describe installed fonts (:mod:`pdfeditor.core.fontmatch`). Nothing here imports MuPDF,
fontTools or Qt.

Font functions take the font file's bytes (anything sliceable: ``bytes`` or an ``mmap``)
and the face ``index`` inside a TrueType collection. Malformed data raises
:class:`FontFileError` (a ``ValueError``); a missing optional table gives an empty
result. Facts relied on (docs/M7_PLAN.md F2): MuPDF's subsets of Type0 fonts drop the
``cmap`` table but keep ``name`` and the full glyph count (sparse ``loca``), so a glyph is
"present" when its ``loca`` entry is not empty.

The PDF side (``parse_tounicode``, ``parse_w_array``, ``parse_differences``,
``glyph_name_to_unicode``) is tolerant: malformed entries are skipped, never raised.
"""

from __future__ import annotations

import enum
import logging
import re
import struct
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

log = logging.getLogger(__name__)

#: OS/2 fsType: "Restricted License embedding" (bits 1–3 hold the embedding level; the
#: least restrictive bit set wins, so a font is restricted only when bit 1 is the only one).
FS_TYPE_RESTRICTED = 0x0002
FS_TYPE_PREVIEW_PRINT = 0x0004
FS_TYPE_EDITABLE = 0x0008
#: Largest range expanded from one ToUnicode ``bfrange`` (a broken CMap cannot blow up).
MAX_BFRANGE = 0x10000
_MAX_UNICODE = 0x10FFFF

TableMap = dict[str, tuple[int, int]]


class FontFileError(ValueError):
    """The font program is malformed (or not a TrueType/OpenType font)."""


# -- table directory ----------------------------------------------------------------------
def _unpack(fmt: str, data: object, offset: int) -> tuple[int, ...]:
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):  # type: ignore[arg-type]
        raise FontFileError(f"read past the end of the font at {offset}")
    return struct.unpack_from(fmt, data, offset)  # type: ignore[arg-type]


def face_count(data: bytes) -> int:
    """Number of faces: the collection's count for a ``ttcf`` file, else 1."""
    if bytes(data[:4]) == b"ttcf":
        return _unpack(">I", data, 8)[0]
    return 1


def tables(data: bytes, index: int = 0) -> TableMap:
    """``tag -> (offset, length)`` of face ``index`` (tables lying outside the file are
    dropped)."""
    off = 0
    if bytes(data[:4]) == b"ttcf":
        count = face_count(data)
        if not 0 <= index < count:
            raise FontFileError(f"face {index} not in a collection of {count}")
        off = _unpack(">I", data, 12 + 4 * index)[0]
    elif index != 0:
        raise FontFileError(f"face {index} of a single-face font")
    version = bytes(data[off : off + 4])
    if version not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
        raise FontFileError(f"not a TrueType/OpenType font ({version!r})")
    num = _unpack(">H", data, off + 4)[0]
    out: TableMap = {}
    size = len(data)
    for i in range(num):
        rec = off + 12 + 16 * i
        tag = bytes(data[rec : rec + 4]).decode("latin-1")
        toff, tlen = _unpack(">II", data, rec + 8)
        if toff + tlen <= size:
            out[tag] = (toff, tlen)
    return out


def _table(data: bytes, tbl: TableMap, tag: str) -> tuple[int, int]:
    try:
        return tbl[tag]
    except KeyError:
        raise FontFileError(f"no {tag!r} table") from None


# -- OS/2, head, maxp, hmtx ---------------------------------------------------------------
def fs_type(data: bytes, index: int = 0) -> int | None:
    """OS/2 ``fsType`` (embedding licence bits), ``None`` without an OS/2 table."""
    tbl = tables(data, index)
    if "OS/2" not in tbl:
        return None
    return _unpack(">H", data, tbl["OS/2"][0] + 8)[0]


def is_restricted(fs: int | None) -> bool:
    """True for "Restricted License embedding": the font may not be embedded to edit."""
    return fs is not None and (fs & 0x000E) == FS_TYPE_RESTRICTED


class FaceStyle(NamedTuple):
    """Weight class (100–900), bold and italic of a face."""

    weight: int
    bold: bool
    italic: bool


def face_style(data: bytes, index: int = 0) -> FaceStyle:
    """Style from OS/2 (``usWeightClass``, ``fsSelection``), else head ``macStyle``."""
    tbl = tables(data, index)
    if "OS/2" in tbl:
        base = tbl["OS/2"][0]
        weight = _unpack(">H", data, base + 4)[0]
        selection = _unpack(">H", data, base + 62)[0]
        italic = bool(selection & 0x0001) or bool(selection & 0x0200)
        bold = bool(selection & 0x0020) or weight >= 600
        return FaceStyle(weight or 400, bold, italic)
    if "head" in tbl:
        mac = _unpack(">H", data, tbl["head"][0] + 44)[0]
        bold = bool(mac & 1)
        return FaceStyle(700 if bold else 400, bold, bool(mac & 2))
    return FaceStyle(400, False, False)


def panose_serif(data: bytes, index: int = 0) -> bool | None:
    """True for a serif face, False for sans, ``None`` when OS/2 does not say (PANOSE
    serif style, then the IBM family class)."""
    tbl = tables(data, index)
    if "OS/2" not in tbl:
        return None
    base, length = tbl["OS/2"]
    if length >= 44:
        family_kind, serif_style = _unpack(">BB", data, base + 32)
        if family_kind == 2 and serif_style >= 2:  # Latin text
            # 2–10 serifs; 11–13 sans, 14 flared and 15 rounded (Calibri) are sans
            return serif_style <= 10
    family_class = _unpack(">h", data, base + 30)[0] >> 8
    if 1 <= family_class <= 7:
        return True
    if family_class == 8:
        return False
    return None


def glyph_count(data: bytes, index: int = 0) -> int:
    """``maxp.numGlyphs``."""
    tbl = tables(data, index)
    return _unpack(">H", data, _table(data, tbl, "maxp")[0] + 4)[0]


def units_per_em(data: bytes, index: int = 0) -> int:
    """``head.unitsPerEm`` (1000 when absent or zero)."""
    tbl = tables(data, index)
    if "head" not in tbl:
        return 1000
    return _unpack(">H", data, tbl["head"][0] + 18)[0] or 1000


def advance_widths(data: bytes, index: int = 0) -> tuple[int, ...]:
    """Advance width of every glyph (font units; ``hmtx`` repeats its last entry)."""
    tbl = tables(data, index)
    count = glyph_count(data, index)
    metrics = _unpack(">H", data, _table(data, tbl, "hhea")[0] + 34)[0]
    hmtx = _table(data, tbl, "hmtx")[0]
    metrics = max(1, min(metrics, count)) if count else 0
    widths = [_unpack(">H", data, hmtx + 4 * g)[0] for g in range(metrics)]
    last = widths[-1] if widths else 0
    widths.extend([last] * (count - len(widths)))
    return tuple(widths)


def nonempty_glyphs(data: bytes, index: int = 0) -> frozenset[int] | None:
    """Glyph ids with an outline (non-empty ``loca`` entry); ``None`` for a font without
    ``glyf``/``loca`` (CFF outlines: presence unknown)."""
    tbl = tables(data, index)
    if "loca" not in tbl or "glyf" not in tbl:
        return None
    long_offsets = _unpack(">h", data, _table(data, tbl, "head")[0] + 50)[0]
    count = glyph_count(data, index)
    loca, length = tbl["loca"]
    step, fmt, scale = (4, ">I", 1) if long_offsets else (2, ">H", 2)
    count = min(count, length // step - 1)
    present: set[int] = set()
    prev = _unpack(fmt, data, loca)[0] * scale
    for g in range(count):
        nxt = _unpack(fmt, data, loca + step * (g + 1))[0] * scale
        if nxt > prev:
            present.add(g)
        prev = nxt
    return frozenset(present)


# -- name ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class FontNames:
    """Strings of the ``name`` table ("" when absent). ``family``/``style`` prefer the
    typographic names (IDs 16/17, e.g. "Segoe UI" + "Semibold") over IDs 1/2."""

    family: str = ""
    style: str = ""
    full_name: str = ""
    postscript_name: str = ""
    legacy_family: str = ""


_ENGLISH = (0x409, 0)


def names(data: bytes, index: int = 0) -> FontNames:
    """Family/style/full/PostScript names (Windows English first)."""
    tbl = tables(data, index)
    if "name" not in tbl:
        return FontNames()
    base, length = tbl["name"]
    count, strings = _unpack(">HH", data, base + 2)
    found: dict[int, tuple[int, str]] = {}  # name id -> (rank, text)
    for i in range(count):
        rec = base + 6 + 12 * i
        if rec + 12 > base + length:
            break
        pid, eid, lid, nid, ln, off = _unpack(">HHHHHH", data, rec)
        if nid not in (1, 2, 4, 6, 16, 17):
            continue
        start = base + strings + off
        raw = bytes(data[start : start + ln])
        if pid == 3 and eid in (0, 1, 10):
            text = raw.decode("utf-16-be", "replace")
            rank = 0 if lid == 0x409 else 1
        elif pid == 0:
            text = raw.decode("utf-16-be", "replace")
            rank = 2
        elif pid == 1 and eid == 0:
            text = raw.decode("mac_roman", "replace")
            rank = 3 if lid == 0 else 4
        else:
            continue
        text = text.strip("\x00 ")
        if text and (nid not in found or rank < found[nid][0]):
            found[nid] = (rank, text)
    get = {nid: text for nid, (_, text) in found.items()}
    return FontNames(
        family=get.get(16) or get.get(1, ""),
        style=get.get(17) or get.get(2, ""),
        full_name=get.get(4, ""),
        postscript_name=get.get(6, ""),
        legacy_family=get.get(1, ""),
    )


# -- cmap ---------------------------------------------------------------------------------
CmapKind = Literal["unicode", "symbol", "mac"]


def cmap(data: bytes, index: int = 0, *, kind: CmapKind = "unicode") -> dict[int, int]:
    """``code -> glyph id`` from the font's ``cmap`` ({} when it has none).

    ``unicode``: (3,10)/(0,4)/(0,6) format 12, else (3,1)/(0,x) format 4. ``symbol``:
    (3,0) (codes as stored, usually U+F000 + byte). ``mac``: (1,0) formats 0/4/6.
    """
    tbl = tables(data, index)
    if "cmap" not in tbl:
        return {}
    base, length = tbl["cmap"]
    count = _unpack(">H", data, base + 2)[0]
    best: tuple[int, int, int] | None = None  # (rank, format, offset)
    for i in range(count):
        pid, eid, off = _unpack(">HHI", data, base + 4 + 8 * i)
        if off >= length:
            continue
        fmt = _unpack(">H", data, base + off)[0]
        rank = _cmap_rank(kind, pid, eid, fmt)
        if rank is not None and (best is None or rank < best[0]):
            best = (rank, fmt, base + off)
    if best is None:
        return {}
    _, fmt, p = best
    if fmt == 4:
        return _cmap_format4(data, p)
    if fmt == 12:
        return _cmap_format12(data, p)
    if fmt == 6:
        first, n = _unpack(">HH", data, p + 6)
        ids = _unpack(f">{n}H", data, p + 10)
        return {first + i: g for i, g in enumerate(ids) if g}
    if fmt == 0:
        ids = _unpack(">256B", data, p + 6)
        return {c: g for c, g in enumerate(ids) if g}
    return {}


def _cmap_rank(kind: CmapKind, pid: int, eid: int, fmt: int) -> int | None:
    if kind == "unicode":
        if fmt == 12 and (pid, eid) in ((3, 10), (0, 4), (0, 6)):
            return 0
        if fmt == 4 and (pid == 0 or (pid, eid) == (3, 1)):
            return 1
        return None
    if kind == "symbol":
        return 0 if (pid, eid) == (3, 0) and fmt in (4, 12) else None
    return 0 if (pid, eid) == (1, 0) and fmt in (0, 4, 6) else None


def _cmap_format4(data: bytes, p: int) -> dict[int, int]:
    segx2 = _unpack(">H", data, p + 6)[0]
    seg = segx2 // 2
    ends = _unpack(f">{seg}H", data, p + 14)
    starts = _unpack(f">{seg}H", data, p + 16 + segx2)
    deltas = _unpack(f">{seg}h", data, p + 16 + 2 * segx2)
    ro_pos = p + 16 + 3 * segx2
    offsets = _unpack(f">{seg}H", data, ro_pos)
    out: dict[int, int] = {}
    for s in range(seg):
        start, end = starts[s], min(ends[s], 0xFFFE)
        for c in range(start, end + 1):
            if offsets[s] == 0:
                g = (c + deltas[s]) & 0xFFFF
            else:
                gp = ro_pos + 2 * s + offsets[s] + 2 * (c - start)
                if gp + 2 > len(data):
                    break
                g = _unpack(">H", data, gp)[0]
                if g:
                    g = (g + deltas[s]) & 0xFFFF
            if g:
                out[c] = g
    return out


def _cmap_format12(data: bytes, p: int) -> dict[int, int]:
    groups = _unpack(">I", data, p + 12)[0]
    out: dict[int, int] = {}
    for i in range(groups):
        first, last, gid = _unpack(">III", data, p + 16 + 12 * i)
        last = min(last, _MAX_UNICODE, first + MAX_BFRANGE)
        for c in range(first, last + 1):
            out[c] = gid + (c - first)
    return out


# -- ToUnicode CMaps ----------------------------------------------------------------------
_HEX = r"<([0-9A-Fa-f\s]*)>"
_BFCHAR_RE = re.compile(r"beginbfchar(.*?)endbfchar", re.S)
_BFRANGE_RE = re.compile(r"beginbfrange(.*?)endbfrange", re.S)
_PAIR_RE = re.compile(_HEX + r"\s*" + _HEX)
_RANGE_RE = re.compile(_HEX + r"\s*" + _HEX + r"\s*(?:" + _HEX + r"|\[([^\]]*)\])")
_HEX_ONLY_RE = re.compile(_HEX)
_COMMENT_RE = re.compile(r"%[^\r\n]*")


def _code(src: str) -> int | None:
    h = "".join(src.split())
    if not h or len(h) > 8:
        return None
    return int(h, 16)


def _unicode(dst: str) -> int | None:
    """One code point from a ToUnicode destination (UTF-16BE; tolerant of odd-length hex
    such as ``<10783>``); ``None`` for empty or multi-character destinations."""
    h = "".join(dst.split())
    if not h:
        return None
    if len(h) % 2:
        h = "0" + h
    raw = bytes.fromhex(h)
    if len(raw) % 2 == 0:
        try:
            text = raw.decode("utf-16-be")
        except UnicodeDecodeError:
            text = ""
        if len(text) == 1:
            return ord(text)
        if len(text) > 1:
            return None
    value = int(h, 16)
    return value if 0 < value <= _MAX_UNICODE else None


def tounicode_map(text: str | bytes) -> dict[int, int]:
    """``code -> unicode`` of a ToUnicode CMap (``bfchar``, ``bfrange`` with a start
    value or an array). Multi-character destinations (ligatures) are skipped."""
    if isinstance(text, bytes):
        text = text.decode("latin-1")
    text = _COMMENT_RE.sub("", text)
    out: dict[int, int] = {}
    for block in _BFCHAR_RE.finditer(text):
        for src, dst in _PAIR_RE.findall(block.group(1)):
            code, u = _code(src), _unicode(dst)
            if code is not None and u is not None:
                out[code] = u
    for block in _BFRANGE_RE.finditer(text):
        for lo_s, hi_s, dst, arr in _RANGE_RE.findall(block.group(1)):
            lo, hi = _code(lo_s), _code(hi_s)
            if lo is None or hi is None or hi < lo:
                continue
            hi = min(hi, lo + MAX_BFRANGE - 1)
            if arr:
                for k, item in enumerate(_HEX_ONLY_RE.findall(arr)):
                    u = _unicode(item)
                    if u is not None and lo + k <= hi:
                        out[lo + k] = u
                continue
            u = _unicode(dst)
            if u is None:
                continue
            for c in range(lo, hi + 1):
                v = u + (c - lo)
                if v <= _MAX_UNICODE:
                    out[c] = v
    return out


def parse_tounicode(text: str | bytes) -> dict[int, set[int]]:
    """``unicode -> {codes}`` of a ToUnicode CMap (the inverse of :func:`tounicode_map`)."""
    inverse: dict[int, set[int]] = {}
    for code, u in tounicode_map(text).items():
        inverse.setdefault(u, set()).add(code)
    return inverse


# -- widths -------------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"\[|\]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _number(tok: str) -> float | None:
    try:
        return float(tok)
    except ValueError:
        return None


def parse_w_array(text: str) -> dict[int, float]:
    """``CID -> width`` (1/1000 em) of a CIDFont ``/W`` array (``c [w1 w2 …]`` and
    ``c_first c_last w`` forms); malformed parts are skipped."""
    toks = _TOKEN_RE.findall(text)
    if toks and toks[0] == "[":
        toks = toks[1:]
        if toks and toks[-1] == "]":
            toks = toks[:-1]
    out: dict[int, float] = {}
    i = 0
    while i < len(toks):
        first = _number(toks[i])
        if first is None:
            i += 1
            continue
        if i + 1 < len(toks) and toks[i + 1] == "[":
            c = int(first)
            j = i + 2
            while j < len(toks) and toks[j] != "]":
                w = _number(toks[j])
                if w is not None and toks[j] != "[":
                    out[c] = w
                    c += 1
                j += 1
            i = j + 1
            continue
        if i + 2 < len(toks):
            last, w = _number(toks[i + 1]), _number(toks[i + 2])
            if last is not None and w is not None and toks[i + 2] not in "[]":
                lo, hi = int(first), int(last)
                for c in range(lo, min(hi, lo + MAX_BFRANGE - 1) + 1):
                    out[c] = w
                i += 3
                continue
        i += 1
    return out


def parse_number_array(text: str) -> list[float]:
    """Numbers of a PDF array (``/Widths``); anything else skipped."""
    return [v for tok in _TOKEN_RE.findall(text) if (v := _number(tok)) is not None]


# -- simple-font encodings ----------------------------------------------------------------
#: Glyph names of the Latin text encodings that are not a letter + accent.
_GLYPH_NAMES: dict[str, int] = {
    "space": 0x20, "exclam": 0x21, "quotedbl": 0x22, "numbersign": 0x23, "dollar": 0x24,
    "percent": 0x25, "ampersand": 0x26, "quotesingle": 0x27, "parenleft": 0x28,
    "parenright": 0x29, "asterisk": 0x2A, "plus": 0x2B, "comma": 0x2C, "hyphen": 0x2D,
    "period": 0x2E, "slash": 0x2F, "zero": 0x30, "one": 0x31, "two": 0x32, "three": 0x33,
    "four": 0x34, "five": 0x35, "six": 0x36, "seven": 0x37, "eight": 0x38, "nine": 0x39,
    "colon": 0x3A, "semicolon": 0x3B, "less": 0x3C, "equal": 0x3D, "greater": 0x3E,
    "question": 0x3F, "at": 0x40, "bracketleft": 0x5B, "backslash": 0x5C,
    "bracketright": 0x5D, "asciicircum": 0x5E, "underscore": 0x5F, "grave": 0x60,
    "braceleft": 0x7B, "bar": 0x7C, "braceright": 0x7D, "asciitilde": 0x7E,
    "Euro": 0x20AC, "quotesinglbase": 0x201A, "florin": 0x0192, "quotedblbase": 0x201E,
    "ellipsis": 0x2026, "dagger": 0x2020, "daggerdbl": 0x2021, "circumflex": 0x02C6,
    "perthousand": 0x2030, "guilsinglleft": 0x2039, "quoteleft": 0x2018,
    "quoteright": 0x2019, "quotedblleft": 0x201C, "quotedblright": 0x201D,
    "bullet": 0x2022, "endash": 0x2013, "emdash": 0x2014, "tilde": 0x02DC,
    "trademark": 0x2122, "guilsinglright": 0x203A, "nbspace": 0xA0,
    "nonbreakingspace": 0xA0, "exclamdown": 0xA1, "cent": 0xA2, "sterling": 0xA3,
    "currency": 0xA4, "yen": 0xA5, "brokenbar": 0xA6, "section": 0xA7, "dieresis": 0xA8,
    "copyright": 0xA9, "ordfeminine": 0xAA, "guillemotleft": 0xAB, "logicalnot": 0xAC,
    "sfthyphen": 0xAD, "registered": 0xAE, "macron": 0xAF, "degree": 0xB0,
    "plusminus": 0xB1, "twosuperior": 0xB2, "threesuperior": 0xB3, "acute": 0xB4,
    "mu": 0xB5, "paragraph": 0xB6, "periodcentered": 0xB7, "cedilla": 0xB8,
    "onesuperior": 0xB9, "ordmasculine": 0xBA, "guillemotright": 0xBB,
    "onequarter": 0xBC, "onehalf": 0xBD, "threequarters": 0xBE, "questiondown": 0xBF,
    "multiply": 0xD7, "divide": 0xF7, "AE": 0xC6, "ae": 0xE6, "OE": 0x152, "oe": 0x153,
    "Oslash": 0xD8, "oslash": 0xF8, "germandbls": 0xDF, "Eth": 0xD0, "eth": 0xF0,
    "Thorn": 0xDE, "thorn": 0xFE, "dotlessi": 0x131, "Lslash": 0x141, "lslash": 0x142,
    "minus": 0x2212, "fraction": 0x2044, "ring": 0x2DA, "caron": 0x2C7, "breve": 0x2D8,
    "dotaccent": 0x2D9, "ogonek": 0x2DB, "hungarumlaut": 0x2DD,
}  # fmt: skip
#: Accent suffixes of composed glyph names ("eacute" = e + U+0301).
_ACCENTS = {
    "acute": "́", "grave": "̀", "circumflex": "̂", "dieresis": "̈",
    "tilde": "̃", "cedilla": "̧", "ring": "̊", "caron": "̌",
    "macron": "̄", "breve": "̆", "ogonek": "̨", "dotaccent": "̇",
    "hungarumlaut": "̋", "commaaccent": "̦",
}  # fmt: skip
_UNI_RE = re.compile(r"^uni([0-9A-F]{4})$")
_U_RE = re.compile(r"^u([0-9A-F]{4,6})$")


def glyph_name_to_unicode(name: str) -> int | None:
    """Code point of a glyph name (common Latin names, ``uniXXXX``, ``uXXXX[XX]``,
    one-letter names, letter + accent names); ``None`` when unknown."""
    name = name.split(".", 1)[0]
    if name in _GLYPH_NAMES:
        return _GLYPH_NAMES[name]
    if len(name) == 1:
        return ord(name)
    if m := (_UNI_RE.match(name) or _U_RE.match(name)):
        value = int(m.group(1), 16)
        return value if value <= _MAX_UNICODE else None
    for accent, mark in _ACCENTS.items():
        if name.endswith(accent) and len(name) == len(accent) + 1 and name[0].isalpha():
            composed = unicodedata.normalize("NFC", name[0] + mark)
            if len(composed) == 1:
                return ord(composed)
    return None


def parse_differences(text: str) -> dict[int, int]:
    """``code -> unicode`` of an ``/Encoding /Differences`` array (unknown names
    skipped)."""
    out: dict[int, int] = {}
    code: int | None = None
    for tok in re.findall(r"/[^\s/\[\]()<>]+|[-+]?\d+", text):
        if tok.startswith("/"):
            if code is None:
                continue
            u = glyph_name_to_unicode(_decode_pdf_name(tok[1:]))
            if u is not None:
                out[code] = u
            code += 1
        else:
            code = int(tok)
    return out


def _decode_pdf_name(name: str) -> str:
    return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), name)


#: Python codecs of the base encodings (StandardEncoding approximated by its ASCII part).
_BASE_CODECS = {"WinAnsiEncoding": "cp1252", "MacRomanEncoding": "mac_roman"}


def base_encoding(name: str | None) -> dict[int, int]:
    """``code -> unicode`` of a base encoding name (``WinAnsiEncoding``,
    ``MacRomanEncoding``; anything else is StandardEncoding's ASCII part)."""
    codec = _BASE_CODECS.get(name or "")
    out: dict[int, int] = {}
    for code in range(32, 256):
        if codec is None:
            if code < 127:
                out[code] = code
            continue
        try:
            out[code] = ord(bytes([code]).decode(codec))
        except UnicodeDecodeError:
            continue
    if codec is None:  # StandardEncoding's quotes differ from ASCII
        out[0x27] = 0x2019
        out[0x60] = 0x2018
    return out


# -- embedded fonts -----------------------------------------------------------------------
class FontKind(enum.StrEnum):
    """How a PDF font object encodes text: Type0 + TrueType CIDFont with Identity-H
    (``TYPE0``), simple TrueType with an embedded program (``SIMPLE``), anything else
    (Type1, CFF, Type3, other CMaps, not embedded: ``OTHER``, never reused)."""

    TYPE0 = "type0"
    SIMPLE = "simple"
    OTHER = "other"


#: FontDescriptor /Flags bits.
FLAG_FIXED_PITCH = 1 << 0
FLAG_SERIF = 1 << 1
FLAG_SYMBOLIC = 1 << 2
FLAG_ITALIC = 1 << 6
FLAG_FORCE_BOLD = 1 << 18


@dataclass(frozen=True, eq=False)
class EmbeddedFont:
    """What a PDF font object can write (built by
    :func:`pdfeditor.core.fontread.read_embedded_font`).

    ``unicode_to_code`` maps a character to the code to show (2-byte CID for ``TYPE0``,
    one byte for ``SIMPLE``), restricted to codes whose glyph exists; ``code_to_gid``
    gives the glyph of a code; ``glyphs_present`` the glyph ids with an outline (``None``
    when unknown); ``widths`` the advance of each code in 1/1000 em (``/W`` or
    ``/Widths``), ``default_width`` for codes missing from a ``/W``.
    """

    xref: int
    resource_name: str = ""
    kind: FontKind = FontKind.OTHER
    base_font: str = ""
    family: str = ""
    style: str = ""
    bold: bool = False
    italic: bool = False
    serif: bool = False
    mono: bool = False
    flags: int = 0
    fs_type: int | None = None
    num_glyphs: int = 0
    glyphs_present: frozenset[int] | None = None
    unicode_to_code: Mapping[int, int] = field(default_factory=dict)
    code_to_gid: Mapping[int, int] = field(default_factory=dict)
    first_char: int = 0
    last_char: int = 0
    widths: Mapping[int, float] = field(default_factory=dict)
    default_width: float = 1000.0
    #: The program's own ``cmap`` supplied ``unicode_to_code`` (else ToUnicode / encoding).
    has_cmap: bool = False

    @property
    def editable(self) -> bool:
        """Can write text at all (a usable kind, and not a restricted licence)."""
        return self.kind is not FontKind.OTHER and not is_restricted(self.fs_type)

    def code_for(self, ch: str) -> int | None:
        """The code that shows ``ch`` with an existing glyph, ``None`` when it cannot."""
        code = self.unicode_to_code.get(ord(ch))
        if code is None:
            return None
        if self.kind is FontKind.SIMPLE and not self.first_char <= code <= self.last_char:
            return None
        if self.kind is FontKind.TYPE0 and self.widths and code not in self.widths:
            return None
        gid = self.code_to_gid.get(code, code if self.kind is FontKind.TYPE0 else None)
        if gid is None or gid <= 0 or (self.num_glyphs and gid >= self.num_glyphs):
            return None
        if self.glyphs_present is not None and gid not in self.glyphs_present:
            if not ch.isspace():  # a space has no outline
                return None
        return code

    def missing(self, text: str) -> str:
        """The distinct characters of ``text`` this font cannot show, in order."""
        out: list[str] = []
        for ch in text:
            if ch not in out and self.code_for(ch) is None:
                out.append(ch)
        return "".join(out)

    def codes(self, text: str) -> tuple[int, ...]:
        """Codes showing ``text``; ``KeyError`` naming the first character it lacks."""
        out: list[int] = []
        for ch in text:
            code = self.code_for(ch)
            if code is None:
                raise KeyError(ch)
            out.append(code)
        return tuple(out)
