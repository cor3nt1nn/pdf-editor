"""core/pages.py and core/snapshots.py (M6a-T1)."""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import fixtures
import pymupdf
import pytest
from fixtures import OWNER_PASSWORD, PASSWORD
from pdfcheck import strict_read

from pdfeditor.core import pages
from pdfeditor.core.document import PasswordRequired, PdfDocument
from pdfeditor.core.forms import XfaKind, detect_xfa
from pdfeditor.core.snapshots import SPILL_PREFIX, SnapshotStore


def _fields(doc: pymupdf.Document) -> list[int]:
    kind, value = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")
    assert kind == "array"
    return [int(x) for x in re.findall(r"(\d+) 0 R", value)]


def _field_xref(doc: pymupdf.Document, name: str) -> int:
    return next(x for x in _fields(doc) if doc.xref_get_key(x, "T")[1] == name)


def _kids(doc: pymupdf.Document, xref: int) -> list[int]:
    return [int(x) for x in re.findall(r"(\d+) 0 R", doc.xref_get_key(xref, "Kids")[1])]


def _without_id(data: bytes) -> bytes:
    """``data`` without the trailer /ID (its second part changes with every write)."""
    return re.sub(rb"/ID\s*\[[^\]]*\]", b"", data)


def _labelled(n: int = 5) -> pymupdf.Document:
    doc = pymupdf.open()
    for ch in "ABCDEFGHIJ"[:n]:
        doc.new_page().insert_text((72, 72), ch, fontsize=40)
    return doc


def _labels(doc: pymupdf.Document) -> str:
    return "".join(doc[i].get_text().strip()[:1] for i in range(doc.page_count))


def _widgets(doc: pymupdf.Document) -> list[tuple[int, str, object]]:
    return [(p.number, w.field_name, w.field_value) for p in doc for w in p.widgets()]


# -- prune_fields --------------------------------------------------------------
def test_prune_fields_after_deleting_page_two(lo_form_pdf, tmp_path) -> None:
    doc = pymupdf.open(lo_form_pdf)
    assert len(_fields(doc)) == 13
    nom = _field_xref(doc, "Nom")
    assert len(_kids(doc, nom)) == 2
    doc.delete_page(1)
    assert len(_fields(doc)) == 13  # MuPDF leaves the deleted page's fields (P1)
    assert pages.prune_fields(doc) == 2  # "Case à cocher 2_1" and Nom's second kid
    assert len(_fields(doc)) == 12
    assert len(_kids(doc, nom)) == 1
    assert doc.is_form_pdf
    out = tmp_path / "pruned.pdf"
    doc.save(out, garbage=3, deflate=True)
    assert len(strict_read(out).fields) == 12
    page = doc[0]  # the widget needs its page alive
    widget = next(w for w in page.widgets() if w.field_name == "Nom")
    widget.field_value = "Dupont"
    widget.update()
    reopened = pymupdf.open(stream=doc.tobytes())
    assert [w.field_value for w in reopened[0].widgets() if w.field_name == "Nom"] == ["Dupont"]
    assert pages.prune_fields(doc) == 0  # idempotent


def test_prune_fields_repairs_partial_insert(simple_pdf, lo_form_pdf) -> None:
    target = pymupdf.open(simple_pdf)
    target.insert_pdf(pymupdf.open(lo_form_pdf), from_page=0, to_page=0)
    nom = _field_xref(target, "Nom")
    kids = _kids(target, nom)
    assert len(kids) == 2  # one refers to the uncopied widget of page 2
    live = {x for p in target for x, _, _ in p.annot_xrefs()}
    assert not set(kids) <= live
    assert pages.prune_fields(target) >= 1
    assert set(_kids(target, nom)) <= live
    assert len(_kids(target, nom)) == 1


def test_prune_fields_all_pruned(form_pdf, simple_pdf) -> None:
    doc = pymupdf.open(form_pdf)
    doc.insert_pdf(pymupdf.open(simple_pdf))
    for _ in range(pymupdf.open(form_pdf).page_count):
        doc.delete_page(0)
    pages.prune_fields(doc)
    assert _fields(doc) == []
    assert not doc.is_form_pdf


def test_prune_fields_without_form(simple_pdf) -> None:
    assert pages.prune_fields(pymupdf.open(simple_pdf)) == 0


