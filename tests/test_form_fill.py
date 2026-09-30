"""M2-T2: form write side (forms.set_*, PdfDocument.set_field_value, SetFieldValueCommand)."""

from __future__ import annotations

import dataclasses

import pymupdf
import pytest
from fixtures import (
    LO_ESCAPED_TOKEN,
    LO_PREFILLED,
    MP_RADIO_NAME,
    PASSWORD,
    make_lo_form_pdf,
    make_multipage_radio_pdf,
)
from PySide6.QtCore import QRectF
from PySide6.QtGui import QUndoStack

from pdfeditor.core.commands import SetFieldValueCommand
from pdfeditor.core.document import FieldError, PdfDocument
from pdfeditor.core.forms import FF_NO_TOGGLE_TO_OFF, WidgetInfo, resolve_widget

ACCENTED = "Élève : é à ç œ €"
LONG_TEXT = "un texte beaucoup trop long pour tenir dans cette zone de saisie, vraiment"


@pytest.fixture
def lo_doc(lo_form_pdf):
    d = PdfDocument.open(lo_form_pdf)
    yield d
    d.close()


def _by_name(doc: PdfDocument, name: str, page: int = 0, index: int = 0) -> WidgetInfo:
    return [w for w in doc.widgets(page) if w.name == name][index]


def _ap_stream(doc: PdfDocument, xref: int) -> bytes:
    with doc.lock:
        kind, value = doc.fitz.xref_get_key(xref, "AP/N")
        assert kind == "xref", (kind, value)
        return doc.fitz.xref_stream(int(value.split()[0]))


def _key(doc: PdfDocument, xref: int, key: str) -> tuple[str, str]:
    with doc.lock:
        return doc.fitz.xref_get_key(xref, key)


def _raw(doc: PdfDocument, xref: int) -> str:
    """The object source as stored (``xref_get_key`` decodes #-escaped names)."""
    with doc.lock:
        return doc.fitz.xref_object(xref, compressed=True)


def _page_text(doc: PdfDocument, page: int) -> str:
    with doc.lock:
        return doc.fitz[page].get_text()


def _dark_pixels(doc: PdfDocument, page: int, rect: QRectF, scale: float = 2.0) -> int:
    """Pixels darker than mid-gray inside ``rect`` (page space) of a render at ``scale``."""
    image = doc.render(page, scale)
    r = QRectF(rect.x() * scale, rect.y() * scale, rect.width() * scale, rect.height() * scale)
    r = r.toAlignedRect().intersected(image.rect())
    count = 0
    for y in range(r.top(), r.bottom() + 1):
        for x in range(r.left(), r.right() + 1):
            if image.pixelColor(x, y).lightness() < 100:
                count += 1
    return count


def _changed_pages(doc: PdfDocument) -> list[int]:
    pages: list[int] = []
    doc.page_changed.connect(pages.append)
    return pages


# -- checkboxes and radios ---------------------------------------------------------
def test_toggle_escaped_checkbox_keeps_authored_appearance(qtbot, lo_doc: PdfDocument) -> None:
    info = _by_name(lo_doc, "Case à cocher 1_3")
    ap_before = _key(lo_doc, info.xref, "AP")
    assert _dark_pixels(lo_doc, 0, info.rect) == 0
    stack = QUndoStack()
    pages = _changed_pages(lo_doc)
    stack.push(SetFieldValueCommand(lo_doc, info, True))
    assert pages == [0]
    assert _key(lo_doc, info.xref, "AS") == ("name", "/Case à cocher 1_3")
    raw = _raw(lo_doc, info.xref)
    assert f"/AS/{LO_ESCAPED_TOKEN}" in raw and f"/V/{LO_ESCAPED_TOKEN}" in raw
    assert _key(lo_doc, info.xref, "AP") == ap_before  # authored /AP /N untouched
    assert _by_name(lo_doc, "Case à cocher 1_3").is_on
    assert _dark_pixels(lo_doc, 0, info.rect) > 50
    assert lo_doc.form_edited

    stack.undo()
    assert _key(lo_doc, info.xref, "AS") == ("name", "/Off")
    assert _key(lo_doc, info.xref, "V") == ("name", "/Off")
    assert not _by_name(lo_doc, "Case à cocher 1_3").is_on
    assert _dark_pixels(lo_doc, 0, info.rect) == 0
    stack.redo()
    assert f"/AS/{LO_ESCAPED_TOKEN}" in _raw(lo_doc, info.xref)
    assert _key(lo_doc, info.xref, "AP") == ap_before


