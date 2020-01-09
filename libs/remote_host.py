# coding: utf-8
import io
import os
import tempfile

from edo_client import get_client
from fabric2 import Connection
from fabric2.transfer import Transfer
from invoke.exceptions import UnexpectedExit


class RemoteHost(Connection):
    """
    继承自fabric2的Connection
    wo_client:工作平台客户端
    host:远程主机
    platform:远程主机操作系统
    logger:记录日志的日志管理器
    """
    def __init__(self, wo_client, host, platform, logger, user=None, port=None, forward_agent=None,
                 connect_timeout=None, connect_kwargs=None, inline_ssh_env=None,):
        super(RemoteHost, self).__init__(
            host=host,
            user=user,
            port=port,
            config=None,
            gateway=None,
            forward_agent=forward_agent,
            connect_timeout=connect_timeout,
            connect_kwargs=connect_kwargs,
            inline_ssh_env=inline_ssh_env,
        )
        self.wo_client = wo_client
        self.host = host
        self.platform = platform
        self.logger = logger

    # 统一windows和linux的run方法,记录每次run的日志
    def run(self, *command, **kwargs):
        """
        :param command: 执行的shell命令，使用逗号分隔多条命令
        :return: fabric2 run result
        """
        command = ' && '.join(command)
        try:
            if self.platform == 'win':
                command = r'cmd.exe /c "{}"'.format(command)
                result = self._run(self._remote_runner(), command, pty=True, hide=True, **kwargs)
            else:
                result = self._run(self._remote_runner(), command, hide=True, **kwargs)
            self.logger.info(result.command)
            self.logger.debug(result.stdout)
        except UnexpectedExit as e:
            self.logger.error(e.result.stderr)
            raise e
        return result

    def get(self, *args, **kwargs):
        try:
            result = Transfer(self).get(*args, **kwargs)
            self.logger.info('远端文件由{}下载至本地{}'.format(result.remote, result.local))
        except Exception as e:
            self.logger.error(e)
            raise e
        return result

    def put(self, *args, **kwargs):
        try:
            result = Transfer(self).put(*args, **kwargs)
            self.logger.info('本地文件由{}上传至远端{}'.format(result.local, result.remote))
        except Exception as e:
            self.logger.error(e)
            raise e
        return result

    def download(self, script_name, path, prefix=''):
        """
        从线上下载原脚本（可通过prefix增加脚本开头补充）到远程主机
        :param script_name: 下载的脚本名
        :param path: 下载脚本到远端的路径，路径不包含文件名则以脚本名命名
        :param prefix: 脚本开头需要补充的内容，例如编码、目录切换
        :return: put result
        # conn.download('zopen.xxx:xxx', '/var/dd/aaa.py', prefix=import os;os.chdir(xxx))
        """
        # 从线上下载脚本
        script = self.wo_client.content.download_shell_script(script_name)
        script_content = script['script']
        # 修改脚本写入本地文件
        prefix = u"# coding: utf-8\n{}".format(prefix)
        script_content = u'''{}\n\n{}'''.format(prefix, script_content)
        _fd, local_script_filepath = tempfile.mkstemp(suffix='.py')
        os.close(_fd)
        with io.open(local_script_filepath, 'w', encoding='utf-8') as lwf:
            lwf.write(script_content)
        # 检查是否指定脚本文件名，确定远端脚本的存放位置，没有文件名则从脚本名解析
        if '.py' in path.split('/')[-1]:
            remote_script_filepath = path
        else:
            remote_script_filename = script_name.split(':')[-1] + '.py'
            remote_script_filepath = path + '/' + remote_script_filename
        return self.put(local_script_filepath, remote_script_filepath)

    # 调用线上脚本
    def call(self, script_name, prefix='', *args, **kwargs):
        """
        在当前目录下调用线上脚本，配合with con.cd('path'): con.call('script_name')使用
        :param script_name: 线上脚本的名称
        :param prefix: 线上脚本开头需要补充的内容，默认添加编码
        :param args: 线上脚本需要的位置参数
        :param kwargs: 线上脚本需要的关键字参数
        :return: {'result': result, 'returned': returned}
        """
        # 1.从线上下载脚本
        script = self.wo_client.content.download_shell_script(script_name)
        script_content = script['script']
        script_args = script['args']

        # 2.修改脚本写入本地文件
        # 2.1 封装脚本获取返回值：有主函数的脚本视为直接执行的脚本，不需要返回值。
        # 没有主函数的脚本视为需要返回值的脚本。
        need_return = False
        if "if __name__ == '__main__':" not in script_content:
            need_return = True
            str_kwargs = []
            for key, value in kwargs.items():
                str_kwargs.append("{}={}".format(key, repr(value)))
            str_kwargs = ', '.join(str_kwargs)
            args = str(args)[1:-1]
            if args and not str_kwargs:
                main_args = args
            elif str_kwargs and not args:
                main_args = str_kwargs
            elif str_kwargs and args:
                main_args = args + ', ' + str_kwargs
            else:
                main_args = ''
            # 增加缩进
            script_content = ''.join(map(lambda line: '    ' + line + '\n', script_content.split('\n')))
            script_content = """
def main({script_args}):
{script_content}
    
if __name__ == '__main__':
     returned = main({main_args})
     print "==========returned=========="
     print returned""".format(script_args=script_args, script_content=script_content, main_args=main_args)
        prefix = u"# coding: utf-8\n{}".format(prefix)
        script_content = u'''{}\n{}'''.format(prefix, script_content)

        # 2.2 将修改内容写入临时本地文件
        _fd, local_script_filepath = tempfile.mkstemp(suffix='.py')
        os.close(_fd)
        with io.open(local_script_filepath, 'w', encoding='utf-8') as lwf:
            lwf.write(script_content)

        # 3. 将文件传送到远端的当前目录下
        remote_curdir = self.run('pwd').stdout.replace('\n','')
        script_name = script_name + '.py'
        self.put(local_script_filepath, remote_curdir + '/' + script_name)
        # 4. 在当前目录下执行远端文件, 返回输出结果
        returned = ''
        try:
            result = self.run('python {}'.format(script_name))
            if need_return:
                returned = result.stdout.split("==========returned==========")[1].replace('\n', '')
                result.stdout = result.stdout.split("==========returned==========")[0].replace('\n', '')
        except UnexpectedExit as e:
            self.logger.error(e.result.stderr)
            raise e
        return {'result': result, 'returned': returned}


def get_remote_host(host, platform, __worker_db=None, __logger=None, *args, **kwargs):
    # 只能够在联机脚本中使用
    worker_db = __worker_db
    wo_client = get_client(
        'workonline', worker_db['oc_server'], worker_db['account'], worker_db['instance'],
        token=worker_db['token']
    )
    return RemoteHost(wo_client=wo_client, host=host, platform=platform, logger=__logger, *args, **kwargs)


