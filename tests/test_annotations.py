"""M3-T1: FreeText annotation core (core/annotations.py, PdfDocument annots API)."""

from __future__ import annotations

import re
import uuid

import pymupdf
import pytest
from fixtures import (
    ANNOT_STAMP_NAME,
    ANNOT_STAMP_RECT,
    ANNOT_TEXT,
    ANNOT_TEXT_NAME,
    ANNOT_TEXT_RECT,
    CROP_BOX,
    FOREIGN_COLOR,
    FOREIGN_RECT,
    FOREIGN_SIZE,
    FOREIGN_TEXT,
    HIDDEN_ANNOT_NAME,
    HIGHLIGHT_RECT,
    ROTATED_ANNOT_NAME,
    ROTATED_ANNOT_RECT,
    ROTATED_ANNOT_TEXT,
)
from PySide6.QtCore import QPointF, QRectF

from pdfeditor.core import annotations
from pdfeditor.core.annotations import (
    LINE_HEIGHT_RATIO,
    STAMP_GLYPHS,
    TEXT_PAD,
    AnnotInfo,
    AnnotKind,
    AnnotSpec,
    parse_da,
    spec_from,
    stamp_rect,
    text_rect,
)
from pdfeditor.core.document import AnnotError, PdfDocument

BLUE = (0.0, 0.0, 1.0)
BLACK = (0.0, 0.0, 0.0)
RECT = QRectF(100, 100, 200, 30)


# -- helpers -------------------------------------------------------------------
def _blank_pdf(path, *, rotation: int = 0, cropbox: bool = False, pages: int = 1):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=612, height=792)
        if cropbox:
            page.set_cropbox(pymupdf.Rect(CROP_BOX))
        page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def blank(tmp_path):
    d = PdfDocument.open(_blank_pdf(tmp_path / "blank.pdf"))
    yield d
    d.close()


@pytest.fixture
def ann(annotated_pdf):
    d = PdfDocument.open(annotated_pdf)
    yield d
    d.close()


def _qrect(r: tuple[float, float, float, float]) -> QRectF:
    return QRectF(r[0], r[1], r[2] - r[0], r[3] - r[1])


def _text_spec(text: str = "Élève : é à ç €", rect: QRectF = RECT, **kw) -> AnnotSpec:
    args = {"page": 0, "kind": AnnotKind.TEXT, "font_size": 11.0, "color": BLUE, "rect": rect}
    args.update(kw)
    return AnnotSpec(text=text, **args)


def _stamp_spec(glyph: str, centre: QPointF, size: float = 12.0) -> AnnotSpec:
    rect, fs = stamp_rect(centre, size, glyph)
    return AnnotSpec(page=0, kind=AnnotKind.STAMP, text=glyph, font_size=fs, color=BLACK, rect=rect)


def _key(doc: PdfDocument, info: AnnotInfo, key: str) -> tuple[str, str]:
    with doc.lock:
        return doc.fitz.xref_get_key(info.xref, key)


def _ap_stream(doc: PdfDocument, info: AnnotInfo) -> bytes:
    with doc.lock:
        ap = doc.fitz.xref_get_key(info.xref, "AP/N")[1]
        return doc.fitz.xref_stream(int(ap.split()[0]))


def _ap_fonts(doc: PdfDocument, info: AnnotInfo) -> str:
    with doc.lock:
        ap = int(doc.fitz.xref_get_key(info.xref, "AP/N")[1].split()[0])
        fonts = doc.fitz.xref_get_key(ap, "Resources/Font")[1]
        out = [
            doc.fitz.xref_object(int(x), compressed=True) for x in re.findall(r"(\d+) 0 R", fonts)
        ]
    return fonts + " ".join(out)


_DARK = bytes(1 if v < 128 else 0 for v in range(256))


def _pixmap(doc: PdfDocument, page: int, scale: float, clip: QRectF | None = None):
    with doc.lock:
        return doc.fitz[page].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale),
            colorspace=pymupdf.csGRAY,
            alpha=False,
            annots=True,
            clip=pymupdf.Rect(clip.left(), clip.top(), clip.right(), clip.bottom())
            if clip is not None
            else None,
        )


