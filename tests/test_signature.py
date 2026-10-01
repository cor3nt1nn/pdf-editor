"""M4-T1: signature core (core/signature.py, annotations and PdfDocument integration)."""

from __future__ import annotations

import re
import time
import uuid

import pymupdf
import pytest
from fixtures import (
    FOREIGN_STAMP_NAME,
    LOCKED_SIGNATURE_NAME,
    MUPDF_STAMP_RECT,
    ROTATED_SIGNED_NAME,
    ROTATED_SIGNED_RECT,
    SIG_ASYM_PROBES,
    SIGNED_NAME,
    SIGNED_RECT,
    SIGNED_TEXT_NAME,
    TEXT_STAMP_NAME,
    sig_asym_samples,
)
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from pdfeditor.core import annotations, signature
from pdfeditor.core.annotations import BLACK, AnnotKind, AnnotSpec
from pdfeditor.core.document import AnnotError, PdfDocument
from pdfeditor.core.signature import MAX_IMAGE_SIDE, ImageData

RECT = QRectF(100, 100, 200, 80)
COLORS = {"red": (255, 0, 0), "blue": (0, 0, 255), "white": (255, 255, 255)}


# -- helpers -----------------------------------------------------------------------
def _blank_pdf(path, *, rotation: int = 0, pages: int = 1, green: QRectF | None = None):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        if green is not None:
            r = pymupdf.Rect(green.left(), green.top(), green.right(), green.bottom())
            page.draw_rect(r, color=None, fill=(0, 1, 0))
        page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


