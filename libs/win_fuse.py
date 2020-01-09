# -*- coding: utf-8 -*-
import logging
import os
import sys
import errno
# import time
from errno import ENOENT, EROFS
import platform
from stat import S_IFDIR, S_IFREG
from time import time, strptime, mktime
from datetime import datetime, tzinfo, timedelta

from fuse import FUSE, FuseOSError, Operations
from edo_client import get_client
from cachetools import TTLCache


def get_logger(name, level=logging.WARN):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    shandler = logging.StreamHandler()
    shandler.setLevel(level)

    logger.addHandler(shandler)
    return logger


logger = get_logger(__name__, level=logging.DEBUG)

ZERO_TIME_DELTA = timedelta(0)
LOCAL_TIME_DELTA = timedelta(hours=8)


class UTC(tzinfo):
    "格林威治时间的tzinfo类"
    def utcoffset(self, dt):
        "返回夏令时的时差调整"
        return ZERO_TIME_DELTA

    def dst(self, dt):
        return ZERO_TIME_DELTA


class LocalTimezone(tzinfo):
    "北京时间的tzinfo类"
    def utcoffset(self, dt):
        return LOCAL_TIME_DELTA

    def dst(self, dt):
        return ZERO_TIME_DELTA

    def tzname(self, dt):
        return '+08:00'


