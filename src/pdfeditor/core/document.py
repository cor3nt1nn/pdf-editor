"""PdfDocument: the single owner of a ``pymupdf.Document``.

All PyMuPDF access must go through this class or happen under ``with doc.lock:``
because MuPDF is not thread-safe. Never hold the lock while calling Qt widgets.

The document is always opened from an in-memory copy of the file, so it never depends
on the file on disk after opening (another program may rewrite it; see
:meth:`PdfDocument.modified_on_disk`). Every save builds the complete new file in
memory, writes it atomically (temp file + ``os.replace``) and reloads the document from
those same bytes.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pymupdf
from PySide6.QtCore import QObject, QRectF, QSizeF, Signal
from PySide6.QtGui import QImage

from pdfeditor.core import annotations, orphans, signature, snapping
from pdfeditor.core import pages as page_ops
from pdfeditor.core.annotations import AnnotInfo, AnnotKind, AnnotSpec
from pdfeditor.core.files import same_file
from pdfeditor.core.forms import (
    FieldKind,
    WidgetInfo,
    XfaKind,
    detect_xfa,
    field_button_state,
    read_widgets,
    resolve_widget,
    set_button_state,
    set_text_value,
    strip_xfa,
    widget_kind,
)
from pdfeditor.core.geometry import fitz_from_qrect
from pdfeditor.core.signature import ImageData
from pdfeditor.core.snapping import PageShapes
from pdfeditor.core.snapshots import SnapshotStore

log = logging.getLogger(__name__)

#: (st_size, st_mtime_ns) of the file on disk, used to detect external modification.
DiskStamp = tuple[int, int]

#: Called with the attempt number (0 = first prompt, >0 = previous password was wrong).
#: Returns the password, or ``None`` to cancel.
PasswordCallback = Callable[[int], str | None]


class DocumentError(Exception):
    """Base class for document errors."""


class OpenError(DocumentError):
    """The file could not be opened as a PDF.

    ``reason`` is one of "missing", "unreadable", "empty", "corrupt", "no_pages", "password".
    """

    def __init__(self, message: str, reason: str = "corrupt") -> None:
        super().__init__(message)
        self.reason = reason


class PasswordRequired(OpenError):  # noqa: N818 - public API name from the design
    """The PDF is encrypted and no (correct) password was supplied."""

    def __init__(self, message: str = "password required", wrong_password: bool = False):
        super().__init__(message, reason="password")
        self.wrong_password = wrong_password


class SaveError(DocumentError):
    """The document could not be saved."""


class FieldError(DocumentError):
    """A form field could not be found (anymore) or cannot hold the given value."""


class AnnotError(DocumentError):
    """An annotation could not be found (anymore), created or changed."""


class PageError(DocumentError):
    """A page operation was refused (permissions, dynamic XFA, last page, unknown page)
    or failed. ``reason`` is one of "permission", "xfa", "last_page", "missing",
    "snapshot", "failed"."""

    def __init__(self, message: str, reason: str = "failed") -> None:
        super().__init__(message)
        self.reason = reason


#: In-memory identity of a page, stable across moves, insertions, deletions of other
#: pages, saves and undo (docs/M6_PLAN.md D4). Never written to the file.
PageId = int

_page_ids = itertools.count(1)


def new_page_ids(n: int) -> list[PageId]:
    """``n`` fresh page ids (unique for the life of the process)."""
    return [next(_page_ids) for _ in range(n)]


@dataclass(frozen=True)
class ExportOptions:
    """What :meth:`PdfDocument.export_copy` does to the copy.

    ``flatten_forms``: draw the form fields into the page content and remove the form
    (``Document.bake(widgets=True)``). ``flatten_annots``: same for the other
    annotations (text boxes, stamps, signatures, highlights; links are kept, hidden
    annotations disappear). Both off = a clean copy (full rewrite, no earlier revisions).
    ``keep_encryption``: keep the password protection (ignored, i.e. always kept, when
    the author restricted the file: see :attr:`PdfDocument.must_keep_encryption`).
    ``keep_metadata``: keep the document properties (Info dictionary and XMP metadata);
    off also drops the pages' XMP (/Metadata) and /PieceInfo of the pages and catalog.
    The trailer /ID and MuPDF's "% Written by MuPDF" header comment remain
    (docs/ARCHITECTURE.md Deviation 57).
    """

    flatten_forms: bool = True
    flatten_annots: bool = True
    keep_encryption: bool = True
    keep_metadata: bool = True


#: ``authenticate()`` bit: the owner password was given.
AUTH_OWNER = 4
#: Every permission bit of the standard security handler: a file whose permissions lack
#: one of them was restricted by its author.
FULL_PERMISSIONS = (
    pymupdf.PDF_PERM_PRINT
    | pymupdf.PDF_PERM_MODIFY
    | pymupdf.PDF_PERM_COPY
    | pymupdf.PDF_PERM_ANNOTATE
    | pymupdf.PDF_PERM_FORM
    | pymupdf.PDF_PERM_ACCESSIBILITY
    | pymupdf.PDF_PERM_ASSEMBLE
    | pymupdf.PDF_PERM_PRINT_HQ
)


class PdfDocument(QObject):
    """Qt-facing wrapper around a PyMuPDF document."""

    #: A page's content or size changed (rotation, field value, annotation...). Views
    #: compare ``page_size(i)`` with the size they laid out to tell a geometry change
    #: (relayout) from a content-only change (re-render in place, no scroll jump).
    page_changed = Signal(int)
    #: Pages were added, removed or reordered. Emitted right after ``pages_remapped``.
    structure_changed = Signal()
    #: Emitted by every structural change just before ``structure_changed`` with
    #: ``old_to_new: list[int | None]``: for each former page index its new index, or
    #: None if the page is gone. ``page_count`` and the page ids are already updated.
    pages_remapped = Signal(object)
    #: The document now lives at another path (Save As). Content is unchanged.
    path_changed = Signal(str)
    #: The underlying ``pymupdf.Document`` was replaced by an identical one reloaded after
    #: a save. Content, page count and sizes are unchanged; never keep pymupdf objects
    #: (pages, widgets, annots) across calls, re-fetch them from ``fitz`` instead.
    reloaded = Signal()

    def __init__(
        self,
        fitz_doc: pymupdf.Document,
        path: str | None = None,
        password: str | None = None,
        encrypted: bool = False,
        parent: QObject | None = None,
        disk_stamp: DiskStamp | None = None,
        owner_access: bool = False,
    ) -> None:
        super().__init__(parent)
        self.lock = threading.RLock()
        self._doc: pymupdf.Document | None = fitz_doc
        self._path = str(path) if path else None
        self._password = password
        self._encrypted = encrypted
        # Authenticated with the owner password (full access to an encrypted file).
        self._owner_access = owner_access
        # Stamp of the file on disk when its content last matched what we loaded/saved;
        # None = unknown (not loaded from disk).
        self._disk_stamp = disk_stamp
        self._needs_full_save = False
        # Objects numbered from here on were created since the load (see core/orphans.py).
        self._first_new_xref = int(fitz_doc.xref_length())
        self._page_count = int(fitz_doc.page_count)
        self._size_cache: dict[int, QSizeF] = {}
        self._widget_cache: dict[int, list[WidgetInfo]] = {}
        self._annot_cache: dict[int, list[AnnotInfo]] = {}
        # Signature image objects of the current load: content digest -> image xref.
        # Dropped with the annotation cache (reloads, full writes renumber xrefs).
        self._image_xrefs: dict[str, int] = {}
        # Snapping shapes by page (read without the lock, written under it).
        self._shapes_cache: dict[int, PageShapes] = {}
        # Scope of synthetic annotation names: bumped whenever xrefs may change.
        self._load_generation = 0
        self._is_form = False
        self._can_fill_forms = False
        self._can_annotate = False
        self._xfa_kind = XfaKind.NONE
        self._form_edited = False
        self._can_assemble = False
        self._can_extract = False
        self._structure_edited = False
        self._page_ids: list[PageId] = new_page_ids(self._page_count)
        self._page_index: dict[PageId, int] = {}
        self._reindex()
        #: Whole-document undo copies of page deletions (see core/snapshots.py).
        self.snapshots = SnapshotStore()
        # Fields inserted by the last insert_pages() collided with existing names.
        self._last_insert_renamed = False
        self._read_form_state()
        # Connected first so that other slots see the refreshed caches.
        self.page_changed.connect(self._on_page_changed)
        self.structure_changed.connect(self._on_structure_changed)
        self.path_changed.connect(self._clear_widget_cache)
        self.path_changed.connect(self._clear_annot_cache)
        self.reloaded.connect(self._on_reloaded)

    # -- opening -----------------------------------------------------------
    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        password_cb: PasswordCallback | None = None,
        password: str | None = None,
    ) -> PdfDocument:
        """Open ``path``.

        ``needs_pass`` is read only *before* authenticating: reading
        ``pymupdf.Document.needs_pass`` (or authenticating with a wrong password) after a
        successful ``authenticate()`` corrupts decryption for the life of that document
        (renders fail with "aes padding out of range"). Use :attr:`is_encrypted`.

        Raises :class:`OpenError` (missing/empty/corrupt file) or :class:`PasswordRequired`
        (encrypted and the password was not supplied, was wrong, or the prompt was cancelled).
        ``password_cb`` is called repeatedly until it returns the right password or ``None``.
        """
        path = str(path)
        fitz_doc, stamp = open_fitz(path)
        encrypted = bool(fitz_doc.needs_pass)
        used_password: str | None = None
        owner_access = False
        if encrypted:
            used_password, owner_access = authenticate(fitz_doc, password_cb, password)
        if fitz_doc.page_count == 0:
            fitz_doc.close()
            raise OpenError(f"document has no pages: {path}", reason="no_pages")
        return cls(
            fitz_doc, path, used_password, encrypted, disk_stamp=stamp, owner_access=owner_access
        )

    # -- properties --------------------------------------------------------
    @property
    def fitz(self) -> pymupdf.Document:
        """The underlying document. Use only under ``self.lock``.

        Never read ``fitz.needs_pass`` (nor call ``authenticate()`` again) on an
        authenticated document: it breaks decryption for the document's life. Use
        :attr:`is_encrypted`, :attr:`permissions`.
        """
        if self._doc is None:
            raise DocumentError("document is closed")
        return self._doc

    @property
    def is_open(self) -> bool:
        return self._doc is not None

    @property
    def path(self) -> str | None:
        return self._path

    @property
    def password(self) -> str | None:
        return self._password

    @property
    def page_count(self) -> int:
        """Number of pages (cached: never waits for a render holding the lock)."""
        return self._page_count

    @property
    def is_encrypted(self) -> bool:
        return self._encrypted

    @property
    def permissions(self) -> int:
        with self.lock:
            return int(self.fitz.permissions)

    @property
    def encryption_method(self) -> str | None:
        """The encryption of the file (e.g. "AES-256 (R6)"), or None if it is not
        encrypted. Set for owner-password-only files too (unlike :attr:`is_encrypted`)."""
        with self.lock:
            method = self.fitz.metadata.get("encryption")
        return str(method) if method else None

    @property
    def has_restrictions(self) -> bool:
        """Encrypted with an owner password only (opens without a password, permissions
        set by the author): exports always keep that protection."""
        return self.encryption_method is not None and not self._encrypted

    @property
    def has_owner_access(self) -> bool:
        """Opened with the owner password (an encrypted file only)."""
        return self._owner_access

    @property
    def must_keep_encryption(self) -> bool:
        """Exports keep the password protection whatever ``keep_encryption`` says.

        True for owner-password restrictions (:attr:`has_restrictions`) and for a file
        opened with its user password whose author restricted it (``permissions`` lack
        one of :data:`FULL_PERMISSIONS`). Only a file with a user password and full
        permissions, or one opened with the owner password, may be exported unprotected
        (docs/ARCHITECTURE.md Deviation 58).
        """
        if self.has_restrictions:
            return True
        if not self._encrypted or self._owner_access:
            return False
        return self.permissions & FULL_PERMISSIONS != FULL_PERMISSIONS

    @property
    def is_form(self) -> bool:
        """The document has AcroForm fields (computed on open and after each reload)."""
        return self._is_form

    @property
    def can_fill_forms(self) -> bool:
        """The permissions allow filling form fields (FORM or ANNOTATE).

        Based on ``permissions``, never on ``is_encrypted``: owner-password-only files open
        without a password but may forbid filling.
        """
        return self._can_fill_forms

    @property
    def can_annotate(self) -> bool:
        """The permissions allow adding and changing annotations (text, stamps).

        ``permissions & PDF_PERM_ANNOTATE`` (computed on open and after each reload).
        """
        return self._can_annotate

    @property
    def xfa_kind(self) -> XfaKind:
        """XFA flavour of the form (computed on open, after each reload and by
        :meth:`strip_xfa`)."""
        return self._xfa_kind

    def strip_xfa(self) -> bool:
        """Remove the /XFA entry so viewers use the AcroForm fields; True if one was removed.

        Done at save time when a static XFA form was edited (the XFA datasets would
        otherwise keep the old values in XFA-aware viewers). Not undoable.
        """
        with self.lock:
            removed = strip_xfa(self.fitz) is not None
            self._xfa_kind = detect_xfa(self.fitz)
        if removed:
            log.info("removed XFA from %s", self._path)
        return removed

    def _read_form_state(self) -> None:
        with self.lock:
            doc = self._doc
            if doc is None:
                self._is_form = self._can_fill_forms = self._can_annotate = False
                self._can_assemble = self._can_extract = False
                self._xfa_kind = XfaKind.NONE
                return
            self._is_form = bool(doc.is_form_pdf)
            try:
                self._xfa_kind = detect_xfa(doc)
            except Exception:  # malformed AcroForm: treat as plain PDF
                log.warning("could not inspect XFA", exc_info=True)
                self._xfa_kind = XfaKind.NONE
            perms = int(doc.permissions)
        self._can_fill_forms = bool(perms & (pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ANNOTATE))
        self._can_annotate = bool(perms & pymupdf.PDF_PERM_ANNOTATE)
        self._can_assemble = bool(perms & (pymupdf.PDF_PERM_ASSEMBLE | pymupdf.PDF_PERM_MODIFY))
        self._can_extract = bool(perms & pymupdf.PDF_PERM_COPY)

    @property
    def was_repaired(self) -> bool:
        with self.lock:
            return bool(self.fitz.is_repaired)

    # -- pages -------------------------------------------------------------
    def _check_index(self, i: int) -> None:
        if not 0 <= i < self._page_count:
            raise IndexError(f"page index out of range: {i}")

    def page_size(self, i: int) -> QSizeF:
        """Page size in points, rotation applied (``page.rect``)."""
        cached = self._size_cache.get(i)
        if cached is not None:
            return QSizeF(cached)
        self._check_index(i)
        with self.lock:
            r = self.fitz[i].rect
        size = QSizeF(r.width, r.height)
        self._size_cache[i] = size
        return QSizeF(size)

    def page_rotation(self, i: int) -> int:
        self._check_index(i)
        with self.lock:
            return int(self.fitz[i].rotation)

    def set_page_rotation(self, i: int, degrees: int) -> None:
        """Set the absolute rotation of page ``i`` (multiple of 90). Emits page_changed.

        Raises :class:`PageError` ("permission") without :attr:`can_assemble`.
        """
        self._check_index(i)
        if not self._can_assemble:
            raise PageError("page operations are not permitted by this document", "permission")
        if int(degrees) % 90:
            raise ValueError(f"rotation must be a multiple of 90: {degrees}")
        degrees = int(degrees) % 360
        with self.lock:
            page = self.fitz[i]
            if page.rotation == degrees:
                return
            page.set_rotation(degrees)
        self.page_changed.emit(i)

    # -- page identity and structure (M6a) -----------------------------------
    def page_id(self, i: int) -> PageId:
        """The stable id of the page now at index ``i``."""
        self._check_index(i)
        return self._page_ids[i]

    def page_index(self, page_id: PageId) -> int | None:
        """The current index of page ``page_id``, or None if it is not in the document."""
        return self._page_index.get(page_id)

    def page_ids(self) -> list[PageId]:
        """The ids of every page, in page order."""
        return list(self._page_ids)

    def _reindex(self) -> None:
        self._page_index = {pid: i for i, pid in enumerate(self._page_ids)}

    @property
    def can_assemble(self) -> bool:
        """The permissions allow inserting, deleting, moving and rotating pages
        (``PDF_PERM_ASSEMBLE`` or ``PDF_PERM_MODIFY``)."""
        return self._can_assemble

    @property
    def can_extract(self) -> bool:
        """The permissions allow copying pages into new files (``PDF_PERM_COPY``)."""
        return self._can_extract

    @property
    def structure_edited(self) -> bool:
        """Pages were inserted, deleted or moved since opening (not reset by saves; a
        static XFA form then loses its /XFA at the next save)."""
        return self._structure_edited

    @property
    def last_insert_renamed_fields(self) -> bool:
        """The last :meth:`insert_pages` brought fields whose names the document already
        had (MuPDF renamed the inserted ones "name [xref]")."""
        return self._last_insert_renamed

    def _check_assemble(self) -> None:
        if self._doc is None:
            raise PageError("document is closed")
        if not self._can_assemble:
            raise PageError("page operations are not permitted by this document", "permission")
        if self._xfa_kind is XfaKind.DYNAMIC:
            raise PageError("page operations are disabled on dynamic XFA forms", "xfa")

    def snapshot(self) -> bytes:
        """An in-memory write of the whole document as it is now (with its encryption),
        for undo and for copying pages out. The next save is a full one (an incremental
        write after an in-memory write produces a corrupt file, docs/M5_PLAN.md C1)."""
        with self.lock:
            try:
                return self.fitz.tobytes(
                    garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_KEEP
                )
            finally:
                self._needs_full_save = True

    def _structure_done(self, ids: list[PageId], old_to_new: list[int | None]) -> None:
        """Common tail of every structural change (lock released)."""
        self._page_ids = list(ids)
        self._reindex()
        self._needs_full_save = True
        self._structure_edited = True
        self._drop_page_caches()
        self._read_form_state()
        self.pages_remapped.emit(list(old_to_new))
        self.structure_changed.emit()

    def _drop_page_caches(self) -> None:
        with self.lock:
            self._page_count = int(self._doc.page_count) if self._doc is not None else 0
        self._size_cache.clear()
        self._widget_cache.clear()
        self._clear_annot_cache()
        self._clear_shapes_cache()

    def _resolve_ids(self, page_ids: list[PageId]) -> list[int]:
        out = []
        for pid in page_ids:
            index = self._page_index.get(pid)
            if index is None:
                raise PageError(f"page {pid} is not in the document", "missing")
            out.append(index)
        return out

    @property
    def has_acroform(self) -> bool:
        """The catalog has an /AcroForm dictionary (possibly without fields)."""
        with self.lock:
            return self._doc is not None and page_ops.has_acroform(self._doc)

    def delete_pages(self, indexes: list[int], *, drop_empty_form: bool = False) -> None:
        """Delete the pages at ``indexes`` (form fields pruned, links to them dropped,
        outline items to them greyed). ``drop_empty_form`` then also removes an /AcroForm
        left without fields (undo of an insertion into a document without a form).
        Raises :class:`PageError` (permissions, dynamic XFA, every page, failure)."""
        self._check_assemble()
        targets = sorted(set(int(i) for i in indexes))
        if not targets:
            return
        for i in targets:
            self._check_index(i)
        if self._page_count - len(targets) < page_ops.MIN_PAGES:
            raise PageError("a document must keep at least one page", "last_page")
        with self.lock:
            try:
                page_ops.delete_pages(self.fitz, targets)
                if drop_empty_form:
                    page_ops.drop_empty_acroform(self.fitz)
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise PageError(str(exc)) from exc
        gone = set(targets)
        old_to_new: list[int | None] = []
        kept: list[PageId] = []
        for i, pid in enumerate(self._page_ids):
            if i in gone:
                old_to_new.append(None)
            else:
                old_to_new.append(len(kept))
                kept.append(pid)
        log.info("deleted pages %s", [i + 1 for i in targets])
        self._structure_done(kept, old_to_new)

    def reorder_pages(self, new_order: list[PageId]) -> None:
        """Rearrange the pages into ``new_order`` (every page id once). Raises
        :class:`PageError`."""
        self._check_assemble()
        if sorted(new_order) != sorted(self._page_ids):
            raise PageError("the new order must list every page once", "missing")
        order = [self._page_index[pid] for pid in new_order]
        if order == list(range(self._page_count)):
            return
        with self.lock:
            try:
                page_ops.reorder(self.fitz, order)
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise PageError(str(exc)) from exc
        old_to_new: list[int | None] = [None] * len(order)
        for new, old in enumerate(order):
            old_to_new[old] = new
        self._structure_done(list(new_order), old_to_new)

    def insert_blank_page(
        self, index: int, size: QSizeF, *, page_id: PageId | None = None
    ) -> PageId:
        """Insert an empty page of ``size`` points at ``index`` (0..page_count); returns
        its id (``page_id`` when given: redo reinstalls the same id). Raises
        :class:`PageError`."""
        self._check_assemble()
        if not 0 <= index <= self._page_count:
            raise IndexError(f"insert position out of range: {index}")
        pid = page_id if page_id is not None else new_page_ids(1)[0]
        if pid in self._page_index:
            raise PageError(f"page {pid} is already in the document")
        with self.lock:
            try:
                page_ops.insert_blank(self.fitz, index, size.width(), size.height())
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise PageError(str(exc)) from exc
        ids = self._page_ids[:index] + [pid] + self._page_ids[index:]
        self._structure_done(ids, _shifted(len(self._page_ids), index, 1))
        return pid

    def insert_pages(
        self,
        data: bytes,
        index: int,
        *,
        password: str | None = None,
        page_ids: list[PageId] | None = None,
    ) -> list[PageId]:
        """Insert every page of the PDF ``data`` (a sub-document, see
        :func:`pages.subdocument_bytes`; decrypted with ``password`` if needed) at
        ``index``; returns their ids (``page_ids`` when given: redo reinstalls the same
        ids). Sets :attr:`last_insert_renamed_fields`. Raises :class:`PageError`."""
        self._check_assemble()
        if not 0 <= index <= self._page_count:
            raise IndexError(f"insert position out of range: {index}")
        try:
            src = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise PageError(f"the pages to insert cannot be read: {exc}") from exc
        try:
            # needs_pass is read only before authenticate() (see open()).
            if src.needs_pass and not src.authenticate(password or ""):
                raise PageError("the pages to insert are encrypted", "failed")
            count = int(src.page_count)
            ids = list(page_ids) if page_ids is not None else new_page_ids(count)
            if len(ids) != count or any(pid in self._page_index for pid in ids):
                raise PageError("page ids do not match the pages to insert")
            with self.lock:
                try:
                    doc = self.fitz
                    collide = bool(
                        doc.is_form_pdf and page_ops.field_names(src) & page_ops.field_names(doc)
                    )
                    page_ops.insert_pages(doc, src, index, had_xfa=page_ops.has_xfa(doc))
                except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                    raise PageError(str(exc)) from exc
        finally:
            src.close()
        self._last_insert_renamed = collide
        new_ids = self._page_ids[:index] + ids + self._page_ids[index:]
        log.info("inserted %d pages at %d", count, index + 1)
        self._structure_done(new_ids, _shifted(len(self._page_ids), index, count))
        return ids

    def restore_snapshot(self, data: bytes, page_ids: list[PageId]) -> None:
        """Replace the document by the :meth:`snapshot` ``data`` whose pages have the ids
        ``page_ids`` (undo of a deletion). Emits ``pages_remapped`` and
        ``structure_changed``. Raises :class:`PageError` (the document is unchanged)."""
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise PageError(f"the undo copy cannot be read: {exc}", "snapshot") from exc
        # Read needs_pass only before authenticate() (see open()).
        locked = bool(doc.needs_pass)
        if locked and self._password is not None:
            locked = not doc.authenticate(self._password)
        if locked or doc.page_count != len(page_ids):
            doc.close()
            raise PageError("the undo copy does not match the document", "snapshot")
        with self.lock:
            old, self._doc = self._doc, doc
            self._first_new_xref = int(doc.xref_length())
            self._load_generation += 1
            if old is not None:
                old.close()
        index = {pid: i for i, pid in enumerate(page_ids)}
        old_to_new = [index.get(pid) for pid in self._page_ids]
        self._structure_done(list(page_ids), old_to_new)

    # -- copying pages out ---------------------------------------------------
    def _output_encryption(self) -> page_ops.OutputEncryption:
        return page_ops.output_encryption(
            is_encrypted=self._encrypted,
            has_restrictions=self.has_restrictions,
            password=self._password,
            permissions=self.permissions,
        )

    def _check_extract(self, paths: list[str]) -> None:
        if not self._can_extract:
            raise PageError("copying pages is not permitted by this document", "permission")
        for path in paths:
            if self._path is not None and same_file(path, self._path):
                raise ValueError("the open document cannot be replaced by extracted pages")

    def _copy_bytes(self, data: bytes, indexes: list[int]) -> bytes:
        """A new file of ``indexes`` from a fresh copy opened from ``data`` (the copy is
        mutated by grafting, so each output gets its own)."""
        copy = pymupdf.open(stream=data, filetype="pdf")
        try:
            if copy.needs_pass and not copy.authenticate(self._password or ""):
                raise SaveError("the copy could not be decrypted")
            return page_ops.extract_bytes(copy, indexes, encryption=self._output_encryption())
        finally:
            copy.close()

    def extract_pages(self, indexes: list[int], path: str | os.PathLike[str]) -> None:
        """Write the pages ``indexes`` (in that order) to a new file ``path``, protected
        as docs/M6_PLAN.md D5 says. The open document is not read directly (only an
        in-memory copy), so it is unchanged; its next save is a full one. Raises
        :class:`PageError` (permissions), ``ValueError`` (``path`` is the open document,
        no pages) or :class:`SaveError`."""
        path = str(path)
        self._check_extract([path])
        for i in indexes:
            self._check_index(i)
        if not indexes:
            raise ValueError("no pages to extract")
        data = self.snapshot()
        try:
            out = self._copy_bytes(data, list(indexes))
        except SaveError:
            raise
        except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
            raise SaveError(str(exc)) from exc
        try:
            _write_atomically(path, out)
        except OSError as exc:
            raise SaveError(str(exc)) from exc
        log.info("extracted %d pages to %s", len(indexes), path)

    def split_document(self, groups: list[list[int]], paths: list[str | os.PathLike[str]]) -> None:
        """Write each page group to the matching path (see :meth:`extract_pages`).
        Raises :class:`PageError`, ``ValueError`` or :class:`SaveError` (files written
        before a failure are kept)."""
        targets = [str(p) for p in paths]
        if len(targets) != len(groups) or not groups:
            raise ValueError("one path per group is needed")
        self._check_extract(targets)
        for group in groups:
            if not group:
                raise ValueError("empty page group")
            for i in group:
                self._check_index(i)
        data = self.snapshot()
        for group, path in zip(groups, targets, strict=True):
            try:
                out = self._copy_bytes(data, list(group))
            except SaveError:
                raise
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise SaveError(str(exc)) from exc
            try:
                _write_atomically(path, out)
            except OSError as exc:
                raise SaveError(str(exc)) from exc
        log.info("split %s into %d files", self._path, len(groups))

    # -- form widgets ------------------------------------------------------
    def widgets(self, i: int) -> list[WidgetInfo]:
        """All widgets of page ``i`` (in /Annots order), cached until the page changes.

        Snapshots only: the cache is dropped on ``page_changed(i)``, ``structure_changed``,
        ``path_changed`` and ``reloaded`` (full saves may renumber xrefs).
        """
        cached = self._widget_cache.get(i)
        if cached is None:
            self._check_index(i)
            with self.lock:
                cached = read_widgets(self.fitz, i)
            self._widget_cache[i] = cached
        return list(cached)

    def all_widgets(self) -> list[WidgetInfo]:
        """Widgets of every page, page by page."""
        return [w for i in range(self._page_count) for w in self.widgets(i)]

    def widget(self, page: int, xref: int) -> WidgetInfo | None:
        """The widget with annotation ``xref`` on ``page``, or None."""
        return next((w for w in self.widgets(page) if w.xref == xref), None)

    def widget_rects(self, i: int) -> list[tuple[str, QRectF]]:
        """(field name, rect in page space) for each form widget of page ``i``."""
        return [(w.name, QRectF(w.rect)) for w in self.widgets(i)]

    @property
    def form_edited(self) -> bool:
        """A form field value was set (by :meth:`set_field_value`) since opening.

        Not reset by saves (only by :meth:`close`): it means "this session filled the
        form". The only save-time consumer, stripping static XFA, is a no-op once the XFA
        is gone (``xfa_kind`` is then ``NONE``).
        """
        return self._form_edited

    def set_field_value(
        self,
        page: int,
        xref: int,
        value: str | bool,
        *,
        font_size: float | None = None,
        name: str = "",
        unrotated_rect: tuple[float, float, float, float] | None = None,
    ) -> list[int]:
        """Set the value of the field shown by widget ``xref`` on ``page``.

        Text/combo/list: ``value`` is the string to store ("" clears the field);
        ``font_size`` (0 = auto-size) rewrites the widgets' font size, ``None`` keeps it.
        Checkbox/radio: ``True`` selects this widget's on state, ``False`` or ``"Off"``
        turns the field off, another string selects the widget with that (decoded) on
        state. The widget is re-resolved by ``name``/``unrotated_rect`` if its xref changed
        (full saves renumber objects). Callers that keep a snapshot across saves (undo
        commands, tools) **must** pass ``name`` and ``unrotated_rect``; without ``name``
        ``xref`` is looked up in the current :meth:`widgets` snapshot, so it must come
        from a snapshot taken after the last save.

        Emits ``page_changed`` for every page showing a widget of the field and returns
        those pages. Raises :class:`FieldError` if the widget is gone or not fillable.
        """
        self._check_index(page)
        if not name:
            info = self.widget(page, xref)
            if info is None:
                raise FieldError(f"no widget xref {xref} on page {page}")
            name, unrotated_rect = info.name, info.unrotated_rect
        with self.lock:
            found = resolve_widget(self.fitz, page, xref, name, unrotated_rect)
            if found is None:
                raise FieldError(f"form field {name!r} not found on page {page}")
            fitz_page, widget = found
            kind = widget_kind(widget)
            try:
                if kind in (FieldKind.CHECKBOX, FieldKind.RADIO):
                    pages = set_button_state(self.fitz, fitz_page, widget, value)
                elif kind in (FieldKind.TEXT, FieldKind.COMBO, FieldKind.LIST):
                    if not isinstance(value, str):
                        raise FieldError(f"form field {name!r} ({kind}) needs a string")
                    pages = set_text_value(self.fitz, fitz_page, widget, value, font_size)
                else:
                    raise FieldError(f"form field {name!r} ({kind}) cannot be filled")
            except FieldError:
                raise
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise FieldError(str(exc)) from exc
        self._form_edited = True
        for i in pages:
            self.page_changed.emit(i)
        return pages

    def field_button_state(self, info: WidgetInfo) -> str:
        """Decoded on state of ``info``'s checkbox/radio field over all its pages.

        ``"Off"`` when no widget of the field is on (radio siblings may be on other pages
        than ``info.page``). Raises :class:`FieldError` if the widget is gone.
        """
        self._check_index(info.page)
        with self.lock:
            found = resolve_widget(self.fitz, info.page, info.xref, info.name, info.unrotated_rect)
            if found is None:
                raise FieldError(f"form field {info.name!r} not found on page {info.page}")
            fitz_page, widget = found
            return field_button_state(self.fitz, fitz_page, widget)

    def _clear_widget_cache(self, *_args: object) -> None:
        self._widget_cache.clear()

    # -- annotations (FreeText text boxes and stamps, signatures) -----------
    def annots(self, i: int) -> list[AnnotInfo]:
        """Visible FreeText annotations and signatures of page ``i`` (in /Annots order),
        cached like :meth:`widgets` (same drop rules).

        Identity is ``(page, name)``. Reading never modifies the document: an annotation
        without a unique /NM has a synthetic name (``annotations.is_synthetic``) valid
        until the next reload (save) or close; commands turn it into a real /NM with
        :meth:`claim_annot_name` when they first change the annotation.
        """
        cached = self._annot_cache.get(i)
        if cached is None:
            self._check_index(i)
            with self.lock:
                cached = annotations.read_annots(self.fitz, i, scope=self._annot_scope())
            self._annot_cache[i] = cached
        return list(cached)

    def annot(self, page: int, name: str) -> AnnotInfo | None:
        """The visible annotation ``name`` on ``page``, or None.

        A synthetic name of the current load also finds the annotation after it was
        given a real /NM (the snapshot then carries the real name).
        """
        listed = self.annots(page)
        found = next((a for a in listed if a.name == name), None)
        if found is None:
            xref = self._synthetic_xref(name)
            if xref is not None:
                found = next((a for a in listed if a.xref == xref), None)
        return found

    def _annot_scope(self) -> str:
        return str(self._load_generation)

    def _synthetic_xref(self, name: str) -> int | None:
        """Xref of a synthetic name of the current load, else None."""
        parts = annotations.synthetic_parts(name)
        if parts is None or parts[0] != self._annot_scope():
            return None
        return parts[1]

    def _check_name(self, page: int, name: str) -> None:
        if annotations.is_synthetic(name) and self._synthetic_xref(name) is None:
            raise AnnotError(f"annotation {name!r} not found on page {page} (stale name)")

    def claim_annot_name(self, page: int, name: str) -> str:
        """The lasting /NM of annotation ``name`` on ``page``: ``name`` itself, or, for a
        synthetic name, a uuid4 written to the annotation now (no signal: the caller's
        change that follows emits ``page_changed``). Raises :class:`AnnotError`."""
        if not annotations.is_synthetic(name):
            return name
        self._check_index(page)
        self._check_annotate()
        self._check_name(page, name)
        with self.lock:
            try:
                real = annotations.claim_name(self.fitz, page, name)
            except LookupError as exc:
                raise AnnotError(f"annotation {name!r} not found on page {page}") from exc
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise AnnotError(str(exc)) from exc
        self._annot_cache.pop(page, None)
        return real

    def _check_annotate(self) -> None:
        if not self._can_annotate:
            raise AnnotError("annotations are not permitted by this document")

    def add_annot(self, spec: AnnotSpec, *, fit_height: bool = False) -> AnnotInfo:
        """Create a FreeText annotation or signature (``spec.name`` "" = new uuid4).
        Emits page_changed.

        ``fit_height``: a text box's height then hugs its wrapped text; otherwise
        ``spec.rect`` is used as is. A signature (``spec.image`` required, already turned
        for the page) draws the document's image object with the same samples when there
        is one, else a new one. Raises :class:`AnnotError`.
        """
        self._check_index(spec.page)
        self._check_annotate()
        image = spec.image
        if spec.kind is AnnotKind.SIGNATURE and image is None:
            raise AnnotError("a signature needs an image")
        with self.lock:
            try:
                if image is not None and spec.kind is AnnotKind.SIGNATURE:
                    xref = self._image_xref(image)
                    info = signature.create_signature_annot(self.fitz, spec.page, spec, xref)
                else:
                    info = annotations.create_annot(
                        self.fitz, spec.page, spec, fit_height=fit_height
                    )
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise AnnotError(str(exc)) from exc
        self.page_changed.emit(spec.page)
        return info

    def _image_xref(self, data: ImageData) -> int:
        """Xref of an image object holding ``data`` (shared: cached, found by a scan of
        the signatures, or created). Under the lock."""
        key = signature.digest(data)
        xref = self._image_xrefs.get(key)
        if xref is None:
            xref = self._find_image(data)
        if xref is None:
            xref = signature.add_image_xobject(self.fitz, data)
            log.debug("added signature image xref %d (%dx%d)", xref, data.width, data.height)
        self._image_xrefs[key] = xref
        return xref

    def _find_image(self, data: ImageData) -> int | None:
        """Image xref of a signature of the document showing ``data``'s samples, or
        None. Records every image hashed on the way. Under the lock."""
        key = signature.digest(data)
        known = set(self._image_xrefs.values())
        for i in range(self._page_count):
            listed = annotations.read_annots(
                self.fitz, i, include_hidden=True, scope=self._annot_scope()
            )
            for info in listed:
                xref = info.image_xref
                if not xref or xref in known or info.image_size != (data.width, data.height):
                    continue
                known.add(xref)
                try:
                    found = signature.digest(signature.read_image(self.fitz, xref))
                except ValueError:
                    continue
                self._image_xrefs.setdefault(found, xref)
                if found == key:
                    return xref
        return None

    def annot_image(self, page: int, name: str) -> ImageData:
        """The image samples of signature ``name`` on ``page`` (as embedded: turned for
        the page), for undo snapshots. Raises :class:`AnnotError` if it is gone, not a
        signature, or its image cannot be read back."""
        self._check_index(page)
        self._check_name(page, name)
        with self.lock:
            try:
                found = annotations.resolve_annot(self.fitz, page, name)
                if found is None:
                    raise AnnotError(f"annotation {name!r} not found on page {page}")
                fitz_page, annot = found
                xref = signature.signature_image_xref(self.fitz, fitz_page, annot)
                if not xref:
                    raise AnnotError(f"annotation {name!r} is not a signature")
                return signature.read_image(self.fitz, xref)
            except AnnotError:
                raise
            except Exception as exc:  # ValueError, MuPDF FzError*
                raise AnnotError(str(exc)) from exc

    def update_annot(
        self,
        page: int,
        name: str,
        *,
        text: str | None = None,
        font_size: float | None = None,
        color: tuple[float, float, float] | None = None,
        rect: QRectF | None = None,
        fit_height: bool = False,
    ) -> AnnotInfo:
        """Change annotation ``name`` on ``page`` (see :func:`annotations.update_annot`;
        ``rect`` in page space) and return its new snapshot. Emits page_changed.

        Raises :class:`AnnotError` if it is gone or cannot be changed (locked).
        """
        self._check_index(page)
        self._check_annotate()
        self._check_name(page, name)
        with self.lock:
            try:
                info = annotations.update_annot(
                    self.fitz,
                    page,
                    name,
                    text=text,
                    font_size=font_size,
                    color=color,
                    rect=rect,
                    fit_height=fit_height,
                )
            except LookupError as exc:
                raise AnnotError(f"annotation {name!r} not found on page {page}") from exc
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise AnnotError(str(exc)) from exc
        self.page_changed.emit(page)
        return info

    def delete_annot(self, page: int, name: str) -> None:
        """Delete annotation ``name`` on ``page``. Emits page_changed.

        Raises :class:`AnnotError` if it is gone or locked.
        """
        self._check_index(page)
        self._check_annotate()
        self._check_name(page, name)
        with self.lock:
            try:
                deleted = annotations.delete_annot(self.fitz, page, name)
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise AnnotError(str(exc)) from exc
        if not deleted:
            raise AnnotError(f"annotation {name!r} not found on page {page}")
        self.page_changed.emit(page)

    def _clear_annot_cache(self, *_args: object) -> None:
        self._annot_cache.clear()
        self._image_xrefs.clear()

    # -- snapping ------------------------------------------------------------
    def page_shapes(self, i: int) -> PageShapes:
        """Snapping targets of page ``i``'s content (cells, rules, checkboxes) in page
        space; see :mod:`pdfeditor.core.snapping`.

        Cached per page and dropped on ``page_changed(i)`` (rotation, and annotation
        edits: paths inside annotation rects are skipped, so moving one changes the
        shapes), ``structure_changed``, ``reloaded`` and ``close()``. A cache hit never
        takes the lock (hover must not wait for a render); only a miss scans under it. A
        page that cannot be scanned has no shapes.
        """
        cached = self._shapes_cache.get(i)
        if cached is not None:
            return cached
        self._check_index(i)
        with self.lock:
            cached = self._shapes_cache.get(i)
            if cached is None:
                try:
                    cached = snapping.scan_page(self.fitz[i])
                except Exception:  # MuPDF raises FzError* (not RuntimeError)
                    log.warning("could not scan page %d for snapping", i, exc_info=True)
                    cached = snapping.EMPTY_SHAPES
                self._shapes_cache[i] = cached
        return cached

    def _clear_shapes_cache(self) -> None:
        self._shapes_cache.clear()

    def _on_reloaded(self) -> None:
        self._widget_cache.clear()
        self._clear_annot_cache()
        self._load_generation += 1
        self._clear_shapes_cache()
        self._read_form_state()

    def render(self, i: int, scale: float, clip: QRectF | None = None) -> QImage:
        """Render page ``i`` (with annotations) at ``scale`` to an RGB888 QImage.

        ``clip`` is in page space (points), for future tiling.
        """
        self._check_index(i)
        with self.lock:
            page = self.fitz[i]
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                alpha=False,
                annots=True,
                clip=fitz_from_qrect(clip) if clip is not None else None,
            )
            image = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
            ).copy()
        return image

    # -- saving ------------------------------------------------------------
    def modified_on_disk(self) -> bool:
        """True if the file at ``path`` changed (or vanished) since it was opened or saved.

        Based on (size, mtime). The document itself is unaffected (it lives in memory),
        but saving overwrites the other program's changes: ask the user first.
        """
        if self._path is None or self._disk_stamp is None:
            return False
        return _stamp(self._path) != self._disk_stamp

    def can_save_incrementally(self) -> bool:
        """True if ``save()`` will append an incremental update to the loaded file."""
        if self._path is None or self._disk_stamp is None or self._needs_full_save:
            return False
        if self.modified_on_disk():
            return False
        with self.lock:
            doc = self.fitz
            return not doc.is_repaired and bool(doc.can_save_incrementally())

    def save(self, force_full: bool = False) -> None:
        """Save in place: incrementally when possible, else a full rewrite.

        A full rewrite is used when ``force_full``, when the file was repaired on open, or
        when it was modified on disk since it was opened/saved (see
        :meth:`modified_on_disk`). Raises :class:`SaveError`; the document stays open and
        keeps its changes after a failure.
        """
        if self._path is None:
            raise SaveError("document has no file path; use save_as()")
        incremental = not force_full and self.can_save_incrementally()
        incremental = self._save_to(self._path, incremental)
        log.info("saved (%s): %s", "incremental" if incremental else "full", self._path)

    def save_as(self, new_path: str | os.PathLike[str]) -> None:
        """Full save to ``new_path``, which becomes the document's path.

        Raises :class:`SaveError`; the path is unchanged after a failure.
        """
        new_path = str(new_path)
        same = self._path is not None and same_file(new_path, self._path)
        self._save_to(new_path, incremental=False)
        if not same:
            self._path = new_path
            self.path_changed.emit(new_path)
        log.info("saved as: %s", new_path)

    def export_copy(
        self, path: str | os.PathLike[str], options: ExportOptions | None = None
    ) -> None:
        """Write a copy of the current state (unsaved changes included) to ``path``.

        The copy is built from an in-memory write of this document, then flattened,
        cleaned and fully rewritten (``garbage=4``) as ``options`` say. The open document,
        its path, caches, undo history and modified state are unchanged and no signal is
        emitted; only its next save is a full one (an incremental write after an
        in-memory write would produce a corrupt file). A static XFA form filled in this
        session loses its /XFA in the copy; a dynamic XFA form is never flattened
        (``flatten_forms`` is ignored). Raises ``ValueError`` if ``path`` is the
        document's own file, :class:`SaveError` if the copy cannot be made or written (no
        temp file is left behind).
        """
        options = options if options is not None else ExportOptions()
        path = str(path)
        if self._path is not None and same_file(path, self._path):
            raise ValueError("an exported copy cannot replace the open document")
        if self._xfa_kind is XfaKind.DYNAMIC and options.flatten_forms:
            log.info("dynamic XFA form: exporting without flattening the fields")
            options = replace(options, flatten_forms=False)
        if self.must_keep_encryption and not options.keep_encryption:
            log.info("the author's restrictions are kept in the exported copy")
            options = replace(options, keep_encryption=True)
        strip = self._xfa_kind is XfaKind.STATIC and self._form_edited
        with self.lock:
            try:
                try:
                    data = self.fitz.tobytes(
                        garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_KEEP
                    )
                finally:
                    # The in-memory write marks the changes as written: an incremental
                    # save on top of it would produce a corrupt file (docs/M5_PLAN.md C1).
                    self._needs_full_save = True
                out = _export_bytes(data, options, self._password, strip_static_xfa=strip)
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise SaveError(str(exc)) from exc
        try:
            _write_atomically(path, out)
        except OSError as exc:
            raise SaveError(str(exc)) from exc
        log.info("exported %s to %s (%s)", self._path, path, options)

    def _full_save_kwargs(self) -> dict[str, object]:
        return {"garbage": 3, "deflate": True, "encryption": pymupdf.PDF_ENCRYPT_KEEP}

    def _save_to(self, path: str, incremental: bool) -> bool:
        """Write the document to ``path``; returns whether the write was incremental."""
        # A full write may renumber objects of the in-memory document, and an incremental
        # write marks its changes as written even if writing the file then fails (a
        # second incremental write of the same document produces a file with missing
        # objects): either way it can no longer be the base of an incremental update
        # until it is reloaded from the bytes on disk (which resets the flag).
        self._needs_full_save = True
        with self.lock:
            try:
                if incremental and not self._drop_session_orphans():
                    incremental = False
                if incremental:
                    data = _incremental_bytes(self.fitz)
                else:
                    try:
                        data = self.fitz.tobytes(**self._full_save_kwargs())
                    finally:
                        # garbage=3 renumbers the in-memory objects: cached xrefs are
                        # stale even if writing the file (or the reload) fails.
                        self._widget_cache.clear()
                        self._clear_annot_cache()
                        self._load_generation += 1
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise SaveError(str(exc)) from exc
        try:
            _write_atomically(path, data)
        except OSError as exc:
            raise SaveError(str(exc)) from exc
        self._disk_stamp = _stamp(path)
        self._reload(data)
        return incremental

    def _drop_session_orphans(self) -> bool:
        """Free the objects created since the load that nothing references any more
        (undone or deleted signatures and their images, replaced appearances...), so that
        an incremental write does not carry them. False if that failed (logged): the
        caller then writes the whole file, whose garbage collection drops them. Under the
        lock."""
        try:
            dead = orphans.session_orphans(self.fitz, self._first_new_xref)
            if dead:
                orphans.drop_objects(self.fitz, dead)
                log.info("dropped %d orphaned objects before an incremental save", len(dead))
        except Exception:  # MuPDF raises FzError* (not RuntimeError)
            log.warning("could not drop orphaned objects; saving in full", exc_info=True)
            self._image_xrefs.clear()
            return False
        if dead:
            self._image_xrefs.clear()  # may name a dropped image
        return True

    def _reload(self, data: bytes) -> None:
        """Replace the document by one loaded from the bytes just written.

        The next incremental save must be based on exactly what is on disk. If loading
        fails, the current (equivalent) document is kept and the next save is a full one:
        the file on disk is already correct, so this is not a save failure.
        """
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            log.warning("could not reload the saved document; keeping it", exc_info=True)
            self._needs_full_save = True
            return
        # Read needs_pass only before authenticate(): reading it afterwards breaks
        # decryption for the document's life (see open()).
        locked = bool(doc.needs_pass)
        if locked and self._password is not None:
            locked = not doc.authenticate(self._password)
        if locked or doc.page_count != self._page_count:
            log.warning("reloaded document differs from the saved one; keeping the old one")
            doc.close()
            self._needs_full_save = True
            return
        with self.lock:
            old, self._doc = self._doc, doc
            self._first_new_xref = int(doc.xref_length())
            if old is not None:
                old.close()
        self._needs_full_save = False
        self.reloaded.emit()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        with self.lock:
            if self._doc is not None:
                self._doc.close()
                self._doc = None
        self._page_count = 0
        self._size_cache.clear()
        self._widget_cache.clear()
        self._clear_annot_cache()
        self._clear_shapes_cache()
        self._is_form = self._can_fill_forms = self._can_annotate = self._form_edited = False
        self._can_assemble = self._can_extract = self._structure_edited = False
        self._xfa_kind = XfaKind.NONE
        self._page_ids = []
        self._reindex()
        self.snapshots.clear()

    def _on_page_changed(self, i: int) -> None:
        self._size_cache.pop(i, None)
        self._widget_cache.pop(i, None)
        self._annot_cache.pop(i, None)
        self._shapes_cache.pop(i, None)

    def _on_structure_changed(self) -> None:
        with self.lock:
            self._page_count = int(self._doc.page_count) if self._doc is not None else 0
        self._size_cache.clear()
        self._widget_cache.clear()
        self._clear_annot_cache()
        self._clear_shapes_cache()


def _shifted(count: int, index: int, n: int) -> list[int | None]:
    """old_to_new of inserting ``n`` pages at ``index`` into ``count`` pages."""
    return [i if i < index else i + n for i in range(count)]


def authenticate(
    fitz_doc: pymupdf.Document, password_cb: PasswordCallback | None, password: str | None
) -> tuple[str, bool]:
    """Authenticate a document whose ``needs_pass`` is set: (password, owner access).

    ``authenticate()`` returns 2 for the user password, 4 for the owner password, 6 when
    both are the same. ``password`` is tried first, then ``password_cb(attempt)`` until it
    returns the right password or ``None``. Closes ``fitz_doc`` and raises
    :class:`PasswordRequired` on failure.
    """
    attempt = 0
    candidate = password
    while True:
        if candidate is None and password_cb is not None:
            candidate = password_cb(attempt)
        if candidate is None:
            fitz_doc.close()
            raise PasswordRequired(wrong_password=attempt > 0)
        level = int(fitz_doc.authenticate(candidate))
        if level:
            return candidate, bool(level & AUTH_OWNER)
        attempt += 1
        candidate = None
        if password_cb is None:
            fitz_doc.close()
            raise PasswordRequired("wrong password", wrong_password=True)


def open_fitz(path: str) -> tuple[pymupdf.Document, DiskStamp]:
    """Open ``path`` from an in-memory copy of its bytes: (document, disk stamp).

    Raises :class:`OpenError` (missing, unreadable, empty or corrupt file).
    """
    p = Path(path)
    if not p.is_file():
        raise OpenError(f"file not found: {path}", reason="missing")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise OpenError(str(exc), reason="unreadable") from exc
    stamp = _stamp(path)
    if stamp is None or not data:
        raise OpenError(f"file is empty: {path}", reason="empty")
    try:
        return pymupdf.open(stream=data, filetype="pdf"), stamp
    except Exception as exc:  # FileDataError, FzError*, ... -> OpenError
        raise OpenError(str(exc), reason="corrupt") from exc


def pdf_library_versions() -> list[tuple[str, str]]:
    """(name, version) of the PDF libraries, for the About box (UI must not import pymupdf)."""
    return [("PyMuPDF", str(pymupdf.VersionBind)), ("MuPDF", str(pymupdf.VersionFitz))]


def _stamp(path: str) -> DiskStamp | None:
    """(size, mtime_ns) of ``path``, or None if it does not exist."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _incremental_bytes(doc: pymupdf.Document) -> bytes:
    """The loaded file followed by an incremental update holding the unsaved changes.

    ``Document.save/tobytes(incremental=True)`` refuse documents opened from memory, so
    MuPDF is called directly: an incremental write to an empty buffer first copies the
    original bytes, then appends the update.
    """
    mupdf = pymupdf.mupdf
    opts = mupdf.PdfWriteOptions()
    opts.do_incremental = 1
    opts.do_encrypt = pymupdf.PDF_ENCRYPT_KEEP
    buf = mupdf.FzBuffer(1024)
    out = mupdf.FzOutput(buf)
    try:
        mupdf.pdf_write_document(pymupdf._as_pdf_document(doc), out, opts)
    finally:
        out.fz_close_output()
    return bytes(buf.fz_buffer_extract())


