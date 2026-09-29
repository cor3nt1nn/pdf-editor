"""Fail if a translation is unfinished.

    uv run python scripts/check_i18n.py           # check the committed .ts files
    uv run python scripts/check_i18n.py --fresh   # also re-extract strings from the sources

``--fresh`` runs lupdate on a temporary copy of each .ts file, so a new ``tr()`` string
that was never added to the .ts file is reported too.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_i18n  # noqa: E402


def unfinished(ts: Path) -> list[tuple[str, str]]:
    """(context, source) of every message whose translation is unfinished or empty."""
    result = []
    root = ET.parse(ts).getroot()
    for context in root.iter("context"):
        name = context.findtext("name") or ""
        for message in context.iter("message"):
            translation = message.find("translation")
            kind = translation.get("type") if translation is not None else "unfinished"
            if kind in ("vanished", "obsolete"):
                continue
            text = (translation.text or "") if translation is not None else ""
            if kind == "unfinished" or not text.strip():
                result.append((name, message.findtext("source") or ""))
    return result


def check(fresh: bool = False) -> list[str]:
    problems = []
    for lang in build_i18n.LANGS:
        ts = build_i18n.I18N / f"pdfeditor_{lang}.ts"
        if not ts.is_file():
            problems.append(f"{ts.name}: missing")
            continue
        target = ts
        tmpdir = None
        if fresh:
            tmpdir = tempfile.mkdtemp()
            target = Path(tmpdir) / ts.name
            shutil.copy(ts, target)
            build_i18n.lupdate(target)
        try:
            problems += [f"{ts.name}: [{c}] {s!r}" for c, s in unfinished(target)]
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)
        qm = ts.with_suffix(".qm")
        if not qm.is_file():
            problems.append(f"{qm.name}: missing (run build_i18n.py)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh", action="store_true", help="re-extract strings first")
    args = parser.parse_args(argv)
    problems = check(args.fresh)
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} translation problem(s)")
        return 1
    print("translations complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
