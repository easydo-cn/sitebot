# coding: utf-8
'''
桌面助手 Blueprint
/admin 路由
'''
from io import open
import json
import os
import sys
import time
import calendar
from datetime import datetime
from functools import wraps

import requests
from flask import (
    Blueprint, current_app, request, render_template,
    redirect,
)
from werkzeug.local import LocalProxy

import config
import ui_client
from config import (
    BUILD_NUMBER, VERSION, LOG_DATA, GIT_INFO, FILE_STORE_DIR,
    HEADLESS, SINGLE_PROCESS, CONFIG
)

from filestore import get_file_store
from utils import (
    addr_check, extract_data, get_object_manage_url,
    utc_to_local, get_filestore_by_filename,
    get_filestore_from_request, translate as _,
    get_deal_time, filter_sensitive_fields,
    console_message, jsonp
)

import worker
from libs.managers import get_site_manager

blueprint = Blueprint('admin', __name__, url_prefix='/admin')
# Caution: this can only be used during a request processing
logger = LocalProxy(lambda: current_app.logger)
site_manager = get_site_manager()

conflict_types = {
    1: _('Both modified'),
    2: _('Remote removed'),
    3: _('Local removed'),
    4: _('Both created')
}

CACHE = None


def show_msg(title, body, type='none'):
    '''显示一个托盘图标消息，或者在静默模式下向终端打印这条消息'''
    if HEADLESS or SINGLE_PROCESS:
        console_message(unicode(title), unicode(body))
    else:
        current_app.trayIcon.message(unicode(title), unicode(body), type=type)


def get_common_template_data():
    return {
        'platform': sys.platform,
        'build_number': BUILD_NUMBER,
        'version': VERSION,
        'LOG_DATA': LOG_DATA,
        'git_info': GIT_INFO
    }


def render(template):
    '''
    Decorator for rendering template to webpages,
    with common data taken care of.
    '''
    def decorator_render(func):
        @wraps(func)
        def decorated_view(*args, **kwargs):
            func_data = func(*args, **kwargs)
            if isinstance(func_data, (dict, )):
                data = get_common_template_data()
                data.update(func_data)
                return render_template(template, **data)
            return func_data
        return decorated_view
    return decorator_render


def generate_uid_url(work):
    oc_server = work.get('oc_server', '')
    account = work.get('account', '')
    instance = work.get('instance', '')
    site = site_manager.get_site(oc_server, account, instance)
    return site.instance_url if site else None


@blueprint.route('/worker', methods=['GET', 'OPTIONS', ])
@addr_check
@render('worker.html')
def view_worker_management():
    '''
    任务管理界面
    '''
    workers = []
    for _w in worker.list_workers():
        _kvs = filter_sensitive_fields(_w['detail']).items()
        _w['detail']['params'] = '<pre>'
        for _kv in _kvs:
            if not _kv[0] == 'error' and not _kv[0].startswith('_'):
                _w['detail']['params'] += '%s: %s\n' % _kv
        _w['detail']['params'] += '</pre>'

        if isinstance(_w['detail'].get('_result', None), list):
            _w['detail']['_result'] = [
                {
                    'url': get_object_manage_url(_i),
                    'text': os.path.basename(_i.get('local_path', '')),
                    'object_type': _i.get('object_type', None),
                    'local_path': _i.get('local_path', None)
                } for _i in _w['detail']['_result']]

        # 自动同步
        if _w['detail'].get('auto', ''):
            _i = {
                'object_type': 'folder',
                'local_path': _w['detail']['path'][0],
                'uid': _w['detail']['uid'][0],
            }
            fs = get_file_store(
                _w['detail']['oc_server'],
                _w['detail']['account'],
                _w['detail']['instance']
            )
            _w['detail']['local_url'] = get_object_manage_url(_i, filestore=fs)
        workers.append(_w)
    return {'workers': workers}