def _dark_bbox(
    doc: PdfDocument, page: int, scale: float = 2.0, clip: QRectF | None = None
) -> QRectF | None:
    """Page-space bounding box of the dark pixels of a render (None if none)."""
    pix = _pixmap(doc, page, scale, clip)
    s, w, stride = pix.samples, pix.width, pix.stride
    x0 = y0 = 10**9
    x1 = y1 = -1
    for y in range(pix.height):
        row = s[y * stride : y * stride + w].translate(_DARK)
        first = row.find(1)
        if first < 0:
            continue
        x0, x1 = min(x0, first), max(x1, row.rfind(1))
        y0, y1 = min(y0, y), y
    if x1 < 0:
        return None
    ox, oy = (clip.left(), clip.top()) if clip is not None else (0.0, 0.0)
    return QRectF(ox + x0 / scale, oy + y0 / scale, (x1 + 1 - x0) / scale, (y1 + 1 - y0) / scale)


def _inside(inner: QRectF, outer: QRectF, tol: float) -> bool:
    return outer.adjusted(-tol, -tol, tol, tol).contains(inner)


def _rects_close(a: QRectF, b: QRectF, tol: float = 0.01) -> bool:
    return all(
        abs(x - y) <= tol
        for x, y in zip(
            (a.left(), a.top(), a.right(), a.bottom()),
            (b.left(), b.top(), b.right(), b.bottom()),
            strict=True,
        )
    )


def _changed_pages(doc: PdfDocument) -> list[int]:
    pages: list[int] = []
    doc.page_changed.connect(pages.append)
    return pages


# -- pure helpers ----------------------------------------------------------------
def test_parse_da() -> None:
    assert parse_da("0 0 1 rg /Helv 11 Tf") == ("Helv", 11.0, (0.0, 0.0, 1.0))
    assert parse_da("/ZaDb 10.8 Tf 0 g") == ("ZaDb", 10.8, (0.0, 0.0, 0.0))
    assert parse_da("0.5 g /Arial 12 Tf") == ("Arial", 12.0, (0.5, 0.5, 0.5))
    assert parse_da("1 0 0 0 k /Helv 9 Tf") == ("Helv", 9.0, (0.0, 1.0, 1.0))
    assert parse_da("") == ("", 0.0, (0.0, 0.0, 0.0))


def test_text_rect_and_stamp_rect_geometry() -> None:
    r = text_rect(100, 200, 180, 10)
    assert (r.left(), r.top(), r.width()) == (100, 192, 180)
    assert r.height() == pytest.approx(12 + TEXT_PAD)
    rect, fs = stamp_rect(QPointF(50, 60), 12, STAMP_GLYPHS["check"])
    assert fs == pytest.approx(10.8)
    assert (rect.width(), rect.height()) == (12, 12)
    assert rect.left() == pytest.approx(50 - 0.425 * 10.8)
    assert rect.top() == pytest.approx(60 - 0.45 * 10.8)


# -- creation --------------------------------------------------------------------
def test_create_text_annotation(blank: PdfDocument) -> None:
    pages = _changed_pages(blank)
    info = blank.add_annot(_text_spec())
    assert pages == [0]
    uuid.UUID(info.name)  # a uuid4 /NM
    assert _key(blank, info, "NM") == ("string", info.name)
    assert _key(blank, info, "CL")[0] == "null"
    assert _key(blank, info, "DA") == ("string", "0 0 1 rg /Helv 11 Tf")
    ap = _ap_stream(blank, info)
    assert b"\xe9" in ap and b"\x80" in ap  # é and € in WinAnsi
    listed = blank.annots(0)
    assert [a.name for a in listed] == [info.name]
    got = listed[0]
    assert got == info
    assert got.kind is AnnotKind.TEXT
    assert _rects_close(got.rect, RECT)
    assert got.text == "Élève : é à ç €"
    assert got.color == BLUE and got.font_size == 11
    assert got.rotate == 0 and got.editable
    assert blank.annot(0, info.name) == info
    assert blank.annot(0, "nope") is None


def test_create_with_given_name_and_fit_height(blank: PdfDocument) -> None:
    info = blank.add_annot(_text_spec("a\nb\nc", name="my-name"), fit_height=True)
    assert info.name == "my-name"
    assert info.rect.top() == pytest.approx(RECT.top())
    assert info.rect.width() == pytest.approx(RECT.width())
    assert info.rect.height() == pytest.approx(3 * LINE_HEIGHT_RATIO * 11 + TEXT_PAD)


