# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import re
import shutil
import string
import locale
import calendar
import hashlib
import urlparse
import gettext
import traceback
import platform
import logging
import logging.config
import logging.handlers
from dateutil import tz
from functools import wraps
from datetime import datetime, timedelta

import config
from config import CURRENT_DIR, APP_DATA, LOG_DATA, RUNTIME_DIR, INTERNAL_URL
from edo_client import (
    WoClient, OcClient, MessageClient, UploadClient, OrgClient,
)
try:
    from edo_client.client import DEFAULT_TIMEOUT as DEFAULT_HTTP_TIMEOUT
except ImportError:
    DEFAULT_HTTP_TIMEOUT = 15

from flask import (
    request, current_app,
    make_response,
    redirect, g as flask_g,
)
from werkzeug.datastructures import MultiDict
import requests
import jinja2
import getpass
import psutil

PUBLIC_KEY_CACHE = None

log = logging.getLogger(__name__)


def load_logging_config(worker_id=None):
    '''从配置文件（edo_assistent/logging.json）读取并应用日志设置。
    出错则使用默认设置'''
    config_file = os.path.join(APP_DATA, 'logging.json')
    # 来自任务的log请求，记录到任务日志文件中
    if worker_id:
        log_fname = 'worker_{}.log'.format(worker_id)
    else:
        log_fname = 'webserver.log'

    try:
        with open(config_file) as rf:
            config_content = rf.read()

        config_content = string.Template(config_content).safe_substitute(
            fname=json.dumps(os.path.join(LOG_DATA, log_fname))[1:-1],  # 去除引号
        )
        logging.config.dictConfig(json.loads(config_content))
    except Exception:
        pass


def get_logger(
    module_name, filename=None, to_console=True,
    init_level=logging.DEBUG, size=300,
    fmt=None, datefmt=None
):
    '''快速创建一个 logger'''
    logger = logging.getLogger(module_name)
    # Prevent log records from being handled by handlers of parent loggers
    logger.propagate = 0
    logger.setLevel(init_level)

    if not fmt:
        fmt = '%(asctime)s %(threadName)s(%(thread)s) %(levelname).1s (%(filename)s:L%(lineno)d) %(name)s: %(message)s'  # noqa: E501
    if not datefmt:
        datefmt = '%Y-%m-%d %H:%M:%S'

    if to_console and not any(map(lambda h: isinstance(h, logging.StreamHandler), logger.handlers)):
        consoleHandler = logging.StreamHandler()
        formatter = logging.Formatter(fmt, datefmt=datefmt)
        consoleHandler.setFormatter(formatter)
        consoleHandler.setLevel(init_level)
        logger.addHandler(consoleHandler)

    from config import LOG_DATA

    # If FileHandler with the same filepath already attached, do nothing;
    # Else close all other FileHandlers and attach newly created one.
    if filename is not None:
        fhandler_attached = False
        file_path = os.path.join(LOG_DATA, filename)
        for handler in logger.handlers[:]:
            if isinstance(handler, logging.FileHandler):
                if handler.baseFilename == file_path:
                    fhandler_attached = True
                    continue
                else:
                    handler.close()
                    logger.removeHandler(handler)
        if not fhandler_attached:
            fhandler = logging.handlers.RotatingFileHandler(
                os.path.join(LOG_DATA, filename),
                maxBytes=size*1024,
                backupCount=1,
                delay=1  # Delay .open() operation on file
            )
            fhandler.setFormatter(
                logging.Formatter('%(asctime)s (L%(lineno)d) %(threadName)s %(levelname)s %(message)s')
            )
            fhandler.setLevel(init_level)
            logger.addHandler(fhandler)
    return logger


def detect_locale(language_code=None):
    '''
    Detect full language code from given code
    Return:
        (language_code, full_language_code) e.g.: ('zh_TW', 'zh_Hant_TW')
    '''
    from config import I18N_DIR

    logger = get_logger('utils.detect_locale')
    # Disable this logger
    logger.setLevel(logging.CRITICAL)
    available_languages = [
        i for i in os.listdir(I18N_DIR)
        if os.path.isdir(os.path.join(I18N_DIR, i))
    ]
    prefered_language = language_code or locale.getdefaultlocale(
        envvars=('LANGUAGE', 'LANG', 'LC_ALL', 'LC_CTYPE')
    )[0]
    # Fallback to general English
    selected_language = 'en'
    logger.debug(
        u'可用的语言: %s; 期望的语言: %s; fallback: %s',
        available_languages, prefered_language, selected_language
    )
    if prefered_language is not None:
        sl_parts = prefered_language.split('_')
        for lang in available_languages:
            al_parts = lang.split('_')
            if len(al_parts) == 3:
                lang_without_script = [al_parts[0], al_parts[2], ]
            else:
                lang_without_script = al_parts[:]
            if len(sl_parts) == 3:
                sl_parts = [sl_parts[0], sl_parts[2], ]
            if sl_parts == lang_without_script\
                    or lang.startswith(prefered_language):
                selected_language = lang
                break
    logger.debug(u'最终选择的语言: %s', selected_language)
    return prefered_language, selected_language


