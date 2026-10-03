"""M4-T4: per-user signature store (core/signature_store.py)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from pdfeditor.core import signature_store as store_mod
from pdfeditor.core.signature_store import (
    INDEX_NAME,
    SignatureRecord,
    SignatureStore,
    default_directory,
)


def _image(w: int = 40, h: int = 16, color: QColor | None = None) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(color if color is not None else QColor(10, 20, 200, 128))
    return img


def _index(store: SignatureStore) -> dict:
    return json.loads(store.index_path.read_text(encoding="utf-8"))


def _reopen(store: SignatureStore) -> SignatureStore:
    return SignatureStore(store.directory)


def test_default_directory_is_app_local_data() -> None:
    path = default_directory()
    assert path.name == "signatures"
    # Only computed, never created here (tests must not touch the real user dir).


def test_empty_store_writes_nothing(tmp_path) -> None:
    directory = tmp_path / "sigs"
    store = SignatureStore(directory)
    assert store.records() == []
    assert store.default_id is None
    assert store.get("nope") is None
    assert store.load("nope") is None
    assert not directory.exists()


def test_add_writes_png_and_index(signature_store, signature_png) -> None:
    source = QImage(str(signature_png))
    record = signature_store.add("  Jane Doe  ", source)
    assert isinstance(record, SignatureRecord)
    assert record.name == "Jane Doe"
    assert record.file == f"{record.id}.png"
    assert (record.width, record.height) == (source.width(), source.height())
    assert datetime.fromisoformat(record.created).tzinfo is not None
    png = signature_store.directory / record.file
    assert png.is_file()
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # The first signature becomes the default.
    assert signature_store.default_id == record.id

    data = _index(signature_store)
    assert data["version"] == 1
    assert data["default"] == record.id
    assert data["signatures"] == [
        {
            "id": record.id,
            "name": "Jane Doe",
            "file": record.file,
            "created": record.created,
            "width": record.width,
            "height": record.height,
        }
    ]


def test_png_round_trip_keeps_alpha(signature_store) -> None:
    img = _image(color=QColor(10, 20, 200, 128))
    img.setPixelColor(0, 0, QColor(0, 0, 0, 0))
    record = signature_store.add("A", img)
    loaded = signature_store.load(record.id)
    assert loaded is not None and loaded.hasAlphaChannel()
    assert loaded.size() == img.size()
    assert loaded.pixelColor(5, 5) == QColor(10, 20, 200, 128)
    assert loaded.pixelColor(0, 0).alpha() == 0


def test_second_add_keeps_default(store_with_one) -> None:
    first = store_with_one.records()[0]
    assert first.name == "My signature"
    second = store_with_one.add("Initials", _image())
    assert store_with_one.default_id == first.id
    assert [r.id for r in store_with_one.records()] == [first.id, second.id]
    assert store_with_one.get(second.id) == second
    assert store_with_one.path(second.id) == store_with_one.directory / second.file


def test_add_rejects_bad_input(signature_store) -> None:
    with pytest.raises(ValueError):
        signature_store.add("   ", _image())
    with pytest.raises(ValueError):
        signature_store.add("Null", QImage())
    assert signature_store.records() == []


def test_rename_delete_set_default_persist(store_with_one) -> None:
    store = store_with_one
    a = store.records()[0]
    b = store.add("B", _image())
    c = store.add("C", _image())

    store.rename(b.id, " Beta ")
    store.set_default(c.id)
    store.delete(a.id)
    assert not (store.directory / a.file).exists()

    again = _reopen(store)
    assert [r.name for r in again.records()] == ["Beta", "C"]
    assert again.default_id == c.id
    assert again.get(b.id).created == b.created
    assert again.load(c.id) is not None


def test_delete_default_picks_another(store_with_one) -> None:
    store = store_with_one
    a = store.records()[0]
    b = store.add("B", _image())
    store.delete(a.id)
    assert store.default_id == b.id
    assert _reopen(store).default_id == b.id
    store.delete(b.id)
    assert store.records() == []
    assert store.default_id is None
    assert _index(store) == {"version": 1, "default": None, "signatures": []}


def test_unknown_id_raises(store_with_one) -> None:
    for call in (
        lambda: store_with_one.rename("nope", "x"),
        lambda: store_with_one.delete("nope"),
        lambda: store_with_one.set_default("nope"),
        lambda: store_with_one.path("nope"),
    ):
        with pytest.raises(KeyError):
            call()
    with pytest.raises(ValueError):
        store_with_one.rename(store_with_one.default_id, "")


def test_changed_emitted(qtbot, signature_store) -> None:
    with qtbot.waitSignal(signature_store.changed, timeout=100):
        rec = signature_store.add("A", _image())
    with qtbot.waitSignal(signature_store.changed, timeout=100):
        b = signature_store.add("B", _image())
    with qtbot.waitSignal(signature_store.changed, timeout=100):
        signature_store.rename(rec.id, "AA")
    with qtbot.waitSignal(signature_store.changed, timeout=100):
        signature_store.set_default(b.id)
    with qtbot.waitSignal(signature_store.changed, timeout=100):
        signature_store.delete(rec.id)
    with qtbot.waitSignal(signature_store.changed, timeout=100):
        signature_store.reload()
    # No-op changes do not emit.
    with qtbot.assertNotEmitted(signature_store.changed):
        signature_store.set_default(b.id)
        signature_store.rename(b.id, "B")


def test_missing_png_dropped_with_warning(store_with_one, caplog) -> None:
    store = store_with_one
    record = store.records()[0]
    (store.directory / record.file).unlink()
    with caplog.at_level(logging.WARNING, logger=store_mod.__name__):
        again = _reopen(store)
    assert again.records() == []
    assert again.default_id is None
    assert "missing" in caplog.text


def test_unreadable_png_load_returns_none(store_with_one, caplog) -> None:
    store = store_with_one
    record = store.records()[0]
    (store.directory / record.file).write_bytes(b"not a png")
    with caplog.at_level(logging.WARNING, logger=store_mod.__name__):
        assert store.load(record.id) is None
    assert "could not be read" in caplog.text


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b"\xff\xfe\x00garbage",
        b"[]",
        b'{"version": 2, "default": null, "signatures": []}',
        b'{"version": 1, "default": null, "signatures": {}}',
    ],
)
def test_corrupt_index_gives_empty_store(tmp_path, caplog, content) -> None:
    directory = tmp_path / "signatures"
    directory.mkdir()
    (directory / INDEX_NAME).write_bytes(content)
    with caplog.at_level(logging.WARNING, logger=store_mod.__name__):
        store = SignatureStore(directory)
    assert store.records() == []
    assert store.default_id is None
    assert caplog.records
    # The store stays usable; the next change rewrites a valid index.
    rec = store.add("Fresh", _image())
    assert _index(store)["signatures"][0]["id"] == rec.id


def test_malformed_entries_dropped(store_with_one, caplog) -> None:
    store = store_with_one
    good = store.records()[0]
    data = _index(store)
    outside = store.directory.parent / "outside.png"
    _image().save(str(outside))
    data["signatures"] += [
        "junk",
        {"id": "x", "name": "no size", "file": good.file, "created": ""},
        {**data["signatures"][0], "name": "duplicate"},
        {**data["signatures"][0], "id": "esc", "file": "../outside.png"},
        {**data["signatures"][0], "id": "neg", "width": -1},
        {**data["signatures"][0], "id": "flt", "height": 3.5},
    ]
    data["default"] = "does-not-exist"
    store.index_path.write_text(json.dumps(data), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=store_mod.__name__):
        again = _reopen(store)
    assert again.records() == [good]
    assert again.default_id == good.id
    assert len(caplog.records) == 6


def test_atomic_write_leaves_no_tmp(store_with_one) -> None:
    store = store_with_one
    b = store.add("B", _image())
    store.rename(b.id, "Bee")
    store.set_default(b.id)
    store.delete(store.records()[0].id)
    names = sorted(p.name for p in store.directory.iterdir())
    assert names == sorted([INDEX_NAME, b.file])
    assert not any(n.endswith(".tmp") for n in names)


def test_failed_index_write_keeps_previous_state(store_with_one, monkeypatch) -> None:
    store = store_with_one
    before = store.index_path.read_bytes()
    records = store.records()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.add("B", _image())
    with pytest.raises(OSError):
        store.rename(records[0].id, "Other")
    monkeypatch.undo()

    assert store.records() == records
    assert store.index_path.read_bytes() == before
    # Neither the half-written index nor the new PNG are left behind.
    assert sorted(p.name for p in store.directory.iterdir()) == sorted(
        [INDEX_NAME, records[0].file]
    )


def test_store_dir_created_on_first_add(tmp_path) -> None:
    directory = tmp_path / "deep" / "signatures"
    store = SignatureStore(Path(directory))
    store.add("A", _image())
    assert (directory / INDEX_NAME).is_file()
