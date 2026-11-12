import copy
from copy import deepcopy
import math
import os
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import numpy as np
from PyQt6 import QtWidgets
import pytest
from shapely import affinity, LineString, Point
from shapely.geometry import Point

from appHandlers.appHistory import apply_matrix_to_object, HistoryManager, transform_metadata
from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appParsers.ParseExcellon import Excellon
from camlib import object_circle_steps
from defaults import AppDefaults
from tclCommands.TclCommandAddDrill import TclCommandAddDrill
from tclCommands.TclCommandAddSlot import TclCommandAddSlot
from tclCommands.TclCommandAlignDrillGrid import TclCommandAlignDrillGrid
from tclCommands.TclCommandBuffer import TclCommandBuffer
from tests.helpers_objects import (
    _headless_qt,
    AFFINE_TRANSFORMS,
    app,
    apply_to,
    assert_poly_equiv,
    assert_xy_close,
    drill_xy,
    excellon,
    export_excellon_text,
    GERBER_C_FLASH,
    GERBER_P_FLASH,
    make_app,
    make_app_0b,
    make_excellon,
    make_excellon_0b,
    make_gerber_0b,
    make_same_diameter_excellon,
    polygon_union,
    pts,
    reparse_excellon,
    save_and_load,
    slot_coords,
    slot_flat,
    slot_xy,
    snapshot_excellon,
    TRANSFORM_NAMES)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


"""Excellon drill-file parser: modes (drill / route), units, zeros, tools, number formats, plated flags."""


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def factory_excellon_defaults(monkeypatch):
    # a headless App earlier in the same worker propagates its preferences into the class defaults
    monkeypatch.setattr(Excellon, 'defaults', {
        "zeros": "L", "excellon_format_upper_mm": '3', "excellon_format_lower_mm": '3',
        "excellon_format_upper_in": '2', "excellon_format_lower_in": '4', "excellon_units": 'INCH',
        "excellon_circle_steps": '32'})


def parse(text, **kwargs):
    app = make_app_0b()
    exc = Excellon(app, **kwargs)
    assert exc.parse_lines(text.splitlines(keepends=True)) is None
    return exc, app


def dump(exc):
    return {t: (round(v['tooldia'], 5),
                [(round(p.x, 4), round(p.y, 4)) for p in v['drills']],
                [((round(a.x, 4), round(a.y, 4)), (round(b.x, 4), round(b.y, 4))) for a, b in v['slots']])
            for t, v in exc.tools.items()}


METRIC = "M48\nMETRIC\nT1C1.0\n%\nT1\n"

# text, preference kwargs, units, tools: {tool: (diameter, drills, slots)}
CASES = {
    'route then drills with another tool': (
        "M48\nMETRIC\nT1C1.0\nT2C0.8\n%\nG90\nT1\nG00X10.0Y10.0\nM15\nG01X15.0Y10.0\nM16\nG05\nT2\nX1.0Y1.0\nX2.0Y2.0\nM30\n",
        {}, 'MM', {1: (1.0, [], [((10, 10), (15, 10))]), 2: (0.8, [(1, 1), (2, 2)], [])}),
    'units switch in the body converts what was read': (
        "M48\nINCH,TZ\nT1C0.0350\n%\nT1\nX1.0Y1.0\nM71\nX25.4Y25.4\nM30\n",
        {}, 'MM', {1: (0.889, [(25.4, 25.4), (25.4, 25.4)], [])}),
    'M80 / M70 mirror or swap only the pattern copy of their M02 line': (
        METRIC + "M25\nX1.0Y1.0\nX2.0Y1.0\nM01\nM02X10.0Y0.0M80\nM02X0.0Y10.0M70\nX3.0Y3.0\nM30\n",
        {}, 'MM', {1: (1.0, [(1, 1), (2, 1), (9, 1), (8, 1), (1, 11), (1, 12), (3, 3)], [])}),
    'G85 slot sets the modal coordinates and a slot can start from them': (
        METRIC + "X1.0Y1.0\nX2.0G85X4.0\nX6.0Y6.0\nM30\n",
        {}, 'MM', {1: (1.0, [(1, 1), (6, 6)], [((2, 1), (4, 1))])}),
    'Allegro tools and units from the comments': (
        ";HEADER:\n;Holesize 1. = 35.000000 Tolerance = +0.000000/-0.000000 PLATED MILS Quantity = 1\n"
        ";Holesize 2. = 125.000000 Tolerance = +0.000000/-0.000000 NON_PLATED MILS Quantity = 1\n%\n"
        "G90\nX01000Y01000\nT02\nX02000Y02000\nM30\n",
        {}, 'IN', {1: (0.035, [(1, 1)], []), 2: (0.125, [(2, 2)], [])}),
    'M15 / M16 tool down and up: a G01 after M16 cuts nothing': (
        METRIC + "G00X0Y0\nM15\nG01X10.0Y0\nM16\nG01X10.0Y10.0\nM15\nG01X0Y10.0\nM16\nM30\n",
        {}, 'MM', {1: (1.0, [], [((0, 0), (10, 0)), ((10, 10), (0, 10))])}),
    'decimal file: coordinates without a period are whole millimetres': (
        "M48\nMETRIC\nT1C1.0\n%\nG05\nT1\nX1.5Y2.5\nX8Y8\nX0Y-3\nM30\n",
        {}, 'MM', {1: (1.0, [(1.5, 2.5), (8, 8), (0, -3)], [])}),
}


@pytest.mark.parametrize('name', list(CASES))
def test_parse_result(name):
    text, kwargs, units, tools = CASES[name]
    exc, _app = parse(text, **kwargs)
    assert exc.units == units
    assert dump(exc) == {t: (dia, drills, [((a[0], a[1]), (b[0], b[1])) for a, b in slots])
                         for t, (dia, drills, slots) in tools.items()}


