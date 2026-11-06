"""appPreProcessor.load_preprocessors() finds the preprocessors that come with the application next to
appPreProcessor.py (next to the executable in a frozen build), whatever the current working directory, and executes
every preprocessor file as a module of its own with importlib.util.spec_from_file_location() / exec_module().

Before, the built-in folder was 'preprocessors' relative to the current working directory (none was found from another
folder), every file was loaded with the deprecated SourceFileLoader.load_module() (1 DeprecationWarning per file on
Python 3.12) and all the files were executed in the same module, 'FlatCAMPostProcessor': a module level name of a
preprocessor file was replaced by the same name of the next file. A file written with 'from appPreProcessor import *'
still gets math from appPreProcessor.
"""

import ast
import glob
import logging
import os
import shutil
import sys
import warnings
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import appPreProcessor
from appPreProcessor import AppPreProcTools, PreProc, load_preprocessors

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILTIN_DIR = os.path.join(ROOT, 'preprocessors')


def builtin_class_names():
    """The names of the classes defined in the preprocessor files that come with the application."""
    names = set()
    for file in glob.glob(os.path.join(BUILTIN_DIR, '*.py')):
        with open(file, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        names.update(node.name for node in tree.body if isinstance(node, ast.ClassDef))
    return names


def write_user_preprocessor(folder, name, source=None, mark=None):
    """Writes <folder>/<name>.py. Without source: a class <name> whose methods return the module level name MARK."""
    os.makedirs(folder, exist_ok=True)
    if source is None:
        methods = ''.join("    def %s(self, p):\n        return MARK\n" % m
                          for m in sorted(AppPreProcTools.__abstractmethods__))
        source = "from appPreProcessor import AppPreProcTools\n\nMARK = %r\n\n\nclass %s(AppPreProcTools):\n%s" % (
            mark, name, methods)
    with open(os.path.join(folder, name + '.py'), 'w', encoding='utf-8') as f:
        f.write(source)


def load(app):
    """load_preprocessors() and the load_module() deprecation warnings it emitted."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        result = load_preprocessors(app)
    deprecated = [str(w.message) for w in caught
                  if issubclass(w.category, DeprecationWarning) and 'load_module' in str(w.message)]
    return result, deprecated


@pytest.fixture
def registry():
    """The registry is module wide: start empty, then restore it and remove the preprocessor modules."""
    saved = dict(appPreProcessor.preprocessors)
    saved_modules = set(sys.modules)
    appPreProcessor.preprocessors.clear()
    # another test module imports the built-in preprocessors as a package at collection time: execute the files again
    # so that this test starts with its own registry
    removed_modules = {name: sys.modules.pop(name) for name in list(sys.modules) if name.startswith('preprocessors.')}
    yield appPreProcessor.preprocessors
    appPreProcessor.preprocessors.clear()
    appPreProcessor.preprocessors.update(saved)
    for name in set(sys.modules) - saved_modules:
        if name.startswith(('FlatCAMPostProcessor', 'preprocessors.')):
            del sys.modules[name]
    sys.modules.update(removed_modules)


@pytest.fixture
def app(tmp_path):
    return SimpleNamespace(data_path=str(tmp_path / 'data'), log=MagicMock())


def test_builtin_preprocessors_load_from_another_working_directory(registry, app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result, _deprecated = load(app)

    assert result is appPreProcessor.preprocessors
    expected = builtin_class_names()
    assert {'default', 'GRBL_11', 'GRBL_laser', 'Marlin'} <= expected
    assert sorted(expected - set(result)) == []
    for name, pp in result.items():
        assert type(pp).__name__ == name
        assert isinstance(pp, (PreProc, AppPreProcTools))
    app.log.error.assert_not_called()
    assert result['default'].lift_code(SimpleNamespace(z_move=2.0, coords_decimals=4)) == 'G00 Z2.0000'


def test_user_preprocessor_loads_and_a_broken_file_is_logged_without_stopping_the_others(registry, app, monkeypatch):
    monkeypatch.chdir(ROOT)
    folder = os.path.join(app.data_path, 'preprocessors')
    write_user_preprocessor(folder, 'User_A', mark='A')
    write_user_preprocessor(folder, 'Broken', source="raise RuntimeError('broken preprocessor')\n")

    result, _deprecated = load(app)

    assert result['User_A'].start_code(None) == 'A'
    assert 'GRBL_11' in result and 'default' in result
    app.log.error.assert_called_once()
    assert 'broken preprocessor' in app.log.error.call_args.args[0]
    assert 'FlatCAMPostProcessor_Broken' not in sys.modules
