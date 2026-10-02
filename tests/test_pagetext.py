"""M6b-T9: page text layer (core/pagetext.py, PdfDocument.page_text)."""

from __future__ import annotations

import logging
import math
import threading

import fixtures
import pymupdf
import pytest
from fixtures import TEXT_DIAGONAL, TEXT_FREETEXT, TEXT_LINES, TEXT_RIGHT_COLUMN, TEXT_WIDGET_VALUE
from PySide6.QtCore import QPointF, QRectF
from timing import best_time

from pdfeditor.core import pagetext
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.pagetext import (
    EMPTY_PAGE_TEXT,
    HIT_TOLERANCE,
    CharRef,
    PageText,
    Quad,
    match_font,
)

#: Cold extraction of a 60 × 95 chars page (best of three runs, see the plan's budget).
EXTRACT_BUDGET_S = 0.050
#: 500 hits with an infinite tolerance on the dense page.
HITS_BUDGET_S = 1.0
ROTATIONS = (0, 90, 180, 270)
SCALE = 2.0


@pytest.fixture
def text_doc(tmp_path):
    doc = PdfDocument.open(str(fixtures.make_text_pdf(tmp_path / "text.pdf")))
    yield doc
    doc.close()


def _find(pt: PageText, needle: str, occurrence: int = 0) -> tuple[CharRef, CharRef]:
    text = "".join(ch.c for ch in pt.chars)
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(needle, start + 1)
    return pt.ref(start), pt.ref(start + len(needle) - 1)


def _center(pt: PageText, ref: CharRef) -> QPointF:
    return pt.char(ref).bbox.center()


def _dark_counter(pix: pymupdf.Pixmap):
    """Count of dark pixels (gray < 128) of a page-space rect of a grey pixmap."""
    table = bytes(1 if v < 128 else 0 for v in range(256))
    samples = pix.samples
    stride = pix.stride

    def count(r: QRectF) -> int:
        x0 = max(0, math.floor(r.left() * SCALE))
        x1 = min(pix.width, math.ceil(r.right() * SCALE))
        y0 = max(0, math.floor(r.top() * SCALE))
        y1 = min(pix.height, math.ceil(r.bottom() * SCALE))
        total = 0
        for y in range(y0, y1):
            total += samples[y * stride + x0 : y * stride + x1].translate(table).count(1)
        return total

    return count


# -- extraction on every rotation and cropbox --------------------------------------------------
@pytest.mark.parametrize("cropbox", [False, True])
@pytest.mark.parametrize("rotate", ROTATIONS)
def test_chars_match_dark_pixels(tmp_path, rotate, cropbox):
    path = fixtures.make_text_pdf(tmp_path / "t.pdf", rotate=rotate, cropbox=cropbox)
    doc = PdfDocument.open(str(path))
    pt = doc.page_text(0)
    texts = [ln.text for ln in pt.lines]
    assert texts == [t for t, _ in TEXT_LINES] + [TEXT_DIAGONAL]
    assert " " in texts[0]  # spaces are characters
    assert all(TEXT_FREETEXT not in t and TEXT_WIDGET_VALUE not in t for t in texts)
    size = doc.page_size(0)
    doc.close()
    raw = pymupdf.open(path)
    page = raw[0]
    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(SCALE, SCALE), colorspace=pymupdf.csGRAY, annots=False
    )
    assert (pix.width, pix.height) == (
        math.ceil(size.width() * SCALE),
        math.ceil(size.height() * SCALE),
    )
    count = _dark_counter(pix)
    for i, ch in enumerate(pt.chars):
        if not ch.c.isspace():
            assert count(ch.bbox) > 0, (rotate, cropbox, i, ch.c)
    # Every dark pixel of the page lies on a line (FreeText and widget not rendered).
    total = count(QRectF(0, 0, size.width(), size.height()))
    on_lines = sum(count(ln.bbox.adjusted(-1, -1, 1, 1)) for ln in pt.lines)
    assert total > 1000
    assert on_lines == pytest.approx(total, rel=0.01)
    raw.close()


