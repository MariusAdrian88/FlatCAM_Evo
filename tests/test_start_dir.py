"""
Relative file names are relative to the folder FlatCAM was started from, not to the application folder it chdir()s into,
unless the user moved the process with set_path or the Tcl cd.

Windows paths are also tested on the other platforms with ntpath (the tests named ..._emulated); the tests that need the
real platform are skipped elsewhere.
"""

import ntpath
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tclCommands.TclCommandOpenGerber import TclCommandOpenGerber
from tclCommands.TclCommandSetPath import TclCommandSetPath
from appCommon import StartDir
from appCommon.StartDir import compute_start_dir, resolve_existing_paths, resolve_user_path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.abspath(os.sep + 'home' + os.sep + 'user')

windows_only = pytest.mark.skipif(sys.platform != 'win32', reason="needs the Windows path rules")
not_windows = pytest.mark.skipif(sys.platform == 'win32', reason="POSIX paths")


def same_folder(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


@pytest.fixture
def in_app_folder(monkeypatch):
    """the state of the running application: the process is in the application folder, the start folder is HOME"""
    monkeypatch.setattr(StartDir, 'START_DIR', HOME)
    monkeypatch.chdir(StartDir.APP_DIR)


@pytest.fixture
def windows_paths(monkeypatch):
    """StartDir with the path rules of Windows, on any platform"""
    monkeypatch.setattr(StartDir, 'os', SimpleNamespace(path=ntpath, getcwd=os.getcwd, environ=os.environ, sep='\\'))


def _command(cls):
    command = cls.__new__(cls)
    command.raise_tcl_error = Mock(side_effect=AssertionError)
    return command


class TestComputeStartDir:
    app_dir = os.path.join(HOME, 'FlatCAM.app', 'Contents', 'Frameworks')

    def test_normal_folder_is_kept(self):
        cwd = os.path.join(HOME, 'boards')
        assert compute_start_dir(cwd, self.app_dir, frozen=False, home=HOME) == cwd
        assert compute_start_dir(cwd, self.app_dir, frozen=True, home=HOME) == cwd

    def test_packaged_application_started_inside_itself_gives_home(self):
        assert compute_start_dir(self.app_dir, self.app_dir, frozen=True, home=HOME) == HOME
        assert compute_start_dir(os.path.join(self.app_dir, 'assets'), self.app_dir, frozen=True, home=HOME) == HOME

    def test_source_checkout_started_from_its_own_folder_is_kept(self):
        assert compute_start_dir(self.app_dir, self.app_dir, frozen=False, home=HOME) == self.app_dir

    def test_a_folder_inside_the_windows_folder_gives_home(self):
        # some launchers start in C:\Windows\System32, where the user cannot write
        system_root = os.path.join(HOME, 'Windows')
        cwd = os.path.join(system_root, 'System32')
        assert compute_start_dir(cwd, self.app_dir, frozen=True, home=HOME, system_root=system_root) == HOME
        assert compute_start_dir(system_root, self.app_dir, frozen=False, home=HOME, system_root=system_root) == HOME

class TestWindowsPathsEmulated:
    """the rules of Windows paths, run with ntpath on every platform"""

    app_dir = 'C:\\Program Files\\FlatCAM'
    home = 'C:\\Users\\me'

    def test_the_drive_root_gives_home(self, windows_paths):
        assert compute_start_dir('C:\\', self.app_dir, frozen=True, home=self.home) == self.home

    def test_a_relative_name_is_joined_to_a_unc_folder(self, windows_paths):
        folder = '\\\\server\\share\\proj'
        assert resolve_user_path('board.gtl', start_dir=folder) == folder + '\\board.gtl'

@not_windows
class TestPosixLaunchers:
    def test_an_appimage_mount_is_the_application_folder(self):
        app_dir = '/tmp/.mount_FlatCAMab12cd/usr/lib/flatcam'
        assert compute_start_dir(app_dir, app_dir, frozen=True, home=HOME) == HOME
        assert compute_start_dir(app_dir + '/assets', app_dir, frozen=True, home=HOME) == HOME

class TestResolveUserPath:
    def test_relative_name_gets_the_start_folder(self):
        assert resolve_user_path('out.nc', start_dir=HOME) == os.path.join(HOME, 'out.nc')
        assert resolve_user_path(os.path.join('sub', 'out.nc'), start_dir=HOME) == os.path.join(HOME, 'sub', 'out.nc')

class TestResolveExistingPaths:
    def test_files_that_exist_are_made_absolute_and_the_other_words_stay(self, tmp_path):
        (tmp_path / 'board.gtl').write_text('M02*\n')
        assert resolve_existing_paths(['board.gtl', 'quit', 'missing.drl'], start_dir=str(tmp_path)) == [
            str(tmp_path / 'board.gtl'), 'quit', 'missing.drl']

class TestBaseDir:
    """set_path and the Tcl cd move the process: the relative names follow it"""

    def test_set_path_then_open_gerber(self, in_app_folder, tmp_path):
        # the pattern of many scripts: set_path <folder>, then the names relative to it
        tkinter = pytest.importorskip('tkinter')
        app = SimpleNamespace(shell=SimpleNamespace(tcl=tkinter.Tcl()), options={}, inform_shell=Mock())
        set_path = _command(TclCommandSetPath)
        set_path.app = app
        args, unnamed = set_path.check_args((str(tmp_path),))

        set_path.execute(args, unnamed)

        assert app.options['global_tcl_path'] == str(tmp_path).replace('\\', '/')
        args, _ = _command(TclCommandOpenGerber).check_args(('board.gtl',))
        assert same_folder(args['filename'], tmp_path / 'board.gtl')

@pytest.fixture(scope='module')
def command_line_runs(tmp_path_factory):
    """Three command lines run in new interpreters at the same time: appMain takes seconds to import."""
    from concurrent.futures import ThreadPoolExecutor
    folder = tmp_path_factory.mktemp('start_dir_cmdline')
    (folder / 'job.FlatScript').write_text('# nothing\n')
    (folder / 'board.gtl').write_text('M02*\n')
    code = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        import appMain
        print('SHELLFILE=' + appMain.App.cmd_line_shellfile)
        print('ARGS=' + repr(appMain.App.args))
    """) % REPO

    def run(*options):
        return subprocess.run([sys.executable, '-c', code, *options], cwd=str(folder), capture_output=True,
                              text=True, timeout=300)

    with ThreadPoolExecutor(3) as pool:
        jobs = [pool.submit(run, *options) for options in (('--shellfile=job.FlatScript',),
                                                           ('--shellfile=missing.FlatScript',),
                                                           ('board.gtl', 'quit', 'missing.drl'))]
        return folder, [job.result() for job in jobs]


class TestShellfileOption:
    """appMain parses the command line when the class is defined: run it in a new interpreter."""

    def test_relative_name_becomes_absolute(self, command_line_runs):
        folder, (run, _, _) = command_line_runs
        assert run.returncode == 0, run.stderr
        shellfile = [line for line in run.stdout.splitlines() if line.startswith('SHELLFILE=')][0]
        assert os.path.realpath(shellfile[len('SHELLFILE='):]) == os.path.realpath(str(folder / 'job.FlatScript'))
        assert os.path.isabs(shellfile[len('SHELLFILE='):])

    def test_missing_file_ends_the_program_before_anything_starts(self, command_line_runs):
        _, (_, run, _) = command_line_runs
        assert run.returncode == 2
        assert 'missing.FlatScript' in run.stderr
        assert 'SHELLFILE=' not in run.stdout


class TestFileArguments:
    """The files to open that are given as arguments on the command line."""

    def test_relative_file_becomes_absolute_and_keywords_stay(self, command_line_runs):
        folder, (_, _, run) = command_line_runs
        assert run.returncode == 0, run.stderr
        line = [line for line in run.stdout.splitlines() if line.startswith('ARGS=')][0]
        args = eval(line[len('ARGS='):])
        assert os.path.realpath(args[0]) == os.path.realpath(str(folder / 'board.gtl'))
        assert os.path.isabs(args[0])
        assert args[1:] == ['quit', 'missing.drl']
