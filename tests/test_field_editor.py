from __future__ import annotations

import pytest
from fixtures import LO_MAX_LEN
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QListWidget, QPlainTextEdit

from pdfeditor.constants import ZoomMode
from pdfeditor.core.document import PdfDocument
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.overlays.field_editor import FieldEditorOverlay


@pytest.fixture
def dv(qtbot):
    view = DocumentView()
    qtbot.addWidget(view)
    view.resize(800, 600)
    view.show()
    qtbot.waitExposed(view)
    view.activateWindow()
    yield view
    view.undo_stack.setClean()
    view.shutdown()


class Recorder:
    def __init__(self, overlay: FieldEditorOverlay) -> None:
        self.committed: list[tuple[object, object]] = []
        self.cancelled = 0
        self.navigate: list[bool] = []
        self.events: list[str] = []
        overlay.committed.connect(self._on_committed)
        overlay.cancelled.connect(self._on_cancelled)
        overlay.navigate.connect(self._on_navigate)

    def _on_committed(self, info, value) -> None:
        self.committed.append((info, value))
        self.events.append("committed")

    def _on_cancelled(self) -> None:
        self.cancelled += 1
        self.events.append("cancelled")

    def _on_navigate(self, backwards: bool) -> None:
        self.navigate.append(backwards)
        self.events.append("navigate")


@pytest.fixture
def setup(qtbot, dv, lo_form_pdf):
    doc = dv.open(str(lo_form_pdf))
    overlay = FieldEditorOverlay(dv.page_view, doc)
    return dv, doc, overlay, Recorder(overlay)


def _info(doc: PdfDocument, name: str, page: int = 0):
    return next(w for w in doc.widgets(page) if w.name == name)


def _assert_geom(overlay: FieldEditorOverlay, view, info) -> None:
    expected = view.page_rect_to_viewport(info.page, info.rect)
    got = overlay.editor.geometry()
    for a, b in (
        (got.x(), expected.x()),
        (got.y(), expected.y()),
        (got.width(), expected.width()),
        (got.height(), expected.height()),
    ):
        assert abs(a - b) <= 2


def test_line_edit_geometry_scroll_and_zoom(qtbot, setup) -> None:
    dv, doc, overlay, _rec = setup
    view = dv.page_view
    info = _info(doc, "Zone de texte 8_54")
    overlay.open(info)
    editor = overlay.editor
    assert overlay.is_open and overlay.current_info is info
    assert isinstance(editor, QLineEdit)
    assert editor.parent() is view.viewport()
    assert editor.isVisible()
    assert editor.maxLength() == LO_MAX_LEN == 30
    _assert_geom(overlay, view, info)
    expected_px = max(9, round(info.font_size * view.view_scale))
    assert editor.font().pixelSize() == expected_px

    before = editor.geometry()
    bar = view.verticalScrollBar()
    start = bar.value()
    bar.setValue(start + 40)
    delta = bar.value() - start
    assert delta == 40
    after = editor.geometry()
    assert after.y() == pytest.approx(before.y() - delta, abs=2)
    assert after.x() == before.x()
    _assert_geom(overlay, view, info)

    bar.setValue(start)
    width_before = editor.width()
    view.set_zoom_percent(view.zoom_percent * 2)
    _assert_geom(overlay, view, info)
    assert editor.width() == pytest.approx(width_before * 2, abs=3)
    assert editor.font().pixelSize() == max(9, round(info.font_size * view.view_scale))

    # Scrolled out of view: hidden, then shown again.
    bar.setValue(bar.maximum())
    assert editor.isHidden()
    assert overlay.is_open
    bar.setValue(start)
    assert editor.isVisible()
    _assert_geom(overlay, view, info)


def test_viewport_resize_repositions(qtbot, setup) -> None:
    dv, doc, overlay, _rec = setup
    view = dv.page_view
    view.set_zoom_mode(ZoomMode.CUSTOM)
    info = _info(doc, "Zone de texte 8_54")
    overlay.open(info)
    dv.resize(1000, 600)
    expected = lambda: view.page_rect_to_viewport(info.page, info.rect)  # noqa: E731
    qtbot.waitUntil(lambda: view.viewport().width() > 800)
    qtbot.waitUntil(lambda: overlay.editor.geometry() == expected())
    _assert_geom(overlay, view, info)


