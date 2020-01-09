#!/usr/bin/env python
# _*_ coding:utf-8 _*_

import copy
import functools
import hashlib
import io
import json
import logging
import multiprocessing
import os

import edo_client
from libs import messenger
from config import APP_DATA, HEADLESS
from ui_client import _request_api

if HEADLESS:
    def emit_webview_refresh_signal(*args, **kwargs):
        pass
else:
    from qtui.ui_utils import emit_webview_refresh_signal

"""
桌面助手主要是通过浏览器发起任务，发送给桌面助手去执行。

但也有如下几种情况，是需要在桌面主动发起任务的：
+ 桌面助手消息通知
+ 映射盘：在桌面进行文件管理
+ 同步盘：可以在桌面文件管理器中发起同步操作

这时候要求桌面助手预先建立和站点的连接，包括站点的信息，当前用户等：
+ 每个站点只能建立一个链接
+ 在首次发起任务的时候建立连接
+ 如果需要切换用户，需要登出现有连接，再重新建立连接

SiteManager 用于管理桌面助手与站点建立的所有连接
Site 表示一个站点连接
"""
logger = logging.getLogger(__name__)


def refresh(func):
    """刷新连接页面"""
    @functools.wraps(func)
    def decorator(*args, **kwargs):
        result = func(*args, **kwargs)
        if multiprocessing.current_process().name != "MainProcess":
            # 不是主进程，发送 HTTP 请求
            try:
                _request_api("/admin/connections", kw={"action": "reload"})
            except Exception:
                logger.exception(u"发送刷新连接页面请求失败")
        else:
            # 是主进程，发送 Qt 信号
            emit_webview_refresh_signal("connections")
        return result
    return decorator


