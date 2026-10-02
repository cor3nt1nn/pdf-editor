"""Page commands and PageId identity of the undo commands (M6a-T2)."""

from __future__ import annotations

import re
from pathlib import Path

import fixtures
import pymupdf
import pytest
from fixtures import ANNOT_TEXT_NAME, OUTLINED_LINKS, ROTATED_ANNOT_NAME
from PySide6.QtCore import QCoreApplication, QObject, QRectF, QSizeF
from PySide6.QtGui import QUndoStack

import pdfeditor.i18n as i18n
from pdfeditor.core import pages
from pdfeditor.core.annotations import (
    STAMP_GLYPHS,
    AnnotKind,
    AnnotSpec,
    is_synthetic,
    stamp_rect,
)
from pdfeditor.core.commands import (
    AddAnnotCommand,
    DeleteAnnotCommand,
    DeletePagesCommand,
    EditAnnotCommand,
    InsertBlankPageCommand,
    InsertPagesCommand,
    MovePagesCommand,
    RotatePageCommand,
    RotatePagesCommand,
    SetFieldValueCommand,
)
from pdfeditor.core.document import PageError, PdfDocument
from pdfeditor.core.forms import FieldKind
from pdfeditor.core.snapshots import SnapshotStore

BLUE = (0.0, 0.0, 1.0)


# -- helpers -------------------------------------------------------------------------
def _open(path: Path) -> PdfDocument:
    return PdfDocument.open(path)


@pytest.fixture
def simple(simple_pdf):
    d = _open(simple_pdf)
    yield d
    d.close()


@pytest.fixture
def five(tmp_path):
    pdf = pymupdf.open()
    for n in range(5):
        page = pdf.new_page(width=300 + 10 * n, height=400)
        page.insert_text((50, 50), "ABCDE"[n], fontsize=30)
    path = tmp_path / "five.pdf"
    pdf.save(path)
    pdf.close()
    d = _open(path)
    yield d
    d.close()


@pytest.fixture
def french(qapp):
    i18n.install_translators(qapp, "fr")
    yield
    i18n.remove_translators(qapp)


def _letters(doc: PdfDocument) -> str:
    with doc.lock:
        return "".join(doc.fitz[i].get_text().strip() for i in range(doc.page_count))


def _render(doc: PdfDocument, page: int) -> bytes:
    with doc.lock:
        pix = doc.fitz[page].get_pixmap(matrix=pymupdf.Matrix(1, 1), annots=True, alpha=False)
    return bytes(pix.samples)


def _renders(doc: PdfDocument) -> list[bytes]:
    return [_render(doc, i) for i in range(doc.page_count)]


def _names(doc: PdfDocument) -> list[list[str]]:
    """Annotation names by page (a synthetic name depends on the load: "?")."""
    return [
        ["?" if is_synthetic(a.name) else a.name for a in doc.annots(i)]
        for i in range(doc.page_count)
    ]


def _links(doc: PdfDocument) -> list[list[tuple[int, object]]]:
    out = []
    with doc.lock:
        for page in doc.fitz:
            out.append(
                [(link["kind"], link.get("page", link.get("uri"))) for link in page.get_links()]
            )
    return out


def _toc(doc: PdfDocument) -> list[list[object]]:
    with doc.lock:
        return doc.fitz.get_toc(simple=True)


def _nom_kids(doc: PdfDocument) -> int:
    return sum(1 for w in doc.all_widgets() if w.name == "Nom")


def _without_id(data: bytes) -> bytes:
    return re.sub(rb"/ID\s*\[[^\]]*\]", b"", data)


def _stamp_spec(page: int) -> AnnotSpec:
    glyph = STAMP_GLYPHS["check"]
    rect, fs = stamp_rect(QRectF(150, 150, 1, 1).topLeft(), 12.0, glyph)
    return AnnotSpec(
        page=page, kind=AnnotKind.STAMP, text=glyph, font_size=fs, color=BLUE, rect=rect
    )


