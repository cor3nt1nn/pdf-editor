"""M8-T2: the searchable OCR layer (core/ocr_layer.py), PdfDocument's OCR API, the page
text plug and AddOcrLayerCommand."""

from __future__ import annotations

import shutil

import pymupdf
import pytest
from pdfcheck import strict_read
from PySide6.QtGui import QUndoStack
from scan_fixtures import copy_to

from pdfeditor.core import ocr
from pdfeditor.core.commands import AddOcrLayerCommand
from pdfeditor.core.document import ExportOptions, PdfDocument
from pdfeditor.core.ocr import OcrError, PageOcr
from pdfeditor.core.ocr_layer import LAYER_MARKER, PREFIX_MARKER

PASSWORD = "secret"


@pytest.fixture(scope="module")
def clean_ocr(scan_clean) -> PageOcr:
    with pymupdf.open(str(scan_clean.path)) as doc:
        request = ocr.render_request(doc[0])
    return ocr.recognise(request)


def _words(page: pymupdf.Page) -> list[tuple]:
    """Words of the page as displayed (page space)."""
    textpage = pymupdf.TextPage(page.get_displaylist(annots=False).get_textpage(0))
    return textpage.extractWORDS()


def _shown(text: str) -> str:
    return text.encode("cp1252", "replace").decode("cp1252")


def _matched(result: PageOcr, words: list[tuple], tol: float = 2.0) -> int:
    pool = {}
    for w in words:
        pool.setdefault(w[4], []).append(w[:4])
    hits = 0
    for word in result.words:
        boxes = pool.get(_shown(word.text), [])
        if any(
            all(abs(a - b) <= tol for a, b in zip(box, word.rect, strict=True)) for box in boxes
        ):
            hits += 1
    return hits


def _pixels(doc: PdfDocument, i: int = 0) -> bytes:
    image = doc.render(i, 1.0)
    return bytes(image.constBits())


def _rotated_copy(src, tmp_path, rotation: int, *, cropbox: bool = False):
    path = tmp_path / f"scan_{rotation}{'_crop' if cropbox else ''}.pdf"
    with pymupdf.open(str(src)) as doc:
        if cropbox:
            doc[0].set_cropbox(pymupdf.Rect(20, 30, 575, 812))
        doc[0].set_rotation(rotation)
        doc.save(str(path))
    return path


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_layer_words_land_on_the_ocr_boxes(scan_clean, clean_ocr, tmp_path, rotation) -> None:
    path = _rotated_copy(scan_clean.path, tmp_path, rotation)
    doc = PdfDocument.open(path)
    before = _pixels(doc)
    result = clean_ocr.rotated(rotation)
    assert doc.add_ocr_layer(0, result) == len(result.words)
    with doc.lock:
        words = _words(doc.fitz[0])
    assert len(words) == len(result.words)
    assert _matched(result, words) >= 0.97 * len(result.words)
    assert _pixels(doc) == before  # invisible: not one pixel changes
    doc.close()


def test_layer_on_a_cropped_rotated_page(scan_rotated, tmp_path) -> None:
    """A scan stored sideways in a /Rotate 90 page with a cropbox: recognised and
    written in the cropped, rotated page space."""
    path = tmp_path / "cropped.pdf"
    with pymupdf.open(str(scan_rotated.path)) as src:
        src[0].set_cropbox(pymupdf.Rect(30, 20, 812, 575))  # unrotated coordinates
        src.save(str(path))
    doc = PdfDocument.open(path)
    result = ocr.recognise(doc.ocr_request(0, 150))
    assert (result.width, result.height, result.rotation) == (555.0, 782.0, 90)
    assert len(result.words) > 100
    doc.add_ocr_layer(0, result)
    with doc.lock:
        words = _words(doc.fitz[0])
    assert _matched(result, words) >= 0.97 * len(result.words)
    doc.close()


