"""Objects created and orphaned since the document was loaded (M4 review, privacy).

MuPDF's incremental writer appends every object created since the load, referenced or
not: the image of a signature placed then undone (or deleted), the annotation dict of a
deleted FreeText with its text, an appearance stream replaced by a newer one... would all
be written to the file. :func:`session_orphans` finds those objects and
:func:`drop_objects` frees them before an incremental write.

Reachability: a new object is live iff it can be reached from the trailer. An object not
modified since the load cannot reference a new object, so the walk starts from the
trailer and from every old object of the incremental section (modified this session,
including old objects that are themselves unreachable: conservative), and only expands
objects of the incremental section. Objects of earlier revisions are never touched:
removing what an earlier save already wrote needs a full save (``garbage=3``).

Pure functions on ``pymupdf.Document``; callers hold ``PdfDocument.lock``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

import pymupdf

log = logging.getLogger(__name__)

_mupdf = pymupdf.mupdf
_REF = re.compile(r"(\d+)\s+(\d+)\s+R\b")


def _refs(text: str) -> list[int]:
    """Object numbers referenced (``n g R``) in an object's source. A string that looks
    like a reference only keeps more objects alive."""
    return [int(m.group(1)) for m in _REF.finditer(text)]


def session_orphans(fitz_doc: pymupdf.Document, first_new: int) -> list[int]:
    """Objects numbered ``first_new`` or above (created since the load, whose xref
    length was ``first_new``) that the trailer no longer reaches, in increasing order."""
    end = int(fitz_doc.xref_length())
    if end <= first_new:
        return []
    pdf = pymupdf._as_pdf_document(fitz_doc)

    def incremental(x: int) -> bool:
        return x >= first_new or bool(_mupdf.pdf_xref_is_incremental(pdf, x))

    stack = [x for x in range(1, first_new) if incremental(x)]
    stack += _refs(fitz_doc.pdf_trailer(compressed=True))
    seen: set[int] = set()
    while stack:
        x = stack.pop()
        if x in seen or not 0 < x < end:
            continue
        seen.add(x)
        if not incremental(x):
            continue
        try:
            text = fitz_doc.xref_object(x, compressed=True)
        except Exception:  # MuPDF raises FzError* on broken objects; keep it alive
            log.debug("cannot read object %d", x, exc_info=True)
            continue
        stack.extend(_refs(text))
    orphans = []
    for x in range(first_new, end):
        if x in seen:
            continue
        try:
            if fitz_doc.xref_object(x, compressed=True).strip() == "null" and not (
                fitz_doc.xref_is_stream(x)
            ):
                continue  # free or never written
        except Exception:  # unreadable: drop it too
            pass
        orphans.append(x)
    return orphans


def drop_objects(fitz_doc: pymupdf.Document, xrefs: Iterable[int]) -> None:
    """Free objects ``xrefs`` (``pdf_delete_object``): the incremental writer then writes
    them as free entries, without their content."""
    pdf = pymupdf._as_pdf_document(fitz_doc)
    for x in xrefs:
        _mupdf.pdf_delete_object(pdf, int(x))
