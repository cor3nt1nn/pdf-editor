"""M2-T8 hardening: many fields, odd widgets, save failure and undo with an open editor."""

from __future__ import annotations

import time

import pytest
from fixtures import MANY_FIELDS_PER_PAGE, ODD_RECTS
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QComboBox, QLineEdit, QMessageBox

import pdfeditor.core.document as document_module
from pdfeditor.core.document import PdfDocument, SaveError
from pdfeditor.core.forms import FieldKind, read_widgets, tab_order
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.base import ToolEvent

#: FieldLayer rebuild budget for 200 widgets (docs/M2_PLAN.md: < 100 ms). Measured on
#: the development machine: about 3 ms with the widget cache warm (the normal case: a
#: field edit only re-reads its page), 65-80 ms cold (MuPDF parses the 200 widgets of 4
#: pages). The warm rebuild is held to the plan's number; the cold one gets a looser
#: bound so that a slow CI machine does not fail on MuPDF parsing time.
REBUILD_BUDGET_S = 0.100
COLD_REBUILD_BUDGET_S = 0.500


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
def read_counter(monkeypatch):
    """Counts the calls PdfDocument makes to forms.read_widgets (the pymupdf scan)."""
    calls: list[int] = []

    def counting(fitz_doc, page_index):
        calls.append(page_index)
        return read_widgets(fitz_doc, page_index)

    monkeypatch.setattr(document_module, "read_widgets", counting)
    return calls


def _info(doc: PdfDocument, name: str, page: int = 0):
    return next(w for w in doc.widgets(page) if w.name == name)


def _event(page: int, x: float, y: float, buttons=Qt.MouseButton.LeftButton) -> ToolEvent:
    pos = QPointF(x, y)
    return ToolEvent(page, pos, pos, buttons, Qt.KeyboardModifier.NoModifier, None)


# -- performance ----------------------------------------------------------------------
def test_widgets_are_cached(many_fields_pdf, read_counter) -> None:
    doc = PdfDocument.open(str(many_fields_pdf))
    try:
        assert doc.page_count == 4
        first = doc.widgets(0)
        assert len(first) == MANY_FIELDS_PER_PAGE
        assert read_counter == [0]
        second = doc.widgets(0)
        assert read_counter == [0]  # served from the cache
        assert second == first and second is not first  # a new list each time
        assert len(doc.all_widgets()) == 200
        assert sorted(read_counter) == [0, 1, 2, 3]
        doc.all_widgets()
        assert len(read_counter) == 4
        doc.page_changed.emit(2)  # a field edit on page 2 drops only that page
        doc.all_widgets()
        assert sorted(read_counter) == [0, 1, 2, 2, 3]
    finally:
        doc.close()


def test_field_layer_rebuild_is_fast(window, many_fields_pdf, read_counter) -> None:
    assert window.open_file(str(many_fields_pdf))
    dv = window.document_view
    doc, layer = dv.document, dv.field_layer
    assert len(layer.items()) == 200

    doc._widget_cache.clear()  # cold: MuPDF parses the widgets again
    start = time.perf_counter()
    layer.rebuild(dv.page_view, doc)
    cold = time.perf_counter() - start
    assert len(layer.items()) == 200

    reads = len(read_counter)
    start = time.perf_counter()
    layer.rebuild(dv.page_view, doc)
    warm = time.perf_counter() - start
    assert len(read_counter) == reads  # warm rebuild does not touch pymupdf
    assert len(layer.items()) == 200
    print(f"FieldLayer rebuild, 200 widgets: cold {cold * 1000:.1f} ms, warm {warm * 1000:.1f} ms")
    assert warm < REBUILD_BUDGET_S
    assert cold < COLD_REBUILD_BUDGET_S


def test_hit_testing_uses_the_cache(window, many_fields_pdf, read_counter) -> None:
    assert window.open_file(str(many_fields_pdf))
    tool = window.form_tool
    doc = window.document_view.document
    doc.all_widgets()
    reads = len(read_counter)
    field = _info(doc, "f010")
    centre = field.rect.center()
    start = time.perf_counter()
    for n in range(500):
        tool.mouse_move(_event(n % 4, 50 + n % 500, 40 + n % 700, Qt.MouseButton.NoButton))
    assert tool.widget_at(0, centre).name == "f010"
    elapsed = time.perf_counter() - start
    assert len(read_counter) == reads
    print(f"500 mouse moves: {elapsed * 1000:.1f} ms")
    assert elapsed < 1.0
    assert len(tab_order(doc.all_widgets())) == 200
    assert len(read_counter) == reads


