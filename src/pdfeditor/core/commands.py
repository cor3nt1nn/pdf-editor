"""Undo commands: the only writers to a PdfDocument."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QCoreApplication, QRectF
from PySide6.QtGui import QUndoCommand

from pdfeditor.core.annotations import AnnotInfo, AnnotKind, AnnotSpec, Color, new_name, spec_from
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
            # that was on before (on any page of the field).
            self.old_value = doc.field_button_state(info)
            if isinstance(new_value, bool):
                new_value = info.on_state if new_value and info.on_state else new_value
            self.new_value = new_value
        else:
            self.old_value = info.value
            self.new_value = new_value
        self._applied = False

    def apply_now(self) -> None:
        """Apply the change immediately (raises :class:`FieldError` if the field is gone).

        The next ``redo()`` (the one ``QUndoStack.push`` performs) is then skipped, so a
        failure is reported before anything reaches the undo stack (an exception raised
        inside ``push`` would leave a broken command on the stack).
        """
        self.redo()
        self._applied = True

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
        if self._applied:
            self._applied = False
            return
        self._apply(self.new_value, self.font_size)

    def undo(self) -> None:
        restore = None
        if self.font_size is not None and self.font_size != self.old_font_size:
            restore = self.old_font_size
        self._apply(self.old_value, restore)


class _ImmediateCommand(DocumentCommand):
    """A command that can be applied before it is pushed (see :meth:`apply_now`).

    Subclasses implement ``_redo()`` / ``_undo()``.
    """

    def __init__(self, doc: PdfDocument, text: str) -> None:
        super().__init__(doc, text)
        self._applied = False

    def apply_now(self) -> None:
        """Apply the change immediately (raises :class:`AnnotError` if it fails, e.g. the
        annotation is gone).

        The next ``redo()`` (the one ``QUndoStack.push`` performs) is then skipped, so a
        failure is reported before anything reaches the undo stack.
        """
        self._redo()
        self._applied = True

    def redo(self) -> None:
        if self._applied:
            self._applied = False
            return
        self._redo()

    def undo(self) -> None:
        self._undo()

    def _redo(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _undo(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class AddAnnotCommand(_ImmediateCommand):
    """Create a text box or stamp from ``spec`` (one undo step).

    The /NM is fixed here (``spec.name``, or a new uuid4), so undo/redo cycles and saves
    keep the same identity. The first redo creates the annotation (a text box's height
    then hugs its text); later redos re-create the exact snapshot :attr:`info`.
    """

    def __init__(self, doc: PdfDocument, spec: AnnotSpec) -> None:
        text = (
            QCoreApplication.translate("Commands", "Add stamp")
            if spec.kind is AnnotKind.STAMP
            else QCoreApplication.translate("Commands", "Add text")
        )
        super().__init__(doc, text)
        self.spec = replace(spec, name=spec.name or new_name())
        # Snapshot of the created annotation (None before the first redo).
        self.info: AnnotInfo | None = None

    @property
    def page(self) -> int:
        return self.spec.page

    @property
    def name(self) -> str:
        return self.spec.name

    def _redo(self) -> None:
        if self.info is None:
            fit = self.spec.kind is AnnotKind.TEXT
            self.info = self.doc.add_annot(self.spec, fit_height=fit)
        else:
            self.info = self.doc.add_annot(spec_from(self.info))

    def _undo(self) -> None:
        self.doc.delete_annot(self.spec.page, self.spec.name)


class EditAnnotCommand(_ImmediateCommand):
    """Change the text, style or rect (page space) of annotation ``info`` (one undo step).

    ``None`` keeps a property. ``fit_height`` (default: ``text is not None``) makes a
    text box's height hug its text after the change. Undo restores the text/colour that
    were changed and always the old rect and font size (a text change refits the height,
    a stamp resize rescales the glyph). The annotation is resolved by ``(page, name)`` on
    every redo/undo, so the command survives saves; :class:`AnnotError` if it is gone.
    """

    def __init__(
        self,
        doc: PdfDocument,
        info: AnnotInfo,
        *,
        text: str | None = None,
        font_size: float | None = None,
        color: Color | None = None,
        rect: QRectF | None = None,
        fit_height: bool | None = None,
    ) -> None:
        if text is None and font_size is None and color is None and rect is None:
            raise ValueError("EditAnnotCommand needs at least one change")
        super().__init__(doc, self._label(info, text, font_size, color, rect))
        self.info = info
        self.new_text = text
        self.new_font_size = font_size
        self.new_color = color
        self.new_rect = QRectF(rect) if rect is not None else None
        self.fit_height = (text is not None) if fit_height is None else fit_height

    @staticmethod
    def _label(
        info: AnnotInfo,
        text: str | None,
        font_size: float | None,
        color: Color | None,
        rect: QRectF | None,
    ) -> str:
        if text is not None:
            return QCoreApplication.translate("Commands", "Edit text")
        if font_size is None and color is None and rect is not None:
            same_size = (
                abs(rect.width() - info.rect.width()) < 1e-6
                and abs(rect.height() - info.rect.height()) < 1e-6
            )
            if same_size:
                return QCoreApplication.translate("Commands", "Move annotation")
            return QCoreApplication.translate("Commands", "Resize annotation")
        return QCoreApplication.translate("Commands", "Change text style")

    @property
    def page(self) -> int:
        return self.info.page

    @property
    def name(self) -> str:
        return self.info.name

    def _redo(self) -> None:
        self.doc.update_annot(
            self.info.page,
            self.info.name,
            text=self.new_text,
            font_size=self.new_font_size,
            color=self.new_color,
            rect=self.new_rect,
            fit_height=self.fit_height,
        )

    def _undo(self) -> None:
        old = self.info
        self.doc.update_annot(
            old.page,
            old.name,
            text=old.text if self.new_text is not None else None,
            font_size=old.font_size,
            color=old.color if self.new_color is not None else None,
            rect=QRectF(old.rect),
        )


class DeleteAnnotCommand(_ImmediateCommand):
    """Delete annotation ``info``; undo re-creates it from the snapshot (same /NM, rect,
    rotation and style; appended at the end of the page's /Annots)."""

    def __init__(self, doc: PdfDocument, info: AnnotInfo) -> None:
        super().__init__(doc, QCoreApplication.translate("Commands", "Delete annotation"))
        self.info = info

    @property
    def page(self) -> int:
        return self.info.page

    @property
    def name(self) -> str:
        return self.info.name

    def _redo(self) -> None:
        self.doc.delete_annot(self.info.page, self.info.name)

    def _undo(self) -> None:
        self.doc.add_annot(spec_from(self.info))
