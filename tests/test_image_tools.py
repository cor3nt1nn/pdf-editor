"""M4-T3: background removal of signature images (core/image_tools.py)."""

from __future__ import annotations

import time

import pytest
from fixtures import SIG_INK_COLOR, SIG_STROKE_POINTS, make_signature_image
from PySide6.QtGui import QColor, QImage, QImageIOHandler, QImageReader, QPainter, QTransform

from pdfeditor.core import image_tools
from pdfeditor.core.image_tools import (
    INK_COLORS,
    MAX_SIDE,
    PreparedImage,
    ProcessedImage,
    alpha_lut,
    load_image,
    otsu,
    prepare,
    process,
    to_qimage,
)


def _alpha_at(p: ProcessedImage, fx: float, fy: float) -> int:
    return p.alpha[int(fy * p.height) * p.width + int(fx * p.width)]


def _otsu(prepared: PreparedImage, **kw) -> ProcessedImage:
    return process(prepared, threshold=prepared.otsu_threshold, **kw)


# -- photo: paper removal -------------------------------------------------------------
def test_photo_even_paper_clean_paper_and_solid_ink(signature_photo):
    prepared = prepare(load_image(signature_photo), even_paper=True)
    out = _otsu(prepared, crop=False)
    assert (out.width, out.height) == (prepared.width, prepared.height)
    assert len(out.alpha) == out.width * out.height
    assert len(out.rgb) == 3 * out.width * out.height
    for fx, fy in SIG_STROKE_POINTS["paper"]:
        assert _alpha_at(out, fx, fy) == 0, (fx, fy)
    for fx, fy in SIG_STROKE_POINTS["ink"]:
        assert _alpha_at(out, fx, fy) >= 200, (fx, fy)


def test_photo_without_even_paper_shadow_corner_not_clean(signature_photo):
    image = load_image(signature_photo)
    flat = _otsu(prepare(image, even_paper=True), crop=False)
    raw = _otsu(prepare(image, even_paper=False), crop=False)

    def corner(p: ProcessedImage) -> int:  # non-transparent pixels in the shadow corner
        w, h = p.width, p.height
        return sum(
            1 for y in range(int(0.9 * h), h) for x in range(int(0.9 * w), w) if p.alpha[y * w + x]
        )

    assert corner(flat) == 0
    assert corner(raw) > 100
    ((fx, fy),) = SIG_STROKE_POINTS["shadow"]
    # Near the ramp's edge (noisy JPEG paper): look at a 9x9 neighbourhood.
    w, h = raw.width, raw.height
    x, y = int(fx * w), int(fy * h)
    assert max(raw.alpha[j * w + i] for j in range(y - 4, y + 5) for i in range(x - 4, x + 5)) > 0


def test_soft_ramp_has_partial_alpha(signature_photo):
    prepared = prepare(load_image(signature_photo))
    soft = _otsu(prepared, softness=24, crop=False).alpha
    hard = _otsu(prepared, softness=0, crop=False).alpha
    partial = len(soft) - soft.count(0) - soft.count(255)
    assert partial > 100
    assert len(hard) == hard.count(0) + hard.count(255)


def test_alpha_lut_and_otsu():
    lut = alpha_lut(128, 24)
    assert len(lut) == 256
    assert lut[116] == 255 and lut[140] == 0
    assert 0 < lut[128] < 255
    assert list(lut[116:141]) == sorted(lut[116:141], reverse=True)
    hard = alpha_lut(100, 0)
    assert hard[100] == 255 and hard[101] == 0
    assert alpha_lut(0, 24)[255] == 0 and alpha_lut(255, 24)[0] == 255
    hist = [0] * 256
    hist[30], hist[220] = 100, 900
    assert 30 <= otsu(hist) < 220


def test_color_replaces_rgb(signature_photo):
    prepared = prepare(load_image(signature_photo))
    for color in ((0, 0, 0), INK_COLORS["blue"]):
        out = _otsu(prepared, color=color)
        assert out.rgb == bytes(color) * (out.width * out.height)
    kept = _otsu(prepared, crop=False)
    # Colours kept where there is any alpha; one constant colour under alpha 0.
    for i, a in enumerate(kept.alpha):
        if a:
            assert kept.rgb[3 * i : 3 * i + 3] == prepared.rgb[3 * i : 3 * i + 3]
    assert INK_COLORS["black"] == (0, 0, 0)


def _clear_colors(out: ProcessedImage) -> set[bytes]:
    return {out.rgb[3 * i : 3 * i + 3] for i, a in enumerate(out.alpha) if a == 0}


