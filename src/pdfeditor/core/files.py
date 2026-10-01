"""File identity helpers shared by the core and the UI (no Qt, no PyMuPDF)."""

from __future__ import annotations

import os


def same_file(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """``a`` and ``b`` name the same file.

    First by spelling (absolute, case-insensitive on Windows, any separator), then, when
    both exist, by identity (``os.path.samefile``: volume serial + file index), which
    also catches aliases such as directory junctions, symbolic links, ``\\\\?\\``
    prefixes, 8.3 short names and UNC administrative shares. Any OS error (a missing
    file, an unreachable share) means "not the same file".
    """
    a, b = os.fspath(a), os.fspath(b)
    try:
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            return True
    except ValueError:  # embedded NUL
        return False
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False
