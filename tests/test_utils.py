# coding: utf-8
import hashlib
import logging
import os
import tempfile
import unittest

from assistent import utils


class UtilsTestClass(unittest.TestCase):

    def setUp(self):
        fd, self.tempfile = tempfile.mkstemp()
        os.close(fd)

    def tearDown(self):
        os.remove(self.tempfile)

    def test_translate(self):
        self.assertIs(
            utils.translate,
            utils._,
            msg='_ is a reference of utils.translate'
        )
        self.assertIsInstance(
            utils.translate('Sitebot'),
            unicode,
            msg='Translated result should be instances of unicode'
        )
        self.assertIsInstance(
            utils.translate('Sublime Text'),
            unicode,
            msg='Untranslated result should be instances of unicode'
        )
        self.assertEqual(
            utils.translate('Sitebot'),
            u'站点机器人',
            msg='App name should be translated'
        )

    def test_get_file_md5(self):
        self.assertEqual(
            utils.get_file_md5(self.tempfile),
            hashlib.md5().hexdigest(),
            msg='MD5 of a empty file should be d41d8cd98f00b204e9800998ecf8427e'
        )

    def test_get_worker_logger(self):
        logger = utils.get_worker_logger(1)
        self.assertIsInstance(
            logger,
            logging.Logger,
            msg='A worker logger should be an instance of logging.Logger'
        )
        self.assertTrue(
            len(logger.handlers) > 0,
            msg='A worker logger should have at least one handler'
        )
        utils.close_logger(logger)
        self.assertEqual(
            len(logger.handlers),
            0,
            msg='A closed worker logger should have no handler'
        )

    def test_search_dict_list(self):
        d_list = [
            {
                'a': 'b',
                'b': 1,
            },
            {
                'a': 'c',
                'b': 1,
            },
            {
                'a': 'a',
                'b': 'd',
            },
            {
                'a': 'b',
                'b': 1,
            },
        ]
        self.assertEqual(
            len(utils.search_dict_list(d_list, pair={'a': 'b'})),
            2,
            msg='There are 2 dicts with {"a": "b"}'
        )
        self.assertEqual(
            len(utils.search_dict_list(d_list, pair={'a': 'b', 'b': 1})),
            2,
            msg='There are 2 dicts with {"a": "b", "b": 1}'
        )
        self.assertEqual(
            len(utils.search_dict_list(d_list, pair={'a': 'e'})),
            0,
            msg='There is no dict with {"a": "e"}'
        )
        self.assertEqual(
            len(utils.search_dict_list(d_list, pair={})),
            len(d_list),
            msg='Search with empty pair should return all elements in list'
        )
        self.assertEqual(
            len(utils.search_dict_list([], pair={'a': 'b'})),
            0,
            msg='There is no dict with {"a": "b"} in empty list'
        )

    def test_unify_path(self):
        self.assertIsInstance(
            utils.unify_path('Test'),
            unicode,
            msg='Should return unicode for ASCII string'
        )
        self.assertIsInstance(
            utils.unify_path(u'Test'),
            unicode,
            msg='Should return unicode for ASCII unicode'
        )
        self.assertIsInstance(
            utils.unify_path('测试テスト'),
            unicode,
            msg='Should return unicode for non-ASCII string'
        )
        self.assertIsInstance(
            utils.unify_path(u'测试テスト'),
            unicode,
            msg='Should return unicode for non-ASCII unicode'
        )
