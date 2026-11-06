#!/usr/bin/env python
"""
Unit tests for the ToolLevelling methods that apply the probed height map
to a CNCJob's G-code (Task B of the autolevelling feature).

Run: python tests/test_levelling_tool.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import threading
import re
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, mock_open, patch

from PyQt6 import QtWidgets

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from shapely import Point, Polygon
from shapely.ops import unary_union

from appPlugins.ToolLevelling import ToolLevelling
from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
from appPlugins.Common.levelling_interp import parse_height_map_line
from copy import deepcopy
import math
import random
import pytest
import appPlugins.Common.levelling_core as lc_module
import copy

from test_levelling_audit import FakeGrbl, emitted, make_audit_tool, tl_module


def plane(x, y):
    return 0.01 * x - 0.02 * y + 0.1


def make_tool(units='MM', storage=None, heights_valid=True, cls=None):
    tool = (cls or ToolLevelling).__new__(cls or ToolLevelling)
    tool.app = MagicMock()
    tool.app.decimals = 4
    tool.app.inform = MagicMock()
    tool.app.log = MagicMock()
    tool.app.proc_container = MagicMock()
    tool.app.app_units = units
    tool.ui = MagicMock()
    tool.ui.plot_probing_pts_cb.get_value.return_value = False
    tool.units = units
    tool.al_voronoi_geo_storage = storage if storage is not None else {}
    tool._al_data_units = units if tool.al_voronoi_geo_storage else None
    tool.al_heights_valid = heights_valid
    tool.grbl_probe_result = ''
    tool.grbl_work_offset = (0.0, 0.0, 0.0)
    tool.build_al_table_sig = MagicMock()
    tool.apply_autolevel_sig = MagicMock()
    tool.show_probing_geo_sig = MagicMock()
    tool._al_apply_running = False
    tool.mouse_events_connected = False
    tool.app.collection = MagicMock()
    tool.app.app_obj = MagicMock()
    tool.app.worker_task = MagicMock()
    tool._al_generation = 0
    tool._grbl_op_lock = threading.Lock()
    tool._al_target_name = None
    tool._al_target_obj = None
    # publish results at once (the worker runs inline in these tests)
    tool.al_result_ready_sig = MagicMock()
    tool.al_result_ready_sig.emit.side_effect = lambda result: tool._on_al_result_ready(result)
    tool.console_line_sig = MagicMock()
    tool.job_map_generation = None
    tool.view3d = None
    tool._grbl_stream_dirty = False
    tool.stream_progress_sig = MagicMock()
    tool.status_report_sig = MagicMock()
    tool.spindle_state_sig = MagicMock()
    tool.stream_ended_sig = MagicMock()
    return tool


def make_tool_target(tools_gcode_by_key, **overrides):
    """A minimal SimpleNamespace target for autolevell_gcode() (the
    per-tool API): tools_gcode_by_key maps tooluid -> raw gcode text."""
    kwargs = dict(
        is_segmented_gcode=True,
        coords_decimals=4,
        units='MM',
        tools={
            key: {'gcode': text, 'data': {}}
            for key, text in tools_gcode_by_key.items()
        },
    )
    kwargs.update(overrides)
    return SimpleNamespace(**kwargs)


def make_storage_grid(xs, ys):
    storage = {}
    idx = 0
    for x in xs:
        for y in ys:
            storage[idx] = {
                'point': Point(x, y),
                'geo': None,
                'height': plane(x, y),
            }
            idx += 1
    return storage


class TestHeightMapCoverage(unittest.TestCase):
    @staticmethod
    def warnings(tool):
        return [call.args[0] for call in tool.app.inform.emit.call_args_list
                if 'outside the measured height map' in call.args[0]]

    def test_bilinear_warns_once_across_tools_without_changing_clamped_values(self):
        tool = make_tool(storage=make_storage_grid([0, 10], [0, 10]))
        target = make_tool_target({1: 'G21 G90\nG1 X20 Y0 Z-1\n',
                                   2: 'G1 X30 Y0\nG1 X40 Y0\n'})
        result = tool.autolevell_gcode(target, 'b')
        self.assertIn('Z-0.8000', result[1])
        self.assertEqual(result[2], 'G1 X30 Y0 Z-0.8000\nG1 X40 Y0 Z-0.8000\n')
        warnings = self.warnings(tool)
        self.assertEqual(len(warnings), 1)
        self.assertIn(': 3.', warnings[0])
        self.assertIn('reprobe', warnings[0])


class TestFiniteLevellingInput(unittest.TestCase):
    def test_non_finite_height_map_rows_are_invalid(self):
        for row in ('0.0,0.0,nan', '0.0,0.0,inf', '0.0,0.0,-inf'):
            with self.subTest(row=row):
                self.assertIsNone(parse_height_map_line(row))


GCODE_SAMPLE = (
    "G21\n"
    "G90\n"
    "G0 Z2.0000\n"
    "G0 X0.0000 Y0.0000\n"
    "G1 Z-0.1000 F100\n"
    "G1 X10.0000 Y5.0000\n"
    "G1 X20.0000 Y10.0000\n"
    "G0 Z2.0000\n"
)


class TestAutolevellGcodeBilinear(unittest.TestCase):
    def setUp(self):
        self.storage = make_storage_grid([0, 10, 20], [0, 5, 10])
        self.tool = make_tool(storage=self.storage)
        self.target = make_tool_target({1: GCODE_SAMPLE})

    def test_g1_lines_get_height_compensated(self):
        result = self.tool.autolevell_gcode(self.target, 'b')
        self.assertIsNotNone(result)
        lines = result[1].splitlines()

        # G0 lines are untouched
        self.assertEqual(lines[2], "G0 Z2.0000")
        self.assertEqual(lines[3], "G0 X0.0000 Y0.0000")

        # first G1: Z-0.1 at (0, 0)
        expected_z0 = -0.1 + plane(0.0, 0.0)
        self.assertEqual(lines[4], "G1 Z{0:.4f} F100".format(expected_z0))

        # second G1: modal Z carried from -0.1, moved to (10, 5)
        expected_z1 = -0.1 + plane(10.0, 5.0)
        self.assertEqual(lines[5], "G1 X10.0000 Y5.0000 Z{0:.4f}".format(expected_z1))

        # third G1: (20, 10)
        expected_z2 = -0.1 + plane(20.0, 10.0)
        self.assertEqual(lines[6], "G1 X20.0000 Y10.0000 Z{0:.4f}".format(expected_z2))


class TestAutolevellGcodeErrors(unittest.TestCase):
    def setUp(self):
        self.storage = make_storage_grid([0, 10], [0, 10])

    def test_not_segmented_returns_none_and_errors(self):
        tool = make_tool(storage=self.storage)
        target = make_tool_target({1: "G1 X0 Y0 Z0\n"}, is_segmented_gcode=False)
        result = tool.autolevell_gcode(target, 'b')
        self.assertIsNone(result)
        tool.app.inform.emit.assert_called()
        self.assertIn('[ERROR_NOTCL]', tool.app.inform.emit.call_args.args[0])


    def test_heights_not_valid_returns_none(self):
        # storage has entries (with placeholder 'height': 0.0, as created
        # when probe points are added) but they were never actually probed
        tool = make_tool(storage=self.storage, heights_valid=False)
        target = make_tool_target({1: "G1 X0 Y0 Z0\n"})
        result = tool.autolevell_gcode(target, 'b')
        self.assertIsNone(result)
        self.assertIn('[ERROR_NOTCL]', tool.app.inform.emit.call_args.args[0])


class TestHeightmapSave(unittest.TestCase):
    def _save(self, storage, heights_valid=True):
        tool = make_tool(storage=storage, heights_valid=heights_valid)
        tool.grbl_probe_result = 'ok\n[PRB:raw protocol]\n'
        tool.app.options = {'cncjob_line_ending': False}
        tool.app.get_last_save_folder.return_value = 'C:/tmp'
        output_file = mock_open()
        with patch(
                'appPlugins.ToolLevelling.FCFileSaveDialog.get_saved_filename',
                return_value=('C:/tmp/map.txt', '')
        ) as dialog, patch('builtins.open', output_file):
            tool.on_grbl_heightmap_save()
        return tool, dialog, output_file

    def test_saves_normalized_rows_in_storage_order(self):
        storage = {
            'first': {'point': Point(1.0, 2.0), 'height': -0.1},
            'second': {'point': Point(3.5, 4.25), 'height': 0.2},
        }
        _, _, output_file = self._save(storage)
        written = ''.join(call.args[0] for call in output_file().write.call_args_list)
        self.assertEqual(written, '1.0,2.0,-0.1\n3.5,4.25,0.2\n')
        self.assertNotIn('[PRB:', written)
        self.assertNotIn('ok', written)


class TestImportHeightMap(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, 'w') as f:
            f.write(content)
        return path


    def test_mach3_comma_format_empty_storage_creates_points(self):
        path = self._write('mach3.txt', "0.0,0.0,-0.010\n10.0,0.0,-0.020\n")
        tool = make_tool(storage={}, heights_valid=False)
        tool.import_height_map(path)

        self.assertTrue(tool.al_heights_valid)
        self.assertEqual(len(tool.al_voronoi_geo_storage), 2)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[1]['point'].x, 0.0)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[1]['height'], -0.010)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[2]['point'].x, 10.0)
        tool.build_al_table_sig.emit.assert_called_once()
        tool.apply_autolevel_sig.emit.assert_not_called()


    def test_storage_point_without_matching_row_errors_and_does_not_apply(self):
        storage = {
            'a': {'point': Point(0.0, 0.0), 'geo': None, 'height': None},
            'b': {'point': Point(10.0, 0.0), 'geo': None, 'height': None},
        }
        # only 1 row: point 'b' has no match
        path = self._write('missing.txt', "0.0,0.0,-0.01\n")
        tool = make_tool(storage=storage, heights_valid=False)
        tool.import_height_map(path)

        self.assertFalse(tool.al_heights_valid)
        tool.apply_autolevel_sig.emit.assert_not_called()
        error_calls = [
            c for c in tool.app.inform.emit.call_args_list
            if '[ERROR_NOTCL]' in c.args[0]
        ]
        self.assertTrue(error_calls)


class FakeNewCNCJob:
    """Minimal stand-in for a newly created CNCJobObject, enough for
    apply_autolevel()'s obj_init callback to run against."""

    def __init__(self):
        self.obj_options = {}
        self.tools = {}
        self.units = None
        self.multitool = None
        self.used_tools = None
        self.gc_start = None
        self.prepend_snippet = None
        self.append_snippet = None
        self.is_segmented_gcode = None
        self.gcode = ''
        self.gcode_parsed = None
        self.geometry_created = False
        self.solid_geometry = None
        self.pp_geometry_name = None
        self.pp_excellon_name = None
        self.pp_solderpaste_name = None
        self.exc_tools = None

    def gcode_parse(self, tool_data=None):
        return list(self.gcode.splitlines())

    def create_geometry(self):
        # mirrors camlib.CNCjob.create_geometry(): builds solid_geometry
        # from self.gcode_parsed (must already hold every tool's geometry,
        # not just the last tool processed)
        self.geometry_created = True
        self.solid_geometry = list(self.gcode_parsed) if self.gcode_parsed else []


