# -*- coding: utf-8 -*-
import json
import locale
import os
import sys

VERSION = 5
BUILD_NUMBER = 2055
GIT_INFO = "3fa2b2fe @ {2020-01-08 12:44:15 +0800} on heads/x-develop"
USER_AGENT = "Mozilla/5.0 (Windows NT 6.2; WOW64) AppleWebKit/534.34 (KHTML, like Gecko) python Safari/534.34 Assistant"
# Windows specifics
if sys.platform != "win32":
    MY_DOCUMENTS = USER_HOME = os.path.expanduser('~')
else:
    # See: http://stackoverflow.com/q/3858851
    # Also ref to: https://gist.github.com/mkropat/7550097
    import ctypes
    from ctypes import windll, wintypes
    from uuid import UUID

    class GUID(ctypes.Structure):   # [1]
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8)
        ]

        def __init__(self, uuid_):
            ctypes.Structure.__init__(self)
            self.Data1, self.Data2, self.Data3, self.Data4[0], self.Data4[1], rest = uuid_.fields
            for i in range(2, 8):
                self.Data4[i] = rest >> (8 - i - 1)*8 & 0xff

    class FOLDERID:     # [2]
        Documents = UUID('{FDD39AD0-238F-46AF-ADB4-6C85480369C7}')
        Profile = UUID('{5E6C858F-0E22-4760-9AFE-EA3317B67173}')

    _CoTaskMemFree = windll.ole32.CoTaskMemFree     # [4]
    _CoTaskMemFree.restype = None
    _CoTaskMemFree.argtypes = [ctypes.c_void_p]

    class PathNotFoundException(Exception):
        pass

    def get_known_folder_path(folderid):
        _SHGetKnownFolderPath = windll.shell32.SHGetKnownFolderPath     # [5] [3]
        _SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID), wintypes.DWORD,
            wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)
        ]

        fid = GUID(folderid)
        pPath = ctypes.c_wchar_p()
        S_OK = 0
        if _SHGetKnownFolderPath(ctypes.byref(fid), 0, wintypes.HANDLE(0), ctypes.byref(pPath)) != S_OK:
            raise PathNotFoundException()
        path = pPath.value
        _CoTaskMemFree(pPath)
        return path

    try:
        from win32com.shell import shellcon
        CSIDL_PERSONAL = shellcon.CSIDL_PERSONAL
        CSIDL_PROFILE = shellcon.CSIDL_PROFILE
    except Exception as e:
        CSIDL_PERSONAL = 5  # My Documents
        CSIDL_PROFILE = 40  # User Home
    SHGFP_TYPE_CURRENT = 0  # Want current, not default value

    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)

    if windll.shell32.SHGetFolderPathW(
        0, CSIDL_PROFILE, 0, SHGFP_TYPE_CURRENT, buf
    ) == 0:
        USER_HOME = buf.value
    else:
        try:
            USER_HOME = get_known_folder_path(FOLDERID.Profile)
        except:
            USER_HOME = os.path.expanduser('~')

    if windll.shell32.SHGetFolderPathW(
        0, CSIDL_PERSONAL, 0, SHGFP_TYPE_CURRENT, buf
    ) == 0:
        MY_DOCUMENTS = buf.value
    else:
        MY_DOCUMENTS = get_known_folder_path(FOLDERID.Documents)

APP_DATA = os.path.join(USER_HOME, 'edo_assistent')
LOG_DATA = os.path.join(APP_DATA, 'logs')
EDO_TEMP = os.path.join(MY_DOCUMENTS, 'edo_temp')

# Folder that holds all database of workers
WORKER_STORAGE_DIR = os.path.join(APP_DATA, 'workers')

# Make sure all the above folders exist
for folder in (
    APP_DATA, LOG_DATA, EDO_TEMP, WORKER_STORAGE_DIR, FILE_STORE_DIR,
):
    if not os.path.exists(folder):
        os.makedirs(folder)

# 主要配置文件
CONFIG_FILE = os.path.join(APP_DATA, 'config.json')
CONFIG = {}

