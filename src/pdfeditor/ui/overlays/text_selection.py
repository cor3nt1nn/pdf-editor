"""The live text selection of the document view (M6b): a range of characters of one
page, painted as translucent quads, copied by Edit ▸ Copy Text and turned into a text
markup by the highlight/underline/strike-through tools.

``TextSelection`` holds the page by its ``PageId`` (docs/M6_PLAN.md D4) and the two ends
of the range as flat character indexes of ``PdfDocument.page_text(page)`` (inclusive,
either order). It follows its page across page operations (``pages_remapped`` /
``structure_changed``: dropped when the page is deleted) and checks itself whenever the
page's text may have changed (``page_changed`` of its page, ``reloaded``): a range that no
longer fits the page's characters, or whose text changed, is cleared. Annotation edits
never change the page text (``page_text`` ignores annotations), so a selection survives
them, as it survives any change to another page.

Painting never extracts page text (which takes the document lock): the quads are cached
with the ``PageText`` they were computed on, and when the page's text is not cached
(``PdfDocument.cached_page_text``) the paint is skipped and the text fetched right after
it, followed by a repaint.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter

from pdfeditor.core.pagetext import CharRef, PageText, Quad

if TYPE_CHECKING:
    from pdfeditor.core.document import PageId, PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

#: Fill of the selected characters: the M3 selection blue, translucent (addendum §3).
SELECTION_FILL = QColor(0, 120, 215, 80)


class TextSelection(QObject):
    """A range of characters of one page of the document shown by ``view``.

    ``set_document`` must be called after ``PageView.set_document``. ``changed()`` is
    emitted whenever the range (or its page) changes, including when it is cleared.
    """

    changed = Signal()

    def __init__(self, view: PageView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._document: PdfDocument | None = None
        self._page: int | None = None
        self._page_id: PageId | None = None
        self._anchor: int | None = None
        self._focus: int | None = None
        # The page text the range was made on: when the page's text is extracted again
        # (page_changed, reloaded, structure_changed) the range must still cover the
        # same characters (M7 edits, a reload of another revision), else it is cleared.
        self._made_on: PageText | None = None
        # (page text, anchor, focus, quads) of the last quads() computation.
        self._quads_cache: tuple[PageText, int, int, list[Quad]] | None = None
        self._refresh_pending = False
        self.changed.connect(self._repaint)

    # -- state -------------------------------------------------------------------------
    @property
    def document(self) -> PdfDocument | None:
        return self._document

    @property
    def page(self) -> int | None:
        """Index of the selection's page (None when nothing is selected)."""
        return self._page

    @property
    def page_id(self) -> PageId | None:
        return self._page_id

    @property
    def anchor(self) -> CharRef | None:
        """The end of the range where the selection started."""
        return self._ref(self._anchor)

    @property
    def focus(self) -> CharRef | None:
        """The moving end of the range."""
        return self._ref(self._focus)

    @property
    def is_empty(self) -> bool:
        return self._page is None

    def page_text(self) -> PageText | None:
        """The selection page's text (None without a selection)."""
        doc = self._document
        if doc is None or self._page is None or not doc.is_open:
            return None
        try:
            return doc.page_text(self._page)
        except (IndexError, ValueError) as exc:  # pragma: no cover - defensive
            log.debug("no page text for the selection: %s", exc)
            return None

    def refs(self) -> list[CharRef]:
        """The selected characters in content order."""
        pt = self.page_text()
        if pt is None or self._anchor is None or self._focus is None:
            return []
        return pt.chars_between(self._anchor, self._focus)

    def quads(self) -> list[Quad]:
        """One page-space quad per selected line part."""
        pt = self.page_text()
        if pt is None:
            return []
        return list(self._quads_of(pt))

    def _quads_of(self, pt: PageText) -> list[Quad]:
        """The selection's quads on ``pt``, cached until the range or the text changes."""
        a, f = self._anchor, self._focus
        if a is None or f is None:
            return []
        cache = self._quads_cache
        if cache is not None and cache[0] is pt and cache[1] == a and cache[2] == f:
            return cache[3]
        # Fake bold copies are left out, unless the range holds nothing else.
        quads = pt.range_quads(a, f, dedupe=True) or pt.range_quads(a, f)
        self._quads_cache = (pt, a, f, quads)
        return quads

    def text(self) -> str:
        """The selected text (``\\n`` between lines; invisible OCR text included;
        fake bold copies once, :attr:`PageText.duplicates`)."""
        pt = self.page_text()
        if pt is None or self._anchor is None or self._focus is None:
            return ""
        refs = pt.chars_between(self._anchor, self._focus)
        return pt.text_of(refs, dedupe=True) or pt.text_of(refs)

    # -- changes -----------------------------------------------------------------------
    def set(self, page: int, anchor: CharRef | int, focus: CharRef | int) -> None:
        """Select the characters ``anchor``..``focus`` (inclusive, either order) of
        ``page``. Raises ``IndexError`` when they are not characters of that page."""
        doc = self._document
        if doc is None or not 0 <= page < doc.page_count:
            raise IndexError(page)
        a, f = _index(anchor), _index(focus)
        count = len(doc.page_text(page).chars)
        if not (0 <= a < count and 0 <= f < count):
            raise IndexError((a, f))
        if (page, a, f) == (self._page, self._anchor, self._focus):
            return
        self._page = page
        self._page_id = doc.page_id(page)
        self._anchor, self._focus = a, f
        self._made_on = doc.page_text(page)
        self.changed.emit()

    def clear(self) -> None:
        """Deselect (no-op when nothing is selected)."""
        if self._page is None:
            return
        self._page = self._page_id = self._anchor = self._focus = None
        self._made_on = None
        self._quads_cache = None
        self.changed.emit()

    # -- document binding --------------------------------------------------------------
    def set_document(self, document: PdfDocument | None) -> None:
        """Follow ``document`` (the selection is cleared)."""
        if document is self._document:
            return
        old = self._document
        if old is not None:
            for signal, slot in (
                (old.page_changed, self._on_page_changed),
                (old.pages_remapped, self._on_pages_remapped),
                (old.structure_changed, self._on_structure_changed),
                (old.reloaded, self._check),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.clear()
        self._document = document
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.pages_remapped.connect(self._on_pages_remapped)
            document.structure_changed.connect(self._on_structure_changed)
            document.reloaded.connect(self._check)

    def _on_page_changed(self, page: int) -> None:
        if page == self._page:
            self._check()  # a rotation moves the quads: repaint (or clear)
            self.changed.emit()

    def _on_pages_remapped(self, mapping: object) -> None:
        """Follow the page to its new index; drop the selection when it was deleted."""
        if self._page is None:
            return
        old_to_new = list(mapping)  # type: ignore[call-overload]
        new = old_to_new[self._page] if 0 <= self._page < len(old_to_new) else None
        if new is None:
            self.clear()
            return
        self._page = new

    def _on_structure_changed(self) -> None:
        """Re-resolve the page by id (the undo of a deletion reinstalls the ids)."""
        doc = self._document
        if self._page is None or doc is None:
            return
        page = doc.page_index(self._page_id) if self._page_id is not None else None
        if page is None:
            self.clear()
            return
        self._page = page
        self._check()
        self.changed.emit()

    def _check(self) -> None:
        """Clear the range if it no longer covers the same characters of the page."""
        if self._page is None:
            return
        pt, old = self.page_text(), self._made_on
        a, f = self._anchor, self._focus
        if pt is None or old is None or a is None or f is None:
            self.clear()
            return
        if pt is old:
            return
        count = len(pt.chars)
        if not (0 <= a < count and 0 <= f < count) or pt.text_of(
            pt.chars_between(a, f)
        ) != old.text_of(old.chars_between(a, f)):
            self.clear()
            return
        self._made_on = pt

    # -- painting ----------------------------------------------------------------------
    def paint(self, painter: QPainter) -> None:
        """Fill the selected quads (scene coordinates; called from a tool's
        ``paint_overlay``)."""
        view, page, doc = self._view, self._page, self._document
        if page is None or doc is None or not doc.is_open or not 0 <= page < view.page_count:
            return
        pt = doc.cached_page_text(page)
        if pt is None or pt is not self._made_on:
            # Not cached (or extracted anew): fetch and check it outside the paint event.
            if not self._refresh_pending:
                self._refresh_pending = True
                QTimer.singleShot(0, self._refresh)
            return
        quads = self._quads_of(pt)
        if not quads:
            return
        item = view.page_item(page)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(SELECTION_FILL)
        for quad in quads:
            painter.drawPolygon(item.mapToScene(quad.polygon()))
        painter.restore()

    def _repaint(self) -> None:
        self._view.viewport().update()

    def _refresh(self) -> None:
        """After a paint found the page text uncached: fetch it (under the lock), check
        the range against it and repaint."""
        self._refresh_pending = False
        if self._page is None:
            return
        self._check()
        if self._page is not None:
            self._repaint()

    def _ref(self, index: int | None) -> CharRef | None:
        pt = self.page_text()
        if index is None or pt is None or not 0 <= index < len(pt.chars):
            return None
        return pt.ref(index)


def _index(ref: CharRef | int) -> int:
    return ref.index if isinstance(ref, CharRef) else int(ref)


__all__ = ["SELECTION_FILL", "TextSelection"]
