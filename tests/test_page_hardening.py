"""M6a-T6 hardening: 300-page documents, undo copies (spill, failures, lifetime), page
trees with intermediate nodes, overlays across structural changes, signature image
sharing after page operations."""

from __future__ import annotations

import gc
import re
import time

import fixtures
import pymupdf
import pytest
from fixtures import (
    FOREIGN_TEXT,
    LOCKED_SIGNATURE_NAME,
    NESTED_LETTERS,
    ROTATED_ANNOT_NAME,
    SIGNED_NAME,
)
from pdfcheck import strict_read
from PySide6.QtCore import QRectF, QSizeF, Qt
from PySide6.QtGui import QPixmap, QUndoStack
from PySide6.QtWidgets import QLineEdit, QMessageBox, QPlainTextEdit

from pdfeditor.core import pages
from pdfeditor.core.annotations import BLACK, AnnotKind, AnnotSpec, is_synthetic
from pdfeditor.core.commands import (
    AddAnnotCommand,
    DeletePagesCommand,
    InsertBlankPageCommand,
    InsertPagesCommand,
    MovePagesCommand,
    RotatePagesCommand,
    SetFieldValueCommand,
)
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.forms import FieldKind
from pdfeditor.core.snapshots import SnapshotStore
from pdfeditor.render.renderer import RenderKind
from pdfeditor.render.service import RenderService
from pdfeditor.ui import dialogs
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.page_view import PageView
from pdfeditor.ui.thumbnails import ThumbnailModel, ThumbnailSidebar

#: docs/M6_PLAN.md M6a-T6: remapping 300 pages (cache + views) stays under 50 ms.
REMAP_BUDGET_S = 0.05
#: Whole delete of one page in a 300-page document, UI attached.
DELETE_BUDGET_S = 1.0
#: Snapshot (spilled to disk) + delete + undo of a 300-page image document.
RESTORE_BUDGET_S = 3.0


# -- helpers -------------------------------------------------------------------------
def _letters(doc: PdfDocument) -> str:
    with doc.lock:
        return "".join(doc.fitz[i].get_text().strip() for i in range(doc.page_count))


def _page_tree(fitz_doc: pymupdf.Document) -> tuple[int, int, list[int]]:
    """(depth, number of /Pages nodes, xrefs of nodes without kids) of the page tree."""
    root = int(fitz_doc.xref_get_key(fitz_doc.pdf_catalog(), "Pages")[1].split()[0])
    nodes = 0
    empty: list[int] = []

    def walk(xref: int) -> int:
        nonlocal nodes
        kind, kids = fitz_doc.xref_get_key(xref, "Kids")
        if kind != "array":
            return 0
        nodes += 1
        refs = [int(m) for m in re.findall(r"(\d+) 0 R", kids)]
        if not refs:
            empty.append(xref)
        return 1 + max([walk(r) for r in refs] or [0])

    return walk(root), nodes, empty


def _check_file(path, letters: str) -> None:
    strict_read(path)
    with pymupdf.open(path) as f:
        assert "".join(f[i].get_text().strip() for i in range(f.page_count)) == letters
        assert _page_tree(f)[2] == []


def _image_objects(fitz_doc: pymupdf.Document) -> list[int]:
    """Xrefs of RGB image XObjects of a document."""
    return [
        xref
        for xref in range(1, fitz_doc.xref_length())
        if fitz_doc.xref_get_key(xref, "Subtype")[1] == "/Image"
        and fitz_doc.xref_get_key(xref, "ColorSpace")[1] == "/DeviceRGB"
    ]


@pytest.fixture
def dv(qtbot):
    view = DocumentView()
    qtbot.addWidget(view)
    view.resize(800, 600)
    view.show()
    qtbot.waitExposed(view)
    yield view
    view.undo_stack.setClean()
    view.shutdown()


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        dialogs, "warn", lambda parent, title, text, details=None: warnings.append(text)
    )
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda *_a: QMessageBox.StandardButton.Discard
    )
    w = MainWindow(settings)
    w.warnings = warnings
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    yield w
    w.undo_stack.setClean()
    w.close()


