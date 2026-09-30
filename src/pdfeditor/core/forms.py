"""AcroForm widgets: reading and filling (M2).

Pure functions on ``pymupdf.Document`` / ``pymupdf.Page``. Callers hold
``PdfDocument.lock``. Never keep ``pymupdf.Page`` / ``Widget`` objects across calls:
every save replaces the document and full saves renumber xrefs. Identify a widget by
``(page, xref, name, unrotated_rect)`` and re-fetch it with :func:`resolve_widget`.

Checkbox / radio values are *names*: ``WidgetInfo.on_state_token`` is the raw token as
it appears in the file (possibly ``#xx``-escaped, e.g. ``Case#20#C3#A0#20cocher#201_3``,
which must be written back verbatim); compare values with :func:`decode_pdf_name`.

Writing: text/choice values go through ``Widget.update()`` (it regenerates the
appearance streams); checkbox/radio states are written directly as /AS and /V so the
authored appearances are kept (``Widget.update()`` would replace them, and it silently
fails on #-escaped on-state names). ``Document.need_appearances()`` is never called (its
getter writes /NeedAppearances false).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import pymupdf
from PySide6.QtCore import QRectF

from pdfeditor.core.geometry import qrect_from_fitz, unrotated_to_page

log = logging.getLogger(__name__)

# Field flags (/Ff) and annotation flags (/F), PDF 32000-1 tables 221, 226, 228, 230, 165.
FF_READ_ONLY = 1
FF_MULTILINE = 1 << 12
FF_COMB = 1 << 24
FF_NO_TOGGLE_TO_OFF = 1 << 14
FF_RADIO = 1 << 15
FF_COMBO = 1 << 17
FF_EDIT = 1 << 18
FF_MULTI_SELECT = 1 << 21
ANNOT_HIDDEN = 2
ANNOT_NO_VIEW = 32

#: Tolerance (points) when matching a widget rect after re-resolution.
RECT_TOLERANCE = 0.01


class FieldKind(StrEnum):
    TEXT = "text"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    COMBO = "combo"
    LIST = "list"
    BUTTON = "button"
    SIGNATURE = "signature"
    UNKNOWN = "unknown"


class XfaKind(StrEnum):
    NONE = "none"
    STATIC = "static"
    DYNAMIC = "dynamic"


_KIND_BY_TYPE = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: FieldKind.TEXT,
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: FieldKind.CHECKBOX,
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: FieldKind.RADIO,
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: FieldKind.COMBO,
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: FieldKind.LIST,
    pymupdf.PDF_WIDGET_TYPE_BUTTON: FieldKind.BUTTON,
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: FieldKind.SIGNATURE,
}

_FILLABLE = frozenset(
    {FieldKind.TEXT, FieldKind.CHECKBOX, FieldKind.RADIO, FieldKind.COMBO, FieldKind.LIST}
)


@dataclass(frozen=True)
class WidgetInfo:
    """Snapshot of one widget annotation (no live pymupdf object)."""

    page: int
    #: Xref of the widget annotation.
    xref: int
    #: Xref of the field dictionary holding /V (the widget itself when it has /T,
    #: else its /Parent, e.g. radio kids or multi-page text fields).
    field_xref: int
    #: Fully qualified field name, decoded.
    name: str
    kind: FieldKind
    #: Text/choice: the /V string ("" when unset). Checkbox/radio: this widget's
    #: decoded /AS ("Off" when off).
    value: str
    #: Decoded on-state name of a checkbox/radio widget ("" for other kinds).
    on_state: str
    #: Raw on-state token as stored in the file (write it back verbatim).
    on_state_token: str
    #: (export value, display text) pairs of a combo/list box.
    choices: tuple[tuple[str, str], ...]
    #: Field flags (/Ff, inherited).
    flags: int
    #: Annotation flags (/F).
    annot_flags: int
    max_len: int
    #: Font size from /DA; 0 = auto-size.
    font_size: float
    #: Widget rect in page space (rotation applied), points.
    rect: QRectF
    #: Raw widget rect (x0, y0, x1, y1) in unrotated page coordinates (``widget.rect``).
    unrotated_rect: tuple[float, float, float, float]

    @property
    def read_only(self) -> bool:
        return bool(self.flags & FF_READ_ONLY)

    @property
    def hidden(self) -> bool:
        return bool(self.annot_flags & (ANNOT_HIDDEN | ANNOT_NO_VIEW))

    @property
    def multiline(self) -> bool:
        return self.kind is FieldKind.TEXT and bool(self.flags & FF_MULTILINE)

    @property
    def editable_combo(self) -> bool:
        return self.kind is FieldKind.COMBO and bool(self.flags & FF_EDIT)

    @property
    def editable(self) -> bool:
        """The user can fill this widget (highlighted, clickable, tabbable)."""
        return (
            self.kind in _FILLABLE
            and not self.read_only
            and not self.hidden
            and not self.rect.isEmpty()
        )

    @property
    def is_on(self) -> bool:
        """A checkbox/radio widget is checked."""
        return self.kind in (FieldKind.CHECKBOX, FieldKind.RADIO) and self.value not in (
            "",
            "Off",
        )


_HEX_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")


def decode_pdf_name(token: str) -> str:
    """Decode a PDF name token: strip a leading "/", expand ``#xx``, decode UTF-8.

    Falls back to Latin-1 when the bytes are not UTF-8. ``"Case#20#C3#A0"`` -> ``"Case à"``.
    """
    if token.startswith("/"):
        token = token[1:]
    if "#" not in token:
        return token
    raw = _HEX_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), token.encode("utf-8"))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# -- low-level dictionary helpers ------------------------------------------
_REF = re.compile(r"(\d+)\s+\d+\s+R")


def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # malformed object
        return ("null", "null")


def _ref(doc: pymupdf.Document, xref: int, key: str) -> int:
    kind, value = _key(doc, xref, key)
    if kind == "xref":
        m = _REF.match(value)
        if m:
            return int(m.group(1))
    return 0


def _int(doc: pymupdf.Document, xref: int, key: str, default: int = 0) -> int:
    kind, value = _key(doc, xref, key)
    if kind == "int":
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _name(doc: pymupdf.Document, xref: int, key: str) -> str:
    """Raw name token (without "/") of ``key``, or ""."""
    kind, value = _key(doc, xref, key)
    return value[1:] if kind == "name" and value.startswith("/") else ""


def _kids(doc: pymupdf.Document, xref: int) -> list[int]:
    kind, value = _key(doc, xref, "Kids")
    if kind != "array":
        return []
    return [int(m.group(1)) for m in _REF.finditer(value)]


def _field_xref(doc: pymupdf.Document, widget_xref: int) -> int:
    """The terminal field holding /V: the widget itself if it has /T, else its /Parent."""
    if _key(doc, widget_xref, "T")[0] != "null":
        return widget_xref
    parent = _ref(doc, widget_xref, "Parent")
    return parent or widget_xref


def acroform_xref(fitz_doc: pymupdf.Document) -> int:
    """Xref of the /AcroForm dictionary, or 0 when it is absent or a direct dictionary."""
    return _ref(fitz_doc, fitz_doc.pdf_catalog(), "AcroForm")


# -- XFA -------------------------------------------------------------------
_DYNAMIC_RENDER = re.compile(rb"<dynamicRender>\s*required\s*</dynamicRender>")
_XFA_PACKET = re.compile(r"\(([^)]*)\)\s*(\d+)\s+\d+\s+R")


def _xfa_streams(doc: pymupdf.Document, value: tuple[str, str]) -> list[bytes]:
    """Streams holding the XFA config: the ``config`` packets of an /XFA array (every
    packet if none is named so), or the single /XFA stream."""
    kind, raw = value
    xrefs: list[int] = []
    if kind == "array":
        packets = _XFA_PACKET.findall(raw)
        xrefs = [int(x) for name, x in packets if name == "config"]
        xrefs = xrefs or [int(x) for _name, x in packets]
    elif kind == "xref":
        m = _REF.match(raw)
        xrefs = [int(m.group(1))] if m else []
    out: list[bytes] = []
    for x in xrefs:
        try:
            if doc.xref_is_stream(x):
                out.append(doc.xref_stream(x) or b"")
        except Exception:  # unreadable packet: ignore
            log.warning("unreadable XFA packet xref %s", x, exc_info=True)
    return out


def detect_xfa(fitz_doc: pymupdf.Document) -> XfaKind:
    """Classify the document's XFA forms.

    ``NONE``: no /AcroForm /XFA. ``DYNAMIC``: the config says
    ``<dynamicRender>required</dynamicRender>``, or there are no AcroForm fields (nothing
    to fill without an XFA engine). ``STATIC``: XFA over a usable AcroForm.
    """
    value = _key(fitz_doc, fitz_doc.pdf_catalog(), "AcroForm/XFA")
    if value[0] == "null":
        return XfaKind.NONE
    if any(_DYNAMIC_RENDER.search(s) for s in _xfa_streams(fitz_doc, value)):
        return XfaKind.DYNAMIC
    if not fitz_doc.is_form_pdf:  # False (no AcroForm) or 0 fields
        return XfaKind.DYNAMIC
    return XfaKind.STATIC


def strip_xfa(fitz_doc: pymupdf.Document) -> str | None:
    """Remove /XFA from the AcroForm; returns its previous raw value, or None if absent.

    With an indirect AcroForm the key is set on the AcroForm object itself: the path form
    ``xref_set_key(catalog, "AcroForm/XFA", "null")`` writes a literal
    ``fitz: replace me!`` through the reference (PyMuPDF 1.28 bug, F12).
    """
    catalog = fitz_doc.pdf_catalog()
    kind, value = _key(fitz_doc, catalog, "AcroForm/XFA")
    if kind == "null":
        return None
    acro = acroform_xref(fitz_doc)
    if acro:
        fitz_doc.xref_set_key(acro, "XFA", "null")
    else:
        fitz_doc.xref_set_key(catalog, "AcroForm/XFA", "null")
    return value


# -- reading ---------------------------------------------------------------
def _choices(widget: pymupdf.Widget) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for item in widget.choice_values or ():
        if isinstance(item, (list, tuple)):
            if len(item) >= 2:
                out.append((str(item[0]), str(item[1])))
            elif item:
                out.append((str(item[0]), str(item[0])))
        else:
            out.append((str(item), str(item)))
    return tuple(out)


def _on_state_token(widget: pymupdf.Widget) -> str:
    states = widget.button_states() or {}
    for key in ("normal", "down"):
        for token in states.get(key) or ():
            if token and token != "Off":
                return str(token)
    return ""


def _text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):  # multi-select list: first selection
        return str(value[0]) if value else ""
    return str(value)


def widget_kind(widget: pymupdf.Widget) -> FieldKind:
    """The :class:`FieldKind` of a live widget."""
    return _KIND_BY_TYPE.get(widget.field_type, FieldKind.UNKNOWN)


#: Larger coordinates (points) mean a missing or corrupt /Rect (MuPDF's "infinite" rect).
MAX_COORD = 1e6


def _page_rect(raw: pymupdf.Rect, page: pymupdf.Page) -> QRectF:
    """Widget rect in page space; empty (so the widget is not editable) for a missing,
    invalid or degenerate /Rect."""
    if (
        not raw.is_valid
        or raw.is_empty
        or not all(math.isfinite(v) and abs(v) < MAX_COORD for v in raw)
    ):
        return QRectF()
    return qrect_from_fitz(unrotated_to_page(raw, page.rotation_matrix))


def _info(doc: pymupdf.Document, page: pymupdf.Page, index: int, w: pymupdf.Widget) -> WidgetInfo:
    kind = widget_kind(w)
    xref = int(w.xref)
    token = ""
    if kind in (FieldKind.CHECKBOX, FieldKind.RADIO):
        token = _on_state_token(w)
        state = _name(doc, xref, "AS")
        if not state and _key(doc, xref, "AS")[0] == "null":
            # No /AS: fall back to the field value.
            state = _name(doc, _field_xref(doc, xref), "V")
        value = decode_pdf_name(state) if state else "Off"
    else:
        value = _text_value(w.field_value)
    raw = pymupdf.Rect(w.rect)
    return WidgetInfo(
        page=index,
        xref=xref,
        field_xref=_field_xref(doc, xref),
        name=w.field_name or "",
        kind=kind,
        value=value,
        on_state=decode_pdf_name(token) if token else "",
        on_state_token=token,
        choices=_choices(w) if kind in (FieldKind.COMBO, FieldKind.LIST) else (),
        flags=int(w.field_flags or 0),
        annot_flags=_int(doc, xref, "F"),
        max_len=int(w.text_maxlen or 0) if kind is FieldKind.TEXT else 0,
        font_size=float(w.text_fontsize or 0),
        rect=_page_rect(raw, page),
        unrotated_rect=(raw.x0, raw.y0, raw.x1, raw.y1),
    )


def read_widgets(fitz_doc: pymupdf.Document, page_index: int) -> list[WidgetInfo]:
    """All widgets of a page, in /Annots order (not reading order; see :func:`tab_order`)."""
    page = fitz_doc[page_index]  # keep the Page alive while its widgets are used
    out: list[WidgetInfo] = []
    for w in page.widgets():
        try:
            out.append(_info(fitz_doc, page, page_index, w))
        except Exception:  # one malformed widget must not hide the others
            log.warning("skipping unreadable widget xref %s", w.xref, exc_info=True)
    return out


def _rect_matches(rect: pymupdf.Rect, expected: tuple[float, float, float, float]) -> bool:
    return all(abs(a - b) <= RECT_TOLERANCE for a, b in zip(rect, expected, strict=True))


def resolve_widget(
    fitz_doc: pymupdf.Document,
    page_index: int,
    xref: int,
    name: str,
    unrotated_rect: tuple[float, float, float, float] | None = None,
) -> tuple[pymupdf.Page, pymupdf.Widget] | None:
    """Find a live widget again, e.g. after a full save renumbered the xrefs.

    Tries ``page.load_widget(xref)`` and checks the name (and rect when given); otherwise
    scans the page for a widget with that name and rect. Returns ``(page, widget)`` — keep
    the page referenced while using the widget — or ``None`` if it is gone.
    """
    page = fitz_doc[page_index]

    def matches(w: pymupdf.Widget) -> bool:
        if (w.field_name or "") != name:
            return False
        return unrotated_rect is None or _rect_matches(w.rect, unrotated_rect)

    try:
        w = page.load_widget(xref)
    except Exception:
        w = None
    if w is not None and matches(w):
        return page, w
    for w in page.widgets():
        if matches(w):
            return page, w
    return None


def _field_widget_xrefs(doc: pymupdf.Document, field_xref: int) -> set[int]:
    """Xrefs of the widget annotations of a field (its terminal /Kids, or itself)."""
    widget_xrefs: set[int] = set()
    stack, seen = [field_xref], set()
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        kids = _kids(doc, x)
        if kids:
            stack.extend(kids)
        else:
            widget_xrefs.add(x)
    return widget_xrefs


def _pages_showing(doc: pymupdf.Document, widget_xrefs: set[int]) -> list[int]:
    pages = []
    for i in range(doc.page_count):
        refs = {int(item[0]) for item in doc[i].annot_xrefs()}
        if refs & widget_xrefs:
            pages.append(i)
    return pages


def field_pages(fitz_doc: pymupdf.Document, field_xref: int) -> list[int]:
    """Indices of the pages showing a widget of this field (sorted)."""
    return _pages_showing(fitz_doc, _field_widget_xrefs(fitz_doc, field_xref))


def tab_order(widgets: Iterable[WidgetInfo]) -> list[WidgetInfo]:
    """Editable widgets in geometric reading order: by page, row, then left edge.

    A row groups widgets whose vertical centre lies within ``max(3pt, 0.6 * min height)``
    of the row's first widget. /Fields and /Annots order are ignored (often arbitrary).
    """
    by_page: dict[int, list[WidgetInfo]] = {}
    for info in widgets:
        if info.editable:
            by_page.setdefault(info.page, []).append(info)
    out: list[WidgetInfo] = []
    for page in sorted(by_page):
        rows: list[list[WidgetInfo]] = []
        for info in sorted(by_page[page], key=lambda w: (w.rect.center().y(), w.rect.left())):
            if rows:
                ref = rows[-1][0]
                tol = max(3.0, 0.6 * min(ref.rect.height(), info.rect.height()))
                if abs(info.rect.center().y() - ref.rect.center().y()) <= tol:
                    rows[-1].append(info)
                    continue
            rows.append([info])
        for row in rows:
            out.extend(sorted(row, key=lambda w: w.rect.left()))
    return out


def text_fits(text: str, font_size: float, width_pt: float, padding: float = 4.0) -> bool:
    """True if every line of ``text`` fits in ``width_pt`` in Helvetica at ``font_size``.

    ``font_size`` 0 (auto-size) always fits.
    """
    if font_size <= 0:
        return True
    available = width_pt - padding
    return all(
        pymupdf.get_text_length(line, fontname="helv", fontsize=font_size) <= available
        for line in text.splitlines() or [""]
    )


# -- writing ---------------------------------------------------------------
def _field_widgets(
    doc: pymupdf.Document, page: pymupdf.Page, widget: pymupdf.Widget
) -> tuple[int, list[tuple[pymupdf.Page, int]]]:
    """(field xref, [(page, widget xref)]) of every widget of ``widget``'s field.

    The pages are returned so that callers keep them alive while using their widgets.
    Only scans the document when the field has several widgets.
    """
    field_xref = _field_xref(doc, int(widget.xref))
    xrefs = _field_widget_xrefs(doc, field_xref)
    xrefs.add(int(widget.xref))
    if xrefs == {int(widget.xref)}:
        return field_xref, [(page, int(widget.xref))]
    out: list[tuple[pymupdf.Page, int]] = []
    for i in _pages_showing(doc, xrefs):
        p = page if i == page.number else doc[i]
        refs = {int(item[0]) for item in p.annot_xrefs()}
        out.extend((p, x) for x in sorted(refs & xrefs))
    return field_xref, out


_NAME_KEY = re.compile(r"/([^\s/<>\[\]()]+)")


def _state_tokens(doc: pymupdf.Document, widget_xref: int) -> list[str]:
    """Raw appearance state names (without "/") of a button widget's /AP /N (and /D)."""
    tokens: list[str] = []
    for key in ("AP/N", "AP/D"):
        kind, value = _key(doc, widget_xref, key)
        if kind == "xref":
            m = _REF.match(value)
            if not m or doc.xref_is_stream(int(m.group(1))):  # single appearance: no states
                continue
            value = doc.xref_object(int(m.group(1)), compressed=True)
            kind = "dict" if value.lstrip().startswith("<<") else kind
        if kind != "dict":
            continue
        # Top-level keys only: values are references or streams, never names.
        for token in _NAME_KEY.findall(value):
            if token not in tokens:
                tokens.append(token)
    return tokens