def test_uncheck_prefilled_checkbox(qtbot, lo_doc: PdfDocument) -> None:
    info = _by_name(lo_doc, "Case à cocher 1_4")
    assert info.is_on
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(lo_doc, info, False))
    assert _key(lo_doc, info.xref, "AS") == ("name", "/Off")
    stack.undo()
    assert _key(lo_doc, info.xref, "AS") == ("name", "/Oui")
    assert _key(lo_doc, info.xref, "V") == ("name", "/Oui")


def test_radio_group(qtbot, lo_doc: PdfDocument) -> None:
    male, female = (_by_name(lo_doc, "Sexe", index=i) for i in (0, 1))
    if male.on_state != "M":
        male, female = female, male
    parent = male.field_xref
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(lo_doc, female, True))
    assert _key(lo_doc, female.xref, "AS") == ("name", "/F")
    assert _key(lo_doc, male.xref, "AS") == ("name", "/Off")
    assert _key(lo_doc, parent, "V") == ("name", "/F")
    assert _key(lo_doc, female.xref, "V") == ("null", "null")  # /V only on the parent

    stack.push(SetFieldValueCommand(lo_doc, _by_name(lo_doc, "Sexe", index=0), True))
    stack.push(SetFieldValueCommand(lo_doc, male, True))
    assert _key(lo_doc, male.xref, "AS") == ("name", "/M")
    assert _key(lo_doc, female.xref, "AS") == ("name", "/Off")
    assert _key(lo_doc, parent, "V") == ("name", "/M")
    stack.undo()
    stack.undo()  # back to F: the sibling that was on is re-selected
    assert _key(lo_doc, female.xref, "AS") == ("name", "/F")
    assert _key(lo_doc, male.xref, "AS") == ("name", "/Off")
    assert _key(lo_doc, parent, "V") == ("name", "/F")
    stack.undo()
    assert _key(lo_doc, female.xref, "AS") == ("name", "/Off")
    assert _key(lo_doc, parent, "V") == ("name", "/Off")


# -- text and choices --------------------------------------------------------------
def test_accented_text_appearance(qtbot, lo_doc: PdfDocument) -> None:
    info = _by_name(lo_doc, "Zone de texte 8_54")
    with lo_doc.lock:
        acro = int(lo_doc.fitz.xref_get_key(lo_doc.fitz.pdf_catalog(), "AcroForm")[1].split()[0])
    dr_before = _key(lo_doc, acro, "DR")
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(lo_doc, info, ACCENTED))
    assert stack.undoText() == "Edit form field"
    ap = _ap_stream(lo_doc, info.xref)
    for byte in (b"\xe9", b"\xe0", b"\xe7", b"\x9c", b"\x80"):
        assert byte in ap
    assert b"/Helv 8 Tf" in ap
    assert ACCENTED in _page_text(lo_doc, 0)
    assert _by_name(lo_doc, "Zone de texte 8_54").value == ACCENTED
    assert _key(lo_doc, acro, "DR") == dr_before
    assert _key(lo_doc, acro, "NeedAppearances") == ("null", "null")


def test_multi_page_field_updates_every_kid(qtbot, lo_doc: PdfDocument) -> None:
    info = _by_name(lo_doc, "Nom")
    pages = _changed_pages(lo_doc)
    assert lo_doc.set_field_value(0, info.xref, "Dupont") == [0, 1]
    assert pages == [0, 1]
    assert "Dupont" in _page_text(lo_doc, 0) and "Dupont" in _page_text(lo_doc, 1)
    assert _by_name(lo_doc, "Nom", page=1).value == "Dupont"

    pages.clear()
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(lo_doc, _by_name(lo_doc, "Nom", page=1), ""))
    assert pages == [0, 1]
    assert "Dupont" not in _page_text(lo_doc, 0) and "Dupont" not in _page_text(lo_doc, 1)
    stack.undo()
    assert "Dupont" in _page_text(lo_doc, 0) and "Dupont" in _page_text(lo_doc, 1)