@pytest.mark.parametrize("crop", [True, False])
def test_keep_colour_hides_the_photo_under_alpha_0(signature_photo, crop):
    """Privacy (M4 review 1): the paper of the photo never survives under alpha 0."""
    prepared = prepare(load_image(signature_photo))
    out = _otsu(prepared, crop=crop)
    assert out.alpha.count(0) > 0
    (clear,) = _clear_colors(out)
    ink = image_tools.ink_color(out.rgb, out.alpha)
    assert tuple(clear) == ink
    assert all(abs(c - s) < 40 for c, s in zip(ink, SIG_INK_COLOR, strict=True))


def test_keep_colour_png_transparent_areas_constant(signature_png):
    out = _otsu(prepare(load_image(signature_png)), crop=False)
    (clear,) = _clear_colors(out)
    assert tuple(clear) == SIG_INK_COLOR


def test_fill_transparent_and_ink_color():
    rgb = bytes(range(12))
    alpha = bytes((0, 255, 7, 0))
    out = image_tools.fill_transparent(rgb, alpha, (200, 201, 202))
    assert out == bytes((200, 201, 202, 3, 4, 5, 6, 7, 8, 200, 201, 202))
    assert image_tools.fill_transparent(rgb, b"\x01" * 4, (0, 0, 0)) == rgb
    with pytest.raises(ValueError):
        image_tools.fill_transparent(rgb, alpha[:3], (0, 0, 0))
    assert image_tools.ink_color(rgb, alpha) == (3, 4, 5)
    assert image_tools.ink_color(rgb, bytes(4)) == (0, 0, 0)
    # A thin stroke missed by the sampling stride is still found.
    n = 100_000
    thin = bytearray(n)
    thin[12345] = 255
    color = bytearray(3 * n)
    color[3 * 12345 : 3 * 12346] = bytes((9, 8, 7))
    assert image_tools.ink_color(bytes(color), bytes(thin)) == (9, 8, 7)


def test_crop_contains_ink_excludes_margins(signature_photo):
    prepared = prepare(load_image(signature_photo))
    full = _otsu(prepared, crop=False)
    out = _otsu(prepared, crop=True)
    x0, y0, x1, y1 = out.ink_bbox
    assert (out.width, out.height) == (x1 - x0, y1 - y0)
    w, h = prepared.width, prepared.height
    for fx, fy in SIG_STROKE_POINTS["ink"]:
        x, y = int(fx * w), int(fy * h)
        assert x0 <= x < x1 and y0 <= y < y1
        # same sample at the same place after the crop
        assert out.alpha[(y - y0) * out.width + (x - x0)] == full.alpha[y * w + x]
    assert x0 > 0.05 * w and y0 > 0.05 * h and x1 < 0.95 * w and y1 < 0.95 * h
    assert len(out.rgb) == 3 * out.width * out.height
    assert full.ink_bbox is not None


def test_no_ink_keeps_full_image(signature_photo):
    prepared = prepare(load_image(signature_photo))
    out = process(prepared, threshold=0, softness=0)
    assert out.ink_bbox is None
    assert (out.width, out.height) == (prepared.width, prepared.height)
    assert out.alpha.count(0) == len(out.alpha)


