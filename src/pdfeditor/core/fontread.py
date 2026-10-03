"""Read a PDF font object into an :class:`~pdfeditor.core.fontinfo.EmbeddedFont` (M7).

The MuPDF side of :mod:`pdfeditor.core.fontinfo`: the caller holds ``PdfDocument.lock``.
Only two kinds can write new text with the document's own font (docs/M7_PLAN.md F2/F3):

* ``TYPE0``: ``/Type0`` + ``/Identity-H`` (or ``-V``) over a ``/CIDFontType2`` with a
  ``/FontFile2``. CID → glyph through ``/CIDToGIDMap`` (absent/``/Identity`` = same
  number, or a stream). Characters come from the program's ``cmap`` when it has one,
  else from the inverted ``/ToUnicode`` CMap; both are filtered by glyph presence
  (MuPDF's ToUnicode covers the whole font, its subsets only the used outlines).
* ``SIMPLE``: ``/TrueType`` with a ``/FontFile2``. Code → character from ``/ToUnicode``
  or ``/Encoding`` (base encoding + ``/Differences``), code → glyph through the
  program's (3,0) symbol, (3,1) Unicode or (1,0) Mac ``cmap``; only codes within
  ``/FirstChar``..``/LastChar``.

Everything else is ``OTHER``. A broken program or dictionary never raises: what could
not be read is left empty (so the font is not reused).
"""

from __future__ import annotations

import logging
import re
import struct

import pymupdf

from pdfeditor.core import fontinfo
from pdfeditor.core.fontinfo import EmbeddedFont, FontKind

log = logging.getLogger(__name__)

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
_REF_RE = re.compile(r"(\d+)\s+\d+\s+R")
#: ``fontembed.BASE_FONT_SUFFIX`` (fontembed imports fontTools; keep this module light).
_OWN_SUFFIX = "-PDFEditor"
_STYLE_WORDS = ("bold", "italic", "oblique", "black", "heavy", "semibold", "demibold")


def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        kind, value = doc.xref_get_key(xref, key)
    except Exception:  # noqa: BLE001 - a broken object reads as absent
        return "null", "null"
    return kind, value


def _ref(value: str) -> int:
    m = _REF_RE.search(value)
    return int(m.group(1)) if m else 0


def _sub_xref(doc: pymupdf.Document, xref: int, key: str) -> int:
    """The xref an indirect ``key`` (or the first reference of an array) points to."""
    kind, value = _key(doc, xref, key)
    if kind in ("xref", "array"):
        return _ref(value)
    return 0


def _int(doc: pymupdf.Document, xref: int, key: str, default: int = 0) -> int:
    kind, value = _key(doc, xref, key)
    if kind == "xref":
        target = _ref(value)
        try:
            value = doc.xref_object(target).strip()
        except Exception:  # noqa: BLE001
            return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _float(doc: pymupdf.Document, xref: int, key: str, default: float = 0.0) -> float:
    kind, value = _key(doc, xref, key)
    if kind == "xref":
        try:
            value = doc.xref_object(_ref(value)).strip()
        except Exception:  # noqa: BLE001
            return default
    try:
        return float(value)
    except ValueError:
        return default


def _name(doc: pymupdf.Document, xref: int, key: str) -> str:
    kind, value = _key(doc, xref, key)
    return value[1:] if kind == "name" else ""


def _value_text(doc: pymupdf.Document, xref: int, key: str) -> str:
    """The source of a (possibly indirect) array or dict value, "" when absent."""
    kind, value = _key(doc, xref, key)
    if kind == "xref":
        try:
            return doc.xref_object(_ref(value))
        except Exception:  # noqa: BLE001
            return ""
    return value if kind in ("array", "dict") else ""


def _stream(doc: pymupdf.Document, xref: int) -> bytes:
    if not xref:
        return b""
    try:
        return doc.xref_stream(xref) or b""
    except Exception:  # noqa: BLE001
        return b""


def split_base_font(base_font: str) -> tuple[str, str]:
    """``(family, style)`` from a ``/BaseFont``: subset prefix and ``#xx`` dropped, style
    after "," or "-" ("Calibri,Bold", "Arial-BoldItalicMT" → "BoldItalic"). The suffix
    of PDF Editor's own fonts ("Calibri-Bold-PDFEditor") is dropped too."""
    name = _SUBSET_RE.sub("", base_font)
    if name.endswith(_OWN_SUFFIX):
        name = name[: -len(_OWN_SUFFIX)]
    name = re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), name)
    for sep in (",", "-"):
        if sep in name:
            family, _, style = name.partition(sep)
            if any(w in style.lower() for w in (*_STYLE_WORDS, "regular", "roman", "mt")):
                return family, re.sub(r"(?i)(MT|PS)$", "", style)
    return name, ""