def make_apply_target(tools_gcode_by_key):
    return SimpleNamespace(
        kind='cncjob',
        obj_options={'name': 'job1', 'type': 'Geometry'},
        units='MM',
        multitool=True,
        used_tools=list(tools_gcode_by_key.keys()),
        gc_start='G21\nG90\n',
        prepend_snippet='',
        append_snippet='',
        is_segmented_gcode=True,
        coords_decimals=4,
        tools={
            key: {'gcode': text, 'gcode_parsed': [], 'data': {}, 'tooldia': 1.0}
            for key, text in tools_gcode_by_key.items()
        },
    )


class TestApplyAutolevel(unittest.TestCase):
    def setUp(self):
        self.storage = make_storage_grid([0, 10, 20], [0, 5, 10])

    def _set_owner(self, tool, target):
        tool._al_target_name = target.obj_options['name']
        tool._al_target_obj = target
        tool._al_data_units = target.units.upper()
        tool.app.collection.get_by_name.return_value = target


    def test_units_mismatch_against_fresh_app_units_errors_and_creates_nothing(self):
        # target and self.units (stale) both say 'MM', but the app's *current*
        # units are 'IN'; AppObject.new_object() converts the new object's
        # units right after obj_init() runs (and CNCJobObject.convert_units()
        # does not scale G-code), so validating against a stale self.units
        # would let a units-mismatched height map through silently
        target = make_apply_target({1: "G1 X0 Y0 Z-0.1\n"})
        target.units = 'MM'
        tool = make_tool(units='MM', storage=self.storage)
        self._set_owner(tool, target)
        tool.app.app_units = 'IN'
        tool.app.collection.get_by_name.return_value = target
        tool.ui.al_method_radio.get_value.return_value = 'b'

        tool.apply_autolevel()
        tool.app.worker_task.emit.assert_not_called()

        tool.app.app_obj.new_object.assert_not_called()
        self.assertTrue(any('[ERROR_NOTCL]' in c.args[0] for c in tool.app.inform.emit.call_args_list))
        self.assertFalse(tool._al_apply_running)

    def _run_worker(self, tool, target, new_obj_factory=FakeNewCNCJob):
        """Trigger apply_autolevel(), capture the emitted worker_task, run
        it synchronously against a fake new object, and return that
        object (or None if new_object was never called)."""
        self._set_owner(tool, target)
        tool.ui.al_method_radio.get_value.return_value = 'b'

        captured = {}

        def fake_new_object(kind, name, obj_init, *args, **kwargs):
            captured['kind'] = kind
            captured['name'] = name
            new_obj = new_obj_factory()
            if obj_init(new_obj, tool.app) == 'fail':
                return 'fail'
            captured['new_obj'] = new_obj
            return 'success'

        tool.app.app_obj.new_object.side_effect = fake_new_object

        tool.apply_autolevel()
        self.assertTrue(tool._al_apply_running)

        worker_dict = tool.app.worker_task.emit.call_args.args[0]
        worker_dict['fcn']()

        return captured


    def test_apply_against_real_cncjob_preserves_all_tools_geometry(self):

        target = make_apply_target({
            1: 'G21\nG90\nG0 X0 Y0 Z2\nG1 Z-0.1\nG1 X5 Y0\n',
            2: 'G1 X10 Y0\n',
        })
        tool = make_tool(storage=self.storage)
        captured = self._run_worker(tool, target, new_obj_factory=make_real_cncjob)
        obj = captured['new_obj']

        self.assertNotEqual(obj.tools[1]['gcode'], target.tools[1]['gcode'])
        self.assertIn('Z0.1000', obj.tools[2]['gcode'])
        self.assertTrue(obj.tools[1]['gcode_parsed'])
        self.assertTrue(obj.tools[2]['gcode_parsed'])
        self.assertEqual(len(obj.gcode_parsed), sum(
            len(value['gcode_parsed']) for value in obj.tools.values()))
        self.assertTrue(obj.solid_geometry)
        self.assertEqual(obj.used_tools, target.used_tools)


    def test_apply_stops_for_unsupported_preprocessor(self):
        target = make_apply_target({1: "G1 X0 Y0 Z-0.1\n"})
        target.pp_geometry_name = 'Roland_test'
        tool = make_tool(storage=self.storage)
        self._set_owner(tool, target)
        tool.ui.al_method_radio.get_value.return_value = 'b'

        tool.apply_autolevel()
        tool.app.worker_task.emit.assert_not_called()

        tool.app.app_obj.new_object.assert_not_called()
        self.assertFalse(tool._al_apply_running)


class TestApplyInputFreshness(unittest.TestCase):
    def setUp(self):
        self.target = make_apply_target({
            1: 'G21\nG90\nG1 X0 Y0 Z-0.1000\n',
            2: 'X10 Y0\n',
        })
        self.tool = make_tool(storage=make_storage_grid([0, 10], [0, 10]))
        self.tool._al_target_name = 'job1'
        self.tool._al_target_obj = self.target
        self.tool.app.collection.get_by_name.return_value = self.target
        self.tool.ui.al_method_radio.get_value.return_value = 'b'
        self.outputs = []

        def new_object(kind, name, initializer):
            obj = FakeNewCNCJob()
            if initializer(obj, self.tool.app) == 'fail':
                return 'fail'
            self.outputs.append(obj)
            return 'success'

        self.tool.app.app_obj.new_object.side_effect = new_object

    def schedule(self):
        self.tool.apply_autolevel(self.target)
        self.tool.app.worker_task.emit.assert_called_once()
        self.assertTrue(self.tool._al_apply_running)
        return self.tool.app.worker_task.emit.call_args.args[0]['fcn']

    def assert_original_map_or_refusal(self):
        for obj in self.outputs:
            self.assertIn('Z0.0000', obj.tools[1]['gcode'])
            self.assertIn('Z0.1000', obj.tools[2]['gcode'])
        self.assertFalse(self.tool._al_apply_running)


    def test_delayed_worker_never_reads_replaced_storage(self):
        worker = self.schedule()
        replacement = make_storage_grid([0, 10], [0, 10])
        for value in replacement.values():
            value['height'] = 5.0
        self.tool.al_voronoi_geo_storage = replacement
        worker()
        self.assert_original_map_or_refusal()


    def test_changed_project_units_reject_delayed_apply(self):
        worker = self.schedule()
        self.tool.app.app_units = 'IN'
        worker()
        self.tool.app.app_obj.new_object.assert_not_called()
        self.assertFalse(self.tool._al_apply_running)

    def test_same_name_target_replacement_rejects_delayed_apply(self):
        worker = self.schedule()
        replacement = make_apply_target({1: 'G1 X0 Y0 Z-0.1000\n'})
        self.tool.app.collection.get_by_name.return_value = replacement
        worker()
        self.tool.app.app_obj.new_object.assert_not_called()
        self.assertFalse(self.tool._al_apply_running)


