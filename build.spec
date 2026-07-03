# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for APT.

Produces a single portable .exe with embedded frontend.

Usage:
    pyinstaller build.spec
"""

from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path.cwd()

# -- Collect backend Python files --
backend_dirs = [
    "backend",
    "backend/cookbook",
    "backend/cookbook/data",
]

# -- Collect frontend static files --
# The React app (web/dist) is what api.py serves when present; the legacy
# frontend/ is only its fallback. BOTH are bundled so the exe always has a UI,
# but web/dist MUST be built (npm run build) before running PyInstaller or the
# shipped exe silently falls back to the legacy UI.
frontend_dir = PROJECT_ROOT / "frontend"
webdist_dir = PROJECT_ROOT / "web" / "dist"
frontend_exts = (".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".svg",
                 ".gif", ".ico", ".woff", ".woff2", ".ttf", ".json")

datas = []
for d in backend_dirs:
    p = PROJECT_ROOT / d
    if p.is_dir():
        for f in p.rglob("*"):
            if f.suffix in (".py", ".json", ".txt") and "__pycache__" not in f.parts:
                datas.append((str(f), d))

if frontend_dir.is_dir():
    for f in frontend_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in frontend_exts and "__pycache__" not in f.parts:
            datas.append((str(f), str(f.parent.relative_to(PROJECT_ROOT))))

if not (webdist_dir / "index.html").exists():
    raise SystemExit("web/dist missing — run `npm run build` in web/ before PyInstaller "
                     "(otherwise the exe ships the legacy UI)")
for f in webdist_dir.rglob("*"):
    if f.is_file():
        datas.append((str(f), str(f.parent.relative_to(PROJECT_ROOT))))

for extra in ["requirements.txt", "CHANGELOG.md", "LICENSE"]:
    p = PROJECT_ROOT / extra
    if p.exists():
        datas.append((str(p), "."))

a = Analysis(
    ["server.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "flask",
        "json", "os", "platform", "subprocess",
        "threading", "time", "webbrowser", "urllib",
        "shutil", "pathlib", "dataclasses", "re", "typing",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pytest",
        "numpy", "matplotlib", "PIL", "cv2", "pandas", "scipy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="model-hub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_travis=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

exe_debug = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="model-hub-console",
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_travis=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
