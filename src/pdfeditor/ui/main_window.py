"""Main application window (single document)."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QDir, QPoint, QProcess, Qt
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSpinBox,
    QToolButton,
)

from pdfeditor.constants import APP_NAME, ZoomMode
from pdfeditor.core import file_assoc, recent
from pdfeditor.core.annotations import AnnotKind
from pdfeditor.core.commands import (
    DeletePagesCommand,
    InsertBlankPageCommand,
    InsertPagesCommand,
    MovePagesCommand,
    RotatePagesCommand,
    _ImmediateCommand,
)
from pdfeditor.core.document import (
    DocumentError,
    OpenError,
    PageError,
    PasswordRequired,
    SaveError,
)
from pdfeditor.core.files import same_file
from pdfeditor.core.forms import XfaKind
from pdfeditor.core.settings import Settings
from pdfeditor.core.signature_store import SignatureStore
from pdfeditor.i18n import LANGUAGE_NAMES, current_language
from pdfeditor.resources import app_icon, icon
from pdfeditor.ui import dialogs, page_dialogs, signature_dialogs
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.export_dialog import ExportDialog
from pdfeditor.ui.thumbnails import ThumbnailModel, ThumbnailSidebar
from pdfeditor.ui.tools.annot_tools import AnnotToolBase, SignatureTool, StampTool, TextTool
from pdfeditor.ui.tools.base import ToolManager
from pdfeditor.ui.tools.form_tool import FormTool
from pdfeditor.ui.tools.hand_tool import HandTool
from pdfeditor.ui.zoom_widget import ZoomWidget, format_zoom

log = logging.getLogger(__name__)

#: Range of the toolbar font-size spin box (pt).
FONT_SIZE_RANGE = (6, 72)


def strip_mnemonic(text: str) -> str:
    """Menu text without its "&" mnemonic marker ("&&" stays a literal "&")."""
    return "&".join(part.replace("&", "") for part in text.split("&&"))


def shortcut_text(action: QAction) -> str:
    """The action's shortcuts as the platform shows them, comma-separated ("" if none)."""
    keys = (seq.toString(QKeySequence.SequenceFormat.NativeText) for seq in action.shortcuts())
    return ", ".join(k for k in keys if k)


def menu_actions(menu: QMenu) -> list[QAction]:
    """The actions of ``menu`` and of its submenus, in menu order (separators excluded)."""
    result: list[QAction] = []
    for act in menu.actions():
        if act.isSeparator():
            continue
        if act.menu() is not None:
            result += menu_actions(act.menu())
        else:
            result.append(act)
    return result


def pdf_paths_from_urls(urls) -> list[str]:
    """Local .pdf files (case-insensitive) among dropped URLs."""
    paths = []
    for url in urls:
        if url.isLocalFile():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                paths.append(path)
    return paths


def file_exists(path: str) -> bool:
    return os.path.isfile(path)


def same_path(a: str, b: str) -> bool:
    """``a`` and ``b`` name the same file (spelling, then identity: see
    :func:`pdfeditor.core.files.same_file`)."""
    return same_file(a, b)


def restart_command(path: str | None) -> tuple[str, list[str]]:
    """Program and arguments to relaunch the application (optionally reopening ``path``)."""
    args = [] if getattr(sys, "frozen", False) else ["-m", "pdfeditor"]
    if path:
        args.append(path)
    return sys.executable, args