def get_translation(language=None):
    '''
    Return a translation in given language, Use its `.ugettext` to translate
    '''
    from config import I18N_DIR
    logger = get_logger('utils.get_translation')
    lang = detect_locale(language)[-1]
    try:
        return gettext.translation(
            'messages',
            localedir=I18N_DIR,
            languages=[lang]
        )
    except:
        logger.warn(
            u'加载 %s (full: %s) 语言的翻译文件失败',
            language, lang,
            exc_info=True
        )
        return gettext.NullTranslations()


_ = translate = get_translation().ugettext


def is_internal_call(request=None):
    from config import APP_ID
    if request is None:
        from flask import request
    return request.headers.get('caller', None) == APP_ID[:12]


def time_profile(func):
    @wraps(func)
    def debug_time_profile(*args, **kwargs):
        time_start = time.time()
        result = None
        try:
            result = func(*args, **kwargs)
        except:
            log.exception('time profile error')
        finally:
            time_end = time.time()
        log.info(
            u'耗时分析：函数 %s 耗时 %.3f 秒', func.func_name, (time_end - time_start)
        )
        return result
    return debug_time_profile


def extract_traceback():
    e_type, _, _ = sys.exc_info()
    return u'{}: {} \n{}'.format(
        datetime.strftime(datetime.now(), '%H:%M:%S'),
        e_type,
        traceback.format_exc().decode('utf-8')
    )


def extract_data(arg_names, request=None):
    '''从 request 中取出参数'''
    if request is None:
        from flask import request
    params = MultiDict(urlparse.parse_qsl(request.data))
    if isinstance(arg_names, (str, unicode, )):
        return request.form.get(
            arg_names,
            request.args.get(
                arg_names,
                params.get(arg_names, None)
            )
        )
    data = []
    for arg in arg_names:
        data.append(
            request.form.get(
                arg,
                request.args.get(arg, params.get(arg, None))
            )
        )
    return tuple(data)


def extract_data_list(arg_names, request=None):
    '''
    从 request 中取出所有参数，并将以下参数统一当作多值参数处理:
    - arg_names 指定的参数
    - 名字以 [] 结尾的参数
    '''
    # 避免取出远程访问 token
    skip_args = ('manager_token', )
    kw = {}
    if request is None:
        from flask import request
    params = MultiDict(urlparse.parse_qsl(request.data))
    if isinstance(arg_names, (str, unicode, )):
        arg_names = [arg_names]
    # Fix for HTTP 'bag' parameters
    # 暂时兼容两种请求方式
    if len(request.form) > 0:
        for key in request.form.keys():
            if key in skip_args:
                continue
            if key.endswith('[]') or key in arg_names:
                kw[key.replace('[]', '')] = request.form.getlist(key)
            else:
                _values = request.form.getlist(key)
                if len(_values) == 1:
                    kw[key] = _values[0]
                else:
                    kw[key] = _values
    elif len(request.args) > 0:
        for key in request.args.keys():
            if key in skip_args:
                continue
            if key.endswith('[]') or key in arg_names:
                kw[key.replace('[]', '')] = request.args.getlist(key)
            else:
                kw[key] = request.args.get(key)
    else:
        for key in params.keys():
            if key in skip_args:
                continue
            if key.endswith('[]') or key in arg_names:
                kw[key.replace('[]', '')] = params.getlist(key)
            else:
                kw[key] = params.get(key)
    return kw


# Taken from:  https://gist.github.com/1094140
def jsonp(func):
    '''
    Wraps JSONified output for JSONP requests.
    '''
    @wraps(func)
    def decorated_function(*args, **kwargs):
        callback = request.form.get(
            'callback', request.args.get('callback', None)
        )
        if callback:
            data = str(func(*args, **kwargs))
            content = '{}({})'.format(str(callback), data)
            mimetype = 'application/javascript'
            resp = current_app.response_class(content, mimetype=mimetype)
        else:
            resp = func(*args, **kwargs)
        resp = make_response(resp)
        resp.headers['Access-Control-Allow-Origin'] = "*"
        resp.headers['Access-Control-Allow-Headers'] = ','.join([
            'Origin', 'X-Requested-With', 'Content-Type', 'Accept',
        ])
        return resp
    return decorated_function


def domain_check(func):
    '''
    检测域名是否允许访问站点机器人
    '''
    from config import ALLOW_DOMAIN

    @wraps(func)
    def check(*args, **kwargs):
        if "*" not in ALLOW_DOMAIN:
            if 'Referer' not in request.headers:
                return redirect('/admin/worker', code=302)
            if request.headers['Referer'] not in ALLOW_DOMAIN:
                return redirect('/admin/worker', code=302)
        return func(*args, **kwargs)
    return check


