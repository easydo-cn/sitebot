import os
import platform
import re
import socket

import config

from datetime import datetime
from dateutil import parser, tz

from qtui.webview_window import show_webview_window
from utils import (
    translate as _, is_syncplugin_installed, render_template_file,
    get_certificate_expire_date_by_file
)

diagnosis_items = []


def diagnosis(exclude_systems=[]):
    """
    添加诊断项
    Args:
        exclude_systems <list|optional> 该诊断项在某些操作系统上不需要运行
    Return:
        <function>
    """
    def decorator(func):
        if platform.system() not in exclude_systems:
            diagnosis_items.append(func)
        return func
    return decorator


@diagnosis()
def diagnose_port_changed():
    if config.HTTP_PORT != 4999 or config.HTTPS_PORT != 4997:
        return {
            'ok': False,
            'title': _("Concurrent ports conflicted"),
            'instructions': [],
            'ports': {'http': config.HTTP_PORT, 'https': config.HTTPS_PORT}
        }
    return {'ok': True}


@diagnosis(exclude_systems=["Linux", "Darwin"])
def diagnose_null_service():
    from win32serviceutil import QueryServiceStatus
    return {
        'ok': QueryServiceStatus('NULL')[1] == 4,
        'title': _('The NULL service is abnormal.'),
        'instructions': [
            # 1. 以管理员身份运行命令行并运行 {}，尝试启动 NULL 服务。
            _(
                "Start the command line as an administrator "
                "and run this command {}, try to start the NULL service."
            ).format(
                "<code>sc config Null start=system && sc start Null</code>"
            ),
            # 2. 如果第 1 步不起作用的话，请运行 {} 对系统进行扫描并自动修复损坏的文件。
            _(
                "If the first step does not work, please run this command {} "
                "to scan the system and try to fix the broken files."
            ).format("<code>sfc /scannow</code>"),
            # 3. 如果上述步骤不起作用，建议重装系统。
            _(
                "If the above steps do not work,"
                "it is recommended to reinstall the operating system."
            ),
        ],
    }


@diagnosis(exclude_systems=["Linux", "Darwin"])
def diagnose_shell_extension():
    return {
        'ok': is_syncplugin_installed(),
        'title': _("The SyncPlugin is not activated."),
        'instructions': [
            _("Please follow the instructions on the SyncPage to activate it."),  # noqa E501
        ]
    }


@diagnosis()
def diagnose_webfolder_task():
    """
    检测映射盘运行条件是否满足
    具体诊断逻辑如下：
    1. 判断是否为支持运行映射盘的操作系统？
    |- 是，进行下一项判断；
    |- 否，诊断不通过，显示“不支持的操作系统 - 当前版本的映射盘只能在 Windows 上运行”
    2. 判断是否已经安装 Dokan 驱动？
    |- 是，诊断通过
    |- 否，进行下一项判断；
    3. 判断操作系统版本是否太旧了？
    |- 是，诊断不通过，显示“不支持的操作系统版本 - 您的操作系统太旧，至少需要 Windows 7 SP1 才能使用映射盘功能”  # noqa E501
    |- 否，进行下一项判断；
    4. 判断当前版本是否需要安装补丁？
    |- 是，进行下一项判断；
    |- 否，诊断不通过，显示“映射盘驱动未安装”
    5. 判断系统补丁是否已经安装？
    |- 是，诊断不通过，显示“映射盘驱动未安装”
    |- 否，诊断不通过，显示“缺少系统补丁和映射盘驱动”
    """

    # 1. 是否为支持运行映射盘的操作系统？
    if platform.system() != 'Windows':
        # 当前版本的映射盘只能在 Windows 上运行
        return {
            'ok': False,
            'title': _("Unsupported operating system"),
            'instructions': [_("The current version webfolder can only run on Windows")],  # noqa E501
        }

    # 2. 是否已经安装 Dokan 驱动？
    from utils.win32_utils import is_dokan_installed
    if is_dokan_installed():
        # Dokan 驱动已安装，诊断通过
        return {'ok': True}

    # 3. 是否操作系统版本太旧了？
    from utils.win32_utils import get_winver
    winver = get_winver()
    if winver < (6, 1, 1):
        # 操作系统版本比 Windows7 SP1 要早
        return {
            'ok': False,
            'title': _('Unsupported OS version'),
            'instructions': [_(
                'Your operating system is outdated, '
                'Windows 7 SP1 or newer is required to use webfolder'
            )]
        }

    # 4. 是否为需要安装补丁的系统版本？
    if winver == (6, 1, 1):
        # 5. 是否已经安装补丁？
        import win32com.client
        patch = 'KB3033929'
        wua = win32com.client.Dispatch("Microsoft.Update.Session")
        update_seeker = wua.CreateUpdateSearcher()
        update_seeker.Online = False
        __ = win32com.client.Dispatch("Microsoft.Update.UpdateColl")
        installed_update = update_seeker.Search(
            "IsInstalled=1 and Type='Software'"
        )
        is_installed = False
        for update in installed_update.Updates:
            if patch in update.Title:
                is_installed = True
                break

        if not is_installed:
            # 补丁没有安装
            return {
                'ok': False,
                'title': _(
                    "Missing system patch: {} and the webfolder driver"
                ).format(patch),
                'instructions': [
                    _(
                        "Please follow this link to get the system patch: {}. "
                        "If already installed, just ignore this prompt."
                    ).format(
                        "<a href='#' onclick='openWithBrowser(\"{}\")'>{}</a>".format(  # noqa E501
                            'https://support.microsoft.com/en-us/kb/3033929',
                            patch
                        )
                    ),
                    _("Click to {}").format(
                        "<a href='#' onclick='installDokan();'>{}</a>".format(
                            _('install the webfolder driver (Requires Administrator privilege)')  # noqa E501
                        )
                    )
                ]
            }

    # 其他条件都满足，只是没有安装映射盘驱动而已
    return {
        'ok': False,
        'title': _('Webfolder driver is not installed'),
        'dokan_not_installed': True,
    }