class Site(object):
    """站点连接类，用于表示站点连接"""
    def __init__(
        self, oc_server, account, instance, token,
        instance_name=None, instance_url=None, username=None, pid=None,
        configs=None
    ):
        """
        初始化站点连接
        Args:
            oc_server <str> OC-API 的地址
            account <str> 站点 account
            instance <str> 站点 instance
            token <str> 站点访问的 token
            instance_url <str> 站点访问地址
            instance_name <str> 站点名字
            username <str> 访问站点的用户名
            pid <str> 访问站点的用户 ID
            configs <dict> 站点连接的设置项，现有以下设置项：
            |- allow_script <bool> 是否允许直接运行站点下发的脚本
            |- notification <bool> 是否启用消息通知
        """
        # 初始化站点连接的属性
        self.oc_server = oc_server
        self.account = account
        self.instance = instance
        self.__token = token

        self.instance_name = instance_name
        self.instance_url = instance_url
        self.pid = pid
        self.username = username

        self.__id = self.gen_id()
        self.__messaging = None
        self.__token_invalid = False
        self.__configs = configs or {}
        self.__configs.setdefault("allow_script", False)
        self.__configs.setdefault("notification", True)

        if not self.pid or not self.username:
            self.login(self.__token)

        if not self.instance_name or not self.instance_url:
            oc_client = self.get_client('oc')
            instance_info = oc_client.account.get_instance(
                account=self.account,
                application="workonline",
                instance=self.instance
            )
            self.instance_name = self.instance_name or instance_info['title']
            self.instance_url = self.instance_url or instance_info['app_url']

    @property
    def id(self):
        return self.__id

    @property
    def token(self):
        return self.__token

    @property
    def configs(self):
        """返回当前站点连接设置的副本"""
        return copy.deepcopy(self.__configs)

    def gen_id(self):
        """获取每个站点连接的唯一 ID"""
        return hashlib.md5(
            '|'.join([self.oc_server, self.account, self.instance])
        ).hexdigest()

    def set_token_invalid(self):
        """设置 token 无效"""
        self.__token_invalid = True

    def is_token_invalid(self):
        """检查 token 是否无效"""
        return self.__token_invalid

    def login(self, token):
        """
        站点连接登录，即修改站点连接的 token
        1. 检查 token 是否有效
        2. 从有效的 token 里得到 pid
        3. 更新 token 和 pid
        Args:
            token <str> 站点访问的 token
        """
        oc_client = edo_client.get_client(
            application='oc',
            oc_api=self.oc_server,
            account=self.account,
            instance=self.instance,
            token=token
        )
        try:
            token_info = oc_client.oauth.get_token_info()
        except edo_client.ApiError:
            logger.exception(u"Call get_token_info failed")
            self.__token_invalid = True
        else:
            self.__token_invalid = False
            user = token_info["user"]
            self.pid = "users.{}".format(user)
            org_client = oc_client.get_client('org')
            objects_info = org_client.org.get_objects_info(
                account=self.account, objects=["person:{}".format(user)]
            )
            if objects_info:
                self.username = objects_info[0].get('title', self.username)
        self.__token = token

    def logout(self):
        """
        站点连接登出，即移除站点连接的 token。同时停止已启动的消息线程。
        """
        msg_thread = self.get_message_thread()
        if msg_thread:
            msg_thread.disconnect()
        self.__token = None

    def has_token(self):
        """
        检查站点连接是否有 token
        Return:
            <bool> 站点连接是否有 token
        """
        return self.__token is not None

    def set_config(self, key, value):
        """
        修改站点连接的某项设置
        Args:
            key <str> 设置名
            value <object> 新的设置信息
        """
        if key == "notification" and self.__messaging is not None:
            # 切换消息通知
            self.__messaging.toggle_notify(value)
        self.__configs[key] = value

    def get_config(self, key):
        """
        获取站点连接的某项设置信息
        Args:
            key <str> 设置名
        Return:
            <object>|None 设置信息
        """
        return self.__configs.get(key, None)

    def get_client(self, app_name):
        """
        获取访问某个服务的 Open API 的客户端
        Args:
            app_name <str> 服务名
        Return:
            <edo_client> 客户端对象
        """
        oc_client = edo_client.get_client(
            application='oc',
            oc_api=self.oc_server,
            account=self.account,
            instance=self.instance,
            token=self.__token
        )
        if app_name in ("oc", ):
            return oc_client
        return oc_client.get_client(app_name)

    def get_message_thread(self):
        """
        获取站点连接的消息管理线程
        注意：除了主进程之外，其他进程没法获得消息管理线程
        Return:
            <messenger.Messaging> | None
        """
        if multiprocessing.current_process().name != 'MainProcess':
            return
        if self.__messaging is None or not self.__messaging.is_alive():
            self.__messaging = messenger.Messaging(
                oc_server=self.oc_server,
                account=self.account,
                instance=self.instance,
                token=self.__token,
                pid=self.pid,
                username=self.username,
                instance_name=self.instance_name,
                instance_url=self.instance_url,
                connection_id=self.__id,
                notification=self.get_config('notification')
            )
            self.__messaging.daemon = True
        return self.__messaging


