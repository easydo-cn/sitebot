# encoding: utf-8
"""
/*
 * Copyright (c) 2019 EasyDo, Inc. <panjunyong@easydo.cn>
 *
 * This program is free software: you can use, redistribute, and/or modify
 * it under the terms of the GNU Affero General Public License, version 3
 * or later ("AGPL"), as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */
"""

import getpass
import importlib
import os
import sys
import json
import logging
import logging.handlers
import time
import traceback

from flask import Flask, request, redirect, url_for, g as flask_g
import gevent.wsgi

import worker
from utils import (
    translate, addr_check, jsonp, extract_data, extract_data_list
)
import config
from config import (
    BUILD_NUMBER, VERSION, ALLOW_DOMAIN, HTTP_PORT,
    HTTPS_PORT, BIND_ADDRESS, CURRENT_DIR, LOG_DATA, APP_DATA
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
allow_workers = ["online_script"]
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
    获取站点机器人和用户计算机的一些信息
    '''
    from libs.managers import get_site_manager
    info = {
        'app_name': _('Sitebot'),
        'app_version': VERSION,  # 大版本号（大版本不同则有重要接口不兼容）
        'app_build': BUILD_NUMBER,  # 小版本号（自增数字，用于升级条件的判断）
        'os_platform': sys.platform,  # 平台信息
        # 是否有这个站点的消息提醒任务
        # None: 没有消息提醒相关信息, False: 用户关闭了消息提醒, True: 消息提醒已经打开
        'messaging': False,
        'disable_upgrade': True,  # 用户是否禁用升级
        'local_user': getpass.getuser().decode(sys.getfilesystemencoding()),  # 当前站点机器人运行的本地用户
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
    （因升级等原因）退出站点机器人
    '''
    global http_greenlet, https_greenlet
    if http_greenlet:
        http_greenlet.kill(block=False)
    if https_greenlet:
        https_greenlet.kill(block=False)
    return json.dumps({'success': True})


@fapp.route('/call_script_async', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
@jsonp
def api_call_script_async():
    '''新建一个任务
    '''
    # 从请求中取出任务的所有参数；其中这些参数的类型是 list
    kw = extract_data_list(
        ('uid', 'path', 'server_path', 'revisions', ),
        request=request,
    )
    return worker.run_online_script(**kw)


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
        #from workers.online_script import online_script as call_script
        parameters = {}
        keywords = (
            'oc_server', 'account', 'instance', 'token',  # 启动任务相关参数
            'script_name', 'args', 'kw'  # 脚本运行相关参数
        )
        values = list(extract_data(keywords, request=request))
        for key, value in zip(keywords, values):
            parameters.update({key: value})
        parameters.update({'__sync': True})
        from worker import start_sync_worker
        result = start_sync_worker('online_script', **parameters)
    try:
        return json.dumps({
            'success': True,
            'result': result,
        })
    except:
        logger.exception("Call failed")
        return json.dumps({
            'success': False,
            'traceback': traceback.format_exc(),
        })


def start_server():
    reload(sys)
    sys.setdefaultencoding("utf-8")
    config.WORKERS = allow_workers

    if os.getenv('MANAGER_TOKEN'):
        fapp.logger.debug(u'设置了远程访问 token %s', os.getenv('MANAGER_TOKEN'))

    http_server = gevent.wsgi.WSGIServer(
        (BIND_ADDRESS, HTTP_PORT),
        fapp
    )
    https_server = gevent.wsgi.WSGIServer(
        (BIND_ADDRESS, HTTPS_PORT),
        fapp,
        keyfile=os.path.join(APP_DATA, 'certifi', 'sitebot.key'),
        certfile=os.path.join(APP_DATA, 'certifi', 'sitebot.crt'),
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
