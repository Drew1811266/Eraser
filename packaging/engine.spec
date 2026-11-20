# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Eraser engine sidecar.

The engine is a console-free executable that speaks newline-delimited JSON-RPC
over stdio, so the shell can spawn it and nothing else. Two consequences shape
this file:

* **onedir, not onefile.** Onefile unpacks ~200 MB to %TEMP% on every launch,
  which costs seconds and trips antivirus heuristics; a folder ships once and
  starts immediately.
* **no console.** `console=False` keeps a black window from flashing behind the
  app; the engine only ever writes to stderr, which Electron captures anyway.

Third-party payloads (models, ffmpeg) are *not* bundled here — the installer
places them next to the executable, and the engine already searches there
before falling back to the repository layout.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent

hiddenimports = [
    # Reached through importlib/registry lookups rather than static imports.
    *collect_submodules("engine"),
]

# onnxruntime ships its providers as sibling DLLs; without them the session
# silently falls back to CPU (or fails to create at all).
binaries = []
for package in ("onnxruntime", "cv2"):
    binaries += collect_dynamic_libs(package)

excludes = [
    # Never imported, and each one is tens of megabytes of noise.
    "tkinter",
    "matplotlib",
    "pandas",
    "scipy",
    "IPython",
    "pytest",
    "setuptools",
    "pip",
    "torch",
    "torchvision",
    "test",
    "unittest",
    "pydoc_data",
]

a = Analysis(
    [str(ROOT / "engine" / "entrypoint.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="eraser-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="eraser-engine",
)
