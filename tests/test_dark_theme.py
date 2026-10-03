"""Dark theme: floating editors keep page colours, thumbnail selection stays visible
(docs/ARCHITECTURE.md Deviation 165).

Each test runs under a forced palette: ``dark`` sets Qt's dark colour scheme (when the
platform honours it) *and* a dark application palette, ``light`` a light one. Editors are
checked twice: through their resolved palette and by grabbing them and measuring the
contrast between the dominant (background) colour and the text pixels.
"""

from __future__ import annotations

import os
from collections import Counter

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QWidget,
)

from pdfeditor.ui.banner import InfoBanner
from pdfeditor.ui.colors import (
    MARK_CONTRAST,
    contrast_ratio,
    ensure_contrast,
    luminance,
    page_palette,
)
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.overlays.annot_editor import AnnotTextEditor
from pdfeditor.ui.overlays.field_editor import FieldEditorOverlay
from pdfeditor.ui.overlays.textedit_editor import TextRunEditor
from pdfeditor.ui.thumbnails import (
    FRAME_GAP,
    FRAME_WIDTH,
    RING_OFFSET,
    ThumbnailDelegate,
    current_ring_color,
    selection_color,
)

#: WCAG AA for text.
TEXT_CONTRAST = 4.5
#: Set PDFEDITOR_THEME_DUMP to a folder to keep the grabbed images (visual check).
DUMP = os.environ.get("PDFEDITOR_THEME_DUMP")


def _palette(dark: bool) -> QPalette:
    if dark:
        window, base, text, button = "#202020", "#2d2d2d", "#ffffff", "#3a3a3a"
        highlight = "#0050a0"  # a dark accent: low contrast with Base on purpose
    else:
        window, base, text, button = "#f3f3f3", "#ffffff", "#000000", "#fdfdfd"
        highlight = "#99c9ef"  # a pale accent: low contrast with Base on purpose
    palette = QPalette()
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role, color in (
            (QPalette.ColorRole.Window, window),
            (QPalette.ColorRole.Base, base),
            (QPalette.ColorRole.AlternateBase, button),
            (QPalette.ColorRole.Button, button),
            (QPalette.ColorRole.Text, text),
            (QPalette.ColorRole.WindowText, text),
            (QPalette.ColorRole.ButtonText, text),
            (QPalette.ColorRole.PlaceholderText, "#a0a0a0" if dark else "#606060"),
            (QPalette.ColorRole.Highlight, highlight),
            (QPalette.ColorRole.HighlightedText, "#ffffff"),
        ):
            palette.setColor(group, role, QColor(color))
    return palette


@pytest.fixture(params=["dark", "light"])
def theme(request, qapp):
    dark = request.param == "dark"
    hints = QGuiApplication.styleHints()
    old_scheme = hints.colorScheme()
    old_palette = QApplication.palette()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
        qapp.processEvents()
    QApplication.setPalette(_palette(dark))
    qapp.processEvents()
    yield request.param
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(old_scheme)
        qapp.processEvents()
    QApplication.setPalette(old_palette)
    qapp.processEvents()


def _dump(image: QImage, name: str) -> None:
    if DUMP:
        os.makedirs(DUMP, exist_ok=True)
        image.save(os.path.join(DUMP, f"{name}.png"))


def _grab(widget: QWidget, name: str, inset: int = 2) -> QImage:
    image = widget.grab().toImage()
    _dump(image, name)
    return image.copy(QRect(inset, inset, image.width() - 2 * inset, image.height() - 2 * inset))


def _text_contrast(image: QImage) -> tuple[QColor, float]:
    """Dominant colour of ``image`` (its background) and the best contrast any of its
    pixels has with it (the text, when the image shows text)."""
    counts: Counter[int] = Counter()
    for y in range(image.height()):
        for x in range(image.width()):
            counts[image.pixel(x, y)] += 1
    background = QColor.fromRgb(counts.most_common(1)[0][0])
    best = max(contrast_ratio(QColor.fromRgb(rgb), background) for rgb in counts)
    return background, best


def _assert_page_like(widget: QWidget, name: str, text: QColor | None = None) -> None:
    """Palette and pixels: light page background, strongly contrasting text, readable
    selection and placeholder."""
    widget.ensurePolished()
    palette = widget.palette()
    base = palette.color(QPalette.ColorRole.Base)
    ink = palette.color(QPalette.ColorRole.Text)
    # A see-through style sheet background is painted by the style, Base left unused.
    if palette.brush(QPalette.ColorRole.Base).style() == Qt.BrushStyle.SolidPattern:
        assert luminance(base) > 0.9, name
        assert contrast_ratio(ink, base) >= TEXT_CONTRAST, name
    if text is not None:
        assert ink.rgb() == text.rgb(), name
    assert (
        contrast_ratio(
            palette.color(QPalette.ColorRole.HighlightedText),
            palette.color(QPalette.ColorRole.Highlight),
        )
        >= TEXT_CONTRAST
    ), name
    placeholder = palette.color(QPalette.ColorRole.PlaceholderText)
    assert contrast_ratio(placeholder, QColor("white")) >= 3, name
    background, best = _text_contrast(_grab(widget, name))
    assert luminance(background) > 0.9, (name, background.name())
    assert best >= TEXT_CONTRAST, (name, background.name(), best)


