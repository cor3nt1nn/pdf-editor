"""core/fontinfo.py and core/fontread.py (M7-T2)."""

from __future__ import annotations

import struct
from pathlib import Path

import pymupdf
import pytest
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_PATH,
    CAMBRIA_TTC_PATH,
    LINE2,
    PRINT_BASE_FONT,
    SIMPLE_TEXT,
    TIMES_PATH,
    font_xref,
    make_kerned_tj_pdf,
    make_print_like_pdf,
    make_simple_font_pdf,
    make_text_edit_pdf,
    make_type3_pdf,
    needs_text_fonts,
)

from pdfeditor.core import fontinfo
from pdfeditor.core.fontinfo import EmbeddedFont, FontFileError, FontKind
from pdfeditor.core.fontread import page_fonts, read_embedded_font, split_base_font


# -- a synthetic font ---------------------------------------------------------------------
def sfnt(tables: dict[str, bytes], version: bytes = b"\x00\x01\x00\x00") -> bytes:
    n = len(tables)
    head = version + struct.pack(">HHHH", n, 0, 0, 0)
    offset = 12 + 16 * n
    directory = b""
    body = b""
    for tag in sorted(tables):
        data = tables[tag]
        directory += tag.encode("latin-1") + struct.pack(">III", 0, offset + len(body), len(data))
        body += data + b"\x00" * (-len(data) % 4)
    return head + directory + body


def head_table(upem: int = 1000, long_loca: bool = False) -> bytes:
    data = bytearray(54)
    struct.pack_into(">H", data, 18, upem)
    struct.pack_into(">H", data, 44, 0)
    struct.pack_into(">h", data, 50, 1 if long_loca else 0)
    return bytes(data)


def os2_table(fs_type: int = 8, weight: int = 400, selection: int = 0x40, serif: int = 11) -> bytes:
    data = bytearray(78)
    struct.pack_into(">H", data, 4, weight)
    struct.pack_into(">H", data, 8, fs_type)
    struct.pack_into(">BB", data, 32, 2, serif)
    struct.pack_into(">H", data, 62, selection)
    return bytes(data)


def name_table(entries: dict[int, str]) -> bytes:
    records = b""
    strings = b""
    for nid, text in entries.items():
        raw = text.encode("utf-16-be")
        records += struct.pack(">HHHHHH", 3, 1, 0x409, nid, len(raw), len(strings))
        strings += raw
    return struct.pack(">HHH", 0, len(entries), 6 + len(records)) + records + strings


def cmap_table() -> bytes:
    """(3,1) format 4: A-C → 1-3 (delta), a → 5, b → 0 (idRangeOffset); (3,10) format 12:
    U+1F600-U+1F601 → 7-8."""
    seg = 3
    ends = (0x43, 0x62, 0xFFFF)
    starts = (0x41, 0x61, 0xFFFF)
    deltas = (1 - 0x41, 0, 1)
    offsets = (0, 4, 0)
    glyph_ids = (5, 0)
    f4 = struct.pack(">HHH", 4, 0, 0) + struct.pack(">HHHH", 2 * seg, 0, 0, 0)
    f4 += struct.pack(f">{seg}H", *ends) + b"\x00\x00" + struct.pack(f">{seg}H", *starts)
    f4 += struct.pack(f">{seg}h", *deltas) + struct.pack(f">{seg}H", *offsets)
    f4 += struct.pack(">2H", *glyph_ids)
    f4 = f4[:2] + struct.pack(">H", len(f4)) + f4[4:]
    f12 = struct.pack(">HHIII", 12, 0, 28, 0, 1) + struct.pack(">III", 0x1F600, 0x1F601, 7)
    header = struct.pack(">HH", 0, 2)
    first = 4 + 16
    header += struct.pack(">HHI", 3, 1, first) + struct.pack(">HHI", 3, 10, first + len(f4))
    return header + f4 + f12


