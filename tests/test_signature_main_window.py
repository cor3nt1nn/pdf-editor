"""M4-T7: signature tool, Signatures menu and toolbar button wired into MainWindow."""

from __future__ import annotations

import fixtures
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox, QToolButton

from pdfeditor.core import signature_store as store_module
from pdfeditor.core.annotations import AnnotKind
from pdfeditor.ui import dialogs, signature_dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.annot_tools import SignatureTool

SIGNATURE_FIRST = "Add a signature first (Signatures ▸ Add Signature…)."


class FakeImport:
    """Stands in for ``signature_dialogs.import_signature``: adds ``image`` under
    ``name`` (or returns None when ``image`` is None) and counts the calls."""

    def __init__(self, image: QImage | None = None, name: str = "Imported") -> None:
        self.image = image
        self.name = name
        self.calls = 0

    def __call__(self, store, parent=None, path=None):
        self.calls += 1
        if self.image is None:
            return None
        return store.add(self.name, self.image)


@pytest.fixture
def fake_import(monkeypatch) -> FakeImport:
    fake = FakeImport()
    monkeypatch.setattr(signature_dialogs, "import_signature", fake)
    return fake


@pytest.fixture
def window(qtbot, qapp, settings, signature_store, fake_import, monkeypatch):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w = MainWindow(settings, signature_store)
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    yield w
    w.undo_stack.setClean()
    w.close()


def _record_names(w: MainWindow) -> list[str]:
    return [a.text() for a in w.signature_actions()]


def _checked_ids(w: MainWindow) -> list[str]:
    return [a.data() for a in w.signature_actions() if a.isChecked()]


def _active(w: MainWindow) -> str:
    return w.tool_manager.active_tool.name


# -- construction ---------------------------------------------------------------------------
def test_default_store_is_never_the_users(qtbot, settings) -> None:
    w = MainWindow(settings)
    qtbot.addWidget(w)
    # conftest redirects default_directory() to a temporary directory for every test.
    assert w.signature_store.directory == store_module.default_directory()
    assert "default_signatures" in str(w.signature_store.directory)
    assert w.signature_store.parent() is w


def test_actions_menu_and_toolbar(window, signature_store) -> None:
    w = window
    act = w.act_signature_tool
    assert w.signature_store is signature_store
    assert act.text() == "&Signature Tool" and act.toolTip() == "Signature (S)"
    assert act.shortcut().toString() == "S" and act.isCheckable()
    assert not act.icon().isNull()
    assert isinstance(w.signature_tool, SignatureTool)
    assert w.tool_manager.tools["signature"] is w.signature_tool
    assert w.tool_manager.actions["signature"] is act
    assert act.actionGroup() is w.tool_manager.action_group
    edit = w.menu_edit.actions()
    sub = w.menu_signatures.menuAction()
    assert edit.index(w.act_stamp_dot) < edit.index(act) < edit.index(sub)
    assert edit.index(sub) < edit.index(w.act_delete_annot)
    assert w.menu_signatures.title() == "Signatures"
    # Empty store: only Add… and Manage….
    assert w.menu_signatures.actions() == [w.act_add_signature, w.act_manage_signatures]
    assert w.act_add_signature.text() == "Add Signature…"
    assert w.act_manage_signatures.text() == "Manage Signatures…"
    button = w.signature_button
    assert isinstance(button, QToolButton) and w.toolbar.isAncestorOf(button)
    assert button.defaultAction() is act
    assert button.popupMode() is QToolButton.ToolButtonPopupMode.MenuButtonPopup
    assert button.menu() is w.menu_signatures


def test_enabled_only_with_can_annotate(window, simple_pdf, owner_locked_pdf) -> None:
    w = window
    assert not w.act_signature_tool.isEnabled()
    assert w.open_file(str(simple_pdf))
    assert w.act_signature_tool.isEnabled()
    assert w.open_file(str(owner_locked_pdf))
    assert not w.document_view.document.can_annotate
    assert not w.act_signature_tool.isEnabled()
    # Add/Manage work on the store, with or without a document.
    assert w.act_add_signature.isEnabled() and w.act_manage_signatures.isEnabled()


# -- the Signatures menu ---------------------------------------------------------------------
def test_menu_lists_records_and_checks_default(window, store_with_one, signature_png) -> None:
    w = window
    store = store_with_one
    first = store.default_id
    assert _record_names(w) == ["My signature"]
    assert _checked_ids(w) == [first]
    second = store.add("R&D", QImage(str(signature_png)))
    assert _record_names(w) == ["My signature", "R&&D"]  # & shown literally
    assert _checked_ids(w) == [first]
    store.set_default(second.id)
    assert _checked_ids(w) == [second.id]
    actions = w.menu_signatures.actions()
    assert actions[-2:] == [w.act_add_signature, w.act_manage_signatures]
    assert actions[2].isSeparator()
    store.delete(second.id)
    assert _record_names(w) == ["My signature"] and _checked_ids(w) == [first]


def test_choosing_a_record_sets_default_and_activates(
    window, store_with_one, signature_png, simple_pdf
) -> None:
    w = window
    store = store_with_one
    second = store.add("Second", QImage(str(signature_png)))
    # Without a document: only the default changes.
    w.signature_actions()[1].trigger()
    assert store.default_id == second.id and _checked_ids(w) == [second.id]
    assert _active(w) == "hand"
    assert w.open_file(str(simple_pdf))
    w.signature_actions()[0].trigger()
    assert store.default_id != second.id
    assert _active(w) == "signature" and w.act_signature_tool.isChecked()


