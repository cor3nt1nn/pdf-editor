"""Background removal for signature images (M4), with QtGui only (no numpy).

A photo or scan of a signature is turned into ink colour + alpha in two steps:

* :func:`prepare` (once per image / "Even out paper" toggle): scale down to
  ``max_side`` pixels, keep the colour samples and any real alpha, compute a grey
  image (transparent areas count as white paper) optionally divided by an estimate of
  the paper brightness (flat-field: a 1/32 downscaled-then-upscaled copy, applied with
  ``QPainter.CompositionMode_ColorDodge``, which removes shadows and gradients), and an
  Otsu threshold on its histogram.
* :func:`process` (on every slider move, a few ms): alpha from the grey bytes through a
  256-entry lookup table (``bytes.translate``, a soft ramp around the threshold),
  multiplied by the image's own alpha, optional constant ink colour, crop to the ink.

Everything heavy runs in Qt or in ``bytes`` methods (C speed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

log = logging.getLogger(__name__)

#: Longest side (pixels) of a prepared image (same as ``signature.MAX_IMAGE_SIDE``).
MAX_SIDE = 1000
#: Default width (grey levels) of the alpha ramp around the threshold.
DEFAULT_SOFTNESS = 24
#: Alpha above which a pixel counts as ink for cropping.
INK_ALPHA = 16
#: Transparent pixels kept around the ink when cropping.
CROP_PADDING = 2
#: Ink colour presets (RGB).
INK_COLORS: dict[str, tuple[int, int, int]] = {"black": (0, 0, 0), "blue": (0, 40, 160)}

_FLAT_CELL = 32


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """A scaled-down image ready for :func:`process`.

    ``gray`` (``width * height`` bytes) is the image the threshold applies to (flat-fielded
    when prepared with ``even_paper``); ``rgb`` the original colours (3 bytes per pixel);
    ``source_alpha`` the image's own alpha, or None when it is opaque.
    """

    width: int
    height: int
    gray: bytes
    rgb: bytes
    otsu_threshold: int
    source_alpha: bytes | None = None


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """Ink colour and alpha samples (no row padding), ready for ``signature.ImageData``.

    ``ink_bbox`` is ``(x0, y0, x1, y1)`` (exclusive) of the pixels whose alpha exceeds
    :data:`INK_ALPHA`, in the coordinates of the prepared image (padded by
    :data:`CROP_PADDING` when cropped), or None when no ink was found.
    """

    width: int
    height: int
    rgb: bytes
    alpha: bytes
    ink_bbox: tuple[int, int, int, int] | None


# -- loading -------------------------------------------------------------------------
def load_image(path: str | Path) -> QImage | None:
    """The image at ``path`` turned upright from its EXIF orientation, or None (logged)
    when it cannot be read."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        log.warning("cannot read image %s: %s", path, reader.errorString())
        return None
    return image


# -- helpers -------------------------------------------------------------------------
def _tight(image: QImage, bpp: int) -> bytes:
    """Pixel bytes of ``image`` without Qt's row padding."""
    w, h, stride = image.width(), image.height(), image.bytesPerLine()
    raw = bytes(image.constBits())[: stride * h]
    if stride == w * bpp:
        return raw
    return b"".join(raw[y * stride : y * stride + w * bpp] for y in range(h))


def _alpha8(data: bytes, w: int, h: int) -> QImage:
    return QImage(data, w, h, w, QImage.Format.Format_Alpha8).copy()


