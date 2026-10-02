# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir, windowed build of PDF Editor (dist/PDFEditor/PDFEditor.exe).

Build with ``powershell -File scripts\\build_exe.ps1`` (or ``uv run pyinstaller --noconfirm
--clean pdfeditor.spec``). ``PDFEDITOR_CONSOLE=1`` builds a console exe instead (same
name; use ``--distpath``/``--workpath`` to keep it apart), handy for ``--version`` and
reading tracebacks. The Qt payload is pruned to what the app uses (docs/M5_PLAN.md X2).
"""

import fnmatch
import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
SRC = ROOT / "src"
PKG = SRC / "pdfeditor"
VERSION = re.search(
    r'__version__ = "([^"]+)"', (PKG / "__init__.py").read_text(encoding="utf-8")
).group(1)
CONSOLE = os.environ.get("PDFEDITOR_CONSOLE") == "1"
NAME = "PDFEditor"


def _version_tuple(version):
    parts = [int(x) for x in re.findall(r"\d+", version)][:4]
    return tuple(parts + [0] * (4 - len(parts)))


version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_version_tuple(VERSION),
        prodvers=_version_tuple(VERSION),
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "PDF Editor contributors"),
                        StringStruct("FileDescription", "PDF Editor"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", NAME),
                        StringStruct("LegalCopyright", "AGPL-3.0-only"),
                        StringStruct("OriginalFilename", f"{NAME}.exe"),
                        StringStruct("ProductName", "PDF Editor"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

datas = [
    (str(PKG / "i18n" / "pdfeditor_fr.qm"), "pdfeditor/i18n"),
    (str(PKG / "resources" / "icons"), "pdfeditor/resources/icons"),
    (str(PKG / "resources" / "app.ico"), "pdfeditor/resources"),
]
# Licence texts (added by M5-T6); an empty or missing directory is fine.
LICENSES = PKG / "resources" / "licenses"
if LICENSES.is_dir():
    datas += [
        (str(f), "pdfeditor/resources/licenses") for f in sorted(LICENSES.glob("*")) if f.is_file()
    ]

excludes = [
    # Qt modules the app never imports (only imported ones are collected; be explicit).
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNetwork", "PySide6.QtNetworkAuth",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPrintSupport", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtXml",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtCharts", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSerialBus", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtTextToSpeech", "PySide6.QtNfc",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets",
    "PySide6.QtHttpServer", "PySide6.QtSpatialAudio", "PySide6.QtConcurrent", "PySide6.QtDBus",
    "PySide6.QtUiTools", "PySide6.QtAxContainer", "PySide6.QtSvgWidgets", "PySide6.QtAsyncio",
    "PySide6.QtExampleIcons",
    # Standard library / third-party modules the app does not need (dev and test tools).
    "tkinter", "numpy", "PIL", "unittest", "pydoc", "doctest", "xmlrpc", "test", "lib2to3",
    "pytest", "_pytest", "pytestqt", "pypdf", "cryptography", "PyInstaller",
]

# fontTools (M7: subsetting installed fonts for edited page text) imports its table
# modules by name (ttLib.getTableModule), invisible to the import analysis.
hiddenimports = collect_submodules("fontTools.ttLib.tables")

a = Analysis(
    [str(PKG / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# Prune the Qt payload pulled in by plugins: excludes= alone does not remove these.
DROP = [
    "PySide6/opengl32sw.dll",
    "PySide6/Qt6Qml*.dll",
    "PySide6/Qt6Quick*.dll",
    "PySide6/Qt6VirtualKeyboard.dll",
    "PySide6/Qt6Pdf*.dll",
    "PySide6/Qt6Network.dll",
    "PySide6/Qt6OpenGL.dll",
    "PySide6/plugins/platforminputcontexts/*",
    "PySide6/plugins/generic/*",
    "PySide6/plugins/platforms/qdirect2d.dll",
    "PySide6/plugins/platforms/qminimal.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/imageformats/qicns.dll",
    "PySide6/plugins/imageformats/qtga.dll",
    "PySide6/plugins/imageformats/qwbmp.dll",
    "_ssl.pyd",
    "libssl-3.dll",
]
# Only the Qt base translation is used (pdfeditor_fr.qm ships with the package).
KEEP_TRANSLATIONS = ["qtbase_fr.qm"]


def _keep(entry):
    dest = entry[0].replace("\\", "/")
    if dest.startswith("PySide6/translations/"):
        return dest.rsplit("/", 1)[1] in KEEP_TRANSLATIONS
    return not any(fnmatch.fnmatch(dest, pattern) for pattern in DROP)


a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PKG / "resources" / "app.ico"),
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name=NAME)
