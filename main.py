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

from utils import (
    translate as _, get_logger, load_logging_config,
    get_certificate_expire_date_by_file, update_certificate, process_exists
)
from config import (
    FILE_STORE_DIR, DATA_VERSION, DATA_VERSION_FILE, APP_DATA,
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



def main():
    # PyInstaller 多进程支持
    freeze_support()
    if len(sys.argv) == 1:
        print('请通过参数指定机器人的访问 token')
        sys.exit(1)
    else:
        os.environ['APP_TOKEN'] = sys.argv[1]

    load_logging_config()
    check_certificate()  # TODO 这个好像不应该在这里？

    config.DEBUG = True

    try:
        logger.debug(
            u'桌面助手以 %s 模式启动',
            u'静默' if headless else u'cmdline',
        )
        from libs.managers import get_site_manager
        # 无界面模式启动桌面助手，所有连接的消息线程都要启动
        for site in get_site_manager().list_sites():
            site.get_message_thread().connect()
        from headless_server import start_server
        start_server()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit('Exit by key interrupt')


if __name__ == "__main__":
    main()