# -- delete / reorder / blank -----------------------------------------------------
def test_delete_pages_prunes_and_keeps_one(lo_form_pdf) -> None:
    doc = pymupdf.open(lo_form_pdf)
    with pytest.raises(ValueError):
        pages.delete_pages(doc, [0, 1])
    assert pages.delete_pages(doc, [1, 1]) == 1
    assert doc.page_count == 1
    assert len(_fields(doc)) == 12
    with pytest.raises(IndexError):
        pages.delete_pages(doc, [3])


def test_reorder_all_permutations_keep_page_xrefs() -> None:
    base = _labelled(5)
    xrefs = [base.page_xref(i) for i in range(5)]
    data = base.tobytes()
    for perm in itertools.permutations(range(5)):
        doc = pymupdf.open(stream=data)
        pages.reorder(doc, list(perm))
        assert _labels(doc) == "".join("ABCDE"[i] for i in perm)
        assert [doc.page_xref(i) for i in range(5)] == [xrefs[i] for i in perm]


def test_reorder_rejects_non_permutation() -> None:
    with pytest.raises(ValueError):
        pages.reorder(_labelled(3), [0, 0, 1])


def test_insert_blank_positions() -> None:
    doc = _labelled(3)
    pages.insert_blank(doc, 1, 200, 300)
    pages.insert_blank(doc, doc.page_count, 100, 150)
    assert doc.page_count == 5
    assert tuple(doc[1].rect) == (0, 0, 200, 300)
    assert tuple(doc[4].rect) == (0, 0, 100, 150)
    assert [doc[i].get_text().strip() for i in range(5)] == ["A", "", "B", "C", ""]
    with pytest.raises(IndexError):
        pages.insert_blank(doc, 9, 100, 100)


# -- insert_pages ---------------------------------------------------------------------
def test_insert_pages_into_non_form_target(simple_pdf, lo_form_pdf, tmp_path) -> None:
    doc = pymupdf.open(simple_pdf)
    src = pymupdf.open(lo_form_pdf)
    assert pages.insert_pages(doc, src, 1, had_xfa=False) == 2
    assert doc.page_count == 5
    assert len(_widgets(doc)) == 15
    assert doc.is_form_pdf
    out = tmp_path / "out.pdf"
    doc.save(out, garbage=3)
    assert len(strict_read(out).fields) == 13


def test_insert_pages_into_form_renames_duplicates(lo_form_pdf, tmp_path) -> None:
    doc = pymupdf.open(lo_form_pdf)
    src = pymupdf.open(fixtures.make_lo_form_pdf(tmp_path / "src.pdf"))
    assert pages.field_names(doc) & pages.field_names(src)
    pages.insert_pages(doc, src, 2, had_xfa=False)
    names = [n for _, n, _ in _widgets(doc)]
    assert len(names) == 30
    assert any(re.fullmatch(r"Nom \[\d+\]", n) for n in names)
    out = tmp_path / "out.pdf"
    doc.save(out, garbage=3)
    strict_read(out)


def test_insert_selected_pages_in_given_order(lo_form_pdf, tmp_path) -> None:
    src = pymupdf.open(lo_form_pdf)
    src.insert_pdf(pymupdf.open(fixtures.make_lo_form_pdf(tmp_path / "b.pdf")))
    doc = pymupdf.open(fixtures.make_simple_pdf(tmp_path / "s.pdf"))
    # Several runs: one graft only (a second graft from the source loses its fields).
    assert pages.insert_pages(doc, src, 0, [3, 0, 2], had_xfa=False) == 3
    assert [len(list(doc[i].widgets())) for i in range(3)] == [2, 13, 13]
    out = tmp_path / "out.pdf"
    doc.save(out, garbage=3)
    strict_read(out)


def test_insert_xfa_source_into_plain_target_strips_xfa(simple_pdf, static_xfa_pdf) -> None:
    doc = pymupdf.open(simple_pdf)
    pages.insert_pages(doc, pymupdf.open(static_xfa_pdf), 0, had_xfa=pages.has_xfa(doc))
    assert not pages.has_xfa(doc)
    assert doc.is_form_pdf


def test_insert_into_static_xfa_target_keeps_xfa(simple_pdf, static_xfa_pdf) -> None:
    doc = pymupdf.open(static_xfa_pdf)
    pages.insert_pages(doc, pymupdf.open(simple_pdf), 1, had_xfa=pages.has_xfa(doc))
    assert detect_xfa(doc) is XfaKind.STATIC


