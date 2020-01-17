# encoding: utf-8
import cgi
import os
import time
import json
import logging
import copy
import inspect
import signal
import sys

from libs import workerdb
import ui_client
from datetime import datetime
from multiprocessing import Process, current_process
import threading
import psutil
from flask import current_app, has_app_context

from errors import AssistantException, Retry, LogicError
from config import (
    WORKER_STORAGE_DIR, LOG_DATA, VERSION,
    BUILD_NUMBER, RETRY_INTERVAL, AUTO_START_INTERVAL,
    FROZEN, SINGLE_PROCESS, GIT_INFO, HEADLESS, WORKERS,
)
from ui_client import _request_api
from utils import (
    get_worker_logger,
    utc_to_local, utc_to_timestamp, extract_traceback,
    get_logger, compare_dicts, translate as _,
    close_logger, filter_sensitive_fields,
    close_worker_logger, is_network_error,
    load_logging_config,
)

# i18n fixes
_('error')
_('finished')
_('running')
_('prepare')
_('paused')

WORKER_REG = {}  # worker_name, {title, function}
WORKER_SCAN_INTERVAL = 10
log = logging.getLogger(__name__)


def register_worker(func, renderer=None):
    WORKER_REG[func.func_name] = {
        'title': _(func.func_doc),
        'function': func,
        'renderer': renderer or standard_worker_renderer,
    }  # 存储的数据的key
    return func


PROCESSES = {}
DAEMON_THREAD = None
DAEMON_THREAD_STOP_EVENT = None


def standard_worker_renderer(wdb):
    '''Render HTML text with given workerdb'''
    wdb = filter_sensitive_fields(wdb)
    html = '<table class="table table-hover">'
    # 任务的进程 ID
    if wdb['state'] in ('running', 'prepare'):
        process = PROCESSES.get(wdb.id)
        if process is not None:
            html += '<tr><td>process_id</td><td>{}</td></tr>'.format(process.pid)
    # 将这些字段放在前面
    for key in (
        'oc_server', 'server', 'message_server', 'upload_server',
        'account', 'instance', 'uid', 'path', 'pid',
    ):
        if key in wdb:
            html += '<tr><td>{}</td><td>{}</td></tr>'.format(
                key, cgi.escape(str(wdb.pop(key, '')))
            )
    for k, v in wdb.items():
        if k.startswith('_'):
            continue
        html += '<tr><td>{}</td><td>{}</td></tr>'.format(k, cgi.escape(str(v)))
    html += '</table>'
    return html


def get_worker_renderer(name=None, id=None):
    name = name or get_worker_db(id).get('name', None)
    if name is not None:
        return WORKER_REG.get(name, {}).get(
            'renderer', standard_worker_renderer
        )
    return standard_worker_renderer


def get_next_id():
    next_id = 0
    for id in list_worker_ids():
        if int(id) > next_id:
            next_id = int(id)
    return next_id + 1


def get_db_path(worker_id):
    return os.path.join(WORKER_STORAGE_DIR, '{}.db'.format(worker_id))


def get_log_path(worker_id):
    return os.path.join(LOG_DATA, 'worker_{}.log'.format(worker_id))


def get_worker_db(worker_id):
    db_path = get_db_path(worker_id)
    try:
        return workerdb.dbopen(db_path)
    except:
        print extract_traceback()
        return None


def get_worker_title(name=None, auto=False, id=None, title=None):
    '''
    获取某个worker的名字
    优先级:
    - 如果有 title，直接返回 title
    - 如果没有 title 但给定了 id，优先使用这个编号的 workerdb 中 title 字段
    - 如果都没有，返回 name
    '''
    if isinstance(title, basestring):
        title = title.strip()
    if title:
        return title
    if id is not None:
        wdb = get_worker_db(id)
        if wdb:
            title = wdb.get('title', None)
            if not name:
                name = wdb.get('name', None)

    if isinstance(title, basestring):
        title = title.strip()

    if not name:
        return 'Unknown'
    return title or WORKER_REG.get(name, {}).get('title', name)


def ready_to_quit():
    '''
    检查当前正在进行的任务是否允许桌面助手退出
    有外部编辑等任务时不允许退出（外部进程会阻塞端口）。
    Rerurns: (ready_to_quit, reason)
    '''
    forbidden_works = ('edit', )
    for work in list_workers():
        if work['state'] in ('running', )\
                and work.get('name') in forbidden_works:
            return False, get_worker_title(name=work['name'])
    return True, None


def get_messaging_status(wo_server, message_server, account, instance, pid):
    '''
    获取指定站点指定人员的消息提醒任务状态
    '''
    for i in list_worker_ids():
        db = get_worker_db(i)
        if db.get('name', None) != 'messaging':
            continue
        fake_db = {
            'server': wo_server,
            'message_server': message_server,
            'account': account,
            'instance': instance,
            'pid': pid,
        }
        if not compare_dicts(db, fake_db, keys=fake_db.keys()):
            continue
        # 有 token，消息提醒是打开的
        if db.get('token', '').strip():
            return True
        # 没有 token 的占位任务，消息提醒的关闭的
        else:
            return False
    # 没有消息提醒任务
    return None


