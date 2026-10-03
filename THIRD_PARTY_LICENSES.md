# Third-party licenses

PDF Editor is free software, licensed under the GNU Affero General Public License
version 3 only (AGPL-3.0, see `LICENSE`). The Windows build (`PDFEditor-<version>-win64.zip`)
also contains the components below. Their full licence texts are in the `licenses` folder
next to `PDFEditor.exe` (and in `_internal\pdfeditor\resources\licenses`):

| File | Licence |
| --- | --- |
| `AGPL-3.0.txt` | GNU Affero General Public License v3 (PDF Editor, PyMuPDF, MuPDF) |
| `LGPL-3.0.txt` | GNU Lesser General Public License v3 (Qt, PySide6, shiboken6) |
| `GPL-3.0.txt` | GNU General Public License v3 (the LGPL-3.0 is a set of additional permissions on top of it) |
| `PSF-2.0.txt` | Python Software Foundation License and the licences of the libraries bundled with Python |
| `PyInstaller-GPL-2.0-bootloader-exception.txt` | PyInstaller: GPL-2.0-or-later with the bootloader exception (and Apache-2.0 run-time hooks) |
| `MIT-fontTools.txt` | MIT License (fontTools), with the notices of the parts it includes (Adobe AGL: BSD-3-Clause; cu2qu: Apache-2.0) |
| `Apache-2.0.txt` | Apache License 2.0 (Tesseract OCR engine inside MuPDF, tessdata_fast language data) |
| `BSD-2-Clause-Leptonica.txt` | Leptonica licence, BSD 2-Clause style (image library inside MuPDF, used by Tesseract) |

## Qt, PySide6 and shiboken6 — LGPL-3.0

- Qt 6 (Qt Core, Gui, Widgets, Svg and the platform, image-format, icon-engine and style
  plugins) © The Qt Company Ltd and other contributors — https://www.qt.io
- PySide6 and shiboken6 (Qt for Python) © The Qt Company Ltd — https://www.qt.io/qt-for-python

They are used under the GNU Lesser General Public License version 3 (`LGPL-3.0.txt`, which
refers to `GPL-3.0.txt`). Source code: https://download.qt.io/official_releases/qt/ (Qt) and
https://code.qt.io/cgit/pyside/pyside-setup.git/ (PySide6, shiboken6), or
https://pypi.org/project/PySide6/#files for the exact wheels used. Qt itself contains
third-party code under permissive licences (FreeType, HarfBuzz, libpng, libjpeg, zlib, PCRE2,
…), listed at https://doc.qt.io/qt-6/licenses-used-in-qt.html.

**Replacing the Qt libraries.** The build is a plain folder (PyInstaller "onedir"), not a
single executable: Qt and PySide6 are separate, dynamically loaded files under
`_internal\PySide6` (`Qt6Core.dll`, `Qt6Gui.dll`, `Qt6Widgets.dll`, `Qt6Svg.dll`,
`plugins\…`, the `QtCore.pyd`, `QtGui.pyd`, … extension modules) and `_internal\shiboken6`.
You may replace them with your own, modified or rebuilt, binary-compatible versions (same Qt
major/minor version and compiler, e.g. PySide6/Qt 6.11 built with MSVC for 64-bit Python
3.12) by overwriting those files; PDF Editor then uses them at the next start. Its own
Python code is in `_internal\base_library.zip` and the PyInstaller archive inside
`PDFEditor.exe`, and its complete source code is published under the AGPL (see below), so
you can also rebuild the whole application against another Qt with
`scripts\build_exe.ps1`.

## PyMuPDF and MuPDF — AGPL-3.0

- PyMuPDF © Artifex Software, Inc. — https://github.com/pymupdf/PyMuPDF
- MuPDF © Artifex Software, Inc. — https://mupdf.com — which includes third-party libraries
  (FreeType, HarfBuzz, jbig2dec, libjpeg, OpenJPEG, zlib, lcms2, Gumbo, …) under licences
  compatible with the AGPL.

They are used under the GNU Affero General Public License version 3 (`AGPL-3.0.txt`); Artifex
also offers them under a commercial licence, which PDF Editor does not use. Because PDF Editor
links with them, PDF Editor as a whole is distributed under the AGPL-3.0 too. Source code:
PyMuPDF and MuPDF at the links above (the exact versions are shown in Help ▸ About); the
complete corresponding source code of PDF Editor (including `pdfeditor.spec` and
`scripts\build_exe.ps1`, which produce this build) is the project repository this release was
published from. If you received this program without its source code, you are entitled to
obtain it from whoever gave it to you.

