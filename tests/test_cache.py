from __future__ import annotations

from PySide6.QtGui import QPixmap

from pdfeditor.render.cache import PixmapCache


def _pm(w: int, h: int) -> QPixmap:
    return QPixmap(w, h)


def test_put_get_and_quantized_key(qapp) -> None:
    cache = PixmapCache()
    cache.put(0, 1.0, "page", _pm(10, 10))
    assert cache.get(0, 1.0) is not None
    assert cache.get(0, 1.001) is not None  # same 1/64 bucket
    assert cache.get(0, 1.5) is None
    assert cache.get(0, 1.0, "thumb") is None
    assert cache.bytes_used == 400


def test_lru_eviction(qapp) -> None:
    cache = PixmapCache(budget_bytes=3 * 400)
    for page in range(3):
        cache.put(page, 1.0, "page", _pm(10, 10))
    assert len(cache) == 3
    cache.get(0, 1.0)  # touch page 0 -> page 1 becomes LRU
    cache.put(3, 1.0, "page", _pm(10, 10))
    assert len(cache) == 3
    assert cache.get(1, 1.0) is None
    assert cache.get(0, 1.0) is not None
    assert cache.bytes_used <= cache.budget_bytes


def test_oversized_entry_kept(qapp) -> None:
    cache = PixmapCache(budget_bytes=100)
    cache.put(0, 1.0, "page", _pm(10, 10))
    cache.put(1, 1.0, "page", _pm(20, 20))
    assert len(cache) == 1
    assert cache.get(1, 1.0) is not None


def test_replace_same_key(qapp) -> None:
    cache = PixmapCache()
    cache.put(0, 1.0, "page", _pm(10, 10))
    cache.put(0, 1.0, "page", _pm(20, 10))
    assert len(cache) == 1
    assert cache.bytes_used == 800


def test_invalidate_best_and_clear(qapp) -> None:
    cache = PixmapCache()
    cache.put(0, 1.0, "page", _pm(10, 10))
    cache.put(0, 2.0, "page", _pm(20, 20))
    cache.put(0, 0.2, "thumb", _pm(2, 2))
    cache.put(1, 1.0, "page", _pm(10, 10))
    assert cache.best(0).width() == 20
    assert cache.best(0, "thumb").width() == 2
    assert cache.best(5) is None
    cache.invalidate_page(0)
    assert len(cache) == 1
    assert cache.bytes_used == 400
    cache.clear()
    assert len(cache) == 0
    assert cache.bytes_used == 0
