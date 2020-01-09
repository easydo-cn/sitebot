# -*- coding: utf-8 -*-

"""load the application list with mimetype

this module only available on windows. this module can load the
application list from the registry according to the file extention.

update:

show the default app for the file type:

1. find all the application under the HKEY_CLASSES_ROOT/*/OpenWithList
2. find the program under the .ext type
3. also search the open with list for the .ext


update:

.ext--
    -- OpenWithList
        -- exe
        -- program
    -- OpenWithProgids
        -- progids
    -- shell
        -- open
            -- command
        -- edit
            -- command
    -- progids

created:    2016/3/21
author: smileboywtu

"""

import logging

from _winreg import (
    QueryInfoKey, QueryValue, QueryValueEx, OpenKey, EnumKey, EnumValue, CreateKeyEx, SetValueEx,
    ExpandEnvironmentStrings, REG_SZ, KEY_SET_VALUE,
    HKEY_CLASSES_ROOT, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE
)

# set the logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(
        logging.Formatter('%(asctime)s -- %(filename)s:%(funcName)s:%(lineno)d - [%(levelname)s] : %(message)s')
)
consoleHandler.setLevel(logging.DEBUG)
logger.addHandler(consoleHandler)

# system direcory
def sys_dir(folder):
    """check if windows system dir"""
    import re

    system_dir = r'windows', r'program files', r'system32', r'system'

    for dir in system_dir:
        match = re.search(dir, folder, flags=re.IGNORECASE)
        if match:
            return True

    return False


def search_name(locals, prog_path):
    """use the prog_path to find out the program display name

    locals: -- (main_key, sub_key)
    prog_path: -- program execute path

    return prog_name

    """

    for local in locals:

        key = OpenKey(*local)
        key_no = QueryInfoKey(key)[0]

        for index in xrange(key_no):

            key_name = EnumKey(key, index)
            app_key = OpenKey(key, key_name)

            for value_name in ('InstallLocation', 'LocationRoot', 'UninstallString'):
                try:
                    value_data = QueryValueEx(app_key, value_name)[0]
                    if value_data and (value_data.startswith(prog_path) or prog_path.startswith(value_data)):
                        prog_name = QueryValueEx(app_key, 'DisplayName')[0]
                        return prog_name
                except WindowsError:
                    pass

    return None


def get_prog_name(progid):
    """use progid to find out the program name

    get this information from the uninstall list

    HKEY_LOCAL_MACHINE\SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall

    HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall

    HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall

    params:
        - program id in registry
    """
    command = get_prog_command(progid)

    prog_path = None

    if command:
        import re
        match = re.search(u'\"(.+)\\\\\w+\.exe\"\s',
                          command, flags=re.IGNORECASE)
        if match:
            prog_path = match.group(1)

    if not prog_path:
        return None

    # deep to any path to find the proper program

    locals = []

    main_key = HKEY_LOCAL_MACHINE
    sub_key = '\\'.join([
        'SOFTWARE', 'Wow6432Node', 'Microsoft', 'Windows',
        'CurrentVersion', 'Uninstall'
    ])
    locals.append((main_key, sub_key))

    main_key = HKEY_LOCAL_MACHINE
    sub_key = '\\'.join([
        'Software', 'Microsoft', 'Windows',
        'CurrentVersion', 'Uninstall'
    ])
    locals.append((main_key, sub_key))

    main_key = HKEY_CURRENT_USER
    sub_key = '\\'.join([
        'SOFTWARE', 'Microsoft', 'Windows',
        'CurrentVersion', 'Uninstall'
    ])
    locals.append((main_key, sub_key))

    return search_name(locals, prog_path)


def get_command(key, sub_key):
    """get the open and edit command under the key
    @params:
        key -- registry key
        sub_key -- sub key
    @return:
        command
    """
    command = None
    try:
        key = OpenKey(key, sub_key)
        command = QueryValueEx(key, None)[0]
    except OSError:
        pass
    return command


def get_exe_command(exe):
    """get the application run command

    @params:
        exe -- application
    @return:
        command -- command
    """
    sub_key = '\\'.join([
        'Applications', exe
    ])

    try:
        key = OpenKey(HKEY_CLASSES_ROOT, sub_key)
    except WindowsError:
        return None

    sub_key = '\\'.join([
        'shell', 'edit', 'command'
    ])
    edit_command = get_command(key, sub_key)
    sub_key = '\\'.join([
        'shell', 'open', 'command'
    ])
    open_command = get_command(key, sub_key)

    if not (open_command or edit_command):
        return None

    return edit_command or open_command


