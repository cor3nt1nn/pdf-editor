"""Undo commands: the only writers to a PdfDocument."""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable, Sequence
from dataclasses import replace

from PySide6.QtCore import QCoreApplication, QRectF, QSizeF
from PySide6.QtGui import QUndoCommand

from pdfeditor.core.annotations import (
    MARKUP_KINDS,
    AnnotInfo,
    AnnotKind,
    AnnotSpec,
    Color,
    is_synthetic,
    new_name,
    spec_from,
)
from pdfeditor.core.document import (
    AnnotError,
    DocumentError,
    FieldError,
    PageError,
    PageId,
    PdfDocument,
)
from pdfeditor.core.forms import FieldKind, WidgetInfo
from pdfeditor.core.signature import ImageData

log = logging.getLogger(__name__)


class DocumentCommand(QUndoCommand):
    """Base class for commands that mutate a PdfDocument (emitting page_changed etc.).

    Page identity rule (docs/M6_PLAN.md D4): page insertions, deletions and moves never
    clear the undo stack, so a command never keeps a page *index* across calls. It
    captures ``doc.page_id(index)`` at construction, resolves the current index with
    :meth:`_index` (``doc.page_index(page_id)``) in every ``redo()``/``undo()``, and
    compares page ids (never indexes) in ``mergeWith``. Snapshots carrying a page index
    (``AnnotInfo``, ``AnnotSpec``, ``WidgetInfo``) are re-targeted with
    ``dataclasses.replace(info, page=index)`` before use. A page id that is no longer in
    the document raises a :class:`DocumentError` (the command's own error class); with a
    linear undo stack that only happens after a bug.

    Failure rule (docs/ARCHITECTURE.md Deviation 90): ``redo()``/``undo()`` never let an
    exception escape (it would be swallowed by the Qt override while ``QUndoStack`` moves
    its index anyway). They record it in :attr:`error` instead (None after a success);
    whoever ran the stack (``DocumentView``) checks it, reports it and clears the history.
    """

    def __init__(self, doc: PdfDocument, text: str, parent: QUndoCommand | None = None):
        super().__init__(text, parent)
        self.doc = doc
        #: The exception raised by the last ``redo()``/``undo()``, None if it succeeded.
        self.error: Exception | None = None

    def _guarded(self, step: Callable[[], None], what: str) -> None:
        """Run ``step``, recording (never raising) its exception in :attr:`error`."""
        self.error = None
        try:
            step()
        except Exception as exc:  # nothing may escape a Qt override
            log.warning("%s of %r failed: %s", what, self.text(), exc, exc_info=True)
            self.error = exc

    def _index(self, page_id: PageId, error: type[DocumentError] = DocumentError) -> int:
        """The current index of page ``page_id``; raises ``error`` if it is gone."""
        index = self.doc.page_index(page_id)
        if index is None:
            raise error(f"page {page_id} is not in the document")
        return index


