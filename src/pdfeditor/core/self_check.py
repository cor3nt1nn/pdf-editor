"""Documents and checks of the hidden ``--self-check`` mode (see ``pdfeditor.self_check``).

Generates its own PDFs with PyMuPDF and drives them through :class:`PdfDocument`: open,
fill, place annotations, render, incremental and full save, flattened and clean export.
Each ``check_*`` function writes into the given directory, returns details and raises
(``AssertionError`` or the error of the failing call) when something is wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from pdfeditor.core.annotations import STAMP_GLYPHS, AnnotKind, AnnotSpec
from pdfeditor.core.document import ExportOptions, PdfDocument
from pdfeditor.core.forms import FieldKind
from pdfeditor.core.signature import ImageData

PASSWORD = "frozen-secret"
FIELD_TEXT = "Frozen é € check"
FREETEXT = "Texte gelé é à ç"


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_form_pdf(path: Path) -> Path:
    """An AES-256 (user password :data:`PASSWORD`) one-page form: a text field and a
    checkbox."""
    doc = pymupdf.open()
    page = doc.new_page()
    text = pymupdf.Widget()
    text.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    text.field_name = "name"
    text.rect = pymupdf.Rect(72, 72, 360, 96)
    text.text_fontsize = 12
    page.add_widget(text)
    box = pymupdf.Widget()
    box.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    box.field_name = "agree"
    box.rect = pymupdf.Rect(72, 120, 90, 138)
    page.add_widget(box)
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=PASSWORD,
        owner_pw=PASSWORD + "-owner",
    )
    doc.close()
    return path


def make_plain_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF Editor self-check", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def signature_image() -> QImage:
    """A transparent QImage with a dark stroke: embedded with an 8-bit /SMask."""
    image = QImage(240, 80, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(20, 30, 120), 6))
    painter.drawLine(QPointF(10, 60), QPointF(80, 15))
    painter.drawLine(QPointF(80, 15), QPointF(150, 65))
    painter.drawLine(QPointF(150, 65), QPointF(230, 20))
    painter.end()
    return image


def _export_both(doc: PdfDocument, stem: Path) -> dict[str, str]:
    flat = stem.with_name(stem.name + "-flattened.pdf")
    clean = stem.with_name(stem.name + "-clean.pdf")
    doc.export_copy(flat, ExportOptions())
    doc.export_copy(clean, ExportOptions(flatten_forms=False, flatten_annots=False))
    return {"flattened": str(flat), "clean": str(clean)}


def _reopen(path: str, password: str | None = None) -> pymupdf.Document:
    """``path`` opened with PyMuPDF (authenticated with ``password`` when encrypted).

    ``needs_pass`` is never read after ``authenticate()``: with PyMuPDF 1.28.2 that
    makes the following text extraction return nothing."""
    doc = pymupdf.open(path)
    if doc.is_encrypted:
        _require(bool(doc.authenticate(password or "")), f"{path}: wrong password")
    _require(not doc.is_repaired, f"{path} needed a repair")
    return doc


def check_form(directory: Path) -> dict[str, Any]:
    path = make_form_pdf(directory / "form-aes256.pdf")
    doc = PdfDocument.open(path, password=PASSWORD)
    try:
        _require(doc.is_form and doc.can_fill_forms, "form not fillable")
        widgets = {w.kind: w for w in doc.widgets(0)}
        text, box = widgets[FieldKind.TEXT], widgets[FieldKind.CHECKBOX]
        doc.set_field_value(
            0, text.xref, FIELD_TEXT, name=text.name, unrotated_rect=text.unrotated_rect
        )
        doc.set_field_value(0, box.xref, True, name=box.name, unrotated_rect=box.unrotated_rect)
        image = doc.render(0, 1.5)
        _require(not image.isNull(), "render failed")
        incremental = doc.can_save_incrementally()
        doc.save()
        doc.save(force_full=True)
        exports = _export_both(doc, directory / "form")
    finally:
        doc.close()
    saved = _reopen(str(path), PASSWORD)
    try:
        values = {w.field_name: w.field_value for w in saved[0].widgets()}
    finally:
        saved.close()
    _require(values.get("name") == FIELD_TEXT, f"text value not saved: {values}")
    flat = _reopen(exports["flattened"], PASSWORD)
    try:
        _require(flat.metadata.get("encryption"), "flattened copy not encrypted")
        _require(not list(flat[0].widgets()), "flattened copy still has fields")
        text = flat[0].get_text()
        _require(FIELD_TEXT in text, f"flattened copy lacks the value: {text!a}")
    finally:
        flat.close()
    clean = _reopen(exports["clean"], PASSWORD)
    try:
        _require(len(list(clean[0].widgets())) == 2, "clean copy lost its fields")
    finally:
        clean.close()
    return {
        "file": str(path),
        "password": PASSWORD,
        "render": [image.width(), image.height()],
        "incremental_available": incremental,
        "values": values,
        **exports,
    }


def check_annotations(directory: Path) -> dict[str, Any]:
    path = make_plain_pdf(directory / "annotations.pdf")
    black = (0.0, 0.0, 0.0)
    doc = PdfDocument.open(path)
    try:
        doc.add_annot(
            AnnotSpec(0, AnnotKind.TEXT, FREETEXT, 12.0, black, QRectF(72, 120, 220, 20)),
            fit_height=True,
        )
        doc.add_annot(
            AnnotSpec(
                0, AnnotKind.STAMP, STAMP_GLYPHS["check"], 18.0, black, QRectF(72, 170, 20, 20)
            )
        )
        sig = doc.add_annot(
            AnnotSpec(
                0,
                AnnotKind.SIGNATURE,
                "",
                11.0,
                black,
                QRectF(72, 220, 180, 60),
                image=ImageData.from_qimage(signature_image()),
            )
        )
        smask = doc.fitz.xref_get_key(sig.image_xref, "SMask")
        _require(smask[0] == "xref", f"signature image has no /SMask: {smask}")
        image = doc.render(0, 1.5)
        _require(not image.isNull(), "render failed")
        kinds = sorted(a.kind.value for a in doc.annots(0))
        _require(kinds == ["signature", "stamp", "text"], f"annotations: {kinds}")
        incremental = doc.can_save_incrementally()
        doc.save()
        doc.save(force_full=True)
        exports = _export_both(doc, directory / "annotations")
    finally:
        doc.close()
    reopened = PdfDocument.open(path)
    try:
        _require(not reopened.was_repaired, "saved file needed a repair")
        saved_kinds = sorted(a.kind.value for a in reopened.annots(0))
    finally:
        reopened.close()
    _require(saved_kinds == kinds, f"saved annotations: {saved_kinds}")
    flat = _reopen(exports["flattened"])
    try:
        _require(not list(flat[0].annots()), "flattened copy still has annotations")
        _require("gelé" in flat[0].get_text(), "flattened copy lacks the text")
        _require(bool(flat[0].get_images()), "flattened copy lacks the signature image")
    finally:
        flat.close()
    clean = _reopen(exports["clean"])
    try:
        _require(len(list(clean[0].annots())) == 3, "clean copy lost its annotations")
    finally:
        clean.close()
    return {
        "file": str(path),
        "render": [image.width(), image.height()],
        "incremental_available": incremental,
        "annotations": kinds,
        **exports,
    }
