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
import binascii

from Crypto.Cipher import AES
from Crypto import Random


def as_bytes(s):
    '''Ensure `s` is bytes type.
    Currently we're on Python 2 so just take care of unicode type.'''
    if isinstance(s, unicode):
        return s.encode('utf-8')
    else:
        return s


class AesLocker(object):
    """
    >>> from Crypto import Random
    >>> key = Random.new().read(AES.block_size)
    >>> iv = Random.new().read(AES.block_size)
    >>> text = b"Hello world"
    >>> skey = AesLocker(key, iv)
    >>> kkey = AesLocker(key, iv)

    >>> kkey.decrypto(skey.encrypto(text)) == text
    True
    >>> skey.decrypto(kkey.encrypto(text)) == text
    True
    """
    def __init__(self, key=None, iv=None):
        Random.atfork()
        self.key = key or Random.new().read(AES.block_size)
        self.iv = iv or Random.new().read(AES.block_size)
        self.cipher = AES.new(self.key, AES.MODE_CFB, self.iv)

    def encrypto(self, bytes_block):
        return self.cipher.encrypt(as_bytes(bytes_block))

    def decrypto(self, bytes_block):
        return self.cipher.decrypt(as_bytes(bytes_block))


class WorkerLocker(object):
    '''Worker AesLocker'''
    def __init__(self, iv=None):
        from config import APP_ID
        self.__key = APP_ID[:32]
        Random.atfork()
        if iv:
            if len(iv) < AES.block_size:
                raise ValueError('IV must be of length {}'.format(AES.block_size))
            else:
                self.iv = iv[:AES.block_size]
        else:
            self.iv = Random.new().read(AES.block_size)

    def enc(self, bytes_block):
        '''
        ??????????????? hexlify ?????????????????????????????? UTF-8 ????????????
        '''
        return binascii.hexlify(
            AesLocker(key=self.__key, iv=self.iv).encrypto(bytes_block)
        )

    def dec(self, bytes_block):
        '''
        ???????????????????????? hexlify ??????????????????????????????????????? unhexlify
        '''
        return AesLocker(key=self.__key, iv=self.iv).decrypto(
            binascii.unhexlify(bytes_block)
        )
