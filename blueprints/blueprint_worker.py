# coding: utf-8
import cgi
import json
from datetime import datetime
from Queue import Queue
from threading import Thread

from flask import (
    Blueprint, current_app, request, abort, render_template, g as flask_g,
)
from werkzeug.local import LocalProxy

from utils import (
    addr_check, extract_data, extract_data_list,
    jsonp, translate as _, console_message, is_internal_call,
    filter_sensitive_fields, get_human_ltime, to_bool, get_wo_client
)
import ui_client
import worker

blueprint = Blueprint('worker', __name__, url_prefix='/worker')
# Caution: this can only be used during a request processing
logger = LocalProxy(lambda: current_app.logger)
trayIcon = LocalProxy(lambda: current_app.trayIcon)
LOCKS = LocalProxy(lambda: current_app.LOCKS)
UpdateItemsQueue = Queue()
UpdateProgressThread = None


def update_progress(window):
    while True:
        window.updateProgressSignal.emit(json.dumps(UpdateItemsQueue.get()))


def show_msg(title, body, type='none'):
    '''显示一个托盘图标消息，或者在静默模式下向终端打印这条消息'''
    console_message(unicode(title), unicode(body))

def unlock_editing_file(wid):
    db = worker.get_worker_db(wid)
    if not db or db.get("name", "") != "edit":
        # 任务不存在或不是外部编辑任务，不做任何事
        return
    token = db.get("token", None)
    server = db.get("server", None)
    account = db.get("account", None)
    instance = db.get("instance", None)
    wo_client = get_wo_client(
        token=token, server=server, account=account, instance=instance
    )
    uid = db.get("uid", None)
    uid = uid[0] if isinstance(uid, (list, tuple, )) else uid
    try:
        wo_client.content.unlock(uid=uid)
    except Exception:
        logger.exception(u"删除 #%s 任务后解锁文件 %s 失败", wid, uid)
        pass


