"""M2-T1: form widget read side (core/forms.py, PdfDocument widget API)."""

from __future__ import annotations

import pymupdf
import pytest
from fixtures import (
    LO_EDITABLE,
    LO_ESCAPED_TOKEN,
    LO_MAX_LEN,
    LO_NOT_EDITABLE,
    PASSWORD,
)
from PySide6.QtCore import QRectF

from pdfeditor.core import forms
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.forms import FieldKind, WidgetInfo


@pytest.fixture
def lo_doc(lo_form_pdf):
    d = PdfDocument.open(lo_form_pdf)
    yield d
    d.close()


def _by_name(doc: PdfDocument, name: str, page: int = 0) -> WidgetInfo:
    return next(w for w in doc.widgets(page) if w.name == name)


def _key(w: WidgetInfo) -> tuple:
    return (w.page, w.name, str(w.kind), tuple(round(v, 3) for v in w.unrotated_rect))


def test_lo_form_widget_inventory(lo_doc: PdfDocument) -> None:
    widgets = lo_doc.all_widgets()
    editable = [w for w in widgets if w.editable]
    assert len(editable) == len(LO_EDITABLE) == 13
    assert sorted(_key(w) for w in editable) == sorted(
        (p, n, k, tuple(float(v) for v in r)) for p, n, k, r in LO_EDITABLE
    )
    assert sorted(_key(w) for w in widgets if not w.editable) == sorted(
        (p, n, k, tuple(float(v) for v in r)) for p, n, k, r in LO_NOT_EDITABLE
    )
    assert lo_doc.is_form
    assert lo_doc.can_fill_forms


def test_lo_form_widget_details(lo_doc: PdfDocument) -> None:
    cb13 = _by_name(lo_doc, "Case à cocher 1_3")
    assert cb13.kind is FieldKind.CHECKBOX
    assert cb13.on_state_token == LO_ESCAPED_TOKEN
    assert cb13.on_state == "Case à cocher 1_3"
    assert cb13.value == "Off" and not cb13.is_on

    cb14 = _by_name(lo_doc, "Case à cocher 1_4")
    assert cb14.is_on and cb14.value == "Oui" and cb14.on_state_token == "Oui"
    assert not _by_name(lo_doc, "Case à cocher 1_2").is_on

    read_only = _by_name(lo_doc, "Zone de texte lecture")
    assert read_only.read_only and not read_only.editable and read_only.value == "fixe"
    hidden = _by_name(lo_doc, "Zone masquée")
    assert hidden.hidden and not hidden.editable

    combo = _by_name(lo_doc, "Civilité")
    assert combo.kind is FieldKind.COMBO and not combo.editable_combo
    assert combo.choices == (("m", "Monsieur"), ("f", "Madame"), ("Autre", "Autre"))
    listbox = _by_name(lo_doc, "Couleur")
    assert listbox.kind is FieldKind.LIST
    assert [export for export, _ in listbox.choices] == ["Rouge", "Vert", "Bleu"]

    assert _by_name(lo_doc, "Zone de texte 8_54").max_len == LO_MAX_LEN
    assert _by_name(lo_doc, "Zone de texte 8_54").value == ""
    assert _by_name(lo_doc, "Zone de texte 8_55").value == "déjà"
    multi = _by_name(lo_doc, "Zone de texte multi")
    assert multi.multiline and multi.font_size == 10
    assert not _by_name(lo_doc, "Zone de texte 8_54").multiline


def test_kids_share_field_and_inherit(lo_doc: PdfDocument) -> None:
    nom1, nom2 = _by_name(lo_doc, "Nom", 0), _by_name(lo_doc, "Nom", 1)
    assert nom1.font_size == 8 and nom2.font_size == 8  # /DA inherited from the parent
    assert nom1.field_xref == nom2.field_xref != nom1.xref
    radios = [w for w in lo_doc.widgets(0) if w.name == "Sexe"]
    assert len(radios) == 2
    assert all(w.kind is FieldKind.RADIO for w in radios)
    assert {w.on_state for w in radios} == {"M", "F"}
    assert radios[0].field_xref == radios[1].field_xref
    assert not any(w.is_on for w in radios)
    cb = _by_name(lo_doc, "Case à cocher 1_2")
    assert cb.field_xref == cb.xref
    with lo_doc.lock:
        assert forms.field_pages(lo_doc.fitz, nom1.field_xref) == [0, 1]
        assert forms.field_pages(lo_doc.fitz, radios[0].field_xref) == [0]
        assert forms.field_pages(lo_doc.fitz, cb.field_xref) == [0]
        assert forms.acroform_xref(lo_doc.fitz) > 0