def test_layer_is_text_for_every_reader(scan_clean, clean_ocr, tmp_path) -> None:
    path = copy_to(scan_clean, tmp_path)
    size = path.stat().st_size
    doc = PdfDocument.open(path)
    assert not doc.has_ocr_layer(0)
    assert not doc.has_content_text(0)
    doc.add_ocr_layer(0, clean_ocr)
    assert doc.has_ocr_layer(0)
    text = doc.page_text(0)
    assert text.source == "content"
    assert doc.has_content_text(0)
    assert all(text.is_invisible(i) for i in range(len(text.chars)))
    assert all(span.invisible for line in text.lines for span in line.spans)
    assert text.lines[0].text.startswith("Formulaire de demande")
    assert doc.can_save_incrementally()
    doc.save()
    assert path.stat().st_size - size < 10_000  # ~3 KB of compressed text
    doc.close()
    with pymupdf.open(str(path)) as reopened:
        page = reopened[0]
        assert page.search_for("Formulaire")
        assert "Beausoleil" in page.get_text()
    read = strict_read(path)
    extracted = read.reader.pages[0].extract_text()
    assert "Formulaire" in extracted and "inscription" in extracted


def test_streams_and_font(scan_clean, clean_ocr, tmp_path) -> None:
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    with doc.lock:
        original = doc.fitz[0].get_contents()
    doc.add_ocr_layer(0, clean_ocr)
    with doc.lock:
        fitz = doc.fitz
        page = fitz[0]
        streams = page.get_contents()
        assert streams[1:-1] == original
        assert fitz.xref_stream(streams[0]).startswith(PREFIX_MARKER + b"\nq\n")
        layer = fitz.xref_stream(streams[-1])
        assert layer.startswith(LAYER_MARKER + b"\nQ\nq\nBT\n3 Tr\n")
        assert layer.rstrip().endswith(b"ET\nQ")
        assert b"(Formulaire ) Tj" in layer
        kind, font = fitz.xref_get_key(page.xref, "Resources/Font/PdfEdOcr")
        assert kind == "dict" and "/Helvetica" in font and "/WinAnsiEncoding" in font
    with pytest.raises(OcrError) as info:
        doc.add_ocr_layer(0, clean_ocr)
    assert info.value.reason == "exists"
    doc.close()


def test_undo_removes_the_layer(scan_clean, clean_ocr, tmp_path) -> None:
    path = copy_to(scan_clean, tmp_path)
    doc = PdfDocument.open(path)
    with doc.lock:
        original = doc.fitz[0].get_contents()
    pixels = _pixels(doc)
    changed = []
    doc.page_changed.connect(changed.append)
    doc.add_ocr_layer(0, clean_ocr)
    assert doc.remove_ocr_layer(0)
    assert changed == [0, 0]
    assert not doc.remove_ocr_layer(0)
    assert not doc.has_ocr_layer(0)
    assert doc.page_text(0).is_empty
    with doc.lock:
        assert doc.fitz[0].get_contents() == original
    assert _pixels(doc) == pixels
    # The streams created and dropped this session are not written incrementally.
    size = path.stat().st_size
    doc.save()
    assert path.stat().st_size - size < 600
    doc.close()
    with pymupdf.open(str(path)) as reopened:
        assert reopened[0].get_text() == ""
        for xref in range(1, reopened.xref_length()):
            if reopened.xref_is_stream(xref):
                assert not reopened.xref_stream(xref).startswith(b"% PDFEditor OCR")


def test_undo_after_a_full_save(scan_clean, clean_ocr, tmp_path) -> None:
    """A full save renumbers objects: the layer is still found by its first line."""
    path = copy_to(scan_clean, tmp_path)
    doc = PdfDocument.open(path)
    doc.add_ocr_layer(0, clean_ocr)
    doc.save(force_full=True)
    assert doc.has_ocr_layer(0)
    assert doc.remove_ocr_layer(0)
    assert doc.page_text(0).is_empty
    doc.save()
    doc.close()
    with pymupdf.open(str(path)) as reopened:
        assert reopened[0].get_text() == ""