def synthetic_font(*, fs_type: int = 8, long_loca: bool = False, glyf: bool = True) -> bytes:
    """10 glyphs; outlines for 1, 2, 5 and 7 only."""
    count = 10
    sizes = [0, 12, 12, 0, 0, 12, 0, 12, 0, 0]
    offsets = [0]
    for s in sizes:
        offsets.append(offsets[-1] + s)
    if long_loca:
        loca = struct.pack(f">{count + 1}I", *offsets)
    else:
        loca = struct.pack(f">{count + 1}H", *(o // 2 for o in offsets))
    hhea = bytearray(36)
    struct.pack_into(">H", hhea, 34, 3)
    tables = {
        "head": head_table(2048, long_loca),
        "maxp": struct.pack(">IH", 0x00010000, count),
        "hhea": bytes(hhea),
        "hmtx": struct.pack(">6H", 500, 0, 600, 0, 700, 0) + struct.pack(">7H", *([0] * 7)),
        "OS/2": os2_table(fs_type),
        "name": name_table({1: "Synth", 2: "Bold", 4: "Synth Bold", 6: "Synth-Bold"}),
        "cmap": cmap_table(),
    }
    if glyf:
        tables["loca"] = loca
        tables["glyf"] = b"\x00" * offsets[-1]
    return sfnt(tables)


def test_synthetic_font_tables() -> None:
    data = synthetic_font()
    tbl = fontinfo.tables(data)
    assert {"head", "maxp", "cmap", "loca", "glyf", "name", "OS/2"} <= set(tbl)
    assert fontinfo.face_count(data) == 1
    assert fontinfo.glyph_count(data) == 10
    assert fontinfo.units_per_em(data) == 2048
    assert fontinfo.fs_type(data) == 8
    n = fontinfo.names(data)
    assert (n.family, n.style, n.full_name, n.postscript_name) == (
        "Synth",
        "Bold",
        "Synth Bold",
        "Synth-Bold",
    )
    assert fontinfo.advance_widths(data) == (500, 600, 700, 700, 700, 700, 700, 700, 700, 700)


@pytest.mark.parametrize("long_loca", [False, True])
def test_nonempty_glyphs_short_and_long_loca(long_loca: bool) -> None:
    data = synthetic_font(long_loca=long_loca)
    assert fontinfo.nonempty_glyphs(data) == frozenset({1, 2, 5, 7})


def test_nonempty_glyphs_unknown_without_glyf() -> None:
    assert fontinfo.nonempty_glyphs(synthetic_font(glyf=False)) is None


def test_cmap_formats_4_and_12() -> None:
    data = synthetic_font()
    table = fontinfo.cmap(data)
    # format 12 wins over format 4
    assert table == {0x1F600: 7, 0x1F601: 8}
    assert fontinfo.cmap(data, kind="symbol") == {}
    # without the format 12 subtable the format 4 one is read (delta and idRangeOffset)
    tbl = fontinfo.tables(data)
    base = tbl["cmap"][0]
    patched = bytearray(data)
    struct.pack_into(">HH", patched, base + 2 + 8 + 2, 1, 0)  # second record → (1, 0)
    assert fontinfo.cmap(bytes(patched)) == {0x41: 1, 0x42: 2, 0x43: 3, 0x61: 5}


def test_restricted_fs_type() -> None:
    assert fontinfo.is_restricted(0x0002)
    assert fontinfo.is_restricted(0x0102)  # no-subsetting bit does not change the level
    assert not fontinfo.is_restricted(0x0006)  # preview & print wins
    assert not fontinfo.is_restricted(0x0008)
    assert not fontinfo.is_restricted(0)
    assert not fontinfo.is_restricted(None)
    assert fontinfo.fs_type(synthetic_font(fs_type=2)) == 2


def test_malformed_fonts_raise_font_file_error() -> None:
    with pytest.raises(FontFileError):
        fontinfo.tables(b"not a font at all")
    with pytest.raises(FontFileError):
        fontinfo.tables(b"\x00\x01")
    data = synthetic_font()
    with pytest.raises(FontFileError):
        fontinfo.tables(data, 1)
    truncated = data[:200]  # every table lies past the end: dropped
    assert fontinfo.tables(truncated) == {}
    assert fontinfo.names(truncated) == fontinfo.FontNames()
    assert fontinfo.cmap(truncated) == {}
    with pytest.raises(FontFileError):
        fontinfo.glyph_count(truncated)


@needs_text_fonts
def test_windows_fonts() -> None:
    calibri = CALIBRI_PATH.read_bytes()
    assert fontinfo.fs_type(calibri) == 8
    assert fontinfo.names(calibri).family == "Calibri"
    assert fontinfo.panose_serif(calibri) is False
    assert fontinfo.panose_serif(TIMES_PATH.read_bytes()) is True
    style = fontinfo.face_style(calibri)
    assert (style.bold, style.italic) == (False, False)
    table = fontinfo.cmap(calibri)
    assert table[ord("a")] and table[ord("é")]
    assert len(fontinfo.nonempty_glyphs(calibri) or ()) > 3000


@pytest.mark.skipif(not CAMBRIA_TTC_PATH.exists(), reason="cambria.ttc missing")
def test_collection_faces() -> None:
    data = CAMBRIA_TTC_PATH.read_bytes()
    assert fontinfo.face_count(data) >= 2
    assert fontinfo.names(data, 0).family == "Cambria"
    assert fontinfo.names(data, 1).family == "Cambria Math"
    assert fontinfo.fs_type(data, 0) == 8
    assert fontinfo.glyph_count(data, 0) > 1000
    assert fontinfo.cmap(data, 0)[ord("A")]


# -- ToUnicode, /W, encodings -------------------------------------------------------------
TOUNICODE = b"""/CIDInit /ProcSet findresource begin
12 dict begin begincmap
1 begincodespacerange <0000> <FFFF> endcodespacerange
% a comment <0099> <0041>
4 beginbfchar
<0003> <0020>
<0044> <0061>
<0045><10783>
<0046> <D835DC00>
<0047> <00660069>
endbfchar
2 beginbfrange
<0010> <0012> <0041>
<0020> <0021> [<00E9> <20AC>]
<0030> <002F> <0041>
endbfrange
endcmap
"""


def test_parse_tounicode_tolerant() -> None:
    inverse = fontinfo.parse_tounicode(TOUNICODE)
    assert inverse[0x20] == {3}
    assert inverse[0x61] == {0x44}
    assert inverse[0x10783] == {0x45}  # odd-length hex accepted
    assert inverse[0x1D400] == {0x46}  # surrogate pair
    assert 0x66 not in inverse  # "fi" ligature skipped
    assert inverse[0x41] == {0x10}
    assert inverse[0x43] == {0x12}
    assert inverse[0xE9] == {0x20}
    assert inverse[0x20AC] == {0x21}
    assert 0x99 not in fontinfo.tounicode_map(TOUNICODE)  # commented out; reversed range
    assert fontinfo.parse_tounicode("garbage <<>> [") == {}
    assert fontinfo.parse_tounicode(TOUNICODE.decode()) == inverse


def test_parse_w_array() -> None:
    assert fontinfo.parse_w_array("[1 [500 600.5] 10 12 250 20[100]]") == {
        1: 500,
        2: 600.5,
        10: 250,
        11: 250,
        12: 250,
        20: 100,
    }
    assert fontinfo.parse_w_array("[abc /x]") == {}
    assert fontinfo.parse_w_array("") == {}
    assert fontinfo.parse_w_array("[3 [") == {}
    assert fontinfo.parse_number_array("[226 0 507.5]") == [226, 0, 507.5]


def test_glyph_names_and_differences() -> None:
    g = fontinfo.glyph_name_to_unicode
    assert g("eacute") == 0xE9
    assert g("Agrave") == 0xC0
    assert g("Euro") == 0x20AC
    assert g("uni20AC") == 0x20AC
    assert g("u1F600") == 0x1F600
    assert g("a") == ord("a")
    assert g("a.sc") == ord("a")
    assert g("space") == 0x20
    assert g("nonsense_name") is None
    assert fontinfo.parse_differences("[32 /space /exclam 128 /Euro /bogus /eacute]") == {
        32: 0x20,
        33: 0x21,
        128: 0x20AC,
        130: 0xE9,
    }
    win = fontinfo.base_encoding("WinAnsiEncoding")
    assert win[0x80] == 0x20AC and win[0xE0] == 0xE0 and 0x81 not in win
    assert fontinfo.base_encoding(None)[0x41] == 0x41


def test_embedded_font_code_for() -> None:
    simple = EmbeddedFont(
        xref=1,
        kind=FontKind.SIMPLE,
        num_glyphs=10,
        glyphs_present=frozenset({5}),
        unicode_to_code={ord("a"): 97, ord(" "): 32, ord("b"): 98, ord("z"): 200},
        code_to_gid={97: 5, 32: 3, 98: 6, 200: 5},
        first_char=32,
        last_char=120,
    )
    assert simple.code_for("a") == 97
    assert simple.code_for(" ") == 32  # a space has no outline
    assert simple.code_for("b") is None  # no outline
    assert simple.code_for("z") is None  # outside FirstChar..LastChar
    assert simple.missing("a bba z") == "bz"
    assert simple.codes("a a") == (97, 32, 97)
    with pytest.raises(KeyError):
        simple.codes("ab")
    restricted = EmbeddedFont(xref=1, kind=FontKind.TYPE0, fs_type=2)
    assert not restricted.editable
    assert not EmbeddedFont(xref=1).editable


def test_split_base_font() -> None:
    assert split_base_font("ABCDEF+Calibri,Bold") == ("Calibri", "Bold")
    assert split_base_font("Arial-BoldItalicMT") == ("Arial", "BoldItalic")
    assert split_base_font("TimesNewRomanPSMT") == ("TimesNewRomanPSMT", "")
    assert split_base_font("CIDFont+F1") == ("CIDFont+F1", "")
    assert split_base_font("Segoe#20UI") == ("Segoe UI", "")
    assert split_base_font("Calibri-Light") == ("Calibri-Light", "")


# -- fixtures through fontread ------------------------------------------------------------
@needs_text_fonts
def test_calibri_subset_is_type0(tmp_path: Path) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "t.pdf"))
    xref, resource = font_xref(doc[0], "Calibri")
    font = read_embedded_font(doc, xref, resource)
    assert font.kind is FontKind.TYPE0
    assert font.family == "Calibri" and not font.bold and not font.italic
    assert font.resource_name == resource
    assert font.num_glyphs == fontinfo.glyph_count(CALIBRI_PATH.read_bytes())
    assert not font.has_cmap  # MuPDF's subsets drop the cmap
    assert font.missing("courrier.") == ""
    assert font.missing("Z") == "Z"
    assert font.missing(LINE2) == ""
    assert font.code_for(" ") is not None  # MuPDF's ToUnicode says U+00A0
    assert not font.serif and font.editable
    doc.close()


