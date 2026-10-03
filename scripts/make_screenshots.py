"""Regenerate the README screenshots (``docs/screenshots/*.png``).

    uv run python scripts/make_screenshots.py [--out DIR] [--only NAME ...] [--theme dark]

Starts the real application window on the native platform (so the Windows style
renders), opens **synthetic** demo PDFs generated here (fake names and addresses
only), drives each tool programmatically and saves a PNG per state. Everything the
application would persist (settings, recent files, log, signatures) goes to a temporary
directory that is deleted at the end: the user's own settings and signatures are never
read or written, and no demo document is ever saved.

The window and its dialogs are captured from the screen (title bar and native menus
included), so keep the screen unlocked and don't move the mouse over the window while
the script runs (about a minute). The window always stays on top of the others. The
colour scheme is light unless ``--theme dark`` (whatever the system's), and the window is
1280×800 at 100 % display scaling (a higher scaling is downscaled to 1280 pixels wide).
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

log = logging.getLogger("make_screenshots")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "screenshots"
WINDOW_SIZE = (1280, 800)
WINDOW_POS = (60, 40)
MAX_WIDTH = 1280
FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
BODY_FONT = {"fontname": "calibri", "fontfile": str(FONTS / "calibri.ttf")}
BOLD_FONT = {"fontname": "calibrib", "fontfile": str(FONTS / "calibrib.ttf")}
A4 = (595.0, 842.0)
INK = (0.13, 0.13, 0.15)
ACCENT = (0.12, 0.33, 0.6)
GREY = (0.45, 0.45, 0.5)

LOREM = (
    "ACME Corporation closed the quarter with steady growth across all regions. Revenue "
    "rose to 4.2 million, driven by the new product line and strong demand from repeat "
    "customers. Operating costs remained under control thanks to the logistics programme "
    "launched last spring, and the team expects a similar trend in the coming months."
)
LOREM2 = (
    "The customer satisfaction survey received 1,250 answers. Delivery times and the "
    "quality of support were rated highly, while the online ordering process was the most "
    "frequent suggestion for improvement. A redesigned checkout will be released next "
    "quarter, together with a new help centre and live chat during office hours."
)
LOREM3 = (
    "Next steps: finalise the budget for the coming year, open the second warehouse in "
    "Springfield and recruit four new members for the customer service team. Each "
    "department will present its objectives at the planning meeting on 14 November."
)


# -- synthetic documents --------------------------------------------------------------------
def _text(page: pymupdf.Page, pos: tuple[float, float], text: str, size: float = 11,
          bold: bool = False, color: tuple[float, float, float] = INK) -> None:  # fmt: skip
    font = BOLD_FONT if bold else BODY_FONT
    page.insert_text(pos, text, fontsize=size, color=color, **font)


def _para(page: pymupdf.Page, rect: tuple[float, float, float, float], text: str,
          size: float = 11) -> None:  # fmt: skip
    page.insert_textbox(pymupdf.Rect(rect), text, fontsize=size, color=INK,
                        lineheight=1.35, **BODY_FONT)  # fmt: skip


def _header(page: pymupdf.Page, title: str, number: int, total: int) -> None:
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(0, 0, A4[0], 8))
    shape.finish(fill=ACCENT, color=None, width=0)
    shape.draw_line((56, 800), (A4[0] - 56, 800))
    shape.finish(color=(0.8, 0.8, 0.82), width=0.6)
    shape.commit()
    _text(page, (56, 44), "ACME Corporation", 10, bold=True, color=ACCENT)
    _text(page, (A4[0] - 150, 44), "Quarterly report — Q3", 10, color=GREY)
    _text(page, (56, 816), title, 9, color=GREY)
    _text(page, (A4[0] - 100, 816), f"Page {number} of {total}", 9, color=GREY)


def _bar_chart(page: pymupdf.Page, x: float, y: float, w: float, h: float) -> None:
    values = (2.6, 3.1, 2.9, 3.6, 3.9, 4.2)
    labels = ("Q2", "Q3", "Q4", "Q1", "Q2", "Q3")
    shape = page.new_shape()
    shape.draw_line((x, y + h), (x + w, y + h))
    shape.finish(color=GREY, width=0.8)
    slot = w / len(values)
    for i, value in enumerate(values):
        bh = h * value / 4.6
        bx = x + i * slot + slot * 0.22
        shape.draw_rect(pymupdf.Rect(bx, y + h - bh, bx + slot * 0.56, y + h))
        fill = ACCENT if i == len(values) - 1 else (0.62, 0.72, 0.85)
        shape.finish(fill=fill, color=None, width=0)
    shape.commit()
    for i, label in enumerate(labels):
        _text(page, (x + i * slot + slot * 0.36, y + h + 14), label, 9, color=GREY)


def _table(page: pymupdf.Page, x: float, y: float, rows: list[tuple[str, ...]],
           widths: tuple[float, ...]) -> None:  # fmt: skip
    shape = page.new_shape()
    row_h = 22
    total_w = sum(widths)
    shape.draw_rect(pymupdf.Rect(x, y, x + total_w, y + row_h))
    shape.finish(fill=(0.91, 0.94, 0.98), color=None, width=0)
    for i in range(len(rows) + 1):
        shape.draw_line((x, y + i * row_h), (x + total_w, y + i * row_h))
    shape.finish(color=(0.75, 0.78, 0.82), width=0.6)
    shape.commit()
    for r, row in enumerate(rows):
        cx = x
        for c, cell in enumerate(row):
            _text(page, (cx + 6, y + r * row_h + 15), cell, 10, bold=(r == 0))
            cx += widths[c]


def make_report(path: Path, pages: int = 6) -> Path:
    """A multi-page "quarterly report" in Calibri (embedded, so its text is editable)."""
    doc = pymupdf.open()
    sections = (
        ("Quarterly Report", "Summary"),
        ("Sales by region", "Regional results"),
        ("Customer feedback", "Survey results"),
        ("Operations", "Logistics"),
        ("Outlook", "Next quarter"),
        ("Appendix", "Key figures"),
    )
    for n in range(pages):
        title, sub = sections[n % len(sections)]
        page = doc.new_page(width=A4[0], height=A4[1])
        _header(page, "Quarterly report — Q3", n + 1, pages)
        _text(page, (56, 110), title, 26, bold=True, color=ACCENT)
        _text(page, (56, 140), sub, 13, color=GREY)
        _para(page, (56, 165, 540, 290), LOREM)
        _bar_chart(page, 70, 305, 300, 150)
        _text(page, (390, 330), "Revenue (million)", 10, bold=True)
        _para(page, (390, 340, 540, 460), "Six quarters of revenue. The last bar is the "
              "current quarter, the best result so far.", 10)  # fmt: skip
        _para(page, (56, 490, 540, 600), LOREM2)
        _table(page, 56, 615, [("Region", "Orders", "Revenue", "Change"),
                               ("North", "3,410", "1.48 M", "+6 %"),
                               ("South", "2,870", "1.21 M", "+4 %"),
                               ("East", "1,960", "0.86 M", "+9 %"),
                               ("West", "1,520", "0.65 M", "+2 %")],
               (150, 110, 110, 110))  # fmt: skip
        _para(page, (56, 735, 540, 795), LOREM3, 10)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def make_acroform(path: Path) -> Path:
    """A one-page membership application with real (AcroForm) fields."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(0, 0, A4[0], 90))
    shape.finish(fill=ACCENT, color=None, width=0)
    shape.commit()
    _text(page, (56, 50), "ACME Sports Club", 24, bold=True, color=(1, 1, 1))
    _text(page, (56, 72), "Membership application 2026–2027", 12, color=(0.9, 0.93, 1))
    _text(page, (56, 130), "1. About you", 14, bold=True, color=ACCENT)

    def field(kind: int, name: str, rect: tuple[float, float, float, float], **kw) -> None:
        widget = pymupdf.Widget()
        widget.field_type = kind
        widget.field_name = name
        widget.rect = pymupdf.Rect(rect)
        widget.text_font = "Helv"
        widget.text_fontsize = 11
        widget.border_color = (0.6, 0.65, 0.72)
        widget.border_width = 0.8
        for key, value in kw.items():
            setattr(widget, key, value)
        page.add_widget(widget)

    text = pymupdf.PDF_WIDGET_TYPE_TEXT
    rows = (
        ("First name", "first_name", (56, 150, 290, 172), (310, 150, 540, 172), "Last name",
         "last_name"),
        ("Date of birth", "birth_date", (56, 200, 290, 222), (310, 200, 540, 222), "Phone",
         "phone"),
    )  # fmt: skip
    for label1, name1, rect1, rect2, label2, name2 in rows:
        _text(page, (rect1[0], rect1[1] - 5), label1, 10, color=GREY)
        _text(page, (rect2[0], rect2[1] - 5), label2, 10, color=GREY)
        field(text, name1, rect1)
        field(text, name2, rect2)
    _text(page, (56, 245), "Address", 10, color=GREY)
    field(text, "address", (56, 250, 540, 272))
    _text(page, (56, 295), "Postcode", 10, color=GREY)
    field(text, "postcode", (56, 300, 170, 322))
    _text(page, (190, 295), "City", 10, color=GREY)
    field(text, "city", (190, 300, 540, 322))
    _text(page, (56, 345), "Email", 10, color=GREY)
    field(text, "email", (56, 350, 540, 372))

    _text(page, (56, 415), "2. Membership", 14, bold=True, color=ACCENT)
    _text(page, (56, 440), "Type", 10, color=GREY)
    field(pymupdf.PDF_WIDGET_TYPE_COMBOBOX, "type", (56, 445, 290, 467),
          choice_values=["Standard", "Family", "Student", "Senior"],
          field_value="Standard")  # fmt: skip
    _text(page, (310, 440), "Start date", 10, color=GREY)
    field(text, "start_date", (310, 445, 540, 467))
    checks = (
        ("sport_tennis", "Tennis", 56),
        ("sport_swimming", "Swimming", 176),
        ("sport_climbing", "Climbing", 296),
        ("sport_yoga", "Yoga", 416),
    )
    _text(page, (56, 495), "Activities", 10, color=GREY)
    for name, label, x in checks:
        field(pymupdf.PDF_WIDGET_TYPE_CHECKBOX, name, (x, 503, x + 14, 517), field_value=False)
        _text(page, (x + 20, 514), label, 11)
    _text(page, (56, 550), "Comments", 10, color=GREY)
    field(text, "comments", (56, 555, 540, 625), field_flags=pymupdf.PDF_TX_FIELD_IS_MULTILINE)
    field(pymupdf.PDF_WIDGET_TYPE_CHECKBOX, "terms", (56, 650, 70, 664), field_value=False)
    _text(page, (78, 661), "I have read and accept the club rules and the privacy notice.", 11)
    _text(page, (56, 720), "Signature", 10, color=GREY)
    field(text, "signed_at", (310, 725, 540, 747))
    _text(page, (310, 720), "Place and date", 10, color=GREY)
    shape = page.new_shape()
    shape.draw_line((56, 760), (290, 760))
    shape.finish(color=GREY, width=0.6)
    shape.commit()
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


