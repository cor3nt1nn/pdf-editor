"""M3-T7 hardening: many annotations, odd FreeText objects, reload and save failure."""

from __future__ import annotations

import time

import pytest
from fixtures import (
    MANY_ANNOTS,
    ODD_ANNOT_RECTS,
    many_annots_name,
    many_annots_rect,
)
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtWidgets import QMessageBox

import pdfeditor.core.document as document_module
from pdfeditor.core import annotations
from pdfeditor.core.commands import AddAnnotCommand
from pdfeditor.core.document import PdfDocument
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.base import ToolEvent

LEFT = Qt.MouseButton.LeftButton
NO_MOD = Qt.KeyboardModifier.NoModifier

#: Budget for re-resolving the selection after a page_changed (docs/M3_PLAN.md: < 5 ms)
#: with the annotation cache warm: measured about 0.02 ms. The cold re-read of the 200
#: annotations by MuPDF (what the first page_changed of that page after an edit costs;
#: measured 75-90 ms, PyMuPDF's load_annot(xref) is linear in the page's annotation
#: count) gets a looser bound so that a slow machine does not fail on MuPDF parsing time.
REFRESH_BUDGET_S = 0.005
COLD_REFRESH_BUDGET_S = 0.250


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def read_counter(monkeypatch):
    """Counts the pymupdf scans PdfDocument makes through annotations.read_annots."""
    calls: list[int] = []
    original = annotations.read_annots

    def counting(fitz_doc, page_index, **kw):
        calls.append(page_index)
        return original(fitz_doc, page_index, **kw)

    monkeypatch.setattr(annotations, "read_annots", counting)
    return calls


def _event(page: int | None, pos: QPointF | None, buttons=LEFT, modifiers=NO_MOD) -> ToolEvent:
    scene = QPointF() if pos is None else QPointF(pos)
    return ToolEvent(page, pos, scene, buttons, modifiers, None)


def _vp(w: MainWindow, page: int, point: QPointF) -> QPoint:
    pv = w.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _centre(rect: tuple[float, float, float, float]) -> QPointF:
    x0, y0, x1, y1 = rect
    return QPointF((x0 + x1) / 2, (y0 + y1) / 2)


# -- performance: 200 annotations on one page ------------------------------------------
def test_many_annots_are_cached(many_annots_pdf, read_counter) -> None:
    doc = PdfDocument.open(str(many_annots_pdf))
    try:
        first = doc.annots(0)
        assert len(first) == MANY_ANNOTS
        assert read_counter == [0]
        for n in (0, 99, MANY_ANNOTS - 1):
            assert doc.annot(0, many_annots_name(n)) is not None
        second = doc.annots(0)
        assert read_counter == [0]  # served from the cache
        assert second == first and second is not first
        doc.page_changed.emit(0)  # an annotation edit drops the page
        doc.annots(0)
        assert read_counter == [0, 0]
    finally:
        doc.close()


def test_selection_refresh_is_fast(window, many_annots_pdf, read_counter) -> None:
    w = window
    assert w.open_file(str(many_annots_pdf))
    w.act_text_tool.trigger()
    doc = w.document_view.document
    selection = w.document_view.annot_selection
    name = many_annots_name(MANY_ANNOTS - 1)
    selection.select(doc.annot(0, name))

    doc.annots(0)
    reads = len(read_counter)
    start = time.perf_counter()
    for _ in range(10):
        selection._on_page_changed(0)
    warm = (time.perf_counter() - start) / 10
    assert len(read_counter) == reads  # warm refresh does not touch pymupdf
    assert selection.current is not None and selection.current.name == name

    doc._annot_cache.clear()
    start = time.perf_counter()
    selection._on_page_changed(0)
    cold = time.perf_counter() - start
    assert len(read_counter) == reads + 1
    assert selection.current.name == name

    # Through the real signal (page view, thumbnails, field layer... also react).
    start = time.perf_counter()
    doc.page_changed.emit(0)
    signal = time.perf_counter() - start
    assert selection.current.name == name
    print(
        f"selection refresh, {MANY_ANNOTS} annots: warm {warm * 1000:.2f} ms, "
        f"cold {cold * 1000:.1f} ms, page_changed {signal * 1000:.1f} ms"
    )
    assert warm < REFRESH_BUDGET_S
    assert cold < COLD_REFRESH_BUDGET_S


def test_hit_testing_many_annots_uses_the_cache(window, many_annots_pdf, read_counter) -> None:
    w = window
    assert w.open_file(str(many_annots_pdf))
    w.act_text_tool.trigger()
    tool = w.text_tool
    w.document_view.document.annots(0)
    reads = len(read_counter)
    start = time.perf_counter()
    for n in range(500):
        tool.mouse_move(_event(0, QPointF(40 + n % 540, 40 + n % 760), Qt.MouseButton.NoButton))
    elapsed = time.perf_counter() - start
    assert len(read_counter) == reads
    target = MANY_ANNOTS // 2
    assert tool.annot_at(0, _centre(many_annots_rect(target))).name == many_annots_name(target)
    print(f"500 hover moves over {MANY_ANNOTS} annots: {elapsed * 1000:.1f} ms")
    assert elapsed < 2.0


# -- odd FreeText objects ----------------------------------------------------------------
def test_odd_annots_are_read_without_crash(odd_annots_pdf) -> None:
    doc = PdfDocument.open(str(odd_annots_pdf))
    try:
        infos = {a.name: a for a in doc.annots(0)}
        assert set(infos) == set(ODD_ANNOT_RECTS)
        assert infos["ok"].editable
        for name in ("no_rect", "huge", "zero", "far_negative"):
            assert infos[name].rect.isEmpty(), name
            assert not infos[name].editable, name
        assert not doc.render(0, 1.0).isNull()
        assert not doc.render(0, 2.0, QRectF(0, 0, 300, 300)).isNull()
        doc.page_shapes(0)  # the snapping scan copes with their appearances
    finally:
        doc.close()


