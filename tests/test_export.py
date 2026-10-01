"""M5-T1: PdfDocument.export_copy (flattened copies, clean copies, encryption and
metadata options) - docs/M5_PLAN.md 1.2-1.4 and 4 "M5-T1"."""

from __future__ import annotations

import os
import re
import time
import zlib
from dataclasses import replace

import fixtures
import pymupdf
import pytest
from fixtures import PASSWORD, fill_lo_form, render_diff
from pdfcheck import strict_read
from PySide6.QtCore import QPointF, QRectF

from pdfeditor.core import document as document_module
from pdfeditor.core.annotations import AnnotKind, AnnotSpec, stamp_rect
from pdfeditor.core.document import ExportOptions, PdfDocument, SaveError
from pdfeditor.core.forms import XfaKind

pytest.importorskip("pypdf")
pytest.importorskip("cryptography")  # AES-256 decryption in pypdf

pytestmark = pytest.mark.usefixtures("qapp")

SCALE = 2.0
#: "Renders identically" (docs/M5_PLAN.md 1.4): max channel difference and share of
#: differing channels.
MAX_ABS = 3
MAX_SHARE = 0.005
FLATTEN_NONE = ExportOptions(flatten_forms=False, flatten_annots=False)
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


# -- helpers ---------------------------------------------------------------------------
def _pixmaps(pdf: pymupdf.Document) -> list[pymupdf.Pixmap]:
    matrix = pymupdf.Matrix(SCALE, SCALE)
    return [page.get_pixmap(matrix=matrix, alpha=False, annots=True) for page in pdf]


def _doc_pixmaps(doc: PdfDocument) -> list[pymupdf.Pixmap]:
    with doc.lock:
        return _pixmaps(doc.fitz)


def _open(path, password: str | None = None) -> pymupdf.Document:
    pdf = pymupdf.open(path)
    assert not pdf.is_repaired
    if pdf.needs_pass:
        assert pdf.authenticate(password or "")
    return pdf


def _assert_same_render(before: list[pymupdf.Pixmap], after: list[pymupdf.Pixmap]) -> None:
    assert len(before) == len(after)
    for i, (a, b) in enumerate(zip(before, after, strict=True)):
        count, largest = render_diff(a, b)
        assert largest <= MAX_ABS, (i, count, largest)
        assert count < MAX_SHARE * len(a.samples), (i, count, largest)


def _acroform(pdf: pymupdf.Document) -> tuple[str, str]:
    return pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm")


def _acroform_key(pdf: pymupdf.Document, key: str) -> tuple[str, str]:
    kind, value = _acroform(pdf)
    assert kind == "xref", (kind, value)
    return pdf.xref_get_key(int(value.split()[0]), key)


def _text(pdf: pymupdf.Document) -> str:
    return "\n".join(page.get_text() for page in pdf)


def _annot_counts(pdf: pymupdf.Document) -> list[int]:
    return [len(list(page.annots())) for page in pdf]


def _widget_counts(pdf: pymupdf.Document) -> list[int]:
    return [len(list(page.widgets())) for page in pdf]


def _raw_contains(path, needle: bytes) -> bool:
    """``needle`` in the raw bytes or in any Flate-decoded stream of the file."""
    data = path.read_bytes()
    if needle in data:
        return True
    for m in _STREAM.finditer(data):
        try:
            if needle in zlib.decompress(m.group(1)):
                return True
        except zlib.error:
            continue
    return False


def _reset_mupdf_warnings() -> None:
    pymupdf.TOOLS.mupdf_warnings()


# -- forms -----------------------------------------------------------------------------
def test_filled_lo_form_flattened(lo_form_pdf, tmp_path) -> None:
    doc = PdfDocument.open(lo_form_pdf)
    fill_lo_form(doc)
    before = _doc_pixmaps(doc)
    out = tmp_path / "flat.pdf"
    _reset_mupdf_warnings()
    doc.export_copy(out)
    assert pymupdf.TOOLS.mupdf_warnings() == ""
    doc.close()

    pdf = _open(out)
    try:
        assert _widget_counts(pdf) == [0, 0]
        assert not pdf.is_form_pdf
        assert _acroform(pdf)[0] == "null"
        _assert_same_render(before, _pixmaps(pdf))
        text = _text(pdf)
        for value in ("Élève à Noël €", "ligne 1", "ligne 2", "Madame", "Vert", "Dupont"):
            assert value in text, value
        assert "Dupont" in pdf[1].get_text()  # the kid widget on page 2
    finally:
        pdf.close()
    assert strict_read(out).fields is None