class SiteManager(object):
    """桌面助手的站点连接管理"""
    def __init__(self, storage=None):
        """
        初始化站点连接管理器
        Args:
            storage <str|optional> 保存已有站点连接的文件路径
        """
        self.__storage = storage
        self.__sites = self.load()
        self.save()

    def load(self):
        """
        从存储文件中加载已有的站点连接
        """
        sites = []
        if self.__storage is not None and os.path.exists(self.__storage):
            with io.open(self.__storage, 'r', encoding='utf-8') as f:
                raw_json = json.load(f)
            for _, site in raw_json.items():
                # 兼容旧的站点连接数据
                site.pop("_SiteConnection__id", None)
                site.pop("expired", None)
                if "configs" not in site:
                    allow_script = site.pop("allow_script", False)
                    notification = site.pop("notification", True)
                    site["configs"] = {
                        "allow_script": bool(allow_script),
                        "notification": bool(notification)
                    }
                sites.append(Site(**site))
        return sites

    def reload_sites(self):
        """
        更新已有的站点连接，并刷新连接页面（注：只在主进程中起作用）
        """
        if multiprocessing.current_process().name != "MainProcess":
            return
        # 1.1 加载存储文件中的站点连接
        new_sites = self.load()
        new_sites_map = {s.id: s for s in new_sites}
        new_site_ids = set(new_sites_map.keys())
        sites_map = {s.id: s for s in self.__sites}
        site_ids = set(sites_map.keys())
        # 1.2 利用集合性质，计算出该删除、该添加和该更新的站点连接
        should_remove_ids = site_ids - new_site_ids  # {1, 2} - {2, 3} = {1}
        should_append_ids = new_site_ids - site_ids  # {2, 3} - {1, 2} = {3}
        should_update_ids = site_ids & new_site_ids  # {1, 2} & {2, 3} = {2}
        # 2.1 删除该删除的站点连接
        for site_id in should_remove_ids:
            self.remove_site(sites_map[site_id])
        # 2.2 添加新的站点连接，并启动消息线程
        for site_id in should_append_ids:
            site = new_sites_map[site_id]
            self.__sites.append(site)
            site.get_message_thread().connect()
        # 2.3 更新已有的站点连接
        for site_id in should_update_ids:
            old_site = sites_map[site_id]
            new_site = new_sites_map[site_id]
            if old_site.token != new_site.token:
                old_site.login(new_site.token)
            for key, value in new_site.configs.items():
                old_site.set_config(key, value)
        # 3 刷新连接页面
        emit_webview_refresh_signal("connections")

    @refresh
    def save(self):
        """保存现在的所有站点连接到存储文件"""
        if self.__storage is None:
            return

        private_attrs = (
            'messaging', 'token', 'id', 'configs', 'token_invalid'
        )
        sites = {}
        for site in self.__sites:
            site_properties = site.__dict__.copy()
            # 从 Site 对象中去除私有属性
            for key in private_attrs:
                site_properties.pop('_Site__{}'.format(key), None)
            site_properties['token'] = site.token
            site_properties['configs'] = site.configs
            sites[site.id] = site_properties

        with open(self.__storage, 'w') as f:
            json.dump(sites, f)

    def add_site(
        self, oc_url, account, instance, instance_url, instance_name,
        username, pid, token
    ):
        """
        新增一个站点连接，并更新存储文件
        1. 遍历已有站点连接
        2. 如果要新增的连接是已有的连接，则更新已有的站点连接
        3. 如果不是已有的连接，则新增一个站点连接
        Args:
            oc_url <str> OC-API 的地址
            account <str> 站点 account
            instance <str> 站点 instance
            instance_url <str> 站点访问地址
            instance_name <str> 站点名字
            username <str> 访问站点的用户名
            pid <str> 访问站点的用户 ID
            token <str> 有效的站点访问 token
        Return:
            <Site>
        """
        site = self.get_site(oc_url, account, instance)
        if site is not None:
            # 已有的站点连接
            site.instance_url = instance_url or site.instance_url
            site.instance_name = instance_name or site.instance_name
            site.login(token)
        else:
            # 新的站点连接
            site = Site(
                oc_server=oc_url,
                account=account,
                instance=instance,
                instance_url=instance_url,
                instance_name=instance_name,
                username=username,
                pid=pid,
                token=token
            )
            self.__sites.append(site)

        self.save()
        return site

    def list_sites(self):
        """获取所有连接"""
        return self.__sites

    def get_site(self, oc_url, account, instance):
        """
        获取特定的站点连接
        Args:
            oc_url <str> OC-API 的地址
            account <str> 站点 account
            instance <str> 站点 instance
        Return:
            <Site> or None
        """
        # 如果 oc_url 不匹配，则可能是因为 oc_url 是 oc 的 hostname，因此需要判断
        for site in self.__sites:
            if all([
                site.oc_server == oc_url or str(oc_url) in site.oc_server,
                site.account == account,
                site.instance == instance
            ]):
                return site

    def remove_site(self, site):
        """
        从站点连接管理器中移除某个站点连接，并更新存储文件
        Args:
            site <Site> 要移除的站点连接对象
        """
        site.logout()
        self.__sites.remove(site)
        self.save()


site_manager = SiteManager(storage=os.path.join(APP_DATA, 'connection.json'))


def get_site_manager(storage=None):
    """
    获取站点连接管理器。如果不指定 storage，则返回默认的站点连接管理器。
    Args:
        storage <str> 存放已有站点连接数据的文件
    """
    if storage is None:
        return site_manager
    else:
        return SiteManager(storage)
