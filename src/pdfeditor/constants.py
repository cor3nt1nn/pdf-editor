"""Application-wide constants."""

from __future__ import annotations

APP_NAME = "PDF Editor"
ORG_NAME = "PDFEditor"
APP_ID = "PDFEditor"

# Layout (PDF points)
PAGE_GAP_PT = 12.0
MARGIN_PT = 12.0

# Screen DPI / PDF points
BASE_SCALE = 96.0 / 72.0

ZOOM_MIN = 25.0
ZOOM_MAX = 400.0
ZOOM_STEPS = (25, 33, 50, 67, 75, 100, 125, 150, 200, 300, 400)

CACHE_BUDGET_BYTES = 256 * 1024 * 1024
THUMB_WIDTH_PX = 140