def read_embedded_font(doc: pymupdf.Document, xref: int, resource_name: str = "") -> EmbeddedFont:
    """Describe the font object ``xref`` (caller holds the document lock)."""
    subtype = _name(doc, xref, "Subtype")
    base_font = _name(doc, xref, "BaseFont")
    family, style = split_base_font(base_font)
    desc = xref
    kind = FontKind.OTHER
    cid_to_gid: dict[int, int] | None = None  # None = identity
    if subtype == "Type0":
        desc = _sub_xref(doc, xref, "DescendantFonts")
        encoding = _name(doc, xref, "Encoding")
        if (
            desc
            and _name(doc, desc, "Subtype") == "CIDFontType2"
            and encoding in ("Identity-H", "Identity-V")
        ):
            kind = FontKind.TYPE0
        if desc:
            kind_map, value = _key(doc, desc, "CIDToGIDMap")
            if kind_map == "xref":
                raw = _stream(doc, _ref(value))
                cid_to_gid = {
                    i // 2: (raw[i] << 8) | raw[i + 1]
                    for i in range(0, len(raw) - 1, 2)
                    if raw[i] or raw[i + 1]
                }
    elif subtype == "TrueType":
        kind = FontKind.SIMPLE
    fd = _sub_xref(doc, desc, "FontDescriptor") if desc else 0
    flags = _int(doc, fd, "Flags") if fd else 0
    program_xref = _sub_xref(doc, fd, "FontFile2") if fd else 0
    program = _stream(doc, program_xref)
    if not program:
        kind = FontKind.OTHER

    names = fontinfo.FontNames()
    fs_type: int | None = None
    num_glyphs = 0
    present: frozenset[int] | None = None
    serif: bool | None = None
    unicode_cmap: dict[int, int] = {}
    symbol_cmap: dict[int, int] = {}
    mac_cmap: dict[int, int] = {}
    if program:
        try:
            names = fontinfo.names(program)
            fs_type = fontinfo.fs_type(program)
            num_glyphs = fontinfo.glyph_count(program)
            present = fontinfo.nonempty_glyphs(program)
            serif = fontinfo.panose_serif(program)
            unicode_cmap = fontinfo.cmap(program)
            symbol_cmap = fontinfo.cmap(program, kind="symbol")
            mac_cmap = fontinfo.cmap(program, kind="mac")
        except (fontinfo.FontFileError, struct.error, ValueError) as exc:
            log.info("font %d: unreadable program (%s)", xref, exc)
            kind = FontKind.OTHER
        if present is None:  # no glyf/loca: cannot verify glyphs
            kind = FontKind.OTHER
    if names.family:
        family, style = names.family, names.style or style

    tounicode = fontinfo.tounicode_map(_stream(doc, _sub_xref(doc, xref, "ToUnicode")))
    unicode_to_code: dict[int, int] = {}
    code_to_gid: dict[int, int] = {}
    widths: dict[int, float] = {}
    default_width = 1000.0
    first_char = last_char = 0
    has_cmap = False

    if kind is FontKind.TYPE0:
        widths = fontinfo.parse_w_array(_value_text(doc, desc, "W"))
        default_width = _float(doc, desc, "DW", 1000.0)
        gid_to_cid: dict[int, int] = {}
        if cid_to_gid is not None:
            code_to_gid = cid_to_gid
            for cid, gid in sorted(cid_to_gid.items(), reverse=True):
                gid_to_cid[gid] = cid
        if unicode_cmap:
            has_cmap = True
            for u, gid in unicode_cmap.items():
                cid = gid if cid_to_gid is None else gid_to_cid.get(gid)
                if cid is not None:
                    unicode_to_code[u] = cid
        for cid, u in sorted(tounicode.items()):
            gid = cid if cid_to_gid is None else cid_to_gid.get(cid, 0)
            if u in unicode_to_code or not gid:
                continue
            if present is not None and gid not in present and not chr(u).isspace():
                continue
            unicode_to_code[u] = cid
    elif kind is FontKind.SIMPLE:
        first_char = _int(doc, xref, "FirstChar")
        last_char = _int(doc, xref, "LastChar", 255)
        for i, w in enumerate(fontinfo.parse_number_array(_value_text(doc, xref, "Widths"))):
            widths[first_char + i] = w
        encoding = _simple_encoding(doc, xref, flags)
        for code in range(first_char, min(last_char, 255) + 1):
            u = encoding.get(code) or tounicode.get(code)
            gid = 0
            if symbol_cmap:
                gid = symbol_cmap.get(0xF000 + code) or symbol_cmap.get(code, 0)
            if not gid and unicode_cmap and u is not None:
                gid = unicode_cmap.get(u, 0)
            if not gid and mac_cmap:
                gid = mac_cmap.get(code, 0)
            if not gid:
                continue
            code_to_gid[code] = gid
            u = tounicode.get(code, u)
            if u is None or u in unicode_to_code:
                continue
            if present is not None and gid not in present and not chr(u).isspace():
                continue
            unicode_to_code[u] = code
        has_cmap = bool(unicode_cmap or symbol_cmap or mac_cmap)

    # A space shown through a no-break-space mapping (MuPDF's own ToUnicode) and back.
    for a, b in ((0x20, 0xA0), (0xA0, 0x20)):
        if a not in unicode_to_code and b in unicode_to_code:
            unicode_to_code[a] = unicode_to_code[b]

    lowered = style.lower()
    weight = _int(doc, fd, "FontWeight") if fd else 0
    bold = (
        any(w in lowered for w in ("bold", "black", "heavy"))
        or bool(flags & fontinfo.FLAG_FORCE_BOLD)
        or weight >= 600
    )
    italic = (
        "italic" in lowered
        or "oblique" in lowered
        or bool(flags & fontinfo.FLAG_ITALIC)
        or (bool(fd) and abs(_float(doc, fd, "ItalicAngle")) > 0.5)
    )
    if serif is None:
        serif = bool(flags & fontinfo.FLAG_SERIF)
        if program and kind is not FontKind.OTHER:
            serif = _mupdf_flag(program, "serif", serif)
    mono = bool(flags & fontinfo.FLAG_FIXED_PITCH)
    if program and not mono:
        mono = _mupdf_flag(program, "mono", mono)
    return EmbeddedFont(
        xref=xref,
        resource_name=resource_name,
        kind=kind,
        base_font=base_font,
        family=family,
        style=style,
        bold=bold,
        italic=italic,
        serif=serif,
        mono=mono,
        flags=flags,
        fs_type=fs_type,
        num_glyphs=num_glyphs,
        glyphs_present=present,
        unicode_to_code=unicode_to_code,
        code_to_gid=code_to_gid,
        first_char=first_char,
        last_char=last_char,
        widths=widths,
        default_width=default_width,
        has_cmap=has_cmap,
    )


