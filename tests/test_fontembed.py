"""M7-T3: embedding installed fonts for edited page text (core/fontembed.py, core/pdfdict.py)."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest
from pdfcheck import strict_read
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_PATH,
    CAMBRIA_TTC_PATH,
    LINE2,
    TIMES_PATH,
    make_inherited_resources_pdf,
    make_simple_font_pdf,
    make_text_edit_pdf,
    needs_text_fonts,
    pixel_diff_bbox,
)
from timing import best_time

from pdfeditor.core import fontembed, fontinfo, fontread
from pdfeditor.core.fontembed import FontEmbedError, M7Font
from pdfeditor.core.fontinfo import FontKind
from pdfeditor.core.pdfdict import (
    dict_refs,
    get_nested,
    inherited,
    inherited_owner,
    set_nested,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pdfeditor"
TEXT20 = "Durand courrier 12/03 ZÉàç"  # 20 distinct glyphs (space included)
EXTEND_BUDGET_S = 0.150
#: Content origin of the test runs (PDF user space; page space y = 842 - 600 = 242).
ORIGIN = (72.0, 600.0)


def _show(doc: pymupdf.Document, page: pymupdf.Page, name: str, hexstr: bytes) -> pymupdf.Page:
    """Append ``BT /name 11 Tf … <hex> Tj ET`` to the page (test helper)."""
    ops = (
        f"q BT /{name} 11 Tf 0 g 1 0 0 1 {ORIGIN[0]:g} {ORIGIN[1]:g} Tm ".encode()
        + hexstr
        + b" Tj ET Q"
    )
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, ops)
    contents = page.get_contents() + [xref]
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in contents) + "]")
    return doc.reload_page(page)


def _line_text(page: pymupdf.Page, y: float = 842 - ORIGIN[1]) -> list[tuple[str, str]]:
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if abs(span["origin"][1] - y) < 0.5:
                    out.append(("".join(c["c"] for c in span["chars"]), span["font"]))
    return out


def _new_objects(doc: pymupdf.Document, start: int) -> dict[int, str]:
    return {x: doc.xref_object(x, compressed=True) for x in range(start, doc.xref_length())}


# -- pdfdict ------------------------------------------------------------------------------
def test_set_nested_descends_indirect_dicts() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    kind, value = doc.xref_get_key(page.xref, "Resources")
    assert kind == "xref"  # MuPDF's new pages have indirect /Resources
    res = int(value.split()[0])
    set_nested(doc, page.xref, "Resources/Font/F9", "/Helvetica")
    assert doc.xref_get_key(page.xref, "Resources") == ("xref", value)  # still shared
    assert doc.xref_get_key(res, "Font/F9") == ("name", "/Helvetica")
    assert get_nested(doc, page.xref, "Resources/Font/F9") == ("name", "/Helvetica")
    # an indirect /Font inside the indirect /Resources
    font_dict = doc.get_new_xref()
    doc.update_object(font_dict, "<</F1 7 0 R>>")
    doc.xref_set_key(res, "Font", f"{font_dict} 0 R")
    set_nested(doc, page.xref, "Resources/Font/F2", "8 0 R")
    assert doc.xref_get_key(font_dict, "F2") == ("xref", "8 0 R")
    kind, source = get_nested(doc, page.xref, "Resources/Font", resolve=True)
    assert kind == "dict" and dict_refs(source) == {"F1": 7, "F2": 8}
    assert get_nested(doc, page.xref, "Resources/Font")[0] == "xref"
    assert get_nested(doc, page.xref, "Resources/Missing/X") == ("null", "null")


def test_set_nested_creates_and_refuses() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    doc.xref_set_key(page.xref, "Resources", "<<>>")
    set_nested(doc, page.xref, "Resources/Font/F1", "/Helvetica")
    assert get_nested(doc, page.xref, "Resources/Font/F1") == ("name", "/Helvetica")
    doc.xref_set_key(page.xref, "Resources/ProcSet", "[/PDF]")
    with pytest.raises(ValueError):
        set_nested(doc, page.xref, "Resources/ProcSet/X", "1")
    with pytest.raises(ValueError):
        set_nested(doc, page.xref, "", "1")


@needs_text_fonts
def test_inherited_resources(tmp_path: Path) -> None:
    doc = pymupdf.open(make_inherited_resources_pdf(tmp_path / "inh.pdf"))
    page = doc[0]
    assert doc.xref_get_key(page.xref, "Resources")[0] == "null"
    owner = inherited_owner(doc, page.xref, "Resources")
    assert owner and owner != page.xref
    assert inherited(doc, page.xref, "Resources")[0] in ("dict", "xref")
    font = fontembed.ensure_font(doc, page.xref, ARIAL_PATH, 0, "Zidane")
    # The page got its own /Resources: a superset of the inherited ones.
    assert doc.xref_get_key(page.xref, "Resources")[0] != "null"
    names = {e[4] for e in doc.reload_page(page).get_fonts(full=True)}
    assert font.resource_name in names and len(names) == 2


# -- ensure_font --------------------------------------------------------------------------
@needs_text_fonts
def test_ensure_font_builds_small_type0_font(tmp_path: Path) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "word.pdf"))
    page = doc[0]
    start = doc.xref_length()
    font = fontembed.ensure_font(doc, page.xref, CALIBRI_PATH, 0, TEXT20)
    assert isinstance(font, M7Font)
    assert font.resource_name == "PdfEd1" and font.ps_name == "Calibri"
    assert len(set(TEXT20)) == 20 and len(font.present_gids) == 20
    objects = _new_objects(doc, start)
    assert len(objects) == 5
    type0 = objects[font.xref]
    assert "/Subtype/Type0" in type0 and "/Encoding/Identity-H" in type0
    assert "/BaseFont/PDFEDT+Calibri" in type0 and f"/ToUnicode {font.tounicode_xref} 0 R" in type0
    cid = objects[font.cid_xref]
    for part in ("/Subtype/CIDFontType2", "/CIDToGIDMap/Identity", "/DW 1000", "/W["):
        assert part in cid, part
    assert "/Ordering(Identity)" in cid
    fd = objects[font.descriptor_xref]
    for part in ("/Type/FontDescriptor", "/FontName/PDFEDT+Calibri", "/FontBBox[", "/Ascent "):
        assert part in fd, part
    assert f"/FontFile2 {font.file_xref} 0 R" in fd
    program = doc.xref_stream(font.file_xref)
    assert doc.xref_get_key(font.file_xref, "Length1") == ("int", str(len(program)))
    total = sum(len(doc.xref_stream_raw(x) or b"") + len(objects[x]) for x in objects)
    assert total < 15_000, total
    # glyph ids are those of the installed font, widths from its hmtx
    full_cmap = fontinfo.cmap(CALIBRI_PATH.read_bytes())
    widths = fontinfo.advance_widths(CALIBRI_PATH.read_bytes())
    upem = fontinfo.units_per_em(CALIBRI_PATH.read_bytes())
    assert font.present_gids == {full_cmap[ord(c)] for c in TEXT20}
    for c in "DZÉ":
        gid = full_cmap[ord(c)]
        assert font.widths[gid] == round(widths[gid] * 1000 / upem)
    present = fontinfo.nonempty_glyphs(program)
    assert present is not None and full_cmap[ord("D")] in present
    assert full_cmap[ord("q")] not in present  # not asked for
    # the resource, in the page's (indirect) /Resources /Font
    assert dict_refs(get_nested(doc, page.xref, "Resources/Font", resolve=True)[1])["PdfEd1"] == (
        font.xref
    )


@needs_text_fonts
def test_text_shows_and_extracts(tmp_path: Path) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "word.pdf"))
    page = doc[0]
    font = fontembed.ensure_font(doc, page.xref, CALIBRI_PATH, 0, "Durand")
    page = _show(doc, page, font.resource_name, fontembed.encode(font, "Durand"))
    assert _line_text(page) == [("Durand", "Calibri-PDFEditor")]
    reopened = pymupdf.open(stream=doc.tobytes(garbage=3, deflate=True))
    assert _line_text(reopened[0]) == [("Durand", "Calibri-PDFEditor")]
    assert LINE2.split()[-1] in reopened[0].get_text()  # other text untouched


@needs_text_fonts
def test_subset_renders_like_the_full_font(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    font = fontembed.ensure_font(doc, page.xref, CALIBRI_PATH, 0, "Durand")
    page = _show(doc, page, font.resource_name, fontembed.encode(font, "Durand"))
    ref = pymupdf.open()
    ref_page = ref.new_page(width=595, height=842)
    ref_page.insert_text(
        (ORIGIN[0], 842 - ORIGIN[1]),
        "Durand",
        fontsize=11,
        fontname="Cal",
        fontfile=str(CALIBRI_PATH),
    )
    clip = pymupdf.Rect(70, 228, 115, 246)
    a = page.get_pixmap(matrix=pymupdf.Matrix(4, 4), clip=clip)
    b = ref_page.get_pixmap(matrix=pymupdf.Matrix(4, 4), clip=clip)
    assert pixel_diff_bbox(a, b, threshold=3) is None
    assert a.samples != bytes(len(a.samples)) and min(a.samples) < 100  # something drawn


@needs_text_fonts
def test_extend_font_keeps_glyphs_and_is_fast(tmp_path: Path) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "word.pdf"))
    page = doc[0]
    font = fontembed.ensure_font(doc, page.xref, CALIBRI_PATH, 0, "Durand")
    assert font.missing("Zidane") == "Zie"
    with pytest.raises(KeyError):
        fontembed.encode(font, "Zidane")
    start = doc.xref_length()
    fonts: list[M7Font] = []
    best, times = best_time(
        lambda: fonts.append(fontembed.extend_font(doc, font, "Z")), EXTEND_BUDGET_S
    )
    assert best < EXTEND_BUDGET_S, times
    bigger = fonts[-1]
    assert doc.xref_length() == start  # same objects rewritten
    assert bigger.xref == font.xref and bigger.present_gids > font.present_gids
    assert len(bigger.present_gids) == len(font.present_gids) + 1
    program = doc.xref_stream(bigger.file_xref)
    present = fontinfo.nonempty_glyphs(program) or frozenset()
    cmap = fontinfo.cmap(CALIBRI_PATH.read_bytes())
    assert {cmap[ord(c)] for c in "DurandZ"} <= present
    # nothing missing: the same font back, nothing rewritten
    assert fontembed.extend_font(doc, bigger, "Zad") is bigger
    page = _show(doc, page, bigger.resource_name, fontembed.encode(bigger, "DZurand"))
    assert _line_text(page) == [("DZurand", "Calibri-PDFEditor")]
    # an alias character (no-break space for space) needs no new glyph
    with_space = fontembed.extend_font(doc, bigger, "a b")
    assert fontembed.extend_font(doc, with_space, "a b") is with_space
    assert fontembed.encode(with_space, "a b") == fontembed.encode(with_space, "a b")


@needs_text_fonts
def test_one_font_per_document_and_face(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    p1, p2 = doc.page_xref(0), doc.page_xref(1)
    a = fontembed.ensure_font(doc, p1, CALIBRI_PATH, 0, "abc")
    b = fontembed.ensure_font(doc, p2, CALIBRI_PATH, 0, "xyz")
    again = fontembed.ensure_font(doc, p1, CALIBRI_PATH, 0, "")
    assert a.xref == b.xref == again.xref
    assert a.resource_name == b.resource_name == again.resource_name == "PdfEd1"
    assert again.present_gids == b.present_gids  # the second page's glyphs are kept
    times = fontembed.ensure_font(doc, p1, TIMES_PATH, 0, "abc")
    assert times.xref != a.xref and times.resource_name == "PdfEd2"
    assert fontembed.registered_fonts(doc) == {"Calibri": a.xref, "TimesNewRomanPSMT": times.xref}


@needs_text_fonts
def test_resource_name_collision(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    set_nested(doc, page.xref, "Resources/Font/PdfEd1", "/Helvetica")  # not a font of ours
    font = fontembed.ensure_font(doc, page.xref, ARIAL_PATH, 0, "ab")
    assert font.resource_name == "PdfEd2"
    assert get_nested(doc, page.xref, "Resources/Font/PdfEd1") == ("name", "/Helvetica")


@needs_text_fonts
def test_registry_rebuilt_after_reload(tmp_path: Path) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "word.pdf"))
    font = fontembed.ensure_font(doc, doc[0].xref, CALIBRI_PATH, 0, "Durand")
    _show(doc, doc[0], font.resource_name, fontembed.encode(font, "Durand"))
    doc.new_page()
    out = tmp_path / "saved.pdf"
    doc.save(out, garbage=3, deflate=True)  # renumbers objects
    strict_read(out)
    reloaded = pymupdf.open(out)
    registry = fontembed.registered_fonts(reloaded)
    assert list(registry) == ["Calibri"]
    count = reloaded.xref_length()
    again = fontembed.ensure_font(reloaded, reloaded[1].xref, CALIBRI_PATH, 0, "Durandeau")
    assert again.xref == registry["Calibri"] and reloaded.xref_length() == count
    assert again.resource_name == "PdfEd1"
    assert again.present_gids > font.present_gids
    assert fontembed.encode(again, "Durand") == fontembed.encode(font, "Durand")
    # a second face gets the next number
    other = fontembed.ensure_font(reloaded, reloaded[1].xref, ARIAL_PATH, 0, "x")
    assert other.resource_name == "PdfEd2"
    # load_font reads the objects back
    loaded = fontembed.load_font(reloaded, again.xref)
    assert loaded.present_gids == again.present_gids and loaded.path is None
    with pytest.raises(FontEmbedError):
        fontembed.extend_font(reloaded, loaded, "Q")  # installed face unknown
    out2 = tmp_path / "saved2.pdf"
    reloaded.save(out2, garbage=3, deflate=True)
    strict_read(out2)


@needs_text_fonts
def test_pypdf_strict_and_incremental(tmp_path: Path) -> None:
    path = make_text_edit_pdf(tmp_path / "word.pdf")
    doc = pymupdf.open(path)
    font = fontembed.ensure_font(doc, doc[0].xref, CALIBRI_PATH, 0, "Zidane")
    _show(doc, doc[0], font.resource_name, fontembed.encode(font, "Zidane"))
    doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()
    reader = strict_read(path).reader
    fonts = reader.pages[0]["/Resources"]["/Font"]
    assert fonts["/PdfEd1"]["/BaseFont"] == "/PDFEDT+Calibri-PDFEditor"
    assert "Zidane" in reader.pages[0].extract_text()


@needs_text_fonts
def test_collection_face(tmp_path: Path) -> None:
    if not CAMBRIA_TTC_PATH.exists():
        pytest.skip("cambria.ttc missing")
    doc = pymupdf.open()
    page = doc.new_page()
    font = fontembed.ensure_font(doc, page.xref, CAMBRIA_TTC_PATH, 0, "Cambria")
    assert font.ps_name == "Cambria"
    program = doc.xref_stream(font.file_xref)
    assert fontinfo.face_count(program) == 1 and fontinfo.names(program).family == "Cambria"
    page = _show(doc, page, font.resource_name, fontembed.encode(font, "Cambria"))
    assert _line_text(page) == [("Cambria", "Cambria-PDFEditor")]


def _restricted_copy(tmp_path: Path) -> Path:
    data = bytearray(ARIAL_PATH.read_bytes())
    os2 = fontinfo.tables(bytes(data))["OS/2"][0]
    data[os2 + 8 : os2 + 10] = (2).to_bytes(2, "big")
    path = tmp_path / "restricted.ttf"
    path.write_bytes(bytes(data))
    return path


@needs_text_fonts
def test_refused_fonts(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    start = doc.xref_length()
    with pytest.raises(FontEmbedError, match="licence"):
        fontembed.ensure_font(doc, page.xref, _restricted_copy(tmp_path), 0, "abc")
    with pytest.raises(FontEmbedError):
        fontembed.ensure_font(doc, page.xref, tmp_path / "missing.ttf", 0, "abc")
    junk = tmp_path / "junk.ttf"
    junk.write_bytes(b"not a font at all" * 10)
    with pytest.raises(FontEmbedError):
        fontembed.ensure_font(doc, page.xref, junk, 0, "abc")
    # a character the face lacks: nothing created
    with pytest.raises(FontEmbedError, match="lacks"):
        fontembed.ensure_font(doc, page.xref, ARIAL_PATH, 0, "abc漢")
    assert doc.xref_length() == start
    font = fontembed.ensure_font(doc, page.xref, ARIAL_PATH, 0, "abc")
    with pytest.raises(FontEmbedError, match="lacks"):
        fontembed.extend_font(doc, font, "漢")
    with pytest.raises(FontEmbedError):
        fontembed.load_font(doc, page.xref)


def _cff_font(path: Path) -> Path:
    """A tiny CFF-outline OpenType font (fontTools' FontBuilder)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    def triangle():  # type: ignore[no-untyped-def]
        pen = T2CharStringPen(500, None)
        pen.moveTo((0, 0))
        pen.lineTo((500, 0))
        pen.lineTo((250, 700))
        pen.closePath()
        return pen.getCharString()

    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({0x41: "A"})
    fb.setupCFF("CffTest", {"FullName": "CffTest"}, {".notdef": triangle(), "A": triangle()}, {})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "A": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "CffTest", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))
    return path


