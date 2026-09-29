from __future__ import annotations

import math

import pytest
from fixtures import PASSWORD, SIMPLE_SIZES
from PySide6.QtCore import QPointF, QRectF, QSizeF
from PySide6.QtGui import QImage

from pdfeditor.core.document import OpenError, PasswordRequired, PdfDocument


@pytest.fixture
def doc(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    yield d
    d.close()


def test_page_sizes(doc: PdfDocument) -> None:
    assert doc.page_count == 3
    for i, (w, h) in enumerate(SIMPLE_SIZES):
        assert doc.page_size(i) == QSizeF(w, h)
    assert doc.page_size(1) == QSizeF(792, 612)
    assert doc.path.endswith("simple.pdf")
    assert not doc.is_encrypted
    assert not doc.was_repaired


def test_page_index_errors(doc: PdfDocument) -> None:
    with pytest.raises(IndexError):
        doc.page_size(3)
    with pytest.raises(IndexError):
        doc.page_rotation(-1)


def test_rotated_fixture_widget_in_page_space(rotated_pdf) -> None:
    d = PdfDocument.open(rotated_pdf)
    assert d.page_rotation(0) == 90
    assert d.page_size(0) == QSizeF(792, 612)
    page_rect = QRectF(0, 0, 792, 612)
    rects = d.widget_rects(0)
    assert [name for name, _ in rects] == ["name"]
    assert page_rect.contains(rects[0][1])
    # Deviation: raw PyMuPDF widget.rect is in *unrotated* coordinates.
    with d.lock:
        raw = next(d.fitz[0].widgets()).rect
    assert raw.y1 > 612
    d.close()


def test_encrypted_requires_password(encrypted_pdf) -> None:
    with pytest.raises(PasswordRequired) as info:
        PdfDocument.open(encrypted_pdf)
    assert not info.value.wrong_password

    d = PdfDocument.open(encrypted_pdf, password=PASSWORD)
    assert d.is_encrypted
    assert d.password == PASSWORD
    assert d.page_count == 2
    d.close()


def test_encrypted_rejects_wrong_password(encrypted_pdf) -> None:
    with pytest.raises(PasswordRequired) as info:
        PdfDocument.open(encrypted_pdf, password="wrong")
    assert info.value.wrong_password


def test_password_callback_loop(encrypted_pdf) -> None:
    answers = iter(["bad1", "bad2", PASSWORD])
    attempts: list[int] = []

    def cb(attempt: int) -> str | None:
        attempts.append(attempt)
        return next(answers)

    d = PdfDocument.open(encrypted_pdf, password_cb=cb)
    assert attempts == [0, 1, 2]
    d.close()

    with pytest.raises(PasswordRequired) as info:
        PdfDocument.open(encrypted_pdf, password_cb=lambda a: "bad" if a == 0 else None)
    assert info.value.wrong_password


def test_open_errors(tmp_path) -> None:
    with pytest.raises(OpenError):
        PdfDocument.open(tmp_path / "missing.pdf")
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(OpenError):
        PdfDocument.open(empty)
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"this is not a pdf " * 50)
    with pytest.raises(OpenError):
        PdfDocument.open(garbage)


def test_render_rgb888(doc: PdfDocument) -> None:
    img = doc.render(0, 1.0)
    assert img.format() == QImage.Format.Format_RGB888
    w, h = SIMPLE_SIZES[0]
    assert (img.width(), img.height()) == (math.ceil(w), math.ceil(h))
    assert img.pixelColor(5, 5).name() == "#ffffff"
    img2 = doc.render(1, 1.5)
    assert (img2.width(), img2.height()) == (math.ceil(792 * 1.5), math.ceil(612 * 1.5))
    clipped = doc.render(0, 2.0, clip=QRectF(0, 0, 100, 50))
    assert (clipped.width(), clipped.height()) == (200, 100)


def test_set_page_rotation(qtbot, doc: PdfDocument) -> None:
    with qtbot.waitSignal(doc.page_changed) as blocker:
        doc.set_page_rotation(1, 90)
    assert blocker.args == [1]
    assert doc.page_rotation(1) == 90
    assert doc.page_size(1) == QSizeF(612, 792)
    with qtbot.assertNotEmitted(doc.page_changed):
        doc.set_page_rotation(1, 450)  # == 90
    doc.set_page_rotation(1, -90)
    assert doc.page_rotation(1) == 270
    with pytest.raises(ValueError):
        doc.set_page_rotation(1, 45)


def test_hot_reads_do_not_wait_for_the_lock(doc: PdfDocument) -> None:
    import threading

    doc.page_size(1)  # warm the size cache
    held = threading.Event()
    release = threading.Event()

    def hold():
        with doc.lock:
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        # These would deadlock-until-timeout if they needed the lock held by "a render".
        assert doc.page_count == 3
        assert doc.page_size(1) == QSizeF(792, 612)
        with pytest.raises(IndexError):
            doc.page_size(7)
    finally:
        release.set()
        t.join()


def test_unreadable_file(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    path = tmp_path / "locked.pdf"
    path.write_bytes(b"%PDF-1.7")

    def denied(self):
        raise PermissionError("locked by another program")

    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(OpenError) as info:
        PdfDocument.open(path)
    assert info.value.reason == "unreadable"


def test_close(doc: PdfDocument) -> None:
    doc.close()
    assert not doc.is_open
    doc.close()  # idempotent


def _is_red(color) -> bool:
    return color.red() > 200 and color.green() < 80 and color.blue() < 80


def assert_rect_matches_red_pixels(image, rect: QRectF) -> None:
    """Pixels just inside ``rect`` (image pixels) are red, pixels just outside are not."""
    inset, outset = 2.5, 3.5
    inner = rect.adjusted(inset, inset, -inset, -inset)
    for p in (
        inner.topLeft(),
        inner.topRight(),
        inner.bottomLeft(),
        inner.bottomRight(),
        rect.center(),
    ):
        assert _is_red(image.pixelColor(int(p.x()), int(p.y()))), (p, rect)
    c = rect.center()
    for x, y in (
        (rect.left() - outset, c.y()),
        (rect.right() + outset, c.y()),
        (c.x(), rect.top() - outset),
        (c.x(), rect.bottom() + outset),
    ):
        assert not _is_red(image.pixelColor(int(x), int(y))), ((x, y), rect)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_widget_rects_match_rendered_pixels_with_cropbox(tmp_path, rotation, scale) -> None:
    from fixtures import CROP_BOX, make_cropped_form_pdf

    path = make_cropped_form_pdf(tmp_path / f"crop{rotation}.pdf", rotation)
    d = PdfDocument.open(path)
    crop_w, crop_h = CROP_BOX[2] - CROP_BOX[0], CROP_BOX[3] - CROP_BOX[1]
    expected = QSizeF(crop_w, crop_h) if rotation in (0, 180) else QSizeF(crop_h, crop_w)
    assert d.page_size(0) == expected
    [(name, rect)] = d.widget_rects(0)
    assert name == "red"
    assert QRectF(QPointF(0, 0), expected).contains(rect)
    image = d.render(0, scale)
    scaled = QRectF(rect.x() * scale, rect.y() * scale, rect.width() * scale, rect.height() * scale)
    assert_rect_matches_red_pixels(image, scaled)
    d.close()
