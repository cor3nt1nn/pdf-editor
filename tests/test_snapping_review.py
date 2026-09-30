"""M3 review fixes: snapping clipped to the page, page borders, annotation appearances,
overlapping boxes, bullets, lock-free cache and rotation + cropbox."""

from __future__ import annotations

import threading
import time

import fixtures
import pymupdf
import pytest
from fixtures import WORD_CROPBOX, WORD_SHAPES
from PySide6.QtCore import QPointF, QRectF

from pdfeditor.core import snapping
from pdfeditor.core.annotations import AnnotKind, AnnotSpec
from pdfeditor.core.document import PdfDocument, _incremental_bytes
from pdfeditor.core.snapping import PageShapes, Snap, SnapKind, snap, text_placement

FS = 11.0
W, H = 612.0, 792.0


def _content_pdf(path, content: str, *, width: float = W, height: float = H):
    """One page whose content stream is ``content`` (PDF coordinates, y up)."""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((10, 10), " ")  # creates a content stream
    xref = page.get_contents()[0]
    doc.update_stream(xref, content.encode())
    doc.save(path)
    doc.close()
    return path


def _open(path) -> PdfDocument:
    return PdfDocument.open(str(path))


def _in_page(seg_bounds: tuple[float, float], limit: float) -> bool:
    lo, hi = seg_bounds
    m = snapping.CLIP_MARGIN
    return -m <= lo <= hi <= limit + m


# -- finding 1: huge and bleeding rules -------------------------------------------------
@pytest.mark.parametrize("extent", ["10000000", "1000000000000000000"])
def test_huge_rule_is_clipped_and_fast(tmp_path, extent):
    # y-up 400 -> y-down 392; a bleed rule -50..650 at y-down 492.
    content = f"0 g -{extent} 400 2{extent} 0.5 re f -50 300 700 0.5 re f"
    doc = _open(_content_pdf(tmp_path / "huge.pdf", content))
    start = time.perf_counter()
    shapes = doc.page_shapes(0)
    hit = snap(shapes, QPointF(300, 380))
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, elapsed
    assert len(shapes.h_segments) == 2
    for seg in shapes.h_segments:
        assert _in_page((seg.start, seg.end), W)
    assert hit.kind is SnapKind.UNDERLINE
    r = text_placement(hit, QPointF(300, 380), FS, 180, W, H)
    assert r.left() == pytest.approx(snapping.CELL_PAD) and r.width() == pytest.approx(180)
    bleed = snap(shapes, QPointF(600, 480))
    assert bleed.kind is SnapKind.UNDERLINE
    r = text_placement(bleed, QPointF(600, 480), FS, 180, W, H)
    assert r.left() >= 0 and r.right() <= W
    doc.close()


def test_shapes_outside_the_page_are_dropped(tmp_path):
    content = "0 G 1 w 2000 2000 30 30 re S -500 -500 20 20 re S 0 g 900 100 100 0.5 re f"
    doc = _open(_content_pdf(tmp_path / "out.pdf", content))
    shapes = doc.page_shapes(0)
    assert shapes.boxes == () and shapes.h_segments == () and shapes.v_segments == ()
    doc.close()


def test_text_placement_clamped_horizontally_and_vertically():
    cell = Snap(SnapKind.CELL, QRectF(-40, 100, W + 80, 20))
    r = text_placement(cell, QPointF(10, 110), FS, 180, W, H)
    assert r.left() >= 0 and r.right() <= W
    underline = Snap(SnapKind.UNDERLINE, QRectF(-1e7, 300, 3e7, 0))
    r = text_placement(underline, QPointF(10, 290), FS, 180, W, H)
    assert r.left() == pytest.approx(snapping.CELL_PAD) and r.width() == pytest.approx(180)
    none = text_placement(Snap(SnapKind.NONE), QPointF(100, H - 2), FS, 180, W, H)
    assert none.bottom() <= H + 1e-9 and none.top() >= 0
    top = text_placement(Snap(SnapKind.NONE), QPointF(100, 0), FS, 180, W, H)
    assert top.top() >= 0


# -- finding 6: page borders ------------------------------------------------------------
@pytest.mark.parametrize(
    "content",
    [
        # four thin filled rects
        "0 g 20 20 572 0.5 re f 20 771.5 572 0.5 re f 20 20 0.5 752 re f 591.5 20 0.5 752 re f",
        # four stroked lines
        "0 G 0.5 w 20 20 m 592 20 l S 20 772 m 592 772 l S 20 20 m 20 772 l S 592 20 m 592 772 l S",
    ],
)
def test_page_border_of_rules_is_not_a_cell(tmp_path, content):
    doc = _open(_content_pdf(tmp_path / "border.pdf", content))
    shapes = doc.page_shapes(0)
    assert shapes.page_size == (W, H)
    assert snap(shapes, QPointF(300, 400)).kind is SnapKind.NONE
    doc.close()


