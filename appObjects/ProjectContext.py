import os
import re

PROJECT_EXT = '.FlatPrj'

# characters that a file name on Windows can not hold
_INVALID_NAME_CHARS = '\\/:*?"<>|'
# names that Windows opens as a device and not as a file, also with an extension (NUL.nc)
_DEVICE_NAMES = frozenset(['CON', 'PRN', 'AUX', 'NUL'] + ['COM%d' % n for n in range(1, 10)] +
                          ['LPT%d' % n for n in range(1, 10)])


class ProjectContext:
    """
    The state of 1 project in the Project tab.

    While the project is active, its live state is in the App attributes (app.collection, app.options,
    app.project_filename, app.should_we_save). ProjectManager.capture() writes that state back here.
    """

    def __init__(self, name, collection, filename=None):
        self.name = name
        self.collection = collection
        self.filename = filename
        # only the project keys (see defaults.project_options); the application settings are not per project
        # None means: copy the project keys from the application defaults when the project becomes active
        self.options = None
        self.should_we_save = False
        # the canvas view rectangle when the project was last active
        self.view_rect = None
        # the Undo / Redo history (appHandlers.appHistory.HistoryManager); made by ProjectManager
        self.history = None


def unique_name(base, used):
    """
    Return base, or base with a number added or incremented, so that the result is not in used.
    The rule is the same as in ObjectCollection.append() for object names.

    :param base:    the wanted name
    :param used:    the names that are already taken
    :return:        a name that is not in used
    """
    name = base
    while name in used:
        match = re.search(r'(.*[^\d])?(\d+)$', name)
        if match:
            name = (match.group(1) or '') + str(int(match.group(2)) + 1)
        else:
            name += '_1'
    return name


def same_path(first, second):
    """
    True if the 2 paths point to the same file. An empty path is never the same.
    """
    if not first or not second:
        return False
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def name_from_filename(filename):
    """
    The project name for a project file: the file name without the folder and the extension.
    """
    return os.path.splitext(os.path.basename(filename))[0]


def is_valid_name(name):
    """
    True if the name is not empty and can be used as a file name.
    """
    if not name or not name.strip():
        return False
    return not any(char in name for char in _INVALID_NAME_CHARS)


def safe_file_name(name, fallback):
    """
    The name as a file name without a folder: the characters that a file name on Windows can not hold (also the path
    separators and the drive colon) and the control characters become '_', and a Windows device name (CON, NUL,
    COM1...) gets a '_' in front. An empty name gives the fallback.
    """
    name = ''.join('_' if char in _INVALID_NAME_CHARS or ord(char) < 32 else char for char in str(name))
    if not name.strip():
        return fallback
    if name.split('.')[0].rstrip(' ').upper() in _DEVICE_NAMES:
        name = '_' + name
    return name
