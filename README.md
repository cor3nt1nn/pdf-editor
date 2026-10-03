# PDF Editor

Free, open-source (AGPL-3.0) PDF editor for Windows, bilingual French/English.

Goals: fill PDF forms, fill flat forms/scans with free text and ✓ ✗ ● stamps, sign with an
image of a handwritten signature. Milestone 1 is a fast, crisp PDF viewer with page rotation,
undo/redo and saving; Milestone 2 adds filling of standard PDF forms (AcroForm); Milestone 3
adds free text and ✓ ✗ ● stamps on flat forms (Word exports, printed PDFs); Milestone 4
adds signing with an image of a handwritten signature; Milestone 5 adds File ▸
Export Copy… (flattened or clean copies), Open Recent, "Open with" registration and a
portable Windows release; Milestone 6 adds page tools (reorder, delete, insert,
extract, split) and text selection, copy and highlight/underline/strike-through markups;
Milestone 7 edits the page's own text in place; Milestone 8 (current) recognises the text of
scanned pages (OCR), makes it searchable and adds a Windows setup program.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/M2_PLAN.md](docs/M2_PLAN.md),
[docs/M3_PLAN.md](docs/M3_PLAN.md), [docs/M4_PLAN.md](docs/M4_PLAN.md),
[docs/M5_PLAN.md](docs/M5_PLAN.md), [docs/M6_PLAN.md](docs/M6_PLAN.md),
[docs/M7_PLAN.md](docs/M7_PLAN.md) and [docs/M8_PLAN.md](docs/M8_PLAN.md).

## Install & use (end users)

### Install (setup) — recommended

1. Download `PDFEditor-<version>-setup.exe` (for example `PDFEditor-0.1.0-setup.exe`,
   about 38 MB).
2. Run it. The program is not signed, so Windows SmartScreen may say "Windows protected your
   PC": click **More info ▸ Run anyway**.
3. Pick the language of the setup (English or French), accept the licence (AGPL-3.0) and
   keep **Install for me only** (no administrator rights; installed in
   `%LOCALAPPDATA%\Programs\PDFEditor`). "Install for all users" asks for administrator
   rights and installs in `C:\Program Files\PDFEditor`.
4. Optional tasks, both unticked: a desktop shortcut, and **Add PDF Editor to the “Open
   with” list of PDF files (this account only)** — the same as **Settings ▸ Register with
   Windows (Open with)…** in the program (not offered by "Install for all users": each user
   registers it from the program). The setup never changes your default PDF program: to
   make PDF Editor the default, choose it in Windows **Settings ▸ Apps ▸ Default apps**.
5. Start it from the Start menu (**PDF Editor**). To upgrade, run the setup of the newer
   version: it offers to close a running PDF Editor and keeps your settings and signatures.
6. Uninstall: Windows **Settings ▸ Apps ▸ Installed apps ▸ PDF Editor ▸ Uninstall** (or
   Control Panel ▸ Programs and Features). This removes the program, its shortcuts and its
   "Open with" entry, then asks whether to also delete your settings, saved signatures and
   log files (default **No**). Uninstalling an all-users installation deletes no user's data:
   it says that each user's settings, signatures and logs stay in that user's
   `AppData\Roaming\PDFEditor` and `AppData\Local\PDFEditor` folders.

### Portable (zip)

No installation: the program runs from any folder, for example a USB stick.

