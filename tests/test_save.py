from __future__ import annotations

import pymupdf
import pytest
from fixtures import PASSWORD

from pdfeditor.core import document as document_module
from pdfeditor.core.document import PasswordRequired, PdfDocument, SaveError


def _rotation_on_disk(path, page=0, password=None) -> int:
    d = pymupdf.open(path)
    if password:
        d.authenticate(password)
    r = d[page].rotation
    d.close()
    return r


def test_incremental_save_after_rotation(simple_pdf) -> None:
    size_before = simple_pdf.stat().st_size
    doc = PdfDocument.open(simple_pdf)
    assert doc.can_save_incrementally()
    doc.set_page_rotation(0, 90)
    doc.save()
    doc.close()
    assert simple_pdf.stat().st_size > size_before
    assert _rotation_on_disk(simple_pdf) == 90


def test_save_as_sets_path_and_reopens(qtbot, simple_pdf, tmp_path) -> None:
    target = tmp_path / "copy.pdf"
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(2, 180)
    with qtbot.waitSignal(doc.path_changed) as blocker:
        doc.save_as(target)
    assert blocker.args == [str(target)]
    assert doc.path == str(target)
    assert doc.page_rotation(2) == 180
    assert doc.can_save_incrementally()
    doc.close()
    assert _rotation_on_disk(target, 2) == 180
    assert _rotation_on_disk(simple_pdf, 2) == 0


def test_forced_full_save_in_place(qtbot, simple_pdf, tmp_path) -> None:
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(1, 270)
    with qtbot.waitSignal(doc.reloaded), qtbot.assertNotEmitted(doc.path_changed):
        doc.save(force_full=True)
    assert doc.page_count == 3
    assert doc.page_rotation(1) == 270
    doc.render(1, 0.5)  # still usable after reopen
    doc.close()
    assert not (tmp_path / "simple.pdf.tmp").exists()
    d = pymupdf.open(simple_pdf)
    assert d.page_count == 3
    assert "Page 2" in d[1].get_text()
    assert d[1].rotation == 270
    d.close()


def test_encrypted_save_keeps_password(encrypted_pdf) -> None:
    doc = PdfDocument.open(encrypted_pdf, password=PASSWORD)
    doc.set_page_rotation(0, 90)
    doc.save()
    doc.set_page_rotation(1, 90)
    doc.save(force_full=True)
    assert doc.page_rotation(1) == 90
    doc.close()
    with pytest.raises(PasswordRequired):
        PdfDocument.open(encrypted_pdf)
    assert _rotation_on_disk(encrypted_pdf, 0, PASSWORD) == 90
    assert _rotation_on_disk(encrypted_pdf, 1, PASSWORD) == 90


def test_permission_error_maps_to_save_error(simple_pdf, monkeypatch) -> None:
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 90)

    def boom(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(document_module, "_write_atomically", boom)
    with pytest.raises(SaveError):
        doc.save()
    with pytest.raises(SaveError):
        doc.save(force_full=True)
    with pytest.raises(SaveError):
        doc.save_as(simple_pdf.with_name("other.pdf"))
    monkeypatch.undo()
    assert doc.page_rotation(0) == 90
    assert doc.path == str(simple_pdf)
    doc.close()


def test_mupdf_write_error_maps_to_save_error(simple_pdf, monkeypatch) -> None:
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 90)

    def boom(*args, **kwargs):
        raise RuntimeError("mupdf failure")

    monkeypatch.setattr(document_module, "_incremental_bytes", boom)
    monkeypatch.setattr(pymupdf.Document, "tobytes", boom)
    with pytest.raises(SaveError):
        doc.save()
    with pytest.raises(SaveError):
        doc.save(force_full=True)
    with pytest.raises(SaveError):
        doc.save_as(simple_pdf.with_name("other.pdf"))
    monkeypatch.undo()
    assert doc.page_rotation(0) == 90
    doc.close()


