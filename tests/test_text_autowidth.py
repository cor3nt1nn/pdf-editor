"""Deviation 164: text boxes shrink-wrap their text width (auto width), a resized box
keeps its width (/PDFEditorFixedWidth), boxes of other programs keep theirs."""

from __future__ import annotations

import pymupdf
import pytest
import test_annot_tools
from fixtures import CROP_BOX, FOREIGN_RECT, FOREIGN_TEXT, WORD_SHAPES, WORD_TABLE_X, WORD_TABLE_Y
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QUndoStack
from test_annot_tools import EMPTY, _annots, _click, _create_text, _drag, _editor

from pdfeditor.core.annotations import (
    FIXED_WIDTH_KEY,
    MAX_WIDTH_KEY,
    TEXT_PAGE_MARGIN,
    AnnotKind,
    AnnotSpec,
    fitted_width,
    natural_text_width,
)
from pdfeditor.core.commands import AddAnnotCommand, EditAnnotCommand
from pdfeditor.core.document import PdfDocument

# The text tool's window fixtures (MainWindow on the word_form page, Text tool active).
window = test_annot_tools.window
word_window = test_annot_tools.word_window

NAME = "Jean Dupont"
LONG = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor " * 3
BLACK = (0.0, 0.0, 0.0)


def _pdf(path, *, rotation: int = 0, cropbox: bool = False):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    if cropbox:
        page.set_cropbox(pymupdf.Rect(CROP_BOX))
    page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


def _auto(text: str = NAME, rect: QRectF | None = None, **kw) -> AnnotSpec:
    rect = QRectF(100, 100, 11, 15) if rect is None else rect
    return AnnotSpec(0, AnnotKind.TEXT, text, 11.0, BLACK, rect, fixed_width=False, **kw)


def _key(doc: PdfDocument, xref: int, key: str) -> tuple[str, str]:
    with doc.lock:
        return doc.fitz.xref_get_key(xref, key)


@pytest.fixture
def blank(tmp_path):
    d = PdfDocument.open(_pdf(tmp_path / "blank.pdf"))
    yield d
    d.close()


# -- core ----------------------------------------------------------------------------
def test_natural_width_uses_helvetica_metrics() -> None:
    measured = pymupdf.get_text_length(NAME, fontname="helv", fontsize=11)
    assert natural_text_width(NAME, 11) == pytest.approx(measured + 2.0)
    assert natural_text_width("a\n" + NAME + "\r\nb", 11) == pytest.approx(measured + 2.0)
    assert fitted_width("", 11, 300) == pytest.approx(11)  # one em at least
    assert fitted_width(LONG, 11, 150) == pytest.approx(150)


def test_auto_box_hugs_its_text_and_wraps_at_page_edge(blank: PdfDocument) -> None:
    info = blank.add_annot(_auto(), fit_height=True)
    assert not info.fixed_width
    assert info.rect.width() == pytest.approx(natural_text_width(NAME, 11), abs=0.01)
    assert info.rect.left() == pytest.approx(100)
    assert info.rect.height() == pytest.approx(11 * 1.2 + 2, abs=0.01)  # one line
    assert _key(blank, info.xref, FIXED_WIDTH_KEY) == ("bool", "false")
    assert _key(blank, info.xref, MAX_WIDTH_KEY)[0] == "null"
    wide = blank.update_annot(0, info.name, text=LONG, fit_height=True)
    assert wide.rect.width() == pytest.approx(612 - 100 - TEXT_PAGE_MARGIN, abs=0.01)
    assert wide.rect.height() > 2 * 11 * 1.2  # wrapped
    short = blank.update_annot(0, info.name, text="ab", fit_height=True)
    assert short.rect.width() == pytest.approx(fitted_width("ab", 11, 400), abs=0.01)
    # The text is fully drawn on one line (nothing clipped or wrapped).
    with blank.lock:
        page = blank.fitz[0]
        annot = page.load_annot(short.xref)
        assert annot.get_text().strip() == "ab"


def test_auto_box_wraps_at_its_stored_max_width(blank: PdfDocument) -> None:
    info = blank.add_annot(_auto(max_width=96.0), fit_height=True)
    assert info.max_width == pytest.approx(96)
    assert info.rect.width() == pytest.approx(natural_text_width(NAME, 11), abs=0.01)
    wide = blank.update_annot(0, info.name, text=LONG, fit_height=True)
    assert wide.rect.width() == pytest.approx(96, abs=0.01)
    assert wide.max_width == pytest.approx(96)


