"""M4 review fixes: nothing hidden under alpha 0 reaches the PDF, size of a "Keep colour"
photo signature."""

from __future__ import annotations

import os

import pymupdf
import pytest
from fixtures import A4
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from pdfeditor.core import image_tools
from pdfeditor.core.annotations import AnnotKind, AnnotSpec
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.signature import ImageData

RECT = QRectF(100, 100, 200, 80)


def _blank_pdf(path):
    pdf = pymupdf.open()
    pdf.new_page(width=A4[0], height=A4[1])
    pdf.save(path)
    pdf.close()
    return path


@pytest.fixture
def blank(tmp_path):
    doc = PdfDocument.open(_blank_pdf(tmp_path / "blank.pdf"))
    yield doc
    doc.close()


def _spec(image: ImageData, rect: QRectF = RECT, name: str = "") -> AnnotSpec:
    return AnnotSpec(0, AnnotKind.SIGNATURE, name, 11.0, (0.0, 0.0, 0.0), rect, image=image)


def _clear_colors(data: ImageData) -> set[bytes]:
    rgb, alpha = data.rgb, data.alpha
    return {rgb[3 * i : 3 * i + 3] for i, a in enumerate(alpha) if a == 0}


# -- 1: alpha-0 pixels carry no picture ----------------------------------------------
def test_from_qimage_neutralises_rgb_under_alpha_0(qapp) -> None:
    w, h = 64, 32
    rgb = bytearray(os.urandom(w * h * 3))
    alpha = bytearray(w * h)
    for i in range(0, w * h, 7):
        alpha[i] = 255
    for i in range(3, w * h, 11):
        alpha[i] = 90
    rgba = bytearray(4 * w * h)
    for c in range(3):
        rgba[c::4] = rgb[c::3]
    rgba[3::4] = alpha
    image = QImage(bytes(rgba), w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
    for rotation in (0, 90):
        data = ImageData.from_qimage(image, rotation)
        assert len(_clear_colors(data)) == 1, rotation
    data = ImageData.from_qimage(image)
    first = alpha.index(0)
    assert _clear_colors(data) == {bytes(rgb[3 * first : 3 * first + 3])}
    out = data.rgb
    for i, a in enumerate(alpha):  # any alpha: colour kept exactly
        if a:
            assert out[3 * i : 3 * i + 3] == rgb[3 * i : 3 * i + 3]
    assert data.alpha == bytes(alpha)


def test_from_qimage_keeps_processed_image(qapp, signature_photo) -> None:
    processed = image_tools.process(
        image_tools.prepare(image_tools.load_image(signature_photo)),
        threshold=120,
    )
    data = ImageData.from_qimage(image_tools.to_qimage(processed))
    assert (data.rgb, data.alpha) == (processed.rgb, processed.alpha)


@pytest.mark.parametrize(
    ("crop", "color", "budget"),
    [(True, None, 60_000), (False, None, 60_000), (True, (0, 0, 0), 15_000)],
)
def test_photo_signature_incremental_save_is_small(
    qapp, blank, signature_photo, crop, color, budget
) -> None:
    """The privacy fix also shrinks a placement: ~170 KB before (the whole paper of the
    photo); with "Keep" the JPEG noise of the visible ink (~24k pixels) remains, about
    45 KB; a constant ink colour costs about 6 KB."""
    prepared = image_tools.prepare(image_tools.load_image(signature_photo))
    processed = image_tools.process(
        prepared, threshold=prepared.otsu_threshold, color=color, crop=crop
    )
    data = ImageData.from_qimage(image_tools.to_qimage(processed))
    assert len(_clear_colors(data)) == 1
    size = os.path.getsize(blank.path)
    blank.add_annot(_spec(data))
    blank.save()
    growth = os.path.getsize(blank.path) - size
    assert growth < budget, growth
    assert blank.annot_image(0, blank.annots(0)[0].name) == data
