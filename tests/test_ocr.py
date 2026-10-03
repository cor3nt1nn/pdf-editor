"""M8-T1: OCR core (core/ocr.py) on synthetic scans (tests/scan_fixtures.py)."""

from __future__ import annotations

import json

import pymupdf
import pytest
from scan_fixtures import recall

from pdfeditor.core import ocr
from pdfeditor.core.ocr import OcrError, OcrLine, OcrWord, PageOcr
from pdfeditor.core.pagetext import PageText
from pdfeditor.resources import tessdata_dir


def _recognise(path, rotation: int | None = None) -> PageOcr:
    with pymupdf.open(str(path)) as doc:
        page = doc[0]
        if rotation is not None:
            page.set_rotation(rotation)
        request = ocr.render_request(page)
    return ocr.recognise(request)


@pytest.fixture(scope="module")
def clean_ocr(scan_clean) -> PageOcr:
    return _recognise(scan_clean.path)


@pytest.fixture(scope="module")
def rotated_ocr(scan_rotated) -> PageOcr:
    return _recognise(scan_rotated.path)


def _unique_boxes(result: PageOcr) -> dict[str, tuple[float, float, float, float]]:
    seen: dict[str, list] = {}
    for w in result.words:
        seen.setdefault(w.text, []).append(w.rect)
    return {text: rects[0] for text, rects in seen.items() if len(rects) == 1}


def _close(a, b, tol: float) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def test_tessdata_is_bundled() -> None:
    folder = tessdata_dir()
    assert (folder / "fra.traineddata").stat().st_size == 1_130_365
    assert (folder / "eng.traineddata").stat().st_size == 4_113_088
    assert (folder / "LICENSE").is_file()
    assert "87416418657359cb625c412a48b6e1d6d41c29bd" in (folder / "VERSION.txt").read_text(
        encoding="utf-8"
    )
    assert ocr.check_tessdata(folder) == folder


def test_clean_scan_recall(scan_clean, clean_ocr) -> None:
    words = [w.text for w in clean_ocr.words]
    assert recall(scan_clean.words, words) >= 0.85
    assert len(words) >= 140
    assert clean_ocr.languages == "fra+eng"
    assert clean_ocr.engine == "tesseract"
    assert clean_ocr.dpi == 300
    assert (clean_ocr.width, clean_ocr.height) == (595.0, 842.0)
    for w in clean_ocr.words:
        x0, y0, x1, y1 = w.rect
        assert 0 <= x0 < x1 <= 595 and 0 <= y0 < y1 <= 842
        assert w.text and not any(c.isspace() for c in w.text)


def test_words_sit_where_the_text_was(scan_clean, clean_ocr) -> None:
    """The title's first word is at the top left, where draw_form put it (50, 60)."""
    first = clean_ocr.lines[0].words[0]
    assert first.text == "Formulaire"
    x0, y0, x1, y1 = first.rect
    assert abs(x0 - 50) <= 3 and y0 < 60 < y1 + 3
    line = clean_ocr.lines[0]
    assert line.dir == (1.0, 0.0)
    assert abs(line.origin[1] - y1) <= 3


def test_degraded_scans_recall(scan_skewed, scan_200) -> None:
    skewed = _recognise(scan_skewed.path)
    assert recall(scan_skewed.words, [w.text for w in skewed.words]) >= 0.80
    low = _recognise(scan_200.path)
    assert low.dpi == 200
    assert recall(scan_200.words, [w.text for w in low.words]) >= 0.80


def test_rotated_page_is_read_as_displayed(scan_clean, scan_rotated, clean_ocr, rotated_ocr):
    """A scan stored sideways in a /Rotate 90 page: words in displayed page space, at the
    same places as on the upright scan."""
    assert rotated_ocr.rotation == 90
    assert (rotated_ocr.width, rotated_ocr.height) == (595.0, 842.0)
    assert recall(scan_rotated.words, [w.text for w in rotated_ocr.words]) >= 0.85
    upright = _unique_boxes(clean_ocr)
    turned = _unique_boxes(rotated_ocr)
    common = [t for t in upright if t in turned]
    assert len(common) >= 80
    assert all(_close(upright[t], turned[t], 3.0) for t in common)


