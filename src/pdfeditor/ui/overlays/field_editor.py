"""Floating in-place editor for text/choice form fields (M2).

``FieldEditorOverlay`` shows one Qt editor widget over a field, as a child of the
PageView's viewport, and reports the result through signals. It never touches the
document: the form tool listens to ``committed`` and pushes a ``SetFieldValueCommand``.
The floating-widget machinery lives in :class:`FloatingEditorOverlay`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QWidget,
)

from pdfeditor.core.forms import FieldKind, WidgetInfo
from pdfeditor.ui.overlays.floating_editor import (
    MIN_FONT_PX,
    FloatingEditorOverlay,
    normalize_newlines,
)

if TYPE_CHECKING:
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

#: Field kinds the overlay can edit (checkboxes/radios toggle without an editor).
EDITABLE_KINDS = (FieldKind.TEXT, FieldKind.COMBO, FieldKind.LIST)
DEFAULT_FONT_PT = 10.0
EDITOR_STYLE = "border: 1px solid rgb(0, 120, 215); padding: 0px; background: white;"


class _ChoiceCombo(QComboBox):
    """QComboBox that knows whether ``activated`` comes from its popup.

    ``activated`` is also emitted by Up/Down on a closed combo; only a choice made in the
    popup should commit. Qt emits ``activated`` right after ``hidePopup()`` in the same
    call, so the flag is cleared on the next event-loop turn.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.popup_active = False

    def showPopup(self) -> None:  # noqa: N802 (Qt override)
        self.popup_active = True
        super().showPopup()

    def hidePopup(self) -> None:  # noqa: N802 (Qt override)
        super().hidePopup()
        QTimer.singleShot(0, self._popup_closed)

    def _popup_closed(self) -> None:
        self.popup_active = False


