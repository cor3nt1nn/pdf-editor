"""Hand tool: panning is the PageView's default ScrollHandDrag behaviour."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

from pdfeditor.ui.tools.base import Tool


class HandTool(Tool):
    name = "hand"

    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.OpenHandCursor)
