"""M3 review fixes in the annotation tools: zoomed-out hit testing, other mouse buttons,
Delete scope, undo/Escape during a drag, text beside checkboxes, clamping, shortcut
keys in widgets, foreign FreeText through the UI and encrypted files."""

from __future__ import annotations

import pymupdf
import pytest
from fixtures import (
    FOREIGN_RECT,
    LOCKED_SIGNATURE_RECT,
    MUPDF_STAMP_RECT,
    PASSWORD,
    SIGNED_RECT,
    WORD_SHAPES,
)
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit

from pdfeditor.constants import ZoomMode
from pdfeditor.core import annotations
from pdfeditor.core.annotations import AnnotKind, AnnotSpec
from pdfeditor.core.commands import AddAnnotCommand, EditAnnotCommand
from pdfeditor.core.document import PdfDocument
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.overlays.annot_items import AnnotHandleItem, Handle

NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
RIGHT = Qt.MouseButton.RightButton
EMPTY = QPointF(400, 600)


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def word_window(window, word_form_pdf):
    assert window.open_file(str(word_form_pdf))
    window.act_text_tool.trigger()
    return window


def _vp(w: MainWindow, point: QPointF, page: int = 0) -> QPoint:
    pv = w.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _click(qtbot, w: MainWindow, point: QPointF) -> None:
    qtbot.mouseClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, point))


def _editor(w: MainWindow) -> QPlainTextEdit:
    return w.document_view.annot_editor.editor


def _messages(tool) -> list[str]:
    out: list[str] = []
    tool.message.connect(out.append)
    return out


def _add_text(w: MainWindow, text: str = "abc", rect: QRectF | None = None):
    rect = rect or QRectF(100, 600, 180, 14)
    doc = w.document_view.document
    command = AddAnnotCommand(doc, AnnotSpec(0, AnnotKind.TEXT, text, 11, (0, 0, 0), rect))
    w.document_view.push(command)
    return doc.annot(0, command.name)