def test_fixed_box_keeps_width_and_flag_survives_save(blank: PdfDocument, tmp_path) -> None:
    info = blank.add_annot(_auto(), fit_height=True)
    resized = QRectF(info.rect.left(), info.rect.top(), 150, info.rect.height())
    stack = QUndoStack()
    stack.push(EditAnnotCommand(blank, info, rect=resized, fit_height=True, fixed_width=True))
    fixed = blank.annot(0, info.name)
    assert fixed.fixed_width and fixed.rect.width() == pytest.approx(150, abs=0.01)
    edited = blank.update_annot(0, info.name, text="x", fit_height=True)
    assert edited.rect.width() == pytest.approx(150, abs=0.01)
    blank.update_annot(0, info.name, text=NAME, fit_height=True)
    out = tmp_path / "saved.pdf"
    blank.save_as(out)
    reopened = PdfDocument.open(out)
    try:
        again = reopened.annot(0, info.name)
        assert again.fixed_width
        assert reopened.update_annot(0, info.name, text="y", fit_height=True).rect.width() == (
            pytest.approx(150, abs=0.01)
        )
    finally:
        reopened.close()
    # Undo of the resize brings back the auto mode (and width).
    stack.undo()
    back = blank.annot(0, info.name)
    assert not back.fixed_width
    assert back.rect.width() == pytest.approx(info.rect.width(), abs=0.01)
    stack.redo()
    assert blank.annot(0, info.name).fixed_width


def test_auto_flag_survives_save_and_reopen(blank: PdfDocument, tmp_path) -> None:
    info = blank.add_annot(_auto(max_width=80.0), fit_height=True)
    out = tmp_path / "auto.pdf"
    blank.save_as(out)
    reopened = PdfDocument.open(out)
    try:
        again = reopened.annot(0, info.name)
        assert not again.fixed_width and again.max_width == pytest.approx(80)
        longer = reopened.update_annot(0, info.name, text=NAME + " Junior", fit_height=True)
        assert longer.rect.width() == pytest.approx(80, abs=0.01)
    finally:
        reopened.close()


def test_foreign_text_box_keeps_its_width(annotated_pdf) -> None:
    doc = PdfDocument.open(annotated_pdf)
    try:
        foreign = next(a for a in doc.annots(0) if a.text == FOREIGN_TEXT)
        assert foreign.fixed_width
        edited = doc.update_annot(0, foreign.name, text="Hi", fit_height=True)
        assert edited.rect.width() == pytest.approx(FOREIGN_RECT[2] - FOREIGN_RECT[0], abs=0.01)
        assert edited.fixed_width
    finally:
        doc.close()


def test_stamps_get_no_width_mode(blank: PdfDocument) -> None:
    spec = AnnotSpec(0, AnnotKind.STAMP, "4", 10.8, BLACK, QRectF(50, 50, 12, 12))
    info = blank.add_annot(spec)
    assert _key(blank, info.xref, FIXED_WIDTH_KEY)[0] == "null"
    assert info.rect.width() == pytest.approx(12)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_auto_width_on_rotated_page(tmp_path, rotation: int) -> None:
    doc = PdfDocument.open(_pdf(tmp_path / "r.pdf", rotation=rotation, cropbox=True))
    try:
        size = doc.page_size(0)
        info = doc.add_annot(_auto(), fit_height=True)
        assert info.rotate == rotation
        # Page space: the text reads upright, its width is horizontal.
        assert info.rect.width() == pytest.approx(natural_text_width(NAME, 11), abs=0.01)
        assert info.rect.topLeft() == QPointF(100, 100)
        wide = doc.update_annot(0, info.name, text=LONG, fit_height=True)
        assert wide.rect.left() == pytest.approx(100, abs=0.01)
        assert wide.rect.width() == pytest.approx(size.width() - 100 - TEXT_PAGE_MARGIN, abs=0.01)
    finally:
        doc.close()


def test_auto_width_follows_text_direction_after_page_rotation(tmp_path) -> None:
    doc = PdfDocument.open(_pdf(tmp_path / "p.pdf"))
    try:
        info = doc.add_annot(_auto("ab"), fit_height=True)
        with doc.lock:
            doc.fitz[0].set_rotation(90)
        turned = doc.annot(0, info.name)
        assert turned.rotate == 0
        longer = doc.update_annot(0, info.name, text=NAME, fit_height=True)
        # The text now runs vertically on screen: the page-space height is its width.
        assert longer.rect.height() == pytest.approx(natural_text_width(NAME, 11), abs=0.01)
        assert longer.rect.width() == pytest.approx(turned.rect.width(), abs=0.01)
    finally:
        doc.close()


def test_add_undo_redo_keeps_auto_mode(blank: PdfDocument) -> None:
    stack = QUndoStack()
    stack.push(AddAnnotCommand(blank, _auto(max_width=90.0)))
    (info,) = blank.annots(0)
    stack.undo()
    assert blank.annots(0) == []
    stack.redo()
    (again,) = blank.annots(0)
    assert again.rect == info.rect and not again.fixed_width
    assert again.max_width == pytest.approx(90)
    stack.push(EditAnnotCommand(blank, again, text=NAME + " Junior"))
    longer = blank.annot(0, info.name)
    assert longer.rect.width() > info.rect.width()
    stack.undo()
    assert blank.annot(0, info.name).rect == info.rect


# -- text tool -----------------------------------------------------------------------
def _commit(qtbot, w) -> None:
    qtbot.keyClick(_editor(w), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)


def _fs(w) -> float:
    return w.text_tool.style()[0]


