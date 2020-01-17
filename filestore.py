# encoding: utf8

'''
FileStore
主管所有文件库相关操作
'''
import copy
import os
import sys
import shutil
import time
import logging
import sqlite3
import urlparse
import platform
import traceback
import tempfile
from datetime import datetime

from edo_client import get_client
from edo_client.error import ApiError, AbortUpload
if platform.system() == 'Windows':
    from win32com.shell import shell, shellcon

import config
from config import FILE_STORE_DIR, EDO_TEMP, APP_KEY, APP_SECRET
import ui_client
from utils import (
    get_file_md5, is_valid_dir,
    get_filesize, is_valid_file, get_numbered_path,
    kwargs_check, search_dict_list,
    get_logger, get_iso_mtime, translate as _,
    get_oc_client, get_upload_client,
    metadata_by_shortcut, classify_exception,
    is_network_error, should_push,
    get_file_hash,
)
from worker import get_worker_db


import requests
import hashlib

# TODO 使用 enum
# 有语义的冲突标记
# 0 无冲突，TODO: 同时会试图删除冲突备份文件
# 1 修改冲突
# 2 线上删除冲突
# 3 线下删除冲突
# 4 创建冲突，应该将本地项目版本标为 0 之后 mark 为 1: 修改冲突

CONFLICT_NO_CONFLICT = 0
CONFLICT_BOTH_MODIFIED = 1
CONFLICT_SERVER_DELETED = 2
CONFLICT_CLIENT_DELETED = 3
CONFLICT_BOTH_CREATED = 4


def exceptionhook(etype, value, tb):
    logging.exception(
        '%s%s',
        datetime.strftime(datetime.now(), '%H:%M:%S - Exception happend: '),
        ''.join(traceback.format_tb(tb))
    )

reload(sys)
sys.excepthook = exceptionhook


def is_file(obj):
    return {'File', 'FileShortCut'} & set(obj.get('object_types', []))


def is_folder(obj):
    return {'Folder', 'FolderShortCut'} & set(obj.get('object_types', []))


