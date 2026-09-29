"""DocumentView: the open document, its PageView and its undo stack."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Signal
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QVBoxLayout, QWidget

from pdfeditor.core.document import PasswordCallback, PdfDocument
from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)


class DocumentView(QWidget):
    """Owns the current PdfDocument (SDI) and one QUndoStack for it."""

    document_changed = Signal()  # a document was opened or closed
    path_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.undo_stack = QUndoStack(self)
        self.page_view = PageView(parent=self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.page_view)
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
        document = PdfDocument.open(path, password_cb=password_cb)
        self._replace(document)
        return document

    def close_document(self) -> None:
        self._replace(None)

    def _replace(self, document: PdfDocument | None) -> None:
        old = self._document
        if old is not None:
            old.path_changed.disconnect(self.path_changed)
        self.undo_stack.clear()
        self._document = document
        if document is not None:
            document.path_changed.connect(self.path_changed)
        self.page_view.set_document(document)
        if old is not None:
            old.close()
        self.undo_stack.setClean()
        self.document_changed.emit()

    def save(self) -> None:
        """Save in place. Raises SaveError."""
        if self._document is None:
            return
        self._document.save()
        self.undo_stack.setClean()

    def save_as(self, path: str) -> None:
        """Save under a new path and continue editing it. Raises SaveError."""
        if self._document is None:
            return
        self._document.save_as(path)
        self.undo_stack.setClean()

    def shutdown(self) -> None:
        """Stop background rendering and close the document (application exit)."""
        self.page_view.service.stop()
        if self._document is not None:
            self._document.close()
