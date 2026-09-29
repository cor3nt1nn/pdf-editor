from __future__ import annotations

import pytest
from fixtures import PASSWORD
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDropEvent, QUndoCommand
from PySide6.QtWidgets import QMessageBox

import pdfeditor.app as app_module
from pdfeditor.ui import dialogs
from pdfeditor.ui import main_window as mw_module
from pdfeditor.ui.main_window import MainWindow


class Dummy(QUndoCommand):
    def __init__(self) -> None:
        super().__init__("dummy")

    def redo(self) -> None:
        pass

    def undo(self) -> None:
        pass


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        dialogs, "warn", lambda parent, title, text, details=None: warnings.append(text)
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


def _drop(window: MainWindow, paths: list[str]) -> QDropEvent:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    event = QDropEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)
    return event


def test_argv_opens_file(qtbot, settings, simple_pdf, monkeypatch) -> None:
    created: list[MainWindow] = []

    class Capturing(MainWindow):
        def __init__(self, s):
            super().__init__(settings)
            created.append(self)

    def fake_exec():
        qtbot.waitUntil(lambda: created and created[0].document_view.document is not None)
        return 0

    monkeypatch.setattr(mw_module, "MainWindow", Capturing)
    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module.QApplication, "exec", staticmethod(fake_exec))
    assert app_module.main(["pdfeditor", str(simple_pdf)]) == 0
    w = created[0]
    qtbot.addWidget(w)
    assert w.document_view.document.page_count == 3
    assert w.windowTitle().startswith("simple.pdf")
    w.close()


def test_title_and_status(qtbot, window: MainWindow, simple_pdf) -> None:
    assert window.windowTitle() == "PDF Editor"
    assert window.open_file(str(simple_pdf))
    assert window.windowTitle() == "simple.pdf[*] — PDF Editor"
    assert window.status_file.text() == "simple.pdf"
    assert window.status_page.text() == "Page 1 / 3"
    assert window.page_total_label.text().strip() == "/ 3"
    window.page_view.set_zoom_percent(125)
    assert window.status_zoom.text() == "125 %"
    assert window.zoom_widget.currentText() == "125 %"
    window.page_view.scroll_to_page(1)
    assert window.status_page.text() == "Page 2 / 3"
    assert window.page_spin.value() == 2


def test_page_spinbox_navigates(qtbot, window: MainWindow, simple_pdf) -> None:
    window.open_file(str(simple_pdf))
    window.page_view.set_zoom_percent(100)
    window.page_spin.setValue(3)
    assert window.page_view.current_page == 2
    assert window.status_page.text() == "Page 3 / 3"
    assert not window.act_next_page.isEnabled()
    window.act_prev_page.trigger()
    assert window.page_view.current_page == 1


def test_zoom_widget_requests(qtbot, window: MainWindow, simple_pdf, settings) -> None:
    window.open_file(str(simple_pdf))
    window.zoom_widget.zoom_requested.emit(150.0)
    assert window.page_view.zoom_percent == 150
    assert settings.zoom_percent == 150
    window.act_fit_page.trigger()
    assert window.page_view.zoom_mode.value == "fit_page"
    window.zoom_widget.setEditText("200")
    window.zoom_widget.lineEdit().returnPressed.emit()
    assert window.page_view.zoom_percent == 200