# -- odd widgets ------------------------------------------------------------------------
def test_odd_widgets_are_read_without_crash(odd_widgets_pdf) -> None:
    doc = PdfDocument.open(str(odd_widgets_pdf))
    try:
        infos = {w.name: w for w in doc.widgets(0)}
        assert set(infos) == set(ODD_RECTS)
        assert infos["signature"].kind is FieldKind.SIGNATURE
        assert infos["push"].kind is FieldKind.BUTTON
        # A missing /Rect reads as MuPDF's infinite rect: it must not cover the page.
        assert infos["no_rect"].rect.isEmpty()
        assert infos["degenerate"].rect.isEmpty()
        assert infos["combo_no_opt"].kind is FieldKind.COMBO
        assert infos["combo_no_opt"].choices == ()
        editable = {w.name for w in doc.widgets(0) if w.editable}
        assert editable == {"ok", "combo_no_opt", "edit_combo_no_opt"}
        assert [w.name for w in tab_order(doc.all_widgets())] == [
            "ok",
            "combo_no_opt",
            "edit_combo_no_opt",
        ]
        assert not doc.render(0, 1.0).isNull()
    finally:
        doc.close()


def test_odd_widgets_in_layer_and_tool(qtbot, window, odd_widgets_pdf) -> None:
    assert window.open_file(str(odd_widgets_pdf))
    w = window
    assert w.tool_manager.active_tool is w.form_tool
    doc = w.document_view.document
    names = {item.info.name for item in w.document_view.field_layer.items()}
    assert names == {"ok", "combo_no_opt", "edit_combo_no_opt"}
    editor = w.document_view.field_editor
    for name in ("signature", "push", "degenerate"):
        info = _info(doc, name)
        x0, y0, x1, y1 = ODD_RECTS[name]
        centre = ((x0 + x1) / 2, (y0 + y1) / 2)
        assert w.form_tool.widget_at(0, QPointF(*centre)) is None
        assert not w.form_tool.mouse_press(_event(0, *centre))  # falls through to panning
        assert not editor.is_open and w.undo_stack.count() == 0, info.name
        w.form_tool.mouse_move(_event(0, *centre, Qt.MouseButton.NoButton))
        assert w.page_view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
    # The widget without /Rect covers nothing: clicks elsewhere still reach the fields.
    assert w.form_tool.widget_at(0, QPointF(10, 10)) is None
    assert w.form_tool.widget_at(0, _info(doc, "ok").rect.center()).name == "ok"
    # Tab walks the three editable fields only.
    seen = []
    for _ in range(4):
        qtbot.keyClick(w.page_view if not editor.is_open else editor.editor, Qt.Key.Key_Tab)
        seen.append(editor.current_info.name)
    assert seen == ["ok", "combo_no_opt", "edit_combo_no_opt", "ok"]
    editor.cancel()


def test_combo_without_options(qtbot, window, odd_widgets_pdf) -> None:
    assert window.open_file(str(odd_widgets_pdf))
    w = window
    doc = w.document_view.document
    editor = w.document_view.field_editor

    w.form_tool.focus(_info(doc, "combo_no_opt"))
    combo = editor.editor
    assert isinstance(combo, QComboBox) and combo.count() == 0
    assert not combo.isEditable()
    assert editor.commit() is False  # nothing to choose: closes without an edit
    assert w.undo_stack.count() == 0

    w.form_tool.focus(_info(doc, "edit_combo_no_opt"))
    combo = editor.editor
    assert isinstance(combo, QComboBox) and combo.isEditable() and combo.count() == 0
    combo.lineEdit().setText("free text")
    editor.commit()
    assert w.undo_stack.count() == 1
    assert _info(doc, "edit_combo_no_opt").value == "free text"


# -- save failure ---------------------------------------------------------------------------
def test_failed_incremental_write_then_save_is_valid(form_pdf, monkeypatch) -> None:
    """Regression: the retry after a failed incremental write used to be a second
    incremental write of the same document, producing a file with no pages."""
    doc = PdfDocument.open(str(form_pdf))
    try:
        info = _info(doc, "text")
        doc.set_field_value(0, info.xref, "retry")
        assert doc.can_save_incrementally()
        with monkeypatch.context() as m:
            m.setattr(document_module, "_write_atomically", _fail_write)
            with pytest.raises(SaveError):
                doc.save()
        assert not doc.can_save_incrementally()  # the retry is a full save
        doc.save()
        assert doc.can_save_incrementally()  # reloaded from the saved bytes
    finally:
        doc.close()
    reopened = PdfDocument.open(str(form_pdf))
    try:
        assert reopened.page_count == 1
        assert _info(reopened, "text").value == "retry"
    finally:
        reopened.close()


def _fail_write(path, data):
    raise PermissionError(13, "Access is denied", path)


