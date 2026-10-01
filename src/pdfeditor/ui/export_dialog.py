"""File ▸ Export Copy…: the export options dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from pdfeditor.core.document import ExportOptions, PdfDocument
from pdfeditor.core.forms import XfaKind
from pdfeditor.core.settings import Settings


class ExportDialog(QDialog):
    """Four options of :class:`ExportOptions`, initialised from ``settings``.

    "Keep password protection" is shown only for an encrypted file; it is checked and
    disabled for owner-password restrictions (always kept). A dynamic XFA form cannot
    be flattened: both flatten boxes are unchecked, disabled and explained by a note.
    """

    def __init__(
        self, document: PdfDocument, settings: Settings, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("export_dialog")
        self.setWindowTitle(self.tr("Export Copy"))
        self._settings = settings
        self._dynamic_xfa = document.xfa_kind is XfaKind.DYNAMIC
        self._encrypted = document.encryption_method is not None
        self._restricted = document.has_restrictions

        self.flatten_forms_box = QCheckBox(self.tr("Flatten form fields"), self)
        self.flatten_annots_box = QCheckBox(self.tr("Flatten text, stamps and signatures"), self)
        self.keep_encryption_box = QCheckBox(self.tr("Keep password protection"), self)
        self.keep_metadata_box = QCheckBox(
            self.tr("Keep document properties (title, author…)"), self
        )
        self.flatten_forms_box.setChecked(settings.export_flatten_forms)
        self.flatten_annots_box.setChecked(settings.export_flatten_annots)
        self.keep_metadata_box.setChecked(settings.export_keep_metadata)
        if self._dynamic_xfa:
            for box in (self.flatten_forms_box, self.flatten_annots_box):
                box.setChecked(False)
                box.setEnabled(False)
        self.keep_encryption_box.setVisible(self._encrypted)
        if self._restricted:
            self.keep_encryption_box.setChecked(True)
            self.keep_encryption_box.setEnabled(False)
        else:
            self.keep_encryption_box.setChecked(settings.export_keep_encryption)

        self.xfa_note = QLabel(
            self.tr("This form uses dynamic XFA: its fields cannot be flattened here."), self
        )
        self.xfa_note.setWordWrap(True)
        self.xfa_note.setVisible(self._dynamic_xfa)
        self.note = QLabel(
            self.tr(
                # Single literal: lupdate does not join implicitly concatenated strings.
                "A copy is written; the open document is not changed. Flattened fields and annotations can no longer be edited. Earlier saved versions (deleted signatures, previous values) are not carried over."  # noqa: E501
            ),
            self,
        )
        self.note.setWordWrap(True)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Export"))
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for widget in (
            self.flatten_forms_box,
            self.flatten_annots_box,
            self.xfa_note,
            self.keep_encryption_box,
            self.keep_metadata_box,
            self.note,
        ):
            layout.addWidget(widget)
        layout.addWidget(self.button_box)
        self.setMinimumWidth(420)

    def options(self) -> ExportOptions:
        """The effective options (forced values included)."""
        return ExportOptions(
            flatten_forms=self.flatten_forms_box.isChecked() and not self._dynamic_xfa,
            flatten_annots=self.flatten_annots_box.isChecked() and not self._dynamic_xfa,
            keep_encryption=self._restricted or self.keep_encryption_box.isChecked(),
            keep_metadata=self.keep_metadata_box.isChecked(),
        )

    def save_choices(self) -> None:
        """Remember the choices the user could make (not the forced or hidden ones)."""
        s = self._settings
        if not self._dynamic_xfa:
            s.export_flatten_forms = self.flatten_forms_box.isChecked()
            s.export_flatten_annots = self.flatten_annots_box.isChecked()
        if self._encrypted and not self._restricted:
            s.export_keep_encryption = self.keep_encryption_box.isChecked()
        s.export_keep_metadata = self.keep_metadata_box.isChecked()
