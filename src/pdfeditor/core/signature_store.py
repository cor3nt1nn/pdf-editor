"""Per-user library of signature images (M4), with QtCore/QtGui only.

Layout of the store directory (default: ``AppLocalDataLocation/signatures``)::

    index.json      {"version": 1, "default": id | null,
                     "signatures": [{"id", "name", "file", "created", "width", "height"}]}
    <uuid>.png      one RGBA PNG per signature, written by Qt

Both the PNG and the index are written atomically (temporary file + ``os.replace``).
A missing or corrupt index, or an entry whose PNG is missing, is dropped with a warning:
the store never raises while reading. Nothing is written until the first change.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QObject, QStandardPaths, Signal
from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

INDEX_NAME = "index.json"
INDEX_VERSION = 1
_TMP_SUFFIX = ".tmp"


def default_directory() -> Path:
    """``AppLocalDataLocation/signatures`` (not created)."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "signatures"


@dataclass(frozen=True, slots=True)
class SignatureRecord:
    """One saved signature: ``file`` is the PNG's name inside the store directory,
    ``created`` an ISO 8601 timestamp, ``width``/``height`` the image size in pixels."""

    id: str
    name: str
    file: str
    created: str
    width: int
    height: int

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "file": self.file,
            "created": self.created,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_json(cls, data: object) -> SignatureRecord | None:
        """The record described by ``data``, or None when it is malformed."""
        if not isinstance(data, dict):
            return None
        try:
            rid, name, file, created = (data[k] for k in ("id", "name", "file", "created"))
            width, height = data["width"], data["height"]
        except KeyError:
            return None
        if not all(isinstance(v, str) for v in (rid, name, file, created)) or not rid:
            return None
        if not all(type(v) is int and v > 0 for v in (width, height)):
            return None
        # A bare file name only: never follow a path out of the store directory.
        if not file or Path(file).name != file or file in (".", ".."):
            return None
        return cls(rid, name, file, created, width, height)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + _TMP_SUFFIX)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class SignatureStore(QObject):
    """Saved signatures of the current user. ``changed`` is emitted after every
    successful modification (add, rename, delete, set_default, reload)."""

    changed = Signal()

    def __init__(self, directory: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dir = Path(directory) if directory is not None else default_directory()
        self._records: list[SignatureRecord] = []
        self._default: str | None = None
        self._read_index()

    # -- reading -----------------------------------------------------------
    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def index_path(self) -> Path:
        return self._dir / INDEX_NAME

    @property
    def default_id(self) -> str | None:
        return self._default

    def records(self) -> list[SignatureRecord]:
        return list(self._records)

    def get(self, sig_id: str) -> SignatureRecord | None:
        return next((r for r in self._records if r.id == sig_id), None)

    def path(self, sig_id: str) -> Path:
        return self._dir / self._require(sig_id).file

    def load(self, sig_id: str) -> QImage | None:
        """The signature's image, or None (with a warning) when it cannot be read."""
        record = self.get(sig_id)
        if record is None:
            return None
        image = QImage(str(self._dir / record.file))
        if image.isNull():
            log.warning("signature image %s could not be read", self._dir / record.file)
            return None
        return image

    def reload(self) -> None:
        self._read_index()
        self.changed.emit()

    def _read_index(self) -> None:
        self._records = []
        self._default = None
        path = self.index_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            log.warning("signature index %s is unreadable (%s); ignored", path, exc)
            return
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
            log.warning("signature index %s has an unsupported format; ignored", path)
            return
        entries = data.get("signatures")
        if not isinstance(entries, list):
            log.warning("signature index %s has no signature list; ignored", path)
            return
        seen: set[str] = set()
        for entry in entries:
            record = SignatureRecord.from_json(entry)
            if record is None:
                log.warning("malformed signature entry %r dropped", entry)
                continue
            if record.id in seen:
                log.warning("duplicate signature id %s dropped", record.id)
                continue
            if not (self._dir / record.file).is_file():
                log.warning("signature image %s is missing; entry dropped", record.file)
                continue
            seen.add(record.id)
            self._records.append(record)
        default = data.get("default")
        self._default = default if default in seen else self._first_id()

    # -- writing -----------------------------------------------------------
    def add(self, name: str, image: QImage) -> SignatureRecord:
        """Save ``image`` as a new signature (the default one if it is the first)."""
        name = name.strip()
        if not name:
            raise ValueError("a signature needs a name")
        if image.isNull():
            raise ValueError("null signature image")
        self._dir.mkdir(parents=True, exist_ok=True)
        sig_id = uuid.uuid4().hex
        file = f"{sig_id}.png"
        self._write_png(self._dir / file, image)
        record = SignatureRecord(
            id=sig_id,
            name=name,
            file=file,
            created=datetime.now(UTC).isoformat(timespec="seconds"),
            width=image.width(),
            height=image.height(),
        )
        records = [*self._records, record]
        default = self._default if self._default is not None else sig_id
        try:
            self._commit(records, default)
        except BaseException:
            (self._dir / file).unlink(missing_ok=True)
            raise
        return record

    def rename(self, sig_id: str, name: str) -> None:
        old = self._require(sig_id)
        name = name.strip()
        if not name:
            raise ValueError("a signature needs a name")
        if name == old.name:
            return
        new = SignatureRecord(old.id, name, old.file, old.created, old.width, old.height)
        self._commit([new if r.id == sig_id else r for r in self._records], self._default)

    def delete(self, sig_id: str) -> None:
        """Remove a signature; if it was the default, the first remaining one becomes it."""
        record = self._require(sig_id)
        records = [r for r in self._records if r.id != sig_id]
        default = self._default
        if default == sig_id:
            default = records[0].id if records else None
        self._commit(records, default)
        try:
            (self._dir / record.file).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not remove %s: %s", self._dir / record.file, exc)

    def set_default(self, sig_id: str) -> None:
        self._require(sig_id)
        if sig_id != self._default:
            self._commit(self._records, sig_id)

    # -- helpers -----------------------------------------------------------
    def _first_id(self) -> str | None:
        return self._records[0].id if self._records else None

    def _require(self, sig_id: str) -> SignatureRecord:
        record = self.get(sig_id)
        if record is None:
            raise KeyError(sig_id)
        return record

    def _commit(self, records: list[SignatureRecord], default: str | None) -> None:
        payload = {
            "version": INDEX_VERSION,
            "default": default,
            "signatures": [r.to_json() for r in records],
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        _write_atomic(self.index_path, text.encode("utf-8"))
        self._records = list(records)
        self._default = default
        self.changed.emit()

    @staticmethod
    def _write_png(path: Path, image: QImage) -> None:
        """Encode in memory (QImageWriter on a file keeps it open on Windows), then
        write atomically."""
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        if not rgba.save(buffer, "PNG"):
            raise OSError(f"could not encode {path.name} as PNG")
        _write_atomic(path, bytes(buffer.data().data()))
