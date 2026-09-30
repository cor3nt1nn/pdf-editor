"""Form field highlights: one FieldHitItem per editable widget, child of its PageItem."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem

from pdfeditor.core.forms import WidgetInfo

if TYPE_CHECKING:
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

FIELD_FILL = QColor(0, 120, 215, 40)
FIELD_BORDER = QColor(0, 120, 215, 120)
FIELD_Z = 1.0


class FieldHitItem(QGraphicsRectItem):
    """Translucent highlight over one editable widget, in page space (points).

    Purely visual: it accepts no mouse buttons nor hover, so clicks reach the view and
    its active tool (the form tool hit-tests ``PdfDocument.widgets`` itself).
    """

    def __init__(self, info: WidgetInfo, parent: QGraphicsItem | None = None) -> None:
        super().__init__(info.rect, parent)
        self.info = info
        # Width 0 = cosmetic 1 px line whose half-width adds nothing to boundingRect(), so
        # sceneBoundingRect() is exactly the widget rect (the view pads exposed areas).
        pen = QPen(FIELD_BORDER, 0)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QBrush(FIELD_FILL))
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(FIELD_Z)


class FieldLayer(QObject):
    """Keeps one FieldHitItem per editable widget of the document shown by ``view``.

    Owned by DocumentView (``DocumentView.field_layer``) and bound to its PageView. Items
    exist only for form documents whose permissions allow filling; ``set_visible`` hides
    or shows them without rebuilding. Rebuilt on ``structure_changed`` (PageItems are
    replaced), ``path_changed`` and ``reloaded`` (widget caches cleared); only page ``i``
    is rebuilt on ``page_changed(i)`` (rotation, field value change).
    """

    def __init__(self, view: PageView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._document: PdfDocument | None = None
        self._visible = True
        self._items: dict[int, list[FieldHitItem]] = {}

    # -- state ----------------------------------------------------------------
    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def document(self) -> PdfDocument | None:
        return self._document

    def items(self, page: int | None = None) -> list[FieldHitItem]:
        """Live highlight items of ``page`` (all pages when None), in /Annots order."""
        if page is not None:
            return list(self._items.get(page, []))
        return [item for p in sorted(self._items) for item in self._items[p]]

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        for item in self.items():
            item.setVisible(self._visible)

    # -- document binding -------------------------------------------------------
    def set_document(self, document: PdfDocument | None) -> None:
        """Follow ``document``. Call after ``PageView.set_document`` so that this layer's
        slots run after the view's (the PageItems must exist before we add children)."""
        old = self._document
        if old is not None:
            try:
                old.page_changed.disconnect(self._on_page_changed)
                old.structure_changed.disconnect(self._rebuild)
                old.path_changed.disconnect(self._rebuild)
                old.reloaded.disconnect(self._rebuild)
            except (RuntimeError, TypeError):
                pass
        self._document = document
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.structure_changed.connect(self._rebuild)
            document.path_changed.connect(self._rebuild)
            document.reloaded.connect(self._rebuild)
        self.rebuild(self._view, document)

    def _fillable(self, doc: PdfDocument | None) -> bool:
        return doc is not None and doc.page_count > 0 and doc.is_form and doc.can_fill_forms

    def rebuild(self, view: PageView, doc: PdfDocument | None) -> None:
        """Recreate every item from ``doc.widgets`` (editable widgets only)."""
        self.clear()
        self._view = view
        if not self._fillable(doc):
            return
        assert doc is not None
        for i in range(min(doc.page_count, view.page_count)):
            self._build_page(doc, i)

    def rebuild_page(self, i: int) -> None:
        doc = self._document
        self._clear_page(i)
        if self._fillable(doc) and 0 <= i < self._view.page_count:
            assert doc is not None
            self._build_page(doc, i)

    def clear(self) -> None:
        for page in list(self._items):
            self._clear_page(page)

    # -- internals -------------------------------------------------------------
    def _rebuild(self, *_args: object) -> None:
        self.rebuild(self._view, self._document)

    def _on_page_changed(self, i: int) -> None:
        self.rebuild_page(i)

    def _build_page(self, doc: PdfDocument, i: int) -> None:
        page_item = self._view.page_item(i)
        items = []
        for info in doc.widgets(i):
            if not info.editable:
                continue
            item = FieldHitItem(info, page_item)
            item.setVisible(self._visible)
            items.append(item)
        if items:
            self._items[i] = items

    def _clear_page(self, i: int) -> None:
        for item in self._items.pop(i, []):
            # Items of a replaced PageItem (structure change) died with their parent.
            if not shiboken6.isValid(item):
                continue
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)  # detaches from the PageItem; Python then frees it
            else:
                item.setParentItem(None)
