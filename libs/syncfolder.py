# coding=utf-8
import hashlib
import json
import os
import platform

from edo_client.client import get_client
if platform.system() == 'Windows':
    from win32com.shell import shell, shellcon

import worker
import workers.sync
import workers.setup_syncfolder
from qtui.ui_utils import refresh_webview, show_console_window

from utils import translate as _
from config import SYNC_JSON_FILE
from filestore import get_file_store
from libs.managers import get_site_manager

site_manager = get_site_manager()


def gen_id(oc_server_host, account, instance, path, uid):
    '''
    根据相关参数值生成同步区 ID
    '''
    return hashlib.md5(
        u':'.join([
            oc_server_host, account, instance, path, uid
        ]).encode('utf-8')
    ).hexdigest()


class SyncManager(object):
    """
    同步区管理类：管理同步区的类
    """

    def __init__(self):
        """
        初始化，读取以存在的同步区
        """
        self.sync_folders = {}
        sync_list = []
        if os.path.exists(SYNC_JSON_FILE):
            with open(SYNC_JSON_FILE, 'r') as f:
                try:
                    sync_list = json.load(f)
                except (ValueError, TypeError):
                    sync_list = []

        # 读取从filestore得到的旧数据
        from utils import list_all_sync_folders
        old_syncfolders = list_all_sync_folders()
        for item in old_syncfolders['sync_folders']:
            site = site_manager.get_site(
                item["hostname"], item["account"], item["instance"]
            )
            if not site:
                # 没有站点连接的情况，从 FileStore 中得到的同步区数据不全
                # 无法启动同步任务，因此不添加到同步区中
                continue
            sync_folder = SyncFolder(
                oc_server_host=item['hostname'],
                instance=item['instance'],
                account=item['account'],
                uid=item['uid'],
                path=item['local_path'],
                oc_server=site.oc_server
            )
            if sync_folder.id not in self.sync_folders:
                self.add(sync_folder)

        for value in sync_list:
            sync_folder = SyncFolder(**value)
            # 初始化 sync_folders 并写入文件
            # modify 方法只有在 sync_folders 初始化之后才能使用
            self.sync_folders[sync_folder.id] = sync_folder
            # FIXME 这里为什么要保存？
            self.__save()

    @refresh_webview('syncfolders')
    def add(self, sync_folder):
        """
        向同步区管理类中增加一个同步区
        @param sync_folder: 同步区实例
        @return:None
        """
        if sync_folder.id in self.sync_folders:
            raise ValueError('Syncfolder already exists')
        else:
            self.sync_folders[sync_folder.id] = sync_folder
            self.__save()
            # TODO call_create_function_from_qt

    @refresh_webview('syncfolders')
    def modify(self, sync_folder):
        """
        修改已经存在的一个同步区
        @param sync_folder: 需要修改的同步区实例
        @return: None
        """
        if sync_folder.id not in self.sync_folders:
            raise ValueError('Syncfolder does not exist')
        else:
            self.sync_folders[sync_folder.id] = sync_folder
            self.__save()
            # TODO call_modify_functions_from_qt

    @refresh_webview('syncfolders')
    def delete(self, *args):
        """
        删除一个已经存在的同步区
        @return: None
        Notice: args is either `id` or [oc_server_host, instance, account, path]
          oc_server_host: oc的服务器地址
          instance: 实例
          account: 账号
          path: 本地路径(为一个list)
        """
        # delete by id
        if len(args) == 1:
            id = args[0]
        # delete by details
        elif len(args) == 4:
            raise ValueError("Error Call")
            # id = self.get_id(*args)
        else:
            raise ValueError('Wrong number of arguments')
        if id in self.sync_folders:
            self.remove_from_filestore(self.sync_folders[id])
            self.sync_folders.pop(id)
        self.__save()

    def list(self, oc_server_host=None, instance=None, account=None, path=None):
        """
        查询已经存在的同步区列表
        @param oc_server_host:oc服务器地址(可选)
        @param instance:实例(可选)
        @param account:账户(可选)
        @param path:同步区的本地路径(可选)
        @return:多个同步区列表
        """
        sync_folders = self.sync_folders.values()
        # 按给定属性的值过滤
        for attr_name in ('oc_server_host', 'account', 'instance', 'path'):
            attr_value = locals().get(attr_name, None)
            # 没有指定值就不按这个条件过滤
            if attr_value is None:
                continue
            if attr_name == 'path':
                sync_folders = filter(
                    lambda s: attr_value.startswith(s.path), sync_folders
                )
                continue
            sync_folders = filter(
                lambda s: getattr(s, attr_name, None) == attr_value,
                sync_folders
            )

        return sync_folders

    def get(
        self, sync_id=None, oc_server_host=None, account=None, instance=None,
        path=None, uid=None
    ):
        """
        获取特定的同步区
        @param sync_id: 同步区的 id
        @param oc_server_host: oc_服务器地址
        @param account: 账户
        @param instance: 实例
        @param path: 本地路径(列表)
        @param uid: 站点同步目录的 uid
        @return: 特定同步区 or None
        """
        if not (sync_id or all([oc_server_host, account, instance, path, uid])):
            raise ValueError("no enough parameters to get syncfolder")
        sync_id = sync_id or gen_id(oc_server_host, account, instance, path, uid)
        return self.sync_folders.get(sync_id, None)

    def __save(self):
        # TODO 只能在主进程写入，防止多进程写入造成数据丢失或损坏
        with open(SYNC_JSON_FILE, 'w') as f:
            f.write(json.dumps([value.__dict__ for value in self.sync_folders.values()]))

    @staticmethod
    def remove_from_filestore(sync_folder):
        file_store = None
        site = site_manager.get_site(
            oc_url=sync_folder.oc_server_host,
            instance=sync_folder.instance,
            account=sync_folder.account
        )

        if not site:
            # 没有连接的情况下，扫描 edo_assistent/filestore 目录下的文件来获取
            # FileStore 对象，通过匹配文件名的开头和结尾来得到相应的数据库文件
            from utils import get_filestore_by_filename
            from config import FILE_STORE_DIR
            import os
            from fnmatch import fnmatch
            for db_file in os.listdir(FILE_STORE_DIR):
                pattern = 'http*.{0.oc_server_host}.*.{0.account}.{0.instance}.db'.format(sync_folder)
                if fnmatch(db_file, pattern):
                    file_store = get_filestore_by_filename(db_file)
                    break
            else:
                # 数据库文件已经不存在，不做任何处理
                return
        else:
            file_store = get_file_store(
                server_url=site.oc_server,
                account=site.account,
                instance=site.instance,
                token=site.token
            )

        # 获取同步区相关的文件夹
        sync_subfolders = file_store.query_items(root_uid=sync_folder.uid, object_type="folder")

        # 刷新同步区相关的文件夹
        # Only for win32
        if platform.system() == 'Windows':
            for item in sync_subfolders:
                shell.SHChangeNotify(
                    shellcon.SHCNE_UPDATEDIR,
                    shellcon.SHCNF_PATH,
                    item['local_path'], None
                )

        # 删除相关的同步任务和同步区的文件关联
        if file_store:
            for id in worker.filter_workers(path=sync_folder.path, name='sync'):
                worker.terminate_worker(id)
            file_store.remove_syncfolder(sync_folder.path)

    @staticmethod
    def update_site_filename(sync_id, uid):
        # TODO update
        def request_site_name(sync_id, uid):
            try:
                global sync_manager
                sync_folder = sync_manager.get(sync_id=sync_id)
                site = site_manager.get_site(
                    oc_url=sync_folder.oc_server_host,
                    instance=sync_folder.instance,
                    account=sync_folder.account
                )

                if not site:
                    raise ValueError("the conn is not exist")

                wo_client = site.get_client('workonline')
                site_filename = wo_client.content.properties(uid=uid)['title']
                sync_folder.site_filename = site_filename
                sync_manager.modify(sync_folder)
            except:
                pass

        from threading import Thread
        p = Thread(target=request_site_name, args=(sync_id, uid))
        p.run()


