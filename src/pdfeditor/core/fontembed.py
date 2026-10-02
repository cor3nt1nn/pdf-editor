"""Embed an installed font for edited page text (M7, docs/M7_PLAN.md §1.2 step 5).

When the document's own font cannot show the new text (:mod:`pdfeditor.core.fontmatch`
plans ``SYSTEM`` or ``GENERIC``), the text is written with an installed TrueType face
embedded by this module. MuPDF's own helpers are unusable: ``Document.subset_fonts``
rewrites every font of the document, ``insert_font`` with a font file embeds the whole font
(1.6 MB for Calibri) with a /W and ToUnicode for every glyph (plan F6). So the objects
are built here:

* ``/Type0`` font, ``/BaseFont /PDFED+<PostScript name>``, ``/Encoding /Identity-H``,
  ``/ToUnicode`` (``bfchar`` for each used glyph), ``/DescendantFonts [CIDFontType2]``;
* ``/CIDFontType2`` with ``/CIDToGIDMap /Identity`` (code = glyph id), ``/DW 1000`` and a
  ``/W`` for each used glyph;
* ``/FontDescriptor`` from the program's head/hhea/OS/2/post tables;
* ``/FontFile2``: a fontTools subset with ``retain_gids`` (glyph ids unchanged, so codes
  stay valid when glyphs are added), no hinting, no layout tables.

One font object per document and installed face (keyed by PostScript name). Later edits
needing more glyphs call :func:`extend_font`, which re-subsets the face with the previous
glyphs plus the new ones and rewrites /W and /ToUnicode (≈ 40 ms for Calibri). Each page
using it names it ``/PdfEd<n>`` in its ``/Resources /Font``. The registry is cached on
the ``pymupdf.Document`` and rebuilt by scanning the pages' ``/Resources /Font`` for
``PdfEd*`` names when the document was (re)opened.

:func:`encode` gives the ``Tj`` operand for a text: glyph ids for an :class:`M7Font`,
the document font's codes for a reused :class:`~pdfeditor.core.fontinfo.EmbeddedFont`.

Everything here runs with ``PdfDocument.lock`` held by the caller.
"""

from __future__ import annotations

import functools
import io
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from fontTools import subset as ft_subset
from fontTools import ttLib

from pdfeditor.core import fontinfo
from pdfeditor.core.fontinfo import EmbeddedFont, FontKind
from pdfeditor.core.pdfdict import (
    dict_refs,
    get_nested,
    inherited,
    inherited_owner,
    set_nested,
)

log = logging.getLogger(__name__)

#: Resource names of the embedded fonts: ``/PdfEd1``, ``/PdfEd2``…
RESOURCE_PREFIX = "PdfEd"
#: ``/BaseFont`` prefix of the embedded fonts (marks them as ours, and as subsets).
BASE_FONT_TAG = "PDFEDT+"
#: Tables dropped from the subset (layout is not applied when writing a run).
DROP_TABLES = ("GSUB", "GPOS", "GDEF", "DSIG", "meta")
_REGISTRY_ATTR = "_pdfeditor_m7_fonts"
_RESOURCE_RE = re.compile(rf"^{RESOURCE_PREFIX}(\d+)$")
_PS_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_SPACE, _NBSP = 0x20, 0xA0


class FontEmbedError(ValueError):
    """An installed font cannot be embedded (unreadable, not TrueType, restricted
    licence, or lacking a character)."""


# -- the installed face ------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class _Source:
    """An installed TrueType face, read once (cached by path, index and mtime)."""

    path: Path
    index: int
    data: bytes
    ps_name: str
    units_per_em: int
    cmap: Mapping[int, int]
    advances: tuple[int, ...]
    descriptor: Mapping[str, float | int]

    def gid(self, ch: str) -> int | None:
        u = ord(ch)
        gid = self.cmap.get(u)
        if gid is None and u in (_SPACE, _NBSP):
            gid = self.cmap.get(_NBSP if u == _SPACE else _SPACE)
        return gid

    def width(self, gid: int) -> int:
        if not self.advances:
            return 1000
        adv = self.advances[gid] if gid < len(self.advances) else self.advances[-1]
        return round(adv * 1000 / self.units_per_em)