@blueprint.route('/worker_detail', methods=['GET', 'OPTIONS', ])
@addr_check
@render('worker_detail.html')
def view_worker_detail():
    worker_id = extract_data('worker_id', request=request)
    wdb = filter_sensitive_fields(worker.get_worker_db(worker_id))
    if not wdb:
        return {'worker': None}
    worker_detail = worker.get_worker_renderer(id=worker_id)(wdb)
    return {
        'worker': wdb,
        'worker_title': worker.get_worker_title(wdb.get('name'), title=wdb.get('title')),
        'worker_detail': worker_detail,
        'build_number': BUILD_NUMBER,
        'worker_id': worker_id,
        'worker_start_time': utc_to_local(wdb.get('start_time', None)),
    }


@blueprint.route('/viewlog', methods=('GET', ))
@addr_check
def view_view_log():
    '''
    返回指定日志文件的内容
    请求参数:
    - name: 日志文件的名称，不带后缀，例如 worker_1 / webserver；
    注意:
    - 此接口返回一个 HTML 页面，其中包含日志内容；
    - 如果日志文件很大，只返回后 2MB 内容；
    '''
    name = extract_data('name', request=request)
    log_file = os.path.join(LOG_DATA, '.'.join([name, 'log']))
    data = {
        'content': None,
        'path': log_file,
    }
    size_limit = 2 ** 20  # 2 Megaabytes
    if os.path.isfile(log_file):
        with open(log_file, 'r', encoding='utf-8', errors='replace') as rf:
            # Read last `size_limit` bytes
            start = 0
            rf.seek(0, 2)
            size = rf.tell()
            if size > size_limit:
                start = size - size_limit
            rf.seek(start)
            data['content'] = rf.read(size_limit)
    return render_template('log.html', data=data)


@blueprint.route('/config', methods=['GET', 'POST', 'OPTIONS'])
@addr_check
def view_manage_config():
    # 处理数据提交
    if request.method == 'POST':
        # 点击【重启资源管理器】按钮
        action = extract_data('action', request)
        if action == 'restart_explorer' and sys.platform == 'win32':
            from utils.win32_utils import restart_explorer
            restart_explorer()
        if action == 'reset_ports_on_next_start':
            if os.path.exists(config.CONFIG_FILE):
                with open(config.CONFIG_FILE, 'r+b') as f:
                    cfg = json.load(f)

                if 'http_port' in cfg:
                    del cfg['http_port']
                if 'https_port' in cfg:
                    del cfg['https_port']

                with open(config.CONFIG_FILE, 'w+b') as f:
                    json.dump(cfg, f)

        return json.dumps({'success': True})

    # 渲染界面
    return render_template('config.html', config=CONFIG)


@blueprint.route('/manage_connections', methods=['GET', 'OPTIONS', 'POST'])
@addr_check
def view_settings():
    '''
    管理设置
    注意：
    - 这是供主窗口的“设置” webview 所使用的接口
    - 同时负责渲染主窗口的“设置” webview 页面
    '''
    return render_template(
        'connections.html', sites=site_manager.list_sites(), headless=HEADLESS
    )