def test_route_half_circle_is_cut_as_short_segments():
    # G03 (counterclockwise) with the radius A, G02 (clockwise) with the centre offset I/J
    for command, clockwise in (('G03X10.0Y0.0A5.0', False), ('G02X10.0Y0.0I5.0J0.0', True)):
        exc, _app = parse(METRIC + "G00X0.0Y0.0\nM15\n%s\nM16\nM30\n" % command)
        slots = exc.tools[1]['slots']
        assert len(slots) >= 8
        assert (slots[0][0].x, slots[0][0].y) == (0, 0)
        assert (round(slots[-1][1].x, 9), round(slots[-1][1].y, 9)) == (10, 0)
        for (_start, stop), (next_start, _next_stop) in zip(slots, slots[1:]):
            assert (stop.x, stop.y) == (next_start.x, next_start.y)
        for _start, stop in slots:
            assert math.hypot(stop.x - 5, stop.y) == pytest.approx(5, abs=1e-9)
        # counterclockwise from (0, 0) to (10, 0) around (5, 0) passes under the centre, clockwise over it
        assert (min(s[1].y for s in slots) < -4.9) is (not clockwise)
        assert (max(s[1].y for s in slots) > 4.9) is clockwise


def test_plated_flag_per_tool():
    cases = [
        ("; #@! TF.FileFunction,NonPlated,1,2,NPTH\nM48\nMETRIC\n; #@! TA.AperFunction,Plated,PTH,ViaDrill\nT1C0.3\n"
         "; #@! TA.AperFunction,NonPlated,NPTH,MechanicalDrill\nT2C3.0\n; #@! TD\nT3C1.0\n%\nT1\nX1.0Y1.0\n",
         {1: True, 2: False, 3: False}),
        (";TYPE=PLATED\nM48\nMETRIC\nT1C0.3\nT2C0.5\n%\nT1\nX1.0Y1.0\n", {1: True, 2: True}),
        (CASES['Allegro tools and units from the comments'][0], {1: True, 2: False}),
        ("M48\nMETRIC\nT1C0.3\n%\nT1\nX1.0Y1.0\n", {}),
    ]
    for text, tool_plated in cases:
        exc, _app = parse(text)
        assert {t: v['plated'] for t, v in exc.tools.items() if 'plated' in v} == tool_plated


def test_equal_diameters_are_merged_only_on_request():
    text = "M48\nMETRIC\nT1C1.0\nT2C1.0\nT3C0.5\n%\nT1\nX1.0Y1.0\nT2\nX2.0Y2.0\nX3.0Y3.0G85X4.0Y3.0\nT3\nX9.0Y9.0\nM30\n"
    exc, _app = parse(text)
    assert sorted(exc.tools) == [1, 2, 3]

    merged = Excellon(make_app_0b())
    merged.merge_equal_tools = True
    merged.parse_lines(text.splitlines(keepends=True))
    assert dump(merged) == {1: (1.0, [(1, 1), (2, 2)], [((3, 3), (4, 3))]), 3: (0.5, [(9, 9)], [])}


def test_hole_and_slot_circles_follow_the_circle_preference():
    for mode in ('fixed', 'adaptive'):
        app = make_app_0b(excellon_circle_steps_mode=mode, excellon_circle_steps=24, excellon_circle_tolerance=5.0)
        exc = Excellon(app)
        text = ("M48\nMETRIC\nT1C0.3\nT2C3.0\n%\nT1\nX0.0Y0.0\nT2\nX5.0Y5.0\nG00X20.0Y0.0\nM15\nG01X30.0Y0.0\nM16\n"
                "M30\n")
        assert exc.parse_lines(text.splitlines(keepends=True)) is None
        exc.create_geometry()
        counts = {t: [len(g.exterior.coords) - 1 for g in v['solid_geometry']] for t, v in exc.tools.items()}
        small, large = (object_circle_steps(app.options, 'excellon', r) for r in (0.15, 1.5))
        if mode == 'fixed':
            assert small == large == 24
        else:
            assert small < large
        assert counts[1] == [small]
        # the drill and the slot of one tool have the same segments: a slot adds the 2 vertices of its straight sides
        assert counts[2] == [large, large + 2]


@pytest.fixture
def pcb_wizard(_headless_qt):
    from appPlugins.ToolPcbWizard import PcbWizard

    app = MagicMock()
    app.decimals = 4
    app.resource_location = 'assets/resources'
    app.options = {}
    app.defaults = {}
    tool = PcbWizard(app)
    tool.set_tool_ui()
    yield tool
    tool.deleteLater()


def test_pcbwizard_import_uses_the_format_corrected_after_loading(pcb_wizard, tmp_path):
    # neither file has the format: the form keeps 2:4 INCH until the user corrects it
    (tmp_path / 'board.drl').write_text("M48\n%\nT1\nX010000Y020000\nM30\n")
    (tmp_path / 'board.inf').write_text("T1 0.8\n")
    pcb_wizard.load_excellon(str(tmp_path / 'board.drl'))
    pcb_wizard.load_inf(str(tmp_path / 'board.inf'))
    pcb_wizard.ui.units_radio.set_value('METRIC')
    pcb_wizard.ui.int_entry.set_value(3)
    pcb_wizard.ui.frac_entry.set_value(3)
    imported = []

    def new_object(kind, name, initialize, **kwargs):
        exc = Excellon(make_app_0b())
        try:
            result = initialize(exc, pcb_wizard.app)
        except Exception as err:    # not raised in the slot of the button: PyQt would abort the test run
            result = err
        imported.append((result, exc.units, dump(exc)))
    pcb_wizard.app.app_obj.new_object.side_effect = new_object

    # the file was built with the format of the loading; the second Import parsed the same, exhausted StringIO
    pcb_wizard.ui.import_button.click()
    pcb_wizard.ui.import_button.click()
    assert imported == [(None, 'MM', {1: (0.8, [(10, 20)], [])})] * 2