def test_auto_shrink_and_undo_restores_font_size(qtbot, lo_doc: PdfDocument) -> None:
    info = _by_name(lo_doc, "Zone de texte 8_55")
    assert info.value == "déjà" and info.font_size == 8
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(lo_doc, info, LONG_TEXT, font_size=0))
    ap = _ap_stream(lo_doc, info.xref)
    size = float(ap.split(b" Tf")[0].split()[-1])
    assert 0 < size < 8
    assert _by_name(lo_doc, "Zone de texte 8_55").font_size == 0
    stack.undo()
    assert b"/Helv 8 Tf" in _ap_stream(lo_doc, info.xref)
    restored = _by_name(lo_doc, "Zone de texte 8_55")
    assert restored.value == "déjà" and restored.font_size == 8
    assert "déjà" in _page_text(lo_doc, 0)


def test_clearing_and_undo_of_first_fill(qtbot, lo_doc: PdfDocument, lo_form_pdf) -> None:
    info = _by_name(lo_doc, "Zone de texte 8_54")
    assert info.value == ""
    blank = _dark_pixels(lo_doc, 0, info.rect)
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(lo_doc, info, "Premier remplissage"))
    assert "Premier remplissage" in _page_text(lo_doc, 0)
    assert _dark_pixels(lo_doc, 0, info.rect) > blank + 50

    stack.undo()  # back to empty
    assert _by_name(lo_doc, "Zone de texte 8_54").value == ""
    assert "Premier remplissage" not in _page_text(lo_doc, 0)
    assert _dark_pixels(lo_doc, 0, info.rect) == blank
    lo_doc.save()
    reopened = PdfDocument.open(lo_form_pdf)
    try:
        assert _by_name(reopened, "Zone de texte 8_54").value == ""
        assert "Premier remplissage" not in _page_text(reopened, 0)
    finally:
        reopened.close()

    # Clearing a prefilled field.
    prefilled = _by_name(lo_doc, "Zone de texte 8_55")
    stack.push(SetFieldValueCommand(lo_doc, prefilled, ""))
    assert _by_name(lo_doc, "Zone de texte 8_55").value == ""
    assert "déjà" not in _page_text(lo_doc, 0)
    assert _dark_pixels(lo_doc, 0, prefilled.rect) == 0
    stack.undo()
    assert "déjà" in _page_text(lo_doc, 0)


def test_combo_and_list(qtbot, lo_doc: PdfDocument) -> None:
    combo = _by_name(lo_doc, "Civilité")
    listbox = _by_name(lo_doc, "Couleur")
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(lo_doc, combo, "f"))
    stack.push(SetFieldValueCommand(lo_doc, listbox, "Vert"))
    assert _by_name(lo_doc, "Civilité").value == "f"
    assert _by_name(lo_doc, "Couleur").value == "Vert"
    assert _key(lo_doc, combo.xref, "V") == ("string", "f")
    stack.undo()
    stack.undo()
    assert _by_name(lo_doc, "Civilité").value == ""
    assert _by_name(lo_doc, "Couleur").value == ""
    assert _key(lo_doc, combo.xref, "V") == ("null", "null")


