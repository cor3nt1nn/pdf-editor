"""M4-T8 hardening: 100 signatures on a page, odd image stamps, save failure, reload and
Locked signatures."""

from __future__ import annotations

import time

import pymupdf
import pytest
from fixtures import (
    LOCKED_SIGNATURE_NAME,
    LOCKED_SIGNATURE_RECT,
    MANY_SIGNATURES,
    ODD_DCT_COLOR,
    ODD_MASK1_COLOR,
    ODD_STAMP_RECTS,
    SIGNED_NAME,
    many_signatures_name,
    many_signatures_rect,
)
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QMessageBox

import pdfeditor.core.document as document_module
from pdfeditor.core import annotations, signature
from pdfeditor.core.annotations import AnnotKind
from pdfeditor.core.commands import AddAnnotCommand, DeleteAnnotCommand, EditAnnotCommand
from pdfeditor.core.document import AnnotError, PdfDocument
from pdfeditor.ui import dialogs, signature_dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.base import ToolEvent

LEFT = Qt.MouseButton.LeftButton
NO_MOD = Qt.KeyboardModifier.NoModifier
ALT = Qt.KeyboardModifier.AltModifier

#: Same budgets as the M3 hardening (tests/test_annot_hardening.py): warm selection
#: refresh < 5 ms (cache only), cold re-read of the page < 250 ms, 500 hover moves < 2 s.
REFRESH_BUDGET_S = 0.005
COLD_REFRESH_BUDGET_S = 0.250
HOVER_BUDGET_S = 2.0


@pytest.fixture
def window(qtbot, qapp, settings, store_with_one, monkeypatch):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    monkeypatch.setattr(signature_dialogs, "import_signature", lambda *a, **k: None)
    w = MainWindow(settings, store_with_one)
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


def _click(qtbot, w: MainWindow, point: QPointF, modifier=NO_MOD) -> None:
    qtbot.mouseClick(w.page_view.viewport(), LEFT, modifier, _vp(w, 0, point))


def _centre(rect: tuple[float, float, float, float]) -> QPointF:
    x0, y0, x1, y1 = rect
    return QPointF((x0 + x1) / 2, (y0 + y1) / 2)


def _signatures(doc: PdfDocument, page: int = 0):
    return [a for a in doc.annots(page) if a.kind is AnnotKind.SIGNATURE]


def _pixel(image: QImage, scale: float, point: QPointF) -> QColor:
    return image.pixelColor(int(point.x() * scale), int(point.y() * scale))


def _close(color: QColor, rgb: tuple[int, int, int], tol: int = 24) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(color.getRgb()[:3], rgb, strict=True))


# -- performance: 100 signatures on one page --------------------------------------------
def test_many_signatures_are_cached(many_signatures_pdf, read_counter) -> None:
    doc = PdfDocument.open(str(many_signatures_pdf))
    try:
        start = time.perf_counter()
        first = doc.annots(0)
        cold = time.perf_counter() - start
        assert len(first) == MANY_SIGNATURES
        assert all(a.kind is AnnotKind.SIGNATURE and a.editable for a in first)
        assert len({a.image_xref for a in first}) == 1
        assert read_counter == [0]
        for n in (0, 49, MANY_SIGNATURES - 1):
            assert doc.annot(0, many_signatures_name(n)) is not None
        assert doc.annots(0) == first
        assert read_counter == [0]
        print(f"cold annots(0), {MANY_SIGNATURES} signatures: {cold * 1000:.1f} ms")
        assert cold < COLD_REFRESH_BUDGET_S
        assert not doc.render(0, 1.0).isNull()
    finally:
        doc.close()