def test_replace_failure_keeps_changes(simple_pdf, monkeypatch) -> None:
    import os

    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 180)

    def locked(src, dst):
        raise PermissionError("file is locked")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(SaveError):
        doc.save(force_full=True)
    monkeypatch.undo()
    assert doc.page_rotation(0) == 180
    assert not doc.can_save_incrementally()
    assert not simple_pdf.with_name("simple.pdf.tmp").exists()
    doc.save()  # now succeeds with a full save
    doc.close()
    assert _rotation_on_disk(simple_pdf) == 180


def test_save_into_missing_directory(simple_pdf, tmp_path) -> None:
    doc = PdfDocument.open(simple_pdf)
    with pytest.raises(SaveError):
        doc.save_as(tmp_path / "no" / "such" / "dir.pdf")
    assert doc.path == str(simple_pdf)
    doc.close()


def test_incremental_saves_keep_original_bytes(simple_pdf) -> None:
    original = simple_pdf.read_bytes()
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 90)
    doc.save()
    first = simple_pdf.read_bytes()
    assert first.startswith(original)  # incremental: original bytes untouched
    assert doc.can_save_incrementally()
    doc.set_page_rotation(1, 180)
    doc.save()
    second = simple_pdf.read_bytes()
    assert second.startswith(first)
    doc.close()
    d = pymupdf.open(simple_pdf)
    assert not d.is_repaired
    assert (d[0].rotation, d[1].rotation) == (90, 180)
    d.close()


def test_file_not_held_open(simple_pdf) -> None:
    doc = PdfDocument.open(simple_pdf)
    simple_pdf.unlink()  # fails on Windows if MuPDF keeps the file open
    assert doc.render(1, 0.25).width() > 0
    doc.close()


def test_external_modification_forces_consistent_full_save(simple_pdf, form_pdf) -> None:
    doc = PdfDocument.open(simple_pdf)
    assert not doc.modified_on_disk()
    # Another program rewrites the file in place.
    with open(simple_pdf, "r+b") as f:
        f.truncate(0)
        f.write(form_pdf.read_bytes())
    assert doc.modified_on_disk()
    assert not doc.can_save_incrementally()
    doc.set_page_rotation(1, 90)
    doc.save()
    assert not doc.modified_on_disk()
    assert doc.can_save_incrementally()
    doc.close()
    d = pymupdf.open(simple_pdf)
    assert not d.is_repaired
    assert d.page_count == 3
    assert "Page 2" in d[1].get_text()
    assert d[1].rotation == 90
    assert not list(d[0].widgets())  # nothing of the other file survived
    d.close()


def test_deleted_file_is_modified_on_disk(simple_pdf) -> None:
    doc = PdfDocument.open(simple_pdf)
    simple_pdf.unlink()
    assert doc.modified_on_disk()
    assert not doc.can_save_incrementally()
    doc.save()  # recreates it with a full save
    assert simple_pdf.is_file()
    doc.close()


def test_reload_failure_after_save_keeps_document(qtbot, simple_pdf, monkeypatch) -> None:
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 90)

    def broken_open(*args, **kwargs):
        raise RuntimeError("cannot open")

    monkeypatch.setattr(pymupdf, "open", broken_open)
    with qtbot.assertNotEmitted(doc.reloaded):
        doc.save()  # the file is written; only the reload fails
    monkeypatch.undo()
    assert doc.is_open
    assert doc.page_rotation(0) == 90
    assert doc.render(0, 0.25).width() > 0
    assert not doc.can_save_incrementally()  # the in-memory doc is no longer the disk base
    doc.set_page_rotation(1, 180)
    with qtbot.waitSignal(doc.reloaded):
        doc.save()
    assert doc.can_save_incrementally()
    doc.close()
    assert _rotation_on_disk(simple_pdf, 0) == 90
    assert _rotation_on_disk(simple_pdf, 1) == 180


def test_replace_retried_on_transient_lock(simple_pdf, monkeypatch) -> None:
    import os

    real_replace = os.replace
    calls: list[int] = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("busy")
        real_replace(src, dst)

    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 270)
    monkeypatch.setattr(os, "replace", flaky)
    doc.save()
    monkeypatch.undo()
    assert len(calls) == 2
    doc.close()
    assert _rotation_on_disk(simple_pdf) == 270