def test_add_signature_imports_and_activates(window, fake_import, signature_png, simple_pdf):
    w = window
    fake_import.image = QImage(str(signature_png))
    assert w.open_file(str(simple_pdf))
    w.act_add_signature.trigger()
    assert fake_import.calls == 1
    (record,) = w.signature_store.records()
    assert record.name == "Imported" and w.signature_store.default_id == record.id
    assert _active(w) == "signature"
    # A second one becomes the default too (the user just made it to use it).
    fake_import.name = "Other"
    w.act_hand_tool.trigger()
    w.act_add_signature.trigger()
    other = w.signature_store.records()[1]
    assert w.signature_store.default_id == other.id and _checked_ids(w) == [other.id]
    assert _active(w) == "signature"


def test_add_signature_cancelled(window, fake_import, simple_pdf) -> None:
    w = window
    assert w.open_file(str(simple_pdf))
    w.act_add_signature.trigger()
    assert fake_import.calls == 1
    assert w.signature_store.records() == [] and _active(w) == "hand"


def test_manage_signatures_opens_manager(window, store_with_one, monkeypatch) -> None:
    w = window
    opened = []

    def fake_exec(dialog):
        opened.append((dialog.store, dialog.parent()))
        return 0

    monkeypatch.setattr(signature_dialogs.SignatureManagerDialog, "exec", fake_exec)
    w.act_manage_signatures.trigger()
    assert opened == [(store_with_one, w)]


# -- activation -----------------------------------------------------------------------------
def test_shortcut_with_empty_store_cancel_keeps_previous_tool(
    qtbot, window, fake_import, simple_pdf
) -> None:
    w = window
    assert w.open_file(str(simple_pdf))
    w.act_text_tool.trigger()
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_S)
    assert fake_import.calls == 1
    assert _active(w) == "text"
    assert w.act_text_tool.isChecked() and not w.act_signature_tool.isChecked()
    assert w.statusBar().currentMessage() == SIGNATURE_FIRST


def test_empty_store_import_then_activate(window, fake_import, signature_png, simple_pdf):
    w = window
    fake_import.image = QImage(str(signature_png))
    assert w.open_file(str(simple_pdf))
    w.act_signature_tool.trigger()
    assert fake_import.calls == 1
    assert len(w.signature_store.records()) == 1
    assert _active(w) == "signature" and w.act_signature_tool.isChecked()


def test_activation_with_saved_signature_skips_dialog(
    window, fake_import, store_with_one, simple_pdf
) -> None:
    w = window
    assert w.open_file(str(simple_pdf))
    w.act_signature_tool.trigger()
    assert fake_import.calls == 0
    assert _active(w) == "signature"
    # Triggering it again keeps it.
    w.act_signature_tool.trigger()
    assert _active(w) == "signature" and w.act_signature_tool.isChecked()


def test_signature_needed_opens_import(
    qtbot, window, fake_import, store_with_one, signature_png, simple_pdf
) -> None:
    w = window
    assert w.open_file(str(simple_pdf))
    w.act_signature_tool.trigger()
    # All signatures deleted while the tool is active: a click asks for one.
    store_with_one.delete(store_with_one.default_id)
    w.signature_tool.signature_needed.emit()
    qtbot.waitUntil(lambda: fake_import.calls == 1)
    assert _active(w) == "signature"
    assert w.statusBar().currentMessage() == SIGNATURE_FIRST
    fake_import.image = QImage(str(signature_png))
    w.signature_tool.signature_needed.emit()
    qtbot.waitUntil(lambda: fake_import.calls == 2)
    assert len(store_with_one.records()) == 1 and _active(w) == "signature"
    # Ignored when another tool is active by then.
    w.signature_tool.signature_needed.emit()
    w.act_hand_tool.trigger()
    qtbot.wait(20)
    assert fake_import.calls == 2


def test_tool_message_reaches_status_bar(window) -> None:
    window.signature_tool.message.emit("hello")
    assert window.statusBar().currentMessage() == "hello"


# -- style widgets ------------------------------------------------------------------------------
def test_style_widgets_inert_for_selected_signature(window, settings, signed_pdf) -> None:
    w = window
    settings.annot_color = "#ff0000"
    assert w.open_file(str(signed_pdf))
    w.act_text_tool.trigger()
    doc = w.document_view.document
    text = doc.annot(0, fixtures.SIGNED_TEXT_NAME)
    signature = doc.annot(0, fixtures.SIGNED_NAME)
    assert signature.kind is AnnotKind.SIGNATURE
    assert w.font_size_spin.isEnabled() and w.color_button.isEnabled()
    w.text_tool.selection.select(signature)
    assert not w.font_size_spin.isEnabled() and not w.color_button.isEnabled()
    assert w.text_color.name() == "#ff0000"  # the defaults are shown
    assert w.act_delete_annot.isEnabled()
    w.text_tool.selection.select(text)
    assert w.font_size_spin.isEnabled() and w.color_button.isEnabled()
    w.text_tool.selection.select(signature)
    w.act_hand_tool.trigger()  # clears the selection
    assert w.font_size_spin.isEnabled() and w.color_button.isEnabled()


# -- French ------------------------------------------------------------------------------------
def test_french_strings(qtbot, qapp, settings, signature_store) -> None:
    from pdfeditor import i18n

    i18n.install_translators(qapp, "fr")
    try:
        w = MainWindow(settings, signature_store)
        qtbot.addWidget(w)
        assert w.act_signature_tool.text() == "Outil &signature"
        assert w.act_signature_tool.toolTip() == "Signature (S)"
        assert w.menu_signatures.title() == "Signatures"
        assert w.act_add_signature.text() == "Ajouter une signature…"
        assert w.act_manage_signatures.text() == "Gérer les signatures…"
        assert w._signature_first_message() == (
            "Ajoutez d’abord une signature (Signatures ▸ Ajouter une signature…)."
        )
    finally:
        i18n.remove_translators(qapp)