def _text_spec(page: int) -> AnnotSpec:
    return AnnotSpec(
        page=page,
        kind=AnnotKind.TEXT,
        text="hello",
        font_size=11.0,
        color=BLUE,
        rect=QRectF(100, 100, 200, 40),
    )


# -- delete --------------------------------------------------------------------------
def test_delete_undo_redo_restores_annotations_and_renders(annotated_pdf) -> None:
    doc = _open(annotated_pdf)
    stack = QUndoStack()
    ids = doc.page_ids()
    names, renders = _names(doc), _renders(doc)
    cmd = DeletePagesCommand(doc, [0])
    assert cmd.text() == "Delete page"
    stack.push(cmd)
    assert doc.page_count == 1 and doc.page_ids() == ids[1:]
    assert [a.name for a in doc.annots(0)] == [ROTATED_ANNOT_NAME]
    assert len(doc.snapshots) == 1
    stack.undo()
    assert doc.page_ids() == ids
    assert _names(doc) == names and ANNOT_TEXT_NAME in names[0]
    assert _renders(doc) == renders
    stack.redo()
    assert doc.page_count == 1 and doc.page_ids() == ids[1:]
    assert len(doc.snapshots) == 1  # the redo reuses the first snapshot
    stack.undo()
    assert _renders(doc) == renders
    doc.close()


def test_delete_restores_links_and_outline(outlined_pdf) -> None:
    doc = _open(outlined_pdf)
    stack = QUndoStack()
    links, toc = _links(doc), _toc(doc)
    assert links[0] == [(kind, target) for kind, _rect, target in OUTLINED_LINKS]
    assert [entry[2] for entry in toc] == [1, 2, 3]
    stack.push(DeletePagesCommand(doc, [2]))
    assert _links(doc)[0] == [(pymupdf.LINK_URI, OUTLINED_LINKS[1][2])]  # GoTo dropped
    assert _toc(doc)[2][2] == -1  # greyed
    stack.undo()
    assert _links(doc) == links and _toc(doc) == toc
    doc.close()


def test_delete_restores_form_fields(lo_form_pdf) -> None:
    doc = _open(lo_form_pdf)
    stack = QUndoStack()
    assert _nom_kids(doc) == 2
    stack.push(DeletePagesCommand(doc, [1]))
    assert _nom_kids(doc) == 1
    stack.undo()
    assert _nom_kids(doc) == 2
    assert doc.page_count == 2 and doc.is_form
    doc.close()


def test_delete_undo_after_full_save(annotated_pdf, tmp_path) -> None:
    doc = _open(annotated_pdf)
    stack = QUndoStack()
    renders = _renders(doc)
    stack.push(DeletePagesCommand(doc, [1]))
    doc.save()  # full: structural changes force it
    assert doc.page_count == 1
    stack.undo()
    assert doc.page_count == 2 and _renders(doc) == renders
    doc.save()
    reopened = _open(annotated_pdf)
    assert reopened.page_count == 2 and _names(reopened) == _names(doc)
    reopened.close()
    stack.redo()
    doc.save()
    assert _open(annotated_pdf).page_count == 1
    doc.close()


def test_delete_every_page_refused_without_touching_stack(simple: PdfDocument) -> None:
    stack = QUndoStack()
    cmd = DeletePagesCommand(simple, [0, 1, 2])
    with pytest.raises(PageError) as info:
        cmd.apply_now()
    assert info.value.reason == "last_page"
    assert stack.count() == 0 and simple.page_count == 3 and len(simple.snapshots) == 0


def test_delete_snapshot_failure_deletes_nothing(simple: PdfDocument, tmp_path) -> None:
    simple.snapshots = SnapshotStore(memory_limit=0, directory=tmp_path / "missing" / "dir")
    cmd = DeletePagesCommand(simple, [1])
    with pytest.raises(PageError) as info:
        cmd.apply_now()
    assert info.value.reason == "snapshot"
    assert simple.page_count == 3