# -- 300 pages -----------------------------------------------------------------------
def test_remap_300_pages_within_budget(qtbot, tmp_path) -> None:
    doc = PdfDocument.open(fixtures.make_many_pages_pdf(tmp_path / "many300.pdf", 300))
    service = RenderService()
    try:
        service.set_document(doc)
        for i in range(300):
            service.cache.put(i, 0.3, RenderKind.THUMB, QPixmap(4, 4))
            service.cache.put(i, 1.0, RenderKind.PAGE, QPixmap(4, 4))
        mapping: list[int | None] = [None, *range(299)]
        start = time.perf_counter()
        service.remap_pages(mapping)
        elapsed = time.perf_counter() - start
        assert service.pixmap(0, 0.3, RenderKind.THUMB) is not None
        assert service.pixmap(298, 1.0, RenderKind.PAGE) is not None
        assert service.pixmap(299, 1.0, RenderKind.PAGE) is None
        print(f"remap of 300 pages: {elapsed * 1000:.1f} ms")
        assert elapsed < REMAP_BUDGET_S
    finally:
        service.stop()
        doc.close()


def test_delete_in_300_page_document_keeps_thumbnails(qtbot, tmp_path) -> None:
    doc = PdfDocument.open(fixtures.make_many_pages_pdf(tmp_path / "many300.pdf", 300))
    service = RenderService()
    try:
        model = ThumbnailModel(service)
        service.set_document(doc)
        model.set_document(doc)
        sidebar = ThumbnailSidebar(model)
        qtbot.addWidget(sidebar)
        view = PageView(service)
        qtbot.addWidget(view)
        view.resize(800, 600)
        view.show()
        view.set_document(doc)
        for row in range(8):
            model.data(model.index(row), Qt.ItemDataRole.DecorationRole)
        qtbot.waitUntil(lambda: all(model.is_rendered(r) for r in range(8)), timeout=10000)
        stamps: dict[str, float] = {}
        doc.pages_remapped.connect(lambda _m: stamps.__setitem__("remapped", time.perf_counter()))
        # Connected last: runs after every other structure_changed slot (views, model).
        doc.structure_changed.connect(lambda: stamps.__setitem__("structure", time.perf_counter()))
        thumbs: list[int] = []
        service.pixmap_ready.connect(
            lambda p, k: thumbs.append(p) if k == RenderKind.THUMB else None
        )
        start = time.perf_counter()
        doc.delete_pages([1])
        elapsed = time.perf_counter() - start
        assert doc.page_count == 299 and model.rowCount() == 299 and view.page_count == 299
        assert all(model.is_rendered(r) for r in range(7))  # moved, not re-rendered
        qtbot.wait(300)
        assert thumbs == []
        remap = stamps["structure"] - stamps["remapped"]
        print(f"delete in 300 pages: {elapsed * 1000:.1f} ms (remap stage {remap * 1000:.1f} ms)")
        assert remap < REMAP_BUDGET_S
        assert elapsed < DELETE_BUDGET_S
    finally:
        service.stop()
        doc.close()


def test_delete_undo_300_image_pages_spills_to_disk(many_images_pdf, tmp_path) -> None:
    doc = PdfDocument.open(many_images_pdf)
    doc.snapshots = SnapshotStore(directory=tmp_path)
    stack = QUndoStack()
    before = [doc.render(i, 0.5) for i in (4, 5, 6)]
    start = time.perf_counter()
    stack.push(DeletePagesCommand(doc, [5]))
    deleted = time.perf_counter()
    assert doc.page_count == 299
    assert doc.snapshots.on_disk_count == 1 and doc.snapshots.bytes_in_memory == 0
    spill = doc.snapshots.spill_directory
    assert spill is not None and spill.parent == tmp_path
    files = list(spill.iterdir())
    assert len(files) == 1 and files[0].stat().st_size > 32 * 1024 * 1024
    stack.undo()
    elapsed = time.perf_counter() - start
    assert doc.page_count == 300
    assert [doc.render(i, 0.5) for i in (4, 5, 6)] == before
    print(
        f"300 image pages: delete {(deleted - start) * 1000:.0f} ms, total {elapsed * 1000:.0f} ms"
    )
    assert elapsed < RESTORE_BUDGET_S
    stack.redo()
    stack.undo()
    assert doc.page_count == 300 and doc.snapshots.on_disk_count == 1
    doc.close()
    assert not spill.exists()


