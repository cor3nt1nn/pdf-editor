"""The Edit Page Text tool (M7, docs/M7_PLAN.md §1.5).

``TextEditTool`` (Edit ▸ Edit Page Text, E) selects a run of the page's own text and
replaces it through the floating ``DocumentView.textedit_editor``:

* hover: ``DocumentView.text_hover`` frames the line and word under the pointer (I-beam
  cursor over editable text);
* a click selects the word (``DocumentView.text_selection``), a double-click the span (the
  chars of one style on the line), a drag a range that stays within the line, Shift+click
  extends the selection; Escape deselects;
* Enter, F2 or a second click on the selection opens the editor over the selected run
  (clamped to one span); the editor's ``run_committed`` pushes one
  :class:`~pdfeditor.core.commands.ReplaceTextCommand`.

Refusals and outcomes are reported with ``message`` (the main window's status bar): no
text under the click (scan, outlined text), an invisible OCR layer, text the core refuses
(``EditReason``), a substituted font, an extended run, narrowed or overflowing text, and
once per document that the next save rewrites the whole file.
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QKeyEvent, QPainter
from PySide6.QtWidgets import QApplication

from pdfeditor.core import fontmatch
from pdfeditor.core.commands import ReplaceTextCommand
from pdfeditor.core.document import DocumentError, PdfDocument
from pdfeditor.core.pagetext import CharRef, PageText
from pdfeditor.core.textedit import MAX_CONTENT_MB, EditReason, Run, TextEditError, TextEditResult
from pdfeditor.ui.overlays.textedit_editor import TextRunAnchor
from pdfeditor.ui.overlays.textedit_items import clamp_to_span, editable, selection_run, span_run
from pdfeditor.ui.tools.base import Tool, ToolEvent, event_button, viewport_pos
from pdfeditor.ui.tools.markup_tools import _TextDrag, _TextSelecting

if TYPE_CHECKING:
    from pdfeditor.core.fontmatch import SystemFonts
    from pdfeditor.ui.document_view import DocumentView
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)


# -- notices ------------------------------------------------------------------------------
def no_text_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool", "No editable text here (scanned image or outlined text)."
    )


def ocr_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool",
        "This text is an invisible OCR layer over an image; it cannot be edited here.",
    )


def failed_notice() -> str:
    return QCoreApplication.translate("TextEditTool", "The text could not be changed.")


def permission_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool",
        "Editing page text is not permitted by this document’s security settings.",
    )


def too_complex_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool", "This page is too complex to edit (content larger than {size} MB)."
    ).format(size=MAX_CONTENT_MB)


def one_style_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool",
        "Only text in a single style can be edited at once; the selection was reduced.",
    )


def xobject_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool",
        "This text belongs to an embedded graphic (form XObject); it cannot be edited here.",
    )


def direction_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool", "Right-to-left and vertical text cannot be edited."
    )


def duplicate_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool",
        "Part of this text is drawn twice (simulated bold); select the whole doubled text.",
    )


def no_font_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool", "No installed font can show these characters."
    )


def stale_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool", "The text changed while it was being edited; the edit was not applied."
    )


def invalid_text_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool", "Control and invisible formatting characters cannot be used in page text."
    )


def full_save_notice() -> str:
    return QCoreApplication.translate(
        "TextEditTool", "After editing page text, the next save rewrites the whole file."
    )


def reason_notice(reason: EditReason) -> str:
    """The status notice for a refused or failed edit."""
    if reason is EditReason.NO_TEXT:
        return no_text_notice()
    if reason is EditReason.INVISIBLE:
        return ocr_notice()
    if reason is EditReason.PERMISSION:
        return permission_notice()
    if reason is EditReason.TOO_COMPLEX:
        return too_complex_notice()
    if reason is EditReason.MULTI_SPAN:
        return one_style_notice()
    if reason is EditReason.XOBJECT:
        return xobject_notice()
    if reason is EditReason.DIRECTION:
        return direction_notice()
    if reason is EditReason.DUPLICATE:
        return duplicate_notice()
    if reason is EditReason.NO_FONT:
        return no_font_notice()
    if reason is EditReason.STALE:
        return stale_notice()
    if reason is EditReason.INVALID_TEXT:
        return invalid_text_notice()
    return failed_notice()


#: Refusals after which the editor is not reopened with the typed text.
_NO_REOPEN = (EditReason.PERMISSION, EditReason.STALE)


def result_notices(result: TextEditResult) -> list[str]:
    """Status notices for a successful edit (substitution, extension, narrowing)."""
    notices: list[str] = []
    if result.substituted:
        notices.append(
            QCoreApplication.translate(
                "TextEditTool",
                "Replaced with {family}: the document’s font lacks some of these characters.",
            ).format(family=result.font_family)
        )
    if result.extended:
        notices.append(
            QCoreApplication.translate(
                "TextEditTool", "Neighbouring characters were included in the edit: “{text}”"
            ).format(text=result.old_text.replace("\xa0", " "))
        )
    if result.overflow:
        notices.append(
            QCoreApplication.translate(
                "TextEditTool", "The new text is wider than the original and overflows."
            )
        )
    elif result.narrowed:
        notices.append(
            QCoreApplication.translate(
                "TextEditTool", "The new text is wider than the original and was narrowed to fit."
            )
        )
    return notices


class TextEditTool(_TextSelecting, Tool):
    """Select and replace runs of the page's own text (one undo step per edit)."""

    name = "textedit"
    message = Signal(str)

    def __init__(self, document_view: DocumentView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.document_view = document_view
        #: Installed fonts for the edits (None: discovered by the core; tests inject).
        self.fonts: SystemFonts | None = None
        self._init_text()
        # A click on the selection opens the editor once the double-click interval has
        # passed without a double-click (which selects the span instead).
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.timeout.connect(self.open_editor)
        self._open_pending = False
        self._had_tracking = False
        # The document already told that the next save rewrites the whole file.
        self._full_save_noticed: PdfDocument | None = None
        # The editor is being committed by deactivate() (no reopening after a refusal).
        self._leaving = False
        document_view.document_changed.connect(self._reset)
        document_view.document_changed.connect(self._forget_document)
        document_view.textedit_editor.committed.connect(self._on_committed)

    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.IBeamCursor)

    @property
    def editor_open(self) -> bool:
        return self.document_view.textedit_editor.is_open

    # -- state ----------------------------------------------------------------------------
    def activate(self, view: PageView) -> None:
        super().activate(view)
        # Hover frames need moves without a button pressed.
        self._had_tracking = view.viewport().hasMouseTracking()
        view.viewport().setMouseTracking(True)
        view.viewport().setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.document_view.annot_selection.clear()
        if self.fonts is None:
            # The first substitution needs the installed fonts: read them off the GUI
            # thread now (once per process).
            fontmatch.warm_system_fonts()

    def deactivate(self) -> None:
        self._leaving = True
        try:
            self.document_view.textedit_editor.commit()
        finally:
            self._leaving = False
        self._reset()
        self.text_selection.clear()
        self.document_view.text_hover.clear()
        if self.view is not None:
            self.view.viewport().setMouseTracking(self._had_tracking)
        super().deactivate()

    def _reset(self) -> None:
        self._open_timer.stop()
        self._open_pending = False
        self._text_drag = None
        self._last_double = None

    def _forget_document(self) -> None:
        self._full_save_noticed = None

    def _doc(self) -> PdfDocument | None:
        return self._text_doc()

    def _set_cursor(self, ibeam: bool) -> None:
        if self.view is not None:
            shape = Qt.CursorShape.IBeamCursor if ibeam else Qt.CursorShape.ArrowCursor
            self.view.viewport().setCursor(QCursor(shape))

    def _notify(self, *texts: str) -> None:
        text = " ".join(t for t in texts if t)
        if text:
            self.message.emit(text)

    # -- hit testing ------------------------------------------------------------------------
    def _check_char(self, pt: PageText, ref: CharRef | None) -> str | None:
        """The notice when the char under a click cannot be edited (None if it can)."""
        if ref is None:
            return no_text_notice()
        if pt.is_invisible(ref):
            return ocr_notice()
        if not editable(pt, ref.index):
            return failed_notice()
        return None

    def _clamp_to_line(self, pt: PageText, anchor: CharRef | int, focus: CharRef) -> int:
        first, last = pt.line_range(anchor)
        return min(max(focus.index, first.index), last.index)

    def _current_run(self) -> Run | None:
        sel = self.text_selection
        if sel.is_empty or sel.page is None or sel.anchor is None or sel.focus is None:
            return None
        return Run.from_refs(sel.anchor, sel.focus)

    # -- mouse ------------------------------------------------------------------------------
    def mouse_press(self, event: ToolEvent) -> bool:
        if event_button(event) != Qt.MouseButton.LeftButton:
            return self._text_drag is not None
        self.document_view.commit_pending_edits()
        self._open_timer.stop()
        self._open_pending = False
        page, pos = event.page_index, event.page_pos
        sel = self.text_selection
        doc = self._doc()
        if doc is None or page is None or pos is None:
            sel.clear()
            return False  # outside the pages: the view pans
        if not doc.can_modify:
            self._notify(permission_notice())
            return True
        pt = self._page_text(page)
        if pt is None:
            return True
        px = viewport_pos(event)
        if self._is_triple(page, px):
            return True  # the third click of a triple-click keeps the span
        self._last_double = None
        ref = None if pt.is_empty else pt.hit(pos, self._hit_tolerance())
        shift = bool(event.modifiers & Qt.KeyboardModifier.ShiftModifier)
        if shift and sel.page == page and sel.anchor is not None:
            focus = ref if ref is not None else pt.hit(pos, math.inf)
            if focus is not None:
                sel.set(page, sel.anchor, self._clamp_to_line(pt, sel.anchor, focus))
            self._text_drag = _TextDrag(page, pos, px, sel.anchor, active=True)
            return True
        notice = self._check_char(pt, ref)
        if notice is not None or ref is None:
            sel.clear()
            self._notify(notice or no_text_notice())
            return True
        run = self._current_run()
        if run is not None and sel.page == page and run.contains(ref.index):
            # A second click on the selection opens the editor (on release).
            self._open_pending = True
        else:
            w0, w1 = pt.word_range(ref)
            word = clamp_to_span(pt, Run.from_refs(w0, w1), ref.index)
            sel.set(page, word.first, word.last)
        self._text_drag = _TextDrag(page, pos, px, ref)
        return True

    def mouse_move(self, event: ToolEvent) -> bool:
        drag = self._text_drag
        if drag is None:
            if event.buttons != Qt.MouseButton.NoButton:
                return False
            target = self.document_view.text_hover.hover(event.page_index, event.page_pos)
            self._set_cursor(target is not None)
            return False
        if self.view is None or not 0 <= drag.page < self.view.page_count:
            self._text_drag = None
            return True
        pt = self._page_text(drag.page)
        if pt is None or pt.is_empty or drag.anchor is None:
            return True
        if not drag.active:
            moved = viewport_pos(event) - drag.start_px
            if moved.manhattanLength() < QApplication.startDragDistance():
                return True
            drag.active = True
            self._open_pending = False
        pos = self._drag_pos(drag.page, event)
        focus = pt.hit(pos, math.inf)
        if focus is not None:
            try:
                index = self._clamp_to_line(pt, drag.anchor, focus)
                self.text_selection.set(drag.page, drag.anchor, index)
            except IndexError:  # the page text changed under the drag
                self._text_drag = None
        return True

    def mouse_release(self, event: ToolEvent) -> bool:
        drag = self._text_drag
        if drag is None:
            return False
        if event_button(event) != Qt.MouseButton.LeftButton:
            return True
        self._text_drag = None
        if self._open_pending:
            self._open_pending = False
            self._open_timer.start(QApplication.doubleClickInterval())
        return True

    def mouse_double_click(self, event: ToolEvent) -> bool:
        """Select the span (the chars of one style on the line) under the pointer."""
        if event_button(event) != Qt.MouseButton.LeftButton:
            return self._text_drag is not None
        self._open_timer.stop()
        self._open_pending = False
        self._text_drag = None
        page, pos = event.page_index, event.page_pos
        doc = self._doc()
        if doc is None or page is None or pos is None:
            return False
        pt = self._page_text(page)
        if pt is None:
            return True
        ref = None if pt.is_empty else pt.hit(pos, self._hit_tolerance())
        if self._check_char(pt, ref) is not None or ref is None:
            return True  # the press already said why
        self._last_double = (time.monotonic(), viewport_pos(event), page)
        span = span_run(pt, ref.index)
        self.text_selection.set(page, span.first, span.last)
        return True

    # -- keys ---------------------------------------------------------------------------
    def key_press(self, event: ToolEvent) -> bool:
        qt_event = event.qt_event
        if not isinstance(qt_event, QKeyEvent):
            return False
        key = qt_event.key()
        if key == Qt.Key.Key_Escape:
            self._open_timer.stop()
            return self.text_escape()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            if self._current_run() is None:
                return False
            self.open_editor()
            return True
        return False

    def paint_overlay(self, painter: QPainter) -> None:
        self.text_selection.paint(painter)

    # -- editing -----------------------------------------------------------------------------
    def open_editor(self) -> bool:
        """Open the run editor over the selection (clamped to the span of its anchor)."""
        self._open_timer.stop()
        sel = self.text_selection
        doc = self._doc()
        page = sel.page
        if doc is None or page is None or sel.anchor is None or sel.focus is None:
            return False
        if not doc.can_modify:
            self._notify(permission_notice())
            return False
        pt = self._page_text(page)
        if pt is None:
            return False
        notice = self._check_char(pt, sel.anchor)
        if notice is not None:
            self._notify(notice)
            return False
        run = selection_run(pt, sel.anchor, sel.focus)
        if run != Run.from_refs(sel.anchor, sel.focus):
            self._notify(one_style_notice())
            sel.set(page, run.first, run.last)
        try:
            self.document_view.textedit_editor.open(page, run)
        except ValueError as exc:
            log.warning("cannot open the page text editor: %s", exc)
            self._notify(failed_notice())
            return False
        return True

    def _on_committed(self, anchor: object, text: object) -> None:
        """The run editor's ``committed``: apply the edit checked against the anchor."""
        if isinstance(anchor, TextRunAnchor):
            self.apply_edit(anchor.page, anchor.run, str(text), anchor=anchor)

    def apply_edit(
        self, page: int, run: Run, text: str, *, anchor: TextRunAnchor | None = None
    ) -> ReplaceTextCommand | None:
        """Replace ``run`` of ``page`` by ``text`` (the editor's commit): one
        :class:`ReplaceTextCommand` pushed, notices emitted. None when refused.

        With the editor's ``anchor``, the run must still hold the text the editor was
        opened on (``anchor.old_text``; else the stale notice and nothing changes), and a
        refused edit reopens the editor with the typed text (not after a permission or
        stale refusal, nor while the tool is being left)."""
        dv = self.document_view
        doc = self._doc()
        if doc is None:
            return None
        if not doc.can_modify:
            self._notify(permission_notice())
            return None
        expect = anchor.old_text if anchor is not None else None
        try:
            cmd = ReplaceTextCommand(doc, page, run, text, fonts=self.fonts, expect_text=expect)
            cmd.apply_now()
        except TextEditError as exc:
            log.info("page text edit refused (%s): %s", exc.reason, exc)
            self._notify(reason_notice(exc.reason))
            if anchor is not None and exc.reason not in _NO_REOPEN:
                self._reopen(anchor, text)
            return None
        except (DocumentError, ValueError, IndexError) as exc:
            log.warning("page text edit failed: %s", exc)
            self._notify(failed_notice())
            if anchor is not None and isinstance(exc, ValueError):
                self._reopen(anchor, text)
            return None
        dv.push(cmd)
        self.text_selection.clear()
        notices = result_notices(cmd.result) if cmd.result is not None else []
        if self._full_save_noticed is not doc and not doc.can_save_incrementally():
            self._full_save_noticed = doc
            notices.append(full_save_notice())
        self._notify(*notices)
        return cmd

    def _reopen(self, anchor: TextRunAnchor, typed: str) -> None:
        """Open the editor again over ``anchor``'s run with ``typed`` (after a refusal),
        when the tool is still active on that document and page."""
        editor = self.document_view.textedit_editor
        doc = self._doc()
        if self._leaving or self.view is None or editor.is_open or doc is None:
            return
        if doc.page_index(anchor.page_id) != anchor.page:
            return
        try:
            editor.open(anchor.page, anchor.run, typed=typed)
        except ValueError as exc:
            log.info("cannot reopen the page text editor: %s", exc)


__all__ = [
    "TextEditTool",
    "direction_notice",
    "duplicate_notice",
    "failed_notice",
    "full_save_notice",
    "invalid_text_notice",
    "no_font_notice",
    "no_text_notice",
    "ocr_notice",
    "one_style_notice",
    "permission_notice",
    "reason_notice",
    "result_notices",
    "stale_notice",
    "too_complex_notice",
    "xobject_notice",
]
