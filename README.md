# PDF Editor

Free, open-source (AGPL-3.0) PDF editor for Windows, bilingual French/English.

Goals: fill PDF forms, fill flat forms/scans with free text and ✓ ✗ ● stamps, sign with an
image of a handwritten signature. Milestone 1 is a fast, crisp PDF viewer with page rotation,
undo/redo and saving; Milestone 2 (current) adds filling of standard PDF forms (AcroForm).
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/M2_PLAN.md](docs/M2_PLAN.md).

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

## Filling forms (Milestone 2)

A PDF with fillable fields opens with the **Form Tool** active (Edit ▸ Form Tool, or the
toolbar); other PDFs open with the hand tool.

- **Text fields**: click to type. Enter (single line) or Ctrl+Enter (multi-line) validates,
  Escape cancels, clicking elsewhere validates.
- **Checkboxes and radio buttons**: click to toggle (or Space when focused with Tab). Their
  original look is kept.
- **Drop-down and list boxes**: click and choose a value (list boxes are single-select).
- **Tab / Shift+Tab** move to the next / previous field in reading order (top to bottom, left
  to right, page after page), whatever order the file lists its fields in.
- **View ▸ Highlight Form Fields** shows or hides the blue tint over fillable fields (the
  choice is remembered).
- **Edit ▸ Auto-shrink Overflowing Text** (on by default, remembered): a single-line value
  too long for its box is written with an automatic font size so it fits.
- Each field change is one Undo step; saving (Ctrl+S) appends the values to the file
  (incremental save), including encrypted PDFs, which keep their encryption.
- Read-only and hidden fields, push buttons and signature fields cannot be edited.

A banner above the pages explains forms that cannot be filled normally (close it with ×; it
comes back when the document is reopened):

- **Dynamic XFA** forms (Adobe LiveCycle, often "Please wait…" pages): cannot be filled here.
  Open the file in Adobe Acrobat Reader, print it to PDF (Microsoft Print to PDF), then fill
  the printed copy as a flat form. The Form Tool is disabled.
- **Static XFA** forms: filled as standard forms; after an edit, saving removes the XFA data
  so every viewer shows the values you entered (the banner then disappears).
- **Filling not permitted** by the document's security settings: the Form Tool is disabled.

Known limitations:

- On rotated pages the editor stays horizontal (the saved value follows the page rotation).
- Comb fields (one character per box) are filled as plain text, not split into boxes.
- List boxes are single-select.
- Field fonts are replaced by Helvetica in the appearance; characters outside the Windows
  Latin-1 (cp1252) set are stored but cannot be displayed or printed (a status-bar message
  warns about it). A drop-down shows its export value in the saved appearance.

### Manual form checklist (Milestone 2)

1. Open a LibreOffice/Word-made form: the Form Tool is active and fields are tinted blue.
2. Type accented text (é à ç œ €) in a text field, press Tab: the value shows, the next field
   (reading order, across pages) opens; the title shows `*`.
3. Toggle checkboxes and a radio group: the check mark looks like the original; Ctrl+Z undoes
   one change at a time.
4. A long value in a small single-line field shrinks to fit (turn Auto-shrink off to compare).
5. Save, reopen here and in Edge/Chrome/Adobe Reader: all values are shown.
6. A dynamic XFA form shows the amber banner mentioning Adobe Acrobat Reader and no Form Tool;
   an owner-locked form shows the permission banner.

## License

GNU Affero General Public License v3.0 only — see [LICENSE](LICENSE).
