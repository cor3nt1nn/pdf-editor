"""Small dialogs and message helpers (patched in tests)."""

from __future__ import annotations

import html
import logging
import os

from PySide6.QtCore import QCoreApplication, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pdfeditor import paths

log = logging.getLogger(__name__)

# Link of the About box that opens Help ▸ Third-Party Licenses….
LICENSES_LINK = "pdfeditor:third-party-licenses"


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


def make_save_dialog(
    parent: QWidget | None, suggested: str, title: str | None = None
) -> QFileDialog:
    """Save dialog that appends ".pdf" *before* its own overwrite confirmation."""
    dialog = QFileDialog(
        parent,
        title or QCoreApplication.translate("Dialogs", "Save PDF As"),
        suggested,
        QCoreApplication.translate("Dialogs", "PDF documents (*.pdf)"),
    )
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setDefaultSuffix("pdf")
    return dialog


def _run_save_dialog(
    parent: QWidget | None, suggested: str, title: str | None = None
) -> str | None:
    dialog = make_save_dialog(parent, suggested, title)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return None
    files = dialog.selectedFiles()
    return files[0] if files else None


def get_export_path(parent: QWidget | None, suggested: str) -> str | None:
    """Target of File ▸ Export Copy…; same rules as :func:`get_save_path`."""
    title = QCoreApplication.translate("Dialogs", "Export a Copy")
    return get_save_path(parent, suggested, title)


def get_save_path(parent: QWidget | None, suggested: str, title: str | None = None) -> str | None:
    """Ask for a target path; always ends in ".pdf" and never silently overwrites."""
    path = _run_save_dialog(parent, suggested, title)
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        # Another extension was typed explicitly: the dialog confirmed *that* name only.
        path += ".pdf"
        if os.path.exists(path) and not confirm_overwrite(parent, path):
            return None
    return path


def confirm_register(parent: QWidget | None, command: str) -> bool:
    """Settings ▸ Register with Windows: explain what is written, show the command."""
    box = QMessageBox(
        QMessageBox.Icon.Question,
        QCoreApplication.translate("Dialogs", "Register with Windows"),
        QCoreApplication.translate(
            "Dialogs",
            "PDF Editor will be added to the “Open with” list for PDF files, for your Windows account only (no administrator rights). You can undo this with Settings ▸ Unregister from Windows.\n\nCommand: {command}",  # noqa: E501
        ).format(command=command),
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        parent,
    )
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    return box.exec() == QMessageBox.StandardButton.Ok


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
    licenses = QCoreApplication.translate("Dialogs", "Third-party licenses")
    return (
        f"<h3>PDF Editor</h3><p>{html.escape(version)}</p>"
        f"<p>{html.escape(summary)}</p>"
        f"<p>{html.escape(notice)} "
        '<a href="https://www.gnu.org/licenses/agpl-3.0.html">AGPL-3.0</a></p>'
        f"<p><b>{html.escape(built_with)}</b><br>{libs}</p>"
        f'<p><a href="{LICENSES_LINK}">{html.escape(licenses)}</a></p>'
        f"{_build_details_html()}"
    )


def _build_details_html() -> str:
    """ "Portable build" and the log file's path, for the frozen build only."""
    if not paths.is_frozen():
        return ""
    portable = QCoreApplication.translate("Dialogs", "Portable build")
    log_line = QCoreApplication.translate("Dialogs", "Log file: {path}").format(
        path=str(paths.log_path())
    )
    return f"<p>{html.escape(portable)}<br>{html.escape(log_line)}</p>"


def on_about_link(parent: QWidget | None, link: str) -> None:
    """A link of the About box: the licences dialog, or the system browser."""
    if link == LICENSES_LINK:
        show_third_party_licenses(parent)
    else:
        QDesktopServices.openUrl(QUrl(link))


def make_about_box(parent: QWidget | None) -> QMessageBox:
    """About box (like ``QMessageBox.about``) whose licences link opens our dialog."""
    box = QMessageBox(parent)
    box.setWindowTitle(QCoreApplication.translate("Dialogs", "About PDF Editor"))
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(about_html())
    icon = parent.windowIcon() if parent is not None else QIcon()
    if not icon.isNull():
        box.setIconPixmap(icon.pixmap(64, 64))
    label = box.findChild(QLabel, "qt_msgbox_label")
    if label is not None:
        label.setOpenExternalLinks(False)
        label.linkActivated.connect(lambda link: on_about_link(box, link))
    return box


def show_about(parent: QWidget | None) -> None:
    make_about_box(parent).exec()


