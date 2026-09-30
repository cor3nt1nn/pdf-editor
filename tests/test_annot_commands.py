"""M3-T2: annotation undo commands (AddAnnotCommand, EditAnnotCommand, DeleteAnnotCommand)."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pymupdf
import pytest
from fixtures import ANNOT_STAMP_NAME, ANNOT_TEXT_NAME, ROTATED_ANNOT_NAME
from PySide6.QtCore import QCoreApplication, QObject, QPointF, QRectF
from PySide6.QtGui import QUndoStack

import pdfeditor.i18n as i18n
from pdfeditor.core.annotations import (
    LINE_HEIGHT_RATIO,
    STAMP_FONT_RATIO,
    STAMP_GLYPHS,
    TEXT_PAD,
    AnnotInfo,
    AnnotKind,
    AnnotSpec,
    fitted_height,
    stamp_rect,
)
from pdfeditor.core.commands import (
    AddAnnotCommand,
    DeleteAnnotCommand,
    DocumentCommand,
    EditAnnotCommand,
)
from pdfeditor.core.document import AnnotError, PdfDocument

BLUE = (0.0, 0.0, 1.0)
RED = (1.0, 0.0, 0.0)
RECT = QRectF(100, 100, 200, 60)


# -- helpers -------------------------------------------------------------------
@pytest.fixture
def blank(tmp_path):
    pdf = pymupdf.open()
    pdf.new_page(width=612, height=792)
    pdf.new_page(width=612, height=792)
    path = tmp_path / "blank.pdf"
    pdf.save(path)
    pdf.close()
    d = PdfDocument.open(path)
    yield d
    d.close()


@pytest.fixture
def ann(annotated_pdf):
    d = PdfDocument.open(annotated_pdf)
    yield d
    d.close()


@pytest.fixture
def french(qapp):
    i18n.install_translators(qapp, "fr")
    yield
    i18n.remove_translators(qapp)


def _text_spec(text: str = "Élève : é à ç €", **kw) -> AnnotSpec:
    args = {"page": 0, "kind": AnnotKind.TEXT, "font_size": 11.0, "color": BLUE, "rect": RECT}
    args.update(kw)
    return AnnotSpec(text=text, **args)


def _stamp_spec(glyph: str = STAMP_GLYPHS["check"]) -> AnnotSpec:
    rect, fs = stamp_rect(QPointF(200, 300), 12.0, glyph)
    return AnnotSpec(page=0, kind=AnnotKind.STAMP, text=glyph, font_size=fs, color=BLUE, rect=rect)


def _render(doc: PdfDocument, page: int = 0) -> bytes:
    with doc.lock:
        pix = doc.fitz[page].get_pixmap(matrix=pymupdf.Matrix(2, 2), annots=True, alpha=False)
    return bytes(pix.samples)


def _rect_tuple(r: QRectF) -> tuple[float, ...]:
    return (r.left(), r.top(), r.right(), r.bottom())


def _same(a: AnnotInfo | None, b: AnnotInfo | None, tol: float = 1e-4) -> bool:
    """Same annotation state (xref ignored: a re-created annotation gets a new one)."""
    if a is None or b is None:
        return a is b
    fields = ("page", "name", "kind", "text", "font_size", "color", "rotate", "hidden")
    if any(getattr(a, f) != getattr(b, f) for f in fields):
        return False
    pairs = zip(_rect_tuple(a.rect), _rect_tuple(b.rect), strict=True)
    return all(abs(x - y) <= tol for x, y in pairs)


# -- add ---------------------------------------------------------------------------
def test_add_text_undo_redo(qtbot, blank: PdfDocument) -> None:
    stack = QUndoStack()
    empty = _render(blank)
    cmd = AddAnnotCommand(blank, _text_spec())
    assert isinstance(cmd, DocumentCommand)
    assert cmd.text() == "Add text"
    uuid.UUID(cmd.name)  # generated in __init__
    assert cmd.spec.name == cmd.name and cmd.page == 0
    with qtbot.waitSignal(blank.page_changed) as blocker:
        stack.push(cmd)
    assert blocker.args == [0]
    created = blank.annot(0, cmd.name)
    assert _same(created, cmd.info)
    # the new text box hugs its single line (width and top edge kept)
    assert created.rect.top() == pytest.approx(100, abs=1e-3)
    assert created.rect.width() == pytest.approx(200, abs=1e-3)
    assert created.rect.height() == pytest.approx(fitted_height(1, 11.0), abs=1e-3)
    drawn = _render(blank)
    assert drawn != empty

    stack.undo()
    assert blank.annot(0, cmd.name) is None
    assert blank.annots(0) == []
    assert _render(blank) == empty

    stack.redo()
    again = blank.annot(0, cmd.name)
    assert _same(again, created)
    assert _render(blank) == drawn
    stack.undo()
    stack.redo()
    assert _same(blank.annot(0, cmd.name), created)


def test_add_stamp_keeps_given_name(blank: PdfDocument) -> None:
    stack = QUndoStack()
    spec = _stamp_spec()
    name = str(uuid.uuid4())
    cmd = AddAnnotCommand(blank, replace(spec, name=name))
    assert cmd.text() == "Add stamp"
    assert cmd.name == name
    stack.push(cmd)
    info = blank.annot(0, name)
    assert info.kind is AnnotKind.STAMP
    # stamps are not height-fitted: the rect is the spec's
    assert _rect_tuple(info.rect) == pytest.approx(_rect_tuple(spec.rect), abs=1e-3)
    stack.undo()
    assert blank.annot(0, name) is None
    stack.redo()
    assert _same(blank.annot(0, name), info)


def test_two_adds_get_distinct_names(blank: PdfDocument) -> None:
    a = AddAnnotCommand(blank, _text_spec())
    b = AddAnnotCommand(blank, _text_spec())
    assert a.name != b.name


# -- edit --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("changes", "label"),
    [
        ({"rect": QRectF(150, 200, 200, 16)}, "Move annotation"),
        ({"rect": QRectF(100, 100, 50, 60)}, "Resize annotation"),  # re-wraps
        ({"text": "Un texte bien plus long qui va passer sur plusieurs lignes"}, "Edit text"),
        ({"font_size": 16.0}, "Change text style"),
        ({"color": RED}, "Change text style"),
        ({"font_size": 9.0, "color": RED}, "Change text style"),
    ],
)
def test_edit_text_annot_undo_redo(ann: PdfDocument, changes: dict, label: str) -> None:
    stack = QUndoStack()
    before = ann.annot(0, ANNOT_TEXT_NAME)
    pixels = _render(ann)
    cmd = EditAnnotCommand(ann, before, **changes)
    assert cmd.text() == label
    assert (cmd.page, cmd.name) == (0, ANNOT_TEXT_NAME)
    stack.push(cmd)
    after = ann.annot(0, ANNOT_TEXT_NAME)
    assert not _same(after, before)
    assert after.xref == before.xref  # edited in place, not re-created
    if "text" in changes:
        assert after.text == changes["text"]
        assert after.rect.height() > before.rect.height()  # refitted to several lines
        assert after.rect.top() == pytest.approx(before.rect.top(), abs=1e-3)
    if "rect" in changes:
        assert _rect_tuple(after.rect) == pytest.approx(_rect_tuple(changes["rect"]), abs=1e-3)
    if "font_size" in changes:
        assert after.font_size == changes["font_size"]
    if "color" in changes:
        assert after.color == changes["color"]
    edited = _render(ann)
    assert edited != pixels

    stack.undo()
    assert _same(ann.annot(0, ANNOT_TEXT_NAME), before)
    assert _render(ann) == pixels
    stack.redo()
    assert _same(ann.annot(0, ANNOT_TEXT_NAME), after)
    assert _render(ann) == edited


def test_edit_text_fit_height_override(ann: PdfDocument) -> None:
    before = ann.annot(0, ANNOT_TEXT_NAME)
    stack = QUndoStack()
    stack.push(EditAnnotCommand(ann, before, text="a\nb\nc", fit_height=False))
    assert ann.annot(0, ANNOT_TEXT_NAME).rect.height() == pytest.approx(before.rect.height())
    stack.undo()
    # a style change can ask for a refit too
    stack.push(EditAnnotCommand(ann, before, font_size=22.0, fit_height=True))
    after = ann.annot(0, ANNOT_TEXT_NAME)
    lines = (after.rect.height() - TEXT_PAD) / (LINE_HEIGHT_RATIO * 22.0)
    assert after.rect.height() != pytest.approx(before.rect.height())
    assert lines == pytest.approx(round(lines), abs=1e-3)
    stack.undo()
    assert _same(ann.annot(0, ANNOT_TEXT_NAME), before)


def test_resize_stamp_undo_restores_glyph_size(ann: PdfDocument) -> None:
    stack = QUndoStack()
    before = ann.annot(0, ANNOT_STAMP_NAME)
    pixels = _render(ann)
    bigger = QRectF(before.rect.left(), before.rect.top(), 30, 30)
    cmd = EditAnnotCommand(ann, before, rect=bigger)
    assert cmd.text() == "Resize annotation"
    stack.push(cmd)
    after = ann.annot(0, ANNOT_STAMP_NAME)
    assert after.font_size == pytest.approx(STAMP_FONT_RATIO * 30)
    stack.undo()
    assert _same(ann.annot(0, ANNOT_STAMP_NAME), before)
    assert _render(ann) == pixels


def test_edit_on_rotated_page(ann: PdfDocument) -> None:
    stack = QUndoStack()
    before = ann.annot(1, ROTATED_ANNOT_NAME)
    pixels = _render(ann, 1)
    stack.push(EditAnnotCommand(ann, before, rect=before.rect.translated(20, 30)))
    moved = ann.annot(1, ROTATED_ANNOT_NAME)
    assert moved.rotate == 90
    assert _rect_tuple(moved.rect) == pytest.approx(
        _rect_tuple(before.rect.translated(20, 30)), abs=1e-3
    )
    stack.undo()
    assert _same(ann.annot(1, ROTATED_ANNOT_NAME), before)
    assert _render(ann, 1) == pixels


def test_edit_without_change_is_rejected(ann: PdfDocument) -> None:
    with pytest.raises(ValueError):
        EditAnnotCommand(ann, ann.annot(0, ANNOT_TEXT_NAME))


# -- delete ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("page", "name"),
    [(0, ANNOT_TEXT_NAME), (0, ANNOT_STAMP_NAME), (1, ROTATED_ANNOT_NAME)],
)
def test_delete_undo_recreates_same_name_and_rendering(
    ann: PdfDocument, page: int, name: str
) -> None:
    stack = QUndoStack()
    before = ann.annot(page, name)
    pixels = _render(ann, page)
    cmd = DeleteAnnotCommand(ann, before)
    assert cmd.text() == "Delete annotation"
    assert (cmd.page, cmd.name) == (page, name)
    stack.push(cmd)
    assert ann.annot(page, name) is None
    gone = _render(ann, page)
    assert gone != pixels
    stack.undo()
    assert _same(ann.annot(page, name), before)
    assert _render(ann, page) == pixels
    stack.redo()
    assert ann.annot(page, name) is None
    assert _render(ann, page) == gone


# -- saves between redo and undo ----------------------------------------------------
def test_commands_survive_full_save(ann: PdfDocument) -> None:
    stack = QUndoStack()
    add = AddAnnotCommand(ann, _text_spec("nouveau", rect=QRectF(100, 400, 200, 20)))
    stack.push(add)
    created = ann.annot(0, add.name)
    ann.save(force_full=True)
    assert _same(ann.annot(0, add.name), created)

    text = ann.annot(0, ANNOT_TEXT_NAME)
    stack.push(EditAnnotCommand(ann, text, text="modifié", color=RED))
    ann.save(force_full=True)
    stamp = ann.annot(0, ANNOT_STAMP_NAME)
    stack.push(DeleteAnnotCommand(ann, stamp))
    ann.save(force_full=True)
    pixels = _render(ann)

    stack.undo()  # delete
    assert _same(ann.annot(0, ANNOT_STAMP_NAME), stamp)
    ann.save(force_full=True)
    stack.undo()  # edit
    assert _same(ann.annot(0, ANNOT_TEXT_NAME), text)
    ann.save(force_full=True)
    stack.undo()  # add
    assert ann.annot(0, add.name) is None
    ann.save(force_full=True)

    stack.redo()
    stack.redo()
    stack.redo()
    assert _same(ann.annot(0, add.name), created)
    assert ann.annot(0, ANNOT_TEXT_NAME).text == "modifié"
    assert ann.annot(0, ANNOT_STAMP_NAME) is None
    assert _render(ann) == pixels
    ann.save()
    reopened = PdfDocument.open(ann.path)
    try:
        assert _same(reopened.annot(0, add.name), created)
        assert reopened.annot(0, ANNOT_STAMP_NAME) is None
    finally:
        reopened.close()


# -- apply_now ---------------------------------------------------------------------
def test_apply_now_reports_vanished_annot_without_touching_stack(ann: PdfDocument) -> None:
    stack = QUndoStack()
    info = ann.annot(0, ANNOT_TEXT_NAME)
    ann.delete_annot(0, ANNOT_TEXT_NAME)  # e.g. removed behind the tool's back
    for cmd in (
        EditAnnotCommand(ann, info, text="x"),
        EditAnnotCommand(ann, info, rect=info.rect.translated(5, 5)),
        DeleteAnnotCommand(ann, info),
    ):
        with pytest.raises(AnnotError):
            cmd.apply_now()
    assert stack.count() == 0


def test_apply_now_then_push_applies_once(ann: PdfDocument) -> None:
    stack = QUndoStack()
    changed: list[int] = []
    ann.page_changed.connect(changed.append)
    before = ann.annot(0, ANNOT_TEXT_NAME)
    cmd = EditAnnotCommand(ann, before, text="une fois")
    cmd.apply_now()
    assert ann.annot(0, ANNOT_TEXT_NAME).text == "une fois"
    stack.push(cmd)  # the push's redo is skipped
    assert changed == [0]
    assert stack.count() == 1
    stack.undo()
    assert _same(ann.annot(0, ANNOT_TEXT_NAME), before)
    stack.redo()
    assert ann.annot(0, ANNOT_TEXT_NAME).text == "une fois"

    add = AddAnnotCommand(ann, _stamp_spec())
    add.apply_now()
    stack.push(add)
    assert len([a for a in ann.annots(0) if a.name == add.name]) == 1


def test_apply_now_add_not_permitted(owner_locked_pdf) -> None:
    doc = PdfDocument.open(owner_locked_pdf)
    try:
        with pytest.raises(AnnotError):
            AddAnnotCommand(doc, _text_spec()).apply_now()
    finally:
        doc.close()


# -- texts -------------------------------------------------------------------------
def _labels(doc: PdfDocument) -> list[str]:
    text = doc.annot(0, ANNOT_TEXT_NAME)
    stamp = doc.annot(0, ANNOT_STAMP_NAME)
    return [
        AddAnnotCommand(doc, _text_spec()).text(),
        AddAnnotCommand(doc, _stamp_spec()).text(),
        EditAnnotCommand(doc, text, text="x").text(),
        EditAnnotCommand(doc, text, rect=text.rect.translated(3, 4)).text(),
        EditAnnotCommand(doc, stamp, rect=QRectF(0, 0, 40, 40)).text(),
        EditAnnotCommand(doc, text, color=RED).text(),
        DeleteAnnotCommand(doc, text).text(),
    ]


def test_command_texts_english(ann: PdfDocument) -> None:
    assert _labels(ann) == [
        "Add text",
        "Add stamp",
        "Edit text",
        "Move annotation",
        "Resize annotation",
        "Change text style",
        "Delete annotation",
    ]
    owner = QObject()
    stack = QUndoStack()
    undo = stack.createUndoAction(owner, "Undo")
    stack.push(AddAnnotCommand(ann, _text_spec()))
    assert undo.text() == "Undo Add text"


def test_command_texts_french(french, ann: PdfDocument) -> None:
    assert _labels(ann) == [
        "l’ajout du texte",
        "l’ajout du tampon",
        "la modification du texte",
        "le déplacement de l’annotation",
        "le redimensionnement de l’annotation",
        "le changement de style du texte",
        "la suppression de l’annotation",
    ]
    owner = QObject()
    stack = QUndoStack()
    undo = stack.createUndoAction(owner, QCoreApplication.translate("MainWindow", "Undo"))
    stack.push(AddAnnotCommand(ann, _text_spec()))
    assert undo.text() == "Annuler l’ajout du texte"