def test_drop_pdf_opens_and_rejects_non_pdf(qtbot, window: MainWindow, simple_pdf, tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    event = _drop(window, [txt])
    assert not event.isAccepted()
    assert window.document_view.document is None

    upper = simple_pdf.with_name("UPPER.PDF")
    upper.write_bytes(simple_pdf.read_bytes())
    event = _drop(window, [txt, upper])
    assert event.isAccepted()
    assert window.document_view.file_name == "UPPER.PDF"


def test_wrong_password_loop_then_cancel(qtbot, window, encrypted_pdf, simple_pdf, monkeypatch):
    calls: list[bool] = []
    answers = iter(["bad", "worse", None])

    def fake_ask(parent, name, wrong):
        calls.append(wrong)
        return next(answers)

    monkeypatch.setattr(dialogs, "ask_password", fake_ask)
    assert not window.open_file(str(encrypted_pdf))
    assert calls == [False, True, True]
    assert window.document_view.document is None
    assert window.warnings == []
    # still usable
    assert window.open_file(str(simple_pdf))
    monkeypatch.setattr(dialogs, "ask_password", lambda p, n, w: PASSWORD)
    assert window.open_file(str(encrypted_pdf))
    assert window.document_view.document.is_encrypted


def test_open_errors_show_dialog(qtbot, window: MainWindow, tmp_path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert not window.open_file(str(empty))
    assert not window.open_file(str(tmp_path / "missing.pdf"))
    assert len(window.warnings) == 2
    assert window.windowTitle() == "PDF Editor"


def test_dirty_close_prompts(qtbot, window: MainWindow, simple_pdf, monkeypatch) -> None:
    window.open_file(str(simple_pdf))
    window.undo_stack.push(Dummy())
    assert window.isWindowModified()
    answers = [QMessageBox.StandardButton.Cancel]
    asked: list[str] = []

    def fake_confirm(parent, name):
        asked.append(name)
        return answers.pop(0)

    monkeypatch.setattr(dialogs, "confirm_save_changes", fake_confirm)
    window.close()
    assert asked == ["simple.pdf"]
    assert window.isVisible()

    answers.append(QMessageBox.StandardButton.Discard)
    assert window.close_document()
    assert window.document_view.document is None

    window.open_file(str(simple_pdf))
    window.undo_stack.push(Dummy())
    answers.append(QMessageBox.StandardButton.Save)
    window.close()
    assert not window.isVisible()
    assert window.undo_stack.isClean()


def test_save_failure_offers_save_as(qtbot, window, simple_pdf, tmp_path, monkeypatch):
    from pdfeditor.core.document import SaveError

    window.open_file(str(simple_pdf))
    window.undo_stack.push(Dummy())

    def boom():
        raise SaveError("locked")

    monkeypatch.setattr(window.document_view.document, "save", boom)
    offered: list[str] = []
    monkeypatch.setattr(dialogs, "offer_save_as", lambda p, e: offered.append(e) or True)
    target = tmp_path / "saved-as.pdf"
    monkeypatch.setattr(dialogs, "get_save_path", lambda p, s: str(target))
    assert window.save()
    assert offered == ["locked"]
    assert target.exists()
    assert window.document_view.file_name == "saved-as.pdf"
    assert window.undo_stack.isClean()


def _rewrite_externally(path, source) -> None:
    with open(path, "r+b") as f:
        f.truncate(0)
        f.write(source.read_bytes())


@pytest.mark.parametrize("answer", ["cancel", "overwrite", "save_as"])
def test_save_warns_when_file_changed_on_disk(
    qtbot, window, simple_pdf, form_pdf, tmp_path, monkeypatch, answer
):
    import pymupdf

    window.open_file(str(simple_pdf))
    window.act_rotate_cw.trigger()
    _rewrite_externally(simple_pdf, form_pdf)
    asked: list[str] = []
    monkeypatch.setattr(
        dialogs, "ask_overwrite_modified", lambda p, name: asked.append(name) or answer
    )
    target = tmp_path / "copy.pdf"
    monkeypatch.setattr(dialogs, "get_save_path", lambda p, s: str(target))
    assert window.save() == (answer != "cancel")
    assert asked == ["simple.pdf"]
    d = pymupdf.open(simple_pdf)
    assert d.page_count == (3 if answer == "overwrite" else 1)
    d.close()
    assert target.exists() == (answer == "save_as")
    assert window.isWindowModified() == (answer == "cancel")
    window.undo_stack.setClean()  # qtbot closes the window before fixture teardown


def test_unchanged_file_saves_without_prompt(qtbot, window, simple_pdf, monkeypatch) -> None:
    window.open_file(str(simple_pdf))
    window.act_rotate_cw.trigger()
    monkeypatch.setattr(dialogs, "ask_overwrite_modified", lambda p, n: pytest.fail("asked"))
    assert window.save()
    window.act_rotate_cw.trigger()
    assert window.save()  # our own save refreshed the disk stamp


def test_save_closes_lost_document_cleanly(qtbot, window, simple_pdf, monkeypatch) -> None:
    from pdfeditor.core.document import DocumentError

    window.open_file(str(simple_pdf))
    window.undo_stack.push(Dummy())
    doc = window.document_view.document

    def dies():
        doc.close()
        raise DocumentError("document is closed")

    monkeypatch.setattr(doc, "save", dies)
    monkeypatch.setattr(dialogs, "offer_save_as", lambda p, e: pytest.fail("offered"))
    assert not window.save()
    assert window.document_view.document is None
    assert window.windowTitle() == "PDF Editor"
    assert "had to be closed" in window.warnings[-1]


def test_save_as_error_is_translated_sentence(qtbot, window, simple_pdf, tmp_path, monkeypatch):
    shown: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        dialogs, "warn", lambda p, title, text, details=None: shown.append((text, details))
    )
    window.open_file(str(simple_pdf))
    bad = tmp_path / "no" / "such" / "dir.pdf"
    monkeypatch.setattr(dialogs, "get_save_path", lambda p, s: str(bad))
    assert not window.save_as()
    text, details = shown[-1]
    assert text.startswith("The document could not be saved as “dir.pdf”.")
    assert details  # raw error kept for "Show Details..."
    assert details not in text


def _capture_box(monkeypatch, result):
    boxes: list[QMessageBox] = []

    def fake_exec(box):
        boxes.append(box)
        return result

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return boxes


def test_offer_save_as_hides_raw_error_in_details(qtbot, monkeypatch) -> None:
    boxes = _capture_box(monkeypatch, QMessageBox.StandardButton.Save)
    assert dialogs.offer_save_as(None, "FzErrorSystem: code=2: cannot remove file")
    box = boxes[0]
    assert "FzError" not in box.text()
    assert box.detailedText() == "FzErrorSystem: code=2: cannot remove file"


def test_warn_details(qtbot, monkeypatch) -> None:
    boxes = _capture_box(monkeypatch, QMessageBox.StandardButton.Ok)
    dialogs.warn(None, "Title", "Sentence.", details="raw")
    assert (boxes[0].text(), boxes[0].detailedText()) == ("Sentence.", "raw")


def test_save_dialog_appends_suffix_before_overwrite_check(qtbot, tmp_path, monkeypatch) -> None:
    dialog = dialogs.make_save_dialog(None, str(tmp_path / "doc.pdf"))
    assert dialog.defaultSuffix() == "pdf"
    assert dialog.acceptMode() == dialog.AcceptMode.AcceptSave
    assert not dialog.testOption(dialog.Option.DontConfirmOverwrite)

    existing = tmp_path / "notes.txt.pdf"
    existing.write_bytes(b"x")
    monkeypatch.setattr(dialogs, "_run_save_dialog", lambda p, s: str(tmp_path / "notes.txt"))
    confirms: list[str] = []
    monkeypatch.setattr(dialogs, "confirm_overwrite", lambda p, path: confirms.append(path))
    assert dialogs.get_save_path(None, "") is None  # declined
    assert confirms == [str(existing)]
    monkeypatch.setattr(dialogs, "confirm_overwrite", lambda p, path: True)
    assert dialogs.get_save_path(None, "") == str(existing)
    monkeypatch.setattr(dialogs, "_run_save_dialog", lambda p, s: str(tmp_path / "new"))
    assert dialogs.get_save_path(None, "") == str(tmp_path / "new.pdf")
    monkeypatch.setattr(dialogs, "_run_save_dialog", lambda p, s: None)
    assert dialogs.get_save_path(None, "") is None


def test_close_event_stops_render_worker(qtbot, window: MainWindow, many_pages_pdf, monkeypatch):
    import threading
    import time

    from pdfeditor.core.document import PdfDocument

    real_render = PdfDocument.render
    rendering = threading.Event()

    def slow_render(self, *args, **kwargs):
        rendering.set()
        time.sleep(0.5)  # outside the lock: closing the document does not wait for it
        return real_render(self, *args, **kwargs)

    monkeypatch.setattr(PdfDocument, "render", slow_render)
    window.open_file(str(many_pages_pdf))
    worker = window.page_view.service.worker
    doc = window.document_view.document
    window.page_view.service.request(5, 1.0)
    assert rendering.wait(3)
    monkeypatch.setattr(worker, "stop", lambda timeout_ms=1000: type(worker).stop(worker, 1))
    window.close()  # stop() gives up after 1 ms: shutdown must still join the thread
    assert not worker.isRunning()
    assert not doc.is_open
