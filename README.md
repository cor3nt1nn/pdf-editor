# PDF Editor

Free, open-source (AGPL-3.0) PDF editor for Windows, bilingual French/English.

Goals: fill PDF forms, fill flat forms/scans with free text and ✓ ✗ ● stamps, sign with an
image of a handwritten signature. Milestone 1 is a fast, crisp PDF viewer with page rotation,
undo/redo and saving; Milestone 2 adds filling of standard PDF forms (AcroForm); Milestone 3
adds free text and ✓ ✗ ● stamps on flat forms (Word exports, printed PDFs); Milestone 4
adds signing with an image of a handwritten signature; Milestone 5 (current) adds File ▸
Export Copy… (flattened or clean copies), Open Recent, "Open with" registration and a
portable Windows release.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/M2_PLAN.md](docs/M2_PLAN.md),
[docs/M3_PLAN.md](docs/M3_PLAN.md), [docs/M4_PLAN.md](docs/M4_PLAN.md) and
[docs/M5_PLAN.md](docs/M5_PLAN.md).

## Install & use (end users)

1. Download `PDFEditor-<version>-win64.zip` (for example `PDFEditor-0.1.0-win64.zip`).
2. Extract it: it contains one `PDFEditor` folder. Put that folder anywhere you like, for
   example `%LOCALAPPDATA%\Programs\PDFEditor` (no administrator rights needed).
3. Run `PDFEditor.exe` in that folder. The program is not signed, so Windows SmartScreen may
   say "Windows protected your PC": click **More info ▸ Run anyway** (only the first time).
4. Optional: **Settings ▸ Register with Windows (Open with)…** adds PDF Editor to the
   "Open with" list of PDF files, for your Windows account only. Moving the folder later?
   Register again from the new place.
