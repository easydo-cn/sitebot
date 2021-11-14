# coding: utf-8
# -*- mode: python -*-

import os
import sys
import glob
import shutil
import certifi

from PyInstaller import compat

sys.path.insert(0, compat.getcwd())
from .config import (
    WORKERS, VERSION, BUILD_NUMBER
)

block_cipher = None
hiddenimports = [
    'zopen.frs',
    'pyoauth2',
    'edo_client',
    'PyPDF2',
    'cStringIO',
    'flask.ext',
    'babel',
    'flask_babel',
]
hiddenimports.extend(['workers.{}'.format(i) for i in WORKERS])
for b in [
    'blueprint_admin', 'blueprint_worker', 
]:
    hiddenimports.append('blueprints.{}'.format(b))

if compat.is_win:
    hiddenimports.extend([
        'wincertstore',
        'plugins',  # <= Not working well
        # Manually add these modules
        'plugins.aclauncher', 'plugins.dreamweaver', 'plugins.homesite',
        'plugins.homesite5', 'plugins.msohtmed',
        'plugins.photoshp', 'plugins.proe', 'plugins.sldworks',
        # MS Office plugins
        'plugins.winword', 'plugins.excel', 'plugins.powerpnt',
        # WPS Office plugins
        'plugins.wps', 'plugins.et', 'plugins.wpp',
        'win32serviceutil', 'reportlab', 'reportlab.pdfbase.cidfonts'
    ])
elif compat.is_darwin:
    hiddenimports.extend([
        'reportlab.rl_settings', 'subprocess32',
    ])
elif compat.is_linux:
    pass
else:
    raise NotImplementedError(
        u'Unsupported platform: {}'.format(compat.system())
    )


def collect_ca_pem():
    pem_path = certifi.where()
    pem_file = os.path.basename(pem_path)
    ca_bundle_dir = 'certifi'
    pem_target_path = os.path.join(ca_bundle_dir, pem_file)
    # Clear CA bundle file upon each building
    # in case we added some certs for testing
    if os.path.isfile(pem_target_path):
        os.remove(pem_target_path)
        print(u'> Previous CA bundle deleted')
    if not os.path.exists(pem_target_path):
        if not os.path.exists(ca_bundle_dir):
            os.mkdir(ca_bundle_dir)
        shutil.copyfile(pem_path, pem_target_path)
        print(u'> New CA bundle file copied')


collect_ca_pem()


def extra_datas(mydir):

    def rec_glob(p, files):
        for d in glob.glob(p):
            if os.path.isfile(d):
                files.append(d)
            rec_glob('{}/*'.format(d), files)

    files = []
    rec_glob('{}/*'.format(mydir), files)
    datas = []
    for f in files:
        datas.append((
            os.path.abspath(f),
            os.path.dirname(f),
        ))

    return datas


datas = []
datas += extra_datas('scripts')
if compat.is_win:
    datas += extra_datas('static')
    datas += extra_datas('templates')
    datas += extra_datas('images')
    datas += extra_datas('certifi')
    datas += extra_datas('qpdf')
translation_datas = extra_datas('translations')
for i in translation_datas:
    ext = os.path.splitext(i[0])[-1]
    if ext not in ('.mo', '.qm', ):
        continue
    datas.append(i)
    print(u'> {} added into datas'.format(i[0]))

RTH_DIR = os.path.join(compat.getcwd(), 'builder')

a = Analysis(
    ['main.py'],
    pathex=[compat.getcwd()],
    binaries=None,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=None,
    runtime_hooks=[os.path.join(RTH_DIR, 'rth_pyqt4.py')],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher
)

# Package VC runtime DLLs within
if compat.is_win:
    a.binaries += [
        ('msvcr100.dll', 'C:\\Windows\\System32\\msvcr100.dll', 'BINARY'),
        ('msvcp100.dll', 'C:\\Windows\\System32\\msvcp100.dll', 'BINARY')
    ]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
# Windows
if compat.is_win:
    exe = EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        name='edo_assistent',
        debug=False,
        strip=False,
        upx=False,
        console=False,
        icon='./images/zopen.ico',
        version='./version_info.txt'
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        name='webserver'  # TESTING
    )
# Mac OS X
elif compat.is_darwin:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name='edo_assistent',
        debug=True,
        strip=False,
        upx=True,
        console=False,
        icon='images/zopen.icns'
    )
    app_name = 'EdoAssistent'
    version = '.'.join([str(VERSION), str(BUILD_NUMBER)])
    coll = BUNDLE(
        exe,
        name='EdoAssistent.app',
        icon='./images/zopen.icns',
        bundle_identifier='easydo.assistent',
        info_plist={
            'CFBundleDevelopmentRegion': 'en',
            'CFBundleExecutable': os.path.join('MacOS', 'edo_assistent'),
            'CFBundleName': app_name,
            'CFBundleDisplayName': app_name,
            'CFBundleShortVersionString': version,
            'CFBundleVersion': version,
            'LSApplicationCategoryType': 'Productivity',
            'LSMinimumSystemVersion': '10.8.5',
            'NSHumanReadableCopyright': u'Copyright © 2014-2016 广州润普网络科技有限公司 All rights reserved.',
            'LSUIElement': '1',
            'LSHasLocalizedDisplayName': '1',
            'NSHighResolutionCapable': 'True',  # For retina display
        }
    )
