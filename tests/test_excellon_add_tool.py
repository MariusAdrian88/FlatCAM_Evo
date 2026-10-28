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

from copy import deepcopy
from unittest.mock import Mock

import pytest
from shapely.geometry import Point

from appParsers.ParseExcellon import Excellon
from tclCommands.TclCommandAddDrill import TclCommandAddDrill
from tclCommands.TclCommandAddSlot import TclCommandAddSlot
from tclCommands.TclCommandAlignDrillGrid import TclCommandAlignDrillGrid
from tests.helpers_objects import make_app_0b as make_app, make_excellon_0b as make_excellon  # noqa: F401,E402


def fresh(default_data=None):
    ex = Excellon(make_app())
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
    def test_first_id_is_1_and_ids_grow(self):
        ex = fresh()
        assert ex.add_tool(0.8) == 1
        assert ex.add_tool(1.0) == 2
        assert list(ex.tools) == [1, 2]

    def test_next_id_is_max_plus_1_not_len_plus_1(self):
        ex = fresh()
        ex.tools = {1: {}, 5: {}}
        assert ex.add_tool(0.8) == 6


    def test_explicit_id_is_used_as_given(self):
        ex = fresh()
        assert ex.add_tool(0.8, tool_id='1') == '1'
        assert list(ex.tools) == ['1']

    def test_existing_id_raises_and_keeps_the_tool(self):
        ex = fresh()
        ex.add_tool(0.8)
        with pytest.raises(ValueError):
            ex.add_tool(1.0, tool_id=1)
        assert ex.tools[1]['tooldia'] == 0.8


    def test_data_argument_is_deep_copied(self):
        ex = fresh(default_data={'from_default': 1})
        data = {'x': {'y': 1}}
        tid = ex.add_tool(1.0, data=data)
        assert ex.tools[tid]['data'] == {'x': {'y': 1}}
        data['x']['y'] = 2
        assert ex.tools[tid]['data']['x']['y'] == 1

    def test_two_tools_do_not_share_data(self):
        ex = fresh(default_data={'a': [1]})
        t1, t2 = ex.add_tool(1.0), ex.add_tool(2.0)
        ex.tools[t1]['data']['a'].append(2)
        assert ex.tools[t2]['data']['a'] == [1]


# ---------------------------------------------------------------------------------------------------------------------
# Tcl sites: literal expectations, valid before and after the move to add_tool
# ---------------------------------------------------------------------------------------------------------------------

def _tcl_app():
    app = make_app()
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

def _old_way(default_data, tools):
    """What the sites did: assign a (deep copied) dict, then create_geometry."""
    ex = fresh(default_data)
    ex.tools = deepcopy(tools)
    ex.create_geometry()
    return ex


def _drop_empty_slots(tools):
    """The old dicts of some sites had no 'slots' key; add_tool always has the (empty) key."""
    return [(tid, {k: v for k, v in tool.items() if not (k == 'slots' and v == [])}) for tid, tool in snap(tools)]


@pytest.mark.parametrize('default_data', [{}, {'tools_drill_ppname_e': 'default', 'x': 1}])
class TestPluginPatterns:
    def test_markers_corner_drills(self, default_data):
        pts = [Point(1, 1), Point(5, 1)]
        old = _old_way(default_data, {1: {'tooldia': 0.5, 'drills': pts, 'slots': [], 'data': {},
                                          'solid_geometry': []}})
        new = fresh(default_data)
        new.add_tool(0.5, drills=pts, data={})
        new.create_geometry()
        assert snap(new.tools) == snap(old.tools)