# HTTP/HTTPS 访问端口现在可以修改
try:
    with open(CONFIG_FILE) as rf:
        CONFIG.update(json.load(rf))
except Exception:
    pass
finally:
    CONFIG.setdefault('http_port', 4999)
    CONFIG.setdefault('https_port', 4997)

HTTP_PORT = CONFIG['http_port']
HTTPS_PORT = CONFIG['https_port']
# 现在修改访问端口后，不支持使用局域网加速下载
P2P_ENABLED = HTTP_PORT == 4999

# 桌面助手监听地址和端口
# TODO Remove ADDRESS & PROTOCOL
PROTOCOL = 'https'
# 外部访问地址
ADDRESS = 'localhost.easydo.cn' if PROTOCOL == 'https' else '127.0.0.1'
# 监听地址
BIND_ADDRESS = '0.0.0.0'
# 内部调用直接使用 HTTP（HTTP 服务器总是会启动）
INTERNAL_PROTOCOL = 'http'
INTERNAL_ADDRESS = '127.0.0.1'
INTERNAL_PORT = HTTP_PORT

# 以这种方式 import config; config.INTERNAL_URL 使用
INTERNAL_URL = '{}://{}:{}'.format(
    INTERNAL_PROTOCOL, INTERNAL_ADDRESS, INTERNAL_PORT
)

DEBUG = True
# 遇到网络错误时的重试间隔（以秒计）
RETRY_INTERVAL = 60
# 自动（定时）任务的检查间隔（以秒计）
AUTO_START_INTERVAL = 60 * 5
# 专属协议
SCHEME = 'edo-ast://'

APP_KEY = 'assistent'
APP_SECRET = '022117e982a933dea7d69110697685c2'

# 允许访问桌面助手的域名
ALLOW_DOMAIN = ["*"]
# 允许从这些远端访问（域名/IP）
ALLOW_REMOTE = ('everydo.tk', 'localhost.easydo.cn', '127.0.0.1', )

# 消息相关设置
MSG_KEEPALIVE = 60
MSG_QOS = 1

# 桌面助手指令 topic
COMMAND_CATEGORY = 'command'

FROZEN = getattr(sys, 'frozen', False)
if not FROZEN:
    GIT_INFO += ' (source code mode)'

# 数据库接口变更了一次
DATA_VERSION = 3
DATA_VERSION_FILE = os.path.join(APP_DATA, '.VERSIONS')

# 缓存控制
# 缓存文件，连续 3 次删除失败后就不理会
MAX_DELETION_RETRY = 3
# 文件查看的缓存文件保留 10 分钟，之后就删除
WAIT_BEFORE_DELETION = 60 * 10
# 是否跳过 SSL 验证
IGNORE_SSL = False

# 从 os.environ 中获取当前是命令行状态，所使用的 key
# TODO 仅为了兼容而存在，未来版本应当去除，改用 SINGLE_PROCESS 与 HEADLESS 变量
SINGLE_PROCESS_KEY = 'SINGLE_PROCESS'
# start the assistant in silent mode
QUIET_MODE_KEY = 'QUIET_MODE'


class LazyEnvBool(object):
    '''Evaluate environment variable existence as boolean'''
    def __init__(self, env_key, value=None):
        '''
        - env_key: 哪个环境变量
        - value: 如果提供value，那么精确匹配这个值，其他任何值都认为是False
        '''
        self.__name = env_key
        self.__value = value

    def __nonzero__(self):
        if 'os' not in sys.modules:
            import os  # noqa

        if self.__value is None:
            return bool(sys.modules['os'].getenv(self.__name, False))
        else:
            return sys.modules['os'].getenv(self.__name, '').strip() == self.__value

    def __repr__(self):
        if self.__value is None:
            return '<LazyEnvBool {} (={})>'.format(self.__name, bool(self))
        else:
            return '<LazyEnvBool {}=={} (={})>'.format(self.__name, self.__value, bool(self))


