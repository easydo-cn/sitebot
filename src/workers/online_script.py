# coding: utf-8
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

import json
import logging
import os
import shlex
import signal
import sys
import threading
import time
import traceback
from functools import partial
from invoke.exceptions import UnexpectedExit
from requests import post
import edo_client
import ui_client
from edo_fabric import get_host
from edo_engine import RemoteScriptingEngine
from libs.progress_log_handler import ProgressLogHandler
from worker import register_worker, get_worker_db
import utils
from utils import (
    translate as _, get_message_client,  # get_site_public_key,
    get_logger, detect_locale
)
from config import ADDON_DIR, APP_KEY, APP_SECRET
from errors import (
    LockAcquireTimeout, LockAcquireFailure, ScriptDownloadError,
    Retry, ScriptSecurityError,
)

# Add ADDON_DIR to python path
sys.path.insert(0, ADDON_DIR)

# We'll patch `sys.moddules` on Windows platform to bypass a rare importer bug
# Please ref to https://github.com/pyinstaller/pyinstaller/issues/1803


# 修补标准库的 Popen，使其支持 timeout
from subprocess32 import Popen, PIPE, TimeoutExpired
# 修改自Python3.shlex.quote

def quote(s):
    """Return a shell-escaped version of the string *s*."""
    # 防止参数不是字符串
    s = str(s)
    if not s:
        return u"''"
    # use single quotes, and put single quotes into double quotes
    # the string $'b is then quoted as '$'"'"'b'
    return u"'" + s.replace(u"'", u"'\"'\"'") + u"'"

PREEXEC_FN = os.setsid

def kill_with_children(pid):
    '''杀死指定进程及其所有子进程'''
    os.killpg(pid, signal.SIGKILL)


def safe_call(cmd, shell=False, comment=None, timeout=10 * 60, **kw):
    '''
    安全地调用一个外部程序，超过指定时间自动杀死
    Return: (return_code, stdout, stderr)
    '''
    if not shell:
        cmd = shlex.split(cmd)
        popen = Popen(
            cmd, shell=shell, stdout=PIPE, stderr=PIPE,
            preexec_fn=PREEXEC_FN, **kw
        )
    else:
        executable = '/bin/bash'
        popen = Popen(
            cmd, shell=shell, stdout=PIPE, stderr=PIPE,
            executable=executable, preexec_fn=PREEXEC_FN, **kw
        )
    try:
        timeout = int(timeout)
    except:
        timeout = 10 * 60

    try:
        out, err = popen.communicate(timeout=timeout)
    except TimeoutExpired:
        kill_with_children(popen.pid)
        code = 137  # as returned by `kill -9`
        out = None
        err = 'Process timed out after {} seconds'.format(timeout)
    else:
        code = popen.poll()

    return code, out, err


# Script runtime environment
SCRIPT_ENV = {
    'ui_client': ui_client,
    'utils': utils,
    'edo_client': edo_client,
    'Popen': Popen,
    'PIPE': PIPE,
    'TimeoutExpired': TimeoutExpired,
    'quote': quote,
    'safe_call': safe_call,
    'LockAcquireFailure': LockAcquireFailure,
    'LockAcquireTimeout': LockAcquireTimeout,
    'Retry': Retry,
    'ScriptDownloadError': ScriptDownloadError,
    'UnexpectedExit': UnexpectedExit,
}


