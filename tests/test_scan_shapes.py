"""M8-T4: snapping on scanned pages (core/scan_shapes.py, PdfDocument.page_shapes)."""

from __future__ import annotations

import shutil

import pymupdf
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from timing import best_time

from pdfeditor.core import scan_shapes, snapping
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.ocr import OcrLine, OcrWord, PageOcr
from pdfeditor.core.snapping import SnapKind

TOL = 3.0


def _grey(path, dpi: int = scan_shapes.RENDER_DPI) -> QImage:
    with pymupdf.open(str(path)) as doc:
        pix = doc[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, annots=False)
        return QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_Grayscale8
        ).copy()


def _scale(image: QImage) -> float:
    return image.width() / 595.0


def _found(expected: list[float], positions: list[float]) -> int:
    return sum(1 for e in expected if any(abs(p - e) <= TOL for p in positions))


@pytest.mark.parametrize("name", ["scan_clean", "scan_skewed", "scan_200"])
def test_estimate_skew(request, name) -> None:
    fixture = request.getfixturevalue(name)
    image = _grey(fixture.path)
    assert abs(scan_shapes.estimate_skew(image) - fixture.skew) <= 0.15


@pytest.mark.parametrize("name", ["scan_clean", "scan_skewed", "scan_200"])
def test_rules_are_found(request, name) -> None:
    """Every underline and table rule within 3 pt, also on skewed scans."""
    fixture = request.getfixturevalue(name)
    image = _grey(fixture.path)
    rules = scan_shapes.scan_rules(image, _scale(image))
    long_h = [s.position for s in rules.h if s.end - s.start >= 100]
    long_v = [s.position for s in rules.v if s.end - s.start >= 50]
    geometry = fixture.geometry
    assert _found(geometry.h_rules, long_h) == len(geometry.h_rules) == 9
    assert _found(geometry.v_rules, long_v) == len(geometry.v_rules) == 4
    # Each underline found spans its drawn extent.
    for x0, x1, y in geometry.underlines:
        seg = min(rules.h, key=lambda s: abs(s.position - y) + abs(s.start - x0))
        assert abs(seg.start - x0) <= 4 and abs(seg.end - x1) <= 4


def test_detect_rules_without_skew(scan_clean) -> None:
    image = _grey(scan_clean.path)
    h, v = scan_shapes.detect_rules(image, _scale(image))
    # Checkbox sides are rules too (11 pt).
    for x0, y0, x1, _y1 in scan_clean.geometry.boxes:
        assert any(abs(p - y0) <= 1.5 and abs(a - x0) <= 1.5 for a, _b, p in h)
        assert any(abs(p - x1) <= 1.5 and abs(a - y0) <= 1.5 for a, _b, p in v)


@pytest.mark.parametrize("name", ["scan_clean", "scan_skewed"])
def test_snap_on_a_scan(request, tmp_path, name) -> None:
    fixture = request.getfixturevalue(name)
    path = tmp_path / "scan.pdf"
    shutil.copyfile(fixture.path, path)
    doc = PdfDocument.open(path)
    shapes = doc.page_shapes(0)
    assert shapes.h_segments and shapes.v_segments
    geometry = fixture.geometry
    # A table cell.
    x0, y0, x1, y1 = geometry.cell(1, 1)
    hit = snapping.snap(shapes, QPointF((x0 + x1) / 2, (y0 + y1) / 2))
    assert hit.kind is SnapKind.CELL
    r = hit.rect
    assert abs(r.left() - x0) <= TOL and abs(r.right() - x1) <= TOL
    assert abs(r.top() - y0) <= TOL and abs(r.bottom() - y1) <= TOL
    # A checkbox square.
    bx0, by0, bx1, by1 = geometry.boxes[1]
    hit = snapping.snap(shapes, QPointF((bx0 + bx1) / 2, (by0 + by1) / 2))
    assert hit.kind is SnapKind.BOX
    assert abs(hit.rect.center().x() - (bx0 + bx1) / 2) <= TOL
    assert abs(hit.rect.center().y() - (by0 + by1) / 2) <= TOL
    # An underline: a click just above it.
    ux0, ux1, uy = geometry.underlines[2]
    hit = snapping.snap(shapes, QPointF((ux0 + ux1) / 2, uy - 6))
    assert hit.kind is SnapKind.UNDERLINE
    assert abs(hit.rect.top() - uy) <= TOL
    doc.close()