def remove_worker_db(worker_id):
    print 'requested to delete ', worker_id
    process = PROCESSES.get(worker_id, None)
    if process is not None:
        kill_process(process)
    db_path = get_db_path(worker_id)
    log_path = get_log_path(worker_id)
    # 删除数据库
    try:
        os.remove(db_path)
        print u'Worker {} deleted'.format(worker_id)
    except:
        pass
    # 删除日志文件
    try:
        os.remove(log_path)
    except:
        pass
    # 删除日志文件备份
    try:
        os.remove('{}.1'.format(log_path))
    except:
        pass

    refresh_worker_tab()


def worker_exists(db, ignore_ids=None, logger=None):
    '''
    Return True if duplicated
    db: workerdb, or dict type
    ignore_ids: list of id of workers which should not be compared
    '''
    ignore_ids = ignore_ids if isinstance(ignore_ids, (list, tuple)) else []
    logger = get_logger(
        'worker', filename='worker.log', init_level=logging.WARN
    )
    ignore_duplicates = ()  # 这些任务忽略重入检查

    try:
        assert 'name' in db
        signature = get_worker_signature(db)
    except Exception:
        logger.warn(
            u'获取 worker 签名时出错: %s',
            json.dumps(db, indent=4),
            exc_info=True
        )
        return True
    else:
        worker_name = db['name']
        for id in list_worker_ids():
            if id in ignore_ids:
                continue
            work = get_worker_db(id)
            automatic = work.get('auto', False)
            finished = work.get('state') in ('error', 'paused', 'finished', )

            # 名字不相同，不构成重复
            if worker_name in ignore_duplicates\
                    or worker_name != work.get('name'):
                continue

            # 已经完成的非自动任务就不需要关心了
            # 注意消息提醒任务是会一直保持运行的，不论状态如何都要进一步对比
            if finished and not automatic\
                    and worker_name not in ('messaging', ):
                continue

            # 按照这些值判断是否重复
            if worker_name == 'messaging':
                match_keys = (
                    'server', 'message_server',
                    'pid', 'account', 'instance',
                )
            elif worker_name == 'sync':
                match_keys = (
                    'oc_server', 'account', 'instance', 'path',
                )
            elif worker_name == 'script':
                match_keys = (
                    'token', 'account', 'instance', 'server',
                    'signature', 'script_url', 'script_content',
                )
            else:
                match_keys = ()

            if worker_name == 'script':
                # 脚本任务只判断 match_keys
                if compare_dicts(work, db, keys=match_keys):
                    logger.debug(
                        u'脚本任务构成重复: \n已有任务: \n%s, 比较的任务: \n%s',
                        json.dumps(work, indent=4),
                        json.dumps(db, indent=4)
                    )
                    return True
            else:
                if match_keys:
                    exists = compare_dicts(work, db, keys=match_keys)
                else:
                    exists = False
                if exists or get_worker_signature(work) == signature:
                    logger.debug(
                        u'%s 任务重复: \n已有任务: \n%s, sig: %s; 比较的任务: \n%s, sig: %s',  # noqa
                        worker_name,
                        json.dumps(work, indent=4),
                        get_worker_signature(work),
                        json.dumps(db, indent=4),
                        signature
                    )
                    return True
        return False
    finally:
        close_logger(logger)


def new_worker(worker_name, **kw):
    '''
    获取（递增的）任务 ID 并保存任务数据到任务的数据库中
    '''
    new_id = get_next_id()

    # 开始日志记录
    # 清理上一次的工作数据库和日志记录，避免日志混杂
    remove_worker_db(new_id)

    logger = get_worker_logger(new_id)
    logger.debug(
        u'----------新建任务，ID：%s，记录任务信息----------', new_id
    )
    worker_storage = get_worker_db(new_id)
    worker_storage['name'] = worker_name

    worker_storage['state'] = 'prepare'

    for key, value in kw.items():
        if value is not None:
            worker_storage[key] = value
    worker_storage.sync()
    close_worker_logger(new_id)
    refresh_worker_tab()
    return str(new_id)


def list_worker_ids():
    """ 所有的工作，包括各种状态的  """
    for filename in os.listdir(WORKER_STORAGE_DIR):
        if filename.endswith('.db'):
            yield filename[:-3]


def get_alive_worker_count():
    '''获取运行的任务数'''
    count = 0
    for work in list_workers():
        if work['state'] in ('running', ):
            count += 1
    return count


def list_workers():
    for worker_id in list_worker_ids():
        try:
            yield get_worker(worker_id)
        except GeneratorExit:
            raise
        except Exception as e:
            print u'list_workers() exception:', e
            if os.path.exists(get_db_path(worker_id))\
                    and not get_worker_db(worker_id):
                remove_worker_db(worker_id)
                print u'Hit empty worker db: {}.db, remove it'.format(
                    worker_id
                )
            continue


