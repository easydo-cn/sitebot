# -*- coding: utf-8 -*-

import os
from random import randint
from tempfile import mkdtemp

import pytest

from config import EDO_TEMP
from filestore import get_file_store
from libs.easydo_fs.cached_wo_client import PCachedWoClient
from libs.easydo_fs.lazyfile import LazyFile


@pytest.fixture(scope='session')
def wo_client():
    wo_api = os.environ.get('WO_API', 'https://zopen.beta.easydo.cn/wo_api')
    oc_api = os.environ.get('OC_API', 'https://oc-api.beta.easydo.cn/')
    secret = os.environ.get('SECRET', '022127e182a934dea7d69s10697s8ac2')
    account = os.environ.get('ACCOUNT', 'zopen')
    instance = os.environ.get('INSTANCE', 'default')
    try:
        token = os.environ['TOKEN']
    except KeyError:
        raise RuntimeError('Environment var not set. please see tests/README')

    wo_client = PCachedWoClient(
        wo_api,
        'test',
        secret,
        auth_host=oc_api,
        account=account,
        instance=instance,
        timeout=10,
    )
    wo_client.auth_with_token(token)

    yield wo_client


@pytest.fixture(scope='session')
def filestore(wo_client):
    oc_api = os.environ.get('OC_API', 'https://oc-api.beta.easydo.cn/')
    account = os.environ.get('ACCOUNT', 'zopen')
    instance = os.environ.get('INSTANCE', 'default')
    try:
        token = os.environ['TOKEN']
        remote_file = os.environ['REMOTE_FILE']
    except KeyError:
        raise RuntimeError('Environment var not set. please see tests/README')

    fs = get_file_store(
        server_url=oc_api,
        account=account,
        instance=instance,
        token=token,
    )
    remote_dir = os.path.dirname(remote_file)
    sync_folder = remote_dir.replace('/', '_') + '-1'
    sync_path = os.path.join(EDO_TEMP, 'mounted', sync_folder)

    if not os.path.isdir(sync_path):
        os.makedirs(sync_path)

    properties = wo_client.content.properties(path=remote_dir)
    fs.new_syncfolder(local=sync_path, remote=properties['uid'])

    yield fs


@pytest.fixture()
def lazyfile(wo_client, filestore):
    try:
        remote_file = os.environ['REMOTE_FILE']
    except KeyError:
        raise RuntimeError('Environment var not set. please see tests/README')
    temp_dir = mkdtemp('-pytest-lazyfile')
    lazyfile = LazyFile(
        remote_file,
        remote_file,
        filestore,
        wo_client,
        cache_dir=temp_dir,
    )
    assert lazyfile.properties['bytes'] >= 1024 * 1024
    yield lazyfile
    lazyfile.close()


def test_read_1_byte(lazyfile):
    lazyfile.seek(0)
    ret = lazyfile.read(1)
    assert len(ret) == 1
    assert ret == lazyfile._read_remote(0, 1)


def test_read_1kb(lazyfile):
    lazyfile.seek(0)
    ret = lazyfile.read(1024)
    assert len(ret) == 1024
    assert ret == lazyfile._read_remote(0, 1024)


def test_read_1025_bytes(lazyfile):
    lazyfile.seek(0)
    ret = lazyfile.read(1025)
    assert len(ret) == 1025
    assert ret == lazyfile._read_remote(0, 1025)


def test_read_2kb(lazyfile):
    lazyfile.seek(0)
    ret = lazyfile.read(2048)
    assert len(ret) == 2048
    assert ret == lazyfile._read_remote(0, 2048)


def test_read_3kb(lazyfile):
    lazyfile.seek(0)
    ret = lazyfile.read(1024 * 3)
    assert len(ret) == 1024 * 3
    assert ret == lazyfile._read_remote(0, 1024 * 3)


def test_read_1_sector(lazyfile):
    lazyfile.seek(0)
    ret = lazyfile.read(512)
    assert len(ret) == 512
    assert ret == lazyfile._read_remote(0, 512)


def test_read_4kb(lazyfile):
    lazyfile.seek(0)
    ret = lazyfile.read(1024 * 4)
    assert len(ret) == 1024 * 4
    assert ret == lazyfile._read_remote(0, 1024 * 4)


def test_seek_and_read(lazyfile):
    offset = 1024 * 4 + 1
    read_size = 1024 * 4
    lazyfile.seek(offset)
    ret = lazyfile.read(read_size)
    assert len(ret) == read_size
    assert ret == lazyfile._read_remote(offset, offset + read_size)


@pytest.mark.repeat(100)
def test_random_seek_and_read(lazyfile):
    offset = randint(0, lazyfile.properties['bytes'])
    read_size = randint(1, lazyfile.properties['bytes'] - offset)
    lazyfile.seek(offset)
    ret = lazyfile.read(read_size)
    assert len(ret) == read_size
    assert ret == lazyfile._read_remote(offset, offset + read_size)
