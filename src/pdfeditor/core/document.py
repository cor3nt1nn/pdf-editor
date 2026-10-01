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

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pymupdf
from PySide6.QtCore import QObject, QRectF, QSizeF, Signal
from PySide6.QtGui import QImage

from pdfeditor.core import annotations, orphans, signature, snapping
from pdfeditor.core.annotations import AnnotInfo, AnnotKind, AnnotSpec
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


class PdfDocument(QObject):
    """Qt-facing wrapper around a PyMuPDF document."""

    #: A page's content or size changed (rotation, field value, annotation...). Views
    #: compare ``page_size(i)`` with the size they laid out to tell a geometry change
    #: (relayout) from a content-only change (re-render in place, no scroll jump).
    page_changed = Signal(int)
    #: Pages were added, removed or reordered.
    structure_changed = Signal()
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
    ) -> None:
        super().__init__(parent)
        self.lock = threading.RLock()
        self._doc: pymupdf.Document | None = fitz_doc
        self._path = str(path) if path else None
        self._password = password
        self._encrypted = encrypted
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
        fitz_doc, stamp = cls._open_fitz(path)
        encrypted = bool(fitz_doc.needs_pass)
        used_password: str | None = None
        if encrypted:
            used_password = cls._authenticate(fitz_doc, password_cb, password)
        if fitz_doc.page_count == 0:
            fitz_doc.close()
            raise OpenError(f"document has no pages: {path}", reason="no_pages")
        return cls(fitz_doc, path, used_password, encrypted, disk_stamp=stamp)

    @staticmethod
    def _authenticate(
        fitz_doc: pymupdf.Document, password_cb: PasswordCallback | None, password: str | None
    ) -> str:
        attempt = 0
        candidate = password
        while True:
            if candidate is None and password_cb is not None:
                candidate = password_cb(attempt)
            if candidate is None:
                fitz_doc.close()
                raise PasswordRequired(wrong_password=attempt > 0)
            if fitz_doc.authenticate(candidate):
                return candidate
            attempt += 1
            candidate = None
            if password_cb is None:
                fitz_doc.close()
                raise PasswordRequired("wrong password", wrong_password=True)

    @staticmethod
    def _open_fitz(path: str) -> tuple[pymupdf.Document, DiskStamp]:
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
        """Set the absolute rotation of page ``i`` (multiple of 90). Emits page_changed."""
        self._check_index(i)
        if int(degrees) % 90:
            raise ValueError(f"rotation must be a multiple of 90: {degrees}")
        degrees = int(degrees) % 360
        with self.lock:
            page = self.fitz[i]
            if page.rotation == degrees:
                return
            page.set_rotation(degrees)
        self.page_changed.emit(i)

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
        same = self._path is not None and _same_file(new_path, self._path)
        self._save_to(new_path, incremental=False)
        if not same:
            self._path = new_path
            self.path_changed.emit(new_path)
        log.info("saved as: %s", new_path)

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
        self._xfa_kind = XfaKind.NONE

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


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