def get_worker(id):
    """ 得到某个worker的信息 """
    worker_storage = get_worker_db(id)
    name = worker_storage.get('name')
    detail = {}
    [detail.update({k: v}) for k, v in worker_storage.items()]
    if 'start_time' in detail:
        detail['start_timestamp'] = utc_to_timestamp(detail['start_time'])
        detail['start_time'] = utc_to_local(detail['start_time'])
    if 'end_time' in detail:
        detail['end_timestamp'] = utc_to_timestamp(detail['end_time'])
        detail['end_time'] = utc_to_local(detail['end_time'])
    if '_result' in detail:
        detail.update({'_result': json.loads(detail['_result'])})

    if worker_storage['state'] in ('prepare', 'running'):
        process = PROCESSES.get(id, None)
        process_id = process.pid if process else None
    else:
        process_id = None
    return {
        'name': name,
        'title': get_worker_title(
            name, id=id, title=worker_storage.get('title')
        ),
        'worker_id': id,
        'state': worker_storage['state'],
        'process_id': process_id,
        'detail': detail,
    }


def get_worker_signature(worker_db):
    '''
    获得指定 worker 的唯一标识
    Return: <String|None>
    Notice: 不存在的 worker 将会返回 None
    '''
    worker_name = worker_db.get('name')
    if worker_db is None or worker_name not in WORKER_REG:
        return None
    signature_parts = []
    # 取出函数的参数列表和默认值列表
    argspec = inspect.getargspec(WORKER_REG[worker_name]['function'])
    # 第一个参数是 worker_id，忽略
    try:
        defaults = list(argspec.defaults) or []
    except:
        defaults = []
    devide = len(argspec.args) - len(defaults)
    args = list(argspec.args[1:devide])
    kwargs = list(argspec.args[devide:])
    # 忽略这些参数
    # _nocache 是 GET 请求取消缓存的随机字符串
    # pipe 由 worker wrapper 提供
    ignores = ('_nocache', 'pipe', )
    for i in ignores:
        try:
            args.remove(i)
        except ValueError:
            pass
        if i in kwargs:
            _i = kwargs.index(i)
            kwargs.remove(i)
            defaults.pop(_i)
    # 组合函数的参数列表和 worker 数据库中参数值列表
    for arg in args:
        if arg not in worker_db:
            raise AssistantException(2, u'获取 worker 标识时发现缺少 {} 参数'.format(arg))
        signature_parts.append('{}={}'.format(arg, worker_db[arg]))
    # 组合这个函数可选参数列表和默认值
    for kwarg in kwargs:
        signature_parts.append(
            '{}={}'.format(kwarg, defaults[kwargs.index(kwarg)])
        )
    # 构成这个 worker 的唯一签名
    return '{}({})'.format(worker_name, ', '.join(signature_parts))


def prepare_worker_args(name, id):
    '''获取 worker 实参列表'''
    ignores = ('pipe', )
    db = get_worker_db(id)
    argspec = inspect.getargspec(WORKER_REG[name]['function'])
    try:
        defaults = list(argspec.defaults) or []
    except:
        defaults = []
    devide = len(argspec.args) - len(defaults)
    args = list(argspec.args[1:devide])
    kwargs = list(argspec.args[devide:])
    real_args = []
    for arg in args:
        real_args.append(db[arg])
    for kwarg in kwargs:
        if kwarg in ignores:
            continue
        default = defaults[kwargs.index(kwarg)]
        real_args.append(db.get(kwarg, default))
    return real_args


def is_background_task(worker_id):
    """
    检测是否为后台自动运行的任务，比如映射盘、或者实时同步
    Args:
        worker_id <int> 任务 ID
    Return:
        <bool>
    """
    worker = get_worker_db(worker_id)
    name = worker.get("name", "")
    return name in ('new_webfolder', ) or (
        name == 'sync' and worker.get("auto", False)
    )