@functools.lru_cache(maxsize=8)
def _load_source(path: str, index: int, mtime_ns: int, size: int) -> _Source:
    del mtime_ns, size  # cache key only
    try:
        data = Path(path).read_bytes()
        tables = fontinfo.tables(data, index)
    except (OSError, fontinfo.FontFileError, ValueError) as exc:
        raise FontEmbedError(f"{path}: unreadable font ({exc})") from exc
    if "glyf" not in tables or "loca" not in tables:
        raise FontEmbedError(f"{path}: not a TrueType-outline font")
    if fontinfo.is_restricted(fontinfo.fs_type(data, index)):
        raise FontEmbedError(f"{path}: the font's licence forbids embedding")
    try:
        font = ttLib.TTFont(io.BytesIO(data), fontNumber=index, lazy=True)
        upem = int(font["head"].unitsPerEm) or 1000
        names = fontinfo.names(data, index)
        ps_name = _PS_NAME_RE.sub("", names.postscript_name or names.family or "Font")
        descriptor = _descriptor_values(font, upem, data, index)
        cmap = fontinfo.cmap(data, index)
        advances = fontinfo.advance_widths(data, index)
    except FontEmbedError:
        raise
    except Exception as exc:  # noqa: BLE001 - fontTools/struct errors on a broken file
        raise FontEmbedError(f"{path}: unreadable font ({exc})") from exc
    return _Source(Path(path), index, data, ps_name or "Font", upem, cmap, advances, descriptor)


def _source(path: Path | str, index: int) -> _Source:
    p = Path(path)
    try:
        st = p.stat()
    except OSError as exc:
        raise FontEmbedError(f"{p}: {exc}") from exc
    return _load_source(str(p), index, st.st_mtime_ns, st.st_size)


def _descriptor_values(
    font: ttLib.TTFont, upem: int, data: bytes, index: int
) -> dict[str, float | int]:
    def em(v: float) -> int:
        return round(v * 1000 / upem)

    head, hhea = font["head"], font["hhea"]
    os2 = font["OS/2"] if "OS/2" in font else None
    post = font["post"] if "post" in font else None
    italic_angle = float(post.italicAngle) if post is not None else 0.0
    weight = int(os2.usWeightClass) if os2 is not None else 400
    ascent = em(hhea.ascent)
    descent = em(hhea.descent)
    cap = em(os2.sCapHeight) if os2 is not None and getattr(os2, "sCapHeight", 0) else 0
    flags = fontinfo.FLAG_SYMBOLIC
    if post is not None and post.isFixedPitch:
        flags |= fontinfo.FLAG_FIXED_PITCH
    if fontinfo.panose_serif(data, index):
        flags |= fontinfo.FLAG_SERIF
    if italic_angle or (head.macStyle & 0x2):
        flags |= fontinfo.FLAG_ITALIC
    return {
        "Flags": flags,
        "x0": em(head.xMin),
        "y0": em(head.yMin),
        "x1": em(head.xMax),
        "y1": em(head.yMax),
        "ItalicAngle": italic_angle,
        "Ascent": ascent,
        "Descent": descent,
        "CapHeight": cap or round(ascent * 0.7),
        "StemV": round(50 + (weight / 65) ** 2),
        "FontWeight": weight,
    }


def subset_program(source_data: bytes, index: int, gids: Iterable[int]) -> bytes:
    """A TrueType program keeping only ``gids`` (and their components, and .notdef) with
    the glyph ids unchanged; no hinting, no layout tables."""
    font = ttLib.TTFont(io.BytesIO(source_data), fontNumber=index)
    options = ft_subset.Options()
    options.retain_gids = True
    options.hinting = False
    options.notdef_outline = True
    options.layout_features = []
    options.drop_tables += list(DROP_TABLES)
    subsetter = ft_subset.Subsetter(options)
    subsetter.populate(gids=sorted(set(gids)))
    subsetter.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


