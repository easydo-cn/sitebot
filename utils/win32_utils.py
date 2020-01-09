# coding: utf-8
'''
A set of handy util functions to be used on Windows platform.
'''
import ctypes
import getpass
import os
import platform
import re
import shutil
import subprocess
import sys
import winreg

import win32api
import win32con

from config import CURRENT_DIR
from utils import translate as _, is_syncplugin_installed


def get_winver():
    '''
    Simple way to get windows version info
    Returns: (major, minor, service_pack_major)
    Notice: a incomplete list of version info:
        WIN_8 = (6, 2, 0)
        WIN_7_SP1 = (6, 1, 1)
        WIN_7 = (6, 1, 0)
        WIN_SERVER_2008 = (6, 0, 1)
        WIN_VISTA_SP1 = (6, 0, 1)
        WIN_VISTA = (6, 0, 0)
        WIN_SERVER_2003_SP2 = (5, 2, 2)
        WIN_SERVER_2003_SP1 = (5, 2, 1)
        WIN_SERVER_2003 = (5, 2, 0)
        WIN_XP_SP3 = (5, 1, 3)
        WIN_XP_SP2 = (5, 1, 2)
        WIN_XP_SP1 = (5, 1, 1)
        WIN_XP = (5, 1, 0)
    '''
    wv = sys.getwindowsversion()
    if hasattr(wv, 'service_pack_major'):  # python >= 2.7
        sp = wv.service_pack_major or 0
    else:
        r = re.search("\s\d$", wv.service_pack)
        sp = int(r.group(0)) if r else 0
    return (wv.major, wv.minor, sp)


def is_dokan_installed():
    if sys.platform != 'win32':
        return False

    from ctypes import windll
    from win32com.shell import shell, shellcon

    # 获取到 system32 目录的位置
    SYSTEM_DIR = shell.SHGetFolderPath(0, shellcon.CSIDL_SYSTEM, None, 0)

    try:
        # 测试是否有 Dokan 内核驱动
        # Dokan 驱动文件是 {sys}\drivers\dokan1.sys
        dokan_driver = os.path.join(SYSTEM_DIR, 'drivers', 'dokan1.sys')
        if not os.path.isfile(dokan_driver):
            return False
        else:  # 有驱动，看看驱动的版本
            # 读注册表太麻烦了，直接读文件版本吧
            from win32api import GetFileVersionInfo, LOWORD, HIWORD
            version_info = GetFileVersionInfo(dokan_driver, u'\\')
            ms = version_info['FileVersionMS']
            ls = version_info['FileVersionLS']
            dokan_driver_version = HIWORD(ms), LOWORD(ms), HIWORD(ls), LOWORD(ls)
            # 我们需要 >= 1.1.0.1000
            if dokan_driver_version < (1, 1, 0, 1000):
                return False

        # 测试是否可以导入 dokan.dll 库中的函数
        DokanMain = windll.Dokan1.DokanMain  # noqa
        DokanVersion = windll.Dokan1.DokanVersion  # noqa
    except (AttributeError, WindowsError, OSError):  # Ref about OSError: https://github.com/pyinstaller/pyinstaller/pull/2541  # noqa E501
        return False
    else:
        return True


def is_dot_net_installed():
    '''检测 .Net 是否有安装。现在固定检测 .Net 4.0 (不需要 Service Pack)。'''
    if sys.platform != 'win32':
        return False

    import _winreg

    try:
        key = _winreg.OpenKey(
            _winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full'
        )
    except:  # noqa E722
        return False

    try:
        installed = _winreg.QueryValueEx(key, 'Install')[0]
    except:  # noqa E722
        return False
    else:
        return installed
    finally:
        key.Close()


def install_dokan_library(lock, logger):
    '''安装 Windows 映射盘的依赖: VC++2017 & Dokan Library'''
    if sys.platform != 'win32':
        return

    if lock.acquire(False):
        import platform
        import subprocess
        import tempfile
        from ui_client import refresh_webview

        redistdir = os.path.join(CURRENT_DIR, 'redist')
        tempdir = tempfile.gettempdir()

        try:
            subprocess.check_call([
                os.path.join(redistdir, 'vc_redist.x86.exe'),
                '/install', '/passive', '/norestart',
                '/log "{}"'.format(os.path.join(tempdir, 'vcpp_2017_x86_install.log')),  # noqa E501
            ])
        except:  # noqa E722
            logger.exception(u'安装 VC++ 2017 x86 出错')

        if platform.architecture()[0] == '64bit':
            try:
                subprocess.check_call([
                    os.path.join(redistdir, 'vc_redist.x64.exe'),
                    '/install', '/passive', '/norestart',
                    '/log "{}"'.format(os.path.join(tempdir, 'vcpp_2017_x64_install.log')),  # noqa E501
                ])
            except:  # noqa E722
                logger.exception(u'安装 VC++ 2017 x64 出错')
            dokan_installer = 'Dokan_x64.msi'
        else:
            dokan_installer = 'Dokan_x86.msi'
        try:
            subprocess.check_call([
                'msiexec',
                '/i', os.path.join(redistdir, dokan_installer),
                '/passive', '/qr', '/norestart',
                '/le', os.path.join(tempdir, 'dokan_install.log'),
            ])
        except:  # noqa E722
            logger.error(u'安装 Dokan Library 出错', exc_info=True)

        refresh_webview('netdrive')
        lock.release()