def test_line_directions_follow_rotation(tmp_path):
    expected = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}
    for rotate, d in expected.items():
        doc = PdfDocument.open(
            str(fixtures.make_text_pdf(tmp_path / f"r{rotate}.pdf", rotate=rotate))
        )
        line = doc.page_text(0).lines[0]
        assert line.dir == pytest.approx(d, abs=1e-6)
        doc.close()


def test_content_order_kept(tmp_path):
    doc = PdfDocument.open(str(fixtures.make_text_pdf(tmp_path / "c.pdf", two_columns=True)))
    texts = [ln.text for ln in doc.page_text(0).lines]
    assert texts[0] == TEXT_RIGHT_COLUMN[0]
    assert texts[1] == TEXT_LINES[0][0]
    doc.close()


def test_spans_carry_font_info(text_doc):
    span = text_doc.page_text(0).lines[0].spans[0]
    assert span.font == "Helvetica"
    assert span.size == pytest.approx(12.0)
    assert span.font_xref > 0 and span.resource_name
    assert span.ascender > 0 > span.descender
    assert not span.invisible
    with text_doc.lock:
        fonts = text_doc.fitz[0].get_fonts(full=True)
    assert (span.font_xref, span.resource_name) in {(f[0], f[4]) for f in fonts}


# -- queries -----------------------------------------------------------------------------------
def test_hit_inside_near_above_and_far(text_doc):
    pt = text_doc.page_text(0)
    first, last = pt.line_range(pt.ref(0))
    line0 = pt.lines[0]
    q = pt.char(pt.ref(4))  # "q" of "quick"
    assert pt.hit(q.bbox.center()) == pt.ref(4)
    # Above the line (within the tolerance) still hits the char below.
    above = QPointF(q.bbox.center().x(), line0.bbox.top() - HIT_TOLERANCE / 2)
    assert pt.hit(above) == pt.ref(4)
    # Past the end of the line: its last char.
    near = QPointF(line0.bbox.right() + 3, line0.bbox.center().y())
    assert pt.hit(near) == last
    before = QPointF(line0.bbox.left() - 3, line0.bbox.center().y())
    assert pt.hit(before) == first
    far = QPointF(line0.bbox.right() + 200, line0.bbox.top() - 60)
    assert pt.hit(far) is None
    nearest = pt.hit(far, tolerance=math.inf)
    assert nearest is not None and nearest.line == 0
    # Between two lines: the closer one.
    l1 = pt.lines[1]
    mid_closer_to_1 = QPointF(q.bbox.center().x(), l1.bbox.top() - 0.5)
    assert pt.hit(mid_closer_to_1).line == 1


def test_word_line_and_span_at(text_doc):
    pt = text_doc.page_text(0)
    a, b = _find(pt, "quick")
    inside = _center(pt, CharRef(a.index + 2, a.line))
    assert pt.word_at(inside) == (a, b)
    assert pt.text_of(pt.chars_between(*pt.word_at(inside))) == "quick"
    assert pt.word_range(a.index - 1) == (pt.ref(a.index - 1), pt.ref(a.index - 1))  # space
    dog_a, dog_b = _find(pt, "dog.")
    assert pt.word_range(dog_a) == (dog_a, dog_b)  # punctuation sticks to the word
    assert pt.line_at(inside) is pt.lines[0]
    assert pt.line_at(QPointF(5, 5)) is None
    assert pt.text_of(pt.chars_between(*pt.line_range(a))) == TEXT_LINES[0][0]
    span = pt.span_at(inside)
    assert span is pt.lines[0].spans[0]
    assert pt.span_at(QPointF(pt.lines[0].bbox.right() + 2, inside.y())) is None
    assert pt.word_at(QPointF(5, 5)) is None


