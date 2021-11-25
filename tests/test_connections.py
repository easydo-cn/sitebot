# coding: utf-8
"""
/*
 * Copyright (c) 2019 EasyDo, Inc. <panjunyong@easydo.cn>
 *
 * This program is free software: you can use, redistribute, and/or modify
 * it under the terms of the GNU Affero General Public License, version 3
 * or later ("AGPL"), as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */
"""

import hashlib
import logging
import os
import shutil
import tempfile
import unittest

from assistent import connections


class SiteConnectionTestCase(unittest.TestCase):

    def setUp(self):
        self.oc_server='http://192.168.1.222/oc/'
        self.account = 'zopen'
        self.instance = 'default'
        self.instance_url='http://192.168.1.222'
        self.instance_name='test instance'
        self.username = 'test user'
        self.pid = 'users.test'
        self.token = 'fake test token'

    def _init(self):
        return connections.SiteConnection(
            oc_server=self.oc_server,
            account=self.account,
            instance=self.instance,
            instance_url=self.instance_url,
            instance_name=self.instance_name,
            username=self.username,
            pid=self.pid,
            token=self.token
        )

    def test_init(self):
        conn = self._init()

    def test_attrs(self):
        conn = self._init()
        for name in (
            'oc_server', 'instance_url', 'instance_name',
            'username', 'pid', 'token',
        ):
            self.assertEquals(
                getattr(conn, name, None), getattr(self, name),
                'attribute `{}` should be the same with init value'.format(name)
            )
        self.assertFalse(
            conn.allow_script, '`allow_script` should be False by default'
        )


class SiteConnectionManagerTestCase(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # We dont want to mess with production storage, small hack here
        self.manager = connections.SiteConnectionManager(
            storage=os.path.join(self.temp_dir, 'connections.json')
        )
        self.oc_server='http://192.168.1.222/oc/'
        self.account = 'zopen'
        self.instance = 'default'
        self.instance_url='http://192.168.1.222'
        self.instance_name='test instance'
        self.username = 'test user'
        self.pid = 'users.test'
        self.token = 'fake test token'

    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        del self.manager

    def test_list(self):
        self.assertSequenceEqual(
            self.manager.list(), [],
            '.list() should return empty list if no connections stored'
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.temp_dir, 'connections.json')),
            'SiteConnectionManager should create JSON file if it does not exist'
        )

    def _add(self):
        return self.manager.add(
            oc_server=self.oc_server,
            account=self.account,
            instance=self.instance,
            instance_url=self.instance_url,
            instance_name=self.instance_name,
            username=self.username,
            pid=self.pid,
            token=self.token
        )

    def test_add(self):
        count_start = len(self.manager.list())

        # Test adding new connection
        added, conn = self._add()
        count_added = len(self.manager.list())

        self.assertTrue(added, '.add() should be able to add')
        self.assertIsInstance(
            conn, connections.SiteConnection,
            '.added() should also return the added connection'
        )
        self.assertEquals(
            count_added, (count_start + 1),
            '.list() should return with added connection as well'
        )

        # Test adding duplicate connection
        added_2, conn_2 = self._add()
        count_added_2 = len(self.manager.list())
        self.assertFalse(
            added_2, '.add() should not add duplicated connection'
        )
        self.assertEquals(
            count_added_2, count_added,
            '.add() should not add duplicated connection'
        )
        self.assertEquals(
            conn.id, conn_2.id,
            '.add() should return existing connection if duplicated'
        )

    def test_remove(self):
        added, conn = self._add()
        count_start = len(self.manager.list())

        removed, conn_2 = self.manager.remove(conn.id)
        count_removed = len(self.manager.list())

        self.assertTrue(removed, '.remove() should be able to remove')
        self.assertEquals(
            count_start, count_removed + 1,
            '.list() result length should reduce after .remove()'
        )
        self.assertIsInstance(
            conn_2, connections.SiteConnection,
            '.remove() should return SiteConnection instance as well'
        )
        self.assertEquals(
            conn.id, conn_2.id,
            '.remove() should return the extact removed connection'
        )

        # Remove a non-existing connection
        removed_2, conn_3 = self.manager.remove('fake non-existing id')
        count_removed_2 = len(self.manager.list())
        self.assertFalse(
            removed_2,
            '.remove() should not be able to remove non-existing connection'
        )
        self.assertIsNone(
            conn_3,
            '.remove() should return None for non-existing connection as well'
        )
        self.assertEquals(
            count_removed, count_removed_2,
            '.list() result length should not change'
        )

    def test_get(self):
        added, conn = self._add()
        conn_get = self.manager.get(conn.id)

        self.assertIsInstance(
            conn_get, connections.SiteConnection,
            '.get() should return instance of SiteConnection'
        )
        self.assertEquals(
            conn.id, conn_get.id,
            '.get() should return extact match by ID'
        )

