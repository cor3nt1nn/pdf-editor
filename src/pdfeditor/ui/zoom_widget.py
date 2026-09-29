"""Editable zoom combo box: presets, Fit Width / Fit Page and free percentages."""

from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QWidget

from pdfeditor.constants import ZOOM_MAX, ZOOM_MIN, ZOOM_STEPS, ZoomMode


def format_zoom(percent: float) -> str:
    return f"{round(percent)} %"


class ZoomWidget(QComboBox):
    zoom_requested = Signal(float)
    mode_requested = Signal(object)  # ZoomMode

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumContentsLength(9)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.addItem(self.tr("Fit Width"), ZoomMode.FIT_WIDTH)
        self.addItem(self.tr("Fit Page"), ZoomMode.FIT_PAGE)
        for z in ZOOM_STEPS:
            self.addItem(format_zoom(z), float(z))
        self.setToolTip(self.tr("Zoom"))
        self.activated.connect(self._on_activated)
        self.lineEdit().returnPressed.connect(self._on_return)
        self._updating = False

    def set_zoom(self, percent: float, mode: ZoomMode) -> None:
        """Reflect the view's zoom without emitting requests."""
        self._updating = True
        try:
            self.setCurrentIndex(-1)
            self.setEditText(format_zoom(percent))
        finally:
            self._updating = False

    def _on_activated(self, index: int) -> None:
        if self._updating:
            return
        data = self.itemData(index)
        if isinstance(data, ZoomMode):
            self.mode_requested.emit(data)
        elif data is not None:
            self.zoom_requested.emit(float(data))

    def _on_return(self) -> None:
        match = re.search(r"\d+(?:[.,]\d+)?", self.currentText())
        if match:
            value = float(match.group().replace(",", "."))
            self.zoom_requested.emit(max(ZOOM_MIN, min(ZOOM_MAX, value)))
