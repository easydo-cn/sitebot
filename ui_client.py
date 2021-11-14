# encoding: utf-8
'''桌面助手 UI 客户端'''

import json
import os
import sys
import subprocess
import urllib
import webbrowser
from threading import Timer
import time

import requests
from requests.adapters import HTTPAdapter

import config
from config import (
    APP_ID, VERSION, BUILD_NUMBER
)
from utils.decorators import ui_api
from errors import LockAcquireFailure, LockAcquireTimeout, LockReleaseFailure
# TODO Move translations into webserver
from utils import (
    translate as _, extract_traceback, reverse_lookup_path_in_webfolder
)


API_REQUEST_SUPPRESSED = False


def _request_api(api, kw=None, internal=False, timeout=2):
    '''
    Send data from kw to target API through internal address and port
    '''
    if API_REQUEST_SUPPRESSED:
        return

    api = api if api.startswith('/') else '/{}'.format(api)
    headers = {}
    if (api.startswith('/ui')):
        return
    api_url = '{}{}'.format(config.INTERNAL_URL, api)
    if internal:
        headers.update({'caller': APP_ID[:12]})

    session = requests.Session()
    session.trust_env = False  # 不要从环境变量中读取代理设置
    session.mount('http://', HTTPAdapter(max_retries=0))
    return session.post(
        api_url,
        kw or {},
        headers=headers,
        timeout=timeout,
        proxies={
            'http': None,  # 对所有HTTP请求不使用代理设置
        }
    )


def message(title, body, type='none'):
    try:
        _request_api(
            'ui/message',
            {'title': title, 'body': body, 'type': type}
        )
    except:
        pass



def start_worker(id):
    '''
    开始一个任务
    '''
    return _request_api('worker/start', {'worker_id': id})


def worker_state(id):
    '''查询 worker 状态'''
    return _request_api('worker/state', {'worker_id': id})


def new_worker(params):
    '''
    新建一个任务
    params <Dict> 任务参数
    '''
    worker_name = params.pop('name', None)
    if worker_name is None:
        return
    internal = worker_name == 'script'
    params.update({
        'version': VERSION,
        'build_number': BUILD_NUMBER,
    })
    return _request_api('worker/new/{}'.format(worker_name), params, internal)


def acquire_lock(name, description=None, timeout=0, worker_id=None):
    if worker_id is None:
        raise ValueError(u'Worker not identified')
    _time_remain = timeout
    while 1:
        try:
            resp = _request_api(
                '/worker/lock/acquire',
                kw={
                    'worker_id': worker_id,
                    'name': name,
                    'description': description,
                },
                internal=False
            ).json()
            # This will be caught
            if not resp.get('success', False):
                raise LockAcquireFailure(worker_id, name, timeout)
            else:
                return True
        except:
            if _time_remain <= 0:
                if timeout == 0:
                    raise LockAcquireFailure(worker_id, name, timeout)
                else:
                    raise LockAcquireTimeout(worker_id, name, timeout)
            else:
                _time_remain -= 1
                time.sleep(1)
                continue


def release_lock(name, worker_id=None):
    if worker_id is None:
        raise ValueError(u'Worker not identified')
    try:
        resp = _request_api(
            '/worker/lock/release',
            kw={'worker_id': worker_id, 'name': name,},
            internal=False,
            timeout=2
        ).json()
        if not resp.get('success', False):
            raise LockReleaseFailure(worker_id, name)
        else:
            return True
    except:
        raise LockReleaseFailure(worker_id, name)