def test_cff_font_is_refused(tmp_path: Path) -> None:
    otf = _cff_font(tmp_path / "cff.otf")
    assert "CFF " in fontinfo.tables(otf.read_bytes())
    doc = pymupdf.open()
    with pytest.raises(FontEmbedError, match="TrueType"):
        fontembed.ensure_font(doc, doc.new_page().xref, otf, 0, "A")


# -- encode -------------------------------------------------------------------------------
@needs_text_fonts
def test_encode_reused_fonts(tmp_path: Path) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "word.pdf"))
    page = doc[0]
    fonts = fontread.page_fonts(doc, page)
    calibri = next(f for f in fonts.values() if f.family == "Calibri")
    assert calibri.kind is FontKind.TYPE0
    hexstr = fontembed.encode(calibri, "courrier")
    assert re.fullmatch(rb"<([0-9A-F]{4}){8}>", hexstr)
    page = _show(doc, page, calibri.resource_name, hexstr)
    assert [t for t, _ in _line_text(page)] == ["courrier"]
    with pytest.raises(KeyError):
        fontembed.encode(calibri, "Z")
    assert fontembed.encode(calibri, "ab", codes=(1, 0x1234)) == b"<00011234>"

    simple_doc = pymupdf.open(make_simple_font_pdf(tmp_path / "simple.pdf"))
    simple = next(iter(fontread.page_fonts(simple_doc, simple_doc[0]).values()))
    assert simple.kind is FontKind.SIMPLE
    assert fontembed.encode(simple, "Jean à") == b"<4A65616E20E0>"
    with pytest.raises(KeyError):
        fontembed.encode(simple, "d")  # not in the subset
    with pytest.raises(KeyError):
        fontembed.encode(simple, "x", codes=(0x1FF,))
    other = fontread.read_embedded_font(simple_doc, 0)
    assert other.kind is FontKind.OTHER
    with pytest.raises(KeyError):
        fontembed.encode(other, "a")