def test_encrypted_document(scan_clean, clean_ocr, tmp_path) -> None:
    path = tmp_path / "aes.pdf"
    with pymupdf.open(str(scan_clean.path)) as src:
        src.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=PASSWORD,
            owner_pw=PASSWORD + "-owner",
        )
    doc = PdfDocument.open(path, password=PASSWORD)
    assert doc.can_modify
    doc.add_ocr_layer(0, clean_ocr)
    doc.save()
    doc.close()
    with pymupdf.open(str(path)) as reopened:
        assert reopened.needs_pass
        reopened.authenticate(PASSWORD)
        assert "Formulaire" in reopened[0].get_text()
    strict_read(path, PASSWORD)


def test_no_modify_permission(scan_clean, clean_ocr, tmp_path) -> None:
    path = tmp_path / "locked.pdf"
    with pymupdf.open(str(scan_clean.path)) as src:
        src.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=PASSWORD,
            owner_pw=PASSWORD + "-owner",
            permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY,
        )
    doc = PdfDocument.open(path, password=PASSWORD)
    assert not doc.can_modify
    with pytest.raises(OcrError) as info:
        doc.add_ocr_layer(0, clean_ocr)
    assert info.value.reason == "permission"
    assert not doc.has_ocr_layer(0)
    # In-memory recognition still works.
    doc.set_page_ocr(0, clean_ocr)
    assert doc.page_text(0).source == "ocr"
    doc.close()


def test_export_keeps_the_layer(scan_clean, clean_ocr, tmp_path) -> None:
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    doc.add_ocr_layer(0, clean_ocr)
    for name, options in [
        ("flat.pdf", ExportOptions()),
        ("clean.pdf", ExportOptions(flatten_forms=False, flatten_annots=False)),
    ]:
        doc.export_copy(tmp_path / name, options)
        with pymupdf.open(str(tmp_path / name)) as copy:
            assert copy[0].search_for("Beausoleil")
        strict_read(tmp_path / name)
    doc.close()


def test_in_memory_ocr_is_page_text(scan_clean, clean_ocr, tmp_path) -> None:
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    assert doc.page_text(0).is_empty
    assert doc.page_ocr(0) is None and not doc.has_page_ocr(0)
    emitted = []
    doc.ocr_changed.connect(emitted.append)
    doc.set_page_ocr(0, clean_ocr)
    assert emitted == [0]
    assert doc.has_page_ocr(0)
    assert doc.page_ocr(0) == clean_ocr
    text = doc.page_text(0)
    assert text.source == "ocr"
    assert text.lines[0].text == clean_ocr.lines[0].text
    assert all(text.is_invisible(i) for i in range(len(text.chars)))
    assert not doc.has_content_text(0)
    # The page is turned: the words follow it.
    doc.set_page_rotation(0, 90)
    turned = doc.page_ocr(0)
    assert turned.rotation == 90 and turned == clean_ocr.rotated(90)
    assert doc.page_text(0).lines[0].dir == (0.0, 1.0)
    # Writing the layer on the turned page uses the turned words.
    doc.add_ocr_layer(0)
    assert doc.page_text(0).source == "content"
    with doc.lock:
        words = _words(doc.fitz[0])
    assert _matched(turned, words) >= 0.97 * len(turned.words)
    doc.set_page_ocr(0, None)
    assert emitted == [0, 0] and doc.page_ocr(0) is None
    doc.set_page_ocr(0, None)  # nothing to forget: no signal
    assert emitted == [0, 0]
    doc.close()
    assert not doc.has_page_ocr(0)


