# -*- coding: utf-8 -*-
from __future__ import print_function
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

'''站点机器人入口文件'''
import base64
import urllib
import urlparse

import config

from contextlib import closing
import getpass
import os
import sys
import json
import shutil
import socket
import sqlite3
import threading
import time
from copy import deepcopy
from datetime import datetime
from multiprocessing import freeze_support

from werkzeug.urls import url_decode

from utils import (
    translate as _, get_logger, load_logging_config, process_exists
)
from config import (
    DATA_VERSION, DATA_VERSION_FILE, APP_DATA,
    INTERNAL_URL,
)
import ui_client
from Crypto.PublicKey import RSA

# SSH related files
KEY_DIR = os.path.expanduser('~/.ssh')
SSH_KEY = os.path.join(KEY_DIR, 'id_rsa')
SSH_PUB = os.path.join(KEY_DIR, 'id_rsa.pub')
AUTH_FILE = os.path.join(KEY_DIR, 'authorized_keys')

logger = get_logger('webserver', filename='webserver.log')

def gen_ssh_key():
    # Generate SSH key pair
    if not os.path.exists(SSH_KEY) or not os.path.exists(SSH_PUB):
        if not os.path.exists(KEY_DIR):
            os.mkdir(KEY_DIR)

        key = RSA.generate(2048)
        pubkey = key.publickey()

        with open(SSH_KEY, 'w') as pvkf:
            pvkf.write(key.exportKey('PEM'))

        os.chmod(SSH_KEY, 0600)

        with open(SSH_PUB, 'w') as pbkf:
            pbkf.write(pubkey.exportKey('OpenSSH'))

    # Write pubkey to `authorized_keys` so we can ssh into current container
    with open(SSH_PUB, 'rb') as rf:
        pubkey_content = rf.read()
    if os.path.exists(AUTH_FILE):
        with open(AUTH_FILE, 'rb') as rf:
            host_auth_keys = rf.read()
    else:
        host_auth_keys = ''

    if pubkey_content not in host_auth_keys:
        with open(AUTH_FILE, 'a') as af:
            af.write(pubkey_content)


def main():
    if os.getenv('MANAGER_TOKEN') is None:
        print('请配置机器人的访问 token')
        sys.exit(1)

    # PyInstaller 多进程支持
    freeze_support()
    gen_ssh_key()
    load_logging_config()

    config.DEBUG = True
    try:
        logger.debug(
            u'站点机器人以静默模式启动',
        )
        from libs.managers import get_site_manager
        # 无界面模式启动机器人，所有连接的消息线程都要启动
        for site in get_site_manager().list_sites():
            site.get_message_thread().connect()
        from headless_server import start_server
        start_server()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit('Exit by key interrupt')


if __name__ == "__main__":
    main()

