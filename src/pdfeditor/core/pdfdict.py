"""Read and write nested PDF dictionary entries through indirect objects (M7).

``Document.xref_get_key``/``xref_set_key`` accept a path such as ``"Resources/Font/F1"``
but cannot step through an *indirect* sub-dictionary (``/Resources 12 0 R``): they treat
it as absent and, when writing, replace the reference by a new direct dictionary
(docs/M7_PLAN.md U2). :func:`get_nested` and :func:`set_nested` descend such references
manually. Shared by ``core/fontembed.py`` and ``core/textedit.py``; the caller holds
``PdfDocument.lock``.
"""

from __future__ import annotations

import re

import pymupdf

_REF_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+R\s*$")
_ENTRY_RE = re.compile(r"/([^\s/<>\[\]()%{}]+)\s*(\d+)\s+\d+\s+R")
#: Deepest /Parent chain followed for inherited page attributes.
MAX_PARENT_DEPTH = 64


def ref_xref(value: str) -> int:
    """The object number of an ``"N G R"`` reference, 0 when ``value`` is not one."""
    m = _REF_RE.match(value)
    return int(m.group(1)) if m else 0


def _get(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # noqa: BLE001 - a broken object reads as absent
        return "null", "null"


def get_nested(
    doc: pymupdf.Document, xref: int, path: str, *, resolve: bool = False
) -> tuple[str, str]:
    """``(kind, value)`` of ``path`` ("A/B/C") in object ``xref``, following indirect
    dictionaries on the way. ``resolve`` also follows a final reference (the value is
    then the target object's source and the kind ``"dict"``, ``"array"``, …).
    ``("null", "null")`` when absent."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        raise ValueError("empty path")
    prefix: list[str] = []
    for i, part in enumerate(parts):
        key = "/".join([*prefix, part])
        kind, value = _get(doc, xref, key)
        last = i == len(parts) - 1
        if kind == "xref" and (not last or resolve):
            target = ref_xref(value)
            if not target:
                return "null", "null"
            if last:
                return _object_kind(doc, target)
            return get_nested(doc, target, "/".join(parts[i + 1 :]), resolve=resolve)
        if last:
            return kind, value
        if kind != "dict":
            return "null", "null"
        prefix.append(part)
    return "null", "null"  # pragma: no cover - loop always returns


def _object_kind(doc: pymupdf.Document, xref: int) -> tuple[str, str]:
    try:
        source = doc.xref_object(xref, compressed=True).strip()
    except Exception:  # noqa: BLE001
        return "null", "null"
    if source.startswith("<<"):
        return "dict", source
    if source.startswith("["):
        return "array", source
    if source in ("", "null"):
        return "null", "null"
    return "other", source


def set_nested(doc: pymupdf.Document, xref: int, path: str, value: str) -> None:
    """Set ``path`` ("A/B/C") of object ``xref`` to ``value`` (PDF source such as
    ``"12 0 R"`` or ``"/Name"``), descending indirect dictionaries (the referenced object
    is changed, so every user of a shared dictionary sees the new entry) and creating
    missing intermediate dictionaries. ``ValueError`` when an intermediate entry is not a
    dictionary."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        raise ValueError("empty path")
    prefix: list[str] = []
    for i, part in enumerate(parts[:-1]):
        key = "/".join([*prefix, part])
        kind, current = _get(doc, xref, key)
        if kind == "xref":
            target = ref_xref(current)
            if not target or _object_kind(doc, target)[0] != "dict":
                raise ValueError(f"{key} of object {xref} is not a dictionary")
            set_nested(doc, target, "/".join(parts[i + 1 :]), value)
            return
        if kind == "null":
            doc.xref_set_key(xref, key, "<<>>")
        elif kind != "dict":
            raise ValueError(f"{key} of object {xref} is a {kind}, not a dictionary")
        prefix.append(part)
    doc.xref_set_key(xref, "/".join([*prefix, parts[-1]]), value)


def dict_refs(source: str) -> dict[str, int]:
    """``name -> object number`` of the indirect entries of a dictionary's source text
    (only the top level is meaningful: use it on flat dictionaries such as
    ``/Resources/Font``)."""
    return {m.group(1): int(m.group(2)) for m in _ENTRY_RE.finditer(source)}


def inherited_owner(doc: pymupdf.Document, page_xref: int, key: str) -> int:
    """The object holding an inheritable page attribute (``Resources``, ``MediaBox``…):
    the page itself, else the nearest ``/Parent`` with the entry; 0 when none has it."""
    xref = page_xref
    seen: set[int] = set()
    for _ in range(MAX_PARENT_DEPTH):
        if _get(doc, xref, key)[0] != "null":
            return xref
        seen.add(xref)
        kind, parent = _get(doc, xref, "Parent")
        xref = ref_xref(parent) if kind == "xref" else 0
        if not xref or xref in seen:
            break
    return 0


def inherited(doc: pymupdf.Document, page_xref: int, key: str) -> tuple[str, str]:
    """``(kind, value)`` of an inheritable page attribute, ``("null", "null")`` when
    neither the page nor its ancestors have it."""
    owner = inherited_owner(doc, page_xref, key)
    return _get(doc, owner, key) if owner else ("null", "null")
