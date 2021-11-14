# encoding: utf-8
import getpass
import importlib
import os
import sys
import json
import logging
import logging.handlers
from multiprocessing import Queue
import time
import traceback

from flask import Blueprint, Flask, request, redirect, url_for, g as flask_g
import gevent.wsgi

import worker
from utils import (
    translate, addr_check, jsonp, extract_data,
    extract_data_list, get_sync_assistant_build_number,
    get_sync_system_info, is_internal_call, detect_locale, clear_old_files,
    call_local_script
)
import config
from config import (
    BUILD_NUMBER, VERSION, ALLOW_DOMAIN, EDO_TEMP, WORKERS,
    CURRENT_DIR, DISABLE_UPGRADE, LOG_DATA, HEADLESS,
)
from libs.managers import get_site_manager

site_manager = get_site_manager()

try:
    from config import GIT_INFO
except ImportError:
    GIT_INFO = None

from flask.ext.babel import Babel

# 注册所有可用的内置 worker
for module_name in WORKERS:
    importlib.import_module('workers.{}'.format(module_name))

DEBUG = True
FILEDIALOG = None
P2P_QUEUE = None

_ = translate

# 创建本地服务器
fapp = Flask(
    __name__,
    static_url_path='/static',
    static_folder=os.path.join(CURRENT_DIR, 'static'),
    template_folder=os.path.join(CURRENT_DIR, 'templates')
)
fapp.config['BABEL_DEFAULT_LOCALE'] = 'en'
# 不要将错误抛出到 wsgi 服务器去。否则 gevent 会将错误信息直接写入 stdout，丢失日志
fapp.config['PROPAGATE_EXCEPTIONS'] = False

# 注册所有的路由模块
for module_name in [
    'blueprint_admin', 'blueprint_worker'
]:
    fapp.register_blueprint(
        importlib.import_module('blueprints.{}'.format(module_name)).blueprint,
    )

# Set logger
fapp.debug = DEBUG
fhandler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DATA, 'webserver.log'),
    maxBytes=100 * 1024,
    backupCount=1
)
# 修改一下日志格式，方便调试
fhandler.setFormatter(logging.Formatter(
    '%(asctime)s (L%(lineno)d) %(threadName)s %(levelname)s %(message)s'
))
flask_debug_level = logging.DEBUG if fapp.debug else logging.WARN
fhandler.setLevel(flask_debug_level)
# 确保仅添加一个文件记录处理器
if not any([type(h) == type(fhandler) for h in fapp.logger.handlers]):
    fapp.logger.addHandler(fhandler)

fapp.PLATFORM_MAPPING = {
    'win32': 'windows',
    'linux': 'deb',
    'linux2': 'deb',
    'darwin': 'mac',
}
logger = fapp.logger

# Flask 国际化
babel = Babel(fapp)

QUESTIONS = {}


# 国际化
@babel.localeselector
def get_locale():
    return detect_locale()[-1]


@babel.timezoneselector
def get_timezone():
    # TODO is this really necessary?
    return {
        'zh': 'Asia/Beijing',  # GMT+8
        'zh-CN': 'Asia/Beijing',
        'zh_CN': 'Asia/Beijing',
        'en': 'EST',  # Eastern Shore Time
    }.get(get_locale(), None)


@fapp.route('/', methods=['GET', 'OPTIONS', ])
@addr_check
def index():
    return redirect(url_for('admin.view_worker_management'), code=302)


@fapp.route('/favicon.ico', methods=['GET', ])
def favicon():
    return '', 204


@fapp.route('/crossdomain.xml', methods=['GET', 'OPTIONS', ])
@addr_check
def crossdomain():
    return """<?xml version="1.0"?>
<!DOCTYPE cross-domain-policy SYSTEM "http://www.adobe.com/xml/dtds/cross-domain-policy.dtd">
    <cross-domain-policy>
        {}
    </cross-domain-policy>
    """.format("\n".join(map((lambda domain: """
        <allow-access-from domain="{domain}" secure="false"/>
        <allow-http-request-headers-from domain="{domain}" headers="*" secure="false"/>
        """.format(domain=domain)
        ), ALLOW_DOMAIN)
    ))


@jsonp
def now_upgrading():
    '''返回 正在升级 的 JSON 信息'''
    return json.dumps({
        'msg': _('New version required, upgrade started. Please try again after upgrading.'),
        'is_alive': False,
        'worker_id': 0
    })


@jsonp
def need_downgrade():
    '''返回 需要降级才能使用 的 JSON 信息'''
    return json.dumps({
        'msg': _('You need to downgrade to an old version to use Assistant on this site.'),
        'is_alive': False,
        'worker_id': 0,
    })


