"""Text markup annotations in the core (M6b-T10): Highlight, Underline, StrikeOut,
Squiggly read, created, recoloured, deleted and re-created, saved and flattened."""

from __future__ import annotations

import uuid

import pymupdf
import pytest
from fixtures import (
    MARKED_COLORS,
    MARKED_FOREIGN,
    MARKED_FOREIGN_AUTHOR,
    MARKED_FOREIGN_CONTENTS,
    MARKED_FOREIGN_OPACITY,
    MARKED_HIDDEN_NAME,
    MARKED_HIGHLIGHT_NAME,
    MARKED_QUADS,
    MARKED_ROTATED_NAME,
    MARKED_SQUIGGLY_NAME,
    MARKED_STRIKEOUT_NAME,
    MARKED_STRIKEOUT_OPACITY,
    MARKED_UNDERLINE_NAME,
    QuadPts,
    make_marked_pdf,
    make_text_pdf,
)
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QUndoStack

from pdfeditor import i18n
from pdfeditor.core import annotations
from pdfeditor.core.annotations import (
    MARKUP_KINDS,
    AnnotInfo,
    AnnotKind,
    markup_spec,
    quads_rect,
    spec_from,
)
from pdfeditor.core.commands import AddAnnotCommand, DeleteAnnotCommand, EditAnnotCommand
from pdfeditor.core.document import AnnotError, ExportOptions, PdfDocument
from pdfeditor.core.pagetext import Quad

GREEN = (0.0, 1.0, 0.0)
YELLOW = (1.0, 1.0, 0.0)
OURS = (MARKED_HIGHLIGHT_NAME, MARKED_UNDERLINE_NAME, MARKED_STRIKEOUT_NAME, MARKED_SQUIGGLY_NAME)


@pytest.fixture
def marked(tmp_path):
    d = PdfDocument.open(make_marked_pdf(tmp_path / "marked.pdf"))
    yield d
    d.close()


@pytest.fixture
def french(qapp):
    i18n.install_translators(qapp, "fr")
    yield
    i18n.remove_translators(qapp)


def _pts(quad: Quad) -> QuadPts:
    return tuple(v for p in quad for v in (p.x(), p.y()))  # type: ignore[return-value]


def _quads_close(got: tuple[Quad, ...], expected: tuple[QuadPts, ...], tol=0.01) -> bool:
    if len(got) != len(expected):
        return False
    pairs = zip(got, expected, strict=True)
    return all(abs(a - b) <= tol for q, e in pairs for a, b in zip(_pts(q), e, strict=True))


def _key(doc: PdfDocument, info: AnnotInfo, key: str) -> tuple[str, str]:
    with doc.lock:
        return doc.fitz.xref_get_key(doc.annot(info.page, info.name).xref, key)


def _rgb_pixmap(doc: PdfDocument, page: int, scale: float = 2.0) -> pymupdf.Pixmap:
    with doc.lock:
        return doc.fitz[page].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)


def _word_quads(doc: PdfDocument, page: int, word: str) -> list[Quad]:
    text = doc.page_text(page)
    start = "".join(c.c for c in text.chars).index(word)
    first, last = text.word_range(start)
    assert text.text_of(text.chars_between(first, last)) == word
    return text.range_quads(first, last)


