# coding: utf-8
'''
所有的装饰器放在这个模块中
注意：
- 之前实现的装饰器仍然在 utils 中，之后应当逐步迁移到这里 (2017.03.27)
'''
import json

from config import HEADLESS
from libs.funcutils import wraps
from _utils import is_internal_call


def ui_api(func):
    '''
    设置某个 API 函数为 UI 操作相关的，在静默模式下不进行调用
    '''
    @wraps(func)
    def wrapped_ui_api_func(*args, **kwargs):
        if HEADLESS:
            return json.dumps({'success': False, 'msg': 'UI not available'})
        else:
            return func(*args, **kwargs)

    return wrapped_ui_api_func


def internal_api(func):
    '''
    设置某个 API 函数为内部接口
    内部接口只能通过 ui_client 中相应工具函数进行调用
    '''
    @wraps(func)
    def wrapped_internal_api_func(*args, **kwargs):
        try:
            if is_internal_call():
                return func(*args, **kwargs)
            else:
                return json.dumps({'success': False, 'msg': 'Restricted API'})
        except Exception:
            return json.dumps({'success': False, 'msg': 'Failed'})

    return wrapped_internal_api_func


def mark_connection_expired_when_401(func):
    '''
    当需要连接的任务出现 token 过期，即 ApiError 401 的时候，从 workerdb 中获取必要参数，
    查询到相应的连接并标记其为过期状态
    '''
    from libs.managers import get_site_manager
    from edo_client.error import ApiError
    from worker import get_worker_db

    site_manager = get_site_manager()

    @wraps(func)
    def decorator(worker_id, *args, **kwargs):
        wdb = get_worker_db(worker_id)
        oc_server = wdb.get('oc_server', None)
        account = wdb.get('account', None)
        instance = wdb.get('instance', None)
        pid = wdb.get('pid', None)
        token = wdb.get('token', None)
        instance_name = wdb.get('instance_name', None)
        instance_url = wdb.get('instance_url', None)
        username = wdb.get('username', None)
        site = site_manager.add_site(
            oc_url=oc_server,
            account=account,
            instance=instance,
            pid=pid,
            token=token,
            instance_name=instance_name,
            instance_url=instance_url,
            username=username,
        )
        wdb['token'] = site.token
        wdb.sync()
        try:
            result = func(worker_id, *args, **kwargs)
        except ApiError as e:
            # 出现 401 错误时，标记连接过期
            if e.code == 401:
                site.set_token_invalid()
                site_manager.save()  # 调用 save 方法会自动刷新连接界面
            raise
        else:
            return result

    return decorator
