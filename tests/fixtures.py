"""Test PDF generators (no binary fixtures are committed)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

A4 = (595.0, 842.0)
LETTER_LANDSCAPE = (792.0, 612.0)
A5 = (420.0, 595.0)
SIMPLE_SIZES = (A4, LETTER_LANDSCAPE, A5)
PASSWORD = "secret"


def _label(page: pymupdf.Page, text: str) -> None:
    page.insert_text((72, 72), text, fontsize=24)
    page.draw_rect(pymupdf.Rect(60, 90, 200, 160), color=(1, 0, 0), fill=(0.2, 0.4, 0.9))


def make_simple_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for n, (w, h) in enumerate(SIMPLE_SIZES, start=1):
        _label(doc.new_page(width=w, height=h), f"Page {n}")
    doc.save(path)
    doc.close()
    return path


def make_rotated_pdf(path: Path) -> Path:
    """One Letter portrait page with /Rotate 90 and a text widget near the bottom-right."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    _label(page, "Rotated")
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "name"
    widget.field_value = "Rotated field"
    widget.rect = pymupdf.Rect(400, 700, 600, 780)
    page.add_widget(widget)
    page.set_rotation(90)
    doc.save(path)
    doc.close()
    return path


def make_encrypted_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    _label(doc.new_page(), "Encrypted")
    doc.new_page()
    doc.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=PASSWORD,
        owner_pw="owner-" + PASSWORD,
        permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY,
    )
    doc.close()
    return path


def make_form_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()

    def add(field_type: int, name: str, rect: tuple[float, float, float, float], **kw) -> None:
        w = pymupdf.Widget()
        w.field_type = field_type
        w.field_name = name
        w.rect = pymupdf.Rect(rect)
        for key, value in kw.items():
            setattr(w, key, value)
        page.add_widget(w)

    add(pymupdf.PDF_WIDGET_TYPE_TEXT, "text", (72, 72, 300, 92), field_value="")
    add(pymupdf.PDF_WIDGET_TYPE_CHECKBOX, "check", (72, 110, 88, 126), field_value=False)
    add(pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON, "radio", (72, 140, 88, 156), field_value=False)
    add(
        pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
        "combo",
        (72, 170, 250, 190),
        choice_values=["One", "Two", "Three"],
        field_value="One",
    )
    add(
        pymupdf.PDF_WIDGET_TYPE_LISTBOX,
        "list",
        (72, 210, 250, 270),
        choice_values=["A", "B", "C"],
        field_value="A",
    )
    doc.save(path)
    doc.close()
    return path


def make_many_pages_pdf(path: Path, count: int = 200) -> Path:
    doc = pymupdf.open()
    for n in range(1, count + 1):
        doc.new_page(width=A4[0], height=A4[1]).insert_text((72, 72), f"Page {n}", fontsize=20)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path
