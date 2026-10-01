"""M4 review fixes: nothing hidden under alpha 0 reaches the PDF, size of a "Keep colour"
photo signature, foreign image stamps are locked, undone/deleted objects are not written
by incremental saves."""

from __future__ import annotations

import os
import re
import zlib
from pathlib import Path

import pymupdf
import pytest
from fixtures import A4, ODD_ACROBAT_KEYS, ODD_STAMP_RECTS
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QUndoStack

from pdfeditor.core import image_tools, orphans, signature
from pdfeditor.core.annotations import AnnotKind, AnnotSpec
from pdfeditor.core.commands import AddAnnotCommand, DeleteAnnotCommand, EditAnnotCommand
from pdfeditor.core.document import AnnotError, PdfDocument
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
    return AnnotSpec(0, AnnotKind.SIGNATURE, "", 11.0, (0.0, 0.0, 0.0), rect, name, image=image)


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


# -- 3: foreign appearances are locked ---------------------------------------------------
def _stamp_state(doc: PdfDocument, name: str) -> tuple[str, bytes, dict[str, str]]:
    with doc.lock:
        xref = doc.annot(0, name).xref
        form = int(doc.fitz.xref_get_key(xref, "AP/N")[1].split()[0])
        keys = {k: doc.fitz.xref_get_key(xref, k)[1] for k in ("Contents", "T", "C", "Rect")}
        return doc.fitz.xref_object(form, compressed=True), doc.fitz.xref_stream(form), keys


@pytest.mark.parametrize("name", ["acrobat", "scaled"])
def test_foreign_appearance_stamp_is_locked_and_kept(qapp, odd_stamps_pdf, name) -> None:
    doc = PdfDocument.open(str(odd_stamps_pdf))
    try:
        info = doc.annot(0, name)
        assert info.kind is AnnotKind.SIGNATURE and info.locked and not info.editable
        with doc.lock:
            assert not signature.has_mupdf_appearance(doc.fitz, info.xref)
            assert signature.has_mupdf_appearance(doc.fitz, doc.annot(0, "ok").xref)
        before = _stamp_state(doc, name)
        if name == "acrobat":
            keys = before[2]
            assert keys["Contents"] == ODD_ACROBAT_KEYS["Contents"]
            assert keys["T"] == ODD_ACROBAT_KEYS["T"]
            assert b"/Im0 Do" in before[1]
        with pytest.raises(AnnotError):
            doc.update_annot(0, name, rect=info.rect.translated(10, 10))
        with pytest.raises(AnnotError):
            doc.delete_annot(0, name)
        with pytest.raises(AnnotError):
            EditAnnotCommand(doc, info, rect=info.rect.translated(10, 10)).apply_now()
        with pytest.raises(AnnotError):
            DeleteAnnotCommand(doc, info).apply_now()
        assert _stamp_state(doc, name) == before
        doc.save()
        assert _stamp_state(doc, name) == before
        assert doc.annot(0, name).rect == QRectF(*ODD_STAMP_RECTS[name][:2], 200, 80)
    finally:
        doc.close()


def test_mupdf_made_stamps_stay_editable(signed_pdf) -> None:
    doc = PdfDocument.open(str(signed_pdf))
    try:
        sigs = [a for a in doc.annots(0) if a.kind is AnnotKind.SIGNATURE and not a.locked]
        assert len(sigs) >= 2
        with doc.lock:
            assert all(signature.has_mupdf_appearance(doc.fitz, a.xref) for a in sigs)
    finally:
        doc.close()


# -- 2: undone/deleted objects are not written by incremental saves ----------------------
#: A size no other image of the tests has.
ODD_W, ODD_H = 123, 45
SECRET = "Secret-text-7f3a"


def _odd_image() -> ImageData:
    rgb = bytes((i * 7) % 256 for i in range(ODD_W * ODD_H * 3))
    alpha = bytes(255 if i % 3 else 0 for i in range(ODD_W * ODD_H))
    return ImageData(ODD_W, ODD_H, rgb, alpha)


_IMAGE_DICT = re.compile(rb"/Width\s*%d\s*/Height\s*%d\b" % (ODD_W, ODD_H))
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def _raw_images(path) -> int:
    """Image dictionaries of the odd size anywhere in the file (all revisions): 2 per
    signature image (the image and its /SMask)."""
    return len(_IMAGE_DICT.findall(path.read_bytes()))


def _raw_contains(path, needle: bytes) -> bool:
    """``needle`` in the raw bytes or in any Flate-decoded stream (all revisions)."""
    data = path.read_bytes()
    if needle in data:
        return True
    for m in _STREAM.finditer(data):
        try:
            if needle in zlib.decompress(m.group(1)):
                return True
        except zlib.error:
            continue
    return False


def _live_images(path) -> list[int]:
    """Image xrefs of the odd size in the latest revision."""
    pdf = pymupdf.open(path)
    try:
        found = []
        for x in range(1, pdf.xref_length()):
            try:
                if pdf.xref_get_key(x, "Subtype")[1] != "/Image":
                    continue
            except Exception:  # a free entry
                continue
            if pdf.xref_get_key(x, "Width")[1] == str(ODD_W) and "SMask" in pdf.xref_object(x):
                found.append(x)
        return found
    finally:
        pdf.close()