def kwargs_check(keys=None):
    def _wrapper(func):
        def wrapper(*args, **kwargs):
            for k, v in kwargs.items():
                if k not in keys or v is None:
                    kwargs.pop(k)
            if len(kwargs) < 1:
                raise TypeError(
                    u'Too few keyword args ({}) for `{}`'.format(
                        len(kwargs), func.func_name
                    )
                )
            return func(*args, **kwargs)
        return wrapper
    return _wrapper


def get_file_md5(filepath):
    '''计算指定文件的 MD5 值'''
    return get_file_hash(filepath, 'md5')


def get_filesize(path):
    """give the file path on the local machine"""
    return os.stat(path).st_size


def get_fobjsize(fileobj):
    """get the size of the file object"""
    cur = fileobj.tell()
    fileobj.seek(0, os.SEEK_END)
    filesize = fileobj.tell()
    fileobj.seek(cur, os.SEEK_SET)
    return filesize


def save_file(filepath, response):
    '''
    从requests.models.Response对象中保存文件，同时计算并返回 MD5 值
    '''
    tempfile_path = '.{}.part'.format(os.path.basename(filepath))
    tempfile_path = os.path.join(os.path.dirname(filepath), tempfile_path)
    hash_obj = hashlib.md5()
    with open(tempfile_path, 'wb') as f:
        for block in response.iter_content(chunk_size=1024):
            if not block:
                break
            f.write(block)
            f.flush()
            hash_obj.update(block)
    shutil.move(tempfile_path, filepath)
    return hash_obj.hexdigest()


def get_wo_client(token=None, server=None, account=None, instance=None):
    # 连接服务器
    from config import APP_KEY, APP_SECRET
    wo_client = WoClient(
        server, APP_KEY, APP_SECRET,
        account=account,
        instance=instance,
        timeout=DEFAULT_HTTP_TIMEOUT,
    )
    wo_client.auth_with_token(token)
    return wo_client


def get_org_client(token=None, server=None, account=None, instance=None):
    from config import APP_KEY, APP_SECRET
    org_client = OrgClient(
        server, APP_KEY, APP_SECRET,
        account=account, instance=instance, timeout=DEFAULT_HTTP_TIMEOUT,
    )
    org_client.auth_with_token(token)
    return org_client


def get_message_client(token=None, server=None, account=None, instance=None):
    from config import APP_KEY, APP_SECRET
    message_client = MessageClient(
        server, APP_KEY, APP_SECRET,
        account=account, instance=instance, timeout=DEFAULT_HTTP_TIMEOUT,
    )
    message_client.auth_with_token(token)
    return message_client


def get_upload_client(token=None, server=None, account=None, instance=None):
    from config import APP_KEY, APP_SECRET
    upload_client = UploadClient(
        server, APP_KEY, APP_SECRET,
        account=account, instance=instance, timeout=DEFAULT_HTTP_TIMEOUT,
    )
    upload_client.auth_with_token(token)
    return upload_client


def get_worker_logger(id, size=300, level=logging.DEBUG):
    '''
    获取一个 worker 的日志记录器
    将会限制日志行数，默认是 100Kb
    level 只影响文件处理器的日志级别
    '''
    module_name = 'worker_{}'.format(id)
    file_path = '{}.log'.format(module_name)
    return get_logger(
        module_name, file_path, to_console=True, init_level=level, size=size
    )


def close_logger(logger):
    '''
    Close all handlers of given logger.
    '''
    for handler in logger.handlers[:]:
        if getattr(handler, 'close', None) is not None:
            handler.close()
        logger.removeHandler(handler)


def close_worker_logger(id):
    '''
    Properly close all handlers of logger of given worker.
    '''
    close_logger(logging.getLogger('worker_{}'.format(id)))


def is_valid_dir(folder):
    '''
    检查指定文件夹是否合法
    合法的定义是满足以下所有条件:
    - 路径存在
    - 可读
    - 可写
    - 可进入文件夹/列出内容（X 权限）
    - 不是 Unix 隐藏文件（不以 . 开头）
    Args:
        folder <String> 文件夹路径
    Returns:
        <Boolean> 合法返回 True 否则返回 False
    '''
    basename = os.path.basename(folder)
    return os.path.isdir(folder) and not basename.startswith('.') and\
        os.access(folder, os.R_OK | os.W_OK | os.X_OK)




