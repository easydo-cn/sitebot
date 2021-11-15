# encoding: utf-8
import getpass
import importlib
import os
import sys
import json
import logging
import logging.handlers
import time

from flask import Flask, request, redirect, url_for, g as flask_g
import gevent.wsgi

import worker
from utils import (
    translate, addr_check, jsonp, extract_data
)
import config
from config import (
    BUILD_NUMBER, VERSION, ALLOW_DOMAIN, HTTP_PORT,
    HTTPS_PORT, BIND_ADDRESS, CURRENT_DIR, DISABLE_UPGRADE,
    LOG_DATA, APP_DATA
)

try:
    from config import GIT_INFO
except ImportError:
    GIT_INFO = None

import flask_babel
import flask_babel._compat  # noqa
from flask.ext.babel import Babel

try:
    # 修补Connection，简化调用
    from fabric2 import Connection
    def winrun(self, *command, **kwargs):
        commands = ' & '.join(command)
        command = r'cmd.exe /c "{}"'.format(commands)
        return self._run(self._remote_runner(), command, pty=True, echo=True, hide=False, **kwargs)
    Connection.winrun = winrun
except ImportError:
    pass

# 注册所有可用的内置 worker
allow_workers = ["online_script", "script"]
for module_name in allow_workers:
    importlib.import_module('workers.{}'.format(module_name))

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
for module_name in ['blueprint_admin', 'blueprint_worker']:
    fapp.register_blueprint(
        importlib.import_module('blueprints.{}'.format(module_name)).blueprint,
    )

# Set logger
fapp.debug = DEBUG = True
fhandler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DATA, 'webserver.log'),
    maxBytes=100*1024,
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

logger = fapp.logger
http_greenlet = https_greenlet = None

# Flask 国际化
babel = Babel(fapp)


# 国际化
@babel.localeselector
def get_locale():
    return request.accept_languages.best_match([
        'zh_CN',
        'zh_TW',
        'en',
    ])


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

@fapp.before_request
def request_hook():
    flask_g.headless = True

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
    from libs.managers import get_site_manager
    info = {
        'app_name': _('Assistant'),
        'app_version': VERSION,  # 大版本号（大版本不同则有重要接口不兼容）
        'app_build': BUILD_NUMBER,  # 小版本号（自增数字，用于升级条件的判断）
        'os_platform': sys.platform,  # 平台信息
        # 是否有这个站点的消息提醒任务
        # None: 没有消息提醒相关信息, False: 用户关闭了消息提醒, True: 消息提醒已经打开
        'messaging': False,
        'disable_upgrade': bool(DISABLE_UPGRADE),  # 用户是否禁用升级
        'local_user': getpass.getuser().decode(sys.getfilesystemencoding()),  # 当前桌面助手运行的本地用户
        # 'local_user': 'test',  # debugging
    }
    # 获取站点连接相关参数，判断站点连接是否存在
    oc_server, account, instance = extract_data(
        ['oc_server', 'account', 'instance'],
        request=request
    )
    # 没有连接的站点，相当于禁用自动升级
    if not get_site_manager().get_site(oc_server, account, instance):
        info['disable_upgrade'] = True
    return json.dumps(info)


@fapp.route('/quit', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
def api_quit():
    '''
    （因升级等原因）退出桌面助手
    '''
    global http_greenlet, https_greenlet
    if http_greenlet:
        http_greenlet.kill(block=False)
    if https_greenlet:
        https_greenlet.kill(block=False)
    return json.dumps({'success': True})


def start_server():
    reload(sys)
    sys.setdefaultencoding("utf-8")
    config.WORKERS = allow_workers

    if os.getenv('APP_TOKEN'):
        fapp.logger.debug(u'设置了远程访问 token')

    http_server = gevent.wsgi.WSGIServer(
        (BIND_ADDRESS, HTTP_PORT),
        fapp
    )
    https_server = gevent.wsgi.WSGIServer(
        (BIND_ADDRESS, HTTPS_PORT),
        fapp,
        keyfile=os.path.join(APP_DATA, 'certifi', 'assistant.key'),
        certfile=os.path.join(APP_DATA, 'certifi', 'assistant.crt'),
    )
    fapp.LOCKS = {}

    global http_greenlet, https_greenlet
    http_greenlet = gevent.spawn(http_server.serve_forever)
    https_greenlet = gevent.spawn(https_server.serve_forever)

    if worker.load_workers():
        from utils import console_message
        console_message(
            unicode(_('Task recovery')), unicode(_('Tasks recovered'))
        )

    gevent.joinall([http_greenlet, https_greenlet])
