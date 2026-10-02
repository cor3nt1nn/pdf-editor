"""Page structure operations on a ``pymupdf.Document`` (docs/M6_PLAN.md, M6a).

Pure functions; callers hold ``PdfDocument.lock`` for the live document. Facts behind
them (PyMuPDF 1.28.2, docs/M6_PLAN.md §0):

* ``delete_pages`` drops links to deleted pages and greys outline items, but leaves the
  deleted widgets in ``/AcroForm /Fields`` (P1): :func:`prune_fields` after every
  deletion and insertion.
* ``move_page(pno, to)`` removes ``pno`` and inserts it before the original page ``to``
  (P3); page xrefs are kept.
* ``insert_pdf`` mutates its source (P5): only ever graft from a throwaway copy opened
  from an in-memory write, never from the open document.
* ``Document.select()`` drops /AcroForm /Fields (P6): never used.
* Intermediate page tree nodes survive every operation, but one left without kids stays
  behind as ``/Kids [] /Count 0``: :func:`prune_empty_page_nodes` after deletions and moves.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pymupdf

from pdfeditor.core.forms import acroform_xref, strip_xfa

if TYPE_CHECKING:
    from pdfeditor.core.document import PasswordCallback

log = logging.getLogger(__name__)

#: A document keeps at least this many pages.
MIN_PAGES = 1

_REF = re.compile(r"(\d+)\s+0\s+R\b")


# -- AcroForm hygiene --------------------------------------------------------
def _refs(text: str | None) -> list[int]:
    return [int(m.group(1)) for m in _REF.finditer(text or "")]


def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # malformed object
        return ("null", "null")


def _array(xrefs: Iterable[int]) -> str:
    return "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]"


def _is_widget(doc: pymupdf.Document, xref: int) -> bool:
    """``xref`` is a widget annotation (possibly merged with its field): /Subtype
    /Widget, or a /Rect (a widget without /Subtype is still drawn by most readers)."""
    return _key(doc, xref, "Subtype") == ("name", "/Widget") or _key(doc, xref, "Rect")[0] != "null"


def prune_fields(doc: pymupdf.Document) -> int:
    """Remove from ``/AcroForm /Fields`` the widgets no page shows any more; returns the
    number of entries (fields and kids) dropped.

    A widget (``/Subtype /Widget`` or a /Rect, merged field or kid) is kept iff some
    page's /Annots references it; a field with /Kids is kept iff it keeps at least one kid
    (its /Kids is rewritten). A field that is not a widget and has no kids (a hidden
    calculated or data field) is always kept: it never belonged to a page
    (docs/ARCHITECTURE.md Deviation 92). Repeated references (MuPDF's graft can write
    ``/Kids [33 0 R 33 0 R]``) are kept once, cycles dropped. The array is written on
    the AcroForm object itself when it is indirect (the path form through the catalog
    writes ``fitz: replace me!``, PyMuPDF 1.28).
    """
    catalog = doc.pdf_catalog()
    kind, value = _key(doc, catalog, "AcroForm/Fields")
    if kind != "array":
        return 0
    live = {xref for page in doc for xref, _subtype, _nm in page.annot_xrefs()}
    dropped = 0
    decided: dict[int, bool] = {}

    def survivors(refs: list[int]) -> list[int]:
        nonlocal dropped
        kept: list[int] = []
        for x in refs:
            if x not in kept and keep(x):
                kept.append(x)
        dropped += len(refs) - len(kept)
        return kept

    def keep(xref: int) -> bool:
        if xref in decided:
            return decided[xref]
        decided[xref] = False  # a cycle back to this field does not keep it
        kids_kind, kids = _key(doc, xref, "Kids")
        old = _refs(kids) if kids_kind == "array" else []
        if old:
            kept = survivors(old)
            if kept and kept != old:
                doc.xref_set_key(xref, "Kids", _array(kept))
            result = bool(kept)
        elif _is_widget(doc, xref):
            result = xref in live
        else:
            result = True  # a field without widgets: not shown by any page
        decided[xref] = result
        return result

    old = _refs(value)
    kept = survivors(old)
    if kept != old:
        _set_acroform_key(doc, "Fields", _array(kept))
    # The calculation order loses the fields just dropped.
    co_kind, co = _key(doc, catalog, "AcroForm/CO")
    if co_kind == "array":
        order = _refs(co)
        still = [x for x in order if decided.get(x, True)]
        if still != order:
            _set_acroform_key(doc, "CO", _array(still))
    if dropped:
        log.debug("pruned %d form field entries", dropped)
    return dropped


def _set_acroform_key(doc: pymupdf.Document, key: str, value: str) -> None:
    """Set ``key`` of the /AcroForm: on the AcroForm object itself when it is indirect
    (the path form through the catalog writes ``fitz: replace me!``, PyMuPDF 1.28)."""
    acro = acroform_xref(doc)
    if acro:
        doc.xref_set_key(acro, key, value)
    else:
        doc.xref_set_key(doc.pdf_catalog(), f"AcroForm/{key}", value)


def has_calc_order(doc: pymupdf.Document) -> bool:
    """The /AcroForm has a /CO (calculation order) entry, even empty."""
    return _key(doc, doc.pdf_catalog(), "AcroForm/CO")[0] != "null"


def drop_empty_calc_order(doc: pymupdf.Document) -> bool:
    """Remove an empty ``/CO []`` (MuPDF's graft writes one into a form that had none);
    True if one was removed."""
    kind, value = _key(doc, doc.pdf_catalog(), "AcroForm/CO")
    if kind != "array" or _refs(value):
        return False
    # Setting the key to "null" writes a literal ``/CO null``: delete it.
    mupdf = pymupdf.mupdf
    pdf = pymupdf._as_pdf_document(doc)
    acro = acroform_xref(doc)
    if acro:
        form = mupdf.pdf_new_indirect(pdf, acro, 0)
    else:
        catalog = mupdf.pdf_new_indirect(pdf, doc.pdf_catalog(), 0)
        form = mupdf.pdf_dict_gets(catalog, "AcroForm")
    mupdf.pdf_dict_dels(form, "CO")
    return True


def has_xfa(doc: pymupdf.Document) -> bool:
    return _key(doc, doc.pdf_catalog(), "AcroForm/XFA")[0] != "null"


def has_acroform(doc: pymupdf.Document) -> bool:
    """The catalog has an /AcroForm (possibly with no fields)."""
    return _key(doc, doc.pdf_catalog(), "AcroForm")[0] != "null"


def drop_empty_acroform(doc: pymupdf.Document) -> bool:
    """Remove an /AcroForm that has no fields and no /XFA; True if one was removed.

    Undoing the insertion of pages with form fields into a document without a form
    deletes the pages and prunes their fields, but MuPDF's graft left an /AcroForm
    (``/Fields [] /CO []``, maybe /DR /DA): this puts the catalog back as it was.
    """
    if not has_acroform(doc) or has_xfa(doc):
        return False
    kind, value = _key(doc, doc.pdf_catalog(), "AcroForm/Fields")
    if kind not in ("null", "array") or _refs(value):
        return False
    mupdf = pymupdf.mupdf
    catalog = mupdf.pdf_new_indirect(pymupdf._as_pdf_document(doc), doc.pdf_catalog(), 0)
    mupdf.pdf_dict_dels(catalog, "AcroForm")
    return True


def strip_foreign_xfa(doc: pymupdf.Document) -> bool:
    """Remove an /XFA grafted by an insertion into a document that had none; True if one
    was removed (the inserted pages keep their AcroForm fields)."""
    return strip_xfa(doc) is not None


def _pages_root(doc: pymupdf.Document) -> int | None:
    refs = _refs(_key(doc, doc.pdf_catalog(), "Pages")[1])
    return refs[0] if refs else None


def prune_empty_page_nodes(doc: pymupdf.Document) -> int:
    """Remove the intermediate ``/Pages`` nodes left without kids; returns how many.

    MuPDF keeps an empty node (``/Kids [] /Count 0``) when every page under an
    intermediate node of the page tree is deleted or moved away. Such a node is valid for
    MuPDF and pypdf, but some readers choke on it, so it is dropped from its parent's
    /Kids (the counts need no change). The root node is always kept.
    """
    root = _pages_root(doc)
    if root is None:
        return 0
    removed = 0
    seen = {root}

    def keep(xref: int) -> bool:
        nonlocal removed
        kind, kids = _key(doc, xref, "Kids")
        if kind != "array":
            return True  # a page (or a node we do not understand): keep it
        old = _refs(kids)
        kept: list[int] = []
        for kid in old:
            if kid in seen:
                continue  # repeated or cyclic reference
            seen.add(kid)
            if keep(kid):
                kept.append(kid)
        if kept != old:
            removed += len(old) - len(kept)
            doc.xref_set_key(xref, "Kids", _array(kept))
        return bool(kept) or xref == root

    keep(root)
    if removed:
        log.debug("removed %d empty page tree nodes", removed)
    return removed


def field_names(doc: pymupdf.Document, pages: Iterable[int] | None = None) -> set[str]:
    """Fully qualified names of the fields shown on ``pages`` (default: every page)."""
    indexes = range(doc.page_count) if pages is None else pages
    names: set[str] = set()
    for i in indexes:
        for widget in doc[i].widgets():
            if widget.field_name:
                names.add(str(widget.field_name))
    return names


# -- structure ---------------------------------------------------------------
def _runs(pages: Sequence[int]) -> list[tuple[int, int]]:
    """Consecutive ascending runs of ``pages`` as (first, last), in the given order."""
    runs: list[tuple[int, int]] = []
    for p in pages:
        if runs and p == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], p)
        else:
            runs.append((p, p))
    return runs


def delete_pages(doc: pymupdf.Document, indexes: Iterable[int]) -> int:
    """Delete the pages ``indexes`` (any order, duplicates ignored), then prune the form
    fields; returns the number of pages deleted. Never leaves fewer than
    :data:`MIN_PAGES` (ValueError)."""
    targets = sorted(set(int(i) for i in indexes))
    if not targets:
        return 0
    if targets[0] < 0 or targets[-1] >= doc.page_count:
        raise IndexError(f"page index out of range: {targets}")
    if doc.page_count - len(targets) < MIN_PAGES:
        raise ValueError("a document must keep at least one page")
    doc.delete_pages(targets)
    prune_fields(doc)
    prune_empty_page_nodes(doc)
    return len(targets)


def reorder(doc: pymupdf.Document, new_order: Sequence[int]) -> None:
    """Rearrange the pages so that new position ``k`` holds old page ``new_order[k]``
    (a permutation of ``range(page_count)``). Page objects are moved, not copied."""
    n = doc.page_count
    order = [int(i) for i in new_order]
    if sorted(order) != list(range(n)):
        raise ValueError(f"not a permutation of {n} pages: {order}")
    current = list(range(n))  # current[k] = old index of the page now at k
    for target, old in enumerate(order):
        pos = current.index(old)
        if pos != target:
            # pos > target: everything before target is already in place.
            doc.move_page(pos, target)
            current.insert(target, current.pop(pos))
    assert current == order
    prune_empty_page_nodes(doc)


def insert_blank(doc: pymupdf.Document, index: int, width: float, height: float) -> None:
    """Insert an empty ``width`` x ``height`` page so that it becomes page ``index``."""
    if not 0 <= index <= doc.page_count:
        raise IndexError(f"insert position out of range: {index}")
    had_labels = _has_page_labels(doc)
    doc.new_page(index if index < doc.page_count else -1, width=width, height=height)
    _drop_new_page_labels(doc, had_labels)


def _has_page_labels(doc: pymupdf.Document) -> bool:
    return _key(doc, doc.pdf_catalog(), "PageLabels")[0] != "null"


def _drop_new_page_labels(doc: pymupdf.Document, had_labels: bool) -> None:
    """Remove a /PageLabels that MuPDF created while inserting at page 0 of a document
    without labels: it writes ``[0 <</S/D>> n <</S/D>>]``, which makes the old first page
    restart at "1" (labels 1, 1, 2, 3...; docs/ARCHITECTURE.md M6a deviations)."""
    if not had_labels and _has_page_labels(doc):
        mupdf = pymupdf.mupdf
        catalog = mupdf.pdf_new_indirect(pymupdf._as_pdf_document(doc), doc.pdf_catalog(), 0)
        mupdf.pdf_dict_dels(catalog, "PageLabels")


def insert_pages(
    doc: pymupdf.Document,
    src: pymupdf.Document,
    index: int,
    pages: Sequence[int] | None = None,
    *,
    had_xfa: bool,
) -> int:
    """Copy ``pages`` of ``src`` (default all, in the given order) so that the first one
    becomes page ``index``; returns the number of pages inserted.

    ``src`` is mutated (P5): pass a throwaway document. Links between copied pages are
    remapped, annotations and form fields copied (names already in ``doc`` are renamed
    "name [xref]" by MuPDF); the fields are then pruned and an /XFA brought by ``src`` is
    removed unless the target ``had_xfa``.
    """
    if not 0 <= index <= doc.page_count:
        raise IndexError(f"insert position out of range: {index}")
    selected = list(range(src.page_count)) if pages is None else [int(p) for p in pages]
    if any(not 0 <= p < src.page_count for p in selected):
        raise IndexError(f"source page out of range: {selected}")
    had_co = has_calc_order(doc)
    if len(_runs(selected)) == 1:
        first, last = selected[0], selected[-1]
        _graft(doc, src, first, last, index)
    else:
        # A second graft from the same source loses its fields (P5): copy once.
        sub = subdocument(src, selected)
        try:
            _graft(doc, sub, 0, sub.page_count - 1, index)
        finally:
            sub.close()
    prune_fields(doc)
    if not had_co:
        drop_empty_calc_order(doc)
    if not had_xfa and has_xfa(doc):
        strip_foreign_xfa(doc)
    return len(selected)


def _graft(doc: pymupdf.Document, src: pymupdf.Document, first: int, last: int, at: int) -> None:
    """``insert_pdf`` of ``src`` pages ``first..last`` so that they start at page ``at``,
    then point every copied annotation's /P back at its page.

    MuPDF's graft drops /P (P4): two widgets of one field on different pages may then be
    identical objects, which a ``garbage=3`` save merges into one widget shown on both
    pages (seen with the LibreOffice-like form's "Nom" field).
    """
    count = last - first + 1
    had_labels = _has_page_labels(doc)
    doc.insert_pdf(
        src,
        from_page=first,
        to_page=last,
        start_at=at if at < doc.page_count else -1,
        links=True,
        annots=True,
    )
    _drop_new_page_labels(doc, had_labels)
    for i in range(at, at + count):
        page_xref = doc.page_xref(i)
        for xref, _subtype, _nm in doc[i].annot_xrefs():
            doc.xref_set_key(xref, "P", f"{page_xref} 0 R")


def subdocument(src: pymupdf.Document, pages: Sequence[int]) -> pymupdf.Document:
    """A new document holding copies of ``pages`` of ``src`` in that order (no
    duplicates; mutates ``src``, P5).

    One graft only: a second ``insert_pdf`` from the same source loses the form fields
    the first one detached. The span from the lowest to the highest page is copied, then
    the pages not asked for are deleted and the rest put in order.
    """
    selected = [int(p) for p in pages]
    if not selected or len(set(selected)) != len(selected):
        raise ValueError(f"pages must be distinct and non-empty: {selected}")
    if any(not 0 <= p < src.page_count for p in selected):
        raise IndexError(f"source page out of range: {selected}")
    low, high = min(selected), max(selected)
    out = pymupdf.open()
    try:
        _graft(out, src, low, high, 0)
        unwanted = sorted(set(range(high - low + 1)) - {p - low for p in selected})
        if unwanted:
            out.delete_pages(unwanted)
        kept = sorted(selected)
        reorder(out, [kept.index(p) for p in selected])
        prune_fields(out)
    except Exception:
        out.close()
        raise
    return out


def subdocument_bytes(src: pymupdf.Document, pages: Sequence[int]) -> bytes:
    """An unencrypted in-memory file holding ``pages`` of ``src`` (mutates ``src``)."""
    out = subdocument(src, pages)
    try:
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


# -- output encryption (D5) --------------------------------------------------
@dataclass(frozen=True)
class OutputEncryption:
    """How extracted/split files are protected (``tobytes`` arguments)."""

    method: int = pymupdf.PDF_ENCRYPT_NONE
    user_pw: str = ""
    owner_pw: str = ""
    permissions: int = -1

    def kwargs(self) -> dict[str, object]:
        if self.method == pymupdf.PDF_ENCRYPT_NONE:
            return {"encryption": pymupdf.PDF_ENCRYPT_NONE}
        return {
            "encryption": self.method,
            "user_pw": self.user_pw,
            "owner_pw": self.owner_pw,
            "permissions": self.permissions,
        }


NO_ENCRYPTION = OutputEncryption()


def output_encryption(
    *, is_encrypted: bool, has_restrictions: bool, password: str | None, permissions: int
) -> OutputEncryption:
    """Protection of files made from pages of a document (docs/M6_PLAN.md D5).

    A file opened with a password: AES-256, user and owner password = that password, same
    permissions. Restricted by its author without a user password: AES-256, empty user
    password, random owner password, same permissions. Otherwise unencrypted.
    """
    if is_encrypted and password:
        return OutputEncryption(pymupdf.PDF_ENCRYPT_AES_256, password, password, permissions)
    if has_restrictions:
        return OutputEncryption(
            pymupdf.PDF_ENCRYPT_AES_256, "", secrets.token_urlsafe(24), permissions
        )
    return NO_ENCRYPTION


def extract_bytes(
    copy: pymupdf.Document,
    pages: Sequence[int],
    *,
    encryption: OutputEncryption = NO_ENCRYPTION,
) -> bytes:
    """A complete new file holding ``pages`` of ``copy`` (a throwaway document: it is
    mutated), fully rewritten (``garbage=4``) with ``encryption``. A static XFA form's
    /XFA is not kept: its template describes every page of the original."""
    out = subdocument(copy, pages)
    try:
        strip_xfa(out)
        return out.tobytes(garbage=4, deflate=True, **encryption.kwargs())
    finally:
        out.close()


# -- page ranges ---------------------------------------------------------------
class PageRangeError(ValueError):
    """A page range text is invalid; ``token`` is the offending part."""

    def __init__(self, token: str) -> None:
        super().__init__(f"invalid page range: {token!r}")
        self.token = token


_RANGE = re.compile(r"^(\d*)\s*-\s*(\d*)$")


def _parse_token(token: str, count: int) -> list[int]:
    text = token.strip()
    if text.isdigit():
        first = last = int(text)
    else:
        m = _RANGE.match(text)
        if m is None or not (m.group(1) or m.group(2)):
            raise PageRangeError(text)
        first = int(m.group(1)) if m.group(1) else 1
        last = int(m.group(2)) if m.group(2) else count
    if not 1 <= first <= last <= count:
        raise PageRangeError(text)
    return list(range(first - 1, last))


def _tokens(text: str) -> list[str]:
    tokens = [t.strip() for t in text.split(",")]
    if not any(tokens):
        raise PageRangeError(text.strip())
    for t in tokens:
        if not t:
            raise PageRangeError(t)
    return tokens


def parse_page_ranges(text: str, count: int) -> list[int]:
    """0-based page indexes of a 1-based range text such as "1-3, 7, 9-" ("-3" = 1-3,
    "9-" = 9 to the end), in the given order, duplicates dropped. Raises
    :class:`PageRangeError` naming the offending token."""
    out: list[int] = []
    seen: set[int] = set()
    for token in _tokens(text):
        for i in _parse_token(token, count):
            if i not in seen:
                seen.add(i)
                out.append(i)
    return out


def split_every(count: int, n: int) -> list[list[int]]:
    """Groups of ``n`` consecutive pages (the last may be shorter)."""
    if n < 1:
        raise ValueError(f"group size must be positive: {n}")
    return [list(range(i, min(i + n, count))) for i in range(0, count, n)]


def split_ranges(text: str, count: int) -> list[list[int]]:
    """One group per comma-separated range of ``text`` ("1-3, 4-6, 7-")."""
    return [_parse_token(token, count) for token in _tokens(text)]


# -- opening another file ----------------------------------------------------
def open_source(
    path: str, password_cb: PasswordCallback | None = None, password: str | None = None
) -> tuple[pymupdf.Document, str | None]:
    """Open the PDF ``path`` to copy pages from: (document, password used or None).

    Same rules as :meth:`PdfDocument.open` (``needs_pass`` read before authenticating,
    ``password_cb`` asked until right or ``None``). Raises ``OpenError`` /
    ``PasswordRequired``. The caller closes the document.
    """
    from pdfeditor.core.document import OpenError, authenticate, open_fitz

    fitz_doc, _stamp = open_fitz(str(path))
    used: str | None = None
    if fitz_doc.needs_pass:
        used, _owner = authenticate(fitz_doc, password_cb, password)
    if fitz_doc.page_count == 0:
        fitz_doc.close()
        raise OpenError(f"document has no pages: {path}", reason="no_pages")
    return fitz_doc, used