def test_tounicode_and_w_sources() -> None:
    cmap = fontembed.tounicode_cmap({3: 0x20, 68: 0x44, 0x1F600 % 0x10000: 0x1F600})
    text = cmap.decode()
    assert "<0003> <0020>" in text and "<0044> <0044>" in text
    assert "<F600> <D83DDE00>" in text
    assert fontinfo.tounicode_map(cmap)[0x44] == 0x44
    many = fontembed.tounicode_cmap({g: 0x41 + g for g in range(250)})
    assert many.decode().count("beginbfchar") == 3
    assert fontembed.w_array({3: 226, 68: 479, 69: 525.5, 70: 1000}) == (
        "[3 [226] 68 [479 525.5 1000]]"
    )
    assert fontinfo.parse_w_array(fontembed.w_array({5: 100, 6: 200})) == {5: 100, 6: 200}
    assert fontembed.w_array({}) == "[]"


# -- packaging ----------------------------------------------------------------------------
def test_production_code_never_uses_mupdf_font_embedding() -> None:
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\.subset_fonts\(", text), path
        assert not re.search(r"\.insert_font\([^)]*fontfile", text), path


def test_fonttools_is_a_runtime_dependency() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    deps = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert '"fonttools>=4.50"' in deps
    spec = (ROOT / "pdfeditor.spec").read_text(encoding="utf-8")
    assert 'collect_submodules("fontTools.ttLib.tables")' in spec
    license_text = (SRC / "resources" / "licenses" / "MIT-fontTools.txt").read_text(
        encoding="utf-8"
    )
    assert "MIT License" in license_text and "Just van Rossum" in license_text
    notice = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
    assert "fontTools" in notice and "`MIT-fontTools.txt`" in notice
    # fontembed is the only module importing fontTools, and only core imports it
    importers = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if re.search(r"^\s*(from|import) fontTools", p.read_text(encoding="utf-8"), re.M)
    }
    assert importers == {"core/fontembed.py"}
