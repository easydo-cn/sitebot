#!/usr/bin/env python
# _*_ coding:utf-8 _*_

"""
与证书相关的工具函数
"""

import os
import tempfile
import shutil

import requests

import config

from datetime import datetime
from zipfile import ZipFile

from OpenSSL import crypto
from utils import save_file


def get_certificate_expire_date_by_file(path):
    """
    通过读取证书文件获取过期时间
    Args:
        path <str> 证书文件的路径
    Return:
        <datetime.datetime> 证书的过期时间
    """
    with open(path) as f:
        cert = crypto.load_certificate(crypto.FILETYPE_PEM, f.read())
    return datetime.strptime(cert.get_notAfter(), "%Y%m%d%H%M%Sz")


def update_certificate(url, path):
    """
    从指定服务器上下载证书文件
    Args:
        url <str> 指向 zip 文件的 URL
        path <str> 证书文件存放目录的本地路径
    """
    tempdir = tempfile.mkdtemp(prefix="ac_")
    temp_zipfile = os.path.join(tempdir, "ac_cert.zip")
    save_file(temp_zipfile, requests.get(url, stream=True))

    with ZipFile(temp_zipfile) as zf:
        zf.extract('assistant.crt', tempdir)
        zf.extract('assistant.key', tempdir)

    crt_file = os.path.join(tempdir, "assistant.crt")
    key_file = os.path.join(tempdir, "assistant.key")

    with open(crt_file) as f:
        cert = crypto.load_certificate(crypto.FILETYPE_PEM, f.read())
        if cert.get_subject().CN == config.ADDRESS:
            expire_date = get_certificate_expire_date_by_file(crt_file)
            if (expire_date - datetime.now()).days > config.NEAR_EXPIRE_DATE:
                shutil.copy2(crt_file, os.path.join(path, 'assistant.crt'))
                shutil.copy2(key_file, os.path.join(path, 'assistant.key'))

    try:
        shutil.rmtree(tempdir)
    except Exception:
        pass
