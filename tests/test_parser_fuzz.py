# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Parser fuzz harness                                       #
# Author: Marius Stanciu (c)                                #
# Method after TRACE Boardviewer (MIT)                      #
# https://github.com/trace-boardviewer/trace-boardviewer    #
# MIT Licence                                               #
# ##########################################################

"""
Mutated input files must give a result or the parser's own parse error, never another exception, a NaN
coordinate or an invalid polygon. Short mode: a few deterministic inputs of each parser and the saved regression
corpus (tests/fixtures/fuzz_regressions). Long mode and its environment variables: see tests/fuzz_parsers.py.
"""

import math
import os

import pytest
from shapely.geometry import Point

from tests import fuzz_parsers as fz


ITERATIONS, SEED, SELECTED, SAVE, HANG = fz.env_config()
LONG_ONLY = ('padsfull',)  # a parse of the seed takes about 8 s


def _params(names):
    return [pytest.param(name, marks=pytest.mark.diptrace) if name == 'diptrace' else name for name in names
            if ITERATIONS is not None or name not in LONG_ONLY]


@pytest.mark.parametrize('name', _params([n for n in fz.NAMES if n in SELECTED]))
def test_parser_survives_mutated_input(name):
    target = fz.get_target(name)
    report = fz.fuzz(target, ITERATIONS if ITERATIONS is not None else target.short_n, SEED, SAVE, HANG)
    if ITERATIONS is not None:
        print('\n' + report.format() + '\n  slowest: ' + '; '.join(report.slowest()))
    assert not report.defects, report.format()


def test_saved_defects_stay_fixed(tmp_path):
    # tests/fixtures/fuzz_regressions/<target>-<kind>-<hash8>.bin: the shrunk input of each defect the harness found
    failures = []
    for path in sorted(fz.CORPUS.glob('*.bin')) if fz.CORPUS.is_dir() else []:
        name = path.name.split('-')[0]
        if name == 'diptrace' and not os.environ.get('FLATCAM_TEST_DIPTRACE'):
            continue
        status, kind, location, message, _ = fz.replay(fz.get_target(name), path.read_bytes(), tmp_path)
        if status == 'defect':
            failures.append('%s: %s at %s: %s' % (path.name, kind, location, message))
    assert not failures, '\n'.join(failures)


def test_harness_finds_and_shrinks_a_defect(tmp_path):
    # a parser that fails on a NaN token and on nothing else: the harness must classify it and cut the input down
    def run(ctx, content, tmp):
        if b'nan' in content:
            raise IndexError('boom')
        if b'bad' in content:
            raise fz_error()
        return None

    class fz_error(ValueError):
        pass

    target = fz.Target('fake', lambda: [('', 'text', b'a\nb\nnan\nc\n' * 4)], run, lambda: (fz_error,))
    assert fz.run_one(target, '', b'nan', tmp_path)[:2] == ('defect', 'IndexError')
    assert fz.run_one(target, '', b'bad', tmp_path)[0] == 'named'
    data = b'x' * 200 + b'nan' + b'y' * 200
    location = fz.run_one(target, '', data, tmp_path)[2]
    small = fz.shrink(data, lambda c: fz.run_one(target, '', c, tmp_path)[2] == location)
    assert small == b'nan'
    # the inputs are the same on every run
    assert list(target.inputs(12, 1)) == list(target.inputs(12, 1))


def test_result_check_flags_nan_and_invalid_geometry():
    class Result:
        layers = {1: {'solid_geometry': [Point(0, 0).buffer(1)]}}
        drill_groups = {}
    assert fz.check_result(Result) is None
    Result.drill_groups = {1: {'tooldia': 1.0, 'drills': [Point(math.nan, 0)], 'slots': []}}
    assert fz.check_result(Result) == 'NonFiniteGeometry'
