# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for InstRef.

Produces a one-folder build in `dist/InstRef/`. One-folder rather than
one-file on purpose: a single .exe unpacks ~400 MB of Qt and OpenCV into a temp
directory on every launch, which makes startup take many seconds and reliably
upsets antivirus software. The installer hides the folder from the user anyway.

One executable, not two: `InstRef.exe --sync` runs the headless sync the
scheduler needs. A separate console binary would double the install size for
nothing but a console window.

    pyinstaller InstRef.spec --noconfirm
"""

from PyInstaller.utils.hooks import collect_submodules

# instagrapi assembles its API from many small mixins imported dynamically,
# so static analysis finds almost none of them.
hidden = collect_submodules("instagrapi") + ["piexif", "mutagen", "PySide6.QtSvg"]

# Qt ships WebEngine, QML, 3D, charts and multimedia we never touch. Dropping
# them is the difference between a ~700 MB and a ~400 MB install.
excluded = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtNetworkAuth", "PySide6.QtPositioning", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "tkinter", "matplotlib", "scipy", "pandas", "notebook", "IPython", "pytest",
]

a = Analysis(
    ["entry.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("assets/*", "assets"),
        ("README.md", "."),
        ("LICENSE", "."),
    ],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excluded,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InstRef",
    debug=False,
    strip=False,
    upx=False,          # UPX ламає підпис Qt-бібліотек і дратує антивіруси
    console=False,      # GUI без чорного вікна консолі
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="InstRef",
)