@blueprint.route('/connections', methods=['POST', ])
@addr_check
def view_connection_management():
    '''
    管理站点连接
    注意:
    - 这是供主窗口的“设置” webview 中，站点连接管理部分 Javascript 所使用的接口
    消息连接的逻辑:
    - 开启: 开启提醒 or 开启信任时；
    - 关闭: 关闭提醒 and 关闭信任时；
    - 重启任务: 切换提醒 or 切换信任，且切换之后消息连接仍处于开启状态时；
    '''
    # 对指定站点连接进行操作
    if request.method == 'POST':
        conn_id, action = extract_data(
            ('connection_id', 'action'), request=request
        )

        if action == 'reload':
            # 更新主进程 site_manager 并刷新站点连接页面
            site_manager.reload_sites()
            return json.dumps({
                'success': True, 'msg': 'Successful reload'
            })

        for site in site_manager.list_sites():
            if site.id == conn_id:
                break
        else:
            site = None
        success = False

        if not all([conn_id, action, site]):
            return json.dumps({
                'success': success, 'msg': 'No such connection'
            })

        if action == 'remove':  # 删除站点连接
            site_manager.remove_site(site)
            success = True

        elif action == 'enable_notification':  # 启用提醒
            site.set_config("notification", True)
            site.get_message_thread().connect()
            success = True
            show_msg(
                _('Messaging'),
                _('Messaging on {1} turned on for {0}').format(
                    site.username, site.instance_name
                )
            )
            site_manager.save()
        elif action == 'disable_notification':  # 关闭提醒
            site.set_config("notification", False)
            site.get_message_thread().disconnect()
            success = True
            show_msg(
                _('Messaging'),
                _('Messaging on {1} turned off for {0}').format(
                    site.username, site.instance_name
                )
            )
            site_manager.save()

        return json.dumps({
            'success': success,
            'msg': 'Action performed' if success else 'Action not performed'
        })


@blueprint.route("/site_connections", methods=["POST", "GET", "OPTIONS"])
@addr_check
@jsonp
def api_site_connections():
    """
    从站点向桌面助手建立连接，或者切换已有连接的通知状态，通过 action 来指定
    - action: "create"/"allow_notify"/"disallow_notify"
    """
    # 1. 获取新建连接或查询连接的必须参数
    oc_server, account, instance, token = extract_data(
        ("oc_server", "account", "instance", "token"), request=request
    )
    if not all([oc_server, account, instance, token]):
        message(_('Desktop assistant connection'),
                _('Desktop assistant connection is failed'),
                'error')
        return json.dumps({
            'success': False,
            'msg': 'not enough parameters'
        })

    # 2. 获取连接相关的额外参数和 action
    instance_name, instance_url, username, pid, action = extract_data(
        ("instance_name", "instance_url", "username", "pid", "action"),
        request=request
    )

    # 3. 进行具体操作
    if action not in ("create", "allow_notify", "disallow_notify"):
        message(_('Desktop assistant connection'),
                _('Desktop assistant connection is failed'),
                'error')
        return json.dumps({
            'success': False,
            'msg': 'unsupported action'
        })
    elif action == "create":
        # 3.1 建立连接
        site = site_manager.add_site(
            oc_url=oc_server,
            account=account,
            instance=instance,
            token=token,
            instance_name=instance_name,
            instance_url=instance_url,
            username=username,
            pid=pid,
        )
        connected = site.get_message_thread().connect()
        if connected:
            message(_('Desktop assistant connection'),
                    _('Desktop assistant connection is existed'),
                    'info')
        else:
            # 建立连接默认不开启消息提醒
            site.set_config("notification", action == "disallow_notify")
            site_manager.save()
            message(_('Desktop assistant connection'),
                    _('Desktop assistant connection success'),
                    'info')
        return json.dumps({
            'success': True,
            'msg': 'created'
        })
    else:
        # 3.2 切换通知
        site = site_manager.get_site(oc_server, account, instance)
        if not site:
            return json.dumps({
                'success': False,
                'msg': "no matches connection"
            })
        site.set_config("notification", action == "allow_notify")
        site_manager.save()
        return json.dumps({
            'success': True,
            'msg': ''
        })


def message(title, body, type='none'):
    try:
        ui_client._request_api(
            'ui/message',
            {'title': title, 'body': body, 'type': type}
        )
    except:
        pass


def count_all_files(req=None):
    try:
        file_store = get_filestore_from_request(req)
        return len(file_store.list_all_files())
    except Exception:
        return sum([
            len(get_filestore_by_filename(db).list_all_files())
            for db in os.listdir(FILE_STORE_DIR) if db.endswith('.db')
        ])


