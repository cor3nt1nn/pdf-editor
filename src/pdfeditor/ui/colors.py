"""Colours that must stay readable whatever the application palette (light or dark).

The floating editors sit over the rendered PDF page, which is white in both themes, so
they use fixed *page* colours (:func:`page_palette`, :func:`page_editor_style`) instead of
the palette's Base/Text, which are dark/light in a dark theme. :func:`contrast_ratio` and
:func:`ensure_contrast` (WCAG 2.x relative luminance) keep palette-derived marks, such as
the thumbnail selection frame, visible against the palette's background.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

#: Background of an editor over the page, its default text, the selection and the
#: placeholder (hint) text: page-like, never taken from the palette.
PAGE_BACKGROUND = QColor(255, 255, 255)
PAGE_TEXT = QColor(0, 0, 0)
PAGE_SELECTION = QColor(0, 103, 192)
PAGE_SELECTED_TEXT = QColor(255, 255, 255)
PAGE_PLACEHOLDER = QColor(110, 110, 110)
#: Minimum contrast of a non-text mark (WCAG 2.1 SC 1.4.11).
MARK_CONTRAST = 3.0


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(color: QColor) -> float:
    """WCAG relative luminance of ``color`` (alpha ignored), 0 (black) to 1 (white)."""
    return (
        0.2126 * _channel(color.redF())
        + 0.7152 * _channel(color.greenF())
        + 0.0722 * _channel(color.blueF())
    )


def contrast_ratio(a: QColor, b: QColor) -> float:
    """WCAG contrast ratio of two opaque colours, 1 (same) to 21 (black on white)."""
    la, lb = luminance(a), luminance(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def ensure_contrast(color: QColor, against: QColor, ratio: float = MARK_CONTRAST) -> QColor:
    """``color``, made lighter (on a dark ``against``) or darker (on a light one) in small
    steps until its contrast with ``against`` reaches ``ratio`` (or it hits white/black)."""
    result = QColor(color)
    result.setAlpha(255)
    lighten = luminance(against) < 0.18
    for _ in range(40):
        if contrast_ratio(result, against) >= ratio:
            break
        h, s, light, a = result.getHslF()
        light = min(1.0, light + 0.04) if lighten else max(0.0, light - 0.04)
        result = QColor.fromHslF(max(h, 0.0), s, light, a)
    return result


def page_palette(base: QPalette, text: QColor | None = None) -> QPalette:
    """``base`` with page-like colours in every colour group: white background, ``text``
    (default black) for text, a blue selection with white text and a grey placeholder."""
    palette = QPalette(base)
    ink = QColor(PAGE_TEXT if text is None else text)
    roles = {
        QPalette.ColorRole.Base: PAGE_BACKGROUND,
        QPalette.ColorRole.AlternateBase: PAGE_BACKGROUND,
        QPalette.ColorRole.Window: PAGE_BACKGROUND,
        QPalette.ColorRole.Button: PAGE_BACKGROUND,
        QPalette.ColorRole.Text: ink,
        QPalette.ColorRole.WindowText: ink,
        QPalette.ColorRole.ButtonText: ink,
        QPalette.ColorRole.Highlight: PAGE_SELECTION,
        QPalette.ColorRole.HighlightedText: PAGE_SELECTED_TEXT,
        QPalette.ColorRole.PlaceholderText: PAGE_PLACEHOLDER,
    }
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role, color in roles.items():
            palette.setColor(group, role, color)
    return palette


def css(color: QColor) -> str:
    """``rgb()``/``rgba()`` notation of ``color`` for a style sheet."""
    if color.alpha() == 255:
        return f"rgb({color.red()}, {color.green()}, {color.blue()})"
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def page_colors_css(text: QColor | None = None, background: QColor | None = None) -> str:
    """Style sheet declarations (no selector) for text over the page: ``color``,
    ``background``, ``selection-color``, ``selection-background-color`` and
    ``placeholder-text-color``."""
    ink = PAGE_TEXT if text is None else text
    paper = PAGE_BACKGROUND if background is None else background
    return (
        f"color: {css(ink)}; background: {css(paper)}; "
        f"selection-color: {css(PAGE_SELECTED_TEXT)}; "
        f"selection-background-color: {css(PAGE_SELECTION)}; "
        f"placeholder-text-color: {css(PAGE_PLACEHOLDER)};"
    )


__all__ = [
    "MARK_CONTRAST",
    "PAGE_BACKGROUND",
    "PAGE_PLACEHOLDER",
    "PAGE_SELECTED_TEXT",
    "PAGE_SELECTION",
    "PAGE_TEXT",
    "contrast_ratio",
    "css",
    "ensure_contrast",
    "luminance",
    "page_colors_css",
    "page_palette",
]