def safe_run_worker(id, sync=False, pipe=None):
    '''
    任务子进程入口
    这个入口负责:
    - 运行 run_worker；
    - 负责执行 Retry 异常指定的重试策略；
    注意:
    - run_worker 对于未经处理的网络错误，默认以每次 10 秒延迟重试最多 10 次；
    - 任务可以自行捕获网络错误，并通过抛出 Retry 异常来指定重试策略；
    '''
    # from workers import * 会 import 名为 sync 的模块，
    # 所以这里将 sync 的值保存到另一个变量里
    from ui_client import refresh_webview, report_detail
    sync_flag = sync
    if not HEADLESS:
        allowed_workers = WORKERS
    else:
        allowed_workers = ["online_script", "script"]
    exec('from workers import ({})'.format(','.join(allowed_workers)))

    load_logging_config(worker_id=id)
    logger = get_worker_logger(id)

    worker_db = get_worker_db(id)
    if worker_db.get('last_state', None) is not None:
        logger.debug(u'任务上次状态: %s', worker_db['last_state'])
    worker_db['last_state'] = worker_db['state']
    worker_db['state'] = 'running'
    worker_db.sync()

    retried = -1
    while 1:
        try:
            if not sync_flag:
                refresh_webview('workers')
            run_worker(id, sync=sync_flag, pipe=pipe)
        except Retry as e:
            if e.count != -1:
                retried += 1
                logger.info(u'任务已重试 %s 次', retried)

                if retried >= e.count:
                    # 重试次数超出重试策略指定次数，显示任务详情界面
                    logger.warn(u'任务的最大重试次数 %s 已过', e.count)
                    worker_db = get_worker_db(id)
                    last_state = worker_db.get('last_state', None)
                    worker_db['state'] = 'error'
                    worker_db.sync()
                    if last_state != 'error':
                        report_detail(e, worker_id=id, error=True)
                    break

            # 重试时延迟指定秒数
            time.sleep(e.delay)

            logger.info(u'开始重试任务')
        except Exception as e:
            logger.exception(u"任务运行出错")
            worker_db = get_worker_db(id)
            last_state = worker_db.get('last_state', None)
            worker_db['state'] = 'error'
            worker_db.sync()

            if HEADLESS or isinstance(e, LogicError):
                # LogicError 不弹出错误窗口
                logger.error(u'任务 %s 出错, traceback:\n%s', id, extract_traceback())
            elif is_background_task(id):
                name = {
                    "new_webfolder": "Webfolder",
                    "sync": "File Sync",
                }.get(worker_db.get('name', id))
                logger.error(u'后台任务 %s 运行异常', name)
                if last_state != 'error':
                    # 首次出错则冒泡提醒，其后出错则静默重试
                    ui_client.message(
                        title=_("Task running abnormaly"),
                        body=_("{} running error and will retry soon").format(_(name)),
                        type="warn"
                    )
            elif last_state != 'error':
                # 重试以外的其他异常，直接显示错误报告界面
                report_detail(e, worker_id=id, error=True)
            break
        except SystemExit as e:
            # 针对 Fabric 经常主动退出解释器进程，做一个修补，让任务状态与进程状态一致
            if e.code != 0:
                logger.exception(u'任务退出，返回值 %d', e.code)
            worker_db = get_worker_db(id)
            worker_db['state'] = 'error'
            worker_db.sync()
            break
        else:
            worker_db = get_worker_db(id)
            worker_db['state'] = 'finished'
            worker_db.sync()
            break

    close_logger(logger)

    # 刷新任务tab
    refresh_webview('workers', sync=sync_flag)


def run_worker(id, sync=False, pipe=None):
    '''
    完整运行一个任务的入口。
    为什么需要:
    - 所有任务都有一组相同的 pre-run & post-run 操作，例如参数检查、启动日志记录、扫尾清理等；
    注意:
    - 重试的逻辑在进程入口 safe_run_worker 中，不在这里；
    '''
    # 这些任务默认不重试
    no_retry_workers = ('script', 'online_script', )
    # 这些任务不需要报告错误
    no_report_workers = ('messaging', )

    # 本次运行日志开始
    logger = get_worker_logger(id)
    worker_db = get_worker_db(id)

    # 取出 worker 函数
    name = worker_db['name']
    func = WORKER_REG[name]['function']

    worker_db['start_time'] = datetime.utcnow().isoformat()
    worker_db['end_time'] = ''
    worker_db['result'] = {}
    worker_db.sync()
    success = None
    try:
        logger.debug(u'开始任务')
        logger.debug(
            u'桌面助手版本 %s.%s (%s)，任务ID %s，任务名 %s',
            VERSION, BUILD_NUMBER, GIT_INFO or u'Unknown', id, name
        )
        logger.debug(
            u'任务详细信息: \n\t%s',
            u'\n\t'.join([
                '{}: {}'.format(
                    k,
                    ', '.join(
                        str(i) for i in v
                    ) if isinstance(v, (list, tuple, )) else v
                )
                for k, v in filter_sensitive_fields(worker_db).items()
            ])
        )
        # 获取到实参列表并运行
        real_args = prepare_worker_args(name, id)
        # 关闭日志处理器，防止日志文件移动等操作无法完成
        close_logger(logger)
        # Monkey patch
        from libs import monkey
        monkey.patch_all()
        # 运行任务
        success = func(id, *real_args, pipe=pipe)
        # 重新获取日志处理器
        logger = get_worker_logger(id)
        worker_db = get_worker_db(id)
        # 记录运行结果
        worker_db['_result'] = json.dumps(success)

        worker_db['_reason'] = 'ok'
        if worker_db.get('executed', None) is None:
            worker_db['executed'] = True
        logger.debug(u'任务成功完成')
        worker_db.sync()
        send_worker_notify(id=id)
    except Exception as e:
        worker_db = get_worker_db(id)
        # 重新获取日志处理器
        logger = get_worker_logger(id)
        logger.exception(u'任务出错')

        worker_db.update({
            'error': extract_traceback(),
            '_reason': 'error',
        })
        worker_db.sync()

        if name not in no_report_workers:

            # 网络错误，默认无限重试
            if is_network_error(e):
                # 有些任务默认不重试，使用错误报告
                if name in no_retry_workers:
                    raise
                else:
                    worker_db['_reason'] = 'network error'
                    worker_db.sync()
                    raise Retry(count=-1, raw_error=extract_traceback())
            else:
                if isinstance(e, Retry):
                    worker_db['_reason'] = 'retry'
                    worker_db.sync()
                raise
    else:
        return success
    finally:
        logger.debug("Release locks for #%s", id)
        release_worker_locks(id, logger)
        # 关闭日志
        close_worker_logger(id)
        worker_db = get_worker_db(id)
        worker_db['end_time'] = datetime.utcnow().isoformat()
        worker_db.sync()