# -- reading ------------------------------------------------------------------------
def test_marked_fixture_listing(marked: PdfDocument) -> None:
    listed = marked.annots(0)
    kinds = [a.kind for a in listed]
    assert kinds == [
        AnnotKind.HIGHLIGHT,
        AnnotKind.UNDERLINE,
        AnnotKind.STRIKEOUT,
        AnnotKind.SQUIGGLY,
        AnnotKind.HIGHLIGHT,
    ]
    assert [a.name for a in listed[:4]] == list(OURS)
    foreign = listed[4]
    assert annotations.is_synthetic(foreign.name)
    assert MARKED_HIDDEN_NAME not in {a.name for a in listed}
    for info in listed:
        key = MARKED_FOREIGN if info is foreign else info.name
        assert _quads_close(info.quads, MARKED_QUADS[key]), info.name
        assert info.color == pytest.approx(MARKED_COLORS[key])
        assert info.is_markup and not info.movable and info.editable
        assert not info.text_editable
        assert info.rect == quads_rect(info.quads)
    assert [a.opacity for a in listed] == pytest.approx(
        [1.0, 1.0, MARKED_STRIKEOUT_OPACITY, 1.0, MARKED_FOREIGN_OPACITY]
    )
    assert foreign.text == MARKED_FOREIGN_CONTENTS
    with marked.lock:
        hidden = annotations.read_annots(marked.fitz, 0, include_hidden=True)
    assert [a.name for a in hidden if a.hidden] == [MARKED_HIDDEN_NAME]

    (rotated,) = marked.annots(1)
    assert rotated.name == MARKED_ROTATED_NAME and rotated.kind is AnnotKind.HIGHLIGHT
    assert _quads_close(rotated.quads, MARKED_QUADS[MARKED_ROTATED_NAME])
    # The stored /Rect is unrotated (and larger than the quads).
    assert rotated.unrotated_rect != (
        rotated.rect.left(),
        rotated.rect.top(),
        rotated.rect.right(),
        rotated.rect.bottom(),
    )


def test_markup_without_quadpoints_is_not_editable(marked: PdfDocument) -> None:
    info = marked.annot(0, MARKED_UNDERLINE_NAME)
    with marked.lock:
        marked.fitz.xref_set_key(info.xref, "QuadPoints", "null")
    marked.page_changed.emit(0)
    odd = marked.annot(0, MARKED_UNDERLINE_NAME)
    assert odd.quads == () and odd.rect.isEmpty() and not odd.editable


# -- creating -----------------------------------------------------------------------
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_create_highlight_on_word_rotated_cropbox(tmp_path, rotate: int) -> None:
    doc = PdfDocument.open(make_text_pdf(tmp_path / "t.pdf", rotate=rotate, cropbox=True))
    try:
        quads = _word_quads(doc, 0, "quick")
        word = quads_rect(quads)
        before = _rgb_pixmap(doc, 0)
        info = doc.add_annot(markup_spec(0, AnnotKind.HIGHLIGHT, quads, YELLOW))
        after = _rgb_pixmap(doc, 0)
        assert info.kind is AnnotKind.HIGHLIGHT
        uuid.UUID(info.name)
        assert _quads_close(info.quads, tuple(_pts(q) for q in quads))
        assert _key(doc, info, "C") == ("array", "[1 1 0]")
        assert _key(doc, info, "F") == ("int", "4")
        assert _key(doc, info, "CA")[0] == "null"
        assert info.opacity == 1.0

        # Background pixels of the word turn yellow; the text elsewhere is untouched.
        inner = word.adjusted(1, 1, -1, -1)
        yellow = white = 0
        for y in range(int(inner.top() * 2), int(inner.bottom() * 2)):
            for x in range(int(inner.left() * 2), int(inner.right() * 2)):
                if min(before.pixel(x, y)) > 240:
                    white += 1
                    r, g, b = after.pixel(x, y)
                    yellow += r > 200 and g > 200 and b < 100
        assert white > 50 and yellow > 0.9 * white
        far = _word_quads(doc, 0, "Paragraph")[0].bounding_rect()
        cx, cy = int(far.center().x() * 2), int(far.center().y() * 2)
        assert before.pixel(cx, cy) == after.pixel(cx, cy)
    finally:
        doc.close()


