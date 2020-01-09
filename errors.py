# coding: utf-8
'''
Contains all custom exception definitions
'''


class AssistantException(Exception):
    '''
    桌面助手内部错误
    无法处理的严重错误（可能导致任务 / 程序 异常退出的错误）才使用这个类，
    如果可以处理或忽略，请使用 logger.error 或 logger.critical 记录详细日志。
    '''

    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __repr__(self):
        return u'<AssistantException {}: {}>'.format(self.code, self.message)

    __str__ = __repr__


class LockExceptionBase(Exception):
    '''
    锁服务相关异常的基类
    '''

    def __init__(self, worker_id, name, timeout=None):
        self.worker_id = worker_id
        self.lock_name = name
        self.timeout_value = timeout


class LockAcquireTimeout(LockExceptionBase):
    '''
    加锁超时
    '''

    def __repr__(self):
        return u'<LockAcquireTimeout {} for worker #{}, timeout={}>'.format(
            self.lock_name, self.worker_id, self.timeout_value
        )

    __str__ = __repr__


class LockAcquireFailure(LockExceptionBase):
    '''
    加锁失败（指定的超时时间已过等）
    '''

    def __repr__(self):
        return u'<LockAcquireFailure {} for worker #{}, timeout={}>'.format(
            self.lock_name, self.worker_id, self.timeout_value
        )

    __str__ = __repr__


class LockReleaseFailure(LockExceptionBase):
    '''
    解锁失败
    '''

    def __repr__(self):
        return u'<LockReleaseFailure {} for worker #{}>'.format(
            self.lock_name, self.worker_id, self.timeout_value
        )


class ScriptDownloadError(Exception):
    '''
    无法从系统下载脚本
    '''

    def __init__(self, script_name, error):
        self.script_name = script_name
        self.error = error

    def __repr__(self):
        return (
            u'<ScriptDownloadError: '
            u'script "{}" failed to download, see .error attribute>'
        ).format(self.script_name)

    __str__ = __repr__


class ScriptSecurityError(Exception):
    '''脚本未能通过安全检查'''
    def __init__(self, script_name):
        self.script_name = script_name

    def __repr__(self):
        return (
            u'<ScriptSecurityError: '
            u'"{}" has been blocked from running because it\'s not signed.>'
        ).format(self.script_name)

    __str__ = __repr__


class Retry(Exception):
    '''
    任务重试
    注意:
    - 任务出现可预期的错误（网络错误等）时可以抛出这个异常；
    - 任务管理会捕获这个异常并根据指定的参数来重试；
    '''

    def __init__(self, delay=2, count=3, raw_error=None):
        '''
        请求重试最多 `count` 次，每次重试前等待 `delay` 秒。
        count=-1 则会无限重试直到不再出错为止。
        '''
        self.delay = int(delay)
        self.count = int(count)
        self.raw_error = raw_error

    def __repr__(self):
        return u'<Retry: {} times max, with a delay of {} seconds>\nRaw traceback: {}'.format(
            self.count, self.delay, self.raw_error
        )

    __str__ = __repr__


class LogicError(Exception):
    """
    有些错误是服务端出错，使得桌面助手无法正常完成任务，如果这种时候抛出错误，会触发桌面助手的
    错误报告，但是用户也无法做任何处理。因此，在服务端出错的时候，比如外部编辑上传新版本时站点
    文件被删除了，就可以抛出 LogicError，让任务状态标记为出错，同时不弹出错误窗口。
    """
    def __init__(self, raw_exception=None):
        self.raw_exception = raw_exception

    def __repr__(self):
        """
        str(type(self.raw_exception)) 的结果类似于 "<class 'edo_client.error.ApiError'>"  # noqa E501
        只截取 edo_client.error.ApiError 的部分。因此 LogicError 的字符串表示为
        "<LogicError(raw: edo_client.error.ApiError)"
        """
        return u'<LogicError(raw: {})>'.format(
            str(type(self.raw_exception)).replace("<class '", "").replace("'>", "")  # noqa E501
        )

    __str__ = __repr__