def release_worker_locks(wid, logger=None):
    '''
    清理属于一个 worker 的所有锁
    当 worker 运行完毕或被杀死时应当调用这个函数
    注意: 这个函数发送 HTTP 请求到主进程服务器去释放这个锁，请不要在主进程中调用这个函数
    '''
    logger = logger or get_worker_logger(wid)
    if "MainProcess" == current_process().name and has_app_context():
        # 在主进程的一个请求上下文中，可以直接操作锁
        logger.debug("Release locks in main process")
        for lock_name in current_app.LOCKS.keys():
            lock = current_app.LOCKS[lock_name]
            if str(lock['worker_id']) == str(wid):
                current_app.LOCKS.pop(lock_name)
                logger.debug(
                    '<LOCK cleanup> cleaned {} for #{}'.format(lock_name, wid)
                )
    else:
        # 不在主进程中或不在请求上下文中
        logger.debug("Release locks by HTTP request")
        retry_count = 0
        while retry_count < 3:
            try:
                _request_api(
                    '/worker/lock/release',
                    kw={'worker_id': wid},
                    internal=True, timeout=10
                )
            except Exception:
                # 主进程的监视线程会定期扫描任务，其中会清理不存在的 worker 可能残留的锁
                retry_count += 1
                logger.exception(
                    "[%d] Failed to release locks for #%s", retry_count, wid
                )
            else:
                logger.debug("locks for #%s released", wid)
                break


def send_worker_notify(id=None):
    '''
    发送一个关于 worker 的通知（给自己）
    '''
    if id is None or not get_worker(id):
        return

    worker = get_worker(id)
    work_error, silent = False, False
    # 这些状态的任务才发送消息
    matched_states = ('finished', 'error', )
    # 这些任务不论状态如何、是否出错，都不需要发送消息
    silent_workers = (
        'view', 'threedpreview', 'messaging',
        'new_webfolder', 'process_duplicate'
    )
    # 这些原因导致的错误不发送消息
    ignore_reasons = ('network', )
    # 带有这些参数的任务不发送通知
    silent_args = ('auto', )  # 自动任务不发送通知
    # 这些任务自行管理消息发送，但出错时将会由这个函数代为发送
    self_managed_workers = (
        'edit', 'upload_v2', 'upload', 'download', 'p2pdownload'
    )

    # 只在任务完成或出错时发送通知
    if worker.get('state', None) not in matched_states:
        return
    if not worker['detail'].get('pid', None):
        return

    for arg in worker['detail']:
        if arg in silent_args and worker['detail'].get(arg, '').strip():
            silent = True
            break
    if silent:
        return

    # 文件的下载查看任务和 3D 文件预览任务不发送系统通知
    # 所有任务的网络错误也不发送通知
    if worker.get('name') in silent_workers\
            or worker['detail']['_reason'] in ignore_reasons:
        return
    uids, local_paths = [], []
    if worker['detail'].get('_result', None) is None\
            or worker['state'] == 'error':
        work_error = True
        if not worker['detail'].get('error', None):
            work_error = False
        else:
            title = _('Task Error')
            data = _(
                'Task "{}" failed.'
                'You can view this task in "Task" page in Assistant.'
            ).format(
                _(get_worker_title(
                    worker.get('name'),
                    id=worker['worker_id'],
                    title=worker.get('title')
                ))
            )
    elif worker['state'] == 'finished':
        title = _('Task finished')
        data = _('Assistant: task "{}" finished').format(
            _(get_worker_title(
                worker.get('name'),
                id=worker['worker_id'],
                title=worker.get('title')
            ))
        )
        success = worker['detail']['_result']
        if success:
            uids = [
                str(i['uid'])
                for i in success if i.get('uid', None)
            ]
            if len(uids) > 10:
                uids = uids[:10]
            local_paths = [
                i['local_path']
                for i in success
                if i.get('local_path', None)
            ]
            if len(local_paths) > 10:
                local_paths = local_paths[:10]
        else:
            return

    if local_paths:
        data = _('{}. Local path is: {}').format(data, ', '.join(local_paths))
    try:
        # 这些任务自行管理系统通知的发送
        if worker.get('name') in self_managed_workers and not work_error:
            return
        else:
            if not FROZEN:
                print u'[debug notify] [to: {}] {}: {}, attachments: {}'.format(
                    worker['detail']['pid'], title, data, uids
                )
                return
            ui_client.message(title, data)
    except:
        raise