def test_combo_appearance_shows_display_text(qtbot, lo_doc, lo_form_pdf) -> None:
    combo = _by_name(lo_doc, "Civilité")
    assert ("f", "Madame") in combo.choices
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(lo_doc, combo, "f"))
    assert _key(lo_doc, combo.xref, "V") == ("string", "f")
    assert _by_name(lo_doc, "Civilité").value == "f"
    assert b"(Madame)" in _ap_stream(lo_doc, combo.xref)
    with lo_doc.lock:
        shown = lo_doc.fitz[0].get_text(clip=pymupdf.Rect(combo.unrotated_rect)).strip()
    assert shown == "Madame"
    stack.push(SetFieldValueCommand(lo_doc, _by_name(lo_doc, "Civilité"), "Autre"))
    assert _key(lo_doc, combo.xref, "V") == ("string", "Autre")  # export == display
    stack.undo()
    lo_doc.save(force_full=True)
    reopened = PdfDocument.open(lo_form_pdf)
    try:
        pymupdf.TOOLS.mupdf_warnings()
        info = _by_name(reopened, "Civilité")
        assert info.value == "f"
        with reopened.lock:
            clip = pymupdf.Rect(info.unrotated_rect)
            assert reopened.fitz[0].get_text(clip=clip).strip() == "Madame"
        reopened.render(0, 1.0)
        assert pymupdf.TOOLS.mupdf_warnings() == ""
    finally:
        reopened.close()


def test_prefilled_choices_are_read(qtbot, tmp_path) -> None:
    doc = PdfDocument.open(make_lo_form_pdf(tmp_path / "pre.pdf", prefill_choices=True))
    try:
        for name, value in LO_PREFILLED.items():
            assert _by_name(doc, name).value == value
        stack = QUndoStack()
        stack.push(SetFieldValueCommand(doc, _by_name(doc, "Couleur"), "Bleu"))
        stack.undo()
        assert _by_name(doc, "Couleur").value == "Vert"
    finally:
        doc.close()


def test_need_appearances_true_is_kept_and_values_render(qtbot, tmp_path) -> None:
    path = make_lo_form_pdf(tmp_path / "na.pdf", need_appearances=True)
    doc = PdfDocument.open(path)
    try:
        info = _by_name(doc, "Zone de texte 8_54")
        doc.set_field_value(0, info.xref, "Valeur NA")
        assert _dark_pixels(doc, 0, info.rect) > 50
        doc.save()
        doc.save(force_full=True)
    finally:
        doc.close()
    reopened = PdfDocument.open(path)
    try:
        with reopened.lock:
            catalog = reopened.fitz.pdf_catalog()
            need = reopened.fitz.xref_get_key(catalog, "AcroForm/NeedAppearances")
        assert need == ("bool", "true")
        info = _by_name(reopened, "Zone de texte 8_54")
        assert info.value == "Valeur NA"
        assert "Valeur NA" in _page_text(reopened, 0)
        assert _dark_pixels(reopened, 0, info.rect) > 50
    finally:
        reopened.close()


# -- multi-page radio group --------------------------------------------------------
@pytest.fixture
def mp_radio(tmp_path):
    d = PdfDocument.open(make_multipage_radio_pdf(tmp_path / "radio.pdf"))
    yield d
    d.close()


def _kid(doc: PdfDocument, state: str) -> WidgetInfo:
    return next(w for w in doc.all_widgets() if w.on_state == state)


def test_multi_page_radio_state_and_undo(qtbot, mp_radio: PdfDocument) -> None:
    a, c = _kid(mp_radio, "A"), _kid(mp_radio, "C")
    assert a.page == 0 and c.page == 1 and c.is_on and not a.is_on
    assert a.name == MP_RADIO_NAME and not a.flags & FF_NO_TOGGLE_TO_OFF
    assert mp_radio.field_button_state(a) == "C"  # the choice is on another page
    stack = QUndoStack()
    command = SetFieldValueCommand(mp_radio, a, True)
    assert command.old_value == "C"
    stack.push(command)
    assert _kid(mp_radio, "A").is_on and not _kid(mp_radio, "C").is_on
    assert _key(mp_radio, a.field_xref, "V") == ("name", "/A")
    stack.undo()  # C (page 2) is selected again, not the whole group turned off
    assert _kid(mp_radio, "C").is_on and not _kid(mp_radio, "A").is_on
    assert _key(mp_radio, a.field_xref, "V") == ("name", "/C")
    stack.redo()
    assert mp_radio.field_button_state(c) == "A"