def test_subdocument_bytes_order_and_validation(tmp_path) -> None:
    data = pages.subdocument_bytes(_labelled(5), [4, 1, 2])
    assert _labels(pymupdf.open(stream=data)) == "EBC"
    with pytest.raises(ValueError):
        pages.subdocument(_labelled(3), [1, 1])
    with pytest.raises(IndexError):
        pages.subdocument(_labelled(3), [5])


# -- page ranges --------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1-3,7", [0, 1, 2, 6]),
        ("9-", [8, 9]),
        (" 2 - 4 , 1 ", [1, 2, 3, 0]),
        ("-2", [0, 1]),
        ("3, 1-4, 3", [2, 0, 1, 3]),
        ("10", [9]),
    ],
)
def test_parse_page_ranges(text, expected) -> None:
    assert pages.parse_page_ranges(text, 10) == expected


@pytest.mark.parametrize(
    ("text", "token"),
    [("0", "0"), ("11", "11"), ("1-3, x", "x"), ("5-3", "5-3"), ("", ""), ("1,,2", ""), ("-", "-")],
)
def test_parse_page_ranges_errors(text, token) -> None:
    with pytest.raises(pages.PageRangeError) as info:
        pages.parse_page_ranges(text, 10)
    assert info.value.token == token
    assert isinstance(info.value, ValueError)


def test_split_every_and_ranges() -> None:
    assert pages.split_every(5, 2) == [[0, 1], [2, 3], [4]]
    assert pages.split_every(4, 4) == [[0, 1, 2, 3]]
    with pytest.raises(ValueError):
        pages.split_every(4, 0)
    assert pages.split_ranges("1-2, 3, 4-", 5) == [[0, 1], [2], [3, 4]]
    with pytest.raises(pages.PageRangeError):
        pages.split_ranges("1-2, 9", 5)


# -- open_source ----------------------------------------------------------------
def test_open_source_encrypted_wrong_then_right(encrypted_pdf) -> None:
    # The fixture forbids copying: only its owner password opens it as a source.
    answers = iter(["wrong", "owner-" + PASSWORD])
    attempts: list[int] = []

    def ask(attempt: int) -> str | None:
        attempts.append(attempt)
        return next(answers)

    doc, password = pages.open_source(str(encrypted_pdf), ask)
    assert password == "owner-" + PASSWORD
    assert attempts == [0, 1]
    assert doc.page_count == 2
    doc.close()


def test_open_source_cancel_and_plain(encrypted_pdf, simple_pdf) -> None:
    with pytest.raises(PasswordRequired):
        pages.open_source(str(encrypted_pdf), lambda attempt: None)
    doc, password = pages.open_source(str(simple_pdf))
    assert password is None and doc.page_count == 3
    doc.close()


# -- extraction and its protection --------------------------------------------------
def test_extract_from_snapshot_of_edited_form(lo_form_pdf, tmp_path) -> None:
    live = PdfDocument.open(lo_form_pdf)
    fixtures.fill_lo_form(live)
    with live.lock:
        before = _without_id(live.fitz.tobytes(garbage=0, deflate=False))
    data = live.snapshot()
    out_path = tmp_path / "extract.pdf"
    out_path.write_bytes(pages.extract_bytes(pymupdf.open(stream=data), [0]))
    with live.lock:
        assert _without_id(live.fitz.tobytes(garbage=0, deflate=False)) == before
    result = strict_read(out_path)
    assert len(result.fields) == 12
    out = pymupdf.open(out_path)
    values = {w.field_name: w.field_value for w in out[0].widgets()}
    assert values["Nom"] == "Dupont"
    assert values["Zone de texte 8_54"] == "Élève à Noël €"
    live.close()


def test_output_encryption_policy() -> None:
    plain = pages.output_encryption(
        is_encrypted=False, has_restrictions=False, password=None, permissions=-4
    )
    assert plain == pages.NO_ENCRYPTION
    user = pages.output_encryption(
        is_encrypted=True, has_restrictions=False, password=PASSWORD, permissions=-4
    )
    assert (user.method, user.user_pw, user.owner_pw) == (
        pymupdf.PDF_ENCRYPT_AES_256,
        PASSWORD,
        PASSWORD,
    )
    owner = pages.output_encryption(
        is_encrypted=False, has_restrictions=True, password=None, permissions=-3900
    )
    assert owner.user_pw == "" and len(owner.owner_pw) >= 16
    assert owner.permissions == -3900


