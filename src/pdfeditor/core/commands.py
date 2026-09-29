"""Undo commands: the only writers to a PdfDocument."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoCommand

from pdfeditor.core.document import PdfDocument


class DocumentCommand(QUndoCommand):
    """Base class for commands that mutate a PdfDocument (emitting page_changed etc.)."""

    def __init__(self, doc: PdfDocument, text: str, parent: QUndoCommand | None = None):
        super().__init__(text, parent)
        self.doc = doc


class RotatePageCommand(DocumentCommand):
    """Rotate one page by ``delta`` degrees (multiple of 90).

    Consecutive rotations of the same page merge into one undo step; rotating back to
    the original orientation makes the merged command obsolete.
    """

    ID = 1001

    def __init__(self, doc: PdfDocument, page: int, delta: int) -> None:
        super().__init__(doc, QCoreApplication.translate("Commands", "Rotate page"))
        if delta % 90:
            raise ValueError(f"rotation delta must be a multiple of 90: {delta}")
        self.page = page
        self.old_rotation = doc.page_rotation(page)
        self.new_rotation = (self.old_rotation + delta) % 360

    def redo(self) -> None:
        self.doc.set_page_rotation(self.page, self.new_rotation)

    def undo(self) -> None:
        self.doc.set_page_rotation(self.page, self.old_rotation)

    def id(self) -> int:
        return self.ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        if (
            not isinstance(other, RotatePageCommand)
            or other.doc is not self.doc
            or other.page != self.page
        ):
            return False
        self.new_rotation = other.new_rotation
        if self.new_rotation == self.old_rotation:
            self.setObsolete(True)
        return True
