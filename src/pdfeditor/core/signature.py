"""Signature images: Stamp annotations whose appearance draws one image (M4).

A signature is a ``/Subtype /Stamp`` annotation with ``/IT /StampImage``, a uuid4
``/NM``, ``/F 4`` (Print), ``/Name``, ``/Contents`` and ``/C`` removed, and the appearance
MuPDF builds for an image stamp (``pdf_set_annot_stamp_image_obj``: a form XObject with
``BBox [0 0 1 1]`` drawing ``/I Do``). The image is our own XObject
(:func:`add_image_xobject`): 8-bit ``/DeviceRGB`` samples with an 8-bit ``/DeviceGray``
``/SMask``, both Flate-compressed (``pdf_add_image`` would store it uncompressed, which
the incremental writer then copies as is). Several signatures may share one image.

On rotated pages the pixels are turned by ``-rotation`` before embedding (the
appearance is not rotated by MuPDF), so the signature reads upright on screen.

Pure functions on ``pymupdf.Document``; callers hold ``PdfDocument.lock``. Never call
``Annot.update()`` on a signature (only ``mupdf.pdf_update_annot``).
"""

from __future__ import annotations

import hashlib
import logging
import zlib
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QTransform

from pdfeditor.core.geometry import fitz_from_qrect, page_to_unrotated

if TYPE_CHECKING:
    from pdfeditor.core.annotations import AnnotInfo, AnnotSpec

log = logging.getLogger(__name__)

#: Longest side (pixels) of an embedded signature image.
MAX_IMAGE_SIDE = 1000
#: /IT of a signature stamp.
STAMP_IMAGE_INTENT = "StampImage"

_mupdf = pymupdf.mupdf
_ZLIB_LEVEL = 1