SINGLE_PROCESS = LazyEnvBool(SINGLE_PROCESS_KEY)
HEADLESS = LazyEnvBool(QUIET_MODE_KEY)

# 每个桌面助手安装都具有唯一的 ID
APP_ID_FILE = os.path.join(APP_DATA, "APP_ID")
if os.path.isfile(APP_ID_FILE):
    with open(APP_ID_FILE) as appidf:
        APP_ID = appidf.read()
else:
    from Crypto.Hash import SHA224
    from Crypto import Random

    APP_ID = SHA224.new(Random.new().read(64)).hexdigest()
    with open(APP_ID_FILE, 'wb') as appidf:
        appidf.write(APP_ID)

try:
    GIT_INFO += ', ID (first 6 bit): {}'.format(APP_ID[:6])
except:
    pass

if sys.platform == 'win32':
    try:
        import win32api
        import win32con
        __hidden = False
        try:
            attribute = win32api.GetFileAttributes(APP_ID_FILE)
            __hidden = attribute & (win32con.FILE_ATTRIBUTE_HIDDEN)
        except:
            pass

        if not __hidden:
            win32api.SetFileAttributes(
                APP_ID_FILE,
                win32con.FILE_ATTRIBUTE_HIDDEN
            )
    except:
        pass

NOUNCE_FIELD = '__nounce'
LANGUAGE, __ = locale.getdefaultlocale()
FS_ROOTDIR = os.path.abspath(os.sep)
# CURRENT_DIR is the app root path, and RUNTIME_DIR is the code root path
if not FROZEN:
    RUNTIME_DIR = CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
else:
    CURRENT_DIR = os.path.abspath(os.path.dirname(sys.executable))
    RUNTIME_DIR = os.path.abspath(os.path.dirname(__file__))
while not os.path.isdir(CURRENT_DIR) and CURRENT_DIR != FS_ROOTDIR:
    CURRENT_DIR = os.path.dirname(CURRENT_DIR)
while not os.path.isdir(RUNTIME_DIR) and RUNTIME_DIR != FS_ROOTDIR:
    RUNTIME_DIR = os.path.dirname(RUNTIME_DIR)
# Windows MBCS hack
try:
    CURRENT_DIR = CURRENT_DIR.decode(sys.getfilesystemencoding())
    RUNTIME_DIR = RUNTIME_DIR.decode(sys.getfilesystemencoding())
except:
    pass

if sys.platform == 'darwin':
    I18N_DIR = os.path.join(RUNTIME_DIR, 'translations')
else:
    I18N_DIR = os.path.join(CURRENT_DIR, 'translations')

ADDON_DIR = os.path.join(APP_DATA, 'addons')
# check if the dir is exist
if not os.path.exists(ADDON_DIR):
    os.mkdir(ADDON_DIR)

if FROZEN:
    WORKERS = [
        'download', 'upload', 'sync', 'view',
        'edit', 'setup_syncfolder',
        'p2pdownload', 'new_webfolder', 'upload_v2', 'script',
        'online_script', 'resolve_conflict', 'process_duplicate',
        'locked_edit',
        'install_webfolder_driver',
    ]
else:
    import pkgutil
    WORKERS = [
        _w for _a, _w, _c in pkgutil.iter_modules(['workers'])
        if _w != 'threedpreview'
    ]

if sys.platform == 'win32':
    WORKERS.append('threedpreview')

GLOBAL_INSTALL = sys.platform == "win32" and FROZEN and ('..' in os.path.relpath(CURRENT_DIR, USER_HOME))
# 可以通过环境变量禁止自动升级；多用户模式安装的桌面助手也禁止升级
if GLOBAL_INSTALL:
    DISABLE_UPGRADE = True
else:
    DISABLE_UPGRADE = LazyEnvBool('AST_AUTO_UPGRADE', value='false')

# 证书临近过期时间段
NEAR_EXPIRE_DATE = 30 * 1  # 过期前一个月
DEFAULT_CERTIFI_URL = "https://easydo.cn/help/download/setups/ast_cert.zip"