def test_in_memory_ocr_follows_page_moves(mixed_scan, clean_ocr, tmp_path) -> None:
    path = tmp_path / "mixed.pdf"
    shutil.copyfile(mixed_scan, path)
    doc = PdfDocument.open(path)
    assert doc.has_content_text(0) and not doc.has_content_text(1)
    assert doc.scanned_pages() == [1]
    doc.set_page_ocr(1, clean_ocr)
    ids = doc.page_ids()
    doc.reorder_pages([ids[1], ids[0]])
    assert doc.page_ocr(0) == clean_ocr and doc.page_ocr(1) is None
    assert doc.page_text(0).source == "ocr"
    doc.save_as(tmp_path / "moved.pdf")  # reload keeps it
    assert doc.page_ocr(0) == clean_ocr
    doc.close()


def test_scanned_pages(scan_clean, simple_pdf) -> None:
    doc = PdfDocument.open(scan_clean.path)
    assert doc.is_scanned_page(0)
    assert doc.scanned_pages(limit=10) == [0]
    request = doc.ocr_request(0)
    assert request.page == 0 and request.dpi == 300 and request.rotation == 0
    assert len(request.samples) == request.width * request.height * 3
    doc.close()
    doc = PdfDocument.open(simple_pdf)
    assert doc.scanned_pages() == []
    assert doc.scanned_pages(limit=1) == []
    doc.close()


def test_command_single_page(scan_clean, clean_ocr, tmp_path) -> None:
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    stack = QUndoStack()
    command = AddOcrLayerCommand(doc, 0, clean_ocr)
    assert command.text() == "Make page text searchable"
    command.apply_now()
    stack.push(command)
    assert command.error is None and doc.has_ocr_layer(0)
    stack.undo()
    assert command.error is None and not doc.has_ocr_layer(0)
    stack.redo()
    assert command.error is None and doc.has_ocr_layer(0)
    # Something else removed the layer: undo records the failure.
    doc.remove_ocr_layer(0)
    stack.undo()
    assert isinstance(command.error, OcrError)
    doc.close()


def test_command_uses_the_in_memory_result(scan_clean, clean_ocr, tmp_path) -> None:
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    command = AddOcrLayerCommand(doc, 0)
    with pytest.raises(OcrError):
        command.apply_now()
    doc.set_page_ocr(0, clean_ocr)
    command.apply_now()
    assert doc.has_ocr_layer(0)
    doc.close()


def test_batch_command(mixed_scan, clean_ocr, tmp_path) -> None:
    path = tmp_path / "mixed.pdf"
    shutil.copyfile(mixed_scan, path)
    doc = PdfDocument.open(path)
    stack = QUndoStack()
    batch = AddOcrLayerCommand(doc)
    assert batch.text() == "Recognise text"
    assert batch.add_page(1, clean_ocr) == len(clean_ocr.words)
    stack.push(batch)  # applied already: the push performs nothing
    assert batch.error is None and doc.has_ocr_layer(1)
    assert batch.pages == [(doc.page_id(1), clean_ocr)]
    # The page moves: undo still finds it by id.
    ids = doc.page_ids()
    doc.reorder_pages([ids[1], ids[0]])
    stack.undo()
    assert batch.error is None
    assert not doc.has_ocr_layer(0) and not doc.has_ocr_layer(1)
    stack.redo()
    assert batch.error is None and doc.has_ocr_layer(0)
    doc.close()


def test_invisible_layer_is_not_editable(scan_clean, clean_ocr, tmp_path) -> None:
    from pdfeditor.core.textedit import EditReason, Run, TextEditError

    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    doc.add_ocr_layer(0, clean_ocr)
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, Run(0, 9), "Formulair")
    assert info.value.reason is EditReason.INVISIBLE
    doc.close()


# -- M8 review M1: strict layer detection ---------------------------------------------------
def _coalesce(doc: PdfDocument, i: int = 0) -> bytes:
    """Merge page ``i``'s content streams into one, as ``qpdf --coalesce-contents`` or
    pikepdf do; returns the merged bytes."""
    with doc.lock:
        fitz = doc.fitz
        page = fitz[i]
        merged = b"\n".join(fitz.xref_stream(x) for x in page.get_contents())
        xref = fitz.get_new_xref()
        fitz.update_object(xref, "<<>>")
        fitz.update_stream(xref, merged, compress=True)
        fitz.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    doc.page_changed.emit(i)
    return merged


