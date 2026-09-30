"""M3-T3: snapping to cells, underlines and checkboxes (core/snapping.py, page_shapes)."""

from __future__ import annotations

import time

import fixtures
import pymupdf
import pytest
from fixtures import (
    A4,
    PRINT_LINE,
    SYMBOL_FONTS_AVAILABLE,
    WORD_CROPBOX,
    WORD_GLYPH_SIZE,
    WORD_GLYPHS,
    WORD_SHAPES,
    WORD_TABLE_X,
    WORD_TABLE_Y,
)
from PySide6.QtCore import QPointF, QRectF

from pdfeditor.core import snapping
from pdfeditor.core.annotations import BASELINE_RATIO, AnnotKind, AnnotSpec
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.snapping import (
    GLYPH_BOX_RATIO,
    PageShapes,
    Segment,
    Snap,
    SnapKind,
    is_checkbox_size,
    snap,
    stamp_placement,
    text_placement,
)

#: Timing bounds (the plan's budgets: scan < 100 ms, 1000 snaps < 200 ms); each is the
#: best of a few runs so that a busy machine does not fail the suite.
SCAN_BUDGET = 0.100
SNAP_BUDGET = 0.200
FS = 11.0
PAGE_W = A4[0]
#: pymupdf.open().new_page() default size.
A4_LETTER_W, A4_LETTER_H = 595.0, 842.0

needs_symbol_fonts = pytest.mark.skipif(
    not SYMBOL_FONTS_AVAILABLE, reason="MS Gothic / Segoe UI Symbol not installed"
)


@pytest.fixture
def word_doc(word_form_pdf):
    doc = PdfDocument.open(str(word_form_pdf))
    yield doc
    doc.close()


@pytest.fixture
def word_shapes(word_doc) -> PageShapes:
    return word_doc.page_shapes(0)


def _rect(r: QRectF) -> tuple[float, float, float, float]:
    return (r.left(), r.top(), r.right(), r.bottom())


def _approx(r: QRectF, expected, abs_=0.01):
    assert _rect(r) == pytest.approx(tuple(expected), abs=abs_)


def _at(shapes: PageShapes, x: float, y: float) -> Snap:
    return snap(shapes, QPointF(x, y))


def _baseline(rect: QRectF, fs: float = FS) -> float:
    return rect.top() + BASELINE_RATIO * fs


def _same_boxes(got, expected, abs_=1e-6) -> None:
    got, expected = sorted(got), sorted(expected)
    assert len(got) == len(expected), (got, expected)
    for g, e in zip(got, expected, strict=True):
        assert g == pytest.approx(tuple(e), abs=abs_), (got, expected)


def _stroked_edges(box):
    x0, y0, x1, y1 = box
    return {Segment(x0, x1, y0), Segment(x0, x1, y1)}, {Segment(y0, y1, x0), Segment(y0, y1, x1)}


# -- scanning ---------------------------------------------------------------------------
def test_word_form_shapes(word_shapes):
    s = word_shapes
    closed = ("checkbox_10", "checkbox_12", "box_lines", "area", "band")
    assert set(s.boxes) == {WORD_SHAPES[k] for k in closed}
    h, v = set(), set()
    for k in ("checkbox_10", "checkbox_12", "box_lines", "area"):
        eh, ev = _stroked_edges(WORD_SHAPES[k])
        h |= eh
        v |= ev
    tx0, ty0, tx1, ty1 = WORD_SHAPES["table"]
    h |= {Segment(tx0 - 0.25, tx1 + 0.25, y) for y in WORD_TABLE_Y}
    v |= {Segment(ty0 - 0.25, ty1 + 0.25, x) for x in WORD_TABLE_X}
    h.add(Segment(110.0, 300.0, 74.25))  # thin filled rect -> its centre line
    h.add(Segment(110.0, 300.0, 100.0))  # stroked line (drawn there and back: one rule)
    assert set(s.h_segments) == h
    assert set(s.v_segments) == v
    assert (len(s.boxes), len(s.h_segments), len(s.v_segments)) == (5, 13, 12)
    assert list(s.h_segments) == sorted(s.h_segments)
    assert len(s.glyph_boxes) == (2 if SYMBOL_FONTS_AVAILABLE else 0)


