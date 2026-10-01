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

## PyInstaller — GPL-2.0-or-later with the bootloader exception

The start-up program (`PDFEditor.exe` is PyInstaller's bootloader with PDF Editor's archive
appended) and PyInstaller's run-time hooks © PyInstaller Development Team —
https://pyinstaller.org. The bootloader is licensed under the GNU General Public License
version 2 or later **with the bootloader exception**, which allows it to be combined with and
distributed as part of programs under any licence; the run-time hooks are under the Apache
License 2.0. Both texts, and PyInstaller's licence notice, are in
`PyInstaller-GPL-2.0-bootloader-exception.txt`. Source code:
https://github.com/pyinstaller/pyinstaller.