def _flat_field(gray: QImage) -> QImage:
    """``gray`` divided by a smooth estimate of the paper brightness (ColorDodge computes
    ``dest / (1 - src)`` and ``src`` is the inverted background)."""
    w, h = gray.width(), gray.height()
    bg = gray.scaled(
        max(1, w // _FLAT_CELL),
        max(1, h // _FLAT_CELL),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    bg.invertPixels()
    out = gray.convertToFormat(QImage.Format.Format_RGB32)
    painter = QPainter(out)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_ColorDodge)
    painter.drawImage(0, 0, bg.convertToFormat(QImage.Format.Format_RGB32))
    painter.end()
    return out.convertToFormat(QImage.Format.Format_Grayscale8)


def otsu(hist: list[int]) -> int:
    """Otsu threshold of a 256-bin histogram (levels ``<=`` it are the dark class)."""
    total = sum(hist)
    sum_all = sum(i * c for i, c in enumerate(hist))
    sum_b = 0
    w_b = 0
    best = -1.0
    threshold = 128
    for i, c in enumerate(hist):
        w_b += c
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += i * c
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best:
            best, threshold = var, i
    return threshold


def alpha_lut(threshold: int, softness: int = DEFAULT_SOFTNESS) -> bytes:
    """256-entry table grey level -> alpha: 255 at or below ``threshold - softness/2``,
    0 at or above ``threshold + softness/2``, linear in between (a hard cut when
    ``softness`` is 0: ink up to ``threshold`` included)."""
    threshold = max(0, min(255, int(threshold)))
    softness = max(0, int(softness))
    if softness == 0:
        return bytes(255 if v <= threshold else 0 for v in range(256))
    lo = threshold - softness / 2
    hi = threshold + softness / 2
    return bytes(
        255 if v <= lo else 0 if v >= hi else round(255 * (hi - v) / (hi - lo)) for v in range(256)
    )


# -- prepare / process ---------------------------------------------------------------
def prepare(image: QImage, *, even_paper: bool = True, max_side: int = MAX_SIDE) -> PreparedImage:
    """Scale ``image`` down to ``max_side`` (aspect kept) and compute its grey image and
    Otsu threshold. Raises ``ValueError`` for a null image."""
    if image.isNull():
        raise ValueError("null image")
    if max(image.width(), image.height()) > max_side:
        image = image.scaled(
            QSize(max_side, max_side),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    w, h = image.width(), image.height()
    source_alpha: bytes | None = None
    if image.hasAlphaChannel():
        argb = image.convertToFormat(QImage.Format.Format_RGBA8888)
        alpha = _tight(argb, 4)[3::4]
        if alpha.count(255) != w * h:
            source_alpha = alpha
        rgb = _tight(argb.convertToFormat(QImage.Format.Format_RGB888), 3)
        # Transparent areas count as white paper for the threshold.
        flat = QImage(w, h, QImage.Format.Format_RGB32)
        flat.fill(QColor(255, 255, 255))
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
    else:
        flat = image
        rgb = _tight(image.convertToFormat(QImage.Format.Format_RGB888), 3)
    gray_image = flat.convertToFormat(QImage.Format.Format_Grayscale8)
    if even_paper:
        gray_image = _flat_field(gray_image)
    gray = _tight(gray_image, 1)
    hist = [gray.count(i) for i in range(256)]
    return PreparedImage(w, h, gray, rgb, otsu(hist), source_alpha)


def _multiply_alpha(a: bytes, b: bytes, w: int, h: int) -> bytes:
    """``a * b / 255`` per pixel, through QPainter's DestinationIn."""
    dest = _alpha8(a, w, h).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    src = _alpha8(b, w, h).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(dest)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, src)
    painter.end()
    return _tight(dest.convertToFormat(QImage.Format.Format_Alpha8), 1)


def _ink_bbox(alpha: bytes, w: int, h: int) -> tuple[int, int, int, int] | None:
    ink = alpha.translate(bytes(0 if v <= INK_ALPHA else 1 for v in range(256)))
    if ink.count(1) == 0:
        return None
    empty_row = bytes(w)
    rows = [y for y in range(h) if ink[y * w : (y + 1) * w] != empty_row]
    empty_col = bytes(h)
    cols = [x for x in range(w) if ink[x::w] != empty_col]
    return cols[0], rows[0], cols[-1] + 1, rows[-1] + 1


def process(
    prepared: PreparedImage,
    *,
    threshold: int,
    softness: int = DEFAULT_SOFTNESS,
    color: tuple[int, int, int] | None = None,
    crop: bool = True,
) -> ProcessedImage:
    """Ink alpha of ``prepared`` for ``threshold`` (see :func:`alpha_lut`), times its own
    alpha; colour kept (``color`` None) or replaced by ``color``; cropped to the ink
    (plus :data:`CROP_PADDING`) when ``crop`` and some ink was found."""
    w, h = prepared.width, prepared.height
    alpha = prepared.gray.translate(alpha_lut(threshold, softness))
    if prepared.source_alpha is not None:
        alpha = _multiply_alpha(alpha, prepared.source_alpha, w, h)
    rgb = prepared.rgb if color is None else bytes(color) * (w * h)
    bbox = _ink_bbox(alpha, w, h)
    if not crop or bbox is None:
        return ProcessedImage(w, h, rgb, alpha, bbox)
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - CROP_PADDING), max(0, y0 - CROP_PADDING)
    x1, y1 = min(w, x1 + CROP_PADDING), min(h, y1 + CROP_PADDING)
    cw = x1 - x0
    alpha = b"".join(alpha[y * w + x0 : y * w + x1] for y in range(y0, y1))
    rgb = b"".join(rgb[3 * (y * w + x0) : 3 * (y * w + x1)] for y in range(y0, y1))
    return ProcessedImage(cw, y1 - y0, rgb, alpha, (x0, y0, x1, y1))


def to_qimage(processed: ProcessedImage) -> QImage:
    """``processed`` as a non-premultiplied RGBA8888 QImage (exact samples)."""
    w, h = processed.width, processed.height
    rgba = bytearray(
        _tight(
            QImage(processed.rgb, w, h, 3 * w, QImage.Format.Format_RGB888).convertToFormat(
                QImage.Format.Format_RGBA8888
            ),
            4,
        )
    )
    rgba[3::4] = processed.alpha
    return QImage(bytes(rgba), w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
