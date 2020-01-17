# coding: utf-8
from base64 import b64decode
import json
import sys

from requests import get, post
import edo_client
import ui_client
from worker import register_worker, get_worker_db
import utils
from utils import (
    translate as _, get_message_client, extract_traceback,
    get_site_public_key,
)
from libs import verify
from config import FROZEN, ADDON_DIR

# Add ADDON_DIR to python path
sys.path.insert(0, ADDON_DIR)


def make_function(func_name, script, variables=None):
    if variables:
        args = '{}, **kw'.format(variables)
    else:
        args = '**kw'
    lines = []
    in_multi_line_str = False
    for line in script.splitlines():
        if in_multi_line_str:
            lines.append(line)
        else:
            lines.append('    ' + line)
        if len(line.split("'''")) == 2 or len(line.split('"""')) == 2:
            in_multi_line_str = not in_multi_line_str
    return 'def {}({}):\n{}'.format(func_name, args, '\n'.join(lines))

SCRIPT_ENV = {
    'ui_client': ui_client,
    'utils': utils,
    'edo_client': edo_client,
}

def script(
    worker_id,
    token, account, instance, server, signature,
    message_server=None, oc_server=None,
    script_url=None, script_content=None,
    report_to_pid=None, callback_url=None,
    script_vars=None, args=None, kw=None,
    pid=None, username=None,
    **kwargs
):
    '''Script task'''
    _('Script task')
    # Prepare parameters
    logger = utils.get_worker_logger(worker_id)
    worker_db = get_worker_db(worker_id)
    message_client = get_message_client(
        token, message_server, account, instance
    )

    def report(text):
        logger.info(u'回报信息: %s', text)
        try:
            message_client.message_v2.trigger_notify_event(
                'default',
                event_name='notify', event_data={
                    'from': {
                        'name': username,
                        'id': pid,
                    },
                    'to': [report_to_pid, ],
                    'body': text,
                }
            )
        except:
            logger.warn(u'回报运行错误信息时出错，发送的文字: %s', text, exc_info=True)

    if not any([script_url, script_content]):
        # Notify `report_to_pid` user and raise error
        report(
            '{}: script_url or script_content'.format(
                _('Missing parameter')
            )
        )
        raise RuntimeError(u'缺少参数: script_url 或 script_content')

    if not script_content:
        try:
            script_content = get(script_url).content
        except:
            logger.error(
                u'下载自定义任务脚本时出错',
                exc_info=True
            )
            raise

    public_key = get_site_public_key(server, account, instance, token=token)
    logger.debug(u'开始验证脚本安全性')
    if not verify(public_key, script_content, b64decode(signature)):
        logger.error(u'指令签名验证不通过，站点公钥:\n%s', public_key)
        raise RuntimeError(u'指令签名验证失败')
    logger.info(u'指令签名验证通过')

    try:
        func_name = 'run_script'
        source = make_function(
            func_name, script_content, script_vars
        )
        logger.debug(u'Script function is:\n%s', source)
        code = compile(source, '<script>', 'exec')
        SCRIPT_ENV.update({
            'worker_db': worker_db,
            'report': report,
            'message_client': message_client,
        })
        exec(code) in SCRIPT_ENV
        result = SCRIPT_ENV[func_name](*(args or []), **(kw or {}))
    except:
        logger.error(
            u'运行自定义任务时出错', exc_info=True
        )
        text = _('Script task #{} failed, traceback information:\n{}')
        report(text.format(worker_id, extract_traceback()))
        raise
    else:
        if not FROZEN:
            logger.info(u'Result is: %s', result)
        if callback_url:
            try:
                response = post(callback_url, data={
                    'access_token': token,
                    'result': result,
                })
                logger.debug(response.content)
            except:
                logger.error(u'回报脚本任务运行结果时出错', exc_info=True)
        return []

register_worker(script)
