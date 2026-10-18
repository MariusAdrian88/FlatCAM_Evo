"""
Relative file names are relative to the folder FlatCAM was started from, not to the application folder it chdir()s into.
"""

import importlib
import inspect
import os
import pkgutil
import re
import subprocess
import sys
import textwrap
from unittest.mock import Mock

import pytest

import tclCommands
from tclCommands.TclCommand import TclCommand
from appCommon import StartDir
from appCommon.StartDir import compute_start_dir, resolve_user_path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.abspath(os.sep + 'home' + os.sep + 'user')


def _all_commands():
    found = {}
    for module_info in pkgutil.walk_packages(tclCommands.__path__, 'tclCommands.'):
        module = importlib.import_module(module_info.name)
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, TclCommand) and cls.__module__ == module_info.name:
                found[name] = cls
    return found


def _path_commands():
    """(class name, the name of its file argument) for every command that has one"""
    result = []
    for name, cls in sorted(_all_commands().items()):
        for key in list(cls.arg_names) + list(cls.option_types):
            if key in cls.path_args:
                result.append((name, key))
    return result


class TestComputeStartDir:
    app_dir = os.path.join(HOME, 'FlatCAM.app', 'Contents', 'Frameworks')

    def test_normal_folder_is_kept(self):
        cwd = os.path.join(HOME, 'boards')
        assert compute_start_dir(cwd, self.app_dir, frozen=False, home=HOME) == cwd
        assert compute_start_dir(cwd, self.app_dir, frozen=True, home=HOME) == cwd

    def test_unreadable_working_directory_gives_home(self):
        assert compute_start_dir(None, self.app_dir, frozen=False, home=HOME) == HOME

    def test_root_gives_home(self):
        # a packaged application started from the Finder / Dock starts in the root folder
        assert compute_start_dir(os.path.abspath(os.sep), self.app_dir, frozen=True, home=HOME) == HOME

    def test_packaged_application_started_inside_itself_gives_home(self):
        assert compute_start_dir(self.app_dir, self.app_dir, frozen=True, home=HOME) == HOME
        assert compute_start_dir(os.path.join(self.app_dir, 'assets'), self.app_dir, frozen=True, home=HOME) == HOME

    def test_source_checkout_started_from_its_own_folder_is_kept(self):
        assert compute_start_dir(self.app_dir, self.app_dir, frozen=False, home=HOME) == self.app_dir

    def test_sibling_of_the_application_folder_is_not_inside_it(self):
        sibling = self.app_dir + '_data'
        assert compute_start_dir(sibling, self.app_dir, frozen=True, home=HOME) == sibling


class TestResolveUserPath:
    def test_relative_name_gets_the_start_folder(self):
        assert resolve_user_path('out.nc', start_dir=HOME) == os.path.join(HOME, 'out.nc')
        assert resolve_user_path(os.path.join('sub', 'out.nc'), start_dir=HOME) == os.path.join(HOME, 'sub', 'out.nc')

    def test_default_start_folder_is_the_module_value(self, monkeypatch):
        monkeypatch.setattr(StartDir, 'START_DIR', HOME)
        assert resolve_user_path('out.nc') == os.path.join(HOME, 'out.nc')

    def test_absolute_name_is_unchanged(self):
        absolute = os.path.join(HOME, 'elsewhere', 'out.nc')
        assert resolve_user_path(absolute, start_dir=os.path.join(HOME, 'x')) == absolute

    def test_tilde_is_expanded(self):
        assert resolve_user_path('~' + os.sep + 'out.nc', start_dir=HOME) == os.path.join(os.path.expanduser('~'),
                                                                                         'out.nc')

    @pytest.mark.parametrize('value', ['', None, 5, b'out.nc'])
    def test_anything_that_is_not_a_name_is_unchanged(self, value):
        assert resolve_user_path(value, start_dir=HOME) == value


