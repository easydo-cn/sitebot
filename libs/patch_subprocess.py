# coding: utf-8
# the following code is excerpted from
# https://gist.github.com/vaab/2ad7051fc193167f15f85ef573e54eb9
# This patched `Popen` class
# adds support for Unicode commandline on Windows platform.

# issue: https://bugs.python.org/issue19264

import os
import ctypes
import subprocess
import _subprocess
from ctypes import (
    byref, windll, c_char_p, c_wchar_p, c_void_p,
    Structure, sizeof, c_wchar, WinError,
)
from ctypes.wintypes import BYTE, WORD, LPWSTR, BOOL, DWORD, LPVOID, HANDLE
import pywintypes
import sys

#
# Types
#

CREATE_UNICODE_ENVIRONMENT = 0x00000400
LPCTSTR = c_char_p
LPTSTR = c_wchar_p
LPSECURITY_ATTRIBUTES = c_void_p
LPBYTE = ctypes.POINTER(BYTE)


def ensure_unicode(s):
    '''Simple (might be buggy) function to turn given value into a unicode'''
    if isinstance(s, unicode):
        return s
    else:
        try:
            return s.decode(sys.getfilesystemencoding())
        except:
            return s.decode('utf-8')


class STARTUPINFOW(Structure):
    _fields_ = [
        ("cb",              DWORD),  ("lpReserved",    LPWSTR),
        ("lpDesktop",       LPWSTR), ("lpTitle",       LPWSTR),
        ("dwX",             DWORD),  ("dwY",           DWORD),
        ("dwXSize",         DWORD),  ("dwYSize",       DWORD),
        ("dwXCountChars",   DWORD),  ("dwYCountChars", DWORD),
        ("dwFillAtrribute", DWORD),  ("dwFlags",       DWORD),
        ("wShowWindow",     WORD),   ("cbReserved2",   WORD),
        ("lpReserved2",     LPBYTE), ("hStdInput",     HANDLE),
        ("hStdOutput",      HANDLE), ("hStdError",     HANDLE),
    ]

LPSTARTUPINFOW = ctypes.POINTER(STARTUPINFOW)


class PROCESS_INFORMATION(Structure):
    _fields_ = [
        ("hProcess",         HANDLE), ("hThread",          HANDLE),
        ("dwProcessId",      DWORD),  ("dwThreadId",       DWORD),
    ]

LPPROCESS_INFORMATION = ctypes.POINTER(PROCESS_INFORMATION)


class DUMMY_HANDLE(ctypes.c_void_p):

    def __init__(self, *a, **kw):
        super(DUMMY_HANDLE, self).__init__(*a, **kw)
        self.closed = False

    def Close(self):
        if not self.closed:
            windll.kernel32.CloseHandle(self)
            self.closed = True

    def __int__(self):
        return self.value


CreateProcessW = windll.kernel32.CreateProcessW
CreateProcessW.argtypes = [
    LPCTSTR, LPTSTR, LPSECURITY_ATTRIBUTES,
    LPSECURITY_ATTRIBUTES, BOOL, DWORD, LPVOID, LPCTSTR,
    LPSTARTUPINFOW, LPPROCESS_INFORMATION,
]
CreateProcessW.restype = BOOL


#
# Patched functions/classes
#

