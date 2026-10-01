"""M4-T2: undo commands for signatures (AddAnnotCommand, EditAnnotCommand,
DeleteAnnotCommand)."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import replace

import pymupdf
import pytest
from fixtures import (
    LOCKED_SIGNATURE_NAME,
    MUPDF_STAMP_RECT,
    ROTATED_SIGNED_NAME,
    SIG_ASYM_PROBES,
    SIGNED_NAME,
    SIGNED_TEXT_NAME,
    sig_asym_samples,
)
from PySide6.QtCore import QCoreApplication, QObject, QRectF
from PySide6.QtGui import QImage, QUndoStack

import pdfeditor.i18n as i18n
from pdfeditor.core.annotations import BLACK, AnnotKind, AnnotSpec, is_synthetic
from pdfeditor.core.commands import AddAnnotCommand, DeleteAnnotCommand, EditAnnotCommand
from pdfeditor.core.document import AnnotError, PdfDocument
from pdfeditor.core.signature import ImageData

RECT = QRectF(100, 100, 200, 80)
COLORS = {"red": (255, 0, 0), "blue": (0, 0, 255), "white": (255, 255, 255)}


# -- helpers -----------------------------------------------------------------------
def _blank_pdf(path, *, rotation: int = 0):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


def _asym(rotation: int = 0) -> ImageData:
    return ImageData(*sig_asym_samples(rotation))


def _spec(rect: QRectF = RECT, image: ImageData | None = None, name: str = "") -> AnnotSpec:
    return AnnotSpec(
        0, AnnotKind.SIGNATURE, "", 11, BLACK, QRectF(rect), name=name, image=image or _asym()
    )


def _image_objects(fitz_doc: pymupdf.Document) -> list[int]:
    """Xrefs of RGB image XObjects of a document."""
    return [
        xref
        for xref in range(1, fitz_doc.xref_length())
        if fitz_doc.xref_get_key(xref, "Subtype")[1] == "/Image"
        and fitz_doc.xref_get_key(xref, "ColorSpace")[1] == "/DeviceRGB"
    ]


def _assert_upright(doc: PdfDocument, page: int, rect: QRectF) -> None:
    img = doc.render(page, 1.0)
    for (fx, fy), colour in SIG_ASYM_PROBES.items():
        c = img.pixelColor(
            int(rect.left() + fx * rect.width()), int(rect.top() + fy * rect.height())
        )
        got = (c.red(), c.green(), c.blue())
        assert all(abs(a - b) <= 8 for a, b in zip(got, COLORS[colour], strict=True)), (
            (fx, fy),
            got,
        )


@pytest.fixture
def blank(tmp_path):
    doc = PdfDocument.open(_blank_pdf(tmp_path / "blank.pdf"))
    yield doc
    doc.close()


@pytest.fixture
def signed(signed_pdf):
    doc = PdfDocument.open(signed_pdf)
    yield doc
    doc.close()


@pytest.fixture
def french(qapp):
    i18n.install_translators(qapp, "fr")
    yield
    i18n.remove_translators(qapp)


# -- add ---------------------------------------------------------------------------
def test_add_signature_undo_redo_keeps_name_rect_and_pixels(blank: PdfDocument) -> None:
    stack = QUndoStack()
    empty = blank.render(0, 2.0)
    cmd = AddAnnotCommand(blank, _spec())
    uuid.UUID(cmd.name)
    stack.push(cmd)
    created = blank.annot(0, cmd.name)
    assert created is not None and created.kind is AnnotKind.SIGNATURE
    assert created.rect == RECT
    drawn = blank.render(0, 2.0)
    assert drawn != empty
    _assert_upright(blank, 0, RECT)
    for _ in range(2):
        stack.undo()
        assert blank.annots(0) == []
        assert blank.render(0, 2.0) == empty
        stack.redo()
        again = blank.annot(0, cmd.name)
        assert again is not None and again.kind is AnnotKind.SIGNATURE
        assert again.name == cmd.name and again.rect == RECT
        assert blank.annot_image(0, cmd.name) == cmd.spec.image
        assert blank.render(0, 2.0) == drawn


def test_add_signature_redo_after_full_save_recreates_collected_image(blank: PdfDocument) -> None:
    stack = QUndoStack()
    cmd = AddAnnotCommand(blank, _spec())
    stack.push(cmd)
    drawn = blank.render(0, 2.0)
    stack.undo()
    blank.save(force_full=True)  # garbage-collects the now unused image
    assert _image_objects(blank.fitz) == []
    stack.redo()
    again = blank.annot(0, cmd.name)
    assert again is not None and again.rect == RECT
    assert blank.annot_image(0, cmd.name) == cmd.spec.image
    assert blank.render(0, 2.0) == drawn
    assert len(_image_objects(blank.fitz)) == 1
    # undo/redo/undo across another full save
    blank.save(force_full=True)
    stack.undo()
    assert blank.annot(0, cmd.name) is None
    stack.redo()
    assert blank.render(0, 2.0) == drawn


def test_add_signature_redo_shares_image_still_present(blank: PdfDocument) -> None:
    stack = QUndoStack()
    first = AddAnnotCommand(blank, _spec(QRectF(100, 400, 200, 80)))
    stack.push(first)
    second = AddAnnotCommand(blank, _spec())
    stack.push(second)
    shared = blank.annot(0, first.name).image_xref
    assert blank.annot(0, second.name).image_xref == shared
    stack.undo()
    blank.save(force_full=True)  # the image survives: the first signature uses it
    kept = blank.annot(0, first.name).image_xref
    assert len(_image_objects(blank.fitz)) == 1
    stack.redo()
    assert blank.annot(0, second.name).image_xref == kept
    assert len(_image_objects(blank.fitz)) == 1
    _assert_upright(blank, 0, RECT)


def test_add_signature_on_rotated_page_stays_upright_through_undo(tmp_path) -> None:
    doc = PdfDocument.open(_blank_pdf(tmp_path / "rot.pdf", rotation=90))
    try:
        stack = QUndoStack()
        stack.push(AddAnnotCommand(doc, _spec(image=_asym(90))))
        _assert_upright(doc, 0, RECT)
        stack.undo()
        doc.save(force_full=True)
        stack.redo()
        _assert_upright(doc, 0, RECT)
    finally:
        doc.close()


def test_add_signature_survives_wiped_source_directory(tmp_path, blank: PdfDocument) -> None:
    store = tmp_path / "store"
    store.mkdir()
    png = store / "sig.png"
    w, h, rgb, alpha = sig_asym_samples()
    rgba = b"".join(rgb[3 * i : 3 * i + 3] + alpha[i : i + 1] for i in range(w * h))
    QImage(rgba, w, h, 4 * w, QImage.Format.Format_RGBA8888).save(str(png))
    data = ImageData.from_qimage(QImage(str(png)))
    shutil.rmtree(store)
    stack = QUndoStack()
    cmd = AddAnnotCommand(blank, _spec(image=data))
    stack.push(cmd)
    drawn = blank.render(0, 2.0)
    stack.undo()
    blank.save(force_full=True)
    stack.redo()
    assert blank.render(0, 2.0) == drawn


# -- delete ------------------------------------------------------------------------
def test_delete_signature_image_none_until_first_redo(signed: PdfDocument) -> None:
    info = signed.annot(0, SIGNED_NAME)
    expected = signed.annot_image(0, SIGNED_NAME)
    cmd = DeleteAnnotCommand(signed, info)
    assert cmd.image is None
    stack = QUndoStack()
    stack.push(cmd)
    assert cmd.image == expected
    assert signed.annot(0, SIGNED_NAME) is None
    stack.undo()
    image = cmd.image
    stack.redo()
    assert cmd.image is image  # read once


def test_delete_text_box_fetches_no_image(signed: PdfDocument) -> None:
    cmd = DeleteAnnotCommand(signed, signed.annot(0, SIGNED_TEXT_NAME))
    QUndoStack().push(cmd)
    assert cmd.image is None


def test_delete_signature_undo_after_full_save_gc(blank: PdfDocument) -> None:
    info = blank.add_annot(_spec())
    drawn = blank.render(0, 2.0)
    stack = QUndoStack()
    stack.push(DeleteAnnotCommand(blank, info))
    blank.save(force_full=True)
    assert _image_objects(blank.fitz) == []
    stack.undo()
    again = blank.annot(0, info.name)
    assert again is not None and again.rect == info.rect
    assert blank.render(0, 2.0) == drawn
    stack.redo()
    assert blank.annot(0, info.name) is None
    blank.save(force_full=True)
    stack.undo()
    assert blank.render(0, 2.0) == drawn


def test_delete_signature_undo_after_source_directory_wiped(tmp_path, blank: PdfDocument) -> None:
    store = tmp_path / "signatures"
    store.mkdir()
    png = store / "a.png"
    w, h, rgb, alpha = sig_asym_samples()
    rgba = b"".join(rgb[3 * i : 3 * i + 3] + alpha[i : i + 1] for i in range(w * h))
    QImage(rgba, w, h, 4 * w, QImage.Format.Format_RGBA8888).save(str(png))
    info = blank.add_annot(_spec(image=ImageData.from_qimage(QImage(str(png)))))
    drawn = blank.render(0, 2.0)
    stack = QUndoStack()
    stack.push(DeleteAnnotCommand(blank, info))
    shutil.rmtree(store)
    blank.save(force_full=True)
    stack.undo()
    assert blank.render(0, 2.0) == drawn


def test_delete_rotated_signature_undo_upright(signed: PdfDocument) -> None:
    info = signed.annot(1, ROTATED_SIGNED_NAME)
    stack = QUndoStack()
    stack.push(DeleteAnnotCommand(signed, info))
    signed.save(force_full=True)
    stack.undo()
    again = signed.annot(1, ROTATED_SIGNED_NAME)
    assert again is not None and again.rect == info.rect
    _assert_upright(signed, 1, info.rect)


def test_delete_lazy_mupdf_stamp_claims_name_and_restores(signed: PdfDocument) -> None:
    lazy = next(a for a in signed.annots(0) if is_synthetic(a.name))
    assert lazy.kind is AnnotKind.SIGNATURE
    assert lazy.rect == QRectF(
        MUPDF_STAMP_RECT[0],
        MUPDF_STAMP_RECT[1],
        MUPDF_STAMP_RECT[2] - MUPDF_STAMP_RECT[0],
        MUPDF_STAMP_RECT[3] - MUPDF_STAMP_RECT[1],
    )
    cmd = DeleteAnnotCommand(signed, lazy)
    stack = QUndoStack()
    stack.push(cmd)
    real = cmd.name
    assert not is_synthetic(real)
    uuid.UUID(real)
    assert cmd.image is not None and (cmd.image.width, cmd.image.height) == (400, 160)
    signed.save(force_full=True)
    stack.undo()
    again = signed.annot(0, real)
    assert again is not None and again.kind is AnnotKind.SIGNATURE
    _assert_upright(signed, 0, again.rect)
    stack.redo()
    assert signed.annot(0, real) is None


# -- move / resize -----------------------------------------------------------------
@pytest.mark.parametrize("new_rect", [QRectF(150, 300, 200, 80), QRectF(100, 100, 300, 120)])
def test_move_resize_signature_undo_restores_rect(signed: PdfDocument, new_rect) -> None:
    info = signed.annot(0, SIGNED_NAME)
    before = signed.render(0, 2.0)
    stack = QUndoStack()
    cmd = EditAnnotCommand(signed, info, rect=new_rect)
    stack.push(cmd)
    assert signed.annot(0, SIGNED_NAME).rect == new_rect
    _assert_upright(signed, 0, new_rect)
    signed.save(force_full=True)
    stack.undo()
    assert signed.annot(0, SIGNED_NAME).rect == info.rect
    _assert_upright(signed, 0, info.rect)
    assert signed.render(0, 2.0) == before
    stack.redo()
    assert signed.annot(0, SIGNED_NAME).rect == new_rect


# -- failures ----------------------------------------------------------------------
def test_apply_now_failures_leave_nothing(signed: PdfDocument) -> None:
    no_image = replace(_spec(), image=None)
    with pytest.raises(AnnotError):
        AddAnnotCommand(signed, no_image).apply_now()
    info = signed.annot(0, SIGNED_NAME)
    gone = replace(info, name=str(uuid.uuid4()))
    cmd = DeleteAnnotCommand(signed, gone)
    with pytest.raises(AnnotError):
        cmd.apply_now()
    assert cmd.image is None
    locked = signed.annot(0, LOCKED_SIGNATURE_NAME)
    cmd = DeleteAnnotCommand(signed, locked)
    with pytest.raises(AnnotError):
        cmd.apply_now()
    assert cmd.image is None
    assert signed.annot(0, LOCKED_SIGNATURE_NAME) is not None
    assert signed.annot(0, SIGNED_NAME) is not None


# -- texts -------------------------------------------------------------------------
def test_signature_command_texts_english(qapp, blank: PdfDocument) -> None:
    cmd = AddAnnotCommand(blank, _spec())
    assert cmd.text() == "Add signature"
    owner = QObject()
    stack = QUndoStack()
    undo = stack.createUndoAction(owner, "Undo")
    stack.push(cmd)
    assert undo.text() == "Undo Add signature"
    info = blank.annot(0, cmd.name)
    assert EditAnnotCommand(blank, info, rect=info.rect.translated(5, 5)).text() == (
        "Move annotation"
    )
    assert DeleteAnnotCommand(blank, info).text() == "Delete annotation"


def test_signature_command_texts_french(french, blank: PdfDocument) -> None:
    cmd = AddAnnotCommand(blank, _spec())
    assert cmd.text() == "l’ajout de la signature"
    owner = QObject()
    stack = QUndoStack()
    undo = stack.createUndoAction(owner, QCoreApplication.translate("MainWindow", "Undo"))
    stack.push(cmd)
    assert undo.text() == "Annuler l’ajout de la signature"
