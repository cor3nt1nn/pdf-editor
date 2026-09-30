"""Floating in-place editor for FreeText text boxes (M3).

``AnnotTextEditor`` edits the text of a new or existing text annotation in a
QPlainTextEdit over its rect. It never touches the document: the text tool listens to
``committed`` and pushes an ``AddAnnotCommand`` (``anchor.is_new``) or an
``EditAnnotCommand`` (``anchor.info`` is the annotation being edited).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRect, QRectF, Qt
from PySide6.QtGui import QFont, QKeyEvent, QTextCursor
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QWidget

from pdfeditor.core.annotations import AnnotInfo, AnnotKind, Color
from pdfeditor.ui.overlays.floating_editor import FloatingEditorOverlay, normalize_newlines

if TYPE_CHECKING:
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

#: Smallest editor font (pixels) so that text stays readable when zoomed out.
MIN_FONT_PX = 4
#: Sans-serif family close to the /Helv the annotation is drawn with.
FONT_FAMILY = "Arial"
_BORDER = "1px dashed rgb(0, 120, 215)"


@dataclass(frozen=True)
class EditorAnchor:
    """What the annotation editor edits: a new text box, or the existing ``info``.

    ``font_size``/``color`` are the *current* style (``AnnotTextEditor.set_style``
    replaces the anchor); ``rect`` is the page-space rect the editor was opened on.
    """

    page: int
    rect: QRectF
    font_size: float
    color: Color
    #: The annotation being edited; None for a new text box.
    info: AnnotInfo | None = None

    @property
    def is_new(self) -> bool:
        return self.info is None

    @property
    def name(self) -> str:
        """/NM of the edited annotation ("" for a new one)."""
        return "" if self.info is None else self.info.name


def _css_color(color: Color) -> str:
    r, g, b = (round(max(0.0, min(1.0, c)) * 255) for c in color)
    return f"rgb({r}, {g}, {b})"


class AnnotTextEditor(FloatingEditorOverlay):
    """One QPlainTextEdit over a new or existing text annotation of ``view``.

    Keys: Enter inserts a newline; Ctrl+Enter, Tab/Shift+Tab and focus-out commit;
    Escape cancels. The font is Arial at ``round(font_size × view_scale)`` pixels in the
    anchor's colour; the editor is at least as tall as the anchor rect and grows with the
    text (never shrinks below the rect). On rotated pages the editor stays horizontal
    (the annotation is drawn upright too, docs/M3_PLAN.md A7).

    ``committed(anchor: EditorAnchor, text: str)`` is emitted after teardown, for a new
    box only when the text is not blank, for an existing one only when the text or the
    style differs from ``anchor.info`` (the text may then be ``""``: the tool decides
    whether that deletes the annotation). ``cancelled()`` after Escape. Unlike the field
    editor, a document switch (``set_document``) closes it silently.
    """

    # committed(anchor: EditorAnchor, text: str) and cancelled() come from the base class.

    def __init__(
        self,
        view: PageView,
        document: PdfDocument | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(view, document, parent)
        self._growing = False

    # -- state ----------------------------------------------------------------
    @property
    def anchor(self) -> EditorAnchor | None:
        return self._anchor

    # -- public API -------------------------------------------------------------
    def open_new(self, page: int, rect: QRectF, font_size: float, color: Color) -> None:
        """Open an empty editor for a new text box at ``rect`` (page space)."""
        self._open_anchor(EditorAnchor(page, QRectF(rect), float(font_size), tuple(color)))

    def open_existing(self, info: AnnotInfo) -> None:
        """Open an editor prefilled with the text of the annotation ``info``."""
        if info.kind is not AnnotKind.TEXT:
            raise ValueError(f"no editor for {info.kind} annotations")
        self._open_anchor(
            EditorAnchor(info.page, QRectF(info.rect), info.font_size, info.color, info)
        )

    def set_style(self, font_size: float, color: Color) -> None:
        """Change the style of the open editor (applied on commit); no-op when closed."""
        anchor = self._anchor
        if anchor is None:
            return
        self._anchor = replace(anchor, font_size=float(font_size), color=tuple(color))
        editor = self._editor
        if editor is not None:
            editor.setStyleSheet(self._style(self._anchor.color))
        self.reposition()

    # -- hooks ----------------------------------------------------------------------
    @staticmethod
    def _style(color: Color) -> str:
        return (
            f"QPlainTextEdit {{ border: {_BORDER}; padding: 0px; "
            f"background: rgba(255, 255, 255, 230); color: {_css_color(color)}; }}"
        )

    def _create_editor(self, anchor: EditorAnchor) -> QWidget:
        edit = QPlainTextEdit(self._view.viewport())
        edit.setObjectName("annotEditor")
        edit.setFrameShape(QFrame.Shape.NoFrame)
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        edit.setTabChangesFocus(True)
        edit.document().setDocumentMargin(1)
        font = QFont(FONT_FAMILY)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        edit.setFont(font)
        edit.setStyleSheet(self._style(anchor.color))
        if anchor.info is not None:
            edit.setPlainText(normalize_newlines(anchor.info.text))
            edit.moveCursor(QTextCursor.MoveOperation.End)
        edit.textChanged.connect(self.reposition)
        edit.hide()
        return edit

    def _before_teardown(self, editor: QWidget) -> None:
        if isinstance(editor, QPlainTextEdit):
            try:
                editor.textChanged.disconnect(self.reposition)
            except (RuntimeError, TypeError):
                pass

    def _editor_value(self) -> str:
        editor = self._editor
        assert isinstance(editor, QPlainTextEdit)
        return normalize_newlines(editor.toPlainText())

    def _initial_value(self, anchor: EditorAnchor) -> str:
        return "" if anchor.info is None else normalize_newlines(anchor.info.text)

    def _should_commit(self, anchor: EditorAnchor, value: str) -> bool:
        info = anchor.info
        if info is None:
            return bool(value.strip())
        return (
            value != self._initial_value(anchor)
            or anchor.font_size != info.font_size
            or tuple(anchor.color) != tuple(info.color)
        )

    def _font_px(self) -> int:
        anchor = self._anchor
        size = anchor.font_size if anchor is not None else 0.0
        return max(MIN_FONT_PX, round(size * self._view.view_scale))

    def _editor_geometry(self, rect: QRect) -> QRect:
        """Grow the anchor rect downwards to fit the wrapped text."""
        editor = self._editor
        if not isinstance(editor, QPlainTextEdit) or self._growing:
            return rect
        self._growing = True
        try:
            # Wrap at the final width before measuring.
            editor.resize(rect.width(), max(rect.height(), editor.height()))
            layout = editor.document().documentLayout()
            lines = max(1, round(layout.documentSize().height()))  # plain text: lines
            margin = editor.document().documentMargin()
            needed = round(lines * editor.fontMetrics().lineSpacing() + 2 * margin + 2)
        finally:
            self._growing = False
        if needed > rect.height():
            rect = QRect(rect.x(), rect.y(), rect.width(), needed)
        return rect

    def _on_document_switch(self) -> None:
        self.close()

    def _key_press(self, event: QKeyEvent) -> bool:
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.commit()
            return True
        return super()._key_press(event)


__all__ = ["AnnotTextEditor", "EditorAnchor"]