def test_tab_order_is_geometric_across_pages(lo_doc: PdfDocument) -> None:
    # /Annots are bottom-first and /Fields scrambled; tab order is reading order.
    assert [w.name for w in lo_doc.widgets(0)][0] == "Couleur"
    order = forms.tab_order(lo_doc.all_widgets())
    assert [(w.page, w.name, w.unrotated_rect[0]) for w in order] == [
        (p, n, float(r[0])) for p, n, _k, r in LO_EDITABLE
    ]


def _info(page: int, name: str, rect: tuple[float, float, float, float]) -> WidgetInfo:
    x0, y0, x1, y1 = rect
    return WidgetInfo(
        page, 0, 0, name, FieldKind.TEXT, "", "", "", (), 0, 4, 0, 8.0,
        QRectF(x0, y0, x1 - x0, y1 - y0), rect,
    )  # fmt: skip


def test_tab_order_rows_tolerate_misalignment() -> None:
    widgets = [
        _info(1, "p2", (10, 10, 50, 20)),
        _info(0, "right", (200, 102, 300, 116)),  # same row, 2pt lower
        _info(0, "below", (10, 130, 100, 144)),
        _info(0, "left", (10, 100, 100, 114)),
    ]
    assert [w.name for w in forms.tab_order(widgets)] == ["left", "right", "below", "p2"]


def test_rotated_fixture_rects(lo_form_pdf, lo_form_rotated_pdf) -> None:
    plain = PdfDocument.open(lo_form_pdf)
    rotated = PdfDocument.open(lo_form_rotated_pdf)
    try:
        assert rotated.page_rotation(0) == 90
        size = rotated.page_size(0)
        page_rect = QRectF(0, 0, size.width(), size.height())
        for a, b in zip(plain.widgets(0), rotated.widgets(0), strict=True):
            assert a.unrotated_rect == b.unrotated_rect
            assert page_rect.contains(b.rect)
        assert rotated.widget_rects(0) == [(w.name, w.rect) for w in rotated.widgets(0)]
        # A widget near the top of the unrotated page ends up near the right edge.
        nom = _by_name(rotated, "Nom")
        assert nom.rect.left() > size.width() / 2
    finally:
        plain.close()
        rotated.close()


def test_can_fill_forms_uses_permissions(
    owner_locked_pdf, lo_form_encrypted_pdf, simple_pdf
) -> None:
    locked = PdfDocument.open(owner_locked_pdf)
    assert not locked.is_encrypted  # opens without a password...
    assert locked.is_form
    assert not locked.can_fill_forms  # ...but filling is forbidden
    locked.close()

    enc = PdfDocument.open(lo_form_encrypted_pdf, password=PASSWORD)
    assert enc.is_encrypted and enc.can_fill_forms and enc.is_form
    assert len([w for w in enc.all_widgets() if w.editable]) == len(LO_EDITABLE)
    enc.close()

    simple = PdfDocument.open(simple_pdf)
    assert not simple.is_form and simple.can_fill_forms
    assert simple.all_widgets() == []
    with simple.lock:
        assert forms.acroform_xref(simple.fitz) == 0
    simple.close()


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("Case#20#C3#A0", "Case à"),
        ("/Oui", "Oui"),
        ("Off", "Off"),
        (LO_ESCAPED_TOKEN, "Case à cocher 1_3"),
        ("caf#E9", "café"),  # not UTF-8: Latin-1 fallback
        ("a#2", "a#2"),  # incomplete escape left alone
    ],
)
def test_decode_pdf_name(token: str, expected: str) -> None:
    assert forms.decode_pdf_name(token) == expected