def test_attributes_of_header_tools_and_drills():
    text = ("M48\n; #@! TF.FileFunction,Plated,1,4,PTH\n; #@! TF.GenerationSoftware,Ucamco,UcamX,2017.04\nMETRIC\n"
            "; #@! TA.AperFunction,ViaDrill\nT01C0.300\n; #@! TA.AperFunction,ComponentDrill\nT02C0.900\n%\nG05\n"
            "T01\nX008.150Y012.050\nT02\n; #@! TO.C,R1\nX030.790Y078.415\n; #@! TD\nX030.790Y080.955\nM30\n")
    exc, _app = parse(text)
    assert exc.file_attributes['generation_software'] == {'vendor': 'Ucamco', 'application': 'UcamX', 'version': '2017.04'}
    assert exc.attributes['TF']['.FileFunction'] == ['Plated', '1', '4', 'PTH']
    assert exc.tools[1]['attributes'] == {'.AperFunction': ['ViaDrill']}
    assert exc.tools[2]['attributes'] == {'.AperFunction': ['ComponentDrill']}
    assert exc.tools[1]['plated'] and exc.tools[2]['plated']
    assert exc.tools[1]['drills_to'] == [0]
    first, second = exc.tools[2]['drills_to']
    assert first != 0 and second == 0
    assert exc.to_attributes[first] == {'.C': ['R1']}


"""Stage 4 merge contract: ExcellonObject.merge fuses tools by diameter only if their data is equal.

Rule under test: with ``fuse_tools=True`` two tools fuse when their rounded diameters match AND their tool
parameters are equal: the ``data`` keys that start with 'tools_'. The tool data is a copy of the object options, so
it also holds per-object keys (name, bounds, zeros, plot ...) that differ between source objects; they are not
compared. Tools of the same diameter with different tool parameters stay separate, each with its own data, drills and
slots. Nothing is lost silently.

Headless: real ExcellonObject instances, ShapeCollection patched out (fixtures come from the characterization
tests).
"""


DATA_A = {'tools_drill_cutz': -1.7, 'tools_drill_multidepth': True, 'tools_drill_depthperpass': 0.4,
          'tools_drill_ppname_e': 'first'}
DATA_B = {'tools_drill_cutz': -2.4, 'tools_drill_multidepth': False, 'tools_drill_depthperpass': 0.8,
          'tools_drill_ppname_e': 'second'}


def object_keys(name):
    """Per-object option keys that the tool data holds; they differ from 1 source object to the next."""
    n = float(len(name)) + sum(map(ord, name)) / 1000.0
    return {'name': name, 'xmin': -n, 'ymin': -2 * n, 'xmax': n, 'ymax': 2 * n,
            'zeros': 'L' if len(name) % 2 else 'T', 'plot': len(name) % 2 == 0, 'units': 'MM'}


def make_exc(app, name, specs):
    """specs: list of (dia, drill xy list, slot xy-pair list, data dict). Tool ids are 1..n."""
    obj = ExcellonObject(name, app)
    obj.units = 'MM'
    obj.tools = {}
    for idx, (dia, drills, slots, data) in enumerate(specs, start=1):
        obj.tools[idx] = {
            'tooldia': dia,
            'drills': [Point(p) for p in drills],
            'slots': [(Point(a), Point(b)) for a, b in slots],
            'solid_geometry': [],
            # as in practice, the data is a copy of the object options: it holds per-object keys too
            'data': dict(data, **object_keys(name)),
        }
    assert obj.create_geometry() is None
    return obj


def merge(app, objs, fuse=True):
    final = ExcellonObject('combo', app)
    ExcellonObject.merge(objs, final, decimals=4, fuse_tools=fuse, log=Mock())
    return final


def pts(items):
    return [(p.x, p.y) for p in items]


def slots_xy(slots):
    return [((s[0].x, s[0].y), (s[1].x, s[1].y)) for s in slots]


def strip_name(data):
    """The tool parameters of a tool data (the 'tools_' keys)."""
    return {k: v for k, v in data.items() if k.startswith('tools_')}


class TestFuseEqualData:

    def test_same_diameter_equal_data_fuses_into_one_tool(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (1, 0)], [((0, 5), (3, 5))], DATA_A),
                                (0.8, [(9, 9)], [], DATA_B)])
        b = make_exc(app, 'b', [(1.0, [(10, 0)], [((10, 5), (13, 5))], DATA_A)])
        final = merge(app, [a, b])
        assert len(final.tools) == 2
        big = [t for t in final.tools.values() if t['tooldia'] == 1.0]
        assert len(big) == 1
        assert pts(big[0]['drills']) == [(0, 0), (1, 0), (10, 0)]
        assert slots_xy(big[0]['slots']) == [((0, 5), (3, 5)), ((10, 5), (13, 5))]
        assert strip_name(big[0]['data']) == DATA_A
        assert len(big[0]['solid_geometry']) == 5
        small = [t for t in final.tools.values() if t['tooldia'] == 0.8]
        assert len(small) == 1 and strip_name(small[0]['data']) == DATA_B


    def test_diameters_equal_after_rounding_fuse(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.00001, [(5, 0)], [], DATA_A)])
        final = merge(app, [a, b])
        assert len(final.tools) == 1
        assert final.tools[1]['tooldia'] == 1.0
        assert pts(final.tools[1]['drills']) == [(0, 0), (5, 0)]


