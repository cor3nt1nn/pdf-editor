"""Test PDF generators (no binary fixtures are committed)."""

from __future__ import annotations

import os
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


CROP_MEDIABOX = (612.0, 792.0)
CROP_BOX = (50.0, 40.0, 562.0, 742.0)
#: Red text widget in unrotated PDF user space (y-down, mediabox origin).
CROP_WIDGET_RECT = (100.0, 600.0, 300.0, 650.0)


def make_cropped_form_pdf(path: Path, rotation: int) -> Path:
    """Letter mediabox with an offset cropbox, one opaque red text widget, and /Rotate."""
    doc = pymupdf.open()
    page = doc.new_page(width=CROP_MEDIABOX[0], height=CROP_MEDIABOX[1])
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "red"
    widget.rect = pymupdf.Rect(CROP_WIDGET_RECT)
    widget.fill_color = (1, 0, 0)
    widget.border_width = 0
    page.add_widget(widget)
    page.set_cropbox(pymupdf.Rect(CROP_BOX))
    page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


# -- LibreOffice-like AcroForm (M2) -------------------------------------------------
# Built by hand (get_new_xref/update_object) because PyMuPDF's add_widget cannot make real
# radio groups, multi-page fields or #-escaped on-state names (docs/M2_PLAN.md F16).

LO_CROPBOX = (20.0, 30.0, 575.0, 812.0)
LO_ESCAPED_TOKEN = "Case#20#C3#A0#20cocher#201_3"
LO_MAX_LEN = 30
LO_PERMISSIONS = pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ACCESSIBILITY
_CB_ON = b"q BT 0 g /ZaDb 9 Tf 1.5 2 Td (4) Tj ET Q"
_CB_OFF = b"q 0.5 G 0.5 0.5 11 11 re S Q"

Rect4 = tuple[float, float, float, float]
#: Combo/list values stored by ``make_lo_form_pdf(prefill_choices=True)``.
LO_PREFILLED = {"Civilité": "f", "Couleur": "Vert"}

#: (page, name, kind, y-down unrotated rect) of every editable widget, in tab order.
LO_EDITABLE: tuple[tuple[int, str, str, Rect4], ...] = (
    (0, "Nom", "text", (150, 80, 400, 94)),
    (0, "Zone de texte 8_54", "text", (150, 110, 400, 124)),
    (0, "Zone de texte 8_55", "text", (420, 110, 540, 124)),
    (0, "Zone de texte multi", "text", (150, 140, 540, 200)),
    (0, "Case à cocher 1_2", "checkbox", (150, 280, 162, 292)),
    (0, "Case à cocher 1_3", "checkbox", (250, 280, 262, 292)),
    (0, "Case à cocher 1_4", "checkbox", (350, 280, 362, 292)),
    (0, "Sexe", "radio", (150, 310, 162, 322)),
    (0, "Sexe", "radio", (250, 310, 262, 322)),
    (0, "Civilité", "combo", (150, 340, 300, 354)),
    (0, "Couleur", "list", (150, 370, 300, 430)),
    (1, "Nom", "text", (150, 80, 400, 94)),
    (1, "Case à cocher 2_1", "checkbox", (150, 120, 162, 132)),
)
#: Read-only and hidden widgets (never editable).
LO_NOT_EDITABLE: tuple[tuple[int, str, str, Rect4], ...] = (
    (0, "Zone de texte lecture", "text", (150, 220, 400, 234)),
    (0, "Zone masquée", "text", (150, 250, 400, 264)),
)


def _pdf_rect(rect: Rect4, height: float = A4[1]) -> str:
    """y-down rect -> PDF /Rect array (y-up)."""
    x0, y0, x1, y1 = rect
    return f"[{x0:g} {height - y1:g} {x1:g} {height - y0:g}]"


