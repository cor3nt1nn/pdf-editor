"""LRU pixmap cache with a byte budget."""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtGui import QPixmap

from pdfeditor.constants import CACHE_BUDGET_BYTES
from pdfeditor.core.geometry import quantize_scale

CacheKey = tuple[int, float, str]


def _cost(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * 4


class PixmapCache:
    """``(page, quantized scale, kind) -> QPixmap``; least-recently-used entries are evicted
    once the total size (``w * h * 4`` bytes each) exceeds the budget. Kinds: "page", "thumb".
    """

    def __init__(self, budget_bytes: int = CACHE_BUDGET_BYTES) -> None:
        self.budget_bytes = budget_bytes
        self._items: OrderedDict[CacheKey, QPixmap] = OrderedDict()
        self._bytes = 0

    @staticmethod
    def key(page: int, scale: float, kind: str = "page") -> CacheKey:
        return (page, quantize_scale(scale), kind)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: CacheKey) -> bool:
        return key in self._items

    @property
    def bytes_used(self) -> int:
        return self._bytes

    def get(self, page: int, scale: float, kind: str = "page") -> QPixmap | None:
        key = self.key(page, scale, kind)
        pixmap = self._items.get(key)
        if pixmap is not None:
            self._items.move_to_end(key)
        return pixmap

    def put(self, page: int, scale: float, kind: str, pixmap: QPixmap) -> None:
        key = self.key(page, scale, kind)
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= _cost(old)
        self._items[key] = pixmap
        self._bytes += _cost(pixmap)
        self._evict()

    def best(self, page: int, kind: str = "page") -> QPixmap | None:
        """Largest cached pixmap of ``page`` at any scale (placeholder while rendering)."""
        found: QPixmap | None = None
        best_scale = -1.0
        for (p, scale, k), pixmap in self._items.items():
            if p == page and k == kind and scale > best_scale:
                found, best_scale = pixmap, scale
        return found

    def invalidate_page(self, page: int) -> None:
        for key in [k for k in self._items if k[0] == page]:
            self._bytes -= _cost(self._items.pop(key))

    def remap(self, mapping: list[int | None]) -> None:
        """Pages were added, removed or reordered: ``mapping[old] -> new`` index (None:
        the page is gone, its pixmaps are dropped). Pages missing from ``mapping`` (index
        out of range) are dropped too. LRU order is preserved."""
        items: OrderedDict[CacheKey, QPixmap] = OrderedDict()
        for (page, scale, kind), pixmap in self._items.items():
            new = mapping[page] if 0 <= page < len(mapping) else None
            if new is None:
                self._bytes -= _cost(pixmap)
            else:
                items[(new, scale, kind)] = pixmap
        self._items = items

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def _evict(self) -> None:
        # Always keep the most recent entry, even if it alone exceeds the budget.
        while self._bytes > self.budget_bytes and len(self._items) > 1:
            _, pixmap = self._items.popitem(last=False)
            self._bytes -= _cost(pixmap)