def stop_all_workers():
    '''Stop all workers'''
    # 如果退出耗时较长，很可能监视线程又会把任务重启了。所以先停止监视线程
    log.debug(u'监视线程存活状态: %s。尝试停止', DAEMON_THREAD.is_alive())
    DAEMON_THREAD_STOP_EVENT.set()
    sleep = threading.Event()
    for i in range(40):
        sleep.wait(timeout=0.05)  # 最多等待 40 * 0.05 = 2 秒
        thread_alive = DAEMON_THREAD.is_alive()
        log.debug(u'监视线程存活状态: %s', thread_alive)
        if not thread_alive:
            break

    for worker_id in PROCESSES.keys():
        process = PROCESSES.get(worker_id, None)
        # 非常罕见的情况下 workerdb 可能被破坏，缺少一些信息
        _db = get_worker_db(worker_id)

        # 任务可以通过在 workerdb 中指定 dontkillme 这个 key 来避免在桌面助手退出时被杀死
        if _db.get('dontkillme', False):
            continue
        if process is not None:
            kill_process(process)
            PROCESSES.pop(worker_id)
        # release_worker_locks(worker_id) 锁已经在 webserver 中处理了


def start_worker(id, sync=False, pipe=None):
    '''
    启动指定的任务
    同一时间对一个 uid 只能有一个任务。
    '''
    if id == 0:
        # 出现 id 为 0 的情况，说明任务重复了，不应该启动
        print u"[worker.start_worker] Duplicate task"
        return

    sync = sync or SINGLE_PROCESS
    if sync:
        return safe_run_worker(id, sync=True, pipe=pipe)
    else:
        p = PROCESSES.get(id, None)
        if p is not None and p.is_alive():
            return {
                'is_alive': p.is_alive(),
                'worker_id': id,
                'msg': _('This task is already running')
            }
        PROCESSES[id] = Process(name='worker-{}'.format(id), target=safe_run_worker, args=(id, sync, pipe))
        PROCESSES[id].start()
        return {
            'is_alive': PROCESSES[id].is_alive(),
            'worker_id': id,
            'msg': _(u'Task started')
        }


def _kill_single_process(process):
    '''
    Kill a single process in the following orders:
    - try .terminate() method of given process
    - try to send a SIGTERM signal to that process
    - try to kill it with `psutil`
    Return False if they all failed, else True.
    '''
    timeout = 5
    try:
        process.terminate()
        return True
    except:
        try:
            os.kill(process.pid, signal.SIGTERM)
            return True
        except:
            try:
                proc = psutil.Process(process.pid)
                proc.kill()
                proc.wait(timeout)
                return True
            except:
                pass
    return False


def kill_process(process):
    '''
    Safely kill a process and all of its subprocesses
    Ref: http://stackoverflow.com/a/4229404
    Args:
        process: an object with .pid attribute and .terminate method
    Return:
        True if killed successfully, else False
    '''
    pid = process.pid
    # sig = signal.SIGTERM
    timeout = 5
    # First, use psutil to kill the whole process tree
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except:
                _kill_single_process(child)
        psutil.wait_procs(children, timeout=timeout)
        _kill_single_process(process)
        return True
    except:
        return False


def terminate_worker(id):
    process = PROCESSES.get(id, None)
    if process is not None:
        kill_process(process)
        PROCESSES.pop(id, None)
    remove_worker_db(id)
    release_worker_locks(id)
    return {
        'is_alive': False,
        'worker_id': id,
        'msg': _('Task deleted')
    }


def filter_workers(**conditions):
    '''过滤出符合条件的任务ID
    例如：找出所有与路径A相符的同步任务：filter_workers(path=A, name='sync')
    '''
    def filter_by_conditions(db):
        for key, value in conditions.items():
            db_values = db.get(key, [])
            if isinstance(db_values, list) and value not in db_values:
                return False
            elif value != db_values:
                return False
        return True

    matched_worker_dbs = list(filter(
        filter_by_conditions,
        [get_worker_db(id) for id in list_worker_ids()]
    ))
    return [db.id for db in matched_worker_dbs]


def turn_off_messaging(id):
    '''
    关闭消息提醒
    '''
    db = get_worker_db(id)
    if not db:
        return
    if db.get('token', '').strip():
        logger = get_worker_logger(id)
        logger.debug(u'消息提醒关闭')
        db['token'] = ''
        db['state'] = 'paused'
        db.sync()
        close_logger(logger)


def pause_worker(id, turn_off_message=False):
    p = PROCESSES.get(id, None)
    if p is not None:
        print u'terminating {}'.format(p)
        kill_process(p)
    worker_db = get_worker_db(id)
    if worker_db.get('name') == 'messaging' and turn_off_message:
        turn_off_messaging(id)
        return {
            'is_alive': p.is_alive() if p is not None else False,
            'worker_id': id,
            'msg': _('Task paused')
        }

    worker_db['state'] = 'paused'

    worker_db.sync()
    refresh_worker_tab()
    logger = get_worker_logger(id)
    logger.debug(u'-------------------任务手动暂停-------------------')
    close_logger(logger)
    release_worker_locks(id)
    return {
        'is_alive': p.is_alive() if p is not None else False,
        'worker_id': id,
        'msg': _('Task paused')
    }