def _export_bytes(
    data: bytes,
    options: ExportOptions,
    password: str | None,
    *,
    strip_static_xfa: bool = False,
) -> bytes:
    """The exported file made from ``data`` (an in-memory write of the document, with
    its encryption): opened as a separate document (authenticated with ``password``),
    XFA stripped, baked and metadata cleared as asked, then fully rewritten with
    ``garbage=4`` (no free or unreferenced objects, no earlier revisions)."""
    copy = pymupdf.open(stream=data, filetype="pdf")
    try:
        # needs_pass is read only before authenticate() (see PdfDocument.open()).
        if copy.needs_pass and not copy.authenticate(password or ""):
            raise SaveError("the copy could not be decrypted")
        if strip_static_xfa:
            strip_xfa(copy)
        if options.flatten_forms or options.flatten_annots:
            copy.bake(annots=options.flatten_annots, widgets=options.flatten_forms)
        if not options.keep_metadata:
            copy.set_metadata({})
            copy.del_xml_metadata()
            _drop_private_metadata(copy)
        encryption = (
            pymupdf.PDF_ENCRYPT_KEEP if options.keep_encryption else pymupdf.PDF_ENCRYPT_NONE
        )
        return copy.tobytes(garbage=4, deflate=True, encryption=encryption)
    finally:
        copy.close()