#: Flat (Word-like) form geometry, page points: label baselines and the rules/boxes.
FLAT_RULES = {
    "name": (150, 172, 540),  # x0, y, x1 of the rule after "Full name:"
    "address": (150, 202, 540),
    "city": (150, 232, 360),
    "postcode": (440, 232, 540),
    "date": (150, 262, 300),
}
FLAT_BOXES = {
    "yes1": (400, 318), "no1": (470, 318),
    "yes2": (400, 346), "no2": (470, 346),
    "yes3": (400, 374), "no3": (470, 374),
    "opt1": (76, 438), "opt2": (76, 462), "opt3": (76, 486),
}  # fmt: skip
FLAT_BOX = 11.0
FLAT_TABLE = (56, 540, 540, 650)


def make_flat_form(path: Path) -> Path:
    """A form exported from a word processor: labels, rules, boxes, a table — no fields."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    _text(page, (56, 80), "Volunteer registration form", 22, bold=True)
    _text(page, (56, 102), "Riverside Community Centre — please fill in and return to the "
          "front desk.", 10, color=GREY)  # fmt: skip
    _text(page, (56, 140), "Personal details", 13, bold=True)
    labels = {"name": "Full name:", "address": "Address:", "city": "City:", "date": "Date:"}
    shape = page.new_shape()
    for key, (x0, y, x1) in FLAT_RULES.items():
        if key in labels:
            _text(page, (56, y - 3), labels[key], 11)
        shape.draw_line((x0, y), (x1, y))
    _text(page, (380, 229), "Postcode:", 11)
    shape.finish(color=INK, width=0.6)
    _text(page, (56, 300), "Availability", 13, bold=True)
    _text(page, (400, 300), "Yes", 10, color=GREY)
    _text(page, (470, 300), "No", 10, color=GREY)
    for i, question in enumerate(("Weekdays (morning)", "Weekday evenings", "Weekends")):
        _text(page, (56, 327 + 28 * i), question, 11)
    for x, y in FLAT_BOXES.values():
        shape.draw_rect(pymupdf.Rect(x, y, x + FLAT_BOX, y + FLAT_BOX))
    shape.finish(color=INK, width=0.75)
    _text(page, (56, 420), "Preferred activity (tick one)", 13, bold=True)
    for i, label in enumerate(("Welcoming visitors", "Garden and maintenance",
                               "Homework club")):  # fmt: skip
        _text(page, (96, 447 + 24 * i), label, 11)
    _text(page, (56, 528), "Emergency contact", 13, bold=True)
    tx0, ty0, tx1, ty1 = FLAT_TABLE
    cols = (tx0, 200, 380, tx1)
    rows = (ty0, ty0 + 24, ty0 + 52, ty0 + 80, ty1)
    for x in cols:
        shape.draw_line((x, ty0), (x, ty1))
    for y in rows:
        shape.draw_line((tx0, y), (tx1, y))
    shape.finish(color=INK, width=0.6)
    shape.commit()
    for x, head in zip(cols, ("Name", "Relationship", "Phone"), strict=False):
        _text(page, (x + 6, ty0 + 16), head, 10, bold=True)
    _text(page, (56, 700), "Signature:", 11)
    _text(page, (330, 700), "Date:", 11)
    shape = page.new_shape()
    shape.draw_line((115, 703), (300, 703))
    shape.draw_line((362, 703), (540, 703))
    shape.finish(color=INK, width=0.6)
    shape.commit()
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


#: Signature line of the agreement (page points): x0, y, x1.
SIG_LINE = (330, 690, 520)


def make_agreement(path: Path) -> Path:
    """A one-page service agreement with a signature line."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    _text(page, (56, 80), "Service Agreement", 24, bold=True, color=ACCENT)
    _text(page, (56, 104), "Agreement no. 2026-0147", 11, color=GREY)
    _para(page, (56, 130, 540, 230),
          "This agreement is made between ACME Corporation, 1 Industrial Way, Springfield "
          "(the \"Provider\"), and Example Ltd, 42 Market Street, Riverside (the "
          "\"Client\"). The Provider agrees to deliver the maintenance services described "
          "below for a period of twelve months starting on 1 January 2027.")  # fmt: skip
    _text(page, (56, 260), "1. Services", 13, bold=True)
    _para(page, (56, 270, 540, 350),
          "Quarterly inspection of the equipment, repairs within two working days, a "
          "telephone helpline from 8 am to 6 pm and an annual report on the condition of "
          "the installation.")  # fmt: skip
    _text(page, (56, 380), "2. Price and payment", 13, bold=True)
    _para(page, (56, 390, 540, 470),
          "The annual fee is 2,400 (two thousand four hundred), payable in four "
          "instalments at the start of each quarter. Invoices are due within 30 days.")  # fmt: skip
    _text(page, (56, 500), "3. Termination", 13, bold=True)
    _para(page, (56, 510, 540, 580),
          "Either party may end this agreement with three months' written notice.")  # fmt: skip
    _text(page, (56, 640), "For the Provider", 11, bold=True)
    _text(page, (330, 640), "For the Client", 11, bold=True)
    _text(page, (56, 708), "John Smith, Director", 10, color=GREY)
    _text(page, (330, 708), "Jane Doe, Managing Director", 10, color=GREY)
    shape = page.new_shape()
    shape.draw_line((56, SIG_LINE[1]), (246, SIG_LINE[1]))
    shape.draw_line((SIG_LINE[0], SIG_LINE[1]), (SIG_LINE[2], SIG_LINE[1]))
    shape.finish(color=INK, width=0.6)
    shape.commit()
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def make_scanned(path: Path, workdir: Path) -> Path:
    """Two image-only pages: a letter rendered to a grey, slightly tilted JPEG."""
    src = pymupdf.open()
    for n in range(2):
        page = src.new_page(width=A4[0], height=A4[1])
        _text(page, (56, 80), "Riverside Community Centre", 18, bold=True)
        _text(page, (56, 98), "12 Park Lane, Riverside", 10, color=GREY)
        _text(page, (380, 150), "Riverside, 3 March 2026", 11)
        _text(page, (56, 200), "Dear Ms Doe," if n == 0 else "Annual programme", 12,
              bold=(n == 1))  # fmt: skip
        _para(page, (56, 220, 540, 420), (LOREM if n == 0 else LOREM2) + " " + LOREM3)
        _text(page, (56, 470), "Kind regards," if n == 0 else "The volunteers team", 11)
    doc = pymupdf.open()
    for n in range(2):
        pix = src[n].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride,
                       QImage.Format.Format_Grayscale8).copy()  # fmt: skip
        tilted = QImage(image.size(), QImage.Format.Format_RGB32)
        tilted.fill(QColor(236, 234, 228))
        painter = QPainter(tilted)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(image.width() / 2, image.height() / 2)
        painter.rotate(0.6 if n == 0 else -0.4)
        painter.translate(-image.width() / 2, -image.height() / 2)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.drawImage(0, 0, image)
        painter.end()
        jpeg = workdir / f"scan{n}.jpg"
        tilted.convertToFormat(QImage.Format.Format_Grayscale8).save(str(jpeg), "JPEG", 70)
        page = doc.new_page(width=A4[0], height=A4[1])
        page.insert_image(page.rect, filename=str(jpeg))
    src.close()
    doc.save(path, deflate=True)
    doc.close()
    return path


