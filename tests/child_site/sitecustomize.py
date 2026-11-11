"""
QSettings of the test processes go to INI files of the test session, never to the settings of the user.

Python imports this module at the start of each process whose PYTHONPATH holds this folder: tests/conftest.py puts
the folder there. So a child process of a test (flatcam.py --headless, python -c ..., a multiprocessing worker) also
keeps its QSettings in the test folder. Before, 1 headless start wrote the theme 'light', the appearance and the
layout into the registry of the user (HKCU\\Software\\Open Source\\FlatCAM_EVO).
"""

import os

SETTINGS_DIR_ENV = 'FLATCAM_TEST_SETTINGS_DIR'


def isolate_qsettings(folder):
    """Replace PyQt6.QtCore.QSettings: QSettings(organization, application) is an INI file in the folder."""
    try:
        from PyQt6 import QtCore
    except ImportError:
        return
    # a second call (the conftest of a pytest child process) takes a new folder, from the original class
    native = getattr(QtCore.QSettings, 'flatcam_native', QtCore.QSettings)

    class TestSettings(native):
        flatcam_native = native
        flatcam_folder = folder

        def __init__(self, *args, **kwargs):
            if len(args) >= 2 and isinstance(args[1], native.Format):
                super().__init__(*args, **kwargs)     # an explicit file: QSettings(path, format)
                return
            names = [arg for arg in args if isinstance(arg, str)] or ['default']
            path = os.path.join(folder, *[name.replace(os.sep, '_') for name in names]) + '.ini'
            super().__init__(path, native.Format.IniFormat)

    QtCore.QSettings = TestSettings


if os.environ.get(SETTINGS_DIR_ENV):
    isolate_qsettings(os.environ[SETTINGS_DIR_ENV])