def test_rotated_result_follows_a_page_rotation(scan_clean, clean_ocr) -> None:
    """PageOcr.rotated(90) predicts what recognising the turned page gives (boxes ±3 pt)
    — so a page rotated after recognition keeps its words in place."""
    predicted = clean_ocr.rotated(90)
    assert (predicted.width, predicted.height, predicted.rotation) == (842.0, 595.0, 90)
    assert predicted.lines[0].dir == (0.0, 1.0)
    # Recognising the turned page needs the text upright: compare geometry only.
    with pymupdf.open(str(scan_clean.path)) as doc:
        page = doc[0]
        page.set_rotation(90)
        m = page.rotation_matrix  # unrotated -> rotated page space
    for w, p in zip(clean_ocr.words, predicted.words, strict=True):
        r = (pymupdf.Rect(w.rect) * m).normalize()
        assert _close(tuple(r), p.rect, 0.01)
    for back in (predicted.rotated(0), clean_ocr.rotated(180).rotated(270).rotated(0)):
        assert (back.width, back.height, back.rotation) == (595.0, 842.0, 0)
        assert [w.text for w in back.words] == [w.text for w in clean_ocr.words]
        for a, b in zip(back.words, clean_ocr.words, strict=True):
            assert _close(a.rect, b.rect, 1e-6)
    with pytest.raises(ValueError):
        clean_ocr.rotated(45)


def test_json_round_trip(clean_ocr) -> None:
    data = json.loads(json.dumps(clean_ocr.to_json()))
    again = PageOcr.from_json(data)
    assert again == clean_ocr
    assert again.text == clean_ocr.text
    with pytest.raises(ValueError):
        PageOcr.from_json({"lines": [{"rect": [0, 0, 1]}]})
    with pytest.raises(ValueError):
        PageOcr.from_json({**data, "version": 99})


def test_rawdict_builds_invisible_page_text(clean_ocr) -> None:
    text = PageText.from_rawdict(clean_ocr.rawdict())
    assert not text.is_empty
    assert len(text.lines) == len(clean_ocr.lines)
    assert text.lines[0].text == clean_ocr.lines[0].text
    assert all(text.is_invisible(i) for i in range(len(text.chars)))
    # Each char sits inside its word box.
    word = clean_ocr.lines[0].words[0]
    first = text.chars[0]
    assert word.rect[0] - 0.01 <= first.bbox.left() and first.bbox.right() <= word.rect[2] + 0.01
    # A word is found by word_at in the middle of its box.
    from PySide6.QtCore import QPointF

    x0, y0, x1, y1 = word.rect
    a, b = text.word_at(QPointF((x0 + x1) / 2, (y0 + y1) / 2))
    assert text.text_of(text.chars_between(a, b)) == word.text


def test_rawdict_of_turned_lines() -> None:
    line = OcrLine((10, 20, 110, 32), (10, 30), (OcrWord((10, 20, 50, 32), "ab"),
                   OcrWord((60, 20, 110, 32), "cd")))  # fmt: skip
    result = PageOcr((line,), 200, 300)
    for rotation in (90, 180, 270):
        turned = result.rotated(rotation)
        text = PageText.from_rawdict(turned.rawdict())
        assert text.lines[0].text == "ab cd"
        for word, (a, b) in zip(turned.words, [(0, 1), (3, 4)], strict=True):
            box = text.chars[a].bbox.united(text.chars[b].bbox)
            assert _close((box.left(), box.top(), box.right(), box.bottom()), word.rect, 0.01)


def test_grey_samples_are_rejected() -> None:
    with pytest.raises(ValueError):
        ocr.ocr_samples(b"\xff" * 100, 10, 10, page_size=(10, 10))
    with pytest.raises(ValueError):
        ocr.ocr_samples(b"\xff" * 300, 10, 10, page_size=(0, 10))


