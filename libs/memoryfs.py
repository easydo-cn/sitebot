#!/usr/bin/env python
# _*_ coding:utf-8 _*_

from fs.memoryfs import MemoryFS


class CustomMemoryFS(MemoryFS):

    def __init__(self, file_factory=None):
        super(CustomMemoryFS, self).__init__(file_factory)

    def isdir(self, path):
        path = path.lower()
        return super(CustomMemoryFS, self).isdir(path)

    def isfile(self, path):
        path = path.lower()
        return super(CustomMemoryFS, self).isfile(path)

    def exists(self, path):
        path = path.lower()
        return super(CustomMemoryFS, self).exists(path)

    def makedir(self, dirname, recursive=False, allow_recreate=False):
        dirname = dirname.lower()
        return super(CustomMemoryFS, self).makedir(dirname, recursive, allow_recreate)

    def open(self, path, mode='r', buffering=-1, encoding=None, errors=None, newline=None, line_buffering=False, **kwargs):
        path = path.lower()
        return super(CustomMemoryFS, self).open(
            path, mode, buffering, encoding, errors, newline,
            line_buffering, **kwargs
        )

    def remove(self, path):
        path = path.lower()
        return super(CustomMemoryFS, self).remove(path)

    def removedir(self, path, recursive=False, force=False):
        path = path.lower()
        return super(CustomMemoryFS, self).removedir(path, recursive, force)

    def rename(self, src, dst):
        src = src.lower()
        dst = dst.lower()
        return super(CustomMemoryFS, self).rename(src, dst)

    def settimes(self, path, accessed_time=None, modified_time=None):
        path = path.lower()
        return super(CustomMemoryFS, self).settimes(path, accessed_time, modified_time)

    def listdir(self, path='/', wildcard=None, full=False, absolute=False, dirs_only=False, files_only=False):
        path = path.lower()
        return super(CustomMemoryFS, self).listdir(path, wildcard, full, absolute, dirs_only, files_only)

    def getinfo(self, path):
        path = path.lower()
        return super(CustomMemoryFS, self).getinfo(path)

    def copydir(self, src, dst, overwrite=False, ignore_errors=False, chunk_size=1024*64):
        src = src.lower()
        dst = dst.lower()
        return super(CustomMemoryFS, self).copydir(src, dst, overwrite, ignore_errors, chunk_size)

    def movedir(self, src, dst, overwrite=False, ignore_errors=False, chunk_size=1024*64):
        src = src.lower()
        dst = dst.lower()
        return super(CustomMemoryFS, self).movedir(src, dst, overwrite, ignore_errors, chunk_size)

    def copy(self, src, dst, overwrite=False, chunk_size=1024*64):
        src = src.lower()
        dst = dst.lower()
        return super(CustomMemoryFS, self).copy(src, dst, overwrite, chunk_size)

    def move(self, src, dst, overwrite=False, chunk_size=1024*64):
        src = src.lower()
        dst = dst.lower()
        return super(CustomMemoryFS, self).move(src, dst, overwrite, chunk_size)

    def getcontents(self, path, mode='rb', encoding=None, errors=None, newline=None):
        path = path.lower()
        return super(CustomMemoryFS, self).getcontents(path, mode, encoding, errors, newline)

    def setcontents(self, path, data=b'', encoding=None, errors=None, chunk_size=1024*64):
        path = path.lower()
        return super(CustomMemoryFS, self).setcontents(path, data, encoding, errors, chunk_size)

    def setxattr(self, path, key, value):
        path = path.lower()
        return super(CustomMemoryFS, self).setxattr(path, key, value)

    def getxattr(self, path, key, default=None):
        path = path.lower()
        return super(CustomMemoryFS, self).getxattr(path, key, default)

    def delxattr(self, path, key):
        path = path.lower()
        return super(CustomMemoryFS, self).delxattr(path, key)

    def listxattrs(self, path):
        path = path.lower()
        return super(CustomMemoryFS, self).listxattrs(path)