def test_large_box_on_large_page_is_a_cell(tmp_path):
    # A3 landscape: an 1100 x 60 box is a table row, not a page frame (was > 800 pt).
    content = "0 G 1 w 20 500 1100 60 re S"
    doc = _open(_content_pdf(tmp_path / "a3.pdf", content, width=1191, height=842))
    hit = snap(doc.page_shapes(0), QPointF(500, 842 - 530))
    assert hit.kind is SnapKind.CELL
    assert hit.rect.width() == pytest.approx(1100)
    doc.close()


# -- finding 13: overlapping boxes and bullets --------------------------------------------
def test_overlapping_boxes_snap_to_smallest_drawn_box(tmp_path):
    # y-down: A = (100, 100)-(300, 200), B = (200, 150)-(400, 300); overlap (200,150)-(300,200)
    content = "0 G 1 w 100 592 200 100 re S 200 492 200 150 re S"
    doc = _open(_content_pdf(tmp_path / "overlap.pdf", content))
    hit = snap(doc.page_shapes(0), QPointF(250, 175))
    assert hit.kind is SnapKind.CELL
    assert (hit.rect.left(), hit.rect.top(), hit.rect.right(), hit.rect.bottom()) == (
        pytest.approx((100, 100, 300, 200))
    )
    doc.close()


def test_table_inside_a_frame_still_gives_cells():
    # A frame box around two columns separated by a rule: the ray cell wins.
    shapes = PageShapes(
        boxes=((0.0, 0.0, 200.0, 40.0),),
        h_segments=(snapping.Segment(0, 200, 0), snapping.Segment(0, 200, 40)),
        v_segments=(
            snapping.Segment(0, 40, 0),
            snapping.Segment(0, 40, 100),
            snapping.Segment(0, 40, 200),
        ),
    )
    hit = snap(shapes, QPointF(50, 20))
    assert hit.kind is SnapKind.CELL
    assert hit.rect.width() == pytest.approx(100)


def _raw(font: str, chars: list[str]) -> dict:
    """A rawdict with one 10 pt line of ``chars`` (9 x 11 pt bboxes, 20 pt apart)."""
    items = [
        {"c": c, "bbox": (100 + 20 * i, 100, 109 + 20 * i, 111), "origin": (100 + 20 * i, 109)}
        for i, c in enumerate(chars)
    ]
    spans = [{"font": font, "size": 10.0, "chars": items}]
    return {"blocks": [{"lines": [{"dir": (1.0, 0.0), "spans": spans}]}]}


def test_bullets_are_not_glyph_boxes():
    assert snapping.glyph_boxes(_raw("Helvetica", ["■", "○"])) == set()
    assert len(snapping.glyph_boxes(_raw("Helvetica", ["☐", "□"]))) == 2
    # Wingdings: "n" (■) and "l" (●) are bullets, "o"/"q"/"þ" boxes, "\x00" unknown.
    wing = snapping.glyph_boxes(_raw("Wingdings-Regular", ["", "", "\x00"]))
    assert len(wing) == 1
    boxes = snapping.glyph_boxes(_raw("Wingdings-Regular", ["", "q", "þ"]))
    assert len(boxes) == 3


# -- finding 7: annotation appearances ----------------------------------------------------
def _annotated(path):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.add_rect_annot(pymupdf.Rect(100, 100, 200, 130))  # Square
    page.add_line_annot(pymupdf.Point(100, 200), pymupdf.Point(300, 200))
    ft = page.add_freetext_annot(
        pymupdf.Rect(100, 300, 250, 330),
        "boxed",
        border_width=1,
        border_color=(0, 0, 0),
        richtext=True,
    )
    doc.xref_set_key(ft.xref, "NM", pymupdf.get_pdf_str("boxed"))
    doc.save(path)
    doc.close()
    return path


def test_annotation_appearances_are_not_snap_targets(tmp_path):
    doc = _open(_annotated(tmp_path / "annots.pdf"))
    shapes = doc.page_shapes(0)
    assert shapes.boxes == () and shapes.h_segments == () and shapes.v_segments == ()
    for x, y in ((150, 115), (200, 195), (170, 315)):
        assert snap(shapes, QPointF(x, y)).kind is SnapKind.NONE
    doc.close()