def make_lo_form_pdf(
    path: Path,
    *,
    encrypted: bool = False,
    rotate: int = 0,
    cropbox: bool = False,
    objstms: bool = True,
    encryption: int = pymupdf.PDF_ENCRYPT_AES_256,
    owner_only: bool = False,
    need_appearances: bool = False,
    prefill_choices: bool = False,
) -> Path:
    """A form shaped like LibreOffice exports (see ``LO_EDITABLE`` / ``LO_NOT_EDITABLE``).

    Indirect /AcroForm with /DA ``/Helv 0 Tf 0 g`` and /DR fonts Helv (no /Encoding) and
    ZaDb, no NeedAppearances, /Fields in scrambled order, /Annots bottom-first. Names use
    PDFDocEncoding accents; checkbox 1_3 has a #-escaped on-state token; 1_4 is checked;
    ``Sexe`` is a real radio group (parent + kids M/F); ``Nom`` has a kid on each page.
    ``rotate``/``cropbox`` apply to page 1 only. ``encrypted`` uses ``encryption``
    (user password ``PASSWORD``); ``owner_only`` sets only an owner password (opens
    without one) with ``LO_PERMISSIONS`` (filling allowed). ``need_appearances`` sets
    /NeedAppearances true; ``prefill_choices`` stores ``LO_PREFILLED`` in the combo and
    list /V (their appearances are not generated).
    """
    rects = {(p, n, r[1]): r for p, n, _k, r in LO_EDITABLE + LO_NOT_EDITABLE}

    def rect_of(page: int, name: str, y0: float) -> Rect4:
        return rects[(page, name, y0)]

    doc = pymupdf.open()
    for n in (1, 2):
        doc.new_page(width=A4[0], height=A4[1]).insert_text(
            (72, 50), f"Formulaire page {n}", fontsize=14
        )
    pages = (doc.page_xref(0), doc.page_xref(1))

    def obj(source: str) -> int:
        x = doc.get_new_xref()
        doc.update_object(x, source)
        return x

    helv = obj("<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    zadb = obj("<</Type/Font/Subtype/Type1/BaseFont/ZapfDingbats>>")

    def form(content: bytes, w: float, h: float, font: tuple[str, int] | None) -> int:
        res = f"/Resources<</Font<</{font[0]} {font[1]} 0 R>>>>" if font else ""
        x = obj(f"<</Type/XObject/Subtype/Form/BBox[0 0 {w:g} {h:g}]{res}>>")
        doc.update_stream(x, content)
        return x

    annots: tuple[list[tuple[float, int]], list[tuple[float, int]]] = ([], [])
    fields: list[int] = []

    def widget(page: int, rect: Rect4, body: str) -> int:
        x = obj(f"<</Type/Annot/Subtype/Widget/P {pages[page]} 0 R/Rect{_pdf_rect(rect)}{body}>>")
        annots[page].append((rect[1], x))
        return x

    def check_ap(token: str) -> str:
        on, off = form(_CB_ON, 12, 12, ("ZaDb", zadb)), form(_CB_OFF, 12, 12, None)
        return f"/AP<</N<</{token} {on} 0 R/Off {off} 0 R>>>>"

    def checkbox(page: int, suffix: str, token: str, y0: float, checked: bool = False) -> None:
        state = f"/{token}" if checked else "/Off"
        rect = rect_of(page, f"Case à cocher {suffix}", y0)
        body = f"/FT/Btn/T(Case \\340 cocher {suffix})/F 4/DA(/ZaDb 0 Tf 0 g)"
        fields.append(widget(page, rect, f"{body}/AS{state}/V{state}{check_ap(token)}"))

    text = "/FT/Tx/F 4/DA(/Helv 8 Tf 0 g)"
    fields.append(
        widget(
            0,
            rect_of(0, "Zone de texte 8_54", 110),
            f"{text}/T(Zone de texte 8_54)/MaxLen {LO_MAX_LEN}",
        )
    )
    ap_content = b"/Tx BMC q BT 0 g /Helv 8 Tf 2 3 Td (d\xe9j\xe0) Tj ET Q EMC"
    ap = form(ap_content, 120, 14, ("Helv", helv))
    fields.append(
        widget(
            0,
            rect_of(0, "Zone de texte 8_55", 110),
            f"{text}/T(Zone de texte 8_55)/V<FEFF006400E9006A00E0>/AP<</N {ap} 0 R>>",
        )
    )
    fields.append(
        widget(
            0,
            rect_of(0, "Zone de texte multi", 140),
            "/FT/Tx/F 4/Ff 4096/DA(/Helv 10 Tf 0 g)/T(Zone de texte multi)",
        )
    )
    fields.append(
        widget(
            0,
            rect_of(0, "Zone de texte lecture", 220),
            f"{text}/Ff 1/T(Zone de texte lecture)/V(fixe)",
        )
    )
    fields.append(
        widget(
            0,
            rect_of(0, "Zone masquée", 250),
            "/FT/Tx/F 6/DA(/Helv 8 Tf 0 g)/T(Zone masqu\\351e)",
        )
    )
    checkbox(0, "1_2", "Oui", 280)
    checkbox(0, "1_3", LO_ESCAPED_TOKEN, 280)
    checkbox(0, "1_4", "Oui", 280, checked=True)
    checkbox(1, "2_1", "Oui", 120)

    # Radio group: parent field + two kid widgets without /T.
    sexe = doc.get_new_xref()
    kids = [
        widget(0, rect, f"/Parent {sexe} 0 R/F 4/DA(/ZaDb 0 Tf 0 g)/AS/Off{check_ap(token)}")
        for token, rect in (("M", (150, 310, 162, 322)), ("F", (250, 310, 262, 322)))
    ]
    doc.update_object(sexe, f"<</FT/Btn/Ff 32768/T(Sexe)/V/Off/Kids[{kids[0]} 0 R {kids[1]} 0 R]>>")
    fields.append(sexe)

    fields.append(
        widget(
            0,
            rect_of(0, "Civilité", 340),
            "/FT/Ch/Ff 131072/F 4/DA(/Helv 8 Tf 0 g)/T(Civilit\\351)"
            "/Opt[[(m)(Monsieur)][(f)(Madame)](Autre)]"
            + (f"/V({LO_PREFILLED['Civilité']})" if prefill_choices else ""),
        )
    )
    fields.append(
        widget(
            0,
            rect_of(0, "Couleur", 370),
            "/FT/Ch/F 4/DA(/Helv 8 Tf 0 g)/T(Couleur)/Opt[(Rouge)(Vert)(Bleu)]"
            + (f"/V({LO_PREFILLED['Couleur']})" if prefill_choices else ""),
        )
    )

    # Text field with one kid widget per page (inherits /DA from the parent).
    nom = doc.get_new_xref()
    nom_kids = [widget(i, rect_of(i, "Nom", 80), f"/Parent {nom} 0 R/F 4") for i in (0, 1)]
    doc.update_object(
        nom, f"<</FT/Tx/T(Nom)/DA(/Helv 8 Tf 0 g)/Kids[{nom_kids[0]} 0 R {nom_kids[1]} 0 R]>>"
    )
    fields.append(nom)

    for i, items in enumerate(annots):  # bottom-first, like many producers
        refs = " ".join(f"{x} 0 R" for _y, x in sorted(items, reverse=True))
        doc.xref_set_key(pages[i], "Annots", f"[{refs}]")
    scrambled = fields[1::2] + fields[0::2]
    acro = obj(
        "<</Fields[" + " ".join(f"{x} 0 R" for x in reversed(scrambled)) + "]"
        f"/DA(/Helv 0 Tf 0 g)/DR<</Font<</Helv {helv} 0 R/ZaDb {zadb} 0 R>>>>"
        + ("/NeedAppearances true" if need_appearances else "")
        + ">>"
    )
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"{acro} 0 R")

    if cropbox:
        doc[0].set_cropbox(pymupdf.Rect(LO_CROPBOX))
    if rotate:
        doc[0].set_rotation(rotate)
    kwargs: dict[str, object] = {"deflate": True}
    if objstms:
        kwargs["use_objstms"] = 1
    if encrypted:
        kwargs.update(
            encryption=encryption,
            user_pw=PASSWORD,
            owner_pw="owner-" + PASSWORD,
            permissions=LO_PERMISSIONS,
        )
    elif owner_only:
        kwargs.update(
            encryption=encryption, user_pw="", owner_pw=OWNER_PASSWORD, permissions=LO_PERMISSIONS
        )
    doc.save(path, **kwargs)
    doc.close()
    return path


OWNER_PASSWORD = "o"


def make_owner_locked_pdf(path: Path) -> Path:
    """Owner password only (opens without a password), printing allowed, form filling not."""
    doc = pymupdf.open()
    page = doc.new_page()
    _label(page, "Owner locked")
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "locked"
    widget.rect = pymupdf.Rect(72, 200, 300, 220)
    page.add_widget(widget)
    doc.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw=OWNER_PASSWORD,
        user_pw="",
        permissions=pymupdf.PDF_PERM_PRINT,
    )
    doc.close()
    return path


XFA_FIELD_NAME = "topmostSubform[0].Page1[0].Nom[0]"


def _xfa_config(render: str) -> bytes:
    return (
        '<config xmlns="http://www.xfa.org/schema/xci/3.1/"><present><pdf>'
        "<version>1.7</version></pdf></present><acrobat><acrobat7>"
        f"<dynamicRender>{render}</dynamicRender></acrobat7></acrobat></config>"
    ).encode()


_XFA_TEMPLATE = (
    b'<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/">'
    b'<subform name="topmostSubform"><pageSet/></subform></template>'
)


def _stream_obj(doc: pymupdf.Document, data: bytes) -> int:
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, data)
    return x