def test_many_signatures_hover_and_selection_are_fast(
    window, many_signatures_pdf, read_counter
) -> None:
    w = window
    assert w.open_file(str(many_signatures_pdf))
    w.activate_signature_tool()
    tool = w.signature_tool
    assert w.tool_manager.active_tool is tool
    doc = w.document_view.document
    selection = w.document_view.annot_selection

    doc.annots(0)
    reads = len(read_counter)
    start = time.perf_counter()
    for n in range(500):
        tool.mouse_move(_event(0, QPointF(20 + n % 560, 30 + n % 780), Qt.MouseButton.NoButton))
    hover = time.perf_counter() - start
    assert len(read_counter) == reads  # hovering never re-reads the page
    target = MANY_SIGNATURES // 2
    hit = tool.annot_at(0, _centre(many_signatures_rect(target)))
    assert hit is not None and hit.name == many_signatures_name(target)

    name = many_signatures_name(MANY_SIGNATURES - 1)
    selection.select(doc.annot(0, name))
    start = time.perf_counter()
    for _ in range(10):
        selection._on_page_changed(0)
    warm = (time.perf_counter() - start) / 10
    assert len(read_counter) == reads
    assert selection.current.name == name

    doc._annot_cache.clear()
    start = time.perf_counter()
    selection._on_page_changed(0)
    cold = time.perf_counter() - start
    assert len(read_counter) == reads + 1
    assert selection.current.name == name
    print(
        f"{MANY_SIGNATURES} signatures: 500 hover moves {hover * 1000:.1f} ms, selection "
        f"refresh warm {warm * 1000:.2f} ms, cold {cold * 1000:.1f} ms"
    )
    assert hover < HOVER_BUDGET_S
    assert warm < REFRESH_BUDGET_S
    assert cold < COLD_REFRESH_BUDGET_S

    # Moving one of them re-reads the page once and keeps the selection.
    info = selection.current
    assert tool.edit(info, rect=info.rect.translated(3, 0))
    assert selection.current.name == name
    assert selection.current.rect == info.rect.translated(3, 0)
    assert len(_signatures(doc)) == MANY_SIGNATURES


# -- odd image stamps ---------------------------------------------------------------------
def test_odd_stamps_are_read_without_crash(odd_stamps_pdf) -> None:
    doc = PdfDocument.open(str(odd_stamps_pdf))
    try:
        infos = {a.name: a for a in doc.annots(0)}
        # The text stamp with /IT /StampImage has no image: not a signature, skipped.
        assert set(infos) == set(ODD_STAMP_RECTS) - {"no_image"}
        assert all(a.kind is AnnotKind.SIGNATURE for a in infos.values())
        # Any image MuPDF decodes is editable (Deviation 33).
        for name in ("ok", "dct", "mask1"):
            assert infos[name].editable, name
            assert infos[name].rect == QRectF(*ODD_STAMP_RECTS[name][:2], 200, 80), name
        assert infos["dct"].image_size == (200, 80)
        # No/huge /Rect: listed with an empty rect, never editable.
        for name in ("no_rect", "huge"):
            assert infos[name].rect.isEmpty() and not infos[name].editable, name
        # An image without /Width: locked.
        assert infos["no_width"].locked and not infos["no_width"].editable
        with pytest.raises(AnnotError):
            doc.update_annot(0, "no_width", rect=QRectF(10, 10, 100, 40))

        scale = 2.0
        page = doc.render(0, scale)
        assert not page.isNull()
        assert not doc.render(0, 2.0, QRectF(0, 0, 300, 300)).isNull()
        assert _close(_pixel(page, scale, _centre(ODD_STAMP_RECTS["dct"])), ODD_DCT_COLOR)
        x0, y0, x1, y1 = ODD_STAMP_RECTS["mask1"]
        top = QPointF((x0 + x1) / 2, y0 + (y1 - y0) * 0.25)
        bottom = QPointF((x0 + x1) / 2, y0 + (y1 - y0) * 0.75)
        assert _close(_pixel(page, scale, top), ODD_MASK1_COLOR)
        assert _close(_pixel(page, scale, bottom), (255, 255, 255))  # 1-bit mask: clear
        doc.page_shapes(0)  # the snapping scan copes with them

        dct = doc.annot_image(0, "dct")
        assert (dct.width, dct.height) == (200, 80)
        assert set(dct.alpha) == {255}
        mask1 = doc.annot_image(0, "mask1")
        assert mask1.alpha[: 200 * 40] == b"\xff" * (200 * 40)
        assert mask1.alpha[200 * 40 :] == b"\x00" * (200 * 40)

        # Like a FreeText (M3), core does not refuse a rect for a rect-less signature
        # (the UI never targets it): giving it one repairs it.
        repaired = doc.update_annot(0, "no_rect", rect=QRectF(300, 450, 200, 80))
        assert repaired.editable and repaired.rect == QRectF(300, 450, 200, 80)
        assert not doc.render(0, 1.0).isNull()
    finally:
        doc.close()


