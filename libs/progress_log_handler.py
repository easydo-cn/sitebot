"""
/*
 * Copyright (c) 2019 EasyDo, Inc. <panjunyong@easydo.cn>
 *
 * This program is free software: you can use, redistribute, and/or modify
 * it under the terms of the GNU Affero General Public License, version 3
 * or later ("AGPL"), as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */
"""
# encoding: utf-8
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