@pytest.mark.parametrize(
    "kind", [AnnotKind.HIGHLIGHT, AnnotKind.UNDERLINE, AnnotKind.STRIKEOUT, AnnotKind.SQUIGGLY]
)
def test_create_every_kind_with_opacity(tmp_path, kind: AnnotKind) -> None:
    doc = PdfDocument.open(make_text_pdf(tmp_path / "t.pdf"))
    try:
        quads = _word_quads(doc, 0, "brown")
        info = doc.add_annot(markup_spec(0, kind, quads, GREEN, opacity=0.4))
        assert info.kind is kind
        assert info.color == pytest.approx(GREEN)
        assert info.opacity == pytest.approx(0.4)
        assert _key(doc, info, "CA") == ("float", ".4")
        assert doc.annot(0, info.name) == info
    finally:
        doc.close()


def test_multi_line_highlight(tmp_path) -> None:
    doc = PdfDocument.open(make_text_pdf(tmp_path / "t.pdf"))
    try:
        text = doc.page_text(0)
        joined = "".join(c.c for c in text.chars)
        a, b = joined.index("lazy"), joined.index("accents")
        quads = text.range_quads(a, b)
        assert len(quads) == 2
        info = doc.add_annot(markup_spec(0, AnnotKind.HIGHLIGHT, quads, YELLOW))
        # /QuadPoints are stored as float32 (about 1e-5 pt off).
        assert _quads_close(info.quads, tuple(_pts(q) for q in quads), tol=1e-3)
        assert info.rect == quads_rect(info.quads)
    finally:
        doc.close()


def test_invalid_markup_specs(marked: PdfDocument) -> None:
    with pytest.raises(ValueError):
        markup_spec(0, AnnotKind.TEXT, [Quad.from_rect(QRectF(0, 0, 5, 5))], YELLOW)
    with pytest.raises(AnnotError):
        marked.add_annot(markup_spec(0, AnnotKind.HIGHLIGHT, [], YELLOW))
    assert len(marked.annots(0)) == 5


# -- changing ------------------------------------------------------------------------
@pytest.mark.parametrize("page,name", [(0, MARKED_UNDERLINE_NAME), (1, MARKED_ROTATED_NAME)])
def test_recolor_keeps_quads_and_rotation(marked: PdfDocument, page: int, name: str) -> None:
    info = marked.annot(page, name)
    rotate = _key(marked, info, "Rotate")
    command = EditAnnotCommand(marked, info, color=(1.0, 0.0, 0.0))
    assert command.text() == "Change markup color"
    command.apply_now()
    stack = QUndoStack()
    stack.push(command)
    new = marked.annot(page, name)
    assert new.color == pytest.approx((1.0, 0.0, 0.0))
    assert _quads_close(new.quads, MARKED_QUADS[name])
    assert _key(marked, new, "Rotate") == rotate
    stack.undo()
    old = marked.annot(page, name)
    assert old.color == pytest.approx(MARKED_COLORS[name])
    assert _quads_close(old.quads, MARKED_QUADS[name])
    stack.redo()
    assert marked.annot(page, name).color == pytest.approx((1.0, 0.0, 0.0))


def test_change_opacity_and_undo(marked: PdfDocument) -> None:
    info = marked.annot(0, MARKED_STRIKEOUT_NAME)
    command = EditAnnotCommand(marked, info, opacity=1.0)
    command.apply_now()
    stack = QUndoStack()
    stack.push(command)
    changed = marked.annot(0, MARKED_STRIKEOUT_NAME)
    assert changed.opacity == 1.0 and _key(marked, changed, "CA")[0] == "null"
    stack.undo()
    assert marked.annot(0, MARKED_STRIKEOUT_NAME).opacity == pytest.approx(0.5)


def test_markup_edits_refused(marked: PdfDocument) -> None:
    info = marked.annot(0, MARKED_HIGHLIGHT_NAME)
    for changes in (
        {"rect": QRectF(10, 10, 50, 20)},
        {"text": "x"},
        {"font_size": 14.0},
        {"color": YELLOW, "fit_height": True},
    ):
        with pytest.raises(ValueError):
            EditAnnotCommand(marked, info, **changes)
    with pytest.raises(AnnotError):
        marked.update_annot(0, info.name, rect=QRectF(10, 10, 50, 20))
    assert _quads_close(marked.annot(0, info.name).quads, MARKED_QUADS[info.name])