class RemoteDirectory(Operations):

    def __init__(self, remote_path, wo_client):
        self.remote_path = remote_path
        self.wo_client = wo_client
        # self.files = wo_client.content.items(path=self.remote_path)
        self._files = []
        self._lastpath = None
        self.ls_cache = TTLCache(5000, 60*1)
        self.stat_cache = TTLCache(self.ls_cache.maxsize, self.ls_cache.ttl)

    def _full_path(self, partial):
        partial = partial[1:] if partial.startswith('/') else partial
        path = os.path.join(self.remote_path, partial).replace(os.path.sep, '/')
        logger.debug(u'Remote root: %s, partial path: %s, full path joined: %s', self.remote_path, partial, path)
        path = path[:-1] if path.endswith('/') else path
        logger.debug(u'Full path: %s => %s', partial, path)

        return path

    def _getfiles(self, path):
        if self._lastpath == path and len(self._files):
            return self._files
        logger.debug(u'Local cache missed, fetching with online API')
        self._lastpath = path
        self._files = self.wo_client.content.items(path)
        if len(self._files) == 0:
            self._files = self.wo_client.content.properties(path)
        return self._files

    # def getfile(self, path):
    #     files = filter(lambda f: f['path'] == path, self._files)
    #     return files[0] if files else None

    def _timetranform(self, file, field):
        time_str = file[field]
        local_time = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC()).astimezone(LocalTimezone())
        # logger.debug(u'Local time is %s', local_time)
        # 转换为时间戳:
        time_stamp = int(mktime(local_time.timetuple()))#* 10000000 + 11644473600L*10000000

        # dt = datetime.strptime(time_str, '%Y-%m-%dT%H:%M:%S.%fZ')
        # timestamp = int(dt.strftime('%s'))

        return time_stamp

    # 访问文件或文件夹
    # def access(self, path, amode):
    #     return 0

    # 修改权限
    # def chmod(self, path, mode):
    #     raise FuseOSError(EROFS)

    #
    # def chown(self, path, uid, gid):
    #     raise FuseOSError(EROFS)

    # def create(self, path, mode, fi=None):
    #     '''
    #     When raw_fi is False (default case), fi is None and create should
    #     return a numerical file handle.

    #     When raw_fi is True the file handle should be set directly by create
    #     and return 0.
    #     '''
    #     raise FuseOSError(EROFS)

    # def destroy(self, path):
    #     'Called on filesystem destruction. Path is always /'
    #     # pass

    # def flush(self, path, fh):
    #     return 0

    # def fsync(self, path, datasync, fh):
    #     return 0

    # def fsyncdir(self, path, datasync, fh):
    #     return 0

    # def getxattr(self, path, name, position=0):
    #     raise FuseOSError(ENOTSUP)

    # def link(self, target, source):
    #     'creates a hard link `target -> source` (e.g. ln source target)'
    #     raise FuseOSError(EROFS)

    # def listxattr(self, path):
    #     return []

    # lock = None

    # def mkdir(self, path, mode):
    #     raise FuseOSError(EROFS)

    # def mknod(self, path, mode, dev):
    #     raise FuseOSError(EROFS)

    # def open(self, path, flags):
    #     '''
    #     When raw_fi is False (default case), open should return a numerical
    #     file handle.

    #     When raw_fi is True the signature of open becomes:
    #         open(self, path, fi)

    #     and the file handle should be set directly.
    #     '''
    #     return 0

    def opendir(self, path):
        'Returns a numerical file handle.'
        logger.debug(u'Opening directory %s', path)
        return 0

    # def read(self, path, size, offset, fh):
    #     'Returns a string containing the data requested.'
    #     raise FuseOSError(EIO)

    # def readlink(self, path):
    #     raise FuseOSError(ENOENT)

    # def release(self, path, fh):
    #     return 0

    def releasedir(self, path, fh):
        logger.debug(u'Release directory %s with `fh` of %s', path, fh)
        return 0

    # def removexattr(self, path, name):
    #     raise FuseOSError(ENOTSUP)

    # def rename(self, old, new):
    #     raise FuseOSError(EROFS)

    # def rmdir(self, path):
    #     full_path = self._full_path(path)
    #     logger.debug(u'Removing %s dictionary', full_path)
    #     self.wo_client.content.delete(full_path)
    #     if self.wo_client.content.items(full_path):
    #         raise FuseOSError(EROFS)

    # def setxattr(self, path, name, value, options, position=0):
    #     raise FuseOSError(ENOTSUP)

    # def statfs(self, path):
    #     '''
    #     Returns a dictionary with keys identical to the statvfs C structure of
    #     statvfs(3).

    #     On Mac OS X f_bsize and f_frsize must be a power of 2
    #     (minimum 512).
    #     '''

    #     return {}

    # def symlink(self, target, source):
    #     'creates a symlink `target -> source` (e.g. ln -s source target)'
    #     raise FuseOSError(EROFS)

    # def truncate(self, path, length, fh=None):
    #     raise FuseOSError(EROFS)

    # def unlink(self, path):
    #     raise FuseOSError(EROFS)

    # def utimens(self, path, times=None):
    #     'Times is a (atime, mtime) tuple. If None use current time.'
    #     return 0

    # def write(self, path, data, offset, fh):
    #     raise FuseOSError(EROFS)

    # 获取并设置文件或文件夹属性
    def getattr(self, path, fh):
        logger.debug('Getting attr of %s with `fh` of %s', path, fh)

        if path == '/':
            st = dict(st_mode=(S_IFDIR | 0o777), st_nlink=1)
        else:
            path = self._full_path(path)
            fitem = self.stat_cache.get(path.lower(), None)

            if fitem is None:
                logger.debug(u'Path %s does not match any file, assume no such file', path)
                raise FuseOSError(ENOENT)

            if 'File' in fitem['object_types']:
                st = dict(st_mode=(S_IFREG | 0o777), st_nlink=0)
                st['st_ctime'] = self._timetranform(fitem, 'created')
                st['st_birthtime'] = self._timetranform(fitem, 'created')
                st['st_mtime'] = self._timetranform(fitem, 'modified')
                st['st_atime'] = self._timetranform(fitem, 'modified')
                st['st_size'] = long(fitem['bytes'])
            elif 'Folder' in fitem['object_types']:
                st = dict(st_mode=(S_IFDIR | 0o777), st_nlink=1)
                st['st_ctime'] = self._timetranform(fitem, 'created')
                st['st_birthtime'] = self._timetranform(fitem, 'created')
                st['st_mtime'] = self._timetranform(fitem, 'modified')
                st['st_atime'] = self._timetranform(fitem, 'modified')
                # st['st_size'] = 0
            else:
                raise FuseOSError(ENOENT)

        st.update({
            'st_uid': 0,
            'st_git': 0,
            'st_ino': 0L,
            'st_dev': 0L,
        })
        return st

    def _listdir(self, fpath):
        files = self.ls_cache.get(fpath.lower(), None)
        fpath_lower = fpath.lower()
        if not files:
            if fpath_lower in self.stat_cache:
                fitems = self.wo_client.content.items(path=self.stat_cache[fpath.lower()]['path'])
            else:
                if fpath_lower == self.remote_path.lower():
                    fitems = self.wo_client.content.items(path=self.remote_path)
                else:
                    logger.warn(u'Failed to listdir %s', fpath)
                    return []

            self.ls_cache[fpath.lower()] = [f.get('title', f['name']) for f in fitems]
            [self.stat_cache.update({f['path'].lower(): f}) for f in fitems]
            files = self.ls_cache[fpath.lower()]

        return files

    # 进入文件夹
    def readdir(self, path, fh):
        logger.debug(u'Reading directory %s with `fh` of %s', path, fh)
        dirents = ['.', '..']
        full_path = self._full_path(path)
        dirents.extend(self._listdir(full_path))
        logger.debug(u'Content of directory %s: %s', path, dirents)
        for r in dirents:
            yield r


def main(mountpoint, remotepath, token):
    wo_client = get_client(
        'workonline', oc_api='https://oc-api.beta.easydo.cn',
        account='zopen', instance='default',
        token=token
    )
    # remote_path = u'prodev/product_docs/易工作'
    remote_path = remotepath
    logger.debug(u'%s will be mounted at %s', remote_path, mountpoint)
    FUSE(
        RemoteDirectory(remote_path, wo_client),
        mountpoint,
        nothreads=True,
        foreground=True,
        volname="test"
    )


if __name__ == '__main__':
    if platform.system() == 'Darwin':
        mountpoint = os.path.expanduser('~/FUSE/test')
    elif platform.system() == 'Windows':
        mountpoint = sys.argv[1] or 'D:\\mount'
    else:
        raise NotImplementedError('Unsupported platform')
    main(mountpoint, sys.argv[2], sys.argv[-1])