def test_need_appearances_form_matches_authored_appearances(tmp_path) -> None:
    path = fixtures.make_lo_form_pdf(tmp_path / "na.pdf", need_appearances=True)
    reference = pymupdf.open(path)
    assert _acroform_key(reference, "NeedAppearances") == ("bool", "true")
    acro = int(_acroform(reference)[1].split()[0])
    reference.xref_set_key(acro, "NeedAppearances", "null")
    expected = _pixmaps(reference)
    reference.close()

    doc = PdfDocument.open(path)
    out = tmp_path / "na_flat.pdf"
    doc.export_copy(out)
    doc.close()
    pdf = _open(out)
    try:
        assert _widget_counts(pdf) == [0, 0]
        got = _pixmaps(pdf)
        _assert_same_render(expected, got)
        # The authored "Off" appearance (a grey square) is drawn at the unchecked boxes.
        for page, name, _kind, rect in fixtures.LO_EDITABLE:
            if name not in ("Case à cocher 1_2", "Case à cocher 2_1"):
                continue
            x0, y0, _x1, y1 = rect
            pix = got[page]
            x, y = int((x0 + 0.5) * SCALE), int((y0 + y1) / 2 * SCALE)
            darkest = min(min(pix.pixel(x + dx, y)) for dx in (-1, 0, 1))
            assert darkest < 200, (name, darkest)
    finally:
        pdf.close()
    strict_read(out)


def test_flatten_forms_only_keeps_annotations(annotated_pdf, tmp_path) -> None:
    doc = PdfDocument.open(annotated_pdf)
    with doc.lock:
        annots_before = _annot_counts(doc.fitz)
    out = tmp_path / "forms_only.pdf"
    doc.export_copy(out, ExportOptions(flatten_forms=True, flatten_annots=False))
    doc.close()
    pdf = _open(out)
    try:
        assert _widget_counts(pdf) == [0, 0]
        assert not pdf.is_form_pdf
        assert _annot_counts(pdf) == annots_before
        assert annots_before[0] > 0
    finally:
        pdf.close()
    strict_read(out)


def test_flatten_annots_only_keeps_fields(annotated_pdf, tmp_path) -> None:
    doc = PdfDocument.open(annotated_pdf)
    before = _doc_pixmaps(doc)
    with doc.lock:
        widgets_before = _widget_counts(doc.fitz)
    out = tmp_path / "annots_only.pdf"
    doc.export_copy(out, ExportOptions(flatten_forms=False, flatten_annots=True))
    doc.close()
    pdf = _open(out)
    try:
        assert _annot_counts(pdf) == [0, 0]
        assert _widget_counts(pdf) == widgets_before == [1, 0]
        assert pdf.is_form_pdf
        _assert_same_render(before, _pixmaps(pdf))
    finally:
        pdf.close()
    strict_read(out)


# -- annotations -----------------------------------------------------------------------
def _add_text_and_stamps(doc: PdfDocument, page: int, top: float) -> None:
    doc.add_annot(
        AnnotSpec(
            page, AnnotKind.TEXT, "new ā 中 Œuvre €", 11.0, (0, 0, 1), QRectF(100, top, 200, 20)
        ),
        fit_height=True,
    )
    for x, code, colour in ((300, "4", (1, 0, 0)), (320, "8", (0, 0.5, 0)), (340, "l", (0, 0, 0))):
        rect, size = stamp_rect(QPointF(x, top + 50), 12.0, code)
        doc.add_annot(AnnotSpec(page, AnnotKind.STAMP, code, size, colour, rect))