def test_opacity_refused_for_freetext(blank_text_doc: PdfDocument) -> None:
    text = next(a for a in blank_text_doc.annots(0) if a.kind is AnnotKind.TEXT)
    with pytest.raises(ValueError):
        EditAnnotCommand(blank_text_doc, text, opacity=0.5)


@pytest.fixture
def blank_text_doc(tmp_path):
    d = PdfDocument.open(make_text_pdf(tmp_path / "t.pdf"))
    yield d
    d.close()


def test_foreign_highlight_recolor_keeps_author(marked: PdfDocument) -> None:
    foreign = marked.annots(0)[4]
    stack = QUndoStack()
    command = EditAnnotCommand(marked, foreign, color=GREEN)
    command.apply_now()
    stack.push(command)
    real = command.name
    uuid.UUID(real)
    new = marked.annot(0, real)
    assert new.color == pytest.approx(GREEN)
    assert new.text == MARKED_FOREIGN_CONTENTS
    assert new.opacity == pytest.approx(MARKED_FOREIGN_OPACITY)
    assert _key(marked, new, "T") == ("string", MARKED_FOREIGN_AUTHOR)
    assert _key(marked, new, "AP")[0] != "null"
    stack.undo()
    assert marked.annot(0, real).color == pytest.approx(MARKED_COLORS[MARKED_FOREIGN])


# -- delete and re-create ----------------------------------------------------------------
@pytest.mark.parametrize("page,name", [(0, n) for n in OURS] + [(1, MARKED_ROTATED_NAME)])
def test_delete_and_recreate_pixel_identical(marked: PdfDocument, page: int, name: str) -> None:
    before = _rgb_pixmap(marked, page).samples
    info = marked.annot(page, name)
    command = DeleteAnnotCommand(marked, info)
    command.apply_now()
    stack = QUndoStack()
    stack.push(command)
    assert marked.annot(page, name) is None
    assert _rgb_pixmap(marked, page).samples != before
    stack.undo()
    again = marked.annot(page, name)
    assert again is not None
    assert (again.kind, again.color, again.opacity) == (info.kind, info.color, info.opacity)
    assert _quads_close(again.quads, MARKED_QUADS[name])
    assert _rgb_pixmap(marked, page).samples == before


def test_foreign_highlight_delete_undo_keeps_contents(marked: PdfDocument) -> None:
    foreign = marked.annots(0)[4]
    stack = QUndoStack()
    command = DeleteAnnotCommand(marked, foreign)
    command.apply_now()
    stack.push(command)
    assert len(marked.annots(0)) == 4
    stack.undo()
    again = marked.annot(0, command.name)
    assert again.kind is AnnotKind.HIGHLIGHT
    assert again.text == MARKED_FOREIGN_CONTENTS
    assert again.opacity == pytest.approx(MARKED_FOREIGN_OPACITY)
    assert _quads_close(again.quads, MARKED_QUADS[MARKED_FOREIGN])


def test_spec_from_markup_round_trip(marked: PdfDocument) -> None:
    info = marked.annot(0, MARKED_HIGHLIGHT_NAME)
    spec = spec_from(info)
    assert spec.kind is AnnotKind.HIGHLIGHT and spec.quads == info.quads
    assert spec.opacity == info.opacity and spec.color == info.color


# -- commands: labels ------------------------------------------------------------------------
def _labels(doc: PdfDocument) -> list[str]:
    quads = [Quad.from_rect(QRectF(72, 89, 30, 14))]
    labels = [
        AddAnnotCommand(doc, markup_spec(0, kind, quads, YELLOW)).text()
        for kind in (AnnotKind.HIGHLIGHT, AnnotKind.UNDERLINE, AnnotKind.STRIKEOUT)
    ]
    info = doc.annot(0, MARKED_HIGHLIGHT_NAME)
    labels.append(EditAnnotCommand(doc, info, color=GREEN).text())
    return labels