class ImageData:
    """Immutable RGB + alpha samples of a signature image (8 bits per sample, rows top
    to bottom, no padding), as embedded in the PDF.

    The samples are held zlib-compressed (undo snapshots stay small) and decompressed by
    :attr:`rgb` / :attr:`alpha`. Equality and hashing use the content.
    """

    __slots__ = ("width", "height", "_rgb", "_alpha", "_digest")

    width: int
    height: int

    def __init__(self, width: int, height: int, rgb: bytes, alpha: bytes) -> None:
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid image size {width}x{height}")
        if len(rgb) != width * height * 3 or len(alpha) != width * height:
            raise ValueError("sample lengths do not match the image size")
        sha = hashlib.sha1(f"{width}x{height}:".encode())
        sha.update(rgb)
        sha.update(alpha)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "_rgb", zlib.compress(bytes(rgb), _ZLIB_LEVEL))
        object.__setattr__(self, "_alpha", zlib.compress(bytes(alpha), _ZLIB_LEVEL))
        object.__setattr__(self, "_digest", sha.hexdigest())

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ImageData is immutable")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ImageData):
            return NotImplemented
        return self._digest == other._digest

    def __hash__(self) -> int:
        return hash(self._digest)

    def __repr__(self) -> str:
        return f"ImageData({self.width}x{self.height}, {self._digest[:10]})"

    @property
    def rgb(self) -> bytes:
        """RGB samples (``width * height * 3`` bytes)."""
        return zlib.decompress(self._rgb)

    @property
    def alpha(self) -> bytes:
        """Alpha samples (``width * height`` bytes, 255 = opaque)."""
        return zlib.decompress(self._alpha)

    @classmethod
    def from_qimage(cls, image: QImage, rotation: int = 0) -> ImageData:
        """Samples of ``image`` (any format; alpha kept, not premultiplied), scaled down
        to :data:`MAX_IMAGE_SIDE` and turned by ``-rotation`` degrees (the page rotation,
        so that the image reads upright on that page)."""
        if image.isNull():
            raise ValueError("null image")
        if max(image.width(), image.height()) > MAX_IMAGE_SIDE:
            image = image.scaled(
                MAX_IMAGE_SIDE,
                MAX_IMAGE_SIDE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        rotation = int(rotation) % 360 // 90 * 90
        if rotation:
            image = image.transformed(QTransform().rotate(-rotation))
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h, stride = rgba.width(), rgba.height(), rgba.bytesPerLine()
        raw = bytes(rgba.constBits())[: stride * h]
        if stride != w * 4:  # never for 32-bit formats, but stay safe
            raw = b"".join(raw[y * stride : y * stride + w * 4] for y in range(h))
        # De-interleave at C speed: drop the alpha channel through a Pixmap.
        rgb = pymupdf.Pixmap(pymupdf.Pixmap(pymupdf.csRGB, w, h, raw, 1), 0).samples
        return cls(w, h, bytes(rgb), raw[3::4])

    def to_qimage(self) -> QImage:
        """The image as a non-premultiplied RGBA8888 QImage."""
        pix = pymupdf.Pixmap(pymupdf.Pixmap(pymupdf.csRGB, self.width, self.height, self.rgb, 0), 1)
        pix.set_alpha(self.alpha, premultiply=False)
        data = bytes(pix.samples)
        return QImage(
            data, self.width, self.height, self.width * 4, QImage.Format.Format_RGBA8888
        ).copy()


def digest(data: ImageData) -> str:
    """Content hash of ``data`` (size and samples), used to share image objects."""
    return data._digest


# -- image objects ---------------------------------------------------------------
def add_image_xobject(fitz_doc: pymupdf.Document, data: ImageData) -> int:
    """Add ``data`` as a Flate-compressed /DeviceRGB image with an 8-bit /SMask; returns
    the image xref (not yet referenced by anything)."""
    w, h = data.width, data.height
    smask = fitz_doc.get_new_xref()
    fitz_doc.update_object(
        smask,
        f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}"
        "/ColorSpace/DeviceGray/BitsPerComponent 8>>",
    )
    # Raw samples + compress=True: pre-deflated bytes with compress=False lose /Filter.
    fitz_doc.update_stream(smask, data.alpha, compress=True)
    xref = fitz_doc.get_new_xref()
    fitz_doc.update_object(
        xref,
        f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}"
        f"/ColorSpace/DeviceRGB/BitsPerComponent 8/SMask {smask} 0 R>>",
    )
    fitz_doc.update_stream(xref, data.rgb, compress=True)
    return xref


def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # malformed object
        return ("null", "null")


def _int(doc: pymupdf.Document, xref: int, key: str) -> int:
    kind, value = _key(doc, xref, key)
    if kind != "int":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _channels(doc: pymupdf.Document, xref: int) -> int:
    """Colour components of image ``xref``'s colour space: 3 (RGB), 1 (grey) or 0."""
    kind, value = _key(doc, xref, "ColorSpace")
    value = value.strip()
    if kind == "name":
        return {"/DeviceRGB": 3, "/DeviceGray": 1}.get(value, 0)
    if kind in ("array", "xref"):
        # [/ICCBased n 0 R] (inline or through a reference): use the profile's /N.
        if kind == "xref":
            try:
                value = doc.xref_object(int(value.split()[0]), compressed=True)
            except Exception:
                return 0
        parts = value.strip("[] ").split()
        if len(parts) == 4 and parts[0] == "/ICCBased" and parts[3] == "R":
            try:
                n = _int(doc, int(parts[1]), "N")
            except ValueError:
                return 0
            return n if n in (1, 3) else 0
    return 0


def _plain_8bit(doc: pymupdf.Document, xref: int) -> bool:
    """Image ``xref`` holds 8-bit samples we can decode to bytes (no JPEG, no /Decode)."""
    if _key(doc, xref, "Subtype")[1] != "/Image" or _int(doc, xref, "BitsPerComponent") != 8:
        return False
    if _key(doc, xref, "ImageMask")[1] == "true" or _key(doc, xref, "Decode")[0] != "null":
        return False
    kind, value = _key(doc, xref, "Filter")
    if kind == "null":
        return True
    filters = value.replace("[", " ").replace("]", " ").split()
    return all(f == "/FlateDecode" for f in filters)