class TestKeepDifferentData:
    def test_same_diameter_different_data_stays_two_tools(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (1, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(10, 0)], [((10, 5), (13, 5))], DATA_B)])
        final = merge(app, [a, b])
        assert len(final.tools) == 2
        first, second = final.tools[1], final.tools[2]
        assert strip_name(first['data']) == DATA_A and strip_name(second['data']) == DATA_B
        assert pts(first['drills']) == [(0, 0), (1, 0)] and first['slots'] == []
        assert pts(second['drills']) == [(10, 0)]
        assert slots_xy(second['slots']) == [((10, 5), (13, 5))]
        assert len(final.solid_geometry) == 4


class TestExcellonMerge:
    def test_merge_without_fusing_keeps_every_tool_and_is_independent(self, app, excellon):
        other = make_excellon(app, name='exc_b')
        other.offset((100.0, 0.0))
        src_a, src_b = snapshot_excellon(excellon), snapshot_excellon(other)
        final = ExcellonObject('combo', app)
        ExcellonObject.merge([excellon, other], final, decimals=4, fuse_tools=False, log=Mock())
        assert sorted(final.tools) == [1, 2, 3, 4, 5, 6]
        assert sum(len(t['drills']) for t in final.tools.values()) == 12
        assert sum(len(t['slots']) for t in final.tools.values()) == 6
        assert len(final.solid_geometry) == 18
        assert pts(final.tools[4]['drills']) == [(105, 5), (110, 5), (115, 5)]
        # sources unchanged
        assert pts(excellon.tools[1]['drills']) == pts(src_a[1]['drills'])
        assert pts(other.tools[1]['drills']) == pts(src_b[1]['drills'])
        # the result owns its data
        final.tools[1]['drills'].append(Point(1, 1))
        final.tools[1]['tooldia'] = 7.0
        assert len(excellon.tools[1]['drills']) == 3 and excellon.tools[1]['tooldia'] == 0.8


def join(app, objs, inform=None):
    final = ExcellonObject('combo', app)
    ExcellonObject.merge(objs, final, decimals=4, fuse_tools=True, log=Mock(), inform=inform)
    return final


def placed(final):
    """(tool diameter, x, y) of every drill of the joined object, sorted."""
    return sorted((round(t['tooldia'], 4), x, y) for t in final.tools.values() for x, y in pts(t['drills']))


class TestJoinDuplicateCheck:
    def test_same_drill_from_two_sources_is_kept_once(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (5, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(0, 0), (10, 0)], [], DATA_A)])
        assert placed(join(app, [a, b])) == [(1.0, 0, 0), (1.0, 5, 0), (1.0, 10, 0)]

    def test_same_center_other_diameter_keeps_both_and_informs(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(0.8, [(0, 0)], [], DATA_B)])
        inform = Mock()
        assert placed(join(app, [a, b], inform=inform)) == [(0.8, 0, 0), (1.0, 0, 0)]
        assert inform.called

    def test_tangent_drills_are_silent_and_overlapping_drills_inform(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        tangent = make_exc(app, 'b', [(1.0, [(1.0, 0)], [], DATA_A)])
        inform = Mock()
        assert placed(join(app, [a, tangent], inform=inform)) == [(1.0, 0, 0), (1.0, 1.0, 0)]
        assert not inform.called

        overlap = make_exc(app, 'c', [(1.0, [(0.9, 0)], [], DATA_A)])
        inform = Mock()
        assert placed(join(app, [a, overlap], inform=inform)) == [(1.0, 0, 0), (1.0, 0.9, 0)]
        assert inform.called

    def test_duplicates_inside_one_source_are_kept(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(5, 0)], [], DATA_A)])
        assert placed(join(app, [a, b])) == [(1.0, 0, 0), (1.0, 0, 0), (1.0, 5, 0)]


class TestJoinKeepsHoleAttributes:
    TEXT = "M48\nMETRIC\nT1C1.0\n%%\nG05\nT1\n; #@! TO.C,%s\nX%s.0Y1.0\n; #@! TD\nX%s.0Y2.0\nM30\n"

    def obj(self, app, name, comp, x1, x2):
        from appParsers.ParseExcellon import Excellon  # noqa: F401
        exc = ExcellonObject(name, app)
        assert exc.parse_lines((self.TEXT % (comp, x1, x2)).splitlines(keepends=True)) is None
        exc.create_geometry()
        return exc

    def state_at(self, final):
        from appParsers.ParseExcellon import hole_ids, to_state
        tool = final.tools[1]
        ids = hole_ids(tool, 'drills')
        return {(p.x, p.y): to_state(final.to_attributes, i) for p, i in zip(tool['drills'], ids)}

    def test_join_keeps_each_hole_component_and_the_ids_follow_the_dedupe(self, app):
        a = self.obj(app, 'a', 'R1', 1, 2)
        b = self.obj(app, 'b', 'R2', 3, 2)      # its hole at (2, 2) duplicates one of a and is removed
        got = self.state_at(join(app, [a, b]))
        assert got == {(1.0, 1.0): {'.C': ['R1']}, (2.0, 2.0): None, (3.0, 1.0): {'.C': ['R2']}}

    def test_object_without_ids_stays_without_keys_and_the_tf_is_dropped_when_it_differs(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(5, 0)], [], DATA_A)])
        final = join(app, [a, b])
        assert 'drills_to' not in final.tools[1] and not final.to_attributes
        c = self.obj(app, 'c', 'R1', 7, 8)
        d = self.obj(app, 'd', 'R1', 9, 9)
        c.attributes['TF'] = {'FileFunction': ['Plated']}
        assert join(app, [c, d]).attributes['TF'] == {}