def test_non_cp1252_text_renders_and_round_trips(blank: PdfDocument) -> None:
    text = "Chinese 中文 and ā ok"
    info = blank.add_annot(_text_spec(text))
    assert info.text == text
    assert blank.annots(0)[0].text == text
    assert _key(blank, info, "DA")[1].endswith("/Helv 11 Tf")
    bbox = _dark_bbox(blank, 0, 2.0)
    assert bbox is not None and _inside(bbox, RECT, 1.0)
    assert bbox.width() > 100


@pytest.mark.parametrize("glyph", list(STAMP_GLYPHS.values()))
def test_stamps_render_with_zapf_dingbats(blank: PdfDocument, glyph: str) -> None:
    info = blank.add_annot(_stamp_spec(glyph, QPointF(200, 200)))
    assert info.kind is AnnotKind.STAMP and info.text == glyph
    assert "/ZaDb" in _key(blank, info, "DA")[1]
    assert "ZapfDingbats" in _ap_fonts(blank, info)
    assert _dark_bbox(blank, 0, 4.0, QRectF(180, 180, 40, 40)) is not None


@pytest.mark.parametrize("glyph", list(STAMP_GLYPHS.values()))
def test_stamp_rect_centres_glyph(blank: PdfDocument, glyph: str) -> None:
    centre = QPointF(200.0, 300.0)
    blank.add_annot(_stamp_spec(glyph, centre, 12.0))
    bbox = _dark_bbox(blank, 0, 8.0, QRectF(185, 285, 30, 30))
    assert bbox is not None
    assert abs(bbox.center().x() - centre.x()) <= 0.5
    assert abs(bbox.center().y() - centre.y()) <= 0.5


@pytest.mark.parametrize("cropbox", [False, True])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_and_cropped_pages_render_upright_in_rect(
    tmp_path, rotation: int, cropbox: bool
) -> None:
    doc = PdfDocument.open(
        _blank_pdf(tmp_path / f"r{rotation}.pdf", rotation=rotation, cropbox=cropbox)
    )
    try:
        info = doc.add_annot(_text_spec("Texte horizontal", color=BLACK))
        assert info.rotate == rotation
        assert _rects_close(info.rect, RECT)
        bbox = _dark_bbox(doc, 0, 2.0)
        assert bbox is not None
        assert _inside(bbox, RECT, 1.0), (bbox, RECT)
        assert bbox.width() > bbox.height()
        # Resizing keeps it upright.
        moved = doc.update_annot(0, info.name, rect=QRectF(150, 200, 150, 40))
        assert moved.rotate == rotation and _rects_close(moved.rect, QRectF(150, 200, 150, 40))
        bbox = _dark_bbox(doc, 0, 2.0)
        assert bbox is not None and _inside(bbox, moved.rect, 1.0)
        assert bbox.width() > bbox.height()
    finally:
        doc.close()


# -- updates -----------------------------------------------------------------------
def test_update_text_refits_height_and_rewraps(blank: PdfDocument) -> None:
    info = blank.add_annot(_text_spec("x"))
    pitch = LINE_HEIGHT_RATIO * 11
    three = blank.update_annot(0, info.name, text="un\ndeux\ntrois", fit_height=True)
    assert three.text == "un\ndeux\ntrois"
    assert three.rect.height() == pytest.approx(3 * pitch + TEXT_PAD)
    assert three.rect.top() == pytest.approx(RECT.top())
    assert three.rect.width() == pytest.approx(RECT.width())
    blank_line = blank.update_annot(0, info.name, text="\nun\n\ndeux\n", fit_height=True)
    assert blank_line.rect.height() == pytest.approx(4 * pitch + TEXT_PAD)

    long = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor"
    wide = blank.update_annot(0, info.name, text=long, fit_height=True)
    lines = round((wide.rect.height() - TEXT_PAD) / pitch)
    assert lines >= 2
    assert wide.rect.height() == pytest.approx(lines * pitch + TEXT_PAD)
    narrow_rect = QRectF(wide.rect.left(), wide.rect.top(), 100, wide.rect.height())
    narrow = blank.update_annot(0, info.name, rect=narrow_rect, fit_height=True)
    narrow_lines = round((narrow.rect.height() - TEXT_PAD) / pitch)
    assert narrow_lines > lines
    assert narrow.rect.height() == pytest.approx(narrow_lines * pitch + TEXT_PAD)
    assert narrow.rect.width() == pytest.approx(100)
    bbox = _dark_bbox(blank, 0, 2.0)
    assert bbox is not None and _inside(bbox, narrow.rect, 1.0)
    # Without fit_height the rect is kept as given.
    plain = blank.update_annot(0, info.name, text="court")
    assert plain.rect.height() == pytest.approx(narrow.rect.height())


