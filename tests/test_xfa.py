"""M2-T3: XFA detection, stripping and the save-time hook."""

from __future__ import annotations

import pymupdf
import pytest
from fixtures import XFA_FIELD_NAME, make_dynamic_xfa_pdf

from pdfeditor.core import forms
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.forms import XfaKind
from pdfeditor.ui.document_view import DocumentView


def _kind(path) -> XfaKind:
    d = PdfDocument.open(path)
    try:
        return d.xfa_kind
    finally:
        d.close()


def _raw_xfa(path) -> tuple[str, str]:
    with pymupdf.open(path) as d:
        return d.xref_get_key(d.pdf_catalog(), "AcroForm/XFA")


def test_detect_static(static_xfa_pdf) -> None:
    assert _kind(static_xfa_pdf) is XfaKind.STATIC


@pytest.mark.parametrize("single_stream", [False, True])
def test_detect_dynamic(tmp_path, single_stream: bool) -> None:
    path = make_dynamic_xfa_pdf(tmp_path / "dyn.pdf", single_stream=single_stream)
    assert _raw_xfa(path)[0] == ("xref" if single_stream else "array")
    assert _kind(path) is XfaKind.DYNAMIC


def test_detect_none(lo_form_pdf, simple_pdf) -> None:
    assert _kind(lo_form_pdf) is XfaKind.NONE
    assert _kind(simple_pdf) is XfaKind.NONE


def test_xfa_without_fields_is_dynamic() -> None:
    """dynamicRender forbidden (or absent) but nothing to fill: still DYNAMIC."""
    doc = pymupdf.open()
    doc.new_page()
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, b"<template/>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"<</Fields[]/XFA[(template) {x} 0 R]>>")
    assert forms.detect_xfa(doc) is XfaKind.DYNAMIC
    doc.close()


def test_strip_direct_acroform() -> None:
    doc = pymupdf.open()
    doc.new_page()
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "AcroForm", "<</Fields[]/XFA(dummy)>>")
    assert forms.acroform_xref(doc) == 0
    assert forms.strip_xfa(doc) == "dummy"
    assert doc.xref_get_key(cat, "AcroForm/XFA") == ("null", "null")
    assert doc.xref_get_key(cat, "AcroForm/Fields")[0] == "array"
    assert forms.strip_xfa(doc) is None
    doc.close()


def test_strip_indirect_acroform_survives_save(static_xfa_pdf) -> None:
    d = PdfDocument.open(static_xfa_pdf)
    try:
        with d.lock:
            acro = forms.acroform_xref(d.fitz)
            assert acro
        assert d.strip_xfa()
        assert d.xfa_kind is XfaKind.NONE
        with d.lock:
            assert d.fitz.xref_get_key(acro, "XFA") == ("null", "null")
            assert "replace me" not in d.fitz.xref_object(acro)
        assert not d.strip_xfa()  # already gone
        d.save()
        assert d.xfa_kind is XfaKind.NONE  # recomputed on reloaded
        assert d.is_form
    finally:
        d.close()
    with pymupdf.open(static_xfa_pdf) as raw:
        acro = forms.acroform_xref(raw)
        assert raw.xref_get_key(acro, "XFA") == ("null", "null")
        assert "replace me" not in raw.xref_object(acro)
        assert [w.field_name for w in raw[0].widgets()] == [XFA_FIELD_NAME]
    assert _kind(static_xfa_pdf) is XfaKind.NONE


@pytest.fixture
def view(qtbot):
    v = DocumentView()
    qtbot.addWidget(v)
    yield v
    v.shutdown()


def _text_widget(doc: PdfDocument):
    return next(w for w in doc.widgets(0) if w.name == XFA_FIELD_NAME)


@pytest.mark.parametrize("save_as", [False, True])
def test_save_strips_static_xfa_after_form_edit(view, static_xfa_pdf, tmp_path, save_as) -> None:
    doc = view.open(str(static_xfa_pdf))
    assert doc.xfa_kind is XfaKind.STATIC
    doc.set_field_value(0, _text_widget(doc).xref, "nouveau")
    target = tmp_path / "copy.pdf" if save_as else static_xfa_pdf
    if save_as:
        view.save_as(str(target))
    else:
        view.save()
    assert doc.xfa_kind is XfaKind.NONE
    assert _raw_xfa(target) == ("null", "null")
    reopened = PdfDocument.open(target)
    try:
        assert reopened.xfa_kind is XfaKind.NONE
        assert _text_widget(reopened).value == "nouveau"
    finally:
        reopened.close()


def test_save_keeps_xfa_without_form_edit(view, static_xfa_pdf) -> None:
    doc = view.open(str(static_xfa_pdf))
    doc.set_page_rotation(0, 90)
    view.save()
    assert not doc.form_edited
    assert doc.xfa_kind is XfaKind.STATIC
    assert _raw_xfa(static_xfa_pdf)[0] == "array"
    assert _kind(static_xfa_pdf) is XfaKind.STATIC


def test_save_keeps_dynamic_xfa(view, dynamic_xfa_pdf) -> None:
    doc = view.open(str(dynamic_xfa_pdf))
    doc.set_page_rotation(0, 90)
    view.save()
    assert _kind(dynamic_xfa_pdf) is XfaKind.DYNAMIC