@pytest.mark.parametrize("name", ["dct", "mask1"])
def test_odd_image_stamp_delete_undo_recreates_as_rgb_smask(qapp, odd_stamps_pdf, name) -> None:
    doc = PdfDocument.open(str(odd_stamps_pdf))
    try:
        before = doc.annot_image(0, name)
        info = doc.annot(0, name)
        move = EditAnnotCommand(doc, info, rect=info.rect.translated(0, 400))
        move.apply_now()
        assert doc.annot(0, name).rect == info.rect.translated(0, 400)
        move.undo()
        delete = DeleteAnnotCommand(doc, doc.annot(0, name))
        delete.apply_now()
        assert doc.annot(0, name) is None
        delete.undo()
        back = doc.annot(0, name)
        assert back is not None and back.editable and back.rect == info.rect
        assert signature.image_supported(doc.fitz, back.image_xref)  # our RGB + SMask
        assert doc.annot_image(0, name) == before
        doc.save(force_full=True)
        assert doc.annot_image(0, name) == before
        assert not doc.render(0, 1.0).isNull()
    finally:
        doc.close()


def test_odd_stamps_are_not_selectable(qtbot, window, odd_stamps_pdf) -> None:
    w = window
    assert w.open_file(str(odd_stamps_pdf))
    w.activate_signature_tool()
    tool = w.signature_tool
    doc = w.document_view.document
    selection = w.document_view.annot_selection

    # Hover anywhere (missing /Rect, huge coordinates) without a crash.
    for y in range(20, 820, 40):
        for x in range(20, 580, 80):
            tool.mouse_move(_event(0, QPointF(x, y), Qt.MouseButton.NoButton))
    for name in ("ok", "dct", "mask1"):
        assert tool.annot_at(0, _centre(ODD_STAMP_RECTS[name])).name == name
    for name in ("no_image", "no_rect", "huge", "no_width"):
        assert tool.annot_at(0, _centre(ODD_STAMP_RECTS[name])) is None, name

    # A click where the unusable "no_width" stamp sits places a new signature instead.
    _click(qtbot, w, _centre(ODD_STAMP_RECTS["no_width"]), ALT)
    assert w.undo_stack.count() == 1
    assert isinstance(w.undo_stack.command(0), AddAnnotCommand)
    current = selection.current
    assert current is not None and current.name not in ODD_STAMP_RECTS
    assert doc.annot(0, "no_width").locked

    # The decodable odd ones are selectable and movable.
    _click(qtbot, w, _centre(ODD_STAMP_RECTS["dct"]))
    assert selection.current is not None and selection.current.name == "dct"
    assert tool.delete_selection()
    assert doc.annot(0, "dct") is None
    w.undo_stack.undo()
    assert doc.annot(0, "dct").editable


# -- Locked signature ---------------------------------------------------------------------
def test_locked_signature_is_not_selectable(qtbot, window, signed_pdf) -> None:
    w = window
    assert w.open_file(str(signed_pdf))
    doc = w.document_view.document
    selection = w.document_view.annot_selection
    locked = doc.annot(0, LOCKED_SIGNATURE_NAME)
    assert locked is not None and locked.locked and not locked.editable
    centre = _centre(LOCKED_SIGNATURE_RECT)

    for tool in (w.text_tool, w.stamp_tools["check"], w.signature_tool):
        w.tool_manager.set_active(tool.name)
        assert tool.annot_at(0, centre) is None, tool.name
        tool.mouse_move(_event(0, centre, Qt.MouseButton.NoButton))
    selection.select(doc.annot(0, SIGNED_NAME))
    assert selection.current.name == SIGNED_NAME

    # With the signature tool a click on it places a new signature on top.
    w.activate_signature_tool()
    _click(qtbot, w, centre, ALT)
    assert w.undo_stack.count() == 1
    assert selection.current is not None and selection.current.name != LOCKED_SIGNATURE_NAME
    assert doc.annot(0, LOCKED_SIGNATURE_NAME) == locked

    # A selection can never be forced onto it: the next refresh drops it.
    selection.select(locked)
    doc.page_changed.emit(0)
    assert selection.current is None
    for command in (
        EditAnnotCommand(doc, locked, rect=locked.rect.translated(5, 5)),
        DeleteAnnotCommand(doc, locked),
    ):
        with pytest.raises(AnnotError):
            command.apply_now()
    assert doc.annot(0, LOCKED_SIGNATURE_NAME) == locked


