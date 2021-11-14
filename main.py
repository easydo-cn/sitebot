# -*- coding: utf-8 -*-
'''桌面助手入口文件'''
import base64
import urllib
import urlparse

import config

from libs import monkey
monkey.patch_all(not config.IGNORE_SSL, load_certs=True)

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
    translate as _, get_logger, load_logging_config,
    get_certificate_expire_date_by_file, update_certificate, process_exists
)
from config import (
    FILE_STORE_DIR, DATA_VERSION, DATA_VERSION_FILE, APP_DATA,
    INTERNAL_URL,
)
import ui_client

logger = get_logger('webserver', filename='webserver.log')


def check_certificate():
    """
    检查证书文件是否需要更新
    """
    certifi_folder = os.path.join(APP_DATA, "certifi")
    expire_date = get_certificate_expire_date_by_file(
        os.path.join(certifi_folder, "assistant.crt")
    )
    if (expire_date - datetime.now()).days <= config.NEAR_EXPIRE_DATE:
        logger.debug(u"证书临近过期，将自动更新")
        try:
            update_certificate(config.DEFAULT_CERTIFI_URL, certifi_folder)
        except Exception:
            logger.exception(u"证书更新失败")
        else:
            logger.debug(u"证书更新完成")
    else:
        logger.debug(u"无需更新证书")

def main():
    # PyInstaller 多进程支持
    freeze_support()
    if len(sys.argv) == 1:
        print('请通过参数指定机器人的访问 token')
        sys.exit(1)
    else:
        os.environ['APP_TOKEN'] = sys.argv[1]

    load_logging_config()
    check_certificate()  # TODO 这个好像不应该在这里？

    config.DEBUG = True

    try:
        logger.debug(
            u'桌面助手以 %s 模式启动',
            u'静默' if headless else u'cmdline',
        )
        from libs.managers import get_site_manager
        # 无界面模式启动桌面助手，所有连接的消息线程都要启动
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
