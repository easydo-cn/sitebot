# -*- coding: utf-8 -*-
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
import pickle
import json
import csv
import os
import shutil
import time

from config import NOUNCE_FIELD
from acrypto import WorkerLocker


def dbopen(filename, flag='c', mode=None, format='json'):
    '''
    Create or load a PersistentDict object
    '''
    return PersistentDict(filename, flag=flag, mode=mode, format=format)


class PersistentDict(dict):
    ''' Persistent dictionary with an API compatible with shelve and anydbm.

    The dict is kept in memory, so the dictionary operations run as fast as
    a regular dictionary.

    Write to disk is delayed until close or sync (similar to gdbm's fast mode).

    Input file format is automatically discovered.
    Output file format is selectable between pickle, json, and csv.
    All three serialization formats are backed by fast C implementations.

    '''
    ENCRYPT_FIELDS = ('token', 'password', )
    ENCRYPT_FIELD_START = 'enc_'

    def __init__(self, filename, flag='c',
                 mode=None, format='pickle',
                 *args, **kwds):
        self.flag = flag  # r=readonly, c=create, or n=new
        self.mode = mode  # None or an octal triple like 0644
        self.format = format  # 'csv', 'json', or 'pickle'
        self.id = os.path.splitext(os.path.basename(filename))[0]
        self.filename = filename
        self.locker = None
        if self.flag != 'n' and os.access(self.filename, os.R_OK):
            fileobj = open(
                self.filename,
                'rb' if self.format == 'pickle' else 'r'
            )
            with fileobj:
                self.load(fileobj)
        if self.locker is None:
            self.locker = WorkerLocker()
            self.__nounce = self.locker.iv
        dict.__init__(self, *args, **kwds)

    def sync(self):
        '''Write dict to disk'''
        if self.flag == 'r':
            return
        filename = self.filename
        tempname = filename + '.tmp'

        try:
            with open(tempname, 'wb' if self.format == 'pickle' else 'w') as f:
                self.dump(f)
        except Exception:
            # dump失败，等1秒，再试
            time.sleep(1)
            with open(tempname, 'wb' if self.format == 'pickle' else 'w') as f:
                self.dump(f)

        shutil.move(tempname, self.filename)  # atomic commit
        if self.mode is not None:
            os.chmod(self.filename, self.mode)

    def close(self):
        self.sync()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def dump(self, fileobj):
        if self.locker is not None:
            assert self.__nounce == self.locker.iv
            data = {}
            for k, v in self.items():
                if k in self.ENCRYPT_FIELDS\
                        or k.startswith(self.ENCRYPT_FIELD_START):
                    data[k] = self.locker.enc(v)
                else:
                    data[k] = v
            data[NOUNCE_FIELD] = binascii.hexlify(self.__nounce)
        else:
            data = dict(self)
        if self.format == 'csv':
            csv.writer(fileobj).writerows(data.items())
        elif self.format == 'json':
            json.dump(data, fileobj, separators=(',', ':'))
        elif self.format == 'pickle':
            pickle.dump(data, fileobj, 2)
        else:
            raise NotImplementedError(
                'Unknown format: {}'.format(repr(self.format))
            )

    def load(self, fileobj):
        # try formats from most restrictive to least restrictive
        for loader in (pickle.load, json.load, csv.reader):
            fileobj.seek(0)
            try:
                data = loader(fileobj)
                # Init locker from given database
                if NOUNCE_FIELD in data:
                    self.locker = WorkerLocker(
                        binascii.unhexlify(data[NOUNCE_FIELD])
                    )
                    self.__nounce = self.locker.iv
                # Unencrypted database
                if self.locker is None:
                    return self.update(data)
                # Contains encrypted fields
                for k in data:
                    if k in self.ENCRYPT_FIELDS\
                            or k.startswith(self.ENCRYPT_FIELD_START):
                        self.update({k: self.locker.dec(data[k])})
                    else:
                        self.update({k: data[k]})
                return
            except Exception:
                pass
        raise ValueError('File not in a supported format')


if __name__ == '__main__':
    '''Test'''
    import random
    import tempfile

    testfile = tempfile.mkstemp(suffix='test.json')[-1]
    # Make and use a persistent dictionary
    with PersistentDict(testfile, 'c', format='json') as d:
        print(d, 'start')
        d['abc'] = '123'
        d['rand'] = random.randrange(10000)
        print(d, 'updated')

    # Show what the file looks like on disk
    with open(testfile, 'rb') as f:
        print(f.read())
