# -*- coding: utf-8 -*-
from __future__ import division

import math
import os
import string
from random import choice, randint
from shutil import rmtree
from tempfile import mkdtemp

import pytest

from libs.easydo_fs.cachefile import BUFF_SIZE, CacheFile

TEST_FILE_SIZE = int(math.floor(1024 * 1024 * 9.5))
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(scope='session', autouse=True)
def data_bin():
    fname = 'data.bin'
    fpath = os.path.join(DATA_DIR, fname)
    with open(fpath, 'w+b') as f:
        random_digits = [choice(string.digits) for _ in range(TEST_FILE_SIZE)]
        f.write(''.join(random_digits))

    with open(fpath, 'r+b') as f:
        yield f


@pytest.fixture()
def tempd():
    tempd = mkdtemp('.pytest-cachefile')
    assert os.path.isdir(tempd)
    yield tempd
    rmtree(tempd)


@pytest.fixture()
def cachefile(tempd, data_bin):
    def _read_origin(start, end):
        data_bin.seek(start)
        return data_bin.read(end - start)

    uid = 12345
    revision = 1
    file_size = TEST_FILE_SIZE  # 9.5MB total size
    cache_dir = tempd

    yield CacheFile(uid, revision, file_size, cache_dir, _read_origin)


def test_read_1_byte(cachefile, data_bin):
    ret = cachefile.read(0, 1)
    assert len(ret) == 1
    data_bin.seek(0)
    assert ret == data_bin.read(1)


def test_read_1_block(cachefile, data_bin):
    ret = cachefile.read(0, BUFF_SIZE)
    assert len(ret) == BUFF_SIZE
    data_bin.seek(0)
    assert ret == data_bin.read(BUFF_SIZE)


def test_read_1_block_plus_1_byte(cachefile, data_bin):
    ret = cachefile.read(0, BUFF_SIZE + 1)
    assert len(ret) == BUFF_SIZE + 1
    data_bin.seek(0)
    assert ret == data_bin.read(BUFF_SIZE + 1)


def test_read_2_block(cachefile, data_bin):
    ret = cachefile.read(0, BUFF_SIZE * 2)
    assert len(ret) == BUFF_SIZE * 2
    data_bin.seek(0)
    assert ret == data_bin.read(BUFF_SIZE * 2)


def test_read_3_block(cachefile, data_bin):
    ret = cachefile.read(0, BUFF_SIZE * 3)
    assert len(ret) == BUFF_SIZE * 3
    data_bin.seek(0)
    assert ret == data_bin.read(BUFF_SIZE * 3)


def test_read_eof(cachefile, data_bin):
    ret = cachefile.read(TEST_FILE_SIZE, TEST_FILE_SIZE + 1)
    assert len(ret) == 0
    data_bin.seek(TEST_FILE_SIZE)
    assert ret == data_bin.read(1)


def test_read_last_byte(cachefile, data_bin):
    ret = cachefile.read(TEST_FILE_SIZE - 1, TEST_FILE_SIZE)
    assert len(ret) == 1
    data_bin.seek(TEST_FILE_SIZE - 1)
    assert ret == data_bin.read(1)


def test_read_over_filesize(cachefile, data_bin):
    ret = cachefile.read(TEST_FILE_SIZE - 1, TEST_FILE_SIZE + 1)
    assert len(ret) == 1
    data_bin.seek(TEST_FILE_SIZE - 1)
    assert ret == data_bin.read(2)


def test_read_last_block_to_eof(cachefile, data_bin):
    offset = 1024 * 1024 * TEST_FILE_SIZE // (1024 * 1024)
    read_size = 1024 * 1024
    ret = cachefile.read(offset, offset + read_size)
    assert len(ret) == TEST_FILE_SIZE - offset
    assert len(ret) <= read_size
    data_bin.seek(offset)
    assert ret == data_bin.read(read_size)


def test_read_all(cachefile, data_bin):
    ret = cachefile.read(0, TEST_FILE_SIZE)
    assert len(ret) == TEST_FILE_SIZE
    data_bin.seek(0)
    assert ret == data_bin.read()


def test_seek_and_read(cachefile, data_bin):
    start = 12345
    read_size = BUFF_SIZE

    ret = cachefile.read(start, start + read_size)
    assert len(ret) == read_size
    data_bin.seek(start)
    assert ret == data_bin.read(read_size)


@pytest.mark.repeat(100)
def test_random_seek_and_read(cachefile, data_bin):
    start = randint(0, TEST_FILE_SIZE)
    read_size = randint(1, TEST_FILE_SIZE - start)
    ret = cachefile.read(start, start + read_size)
    assert len(ret) == read_size
    data_bin.seek(start)
    assert ret == data_bin.read(read_size)
