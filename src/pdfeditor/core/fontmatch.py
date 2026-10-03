"""Choose the font that writes edited page text (M7, docs/M7_PLAN.md §1.2 step 4).

:func:`match` returns a :class:`FontPlan`:

* ``REUSE``: the span's embedded font shows every character of the new text (an existing
  glyph and a code for each one) → write with it, no new font object; ``codes`` are the
  codes to show.
* ``SYSTEM``: an installed face of the same family and style (family from the program's
  name table, else ``/BaseFont`` such as "Calibri,Bold"; generic names such as
  "CIDFont+F1" are identified by comparing the font's widths with common families,
  :func:`metric_match`).
* ``GENERIC``: Arial, Times New Roman or Courier New in the closest style (serif / mono
  / bold / italic of the document's font).

Every plan but ``REUSE`` carries ``warning = FontWarning.SUBSTITUTED`` (the UI says
"Replaced with {family}…"). Installed faces whose OS/2 ``fsType`` is "restricted" are
never chosen, nor is a restricted embedded font reused; neither are installed faces
without TrueType outlines (CFF ``.otf``: :mod:`fontembed` cannot subset them), so the
next candidate (or the generic font) is used instead.

Installed fonts (:func:`system_fonts`, cached for the process): the Windows registry
(``HKLM`` and ``HKCU`` ``…\\CurrentVersion\\Fonts``; values are bare file names in
``%WINDIR%\\Fonts`` or full paths), else a scan of the fonts folder. Each file is read
once through ``mmap`` (names, style, fsType, glyph count). Read-only: the registry is
never written.
"""

from __future__ import annotations

import enum
import functools
import logging
import mmap
import os
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pdfeditor.core import fontinfo
from pdfeditor.core.fontinfo import EmbeddedFont, FontKind

log = logging.getLogger(__name__)

FONTS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")
#: Generic fallbacks (family, file of the regular face).
GENERIC_SANS = "Arial"
GENERIC_SERIF = "Times New Roman"
GENERIC_MONO = "Courier New"
#: Families compared by :func:`metric_match` when a font's name says nothing.
METRIC_FAMILIES = (
    "Arial",
    "Calibri",
    "Times New Roman",
    "Cambria",
    "Verdana",
    "Tahoma",
    "Segoe UI",
    "Georgia",
    "Courier New",
    "Arial Narrow",
    "Trebuchet MS",
    "Garamond",
    "Century Gothic",
    "Aptos",
)
#: Largest mean width difference (em) for a metric match (Arial vs Arial: 0.0004 em;
#: the next family ≥ 0.049, plan F9).
METRIC_TOLERANCE = 0.01
#: Fewest compared characters for a metric match.
METRIC_MIN_SAMPLES = 5

_NOISE_RE = re.compile(r"[\s,\-_]+")
_SUFFIX_RE = re.compile(r"(psmt|mt|ps)$")


class PlanKind(enum.StrEnum):
    REUSE = "reuse"
    SYSTEM = "system"
    GENERIC = "generic"


class FontWarning(enum.StrEnum):
    """Why the UI should tell the user about the font (translated there)."""

    #: Another font replaces the document's one ("Replaced with {family}: …").
    SUBSTITUTED = "substituted"


