import math
import os

import pytest
from shapely.geometry import Point

from tests import fuzz_parsers as fz

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Parser fuzz harness                                       #
# Author: Marius Stanciu (c)                                #
# Method after TRACE Boardviewer (MIT)                      #
# https://github.com/trace-boardviewer/trace-boardviewer    #
# MIT Licence                                               #
# ##########################################################


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
