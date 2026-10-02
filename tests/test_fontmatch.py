"""core/fontmatch.py (M7-T2): installed fonts and the font plan of an edit."""

from __future__ import annotations

import shutil
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_BOLD_PATH,
    CALIBRI_ITALIC_PATH,
    CALIBRI_PATH,
    CAMBRIA_TTC_PATH,
    FONT_DIR,
    PRINT_BASE_FONT,
    TIMES_PATH,
    font_xref,
    make_print_like_pdf,
    make_simple_font_pdf,
    make_text_edit_pdf,
    needs_text_fonts,
)

from pdfeditor.core import fontinfo, fontmatch
from pdfeditor.core.fontinfo import EmbeddedFont, FontKind
from pdfeditor.core.fontmatch import FontWarning, PlanKind, SystemFonts, match
from pdfeditor.core.fontread import read_embedded_font

COURIER_PATH = FONT_DIR / "cour.ttf"
CALIBRI_BOLD_ITALIC_PATH = FONT_DIR / "calibriz.ttf"
TIMES_BOLD_PATH = FONT_DIR / "timesbd.ttf"


@pytest.fixture
def clean_cache() -> Iterator[None]:
    fontmatch.clear_system_fonts_cache()
    yield
    fontmatch.clear_system_fonts_cache()


@pytest.fixture(scope="module")
def fonts() -> SystemFonts:
    """A small, fixed set of installed faces (tests do not depend on the machine's
    other fonts)."""
    paths = [
        CALIBRI_PATH,
        CALIBRI_BOLD_PATH,
        CALIBRI_ITALIC_PATH,
        CALIBRI_BOLD_ITALIC_PATH,
        ARIAL_PATH,
        FONT_DIR / "arialbd.ttf",
        TIMES_PATH,
        TIMES_BOLD_PATH,
        COURIER_PATH,
        FONT_DIR / "verdana.ttf",
        CAMBRIA_TTC_PATH,
    ]
    return SystemFonts.from_paths(p for p in paths if p.exists())


def _same(a: Path | None, b: Path) -> bool:
    return a is not None and a.name.lower() == b.name.lower()


def test_normalise_family() -> None:
    n = fontmatch.normalise_family
    assert n("Times New Roman") == n("TimesNewRomanPSMT") == n("TimesNewRomanPS")
    assert n("ArialMT") == n("Arial") == "arial"
    assert n("Segoe UI") == n("Segoe_UI") == "segoeui"


# -- installed fonts ---------------------------------------------------------------------
@needs_text_fonts
def test_find_by_family_and_style(fonts: SystemFonts) -> None:
    assert _same(fonts.find("Calibri").path, CALIBRI_PATH)
    assert _same(fonts.find("Calibri", bold=True).path, CALIBRI_BOLD_PATH)
    assert _same(fonts.find("Calibri", italic=True).path, CALIBRI_ITALIC_PATH)
    assert _same(fonts.find("calibri", bold=True, italic=True).path, CALIBRI_BOLD_ITALIC_PATH)
    assert _same(fonts.find("TimesNewRomanPSMT").path, TIMES_PATH)
    assert _same(fonts.find("ArialMT").path, ARIAL_PATH)
    assert fonts.find("Aptos Display Nonexistent") is None
    assert fonts.covers(fonts.find("Calibri"), "Zidane éàç €")
    assert not fonts.covers(fonts.find("Calibri"), "漢")


@pytest.mark.skipif(not CAMBRIA_TTC_PATH.exists(), reason="cambria.ttc missing")
def test_collection_faces_are_listed(fonts: SystemFonts) -> None:
    faces = fontmatch.read_faces(CAMBRIA_TTC_PATH)
    assert [f.family for f in faces[:2]] == ["Cambria", "Cambria Math"]
    assert [f.index for f in faces[:2]] == [0, 1]
    face = fonts.find("Cambria Math")
    assert face is not None and face.index == 1 and _same(face.path, CAMBRIA_TTC_PATH)
    assert fonts.find("Cambria").index == 0


