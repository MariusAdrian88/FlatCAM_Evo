# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# MIT Licence                                              #
# ##########################################################
"""
Path completion for the auto-completer of the Tcl Shell, the Script editor and the text editor.

A word at the cursor that looks like a path (Windows, Linux or macOS) gets the entries of its folder: the folders
first, then the files. After a Tcl command that opens or writes one kind of file (open_gerber, export_svg ...) only
the files of that kind are listed.
"""

import os
import re
import sys
import time
from dataclasses import dataclass

from appCommon.RegisterFileKeywords import Extensions

# the most entries of one folder in the popup: a bigger folder is cut
MAX_ENTRIES = 500
# a folder listing is used again for this time (seconds) while the user types in the same folder
_CACHE_SECONDS = 2.0

# the start of a path: a drive (C:/), the root or a UNC path (/ \), the home (~) or the current folder (./ ../)
_PATH_START = re.compile(r'^(?:[A-Za-z]:[\\/]|[\\/]|~(?:[\\/]|$)|\.{1,2}[\\/])')
# what ends a word of a Tcl command
_WORD_END = re.compile(r'[\s\[;]')
# a closed group of a Tcl command: {...} or "..."
_CLOSED_GROUP = re.compile(r'\{[^{}]*\}|"[^"]*"')
# the first word of the last Tcl command: after the start of the line, ';', '[' or an open '{'
_COMMAND_START = re.compile(r'(?:^|[;\[{])\s*([A-Za-z_][\w:]*)[^;\[{]*$')

# FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM of Windows
_HIDDEN = 0x2 | 0x4
# the names on Windows and macOS have no case
_NO_CASE = sys.platform in ('win32', 'darwin')


def _command_extensions():
    ext = Extensions
    table = {
        ('open_gerber', 'export_gerber', 'export_grb', 'egr'): ext.grb_list,
        ('open_excellon', 'export_excellon', 'export_exc', 'ee'): ext.exc_list,
        ('open_gcode', 'export_gcode', 'write_gcode'): ext.gcode_list,
        ('open_svg', 'import_svg', 'export_svg'): ext.svg_list,
        ('open_dxf', 'export_dxf', 'edxf'): ext.dxf_list,
        ('open_project', 'save_project'): ext.prj_list,
        ('source',): ext.script_list,
    }
    return {name: frozenset(exts) for names, exts in table.items() for name in names}


# Tcl command -> the extensions of the files it opens or writes
COMMAND_EXTENSIONS = _command_extensions()


@dataclass
class PathToken:
    """The path at the cursor."""
    # the text of the path, from its first character to the cursor
    text: str
    # '{' or '"' if the path is in braces or quotes, else ''
    opener: str
    # the folder part (with its last separator) and the start of the name after it
    folder: str
    partial: str


def path_at_cursor(line):
    """
    The path that ends at the cursor.

    :param line:    the text of the line before the cursor
    :return:        PathToken, or None if the word at the cursor is not a path
    """
    candidates = []
    # a path in braces or quotes can have spaces; a brace that opens a Tcl body is no path and the word is tried
    brace = line.rfind('{')
    if brace >= 0 and '}' not in line[brace + 1:]:
        candidates.append(('{', line[brace + 1:]))
    if line.count('"') % 2 == 1:
        candidates.append(('"', line[line.rfind('"') + 1:]))
    candidates.append(('', _WORD_END.split(line)[-1]))
    for opener, text in candidates:
        if text and not text.startswith('$') and looks_like_path(text):
            cut = max(text.rfind('/'), text.rfind('\\')) + 1
            return PathToken(text=text, opener=opener, folder=text[:cut], partial=text[cut:])
    return None