def test_extract_bytes_encryption_outputs(tmp_path) -> None:
    src = _labelled(3)
    user = pages.output_encryption(
        is_encrypted=True, has_restrictions=False, password=PASSWORD, permissions=-4
    )
    out = pymupdf.open(stream=pages.extract_bytes(src, [1], encryption=user))
    assert out.needs_pass
    assert out.authenticate(PASSWORD)
    assert _labels(out) == "B"

    perms = pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY
    owner = pages.output_encryption(
        is_encrypted=False, has_restrictions=True, password=None, permissions=perms
    )
    data = pages.extract_bytes(_labelled(3), [2, 0], encryption=owner)
    out = pymupdf.open(stream=data)
    assert not out.needs_pass
    assert out.metadata["encryption"]
    assert not out.permissions & pymupdf.PDF_PERM_MODIFY
    assert out.permissions & pymupdf.PDF_PERM_COPY
    assert _labels(out) == "CA"
    assert not out.authenticate(OWNER_PASSWORD)

    out = pymupdf.open(stream=pages.extract_bytes(_labelled(2), [0]))
    assert not out.metadata["encryption"]


# -- SnapshotStore -------------------------------------------------------------------
def test_snapshot_store_memory_then_spill(tmp_path) -> None:
    store = SnapshotStore(memory_limit=10, directory=tmp_path)
    a = store.put(b"12345")
    b = store.put(b"67890")
    assert store.bytes_in_memory == 10 and store.on_disk_count == 0
    c = store.put(b"abc")
    assert store.on_disk_count == 1
    spill = store.spill_directory
    assert spill is not None and spill.parent == tmp_path
    assert spill.name.startswith(SPILL_PREFIX)
    assert (spill / f"{c}.bin").read_bytes() == b"abc"
    assert [store.get(x) for x in (a, b, c)] == [b"12345", b"67890", b"abc"]
    assert len(store) == 3
    store.discard(c)
    assert not (spill / f"{c}.bin").exists()
    store.discard(a)
    store.discard(999)
    assert store.bytes_in_memory == 5 and len(store) == 1
    with pytest.raises(KeyError):
        store.get(a)
    store.put(b"0123456789")
    store.clear()
    assert len(store) == 0 and store.bytes_in_memory == 0
    assert not spill.exists() and store.spill_directory is None


def test_snapshot_store_spill_failure(tmp_path, monkeypatch) -> None:
    store = SnapshotStore(memory_limit=0, directory=tmp_path)

    def fail(self: Path, data: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError):
        store.put(b"data")
    assert len(store) == 0
    monkeypatch.undo()
    store.clear()


def test_inserted_widgets_survive_full_save_distinct(simple_pdf, lo_form_pdf) -> None:
    """MuPDF's graft drops /P; without it garbage=3 merged Nom's two kids into one."""
    doc = pymupdf.open(simple_pdf)
    pages.insert_pages(doc, pymupdf.open(lo_form_pdf), 3, had_xfa=False)
    for i in (3, 4):
        for xref, _, _ in doc[i].annot_xrefs():
            assert doc.xref_get_key(xref, "P")[1] == f"{doc.page_xref(i)} 0 R"
    saved = pymupdf.open(stream=doc.tobytes(garbage=3, deflate=True))
    noms = [w.xref for p in saved for w in p.widgets() if w.field_name == "Nom"]
    assert len(noms) == 2 and len(set(noms)) == 2


def test_prune_fields_duplicate_kid_refs() -> None:
    doc = _labelled(1)
    page = doc[0]
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "kid"
    widget.rect = pymupdf.Rect(72, 100, 200, 120)
    page.add_widget(widget)
    kid = page.annot_xrefs()[0][0]
    parent = doc.get_new_xref()
    doc.update_object(parent, f"<< /T (p) /FT /Tx /Kids [{kid} 0 R {kid} 0 R] >>")
    doc.xref_set_key(kid, "Parent", f"{parent} 0 R")
    doc.xref_set_key(kid, "T", "null")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", f"[{parent} 0 R]")  # direct dict
    assert pages.prune_fields(doc) == 1
    assert _kids(doc, parent) == [kid]
    doc.new_page()
    doc.delete_page(0)
    assert pages.prune_fields(doc) == 2
    assert _fields(doc) == []