def _signature_spec(rect: QRectF = RECT) -> AnnotSpec:
    return _spec(_odd_image(), rect)


def _text_spec(text: str = SECRET) -> AnnotSpec:
    return AnnotSpec(0, AnnotKind.TEXT, text, 12.0, (0.0, 0.0, 0.0), QRectF(50, 400, 200, 30))


def test_undone_signature_is_not_written(qapp, blank) -> None:
    stack = QUndoStack()
    size = os.path.getsize(blank.path)
    cmd = AddAnnotCommand(blank, _signature_spec())
    cmd.apply_now()
    stack.push(cmd)
    name = cmd.name
    stack.undo()
    blank.save()
    path = Path(blank.path)
    assert _raw_images(path) == 0
    assert not _raw_contains(path, name.encode())
    assert not _live_images(path)
    assert os.path.getsize(path) - size < 2_000
    assert blank.can_save_incrementally()  # the reloaded file needed no repair
    # Redo after the save re-creates it from the snapshot (new objects).
    stack.redo()
    info = blank.annot(0, name)
    assert info is not None and blank.annot_image(0, name) == _odd_image()
    blank.save()
    assert len(_live_images(path)) == 1 and _raw_images(path) == 2
    reopened = PdfDocument.open(path)
    try:
        assert reopened.annot_image(0, name) == _odd_image()
    finally:
        reopened.close()


def test_deleted_signature_is_not_written(qapp, blank) -> None:
    info = blank.add_annot(_signature_spec())
    delete = DeleteAnnotCommand(blank, info)
    delete.apply_now()
    blank.save()
    path = Path(blank.path)
    assert _raw_images(path) == 0 and not _raw_contains(path, info.name.encode())
    # Undo of the delete still works after the save.
    delete.undo()
    assert blank.annot_image(0, info.name) == _odd_image()
    blank.save()
    assert len(_live_images(path)) == 1


def test_shared_image_still_used_is_kept(qapp, blank) -> None:
    first = blank.add_annot(_signature_spec())
    second = blank.add_annot(_signature_spec(RECT.translated(0, 200)))
    assert first.image_xref == second.image_xref
    blank.delete_annot(0, first.name)
    blank.save()
    path = Path(blank.path)
    assert len(_live_images(path)) == 1 and _raw_images(path) == 2
    assert blank.annot(0, first.name) is None
    assert blank.annot_image(0, second.name) == _odd_image()
    assert not _raw_contains(path, first.name.encode())


def test_deleted_freetext_and_replaced_text_are_not_written(qapp, blank) -> None:
    gone = blank.add_annot(_text_spec())
    blank.delete_annot(0, gone.name)
    kept = blank.add_annot(_text_spec("Other " + SECRET[::-1]))
    blank.update_annot(0, kept.name, text="Public")
    blank.save()
    path = Path(blank.path)
    assert not _raw_contains(path, SECRET.encode())
    assert not _raw_contains(path, SECRET[::-1].encode())
    assert blank.annot(0, kept.name).text == "Public"


def test_earlier_revision_keeps_it_until_a_full_save(qapp, blank) -> None:
    info = blank.add_annot(_signature_spec())
    blank.save()
    path = Path(blank.path)
    blank.delete_annot(0, info.name)
    blank.save()  # incremental: the earlier revision still holds the image
    assert blank.annot(0, info.name) is None
    assert _raw_images(path) == 2  # (unreferenced, but in the file: Save As / full save)
    blank.save(force_full=True)  # garbage collection drops it from the file
    assert _raw_images(path) == 0 and not _raw_contains(path, info.name.encode())


def test_orphans_in_encrypted_document(qapp, tmp_path) -> None:
    path = tmp_path / "aes.pdf"
    pdf = pymupdf.open()
    pdf.new_page(width=A4[0], height=A4[1])
    pdf.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="ow")
    pdf.close()
    doc = PdfDocument.open(path, password="pw")
    try:
        gone = doc.add_annot(_signature_spec())
        kept = doc.add_annot(_signature_spec(RECT.translated(0, 200)))
        doc.delete_annot(0, gone.name)
        doc.save()
        assert doc.can_save_incrementally()
        assert _raw_images(path) == 2
        assert doc.annot(0, gone.name) is None
        assert doc.annot_image(0, kept.name) == _odd_image()
    finally:
        doc.close()


def test_session_orphans_core(qapp, blank) -> None:
    with blank.lock:
        assert orphans.session_orphans(blank.fitz, blank.fitz.xref_length()) == []
    first_new = blank._first_new_xref
    info = blank.add_annot(_signature_spec())
    with blank.lock:
        live = set(orphans.session_orphans(blank.fitz, first_new))
        assert info.xref not in live and info.image_xref not in live
    blank.delete_annot(0, info.name)
    with blank.lock:
        dead = orphans.session_orphans(blank.fitz, first_new)
        assert {info.xref, info.image_xref} <= set(dead)
        smask = int(blank.fitz.xref_get_key(info.image_xref, "SMask")[1].split()[0])
        assert smask in dead
        assert all(x >= first_new for x in dead)