# -- the embedded font -------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class M7Font:
    """An installed face embedded by this module.

    ``xref`` is the ``/Type0`` font, ``resource_name`` its name in the page passed to
    :func:`ensure_font` (``PdfEd<n>``), ``present_gids`` the glyphs with a ``/W`` entry
    (they can be shown), ``unicode_to_gid`` their characters (from ``/ToUnicode``).
    ``path``/``index`` locate the installed face (needed by :func:`extend_font`).
    """

    xref: int
    resource_name: str
    present_gids: frozenset[int]
    ps_name: str
    cid_xref: int
    descriptor_xref: int
    file_xref: int
    tounicode_xref: int
    unicode_to_gid: Mapping[int, int] = field(default_factory=dict)
    widths: Mapping[int, float] = field(default_factory=dict)
    path: Path | None = None
    index: int = 0
    #: The installed face's cmap (when ``path`` is known): a character whose glyph is
    #: already present under another character (an alias) needs no new glyph.
    source_cmap: Mapping[int, int] = field(default_factory=dict)

    @property
    def base_font(self) -> str:
        return BASE_FONT_TAG + self.ps_name

    def gid_for(self, ch: str) -> int | None:
        """The glyph showing ``ch``, ``None`` when it is not in the subset yet."""
        u = ord(ch)
        gid = self.unicode_to_gid.get(u)
        if gid is None and u in (_SPACE, _NBSP):
            gid = self.unicode_to_gid.get(_NBSP if u == _SPACE else _SPACE)
        if gid is None:
            alias = self.source_cmap.get(u)
            if alias is not None and alias in self.present_gids:
                gid = alias
        return gid

    def missing(self, text: str) -> str:
        """The distinct characters of ``text`` not in the subset yet, in order."""
        out: list[str] = []
        for ch in text:
            if ch not in out and self.gid_for(ch) is None:
                out.append(ch)
        return "".join(out)


@dataclass
class _Registry:
    """PostScript name → ``/Type0`` xref, and the highest ``PdfEd<n>`` seen."""

    fonts: dict[str, int] = field(default_factory=dict)
    numbers: dict[int, int] = field(default_factory=dict)  # xref -> n
    last_number: int = 0


def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # noqa: BLE001
        return "null", "null"


def _ref(value: str) -> int:
    m = re.search(r"(\d+)\s+\d+\s+R", value)
    return int(m.group(1)) if m else 0


def _is_ours(doc: pymupdf.Document, xref: int) -> str:
    """The PostScript name of our ``/Type0`` font ``xref``, "" when it is not one."""
    kind, subtype = _key(doc, xref, "Subtype")
    if kind != "name" or subtype != "/Type0":
        return ""
    kind, base = _key(doc, xref, "BaseFont")
    prefix = "/" + BASE_FONT_TAG
    if kind != "name" or not base.startswith(prefix):
        return ""
    return base[len(prefix) :]


def _page_font_dict(doc: pymupdf.Document, page_xref: int) -> dict[str, int]:
    """``name -> xref`` of the page's (possibly inherited) ``/Resources /Font``."""
    owner = inherited_owner(doc, page_xref, "Resources")
    if not owner:
        return {}
    kind, source = get_nested(doc, owner, "Resources/Font", resolve=True)
    return dict_refs(source) if kind == "dict" else {}


def scan_registry(doc: pymupdf.Document) -> _Registry:
    """Rebuild the registry from the pages' ``/Resources /Font`` ``PdfEd*`` entries."""
    reg = _Registry()
    for pno in range(doc.page_count):
        try:
            page_xref = doc.page_xref(pno)
        except Exception:  # noqa: BLE001
            continue
        for name, xref in _page_font_dict(doc, page_xref).items():
            m = _RESOURCE_RE.match(name)
            if not m:
                continue
            n = int(m.group(1))
            reg.last_number = max(reg.last_number, n)
            ps = _is_ours(doc, xref)
            if ps and ps not in reg.fonts:
                reg.fonts[ps] = xref
                reg.numbers[xref] = n
    setattr(doc, _REGISTRY_ATTR, reg)
    return reg


