"""Floating single-line editor of a run of page text (M7, docs/M7_PLAN.md §1.5).

``TextRunEditor`` shows a QLineEdit over a ``textedit.Run`` of a page, prefilled with the
run's text and drawn in the run's font, size and colour. It never touches the document:
the Edit Page Text tool listens to ``run_committed(page, run, text)`` and pushes a
``ReplaceTextCommand``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QLineEdit, QWidget

from pdfeditor.core.fontread import split_base_font
from pdfeditor.core.pagetext import PageText, Span
from pdfeditor.core.textedit import Run
from pdfeditor.ui.overlays.floating_editor import FloatingEditorOverlay

if TYPE_CHECKING:
    from pdfeditor.core.document import PageId, PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

#: Smallest editor font (pixels) so that text stays readable when zoomed out.
MIN_FONT_PX = 4
_BORDER = "1px dashed rgb(0, 120, 215)"
#: PDF font flags (MuPDF span ``flags``).
FLAG_ITALIC, FLAG_BOLD = 2, 16
FLAG_SERIF, FLAG_MONO = 4, 8
#: Trailing words of a font name that name a style, not the family ("Calibri Regular").
_STYLE_WORDS = frozenset(
    (
        "regular",
        "book",
        "normal",
        "bold",
        "italic",
        "oblique",
        "bolditalic",
        "boldoblique",
        "semibold",
        "demibold",
        "light",
        "medium",
        "black",
        "heavy",
    )
)


@dataclass(frozen=True)
class TextRunAnchor:
    """What the run editor edits: the chars ``run`` of ``page`` (``page_id`` names the
    page across page operations), shown over ``rect`` (page space) in ``family`` at
    ``size`` points with ``color`` (r, g, b in 0..255). ``text`` is the prefilled text
    (the run's text, U+00A0 replaced by a space)."""

    page: int
    page_id: PageId
    run: Run
    rect: QRectF
    text: str
    family: str
    size: float
    bold: bool
    italic: bool
    color: tuple[int, int, int]


def display_text(text: str) -> str:
    """Page text as shown in the editor (no-break spaces become spaces)."""
    return text.replace(" ", " ")


def span_family(span: Span) -> str:
    """Family name of ``span``'s font: subset prefix and style suffixes dropped."""
    family, _style = split_base_font(span.font)
    words = family.split()
    while len(words) > 1 and words[-1].lower() in _STYLE_WORDS:
        words.pop()
    return " ".join(words) or family


def span_style(span: Span) -> tuple[bool, bool]:
    """``(bold, italic)`` of ``span`` from its flags and its font name."""
    lowered = span.font.lower()
    bold = bool(span.flags & FLAG_BOLD) or "bold" in lowered
    italic = bool(span.flags & FLAG_ITALIC) or "italic" in lowered or "oblique" in lowered
    return bold, italic


def span_rgb(span: Span) -> tuple[int, int, int]:
    c = int(span.color) & 0xFFFFFF
    return (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF


def run_rect(text: PageText, run: Run) -> QRectF:
    """Page-space rect of ``run``: its chars' extent along a horizontal line, from the
    span's ascender to its descender; the bounds of the run's quads otherwise."""
    span = text.span_of(run.first)
    line = text.line_of(run.first)
    dx, dy = line.dir
    if abs(dy) < 1e-3 and dx > 0:
        boxes = [text.chars[i].bbox for i in run.indexes]
        left = min(b.left() for b in boxes)
        right = max(b.right() for b in boxes)
        base = span.origin.y()
        top = base - span.ascender * span.size
        bottom = base - span.descender * span.size
        if bottom - top > 0:
            return QRectF(left, top, right - left, bottom - top)
    quads = text.range_quads(run.first, run.last)
    rect = QRectF()
    for q in quads:
        rect = rect.united(q.bounding_rect())
    return rect


class TextRunEditor(FloatingEditorOverlay):
    """One QLineEdit over a run of page text of ``view``.

    Keys: Enter, Tab/Shift+Tab and focus-out commit; Escape cancels. The font is the
    run's family (or the ``family`` given to :meth:`open`, e.g. a font plan's), bold and
    italic from the span, at ``round(size × view_scale)`` pixels in the span's colour,
    with a dashed border. The editor covers the run's rect (height ascender − descender)
    and widens when the text no longer fits.

    ``run_committed(page: int, run: Run, text: str)`` is emitted (after the base class's
    ``committed(anchor: TextRunAnchor, text)``) only when the text differs from the
    prefilled one; ``""`` asks for the run's removal. ``cancelled()`` after Escape. Like
    the text box editor, a document switch (``set_document``) closes it silently; it
    commits on ``reloaded``/``structure_changed``/``path_changed`` and follows its page
    through ``pages_remapped`` (closed silently when the page is deleted).
    """

    run_committed = Signal(int, object, str)  # (page, Run, new text)

    def __init__(
        self,
        view: PageView,
        document: PdfDocument | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(view, document, parent)
        self.committed.connect(self._emit_run_committed)

    # -- state ----------------------------------------------------------------
    @property
    def anchor(self) -> TextRunAnchor | None:
        return self._anchor

    # -- public API -------------------------------------------------------------
    def open(self, page: int, run: Run, *, family: str | None = None) -> TextRunAnchor:
        """Open the editor over ``run`` of ``page`` (a pending edit is committed first).

        ``family`` overrides the span's font family (a font plan's). Raises
        ``ValueError`` without a document or when ``run`` is not on the page.
        """
        doc = self._document
        if doc is None or not doc.is_open or not 0 <= page < doc.page_count:
            raise ValueError(f"no page {page} to edit")
        text = doc.page_text(page)
        if run.last >= len(text.chars):
            raise ValueError(f"{run} is not on page {page}")
        span = text.span_of(run.first)
        bold, italic = span_style(span)
        anchor = TextRunAnchor(
            page=page,
            page_id=doc.page_id(page),
            run=run,
            rect=run_rect(text, run),
            text=display_text(run.text(text)),
            family=family or span_family(span),
            size=float(span.size),
            bold=bold,
            italic=italic,
            color=span_rgb(span),
        )
        self._open_anchor(anchor)
        return anchor

    # -- hooks ----------------------------------------------------------------------
    @staticmethod
    def _style(color: tuple[int, int, int]) -> str:
        r, g, b = color
        return (
            f"QLineEdit {{ border: {_BORDER}; padding: 0px; margin: 0px; "
            f"background: rgba(255, 255, 255, 235); color: rgb({r}, {g}, {b}); }}"
        )

    def _create_editor(self, anchor: TextRunAnchor) -> QWidget:
        edit = QLineEdit(self._view.viewport())
        edit.setObjectName("textRunEditor")
        edit.setFrame(False)
        edit.setTextMargins(0, 0, 0, 0)
        edit.setContentsMargins(0, 0, 0, 0)
        edit.setMinimumSize(0, 0)
        font = QFont(anchor.family)
        font.setBold(anchor.bold)
        font.setItalic(anchor.italic)
        edit.setFont(font)
        edit.setStyleSheet(self._style(anchor.color))
        edit.setText(anchor.text)
        edit.selectAll()
        edit.textChanged.connect(self.reposition)
        edit.hide()
        return edit

    def _before_teardown(self, editor: QWidget) -> None:
        if isinstance(editor, QLineEdit):
            try:
                editor.textChanged.disconnect(self.reposition)
            except (RuntimeError, TypeError):
                pass

    def _editor_value(self) -> str:
        editor = self._editor
        assert isinstance(editor, QLineEdit)
        return editor.text()

    def _initial_value(self, anchor: TextRunAnchor) -> str:
        return anchor.text

    def _font_px(self) -> int:
        anchor = self._anchor
        size = anchor.size if anchor is not None else 0.0
        return max(MIN_FONT_PX, round(size * self._view.view_scale))

    def _editor_geometry(self, rect: QRect) -> QRect:
        """Widen the run's rect to the right when the text needs more room."""
        editor = self._editor
        if not isinstance(editor, QLineEdit):
            return rect
        needed = editor.fontMetrics().horizontalAdvance(editor.text()) + 6
        if needed > rect.width():
            rect = QRect(rect.x(), rect.y(), needed, rect.height())
        return rect

    def _on_document_switch(self) -> None:
        self.close()

    def _key_press(self, event: QKeyEvent) -> bool:
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.commit()
            return True
        return super()._key_press(event)

    # -- slots ------------------------------------------------------------------------
    def _emit_run_committed(self, anchor: object, text: object) -> None:
        if isinstance(anchor, TextRunAnchor):
            self.run_committed.emit(anchor.page, anchor.run, str(text))


__all__ = [
    "MIN_FONT_PX",
    "TextRunAnchor",
    "TextRunEditor",
    "display_text",
    "run_rect",
    "span_family",
    "span_rgb",
    "span_style",
]