def utc_to_local(utc_dt):
    '''
    将 UTC 时间转换为本地时间
    '''
    if isinstance(utc_dt, (str, unicode, )):
        if not utc_dt:
            return ''
        try:
            utc_dt = datetime.strptime(utc_dt, '%Y-%m-%dT%H:%M:%S.%f')
        except Exception as e:
            if isinstance(e, ValueError):
                utc_dt = datetime.strptime(utc_dt, '%Y-%m-%dT%H:%M:%S')
            else:
                return ''
    return get_human_ltime(utc_dt)


def utc_to_timestamp(utc_dt):
    '''
    将 UTC 时间转换为 Unix 时间戳
    '''
    if isinstance(utc_dt, (str, unicode, )):
        if not utc_dt:
            return 0
        try:
            utc_dt = datetime.strptime(utc_dt, '%Y-%m-%dT%H:%M:%S.%f')
        except Exception:
            try:
                utc_dt = datetime.strptime(utc_dt, '%Y-%m-%dT%H:%M:%S')
            except Exception:
                return 0
            return calendar.timegm(utc_dt.timetuple())
    return calendar.timegm(utc_dt.timetuple())


def utc_format(utc_str, formatter="%Y-%m-%d %H:%M:%S"):
    if utc_str == '':
        return ''
    timestamp = utc_to_timestamp(utc_str)
    return datetime.fromtimestamp(timestamp).strftime(formatter)


def search_dict_list(l, pair={}):
    result = []
    for i in l:
        match = True
        for k, v in pair.items():
            if i.get(k, None) == v:
                continue
            else:
                match = False
                break
        if match:
            result.append(i)
    return result


def get_numbered_path(path):
    '''
    对相同路径内的重复文件进行编号，获取到不重复的文件名
    Args:
        path <String> 文件路径
    Returns:
        <String> 编号后的文件路径
    '''
    while os.path.exists(path):
        path = get_numbered_filename(path)
    return path


def construct_server(hostname, port=None, scheme='http'):
    '''
    创建一个 URL
    '''
    parse_result = urlparse.urlparse(hostname)
    _scheme = getattr(parse_result, 'scheme', scheme)
    _port = getattr(parse_result, 'port', port)
    _hostname = getattr(parse_result, 'hostname', hostname)
    if _scheme is None or _scheme == '':
        _scheme = scheme
    if _port is None or _port == '':
        if port is not None:
            _port = port
        elif _scheme == 'http':
            _port = 80
        elif _scheme == 'https':
            _port = 443
    if _hostname is None or _hostname == '':
        _hostname = hostname
    return '{}://{}:{}'.format(_scheme, _hostname, str(_port))




def fobj_from_md(md):
    '''
    从服务端查询的元数据中构造一个通用的字典
    '''
    if 'File' in md['object_types']:
        object_type = 'file'
    elif 'Folder' in md['object_types']:
        object_type = 'folder'
    return {
        'object_type': object_type,
        'local_path': '',
        'server_path': md['path'],
        'revision': md['revision'],
        'uid': md['uid']
    }


def clear_logs():
    '''
    清理过多的日志文件
    '''
    pass



def get_oc_client(oc_server=None, account=None, instance=None, token=None):
    from config import APP_KEY, APP_SECRET
    client = OcClient(
        oc_server, APP_KEY, APP_SECRET,
        account=account, instance=instance, timeout=DEFAULT_HTTP_TIMEOUT,
    )
    if token:
        client.auth_with_token(token)
    return client


def verify_request_token(request):
    '''检查请求是否有 token 授权'''
    TOKEN = os.getenv('MANAGER_TOKEN')
    request_token = extract_data('manager_token', request=request)
    cookie_token = request.cookies.get('manager_token', None)
    request_verified = TOKEN is not None \
        and (request_token or cookie_token) == TOKEN

    # cookie 中没有 token 信息，但本次通过了 token 验证；可能的情况是
    # 「用户通过带 token 的链接首次访问」，写入一个 token cookie，让用户下次可以使用 cookie 验证
    if request_verified and cookie_token is None:
        flask_g.cookies = {
            'manager_token': (TOKEN, 604800),  # 1 week
        }
    return request_verified


def addr_check(func):
    '''
    检查请求的来源地址，只允许以下情况的访问:
    - 静默模式，允许任何来源带有 TOKEN 的请求
    - 只允许列表中的来源请求（目前列表中只包含本地回环地址）
    '''
    from config import ALLOW_REMOTE

    @wraps(func)
    def check(*args, **kwargs):
        # 两种情况下允许请求通行：
        # 1. 请求来源位于白名单中
        remote_allowed = request.remote_addr in ALLOW_REMOTE
        # 2. 请求携带有效的远程访问 token
        request_verified = verify_request_token(request)
        flask_g.request_verified = request_verified

        if remote_allowed or request_verified:
            # OPTIONS 一般是浏览器执行跨域请求时做的预先检查，只要返回空响应即可
            if request.method == 'OPTIONS':
                return jsonp(lambda: '')()
            else:
                # 调用实际的 view 函数
                return func(*args, **kwargs)
        else:
            return json.dumps({
                'success': False,
                'msg': _(
                    'Permission denied. Please visit via loopback address.'
                )
            }), 403

    return check


