from __future__ import annotations

import pymupdf
import pytest
from fixtures import PASSWORD

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
    with qtbot.waitSignal(doc.path_changed):
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

    monkeypatch.setattr(pymupdf.Document, "save", boom)
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