def test_annotations_flattened_identically(annotated_pdf, tmp_path) -> None:
    doc = PdfDocument.open(annotated_pdf)
    _add_text_and_stamps(doc, 0, 400)
    doc.add_annot(
        AnnotSpec(1, AnnotKind.TEXT, "rotated page é", 11.0, (0, 0, 0), QRectF(50, 300, 200, 20)),
        fit_height=True,
    )
    before = _doc_pixmaps(doc)
    out = tmp_path / "ann_flat.pdf"
    _reset_mupdf_warnings()
    doc.export_copy(out)
    assert pymupdf.TOOLS.mupdf_warnings() == ""
    doc.close()
    pdf = _open(out)
    try:
        assert _annot_counts(pdf) == [0, 0]
        assert _widget_counts(pdf) == [0, 0]
        _assert_same_render(before, _pixmaps(pdf))
        text = _text(pdf)
        assert fixtures.ANNOT_TEXT in text
        assert fixtures.ROTATED_ANNOT_TEXT in text
        assert "hidden" not in text  # hidden annotations are not flattened
    finally:
        pdf.close()
    strict_read(out)


def test_rotated_cropbox_page_flattened_identically(tmp_path) -> None:
    path = fixtures.make_word_form_pdf(tmp_path / "word.pdf", rotate=90, cropbox=True)
    doc = PdfDocument.open(path)
    _add_text_and_stamps(doc, 0, 100)
    before = _doc_pixmaps(doc)
    out = tmp_path / "word_flat.pdf"
    doc.export_copy(out)
    doc.close()
    pdf = _open(out)
    try:
        assert _annot_counts(pdf) == [0]
        assert pdf[0].rotation == 90
        _assert_same_render(before, _pixmaps(pdf))
    finally:
        pdf.close()
    strict_read(out)


def test_links_survive_flattening(tmp_path) -> None:
    path = fixtures.make_links_pdf(tmp_path / "links.pdf")
    doc = PdfDocument.open(path)
    with doc.lock:
        links_before = [len(page.get_links()) for page in doc.fitz]
        assert _annot_counts(doc.fitz)[0] == 2  # sticky note + highlight
        assert _widget_counts(doc.fitz)[0] == 1  # unsigned signature field
    before = _doc_pixmaps(doc)
    out = tmp_path / "links_flat.pdf"
    doc.export_copy(out)
    doc.close()
    pdf = _open(out)
    try:
        assert [len(page.get_links()) for page in pdf] == links_before == [2, 0]
        uris = [link.get("uri") for link in pdf[0].get_links()]
        assert fixtures.LINK_URI in uris
        assert _annot_counts(pdf) == [0, 0]
        assert _widget_counts(pdf) == [0, 0]
        _assert_same_render(before, _pixmaps(pdf))
    finally:
        pdf.close()
    strict_read(out)


def _image_objects(pdf: pymupdf.Document) -> tuple[set[int], set[bytes]]:
    """Image xrefs drawn on the pages (form XObjects included) and the distinct image
    objects they are (dictionary without the /SMask reference, stream, soft mask)."""
    xrefs: set[int] = set()
    contents: set[bytes] = set()
    for page in pdf:
        for xref, smask, *_rest in page.get_images(full=True):
            if xref in xrefs:
                continue
            xrefs.add(xref)
            head = re.sub(r"/SMask \d+ 0 R", "", pdf.xref_object(xref, compressed=True))
            mask = pdf.xref_stream_raw(smask) if smask else b""
            contents.add(head.encode() + pdf.xref_stream_raw(xref) + b"|" + mask)
    return xrefs, contents


def test_signatures_flattened_with_alpha(signed_pdf, tmp_path) -> None:
    doc = PdfDocument.open(signed_pdf)
    before = _doc_pixmaps(doc)
    out = tmp_path / "signed_flat.pdf"
    _reset_mupdf_warnings()
    doc.export_copy(out)
    assert pymupdf.TOOLS.mupdf_warnings() == ""
    doc.close()
    pdf = _open(out)
    try:
        assert _annot_counts(pdf) == [0, 0]
        _assert_same_render(before, _pixmaps(pdf))
        # Our signature image is shared by three stamps of page 1; MuPDF's own stamp
        # image (ICC colour space, 1-bit mask) and the pre-rotated one of page 2 differ.
        xrefs, contents = _image_objects(pdf)
        assert len(xrefs) == len(contents) == 3  # identical images are stored once
        masks = [smask for page in pdf for _x, smask, *_r in page.get_images(full=True)]
        assert all(masks), masks  # every signature keeps its transparency
    finally:
        pdf.close()
    strict_read(out)


