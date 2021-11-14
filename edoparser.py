# -*- coding: utf-8 -*-

import argparse
from datetime import datetime
import importlib
import inspect
import os
import pkgutil
import sys
import traceback

from config import (
    VERSION, BUILD_NUMBER, WORKERS,
)
from utils import translate as _
import worker
import workers


class EdoArgParser(argparse.ArgumentParser):
    '''
    An argument parser.
    '''
    def error(self, message):
        print >> sys.stderr, u'Error: {}'.format(message)
        self.print_help()
        return

subcommands = []



def parse_args(args):
    """start parse the args
    Return:
        start_server <bool>
        silent_mode <bool>
    """

    # Main parser
    parser = EdoArgParser(
        description=_('Assistant commandline mode'),
        epilog=u'(c) 2014-2016 Everydo.cn',
        version=u'{}.{}'.format(VERSION, BUILD_NUMBER)
    )

    # Pass in token for remote API calling
    parser.add_argument(
        '-token', '--ast-token',
        help='set the token for remotely accessing APIs',
        action='store'
    )

    # start to parse args
    parsed, remained = parser.parse_known_args(args)

    if parsed.ast_token:
        os.environ['APP_TOKEN'] = parsed.ast_token

    return True, quiet