def set_button_state(
    fitz_doc: pymupdf.Document, page: pymupdf.Page, widget: pymupdf.Widget, on: bool | str
) -> list[int]:
    """Check/uncheck a checkbox or select a radio button; returns the affected pages.

    ``on`` is ``True`` (this widget's on state), ``False`` / ``"Off"`` (field off) or the
    decoded on-state name to select among the field's widgets. Writes /AS on every widget
    of the field (the matching ones on, the others /Off) and /V on the field dictionary,
    with the on-state token verbatim; the authored appearance streams are kept. A widget
    without appearance states falls back to ``Widget.update()``.
    """
    if isinstance(on, bool):
        token = _on_state_token(widget)
        if on and not token:  # no appearance states: let MuPDF build them
            widget.field_value = True
            widget.update()
            return [page.number]
        target = decode_pdf_name(token) if on else ""
    else:
        target = "" if on in ("", "Off") else on
    field_xref, members = _field_widgets(fitz_doc, page, widget)
    chosen = ""
    for _page, x in members:
        match = ""
        if target:
            states = (t for t in _state_tokens(fitz_doc, x) if t != "Off")
            match = next((t for t in states if decode_pdf_name(t) == target), "")
        chosen = chosen or match
        fitz_doc.xref_set_key(x, "AS", "/" + (match or "Off"))
    if target and not chosen:
        log.warning("no widget of field xref %s has the state %r", field_xref, target)
    fitz_doc.xref_set_key(field_xref, "V", "/" + (chosen or "Off"))
    return sorted({p.number for p, _x in members})


