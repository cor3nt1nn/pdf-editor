"""Undo copies of the whole document (page deletion, docs/M6_PLAN.md D1) and of the pages
an insertion brings.

A :class:`SnapshotStore` keeps byte strings (in-memory writes of the document) by id. They
stay in RAM while the total is under ``memory_limit``; past it, new snapshots are written
to a private temporary directory (``<tmp>/pdfeditor-undo-<random>/<id>.bin``) created on
first need and removed by :meth:`SnapshotStore.clear`. While it exists the session holds
``<that directory>.lock`` open (its process id inside), so that :func:`sweep_orphans`
at the next start can tell the directories a crashed session left behind from those of a
running one (docs/ARCHITECTURE.md Deviation 98). No pymupdf, no Qt.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
from itertools import count
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

#: Snapshots are kept in memory while their total size stays under this many bytes.
SNAPSHOT_MEMORY_LIMIT = 32 * 1024 * 1024
#: Prefix of the temporary directory holding spilled snapshots.
SPILL_PREFIX = "pdfeditor-undo-"
#: Suffix of the lock file next to a spill directory (``<directory>.lock``).
LOCK_SUFFIX = ".lock"
#: A spill directory without a lock file is an orphan once it is this old (seconds).
ORPHAN_AGE_S = 24 * 3600.0


class SnapshotStore:
    """Byte snapshots by id, in memory then spilled to temporary files.

    ``directory``: parent of the spill directory (default: the system temp directory).
    """

    def __init__(
        self, memory_limit: int = SNAPSHOT_MEMORY_LIMIT, directory: str | Path | None = None
    ) -> None:
        self._memory_limit = int(memory_limit)
        self._parent = str(directory) if directory is not None else None
        self._memory: dict[int, bytes] = {}
        self._files: dict[int, Path] = {}
        self._spill_dir: Path | None = None
        self._lock: IO[str] | None = None
        self._ids = count(1)

    @property
    def bytes_in_memory(self) -> int:
        return sum(len(data) for data in self._memory.values())

    @property
    def on_disk_count(self) -> int:
        return len(self._files)

    @property
    def spill_directory(self) -> Path | None:
        """The temporary directory of spilled snapshots, or None if none was written."""
        return self._spill_dir

    def __len__(self) -> int:
        return len(self._memory) + len(self._files)

    def put(self, data: bytes) -> int:
        """Store ``data``; returns its id. Raises OSError if it had to be spilled to disk
        and could not be written (nothing is stored then)."""
        sid = next(self._ids)
        if self.bytes_in_memory + len(data) <= self._memory_limit:
            self._memory[sid] = bytes(data)
            return sid
        if self._spill_dir is None:
            self._spill_dir = Path(tempfile.mkdtemp(prefix=SPILL_PREFIX, dir=self._parent))
            self._lock = _hold_lock(self._spill_dir)
        path = self._spill_dir / f"{sid}.bin"
        try:
            path.write_bytes(data)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        self._files[sid] = path
        log.debug("undo snapshot %d (%d bytes) spilled to %s", sid, len(data), path)
        return sid

    def get(self, sid: int) -> bytes:
        """The snapshot ``sid``. Raises KeyError if unknown, OSError if unreadable."""
        data = self._memory.get(sid)
        if data is not None:
            return data
        return self._files[sid].read_bytes()

    def discard(self, sid: int) -> None:
        """Forget snapshot ``sid`` (unknown ids are ignored)."""
        if self._memory.pop(sid, None) is not None:
            return
        path = self._files.pop(sid, None)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.warning("could not remove undo snapshot %s", path, exc_info=True)

    def clear(self) -> None:
        """Forget every snapshot and remove the spill directory."""
        self._memory.clear()
        self._files.clear()
        if self._spill_dir is not None:
            shutil.rmtree(self._spill_dir, ignore_errors=True)
            if self._lock is not None:
                self._lock.close()
                self._lock = None
            _lock_path(self._spill_dir).unlink(missing_ok=True)
            self._spill_dir = None


def _lock_path(directory: Path) -> Path:
    return directory.with_name(directory.name + LOCK_SUFFIX)


def _hold_lock(directory: Path) -> IO[str] | None:
    """Create ``<directory>.lock`` (our process id) and keep it open: on Windows an open
    file cannot be deleted, which is what :func:`sweep_orphans` tests."""
    try:
        handle = open(_lock_path(directory), "w", encoding="ascii")
        handle.write(str(os.getpid()))
        handle.flush()
        return handle
    except OSError:
        log.warning("cannot create the lock of %s", directory, exc_info=True)
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # exists but not ours
        return True
    return True


def _in_use(directory: Path, now: float, max_age: float) -> bool:
    """A running session owns ``directory`` (see :func:`sweep_orphans`)."""
    lock = _lock_path(directory)
    if lock.exists():
        if sys.platform == "win32":
            try:
                lock.unlink()  # refused while the owning session holds it open
            except OSError:
                return True
            return False
        try:
            return _pid_alive(int(lock.read_text(encoding="ascii").strip()))
        except (OSError, ValueError):
            return False
    try:  # no lock: an older version's directory, or one being created right now
        return now - directory.stat().st_mtime < max_age
    except OSError:
        return True


def sweep_orphans(directory: str | Path | None = None, *, max_age: float = ORPHAN_AGE_S) -> int:
    """Remove the spill directories (``pdfeditor-undo-*``) that crashed sessions left in
    ``directory`` (default: the system temp directory); returns how many were removed.

    A directory whose lock file its session still holds is kept whatever its age; one
    without a lock file only once it is older than ``max_age`` seconds. Called at start-up;
    never raises.
    """
    parent = Path(directory) if directory is not None else Path(tempfile.gettempdir())
    now = time.time()
    removed = 0
    try:
        candidates = [p for p in parent.glob(SPILL_PREFIX + "*") if p.is_dir()]
    except OSError:
        log.warning("cannot list %s", parent, exc_info=True)
        return 0
    for spill in candidates:
        if _in_use(spill, now, max_age):
            continue
        shutil.rmtree(spill, ignore_errors=True)
        if spill.exists():
            continue
        try:
            _lock_path(spill).unlink(missing_ok=True)
        except OSError:
            pass
        removed += 1
        log.info("removed the undo copies of a previous session: %s", spill)
    return removed