def unify_path(path):
    '''
    FIx CJK chars in file / folder path
    '''
    try:
        return path.decode(sys.getfilesystemencoding())
    except:
        return path


def compare_dicts(da, db, keys=None):
    '''
    Compare if each key in given `keys` have the same value in both dict
    If no key was given, compare all the exsiting keys
    '''
    keys = (keys, ) if isinstance(keys, (str, unicode, )) else keys
    if keys is None:
        if len(da) != len(db):
            return False
        for k in da.keys():
            if k not in db:
                return False
            try:
                if da[k] == db[k]:
                    continue
                return False
            except KeyError:
                return False
        return True
    for k in keys:
        if k not in da:
            if k in db:
                return False
            continue
        if k not in db:
            return False
        try:
            if da[k] == db[k]:
                continue
            return False
        except KeyError:
            return False
    return True


def console_message(title, body):
    """show message in console
        this methos is used when start in quiet mode

        --title message title
        --body  message body
    """
    log.info(
        u'console_message:\ntitle: %s\nbody: %s', unicode(title), unicode(body)
    )


def icon_message(title, body, type):
    try:
        current_app.trayIcon.message(
            unicode(title),
            unicode(body),
            type=(type or 'info').decode('utf-8')
        )
    except AttributeError:
        console_message(
            unicode(title),
            unicode(body),
        )


def getmtime(file_path):
    '''
    `os.path.getmtime` alternative
    Will fix mtime of file if it's illegal, atime will also be touched.
    '''
    try:
        mtime = os.path.getmtime(file_path)
    except WindowsError as e:
        errno = getattr(e, 'winerror', None)
        if errno:
            mtime = time.time()
        else:
            raise

    try:
        datetime.utcfromtimestamp(mtime).isoformat()
        return mtime
    except ValueError:
        try:
            os.utime(file_path, None)
            return getmtime(file_path)
        except:
            return 0


def get_iso_mtime(file_path):
    '''
    Return ISO format mtime string of file
    '''
    return datetime.utcfromtimestamp(getmtime(file_path)).isoformat()


def get_deal_time(dates):
    """get the latest time from a tuple of time stamp"""
    dts = []
    for date in dates:
        try:
            date and dts.append(datetime.strptime(date, '%Y-%m-%dT%H:%M:%S'))
        except ValueError:
            dts.append(datetime.strptime(date, '%Y-%m-%dT%H:%M:%S.%f'))

    latest = dts[0] if dts else datetime.today()
    for dt in dts:
        if dt > latest:
            latest = dt
    # convert utc time to local time
    from_zone = tz.tzutc()
    to_zone = tz.tzlocal()
    latest = latest.replace(tzinfo=from_zone)
    latest = latest.astimezone(to_zone)
    return latest.strftime('%Y-%m-%d %H:%M:%S')


def get_human_ltime(dt):
    '''
    Return human readable local time string for given datetime object.
    '''
    try:
        # Stupid way to convert UTC time object to local time object
        # Would like to use python-dateutil, but not before they
        # fix their encoding bug on Windows platform.
        # See http://stackoverflow.com/a/19238551
        now_timestamp = time.time()
        offset = datetime.fromtimestamp(now_timestamp) - \
            datetime.utcfromtimestamp(now_timestamp)
        local_dt = dt + offset
        # Generate time format based on the difference of time
        local_dt_now = datetime.now()
        fstring = u'%H:%M:%S'
        if local_dt_now.year != local_dt.year:
            fstring = u'%Y-%m-%d ' + fstring
        elif local_dt_now.month != local_dt.month:
            fstring = u'%m-%d ' + fstring
        else:
            if local_dt_now.day - local_dt.day > 1:
                fstring = u'%m-%d ' + fstring
            elif local_dt_now.day - local_dt.day == 1:
                fstring = u' '.join([_('Yesterday'), fstring])
        return local_dt.strftime(fstring)
    except:
        return ''


def get_human_mtime(path=None, timestamp=None):
    '''
    Return human readable mtime string of file
    '''
    if path and os.path.exists(path):
        return get_human_mtime(timestamp=getmtime(path))
    return get_human_ltime(datetime.utcfromtimestamp(timestamp))


def get_numbered_filename(name):
    '''
    E.g.: file.txt => file-1.txt; file-1.txt => file-2.txt
    '''
    divider = '__'
    parts = list(os.path.splitext(name))
    name_parts = parts[0].split(divider)
    if len(name_parts) == 1:
        name_parts.append('0')
    try:
        index = int(name_parts[-1])
        name_parts[-1] = str(index + 1)
    except:
        name_parts.append('1')
    parts[0] = divider.join(name_parts)
    return ''.join(parts)