def test_fit_height_on_rotated_page_keeps_top(ann: PdfDocument) -> None:
    info = ann.annot(1, ROTATED_ANNOT_NAME)
    assert info is not None and info.rotate == 90
    new = ann.update_annot(1, info.name, text="a\nb\nc\nd", fit_height=True)
    assert new.rotate == 90
    assert new.rect.top() == pytest.approx(ROTATED_ANNOT_RECT[1])
    assert new.rect.left() == pytest.approx(ROTATED_ANNOT_RECT[0])
    assert new.rect.width() == pytest.approx(ROTATED_ANNOT_RECT[2] - ROTATED_ANNOT_RECT[0])
    assert new.rect.height() == pytest.approx(4 * LINE_HEIGHT_RATIO * 11 + TEXT_PAD)
    bbox = _dark_bbox(ann, 1, 2.0)
    assert bbox is not None and _inside(bbox, new.rect, 1.0)
    assert bbox.height() > 3 * LINE_HEIGHT_RATIO * 11  # four upright lines


def test_update_color_and_font_size_rewrite_da(blank: PdfDocument) -> None:
    info = blank.add_annot(_text_spec())
    red = blank.update_annot(0, info.name, color=(1, 0, 0))
    assert _key(blank, red, "DA") == ("string", "1 0 0 rg /Helv 11 Tf")
    assert red.color == (1.0, 0.0, 0.0)
    big = blank.update_annot(0, info.name, font_size=14)
    assert _key(blank, big, "DA") == ("string", "1 0 0 rg /Helv 14 Tf")
    assert big.font_size == 14 and big.text == info.text and big.name == info.name
    assert b"/Helv 14 Tf" in _ap_stream(blank, big)
    assert _key(blank, big, "CL")[0] == "null"


def test_resize_stamp_scales_glyph(blank: PdfDocument) -> None:
    info = blank.add_annot(_stamp_spec("4", QPointF(200, 200)))
    new = blank.update_annot(0, info.name, rect=QRectF(100, 100, 20, 24))
    assert new.kind is AnnotKind.STAMP
    assert new.font_size == pytest.approx(0.9 * 20)
    assert "/ZaDb 18 Tf" in _key(blank, new, "DA")[1]


# -- fixture 3: reading ----------------------------------------------------------
def test_annotated_fixture_listing(ann: PdfDocument) -> None:
    listed = ann.annots(0)
    # Our text, our stamp, the highlight (M6b), the foreign FreeText; not the hidden one
    # or the widget.
    assert len(listed) == 4
    text, stamp, highlight, foreign = listed
    assert highlight.kind is AnnotKind.HIGHLIGHT and not highlight.movable
    assert _rects_close(highlight.rect, _qrect(HIGHLIGHT_RECT))
    assert foreign.kind is AnnotKind.TEXT and annotations.is_synthetic(foreign.name)
    assert (text.name, text.kind, text.text) == (ANNOT_TEXT_NAME, AnnotKind.TEXT, ANNOT_TEXT)
    assert _rects_close(text.rect, _qrect(ANNOT_TEXT_RECT))
    assert (stamp.name, stamp.kind, stamp.text) == (ANNOT_STAMP_NAME, AnnotKind.STAMP, "4")
    assert _rects_close(stamp.rect, _qrect(ANNOT_STAMP_RECT))
    assert HIDDEN_ANNOT_NAME not in {a.name for a in listed}
    assert all(a.editable for a in listed)
    with ann.lock:
        hidden = annotations.read_annots(ann.fitz, 0, include_hidden=True)
    assert [a.name for a in hidden if a.hidden] == [HIDDEN_ANNOT_NAME]
    assert not next(a for a in hidden if a.hidden).editable

    rotated = ann.annots(1)
    assert [(a.name, a.text, a.rotate) for a in rotated] == [
        (ROTATED_ANNOT_NAME, ROTATED_ANNOT_TEXT, 90)
    ]
    assert _rects_close(rotated[0].rect, _qrect(ROTATED_ANNOT_RECT))