class FileStore:
    ''' 一个站点的文件存储，包括服务器和本地存储

    记录所有文件元数据。并能够保持冲突文件以及元数据。

    http.192.168.1.115.8888.zopen.default.db

    - uid: 唯一id
    - revision: 版本
    - server_path: 服务器存放位置
    - local_path: 本地存放位置
    - modified: 下载时的更改时间
    - md5: 修改信息
    - root_uid: 所属的根同步点uid
    '''

    def __init__(
        self,
        server, account, instance,
        wo_server=None,
        upload_server=None,
        logger=None,
        token=None,
        worker_id=None,
        pid=None
    ):
        self.server = server
        self.account = account
        self.instance = instance
        server_info = urlparse.urlparse(self.server)
        protocol = getattr(server_info, 'scheme', 'http')
        self.hostname = getattr(server_info, 'hostname')
        port = getattr(server_info, 'port', None)
        if port is None:
            if protocol == 'https':
                port = '443'
            elif protocol == 'http':
                port = '80'
        self.db_prefix = '{}.{}.{}.{}.{}'.format(
            protocol, self.hostname, port, self.account, self.instance
        )

        self.__wo_server = wo_server
        self.upload_server = upload_server
        self.oc_client = get_oc_client(
            oc_server=self.server,
            account=self.account,
            instance=self.instance
        )
        self.token = token
        if self.token is not None:
            self.__auth_oc()
        self.__wo_client = None

        self.logger = logger or get_logger(
            'filestore-{}'.format(self.db_prefix)
        )
        self.worker_id = worker_id
        self.pid = pid

    def __auth_oc(self, token=None):
        if self.oc_client.token_code is None:
            if token is None:
                if self.token is None:
                    return
                else:
                    token = self.token
            if self.token is None:
                self.token = token
            self.oc_client.auth_with_token(token)

    @property
    def wo_client(self):
        if not self.__wo_client:
            self.__wo_client = get_client(
                'workonline', self.server, self.account, self.instance,
                token=self.token, client_id=APP_KEY, client_secret=APP_SECRET
            )
        return self.__wo_client

    @wo_client.setter
    def wo_client(self, client):
        self.__wo_client = client

    @property
    def wo_server(self):
        self.__auth_oc(token=None)
        if not self.__wo_server:
            try:
                self.__wo_server = self.oc_client.account.get_instance(
                    'workonline',
                    self.instance,
                    self.account
                )['api_url']
            except TypeError:
                pass
            except:
                self.logger.warn(u'获取 wo_server 出错', exc_info=True)
                raise
        return self.__wo_server

    def _get_site_db(self):
        '''
        连接到存储站点文件/文件夹数据的数据库
        '''
        db_path = os.path.join(FILE_STORE_DIR, '{}.db'.format(self.db_prefix))
        if not os.path.exists(db_path):
            connection = sqlite3.connect(db_path)
            cursor = connection.cursor()
            # Create table
            cursor.executescript(
                '''BEGIN;
                CREATE TABLE IF NOT EXISTS site_files
                (id integer primary key, uid text, revision int, local_path text,
                 server_path text, modified text, md5 text,
                 root_uid text, conflict integer, last_pull text,
                 last_push text, usage text);
                CREATE INDEX IF NOT EXISTS uid_idx ON site_files (uid);
                COMMIT;'''
            )
            cursor.executescript(
                '''BEGIN;
                CREATE TABLE IF NOT EXISTS sync_folders
                (id integer primary key, uid text, local_path text, server_path text,
                 modified text, root_uid text, last_pull text,
                 last_push text, conflict integer);
                CREATE INDEX IF NOT EXISTS uid_idx ON sync_folders (uid);
                COMMIT;'''
            )
            # Commit operation already done in SQL
            # connection.commit()
        return sqlite3.connect(db_path)

    def format_data_item(self, data, object_type='file'):
        '''Format a dict so it can be saved into database'''
        if not isinstance(data, (dict, )):
            raise TypeError(u'format_data_item takes only one dict')
        if 'object_type' in data:
            object_type = data.pop('object_type', 'file')
        if object_type == 'file':
            required_keys = ('uid', 'local_path', 'server_path', 'revision', )
            optional_keys = ('root_uid', 'modified', 'usage',
                             'last_pull', 'last_push', 'conflict', 'md5', )
        elif object_type == 'folder':
            required_keys = ('uid', 'local_path', 'server_path', )
            optional_keys = ('modified', 'root_uid', 'last_pull',
                             'last_push', 'conflict')
        if not all(k in data for k in required_keys):
            raise ValueError(u'missing key for object_type "{}"'.format(
                object_type
            ))
        for k in data.keys():
            if k not in required_keys + optional_keys:
                data.pop(k, None)
        for k in optional_keys:
            if k not in data:
                data[k] = ''
        return data

    def save_item(self, data, query=None, object_type='file'):
        '''
        将文件信息存入数据库
        Args:
            data <Dict> 要存入的数据，必须包含 uid 和 local_path 键
        Returns:
            None
        '''
        try:
            if data.get('object_type', None):
                object_type = data.pop('object_type')

            if object_type == 'file':
                table = 'site_files'
            elif object_type == 'folder':
                table = 'sync_folders'

            if not query:
                if not data.get('uid', None):
                    return
                else:
                    query = {
                        'uid': data['uid'],
                        'local_path': data['local_path']
                    }
                data.pop('id', None)

            connection = self._get_site_db()
            cursor = connection.cursor()

            sql_count = (
                'SELECT COUNT(*) FROM `{}` '
                'WHERE `uid`=? AND `local_path`=?'
            ).format(table)
            cursor.execute(
                sql_count,
                (query.get('uid', data['uid']), query.get('local_path', data['local_path']))
            )
            record_count = cursor.fetchone()[0]

            # 统一路径分隔符
            if 'local_path' in data:
                data['local_path'] = unicode(
                    os.path.abspath(data['local_path'])
                )

            if record_count == 0:
                # 不存在记录，新增一条
                sql_insert = 'INSERT INTO `{}` ({}) VALUES ({})'.format(
                    table,
                    ','.join(data.keys()),
                    ','.join('?' * len(data.keys()))
                )
                cursor.execute(sql_insert, data.values())
            else:
                # 修改已有的记录
                uid, local_path = data.pop('uid'), data.get('local_path')
                sql_update = 'UPDATE `{}` SET {} WHERE {}'.format(
                    table,
                    ', '.join([
                        '`{}`=\'{}\''.format(*kv) for kv in data.items()
                    ]),
                    ' AND '.join([
                        '`{}`=\'{}\''.format(*kv) for kv in query.items()
                    ])
                )
                data.update({
                    'uid': uid,
                    'local_path': local_path
                })
                cursor.execute(sql_update)
            # 保存数据库修改
            connection.commit()
            data.update({'object_type': object_type})
            return data
        except Exception:
            self.logger.error(u'save_item 出错', exc_info=True)
            return

    @kwargs_check(keys=('uid', 'local_path', 'server_path', 'root_uid',
                        'md5', 'conflict', 'usage', 'object_type',
                        'revision', 'id'))
    def query_items(self, **kwargs):
        tables, results = ['site_files', 'sync_folders', ], []

        def field_map(record, table):
            if table == 'site_files':
                return {
                    'object_type': 'file',
                    'id': record[0],
                    'uid': record[1],
                    'revision': record[2],
                    'local_path': record[3],
                    'server_path': record[4],
                    'modified': record[5],
                    'md5': record[6],
                    'root_uid': record[7],
                    'conflict': record[8],
                    'last_pull': record[9] or '',
                    'last_push': record[10] or '',
                    'usage': record[11]
                }
            elif table == 'sync_folders':
                return {
                    'object_type': 'folder',
                    'id': record[0],
                    'uid': record[1],
                    'local_path': record[2],
                    'server_path': record[3],
                    'modified': record[4],
                    'root_uid': record[5],
                    'last_pull': record[6] or '',
                    'last_push': record[7] or '',
                    'conflict': record[8]
                }

        object_type = kwargs.pop('object_type', None)
        if object_type == 'file':
            tables = ['site_files']
        elif object_type == 'folder':
            tables = ['sync_folders']

        connection = self._get_site_db()
        cursor = connection.cursor()
        for table in tables:
            # 准备查询条件
            ks = []
            for k in kwargs.keys():
                if isinstance(kwargs[k], unicode):
                    string = str(kwargs[k].encode('utf-8'))
                else:
                    string = str(kwargs[k])
                if '%' in string:
                    ks.append('`{}` LIKE ?'.format(k))
                    continue
                ks.append('`{}`=?'.format(k))

            # 拼装查询语句
            sql = 'SELECT * FROM `{}` WHERE {}'.format(table, ' AND '.join(ks))
            try:
                cursor.execute(sql, kwargs.values())
                for record in cursor.fetchall():
                    results.append(field_map(record, table))
            except Exception:
                self.logger.error(u'执行 SQL 查询时出错', exc_info=True)
                raise

        return results

    def download_file(
        self, token, metadata, local_folder, file_name=None,
        usage='download', root_uid=None, last_pull=None,
        skip_db=False, revision=None, silent=True,
        worker_id=None, on_progress=None
    ):
        '''
        下载某个文件到指定的本地路径
        已实现本地缓存文件查询，如果本地有未修改的版本将直接从本地拷贝。
        Args:
            token <String> token
            metadata <?> WoClient 查询到的元数据对象
            local_folder <String> 指定的本地路径（文件夹）
            root_uid <String|Number> 所属同步区的 uid （下载单个文件不需要）
            last_pull <String> 上次同步时间（下载单个文件不需要）
            on_progress <Callable> 下载回调函数，参数 offset, size
        Returns:
            None
        '''
        self.__auth_oc(token)
        silent = silent or (usage in ('3dpreview',))

        # Notice: 历史版本没有 name，因此需要优先根据 title 确定名字
        if file_name:
            fname = file_name
        else:
            fname = metadata.get('title', metadata.get('name', None))

        if fname is None:
            self.logger.error(u'无法获取文件名, metadata: %s', metadata)
            raise RuntimeError(u'无法获取文件名')
        if 'name' not in metadata:
            if 'local_path' in metadata:
                metadata.update({
                    'name': os.path.basename(metadata['local_path'])
                })
            else:
                metadata.update({
                    'name': fname
                })
        not silent and ui_client.message(
            _('Download started'), fname, type='info'
        )

        if usage == 'conflict_backup':
            # 由于策略上的原因， 不会下载冲突的文件。
            # 如果程序执行到这里，则说明有bug
            raise ValueError("get_conflict_backup_filename method does not exists")
        revision = revision or metadata.get('revision', None)

        if file_name:
            local_path = os.path.join(local_folder, file_name)
        else:
            local_path = get_numbered_path(os.path.join(local_folder, fname))
            fname = os.path.basename(local_path)

        # 先查询文件是否存在
        cached_file = self.get_cached_file(
            metadata['uid'],
            revision,
            local_path=metadata.get('local_path', None)
        )

        if cached_file:
            # 若下载文件和cache_file是同一个文件，则说明不需要下载
            if local_path == cached_file['local_path']:
                self.logger.debug(
                    u'download_file中的 cache_file:%s 和 local_path:%s 是同一份文件，不需要下载',
                    cached_file['local_path'],
                    local_path
                )
                return cached_file

            self.logger.debug(
                u'download_file 找到对应 %s (rv%s) 的缓存文件 %s',
                metadata.get('path') or metadata.get('name'),
                revision, cached_file['local_path']
            )
            # TESTING 外部编辑，如果缓存文件位于 edo_temp 中，且不是要下载到内存盘时，
            # 不要复制文件，直接使用找到的缓存
            if usage == 'edit'\
                    and os.path.dirname(cached_file['local_path']) == EDO_TEMP\
                    and '.memory' not in local_path:
                return cached_file

            self.logger.debug(
                u'复制文件 %s 到 %s', cached_file['local_path'], local_path
            )
            try:
                shutil.copyfile(cached_file['local_path'], local_path)
                ui_client.update_progress(
                    self.worker_id, direction='down',
                    fpath=local_path, filename=fname,
                    size=metadata['bytes'] or get_filesize(local_path),
                    progress=100, status=_('Finished'),
                    uid=metadata['uid']
                )
            except Exception as e:
                if isinstance(e, IOError):
                    self.logger.warn(
                        u'download_file 复制文件到 %s 时发生磁盘读写错误',
                        local_folder,
                        exc_info=True
                    )
                raise

            # 将目标路径加入数据库中
            cached_file.update({
                'local_path': local_path,
                'modified': get_iso_mtime(local_path),  # 此处应该用本地文件创建时间
                'root_uid': root_uid or '',
                'conflict': int(False),
                'last_pull': last_pull or datetime.utcnow().isoformat(),
                'usage': usage
            })
            data = cached_file.copy()
            not skip_db and self.save_item(cached_file, object_type='file')
            # For return

            # 冒泡提示是从本地找到的副本
            not silent and ui_client.message(
                _('Download finished'),
                _('Found a cached local copy of {}').format(fname),
                type='info'
            )
        else:
            self.logger.debug(metadata)

            # 若本地没有这个文件的副本，从服务器下载
            self.logger.debug(
                u'download_file 从服务器下载文件 %s',
                metadata.get('path') or metadata.get('local_path')
            )
            if worker_id is not None:
                self.worker_id = worker_id
            size = metadata['bytes'] or 0

            def progress_callback(offset, total_size, filename=None):
                if callable(on_progress):
                    try:
                        on_progress(offset, total_size, filename)
                    except Exception:
                        self.logger.debug(u'下载进度回调可能有问题', exc_info=True)
                if self.worker_id is not None:
                    ui_client.update_progress(
                        self.worker_id, direction='down',
                        fpath=local_path, filename=fname, size=total_size,
                        progress=(offset * 100.0 / total_size) if total_size else 0,
                        uid=metadata['uid']
                    )

            # 写文件内容，同时计算 MD5 值
            try:
                resumable = metadata.get('bytes', None) != 0
                mime = 'application/3d' if usage == '3dpreview' else None
                md5 = self.wo_client.content.download(
                    dst_path=local_path,
                    calc_hash=True,
                    uid=metadata['uid'],
                    resumable=resumable,
                    revision=revision,
                    mime=mime,
                    on_progress=progress_callback
                )['hash']
            except ApiError as e:
                self.logger.error(u'从服务器下载文件时出错', exc_info=True)
                if e.status == 404:
                    if usage == '3dpreview':
                        self.logger.warn(u'3D 文件预览时发生错误：线上文件不存在')
                        not silent and ui_client.message(
                            _('3D Preview'),
                            _('Error: remote file does not exist'),
                            type='error'
                        )
                    else:
                        self.logger.warn(
                            u'download_file 下载文件时发生 404 错误，文件信息: %s',
                            '\t\n'.join([
                                '{}: {}'.format(*kv) for kv in metadata.items()
                            ])
                        )
                        ui_client.message(
                            _('Download failed'),
                            _('Remote file does not exist'),
                            type='warn'
                        )
                    status = _('File not found')
                elif e.code == 403:
                    self.logger.warn(
                        u'download_file 没有权限下载文件: %s',
                        '\t\n'.join([
                            '{}: {}'.format(*kv) for kv in metadata.items()
                        ])
                    )
                    ui_client.message(
                        _('Download failed'),
                        _('You have no permission to download {}, skipped').format(metadata['path']),
                        type='warn'
                    )
                    status = _('Permission denied')
                else:
                    status = _('Failed')
                if self.worker_id is not None:
                    ui_client.update_progress(
                        self.worker_id, direction='down',
                        fpath=local_path, filename=fname, size=size,
                        progress=0, status=status,
                        uid=metadata['uid'], error=classify_exception(e)
                    )
                return
            except IOError as e:
                self.logger.warn(
                    u'download_file 写入文件到 %s 时发生 IO 错误',
                    local_folder, exc_info=True
                )
                status = _('Network error') if is_network_error(e) else _('Failed to write to disk')
                if len(local_path) > 255:
                    if self.worker_id is not None:
                        ui_client.update_progress(
                            self.worker_id, direction='down',
                            fpath=local_path, filename=fname, size=size,
                            progress=0, status=_('File name is too long'),
                            uid=metadata['uid'],
                            error={
                                'msg': _(
                                    'Windows does not support downloading'
                                    ' files with long file names'
                                ),
                                'code': e.errno,
                                'detail': _(
                                    'The length of local path is {} characters,'
                                    ' which exceeds the path limit of Windows.'
                                ).format(len(local_path))
                            }
                        )
                        return
                if self.worker_id is not None:
                    ui_client.update_progress(
                        self.worker_id, direction='down',
                        fpath=local_path, filename=fname, size=size,
                        progress=0, status=status,
                        uid=metadata['uid'], error=classify_exception(e)
                    )
                raise
            except Exception as e:
                if self.worker_id is not None:
                    ui_client.update_progress(
                        self.worker_id, direction='down',
                        fpath=local_path, filename=fname, size=size,
                        progress=0, status=_('Failed'), uid=metadata['uid'],
                        error=classify_exception(e)
                    )
                raise

            # Fix 0-byte size file progres (like shortcuts, etc.)
            if not size and self.worker_id is not None:
                ui_client.update_progress(
                    self.worker_id, direction='down',
                    fpath=local_path, filename=fname, size=size, progress=100,
                    uid=metadata['uid']
                )

            # 存入数据库
            data = {
                'uid': metadata['uid'],
                'revision': metadata.get('revision', None),
                'local_path': local_path,
                'server_path': metadata['path'] or metadata['server_path'],
                'modified': get_iso_mtime(local_path),
                'md5': md5,
                'root_uid': root_uid or '',  # 零散文件没有根节点
                'conflict': int(False),
                'last_pull': last_pull or datetime.utcnow().isoformat(),
                'usage': usage
            }
            not skip_db and self.save_item(data, object_type='file')

            # 冒泡提示
            not silent and ui_client.message(
                _('Download finished'), fname, type='info'
            )
        return data

    # TODO v6.5.0 简化为 .new_syncfolder(remote, local)，详细参考外网文档
    def new_syncfolder(
        self, local, remote,
        token=None, force=False
        # self, local_path, up=False, token=None,
        # metadata=None, uid=None, create=True, root_uid=None
    ):
        '''
        新建一个同步区
        Args:
            local       <String> 指定的本地路径（文件夹）
            remote      <String|Number> 站点目录的 uid
            token=None  <String> 向上建立同步区时需要的 token
            force=False <Boolean> 是否直接新建同步区而不需要经过查询

        Returns:
            <Dict> 包含同步区信息的字典
        '''
        if token:
            self.__auth_oc(token)

        if not os.path.exists(local):
            os.makedirs(local)
        elif not os.path.isdir(local):
            raise ValueError(u'{} 不是一个有效的本地目录'.format(local))

        metadata = self.wo_client.content.properties(uid=remote)

        root_uid = ''
        matched_roots = self.query_items(uid=remote, root_uid='', object_type='folder')
        # 已有且是根同步点
        if not force and matched_roots:
            return matched_roots[0]
        else:
            matched_folders = self.query_items(uid=remote, object_type='folder')
            if matched_folders:
                root_uid = matched_folders[0]['root_uid']

        data = {
            'uid': remote,
            'local_path': local,
            'server_path': metadata['path'],
            'modified': get_iso_mtime(local),
            'root_uid': root_uid,
            'conflict': CONFLICT_NO_CONFLICT,
        }
        self.save_item(data, object_type='folder')
        return data

    def list_syncfolders(self, uid, parents=[], path=None):
        '''
        找出某个线上文件夹的所有同步区
        Args:
            uid <String|Number> 文件夹的 uid
            parents <Sequence> 文件夹的所有父文件夹的 uid
            path <String> 文件夹的线上路径
        Returns:
            <List> 其中每一项都包含文件夹的本地路径和所属根同步点的 uid
        '''
        folders = []
        # 查询：指定文件夹是根同步点；如果有结果，本地必然存在这个文件夹
        root_syncfolder_matches = self.query_items(
            uid=uid, root_uid='',
            object_type='folder'
        )
        folders.extend([
            {
                'local_path': r['local_path'],
                # 自身就是根同步点，此处传回这个参数让链接可以直接进入控制台
                'root_uid': uid,
                'root_path': r['local_path']  # 自身就是根同步点
            }
            for r in root_syncfolder_matches
        ])

        # 查询：指定文件夹在某个根同步点内
        for parent in parents:
            # 如果有结果，本地必然存在这个文件夹
            try:
                folders.extend([
                    {
                        'local_path': r['local_path'],
                        'root_uid': parent,
                        'root_path': self.query_items(
                            uid=parent, root_uid='',
                            object_type='folder'
                        )[0]['local_path']  # 此处可能会拖慢速度？
                    }
                    for r in self.query_items(
                        uid=uid, root_uid=parent,
                        object_type='folder'
                    )
                ])
            except IndexError:
                self.logger.error(
                    u'在根同步点中查询线上文件夹对应关系时发生 IndexError，可能需要更新之前的错误数据'
                )
            # 本地没有这个文件夹，但其中一个 parent 是根同步点：构造一个虚拟的（还不存在的）本地路径
            folders.extend([
                {
                    'local_path': os.path.join(
                        r['local_path'],
                        *path.replace(r['server_path'], '').split('/')
                    ),
                    'root_uid': parent,
                    # 反正本地路径不存在，会跳转到所有同步点列表，无所谓了
                    'root_path': r['local_path']
                }
                for r in self.query_items(
                    uid=parent, root_uid='',
                    object_type='folder'
                )
            ])
        # 按 local_path 去除重复项
        return {v['local_path']: v for v in folders}.values()

    def search_syncfolders(self, path=None):
        '''
        找到路径下的所有同步区
        Args:
            path <String> 目标路径
        Returns:
            <List> 其中每一项都是同步区的本地路径
        '''
        # 统一路径分隔符
        path = ''.join([os.path.abspath(path), os.path.sep, '%'])
        items = self.query_items(
            local_path=path,
            root_uid='',
            object_type='folder'
        )
        return [item['local_path'] for item in items]

    def find_syncitems(self, uid, path):
        '''
        找到某个位置下的所有同步项目，包括文件夹和文件
        Args:
            uid <Number|String> 同步文件夹的 uid
            path <String> 本地同步文件夹路径
        Returns:
            <Tuple> (文件夹列表, 文件列表)
        '''
        # 统一路径分隔符，并拼接模糊查询
        path = ''.join([os.path.abspath(path), os.path.sep, '%'])

        files = self.query_items(
            root_uid=uid,
            local_path=path,
            object_type='file'
        )
        folders = self.query_items(
            root_uid=uid,
            local_path=path,
            object_type='folder'
        )
        return folders, files

    def get_syncfolder_root(self, local_path=None):
        if not local_path:
            return local_path, None
        items = self.query_items(local_path=local_path, object_type='folder')
        if len(items) == 0:
            return local_path, None
        item = items[0]
        roots = self.query_items(
            uid=item['root_uid'], root_uid='',
            object_type='folder'
        )
        if len(roots) == 0:
            return local_path, None
        return roots[0]['local_path'], roots[0]['uid']

    def upload_file(
        self, token, local_path, root_uid=None, last_push=None,
        parent_rev=None, folder_uid=None, file_uid=None,
        usage='upload', resumable=False,
        skip_db=False, silent=True, autorename=True,
        rename_to=None, on_progress=None, auto_fork=False,
        setprivate=False, allow_duplicate=False, delete_on_cancel=False,
        show_console=True, notify_subscribers=True,
    ):
        '''
        上传文件
        Args:
            token <String> token
            local_path <String> 文件的本地路径
            root_uid <String|Number> 同步区的 uid （上传单个文件不需要）
            last_push <String> 上次同步时间（上传单个文件不需要）
            parent_rev <String|Number> 基于的版本号（上传单个文件不需要）
            folder_uid <String|Number> 要上传到的文件夹的 uid
            file_uid <String|Number> 文件的 uid （上传单个文件不需要）
            usage <String> 文件上传用途
            resumable <Bool> 废弃参数，现在所有文件都使用支持续传的方式
            skip_db <Bool> 略过数据库写入
            silent <Bool> 不进行冒泡提醒
            autorename <Bool> 自动处理文件重名
            rename_to <String> 文件需要上传为与本地 basename 不同的名字
            on_progress <Callable> 上传回调函数，每上传一块（4MB）调用一次，参数 offset, size, fpath
            auto_fork <Bool> （文件当前不允许上传新版本时）自动上传为分支版本
            allow_duplicate <Bool> 是否上传重复文件（内容重复）
            setprivate <Bool> 是否为保密上传
            show_console <Bool> 上传出错时是否弹出控制台
            notify_subscribers <Bool> 上传完成后是否触发站点通知，默认为 True
        Returns:
            <Dict> 数据库中文件表的一行
        '''
        # 每次上传一部分，会调用这个回调，可以用来显示进度等
        # 参数: 上传到的 offset 和文件总大小，可选参数 filename
        if not is_valid_file(local_path):
            self.logger.warn("not a valid local file path: %s", local_path)
            return

        self.__auth_oc(token)
        fname = rename_to or os.path.basename(local_path)

        # edo_client.get_client is way too expensive (it accesses network),
        # so here we init the client locally

        not silent and ui_client.message(
            _('Upload started'), local_path, type='info'
        )
        self.logger.debug(
            u'upload_file 上传：%s, 文件大小: %d 字节',
            fname, get_filesize(local_path)
        )

        # prepare upload
        chunk_size = 4 * 2 ** 20  # 4MB per chunk
        signcode_expiration = 60 * 60 * 24 * 1  # 1day expiration

        # 上传新文件时，uid 的值是文件夹的 uid
        # 上传新版本时，uid 的值是文件的 uid
        target_uid = folder_uid if parent_rev is None else file_uid

        def progress_callback(offset, size, fpath=None):
            if callable(on_progress):
                try:
                    on_progress(offset, size, filename=fname)
                except AbortUpload:
                    self.logger.info(
                        u'上传回调取消了本次上传，文件 %s 上传到 %s 字节处',
                        fname, offset
                    )
                    ui_client.update_progress(
                        self.worker_id, direction='up',
                        fpath=local_path, filename=fname, size=size,
                        progress=(offset * 100.0 / size) if size else 100,
                        status=_('Cancelled'), uid=target_uid
                    )
                    raise
                except:
                    self.logger.debug(u'上传进度回调可能有问题', exc_info=True)
            if self.worker_id is not None:
                ui_client.update_progress(
                    self.worker_id, direction='up',
                    fpath=local_path, filename=fname, size=size,
                    progress=(offset * 100.0 / size) if size else 100,
                    uid=target_uid
                )

        fsize = get_filesize(local_path)
        file_hash = get_file_hash(local_path)
        # 空文件不做去重
        if fsize == 0:
            allow_duplicate = True

        while 1:
            # 优先从 workerdb 中读取上传凭证
            # 如果 workerdb 中没有上传凭证，申请一个
            new_ticket_required = True  # 是否需要申请全新的上传凭证
            ticket_expired = False  # 是否需要进行续期操作
            ticket_deadline = None
            workerdb = get_worker_db(self.worker_id)
            upload_ticket = workerdb.get('tickets', {}).get(local_path, {})
            # 如果 workerdb 中的上传凭证过期了，调用 renew_upload_ticket 接口续期，并合并到过期的凭证中
            if upload_ticket:
                self.logger.debug(u'加载了上次保存的 %s 上传凭证', local_path)
                ticket_expired = new_ticket_required = False
                ticket_deadline = upload_ticket.get('expires_at', None)
                if ticket_deadline is not None and time.time() > ticket_deadline:
                    ticket_expired = True

            if not new_ticket_required and not ticket_expired:
                # 不需要申请新的上传凭证，也不需要进行续期，说明之前的凭证有效
                self.logger.debug(u'加载的上传凭证有效，直接使用')

            if ticket_expired:
                # 需要进行续期
                self.logger.debug(u'加载的上传凭证已过期，续期...')
                try:
                    new_upload_ticket = self.wo_client.content.renew_upload_ticket(
                        expire=signcode_expiration,
                        uid=target_uid,
                        filename=fname,
                        maxsize=fsize,
                        parent_rev=parent_rev,
                        auto_fork=auto_fork,
                        notify_subscribers=notify_subscribers
                    )
                except ApiError as e:
                    # 续期出错
                    if e.status == 400 and e.code == 457:
                        # 站点使用的存储设备不支持续期，重新申请上传凭证
                        self.logger.debug(u"不支持续期，重新申请上传凭证")
                        new_ticket_required = True
                    else:
                        self.logger.exception(u"续期出错")
                        raise
                else:
                    new_ticket_required = False
                    upload_ticket.update(new_upload_ticket)
                    ticket_deadline = time.time() + upload_ticket.get('expire', signcode_expiration) - 5

            if new_ticket_required:
                # 申请新的上传凭证
                try:
                    self.logger.debug(u'申请 %s (%s) 的全新上传凭证', local_path, target_uid)
                    upload_ticket = self.wo_client.content.get_upload_signcode(
                        expire=signcode_expiration,
                        uid=target_uid,
                        filename=fname,
                        maxsize=fsize,
                        parent_rev=parent_rev,
                        auto_fork=auto_fork,
                        notify_subscribers=notify_subscribers,
                        setprivate=setprivate,
                        hash=file_hash,
                    )
                    self.logger.debug('upload ticket: %s', upload_ticket)
                    if not upload_ticket.get('duplicated_files'):
                        progress_callback(0, fsize, fpath=fname)
                    ticket_deadline = time.time() + upload_ticket.get('expire', signcode_expiration) - 5
                except ApiError as e:
                    self.logger.debug(u'获取上传凭证出错', exc_info=True)
                    if autorename and e.code == 409:
                        # FIXME 获取重名文件的信息，更新状态为“重名”
                        parent_folder = self.wo_client.content.properties(uid=folder_uid)
                        dup_path = '/'.join([parent_folder['path'], fname])
                        try:
                            dup = self.wo_client.content.properties(path=dup_path)
                        except ApiError as e:
                            self.logger.exception(u"查询重名文件出错: %s", dup_path)
                            status = _("Failed")
                            if e.code != 401:
                                error_detail = classify_exception(e)
                            else:
                                error_detail = {
                                    "code": e.code,
                                    "msg": e.message,
                                    "detail": _(
                                        "Error querying duplicate files: {}"
                                    ).format(_("Permission denied"))
                                }
                            ui_client.update_progress(
                                self.worker_id, direction='up',
                                fpath=local_path, filename=fname, size=fsize,
                                progress=0, status=status, error=error_detail,
                                show_console=show_console
                            )
                            raise
                        dup['local_path'] = local_path
                        if '_duplicated_files' not in workerdb:
                            workerdb['_duplicated_files'] = {}
                        workerdb['_duplicated_files'][local_path] = {
                            'path': local_path,
                            'worker_id': self.worker_id,
                            'filename': fname,
                            'size': fsize,
                            'parent_folder': folder_uid,
                            'duplicated_uid': dup['uid'],
                            'duplicated_types': dup['object_types'],
                            'duplicated_revision': dup.get('revision'),
                        }
                        workerdb.sync()
                        return dup
                    error_detail = classify_exception(e)
                    if e.code == 408:
                        status = _('File size limit exceeded')
                    elif e.code == 404:
                        # 获取凭证时出现 404 的错误，说明站点文件被删除了
                        error_detail = {
                            "code": 404,
                            "msg": "File not found",
                            "detail": _("Site file is deleted and new version can not be uploaded.")  # noqa E501
                        }
                        status = _('Failed')
                    else:
                        status = _('Failed')
                    ui_client.update_progress(
                        self.worker_id, direction='up',
                        fpath=local_path, filename=fname, size=fsize,
                        progress=0, status=status, error=error_detail,
                        uid=target_uid, show_console=show_console
                    )
                    # Do we have a better option here?
                    if e.code == 408:
                        return {
                            'local_path': local_path,
                            'modified': get_iso_mtime(local_path),  # 此处应该用本地文件创建时间
                            'root_uid': root_uid or '',  # 零散文件没有根节点
                            'conflict': int(False),
                            'usage': usage or ''
                        }
                    raise

            # 文件内容重复（通过MD5检查得到）
            # 注意：映射盘或同步任务中上传新文件时，如果文件内容重复，「取消上传」操作中，应当将本地同步区的文件移入 .edo 目录下
            if not allow_duplicate and upload_ticket.get('duplicated_files'):
                self.logger.warn(u'文件内容重复，%s, 站点已有文件 %s', upload_ticket['hash'], upload_ticket['duplicated_files'])
                # TODO 映射盘任务，
                if '_duplicated_files' not in workerdb:
                    workerdb['_duplicated_files'] = {}
                workerdb['_duplicated_files'][local_path] = {
                    'path': local_path,
                    'worker_id': self.worker_id,
                    'filename': fname,
                    'size': fsize,
                    'parent_folder': folder_uid,
                    'duplicated_uid': upload_ticket['duplicated_files'][0],  # 线上内容相同文件的 uid
                    'status': _('Content Duplicated'),
                    'revision': parent_rev,
                    'file_uid': file_uid,
                    'delete_on_cancel': delete_on_cancel,
                }
                workerdb.sync()
                if file_uid:
                    return {'local_path': local_path, 'uid': file_uid, 'revision': parent_rev}
                else:
                    return {'local_path': local_path}

            try:
                # TESTING We should either cache up_client or init it locally,
                # asking OC server each time is way too expensive
                upload_ticket.pop('expires_at', None)
                up_client = get_upload_client(
                    token=token,
                    server=upload_ticket.get('upload_server'),
                    account=self.account,
                    instance=self.instance
                )
                metadata = up_client.upload.upload(
                    local_path, copy.deepcopy(upload_ticket),
                    on_progress=progress_callback, chunk_size=chunk_size
                )
            except AbortUpload:
                not silent and ui_client.message(
                    _('Upload'),
                    _('File {} modified during uploading, aborted').format(
                        fname
                    ),
                    type='info'
                )
                self.logger.debug(u'上传期间文件被修改, 重新上传')
                # 保存上传凭证到 workerdb 中，上传完这个文件再删除
                # 如果中途出错，下次优先读取保存的凭证
                workerdb = get_worker_db(self.worker_id)
                if 'tickets' not in workerdb:
                    workerdb['tickets'] = {}
                workerdb['tickets'][local_path] = copy.deepcopy(upload_ticket)
                workerdb['tickets'][local_path]['expires_at'] = ticket_deadline
                workerdb.sync()
                continue
            except ApiError as e:
                self.logger.debug(u'上传文件内容出错', exc_info=True)
                if autorename and e.code == 409:
                    # FIXME 获取重名文件的信息，更新状态为“重名”
                    parent_folder = self.wo_client.content.properties(uid=folder_uid)
                    dup = self.wo_client.content.properties(
                        path='/'.join([parent_folder['path'], fname])
                    )
                    dup['local_path'] = local_path
                    if '_duplicated_files' not in workerdb:
                        workerdb['_duplicated_files'] = {}
                    workerdb['_duplicated_files'][local_path] = {
                        'path': local_path,
                        'worker_id': self.worker_id,
                        'filename': fname,
                        'size': fsize,
                        'parent_folder': folder_uid,
                        'uid': dup['uid'],
                        'revision': dup['revision'],
                    }
                    workerdb.sync()
                    return dup
                elif e.code == 404:
                    self.logger.debug(u'出现404错误，开始检查线上文件')
                    # 文件可能之前已经传上去了，但是最后文件信息可能因为网络原因响应失败，导致没有清除上传会话
                    # 如果线上文件与上传会话一致，就认为已经上传完成
                    if parent_rev is None:
                        site_folder = self.wo_client.content.properties(uid=target_uid)
                        site_file = self.wo_client.content.properties(path=site_folder['path'] + '/' + fname)
                    else:
                        site_file = self.wo_client.content.properties(uid=target_uid)

                    self.logger.debug(u'线上文件信息: device=%s, key=%s', site_file.get('mdfs_device'), site_file.get('mdfs_key'))
                    self.logger.debug(u'本地ticket信息: device=%s, key=%s', upload_ticket.get('mdfs_device'), upload_ticket.get('mdfs_key'))
                    if all([
                        site_file.get('mdfs_device'),
                        upload_ticket.get('mdfs_device'),
                        site_file.get('mdfs_device', None) == upload_ticket.get('mdfs_device'),
                        site_file.get('mdfs_key', None) == upload_ticket.get('mdfs_key'),
                    ]):
                        self.logger.info(u'线上文件%s(uid=%s)与本地一致，略过上传', site_file['path'], site_file['uid'])
                        metadata = site_file
                    else:
                        raise
                ui_client.update_progress(
                    self.worker_id, direction='up',
                    fpath=local_path, filename=fname, size=fsize,
                    progress=0, status=_('Failed'),
                    error=classify_exception(e),
                    uid=target_uid, show_console=show_console
                )
                # 保存上传凭证到 workerdb 中，上传完这个文件再删除
                # 如果中途出错，下次优先读取保存的凭证
                workerdb = get_worker_db(self.worker_id)
                if 'tickets' not in workerdb:
                    workerdb['tickets'] = {}
                workerdb['tickets'][local_path] = copy.deepcopy(upload_ticket)
                workerdb['tickets'][local_path]['expires_at'] = ticket_deadline
                workerdb.sync()
                raise
            else:
                self.logger.debug(u'metadata is: %r', metadata)
                # 当 worker_id 为 None 时，说明是在 webserver 主线程里。
                # 此时不允许调用 webserver 自己的接口。
                if self.worker_id is not None:
                    ui_client.update_progress(
                        self.worker_id, direction='up', fpath=local_path,
                        filename=fname, size=fsize, progress=100,
                        uid=metadata['uid']
                    )
                # 上传完成之后，删除这个文件的上传凭证
                workerdb = get_worker_db(self.worker_id)
                workerdb.get('tickets', {}).pop(local_path, None)
                workerdb.sync()
                break

        if metadata is None and file_uid:
            metadata = self.wo_client.content.properties(uid=file_uid)

        self._refresh_file_manager(local_path)

        data = {
            'uid': metadata['uid'],
            'revision': metadata['revision'],
            'local_path': local_path,
            'server_path': metadata['path'],
            'modified': get_iso_mtime(local_path),  # 此处应该用本地文件创建时间
            'md5': file_hash,
            'root_uid': root_uid or '',  # 零散文件没有根节点
            'conflict': int(False),
            'last_push': datetime.utcnow().isoformat(),
            'usage': usage or ''
        }
        not skip_db and self.save_item(data, object_type='file')
        not silent and ui_client.message(
            _('Upload finished'), os.path.basename(local_path), type='info'
        )
        return data

    def mark_conflict(
        self, conflict_type, local_path=None,
        object_type='file'
    ):
        '''
        标记一条记录（同步区子文件夹/文件）为冲突
        Args:
            conflict_type <Boolean> 冲突类型
                0 无冲突
                1 修改冲突
                2 线上删除冲突
                3 线下删除冲突
                4 创建冲突，应该将本地项目版本标为 0 之后 mark 为 1: 修改冲突
            local_path <String> 要标记的文件的本地路径
        Returns:
            None
        '''
        types = {
            0: _('No conflict'),
            1: _('Both modified'),
            2: _('Remote removed'),
            3: _('Local removed'),
            4: _('Both created'),
        }

        if object_type == 'file':
            fobjs = self.query_items(
                local_path=local_path,
                object_type='file'
            )
        elif object_type == 'folder':
            fobjs = self.query_items(
                local_path=local_path,
                object_type='folder'
            )
        else:
            raise ValueError("No this object_type: %s" % (object_type))

        for fobj in fobjs:
            fobj.update({'conflict': int(conflict_type)})
            self.save_item(fobj)

            if object_type == 'file':
                if int(conflict_type) != 0:
                    self.logger.debug(
                        u'mark_conflict 标记 %s 为%s冲突',
                        fobj['local_path'],
                        types[int(conflict_type)]
                    )
                    try:
                        md = self.wo_client.content.properties(uid=fobj['uid'])
                        fobj.update(md)
                    except ApiError, e:
                        if e.status == 404:
                            self.logger.warn(
                                u'文件的线上版本已被删除: %s',
                                fobj
                            )

                    # 更改当前文件的同步状态
                    self._refresh_file_manager(fobj['local_path'])
            else:
                # 更改当前文件夹的同步状态
                self._refresh_file_manager(fobj['local_path'])

    def _refresh_file_manager(self, lpath):
        '''
        刷新文件管理器中指定项目的状态
        - 当前只支持 Windows
        '''
        if not os.path.exists(lpath):
            return
        if platform.system() == 'Windows':
            shell.SHChangeNotify(
                shellcon.SHCNE_UPDATEDIR,
                shellcon.SHCNF_PATH,
                lpath.encode(sys.getfilesystemencoding()),
                None
            )

    def resolve_conflict(self, fobj, action=None):
        '''
        解决指定文件的冲突
        '''
        NO_CONFLICT = 0
        resolve_actions = {
            # 修改冲突
            CONFLICT_BOTH_MODIFIED: ('merged', 'use_local', 'use_remote', ),
            # 线上删除冲突
            CONFLICT_SERVER_DELETED: ('remove_local', 'reserve_local', 'use_local', 'use_remote'),
            # 本地删除冲突
            CONFLICT_CLIENT_DELETED: ('remove_remote', 'reserve_remote', 'use_local', 'use_remote'),
            # 没有冲突，但如果本地文件有修改，也允许放弃本地修改
            CONFLICT_NO_CONFLICT: ('use_remote', ),
        }
        self.logger.debug(
            u'resolve_conflict 解决 %s 的 [%s] 冲突，策略是: %s',
            fobj['local_path'], fobj['conflict'], action
        )
        # 文件处于非冲突状态
        # NOTICE: 唯一的例外：本地文件修改了，这时候可以放弃本地修改，与冲突的处理一致
        if fobj['conflict'] not in resolve_actions.keys():
            self.logger.debug(u"resolve_conflict 无须处理")
            return False

        # 非预期操作
        if action not in resolve_actions[fobj['conflict']]:
            self.logger.debug(u"resolve_conflict 非预期冲突处理策略：%s", action)
            return False

        if action == 'merged':
            # 仅标记为冲突解决
            # TODO 这个是不是可以废弃了？
            online_fobj = self.wo_client.content.properties(uid=fobj['uid'])
            try:
                fobj.update({
                    'revision': int(online_fobj['revision'])+1,
                    'conflict': NO_CONFLICT,
                })
                self.save_item(fobj)
            except:
                self.logger.warn(u'resolve_conflict 遇到错误', exc_info=True)
                return False
        elif action == 'use_local':
            # 两端同时修改造成冲突，保留本地版本（强制上传）
            self.logger.debug(u"使用强制上传策略处理同步冲突文件")
            try:
                online_fobj = self.wo_client.content.properties(uid=fobj['uid'])
            except ApiError as e:
                self.logger.exception(u"resolve_conflict 查询 %s 时出错", fobj["uid"])
                if 404 != e.status:
                    raise
                self.logger.debug(u"站点文件被删除，重新上传")
                self.delete_item(
                    local_path=fobj['local_path'],
                    object_type=fobj['object_type']
                )
                self.push(fobj["local_path"])
            else:
                self.logger.debug(u"站点文件存在，上传新版本")
                try:
                    fobj.update({
                        'revision': int(online_fobj['revision']),
                        'conflict': NO_CONFLICT,
                    })
                    self.save_item(fobj)
                    self.push(fobj['local_path'])
                except:
                    self.logger.warn(u'使用 %s 策略解决冲突遇到错误', action, exc_info=True)
                    return False
        elif action == 'use_remote':
            # 两端同时修改造成冲突，保留线上版本（强制下载）
            # 即使目标路径可能处于监视中（实时同步），此处也不需要先下载到别处再移动替换
            # 因为下载本身就是先写入到 . 开头的临时文件，之后在同一目录中移动替换文件
            # 而以 . 开头的临时文件会被实时同步忽略
            self.logger.debug(u"使用放弃本地修改策略处理同步冲突文件")
            backup_dir = os.path.join(
                os.path.dirname(fobj['local_path']), '.edo'
            )
            if not os.path.isdir(backup_dir):
                os.makedirs(backup_dir)
            self.logger.debug(u"备份本地修改的文件到 %s", backup_dir)
            try:
                shutil.move(fobj['local_path'], backup_dir)
            except Exception:
                # 移动失败，忽略
                self.logger.exception(u"备份本地修改的文件失败")

            try:
                online_fobj = self.wo_client.content.properties(uid=fobj['uid'])
            except ApiError as e:
                self.logger.exception(u"resolve_conflict 查询 %s 时出错", fobj["uid"])
                if 404 != e.status:
                    raise
                self.logger.debug(u"站点文件被删除，不重新下载")
            else:
                self.logger.debug(u"站点文件存在，开始重新下载")
                self.download_file(
                    self.wo_client.token_code,
                    online_fobj,
                    os.path.dirname(fobj['local_path']),
                    file_name=os.path.basename(fobj['local_path']),
                    usage='sync',
                    root_uid=fobj.get('root_uid', '')
                )
        elif action == 'remove_local':
            # （文件夹线上删除冲突）将本地文件夹和其记录删除
            if fobj['object_type'] == 'folder':
                folders, files = self.find_syncitems(
                    fobj['uid'],
                    fobj['local_path']
                )
                for f in folders + files:
                    try:
                        os.remove(f['local_path'])
                    except:
                        self.logger.warn(u'冲突解决：删除文件遇到错误', exc_info=True)
                    self.delete_item(
                        f['local_path'],
                        object_type=f['object_type']
                    )
                try:
                    os.remove(fobj['local_path'])
                except:
                    self.logger.warn(u'冲突解决：删除文件遇到错误', exc_info=True)
                self.delete_item(
                    fobj['local_path'],
                    object_type=fobj['object_type']
                )
                return True
            # 将本地文件和文件记录删除
            try:
                self.delete_file(fobj['local_path'])
            except:
                self.logger.warn(u'resolve_conflict 遇到错误', exc_info=True)
                return False
        elif action == 'reserve_local':
            # 删除同步记录，然后重新上传
            try:
                # 如果 fobj 是目录，则将其子项记录删除
                if fobj['object_type'] == 'folder':
                    folders, files = self.find_syncitems(
                        fobj['uid'],
                        fobj['local_path']
                    )
                    for f in folders + files:
                        self.delete_item(
                            f['local_path'],
                            object_type=f['object_type']
                        )
                # 将 fobj 自身的记录删除
                self.delete_item(
                    local_path=fobj['local_path'],
                    object_type=fobj['object_type']
                )
            except Exception:
                self.logger.warn(u'resolve_conflict 遇到错误', exc_info=True)
                return False
            self.push(fobj['local_path'])
        elif action == 'remove_remote':
            # 将本地文件记录的版本改为线上版本
            online_fobj = self.wo_client.content.properties(uid=fobj['uid'])
            try:
                fobj.update({
                    'revision': online_fobj['revision'],
                    'conflict': NO_CONFLICT
                })
                self.save_item(fobj)
            except:
                self.logger.warn(u'resolve_conflict 遇到错误', exc_info=True)
                return False
        elif action == 'reserve_remote':
            # 将线上版本下载到本地并覆盖本地之前的文件
            online_fobj = self.wo_client.content.properties(uid=fobj['uid'])
            try:
                # 将线上的文件下载到本地
                download_folder, file_name = os.path.split(fobj['local_path'])
                self.download_file(
                    self.token, online_fobj,
                    download_folder, file_name,
                    usage='sync', root_uid=fobj['uid']
                )

                fobj.update({
                    'revision': online_fobj['revision'],
                    'conflict': NO_CONFLICT,
                    'md5': get_file_md5(fobj['local_path'])
                })
                self.save_item(fobj)
            except:
                self.logger.warn(u'resolve_conflict 遇到错误', exc_info=True)
                return False

        self._refresh_file_manager(fobj['local_path'])
        return True

    def list_conflicts(self, root_uid=None, root_local_folder=None):
        '''
        获取指定的本地文件夹路径中所有冲突文件列表
        Args:
            root_uid <String|Number> 同步区的 uid
            root_local_folder <String> 同步区的本地路径
        Returns:
            <List>
        '''
        if not root_local_folder:
            root_local_folder = self.query_items(
                uid=root_uid, root_uid='', object_type='folder'
            )[0]['local_path']

        root_local_folder = ''.join([root_local_folder, os.path.sep, '%'])
        conflict_list = []
        for conflict_type in xrange(1, 4):
            conflict_list.extend(
                self.query_items(
                    root_uid=root_uid,
                    local_path=root_local_folder,
                    conflict=conflict_type,
                    object_type='file'
                )
            )
        return conflict_list

    def list_all_syncfolders(self):
        '''
        获取所有同步区信息
        '''
        items = self.query_items(root_uid='', object_type='folder')
        [item.update({
            'server': self.server,
            'instance': self.instance,
            'account': self.account,
            'conflict_count': len(
                self.list_conflicts(item['uid'], item['local_path'])
            )
        }) for item in items]
        return items

    def list_all_files(self):
        '''
        获取所有零散文件的信息
        '''
        items = self.query_items(root_uid='', object_type='file')
        [item.update({
            'server': self.server,
            'instance': self.instance,
            'account': self.account
        }) for item in items]
        return items

    def stat_files(self):
        '''
        统计零散文件的分类
        '''
        stat_info = {}
        for f in self.query_items(root_uid='', object_type='file'):
            usage = f.get('usage', 'unknown') or 'unknown'
            if usage not in stat_info:
                stat_info[usage] = 1
            else:
                stat_info[usage] += 1
        return stat_info

    def delete_file(
        self, local_path, delete_file=True, delete_site_file=False,
        ignore_errors=True
    ):
        '''
        删除指定文件，同时删除相关的文件记录
        '''
        # 只允许删除在表中的文件
        file_objs = self.query_items(local_path=local_path, object_type='file')
        for file_obj in file_objs:
            self.delete_item(local_path=local_path, object_type='file')
            if delete_site_file:
                try:
                    self.wo_client.content.delete(uid=file_obj['uid'])
                except:
                    self.logger.warn(
                        u'delete_file 试图删除站点文件`%s`时发生了错误',
                        file_obj["uid"], exc_info=True
                    )
                    if not ignore_errors:
                        raise

        if not file_objs and not delete_file:
            site_file = self._get_site_path(local_path=local_path)
            try:
                self.wo_client.content.delete(path=site_file)
            except:
                self.logger.warn(
                    u'delete_file 试图删除站点文件`%s`时发生了错误',
                    site_file, exc_info=True
                )
                if not ignore_errors:
                    raise

        if delete_file:
            try:
                os.remove(local_path)
            except:
                self.logger.warn(
                    u"delete_file 试图删除本地文件`%s`时发生了错误",
                    local_path, exc_info=True
                )
                if not ignore_errors:
                    raise

    def delete_folder(
        self, local_path, delete_local_folder=True, delete_site_folder=False,
        ignore_errors=True
    ):
        '''
        删除指定文件，同时删除相关的文件记录
        '''
        # 删除表中的文件夹数据
        items = self.query_items(local_path=local_path, object_type="folder")
        item = items[0] if items else None
        self.delete_item(local_path=local_path, object_type="folder")

        # 构造出包含文件夹中的所有文件模糊查询的路径
        query_path = os.path.join(local_path, '%')
        folder_objs = self.query_items(local_path=query_path, object_type='folder')
        for folder_obj in folder_objs:
            self.delete_item(local_path=folder_obj['local_path'], object_type='folder')

        # 删除文件夹中的文件数据
        file_objs = self.query_items(local_path=query_path, object_type='file')
        for file_obj in file_objs:
            self.delete_file(file_obj['local_path'], True, True, ignore_errors)

        # 删除本地文件夹
        if delete_local_folder:
            try:
                shutil.rmtree(local_path)
            except:
                self.logger.warn(
                    u'delete_folder 试图删除本地目录`%s`时发生了错误',
                    local_path, exc_info=True
                )
                if not ignore_errors:
                    raise

        # 删除线上文件夹
        if delete_site_folder:
            if not item and not delete_local_folder:
                site_folder = self._get_site_path(local_path=local_path)
                item = self.wo_client.content.properties(path=site_folder)
            try:
                self.wo_client.content.delete(uid=item['uid'])
            except:
                self.logger.warn(
                    u'delete_folder 试图删除站点目录`%s`时发生了错误',
                    item["uid"], exc_info=True
                )
                if not ignore_errors:
                    raise


    def remove_syncfolder(self, local_path):
        '''
        解除同步区关联（删除同步区及其中所有内容的数据库记录）
        '''
        # 查询到同步区的 uid
        connection = self._get_site_db()
        cursor = connection.cursor()
        sql_select = (
            'SELECT `uid` FROM `sync_folders` '
            'WHERE `local_path`=? AND `root_uid`=""'
        )
        cursor.execute(sql_select, (local_path, ))
        try:
            sync_folder_uid = cursor.fetchone()
        except TypeError:
            self.logger.warn(
                u'remove_syncfolder: 数据库查询时出错',
                exc_info=True
            )
            return
        if sync_folder_uid is None:
            return
        else:
            sync_folder_uid = sync_folder_uid[0]

        # 删除所有归属于同步区的文件和文件夹记录
        sql_delete = ('DELETE FROM `{}` '
                      'WHERE `root_uid`=? AND `local_path` LIKE ?')
        for table in ('site_files', 'sync_folders', ):
            cursor.execute(
                sql_delete.format(table),
                (
                    sync_folder_uid,
                    ''.join([local_path, os.path.sep, '%']),
                )
            )
            connection.commit()

        # 最后删除同步区记录
        self.delete_item(local_path=local_path, object_type='folder')

    def delete_item(self, local_path=None, object_type='file'):
        '''
        删除一条数据库记录
        Args:
            isfolder <Boolean> 指定记录是否是文件夹
            local_path <String> 项目的本地路径
        Returns:
            None
        '''
        if object_type == 'file':
            table = 'site_files'
        elif object_type == 'folder':
            table = 'sync_folders'

        connection = self._get_site_db()
        cursor = connection.cursor()
        sql_delete = '''DELETE FROM `{}` WHERE `local_path`=?'''.format(table)
        cursor.execute(sql_delete, (local_path, ))
        connection.commit()

    def file_changed(self, file_path, data):
        '''
        检查文件是否变化
        Args:
            file_path <String> 文件路径
            data <Dict|?> 带有 modified 和 md5 两个 key 的对象
        Returns:
            <Boolean> 文件变化返回 True 否则返回 False
        '''
        if not os.path.exists(file_path):
            return True
        # 首先比较文件修改时间
        try:
            l_mtime = get_iso_mtime(file_path)
            r_mtime = datetime.strptime(
                data['modified'],
                '%Y-%m-%dT%H:%M:%S.%f'
            ).isoformat()
        except ValueError:
            try:
                r_mtime = datetime.strptime(
                    data['modified'],
                    '%Y-%m-%dT%H:%M:%S'
                ).isoformat()
            except:
                self.logger.warn(u'FS.file_changed: ValueError', exc_info=True)
                return data.get('md5', None) != get_file_md5(file_path)
        # 文件修改时间不一致也可能内容未变化，再检查哈希值
        if r_mtime != l_mtime:
            return data.get('md5', None) != get_file_md5(file_path)
        return r_mtime != l_mtime

    def file_changed_time(self, file_path, data):
        if not os.path.exists(file_path):
            return True
        l_mtime = get_iso_mtime(file_path)
        try:
            r_mtime = datetime.strptime(
                data['modified'],
                '%Y-%m-%dT%H:%M:%S.%f'
            ).isoformat()
        except ValueError:
            try:
                r_mtime = datetime.strptime(
                    data['modified'],
                    '%Y-%m-%dT%H:%M:%S'
                ).isoformat()
            except:
                return True
        if r_mtime != l_mtime:
            return True
        return False

    def remote_diff(
        self, client=None, root_uid=None, uid=None,
        root_local_path=None, local_path=None
    ):
        '''
        服务器上，某个文件夹的差异
        Args:
            token <String> token
            root_uid <String|Number> 同步区的 uid
            uid <String|Number> 当前要计算差异的文件夹的 uid
            root_local_path <String> 同步区的本地路径
            local_path <String> 要计算差异的文件夹的本地路径
        Returns:
            <Dict> 含有以下键，值为列表
                new 新增的文件/文件夹
                modified 修改的文件
                removed 删除的文件/文件夹
                moved 空列表
        '''
        def depth1(i):
            '''
            过滤出当前层次的文件和文件夹
            '''
            sep = os.path.sep
            left_path_part = i['local_path'].replace(local_path, '')
            return left_path_part.count(sep) == 1\
                and left_path_part.startswith(sep)

        if client.token_code:
            self.__auth_oc(client.token_code)
        self.logger.debug(u'remote_diff 开始检查改动: %s', local_path)
        if local_path is None:
            self.logger.warn(
                u'remote_diff 未预料的值：local_path==None，uid==%s, 对应的服务端项为%s',
                str(uid), client.content.properties(uid=uid)
            )
        # 列出服务器端文件夹的所有内容
        start, limit = 0, 1000
        remote_items = client.content.items(uid=uid, start=start, limit=limit)
        is_first_time = True
        while is_first_time or len(remote_items) > 0:
            # 首次查询，无论查询结果数量是否为 0，都需要进行 diff
            # 每查询一次则进行 diff，diff 完成后再进行下一次查询
            folders, files = self.find_syncitems(root_uid, root_local_path)
            new_folders, removed_folders, unknown_folders = [], [], []

            local_items = folders + files
            local_items = filter(depth1, local_items)

            local_item_uids = [str(i['uid']) for i in local_items]
            unknown_folders = filter(depth1, folders)

            for item in remote_items:
                # 获取新增的项目
                if str(item['uid']) not in local_item_uids:
                    if is_folder(item):
                        self.logger.debug(u'remote_diff 找到新文件夹: %s', item['path'])
                        new_folders.append({
                            'type': 'new_folder',
                            'item': item
                        })
                    elif is_file(item):
                        self.logger.debug(u'remote_diff 找到新文件: %s', item['path'])
                        yield {'type': 'new_file', 'item': item}
                else:
                    if is_folder(item):
                        for _each in search_dict_list(
                            unknown_folders,
                            pair={'uid': str(item['uid'])}
                        ):
                            _each.update({'name': item['name']})

            for item in local_items:
                remote_found = False
                for remote_item in remote_items:
                    if str(remote_item['uid']) == str(item['uid']):
                        remote_found = True
                        if is_file(remote_item) and\
                                (str(remote_item['revision']) != str(item['revision'])
                                 or remote_item['path'] != item['server_path']):
                            remote_item.update({
                                'md5': item['md5'],
                                'modified': item['modified'],
                                'local_path': item['local_path']
                            })
                            self.logger.debug(
                                u'remote_diff 找到修改的文件: %s',
                                item['local_path']
                            )
                            yield {'type': 'modified_file', 'item': remote_item}
                if not remote_found:
                    if item['object_type'] == 'file':
                        self.logger.debug(
                            u'remote_diff 找到删除的文件: %s',
                            item['local_path']
                        )
                        yield {'type': 'removed_file', 'item': item}
                    elif item['object_type'] == 'folder':
                        self.logger.debug(
                            u'remote_diff 找到删除的文件夹: %s',
                            item['local_path']
                        )
                        removed_folders.append({
                            'type': 'removed_folder',
                            'item': item
                        })
                        unknown_folders.remove(item)

            for folder in removed_folders:
                yield folder
            for folder in new_folders:
                yield folder
            for folder in unknown_folders:
                self.logger.debug(u'remote_diff 找到常规文件夹: %s', folder['local_path'])
                yield {'type': 'unknown_folder', 'item': folder}

            start += limit
            remote_items = client.content.items(
                uid=uid, start=start, limit=limit
            )
            is_first_time = False

    def get_cached_file(self, uid, revision, local_path=None, usage=None):
        '''
        缓存查询
        若本地有指定版本的未修改文件，直接返回文件信息，否则返回 False 。
        查询 site_db.site_files
        条件： (uid=? and revision?) and
              (modified 没有变化 或 (modified 变化 但 md5 不变))
        需要： 更新数据 if (modified 变化 or md5 变化 or 文件删除)
        Args:
            uid <String> 文件 uid
            revision <String|Integer> 文件的版本
        Returns:
            <Dict> 包含文件信息的字典 or False <Boolean>
        '''
        items = self.query_items(uid=uid, usage=usage, conflict='0',
                                 revision=revision, object_type='file')
        for fobj in items:
            # 文件是否变化的 Flag
            if os.path.exists(fobj['local_path']):
                # 检查缓存文件类型是否匹配
                if os.path.splitext(fobj["server_path"])[-1] != os.path.splitext(fobj["local_path"])[-1]:
                    continue
                if local_path == fobj['local_path']:
                    continue
                # 检查文件是否变化
                file_changed = self.file_changed(fobj['local_path'],
                                                 fobj)
                # 若没有变化，返回这一文件条目（Dict类型）
                if not file_changed:
                    return fobj
                # TODO: 文件被修改，做一些操作
                else:
                    pass
            else:
                pass
                # 本地文件被删除，更新数据库记录
                # cursor.execute('''DELETE FROM `site_files` WHERE `uid`=? AND
                # `revision`=? AND `local_path`=? AND `server_path`=? AND `modified`=? AND `md5`=?''', record)
                # connection.commit()

    def check_pull_conflict(
        self, item=None, root_uid=None,
        local_folder=None, root_local_folder=None
    ):
        '''
        检查Pull冲突
        Args:
            item <Dict> remote_diff 返回的结果
            root_uid <String|Number> 同步区的 uid
            local_folder <String> 要检查的项目所在的本地文件夹路径
            root_local_folder <String> 同步区的本地路径
        Returns:
            None
        '''
        self.logger.debug(
            u'检查PULL冲突: %s 类型是 %s，本地文件夹 %s，根同步点 %s，本地根 %s',
            item['item'].get('path', item['item'].get('local_path', None)),
            item['type'], local_folder, root_uid, root_local_folder
        )
        local_folder_items = []
        try:
            local_folder_items = os.listdir(local_folder)
        except:
            self.logger.info(u'检查 pull 冲突时可能有问题: "%s"', local_folder, exc_info=True)

        if item['type'] == 'new_file':
            if item['item']['name'] not in local_folder_items:
                return False
            # 服务端新增 & 本地同名项目 => 新建冲突
            item_path = os.path.join(local_folder, item['item']['name'])
            if os.path.isfile(item_path):
                self.save_item({
                    'uid': item['item']['uid'],
                    'revision': 0,
                    'local_path': item_path,
                    'server_path': item['item']['path'],
                    'modified': get_iso_mtime(item_path),
                    'md5': get_file_md5(item_path),
                    'root_uid': root_uid,
                    'conflict': 1,
                    'last_pull': '',
                    'last_push': '',
                    'usage': 'sync'
                }, object_type='file')
                # 保存信息时已经标记冲突，此处下载冲突备份文件
                self.mark_conflict(
                    CONFLICT_BOTH_MODIFIED,
                    local_path=item_path,
                    object_type='file'
                )
                return True
            return False
        elif item['type'] == 'new_folder':
            if item['item']['name'] in local_folder_items:
                # 新建文件夹记录
                _local_path = os.path.join(
                    local_folder,
                    item['item']['name']
                )
                self.save_item({
                    'uid': item['item']['uid'],
                    'server_path': item['item']['path'],
                    'local_path': _local_path,
                    'modified': get_iso_mtime(_local_path),
                    'root_uid': root_uid,
                    'last_pull': '',
                    'last_push': '',
                    'conflict': 0
                }, object_type='folder')
                self.logger.warn(
                    u'check_pull_conflict 自动处理了文件夹新建冲突: %s',
                    _local_path
                )
            return False
        elif item['type'] == 'modified_file':
            _remote_fname = os.path.basename(item['item']['path'])
            _local_fname = os.path.basename(item['item']['local_path'])
            if os.path.exists(item['item']['local_path']):
                # 服务端修改 & 本地修改
                if self.file_changed(item['item']['local_path'], item['item']):
                    self.mark_conflict(
                        CONFLICT_BOTH_MODIFIED,
                        local_path=item['item']['local_path'],
                        object_type='file'
                    )
                    return True
                # 服务端改名
                elif _remote_fname != _local_fname:
                    self.logger.warn(
                        u'check_pull_conflict 文件可能被改名: %s => %s',
                        _local_fname, _remote_fname
                    )
                    _local_fname_new = os.path.join(
                        os.path.dirname(item['item']['local_path']),
                        _remote_fname
                    )
                    if os.path.exists(_local_fname_new)\
                            and not self.query_items(
                                local_path=_local_fname_new,
                                object_type='file'
                    ):
                        self.save_item({
                            'uid': item['item']['uid'],
                            'revision': 0,
                            'local_path': _local_fname_new,
                            'server_path': item['item']['path'],
                            'modified': get_iso_mtime(_local_fname_new),
                            'md5': get_file_md5(_local_fname_new),
                            'root_uid': root_uid,
                            'conflict': 1,
                            'last_pull': '',
                            'last_push': '',
                            'usage': 'sync'
                        }, object_type='file')
                        self.mark_conflict(
                            CONFLICT_BOTH_MODIFIED,
                            local_path=item['item']['local_path'],
                            object_type='file'
                        )
                        return True
                    else:
                        return item.get('conflict', '')
            return False
        elif item['type'] == 'removed_file':
            if os.path.exists(item['item']['local_path']):
                # 服务端删除 & 本地修改
                if self.file_changed(item['item']['local_path'], item['item']):
                    self.mark_conflict(
                        CONFLICT_SERVER_DELETED,
                        local_path=item['item']['local_path'],
                        object_type='file'
                    )
                    return True
            return False
        elif item['type'] == 'removed_folder':
            return False
        elif item['type'] == 'unknown_folder':
            return False

    def delete_tree(self, local_path, force=False, root_uid=None, client=None):
        '''
        删除本地文件夹
        - local_path 路径
        - force 忽略文件夹内修改的内容
        - root_uid 根同步点 uid
        '''
        diff_count = 0
        conflict_count = 0
        if force:
            shutil.rmtree(local_path, ignore_errors=True)
        else:
            folders, files = self.find_syncitems(root_uid, local_path)
            self.logger.debug(
                u'%s 中有文件夹: %s, 文件: %s',
                local_path, folders, files
            )
            for file_item in files:
                if not self.file_changed(
                    file_item['local_path'], file_item
                ):
                    try:
                        os.remove(file_item['local_path'])
                    except:
                        self.logger.warn(
                            u'删除文件出错: %s', file_item['local_path'],
                            exc_info=True
                        )
                    finally:
                        self.delete_item(
                            local_path=file_item['local_path'],
                            object_type='file'
                        )
                    diff_count += 1
                else:
                    self.mark_conflict(
                        CONFLICT_SERVER_DELETED,
                        local_path=file_item['local_path'],
                        object_type='file'
                    )
                    conflict_count += 1
            for folder in folders:
                # 递归删除文件夹
                _conflict_count, _diff_count = self.delete_tree(
                    folder['local_path'],
                    root_uid=folder['root_uid'],
                    client=client
                )
                diff_count += _diff_count
                conflict_count += _conflict_count
            try:
                # 如果文件夹为空，删除这个文件夹，并删除文件夹记录
                if len(os.listdir(local_path)) == 0:
                    os.rmdir(local_path)
                    self.delete_item(local_path=local_path, object_type='folder')
                else:
                    self.logger.debug(
                        u'文件夹 %s 中还有以下内容: %s',
                        local_path, os.listdir(local_path)
                    )
                    self.mark_conflict(
                        CONFLICT_SERVER_DELETED,
                        local_path=local_path,
                        object_type='folder'
                    )
                    conflict_count += 1
            except WindowsError:
                self.logger.warn(
                    u'_download_folder 遇到 Windows 错误', exc_info=True
                )
        self.logger.debug(u'删除了本地文件夹 %s', local_path)
        return conflict_count, diff_count

    def _download_folder(
        self, root_uid=None, client=None, uid=None,
        root_local_folder=None, local_folder=None,
        skip_db=False, silent=True, oncomplete=None,
        on_progress=None
    ):
        '''
        下载指定文件夹到指定的本地路径
        其中包含记录同步点内部数据的操作（会写入site_db）
        Args:
            root_uid <String|Number> 同步区的 uid
            client <WoClient> WoClient 实例
            uid <String|Number> 要下载的文件夹的 uid
            root_local_path <String> 同步区的本地路径
            local_folder <String> 要下载到的本地文件夹路径
            oncomplete <function> 下载完成后的回调函数
            on_progress <Callable> 每下载完成一块后的回调函数，参数 offset, size
        Returns:
            (conflict_count, diff_count) <Tuple>
        '''
        # 仅对变化的文件/文件夹进行操作
        self.logger.debug(
            u'下载文件夹 %s，远端目标 %s，根同步点 %s，本地根 %s %s',
            local_folder, uid,
            root_uid, root_local_folder,
            u'，将略过数据库' if skip_db else ''
        )
        diff_count = 0
        conflict_count = 0
        oncomplete = oncomplete or (lambda *args, **kwargs: None)
        not skip_db and self.save_item({
            'uid': uid,
            'local_path': local_folder,
            'last_pull': datetime.utcnow().isoformat(),
            'server_path': client.content.properties(uid=uid)['path'],
            'root_uid': '' if int(root_uid) == int(uid) else root_uid,
        }, object_type='folder')
        if skip_db:  # 单纯下载，不操作数据库
            md = client.content.properties(uid=uid)
            local_folder = get_numbered_path(
                os.path.join(local_folder, md.get('name'))
            )
            if not os.path.exists(local_folder):
                os.makedirs(local_folder)
            for item in client.content.items(uid=uid):
                if is_file(item):
                    self.download_file(
                        client.token_code, item, local_folder,
                        usage='download', skip_db=skip_db,
                        silent=silent, on_progress=on_progress
                    )
                    oncomplete()
                elif is_folder(item):
                    _local_folder = os.path.join(local_folder, item['name'])
                    os.makedirs(_local_folder)
                    _conflict_count, _diff_count = self._download_folder(
                        client=client,
                        uid=item.get('uid'),
                        local_folder=_local_folder,
                        skip_db=skip_db,
                        silent=silent, oncomplete=oncomplete,
                        on_progress=on_progress
                    )
                    conflict_count += _conflict_count
                    diff_count += _diff_count
            return conflict_count, diff_count
        for item in self.remote_diff(
            client=client, root_uid=root_uid,
            uid=uid,
            root_local_path=root_local_folder,
            local_path=local_folder
        ):
            # 对冲突项不处理
            if self.check_pull_conflict(
                item=item,
                root_uid=root_uid,
                local_folder=local_folder,
                root_local_folder=root_local_folder
            ):
                conflict_count += 1
                continue

            # 下载新文件
            if item['type'] == 'new_file':
                self.download_file(
                    client.token_code, item['item'],
                    local_folder, root_uid=root_uid,
                    last_pull=datetime.utcnow().isoformat(),
                    usage='sync', silent=silent, on_progress=on_progress
                )
                diff_count += 1
                oncomplete()
            # 下载修改的文件
            elif item['type'] == 'modified_file':
                if not os.path.exists(item['item']['local_path']):
                    self.logger.warn(
                        u'_download_folder 发现文件本地删除冲突: %s',
                        item['item']
                    )
                    self.mark_conflict(
                        CONFLICT_CLIENT_DELETED,
                        local_path=item['item']['local_path'],
                        object_type='file'
                    )
                    conflict_count += 1
                    continue
                # Maybe we could use 'move into trash bin' instead?
                try:
                    os.remove(item['item']['local_path'])
                except WindowsError:
                    self.logger.error(
                        u'_download_folder 发生 Windows 错误',
                        exc_info=True
                    )
                self.delete_item(
                    local_path=item['item']['local_path'],
                    object_type='file'
                )
                self.download_file(
                    client.token_code, item['item'],
                    local_folder, root_uid=root_uid,
                    last_pull=datetime.utcnow().isoformat(),
                    usage='sync', silent=silent, on_progress=on_progress
                )
                diff_count += 1
                oncomplete()
            # 线上删除文件
            elif item['type'] == 'removed_file':
                self.logger.debug(
                    u'_download_folder 删除文件: %s，类型是: %s',
                    item['item']['local_path'], item['type']
                )
                try:
                    os.remove(item['item']['local_path'])
                except:
                    self.logger.warn(
                        u'要删除的文件不存在: %s',
                        item['item']['local_path'],
                        exc_info=True
                    )
                finally:
                    self.delete_item(
                        local_path=item['item']['local_path'],
                        object_type='file'
                    )
                diff_count += 1
            # 线上删除文件夹
            elif item['type'] == 'removed_folder':
                # 删除这个文件夹，或标记为冲突
                self.delete_tree(
                    item['item']['local_path'],
                    root_uid=item['item']['root_uid'],
                    client=client
                )
            # 新建文件夹并递归下载其内容
            elif item['type'] == 'new_folder':
                folder = os.path.join(local_folder, item['item']['name'])
                if not os.path.isdir(folder):
                    os.mkdir(folder)
                else:
                    self.logger.info(
                        u'_download_folder 服务端新增文件夹在本地有对应项: %s将会合并内容',
                        folder
                    )
                self.save_item({
                    'uid': item['item']['uid'],
                    'local_path': folder,
                    'server_path': item['item']['path'],
                    'modified': get_iso_mtime(folder),
                    # 避免将同步点自身设置为自己的根同步点
                    'root_uid': root_uid if str(root_uid) != str(item['item']['uid']) else '',
                    'last_pull': datetime.utcnow().isoformat(),
                    'last_push': '',
                    'conflict': 0
                }, object_type='folder')
                _conflict_count, _diff_count = self._download_folder(
                    root_uid=root_uid,
                    client=client,
                    uid=item['item']['uid'],
                    root_local_folder=root_local_folder,
                    local_folder=folder,
                    silent=silent, oncomplete=oncomplete,
                    on_progress=on_progress
                )
                conflict_count += _conflict_count
                diff_count += _diff_count
            # 对不变（未知）文件夹进行递归下载操作
            elif item['type'] == 'unknown_folder':
                # 本地未删除
                if os.path.basename(item['item']['local_path']) != item['item']['name']:
                    self.logger.warn(
                        u'_download_folder 文件夹可能被改名: %s => %s',
                        item['item']['local_path'],
                        item['item']['name']
                    )
                    _p = ''.join([item['item']['local_path'], '%'])
                    _p_new = os.path.join(
                        os.path.dirname(item['item']['local_path']),
                        item['item']['name']
                    )
                    for fobj in self.query_items(local_path=_p):
                        _fobj = fobj.copy()
                        _fobj['local_path'] = _fobj['local_path'].replace(
                            item['item']['local_path'],
                            _p_new
                        )
                        self.save_item(
                            _fobj,
                            query={
                                'local_path': fobj['local_path']
                            }
                        )
                    try:
                        shutil.move(item['item']['local_path'], _p_new)
                    except:
                        self.logger.warn(
                            u'试图移动文件时出现问题: "%s" => "%s"',
                            item['item']['local_path'], _p_new,
                            exc_info=True
                        )
                    item['item']['local_path'] = _p_new
                _conflict_count, _diff_count = self._download_folder(
                    root_uid=root_uid,
                    client=client,
                    uid=item['item']['uid'],
                    root_local_folder=root_local_folder,
                    local_folder=item['item']['local_path'],
                    silent=silent, oncomplete=oncomplete,
                    on_progress=on_progress
                )
                conflict_count += _conflict_count
                diff_count += _diff_count
        return conflict_count, diff_count

    def _root_by_server_path(self, server_path):
        '''Get root record of given server_path'''
        for split_count in range(server_path.count('/') + 1):
            folder_path = server_path.rsplit('/', split_count)[0]
            root_records = self.query_items(server_path=folder_path, root_uid='', object_type='folder')
            if root_records:
                return root_records[0]

            folder_records = self.query_items(server_path=folder_path, object_type='folder')
            if folder_records:
                return self.query_items(uid=folder_records[0]['root_uid'], root_uid='', object_type='folder')[0]

        return None

    def _remove_path_number(self, path, sep='-'):
        '''
        去除路径编号
        Args:
            path <String> 本地路径
            sep  <String> 编号分隔符
        Returns:
            <String> (去除编号的路径或原路径)
        '''
        try:
            return path[:path.rindex('-')]
        except ValueError:
            return path

    def pull(
        self, remote, local_path=None, always=True,
        oncomplete=None, on_progress=None,
    ):
        '''
        向服务器取最新内容，包括增删改移动的。
        Args:
            remote  <int / str> 要 pull 的服务端 path 或 uid，必须在同步区内
            local_path 可选，指定更新到这个本地路径；默认根据 remote 计算本地路径
            always <bool> 可选，是否强制 pull，即使文件没有改动，默认为 True
            oncomplete <Callable> 可选，下载完成后的回调函数
            on_progress <Callable> 可选，每下载完成一块之后的回调函数，参数 offset, size
        Returns:
            (冲突数量, 实际更新项的数量)
        '''

        # 查询path对应到filestore中的数据记录
        using_uid = isinstance(remote, int) or remote.isdigit()
        oncomplete = oncomplete or (lambda *args, **kwargs: None)

        remote_item = self.wo_client.content.properties(**(
            {'uid': remote} if using_uid else {'path': remote}
        ))
        # Notice: 这里逻辑本身是很简单，只需要判断 root 是否存在，就可以确认对象是否在一个同步区内
        # 但是为了兼容在 edo_temp 目录中使用同步机制，对 root 不存在的情况做了额外判断。
        # edo_temp 下的 .mounted 目录里是映射盘内置同步区的本地目录，应该对其下目录进行判断
        root = self._root_by_server_path(
            remote if not using_uid else remote_item['path']
        )
        self.logger.debug(root)
        in_edo_temp = local_path and local_path.startswith(EDO_TEMP) and not local_path.startswith(os.path.join(EDO_TEMP, '.mounted'))
        if root is None and not in_edo_temp:
            raise ValueError(
                u'Remote item identified by {} {} is not in a syncfolder'.format(
                    ('uid' if using_uid else 'path'), remote
                )
            )

        if is_file(remote_item):
            self.logger.debug('Download file')
            if local_path:
                local_folder = os.path.dirname(local_path)
                filename = os.path.basename(local_path)
            else:
                local_folder = os.path.dirname(
                    remote_item['path'].replace(
                        root['server_path'], root['local_path']
                    ).replace('/', os.path.sep)
                )
                filename = remote_item.get('title', remote_item['name'])

            if not os.path.isdir(local_folder):
                # os.makedirs(local_folder)
                # for split_count in reversed(xrange(local_folder.count('/'))):
                #     folder = local_folder.rsplit(os.path.sep, split_count)[0]
                self.__create_local_folder(
                    client=self.wo_client,
                    local_path=local_folder,
                    server_path=os.path.dirname(remote_item['path']),
                    root_uid=root['uid']
                )

            # 如果 always 为 False，本地文件存在且版本与线上一致，则无需更新文件
            local_path = os.path.join(local_folder, filename)
            if not always and os.path.isfile(local_path) and self.query_items(
                local_path=os.path.join(local_folder, filename),
                revision=remote_item.get("revision", 0),
                usage='sync',
                object_type='file'
            ):
                self.logger.debug("No need to update: %s", local_path)
                return 0, 0

            self.download_file(
                token=self.token,
                metadata=remote_item,
                local_folder=local_folder,
                file_name=filename,
                usage='sync',
                root_uid=('' if in_edo_temp else root['uid']),
                skip_db=False,
                on_progress=on_progress,
            )
            oncomplete()
            return 0, 1
        elif is_folder(remote_item):
            self.logger.debug('Download folder')
            if local_path:
                local_folder = local_path
            else:
                local_folder = remote_item['path'].replace(
                    root['server_path'], root['local_path']
                ).replace('/', os.path.sep)
            self.logger.debug('Local folder: %r', local_folder)
            if not os.path.isdir(local_folder):
                os.makedirs(local_folder)

            return self._download_folder(
                root_uid=root['uid'],
                client=self.wo_client,
                uid=remote_item['uid'],
                root_local_folder=root['local_path'],
                local_folder=local_folder,
                silent=True, oncomplete=oncomplete, on_progress=on_progress
            )
        else:
            raise TypeError(
                u'Item of types %s is not downloadable'.format(remote_item['object_types'])
            )

    def _delete_remote_file(self, item):
        '''
        删除线上文件
        `item` 必须含有 `uid` 和 `local_path`
        '''
        try:
            self.wo_client.content.delete(uid=item['uid'])
            # 删除本地记录
            self.logger.debug(
                u'_delete_remote_file 删除本地文件记录: %s', item['local_path']
            )
            self.delete_item(
                local_path=item['local_path'], object_type='file'
            )
        except Exception as e:
            if e.status == 404:
                # 文件已经不存在，删除本地记录
                self.logger.debug(
                    u'_delete_remote_file 删除本地文件记录: %s', item['local_path']
                )
                self.delete_item(
                    local_path=item['local_path'], object_type='file'
                )
        return 0, 1

    def _delete_remote_folder(self, item):
        '''
        删除线上文件夹
        `item` 必须含有以下 key:
        - uid
        - root_uid
        - local_path
        - server_path
        '''
        conflict_count = diff_count = 0
        local_folders, local_files = self.find_syncitems(
            item['root_uid'], item['local_path']
        )
        try:
            metadata = self.wo_client.content.properties(uid=item['uid'])
            # 服务端没有删除没有移动
            if item['server_path'] == metadata['path']:
                for local_file in local_files:
                    remote_file = self.wo_client.content.properties(
                        uid=local_file['uid']
                    )
                    if str(remote_file['revision']) == str(local_file['revision']):
                        # 线上子文件没有修改，直接删除
                        self.logger.debug(
                            u'_delete_remote_folder 删除服务端文件: %s',
                            local_file['local_path']
                        )
                        self.wo_client.content.delete(uid=local_file['uid'])
                        # 删除本地文件记录
                        self.delete_item(
                            local_path=local_file['local_path'],
                            object_type='file'
                        )
                    else:
                        # 线上子文件被修改，标记为本地删除冲突（3）
                        self.logger.debug(
                            u'_delete_remote_folder 标记冲突: %s',
                            local_file['local_path']
                        )
                        conflict_count += 1
                        # 一旦产生冲突，停止 push ，转为 pull
                        return conflict_count, diff_count
                # 如果线上文件夹为空，删除这个文件夹
                if len(self.wo_client.content.items(uid=item['uid'])) == 0:
                    self.logger.debug(
                        u'_delete_remote_folder 服务端目录: %s 为空目录，直接删除',
                        item['server_path']
                    )
                    self.wo_client.content.delete(uid=item['uid'])
                    diff_count += 1
                # 否则标记为文件夹的本地删除冲突（3）
                else:
                    self.logger.debug(
                        u'_delete_remote_folder 服务端目录: %s 非空，标记冲突',
                        item['server_path']
                    )
                    conflict_count += 1
                    # 一旦产生冲突，停止 push ，转为 pull
                    return conflict_count, diff_count
            # 服务端移动了文件夹，将本地记录删除，服务端不做处理
            else:
                for local_file in local_files:
                    self.logger.debug(
                        u'_delete_remote_folder 删除本地文件记录: %s',
                        local_file['local_path']
                    )
                    self.delete_item(
                        local_path=local_file['local_path'], object_type='file'
                    )
            self.logger.debug(
                u'_delete_remote_folder 删除本地文件夹记录: %s', item['local_path']
            )
            self.delete_item(local_path=item['local_path'], object_type='folder')
        except Exception as e:
            self.logger.warn(u'删除远端文件夹可能有问题: %s', item, exc_info=True)
            if getattr(e, 'status', None) == 404:
                # 服务端已经删除，删除本地记录
                for local_file in local_files:
                    self.logger.debug(
                        u'_delete_remote_folder 删除本地文件记录: %s', local_file['local_path']
                    )
                    self.delete_item(
                        local_path=local_file['local_path'], object_type='file'
                    )
                self.logger.debug(
                    u'_delete_remote_folder 删除本地文件夹记录: %s', item['local_path']
                )
                self.delete_item(
                    local_path=item['local_path'], object_type='folder'
                )
                diff_count += 1
        finally:
            return conflict_count, diff_count

    def _upload_folder(
        self, root_uid=None, client=None, uid=None,
        root_local_folder=None, local_folder=None,
        resumable=False, skip_db=False,
        silent=True, autorename=True, on_progress=None,
        oncomplete=None, setprivate=False
    ):
        '''
        遍历上传指定文件夹
        Args:
            root_uid <String|Number> 本地同步点对应的服务端文件夹 uid
            client <WoClient> WO Client
            uid <String|Number> 当前要上传到目标文件夹的 uid
            root_local_folder <String> 本地的同步区路径
            local_folder <String> 当前要上传的本地文件夹路径
            oncomplete <Function> 上传完成后的回调函数
            setprivate <Bool> 是否为保密上传
        Returns:
            None
        '''
        # FIXME 使用 autorename 参数
        # 对变化的项目进行上传
        diff_count = 0
        conflict_count = 0
        oncomplete = oncomplete or (lambda *args, **kwargs: None)
        not skip_db and self.save_item({
            'uid': uid,
            'local_path': local_folder,
            'last_push': datetime.utcnow().isoformat()
        }, object_type='folder')
        if skip_db:
            items = os.listdir(local_folder)
            batch_upload = len(items) > 1
            for item in items:
                _item = os.path.join(local_folder, item)
                if os.path.isfile(_item):
                    try:
                        self.upload_file(
                            client.token_code,
                            _item,
                            folder_uid=uid,
                            usage='upload',
                            resumable=resumable,
                            skip_db=False,
                            silent=silent,
                            on_progress=on_progress,
                            autorename=autorename,
                            setprivate=setprivate
                        )
                    except Exception:
                        # 忽略上传文件出错，继续后续上传
                        if not batch_upload:
                            raise
                        continue
                    oncomplete()
                elif os.path.isdir(_item):
                    try:
                        item_md = client.content.create_folder(
                            uid=uid, name=item
                        )
                    except ApiError as e:
                        if e.code not in (409, ):
                            raise e
                        else:
                            parent_md = client.content.properties(uid=uid)
                            item_md = client.content.properties(
                                path=('/'.join((parent_md['path'], item)))
                            )
                    _conflict_count, _diff_count = self._upload_folder(
                        client=client,
                        uid=item_md['uid'],
                        local_folder=_item,
                        resumable=resumable,
                        skip_db=False,
                        silent=silent,
                        on_progress=on_progress,
                        autorename=autorename,
                        oncomplete=oncomplete,
                        setprivate=setprivate
                    )
                    conflict_count += _conflict_count
                    diff_count += _diff_count
            return conflict_count, diff_count
        batch_upload = len(os.listdir(local_folder)) > 1
        for item in self.local_diff(
            root_uid=root_uid,
            root_path=root_local_folder,
            path=local_folder
        ):
            self.logger.debug(
                u'_upload_folder 处理: %s',
                item['item']['local_path']
            )
            # 冲突项目需要获取线上版本，作为冲突解决时的对比版本
            if self.check_push_conflict(
                item=item,
                root_uid=root_uid,
                parent_uid=uid,
                root_local_folder=root_local_folder
            ):
                ui_client.message(
                    _('Push conflicts'),
                    _(
                        'Found conflict with {}'
                    ).format(
                        item['item']['local_path']
                    ),
                    type='error'
                )
                conflict_count += 1
                continue

            # 忽略已经标记冲突的条目
            if item['item'].get('conflict', None):
                self.logger.info(
                    u'_upload_folder 略过已标记为冲突的条目: %s',
                    item['item']
                )
                conflict_count += 1
                continue

            # 先上传当前层新增的文件
            if item['type'] == 'new_file':
                self.logger.debug(
                    u'_upload_folder 上传新文件: %s',
                    item['item']['local_path']
                )
                try:
                    self.upload_file(
                        client.token_code,
                        item['item']['local_path'],
                        folder_uid=uid,
                        root_uid=root_uid,
                        last_push=datetime.utcnow().isoformat(),
                        usage='sync',
                        resumable=resumable,
                        silent=silent,
                        on_progress=on_progress,
                        autorename=autorename,
                        setprivate=setprivate
                    )
                except Exception:
                    # 忽略上传文件出错，继续后续上传
                    if not batch_upload:
                        raise
                    continue
                oncomplete()
                diff_count += 1

            # 上传当前修改的文件
            elif item['type'] == 'modified_file':
                self.logger.debug(
                    u'_upload_folder 上传修改的文件: %s',
                    item['item']['local_path']
                )
                try:
                    self.upload_file(
                        client.token_code,
                        item['item']['local_path'],
                        folder_uid=uid,
                        root_uid=root_uid,
                        last_push=datetime.utcnow().isoformat(),
                        file_uid=item['item']['uid'],
                        parent_rev=item['item']['revision'],
                        usage='sync',
                        resumable=resumable,
                        silent=silent,
                        on_progress=on_progress,
                        autorename=autorename,
                        setprivate=setprivate
                    )
                except Exception:
                    # 忽略上传文件出错，继续后续上传
                    if not batch_upload:
                        raise
                    continue
                oncomplete()
                diff_count += 1

            # 从服务端移动本地移动的文件
            elif item['type'] == 'moved_file':
                self.move_file(
                    item['src']['local_path'],
                    item['item']['local_path'],
                    remote_only=not os.path.exists(item['src']['local_path'])
                )
                diff_count += 1

            # 从服务端删除本地删除的文件
            elif item['type'] == 'removed_file':
                _conflict_count, _diff_count = self._delete_remote_file(item['item'])
                conflict_count += _conflict_count
                diff_count += _diff_count

            # 从服务端删除本地删除的文件夹
            elif item['type'] == 'removed_folder':
                _conflict_count, _diff_count = self._delete_remote_folder(item['item'])
                conflict_count += _conflict_count
                diff_count += _diff_count

            # 在服务端创建本地新建的文件夹
            elif item['type'] == 'new_folder':
                metadata = self.__create_site_folder(item['item']['local_path'], client, uid, root_uid)
                diff_count += 1
                _conflict_count, _diff_count = self._upload_folder(
                    root_uid=root_uid,
                    client=client,
                    uid=metadata['uid'],
                    root_local_folder=root_local_folder,
                    local_folder=item['item']['local_path'],
                    silent=silent,
                    on_progress=on_progress,
                    autorename=autorename,
                    oncomplete=oncomplete,
                    setprivate=setprivate
                )
                conflict_count += _conflict_count
                diff_count += _diff_count

            # 处理本地不知道变化情况的文件夹
            elif item['type'] == 'unknown_folder':
                self.logger.debug(
                    u'_upload_folder 处理常规文件夹：%s',
                    item['item']['local_path']
                )
                _conflict_count, _diff_count = self._upload_folder(
                    root_uid=root_uid,
                    client=client,
                    uid=item['item']['uid'],
                    root_local_folder=root_local_folder,
                    local_folder=item['item']['local_path'],
                    silent=silent,
                    on_progress=on_progress,
                    autorename=autorename,
                    oncomplete=oncomplete,
                    setprivate=setprivate
                )
                conflict_count += _conflict_count
                diff_count += _diff_count
        return conflict_count, diff_count

    def __create_local_folder(self, client, local_path, server_path, root_uid=None):

        metadata = client.content.properties(path=server_path)
        self.save_item({
            'uid': metadata['uid'],
            'local_path': local_path
        }, object_type='folder')

        try:
            os.makedirs(local_path)
        except Exception as e:
            # 如果文件已经存在，则过滤掉
            if e.errno ==17:
                pass

        # 更新本地的文件夾图标的状态
        self._refresh_file_manager(os.path.dirname(local_path))

        self.save_item({
            'uid': metadata['uid'],
            'local_path': local_path,
            'server_path': metadata['path'],
            'modified': get_iso_mtime(local_path),
            'root_uid': root_uid if str(root_uid) != str(metadata['uid']) else '',
            'last_pull': datetime.utcnow().isoformat(),
            'conflict': 0
        }, object_type='folder')

        return metadata

    def __create_site_folder(self, local_path, client, uid, root_uid=None):
        folder_name = os.path.basename(local_path)
        self.logger.debug(u'_upload_folder 在服务端创建文件夹: %s', folder_name)
        try:
            metadata = client.content.create_folder(
                uid=uid,
                name=folder_name
            )
        except Exception as e:
            self.logger.warn(u'在服务端创建文件夹时出错', exc_info=True)
            if isinstance(e, (ApiError, )):
                # 文件夹已经存在
                if e.code == 409:
                    # 查询文件夹信息
                    metadata = client.content.properties(
                        path='/'.join([
                            client.content.properties(uid=uid)['path'],
                            folder_name,
                        ])
                    )
                else:
                    raise
            else:
                raise

        # 更新本地的文件夾图标的状态
        self._refresh_file_manager(local_path)

        self.save_item({
            'uid': metadata['uid'],
            'local_path': local_path,
            'server_path': metadata['path'],
            'modified': get_iso_mtime(local_path),
            'root_uid': root_uid if str(root_uid) != str(metadata['uid']) else '',
            'last_push': datetime.utcnow().isoformat(),
            'conflict': 0
        }, object_type='folder')
        return metadata

    def local_diff(self, root_uid=None, root_path=None, path=None):
        '''
        检查指定同步文件夹中指定路径下的本地差异变化
        Args:
            root_uid <String|Number> 同步区的 uid
            root_path <String> 同步区的路径
            path <String> 当前层次的路径
        Returns:
            <Generator> 当中每项为以下类型：
                <Dict> 是新增文件/文件夹时 type='new' ，item 是路径
                <Dict> 修改文件/删除项目时 type='modified' or 'removed'，
                item 是 <Dict> 包含数据库中所有字段
        '''
        self.logger.debug(u'local_diff 开始检查改动: %s', path)

        new_items, removed_items, unknown_items = [], [], []
        folders, files = self.find_syncitems(root_uid, root_path)

        def depth1(i):
            '''
            过滤出当前层次的文件和文件夹
            '''
            sep = os.path.sep
            left_path_part = i['local_path'].replace(path, '')
            return left_path_part.count(os.path.sep) == 1\
                and left_path_part.startswith(sep)

        unknown_items = filter(depth1, folders)

        local_items = filter(depth1, files) + unknown_items
        local_item_paths = [i['local_path'] for i in local_items]
        self.logger.debug(u'<local_diff>\n\tlocal_items: %s\n\tlocal_item_paths: %s', local_items, local_item_paths)
        # 获得新增的文件记录
        for item in os.listdir(path):
            if not should_push(item):
                continue

            item_path = os.path.abspath(os.path.join(path, item))
            if item_path not in local_item_paths:
                if os.path.isdir(item_path) and is_valid_dir(item_path):
                    self.logger.debug(u'local_diff 找到新文件夹: %s', item_path)
                    new_items.append({
                        'type': 'new_folder',
                        'item': {
                            'local_path': item_path,
                            'object_type': 'folder'
                        }
                    })
                elif os.path.isfile(item_path) and is_valid_file(item_path):
                    self.logger.debug(u'local_diff 找到新文件: %s', item_path)
                    new_items.append({
                        'type': 'new_file',
                        'item': {
                            'local_path': item_path,
                            'object_type': 'file',
                            'md5': get_file_md5(item_path)
                        }
                    })

        # 获得改动和删除的文件记录
        for item in local_items:
            if not os.path.exists(item['local_path']):
                if item['object_type'] == 'file':
                    self.logger.debug(
                        u'local_diff 找到删除的文件: %s',
                        item['local_path']
                    )
                    removed_items.append({
                        'type': 'removed_file',
                        'item': item
                    })
                elif item['object_type'] == 'folder':
                    self.logger.debug(
                        u'local_diff 找到删除的文件夹: %s',
                        item['local_path']
                    )
                    removed_items.append({
                        'type': 'removed_folder',
                        'item': item
                    })
                    unknown_items.remove(item)
            else:
                if os.path.isfile(item['local_path']) \
                        and self.file_changed(item['local_path'], item):
                    self.logger.debug(
                        u'local_diff 找到修改的文件: %s',
                        item['local_path']
                    )
                    yield {'type': 'modified_file', 'item': item}

        # 处理新建的项
        for item in new_items:
            if item['type'] == 'new_file':
                # 如果是新建的文件，从数据库记录中比对 md5
                # 一旦发现 md5 相同且本地文件不存在的，则认为是将该文件移动到新建文件的位置
                item_md5 = item['item']['md5']
                for f in files:
                    if f['md5'] == item_md5 and not os.path.exists(f['local_path']):
                        # md5 相同且本地文件不存在
                        self.logger.debug(
                            u'local_diff 发现移动的文件: %s => %s',
                            f['local_path'], item['item']['local_path']
                        )
                        # 如果是移动的文件，需要从 removed_items 中去除对应的项
                        if f in removed_items:
                            removed_items.remove(f)
                        item = {
                            'type': 'moved_file',
                            'item': item['item'],
                            'src': f
                        }
                        break
                yield item
            else:
                # 如果是新建的目录
                yield item
        # 处理删除的项
        for item in removed_items:
            yield item
        for folder in unknown_items:
            self.logger.debug(
                u'local_diff 找到常规文件夹: %s',
                folder['local_path']
            )
            yield {'type': 'unknown_folder', 'item': folder}

    def _get_parents(self, local_path):
        '''
        根据给定的本地路径，获得父文件夹的和根同步点的数据库记录（dict）
        Returns: parent_record, root_record
        '''
        local_path = local_path.replace('/', os.path.sep)
        parent = self.query_items(local_path=os.path.dirname(local_path), object_type='folder')
        if len(parent) == 0:
            # 查询不到父目录的同步记录，说明 local_path 是同步区的根目录
            return None, None
        else:
            parent = parent[0]

        try:
            item = self.query_items(local_path=local_path)[0]
        except IndexError:
            directory_path = local_path
            while 1 and os.path.dirname(directory_path) != directory_path:
                directory_path = os.path.dirname(directory_path)
                items = self.query_items(local_path=directory_path)
                if items:
                    item = items[0]
                    break
        # 新文件就处于同步区最外一层 (/root_syncfolder/new_file)
        if item['object_type'] == 'folder' and item['root_uid'] == '':
            root = item
        else:
            root = list(filter(
                lambda x: x['root_uid'] == '', self.query_items(uid=item['root_uid'])
            ))[0]

        return parent, root

    def check_push_conflict(
        self, item=None, root_uid=None,
        parent_uid=None, root_local_folder=None
    ):
        '''
        判断 diff 结果中一项是否冲突
        Args:
            item <Dict> 要检查的项目（由 local_diff 获取的）
            root_uid <String|Number> 同步区的 uid
            parent_uid <String|Number> 父文件夹的 uid
            root_local_folder <String> 同步区的本地路径
        Returns:
            <Boolean> 冲突则标记并返回 True ， 否则返回 False
        '''
        try:
            self.logger.debug(
                u'check_push_conflict 检查冲突: %s 类型是: %s',
                item['item']['local_path'], item['type']
            )
        except Exception as e:
            self.logger.debug(
                u'check_push_conflict 检查冲突: %s 类型是: %s',
                item['item']['path'], item['type']
            )

        if item['type'] == 'new_file':
            parent_md = self.wo_client.content.properties(uid=parent_uid)
            # TESTING 此处同名文件判断可以直接组合文件路径进行判断，不必遍历文件夹
            try:
                item['item']['name'] = os.path.basename(
                    item['item']['local_path']
                )
                remote_item = self.wo_client.content.properties(
                    path='/'.join([parent_md['path'], item['item']['name']]),
                    fields=['mdfs_hash']
                )
                remote_md5 = remote_item.get('mdfs_hash', None)
                if is_file(remote_item):
                    if not remote_md5:
                        local_path = os.path.join(
                            tempfile.mkdtemp(), remote_item['title']
                        )
                        self.pull(remote_item['uid'], local_path=local_path)
                        remote_md5 = get_file_md5(local_path)
                        try:
                            os.remove(local_path)
                        except Exception:
                            # 删除临时文件出错，暂不处理
                            pass
                    self.save_item({
                        'uid': remote_item['uid'],
                        'revision': '0',  # 将新文件冲突变为修改冲突
                        'local_path': item['item']['local_path'],
                        'server_path': remote_item['path'],
                        'modified': remote_item['modified'].replace('Z', ''),
                        'md5': remote_md5,
                        'root_uid': root_uid,
                        'conflict': 1,
                        'last_pull': '',
                        'last_push': '',
                        'usage': 'sync',
                    }, object_type='file')
                    # 不仅要标记冲突，也要下载冲突备份文件
                    self.mark_conflict(
                        CONFLICT_BOTH_MODIFIED,
                        local_path=item['item']['local_path'],
                        object_type='file'
                    )
                    return True
            except ApiError as e:
                if e.code != 404:
                    self.logger.error(
                        u'FS.check_push_conflict API 错误',
                        exc_info=True
                    )
                    raise
            return False
        elif item['type'] == 'new_folder':
            parent_md = self.wo_client.content.properties(uid=parent_uid)
            # TESTING 此处同名文件判断可以直接组合文件路径进行判断，不必遍历文件夹
            try:
                item['item']['name'] = os.path.basename(
                    item['item']['local_path']
                )
                remote_item = self.wo_client.content.properties(
                    path='/'.join([parent_md['path'], item['item']['name']])
                )
                if is_folder(remote_item):
                    self.save_item({
                        'uid': remote_item['uid'],
                        'local_path': item['item']['local_path'],
                        'server_path': remote_item['path'],
                        'modified': get_iso_mtime(item['item']['local_path']),
                        'root_uid': root_uid,
                        'last_pull': '',
                        'last_push': '',
                        'conflict': 0,
                        'usage': 'sync',
                    }, object_type='folder')
                    self.logger.warn(
                        u'check_push_conflict 自动处理了文件夹新建冲突: %s',
                        item['item']['local_path']
                    )
            except ApiError as e:
                if e.code != 404:
                    self.logger.error(
                        u'FS.check_push_conflict API 错误',
                        exc_info=True
                    )
            return False

        elif item['type'] == 'modified_file':
            try:
                remote_item = self.wo_client.content.properties(
                    uid=item['item']['uid']
                )
                # 如果是文件快捷方式，需要取源文件的版本信息
                if 'FileShortCut' in remote_item['object_types']:
                    remote_item = metadata_by_shortcut(self.wo_client, metadata=remote_item)

                # 本地修改 & 服务端修改
                if str(item['item']['revision']) != str(remote_item['revision']):
                    # 记录冲突项目
                    self.mark_conflict(
                        CONFLICT_BOTH_MODIFIED,
                        local_path=item['item']['local_path'],
                        object_type='file'
                    )
                    return True
                else:
                    return False
            except ApiError as e:
                # 本地修改 & 服务端删除
                if e.status == 404:
                    self.mark_conflict(
                        CONFLICT_SERVER_DELETED,
                        local_path=item['item']['local_path'],
                        object_type='file'
                    )
                    return True
        elif item['type'] == 'removed_file':
            try:
                remote_item = self.wo_client.content.properties(
                    uid=item['item']['uid']
                )
                # 本地删除 & 服务端修改
                # 本地删除 & 服务端移动
                if str(remote_item['revision']) != str(item['item']['revision'])\
                        or remote_item['path'] != item['item']['server_path']:
                    self.mark_conflict(
                        CONFLICT_CLIENT_DELETED,
                        local_path=item['item']['local_path'],
                        object_type='file'
                    )
                    return True
                else:
                    return False
            except ApiError as e:
                if e.status == 404:
                    return False
                else:
                    raise
        elif item['type'] == 'removed_folder':
            # 本地删除的文件夹，要删除内部子文件之后才能计算冲突状态
            return False
        elif item['type'] == 'unknown_folder':
            try:
                self.wo_client.content.properties(uid=item['item']['uid'])
            except ApiError as e:
                if e.status == 404:
                    return True
            return False

    def move_file(self, src_lpath, dest_lpath, remote_only=True):
        src_record = self.query_items(local_path=src_lpath, object_type='file')[0]

        # 找到目标路径的文件夹对应的服务端对象
        dest_local_folder = os.path.dirname(dest_lpath)
        dest_parent_records = self.query_items(local_path=dest_local_folder, object_type='folder')

        # 可能连同文件夹一起移动，这时目标目录可能还不存在记录，逐级向上找到最近的父文件夹记录
        while not dest_parent_records and os.path.dirname(dest_local_folder) != dest_local_folder:
            dest_local_folder = os.path.dirname(dest_local_folder)
            dest_parent_records = self.query_items(local_path=dest_local_folder, object_type='folder')

        dest_parent_record = dest_parent_records[0]
        new_directories = os.path.relpath(dest_lpath, dest_local_folder).rsplit(os.path.sep)[:-1]
        self.logger.debug(u'%s => %s 的移动过程中可能需要新建这些目录: %s', src_lpath, dest_lpath, new_directories)

        parent_uid = dest_parent_record['uid']
        parent_lpath = dest_parent_record['local_path']
        md = dest_parent_record
        for directory in new_directories:
            parent_lpath = os.path.join(parent_lpath, directory)
            md = self.__create_site_folder(
                parent_lpath, self.wo_client, md['uid'], src_record['root_uid']
            )
            parent_uid = md['uid']

        try:
            md = self.wo_client.content.move(
                uid=src_record['uid'], to_uid=parent_uid, name=os.path.basename(dest_lpath)
            )
        except:
            self.logger.warn(
                u'在服务端移动文件 %s (uid:%s) => to_uid:%s name:%s 出错',
                src_record['local_path'], src_record['uid'],
                parent_uid, os.path.basename(dest_lpath)
            )
            raise
        else:
            self.logger.debug(u'在服务端移动文件 %s => %s 完成', src_record['local_path'], md['path'])

        # 保存到数据库
        src_record.update({
            'local_path': dest_lpath,
            'server_path': md['path'],
        })
        object_type = src_record.pop('object_type')
        src_record.pop('id', None)
        self.save_item(
            src_record,
            query={'local_path': src_lpath, 'uid': src_record['uid']},
            object_type=object_type
        )

        if not remote_only and os.path.isfile(src_lpath):
            shutil.move(src_lpath, dest_lpath)

        self._refresh_file_manager(dest_lpath)

    def move_folder(self, src_lfolder, dest_lfolder, remote_only=True):
        '''
        移动 src_lfolder 对应的线上目录到 dest_lfolder 对应的位置
        Notice: 只能处理在同步区内的移动
        Args:
        - remote_only: 仅移动线上目录，不移动本地目录，适合本地移动后的回调
        '''
        src_record = self.query_items(local_path=src_lfolder, object_type='folder')[0]

        # 找到目标路径的文件夹对应的服务端对象
        dest_local_folder = os.path.dirname(dest_lfolder)
        dest_parent_records = self.query_items(local_path=dest_local_folder, object_type='folder')
        dest_basename = os.path.basename(dest_lfolder)

        # 可能连同文件夹一起移动，这时目标目录可能还不存在记录，逐级向上找到最近的父文件夹记录
        while not dest_parent_records and os.path.dirname(dest_local_folder) != dest_local_folder:
            dest_local_folder = os.path.dirname(dest_local_folder)
            dest_parent_records = self.query_items(local_path=dest_local_folder, object_type='folder')

        dest_parent_record = dest_parent_records[0]
        new_directories = os.path.relpath(dest_lfolder, dest_local_folder).rsplit(os.path.sep)[:-1]
        self.logger.debug(u'%s => %s 的移动过程中可能需要新建这些目录: %s', src_lfolder, dest_lfolder, new_directories)

        parent_uid = dest_parent_record['uid']
        parent_lpath = dest_parent_record['local_path']
        md = dest_parent_record
        for directory in new_directories:
            parent_lpath = os.path.join(parent_lpath, directory)
            md = self.__create_site_folder(
                parent_lpath, self.wo_client, md['uid'], src_record['root_uid']
            )
            parent_uid = md['uid']

        try:
            self.wo_client.content.properties(
                path=self.wo_client.content.properties(uid=parent_uid, fields=['path'])['path'] +'/'+dest_basename
            )
        except ApiError as e:
            if e.code == 404:
                try:
                    md = self.wo_client.content.move(
                        uid=src_record['uid'],
                        to_uid=parent_uid,
                        name=dest_basename
                    )
                except:
                    self.logger.warn(
                        u'在服务端移动目录 %s (uid:%s) => to_uid:%s name:"%s" 出错',
                        src_record['local_path'], src_record['uid'], parent_uid, dest_basename
                    )
                    raise
                else:
                    self.logger.debug(
                        u'在服务端移动目录 uid:%s => %s (uid:%s) 完成', src_record['uid'], md['path'], md['uid']
                    )
            else:
                raise
        else:
            self.logger.warn(
                u'%s (uid:%s) => to_uid:%s name:"%s"，目标位置已经存在，不进行移动',
                src_record['local_path'], src_record['uid'], parent_uid, dest_basename
            )
            return

        old_parent_lpath = src_lfolder + os.path.sep
        old_parent_spath = src_record['server_path'] + '/'
        new_parent_lpath = dest_lfolder + os.path.sep
        new_parent_spath = md['path'] + '/'

        # 线上目录移动完成，更新同步信息记录的路径（本地和线上）
        src_record.update({
            'local_path': dest_lfolder,
            'server_path': md['path'],
        })
        object_type = src_record.pop('object_type')
        src_record.pop('id', None)
        self.save_item(
            src_record,
            query={'local_path': src_lfolder, 'uid': src_record['uid']},
            object_type=object_type
        )
        # 目录移动完成后，需要将其子项的本地和线上路径更改为目录移动后的路径
        for record in self.query_items(root_uid=src_record['root_uid']):
            old_local_path = record['local_path']
            if not old_local_path.startswith(old_parent_lpath):
                continue
            old_server_path = record['server_path']
            object_type = record.pop('object_type')
            record.pop('id', None)
            new_local_path = old_local_path.replace(
                old_parent_lpath, new_parent_lpath
            )
            new_server_path = old_server_path.replace(
                old_parent_spath, new_parent_spath
            )
            record.update({
                'local_path': new_local_path,
                'server_path': new_server_path
            })
            self.save_item(
                record,
                query={'local_path': old_local_path, 'uid': record['uid']},
                object_type=object_type
            )

        if not remote_only and os.path.isfile(src_lfolder):
            shutil.move(src_lfolder, dest_lfolder)

        self._refresh_file_manager(dest_lfolder)

    def move(self, src_lpath, dest_lpath, remote_only=False):
        '''
        将 src_lpath 对应的线上文件移动到 dest_lpath 对应的文件位置
        Notice: 只能处理在同步区内的移动
        Args:
        - remote_only: 仅移动线上文件，不操作本地文件，适合本地移动后的回调
        '''
        self.logger.debug(u'filestore.move(local): %s => %s', src_lpath, dest_lpath)
        if os.path.isfile(src_lpath) or os.path.isfile(dest_lpath):
            return self.move_file(src_lpath, dest_lpath, remote_only=remote_only)
        elif os.path.isdir(src_lpath) or os.path.isdir(dest_lpath):
            return self.move_folder(src_lpath, dest_lpath, remote_only=remote_only)
        else:
            src_spath = self._get_site_path(local_path=src_lpath)
            dst_spath = self._get_site_path(local_path=dest_lpath)
            self.logger.debug(u'filestore.move(site): %s => %s', src_spath, dst_spath)
            is_rename = os.path.dirname(src_spath) == os.path.dirname(dst_spath)
            if is_rename:
                name = os.path.basename(dst_spath)
            else:
                name = os.path.basename(src_spath)
            dst_path = os.path.dirname(dst_spath)
            self.wo_client.content.move(path=src_spath, to_path=dst_path, name=name)

    def _get_site_path(self, local_path):
        '''
        根据本地路径去获得相应的线上路径
        '''
        self.logger.debug(u'Get site path of %s', local_path)
        query_results = self.query_items(local_path=local_path)
        mapping_record = query_results[0] if query_results else None

        if not mapping_record:
            path = local_path
            while os.path.dirname(path) != path:
                self.logger.debug(u'Query path: %s', path)
                query_results = self.query_items(local_path=path)
                folder_md = query_results[0] if query_results else None
                if folder_md:
                    spath = folder_md['server_path']
                    lpath = folder_md['local_path']
                    site_path = spath + local_path.replace(lpath, '').replace(os.path.sep, '/')
                    return site_path
                else:
                    path = os.path.dirname(path)
            else:
                raise ValueError('the path is not in syncfolder')
        else:
            return mapping_record['server_path']

    def push(
        self, path, oncomplete=None, on_progress=None, delete_on_cancel=False,
        remote_uid=None, setprivate=False
    ):
        '''
        将本地变更，推送到服务器，包括冲突的文件
        Args:
            path <str> 推送到服务器的本地路径
            oncomplete <callable> 上传完成后的回调函数
            onprogress <callable> 每上传完成一块后的回调函数
            delete_on_cancel <bool> 如果出现重名或重复，用户取消上传时是否同时删除线上文件
            remote_uid <str> 推送到服务器的存放路径
            setprivate <bool> 是否为保密上传
        Returns:
            None
        '''
        # 查询path对应到filestore中的映射记录
        if remote_uid:
            query_results = self.query_items(local_path=path, uid=remote_uid)
        else:
            query_results = self.query_items(local_path=path)
        mapping_record = query_results[0] if query_results else None
        oncomplete = oncomplete or (lambda *args, **kwargs: None)

        # 如果文件对应的映射记录不存在，则向上寻找最近的有同步记录的文件夹
        if not mapping_record:
            origin_path = path
            unsync_path_list = []
            # 一直向上寻找最近的一个存在同步记录的文件夹
            self.logger.debug(u'对应 %s 的记录不存在，向上寻找父目录', path)
            while os.path.dirname(origin_path) != origin_path:
                query_results = self.query_items(local_path=origin_path)
                self.logger.debug(u'查询结果变更为: %s', query_results)
                folder_md = query_results[0] if query_results else None
                if folder_md:
                    # 找到最近的同步记录的文件夹，用非同步文件夹路径列表中的路径不断创建新的同步文件夹
                    root_uid = folder_md['root_uid'] or folder_md['uid']
                    for unsync_path in unsync_path_list[::-1]:
                        if os.path.isdir(unsync_path):
                            folder_md = self.__create_site_folder(
                                local_path=unsync_path, client=self.wo_client,
                                uid=folder_md['uid'], root_uid=root_uid
                            )
                        uid = folder_md['uid']
                    break
                else:
                    # 将没有同步记录的路径加入到非同步文件夹路径列表中
                    unsync_path_list.append(origin_path)
                    origin_path = os.path.dirname(origin_path)
            # 不存在与同步区映射记录，不能进行push
            else:
                raise ValueError('the path is not in syncfolder')
            self.logger.debug(u'最终使用的查询记录: %s', query_results)
        else:
            root_uid = mapping_record['root_uid'] or mapping_record['uid']
            uid = mapping_record['uid']

        self.logger.debug(
            u'push 向上同步: %s 服务端文件是: %s',
            path, mapping_record['server_path'] if mapping_record else "暂无"
        )

        # 如果是文件则上传单个文件
        if os.path.isfile(path):
            item = {
                'type': 'modified_file' if mapping_record else 'new_file',
                'item': mapping_record if mapping_record else {
                    'local_path': path,
                    'object_type': 'file',
                }
            }
            # 兼容 EDO_TEMP 中的文件：这些文件在本地可能没有父目录（例如只是外部编辑，并没有经过同步的文件）
            try:
                _parent, __ = self._get_parents(path)
                parent_uid = _parent['uid']
            except TypeError:
                if mapping_record and path.startswith(EDO_TEMP):
                    parent_uid = None
                else:
                    raise
            if not self.check_push_conflict(
                item=item, root_uid=root_uid,
                parent_uid=parent_uid
            ):
                if not mapping_record or self.file_changed(path, mapping_record):
                    self.upload_file(
                        token=self.token,
                        local_path=path,
                        usage='sync',
                        folder_uid=(None if mapping_record else uid),
                        file_uid=(uid if mapping_record else None),
                        root_uid=root_uid,
                        resumable=True,
                        skip_db=False,
                        autorename=(False if mapping_record else True),
                        parent_rev=(mapping_record['revision'] if mapping_record else None),
                        on_progress=on_progress,
                        delete_on_cancel=delete_on_cancel,
                        setprivate=setprivate
                    )
                    conflict_count, diff_count = 0, 1
                    oncomplete()
                else:
                    if mapping_record:
                        ui_client.message(
                            _("File push"),
                            _("File has not changed, does not trigger upload"),
                        )
                    conflict_count, diff_count = 0, 0
            else:
                ui_client.message(
                    _('Push conflicts'),
                    _('Found conflict with {}').format(path),
                    type='error'
                )
                conflict_count, diff_count = 1, 0
        elif os.path.isdir(path):
            conflict_count, diff_count = self._upload_folder(
                root_uid=root_uid,
                client=self.wo_client,
                uid=uid,
                root_local_folder=path,
                local_folder=path,
                resumable=True,
                oncomplete=oncomplete,
                on_progress=on_progress,
                setprivate=setprivate
            )
        elif not os.path.exists(path):
            # 用户可能从文件的【同步信息】窗口删除了此文件，此时本地和线上文件都会被删除
            # 这种情况下不要进行任何操作（缺少必要信息：文件在服务端对应的 uid）
            if not mapping_record:
                self.logger.debug(u'文件 %s 记录不存在，不进行任何操作', path)
                return 0, 0

            # 单独同步一个删除的文件，将线上的文件和映射记录都删除
            conflict_count = diff_count = 0
            for record in query_results:
                item = {
                    'item': record,
                    'type': 'removed_{}'.format(record['object_type']),
                }
                parent, root = self._get_parents(record['local_path'])
                if parent is None:
                    # 父目录的同步记录为空，说明被删除的是同步区的根目录
                    # 这时候要解除该同步区的同步关系
                    ui_client.message(
                        _('Push'),
                        _(
                            'The local synchronization directory has '
                            'been deleted, synchronization relationship '
                            'will be released.'
                        )
                    )
                    sync_id = hashlib.md5(
                        u':'.join([
                            self.hostname, self.account, self.instance,
                            record['local_path'], record['uid'],
                        ]).encode('utf-8')
                    ).hexdigest()
                    requests.post(
                        url="{}/sync/delete".format(config.INTERNAL_URL),
                        data={"id": sync_id}
                    )
                    continue

                if not self.check_push_conflict(
                    item=item, root_uid=record['root_uid'],
                    parent_uid=parent['uid'], root_local_folder=root['local_path']
                ):
                    if record['object_type'] == 'file':
                        _ccount, _dcount = self._delete_remote_file(item['item'])
                    else:
                        _ccount, _dcount = self._delete_remote_folder(item['item'])

                    conflict_count += _ccount
                    diff_count += _dcount
                else:
                    conflict_count += 1

        # 将重名和重复的文件更新到进度窗口上
        self._update_duplicated_progress()
        return conflict_count, diff_count

    def _update_duplicated_progress(self):
        '''将重名和重复的文件信息更新（一条）到进度窗口上。
        '''
        from worker import get_worker_db
        worker_db = get_worker_db(self.worker_id)
        if worker_db.get('_duplicated_files', {}):
            duplicated_file = worker_db['_duplicated_files'].values()[0]

            # 一些额外数据
            status = duplicated_file.get('status', _('Duplicated'))
            if status == _('Duplicated'):  # 文件重名
                extra_data = dict(
                    duplicated_types=duplicated_file['duplicated_types'],
                )
            elif status == _('Content Duplicated'):
                extra_data = dict(
                    duplicated_uid=duplicated_file['duplicated_uid'],
                    upload_new_revision=duplicated_file.get('file_uid') is not None,
                    delete_on_cancel=duplicated_file.get('delete_on_cancel')
                )
            else:
                extra_data = None

            ui_client.update_progress(
                self.worker_id,
                direction='up',
                fpath=duplicated_file['path'],
                filename=duplicated_file['filename'],
                size=duplicated_file['size'],
                progress=0,
                status=status,
                parent_folder=duplicated_file['parent_folder'],
                extra=extra_data,
            )

    def file_state(self, file_path):
        '''
        file_path: 本地路径
        '''
        if os.path.isdir(file_path):
            file_type = 'folder'
        else:
            file_type = 'file'

        if file_type == 'folder':
            folders = self.query_items(local_path=file_path, object_type=file_type)
            if len(folders) > 0 and folders[0]['conflict'] in (0, ''):
                return 'child_folder'
            elif len(folders) > 0:
                return 'conflict_folder'
            else:
                return 'new_folder'
        else:
            files = self.query_items(local_path=file_path, object_type=file_type)
            if len(files) > 0 and files[0]['conflict'] in (0, ''):
                if self.file_changed(file_path, files[0]):
                    return 'modified_file'
                else:
                    return 'normal_file'
            elif len(files) > 0 and files[0]['conflict'] not in (0, ''):
                return 'conflict_file'
            elif len(files) == 0:
                return 'new_file'
        return 'unknown_state'

    def __repr__(self):
        return '<FileStore(OC:{}, site: {}.{}, pid: {})>'.format(
            self.server, self.account, self.instance, self.pid
        )


FILE_STORES = {}


def get_file_store(
    server_url, account, instance, token=None, wo_server=None,
    logger=None, upload_server=None, worker_id=None, pid=None,
    use_cache=True,
):
    key = '/'.join([server_url, account, instance])
    if not use_cache:
        return FileStore(
            server_url, account,
            instance, token=token, wo_server=wo_server,
            upload_server=upload_server, logger=logger,
            worker_id=worker_id, pid=pid
        )
    elif key not in FILE_STORES:
        FILE_STORES[key] = FileStore(
            server_url, account,
            instance, token=token, wo_server=wo_server,
            upload_server=upload_server, logger=logger,
            worker_id=worker_id, pid=pid
        )
    else:
        print 'using cached FS: %s' % FILE_STORES[key]
        if isinstance(worker_id, int) or str(worker_id).isdigit():
            FILE_STORES[key].worker_id = worker_id
    return FILE_STORES[key]


def list_file_stores():
    return FILE_STORES