def notify_duplicate_item(
    oc_server, wo_client, folder_uid, original_name, uid, pid, logger=None, type='file'
):
    logger = logger or get_logger('utils')
    try:
        parent_folder = wo_client.content.properties(uid=folder_uid)
        old_uid = wo_client.content.properties(
            path='/'.join([parent_folder['path'], original_name])
        )['uid']
        lang = get_org_client(
            wo_client.token_code, oc_server,
            account=wo_client.account_name,
            instance=wo_client.instance_name
        ).org.get_objects_info(
            objects=['person:{}'.format(pid.replace('users.', ''))]
        )[0].get('lang', None)
        logger.debug(u'用户 %s 的首选语言是 %s', pid, lang)
        # babel extract hint
        _('File with the same name (see attachments) already exists')
        _('Folder with the same name (see attachments) already exists')
        _('Item with the same name (see attachments) already exists')
        types = {
            'file': 'File with the same name (see attachments) already exists',
            'folder': 'Folder with the same name (see attachments) already exists',
        }
        text = get_translation(lang).ugettext(
            types.get(type, 'Item with the same name (see attachments) already exists')
        )
        wo_client.content.notify(
            uid=uid,
            body=text,
            methods=['message'],
            to_pids=[pid, ],
            exclude_me=False,
            attachments=[str(old_uid)]
        )
    except:
        logger.warn(
            u'文件重名通知失败, 新上传的文件: "%s", 已有文件: "%s"',
            uid, original_name, exc_info=True
        )


def get_site_public_key(wo_server, account, instance, token=None):
    '''
    Get public key of given site, cache enabled
    '''
    from config import APP_DATA

    # Prepare cache_key for cache searching
    key_cache_file = os.path.join(APP_DATA, '.know_keys')
    cache_key = '.'.join([wo_server, account, instance, ])

    # Load cache into memory from disk if necessary
    global PUBLIC_KEY_CACHE
    if PUBLIC_KEY_CACHE is None:
        # Only use cache in release bundle, not for source code mode
        PUBLIC_KEY_CACHE = {}
    public_key = PUBLIC_KEY_CACHE.get(cache_key, None)

    # Cache missed, grab public key via API, and update cache
    if public_key is None:
        print u'Public key cache missed for {}'.format(cache_key)
        wo_client = get_wo_client(
            token=token, server=wo_server, account=account, instance=instance
        )
        public_key = wo_client.content.get_site_public_key()['public_key']
        PUBLIC_KEY_CACHE[cache_key] = public_key
        with open(key_cache_file, 'w') as wf:
            json.dump(PUBLIC_KEY_CACHE, wf)
            print u'Public key cache updated'
    else:
        print u'Public key cache hit for {}'.format(cache_key)

    return public_key


def repr_md(md):
    '''Represent an object by a string, constructed from its metadata'''
    return u'<{} "{}" (uid: {}; mime: {})>'.format(
        ','.join(md.get('object_types', [])), md.get('name'),
        md['uid'], md.get('content_type', 'unknown')
    )


def get_human_size(size):
    '''Return human readable format of {size} bytes'''
    suffix = 'B'
    for unit in ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', ]:
        if abs(size) < 1024.0:
            return "{:3.1f} {}{}".format(size, unit, suffix)
        size /= 1024.0
    return "{:.1f} {}{}".format(size, 'Y', suffix)



def get_editing_worker(uid=None):
    '''
    按条件找到某个正在运行的外部编辑任务
    '''
    import worker
    for wid in worker.list_worker_ids():
        wdb = worker.get_worker_db(wid)
        if wdb.get('state', None) == 'running'\
                and wdb.get('name', None) == 'edit'\
                and str(uid) in str(wdb.get('uid', [])):
            return wdb
    return None


def filter_sensitive_fields(d):
    '''
    从一个字典中滤除敏感字段
    '''
    from config import NOUNCE_FIELD
    sensitive_fields = ('token', 'password', NOUNCE_FIELD, )
    sensitive_header = 'enc_'
    for k in d.keys():
        if k in sensitive_fields or k.startswith(sensitive_header):
            d[k] = '*' * len(d[k])
    return d