# -- save failure (window) -------------------------------------------------------------------------
def test_save_failure_keeps_pending_edit(window, lo_form_pdf, monkeypatch) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    doc = w.document_view.document
    original = lo_form_pdf.read_bytes()

    offered: list[str] = []
    monkeypatch.setattr(document_module, "_write_atomically", _fail_write)
    monkeypatch.setattr(dialogs, "offer_save_as", lambda p, e: offered.append(e) or False)
    w.form_tool.focus(_info(doc, "Zone de texte 8_54"))
    w.document_view.field_editor.editor.setText("kept after failure")

    assert w.save() is False
    assert len(offered) == 1 and "denied" in offered[0]
    assert not w.document_view.field_editor.is_open
    assert w.undo_stack.count() == 1  # the pending edit was committed before the save
    assert w.isWindowModified() and w.document_view.is_dirty
    assert w.document_view.document is doc and doc.is_open
    assert _info(doc, "Zone de texte 8_54").value == "kept after failure"
    assert lo_form_pdf.read_bytes() == original

    monkeypatch.undo()  # the target is writable again: the retry saves the value
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    assert w.save()
    assert not w.isWindowModified()
    reopened = PdfDocument.open(str(lo_form_pdf))
    try:
        assert _info(reopened, "Zone de texte 8_54").value == "kept after failure"
    finally:
        reopened.close()


# -- undo / redo with an open editor -----------------------------------------------------------
TEXT = "Zone de texte 8_54"
CHECK = "Case à cocher 1_2"


def _toggle_checkbox(w: MainWindow) -> None:
    """A first document edit, so that Edit > Undo is enabled."""
    doc = w.document_view.document
    w.form_tool.toggle(_info(doc, CHECK))
    assert w.undo_stack.count() == 1 and _info(doc, CHECK).is_on


def test_undo_action_commits_then_undoes_the_typed_value(window, lo_form_pdf) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    doc = w.document_view.document
    editor = w.document_view.field_editor
    _toggle_checkbox(w)
    before = _info(doc, TEXT).value
    w.form_tool.focus(_info(doc, TEXT))
    editor.editor.setText("typed")

    w.act_undo.trigger()  # Edit menu / toolbar
    assert not editor.is_open  # no stale editor left over the field
    assert _info(doc, TEXT).value == before
    assert _info(doc, CHECK).is_on  # only the typed value was undone
    assert w.undo_stack.count() == 2 and w.undo_stack.index() == 1
    w.act_redo.trigger()
    assert _info(doc, TEXT).value == "typed"


def test_undo_with_unchanged_editor_undoes_previous_edit(window, lo_form_pdf) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    doc = w.document_view.document
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(doc, TEXT))
    editor.editor.setText("first")
    editor.commit()
    w.form_tool.focus(_info(doc, TEXT))
    assert editor.editor.text() == "first"
    w.act_undo.trigger()
    assert not editor.is_open
    assert _info(doc, TEXT).value != "first"
    w.form_tool.focus(_info(doc, TEXT))
    w.act_redo.trigger()  # redo also closes the editor first
    assert not editor.is_open
    assert _info(doc, TEXT).value == "first"


def test_ctrl_z_in_editor_undoes_typing_first(qtbot, window, lo_form_pdf) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    doc = w.document_view.document
    editor = w.document_view.field_editor
    _toggle_checkbox(w)
    before = _info(doc, TEXT).value
    w.form_tool.focus(_info(doc, TEXT))
    line = editor.editor
    assert isinstance(line, QLineEdit)
    line.selectAll()
    qtbot.keyClicks(line, "abc")
    assert line.text() == "abc"
    # The line edit has typing to undo: Ctrl+Z is the line edit's own undo.
    while line.isUndoAvailable():
        qtbot.keyClick(line, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert editor.is_open and w.undo_stack.index() == 1
    assert line.text() == before
    # Nothing left to undo locally: Ctrl+Z reaches Edit > Undo, which closes the
    # (unchanged) editor and undoes the previous document edit.
    qtbot.keyClick(line, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not editor.is_open
    assert w.undo_stack.index() == 0 and w.undo_stack.count() == 1
    assert not _info(doc, CHECK).is_on


def test_ctrl_z_with_empty_stack_keeps_editor(qtbot, window, lo_form_pdf) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(w.document_view.document, TEXT))
    assert not w.act_undo.isEnabled()
    qtbot.keyClick(editor.editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert editor.is_open and w.undo_stack.count() == 0


def test_ctrl_z_in_editor_undoes_a_previous_document_edit(qtbot, window, lo_form_pdf) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    doc = w.document_view.document
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(doc, TEXT))
    editor.editor.setText("first")
    editor.commit()
    w.form_tool.focus(_info(doc, TEXT))
    qtbot.keyClick(editor.editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not editor.is_open
    assert w.undo_stack.index() == 0
    assert _info(doc, TEXT).value != "first"