@needs_text_fonts
def test_tounicode_codes_equal_system_glyph_ids(tmp_path: Path) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "t.pdf"))
    xref, _ = font_xref(doc[0], "Calibri")
    font = read_embedded_font(doc, xref)
    system = fontinfo.cmap(CALIBRI_PATH.read_bytes())
    for ch in "aer":
        assert font.unicode_to_code[ord(ch)] == system[ord(ch)]
    times = read_embedded_font(doc, font_xref(doc[0], "Times")[0])
    assert times.family == "Times New Roman" and times.serif
    assert times.missing("Titre") == "" and times.missing("c") == "c"
    doc.close()


@needs_text_fonts
def test_simple_font_keeps_cmap(tmp_path: Path) -> None:
    doc = pymupdf.open(make_simple_font_pdf(tmp_path / "s.pdf"))
    xref, _ = font_xref(doc[0], "Calibri")
    font = read_embedded_font(doc, xref)
    assert font.kind is FontKind.SIMPLE
    assert font.has_cmap
    assert font.missing(SIMPLE_TEXT) == ""
    assert font.missing("d") == "d"
    assert font.codes("Jean à") == tuple("Jean à".encode("cp1252"))
    assert font.first_char <= 0xE0 <= font.last_char
    doc.close()


@needs_text_fonts
def test_kerned_simple_font_and_type3(tmp_path: Path) -> None:
    doc = pymupdf.open(make_kerned_tj_pdf(tmp_path / "k.pdf"))
    fonts = list(page_fonts(doc, doc[0]).values())
    assert [f.kind for f in fonts] == [FontKind.SIMPLE]
    assert fonts[0].missing("Facture due") == ""
    doc.close()
    doc = pymupdf.open(make_type3_pdf(tmp_path / "t3.pdf"))
    kinds = sorted(f.kind for f in page_fonts(doc, doc[0]).values())
    assert kinds == [FontKind.OTHER, FontKind.TYPE0]
    doc.close()