# -- helpers ------------------------------------------------------------------------


def test_contrast_helpers() -> None:
    white, black = QColor("white"), QColor("black")
    assert contrast_ratio(white, black) == pytest.approx(21.0)
    assert contrast_ratio(white, white) == pytest.approx(1.0)
    dark_base = QColor("#2d2d2d")
    assert contrast_ratio(QColor("#0050a0"), dark_base) < MARK_CONTRAST
    lifted = ensure_contrast(QColor("#0050a0"), dark_base)
    assert contrast_ratio(lifted, dark_base) >= MARK_CONTRAST
    assert lifted.hue() == pytest.approx(QColor("#0050a0").hue(), abs=3)
    pale = ensure_contrast(QColor("#99c9ef"), white)
    assert contrast_ratio(pale, white) >= MARK_CONTRAST
    palette = page_palette(_palette(True), QColor(200, 0, 0))
    assert palette.color(QPalette.ColorRole.Base) == white
    assert palette.color(QPalette.ColorRole.Text) == QColor(200, 0, 0)
    for group in (QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        assert palette.color(group, QPalette.ColorRole.Base) == white


# -- form field editor ----------------------------------------------------------------


@pytest.fixture
def dv(qtbot, theme):
    view = DocumentView()
    qtbot.addWidget(view)
    view.resize(800, 600)
    view.show()
    qtbot.waitExposed(view)
    view.activateWindow()
    yield view
    view.undo_stack.setClean()
    view.shutdown()


def _widget(doc, name: str):
    return next(w for w in doc.widgets(0) if w.name == name)


def test_field_line_edit_is_readable(qtbot, dv, theme, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    overlay = FieldEditorOverlay(dv.page_view, doc)
    overlay.open(_widget(doc, "Zone de texte lecture"))  # prefilled "fixe"
    editor = overlay.editor
    assert isinstance(editor, QLineEdit) and editor.text()
    editor.deselect()
    _assert_page_like(editor, f"field-line-{theme}")
    editor.setText("")
    editor.setPlaceholderText("Placeholder")
    background, best = _text_contrast(_grab(editor, f"field-placeholder-{theme}"))
    assert luminance(background) > 0.9 and best >= 3
    editor.setText("Selected text")
    editor.selectAll()
    _background, best = _text_contrast(_grab(editor, f"field-selection-{theme}"))
    assert best >= TEXT_CONTRAST
    overlay.cancel()


def test_field_plain_text_edit_is_readable(qtbot, dv, theme, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    overlay = FieldEditorOverlay(dv.page_view, doc)
    overlay.open(_widget(doc, "Zone de texte multi"))
    editor = overlay.editor
    assert isinstance(editor, QPlainTextEdit)
    editor.setPlainText("Line one\nLine two")
    _assert_page_like(editor, f"field-multi-{theme}")
    overlay.cancel()


def test_field_combo_and_popup_are_readable(qtbot, dv, theme, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    overlay = FieldEditorOverlay(dv.page_view, doc)
    overlay.open(_widget(doc, "Civilité"))
    combo = overlay.editor
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(0)
    _assert_page_like(combo, f"field-combo-{theme}")
    view = combo.view()
    combo.showPopup()
    qtbot.waitUntil(view.isVisible)
    _assert_page_like(view, f"field-combo-popup-{theme}")
    combo.hidePopup()
    overlay.cancel()


def test_field_list_is_readable(qtbot, dv, theme, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    overlay = FieldEditorOverlay(dv.page_view, doc)
    overlay.open(_widget(doc, "Couleur"))
    lst = overlay.editor
    assert isinstance(lst, QListWidget) and lst.count()
    lst.clearSelection()
    _assert_page_like(lst, f"field-list-{theme}")
    lst.setCurrentRow(0)
    _background, best = _text_contrast(_grab(lst, f"field-list-selected-{theme}"))
    assert best >= TEXT_CONTRAST
    overlay.cancel()


# -- annotation text box and page text editors ----------------------------------------------


def test_annot_editor_keeps_annotation_colour_on_page(qtbot, dv, theme, simple_pdf) -> None:
    from PySide6.QtCore import QRectF

    doc = dv.open(str(simple_pdf))
    editor = AnnotTextEditor(dv.page_view, doc)
    editor.open_new(0, QRectF(72, 72, 200, 40), 14, (0.0, 0.0, 0.6))
    widget = editor.editor
    assert isinstance(widget, QPlainTextEdit)
    widget.setPlainText("Annotation")
    _assert_page_like(widget, f"annot-{theme}", text=QColor(0, 0, 153))
    style = widget.styleSheet()
    assert "selection-color" in style and "selection-background-color" in style
    widget.selectAll()
    _background, best = _text_contrast(_grab(widget, f"annot-selection-{theme}"))
    assert best >= TEXT_CONTRAST
    editor.cancel()


def test_text_run_editor_is_readable(qtbot, dv, theme, tmp_path) -> None:
    from textedit_fixtures import LINE1, make_text_edit_pdf

    from pdfeditor.core.textedit import Run

    doc = dv.open(str(make_text_edit_pdf(tmp_path / "edit.pdf")))
    editor = TextRunEditor(dv.page_view, doc)
    jean = LINE1.index("Jean")
    editor.open(0, Run(jean, jean + 3))
    widget = editor.editor
    assert isinstance(widget, QLineEdit)
    widget.deselect()
    _assert_page_like(widget, f"textrun-{theme}", text=QColor(0, 0, 0))
    editor.cancel()


# -- info banner -----------------------------------------------------------------------------


def test_banner_text_contrasts(qtbot, theme) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    banner = InfoBanner(host)
    banner.resize(500, 40)
    for kind in ("info", "warning"):
        banner.show_message("Banner message text", kind)
        background, best = _text_contrast(_grab(banner, f"banner-{kind}-{theme}"))
        assert best >= TEXT_CONTRAST, (kind, background.name(), best)


# -- thumbnails ---------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, theme, settings, many_pages_pdf):
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(1000, 800)
    w.show()
    qtbot.waitExposed(w)
    assert w.open_file(str(many_pages_pdf))
    yield w
    w.undo_stack.setClean()
    w.close()


def _band(image: QImage, thumb: QRect, outer: int, inner: int) -> list[QColor]:
    """Pixels between ``thumb`` grown by ``inner`` (excluded) and by ``outer``."""
    out = thumb.adjusted(-outer, -outer, outer, outer)
    inside = thumb.adjusted(-inner, -inner, inner, inner)
    pixels = []
    for y in range(out.top(), out.bottom() + 1):
        for x in range(out.left(), out.right() + 1):
            if not inside.contains(x, y) and image.rect().contains(x, y):
                pixels.append(image.pixelColor(x, y))
    return pixels


def test_thumbnail_selection_and_current_page_visible(qtbot, window, theme) -> None:
    sidebar = window.thumbnails
    model = window.thumbnail_model
    sidebar.select_pages([0, 2], current=2)
    sidebar.scrollToTop()
    qtbot.wait(50)
    viewport = sidebar.viewport()
    palette = viewport.palette()
    base = palette.color(QPalette.ColorRole.Base)
    accent = selection_color(palette)
    ring = current_ring_color(palette)
    assert contrast_ratio(accent, base) >= MARK_CONTRAST
    assert contrast_ratio(ring, base) >= MARK_CONTRAST
    assert accent.rgb() != ring.rgb()
    image = viewport.grab().toImage()
    if image.devicePixelRatio() != 1:
        image = image.scaled(viewport.size())
    _dump(image, f"thumbnails-{theme}")

    def frame_share(row: int) -> float:
        rect = sidebar.visualRect(model.index(row))
        pixmap = model.data(model.index(row), Qt.ItemDataRole.DecorationRole)
        thumb = ThumbnailDelegate.thumb_rect(rect, pixmap)
        band = _band(image, thumb, FRAME_GAP + FRAME_WIDTH, FRAME_GAP + 1)
        hits = sum(1 for c in band if contrast_ratio(c, accent) < 1.15)
        return hits / len(band)

    def ring_share(row: int) -> float:
        rect = sidebar.visualRect(model.index(row))
        pixmap = model.data(model.index(row), Qt.ItemDataRole.DecorationRole)
        thumb = ThumbnailDelegate.thumb_rect(rect, pixmap)
        band = _band(image, thumb, RING_OFFSET, RING_OFFSET - 1)
        hits = sum(1 for c in band if contrast_ratio(c, ring) < 1.15)
        return hits / len(band)

    # Selected thumbnails: a solid frame all around; an unselected one: none.
    assert frame_share(0) > 0.8
    assert frame_share(2) > 0.8
    assert frame_share(1) < 0.05
    # The current page (2) also has its ring; the other selected page (0) does not.
    assert sidebar.currentIndex().row() == 2
    assert ring_share(2) > 0.8
    assert ring_share(0) < 0.2


def test_thumbnail_current_page_unselected_has_ring(qtbot, window, theme) -> None:
    sidebar = window.thumbnails
    model = window.thumbnail_model
    sidebar.select_pages([0, 1], current=0)
    sidebar.selectionModel().select(model.index(0), sidebar.selectionModel().SelectionFlag.Deselect)
    assert sidebar.currentIndex().row() == 0 and sidebar.selected_pages() == [1]
    sidebar.scrollToTop()
    qtbot.wait(50)
    viewport = sidebar.viewport()
    image = viewport.grab().toImage()
    if image.devicePixelRatio() != 1:
        image = image.scaled(viewport.size())
    ring = current_ring_color(viewport.palette())
    rect = sidebar.visualRect(model.index(0))
    thumb = ThumbnailDelegate.thumb_rect(
        rect, model.data(model.index(0), Qt.ItemDataRole.DecorationRole)
    )
    band = _band(image, thumb, RING_OFFSET, RING_OFFSET - 1)
    assert sum(1 for c in band if contrast_ratio(c, ring) < 1.15) / len(band) > 0.8
