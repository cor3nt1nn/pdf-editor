"""Signature dialogs: import an image as a signature, and manage the saved ones.

Modal helpers (file chooser, text prompt, confirmations, warnings) go through
:mod:`pdfeditor.ui.dialogs` so tests can patch them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from pdfeditor.core import image_tools
from pdfeditor.core.image_tools import PreparedImage, ProcessedImage
from pdfeditor.core.signature_store import SignatureRecord, SignatureStore
from pdfeditor.ui import dialogs

log = logging.getLogger(__name__)

# Preview area of the import dialog (pixels) and its checkerboard.
PREVIEW_SIZE = QSize(420, 180)
CHECKER_CELL = 8
CHECKER_LIGHT = QColor(255, 255, 255)
CHECKER_DARK = QColor(204, 204, 204)
# Debounce of the preview after a slider move / option change (ms).
PREVIEW_DELAY_MS = 30
# Thumbnails of the manager list (pixels).
THUMBNAIL_SIZE = QSize(120, 48)


def checkerboard(size: QSize) -> QImage:
    """An opaque checkerboard (transparency backdrop) of ``size``."""
    image = QImage(size, QImage.Format.Format_RGB32)
    image.fill(CHECKER_LIGHT)
    painter = QPainter(image)
    for y in range(0, size.height(), CHECKER_CELL):
        for x in range((y // CHECKER_CELL) % 2 * CHECKER_CELL, size.width(), 2 * CHECKER_CELL):
            painter.fillRect(x, y, CHECKER_CELL, CHECKER_CELL, CHECKER_DARK)
    painter.end()
    return image


def _fit(source: QSize, bounds: QSize) -> QRectF:
    """Largest rect of ``source``'s aspect inside ``bounds`` (no upscaling), centred."""
    scale = min(bounds.width() / source.width(), bounds.height() / source.height(), 1.0)
    w, h = source.width() * scale, source.height() * scale
    return QRectF((bounds.width() - w) / 2, (bounds.height() - h) / 2, w, h)