class TestApplyCompatibility(unittest.TestCase):
    # Keep compatibility checks separate from the expected RED regressions.
    setUp = TestApplyInputFreshness.setUp
    schedule = TestApplyInputFreshness.schedule
    assert_original_map_or_refusal = TestApplyInputFreshness.assert_original_map_or_refusal

    def test_unchanged_apply_preserves_source_and_per_tool_modal_state(self):
        source = deepcopy(self.target.tools)
        worker = self.schedule()
        worker()
        self.assertEqual(len(self.outputs), 1)
        self.assert_original_map_or_refusal()
        self.assertEqual(self.target.tools, source)
        obj = self.outputs[0]
        self.assertEqual(obj.units, self.target.units)
        self.assertEqual(obj.used_tools, self.target.used_tools)
        self.assertTrue(obj.geometry_created)
        self.assertEqual(obj.gcode, ''.join(t['gcode'] for t in obj.tools.values()))


class TestDirectMapUnits(unittest.TestCase):
    setUp = TestApplyInputFreshness.setUp

    def test_direct_call_rejects_mm_map_for_in_target_despite_tool_units(self):
        self.assertEqual(self.tool._al_data_units, 'MM')
        self.tool.units = 'IN'
        self.target.units = 'IN'
        self.target.tools[1]['gcode'] = self.target.tools[1]['gcode'].replace('G21', 'G20')
        source = deepcopy(self.target.tools)

        result = self.tool.autolevell_gcode(self.target, 'b')

        self.assertIsNone(result)
        self.assertEqual(self.target.tools, source)
        self.assertTrue(any('[ERROR_NOTCL]' in call.args[0]
                            for call in self.tool.app.inform.emit.call_args_list))


#!/usr/bin/env python


_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

# appParsers.ParseGerber (transitively) imports appMain, whose module-level
# code parses sys.argv with getopt; running this file as
# `python tests/test_levelling_journey.py -v` would otherwise blow up with a
# GetoptError on our own `-v` flag. Neutralize sys.argv for the duration of
# these FlatCAM imports only.
_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
try:
    from shapely import Point
    from shapely.ops import unary_union

    from appParsers.ParseGerber import Gerber
    from appPlugins.ToolLevelling import ToolLevelling
    from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
    from appPlugins.Common.grbl.grbl_protocol import parse_grbl_work_offset
    import camlib

    # appParsers.ParseGerber.Gerber.__init__() itself does a *lazy*
    # `from appMain import App` the first time a Gerber() is constructed
    # (not at module import time), which would otherwise re-trigger the
    # sys.argv parsing later, well after this block has restored argv.
    # Force that import to happen now, once, while argv is still patched,
    # so it's cached in sys.modules for every Gerber() built by the tests.
    import appMain  # noqa: F401
finally:
    sys.argv = _saved_argv


TEST_FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_files')
SIMPLE_LINE_GBR = os.path.join(TEST_FILES_DIR, 'simple_line.gbr')
REGION_GBR = os.path.join(TEST_FILES_DIR, 'region_test.gbr')
FLASH_GBR = os.path.join(TEST_FILES_DIR, 'flash_test.gbr')
ALL_GERBERS = [SIMPLE_LINE_GBR, REGION_GBR, FLASH_GBR]


# ---------------------------------------------------------------------------
# Gerber -> toolpaths
# ---------------------------------------------------------------------------

class MockGerberLog:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass

    def info(self, msg):
        pass


class MockGerberInform:
    def emit(self, msg):
        pass


class MockGerberApp:
    """Same pattern as tests/test_gerber_parser.py's MockApp."""

    def __init__(self):
        self.options = {
            'gerber_def_units': 'MM',
            'gerber_def_zeros': 'L',
            'gerber_circle_steps': 64,
            'gerber_simp_tolerance': 0.001,
            'gerber_simplification': True,
            'gerber_buffering': True,
            'gerber_extra_buffering': 0.0,
            'gerber_clean_apertures': True,
            'gerber_use_buffer_for_union': True,
            'global_tolerance': 0.01,
        }
        self.decimals = 4
        self.abort_flag = False
        self.log = MockGerberLog()
        self.inform = MockGerberInform()
        self.app_units = 'MM'

        class MockPlotCanvas:
            class new_shape_collection:
                def __init__(self, layers=1):
                    pass

            def __init__(self):
                pass

        self.plotcanvas = MockPlotCanvas()

        class MockProcContainer:
            def update_view_text(self, text):
                pass

        self.proc_container = MockProcContainer()


def gerber_toolpaths(path, tool_dia=1.0):
    """Parse a Gerber file and build isolation-routing toolpaths: the
    exterior/interior rings of solid_geometry buffered outward by
    tool_dia/2. Returns a list of coordinate paths (list of (x, y)
    tuples, closed rings), always in mm."""
    gbr = Gerber(MockGerberApp())
    gbr.parse_file(path)

    geoms = gbr.solid_geometry
    union = unary_union(geoms) if isinstance(geoms, list) else geoms
    outline = union.buffer(tool_dia / 2.0)

    polys = list(outline.geoms) if hasattr(outline, 'geoms') else [outline]

    paths = []
    for poly in polys:
        paths.append(list(poly.exterior.coords))
        for interior in poly.interiors:
            paths.append(list(interior.coords))

    if gbr.units == 'IN':
        paths = [[(x * 25.4, y * 25.4) for x, y in ring] for ring in paths]

    return paths


def paths_bounds(paths, pad_frac=0.1):
    xs = [x for ring in paths for x, y in ring]
    ys = [y for ring in paths for x, y in ring]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    padx = max((xmax - xmin) * pad_frac, 1.0)
    pady = max((ymax - ymin) * pad_frac, 1.0)
    return xmin - padx, xmax + padx, ymin - pady, ymax + pady


def make_grid_points(bounds, nx=4, ny=4):
    xmin, xmax, ymin, ymax = bounds
    xs = [xmin + i * (xmax - xmin) / (nx - 1) for i in range(nx)]
    ys = [ymin + j * (ymax - ymin) / (ny - 1) for j in range(ny)]
    return xs, ys


# ---------------------------------------------------------------------------
# G-code generation (FlatCAM/GRBL_11-style, segmented)
# ---------------------------------------------------------------------------

def _subdivide_path(points, max_len):
    if not points:
        return points
    out = [points[0]]
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist <= max_len or dist == 0:
            out.append((x1, y1))
            continue
        n = int(math.ceil(dist / max_len))
        for i in range(1, n + 1):
            t = i / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


def make_gcode(paths, cut_z=-0.1, travel_z=2.0, feed=300.0, segment_len=5.0,
               decimals=4, spindle=10000, include_arc=False):
    """Build FlatCAM/GRBL_11-style segmented G-code for a list of toolpaths.
    Guaranteed: no G01 cutting segment longer than segment_len (is_segmented
    semantics); at least 1 trailing inline comment; with include_arc, 1 G02
    arc line (at travel height). An arc is split into G1 lines by the
    levelling, so verify_levelling() (same line count) can not check a job
    with an arc: the arcs are checked in tests/test_levelling_arc_g91.py."""

    def f(v):
        return "{0:.{1}f}".format(v, decimals)

    lines = [
        "(FlatCAM Gcode Generator)",
        "(This is a Test)",
        "G21",
        "G90",
        "G94",
        "F" + f(feed),
        "M03 S%d" % spindle,
    ]

    last_x, last_y = 0.0, 0.0
    for idx, ring in enumerate(paths):
        pts = _subdivide_path(ring, segment_len)
        x0, y0 = pts[0]
        comment = " (start path %d)" % idx if idx == 0 else ""
        lines.append("G00 Z" + f(travel_z) + comment)
        lines.append("G00 X" + f(x0) + " Y" + f(y0))
        lines.append("G01 Z" + f(cut_z) + " F" + f(feed))
        for x, y in pts[1:]:
            lines.append("G01 X" + f(x) + " Y" + f(y))
        lines.append("G00 Z" + f(travel_z))
        last_x, last_y = x0, y0

    if include_arc:
        # Semicircle at travel height (Z already lifted above 0).
        cx, cy = last_x + 1.0, last_y
        x2, y2 = last_x + 2.0, last_y
        lines.append("G02 X" + f(x2) + " Y" + f(y2) + " I1.0 J0.0 (return arc)")

    # A rapid at or below Z0: rapids are never height-compensated regardless
    # of their Z value, only cutting (G1) moves are. Without at least 1 rapid
    # sitting at Z<=0 a mutant that also levels G0 moves would go unnoticed
    # (every other G0 in this file is at the travel height, which is > 0).
    lines.append("G00 Z-0.0500 (rapid at Z<=0 - must never be levelled)")
    lines.append("G00 X0.0000 Y0.0000")

    lines += ["M05", "G00 X0.0000 Y0.0000", "M2"]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------