"""Excellon.add_tool: the contract, and the sites that build Excellon tools.

Sections:
- ``TestAddToolContract``: ids, keys, copies, data handling.
- ``TestTclSites``: the Tcl commands that create tools (add_drill, add_slot, aligndrillgrid). They compare the tools dict
  with literal expected values and do NOT use add_tool, so the same tests pass before and after a site is moved to
  add_tool (that is the before/after proof).
- ``TestPluginPatterns``: the plugin sites (Markers, CutOut, PDF import, 2-sided alignment drills) cannot run headless
  (they sit inside GUI handlers). These tests build the tools the way the site did before (a hand-made dict, assigned
  and then ``create_geometry``) and the way it does now (``add_tool``), and compare the resulting tools dict.
"""


def fresh(default_data=None):
    ex = Excellon(make_app_0b())
    if default_data is not None:
        ex.default_data = default_data
    return ex


def snap(tools):
    """A comparable snapshot of a tools dict, including the order of the tool ids."""
    out = []
    for tid, tool in tools.items():
        out.append((tid, {
            key: ([g.wkt for g in val] if key == 'solid_geometry' else
                  [(a.wkt, b.wkt) for a, b in val] if key == 'slots' else
                  [p.wkt for p in val] if key == 'drills' else deepcopy(val))
            for key, val in tool.items()}))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------------------------------------------------

class TestAddToolContract:

    def test_next_id_is_max_plus_1_not_len_plus_1(self):
        ex = fresh()
        ex.tools = {1: {}, 5: {}}
        assert ex.add_tool(0.8) == 6


    def test_existing_id_raises_and_keeps_the_tool(self):
        ex = fresh()
        ex.add_tool(0.8)
        with pytest.raises(ValueError):
            ex.add_tool(1.0, tool_id=1)
        assert ex.tools[1]['tooldia'] == 0.8


# ---------------------------------------------------------------------------------------------------------------------
# Tcl sites: literal expectations, valid before and after the move to add_tool
# ---------------------------------------------------------------------------------------------------------------------

def _tcl_app():
    app = make_app_0b()
    app.dec_format = lambda value, digits=None: round(float(value), 4 if digits is None else digits)
    app.options.update({'excellon_circle_steps_mode': 'fixed', 'excellon_circle_steps': 16})
    return app


def _tcl_obj(app, tools_spec):
    """An Excellon with the given {id: (dia, drills, slots)}; its data is the marker ``{'seed': id}``."""
    ex = Excellon(app)
    ex.kind = 'excellon'
    ex.units = 'MM'
    ex.obj_options = {'name': 'ex'}
    ex.tools = {}
    for tid, (dia, drills, slots) in tools_spec.items():
        ex.tools[tid] = {'tooldia': dia, 'drills': [Point(p) for p in drills],
                         'slots': [(Point(a), Point(b)) for a, b in slots],
                         'solid_geometry': [], 'data': {'seed': tid}}
    ex.create_geometry()
    return ex


def _run_tcl(cmd_cls, app, obj, *argv):
    app.collection.get_by_name.side_effect = lambda n: obj if n == 'ex' else None
    cmd = cmd_cls(app)
    args, unnamed = cmd.check_args(('ex',) + argv)
    return cmd.execute(args, unnamed)