def _stroke_image(w: int = 400, h: int = 160) -> QImage:
    """Transparent image with an anti-aliased dark-blue diagonal stroke."""
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(QColor(20, 40, 160), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(10, h - 20, w - 10, 20)
    p.end()
    return img


def _asym(rotation: int = 0) -> ImageData:
    return ImageData(*sig_asym_samples(rotation))


def _spec(rect: QRectF = RECT, image: ImageData | None = None, page: int = 0, name: str = ""):
    return AnnotSpec(
        page, AnnotKind.SIGNATURE, "", 11, BLACK, QRectF(rect), name=name, image=image or _asym()
    )


def _probe(doc: PdfDocument, page: int, rect: QRectF) -> dict[tuple[float, float], tuple]:
    img = doc.render(page, 1.0)
    out = {}
    for fx, fy in SIG_ASYM_PROBES:
        c = img.pixelColor(
            int(rect.left() + fx * rect.width()), int(rect.top() + fy * rect.height())
        )
        out[(fx, fy)] = (c.red(), c.green(), c.blue())
    return out


def _assert_upright(doc: PdfDocument, page: int, rect: QRectF) -> None:
    got = _probe(doc, page, rect)
    for key, colour in SIG_ASYM_PROBES.items():
        want = COLORS[colour]
        assert all(abs(a - b) <= 8 for a, b in zip(got[key], want, strict=True)), (key, got)


def _image_objects(fitz_doc: pymupdf.Document) -> list[int]:
    """Xrefs of RGB image XObjects of a document."""
    out = []
    for xref in range(1, fitz_doc.xref_length()):
        if fitz_doc.xref_get_key(xref, "Subtype")[1] == "/Image" and (
            fitz_doc.xref_get_key(xref, "ColorSpace")[1] == "/DeviceRGB"
        ):
            out.append(xref)
    return out


@pytest.fixture
def blank(tmp_path):
    doc = PdfDocument.open(_blank_pdf(tmp_path / "blank.pdf", pages=2))
    yield doc
    doc.close()


# -- ImageData ---------------------------------------------------------------------
def test_image_data_from_qimage_has_tight_samples_and_alpha() -> None:
    img = _stroke_image()
    data = ImageData.from_qimage(img)
    assert (data.width, data.height) == (400, 160)
    assert len(data.rgb) == 400 * 160 * 3
    assert len(data.alpha) == 400 * 160
    ink = 80 * 400 + 200  # centre of the stroke
    assert data.alpha[ink] == 255
    assert data.rgb[3 * ink : 3 * ink + 3] == bytes((20, 40, 160))
    assert data.alpha[5 * 400 + 5] == 0  # paper
    assert data.alpha[150 * 400 + 395] == 0
    turned = ImageData.from_qimage(img, rotation=90)
    assert (turned.width, turned.height) == (160, 400)
    assert ImageData.from_qimage(img, rotation=180).width == 400


def test_image_data_is_scaled_down_and_round_trips() -> None:
    data = ImageData.from_qimage(_stroke_image(2000, 500))
    assert (data.width, data.height) == (MAX_IMAGE_SIDE, 250)
    back = data.to_qimage()
    assert (back.width(), back.height()) == (MAX_IMAGE_SIDE, 250)
    assert ImageData.from_qimage(back) == data
    assert hash(ImageData.from_qimage(back)) == hash(data)
    assert signature.digest(data) != signature.digest(_asym())
    with pytest.raises(AttributeError):
        data.width = 3  # type: ignore[misc]
    with pytest.raises(ValueError):
        ImageData(2, 2, b"\0" * 11, b"\0" * 4)


# -- creation ----------------------------------------------------------------------
def test_add_signature_writes_stamp_with_image_appearance(blank: PdfDocument) -> None:
    info = blank.add_annot(_spec())
    assert info.kind is AnnotKind.SIGNATURE
    uuid.UUID(info.name)
    assert info.rect == RECT
    assert info.image_size == (400, 160)
    assert info.editable and not info.text_editable
    fitz = blank.fitz
    x = info.xref
    assert fitz.xref_get_key(x, "Subtype")[1] == "/Stamp"
    assert fitz.xref_get_key(x, "IT")[1] == "/StampImage"
    assert fitz.xref_get_key(x, "NM") == ("string", info.name)
    assert fitz.xref_get_key(x, "F")[1] == "4"
    for key in ("Name", "Contents", "C", "CL"):
        assert fitz.xref_get_key(x, key)[0] == "null", key
    ap = int(fitz.xref_get_key(x, "AP/N")[1].split()[0])
    bbox = [float(v) for v in fitz.xref_get_key(ap, "BBox")[1].strip("[]").split()]
    assert bbox == [0, 0, 1, 1]
    assert b"/I Do" in fitz.xref_stream(ap)
    assert re.search(rf"/I {info.image_xref} 0 R", fitz.xref_object(ap))
    img = info.image_xref
    assert fitz.xref_get_key(img, "ColorSpace")[1] == "/DeviceRGB"
    assert fitz.xref_get_key(img, "BitsPerComponent")[1] == "8"
    assert fitz.xref_get_key(img, "Filter")[1] == "/FlateDecode"
    smask = int(fitz.xref_get_key(img, "SMask")[1].split()[0])
    assert fitz.xref_get_key(smask, "ColorSpace")[1] == "/DeviceGray"
    assert fitz.xref_get_key(smask, "Filter")[1] == "/FlateDecode"
    listed = blank.annots(0)
    assert [(a.name, a.kind, a.image_size) for a in listed] == [
        (info.name, AnnotKind.SIGNATURE, (400, 160))
    ]
    assert listed[0] == info


def test_signature_without_image_is_refused(blank: PdfDocument) -> None:
    spec = AnnotSpec(0, AnnotKind.SIGNATURE, "", 11, BLACK, RECT)
    with pytest.raises(AnnotError):
        blank.add_annot(spec)
    assert blank.annots(0) == []


def test_transparent_pixels_show_the_page(tmp_path) -> None:
    doc = PdfDocument.open(_blank_pdf(tmp_path / "green.pdf", green=RECT))
    doc.add_annot(_spec())
    img = doc.render(0, 1.0)
    clear = img.pixelColor(int(RECT.left() + 0.95 * RECT.width()), int(RECT.top() + 10))
    assert (clear.red(), clear.green(), clear.blue()) == (0, 255, 0)
    red = img.pixelColor(int(RECT.left() + 5), int(RECT.top() + 5))
    assert (red.red(), red.green(), red.blue()) == (255, 0, 0)
    doc.close()


def test_placements_share_one_image_also_after_full_save(tmp_path) -> None:
    path = _blank_pdf(tmp_path / "share.pdf", pages=2)
    doc = PdfDocument.open(path)
    a = doc.add_annot(_spec())
    b = doc.add_annot(_spec(QRectF(100, 300, 100, 40), page=1))
    assert a.image_xref == b.image_xref
    other = doc.add_annot(_spec(QRectF(100, 400, 100, 40), image=_asym(90)))
    assert other.image_xref != a.image_xref
    doc.save(force_full=True)
    first = doc.annot(0, a.name)
    assert first is not None
    c = doc.add_annot(_spec(QRectF(300, 500, 100, 40)))
    assert c.image_xref == first.image_xref  # found again by the scan
    doc.save(force_full=True)
    assert len(_image_objects(doc.fitz)) == 2  # the asymmetric image and its turned copy
    doc.close()
    reopened = pymupdf.open(path)
    assert len(_image_objects(reopened)) == 2
    reopened.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_pages_show_signature_upright(tmp_path, rotation: int) -> None:
    path = _blank_pdf(tmp_path / f"rot{rotation}.pdf", rotation=rotation)
    doc = PdfDocument.open(path)
    rect = QRectF(100, 100, 200, 80)
    info = doc.add_annot(_spec(rect, image=_asym(rotation)))
    swapped = rotation in (90, 270)
    assert info.image_size == ((160, 400) if swapped else (400, 160))
    _assert_upright(doc, 0, rect)
    moved = QRectF(150, 300, 250, 100)
    doc.update_annot(0, info.name, rect=moved)
    assert doc.annot(0, info.name).rect == moved
    _assert_upright(doc, 0, moved)
    doc.save()
    _assert_upright(doc, 0, moved)
    doc.save(force_full=True)
    _assert_upright(doc, 0, moved)
    doc.close()
    doc = PdfDocument.open(path)
    _assert_upright(doc, 0, moved)
    doc.close()


def test_annot_image_returns_identical_samples(blank: PdfDocument) -> None:
    data = ImageData.from_qimage(_stroke_image())
    info = blank.add_annot(_spec(image=data))
    assert blank.annot_image(0, info.name) == data
    got = blank.annot_image(0, info.name)
    assert (got.rgb, got.alpha) == (data.rgb, data.alpha)
    blank.save(force_full=True)
    assert blank.annot_image(0, info.name) == data
    with pytest.raises(AnnotError):
        blank.annot_image(0, "missing")


def test_delete_and_recreate_is_pixel_identical(blank: PdfDocument) -> None:
    info = blank.add_annot(_spec())
    before = blank.render(0, 2.0)
    data = blank.annot_image(0, info.name)
    blank.delete_annot(0, info.name)
    assert blank.annots(0) == []
    blank.save(force_full=True)  # garbage-collects the image
    assert _image_objects(blank.fitz) == []
    again = blank.add_annot(annotations.spec_from(info, image=data))
    assert again.name == info.name and again.rect == info.rect
    assert blank.render(0, 2.0) == before


def test_update_annot_on_signature_ignores_text_and_style(blank: PdfDocument) -> None:
    info = blank.add_annot(_spec())
    obj = blank.fitz.xref_object(info.xref)
    same = blank.update_annot(0, info.name, text="x", font_size=30, color=(1, 0, 0))
    assert same == info
    assert blank.fitz.xref_object(info.xref) == obj


# -- reading foreign files ---------------------------------------------------------
def test_signed_pdf_lists_signatures_only(signed_pdf) -> None:
    doc = PdfDocument.open(signed_pdf)
    listed = doc.annots(0)
    names = [a.name for a in listed]
    assert SIGNED_NAME in names
    assert SIGNED_TEXT_NAME in names
    assert TEXT_STAMP_NAME not in names
    assert FOREIGN_STAMP_NAME not in names
    ours = doc.annot(0, SIGNED_NAME)
    assert ours.kind is AnnotKind.SIGNATURE and ours.rect == QRectF(*SIGNED_RECT[:2], 200, 80)
    assert ours.editable
    lazy = [a for a in listed if annotations.is_synthetic(a.name)]
    assert len(lazy) == 1 and lazy[0].kind is AnnotKind.SIGNATURE
    x0, y0, x1, y1 = MUPDF_STAMP_RECT
    assert lazy[0].rect == QRectF(x0, y0, x1 - x0, y1 - y0)
    assert lazy[0].editable
    locked = doc.annot(0, LOCKED_SIGNATURE_NAME)
    assert locked.kind is AnnotKind.SIGNATURE and locked.locked and not locked.editable
    with pytest.raises(AnnotError):
        doc.update_annot(0, LOCKED_SIGNATURE_NAME, rect=QRectF(10, 10, 100, 40))
    with pytest.raises(AnnotError):
        doc.delete_annot(0, LOCKED_SIGNATURE_NAME)
    rotated = doc.annot(1, ROTATED_SIGNED_NAME)
    x0, y0, x1, y1 = ROTATED_SIGNED_RECT
    assert rotated.rect == QRectF(x0, y0, x1 - x0, y1 - y0)
    assert rotated.image_size == (160, 400)
    _assert_upright(doc, 1, rotated.rect)
    doc.close()


def test_mupdf_made_stamp_gets_a_name_when_edited(signed_pdf) -> None:
    doc = PdfDocument.open(signed_pdf)
    lazy = next(a for a in doc.annots(0) if annotations.is_synthetic(a.name))
    image = doc.annot_image(0, lazy.name)  # its image (ICC, 1-bit mask) is decoded
    assert (image.width, image.height) == sig_asym_samples()[:2]
    assert image == _asym()
    real = doc.claim_annot_name(0, lazy.name)
    uuid.UUID(real)
    moved = QRectF(300, 600, 200, 80)
    doc.update_annot(0, real, rect=moved)
    _assert_upright(doc, 0, moved)
    doc.save(force_full=True)
    again = doc.annot(0, real)
    assert again is not None and again.rect == moved
    _assert_upright(doc, 0, moved)
    doc.close()


def test_encrypted_signature_saves_and_reopens(tmp_path) -> None:
    path = tmp_path / "aes.pdf"
    d = pymupdf.open()
    d.new_page(width=595, height=842)
    d.save(
        path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="ow", permissions=-1
    )
    d.close()
    doc = PdfDocument.open(path, password="pw")
    info = doc.add_annot(_spec())
    assert doc.can_save_incrementally()
    doc.save()
    _assert_upright(doc, 0, RECT)
    moved = QRectF(200, 400, 200, 80)
    doc.update_annot(0, info.name, rect=moved)
    doc.save(force_full=True)
    _assert_upright(doc, 0, moved)
    doc.close()
    doc = PdfDocument.open(path, password="pw")
    assert doc.is_encrypted
    assert doc.annot(0, info.name).kind is AnnotKind.SIGNATURE
    _assert_upright(doc, 0, moved)
    doc.close()


def test_bake_flattens_with_alpha(tmp_path) -> None:
    path = _blank_pdf(tmp_path / "bake.pdf", green=RECT)
    doc = PdfDocument.open(path)
    doc.add_annot(_spec())
    doc.save()
    doc.close()
    d = pymupdf.open(path)
    d.bake()
    assert list(d[0].annots()) == []
    pix = d[0].get_pixmap(dpi=72)
    assert pix.pixel(int(RECT.left() + 5), int(RECT.top() + 5)) == (255, 0, 0)
    assert pix.pixel(int(RECT.left() + 0.95 * RECT.width()), int(RECT.top() + 10)) == (0, 255, 0)
    d.close()


def test_no_mupdf_warnings(signed_pdf) -> None:
    pymupdf.TOOLS.mupdf_warnings()  # reset
    doc = PdfDocument.open(signed_pdf)
    for i in range(doc.page_count):
        doc.annots(i)
        doc.page_shapes(i)
        doc.render(i, 1.0)
    info = doc.add_annot(_spec(QRectF(100, 600, 200, 80)))
    doc.add_annot(_spec(QRectF(100, 500, 100, 40), image=_asym(90), page=1))
    doc.update_annot(0, SIGNED_NAME, rect=QRectF(50, 700, 100, 40))
    doc.save()
    doc.annot_image(0, info.name)
    doc.delete_annot(0, info.name)
    doc.save(force_full=True)
    for i in range(doc.page_count):
        doc.render(i, 1.0)
    doc.close()
    assert pymupdf.TOOLS.mupdf_warnings() == ""


def test_reading_hovering_and_saving_signed_pdf_changes_no_byte(signed_pdf) -> None:
    original = signed_pdf.read_bytes()
    doc = PdfDocument.open(signed_pdf)
    for _ in range(2):
        for i in range(doc.page_count):
            for a in doc.annots(i):
                assert doc.annot(i, a.name) == a
            doc.page_shapes(i)
            doc.render(i, 1.0)
    doc.save()
    assert signed_pdf.read_bytes() == original
    doc.close()


# -- performance -------------------------------------------------------------------
def test_hundred_signatures_sharing_one_image_are_fast(tmp_path) -> None:
    doc = PdfDocument.open(_blank_pdf(tmp_path / "many.pdf"))
    data = _asym()
    doc.add_annot(_spec(QRectF(5, 5, 20, 8), image=data))  # warm-up (image object)
    start = time.perf_counter()
    for i in range(100):
        rect = QRectF(20 + (i % 5) * 110, 20 + (i // 5) * 40, 100, 40)
        doc.add_annot(_spec(rect, image=data))
    created = time.perf_counter() - start
    assert created < 0.5, created
    doc.page_changed.emit(0)
    start = time.perf_counter()
    listed = doc.annots(0)
    cold = time.perf_counter() - start
    assert len(listed) == 101
    assert len({a.image_xref for a in listed}) == 1
    assert cold < 0.25, cold
    doc.close()


def test_signature_and_point_helpers_do_not_touch_freetext(blank: PdfDocument) -> None:
    """A text box next to a signature keeps working (dispatch by subtype)."""
    sig = blank.add_annot(_spec())
    text = blank.add_annot(
        AnnotSpec(0, AnnotKind.TEXT, "hello", 11, BLACK, QRectF(100, 300, 150, 20))
    )
    blank.update_annot(0, text.name, text="world", rect=QRectF(QPointF(120, 320), text.rect.size()))
    assert blank.annot(0, text.name).text == "world"
    assert blank.annot(0, sig.name).rect == RECT
    blank.delete_annot(0, sig.name)
    assert [a.name for a in blank.annots(0)] == [text.name]
