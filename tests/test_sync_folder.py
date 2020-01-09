import unittest

from syncfolder import SyncManager, SyncFolder

import workers.sync
import workers.setup_syncfolder


class SyncFolderTest(unittest.TestCase):
    def setUp(self):
        self.oc_server_host = 'http://192.168.1.79'
        self.instance = 'default'
        self.account = 'zopen'
        self.uid = ''
        self.path = ''

        self.sync_folder = SyncFolder(
            oc_server_host=self.oc_server_host,
            instance=self.instance,
            account=self.account,
            uid=self.uid,
            path=self.path
        )

    def test_init_sync_folder(self):

        self.assertIsInstance(self.sync_folder, SyncFolder, "sync folder init success")

    def test_run(self):
        self.assertRaises(AttributeError, self.sync_folder.run())

    def test_pause(self):
        self.assertIsInstance(self.sync_folder.pause(), basestring)

    def test_state(self):
        self.assertIsInstance(self.sync_folder.state(), basestring)

    def test_property(self):
        self.assertIsInstance(self.sync_folder.properties(), dict)


class SyncManagerTest(unittest.TestCase):
    def setUp(self):
        self.params = dict(oc_server='http://192.168.1.135:80/oc_api', instance='default',
                           token='73aeffa9836b6e717e60e51fc27a404e', account='zopen',
                           site_name='192.168.1.135', uid='1477813047', path='E:\\test', sync_type='sync', policy='auto')
        self.sync_folder = SyncFolder(**self.params)

    def test_add(self):
        manager = SyncManager()
        id = SyncManager.get_id(
            self.params.get('oc_server'), self.params.get('instance'),
            self.params.get('account'), self.params.get('path')
        )
        self.assertIsInstance(id, str, 'Syncfolder ID should be string instance')
        if manager.get(id) is None:
            manager.add(self.sync_folder)
        self.assertIsNotNone(id)

    def test_modify(self):
        manager = SyncManager()
        id = SyncManager.get_id(
            self.params.get('oc_server'), self.params.get('instance'),
            self.params.get('account'), self.params.get('path')
        )
        if manager.get(id) is None:
            manager.add(self.sync_folder)
        if self.sync_folder.policy == 'manual':
            self.sync_folder.policy = 'auto'
        else:
            self.sync_folder.policy = 'manual'
        manager.modify(self.sync_folder)

    def test_delete(self):
        manager = SyncManager()
        id = SyncManager.get_id(
            self.params.get('oc_server'), self.params.get('instance'),
            self.params.get('account'), self.params.get('path')
        )
        if manager.get(id) is None:
            manager.add(self.sync_folder)
        manager.delete(id)
        self.assertIsNone(manager.get(id))
        self.assertEquals(len(manager.list()), 0)

    def test_list(self):
        manager = SyncManager()
        self.assertIsInstance(manager.list(), list)


if __name__ == '__main__':
    unittest.main()