class FieldEditorOverlay(FloatingEditorOverlay):
    """One floating editor over a text, combo or list field of ``view``.

    ``open(info)`` shows a QLineEdit (text; ``setMaxLength`` when ``info.max_len > 0``),
    QPlainTextEdit (multiline: Enter inserts a newline, Ctrl+Enter commits), QComboBox
    (display strings shown, export value committed; editable combos commit free text as
    typed) or QListWidget (single selection, export value committed), prefilled with
    ``info.value``. Keys: Enter commits, Tab/Shift+Tab commit then emit ``navigate``,
    Escape cancels; losing focus to anything outside the editor commits. The editor
    follows scrolling, zoom and viewport resizes and hides while the field is scrolled
    out of view. Rotated pages: the editor stays horizontal.

    ``committed(info, value)`` is emitted **after** the editor is closed (``is_open`` is
    False, so a handler may open another field) and **only when the value changed**;
    committing an unchanged value just closes. ``value`` is always a ``str``: the text
    (newlines ``"\\n"``), the chosen export value, or ``""`` for no selection.
    ``info`` is the snapshot passed to ``open`` (its document is ``self.document`` at
    emission time, even on a document change).
    """

    # committed(info: WidgetInfo, value: str) and cancelled() come from the base class.
    navigate = Signal(bool)  # backwards

    def __init__(
        self,
        view: PageView,
        document: PdfDocument | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(view, document, parent)

    # -- state ----------------------------------------------------------------
    @property
    def current_info(self) -> WidgetInfo | None:
        return self._anchor

    # -- public API -------------------------------------------------------------
    def open(self, info: WidgetInfo) -> None:
        """Show an editor for ``info`` (a pending edit is committed first)."""
        if info.kind not in EDITABLE_KINDS:
            raise ValueError(f"no editor for {info.kind} fields")
        self._open_anchor(info)

    # -- editor construction ------------------------------------------------------
    def _font_px(self) -> int:
        info = self._anchor
        size = (info.font_size if info is not None else 0) or DEFAULT_FONT_PT
        return max(MIN_FONT_PX, round(size * self._view.view_scale))

    def _create_editor(self, info: WidgetInfo) -> QWidget:
        parent = self._view.viewport()
        editor: QWidget
        if info.kind is FieldKind.TEXT and info.multiline:
            edit = QPlainTextEdit(parent)
            edit.setPlainText(normalize_newlines(info.value))
            edit.setTabChangesFocus(True)
            edit.setStyleSheet(EDITOR_STYLE)
            edit.moveCursor(QTextCursor.MoveOperation.End)
            editor = edit
        elif info.kind is FieldKind.TEXT:
            line = QLineEdit(parent)
            if info.max_len > 0:
                line.setMaxLength(info.max_len)
            line.setText(info.value)
            line.setStyleSheet(EDITOR_STYLE)
            line.selectAll()
            editor = line
        elif info.kind is FieldKind.COMBO:
            combo = _ChoiceCombo(parent)
            combo.setEditable(info.editable_combo)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            for export, display in info.choices:
                combo.addItem(display, export)
            index = self._choice_index(info)
            combo.setCurrentIndex(index)
            if index < 0 and info.editable_combo:
                combo.setEditText(info.value)
            combo.activated.connect(self._on_combo_activated)
            editor = combo
        else:
            lst = QListWidget(parent)
            lst.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            for export, display in info.choices:
                item = QListWidgetItem(display, lst)
                item.setData(Qt.ItemDataRole.UserRole, export)
            index = self._choice_index(info)
            if index >= 0:
                lst.setCurrentRow(index)
                lst.scrollToItem(lst.item(index))
            else:
                lst.setCurrentRow(-1)
                lst.clearSelection()
            lst.itemDoubleClicked.connect(lambda _item: self.commit())
            editor = lst
        editor.setObjectName("fieldEditor")
        editor.hide()
        return editor

    @staticmethod
    def _choice_index(info: WidgetInfo) -> int:
        if not info.value:
            return -1
        for i, (export, _display) in enumerate(info.choices):
            if export == info.value:
                return i
        for i, (_export, display) in enumerate(info.choices):
            if display == info.value:
                return i
        return -1

    # -- values ---------------------------------------------------------------------
    def _initial_value(self, info: WidgetInfo) -> str:
        return normalize_newlines(info.value) if info.multiline else info.value

    def _editor_value(self) -> str:
        editor, info = self._editor, self._anchor
        assert editor is not None and info is not None
        if isinstance(editor, QPlainTextEdit):
            return editor.toPlainText()
        if isinstance(editor, QLineEdit):
            return editor.text()
        if isinstance(editor, QComboBox):
            if editor.isEditable():
                text = editor.currentText()
                for export, display in info.choices:
                    if display == text:
                        return export
                return text
            data = editor.currentData()
            return "" if data is None else str(data)
        assert isinstance(editor, QListWidget)
        items = editor.selectedItems()
        if not items:
            return ""
        return str(items[0].data(Qt.ItemDataRole.UserRole))

    # -- teardown ---------------------------------------------------------------------
    def _before_teardown(self, editor: QWidget) -> None:
        if isinstance(editor, _ChoiceCombo):
            try:
                editor.activated.disconnect(self._on_combo_activated)
            except (RuntimeError, TypeError):
                pass
            if editor.popup_active:
                editor.hidePopup()

    # -- slots --------------------------------------------------------------------------
    def _on_combo_activated(self, _index: int) -> None:
        editor = self._editor
        if isinstance(editor, _ChoiceCombo) and editor.popup_active:
            self.commit()

    # -- events -------------------------------------------------------------------------
    def _popup_open(self) -> bool:
        editor = self._editor
        return isinstance(editor, _ChoiceCombo) and editor.view().isVisible()

    def _key_press(self, event: QKeyEvent) -> bool:
        if self._popup_open():
            return False  # the combo popup handles its own keys
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            backwards = key == Qt.Key.Key_Backtab or bool(mods & Qt.KeyboardModifier.ShiftModifier)
            self.commit()
            self.navigate.emit(backwards)
            return True
        return super()._key_press(event)


__all__ = ["EDITABLE_KINDS", "FieldEditorOverlay"]