def test_read_faces_skips_non_fonts(tmp_path: Path) -> None:
    junk = tmp_path / "junk.ttf"
    junk.write_bytes(b"not a font")
    (tmp_path / "readme.txt").write_text("x")
    assert fontmatch.read_faces(junk) == []
    assert fontmatch.read_faces(tmp_path / "readme.txt") == []
    assert fontmatch.read_faces(tmp_path / "missing.ttf") == []
    assert SystemFonts.from_paths([junk]).faces == ()


class _FakeKey:
    def __init__(self, values: list[tuple[str, str, int]]) -> None:
        self.values = values

    def __enter__(self) -> _FakeKey:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_winreg(hklm: list[tuple[str, str, int]], hkcu: list[tuple[str, str, int]] | None):
    module = types.ModuleType("winreg")
    module.HKEY_LOCAL_MACHINE = "HKLM"  # type: ignore[attr-defined]
    module.HKEY_CURRENT_USER = "HKCU"  # type: ignore[attr-defined]

    def open_key(hive: str, path: str) -> _FakeKey:
        assert path == fontmatch.FONTS_KEY
        values = hklm if hive == "HKLM" else hkcu
        if values is None:
            raise OSError("no key")
        return _FakeKey(values)

    def enum_value(key: _FakeKey, i: int) -> tuple[str, str, int]:
        if i >= len(key.values):
            raise OSError("no more")
        return key.values[i]

    module.OpenKey = open_key  # type: ignore[attr-defined]
    module.EnumValue = enum_value  # type: ignore[attr-defined]
    return module


def test_registry_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user_font = tmp_path / "user.ttf"
    fake = _fake_winreg(
        [("Calibri Bold (TrueType)", "calibrib.ttf", 1), ("Empty", "", 1)],
        [("My Font (TrueType)", str(user_font), 1)],
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(fontmatch, "fonts_dir", lambda: tmp_path / "Fonts")
    assert fontmatch.registry_font_files() == [tmp_path / "Fonts" / "calibrib.ttf", user_font]
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg([], None))
    assert fontmatch.registry_font_files() == []


@needs_text_fonts
def test_system_fonts_from_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clean_cache: None
) -> None:
    monkeypatch.setattr(
        fontmatch, "registry_font_files", lambda: [CALIBRI_PATH, tmp_path / "gone.ttf"]
    )
    fonts = fontmatch.system_fonts()
    assert [f.family for f in fonts.faces] == ["Calibri"]
    assert fontmatch.system_fonts() is fonts  # cached


@needs_text_fonts
def test_folder_scan_when_registry_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clean_cache: None
) -> None:
    shutil.copy(ARIAL_PATH, tmp_path / "arial.ttf")
    (tmp_path / "old.fon").write_bytes(b"MZ")
    monkeypatch.setattr(fontmatch, "registry_font_files", lambda: [])
    monkeypatch.setattr(fontmatch, "fonts_dir", lambda: tmp_path)
    fonts = fontmatch.system_fonts()
    assert [(f.family, f.path.name) for f in fonts.faces] == [("Arial", "arial.ttf")]


# -- plans --------------------------------------------------------------------------------
@needs_text_fonts
def test_reuse_embedded_font(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "t.pdf"))
    font = read_embedded_font(doc, *font_xref(doc[0], "Calibri"))
    plan = match(font, "courrier", fonts=fonts)
    assert plan.kind is PlanKind.REUSE and plan.warning is None
    assert plan.family == "Calibri" and plan.path is None
    system = fontinfo.cmap(CALIBRI_PATH.read_bytes())
    assert plan.codes == tuple(system[ord(c)] for c in "courrier")
    assert match(font, "Jean Dupont", fonts=fonts).kind is PlanKind.REUSE
    plan = match(font, "Zidane", fonts=fonts)
    assert plan.kind is PlanKind.SYSTEM and plan.warning is FontWarning.SUBSTITUTED
    assert plan.family == "Calibri" and _same(plan.path, CALIBRI_PATH) and plan.index == 0
    doc.close()