# -- undo copies: failures and lifetime ----------------------------------------------
def test_store_put_failure_stores_nothing(tmp_path) -> None:
    store = SnapshotStore(memory_limit=4, directory=tmp_path / "missing" / "dir")
    assert store.put(b"ok") == 1
    with pytest.raises(OSError):
        store.put(b"too big for memory")
    assert len(store) == 1 and store.on_disk_count == 0
    assert store.get(1) == b"ok"
    with pytest.raises(KeyError):
        store.get(2)
    store.clear()


def test_snapshot_failure_keeps_document_and_warns(window: MainWindow, simple_pdf, tmp_path):
    assert window.open_file(str(simple_pdf))
    doc = window.document_view.document
    doc.snapshots = SnapshotStore(memory_limit=0, directory=tmp_path / "missing" / "dir")
    window.page_view.scroll_to_page(1)
    window.act_delete_pages.trigger()
    assert window.warnings == ["The page could not be deleted: no room for the undo copy."]
    assert doc.page_count == 3 and len(doc.snapshots) == 0
    assert window.undo_stack.count() == 0 and not window.isWindowModified()
    assert not doc.structure_edited
    # Thumbnail Delete key goes through the same path.
    window.thumbnails.pages_delete_requested.emit([0])
    assert len(window.warnings) == 2 and doc.page_count == 3


def test_dropped_redo_branch_frees_undo_copies(simple_pdf) -> None:
    doc = PdfDocument.open(simple_pdf)
    stack = QUndoStack()
    stack.push(DeletePagesCommand(doc, [0]))
    stack.push(DeletePagesCommand(doc, [0]))
    assert len(doc.snapshots) == 2
    stack.undo()
    stack.undo()
    assert len(doc.snapshots) == 2  # still redoable
    stack.push(RotatePagesCommand(doc, [0], 90))  # Qt deletes the redo branch
    gc.collect()
    assert len(doc.snapshots) == 0
    stack.push(DeletePagesCommand(doc, [0]))
    assert len(doc.snapshots) == 1 and doc.page_count == 2
    stack.clear()
    gc.collect()
    assert len(doc.snapshots) == 0
    doc.close()


def test_spilled_copy_removed_with_its_command(simple_pdf, tmp_path) -> None:
    doc = PdfDocument.open(simple_pdf)
    doc.snapshots = SnapshotStore(memory_limit=0, directory=tmp_path)
    stack = QUndoStack()
    stack.push(DeletePagesCommand(doc, [2]))
    spill = doc.snapshots.spill_directory
    assert spill is not None and len(list(spill.iterdir())) == 1
    stack.undo()
    stack.push(InsertBlankPageCommand(doc, 0, QSizeF(50, 50)))
    gc.collect()
    assert list(spill.iterdir()) == [] and doc.snapshots.on_disk_count == 0
    doc.close()
    assert not spill.exists()


# -- page tree with intermediate nodes -----------------------------------------------
def test_nested_tree_survives_operations_and_undo(nested_pages_pdf, tmp_path) -> None:
    doc = PdfDocument.open(nested_pages_pdf)
    with doc.lock:
        assert _page_tree(doc.fitz) == (3, 4, [])
    assert _letters(doc) == NESTED_LETTERS
    stack = QUndoStack()
    stack.push(DeletePagesCommand(doc, [1]))  # B, inside N1
    assert _letters(doc) == "ACDEF"
    stack.push(MovePagesCommand(doc, [3, 4], 0))  # E and F leave N3
    assert _letters(doc) == "EFACD"
    with doc.lock:
        assert _page_tree(doc.fitz) == (2, 3, [])  # N3 (empty) was removed, N1 and N2 kept
    stack.push(InsertBlankPageCommand(doc, 2, QSizeF(100, 100)))
    src = pymupdf.open(fixtures.make_nested_pages_pdf(tmp_path / "src.pdf"))
    data = pages.subdocument_bytes(src, [0])
    src.close()
    stack.push(InsertPagesCommand(doc, data, 4, 1))
    assert _letters(doc) == "EFAACD"
    stack.push(DeletePagesCommand(doc, [3]))  # A (the inserted copy stays in N1)
    assert _letters(doc) == "EFACD"
    with doc.lock:
        depth, nodes, empty = _page_tree(doc.fitz)
    assert (depth, nodes, empty) == (2, 3, [])
    doc.save()
    _check_file(nested_pages_pdf, "EFACD")
    while stack.canUndo():
        stack.undo()
    assert _letters(doc) == NESTED_LETTERS
    with doc.lock:
        assert _page_tree(doc.fitz) == (3, 4, [])
    while stack.canRedo():
        stack.redo()
    assert _letters(doc) == "EFACD"
    doc.save()
    _check_file(nested_pages_pdf, "EFACD")
    doc.close()


