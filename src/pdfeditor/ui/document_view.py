"""DocumentView: the open document, its PageView and its undo stack."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Signal
from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtWidgets import QVBoxLayout, QWidget

from pdfeditor.core.document import PasswordCallback, PdfDocument
from pdfeditor.core.forms import XfaKind
from pdfeditor.ui.banner import InfoBanner
from pdfeditor.ui.overlays.annot_editor import AnnotTextEditor
from pdfeditor.ui.overlays.annot_items import AnnotSelection
from pdfeditor.ui.overlays.field_editor import FieldEditorOverlay
from pdfeditor.ui.overlays.field_items import FieldLayer
from pdfeditor.ui.overlays.text_selection import TextSelection
from pdfeditor.ui.overlays.textedit_editor import TextRunEditor
from pdfeditor.ui.overlays.textedit_items import TextHover
from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)


class DocumentView(QWidget):
    """Owns the current PdfDocument (SDI) and one QUndoStack for it."""

    document_changed = Signal()  # a document was opened or closed
    path_changed = Signal(str)
    #: A command failed while the stack ran it: (``"undo"``, ``"redo"`` or ``"push"``,
    #: the exception). The undo history is already cleared and the document marked
    #: modified (docs/ARCHITECTURE.md Deviation 90).
    history_failed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.undo_stack = QUndoStack(self)
        self.page_view = PageView(parent=self)
        # Document-level notices (XFA, form permissions) above the pages.
        self.banner = InfoBanner(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.banner)
        layout.addWidget(self.page_view)
        # Form field highlights of the current document (the form tool reuses it).
        self.field_layer = FieldLayer(self.page_view, self)
        # The floating form field editor (driven by the form tool, which pushes the
        # commands for its ``committed`` values).
        self.field_editor = FieldEditorOverlay(self.page_view, parent=self)
        # The selected text box/stamp and the text box editor (driven by the annotation
        # tools, which push the commands).
        self.annot_selection = AnnotSelection(self.page_view, self)
        self.annot_editor = AnnotTextEditor(self.page_view, parent=self)
        # The selected page text (Select Text and markup tools, Edit > Copy Text).
        self.text_selection = TextSelection(self.page_view, self)
        # The Edit Page Text tool's hover frames and run editor (M7; the tool pushes a
        # ReplaceTextCommand for each ``run_committed``).
        self.text_hover = TextHover(self.page_view, self)
        self.textedit_editor = TextRunEditor(self.page_view, parent=self)
        self._document: PdfDocument | None = None

    @property
    def document(self) -> PdfDocument | None:
        return self._document

    @property
    def is_dirty(self) -> bool:
        return self._document is not None and not self.undo_stack.isClean()

    @property
    def file_name(self) -> str:
        if self._document is None or self._document.path is None:
            return ""
        return os.path.basename(self._document.path)

    def open(self, path: str, password_cb: PasswordCallback | None = None) -> PdfDocument:
        """Open ``path`` replacing the current document. Raises OpenError/PasswordRequired."""
        self.commit_pending_edits()
        document = PdfDocument.open(path, password_cb=password_cb)
        self._replace(document)
        return document

    def close_document(self) -> None:
        self.commit_pending_edits()
        self._replace(None)

    def commit_pending_edits(self) -> None:
        """Commit an open field, text box or page text editor, and the active tool's
        deferred action (``Tool.flush_pending``, e.g. a double-clicked word to mark), so
        that ``is_dirty`` and saves include them.

        Called before saving, closing or replacing the document (and by ``MainWindow``
        before it consults ``is_dirty``).
        """
        self.field_editor.commit()
        self.annot_editor.commit()
        self.textedit_editor.commit()
        manager = self.page_view.tool_manager
        tool = manager.active_tool if manager is not None else None
        if tool is not None:
            tool.flush_pending()

    def push(self, command: QUndoCommand) -> None:
        """Push ``command`` on the undo stack; **every** command push goes through here.

        A pending field edit is committed first, so it becomes its own undo step before
        ``command`` and is never pushed re-entrantly from inside ``command.redo()`` (e.g.
        a rotation closing the editor). Build commands that snapshot document state
        after :meth:`commit_pending_edits` (or make that snapshot in ``redo()``).
        """
        self.commit_pending_edits()
        self.undo_stack.push(command)
        self._check(command, "push")

    def undo(self) -> None:
        """Commit a pending edit, then undo the last command; a failure clears the
        history (see :meth:`_check`)."""
        self.commit_pending_edits()
        index = self.undo_stack.index()
        if index <= 0:
            return
        command = self.undo_stack.command(index - 1)
        self.undo_stack.undo()
        self._check(command, "undo")

    def redo(self) -> None:
        """Commit a pending edit, then redo the next command (failure: see :meth:`_check`)."""
        self.commit_pending_edits()
        index = self.undo_stack.index()
        if index >= self.undo_stack.count():
            return
        command = self.undo_stack.command(index)
        self.undo_stack.redo()
        self._check(command, "redo")

    def _check(self, command: QUndoCommand | None, kind: str) -> None:
        """After the stack ran ``command``: if it recorded an error (``DocumentCommand``
        never raises from ``redo``/``undo``), the document no longer matches the stack's
        index, so the history is cleared and the document marked modified (Discard
        must still be asked before closing), then :attr:`history_failed` is emitted."""
        error = getattr(command, "error", None)
        if error is None:
            return
        log.warning("%s failed, clearing the undo history: %s", kind, error)
        self.undo_stack.clear()
        self.undo_stack.resetClean()
        self.history_failed.emit(kind, error)

    def _replace(self, document: PdfDocument | None) -> None:
        old = self._document
        if old is not None:
            old.path_changed.disconnect(self.path_changed)
            old.reloaded.disconnect(self._refresh_banner)
            old.structure_changed.disconnect(self._refresh_banner)
        # Pending edits were committed by the callers; anything left belongs to the old
        # document and is dropped (its undo stack is being cleared).
        self.field_editor.close()
        self.annot_editor.close()
        self.textedit_editor.close()
        self.undo_stack.clear()
        if old is not None:
            # The cleared commands' undo copies of deleted pages (memory, temp files).
            old.snapshots.clear()
        self._document = document
        if document is not None:
            document.path_changed.connect(self.path_changed)
            # After the document's own slot (connected at construction): xfa_kind is
            # already recomputed when the banner is refreshed. Page operations change it
            # too (inserting form pages, a form losing its last fields).
            document.reloaded.connect(self._refresh_banner)
            document.structure_changed.connect(self._refresh_banner)
        self.page_view.set_document(document)
        self.field_layer.set_document(document)  # after the view: its items need PageItems
        self.field_editor.set_document(document)
        self.annot_editor.set_document(document)
        self.textedit_editor.set_document(document)
        self.annot_selection.set_document(document)  # after the view (PageItems)
        self.text_selection.set_document(document)
        self.text_hover.set_document(document)  # after the view (PageItems)
        if old is not None:
            old.close()
        self.undo_stack.setClean()
        self.banner.clear()  # a closed banner reappears for the next document
        self._refresh_banner()
        self.document_changed.emit()

    def save(self) -> None:
        """Save in place. Raises SaveError."""
        if self._document is None:
            return
        self.commit_pending_edits()
        self._prepare_save(self._document)
        self._document.save()
        self.undo_stack.setClean()
        self._refresh_banner()  # the save may have stripped the XFA

    def save_as(self, path: str) -> None:
        """Save under a new path and continue editing it. Raises SaveError."""
        if self._document is None:
            return
        self.commit_pending_edits()
        self._prepare_save(self._document)
        self._document.save_as(path)
        self.undo_stack.setClean()
        self._refresh_banner()

    def banner_message(self) -> tuple[str, str] | None:
        """(text, kind) the banner should show for the current document, or None."""
        doc = self._document
        if doc is None:
            return None
        if doc.xfa_kind is XfaKind.DYNAMIC:
            return (
                self.tr(
                    "This form uses dynamic XFA, which cannot be filled here. Open it in "
                    "Adobe Acrobat Reader, print it to PDF (Microsoft Print to PDF), then "
                    "fill the printed copy in PDF Editor as a flat form."
                ),
                "warning",
            )
        if doc.is_form and not doc.can_fill_forms:
            return (
                self.tr("Form filling is not permitted by this document’s security settings."),
                "info",
            )
        if doc.xfa_kind is XfaKind.STATIC:
            return (
                self.tr(
                    "This form contains XFA data; saving will convert it to a standard PDF form."
                ),
                "info",
            )
        return None

    def _refresh_banner(self) -> None:
        """Show the banner for the current state; a message the user closed stays closed
        (until the document changes), a message that no longer applies disappears."""
        message = self.banner_message()
        if message is None:
            self.banner.clear()
        elif message != self.banner.message:
            self.banner.show_message(*message)

    @staticmethod
    def _prepare_save(document: PdfDocument) -> None:
        """Document changes that belong to every save (Save and Save As).

        A filled static XFA form loses its /XFA: XFA-aware viewers would otherwise show
        the stale XFA datasets instead of the AcroForm values just entered. So does a
        static XFA form whose pages were inserted, deleted or moved (the XFA template
        would no longer match the pages).
        """
        if document.xfa_kind is XfaKind.STATIC and (
            document.form_edited or document.structure_edited
        ):
            document.strip_xfa()

    def shutdown(self) -> None:
        """Stop background rendering and close the document (application exit)."""
        self.field_editor.close()  # pending edits were resolved by the window
        self.annot_editor.close()
        self.textedit_editor.close()
        self.annot_selection.clear()
        self.text_selection.clear()
        self.text_hover.clear()
        service = self.page_view.service
        service.stop()  # asks the worker to stop; waits up to 1 s
        if self._document is not None:
            self._document.close()  # waits for a render in progress (lock)
        # Never let the QThread be destroyed while running: the worker exits right after
        # its current render (the stop request is first in its queue).
        service.worker.wait()