@diagnosis()
def diagnose_certificate():
    result = {'ok': True}
    try:
        expire_date = get_certificate_expire_date_by_file(
            os.path.join(config.APP_DATA, 'certifi', 'assistant.crt')
        )
    except Exception:
        return {
            'ok': False,
            'title': _('Failed to get the expire date of certificate'),
            'instructions': [_("Try to reinstall Assistant")]
        }
    else:
        expire_in = expire_date - datetime.now()
        if expire_in.days > config.NEAR_EXPIRE_DATE:
            return result
        if expire_in.days > 0:
            title = _("The certificate is close to expiring date")
        else:
            title = _("The certificate has expired")
        cert_file = os.path.join(config.APP_DATA, 'certifi', 'assistant.crt')
        if platform.system() == "Windows":
            cert_file = cert_file.replace(os.path.sep, "\\\\")
        return {
            'ok': False,
            'title': title,
            'instructions': [
                # Step 1. 下载新的证书文件
                "<a href='#' onclick='openWithBrowser(\"{}\")'>{}</a>".format(
                    config.DEFAULT_CERTIFI_URL,
                    _("Please visit this link to download a new certificate")
                ),
                # Step 2. 打开证书文件的存放位置
                "<a href='#' onclick='native.showInFolder(\"{}\")'>{}</a>".format(
                    cert_file,
                    _("Open the location of local certificate")
                ),
                # Step 3. 替换证书文件并重启桌面助手
                _("Please replace the certificate files and relaunch the Assistant")
            ]
        }


@diagnosis()
def diagnose_hostname_resolution():
    result = {'ok': True}
    try:
        ip = socket.gethostbyname(config.ADDRESS)
    except Exception:
        result['ok'] = False
    else:
        result['ok'] = ip == "127.0.0.1"

    if not result['ok']:
        result['title'] = _("Abnormal domain name resolution")
        result['instructions'] = [
            _("Please contact your network administrator to check DNS")
        ]
    return result


@diagnosis()
def diagnose_auto_upgrade():
    if not config.DISABLE_UPGRADE:
        return {"ok": True}
    return {
        "ok": False,
        "title": _("Can't auto-upgrading"),
        "instructions": [_("The assistant auto-upgrade was disabled")]
    }


def run_diagnose():
    '''运行所有可用的诊断逻辑，并返回报告内容（HTML片段）
    诊断逻辑返回格式是 {ok, title, instructions}
    '''
    report = []

    for func in diagnosis_items:
        result = func()
        if not result['ok']:
            report.append(result)
    return report


def get_version_info():
    """得到桌面助手的版本号，构建时间以及 ID"""
    commit_id = local_date = ""
    version = "{}.{}".format(config.VERSION, config.BUILD_NUMBER)
    app_id = config.APP_ID[:6]
    pattern = re.compile(r"^(.*?) @ {(.*?) \+0(\d)00}.*?$")
    matched = pattern.search(config.GIT_INFO)
    if matched:
        commit_id, time, timezone = matched.groups()
        if int(timezone) == 0:
            try:
                date = parser.parse(time).replace(tzinfo=tz.tzutc())
                local_date = date.astimezone(tz.tzlocal()).strftime("%Y-%m-%d %H:%M:%S")  # noqa E501
            except Exception:
                local_date = ""
        else:
            local_date = time

    return u"{version_str}: {version}; {build_str}: {build_time}; ID: {app_id}; Commit: {git_info}".format(  # noqa E501
        version_str=_("Version"), version=version,
        build_str=_("Build on"), build_time=local_date, app_id=app_id,
        git_info=commit_id
    )


show_webview_window(
    title=_("Assistant troubleshooting"),  # 桌面助手疑难解答
    size=(680, 400),
    body=render_template_file(
        "troubleshooting.html",
        title=_("Problem diagnosis"),
        diagnosis=run_diagnose(),
        version_info=get_version_info(),
        viewlog=_('View log'),
    ),
    resizable=False,
    minbutton=False,
    maxbutton=False,
)
