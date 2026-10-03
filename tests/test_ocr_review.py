"""M8 review findings on OCR: snapping after a layer (M2), cheap "Pages without text"
(m2), scans with a small real text stamp (m6), the scan banner (m1), ligatures in the
layer (m3), page limits and per-page timeouts (m4)."""

from __future__ import annotations

import pymupdf
import pytest
from scan_fixtures import copy_to

from pdfeditor.core import ocr, pagetext
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.ocr import OcrLine, OcrWord, PageOcr
from pdfeditor.ui.ocr_dialog import OcrDialog

STAMP = "BATES-000123"


@pytest.fixture(scope="module")
def clean_ocr(scan_clean) -> PageOcr:
    with pymupdf.open(str(scan_clean.path)) as doc:
        return ocr.recognise(ocr.render_request(doc[0]))


def _shapes(doc: PdfDocument, i: int = 0) -> tuple:
    s = doc.page_shapes(i)
    return (s.boxes, s.h_segments, s.v_segments, s.glyph_boxes)


# -- M2: snapping on a scan with an OCR layer ------------------------------------------------
def test_scan_shapes_survive_the_layer(scan_clean, clean_ocr, tmp_path) -> None:
    path = copy_to(scan_clean, tmp_path)
    doc = PdfDocument.open(path)
    before = _shapes(doc)
    assert before[1] and before[2]  # the scan's rules were found
    doc.add_ocr_layer(0, clean_ocr)
    assert doc.has_content_text(0)
    assert not doc.is_scanned_page(0)  # recognised: no banner, no "without text"
    assert _shapes(doc) == before
    doc.save()
    doc.close()
    reopened = PdfDocument.open(path)
    assert reopened.has_ocr_layer(0)
    assert _shapes(reopened) == before
    reopened.close()


def test_text_profile(scan_clean, clean_ocr, tmp_path, simple_pdf) -> None:
    doc = PdfDocument.open(copy_to(scan_clean, tmp_path))
    with doc.lock:
        assert ocr.text_profile(doc.fitz[0]) == ocr.TextProfile()
    doc.add_ocr_layer(0, clean_ocr)
    with doc.lock:
        page = doc.fitz[0]
        profile = ocr.text_profile(page)
        assert profile.invisible and not profile.visible and profile.has_text
        assert ocr.page_has_text(page)
        assert not ocr.is_scanned_page(page)
        assert ocr.is_scanned_page(page, ignore_invisible=True)
    doc.close()
    with pymupdf.open(str(simple_pdf)) as plain:
        profile = ocr.text_profile(plain[0])
        assert profile.visible and not profile.invisible


# -- m6: a scan with a small real text stamp ------------------------------------------------
def _stamped(scan_clean, tmp_path, text: str = STAMP, size: float = 7) -> tuple:
    """The clean scan with a line of real text over its top margin; returns the path
    and the stamp's box (page space)."""
    path = tmp_path / "stamped.pdf"
    with pymupdf.open(str(scan_clean.path)) as doc:
        page = doc[0]
        page.insert_text((420, 20), text, fontsize=size, fontname="helv")
        box = page.search_for(text)[0]
        doc.save(str(path))
    return path, (box.x0, box.y0, box.x1, box.y1)


def test_stamped_scan_still_counts_as_scanned(scan_clean, tmp_path) -> None:
    path, _box = _stamped(scan_clean, tmp_path)
    doc = PdfDocument.open(path)
    assert doc.has_content_text(0)
    assert doc.is_scanned_page(0) and doc.lacks_text(0)
    assert doc.scanned_pages() == [0]
    doc.close()