@blueprint.route('/all', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
@jsonp
def api_worker_list():
    '''
    列出所有的任务的信息
    '''
    return json.dumps({
        'workers': [work for work in worker.list_workers()]
    })


@blueprint.route('/state', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
@jsonp
def api_worker_state():
    '''
    获取指定 ID 的任务的信息
    '''
    wid, fields = extract_data(('worker_id', 'fields',), request=request)
    if wid not in worker.list_worker_ids():
        abort(404)
    wstate = worker.get_worker(wid)
    detail = wstate.get('detail', {})
    result = {}
    if fields is not None:
        fields = json.loads(fields)
        for k in fields:
            result[k] = detail.get(k, None)
    else:
        result.update(detail)
    wstate['detail'] = filter_sensitive_fields(result)
    return json.dumps(wstate)


@blueprint.route('/cancel', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
@jsonp
def api_worker_cancel():
    '''
    删除指定 ID 的任务
    '''
    id, silent = extract_data(('worker_id', 'silent'), request=request)

    if id is not None:
        unlock_editing_file(id)
        result = worker.terminate_worker(id)
        show_msg(unicode(_('Assistant')), unicode(_('Task deleted')), 'info')
        return json.dumps(result)
    return json.dumps({
        'is_alive': False,
        'msg': _('Task ID not specified')
    })


@blueprint.route("/pause", methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
@jsonp
def api_worker_pause():
    '''
    暂停指定 ID 的任务
    '''
    id, turn_off_message = extract_data(
        ('worker_id', 'turn_off_message',), request=request
    )

    if id is not None:
        result = worker.pause_worker(id, turn_off_message)
        return json.dumps(result)
    return json.dumps({
        'is_alive': False,
        'msg': _('Task ID not specified')
    })


@blueprint.route('/restart', methods=['POST', 'OPTIONS', ])
@addr_check
@jsonp
def api_worker_restart():
    '''
    替换指定参数值并重启任务
    '''
    id = extract_data('worker_id', request=request)
    worker_db = worker.get_worker_db(id)

    if not worker_db:
        # not such task
        return json.dumps({
            'is_alive': False,
            'msg': _('No such task')
        })

    if worker_db['state'] == 'running':
        worker.pause_worker(id)

        # reload worker db
        worker_db = worker.get_worker_db(id)
        for k in request.form:
            if k == 'worker_id':
                continue
            worker_db[k] = request.form[k]
        worker_db.sync()

        # restart the worker
        return json.dumps(worker.start_worker(id))

    else:
        # the task has stopped
        # set state, do not need to reload
        for k in request.form:
            if k == 'worker_id':
                continue
            worker_db[k] = request.form[k]
        worker_db.sync()

        return json.dumps({
            "msg": "Task stopped",
            "is_alive": False,
            "worker_id": id
        })


@blueprint.route("/start", methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
@jsonp
def api_worker_start():
    '''
    开始指定 ID 的任务
    '''
    id = extract_data('worker_id', request=request)

    if id is not None:
        work = worker.get_worker_db(id)
        if not work:
            return json.dumps({
                'is_alive': False,
                'msg': _('No such task')
            })

        # 启动一个空的想消息提醒占位任务 => 打开消息提醒
        # 删除这个占位任务，然后跳转到站点地址上由用户手动打开消息提醒
        if work['name'] == 'messaging' and not work.get('token', '').strip():
            site_url = work.get('instance_url', None)
            if site_url is None:
                logger.debug(u'消息提醒没有站点 URL: %s', work)
                console_message(
                    unicode(_('Assistant messaging')),
                    unicode(_('You can visit that site to enable notification'))
                )
            else:
                ui_client.open_url(site_url)
            worker.terminate_worker(id)
            return json.dumps({
                'is_alive': False,
                'msg': _('You can now enable Assistant messaging')
            })

        return json.dumps(worker.start_worker(id))
    return json.dumps({
        'is_alive': False,
        'msg': _('Task ID not specified')
    })


@blueprint.route('/new/<worker_name>', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
@jsonp
def api_worker_new(worker_name):
    '''新建一个任务
    worker_name <String> 任务名称，同时也是要调用的任务处理模块的名称
    请求参数 onduplicate=warn[, run, ignore] 改变任务重复时的行为
      - run 不去重，可以创建并运行重复任务；
      - warn 去重，提示重复任务；
      - ignore 去重，并且不做提示；
    '''
    # 这是非常旧版本的脚本任务，只能从消息任务创建，现在已经基本不用
    if worker_name == 'script' and not is_internal_call(request):
        return json.dumps({
            'is_alive': False,
            'msg': _('Not allowed to create script task'),
        })

    # 不支持请求创建的任务
    if worker_name not in worker.WORKER_REG:
        logger.warn(u'没有找到可用的 worker: %s', worker_name)
        show_msg(
            _('Assistant'),
            _('This feature is not supported in current version'),
        )
        return json.dumps({
            'is_alive': False,
            'type': 'warn',
            'msg': _('This feature is not supported in current version'),
        })

    # 默认先提示用户「收到新任务」，但是这些任务不要提示
    silent_workers = (
        'upload', 'upload_v2',
        'script', 'online_script', 'process_duplicate'
    )
    silent = extract_data('silent', request=request)
    if not silent and worker_name not in silent_workers:
        show_msg(
            _('New task'),
            _('New task: {}').format(_(worker.get_worker_title(worker_name))),
            type='info',
        )

    # 从请求中取出任务的所有参数；其中这些参数的类型是 list
    kw = extract_data_list(
        ('uid', 'path', 'server_path', 'revisions', ),
        request=request,
    )

    # 旧版本的 script 任务，将脚本参数提升为任务参数
    if worker_name == 'script':
        script_kw = {}
        for k in ('script_vars', 'args', 'kw',):
            script_kw[k] = json.loads(request.form.get(k))
        kw.update(script_kw)
    # 此参数不能通过 HTTP 请求控制，只能在这里设置，并且在脚本任务中会被预先移除
    # 用途是：通过 HTTP 请求创建脚本任务，如果请求中 ast_token 验证通过，将可以不验证站点信任和脚本签名有效性
    kw['_request_verified'] = flask_g.request_verified

    # 新建任务
    try:
        worker_id = worker.new_worker(worker_name, **kw)
    except:  # noqa E722
        logger.error(u'新建任务出错', exc_info=True)
        worker_id = 0

    # 开始任务
    result = worker.start_worker(worker_id)
    return json.dumps(result)


@blueprint.route('/progress/', methods=['GET', 'POST', ])
@addr_check
def view_worker_progress_table():
    '''渲染进度表格'''
    return render_template('progress_view.html')


@blueprint.route('/progress/update', methods=['POST', 'GET', ])
@addr_check
def api_worker_progress_update():
    (wid, direction, fpath, filename, size,
     progress, status, uid,
     error_code, error_msg, error_detail, extra, show_console) = extract_data(
        ('worker_id', 'direction', 'fpath', 'filename', 'size',
         'progress', 'status', 'uid',
         'error_code', 'error_msg', 'error_detail', 'extra', 'show_console'),
        request=request
    )
    show_console = to_bool(show_console)
    try:
        wid = int(wid)
    except:
        return json.dumps({'success': False, 'msg': 'Invalid worker_id', })
    else:
        if not current_app.progress_window:
            return json.dumps({'success': False, 'msg': 'No UI available', })
        wdb = worker.get_worker_db(wid)
        worker_name = wdb.get('name', None)
        if not worker_name:
            return json.dumps({'success': False, 'msg': 'Invalid workerdb',})
        worker_title = _(
            worker.get_worker_title(worker_name, title=wdb.get('title'))
        )
        # Error detail might be very long, truncate first 500 chars
        if error_detail:
            error_detail = cgi.escape(error_detail)[:500]
        # 每一行可以存储一些额外数据
        if extra:
            extra = json.loads(extra)
        # 将接收到的进度更新请求放入队列中，异步去更新
        UpdateItemsQueue.put({
            'wid': wid,
            'name': worker_title,
            'direction': direction,
            'fpath': fpath,
            'filename': filename,
            'size': size,
            'progress': progress,
            'status': status,
            'uid': uid,
            'error_code': error_code,
            'error_msg': error_msg,
            'error_detail': error_detail,
            'extra': extra
        })

        return json.dumps({'success': True, 'msg': 'OK', })


@blueprint.route('/progress/show', methods=['POST', 'GET', ])
@addr_check
@jsonp
def api_worker_progress_show():
    return json.dumps({'success': False})


@blueprint.route('/progress/hide', methods=['POST', 'GET', ])
@addr_check
@jsonp
def api_worker_progress_hide():
    current_app.progress_window.hide()
    return json.dumps({'success': True, 'msg': 'OK', })


@blueprint.route('/progress/remove', methods=['POST', 'GET', ])
@addr_check
@jsonp
def api_worker_progress_remove():
    worker_id, status = extract_data(('worker_id', 'status'), request=request)
    if not (worker_id or status):
        return json.dumps({
            'success': False,
            'msg': 'Not enough arguments to remove the progress rows'
        })
    logger.debug("Worker ID: %r(%s), Status: %r(%s)", worker_id, type(worker_id), status, type(status))
    current_app.progress_window.remove_progress_row(worker_id, status)
    return json.dumps({'success': True, 'msg': 'OK'})


@blueprint.route('/lock/acquire', methods=['POST', ])
@addr_check
@jsonp
def api_lock_acquire():
    worker_id, lock_name, description = extract_data(
        ('worker_id', 'name', 'description'), request=request
    )
    if not all([worker_id, lock_name, ]):
        locked = {'success': False, 'msg': 'Missing parameter',}
    else:
        # 只能为运行的任务加锁
        if worker.get_worker_db(worker_id).get('state', None) != 'running':
            return json.dumps({'success': False, 'msg': 'Worker invalid',})
        lock = LOCKS.get(lock_name, None)
        # 加锁
        if lock is None:
            LOCKS[lock_name] = {
                'worker_id': worker_id,
                'description': description or '',
                'since': datetime.now(),
            }
            locked = {'success': True, 'msg': 'OK',}
        else:
            # 已经有人锁了
            if lock['worker_id'] != worker_id:
                locked = {
                    'success': False,
                    'msg': 'Locked by #{}'.format(lock['worker_id']),
                }
            else:
                # 自己重复加锁，也允许
                locked = {'success': True, 'msg': 'Already locked',}

    return json.dumps(locked)


@blueprint.route('/lock/release', methods=['POST', ])
@addr_check
@jsonp
def api_lock_release():
    worker_id, lock_name = extract_data(
        ('worker_id', 'name', ), request=request
    )

    if is_internal_call(request) and not lock_name:  # 内部清理一个任务所有的锁
        for lock_name in LOCKS.keys():
            if LOCKS[lock_name]['worker_id'] == worker_id:
                LOCKS.pop(lock_name)
                logger.debug(u'自动清理了 #%s 的锁 %s', worker_id, lock_name)
        return json.dumps({'success': True, 'msg': 'OK',})

    if not all([worker_id, lock_name, ]):
        released = {'success': False, msg: 'Missing parameter',}
    else:
        lock = LOCKS.get(lock_name, None)
        # 不存在的锁也还是允许解吧，反正没影响
        if lock is None:
            released = {'success': True, 'msg': 'No such lock',}
        else:
            # 不能解别人的锁
            if lock['worker_id'] != worker_id:
                released = {
                    'success': False,
                    'msg': 'Lock "{}" is acquired by #{}'.format(
                        lock_name, lock['worker_id']
                    ),
                }
            else:
                # 解锁
                LOCKS.pop(lock_name)
                released = {'success': True, 'msg': 'OK',}
    return json.dumps(released)