class SignatureImportDialog(QDialog):
    """Turn a scanned or photographed signature into a transparent image and save it
    in ``store``. On acceptance :attr:`result_record` is the new record.

    ``path`` (optional) is loaded right away, as if chosen with Browse… (tests).
    """

    def __init__(
        self, store: SignatureStore, parent: QWidget | None = None, path: str | None = None
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.result_record: SignatureRecord | None = None
        self.image_path: str | None = None
        self._source: QImage | None = None
        self._prepared: PreparedImage | None = None
        self._processed: ProcessedImage | None = None
        self._auto_name = ""
        self.preview_image = QImage()
        self.preview_target = QRectF()

        self.setWindowTitle(self.tr("Import Signature"))
        self.setAcceptDrops(True)

        self.file_label = QLabel()
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.browse_button = QPushButton(self.tr("Browse…"))
        self.browse_button.clicked.connect(self.browse)
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(self.browse_button)

        self.preview_label = QLabel()
        self.preview_label.setFixedSize(PREVIEW_SIZE)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setAccessibleName(self.tr("Preview"))

        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(0, 255)
        self.threshold_slider.setValue(128)
        self.threshold_value = QLabel("128")
        self.threshold_value.setMinimumWidth(30)
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(self.threshold_slider, 1)
        threshold_row.addWidget(self.threshold_value)

        self.even_paper_check = QCheckBox(self.tr("Even out paper"))
        self.even_paper_check.setChecked(True)
        self.crop_check = QCheckBox(self.tr("Crop to ink"))
        self.crop_check.setChecked(True)

        self.color_combo = QComboBox()
        self.color_combo.addItem(self.tr("Keep"), None)
        self.color_combo.addItem(self.tr("Black"), "black")
        self.color_combo.addItem(self.tr("Blue"), "blue")

        self.name_edit = QLineEdit()

        form = QFormLayout()
        form.addRow(self.tr("Image file"), file_row)
        form.addRow(self.tr("Preview"), self.preview_label)
        form.addRow(self.tr("Threshold"), threshold_row)
        form.addRow("", self.even_paper_check)
        form.addRow("", self.crop_check)
        form.addRow(self.tr("Ink colour"), self.color_combo)
        form.addRow(self.tr("Name"), self.name_edit)

        self.privacy_label = QLabel(
            self.tr(
                "The signature is stored only on this computer. Anyone who receives a signed PDF can copy the image from it."  # noqa: E501
            )
        )
        self.privacy_label.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.privacy_label)
        layout.addWidget(self.buttons)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(PREVIEW_DELAY_MS)
        self.preview_timer.timeout.connect(self.refresh_preview)

        self.threshold_slider.valueChanged.connect(self._threshold_changed)
        self.crop_check.toggled.connect(self.schedule_preview)
        self.color_combo.currentIndexChanged.connect(self.schedule_preview)
        self.even_paper_check.toggled.connect(self._prepare)
        self.name_edit.textChanged.connect(self._update_ok)

        self._show_preview()
        self._update_ok()
        if path:
            self.load(path)

    # -- state ---------------------------------------------------------------
    @property
    def processed(self) -> ProcessedImage | None:
        """The image as it would be saved (after the pending preview update)."""
        return self._processed

    def ok_button(self) -> QPushButton:
        return self.buttons.button(QDialogButtonBox.StandardButton.Ok)

    def _update_ok(self) -> None:
        self.ok_button().setEnabled(
            self._prepared is not None and bool(self.name_edit.text().strip())
        )

    # -- loading -------------------------------------------------------------
    def browse(self) -> None:
        directory = str(Path(self.image_path).parent) if self.image_path else ""
        path = dialogs.get_image_path(self, directory)
        if path:
            self.load(path)

    def load(self, path: str) -> bool:
        """Load ``path``; on failure warn and keep the current image."""
        image = image_tools.load_image(path)
        if image is None:
            dialogs.warn(self, self.windowTitle(), self.tr("The image could not be read."))
            return False
        self.image_path = str(path)
        self._source = image
        self.file_label.setText(Path(path).name)
        stem = Path(path).stem
        if not self.name_edit.text().strip() or self.name_edit.text() == self._auto_name:
            self.name_edit.setText(stem)
        self._auto_name = stem
        self._prepare()
        return True

    def _prepare(self) -> None:
        """(Re)compute the grey image (slow, ~70 ms) and reset the threshold to Otsu's."""
        if self._source is None:
            return
        try:
            self._prepared = image_tools.prepare(
                self._source, even_paper=self.even_paper_check.isChecked()
            )
        except ValueError:
            log.warning("could not prepare %s", self.image_path)
            self._prepared = None
            self._update_ok()
            return
        self.threshold_slider.blockSignals(True)
        self.threshold_slider.setValue(self._prepared.otsu_threshold)
        self.threshold_slider.blockSignals(False)
        self.threshold_value.setText(str(self._prepared.otsu_threshold))
        self._update_ok()
        self.refresh_preview()

    # -- preview -------------------------------------------------------------
    def _threshold_changed(self, value: int) -> None:
        self.threshold_value.setText(str(value))
        self.schedule_preview()

    def schedule_preview(self) -> None:
        if self._prepared is not None:
            self.preview_timer.start()

    def _color(self) -> tuple[int, int, int] | None:
        key = self.color_combo.currentData()
        return image_tools.INK_COLORS[key] if key else None

    def refresh_preview(self) -> None:
        """Process the image now (the debounced slot; also flushes a pending update)."""
        self.preview_timer.stop()
        if self._prepared is None:
            return
        self._processed = image_tools.process(
            self._prepared,
            threshold=self.threshold_slider.value(),
            color=self._color(),
            crop=self.crop_check.isChecked(),
        )
        self._show_preview()

    def _show_preview(self) -> None:
        image = checkerboard(PREVIEW_SIZE)
        self.preview_target = QRectF()
        if self._processed is not None:
            signature = image_tools.to_qimage(self._processed)
            self.preview_target = _fit(signature.size(), PREVIEW_SIZE)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(self.preview_target, signature)
            painter.end()
        self.preview_image = image
        self.preview_label.setPixmap(QPixmap.fromImage(image))

    # -- drag & drop -----------------------------------------------------------
    @staticmethod
    def _dropped_path(event: QDragEnterEvent | QDropEvent) -> str | None:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                return url.toLocalFile()
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._dropped_path(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        path = self._dropped_path(event)
        if path:
            event.acceptProposedAction()
            self.load(path)
        else:
            event.ignore()

    # -- result ----------------------------------------------------------------
    def accept(self) -> None:
        self.refresh_preview()
        name = self.name_edit.text().strip()
        if self._processed is None or not name:
            return
        if self._processed.ink_bbox is None:
            dialogs.warn(
                self,
                self.windowTitle(),
                self.tr("No ink was found in the image; raise the threshold."),
            )
            return
        try:
            self.result_record = self.store.add(name, image_tools.to_qimage(self._processed))
        except (OSError, ValueError) as exc:
            log.warning("could not save the signature: %s", exc)
            dialogs.warn(
                self, self.windowTitle(), self.tr("The signature could not be saved."), str(exc)
            )
            return
        super().accept()


def import_signature(
    store: SignatureStore, parent: QWidget | None = None, path: str | None = None
) -> SignatureRecord | None:
    """Run :class:`SignatureImportDialog` modally; the new record, or None if cancelled."""
    dialog = SignatureImportDialog(store, parent, path)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.result_record
        return None
    finally:
        dialog.deleteLater()


class SignatureManagerDialog(QDialog):
    """List of saved signatures (thumbnail + name, the default one in bold) with Add…,
    Rename…, Delete, Set as Default and Close."""

    def __init__(self, store: SignatureStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(self.tr("Signatures"))

        self.list = QListWidget()
        self.list.setIconSize(THUMBNAIL_SIZE)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.currentItemChanged.connect(self._update_buttons)

        self.add_button = QPushButton(self.tr("Add…"))
        self.rename_button = QPushButton(self.tr("Rename…"))
        self.delete_button = QPushButton(self.tr("Delete"))
        self.default_button = QPushButton(self.tr("Set as Default"))
        self.add_button.clicked.connect(self.add_signature)
        self.rename_button.clicked.connect(self.rename_current)
        self.delete_button.clicked.connect(self.delete_current)
        self.default_button.clicked.connect(self.set_current_default)

        side = QVBoxLayout()
        for button in (self.add_button, self.rename_button, self.delete_button):
            side.addWidget(button)
        side.addWidget(self.default_button)
        side.addStretch(1)

        body = QHBoxLayout()
        body.addWidget(self.list, 1)
        body.addLayout(side)

        self.buttons = QDialogButtonBox()
        close = self.buttons.addButton(self.tr("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(self.buttons)
        self.resize(460, 320)

        store.changed.connect(self.rebuild)
        self.rebuild()

    # -- list ------------------------------------------------------------------
    def _thumbnail(self, record: SignatureRecord) -> QIcon:
        image = self.store.load(record.id)
        if image is None:
            return QIcon()
        canvas = QPixmap(THUMBNAIL_SIZE)
        canvas.fill(Qt.GlobalColor.white)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(_fit(image.size(), THUMBNAIL_SIZE), image)
        painter.end()
        return QIcon(canvas)

    def rebuild(self) -> None:
        """Refill the list from the store, keeping the current signature selected."""
        current = self.current_id()
        self.list.blockSignals(True)
        self.list.clear()
        default = self.store.default_id
        select_row = 0
        for row, record in enumerate(self.store.records()):
            item = QListWidgetItem(self._thumbnail(record), record.name)
            item.setData(Qt.ItemDataRole.UserRole, record.id)
            if record.id == default:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            self.list.addItem(item)
            if record.id == current:
                select_row = row
        if self.list.count():
            self.list.setCurrentRow(select_row)
        self.list.blockSignals(False)
        self._update_buttons()

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select(self, sig_id: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == sig_id:
                self.list.setCurrentRow(row)
                return

    def _update_buttons(self, *_args: object) -> None:
        sig_id = self.current_id()
        for button in (self.rename_button, self.delete_button):
            button.setEnabled(sig_id is not None)
        self.default_button.setEnabled(sig_id is not None and sig_id != self.store.default_id)

    # -- actions -----------------------------------------------------------------
    def _store_failed(self, exc: Exception) -> None:
        log.warning("signature store update failed: %s", exc)
        dialogs.warn(
            self, self.windowTitle(), self.tr("The signature could not be saved."), str(exc)
        )

    def add_signature(self) -> None:
        record = import_signature(self.store, self)
        if record is not None:
            self.select(record.id)

    def rename_current(self) -> None:
        sig_id = self.current_id()
        record = self.store.get(sig_id) if sig_id else None
        if record is None:
            return
        name = dialogs.ask_text(
            self, self.tr("Rename Signature"), self.tr("Signature name"), record.name
        )
        if name is None or not name.strip():
            return
        try:
            self.store.rename(record.id, name)
        except OSError as exc:
            self._store_failed(exc)

    def delete_current(self) -> None:
        sig_id = self.current_id()
        record = self.store.get(sig_id) if sig_id else None
        if record is None or not dialogs.confirm_delete_signature(self, record.name):
            return
        try:
            self.store.delete(record.id)
        except OSError as exc:
            self._store_failed(exc)

    def set_current_default(self) -> None:
        sig_id = self.current_id()
        if sig_id is None or self.store.get(sig_id) is None:
            return
        try:
            self.store.set_default(sig_id)
        except OSError as exc:
            self._store_failed(exc)
