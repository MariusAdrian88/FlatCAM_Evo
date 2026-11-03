"""The X2 attribute %TF.FileFunction is read before the %TF lines are removed from the text to parse."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.helpers_objects import app, _headless_qt, make_gerber  # noqa: F401,E402

FIX = Path(__file__).parent / 'fixtures' / 'cutout'


def parse(app, name):
    return make_gerber(app, str(FIX / name), name=name)


def test_profile_attribute_is_stored_in_obj_options(app):
    grb = parse(app, 'outline_region_x2.gbr')
    assert grb.obj_options['file_function'] == ['Profile', 'NP']
    assert grb.file_function == ['Profile', 'NP']


@pytest.mark.parametrize('name', ['outline_region_cutout.gbr'])
def test_no_attribute_gives_an_empty_list(app, name):
    assert parse(app, name).obj_options['file_function'] == []


def test_copper_function_value_list(app):
    assert type(parse(app, 'outline_single.gbr')).__mro__  # object builds
    from appParsers.ParseGerber import Gerber
    assert Gerber._read_file_function('%TF.FileFunction,Copper,L1,Top*%\nX') == ['Copper', 'L1', 'Top']