def make_static_xfa_pdf(path: Path) -> Path:
    """Static XFA (dynamicRender forbidden) over one AcroForm text field ``XFA_FIELD_NAME``.

    Indirect /AcroForm with /XFA [(config) c 0 R (template) t 0 R (datasets) d 0 R].
    """
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((72, 72), "Static XFA form", fontsize=14)
    helv = doc.get_new_xref()
    doc.update_object(helv, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    field = doc.get_new_xref()
    rect = _pdf_rect((72, 100, 300, 114))
    doc.update_object(
        field,
        f"<</Type/Annot/Subtype/Widget/P {doc.page_xref(0)} 0 R/Rect{rect}"
        f"/FT/Tx/F 4/DA(/Helv 8 Tf 0 g)/T({XFA_FIELD_NAME})>>",
    )
    doc.xref_set_key(doc.page_xref(0), "Annots", f"[{field} 0 R]")
    config = _stream_obj(doc, _xfa_config("forbidden"))
    template = _stream_obj(doc, _XFA_TEMPLATE)
    datasets = _stream_obj(
        doc,
        b'<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><xfa:data>'
        b"<topmostSubform><Nom>ancien</Nom></topmostSubform></xfa:data></xfa:datasets>",
    )
    acro = doc.get_new_xref()
    doc.update_object(
        acro,
        f"<</Fields[{field} 0 R]/DA(/Helv 0 Tf 0 g)/DR<</Font<</Helv {helv} 0 R>>>>"
        f"/XFA[(config) {config} 0 R (template) {template} 0 R (datasets) {datasets} 0 R]>>",
    )
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"{acro} 0 R")
    doc.save(path, deflate=True)
    doc.close()
    return path


def make_dynamic_xfa_pdf(path: Path, single_stream: bool = False) -> Path:
    """Dynamic XFA: a "Please wait..." page, no AcroForm fields, /Extensions /ADBE.

    /XFA is a packet array (config dynamicRender required) or, with ``single_stream``,
    one XDP stream.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text(
        (72, 72),
        "Please wait... If this message is not eventually replaced by the proper contents"
        " of the document, your PDF viewer may not be able to display this type of document.",
        fontsize=8,
    )
    if single_stream:
        xdp = _stream_obj(
            doc,
            b'<?xml version="1.0"?><xdp:xdp xmlns:xdp="http://ns.adobe.com/xdp/">'
            + _xfa_config("required")
            + _XFA_TEMPLATE
            + b"</xdp:xdp>",
        )
        xfa = f"{xdp} 0 R"
    else:
        config = _stream_obj(doc, _xfa_config("required"))
        template = _stream_obj(doc, _XFA_TEMPLATE)
        xfa = f"[(config) {config} 0 R (template) {template} 0 R]"
    acro = doc.get_new_xref()
    doc.update_object(acro, f"<</Fields[]/XFA {xfa}>>")
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "AcroForm", f"{acro} 0 R")
    doc.xref_set_key(catalog, "Extensions", "<</ADBE<</BaseVersion/1.7/ExtensionLevel 8>>>>")
    doc.save(path, deflate=True)
    doc.close()
    return path


MANY_FIELDS_PER_PAGE = 50


def make_many_fields_pdf(path: Path, count: int = 200) -> Path:
    """``count`` single-line text widgets ("f000"...), 50 per A4 page in 2 columns."""
    doc = pymupdf.open()
    page = None
    for n in range(count):
        slot = n % MANY_FIELDS_PER_PAGE
        if slot == 0:
            page = doc.new_page(width=A4[0], height=A4[1])
        assert page is not None
        col, row = divmod(slot, MANY_FIELDS_PER_PAGE // 2)
        x0, y0 = 40 + col * 280, 40 + row * 30
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = f"f{n:03d}"
        w.field_value = f"value {n}"
        w.text_fontsize = 10
        w.rect = pymupdf.Rect(x0, y0, x0 + 250, y0 + 20)
        page.add_widget(w)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


#: Widget names of :func:`make_odd_widgets_pdf` and their rects (fitz coordinates).
ODD_RECTS = {
    "ok": (72, 72, 250, 92),
    "signature": (72, 110, 250, 150),
    "push": (72, 170, 250, 190),
    "no_rect": (72, 210, 250, 230),
    "degenerate": (72, 250, 250, 270),
    "combo_no_opt": (72, 290, 250, 310),
    "edit_combo_no_opt": (72, 330, 250, 350),
}


def make_odd_widgets_pdf(path: Path) -> Path:
    """One page with a normal text field and odd widgets: a signature field, a push
    button, a widget without /Rect, one with a zero-size /Rect, and two combo boxes
    (one editable) without /Opt. Built as text widgets, then rewritten by xref."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    for name, rect in ODD_RECTS.items():
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.rect = pymupdf.Rect(rect)
        page.add_widget(w)
    xrefs = {w.field_name: w.xref for w in page.widgets()}
    doc.xref_set_key(xrefs["signature"], "FT", "/Sig")
    doc.xref_set_key(xrefs["push"], "FT", "/Btn")
    doc.xref_set_key(xrefs["push"], "Ff", str(1 << 16))  # push button
    doc.xref_set_key(xrefs["no_rect"], "Rect", "null")
    doc.xref_set_key(xrefs["degenerate"], "Rect", "[72 250 72 250]")
    doc.xref_set_key(xrefs["combo_no_opt"], "FT", "/Ch")
    doc.xref_set_key(xrefs["combo_no_opt"], "Ff", str(1 << 17))
    doc.xref_set_key(xrefs["edit_combo_no_opt"], "FT", "/Ch")
    doc.xref_set_key(xrefs["edit_combo_no_opt"], "Ff", str((1 << 17) | (1 << 18)))
    doc.save(path)
    doc.close()
    return path


#: (page, on-state, y-down rect) of the kids of :func:`make_multipage_radio_pdf`'s radio.
MP_RADIO_KIDS: tuple[tuple[int, str, Rect4], ...] = (
    (0, "A", (100, 100, 114, 114)),
    (0, "B", (200, 100, 214, 114)),
    (1, "C", (100, 100, 114, 114)),
)
MP_RADIO_NAME = "Choix"
_FF_RADIO = 1 << 15
_FF_NO_TOGGLE_TO_OFF = 1 << 14


