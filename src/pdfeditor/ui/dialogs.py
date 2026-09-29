"""Small dialogs and message helpers (patched in tests)."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QWidget


def tr(text: str) -> str:
    return QCoreApplication.translate("Dialogs", text)


def ask_password(parent: QWidget | None, file_name: str, wrong: bool) -> str | None:
    """Prompt for a document password. Returns None if cancelled."""
    label = tr("The document “{name}” is protected by a password.").format(name=file_name)
    if wrong:
        label = tr("Wrong password. Please try again.") + "\n\n" + label
    label += "\n\n" + tr("Password:")
    text, ok = QInputDialog.getText(
        parent, tr("Password required"), label, QLineEdit.EchoMode.Password
    )
    return text if ok else None


def warn(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def confirm_save_changes(parent: QWidget | None, file_name: str) -> QMessageBox.StandardButton:
    """Ask Save / Discard / Cancel for unsaved changes."""
    return QMessageBox.question(
        parent,
        tr("Unsaved changes"),
        tr("The document “{name}” has unsaved changes.\nDo you want to save them?").format(
            name=file_name
        ),
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Save,
    )


def offer_save_as(parent: QWidget | None, error: str) -> bool:
    """Tell the user saving failed; return True if they want to try Save As."""
    answer = QMessageBox.warning(
        parent,
        tr("Save failed"),
        tr("The document could not be saved:\n{error}\n\nSave it under another name?").format(
            error=error
        ),
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Save,
    )
    return answer == QMessageBox.StandardButton.Save


def get_open_path(parent: QWidget | None, directory: str) -> str | None:
    path, _ = QFileDialog.getOpenFileName(
        parent, tr("Open PDF"), directory, tr("PDF documents (*.pdf);;All files (*)")
    )
    return path or None


def get_save_path(parent: QWidget | None, suggested: str) -> str | None:
    path, _ = QFileDialog.getSaveFileName(
        parent, tr("Save PDF As"), suggested, tr("PDF documents (*.pdf)")
    )
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    return path