# -- sizes and orientation -------------------------------------------------------------
@pytest.mark.parametrize("size", [(3000, 1234), (777, 2500), (400, 160)])
def test_downscale_keeps_aspect(size):
    w, h = size
    image = QImage(w, h, QImage.Format.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    prepared = prepare(image)
    assert max(prepared.width, prepared.height) == min(MAX_SIDE, max(w, h))
    assert abs(prepared.width / prepared.height - w / h) * prepared.height <= 1
    assert len(prepared.gray) == prepared.width * prepared.height
    assert len(prepared.rgb) == 3 * prepared.width * prepared.height
    assert prepared.source_alpha is None


def test_exif_orientation_loads_upright(tmp_path):
    # Upright: 300x120, red left third, white elsewhere.
    upright = QImage(300, 120, QImage.Format.Format_RGB32)
    upright.fill(QColor(255, 255, 255))
    for y in range(120):
        for x in range(100):
            upright.setPixelColor(x, y, QColor(255, 0, 0))
    # A phone stores the sensor pixels (turned) plus orientation 6 ("rotate 90 cw").
    sensor = upright.transformed(QTransform().rotate(-90))
    path = tmp_path / "phone.jpg"
    # QImageWriter.setTransformation turns the pixels instead of writing the tag, so
    # the Exif APP1 segment (big-endian, one IFD entry: Orientation = 6) is inserted.
    assert sensor.save(str(path), "JPEG", 95)
    data = path.read_bytes()
    tiff = bytes.fromhex("4d4d002a00000008000101120003000000010006000000000000")
    payload = b"Exif" + bytes(2) + tiff
    app1 = bytes.fromhex("ffe1") + (len(payload) + 2).to_bytes(2, "big") + payload
    path.write_bytes(data[:2] + app1 + data[2:])
    assert QImageReader(str(path)).transformation() == (
        QImageIOHandler.Transformation.TransformationRotate90
    )
    raw = QImageReader(str(path))
    raw.setAutoTransform(False)
    assert raw.read().size().toTuple() == (120, 300)  # tag written, not applied to pixels
    image = load_image(path)
    assert image.size().toTuple() == (300, 120)
    left, right = image.pixelColor(30, 60), image.pixelColor(250, 60)
    assert left.red() > 200 and left.green() < 60
    assert right.green() > 200


def test_big_photo_is_decoded_scaled_down(tmp_path):
    """A large photo is decoded at most MAX_SIDE wide (M4 review 4)."""
    big = QImage(4000, 1600, QImage.Format.Format_RGB32)
    big.fill(QColor(240, 240, 230))
    path = tmp_path / "big.jpg"
    assert big.save(str(path), "JPEG", 80)
    image = load_image(path)
    assert image.size().toTuple() == (MAX_SIDE, 400)
    small = load_image(path, max_side=5000)
    assert small.size().toTuple() == (4000, 1600)


def test_big_exif_rotated_photo_scaled_and_upright(tmp_path):
    upright = QImage(2400, 1000, QImage.Format.Format_RGB32)
    upright.fill(QColor(255, 255, 255))
    painter = QPainter(upright)
    painter.fillRect(0, 0, 800, 1000, QColor(255, 0, 0))
    painter.end()
    path = _with_exif_orientation_6(upright, tmp_path / "big_phone.jpg")
    image = load_image(path)
    assert image.width() == MAX_SIDE and image.height() in (416, 417)
    left, right = image.pixelColor(100, 200), image.pixelColor(900, 200)
    assert left.red() > 200 and left.green() < 60
    assert right.green() > 200


def _with_exif_orientation_6(upright: QImage, path):
    sensor = upright.transformed(QTransform().rotate(-90))
    assert sensor.save(str(path), "JPEG", 90)
    data = path.read_bytes()
    tiff = bytes.fromhex("4d4d002a00000008000101120003000000010006000000000000")
    payload = b"Exif" + bytes(2) + tiff
    app1 = bytes.fromhex("ffe1") + (len(payload) + 2).to_bytes(2, "big") + payload
    path.write_bytes(data[:2] + app1 + data[2:])
    return path


def test_load_image_unreadable(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert load_image(bad) is None
    assert load_image(tmp_path / "missing.jpg") is None
    with pytest.raises(ValueError):
        prepare(QImage())


# -- clean PNG with real alpha ---------------------------------------------------------
def test_clean_png_alpha_multiplied_in(signature_png):
    image = load_image(signature_png)
    assert image.hasAlphaChannel()
    prepared = prepare(image)
    source = prepared.source_alpha
    assert source is not None and 0 < source.count(0) < len(source)
    # A threshold letting everything through leaves exactly the image's own alpha.
    everything = process(prepared, threshold=255, softness=0, crop=False)
    assert all(abs(a - b) <= 1 for a, b in zip(everything.alpha, source, strict=True))
    # Anti-aliased edges: partial source alpha stays partial (never raised).
    out = _otsu(prepared, crop=False)
    assert all(a <= b + 1 for a, b in zip(out.alpha, source, strict=True))
    assert any(0 < a < 255 for a in out.alpha)
    for fx, fy in SIG_STROKE_POINTS["ink"]:
        assert _alpha_at(out, fx, fy) == 255
        i = int(fy * out.height) * out.width + int(fx * out.width)
        assert tuple(out.rgb[3 * i : 3 * i + 3]) == SIG_INK_COLOR
    for fx, fy in SIG_STROKE_POINTS["paper"]:
        assert _alpha_at(out, fx, fy) == 0


def test_to_qimage_exact_samples(signature_png):
    out = _otsu(prepare(load_image(signature_png)))
    image = to_qimage(out)
    assert image.format() == QImage.Format.Format_RGBA8888
    assert (image.width(), image.height()) == (out.width, out.height)
    raw = image_tools._tight(image, 4)
    assert raw[3::4] == out.alpha
    assert bytes(b for i, b in enumerate(raw) if i % 4 != 3) == out.rgb


def test_signature_image_kinds(tmp_path):
    with pytest.raises(ValueError):
        make_signature_image(tmp_path / "x.png", kind="scan")
    photo = load_image(make_signature_image(tmp_path / "p.jpg", kind="photo", size=(800, 320)))
    assert photo.size().toTuple() == (800, 320) and not photo.hasAlphaChannel()


# -- performance -----------------------------------------------------------------------
def test_process_fast(signature_photo):
    prepared = prepare(load_image(signature_photo))
    best = min(
        _timed(lambda: _otsu(prepared, color=(0, 0, 0), crop=True)),
        _timed(lambda: _otsu(prepared, crop=True)),
    )
    assert best < 0.2


def _timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t
