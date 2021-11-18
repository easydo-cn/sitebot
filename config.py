# -*- coding: utf-8 -*-
import json
import locale
import os
import sys

VERSION = 5
BUILD_NUMBER = 2055
GIT_INFO = "3fa2b2fe @ {2020-01-08 12:44:15 +0800} on heads/x-develop"
USER_AGENT = "Mozilla/5.0 (Windows NT 6.2; WOW64) AppleWebKit/534.34 (KHTML, like Gecko) python Safari/534.34 Sitebot"
# Windows specifics
MY_DOCUMENTS = USER_HOME = os.path.expanduser('~')
APP_DATA = os.path.join(USER_HOME, 'edo_assistent')
LOG_DATA = os.path.join(APP_DATA, 'logs')

# Folder that holds all database of workers
WORKER_STORAGE_DIR = os.path.join(APP_DATA, 'workers')

# Make sure all the above folders exist
for folder in (
    APP_DATA, LOG_DATA, WORKER_STORAGE_DIR,
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

# 站点机器人监听地址和端口
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

# 允许访问站点机器人的域名
ALLOW_DOMAIN = ["*"]
# 允许从这些远端访问（域名/IP）
ALLOW_REMOTE = ('everydo.tk', 'localhost.easydo.cn', '127.0.0.1', )

# 消息相关设置
MSG_KEEPALIVE = 60
MSG_QOS = 1

# 站点机器人指令 topic
COMMAND_CATEGORY = 'command'

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


# 每个站点机器人安装都具有唯一的 ID
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


NOUNCE_FIELD = '__nounce'
LANGUAGE, __ = locale.getdefaultlocale()
FS_ROOTDIR = os.path.abspath(os.sep)
# CURRENT_DIR is the app root path, and RUNTIME_DIR is the code root path
RUNTIME_DIR = CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
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

import pkgutil
WORKERS = [
    _w for _a, _w, _c in pkgutil.iter_modules(['workers'])
    if _w != 'threedpreview'
]


# 证书临近过期时间段
NEAR_EXPIRE_DATE = 30 * 1  # 过期前一个月
DEFAULT_CERTIFI_URL = "https://easydo.cn/help/download/setups/ast_cert.zip"