def make_multipage_radio_pdf(path: Path, *, no_toggle_off: bool = False) -> Path:
    """Two A4 pages and one radio group ``Choix`` whose kids (``MP_RADIO_KIDS``) span
    both pages; ``C`` (page 2) is selected. ``no_toggle_off`` sets NoToggleToOff."""
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page(width=A4[0], height=A4[1])
    pages = (doc.page_xref(0), doc.page_xref(1))

    def obj(source: str) -> int:
        x = doc.get_new_xref()
        doc.update_object(x, source)
        return x

    zadb = obj("<</Type/Font/Subtype/Type1/BaseFont/ZapfDingbats>>")

    def form(content: bytes, font: bool) -> int:
        res = f"/Resources<</Font<</ZaDb {zadb} 0 R>>>>" if font else ""
        x = obj(f"<</Type/XObject/Subtype/Form/BBox[0 0 14 14]{res}>>")
        doc.update_stream(x, content)
        return x

    field = doc.get_new_xref()
    annots: tuple[list[int], list[int]] = ([], [])
    kids = []
    for page, state, rect in MP_RADIO_KIDS:
        on, off = form(_CB_ON, True), form(_CB_OFF, False)
        current = state if state == "C" else "Off"
        x = obj(
            f"<</Type/Annot/Subtype/Widget/P {pages[page]} 0 R/Rect{_pdf_rect(rect)}"
            f"/Parent {field} 0 R/F 4/DA(/ZaDb 0 Tf 0 g)/AS/{current}"
            f"/AP<</N<</{state} {on} 0 R/Off {off} 0 R>>>>>>"
        )
        annots[page].append(x)
        kids.append(x)
    flags = _FF_RADIO | (_FF_NO_TOGGLE_TO_OFF if no_toggle_off else 0)
    refs = " ".join(f"{x} 0 R" for x in kids)
    doc.update_object(field, f"<</FT/Btn/Ff {flags}/T({MP_RADIO_NAME})/V/C/Kids[{refs}]>>")
    for i, items in enumerate(annots):
        doc.xref_set_key(pages[i], "Annots", "[" + " ".join(f"{x} 0 R" for x in items) + "]")
    acro = obj(f"<</Fields[{field} 0 R]/DA(/Helv 0 Tf 0 g)/DR<</Font<</ZaDb {zadb} 0 R>>>>>>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"{acro} 0 R")
    doc.save(path, deflate=True)
    doc.close()
    return path


SQUARE_SIZE = 400.0
SQUARE_FIELD_RECT = (50.0, 60.0, 250.0, 80.0)


def make_square_form_pdf(path: Path) -> Path:
    """One square page (``SQUARE_SIZE``) with one off-centre text field ``carre``."""
    doc = pymupdf.open()
    page = doc.new_page(width=SQUARE_SIZE, height=SQUARE_SIZE)
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "carre"
    w.text_fontsize = 10
    w.rect = pymupdf.Rect(SQUARE_FIELD_RECT)
    page.add_widget(w)
    doc.save(path)
    doc.close()
    return path


# -- FreeText annotations (M3) ----------------------------------------------------
#: /NM, text and y-down rect of our own FreeText on page 1 of :func:`make_annotated_pdf`.
ANNOT_TEXT_NAME = "0f7c1c2e-6a57-4d0e-9b1a-3c2f5e8d9a01"
ANNOT_TEXT = "Élève : é à ç €"
ANNOT_TEXT_RECT: Rect4 = (100, 100, 300, 116)
#: /NM and y-down rect of the ✓ stamp (ZapfDingbats "4", 10.8 pt) on page 1.
ANNOT_STAMP_NAME = "6d1f0b53-2c9e-4f7a-8e44-0b7d9f1a2c33"
ANNOT_STAMP_RECT: Rect4 = (100, 150, 112, 162)
#: Adobe-style FreeText (no /NM, no /AP, /DA /Arial, /DS, /RC) on page 1.
FOREIGN_TEXT = "Adobe style é"
FOREIGN_RECT: Rect4 = (100, 200, 300, 230)
FOREIGN_COLOR = (0.0, 0.0, 1.0)
FOREIGN_SIZE = 12.0
#: Hidden FreeText (/F 6) on page 1: never listed.
HIDDEN_ANNOT_NAME = "hidden-freetext"
HIDDEN_RECT: Rect4 = (100, 250, 300, 270)
HIGHLIGHT_RECT: Rect4 = (100, 300, 300, 320)
ANNOT_WIDGET_NAME = "champ"
#: /NM, text and page-space rect of the FreeText on page 2 (/Rotate 90, created rotate=90).
ROTATED_ANNOT_NAME = "9a3e5b7c-1d2f-4e6a-8b0c-2d4f6a8c0e12"
ROTATED_ANNOT_TEXT = "Rotated text"
ROTATED_ANNOT_RECT: Rect4 = (100, 100, 300, 116)


def make_annotated_pdf(path: Path) -> Path:
    """Two A4 pages. Page 1: our FreeText (accents, uuid /NM), a ✓ stamp, a foreign
    Adobe-style FreeText, a hidden FreeText, a Highlight and a text widget. Page 2
    (/Rotate 90): a FreeText created upright (rotate=90) at ``ROTATED_ANNOT_RECT``."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((72, 60), "Annotated", fontsize=14)

    def ours(p: pymupdf.Page, rect: Rect4, text: str, name: str, **kw) -> int:
        a = p.add_freetext_annot(pymupdf.Rect(rect), text, border_width=0, **kw)
        doc.xref_set_key(a.xref, "NM", pymupdf.get_pdf_str(name))
        doc.xref_set_key(a.xref, "CL", "null")
        return a.xref

    ours(page, ANNOT_TEXT_RECT, ANNOT_TEXT, ANNOT_TEXT_NAME, fontsize=11, fontname="helv")
    ours(page, ANNOT_STAMP_RECT, "4", ANNOT_STAMP_NAME, fontsize=10.8, fontname="zadb")
    hidden = ours(page, HIDDEN_RECT, "hidden", HIDDEN_ANNOT_NAME, fontsize=11, fontname="helv")
    doc.xref_set_key(hidden, "F", "6")
    page.add_highlight_annot(pymupdf.Rect(HIGHLIGHT_RECT))
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = ANNOT_WIDGET_NAME
    w.rect = pymupdf.Rect(100, 350, 300, 370)
    page.add_widget(w)

    page_ref = doc.page_xref(0)
    foreign = doc.get_new_xref()
    r, g, b = FOREIGN_COLOR
    doc.update_object(
        foreign,
        f"<</Type/Annot/Subtype/FreeText/Rect{_pdf_rect(FOREIGN_RECT)}/P {page_ref} 0 R/F 4"
        f"/Contents{pymupdf.get_pdf_str(FOREIGN_TEXT)}"
        f"/DA({r:g} {g:g} {b:g} rg /Arial {FOREIGN_SIZE:g} Tf)"
        f"/DS(font: Arial {FOREIGN_SIZE:g}pt; color:#0000FF)"
        f"/RC(<body><p>Adobe style</p></body>)>>",
    )
    annots = doc.xref_get_key(page_ref, "Annots")[1].strip()
    doc.xref_set_key(page_ref, "Annots", annots[:-1] + f" {foreign} 0 R]")

    page2 = doc.new_page(width=A4[0], height=A4[1])
    page2.set_rotation(90)
    unrotated = (pymupdf.Rect(ROTATED_ANNOT_RECT) * page2.derotation_matrix).normalize()
    ours(
        page2,
        tuple(unrotated),
        ROTATED_ANNOT_TEXT,
        ROTATED_ANNOT_NAME,
        fontsize=11,
        fontname="helv",
        rotate=90,
    )
    doc.save(path)
    doc.close()
    return path


#: Number of FreeText annotations on the page of :func:`make_many_annots_pdf`.
MANY_ANNOTS = 200


def many_annots_name(n: int) -> str:
    """/NM of annotation ``n`` of :func:`make_many_annots_pdf`."""
    return f"many-{n:03d}"


def many_annots_rect(n: int) -> Rect4:
    """Y-down rect of annotation ``n`` of :func:`make_many_annots_pdf` (4 columns)."""
    x0 = 40 + (n % 4) * 135
    y0 = 40 + (n // 4) * 15
    return (x0, y0, x0 + 125, y0 + 12)


def make_many_annots_pdf(path: Path, n: int = MANY_ANNOTS) -> Path:
    """One A4 page with ``n`` small FreeText annotations (Helvetica 8 pt), named by
    :func:`many_annots_name`, laid out by :func:`many_annots_rect`."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    for i in range(n):
        a = page.add_freetext_annot(
            pymupdf.Rect(many_annots_rect(i)), f"note {i}", fontsize=8, border_width=0
        )
        doc.xref_set_key(a.xref, "NM", pymupdf.get_pdf_str(many_annots_name(i)))
        doc.xref_set_key(a.xref, "CL", "null")
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


#: FreeText /NM -> y-down rect written by :func:`make_odd_annots_pdf` before the odd
#: /Rect values replace it ("ok" keeps it).
ODD_ANNOT_RECTS: dict[str, Rect4] = {
    "ok": (72, 72, 250, 92),
    "no_rect": (72, 110, 250, 130),
    "huge": (72, 150, 250, 170),
    "zero": (72, 190, 250, 210),
    "far_negative": (72, 230, 250, 250),
}


def make_odd_annots_pdf(path: Path) -> Path:
    """One A4 page with a normal FreeText ("ok") and odd ones: no /Rect, coordinates
    beyond 1e6 pt, a zero-size /Rect and coordinates far below -1e6 pt."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    xrefs: dict[str, int] = {}
    for name, rect in ODD_ANNOT_RECTS.items():
        a = page.add_freetext_annot(pymupdf.Rect(rect), name, fontsize=11, border_width=0)
        doc.xref_set_key(a.xref, "NM", pymupdf.get_pdf_str(name))
        doc.xref_set_key(a.xref, "CL", "null")
        xrefs[name] = a.xref
    del page
    doc.xref_set_key(xrefs["no_rect"], "Rect", "null")
    doc.xref_set_key(xrefs["huge"], "Rect", "[1000000 1000000 2000000 2000010]")
    doc.xref_set_key(xrefs["zero"], "Rect", "[72 632 72 632]")
    doc.xref_set_key(xrefs["far_negative"], "Rect", "[-3000000 -3000000 -2000000 -1000000]")
    doc.save(path)
    doc.close()
    return path


# -- snapping (M3-T3)---------------------------------------------------------------
_FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
MSGOTHIC_PATH = _FONT_DIR / "msgothic.ttc"
SEGUISYM_PATH = _FONT_DIR / "seguisym.ttf"
ARIAL_PATH = _FONT_DIR / "arial.ttf"
#: The ☐ glyph checkboxes of :func:`make_word_form_pdf` need both Windows symbol fonts.
SYMBOL_FONTS_AVAILABLE = MSGOTHIC_PATH.exists() and SEGUISYM_PATH.exists()

#: Every drawn element of :func:`make_word_form_pdf`, as (x0, y0, x1, y1) in unrotated,
#: uncropped (mediabox, y-down) coordinates.
WORD_SHAPES: dict[str, Rect4] = {
    #: Underline drawn as a thin filled rect (how Word exports a paragraph border).
    "underline_rect": (110.0, 74.0, 300.0, 74.5),
    #: Underline drawn as a 0.5 pt stroked line (zero height).
    "underline_line": (110.0, 100.0, 300.0, 100.0),
    #: 3 x 2 table; borders are 0.5 pt filled rects centred on WORD_TABLE_X/Y.
    "table": (72.0, 150.0, 372.0, 210.0),
    #: Stroked 10 pt and 12 pt checkboxes (0.75 pt).
    "checkbox_10": (72.0, 250.0, 82.0, 260.0),
    "checkbox_12": (120.0, 248.0, 132.0, 260.0),
    #: 12 pt box drawn as four separate lines in one path.
    "box_lines": (200.0, 250.0, 212.0, 262.0),
    #: Large stroked area (1 pt).
    "area": (72.0, 300.0, 500.0, 400.0),
    #: Grey filled band (no stroke).
    "band": (72.0, 420.0, 300.0, 440.0),
}
WORD_TABLE_X = (72.0, 172.0, 272.0, 372.0)
WORD_TABLE_Y = (150.0, 180.0, 210.0)
#: Glyph checkboxes ☐ (U+2610, 12 pt): font name -> text origin (baseline start).
WORD_GLYPHS: dict[str, tuple[float, float]] = {"msgothic": (72.0, 500.0), "seguisym": (72.0, 530.0)}
WORD_GLYPH_SIZE = 12.0
#: Cropbox used with ``make_word_form_pdf(..., cropbox=True)`` (mediabox is A4).
WORD_CROPBOX: Rect4 = (20.0, 30.0, 575.0, 812.0)


def make_word_form_pdf(path: Path, *, rotate: int = 0, cropbox: bool = False) -> Path:
    """One A4 page looking like a Word export of a flat form (see ``WORD_SHAPES``):
    Helvetica labels, underlines, a table, stroked and line-drawn checkboxes, a large
    area, a grey band and (when ``SYMBOL_FONTS_AVAILABLE``) ☐ glyphs from MS Gothic and
    Segoe UI Symbol. ``rotate`` sets /Rotate, ``cropbox`` sets ``WORD_CROPBOX``."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    for pos, label in (
        ((72, 72), "Nom :"),
        ((72, 98), "Ville :"),
        ((72, 140), "Tableau"),
        ((86, 259), "Oui"),
        ((136, 259), "Non"),
        ((216, 261), "Autre"),
        ((72, 295), "Observations"),
    ):
        page.insert_text(pos, label, fontsize=11, fontname="helv")
    sh = page.new_shape()

    def thin(rect: Rect4) -> None:
        sh.draw_rect(pymupdf.Rect(rect))
        sh.finish(fill=(0, 0, 0), color=None, width=0)

    def stroked(rect: Rect4, width: float) -> None:
        sh.draw_rect(pymupdf.Rect(rect))
        sh.finish(color=(0, 0, 0), width=width)

    thin(WORD_SHAPES["underline_rect"])
    x0, y, x1, _ = WORD_SHAPES["underline_line"]
    sh.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y))
    sh.finish(color=(0, 0, 0), width=0.5)
    tx0, ty0, tx1, ty1 = WORD_SHAPES["table"]
    for x in WORD_TABLE_X:
        thin((x - 0.25, ty0 - 0.25, x + 0.25, ty1 + 0.25))
    for y in WORD_TABLE_Y:
        thin((tx0 - 0.25, y - 0.25, tx1 + 0.25, y + 0.25))
    stroked(WORD_SHAPES["checkbox_10"], 0.75)
    stroked(WORD_SHAPES["checkbox_12"], 0.75)
    bx0, by0, bx1, by1 = WORD_SHAPES["box_lines"]
    corners = ((bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1), (bx0, by0))
    for a, b in zip(corners, corners[1:], strict=False):
        sh.draw_line(pymupdf.Point(a), pymupdf.Point(b))
    sh.finish(color=(0, 0, 0), width=0.75)
    stroked(WORD_SHAPES["area"], 1.0)
    sh.draw_rect(pymupdf.Rect(WORD_SHAPES["band"]))
    sh.finish(fill=(0.85, 0.85, 0.85), color=None, width=0)
    sh.commit()
    if SYMBOL_FONTS_AVAILABLE:
        for name, font in (("msgothic", MSGOTHIC_PATH), ("seguisym", SEGUISYM_PATH)):
            page.insert_text(
                WORD_GLYPHS[name],
                "\u2610",
                fontsize=WORD_GLYPH_SIZE,
                fontname=name,
                fontfile=str(font),
            )
    if cropbox:
        page.set_cropbox(pymupdf.Rect(WORD_CROPBOX))
    if rotate:
        page.set_rotation(rotate)
    doc.save(path)
    doc.close()
    return path


