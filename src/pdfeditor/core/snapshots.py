"""Undo copies of the whole document (page deletion, docs/M6_PLAN.md D1).

A :class:`SnapshotStore` keeps byte strings (in-memory writes of the document) by id. They
stay in RAM while the total is under ``memory_limit``; past it, new snapshots are written
to a private temporary directory (``<tmp>/pdfeditor-undo-<random>/<id>.bin``) created on
first need and removed by :meth:`SnapshotStore.clear`. No pymupdf, no Qt.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from itertools import count
from pathlib import Path

log = logging.getLogger(__name__)

#: Snapshots are kept in memory while their total size stays under this many bytes.
SNAPSHOT_MEMORY_LIMIT = 32 * 1024 * 1024
#: Prefix of the temporary directory holding spilled snapshots.
SPILL_PREFIX = "pdfeditor-undo-"


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
            self._spill_dir = None