def get_prog_command(progid):
    """use the program id to find out program path

    params:
        progid - program id in registry
    """
    open_command = None
    edit_command = None

    sub_key = '\\'.join([
        progid, 'shell', 'edit', 'command'
    ])

    try:
        key = OpenKey(HKEY_CLASSES_ROOT, sub_key)
        edit_command = QueryValueEx(key, None)[0]
    except WindowsError:
        pass

    sub_key = '\\'.join([
        progid, 'shell', 'open', 'command'
    ])

    try:
        key = OpenKey(HKEY_CLASSES_ROOT, sub_key)
        open_command = QueryValueEx(key, None)[0]
    except WindowsError:
        pass

    if not (open_command or edit_command):
        return None

    return edit_command or open_command


def get_open_with_list(ext):
    """get open with list of an ext

    find all the exe programs and direct program

    @params:
        ext --  file extention

    """
    import re

    sub_key = '\\'.join([
        ext, 'OpenWithList'
    ])

    try:
        key = OpenKey(HKEY_CLASSES_ROOT, sub_key)
    except WindowsError:
        return None

    key_no = QueryInfoKey(key)[0]

    exes = []
    for index in xrange(key_no):
        exe = EnumKey(key, index)
        if exe.lower().endswith('.exe'):
            command = get_exe_command(exe)
            if not command:
                continue
            match = re.search(u'.+\\\\(.+)\\\\(\w+)\.exe', command,  flags=re.IGNORECASE)
            if match:
                name = match.group(2)
                progid = '.'.join([name, 'edo'])
                exes.append((name, progid, command))
        else:
            sub_key = '\\'.join([
                exe, 'shell', 'edit', 'command'
            ])
            edit_command = get_command(key, sub_key)
            sub_key = '\\'.join([
                exe, 'shell', 'open', 'command'
            ])
            open_command = get_command(key, sub_key)

            if not (open_command or edit_command):
                continue

            command = edit_command or open_command
            match = re.search(u'.+\\\\(.+)\\\\(\w+)\.exe', command,  flags=re.IGNORECASE)
            if match:
                name = match.group(2)
                progid = '.'.join([name, 'edo'])
                exes.append((name, progid, command))

    return exes


def get_open_with_progs(ext):
    """get the open with progids

    @params
        ext -- file extention
    @return
        name progrid command
    """
    import re

    # find all keys
    key = OpenKey(HKEY_CLASSES_ROOT, None)
    key_no = QueryInfoKey(key)[0]

    all_keys = []
    for index in xrange(key_no):
        all_keys.append(EnumKey(key, index))

    # try to find open with progids
    sub_key = '\\'.join([
        ext, 'OpenWithProgids'
    ])

    try:
        key = OpenKey(HKEY_CLASSES_ROOT, sub_key)
    except WindowsError:
        return None

    # add default program
    progids = []

    logger.debug('current ext: %s', ext)
    logger.debug('all key number: %s', len(all_keys))

    # enum value under the key
    value_no = QueryInfoKey(key)[1]
    for index in xrange(value_no):
        value = EnumValue(key, index)[0]
        value and logger.debug('find progid: %s', value)
        if value and value in all_keys:
            progids.append(value)

    logger.debug('open with progrids: %s', progids)

    # get the information about the progids
    exes = []
    for progid in progids:
        name = get_prog_name(progid)
        command = get_prog_command(progid)
        if name and command:
            exes.append((name, progid, command))
        if command and not name:
            match = re.search(u'.+\\\\(\w+)\.exe', command,  flags=re.IGNORECASE)
            if match:
                name = match.group(1)
                exes.append((name, progid, command))
    return exes


def get_shell_progids(ext):
    """get shell command of the ext
    notice that this function just find the first level shell command
    @params:
        ext -- file extention
    @return
        name, progid, command

    """
    import re

    sub_key = '\\'.join([
        ext, 'shell'
    ])

    try:
        key = OpenKey(HKEY_CLASSES_ROOT, sub_key)
    except WindowsError:
        return None

    key_no = QueryInfoKey(key)[0]

    exes = []
    for index in xrange(key_no):
        _name = EnumKey(key, index)
        sub_key = '\\'.join([
            _name, 'command'
        ])
        try:
            _key = OpenKey(key, sub_key)
            command = QueryValueEx(_key, None)[0]
            if command:
                match = re.search(u'.+\\\\(.+)\\\\\w+\.exe', command,  flags=re.IGNORECASE)
                if match:
                    name = match.group(1)
                    progid = '.'.join([name, 'edo'])
                    exes.append((name, progid, command))
        except WindowsError:
            pass

    logger.debug('shell program: %s', exes)

    return exes