@register_worker
def online_script(
    worker_id,
    oc_server, account, instance, token,
    script_name, args, kw,
    error_callback_url=None, error_script=None, error_params=None,
    callback_url=None, return_script=None, return_params=None,
    progress_script=None, progress_params=None, progress_level=None, timeout=0,
    __sync=False, pipe=None,
):
    '''Script task'''
    _('Script task')

    # Notice:
    # __sync 是内部参数，用于区分是否是同步联机脚本调用
    # 同步联机脚本调用有更严格的限制：
    # - 不能存取 worker_db
    # - 调用 ui_client 接口将会卡死（目前实现的限制，之后可能会开放）
    # - 调用站点机器人 HTTP API 将会卡死（目前实现的限制，之后可能会开放）

    wo_client = edo_client.get_client(
        'workonline', oc_server, account, instance,
        token=token, client_id=APP_KEY, client_secret=APP_SECRET
    )

    # 初始化一些对象
    logger = utils.get_worker_logger(worker_id)

    args = json.loads(args) if args else []
    kw = json.loads(kw) if kw else {}

    remote_log = None
    if progress_script and progress_params:
        progress_params = json.loads(progress_params)
        progress_level = json.loads(progress_level)
        progress_log_handler = ProgressLogHandler(
            wo_client=wo_client,
            progress_script=progress_script,
            script_title=kw.get('script_title', ''),
            progress_params=progress_params,
            logger=logger,
            )
        # 如果只有一个级别则为这个级别，多个级别则取最低级别，默认为info级别
        # 兼容流程中直接指定level: 'info', 开发调试时选择选项数组
        level = ''
        if isinstance(progress_level, (str, unicode)):
            level = progress_level.upper()
        elif progress_level:
            # 遍历level，转成int，用sorted排序，取最低level。对于不存在的level都认为是info。
            level = sorted(
                map(lambda l: dict(DEBUG=logging.DEBUG, INFO=logging.INFO, ERROR=logging.ERROR).get(l.upper(), 'INFO'),
                    progress_level))[0]
        else:
            level = 'INFO'
        progress_log_handler.setLevel(level)
        remote_log = get_logger('RemoteHost:' + str(threading.currentThread().ident))
        remote_log.handlers = []
        remote_log.addHandler(progress_log_handler)
        logger.addHandler(progress_log_handler)

    worker_db = get_worker_db(worker_id)

    # 为了避免污染公用的脚本执行环境，为每一任务提供一继承于SCRIPT_ENV的环境
    script_env = SCRIPT_ENV.copy()
    # 获取远端脚本执行引擎。
    rse = RemoteScriptingEngine(wo_client, script_env, False) # 站点机器人不需要对脚本签名校验
    # 手动加载脚本以便在脚本执行前获取脚本信息。
    script_obj = rse.load_script(script_name)

    worker_db['title'] = script_obj['title'] 
    worker_db.sync()

    # 快速构造消息客户端
    if not __sync:
        if worker_db.get('message_server', None):
            message_client = get_message_client(
                token, worker_db['message_server'], account, instance
            )
        else:
            # 在线构造消息客户端，会慢一点
            try:
                message_client = edo_client.get_client(
                    'message', oc_server, account, instance,
                    token=token, client_id=APP_KEY, client_secret=APP_SECRET
                )
            except:
                message_client = None

    i18n_content = {}

    def load_i18n(package_name, script_name_json, default_lang="zh_CN"):
        '''
        下载指定的翻译文件，并加载其内容到 i18n_content 中
        如果下载失败，则使用 default_lang 指定的翻译文件
        <Args>
            package_name <String> 软件包的名字
            script_name_json <String> 要翻译的脚本对应的 json 文件
            default_lang <String> 可选：在找不到对应 json 文件时加载的翻译文件
        '''
        system_lang = detect_locale()[0]
        json_path = 'i18n/{lang}/{name}'.format(
            lang=system_lang, name=script_name_json
        )
        default_path = 'i18n/{lang}/{name}'.format(
            lang=default_lang, name=script_name_json
        )

        try:
            json_content = wo_client.package.get_resource(
                package_name=package_name,
                res_path=json_path,
            ).json()
        except edo_client.error.ApiError:
            try:
                json_content = wo_client.package.get_resource(
                    package_name=package_name,
                    res_path=default_path,
                ).json()
            except edo_client.error.ApiError:
                json_content = {}

        i18n_content.update(**json_content)

    def custom_translate(string, default=None):
        '''
        获取翻译后的字符串，如果获取不到，则使用 default 的值
        <Args>
            string <String> 要翻译的字符串
            default <String> 翻译的字符串没有对应值的情况使用的字符串
        <Return>
            <String> 翻译后的字符串
        '''
        return i18n_content.get(string, None) or default or string

    # 在非同步情况下为脚本运行添加的全局运行环境
    if not __sync:
        def report(text):
            '''回报指定的文本'''
            if not isinstance(text, unicode):
                text = unicode(text)

            logger.info(u'尝试回报信息: %s', text)
            username = worker_db.get('username', None)
            user_id = worker_db.get('pid', None)
            to_user = worker_db.get('report_to_pid', None)
            if not all([username, user_id, to_user, message_client, ]):
                return
            try:
                message_client.message_v2.trigger_notify_event(
                    'default',
                    event_name='notify', event_data={
                        'from': {'name': username, 'id': user_id},
                        'to': [to_user, ],
                        'body': text,
                    }
                )
            except:
                logger.warn(u'回报运行错误信息时出错，发送的文字: %s', text, exc_info=True)

        def acquire_lock(name, description=None, timeout=0):
            '''获取锁'''
            return ui_client.acquire_lock(
                name, description=description, timeout=timeout, worker_id=worker_id
            )

        def release_lock(name):
            '''解锁'''
            return ui_client.release_lock(name, worker_id=worker_id)

        rse.script_exec_env.update({
            'worker_db': worker_db,
            # 'get_remote_host': partial(get_remote_host, __worker_db=worker_db),
            'acquire_lock': acquire_lock,
            'release_lock': release_lock,
            'report': report
        })

    rse.script_exec_env.update({
    	'RSE_NAME': 'bot',
        'logger': logger,
        'wo_client': wo_client,
        'get_host': partial(get_host,
                                   __worker_db=worker_db,
                                   __logger=remote_log,
                                   __package_versions=kw['package_versions_']),
        'load_i18n': load_i18n,
        '_': custom_translate,
    })
    rse.script_exec_env['get_remote_host'] = rse.script_exec_env['get_host']

    try:
        result = rse.call(script_name, *args, **kw)
    except ScriptSecurityError:
        if __sync:
            raise ScriptSecurityError(script_name)
        else:
            ui_client.message(
                _('Script task'),
                _("{} has been blocked from running because it's not signed.").format(
                    script_obj.get('title', script_name)
                ),
                type='warn'
            )
            return []
    except Exception:
        logger.exception(u'运行自定义任务时出错')
        raw_traceback = sys.exc_info()
        use_raw = False

        # 同步运行的情况下，直接出错
        if __sync:
            raise

        # 如果有错误回调地址，POST traceback
        if error_callback_url or (error_script and error_params):
            try:
                if error_script and error_params:
                    error_params = json.loads(error_params)
                    logger.debug(u'使用新版本错误回调脚本：%s', error_script)
                    error_result = wo_client.xapi(
                        error_script,
                        script_title=kw.get('script_title', ''),
                        traceback=traceback.format_exc(),
                        **error_params
                    )
                    logger.debug(error_result)
                    if error_result.get('errcode') != 0:
                        raise Exception(u'出错回调出错，出错原因：{}'.format(error_result.get('errmsg', '')))
                else:
                    logger.debug(u'POST 错误回调地址: %s', error_callback_url)
                    resp = post(
                        error_callback_url,
                        data={
                            'traceback': traceback.format_exc(),
                        }
                    )
            except:
                logger.exception(u'回调出错')
            else:
                if not error_script and error_params:
                    logger.info(u'错误回调响应状态码 %s，响应内容:\n%s', resp.status_code, resp.content)
        else:
            try:
                traceback_info = traceback.format_exc()
                traceback_info.decode('utf-8')
            except UnicodeDecodeError:
                traceback_info = traceback_info.decode(sys.getfilesystemencoding())
                use_raw = True

            text = _(
                'Script task #{} failed, script name: {}, '
                'args: {}, kw: {}\ntraceback information:\n{}'
            ).format(
                worker_id, script_name,
                ', '.join([json.dumps(a) for a in args]) or _('None'),
                ', '.join(['='.join([k, json.dumps(v)]) for k, v in kw.items()]) or _('None'),
                traceback_info
            )
            report(text)
        if not use_raw:
            raise
        else:
            raise raw_traceback[0], raw_traceback[1], raw_traceback[2]
    else:
        logger.info(u'Result is: %s', result)
        if __sync:
            return result

        if callback_url or (return_script and return_params):
            try:
                if return_script and return_params:
                    return_params = json.loads(return_params)
                    logger.debug(u'使用新版本回调脚本：%s', return_script)
                    return_result = wo_client.xapi(
                        return_script,
                        script_title=kw.get('script_title', ''),
                        result=json.dumps(result),
                        **return_params
                    )
                    logger.info(return_result)
                    if return_result.get('errcode') != 0:
                        raise Exception(u'成功回调出错，出错原因：{}'.format(return_result.get('errmsg', '')))
                else:
                    logger.debug(u'回调地址: %s', callback_url)
                    resp = post(
                        callback_url,
                        data={
                            'result': json.dumps(result),
                        }
                    )
            except:
                logger.exception(u'回调出错')
            else:
                if not return_script and return_params:
                    logger.info(u'回调响应状态码 %s，响应内容:\n%s', resp.status_code, resp.content)
        return []

    finally:
        if not __sync:
            worker_db = get_worker_db(worker_id)
            worker_db.sync()

