"""
Relative file names typed by the user.

App.__init__() does os.chdir() into the application folder (the GUI resources are loaded by relative path). A relative
file name in a Tcl command (``write_gcode out.nc``) or on the command line is meant relative to the folder FlatCAM was
started from, not relative to the application folder. For a packaged application that folder is the signed, often
read-only application bundle: the file was written inside it and the code signature broke.

This module is imported before the chdir, so it can remember the folder FlatCAM was started from.
"""

import os
import sys


def _is_inside(path, folder):
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(folder)]) == os.path.realpath(folder)
    except ValueError:
        # different drives on Windows
        return False


def compute_start_dir(cwd, app_dir, frozen, home):
    """
    :param cwd: the working directory at the start of the application, None if it could not be read
    :param app_dir: the application folder
    :param frozen: True for a packaged application (PyInstaller, cx_freeze ...)
    :param home: the home folder of the user
    :return: the folder that relative file names are resolved against.
    The home folder replaces a working directory that is no place for the user's files: none, the root of the file
    system (a packaged application started from the Finder / Dock has it) or, for a packaged application, a folder
    inside the application itself. A source checkout started from its own folder keeps it.
    """

    if not cwd:
        return home
    if os.path.dirname(cwd) == cwd:
        return home
    if frozen and _is_inside(cwd, app_dir):
        return home
    return cwd


def _current_dir():
    try:
        return os.getcwd()
    except OSError:
        # the folder was deleted under the running process
        return None


START_DIR = compute_start_dir(
    cwd=_current_dir(),
    app_dir=os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
    frozen=getattr(sys, 'frozen', False),
    home=os.path.expanduser('~'),
)


def resolve_user_path(path, start_dir=None):
    """
    :param path: a file or folder name as the user typed it
    :param start_dir: the folder for relative names; default the folder FlatCAM was started from
    :return: the absolute name. ``~`` is expanded, an absolute name and anything that is not a non-empty string are
    returned unchanged.
    """

    if not isinstance(path, str) or path == '':
        return path

    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return path
    return os.path.join(START_DIR if start_dir is None else start_dir, path)