#: Keys holding document-private data besides the Info dictionary and the catalog XMP:
#: XMP streams of the pages (/Metadata) and application private data (/PieceInfo, ISO
#: 32000 §14.5), plus, in the catalog only, the non-standard /Info dictionary in which
#: MuPDF-made documents carry a /Producer.
_PAGE_PRIVATE_KEYS = ("Metadata", "PieceInfo")
_CATALOG_PRIVATE_KEYS = ("Metadata", "PieceInfo", "Info")


def _drop_private_metadata(pdf: pymupdf.Document) -> None:
    """Delete the private-metadata keys from the catalog and every page (the objects
    they pointed to become unreferenced and are dropped by ``garbage=4``)."""
    mupdf = pymupdf.mupdf
    raw = pymupdf._as_pdf_document(pdf)
    targets = [(pdf.pdf_catalog(), _CATALOG_PRIVATE_KEYS)]
    targets += [(pdf.page_xref(i), _PAGE_PRIVATE_KEYS) for i in range(pdf.page_count)]
    for xref, keys in targets:
        obj = mupdf.pdf_new_indirect(raw, xref, 0)
        for key in keys:
            mupdf.pdf_dict_dels(obj, key)


REPLACE_ATTEMPTS = 3
REPLACE_RETRY_DELAY_S = 0.1


def _write_atomically(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` through ``path + ".tmp"`` and ``os.replace``.

    ``os.replace`` is retried briefly (antivirus or indexer holding the file for a moment).
    Raises OSError; then the original file is untouched and no temp file is left behind.
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(REPLACE_RETRY_DELAY_S)
    except OSError:
        _remove_quietly(tmp)
        raise


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
