"""M6a-T5: Pages menu, page dialogs and their wiring in MainWindow (dialogs patched)."""

from __future__ import annotations

import os
import shutil

import pymupdf
import pytest
from fixtures import PASSWORD, make_simple_pdf
from pdfcheck import strict_read
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from pdfeditor.core.commands import MovePagesCommand
from pdfeditor.core.forms import XfaKind
from pdfeditor.i18n import install_translators, remove_translators
from pdfeditor.ui import dialogs, page_dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.page_dialogs import InsertPagesDialog, SplitDialog


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        dialogs, "warn", lambda parent, title, text, details=None: warnings.append(text)
    )
    # pytest-qt closes the window before this fixture's teardown: never ask to save.
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


def _open(window: MainWindow, path, monkeypatch=None, password: str | None = None):
    if password is not None:
        monkeypatch.setattr(dialogs, "ask_password", lambda *_a: password)
    assert window.open_file(str(path))
    return window.document_view.document


def _texts(doc) -> list[str]:
    with doc.lock:
        return [doc.fitz[i].get_text().strip() for i in range(doc.page_count)]


def _status(window: MainWindow) -> str:
    return window.statusBar().currentMessage()


def _one_page_pdf(path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Only page")
    doc.save(path)
    doc.close()
    return path


def _accepting(dialog_cls, monkeypatch, setup=None):
    """Make ``dialog_cls.exec`` run ``setup(dialog)`` then accept."""

    def fake_exec(self):
        if setup is not None:
            setup(self)
        self.accept()
        return self.result()

    monkeypatch.setattr(dialog_cls, "exec", fake_exec)


# -- menu and enablement --------------------------------------------------------------
def test_pages_menu_between_edit_and_view(window: MainWindow) -> None:
    titles = [a.text() for a in window.menuBar().actions()]
    assert titles.index("&Pages") == titles.index("&Edit") + 1
    assert titles.index("&View") == titles.index("&Pages") + 1
    acts = [a for a in window.menu_pages.actions() if not a.isSeparator()]
    assert acts == [
        window.act_insert_blank,
        window.act_insert_pages,
        window.act_delete_pages,
        window.act_rotate_cw,
        window.act_rotate_ccw,
        window.act_extract_pages,
        window.act_split,
    ]
    assert window.act_rotate_cw not in window.menu_edit.actions()
    shortcuts = {a.objectName(): a.shortcut().toString() for a in acts}
    assert shortcuts["insert_blank_page"] == "Ctrl+Shift+N"
    assert shortcuts["insert_pages"] == "Ctrl+Shift+I"
    assert shortcuts["delete_pages"] == "Ctrl+Shift+Del"
    assert shortcuts["extract_pages"] == "Ctrl+Shift+E"
    # "E" stays free (reserved for M7's Edit Page Text tool).
    every = [s.toString() for a in window.findChildren(type(acts[0])) for s in a.shortcuts()]
    assert "E" not in every


def _page_actions(w: MainWindow):
    return (
        w.act_insert_blank,
        w.act_insert_pages,
        w.act_delete_pages,
        w.act_rotate_cw,
        w.act_rotate_ccw,
        w.act_extract_pages,
        w.act_split,
    )


def test_actions_disabled_without_document(window: MainWindow) -> None:
    assert not any(a.isEnabled() for a in _page_actions(window))


def test_actions_enabled_with_document(window: MainWindow, simple_pdf) -> None:
    _open(window, simple_pdf)
    assert all(a.isEnabled() for a in _page_actions(window))


def test_actions_disabled_without_assemble_permission(window: MainWindow, owner_locked_pdf) -> None:
    doc = _open(window, owner_locked_pdf)
    assert not doc.can_assemble
    for act in (window.act_insert_blank, window.act_delete_pages, window.act_rotate_cw):
        assert not act.isEnabled()
    window.thumbnails.pages_delete_requested.emit([0])
    window.thumbnails.pages_move_requested.emit([0], 1)
    assert doc.page_count == 1 and window.undo_stack.count() == 0
    assert "security settings" in _status(window)


def test_extract_disabled_without_copy_permission(
    window: MainWindow, encrypted_pdf, monkeypatch
) -> None:
    doc = _open(window, encrypted_pdf, monkeypatch, PASSWORD)
    assert not doc.can_extract
    assert not window.act_extract_pages.isEnabled() and not window.act_split.isEnabled()
    assert not window.extract_pages([0])
    assert "Copying pages is not permitted" in _status(window)


def test_actions_disabled_for_dynamic_xfa(window: MainWindow, dynamic_xfa_pdf) -> None:
    doc = _open(window, dynamic_xfa_pdf)
    assert doc.xfa_kind is XfaKind.DYNAMIC
    assert not any(a.isEnabled() for a in _page_actions(window))


# -- delete -------------------------------------------------------------------------
def test_delete_current_page(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    window.page_view.scroll_to_page(1)
    window.act_delete_pages.trigger()
    assert _texts(doc) == ["Page 1", "Page 3"]
    assert window.isWindowModified()
    assert window.act_undo.text() == "Undo Delete page"
    assert window.page_spin.maximum() == 2
    assert window.page_total_label.text() == " / 2 "
    window.act_undo.trigger()
    assert _texts(doc) == ["Page 1", "Page 2", "Page 3"]
    assert window.page_spin.maximum() == 3


def test_delete_thumbnail_selection(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    window.thumbnails.select_pages([0, 2])
    assert window.target_pages() == [0, 2]
    window.act_delete_pages.trigger()
    assert _texts(doc) == ["Page 2"]
    assert window.act_undo.text() == "Undo Delete pages"


def test_delete_key_in_sidebar_and_undo_redo(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    window.thumbnails.pages_delete_requested.emit([1])
    assert doc.page_count == 2
    window.act_undo.trigger()
    assert doc.page_count == 3
    window.act_redo.trigger()
    assert _texts(doc) == ["Page 1", "Page 3"]


def test_delete_refuses_last_page(window: MainWindow, tmp_path) -> None:
    doc = _open(window, _one_page_pdf(tmp_path / "one.pdf"))
    assert not window.delete_pages()
    assert doc.page_count == 1
    assert window.undo_stack.count() == 0
    assert _status(window) == "A document must keep at least one page."
    assert not window.isWindowModified()


def test_delete_all_selected_refused(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    assert not window.delete_pages([0, 1, 2])
    assert doc.page_count == 3 and window.undo_stack.count() == 0


# -- move (drag and drop) -------------------------------------------------------------
def test_drag_pushes_one_move_command(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    window.thumbnails.pages_move_requested.emit([0, 1], 3)
    assert _texts(doc) == ["Page 3", "Page 1", "Page 2"]
    assert window.undo_stack.count() == 1
    assert isinstance(window.undo_stack.command(0), MovePagesCommand)
    assert window.isWindowModified()
    assert window.windowTitle() == "simple.pdf[*] — PDF Editor"
    window.act_undo.trigger()
    assert _texts(doc) == ["Page 1", "Page 2", "Page 3"]
    assert not window.isWindowModified()


def test_noop_drag_pushes_nothing(window: MainWindow, simple_pdf) -> None:
    _open(window, simple_pdf)
    window.thumbnails.pages_move_requested.emit([1], 1)
    window.thumbnails.pages_move_requested.emit([1], 2)
    assert window.undo_stack.count() == 0


# -- rotate -------------------------------------------------------------------------
def test_rotate_selection_one_step(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    window.thumbnails.select_pages([0, 2])
    window.act_rotate_cw.trigger()
    assert [doc.page_rotation(i) for i in range(3)] == [90, 0, 90]
    window.act_rotate_cw.trigger()
    assert [doc.page_rotation(i) for i in range(3)] == [180, 0, 180]
    assert window.undo_stack.count() == 1
    assert window.act_undo.text() == "Undo Rotate pages"
    window.act_undo.trigger()
    assert [doc.page_rotation(i) for i in range(3)] == [0, 0, 0]


# -- insert blank ---------------------------------------------------------------------
def test_insert_blank_after_current_with_its_size(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    window.page_view.scroll_to_page(1)
    letter = doc.page_size(1)
    window.act_insert_blank.trigger()
    assert doc.page_count == 4
    assert _texts(doc) == ["Page 1", "Page 2", "", "Page 3"]
    assert doc.page_size(2) == letter
    assert window.page_view.current_page == 2
    assert window.act_undo.text() == "Undo Insert blank page"
    window.act_undo.trigger()
    assert _texts(doc) == ["Page 1", "Page 2", "Page 3"]


def test_full_save_after_page_op_then_incremental(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    window.insert_blank_page()
    assert not doc.can_save_incrementally()
    assert window.save()
    assert doc.can_save_incrementally()
    with pymupdf.open(simple_pdf) as reopened:
        assert reopened.page_count == 4


def test_save_strips_static_xfa_after_page_op(window: MainWindow, static_xfa_pdf) -> None:
    doc = _open(window, static_xfa_pdf)
    assert doc.xfa_kind is XfaKind.STATIC
    assert window.insert_blank_page()
    assert doc.xfa_kind is XfaKind.STATIC  # until the save
    assert window.save()
    assert doc.xfa_kind is XfaKind.NONE
    with pymupdf.open(static_xfa_pdf) as reopened:
        assert reopened.page_count == 2
        acro = reopened.xref_get_key(reopened.pdf_catalog(), "AcroForm")
        assert acro[0] == "xref"
        xref = int(acro[1].split()[0])
        assert reopened.xref_get_key(xref, "XFA")[0] == "null"
    strict_read(static_xfa_pdf)


# -- insert pages from file -------------------------------------------------------------
def test_insert_dialog_range_and_position(qtbot, tmp_path, simple_pdf, settings) -> None:
    from pdfeditor.core.document import PdfDocument

    other = make_simple_pdf(tmp_path / "other.pdf")
    doc = PdfDocument.open(str(simple_pdf))
    try:
        dialog = InsertPagesDialog(doc, 1, settings)
        qtbot.addWidget(dialog)
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert not ok.isEnabled()
        assert dialog.set_path(str(other))
        assert dialog.count_label.text() == "3 pages"
        assert ok.isEnabled()
        assert dialog.insert_index() == 2  # after the current page by default
        dialog.set_position(page_dialogs.BEFORE_CURRENT)
        assert dialog.insert_index() == 1
        dialog.set_position(page_dialogs.AT_END)
        assert dialog.insert_index() == 3
        dialog.range_radio.setChecked(True)
        dialog.range_edit.setText("3, 1-2, x")
        assert not ok.isEnabled()
        assert dialog.error_label.text() == "Invalid page range: “x”"
        dialog.range_edit.setText("3, 1")
        assert ok.isEnabled()
        dialog.accept()
        request = dialog.request
        assert request is not None
        assert (request.index, request.count) == (3, 2)
        with pymupdf.open(stream=request.data, filetype="pdf") as sub:
            assert [p.get_text().strip() for p in sub] == ["Page 3", "Page 1"]
        assert settings.last_open_dir == str(tmp_path)
    finally:
        doc.close()


def test_insert_dialog_refuses_open_document(qtbot, simple_pdf, settings) -> None:
    from pdfeditor.core.document import PdfDocument

    doc = PdfDocument.open(str(simple_pdf))
    try:
        dialog = InsertPagesDialog(doc, 0, settings)
        qtbot.addWidget(dialog)
        assert not dialog.set_path(str(simple_pdf))
        assert dialog.error_label.text() == "The document cannot be inserted into itself."
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    finally:
        doc.close()


def test_insert_dialog_encrypted_source(qtbot, simple_pdf, tmp_path, settings, monkeypatch) -> None:
    from pdfeditor.core.document import PdfDocument

    # A user password and the copy permission (a source forbidding copying is refused).
    encrypted_pdf = tmp_path / "encrypted_copyable.pdf"
    src = pymupdf.open()
    src.new_page()
    src.new_page()
    src.save(
        encrypted_pdf,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=PASSWORD,
        owner_pw="owner-" + PASSWORD,
        permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY,
    )
    src.close()
    answers = iter(["wrong", PASSWORD])
    asked: list[bool] = []

    def ask(_parent, name, wrong):
        asked.append(wrong)
        return next(answers)

    monkeypatch.setattr(dialogs, "ask_password", ask)
    doc = PdfDocument.open(str(simple_pdf))
    try:
        dialog = InsertPagesDialog(doc, 0, settings)
        qtbot.addWidget(dialog)
        assert dialog.set_path(str(encrypted_pdf))
        assert asked == [False, True]
        assert dialog.source_page_count == 2
        dialog.accept()
        with pymupdf.open(stream=dialog.request.data, filetype="pdf") as sub:
            assert not sub.needs_pass and sub.page_count == 2
        # Cancelled prompt: explained, OK disabled.
        monkeypatch.setattr(dialogs, "ask_password", lambda *_a: None)
        dialog2 = InsertPagesDialog(doc, 0, settings)
        qtbot.addWidget(dialog2)
        assert not dialog2.set_path(str(encrypted_pdf))
        assert "protected by a password" in dialog2.error_label.text()
    finally:
        doc.close()


def test_insert_pages_through_window(window: MainWindow, simple_pdf, tmp_path, monkeypatch) -> None:
    other = make_simple_pdf(tmp_path / "other.pdf")
    doc = _open(window, simple_pdf)
    window.page_view.scroll_to_page(0)
    monkeypatch.setattr(dialogs, "get_open_path", lambda *_a: str(other))

    def setup(dialog):
        dialog.range_radio.setChecked(True)
        dialog.range_edit.setText("2-3")
        dialog.set_position(page_dialogs.AT_END)

    _accepting(InsertPagesDialog, monkeypatch, setup)
    assert window.act_insert_pages.isEnabled()
    window.act_insert_pages.trigger()
    assert _texts(doc) == ["Page 1", "Page 2", "Page 3", "Page 2", "Page 3"]
    assert _status(window) == "Inserted 2 pages"
    assert window.page_view.current_page == 3  # first inserted page
    assert window.act_undo.text() == "Undo Insert pages"
    window.act_undo.trigger()
    assert doc.page_count == 3
    window.act_redo.trigger()
    assert doc.page_count == 5
    assert window.save()
    strict_read(simple_pdf)


def test_insert_pages_cancelled(window: MainWindow, simple_pdf, monkeypatch) -> None:
    doc = _open(window, simple_pdf)
    monkeypatch.setattr(dialogs, "get_open_path", lambda *_a: None)
    assert not window.insert_pages()
    assert doc.page_count == 3 and window.undo_stack.count() == 0


def test_insert_colliding_fields_notice(
    window: MainWindow, lo_form_pdf, tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source.pdf"
    shutil.copy(lo_form_pdf, source)
    doc = _open(window, lo_form_pdf)
    monkeypatch.setattr(
        page_dialogs,
        "insert_pages",
        lambda parent, document, current, settings: _request(document, source),
    )
    assert window.insert_pages()
    assert doc.page_count == 4
    assert "renamed" in _status(window)


def _request(document, path):
    from pdfeditor.core import pages

    src, _pw = pages.open_source(str(path))
    try:
        count = src.page_count
        data = pages.subdocument_bytes(src, list(range(count)))
    finally:
        src.close()
    return page_dialogs.InsertRequest(data=data, index=document.page_count, count=count)


# -- extract --------------------------------------------------------------------------
def test_extract_selection(window: MainWindow, simple_pdf, tmp_path, monkeypatch) -> None:
    doc = _open(window, simple_pdf)
    target = tmp_path / "out" / "extract.pdf"
    target.parent.mkdir()
    suggested: list[str] = []

    def get_path(_parent, path):
        suggested.append(path)
        return str(target)

    monkeypatch.setattr(dialogs, "get_extract_path", get_path)
    window.thumbnails.select_pages([0, 2])
    before = simple_pdf.read_bytes()
    window.act_extract_pages.trigger()
    assert os.path.basename(suggested[0]) == "simple - pages.pdf"
    strict_read(target)
    with pymupdf.open(target) as out:
        assert [p.get_text().strip() for p in out] == ["Page 1", "Page 3"]
    assert _status(window) == "Extracted 2 pages to “extract.pdf”"
    assert simple_pdf.read_bytes() == before
    assert doc.page_count == 3 and window.undo_stack.count() == 0
    assert not window.isWindowModified()


def test_extract_keeps_user_password(
    window: MainWindow, lo_form_full_access_pdf, tmp_path, monkeypatch
) -> None:
    _open(window, lo_form_full_access_pdf, monkeypatch, PASSWORD)
    target = tmp_path / "extract.pdf"
    monkeypatch.setattr(dialogs, "get_extract_path", lambda *_a: str(target))
    assert window.extract_pages([1])
    with pymupdf.open(target) as out:
        assert out.needs_pass
        assert out.authenticate(PASSWORD)
        assert out.page_count == 1
    strict_read(target, PASSWORD)


def test_extract_refuses_open_document(window: MainWindow, simple_pdf, monkeypatch) -> None:
    _open(window, simple_pdf)
    before = simple_pdf.read_bytes()
    monkeypatch.setattr(dialogs, "get_extract_path", lambda *_a: str(simple_pdf))
    assert not window.extract_pages([0])
    assert window.warnings == ["Choose another name: the copy cannot replace the open document."]
    assert simple_pdf.read_bytes() == before


def test_extract_write_failure(window: MainWindow, simple_pdf, tmp_path, monkeypatch) -> None:
    _open(window, simple_pdf)
    target = tmp_path / "missing-dir" / "x.pdf"
    monkeypatch.setattr(dialogs, "get_extract_path", lambda *_a: str(target))
    assert not window.extract_pages([0])
    assert window.warnings == ["The pages could not be written."]


# -- split ----------------------------------------------------------------------------
def test_split_dialog(qtbot, tmp_path) -> None:
    dialog = SplitDialog(5, str(tmp_path), "report")
    qtbot.addWidget(dialog)
    dialog.every_spin.setValue(2)
    groups, paths = dialog.result_value()
    assert groups == [[0, 1], [2, 3], [4]]
    assert [os.path.basename(p) for p in paths] == [
        "report-01.pdf",
        "report-02.pdf",
        "report-03.pdf",
    ]
    assert dialog.summary_label.text() == "3 files will be written: report-01.pdf … report-03.pdf"
    dialog.ranges_radio.setChecked(True)
    dialog.ranges_edit.setText("1-2, 3-")
    assert dialog.groups() == [[0, 1], [2, 3, 4]]
    dialog.ranges_edit.setText("1-2, 9")
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    assert dialog.summary_label.text() == "Invalid page range: “9”"
    dialog.ranges_edit.setText("1-2")
    assert ok.isEnabled()
    dialog.base_edit.setText("")
    assert not ok.isEnabled()


def test_split_through_window(window: MainWindow, simple_pdf, tmp_path, monkeypatch) -> None:
    doc = _open(window, simple_pdf)
    out = tmp_path / "split"
    out.mkdir()
    window.settings.last_open_dir = str(out)

    def setup(dialog):
        assert dialog.folder_edit.text() == str(out)
        assert dialog.base_edit.text() == "simple"
        dialog.every_spin.setValue(2)

    _accepting(SplitDialog, monkeypatch, setup)
    window.act_split.trigger()
    first, second = out / "simple-01.pdf", out / "simple-02.pdf"
    for path, expected in ((first, ["Page 1", "Page 2"]), (second, ["Page 3"])):
        strict_read(path)
        with pymupdf.open(path) as part:
            assert [p.get_text().strip() for p in part] == expected
    assert _status(window) == f"Split into 2 files in “{os.path.normpath(out)}”"
    assert doc.page_count == 3 and window.undo_stack.count() == 0


def test_split_confirms_overwrite(window: MainWindow, simple_pdf, tmp_path, monkeypatch) -> None:
    _open(window, simple_pdf)
    existing = tmp_path / "simple-01.pdf"
    existing.write_bytes(b"old")
    monkeypatch.setattr(
        page_dialogs,
        "split_document",
        lambda *_a: ([[0], [1, 2]], [str(existing), str(tmp_path / "simple-02.pdf")]),
    )
    asked: list[list[str]] = []
    monkeypatch.setattr(
        dialogs, "confirm_overwrite_files", lambda _p, paths: asked.append(paths) or False
    )
    assert not window.split_document()
    assert asked == [[str(existing)]]
    assert existing.read_bytes() == b"old"
    assert not (tmp_path / "simple-02.pdf").exists()
    monkeypatch.setattr(dialogs, "confirm_overwrite_files", lambda _p, paths: True)
    assert window.split_document()
    with pymupdf.open(existing) as part:
        assert part.page_count == 1


# -- context menu, French ----------------------------------------------------------------
def test_context_menu_acts_on_rows(window: MainWindow, simple_pdf) -> None:
    doc = _open(window, simple_pdf)
    menu = window.page_context_menu([0, 1])
    names = [a.objectName() for a in menu.actions() if not a.isSeparator()]
    assert names == [
        "insert_blank_page",
        "insert_pages",
        "delete_pages",
        "rotate_cw",
        "rotate_ccw",
        "extract_pages",
        "split_document",
    ]
    rotate = next(a for a in menu.actions() if a.objectName() == "rotate_ccw")
    rotate.trigger()
    assert [doc.page_rotation(i) for i in range(3)] == [270, 270, 0]
    delete = next(a for a in menu.actions() if a.objectName() == "delete_pages")
    delete.trigger()
    assert _texts(doc) == ["Page 3"]
    menu.deleteLater()


def test_context_menu_inserts_next_to_clicked_row(
    window: MainWindow, simple_pdf, tmp_path, monkeypatch
) -> None:
    doc = _open(window, simple_pdf)
    window.page_view.scroll_to_page(0)  # the current page is not the clicked one
    menu = window.page_context_menu([1])
    blank = next(a for a in menu.actions() if a.objectName() == "insert_blank_page")
    blank.trigger()
    assert _texts(doc) == ["Page 1", "Page 2", "", "Page 3"]
    assert window.page_view.current_page == 2
    menu.deleteLater()

    other = make_simple_pdf(tmp_path / "other.pdf")
    monkeypatch.setattr(dialogs, "get_open_path", lambda *_a: str(other))
    _accepting(
        InsertPagesDialog, monkeypatch, lambda d: d.set_position(page_dialogs.BEFORE_CURRENT)
    )
    window.page_view.scroll_to_page(0)
    menu = window.page_context_menu([2, 3])  # multi-selection: next to its last page
    insert = next(a for a in menu.actions() if a.objectName() == "insert_pages")
    insert.trigger()
    assert _texts(doc) == ["Page 1", "Page 2", "", "Page 1", "Page 2", "Page 3", "Page 3"]
    menu.deleteLater()


def test_context_menu_signal_wired(window: MainWindow, simple_pdf, monkeypatch) -> None:
    _open(window, simple_pdf)
    shown: list[list[int]] = []
    monkeypatch.setattr(window, "page_context_menu", lambda rows: shown.append(rows) or _NoMenu())
    window.thumbnails.context_menu_requested.emit([2], window.mapToGlobal(window.rect().center()))
    assert shown == [[2]]


class _NoMenu:
    def exec(self, _pos):
        return None

    def deleteLater(self):  # noqa: N802 - Qt name
        pass


def test_pages_menu_in_french(qtbot, qapp, settings) -> None:
    install_translators(qapp, "fr")
    try:
        w = MainWindow(settings)
        qtbot.addWidget(w)
        assert w.menu_pages.title() == "&Pages"
        assert w.act_insert_blank.text() == "Insérer une page &vierge"
        assert w.act_insert_pages.text() == "Insérer des pages depuis un &fichier…"
        assert w.act_delete_pages.text() == "&Supprimer les pages"
        assert w.act_extract_pages.text() == "&Extraire les pages…"
        assert w.act_split.text() == "&Scinder le document…"
        rows = dict(w.shortcut_sections()[-1][1])
        assert rows["Réordonner les pages"] == "Glisser les vignettes"
        dialog = SplitDialog(4, "", "x")
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Scinder le document"
        assert dialog.every_radio.text() == "Toutes les"
        assert dialog.every_suffix.text() == "pages"
    finally:
        remove_translators(qapp)