def activate_syncplugin(lock, logger):
    '''激活同步助手'''
    if lock.acquire(False):
        from ui_client import refresh_webview, message
        try:
            rc, regsvr32 = win32api.FindExecutable('regsvr32')
            if rc < 32:
                logger.error(
                    u'FindExecutable 找不到 regsvr32, 返回值是 %r, %r', rc, regsvr32,
                )
                lock.release()
                return rc
            plugin_dll = get_syncplugin_path()
            if not plugin_dll or not os.path.isfile(plugin_dll):
                logger.warn(u'DLL文件 %s 不存在', plugin_dll)
                return

            rc = run_as_admin([
                regsvr32, '/s',
                u'"{}"'.format(as_unicode(plugin_dll)),
            ])
            logger.info(u'激活同步助手，调用 ShellExecute 的返回值是 %r', rc)
            refresh_webview('syncfolders')
            # 大于 32 表示调用成功（但不一定表示注册成功）
            if rc > 32:
                restart_explorer()
        finally:
            lock.release()
            if is_syncplugin_installed():
                message(
                    _('Activate SyncPlugin'),
                    _('Successful activation')
                )
            else:
                message(
                    _('Activate SyncPlugin'),
                    _('Activation fails'),
                    type='warn'
                )


def get_syncplugin_path():
    '''获得shell扩展的路径
    因为shell扩展现在存放在单独的目录里，并且目录名里带有版本号，所以用这个函数来找到最新的路径。
    '''
    versions = []
    for name in os.listdir(CURRENT_DIR):
        fpath = os.path.join(CURRENT_DIR, name)
        if os.path.isdir(fpath) and name.startswith('shellExt_'):
            try:
                versions.append(int(name.rsplit('_', 1)[-1]))
            except ValueError:
                pass

    if versions:
        return os.path.join(
            CURRENT_DIR, 'shellExt_{}'.format(max(versions)), 'shellExt.dll',
        )
    else:
        return None


def get_installed_syncplugin_dll():
    '''
    从注册表中查询当前注册的shell扩展DLL文件的路径。
    '''
    # 从注册表中查询
    dll_key = r'CLSID\{CED0336C-C9EE-4a7f-8D7F-C660393C381F}\InprocServer32'

    if platform.machine() == 'AMD64':
        bitness_flag = winreg.KEY_WOW64_64KEY
    else:
        bitness_flag = winreg.KEY_WOW64_32KEY

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, dll_key, 0, winreg.KEY_READ | bitness_flag) as key:  # noqa E501
            return winreg.QueryValue(key, '')
    except Exception:
        return None


def cleanup_old_syncplugins():
    '''尝试清理掉旧版本的shell扩展
    '''
    current_dll = get_installed_syncplugin_dll()
    if not current_dll:
        return

    current_dll_directory = os.path.dirname(current_dll)

    # 扫描安装目录下所有 shellExt_ 开头的目录，找到非当前版本的同步助手
    for name in os.listdir(CURRENT_DIR):
        fpath = os.path.join(CURRENT_DIR, name)
        if not os.path.isdir(fpath) or not name.startswith('shellExt_'):
            continue

        if fpath != current_dll_directory:
            # 如果 dll 删不掉，说明正在使用，就不要删其他的任何东西
            # 如果没有 dll 文件，或者 dll 文件可以被删除，说明是已经卸载的版本，可以直接删除所有文件
            if os.path.isfile(os.path.join(fpath, 'shellExt.dll')):
                try:
                    os.remove(os.path.join(fpath, 'shellExt.dll'))
                except Exception:
                    continue

            try:
                shutil.rmtree(fpath)
            except Exception:
                pass


def as_unicode(s):
    '''将（来自文件系统的路径等）字符串转换为 unicode
    '''
    if isinstance(s, unicode):
        return s
    try:
        return s.decode(sys.getfilesystemencoding())
    except Exception:
        return s.decode('utf-8')


def run_as_admin(cmd, show=False):
    '''以管理员身份运行一个命令行
    `cmd` 是一个 list，第一个字符串被认为是可执行程序，后面所有都是参数
    `show` 控制 ShellExecute 的 `nShowCmd` 参数
    '''
    executable = as_unicode(cmd[0])
    parameters = u' '.join([as_unicode(p) for p in cmd[1:]])

    # ShellExecuteW is the unicode version of ShellExecute function
    # Doc ref: https://msdn.microsoft.com/en-us/library/windows/desktop/bb762153(v=vs.85).aspx  # noqa E501
    # Return code > 32 means operation succeeds
    return ctypes.windll.shell32.ShellExecuteW(
        None, u'runas', executable, parameters, None, show,
    )


def restart_explorer():
    '''重启资源管理器'''
    CREATE_NO_WINDOW = 0x08000000
    subprocess.call(
        'taskkill /F /FI "IMAGENAME eq explorer.exe" /FI "USERNAME eq {}"'.format(getpass.getuser()),  # noqa E501
        creationflags=CREATE_NO_WINDOW,
    )

    # 新的 Windows 版本中，shell 模式 explorer 路径与窗口模式不一样
    new_explorer = os.path.join(
        os.getenv('WINDIR', 'C:\\Windows'), 'explorer.exe',
    )
    if os.path.isfile(new_explorer):
        explorer = new_explorer
    else:
        explorer = 'explorer.exe'

    subprocess.Popen(
        [explorer, ],
        cwd=os.getenv('WINDIR', 'C:\\Windows'),
        shell=False,
        creationflags=CREATE_NO_WINDOW | win32con.DETACHED_PROCESS,
        close_fds=True,
    )
