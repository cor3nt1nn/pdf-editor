"""Update and compile translations.

    uv run python scripts/build_i18n.py            # lupdate + lrelease
    uv run python scripts/build_i18n.py --no-release

Runs ``pyside6-lupdate`` over every Python file in ``src/pdfeditor`` to refresh
``src/pdfeditor/i18n/pdfeditor_fr.ts`` (translate new entries with Qt Linguist:
``uv run pyside6-linguist src/pdfeditor/i18n/pdfeditor_fr.ts``), then
``pyside6-lrelease`` to produce ``pdfeditor_fr.qm``. Both files are committed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pdfeditor"
I18N = SRC / "i18n"
LANGS = ("fr",)


def tool(name: str) -> str:
    """Locate a PySide6 tool next to the running interpreter or on PATH."""
    scripts = Path(sys.executable).parent
    for candidate in (scripts / f"{name}.exe", scripts / name):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"{name} not found (run `uv sync --extra dev`)")
    return found


def sources() -> list[str]:
    return [str(p) for p in sorted(SRC.rglob("*.py"))]


def lupdate(ts: Path) -> None:
    cmd = [tool("pyside6-lupdate"), *sources(), "-no-obsolete", "-ts", str(ts)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def lrelease(ts: Path, qm: Path) -> None:
    cmd = [tool("pyside6-lrelease"), str(ts), "-qm", str(qm)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-release", action="store_true", help="only run lupdate")
    args = parser.parse_args(argv)
    for lang in LANGS:
        ts = I18N / f"pdfeditor_{lang}.ts"
        lupdate(ts)
        print(f"updated {ts.relative_to(ROOT)}")
        if not args.no_release:
            qm = ts.with_suffix(".qm")
            lrelease(ts, qm)
            print(f"compiled {qm.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
