"""Tests for the split of application settings and project options."""
import json
import os
import sys
import tempfile
import unittest
import threading
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appObjects.AppObject import AppObject
from defaults import AppDefaults, AppOptions, APP_TOOL_KEYS, PROJECT_KEYS, is_project_key, project_options


class TestIsProjectKey(unittest.TestCase):
    def test_tools_keys_are_project_keys(self):
        for key in ('tools_iso_tooldia', 'tools_drill_ppname_e', 'tools_mill_ppname_g', 'tools_ncc_operation'):
            self.assertTrue(is_project_key(key), key)
        for key in ('global_gridx', 'global_workspace', 'global_appearance', 'cncjob_tooldia',
                    'excellon_optimization_type', 'gerber_plot', 'units_precision', 'first_run',
                    'root_folder_path', None, 5):
            self.assertFalse(is_project_key(key), key)

class TestAppOptionsMergedView(unittest.TestCase):
    def make(self):
        defaults = AppDefaults(beta=False)
        calls = []
        options = AppOptions(version='test', callback=calls.append, baseline=defaults, defaults=defaults)
        return defaults, options, calls

    def test_application_key_reads_and_writes_defaults(self):
        defaults, options, calls = self.make()
        options['global_gridx'] = 7.5
        self.assertEqual(defaults['global_gridx'], 7.5)
        self.assertEqual(options['global_gridx'], 7.5)
        self.assertEqual(options.get('global_gridx'), 7.5)
        self.assertIn('global_gridx', options)
        self.assertEqual(calls, ['global_gridx'])
        defaults['global_gridx'] = 3.0
        self.assertEqual(options['global_gridx'], 3.0)

    def test_project_key_stays_in_own_storage(self):
        defaults, options, calls = self.make()
        before = defaults['gerber_bboxmargin']
        options['gerber_bboxmargin'] = before + 1.0
        self.assertEqual(options['gerber_bboxmargin'], before + 1.0)
        self.assertEqual(defaults['gerber_bboxmargin'], before)
        self.assertEqual(calls, ['gerber_bboxmargin'])

    def test_load_project_ignores_application_keys(self):
        defaults, options, _calls = self.make()
        appearance = defaults['global_appearance']
        options['gerber_bboxrounded'] = not defaults['gerber_bboxrounded']
        options.load_project({'global_appearance': 'not-a-theme', 'gerber_bboxmargin': 0.75,
                              'global_graphic_engine': '2D'})
        self.assertEqual(defaults['global_appearance'], appearance)
        self.assertEqual(options['gerber_bboxmargin'], 0.75)
        self.assertEqual(options['gerber_bboxrounded'], defaults['gerber_bboxrounded'])
        self.assertNotIn('global_graphic_engine', options)

    def test_load_project_converts_default_lengths_the_file_lacks(self):
        defaults, options, _calls = self.make()
        defaults['units'] = 'MM'
        options.load_project({'units': 'IN', 'gerber_bboxmargin': 0.5})
        self.assertAlmostEqual(options['excellon_drill_tooldia'], 0.8 / 25.4)
        self.assertEqual(options['gerber_bboxmargin'], 0.5)
        options.load_project({'units': 'MM'})
        self.assertEqual(options['excellon_drill_tooldia'], 0.8)

    def test_load_keeps_only_project_keys_unless_asked(self):
        defaults, options, _calls = self.make()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'prefs.FlatConfig')
            with open(path, 'w') as f:
                json.dump({'global_gridx': 42.0, 'gerber_bboxmargin': 0.5}, f)
            options.load(path, Mock())
            self.assertEqual(options['gerber_bboxmargin'], 0.5)
            self.assertNotEqual(defaults['global_gridx'], 42.0)
            options.load(path, Mock(), include_app_keys=True)
            self.assertEqual(defaults['global_gridx'], 42.0)
        self.assertTrue(all(is_project_key(key) for key in options.options))


class TestNewObjectOptionFill(unittest.TestCase):
    def fill(self, project_units, provided=None):
        defaults = AppDefaults(beta=False)
        options = AppOptions(version='test', baseline=defaults, defaults=defaults)
        options['units'] = project_units
        app = SimpleNamespace(options=options, defaults=defaults, log=Mock(), inform_shell=Mock(), inform=Mock())
        owner = SimpleNamespace(app=app, load_context=threading.local())
        owner._app_option = MethodType(AppObject._app_option, owner)
        made = []

        class FakeObject:
            tool_option_prefix = ''
            inherits_mill_options = False

            def __init__(self, name, app=None):
                self.obj_options = {}
                self.units = None
                made.append(self)

        def initialize(obj, _app):
            return False
        if provided is not None:
            initialize.provided_options = provided
        with patch('appObjects.AppObject.GerberObject', FakeObject), patch('appObjects.AppObject.CNCJobObject', FakeObject):
            for kind in ('gerber', 'cncjob'):
                AppObject._new_object(owner, kind, 'name', initialize)
        return defaults, made

    def test_application_lengths_follow_the_units_of_the_project(self):
        defaults, made = self.fill('IN')
        self.assertAlmostEqual(made[1].obj_options['tooldia'], defaults['cncjob_tooldia'] / 25.4)
        defaults, made = self.fill('MM')
        self.assertEqual(made[1].obj_options['tooldia'], defaults['cncjob_tooldia'])


if __name__ == '__main__':
    unittest.main()