def make_plane(a, b, c):
    def fn(x, y):
        return a * x + b * y + c
    return fn


def nearest_height_bruteforce(points, x, y):
    """points: list of (x, y, height). Independent re-implementation (does
    not call levelling_interp.nearest_offset)."""
    best = None
    best_d = None
    for px, py, pz in points:
        d = math.hypot(px - x, py - y)
        if best_d is None or d < best_d:
            best_d = d
            best = pz
    return best


# ---------------------------------------------------------------------------
# Height-map file writers (real files consumed by the real import_height_map)
# ---------------------------------------------------------------------------

def write_mach3(path, points, decimals=5):
    with open(path, 'w') as f:
        for x, y, z in points:
            f.write(("%.{0}f,%.{0}f,%.{0}f\n".format(decimals)) % (x, y, z))


def write_mach4_extra_columns(path, points, decimals=5):
    with open(path, 'w') as f:
        for x, y, z in points:
            f.write(("%.{0}f, %.{0}f, %.{0}f, 0.0, 0.0, 0.0\n".format(decimals)) % (x, y, z))


def write_linuxcnc(path, points, decimals=5):
    with open(path, 'w') as f:
        for x, y, z in points:
            f.write(("%.{0}f %.{0}f %.{0}f 0 0 0 0 0 0\n".format(decimals)) % (x, y, z))


def prefill_storage(grid_points):
    """Mimic on_add_grid_points(): storage pre-filled with the probe grid
    points (height 0.0, not yet probed) before the height-map file/probe
    result comes back. Used so import_height_map() must go through its
    "match existing storage points by nearest coordinate" branch instead of
    the "storage is empty, create points from file rows" branch."""
    return {
        i: {'point': Point(x, y), 'geo': None, 'height': 0.0}
        for i, (x, y, _z) in enumerate(grid_points)
    }


def shuffled_rows(points, seed=1234):
    """Return `points` in a different order than the canonical grid order,
    as a real probe-log file would not necessarily be written back in the
    same order the storage dict was built in."""
    rows = list(points)
    random.Random(seed).shuffle(rows)
    return rows


def make_grbl_offset_text(g54=(-100.0, -50.0, -20.0), g92=(0.0, 0.0, 0.0), tlo=0.0):
    return (
        "[G54:%.3f,%.3f,%.3f]\n" % g54 +
        "[G92:%.3f,%.3f,%.3f]\n" % g92 +
        "[TLO:%.3f]\n" % tlo +
        "ok\n"
    )


def make_grbl_probe_text(points, offset, noise=0.0125):
    ox, oy, oz = offset
    lines = []
    for x, y, z in points:
        mx = x + noise + ox
        my = y - noise + oy
        mz = z + oz
        lines.append("[PRB:%.4f,%.4f,%.4f:1]" % (mx, my, mz))
        lines.append("ok")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Independent G-code reader/verifier (does NOT call level_gcode_line/codes_split)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r'([A-Z])([+\-]?\d*\.?\d+)')


def _line_words(line):
    code = line.split('(')[0]
    return {m.group(1): float(m.group(2)) for m in _WORD_RE.finditer(code)}


def verify_levelling(tc, original_text, levelled_text, decimals, offset_fn, tol=None):
    """Walk `original_text` and `levelled_text` line-by-line with an
    independent modal-state tracker and check:
      - same number of lines
      - every non-cut-move line is byte-identical
      - X/Y never change anywhere
      - every G1 cut line (any modal Z) got Z = original modal Z + offset_fn(x, y)
      - a line with no Z word originally now has one after levelling

    Returns (max_abs_dz, cut_points) where cut_points is the list of
    (x, y) at every cut line encountered (for range/sanity checks).
    """
    if tol is None:
        tol = 2 * 10 ** (-decimals)
    orig_lines = original_text.splitlines()
    lev_lines = levelled_text.splitlines()
    tc.assertEqual(len(orig_lines), len(lev_lines), "levelled line count differs from source")

    modal_g = None
    modal_x = 0.0
    modal_y = 0.0
    modal_z = 0.0
    max_abs_dz = 0.0
    cut_points = []

    for lineno, (orig_line, lev_line) in enumerate(zip(orig_lines, lev_lines)):
        ow = _line_words(orig_line)
        lw = _line_words(lev_line)

        has_xyz = ('X' in ow) or ('Y' in ow) or ('Z' in ow)

        if 'G' in ow and ow['G'] in (0.0, 1.0, 2.0, 3.0):
            modal_g = ow['G']

        if 'X' in ow:
            modal_x = ow['X']
        if 'Y' in ow:
            modal_y = ow['Y']
        if 'Z' in ow:
            modal_z = ow['Z']

        tc.assertEqual(ow.get('X'), lw.get('X'), "line %d: X changed (%r vs %r)" % (lineno, orig_line, lev_line))
        tc.assertEqual(ow.get('Y'), lw.get('Y'), "line %d: Y changed (%r vs %r)" % (lineno, orig_line, lev_line))

        # A G0 word on this exact line always means "rapid, never a cutting
        # move" regardless of Z, even if a bug elsewhere left modal_g stale;
        # checked explicitly (not just via modal_g == 1.0) so a mutant that
        # widens the cutting-move check to include G0 is caught even at a
        # rapid whose Z happens to be <= 0.
        is_g0_line = 'G' in ow and ow['G'] == 0.0
        is_cut_line = has_xyz and modal_g == 1.0 and not is_g0_line

        if not is_cut_line:
            tc.assertEqual(orig_line, lev_line, "line %d should be untouched: %r vs %r" % (lineno, orig_line, lev_line))
            continue

        tc.assertIn('Z', lw, "line %d: missing Z after levelling: %r" % (lineno, lev_line))
        expected_z = modal_z + offset_fn(modal_x, modal_y)
        tc.assertAlmostEqual(lw['Z'], expected_z, delta=tol,
                              msg="line %d: bad levelled Z (%r vs %r)" % (lineno, lev_line, orig_line))

        dz = lw['Z'] - modal_z
        max_abs_dz = max(max_abs_dz, abs(dz))
        cut_points.append((modal_x, modal_y))

    return max_abs_dz, cut_points


class LevellingJourneyTestCase(unittest.TestCase):
    """Common assertions shared by every journey test."""

    def assert_journey(self, tool, target, tooluid, al_method, offset_fn, expect_warning=None, tol=None):
        original = target.tools[tooluid]['gcode']
        result = tool.autolevell_gcode(target, al_method)
        self.assertIsNotNone(result, "autolevell_gcode() returned None")

        # source untouched
        self.assertEqual(target.tools[tooluid]['gcode'], original)

        max_abs_dz, cut_points = verify_levelling(
            self, original, result[tooluid], target.coords_decimals, offset_fn, tol=tol
        )

        # sanity check: levelling really happened (not a no-op)
        self.assertGreater(len(cut_points), 0, "no cut lines found to verify")
        expected_max_abs = max(abs(offset_fn(x, y)) for x, y in cut_points)
        range_tol = tol if tol is not None else 2 * 10 ** (-target.coords_decimals)
        self.assertAlmostEqual(max_abs_dz, expected_max_abs, delta=range_tol)
        self.assertGreater(max_abs_dz, 1e-6, "levelling produced no change at all")

        if expect_warning is not None:
            warning_calls = [
                c for c in tool.app.inform.emit.call_args_list
                if '[WARNING_NOTCL]' in c.args[0] and expect_warning in c.args[0]
            ]
            self.assertEqual(len(warning_calls), 1)

        return result


# ---------------------------------------------------------------------------
# Journey 1: each Gerber x each height-map source, tilted plane, method 'b'
# ---------------------------------------------------------------------------

