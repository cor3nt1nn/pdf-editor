"""Smoke test of the frozen build (dist/PDFEditor/PDFEditor.exe).

Runs the exe's hidden ``--self-check DIR`` (headless: translators, icons, image formats,
open/fill/annotate/save/export of generated PDFs), then launches the GUI with
``--lang fr --quit-after 3000`` on one of the generated PDFs and checks its log. Settings
and the log go to a temporary directory (``PDFEDITOR_SETTINGS_DIR``,
``PDFEDITOR_LOG_DIR``), never to the user's real ones. Also reads the exe's version
resource. Needs a desktop session (the GUI launch uses the real Windows platform).

    uv run python scripts/smoke_frozen.py [path\\to\\PDFEditor.exe]

tests/test_frozen.py (``PDFEDITOR_FROZEN_EXE=... uv run pytest -m frozen``) uses these
helpers and adds strict pypdf checks of the written files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "dist" / "PDFEditor" / "PDFEditor.exe"
QUIT_AFTER_MS = 3000


def _env(work: Path, *, gui: bool) -> dict[str, str]:
    env = dict(os.environ)
    env["PDFEDITOR_SETTINGS_DIR"] = str(work / "settings")
    env["PDFEDITOR_LOG_DIR"] = str(work / "logs")
    if gui:
        # pytest's conftest sets the offscreen platform: the GUI launch must use qwindows.
        env.pop("QT_QPA_PLATFORM", None)
    return env


def run_self_check(exe: Path, out: Path, timeout: float = 180) -> tuple[int, dict[str, Any]]:
    """Run ``exe --self-check out``; returns (exit code, report.json content or {})."""
    proc = subprocess.run(
        [str(exe), "--self-check", str(out)],
        env=_env(out.parent, gui=False),
        timeout=timeout,
        check=False,
    )
    report = out / "report.json"
    data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    return proc.returncode, data


def run_gui(
    exe: Path, pdf: Path, work: Path, quit_after: int = QUIT_AFTER_MS, timeout: float = 90
) -> tuple[int, Path]:
    """Launch the GUI on ``pdf`` (French, quits after ``quit_after`` ms); returns
    (exit code, log file path)."""
    proc = subprocess.run(
        [str(exe), "--lang", "fr", "--quit-after", str(quit_after), str(pdf)],
        env=_env(work, gui=True),
        timeout=timeout,
        check=False,
    )
    return proc.returncode, work / "logs" / "pdfeditor.log"


def run_version(exe: Path, timeout: float = 60) -> int:
    """Exit code of ``exe --version`` (a windowed exe has no stdout: prints nothing)."""
    return subprocess.run([str(exe), "--version"], timeout=timeout, check=False).returncode


def problem_lines(log_text: str) -> list[str]:
    """Log lines at ERROR or CRITICAL level."""
    return [line for line in log_text.splitlines() if " ERROR " in line or " CRITICAL " in line]


def file_version(exe: Path) -> dict[str, str]:
    """The exe's version resource: FileVersion/ProductVersion strings and the fixed
    FileVersion ("a.b.c.d")."""
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version")
    size = version.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        raise OSError(f"no version resource in {exe}")
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(exe), 0, size, buffer):
        raise ctypes.WinError()
    result: dict[str, str] = {}
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    for key in ("FileVersion", "ProductVersion", "ProductName"):
        query = f"\\StringFileInfo\\040904B0\\{key}"
        if version.VerQueryValueW(buffer, query, ctypes.byref(pointer), ctypes.byref(length)):
            result[key] = ctypes.wstring_at(pointer.value, length.value).rstrip("\0")
    if version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        words = (wintypes.DWORD * 13).from_address(pointer.value)
        ms, ls = words[2], words[3]  # dwFileVersionMS, dwFileVersionLS
        result["fixed"] = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    return result


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    exe = Path(args[0]) if args else DEFAULT_EXE
    if not exe.is_file():
        print(f"not found: {exe} (build it with scripts\\build_exe.ps1)")
        return 2
    print(f"version resource: {file_version(exe)}")
    print(f"--version exit code: {run_version(exe)}")
    ok = True
    with tempfile.TemporaryDirectory(prefix="pdfeditor-smoke-") as tmp:
        work = Path(tmp)
        code, report = run_self_check(exe, work / "self-check")
        for check in report.get("checks", []):
            print(f"  {'ok  ' if check['ok'] else 'FAIL'} {check['name']}")
            if not check["ok"]:
                print(check.get("error", ""))
        print(f"self-check exit code {code}, ok={report.get('ok')}")
        ok &= code == 0 and bool(report.get("ok"))
        code, log_file = run_gui(exe, work / "self-check" / "annotations.pdf", work)
        text = log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
        problems = problem_lines(text)
        print(f"GUI exit code {code}, log {'found' if text else 'MISSING'}, {len(problems)} errors")
        for line in problems:
            print(f"  {line}")
        ok &= code == 0 and bool(text) and not problems
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
