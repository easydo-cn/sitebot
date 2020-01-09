#!/usr/bin/env python
# _*_ coding:utf-8 _*_

"""
desktop 模块提供了一些界面与系统交互的工具函数
|- console 子模块提供了控制桌面助手控制台的工具函数
"""

import json
import logging
import os
import time
import webbrowser

import requests

import console
import ui_client
import worker

logger = logging.getLogger(__name__)
HISTORY_PATH = ''

def message(title, body, type='info'):
    """
    调用桌面助手冒泡提示一条消息
    Args:
        title <str> 消息的标题
        body <str> 消息的内容
        type <str|optional> 消息上的图标，可选值为 ['info', 'warn', 'error']
    """
    if type not in ['info', 'warn', 'error']:
        type = 'info'
    try:
        return ui_client._request_api(
            'ui/message', {'title': title, 'body': body, 'type': type}
        ).json()
    except Exception:
        logger.exception("call libs.desktop.message failed")
        return {'success': False}


def msgbox(title, body, buttons=None):
    """
    弹出一个窗口，并返回用户点击按钮的文字或者 False（点击了右上角的关闭）
    Args:
        title <str> 窗口标题
        body <str> 要展示的消息
        buttons <list|str|optional> 弹窗上要渲染的按钮文本
    Return:
        <str|False> 被点击按钮的文本或 False
    Raise:
        RuntimeError
    """
    msgbox_id = ui_client.question_start(title, body, buttons).get('id', None)
    if msgbox_id is None:
        raise RuntimeError("failed to pop up msgbox")
    result = ui_client.question_status(msgbox_id).get('selected', None)
    while result is None:
        time.sleep(1)
        result = ui_client.question_status(msgbox_id).get('selected', None)
    return result


def window(
    url=None, body=None, title=None,
    size=None, position=None, resizable=True,
    maxbutton=True, minbutton=True
):
    """
    弹出一个窗口，加载 URL 或渲染 body 字段（有默认模板），必须指定 url 或 body
    Args:
        url <str> 要加载的 URL
        body <str> 要渲染的页面 body 内容
        title <str|optional> 窗口标题
        size <2-tuple|optional> 窗口大小，默认为 (500, 360)
        position <2-tuple|optional> 窗口位置，默认居中
        resizable <bool|optional> 是否可以改变窗口大小
        maxbutton <bool|optional> 是否显示最大化按钮
        minbutton <bool|optional> 是否显示最小化按钮
    """
    if not any([url, body, ]):
        return {'success': False, 'reason': "both url and body are NoneType"}
    try:
        return ui_client._request_api(
            '/ui/show_webview',
            kw={
                'url': url,
                'body': body,
                'title': title,
                'size': json.dumps(size),
                'position': json.dumps(position),
                'resizable': json.dumps(resizable),
                'maxbutton': json.dumps(maxbutton),
                'minbutton': json.dumps(minbutton),
            },
            internal=True, timeout=5
        ).json()
    except Exception:
        logger.exception("call libs.desktop.window failed")
        return {"success": False}


def open_path(path):
    """
    使用默认打开方式打开文件，或使用文件管理器打开文件夹
    Args:
        path <str> 要打开的路径
    """
    ui_client.open_path(path)


def show_in_folder(path):
    """
    在文件管理器中打开指定项所在文件夹，并选中指定项
    注：当前只支持在 Windows 平台选中指定项，在其他平台只会打开所在文件夹
    Args:
        path <str> 要打开的文件夹路径
    """
    ui_client.show_in_folder(path)


def open_url(url):
    """
    使用用户的默认浏览器打开指定的 URL
    Args:
        url <str> 要访问的 URL
    """
    webbrowser.open(url)


def show_task_window(task_id):
    """
    显示指定任务的详情
    Args:
        task_id <int> 任务 ID
    """
    if task_id not in worker.list_worker_ids():
        logger.debug("task id %d is invalid", task_id)
        return json.dumps({'success': False, 'reason': 'invalid id'})
    return ui_client._request_api(
        '/ui/report_detail', {'worker_id': task_id}
    ).json()


def quit():
    """
    退出桌面助手
    """
    ui_client.quit_assistant()


def ready_to_quit():
    """
    询问桌面助手能否执行退出操作
    """
    try:
        return ui_client._request_api('ready_to_quit').json().get('ready', False)  # noqa E501
    except requests.exceptions.HTTPError:
        return False


def select_path(mode='folder', timeout=300):
    """
    :param mode: 选择文件的方式（files/folder）
    :param timeout: 选择文件框有效时间，超出这个时间选择的文件是无效的
    :return: 返回选择文件的路径
    """
    try:
        return ui_client._request_api(
            '/ui/select_paths',
            kw={
                'mode': mode,
            }, timeout=timeout).json()
    except Exception as e:
        logger.debug(e)
        return {"success": False}