def get_direct_progids(ext):
    """get all direct program ids
    notice under the first level of the ext registry

    @params:
        ext -- file extention
    @return
        name, progid, command
    """
    import re

    key = OpenKey(HKEY_CLASSES_ROOT, None)
    key_no = QueryInfoKey(key)[0]

    # get all sub keys
    all_keys = []
    for index in xrange(key_no):
        all_keys.append(EnumKey(key, index))

    # check if the default program already in list
    key = OpenKey(HKEY_CLASSES_ROOT, ext)
    progids = [QueryValue(key, None)]

    logger.debug('default progid: %s', progids)

    # get backup programs
    try:
        progid = QueryValueEx(key, 'backup')[0]
    except WindowsError:
        progid = ''

    logger.debug('backup progid: %s', progid)

    progids.append(progid)

    key_no = QueryInfoKey(key)[0]
    for index in xrange(key_no):
        _id = EnumKey(key, index)
        if _id in all_keys:
            progids.append(_id)

    logger.debug('assic progid: %s', progids)

    filter_func = (
        lambda ext_:
            (ext_.endswith(ext) or ext_.endswith(ext.upper())) and
            (ext_ != ext and ext_ != ext.upper()))
    progids.extend(filter(filter_func, all_keys))

    logger.debug('related progid: %s', progids)

    exes = []
    for progid in progids:
        name = get_prog_name(progid)
        command = get_prog_command(progid)
        if name and command:
            exes.append((name, progid, command))
        if command and not name:
            match = re.search(u'.+\\\\(.+)\\\\(\w+)\.exe', command,  flags=re.IGNORECASE)
            if match:
                name = match.group(2)
                exes.append((name, progid, command))
    return exes


def get_all_program(ext):
    """find all exe inside the wildcard key
    @params:
        ext -- file extention
    @return
        [(name, progid, command)] -- program name and command
    """
    exes = []


    # open with list
    exes.extend(get_open_with_list(ext) or [])

    # open with progid
    exes.extend(get_open_with_progs(ext) or [])

    # get shell command
    exes.extend(get_shell_progids(ext) or [])

    # get direct program
    exes.extend(get_direct_progids(ext) or [])

    # wildcard programs
    # exes.extend(get_wildcard_program() or [])

    # get rid of the duplicate program
    dup = set()
    uniq = [exe for exe in exes if exe[0].lower() not in dup and not dup.add(exe[0].lower())]

    # make the default editor the first one
    default_editor = user_current_editor(ext)

    if default_editor:
        for index, item in enumerate(uniq):
            if default_editor == item[1]:
                default_editor = item
                break
        if len(default_editor) > 1:
            del uniq[index]
            uniq.insert(0, default_editor)

    return uniq


def get_wildcard_program():
    """get wildcard program"""

    exes = []

    # open with list
    exes.extend(get_open_with_list('*') or [])

    # open with progid
    exes.extend(get_open_with_progs('*') or [])

    # get shell command
    exes.extend(get_shell_progids('*') or [])

    # get direct program
    exes.extend(get_direct_progids('*') or [])

    return list(set(exes))


def user_current_editor(ext):
    """get the usr current editor
    @params:
        ext -- file extention
    @return
        progid -- program progid
    """
    try:
        key = OpenKey(HKEY_CLASSES_ROOT, ext)
        return QueryValue(key, None)
    except WindowsError:
        return None


def backup_user_editor(ext):
    """back up the user editor"""

    current_editor = user_current_editor(ext)

    if not current_editor:
        return

    # need to backup the user editor
    sub_key = '\\'.join([
        'Software', 'Classes', ext
    ])

    key = CreateKeyEx(HKEY_CURRENT_USER, sub_key, 0, KEY_SET_VALUE)
    SetValueEx(key, 'backup', 0, REG_SZ, current_editor)


def set_user_editor(ext, progid, command):
    """set the default editor for the user

    this method rely on the program path

    params:
        ext - file ext
        progid - progid
        progpath - program execute path
    """

    # backup editor first
    backup_user_editor(ext)

    # must use the expand winreg string, if not, you may not have access
    # to the app and you need to use REG_EXPAND_SZ value type
    command = ExpandEnvironmentStrings(command)

    sub_key = '\\'.join([
        'Software', 'Classes', ext
    ])

    key = CreateKeyEx(HKEY_CURRENT_USER, sub_key, 0, KEY_SET_VALUE)
    SetValueEx(key, None, 0, REG_SZ, progid)

    sub_key = '\\'.join([
        'Software', 'Classes', progid, 'shell', 'edit', 'command'
    ])

    key = CreateKeyEx(HKEY_CURRENT_USER, sub_key, 0, KEY_SET_VALUE)
    SetValueEx(key, None, 0, REG_SZ, command)


def set_user_choice(ext, progid):
    """set the default program for the ext

    you may batter make sure the progid exists and this program just work for
    open. do not for edit.

    params:
        - ext file extention
        - progid program id in regitstry
    """
    key_seq = '\\'.join([
        'Software', 'Microsoft', 'Windows',
        'CurrentVersion', 'Explorer', 'FileExts', ext, 'UserChoice'
    ])

    user_default = CreateKeyEx(HKEY_CURRENT_USER, key_seq, 0, KEY_SET_VALUE)

    SetValueEx(user_default, 'Progid', 0, REG_SZ, progid)


if __name__ == '__main__':

    print get_all_program('.txt')
    print get_all_program('.js')

    print "user default editor for text: ", user_current_editor(".txt")
    print 'backup the user editor'

    backup_user_editor('.txt')