def stat_all_files(req=None):
    try:
        file_store = get_filestore_from_request(req)
        return file_store.stat_files()
    except Exception:
        stat_info = {}
        for db in os.listdir(FILE_STORE_DIR):
            if not db.endswith('.db'):
                continue
            stat = get_filestore_by_filename(db).stat_files()
            for k in stat.keys():
                if stat_info.get(k, None) is not None:
                    stat_info[k] += stat[k]
                else:
                    stat_info[k] = stat[k]
        return stat_info


if not HEADLESS:
    @blueprint.route('/worker_list', methods=['GET', 'OPTIONS', ])
    @addr_check
    @render('worker_list.html')
    def view_worker_list():
        workers = {'workers': []}
        for work in worker.list_workers():
            if work.get('name', '') in ('new_webfolder', ):
                # 不需要显示特定任务
                continue

            if work.get('name', '') == 'sync':
                # 不需要显示自动同步任务
                if work.get("detail", {}).get('auto', False):
                    continue

            if work.get('state', '') == 'finished':
                # 不需要显示已经完成的任务
                continue

            # 任务结束时间超过一周的，不显示在任务页面
            end_timestamp = work['detail'].get('end_timestamp', None)
            if end_timestamp:
                if (int(time.time()) - end_timestamp) > 60 * 60 * 24 * 7:
                    continue

            start_timestamp = work['detail'].get('start_timestamp', None)
            if start_timestamp:
                start_time = datetime.fromtimestamp(start_timestamp)
                work['start_time'] = start_time.strftime("%m-%d %H:%M")
            else:
                work['detail']['start_timestamp'] = 0
                work['start_time'] = ''
            workers['workers'].append(work)
        workers['workers'] = sorted(
            workers['workers'],
            key=lambda x: x['detail']['start_timestamp'],
            reverse=True,
        )
        return workers


    @render('sitefile_categories.html')
    def render_all_file_categories():
        stat_info = []
        pre_stat = stat_all_files()
        for usage in pre_stat.keys():
            stat_info.append({
                'count': pre_stat[usage],
                # Dont translate here, translate in template engine instead
                'name': usage,
                'management_url': '?usage={}'.format(usage),
            })
        return {
            'file_count': count_all_files(),
            'categories': stat_info,
        }


    @render('site_file.html')
    def render_file_category(category):
        usage = category
        usage = '' if usage == 'unknown' else usage
        files = []
        for db_file in os.listdir(FILE_STORE_DIR):
            if db_file.endswith('.db'):
                file_store = get_filestore_by_filename(db_file)
                _files = file_store.query_items(
                    root_uid='', usage=usage, object_type='file'
                )
                for i in _files:
                    last_deal = get_deal_time((
                        i['last_pull'],
                        i['last_push'],
                        i['modified']
                    ))
                    sort_time = datetime.strptime(last_deal, '%Y-%m-%d %H:%M:%S')
                    sort_time = calendar.timegm(sort_time.timetuple())
                    i.update({
                        'server': file_store.server,
                        'account': file_store.account,
                        'instance': file_store.instance,
                        'last_deal': last_deal,
                        'sort_time': sort_time
                    })
                files.extend(_files)
        return {'files': files, 'usage': usage or 'unknown'}


    @blueprint.route('/site_file', methods=['GET', 'OPTIONS', ])
    @addr_check
    def view_sitefile_statics_management():
        # gettext evals
        _('download')
        _('upload')
        _('edit')
        _('unknown')
        _('3dpreview')
        _('view')
        _('p2pdownload')
        _('conflict_backup')

        usage = extract_data('usage', request=request)
        if usage is None:
            return render_all_file_categories()
        else:
            return render_file_category(usage)


    @blueprint.route('/web_folder')
    @addr_check
    @render('web_folders.html')
    def view_web_folders_management():
        webfolders = []
        if sys.platform != 'win32':
            return {'workers': []}
        import urllib
        for wid in worker.list_worker_ids():
            work = worker.get_worker_db(wid)
            if not work or work.get('name', '') != 'new_webfolder':
                continue
            if not work.get('executed', False):
                continue
            work['worker_id'] = wid
            if not work.get('folder_name', ''):
                folder_url = urllib.unquote(
                    str(work['folder_url'])
                ).decode('utf-8')
                work['folder_name'] = folder_url[-1]
            if not work.get('uid_url', ''):
                work['uid_url'] = '{url}/++intid++{uid}/view.html'.format(
                    url=generate_uid_url(work),
                    uid=work['uid'][0],
                )
            webfolders.append(work)
        data = {
            'workers': webfolders,
            'supported_platform': sys.platform == "win32"
        }
        if sys.platform == 'win32':
            from utils.win32_utils import is_dokan_installed, is_dot_net_installed, get_winver  # noqa E501
            data.update({
                'dokan_installed': is_dokan_installed(),
                'dot_net_installed': is_dot_net_installed(),
                # (6, 1, 1) ==> Windows7 SP 1
                'supported_platform': get_winver() >= (6, 1, 1),
            })
        return data