def test_odd_stamps_flattened_without_warnings(odd_stamps_pdf, tmp_path) -> None:
    doc = PdfDocument.open(odd_stamps_pdf)
    before = _doc_pixmaps(doc)
    out = tmp_path / "odd_flat.pdf"
    _reset_mupdf_warnings()
    doc.export_copy(out)
    assert pymupdf.TOOLS.mupdf_warnings() == ""
    doc.close()
    pdf = _open(out)
    try:
        assert _annot_counts(pdf) == [0]
        _assert_same_render(before, _pixmaps(pdf))
    finally:
        pdf.close()
    strict_read(out)


# -- encryption and metadata -------------------------------------------------------------
def test_aes256_copy_keeps_password(lo_form_encrypted_pdf, tmp_path) -> None:
    doc = PdfDocument.open(lo_form_encrypted_pdf, password=PASSWORD)
    assert doc.encryption_method is not None
    assert not doc.has_restrictions
    fill_lo_form(doc)
    before = _doc_pixmaps(doc)
    out = tmp_path / "enc_flat.pdf"
    doc.export_copy(out)
    doc.close()
    pdf = pymupdf.open(out)
    try:
        assert pdf.needs_pass
        assert pdf.authenticate(PASSWORD)
        assert "AES" in (pdf.metadata.get("encryption") or "")
        assert _widget_counts(pdf) == [0, 0]
        _assert_same_render(before, _pixmaps(pdf))
    finally:
        pdf.close()
    assert strict_read(out, PASSWORD).fields is None


def test_copy_without_password(lo_form_full_access_pdf, tmp_path) -> None:
    doc = PdfDocument.open(lo_form_full_access_pdf, password=PASSWORD)
    assert not doc.has_owner_access
    assert not doc.must_keep_encryption  # user password, full permissions
    fill_lo_form(doc)
    out = tmp_path / "enc_none.pdf"
    doc.export_copy(out, ExportOptions(keep_encryption=False))
    doc.close()
    pdf = pymupdf.open(out)
    try:
        assert not pdf.needs_pass
        assert not pdf.metadata.get("encryption")
        assert "Madame" in _text(pdf)
    finally:
        pdf.close()
    strict_read(out)


@pytest.mark.parametrize("kind", ["owner_only", "owner_locked"])
@pytest.mark.parametrize("keep", [True, False])
def test_owner_restrictions_kept(tmp_path, kind, keep) -> None:
    if kind == "owner_only":
        path = fixtures.make_lo_form_pdf(tmp_path / "owner.pdf", owner_only=True)
    else:
        path = fixtures.make_owner_locked_pdf(tmp_path / "locked.pdf")
    doc = PdfDocument.open(path)
    assert doc.has_restrictions and doc.must_keep_encryption
    assert doc.encryption_method is not None
    permissions = doc.permissions
    out = tmp_path / "copy.pdf"
    doc.export_copy(out, ExportOptions(keep_encryption=keep))
    doc.close()
    pdf = pymupdf.open(out)
    try:
        assert not pdf.needs_pass
        assert pdf.metadata.get("encryption")
        assert pdf.permissions == permissions
    finally:
        pdf.close()
    strict_read(out)


def test_user_password_with_restrictions_keeps_protection(lo_form_encrypted_pdf, tmp_path):
    """Review finding 4: a user-password file whose author restricted it (no copy, no
    modify...) keeps its protection when opened with the user password."""
    doc = PdfDocument.open(lo_form_encrypted_pdf, password=PASSWORD)
    assert doc.is_encrypted and not doc.has_restrictions and not doc.has_owner_access
    assert doc.must_keep_encryption
    permissions = doc.permissions
    assert permissions & fixtures.LO_PERMISSIONS == fixtures.LO_PERMISSIONS
    fill_lo_form(doc)
    out = tmp_path / "restricted.pdf"
    doc.export_copy(out, ExportOptions(keep_encryption=False))
    doc.close()
    pdf = pymupdf.open(out)
    try:
        assert pdf.needs_pass
        assert pdf.authenticate(PASSWORD) == 2  # still the same user password
        assert pdf.permissions == permissions
        assert "Madame" in _text(pdf)
    finally:
        pdf.close()
    pdf = pymupdf.open(out)
    try:
        assert pdf.authenticate("owner-" + PASSWORD) & 4  # owner password unchanged
    finally:
        pdf.close()


