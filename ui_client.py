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
    APP_ID, VERSION, BUILD_NUMBER, HEADLESS, SINGLE_PROCESS,
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
    if SINGLE_PROCESS or (HEADLESS and api.startswith('/ui')):
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


def quit_assistant():
    try:
        global API_REQUEST_SUPPRESSED
        API_REQUEST_SUPPRESSED = _request_api('quit').json().get('success', False)
    except Exception:
        pass


def ready_to_quit():
    NetworkError = (
        requests.exceptions.HTTPError,
        requests.exceptions.Timeout
    )
    try:
        return _request_api('ready_to_quit').json().get('ready', False)
    except NetworkError as e:
        print "Capture %s" % str(e)
        return False


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


@ui_api
def open_path(path):
    '''
    打开文件或文件夹
    '''
    # TODO implement `reveal items in explorer/Finder`
    # to show items in folder rather than just open that folder.
    if sys.platform == 'win32':
        try:
            if os.path.isfile(path):
                os.startfile(path)
            elif os.path.isdir(path):
                subprocess.Popen(
                    u'explorer "{}"'.format(
                        path
                    ).encode(sys.getfilesystemencoding())  # Windows path encoding hack
                )
        except WindowsError as e:
            # Detailed WindowsError ref: https://msdn.microsoft.com/en-us/library/windows/desktop/ms681381(v=vs.85).aspx
            # WindowsError 1155 is for 'no file extension association'
            # WindowsError(1155, '') will have `.errno` set to 22, and `.winerror` set to 1155
            if e.winerror in (1155, -2147221003, ):
                message(
                    _('Unable to open file'),
                    _(
                        'No program could open files with extension of {}'
                    ).format(os.path.basename(path)),
                    'info'
                )
                # Open folder instead
                return open_path(os.path.dirname(path))
            else:
                raise
    elif sys.platform == 'darwin':
        subprocess.call(['open', path])
    elif sys.platform.startswith('linux'):
        subprocess.call(['xdg-open', path])
    else:
        raise NotImplementedError(u'不支持的操作系统: {}'.format(sys.platform))


@ui_api
def show_in_folder(fpath):
    if not os.path.exists(os.path.dirname(fpath)):
        return

    fpath = reverse_lookup_path_in_webfolder(fpath)

    if sys.platform == 'win32':
        try:
            sfpath = unicode(fpath).encode(sys.getfilesystemencoding())
            subprocess.call(['explorer', '/select,', sfpath])
        except:
            open_path(os.path.dirname(fpath))
    else:
        # Not properly implemented on other platforms, just open the folder
        try:
            open_path(os.path.dirname(fpath))
        except:
            pass


def tool_tip(text=''):
    '''Wrapper to internal API /ui/tool_tip'''
    return _request_api('ui/tool_tip', {'text': text}, internal=True)


def set_icon(icon='default'):
    '''Wrapper to internal API /ui/icon'''
    try:
        return _request_api('ui/icon', {'name': icon}, internal=True)
    except Exception:
        return {'success': False}


@ui_api
def render_linux_update(info):
    '''
    渲染 Linux 更新提示
    '''
    [info.pop(k, None) for k in info.keys() if k not in ('deb', 'rpm', )]
    webbrowser.open(
        '{}/admin/linux_update?info={}'.format(
            config.INTERNAL_URL,
            urllib.quote(json.dumps(info))
        )
    )
    return []


@ui_api
def open_url(url):
    webbrowser.open(url)


def question_start(title, message, buttons=None):
    return _request_api(
        '/v2/ui/question/start',
        {
            'title': title,
            'text': message,
            'buttons': json.dumps(buttons) if buttons else None,
        },
        True
    ).json()


def question_status(msgbox_id):
    return _request_api(
        '/v2/ui/question/status',
        {
            'id': msgbox_id,
        },
        True
    ).json()


def update_progress(
    worker_id, direction='up',
    filename=None, size=0, progress=None, status=None, error=None,
    extra=None, show_console=True, **kw
):
    data = {
        'worker_id': worker_id,
        'direction': direction,
        'filename': filename,
        'size': size,
        'progress': progress,
        'status': status,
        'show_console': json.dumps(show_console),
    }
    if extra is not None:
        data['extra'] = json.dumps(extra)
    data.update(kw)

    # Detailed error info
    if error is not None:
        data.update({
            'error_code': error['code'],
            'error_msg': error['msg'],
            'error_detail': error['detail'],
        })
    try:
        return _request_api(
            '/worker/progress/update', kw=data, internal=True
        ).json()
    except Exception:
        pass


def remove_progress_row(worker_id, status):
    # 删除特定任务的某个状态的所有进度行
    try:
        return _request_api(
            '/worker/progress/remove',
            kw={'worker_id': worker_id, 'status': status},
            internal=True
        ).json()
    except Exception:
        pass


def show_progress_window():
    # 显示桌面助手的进度窗口
    try:
        return _request_api('/worker/progress/show', internal=True).json()
    except Exception as e:
        return {"success": False, "reason": str(e)}


def hide_progress_window():
    # 隐藏桌面助手的进度窗口
    try:
        return _request_api('/worker/progress/hide', internal=True).json()
    except Exception as e:
        return {"success": False, "reason": str(e)}


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


def report_detail(log=None, worker_id=None, error=False):
    if HEADLESS or SINGLE_PROCESS or not any([log, worker_id, ]):
        return
    try:
        traceback_content = extract_traceback()
        exception_message = str(log)
        return _request_api(
            '/ui/report_detail',
            kw={'log': traceback_content, 'worker_id': worker_id, 'error': error},
            internal=True, timeout=5
        )
    except:
        pass


def show_webview(
    url=None, body=None, title=None,
    size=None, position=None, resizable=True,
    maxbutton=True, minbutton=True
):
    if not any([url, body, ]):
        return
    try:
        return _request_api(
            '/ui/show_webview',
            kw={
                'url': url,
                'body': body,
                'title': title,
                'size': json.dumps(size),
                'position': json.dumps(position),
                'resizable': json.dumps(resizable),
                'maxbutton': json.dumps(maxbutton),
                'minbutton': json.dumps(minbutton),
            },
            internal=True, timeout=5
        ).json()
    except:
        pass


def refresh_webview(name, sync=False):
    '''刷新主窗口的指定tab。会自动判断合适的触发方式'''
    if HEADLESS or SINGLE_PROCESS:
        return {'success': True}

    if sync:
        from qtui.ui_utils import emit_webview_refresh_signal
        Timer(1.5, emit_webview_refresh_signal, (name, )).start()
        return {'success': True}

    try:
        return _request_api(
            '/ui/refresh_webview',
            kw={'webview': name},
            internal=True, timeout=2,
        ).json()
    except Exception:
        return {'success': False}