def test_missing_language_data(tmp_path) -> None:
    samples = b"\xff" * (20 * 20 * 3)
    with pytest.raises(OcrError) as info:
        ocr.ocr_samples(samples, 20, 20, page_size=(20, 20), tessdata=tmp_path)
    assert info.value.reason == "tessdata"
    with pytest.raises(OcrError) as info:
        ocr.ocr_samples(samples, 20, 20, page_size=(20, 20), languages="deu")
    assert info.value.reason == "tessdata"


def test_blank_page_has_no_words() -> None:
    result = ocr.ocr_samples(b"\xff" * (200 * 300 * 3), 200, 300, page_size=(200, 300))
    assert result.is_empty
    assert result.lines == ()


def _image_page(doc: pymupdf.Document, pixels: tuple[int, int], rect=None) -> pymupdf.Page:
    page = doc.new_page(width=595, height=842)
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, *pixels), False)
    pix.clear_with(200)
    page.insert_image(rect or page.rect, pixmap=pix, keep_proportion=False)
    return page


def test_choose_dpi_clamps(scan_clean, scan_200) -> None:
    with pymupdf.open(str(scan_clean.path)) as doc:
        assert ocr.choose_dpi(doc[0]) == 300
    with pymupdf.open(str(scan_200.path)) as doc:
        assert ocr.choose_dpi(doc[0]) == 200
    doc = pymupdf.open()
    assert ocr.choose_dpi(_image_page(doc, (595, 842))) == 150  # 72 dpi -> 150
    assert ocr.choose_dpi(_image_page(doc, (595 * 600 // 72, 842 * 600 // 72))) == 300
    assert ocr.choose_dpi(doc.new_page()) == 300  # no image
    # The largest image decides.
    page = _image_page(doc, (1653, 2339))  # 200 dpi
    page.insert_image(pymupdf.Rect(10, 10, 60, 60), pixmap=pymupdf.Pixmap(
        pymupdf.csGRAY, pymupdf.IRect(0, 0, 1000, 1000), False))  # fmt: skip
    assert ocr.choose_dpi(page) == 200
    doc.close()


def test_is_scanned_page(scan_clean, scan_rotated, simple_pdf, tmp_path) -> None:
    import fixtures

    with pymupdf.open(str(scan_clean.path)) as doc:
        assert ocr.is_scanned_page(doc[0])
        assert ocr.image_coverage(doc[0]) > 0.99
    with pymupdf.open(str(scan_rotated.path)) as doc:
        assert ocr.is_scanned_page(doc[0])
    with pymupdf.open(str(fixtures.make_scanned_pdf(tmp_path / "grey.pdf"))) as doc:
        assert ocr.is_scanned_page(doc[0])
    with pymupdf.open(str(simple_pdf)) as doc:
        assert not ocr.is_scanned_page(doc[0])  # text
    doc = pymupdf.open()
    assert not ocr.is_scanned_page(doc.new_page())  # blank: no image
    small = _image_page(doc, (100, 100), pymupdf.Rect(0, 0, 200, 200))
    assert not ocr.is_scanned_page(small)  # image covers 8 % of the page
    busy = _image_page(doc, (595, 842))
    for i in range(30):
        busy.draw_line((10, 10 + 5 * i), (300, 10 + 5 * i))
    assert not ocr.is_scanned_page(busy)  # many vector paths: a digital drawing
    doc.close()


def test_looking_at_pages_changes_nothing(tmp_path) -> None:
    """Scan detection and resolution choice never make MuPDF write appearance streams
    of annotations lacking them (the document would have changes to save)."""
    import fixtures

    from pdfeditor.core.document import _incremental_bytes

    path = fixtures.make_annotated_pdf(tmp_path / "annotated.pdf")
    with pymupdf.open(str(path)) as doc:
        before = len(_incremental_bytes(doc))
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            ocr.is_scanned_page(page)
            ocr.choose_dpi(page)
            ocr.image_coverage(page)
        assert len(_incremental_bytes(doc)) == before