def set_text_value(
    fitz_doc: pymupdf.Document,
    page: pymupdf.Page,
    widget: pymupdf.Widget,
    value: str,
    font_size: float | None = None,
) -> list[int]:
    """Set a text/combo/list value and regenerate the appearance of each of its widgets.

    A combo/list ``value`` is the export value; when its display text differs, the
    appearance shows the display text and /V holds the export value.
    ``font_size`` (0 = auto-size) rewrites the widgets' /DA; ``None`` keeps it. An empty
    ``value`` clears the field: ``field_value = ""`` + ``update()`` leaves /V untouched,
    so /V is reset on the field dictionary first (``()`` for text, removed for choices),
    then every widget's appearance is regenerated empty. Returns the affected pages.
    """
    field_xref, members = _field_widgets(fitz_doc, page, widget)
    is_text = widget.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT
    if value == "":
        fitz_doc.xref_set_key(field_xref, "V", "()" if is_text else "null")
    # Choice whose export value differs from its display text: ``update()`` draws the
    # value it is given, so draw the display text, then store the export value in /V.
    shown = value
    if not is_text and value:
        shown = next((d for e, d in _choices(widget) if e == value), value)
    for p, x in members:
        w = widget if x == int(widget.xref) else p.load_widget(x)
        if w is None:
            continue
        w.field_value = shown
        if font_size is not None:
            w.text_fontsize = font_size
        w.update()
    if shown != value:
        fitz_doc.xref_set_key(field_xref, "V", pymupdf.get_pdf_str(value))
    return sorted({p.number for p, _x in members})


def field_button_state(
    fitz_doc: pymupdf.Document, page: pymupdf.Page, widget: pymupdf.Widget
) -> str:
    """Decoded on state of a checkbox/radio field across **all** its pages ("Off").

    The /AS of the first widget of the field that is on (radio kids may sit on other
    pages than ``widget``), else the field's decoded /V, else ``"Off"``.
    """
    field_xref, members = _field_widgets(fitz_doc, page, widget)
    for _p, x in members:
        state = _name(fitz_doc, x, "AS")
        if state and state != "Off":
            return decode_pdf_name(state)
    value = _name(fitz_doc, field_xref, "V")
    return decode_pdf_name(value) if value else "Off"
