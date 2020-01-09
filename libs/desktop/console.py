#!/usr/bin/env python
# _*_ coding:utf-8 _*_

"""
console 模块提供控制桌面助手控制台的工具
"""

import ui_client


def set_active_tab(tab_name):
    """
    设置桌面助手控制台展示的 tab 页
    Args:
        tab_name <str> tab 页的名字
    """
    try:
        return ui_client._request_api(
            '/ui/activate_tab', {'name': tab_name}, True
        ).json()
    except Exception:
        return {'suceess': False}


def show():
    """
    显示桌面助手控制台
    """
    try:
        return ui_client._request_api("/ui/show_console", internal=True).json()
    except Exception:
        return {'success': False}


def refresh_tab(tab_name):
    """
    刷新桌面助手控制台的指定 tab 页
    Args:
        tab_name <str> tab 页的名字
    Return:
        <json> {'success': True/False}
    """
    return ui_client.refresh_webview(tab_name)
