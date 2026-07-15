# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import platform
import runpy


project_root = Path(SPECPATH).resolve().parent
version = runpy.run_path(project_root / "chat_online" / "version.py")["__version__"]
machine = platform.machine().lower()
architecture = "x64" if machine in {"amd64", "x86_64"} else machine.replace(" ", "-")
release_name = f"ChatOnline-{version}-windows-{architecture}"
entry_point = project_root / "聊天.py"
icon_path = project_root / "packaging" / "chat-online.ico"
version_file = project_root / "packaging" / "version_info.txt"


a = Analysis(
    [str(entry_point)],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChatOnline",
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
    icon=str(icon_path),
    version=str(version_file),
)

cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChatOnline-CLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=str(version_file),
)

bundle = COLLECT(
    gui,
    cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=release_name,
)