def _drag(qtbot, w: MainWindow, point: QPointF, dx: int, dy: int = 0) -> None:
    vp = w.page_view.viewport()
    start = _vp(w, point)
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    qtbot.mouseMove(vp, start + QPoint(dx // 2, dy // 2))
    qtbot.mouseMove(vp, start + QPoint(dx, dy))
    qtbot.mouseRelease(vp, LEFT, NO_MOD, start + QPoint(dx, dy))


# -- finding 4: small annotations when zoomed out ---------------------------------------------
def test_handle_at_prefers_body_of_small_boxes(qtbot) -> None:
    item = AnnotHandleItem(QRectF(100, 100, 180, 15))
    scale = 0.25 * 96 / 72  # 25 %
    tol = 6 / scale  # 18 pt: more than the box height
    assert item.handle_at(QPointF(190, 107.5), tol) is None  # centre: body
    assert item.handle_at(QPointF(100, 100), tol) is Handle.TOP_LEFT
    assert item.handle_at(QPointF(281, 116), tol) is Handle.BOTTOM_RIGHT
    assert item.handle_at(QPointF(190, 96), tol) is Handle.TOP  # just outside
    assert item.handle_at(QPointF(190, 101), tol) is Handle.TOP  # on the edge


@pytest.mark.parametrize("zoom", ["fit_page", 25])
def test_small_box_movable_and_editable_when_zoomed_out(qtbot, word_window, zoom) -> None:
    w = word_window
    info = _add_text(w)
    if zoom == "fit_page":
        w.page_view.set_zoom_mode(ZoomMode.FIT_PAGE)
    else:
        w.page_view.set_zoom_percent(zoom)
    qtbot.wait(20)
    assert w.page_view.view_scale < 0.8
    selection = w.document_view.annot_selection
    _click(qtbot, w, info.rect.center())
    assert selection.current is not None and selection.current.name == info.name
    count = w.undo_stack.count()
    _drag(qtbot, w, info.rect.center(), 40, 0)
    assert w.undo_stack.count() == count + 1
    moved = w.document_view.document.annot(0, info.name)
    assert moved.rect.height() == pytest.approx(info.rect.height(), abs=1e-3)
    assert moved.rect.left() > info.rect.left() + 10
    _click(qtbot, w, moved.rect.center())
    assert w.document_view.annot_editor.is_open
    w.document_view.annot_editor.cancel()
    # The bottom-right corner still resizes.
    _drag(qtbot, w, moved.rect.bottomRight(), 30, 0)
    resized = w.document_view.document.annot(0, info.name)
    assert resized.rect.width() > moved.rect.width() + 10
    assert resized.rect.left() == pytest.approx(moved.rect.left(), abs=1e-3)


# -- finding 9: other mouse buttons during a drag ----------------------------------------------
def test_right_press_during_left_drag_is_ignored(qtbot, word_window) -> None:
    w = word_window
    info = _add_text(w)
    selection = w.document_view.annot_selection
    count = w.undo_stack.count()
    vp = w.page_view.viewport()
    start = _vp(w, info.rect.center())
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    qtbot.mouseMove(vp, start + QPoint(20, 0))
    qtbot.mousePress(vp, RIGHT, NO_MOD, start + QPoint(20, 0))
    qtbot.mouseRelease(vp, RIGHT, NO_MOD, start + QPoint(20, 0))
    assert selection.current is not None and selection.current.name == info.name
    assert selection.item.ghost is not None  # still dragging
    qtbot.mouseMove(vp, start + QPoint(40, 0))
    qtbot.mouseRelease(vp, LEFT, NO_MOD, start + QPoint(40, 0))
    assert w.undo_stack.count() == count + 1
    assert isinstance(w.undo_stack.command(count), EditAnnotCommand)
    assert len(w.document_view.document.annots(0)) == 1


# -- finding 10: Delete scope --------------------------------------------------------------------
def test_delete_in_thumbnails_does_not_delete_annotation(qtbot, word_window) -> None:
    w = word_window
    info = _add_text(w)
    _click(qtbot, w, info.rect.center())
    assert w.document_view.annot_selection.current is not None
    w.thumbnails_dock.show()
    w.thumbnails.setFocus()
    qtbot.waitUntil(lambda: w.thumbnails.hasFocus())
    for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
        qtbot.keyClick(w.thumbnails, key)
    assert w.document_view.document.annot(0, info.name) is not None
    w.zoom_widget.setFocus()
    qtbot.keyClick(w.zoom_widget, Qt.Key.Key_Delete)
    assert w.document_view.document.annot(0, info.name) is not None
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view.viewport(), Qt.Key.Key_Delete)
    assert w.document_view.document.annot(0, info.name) is None
    w.undo_stack.undo()
    _click(qtbot, w, info.rect.center())
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view.viewport(), Qt.Key.Key_Backspace)
    assert w.document_view.document.annot(0, info.name) is None


# -- finding 11: undo / Escape during a drag ---------------------------------------------------
def test_undo_during_drag_drops_it_silently(qtbot, word_window) -> None:
    w = word_window
    info = _add_text(w)
    messages = _messages(w.text_tool)
    vp = w.page_view.viewport()
    start = _vp(w, info.rect.center())
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    qtbot.mouseMove(vp, start + QPoint(20, 0))
    w.act_undo.trigger()  # Ctrl+Z: the Add is undone
    assert w.document_view.document.annots(0) == []
    count, index = w.undo_stack.count(), w.undo_stack.index()
    qtbot.mouseMove(vp, start + QPoint(40, 0))
    qtbot.mouseRelease(vp, LEFT, NO_MOD, start + QPoint(40, 0))
    assert messages == []
    assert (w.undo_stack.count(), w.undo_stack.index()) == (count, index)
    assert w.document_view.document.annots(0) == []