def worker_guardian():
    '''
    后台监视线程，定时扫描所有任务，并:
      - 每分钟重启网络错误的任务
      - 重启达到定时间隔的定时任务
      - 删除成功完成的任务（6 小时后）
      - 删除主动要求被删除的任务（带有 deleted 键）
    行为:
      - 每 10 秒扫描一次
      - 扫描到主动要求被删除的任务，立刻停止并删除
      - 扫描到网络错误的任务，如果结束时间过去 55 秒以上，重启
      - 成功和出错的任务，结束时间过去一周的，删除
      - 首次扫描先等待 2 秒，让服务器启动
    '''
    if HEADLESS:
        from headless_server import P2P_QUEUE
    else:
        from webserver import P2P_QUEUE
    AUTO_REMOVE_DELAY = 7 * 24 * 60 * 60
    logger = get_logger('DAEMON_THREAD', filename='daemon_thread.log', init_level=logging.INFO)
    logger.info(u'监视线程启动')

    ignore_reasons = ('retry', )
    # 严重错误不要重试
    fatal_error_reasons = ('internal', 'error', )
    # 这些任务需要确保一直运行，除非没有 token
    keep_running_workers = ('new_webfolder', )
    # 这些任务在桌面助手启动时启动一次就可以，之后不需要理会
    one_time_workers = ('', )
    require_executed = ('new_webfolder', )
    # 这些任务在桌面助手启动时清理掉，除非处于出错状态
    remove_upon_start_workers = tuple()
    # 是否是首次扫描（桌面助手启动后第一次扫描）
    first_loop = True
    self_destructed_workers = set()
    while 1:
        for wid in list(self_destructed_workers):
            logger.info(u'将删除上一次的自毁任务 %s', wid)
            self_destructed_workers.remove(wid)
            remove_worker_db(wid)
            release_worker_locks(wid)

        logger.debug(u'监视线程正在扫描所有任务')
        if first_loop:
            logger.info(u'启动后首次扫描，先等待服务器启动')
            if DAEMON_THREAD_STOP_EVENT.wait(timeout=2):
                logger.info(u'退出守护线程')
                break
        else:
            if DAEMON_THREAD_STOP_EVENT.wait(timeout=WORKER_SCAN_INTERVAL):
                logger.info(u'退出守护线程')
                break

        # 待删除的任务 ID
        pending_removal_workers = []

        for wid in list_worker_ids():
            work = get_worker_db(wid)
            if not work:
                logger.warn(u'任务（ID: %s）可能已经损坏，将删除', wid)
                pending_removal_workers.append(wid)
                continue

            try:
                name = work.get('name', None)
                no_token = not work.get('token', '').strip()
                state = work.get('state')
                error_reason = work.get('_reason', None)
                self_destructed = work.get('deleted', False)
                try:
                    last_start_time = utc_to_timestamp(work.get('start_time'))
                except:
                    last_start_time = 0
                is_auto_start = work.get('auto', False)
                is_auto_start = bool(is_auto_start.strip()) if isinstance(
                    is_auto_start, (str, unicode)
                ) else is_auto_start
                executed = work.get('executed', False)
                if name in keep_running_workers:
                    is_residential = True
                else:
                    is_residential = work.get('residential', False)
                    is_residential = bool(
                        is_residential.strip()
                    ) if isinstance(
                        is_residential, (str, unicode)
                    ) else is_residential
                now = time.time()
                _work_process = PROCESSES.get(wid, None)
                work_process_running = _work_process and _work_process.is_alive()
            except:
                logger.warn(u'监视线程扫描出错', exc_info=True)
                continue

            # 没有 token 不能运行
            # 任务自身请求删除
            if self_destructed:
                logger.info(u'%s 任务（ID: %s）主动要求被删除，进入删除队列', name, wid)
                self_destructed_workers.add(wid)
                continue
            if no_token and name not in one_time_workers:
                logger.debug(u'%s 任务（ID: %s）没有 token，略过', name, wid)
                continue

            # 有些任务要求至少成功运行一次后才能由监视线程启动
            if name in require_executed and not executed:
                continue

            if first_loop:
                if name in remove_upon_start_workers and state != 'error':
                    pending_removal_workers.append(wid)
                    logger.info(u'启动后首次扫描，删除了上次运行的 %s 任务（ID: %s）', name, wid)
                    continue
                if name in one_time_workers:
                    start_worker(wid, pipe=P2P_QUEUE)
                    logger.info(u'启动后首次扫描，启动了一次性的 %s 任务（ID: %s）', name, wid)
                    continue
                if state == 'running':
                    start_worker(wid, pipe=P2P_QUEUE)
                    logger.info(
                        u'启动后首次扫描，启动了上次退出时正在运行的 %s 任务（ID: %s）', name, wid
                    )
                    continue
            # 只需要桌面助手启动时启动一次就好了，之后不理会
            if name in one_time_workers:
                continue
            # 保持运行的任务
            if is_residential and state != 'paused' and (state != 'running' or not work_process_running):
                start_worker(wid, pipe=P2P_QUEUE)
                logger.info(u'启动了驻留任务 %s（ID: %s）', name, wid)
                continue
            # 定时任务
            if is_auto_start:
                try:
                    interval = int(work.get('interval', None))
                except:
                    interval = AUTO_START_INTERVAL
                if now - last_start_time >= interval and not work_process_running:
                    start_worker(wid, pipe=P2P_QUEUE)
                    logger.debug(
                        u'启动了定时运行的 %s 任务（ID: %s），距上次运行已过去 %s 秒',
                        name, wid, now - last_start_time
                    )
                    continue
            # 出错但可以重试的任务
            if state == 'error'\
                    and error_reason not in fatal_error_reasons\
                    and error_reason not in ignore_reasons\
                    and now - last_start_time >= RETRY_INTERVAL:
                start_worker(wid, pipe=P2P_QUEUE)
                logger.debug(
                    u'启动了上次出错的 %s 任务（ID: %s），距上次运行已过去 %s 秒',
                    name, wid, now - last_start_time
                )
                continue
            # 成功和出错的任务，一周后删除
            if state in ('finished', 'error'):
                try:
                    last_finish_time = utc_to_timestamp(work.get('end_time')) or time.time()
                except:
                    last_finish_time = time.time()
                if now - last_finish_time > AUTO_REMOVE_DELAY:
                    pending_removal_workers.append(wid)
                    logger.info(
                        u'%s 任务（ID: %s）%s已超过一周，将被删除',
                        name, wid, u'完成' if state == 'finished' else u'出错'
                    )
        if first_loop:
            first_loop = False

        # 删除待删除的任务
        for wid in pending_removal_workers:
            remove_worker_db(wid)
            release_worker_locks(wid)


