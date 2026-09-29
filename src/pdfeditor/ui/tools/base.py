"""Tool framework: the PageView forwards mouse/key events to the active tool.

A tool returns True from a handler when it consumed the event; otherwise the view falls
back to its default behaviour (ScrollHandDrag panning). Tools never mutate the document
directly: they push QUndoCommands (see core/commands.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor, QPainter

if TYPE_CHECKING:
    from pdfeditor.ui.page_view import PageView


@dataclass
class ToolEvent:
    page_index: int | None
    page_pos: QPointF | None
    scene_pos: QPointF
    buttons: Qt.MouseButton
    modifiers: Qt.KeyboardModifier
    qt_event: QEvent


class Tool(QObject):
    name: str = "tool"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.view: PageView | None = None

    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.ArrowCursor)

    def activate(self, view: PageView) -> None:
        self.view = view
        view.viewport().setCursor(self.cursor)

    def deactivate(self) -> None:
        if self.view is not None:
            self.view.viewport().unsetCursor()
        self.view = None

    def mouse_press(self, event: ToolEvent) -> bool:
        return False

    def mouse_move(self, event: ToolEvent) -> bool:
        return False

    def mouse_release(self, event: ToolEvent) -> bool:
        return False

    def key_press(self, event: ToolEvent) -> bool:
        return False

    def paint_overlay(self, painter: QPainter) -> None:
        """Paint in scene coordinates above the pages (called from drawForeground)."""


class ToolManager(QObject):
    tool_changed = Signal(str)

    def __init__(self, view: PageView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.view = view
        self.tools: dict[str, Tool] = {}
        self.actions: dict[str, QAction] = {}
        self.action_group = QActionGroup(self)
        self.action_group.setExclusive(True)
        self._active: Tool | None = None
        view.tool_manager = self

    @property
    def active_tool(self) -> Tool | None:
        return self._active

    def register(self, tool: Tool, action: QAction | None = None) -> None:
        self.tools[tool.name] = tool
        if action is not None:
            action.setCheckable(True)
            action.setData(tool.name)
            self.action_group.addAction(action)
            self.actions[tool.name] = action
            action.triggered.connect(lambda _checked=False, n=tool.name: self.set_active(n))
        if self._active is None:
            self.set_active(tool.name)

    def set_active(self, name: str) -> None:
        tool = self.tools[name]
        if tool is self._active:
            return
        if self._active is not None:
            self._active.deactivate()
        self._active = tool
        tool.activate(self.view)
        action = self.actions.get(name)
        if action is not None:
            action.setChecked(True)
        self.view.viewport().update()
        self.tool_changed.emit(name)
