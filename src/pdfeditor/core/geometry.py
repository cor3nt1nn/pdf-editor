"""Coordinate helpers between PyMuPDF, page space, scene space and device space."""

from __future__ import annotations

import math

import pymupdf
from PySide6.QtCore import QRectF

from pdfeditor.constants import BASE_SCALE

SCALE_QUANTUM = 64


def qrect_from_fitz(rect: pymupdf.Rect | tuple[float, float, float, float]) -> QRectF:
    """Convert a PyMuPDF rect (x0, y0, x1, y1) to a QRectF."""
    r = pymupdf.Rect(rect)
    return QRectF(r.x0, r.y0, r.width, r.height)


def fitz_from_qrect(rect: QRectF) -> pymupdf.Rect:
    """Convert a QRectF to a PyMuPDF Rect."""
    return pymupdf.Rect(rect.left(), rect.top(), rect.right(), rect.bottom())


def zoom_to_scale(zoom_percent: float) -> float:
    """View scale (device pixels per point at DPR 1) for a zoom percentage."""
    return zoom_percent / 100.0 * BASE_SCALE


def scale_to_zoom(scale: float) -> float:
    """Inverse of :func:`zoom_to_scale`."""
    return scale / BASE_SCALE * 100.0


def quantize_scale(scale: float) -> float:
    """Round a render scale to 1/64 so nearby scales share cache entries."""
    q = round(scale * SCALE_QUANTUM) / SCALE_QUANTUM
    return max(q, 1.0 / SCALE_QUANTUM)


def pixel_size(width_pt: float, height_pt: float, scale: float) -> tuple[int, int]:
    """Pixel size of a page rendered at ``scale`` (MuPDF rounds outwards)."""
    return math.ceil(width_pt * scale), math.ceil(height_pt * scale)


def unrotated_to_page(rect: pymupdf.Rect, rotation_matrix: pymupdf.Matrix) -> pymupdf.Rect:
    """Map a rect in unrotated page coordinates (annot/widget rects) to page space.

    ``rotation_matrix`` is ``page.rotation_matrix``.
    """
    return (pymupdf.Rect(rect) * rotation_matrix).normalize()


def page_to_unrotated(rect: pymupdf.Rect, derotation_matrix: pymupdf.Matrix) -> pymupdf.Rect:
    """Map a rect in page space to unrotated coordinates (``page.derotation_matrix``)."""
    return (pymupdf.Rect(rect) * derotation_matrix).normalize()
