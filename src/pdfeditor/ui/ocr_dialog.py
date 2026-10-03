"""Edit ▸ Recognise Text (OCR)…: the scope dialog and the progress dialog (M8)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressDialog,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.settings import OCR_SCOPES, Settings


class OcrDialog(QDialog):
    """Which pages to recognise (this page, the pages without text, all pages) and
    whether to write the text into the pages, initialised from ``settings``.

    The "searchable" box is unchecked and disabled, with an explanation, when the
    document's permissions forbid changing its pages (``can_modify``): the text is then
    recognised in memory only (selectable and copyable until the document is closed).
    """

    def __init__(
        self,
        document: PdfDocument,
        settings: Settings,
        current_page: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ocr_dialog")
        self.setWindowTitle(self.tr("Recognise Text"))
        self._document = document
        self._settings = settings
        self._current = max(0, min(current_page, document.page_count - 1))
        self._can_modify = document.can_modify

        self.page_radio = QRadioButton(self.tr("This page"), self)
        self.without_text_radio = QRadioButton(self.tr("Pages without text"), self)
        self.all_radio = QRadioButton(self.tr("All pages"), self)
        self.scope_group = QButtonGroup(self)
        for radio in (self.page_radio, self.without_text_radio, self.all_radio):
            self.scope_group.addButton(radio)
        self._radios = dict(zip(OCR_SCOPES, (self.page_radio, self.without_text_radio,
                                             self.all_radio), strict=True))  # fmt: skip
        self._radios[settings.ocr_scope].setChecked(True)

        self.searchable_box = QCheckBox(
            self.tr("Make the text searchable (saved with the file)"), self
        )
        if self._can_modify:
            self.searchable_box.setChecked(settings.ocr_make_searchable)
        else:
            self.searchable_box.setChecked(False)
            self.searchable_box.setEnabled(False)
            self.searchable_box.setToolTip(
                self.tr(
                    # Single literal: lupdate does not join implicitly concatenated strings.
                    "This document’s security settings do not allow changing its pages: the recognised text stays in memory only."  # noqa: E501
                )
            )
        self.languages_label = QLabel(self.tr("Languages: French and English"), self)
        self.note = QLabel(
            self.tr(
                # Single literal: lupdate does not join implicitly concatenated strings.
                "Recognition takes about one second per page. Recognised text can be selected, searched and copied; it does not change how the page looks."  # noqa: E501
            ),
            self,
        )
        self.note.setWordWrap(True)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText(
            self.tr("Recognise Text")
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for widget in (
            self.page_radio,
            self.without_text_radio,
            self.all_radio,
            self.searchable_box,
            self.languages_label,
            self.note,
        ):
            layout.addWidget(widget)
        layout.addWidget(self.button_box)
        self.setMinimumWidth(420)

    def scope(self) -> str:
        """The chosen scope (one of :data:`OCR_SCOPES`)."""
        for scope, radio in self._radios.items():
            if radio.isChecked():
                return scope
        return OCR_SCOPES[1]

    def make_searchable(self) -> bool:
        return self._can_modify and self.searchable_box.isChecked()

    def pages(self) -> list[int]:
        """The pages to recognise for the chosen scope (the pages without text are those
        whose content shows no text — scans, blank pages — and scans whose only text is a
        small stamp: ``PdfDocument.lacks_text``, cheap and cached, never the full page
        text)."""
        doc = self._document
        scope = self.scope()
        if scope == "page":
            return [self._current]
        if scope == "all":
            return list(range(doc.page_count))
        return [i for i in range(doc.page_count) if doc.lacks_text(i)]

    def save_choices(self) -> None:
        self._settings.ocr_scope = self.scope()
        if self._can_modify:
            self._settings.ocr_make_searchable = self.searchable_box.isChecked()


class OcrProgress(QProgressDialog):
    """ "Recognising text… page n of m" with Cancel (window-modal while a run lasts)."""

    def __init__(self, total: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ocr_progress")
        self.setWindowTitle(self.tr("Recognise Text"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setMinimumDuration(0)
        self.setRange(0, max(total, 1))
        self.setValue(0)
        self._total = total
        self.set_done(0)

    def set_done(self, done: int) -> None:
        current = min(done + 1, self._total)
        self.setLabelText(
            self.tr("Recognising text… page {n} of {m}").format(n=current, m=self._total)
        )
        self.setValue(min(done, self._total))
