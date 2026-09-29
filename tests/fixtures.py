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
) -> Path:
    """A form shaped like LibreOffice exports (see ``LO_EDITABLE`` / ``LO_NOT_EDITABLE``).

    Indirect /AcroForm with /DA ``/Helv 0 Tf 0 g`` and /DR fonts Helv (no /Encoding) and
    ZaDb, no NeedAppearances, /Fields in scrambled order, /Annots bottom-first. Names use
    PDFDocEncoding accents; checkbox 1_3 has a #-escaped on-state token; 1_4 is checked;
    ``Sexe`` is a real radio group (parent + kids M/F); ``Nom`` has a kid on each page.
    ``rotate``/``cropbox`` apply to page 1 only.
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
            "/Opt[[(m)(Monsieur)][(f)(Madame)](Autre)]",
        )
    )
    fields.append(
        widget(
            0,
            rect_of(0, "Couleur", 370),
            "/FT/Ch/F 4/DA(/Helv 8 Tf 0 g)/T(Couleur)/Opt[(Rouge)(Vert)(Bleu)]",
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
        f"/DA(/Helv 0 Tf 0 g)/DR<</Font<</Helv {helv} 0 R/ZaDb {zadb} 0 R>>>>>>"
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
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=PASSWORD,
            owner_pw="owner-" + PASSWORD,
            permissions=LO_PERMISSIONS,
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
