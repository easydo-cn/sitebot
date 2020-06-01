from logging import Handler


class ProgressLogHandler(Handler):
    def __init__(self, wo_client, progress_script, script_title,  progress_params, logger):
        Handler.__init__(self)
        self.wo_client = wo_client
        self.progress_script = progress_script
        self.progress_params = progress_params
        self.script_title = script_title
        self.logger = logger

    def emit(self, record):
        try:
            self.wo_client.xapi(
                self.progress_script,
                script_title = self.script_title,
                uid=self.progress_params['uid'],
                message=record.getMessage(),
                uids=self.progress_params['uid']
            )
        except Exception:
            self.logger.exception('进度回调失败！')