def test_owner_password_allows_removing_protection(lo_form_encrypted_pdf, tmp_path) -> None:
    doc = PdfDocument.open(lo_form_encrypted_pdf, password="owner-" + PASSWORD)
    assert doc.is_encrypted and doc.has_owner_access
    assert not doc.must_keep_encryption
    out = tmp_path / "unprotected.pdf"
    doc.export_copy(out, ExportOptions(keep_encryption=False))
    doc.close()
    pdf = pymupdf.open(out)
    try:
        assert not pdf.needs_pass
        assert not pdf.metadata.get("encryption")
    finally:
        pdf.close()
    strict_read(out)


def test_plain_document_has_no_encryption(simple_pdf) -> None:
    doc = PdfDocument.open(simple_pdf)
    assert doc.encryption_method is None
    assert not doc.has_restrictions
    doc.close()


@pytest.fixture
def metadata_pdf(tmp_path):
    path = tmp_path / "meta.pdf"
    pdf = pymupdf.open(fixtures.make_simple_pdf(tmp_path / "plain.pdf"))
    pdf.set_metadata({"title": "Titre é", "author": "Moi", "subject": "Sujet"})
    pdf.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/">xmp-marker</x:xmpmeta>')
    pdf.save(path)
    pdf.close()
    return path


@pytest.mark.parametrize("keep", [True, False])
def test_metadata_kept_or_cleared(metadata_pdf, tmp_path, keep) -> None:
    doc = PdfDocument.open(metadata_pdf)
    out = tmp_path / "copy.pdf"
    doc.export_copy(out, ExportOptions(keep_metadata=keep))
    doc.close()
    pdf = pymupdf.open(out)
    try:
        if keep:
            assert pdf.metadata["title"] == "Titre é"
            assert pdf.metadata["author"] == "Moi"
            assert "xmp-marker" in pdf.get_xml_metadata()
        else:
            assert not pdf.metadata["title"]
            assert not pdf.metadata["author"]
            assert not pdf.metadata["subject"]
            assert pdf.get_xml_metadata() == ""
    finally:
        pdf.close()
    assert _raw_contains(out, b"xmp-marker") is keep
    strict_read(out)


@pytest.fixture
def private_metadata_pdf(tmp_path):
    """Info (standard + custom key), catalog XMP, a page XMP stream, page and catalog
    /PieceInfo: every place where a document keeps private metadata."""
    path = tmp_path / "private_meta.pdf"
    pdf = pymupdf.open(fixtures.make_simple_pdf(tmp_path / "plain.pdf"))
    pdf.set_metadata({"title": "TITLE-SECRET", "author": "AUTHOR-SECRET", "producer": "PROD"})
    info = int(re.search(r"/Info\s+(\d+)\s+0\s+R", pdf.pdf_trailer()).group(1))
    pdf.xref_set_key(info, "Company", "(COMPANY-SECRET)")
    pdf.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/">CATALOG-XMP-SECRET</x:xmpmeta>')
    page_xmp = pdf.get_new_xref()
    pdf.update_object(page_xmp, "<</Type/Metadata/Subtype/XML>>")
    pdf.update_stream(page_xmp, b"PAGE-XMP-SECRET", compress=False)
    for page in pdf:
        pdf.xref_set_key(page.xref, "Metadata", f"{page_xmp} 0 R")
        pdf.xref_set_key(page.xref, "PieceInfo", "<</Illustrator<</Private(PAGE-PIECE-SECRET)>>>>")
    pdf.xref_set_key(pdf.pdf_catalog(), "PieceInfo", "<</App<</Private(CAT-PIECE-SECRET)>>>>")
    pdf.save(path)
    pdf.close()
    return path


PRIVATE_NEEDLES = [
    b"TITLE-SECRET",
    b"AUTHOR-SECRET",
    b"COMPANY-SECRET",
    b"CATALOG-XMP-SECRET",
    b"PAGE-XMP-SECRET",
    b"PAGE-PIECE-SECRET",
    b"CAT-PIECE-SECRET",
    b"/PieceInfo",
    b"/Metadata",
    b"/Producer",
]