def test_delete_not_permitted(owner_locked_pdf) -> None:
    doc = _open(owner_locked_pdf)
    cmd = DeletePagesCommand(doc, [0])
    with pytest.raises(PageError) as info:
        cmd.apply_now()
    assert info.value.reason == "permission" and len(doc.snapshots) == 0
    doc.close()


# -- move ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("indexes", "target", "expected"),
    [
        ([0], 2, "BACDE"),
        ([0], 5, "BCDEA"),
        ([4], 0, "EABCD"),
        ([1, 3], 0, "BDACE"),
        ([1, 3], 5, "ACEBD"),
        ([0, 4], 2, "BAECD"),
        ([1, 2], 4, "ADBCE"),
    ],
)
def test_move_orders(five: PdfDocument, indexes, target, expected) -> None:
    stack = QUndoStack()
    ids = five.page_ids()
    stack.push(MovePagesCommand(five, indexes, target))
    assert _letters(five) == expected
    assert sorted(five.page_ids()) == sorted(ids)
    assert [ids["ABCDE".index(c)] for c in expected] == five.page_ids()
    stack.undo()
    assert _letters(five) == "ABCDE" and five.page_ids() == ids
    stack.redo()
    assert _letters(five) == expected


@pytest.mark.parametrize(("indexes", "target"), [([1], 1), ([1], 2), ([1, 2], 3), ([0, 1], 0)])
def test_move_noop_is_obsolete(five: PdfDocument, indexes, target) -> None:
    stack = QUndoStack()
    cmd = MovePagesCommand(five, indexes, target)
    assert cmd.is_noop and cmd.isObsolete()
    stack.push(cmd)
    assert stack.count() == 0 and _letters(five) == "ABCDE"


# -- insert --------------------------------------------------------------------------
def test_insert_blank_undo_redo_same_id(simple: PdfDocument) -> None:
    stack = QUndoStack()
    ids = simple.page_ids()
    cmd = InsertBlankPageCommand(simple, 1, QSizeF(200, 300))
    assert cmd.text() == "Insert blank page"
    stack.push(cmd)
    new = simple.page_id(1)
    assert cmd.page_ids == [new] and cmd.pages == [1]
    assert simple.page_size(1) == QSizeF(200, 300)
    stack.undo()
    assert simple.page_ids() == ids
    stack.redo()
    assert simple.page_ids() == ids[:1] + [new] + ids[1:]
    stack.undo()
    stack.redo()
    assert simple.page_id(1) == new


def test_insert_blank_at_end(simple: PdfDocument) -> None:
    stack = QUndoStack()
    stack.push(InsertBlankPageCommand(simple, 3, QSizeF(100, 100)))
    assert simple.page_count == 4 and simple.page_size(3) == QSizeF(100, 100)


def _full_bytes(doc: PdfDocument) -> bytes:
    with doc.lock:
        return _without_id(doc.fitz.tobytes(garbage=3, deflate=True))


def test_insert_undo_is_exact(simple_pdf, lo_form_pdf) -> None:
    untouched = _open(simple_pdf)
    expected = _full_bytes(untouched)
    untouched.close()
    doc = _open(simple_pdf)
    stack = QUndoStack()
    stack.push(InsertBlankPageCommand(doc, 0, QSizeF(100, 100)))
    src = pymupdf.open(lo_form_pdf)
    data = pages.subdocument_bytes(src, [0, 1])
    src.close()
    cmd = InsertPagesCommand(doc, data, 2, 2)
    assert cmd.text() == "Insert pages"
    stack.push(cmd)
    assert doc.page_count == 6 and doc.is_form
    inserted = list(cmd.page_ids or [])
    assert cmd.pages == [2, 3]
    stack.undo()
    stack.undo()
    assert doc.page_count == 3 and not doc.is_form and not doc.has_acroform
    assert _full_bytes(doc) == expected
    stack.redo()
    stack.redo()
    assert doc.page_ids()[2:4] == inserted and _nom_kids(doc) == 2
    stack.undo()
    stack.redo()
    assert doc.page_ids()[2:4] == inserted
    doc.close()