@needs_text_fonts
def test_type0_without_tounicode_borrows_installed_cmap(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "t.pdf"))
    xref, resource = font_xref(doc[0], "Calibri")
    doc.xref_set_key(xref, "ToUnicode", "null")
    font = read_embedded_font(doc, xref, resource)
    assert font.unicode_to_code == {}
    plan = match(font, "courrier", fonts=fonts)
    assert plan.kind is PlanKind.REUSE
    system = fontinfo.cmap(CALIBRI_PATH.read_bytes())
    assert plan.codes == tuple(system[ord(c)] for c in "courrier")
    assert match(font, "Zoé", fonts=fonts).kind is PlanKind.SYSTEM
    doc.close()


@needs_text_fonts
def test_simple_font_plans(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = pymupdf.open(make_simple_font_pdf(tmp_path / "s.pdf"))
    font = read_embedded_font(doc, *font_xref(doc[0], "Calibri"))
    plan = match(font, "Jean", fonts=fonts)
    assert plan.kind is PlanKind.REUSE and plan.codes == tuple(b"Jean")
    assert match(font, "Durand", fonts=fonts).kind is PlanKind.SYSTEM  # no "d"
    doc.close()


@needs_text_fonts
def test_print_like_font_by_name_table_then_metrics(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = pymupdf.open(make_print_like_pdf(tmp_path / "p.pdf"))
    font = read_embedded_font(doc, font_xref(doc[0], PRINT_BASE_FONT)[0])
    plan = match(font, "Zidane", fonts=fonts)
    assert plan.kind is PlanKind.SYSTEM and _same(plan.path, ARIAL_PATH)
    assert "installed" in plan.reason
    doc.close()
    doc = pymupdf.open(make_print_like_pdf(tmp_path / "p2.pdf", strip_name=True))
    font = read_embedded_font(doc, font_xref(doc[0], PRINT_BASE_FONT)[0])
    assert font.family == PRINT_BASE_FONT
    plan = match(font, "Zidane", fonts=fonts)
    assert plan.kind is PlanKind.SYSTEM and _same(plan.path, ARIAL_PATH)
    assert plan.family == "Arial" and "metric" in plan.reason
    doc.close()


@needs_text_fonts
def test_metric_match(tmp_path: Path, fonts: SystemFonts) -> None:
    candidates = [fonts.find(name) for name in ("Calibri", "Times New Roman", "Arial")]
    arial = fontinfo.cmap(ARIAL_PATH.read_bytes())
    adv = fontinfo.advance_widths(ARIAL_PATH.read_bytes())
    upem = fontinfo.units_per_em(ARIAL_PATH.read_bytes())
    widths = {ord(c): adv[arial[ord(c)]] * 1000 / upem for c in "Facturejanvi2024"}
    assert fontmatch.metric_match(widths, candidates, fonts).family == "Arial"
    calibri_like = {u: w * 0.9 for u, w in widths.items()}
    assert fontmatch.metric_match(calibri_like, candidates, fonts) is None
    few = dict(list(widths.items())[:3])
    assert fontmatch.metric_match(few, candidates, fonts) is None


@needs_text_fonts
@pytest.mark.parametrize(
    ("base_font", "expected"),
    [
        ("Calibri,Bold", CALIBRI_BOLD_PATH),
        ("Calibri,Italic", CALIBRI_ITALIC_PATH),
        ("Calibri-BoldItalic", CALIBRI_BOLD_ITALIC_PATH),
        ("ABCDEF+Calibri", CALIBRI_PATH),
        ("TimesNewRomanPS-BoldMT", TIMES_BOLD_PATH),
    ],
)
def test_unembedded_font_by_base_font_style(
    base_font: str, expected: Path, fonts: SystemFonts
) -> None:
    doc = pymupdf.open()
    xref = doc.get_new_xref()
    doc.update_object(
        xref,
        f"<< /Type /Font /Subtype /TrueType /BaseFont /{base_font} /FirstChar 32 "
        "/LastChar 32 /Widths [226] /Encoding /WinAnsiEncoding >>",
    )
    plan = match(read_embedded_font(doc, xref), "Zidane", fonts=fonts)
    assert plan.kind is PlanKind.SYSTEM and plan.warning is FontWarning.SUBSTITUTED
    assert _same(plan.path, expected)
    doc.close()


@needs_text_fonts
def test_descriptor_style_bits(fonts: SystemFonts) -> None:
    doc = pymupdf.open()
    fd = doc.get_new_xref()
    doc.update_object(fd, "<< /Type /FontDescriptor /Flags 96 /ItalicAngle -11 /FontWeight 700 >>")
    xref = doc.get_new_xref()
    doc.update_object(
        xref, f"<< /Type /Font /Subtype /TrueType /BaseFont /Calibri /FontDescriptor {fd} 0 R >>"
    )
    plan = match(read_embedded_font(doc, xref), "x", fonts=fonts)
    assert _same(plan.path, CALIBRI_BOLD_ITALIC_PATH)
    doc.close()


@needs_text_fonts
def test_unknown_family_falls_back_to_generic(fonts: SystemFonts) -> None:
    aptos = EmbeddedFont(xref=0, family="Aptos", base_font="ABCDEF+Aptos")
    plan = match(aptos, "Bonjour", fonts=fonts)
    assert plan.kind is PlanKind.GENERIC and plan.family == "Arial"
    assert plan.warning is FontWarning.SUBSTITUTED and _same(plan.path, ARIAL_PATH)
    serif = EmbeddedFont(xref=0, family="Garamondish", serif=True, bold=True)
    plan = match(serif, "Bonjour", fonts=fonts)
    assert plan.kind is PlanKind.GENERIC and _same(plan.path, TIMES_BOLD_PATH)
    if COURIER_PATH.exists():
        mono = EmbeddedFont(xref=0, family="Consolish", mono=True, serif=True)
        assert _same(match(mono, "x", fonts=fonts).path, COURIER_PATH)
    # nothing installed at all: a generic plan without a file
    plan = match(aptos, "Bonjour", fonts=SystemFonts([]))
    assert (plan.kind, plan.family, plan.path) == (PlanKind.GENERIC, "Arial", None)


@needs_text_fonts
def test_family_lacking_characters_falls_back(fonts: SystemFonts) -> None:
    font = EmbeddedFont(xref=0, family="Calibri")
    plan = match(font, "漢字", fonts=fonts)
    assert plan.kind is PlanKind.GENERIC


def _restricted_copy(tmp_path: Path) -> Path:
    """arial.ttf with fsType 2 and the family renamed "Qrial"."""
    data = bytearray(ARIAL_PATH.read_bytes())
    tbl = fontinfo.tables(bytes(data))
    os2 = tbl["OS/2"][0]
    data[os2 + 8 : os2 + 10] = (2).to_bytes(2, "big")
    base, length = tbl["name"]
    region = bytes(data[base : base + length])
    region = region.replace("Arial".encode("utf-16-be"), "Qrial".encode("utf-16-be"))
    region = region.replace(b"Arial", b"Qrial")
    data[base : base + length] = region
    path = tmp_path / "qrial.ttf"
    path.write_bytes(bytes(data))
    return path


@needs_text_fonts
def test_restricted_fonts_are_refused(tmp_path: Path) -> None:
    qrial = _restricted_copy(tmp_path)
    assert fontinfo.fs_type(qrial.read_bytes()) == 2
    fonts = SystemFonts.from_paths([qrial, ARIAL_PATH, TIMES_PATH])
    assert [f.restricted for f in fonts.family_faces("Qrial")] == [True]
    assert fonts.find("Qrial") is None
    plan = match(EmbeddedFont(xref=0, family="Qrial"), "Bonjour", fonts=fonts)
    assert plan.kind is PlanKind.GENERIC and _same(plan.path, ARIAL_PATH)
    # an embedded font with a restricted licence is not reused either
    embedded = EmbeddedFont(
        xref=1,
        kind=FontKind.TYPE0,
        family="Qrial",
        fs_type=2,
        num_glyphs=10,
        unicode_to_code={ord("a"): 4},
    )
    assert match(embedded, "a", fonts=fonts).kind is PlanKind.GENERIC