class MainWindow(QMainWindow):
    def __init__(
        self, settings: Settings | None = None, signature_store: SignatureStore | None = None
    ) -> None:
        super().__init__()
        self._force_close = False
        self.settings = settings if settings is not None else Settings()
        #: The user's saved signatures (injectable: tests pass a store in tmp_path).
        self.signature_store = (
            signature_store if signature_store is not None else SignatureStore(parent=self)
        )
        self.setObjectName("MainWindow")
        self.setAcceptDrops(True)
        self.setWindowIcon(app_icon())
        self.resize(1000, 800)

        self.document_view = DocumentView(self)
        self.page_view = self.document_view.page_view
        self.undo_stack = self.document_view.undo_stack
        self.setCentralWidget(self.document_view)
        self._create_thumbnails()

        self._create_actions()
        self._create_tools()
        self._create_menus()
        self._create_toolbar()
        self._create_status_bar()
        self._connect()

        mode = self.settings.zoom_mode
        if mode == ZoomMode.CUSTOM:
            self.page_view.set_zoom_percent(self.settings.zoom_percent)
        else:
            self.page_view.set_zoom_mode(mode)

        if not self._restore_window_state():
            # First run (no saved dock layout): fall back to the plain setting.
            self.thumbnails_dock.setVisible(self.settings.thumbnails_visible)
        self._update_title()
        self._update_actions()

    # -- construction ---------------------------------------------------------
    def _create_thumbnails(self) -> None:
        self.thumbnail_model = ThumbnailModel(self.page_view.service, self)
        self.thumbnails = ThumbnailSidebar(self.thumbnail_model, self)
        dock = QDockWidget(self.tr("Pages"), self)
        dock.setObjectName("thumbnails_dock")
        dock.setWidget(self.thumbnails)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.thumbnails_dock = dock

    def _action(self, text: str, shortcut=None, slot=None, name: str = "") -> QAction:
        action = QAction(text, self)
        if name:
            action.setObjectName(name)
        if shortcut is not None:
            if isinstance(shortcut, list):
                action.setShortcuts(shortcut)
            else:
                action.setShortcut(QKeySequence(shortcut))
        if slot is not None:
            action.triggered.connect(slot)
        self.addAction(action)
        return action

    def _create_actions(self) -> None:
        pv = self.page_view
        self.act_open = self._action(
            self.tr("&Open…"), QKeySequence.StandardKey.Open, self.open_dialog, "open"
        )
        self.act_save = self._action(
            self.tr("&Save"), QKeySequence.StandardKey.Save, self.save, "save"
        )
        self.act_save_as = self._action(
            self.tr("Save &As…"), QKeySequence("Ctrl+Shift+S"), self.save_as, "save_as"
        )
        self.act_export = self._action(
            self.tr("&Export Copy…"), QKeySequence("Ctrl+E"), self.export_copy, "export"
        )
        self.act_clear_recent = self._action(
            self.tr("&Clear List"), None, self.clear_recent_files, "clear_recent"
        )
        self.act_close = self._action(
            self.tr("&Close"), QKeySequence("Ctrl+W"), self.close_document, "close"
        )
        self.act_quit = self._action(self.tr("&Quit"), QKeySequence("Ctrl+Q"), self.close, "quit")
        self.act_undo = self.undo_stack.createUndoAction(self, self.tr("Undo"))
        self.act_undo.setObjectName("undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_redo = self.undo_stack.createRedoAction(self, self.tr("Redo"))
        self.act_redo.setObjectName("redo")
        self.act_redo.setShortcuts(
            [QKeySequence(QKeySequence.StandardKey.Redo), QKeySequence("Ctrl+Shift+Z")]
        )
        # Commit an open field editor first, so that Undo undoes the value just typed
        # (and the editor never shows a stale value): replace the stack's own slots.
        self.act_undo.triggered.disconnect()
        self.act_undo.triggered.connect(self.undo)
        self.act_redo.triggered.disconnect()
        self.act_redo.triggered.connect(self.redo)
        self.addAction(self.act_undo)
        self.addAction(self.act_redo)
        self.act_rotate_cw = self._action(
            self.tr("Rotate Page &Clockwise"),
            QKeySequence("Ctrl+R"),
            lambda: self.rotate_pages(None, 90),
            "rotate_cw",
        )
        self.act_rotate_ccw = self._action(
            self.tr("Rotate Page C&ounterclockwise"),
            QKeySequence("Ctrl+Shift+R"),
            lambda: self.rotate_pages(None, -90),
            "rotate_ccw",
        )
        self.act_insert_blank = self._action(
            self.tr("Insert &Blank Page"),
            QKeySequence("Ctrl+Shift+N"),
            lambda: self.insert_blank_page(),
            "insert_blank_page",
        )
        self.act_insert_pages = self._action(
            self.tr("Insert Pages from &File…"),
            QKeySequence("Ctrl+Shift+I"),
            lambda: self.insert_pages(),
            "insert_pages",
        )
        self.act_delete_pages = self._action(
            self.tr("&Delete Pages"),
            QKeySequence("Ctrl+Shift+Delete"),
            lambda: self.delete_pages(None),
            "delete_pages",
        )
        self.act_extract_pages = self._action(
            self.tr("&Extract Pages…"),
            QKeySequence("Ctrl+Shift+E"),
            lambda: self.extract_pages(None),
            "extract_pages",
        )
        self.act_split = self._action(
            self.tr("Sp&lit Document…"), None, self.split_document, "split_document"
        )
        # Plain-key tool shortcuts: text editors and spin boxes accept ShortcutOverride
        # for printable keys, so typing there never switches tools.
        self.act_hand_tool = self._action(
            self.tr("&Hand Tool"), QKeySequence("H"), None, "hand_tool"
        )
        self.act_form_tool = self._action(
            self.tr("&Form Tool"), QKeySequence("F"), None, "form_tool"
        )
        self.act_text_tool = self._action(
            self.tr("&Text Tool"), QKeySequence("T"), None, "text_tool"
        )
        self.act_text_tool.setToolTip(self.tr("Text (T)"))
        self.act_stamp_check = self._action(
            self.tr("Check Mark Stamp"), QKeySequence("1"), None, "stamp_check"
        )
        self.act_stamp_check.setToolTip(self.tr("Check mark (1)"))
        self.act_stamp_cross = self._action(
            self.tr("Cross Stamp"), QKeySequence("2"), None, "stamp_cross"
        )
        self.act_stamp_cross.setToolTip(self.tr("Cross (2)"))
        self.act_stamp_dot = self._action(
            self.tr("Dot Stamp"), QKeySequence("3"), None, "stamp_dot"
        )
        self.act_stamp_dot.setToolTip(self.tr("Dot (3)"))
        # Not registered through ToolManager.register: activating it with an empty
        # signature store first runs the import dialog (activate_signature_tool).
        self.act_signature_tool = self._action(
            self.tr("&Signature Tool"), QKeySequence("S"), None, "signature_tool"
        )
        self.act_signature_tool.setToolTip(self.tr("Signature (S)"))
        self.act_signature_tool.triggered.connect(self.activate_signature_tool)
        self.act_add_signature = self._action(
            self.tr("Add Signature…"), None, self.add_signature, "add_signature"
        )
        self.act_manage_signatures = self._action(
            self.tr("Manage Signatures…"), None, self.manage_signatures, "manage_signatures"
        )
        self.act_delete_annot = self._action(
            self.tr("&Delete Annotation"),
            [QKeySequence(QKeySequence.StandardKey.Delete), QKeySequence(Qt.Key.Key_Backspace)],
            self.delete_annotation,
            "delete_annot",
        )
        # Delete/Backspace act on the page view only (not in the thumbnails or the
        # toolbar widgets): the shortcut lives on the document view.
        self.removeAction(self.act_delete_annot)
        self.act_delete_annot.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.document_view.addAction(self.act_delete_annot)
        self.act_auto_shrink = self._action(
            self.tr("Auto-shrink Overflowing Text"), None, None, "auto_shrink_text"
        )
        self.act_auto_shrink.setCheckable(True)
        self.act_auto_shrink.setChecked(self.settings.auto_shrink_text)
        self.act_auto_shrink.toggled.connect(self._on_auto_shrink_toggled)
        self.act_zoom_in = self._action(
            self.tr("Zoom &In"),
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")],
            lambda: pv.zoom_in(),
            "zoom_in",
        )
        self.act_zoom_out = self._action(
            self.tr("Zoom &Out"), QKeySequence("Ctrl+-"), lambda: pv.zoom_out(), "zoom_out"
        )
        self.act_fit_width = self._action(
            self.tr("Fit &Width"),
            QKeySequence("Ctrl+1"),
            lambda: pv.set_zoom_mode(ZoomMode.FIT_WIDTH),
            "fit_width",
        )
        self.act_fit_page = self._action(
            self.tr("Fit &Page"),
            QKeySequence("Ctrl+2"),
            lambda: pv.set_zoom_mode(ZoomMode.FIT_PAGE),
            "fit_page",
        )
        self.act_actual_size = self._action(
            self.tr("&Actual Size"),
            QKeySequence("Ctrl+0"),
            lambda: pv.set_zoom_percent(100),
            "actual_size",
        )
        self.act_prev_page = self._action(
            self.tr("&Previous Page"), None, pv.previous_page, "prev_page"
        )
        self.act_next_page = self._action(self.tr("&Next Page"), None, pv.next_page, "next_page")
        self.act_thumbnails = self.thumbnails_dock.toggleViewAction()
        self.act_thumbnails.setText(self.tr("&Thumbnails"))
        self.act_thumbnails.setObjectName("toggle_thumbnails")
        self.act_thumbnails.setShortcut(QKeySequence("F4"))
        # toggled (not triggered): also fires when the dock's own X button closes it.
        self.act_thumbnails.toggled.connect(self._on_thumbnails_toggled)
        self.addAction(self.act_thumbnails)
        self.act_highlight_fields = self._action(
            self.tr("Highlight Form &Fields"), None, None, "highlight_fields"
        )
        self.act_highlight_fields.setCheckable(True)
        self.act_highlight_fields.setChecked(self.settings.highlight_fields)
        self.document_view.field_layer.set_visible(self.settings.highlight_fields)
        self.act_highlight_fields.toggled.connect(self._on_highlight_fields_toggled)
        self.act_about = self._action(self.tr("&About PDF Editor…"), None, self.show_about, "about")
        self.act_about.setMenuRole(QAction.MenuRole.AboutRole)
        self.act_shortcuts = self._action(
            self.tr("&Keyboard Shortcuts…"), QKeySequence("F1"), self.show_shortcuts, "shortcuts"
        )
        self.act_third_party = self._action(
            self.tr("&Third-Party Licenses…"), None, self.show_third_party_licenses, "third_party"
        )
        self.act_register_assoc = self._action(
            self.tr("Register with Windows (Open with)…"),
            None,
            self.register_file_assoc,
            "register_assoc",
        )
        self.act_unregister_assoc = self._action(
            self.tr("Unregister from Windows"), None, self.unregister_file_assoc, "unregister_assoc"
        )
        # Real state is read when the Settings menu opens (no registry access at startup).
        supported = file_assoc.is_supported()
        self.act_register_assoc.setEnabled(supported)
        self.act_unregister_assoc.setEnabled(supported)
        self.act_rotate_cw.setIconText(self.tr("Rotate"))
        for act, name in (
            (self.act_open, "open"),
            (self.act_save, "save"),
            (self.act_undo, "undo"),
            (self.act_redo, "redo"),
            (self.act_rotate_cw, "rotate_cw"),
            (self.act_rotate_ccw, "rotate_ccw"),
            (self.act_prev_page, "prev"),
            (self.act_next_page, "next"),
            (self.act_zoom_in, "zoom_in"),
            (self.act_zoom_out, "zoom_out"),
            (self.act_hand_tool, "hand"),
            (self.act_form_tool, "form"),
            (self.act_text_tool, "text"),
            (self.act_stamp_check, "stamp_check"),
            (self.act_stamp_cross, "stamp_cross"),
            (self.act_stamp_dot, "stamp_dot"),
            (self.act_signature_tool, "signature"),
            (self.act_thumbnails, "thumbnails"),
        ):
            act.setIcon(icon(name))

    def _create_tools(self) -> None:
        self.tool_manager = ToolManager(self.page_view, self)
        self.tool_manager.register(HandTool(self), self.act_hand_tool)
        self.form_tool = FormTool(self.document_view, self.settings, self)
        self.form_tool.message.connect(self._show_message)
        self.tool_manager.register(self.form_tool, self.act_form_tool)
        self.text_tool = TextTool(self.document_view, self.settings, self)
        self.stamp_tools = {
            stamp: StampTool(self.document_view, self.settings, stamp, self)
            for stamp in ("check", "cross", "dot")
        }
        self.annot_tools: list[AnnotToolBase] = [self.text_tool, *self.stamp_tools.values()]
        for tool, action in zip(
            self.annot_tools,
            (self.act_text_tool, self.act_stamp_check, self.act_stamp_cross, self.act_stamp_dot),
            strict=True,
        ):
            tool.message.connect(self._show_message)
            self.tool_manager.register(tool, action)
        self.signature_tool = SignatureTool(
            self.document_view, self.settings, self.signature_store, self
        )
        self.signature_tool.message.connect(self._show_message)
        # Queued: the tool emits from inside a mouse press; run the dialog after it.
        self.signature_tool.signature_needed.connect(
            self._on_signature_needed, Qt.ConnectionType.QueuedConnection
        )
        self.annot_tools.append(self.signature_tool)
        tm = self.tool_manager
        tm.register(self.signature_tool)
        self.act_signature_tool.setCheckable(True)
        self.act_signature_tool.setData(self.signature_tool.name)
        tm.action_group.addAction(self.act_signature_tool)
        tm.actions[self.signature_tool.name] = self.act_signature_tool
        self.tool_manager.tool_changed.connect(self._update_delete_action)
        self.document_view.annot_selection.changed.connect(self._update_delete_action)
        self.document_view.annot_selection.changed.connect(self._sync_style_widgets)

    def _create_menus(self) -> None:
        bar = self.menuBar()
        self.menu_file = bar.addMenu(self.tr("&File"))
        self.menu_file.addAction(self.act_open)
        # Open Recent: rebuilt (and pruned of vanished files) each time it is shown, so
        # that building the window never touches the disk.
        self.menu_recent = QMenu(self.tr("Open &Recent"), self)
        self.menu_recent.setObjectName("recent_menu")
        self.menu_recent.setToolTipsVisible(True)  # needed for the full-path tooltips
        self.menu_recent.aboutToShow.connect(self._rebuild_recent_menu)
        self.menu_file.addMenu(self.menu_recent)
        self._populate_recent_menu(self.settings.recent_files)
        for act in (
            self.act_save,
            self.act_save_as,
            self.act_export,
            self.act_close,
        ):
            self.menu_file.addAction(act)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_quit)
        self.menu_edit = bar.addMenu(self.tr("&Edit"))
        self.menu_edit.addAction(self.act_undo)
        self.menu_edit.addAction(self.act_redo)
        self.menu_edit.addSeparator()
        self.menu_edit.addAction(self.act_hand_tool)
        self.menu_edit.addAction(self.act_form_tool)
        self.menu_edit.addSeparator()
        for act in self._annot_actions():
            self.menu_edit.addAction(act)
        self.menu_edit.addAction(self.act_signature_tool)
        self.menu_signatures = QMenu(self.tr("Signatures"), self)
        self.menu_signatures.setObjectName("signatures_menu")
        self._signature_group: QActionGroup | None = None
        self.menu_edit.addMenu(self.menu_signatures)
        self._rebuild_signatures_menu()
        self.signature_store.changed.connect(self._rebuild_signatures_menu)
        self.menu_edit.addAction(self.act_delete_annot)
        self.menu_edit.addSeparator()
        self.menu_edit.addAction(self.act_auto_shrink)
        self.menu_pages = bar.addMenu(self.tr("&Pages"))
        self.menu_pages.setObjectName("pages_menu")
        for act in self._page_menu_actions():
            if act is None:
                self.menu_pages.addSeparator()
            else:
                self.menu_pages.addAction(act)
        self.menu_view = bar.addMenu(self.tr("&View"))
        for act in (
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_fit_width,
            self.act_fit_page,
            self.act_actual_size,
        ):
            self.menu_view.addAction(act)
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.act_prev_page)
        self.menu_view.addAction(self.act_next_page)
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.act_thumbnails)
        self.menu_view.addAction(self.act_highlight_fields)
        self.menu_view.addSeparator()
        self.menu_language = self.menu_view.addMenu(self.tr("&Language"))
        self.language_group = QActionGroup(self)
        self.language_group.setExclusive(True)
        self.language_actions: dict[str, QAction] = {}
        for code, label in LANGUAGE_NAMES.items():
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(code == current_language())
            act.setObjectName(f"language_{code}")
            act.triggered.connect(lambda _checked=False, c=code: self.change_language(c))
            self.language_group.addAction(act)
            self.menu_language.addAction(act)
            self.language_actions[code] = act
        self.menu_settings = bar.addMenu(self.tr("&Settings"))
        self.menu_settings.addAction(self.act_register_assoc)
        self.menu_settings.addAction(self.act_unregister_assoc)
        self.menu_settings.aboutToShow.connect(self._update_assoc_actions)
        self.menu_help = bar.addMenu(self.tr("&Help"))
        self.menu_help.addAction(self.act_shortcuts)
        self.menu_help.addSeparator()
        self.menu_help.addAction(self.act_third_party)
        self.menu_help.addAction(self.act_about)

    def _create_toolbar(self) -> None:
        tb = self.addToolBar(self.tr("Main toolbar"))
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        self.toolbar = tb
        tb.addAction(self.act_open)
        tb.addAction(self.act_save)
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        tb.addAction(self.act_rotate_cw)
        tb.addSeparator()
        tb.addAction(self.act_hand_tool)
        tb.addAction(self.act_form_tool)
        tb.addSeparator()
        for act in self._annot_actions():
            tb.addAction(act)
        # The signature tool, with the saved signatures in its drop-down menu.
        self.signature_button = QToolButton(self)
        self.signature_button.setObjectName("signature_button")
        self.signature_button.setDefaultAction(self.act_signature_tool)
        self.signature_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.signature_button.setMenu(self.menu_signatures)
        tb.addWidget(self.signature_button)
        tb.addSeparator()
        self.font_size_spin = QSpinBox(self)
        self.font_size_spin.setObjectName("font_size_spin")
        self.font_size_spin.setRange(*FONT_SIZE_RANGE)
        self.font_size_spin.setSuffix(self.tr(" pt"))
        self.font_size_spin.setKeyboardTracking(False)
        self.font_size_spin.setToolTip(self.tr("Font size"))
        self.font_size_spin.setAccessibleName(self.tr("Font size"))
        tb.addWidget(self.font_size_spin)
        self.color_button = QToolButton(self)
        self.color_button.setObjectName("color_button")
        self.color_button.setToolTip(self.tr("Text color"))
        self.color_button.setAccessibleName(self.tr("Text color"))
        tb.addWidget(self.color_button)
        self._show_style(self.settings.annot_font_size, QColor(self.settings.annot_color))
        tb.addSeparator()
        tb.addAction(self.act_prev_page)
        self.page_spin = QSpinBox(self)
        self.page_spin.setObjectName("page_spin")
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setRange(0, 0)
        self.page_spin.setToolTip(self.tr("Current page"))
        tb.addWidget(self.page_spin)
        self.page_total_label = QLabel(" / 0 ", self)
        tb.addWidget(self.page_total_label)
        tb.addAction(self.act_next_page)
        tb.addSeparator()
        self.zoom_widget = ZoomWidget(self)
        tb.addWidget(self.zoom_widget)

    def _create_status_bar(self) -> None:
        sb = self.statusBar()
        self.status_file = QLabel(self)
        self.status_page = QLabel(self)
        self.status_zoom = QLabel(self)
        sb.addWidget(self.status_file, 1)
        sb.addPermanentWidget(self.status_page)
        sb.addPermanentWidget(self.status_zoom)

    def _connect(self) -> None:
        pv = self.page_view
        pv.current_page_changed.connect(self._on_current_page_changed)
        pv.zoom_changed.connect(self._on_zoom_changed)
        self.page_spin.valueChanged.connect(self._on_page_spin)
        self.zoom_widget.zoom_requested.connect(lambda z: pv.set_zoom_percent(z))
        self.zoom_widget.mode_requested.connect(pv.set_zoom_mode)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)
        self.thumbnails.page_requested.connect(pv.scroll_to_page)
        self.thumbnails.pages_move_requested.connect(self.move_pages)
        self.thumbnails.pages_delete_requested.connect(self.delete_pages)
        self.thumbnails.context_menu_requested.connect(self._show_page_context_menu)
        self.document_view.document_changed.connect(self._on_document_changed)
        self.document_view.path_changed.connect(self._on_path_changed)
        self.document_view.history_failed.connect(self._on_history_failed)
        self.font_size_spin.valueChanged.connect(self._on_font_size_changed)
        self.color_button.clicked.connect(self.choose_text_color)

    # -- state sync -------------------------------------------------------------
    def _update_title(self) -> None:
        name = self.document_view.file_name
        if name:
            self.setWindowTitle(self.tr("{name}[*] — {app}").format(name=name, app=APP_NAME))
        else:
            self.setWindowTitle(APP_NAME)
        self.setWindowModified(self.document_view.is_dirty)

    def _update_actions(self) -> None:
        has_doc = self.document_view.document is not None
        for act in (
            self.act_save,
            self.act_save_as,
            self.act_export,
            self.act_close,
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_fit_width,
            self.act_fit_page,
            self.act_actual_size,
            self.act_prev_page,
            self.act_next_page,
        ):
            act.setEnabled(has_doc)
        can_assemble = self._can_assemble()
        for act in self._assemble_actions():
            act.setEnabled(can_assemble)
        can_extract = self._can_extract()
        self.act_extract_pages.setEnabled(can_extract)
        self.act_split.setEnabled(can_extract)
        self.page_spin.setEnabled(has_doc)
        self.act_form_tool.setEnabled(self._can_fill_forms())
        can_annotate = self._can_annotate()
        for act in self._annot_actions():
            act.setEnabled(can_annotate)
        self.act_signature_tool.setEnabled(can_annotate)
        self._update_style_enabled()
        self._update_delete_action()
        if has_doc:
            cur = self.page_view.current_page
            self.act_prev_page.setEnabled(cur > 0)
            self.act_next_page.setEnabled(cur < self.page_view.page_count - 1)

    def _update_status(self) -> None:
        n = self.page_view.page_count
        cur = self.page_view.current_page
        self.status_file.setText(self.document_view.file_name)
        if n:
            self.status_page.setText(
                self.tr("Page {current} / {total}").format(current=cur + 1, total=n)
            )
        else:
            self.status_page.setText("")
        self.status_zoom.setText(format_zoom(self.page_view.zoom_percent))

    def _on_document_changed(self) -> None:
        self.thumbnail_model.set_document(self.document_view.document)
        n = self.page_view.page_count
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1 if n else 0, n)
        self.page_spin.setValue(self.page_view.current_page + 1 if n else 0)
        self.page_spin.blockSignals(False)
        self.page_total_label.setText(f" / {n} ")
        self.thumbnails.set_current_page(self.page_view.current_page)
        self._update_title()
        self._update_actions()
        self._update_status()
        # A fillable form opens with the form tool; anything else with the hand tool.
        self.tool_manager.set_active("form" if self._can_fill_forms() else "hand")

    def _annot_actions(self) -> tuple[QAction, ...]:
        return (self.act_text_tool, self.act_stamp_check, self.act_stamp_cross, self.act_stamp_dot)

    def _can_annotate(self) -> bool:
        doc = self.document_view.document
        return doc is not None and doc.can_annotate

    def _active_annot_tool(self) -> AnnotToolBase | None:
        tool = self.tool_manager.active_tool
        return tool if isinstance(tool, AnnotToolBase) else None

    def _update_delete_action(self, *_args: object) -> None:
        self.act_delete_annot.setEnabled(
            self._can_annotate()
            and self._active_annot_tool() is not None
            and self.document_view.annot_selection.current is not None
        )

    # -- text style widgets -----------------------------------------------------------
    def _show_style(self, font_size: float, color: QColor) -> None:
        """Show ``font_size``/``color`` in the toolbar widgets without applying them."""
        if not color.isValid():
            color = QColor(Qt.GlobalColor.black)
        self.font_size_spin.blockSignals(True)
        self.font_size_spin.setValue(round(font_size))
        self.font_size_spin.blockSignals(False)
        self._color = color
        self.color_button.setIcon(self._swatch(color))

    def _swatch(self, color: QColor) -> QIcon:
        size = self.color_button.iconSize()
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QColor(Qt.GlobalColor.gray))
        painter.setBrush(color)
        painter.drawRect(2, 2, size.width() - 5, size.height() - 5)
        painter.end()
        return QIcon(pixmap)

    @property
    def text_color(self) -> QColor:
        """The colour shown by the toolbar colour button."""
        return QColor(self._color)

    def _signature_selected(self) -> bool:
        current = self.document_view.annot_selection.current
        return current is not None and current.kind is AnnotKind.SIGNATURE

    def _update_style_enabled(self) -> None:
        """The style widgets work with a document that allows annotations, and are
        inert while a signature (which has no text style) is selected."""
        enabled = self._can_annotate() and not self._signature_selected()
        self.font_size_spin.setEnabled(enabled)
        self.color_button.setEnabled(enabled)

    def _sync_style_widgets(self) -> None:
        """Show the selected annotation's style, or the defaults without a selection
        (and for a selected signature, which has no style of its own)."""
        current = self.document_view.annot_selection.current
        font_size = self.settings.annot_font_size
        color = QColor(self.settings.annot_color)
        if current is not None and current.kind is not AnnotKind.SIGNATURE:
            color = QColor.fromRgbF(*current.color)
            if current.kind is AnnotKind.TEXT:
                font_size = current.font_size
        self._show_style(font_size, color)
        self._update_style_enabled()

    def _apply_style(self, font_size: float | None = None, color: QColor | None = None) -> None:
        rgb = (color.redF(), color.greenF(), color.blueF()) if color is not None else None
        tool = self._active_annot_tool()
        if tool is not None:
            tool.apply_style(font_size=font_size, color=rgb)
        else:
            if font_size is not None:
                self.settings.annot_font_size = float(font_size)
            if color is not None:
                self.settings.annot_color = color.name()

    def _on_font_size_changed(self, value: int) -> None:
        self._apply_style(font_size=float(value))

    def choose_text_color(self) -> None:
        """Toolbar colour button: pick the text colour (defaults and selection)."""
        color = dialogs.get_color(self, self.text_color)
        if color is None:
            return
        self._show_style(self.font_size_spin.value(), color)
        self._apply_style(color=color)

    # -- signatures ------------------------------------------------------------------
    def _rebuild_signatures_menu(self) -> None:
        """Edit ▸ Signatures (also the signature button's drop-down): the saved
        signatures (the checked one is the default), Add Signature…, Manage Signatures…."""
        menu = self.menu_signatures
        menu.clear()  # deletes the record actions (owned by the menu), not Add/Manage
        if self._signature_group is not None:
            self._signature_group.deleteLater()
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._signature_group = group
        default = self.signature_store.default_id
        for record in self.signature_store.records():
            act = QAction(record.name.replace("&", "&&"), menu)
            act.setCheckable(True)
            act.setChecked(record.id == default)
            act.setData(record.id)
            act.triggered.connect(lambda _checked=False, i=record.id: self.choose_signature(i))
            group.addAction(act)
            menu.addAction(act)
        if group.actions():
            menu.addSeparator()
        menu.addAction(self.act_add_signature)
        menu.addAction(self.act_manage_signatures)

    def signature_actions(self) -> list[QAction]:
        """The saved-signature entries of the Signatures menu, in store order."""
        return [a for a in self.menu_signatures.actions() if isinstance(a.data(), str)]

    def _signature_store_failed(self, exc: Exception) -> None:
        log.warning("signature store write failed: %s", exc)
        dialogs.warn(
            self, self.tr("Signatures"), self.tr("The signature could not be saved."), str(exc)
        )

    def choose_signature(self, sig_id: str) -> None:
        """A saved signature picked in the Signatures menu: make it the default and
        switch to the signature tool (when the document can be annotated)."""
        try:
            self.signature_store.set_default(sig_id)
        except KeyError:
            log.warning("signature %s no longer exists", sig_id)
            self._rebuild_signatures_menu()
            return
        except OSError as exc:
            self._signature_store_failed(exc)
            self._rebuild_signatures_menu()  # check the actual default again
            return
        if self._can_annotate():
            self.tool_manager.set_active(self.signature_tool.name)

    def _import_signature(self) -> bool:
        """Run the import dialog; the new signature becomes the default. True if added."""
        record = signature_dialogs.import_signature(self.signature_store, self)
        if record is None:
            return False
        try:
            self.signature_store.set_default(record.id)
        except (KeyError, OSError) as exc:
            log.warning("could not make %s the default signature: %s", record.id, exc)
        return True

    def add_signature(self) -> None:
        """Signatures ▸ Add Signature…: import one and switch to the signature tool."""
        if self._import_signature() and self._can_annotate():
            self.tool_manager.set_active(self.signature_tool.name)

    def manage_signatures(self) -> None:
        """Signatures ▸ Manage Signatures…."""
        dialog = signature_dialogs.SignatureManagerDialog(self.signature_store, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def activate_signature_tool(self) -> None:
        """Edit ▸ Signature Tool (S): with no saved signature, run the import dialog
        first; cancelling it keeps the previous tool."""
        tm = self.tool_manager
        previous = tm.active_tool
        if self._can_annotate() and (self.signature_store.records() or self._import_signature()):
            tm.set_active(self.signature_tool.name)
            return
        # The action group has already checked the signature action: give the check
        # back to the previous tool.
        action = tm.actions.get(previous.name) if previous is not None else None
        if action is not None and action is not self.act_signature_tool:
            action.setChecked(True)
        else:
            self.act_signature_tool.setChecked(previous is self.signature_tool)
        if self._can_annotate():
            self._show_message(self._signature_first_message())

    def _signature_first_message(self) -> str:
        return self.tr("Add a signature first (Signatures ▸ Add Signature…).")

    def _on_signature_needed(self) -> None:
        """A click with the signature tool while the store is empty."""
        if self.tool_manager.active_tool is not self.signature_tool:
            return
        if not self._import_signature():
            self._show_message(self._signature_first_message())

    def _can_fill_forms(self) -> bool:
        doc = self.document_view.document
        return (
            doc is not None
            and doc.is_form
            and doc.can_fill_forms
            and doc.xfa_kind is not XfaKind.DYNAMIC
        )

    def _on_path_changed(self, _path: str) -> None:
        # Save As: same content under a new name; views and caches stay as they are.
        self._update_title()
        self._update_status()

    def _sync_page_count(self) -> None:
        """Page spin box range and total (the page count changes with page operations)."""
        n = self.page_view.page_count
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1 if n else 0, n)
        self.page_spin.blockSignals(False)
        self.page_total_label.setText(f" / {n} ")

    def _on_current_page_changed(self, index: int) -> None:
        # Also emitted after every page operation (PageView), with the new page count.
        self._sync_page_count()
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)
        self.thumbnails.set_current_page(index)
        self._update_actions()
        self._update_status()

    def _on_page_spin(self, value: int) -> None:
        if value >= 1:
            self.page_view.scroll_to_page(value - 1)

    def _on_zoom_changed(self, percent: float, mode: ZoomMode) -> None:
        self.zoom_widget.set_zoom(percent, mode)
        self._update_status()
        self.settings.zoom_mode = mode
        if mode == ZoomMode.CUSTOM:
            self.settings.zoom_percent = percent

    def _on_thumbnails_toggled(self, visible: bool) -> None:
        self.settings.thumbnails_visible = visible

    def _on_highlight_fields_toggled(self, checked: bool) -> None:
        self.settings.highlight_fields = checked
        self.document_view.field_layer.set_visible(checked)

    def _on_auto_shrink_toggled(self, checked: bool) -> None:
        self.settings.auto_shrink_text = checked

    def _show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 10000)

    def _on_clean_changed(self, _clean: bool) -> None:
        self._update_title()

    # -- editing -------------------------------------------------------------------
    def undo(self) -> None:
        """Edit ▸ Undo: commit a pending field edit, then undo the last command (which
        is that edit when the value changed)."""
        self.document_view.undo()

    def redo(self) -> None:
        self.document_view.redo()

    def _on_history_failed(self, kind: str, error: object) -> None:
        """A command failed inside the undo stack: the history is already cleared."""
        if kind == "undo":
            title = self.tr("Undo")
            text = self.tr(
                "The last change could not be undone. The undo history has been cleared; the document stays as it is now and is marked as modified."  # noqa: E501
            )
        elif kind == "redo":
            title = self.tr("Redo")
            text = self.tr(
                "The change could not be redone. The undo history has been cleared; the document stays as it is now and is marked as modified."  # noqa: E501
            )
        else:
            title = APP_NAME
            text = self.tr(
                "The change could not be applied. The undo history has been cleared; the document stays as it is now and is marked as modified."  # noqa: E501
            )
        self._update_title()
        self._update_actions()
        dialogs.warn(self, title, text, details=str(error))

    def delete_annotation(self) -> None:
        """Edit ▸ Delete Annotation: delete the selection of the active annotation tool."""
        tool = self._active_annot_tool()
        if tool is not None:
            tool.delete_selection()

    def rotate_current_page(self, delta: int) -> None:
        """Rotate the current page (kept for callers predating M6: see rotate_pages)."""
        page = self.page_view.current_page
        if page >= 0:
            self.rotate_pages([page], delta)

    # -- pages ------------------------------------------------------------------------
    def _page_menu_actions(self) -> list[QAction | None]:
        """Pages menu order; None = separator."""
        return [
            self.act_insert_blank,
            self.act_insert_pages,
            self.act_delete_pages,
            None,
            self.act_rotate_cw,
            self.act_rotate_ccw,
            None,
            self.act_extract_pages,
            self.act_split,
        ]

    def _assemble_actions(self) -> tuple[QAction, ...]:
        return (
            self.act_insert_blank,
            self.act_insert_pages,
            self.act_delete_pages,
            self.act_rotate_cw,
            self.act_rotate_ccw,
        )

    def _can_assemble(self) -> bool:
        """Page operations (insert, delete, move, rotate) are allowed: the permissions
        allow assembling and the document is not a dynamic XFA form."""
        doc = self.document_view.document
        return doc is not None and doc.can_assemble and doc.xfa_kind is not XfaKind.DYNAMIC

    def _can_extract(self) -> bool:
        doc = self.document_view.document
        return doc is not None and doc.can_extract and doc.xfa_kind is not XfaKind.DYNAMIC

    def target_pages(self) -> list[int]:
        """Pages a Pages menu action works on: the thumbnail selection when it holds two
        or more pages, else the current page."""
        if self.document_view.document is None:
            return []
        selected = self.thumbnails.selected_pages()
        if len(selected) >= 2:
            return selected
        current = self.page_view.current_page
        return [current] if current >= 0 else []

    def _page_error_text(self, reason: str) -> str:
        if reason == "last_page":
            return self.tr("A document must keep at least one page.")
        if reason in ("permission", "xfa"):
            return self.tr(
                "Page operations are not permitted by this document’s security settings."
            )
        if reason == "snapshot":
            return self.tr("The page could not be deleted: no room for the undo copy.")
        if reason == "insert_copy":
            return self.tr("The pages could not be inserted: no room for their undo copy.")
        return self.tr("The page operation failed.")

    def _run_page_command(self, make) -> _ImmediateCommand | None:
        """Build a page command with ``make()`` (after committing pending edits, so it
        sees their result), apply it, then push it. Refusals and failures are reported
        and leave the undo stack untouched; returns the pushed command or None."""
        if self.document_view.document is None:
            return None
        self.document_view.commit_pending_edits()
        try:
            command = make()
            if isinstance(command, MovePagesCommand) and command.is_noop:
                return None
            command.apply_now()
        except PageError as exc:
            log.warning("page operation refused: %s (%s)", exc, exc.reason)
            text = self._page_error_text(exc.reason)
            if exc.reason in ("snapshot", "insert_copy", "failed"):
                dialogs.warn(self, self.tr("Pages"), text, details=str(exc))
            else:
                self._show_message(text)
            return None
        except (DocumentError, IndexError, ValueError) as exc:
            log.warning("page operation failed: %s", exc)
            dialogs.warn(self, self.tr("Pages"), self._page_error_text("failed"), details=str(exc))
            return None
        self.document_view.push(command)
        return command

    def _refuse_assemble(self) -> bool:
        """True (and a status message) when page operations are not allowed."""
        if self._can_assemble():
            return False
        if self.document_view.document is not None:
            self._show_message(self._page_error_text("permission"))
        return True

    def rotate_pages(self, rows: list[int] | None, delta: int) -> bool:
        """Pages ▸ Rotate: ``rows`` (default :meth:`target_pages`) by ``delta`` degrees."""
        doc = self.document_view.document
        if doc is None or self._refuse_assemble():
            return False
        rows = self.target_pages() if rows is None else list(rows)
        if not rows:
            return False
        return self._run_page_command(lambda: RotatePagesCommand(doc, rows, delta)) is not None

    def delete_pages(self, rows: list[int] | None = None) -> bool:
        """Pages ▸ Delete Pages (and Delete in the thumbnails): ``rows`` (default
        :meth:`target_pages`). Undoable, so no confirmation."""
        doc = self.document_view.document
        if doc is None or self._refuse_assemble():
            return False
        rows = self.target_pages() if rows is None else list(rows)
        if not rows:
            return False
        return self._run_page_command(lambda: DeletePagesCommand(doc, rows)) is not None

    def move_pages(self, rows: list[int], target: int) -> bool:
        """Thumbnail drag and drop: move ``rows`` before the page at ``target``."""
        doc = self.document_view.document
        if doc is None or not rows or self._refuse_assemble():
            return False
        return self._run_page_command(lambda: MovePagesCommand(doc, rows, target)) is not None

    def insert_blank_page(self, anchor: int | None = None) -> bool:
        """Pages ▸ Insert Blank Page: after page ``anchor`` (default the current page),
        with its size."""
        doc = self.document_view.document
        if doc is None or self._refuse_assemble():
            return False
        current = self.page_view.current_page if anchor is None else anchor
        current = min(max(0, current), doc.page_count - 1)
        size = doc.page_size(current)
        cmd = self._run_page_command(lambda: InsertBlankPageCommand(doc, current + 1, size))
        return cmd is not None

    def insert_pages(self, anchor: int | None = None) -> bool:
        """Pages ▸ Insert Pages from File…: the dialog's "before/after the current page"
        positions are relative to page ``anchor`` (default the current page)."""
        doc = self.document_view.document
        if doc is None or self._refuse_assemble():
            return False
        self.document_view.commit_pending_edits()
        current = self.page_view.current_page if anchor is None else anchor
        request = page_dialogs.insert_pages(self, doc, current, self.settings)
        if request is None:
            return False
        cmd = self._run_page_command(
            lambda: InsertPagesCommand(doc, request.data, request.index, request.count)
        )
        if cmd is None:
            return False
        text = self.tr("Inserted {count} pages").format(count=request.count)
        if doc.last_insert_renamed_fields:
            text += " — " + self.tr(
                "Some inserted form fields were renamed because the document already had fields with the same names."  # noqa: E501
            )
        self._show_message(text)
        return True

    def _stem(self) -> str:
        doc = self.document_view.document
        if doc is not None and doc.path:
            return os.path.splitext(os.path.basename(doc.path))[0]
        return "document"

    def _output_dir(self) -> str:
        doc = self.document_view.document
        return self.settings.last_open_dir or (
            os.path.dirname(doc.path) if doc is not None and doc.path else ""
        )

    def _check_extract(self) -> bool:
        """Extract/Split are allowed (else a status message explains why not)."""
        doc = self.document_view.document
        if doc is None:
            return False
        if not doc.can_extract:
            self._show_message(
                self.tr("Copying pages is not permitted by this document’s security settings.")
            )
            return False
        return self._can_extract()

    def _replaces_open_document(self, paths: list[str]) -> bool:
        doc = self.document_view.document
        if doc is None or doc.path is None or not any(same_path(p, doc.path) for p in paths):
            return False
        dialogs.warn(
            self,
            self.tr("Extract Pages"),
            self.tr("Choose another name: the copy cannot replace the open document."),
        )
        return True

    def _write_pages(self, write) -> bool:
        """Run ``write()`` (extract or split) with a wait cursor; report failures."""
        error: Exception | None = None
        refused = False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            write()
        except PageError as exc:
            log.warning("copying pages refused: %s", exc)
            refused = True
        except (DocumentError, ValueError) as exc:
            log.warning("writing pages failed: %s", exc)
            error = exc
        finally:
            QApplication.restoreOverrideCursor()
        if refused:
            self._show_message(
                self.tr("Copying pages is not permitted by this document’s security settings.")
            )
            return False
        if error is not None:
            dialogs.warn(
                self,
                self.tr("Pages"),
                self.tr("The pages could not be written."),
                details=str(error),
            )
            return False
        return True

    def extract_pages(self, rows: list[int] | None = None) -> bool:
        """Pages ▸ Extract Pages…: write ``rows`` (default :meth:`target_pages`) to a new
        file. The open document is unchanged."""
        doc = self.document_view.document
        if not self._check_extract():
            return False
        rows = self.target_pages() if rows is None else list(rows)
        if not rows:
            return False
        self.document_view.commit_pending_edits()  # the copy includes the value typed
        name = self.tr("{stem} - pages.pdf").format(stem=self._stem())
        path = dialogs.get_extract_path(self, os.path.join(self._output_dir(), name))
        if not path or self._replaces_open_document([path]):
            return False
        if not self._write_pages(lambda: doc.extract_pages(rows, path)):
            return False
        self.settings.last_open_dir = os.path.dirname(path)
        self._show_message(
            self.tr("Extracted {count} pages to “{name}”").format(
                count=len(rows), name=os.path.basename(path)
            )
        )
        return True

    def split_document(self) -> bool:
        """Pages ▸ Split Document…: write groups of pages to numbered files."""
        doc = self.document_view.document
        if not self._check_extract():
            return False
        self.document_view.commit_pending_edits()
        result = page_dialogs.split_document(self, doc.page_count, self._output_dir(), self._stem())
        if result is None:
            return False
        groups, paths = result
        if self._replaces_open_document(paths):
            return False
        existing = [p for p in paths if os.path.exists(p)]
        if existing and not dialogs.confirm_overwrite_files(self, existing):
            return False
        if not self._write_pages(lambda: doc.split_document(groups, paths)):
            return False
        folder = os.path.dirname(paths[0])
        self.settings.last_open_dir = folder
        self._show_message(
            self.tr("Split into {count} files in “{folder}”").format(
                count=len(paths), folder=QDir.toNativeSeparators(folder)
            )
        )
        return True

    def page_context_menu(self, rows: list[int]) -> QMenu:
        """The thumbnail context menu: the Pages menu actions applied to ``rows``."""
        menu = QMenu(self)
        menu.setObjectName("page_context_menu")
        targets = list(rows)

        def add(source: QAction, slot) -> None:
            act = menu.addAction(source.icon(), source.text())
            act.setObjectName(source.objectName())
            act.setEnabled(source.isEnabled())
            act.triggered.connect(lambda _checked=False: slot())

        # Inserts go next to the clicked page (the last page of a multi-selection).
        anchor = max(targets) if targets else None
        add(self.act_insert_blank, lambda: self.insert_blank_page(anchor))
        add(self.act_insert_pages, lambda: self.insert_pages(anchor))
        add(self.act_delete_pages, lambda: self.delete_pages(targets))
        menu.addSeparator()
        add(self.act_rotate_cw, lambda: self.rotate_pages(targets, 90))
        add(self.act_rotate_ccw, lambda: self.rotate_pages(targets, -90))
        menu.addSeparator()
        add(self.act_extract_pages, lambda: self.extract_pages(targets))
        add(self.act_split, self.split_document)
        return menu

    def _show_page_context_menu(self, rows: list[int], pos: QPoint) -> None:
        menu = self.page_context_menu(rows)
        try:
            menu.exec(pos)
        finally:
            menu.deleteLater()

    def show_about(self) -> None:
        dialogs.show_about(self)

    def show_third_party_licenses(self) -> None:
        dialogs.show_third_party_licenses(self)

    def shortcut_sections(self) -> list[dialogs.ShortcutSection]:
        """Menu actions with a shortcut, grouped by menu, then the pointer/editing keys."""
        sections: list[dialogs.ShortcutSection] = []
        for menu in (
            self.menu_file,
            self.menu_edit,
            self.menu_pages,
            self.menu_view,
            self.menu_settings,
            self.menu_help,
        ):
            rows = [
                (strip_mnemonic(act.text()), shortcut_text(act))
                for act in menu_actions(menu)
                if shortcut_text(act)
            ]
            if rows:
                sections.append((strip_mnemonic(menu.title()), rows))
        sections.append((self.tr("Pointer and editing"), dialogs.interaction_shortcuts()))
        return sections

    def show_shortcuts(self) -> None:
        dialogs.show_shortcuts(self, self.shortcut_sections())

    # -- language -------------------------------------------------------------------
    def change_language(self, code: str) -> None:
        """Persist the UI language and offer to restart to apply it."""
        self.settings.language = code
        self.settings.sync()
        for c, act in self.language_actions.items():
            act.setChecked(c == code)
        if code == current_language():
            return
        if dialogs.ask_restart(self):
            self.restart()

    def restart(self) -> bool:
        """Relaunch the application (after resolving unsaved changes) and close."""
        if not self.maybe_save():
            return False
        doc = self.document_view.document
        program, args = restart_command(doc.path if doc is not None else None)
        result = QProcess.startDetached(program, args)
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            log.error("could not restart: %s %s", program, args)
            return False
        self._force_close = True
        self.close()
        return True

    # -- file operations ------------------------------------------------------------
    def _password_prompt(self, path: str):
        name = os.path.basename(path)
        return lambda attempt: dialogs.ask_password(self, name, attempt > 0)

    def open_dialog(self) -> None:
        path = dialogs.get_open_path(self, self.settings.last_open_dir)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> bool:
        """Open ``path`` (asking to save unsaved changes first). Returns True on success."""
        if not self.maybe_save():
            return False
        path = os.path.abspath(path)
        try:
            self.document_view.open(path, self._password_prompt(path))
        except PasswordRequired:
            log.info("password prompt cancelled for %s", path)
            return False
        except OpenError as exc:
            log.warning("cannot open %s: %s", path, exc)
            dialogs.warn(
                self,
                self.tr("Cannot open file"),
                self.tr("“{path}” could not be opened as a PDF document.\n\n{error}").format(
                    path=path, error=self._open_error_text(exc)
                ),
                details=str(exc),
            )
            return False
        except Exception as exc:  # never let a bad file take the application down
            log.exception("unexpected error opening %s", path)
            dialogs.warn(
                self,
                self.tr("Cannot open file"),
                self.tr("“{path}” could not be opened as a PDF document.\n\n{error}").format(
                    path=path, error=self.tr("An unexpected error occurred.")
                ),
                details=f"{type(exc).__name__}: {exc}",
            )
            return False
        self.settings.last_open_dir = os.path.dirname(path)
        self._add_recent(path)
        doc = self.document_view.document
        if doc is not None and not doc.can_annotate:
            self.statusBar().showMessage(
                self.tr(
                    "Adding text and stamps is not permitted by this document’s security settings."
                ),
                10000,
            )
        if doc is not None and doc.was_repaired:
            self.statusBar().showMessage(
                self.tr("The file was repaired while opening; saving will rewrite it completely."),
                10000,
            )
        return True

    # -- recent files -----------------------------------------------------------
    def _add_recent(self, path: str) -> None:
        self.settings.recent_files = recent.push(self.settings.recent_files, path)

    def _rebuild_recent_menu(self) -> None:
        """Drop vanished files from the recent list, then rebuild File ▸ Open Recent."""
        paths = self.settings.recent_files
        kept = recent.prune(paths, file_exists)
        if kept != paths:
            self.settings.recent_files = kept
        self._populate_recent_menu(kept)

    def _populate_recent_menu(self, paths: list[str]) -> None:
        menu = self.menu_recent
        menu.clear()  # deletes the entries (owned by the menu), not Clear List
        if not paths:
            placeholder = QAction(self.tr("No recent files"), menu)
            placeholder.setEnabled(False)
            menu.addAction(placeholder)
            self.act_clear_recent.setEnabled(False)
            return
        for number, path in enumerate(paths, start=1):
            label = str(number)
            # "&1" … "&9", then "1&0": the mnemonic is the last digit.
            label = f"&{label}" if number < 10 else f"{label[:-1]}&{label[-1]}"
            name = os.path.basename(path).replace("&", "&&")
            act = QAction(f"{label} {name}", menu)
            native = QDir.toNativeSeparators(path)
            act.setToolTip(native)
            act.setStatusTip(native)
            act.setData(path)
            act.triggered.connect(lambda _checked=False, p=path: self.open_recent(p))
            menu.addAction(act)
        menu.addSeparator()
        self.act_clear_recent.setEnabled(True)
        menu.addAction(self.act_clear_recent)

    def recent_actions(self) -> list[QAction]:
        """The file entries of File ▸ Open Recent, most recent first."""
        return [a for a in self.menu_recent.actions() if isinstance(a.data(), str)]

    def open_recent(self, path: str) -> bool:
        """A File ▸ Open Recent entry: open it, or drop it if the file has vanished."""
        if not file_exists(path):
            log.info("recent file vanished: %s", path)
            self.settings.recent_files = recent.remove(self.settings.recent_files, path)
            self._populate_recent_menu(self.settings.recent_files)
            dialogs.warn(
                self,
                self.tr("Cannot open file"),
                self.tr("“{path}” could not be opened as a PDF document.\n\n{error}").format(
                    path=QDir.toNativeSeparators(path), error=self.tr("The file does not exist.")
                ),
            )
            return False
        return self.open_file(path)

    def clear_recent_files(self) -> None:
        self.settings.recent_files = []
        self._populate_recent_menu([])

    def _open_error_text(self, exc: OpenError) -> str:
        messages = {
            "missing": self.tr("The file does not exist."),
            "unreadable": self.tr(
                "The file could not be read. It may be locked by another program."
            ),
            "empty": self.tr("The file is empty."),
            "corrupt": self.tr("The file is damaged or is not a PDF document."),
            "no_pages": self.tr("The document has no pages."),
        }
        return messages.get(exc.reason, self.tr("An unexpected error occurred."))

    def close_document(self) -> bool:
        if not self.maybe_save():
            return False
        self.document_view.close_document()
        return True

    def save(self) -> bool:
        doc = self.document_view.document
        if doc is None:
            return False
        # Before the modified-on-disk check: the pending value is part of what is saved.
        self.document_view.commit_pending_edits()
        if doc.path is None:
            return self.save_as()
        if not file_exists(doc.path):
            dialogs.warn(
                self,
                self.tr("Save failed"),
                self.tr("The file “{name}” no longer exists. Use Save As to save a copy.").format(
                    name=os.path.basename(doc.path)
                ),
            )
            return self.save_as()
        if doc.modified_on_disk():
            answer = dialogs.ask_overwrite_modified(self, os.path.basename(doc.path))
            if answer == dialogs.SAVE_AS:
                return self.save_as()
            if answer != dialogs.OVERWRITE:
                return False
        try:
            self.document_view.save()
        except DocumentError as exc:
            log.warning("save failed: %s", exc)
            if not self._document_survived(exc):
                return False
            if dialogs.offer_save_as(self, str(exc)):
                return self.save_as()
            return False
        self.statusBar().showMessage(self.tr("Saved"), 3000)
        return True

    def _document_survived(self, exc: Exception) -> bool:
        """After a failed save: if the document was lost, close it cleanly and tell the user."""
        doc = self.document_view.document
        if doc is not None and doc.is_open:
            return True
        log.error("document lost after failed save: %s", exc)
        self.document_view.close_document()
        dialogs.warn(
            self,
            self.tr("Save failed"),
            self.tr("The document could not be saved and had to be closed."),
            details=str(exc),
        )
        return False

    def save_as(self) -> bool:
        doc = self.document_view.document
        if doc is None:
            return False
        self.document_view.commit_pending_edits()
        suggested = doc.path or os.path.join(self.settings.last_open_dir, "document.pdf")
        path = dialogs.get_save_path(self, suggested)
        if not path:
            return False
        try:
            self.document_view.save_as(path)
        except DocumentError as exc:
            log.warning("save as failed: %s", exc)
            if not self._document_survived(exc):
                return False
            dialogs.warn(
                self,
                self.tr("Save failed"),
                self.tr(
                    "The document could not be saved as “{name}”. Check that the folder exists and that you are allowed to write there."  # noqa: E501
                ).format(name=os.path.basename(path)),
                details=str(exc),
            )
            return False
        self.settings.last_open_dir = os.path.dirname(path)
        self._add_recent(path)
        self._update_title()
        return True

    def export_copy(self) -> bool:
        """File ▸ Export Copy…: write a (flattened or clean) copy of the current state.

        The open document, its path, undo history and modified state are unchanged.
        Returns True if a copy was written.
        """
        doc = self.document_view.document
        if doc is None:
            return False
        self.document_view.commit_pending_edits()  # the copy includes the value typed
        dialog = ExportDialog(doc, self.settings, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            options = dialog.options()
            dialog.save_choices()
        finally:
            dialog.deleteLater()
        stem = os.path.splitext(os.path.basename(doc.path))[0] if doc.path else "document"
        if options.flatten_forms or options.flatten_annots:
            name = self.tr("{stem} - flattened.pdf").format(stem=stem)
        else:
            name = self.tr("{stem} - copy.pdf").format(stem=stem)
        directory = self.settings.last_open_dir or (os.path.dirname(doc.path) if doc.path else "")
        path = dialogs.get_export_path(self, os.path.join(directory, name))
        if not path:
            return False
        if doc.path is not None and same_path(path, doc.path):
            dialogs.warn(
                self,
                self.tr("Export Copy"),
                self.tr("Choose another name: the copy cannot replace the open document."),
            )
            return False
        error: Exception | None = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            doc.export_copy(path, options)
        except (SaveError, ValueError) as exc:
            log.warning("export to %s failed: %s", path, exc)
            error = exc
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            dialogs.warn(
                self,
                self.tr("Export failed"),
                self.tr(
                    "The copy could not be written as “{name}”. Check that the folder exists and that you are allowed to write there."  # noqa: E501
                ).format(name=os.path.basename(path)),
                details=str(error),
            )
            return False
        self.statusBar().showMessage(
            self.tr("Exported to “{name}”").format(name=os.path.basename(path)), 10000
        )
        return True

    def _update_assoc_actions(self) -> None:
        """Register: when not registered or registered with another command (app moved).
        Unregister: when registered. Both disabled outside Windows."""
        if not file_assoc.is_supported():
            self.act_register_assoc.setEnabled(False)
            self.act_unregister_assoc.setEnabled(False)
            return
        registered: str | None = None
        try:
            registered = file_assoc.registered_command()
        except OSError as exc:
            log.warning("cannot read the file association: %s", exc)
        current = file_assoc.command_line(file_assoc.app_command())
        self.act_register_assoc.setEnabled(registered is None or registered != current)
        self.act_unregister_assoc.setEnabled(registered is not None)

    def register_file_assoc(self) -> bool:
        """Settings ▸ Register with Windows (Open with)…, after confirmation."""
        command = file_assoc.command_line(file_assoc.app_command())
        if not dialogs.confirm_register(self, command):
            return False
        return self._change_file_assoc(file_assoc.register, self.tr("Registered with Windows"))

    def unregister_file_assoc(self) -> bool:
        """Settings ▸ Unregister from Windows."""
        return self._change_file_assoc(file_assoc.unregister, self.tr("Removed from Windows"))

    def _change_file_assoc(self, change, done: str) -> bool:
        try:
            change()
        except OSError as exc:
            log.warning("file association change failed: %s", exc)
            dialogs.warn(
                self,
                self.tr("Settings"),
                self.tr("The registration could not be changed."),
                details=str(exc),
            )
            return False
        finally:
            self._update_assoc_actions()
        self.statusBar().showMessage(done, 10000)
        return True

    def maybe_save(self) -> bool:
        """If there are unsaved changes ask Save/Discard/Cancel. True = go ahead."""
        self.document_view.commit_pending_edits()  # an open field editor counts
        if not self.document_view.is_dirty:
            return True
        answer = dialogs.confirm_save_changes(self, self.document_view.file_name)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    # -- window state ------------------------------------------------------------
    def _restore_window_state(self) -> bool:
        """Restore geometry and dock/toolbar state. False if no dock state was saved."""
        geometry = self.settings.window_geometry
        if not geometry.isEmpty():
            self.restoreGeometry(geometry)
        state = self.settings.window_state
        return not state.isEmpty() and self.restoreState(state)

    def _save_window_state(self) -> None:
        self.settings.window_geometry = self.saveGeometry()
        self.settings.window_state = self.saveState()
        self.settings.sync()

    # -- events ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls() and pdf_paths_from_urls(mime.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        paths = pdf_paths_from_urls(mime.urls()) if mime.hasUrls() else []
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.open_file(paths[0])

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._force_close and not self.maybe_save():
            event.ignore()
            return
        self._save_window_state()
        self.document_view.shutdown()
        super().closeEvent(event)