def test_foreign_freetext_is_named_and_editable(ann: PdfDocument, tmp_path) -> None:
    foreign = ann.annots(0)[3]
    assert annotations.is_synthetic(foreign.name)  # nothing written when read
    assert _key(ann, foreign, "NM")[0] == "null"
    real = ann.claim_annot_name(0, foreign.name)
    uuid.UUID(real)
    assert _key(ann, foreign, "NM") == ("string", real)
    assert ann.annot(0, foreign.name).name == real  # the synthetic name still finds it
    foreign = ann.annot(0, real)
    assert foreign.kind is AnnotKind.TEXT
    assert foreign.text == FOREIGN_TEXT
    assert foreign.color == FOREIGN_COLOR and foreign.font_size == FOREIGN_SIZE
    assert _rects_close(foreign.rect, _qrect(FOREIGN_RECT))
    assert foreign.editable
    assert ann.annots(0)[3].name == foreign.name  # stable (cached)
    assert _key(ann, foreign, "RC")[0] == "string"

    edited = ann.update_annot(0, foreign.name, text="Edited é")
    assert edited.text == "Edited é" and edited.name == foreign.name
    assert _key(ann, edited, "RC")[0] == "null"
    assert _key(ann, edited, "DS")[0] == "null"
    assert _key(ann, edited, "DA") == ("string", "0 0 1 rg /Helv 12 Tf")
    assert b"Edited \xe9" in _ap_stream(ann, edited)

    # The assigned name is saved with the document.
    ann.save_as(tmp_path / "copy.pdf")
    again = PdfDocument.open(tmp_path / "copy.pdf")
    try:
        assert ann.annot(0, foreign.name) is not None
        assert again.annot(0, foreign.name) is not None
    finally:
        again.close()


def test_duplicate_names_are_made_unique(blank: PdfDocument) -> None:
    a = blank.add_annot(_text_spec("one", name="dup"))
    b = blank.add_annot(_text_spec("two", rect=QRectF(100, 200, 200, 30), name="dup"))
    assert a.name == b.name == "dup"
    blank.page_changed.emit(0)
    names = [x.name for x in blank.annots(0)]
    assert names[0] == "dup" and annotations.is_synthetic(names[1])
    assert blank.annot(0, names[1]).text == "two"
    assert blank.annot(0, "dup").text == "one"
    # A synthetic name is only valid for the current load (xrefs may change on save).
    blank.reloaded.emit()
    assert blank.annot(0, names[1]) is None
    with pytest.raises(AnnotError):
        blank.update_annot(0, names[1], text="stale")


def test_annots_cached_until_page_changed(ann: PdfDocument, monkeypatch) -> None:
    ann.annots(0)
    calls: list[int] = []
    real = annotations.read_annots

    def spy(doc, i, **kw):
        calls.append(i)
        return real(doc, i, **kw)

    monkeypatch.setattr(annotations, "read_annots", spy)
    ann.annots(0)
    assert calls == []
    ann.page_changed.emit(0)
    ann.annots(0)
    ann.annots(0)
    assert calls == [0]


# -- delete / re-create ------------------------------------------------------------
def test_delete_and_recreate_from_spec_is_identical(ann: PdfDocument) -> None:
    """Our own text and stamp come back pixel-identical (a foreign annotation is
    re-created normalised to Helvetica: see test_foreign_freetext_recreated)."""
    clip = QRectF(80, 80, 260, 100)
    for info in ann.annots(0)[:2]:
        before = _pixmap(ann, 0, 3.0, clip).samples
        ann.delete_annot(0, info.name)
        assert ann.annot(0, info.name) is None
        assert _pixmap(ann, 0, 3.0, clip).samples != before
        again = ann.add_annot(spec_from(info))
        assert again.name == info.name
        assert (again.kind, again.text, again.font_size, again.color, again.rotate) == (
            info.kind,
            info.text,
            info.font_size,
            info.color,
            info.rotate,
        )
        assert _rects_close(again.rect, info.rect, 1e-6)
        assert ann.annot(0, info.name) == again
        assert _pixmap(ann, 0, 3.0, clip).samples == before, info.text


