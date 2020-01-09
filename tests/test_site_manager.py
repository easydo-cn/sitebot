#!/usr/bin/env python
# _*_ coding:utf-8 _*_

import hashlib
import os
import tempfile
import unittest

import pytest

from libs.managers import get_site_manager
from libs.managers.site import Site
from messenger import Messaging


class TestSiteManager(unittest.TestCase):

    @classmethod
    def setup_class(self):
        self.storage = os.path.join(tempfile.mkdtemp(), "test.json")
        self.site_manager = get_site_manager(storage=self.storage)
        self.oc_url = "http://192.168.1.116:1100/oc_api"
        self.account = "zopen"
        self.instance = "default"
        self.pid = "users.admin"
        self.test_id = hashlib.md5(
            '|'.join([self.oc_url, self.account, self.instance])
        ).hexdigest()

    @classmethod
    def teardown_class(self):
        try:
            os.remove(self.storage)
        except Exception:
            pass

    @pytest.fixture(autouse=True)
    def _site(self):
        self.site = self.site_manager.get_site(
            self.oc_url, self.account, self.instance
        )

    def test_00_add_new_site(self):
        """如何往连接管理器里新建一个连接？"""
        site = self.site_manager.add_site(
            oc_url=self.oc_url,
            account=self.account,
            instance=self.instance,
            instance_url="http://192.168.1.116:1100/default/",
            instance_name=u"文档管理",
            username="admin",
            pid=self.pid,
            token="df52ccc3ae7df3ede91d55bfc5d886eb"
        )
        self.assertIsInstance(site, Site)
        self.assertEqual(site.id, self.test_id)

    def test_01_list_all_sites(self):
        """如何查看连接管理器中所有连接？"""
        self.assertEqual(len(self.site_manager.list_sites()), 1)

    def test_02_get_specific_site(self):
        """如何获取特定的站点连接？"""
        site = self.site_manager.get_site(
            self.oc_url, self.account, self.instance
        )
        self.assertIsNotNone(site)
        self.assertIsInstance(site, Site)
        self.assertEqual(site.id, self.test_id)
        self.assertIsNone(self.site_manager.get_site('fake', 'fake', 'fake'))

    def test_10_site_login(self):
        """如何更新站点连接的 token？也就是切换站点连接为登录状态"""
        self.site.login("b91e248ca845136d51fb2fff5fd9116f")
        self.assertEqual(self.site.token, "b91e248ca845136d51fb2fff5fd9116f")
        self.site.login("fake token")
        self.assertFalse(self.site.is_token_valid())

    def test_11_site_logout(self):
        """如何登出站点连接？"""
        self.site.logout()
        self.assertIsNone(self.site.token)

    def test_12_site_has_token(self):
        """如何确定站点连接是否已经登录？"""
        self.site.login("b91e248ca845136d51fb2fff5fd9116f")
        self.assertTrue(self.site.has_token())
        self.site.logout()
        self.assertFalse(self.site.has_token())

    def test_13_site_set_option(self):
        """如何更改站点连接的设置项？"""
        self.site.set_config("TestConfig", "Test")
        self.assertIn("TestConfig", self.site.configs)
        self.assertEqual("Test", self.site.configs.get("TestConfig"))
        self.site.set_config("notification", False)
        self.assertFalse(self.site.configs.get("notification"))

    def test_14_site_get_option(self):
        """如何获取站点连接的设置项？"""
        self.assertEqual(self.site.get_config("TestConfig"), "Test")
        self.assertIsNone(self.site.get_config("Fake"))

    def test_15_site_get_client(self):
        """如何通过站点连接得到站点访问客户端？"""
        self.site.login("df52ccc3ae7df3ede91d55bfc5d886eb")
        self.assertEqual(self.site.get_client("oc").api_host, self.oc_url)
        wo_api = self.oc_url.replace("oc_api", "wo_api")
        message_api = self.oc_url.replace("oc_api", "_message")
        self.assertEqual(self.site.get_client("workonline").api_host, wo_api)
        self.assertEqual(self.site.get_client("message").api_host, message_api)

    def test_16_site_get_message_thread(self):
        """如何通过站点连接得到消息线程？"""
        self.assertIsInstance(self.site.get_message_thread(), Messaging)

    def test_20_message_state(self):
        """如何获取消息线程的状态？"""
        message = self.site.get_message_thread()
        self.assertEqual(message.state, "offline")

    def test_21_message_connect(self):
        """如何启动消息线程？"""
        message = self.site.get_message_thread()
        message.connect()
        self.assertTrue(message.is_alive())

    def test_22_message_disconnect(self):
        """如何停止消息线程？"""
        message = self.site.get_message_thread()
        message.disconnect()
        message.join()
        self.assertEqual(message.state, "offline")

    def test_30_remove_site(self):
        """如何删除某个站点连接？"""
        self.site_manager.remove_site(self.site)
        self.assertEqual(len(self.site_manager.list_sites()), 0)