def test_escape_during_drag_cancels(qtbot, word_window) -> None:
    w = word_window
    info = _add_text(w)
    count = w.undo_stack.count()
    vp = w.page_view.viewport()
    start = _vp(w, info.rect.center())
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    qtbot.mouseMove(vp, start + QPoint(30, 0))
    assert w.document_view.annot_selection.item.ghost is not None
    qtbot.keyClick(vp, Qt.Key.Key_Escape)
    assert w.document_view.annot_selection.item.ghost is None
    qtbot.mouseRelease(vp, LEFT, NO_MOD, start + QPoint(30, 0))
    assert w.undo_stack.count() == count
    assert w.document_view.document.annot(0, info.name).rect == info.rect


# -- finding 8: text beside / in a checkbox --------------------------------------------------
@pytest.mark.parametrize("where", ["beside", "inside"])
def test_text_near_checkbox_gets_a_normal_box(qtbot, word_window, where) -> None:
    w = word_window
    x0, y0, x1, y1 = WORD_SHAPES["checkbox_12"]
    point = QPointF(x1 + 3, (y0 + y1) / 2) if where == "beside" else QPointF(126, 254)
    _click(qtbot, w, point)
    editor = w.document_view.annot_editor
    assert editor.is_open
    assert editor.anchor.width_limit >= 36
    editor.cancel()


def test_stamp_still_snaps_to_checkbox_within_reach(qtbot, window, word_form_pdf) -> None:
    w = window
    assert w.open_file(str(word_form_pdf))
    w.act_stamp_check.trigger()
    x0, y0, x1, y1 = WORD_SHAPES["checkbox_12"]
    _click(qtbot, w, QPointF(x1 + 3, (y0 + y1) / 2))
    (stamp,) = w.document_view.document.annots(0)
    assert stamp.rect.width() == pytest.approx(x1 - x0, abs=0.01)


# -- finding 12: clamping ------------------------------------------------------------------------
def test_text_near_page_bottom_stays_on_page(qtbot, word_window) -> None:
    w = word_window
    size = w.document_view.document.page_size(0)
    _click(qtbot, w, QPointF(300, size.height() - 2))
    rect = w.document_view.annot_editor.anchor.rect
    assert rect.bottom() <= size.height() + 1e-6
    w.document_view.annot_editor.cancel()


def test_stamp_near_page_corner_stays_on_page(qtbot, window, word_form_pdf) -> None:
    w = window
    assert w.open_file(str(word_form_pdf))
    w.act_stamp_check.trigger()
    size = w.document_view.document.page_size(0)
    _click(qtbot, w, QPointF(size.width() - 1, size.height() - 1))
    (stamp,) = w.document_view.document.annots(0)
    page = QRectF(QPointF(0, 0), size)
    assert page.contains(stamp.rect)


def test_resize_is_clamped_to_page(qtbot, word_window) -> None:
    w = word_window
    size = w.document_view.document.page_size(0)
    info = _add_text(w, rect=QRectF(size.width() - 120, 600, 100, 14))
    _click(qtbot, w, info.rect.center())
    _drag(qtbot, w, info.rect.bottomRight(), 300, 0)
    resized = w.document_view.document.annot(0, info.name)
    assert resized.rect.right() <= size.width() + 1e-3
    assert resized.rect.width() > info.rect.width()


# -- shortcut keys typed in widgets ---------------------------------------------------------------
def test_tool_keys_typed_in_editor_spin_and_zoom_keep_tool(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, EMPTY)
    qtbot.keyClicks(_editor(w), "123THF")
    assert w.tool_manager.active_tool is w.text_tool
    assert _editor(w).toPlainText() == "123THF"
    w.document_view.annot_editor.cancel()
    w.font_size_spin.setFocus()
    w.font_size_spin.lineEdit().selectAll()
    qtbot.keyClicks(w.font_size_spin.lineEdit(), "12")
    assert w.tool_manager.active_tool is w.text_tool
    w.zoom_widget.setFocus()
    w.zoom_widget.lineEdit().selectAll()
    qtbot.keyClicks(w.zoom_widget.lineEdit(), "123THF")
    assert w.tool_manager.active_tool is w.text_tool
    assert w.zoom_widget.lineEdit().text() == "123THF"