def make_signature_photo(path: Path) -> Path:
    """A synthetic "photo" of a handwritten signature: a drawn scribble on paper."""
    w, h = 900, 320
    image = QImage(w, h, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, w, h)
    gradient.setColorAt(0, QColor(247, 245, 238))
    gradient.setColorAt(1, QColor(226, 223, 214))
    painter.fillRect(image.rect(), gradient)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(24, 36, 92), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)  # fmt: skip
    painter.setPen(pen)
    # A big looping capital, then a run of joined loops, then an underline swoosh.
    path_ = QPainterPath(QPointF(150, 70))
    path_.cubicTo(QPointF(120, 180), QPointF(60, 260), QPointF(110, 250))
    path_.cubicTo(QPointF(170, 240), QPointF(150, 120), QPointF(110, 150))
    path_.cubicTo(QPointF(80, 175), QPointF(200, 190), QPointF(240, 160))
    x = 240.0
    for i in range(9):
        height = 55 if i % 3 == 1 else 30
        path_.cubicTo(QPointF(x + 15, 160 - height), QPointF(x + 40, 160 - height),
                      QPointF(x + 30, 175))  # fmt: skip
        path_.cubicTo(QPointF(x + 25, 190), QPointF(x + 50, 185), QPointF(x + 62, 165))
        x += 52
    painter.drawPath(path_)
    swoosh = QPainterPath(QPointF(90, 235))
    swoosh.cubicTo(QPointF(300, 205), QPointF(600, 215), QPointF(800, 190))
    painter.setPen(QPen(QColor(24, 36, 92), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawPath(swoosh)
    painter.drawEllipse(QPointF(612, 92), 4, 4)
    painter.end()
    image.save(str(path), "PNG")
    return path


# -- driving the application ----------------------------------------------------------------
def pump(ms: int = 0) -> None:
    """Process events for ``ms`` milliseconds (renders run on a worker thread)."""
    app = QApplication.instance()
    end = time.monotonic() + ms / 1000
    while True:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if time.monotonic() >= end:
            return
        time.sleep(0.01)


def viewport_point(window, page: int, x: float, y: float) -> QPoint:
    return window.page_view.page_rect_to_viewport(page, QRectF(x, y, 0, 0)).topLeft()


def mouse(window, kind: QEvent.Type, page: int, x: float, y: float,
          buttons: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:  # fmt: skip
    """Send one mouse event to the page view at page point (x, y) (no real cursor)."""
    viewport = window.page_view.viewport()
    pos = QPointF(viewport_point(window, page, x, y))
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else buttons
    held = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else buttons
    event = QMouseEvent(kind, pos, QPointF(viewport.mapToGlobal(pos.toPoint())), button, held,
                        Qt.KeyboardModifier.NoModifier)  # fmt: skip
    QApplication.sendEvent(viewport, event)
    pump(30)


def scroll_to(window, page: int, y: float) -> None:
    """Scroll the page view so that page point ``y`` is at the top of the viewport."""
    bar = window.page_view.verticalScrollBar()
    bar.setValue(bar.value() + viewport_point(window, page, 0, y).y())
    pump(150)


def find_text(path: Path, page: int, phrase: str) -> QRectF:
    """Page-space rect of the first occurrence of ``phrase`` (one line)."""
    with pymupdf.open(path) as doc:
        hits = doc[page].search_for(phrase)
    if not hits:
        raise RuntimeError(f"{phrase!r} not found in {path.name}")
    r = hits[0]
    return QRectF(r.x0, r.y0, r.width, r.height)


def drag_over(window, page: int, rect: QRectF) -> None:
    """Drag across a line of text from the left to the right end of ``rect``."""
    y = rect.center().y()
    drag(window, page, (rect.left() + 1, y), (rect.right() - 1, y))


def click(window, page: int, x: float, y: float) -> None:
    mouse(window, QEvent.Type.MouseButtonPress, page, x, y)
    mouse(window, QEvent.Type.MouseButtonRelease, page, x, y)


def hover(window, page: int, x: float, y: float) -> None:
    mouse(window, QEvent.Type.MouseMove, page, x, y, Qt.MouseButton.NoButton)


def drag(window, page: int, start: tuple[float, float], end: tuple[float, float]) -> None:
    mouse(window, QEvent.Type.MouseButtonPress, page, *start)
    for i in range(1, 9):
        t = i / 8
        mouse(window, QEvent.Type.MouseMove, page, start[0] + (end[0] - start[0]) * t,
              start[1] + (end[1] - start[1]) * t)  # fmt: skip
    mouse(window, QEvent.Type.MouseButtonRelease, page, *end)


def type_text(text: str, widget: QWidget | None = None) -> None:
    target = widget or QApplication.focusWidget()
    if target is None:
        raise RuntimeError("no focused widget to type into")
    for char in text:
        if char == "\n":
            QTest.keyClick(target, Qt.Key.Key_Return)
            continue
        # QTest.keyClicks() crashes on some non-ASCII characters: send the text itself.
        code = ord(char.upper()) if char.isascii() else int(Qt.Key.Key_unknown)
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(
                target, QKeyEvent(kind, code, Qt.KeyboardModifier.NoModifier, char)
            )
    pump(50)


def key(widget: QWidget, k: Qt.Key, mods=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(widget, k, mods)
    pump(50)


class Shooter:
    """Creates the windows and saves the captures."""

    def __init__(self, app: QApplication, out: Path, work: Path, store) -> None:
        self.app = app
        self.out = out
        self.work = work
        self.store = store
        self.saved: list[Path] = []
        self.windows: list[QWidget] = []

    def window(self, document: Path | None = None, zoom: float = 100.0):
        from pdfeditor.core.settings import Settings
        from pdfeditor.ui.main_window import MainWindow

        window = MainWindow(Settings(), self.store)
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        window.resize(*WINDOW_SIZE)
        window.move(*WINDOW_POS)
        window.show()
        window.raise_()
        window.activateWindow()
        QTest.qWaitForWindowExposed(window)
        self.windows.append(window)
        if document is not None and not window.open_file(str(document)):
            raise RuntimeError(f"could not open {document.name}")
        window.page_view.set_zoom_percent(zoom)
        pump(400)
        return window

    def close(self, window) -> None:
        window.document_view.commit_pending_edits()
        window.undo_stack.setClean()  # never asks to save: nothing is written
        window.close()
        window.deleteLater()
        pump(100)

    def shoot(self, name: str, window: QWidget, *popups: QWidget, settle: int = 900) -> None:
        """Capture ``window``'s frame (and the ``popups`` drawn over it) from the screen."""
        for popup in popups:
            popup.raise_()
        pump(settle)
        frame = window.frameGeometry()
        inner = window.geometry()
        # The visible frame: the title bar above the client area, no invisible borders.
        rect = QRect(inner.left(), frame.top(), inner.width(), inner.bottom() - frame.top() + 1)
        for popup in popups:
            rect = rect.united(popup.frameGeometry().intersected(QRect(
                popup.geometry().left(), popup.frameGeometry().top(),
                popup.geometry().width(), popup.frameGeometry().height())))  # fmt: skip
        screen = window.screen()
        geo = screen.geometry()
        pixmap = screen.grabWindow(0, rect.x() - geo.x(), rect.y() - geo.y(), rect.width(),
                                   rect.height())  # fmt: skip
        image = pixmap.toImage()
        if image.width() > MAX_WIDTH:
            image = image.scaledToWidth(MAX_WIDTH, Qt.TransformationMode.SmoothTransformation)
        image = image.convertToFormat(QImage.Format.Format_RGB888)
        target = self.out / f"{name}.png"
        if not image.save(str(target), "PNG", 0):
            raise RuntimeError(f"could not save {target}")
        self.saved.append(target)
        log.info("saved %s (%d×%d)", target.name, image.width(), image.height())


# -- the shots ------------------------------------------------------------------------------
def shot_main_window(s: Shooter) -> None:
    w = s.window(s.work / "quarterly-report.pdf", zoom=100)
    w.thumbnails.select_pages([0])
    s.shoot("main-window", w, settle=1500)
    s.close(w)


def shot_form_filling(s: Shooter) -> None:
    w = s.window(s.work / "application-form.pdf", zoom=100)
    w.act_highlight_fields.setChecked(True)
    editor = w.document_view.field_editor

    def fill(x: float, y: float, text: str, commit: bool = True) -> None:
        scroll_to(w, 0, y - 120)
        click(w, 0, x, y)
        pump(150)
        if editor.editor is None:
            raise RuntimeError(f"no field editor at {x}, {y}")
        editor.editor.setFocus()
        type_text(text, editor.editor)
        if commit:
            editor.commit()
            pump(100)

    fill(100, 161, "Jane")
    fill(350, 161, "Doe")
    fill(100, 211, "14/02/1990")
    fill(350, 211, "+1 555 0100")
    fill(100, 261, "12 rue des Érables, appartement 3")
    fill(100, 311, "H2X 1Y4")
    fill(230, 311, "Montréal")
    scroll_to(w, 0, 341)
    click(w, 0, 100, 456)  # the membership type (a combo box)
    pump(150)
    combo = editor.editor
    combo.setCurrentIndex(combo.findText("Family"))
    editor.commit()
    fill(100, 565, "Beginner in climbing; happy to help at the summer tournament.")
    for x, y in ((63, 510), (303, 510), (63, 657)):  # Tennis, Climbing, the club rules
        scroll_to(w, 0, y - 120)
        click(w, 0, x, y)
    fill(100, 361, "jane.doe@example.com", commit=False)  # left open in the editor
    scroll_to(w, 0, 112)
    w.statusBar().clearMessage()
    s.shoot("form-filling", w)
    s.close(w)


def shot_flat_form(s: Shooter) -> None:
    w = s.window(s.work / "registration-form.pdf", zoom=100)
    scroll_to(w, 0, 45)

    def text_box(x: float, y: float, text: str) -> None:
        w.act_text_tool.trigger()
        click(w, 0, x, y)
        pump(150)
        type_text(text)
        key(QApplication.focusWidget(), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

    rule = FLAT_RULES
    text_box(160, rule["name"][1] - 4, "Jane Doe")
    text_box(160, rule["address"][1] - 4, "42 Market Street")
    text_box(160, rule["city"][1] - 4, "Riverside")
    text_box(450, rule["postcode"][1] - 4, "RS1 4AB")
    text_box(160, rule["date"][1] - 4, "3 March 2026")
    for tool, keys in ((w.act_stamp_check, ("yes1", "yes3", "opt2")),
                       (w.act_stamp_cross, ("no2",))):  # fmt: skip
        tool.trigger()
        for k in keys:
            x, y = FLAT_BOXES[k]
            click(w, 0, x + FLAT_BOX / 2 + 1, y + FLAT_BOX / 2 + 1)
    w.document_view.annot_selection.clear()
    w.act_stamp_check.trigger()
    x, y = FLAT_BOXES["opt3"]
    hover(w, 0, x + FLAT_BOX / 2 + 2, y + FLAT_BOX / 2)  # the snap preview
    w.statusBar().clearMessage()
    s.shoot("flat-form", w)
    s.close(w)


def shot_signature(s: Shooter) -> None:
    from pdfeditor.ui.signature_dialogs import SignatureImportDialog

    photo = make_signature_photo(s.work / "jane-doe-signature.png")
    w = s.window(s.work / "service-agreement.pdf", zoom=100)
    dialog = SignatureImportDialog(s.store, w, path=str(photo))
    dialog.name_edit.setText("Jane Doe")
    pump(300)
    dialog.accept()
    pump(200)
    record = dialog.result_record
    if record is None:
        raise RuntimeError("the signature was not imported")
    w.choose_signature(record.id)
    pv = w.page_view
    pv.ensureVisible(pv.page_item(0).mapRectToScene(QRectF(300, 600, 240, 140)), 40, 40)
    pump(300)
    x0, y, x1 = SIG_LINE
    drag(w, 0, (x0 + 10, y - 52), (x1 - 30, y + 6))
    pump(200)
    w.act_hand_tool.trigger()
    w.document_view.annot_selection.clear()
    again = SignatureImportDialog(s.store, w, path=str(photo))
    again.name_edit.setText("Jane Doe")
    again.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    again.show()
    geo = w.geometry()
    again.move(geo.left() + 60, geo.top() + 140)
    pump(300)
    s.shoot("signature", w, again)
    again.reject()
    s.close(w)


def shot_markup(s: Shooter) -> None:
    w = s.window(s.work / "quarterly-report.pdf", zoom=125)
    pv = w.page_view
    pv.ensureVisible(pv.page_item(0).mapRectToScene(QRectF(56, 100, 480, 380)), 10, 10)
    pump(300)
    doc = s.work / "quarterly-report.pdf"
    w.act_highlight.trigger()
    drag_over(w, 0, find_text(doc, 0, "line and strong demand from repeat customers"))
    w.act_underline.trigger()
    drag_over(w, 0, find_text(doc, 0, "Revenue rose to 4.2 million"))
    w.act_strikeout.trigger()
    drag_over(w, 0, find_text(doc, 0, "launched last spring"))
    w.act_select_text.trigger()
    w.document_view.annot_selection.clear()
    drag(w, 0, (390, 336), (530, 380))
    w.statusBar().clearMessage()
    s.shoot("markup", w)
    s.close(w)


def shot_edit_text(s: Shooter) -> None:
    w = s.window(s.work / "quarterly-report.pdf", zoom=125)
    pv = w.page_view
    pv.ensureVisible(pv.page_item(0).mapRectToScene(QRectF(56, 90, 480, 300)), 10, 10)
    pump(300)
    w.act_textedit_tool.trigger()
    hover(w, 0, 120, 137)
    click(w, 0, 100, 137)  # "Summary"
    pump(100)
    key(pv, Qt.Key.Key_Return)
    pump(300)
    editor = QApplication.focusWidget()
    if editor is not None:
        QTest.keyClick(editor, Qt.Key.Key_End)
        type_text(" and highlights", editor)
    s.shoot("edit-text", w)
    w.document_view.textedit_editor.cancel()
    s.close(w)


def shot_page_tools(s: Shooter) -> None:
    w = s.window(s.work / "quarterly-report.pdf", zoom=75)
    w.page_view.scroll_to_page(3)
    w.thumbnails.select_pages([1, 2, 3], current=3)
    pump(600)
    menu = w.page_context_menu([1, 2, 3], 3)
    thumbs = w.thumbnails
    rect = thumbs.visualRect(thumbs.model().index(2, 0))
    menu.popup(thumbs.viewport().mapToGlobal(rect.center() + QPoint(20, 0)))
    s.shoot("page-tools", w, menu)
    menu.close()
    menu.deleteLater()
    s.close(w)


def shot_ocr(s: Shooter) -> None:
    from pdfeditor.ui.ocr_dialog import OcrDialog

    w = s.window(s.work / "scanned-letter.pdf", zoom=100)
    w.page_view.verticalScrollBar().setValue(0)
    dialog = OcrDialog(w.document_view.document, w.settings, 0, w)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dialog.show()
    geo = w.geometry()
    dialog.adjustSize()
    dialog.move(geo.center().x() - dialog.width() // 2 + 100, geo.top() + 230)
    s.shoot("ocr", w, dialog, settle=1500)
    dialog.reject()
    s.close(w)


def shot_french(s: Shooter) -> None:
    from pdfeditor.i18n import install_translators, remove_translators

    install_translators(s.app, "fr")
    try:
        w = s.window(s.work / "registration-form.pdf", zoom=100)
        menu = w.menu_edit
        bar = w.menuBar()
        menu.popup(bar.mapToGlobal(bar.actionGeometry(menu.menuAction()).bottomLeft()))
        s.shoot("interface-fr", w, menu)
        menu.close()
        s.close(w)
    finally:
        remove_translators(s.app)


SHOTS: dict[str, Callable[[Shooter], None]] = {
    "main-window": shot_main_window,
    "form-filling": shot_form_filling,
    "flat-form": shot_flat_form,
    "signature": shot_signature,
    "markup": shot_markup,
    "edit-text": shot_edit_text,
    "page-tools": shot_page_tools,
    "ocr": shot_ocr,
    "interface-fr": shot_french,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--only", nargs="*", metavar="NAME", help=f"shots to take: {', '.join(SHOTS)}"
    )
    parser.add_argument(
        "--theme",
        choices=("light", "dark"),
        default="light",
        help="colour scheme of the window (default: light, whatever the system's)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    temp = Path(tempfile.mkdtemp(prefix="pdfeditor-shots-"))
    try:
        # Private settings, log and signatures: never the user's own.
        os.environ["PDFEDITOR_SETTINGS_DIR"] = str(temp / "settings")
        os.environ["PDFEDITOR_LOG_DIR"] = str(temp / "logs")
        (temp / "settings").mkdir()
        work = temp / "docs"
        work.mkdir()

        from pdfeditor.constants import APP_ID, ORG_NAME
        from pdfeditor.core import signature_store
        from pdfeditor.ui import dialogs

        signature_store.default_directory = lambda: temp / "signatures"
        dialogs.confirm_save_changes = lambda *_a, **_k: QMessageBox.StandardButton.Discard
        dialogs.warn = lambda *_a, **_k: None

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv[:1])
        app.setOrganizationName(ORG_NAME)
        app.setApplicationName(APP_ID)
        scheme = Qt.ColorScheme.Light if args.theme == "light" else Qt.ColorScheme.Dark
        app.styleHints().setColorScheme(scheme)

        from pdfeditor.core.settings import Settings

        settings = Settings()
        settings.language = "en"
        settings.sync()

        make_report(work / "quarterly-report.pdf")
        make_acroform(work / "application-form.pdf")
        make_flat_form(work / "registration-form.pdf")
        make_agreement(work / "service-agreement.pdf")
        make_scanned(work / "scanned-letter.pdf", temp)

        store = signature_store.SignatureStore(temp / "signatures")
        args.out.mkdir(parents=True, exist_ok=True)
        shooter = Shooter(app, args.out, work, store)
        unknown = sorted(set(args.only or ()) - set(SHOTS))
        if unknown:
            parser.error(f"unknown shot(s): {', '.join(unknown)}")
        for name in args.only or SHOTS:
            log.info("shot %s", name)
            SHOTS[name](shooter)
        for path in shooter.saved:
            log.info("%s: %d KB", path, math.ceil(path.stat().st_size / 1024))
        app.processEvents()
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
