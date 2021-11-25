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
'''Sitebot 服务启动脚本'''
from __future__ import print_function
import os
import sys
from Crypto.PublicKey import RSA

# SSH related files
KEY_DIR = os.path.expanduser('~/.ssh')
SSH_KEY = os.path.join(KEY_DIR, 'id_rsa')
SSH_PUB = os.path.join(KEY_DIR, 'id_rsa.pub')
AUTH_FILE = os.path.join(KEY_DIR, 'authorized_keys')


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


if __name__ == '__main__':
    gen_ssh_key()

    if os.path.isfile('/sitebot/main.py'):
        entrance = '/sitebot/main.py'
    else:
        entrance = '/sitebot/main.pyc'

    if os.getenv('MANAGER_TOKEN') is None:
        print('请配置机器人的访问 token')
        sys.exit(1)
    else:
        sitebot_token = os.getenv('MANAGER_TOKEN')
        os.system(
            'python {} "{}"'.format(entrance, sitebot_token),
        )