def test_range_quads_two_lines(text_doc):
    pt = text_doc.page_text(0)
    a, _ = _find(pt, "brown")
    _, b = _find(pt, "line")
    quads = pt.range_quads(b, a)  # either order
    assert len(quads) == 2
    r0, r1 = (q.bounding_rect() for q in quads)
    ca, cb = pt.char(a).bbox, pt.char(b).bbox
    assert r0.left() == pytest.approx(ca.left())
    assert r0.right() == pytest.approx(pt.lines[0].bbox.right())
    assert r1.left() == pytest.approx(pt.lines[1].bbox.left())
    assert r1.right() == pytest.approx(cb.right())
    assert r0.top() == pytest.approx(ca.top()) and r1.bottom() == pytest.approx(cb.bottom())
    assert pt.range_quads(a, a)[0].bounding_rect() == ca
    text = pt.text_of(pt.chars_between(a, b))
    assert text == "brown fox jumps over the lazy dog.\nSecond line"
    assert pt.text_of([b, a, a]) == "b\ne"  # sorted, unique, newline between lines


def test_chars_in_rect_and_quad(text_doc):
    pt = text_doc.page_text(0)
    a, b = _find(pt, "quick")
    quad = pt.range_quads(a, b)[0]
    assert pt.text_of(pt.chars_in_rect(quad.bounding_rect())) == "quick"
    assert pt.text_of(pt.chars_in_rect(quad)) == "quick"
    assert pt.chars_in_rect(QRectF(0, 0, 10, 10)) == []


def test_diagonal_quad_not_rectangular(tmp_path):
    for rotate in (0, 90):
        path = fixtures.make_text_pdf(tmp_path / f"d{rotate}.pdf", rotate=rotate, cropbox=True)
        doc = PdfDocument.open(str(path))
        pt = doc.page_text(0)
        a, b = pt.line_range(pt.ref(-1))
        assert pt.text_of(pt.chars_between(a, b)) == TEXT_DIAGONAL
        (quad,) = pt.range_quads(a, b)
        assert abs(quad.ul.y() - quad.ur.y()) > 10 and abs(quad.ul.x() - quad.ur.x()) > 10
        raw = pymupdf.open(path)
        dl = raw[0].get_displaylist(annots=False)
        rd = pymupdf.TextPage(dl.get_textpage(pymupdf.TEXTFLAGS_RAWDICT)).extractRAWDICT()
        raw.close()
        (line,) = [
            ln
            for blk in rd["blocks"]
            for ln in blk.get("lines", ())
            if "".join(c["c"] for sp in ln["spans"] for c in sp["chars"]) == TEXT_DIAGONAL
        ]
        s0, s1 = line["spans"][0], line["spans"][-1]
        q0 = pymupdf.utils.recover_char_quad(line["dir"], s0, s0["chars"][0])
        q1 = pymupdf.utils.recover_char_quad(line["dir"], s1, s1["chars"][-1])
        expected = (q0.ul, q1.ur, q0.ll, q1.lr)
        for got, exp in zip(quad, expected, strict=True):
            assert (got.x(), got.y()) == pytest.approx((exp.x, exp.y), abs=0.01)
        # Hit along the slanted line: the centre of each char finds that char.
        for ref in pt.chars_between(a, b):
            assert pt.hit(_center(pt, ref)) == ref
        doc.close()


def test_hit_on_rotated_pages(tmp_path):
    for rotate in ROTATIONS:
        path = fixtures.make_text_pdf(tmp_path / f"h{rotate}.pdf", rotate=rotate, cropbox=True)
        doc = PdfDocument.open(str(path))
        pt = doc.page_text(0)
        for ref in pt.chars_between(*pt.line_range(pt.ref(0))):
            assert pt.hit(_center(pt, ref)) == ref, (rotate, ref)
        a, b = _find(pt, "quick")
        (quad,) = pt.range_quads(a, b)
        assert pt.text_of(pt.chars_in_rect(quad)) == "quick"
        doc.close()


