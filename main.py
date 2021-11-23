# -*- coding: utf-8 -*-
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

logger = get_logger('webserver', filename='webserver.log')


def main():
    # PyInstaller 多进程支持
    freeze_support()
    if os.getenv('MANAGER_TOKEN') is None:
        print('请配置机器人的访问 token')
        sys.exit(1)

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