class TestTclPathArguments:
    def test_the_commands_with_a_file_argument_are_found(self):
        names = {name for name, _ in _path_commands()}
        assert {'TclCommandWriteGCode', 'TclCommandSaveProject', 'TclCommandOpenGerber', 'TclCommandExportSVG',
                'TclCommandSetPath', 'TclCommandOpenFolder'} <= names

    def test_no_command_has_an_unlisted_file_like_argument(self):
        # a new command with a file argument under another name would write into the application folder again
        flag_arguments = {('TclCommandNew', 'keep_scripts')}
        missed = []
        for name, cls in _all_commands().items():
            for key in list(cls.arg_names) + list(cls.option_types):
                if re.search(r'file|path|dir|folder', key, re.I) and key not in cls.path_args \
                        and (name, key) not in flag_arguments:
                    missed.append((name, key))
        assert missed == []

    @pytest.mark.parametrize('command_name,key', _path_commands())
    def test_relative_name_is_resolved_against_the_start_folder(self, command_name, key, monkeypatch):
        monkeypatch.setattr(StartDir, 'START_DIR', HOME)
        cls = _all_commands()[command_name]
        command = cls.__new__(cls)
        command.raise_tcl_error = Mock(side_effect=AssertionError)

        if key in cls.arg_names:
            before = list(cls.arg_names)[:list(cls.arg_names).index(key)]
            assert all(cls.arg_names[k] is str for k in before)
            args = ['obj'] * len(before) + ['out.dat']
        else:
            args = ['-%s' % key, 'out.dat']
        named_args, _ = command.check_args(tuple(args))

        assert named_args[key] == os.path.join(HOME, 'out.dat')

    def test_absolute_name_is_not_touched(self, monkeypatch):
        monkeypatch.setattr(StartDir, 'START_DIR', HOME)
        cls = _all_commands()['TclCommandWriteGCode']
        command = cls.__new__(cls)
        command.raise_tcl_error = Mock(side_effect=AssertionError)

        absolute = os.path.abspath(os.sep + 'tmp' + os.sep + 'out.nc')
        assert command.check_args(('obj', absolute))[0]['filename'] == absolute

    def test_missing_name_is_still_reported(self, monkeypatch):
        monkeypatch.setattr(StartDir, 'START_DIR', HOME)
        cls = _all_commands()['TclCommandWriteGCode']
        command = cls.__new__(cls)
        command.raise_tcl_error = Mock()

        named_args, _ = command.check_args(('obj',))

        assert 'filename' not in named_args
        command.raise_tcl_error.assert_called_once_with("Missing required argument 'filename'.")

    def test_other_arguments_are_not_touched(self, monkeypatch):
        monkeypatch.setattr(StartDir, 'START_DIR', HOME)
        cls = _all_commands()['TclCommandWriteGCode']
        command = cls.__new__(cls)
        command.raise_tcl_error = Mock(side_effect=AssertionError)

        named_args, _ = command.check_args(('obj', 'out.nc', '-muted', '1'))
        assert named_args['name'] == 'obj'
        assert named_args['muted'] == '1'


class TestShellfileOption:
    """appMain parses the command line when the class is defined: run it in a new interpreter."""

    CODE = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        import appMain
        print('SHELLFILE=' + appMain.App.cmd_line_shellfile)
        print('ARGS=' + repr(appMain.App.args))
    """) % REPO

    def _run(self, cwd, *options):
        return subprocess.run([sys.executable, '-c', self.CODE, *options], cwd=cwd, capture_output=True, text=True,
                              timeout=120)

    def test_relative_name_becomes_absolute(self, tmp_path):
        (tmp_path / 'job.FlatScript').write_text('# nothing\n')
        run = self._run(str(tmp_path), '--shellfile=job.FlatScript')
        assert run.returncode == 0, run.stderr
        shellfile = [line for line in run.stdout.splitlines() if line.startswith('SHELLFILE=')][0]
        assert os.path.realpath(shellfile[len('SHELLFILE='):]) == os.path.realpath(str(tmp_path / 'job.FlatScript'))
        assert os.path.isabs(shellfile[len('SHELLFILE='):])

    def test_missing_file_ends_the_program_before_anything_starts(self, tmp_path):
        run = self._run(str(tmp_path), '--shellfile=missing.FlatScript')
        assert run.returncode == 2
        assert 'missing.FlatScript' in run.stderr
        assert 'SHELLFILE=' not in run.stdout

    def test_a_folder_is_not_a_shellfile(self, tmp_path):
        run = self._run(str(tmp_path), '--shellfile=%s' % tmp_path)
        assert run.returncode == 2

    def test_without_the_option_nothing_changes(self, tmp_path):
        run = self._run(str(tmp_path))
        assert run.returncode == 0, run.stderr
        assert 'SHELLFILE=\n' in run.stdout + '\n'


class TestFileArguments:
    """The files to open that are given as arguments on the command line."""

    def test_relative_file_becomes_absolute_and_keywords_stay(self, tmp_path):
        (tmp_path / 'board.gtl').write_text('M02*\n')
        run = TestShellfileOption()._run(str(tmp_path), 'board.gtl', 'quit', 'missing.drl')
        assert run.returncode == 0, run.stderr
        line = [line for line in run.stdout.splitlines() if line.startswith('ARGS=')][0]
        args = eval(line[len('ARGS='):])
        assert os.path.realpath(args[0]) == os.path.realpath(str(tmp_path / 'board.gtl'))
        assert os.path.isabs(args[0])
        assert args[1:] == ['quit', 'missing.drl']