@needs_text_fonts
def test_print_like_font_named_by_its_name_table(tmp_path: Path) -> None:
    doc = pymupdf.open(make_print_like_pdf(tmp_path / "p.pdf"))
    xref, _ = font_xref(doc[0], PRINT_BASE_FONT)
    font = read_embedded_font(doc, xref)
    assert font.base_font.endswith(PRINT_BASE_FONT)
    assert font.kind is FontKind.TYPE0
    assert font.family == "Arial"
    arial = fontinfo.cmap(ARIAL_PATH.read_bytes())
    assert font.unicode_to_code[ord("F")] == arial[ord("F")]
    doc.close()
    doc = pymupdf.open(make_print_like_pdf(tmp_path / "p2.pdf", strip_name=True))
    font = read_embedded_font(doc, font_xref(doc[0], PRINT_BASE_FONT)[0])
    assert font.family == PRINT_BASE_FONT
    assert font.kind is FontKind.TYPE0  # still usable, only unnamed
    doc.close()


def _font_object(doc: pymupdf.Document, source: str) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, source)
    return xref


def test_unembedded_and_broken_fonts_are_other() -> None:
    doc = pymupdf.open()
    doc.new_page()
    plain = _font_object(
        doc,
        "<< /Type /Font /Subtype /TrueType /BaseFont /Calibri,Bold /FirstChar 32 "
        "/LastChar 32 /Widths [226] /Encoding /WinAnsiEncoding >>",
    )
    font = read_embedded_font(doc, plain)
    assert font.kind is FontKind.OTHER
    assert (font.family, font.bold, font.italic) == ("Calibri", True, False)
    garbage = doc.get_new_xref()
    doc.update_object(garbage, "<< >>")
    doc.update_stream(garbage, b"\x00\x01\x00\x00garbage")
    fd = _font_object(doc, f"<< /Type /FontDescriptor /Flags 96 /ItalicAngle -11 "
                           f"/FontWeight 700 /FontFile2 {garbage} 0 R >>")  # fmt: skip
    broken = _font_object(
        doc,
        f"<< /Type /Font /Subtype /TrueType /BaseFont /Calibri /FontDescriptor {fd} 0 R >>",
    )
    font = read_embedded_font(doc, broken)
    assert font.kind is FontKind.OTHER
    assert (font.bold, font.italic) == (True, True)
    assert read_embedded_font(doc, 99999).kind is FontKind.OTHER
    doc.close()