# -- reload keeps the selection -------------------------------------------------------------
@pytest.mark.parametrize("force_full", [False, True])
def test_reload_keeps_signature_selection_by_name(window, signed_pdf, force_full) -> None:
    w = window
    assert w.open_file(str(signed_pdf))
    w.activate_signature_tool()
    dv = w.document_view
    doc = dv.document
    selection = dv.annot_selection
    selection.select(doc.annot(0, SIGNED_NAME))
    old = selection.current
    image = doc.annot_image(0, SIGNED_NAME)
    reloads: list[int] = []
    doc.reloaded.connect(lambda: reloads.append(1))

    doc.save(force_full=force_full)
    assert reloads == [1]
    current = selection.current
    assert current is not None and current.name == SIGNED_NAME
    assert current.rect == old.rect and current.image_size == old.image_size
    assert current == doc.annot(0, SIGNED_NAME)  # xrefs may be renumbered by a full save
    assert selection.item is not None and selection.item.rect == old.rect
    assert doc.annot_image(0, SIGNED_NAME) == image
    # Still usable after the reload: move it.
    assert w.signature_tool.edit(current, rect=current.rect.translated(10, 0))
    assert doc.annot(0, SIGNED_NAME).rect == old.rect.translated(10, 0)
    assert selection.current.rect == old.rect.translated(10, 0)


# -- save failure -----------------------------------------------------------------------------
def _fail_write(path, data):
    raise PermissionError(13, "Access is denied", path)


def test_save_failure_keeps_signatures(qtbot, window, word_form_pdf, monkeypatch) -> None:
    w = window
    assert w.open_file(str(word_form_pdf))
    w.activate_signature_tool()
    dv = w.document_view
    doc = dv.document
    original = word_form_pdf.read_bytes()
    _click(qtbot, w, QPointF(400, 600), ALT)
    _click(qtbot, w, QPointF(400, 720), ALT)
    placed = _signatures(doc)
    assert len(placed) == 2
    images = [doc.annot_image(0, a.name) for a in placed]

    offered: list[str] = []
    monkeypatch.setattr(document_module, "_write_atomically", _fail_write)
    monkeypatch.setattr(dialogs, "offer_save_as", lambda p, e: offered.append(e) or False)
    assert w.save() is False
    assert len(offered) == 1 and "denied" in offered[0]
    assert [type(w.undo_stack.command(i)) for i in range(w.undo_stack.count())] == [
        AddAnnotCommand,
        AddAnnotCommand,
    ]
    assert w.isWindowModified() and dv.is_dirty
    assert dv.document is doc and doc.is_open
    assert _signatures(doc) == placed
    assert [doc.annot_image(0, a.name) for a in placed] == images
    assert word_form_pdf.read_bytes() == original
    assert not doc.render(0, 1.0).isNull()

    # Undo/redo still work on the in-memory document.
    w.undo_stack.undo()
    assert [a.name for a in _signatures(doc)] == [placed[0].name]
    w.undo_stack.redo()
    assert [a.name for a in _signatures(doc)] == [a.name for a in placed]

    monkeypatch.undo()  # writable again: the retry (a full save) writes both
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    assert w.save()
    assert not w.isWindowModified()
    reopened = PdfDocument.open(str(word_form_pdf))
    try:
        again = _signatures(reopened)
        assert [a.name for a in again] == [a.name for a in placed]
        assert [a.rect for a in again] == [a.rect for a in placed]
        assert [reopened.annot_image(0, a.name) for a in again] == images
        assert len({a.image_xref for a in again}) == 1  # still one shared image
    finally:
        reopened.close()
    with pymupdf.open(str(word_form_pdf)) as raw:
        assert raw.is_pdf and raw.page_count == 1