def test_tool_short_name_on_empty_space(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w, NAME)
    assert not info.fixed_width and info.max_width == 0
    assert info.rect.width() == pytest.approx(natural_text_width(NAME, _fs(w)), abs=0.5)
    assert info.rect.left() == pytest.approx(EMPTY.x(), abs=1.5)


def test_tool_long_text_wraps_at_page_margin(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, EMPTY)
    _editor(w).setPlainText(LONG)
    _commit(qtbot, w)
    (info,) = _annots(w)
    page_w = w.document_view.document.page_size(0).width()
    assert info.rect.right() == pytest.approx(page_w - TEXT_PAGE_MARGIN, abs=0.5)
    assert info.rect.height() > 3 * _fs(w)


def test_tool_short_and_long_text_in_cell(qtbot, word_window) -> None:
    w = word_window
    cell = QPointF(120, (WORD_TABLE_Y[0] + WORD_TABLE_Y[1]) / 2)
    info = _create_text(qtbot, w, NAME, at=cell)
    assert info.rect.left() == pytest.approx(WORD_TABLE_X[0] + 2, abs=0.5)
    assert info.rect.width() == pytest.approx(natural_text_width(NAME, _fs(w)), abs=0.5)
    assert info.max_width == pytest.approx(100 - 4, abs=0.6)
    other = QPointF(220, (WORD_TABLE_Y[1] + WORD_TABLE_Y[2]) / 2)
    _click(qtbot, w, other)
    _editor(w).setPlainText(LONG)
    _commit(qtbot, w)
    wide = next(a for a in _annots(w) if a.text == LONG)
    assert wide.rect.width() == pytest.approx(100 - 4, abs=0.6)


def test_tool_short_and_long_text_on_underline(qtbot, word_window) -> None:
    w = word_window
    ux0, uy0, ux1, _uy1 = WORD_SHAPES["underline_rect"]
    info = _create_text(qtbot, w, NAME, at=QPointF(150, uy0 - 5))
    assert info.rect.left() == pytest.approx(ux0 + 2, abs=0.5)
    assert info.rect.width() == pytest.approx(natural_text_width(NAME, _fs(w)), abs=0.5)
    w.document_view.annot_selection.clear()
    w.text_tool.delete(info)
    _click(qtbot, w, QPointF(150, uy0 - 5))
    _editor(w).setPlainText(LONG)
    _commit(qtbot, w)
    wide = next(a for a in _annots(w) if a.text == LONG)
    assert wide.rect.right() == pytest.approx(ux1, abs=0.6)  # the rule's length


def test_live_editor_width_follows_typing(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, EMPTY)
    editor = _editor(w)
    scale = w.page_view.view_scale
    start = editor.width()
    qtbot.keyClicks(editor, NAME)
    grown = editor.width()
    assert grown > start
    # The box's Helvetica width, or more when the editor's own font needs it (the
    # offscreen test platform's fallback font is much wider than Arial).
    helvetica = natural_text_width(NAME, _fs(w)) * scale
    qt_needs = editor.fontMetrics().horizontalAdvance(NAME) + 2 * 1 + 2 + editor.cursorWidth()
    assert grown == pytest.approx(max(helvetica, qt_needs), abs=1.5)
    for _ in range(len(NAME) - 2):
        qtbot.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.width() < grown
    editor.setPlainText(LONG)
    limit = w.document_view.annot_editor.anchor.width_limit
    assert editor.width() <= round(limit * scale) + 1
    assert editor.height() > 2 * _fs(w) * scale  # wrapped
    _commit(qtbot, w)


def test_editing_auto_box_refits_width(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w, "ab")
    w.text_tool.click_selected(info)
    assert w.document_view.annot_editor.anchor.auto_width
    qtbot.keyClicks(_editor(w), " " + NAME)
    _commit(qtbot, w)
    edited = w.document_view.document.annot(0, info.name)
    assert edited.rect.width() == pytest.approx(natural_text_width("ab " + NAME, _fs(w)), abs=0.5)
    assert w.undo_stack.count() == 2
    w.undo_stack.undo()
    assert w.document_view.document.annot(0, info.name).rect == info.rect


def test_resize_makes_box_fixed_width(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w, NAME, at=QPointF(100, 600))
    _drag(qtbot, w, info.rect.bottomRight(), 60, 0)
    assert w.undo_stack.count() == 2
    resized = w.document_view.document.annot(0, info.name)
    assert resized.fixed_width
    assert resized.rect.width() > info.rect.width() + 10
    w.text_tool.click_selected(resized)
    assert not w.document_view.annot_editor.anchor.auto_width
    _editor(w).setPlainText("x")
    _commit(qtbot, w)
    edited = w.document_view.document.annot(0, info.name)
    assert edited.rect.width() == pytest.approx(resized.rect.width(), abs=0.01)
    w.undo_stack.undo()
    w.undo_stack.undo()
    back = w.document_view.document.annot(0, info.name)
    assert not back.fixed_width
    assert back.rect.width() == pytest.approx(info.rect.width(), abs=0.01)
    w.undo_stack.redo()
    assert w.document_view.document.annot(0, info.name).fixed_width
