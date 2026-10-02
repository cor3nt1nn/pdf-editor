"""Text box, stamp (M3) and signature (M4) tools: place, select, move, resize, edit and
delete FreeText annotations and signature image stamps.

Both tools share ``DocumentView.annot_selection`` (the selected annotation and its
handles) and ``DocumentView.annot_editor`` (the floating text box editor). A click on
an annotation selects it; dragging it (or one of its handles) shows a dashed ghost and
pushes **one** ``EditAnnotCommand`` on release. A click on empty page space places a new
text box (the editor opens; the ``AddAnnotCommand`` is pushed when the text is
committed) or a stamp, snapped to the table cell, underline or checkbox under the
pointer (``core.snapping``; Alt disables snapping). Inside a fillable form field nothing
is created unless Alt is held (the Form tool fills fields). Signatures keep their aspect
when resized (by any of these tools). The tools never mutate the document: every change
is a command pushed through ``DocumentView.push``.

Text markups (highlight, underline, strike-out, squiggly; M6b) are selected, recoloured
and deleted by the same tools but never moved nor resized: their selection outlines the
quads without handles and they are hit by their quads, not by the union rect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QObject, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication

from pdfeditor.core import snapping
from pdfeditor.core.annotations import (
    DEFAULT_TEXT_WIDTH,
    STAMP_GLYPHS,
    AnnotInfo,
    AnnotKind,
    AnnotSpec,
    Color,
    stamp_rect,
)
from pdfeditor.core.commands import AddAnnotCommand, DeleteAnnotCommand, EditAnnotCommand
from pdfeditor.core.document import DocumentError, PdfDocument
from pdfeditor.core.settings import MARKUP_COLOR_DEFAULTS
from pdfeditor.core.signature import ImageData
from pdfeditor.core.snapping import Snap, SnapKind
from pdfeditor.ui.overlays.annot_items import Handle, annot_hit
from pdfeditor.ui.overlays.floating_editor import normalize_newlines
from pdfeditor.ui.tools.base import Tool, ToolEvent

if TYPE_CHECKING:
    from pdfeditor.core.settings import Settings
    from pdfeditor.core.signature_store import SignatureRecord, SignatureStore
    from pdfeditor.ui.document_view import DocumentView
    from pdfeditor.ui.overlays.annot_editor import EditorAnchor
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

# Handle hit tolerance (viewport pixels).
HANDLE_TOLERANCE_PX = 6.0
# Extra margin (viewport pixels) around annotations when hit-testing them.
HIT_TOLERANCE_PX = 3.0
# Checkbox snapping reach (points) of the stamp tools (the text tool uses 0).
SNAP_TOLERANCE = 6.0
# Smallest side (points) a resize can produce; text boxes keep at least MIN_TEXT_WIDTH.
MIN_SIDE = 4.0
MIN_TEXT_WIDTH = 12.0
# Smallest width (points) of a signature placed by dragging.
MIN_SIGNATURE_WIDTH = 12.0
PREVIEW_COLOR = QColor(0, 120, 215)

_LEFT = (Handle.TOP_LEFT, Handle.LEFT, Handle.BOTTOM_LEFT)
_RIGHT = (Handle.TOP_RIGHT, Handle.RIGHT, Handle.BOTTOM_RIGHT)
_TOP = (Handle.TOP_LEFT, Handle.TOP, Handle.TOP_RIGHT)
_BOTTOM = (Handle.BOTTOM_LEFT, Handle.BOTTOM, Handle.BOTTOM_RIGHT)
_HANDLE_CURSORS = {
    Handle.TOP_LEFT: Qt.CursorShape.SizeFDiagCursor,
    Handle.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
    Handle.TOP_RIGHT: Qt.CursorShape.SizeBDiagCursor,
    Handle.BOTTOM_LEFT: Qt.CursorShape.SizeBDiagCursor,
    Handle.TOP: Qt.CursorShape.SizeVerCursor,
    Handle.BOTTOM: Qt.CursorShape.SizeVerCursor,
    Handle.LEFT: Qt.CursorShape.SizeHorCursor,
    Handle.RIGHT: Qt.CursorShape.SizeHorCursor,
}


def _update_failed() -> str:
    return QCoreApplication.translate("AnnotTools", "The annotation could not be updated.")


def form_field_message() -> str:
    return QCoreApplication.translate(
        "AnnotTools",
        "This is a form field: use the Form tool (F) to fill it. Hold Alt to place text over it anyway.",  # noqa: E501
    )


def _image_unreadable() -> str:
    return QCoreApplication.translate("AnnotTools", "The image could not be read.")


def resized_rect(
    rect: QRectF,
    handle: Handle,
    delta: QPointF,
    *,
    square: bool = False,
    aspect: float | None = None,
) -> QRectF:
    """``rect`` with the edges of ``handle`` moved by ``delta`` (no flipping; sides at
    least ``MIN_SIDE``). ``aspect`` (width / height; signatures) keeps that ratio,
    anchored on the opposite edge or corner and centred across an edge handle: a corner
    follows the dominant side, an edge scales the other side around the centre.
    ``square`` (stamps) is ``aspect=1.0``."""
    if aspect is None and square:
        aspect = 1.0
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    if handle in _LEFT:
        left = min(left + delta.x(), right - MIN_SIDE)
    if handle in _RIGHT:
        right = max(right + delta.x(), left + MIN_SIDE)
    if handle in _TOP:
        top = min(top + delta.y(), bottom - MIN_SIDE)
    if handle in _BOTTOM:
        bottom = max(bottom + delta.y(), top + MIN_SIDE)
    if aspect is None or aspect <= 0:
        return QRectF(QPointF(left, top), QPointF(right, bottom))
    if handle in (Handle.LEFT, Handle.RIGHT):
        width = right - left
    elif handle in (Handle.TOP, Handle.BOTTOM):
        width = (bottom - top) * aspect
    else:
        width = max(right - left, (bottom - top) * aspect)
    height = width / aspect
    if height < MIN_SIDE:
        height = MIN_SIDE
        width = height * aspect
    if width < MIN_SIDE:
        width = MIN_SIDE
        height = width / aspect
    if handle in _LEFT:
        left = right - width
    elif handle in _RIGHT:
        right = left + width
    else:
        left = rect.center().x() - width / 2
    if handle in _TOP:
        top = bottom - height
    elif handle in _BOTTOM:
        bottom = top + height
    else:
        top = rect.center().y() - height / 2
    return QRectF(left, top, width, height)


class _Mode(Enum):
    PENDING = auto()  # pressed on an annotation, not moved yet
    MOVE = auto()
    RESIZE = auto()


@dataclass
class _Drag:
    mode: _Mode
    info: AnnotInfo
    start: QPointF  # page space
    start_px: QPoint  # viewport
    handle: Handle | None = None
    #: The press was on the already selected (armed) annotation: a click reopens it.
    reopen: bool = False
    ghost: QRectF | None = None


class AnnotToolBase(Tool):
    """Selection, move/resize/delete of FreeText annotations and placement on click.

    Subclasses implement :meth:`create_at` (click on empty page space),
    :meth:`preview_rect` (hover preview) and may override :meth:`click_selected` (a
    second click on the selected annotation). ``message(str)`` carries translated
    status bar notices.
    """

    message = Signal(str)

    def __init__(
        self,
        document_view: DocumentView,
        settings: Settings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.document_view = document_view
        self.settings = settings
        self.selection = document_view.annot_selection
        self.editor = document_view.annot_editor
        self._drag: _Drag | None = None
        self._preview: tuple[int, QRectF] | None = None
        # Name of the annotation the user selected with a click: clicking it again
        # opens it (a selection made by placing an annotation is not armed).
        self._armed: str | None = None
        self._had_tracking = False
        document_view.document_changed.connect(self._reset)

    # -- state ----------------------------------------------------------------
    @property
    def document(self) -> PdfDocument | None:
        return self.document_view.document

    @property
    def preview(self) -> tuple[int, QRectF] | None:
        """(page, page-space rect) of the hover preview, or None."""
        return self._preview

    def activate(self, view: PageView) -> None:
        super().activate(view)
        # Hover events drive the placement preview and the cursor.
        self._had_tracking = view.viewport().hasMouseTracking()
        view.viewport().setMouseTracking(True)
        self._reset()

    def deactivate(self) -> None:
        self.editor.commit()
        self._reset()
        self.selection.clear()
        if self.view is not None:
            self.view.viewport().setMouseTracking(self._had_tracking)
        super().deactivate()

    def _reset(self) -> None:
        if self._drag is not None:
            self.selection.set_ghost(None)
        self._drag = None
        self._armed = None
        self._set_preview(None)

    def _doc(self) -> PdfDocument | None:
        doc = self.document
        return doc if doc is not None and doc.is_open else None

    def _scale(self) -> float:
        return max(self.view.view_scale, 1e-6) if self.view is not None else 1.0

    def style(self) -> tuple[float, Color]:
        """(font size, colour) of new annotations, from the settings."""
        color = QColor(self.settings.annot_color)
        if not color.isValid():
            color = QColor(Qt.GlobalColor.black)
        return self.settings.annot_font_size, (color.redF(), color.greenF(), color.blueF())

    # -- hit testing ------------------------------------------------------------
    def annot_at(self, page: int | None, pos: QPointF | None) -> AnnotInfo | None:
        """The topmost editable annotation under ``pos`` on ``page`` (a text markup only
        inside one of its quads, see :func:`annot_hit`)."""
        doc = self._doc()
        if doc is None or page is None or pos is None:
            return None
        try:
            annots = doc.annots(page)
        except (DocumentError, IndexError):
            return None
        tol = HIT_TOLERANCE_PX / self._scale()
        for info in reversed(annots):
            if info.editable and annot_hit(info, pos, tol):
                return info
        return None

    def form_field_at(self, page: int, pos: QPointF) -> bool:
        """A fillable form field is under ``pos``."""
        doc = self._doc()
        if doc is None or not doc.is_form or not doc.can_fill_forms:
            return False
        try:
            widgets = doc.widgets(page)
        except (DocumentError, IndexError):
            return False
        return any(w.editable and w.rect.contains(pos) for w in widgets)

    def _handle_at(self, page: int, pos: QPointF) -> Handle | None:
        """The resize handle of the selection under ``pos`` (see
        :meth:`AnnotHandleItem.handle_at`: inside the rect a handle only wins within a
        quarter of the rect's side, so small boxes stay movable when zoomed out)."""
        current, item = self.selection.current, self.selection.item
        if current is None or item is None or current.page != page:
            return None
        return item.handle_at(pos, HANDLE_TOLERANCE_PX / self._scale())

    def snap_at(
        self, page: int, pos: QPointF, alt: bool, *, tolerance: float = SNAP_TOLERANCE
    ) -> Snap:
        """The snapping target under ``pos`` (none with Alt)."""
        doc = self._doc()
        if alt or doc is None:
            return Snap(SnapKind.NONE)
        try:
            return snapping.snap(doc.page_shapes(page), pos, tolerance=tolerance)
        except (DocumentError, IndexError):
            return Snap(SnapKind.NONE)

    def _page_pos(self, page: int, event: ToolEvent) -> QPointF:
        """``event`` in the page space of ``page`` (even when outside that page)."""
        assert self.view is not None
        return self.view.page_item(page).mapFromScene(event.scene_pos)

    # -- mouse ------------------------------------------------------------------------
    def mouse_press(self, event: ToolEvent) -> bool:
        if _button(event) != Qt.MouseButton.LeftButton:
            return self._drag is not None  # other buttons are ignored during a drag
        doc = self._doc()
        if doc is None or self.view is None:
            return False
        # Normally done already by the editor's focus-out: its command comes first.
        self.document_view.commit_pending_edits()
        self._set_preview(None)
        page, pos = event.page_index, event.page_pos
        if page is None or pos is None:
            self.selection.clear()
            self._armed = None
            return False  # outside the pages: the view pans
        px = _viewport_pos(event)
        handle = self._handle_at(page, pos)
        current = self.selection.current
        # A markup's item has no handles; ``movable`` guards against a stale item.
        if handle is not None and current is not None and current.movable:
            self._drag = _Drag(_Mode.RESIZE, current, pos, px, handle=handle)
            return True
        info = self.annot_at(page, pos)
        if info is not None:
            reopen = (
                current is not None
                and current.page == info.page
                and current.name == info.name
                and self._armed == info.name
            )
            self._select(info)
            self._drag = _Drag(_Mode.PENDING, info, pos, px, reopen=reopen)
            return True
        self.selection.clear()
        self._armed = None
        alt = bool(event.modifiers & Qt.KeyboardModifier.AltModifier)
        if not alt and self.form_field_at(page, pos):
            self.message.emit(form_field_message())
            return True
        return self.press_empty(page, pos, alt, px)

    def press_empty(self, page: int, pos: QPointF, alt: bool, px: QPoint) -> bool:
        """A left press on empty page space (not in a form field, or with Alt)."""
        self.create_at(page, pos, alt)
        return True

    def mouse_move(self, event: ToolEvent) -> bool:
        drag = self._drag
        if drag is None:
            if event.buttons == Qt.MouseButton.NoButton:
                self._hover(event)
            return False
        if self.view is None:
            return True
        if not self._drag_alive(drag):
            self._cancel_drag()
            return True
        pos = self._page_pos(drag.info.page, event)
        if drag.mode is _Mode.PENDING:
            moved = _viewport_pos(event) - drag.start_px
            if moved.manhattanLength() < QApplication.startDragDistance():
                return True
            if not drag.info.movable:
                return True  # a text markup stays on its text (never moved, D10)
            drag.mode = _Mode.MOVE
        delta = pos - drag.start
        rect = drag.info.rect
        if drag.mode is _Mode.MOVE:
            ghost = self._clamped(drag.info.page, rect.translated(delta))
        else:
            assert drag.handle is not None
            ghost = resized_rect(
                rect,
                drag.handle,
                delta,
                square=drag.info.kind is AnnotKind.STAMP,
                aspect=_aspect(drag.info),
            )
            if drag.info.kind is AnnotKind.TEXT and ghost.width() < MIN_TEXT_WIDTH:
                if drag.handle in _LEFT:
                    ghost.setLeft(ghost.right() - MIN_TEXT_WIDTH)
                else:
                    ghost.setRight(ghost.left() + MIN_TEXT_WIDTH)
            ghost = self._resize_on_page(drag, ghost)
            if ghost is None:
                return True  # would leave the page: keep the last ghost
        drag.ghost = ghost
        self.selection.set_ghost(ghost)
        return True

    def mouse_release(self, event: ToolEvent) -> bool:
        drag = self._drag
        if drag is None:
            return False
        if _button(event) != Qt.MouseButton.LeftButton:
            return True  # e.g. a right click during a left drag
        self._drag = None
        if not self._drag_alive(drag):
            # Undone (Ctrl+Z) or removed while dragging: nothing to move, no message.
            self.selection.set_ghost(None)
            return True
        info = drag.info
        if drag.mode is _Mode.PENDING:
            if drag.reopen:
                self.click_selected(info)
            else:
                self._armed = info.name
            return True
        self.selection.set_ghost(None)
        ghost = drag.ghost
        if ghost is None or _same_rect(ghost, info.rect):
            return True
        fit = drag.mode is _Mode.RESIZE and info.kind is AnnotKind.TEXT
        if self.edit(info, rect=ghost, fit_height=fit):
            # The edit may have given a foreign annotation its lasting name.
            current = self.selection.current
            self._armed = current.name if current is not None else info.name
        return True

    def _drag_alive(self, drag: _Drag) -> bool:
        """The dragged annotation still exists and is still the selection."""
        current = self.selection.current
        if current is None or current.page != drag.info.page:
            return False
        return self.fresh(drag.info.page, drag.info.name) is not None

    def _cancel_drag(self) -> None:
        self._drag = None
        self.selection.set_ghost(None)

    def _resize_on_page(self, drag: _Drag, ghost: QRectF) -> QRectF | None:
        """``ghost`` cut to the page (text boxes), or None when it would leave the page
        (stamps and signatures keep their aspect). No limit for an annotation already off
        the page."""
        doc = self._doc()
        if doc is None:
            return ghost
        page = QRectF(QPointF(0, 0), doc.page_size(drag.info.page))
        if page.contains(ghost) or not page.contains(drag.info.rect):
            return ghost
        if drag.info.kind in (AnnotKind.STAMP, AnnotKind.SIGNATURE):
            return None
        cut = ghost.intersected(page)
        return cut if cut.width() >= MIN_SIDE and cut.height() >= MIN_SIDE else None

    def mouse_double_click(self, event: ToolEvent) -> bool:
        if _button(event) != Qt.MouseButton.LeftButton:
            return self._drag is not None
        info = self.annot_at(event.page_index, event.page_pos)
        if info is None:
            return True  # the press already placed something (or showed a notice)
        anchor = self.editor.anchor
        if anchor is not None and anchor.name == info.name:
            return True
        self._drag = None
        self._select(info)
        self.click_selected(info)
        return True

    def _hover(self, event: ToolEvent) -> None:
        page, pos = event.page_index, event.page_pos
        shape = self.cursor.shape()
        preview = None
        if page is not None and pos is not None and self._doc() is not None:
            alt = bool(event.modifiers & Qt.KeyboardModifier.AltModifier)
            handle = self._handle_at(page, pos)
            if handle is not None:
                shape = _HANDLE_CURSORS[handle]
            elif (hit := self.annot_at(page, pos)) is not None:
                # A markup is selectable, not movable.
                shape = (
                    Qt.CursorShape.SizeAllCursor
                    if hit.movable
                    else Qt.CursorShape.PointingHandCursor
                )
            elif alt or not self.form_field_at(page, pos):
                rect = self.preview_rect(page, pos, alt)
                preview = None if rect is None else (page, rect)
            else:
                shape = Qt.CursorShape.ArrowCursor
        self._set_preview(preview)
        view = self.view
        if view is not None and view.viewport().cursor().shape() != shape:
            view.viewport().setCursor(QCursor(shape))

    def _clamped(self, page: int, rect: QRectF) -> QRectF:
        """``rect`` shifted to stay on the page (when it fits)."""
        doc = self._doc()
        if doc is None:
            return rect
        size = doc.page_size(page)
        dx = dy = 0.0
        if rect.width() <= size.width():
            dx = max(0.0, -rect.left()) - max(0.0, rect.right() - size.width())
        if rect.height() <= size.height():
            dy = max(0.0, -rect.top()) - max(0.0, rect.bottom() - size.height())
        return rect.translated(dx, dy)

    # -- keyboard -------------------------------------------------------------------------
    def key_press(self, event: ToolEvent) -> bool:
        qt_event = event.qt_event
        if not isinstance(qt_event, QKeyEvent) or self.editor.is_open:
            return False
        key = qt_event.key()
        mods = qt_event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key == Qt.Key.Key_Escape and self._drag is not None:
            self._cancel_drag()
            return True
        if mods != Qt.KeyboardModifier.NoModifier or self.selection.current is None:
            return False
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selection()
            return True
        if key == Qt.Key.Key_Escape:
            self.selection.clear()
            self._armed = None
            return True
        return False

    # -- overlay -------------------------------------------------------------------------
    def _set_preview(self, preview: tuple[int, QRectF] | None) -> None:
        if preview == self._preview:
            return
        self._preview = preview
        if self.view is not None:
            self.view.viewport().update()

    def paint_overlay(self, painter: QPainter) -> None:
        preview, view = self._preview, self.view
        if preview is None or view is None or not 0 <= preview[0] < view.page_count:
            return
        page, rect = preview
        scene_rect = view.page_item(page).mapRectToScene(rect)
        pen = QPen(PREVIEW_COLOR, 0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if rect.height() <= 0:
            painter.drawLine(scene_rect.bottomLeft(), scene_rect.bottomRight())
        else:
            painter.drawRect(scene_rect)
        painter.restore()

    # -- commands ------------------------------------------------------------------------
    def run(self, command: AddAnnotCommand | EditAnnotCommand | DeleteAnnotCommand) -> bool:
        """Apply ``command`` now (reporting a failure without touching the stack), then
        push it."""
        try:
            command.apply_now()
        except (DocumentError, IndexError) as exc:
            log.warning("annotation command failed: %s", exc)
            self.message.emit(_update_failed())
            return False
        self.document_view.push(command)
        return True

    def fresh(self, page: int, name: str) -> AnnotInfo | None:
        doc = self._doc()
        if doc is None:
            return None
        try:
            return doc.annot(page, name)
        except (DocumentError, IndexError):
            return None

    def add(self, spec: AnnotSpec) -> AnnotInfo | None:
        """Create an annotation (one undo step) and select it."""
        doc = self._doc()
        if doc is None:
            return None
        self.document_view.commit_pending_edits()
        command = AddAnnotCommand(doc, spec)
        if not self.run(command):
            return None
        info = self.fresh(command.page, command.name)
        if info is not None and info.editable:
            self._select(info)
            self._armed = None
        return info

    def edit(self, info: AnnotInfo, **changes: object) -> bool:
        """Push an ``EditAnnotCommand`` for the current state of ``info``."""
        doc = self._doc()
        if doc is None:
            return False
        self.document_view.commit_pending_edits()
        current = self.fresh(info.page, info.name)
        if current is None:
            self.message.emit(_update_failed())
            return False
        try:
            command = EditAnnotCommand(doc, current, **changes)  # type: ignore[arg-type]
        except ValueError:
            return False
        return self.run(command)

    def delete(self, info: AnnotInfo) -> bool:
        doc = self._doc()
        if doc is None:
            return False
        self.document_view.commit_pending_edits()
        current = self.fresh(info.page, info.name)
        if current is None:
            self.message.emit(_update_failed())
            return False
        return self.run(DeleteAnnotCommand(doc, current))

    def delete_selection(self) -> bool:
        """Delete the selected annotation (Edit ▸ Delete Annotation)."""
        current = self.selection.current
        if current is None or self.editor.is_open:
            return False
        self._drag = None
        self._armed = None
        return self.delete(current)

    def apply_style(self, font_size: float | None = None, color: Color | None = None) -> None:
        """New default style; also applied to the open editor or the selection.

        A selected text markup only takes the colour, which also becomes the default
        colour of its kind (``Settings.markup_color``); the font size never applies to it
        and the text box defaults are left alone (docs/M6_PLAN.md D10)."""
        current = self.selection.current
        if self.editor.anchor is None and current is not None and current.is_markup:
            if color is not None:
                self.recolor_markup(current, color)
            return
        self.store_defaults(font_size, color)
        fs, col = self.style()
        anchor = self.editor.anchor
        if anchor is not None:
            self.editor.set_style(
                fs if font_size is not None else anchor.font_size,
                col if color is not None else anchor.color,
            )
            return
        current = self.selection.current
        if current is None or current.kind is AnnotKind.SIGNATURE:
            return  # a signature has no style
        changes: dict[str, object] = {}
        if color is not None and tuple(col) != tuple(current.color):
            changes["color"] = col
        if (
            font_size is not None
            and current.kind is AnnotKind.TEXT
            and abs(fs - current.font_size) > 1e-6
        ):
            changes["font_size"] = fs
            changes["fit_height"] = True
        if changes:
            self.edit(current, **changes)

    def store_defaults(self, font_size: float | None, color: Color | None) -> None:
        """Remember the style chosen in the toolbar for new annotations."""
        if font_size is not None:
            self.settings.annot_font_size = float(font_size)
        if color is not None:
            self.settings.annot_color = QColor.fromRgbF(*color).name()

    def recolor_markup(self, info: AnnotInfo, color: Color) -> bool:
        """Make ``color`` the default of ``info``'s markup kind (highlight, underline,
        strike-out) and push a "Change markup color" edit when it differs."""
        if info.kind.value in MARKUP_COLOR_DEFAULTS:
            self.settings.set_markup_color(info.kind.value, QColor.fromRgbF(*color).name())
        if same_color(color, info.color):
            return False
        return self.edit(info, color=tuple(color))

    def _select(self, info: AnnotInfo) -> None:
        try:
            self.selection.select(info)
        except ValueError:
            log.debug("cannot select %r: page %d not shown", info.name, info.page)

    # -- subclass hooks --------------------------------------------------------------------
    def create_at(self, page: int, pos: QPointF, alt: bool) -> None:
        raise NotImplementedError

    def preview_rect(self, page: int, pos: QPointF, alt: bool) -> QRectF | None:
        return None

    def click_selected(self, info: AnnotInfo) -> None:
        """A click on the selected annotation (or a double-click on any)."""


class TextTool(AnnotToolBase):
    """Place and edit text boxes (the floating editor; Ctrl+Enter or a click elsewhere
    commits, Escape cancels)."""

    name = "text"

    def __init__(
        self,
        document_view: DocumentView,
        settings: Settings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(document_view, settings, parent)
        self.editor.committed.connect(self._on_committed)

    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.IBeamCursor)

    def _placement(self, page: int, pos: QPointF, alt: bool) -> tuple[Snap, QRectF] | None:
        doc = self._doc()
        if doc is None:
            return None
        # No reach: a click beside a checkbox must not wrap text into it. A click in a
        # checkbox-sized box places a normal text box (one that small would wrap every
        # word).
        snap = self.snap_at(page, pos, alt, tolerance=0.0)
        if snap.kind is SnapKind.BOX:
            snap = Snap(SnapKind.NONE)
        font_size, _color = self.style()
        size = doc.page_size(page)
        rect = snapping.text_placement(
            snap, pos, font_size, DEFAULT_TEXT_WIDTH, size.width(), size.height()
        )
        return snap, rect

    def create_at(self, page: int, pos: QPointF, alt: bool) -> None:
        placement = self._placement(page, pos, alt)
        if placement is None:
            return
        font_size, color = self.style()
        self.editor.open_new(page, placement[1], font_size, color)

    def preview_rect(self, page: int, pos: QPointF, alt: bool) -> QRectF | None:
        placement = self._placement(page, pos, alt)
        if placement is None:
            return None
        snap, rect = placement
        return QRectF(snap.rect) if snap.rect is not None else rect

    def click_selected(self, info: AnnotInfo) -> None:
        if info.kind is not AnnotKind.TEXT:
            return
        current = self.fresh(info.page, info.name)
        if current is None or not current.text_editable:
            return
        self._armed = None
        self.editor.open_existing(current)

    def _on_committed(self, anchor: EditorAnchor, text: str) -> None:
        doc = self._doc()
        if doc is None or self.editor.document is not doc:
            log.info("dropping a text box edit: its document is gone")
            return
        if anchor.is_new:
            self.add(
                AnnotSpec(
                    anchor.page,
                    AnnotKind.TEXT,
                    text,
                    anchor.font_size,
                    tuple(anchor.color),
                    QRectF(anchor.rect),
                )
            )
            return
        current = self.fresh(anchor.page, anchor.name)
        if current is None:
            self.message.emit(_update_failed())
            return
        if not text.strip():
            if self.delete(current):
                self._armed = None
            return
        changes: dict[str, object] = {}
        if text != normalize_newlines(current.text):
            changes["text"] = text
        if abs(anchor.font_size - current.font_size) > 1e-6:
            changes["font_size"] = anchor.font_size
        if tuple(anchor.color) != tuple(current.color):
            changes["color"] = tuple(anchor.color)
        if not changes:
            return
        changes["fit_height"] = "text" in changes or "font_size" in changes
        if self.edit(current, **changes):
            info = self.fresh(anchor.page, anchor.name)
            if info is not None and info.editable:
                self._select(info)
                self._armed = None


class StampTool(AnnotToolBase):
    """Place ✓ / ✗ / ● stamps (centred in the checkbox or small cell under the pointer)."""

    def __init__(
        self,
        document_view: DocumentView,
        settings: Settings,
        stamp: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(document_view, settings, parent)
        self.stamp = stamp
        self.glyph = STAMP_GLYPHS[stamp]
        self.name = f"stamp_{stamp}"

    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.CrossCursor)

    def _placement(self, page: int, pos: QPointF, alt: bool) -> tuple[QPointF, float]:
        snap = self.snap_at(page, pos, alt)
        return snapping.stamp_placement(snap, pos, self.settings.stamp_size)

    def create_at(self, page: int, pos: QPointF, alt: bool) -> None:
        centre, side = self._placement(page, pos, alt)
        rect, font_size = stamp_rect(centre, side, self.glyph)
        _fs, color = self.style()
        rect = self._clamped(page, rect)  # kept on the page
        self.add(AnnotSpec(page, AnnotKind.STAMP, self.glyph, font_size, color, rect))

    def preview_rect(self, page: int, pos: QPointF, alt: bool) -> QRectF | None:
        centre, side = self._placement(page, pos, alt)
        return self._clamped(page, QRectF(centre.x() - side / 2, centre.y() - side / 2, side, side))


@dataclass
class _Place:
    """A press on empty page space with the signature tool: a click places the
    signature where it snaps, a drag sizes it from the press point."""

    page: int
    start: QPointF  # page space
    start_px: QPoint  # viewport
    alt: bool
    record_id: str
    aspect: float  # width / height
    ghost: QRectF | None = None


class SignatureTool(AnnotToolBase):
    """Place the default signature of ``store`` (an image stamp, upright on rotated
    pages): a click places it in the table cell or on the underline under the pointer
    (``snapping.signature_placement``; Alt disables snapping), a drag on empty space
    sizes it (aspect kept). ``signature_needed`` is emitted instead when the store is
    empty."""

    name = "signature"
    signature_needed = Signal()

    def __init__(
        self,
        document_view: DocumentView,
        settings: Settings,
        store: SignatureStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(document_view, settings, parent)
        self.store = store
        self._place: _Place | None = None
        # (signature id, page rotation) -> embedded samples; dropped on store changes.
        self._images: dict[tuple[str, int], ImageData] = {}
        store.changed.connect(self._images.clear)

    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.CrossCursor)

    def current_signature(self) -> SignatureRecord | None:
        """The signature placed by a click: the store's default (None when empty)."""
        default = self.store.default_id
        record = self.store.get(default) if default is not None else None
        if record is None:
            records = self.store.records()
            record = records[0] if records else None
        return record

    def _reset(self) -> None:
        self._place = None
        super()._reset()

    # -- placement ------------------------------------------------------------------
    def placement(self, page: int, pos: QPointF, alt: bool, aspect: float) -> QRectF | None:
        """Page-space rect of a signature placed by a click at ``pos``."""
        doc = self._doc()
        if doc is None:
            return None
        # No reach: a click in a cell next to a checkbox must target the cell.
        snap = self.snap_at(page, pos, alt, tolerance=0.0)
        try:
            size = doc.page_size(page)
        except (DocumentError, IndexError):
            return None
        return snapping.signature_placement(snap, pos, self.settings.signature_width, aspect, size)

    def _drag_rect(self, place: _Place, pos: QPointF) -> QRectF | None:
        """The rect of a drag from ``place.start`` to ``pos``: the dragged width (at
        least ``MIN_SIGNATURE_WIDTH``), aspect kept, kept on the page."""
        doc = self._doc()
        if doc is None:
            return None
        start = place.start
        dx = pos.x() - start.x()
        width = max(abs(dx), MIN_SIGNATURE_WIDTH)
        height = width / place.aspect
        left = start.x() if dx >= 0 else start.x() - width
        top = start.y() if pos.y() >= start.y() else start.y() - height
        try:
            size = doc.page_size(place.page)
        except (DocumentError, IndexError):
            return None
        return snapping.fit_on_page(QRectF(left, top, width, height), place.aspect, size)

    def preview_rect(self, page: int, pos: QPointF, alt: bool) -> QRectF | None:
        record = self.current_signature()
        if record is None:
            return None
        return self.placement(page, pos, alt, _record_aspect(record))

    def create_at(self, page: int, pos: QPointF, alt: bool) -> None:
        record = self.current_signature()
        if record is None:
            self.signature_needed.emit()
            return
        rect = self.placement(page, pos, alt, _record_aspect(record))
        if rect is not None:
            self.place(page, rect, record.id)

    def place(self, page: int, rect: QRectF, record_id: str) -> AnnotInfo | None:
        """Add signature ``record_id`` at page-space ``rect`` (one undo step)."""
        doc = self._doc()
        if doc is None:
            return None
        try:
            rotation = doc.page_rotation(page)
        except (DocumentError, IndexError):
            return None
        image = self._image(record_id, rotation)
        if image is None:
            self.message.emit(_image_unreadable())
            return None
        spec = AnnotSpec(
            page, AnnotKind.SIGNATURE, "", 11.0, (0.0, 0.0, 0.0), QRectF(rect), image=image
        )
        return self.add(spec)

    def _image(self, record_id: str, rotation: int) -> ImageData | None:
        """Samples of signature ``record_id`` turned for a page rotated by
        ``rotation`` (upright on screen), or None when it cannot be loaded."""
        key = (record_id, rotation % 360)
        image = self._images.get(key)
        if image is not None:
            return image
        if self.store.get(record_id) is None:
            return None
        qimage = self.store.load(record_id)
        if qimage is None or qimage.isNull():
            return None
        try:
            image = ImageData.from_qimage(qimage, rotation)
        except ValueError as exc:
            log.warning("signature %s unusable: %s", record_id, exc)
            return None
        self._images[key] = image
        return image

    # -- mouse ------------------------------------------------------------------------
    def press_empty(self, page: int, pos: QPointF, alt: bool, px: QPoint) -> bool:
        record = self.current_signature()
        if record is None:
            self.signature_needed.emit()
            return True
        self._place = _Place(page, pos, px, alt, record.id, _record_aspect(record))
        return True

    def mouse_press(self, event: ToolEvent) -> bool:
        if self._place is not None:
            return True  # another button during a placement drag
        return super().mouse_press(event)

    def mouse_move(self, event: ToolEvent) -> bool:
        place = self._place
        if place is None:
            return super().mouse_move(event)
        if self.view is None:
            return True
        if place.ghost is None:
            moved = _viewport_pos(event) - place.start_px
            if moved.manhattanLength() < QApplication.startDragDistance():
                return True
        ghost = self._drag_rect(place, self._page_pos(place.page, event))
        if ghost is not None:
            place.ghost = ghost
            self._set_preview((place.page, ghost))
        return True

    def mouse_release(self, event: ToolEvent) -> bool:
        place = self._place
        if place is None:
            return super().mouse_release(event)
        if _button(event) != Qt.MouseButton.LeftButton:
            return True
        self._place = None
        self._set_preview(None)
        rect = place.ghost
        if rect is None:
            rect = self.placement(place.page, place.start, place.alt, place.aspect)
        if rect is not None:
            self.place(place.page, rect, place.record_id)
        return True

    def mouse_double_click(self, event: ToolEvent) -> bool:
        if self._place is not None:
            return True
        return super().mouse_double_click(event)

    def key_press(self, event: ToolEvent) -> bool:
        qt_event = event.qt_event
        if (
            self._place is not None
            and isinstance(qt_event, QKeyEvent)
            and qt_event.key() == Qt.Key.Key_Escape
        ):
            self._place = None
            self._set_preview(None)
            return True
        return super().key_press(event)


def _record_aspect(record: SignatureRecord) -> float:
    return record.width / record.height if record.width > 0 and record.height > 0 else 1.0


def _button(event: ToolEvent) -> Qt.MouseButton:
    """The button that changed (``QMouseEvent.button()``), not all the held ones."""
    qt_event = event.qt_event
    if isinstance(qt_event, QMouseEvent):
        return qt_event.button()
    if event.buttons & Qt.MouseButton.LeftButton:
        return Qt.MouseButton.LeftButton
    return Qt.MouseButton.NoButton


def _viewport_pos(event: ToolEvent) -> QPoint:
    qt_event = event.qt_event
    if isinstance(qt_event, QMouseEvent):
        return qt_event.position().toPoint()
    return QPoint()


def _aspect(info: AnnotInfo) -> float | None:
    """Width / height a resize of ``info`` keeps (signatures only: the page-space aspect
    of its rect, which is the image's as placed)."""
    if info.kind is not AnnotKind.SIGNATURE or info.rect.height() <= 0:
        return None
    return info.rect.width() / info.rect.height()


def same_color(a: Color, b: Color) -> bool:
    """Equal as 8-bit colours (what the colour dialog and /C round-trips keep)."""
    return all(round(x * 255) == round(y * 255) for x, y in zip(a, b, strict=True))


def _same_rect(a: QRectF, b: QRectF) -> bool:
    return all(abs(x - y) < 1e-6 for x, y in zip(a.getCoords(), b.getCoords(), strict=True))


__all__ = [
    "AnnotToolBase",
    "SignatureTool",
    "StampTool",
    "TextTool",
    "form_field_message",
    "resized_rect",
    "same_color",
]