5. **Help ▸ Keyboard Shortcuts…** (F1) lists the shortcuts; **Help ▸ Third-Party
   Licenses…** shows the licences of the bundled components (also in
   `THIRD_PARTY_LICENSES.md` and `licenses\` next to `PDFEditor.exe`).

Where PDF Editor keeps its data:

- settings: `%APPDATA%\PDFEditor\PDFEditor.ini`
- saved signatures: `%LOCALAPPDATA%\PDFEditor\PDFEditor\signatures`
- log file: `%LOCALAPPDATA%\PDFEditor\PDFEditor\logs\pdfeditor.log` (its exact path is
  shown in Help ▸ About PDF Editor…; attach it when reporting a problem)

Uninstall: **Settings ▸ Unregister from Windows** (if you registered it), then delete the
`PDFEditor` folder and, to remove your settings, signatures and log too,
`%APPDATA%\PDFEditor` and `%LOCALAPPDATA%\PDFEditor`.

## Installation et utilisation (utilisateurs)

1. Téléchargez `PDFEditor-<version>-win64.zip` (par exemple `PDFEditor-0.1.0-win64.zip`).
2. Extrayez-le : il contient un dossier `PDFEditor`. Placez ce dossier où vous voulez, par
   exemple `%LOCALAPPDATA%\Programs\PDFEditor` (aucun droit d’administrateur nécessaire).
3. Lancez `PDFEditor.exe` dans ce dossier. Le programme n’est pas signé : si Windows
   SmartScreen affiche « Windows a protégé votre ordinateur », cliquez sur **Informations
   complémentaires ▸ Exécuter quand même** (la première fois seulement).
4. Facultatif : **Paramètres ▸ Enregistrer dans Windows (Ouvrir avec)…** ajoute PDF Editor à
   la liste « Ouvrir avec » des fichiers PDF, pour votre compte Windows uniquement. Si vous
   déplacez le dossier, enregistrez-le à nouveau depuis le nouvel emplacement.
5. **Aide ▸ Raccourcis clavier…** (F1) liste les raccourcis ; **Aide ▸ Licences tierces…**
   affiche les licences des composants inclus (aussi dans `THIRD_PARTY_LICENSES.md` et
   `licenses\` à côté de `PDFEditor.exe`).

Où PDF Editor conserve ses données :

- paramètres : `%APPDATA%\PDFEditor\PDFEditor.ini`
- signatures enregistrées : `%LOCALAPPDATA%\PDFEditor\PDFEditor\signatures`
- fichier journal : `%LOCALAPPDATA%\PDFEditor\PDFEditor\logs\pdfeditor.log` (son chemin
  exact est indiqué dans Aide ▸ À propos de PDF Editor… ; joignez-le pour signaler un
  problème)

Désinstallation : **Paramètres ▸ Retirer de Windows** (si vous l’aviez enregistré), puis
supprimez le dossier `PDFEditor` et, pour effacer aussi vos paramètres, signatures et
journal, `%APPDATA%\PDFEditor` et `%LOCALAPPDATA%\PDFEditor`.

## Build the Windows release

```powershell
powershell -File scripts\build_exe.ps1          # build + zip
powershell -File scripts\build_exe.ps1 -Smoke   # same, then run the frozen tests
```

It produces `dist\PDFEditor\` (`PDFEditor.exe`, `_internal\`, `README.md`, `LICENSE`,
`THIRD_PARTY_LICENSES.md`, `licenses\`) and the deliverable
`dist\PDFEditor-<version>-win64.zip` (about 44 MB; about 102 MB extracted). To rerun the
frozen tests against an existing build:

```powershell
$env:PDFEDITOR_FROZEN_EXE = "$PWD\dist\PDFEditor\PDFEditor.exe"; uv run pytest -m frozen
```

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
- Each field change is one Undo step. While typing, Ctrl+Z undoes the typing first; Edit ▸
  Undo validates the field being edited, then undoes the last change.
- Saving (Ctrl+S) appends the values to the file (incremental save), including encrypted
  PDFs, which keep their encryption. If a save fails (read-only or locked file), nothing is
  lost: the values stay in the window and you can retry or use Save As.
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

Run it on your own copy of a real form (e.g. a French Cerfa with ~94 text fields and 20
checkboxes); keep the original untouched and work on a copy.

1. **Open**: the Form Tool is active and every fillable field is tinted blue (on the Cerfa:
   the ~94 text fields and the 20 checkboxes, on every page); push buttons and signature
   fields are not tinted. View ▸ Highlight Form Fields hides/shows the tint.
2. **Accents**: type `é à ç œ €` in a text field (and a name like "Françoise Lœuvre"),
   press Enter: the value renders with the accents; the title shows `*`.
3. **Tab order**: from the first field press Tab repeatedly: fields follow reading order (top
   to bottom, left to right), the view scrolls to the next page after the last field of a
   page, and Shift+Tab goes back; checkboxes get a dashed focus frame and Space toggles them.
4. **Checkboxes**: toggle several checkboxes (click and Space): the tick mark looks exactly
   like the form's original one (same ZapfDingbats tick, same size), checked and unchecked.
   Radio buttons in a group exclude each other.
5. **Auto-shrink**: type a long value in a small single-line field: it is written smaller so
   it fits; with Edit ▸ Auto-shrink Overflowing Text off it keeps its size and is cut.
6. **Undo/redo**: Ctrl+Z / Ctrl+Y undo and redo one field change at a time (checkbox, text,
   drop-down). While typing in a field, Ctrl+Z first undoes the typing; Edit ▸ Undo (or Ctrl+Z
   once there is no typing left to undo) validates the field, then undoes the last change.
7. **Incremental save**: note the file size, press Ctrl+S: the save is immediate, the `*`
   disappears and the file is slightly larger (the changes are appended, the original bytes
   are kept). Ctrl+S again after one more change is just as fast.
8. **Reopen**: close and reopen the file here: every value and checkbox is as you left it.
9. **Other viewers**: open the saved file in Edge, Chrome and Adobe Acrobat Reader: all text
   values (with accents) and checkboxes are shown, and Adobe Reader does not ask to save or
   "repair" the file when you close it without changes.
10. **Save failure**: make the file read-only (Properties ▸ Read-only) and press Ctrl+S: an
    error offers Save As; cancel it: the title still shows `*` and all values are still there.
    Clear Read-only and Ctrl+S saves normally.
11. **Special forms**: a dynamic XFA form shows the amber banner mentioning Adobe Acrobat
    Reader and no Form Tool; an owner-locked form shows the permission banner.

## Free text and stamps (Milestone 3)

For flat forms (no fillable fields: Word exports, "Microsoft Print to PDF" copies, forms
filled by hand before): type text anywhere and tick boxes with ✓ ✗ ● stamps. Text and
stamps are standard PDF annotations (FreeText): other viewers show and print them, and they
stay editable here after the file is reopened.

| Key | Tool / action |
|---|---|
| **H** | Hand tool (pan) |
| **F** | Form Tool (fill fillable fields) |
| **T** | Text Tool |
| **1** / **2** / **3** | ✓ check mark / ✗ cross / ● dot stamp |
| **Delete** (or Backspace) | Delete the selected text or stamp (Edit ▸ Delete Annotation) |
| **Alt** + click | Place without snapping, and over a form field |
| **Ctrl+Enter** | Validate the text being typed (Enter is a new line; Escape cancels) |

- **Text Tool**: click to open a text box and type; Ctrl+Enter, or a click elsewhere,
  validates it (a click elsewhere also starts the next box there, for fast cell-by-cell
  entry). The box snaps to what is under the pointer: inside a **table cell** it starts at
  the cell's left edge (on the baseline for a one-line cell), on an **underline** ("Name:
  ______") the text sits on the line. A dashed blue preview shows the target before you click.
- **Stamps**: clicking in a **checkbox** (a drawn square or a ☐ glyph) or a small cell puts
  the ✓ ✗ ● centred in it and sized to it; elsewhere a 12 pt stamp is centred on the click.
- **Select, move, resize, edit**: with the Text or a stamp tool, click a text or stamp to
  select it (frame with 8 handles); drag it to move it, drag a handle to resize it (a text
  box's width; its height follows the text). Click a selected text again, or double-click
  it, to edit it; emptying it deletes it. Escape deselects.
- **Style**: the toolbar's font size box and colour button set the size and colour of new
  text and change the selected one. Stamps use the current colour.
- Each placement, edit, move, resize, style change and deletion is one Undo step (Ctrl+Z /
  Ctrl+Y). Saving (Ctrl+S) appends them to the file, like form values.
- With the Text or a stamp tool, clicking in a fillable **form field** creates nothing and the
  status bar suggests the Form Tool (F); hold **Alt** to place text over the field anyway.
- Documents whose security settings forbid annotations open with these tools disabled (the
  status bar explains why).

Known limitations:

- Helvetica only (no bold, italic or alignment); integer font sizes (6–72 pt) in the toolbar;
  the box being typed uses Arial, so line breaks may differ slightly from the saved text.
- One selection at a time (no multi-selection, arrow-key nudge or copy/paste); the Hand and
  Form tools cannot select annotations (press T).
- Snapping only knows straight horizontal/vertical lines and rectangles drawn in the page and
  ☐-like symbol characters: nothing on scanned images (OCR is planned for later) and no
  snapping to underlines on pages rotated by 90° or 270°.
- Text boxes made by other programs (e.g. Adobe Acrobat comments) can be moved and edited,
  but editing them (or deleting then undoing) turns them into plain Helvetica text: rich text
  and the original font are lost. Opening such a file may give their comments an internal
  identifier (saved with the next save).
- Rotating a page after placing text keeps the text rotated with the page.
- The hover preview does not change when Alt is pressed or released until the mouse moves.
- Flattening (turning text and stamps into page content on export) comes with Milestone 5.

### Manual text and stamp checklist (Milestone 3)

Run it on copies of your own samples: a form exported from Word (tables, "______" lines,
☐ checkboxes) and a document printed with "Microsoft Print to PDF"; keep the originals.

1. **Tools**: open the Word export: H, F, T, 1, 2, 3 switch tools (toolbar and Edit menu
   follow); typing digits in the page box or in a text box does not switch tools.
2. **Table cells**: with the Text Tool, hover over table cells (dashed preview of the cell),
   click in one and type `Élève à Noël €`, then click the next cell and type again: each text
   starts at its cell's left edge, sits well in the cell, and the accents are shown.
3. **Underlines**: click just above or on a "Name: ______" line: the text sits on the line.
   Alt+click places the text exactly at the pointer instead.
4. **Checkboxes**: with ✓ (1), click in a drawn checkbox and in a ☐ character: the tick is
   centred and fills the box. Do the same with ✗ (2) and ● (3). Click in an empty area:
   a 12 pt stamp is centred on the pointer.
5. **Print-to-PDF sample**: text and stamps can be placed (little or nothing snaps there
   unless the page has drawn lines); the page text is unchanged.
6. **Edit / move / resize / delete**: select a text, drag it, resize it with a side handle
   (its height follows the text), double-click it and change the text, change its size and
   colour from the toolbar, then press Delete. Undo (Ctrl+Z) every step back to the start
   and Redo (Ctrl+Y) them again: each step is one undo, the text and stamps come back exactly.
7. **Incremental save**: Ctrl+S: the `*` disappears and the file is slightly larger.
8. **Other viewers**: open the saved file in Adobe Acrobat Reader, Edge and Chrome: every text
   (with accents) and stamp is shown at the same place, and printing (or printing to PDF)
   from each of them includes them.
9. **Reopen**: close and reopen the file here: select, move and edit a text placed in the
   previous session, save again, and check it once more in another viewer.
10. **Rotated pages**: rotate a page (Ctrl+R), place text and a stamp: they read upright on
    screen and in the other viewers; the editor stays horizontal.
11. **Form fields**: on a fillable form, the Text Tool clicked in a field shows the "use the
    Form tool (F)" message; Alt+click places text over it.
12. **Permissions**: open a document whose security settings forbid comments/annotations:
    the Text and stamp tools are disabled and the status bar explains why.
13. **Save failure**: make the file read-only, add a text, Ctrl+S: an error offers Save As;
    cancel it: the title still shows `*`, the text is still there and Undo still works.
14. **Flatten**: not available yet (Milestone 5): texts and stamps stay editable annotations.

## Signature image (Milestone 4)

Sign a document with a picture of your handwritten signature: photograph or scan it once,
the editor removes the paper background, keeps the signature on this computer and places it
on any page. A placed signature is a standard PDF stamp annotation with a transparent image:
other viewers show and print it, and it stays movable and resizable here after reopening.
This is **not** a certified digital signature.

| Key | Tool / action |
|---|---|
| **S** | Signature Tool (with no saved signature it first opens the import dialog) |
| Signatures ▸ *name* | Use that saved signature (it becomes the default) |
| Signatures ▸ Add Signature… / Manage Signatures… | Import a signature; rename, delete, set the default |
| **Alt** + click | Place without snapping, and over a form field |
| **Delete** (or Backspace) | Delete the selected signature |

- **Import** (Signatures ▸ Add Signature…, also on the toolbar button's arrow): choose or
  drop a photo or scan (PNG, JPEG, BMP, TIFF, WebP; phone photos are turned upright). The
  preview shows the result on a checkerboard (= transparent). Move **Threshold** to the right
  to keep more ink (fainter strokes), to the left to remove more background (shadows,
  grain). **Even out paper** removes shadows and gradients; **Crop to ink** trims the
  margins; **Ink colour** keeps the photographed colour or forces black or blue.
- **Place**: with the Signature Tool, click in a table cell (e.g. next to "Signature :") or
  on a "______" line: the signature sits at the bottom-left of the cell, or on the line,
  sized to fit (at most the default width, 150 pt). Elsewhere a click centres it on the
  pointer; drag on empty space to choose its width. A dashed preview shows where it goes.
- **Move, resize, delete**: click a signature to select it, drag it to move it, drag a
  handle to resize it (its proportions are kept); Delete removes it. Each step is one Undo
  step. The text and stamp tools can select signatures too.
- The image is embedded once per document, however many times it is placed.

Known limitations:

- Background removal uses one global threshold: printed lines or text touching the
  signature in the photo stay unless cropped away; light pixels inside thick strokes may be
  slightly transparent.
- Only signatures made by this editor (or MuPDF-based tools) are movable; image stamps from
  other programs are shown but cannot be selected, and Locked ones cannot be changed.
- Rotating a page after signing turns the signature with the page.
- An image placed several times is shared in the file only while the document stays open
  (and after a full save); undo keeps its own copy of the image, so deleting a signature
  from the saved list never breaks undo.
- Privacy of earlier saves: a signature (or text) placed and then undone or deleted
  before saving is never written to the file. But Save keeps the file's earlier versions
  inside it (it only appends the changes), so a signature that was saved once and deleted
  later can still be recovered from the file with technical tools. To remove it for good,
  use **File ▸ Save As…** (it rewrites the whole file without the leftovers).
- No drag & drop of an image straight onto the page; no certified digital signatures.
- Flattening (turning signatures into page content on export) comes with Milestone 5.

### Manual signature checklist (Milestone 4)

Run it on copies of your own documents (a form with a "Signature :" cell or a signature
line); keep the originals.

1. **Import a phone photo**: sign a white sheet, photograph it with a phone (portrait,
   slight shadow), press S (or Signatures ▸ Add Signature…) and choose the photo: it is
   upright, the paper is transparent on the checkerboard, the shadow is gone with "Even out
   paper" and comes back without it.
2. **Threshold**: move the slider right (more ink, faint strokes appear) and left (less
   background): the preview follows at once; keep a value where the strokes are complete
   and the paper is clean. Try "Crop to ink" and the Black/Blue ink colours; name it and OK.
3. **Place in a cell**: hover over the "Signature :" cell (dashed preview), click: the
   signature sits in the cell, bottom-left, not wider than the cell. Click on a "______"
   line: it sits on the line. Alt+click places it centred on the pointer; drag on empty space
   to choose its width.
4. **Resize**: select it, drag a corner and a side handle: the proportions never change;
   move it; Undo/Redo every step.
5. **Incremental save**: Ctrl+S: the `*` disappears and the file grows by a few KB per
   signature with Black/Blue ink (about 50 KB when the photographed colour is kept), not
   by the size of the photo.
6. **Other viewers**: open the saved file in Adobe Acrobat Reader, Edge and Chrome: the
   signature is at the same place and size, with a transparent background (the cell lines
   show through), and printing (or printing to PDF) from each includes it.
7. **Reopen and move**: close and reopen the file here: select the signature placed in the
   previous session, move and resize it, save, and check it once more in another viewer.
8. **Rotated page**: rotate a page (Ctrl+R) and place a signature: it reads upright here
   and in the other viewers.
9. **Saved signatures**: restart the editor: the signature is still in the Signatures menu;
   add a second one, switch the default from the menu, rename and delete one in Manage
   Signatures…; undoing the deletion of a placed signature still works after deleting the
   saved one.
10. **Save failure**: make the file read-only, place a signature, Ctrl+S: an error offers
    Save As; cancel it: the title still shows `*`, the signature is still there and Undo
    still works.
11. **Privacy of deleted signatures**: place a signature, Undo, Ctrl+S: the file grows
    by well under 2 KB (the image is not written). Place one, Ctrl+S, delete it, Ctrl+S: it
    is gone from every viewer, but the file still holds the earlier version; File ▸ Save As…
    a new file: that one no longer contains the image (its size drops accordingly).
12. **Flatten**: not available yet (Milestone 5): signatures stay movable annotations, so
    anyone with an editor can move or copy them; flatten on export will bake them into the
    page.

## Export, recent files and Windows integration (Milestone 5)

**File ▸ Export Copy…** (Ctrl+E) writes a copy and never changes the open document: with
"Flatten form fields" and "Flatten text, stamps and signatures" the copy shows the same
page but its fields and annotations become plain page content (no longer editable); the
copy is rewritten from scratch, so earlier saved versions (deleted signatures, old
values) are not carried over. **File ▸ Open Recent** lists the last opened files;
**Settings ▸ Register with Windows (Open with)…** / **Unregister from Windows** add or
remove PDF Editor in the "Open with" list of PDF files (current user only).

### Manual checklist (Milestone 5)

Run it on copies of your own documents, with the zip built by `scripts\build_exe.ps1`.

1. **Export of a filled form**: fill a form (e.g. a Cerfa), File ▸ Export Copy… with both
   flatten options: open the copy in Adobe Acrobat Reader, Edge and Chrome: the values are
   shown and there are no fields left to fill (clicking does nothing, no field highlight).
2. **Clean copy**: in a file where a signature was placed, saved, then deleted and saved
   again, File ▸ Export Copy… (no flatten needed): the copy no longer contains the image
   (smaller file; it is not recoverable from the copy).
3. **Recent menu**: open three files, restart: File ▸ Open Recent lists them, most recent
   first, with the full path as tooltip; delete one of the files and choose it: it is
   reported and removed from the list; Clear List empties it.
4. **Open with**: Settings ▸ Register with Windows (Open with)…: in Explorer, right-click a
   PDF ▸ Open with: PDF Editor is listed and opens the file; Settings ▸ Unregister from
   Windows: it is gone from the list (Windows may need a moment to refresh).
5. **Portable build**: extract the zip to `%LOCALAPPDATA%\Programs\PDFEditor`, run
   `PDFEditor.exe`: Help ▸ About shows "Portable build" and the log file path; F1 shows the
   keyboard shortcuts; Help ▸ Third-Party Licenses… shows the notice and the licence texts.

## License

GNU Affero General Public License v3.0 only — see [LICENSE](LICENSE).
