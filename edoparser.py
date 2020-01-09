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
    VERSION, BUILD_NUMBER, SINGLE_PROCESS_KEY, WORKERS,
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


def add_subcommands_to_parser(parser):
    """add the subcommands to the parser
        param:
            --parser inherited from the root parser
    """
    sub_parsers = parser.add_subparsers(
        title=_('Subcommands'),
        dest='subcommand'
    )

    subcommands = []
    # Add subparser for each worker
    MULTI_VALUE_PARAMS = ('uid', 'path', 'server_path', )
    for _worker in WORKERS:
        subcommands.append(_worker)
        _module_name = '{}.{}'.format(workers.__name__, _worker)
        importlib.import_module(_module_name)
        # _module = sys.modules[_module_name]
        _parser = sub_parsers.add_parser(
            _worker,
            description=worker.get_worker_title(_worker)
        )

        # Get a list of arguments by inspecting this worker
        ignores = ('pipe', )
        argspec = inspect.getargspec(worker.WORKER_REG[_worker]['function'])
        try:
            defaults = list(argspec.defaults) or []
        except:
            defaults = []
        devide = len(argspec.args) - len(defaults)
        args = list(argspec.args[1:devide])
        kwargs = list(argspec.args[devide:])

        # Add each arguments
        for arg in args:
            nargs = '+' if arg in MULTI_VALUE_PARAMS else None
            _parser.add_argument(
                '--{}'.format(arg),
                action='store',
                nargs=nargs
            )
        for kwarg in kwargs:
            if kwarg in ignores:
                continue
            nargs = '+' if kwarg in MULTI_VALUE_PARAMS else None
            _parser.add_argument(
                '--{}'.format(kwarg),
                action='store',
                default=defaults[kwargs.index(kwarg)],
                nargs=nargs
            )

    return subcommands


def parse_args(args, fake=False):
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

    # Silent mode: start without UI
    parser.add_argument('-q', '--quiet',
                        help="start assistant in silent mode",
                        action='store_true')
    # Pass in token for remote API calling
    parser.add_argument(
        '-token', '--ast-token',
        help='set the token for remotely accessing APIs',
        action='store'
    )

    # start to parse args
    parsed, remained = parser.parse_known_args(args)

    # parse arguments
    quiet = parsed.quiet
    if quiet:
        # update the environment key
        from config import QUIET_MODE_KEY
        os.environ[QUIET_MODE_KEY] = '1'
    if parsed.ast_token:
        os.environ['APP_TOKEN'] = parsed.ast_token

    if len(remained):
        # add subcommand
        subcommands = add_subcommands_to_parser(parser)
        parsed, remained = parser.parse_known_args(remained)

        # parse subcommand
        for _subcommand in subcommands:
            if getattr(parsed, 'subcommand', None) == _subcommand:
                worker_args = parsed.__dict__.copy()
                for k in worker_args.keys():
                    if isinstance(worker_args[k], str):
                        worker_args[k] = unicode(
                            worker_args[k].decode(sys.getfilesystemencoding())
                        )
                worker_name = worker_args.pop('subcommand')
                print(u"单独运行 %s 任务" % worker_name)
                worker_id = worker.new_worker(worker_name, **worker_args)
                os.environ.update({SINGLE_PROCESS_KEY: '1'})
                worker.start_worker(worker_id, sync=True)
                return False, False

    # debug
    if fake:
        print(args)
        print(parsed)
        sys.exit(1)

    # must return the quiet argument to the parent
    return True, quiet


def except_debug(type, value, tb):
    '''
    Setup pdb breakpoint for debug purpose
    '''
    print (
        u"{0} - Uncaught exception: {1}\n{2}".format(
            datetime.strftime(datetime.now(), '%H:%M:%S'),
            str(value), ''.join(traceback.format_tb(tb))
        )
    )
    print(u'Entering debug tool...')
    if 'pdb' not in locals():
        import pdb
    pdb.set_trace()

if __name__ == '__main__':
    '''
    Test
    '''
    sys.excepthook = except_debug
    parse_args(sys.argv[1:], fake=True)