@blueprint.route('/locks', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
def view_locks_management():
    # 在控制台页面强制释放指定锁
    if request.method == 'POST':
        lock_name, worker_id, action = extract_data(
            ('lock_name', 'worker_id', 'action', ), request=request
        )
        if action == 'force_release'\
                and lock_name in current_app.LOCKS\
                and str(current_app.LOCKS[lock_name]['worker_id']) == str(worker_id):
            current_app.LOCKS.pop(lock_name)
            logger.debug(u'强制解锁了 #%s 的锁 %s', worker_id, lock_name)
            return json.dumps({'success': True, 'msg': 'Unlocked',})
        else:
            return json.dumps({'success': False, 'msg': 'Failed to unlock',})
    # 渲染锁列表页面
    return render_template(
        'locks.html',
        locks=current_app.LOCKS,
        **get_common_template_data()
    )


@blueprint.route('/help', methods=['GET', 'OPTIONS'])
@addr_check
def view_help():
    return render_template('help.html')


@blueprint.route('/linux_update', methods=['POST', 'GET', 'OPTIONS', ])
@addr_check
@render('linux_update.html')
def view_linux_update():
    info = extract_data('info', request=request)
    if info is None:
        return redirect('/admin/worker', code=302)
    try:
        info = json.loads(info)
    except Exception:
        logger.error(u'Linux 升级页面渲染错误', exc_info=True)
        return redirect('/admin/worker', code=302)
    else:
        [info[k].update({'dist': k}) for k in info.keys()]
        updates = [info[k] for k in info.keys()]
        return {'updates': updates}

if not HEADLESS and sys.platform.startswith('win'):
    from libs import (get_all_program, set_user_editor)

    @blueprint.route('/default_progs', methods=['GET', 'POST'])
    @addr_check
    def view_default_progs():
        '''Only available on Windows'''
        global CACHE
        if request.method == 'GET':
            data = []
            ext = extract_data('fileext', request=request)
            if ext:
                # add progid programs
                programs = {'name': ext}
                programs['data'] = get_all_program(ext)
                CACHE = programs
                data = [prog[0] for prog in programs['data']]
                return json.dumps(data)
            return render_template('default_progs.html', data={
                'platform': sys.platform,
                'build_number': BUILD_NUMBER,
                'version': VERSION,
                'LOG_DATA': LOG_DATA,
                'git_info': GIT_INFO,
            })
        else:
            programs = CACHE
            progname = extract_data('progname', request=request)
            ext = programs['name']
            try:
                for name, progid, command in programs['data']:
                    if progname == name:
                        set_user_editor(ext, progid, command)
                        return json.dumps({
                            'status': 0,
                            'msg': 'set successfully'
                        })
                return json.dumps({
                    'status': 1,
                    'msg': 'fail to set editor'
                })
            except WindowsError:
                return json.dumps({
                    'status': 1,
                    'msg': 'fail to set editor'
                })
