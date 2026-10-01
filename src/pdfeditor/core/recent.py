"""Recent files list helpers (docs/M5_PLAN.md 1.5).

Pure functions over a list of paths, most recent first. Only paths are stored, never
passwords. Paths are compared case-insensitively after normalisation (Windows).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable

#: Maximum number of entries kept in File ▸ Open Recent.
RECENT_MAX = 10


def normalise(path: str) -> str:
    """Absolute, normalised form of ``path`` (native separators)."""
    return os.path.normpath(os.path.abspath(path))


def _key(path: str) -> str:
    return os.path.normcase(normalise(path)).casefold()


def push(paths: Iterable[str], path: str, cap: int = RECENT_MAX) -> list[str]:
    """``paths`` with ``path`` moved (or added) to the front, duplicates dropped,
    at most ``cap`` entries."""
    key = _key(path)
    result = [normalise(path)]
    seen = {key}
    for p in paths:
        if not p:
            continue
        k = _key(p)
        if k not in seen:
            seen.add(k)
            result.append(p)
    return result[: max(cap, 0)]


def remove(paths: Iterable[str], path: str) -> list[str]:
    """``paths`` without any entry naming ``path``."""
    key = _key(path)
    return [p for p in paths if p and _key(p) != key]


def prune(paths: Iterable[str], exists: Callable[[str], bool] = os.path.isfile) -> list[str]:
    """``paths`` without the entries that no longer exist."""
    return [p for p in paths if p and exists(p)]