#: The only vector element of :func:`make_print_pdf`: a 0.75 pt stroked line.
PRINT_LINE: Rect4 = (72.0, 130.0, 400.0, 130.0)


def make_print_pdf(path: Path) -> Path:
    """One A4 page looking like a "Microsoft Print to PDF" output: text in an embedded
    TrueType font (Arial, Helvetica if missing) and one stroked line, no boxes."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    font = {"fontname": "arial", "fontfile": str(ARIAL_PATH)} if ARIAL_PATH.exists() else {}
    for y, text in ((72, "Formulaire imprimé"), (110, "Nom : Jean Dupont"), (160, "Oui  Non")):
        page.insert_text((72, y), text, fontsize=11, **font)
    x0, y, x1, _ = PRINT_LINE
    page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y), color=(0, 0, 0), width=0.75)
    doc.save(path)
    doc.close()
    return path


def make_busy_drawings_pdf(path: Path, n: int = 3000) -> Path:
    """One A4 page with ``n`` stroked 8 pt squares (50 per row), for scan performance."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    sh = page.new_shape()
    for i in range(n):
        x, y = 20 + (i % 50) * 11, 20 + (i // 50) * 13
        sh.draw_rect(pymupdf.Rect(x, y, x + 8, y + 8))
        sh.finish(color=(0, 0, 0), width=0.5)
    sh.commit()
    doc.save(path)
    doc.close()
    return path


# -- M4: signatures --------------------------------------------------------------------
#: Pixel size of the asymmetric signature image of :func:`make_signed_pdf`.
SIG_IMAGE_SIZE = (400, 160)
#: Its red top-left square side and blue bottom band height (pixels); the rest is clear.
SIG_RED_SIDE = 60
SIG_BLUE_BAND = 30
SIGNED_NAME = "5b0e8f3a-6c1d-4f2e-9a7b-3d4c5e6f7a81"
SIGNED_RECT: Rect4 = (100, 100, 300, 180)
#: MuPDF-made image stamp (pdf_set_annot_stamp_image, /IT /StampImage) without /NM.
MUPDF_STAMP_RECT: Rect4 = (100, 220, 300, 300)
#: MuPDF "Approved" text stamp (not a signature).
TEXT_STAMP_NAME = "text-stamp"
TEXT_STAMP_RECT: Rect4 = (100, 320, 300, 360)
#: Adobe-like image stamp without /IT (not a signature).
FOREIGN_STAMP_NAME = "foreign-image-stamp"
FOREIGN_STAMP_RECT: Rect4 = (320, 100, 520, 180)
#: Our signature flagged Locked (/F 132).
LOCKED_SIGNATURE_NAME = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
LOCKED_SIGNATURE_RECT: Rect4 = (320, 220, 520, 300)
SIGNED_TEXT_NAME = "7e8f9a0b-1c2d-4e3f-8a5b-6c7d8e9f0a1b"
SIGNED_TEXT_RECT: Rect4 = (100, 400, 300, 420)
#: Page 2 (/Rotate 90): our signature with pre-rotated pixels, page-space rect.
ROTATED_SIGNED_NAME = "2f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c"
ROTATED_SIGNED_RECT: Rect4 = (100, 100, 300, 180)
#: (fraction of the rect width, of its height) -> expected colour of a rendering of the
#: asymmetric image upright in a rect: "red", "blue" or "white" (transparent).
SIG_ASYM_PROBES: dict[tuple[float, float], str] = {
    (0.05, 0.1): "red",
    (0.05, 0.95): "blue",
    (0.95, 0.95): "blue",
    (0.95, 0.1): "white",
    (0.5, 0.5): "white",
}


def sig_asym_samples(rotation: int = 0) -> tuple[int, int, bytes, bytes]:
    """(width, height, rgb, alpha) of the asymmetric signature image turned by
    ``-rotation`` degrees (pixels pre-rotated for a page with that /Rotate)."""
    w, h = SIG_IMAGE_SIZE

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        if x < SIG_RED_SIDE and y < SIG_RED_SIDE:
            return (255, 0, 0, 255)
        if y >= h - SIG_BLUE_BAND:
            return (0, 0, 255, 255)
        return (0, 0, 0, 0)

    rotation %= 360
    # (x', y') of the turned image -> (x, y) of the upright one.
    if rotation == 90:
        out_w, out_h, src = h, w, lambda x, y: (w - 1 - y, x)
    elif rotation == 180:
        out_w, out_h, src = w, h, lambda x, y: (w - 1 - x, h - 1 - y)
    elif rotation == 270:
        out_w, out_h, src = h, w, lambda x, y: (y, h - 1 - x)
    else:
        out_w, out_h, src = w, h, lambda x, y: (x, y)
    rgb, alpha = bytearray(), bytearray()
    for y in range(out_h):
        for x in range(out_w):
            r, g, b, a = pixel(*src(x, y))
            rgb += bytes((r, g, b))
            alpha.append(a)
    return out_w, out_h, bytes(rgb), bytes(alpha)


def _sig_image(doc: pymupdf.Document, w: int, h: int, rgb: bytes, alpha: bytes) -> int:
    smask = doc.get_new_xref()
    doc.update_object(
        smask,
        f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}"
        "/ColorSpace/DeviceGray/BitsPerComponent 8>>",
    )
    doc.update_stream(smask, alpha, compress=True)
    xref = doc.get_new_xref()
    doc.update_object(
        xref,
        f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}"
        f"/ColorSpace/DeviceRGB/BitsPerComponent 8/SMask {smask} 0 R>>",
    )
    doc.update_stream(xref, rgb, compress=True)
    return xref