def image_supported(fitz_doc: pymupdf.Document, image_xref: int) -> bool:
    """Image ``image_xref`` can be read back by :func:`read_image` (checks the
    dictionaries only; no stream is decoded)."""
    doc = fitz_doc
    if image_xref <= 0 or not _plain_8bit(doc, image_xref) or not _channels(doc, image_xref):
        return False
    w, h = _int(doc, image_xref, "Width"), _int(doc, image_xref, "Height")
    if w <= 0 or h <= 0:
        return False
    kind, value = _key(doc, image_xref, "SMask")
    if kind != "xref":
        return False
    smask = int(value.split()[0])
    return (
        _plain_8bit(doc, smask)
        and _channels(doc, smask) == 1
        and _key(doc, smask, "Matte")[0] == "null"
        and (_int(doc, smask, "Width"), _int(doc, smask, "Height")) == (w, h)
    )


def image_size(fitz_doc: pymupdf.Document, image_xref: int) -> tuple[int, int]:
    """(/Width, /Height) of image ``image_xref`` ((0, 0) when missing)."""
    return _int(fitz_doc, image_xref, "Width"), _int(fitz_doc, image_xref, "Height")


def read_image(fitz_doc: pymupdf.Document, image_xref: int) -> ImageData:
    """The samples of image ``image_xref`` and its /SMask, as RGB + alpha.

    Exact for :func:`image_supported` images (ours: the stored samples are returned as
    they are); any other image MuPDF can decode (JPEG, 1-bit /SMask, other colour
    spaces...) is converted to RGB (no /SMask = opaque). Raises ``ValueError`` when it
    cannot be decoded.
    """
    if not image_supported(fitz_doc, image_xref):
        return _decode_image(fitz_doc, image_xref)
    w, h = image_size(fitz_doc, image_xref)
    smask = int(fitz_doc.xref_get_key(image_xref, "SMask")[1].split()[0])
    try:
        color = fitz_doc.xref_stream(image_xref)
        alpha = fitz_doc.xref_stream(smask)
    except Exception as exc:  # MuPDF raises FzError* on broken streams
        raise ValueError(f"unreadable signature image xref {image_xref}: {exc}") from exc
    if color is None or alpha is None or len(alpha) < w * h:
        raise ValueError(f"truncated signature image xref {image_xref}")
    if _channels(fitz_doc, image_xref) == 1:
        if len(color) < w * h:
            raise ValueError(f"truncated signature image xref {image_xref}")
        gray = pymupdf.Pixmap(pymupdf.csGRAY, w, h, color[: w * h], 0)
        color = pymupdf.Pixmap(pymupdf.csRGB, gray).samples
    if len(color) < w * h * 3:
        raise ValueError(f"truncated signature image xref {image_xref}")
    return ImageData(w, h, bytes(color[: w * h * 3]), bytes(alpha[: w * h]))


def _decode_image(fitz_doc: pymupdf.Document, image_xref: int) -> ImageData:
    """Any image MuPDF decodes, as 8-bit RGB + alpha (from its /SMask, else opaque)."""
    w, h = image_size(fitz_doc, image_xref)
    if w <= 0 or h <= 0 or _key(fitz_doc, image_xref, "Subtype")[1] != "/Image":
        raise ValueError(f"not an image: xref {image_xref}")
    try:
        base = pymupdf.Pixmap(fitz_doc, image_xref)
        if base.alpha:
            base = pymupdf.Pixmap(base, 0)
        if base.colorspace is None or base.colorspace.n != 3:
            base = pymupdf.Pixmap(pymupdf.csRGB, base)
        alpha = b"\xff" * (w * h)
        kind, value = _key(fitz_doc, image_xref, "SMask")
        if kind == "xref":
            mask = pymupdf.Pixmap(fitz_doc, int(value.split()[0]))
            if mask.n != 1 or (mask.width, mask.height) != (base.width, base.height):
                raise ValueError(f"unusable /SMask of image xref {image_xref}")
            alpha = bytes(mask.samples)
    except ValueError:
        raise
    except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
        raise ValueError(f"undecodable image xref {image_xref}: {exc}") from exc
    if (base.width, base.height) != (w, h):
        raise ValueError(f"image xref {image_xref} decodes to another size")
    return ImageData(w, h, bytes(base.samples), alpha)


