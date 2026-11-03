"""Stage 1 (reduce unnecessary inheritance): the shared app_option function and the ser_attrs duplicates.

Everything is headless: objects are built with a SimpleNamespace app and the VisPy ShapeCollection patched out.
"""

import os
import sys
from collections import Counter, defaultdict
from types import SimpleNamespace
from unittest.mock import Mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import camlib  # noqa: E402
from appObjects.AppObjectTemplate import FlatCAMObj  # noqa: E402
from appObjects.CNCJobObject import CNCJobObject  # noqa: E402
from appObjects.DocumentObject import DocumentObject  # noqa: E402
from appObjects.GeometryObject import GeometryObject  # noqa: E402
from appObjects.ScriptObject import ScriptObject  # noqa: E402
from appParsers.ParseExcellon import Excellon  # noqa: E402
from appParsers.ParseGerber import Gerber  # noqa: E402
from camlib import CNCjob, Geometry, app_option  # noqa: E402
# fixtures and helpers of the characterization tests (a fixture imported here is collected as ours)
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_same_tools, _headless_qt, make_excellon, make_gerber, save_and_load,
)


class Defaults(dict):
    def __init__(self, data=None, factory=None):
        super().__init__(data or {})
        self.factory_defaults = factory or {}


def fake_app(options=None, defaults=None):
    return SimpleNamespace(options=options or {}, defaults=defaults)


class TestAppOptionFallbackOrder:
    def test_options_win_over_defaults_and_factory(self):
        a = fake_app({'k': 1}, Defaults({'k': 2}, {'k': 3}))
        assert app_option(a, 'k', 4) == 1


    def test_fallback_last(self):
        a = fake_app({}, Defaults())
        assert app_option(a, 'k', 4) == 4
        assert app_option(a, 'k') is None


    def test_falsy_option_value_is_kept(self):
        a = fake_app({'k': 0}, Defaults({'k': 2}))
        assert app_option(a, 'k', 4) == 0


def build_objects(app):
    app.preprocessors = defaultdict(Mock)
    return {
        'Geometry': Geometry(app), 'Gerber': Gerber(app), 'Excellon': Excellon(app), 'CNCjob': CNCjob(app),
        'GerberObject': make_gerber(app), 'ExcellonObject': make_excellon(app),
        'GeometryObject': GeometryObject('geo', app), 'CNCJobObject': CNCJobObject('job', app),
    }


class TestSerAttrsHaveNoDuplicates:

    def test_old_layout_dict_loads_into_new_layout(self, app):
        """A project saved with the old (duplicated) ser_attrs has the same keys: it must load."""
        grb = make_gerber(app)
        new_keys = set(grb.to_dict())
        old_layout = list(grb.ser_attrs) + ['tools', 'solid_geometry']
        grb.ser_attrs = old_layout
        old_dict = grb.to_dict()
        assert set(old_dict) == new_keys

        other = make_gerber(app, name='other')
        other.from_dict(old_dict)
        assert other.units == grb.units
        assert sorted(other.tools) == sorted(grb.tools)

        app.preprocessors = defaultdict(Mock)
        job = CNCJobObject('job', app)
        job_keys = set(job.to_dict())
        job.ser_attrs = list(job.ser_attrs) + ['kind', 'tools']
        assert set(job.to_dict()) == job_keys


