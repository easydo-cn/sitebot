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
                message=record.getMessage(),
                **self.progress_params
            )
        except Exception:
            self.logger.exception('进度回调失败！')

