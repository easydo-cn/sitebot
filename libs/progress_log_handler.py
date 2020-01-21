# coding: utf-8
import os
import sys
from logging import Handler


class ProgressLogHandler(Handler):
    def __init__(self, wo_client, progress_script, script_title,  progress_params):
        Handler.__init__(self)
        self.wo_client = wo_client
        self.progress_script = progress_script
        self.progress_params = progress_params
        self.script_title = script_title

    def emit(self, record):
        self.wo_client.xapi(
            self.progress_script,
            script_title = self.script_title,
            uid=self.progress_params['uid'],
            message=record.getMessage(),
            uids=self.progress_params['uid']
        )


class StdoutCollector(object):
    def __init__(self, out=sys.stdout, echo=False, worker_db=None,
                 message_client=None, progress_params=None):
        '''
        Args:
        - with `echo`, we write to original sys.stdout as well;
        '''
        self.__io = []
        self.original = out
        self.encoding = 'utf-8'
        self.echo = echo
        self.worker_db = worker_db
        self.message_client = message_client
        self.progress_params = progress_params

    @property
    def buffer(self):
        return self

    def write(self, x):
        if not isinstance(x, basestring):
            x = str(x)

        if isinstance(x, str):
            try:
                x = x.decode(self.encoding).strip()
            except:
                x = x.decode(self.original.encoding).strip()

        # if in echo mode, send each printed text as a separate message
        if self.progress_params and x:
            text = u'{worker_title}: {text}'.format(
                worker_title=unicode(self.worker_db.get('title', '执行中的任务')),
                text=x,
            )
            for group in self.progress_params.get('uid', []):
                try:
                    self.message_client.message_v2.trigger_group_event(
                        group,
                        event_name='chat',
                        event_type='transient',  # 这个函数只处理 `print` 语句的输出，这些消息不需要储存
                        event_data={
                            'from': {
                                'id': self.worker_db['pid'],
                                'name': self.worker_db['username'],
                            },
                            'body': text,
                        }
                    )
                except:
                    pass
            return

        # We store <unicode> internally
        self.__io.extend([l for l in x.split('\n') if l])  # 去除完全的空行。但是如果某一行里有空白字符，会保留

        if self.echo:
            self.original.write(x.encode(self.original.encoding))

    def flush(self):
        return len(self.__io)

    def seek(self, cursor, mode=os.SEEK_SET):
        '''
        Notice: currently this method has no effect.
        '''
        return

    def read(self, size=-1):
        '''
        Notice:
        - this will always return unicode;
        '''
        if size == -1:  # read 时 size=-1 有 特殊含义，代表“读取所有内容”
            size = None
        return u'\n'.join(self.__io)[:size]

