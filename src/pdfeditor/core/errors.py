"""The base class of the document errors (no Qt, no PyMuPDF: the OCR worker process
imports it through :mod:`pdfeditor.core.ocr`).

Re-exported by :mod:`pdfeditor.core.document`, where the other document errors live.
"""

from __future__ import annotations


class DocumentError(Exception):
    """Base class for document errors."""