def looks_like_path(text):
    """
    True for a path: it starts like an absolute or relative path and has a name after the start (/us, ./ap, C:\\Us)
    or is a drive root typed in full (C:/); or its folder exists in the current folder. A lone '/', '~', './' or
    '../' is no path (it can be a Tcl division).
    """
    start = _PATH_START.match(text)
    if start:
        if re.fullmatch(r'[A-Za-z]:[\\/]', text) or text[start.end():].strip('\\/'):
            return True
        return False
    cut = max(text.rfind('/'), text.rfind('\\'))
    return cut > 0 and os.path.isdir(text[:cut])


def command_at_cursor(line):
    """
    The name of the Tcl command whose arguments the cursor is in ('' if none).

    :param line:    the text of the line before the word at the cursor
    """
    # the closed groups are arguments; what stays open ({ of a body, [ of a command) starts a command
    previous = None
    while previous != line:
        previous, line = line, _CLOSED_GROUP.sub('x', line)
    match = _COMMAND_START.search(line)
    return match.group(1) if match else ''


def _resolved_folder(folder):
    if not folder:
        return os.getcwd()
    folder = os.path.expanduser(folder)
    if folder == '~':
        return os.path.expanduser('~')
    return folder


def _not_listed(folder):
    """
    True for a folder that is not listed: a network path (UNC) or a drive that is not local. Listing it asks the
    network and blocks the GUI for a long time when the server is gone.
    """
    if folder[:2] in ('\\\\', '//'):
        return True
    if sys.platform != 'win32':
        return False
    drive = os.path.splitdrive(os.path.abspath(folder))[0]
    if drive[:2] in ('\\\\', '//') or not drive:
        return True
    try:
        import ctypes
        # asks the drive letter only, never the network; 2 removable, 3 fixed, 6 RAM disk
        return ctypes.windll.kernel32.GetDriveTypeW(drive + '\\') not in (2, 3, 6)
    except (AttributeError, OSError):
        return True


_cache = {'folder': None, 'time': 0.0, 'entries': []}


def _folder_entries(folder):
    """(name, is a folder, is hidden) of every entry of the folder; empty if it can not be read."""
    now = time.monotonic()
    if _cache['folder'] == folder and now - _cache['time'] < _CACHE_SECONDS:
        return _cache['entries']
    entries = []
    try:
        with os.scandir(folder) as found:
            for entry in found:
                try:
                    # the hidden and system entries of Windows ($Recycle.Bin, AppData ...)
                    hidden = bool(getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0) & _HIDDEN)
                    entries.append((entry.name, entry.is_dir(), hidden or entry.name.startswith('.')))
                except OSError:
                    continue
    except OSError:
        entries = []
    _cache.update(folder=folder, time=now, entries=entries)
    return entries


def list_entries(token, extensions=None):
    """
    The entries of the folder of a path that start with its partial name: the folders (with a '/' at the end)
    first, then the files, each group in alphabetical order.

    :param token:       PathToken
    :param extensions:  the extensions (lower case, no dot) of the files to list; None lists all the files
    :return:            list of names
    """
    folder = _resolved_folder(token.folder)
    if _not_listed(folder):
        return []
    partial = token.partial.casefold() if _NO_CASE else token.partial
    folders, files = [], []
    for name, is_folder, hidden in _folder_entries(folder):
        key = name.casefold() if _NO_CASE else name
        if not key.startswith(partial):
            continue
        # the hidden entries only after the first letters of their name (AppData, .config)
        if hidden and not partial:
            continue
        if is_folder:
            folders.append(name)
        elif extensions is None or os.path.splitext(name)[1][1:].lower() in extensions:
            files.append(name)
    folders.sort(key=str.casefold)
    files.sort(key=str.casefold)
    return ([name + '/' for name in folders] + files)[:MAX_ENTRIES]


def path_completions(line):
    """
    The path at the cursor and its completions.

    :param line:    the text of the line before the cursor
    :return:        (PathToken, list of names), or (None, []) if the word at the cursor is not a path
    """
    token = path_at_cursor(line)
    if token is None:
        return None, []
    before = line[:len(line) - len(token.text) - len(token.opener)]
    return token, list_entries(token, COMMAND_EXTENSIONS.get(command_at_cursor(before)))