def test_foreign_freetext_recreated(ann: PdfDocument) -> None:
    foreign = ann.annots(0)[3]
    ann.delete_annot(0, foreign.name)
    again = ann.add_annot(spec_from(foreign))
    assert (again.name, again.text, again.color, again.font_size) == (
        foreign.name,
        FOREIGN_TEXT,
        FOREIGN_COLOR,
        FOREIGN_SIZE,
    )
    assert _rects_close(again.rect, foreign.rect)
    bbox = _dark_bbox(ann, 0, 2.0, again.rect.adjusted(-10, -10, 10, 10))
    assert bbox is not None and _inside(bbox, again.rect, 1.0)


def test_delete_and_recreate_rotated(ann: PdfDocument) -> None:
    info = ann.annot(1, ROTATED_ANNOT_NAME)
    before = _pixmap(ann, 1, 2.0).samples
    ann.delete_annot(1, info.name)
    again = ann.add_annot(spec_from(info))
    assert again.rotate == 90 and again.name == info.name
    assert _pixmap(ann, 1, 2.0).samples == before


def test_removed_annotation_raises(blank: PdfDocument) -> None:
    info = blank.add_annot(_text_spec())
    blank.delete_annot(0, info.name)
    with pytest.raises(AnnotError):
        blank.update_annot(0, info.name, text="x")
    with pytest.raises(AnnotError):
        blank.delete_annot(0, info.name)
    with pytest.raises(IndexError):
        blank.annots(5)


def test_not_permitted_raises(owner_locked_pdf) -> None:
    doc = PdfDocument.open(owner_locked_pdf)
    try:
        assert not doc.can_annotate
        with pytest.raises(AnnotError):
            doc.add_annot(_text_spec())
    finally:
        doc.close()


def test_can_annotate(blank: PdfDocument) -> None:
    assert blank.can_annotate


# -- persistence -------------------------------------------------------------------
def test_resolve_by_name_after_incremental_and_full_save(ann: PdfDocument) -> None:
    ann.update_annot(0, ANNOT_TEXT_NAME, text="après incrémental")
    assert ann.can_save_incrementally()
    ann.save()
    assert ann.annot(0, ANNOT_TEXT_NAME).text == "après incrémental"
    old_xref = ann.annot(0, ANNOT_TEXT_NAME).xref
    ann.save(force_full=True)
    info = ann.annot(0, ANNOT_TEXT_NAME)
    assert info is not None and info.text == "après incrémental"
    assert ann.annot(0, ANNOT_STAMP_NAME) is not None
    assert ann.annot(1, ROTATED_ANNOT_NAME) is not None
    assert old_xref > 0  # (full save may renumber; identity is the name)
    edited = ann.update_annot(0, ANNOT_TEXT_NAME, text="après complet")
    assert edited.text == "après complet"
    ann.delete_annot(0, ANNOT_STAMP_NAME)
    ann.save()
    reopened = PdfDocument.open(ann.path)
    try:
        assert reopened.annot(0, ANNOT_TEXT_NAME).text == "après complet"
        assert reopened.annot(0, ANNOT_STAMP_NAME) is None
    finally:
        reopened.close()


def test_bake_flattens_annotations(ann: PdfDocument) -> None:
    with ann.lock:
        ann.fitz.bake(annots=True, widgets=True)
        text = ann.fitz[0].get_text()
        kinds = [a.type[0] for a in ann.fitz[0].annots()]
    assert pymupdf.PDF_ANNOT_FREE_TEXT not in kinds
    assert "Élève" in text
    ann.page_changed.emit(0)
    assert ann.annots(0) == []


def test_no_mupdf_warnings(ann: PdfDocument) -> None:
    pymupdf.TOOLS.mupdf_warnings()  # reset
    for i in range(2):
        ann.annots(i)
    foreign = ann.annots(0)[3]
    ann.update_annot(0, foreign.name, text="Edited", fit_height=True)
    ann.update_annot(1, ROTATED_ANNOT_NAME, font_size=14, color=(1, 0, 0), fit_height=True)
    info = ann.add_annot(_text_spec("new ā 中", rect=QRectF(100, 400, 200, 20)), fit_height=True)
    ann.add_annot(_stamp_spec("8", QPointF(300, 450)))
    ann.render(0, 1.0)
    ann.render(1, 1.0)
    ann.save()
    ann.delete_annot(0, info.name)
    ann.save(force_full=True)
    ann.render(0, 1.0)
    assert pymupdf.TOOLS.mupdf_warnings() == ""