def _registry(doc: pymupdf.Document) -> _Registry:
    reg = getattr(doc, _REGISTRY_ATTR, None)
    return reg if isinstance(reg, _Registry) else scan_registry(doc)


def registered_fonts(doc: pymupdf.Document) -> dict[str, int]:
    """PostScript name → ``/Type0`` xref of the fonts embedded by this module."""
    reg = _registry(doc)
    return {ps: x for ps, x in reg.fonts.items() if _is_ours(doc, x) == ps}


def load_font(
    doc: pymupdf.Document,
    xref: int,
    resource_name: str = "",
    *,
    path: Path | None = None,
    index: int = 0,
) -> M7Font:
    """Read our ``/Type0`` font ``xref`` back (``FontEmbedError`` when it is not ours)."""
    ps = _is_ours(doc, xref)
    if not ps:
        raise FontEmbedError(f"object {xref} is not a font embedded by PDF Editor")
    cid = _ref(_key(doc, xref, "DescendantFonts")[1])
    fd = _ref(_key(doc, cid, "FontDescriptor")[1]) if cid else 0
    ff = _ref(_key(doc, fd, "FontFile2")[1]) if fd else 0
    tu = _ref(_key(doc, xref, "ToUnicode")[1])
    if not (cid and fd and ff and tu):
        raise FontEmbedError(f"font {xref}: incomplete objects")
    w_kind, w_value = get_nested(doc, cid, "W", resolve=True)
    widths = fontinfo.parse_w_array(w_value) if w_kind == "array" else {}
    try:
        tounicode = fontinfo.tounicode_map(doc.xref_stream(tu) or b"")
    except Exception:  # noqa: BLE001
        tounicode = {}
    unicode_to_gid: dict[int, int] = {}
    for gid, u in sorted(tounicode.items()):
        if gid in widths:
            unicode_to_gid.setdefault(u, gid)
    source_cmap: Mapping[int, int] = {}
    if path is not None:
        try:
            source_cmap = _source(path, index).cmap
        except FontEmbedError as exc:
            log.info("font %d: %s", xref, exc)
    return M7Font(
        xref=xref,
        resource_name=resource_name,
        present_gids=frozenset(widths),
        ps_name=ps,
        cid_xref=cid,
        descriptor_xref=fd,
        file_xref=ff,
        tounicode_xref=tu,
        unicode_to_gid=unicode_to_gid,
        widths=widths,
        path=path,
        index=index,
        source_cmap=source_cmap,
    )


# -- PDF objects -------------------------------------------------------------------------
def w_array(widths: Mapping[int, float]) -> str:
    """``/W`` source: consecutive glyph ids grouped (``[3 [226] 68 [479 525]]``)."""
    parts: list[str] = []
    run: list[int] = []
    for gid in sorted(widths):
        if run and gid != run[-1] + 1:
            parts.append(_w_group(run, widths))
            run = []
        run.append(gid)
    if run:
        parts.append(_w_group(run, widths))
    return "[" + " ".join(parts) + "]"


def _w_group(run: Sequence[int], widths: Mapping[int, float]) -> str:
    return f"{run[0]} [" + " ".join(_num(widths[g]) for g in run) + "]"


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.3f}".rstrip("0").rstrip(".")


def _utf16_hex(u: int) -> str:
    return chr(u).encode("utf-16-be", "surrogatepass").hex().upper()


