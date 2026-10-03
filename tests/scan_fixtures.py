"""Synthetic scanned pages for the OCR tests (M8, docs/M8_PLAN.md "OCR fixtures").

Generated on the fly like ``tests/fixtures.py``: a French form page is drawn with
PyMuPDF (title, paragraphs, labelled underlines, three checkbox squares, a 3×3 table),
rendered in grey, degraded with Qt (``QtGui`` only: rotation about the centre, blur by
down/up-scaling, salt-and-pepper noise, JPEG) and placed alone on an image-only page.

* :func:`make_scan_pdf` → :class:`ScanFixture` (path, the true words, the rule geometry).
* :func:`make_mixed_pdf` — page 1 digital text, page 2 a clean scan.
* :func:`recall` — fraction of the true words recognised (multiset match).

The conftest exposes them as session fixtures (``scan_clean``, ``scan_skewed``,
``scan_200``, ``scan_rotated``, ``mixed_scan``): copy a file before saving over it.
"""

from __future__ import annotations

import random
import shutil
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform

A4 = (595.0, 842.0)
TITLE = "Formulaire de demande d'inscription à l'école élémentaire de Beausoleil"
PARAGRAPHS = (
    "Nom et prénom de l'enfant : Élodie Grüner-Lévêque, née le 14 février 2017 à Nice "
    "(Alpes-Maritimes).",
    "Adresse : 27, avenue du Général-de-Gaulle, 06240 Beausoleil. Téléphone : 04 93 78 12 34.",
    "Je soussigné(e), représentant légal, certifie l'exactitude des renseignements ci-dessus "
    "et m'engage à signaler",
    "tout changement de situation (déménagement, garde alternée, allergies alimentaires) au "
    "secrétariat.",
    "Pièces à joindre : justificatif de domicile, livret de famille, carnet de vaccinations à "
    "jour, attestation d'assurance.",
    "Fait à Monaco, le 3 septembre 2026. Signature du responsable légal :",
    "The applicant confirms that the information above is accurate and complete; false "
    "statements may void the request.",
    "Référence interne : DOS-2026/0147 — Montant des frais : 48,50 € (quarante-huit euros et "
    "cinquante centimes).",
)
LABELS = ("Nom :", "Prénom :", "Date de naissance :", "Classe souhaitée :", "Courriel :")
OPTIONS = ("oui", "non", "parfois")
CELLS = (
    "Jour", "Matin", "Après-midi",
    "Lundi", "8h30 – 11h30", "13h30 – 16h30",
    "Mardi", "8h30 – 11h30", "—",
)  # fmt: skip
#: Underline x extent, rule width (points).
UNDERLINE_X = (170.0, 420.0)
RULE_WIDTH = 0.8
#: Checkbox side (points).
BOX_SIDE = 11.0
#: Table cell size (points).
CELL_W, CELL_H = 160.0, 22.0
#: Dotted leader line ("Observations ......") below the table: no drawn rule.
LEADER_LABEL = "Observations :"
LEADER = "." * 60


@dataclass(frozen=True)
class ScanGeometry:
    """Where the drawn rules and boxes are (page space of the unrotated page, points)."""

    underlines: tuple[tuple[float, float, float], ...]  # (x0, x1, y)
    boxes: tuple[tuple[float, float, float, float], ...]  # checkbox squares
    table: tuple[float, float, float, float]  # x0, y0, x1, y1
    leader: tuple[float, float]  # (x, baseline y) of the dotted leader

    @property
    def h_rules(self) -> list[float]:
        """y of every long horizontal rule (underlines and table rows)."""
        ys = [u[2] for u in self.underlines]
        ys += [self.table[1] + r * CELL_H for r in range(4)]
        return sorted(ys)

    @property
    def v_rules(self) -> list[float]:
        """x of every long vertical rule (table columns)."""
        return [self.table[0] + c * CELL_W for c in range(4)]

    def cell(self, row: int, col: int) -> tuple[float, float, float, float]:
        x0, y0 = self.table[0] + col * CELL_W, self.table[1] + row * CELL_H
        return (x0, y0, x0 + CELL_W, y0 + CELL_H)


@dataclass(frozen=True)
class ScanFixture:
    path: Path
    words: tuple[str, ...]
    geometry: ScanGeometry
    skew: float
    dpi: int
    rotate: int