@fapp.before_request
def request_upgrade_hook():
    '''
    1. 检测站点连接是否过期，如果过期则自动刷新同一用户的过期连接。
    2. 从请求中判断并自动升级桌面助手
    '''
    oc_server, account, instance, pid, token = extract_data(
        ('oc_server', 'account', 'instance', 'pid', 'token'),
        request=request
    )
    site = site_manager.get_site(oc_server, account, instance)
    if site and site.pid == pid and token and site.is_token_invalid():
        # 自动更新同一用户的过期连接
        site.login(token)
        site_manager.save()
    # /about 接口在没有连接的时候不自动升级
    if request.path.startswith('/about') and not site:
        return
    # 静默模式下禁止自动升级
    if HEADLESS:
        return

    request_version, request_build, request_builds = extract_data(
        ('version', 'build_number', 'min_builds', ),
        request=request
    )
    UPGRADE_SCRIPT = 'zopen.assistant:ast_arch_upgrade'
    script_name = extract_data(['script_name'], request=request)
    upgrade_available = False
    downgrade_required = False
    # 禁用了自动升级
    if DISABLE_UPGRADE:
        request_build = request_version = '*'
    else:
        # 如果有所有平台的最新版本信息，从里面提取当前平台的会更准确
        if request_builds:
            try:
                request_builds = json.loads(request_builds)
            except:
                request_builds = {}
            request_build = request_builds.get(
                fapp.PLATFORM_MAPPING[sys.platform],
                request_build
            )

    if request_build == request_version == '*':
        pass
    else:
        try:
            request_build = int(request_build) or BUILD_NUMBER
        except:
            request_build = BUILD_NUMBER
        # 没有大版本号的认为是 3 版本
        try:
            request_version = int(request_version) or 3
        except:
            request_version = 3

        if request_version > VERSION:
            upgrade_available = True
        elif request_version == VERSION:
            if request_build > BUILD_NUMBER:
                upgrade_available = True
        else:
            # 需要手动降级才能使用
            downgrade_required = True
            upgrade_available = False

    worker_name = None
    creating_worker = request.path.startswith('/worker/new/')

    if downgrade_required and creating_worker:
        trayIcon.message(
            _('Assistant'),
            _('You need to downgrade to use Assistant on this site.'),
            type='info'
        )
        return need_downgrade()

    if upgrade_available:
        # 拦截所有任务的新建过程
        # 如果是新建升级任务，就不需要拦截
        if creating_worker:
            worker_name = request.path.replace('/worker/new/', '').strip()
            if worker_name == 'online_script' and script_name == UPGRADE_SCRIPT:
                return

        logger.info(
            u'"%s" 发现有可用的新版本，将开始自动升级 (%s.%s => %s.%s)',
            request.path, VERSION, BUILD_NUMBER, request_version, request_build
        )
        # 检查是否已经有升级任务在运行
        upgrading = False
        for work in worker.list_workers():
            if work['name'] == 'online_script' \
                    and work['detail'].get('script_name', None) == UPGRADE_SCRIPT \
                    and work['state'] in ('running', 'prepare', ):
                upgrading = True
                break

        # 如果没有，插入一个桌面助手的升级任务
        if not upgrading:
            kw = extract_data_list([], request=request)
            args = [
                kw.get(i, None)
                for i in ('server', 'account', 'instance', 'token')
            ]
            # 参数不足，不能触发升级任务
            if not all(args):
                return

            kw.update({
                'script_name': UPGRADE_SCRIPT,
                'args': json.dumps(args),
                'kw': '{}',
            })
            kw.pop('silent', None)
            kw.pop('auto', None)
            upgrade_worker_id = worker.new_worker('online_script', **kw)
            worker.start_worker(upgrade_worker_id)
            logger.debug(u'Upgrade hook 启动了 ID=%s 的升级任务', upgrade_worker_id)
        else:
            logger.debug(u'已经有自动升级任务在运行')

        # 对于创建新任务，返回升级提示信息，不开始任务
        # 对于除创建新任务之外的其他接口，还需要正常返回数据
        if creating_worker:
            trayIcon.message(
                _('Upgrade'),
                _('New version required, upgrade started. Please try again after upgrading.'),
                type='info'
            )
            return now_upgrading()


@fapp.after_request
def set_cookies(response):
    if getattr(flask_g, 'cookies', None) is not None:
        now = time.time()
        for name, cookie in flask_g.cookies.items():
            response.set_cookie(
                name, cookie[0], max_age=cookie[1], expires=(now + cookie[1])
            )
    return response