def test_markup_command_labels(marked: PdfDocument) -> None:
    assert _labels(marked) == [
        "Add highlight",
        "Add underline",
        "Add strike-through",
        "Change markup color",
    ]
    squiggly = markup_spec(0, AnnotKind.SQUIGGLY, [Quad.from_rect(QRectF(0, 0, 9, 9))], YELLOW)
    assert AddAnnotCommand(marked, squiggly).text() == "Add underline"


def test_markup_command_labels_french(french, marked: PdfDocument) -> None:
    assert _labels(marked) == [
        "l’ajout du surlignage",
        "l’ajout du soulignement",
        "l’ajout du texte barré",
        "le changement de couleur du marquage",
    ]


def test_add_command_undo_redo_same_name(marked: PdfDocument) -> None:
    quads = _word_quads(marked, 0, "starts")
    command = AddAnnotCommand(marked, markup_spec(0, AnnotKind.UNDERLINE, quads, GREEN))
    command.apply_now()
    stack = QUndoStack()
    stack.push(command)
    created = marked.annot(0, command.name)
    assert created is not None
    stack.undo()
    assert marked.annot(0, command.name) is None
    stack.redo()
    again = marked.annot(0, command.name)
    assert again.quads == created.quads and again.color == created.color


# -- saves and flattening ----------------------------------------------------------------
def test_markups_resolve_after_incremental_and_full_saves(marked: PdfDocument) -> None:
    quads = _word_quads(marked, 0, "Paragraph")
    stack = QUndoStack()
    command = AddAnnotCommand(marked, markup_spec(0, AnnotKind.HIGHLIGHT, quads, YELLOW))
    command.apply_now()
    stack.push(command)
    recolor = EditAnnotCommand(marked, marked.annot(0, MARKED_UNDERLINE_NAME), color=GREEN)
    recolor.apply_now()
    stack.push(recolor)
    expected = {a.name: a.quads for a in marked.annots(0) if not annotations.is_synthetic(a.name)}
    assert marked.can_save_incrementally()
    marked.save()
    assert {a.name: a.quads for a in marked.annots(0) if a.name in expected} == expected
    marked.save(force_full=True)
    assert {a.name: a.quads for a in marked.annots(0) if a.name in expected} == expected
    assert marked.annot(0, MARKED_UNDERLINE_NAME).color == pytest.approx(GREEN)
    stack.undo()  # recolour
    assert marked.annot(0, MARKED_UNDERLINE_NAME).color == pytest.approx(
        MARKED_COLORS[MARKED_UNDERLINE_NAME]
    )
    stack.undo()  # add
    assert marked.annot(0, command.name) is None
    reopened = PdfDocument.open(marked.path)
    try:
        assert _quads_close(
            reopened.annot(1, MARKED_ROTATED_NAME).quads, MARKED_QUADS[MARKED_ROTATED_NAME]
        )
        assert reopened.annot(0, command.name) is not None  # saved before the undo
    finally:
        reopened.close()


def test_bake_flattens_every_markup_kind(marked: PdfDocument, tmp_path) -> None:
    listed = {a.name: a for a in marked.annots(0)}
    assert {a.kind for a in listed.values()} == set(MARKUP_KINDS)
    before = _rgb_pixmap(marked, 0, 1.0)
    out = tmp_path / "flat.pdf"
    marked.export_copy(out, ExportOptions(flatten_annots=True))
    flat = pymupdf.open(out)
    try:
        assert [len(list(p.annots())) for p in flat] == [0, 0]
        after = flat[0].get_pixmap(alpha=False)
        for info in listed.values():
            for quad in info.quads:
                c = quad.bounding_rect().center()
                pt = QPointF(c.x(), c.y())
                b, a = before.pixel(int(pt.x()), int(pt.y())), after.pixel(int(pt.x()), int(pt.y()))
                assert max(abs(x - y) for x, y in zip(b, a, strict=True)) <= 8, info.name
    finally:
        flat.close()
