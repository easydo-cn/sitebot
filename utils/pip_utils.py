# coding: utf-8
'''
Simple utils that wrap around `pip` commandline tool.
'''
import sys
import os.path
import logging
import subprocess

try:
    from config import ADDON_DIR
except ImportError:
    ADDON_DIR = os.path.join(
        os.path.expanduser('~'), 'edo_assistent', 'addons'
    )
    if not os.path.exists(ADDON_DIR):
        os.makedirs(ADDON_DIR)

if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)

EXTENDED_PIP_ARGS = ('--disable-pip-version-check', '--isolated', )
DEFAULT_PIP_INDEX = 'https://pypi.mirrors.ustc.edu.cn/simple'


def require(packages, index=DEFAULT_PIP_INDEX, upgrade=False):
    '''
    动态安装指定的包
    - 已经安装的包不会重复安装；
    - 指定特定版本需求的包会重复安装；
    - 安装之后，指定的包立刻可用；
    '''
    if isinstance(packages, (list, tuple, )):
        packages = [str(pkg) for pkg in packages]
    else:
        packages = [str(packages), ]

    # 检测是否已经安装
    # 如果带版本号，认为没安装（检测版本号很麻烦）
    # TODO 带版本号的需求也支持检测是否已安装，避免重复安装
    to_be_installed = [
        pkg for pkg in packages
        if not any([
            '>' in pkg,  # pkg>x.y / pkg>=x.y
            '<' in pkg,  # pkg<x.y / pkg<=x.y
            '=' in pkg,  # pkg==x.y
            _is_package_installed(pkg),
        ])
    ]
    # 只安装没有安装的
    if to_be_installed:
        logging.getLogger().debug(
            u'Packages to be installed: %s', to_be_installed
        )
        _install_packages(to_be_installed, index=index, upgrade=upgrade)
    else:
        logging.getLogger().debug(u'All requirements satisfied, skipping')


def _is_package_installed(pkg):
    '''
    Check whether given package is installed
    Notice: Python alternative to `pip show "pkg"`.
    '''
    logger = logging.getLogger()

    pkg = str(pkg)
    logger.debug(u'Check installation for "%s"', pkg)
    if '>' in pkg:
        pkg = pkg.split('>')[0]
    if '<' in pkg:
        pkg = pkg.split('<')[0]
    if '=' in pkg:
        pkg = pkg.split('=')[0]

    logger.debug(u'Normalized package name is "%s"', pkg)
    args = ['pip', 'show', pkg, ]
    args.extend(EXTENDED_PIP_ARGS)
    logger.debug(u'Final commandline: %s', args)
    return subprocess.call(args) == 0


def _install_packages(packages, index=DEFAULT_PIP_INDEX, upgrade=False):
    '''
    Install packages by given name(s).
    Notice:
    - this is an alternative to `pip install --index-url "index" "pkg1" "pkg2"`;
    '''
    logger = logging.getLogger()

    args = ['pip', 'install', ]
    if upgrade:
        args.append('--upgrade')

    if isinstance(packages, (list, tuple, )):
        args.extend([str(pkg) for pkg in packages])
    else:
        args.append(str(packages))

    args.extend(EXTENDED_PIP_ARGS)
    args.extend(['--index-url', DEFAULT_PIP_INDEX, '--target', ADDON_DIR, ])

    logger.debug(u'Final commandline: %s', args)
    return subprocess.call(args) == 0
