"""Excellon Editor save (update_fcexcellon): a slot diameter goes into the tool with the same diameter or into a new
tool after the highest tool number; the diameter and the holes of an existing tool are never changed. Every saved tool
has the 'drills' and the 'slots' lists, even empty, like a tool of a parsed Excellon file, so the Excellon export and
the drill job of the saved object work.

The save is called on a minimal fake editor (the storage of the shapes by diameter, as the editor builds it)."""
import importlib
import re
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.geometry import LineString, MultiLineString

from appEditors.appExcEditor import AppExcEditor
from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape
from appObjects.ExcellonObject import ExcellonObject
from appParsers.ParseExcellon import Excellon
from camlib import CNCjob
from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob
from tests.helpers_objects import make_app_0b as make_app, no_shape_collection  # noqa: F401,E402


def drill_shape(dia, x, y):
    # the '+' sign of a drill in the editor, the line length is the tool diameter
    r = dia / 2
    return DrawToolShape(MultiLineString([((x - r, y), (x + r, y)), ((x, y - r), (x, y + r))]))


def slot_shape(dia, start, stop):
    return DrawToolShape(LineString([start, stop]).buffer(dia / 2))


def save(drills=None, slots=None):
    """Run the editor save. drills: {dia: [(x, y), ...]}, slots: {dia: [((x1, y1), (x2, y2)), ...]}.
    Returns the tools dict passed to the new Excellon object."""
    storage_dict = {}
    for dia, points in (drills or {}).items():
        storage = storage_dict.setdefault(dia, AppGeoEditor.make_storage())
        for x, y in points:
            storage.insert(drill_shape(dia, x, y))
    for dia, segments in (slots or {}).items():
        storage = storage_dict.setdefault(dia, AppGeoEditor.make_storage())
        for start, stop in segments:
            storage.insert(slot_shape(dia, start, stop))

    editor = SimpleNamespace(
        storage_dict=storage_dict, olddia_newdia={dia: dia for dia in storage_dict}, new_tools={},
        history=SimpleNamespace(ready=lambda: True, pending=None), _save_excellon_in_place=Mock())
    source = SimpleNamespace(geo_steps_per_circle=64)
    AppExcEditor.update_fcexcellon(editor, source, in_place=True)
    editor._save_excellon_in_place.assert_called_once_with(source, None)
    return editor.new_tools


def summary(tools):
    """{tool number: (diameter, sorted drill centers, sorted slot ends)} with rounded coordinates.
    The save finds the slot ends again from the slot outline and they move a few hundredths of a mm (not the subject
    of these tests), so the slot ends are rounded to 0.1 mm."""
    out = {}
    for tid, tool in tools.items():
        drills = sorted((round(p.x, 2), round(p.y, 2)) for p in tool.get('drills', []))
        slots = sorted(((round(a.x, 1), round(a.y, 1)), (round(b.x, 1), round(b.y, 1)))
                       for a, b in tool.get('slots', []))
        out[tid] = (tool['tooldia'], drills, slots)
    return out


def assert_geometry_matches(tools):
    # one shape for each drill and slot of the tool, made with the diameter of the tool
    for tid, tool in tools.items():
        dia = tool['tooldia']
        drills, slots = tool.get('drills', []), tool.get('slots', [])
        assert len(tool['solid_geometry']) == len(drills) + len(slots), tid
        for poly, point in zip(tool['solid_geometry'], drills):
            assert poly.distance(point) == 0, tid
            assert poly.bounds[2] - poly.bounds[0] == pytest.approx(dia, abs=1e-3), tid
        for poly in tool['solid_geometry'][len(drills):]:
            # all the slots of these tests are horizontal
            assert poly.bounds[3] - poly.bounds[1] == pytest.approx(dia, abs=1e-3), tid


SLOT_10 = ((10.0, 0.0), (14.0, 0.0))
SLOT_15 = ((20.0, 0.0), (25.0, 0.0))

# drills 0.8 / 1.0 / 3.2 mm and slots 1.0 / 1.5 mm (KiCad oval pads and mounting holes)
BOARD = dict(drills={0.8: [(0, 0), (1, 0)], 1.0: [(0, 5)], 3.2: [(30, 30), (-30, 30)]},
             slots={1.0: [SLOT_10], 1.5: [SLOT_15]})
# drill-only tools and a slot-only tool
SLOT_ONLY_TOOL = dict(drills={0.8: [(0, 0)], 3.2: [(30, 30)]}, slots={1.5: [SLOT_15]})