def test_field_button_state_falls_back_to_v(qtbot, mp_radio: PdfDocument) -> None:
    c = _kid(mp_radio, "C")
    with mp_radio.lock:
        mp_radio.fitz.xref_set_key(c.xref, "AS", "/Off")
    assert mp_radio.field_button_state(c) == "C"  # from /V
    with mp_radio.lock:
        mp_radio.fitz.xref_set_key(c.field_xref, "V", "/Off")
    assert mp_radio.field_button_state(c) == "Off"


def test_no_toggle_to_off_flag_is_read(qtbot, tmp_path) -> None:
    doc = PdfDocument.open(make_multipage_radio_pdf(tmp_path / "r.pdf", no_toggle_off=True))
    try:
        widgets = doc.all_widgets()
        assert len(widgets) == 3
        assert all(w.flags & FF_NO_TOGGLE_TO_OFF for w in widgets)
    finally:
        doc.close()


# -- saving ------------------------------------------------------------------------
def _fill_some(doc: PdfDocument, stack: QUndoStack) -> None:
    stack.push(SetFieldValueCommand(doc, _by_name(doc, "Zone de texte 8_54"), ACCENTED))
    stack.push(SetFieldValueCommand(doc, _by_name(doc, "Case à cocher 1_3"), True))
    stack.push(SetFieldValueCommand(doc, _by_name(doc, "Nom"), "Dupont"))


def _check_filled(path, password: str | None = None) -> None:
    doc = PdfDocument.open(path, password=password)
    try:
        pymupdf.TOOLS.mupdf_warnings()  # reset
        assert _by_name(doc, "Zone de texte 8_54").value == ACCENTED
        assert _by_name(doc, "Case à cocher 1_3").is_on
        assert _by_name(doc, "Nom", page=1).value == "Dupont"
        assert ACCENTED in _page_text(doc, 0)
        assert "Dupont" in _page_text(doc, 1)
        doc.render(0, 1.0)
        doc.render(1, 1.0)
        assert pymupdf.TOOLS.mupdf_warnings() == ""
    finally:
        doc.close()


SAVE_VARIANTS = {
    "plain": {},
    "no_objstms": {"objstms": False},
    "aes256": {"encrypted": True},
    "aes128": {"encrypted": True, "encryption": pymupdf.PDF_ENCRYPT_AES_128},
    "rc4_128": {"encrypted": True, "encryption": pymupdf.PDF_ENCRYPT_RC4_128},
    "owner_only": {"owner_only": True},
}


@pytest.mark.parametrize("full", [False, True], ids=["incremental", "full"])
@pytest.mark.parametrize("variant", list(SAVE_VARIANTS))
def test_save_persists_values(qtbot, tmp_path, variant: str, full: bool) -> None:
    kwargs = SAVE_VARIANTS[variant]
    path = make_lo_form_pdf(tmp_path / "form.pdf", **kwargs)
    password = PASSWORD if kwargs.get("encrypted") else None
    size_before = path.stat().st_size
    original = path.read_bytes()
    doc = PdfDocument.open(path, password=password)
    try:
        assert doc.can_fill_forms  # owner_only: opens without a password, filling allowed
        assert doc.is_encrypted == bool(password)
        assert doc.can_save_incrementally()
        _fill_some(doc, QUndoStack())
        doc.save(force_full=full)
        data = path.read_bytes()
        if not full:
            assert len(data) > size_before and data.startswith(original)  # appended update
        pymupdf.TOOLS.mupdf_warnings()
        doc.render(0, 1.0)
        assert pymupdf.TOOLS.mupdf_warnings() == ""
    finally:
        doc.close()
    raw = pymupdf.open(path)
    try:
        assert bool(raw.needs_pass) == bool(password)  # encryption kept
        if variant == "owner_only":  # still owner-protected: modifying is not allowed
            assert (raw.metadata or {}).get("encryption")
            assert not raw.permissions & pymupdf.PDF_PERM_MODIFY
    finally:
        raw.close()
    _check_filled(path, password)


