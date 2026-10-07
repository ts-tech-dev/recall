# PyInstaller spec for the Recall desktop app. Build with packaging/build-windows.ps1,
# or directly: python -m PyInstaller --noconfirm packaging/recall.spec
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(root, "recall", "static"), os.path.join("recall", "static"))]
# The app is offline: it ships the search models (fetched by build-windows.ps1) and never downloads them.
models = os.path.join(root, "models")
if not os.path.isdir(models):
    sys.exit("models/ is missing: run python -m recall.fetch_models first")
datas.append((models, "models"))
binaries = []
hiddenimports = collect_submodules("recall") + collect_submodules("uvicorn")

# Packages imported lazily or that ship model/config files PyInstaller can't see.
for pkg in ("rapidocr_onnxruntime", "fastembed", "pystray"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

icon = os.path.join(SPECPATH, "recall.ico")

a = Analysis(
    [os.path.join(SPECPATH, "recall_app.py")],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Recall",
    console=False,  # no console window (the tray icon has Quit); logs go to %LOCALAPPDATA%\Recall\recall.log
    icon=icon if os.path.exists(icon) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Recall")