def test_new_slot_diameter_after_a_matching_one_does_not_overwrite_a_drill_tool():
    tools = save(**BOARD)

    assert summary(tools) == {
        1: (0.8, [(0, 0), (1, 0)], []),
        2: (1.0, [(0, 5)], [SLOT_10]),
        3: (3.2, [(-30, 30), (30, 30)], []),
        4: (1.5, [], [SLOT_15]),
    }
    assert_geometry_matches(tools)


# ---------------------------------------------------------------------------------------------------------------------
# the saved tools have the 'drills' and the 'slots' keys
# ---------------------------------------------------------------------------------------------------------------------


def exported_holes(code):
    """{tool number: (sorted drill lines, number of slots)} of the body made by ExcellonObject.export_excellon():
    first the drills of each tool, then from the first G05 the slots of each tool."""
    holes, tool, in_slots = {}, None, False
    for line in code.splitlines():
        if line == 'G05':
            in_slots = True
        elif line.startswith('T'):
            tool = int(line[1:])
            holes.setdefault(tool, [[], 0])
        elif in_slots and line.startswith('G00X'):
            holes[tool][1] += 1
        elif not in_slots and line.startswith('X'):
            holes[tool][0].append(line)
    return {tid: (sorted(drills), slots) for tid, (drills, slots) in holes.items()}


@pytest.mark.parametrize('case, expected', [
    (BOARD, {1: (['X0.000Y0.000', 'X1.000Y0.000'], 0), 2: (['X0.000Y5.000'], 1),
             3: (['X-30.000Y30.000', 'X30.000Y30.000'], 0), 4: ([], 1)}),
], ids=['board'])
def test_excellon_export_of_the_saved_object(no_shape_collection, case, expected):  # noqa: F811
    # the edited object gets the saved tools like AppExcEditor.new_edited_excellon() does
    obj = ExcellonObject('board_edit', make_app())
    obj.units = 'MM'
    obj.tools = deepcopy(save(**case))
    for tool in obj.tools.values():
        tool['data'] = {}
    obj.create_geometry()

    has_slots, code = obj.export_excellon(3, 3, e_zeros='LZ', form='dec', slot_type='routing')
    assert has_slots == 1
    assert exported_holes(code) == expected


def drill_job_gcode(tools):
    """G-code of the Tcl drillcncjob with all the tools ('default' preprocessor) of an Excellon with these tools."""
    app = make_app(tools_drill_toolchange=True)
    app.preprocessors['default'] = importlib.import_module('preprocessors.default').default()
    app.dec_format = round
    app.exc_areas = SimpleNamespace(exclusion_areas_storage=[],
                                    travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    source = Excellon(app)
    source.units = 'MM'
    source.tools = deepcopy(tools)
    for tool in source.tools.values():
        tool['data'] = {}
    source.create_geometry()
    source.kind = 'excellon'
    source.obj_options = {'name': 'board_edit'}
    source.obj_options.update(app.options)
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), source.bounds()))
    app.collection.get_by_name.return_value = source

    job = CNCjob(app)
    job.kind = 'cncjob'
    job.obj_options = {}
    job.excellon_tool_gcode_parse = Mock(return_value=[])

    def create(kind, name, init, **kwargs):
        assert init(job, app) != 'fail'
        return job

    app.app_obj.new_object.side_effect = create
    TclCommandDrillcncjob(app).execute({'name': 'board_edit', 'pp': 'default', 'drilled_dias': 'all'}, [])
    return job.source_file


def drills_by_tool_change(gcode):
    """{tool change message: sorted drill positions after it}; a drill is a rapid X-Y move followed by a plunge"""
    out, message, rapid = {}, None, None
    for line in gcode.splitlines():
        if line.startswith('(MSG, Change to Tool Dia'):
            message = line
            out[message] = []
        elif message and re.fullmatch(r'G00 X-?\d+\.\d{4} Y-?\d+\.\d{4}', line):
            rapid = line[4:]
        elif message and rapid and line.startswith('G01 Z'):
            out[message].append(rapid)
            rapid = None
    return {msg: sorted(positions) for msg, positions in out.items()}


@pytest.mark.parametrize('case, expected', [
    (SLOT_ONLY_TOOL, {
        '(MSG, Change to Tool Dia = 0.8000 ||| Total drills for tool T1 = 1)': ['X0.0000 Y0.0000'],
        '(MSG, Change to Tool Dia = 3.2000 ||| Total drills for tool T2 = 1)': ['X30.0000 Y30.0000'],
    }),
], ids=['slot-only-tool'])
def test_drillcncjob_with_all_tools_of_the_saved_object(case, expected):
    assert drills_by_tool_change(drill_job_gcode(save(**case))) == expected
