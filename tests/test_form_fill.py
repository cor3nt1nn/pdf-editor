"""M2-T2: form write side (forms.set_*, PdfDocument.set_field_value, SetFieldValueCommand)."""

from __future__ import annotations

import pymupdf
import pytest
from fixtures import LO_ESCAPED_TOKEN, PASSWORD, make_lo_form_pdf
from PySide6.QtCore import QRectF
from PySide6.QtGui import QUndoStack

from pdfeditor.core.commands import SetFieldValueCommand
from pdfeditor.core.document import FieldError, PdfDocument
from pdfeditor.core.forms import WidgetInfo

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


@pytest.mark.parametrize("encrypted", [False, True])
def test_incremental_save_persists_values(qtbot, tmp_path, encrypted: bool) -> None:
    path = make_lo_form_pdf(tmp_path / "form.pdf", encrypted=encrypted)
    password = PASSWORD if encrypted else None
    size_before = path.stat().st_size
    original = path.read_bytes()
    doc = PdfDocument.open(path, password=password)
    try:
        assert doc.can_save_incrementally()
        _fill_some(doc, QUndoStack())
        doc.save()
        data = path.read_bytes()
        assert len(data) > size_before and data.startswith(original)  # appended update
        pymupdf.TOOLS.mupdf_warnings()
        doc.render(0, 1.0)
        assert pymupdf.TOOLS.mupdf_warnings() == ""
    finally:
        doc.close()
    _check_filled(path, password)


def test_command_undoes_after_full_save(qtbot, lo_doc: PdfDocument, lo_form_pdf) -> None:
    stack = QUndoStack()
    _fill_some(lo_doc, stack)
    lo_doc.save(force_full=True)  # garbage=3: objects may be renumbered
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