class TestJourneyPlaneAllSources(LevellingJourneyTestCase):
    def test_plane_bilinear_all_gerbers_all_sources(self):
        # 'mach3_empty_storage' keeps import_height_map()'s "storage is empty,
        # create points straight from the file rows" branch covered. The
        # other 3 file-based sources pre-fill storage (as on_add_grid_points()
        # does before a real probe/import) and write rows out of order and
        # rounded to 3 decimals (as a controller log would), so
        # import_height_map() is forced through its "match an existing
        # storage point by nearest coordinate" branch instead.
        sources = ['mach3', 'mach3_empty_storage', 'mach4', 'linuxcnc', 'grbl']
        for gerber_path in ALL_GERBERS:
            paths = gerber_toolpaths(gerber_path)
            gcode_text = make_gcode(paths)
            bounds = paths_bounds(paths)
            xs, ys = make_grid_points(bounds, nx=4, ny=4)
            plane_fn = make_plane(0.0003, -0.0002, 0.05)
            grid_points = [(x, y, plane_fn(x, y)) for x in xs for y in ys]

            # Rows written with only 3 decimals of height precision (as a
            # controller log would) can be off from the exact plane value by
            # up to half a thousandth of a mm; the verifier's tolerance is
            # widened accordingly for those 3 sources.
            rounded_height_tol = 6e-4

            for source in sources:
                with self.subTest(gerber=os.path.basename(gerber_path), source=source):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        target = make_tool_target({1: gcode_text}, units='MM')
                        tol = None

                        if source == 'mach3':
                            storage = prefill_storage(grid_points)
                            tool = make_tool(units='MM', storage=storage, heights_valid=False)
                            path = os.path.join(tmpdir, 'hm.txt')
                            write_mach3(path, shuffled_rows(grid_points), decimals=3)
                            tool.import_height_map(path)
                            tol = rounded_height_tol
                        elif source == 'mach3_empty_storage':
                            tool = make_tool(units='MM', storage={}, heights_valid=False)
                            path = os.path.join(tmpdir, 'hm.txt')
                            write_mach3(path, grid_points)
                            tool.import_height_map(path)
                        elif source == 'mach4':
                            storage = prefill_storage(grid_points)
                            tool = make_tool(units='MM', storage=storage, heights_valid=False)
                            path = os.path.join(tmpdir, 'hm.txt')
                            write_mach4_extra_columns(path, shuffled_rows(grid_points, seed=42), decimals=3)
                            tool.import_height_map(path)
                            tol = rounded_height_tol
                        elif source == 'linuxcnc':
                            storage = prefill_storage(grid_points)
                            tool = make_tool(units='MM', storage=storage, heights_valid=False)
                            path = os.path.join(tmpdir, 'hm.txt')
                            write_linuxcnc(path, shuffled_rows(grid_points, seed=99), decimals=3)
                            tool.import_height_map(path)
                            tol = rounded_height_tol
                        else:  # grbl
                            storage = prefill_storage(grid_points)
                            tool = make_tool(units='MM', storage=storage, heights_valid=False,
                                             cls=ToolGCodeSender)
                            offset_text = make_grbl_offset_text()
                            tool.grbl_work_offset = parse_grbl_work_offset(offset_text)
                            # each G38.2 reply goes through the real per-probe check, then the
                            # complete result is published through the real GUI-thread slot
                            probe_text = make_grbl_probe_text(grid_points, tool.grbl_work_offset)
                            replies = [r for r in probe_text.split('ok\n') if r.strip()]
                            points = {k: (v['point'].x, v['point'].y) for k, v in storage.items()}
                            tol = tool._al_match_tolerance(list(points.values()))
                            heights = {
                                key: tool._al_check_probe_reply('G38.2', reply, key, points, tol,
                                                                tool.grbl_work_offset, 1.0)
                                for key, reply in zip(sorted(points), replies)
                            }
                            tool._on_al_result_ready({'kind': 'import', 'context': tool._al_capture_context(),
                                                      'heights': heights})

                        self.assertTrue(tool.al_heights_valid)
                        self.assert_journey(tool, target, 1, 'b', plane_fn, tol=tol)


# ---------------------------------------------------------------------------
# Journey 2: saddle surface, method 'b'
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Journey 3: irregular grid falls back to nearest, method 'b' (1 warning)
# ---------------------------------------------------------------------------

class TestJourneyIrregularGridFallback(LevellingJourneyTestCase):
    def test_irregular_grid_falls_back_to_nearest(self):
        for gerber_path in ALL_GERBERS:
            with self.subTest(gerber=os.path.basename(gerber_path)):
                paths = gerber_toolpaths(gerber_path)
                gcode_text = make_gcode(paths)
                bounds = paths_bounds(paths)
                xs, ys = make_grid_points(bounds, nx=3, ny=3)
                plane_fn = make_plane(0.0004, 0.0001, 0.03)
                grid_points = [(x, y, plane_fn(x, y)) for x in xs for y in ys]

                # Break the regular grid: nudge 1 point off its column/row.
                bad_x, bad_y, bad_z = grid_points[-1]
                grid_points[-1] = (bad_x + 7.0, bad_y + 3.0, bad_z)

                with tempfile.TemporaryDirectory() as tmpdir:
                    tool = make_tool(units='MM', storage={}, heights_valid=False)
                    path = os.path.join(tmpdir, 'hm.txt')
                    write_mach3(path, grid_points)
                    tool.import_height_map(path)
                    self.assertTrue(tool.al_heights_valid)

                    target = make_tool_target({1: gcode_text}, units='MM')

                    def nearest_fn(x, y):
                        return nearest_height_bruteforce(grid_points, x, y)

                    self.assert_journey(
                        tool, target, 1, 'b', nearest_fn,
                        expect_warning="Probe points do not form a regular grid"
                    )


# ---------------------------------------------------------------------------
# Journey 4: bump map, method 'v' (nearest point, no grid requirement)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Journey 5: multi-tool, modal state carries across tools in dict order
# ---------------------------------------------------------------------------

class TestJourneyMultiTool(LevellingJourneyTestCase):
    def test_modal_state_carries_across_tools_from_two_gerbers(self):
        paths_a = gerber_toolpaths(SIMPLE_LINE_GBR)
        paths_b = gerber_toolpaths(REGION_GBR)
        ax0, ay0 = paths_a[0][0]
        bx0, by0 = paths_b[0][0]

        # Tool 9: a normal cut move that sets modal Z = -0.15 and leaves the
        # machine there (no rapid lift at the end).
        gcode_1 = (
            "G21\nG90\nG94\n"
            "G00 X{0:.4f} Y{1:.4f}\n"
            "G01 Z-0.1500 F300\n"
        ).format(ax0, ay0)

        # Tool 3: no G/Z word at all on its only line - if the modal state
        # were reset between tools, this would not be recognized as a
        # cutting move and would be left untouched.
        gcode_2 = "X{0:.4f} Y{1:.4f}\n".format(bx0, by0)

        target = SimpleNamespace(
            is_segmented_gcode=True, coords_decimals=4, units='MM',
            tools={
                9: {'gcode': gcode_1, 'data': {}},
                3: {'gcode': gcode_2, 'data': {}},
            },
        )

        all_paths = paths_a + paths_b
        bounds = paths_bounds(all_paths)
        xs, ys = make_grid_points(bounds, nx=4, ny=4)
        plane_fn = make_plane(0.0002, -0.00015, 0.02)
        grid_points = [(x, y, plane_fn(x, y)) for x in xs for y in ys]

        with tempfile.TemporaryDirectory() as tmpdir:
            tool = make_tool(units='MM', storage={}, heights_valid=False)
            path = os.path.join(tmpdir, 'hm.txt')
            write_mach3(path, grid_points)
            tool.import_height_map(path)
            self.assertTrue(tool.al_heights_valid)

            result = tool.autolevell_gcode(target, 'b')
            self.assertIsNotNone(result)

            # tool 9's cut line got compensated
            z9 = _line_words(result[9].splitlines()[-1])['Z']
            self.assertAlmostEqual(z9, -0.15 + plane_fn(ax0, ay0), delta=1e-3)

            # tool 3's bare "X.. Y.." line inherited tool 9's modal G1/Z=-0.15
            # and got compensated too - proof the state carried across tools.
            lev_line_3 = result[3].strip()
            self.assertIn('Z', _line_words(lev_line_3))
            z3 = _line_words(lev_line_3)['Z']
            self.assertAlmostEqual(z3, -0.15 + plane_fn(bx0, by0), delta=1e-3)

            # source objects unchanged
            self.assertEqual(target.tools[9]['gcode'], gcode_1)
            self.assertEqual(target.tools[3]['gcode'], gcode_2)


# ---------------------------------------------------------------------------
# Real-object test (reviewer request): run apply_autolevel()'s obj_init
# against a REAL camlib.CNCjob-based object, not a fake, so that attribute
# bugs in obj_init are not hidden.
#
# A real appObjects.CNCJobObject.CNCJobObject is not used here: its
# __init__ (appObjects/CNCJobObject.py) calls build_ui()/build_cnc_tools_table()
# and friends, which need a live app.ui / FlatCAMObj plumbing far beyond what
# a headless unit test should stand up. camlib.CNCjob (the pure geometry/CNC
# base class CNCJobObject also inherits from) is used instead, with a
# minimal app double satisfying only what CNCjob.__init__()/gcode_parse()/
# create_geometry() actually read (see camlib.py Geometry.__init__/CNCjob.__init__).
# ---------------------------------------------------------------------------

def make_real_cncjob_app():
    return SimpleNamespace(
        decimals=4,
        app_units='MM',
        plotcanvas=SimpleNamespace(new_shape_collection=lambda layers=1: None),
        options={
            'cncjob_steps_per_circle': 64,
            'cncjob_coords_type': 'G90',
            'cncjob_bed_max_x': 0,
            'cncjob_bed_max_y': 0,
            'cncjob_bed_offset_x': 0,
            'cncjob_bed_offset_y': 0,
            'cncjob_bed_skew_x': 0,
            'cncjob_bed_skew_y': 0,
        },
        preprocessors={'default': object()},
        inform=MagicMock(),
        log=MagicMock(),
    )