def test_nested_tree_extract_and_split(nested_pages_pdf, tmp_path) -> None:
    doc = PdfDocument.open(nested_pages_pdf)
    out = tmp_path / "ef.pdf"
    doc.extract_pages([4, 5], out)
    _check_file(out, "EF")
    paths = [tmp_path / f"part-{n}.pdf" for n in range(3)]
    doc.split_document(pages.split_every(6, 2), paths)
    for path, letters in zip(paths, ("AB", "CD", "EF"), strict=True):
        _check_file(path, letters)
    assert _letters(doc) == NESTED_LETTERS  # the document itself is untouched
    doc.close()


def test_prune_empty_page_nodes(nested_pages_pdf) -> None:
    f = pymupdf.open(nested_pages_pdf)
    f.delete_pages([4, 5])  # MuPDF leaves N3 as /Kids [] /Count 0
    assert len(_page_tree(f)[2]) == 1
    assert pages.prune_empty_page_nodes(f) == 1
    assert _page_tree(f) == (2, 3, [])
    assert pages.prune_empty_page_nodes(f) == 0
    assert "".join(p.get_text().strip() for p in f) == "ABCD"
    f.close()


# -- overlays across structural changes ----------------------------------------------
def test_selection_follows_its_page_across_structure_changes(qtbot, dv, annotated_pdf) -> None:
    doc = dv.open(str(annotated_pdf))
    sel = dv.annot_selection
    changes: list[int | None] = []
    sel.changed.connect(lambda: changes.append(None if sel.current is None else sel.current.page))
    sel.select(doc.annot(1, ROTATED_ANNOT_NAME))
    pid = doc.page_id(1)
    dv.push(DeletePagesCommand(doc, [0]))
    assert sel.current is not None and sel.current.page == 0
    assert sel.current.name == ROTATED_ANNOT_NAME
    assert sel.item is not None and sel.item.parentItem() is dv.page_view.page_item(0)
    dv.undo_stack.undo()  # reopened from the snapshot: the real /NM is found again
    assert sel.current is not None and sel.current.page == doc.page_index(pid) == 1
    dv.push(InsertBlankPageCommand(doc, 0, QSizeF(100, 100)))
    assert sel.current.page == 2
    dv.push(MovePagesCommand(doc, [2], 0))
    assert sel.current.page == 0
    assert sel.item.parentItem() is dv.page_view.page_item(0)
    assert changes == [1, 0, 1, 2, 0]
    dv.push(DeletePagesCommand(doc, [0]))  # the selected annotation's own page
    assert sel.current is None and sel.item is None and changes[-1] is None


def test_synthetic_selection_cleared_by_snapshot_restore(qtbot, dv, annotated_pdf) -> None:
    doc = dv.open(str(annotated_pdf))
    sel = dv.annot_selection
    foreign = next(a for a in doc.annots(0) if a.text == FOREIGN_TEXT)
    assert is_synthetic(foreign.name)
    sel.select(foreign)
    dv.push(DeletePagesCommand(doc, [1]))  # same load: the synthetic name still resolves
    assert sel.current is not None and sel.current.page == 0
    dv.undo_stack.undo()  # the snapshot is a new load: synthetic names are stale
    assert sel.current is None and sel.item is None
    assert doc.page_count == 2


def _text_widget(doc: PdfDocument, page: int):
    return next(w for w in doc.widgets(page) if w.kind is FieldKind.TEXT and w.editable)


def _type(editor, text: str) -> None:
    if isinstance(editor, QPlainTextEdit):
        editor.setPlainText(text)
    else:
        assert isinstance(editor, QLineEdit)
        editor.setText(text)


