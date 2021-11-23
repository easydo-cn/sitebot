# -*- coding: utf-8 -*-
'''站点机器人入口文件'''
import config
import os
import sys
from multiprocessing import freeze_support
from utils import (
    get_logger, load_logging_config, process_exists
)

logger = get_logger('webserver', filename='webserver.log')


def main():
    # PyInstaller 多进程支持
    freeze_support()
    logger.debug("参数 ：%s", sys.argv)
    if len(sys.argv) == 1:
        print('请通过参数指定机器人的访问 token')
        sys.exit(1)
    else:
        os.environ['APP_TOKEN'] = sys.argv[1]

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