def test_print_pdf_has_only_the_line(print_pdf):
    doc = PdfDocument.open(str(print_pdf))
    s = doc.page_shapes(0)
    x0, y, x1, _ = PRINT_LINE
    assert s.boxes == () and s.v_segments == () and s.glyph_boxes == ()
    assert s.h_segments == (Segment(x0, x1, y),)
    for x, yy in ((100, 72), (100, 100), (300, 300), (500, 130), (80, 160)):
        assert _at(s, x, yy).kind is SnapKind.NONE
    hit = _at(s, 200, y - 5)
    assert hit.kind is SnapKind.UNDERLINE
    _approx(hit.rect, (x0, y, x1, y))
    doc.close()


def test_white_fills_and_slanted_quads_are_ignored(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 150, 150), color=None, fill=(1, 1, 1))
    page.draw_quad(pymupdf.Quad((200, 50), (260, 60), (190, 110), (250, 120)), color=(0, 0, 0))
    page.draw_rect(pymupdf.Rect(20, 20, 592, 772), color=(0, 0, 0))  # page frame
    page.draw_rect(pymupdf.Rect(20, 20, 592, 200), color=(0, 0, 0))  # section: kept
    s = snapping.scan_page(page)
    assert s.boxes == ((20, 20, 592, 200),)
    assert {seg.position for seg in s.h_segments} == {20, 200}
    doc.close()