def test_field_editor_commits_to_remapped_page(qtbot, dv, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    editor = dv.field_editor
    committed: list[tuple[object, object]] = []
    editor.committed.connect(lambda info, value: committed.append((info, value)))
    widget = _text_widget(doc, 1)
    editor.open(widget)
    _type(editor.editor, "Rossi")
    doc.insert_blank_page(0, QSizeF(100, 100))
    assert not editor.is_open
    [(info, value)] = committed
    assert value == "Rossi" and info.page == 2 and info.name == widget.name
    cmd = SetFieldValueCommand(doc, info, value)  # what the form tool pushes
    cmd.apply_now()
    assert next(w.value for w in doc.widgets(2) if w.name == widget.name) == "Rossi"


def test_field_editor_closes_when_its_page_is_deleted(qtbot, dv, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    editor = dv.field_editor
    committed: list[object] = []
    cancelled: list[int] = []
    editor.committed.connect(lambda info, value: committed.append(value))
    editor.cancelled.connect(lambda: cancelled.append(1))
    editor.open(_text_widget(doc, 1))
    _type(editor.editor, "lost")
    doc.delete_pages([1])
    assert not editor.is_open and committed == [] and cancelled == []
    assert doc.page_count == 1


def test_annot_editor_commits_remapped_anchor(qtbot, dv, annotated_pdf) -> None:
    doc = dv.open(str(annotated_pdf))
    editor = dv.annot_editor
    committed: list[tuple[object, str]] = []
    editor.committed.connect(lambda anchor, text: committed.append((anchor, text)))
    editor.open_existing(doc.annot(1, ROTATED_ANNOT_NAME))
    _type(editor.editor, "moved text")
    doc.delete_pages([0])
    [(anchor, text)] = committed
    assert text == "moved text"
    assert anchor.page == 0 and anchor.info.page == 0 and anchor.name == ROTATED_ANNOT_NAME
    assert doc.annot(0, ROTATED_ANNOT_NAME) is not None


# -- signature images ----------------------------------------------------------------
def test_signature_images_shared_after_page_operations(signed_pdf, tmp_path) -> None:
    doc = PdfDocument.open(signed_pdf)
    stack = QUndoStack()
    with doc.lock:
        images = _image_objects(doc.fitz)
    shared = doc.annot(0, SIGNED_NAME).image_xref
    assert shared and doc.annot(0, LOCKED_SIGNATURE_NAME).image_xref == shared
    sharing = [a.name for a in doc.annots(0) if a.image_xref == shared]
    distinct = len({a.image_xref for a in doc.annots(0) if a.image_xref})
    assert len(sharing) >= 2
    samples = doc.annot_image(0, SIGNED_NAME)
    pid = doc.page_id(0)
    stack.push(MovePagesCommand(doc, [1], 0))
    stack.push(InsertBlankPageCommand(doc, 0, QSizeF(300, 300)))
    stack.push(DeletePagesCommand(doc, [0]))
    stack.undo()  # reopened from the snapshot
    page = doc.page_index(pid)
    assert page == 2
    assert doc.annot(page, SIGNED_NAME).image_xref == shared
    add = AddAnnotCommand(
        doc,
        AnnotSpec(
            page, AnnotKind.SIGNATURE, "", 11, BLACK, QRectF(100, 400, 200, 80), image=samples
        ),
    )
    stack.push(add)
    assert doc.annot(page, add.name).image_xref == shared
    with doc.lock:
        assert _image_objects(doc.fitz) == images  # no new image object
    doc.save()  # full: structure changed
    reopened = PdfDocument.open(signed_pdf)
    again = reopened.annot(2, SIGNED_NAME).image_xref
    now_sharing = [a.name for a in reopened.annots(2) if a.image_xref == again]
    assert sorted(now_sharing) == sorted([*sharing, add.name])
    assert len({a.image_xref for a in reopened.annots(2) if a.image_xref}) == distinct
    with reopened.lock:
        assert len(_image_objects(reopened.fitz)) == len(images)
    out = tmp_path / "signed-page.pdf"
    reopened.extract_pages([2], out)
    strict_read(out)
    with pymupdf.open(out) as f:
        # Our shared RGB image is written once (the MuPDF stamp's image uses an ICC space).
        assert len(_image_objects(f)) == 1
    reopened.close()
    doc.close()