def get_human_timedelta(delta):
    '''Format a readable string out of given datetime.timedelta object'''
    d, h, m, s = (
        delta.days, delta.seconds // 3600,
        (delta.seconds // 60) % 60, delta.seconds % 60
    )
    result = '{:02}:{:02}'.format(m, s)
    if h > 0:
        result = '{:02}:'.format(h) + result
    if d > 0:
        result = '{:02}:'.format(d) + result
    return result


def metadata_by_shortcut(wo_client, uid=None, metadata=None, **kwargs):
    '''通过快捷方式信息查询到原始文件信息'''
    uid = uid or metadata.get('uid', None)
    original_file_uid = None
    if uid is None:
        raise ValueError(u'没有足够的信息')
    metadata = wo_client.content.properties(uid=uid, fields=["source"], **kwargs)
    original_file_uid = metadata['fields'].get('source', None)
    if original_file_uid is None:
        return None
    return wo_client.content.properties(uid=original_file_uid, **kwargs)


def get_time_range(rtype, use_timestamp=False):
    '''
    rtype: today / yesterday / this_week / last_week
    '''
    if rtype == 'today':
        delta = timedelta(days=1)
        start = datetime.now().date()
        end = start + delta
    elif rtype == 'yesterday':
        delta = timedelta(days=1)
        end = datetime.now().date()
        start = end - delta
    elif rtype == 'this_week':
        # Monday as starting workday
        end = datetime.now().date()
        delta = timedelta(days=-end.weekday(), weeks=0)
        start = end + delta
    elif rtype == 'last_week':
        today = datetime.now().date()
        delta_s = timedelta(days=-today.weekday(), weeks=-1)
        delta_e = timedelta(days=-today.weekday(), weeks=0)
        start = today + delta_s
        end = today + delta_e

    if use_timestamp:
        epoch = datetime(1970, 1, 1).date()
        return (start - epoch).total_seconds(), (end - epoch).total_seconds()
    return (start, end)




def classify_exception(e):
    '''
    将异常处理为格式化后的 dict
    Args: e <Exception | pythoncom.com_error>
    Returns: formatted <dict>: includes the following keys:
        - code <int>: 错误码
        - msg <str | unicode>: 简略错误信息
        - detail <str | unicode>: 完整的信息
    '''
    from errors import SitebotException
    from edo_client import ApiError
    from socket import error as socket_error
    from requests.exceptions import (
        ConnectionError, HTTPError, RequestException,
        SSLError, Timeout, TooManyRedirects,
    )
    from oss2.exceptions import RequestError as OSS2_RequestError

    default_msg = '未知错误'
    general_network_exceptions = (
        ConnectionError, HTTPError, RequestException,
        SSLError, Timeout, TooManyRedirects,
        OSS2_RequestError,
        socket_error,
    )
    if isinstance(e, general_network_exceptions):
        formatted = {
            'code': '',  # TODO refine error code
            'msg': '网络错误',
            'detail': str(e),
        }
    elif isinstance(e, ApiError):
        msg = {
            111: '网络错误',
            500: '服务器错误',
            400: '客户端错误',
            403: '拒绝访问',
            404: '地址有误或文件不存在',
            405: '无权移动到目标文件夹',
            410: '版本冲突',
            409: '文件已存在',
        }.get(e.status, default_msg)
        formatted = {
            'code': e.status,
            'msg': msg,
            'detail': str(e),
        }
    elif isinstance(e, SitebotException):
        formatted = {
            'code': e.code,
            'msg': e.message,
            'detail': str(e),
        }
    else:
        formatted = {
            'code': None,
            'msg': default_msg,
            'detail': str(e),
        }

    return formatted


def is_network_error(e):
    '''
    Check if given exception is caused by network issue.
    '''
    return classify_exception(e)['msg'] == '网络错误'




def to_bool(value):
    """
    将 JavaScript 传入的 'true' 和 'false' 字符串转为 Python 中的 True 和 False
    由于 json.loads 的参数不能为 None 或 ''，所以这里加了判断
    """
    if value in (None, ''):
        return False
    elif isinstance(value, (basestring, )):
        value = value.lower()
        if value in ('true', 'false'):
            return json.loads(value)
    return value


def enable_debug_output(logger):
    """开关某个函数的日志输出"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger.setLevel(logging.DEBUG)
            for handler in logger.handlers:
                handler.setLevel(logging.DEBUG)
            result = func(*args, **kwargs)
            logger.setLevel(logging.INFO)
            for handler in logger.handlers:
                handler.setLevel(logging.DEBUG)
            return result
        return wrapper
    return decorator


def get_file_hash(fpath, algorithm='md5'):
    '''Get hash from given file path
    暂时支持标准库 hashlib 中支持的 hash 算法
    '''
    if algorithm not in dir(hashlib):
        raise TypeError('stdlib `hashlib` does not support `{}` algorithm'.format(algorithm))  # noqa E501

    hash_obj = getattr(hashlib, algorithm)()
    with open(fpath, 'rb') as f:
        while 1:
            block = f.read(10240)
            if not block:
                break
            hash_obj.update(block)
    return hash_obj.hexdigest()


def call_local_script(name, args, kwargs):
    """
    调用 scipts 目录下的指定脚本
    Args:
        name 脚本的名字
        args 脚本需要的参数
        kwargs 脚本需要的关键字参数
    Return:
        脚本的运行结果
    """
    filename = "{}.py".format(name)
    directory = os.path.join(RUNTIME_DIR, 'scripts')
    script = os.path.join(directory, filename)
    with open(script, 'r') as f:
        source = f.read()

    args = args or []
    kwargs = kwargs or {}
    source = make_function(name, source, args)
    code = compile(source, '<script>', 'exec')
    script_env = {}
    exec(code) in script_env
    return script_env[name](*args, **kwargs)


def make_function(func_name, script, variables=None):
    '''
    Form a function definition source code
    '''
    if variables:
        args = u'{}, **kw'.format(variables)
    else:
        args = u'**kw'
    lines = []
    in_multi_line_str = False
    for line in script.splitlines():
        if in_multi_line_str:
            lines.append(line)
        else:
            lines.append(u'    ' + line)
        if len(line.split("'''")) == 2 or len(line.split('"""')) == 2:
            in_multi_line_str = not in_multi_line_str
    return u'def {}({}):\n{}'.format(func_name, args, u'\n'.join(lines))


def render_template(template_content, **context):
    context.update({'_': translate})
    return jinja2.Template(template_content).render(context)


def render_template_file(template_path, **context):
    context.update({'_': translate})
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(CURRENT_DIR, 'templates'))
    ).get_template(template_path).render(context)