def make_real_cncjob():
    app = make_real_cncjob_app()
    obj = camlib.CNCjob(
        app=app, units='MM', pp_geometry_name='default', pp_excellon_name='default',
        steps_per_circle=64,
    )
    obj.obj_options = {'type': 'Geometry', 'name': 'job1'}
    obj.tools = {}
    obj.multitool = True
    obj.used_tools = []
    obj.gc_start = ''
    obj.prepend_snippet = ''
    obj.append_snippet = ''
    obj.is_segmented_gcode = True
    return obj


def import_tool():
    target = MagicMock()
    target.kind = 'cncjob'
    target.obj_options = {'name': 'job'}
    storage = {
        1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.0},
        2: {'point': Point(10.0, 0.0), 'geo': None, 'height': 0.0},
    }
    tool = make_audit_tool(storage=storage, cls=ToolLevelling)
    tool._al_target_name = 'job'
    tool._al_target_obj = target
    tool.app.collection.get_by_name.return_value = target
    tool.apply_autolevel = MagicMock()
    return tool


def ctx_result(tool, kind, **extra):
    result = {'kind': kind, 'context': tool._al_capture_context()}
    result.update(extra)
    return result


# ---------------------------------------------------------------- defect 1


# ---------------------------------------------------------------- defect 2
GCODE_LINE = re.compile(r'^(\(.*\)|[GMO#%]\S*.*)$')


def gcode_tool(controller):
    tool = make_audit_tool(cls=ToolLevelling)
    target = MagicMock()
    target.obj_options = {'name': 'job'}
    target.coords_decimals = 4
    target.fr_decimals = 4
    tool.app.collection.get_by_name.return_value = target
    tool.app.dec_format.side_effect = lambda value, dec=4: round(value, 4)
    tool.app.version = '1'
    tool.app.version_date = 'x'
    tool.ui.al_method_radio.get_value.return_value = 'v'
    tool.ui.al_controller_combo.get_value.return_value = controller
    tool.ui.ptravelz_entry.get_value.return_value = 2.0
    tool.ui.feedrate_probe_entry.get_value.return_value = 50.0
    tool.ui.pdepth_entry.get_value.return_value = -1.0
    return tool


def storage():
    return {1: {'point': Point(1.0, 2.0), 'geo': None, 'height': 0.0}}


@pytest.mark.parametrize('controller', ['MACH3', 'LinuxCNC'])
def test_probing_code_has_no_bare_prose(controller):
    tool = gcode_tool(controller)
    text = tool.probing_gcode(storage=storage())
    for line in text.splitlines():
        if not line.strip():
            continue
        assert GCODE_LINE.match(line.strip()), 'prose line in G-code: %r' % line
        if line.startswith('('):
            assert '(' not in line[1:-1] and ')' not in line[1:-1]


# ---------------------------------------------------------------- defect 4
@pytest.mark.parametrize('new', ['LinuxCNC'])
def test_controller_change_drops_old_controller_code(new):
    tool = gcode_tool('MACH3')
    tool.on_controller_change_alter_ui = MagicMock()
    tool.ui.object_combo.get_value.return_value = 'job'
    tool.probing_gcode_text = tool.probing_gcode(storage=storage())
    assert 'G31' in tool.probing_gcode_text
    tool.al_voronoi_geo_storage = storage()
    tool.ui.al_controller_combo.get_value.return_value = new
    tool.on_controller_change()
    text = tool.probing_gcode_text
    if new == 'LinuxCNC':
        assert 'G38.2' in text
        assert 'G31' not in text and 'M40' not in text and 'M41' not in text
    else:
        assert 'G31' in text


# ---------------------------------------------------------------- defect 3
def grid_tool(step):
    tool = make_audit_tool()
    values = {
        'avoid_exc_holes_cb': True, 'avoid_exc_holes_size_entry': step,
        'probe_tip_dia_entry': 0.2, 'al_columns_entry': 3, 'al_rows_entry': 3,
    }
    for name, value in values.items():
        getattr(tool.ui, name).get_value.return_value = value
    tool.ui.al_method_radio.get_value.return_value = 'v'
    tool.solid_geo = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    tool.app.dec_format.side_effect = lambda value, dec=4: round(value, 4)
    tool.generate_voronoi_geometry = MagicMock()
    tool.plot_probing_geo = MagicMock()
    tool.plot_probing_geo = MagicMock()
    tool.probing_gcode = MagicMock(return_value='')
    hole = Polygon([(4, 4), (6, 4), (6, 6), (4, 6)])
    tool.check_point_over_excellon = lambda pol, check: pol.intersection(hole) if pol.intersects(hole) \
        else Polygon()
    return tool


def run_guarded(tool):
    counter = {'n': 0}
    real_contains = Polygon.contains

    def guard_contains(self, other):
        counter['n'] += 1
        if counter['n'] > 200000:
            raise RuntimeError('avoidance loop did not terminate')
        return real_contains(self, other)

    errors = []

    def run():
        try:
            with patch.object(lc_module, 'box', wraps=lc_module.box) as _b:
                tool.on_add_grid_points()
        except BaseException as err:
            errors.append(err)

    # limit by wrapping check_point_over_excellon, which runs once per iteration
    inner = tool.check_point_over_excellon

    def counting(pol, check):
        counter['n'] += 1
        if counter['n'] > 5000:
            raise RuntimeError('avoidance loop did not terminate')
        return inner(pol, check)

    tool.check_point_over_excellon = counting
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), 'on_add_grid_points hung'
    if errors:
        raise errors[0]


def test_zero_avoid_step_is_rejected_without_points():
    tool = grid_tool(0.0)
    run_guarded(tool)
    assert tool.al_voronoi_geo_storage == {}
    messages = [c.args[0] for c in tool.app.inform.emit.call_args_list]
    assert any(m.startswith('[ERROR_NOTCL]') for m in messages)


PLANE = make_plane(0.01, 0.02, 0.0)       # z = 0.01*x + 0.02*y
TOL = 3e-4
COLS, ROWS = 4, 3

CONTROLLERS = ['GRBL', 'MACH3', 'MACH4', 'LinuxCNC']
FIXTURES = {
    'simple_line': SIMPLE_LINE_GBR,
    'region': REGION_GBR,
    'flash': FLASH_GBR,
}

# controller -> (probe command, open command, close command)
PROBE_WORDS = {
    'MACH3': ('G31', 'M40', 'M41'),
    'MACH4': ('G31', 'M40', 'M41'),
    'LinuxCNC': ('G38.2', '(PROBEOPEN a_probing_points_file.txt)', '(PROBECLOSE)'),
}
HEIGHT_MAP_WRITERS = {
    'MACH3': write_mach3,                    # x,y,z
    'MACH4': write_mach4_extra_columns,      # x, y, z, 0.0, 0.0, 0.0
    'LinuxCNC': write_linuxcnc,              # x y z 0 0 0 0 0 0
}
# (controller, method, fixture): every controller, both methods, all 3 fixtures
FILE_CASES = [
    ('MACH3', 'b', 'simple_line'),
    ('LinuxCNC', 'v', 'simple_line'),
]
GRBL_CASES = [('b', 'simple_line')]

_TC = unittest.TestCase()   # reuse the independent G-code verifier of the journey tests


# --------------------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------------------

def make_source_job(gerber_path, name):
    """A real camlib.CNCjob with G-code made from a real Gerber file."""
    paths = gerber_toolpaths(gerber_path)
    gcode = make_gcode(paths, include_arc=False)
    job = make_real_cncjob()
    job.obj_options = {'name': name, 'type': 'Geometry'}
    job.kind = 'cncjob'
    job.units = 'MM'
    job.coords_decimals = 4
    job.fr_decimals = 2
    job.pp_geometry_name = 'default'
    job.pp_excellon_name = 'default'
    job.pp_solderpaste_name = None
    job.exc_tools = {}
    job.used_tools = [1]
    job.gcode = gcode
    parsed = job.gcode_parse(tool_data={})
    job.tools = {1: {'gcode': gcode, 'gcode_parsed': parsed, 'data': {}, 'tooldia': 1.0}}
    job.gcode_parsed = parsed
    job.create_geometry()
    return job


