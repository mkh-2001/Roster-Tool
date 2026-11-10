# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Roster Tool (one shared spec for macOS and Windows).

Build with:  python scripts/build_app.py [--debug]
(the script runs PyInstaller with this spec and then verifies the output).

Output (one-folder build; nothing is unpacked to a temp folder at launch):
  macOS:   dist/Roster Tool.app
  Windows: dist/Roster Tool/Roster Tool.exe

Only runtime code and resources are included: the roster_tool package (found
by import analysis from packaging/launcher.py), its static UI files and fonts,
and third-party license notices. Tests, fixtures, scripts, docs, private
rosters and development tools are never added.
"""

import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))
from roster_tool import APP_NAME, __version__  # noqa: E402  (single source of truth)

DEBUG = os.environ.get("ROSTER_TOOL_DEBUG_BUILD") == "1"  # console window + debug name
NAME = APP_NAME + (" Debug" if DEBUG else "")
BUNDLE_ID = "local.rostertool" + (".debug" if DEBUG else "")  # IT may replace with the college's reverse-DNS ID
MACOS_MIN = os.environ.get("ROSTER_TOOL_MACOS_MIN", "13.0")  # computed by scripts/build_app.py

PKG = ROOT / "roster_tool"
datas = [
    (str(PKG / "static"), "roster_tool/static"),
    (str(PKG / "assets" / "fonts"), "roster_tool/assets/fonts"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]
# License texts shipped inside the bundled packages' metadata folders.
for dist in ("pypdfium2", "reportlab", "pillow", "openpyxl", "et_xmlfile", "pyinstaller"):
    datas += copy_metadata(dist)
# The Python runtime's own license.
for candidate in (Path(sys.base_prefix) / "LICENSE.txt",
                  Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"):
    if candidate.is_file():
        datas.append((str(candidate), "licenses/Python"))
        break

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=[],
    excludes=[
        # development / unused heavy optional imports
        "pytest", "_pytest", "tkinter", "IPython", "numpy", "pandas", "matplotlib",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "tests",
    ],
    noarchive=False,
)

# ReportLab ships demo fonts (some GPL-licensed) that Roster Tool never uses;
# the help sheet uses the bundled DejaVu fonts only.
a.datas = [d for d in a.datas if not re.search(r"reportlab[/\\]fonts[/\\]", d[0])]

pyz = PYZ(a.pure)

win_version = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )
    numbers = tuple(int(n) for n in re.findall(r"\d+", __version__)[:3]) + (0,)
    win_version = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("FileDescription", APP_NAME),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", APP_NAME),
                StringStruct("OriginalFilename", f"{NAME}.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    strip=False,
    upx=False,            # UPX-compressed executables trigger more antivirus false positives
    console=DEBUG,        # production: no console window; debug build: console with live log
    argv_emulation=False,
    target_arch=None,     # native architecture of the build machine
    codesign_identity=None,
    entitlements_file=None,
    version=win_version,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=None,
        bundle_identifier=BUNDLE_ID,
        version=__version__,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": MACOS_MIN,
            # No Dock icon: the UI is a browser tab, and the app quits when that
            # tab is closed (roster_tool/lifecycle.py).
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Internal college utility. Bundled components: see THIRD_PARTY_NOTICES.md.",
        },
    )