@fapp.route('/about', methods=['POST', 'GET', 'OPTIONS', ])
@jsonp
def api_about():
    '''
    获取桌面助手和用户计算机的一些信息
    '''
    info = {
        'app_name': _('Assistant'),
        'app_version': VERSION,  # 大版本号（大版本不同则有重要接口不兼容）
        'app_build': BUILD_NUMBER,  # 小版本号（自增数字，用于升级条件的判断）
        'os_platform': sys.platform,  # 平台信息
        # 是否有这个站点的消息提醒任务
        # None: 没有消息提醒相关信息, False: 用户关闭了消息提醒, True: 消息提醒已经打开
        'messaging': False,
        'disable_upgrade': bool(DISABLE_UPGRADE),  # 用户是否禁用升级
        'sync_build_number': get_sync_assistant_build_number(),  # 同步助手版本号
        'sync_system_info': get_sync_system_info(),  # 同步助手的平台信息
        'local_user': getpass.getuser().decode(sys.getfilesystemencoding()),  # 当前桌面助手运行的本地用户
        # 'local_user': 'test',  # debugging
    }
    # 获取站点连接相关参数，判断站点连接是否存在
    oc_server, account, instance = extract_data(
        ['oc_server', 'account', 'instance'],
        request=request
    )

    # 没有连接的站点，相当于禁用自动升级
    site = site_manager.get_site(oc_server, account, instance)
    if not site:
        info['disable_upgrade'] = True
        info['connection'] = False
        info['messaging'] = False
    else:
        info['connection'] = True
        info['messaging'] = site.get_config("notification")

    # 映射盘驱动的安装情况
    if sys.platform == 'win32':  # 现在只支持 Windows 系统
        from utils.win32_utils import is_dokan_installed
        info['webfolder_driver'] = is_dokan_installed()
    return json.dumps(info)


@fapp.route('/quit', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
def api_quit():
    '''
    （因升级等原因）退出桌面助手
    '''
    ready, reason = worker.ready_to_quit()
    if ready:
        # Must call here in the request context
        if not trayIcon.quit():
            return json.dumps({'success': False, 'msg': reason})
        fapp.LOCKS = {}  # maybe uncecessary because we're quiting...
        return json.dumps({'success': True, 'msg': reason})
    else:
        return json.dumps({'success': False, 'msg': reason})


@fapp.route('/ready_to_quit', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
def api_ready_to_quit():
    '''
    （因升级等原因）退出桌面助手
    '''
    ready, reason = worker.ready_to_quit()
    return json.dumps({
        'ready': ready,
        'msg': reason,
    })


@fapp.route('/call_script_sync', methods=['POST', 'GET', 'OPTIONS'])
@addr_check
@jsonp
def api_call_script_sync():
    '''同步调用本地脚本或联机脚本，将结果以 HTTP 响应方式返回'''
    # Extract parameters
    local_script = extract_data('local', request=request)
    if local_script:
        if not is_internal_call(request):
            # 调用本地脚本只允许内部调用
            return json.dumps({
                'success': False,
                'result': "This is not an internal request."
            })
        script_name, args, kw = extract_data(
            ('script_name', 'args', 'kw'), request=request
        )
        call_script = call_local_script
        parameters = {'name': script_name, 'args': args, 'kwargs': kw}
    else:
        from workers.online_script import online_script as call_script
        parameters = {}
        keywords = (
            'oc_server', 'account', 'instance', 'token',  # 启动任务相关参数
            'script_name', 'args', 'kw'  # 脚本运行相关参数
        )
        values = list(extract_data(keywords, request=request))
        for key, value in zip(keywords, values):
            parameters.update({key: value})
        parameters.update({'worker_id': None, '__sync': True})

    try:
        return json.dumps({
            'success': True,
            'result': json.dumps(call_script(**parameters)),
        })
    except:
        logger.exception("Call failed")
        return json.dumps({
            'success': False,
            'traceback': traceback.format_exc(),
        })



def start_server(ui=True):
    if os.environ.get('APP_TOKEN'):
        fapp.logger.debug(u'设置了远程访问 token')

    http_server = gevent.wsgi.WSGIServer(
        (config.BIND_ADDRESS, config.HTTP_PORT),
        fapp
    )
    https_server = gevent.wsgi.WSGIServer(
        (config.BIND_ADDRESS, config.HTTPS_PORT),
        fapp,
        keyfile=os.path.join(config.APP_DATA, 'certifi', 'assistant.key'),
        certfile=os.path.join(config.APP_DATA, 'certifi', 'assistant.crt')
    )
    http_greenlet = gevent.spawn(http_server.serve_forever)
    https_greenlet = gevent.spawn(https_server.serve_forever) if https_server else None

    app = None
    window = None

    global trayIcon
    trayIcon = TrayIconMixin()
    from utils import console_message
    console_message(
        unicode(_('Assistant')), unicode(_('Assistant launched'))
    )

    # clear the database
    clear_old_files()

    # Store a reference into flask g object, so we can access it in blueprint
    fapp.trayIcon = trayIcon
    global P2P_QUEUE
    fapp.P2P_QUEUE = P2P_QUEUE = Queue()
    fapp.LOCKS = {}

    import worker
    result = worker.load_workers()
    if result:
        from utils import console_message
        console_message(
            unicode(_('Task recovery')), unicode(_('Tasks recovered'))
        )

    qt_greenlet = None

    def kill_all():
        http_greenlet.kill(block=False)
        if qt_greenlet is not None:
            qt_greenlet.kill(block=False)
        if https_greenlet is not None:
            https_greenlet.kill(block=False)
        logger.debug(u'将要退出桌面助手')

    trayIcon.message(_('Assistant'), _('Assistant has started'))
    trayIcon.add_quit_callback(kill_all)

    greenlets = [
        i for i in (http_greenlet, https_greenlet, qt_greenlet)
        if i is not None
    ]

    # start
    gevent.joinall(greenlets)