class Env:
    """The tool with a mocked UI/app around the real logic, plus a project registry."""

    def __init__(self, tmp_path, monkeypatch, cls=None):
        self.tmp_path = tmp_path
        self.objects = {}
        tool = make_audit_tool(units='MM', cls=cls or ToolLevelling)
        self.tool = tool
        app = tool.app
        app.defaults = {}
        app.options = {'cncjob_save_filters': 'Text (*.txt)', 'cncjob_line_ending': False}
        app.version = '9.9'
        app.version_date = '2026/10/12'
        app.get_last_save_folder.return_value = str(tmp_path)
        app.get_last_folder.return_value = str(tmp_path)
        app.dec_format.side_effect = lambda value, dec=4: round(value, dec)
        app.collection.get_by_name.side_effect = lambda n: self.objects.get(n)
        app.collection.get_list.return_value = []
        app.app_obj.new_object.side_effect = self._new_object
        # the worker runs inline
        app.worker_task.emit.side_effect = lambda d: d['fcn'](*d.get('params', []))
        # the apply step is the real one
        tool.apply_autolevel_sig = MagicMock()
        tool.plot_probing_geo = MagicMock()
        tool.on_grbl_search_ports = MagicMock()
        monkeypatch.setattr(lc_module, 'ShapeCollection', MagicMock())

        ui = tool.ui
        ui.al_mode_radio.get_value.return_value = 'grid'
        ui.avoid_exc_holes_cb.get_value.return_value = False
        ui.avoid_exc_holes_size_entry.get_value.return_value = 0.5
        ui.probe_tip_dia_entry.get_value.return_value = 0.2
        ui.al_columns_entry.get_value.return_value = COLS
        ui.al_rows_entry.get_value.return_value = ROWS
        ui.ptravelz_entry.get_value.return_value = 2.0
        ui.feedrate_probe_entry.get_value.return_value = 50.0
        ui.pdepth_entry.get_value.return_value = -1.0

        self.save_path = tmp_path / 'probing.nc'
        monkeypatch.setattr(
            tl_module, 'FCFileSaveDialog',
            SimpleNamespace(get_saved_filename=lambda **kw: (str(self.save_path), '')))

    def _new_object(self, kind, name, obj_init, *args, **kwargs):
        new_obj = make_real_cncjob()
        new_obj.obj_options = {'name': name, 'type': 'Geometry'}
        new_obj.kind = kind
        if obj_init(new_obj, self.tool.app) == 'fail':
            return 'fail'
        self.objects[name] = new_obj
        return 'success'

    # ---- user steps ----
    def load_cncjob(self, fixture, name):
        job = make_source_job(FIXTURES[fixture], name)
        self.objects[name] = job
        self.source_name = name
        self.snapshot = {
            'gcode': job.tools[1]['gcode'],
            'parsed_len': len(job.tools[1]['gcode_parsed']),
            'tools': copy.deepcopy({k: v['gcode'] for k, v in job.tools.items()}),
        }
        return job

    def select(self, name, controller, method):
        ui = self.tool.ui
        ui.object_combo.currentText.return_value = name
        ui.object_combo.get_value.return_value = name
        self.tool.on_object_changed()
        ui.al_method_radio.get_value.return_value = method
        self.tool.on_method_radio(method)
        if isinstance(self.tool, ToolGCodeSender):
            # the sender is always GRBL: it has no controller combo
            return
        ui.al_controller_combo.get_value.return_value = controller
        self.tool.on_controller_change()

    def add_points(self):
        assert self.tool.on_add_al_probepoints() != 'fail'
        storage = self.tool.al_voronoi_geo_storage
        assert len(storage) == COLS * ROWS
        return [(v['point'].x, v['point'].y) for v in storage.values()]

    def messages(self):
        return emitted(self.tool)

    def errors(self):
        return [m for m in self.messages() if '[ERROR_NOTCL]' in m]

    def levelled_name(self):
        return self.source_name + '_levelled'

    # ---- checks ----
    def assert_no_levelled(self):
        assert self.levelled_name() not in self.objects
        assert not any(n.endswith('_levelled') for n in self.objects)

    def assert_levelled(self, method, probe_points):
        """New '<source>_levelled' CNCJob; source untouched; Z = source Z + plane height."""
        name = self.levelled_name()
        assert name in self.objects, 'no levelled object; messages: %r' % self.messages()
        new, src = self.objects[name], self.objects[self.source_name]
        assert new is not src and new.obj_options['name'] == name
        assert new.kind == 'cncjob'

        # the source CNCJob is unchanged
        assert src.tools[1]['gcode'] == self.snapshot['gcode']
        assert src.gcode == self.snapshot['gcode']
        assert {k: v['gcode'] for k, v in src.tools.items()} == self.snapshot['tools']
        assert len(src.tools[1]['gcode_parsed']) == self.snapshot['parsed_len']

        if method == 'b':
            offset_fn = PLANE
        else:
            pts = [(x, y, PLANE(x, y)) for x, y in probe_points]

            def offset_fn(x, y):
                return nearest_height_bruteforce(pts, x, y)

        max_dz, cut_points = verify_levelling(
            _TC, self.snapshot['gcode'], new.tools[1]['gcode'], 4, offset_fn, tol=TOL)
        assert cut_points, 'no cut moves verified'
        assert max_dz > 1e-3, 'the levelled G-code is not changed'
        assert new.gcode == new.tools[1]['gcode']

        # parsed geometry exists
        parsed = new.tools[1]['gcode_parsed']
        assert len(parsed) > 0 and len(new.gcode_parsed) == len(parsed)
        assert all(g['geom'].geom_type == 'LineString' for g in parsed)
        assert new.solid_geometry is not None and len(new.solid_geometry) > 0
        assert any('[success]' in m for m in self.messages())
        return new


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


@pytest.fixture
def sender_env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch, cls=ToolGCodeSender)


# --------------------------------------------------------------------------------------
# G-code and height-map helpers
# --------------------------------------------------------------------------------------

WORD_LINE = re.compile(r'([A-Z]\s*[-+]?\d+(\.\d+)?\s*)+')


def check_probing_file(path, controller, probe_points):
    text = path.read_text()
    probe_cmd, open_cmd, close_cmd = PROBE_WORDS[controller]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for ln in lines:
        if ln.startswith('('):
            assert ln.endswith(')'), 'broken comment: %r' % ln
        else:
            assert WORD_LINE.fullmatch(ln) or ln == '#', 'plain prose / not G-code: %r' % ln
    assert lines.count(open_cmd) == 1 and lines.count(close_cmd) == 1
    assert lines[-1] == 'M2'
    for other_cmd in {'G31', 'G38.2'} - {probe_cmd}:
        assert not any(ln.startswith(other_cmd + ' ') for ln in lines)

    # a probe move for every point, in order, right after the move to X/Y
    moves, probes = [], 0
    for i, ln in enumerate(lines):
        m = re.fullmatch(r'G0 X(-?[\d.]+)Y(-?[\d.]+)', ln)
        if m:
            nxt = lines[i + 1]
            assert re.fullmatch(re.escape(probe_cmd) + r' Z-[\d.]+ F[\d.]+', nxt), nxt
            moves.append((float(m.group(1)), float(m.group(2))))
            probes += 1
    assert probes == len(probe_points)
    for (mx, my), (px, py) in zip(moves, probe_points):
        assert mx == pytest.approx(px, abs=1e-4) and my == pytest.approx(py, abs=1e-4)
    return text


def write_height_map(path, controller, probe_points, drop_last=False, shuffle=False):
    rows = [(x, y, PLANE(x, y)) for x, y in probe_points]
    if shuffle:
        rows = shuffled_rows(rows, seed=7)
    if drop_last:
        rows = rows[:-1]
    HEIGHT_MAP_WRITERS[controller](str(path), rows, decimals=5)


def import_through_button(env, path):
    with patch.object(tl_module.QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(path), '')):
        env.tool.on_import_height_map()


# --------------------------------------------------------------------------------------
# MACH3 / MACH4 / LinuxCNC
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize('controller,method,fixture', FILE_CASES)
def test_file_controller_full_journey(env, controller, method, fixture):
    """Gerber -> CNCJob -> select -> probing G-code -> export -> height map -> import -> levelled job."""
    env.load_cncjob(fixture, 'job_' + fixture)
    env.select('job_' + fixture, controller, method)
    points = env.add_points()

    # probing G-code through the real export path (dialog patched to a tmp file)
    assert env.tool.probing_gcode_text
    env.tool.on_save_probing_gcode()
    text = check_probing_file(env.save_path, controller, points)
    assert text == env.tool.probing_gcode_text

    # the controller writes a height map; the import button reads it
    hm = env.tmp_path / 'heights.txt'
    write_height_map(hm, controller, points, shuffle=(method == 'v'))
    env.assert_no_levelled()
    import_through_button(env, hm)

    assert env.tool.al_heights_valid
    assert not env.errors(), env.errors()
    env.assert_levelled(method, points)


@pytest.mark.parametrize('controller', ['MACH3'])
def test_file_controller_incomplete_height_map_makes_no_levelled_job(env, controller):
    env.load_cncjob('simple_line', 'job_neg')
    env.select('job_neg', controller, 'b')
    points = env.add_points()
    hm = env.tmp_path / 'heights_incomplete.txt'
    write_height_map(hm, controller, points, drop_last=True)
    import_through_button(env, hm)

    env.assert_no_levelled()
    env.tool.app.app_obj.new_object.assert_not_called()
    assert not env.tool.al_heights_valid
    assert any('no matching row' in m for m in env.errors()), env.messages()


ARC_G91_MARKER = '(ARC AND G91 BLOCK)'
ARC_G91_BLOCK = [
    ARC_G91_MARKER,
    'G00 Z2.0000',
    'G00 X1000.0000 Y1000.0000',
    'G01 Z-0.1000 F300.0000',
    'G03 X995.0000 Y1005.0000 I-5.0000 J0.0000',     # quarter arc around (995, 1000)
    'G91',
    'G01 X2.0000 Y1.0000 Z-0.0500',
    'G01 X1.0000',
    'G90',
    'G00 Z2.0000',
]