@pytest.mark.parametrize("flatten", [True, False])
@pytest.mark.parametrize("keep", [True, False])
def test_private_metadata_raw_bytes(private_metadata_pdf, tmp_path, keep, flatten) -> None:
    for needle in PRIVATE_NEEDLES:
        assert _raw_contains(private_metadata_pdf, needle), needle
    doc = PdfDocument.open(private_metadata_pdf)
    out = tmp_path / "copy.pdf"
    options = replace(ExportOptions() if flatten else FLATTEN_NONE, keep_metadata=keep)
    doc.export_copy(out, options)
    doc.close()
    found = [needle for needle in PRIVATE_NEEDLES if _raw_contains(out, needle)]
    if keep:
        assert found == PRIVATE_NEEDLES
    else:
        assert found == []
        with pymupdf.open(out) as pdf:
            assert "Info" not in pdf.pdf_trailer()
            assert pdf[0].get_text().strip() != ""  # the content is untouched
    strict_read(out)


# -- XFA -------------------------------------------------------------------------------
def test_static_xfa_edited_loses_xfa_in_copy(static_xfa_pdf, tmp_path) -> None:
    doc = PdfDocument.open(static_xfa_pdf)
    assert doc.xfa_kind is XfaKind.STATIC
    w = doc.widgets(0)[0]
    doc.set_field_value(0, w.xref, "nouveau")
    out = tmp_path / "xfa_copy.pdf"
    doc.export_copy(out, FLATTEN_NONE)
    assert doc.xfa_kind is XfaKind.STATIC  # the open document keeps it
    doc.close()
    pdf = _open(out)
    try:
        assert pdf.is_form_pdf
        assert _acroform_key(pdf, "XFA")[0] == "null"
        assert [w.field_value for w in pdf[0].widgets()] == ["nouveau"]
    finally:
        pdf.close()
    strict_read(out)


def test_static_xfa_not_edited_keeps_xfa_without_flattening(static_xfa_pdf, tmp_path) -> None:
    doc = PdfDocument.open(static_xfa_pdf)
    out = tmp_path / "xfa_copy.pdf"
    doc.export_copy(out, FLATTEN_NONE)
    doc.close()
    pdf = _open(out)
    try:
        assert _acroform_key(pdf, "XFA")[0] == "array"
    finally:
        pdf.close()


def test_dynamic_xfa_is_never_flattened(dynamic_xfa_pdf, tmp_path) -> None:
    doc = PdfDocument.open(dynamic_xfa_pdf)
    assert doc.xfa_kind is XfaKind.DYNAMIC
    out = tmp_path / "dxfa_copy.pdf"
    doc.export_copy(out)  # flatten_forms is ignored
    doc.close()
    pdf = _open(out)
    try:
        assert _acroform(pdf)[0] != "null"
        assert _acroform_key(pdf, "XFA")[0] != "null"
    finally:
        pdf.close()


# -- the open document -------------------------------------------------------------------
def test_unsaved_edits_exported_original_untouched(simple_pdf, tmp_path) -> None:
    original = simple_pdf.read_bytes()
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 90)
    info = doc.add_annot(
        AnnotSpec(0, AnnotKind.TEXT, "unsaved text", 11.0, (0, 0, 0), QRectF(100, 300, 200, 20)),
        fit_height=True,
    )
    events: list[str] = []
    doc.page_changed.connect(lambda i: events.append(f"page {i}"))
    doc.structure_changed.connect(lambda: events.append("structure"))
    doc.path_changed.connect(lambda p: events.append("path"))
    doc.reloaded.connect(lambda: events.append("reloaded"))
    out = tmp_path / "copy.pdf"
    doc.export_copy(out, FLATTEN_NONE)
    assert events == []
    assert doc.path == str(simple_pdf)
    assert simple_pdf.read_bytes() == original
    assert not simple_pdf.with_name("simple.pdf.tmp").exists()
    # Snapshots and names stay valid: the copy did not renumber the open document.
    assert doc.annot(0, info.name) is not None
    doc.update_annot(0, info.name, text="changed after export")
    doc.close()
    pdf = _open(out)
    try:
        assert pdf[0].rotation == 90
        texts = [a.info["content"] for a in pdf[0].annots()]
        assert texts == ["unsaved text"]
    finally:
        pdf.close()
    strict_read(out)


