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

import unittest

from acrypto import AesLocker, WorkerLocker


class AesLockerTestCase(unittest.TestCase):

    def setUp(self):
        self.key = '12345678abcdefgh'
        self.iv = '12345678abcdefgh'

    def test_1_aeslocker_init(self):
        # key 和 iv 必须是 16 位字符串（AES.block_size）
        self.assertIsInstance(AesLocker(), AesLocker)
        self.assertRaises(Exception, AesLocker, key='12345678')
        self.assertRaises(Exception, AesLocker, iv='12345678')
        self.assertRaises(Exception, AesLocker, key='12345678', iv='12345678')
        self.assertIsInstance(AesLocker(key=self.key, iv=self.iv), AesLocker)

    def test_2_aeslocker_str(self):
        plain_text = b'123abc你好こんにちは'
        encrypted_text = AesLocker(key=self.key, iv=self.iv).encrypto(plain_text)
        self.assertNotEqual(encrypted_text, plain_text)

        decrypted_text = AesLocker(key=self.key, iv=self.iv).decrypto(encrypted_text)
        self.assertEqual(decrypted_text, plain_text)

    def test_3_aeslocker_unicode(self):
        plain_text = u'123abc你好こんにちは'
        self.assertIsInstance(plain_text, unicode)

        encrypted_text = AesLocker(key=self.key, iv=self.iv).encrypto(plain_text)
        self.assertNotEqual(encrypted_text, plain_text)

        decrypted_text = AesLocker(key=self.key, iv=self.iv).decrypto(encrypted_text)
        self.assertIsInstance(decrypted_text, str)
        self.assertEqual(decrypted_text.decode('utf-8'), plain_text)


class WorkerLockerTestCase(unittest.TestCase):

    def setUp(self):
        self.iv = '12345678abcdefgh'

    def test_1_workerlocker_init(self):
        self.assertIsInstance(WorkerLocker(), WorkerLocker)
        self.assertRaises(Exception, WorkerLocker, iv='12345678')
        self.assertIsInstance(WorkerLocker(iv=self.iv), WorkerLocker)

    def test_2_str(self):
        locker = WorkerLocker(iv=self.iv)
        plain_text = b'123abc你好こんにちは'

        encrypted_text = locker.enc(plain_text)
        self.assertNotEqual(encrypted_text, plain_text)

        decrypted_text = locker.dec(encrypted_text)
        self.assertEqual(decrypted_text, plain_text)

    def test_3_unicode(self):
        locker = WorkerLocker(iv=self.iv)
        plain_text = u'123abc你好こんにちは'

        encrypted_text = locker.enc(plain_text)
        self.assertNotEqual(encrypted_text, plain_text)

        decrypted_text = locker.dec(encrypted_text)
        self.assertIsInstance(decrypted_text, str)
        self.assertEqual(decrypted_text.decode('utf-8'), plain_text)