class SyncFolder(object):
    """
    同步区实例类，实例化一个同步区
    """

    def __init__(
        self, oc_server_host, instance, account, uid, path,
        sync_type='sync', policy='manual', site_filename=None, id=None,
        default=None, **kwargs
    ):
        """
        初始化同步区
        @:param oc_server_host: oc服务器的host
        @:param instance: 实例
        @:param account: 账户
        @:param uid: 服务器文件uid
        @:param path: 同步区本地路径
        @:param sync_type: 同步区的类型
        @:param policy: 同步区的策略
        @:param site_filename: 网站同步文件夹名
        @:param id: 同步区 ID
        @:param default: 默认同步区的 ID
        """
        self.oc_server_host = oc_server_host
        self.instance = instance
        self.account = account
        self.uid = uid
        if isinstance(path, basestring):
            self.path = path
        elif isinstance(path, list) and len(path) > 0:
            self.path = path[0]
        else:
            raise ValueError("path value is Error")
        self.sync_type = sync_type
        self.policy = policy
        if id is None:
            self.id = gen_id(
                self.oc_server_host, self.account, self.instance, self.path,
                self.uid
            )
        else:
            self.id = id
        if site_filename:
            self.site_filename = site_filename
        self.default = default

        self.instance_name = kwargs.get('instance_name', None)
        self.instance_url = kwargs.get('instance_url', None)
        self.oc_server = kwargs.get('oc_server', None)
        self.pid = kwargs.get('pid', None)
        self.username = kwargs.get('username', None)

    def __repr__(self):
        return '<SyncFolder {remote}{direction}{local} @{policy} ={state}>'.format(
            remote=self.uid,
            local=self.path,
            direction={
                'up': '<--',
                'down': '-->',
                'sync': '<->',
            }.get(self.sync_type, '-?-'),
            policy=self.policy,
            state=self.state(),
        )

    @refresh_webview('syncfolders')
    def run(self):
        """
        发起一次同步任务
        @return: 同步任务结果的状态
        """
        worker_id, worker_state = self._sync_worker_state()
        if worker_id != 0:
            if worker_state in ('running', 'prepare'):
                raise RuntimeError("sync folder is running")
            elif worker_state in ('paused', 'error'):
                pass
            else:
                worker_kw = self.worker_params()
                # 在运行一个新的任务时， 如果同步任务为自动，则直接执行id为worker_id的任务
                # 如果同步任务为手动， 则创建一个新的任务
                if worker_kw.get('policy') != 'auto':
                    worker_id = worker.new_worker('sync', **worker_kw)
        else:
            worker_kw = self.worker_params()
            worker_id = worker.new_worker('sync', **worker_kw)
        try:
            if worker_id != 0:
                result = worker.start_worker(worker_id)
                # sync_manager.update_site_filename(sync_id=self.id, uid=self.uid)
            else:
                raise RuntimeError('create a new worker fail')
        except Exception as e:
            raise RuntimeError('Error: {}'.format(e))
        if result:
            return u'start sync success'

    def worker_params(self):
        site = site_manager.get_site(
            oc_url=self.oc_server_host,
            account=self.account,
            instance=self.instance
        )
        if not site:
            raise ValueError("the site connect is not exist")

        file_store = get_file_store(
            server_url=site.oc_server,
            account=site.account,
            instance=site.instance,
            token=site.token
        )
        syncfolder_records = file_store.query_items(local_path=self.path)
        syncfolder_record = syncfolder_records[0] if syncfolder_records else None

        if not syncfolder_record:
            # 同步区根节点的记录丢失，但是可以通过 uid 恢复记录
            wo_client = site.get_client("workonline")
            server_path = wo_client.content.properties(uid=self.uid)["path"]
            file_store.save_item({
                'uid': self.uid,
                'local_path': self.path,
                'server_path': server_path,
                'root_uid': ""
            }, object_type="folder")
        else:
            server_path = syncfolder_record['server_path']

        worker_kw = {'oc_server': site.oc_server, 'token': site.token, 'server_path': server_path}
        worker_kw.update(**self.properties())
        if worker_kw['policy'] == 'auto':
            worker_kw['auto'] = 'True'
        if worker_kw['sync_type'] == 'up':
            worker_kw['sync_type'] = 'push'
        elif worker_kw['sync_type'] == 'down':
            worker_kw['sync_type'] = 'pull'
        return worker_kw

    def setup_syncfolder(self, token=None):
        '''
            创建同步区任务
        '''
        # 构造创建同步区任务需要的参数
        site = site_manager.add_site(
            oc_url=self.oc_server,
            account=self.account,
            instance=self.instance,
            pid=self.pid,
            token=token,
            instance_name=self.instance_name,
            instance_url=self.instance_url,
            username=self.username
        )
        if site.get_message_thread().state != "online":
            site.get_message_thread().connect()

        worker_kw = {'oc_server': site.oc_server, 'token': site.token}
        worker_kw.update(**self.properties())

        # 创建同步区
        worker_id = worker.new_worker('setup_syncfolder', **worker_kw)
        if worker_id != 0:
            result = worker.start_worker(worker_id, sync=True)
            return u'start sync success'
        else:
            raise ValueError('syncfolder setup fail')

    @refresh_webview('syncfolders')
    def pause(self):
        """
        暂停当前同步区的同步任务
        @return: 暂停同步任务的结果
        """
        worker_id, worker_state = self._sync_worker_state()
        try:
            result = worker.pause_worker(worker_id)
        except Exception as e:
            raise RuntimeError('Error: {}'.format(e))
        if result:
            return _('pause sync success')

    def _sync_worker_state(self):
        wid, state = 0, None
        worker_list = list(worker.list_worker_ids())
        worker_list.reverse()
        if worker_list is not None:
            for wid in worker_list:
                db = worker.get_worker_db(wid)
                if all([
                    db.get('name', None) == 'sync',
                    db.get('account', None) == self.account,
                    db.get('instance', None) == self.instance,
                    db.get('oc_server_host', None) == self.oc_server_host,
                ]):
                    wdb_path = db.get('path', None)
                    if isinstance(wdb_path, list):
                        wdb_path = wdb_path[0] if wdb_path else None
                    if unicode(wdb_path) != unicode(self.path):
                        continue
                    return int(wid), db.get('state', None)
        return 0, None

    def state(self):
        """
        返回当前同步区的状态
        @return: 同步区状态
        """
        sync_worker_instance = self.sync_worker()
        if sync_worker_instance is not None:
            return sync_worker_instance.get('state')
        else:
            return 'free'

    def sync_worker(self):
        """
        获取当前同步区对应的同步任务的实例
        :return: 同步任务实例
        """
        worker_instance = None
        for wid in worker.list_worker_ids():
            db = worker.get_worker_db(wid)
            if db.get('name', None) == 'sync':
                if all([
                    db.get('account', None) == self.account,
                    db.get('instance', None) == self.instance,
                    db.get('oc_server_host', None) == self.oc_server_host,
                    db.get('path', []) == self.path,
                ]):
                    if db.get('state') in ('running', 'paused', 'prepared', 'error'):
                        return db
                    if db.get('state') == 'finished':
                        worker_instance = db
        return worker_instance

    def properties(self):
        property_dict = vars(self).copy()
        if 'state' in property_dict:
            del property_dict['state']
        return property_dict

    @staticmethod
    @refresh_webview('syncfolders')
    def report_state():
        return True

    # TODO rename to `to_json`
    def load_property(self):
        """
        获取当前同步区属性
        @return: 同步区属性
        """
        return json.dumps(self.__dict__)


sync_manager = SyncManager()