def _fill(doc: PdfDocument, name: str, value: str) -> None:
    w = next(x for x in doc.all_widgets() if x.name == name)
    doc.set_field_value(w.page, w.xref, value, name=w.name, unrotated_rect=w.unrotated_rect)


def test_next_save_after_export_is_full_then_incremental(lo_form_pdf, tmp_path) -> None:
    doc = PdfDocument.open(lo_form_pdf)
    _fill(doc, "Zone de texte 8_54", "avant export")
    assert doc.can_save_incrementally()
    doc.export_copy(tmp_path / "copy.pdf")
    assert not doc.can_save_incrementally()  # C1: the in-memory write forces a full save
    _fill(doc, "Nom", "apres export")
    doc.save()
    saved = lo_form_pdf.read_bytes()
    assert saved.count(b"%%EOF") == 1  # full rewrite
    fields = strict_read(lo_form_pdf).fields
    assert fields["Zone de texte 8_54"]["/V"] == "avant export"
    assert fields["Nom"]["/V"] == "apres export"
    assert doc.can_save_incrementally()
    doc.set_page_rotation(1, 90)
    doc.save()
    assert lo_form_pdf.read_bytes().startswith(saved)  # incremental again
    strict_read(lo_form_pdf)
    doc.close()
    reopened = _open(lo_form_pdf)
    try:
        assert reopened[1].rotation == 90
    finally:
        reopened.close()


def test_export_to_own_path_refused(simple_pdf) -> None:
    original = simple_pdf.read_bytes()
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 90)
    with pytest.raises(ValueError):
        doc.export_copy(simple_pdf)
    variant = os.path.join(str(simple_pdf.parent).upper(), ".", simple_pdf.name)
    with pytest.raises(ValueError):
        doc.export_copy(variant)
    assert doc.can_save_incrementally()  # nothing was written
    doc.close()
    assert simple_pdf.read_bytes() == original


def test_write_failure_raises_save_error_without_temp_file(simple_pdf, tmp_path, monkeypatch):
    doc = PdfDocument.open(simple_pdf)
    doc.set_page_rotation(0, 90)

    def locked(src, dst):
        raise PermissionError("file is locked")

    monkeypatch.setattr(document_module, "REPLACE_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(os, "replace", locked)
    out = tmp_path / "copy.pdf"
    with pytest.raises(SaveError):
        doc.export_copy(out)
    monkeypatch.undo()
    assert not out.exists()
    assert not out.with_name("copy.pdf.tmp").exists()
    assert doc.page_rotation(0) == 90
    doc.export_copy(out)  # works once the file is free
    doc.close()
    pdf = _open(out)
    try:
        assert pdf[0].rotation == 90
    finally:
        pdf.close()


def test_clean_copy_drops_earlier_revisions(simple_pdf, tmp_path) -> None:
    """Deviation 42: a deleted annotation stays in the earlier revision of an
    incrementally saved file; a clean copy (no flattening) does not carry it."""
    secret = b"SECRET-TEXT-123"
    doc = PdfDocument.open(simple_pdf)
    info = doc.add_annot(
        AnnotSpec(0, AnnotKind.TEXT, secret.decode(), 11.0, (0, 0, 0), QRectF(100, 300, 200, 20)),
        fit_height=True,
    )
    doc.save()
    doc.delete_annot(0, info.name)
    doc.save()
    assert _raw_contains(simple_pdf, secret)
    out = tmp_path / "clean.pdf"
    doc.export_copy(out, FLATTEN_NONE)
    doc.close()
    assert not _raw_contains(out, secret)
    strict_read(out)


def test_export_of_many_fields_is_fast(many_fields_pdf, tmp_path) -> None:
    doc = PdfDocument.open(many_fields_pdf)
    start = time.perf_counter()
    doc.export_copy(tmp_path / "many.pdf")
    elapsed = time.perf_counter() - start
    doc.close()
    assert elapsed < 1.0, elapsed
    pdf = _open(tmp_path / "many.pdf")
    try:
        assert sum(_widget_counts(pdf)) == 0
    finally:
        pdf.close()


def test_export_options_defaults() -> None:
    options = ExportOptions()
    assert options.flatten_forms and options.flatten_annots
    assert options.keep_encryption and options.keep_metadata
    with pytest.raises(AttributeError):
        options.flatten_forms = False  # type: ignore[misc]
