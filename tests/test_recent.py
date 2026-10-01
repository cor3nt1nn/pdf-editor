"""M5-T3: recent files (core/recent.py, File ▸ Open Recent)."""

from __future__ import annotations

import os

import fixtures
import pytest
from fixtures import PASSWORD
from PySide6.QtCore import QDir
from PySide6.QtWidgets import QDialog, QMessageBox

from pdfeditor.core import recent
from pdfeditor.core.recent import RECENT_MAX
from pdfeditor.core.settings import Settings
from pdfeditor.ui import dialogs, main_window
from pdfeditor.ui.export_dialog import ExportDialog
from pdfeditor.ui.main_window import MainWindow


# -- core/recent.py -------------------------------------------------------------
def test_normalise_is_absolute_and_normalised(tmp_path) -> None:
    path = str(tmp_path / "a" / ".." / "b.pdf")
    assert recent.normalise(path) == os.path.normpath(str(tmp_path / "b.pdf"))
    assert os.path.isabs(recent.normalise("x.pdf"))


def test_push_adds_to_front_and_moves_existing(tmp_path) -> None:
    a, b, c = (str(tmp_path / n) for n in ("a.pdf", "b.pdf", "c.pdf"))
    paths = recent.push([], a)
    paths = recent.push(paths, b)
    paths = recent.push(paths, c)
    assert paths == [c, b, a]
    assert recent.push(paths, a) == [a, c, b]


def test_push_dedupes_case_insensitively_on_normalised_paths(tmp_path) -> None:
    a = str(tmp_path / "Doc.pdf")
    other = str(tmp_path / "other.pdf")
    variant = str(tmp_path / "sub" / ".." / "DOC.PDF").replace("\\", "/")
    paths = recent.push([a, other], variant)
    assert len(paths) == 2
    assert paths[0] == recent.normalise(variant)
    assert paths[1] == other


def test_push_caps_list() -> None:
    paths: list[str] = []
    for i in range(15):
        paths = recent.push(paths, f"C:/docs/f{i}.pdf")
    assert len(paths) == RECENT_MAX == 10
    assert os.path.basename(paths[0]) == "f14.pdf"
    assert os.path.basename(paths[-1]) == "f5.pdf"
    assert len(recent.push(paths, "C:/docs/new.pdf", cap=3)) == 3


def test_remove_matches_case_insensitively(tmp_path) -> None:
    a, b = str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")
    assert recent.remove([a, b], a.upper()) == [b]
    assert recent.remove([a, b], str(tmp_path / "zzz.pdf")) == [a, b]


def test_prune_drops_missing(tmp_path) -> None:
    present = fixtures.make_simple_pdf(tmp_path / "present.pdf")
    missing = tmp_path / "missing.pdf"
    assert recent.prune([str(missing), str(present)]) == [str(present)]
    assert recent.prune(["x", "y"], exists=lambda p: p == "y") == ["y"]


def test_settings_recent_files_capped(settings: Settings) -> None:
    settings.recent_files = [f"C:/f{i}.pdf" for i in range(12)]
    assert len(settings.recent_files) == RECENT_MAX
    settings.recent_files = []
    assert settings.recent_files == []


# -- File ▸ Open Recent ---------------------------------------------------------
@pytest.fixture
def warnings(monkeypatch):
    found: list[tuple[str, str]] = []
    monkeypatch.setattr(
        dialogs, "warn", lambda parent, title, text, details=None: found.append((title, text))
    )
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    monkeypatch.setattr(dialogs, "ask_password", lambda p, n, w: PASSWORD)
    return found


@pytest.fixture
def window(qtbot, settings, warnings):
    w = MainWindow(settings)
    qtbot.addWidget(w)
    yield w
    w.undo_stack.setClean()
    w.close()


def show_recent(w: MainWindow) -> None:
    w.menu_recent.aboutToShow.emit()


def test_recent_menu_after_open_in_file_menu(window) -> None:
    actions = window.menu_file.actions()
    assert actions[0] is window.act_open
    assert actions[1].menu() is window.menu_recent
    assert window.menu_recent.title() == "Open &Recent"
    assert window.menu_recent.toolTipsVisible()


def test_empty_list_shows_disabled_placeholder(window) -> None:
    show_recent(window)
    actions = window.menu_recent.actions()
    assert [a.text() for a in actions] == ["No recent files"]
    assert not actions[0].isEnabled()
    assert window.recent_actions() == []