# -- hand-made dicts, invisible text, scans ----------------------------------------------------
def test_from_rawdict_hand_made():
    raw = {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {
                                "chars": [
                                    {"c": "H", "bbox": (10, 10, 18, 22)},
                                    {"c": "i", "bbox": (18, 10, 22, 22)},
                                ]
                            }
                        ]
                    },
                    {"spans": [{"chars": []}]},  # empty line dropped
                    {"spans": [{"chars": [{"c": "!", "bbox": (10, 30, 14, 42)}]}]},
                ]
            },
            {"type": 1, "bbox": (0, 100, 50, 150)},
        ]
    }
    pt = PageText.from_rawdict(raw)
    assert [ln.text for ln in pt.lines] == ["Hi", "!"]
    assert pt.text == "Hi\n!"
    assert pt.lines[0].dir == (1.0, 0.0)
    assert pt.lines[0].bbox == QRectF(10, 10, 12, 12)
    assert pt.chars[0].origin == QPointF(10, 22)
    span = pt.lines[0].spans[0]
    assert (span.font, span.font_xref, span.resource_name) == ("", 0, "")
    assert pt.image_rects == (QRectF(0, 100, 50, 50),)
    assert pt.hit(QPointF(19, 16)) == CharRef(1, 0)
    assert pt.range_quads(0, 2)[1] == Quad.from_rect(QRectF(10, 30, 4, 12))
    assert not pt.is_invisible(0)