def test_typing_enter_commits_once(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Zone de texte 8_54")
    overlay.open(info)
    editor = overlay.editor
    qtbot.waitUntil(editor.hasFocus)
    # QTest.keyClicks crashes on non-ASCII characters: insert accents directly.
    qtbot.keyClicks(editor, "El")
    editor.insert("è")
    qtbot.keyClicks(editor, "ve")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert rec.committed == [(info, "Elève")]
    assert not overlay.is_open and overlay.current_info is None
    assert editor.isHidden()
    qtbot.wait(10)  # deleteLater, focus changes: still exactly one commit
    assert len(rec.committed) == 1
    assert rec.cancelled == 0
    assert dv.page_view.hasFocus()


def test_unchanged_value_does_not_emit(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Zone de texte 8_55")
    assert info.value == "déjà"
    overlay.open(info)
    assert overlay.editor.text() == "déjà"
    assert overlay.commit() is False
    assert rec.committed == [] and not overlay.is_open


def test_escape_cancels(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    overlay.open(_info(doc, "Zone de texte 8_54"))
    editor = overlay.editor
    qtbot.keyClicks(editor, "abc")
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    assert rec.cancelled == 1 and rec.committed == []
    assert not overlay.is_open
    qtbot.wait(10)
    assert rec.committed == []


def test_tab_commits_then_navigates(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Zone de texte 8_54")
    overlay.open(info)
    qtbot.keyClicks(overlay.editor, "x")
    qtbot.keyClick(overlay.editor, Qt.Key.Key_Tab)
    assert rec.events == ["committed", "navigate"]
    assert rec.committed == [(info, "x")] and rec.navigate == [False]

    overlay.open(info)
    qtbot.keyClick(overlay.editor, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    # Unchanged value: no commit, but still navigates.
    assert rec.navigate == [False, True]
    assert len(rec.committed) == 1


def test_multiline(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Zone de texte multi")
    assert info.multiline
    overlay.open(info)
    editor = overlay.editor
    assert isinstance(editor, QPlainTextEdit)
    qtbot.keyClicks(editor, "a")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.keyClicks(editor, "b")
    assert overlay.is_open and rec.committed == []
    assert editor.toPlainText() == "a\nb"
    qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert rec.committed == [(info, "a\nb")]
    assert not overlay.is_open


def test_combo_commits_export_value(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Civilité")
    overlay.open(info)
    combo = overlay.editor
    assert isinstance(combo, QComboBox) and not combo.isEditable()
    assert [combo.itemText(i) for i in range(combo.count())] == ["Monsieur", "Madame", "Autre"]
    # Arrow keys on the closed combo change the choice without committing.
    qtbot.keyClick(combo, Qt.Key.Key_Down)
    qtbot.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentText() == "Madame" and overlay.is_open
    qtbot.keyClick(combo, Qt.Key.Key_Return)
    assert rec.committed == [(info, "f")]


def test_combo_popup_choice_commits(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Civilité")
    overlay.open(info)
    combo = overlay.editor
    combo.showPopup()
    qtbot.waitUntil(combo.view().isVisible)
    # Focus moving to the popup must not commit.
    assert overlay.is_open and rec.committed == []
    index = combo.model().index(1, 0)
    combo.view().setCurrentIndex(index)
    qtbot.keyClick(combo.view(), Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: bool(rec.committed))
    assert rec.committed == [(info, "f")]
    assert not overlay.is_open


def test_editable_combo_free_text(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Civilité")
    from dataclasses import replace

    from pdfeditor.core.forms import FF_EDIT

    editable = replace(info, flags=info.flags | FF_EDIT)
    assert editable.editable_combo
    overlay.open(editable)
    combo = overlay.editor
    assert combo.isEditable()
    line = combo.lineEdit()
    line.selectAll()
    qtbot.keyClicks(line, "Docteur")
    qtbot.keyClick(line, Qt.Key.Key_Return)
    assert rec.committed == [(editable, "Docteur")]

    overlay.open(editable)
    overlay.editor.lineEdit().setText("Madame")
    overlay.commit()
    assert rec.committed[-1] == (editable, "f")


def test_list_commits_export(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Couleur")
    overlay.open(info)
    lst = overlay.editor
    assert isinstance(lst, QListWidget)
    assert lst.selectionMode() == QListWidget.SelectionMode.SingleSelection
    lst.setCurrentRow(2)
    qtbot.keyClick(lst, Qt.Key.Key_Return)
    assert rec.committed == [(info, "Bleu")]


def test_focus_out_commits(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Zone de texte 8_54")
    overlay.open(info)
    editor = overlay.editor
    qtbot.waitUntil(editor.hasFocus)
    qtbot.keyClicks(editor, "focus")
    dv.page_view.setFocus(Qt.FocusReason.MouseFocusReason)
    qtbot.waitUntil(lambda: not overlay.is_open)
    assert rec.committed == [(info, "focus")]
    qtbot.wait(10)
    assert len(rec.committed) == 1


def test_scrolling_out_of_view_does_not_commit(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    view = dv.page_view
    overlay.open(_info(doc, "Zone de texte 8_54"))
    editor = overlay.editor
    qtbot.waitUntil(editor.hasFocus)
    bar = view.verticalScrollBar()
    start = bar.value()
    bar.setValue(bar.maximum())
    assert editor.isHidden() and overlay.is_open and rec.committed == []
    bar.setValue(start)
    assert editor.isVisible()
    qtbot.waitUntil(editor.hasFocus)


def test_open_another_commits_previous(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    first = _info(doc, "Zone de texte 8_54")
    overlay.open(first)
    qtbot.keyClicks(overlay.editor, "1")
    overlay.open(_info(doc, "Zone de texte 8_55"))
    assert rec.committed == [(first, "1")]
    assert overlay.current_info.name == "Zone de texte 8_55"
    assert len(dv.page_view.viewport().findChildren(QLineEdit)) >= 1


def test_close_on_document_change(qtbot, setup, simple_pdf) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Zone de texte 8_54")
    overlay.open(info)
    qtbot.keyClicks(overlay.editor, "bye")
    seen_doc = []
    overlay.committed.connect(lambda *_a: seen_doc.append(overlay.document))
    other = PdfDocument.open(str(simple_pdf))
    try:
        overlay.set_document(other)
        assert rec.committed == [(info, "bye")]
        assert seen_doc == [doc]
        assert not overlay.is_open and overlay.document is other
    finally:
        overlay.set_document(None)
        other.close()


@pytest.mark.parametrize("signal", ["path_changed", "reloaded", "structure_changed"])
def test_close_on_document_signals(qtbot, setup, signal) -> None:
    dv, doc, overlay, rec = setup
    info = _info(doc, "Zone de texte 8_54")
    overlay.open(info)
    qtbot.keyClicks(overlay.editor, "v")
    sig = getattr(doc, signal)
    if signal == "path_changed":
        sig.emit("elsewhere.pdf")
    else:
        sig.emit()
    assert rec.committed == [(info, "v")]
    assert not overlay.is_open


def test_close_is_silent_and_bad_kind_rejected(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    overlay.open(_info(doc, "Zone de texte 8_54"))
    qtbot.keyClicks(overlay.editor, "z")
    overlay.close()
    assert not overlay.is_open and rec.committed == [] and rec.cancelled == 0
    overlay.commit()
    overlay.cancel()
    assert rec.committed == [] and rec.cancelled == 0
    with pytest.raises(ValueError):
        overlay.open(_info(doc, "Case à cocher 1_2"))
    assert not overlay.is_open


def test_page_click_focus_leaves_no_editor(qtbot, setup) -> None:
    """A click on the page moves focus to the view, which commits the edit."""
    dv, doc, overlay, rec = setup
    info = _info(doc, "Zone de texte 8_54")
    overlay.open(info)
    qtbot.waitUntil(overlay.editor.hasFocus)
    qtbot.keyClicks(overlay.editor, "clic")
    qtbot.mouseClick(dv.page_view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    qtbot.waitUntil(lambda: not overlay.is_open)
    assert rec.committed == [(info, "clic")]
    assert QApplication.focusWidget() is not None


def test_open_out_of_view_stays_hidden_until_scrolled_in(qtbot, setup) -> None:
    dv, doc, overlay, rec = setup
    view = dv.page_view
    bar = view.verticalScrollBar()
    start = bar.value()
    bar.setValue(bar.maximum())
    overlay.open(_info(doc, "Zone de texte 8_54"))
    editor = overlay.editor
    assert overlay.is_open and editor.isHidden()
    bar.setValue(start)
    assert editor.isVisible()
    qtbot.waitUntil(editor.hasFocus)