# -- finding 15 through the UI ---------------------------------------------------------------------
def test_hover_and_save_clean_foreign_document_writes_nothing(qtbot, window, annotated_pdf) -> None:
    w = window
    original = annotated_pdf.read_bytes()
    assert w.open_file(str(annotated_pdf))
    w.act_text_tool.trigger()
    x0, y0, x1, y1 = FOREIGN_RECT
    for p in (QPointF((x0 + x1) / 2, (y0 + y1) / 2), EMPTY, QPointF(126, 254)):
        qtbot.mouseMove(w.page_view.viewport(), _vp(w, p))
    assert w.save()
    assert annotated_pdf.read_bytes() == original


def test_hover_and_save_clean_signed_document_writes_nothing(qtbot, window, signed_pdf) -> None:
    w = window
    original = signed_pdf.read_bytes()
    assert w.open_file(str(signed_pdf))
    w.act_text_tool.trigger()
    for x0, y0, x1, y1 in (SIGNED_RECT, MUPDF_STAMP_RECT, LOCKED_SIGNATURE_RECT):
        qtbot.mouseMove(w.page_view.viewport(), _vp(w, QPointF((x0 + x1) / 2, (y0 + y1) / 2)))
        qtbot.mouseMove(w.page_view.viewport(), _vp(w, QPointF(x1 - 1, y1 - 1)))
    qtbot.mouseMove(w.page_view.viewport(), _vp(w, EMPTY))
    assert w.save()
    assert signed_pdf.read_bytes() == original


def test_move_foreign_then_click_again_opens_editor(qtbot, window, annotated_pdf) -> None:
    w = window
    assert w.open_file(str(annotated_pdf))
    w.act_text_tool.trigger()
    x0, y0, x1, y1 = FOREIGN_RECT
    centre = QPointF((x0 + x1) / 2, (y0 + y1) / 2)
    _click(qtbot, w, centre)
    selection = w.document_view.annot_selection
    assert annotations.is_synthetic(selection.current.name)
    _drag(qtbot, w, centre, 30, 0)
    real = selection.current.name
    assert not annotations.is_synthetic(real)
    moved = w.document_view.document.annot(0, real)
    _click(qtbot, w, moved.rect.center())
    assert w.document_view.annot_editor.is_open
    w.document_view.annot_editor.cancel()
    w.undo_stack.undo()
    assert w.document_view.document.annot(0, real).rect.left() == pytest.approx(x0, abs=0.01)


# -- encrypted files ------------------------------------------------------------------------------
def test_encrypted_file_annotations_save_and_reopen(tmp_path) -> None:
    path = tmp_path / "enc.pdf"
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=PASSWORD,
        owner_pw="owner-" + PASSWORD,
        permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ANNOTATE,
    )
    pdf.close()
    doc = PdfDocument.open(path, password=PASSWORD)
    assert doc.is_encrypted and doc.can_annotate
    stack = QUndoStack()
    add = AddAnnotCommand(
        doc, AnnotSpec(0, AnnotKind.TEXT, "Élève chiffré", 11, (0, 0, 0), QRectF(72, 72, 200, 14))
    )
    stack.push(add)
    doc.save()  # incremental
    info = doc.annot(0, add.name)
    stack.push(EditAnnotCommand(doc, info, rect=info.rect.translated(0, 50)))
    doc.save(force_full=True)
    for _ in range(2):
        reopened = PdfDocument.open(path, password=PASSWORD)
        try:
            assert reopened.is_encrypted
            got = reopened.annot(0, add.name)
            assert got is not None and got.text == "Élève chiffré"
            assert got.rect.top() == pytest.approx(info.rect.top() + 50, abs=0.01)
        finally:
            reopened.close()
        stack.undo()
        stack.redo()
        doc.save()
    doc.close()
