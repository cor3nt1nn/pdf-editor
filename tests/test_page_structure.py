"""PdfDocument page identity and structural operations (M6a-T1)."""

from __future__ import annotations

import fixtures
import pymupdf
import pytest
from fixtures import PASSWORD, SIMPLE_SIZES
from pdfcheck import strict_read
from PySide6.QtCore import QSizeF

from pdfeditor.core import pages
from pdfeditor.core.document import PageError, PdfDocument, SaveError


@pytest.fixture
def doc(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    yield d
    d.close()


class Recorder:
    def __init__(self, document: PdfDocument) -> None:
        self.events: list[tuple[str, object]] = []
        self.document = document
        document.pages_remapped.connect(self._remapped)
        document.structure_changed.connect(lambda: self.events.append(("structure", None)))

    def _remapped(self, mapping: object) -> None:
        # page_count and ids are already up to date when pages_remapped is emitted.
        self.events.append(("remapped", (mapping, self.document.page_count)))


def _sizes(d: PdfDocument) -> list[tuple[float, float]]:
    return [(d.page_size(i).width(), d.page_size(i).height()) for i in range(d.page_count)]


def test_page_ids_at_open_and_after_saves(doc: PdfDocument, tmp_path) -> None:
    ids = doc.page_ids()
    assert len(ids) == 3 and len(set(ids)) == 3
    assert [doc.page_id(i) for i in range(3)] == ids
    assert [doc.page_index(pid) for pid in ids] == [0, 1, 2]
    assert doc.page_index(-5) is None
    with pytest.raises(IndexError):
        doc.page_id(3)
    doc.set_page_rotation(0, 90)
    doc.save()  # incremental
    doc.save(force_full=True)
    doc.save_as(tmp_path / "copy.pdf")
    assert doc.page_ids() == ids
    other = PdfDocument.open(fixtures.make_simple_pdf(tmp_path / "other.pdf"))
    assert not set(other.page_ids()) & set(ids)
    other.close()
    assert not doc.structure_edited


def test_permissions_flags(doc, owner_locked_pdf, lo_form_full_access_pdf) -> None:
    assert doc.can_assemble and doc.can_extract
    locked = PdfDocument.open(owner_locked_pdf)
    assert not locked.can_assemble and not locked.can_extract
    with pytest.raises(PageError) as info:
        locked.delete_pages([0])
    assert info.value.reason == "permission"
    with pytest.raises(PageError):
        locked.insert_blank_page(0, QSizeF(100, 100))
    with pytest.raises(PageError):
        locked.extract_pages([0], "unused.pdf")
    locked.close()
    full = PdfDocument.open(lo_form_full_access_pdf, password=PASSWORD)
    assert full.can_assemble and full.can_extract
    full.close()


def test_dynamic_xfa_refuses_page_operations(dynamic_xfa_pdf) -> None:
    d = PdfDocument.open(dynamic_xfa_pdf)
    with pytest.raises(PageError) as info:
        d.reorder_pages(list(reversed(d.page_ids())))
    assert info.value.reason == "xfa"
    with pytest.raises(PageError):
        d.insert_blank_page(0, QSizeF(100, 100))
    d.close()


def test_delete_pages(doc: PdfDocument) -> None:
    ids = doc.page_ids()
    doc.page_size(2)  # fill the size cache
    rec = Recorder(doc)
    assert doc.can_save_incrementally()
    doc.delete_pages([0])
    assert rec.events == [("remapped", ([None, 0, 1], 2)), ("structure", None)]
    assert doc.page_count == 2
    assert doc.page_ids() == ids[1:]
    assert doc.page_index(ids[0]) is None
    assert doc.page_index(ids[2]) == 1
    assert _sizes(doc) == list(SIMPLE_SIZES[1:])
    assert doc.structure_edited
    assert not doc.can_save_incrementally()
    with pytest.raises(PageError) as info:
        doc.delete_pages([0, 1])
    assert info.value.reason == "last_page"
    assert doc.page_count == 2
    with pytest.raises(IndexError):
        doc.delete_pages([5])
    rec.events.clear()
    doc.delete_pages([])
    assert rec.events == []


def test_delete_prunes_form_fields(lo_form_pdf, tmp_path) -> None:
    d = PdfDocument.open(lo_form_pdf)
    d.delete_pages([1])
    assert [w.name for w in d.all_widgets()].count("Nom") == 1
    d.save()
    assert len(strict_read(d.path).fields) == 12
    d.close()


def test_reorder_pages(doc: PdfDocument) -> None:
    a, b, c = doc.page_ids()
    rec = Recorder(doc)
    doc.reorder_pages([c, a, b])
    assert rec.events == [("remapped", ([1, 2, 0], 3)), ("structure", None)]
    assert doc.page_ids() == [c, a, b]
    assert _sizes(doc) == [SIMPLE_SIZES[2], SIMPLE_SIZES[0], SIMPLE_SIZES[1]]
    rec.events.clear()
    doc.reorder_pages([c, a, b])  # unchanged: nothing happens
    assert rec.events == []
    with pytest.raises(PageError):
        doc.reorder_pages([a, b])


def test_insert_blank_page(doc: PdfDocument) -> None:
    ids = doc.page_ids()
    rec = Recorder(doc)
    pid = doc.insert_blank_page(1, QSizeF(200, 300))
    assert rec.events == [("remapped", ([0, 2, 3], 4)), ("structure", None)]
    assert doc.page_ids() == [ids[0], pid, ids[1], ids[2]]
    assert doc.page_size(1) == QSizeF(200, 300)
    end = doc.insert_blank_page(4, QSizeF(100, 100))
    assert doc.page_index(end) == 4
    doc.delete_pages([1])
    again = doc.insert_blank_page(1, QSizeF(200, 300), page_id=pid)
    assert again == pid and doc.page_index(pid) == 1
    with pytest.raises(PageError):
        doc.insert_blank_page(0, QSizeF(10, 10), page_id=pid)
    with pytest.raises(IndexError):
        doc.insert_blank_page(9, QSizeF(10, 10))


def test_insert_pages(doc: PdfDocument, lo_form_pdf) -> None:
    src = pymupdf.open(lo_form_pdf)
    data = pages.subdocument_bytes(src, [0, 1])
    ids = doc.page_ids()
    rec = Recorder(doc)
    new = doc.insert_pages(data, 3)
    assert rec.events == [("remapped", ([0, 1, 2], 5)), ("structure", None)]
    assert len(new) == 2
    assert doc.page_ids() == ids + new
    assert doc.is_form and not doc.last_insert_renamed_fields
    assert len(doc.all_widgets()) == 15
    doc.delete_pages([3, 4])
    assert not doc.is_form
    again = doc.insert_pages(data, 0, page_ids=new)
    assert again == new and doc.page_ids() == new + ids
    with pytest.raises(PageError):
        doc.insert_pages(data, 0, page_ids=new)  # ids already present
    with pytest.raises(PageError):
        doc.insert_pages(b"not a pdf", 0)


def test_insert_pages_reports_renamed_fields(lo_form_pdf, tmp_path) -> None:
    d = PdfDocument.open(lo_form_pdf)
    src = pymupdf.open(fixtures.make_lo_form_pdf(tmp_path / "src.pdf"))
    d.insert_pages(pages.subdocument_bytes(src, [0]), 2)
    assert d.last_insert_renamed_fields
    assert any(w.name == "Nom (2)" for w in d.all_widgets())
    assert ("Nom", "Nom (2)") in d.last_insert_field_renames
    d.close()


def test_insert_encrypted_subdocument(doc: PdfDocument, encrypted_pdf) -> None:
    data = encrypted_pdf.read_bytes()
    with pytest.raises(PageError):
        doc.insert_pages(data, 0)
    assert len(doc.insert_pages(data, 0, password=PASSWORD)) == 2


def test_restore_snapshot(doc: PdfDocument) -> None:
    ids = doc.page_ids()
    data = doc.snapshot()
    doc.delete_pages([1])
    blank = doc.insert_blank_page(0, QSizeF(50, 50))
    rec = Recorder(doc)
    doc.restore_snapshot(data, ids)
    assert rec.events == [("remapped", ([None, 0, 2], 3)), ("structure", None)]
    assert doc.page_ids() == ids
    assert doc.page_index(blank) is None
    assert _sizes(doc) == list(SIMPLE_SIZES)
    assert not doc.can_save_incrementally()
    with pytest.raises(PageError):
        doc.restore_snapshot(data, ids[:2])
    with pytest.raises(PageError):
        doc.restore_snapshot(b"garbage", ids)
    assert doc.page_ids() == ids


def test_snapshot_forces_full_save(doc: PdfDocument) -> None:
    assert doc.can_save_incrementally()
    doc.snapshot()
    assert not doc.can_save_incrementally()
    doc.save()
    assert doc.can_save_incrementally()


def test_full_then_incremental_after_structure_change(doc: PdfDocument) -> None:
    doc.delete_pages([2])
    doc.save()
    assert doc.can_save_incrementally()
    doc.set_page_rotation(0, 90)
    doc.save()
    assert PdfDocument.open(doc.path).page_count == 2


def test_encrypted_document_page_ops_keep_encryption(lo_form_full_access_pdf, simple_pdf) -> None:
    d = PdfDocument.open(lo_form_full_access_pdf, password=PASSWORD)
    d.insert_blank_page(0, QSizeF(300, 400))
    d.reorder_pages(list(reversed(d.page_ids())))
    ids = d.page_ids()
    data = d.snapshot()
    d.delete_pages([0])
    d.restore_snapshot(data, ids)
    d.insert_pages(pages.subdocument_bytes(pymupdf.open(simple_pdf), [0]), 1)
    d.save()
    reopened = pymupdf.open(d.path)
    assert reopened.needs_pass
    assert reopened.authenticate(PASSWORD)
    assert reopened.page_count == 4
    strict_read(d.path, PASSWORD)
    d.close()


def test_extract_pages(lo_form_pdf, tmp_path) -> None:
    d = PdfDocument.open(lo_form_pdf)
    fixtures.fill_lo_form(d)
    out = tmp_path / "out.pdf"
    d.extract_pages([1, 0], out)
    result = strict_read(out)
    assert len(result.reader.pages) == 2
    copy = pymupdf.open(out)
    assert [len(list(copy[i].widgets())) for i in range(2)] == [2, 13]
    assert not copy.metadata["encryption"]
    assert not d.can_save_incrementally()  # the in-memory copy forces a full save
    with pytest.raises(ValueError):
        d.extract_pages([0], lo_form_pdf)
    with pytest.raises(ValueError):
        d.extract_pages([], out)
    with pytest.raises(IndexError):
        d.extract_pages([5], out)
    with pytest.raises(SaveError):
        d.extract_pages([0], tmp_path / "missing" / "dir" / "x.pdf")
    d.close()


def test_extract_from_password_file_keeps_password(lo_form_full_access_pdf, tmp_path) -> None:
    d = PdfDocument.open(lo_form_full_access_pdf, password=PASSWORD)
    out = tmp_path / "out.pdf"
    d.extract_pages([0], out)
    copy = pymupdf.open(out)
    assert copy.needs_pass and copy.authenticate(PASSWORD)
    strict_read(out, PASSWORD)
    d.close()


def test_split_document(tmp_path, simple_pdf) -> None:
    d = PdfDocument.open(simple_pdf)
    groups = pages.split_every(d.page_count, 2)
    paths = [tmp_path / "simple-01.pdf", tmp_path / "simple-02.pdf"]
    d.split_document(groups, paths)
    assert [pymupdf.open(p).page_count for p in paths] == [2, 1]
    for p in paths:
        strict_read(p)
    with pytest.raises(ValueError):
        d.split_document(groups, paths[:1])
    with pytest.raises(ValueError):
        d.split_document([[0], [1]], [paths[0], simple_pdf])
    d.close()


def test_close_clears_ids_and_snapshots(doc: PdfDocument) -> None:
    doc.snapshots.put(b"x")
    doc.close()
    assert doc.page_ids() == []
    assert len(doc.snapshots) == 0
    assert not doc.can_assemble
