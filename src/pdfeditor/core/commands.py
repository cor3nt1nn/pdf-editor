"""Undo commands: the only writers to a PdfDocument."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoCommand

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.forms import FieldKind, WidgetInfo


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


class SetFieldValueCommand(DocumentCommand):
    """Set one form field value (one undo step per commit, never merged).

    ``new_value``: a string for text/combo/list fields ("" clears), ``True``/``False``
    for a checkbox/radio widget (``True`` selects ``info``'s on state). ``font_size``
    (0 = auto-size) is applied with the value; undo restores the old value and, if it was
    changed, the old font size. The widget is re-resolved on every redo/undo (by xref,
    then name and rect), so the command survives saves that renumber objects.
    """

    def __init__(
        self,
        doc: PdfDocument,
        info: WidgetInfo,
        new_value: str | bool,
        font_size: float | None = None,
    ) -> None:
        super().__init__(doc, QCoreApplication.translate("Commands", "Edit form field"))
        self.info = info
        self.font_size = font_size
        self.old_font_size = info.font_size
        self.new_value: str | bool
        self.old_value: str | bool
        if info.kind in (FieldKind.CHECKBOX, FieldKind.RADIO):
            # Values are on-state names, so that undo re-selects the sibling radio button
            # that was on before (siblings are looked up on the same page).
            self.old_value = next(
                (
                    w.on_state
                    for w in doc.widgets(info.page)
                    if w.field_xref == info.field_xref and w.is_on
                ),
                "Off",
            )
            if isinstance(new_value, bool):
                new_value = info.on_state if new_value and info.on_state else new_value
            self.new_value = new_value
        else:
            self.old_value = info.value
            self.new_value = new_value

    def _apply(self, value: str | bool, font_size: float | None) -> None:
        self.doc.set_field_value(
            self.info.page,
            self.info.xref,
            value,
            font_size=font_size,
            name=self.info.name,
            unrotated_rect=self.info.unrotated_rect,
        )

    def redo(self) -> None:
        self._apply(self.new_value, self.font_size)

    def undo(self) -> None:
        restore = None
        if self.font_size is not None and self.font_size != self.old_font_size:
            restore = self.old_font_size
        self._apply(self.old_value, restore)