def CreateProcess(executable, args, _p_attr, _t_attr,
                  inherit_handles, creation_flags, env, cwd,
                  startup_info):
    """Create a process supporting unicode executable and args for win32

    Python implementation of CreateProcess using CreateProcessW for Win32

    """

    if startup_info is None:
        startup_info = subprocess.STARTUPINFO()

    si = STARTUPINFOW(
        dwFlags=startup_info.dwFlags,
        wShowWindow=startup_info.wShowWindow,
        cb=sizeof(STARTUPINFOW),
        # XXXvlab: not sure of the casting here to ints.
        hStdInput=int(startup_info.hStdInput) if startup_info.hStdInput else None,
        hStdOutput=int(startup_info.hStdOutput) if startup_info.hStdOutput else None,
        hStdError=int(startup_info.hStdError) if startup_info.hStdError else None,
    )

    wenv = None
    if env is not None:
        # LPCWSTR seems to be c_wchar_p, so let's say CWSTR is c_wchar
        env = (unicode("").join([
            unicode("%s=%s\0") % (k, v)
            for k, v in env.items()])) + unicode("\0")
        wenv = (c_wchar * len(env))()
        wenv.value = env

    pi = PROCESS_INFORMATION()
    creation_flags |= CREATE_UNICODE_ENVIRONMENT

    if CreateProcessW(executable, args, None, None,
                      inherit_handles, creation_flags,
                      wenv, cwd, byref(si), byref(pi)):
        return (DUMMY_HANDLE(pi.hProcess), DUMMY_HANDLE(pi.hThread),
                pi.dwProcessId, pi.dwThreadId)
    raise WinError()


class Popen(subprocess.Popen):
    """This superseeds Popen and corrects a bug in cPython 2.7 implem"""

    def _execute_child(self, args, executable, preexec_fn, close_fds,
                       cwd, env, universal_newlines,
                       startupinfo, creationflags, shell, to_close,
                       p2cread, p2cwrite,
                       c2pread, c2pwrite,
                       errread, errwrite):
        """Code from part of _execute_child from Python 2.7 (9fbb65e)

        There are only 2 little changes concerning the construction of
        the the final string in shell mode: we preempt the creation of
        the command string when shell is True, because original function
        will try to encode unicode args which we want to avoid to be able to
        sending it as-is to ``CreateProcess``.

        """
        if not isinstance(args, subprocess.types.StringTypes):
            args = subprocess.list2cmdline(
                [ensure_unicode(_arg) for _arg in args]
            )
        else:
            args = ensure_unicode(args)

        if startupinfo is None:
            startupinfo = subprocess.STARTUPINFO()
        if shell:
            startupinfo.dwFlags |= _subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = _subprocess.SW_HIDE
            comspec = os.environ.get("COMSPEC", unicode("cmd.exe"))
            args = unicode('{} /c "{}"').format(comspec, args)
            if (_subprocess.GetVersion() >= 0x80000000 or
                    os.path.basename(comspec).lower() == "command.com"):
                w9xpopen = self._find_w9xpopen()
                args = unicode('"%s" %s') % (w9xpopen, args)
                creationflags |= _subprocess.CREATE_NEW_CONSOLE

        if None not in (p2cread, c2pwrite, errwrite):
            startupinfo.dwFlags |= _subprocess.STARTF_USESTDHANDLES
            startupinfo.hStdInput = p2cread
            startupinfo.hStdOutput = c2pwrite
            startupinfo.hStdError = errwrite

        def _close_in_parent(fd):
            fd.Close()
            to_close.remove(fd)

        # Start the process
        try:
            hp, ht, pid, tid = CreateProcess(
                executable, args,
                # no special security
                None, None,
                int(not close_fds),
                creationflags,
                env,
                cwd,
                startupinfo
            )
        except pywintypes.error as e:
            # Translate pywintypes.error to WindowsError, which is
            # a subclass of OSError.  FIXME: We should really
            # translate errno using _sys_errlist (or similar), but
            # how can this be done from Python?
            raise WindowsError(*e.args)
        finally:
            # Child is launched. Close the parent's copy of those pipe
            # handles that only the child should have open.  You need
            # to make sure that no handles to the write end of the
            # output pipe are maintained in this process or else the
            # pipe will not close when the child process exits and the
            # ReadFile will hang.
            if p2cread is not None:
                _close_in_parent(p2cread)
            if c2pwrite is not None:
                _close_in_parent(c2pwrite)
            if errwrite is not None:
                _close_in_parent(errwrite)

        # Retain the process handle, but close the thread handle
        self._child_created = True
        self._handle = hp
        self.pid = pid
        ht.Close()