def _geo(dia, drills=(), slots=(), steps=16):
    out = [Point(p).buffer(dia / 2.0, steps // 4).wkt for p in drills]
    from shapely.geometry import LineString
    out += [LineString([a, b]).buffer(dia / 2.0, steps // 4).wkt for a, b in slots]
    return out


def _option_data(app):
    """What add_drill / add_slot put in the data of a new tool."""
    data = {}
    for option in app.options:
        if option.find('excellon_') == 0:
            data[option[len('excellon_'):]] = app.options[option]
        if option.find('tools_drill_') == 0:
            data[option] = app.options[option]
    return data


class TestTclSites:


    def test_add_drill_existing_diameter_appends_and_rejects_a_duplicate(self):
        app = _tcl_app()
        obj = _tcl_obj(app, {1: (0.8, [(0, 0)], [])})
        _run_tcl(TclCommandAddDrill, app, obj, '-dia', '0.8', '-x', '1', '-y', '2')
        assert [p.wkt for p in obj.tools[1]['drills']] == [Point(0, 0).wkt, Point(1, 2).wkt]
        assert obj.tools[1]['data'] == {'seed': 1}
        msg = _run_tcl(TclCommandAddDrill, app, obj, '-dia', '0.8', '-x', '1', '-y', '2')
        assert 'already' in msg and len(obj.tools[1]['drills']) == 2


    def test_add_slot_new_diameter_renumbers_by_diameter(self):
        app = _tcl_app()
        obj = _tcl_obj(app, {1: (1.0, [(0, 0)], []), 2: (0.5, [], [((0, 5), (2, 5))])})
        args = {'name': 'ex', 'dia': 0.8, 'startx': 0.0, 'starty': 1.0, 'stopx': 4.0, 'stopy': 1.0}
        app.collection.get_by_name.side_effect = lambda n: obj if n == 'ex' else None
        TclCommandAddSlot(app).execute(args, [])
        got = snap(obj.tools)
        assert [t['tooldia'] for _, t in got] == [0.5, 0.8, 1.0]
        new = got[1][1]
        assert new['drills'] == [] and new['slots'] == [(Point(0, 1).wkt, Point(4, 1).wkt)]
        assert new['data'] == _option_data(app)
        assert set(new) == {'tooldia', 'drills', 'slots', 'data', 'solid_geometry'}

    def test_aligndrillgrid_creates_one_tool_with_int_id(self):
        app = _tcl_app()
        created = []

        def new_object(kind, name, init, plot=False):
            obj = Excellon(app)
            obj.units = 'MM'
            obj.obj_options = {'name': name}
            # the camlib Excellon is not an object: the method that rewrites its source text is a stand-in
            obj.regenerate_source = Mock()
            init(obj, app)
            created.append(obj)

        app.app_obj.new_object.side_effect = new_object
        cmd = TclCommandAlignDrillGrid(app)
        args, unnamed = cmd.check_args(('-dia', '1.5', '-rows', '2', '-columns', '3', '-gridx', '2', '-gridy', '3',
                                        '-gridoffsetx', '1', '-gridoffsety', '4'))
        cmd.execute(args, unnamed)
        obj, = created
        drills = [(1, 4), (3, 4), (5, 4), (1, 7), (3, 7), (5, 7)]
        got = snap(obj.tools)
        assert [tid for tid, _ in got] == [1]
        tool = got[0][1]
        assert tool['tooldia'] == 1.5 and tool['drills'] == [Point(p).wkt for p in drills]
        assert tool['solid_geometry'] == _geo(1.5, drills)
        assert tool['data'] == {}
        assert set(tool).issuperset({'tooldia', 'drills', 'solid_geometry', 'data'})
        assert set(tool) - {'tooldia', 'drills', 'solid_geometry', 'data', 'slots'} == set()


# ---------------------------------------------------------------------------------------------------------------------
# plugin sites: the old hand-made dict against add_tool
# ---------------------------------------------------------------------------------------------------------------------


def test_add_drill_to_a_tool_with_hole_ids_keeps_the_ids_in_step():
    from appParsers.ParseExcellon import hole_ids
    app = _tcl_app()
    obj = _tcl_obj(app, {1: (0.8, [(0, 0)], [])})
    obj.tools[1]['drills_to'] = [1]
    obj.to_attributes = {1: {'C': ['R1']}}
    _run_tcl(TclCommandAddDrill, app, obj, '-dia', '0.8', '-x', '1', '-y', '2')
    assert hole_ids(obj.tools[1], 'drills') == [1, 0]
    _run_tcl(TclCommandAddDrill, app, obj, '-dia', '1.2', '-x', '5', '-y', '5')
    assert 'drills_to' not in obj.tools[2]


"""Excellon scale / offset / mirror / skew / rotate / buffer: the vectorized path must equal per element shapely."""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_QAPP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

STEPS = int(AppDefaults().defaults["excellon_circle_steps"])


def _make(app):
    obj = ExcellonObject('exc', app)
    obj.units = 'MM'
    obj.tools = {
        1: {'tooldia': 0.8, 'drills': [Point(1, 2), Point(3.5, -4), Point(10, 10)], 'slots': [], 'solid_geometry': [],
            'data': {'cutz': -1.7}},
        2: {'tooldia': 1.2, 'drills': [Point(0, 0)], 'slots': [(Point(5, 5), Point(9, 6)), (Point(-2, 3), Point(4, 3))],
            'solid_geometry': [], 'data': {'cutz': -2.0}},
        3: {'tooldia': 2.0, 'drills': [], 'solid_geometry': [], 'data': {}},
    }
    assert obj.create_geometry() is None
    return obj


def _reference(obj, op, tooldia_fn=lambda d: d):
    """What the old code produced: transform each point with shapely.affinity, then a fresh per element buffer."""
    out = {}
    for key, tool in obj.tools.items():
        r = tooldia_fn(tool['tooldia']) / 2.0
        drills = [op(p) for p in tool['drills']]
        slots = [(op(a), op(b)) for a, b in tool.get('slots', [])]
        geo = [p.buffer(r, STEPS // 4) for p in drills] + [LineString([a, b]).buffer(r, STEPS // 4) for a, b in slots]
        out[key] = (drills, slots, geo)
    return out


class ExcellonTransformsTest(unittest.TestCase):
    def setUp(self):
        patcher = patch('appObjects.AppObjectTemplate.ShapeCollection')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = make_app()
        self.app.options['excellon_circle_steps_mode'] = 'fixed'     # STEPS segments for every hole
        self.obj = _make(self.app)

    def check(self, ref):
        total = []
        for key, (drills, slots, geo) in ref.items():
            tool = self.obj.tools[key]
            self.assertEqual(len(tool['drills']), len(drills))
            for got, want in zip(tool['drills'], drills):
                self.assertTrue(got.equals_exact(want, 1e-9))
            self.assertEqual(len(tool.get('slots', [])), len(slots))
            for got, want in zip(tool.get('slots', []), slots):
                self.assertIs(type(got), tuple)
                self.assertTrue(got[0].equals_exact(want[0], 1e-9) and got[1].equals_exact(want[1], 1e-9))
            self.assertEqual(len(tool['solid_geometry']), len(geo))
            for got, want in zip(tool['solid_geometry'], geo):
                self.assertTrue(got.equals_exact(want, 1e-9))
            total.extend(geo)
        self.assertEqual(len(self.obj.solid_geometry), len(total))
        for got, want in zip(self.obj.solid_geometry, total):
            self.assertTrue(got.equals_exact(want, 1e-9))


    def test_scale_moves_holes_keeps_diameters(self):
        ref = _reference(self.obj, lambda p: affinity.scale(p, 1.5, 0.5, origin=(1, 2)))
        self.obj.scale(1.5, 0.5, (1, 2))
        self.check(ref)
        self.assertEqual([t['tooldia'] for t in self.obj.tools.values()], [0.8, 1.2, 2.0])


    def test_buffer_distance_and_factor(self):
        ref = _reference(self.obj, lambda p: p, lambda d: d + 0.2)
        self.obj.buffer(0.1, 'round', False)
        self.check(ref)
        self.assertAlmostEqual(self.obj.tools[1]['tooldia'], 1.0)

        obj = _make(self.app)
        self.obj = obj
        ref = _reference(obj, lambda p: p, lambda d: d * 1.5)
        obj.buffer(1.5, 'round', True)
        self.check(ref)


@pytest.mark.parametrize('name', ['mirror_x', 'rotate', 'skew'])
class TestExcellonTransforms:
    def test_drill_and_slot_coordinates(self, app, name):
        obj = make_excellon(app)
        before = snapshot_excellon(obj)
        AFFINE_TRANSFORMS[name][0](obj)
        fn = AFFINE_TRANSFORMS[name][1]
        assert sorted(obj.tools) == sorted(before)
        for t, tool in before.items():
            now = obj.tools[t]
            assert len(now['drills']) == len(tool['drills'])
            assert len(now['slots']) == len(tool['slots'])
            for p_before, p_now in zip(tool['drills'], now['drills']):
                expected = fn(p_before)
                assert (p_now.x, p_now.y) == pytest.approx((expected.x, expected.y), abs=1e-9), (name, t)
            for s_before, s_now in zip(tool['slots'], now['slots']):
                for end_before, end_now in zip(s_before, s_now):
                    expected = fn(end_before)
                    assert (end_now.x, end_now.y) == pytest.approx((expected.x, expected.y), abs=1e-9), (name, t)


class TestExcellonFileRoundTrip:
    def test_excellon_round_trip(self, app, tmp_path):
        exc = make_excellon(app)
        again = reparse_excellon(app, export_excellon_text(app, exc), tmp_path)
        assert again.units == 'MM'
        assert sorted(again.tools) == [1, 2, 3]
        for t, tool in exc.tools.items():
            assert again.tools[t]['tooldia'] == pytest.approx(tool['tooldia'], abs=1e-9)
            assert pts(again.tools[t]['drills']) == pytest.approx(np.array(pts(tool['drills'])), abs=1e-9)
            assert slot_flat(again.tools[t]['slots']) == pytest.approx(np.array(slot_flat(tool['slots'])), abs=1e-9)
        assert_poly_equiv(again.solid_geometry, exc.solid_geometry, rel=1e-6, abs_tol=1e-6, msg='excellon file')


    def test_excellon_attributes_survive_the_export(self, app, tmp_path):
        text = ("M48\n; #@! TF.FileFunction,Plated,1,2,PTH\nMETRIC\n; #@! TA.AperFunction,Plated,PTH,ViaDrill\n"
                "T1C0.300\n; #@! TD\nT2C0.800\n%\nG05\nT1\nX1.0Y1.0\n; #@! TO.C,R1\n; #@! TO.N,GND\nX2.0Y2.0\n"
                "X3.0Y3.0\n; #@! TD\nX4.0Y4.0\nT2\n; #@! TO.C,R2\nX5.0Y5.0\nM30\n")
        src = reparse_excellon(app, text, tmp_path, 'src')
        exported = export_excellon_text(app, src)
        assert exported.splitlines()[1] == '; #@! TF.FileFunction,Plated,1,2,PTH'
        again = reparse_excellon(app, exported, tmp_path)
        assert again.attributes['TF'] == src.attributes['TF'] and again.file_attributes == src.file_attributes
        assert again.tools[1]['attributes'] == src.tools[1]['attributes'] != {}
        assert not again.tools[2].get('attributes')
        for key in (1, 2):
            assert again.tools[key]['drills_to'] == src.tools[key]['drills_to']
        assert again.tools[1]['drills_to'].count(0) == 2
        assert {n: s for n, s in again.to_attributes.items()} == src.to_attributes


def _tcl_buffer(ex, *argv):
    app = make_app_0b()
    app.collection.get_by_name.return_value = ex
    cmd = TclCommandBuffer(app)
    args, unnamed = cmd.check_args(('ex',) + argv)
    cmd.execute(args, unnamed)


class TestTransformBugs_0B4:
    def test_excellon_rotate_without_point_uses_bounds_center(self):
        ex = make_excellon_0b(default_data={})
        minx, miny, maxx, maxy = ex.bounds()
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        old_drills = drill_xy(ex)
        old_slots = slot_xy(ex)

        def rot90(p):
            # 90 degrees counter-clockwise about (cx, cy)
            return cx - (p[1] - cy), cy + (p[0] - cx)

        ex.rotate(90)
        assert_xy_close(drill_xy(ex), [rot90(p) for p in old_drills], tol=1e-6)
        assert_xy_close([s[0] for s in slot_xy(ex)], [rot90(s[0]) for s in old_slots], tol=1e-6)
        assert_xy_close([s[1] for s in slot_xy(ex)], [rot90(s[1]) for s in old_slots], tol=1e-6)


class TestGuardsAlreadyPassing_0B:
    def test_excellon_scale_negative_x_uses_signed_factor(self):
        ex = make_excellon_0b(default_data={}, drills=((2.0, 1.0), (4.0, 3.0)), slots=(((2.0, 5.0), (6.0, 5.0)),))
        ex.scale(-1, 1, point=(1, 1))
        assert_xy_close(drill_xy(ex), [(0.0, 1.0), (-2.0, 3.0)])
        assert_xy_close([s[0] for s in slot_xy(ex)], [(0.0, 5.0)])
        assert_xy_close([s[1] for s in slot_xy(ex)], [(-4.0, 5.0)])


    def test_excellon_convert_units_round_trip_scales_once(self):
        ex = make_excellon_0b(default_data={}, units='IN', tooldia=1.0, drills=((1.0, 2.0),),
                           slots=(((0.0, 0.0), (1.0, 0.0)),))
        ex.convert_units('MM')
        assert ex.tools[1]['tooldia'] == pytest.approx(25.4, abs=1e-9)
        assert_xy_close(drill_xy(ex), [(25.4, 50.8)], tol=1e-9)
        assert_xy_close([s[1] for s in slot_xy(ex)], [(25.4, 0.0)], tol=1e-9)
        ex.convert_units('IN')
        assert ex.tools[1]['tooldia'] == pytest.approx(1.0, abs=1e-9)
        assert_xy_close(drill_xy(ex), [(1.0, 2.0)], tol=1e-9)
        assert_xy_close([s[1] for s in slot_xy(ex)], [(1.0, 0.0)], tol=1e-9)


class _RecordingShapes:
    """The ShapeGroup API used by ExcellonObject.plot(), keeping the shapes it was given."""

    def __init__(self):
        self.data = {}
        self._indexes = []
        self._collection = self
        self.visible = True

    def add(self, shape, **kwargs):
        key = len(self._indexes)
        self._indexes.append(key)
        self.data[key] = shape
        return key

    def add_batch(self, shapes_data, **kwargs):
        return [self.add(item['shape']) for item in shapes_data]

    def transform(self, matrix, update=True):
        for key in self._indexes:
            self.data[key] = affinity.affine_transform(self.data[key], matrix)
        return True

    def redraw(self, *args, **kwargs):
        pass

    def clear(self, update=False):
        self.data.clear()
        del self._indexes[:]


def test_matrix_undo_redo_keeps_excellon_source_and_plot_in_sync(app, tmp_path):
    obj = make_same_diameter_excellon(app)
    app.f_handlers = appIO(app)
    obj.shapes = _RecordingShapes()
    obj.plot()

    before_tools = snapshot_excellon(obj)
    assert obj.regenerate_source() is True

    matrix = (2, 0, 0, 1, 0, 0)
    before_meta = transform_metadata(obj)
    obj.scale(2, 1, point=(0, 0))
    obj.regenerate_source()
    scaled_tools = snapshot_excellon(obj)

    history = HistoryManager(app, SimpleNamespace(get_list=lambda: [obj]))
    app.history = history
    history.record_matrix('Scale', [(obj, matrix, before_meta)])

    assert history.undo() is True
    undone = reparse_excellon(app, obj.source_file, tmp_path, 'undone')
    assert pts(undone.tools[1]['drills']) == pytest.approx(np.array(pts(before_tools[1]['drills'])), abs=1e-9)
    assert slot_coords(undone.tools[2]['slots']) == pytest.approx(
        np.array(slot_coords(before_tools[2]['slots'])), abs=1e-9)
    assert obj.shapes.data[0].bounds == pytest.approx((4.5, 4.5, 5.5, 5.5), abs=1e-6)

    assert history.redo() is True
    redone = reparse_excellon(app, obj.source_file, tmp_path, 'redone')
    assert pts(redone.tools[1]['drills']) == pytest.approx(np.array(pts(scaled_tools[1]['drills'])), abs=1e-9)
    assert slot_coords(redone.tools[2]['slots']) == pytest.approx(
        np.array(slot_coords(scaled_tools[2]['slots'])), abs=1e-9)
    assert obj.shapes.data[0].bounds == pytest.approx((9.5, 4.5, 10.5, 5.5), abs=1e-6)


def _buffer_state(obj):
    return ({key: tool['tooldia'] for key, tool in obj.tools.items()},
            list(obj.solid_geometry),
            {key: list(tool['solid_geometry']) for key, tool in obj.tools.items()},
            {key: dict(tool['data']) for key, tool in obj.tools.items()})


def _assert_buffer_state(obj, state):
    diameters, geometry, tool_geometry, data = state
    assert {key: tool['tooldia'] for key, tool in obj.tools.items()} == diameters
    assert obj.solid_geometry == geometry
    for key, tool in obj.tools.items():
        assert tool['solid_geometry'] == tool_geometry[key]
        assert tool['data'] == data[key]


def test_buffer_validates_every_tool_before_changing_any(app):
    obj = _make(app)
    before = _buffer_state(obj)
    # tool 1 (0.8 mm) would become negative, tools 2 and 3 would stay valid
    assert obj.buffer(-0.5, 'round', None) == 'fail'
    _assert_buffer_state(obj, before)


# slots stay tuples of Points after a project save/load (folded from test_excellon_slots_load.py)

def _tuple_slots(obj):
    out = {}
    for key, tool in obj.tools.items():
        if 'slots' in tool:
            out[key] = tool['slots']
    return out


def test_slots_are_tuples_after_project_round_trip(app, tmp_path):
    exc = make_excellon(app, name='exc')
    before = _tuple_slots(exc)
    assert sum(len(s) for s in before.values()) > 0

    _, restored = save_and_load(app, [exc], tmp_path)
    after = _tuple_slots(restored[0])

    assert sorted(after) == sorted(before)
    for key, slots in after.items():
        assert len(slots) == len(before[key])
        for slot in slots:
            assert type(slot) is tuple and len(slot) == 2
            assert all(isinstance(p, Point) for p in slot)
        assert slot_flat(slots) == slot_flat(before[key]) or all(
            abs(a - b) < 2e-6 for x, y in zip(slot_flat(slots), slot_flat(before[key])) for a, b in zip(x, y))