def test_scan_ignores_annotation_text(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.add_freetext_annot(pymupdf.Rect(100, 100, 200, 130), "☐ ☐", fontsize=12)
    s = snapping.scan_page(page)
    assert s == PageShapes(page_size=(A4_LETTER_W, A4_LETTER_H))
    doc.close()


def _char(c, bbox, origin):
    return {"c": c, "bbox": bbox, "origin": origin}


def _square(cx, cy, size):
    half = GLYPH_BOX_RATIO * size / 2
    return (cx - half, cy - half, cx + half, cy + half)


def test_glyph_boxes_from_rawdict():
    gothic = _char("☐", (72, 490, 84, 502), (72, 500))
    wing_square = _char("\x00", (100, 100, 108.9, 111), (100, 109))
    wing_narrow = _char("\x00", (120, 100, 124, 111), (120, 109))
    upright = [
        {"font": "MS-Gothic", "size": 12.0, "chars": [gothic]},
        {"font": "Wingdings-Regular", "size": 10.0, "chars": [wing_square, wing_narrow]},
        {
            "font": "Helvetica",
            "size": 11.0,
            "chars": [_char("o", (150, 100, 156, 111), (150, 109))],
        },
        {"font": "Webdings", "size": 0.0, "chars": [_char("\x00", (0, 0, 10, 10), (0, 8))]},
    ]
    # A line written downwards (page rotated by 90°): "up" is +x.
    down = [
        {
            "font": "SegoeUISymbol",
            "size": 10.0,
            "chars": [_char("☐", (300, 50, 312, 60), (300, 50))],
        }
    ]
    raw = {
        "blocks": [
            {"lines": [{"dir": (1.0, 0.0), "spans": upright}, {"dir": (0.0, 1.0), "spans": down}]},
            {"type": 1},  # image block
        ]
    }
    expected = [_square(78, 500 - 4.2, 12), _square(104.45, 109 - 3.5, 10), _square(303.5, 55, 10)]
    _same_boxes(snapping.glyph_boxes(raw), expected, 0.01)


# -- snap -------------------------------------------------------------------------------
def test_click_in_table_cell(word_shapes):
    hit = _at(word_shapes, 120, 165)
    assert hit.kind is SnapKind.CELL
    _approx(hit.rect, (72, 150, 172, 180))
    # 30 pt >= 2.4 x 11: first baseline near the top of the cell.
    r = text_placement(hit, QPointF(120, 165), FS, 180, PAGE_W)
    assert (r.left(), r.width()) == pytest.approx((74, 96))
    assert _baseline(r) == pytest.approx(150 + 2 + BASELINE_RATIO * FS)
    # 30 pt < 2.4 x 14: baseline 2 pt above the bottom border.
    r = text_placement(hit, QPointF(120, 165), 14.0, 180, PAGE_W)
    assert _baseline(r, 14.0) == pytest.approx(178)
    # Every cell of the grid.
    for col in range(3):
        for row in range(2):
            x0, x1 = WORD_TABLE_X[col], WORD_TABLE_X[col + 1]
            y0, y1 = WORD_TABLE_Y[row], WORD_TABLE_Y[row + 1]
            hit = _at(word_shapes, (x0 + x1) / 2, (y0 + y1) / 2)
            assert hit.kind is SnapKind.CELL
            _approx(hit.rect, (x0, y0, x1, y1))


def test_click_above_underlines(word_shapes):
    ux0, uy0, ux1, uy1 = WORD_SHAPES["underline_rect"]
    rule = (uy0 + uy1) / 2
    hit = _at(word_shapes, 150, uy0 - 5)
    assert hit.kind is SnapKind.UNDERLINE
    _approx(hit.rect, (ux0, rule, ux1, rule))
    r = text_placement(hit, QPointF(150, uy0 - 5), FS, 180, PAGE_W)
    assert _baseline(r) == pytest.approx(72, abs=0.3)
    assert (r.left(), r.width()) == pytest.approx((ux0 + 2, 180))
    r = text_placement(hit, QPointF(150, uy0 - 5), FS, 250, PAGE_W)
    assert r.right() == pytest.approx(ux1)  # clamped to the end of the rule
    # Stroked line underline; clicking on it or 3 pt below still picks it.
    lx0, ly, lx1, _ = WORD_SHAPES["underline_line"]
    for dy in (-12, 0, 3):
        hit = _at(word_shapes, 200, ly + dy)
        assert hit.kind is SnapKind.UNDERLINE
        _approx(hit.rect, (lx0, ly, lx1, ly))
    # 5.75 pt below the first rule: too far below it, the next one (21 pt down) wins.
    hit = _at(word_shapes, 150, 80)
    assert hit.rect.top() == pytest.approx(ly)
    # Outside the rule's x range: nothing.
    assert _at(word_shapes, 320, uy0 - 5).kind is SnapKind.NONE


@pytest.mark.parametrize("key", ["checkbox_10", "checkbox_12", "box_lines"])
def test_click_in_checkbox(word_shapes, key):
    x0, y0, x1, y1 = WORD_SHAPES[key]
    centre = QPointF((x0 + x1) / 2, (y0 + y1) / 2)
    for click in (centre, QPointF(x0 + 1, y1 - 1), QPointF(x1 + 4, centre.y())):
        hit = snap(word_shapes, click)
        assert hit.kind is SnapKind.BOX
        _approx(hit.rect, WORD_SHAPES[key])
        c, size = stamp_placement(hit, click, 12.0)
        assert (c.x(), c.y(), size) == pytest.approx((centre.x(), centre.y(), x1 - x0))


def test_click_between_boxes_is_not_a_cell(word_shapes):
    # Rays from here hit the table, the big area and two checkbox edges: no cell.
    assert _at(word_shapes, 100, 255).kind is SnapKind.NONE


@needs_symbol_fonts
@pytest.mark.parametrize("font", sorted(WORD_GLYPHS))
def test_click_on_glyph_checkbox(word_form_pdf, word_shapes, font):
    ox, oy = WORD_GLYPHS[font]
    fitz = pymupdf.open(word_form_pdf)
    page = fitz[0]
    clip = pymupdf.Rect(ox - 4, oy - 16, ox + 16, oy + 6)
    zoom = 8
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, colorspace=pymupdf.csGRAY)
    dark = [
        (x, y)
        for y in range(pix.height)
        for x in range(pix.width)
        if pix.samples[y * pix.stride + x] < 128
    ]
    xs, ys = [p[0] for p in dark], [p[1] for p in dark]
    rendered = QRectF(
        clip.x0 + min(xs) / zoom,
        clip.y0 + min(ys) / zoom,
        (max(xs) + 1 - min(xs)) / zoom,
        (max(ys) + 1 - min(ys)) / zoom,
    )
    fitz.close()
    hit = snap(word_shapes, rendered.center())
    assert hit.kind is SnapKind.BOX
    side = GLYPH_BOX_RATIO * WORD_GLYPH_SIZE  # 8.64 pt
    assert (hit.rect.width(), hit.rect.height()) == pytest.approx((side, side))
    # The box is the visible square (within 0.5 pt).
    for got, want in zip(_rect(hit.rect), _rect(rendered), strict=True):
        assert got == pytest.approx(want, abs=0.5)
    c, size = stamp_placement(hit, rendered.center(), 12.0)
    assert size == pytest.approx(side)
    assert (c.x(), c.y()) == pytest.approx((rendered.center().x(), rendered.center().y()), abs=0.5)


def test_click_in_large_area_is_top_aligned_cell(word_shapes):
    hit = _at(word_shapes, 200, 350)
    assert hit.kind is SnapKind.CELL
    _approx(hit.rect, WORD_SHAPES["area"])
    r = text_placement(hit, QPointF(200, 350), FS, 180, PAGE_W)
    assert (r.left(), r.width()) == pytest.approx((74, 424))
    assert _baseline(r) == pytest.approx(302 + BASELINE_RATIO * FS)
    c, size = stamp_placement(hit, QPointF(200, 350), 12.0)
    assert (c.x(), c.y(), size) == pytest.approx((200, 350, 12))  # not a checkbox: at click