# -- identity across structural changes -------------------------------------------
def test_annot_command_follows_moved_page(five: PdfDocument) -> None:
    stack = QUndoStack()
    add = AddAnnotCommand(five, _text_spec(1))
    stack.push(add)
    pid = five.page_id(1)
    stack.push(MovePagesCommand(five, [1], 0))
    assert five.page_index(pid) == 0 and add.page == 0
    assert [a.name for a in five.annots(0)] == [add.name]
    stack.undo()  # move
    assert add.page == 1
    stack.undo()  # add
    assert five.annots(1) == []
    stack.redo()
    stack.redo()
    assert [a.name for a in five.annots(0)] == [add.name]
    stack.undo()
    stack.undo()
    assert all(not five.annots(i) for i in range(5))


def test_stamp_on_inserted_page_survives_undo_redo(simple: PdfDocument) -> None:
    stack = QUndoStack()
    stack.push(InsertBlankPageCommand(simple, 1, QSizeF(300, 300)))
    stamp = AddAnnotCommand(simple, _stamp_spec(1))
    stack.push(stamp)
    stack.undo()
    stack.undo()
    assert simple.page_count == 3
    stack.redo()
    stack.redo()
    assert [a.name for a in simple.annots(1)] == [stamp.name]


def test_edit_and_delete_annot_after_earlier_page_deleted(annotated_pdf) -> None:
    doc = _open(annotated_pdf)
    stack = QUndoStack()
    info = doc.annot(1, ROTATED_ANNOT_NAME)
    assert info is not None
    edit = EditAnnotCommand(doc, info, color=(1.0, 0.0, 0.0))
    stack.push(edit)
    stack.push(InsertBlankPageCommand(doc, 0, QSizeF(100, 100)))
    assert edit.page == 2
    stack.push(DeleteAnnotCommand(doc, doc.annot(2, ROTATED_ANNOT_NAME)))
    assert doc.annot(2, ROTATED_ANNOT_NAME) is None
    stack.push(DeletePagesCommand(doc, [0]))
    while stack.canUndo():
        stack.undo()
    restored = doc.annot(1, ROTATED_ANNOT_NAME)
    assert restored is not None and restored.color == info.color
    while stack.canRedo():
        stack.redo()
    assert doc.page_count == 2 and doc.annot(1, ROTATED_ANNOT_NAME) is None
    doc.close()


def test_field_command_survives_structural_changes(lo_form_pdf) -> None:
    doc = _open(lo_form_pdf)
    stack = QUndoStack()
    widget = next(w for w in doc.widgets(1) if w.kind is FieldKind.TEXT and w.editable)
    fill = SetFieldValueCommand(doc, widget, "Rossi")
    stack.push(fill)
    stack.push(InsertBlankPageCommand(doc, 0, QSizeF(100, 100)))
    stack.push(MovePagesCommand(doc, [2], 0))
    assert fill.page == 0
    stack.push(DeletePagesCommand(doc, [1]))

    def value() -> str:
        return next(w.value for w in doc.all_widgets() if w.name == widget.name)

    assert value() == "Rossi"
    while stack.canUndo():
        stack.undo()
    assert doc.page_count == 2 and value() == widget.value
    while stack.canRedo():
        stack.redo()
    assert value() == "Rossi"
    doc.close()


# -- rotation ------------------------------------------------------------------------
def test_rotate_pages_one_step(simple: PdfDocument) -> None:
    stack = QUndoStack()
    cmd = RotatePagesCommand(simple, [0, 1, 2], 90)
    assert cmd.text() == "Rotate pages"
    stack.push(cmd)
    assert [simple.page_rotation(i) for i in range(3)] == [90, 90, 90]
    stack.push(RotatePagesCommand(simple, [2, 1, 0], 90))
    assert stack.count() == 1
    stack.push(RotatePagesCommand(simple, [0, 1], 90))
    assert stack.count() == 2
    stack.undo()
    stack.undo()
    assert [simple.page_rotation(i) for i in range(3)] == [0, 0, 0]


