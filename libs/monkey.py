# coding: utf-8
'''动态修补第三方库
注意：
  - 数据文件路径相关的修补，一般仅适用于打包之后的运行环境（FROZEN 状态）
'''
import os
import shutil
import sys

import requests
import certifi
import pyoauth2
import babel

from config import VERSION, BUILD_NUMBER, GIT_INFO, CURRENT_DIR, APP_DATA

# Detect whether we're in bundled executable state
FROZEN = getattr(sys, 'frozen', False)
UA = 'Assistant v{version}.{build} ({platform}) / {git}'.format(
    version=VERSION, build=BUILD_NUMBER,
    platform=sys.platform, git=GIT_INFO
)
__all__ = ['patch_all']

certifi_folder = os.path.join(APP_DATA, 'certifi')
certifi_files = {"cacert.pem", "assistant.key", "assistant.crt"}
if not os.path.isdir(certifi_folder):
    os.makedirs(certifi_folder)
for f in certifi_files - set(os.listdir(certifi_folder)):
    # 检查 edo_assistent/certifi 目录是否有证书文件，没有则从安装目录下拷贝
    if f == 'cacert.pem':
        old_cacert = os.path.join(APP_DATA, f)
        if not os.path.isfile(old_cacert):
            shutil.copy2(
                os.path.join(CURRENT_DIR, 'certifi', f) if FROZEN else certifi.where(),
                certifi_folder
            )
        else:
            # 将旧版本放在 edo_assistan 目录下的 cacert.pem 文件移动到 certifi 目录
            shutil.move(old_cacert, certifi_folder)
    else:
        shutil.copy2(
            os.path.join(CURRENT_DIR, 'certifi', f), certifi_folder
        )


def _requests_certs_where():
    '''修补 requests 和 certifi 中 where() 函数
    替换：
      - <fn> certifi.where
      - <fn> requests.certs.where
      - <value> requests.adapters.DEFAULT_CA_BUNDLE_PATH
      - <value> requests.utils.DEFAULT_CA_BUNDLE_PATH
    内容：
      - 修正返回的数据文件路径
    '''
    return os.path.join(certifi_folder, 'cacert.pem')


def _requests_api_request(method, url, **kwargs):
    '''修补 requests
    替换：
      - <fn> requests.api.request
      - <fn> requests.request
    内容：
      - 关闭 SSL 验证；
      - 修改默认请求头的 User-Agent；
      - 修改默认出错重试次数为 5（通过挂载自定义的 HTTPAdapter）；
    '''
    kwargs.update({'verify': False})
    if not isinstance(kwargs.get('headers', None), (dict, )):
        kwargs['headers'] = {}
    kwargs['headers'].update({
        'User-Agent': UA,
    })
    session = requests.sessions.Session()

    # 对内部地址不使用代理，也不要从环境变量中自动读取系统代理
    if url.startswith('http://127.0.0.1') or url.startswith('https://127.0.0.1'):
        session.trust_env = False
        kwargs['proxies'] = None

    session.mount('https://', requests.adapters.HTTPAdapter(max_retries=5))
    session.mount('http://', requests.adapters.HTTPAdapter(max_retries=5))
    return session.request(method=method, url=url, **kwargs)


def _pyoauth2_libs_response_response_init(self, response, **opts):
    '''修补 pyoauth2
    替换：
      - <fn> pyoauth2.libs.response.Response.__init__
    内容：
      - 修复通过 OAuth 链接（通过跳转）下载文件时，因为尝试进行文本解码而导致占用巨量内存的问题；
    '''
    self.response = self.resp = response
    self.status_code = self.status = response.status_code
    self.reason = response.reason
    self.content_type = response.headers.get('content-type')

    options = {'parse': 'text'}
    options.update(opts)
    if options['parse'] in ('text', 'query', 'json', ):
        self.body = response.text
    self.options = options


def patch_all(patch_ssl=True, load_certs=False):
    '''修补第三方库的总入口
    '''
    # 1. requests
    if FROZEN:
        if not os.path.isfile(certifi.where()):
            certifi.where = _requests_certs_where
        if not os.path.isfile(requests.certs.where()):
            requests.certs.where = _requests_certs_where
        requests.adapters.DEFAULT_CA_BUNDLE_PATH = requests.utils.DEFAULT_CA_BUNDLE_PATH = certifi.where()

    requests.adapters.DEFAULT_RETRIES = 5
    requests.api.request = _requests_api_request
    requests.request = _requests_api_request

    # 2. pyoauth2
    pyoauth2.libs.response.Response.__init__ = _pyoauth2_libs_response_response_init

