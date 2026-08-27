# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from neon_drive import __version__

# Prefer Windows' ICU over unrelated DLLs exposed by PDF/developer tools on PATH.
if os.name == 'nt':
    system32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
    os.environ['PATH'] = system32 + os.pathsep + os.environ.get('PATH', '')


rclone_name = 'rclone.exe' if os.name == 'nt' else 'rclone'
app_icon = ['assets/neon-drive-v2.ico'] if os.name == 'nt' else None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[(f'vendor/rclone/{rclone_name}', 'tools')],
    datas=[
        ('assets/neon-drive-v2.png', 'assets'),
        ('vendor/rclone/install.json', 'tools'),
    ],
    hiddenimports=['PySide6.QtNetwork'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NeonDriveDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
)

cli = Analysis(
    ['cli_main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['PySide6.QtNetwork'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
cli_pyz = PYZ(cli.pure)
cli_exe = EXE(
    cli_pyz,
    cli.scripts,
    [],
    exclude_binaries=True,
    name='NeonDriveCLI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
)

coll = COLLECT(
    exe,
    cli_exe,
    a.binaries,
    a.datas,
    cli.binaries,
    cli.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='NeonDriveDownloader',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Neon Drive.app',
        icon=None,
        bundle_identifier='com.neontools.neondrive',
        info_plist={
            'CFBundleShortVersionString': __version__,
            'CFBundleVersion': '5.5.0.7',
            'LSMinimumSystemVersion': '12.0',
            'NSHighResolutionCapable': True,
        },
    )
