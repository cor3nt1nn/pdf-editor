"""M6b-T12: Select Text and markup tools, text selection and copy (docs/M6_PLAN.md D11).

The tools run in a bare ``DocumentView`` with their own ``ToolManager`` (the
``MainWindow`` wiring is tested in ``test_markup_main_window.py``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fixtures
import pymupdf
import pytest
from fixtures import MARKED_HIGHLIGHT_NAME, TEXT_LINES
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtWidgets import QApplication

from pdfeditor.core.annotations import AnnotKind, AnnotSpec
from pdfeditor.core.commands import (
    AddAnnotCommand,
    DeletePagesCommand,
    EditAnnotCommand,
    MovePagesCommand,
    RotatePagesCommand,
)
from pdfeditor.core.settings import Settings
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.overlays.text_selection import TextSelection
from pdfeditor.ui.tools.annot_tools import TextTool
from pdfeditor.ui.tools.base import ToolManager
from pdfeditor.ui.tools.hand_tool import HandTool
from pdfeditor.ui.tools.markup_tools import (
    TOOL_KINDS,
    MarkupTool,
    TextSelectTool,
    markup_text,
    no_text_message,
    selected_text,
)

NO_MOD = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
LEFT = Qt.MouseButton.LeftButton
LINE_1 = TEXT_LINES[0][0]  # "The quick brown fox jumps over the lazy dog."
QUICK = LINE_1.index("quick")
BROWN_END = LINE_1.index("brown") + len("brown") - 1


@dataclass
class Harness:
    view: DocumentView
    tools: ToolManager
    select: TextSelectTool
    markups: dict[AnnotKind, MarkupTool]
    text_tool: TextTool
    settings: Settings
    messages: list[str] = field(default_factory=list)

    @property
    def doc(self):
        return self.view.document

    @property
    def stack(self):
        return self.view.undo_stack

    @property
    def sel(self) -> TextSelection:
        return self.view.text_selection

    def open(self, path: Path) -> None:
        self.view.open(str(path))

    def use(self, name: str) -> None:
        self.tools.set_active(name)


@pytest.fixture
def h(qtbot, settings):
    view = DocumentView()
    qtbot.addWidget(view)
    view.resize(900, 700)
    view.show()
    qtbot.waitExposed(view)
    tools = ToolManager(view.page_view, view)
    tools.register(HandTool(view))
    select = TextSelectTool(view, view)
    tools.register(select)
    markups = {kind: MarkupTool(view, settings, kind, view) for kind in TOOL_KINDS}
    for tool in markups.values():
        tools.register(tool)
    text_tool = TextTool(view, settings, view)
    tools.register(text_tool)
    harness = Harness(view, tools, select, markups, text_tool, settings)
    for tool in (select, *markups.values(), text_tool):
        tool.message.connect(harness.messages.append)
    yield harness
    view.undo_stack.setClean()
    view.close_document()


@pytest.fixture
def text_h(h, tmp_path):
    h.open(fixtures.make_text_pdf(tmp_path / "text.pdf"))
    return h


def _vp(h: Harness, page: int, point: QPointF) -> QPoint:
    pv = h.view.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _char(h: Harness, index: int, page: int = 0) -> QPointF:
    return h.doc.page_text(page).chars[index].bbox.center()


def _drag(qtbot, h: Harness, a: QPointF, b: QPointF, *, page: int = 0, mods=NO_MOD) -> None:
    vp = h.view.page_view.viewport()
    start, end = _vp(h, page, a), _vp(h, page, b)
    qtbot.mousePress(vp, LEFT, mods, start)
    for k in (1, 2, 3, 4):
        qtbot.mouseMove(vp, start + (end - start) * k / 4)
    qtbot.mouseRelease(vp, LEFT, mods, end)


def _click(qtbot, h: Harness, p: QPointF, *, page: int = 0, mods=NO_MOD) -> None:
    qtbot.mouseClick(h.view.page_view.viewport(), LEFT, mods, _vp(h, page, p))


def _dclick(qtbot, h: Harness, p: QPointF, page: int = 0) -> None:
    """A real double-click sequence: press, release, double-click, release."""
    vp = h.view.page_view.viewport()
    pos = _vp(h, page, p)
    qtbot.mouseClick(vp, LEFT, NO_MOD, pos)
    qtbot.mouseDClick(vp, LEFT, NO_MOD, pos)


def _markups(h: Harness, page: int = 0):
    return [a for a in h.doc.annots(page) if a.is_markup]


# -- Select Text tool -------------------------------------------------------------------
def test_select_tool_drag_selects_in_reading_order(qtbot, text_h) -> None:
    h = text_h
    h.use("select_text")
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    assert h.sel.text() == "quick brown"
    assert h.sel.page == 0
    assert h.stack.count() == 0  # selecting never changes the document
    # Backwards gives the same range.
    _drag(qtbot, h, _char(h, BROWN_END), _char(h, QUICK))
    assert h.sel.text() == "quick brown"


def test_drag_across_lines_and_from_the_margin(qtbot, text_h) -> None:
    h = text_h
    h.use("select_text")
    second = len(LINE_1)  # first char of line 2
    _drag(qtbot, h, _char(h, LINE_1.index("lazy")), _char(h, second + 5))
    assert h.sel.text() == "lazy dog.\nSecond"
    assert len(h.sel.quads()) == 2
    # From the left margin (no char under the press) to "quick": the line from its start.
    margin = QPointF(30, _char(h, 0).y())
    _drag(qtbot, h, margin, _char(h, QUICK + 4))
    assert h.sel.text() == "The quick"
    # Leaving the page: the nearest char of the nearest line.
    _drag(qtbot, h, _char(h, QUICK), QPointF(-20, _char(h, QUICK).y()))
    assert h.sel.text() == "The q"
    _drag(qtbot, h, _char(h, QUICK), QPointF(_char(h, QUICK).x(), -20))
    assert h.sel.text() == "q"


def test_click_clears_and_escape_clears(qtbot, text_h) -> None:
    h = text_h
    h.use("select_text")
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    assert not h.sel.is_empty
    _click(qtbot, h, _char(h, 20))
    assert h.sel.is_empty
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    qtbot.keyClick(h.view.page_view, Qt.Key.Key_Escape)
    assert h.sel.is_empty


def test_double_and_triple_click(qtbot, text_h) -> None:
    h = text_h
    h.use("select_text")
    brown = _char(h, LINE_1.index("brown") + 2)
    _dclick(qtbot, h, brown)
    assert h.sel.text() == "brown"
    # A third press right after the double-click selects the line.
    _click(qtbot, h, brown)
    assert h.sel.text() == LINE_1
    # Not a triple-click once the interval has passed.
    _dclick(qtbot, h, brown)
    h.select._last_double = (0.0, h.select._last_double[1], 0)
    _click(qtbot, h, brown)
    assert h.sel.is_empty


def test_shift_click_and_shift_drag_extend(qtbot, text_h) -> None:
    h = text_h
    h.use("select_text")
    _drag(qtbot, h, _char(h, QUICK), _char(h, QUICK + 4))
    assert h.sel.text() == "quick"
    _click(qtbot, h, _char(h, LINE_1.index("fox")), mods=SHIFT)
    assert h.sel.text() == "quick brown f"
    _drag(qtbot, h, _char(h, 30), _char(h, LINE_1.index("over")), mods=SHIFT)
    assert h.sel.text() == "quick brown fox jumps o"


def test_scanned_page_says_no_text(qtbot, h, tmp_path) -> None:
    h.open(fixtures.make_scanned_pdf(tmp_path / "scan.pdf"))
    h.use("select_text")
    _drag(qtbot, h, QPointF(100, 100), QPointF(300, 200))
    assert h.sel.is_empty
    assert h.messages[-1] == no_text_message()
    h.messages.clear()
    h.use("highlight")
    _drag(qtbot, h, QPointF(100, 100), QPointF(300, 200))
    assert h.stack.count() == 0
    assert h.messages == [no_text_message()]


def test_invisible_text_is_selectable_and_copied(qtbot, h, tmp_path) -> None:
    path = tmp_path / "ocr.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Invisible words here", fontsize=12, render_mode=3)
    doc.save(path)
    doc.close()
    h.open(path)
    h.use("select_text")
    pt = h.doc.page_text(0)
    assert pt.is_invisible(0)
    _drag(qtbot, h, _char(h, 0), _char(h, len("Invisible words") - 1))
    assert selected_text(h.view) == "Invisible words"
    h.use("highlight")
    _drag(qtbot, h, _char(h, 0), _char(h, len("Invisible") - 1))
    (info,) = _markups(h)
    assert markup_text(h.doc, info) == "Invisible"


def test_selection_paints_translucent_quads(qtbot, text_h) -> None:
    h = text_h
    h.use("select_text")
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    pv = h.view.page_view
    centre = _vp(h, 0, _char(h, QUICK + 2))
    blank = _vp(h, 0, QPointF(_char(h, QUICK + 2).x(), 300))
    image = pv.viewport().grab().toImage()
    sel_pixel = image.pixelColor(centre)
    assert sel_pixel.blue() > sel_pixel.red() + 20 or sel_pixel.blue() > 200
    h.sel.clear()
    qtbot.wait(10)
    image2 = pv.viewport().grab().toImage()
    assert image2.pixelColor(centre) != sel_pixel
    assert image2.pixelColor(blank) == image.pixelColor(blank)


# -- markup tools -------------------------------------------------------------------------
def test_highlight_drag_pushes_one_command(qtbot, text_h) -> None:
    h = text_h
    h.use("highlight")
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    assert h.stack.count() == 1
    command = h.stack.command(0)
    assert isinstance(command, AddAnnotCommand)
    assert command.text() == "Add highlight"
    (info,) = _markups(h)
    assert info.kind is AnnotKind.HIGHLIGHT
    assert markup_text(h.doc, info) == "quick brown"
    assert info.color == pytest.approx((1.0, 1.0, 0.0))
    # The new markup is selected and the text selection cleared.
    assert h.view.annot_selection.current.name == info.name
    assert h.sel.is_empty
    assert selected_text(h.view) == "quick brown"
    h.view.undo()
    assert _markups(h) == []
    h.view.redo()
    assert len(_markups(h)) == 1


@pytest.mark.parametrize(
    ("name", "kind", "label", "color"),
    [
        ("underline", AnnotKind.UNDERLINE, "Add underline", (1.0, 0.0, 0.0)),
        ("strikeout", AnnotKind.STRIKEOUT, "Add strike-through", (1.0, 0.0, 0.0)),
    ],
)
def test_underline_and_strikeout(qtbot, text_h, name, kind, label, color) -> None:
    h = text_h
    h.use(name)
    second = len(LINE_1)
    _drag(qtbot, h, _char(h, LINE_1.index("lazy")), _char(h, second + 5))
    (info,) = _markups(h)
    assert info.kind is kind
    assert h.stack.command(0).text() == label
    assert info.color == pytest.approx(color)
    assert len(info.quads) == 2
    assert markup_text(h.doc, info) == "lazy dog.\nSecond"


def test_markup_double_and_triple_click(qtbot, text_h) -> None:
    h = text_h
    h.use("highlight")
    brown = _char(h, LINE_1.index("brown") + 2)
    _dclick(qtbot, h, brown)
    assert h.stack.count() == 0  # waits for a possible third click
    assert h.sel.text() == "brown"
    qtbot.waitUntil(lambda: h.stack.count() == 1, timeout=QApplication.doubleClickInterval() + 2000)
    (info,) = _markups(h)
    assert markup_text(h.doc, info) == "brown"
    # Triple-click on line 3: one markup over the whole line.
    third = len(LINE_1) + len(TEXT_LINES[1][0])
    p = _char(h, third + 3)
    _dclick(qtbot, h, p)
    _click(qtbot, h, p)
    assert h.stack.count() == 2
    assert markup_text(h.doc, _markups(h)[-1]) == TEXT_LINES[2][0]
    qtbot.wait(QApplication.doubleClickInterval() + 100)
    assert h.stack.count() == 2  # nothing else marked later


def test_switching_tools_marks_a_pending_word_and_clears_the_selection(qtbot, text_h) -> None:
    h = text_h
    h.use("highlight")
    _dclick(qtbot, h, _char(h, LINE_1.index("fox")))
    h.use("select_text")
    assert h.stack.count() == 1
    assert markup_text(h.doc, _markups(h)[0]) == "fox"
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    assert not h.sel.is_empty
    h.use("underline")
    assert h.sel.is_empty
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    h.use("hand")
    assert h.sel.is_empty
    assert h.view.annot_selection.current is None


def test_drag_without_text_says_so(qtbot, text_h) -> None:
    h = text_h
    h.use("highlight")
    _drag(qtbot, h, QPointF(300, 500), QPointF(400, 560))
    assert h.stack.count() == 0
    assert h.messages == [no_text_message()]
    # A plain click on text marks nothing and says nothing.
    h.messages.clear()
    _click(qtbot, h, _char(h, QUICK))
    assert h.stack.count() == 0
    assert h.messages == []


def test_markup_on_rotated_cropped_page(qtbot, h, tmp_path) -> None:
    h.open(fixtures.make_text_pdf(tmp_path / "rot.pdf", rotate=90, cropbox=True))
    h.use("highlight")
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    (info,) = _markups(h)
    assert markup_text(h.doc, info) == "quick brown"
    pt = h.doc.page_text(0)
    (expected,) = pt.range_quads(QUICK, BROWN_END)
    (quad,) = info.quads
    for a, b in zip(quad, expected, strict=True):
        assert a.x() == pytest.approx(b.x(), abs=0.01)
        assert a.y() == pytest.approx(b.y(), abs=0.01)


def test_click_selects_existing_markup_delete_and_escape(qtbot, h, tmp_path) -> None:
    h.open(fixtures.make_marked_pdf(tmp_path / "marked.pdf"))
    h.use("underline")
    _click(qtbot, h, QPointF(120, 96))  # inside the highlight's first quad
    current = h.view.annot_selection.current
    assert current is not None and current.name == MARKED_HIGHLIGHT_NAME
    assert h.stack.count() == 0
    assert selected_text(h.view) == markup_text(h.doc, current)
    assert "quick" in selected_text(h.view)
    qtbot.keyClick(h.view.page_view, Qt.Key.Key_Escape)
    assert h.view.annot_selection.current is None
    _click(qtbot, h, QPointF(120, 96))
    qtbot.keyClick(h.view.page_view, Qt.Key.Key_Delete)
    assert h.stack.count() == 1
    assert h.stack.command(0).text() == "Delete annotation"
    assert all(a.name != MARKED_HIGHLIGHT_NAME for a in h.doc.annots(0))


def test_drag_from_a_markup_selects_text_instead_of_moving_it(qtbot, h, tmp_path) -> None:
    h.open(fixtures.make_marked_pdf(tmp_path / "marked.pdf"))
    before = h.doc.annot(0, MARKED_HIGHLIGHT_NAME)
    h.use("strikeout")
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    after = h.doc.annot(0, MARKED_HIGHLIGHT_NAME)
    assert [q.bounding_rect() for q in after.quads] == [q.bounding_rect() for q in before.quads]
    assert h.stack.count() == 1
    assert h.stack.command(0).text() == "Add strike-through"
    new = [
        a for a in _markups(h) if a.kind is AnnotKind.STRIKEOUT and "quick" in markup_text(h.doc, a)
    ]
    assert len(new) == 1


# -- colours ------------------------------------------------------------------------------
def test_color_button_sets_kind_default_and_recolors_selection(qtbot, text_h) -> None:
    h = text_h
    s = h.settings
    tool = h.markups[AnnotKind.HIGHLIGHT]
    h.use("highlight")
    annot_color = s.annot_color
    tool.apply_style(color=(0.0, 1.0, 1.0))  # nothing selected: the default only
    assert s.markup_highlight_color == "#00ffff"
    assert s.markup_underline_color == "#ff0000"
    assert s.annot_color == annot_color
    assert h.stack.count() == 0
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    (info,) = _markups(h)
    assert info.color == pytest.approx((0.0, 1.0, 1.0))
    # Recolour the selected markup: one "Change markup color" step, default follows.
    tool.apply_style(color=(1.0, 0.5, 0.0))
    assert h.stack.count() == 2
    command = h.stack.command(1)
    assert isinstance(command, EditAnnotCommand)
    assert command.text() == "Change markup color"
    assert h.doc.annot(0, info.name).color == pytest.approx((1.0, 0.5, 0.0), abs=0.01)
    assert s.markup_highlight_color == "#ff8000"
    # The font size is inert on a markup.
    font_size = s.annot_font_size
    tool.apply_style(font_size=30.0)
    assert h.stack.count() == 2
    assert s.annot_font_size == font_size
    # Same colour again: nothing pushed.
    tool.apply_style(color=(1.0, 0.5, 0.0))
    assert h.stack.count() == 2


def test_text_tool_recolors_a_selected_markup_without_touching_text_defaults(
    qtbot, h, tmp_path
) -> None:
    h.open(fixtures.make_marked_pdf(tmp_path / "marked.pdf"))
    s = h.settings
    s.annot_color = "#123456"
    h.use("text")
    _click(qtbot, h, QPointF(120, 96))
    assert h.view.annot_selection.current.name == MARKED_HIGHLIGHT_NAME
    h.text_tool.apply_style(color=(0.0, 0.0, 1.0))
    assert s.annot_color == "#123456"
    assert s.markup_highlight_color == "#0000ff"
    assert h.doc.annot(0, MARKED_HIGHLIGHT_NAME).color == pytest.approx((0.0, 0.0, 1.0))
    assert h.stack.command(0).text() == "Change markup color"


def test_squiggly_recolor_keeps_tool_defaults(qtbot, h, tmp_path) -> None:
    h.open(fixtures.make_marked_pdf(tmp_path / "marked.pdf"))
    s = h.settings
    h.use("highlight")
    squiggly = next(a for a in h.doc.annots(0) if a.kind is AnnotKind.SQUIGGLY)
    _click(qtbot, h, squiggly.quads[0].bounding_rect().center())
    assert h.view.annot_selection.current.kind is AnnotKind.SQUIGGLY
    h.markups[AnnotKind.HIGHLIGHT].apply_style(color=(0.0, 0.0, 0.0))
    assert s.markup_highlight_color == "#ffff00"
    assert h.doc.annot(0, squiggly.name).color == pytest.approx((0.0, 0.0, 0.0))


# -- selection life cycle -----------------------------------------------------------------
def test_selection_survives_other_changes_and_follows_its_page(qtbot, h, tmp_path) -> None:
    h.open(fixtures.make_marked_pdf(tmp_path / "marked.pdf"))
    doc = h.doc
    h.use("select_text")
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    assert h.sel.text() == "quick brown"
    h.view.push(RotatePagesCommand(doc, [1], 90))  # another page
    assert h.sel.text() == "quick brown"
    spec = AnnotSpec(0, AnnotKind.TEXT, "note", 11.0, (0.0, 0.0, 0.0), QRectF(300, 500, 100, 20))
    command = AddAnnotCommand(doc, spec)
    command.apply_now()
    h.view.push(command)  # its own page, but not its text
    assert h.sel.text() == "quick brown"
    h.view.push(RotatePagesCommand(doc, [0], 90))  # its own page turns: still the text
    assert h.sel.text() == "quick brown"
    command = MovePagesCommand(doc, [0], 2)
    command.apply_now()
    h.view.push(command)
    assert h.sel.page == 1
    assert h.sel.text() == "quick brown"
    h.view.undo()
    assert h.sel.page == 0
    command = DeletePagesCommand(doc, [0])
    command.apply_now()
    h.view.push(command)
    assert h.sel.is_empty


def test_selection_survives_a_save(qtbot, h, tmp_path) -> None:
    h.open(fixtures.make_text_pdf(tmp_path / "text.pdf"))
    h.use("select_text")
    _drag(qtbot, h, _char(h, QUICK), _char(h, BROWN_END))
    h.view.save_as(str(tmp_path / "copy.pdf"))  # reloads the document
    assert h.sel.text() == "quick brown"
    assert selected_text(h.view) == "quick brown"


def test_text_selection_model(qtbot, text_h) -> None:
    h = text_h
    sel = h.sel
    changes = []
    sel.changed.connect(lambda: changes.append(1))
    with pytest.raises(IndexError):
        sel.set(0, 0, 10_000)
    with pytest.raises(IndexError):
        sel.set(3, 0, 1)
    sel.set(0, BROWN_END, QUICK)
    assert sel.text() == "quick brown"
    assert sel.anchor.index == BROWN_END and sel.focus.index == QUICK
    assert [r.index for r in sel.refs()] == list(range(QUICK, BROWN_END + 1))
    assert changes == [1]
    sel.set(0, BROWN_END, QUICK)  # unchanged: no signal
    assert changes == [1]
    sel.clear()
    sel.clear()
    assert changes == [1, 1]
    assert sel.text() == "" and sel.quads() == [] and sel.refs() == []
    h.view.close_document()
    assert sel.document is None


def test_markup_tool_rejects_other_kinds(h) -> None:
    with pytest.raises(ValueError):
        MarkupTool(h.view, h.settings, AnnotKind.SQUIGGLY)
    with pytest.raises(ValueError):
        MarkupTool(h.view, h.settings, AnnotKind.TEXT)
