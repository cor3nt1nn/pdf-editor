"""Pages ▸ Insert Pages from File… and Pages ▸ Split Document… dialogs.

The module functions :func:`insert_pages` and :func:`split_document` run the dialogs
(tests patch them).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pdfeditor.core import pages
from pdfeditor.core.document import OpenError, PasswordRequired, PdfDocument
from pdfeditor.core.files import same_file
from pdfeditor.core.settings import Settings
from pdfeditor.ui import dialogs

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InsertRequest:
    """What :class:`InsertPagesDialog` asks for: the pages to insert as an unencrypted
    sub-document (``count`` pages) and the position they go to."""

    data: bytes
    index: int
    count: int
    path: str = ""
    password: str | None = None


# Positions of InsertPagesDialog.
BEFORE_CURRENT, AFTER_CURRENT, AT_END = "before", "after", "end"


class InsertPagesDialog(QDialog):
    """Choose a PDF file, which of its pages and where they go.

    The source is opened with :func:`pages.open_source` (password prompt loop of
    :func:`dialogs.ask_password`), never the open document itself. On accept the chosen
    pages are copied into an in-memory sub-document (:attr:`request`), so undo/redo
    never read the file again.
    """

    def __init__(
        self,
        document: PdfDocument,
        current: int,
        settings: Settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("insert_pages_dialog")
        self.setWindowTitle(self.tr("Insert Pages"))
        self._document = document
        self._current = max(0, current)
        self._settings = settings
        self._source = None  # pymupdf.Document opened by core.pages.open_source
        self._source_path = ""
        self._password: str | None = None
        self.request: InsertRequest | None = None

        self.path_edit = QLineEdit(self)
        self.path_edit.setReadOnly(True)
        self.browse_button = QPushButton(self.tr("Browse…"), self)
        self.browse_button.clicked.connect(self.browse)
        self.count_label = QLabel(self)
        file_row = QHBoxLayout()
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(self.browse_button)

        self.all_radio = QRadioButton(self.tr("All pages"), self)
        self.range_radio = QRadioButton(self.tr("Pages:"), self)
        self.range_edit = QLineEdit(self)
        self.range_edit.setPlaceholderText(self.tr("e.g. 1-3, 7, 10-"))
        self.all_radio.setChecked(True)
        self._which = QButtonGroup(self)
        self._which.addButton(self.all_radio)
        self._which.addButton(self.range_radio)
        pages_box = QGroupBox(self.tr("Pages to insert"), self)
        pages_layout = QVBoxLayout(pages_box)
        pages_layout.addWidget(self.all_radio)
        range_row = QHBoxLayout()
        range_row.addWidget(self.range_radio)
        range_row.addWidget(self.range_edit, 1)
        pages_layout.addLayout(range_row)

        self.before_radio = QRadioButton(self.tr("Before the current page"), self)
        self.after_radio = QRadioButton(self.tr("After the current page"), self)
        self.end_radio = QRadioButton(self.tr("At the end"), self)
        self.after_radio.setChecked(True)
        self._where = QButtonGroup(self)
        position_box = QGroupBox(self.tr("Position"), self)
        position_layout = QVBoxLayout(position_box)
        for radio in (self.before_radio, self.after_radio, self.end_radio):
            self._where.addButton(radio)
            position_layout.addWidget(radio)

        self.error_label = QLabel(self)
        self.error_label.setObjectName("error_label")
        self.error_label.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow(self.tr("File"), file_row)
        form.addRow("", self.count_label)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(pages_box)
        layout.addWidget(position_box)
        layout.addWidget(self.error_label)
        layout.addWidget(self.buttons)

        self.range_edit.textEdited.connect(lambda _t: self.range_radio.setChecked(True))
        self.range_edit.textChanged.connect(self._validate)
        self._which.buttonToggled.connect(self._validate)
        self._validate()

    # -- source ---------------------------------------------------------------
    @property
    def source_page_count(self) -> int:
        return int(self._source.page_count) if self._source is not None else 0

    def browse(self) -> None:
        directory = self._settings.last_open_dir
        path = dialogs.get_open_path(self, directory)
        if path:
            self.set_path(path)

    def set_path(self, path: str) -> bool:
        """Open ``path`` as the source (asking for its password if needed). False (and
        an explanation in the dialog) if it cannot be used."""
        self._close_source()
        path = os.path.abspath(path)
        self.path_edit.setText(path)
        error = ""
        doc_path = self._document.path
        if doc_path is not None and same_file(path, doc_path):
            error = self.tr("The document cannot be inserted into itself.")
        else:
            name = os.path.basename(path)
            try:
                self._source, self._password = pages.open_source(
                    path, lambda attempt: dialogs.ask_password(self, name, attempt > 0)
                )
                self._source_path = path
            except PasswordRequired:
                log.info("password prompt cancelled for %s", path)
                error = self.tr("“{name}” is protected by a password.").format(name=name)
            except OpenError as exc:
                log.warning("cannot open %s: %s", path, exc)
                if exc.reason == "no_copy":
                    error = self.tr(
                        "Copying pages from “{name}” is not permitted by its security settings."
                    ).format(name=name)
                else:
                    error = self.tr("“{name}” could not be opened as a PDF document.").format(
                        name=name
                    )
        self._source_error = error
        count = self.source_page_count
        self.count_label.setText(self.tr("{count} pages").format(count=count) if count else "")
        self._validate()
        return self._source is not None

    def _close_source(self) -> None:
        if self._source is not None:
            self._source.close()
        self._source = None
        self._source_path = ""
        self._password = None
        self._source_error = ""

    # -- choices --------------------------------------------------------------
    def chosen_pages(self) -> list[int]:
        """0-based pages of the source to insert. Raises :class:`pages.PageRangeError`."""
        count = self.source_page_count
        if self.all_radio.isChecked():
            return list(range(count))
        result = pages.parse_page_ranges(self.range_edit.text(), count)
        if not result:
            raise pages.PageRangeError(self.range_edit.text().strip())
        return result

    def set_position(self, position: str) -> None:
        {
            BEFORE_CURRENT: self.before_radio,
            AFTER_CURRENT: self.after_radio,
            AT_END: self.end_radio,
        }[position].setChecked(True)

    def insert_index(self) -> int:
        count = self._document.page_count
        if self.end_radio.isChecked():
            return count
        if self.before_radio.isChecked():
            return min(self._current, count)
        return min(self._current + 1, count)

    def _validate(self, *_args: object) -> bool:
        error = getattr(self, "_source_error", "")
        ok = self._source is not None and not error
        if ok:
            try:
                self.chosen_pages()
            except pages.PageRangeError as exc:
                ok = False
                error = self.tr("Invalid page range: “{token}”").format(token=exc.token)
        self.error_label.setText(error)
        self.error_label.setVisible(bool(error))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
        return ok

    def accept(self) -> None:
        if not self._validate():
            return
        chosen = self.chosen_pages()
        try:
            data = pages.subdocument_bytes(self._source, chosen)
        except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
            log.warning("cannot copy pages of %s: %s", self._source_path, exc)
            self._source_error = self.tr("The pages could not be read.")
            self._validate()
            return
        self.request = InsertRequest(
            data=data,
            index=self.insert_index(),
            count=len(chosen),
            path=self._source_path,
        )
        self._settings.last_open_dir = os.path.dirname(self._source_path)
        self._close_source()  # subdocument_bytes mutated it
        super().accept()

    def done(self, result: int) -> None:
        self._close_source()
        super().done(result)


def insert_pages(
    parent: QWidget | None, document: PdfDocument, current: int, settings: Settings
) -> InsertRequest | None:
    """Run :class:`InsertPagesDialog` (asking for the file first); None when cancelled."""
    dialog = InsertPagesDialog(document, current, settings, parent)
    try:
        path = dialogs.get_open_path(dialog, settings.last_open_dir)
        if not path:
            return None
        dialog.set_path(path)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.request
    finally:
        dialog.deleteLater()


def split_file_names(base: str, count: int) -> list[str]:
    """``<base>-01.pdf`` … (at least two digits)."""
    width = max(2, len(str(count)))
    return [f"{base}-{n:0{width}d}.pdf" for n in range(1, count + 1)]


#: Characters a Windows file name cannot contain.
INVALID_NAME_CHARS = '\\/:*?"<>|'


class SplitDialog(QDialog):
    """Split the document every N pages or along explicit ranges into files
    ``<base>-01.pdf``… of a folder. A relative folder is relative to ``document_dir``
    (the document's folder). When OK is disabled the summary line says why."""

    def __init__(
        self,
        page_count: int,
        suggested_dir: str,
        stem: str,
        parent: QWidget | None = None,
        *,
        document_dir: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("split_dialog")
        self.setWindowTitle(self.tr("Split Document"))
        self._page_count = page_count
        self._document_dir = document_dir

        # "Every {n} pages": the spin box takes the place of {n}.
        before, _sep, after = self.tr("Every {n} pages").partition("{n}")
        self.every_radio = QRadioButton(before.strip(), self)
        self.every_spin = QSpinBox(self)
        self.every_spin.setRange(1, max(1, page_count))
        self.every_spin.setValue(max(1, min(page_count // 2 or 1, page_count)))
        self.every_suffix = QLabel(after.strip(), self)
        self.ranges_radio = QRadioButton(self.tr("Page ranges"), self)
        self.ranges_edit = QLineEdit(self)
        self.ranges_edit.setPlaceholderText(self.tr("e.g. 1-3, 7, 10-"))
        self.every_radio.setChecked(True)
        self._mode = QButtonGroup(self)
        self._mode.addButton(self.every_radio)
        self._mode.addButton(self.ranges_radio)
        every_row = QHBoxLayout()
        every_row.addWidget(self.every_radio)
        every_row.addWidget(self.every_spin)
        every_row.addWidget(self.every_suffix)
        every_row.addStretch(1)
        ranges_row = QHBoxLayout()
        ranges_row.addWidget(self.ranges_radio)
        ranges_row.addWidget(self.ranges_edit, 1)

        self.folder_edit = QLineEdit(suggested_dir, self)
        self.folder_button = QPushButton(self.tr("Browse…"), self)
        self.folder_button.clicked.connect(self.browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.folder_button)
        self.base_edit = QLineEdit(stem, self)

        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("summary_label")
        self.summary_label.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow(self.tr("Folder"), folder_row)
        form.addRow(self.tr("Base name"), self.base_edit)
        layout = QVBoxLayout(self)
        layout.addLayout(every_row)
        layout.addLayout(ranges_row)
        layout.addLayout(form)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.buttons)

        self.every_spin.valueChanged.connect(self._on_every_changed)
        self.ranges_edit.textEdited.connect(lambda _t: self.ranges_radio.setChecked(True))
        for edit in (self.ranges_edit, self.folder_edit, self.base_edit):
            edit.textChanged.connect(self._validate)
        self._mode.buttonToggled.connect(self._validate)
        self._validate()

    def _on_every_changed(self, _value: int) -> None:
        self.every_radio.setChecked(True)
        self._validate()

    def browse(self) -> None:
        folder = dialogs.get_directory(self, self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def groups(self) -> list[list[int]]:
        """Page groups (0-based). Raises :class:`pages.PageRangeError`."""
        if self.every_radio.isChecked():
            return pages.split_every(self._page_count, self.every_spin.value())
        result = pages.split_ranges(self.ranges_edit.text(), self._page_count)
        if not result:
            raise pages.PageRangeError(self.ranges_edit.text().strip())
        return result

    def folder(self) -> str:
        """The chosen folder, absolute (a relative one is relative to the document's
        folder, else to the current directory)."""
        folder = os.path.expanduser(self.folder_edit.text().strip())
        if folder and not os.path.isabs(folder):
            folder = os.path.join(self._document_dir or os.getcwd(), folder)
        return os.path.normpath(folder) if folder else ""

    def paths(self, count: int) -> list[str]:
        folder = self.folder()
        base = self.base_edit.text().strip()
        return [os.path.join(folder, name) for name in split_file_names(base, count)]

    def result_value(self) -> tuple[list[list[int]], list[str]]:
        groups = self.groups()
        return groups, self.paths(len(groups))

    def _validate(self, *_args: object) -> bool:
        ok = True
        text = ""
        try:
            groups = self.groups()
        except pages.PageRangeError as exc:
            ok = False
            text = self.tr("Invalid page range: “{token}”").format(token=exc.token)
        else:
            names = split_file_names(self.base_edit.text().strip(), len(groups))
            text = self.tr("{count} files will be written: {first} … {last}").format(
                count=len(names), first=names[0], last=names[-1]
            )
        base = self.base_edit.text().strip()
        if ok and not base:
            ok = False
            text = self.tr("Enter a base name for the files.")
        elif ok and any(c in base for c in INVALID_NAME_CHARS):
            ok = False
            text = self.tr("A file name cannot contain any of these characters: {chars}").format(
                chars=" ".join(INVALID_NAME_CHARS)
            )
        if ok and not self.folder_edit.text().strip():
            ok = False
            text = self.tr("Choose the folder of the files.")
        self.summary_label.setText(text)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
        return ok

    def accept(self) -> None:
        if self._validate():
            super().accept()


def split_document(
    parent: QWidget | None,
    page_count: int,
    suggested_dir: str,
    stem: str,
    document_dir: str = "",
) -> tuple[list[list[int]], list[str]] | None:
    """Run :class:`SplitDialog`: (page groups, file paths) or None when cancelled."""
    dialog = SplitDialog(page_count, suggested_dir, stem, parent, document_dir=document_dir)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.result_value()
    finally:
        dialog.deleteLater()
