"""M7-T6: hover frames and the floating run editor of the Edit Page Text tool."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtWidgets import QLineEdit
from textedit_fixtures import (
    BODY_SIZE,
    LINE1,
    make_ocr_pdf,
    make_text_edit_pdf,
    make_xobject_text_pdf,
    needs_text_fonts,
)

from pdfeditor.constants import ZoomMode
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.pagetext import CharRef
from pdfeditor.core.textedit import Run
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.overlays.textedit_editor import (
    TextRunAnchor,
    TextRunEditor,
    display_text,
    run_rect,
    span_family,
)
from pdfeditor.ui.overlays.textedit_items import (
    HOVER_Z,
    TextHoverItem,
    clamp_to_span,
    selection_run,
)

pytestmark = needs_text_fonts

JEAN = LINE1.index("Jean")
JEAN_RUN = Run(JEAN, JEAN + 3)


@pytest.fixture
def dv(qtbot):
    view = DocumentView()
    qtbot.addWidget(view)
    view.resize(800, 600)
    view.show()
    qtbot.waitExposed(view)
    view.activateWindow()
    yield view
    view.undo_stack.setClean()
    view.shutdown()


class Recorder:
    def __init__(self, editor: TextRunEditor) -> None:
        self.runs: list[tuple[int, Run, str]] = []
        self.anchors: list[tuple[TextRunAnchor, str]] = []
        self.cancelled = 0
        editor.run_committed.connect(lambda page, run, text: self.runs.append((page, run, text)))
        editor.committed.connect(lambda anchor, text: self.anchors.append((anchor, text)))
        editor.cancelled.connect(self._on_cancelled)

    def _on_cancelled(self) -> None:
        self.cancelled += 1


@pytest.fixture
def setup(qtbot, dv, tmp_path):
    doc = dv.open(str(make_text_edit_pdf(tmp_path / "edit.pdf")))
    dv.page_view.set_zoom_mode(ZoomMode.CUSTOM)
    dv.page_view.set_zoom_percent(100)
    editor = dv.textedit_editor
    return dv, doc, editor, Recorder(editor)


def _open(qtbot, editor: TextRunEditor) -> QLineEdit:
    editor.open(0, JEAN_RUN)
    widget = editor.editor
    assert isinstance(widget, QLineEdit)
    qtbot.waitUntil(widget.hasFocus)
    return widget


def _assert_geom(editor: TextRunEditor, view, page: int, rect: QRectF) -> None:
    expected = view.page_rect_to_viewport(page, rect)
    got = editor.editor.geometry()
    assert abs(got.x() - expected.x()) <= 2
    assert abs(got.y() - expected.y()) <= 2
    assert abs(got.height() - expected.height()) <= 2
    assert got.width() >= expected.width() - 2


def _line_center(doc: PdfDocument, word: str = "Jean") -> QPointF:
    pt = doc.page_text(0)
    i = pt.text.index(word) if word in pt.text else LINE1.index(word)
    return pt.chars[i + 1].bbox.center()


# -- hover ----------------------------------------------------------------------
def test_hover_frames_line_and_word(qtbot, setup) -> None:
    dv, doc, _editor, _rec = setup
    view = dv.page_view
    target = dv.text_hover.hover(0, _line_center(doc))
    assert target is not None
    pt = doc.page_text(0)
    assert target.word == JEAN_RUN
    item = dv.text_hover.item
    assert isinstance(item, TextHoverItem)
    assert item.parentItem() is view.page_item(0)
    assert item.zValue() == HOVER_Z
    line = pt.lines[0].bbox
    got = view.mapFromScene(item.mapToScene(item.line_polygon())).boundingRect()
    expected = view.page_rect_to_viewport(0, line)
    for a, b in (
        (got.left(), expected.left()),
        (got.top(), expected.top()),
        (got.right(), expected.right()),
        (got.bottom(), expected.bottom()),
    ):
        assert abs(a - b) <= 1
    word = item.word_polygon()
    assert word is not None
    wr = word.boundingRect()
    assert wr.left() == pytest.approx(pt.chars[JEAN].bbox.left(), abs=0.01)
    assert wr.right() == pytest.approx(pt.chars[JEAN + 3].bbox.right(), abs=0.01)
    # Same target: the item is kept; another word: same item, moved.
    assert dv.text_hover.hover(0, _line_center(doc)) == target
    assert dv.text_hover.item is item
    other = dv.text_hover.hover(0, pt.chars[1].bbox.center())
    assert other is not None and other.word == Run(0, 7)
    assert dv.text_hover.item is item


def test_hover_space_has_line_frame_only(qtbot, setup) -> None:
    dv, doc, _editor, _rec = setup
    space = LINE1.index(" ")
    target = dv.text_hover.hover(0, doc.page_text(0).chars[space].bbox.center())
    assert target is not None and target.word is None
    assert dv.text_hover.item.word_polygon() is None


def test_hover_nothing_without_chars(qtbot, setup) -> None:
    dv, doc, _editor, _rec = setup
    dv.text_hover.hover(0, _line_center(doc))
    assert dv.text_hover.item is not None
    assert dv.text_hover.hover(0, QPointF(500, 700)) is None
    assert dv.text_hover.item is None and dv.text_hover.target is None
    assert dv.text_hover.hover(None, None) is None


def test_hover_cleared_on_page_change_and_document_switch(qtbot, setup, simple_pdf) -> None:
    dv, doc, _editor, _rec = setup
    dv.text_hover.hover(0, _line_center(doc))
    doc.page_changed.emit(0)
    assert dv.text_hover.item is None
    dv.text_hover.hover(0, _line_center(doc))
    dv.open(str(simple_pdf))
    assert dv.text_hover.item is None and dv.text_hover.target is None


def test_hover_skips_invisible_text(qtbot, dv, tmp_path) -> None:
    doc = dv.open(str(make_ocr_pdf(tmp_path / "ocr.pdf")))
    pt = doc.page_text(0)
    invisible = [i for i in range(len(pt.chars)) if pt.is_invisible(i)]
    assert invisible
    assert dv.text_hover.hover(0, pt.chars[invisible[0]].bbox.center()) is None
    assert dv.text_hover.item is None


def test_hover_skips_xobject_text(qtbot, dv, tmp_path) -> None:
    doc = dv.open(str(make_xobject_text_pdf(tmp_path / "xobj.pdf")))
    pt = doc.page_text(0)
    inside = [i for i in range(len(pt.chars)) if pt.span_of(i).in_xobject]
    assert inside
    assert dv.text_hover.hover(0, pt.chars[inside[0]].bbox.center()) is None


def test_selection_run_clamps_to_anchor_span(qtbot, setup) -> None:
    _dv, doc, _editor, _rec = setup
    pt = doc.page_text(0)
    line2 = pt.line_range(pt.ref(len(LINE1) + 1))
    # A range running from line 1 into line 2 is clamped to line 1's span.
    run = selection_run(pt, CharRef(JEAN, 0), line2[1])
    assert run == Run(JEAN, len(LINE1) - 1)
    assert clamp_to_span(pt, Run(0, 3), 2) == Run(0, 3)


# -- run editor -------------------------------------------------------------------
def test_open_geometry_font_prefill(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    view = dv.page_view
    widget = _open(qtbot, editor)
    assert widget.parent() is view.viewport()
    assert widget.isVisible() and editor.is_open
    assert widget.text() == "Jean"
    pt = doc.page_text(0)
    span = pt.span_of(JEAN)
    rect = run_rect(pt, JEAN_RUN)
    assert rect.height() == pytest.approx((span.ascender - span.descender) * span.size)
    assert rect.left() == pytest.approx(pt.chars[JEAN].bbox.left())
    _assert_geom(editor, view, 0, rect)
    assert widget.font().pixelSize() == round(BODY_SIZE * view.view_scale)
    assert widget.font().family() == "Calibri"
    assert not widget.font().bold() and not widget.font().italic()
    assert "dashed" in widget.styleSheet() and "rgb(0, 0, 0)" in widget.styleSheet()
    anchor = editor.anchor
    assert anchor is not None
    assert (anchor.page, anchor.run, anchor.text) == (0, JEAN_RUN, "Jean")
    assert anchor.page_id == doc.page_id(0)


def test_family_override_and_colour(qtbot, setup) -> None:
    _dv, doc, editor, _rec = setup
    pt = doc.page_text(0)
    title = pt.text.index("Titre")
    editor.open(0, Run(title, title + 4), family="Arial")
    widget = editor.editor
    assert widget.font().family() == "Arial"
    assert "rgb(0, 0, 255)" in widget.styleSheet()
    assert span_family(pt.span_of(title)) == "Times New Roman"
    editor.close()


def test_prefill_replaces_no_break_spaces(qtbot, setup) -> None:
    _dv, doc, editor, rec = setup
    pt = doc.page_text(0)
    run = Run(0, JEAN + 3)  # "Monsieur Jean"
    assert " " in run.text(pt)
    editor.open(0, run)
    assert editor.editor.text() == "Monsieur Jean" == display_text(run.text(pt))
    editor.commit()
    assert rec.runs == []  # unchanged


def test_enter_commits_once(qtbot, setup) -> None:
    dv, _doc, editor, rec = setup
    widget = _open(qtbot, editor)
    widget.selectAll()
    qtbot.keyClicks(widget, "Pierre")
    assert rec.runs == []
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert rec.runs == [(0, JEAN_RUN, "Pierre")]
    assert len(rec.anchors) == 1 and rec.anchors[0][0].run == JEAN_RUN
    assert not editor.is_open and widget.isHidden()
    qtbot.wait(10)
    assert len(rec.runs) == 1 and rec.cancelled == 0
    assert dv.page_view.hasFocus()


def test_unchanged_commits_nothing(qtbot, setup) -> None:
    _dv, _doc, editor, rec = setup
    widget = _open(qtbot, editor)
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert not editor.is_open and rec.runs == [] and rec.anchors == []


def test_empty_commit_is_removal(qtbot, setup) -> None:
    _dv, _doc, editor, rec = setup
    widget = _open(qtbot, editor)
    widget.clear()
    qtbot.keyClick(widget, Qt.Key.Key_Tab)
    assert rec.runs == [(0, JEAN_RUN, "")]


def test_escape_cancels(qtbot, setup) -> None:
    _dv, _doc, editor, rec = setup
    widget = _open(qtbot, editor)
    qtbot.keyClicks(widget, "xyz")
    qtbot.keyClick(widget, Qt.Key.Key_Escape)
    assert rec.cancelled == 1 and rec.runs == [] and not editor.is_open
    qtbot.wait(10)
    assert rec.runs == []


def test_focus_out_commits(qtbot, setup) -> None:
    dv, _doc, editor, rec = setup
    widget = _open(qtbot, editor)
    qtbot.keyClicks(widget, "Paul")
    dv.page_view.setFocus(Qt.FocusReason.MouseFocusReason)
    qtbot.waitUntil(lambda: not editor.is_open)
    assert rec.runs == [(0, JEAN_RUN, "Paul")]


def test_widens_for_longer_text(qtbot, setup) -> None:
    _dv, _doc, editor, _rec = setup
    widget = _open(qtbot, editor)
    w0 = widget.width()
    widget.setText("Jean-Christophe Durandeau")
    assert widget.width() > w0
    editor.close()


def test_follows_scroll_and_zoom(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    view = dv.page_view
    widget = _open(qtbot, editor)
    rect = run_rect(doc.page_text(0), JEAN_RUN)
    before = widget.geometry()
    bar = view.verticalScrollBar()
    bar.setMaximum(max(bar.maximum(), 100))
    start = bar.value()
    bar.setValue(start + 40)
    delta = bar.value() - start
    assert delta > 0
    assert widget.geometry().y() == pytest.approx(before.y() - delta, abs=2)
    _assert_geom(editor, view, 0, rect)

    bar.setValue(start)
    view.set_zoom_percent(200)
    _assert_geom(editor, view, 0, rect)
    assert widget.font().pixelSize() == round(BODY_SIZE * view.view_scale)
    assert editor.is_open and rec.runs == []


def test_closes_silently_on_document_switch(qtbot, setup, simple_pdf) -> None:
    dv, _doc, editor, rec = setup
    widget = _open(qtbot, editor)
    qtbot.keyClicks(widget, "bye")
    dv._replace(PdfDocument.open(str(simple_pdf)))
    assert not editor.is_open
    assert rec.runs == [] and rec.cancelled == 0
    assert editor.document is dv.document


def test_commits_on_reloaded(qtbot, setup) -> None:
    _dv, doc, editor, rec = setup
    widget = _open(qtbot, editor)
    qtbot.keyClicks(widget, "Luc")
    doc.reloaded.emit()
    assert not editor.is_open
    assert rec.runs == [(0, JEAN_RUN, "Luc")]


def test_commit_pending_edits_commits(qtbot, setup) -> None:
    dv, _doc, editor, rec = setup
    widget = _open(qtbot, editor)
    qtbot.keyClicks(widget, "Marc")
    dv.commit_pending_edits()
    assert rec.runs == [(0, JEAN_RUN, "Marc")]


def test_follows_page_on_remap(qtbot, setup) -> None:
    _dv, doc, editor, _rec = setup
    _open(qtbot, editor)
    doc.pages_remapped.emit([1])
    anchor = editor.anchor
    assert anchor is not None and anchor.page == 1
    doc.pages_remapped.emit([None, None])
    assert not editor.is_open


def test_open_rejects_bad_run(qtbot, setup) -> None:
    _dv, _doc, editor, _rec = setup
    with pytest.raises(ValueError):
        editor.open(0, Run(10_000, 10_001))
    with pytest.raises(ValueError):
        editor.open(5, JEAN_RUN)
    assert not editor.is_open