def test_invisible_text_selectable(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Visible", fontsize=12)
    page.insert_text((72, 140), "OCR layer", fontsize=12, render_mode=3)
    page.set_rotation(90)
    path = tmp_path / "ocr.pdf"
    doc.save(path)
    doc.close()
    pdoc = PdfDocument.open(str(path))
    pt = pdoc.page_text(0)
    assert [ln.text for ln in pt.lines] == ["Visible", "OCR layer"]
    a, b = pt.line_range(pt.ref(-1))
    assert all(pt.is_invisible(r) for r in pt.chars_between(a, b))
    assert not any(pt.is_invisible(r) for r in pt.chars_between(*pt.line_range(pt.ref(0))))
    assert pt.lines[1].spans[0].invisible
    # bboxlog areas mapped to page space cover the invisible line.
    assert len(pt.invisible_rects) == 1
    assert all(pt.invisible_rects[0].contains(_center(pt, r)) for r in pt.chars_between(a, b))
    assert pt.hit(_center(pt, a)) == a
    # Only the rects: a span without alpha still reads as invisible there.
    bare = PageText(pt.blocks, pt.invisible_rects)
    plain = PageText.from_rawdict(
        {
            "blocks": [
                {"lines": [{"spans": [{"chars": [{"c": "x", "bbox": _box(pt.char(a).bbox)}]}]}]}
            ]
        },
        bboxlog=[("ignore-text", _box(pt.invisible_rects[0]))],
    )
    assert bare.is_invisible(a) and plain.is_invisible(0)
    pdoc.close()


def _box(r: QRectF) -> tuple[float, float, float, float]:
    return (r.left(), r.top(), r.right(), r.bottom())


def test_scanned_page_has_no_text(tmp_path):
    doc = PdfDocument.open(str(fixtures.make_scanned_pdf(tmp_path / "scan.pdf")))
    pt = doc.page_text(0)
    assert pt.is_empty and pt.chars == () and pt.lines == ()
    assert pt.text == ""
    assert pt.hit(QPointF(100, 100), tolerance=math.inf) is None
    assert pt.word_at(QPointF(100, 100)) is None
    assert pt.range_quads(0, 0) == []
    assert len(pt.image_rects) == 1
    size = doc.page_size(0)
    assert pt.image_rects[0] == QRectF(0, 0, size.width(), size.height())
    assert EMPTY_PAGE_TEXT.is_empty and EMPTY_PAGE_TEXT.text_of([]) == ""
    doc.close()


# -- font join -----------------------------------------------------------------------------------
def test_match_font_rules():
    fonts = [
        (10, "ttf", "TrueType", "ABCDEF+Arial#20Regular", "F1", "WinAnsiEncoding", 0),
        (11, "n/a", "Type1", "Helvetica", "helv", "WinAnsiEncoding", 0),
        (12, "cff", "Type1C", "Times-Bold", "F3", "", 0),
        (13, "cff", "Type1C", "Times-Bold", "F4", "", 0),
    ]
    assert match_font("Helvetica", fonts) == (11, "helv")
    assert match_font("Arial Regular", fonts) == (10, "F1")  # subset prefix, #20 decoded
    assert match_font("ArialMT", fonts) == (10, "F1")  # PostScript name vs BaseFont
    assert match_font("Times-Bold", fonts) == (12, "F3")  # exact: first entry
    assert match_font("TimesBold", fonts) == (0, "")  # loose and ambiguous
    assert match_font("Courier", fonts) == (0, "")
    assert match_font("", fonts) == (0, "")


@pytest.mark.skipif(not fixtures.ARIAL_PATH.exists(), reason="Arial not installed")
def test_embedded_truetype_span_joined(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Helvetica text", fontsize=12)
    page.insert_text(
        (72, 140), "Arial text", fontsize=12, fontname="F1", fontfile=str(fixtures.ARIAL_PATH)
    )
    path = tmp_path / "fonts.pdf"
    doc.save(path)
    doc.close()
    pdoc = PdfDocument.open(str(path))
    spans = [ln.spans[0] for ln in pdoc.page_text(0).lines]
    with pdoc.lock:
        fonts = {f[0]: f for f in pdoc.fitz[0].get_fonts(full=True)}
    assert len({s.font_xref for s in spans}) == 2
    for s in spans:
        assert s.font_xref in fonts and fonts[s.font_xref][4] == s.resource_name
    assert spans[1].resource_name == "F1"
    pdoc.close()


# -- PdfDocument cache --------------------------------------------------------------------------
def test_page_text_cache(text_doc, tmp_path):
    first = text_doc.page_text(0)
    assert text_doc.page_text(0) is first
    text_doc.page_changed.emit(0)
    second = text_doc.page_text(0)
    assert second is not first and second.text == first.text
    text_doc.structure_changed.emit()
    third = text_doc.page_text(0)
    assert third is not second
    text_doc.save_as(tmp_path / "saved.pdf")  # reloaded
    assert text_doc.page_text(0) is not third
    with pytest.raises(IndexError):
        text_doc.page_text(3)


def test_page_text_follows_structure_changes(text_doc):
    text = text_doc.page_text(0).text
    text_doc.insert_blank_page(0, text_doc.page_size(0))
    assert text_doc.page_text(0).is_empty
    assert text_doc.page_text(1).text == text
    text_doc.delete_pages([0])
    assert text_doc.page_text(0).text == text


def test_page_text_dropped_on_close(text_doc):
    text_doc.page_text(0)
    text_doc.close()
    assert text_doc._text_cache == {}


def test_page_text_hit_is_lock_free(text_doc):
    text_doc.page_text(0)
    held = threading.Event()
    release = threading.Event()

    def hold():
        with text_doc.lock:
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(5)
        result: list[PageText] = []
        reader = threading.Thread(target=lambda: result.append(text_doc.page_text(0)))
        reader.start()
        reader.join(2)
        assert result, "page_text waited for the document lock on a cache hit"
    finally:
        release.set()
        holder.join()


def test_page_text_failure_gives_empty_text(text_doc, monkeypatch, caplog):
    def boom(page):
        raise RuntimeError("no text")

    monkeypatch.setattr(pagetext, "extract_page_text", boom)
    with caplog.at_level(logging.WARNING):
        assert text_doc.page_text(0) is EMPTY_PAGE_TEXT
    assert "could not extract the text of page 0" in caplog.text


# -- performance --------------------------------------------------------------------------------
def test_dense_page_extraction_budget(tmp_path):
    raw = pymupdf.open()
    page = raw.new_page()
    for i in range(60):
        page.insert_text((40, 40 + i * 12), "x" * 95, fontsize=9)
    path = tmp_path / "dense.pdf"
    raw.save(path)
    raw.close()
    doc = PdfDocument.open(str(path))
    best, runs = best_time(
        lambda: doc.page_text(0), EXTRACT_BUDGET_S, setup=lambda: doc.page_changed.emit(0)
    )
    assert best < EXTRACT_BUDGET_S, runs
    pt = doc.page_text(0)
    assert len(pt.chars) == 60 * 95

    def hits() -> None:
        for i in range(500):
            pt.hit(QPointF(40 + i, 40 + i % 700), tolerance=math.inf)

    best, runs = best_time(hits, HITS_BUDGET_S)
    assert best < HITS_BUDGET_S, runs
    doc.close()