def _image_stamp(page: pymupdf.Page, rect: Rect4, image: object) -> int:
    """A Stamp drawing ``image`` (a mupdf image object or FzImage) at the page-space
    ``rect``; MuPDF's own appearance. Returns its xref."""
    mupdf = pymupdf.mupdf
    unrotated = (pymupdf.Rect(rect) * page.derotation_matrix).normalize()
    a = page.add_stamp_annot(unrotated)
    if isinstance(image, mupdf.FzImage):
        mupdf.pdf_set_annot_stamp_image(a.this, image)
    else:
        mupdf.pdf_set_annot_stamp_image_obj(a.this, image)
    a.set_rect(unrotated)
    mupdf.pdf_update_annot(a.this)
    return a.xref


def _our_signature(doc: pymupdf.Document, page: pymupdf.Page, rect: Rect4, image: int, name: str):
    mupdf = pymupdf.mupdf
    obj = mupdf.pdf_new_indirect(pymupdf._as_pdf_document(doc), image, 0)
    xref = _image_stamp(page, rect, obj)
    doc.xref_set_key(xref, "NM", pymupdf.get_pdf_str(name))
    doc.xref_set_key(xref, "IT", "/StampImage")
    for key in ("Name", "Contents", "C"):
        doc.xref_set_key(xref, key, "null")
    return xref


