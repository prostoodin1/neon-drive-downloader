# -*- mode: python ; coding: utf-8 -*-
from neon_drive import __version__

a = Analysis(
    ['installer_main.py'], pathex=[], binaries=[],
    datas=[('assets/neon-drive-v2.png', 'assets')],
    hiddenimports=['PySide6.QtNetwork'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='NeonDriveInstaller',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='NeonDriveInstallerMac')
app = BUNDLE(
    coll, name='Neon Drive Installer.app',
    bundle_identifier='com.neontools.neondrive.installer',
    info_plist={
        'CFBundleShortVersionString': __version__,
        'CFBundleVersion': '5.5.0.9',
        'LSMinimumSystemVersion': '12.0',
        'NSHighResolutionCapable': True,
    },
)