# -- keyboard shortcuts -------------------------------------------------------------


def interaction_shortcuts() -> list[tuple[str, str]]:
    """(description, keys) of the mouse and keyboard keys that are not menu actions
    (page navigation keys are handled by the page view)."""

    def keys(*names: str) -> str:
        native = QKeySequence.SequenceFormat.NativeText
        return " / ".join(QKeySequence(n).toString(native) for n in names)

    return [
        (
            QCoreApplication.translate("Dialogs", "Zoom under the pointer"),
            QCoreApplication.translate("Dialogs", "Ctrl+Wheel"),
        ),
        # Handled by PageView.keyPressEvent (not menu shortcuts: the keys must keep
        # scrolling text editors and lists when they have the focus).
        (
            QCoreApplication.translate("Dialogs", "Next / previous page"),
            keys("PgDown", "PgUp"),
        ),
        (QCoreApplication.translate("Dialogs", "First / last page"), keys("Home", "End")),
        (
            QCoreApplication.translate("Dialogs", "Place without snapping / over a form field"),
            QCoreApplication.translate("Dialogs", "Alt+Click"),
        ),
        (
            QCoreApplication.translate("Dialogs", "Validate the text being typed"),
            keys("Ctrl+Return"),
        ),
        (QCoreApplication.translate("Dialogs", "Next / previous field"), keys("Tab", "Shift+Tab")),
        (QCoreApplication.translate("Dialogs", "Toggle the focused checkbox"), keys("Space")),
        (QCoreApplication.translate("Dialogs", "Cancel, deselect"), keys("Esc")),
    ]


# One section of the shortcuts dialog: (title, [(action, keys), ...]).
ShortcutSection = tuple[str, list[tuple[str, str]]]


def make_shortcuts_dialog(parent: QWidget | None, sections: list[ShortcutSection]) -> QDialog:
    """Two-column table (Action, Shortcut) with a bold title row per section."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(QCoreApplication.translate("Dialogs", "Keyboard Shortcuts"))
    table = QTableWidget(0, 2, dialog)
    table.setObjectName("shortcuts_table")
    table.setHorizontalHeaderLabels(
        [
            QCoreApplication.translate("Dialogs", "Action"),
            QCoreApplication.translate("Dialogs", "Shortcut"),
        ]
    )
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setShowGrid(False)
    bold = QFont(table.font())
    bold.setBold(True)
    for title, rows in sections:
        if not rows:
            continue
        r = table.rowCount()
        table.insertRow(r)
        item = QTableWidgetItem(title)
        item.setFont(bold)
        table.setItem(r, 0, item)
        table.setSpan(r, 0, 1, 2)
        for action, keys in rows:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(action))
            table.setItem(r, 1, QTableWidgetItem(keys))
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addWidget(table)
    layout.addWidget(buttons)
    dialog.resize(520, 600)
    return dialog


def show_shortcuts(parent: QWidget | None, sections: list[ShortcutSection]) -> None:
    make_shortcuts_dialog(parent, sections).exec()


# -- third-party licences -----------------------------------------------------------


def make_licenses_dialog(parent: QWidget | None) -> QDialog:
    """Third-party notice (THIRD_PARTY_LICENSES.md) and the full licence texts."""
    from pdfeditor.resources import license_files, third_party_notice

    dialog = QDialog(parent)
    dialog.setWindowTitle(QCoreApplication.translate("Dialogs", "Third-Party Licenses"))
    chooser = QComboBox(dialog)
    chooser.setObjectName("license_chooser")
    browser = QTextBrowser(dialog)
    browser.setObjectName("license_browser")
    browser.setOpenExternalLinks(True)
    notice = third_party_notice()
    if notice is not None:
        chooser.addItem(notice.name, str(notice))
    for path in license_files():
        chooser.addItem(path.name, str(path))

    def show(index: int) -> None:
        path = chooser.itemData(index)
        if not path:
            browser.clear()
            return
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            log.warning("cannot read %s: %s", path, exc)
            browser.setPlainText(str(exc))
            return
        if path.endswith(".md"):
            browser.setMarkdown(text)
        else:
            browser.setPlainText(text)

    chooser.currentIndexChanged.connect(show)
    show(chooser.currentIndex())
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addWidget(chooser)
    layout.addWidget(browser)
    layout.addWidget(buttons)
    dialog.resize(760, 640)
    return dialog


def show_third_party_licenses(parent: QWidget | None) -> None:
    make_licenses_dialog(parent).exec()
