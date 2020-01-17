# -*- coding: utf-8 -*-
'''桌面助手入口文件'''
import base64
import urllib
import urlparse

import config

from libs import monkey
monkey.patch_all(not config.IGNORE_SSL, load_certs=True)

from contextlib import closing
import getpass
import os
import sys
import json
import shutil
import socket
import sqlite3
import threading
import time
from copy import deepcopy
from datetime import datetime
from multiprocessing import freeze_support

from werkzeug.urls import url_decode

import edoparser
from utils import (
    translate as _, get_logger, load_logging_config,
    get_certificate_expire_date_by_file, update_certificate, process_exists
)
from config import (
    FILE_STORE_DIR, SCHEME, DATA_VERSION, DATA_VERSION_FILE, APP_DATA,
    INTERNAL_URL,
)
import ui_client

logger = get_logger('webserver', filename='webserver.log')


def check_certificate():
    """
    检查证书文件是否需要更新
    """
    certifi_folder = os.path.join(APP_DATA, "certifi")
    expire_date = get_certificate_expire_date_by_file(
        os.path.join(certifi_folder, "assistant.crt")
    )
    if (expire_date - datetime.now()).days <= config.NEAR_EXPIRE_DATE:
        logger.debug(u"证书临近过期，将自动更新")
        try:
            update_certificate(config.DEFAULT_CERTIFI_URL, certifi_folder)
        except Exception:
            logger.exception(u"证书更新失败")
        else:
            logger.debug(u"证书更新完成")
    else:
        logger.debug(u"无需更新证书")


