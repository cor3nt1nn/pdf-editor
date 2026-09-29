"""Small dialogs and message helpers (patched in tests)."""

from __future__ import annotations

import html

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


def about_html() -> str:
    """Rich text for the About box: version, license notice and library versions."""
    import platform

    import pymupdf
    import PySide6
    from PySide6.QtCore import qVersion

    from pdfeditor import __version__

    rows = [
        ("PyMuPDF", pymupdf.VersionBind),
        ("MuPDF", pymupdf.VersionFitz),
        ("PySide6", PySide6.__version__),
        ("Qt", qVersion()),
        ("Python", platform.python_version()),
    ]
    libs = "<br>".join(f"{name} {html.escape(str(ver))}" for name, ver in rows)
    version = QCoreApplication.translate("Dialogs", "Version {version}").format(version=__version__)
    summary = QCoreApplication.translate(
        "Dialogs", "Free and open-source PDF editor: fill forms, add text and stamps, sign."
    )
    # Single literal on purpose: lupdate does not join implicitly concatenated strings.
    notice = QCoreApplication.translate(
        "Dialogs",
        "This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License version 3. It comes with ABSOLUTELY NO WARRANTY.",  # noqa: E501
    )
    built_with = QCoreApplication.translate("Dialogs", "Built with:")
    return (
        f"<h3>PDF Editor</h3><p>{html.escape(version)}</p>"
        f"<p>{html.escape(summary)}</p>"
        f"<p>{html.escape(notice)} "
        '<a href="https://www.gnu.org/licenses/agpl-3.0.html">AGPL-3.0</a></p>'
        f"<p><b>{html.escape(built_with)}</b><br>{libs}</p>"
    )


def show_about(parent: QWidget | None) -> None:
    QMessageBox.about(
        parent, QCoreApplication.translate("Dialogs", "About PDF Editor"), about_html()
    )