def _mupdf_flag(program: bytes, name: str, default: bool) -> bool:
    try:
        return bool(pymupdf.Font(fontbuffer=program).flags.get(name, default))
    except Exception:  # noqa: BLE001 - FreeType refused the program
        return default


def _simple_encoding(doc: pymupdf.Document, xref: int, flags: int) -> dict[int, int]:
    """``code -> unicode`` of a simple font's ``/Encoding`` (name or dictionary with
    ``/BaseEncoding`` and ``/Differences``); empty for a symbolic font without one."""
    kind, value = _key(doc, xref, "Encoding")
    if kind == "name":
        return fontinfo.base_encoding(value[1:])
    if kind in ("dict", "xref"):
        enc = _ref(value) if kind == "xref" else 0
        if enc:
            base = _name(doc, enc, "BaseEncoding")
            diffs = _value_text(doc, enc, "Differences")
        else:
            base = _name(doc, xref, "Encoding/BaseEncoding")
            diffs = _value_text(doc, xref, "Encoding/Differences")
        out = {} if (flags & fontinfo.FLAG_SYMBOLIC and not base) else fontinfo.base_encoding(base)
        out.update(fontinfo.parse_differences(diffs))
        return out
    if flags & fontinfo.FLAG_SYMBOLIC:
        return {}
    return fontinfo.base_encoding(None)


def page_fonts(doc: pymupdf.Document, page: pymupdf.Page) -> dict[int, EmbeddedFont]:
    """Every font of ``page`` (``get_fonts(full=True)``) by xref (caller holds the lock)."""
    out: dict[int, EmbeddedFont] = {}
    for entry in page.get_fonts(full=True):
        xref = int(entry[0])
        if xref and xref not in out:
            out[xref] = read_embedded_font(doc, xref, str(entry[4]))
    return out