def test_text_page_over_an_image_is_not_scanned(tmp_path) -> None:
    """Real text covering more than 2 % of the page: a digital page with a background."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 595, 842), False)
    pix.clear_with(240)
    page.insert_image(page.rect, pixmap=pix)
    for k in range(25):
        page.insert_text((40, 60 + 28 * k), "A line of real text " * 4, fontsize=11)
    assert ocr.text_coverage(ocr.text_profile(page).visible, page.rect) > 0.02
    assert ocr.image_coverage(page) > 0.99
    assert not ocr.is_scanned_page(page)
    assert ocr.scan_state(page) == (False, False)
    doc.close()


def test_layer_leaves_out_the_stamp(scan_clean, clean_ocr, tmp_path) -> None:
    path, box = _stamped(scan_clean, tmp_path)
    stamp_line = OcrLine(box, (box[0], box[3]), (OcrWord(box, STAMP),))
    result = PageOcr((stamp_line, *clean_ocr.lines), clean_ocr.width, clean_ocr.height)
    doc = PdfDocument.open(path)
    words = doc.add_ocr_layer(0, result)
    assert words == len(clean_ocr.words)  # every word but the stamp
    doc.save()
    doc.close()
    with pymupdf.open(str(path)) as reopened:
        page = reopened[0]
        assert page.get_text().count(STAMP) == 1  # the real stamp, not a copy
        assert page.search_for("Formulaire")


def test_service_writes_a_layer_on_a_stamped_scan(qtbot, scan_clean, tmp_path) -> None:
    from pdfeditor.core.ocr_service import OcrService

    path, _box = _stamped(scan_clean, tmp_path)
    doc = PdfDocument.open(path)
    service = OcrService()
    with qtbot.waitSignal(service.finished, timeout=60_000):
        service.start(doc, [0], make_searchable=True)
    command = service.take_command()
    assert command is not None and doc.has_ocr_layer(0)
    assert not doc.is_scanned_page(0)
    doc.close()


# -- m2: "Pages without text" never extracts the full page text -----------------------------
def test_without_text_scope_is_cheap(qtbot, settings, mixed_scan, monkeypatch) -> None:
    def boom(_page):
        raise AssertionError("full text extraction")

    monkeypatch.setattr(pagetext, "extract_page_text", boom)
    doc = PdfDocument.open(mixed_scan)
    dialog = OcrDialog(doc, settings, current_page=0)
    qtbot.addWidget(dialog)
    assert dialog.pages() == [1]
    doc.close()


def test_without_text_scope_on_many_pages(qtbot, settings, simple_pdf, tmp_path) -> None:
    import time

    path = tmp_path / "long.pdf"
    with pymupdf.open(str(simple_pdf)) as src:
        out = pymupdf.open()
        for _ in range(200):
            out.insert_pdf(src, from_page=0, to_page=0)
        out.save(str(path))
    doc = PdfDocument.open(path)
    dialog = OcrDialog(doc, settings, current_page=0)
    qtbot.addWidget(dialog)
    start = time.perf_counter()
    assert dialog.pages() == []
    assert time.perf_counter() - start < 3.0  # ~2 ms a page
    start = time.perf_counter()
    assert dialog.pages() == []  # cached
    assert time.perf_counter() - start < 0.05
    doc.close()


# -- m3: ligatures and other compatibility characters in the layer --------------------------
def test_layer_splits_ligatures() -> None:
    from pdfeditor.core import ocr_layer

    assert ocr_layer.winansi("ﬁnance ﬂux ﬀ") == "finance flux ff"
    assert ocr_layer.winansi("été … ½ €") == "été … ½ €"  # in cp1252: unchanged
    assert ocr_layer.winansi("Ｆｕｌｌ") == "Full"  # full-width forms
    assert ocr_layer.winansi("日本 Ω") == "?? ?"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    words = (OcrWord((20, 40, 90, 56), "ﬁnance"), OcrWord((100, 40, 160, 56), "eﬀort"))
    line = OcrLine((20, 40, 160, 56), (20, 56), words)
    assert ocr_layer.add_layer(doc, page, PageOcr((line,), 300, 200)) == 2
    data = pymupdf.open("pdf", doc.tobytes())
    hit = data[0].search_for("finance")
    assert len(hit) == 1 and abs(hit[0].x0 - 20) < 2
    assert data[0].search_for("effort")
    data.close()
    doc.close()


# -- m1: a closed scan notice stays closed -----------------------------------------------------
@pytest.fixture
def window(qtbot, settings, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox

    from pdfeditor.ui import dialogs
    from pdfeditor.ui.main_window import MainWindow

    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )

    def this_page_only(dialog):
        dialog.page_radio.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OcrDialog, "exec", this_page_only)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    yield w
    w.undo_stack.setClean()
    w.close()


def _three_scans(scan_clean, tmp_path):
    path = tmp_path / "three.pdf"
    with pymupdf.open(str(scan_clean.path)) as src:
        out = pymupdf.open()
        for _ in range(3):
            out.insert_pdf(src)
        out.save(str(path))
    return path


def _recognise_one_page(qtbot, window) -> None:
    service = window.document_view.ocr_service
    with qtbot.waitSignal(service.finished, timeout=60_000):
        assert window.recognise_text()
        assert not window.document_view.banner.isVisible()


def test_dismissed_scan_notice_stays_closed(qtbot, window, scan_clean, tmp_path) -> None:
    window.open_file(str(_three_scans(scan_clean, tmp_path)))
    banner = window.document_view.banner
    assert banner.isVisible() and banner.text.startswith("This document looks scanned: 3 of 3")
    banner.close_button.click()
    assert not banner.isVisible()
    _recognise_one_page(qtbot, window)  # 2 pages still without text: a new count
    assert window.document_view.document.has_ocr_layer(0)
    assert not banner.isVisible()
    window.document_view.refresh_banner()
    assert not banner.isVisible()
    # Another document: the notice is back.
    window.open_file(str(copy_to(scan_clean, tmp_path)))
    assert banner.isVisible()


def test_scan_notice_hidden_during_a_run(qtbot, window, scan_clean, tmp_path) -> None:
    window.open_file(str(_three_scans(scan_clean, tmp_path)))
    banner = window.document_view.banner
    assert banner.isVisible()
    _recognise_one_page(qtbot, window)
    assert banner.isVisible()
    assert banner.text == "This document looks scanned: 2 of 3 pages have no selectable text."


# -- m4: page size limit and per-page timeouts ------------------------------------------------
def test_choose_dpi_caps_the_pixels() -> None:
    doc = pymupdf.open()
    a4 = doc.new_page(width=595, height=842)
    assert ocr.choose_dpi(a4) == 300  # no image: the default, 35 Mpx
    a0 = doc.new_page(width=2384, height=3370)  # A0: 300 dpi would be 723 Mpx
    dpi = ocr.choose_dpi(a0)
    assert dpi == ocr.max_dpi(a0) < 300
    assert (2384 * dpi / 72) * (3370 * dpi / 72) <= ocr.MAX_PIXELS
    request = ocr.render_request(a0, 300)  # an explicit resolution is capped too
    assert request.dpi == dpi and request.width * request.height <= ocr.MAX_PIXELS
    doc.close()


def test_skipped_pages_in_the_status(qtbot, window, scan_clean, tmp_path, monkeypatch) -> None:
    import sys

    from pdfeditor.core import ocr_service

    monkeypatch.setattr(ocr_service, "PAGE_TIMEOUT_MS", 3_000)
    from PySide6.QtWidgets import QDialog

    accepted = QDialog.DialogCode.Accepted
    monkeypatch.setattr(OcrDialog, "exec", lambda d: d.all_radio.setChecked(True) or accepted)
    window.open_file(str(_three_scans(scan_clean, tmp_path)))
    service = window.document_view.ocr_service
    from test_ocr_service import HANGS_ON_PAGE_1

    service._command = [sys.executable, "-c", HANGS_ON_PAGE_1]
    with qtbot.waitSignal(service.finished, timeout=60_000):
        assert window.recognise_text()
    assert (
        window.statusBar().currentMessage()
        == "Text recognised on 2 page(s); not recognised: page(s) 2"
    )