# -- annotations -------------------------------------------------------------------
def is_signature_intent(fitz_doc: pymupdf.Document, annot_xref: int) -> bool:
    """Annotation ``annot_xref`` has ``/IT /StampImage``."""
    kind, value = _key(fitz_doc, annot_xref, "IT")
    return kind == "name" and value.lstrip("/") == STAMP_IMAGE_INTENT


def signature_image_xref(
    fitz_doc: pymupdf.Document, page: pymupdf.Page, annot: pymupdf.Annot
) -> int:
    """Xref of the image drawn by signature ``annot`` (on ``page``, kept referenced by
    the caller), or 0 when ``annot`` is not a signature (not a Stamp, no
    ``/IT /StampImage``, or an appearance without an image object)."""
    del page  # only documents the lifetime rule
    xref = int(annot.xref)
    if annot.type[0] != pymupdf.PDF_ANNOT_STAMP or not is_signature_intent(fitz_doc, xref):
        return 0
    try:
        obj = _mupdf.pdf_annot_stamp_image_obj(annot.this)
    except Exception:  # malformed appearance
        log.debug("no stamp image for xref %s", xref, exc_info=True)
        return 0
    if not obj.m_internal:
        return 0
    image = int(_mupdf.pdf_to_num(obj))
    if image <= 0 or _key(fitz_doc, image, "Subtype")[1] != "/Image":
        return 0
    return image


def _image_obj(fitz_doc: pymupdf.Document, image_xref: int):  # noqa: ANN202 - mupdf type
    return _mupdf.pdf_new_indirect(pymupdf._as_pdf_document(fitz_doc), image_xref, 0)


def set_signature_rect(annot: pymupdf.Annot, unrotated: pymupdf.Rect) -> None:
    """Move/resize signature ``annot`` to the unrotated ``rect`` and rebuild its
    appearance (MuPDF regenerates the ``/I Do`` form; never ``Annot.update()``)."""
    annot.set_rect(unrotated)
    _mupdf.pdf_update_annot(annot.this)


def create_signature_annot(
    fitz_doc: pymupdf.Document, page_index: int, spec: AnnotSpec, image_xref: int
) -> AnnotInfo:
    """Create a signature at ``spec.rect`` (page space) drawing image ``image_xref``
    (already pre-rotated for the page) and return its snapshot. ``spec.name`` "" = new
    uuid4 /NM."""
    from pdfeditor.core import annotations  # annotations imports this module

    page = fitz_doc[page_index]
    unrotated = page_to_unrotated(fitz_from_qrect(spec.rect), page.derotation_matrix)
    annot = page.add_stamp_annot(unrotated)
    xref = int(annot.xref)
    _mupdf.pdf_set_annot_stamp_image_obj(annot.this, _image_obj(fitz_doc, image_xref))
    # add_stamp_annot shrinks the rect to the "Approved" text aspect: set it again.
    set_signature_rect(annot, unrotated)
    fitz_doc.xref_set_key(xref, "NM", pymupdf.get_pdf_str(spec.name or annotations.new_name()))
    fitz_doc.xref_set_key(xref, "IT", "/" + STAMP_IMAGE_INTENT)
    for key in ("Name", "Contents", "C", "CL"):
        fitz_doc.xref_set_key(xref, key, "null")
    fitz_doc.xref_set_key(xref, "F", str(pymupdf.PDF_ANNOT_IS_PRINT))
    return annotations.read_one(fitz_doc, page_index, xref)