def reverse_lookup_path_in_webfolder(path):
    if not re.search(r"edo_temp[\\/]\.mounted", path):
        return path
    import worker
    # 遍历所有任务
    for wid in worker.list_worker_ids():
        work = worker.get_worker_db(wid)
        if not work or work.get("name", "") != "new_webfolder":
            # 任务不存在或不是映射盘任务
            continue
        if not work.get('executed', False):
            # 如果是没有成功运行过的映射盘任务
            continue
        sync_path = work.get('sync_path', None)
        mountpoint = work.get('mountpoint', None)
        if not (sync_path and mountpoint):
            # 如果没有记录同步路径和挂载点
            continue
        if not sync_path.endswith(os.path.sep):
            sync_path = sync_path + os.path.sep
        if not path.startswith(sync_path):
            # 如果并不是在映射盘的同步路径下
            continue
        if not mountpoint.endswith(os.path.sep):
            mountpoint = mountpoint + os.path.sep
        return path.replace(sync_path, mountpoint)
    else:
        return path


def process_exists(name):
    """
    检查是否存在以当前登录用户身份运行的某个进程
    Args:
        name <str> 进程名
    Return:
        result <bool> 进程是否存在
    """
    username = getpass.getuser().decode(sys.getfilesystemencoding())
    for process in psutil.process_iter():
        try:
            info = process.as_dict(attrs=["pid", "name", "username"])
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):  # noqa E501
            continue
        else:
            # On macOS with old version of psutil (namely < 5.6.2), some zombie processes do not reliably raise `ZombieProcess`,
            # We're not going to upgrade psutil to 5.6.2 since it's pretty new. Hence these hacks.
            if all([
                (info.get("pid") or 0) != os.getpid(),  # 过滤掉当前进程
                # PyInstaller 在 macOS 会有两个进程(Bootstraper + 业务代码)，父进程不应当被计算在内
                # 注意：os.getppid 只在 Unix 上可用
                (info.get('pid') or 0) != os.getppid() if getattr(os, 'getppid', None) else True,
                (info.get("name") or "").lower() == name.lower(),
                (info.get("username") or "").lower() == username.lower(),  # noqa E501
            ]):
                log.debug(u'Found running process matching "%s": %s', name, info)
                return True
    else:
        return False


def trace(logger_name='default', enable=True):
    if isinstance(logger_name, (str, unicode)):
        logger = get_logger(
            'trace.' + logger_name,
            init_level='INFO',
            fmt='%(asctime)s %(threadName)s(%(thread)s) %(levelname).1s %(name)s: %(message)s')
    else:
        logger = get_logger('trace.default', init_level='DEBUG')

    def wrapper(func):
        def format_ret(arg):
            if isinstance(arg, (unicode, str)):
                return '{}({})'.format(type(arg).__name__, len(arg))
            else:
                return repr(arg)

        def format_args(*args):
            return ', '.join(map(repr, args))

        def format_kwargs(**kwargs):
            if kwargs:
                return ', ' + ', '.join(
                    ['{}={}'.format(k, v) for k, v in kwargs.items()])
            return ''

        @wraps(func)
        def log(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
                if enable:
                    logger.debug(
                        u'call %s.%s(%s%s) -> %s',
                        repr(args[0]),
                        func.__name__,
                        format_args(*args[1:]),
                        format_kwargs(**kwargs),
                        format_ret(result),
                    )
            except BaseException:
                if enable:
                    logger.exception('caught exception')
                raise

            return result

        return log

    if callable(logger_name):
        return wrapper(logger_name)
    else:
        return wrapper
