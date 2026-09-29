"""Small dialogs and message helpers (patched in tests)."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QWidget


def ask_password(parent: QWidget | None, file_name: str, wrong: bool) -> str | None:
    """Prompt for a document password. Returns None if cancelled."""
    label = QCoreApplication.translate(
        "Dialogs", "The document “{name}” is protected by a password."
    ).format(name=file_name)
    if wrong:
        label = (
            QCoreApplication.translate("Dialogs", "Wrong password. Please try again.")
            + "\n\n"
            + label
        )
    label += "\n\n" + QCoreApplication.translate("Dialogs", "Password:")
    text, ok = QInputDialog.getText(
        parent,
        QCoreApplication.translate("Dialogs", "Password required"),
        label,
        QLineEdit.EchoMode.Password,
    )
    return text if ok else None


def warn(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def confirm_save_changes(parent: QWidget | None, file_name: str) -> QMessageBox.StandardButton:
    """Ask Save / Discard / Cancel for unsaved changes."""
    return QMessageBox.question(
        parent,
        QCoreApplication.translate("Dialogs", "Unsaved changes"),
        QCoreApplication.translate(
            "Dialogs", "The document “{name}” has unsaved changes.\nDo you want to save them?"
        ).format(name=file_name),
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Save,
    )


def offer_save_as(parent: QWidget | None, error: str) -> bool:
    """Tell the user saving failed; return True if they want to try Save As."""
    answer = QMessageBox.warning(
        parent,
        QCoreApplication.translate("Dialogs", "Save failed"),
        QCoreApplication.translate(
            "Dialogs", "The document could not be saved:\n{error}\n\nSave it under another name?"
        ).format(error=error),
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Save,
    )
    return answer == QMessageBox.StandardButton.Save


def get_open_path(parent: QWidget | None, directory: str) -> str | None:
    path, _ = QFileDialog.getOpenFileName(
        parent,
        QCoreApplication.translate("Dialogs", "Open PDF"),
        directory,
        QCoreApplication.translate("Dialogs", "PDF documents (*.pdf);;All files (*)"),
    )
    return path or None


def get_save_path(parent: QWidget | None, suggested: str) -> str | None:
    path, _ = QFileDialog.getSaveFileName(
        parent,
        QCoreApplication.translate("Dialogs", "Save PDF As"),
        suggested,
        QCoreApplication.translate("Dialogs", "PDF documents (*.pdf)"),
    )
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    return path


def ask_restart(parent: QWidget | None) -> bool:
    """Ask whether to restart now to apply a new language."""
    answer = QMessageBox.question(
        parent,
        QCoreApplication.translate("Dialogs", "Language changed"),
        QCoreApplication.translate(
            "Dialogs", "The new language will be used after restarting PDF Editor.\nRestart now?"
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    return answer == QMessageBox.StandardButton.Yes