def test_odd_annots_are_not_selectable(qtbot, window, odd_annots_pdf) -> None:
    w = window
    assert w.open_file(str(odd_annots_pdf))
    w.act_text_tool.trigger()
    tool = w.text_tool
    doc = w.document_view.document
    selection = w.document_view.annot_selection
    editor = w.document_view.annot_editor

    # Hover anywhere (the missing /Rect reads as MuPDF's infinite rect) without a crash.
    for y in range(20, 820, 40):
        for x in range(20, 580, 80):
            tool.mouse_move(_event(0, QPointF(x, y), Qt.MouseButton.NoButton))
    ok = _centre(ODD_ANNOT_RECTS["ok"])
    assert tool.annot_at(0, ok).name == "ok"
    for name in ("no_rect", "huge", "zero", "far_negative"):
        point = _centre(ODD_ANNOT_RECTS[name])
        assert tool.annot_at(0, point) is None, name
    assert tool.annot_at(0, QPointF(72, 210)) is None  # where the zero-size rect sits

    # A click where an odd annotation was placed selects nothing: it starts a new box.
    qtbot.mouseClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, 0, QPointF(150, 160)))
    assert selection.current is None
    assert editor.is_open
    editor.cancel()
    assert w.undo_stack.count() == 0

    # The normal one still works, and the others survive its edit.
    qtbot.mouseClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, 0, ok))
    assert selection.current is not None and selection.current.name == "ok"
    assert tool.delete_selection()
    assert {a.name for a in doc.annots(0)} == set(ODD_ANNOT_RECTS) - {"ok"}
    w.undo_stack.undo()
    assert {a.name for a in doc.annots(0)} == set(ODD_ANNOT_RECTS)


# -- reload keeps the selection --------------------------------------------------------
@pytest.mark.parametrize("force_full", [False, True])
def test_reload_keeps_selection_by_name(window, annotated_pdf, force_full) -> None:
    from fixtures import ANNOT_TEXT_NAME

    w = window
    assert w.open_file(str(annotated_pdf))
    w.act_text_tool.trigger()
    dv = w.document_view
    doc = dv.document
    selection = dv.annot_selection
    selection.select(doc.annot(0, ANNOT_TEXT_NAME))
    old = selection.current
    changes: list[int] = []
    selection.changed.connect(lambda: changes.append(1))
    reloads: list[int] = []
    doc.reloaded.connect(lambda: reloads.append(1))

    doc.save(force_full=force_full)
    assert reloads == [1]
    current = selection.current
    assert current is not None and current.name == ANNOT_TEXT_NAME
    assert current.rect == old.rect and current.text == old.text
    assert selection.item is not None and selection.item.rect == old.rect
    # A full save renumbers xrefs: the snapshot follows (identity is the name).
    assert current == doc.annot(0, ANNOT_TEXT_NAME)
    assert bool(changes) == (current.xref != old.xref)
    # The selected annotation is still usable after the reload.
    assert w.text_tool.edit(current, rect=current.rect.translated(10, 0))
    assert doc.annot(0, ANNOT_TEXT_NAME).rect == old.rect.translated(10, 0)
    assert selection.current.rect == old.rect.translated(10, 0)


# -- save failure -------------------------------------------------------------------------
def _fail_write(path, data):
    raise PermissionError(13, "Access is denied", path)


def test_save_failure_keeps_annotations(window, word_form_pdf, monkeypatch) -> None:
    w = window
    assert w.open_file(str(word_form_pdf))
    w.act_stamp_check.trigger()
    dv = w.document_view
    doc = dv.document
    original = word_form_pdf.read_bytes()
    w.stamp_tools["check"].mouse_press(_event(0, QPointF(400, 600)))
    w.stamp_tools["check"].mouse_release(_event(0, QPointF(400, 600)))
    w.act_text_tool.trigger()
    w.text_tool.mouse_press(_event(0, QPointF(300, 700)))
    dv.annot_editor.editor.setPlainText("kept after failure")
    # The text box is still being typed when Save is pressed: it is committed first.

    offered: list[str] = []
    monkeypatch.setattr(document_module, "_write_atomically", _fail_write)
    monkeypatch.setattr(dialogs, "offer_save_as", lambda p, e: offered.append(e) or False)
    assert w.save() is False
    assert len(offered) == 1 and "denied" in offered[0]
    assert not dv.annot_editor.is_open
    assert [type(w.undo_stack.command(i)) for i in range(w.undo_stack.count())] == [
        AddAnnotCommand,
        AddAnnotCommand,
    ]
    assert w.undo_stack.index() == 2
    assert w.isWindowModified() and dv.is_dirty
    assert dv.document is doc and doc.is_open
    texts = sorted(a.text for a in doc.annots(0))
    assert texts == ["4", "kept after failure"]
    assert word_form_pdf.read_bytes() == original

    # The undo stack still works on the in-memory document.
    w.undo_stack.undo()
    assert [a.text for a in doc.annots(0)] == ["4"]
    w.undo_stack.redo()
    assert sorted(a.text for a in doc.annots(0)) == texts

    monkeypatch.undo()  # writable again: the retry (a full save) writes both
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    assert w.save()
    assert not w.isWindowModified()
    reopened = PdfDocument.open(str(word_form_pdf))
    try:
        assert sorted(a.text for a in reopened.annots(0)) == texts
    finally:
        reopened.close()
