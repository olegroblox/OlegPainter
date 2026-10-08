# -*- mode: python ; coding: utf-8 -*-


# OlegPainter.spec
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
from PyInstaller.utils.hooks import collect_all
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.building.datastruct import Tree
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
)
import re

# File properties of OlegPainter.exe («Подробно» in Explorer): the same version as
# the window title, read from the one place it is defined.
APP_VERSION = re.search(r'APP_VERSION = "([^"]+)"', open("application/support.py", encoding="utf-8").read()).group(1)
_numbers = tuple((list(map(int, APP_VERSION.split("."))) + [0, 0, 0, 0])[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_numbers, prodvers=_numbers),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "OlegPainter"),
            StringStruct("FileDescription", "OlegPainter"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", "OlegPainter"),
            StringStruct("LegalCopyright", "GNU GPL v3.0"),
            StringStruct("OriginalFilename", "OlegPainter.exe"),
            StringStruct("ProductName", "OlegPainter"),
            StringStruct("ProductVersion", APP_VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

datas = []
binaries = []
hiddenimports = []

for pkg in ("PIL", "numpy", "sklearn", "keyboard", "pynput", "windows_capture"):
    collected = collect_all(pkg)
    datas += collected[0]
    binaries += collected[1]
    hiddenimports += collected[2]

hiddenimports += collect_submodules("cv2")
# SciPy 1.18 imports its bundled array-API layer by name at run time (scikit-learn
# pulls it in); the PyInstaller hook misses it and the EXE fails on start.
hiddenimports += collect_submodules("scipy._external")
hiddenimports += ["PySide6.QtSvg", "PySide6.QtOpenGLWidgets"]  # явно подтянуть нужные модули
# interception-python imports win32api/win32con at module load but declares no
# deps, so make sure pywin32 submodules ship even though nothing imports them
# directly in our source (otherwise `import interception` crashes the engine).
hiddenimports += ["win32api", "win32con", "win32gui", "win32file"]

datas += collect_data_files("interception")

datas += [
    ("assets", "assets"),
    ("ui/quick/qml", "ui/quick/qml"),
    ("docs/licenses/windows-capture-MIT.txt", "licenses"),
    ("ui/OlegPainter Pro v1.3.ico", "ui"),
    # Model registry manifest (tiny, versioned). Heavy model binaries stay out
    # of the build and are fetched on demand via the in-app downloader.
    ("models/manifest.json", "models"),
    # AI-001 catalog (read next to engine/ai/models.py) and the QML English table.
    ("engine/ai/catalog.json", "engine/ai"),
    ("ui/quick/i18n", "ui/quick/i18n"),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={"pyside6": {"hiddenimports": ["PySide6.QtSvg"]}},
    runtime_hooks=[],
    excludes=[
        # Heavy ML stacks pulled in transitively by the onnxruntime / sklearn
        # PyInstaller hooks but NEVER imported by OlegPainter source (verified by
        # grep across the whole tree). onnxruntime *inference* (InferenceSession)
        # does not need torch/tensorflow — those are only optional training/convert
        # integrations. Excluding them removes ~2-3 GB of dead weight from the build.
        "torch",
        "torchvision",
        "torchaudio",
        "tensorflow",
        "tensorboard",
        "tensorflow_probability",
        "keras",
        "jax",
        "flax",
        # Plotting / dataframe stacks: not imported anywhere in the app.
        "matplotlib",
        "pandas",
        # Dev/test-only — leak in via numba.testing / sklearn test utils.
        "pytest",
        "_pytest",
        "IPython",
        "notebook",
        "jupyter",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtDesigner",
        "PySide6.QtLocation",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtPositioning",
        "PySide6.QtQuick3D",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtStateMachine",
        "PySide6.QtTest",
        "PySide6.QtTextToSpeech",
        "PySide6.QtVirtualKeyboard",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
    ],
)
# The PySide6 hooks ship every QML module of Qt with the DLLs it loads. The window
# uses only QtQuick (Controls, Layouts, Dialogs): leave out the browser engine
# (190 MB, left from the removed web shell), 3D, PDF and the like, and OpenCV's video
# codec (the app reads no video). About 260 MB less; `--self-test` checks the result.
_UNUSED_QT = ("webengine", "webview", "webchannel", "websockets", "quick3d", "qt63d", "qt3d",
              "qt6pdf", "qtpdf", "/pdf/", "texttospeech", "sensors", "scxml", "remoteobjects",
              "positioning", "location", "multimedia", "spatialaudio", "charts", "datavisualization",
              "graphs", "virtualkeyboard", "qttest", "qt6test", "quicktest")


def _needed(entry):
    name = entry[0].replace("\\", "/").lower()
    if name.startswith("pyside6/"):
        return not any(part in name for part in _UNUSED_QT)
    return "opencv_videoio_ffmpeg" not in name


a.binaries = [entry for entry in a.binaries if _needed(entry)]
a.datas = [entry for entry in a.datas if _needed(entry)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    # One-dir build: keep binaries/datas OUT of the exe (exclude_binaries=True) so
    # COLLECT lays them into _internal exactly once. The previous spec passed
    # a.binaries/a.datas to BOTH EXE and COLLECT, which embedded a full copy inside
    # the exe (542 MB) AND duplicated it in _internal -> 1.6 GB dist. With this the
    # exe is a small bootstrapper and the dist shrinks by the duplicated payload.
    exclude_binaries=True,
    name="OlegPainter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed: a console window next to the app confuses users. Startup errors are
    # shown in a message box and written to logs/ (STARTUP-001).
    console=False,
    icon="ui/OlegPainter Pro v1.3.ico",
    version=version_info,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OlegPainter",
)