def make_signed_pdf(path: Path) -> Path:
    """Two A4 pages. Page 1: our signature (``SIGNED_NAME`` at ``SIGNED_RECT``, the
    asymmetric 400x160 image), a MuPDF-made image stamp with /IT /StampImage and no /NM
    (``MUPDF_STAMP_RECT``), a MuPDF "Approved" text stamp, an Adobe-like image stamp
    without /IT (``FOREIGN_STAMP_NAME``), a Locked signature (/F 132,
    ``LOCKED_SIGNATURE_NAME``, sharing the first image) and a FreeText. Page 2
    (/Rotate 90): our signature with pre-rotated pixels at ``ROTATED_SIGNED_RECT``."""
    mupdf = pymupdf.mupdf
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((72, 60), "Signed", fontsize=14)
    w, h, rgb, alpha = sig_asym_samples()
    image = _sig_image(doc, w, h, rgb, alpha)
    _our_signature(doc, page, SIGNED_RECT, image, SIGNED_NAME)

    rgba = bytearray()
    for i in range(w * h):
        rgba += rgb[3 * i : 3 * i + 3] + alpha[i : i + 1]
    pix = pymupdf.Pixmap(pymupdf.csRGB, w, h, bytes(rgba), 1)
    fz_image = mupdf.fz_new_image_from_pixmap(pix.this, mupdf.FzImage())
    lazy = _image_stamp(page, MUPDF_STAMP_RECT, fz_image)
    doc.xref_set_key(lazy, "IT", "/StampImage")
    doc.xref_set_key(lazy, "NM", "null")

    text_stamp = page.add_stamp_annot(pymupdf.Rect(TEXT_STAMP_RECT))
    doc.xref_set_key(text_stamp.xref, "NM", pymupdf.get_pdf_str(TEXT_STAMP_NAME))

    foreign = _image_stamp(
        page, FOREIGN_STAMP_RECT, mupdf.pdf_new_indirect(pymupdf._as_pdf_document(doc), image, 0)
    )
    doc.xref_set_key(foreign, "NM", pymupdf.get_pdf_str(FOREIGN_STAMP_NAME))
    doc.xref_set_key(foreign, "Name", "null")

    locked = _our_signature(doc, page, LOCKED_SIGNATURE_RECT, image, LOCKED_SIGNATURE_NAME)
    doc.xref_set_key(locked, "F", "132")

    text = page.add_freetext_annot(
        pymupdf.Rect(SIGNED_TEXT_RECT), "Signed by", fontsize=11, border_width=0
    )
    doc.xref_set_key(text.xref, "NM", pymupdf.get_pdf_str(SIGNED_TEXT_NAME))
    doc.xref_set_key(text.xref, "CL", "null")

    page2 = doc.new_page(width=A4[0], height=A4[1])
    page2.set_rotation(90)
    w2, h2, rgb2, alpha2 = sig_asym_samples(90)
    _our_signature(
        doc, page2, ROTATED_SIGNED_RECT, _sig_image(doc, w2, h2, rgb2, alpha2), ROTATED_SIGNED_NAME
    )
    doc.save(path)
    doc.close()
    return path


# -- M4: signature images (fixture 1) ----------------------------------------------------
#: Colour of the signature stroke in :func:`make_signature_image`.
SIG_INK_COLOR = (25, 35, 120)
#: Default pixel size of :func:`make_signature_image`.
SIG_PHOTO_SIZE = (1600, 640)
#: Stroke polyline and loop of the signature (fractions of the image size).
_SIG_POLYLINE = ((0.12, 0.62), (0.28, 0.22), (0.42, 0.66), (0.58, 0.24), (0.72, 0.6))
_SIG_LOOP = (0.84, 0.42, 0.06, 0.25)  # centre x, y, radius x, y
#: Probes (fractions of the image size): "ink" on the 22 px stroke, "paper" away from
#: any mark (corners, the hole of the loop, between strokes). "shadow" is the darkest
#: corner of the photo's paper gradient (also in "paper").
SIG_STROKE_POINTS: dict[str, tuple[tuple[float, float], ...]] = {
    "ink": (
        (0.20, 0.42),
        (0.35, 0.44),
        (0.50, 0.45),
        (0.65, 0.42),
        (0.78, 0.42),
        (0.84, 0.17),
    ),
    "paper": (
        (0.02, 0.03),
        (0.98, 0.03),
        (0.02, 0.97),
        (0.98, 0.97),
        (0.84, 0.42),
        (0.28, 0.55),
        (0.50, 0.10),
        (0.50, 0.95),
        (0.95, 0.50),
    ),
    "shadow": ((0.98, 0.97),),
}


