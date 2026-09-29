"""Main application window (single document)."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
)
from PySide6.QtWidgets import QDockWidget, QLabel, QMainWindow, QMessageBox, QSpinBox

from pdfeditor.constants import APP_NAME, ZoomMode
from pdfeditor.core.commands import RotatePageCommand
from pdfeditor.core.document import OpenError, PasswordRequired, SaveError
from pdfeditor.core.settings import Settings
from pdfeditor.ui import dialogs
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.thumbnails import ThumbnailModel, ThumbnailSidebar
from pdfeditor.ui.tools.base import ToolManager
from pdfeditor.ui.tools.hand_tool import HandTool
from pdfeditor.ui.zoom_widget import ZoomWidget, format_zoom

log = logging.getLogger(__name__)


def pdf_paths_from_urls(urls) -> list[str]:
    """Local .pdf files (case-insensitive) among dropped URLs."""
    paths = []
    for url in urls:
        if url.isLocalFile():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                paths.append(path)
    return paths


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else Settings()
        self.setObjectName("MainWindow")
        self.setAcceptDrops(True)
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

        self._restore_window_state()
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
        self.addAction(self.act_undo)
        self.addAction(self.act_redo)
        self.act_rotate_cw = self._action(
            self.tr("Rotate Page &Clockwise"),
            QKeySequence("Ctrl+R"),
            lambda: self.rotate_current_page(90),
            "rotate_cw",
        )
        self.act_rotate_ccw = self._action(
            self.tr("Rotate Page C&ounterclockwise"),
            QKeySequence("Ctrl+Shift+R"),
            lambda: self.rotate_current_page(-90),
            "rotate_ccw",
        )
        self.act_hand_tool = self._action(self.tr("&Hand Tool"), None, None, "hand_tool")
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
        self.act_thumbnails.triggered.connect(self._on_thumbnails_toggled)
        self.addAction(self.act_thumbnails)

    def _create_tools(self) -> None:
        self.tool_manager = ToolManager(self.page_view, self)
        self.tool_manager.register(HandTool(self), self.act_hand_tool)

    def _create_menus(self) -> None:
        bar = self.menuBar()
        self.menu_file = bar.addMenu(self.tr("&File"))
        for act in (self.act_open, self.act_save, self.act_save_as, self.act_close):
            self.menu_file.addAction(act)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_quit)
        self.menu_edit = bar.addMenu(self.tr("&Edit"))
        self.menu_edit.addAction(self.act_undo)
        self.menu_edit.addAction(self.act_redo)
        self.menu_edit.addSeparator()
        self.menu_edit.addAction(self.act_rotate_cw)
        self.menu_edit.addAction(self.act_rotate_ccw)
        self.menu_edit.addSeparator()
        self.menu_edit.addAction(self.act_hand_tool)
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
        self.menu_help = bar.addMenu(self.tr("&Help"))

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
        self.document_view.document_changed.connect(self._on_document_changed)
        self.document_view.path_changed.connect(lambda _p: self._on_document_changed())

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
            self.act_close,
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_fit_width,
            self.act_fit_page,
            self.act_actual_size,
            self.act_prev_page,
            self.act_next_page,
            self.act_rotate_cw,
            self.act_rotate_ccw,
        ):
            act.setEnabled(has_doc)
        self.page_spin.setEnabled(has_doc)
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

    def _on_current_page_changed(self, index: int) -> None:
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

    def _on_clean_changed(self, _clean: bool) -> None:
        self._update_title()

    # -- editing -------------------------------------------------------------------
    def rotate_current_page(self, delta: int) -> None:
        doc = self.document_view.document
        page = self.page_view.current_page
        if doc is None or page < 0:
            return
        self.undo_stack.push(RotatePageCommand(doc, page, delta))

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
                    path=path, error=exc
                ),
            )
            return False
        self.settings.last_open_dir = os.path.dirname(path)
        return True

    def close_document(self) -> bool:
        if not self.maybe_save():
            return False
        self.document_view.close_document()
        return True

    def save(self) -> bool:
        doc = self.document_view.document
        if doc is None:
            return False
        if doc.path is None:
            return self.save_as()
        try:
            self.document_view.save()
        except SaveError as exc:
            log.warning("save failed: %s", exc)
            if dialogs.offer_save_as(self, str(exc)):
                return self.save_as()
            return False
        self.statusBar().showMessage(self.tr("Saved"), 3000)
        return True

    def save_as(self) -> bool:
        doc = self.document_view.document
        if doc is None:
            return False
        suggested = doc.path or os.path.join(self.settings.last_open_dir, "document.pdf")
        path = dialogs.get_save_path(self, suggested)
        if not path:
            return False
        try:
            self.document_view.save_as(path)
        except SaveError as exc:
            log.warning("save as failed: %s", exc)
            dialogs.warn(
                self,
                self.tr("Save failed"),
                self.tr("The document could not be saved:\n{error}").format(error=exc),
            )
            return False
        self.settings.last_open_dir = os.path.dirname(path)
        self._update_title()
        return True

    def maybe_save(self) -> bool:
        """If there are unsaved changes ask Save/Discard/Cancel. True = go ahead."""
        if not self.document_view.is_dirty:
            return True
        answer = dialogs.confirm_save_changes(self, self.document_view.file_name)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    # -- window state ------------------------------------------------------------
    def _restore_window_state(self) -> None:
        geometry = self.settings.window_geometry
        if not geometry.isEmpty():
            self.restoreGeometry(geometry)
        state = self.settings.window_state
        if not state.isEmpty():
            self.restoreState(state)

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
        if not self.maybe_save():
            event.ignore()
            return
        self._save_window_state()
        self.document_view.shutdown()
        super().closeEvent(event)