def test_file_controller_arc_and_g91_block_is_levelled(env):
    """A G2/G3 arc and a G91 block in the tool G-code: the machine follows the height map."""
    from tests.test_levelling_arc_g91 import Machine

    job = env.load_cncjob('simple_line', 'job_arc')
    gcode = job.tools[1]['gcode']
    assert '\nM05\n' in gcode
    gcode = gcode.replace('\nM05\n', '\n' + '\n'.join(ARC_G91_BLOCK) + '\nM05\n')
    job.tools[1]['gcode'] = gcode
    job.gcode = gcode
    env.snapshot['gcode'] = gcode
    env.snapshot['tools'] = {1: gcode}
    env.select('job_arc', 'MACH3', 'b')
    points = env.add_points()
    hm = env.tmp_path / 'heights_arc.txt'
    write_height_map(hm, 'MACH3', points)
    import_through_button(env, hm)

    assert env.tool.al_heights_valid
    assert not env.errors(), env.errors()
    new = env.objects[env.levelled_name()]
    assert env.objects[env.source_name].tools[1]['gcode'] == gcode
    levelled = new.tools[1]['gcode']
    assert 'G03' not in levelled and 'G3 ' not in levelled
    assert any(m.endswith('Arc moves split into line moves: 1') for m in env.messages()), env.messages()

    head, block = levelled.split(ARC_G91_MARKER, 1)
    machine = Machine()
    machine.feed(head)
    done = len(machine.trace)
    machine.feed(block.split('\nM05\n')[0])
    moves = machine.trace[done:]
    cuts = [m for m in moves if m[3] == 1]
    assert len(cuts) >= 12                                   # plunge + 9 or more arc segments + 2 relative
    programmed_z = [-0.1] * (len(cuts) - 2) + [-0.15] * 2
    for (x, y, z, _g), prog in zip(cuts, programmed_z):
        assert z == pytest.approx(prog + PLANE(x, y), abs=TOL), (x, y, z)
    for x, y, _z, _g in cuts[1:-2]:                          # the arc points are on the circle
        assert ((x - 995.0) ** 2 + (y - 1000.0) ** 2) ** 0.5 == pytest.approx(5.0, abs=2e-4)
    assert (cuts[-3][0], cuts[-3][1]) == (995.0, 1005.0)
    assert (cuts[-1][0], cuts[-1][1]) == pytest.approx((998.0, 1006.0), abs=1e-9)
    assert moves[-1][2] == 2.0                               # the final rapid is not compensated


# --------------------------------------------------------------------------------------
# GRBL
# --------------------------------------------------------------------------------------

class PlaneHeights(dict):
    """FakeGrbl.heights: the probe height of any (x, y) from the known plane."""

    def __bool__(self):
        return True     # FakeGrbl does `heights or {}`

    def get(self, key, default=None):
        return PLANE(*key)


def grbl_env(env):
    fake = FakeGrbl(heights=PlaneHeights())
    tool = env.tool
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    return fake


@pytest.mark.parametrize('method,fixture', GRBL_CASES)
def test_grbl_full_journey(sender_env, method, fixture):
    env = sender_env
    fake = grbl_env(env)
    env.load_cncjob(fixture, 'job_grbl_' + fixture)
    env.select('job_grbl_' + fixture, 'GRBL', method)
    points = env.add_points()
    # GRBL has no probing G-code file: it probes through the serial port
    storage = env.tool.al_voronoi_geo_storage if method == 'v' else env.tool.al_bilinear_geo_storage
    assert not env.tool.probing_gcode(storage)
    env.assert_no_levelled()

    env.tool._al_on_height_map_ready = MagicMock()
    env.tool.on_grbl_autolevel()

    cmds = fake.commands()
    probes = [c for c in cmds if c.startswith('G38.2')]
    assert len(probes) == len(points)
    assert not env.errors(), env.messages()
    assert env.tool.al_heights_valid
    # each stored height is what the fake reported for that point
    for v in env.tool.al_voronoi_geo_storage.values():
        assert v['height'] == pytest.approx(PLANE(v['point'].x, v['point'].y), abs=1e-4)
    assert env.tool._grbl_op is None
    env.tool._al_on_height_map_ready.assert_called_once_with(env.objects[env.source_name], ANY)


def test_grbl_failed_probe_makes_no_levelled_job(sender_env):
    env = sender_env
    fake = grbl_env(env)
    fake.fail_probe_at = len(range(COLS * ROWS))     # the last probe answers ALARM:4
    env.load_cncjob('flash', 'job_grbl_neg')
    env.select('job_grbl_neg', 'GRBL', 'b')
    env.add_points()

    env.tool._al_on_height_map_ready = MagicMock()
    env.tool.on_grbl_autolevel()

    env.assert_no_levelled()
    env.tool._al_on_height_map_ready.assert_not_called()
    env.tool.app.app_obj.new_object.assert_not_called()
    assert not env.tool.al_heights_valid
    assert env.errors(), env.messages()


# --------------------------------------------------------------------------------------
# a job imported from a G-code file
# --------------------------------------------------------------------------------------

IMPORTED_INCH = ("G20\nG90\nT1\nG0 Z0.1\nG0 X0.5 Y0.5\nG1 Z-0.02 F10\nG1 X1.5 Y0.5\nG1 X1.5 Y1.5\nG0 Z0.1\n"
                 "T2\nG0 X0.5 Y0.5\nG1 Z-0.02 F10\nG1 X1.0 Y1.0\nG0 Z0.1\n")


def _imported_job(env, text, name):
    from appParsers.ParseGCode import parse_gcode

    job = make_real_cncjob()
    parsed = parse_gcode(text, target_units='MM')
    job.obj_options = {'name': name, 'type': 'Geometry', 'gcode_units': parsed['units'], 'tooldia': 1.0}
    job.kind, job.units, job.gcode_imported, job.is_segmented_gcode = 'cncjob', 'MM', True, False
    job.coords_decimals, job.fr_decimals = 4, 2
    job.pp_geometry_name = job.pp_excellon_name = 'default'
    job.pp_solderpaste_name = None
    job.exc_tools = {}
    job.tools = {k: {'tool': t['tool'], 'tooldia': 1.0, 'data': {}, 'gcode': t['gcode'],
                     'gcode_parsed': t['gcode_parsed'], 'solid_geometry': t['solid_geometry']}
                 for k, t in parsed['tools'].items()}
    job.used_tools = list(job.tools)
    job.gcode = text
    job.gcode_parsed = [g for t in job.tools.values() for g in t['gcode_parsed']]
    job.create_geometry()
    env.objects[name] = job
    env.source_name = name
    return job


def test_imported_gcode_with_macros_is_not_levelled(env):
    job = _imported_job(env, IMPORTED_INCH.replace("G1 X1.5 Y0.5\n", "#100=1.5\nG1 X#100 Y0.5\n"), 'job_macro')
    env.select('job_macro', 'MACH3', 'b')
    points = env.add_points()
    hm = env.tmp_path / 'heights.txt'
    write_height_map(hm, 'MACH3', points)
    import_through_button(env, hm)

    assert any('macro variables or subprograms' in e for e in env.errors()), env.errors()
    assert env.levelled_name() not in env.objects
    assert job.gcode.count('#100') == 2


def test_imported_gcode_in_inches_is_levelled_once_in_its_own_units(env):
    """Metric application, inch file: no duplicated program, the probe heights are written in inches."""
    job = _imported_job(env, IMPORTED_INCH, 'job_in')
    assert len(job.tools) == 2 and ''.join(t['gcode'] for t in job.tools.values()) == IMPORTED_INCH
    env.select('job_in', 'MACH3', 'b')
    points = env.add_points()
    hm = env.tmp_path / 'heights.txt'
    write_height_map(hm, 'MACH3', points)
    import_through_button(env, hm)

    assert not env.errors(), env.errors()
    new = env.objects[env.levelled_name()]
    assert new.gcode_imported is True and new.obj_options['gcode_units'] == 'IN'
    assert new.gcode.count('T1') == 1 and new.gcode.count('T2') == 1
    assert ''.join(t['gcode'] for t in new.tools.values()) == new.gcode
    assert len(new.gcode_parsed) == sum(len(t['gcode_parsed']) for t in new.tools.values()) > 0
    cuts = [m for m in re.finditer(r'G1 X([-\d.]+) Y([-\d.]+) Z([-\d.]+)', new.gcode)]
    # the 1 inch moves are not segmented in the file: they are split (1 mm pieces) and levelled between their ends
    assert len(cuts) > 50
    for m in cuts:
        x, y, z = (float(v) for v in m.groups())
        assert z == pytest.approx(-0.02 + PLANE(x * 25.4, y * 25.4) / 25.4, abs=1e-3)