def test_fallback_cold_time(scan_skewed, tmp_path) -> None:
    path = tmp_path / "scan.pdf"
    shutil.copyfile(scan_skewed.path, path)
    doc = PdfDocument.open(path)

    best, times = best_time(lambda: doc.page_shapes(0), 0.4, setup=lambda: doc.page_changed.emit(0))
    assert best <= 0.4, times
    doc.close()


def test_vector_pages_are_unchanged(tmp_path) -> None:
    """A digital page with drawn rules keeps its vector shapes (no pixel scan)."""
    import fixtures

    path = fixtures.make_word_form_pdf(tmp_path / "word.pdf")
    doc = PdfDocument.open(path)
    with doc.lock:
        expected = snapping.scan_page(doc.fitz[0])
    assert doc.page_shapes(0) == expected
    doc.close()
    # A blank page and a text page: not scans, no shapes.
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(tmp_path / "blank.pdf"))
    doc.close()
    blank = PdfDocument.open(tmp_path / "blank.pdf")
    shapes = blank.page_shapes(0)
    assert not shapes.h_segments and not shapes.v_segments and not shapes.boxes
    assert not blank.is_scanned_page(0)
    blank.close()


def test_dotted_leader_in_the_pixels(scan_skewed, tmp_path) -> None:
    """The form's "Observations : ......" leader has no drawn rule: its dots are found
    in the pixels (Tesseract does not report them as a word)."""
    image = _grey(scan_skewed.path)
    rules = scan_shapes.scan_rules(image, _scale(image))
    lx, ly = scan_skewed.geometry.leader
    assert any(abs(s.position - ly) <= TOL and s.start <= lx + 3 for s in rules.dotted)
    assert len(rules.dotted) <= 2  # nothing else on the page looks like dots
    path = tmp_path / "scan.pdf"
    shutil.copyfile(scan_skewed.path, path)
    doc = PdfDocument.open(path)
    hit = snapping.snap(doc.page_shapes(0), QPointF(lx + 60, ly - 5))
    assert hit.kind is SnapKind.UNDERLINE
    assert abs(hit.rect.top() - ly) <= TOL
    doc.close()


def test_ocr_leaders_become_underlines(scan_clean, tmp_path) -> None:
    """Leader words of an OCR result (some engines report the dots) are underlines too:
    the shapes are rebuilt when the result is set."""
    path = tmp_path / "scan.pdf"
    shutil.copyfile(scan_clean.path, path)
    doc = PdfDocument.open(path)
    x0, x1, y = 300.0, 520.0, 780.0  # an empty band at the bottom of the page
    click = QPointF(400, y - 5)
    assert snapping.snap(doc.page_shapes(0), click).kind is SnapKind.NONE
    leader = OcrLine((x0, y - 3, x1, y), (x0, y), (OcrWord((x0, y - 3, x1, y), "." * 40),))
    doc.set_page_ocr(0, PageOcr((leader,), 595.0, 842.0))  # drops the shapes cache
    hit = snapping.snap(doc.page_shapes(0), click)
    assert hit.kind is SnapKind.UNDERLINE
    assert abs(hit.rect.top() - y) <= 0.01
    doc.close()


def test_leader_segments() -> None:
    line = OcrLine(
        (10, 10, 300, 22),
        (10, 20),
        (
            OcrWord((10, 10, 60, 22), "Nom"),
            OcrWord((70, 10, 200, 21), "…………"),
            OcrWord((210, 10, 212, 21), "...."),  # too short
            OcrWord((220, 10, 300, 22), "a.b.c."),
        ),
    )
    result = PageOcr((line,), 400, 400)
    assert scan_shapes.leader_segments(result) == [snapping.Segment(70, 200, 21)]
    assert scan_shapes.leader_segments(result.rotated(90)) == []  # vertical lines
    assert scan_shapes.leader_segments(None) == []
