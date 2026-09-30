"""InfoBanner: a closable one-line message strip shown above the pages."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStyle, QToolButton, QWidget

# Accent colour of a "warning" banner (amber); "info" uses the palette highlight.
WARNING_ACCENT = QColor(232, 160, 0)
# How much of the accent is blended into the palette base colour for the background.
BACKGROUND_BLEND = 0.18

KINDS = ("info", "warning")


def _blend(base: QColor, accent: QColor, amount: float) -> QColor:
    return QColor(
        round(base.red() + (accent.red() - base.red()) * amount),
        round(base.green() + (accent.green() - base.green()) * amount),
        round(base.blue() + (accent.blue() - base.blue()) * amount),
    )


class InfoBanner(QFrame):
    """Message strip with a close button, hidden until :meth:`show_message`.

    Colours derive from the palette (a light tint of the accent over the base colour
    with the palette's text colour), so the banner stays readable in light and dark
    themes. The close button only hides the banner: :attr:`message` keeps the text, so
    the owner can tell a dismissed message from a new one.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("InfoBanner")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._message: tuple[str, str] | None = None
        self._styling = False

        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.close_button = QToolButton(self)
        self.close_button.setAutoRaise(True)
        self.close_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        )
        self.close_button.setToolTip(self.tr("Close"))
        self.close_button.setAccessibleName(self.tr("Close"))
        self.close_button.clicked.connect(self.hide)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    @property
    def message(self) -> tuple[str, str] | None:
        """(text, kind) of the last message, even if the user closed the banner."""
        return self._message

    @property
    def text(self) -> str:
        return self.label.text()

    @property
    def kind(self) -> str:
        return self._message[1] if self._message is not None else ""

    def show_message(self, text: str, kind: str = "info") -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown banner kind: {kind!r}")
        self._message = (text, kind)
        self.label.setText(text)
        self._apply_style()
        self.show()

    def clear(self) -> None:
        """Hide the banner and forget its message."""
        self._message = None
        self.label.clear()
        self.hide()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and not self._styling:
            self._apply_style()

    def _apply_style(self) -> None:
        palette = self.palette()
        accent = (
            WARNING_ACCENT
            if self.kind == "warning"
            else palette.color(QPalette.ColorRole.Highlight)
        )
        background = _blend(palette.color(QPalette.ColorRole.Base), accent, BACKGROUND_BLEND)
        text = palette.color(QPalette.ColorRole.WindowText)
        self._styling = True  # setStyleSheet may itself post a palette change
        try:
            self.setStyleSheet(
                f"#InfoBanner {{ background-color: {background.name()};"
                f" border-bottom: 2px solid {accent.name()}; }}"
                f" #InfoBanner QLabel {{ color: {text.name()}; background: transparent; }}"
            )
        finally:
            self._styling = False