def make_signature_image(path: Path, *, kind: str = "clean", size=SIG_PHOTO_SIZE) -> Path:
    """A signature image. ``kind="clean"``: transparent PNG with an anti-aliased
    ``SIG_INK_COLOR`` stroke and a loop (its hole is transparent). ``kind="photo"``: JPEG
    of the same stroke on paper with a 250 -> 150 brightness gradient (darkest at the
    bottom-right), speckle noise, a lighter 9 px line and a grey printed rule."""
    import random

    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen

    w, h = size
    if kind == "clean":
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
    elif kind == "photo":
        img = QImage(w, h, QImage.Format.Format_RGB32)
    else:
        raise ValueError(kind)
    p = QPainter(img)
    if kind == "photo":
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor(250, 246, 236))
        g.setColorAt(1, QColor(150, 145, 135))
        p.fillRect(0, 0, w, h, QBrush(g))
        rnd = random.Random(1)
        noise = QImage(w // 8, h // 8, QImage.Format.Format_ARGB32)
        for y in range(noise.height()):
            for x in range(noise.width()):
                noise.setPixel(x, y, rnd.randint(0, 40) << 24)  # translucent dark speckle
        p.drawImage(img.rect(), noise)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(*SIG_INK_COLOR), 22, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path_ = QPainterPath(QPointF(_SIG_POLYLINE[0][0] * w, _SIG_POLYLINE[0][1] * h))
    for fx, fy in _SIG_POLYLINE[1:]:
        path_.lineTo(fx * w, fy * h)
    p.drawPath(path_)
    cx, cy, rx, ry = _SIG_LOOP
    p.drawEllipse(QRectF((cx - rx) * w, (cy - ry) * h, 2 * rx * w, 2 * ry * h))
    if kind == "photo":
        p.setPen(QPen(QColor(*SIG_INK_COLOR, 140), 9))
        p.drawLine(QPointF(0.15 * w, 0.80 * h), QPointF(0.85 * w, 0.78 * h))
        p.setPen(QPen(QColor(90, 90, 90), 3))
        p.drawLine(QPointF(0.10 * w, 0.88 * h), QPointF(0.90 * w, 0.88 * h))
    p.end()
    if not img.save(str(path), "PNG" if kind == "clean" else "JPEG", -1 if kind == "clean" else 85):
        raise OSError(f"cannot write {path}")
    return Path(path)


# -- M4-T8: hardening ------------------------------------------------------------------
MANY_SIGNATURES = 100


def many_signatures_name(n: int) -> str:
    """/NM of signature ``n`` of :func:`make_many_signatures_pdf`."""
    return f"sig-{n:03d}"


def many_signatures_rect(n: int) -> Rect4:
    """Page-space rect of signature ``n`` of :func:`make_many_signatures_pdf` (10x10 grid
    of 50x20 pt rects)."""
    x0 = 30 + (n % 10) * 55
    y0 = 40 + (n // 10) * 75
    return (x0, y0, x0 + 50, y0 + 20)


def make_many_signatures_pdf(path: Path, n: int = MANY_SIGNATURES) -> Path:
    """One A4 page with ``n`` of our signatures sharing the asymmetric image, named by
    :func:`many_signatures_name`, laid out by :func:`many_signatures_rect`."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    image = _sig_image(doc, *sig_asym_samples())
    for i in range(n):
        _our_signature(doc, page, many_signatures_rect(i), image, many_signatures_name(i))
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


#: /NM -> page-space rect of the stamps of :func:`make_odd_stamps_pdf` (as placed, before
#: the odd /Rect values replace "no_rect" and "huge").
ODD_STAMP_RECTS: dict[str, Rect4] = {
    "ok": (50, 50, 250, 130),
    "no_image": (50, 150, 250, 190),
    "dct": (50, 210, 250, 290),
    "mask1": (50, 310, 250, 390),
    "no_rect": (300, 50, 500, 130),
    "huge": (300, 150, 500, 230),
    "no_width": (300, 250, 500, 330),
    "acrobat": (300, 350, 500, 430),
    "scaled": (300, 450, 500, 530),
}
#: /Contents, /T and /C of the "acrobat" stamp of :func:`make_odd_stamps_pdf`.
ODD_ACROBAT_KEYS = {"Contents": "Signed by Alice", "T": "Alice", "C": "[1 0 0]"}
#: Colour of the opaque JPEG of the "dct" stamp and of the "mask1" image.
ODD_DCT_COLOR = (30, 40, 160)
ODD_MASK1_COLOR = (200, 0, 0)


def _raw_image(doc: pymupdf.Document, header: str, data: bytes, filt: str = "") -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, f"<</Type/XObject/Subtype/Image{header}>>")
    doc.update_stream(xref, data, compress=not filt)
    if filt:  # update_stream(compress=False) drops /Filter: set it afterwards
        doc.xref_set_key(xref, "Filter", filt)
    return xref


def make_odd_stamps_pdf(path: Path) -> Path:
    """One A4 page with ``/IT /StampImage`` stamps (all named by /NM, see
    :data:`ODD_STAMP_RECTS`): "ok" (ours), "no_image" (an "Approved" text stamp with
    /IT /StampImage), "dct" (opaque 200x80 JPEG, no /SMask), "mask1" (200x80 RGB with a
    1-bit /SMask: top half opaque, bottom half clear), "no_rect" (/Rect null), "huge"
    (coordinates beyond 1e6 pt), "no_width" (image without /Width), "acrobat" (an
    Acrobat-like appearance: a 200x80 form drawing a red border and the image as /Im0,
    with :data:`ODD_ACROBAT_KEYS`) and "scaled" (MuPDF's appearance with a /Matrix)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    r = ODD_STAMP_RECTS
    image = _sig_image(doc, *sig_asym_samples())
    _our_signature(doc, page, r["ok"], image, "ok")

    text = page.add_stamp_annot(pymupdf.Rect(r["no_image"]))
    doc.xref_set_key(text.xref, "IT", "/StampImage")
    doc.xref_set_key(text.xref, "NM", pymupdf.get_pdf_str("no_image"))

    q = QImage(200, 80, QImage.Format.Format_RGB32)
    q.fill(QColor(*ODD_DCT_COLOR))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    q.save(buf, "JPEG", 90)
    buf.close()
    rgb = "/ColorSpace/DeviceRGB/BitsPerComponent 8"
    dct = _raw_image(doc, f"/Width 200/Height 80{rgb}", bytes(data.data()), "/DCTDecode")
    _our_signature(doc, page, r["dct"], dct, "dct")

    row = (200 + 7) // 8
    bits = b"\xff" * (row * 40) + b"\x00" * (row * 40)
    smask = _raw_image(doc, "/Width 200/Height 80/ColorSpace/DeviceGray/BitsPerComponent 1", bits)
    mask1 = _raw_image(
        doc, f"/Width 200/Height 80{rgb}/SMask {smask} 0 R", bytes(ODD_MASK1_COLOR) * (200 * 80)
    )
    _our_signature(doc, page, r["mask1"], mask1, "mask1")

    no_rect = _our_signature(doc, page, r["no_rect"], image, "no_rect")
    huge = _our_signature(doc, page, r["huge"], image, "huge")
    no_width = _raw_image(doc, f"/Height 10{rgb}", b"\0" * 300)
    _our_signature(doc, page, r["no_width"], no_width, "no_width")
    acrobat = _our_signature(doc, page, r["acrobat"], image, "acrobat")
    ap = doc.get_new_xref()
    doc.update_object(
        ap,
        f"<</Type/XObject/Subtype/Form/BBox[0 0 200 80]/Matrix[1 0 0 1 0 0]"
        f"/Resources<</XObject<</Im0 {image} 0 R>>>>>>",
    )
    doc.update_stream(ap, b"q 1 0 0 RG 2 w 1 1 198 78 re S Q q 200 0 0 80 0 0 cm /Im0 Do Q")
    doc.xref_set_key(acrobat, "AP", f"<</N {ap} 0 R>>")
    for key, value in ODD_ACROBAT_KEYS.items():
        doc.xref_set_key(acrobat, key, value if key == "C" else pymupdf.get_pdf_str(value))
    scaled = _our_signature(doc, page, r["scaled"], image, "scaled")
    scaled_ap = int(doc.xref_get_key(scaled, "AP/N")[1].split()[0])
    doc.xref_set_key(scaled_ap, "Matrix", "[2 0 0 2 0 0]")
    del page
    doc.xref_set_key(no_rect, "Rect", "null")
    doc.xref_set_key(huge, "Rect", "[1000000 1000000 2000000 2000010]")
    doc.save(path)
    doc.close()
    return path