def test_rotation_merges_by_page_id_after_move(five: PdfDocument) -> None:
    stack = QUndoStack()
    stack.push(RotatePageCommand(five, 1, 90))
    stack.push(MovePagesCommand(five, [1], 0))
    stack.push(RotatePageCommand(five, 0, 90))  # the same page, now first
    assert stack.count() == 3  # not merged across the move (different command between)
    rot = RotatePageCommand(five, 0, 90)
    stack.push(rot)
    assert stack.count() == 3 and five.page_rotation(0) == 270
    stack.push(RotatePageCommand(five, 1, 90))  # another page: not merged
    assert stack.count() == 4
    while stack.canUndo():
        stack.undo()
    assert _letters(five) == "ABCDE"
    assert all(five.page_rotation(i) == 0 for i in range(5))


def test_rotate_not_permitted_leaves_stack(owner_locked_pdf) -> None:
    doc = _open(owner_locked_pdf)
    assert not doc.can_assemble
    cmd = RotatePageCommand(doc, 0, 90)
    with pytest.raises(PageError) as info:
        cmd.apply_now()
    assert info.value.reason == "permission"
    assert doc.page_rotation(0) == 0
    doc.close()


# -- labels --------------------------------------------------------------------------
def _page_labels(doc: PdfDocument) -> list[str]:
    return [
        DeletePagesCommand(doc, [0]).text(),
        DeletePagesCommand(doc, [0, 1]).text(),
        MovePagesCommand(doc, [0], 2).text(),
        InsertBlankPageCommand(doc, 0, QSizeF(10, 10)).text(),
        InsertPagesCommand(doc, b"", 0, 1).text(),
        RotatePagesCommand(doc, [0], 90).text(),
        RotatePagesCommand(doc, [0, 1], 90).text(),
    ]


def test_page_command_labels_english(simple: PdfDocument) -> None:
    assert _page_labels(simple) == [
        "Delete page",
        "Delete pages",
        "Move pages",
        "Insert blank page",
        "Insert pages",
        "Rotate page",
        "Rotate pages",
    ]


def test_page_command_labels_french(french, simple: PdfDocument) -> None:
    assert _page_labels(simple) == [
        "la suppression de la page",
        "la suppression des pages",
        "le déplacement des pages",
        "l’insertion d’une page vierge",
        "l’insertion des pages",
        "la rotation de la page",
        "la rotation des pages",
    ]
    owner = QObject()
    stack = QUndoStack()
    undo = stack.createUndoAction(owner, QCoreApplication.translate("MainWindow", "Undo"))
    stack.push(MovePagesCommand(simple, [0], 3))
    assert undo.text() == "Annuler le déplacement des pages"


# -- snapshot store lifetime -----------------------------------------------------------
def test_snapshots_cleared_with_undo_stack(qtbot, settings, simple_pdf, tmp_path) -> None:
    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow(settings)
    qtbot.addWidget(window)
    view = window.document_view
    doc = view.open(str(simple_pdf))
    view.push(DeletePagesCommand(doc, [0]))
    assert len(doc.snapshots) == 1
    other = fixtures.make_simple_pdf(tmp_path / "other.pdf")
    view.open(str(other))
    assert len(doc.snapshots) == 0
    assert view.undo_stack.count() == 0


def test_insert_at_start_adds_no_page_labels(simple: PdfDocument, lo_form_pdf) -> None:
    """MuPDF writes /PageLabels [0 D 1 D] when inserting at page 0 of a label-less
    document (labels 1, 1, 2...): the insert operations remove it."""
    stack = QUndoStack()
    stack.push(InsertBlankPageCommand(simple, 0, QSizeF(100, 100)))
    src = pymupdf.open(lo_form_pdf)
    data = pages.subdocument_bytes(src, [0])
    src.close()
    stack.push(InsertPagesCommand(simple, data, 0, 1))
    with simple.lock:
        catalog = simple.fitz.pdf_catalog()
        assert simple.fitz.xref_get_key(catalog, "PageLabels") == ("null", "null")
        assert [p.get_label() for p in simple.fitz] == [""] * 5