1. Download `PDFEditor-<version>-win64.zip` (for example `PDFEditor-0.1.0-win64.zip`).
2. Extract it: it contains one `PDFEditor` folder. Put that folder anywhere you like, for
   example `Documents\PDFEditor` or a USB stick (no administrator rights needed; not the
   setup's folder `%LOCALAPPDATA%\Programs\PDFEditor`).
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

Uninstall the portable copy: **Settings ▸ Unregister from Windows** (if you registered it),
then delete the `PDFEditor` folder and, to remove your settings, signatures and log too,
`%APPDATA%\PDFEditor` and `%LOCALAPPDATA%\PDFEditor`. Both versions share these settings.

## Installation et utilisation (utilisateurs)

### Installation (programme d’installation) — recommandée

1. Téléchargez `PDFEditor-<version>-setup.exe` (par exemple `PDFEditor-0.1.0-setup.exe`,
   environ 38 Mo).
2. Lancez-le. Le programme n’est pas signé : si Windows SmartScreen affiche « Windows a
   protégé votre ordinateur », cliquez sur **Informations complémentaires ▸ Exécuter quand
   même**.
3. Choisissez la langue de l’installation (français ou anglais), acceptez la licence
   (AGPL-3.0) et gardez **Installer pour moi uniquement** (aucun droit d’administrateur ;
   installé dans `%LOCALAPPDATA%\Programs\PDFEditor`). « Installer pour tous les
   utilisateurs » demande les droits d’administrateur et installe dans
   `C:\Program Files\PDFEditor`.
4. Tâches facultatives, décochées : un raccourci sur le bureau et **Ajouter PDF Editor à la
   liste « Ouvrir avec » des fichiers PDF (ce compte uniquement)** — comme **Paramètres ▸
   Enregistrer dans Windows (Ouvrir avec)…** dans le programme (non proposé par « Installer
   pour tous les utilisateurs » : chaque utilisateur le fait depuis le programme).
   L’installation ne change jamais votre programme PDF par défaut : pour faire de PDF Editor le programme par défaut,
   choisissez-le dans **Paramètres ▸ Applications ▸ Applications par défaut** de Windows.
5. Lancez-le depuis le menu Démarrer (**PDF Editor**). Pour mettre à jour, lancez
   l’installation de la nouvelle version : elle propose de fermer PDF Editor s’il est ouvert
   et garde vos paramètres et signatures.
6. Désinstallation : **Paramètres ▸ Applications ▸ Applications installées ▸ PDF Editor ▸
   Désinstaller** (ou Panneau de configuration ▸ Programmes et fonctionnalités). Le programme,
   ses raccourcis et son entrée « Ouvrir avec » sont supprimés, puis une question propose de
   supprimer aussi vos paramètres, signatures enregistrées et fichiers journaux (par défaut
   **Non**). La désinstallation d’une installation pour tous les utilisateurs ne supprime
   les données de personne : elle indique que les paramètres, signatures et journaux de
   chaque utilisateur restent dans ses dossiers `AppData\Roaming\PDFEditor` et
   `AppData\Local\PDFEditor`.

### Version portable (zip)

Sans installation : le programme fonctionne depuis n’importe quel dossier, par exemple une
clé USB.

1. Téléchargez `PDFEditor-<version>-win64.zip` (par exemple `PDFEditor-0.1.0-win64.zip`).
2. Extrayez-le : il contient un dossier `PDFEditor`. Placez ce dossier où vous voulez, par
   exemple `Documents\PDFEditor` ou une clé USB (aucun droit d’administrateur nécessaire ;
   pas le dossier de l’installation, `%LOCALAPPDATA%\Programs\PDFEditor`).
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

Désinstallation de la version portable : **Paramètres ▸ Retirer de Windows** (si vous
l’aviez enregistré), puis supprimez le dossier `PDFEditor` et, pour effacer aussi vos
paramètres, signatures et journal, `%APPDATA%\PDFEditor` et `%LOCALAPPDATA%\PDFEditor`. Les
deux versions partagent ces paramètres.

## Build the Windows release

```powershell
powershell -File scripts\build_exe.ps1                     # build + zip
powershell -File scripts\build_exe.ps1 -Smoke              # same, then run the frozen tests
powershell -File scripts\build_exe.ps1 -Installer          # build + zip + setup program
powershell -File scripts\build_exe.ps1 -Installer -Smoke   # same, then frozen + installer tests
```

It produces `dist\PDFEditor\` (`PDFEditor.exe`, `_internal\`, `README.md`, `LICENSE`,
`THIRD_PARTY_LICENSES.md`, `licenses\`) and the deliverable
`dist\PDFEditor-<version>-win64.zip` (about 49 MB; about 110 MB extracted). With
`-Installer` it also compiles `installer\pdfeditor.iss` into
`dist\PDFEditor-<version>-setup.exe` (about 38 MB) with Inno Setup: the compiler is
`$env:ISCC` if set, else an installed Inno Setup 7 or 6, else the portable Inno Setup 7.1.0
package that `scripts\fetch_innosetup.ps1` downloads once from nuget.org (SHA-256 checked)
into `build\tools\innosetup\` — nothing is installed on the build machine. To rerun the
frozen or installer tests against an existing build:

```powershell
$env:PDFEDITOR_FROZEN_EXE = "$PWD\dist\PDFEditor\PDFEditor.exe"; uv run pytest -m frozen
$env:PDFEDITOR_INSTALLER = "$PWD\dist\PDFEditor-0.1.0-setup.exe"; uv run pytest -m installer
```

The installer tests install silently for the current user into a temporary folder (no
shortcut, no "Open with" entry), upgrade, run `--self-check` from the installed copy and
uninstall; they skip when PDF Editor is really installed on the machine.

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

Known limitations:

1. **Document properties**: with "Keep document properties" unchecked the copy loses its
   title, author, keywords, XMP metadata (document and pages) and application private
   data (/PieceInfo), but it still has a file identifier (trailer /ID, needed by PDF
   readers) and a header comment naming the PDF library that wrote it (MuPDF).
2. **Password protection**: "Keep password protection" can be unchecked only for a file
   opened with its owner password, or protected by a user password without any
   restriction. When the author restricted the file (printing, copying, editing…), the
   copy always keeps the protection and its restrictions.

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

## Page tools (Milestone 6a)

Reorder, delete, insert, extract and split pages, with full undo. The **Pages** menu (between
Edit and View) works on the pages selected (highlighted) in the thumbnail sidebar, or on the
current page when none is; a right-click on a thumbnail offers the same actions for the clicked
pages (inserts go next to the clicked page). In the sidebar, **Ctrl+click** / **Shift+click**
build a multi-selection, **dragging** thumbnails moves them where the insertion line shows
(the list scrolls when the pointer nears its top or bottom edge), and **Delete** removes the
selected pages. Nothing asks for confirmation: every operation is one Undo step (Ctrl+Z / Ctrl+Y),
including deletions (the document is restored exactly: annotations, links, outline and form
fields included).

| Key | Pages ▸ action |
|---|---|
| **Ctrl+Shift+N** | Insert Blank Page (after the current page, same size) |
| **Ctrl+Shift+I** | Insert Pages from File… (all pages or a range such as `1-3, 7, 10-`, before/after the current page or at the end) |
| **Ctrl+Shift+Delete** | Delete Pages |
| **Ctrl+R** / **Ctrl+Shift+R** | Rotate Clockwise / Counterclockwise (the selected pages) |
| **Ctrl+Shift+E** | Extract Pages… (the selected pages to a new file; the open document is unchanged) |
| Pages ▸ Split Document… | Every *N* pages, or explicit ranges, to `name-01.pdf`, `name-02.pdf`… in a chosen folder |

- **Inserting from another PDF** copies its pages with their annotations, links between the
  copied pages and form fields; fields whose names the document already has are renamed
  "Name (2)", "Name (3)"… (the status bar says so). An encrypted source asks for its password;
  a source whose security settings forbid copying is refused (unless opened with its owner
  password). A document cannot be inserted into itself.
- **Extracted and split files** are rewritten from scratch (no earlier revisions) and keep the
  protection of the original: a file opened with a password gives files with the same password;
  a file restricted by its author without a password gives files with the same restrictions.
- **Saving after a page operation** rewrites the whole file once (deleted pages leave no trace);
  later saves are incremental again. A static XFA form loses its XFA at that save, and in
  exported, extracted and split copies (the AcroForm fields remain); it stays a static form even
  when the pages holding its fields are deleted. Dynamic XFA forms and documents whose security
  settings forbid assembling pages open with the Pages menu disabled (and thumbnails cannot be
  dragged); copying pages (Extract, Split) follows the copy permission.
- A document always keeps at least one page.

Known limitations:

- Undoing a deletion relies on a complete copy of the document taken when the pages were
  deleted (inserted pages are kept the same way for redo): copies stay in memory up to 32 MB
  in all, then in temporary files (`%TEMP%\pdfeditor-undo-…`, removed when the document is
  closed, or at the next start after a crash). A copy that cannot be stored (disk full)
  cancels the operation with a message.
- **Page labels** (`/PageLabels`: "i, ii, iii, 1, 2…") are not adjusted: labels are positional,
  so after deleting, inserting or moving pages they name other pages than before; inserting at
  the start of a document without labels adds none.
- Outline (bookmark) entries pointing to deleted pages are kept, greyed out, instead of removed;
  the outline of an inserted file is not merged.
- Inserting only part of a form field spread over several pages creates an independent field;
  inserting from an XFA form brings its AcroForm fields only.
- A text box or stamp made by another program (no identifier) that was selected is deselected
  when a page deletion is undone; our own annotations stay selected.
- Reordering is by mouse only (no keyboard reorder); the thumbnails of a 300-page scan take
  a few seconds to appear after opening.

### Manual page tools checklist (Milestone 6a)

Run it on copies of real documents (a scanned Cerfa, a form with fields); keep the originals.

1. **Reorder**: open a scanned Cerfa of several pages, Ctrl+click two thumbnails and drag them
   to the top: the pages move in the view and the sidebar, the title shows `*`, Ctrl+Z puts
   them back and Ctrl+Y moves them again. Drag a single thumbnail to the end.
2. **Delete**: select two pages and press Delete in the sidebar (and Ctrl+Shift+Delete on the
   current page): the pages disappear, the view stays on the same page content, Ctrl+Z brings
   them back with their stamps, text boxes and field values; deleting every page is refused.
3. **Insert from a form**: Pages ▸ Insert Pages from File… with a PDF that has form fields,
   range `1-2`, "After the current page": the two pages arrive with their fields fillable with
   the Form Tool (F); if names collide, the status bar mentions renamed fields. Insert a blank
   page (Ctrl+Shift+N) and type a text box on it.
4. **Extract**: select three pages, Ctrl+Shift+E, save `… - pages.pdf`: the new file has
   exactly those pages, in that order, with their field values; the open document is unchanged.
5. **Split**: Pages ▸ Split Document…, every 2 pages into a new folder: `name-01.pdf`,
   `name-02.pdf`… have the right page counts; running it again asks before replacing them.
6. **Other viewers**: open the saved document, the extracted file and the split files in Adobe
   Acrobat Reader, Edge and Chrome: pages are in the right order, fields fillable, no repair
   prompt when closing Adobe Reader.
7. **Undo after save**: delete a page, Ctrl+S (the save takes a moment: full rewrite), then
   Ctrl+Z: the page is back; Ctrl+S again, reopen the file: the page is there. Rotate a page and
   Ctrl+S: this save is immediate (incremental again).
8. **Protected files**: a file with a user password: extracted files ask for the same password;
   an owner-locked file without the assembling permission: the Pages menu is disabled and the
   status bar explains why; a dynamic XFA form: idem.

## Text markup (Milestone 6b)

Select the text of a page, copy it, and highlight, underline or strike it through. The tools
are in the **Edit** menu and the toolbar:

| Key | Tool / action |
|---|---|
| **Shift+T** | Select Text: drag over text to select it (in reading order), double-click a word, triple-click a line, Shift+click or Shift+drag to extend, Esc to clear |
| **Shift+H** / **Shift+U** / **Shift+S** | Highlight / Underline / Strike Through: drag over text (or double-click a word, triple-click a line) and release: the text is marked |
| **Ctrl+C** | Edit ▸ Copy Text: copies the selected text, or the text under the selected markup, as plain text |
| **Delete** | Deletes the selected markup |

- **Markups** are standard PDF highlight, underline and strike-out annotations: Adobe Reader,
  Edge and Chrome show them, list them in their comments panel and can edit them. Each one
  is one Undo step.
- **Colours**: with a markup tool active, the toolbar colour button sets the colour of new
  markups of that kind (yellow highlight, red underline and strike-through by default; kept
  between sessions). Click an existing markup (with any markup, text or stamp tool) to select
  it: its quads are outlined, the colour button recolours it (one Undo step, "Change markup
  color") and Delete removes it. Markups are never moved or resized; dragging from a markup
  selects the text under it. The font size box does not apply to markups.
- **What text is selectable**: the text drawn by the page itself, including the invisible text
  layer of OCR'd scans. Text boxes, stamps and form field values are not page text. A scanned
  page without OCR has no text: the status bar says "No selectable text here (scanned
  page?)."
- Copying follows the document's security settings: a file whose author forbids copying keeps
  Copy Text disabled. Highlighting needs the annotation permission, like text boxes.
- Squiggly underlines made by other programs are shown, selectable, recolourable and deletable,
  but PDF Editor does not create them.

Known limitations:

- Selection follows the order in which the page draws its text: on some multi-column or
  generated PDFs a drag may jump between columns. Text cannot be selected across pages.
- A markup made by another program that is edited (recoloured) keeps its author and comment;
  deleting it and undoing the deletion re-creates it with PDF Editor's appearance and without
  its other details (author, creation date, reply popup).
- Copy Text copies plain text only (no formatting); right-to-left and vertical text are
  selected in content order.
- Scanned pages need OCR first (planned for a later milestone).

### Manual text markup checklist (Milestone 6b)

Run it on copies of real documents (a Word export, a scanned and OCR'd PDF); keep the
originals.

1. **Select and copy**: Shift+T, drag over two lines of a Word-exported PDF, Ctrl+C, paste in
   Notepad: the text and the line break are right. Double-click a word, triple-click a line,
   Shift+click further down: the blue selection follows.
2. **Highlight**: Shift+H, drag over a sentence: it turns yellow when the mouse is released and
   the title shows `*`; Ctrl+Z removes it, Ctrl+Y brings it back. Double-click a word: it is
   highlighted after a short moment; a triple-click highlights the whole line once.
3. **Underline and strike-through**: Shift+U and Shift+S over another sentence spanning two
   lines: one markup each, red, one line part per line.
4. **Colours**: with the highlight tool, pick green in the colour button and highlight a word:
   it is green; restart PDF Editor: the highlight colour is still green. Click an existing
   underline and pick blue: it turns blue (one Undo step).
5. **Delete and copy a markup**: click a highlight, Ctrl+C (its text is copied), Delete (it
   disappears), Ctrl+Z (it is back).
6. **Rotated and cropped pages**: rotate a page (Ctrl+R) and highlight text on it: the
   highlight sits on the text; rotate back: it still does.
7. **Scanned pages**: on a scan without OCR, dragging with the highlight tool shows "No
   selectable text here (scanned page?)."; on an OCR'd scan, the invisible text can be selected,
   copied and highlighted (the highlight covers the printed words).
8. **Other viewers**: save, open the file in Adobe Acrobat Reader, Edge and Chrome: the
   markups are where they were, in their colours, listed as comments; no repair prompt when
   closing Adobe Reader. A highlight made in Adobe Reader can be selected, recoloured and
   deleted in PDF Editor and keeps its author after recolouring.
9. **Permissions**: an owner-locked file without the annotation or copy permission: the markup
   tools and Copy Text are disabled, Select Text still works.

## Edit page text (Milestone 7)

Correct a word or a few characters of the page's own text — a typo in a Word export, a name,
a date — in place, in the document's font when it has the needed letters. Edit ▸ **Edit Page
Text** (**E**, also on the toolbar after the signature button):

| Gesture | Effect |
|---|---|
| Hover | The line under the pointer gets a light frame and the word a stronger one (I-beam cursor) |
| **Click** / **Double-click** | Select a word / the part of the line in one style (same font, size and colour) |
| **Drag**, **Shift+click** | Select a range of characters; it stays on the line where it started |
| **Enter**, **F2**, or a second click on the selection | Open the editor over the selection, prefilled with its text |
| Type, then **Enter** (or **Tab**, or click elsewhere) | Replace the selected text; an empty editor deletes it |
| **Esc** | Close the editor without changing anything; press again to deselect |

Typing does not open the editor by itself: select, then Enter/F2 (or click the selection
again), then type. Each change is one Undo step ("Edit page text"; Ctrl+Z / Ctrl+Y).

- **Font**: the new text is written with the document's embedded font when it contains every
  character typed (Word, LibreOffice and "Print to PDF" files usually embed only the letters
  they use). Otherwise the installed font of the same family and style is used (Calibri for a
  Calibri document; for "Microsoft Print to PDF" files, whose font names are generic, the
  family is recognised from the font itself, e.g. Arial), else Arial, Times New Roman or
  Courier New — and the status bar says "Replaced with {font}: the document's font lacks some
  of these characters." Only the characters needed are embedded (a few KB).
- **Width**: a slightly longer text is narrowed (down to 85 % of its normal width) to fit the
  original's place, with a status message; beyond that it overflows to the right (status
  message). Shorter text leaves a gap: nothing is re-flowed.
- **Neighbouring characters**: when glyphs of the selection overlap neighbours (kerned text),
  the neighbours are removed and written again unchanged; the status bar names them.
- **Saving**: the next **Save after an edit rewrites the whole file** (status message, once per
  document); later saves are incremental again. Other viewers show the new text and find it
  when searching; the old text is not kept in the file.
- What cannot be edited, with a status message: scanned pages and outlined text ("No editable
  text here…"), the invisible text layer of OCR'd scans, files whose security settings forbid
  changes (the tool is disabled), and pages whose content exceeds 20 MB. Text the program
  cannot rewrite safely — text inside embedded page objects (Form XObjects, e.g. pages
  imposed or stamped by other software), right-to-left (Arabic, Hebrew) or vertical text,
  text drawn twice only in part (fake bold), or characters no installed font has — shows "The
  text could not be changed." and the page stays as it was.

Known limitations:

- One run at a time, within one style on one line: no mixed bold/italic, no multi-line text,
  no re-flow of the paragraph; justified lines keep their original word spacing.
- Missing letters force a substitute font, visible with brand or uncommon fonts (Aptos, for
  example, is not installed on every Windows); font discovery is Windows-only, and installed
  fonts that forbid embedding or have PostScript (CFF `.otf`) outlines are skipped.
- Ligatures such as "ﬁ" appear as one character in the editor and are written back as
  separate letters.
- Narrowed text is slightly compressed horizontally; substitute fonts are embedded without
  hinting (may look a little softer on screen at small sizes); outlined or clipping text
  effects (stroked text) come back as plain filled text.
- The edited text moves to the end of the page's reading order: text selection, copy and
  screen readers meet it after the rest of the page. It is also drawn last, above anything
  the page painted later than the original text (a shape or image over it now sits below
  it); neighbouring letters that overlapped the edited ones are drawn again the same way.
- The new text is written outside the page's optional-content groups (layers) and outside
  its tagged structure (no marked content): it stays visible when the original text's layer
  is hidden, and tagged-PDF readers (accessibility, reflow) do not see it in the structure.
- Text boxes, stamps, signatures and markups over the edited text stay where they were.
- The first edit merges the page's content into one stream, and the page's resources keep
  every name they had; Type3 (bitmap) text is replaced with a substitute font.
- Undoing an edit fails with "The page changed since this edit; it cannot be undone." if the
  page's content was changed differently in between.

### Manual page text checklist (Milestone 7)

Run it on copies of real documents; keep the originals.

1. **Word export** (Calibri, "Save as PDF"): E, hover a paragraph (line and word frames), click
   a misspelt word, Enter, fix it, Enter: the word changes in place, same font and colour, no
   status message about fonts; Ctrl+Z restores it exactly, Ctrl+Y redoes it. Type a letter
   absent from the page (for example a capital Z): "Replaced with Calibri…".
2. **Microsoft Print to PDF** (fonts named `CIDFont+F1`): edit a word with new letters: the
   status bar says "Replaced with Arial…" (or the original family) and the text looks the same.
3. **LibreOffice export** (Writer, File ▸ Export as PDF): edit a word in body text and one in a
   heading; double-click selects only the run in one style; a selection over a bold word is
   reduced with "Only text in a single style can be edited at once…".
4. **Width**: replace a short word by a slightly longer one ("narrowed to fit") and by a much
   longer one ("overflows"); replace a word by nothing (it disappears, the gap stays).
5. **Refusals**: a scanned page ("No editable text here…"), an OCR'd scan ("…invisible OCR
   layer…"), a password-protected file without the change permission (tool disabled).
6. **Rotated page**: rotate a page (Ctrl+R) and edit a word on it: the new text sits on the
   line, in the right direction.
7. **Save is full**: after an edit the status bar says "After editing page text, the next save
   rewrites the whole file." (once per document); Ctrl+S rewrites the file and the title's `*`
   goes. Add a text box and save: incremental again (fast); edit page text again: the next
   save is full again.
8. **Other viewers**: open the saved file in Adobe Acrobat Reader, Edge and Chrome: the new
   text is in place, in the expected font, Ctrl+F finds it and not the old word, copying it
   gives the new text; no repair prompt when closing Adobe Reader. Reopen it in PDF Editor:
   the edited word can be edited again.

## Recognise text in scans (Milestone 8)

A scanned page is a picture: its text cannot be selected, searched, copied or snapped to.
Edit ▸ **Recognise Text (OCR)…** (**Ctrl+Shift+O**) reads it with Tesseract, the OCR engine
built into the PDF library, using the French and English language data shipped with PDF
Editor (nothing is downloaded, nothing leaves the computer). When a document looks scanned,
a banner above the pages says so ("This document looks scanned: …") with a **Recognise
text…** button.

- **Pages**: *This page*, *Pages without text* (default: scans and blank pages, including
  scans whose only real text is a small stamp such as a scanner header or a Bates number)
  or *All pages*. Pages that already have text are recognised but never get a second text
  layer; on a stamped scan the stamp's words are not written again.
- **Make the text searchable (saved with the file)** (default on): the recognised words are
  written into the page as invisible text, exactly over the words of the image. The page
  looks the same; Ctrl+F in any viewer finds the words, and selection, copy and
  highlight/underline/strike-through work on them. The whole run is one Undo step
  ("Recognise text"); the next save is incremental (about 3 KB per page). Unchecked — or
  when the document's security settings forbid changing it (the box is then disabled) — the
  text is recognised for this session only: it can be selected and copied until the document
  is closed, and is not saved.
- Recognition takes about one second per page, in a separate process (the window stays
  responsive; a progress dialog shows "Recognising text… page n of m" with **Cancel**, which
  keeps the pages already done). The status bar ends with "Text recognised on n page(s)".
- **Snapping on scans**: text boxes, stamps and signatures snap to the table cells,
  underlines, checkbox squares and dotted leader lines of a scan, found in its pixels
  (slightly skewed scans are straightened first). No OCR is needed for that.
- The recognised layer is invisible text, so **Edit Page Text** refuses it ("…invisible OCR
  layer…").

Known limitations:

- The page is read as displayed: turn sideways scans upright (Ctrl+R) first; there is no
  automatic orientation detection. Only French and English; other languages come out with
  wrong accents or words.
- Accuracy depends on the scan: about 9 words out of 10 on a clean 200–300 dpi page, less on
  blurred, low-resolution or handwritten pages; there is no confidence display or correction.
- The invisible text uses a standard font (Helvetica, Windows-1252): characters outside it
  (Greek, Cyrillic, some symbols) are stored as "?" in the searchable layer (they are right in
  the session's selectable text); ligatures such as "ﬁ" are stored as their letters ("fi"),
  so a search for "finance" finds them.
- Each page briefly occupies the window (≈ 0.1 s to prepare it). "Make searchable" needs the
  permission to change the document. Snapping on skewed scans may be off by up to ≈ 3 pt;
  dotted leaders are found when their dots are clearly separated.

En français : Édition ▸ **Reconnaître le texte (OCR)…** (Ctrl+Maj+O) lit le texte des pages
numérisées (français et anglais, sans connexion). Choisissez les pages (cette page, les pages
sans texte, toutes les pages) et, coché par défaut, « Rendre le texte recherchable (enregistré
dans le fichier) » : le texte reconnu est ajouté de façon invisible sur l’image, l’aspect de la
page ne change pas, la recherche (Ctrl+F), la sélection et la copie fonctionnent ; une seule
annulation (Ctrl+Z) retire tout. Environ une seconde par page ; **Annuler** garde les pages
déjà faites. Un bandeau propose « Reconnaître le texte… » quand le document semble numérisé.

### Manual OCR checklist (Milestone 8)

Run it on copies of real scans; keep the originals.

1. **Scanner PDF** (a multi-page letter scanned at 300 dpi): the banner "This document looks
   scanned…" appears; click **Recognise text…**, keep the defaults: the progress dialog counts
   pages, the window can still be scrolled, the status bar says "Text recognised on n
   page(s)", the banner disappears, the pages look unchanged.
2. Select Text (Shift+T): drag over a recognised line, Ctrl+C, paste into Notepad: the words
   (accents included) come out. Highlight a recognised word: the highlight sits on the word.
3. Ctrl+Z: the text layer goes (the title's `*` too if nothing else changed); Ctrl+Y brings it
   back. Save (incremental), reopen in Adobe Acrobat Reader, Edge and Chrome: Ctrl+F finds a
   recognised word, the page looks the same, no repair prompt.
4. **Cancel**: start on a 10-page scan and cancel after two pages: those two are searchable
   (one Undo step), the others are not.
5. **Sideways scan**: a page shown on its side: Ctrl+R until upright, then recognise: the words
   are found; rotate it back: selection still follows the words.
6. **Protected file** (password, changes not allowed): the "searchable" box is disabled with a
   tooltip; recognised text is selectable but nothing is saved.
7. **Snapping**: on a scanned form, the text tool snaps to table cells, underlines and
   checkbox squares (and a stamp centres in a square); on a slightly skewed scan too.

### Manual installer checklist (Milestone 8)

Build it with `scripts\build_exe.ps1 -Installer`; do it on a test account or a machine
where PDF Editor is not installed yet.

1. **SmartScreen**: run `PDFEditor-<version>-setup.exe` from Downloads: "Windows protected
   your PC" → More info ▸ Run anyway; the setup shows the app icon and "PDF Editor".
2. **Language**: with Windows in French the setup runs in French without asking, in English
   on an English Windows; on a Windows in another language a box offers English or French.
   Every page is in that language (the licence text itself is the English AGPL).
3. **Install mode**: "Install for me only" needs no administrator prompt; the folder is
   `%LOCALAPPDATA%\Programs\PDFEditor`.
4. **Tasks**: both boxes are unticked; the "Open with" task reads "Add PDF Editor to the
   “Open with” list of PDF files (this account only)" (French: « Ajouter PDF Editor à la
   liste « Ouvrir avec » des fichiers PDF (ce compte uniquement) »). Tick it: after the
   install, right-click a PDF ▸ Open with lists PDF Editor and opens the file; the default
   PDF program is unchanged; **Settings ▸ Unregister from Windows** is enabled in the app.
5. **Start menu**: "PDF Editor" is in the Start menu with its icon and starts the app; the
   last page's "Launch PDF Editor" box starts it too; no desktop shortcut unless ticked.
6. **Apps & features**: Settings ▸ Apps ▸ Installed apps lists "PDF Editor", version
   `<version>`, publisher "PDF Editor contributors", the app icon and about 117 MB.
7. **Upgrade while running**: with PDF Editor open on a document, run the setup again: it
   offers to close the application, installs, and there is still one "PDF Editor" entry in
   Installed apps; settings, recent files and signatures are kept.
8. **Uninstall**: from Installed apps: after the files are removed, a question asks whether
   to also delete settings, saved signatures and log files, listing `%APPDATA%\PDFEditor` and
   `%LOCALAPPDATA%\PDFEditor`; **No** (the default) keeps them, **Yes** deletes both; the
   install folder, the Start menu entry and the "Open with" entry are gone either way.
9. **All users**: run the setup, choose "Install for all users" (administrator prompt): the
   "Open with" task is not offered. Uninstall it from Installed apps: no question about
   deleting data, an information box says where each user's data stays instead; the
   administrator's own `%APPDATA%\PDFEditor` is untouched.

## License

GNU Affero General Public License v3.0 only — see [LICENSE](LICENSE).