def test_text_fits() -> None:
    assert forms.text_fits("", 8, 10)
    assert forms.text_fits("court", 8, 100)
    assert not forms.text_fits("un texte beaucoup trop long pour la zone", 8, 60)
    assert forms.text_fits("un texte beaucoup trop long pour la zone", 0, 60)  # auto size
    assert not forms.text_fits("ok\nune ligne beaucoup trop longue", 8, 60)
    width = pymupdf.get_text_length("éàç", fontname="helv", fontsize=8)
    assert forms.text_fits("éàç", 8, width + 4)
    assert not forms.text_fits("éàç", 8, width + 3.9)


def test_resolve_widget(lo_doc: PdfDocument) -> None:
    nom = _by_name(lo_doc, "Nom")
    other = _by_name(lo_doc, "Couleur")
    with lo_doc.lock:
        page, w = forms.resolve_widget(lo_doc.fitz, 0, nom.xref, "Nom", nom.unrotated_rect)
        assert w.xref == nom.xref and page.number == 0
        # Stale xref (e.g. after a renumbering full save): found again by name + rect.
        _page, w = forms.resolve_widget(lo_doc.fitz, 0, other.xref, "Nom", nom.unrotated_rect)
        assert w.xref == nom.xref
        _page, w = forms.resolve_widget(lo_doc.fitz, 0, 999_999, "Nom", nom.unrotated_rect)
        assert w.xref == nom.xref
        assert forms.resolve_widget(lo_doc.fitz, 0, nom.xref, "Gone", None) is None
        assert forms.resolve_widget(lo_doc.fitz, 0, nom.xref, "Nom", (0, 0, 1, 1)) is None


def test_widget_cache_invalidation(qtbot, lo_doc: PdfDocument) -> None:
    first = lo_doc.widgets(0)
    assert lo_doc.widgets(0)[0] is first[0]  # cached
    nom = _by_name(lo_doc, "Nom")
    assert lo_doc.widget(0, nom.xref) == nom
    assert lo_doc.widget(1, nom.xref) is None

    page1 = lo_doc.widgets(1)
    lo_doc.page_changed.emit(0)
    assert lo_doc.widgets(0)[0] is not first[0]  # page 0 re-read
    assert lo_doc.widgets(1)[0] is page1[0]  # page 1 kept

    cached = lo_doc.widgets(0)[0]
    lo_doc.structure_changed.emit()
    assert lo_doc.widgets(0)[0] is not cached

    cached = lo_doc.widgets(0)[0]
    with qtbot.waitSignal(lo_doc.reloaded):
        lo_doc.save(force_full=True)
    after = lo_doc.widgets(0)
    assert after[0] is not cached
    assert sorted(_key(w) for w in after) == sorted(_key(w) for w in first)

    cached = lo_doc.widgets(0)[0]
    lo_doc.path_changed.emit("elsewhere.pdf")
    assert lo_doc.widgets(0)[0] is not cached


@pytest.mark.parametrize("force_full", [False, True])
def test_encrypted_render_after_save_has_no_mupdf_warnings(
    lo_form_encrypted_pdf, force_full: bool
) -> None:
    """F15: needs_pass must never be read after authenticate() (open and reload)."""
    doc = PdfDocument.open(lo_form_encrypted_pdf, password=PASSWORD)
    try:
        pymupdf.TOOLS.mupdf_warnings()  # reset
        doc.render(0, 1.0)
        assert pymupdf.TOOLS.mupdf_warnings() == ""
        reloads: list[bool] = []
        doc.reloaded.connect(lambda: reloads.append(True))
        doc.set_page_rotation(1, 90)
        doc.save(force_full=force_full)
        assert reloads == [True]  # the saved bytes were reloaded (and decrypt correctly)
        assert doc.can_save_incrementally()
        doc.render(0, 1.0)
        doc.render(1, 1.0)
        assert pymupdf.TOOLS.mupdf_warnings() == ""
        assert doc.can_fill_forms
        assert len([w for w in doc.all_widgets() if w.editable]) == len(LO_EDITABLE)
    finally:
        doc.close()