## fontTools — MIT License

fontTools © Just van Rossum and the fontTools contributors —
https://github.com/fonttools/fonttools — subsets installed fonts when edited page text needs
a font the document does not contain. It is used under the MIT License (`MIT-fontTools.txt`,
which also reproduces its `LICENSE.external`: the Adobe Glyph List under the BSD 3-Clause
License and cu2qu under the Apache License 2.0). Source code: https://pypi.org/project/fonttools/#files.

## Tesseract, Leptonica and tessdata_fast — text recognition (OCR)

- Tesseract OCR © Google and the Tesseract contributors — https://github.com/tesseract-ocr/tesseract
  — and Leptonica © Dan Bloomberg and the Leptonica contributors — http://www.leptonica.org —
  are compiled into MuPDF (`_internal\pymupdf\mupdfcpp64.dll`) and recognise the text of
  scanned pages. Tesseract is under the Apache License 2.0 (`Apache-2.0.txt`), Leptonica under
  its BSD 2-Clause style licence (`BSD-2-Clause-Leptonica.txt`). Source code: with MuPDF's
  source distribution (both are in its `thirdparty` folder), and at the links above.
- Language data `fra.traineddata` and `eng.traineddata` (in
  `_internal\pdfeditor\resources\tessdata`) from tessdata_fast © Google and the Tesseract
  contributors — https://github.com/tesseract-ocr/tessdata_fast — under the Apache License 2.0
  (`Apache-2.0.txt`, also as `LICENSE` next to the files; `VERSION.txt` names the exact
  commit). The files are unmodified.

## Python — PSF License

Python 3.12 runtime (`python312.dll`, the standard library in `_internal\base_library.zip`
and `_internal\*.pyd`) © Python Software Foundation — https://www.python.org — under the
Python Software Foundation License Version 2 (`PSF-2.0.txt`). That file also contains the
licences of the libraries shipped with Python that this build includes (bzip2, libffi, XZ
Utils/liblzma, zlib, OpenSSL's libcrypto, mpdecimal, …). Source code:
https://www.python.org/downloads/source/.

The Microsoft Visual C++ runtime (`VCRUNTIME140*.dll`, `MSVCP140*.dll`, `ucrtbase.dll`,
`api-ms-win-*.dll`) is redistributed as permitted by Microsoft's Visual Studio
licence terms for redistributable code.

## Inno Setup — the setup program and the uninstaller

`PDFEditor-<version>-setup.exe` and the uninstaller it installs (`unins000.exe`) are built
with Inno Setup © 1997-2026 Jordan Russell, portions © 2000-2026 Martijn Laan —
https://jrsoftware.org/isinfo.php. They contain Inno Setup's setup and uninstall program,
which is used under the Inno Setup License (https://jrsoftware.org/license.txt):

> Permission is granted to anyone to use this software for any purpose, including
> commercial applications, and to alter and redistribute it, provided that the following
> conditions are met: 1. All redistributions of source code files must retain all
> copyright notices that are currently in place, and this list of conditions without
> modification. 2. All redistributions in binary form must retain all occurrences of the
> above copyright notice and web site addresses that are currently in place (for example,
> in the About boxes). 3. The origin of this software must not be misrepresented; you must
> not claim that you wrote the original software. If you use this software to distribute a
> product, an acknowledgment in the product documentation would be appreciated but is not
> required. 4. Modified versions in source or binary form must be plainly marked as such,
> and must not be misrepresented as being the original software.
>
> This software is provided "as-is," without any express or implied warranty. In no event
> shall the author be held liable for any damages arising from the use of this software.

The portable zip does not contain Inno Setup. The setup script is `installer\pdfeditor.iss`
in PDF Editor's source code (AGPL-3.0).

## PyInstaller — GPL-2.0-or-later with the bootloader exception

The start-up program (`PDFEditor.exe` is PyInstaller's bootloader with PDF Editor's archive
appended) and PyInstaller's run-time hooks © PyInstaller Development Team —
https://pyinstaller.org. The bootloader is licensed under the GNU General Public License
version 2 or later **with the bootloader exception**, which allows it to be combined with and
distributed as part of programs under any licence; the run-time hooks are under the Apache
License 2.0. Both texts, and PyInstaller's licence notice, are in
`PyInstaller-GPL-2.0-bootloader-exception.txt`. Source code:
https://github.com/pyinstaller/pyinstaller.
