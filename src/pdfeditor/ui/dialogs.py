"""Small dialogs and message helpers (patched in tests)."""

from __future__ import annotations

import html
import os

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QWidget,
)


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


def warn(parent: QWidget | None, title: str, text: str, details: str | None = None) -> None:
    """Warning box; ``details`` (e.g. a raw technical error) goes behind "Show Details..."."""
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.Ok, parent)
    if details:
        box.setDetailedText(details)
    box.exec()


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


def offer_save_as(parent: QWidget | None, details: str) -> bool:
    """Tell the user saving failed; return True if they want to try Save As.

    ``details`` is the technical error, shown only under "Show Details...".
    """
    box = QMessageBox(
        QMessageBox.Icon.Warning,
        QCoreApplication.translate("Dialogs", "Save failed"),
        QCoreApplication.translate(
            "Dialogs",
            # Single literal: lupdate does not join implicitly concatenated strings.
            "The document could not be saved. The file may be open in another program, read-only, or the disk may be full.\n\nSave it under another name?",  # noqa: E501
        ),
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        parent,
    )
    box.setDefaultButton(QMessageBox.StandardButton.Save)
    if details:
        box.setDetailedText(details)
    return box.exec() == QMessageBox.StandardButton.Save


# Answers of ask_overwrite_modified().
OVERWRITE, SAVE_AS, CANCEL = "overwrite", "save_as", "cancel"


def ask_overwrite_modified(parent: QWidget | None, file_name: str) -> str:
    """The file changed on disk since it was opened: overwrite, Save As, or cancel?"""
    box = QMessageBox(
        QMessageBox.Icon.Warning,
        QCoreApplication.translate("Dialogs", "File changed on disk"),
        QCoreApplication.translate(
            "Dialogs",
            "The file “{name}” has been changed by another program since it was opened.\n\nOverwrite it with your version? The other changes will be lost.",  # noqa: E501
        ).format(name=file_name),
        QMessageBox.StandardButton.NoButton,
        parent,
    )
    overwrite = box.addButton(
        QCoreApplication.translate("Dialogs", "Overwrite"), QMessageBox.ButtonRole.AcceptRole
    )
    save_as = box.addButton(
        QCoreApplication.translate("Dialogs", "Save As…"), QMessageBox.ButtonRole.ActionRole
    )
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(save_as)
    box.exec()
    clicked = box.clickedButton()
    if clicked is overwrite:
        return OVERWRITE
    if clicked is save_as:
        return SAVE_AS
    return CANCEL


def confirm_overwrite(parent: QWidget | None, path: str) -> bool:
    """Ask before replacing an existing file."""
    answer = QMessageBox.question(
        parent,
        QCoreApplication.translate("Dialogs", "Confirm Save As"),
        QCoreApplication.translate(
            "Dialogs", "“{name}” already exists.\nDo you want to replace it?"
        ).format(name=os.path.basename(path)),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def get_color(parent: QWidget | None, initial: QColor) -> QColor | None:
    """Text colour picker; ``None`` when cancelled."""
    color = QColorDialog.getColor(
        initial, parent, QCoreApplication.translate("Dialogs", "Choose text color")
    )
    return color if color.isValid() else None


def get_image_path(parent: QWidget | None, directory: str) -> str | None:
    """Choose a signature image; ``None`` when cancelled."""
    path, _ = QFileDialog.getOpenFileName(
        parent,
        QCoreApplication.translate("Dialogs", "Choose a signature image"),
        directory,
        QCoreApplication.translate(
            "Dialogs", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)"
        ),
    )
    return path or None


def ask_text(parent: QWidget | None, title: str, label: str, initial: str) -> str | None:
    """One-line text prompt; ``None`` when cancelled."""
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, initial)
    return text if ok else None


def confirm_delete_signature(parent: QWidget | None, name: str) -> bool:
    """Ask before deleting a saved signature."""
    answer = QMessageBox.question(
        parent,
        QCoreApplication.translate("Dialogs", "Delete"),
        QCoreApplication.translate("Dialogs", "Delete the signature “{name}”?").format(name=name),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def get_open_path(parent: QWidget | None, directory: str) -> str | None:
    path, _ = QFileDialog.getOpenFileName(
        parent,
        QCoreApplication.translate("Dialogs", "Open PDF"),
        directory,
        QCoreApplication.translate("Dialogs", "PDF documents (*.pdf);;All files (*)"),
    )
    return path or None


def make_save_dialog(parent: QWidget | None, suggested: str) -> QFileDialog:
    """Save dialog that appends ".pdf" *before* its own overwrite confirmation."""
    dialog = QFileDialog(
        parent,
        QCoreApplication.translate("Dialogs", "Save PDF As"),
        suggested,
        QCoreApplication.translate("Dialogs", "PDF documents (*.pdf)"),
    )
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setDefaultSuffix("pdf")
    return dialog


def _run_save_dialog(parent: QWidget | None, suggested: str) -> str | None:
    dialog = make_save_dialog(parent, suggested)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return None
    files = dialog.selectedFiles()
    return files[0] if files else None


def get_save_path(parent: QWidget | None, suggested: str) -> str | None:
    """Ask for a target path; always ends in ".pdf" and never silently overwrites."""
    path = _run_save_dialog(parent, suggested)
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        # Another extension was typed explicitly: the dialog confirmed *that* name only.
        path += ".pdf"
        if os.path.exists(path) and not confirm_overwrite(parent, path):
            return None
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

    import PySide6
    from PySide6.QtCore import qVersion

    from pdfeditor import __version__
    from pdfeditor.core.document import pdf_library_versions

    rows = [
        *pdf_library_versions(),
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