def database_upgrade():
    '''
    Database schema upgrade
    '''
    new_schema = {
        'site_files': (
            ('uid', 'text'),
            ('revision', 'integer'),
            ('local_path', 'text'),
            ('server_path', 'text'),
            ('modified', 'text'),
            ('md5', 'text'),
            ('root_uid', 'text'),
            ('conflict', 'integer'),
            ('last_pull', 'text'),
            ('last_push', 'text'),
            ('usage', 'text'),
        ),
        'sync_folders': (
            ("uid", "text"),
            ("local_path", "text"),
            ("server_path", "text"),
            ("modified", "text"),
            ("root_uid", "text"),
            ("last_pull", "text"),
            ("last_push", "text"),
            ("conflict", "integer"),
        )
    }
    primary_key = {
        'site_files': ('id integer primary key,'),
        'sync_folders': ('id integer primary key,')
    }
    new_index = {
        'site_files': ('uid', ),
        'sync_folders': ('uid', )
    }

    def record_remap(old, new, record):
        '''
        将 old 结构的表记录 record 转换为 new 结构的表记录
        '''
        if len(old) != len(record):
            raise TypeError(u'数据记录字段不符')

        result = []
        for n in new:
            if n[1] == 'integer':
                result.append('0')
            elif n[1] == 'int':
                result.append('0')
            else:
                result.append('')
        for i in xrange(len(record)):
            # Each column in `old` has the following structure:
            # (column_id, name, type_name, not_null, default_value, primary, )
            name = old[i][1]
            for j in xrange(len(new)):
                if name == new[j][0]:
                    if record[i] is not None:
                        if new[j][1] == 'integer':
                            result[j] = u'{}'.format(record[i])
                        else:
                            if len(record[i]) >= 2 and record[i][0] in ("'", '"')\
                                    and record[i][0] == record[i][-1]:
                                result[j] = u'{}'.format(record[i][1:-2])
                            else:
                                result[j] = u'{}'.format(record[i])
        return result

    if not os.path.isfile(DATA_VERSION_FILE):
        now_versions = {}
    else:
        with open(DATA_VERSION_FILE, 'r') as rvf:
            now_versions = json.load(rvf)
    old_versions = deepcopy(now_versions)

    for _file in os.listdir(FILE_STORE_DIR):
        # 只升级旧版本数据
        if _file.endswith('.db') and now_versions.get(_file, 1) < DATA_VERSION:
            _db_file = os.path.join(FILE_STORE_DIR, _file)
            logger.debug(u'检查数据库 %s', _db_file)
            try:
                connection = sqlite3.connect(_db_file)
                cursor = connection.cursor()
                for table in ('site_files', 'sync_folders', ):
                    # 查询数据表结构
                    cursor.execute('pragma table_info({})'.format(table))
                    logger.debug(u'查询表 %s 当前的结构', table)
                    old_schema = cursor.fetchall()
                    need_change = False
                    if len(old_schema) != (len(new_schema[table]) + len(primary_key[table])):
                        need_change = True
                    else:
                        for _column in old_schema:
                            if (_column[1], _column[2]) not in new_schema[table]:
                                need_change = True
                                break

                    # 如果结构与预期不同，则新建新结构的表
                    if need_change:

                        # 创建数据库备份文件
                        shutil.copyfile(_db_file, '{}.upbak'.format(_db_file))
                        logger.debug(
                            u'已创建备份文件 %s',
                            u'{}.upbak'.format(_db_file)
                        )

                        fields = [' '.join(f) for f in new_schema[table]]
                        cursor.execute(
                            'create table {}_temp({}{})'.format(
                                table,
                                primary_key[table],
                                ', '.join(fields)
                            )
                        )
                        logger.debug(u'临时数据表构建完成')

                        # 将旧数据迁移到新表
                        cursor.execute('select * from {}'.format(table))
                        logger.debug(u'读取表 %s 的旧数据', table)

                        # get all the columns
                        columns = [column[0] for column in new_schema[table]]
                        values_wildcard = '?'
                        for count in range(len(columns) - 1):
                            values_wildcard += ',?'
                        # format the sql
                        sql = 'insert into {}_temp({}) values({});'.format(
                                table,
                                ', '.join(columns),
                                values_wildcard)
                        logger.debug(columns)
                        for record in cursor.fetchall():
                            data = tuple(record_remap(
                                        old_schema,
                                        new_schema[table],
                                        record))
                            logger.debug(data)
                            cursor.execute(sql, data)

                        logger.debug(u'数据表 %s 数据迁移完成', table)

                        # 删除旧表
                        cursor.execute('drop table {}'.format(table))
                        logger.debug(u'旧的数据表 %s 已经删除', table)

                        # 重命名新表
                        cursor.execute(
                            'alter table {0}_temp rename to `{0}`'.format(
                                table
                            )
                        )
                        logger.debug(u'临时表重命名完成')

                        # 建立指定的索引
                        for idx in new_index[table]:
                            cursor.execute(
                                'create index if not exists {0}_idx on {1}({0})'.format(
                                    idx, table
                                )
                            )
                            logger.debug(u'%s 索引构建完成', idx)
                        connection.commit()
                        logger.debug(u'表 %s 的修改已经提交', table)

                        # 删除备份文件
                        os.remove('{}.upbak'.format(_db_file))
                    else:
                        logger.debug(u'表 %s 结构与预期一致，无需修改', table)
            except Exception:
                logger.error(
                    u'数据库升级时遇到错误，您可以手动恢复数据库备份文件',
                    exc_info=True
                )
            else:
                now_versions.update({_file: DATA_VERSION})

    for _dbfile in now_versions.keys():
        if _dbfile not in os.listdir(FILE_STORE_DIR):
            now_versions.pop(_dbfile, None)
    if now_versions != old_versions:
        with open(DATA_VERSION_FILE, 'w') as wvf:
            json.dump(now_versions, wvf)
    logger.debug(u'数据库升级结束')


def get_random_port():
    """
    获取一个可用的随机端口号
    Return:
        port <int> 端口号
    """
    with closing(socket.socket(socket.AF_INET)) as new_sock:
        new_sock.bind((config.BIND_ADDRESS, 0))
        return new_sock.getsockname()[-1]