@dataclass(frozen=True)
class FontPlan:
    """The font to write with. ``path``/``index`` locate the installed face (``None``
    for ``REUSE``, or when not even a generic font is installed); ``codes`` are the codes
    of the text for ``REUSE``; ``reason`` is a log-only explanation."""

    kind: PlanKind
    family: str
    path: Path | None = None
    index: int = 0
    warning: FontWarning | None = None
    codes: tuple[int, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class SystemFace:
    """One installed face (``index`` inside a collection)."""

    path: Path
    index: int
    family: str
    style: str
    legacy_family: str
    full_name: str
    postscript_name: str
    weight: int
    bold: bool
    italic: bool
    fs_type: int | None
    num_glyphs: int
    #: TrueType outlines (``glyf``/``loca``); CFF faces cannot be embedded.
    truetype: bool = True

    @property
    def restricted(self) -> bool:
        return fontinfo.is_restricted(self.fs_type)

    @property
    def embeddable(self) -> bool:
        """Not restricted and with TrueType outlines (what :mod:`fontembed` accepts)."""
        return self.truetype and not self.restricted


def normalise_family(name: str) -> str:
    """Comparison key of a family name: lower case, no spaces/``-``/``,``/``_``, "MT"/"PS"
    suffixes dropped ("TimesNewRomanPSMT" = "Times New Roman")."""
    key = _NOISE_RE.sub("", name.lower())
    return _SUFFIX_RE.sub("", key) or key


class SystemFonts:
    """Installed faces, looked up by family and style."""

    def __init__(self, faces: Iterable[SystemFace]) -> None:
        self.faces: tuple[SystemFace, ...] = tuple(faces)
        self._by_family: dict[str, list[SystemFace]] = {}
        for face in self.faces:
            keys = {
                normalise_family(face.family),
                normalise_family(face.legacy_family),
                normalise_family(face.full_name),
                normalise_family(face.postscript_name),
            }
            for key in keys - {""}:
                self._by_family.setdefault(key, []).append(face)
        self._cmaps: dict[tuple[Path, int], dict[int, int]] = {}
        self._widths: dict[tuple[Path, int], tuple[tuple[int, ...], int]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_paths(cls, paths: Iterable[Path | str]) -> SystemFonts:
        """Read the faces of ``paths`` (unreadable files are skipped)."""
        faces: list[SystemFace] = []
        seen: set[Path] = set()
        for p in paths:
            path = Path(p)
            key = Path(os.path.normcase(str(path)))
            if key in seen:
                continue
            seen.add(key)
            faces.extend(read_faces(path))
        return cls(faces)

    def family_faces(self, family: str) -> list[SystemFace]:
        """Faces whose family (or full/PostScript name) is ``family``."""
        return list(self._by_family.get(normalise_family(family), ()))

    def find(self, family: str, *, bold: bool = False, italic: bool = False) -> SystemFace | None:
        """The closest face of ``family`` (italic matters most, then weight), skipping
        faces that cannot be embedded (restricted, CFF outlines); ``None`` when the family
        has no such face."""
        faces = [f for f in self.family_faces(family) if f.embeddable]
        if not faces:
            return None
        target = 700 if bold else 400

        def score(face: SystemFace) -> tuple[int, int, int, str]:
            return (
                int(face.italic != italic),
                abs(face.weight - target),
                int(face.bold != bold),
                str(face.path).lower(),
            )

        return min(faces, key=score)

    def cmap(self, face: SystemFace) -> dict[int, int]:
        """``unicode -> glyph id`` of ``face`` (cached; {} when unreadable)."""
        key = (face.path, face.index)
        with self._lock:
            if key not in self._cmaps:
                self._cmaps[key] = _read(face.path, lambda d: fontinfo.cmap(d, face.index)) or {}
            return self._cmaps[key]

    def widths(self, face: SystemFace) -> tuple[tuple[int, ...], int]:
        """``(advance widths by glyph, unitsPerEm)`` of ``face`` (cached)."""
        key = (face.path, face.index)
        with self._lock:
            if key not in self._widths:
                got = _read(
                    face.path,
                    lambda d: (
                        fontinfo.advance_widths(d, face.index),
                        fontinfo.units_per_em(d, face.index),
                    ),
                )
                self._widths[key] = got or ((), 1000)
            return self._widths[key]

    def covers(self, face: SystemFace, text: str) -> bool:
        """``face`` has a glyph for every non-space character of ``text``."""
        table = self.cmap(face)
        return all(ord(ch) in table for ch in text if not ch.isspace())


def _read(path: Path, fn):  # type: ignore[no-untyped-def]
    try:
        with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return fn(data)
    except (OSError, ValueError, fontinfo.FontFileError) as exc:
        log.debug("font %s unreadable: %s", path, exc)
        return None


def read_faces(path: Path) -> list[SystemFace]:
    """Every face of the font file ``path`` ([] when it is not a readable font)."""

    def faces(data: bytes) -> list[SystemFace]:
        out: list[SystemFace] = []
        for index in range(min(fontinfo.face_count(data), 64)):
            try:
                n = fontinfo.names(data, index)
                style = fontinfo.face_style(data, index)
                out.append(
                    SystemFace(
                        path=path,
                        index=index,
                        family=n.family,
                        style=n.style,
                        legacy_family=n.legacy_family,
                        full_name=n.full_name,
                        postscript_name=n.postscript_name,
                        weight=style.weight,
                        bold=style.bold,
                        italic=style.italic,
                        fs_type=fontinfo.fs_type(data, index),
                        num_glyphs=fontinfo.glyph_count(data, index),
                        truetype={"glyf", "loca"} <= fontinfo.tables(data, index).keys(),
                    )
                )
            except (fontinfo.FontFileError, ValueError) as exc:
                log.debug("font %s face %d unreadable: %s", path, index, exc)
        return out

    if path.suffix.lower() not in FONT_SUFFIXES:
        return []
    return _read(path, faces) or []


# -- installed fonts ---------------------------------------------------------------------
def fonts_dir() -> Path:
    """``%WINDIR%\\Fonts``."""
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def registry_font_files() -> list[Path]:
    """Font files listed in the registry (HKLM then HKCU); [] when it cannot be read.
    Tests patch this function."""
    try:
        import winreg
    except ImportError:  # not Windows
        return []
    out: list[Path] = []
    base = fonts_dir()
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, FONTS_KEY)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    _name, value, _type = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if isinstance(value, str) and value.strip():
                    p = Path(os.path.expandvars(value.strip()))
                    out.append(p if p.is_absolute() else base / p)
    return out


def scan_font_files(folder: Path | None = None) -> list[Path]:
    """Font files of ``folder`` (default :func:`fonts_dir`), sorted."""
    folder = folder or fonts_dir()
    try:
        return sorted(p for p in folder.iterdir() if p.suffix.lower() in FONT_SUFFIXES)
    except OSError:
        return []


@functools.cache
def system_fonts() -> SystemFonts:
    """The installed fonts (registry, else folder scan), read once per process."""
    files = [p for p in registry_font_files() if p.suffix.lower() in FONT_SUFFIXES]
    files = [p for p in files if p.is_file()]
    if not files:
        log.info("no fonts in the registry; scanning %s", fonts_dir())
        files = scan_font_files()
    fonts = SystemFonts.from_paths(files)
    log.info("%d installed font faces", len(fonts.faces))
    return fonts


def clear_system_fonts_cache() -> None:
    """Forget :func:`system_fonts` (tests)."""
    system_fonts.cache_clear()


# -- matching -----------------------------------------------------------------------------
def metric_match(
    widths: Mapping[int, float],
    candidates: Sequence[SystemFace],
    fonts: SystemFonts,
    *,
    tolerance: float = METRIC_TOLERANCE,
) -> SystemFace | None:
    """The candidate whose advance widths are closest to ``widths`` (``unicode ->`` 1/1000
    em, letters and digits compared), when the mean difference is within ``tolerance``
    em over at least :data:`METRIC_MIN_SAMPLES` characters; else ``None``."""
    sample = {u: w / 1000.0 for u, w in widths.items() if chr(u).isalnum() and w > 0}
    best: tuple[float, SystemFace] | None = None
    for face in candidates:
        table = fonts.cmap(face)
        advances, upem = fonts.widths(face)
        diffs = [
            abs(advances[table[u]] / upem - w)
            for u, w in sample.items()
            if u in table and table[u] < len(advances)
        ]
        if len(diffs) < METRIC_MIN_SAMPLES:
            continue
        err = sum(diffs) / len(diffs)
        if best is None or err < best[0]:
            best = (err, face)
    if best is None or best[0] > tolerance:
        return None
    log.debug("metric match %s (%.4f em)", best[1].family, best[0])
    return best[1]


def _unicode_widths(font: EmbeddedFont) -> dict[int, float]:
    return {
        u: font.widths[code]
        for u, code in font.unicode_to_code.items()
        if code in font.widths and font.code_for(chr(u)) is not None
    }


def _reuse_codes(
    font: EmbeddedFont, text: str, fonts: SystemFonts | None, *, borrow: bool = True
) -> tuple[int, ...]:
    """Codes of ``text`` in ``font``; a Type0 subset lacking an encoding for some
    characters borrows the cmap of the installed same font (same glyph count = same
    glyph ids, plan F2; ``fonts`` defaults to :func:`system_fonts`, read only then).
    Raises ``KeyError`` when a character cannot be shown."""
    try:
        return font.codes(text)
    except KeyError:
        if not borrow or font.kind is not FontKind.TYPE0 or font.code_to_gid:
            raise
    if fonts is None:
        fonts = system_fonts()
    for face in fonts.family_faces(font.family):
        if face.num_glyphs != font.num_glyphs or face.italic != font.italic:
            continue
        table = fonts.cmap(face)
        codes: list[int] = []
        for ch in text:
            code = font.code_for(ch)
            if code is None:
                gid = table.get(ord(ch), 0)
                present = font.glyphs_present
                ok = gid and (present is None or gid in present or ch.isspace())
                if not ok or (font.widths and gid not in font.widths):
                    break
                code = gid
            codes.append(code)
        else:
            return tuple(codes)
    raise KeyError(text)


def match(
    embedded: EmbeddedFont,
    needed_text: str,
    *,
    fonts: SystemFonts | None = None,
    borrow: bool = True,
) -> FontPlan:
    """The font to write ``needed_text`` that was shown with ``embedded``.

    ``fonts`` defaults to :func:`system_fonts` (it is read only when the embedded font
    cannot be reused). ``borrow=False`` forbids codes borrowed from the installed font's
    cmap (used when they could not be added to the font's ToUnicode).
    """
    if embedded.editable:
        try:
            codes = _reuse_codes(embedded, needed_text, fonts, borrow=borrow)
        except KeyError:
            pass
        else:
            return FontPlan(PlanKind.REUSE, embedded.family, codes=codes, reason="embedded")
    if fonts is None:
        fonts = system_fonts()
    bold, italic = embedded.bold, embedded.italic
    family = embedded.family
    face = fonts.find(family, bold=bold, italic=italic) if family else None
    reason = f"installed {family!r}"
    if face is None:
        widths = _unicode_widths(embedded)
        candidates = [
            f
            for name in METRIC_FAMILIES
            if (f := fonts.find(name, bold=bold, italic=italic)) is not None
        ]
        face = metric_match(widths, candidates, fonts) if widths else None
        reason = f"metric match for {family!r}"
    if face is not None and not fonts.covers(face, needed_text):
        log.info("%s lacks characters of the new text", face.family)
        face = None
    if face is not None:
        return FontPlan(
            PlanKind.SYSTEM,
            face.family,
            face.path,
            face.index,
            FontWarning.SUBSTITUTED,
            reason=reason,
        )
    if embedded.mono:
        generic = GENERIC_MONO
    elif embedded.serif:
        generic = GENERIC_SERIF
    else:
        generic = GENERIC_SANS
    face = fonts.find(generic, bold=bold, italic=italic)
    if face is None:
        log.warning("generic font %s is not installed", generic)
        return FontPlan(PlanKind.GENERIC, generic, warning=FontWarning.SUBSTITUTED)
    return FontPlan(
        PlanKind.GENERIC,
        face.family,
        face.path,
        face.index,
        FontWarning.SUBSTITUTED,
        reason=f"no installed {family!r}",
    )