class _ImmediateCommand(DocumentCommand):
    """A command that can be applied before it is pushed (see :meth:`apply_now`).

    Subclasses implement ``_redo()`` / ``_undo()``.
    """

    def __init__(self, doc: PdfDocument, text: str) -> None:
        super().__init__(doc, text)
        self._applied = False

    def apply_now(self) -> None:
        """Apply the change immediately (raises a :class:`DocumentError` if it fails:
        :class:`AnnotError` when the annotation is gone, :class:`PageError` when a page
        operation is refused...).

        The next ``redo()`` (the one ``QUndoStack.push`` performs) is then skipped, so a
        failure is reported before anything reaches the undo stack.
        """
        self._redo()
        self._applied = True

    def redo(self) -> None:
        if self._applied:
            self._applied = False
            self.error = None
            return
        self._guarded(self._redo, "redo")

    def undo(self) -> None:
        self._guarded(self._undo, "undo")

    def _redo(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _undo(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


# -- pages -------------------------------------------------------------------
class RotatePagesCommand(_ImmediateCommand):
    """Rotate the pages ``indexes`` by ``delta`` degrees (multiple of 90), one undo step.

    Consecutive rotations of the same set of pages merge into one undo step; rotating
    back to the original orientations makes the merged command obsolete. Refused
    (:class:`PageError` from :meth:`PdfDocument.set_page_rotation`) without
    :attr:`PdfDocument.can_assemble`.
    """

    ID = 1001

    def __init__(self, doc: PdfDocument, indexes: Sequence[int], delta: int) -> None:
        if delta % 90:
            raise ValueError(f"rotation delta must be a multiple of 90: {delta}")
        pages = sorted(set(int(i) for i in indexes))
        if not pages:
            raise ValueError("RotatePagesCommand needs at least one page")
        if len(pages) == 1:
            text = QCoreApplication.translate("Commands", "Rotate page")
        else:
            text = QCoreApplication.translate("Commands", "Rotate pages")
        super().__init__(doc, text)
        self.page_ids: list[PageId] = [doc.page_id(i) for i in pages]
        self.old_rotations: dict[PageId, int] = {
            pid: doc.page_rotation(i) for pid, i in zip(self.page_ids, pages, strict=True)
        }
        self.new_rotations: dict[PageId, int] = {
            pid: (old + delta) % 360 for pid, old in self.old_rotations.items()
        }

    @property
    def pages(self) -> list[int]:
        """The current indexes of the rotated pages."""
        return [self._index(pid, PageError) for pid in self.page_ids]

    def _apply(self, rotations: dict[PageId, int]) -> None:
        for pid in self.page_ids:
            self.doc.set_page_rotation(self._index(pid, PageError), rotations[pid])

    def _redo(self) -> None:
        self._apply(self.new_rotations)

    def _undo(self) -> None:
        self._apply(self.old_rotations)

    def id(self) -> int:
        return self.ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        if (
            not isinstance(other, RotatePagesCommand)
            or other.doc is not self.doc
            or set(other.page_ids) != set(self.page_ids)
        ):
            return False
        self.new_rotations = dict(other.new_rotations)
        if self.new_rotations == self.old_rotations:
            self.setObsolete(True)
        return True


class RotatePageCommand(RotatePagesCommand):
    """Rotate one page by ``delta`` degrees (see :class:`RotatePagesCommand`)."""

    def __init__(self, doc: PdfDocument, page: int, delta: int) -> None:
        super().__init__(doc, [page], delta)

    @property
    def page(self) -> int:
        return self.pages[0]

    @property
    def old_rotation(self) -> int:
        return self.old_rotations[self.page_ids[0]]

    @property
    def new_rotation(self) -> int:
        return self.new_rotations[self.page_ids[0]]


class DeletePagesCommand(_ImmediateCommand):
    """Delete the pages ``indexes`` (one undo step).

    The first redo stores a whole-document :meth:`PdfDocument.snapshot` in
    ``doc.snapshots`` before deleting; undo reopens it (:meth:`PdfDocument.restore_snapshot`
    with the page ids of that moment), so annotations, links, outline and form fields
    come back exactly. Later redos delete again (the document is then in the same state
    as at the first redo). Raises :class:`PageError`: "last_page" when every page would
    go, "snapshot" when the undo copy cannot be stored or read back (nothing deleted).

    The undo copy lives exactly as long as the command: ``QUndoStack`` deletes commands
    without undoing them (the redo branch dropped by a push after undo, ``clear()``),
    and shiboken then releases the Python object, whose ``weakref.finalize`` discards
    the copy from the store (the store's own ``clear()`` covers a closed document).
    """

    def __init__(self, doc: PdfDocument, indexes: Sequence[int]) -> None:
        pages = sorted(set(int(i) for i in indexes))
        if not pages:
            raise ValueError("DeletePagesCommand needs at least one page")
        if len(pages) == 1:
            text = QCoreApplication.translate("Commands", "Delete page")
        else:
            text = QCoreApplication.translate("Commands", "Delete pages")
        super().__init__(doc, text)
        self.page_ids: list[PageId] = [doc.page_id(i) for i in pages]
        # Page ids of the whole document before deleting (set by the first redo).
        self.saved_ids: list[PageId] | None = None
        self.snapshot_id: int | None = None

    def _redo(self) -> None:
        doc = self.doc
        indexes = [self._index(pid, PageError) for pid in self.page_ids]
        if not doc.can_assemble:
            raise PageError("page operations are not permitted by this document", "permission")
        if doc.page_count - len(indexes) < 1:
            raise PageError("a document must keep at least one page", "last_page")
        fresh = self.snapshot_id is None
        if fresh:
            ids = doc.page_ids()
            data = doc.snapshot()
            try:
                self.snapshot_id = self._store(data)
            except OSError as exc:
                raise PageError(f"no room for the undo copy: {exc}", "snapshot") from exc
            self.saved_ids = ids
        try:
            doc.delete_pages(indexes)
        except Exception:
            if fresh and self.snapshot_id is not None:
                doc.snapshots.discard(self.snapshot_id)
                self.snapshot_id = self.saved_ids = None
            raise

    def _store(self, data: bytes) -> int:
        """Put ``data`` in the document's snapshot store; the copy is discarded when this
        command is garbage-collected (deleted by the undo stack). Raises OSError."""
        store = self.doc.snapshots
        sid = store.put(data)
        finalizer = weakref.finalize(self, store.discard, sid)
        finalizer.atexit = False
        return sid

    def _undo(self) -> None:
        if self.snapshot_id is None or self.saved_ids is None:
            raise PageError("nothing to undo", "snapshot")
        try:
            data = self.doc.snapshots.get(self.snapshot_id)
        except (KeyError, OSError) as exc:
            raise PageError(f"the undo copy is gone: {exc}", "snapshot") from exc
        self.doc.restore_snapshot(data, self.saved_ids)


class MovePagesCommand(_ImmediateCommand):
    """Move the pages ``indexes`` (kept in their order) before the page now at
    ``target`` (``page_count`` = to the end), one undo step; undo restores the former
    order. A move that changes nothing is obsolete (:attr:`is_noop`; ``QUndoStack.push``
    then drops it)."""

    def __init__(self, doc: PdfDocument, indexes: Sequence[int], target: int) -> None:
        pages = sorted(set(int(i) for i in indexes))
        if not pages:
            raise ValueError("MovePagesCommand needs at least one page")
        if not 0 <= target <= doc.page_count:
            raise IndexError(f"move target out of range: {target}")
        for i in pages:
            doc.page_id(i)  # IndexError if out of range
        super().__init__(doc, QCoreApplication.translate("Commands", "Move pages"))
        self.old_order: list[PageId] = doc.page_ids()
        moving = [self.old_order[i] for i in pages]
        chosen = set(pages)
        rest = [pid for i, pid in enumerate(self.old_order) if i not in chosen]
        at = sum(1 for i in range(target) if i not in chosen)
        self.new_order: list[PageId] = rest[:at] + moving + rest[at:]
        self.page_ids = moving
        if self.is_noop:
            self.setObsolete(True)

    @property
    def is_noop(self) -> bool:
        return self.new_order == self.old_order

    def _redo(self) -> None:
        self.doc.reorder_pages(self.new_order)

    def _undo(self) -> None:
        self.doc.reorder_pages(self.old_order)


def _anchor(doc: PdfDocument, index: int) -> PageId | None:
    """The id of the page now at ``index`` (insertions go before it), None for the end."""
    if not 0 <= index <= doc.page_count:
        raise IndexError(f"insert position out of range: {index}")
    return doc.page_id(index) if index < doc.page_count else None


class _InsertCommand(_ImmediateCommand):
    """Common part of the insert commands: the position is remembered as the id of the
    page the new pages go before (None = at the end); undo deletes the inserted pages by
    id (their new objects become orphans that the next save drops)."""

    def __init__(self, doc: PdfDocument, text: str, index: int) -> None:
        super().__init__(doc, text)
        self.before: PageId | None = _anchor(doc, index)
        # Ids of the inserted pages (set by the first redo, reinstalled by later ones).
        self.page_ids: list[PageId] | None = None

    def _position(self) -> int:
        if self.before is None:
            return self.doc.page_count
        return self._index(self.before, PageError)

    @property
    def pages(self) -> list[int]:
        """Current indexes of the inserted pages ([] before the first redo)."""
        return [self._index(pid, PageError) for pid in self.page_ids or []]

    def _undo(self) -> None:
        if self.page_ids:
            self.doc.delete_pages(self.pages)


class InsertBlankPageCommand(_InsertCommand):
    """Insert an empty page of ``size`` points at ``index`` (one undo step)."""

    def __init__(self, doc: PdfDocument, index: int, size: QSizeF) -> None:
        super().__init__(doc, QCoreApplication.translate("Commands", "Insert blank page"), index)
        self.size = QSizeF(size)

    def _redo(self) -> None:
        pid = self.page_ids[0] if self.page_ids else None
        new = self.doc.insert_blank_page(self._position(), self.size, page_id=pid)
        self.page_ids = [new]


class InsertPagesCommand(_InsertCommand):
    """Insert every page of the sub-document ``data`` (``count`` pages, see
    :func:`pages.subdocument_bytes`; ``password`` decrypts it) at ``index``, one undo
    step. Every redo inserts from ``data``, never from the source file."""

    def __init__(
        self,
        doc: PdfDocument,
        data: bytes,
        index: int,
        count: int,
        password: str | None = None,
    ) -> None:
        super().__init__(doc, QCoreApplication.translate("Commands", "Insert pages"), index)
        self.data = bytes(data)
        # Undo removes the /AcroForm the inserted fields brought into a form-less document.
        self.had_form = doc.has_acroform
        self.count = int(count)
        self.password = password

    def _redo(self) -> None:
        self.page_ids = self.doc.insert_pages(
            self.data, self._position(), password=self.password, page_ids=self.page_ids
        )

    def _undo(self) -> None:
        if self.page_ids:
            self.doc.delete_pages(self.pages, drop_empty_form=not self.had_form)


# -- form fields ---------------------------------------------------------------
class SetFieldValueCommand(_ImmediateCommand):
    """Set one form field value (one undo step per commit, never merged).

    ``new_value``: a string for text/combo/list fields ("" clears), ``True``/``False``
    for a checkbox/radio widget (``True`` selects ``info``'s on state). ``font_size``
    (0 = auto-size) is applied with the value; undo restores the old value and, if it was
    changed, the old font size. The widget is re-resolved on every redo/undo (its page by
    id, then the widget by xref, then name and rect), so the command survives page moves
    and saves that renumber objects.
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
        self.page_id: PageId = doc.page_id(info.page)
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

    @property
    def page(self) -> int:
        """The widget's current page index."""
        return self._index(self.page_id, FieldError)

    def _apply(self, value: str | bool, font_size: float | None) -> None:
        self.doc.set_field_value(
            self.page,
            self.info.xref,
            value,
            font_size=font_size,
            name=self.info.name,
            unrotated_rect=self.info.unrotated_rect,
        )

    def _redo(self) -> None:
        self._apply(self.new_value, self.font_size)

    def _undo(self) -> None:
        restore = None
        if self.font_size is not None and self.font_size != self.old_font_size:
            restore = self.old_font_size
        self._apply(self.old_value, restore)


# -- annotations -----------------------------------------------------------------
def _claimed(doc: PdfDocument, info: AnnotInfo) -> AnnotInfo:
    """``info`` under its lasting /NM (a synthetic name is claimed: the /NM is written
    now, so undo/redo keep working across saves)."""
    if not is_synthetic(info.name):
        return info
    return replace(info, name=doc.claim_annot_name(info.page, info.name))


class _AnnotCommand(_ImmediateCommand):
    """An annotation command: the annotation's page is kept by id (:attr:`page_id`)."""

    page_id: PageId

    @property
    def page(self) -> int:
        """The annotation's current page index."""
        return self._index(self.page_id, AnnotError)


class AddAnnotCommand(_AnnotCommand):
    """Create a text box, stamp, signature or text markup from ``spec`` (one undo step).

    The /NM is fixed here (``spec.name``, or a new uuid4), so undo/redo cycles and saves
    keep the same identity. The first redo creates the annotation (a text box's height
    then hugs its text); later redos re-create the exact snapshot :attr:`info`.
    """

    def __init__(self, doc: PdfDocument, spec: AnnotSpec) -> None:
        super().__init__(doc, self._label(spec.kind))
        self.page_id = doc.page_id(spec.page)
        self.spec = replace(spec, name=spec.name or new_name())
        # Snapshot of the created annotation (None before the first redo).
        self.info: AnnotInfo | None = None

    @staticmethod
    def _label(kind: AnnotKind) -> str:
        if kind is AnnotKind.SIGNATURE:
            return QCoreApplication.translate("Commands", "Add signature")
        if kind is AnnotKind.STAMP:
            return QCoreApplication.translate("Commands", "Add stamp")
        if kind is AnnotKind.HIGHLIGHT:
            return QCoreApplication.translate("Commands", "Add highlight")
        if kind in (AnnotKind.UNDERLINE, AnnotKind.SQUIGGLY):
            return QCoreApplication.translate("Commands", "Add underline")
        if kind is AnnotKind.STRIKEOUT:
            return QCoreApplication.translate("Commands", "Add strike-through")
        return QCoreApplication.translate("Commands", "Add text")

    @property
    def name(self) -> str:
        return self.spec.name

    def _redo(self) -> None:
        page = self.page
        if self.info is None:
            fit = self.spec.kind is AnnotKind.TEXT
            self.info = self.doc.add_annot(replace(self.spec, page=page), fit_height=fit)
        else:
            # A signature keeps the spec's samples (shared with the document's image
            # object when it still exists, re-created after a full save dropped it).
            info = replace(self.info, page=page)
            self.info = self.doc.add_annot(spec_from(info, image=self.spec.image))

    def _undo(self) -> None:
        self.doc.delete_annot(self.page, self.spec.name)


class EditAnnotCommand(_AnnotCommand):
    """Change the text, style or rect (page space) of annotation ``info`` (one undo step).

    ``None`` keeps a property. ``fit_height`` (default: ``text is not None``) makes a
    text box's height hug its text after the change. Undo restores the text/colour that
    were changed and always the old rect and font size (a text change refits the height,
    a stamp resize rescales the glyph). The annotation is resolved by ``(page id, name)``
    on every redo/undo, so the command survives saves and page moves; :class:`AnnotError`
    if it is gone.

    A text markup (:data:`~pdfeditor.core.annotations.MARKUP_KINDS`) only changes its
    ``color`` and ``opacity`` ("Change markup color"); ``text``, ``font_size``, ``rect``
    or ``fit_height=True`` on a markup, and ``opacity`` on anything else, raise
    ``ValueError`` here, before anything reaches the document.
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
        opacity: float | None = None,
    ) -> None:
        changes = (text, font_size, color, rect, opacity)
        if all(change is None for change in changes):
            raise ValueError("EditAnnotCommand needs at least one change")
        if info.kind in MARKUP_KINDS:
            if text is not None or font_size is not None or rect is not None or fit_height:
                raise ValueError("a text markup only changes its colour and opacity")
        elif opacity is not None:
            raise ValueError("only a text markup has an opacity to change")
        super().__init__(doc, self._label(info, text, font_size, color, rect))
        self.page_id = doc.page_id(info.page)
        self.info = info
        self.new_text = text
        self.new_font_size = font_size
        self.new_color = color
        self.new_rect = QRectF(rect) if rect is not None else None
        self.new_opacity = opacity
        self.fit_height = (text is not None) if fit_height is None else fit_height

    @staticmethod
    def _label(
        info: AnnotInfo,
        text: str | None,
        font_size: float | None,
        color: Color | None,
        rect: QRectF | None,
    ) -> str:
        if info.kind in MARKUP_KINDS:
            return QCoreApplication.translate("Commands", "Change markup color")
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
    def name(self) -> str:
        return self.info.name

    def _redo(self) -> None:
        self.info = _claimed(self.doc, replace(self.info, page=self.page))
        if self.info.kind in MARKUP_KINDS:
            self.doc.update_annot(
                self.info.page, self.info.name, color=self.new_color, opacity=self.new_opacity
            )
            return
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
        if old.kind in MARKUP_KINDS:
            self.doc.update_annot(
                self.page,
                old.name,
                color=old.color if self.new_color is not None else None,
                opacity=old.opacity if self.new_opacity is not None else None,
            )
            return
        self.doc.update_annot(
            self.page,
            old.name,
            text=old.text if self.new_text is not None else None,
            font_size=old.font_size,
            color=old.color if self.new_color is not None else None,
            rect=QRectF(old.rect),
        )


class DeleteAnnotCommand(_AnnotCommand):
    """Delete annotation ``info``; undo re-creates it from the snapshot (same /NM, rect,
    rotation and style; appended at the end of the page's /Annots).

    A signature's image samples (:attr:`image`) are read from the document by the first
    redo, before deleting, so undo does not depend on the image object surviving a full
    save (or on the signature store).
    """

    def __init__(self, doc: PdfDocument, info: AnnotInfo) -> None:
        super().__init__(doc, QCoreApplication.translate("Commands", "Delete annotation"))
        self.page_id = doc.page_id(info.page)
        self.info = info
        # Signature samples (None until the first redo, and for other kinds).
        self.image: ImageData | None = None

    @property
    def name(self) -> str:
        return self.info.name

    def _redo(self) -> None:
        self.info = _claimed(self.doc, replace(self.info, page=self.page))
        image = self.image
        if self.info.kind is AnnotKind.SIGNATURE and image is None:
            image = self.doc.annot_image(self.info.page, self.info.name)
        self.doc.delete_annot(self.info.page, self.info.name)
        self.image = image

    def _undo(self) -> None:
        self.doc.add_annot(spec_from(replace(self.info, page=self.page), image=self.image))