def draw_form(page: pymupdf.Page, *, leader: bool = True) -> tuple[list[str], ScanGeometry]:
    """Draw the form on an A4 ``page``; returns its words and geometry."""
    y = 60.0
    page.insert_text((50, y), TITLE, fontsize=14, fontname="hebo")
    y += 30
    for p in PARAGRAPHS:
        page.insert_text((50, y), p, fontsize=9.5, fontname="helv")
        y += 16
    y += 20
    underlines = []
    for i, label in enumerate(LABELS):
        yy = y + i * 28
        page.insert_text((50, yy), label, fontsize=10, fontname="helv")
        page.draw_line((UNDERLINE_X[0], yy + 3), (UNDERLINE_X[1], yy + 3), width=RULE_WIDTH)
        underlines.append((UNDERLINE_X[0], UNDERLINE_X[1], yy + 3))
    y += len(LABELS) * 28 + 10
    page.insert_text((50, y), "Cantine :", fontsize=10, fontname="helv")
    boxes = []
    for j, option in enumerate(OPTIONS):
        x = 120 + j * 80
        rect = pymupdf.Rect(x, y - 9, x + BOX_SIDE, y + 2)
        page.draw_rect(rect, width=RULE_WIDTH)
        boxes.append(tuple(rect))
        page.insert_text((x + 16, y), option, fontsize=10, fontname="helv")
    y += 30
    tx0, ty0 = 50.0, y
    for r in range(4):
        page.draw_line((tx0, ty0 + r * CELL_H), (tx0 + 3 * CELL_W, ty0 + r * CELL_H), width=0.8)
    for c in range(4):
        page.draw_line((tx0 + c * CELL_W, ty0), (tx0 + c * CELL_W, ty0 + 3 * CELL_H), width=0.8)
    for k, text in enumerate(CELLS):
        r, c = divmod(k, 3)
        page.insert_text(
            (tx0 + c * CELL_W + 4, ty0 + r * CELL_H + 15), text, fontsize=9.5, fontname="helv"
        )
    leader_at = (150.0, ty0 + 3 * CELL_H + 40)
    if leader:
        page.insert_text((50, leader_at[1]), LEADER_LABEL, fontsize=10, fontname="helv")
        page.insert_text(leader_at, LEADER, fontsize=10, fontname="helv")
    words = [w[4] for w in page.get_text("words")]
    geometry = ScanGeometry(
        underlines=tuple(underlines),
        boxes=tuple(boxes),  # type: ignore[arg-type]
        table=(tx0, ty0, tx0 + 3 * CELL_W, ty0 + 3 * CELL_H),
        leader=leader_at,
    )
    return words, geometry


def _gray(page: pymupdf.Page, dpi: int) -> QImage:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return QImage(
        pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_Grayscale8
    ).copy()


def degrade(
    image: QImage, *, skew: float = 0.0, blur: float = 0.0, noise: float = 0.0, seed: int = 42
) -> QImage:
    """``image`` turned by ``skew`` degrees clockwise about its centre on a light grey
    canvas, blurred (down/up-scaling by ``1 + blur``) and salted with ``noise`` × pixels
    black or white dots."""
    w, h = image.width(), image.height()
    out = image
    if skew:
        out = QImage(w, h, QImage.Format.Format_Grayscale8)
        out.fill(QColor(235, 235, 235))
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setTransform(
            QTransform().translate(w / 2, h / 2).rotate(skew).translate(-w / 2, -h / 2)
        )
        painter.drawImage(0, 0, image)
        painter.end()
    if blur:
        smooth = Qt.TransformationMode.SmoothTransformation
        small = out.scaled(
            max(1, round(w / (1 + blur))), max(1, round(h / (1 + blur))),
            Qt.AspectRatioMode.IgnoreAspectRatio, smooth,
        )  # fmt: skip
        out = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
        out = out.convertToFormat(QImage.Format.Format_Grayscale8)
    if noise:
        out = out.copy()
        rnd = random.Random(seed)
        for _ in range(int(w * h * noise)):
            v = 0 if rnd.random() < 0.5 else 255
            out.setPixel(rnd.randrange(w), rnd.randrange(h), QColor(v, v, v).rgb())
    return out


def _jpeg(image: QImage, quality: int) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "JPEG", quality)
    buf.close()
    return bytes(data.data())


def make_scan_pdf(
    path: Path,
    *,
    skew: float = 0.0,
    noise: float = 0.0,
    blur: float = 0.0,
    dpi: int = 300,
    jpeg: int = 75,
    rotate: int = 0,
) -> ScanFixture:
    """An image-only A4 page: the form rendered at ``dpi``, degraded, JPEG ``jpeg``.
    With ``rotate`` the page has that /Rotate and stores the image turned back by it (as
    scanners do), so the page still displays the form upright: :attr:`ScanFixture.geometry`
    is in displayed page space in every case."""
    src = pymupdf.open()
    words, geometry = draw_form(src.new_page(width=A4[0], height=A4[1]))
    image = degrade(_gray(src[0], dpi), skew=skew, blur=blur, noise=noise)
    src.close()
    rotate = int(rotate) % 360
    if rotate:
        # Stored turned back by the page rotation: the page *displays* upright.
        image = image.transformed(QTransform().rotate(-rotate))
    size = A4 if rotate in (0, 180) else (A4[1], A4[0])
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_image(page.rect, stream=_jpeg(image, jpeg))
    if rotate:
        page.set_rotation(rotate)
    doc.save(str(path), deflate=True)
    doc.close()
    return ScanFixture(Path(path), tuple(words), geometry, skew, dpi, rotate)


#: Text of the digital page of :func:`make_mixed_pdf`.
MIXED_TEXT = "Page numérique avec du texte"


def make_mixed_pdf(path: Path, scan: ScanFixture) -> Path:
    """Page 1: digital text (:data:`MIXED_TEXT`); page 2: the scan of ``scan``."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((72, 100), MIXED_TEXT, fontsize=14, fontname="helv")
    with pymupdf.open(str(scan.path)) as other:
        doc.insert_pdf(other)
    doc.save(str(path), deflate=True)
    doc.close()
    return Path(path)


def copy_to(fixture: ScanFixture, directory: Path) -> Path:
    """A private copy of ``fixture``'s file (to save over)."""
    target = Path(directory) / fixture.path.name
    shutil.copyfile(fixture.path, target)
    return target


def recall(truth: tuple[str, ...] | list[str], found: list[str]) -> float:
    """Fraction of ``truth`` words found (each found word matches one true word)."""
    pool = list(truth)
    hits = 0
    for word in found:
        if word in pool:
            pool.remove(word)
            hits += 1
    return hits / len(truth) if truth else 0.0