def port_occupied(port):
    """
    检查指定端口是否被占用
    Args:
        port <int> 端口号
    Return:
        result <bool> 端口是否被占用
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        return 0 == sock.connect_ex(("127.0.0.1", port))


def get_available_port(expected_port, blacklist=[]):
    '''
    检查给定端口是否可用，如果不可用则返回一个可用端口。支持使用黑名单限制可用端口
    Args:
        expected_port <int> 要检查的端口号
        blacklist <list> 不允许使用的端口号
    Return:
        port_number <int> 可用的端口号
    '''
    port = expected_port
    # 检查端口是否是否被禁用或被占用
    while port in blacklist or port_occupied(port):
        old_port, port = port, get_random_port()
        logger.debug(
            u"端口 %s 被%s，获取新的端口 %s",
            old_port,
            u"禁用" if old_port in blacklist else u"占用",
            port
        )
    return port


def get_ports_blacklist():
    # 从环境变量中获取端口黑名单
    ports_blacklist = []
    disallowed_ports = os.environ.get("EDO_AST_DISALLOWED_PORTS")
    if disallowed_ports:
        for port in disallowed_ports.split(","):
            try:
                ports_blacklist.append(int(port.strip()))
            except Exception:
                logger.exception(u"端口号解析错误：%s", port)
                continue
    return ports_blacklist


def post_port_change(http_port, https_port):
    '''自动修改端口的后续处理
    - 延迟一段时间后打开一个webview窗口，提示用户去站点上设置桌面助手的访问端口。
    - 重新加载一些模块，确保端口生效。
    '''
    def show_config_window():
        time.sleep(6)
        ui_client.show_webview(
            url='{}/admin/config?auto_changed'.format(config.INTERNAL_URL),
            title=_('Assistant port conflict'),
            size=(370, 270), resizable=False,
        )

    threading.Thread(target=show_config_window).start()
    # 存储端口信息到配置文件
    with open(config.CONFIG_FILE, 'w') as wf:
        json.dump({'http_port': http_port, 'https_port': https_port}, wf)
    # 重新设置config模块的端口值，确保端口设置生效
    # FIXME This is ugly as hell, MUST figure a way to do it correctly
    config.INTERNAL_PORT = config.HTTP_PORT = http_port
    config.HTTPS_PORT = https_port
    config.INTERNAL_URL = 'http://127.0.0.1:{}'.format(http_port)
    config.CONFIG.update({'http_port': http_port, 'https_port': https_port})
    config.P2P_ENABLED = False


def prepare_ports():
    """
    检测能否以当前配置文件中记录的端口启动桌面助手
    1 已有桌面助手进程，则提示无需重复启动并退出
    2 没有桌面助手进程，获取可用端口并启动桌面助手
    """
    ports_blacklist = get_ports_blacklist()
    logger.debug(u"端口黑名单：%r", ports_blacklist)
    logger.debug(
        u"以用户 %s 启动%s桌面助手",
        getpass.getuser().decode(sys.getfilesystemencoding()),
        u"全局安装的" if config.GLOBAL_INSTALL else u""
    )

    # TODO: Linux 版本的桌面助手是直接通过命令启动的，需要支持 Linux 版的检测
    process_name = {
        "win32": "edo_assistent.exe",
        "darwin": "edo_assistent",
    }.get(sys.platform, "")
    if process_exists(process_name):
        logger.debug(u"桌面助手已启动，无需重复启动")
        ui_client.message(
            _('Assistant'), _('Assistant launched'), type='info'
        )
        sys.exit()

    # 检测当前配置文件中记录的端口是否可用，如不可用，则获取新的可用端口
    http_port = get_available_port(config.HTTP_PORT, ports_blacklist)
    https_port = get_available_port(config.HTTPS_PORT, ports_blacklist)
    logger.info(u'桌面助手访问端口：http %s, https %s', http_port, https_port)

    if http_port != config.HTTP_PORT or https_port != config.HTTPS_PORT:
        # 让各模块使用新的端口，并且弹出提示窗口，告知用户端口已经更改
        post_port_change(http_port, https_port)


def start_gui():
    reload(sys)
    sys.setdefaultencoding('utf-8')

    prepare_ports()
    database_upgrade()

    from libs.managers import get_site_manager
    # GUI 模式启动桌面助手时，只启动允许通知的连接的消息线程
    for site in get_site_manager().list_sites():
        if site.get_config("notification"):
            site.get_message_thread().connect()

    from webserver import start_server
    start_server()


def flash_window():
    import qtui  # API'QString'只能设置一次，引入桌面助手的设置，防止冲突
    from PyQt4 import QtGui
    from PyQt4 import QtCore
    app = QtGui.QApplication(sys.argv)
    splash = QtGui.QSplashScreen()  # 欢迎窗口
    splash.resize(1, 1)  # 大小为0不会失焦，需要1像素
    loop = QtCore.QEventLoop()  # 设定一个事件循环
    timer = QtCore.QTimer()  # 设定一个定时器
    timer.timeout.connect(loop.quit)  # 连接定时器，到时后执行loop.quit
    splash.show()  # 显示一个窗口
    timer.start(500)  # 0.5秒定时器
    loop.exec_()  # 事件循环


def invoke_scheme(url):
    '''
        基于输入的edo-ast://xxx协议字符串，调用匹配的API
    '''
    result = urlparse.urlparse(url)
    api_path = result.path
    api_data = None
    # 1. edo-ast://start只以主进程方式启动（有界面）
    if result.netloc == 'start':
        logger.debug(u'协议：启动桌面助手')
        return start_gui()
    # 2. 协议字符串样例：edo-ast://assistant/api_path?param=JTdCJTIydG9rZW4lMjIlM
    else:
        api_data = urlparse.parse_qs(result.query)['params'][0]
        # 前端传递参数格式：btoa(encodeURIComponent(JSON.stringify(data)))
        api_data = json.loads(urllib.unquote(base64.b64decode(api_data)))

    # 检查是否有桌面助手在运行
    expected_process = {
        "win32": "edo_assistent.exe",
        "darwin": "edo_assistent",
    }.get(sys.platform, "")
    if not process_exists(expected_process):
        # 2.a 如果没有，应该以主进程模式运行，启动新线程去POST API

        def delayed_api_call():
            logger.debug(u'在后台线程中等待调用主进程API...')
            while 1:
                try:
                    ui_client._request_api('about', internal=True)
                except Exception:
                    logger.debug(u'主进程尚未就绪')
                    time.sleep(1)
                else:
                    break

            logger.debug(u'主进程已经就绪，api=%s, data=%s', api_path, api_data)
            ui_client._request_api(api_path, kw=api_data, internal=True)

        caller = threading.Thread(target=delayed_api_call, name='delayed_api_call')
        caller.daemon = True
        caller.start()

        start_gui()
    else:
        # 2.b 如果有，直接POST API然后退出
        logger.debug(u'桌面助手已经在运行，直接调用 api=%s, data=%s', api_path, api_data)
        try:
            result = ui_client._request_api(api_path, kw=api_data, internal=True)
            logger.debug(u'调用结果: %s', result.content)
            sys.exit()
        except Exception:
            logger.error(u'调用出错')
            sys.exit(2)


def main():
    # PyInstaller 多进程支持
    freeze_support()

    load_logging_config()
    check_certificate()  # TODO 这个好像不应该在这里？

    # Ignore start command
    if len(sys.argv) > 1:
        config.DEBUG = True
        if sys.argv[-1].startswith(SCHEME):
            scheme_data = sys.argv.pop(-1)
            flash_window()
            logger.debug(u'桌面助手被自定义协议启动，数据: %s', scheme_data)
            return invoke_scheme(scheme_data)
        else:
            try:
                start_server, headless = edoparser.parse_args(sys.argv[1:])
                headless = True
                # 通过命令行一次性运行任务，已经在parse_args中就运行了，这里直接退出
                if not start_server:
                    return

                logger.debug(
                    u'桌面助手以 %s 模式启动',
                    u'静默' if headless else u'cmdline',
                )
                if headless:
                    from libs.managers import get_site_manager
                    # 无界面模式启动桌面助手，所有连接的消息线程都要启动
                    for site in get_site_manager().list_sites():
                        site.get_message_thread().connect()
                    from headless_server import start_server
                    start_server()
                else:
                    start_gui()
            except SystemExit:
                raise
            except KeyboardInterrupt:
                raise SystemExit('Exit by key interrupt')
            except Exception as e:
                logger.debug(u'桌面助手无法解析命令 %r，将会启动图形界面', sys.argv, exc_info=True)
                start_gui()
    else:
        start_gui()


if __name__ == "__main__":
    main()