def test_command_undoes_after_full_save(qtbot, lo_doc: PdfDocument, lo_form_pdf) -> None:
    stack = QUndoStack()
    _fill_some(lo_doc, stack)
    before = [(w.name, w.xref) for w in lo_doc.widgets(0)]
    lo_doc.save(force_full=True)  # garbage=3: objects are renumbered
    after = [(w.name, w.xref) for w in lo_doc.widgets(0)]
    assert [n for n, _x in after] == [n for n, _x in before]
    assert after != before  # the commands really hold stale xrefs
    _check_filled(lo_form_pdf)
    stack.undo()
    stack.undo()
    stack.undo()
    assert _by_name(lo_doc, "Zone de texte 8_54").value == ""
    assert not _by_name(lo_doc, "Case à cocher 1_3").is_on
    assert _by_name(lo_doc, "Nom", page=1).value == ""
    assert "Dupont" not in _page_text(lo_doc, 1)
    stack.redo()
    assert ACCENTED in _page_text(lo_doc, 0)


def test_stale_xref_is_resolved_by_name_and_rect(qtbot, lo_doc: PdfDocument) -> None:
    info = _by_name(lo_doc, "Zone de texte 8_54")
    other = _by_name(lo_doc, "Couleur")
    pages = lo_doc.set_field_value(
        0, other.xref, "abc", name=info.name, unrotated_rect=info.unrotated_rect
    )
    assert pages == [0]
    assert _by_name(lo_doc, "Zone de texte 8_54").value == "abc"
    assert _by_name(lo_doc, "Couleur").value == ""


def test_stale_xref_of_a_same_name_sibling_is_resolved_by_rect(qtbot, lo_doc) -> None:
    male, female = (_by_name(lo_doc, "Sexe", index=i) for i in (0, 1))
    with lo_doc.lock:
        found = resolve_widget(lo_doc.fitz, 0, female.xref, "Sexe", male.unrotated_rect)
        assert found is not None and found[1].xref == male.xref
    stack = QUndoStack()
    stale = dataclasses.replace(male, xref=female.xref)  # the xref names the sibling
    stack.push(SetFieldValueCommand(lo_doc, stale, True))
    assert _key(lo_doc, male.xref, "AS")[1] == "/" + male.on_state
    assert _key(lo_doc, female.xref, "AS") == ("name", "/Off")


def test_rotated_cropped_fill_renders_inside_widget(qtbot, tmp_path) -> None:
    path = make_lo_form_pdf(tmp_path / "rot.pdf", rotate=90, cropbox=True)
    doc = PdfDocument.open(path)
    try:
        info = _by_name(doc, "Zone de texte 8_54")
        size = doc.page_size(0)
        assert QRectF(0, 0, size.width(), size.height()).contains(info.rect)
        assert _dark_pixels(doc, 0, info.rect) == 0
        doc.set_field_value(0, info.xref, "MMMMMMMMMMMMMMMMMMMM")
        inside = _dark_pixels(doc, 0, info.rect)
        assert inside > 100
        # All the ink is inside the (page-space) widget rect, with 2pt slack.
        grown = info.rect.adjusted(-2, -2, 2, 2)
        whole = QRectF(0, 0, size.width(), size.height())
        blank_doc = PdfDocument.open(path)
        try:
            baseline = _dark_pixels(blank_doc, 0, whole)
        finally:
            blank_doc.close()
        assert _dark_pixels(doc, 0, whole) - baseline == _dark_pixels(doc, 0, grown)
    finally:
        doc.close()


# -- errors ------------------------------------------------------------------------
def test_field_error_when_widget_is_gone(qtbot, lo_doc: PdfDocument) -> None:
    info = _by_name(lo_doc, "Zone de texte 8_54")
    with pytest.raises(FieldError):
        lo_doc.set_field_value(0, 999_999, "x")  # unknown xref, no name
    with lo_doc.lock:
        page = lo_doc.fitz[0]
        widget = page.load_widget(info.xref)
        page.delete_widget(widget)
    with pytest.raises(FieldError):
        lo_doc.set_field_value(
            0, info.xref, "x", name=info.name, unrotated_rect=info.unrotated_rect
        )
    assert not lo_doc.form_edited
    with pytest.raises(FieldError):
        lo_doc.set_field_value(0, _by_name(lo_doc, "Nom").xref, True)  # text needs a str