def _contents(doc: PdfDocument, i: int = 0) -> list[bytes]:
    with doc.lock:
        return [doc.fitz.xref_stream(x) for x in doc.fitz[i].get_contents()]


def test_coalesced_layer_is_kept(scan_clean, clean_ocr, tmp_path) -> None:
    """Another program merged the streams: the layer is still found (never added twice)
    and undo refuses instead of dropping the merged stream (which blanked the page)."""
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    stack = QUndoStack()
    command = AddOcrLayerCommand(doc, 0, clean_ocr)
    command.apply_now()
    stack.push(command)
    pixels = _pixels(doc)
    merged = _coalesce(doc)
    assert merged.startswith(PREFIX_MARKER)
    assert doc.has_ocr_layer(0)
    with pytest.raises(OcrError) as info:
        doc.add_ocr_layer(0, clean_ocr)
    assert info.value.reason == "exists"
    assert not doc.remove_ocr_layer(0)
    assert _contents(doc) == [merged]
    assert _pixels(doc) == pixels
    stack.undo()
    assert isinstance(command.error, OcrError)
    assert "page 1 cannot be removed" in str(command.error)
    assert _contents(doc) == [merged] and _pixels(doc) == pixels
    doc.close()


def test_layer_stream_changed_by_another_program(scan_clean, clean_ocr, tmp_path) -> None:
    """A layer stream with anything but our word operators (here a stroke appended by a
    stamping tool) is not ours: nothing is removed."""
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    doc.add_ocr_layer(0, clean_ocr)
    with doc.lock:
        fitz = doc.fitz
        layer = fitz[0].get_contents()[-1]
        data = fitz.xref_stream(layer)
        fitz.update_stream(layer, data.replace(b"ET\nQ\n", b"ET\n0 0 m 50 50 l S\nQ\n"))
    before = _contents(doc)
    assert doc.has_ocr_layer(0)
    assert not doc.remove_ocr_layer(0)
    assert _contents(doc) == before
    doc.close()


def test_layer_alone_is_not_removed(scan_clean, clean_ocr, tmp_path) -> None:
    """Removing the streams would leave the page without content: refused."""
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    doc.add_ocr_layer(0, clean_ocr)
    with doc.lock:
        fitz = doc.fitz
        page = fitz[0]
        streams = page.get_contents()
        fitz.xref_set_key(page.xref, "Contents", f"[{streams[0]} 0 R {streams[-1]} 0 R]")
    assert doc.has_ocr_layer(0)
    assert not doc.remove_ocr_layer(0)
    assert len(_contents(doc)) == 2
    doc.close()


def test_strict_stream_checks(scan_clean, clean_ocr, tmp_path) -> None:
    from pdfeditor.core import ocr_layer

    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    with doc.lock:
        data = ocr_layer.layer_stream(doc.fitz[0], clean_ocr)
    assert ocr_layer.is_layer_stream(data)
    assert ocr_layer.is_prefix_stream(PREFIX_MARKER + b"\nq\n")
    assert not ocr_layer.is_prefix_stream(PREFIX_MARKER + b"\nq\n0 0 m\n")
    assert not ocr_layer.is_layer_stream(data + b"q\n")
    assert not ocr_layer.is_layer_stream(data.replace(b" Tj\n", b" Tj\n0 0 10 10 re f\n", 1))
    assert not ocr_layer.is_layer_stream(data.replace(b"3 Tr", b"0 Tr"))
    empty = ocr_layer.LAYER_HEAD + ocr_layer.LAYER_TAIL
    assert ocr_layer.is_layer_stream(empty)
    doc.close()