def test_click_in_grey_band(word_shapes):
    hit = _at(word_shapes, 150, 430)
    assert hit.kind is SnapKind.CELL
    _approx(hit.rect, WORD_SHAPES["band"])
    r = text_placement(hit, QPointF(150, 430), FS, 180, PAGE_W)
    assert _baseline(r) == pytest.approx(438)  # 20 pt < 2.4 x 11: bottom-aligned


def test_click_on_empty_page(word_shapes):
    hit = _at(word_shapes, 300, 700)
    assert hit == Snap(SnapKind.NONE)
    r = text_placement(hit, QPointF(300, 700), FS, 180, PAGE_W)
    assert (r.left(), r.width()) == pytest.approx((300, 180))
    assert _baseline(r) == pytest.approx(700 + snapping.CLICK_BASELINE_RATIO * FS)
    r = text_placement(hit, QPointF(500, 700), FS, 180, PAGE_W)
    assert (r.left(), r.right()) == pytest.approx((500, PAGE_W))  # clamped to the page
    r = text_placement(hit, QPointF(590, 700), FS, 180, PAGE_W)
    assert r.right() == pytest.approx(PAGE_W) and r.width() == pytest.approx(36)
    c, size = stamp_placement(hit, QPointF(300, 700), 14.0)
    assert (c.x(), c.y(), size) == (300, 700, 14.0)


def test_is_checkbox_size_and_small_cell_stamp():
    assert is_checkbox_size(QRectF(0, 0, 12, 12))
    assert is_checkbox_size(QRectF(0, 0, 28, 18))
    assert not is_checkbox_size(QRectF(0, 0, 29, 29))
    assert not is_checkbox_size(QRectF(0, 0, 20, 10))
    assert not is_checkbox_size(QRectF(0, 0, 0, 0))
    cell = Snap(SnapKind.CELL, QRectF(10, 10, 16, 14))
    c, size = stamp_placement(cell, QPointF(12, 12), 12.0)
    assert (c.x(), c.y(), size) == pytest.approx((18, 17, 14))


def test_table_drawn_with_segments_makes_checkbox_cells():
    # A 14 x 14 square built from rules only is reported as a BOX.
    h = (Segment(0, 100, 0), Segment(0, 100, 14))
    v = (Segment(0, 14, 0), Segment(0, 14, 14), Segment(0, 14, 100))
    shapes = PageShapes(h_segments=h, v_segments=v)
    hit = _at(shapes, 7, 7)
    assert hit.kind is SnapKind.BOX
    _approx(hit.rect, (0, 0, 14, 14))
    hit = _at(shapes, 50, 7)
    assert hit.kind is SnapKind.CELL
    _approx(hit.rect, (14, 0, 100, 14))


# -- rotation and cropbox -----------------------------------------------------------------
def _mapped(box, matrix) -> tuple[float, ...]:
    return tuple((pymupdf.Rect(box) * matrix).normalize())


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_rotated_page_same_shapes_in_page_space(tmp_path, word_shapes, rotate):
    path = fixtures.make_word_form_pdf(tmp_path / f"r{rotate}.pdf", rotate=rotate)
    doc = PdfDocument.open(str(path))
    with doc.lock:
        matrix = doc.fitz[0].rotation_matrix
    s = doc.page_shapes(0)
    _same_boxes(s.boxes, [_mapped(b, matrix) for b in word_shapes.boxes])
    _same_boxes(s.glyph_boxes, [_mapped(b, matrix) for b in word_shapes.glyph_boxes], 0.02)
    clicks = {
        "cell": ((120, 165), (72, 150, 172, 180), SnapKind.CELL),
        "box": ((126, 254), WORD_SHAPES["checkbox_12"], SnapKind.BOX),
        "lines": ((206, 256), WORD_SHAPES["box_lines"], SnapKind.BOX),
        "area": ((200, 350), WORD_SHAPES["area"], SnapKind.CELL),
        "band": ((150, 430), WORD_SHAPES["band"], SnapKind.CELL),
    }
    for (x, y), box, kind in clicks.values():
        p = pymupdf.Point(x, y) * matrix
        hit = snap(s, QPointF(p.x, p.y))
        assert hit.kind is kind
        _approx(hit.rect, _mapped(box, matrix))
    if rotate == 180:  # rules stay horizontal: underline found above the rule on screen
        p = pymupdf.Point(150, 69) * matrix
        rule = pymupdf.Point(150, 74.25) * matrix
        hit = snap(s, QPointF(p.x, rule.y - 5))
        assert hit.kind is SnapKind.UNDERLINE
        assert hit.rect.top() == pytest.approx(rule.y)
    doc.close()