def tounicode_cmap(gid_to_unicode: Mapping[int, int]) -> bytes:
    """A ToUnicode CMap (``bfchar`` blocks of ≤ 100 entries) for 2-byte codes."""
    items = sorted(gid_to_unicode.items())
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    for start in range(0, len(items), 100):
        chunk = items[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines += [f"<{gid:04X}> <{_utf16_hex(u)}>" for gid, u in chunk]
        lines.append("endbfchar")
    lines += [
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def _new_object(doc: pymupdf.Document, source: str) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, source)
    return xref


def _write_program(doc: pymupdf.Document, file_xref: int, program: bytes) -> None:
    doc.update_stream(file_xref, program, compress=True)
    doc.xref_set_key(file_xref, "Length1", str(len(program)))


def _create_font(doc: pymupdf.Document, src: _Source) -> int:
    """Create the five objects of a font for ``src`` (the program, /W and ToUnicode are
    written by :func:`_write_glyphs`)."""
    base = "/" + BASE_FONT_TAG + src.ps_name
    d = src.descriptor
    file_xref = _new_object(doc, "<<>>")
    doc.update_stream(file_xref, b"", compress=False)
    fd_xref = _new_object(
        doc,
        f"<</Type/FontDescriptor/FontName{base}/Flags {d['Flags']}"
        f"/FontBBox[{d['x0']} {d['y0']} {d['x1']} {d['y1']}]"
        f"/ItalicAngle {_num(float(d['ItalicAngle']))}/Ascent {d['Ascent']}"
        f"/Descent {d['Descent']}/CapHeight {d['CapHeight']}/StemV {d['StemV']}"
        f"/FontWeight {d['FontWeight']}/FontFile2 {file_xref} 0 R>>",
    )
    cid_xref = _new_object(
        doc,
        f"<</Type/Font/Subtype/CIDFontType2/BaseFont{base}"
        "/CIDSystemInfo<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>"
        f"/FontDescriptor {fd_xref} 0 R/DW 1000/W []/CIDToGIDMap/Identity>>",
    )
    tu_xref = _new_object(doc, "<<>>")
    doc.update_stream(tu_xref, tounicode_cmap({}), compress=True)
    return _new_object(
        doc,
        f"<</Type/Font/Subtype/Type0/BaseFont{base}/Encoding/Identity-H"
        f"/DescendantFonts[{cid_xref} 0 R]/ToUnicode {tu_xref} 0 R>>",
    )


def _add_resource(doc: pymupdf.Document, page_xref: int, xref: int, reg: _Registry) -> str:
    """The name of font ``xref`` in the page's ``/Resources /Font``, added when absent."""
    entries = _page_font_dict(doc, page_xref)
    for name, target in entries.items():
        if target == xref:
            return name
    owner = inherited_owner(doc, page_xref, "Resources")

    def taken(name: str) -> bool:  # any value, not only references
        if name in entries:
            return True
        return bool(owner) and get_nested(doc, owner, f"Resources/Font/{name}")[0] != "null"

    n = reg.numbers.get(xref)
    if n is None:
        reg.last_number += 1
        n = reg.numbers[xref] = reg.last_number
    while taken(f"{RESOURCE_PREFIX}{n}"):
        n += 1
    name = f"{RESOURCE_PREFIX}{n}"
    reg.last_number = max(reg.last_number, n)
    kind, value = _key(doc, page_xref, "Resources")
    if kind == "null":
        # Inherited resources: give the page its own (same entries, superset after).
        inh_kind, inh_value = inherited(doc, page_xref, "Resources")
        doc.xref_set_key(page_xref, "Resources", inh_value if inh_kind != "null" else "<<>>")
    set_nested(doc, page_xref, f"Resources/Font/{name}", f"{xref} 0 R")
    return name


def ensure_font(
    doc: pymupdf.Document,
    page_xref: int,
    path: Path | str,
    index: int = 0,
    text: str = "",
) -> M7Font:
    """The installed face ``path``/``index`` embedded in ``doc`` (created on first use,
    one per document and face), named in page ``page_xref``'s ``/Resources /Font``, with
    glyphs for ``text`` (:func:`extend_font`). ``FontEmbedError`` when the face cannot
    be embedded or lacks a character of ``text``."""
    src = _source(path, index)
    reg = _registry(doc)
    xref = reg.fonts.get(src.ps_name, 0)
    if xref and _is_ours(doc, xref) != src.ps_name:
        reg = scan_registry(doc)
        xref = reg.fonts.get(src.ps_name, 0)
    created = not xref
    if created:
        _check_text(src, text)
        xref = _create_font(doc, src)
        reg.fonts[src.ps_name] = xref
        log.info("embedded font %s as object %d", src.ps_name, xref)
    name = _add_resource(doc, page_xref, xref, reg)
    font = load_font(doc, xref, name, path=src.path, index=src.index)
    if created:
        return _write_glyphs(doc, font, src, text)
    return extend_font(doc, font, text) if text else font


def _check_text(src: _Source, text: str) -> None:
    missing = "".join(dict.fromkeys(ch for ch in text if src.gid(ch) is None))
    if missing:
        raise FontEmbedError(f"{src.ps_name} lacks {missing!r}")


def extend_font(doc: pymupdf.Document, font: M7Font, text: str) -> M7Font:
    """``font`` with glyphs for every character of ``text``: when some are missing, the
    face is subset again with the previous glyphs plus the new ones (glyph ids kept) and
    ``/W`` and ``/ToUnicode`` are rewritten. Returns the updated font (``font`` itself
    when nothing was missing)."""
    if not font.missing(text):
        return font
    if font.path is None:
        raise FontEmbedError(f"{font.ps_name}: the installed font is unknown")
    src = _source(font.path, font.index)
    if src.ps_name != font.ps_name:
        raise FontEmbedError(f"{font.path} is not {font.ps_name}")
    _check_text(src, text)
    return _write_glyphs(doc, font, src, text)


def _write_glyphs(doc: pymupdf.Document, font: M7Font, src: _Source, text: str) -> M7Font:
    """Subset ``src`` with ``font``'s glyphs plus those of ``text``; rewrite the program,
    /W and ToUnicode."""
    gid_to_unicode: dict[int, int] = {g: u for u, g in font.unicode_to_gid.items()}
    for ch in text:
        if font.gid_for(ch) is None:
            gid = src.gid(ch)
            assert gid is not None  # _check_text
            gid_to_unicode.setdefault(gid, ord(ch))
    gids = set(font.present_gids) | set(gid_to_unicode)
    widths = {g: font.widths.get(g, src.width(g)) for g in gids}
    _write_program(doc, font.file_xref, subset_program(src.data, src.index, gids))
    doc.xref_set_key(font.cid_xref, "W", w_array(widths))
    doc.update_stream(font.tounicode_xref, tounicode_cmap(gid_to_unicode), compress=True)
    return load_font(doc, font.xref, font.resource_name, path=font.path, index=font.index)


# -- text → Tj operand -------------------------------------------------------------------
def encode(font: M7Font | EmbeddedFont, text: str, codes: Sequence[int] | None = None) -> bytes:
    """The hex string operand of ``Tj`` showing ``text`` with ``font``.

    An :class:`M7Font` shows glyph ids (Identity-H, 2 bytes); a reused
    :class:`EmbeddedFont` its own codes (2 bytes for ``TYPE0``, 1 byte within
    ``FirstChar``..``LastChar`` for ``SIMPLE``), ``codes`` overriding them (pass
    ``FontPlan.codes``). ``KeyError`` naming the first character that cannot be shown.
    """
    if isinstance(font, M7Font):
        out: list[int] = []
        for ch in text:
            gid = font.gid_for(ch)
            if gid is None:
                raise KeyError(ch)
            out.append(gid)
        return _hex(out, 2)
    if font.kind is FontKind.OTHER:
        raise KeyError(text[:1])
    values = tuple(codes) if codes is not None else font.codes(text)
    if font.kind is FontKind.SIMPLE:
        for ch, code in zip(text, values, strict=False):
            if not (font.first_char <= code <= font.last_char and 0 <= code <= 0xFF):
                raise KeyError(ch)
        return _hex(values, 1)
    for ch, code in zip(text, values, strict=False):
        if not 0 <= code <= 0xFFFF:
            raise KeyError(ch)
    return _hex(values, 2)


def _hex(codes: Iterable[int], width: int) -> bytes:
    fmt = "{:04X}" if width == 2 else "{:02X}"
    return ("<" + "".join(fmt.format(c) for c in codes) + ">").encode("ascii")
