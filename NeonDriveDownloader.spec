# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('vendor/rclone/rclone.exe', 'tools')],
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
    icon=['assets/neon-drive-v2.ico'],
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
    icon=['assets/neon-drive-v2.ico'],
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
