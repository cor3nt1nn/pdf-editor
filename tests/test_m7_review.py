"""M7 review findings (core): privacy across undo/redo saves, reloads while a page is
held, our fonts' names, borrowed codes' ToUnicode, collateral neighbours of other spans,
the stale-run guard and refused characters."""

from __future__ import annotations

import re
import zlib
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtGui import QUndoStack
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_PATH,
    LINE1,
    TIMES_PATH,
    make_text_edit_pdf,
    needs_text_fonts,
)

from pdfeditor.core.commands import ReplaceTextCommand
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.fontmatch import SystemFonts
from pdfeditor.core.pagetext import PageText
from pdfeditor.core.textedit import Run

pytestmark = needs_text_fonts


@pytest.fixture(scope="module")
def fonts() -> SystemFonts:
    return SystemFonts.from_paths([CALIBRI_PATH, ARIAL_PATH, TIMES_PATH])


def _norm(s: str) -> str:
    return s.replace("\xa0", " ")


def find_run(text: PageText, word: str) -> Run:
    flat = _norm("".join(ch.c for ch in text.chars))
    start = flat.index(word)
    return Run(start, start + len(word) - 1)


def file_streams(path: Path) -> list[bytes]:
    """Every stream of every revision of the file at ``path`` (inflated when possible)."""
    out: list[bytes] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", path.read_bytes(), re.S):
        raw = m.group(1)
        try:
            out.append(zlib.decompress(raw))
        except zlib.error:
            out.append(raw)
    return out


# -- 4. privacy: save, undo, save, redo, save -----------------------------------------------
def test_removed_text_gone_after_save_undo_save_redo_save(
    tmp_path: Path, fonts: SystemFonts, qapp
) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    doc = PdfDocument.open(path)
    with doc.lock:
        content = doc.fitz[0].read_contents()
    line_hex = re.search(rb"\[<([0-9A-Fa-f]+)>\]\s*TJ", content).group(1)  # type: ignore[union-attr]
    start = LINE1.index("Dupont")
    dupont = line_hex[start * 4 : (start + 6) * 4]
    assert any(dupont in s for s in file_streams(path))

    stack = QUndoStack()
    cmd = ReplaceTextCommand(doc, 0, find_run(doc.page_text(0), "Dupont"), "Zidane", fonts=fonts)
    cmd.apply_now()
    stack.push(cmd)
    doc.save()
    assert not any(dupont in s for s in file_streams(path))
    stack.undo()
    assert cmd.error is None
    doc.save()  # the old text is back: it is in the file, as it should be
    assert any(dupont in s for s in file_streams(path))
    stack.redo()
    assert cmd.error is None
    assert not doc.can_save_incrementally()
    doc.save()
    assert not any(dupont in s for s in file_streams(path))
    assert "Zidane" in _norm(doc.page_text(0).text)
    doc.close()
    reopened = pymupdf.open(path)
    assert "Zidane" in _norm(reopened[0].get_text())
    reopened.close()