def load_workers():
    '''桌面助手启动时，加载上次的任务
    处理逻辑：
    - 驻留任务：由监视线程启动；
    - 定时任务：由监视线程处理；
    - 映射盘：如果没有出现内部错误，就启动任务；  # TODO v7.2.1 映射盘改为使用「驻留任务」实现
    - 升级：处于运行状态的话就删除；
    - 其他任务，额外判断上次状态：
      - 暂停、出错：不处理；
      - prepare：启动任务；
      - 运行：暂停任务；
      - 其他：删除任务；
    '''

    def is_upgrade_task(work):
        return work['name'] == 'online_script' and \
            work.get('script_name') in ('zopen.assistant:ast_arch_upgrade', 'zopen.assistant.ast_arch_upgrade')  # noqa E501

    pending_removal_workers = []

    # 加载之前保存的任务
    for id in list_worker_ids():
        work = get_worker_db(id)

        if not work or not work.get('name'):
            remove_worker_db(id)
            continue

        if work.get('residential', False):
            continue
        elif work.get('auto', False):
            continue
        elif work['name'] == 'new_webfolder' and work.get('_reason') != 'internal':  # noqa E501
            continue
        else:
            last_state = work.get('state')
            if last_state in ('paused', 'error'):
                continue
            elif last_state == 'prepare':
                continue
            elif last_state == 'running':
                if is_upgrade_task(work):
                    pending_removal_workers.append(id)
                else:
                    work['state'] = 'paused'
                    work.sync()
            else:
                pending_removal_workers.append(id)
                continue

    # 收集需要清理的遗留日志（日志对应的 worker_db 已经不存在）
    for log_file in os.listdir(LOG_DATA):
        if not os.path.isfile(os.path.join(LOG_DATA, log_file)):
            continue

        # 从 worker_2.log 或 worker_3.log.1 文件名中取出任务的id
        if log_file.startswith('worker_') and (
            log_file.endswith('.log') or log_file.endswith('.log.1')
        ):
            name, _ = os.path.splitext(
                log_file if log_file.endswith('.log') else log_file[:-2]
            )
            try:
                id = int(name.split('_')[-1])
            except ValueError:
                continue
            else:
                if not get_worker_db(id):
                    pending_removal_workers.append(id)

    [remove_worker_db(i) for i in set(pending_removal_workers)]
    # 尝试清理一下旧版本的shell扩展
    if sys.platform == 'win32' and not HEADLESS:
        from utils.win32_utils import cleanup_old_syncplugins
        cleanup_old_syncplugins()

    # 启动监视线程
    global DAEMON_THREAD, DAEMON_THREAD_STOP_EVENT
    DAEMON_THREAD_STOP_EVENT = threading.Event()
    DAEMON_THREAD = threading.Thread(target=worker_guardian)
    DAEMON_THREAD.daemon = True
    DAEMON_THREAD.start()


def refresh_worker_tab():
    if HEADLESS:
        return
    else:
        from qtui.ui_utils import emit_webview_refresh_signal
        emit_webview_refresh_signal('workers')
