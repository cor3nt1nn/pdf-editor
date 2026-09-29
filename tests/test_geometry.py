from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QRectF

from pdfeditor.core.geometry import (
    fitz_from_qrect,
    page_to_unrotated,
    pixel_size,
    qrect_from_fitz,
    quantize_scale,
    scale_to_zoom,
    unrotated_to_page,
    zoom_to_scale,
)


def test_rect_round_trip() -> None:
    q = qrect_from_fitz(pymupdf.Rect(10, 20, 110, 70))
    assert q == QRectF(10, 20, 100, 50)
    assert fitz_from_qrect(q) == pymupdf.Rect(10, 20, 110, 70)


def test_zoom_scale() -> None:
    assert zoom_to_scale(100) == pytest.approx(96 / 72)
    assert zoom_to_scale(75) == pytest.approx(1.0)
    assert scale_to_zoom(zoom_to_scale(137.0)) == pytest.approx(137.0)


def test_quantize_scale() -> None:
    assert quantize_scale(1.0) == 1.0
    assert quantize_scale(1.0 + 1 / 200) == 1.0
    assert quantize_scale(1.337) == pytest.approx(round(1.337 * 64) / 64)
    assert quantize_scale(0.0) == 1 / 64
    assert (quantize_scale(2.3456) * 64).is_integer()


def test_pixel_size() -> None:
    assert pixel_size(595, 842, 1.0) == (595, 842)
    assert pixel_size(595, 842, 1.5) == (893, 1263)


def test_rotation_round_trip() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.set_rotation(90)
    r = pymupdf.Rect(400, 700, 600, 780)
    mapped = unrotated_to_page(r, page.rotation_matrix)
    assert mapped in page.rect
    back = page_to_unrotated(mapped, page.derotation_matrix)
    assert tuple(back) == pytest.approx(tuple(r))
