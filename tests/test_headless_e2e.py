"""flatcam.py --headless in a new process, without a display setting (QT_QPA_PLATFORM not set).

A start of the program takes about 3 seconds. The 3 processes of the module run together (a module fixture).
"""

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.slow

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_headless(args, cwd, stdin=None, timeout=300):
    env = dict(os.environ)
    env.pop('QT_QPA_PLATFORM', None)
    return subprocess.run([sys.executable, os.path.join(REPO, 'flatcam.py'), '--headless'] + args, cwd=str(cwd),
                          input=stdin, capture_output=True, text=True, timeout=timeout, env=env)


EXAMPLE_GERBER = os.path.join(REPO, 'assets', 'examples', 'files', 'test.gbr')


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    """The 3 processes start together (a start takes about 3 seconds): the shell file run saves a project and
    makes G-code from a Gerber, the failed command, the prompt that reads stdin."""
    shell = tmp_path_factory.mktemp('shellfile_run')
    shutil.copy(EXAMPLE_GERBER, shell / 'test.gbr')
    script = (
        'new_geometry g\n'
        'save_project $shellvar_0\n'
        'open_gerber test.gbr -outname board\n'
        'isolate board -dia 0.2 -outname iso\n'
        'cncjob iso -dia 0.2 -z_cut -0.1 -z_move 2 -feedrate 100 -outname cnc\n'
        'write_gcode cnc out.nc\n'
    )
    (shell / 'job.FlatScript').write_text(script)
    failed = tmp_path_factory.mktemp('failed_run')
    prompt = tmp_path_factory.mktemp('prompt_run')
    with ThreadPoolExecutor(3) as pool:
        jobs = {
            'shell': pool.submit(run_headless, ['--shellfile=job.FlatScript', '--shellvar=var.FlatPrj'], shell),
            'failed': pool.submit(run_headless, ['--command=new_geometry g; save_project out.FlatPrj; no_such_command'],
                                  failed),
            'prompt': pool.submit(run_headless, [], prompt, 'puts hello\nnew_geometry g\nsave_project r.FlatPrj\n'),
        }
        return {'shell': (shell, jobs['shell'].result()), 'failed': (failed, jobs['failed'].result()),
                'prompt': (prompt, jobs['prompt'].result())}


def test_a_failed_command_gives_exit_code_1_after_the_earlier_commands_ran(runs):
    folder, result = runs['failed']
    assert result.returncode == 1
    assert 'invalid command name "no_such_command"' in result.stderr
    assert (folder / 'out.FlatPrj').is_file()


def test_the_prompt_reads_stdin(runs):
    folder, result = runs['prompt']
    assert result.returncode == 0, result.stderr
    assert 'hello' in result.stdout
    assert (folder / 'r.FlatPrj').is_file()


def test_gerber_to_gcode(runs):
    folder, result = runs['shell']
    assert result.returncode == 0, result.stderr + result.stdout
    assert (folder / 'var.FlatPrj').is_file()   # a relative shellfile name and the shellvar
    gcode = (folder / 'out.nc').read_text()
    moves = [line for line in gcode.splitlines() if line.startswith(('G01', 'G1 ')) and 'X' in line]
    assert len(moves) > 10
