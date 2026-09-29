"""PdfDocument: the single owner of a ``pymupdf.Document``.

All PyMuPDF access must go through this class or happen under ``with doc.lock:``
because MuPDF is not thread-safe. Never hold the lock while calling Qt widgets.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pymupdf
from PySide6.QtCore import QObject, QRectF, QSizeF, Signal
from PySide6.QtGui import QImage

from pdfeditor.core.geometry import fitz_from_qrect, qrect_from_fitz, unrotated_to_page

log = logging.getLogger(__name__)

#: Called with the attempt number (0 = first prompt, >0 = previous password was wrong).
#: Returns the password, or ``None`` to cancel.
PasswordCallback = Callable[[int], str | None]


class DocumentError(Exception):
    """Base class for document errors."""


class OpenError(DocumentError):
    """The file could not be opened as a PDF (missing, empty, corrupt...)."""


class PasswordRequired(OpenError):  # noqa: N818 - public API name from the design
    """The PDF is encrypted and no (correct) password was supplied."""

    def __init__(self, message: str = "password required", wrong_password: bool = False):
        super().__init__(message)
        self.wrong_password = wrong_password


class SaveError(DocumentError):
    """The document could not be saved."""


class PdfDocument(QObject):
    """Qt-facing wrapper around a PyMuPDF document."""

    page_changed = Signal(int)
    structure_changed = Signal()
    path_changed = Signal(str)

    def __init__(
        self,
        fitz_doc: pymupdf.Document,
        path: str | None = None,
        password: str | None = None,
        encrypted: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.lock = threading.RLock()
        self._doc: pymupdf.Document | None = fitz_doc
        self._path = str(path) if path else None
        self._password = password
        self._encrypted = encrypted
        self._from_disk = path is not None
        self._size_cache: dict[int, QSizeF] = {}
        self.page_changed.connect(self._on_page_changed)
        self.structure_changed.connect(self._size_cache.clear)

    # -- opening -----------------------------------------------------------
    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        password_cb: PasswordCallback | None = None,
        password: str | None = None,
    ) -> PdfDocument:
        """Open ``path``.

        Raises :class:`OpenError` (missing/empty/corrupt file) or :class:`PasswordRequired`
        (encrypted and the password was not supplied, was wrong, or the prompt was cancelled).
        ``password_cb`` is called repeatedly until it returns the right password or ``None``.
        """
        path = str(path)
        fitz_doc = cls._open_fitz(path)
        encrypted = bool(fitz_doc.needs_pass)
        used_password: str | None = None
        if encrypted:
            used_password = cls._authenticate(fitz_doc, password_cb, password)
        if fitz_doc.page_count == 0:
            fitz_doc.close()
            raise OpenError(f"document has no pages: {path}")
        return cls(fitz_doc, path, used_password, encrypted)

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
    def _open_fitz(path: str) -> pymupdf.Document:
        p = Path(path)
        if not p.is_file():
            raise OpenError(f"file not found: {path}")
        if p.stat().st_size == 0:
            raise OpenError(f"file is empty: {path}")
        try:
            return pymupdf.open(path, filetype="pdf")
        except Exception as exc:  # FileDataError, FzError*, ... -> OpenError
            raise OpenError(str(exc)) from exc

    # -- properties --------------------------------------------------------
    @property
    def fitz(self) -> pymupdf.Document:
        """The underlying document. Use only under ``self.lock``."""
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
        with self.lock:
            return self.fitz.page_count

    @property
    def is_encrypted(self) -> bool:
        return self._encrypted

    @property
    def permissions(self) -> int:
        with self.lock:
            return int(self.fitz.permissions)

    @property
    def was_repaired(self) -> bool:
        with self.lock:
            return bool(self.fitz.is_repaired)

    # -- pages -------------------------------------------------------------
    def _check_index(self, i: int) -> None:
        if not 0 <= i < self.page_count:
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

    def widget_rects(self, i: int) -> list[tuple[str, QRectF]]:
        """(field name, rect in page space) for each form widget of page ``i``."""
        self._check_index(i)
        with self.lock:
            page = self.fitz[i]
            matrix = page.rotation_matrix
            return [
                (w.field_name or "", qrect_from_fitz(unrotated_to_page(w.rect, matrix)))
                for w in page.widgets()
            ]

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
    def can_save_incrementally(self) -> bool:
        """True if ``save()`` can append an incremental update to the file on disk."""
        if self._path is None or not self._from_disk or not Path(self._path).is_file():
            return False
        with self.lock:
            doc = self.fitz
            return not doc.is_repaired and bool(doc.can_save_incrementally())

    def save(self, force_full: bool = False) -> None:
        """Save in place: incrementally when possible, else a full rewrite.

        Raises :class:`SaveError`. The document stays usable after a failure.
        """
        if self._path is None:
            raise SaveError("document has no file path; use save_as()")
        if not force_full and self.can_save_incrementally():
            try:
                with self.lock:
                    self.fitz.save(
                        self._path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP
                    )
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise SaveError(str(exc)) from exc
            log.info("saved incrementally: %s", self._path)
            return
        self._full_save_in_place()

    def _full_save_kwargs(self) -> dict[str, object]:
        return {"garbage": 3, "deflate": True, "encryption": pymupdf.PDF_ENCRYPT_KEEP}

    def _full_save_in_place(self) -> None:
        assert self._path is not None
        path = self._path
        tmp = path + ".tmp"
        with self.lock:
            try:
                self.fitz.save(tmp, **self._full_save_kwargs())
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                _remove_quietly(tmp)
                raise SaveError(str(exc)) from exc
            # Windows: MuPDF keeps the file open, so close before replacing.
            self._doc.close()
            self._doc = None
            try:
                os.replace(tmp, path)
            except OSError as exc:
                # Keep the saved changes alive in memory; the original file is untouched.
                self._reopen_from_bytes(tmp)
                raise SaveError(str(exc)) from exc
            self._reopen(path)
        log.info("saved (full rewrite): %s", path)
        self._size_cache.clear()
        self.path_changed.emit(path)

    def save_as(self, new_path: str | os.PathLike[str]) -> None:
        """Full save to ``new_path`` then reopen from there. Raises :class:`SaveError`."""
        new_path = str(new_path)
        if self._path is not None and _same_file(new_path, self._path):
            self._full_save_in_place()
            return
        with self.lock:
            try:
                self.fitz.save(new_path, **self._full_save_kwargs())
            except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
                raise SaveError(str(exc)) from exc
            self._doc.close()
            self._doc = None
            self._reopen(new_path)
        self._path = new_path
        log.info("saved as: %s", new_path)
        self._size_cache.clear()
        self.path_changed.emit(new_path)

    def _reopen(self, path: str) -> None:
        doc = pymupdf.open(path, filetype="pdf")
        if doc.needs_pass and self._password is not None:
            doc.authenticate(self._password)
        self._doc = doc
        self._from_disk = True

    def _reopen_from_bytes(self, tmp: str) -> None:
        data = Path(tmp).read_bytes()
        _remove_quietly(tmp)
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.needs_pass and self._password is not None:
            doc.authenticate(self._password)
        self._doc = doc
        self._from_disk = False

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        with self.lock:
            if self._doc is not None:
                self._doc.close()
                self._doc = None
        self._size_cache.clear()

    def _on_page_changed(self, i: int) -> None:
        self._size_cache.pop(i, None)


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