def test_entries_numbered_with_tooltips(window, settings, tmp_path) -> None:
    paths = [
        str(fixtures.make_simple_pdf(tmp_path / f"doc{i}{'&x' if i == 2 else ''}.pdf"))
        for i in range(1, 11)
    ]
    settings.recent_files = paths
    show_recent(window)
    entries = window.recent_actions()
    assert len(entries) == 10
    assert entries[0].text() == "&1 doc1.pdf"
    assert entries[1].text() == "&2 doc2&&x.pdf"
    assert entries[8].text() == "&9 doc9.pdf"
    assert entries[9].text() == "1&0 doc10.pdf"
    for act, path in zip(entries, paths, strict=True):
        assert act.toolTip() == QDir.toNativeSeparators(path)
        assert act.statusTip() == QDir.toNativeSeparators(path)
        assert act.data() == path
    tail = window.menu_recent.actions()[-2:]
    assert tail[0].isSeparator()
    assert tail[1] is window.act_clear_recent and tail[1].isEnabled()


def test_clear_list_empties_and_shows_placeholder(window, settings, simple_pdf) -> None:
    settings.recent_files = [str(simple_pdf)]
    show_recent(window)
    assert len(window.recent_actions()) == 1
    window.act_clear_recent.trigger()
    assert settings.recent_files == []
    show_recent(window)
    assert [a.text() for a in window.menu_recent.actions()] == ["No recent files"]


def test_open_adds_to_front(window, settings, tmp_path) -> None:
    a = fixtures.make_simple_pdf(tmp_path / "a.pdf")
    b = fixtures.make_simple_pdf(tmp_path / "b.pdf")
    assert window.open_file(str(a))
    assert window.open_file(str(b))
    assert window.open_file(str(a))
    assert settings.recent_files == [recent.normalise(str(a)), recent.normalise(str(b))]
    show_recent(window)
    assert [act.text() for act in window.recent_actions()] == ["&1 a.pdf", "&2 b.pdf"]


def test_failed_open_not_added(window, settings, tmp_path, warnings) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert not window.open_file(str(bad))
    assert settings.recent_files == []


def test_save_as_adds(window, settings, simple_pdf, tmp_path, monkeypatch) -> None:
    assert window.open_file(str(simple_pdf))
    target = tmp_path / "saved as.pdf"
    monkeypatch.setattr(dialogs, "get_save_path", lambda p, s: str(target))
    assert window.save_as()
    assert settings.recent_files[0] == recent.normalise(str(target))
    assert settings.recent_files[1] == recent.normalise(str(simple_pdf))


def test_export_does_not_add(window, settings, simple_pdf, tmp_path, monkeypatch) -> None:
    assert window.open_file(str(simple_pdf))
    target = tmp_path / "exported.pdf"
    monkeypatch.setattr(ExportDialog, "exec", lambda d: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(dialogs, "get_export_path", lambda p, s: str(target))
    before = settings.recent_files
    assert window.export_copy()
    assert target.is_file()
    assert settings.recent_files == before


def test_choosing_entry_opens_it(window, settings, simple_pdf) -> None:
    settings.recent_files = [str(simple_pdf)]
    show_recent(window)
    window.recent_actions()[0].trigger()
    doc = window.document_view.document
    assert doc is not None and os.path.samefile(doc.path, simple_pdf)


def test_vanished_entry_warns_and_is_removed(window, settings, tmp_path, warnings) -> None:
    keep = fixtures.make_simple_pdf(tmp_path / "keep.pdf")
    gone = fixtures.make_simple_pdf(tmp_path / "gone.pdf")
    settings.recent_files = [str(gone), str(keep)]
    show_recent(window)
    action = window.recent_actions()[0]
    os.remove(gone)
    action.trigger()
    assert len(warnings) == 1
    assert "The file does not exist." in warnings[0][1]
    assert settings.recent_files == [str(keep)]
    assert window.document_view.document is None
    assert [a.text() for a in window.recent_actions()] == ["&1 keep.pdf"]


def test_menu_prunes_missing_on_show(window, settings, tmp_path) -> None:
    keep = fixtures.make_simple_pdf(tmp_path / "keep.pdf")
    settings.recent_files = [str(tmp_path / "missing.pdf"), str(keep)]
    show_recent(window)
    assert [a.text() for a in window.recent_actions()] == ["&1 keep.pdf"]
    assert settings.recent_files == [str(keep)]


def test_lazy_no_file_checks_at_construction(qtbot, settings, monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def counting_exists(path: str) -> bool:
        calls.append(path)
        return True

    monkeypatch.setattr(main_window, "file_exists", counting_exists)
    settings.recent_files = [str(tmp_path / "x.pdf"), str(tmp_path / "y.pdf")]
    w = MainWindow(settings)
    qtbot.addWidget(w)
    assert calls == []
    show_recent(w)
    assert sorted(calls) == sorted(settings.recent_files)
    assert len(w.recent_actions()) == 2


def test_ini_holds_only_paths(window, settings, ini_path, tmp_path) -> None:
    locked = fixtures.make_encrypted_pdf(tmp_path / "locked.pdf")
    assert window.open_file(str(locked))
    settings.sync()
    text = ini_path.read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert settings.recent_files == [recent.normalise(str(locked))]
