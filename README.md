# PDF Editor

Free, open-source (AGPL-3.0) PDF editor for Windows, bilingual French/English.

Goals: fill PDF forms, fill flat forms/scans with free text and ✓ ✗ ● stamps, sign with an
image of a handwritten signature. Milestone 1 (current) is a fast, crisp PDF viewer with
page rotation, undo/redo and saving. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- Windows 10/11
- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```powershell
uv venv --python 3.12
uv sync --extra dev
```

## Run

```powershell
uv run pdfeditor                 # empty window
uv run pdfeditor path\to\file.pdf
uv run pdfeditor --lang fr file.pdf
uv run python -m pdfeditor file.pdf   # same thing
```

Settings are stored in `%APPDATA%\PDFEditor\PDFEditor.ini`.

## Develop

```powershell
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Translations

User-visible strings use `self.tr()` / `QCoreApplication.translate()`. After changing them:

```powershell
uv run python scripts/build_i18n.py          # lupdate + lrelease
uv run pyside6-linguist src/pdfeditor/i18n/pdfeditor_fr.ts   # translate new entries
uv run python scripts/build_i18n.py          # recompile the .qm
uv run python scripts/check_i18n.py --fresh  # must report "translations complete"
```

## Manual smoke checklist (Milestone 1)

Run `uv run pdfeditor` and check:

1. Empty window titled "PDF Editor"; File ▸ Open (Ctrl+O) opens a multi-page PDF.
2. Pages are crisp at 25 %, 100 %, 400 % (Ctrl+wheel zooms under the cursor; Ctrl++ / Ctrl+- /
   Ctrl+0 / Ctrl+1 Fit Width / Ctrl+2 Fit Page; the zoom box accepts typed values).
3. Scrolling is continuous; status bar shows "Page x / N" and the zoom; the page spin box,
   Previous/Next, PageUp/PageDown/Home/End navigate; pages render progressively.
4. Thumbnails (F4) follow the current page; clicking one jumps to it; F4 state survives a restart.
5. Ctrl+R / Ctrl+Shift+R rotate the current page; the title shows `*`; Undo/Redo (Ctrl+Z / Ctrl+Y)
   work and consecutive rotations are one undo step.
6. Ctrl+S saves (reopen the file: rotation kept); Save As (Ctrl+Shift+S) switches to the new file.
7. Closing with unsaved changes asks Save / Discard / Cancel.
8. Password-protected PDF: wrong password re-prompts; Cancel leaves the app usable.
9. Missing, empty (0 byte), corrupt and non-PDF files show a clear error message.
10. Drag & drop a `.pdf` (any case) onto the window opens it; other files are ignored.
11. View ▸ Language ▸ Français asks to restart; after restart all menus and standard dialogs
    are in French (`uv run pdfeditor --lang fr` forces it for one run).
12. Help ▸ About shows the version, the AGPL notice and library versions.
13. Window size/position and zoom mode are restored on the next start.

## License

GNU Affero General Public License v3.0 only — see [LICENSE](LICENSE).