def test_moved_bordered_freetext_leaves_no_stale_target(tmp_path):
    doc = _open(_annotated(tmp_path / "annots.pdf"))
    assert snap(doc.page_shapes(0), QPointF(170, 315)).kind is SnapKind.NONE
    doc.update_annot(0, "boxed", rect=QRectF(300, 500, 150, 30))
    shapes = doc.page_shapes(0)
    for x, y in ((170, 315), (370, 515)):
        assert snap(shapes, QPointF(x, y)).kind is SnapKind.NONE
    doc.close()


def test_content_box_under_an_annotation_counts_again_once_it_moves(tmp_path):
    content = "0 G 1 w 100 462 200 30 re S"  # y-down (100, 300)-(300, 330)
    path = _content_pdf(tmp_path / "under.pdf", content)
    doc = _open(path)
    spec = AnnotSpec(0, AnnotKind.TEXT, "cover", FS, (0, 0, 0), QRectF(90, 290, 220, 50))
    info = doc.add_annot(spec)
    assert snap(doc.page_shapes(0), QPointF(200, 315)).kind is SnapKind.NONE
    doc.update_annot(0, info.name, rect=QRectF(90, 600, 220, 50))
    assert snap(doc.page_shapes(0), QPointF(200, 315)).kind is SnapKind.CELL
    doc.close()


# -- rotation + cropbox ---------------------------------------------------------------------
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_rotation_and_cropbox_combined(tmp_path, rotate):
    path = fixtures.make_word_form_pdf(tmp_path / f"rc{rotate}.pdf", rotate=rotate, cropbox=True)
    doc = PdfDocument.open(str(path))
    dx, dy = WORD_CROPBOX[0], WORD_CROPBOX[1]
    with doc.lock:
        matrix = doc.fitz[0].rotation_matrix
    s = doc.page_shapes(0)

    def mapped(box):
        x0, y0, x1, y1 = box
        return tuple((pymupdf.Rect(x0 - dx, y0 - dy, x1 - dx, y1 - dy) * matrix).normalize())

    for (x, y), key, kind in (
        ((126, 254), "checkbox_12", SnapKind.BOX),
        ((206, 256), "box_lines", SnapKind.BOX),
        ((200, 350), "area", SnapKind.CELL),
    ):
        p = pymupdf.Point(x - dx, y - dy) * matrix
        hit = snap(s, QPointF(p.x, p.y))
        assert hit.kind is kind
        got = (hit.rect.left(), hit.rect.top(), hit.rect.right(), hit.rect.bottom())
        assert got == pytest.approx(mapped(WORD_SHAPES[key]), abs=0.01)
    # A bordered annotation placed over the area is skipped on this page too.
    area = WORD_SHAPES["area"]
    centre = pymupdf.Point(300 - dx, 350 - dy) * matrix
    rect = QRectF(centre.x - 30, centre.y - 30, 60, 60)
    with doc.lock:
        page = doc.fitz[0]
        unrotated = pymupdf.Rect(rect.left(), rect.top(), rect.right(), rect.bottom())
        unrotated = (unrotated * page.derotation_matrix).normalize()
        page.add_rect_annot(unrotated)
    doc.page_changed.emit(0)
    s = doc.page_shapes(0)
    hit = snap(s, QPointF(centre.x, centre.y))
    assert hit.kind is SnapKind.CELL  # still the area, not the annotation's square
    assert hit.rect.width() * hit.rect.height() == pytest.approx(
        (area[2] - area[0]) * (area[3] - area[1]), rel=1e-3
    )
    doc.close()


def test_scanning_a_rotated_page_does_not_modify_the_document(tmp_path):
    path = fixtures.make_word_form_pdf(tmp_path / "r.pdf", rotate=90)
    doc = PdfDocument.open(str(path))
    with doc.lock:
        before = _incremental_bytes(doc.fitz)
    doc.page_shapes(0)
    with doc.lock:
        assert _incremental_bytes(doc.fitz) == before
    doc.close()


# -- finding 5: lock-free cache hits --------------------------------------------------------
def test_cached_shapes_do_not_wait_for_the_lock(word_form_pdf):
    doc = PdfDocument.open(str(word_form_pdf))
    shapes = doc.page_shapes(0)
    held, release = threading.Event(), threading.Event()

    def hold():
        with doc.lock:
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        start = time.perf_counter()
        assert doc.page_shapes(0) is shapes
        assert time.perf_counter() - start < 0.05
    finally:
        release.set()
        t.join()
    doc.close()