def test_rotated_fixture(word_form_rotated_pdf):
    doc = PdfDocument.open(str(word_form_rotated_pdf))
    assert doc.page_rotation(0) == 90
    s = doc.page_shapes(0)
    # Unrotated (72,150)-(172,180) on a 595 x 842 page shown at 90°: x' = 842 - y, y' = x.
    hit = _at(s, 842 - 165, 120)
    assert hit.kind is SnapKind.CELL
    _approx(hit.rect, (842 - 180, 72, 842 - 150, 172))
    doc.close()


def test_cropbox_shapes_are_cropbox_relative(tmp_path, word_shapes):
    path = fixtures.make_word_form_pdf(tmp_path / "crop.pdf", cropbox=True)
    doc = PdfDocument.open(str(path))
    dx, dy = WORD_CROPBOX[0], WORD_CROPBOX[1]
    s = doc.page_shapes(0)
    shifted = sorted((x0 - dx, y0 - dy, x1 - dx, y1 - dy) for x0, y0, x1, y1 in word_shapes.boxes)
    _same_boxes(s.boxes, shifted)
    hit = _at(s, 120 - dx, 165 - dy)
    assert hit.kind is SnapKind.CELL
    _approx(hit.rect, (72 - dx, 150 - dy, 172 - dx, 180 - dy))
    doc.close()


# -- PdfDocument cache ----------------------------------------------------------------------
def test_page_shapes_cache(word_doc, tmp_path):
    first = word_doc.page_shapes(0)
    assert word_doc.page_shapes(0) is first
    word_doc.set_page_rotation(0, 90)
    rotated = word_doc.page_shapes(0)
    assert rotated is not first and rotated.boxes != first.boxes
    word_doc.set_page_rotation(0, 0)
    back = word_doc.page_shapes(0)
    assert back is not rotated and back == first  # page_changed drops the page's entry
    # Annotation appearances are not part of the scan, but page_changed drops the cache.
    spec = AnnotSpec(0, AnnotKind.TEXT, "hello", FS, (0, 0, 0), QRectF(300, 600, 100, 16))
    word_doc.add_annot(spec)
    fresh = word_doc.page_shapes(0)
    assert fresh is not back and fresh == first
    assert word_doc.page_shapes(0) is fresh
    word_doc.structure_changed.emit()
    again = word_doc.page_shapes(0)
    assert again is not fresh and again == first
    word_doc.save_as(tmp_path / "saved.pdf")  # reloaded
    assert word_doc.page_shapes(0) is not again
    assert word_doc.page_shapes(0) == first
    with pytest.raises(IndexError):
        word_doc.page_shapes(5)


def test_page_shapes_scan_failure_gives_no_shapes(word_doc, monkeypatch, caplog):
    def boom(page):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(snapping, "scan_page", boom)
    assert word_doc.page_shapes(0) == PageShapes()
    assert "could not scan page 0" in caplog.text


# -- performance ------------------------------------------------------------------------------
def test_scan_and_snap_performance(tmp_path):
    path = fixtures.make_busy_drawings_pdf(tmp_path / "busy.pdf", n=3000)
    doc = PdfDocument.open(str(path))
    scans = []
    for _ in range(3):
        doc.structure_changed.emit()  # drop the cache
        start = time.perf_counter()
        shapes = doc.page_shapes(0)
        scans.append(time.perf_counter() - start)
    assert len(shapes.boxes) == 3000
    assert min(scans) < SCAN_BUDGET, scans
    points = [QPointF(10 + (k * 7) % 570, 10 + (k * 13) % 800) for k in range(1000)]
    snaps = []
    for _ in range(3):
        fresh = PageShapes(shapes.boxes, shapes.h_segments, shapes.v_segments)  # cold index
        start = time.perf_counter()
        results = [snap(fresh, p) for p in points]
        snaps.append(time.perf_counter() - start)
    assert min(snaps) < SNAP_BUDGET, snaps
    assert sum(r.kind is SnapKind.BOX for r in results) > 100
    doc.close()
