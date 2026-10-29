"""Isolation plugin: isolate() with the passes not combined and no specific geometry.

With 'Combine' off and the selection 'All' or 'Area Selection', ToolIsolation.isolate() gets geometry=None. It called
flatten_shapely_geometry(None), which raises NotImplementedError, so no Geometry object was made. The tests build the
real ToolIsolation headless and isolate a parsed Gerber object (fixtures of tests.helpers_objects).
"""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from defaults import AppDefaults
from appObjects.GeometryObject import GeometryObject
from appPlugins.ToolIsolation import ToolIsolation

from test_plugin_geometry_bases import make_tool_app
from tests.helpers_objects import app, _headless_qt, make_gerber  # noqa: F401,E402

DIA = 0.2


def tools_storage(passes):
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    data.update(tools_iso_passes=passes, tools_iso_overlap=10, tools_iso_isotype='full',
                tools_iso_milling_type='cl')
    return {1: {'tooldia': DIA, 'data': data, 'solid_geometry': []}}


@pytest.fixture(scope='module')
def tool(_headless_qt):  # noqa: F811
    return ToolIsolation(make_tool_app())


class Created(dict):
    """{object name: Geometry object built by the init callback}; .result is what isolate() returned."""
    result = None


def run_isolate(tool, app, gerber, passes, storage=None, sel_tools=(1,), **kwargs):
    """Calls the plugin isolate() (combine off unless given)."""
    created = Created()

    def new_object(kind, name, initialize, plot=True, **kw):
        assert kind == 'geometry'
        obj = GeometryObject(name, app)
        if initialize(obj, app) == 'fail':
            return 'fail'
        created[name] = obj
        return 'ok'

    tool.app.app_obj.new_object = Mock(side_effect=new_object)
    options = dict(combine=False, iso_except=False, e_passes=0, area_except=False, area_shape='square', rest=False,
                   sel_type=0, tip_shape='C1', iso_simplification=False, simplification_tol=0.001)
    options.update(kwargs)
    created.result = tool.isolate(isolated_obj=gerber, sel_tools=list(sel_tools), plot=False,
                                  tools_storage=storage if storage is not None else tools_storage(passes), **options)
    return created


def two_tools(passes=1):
    """Tool 1 (0.2) and tool 2 (0.5)."""
    storage = tools_storage(passes)
    storage[2] = deepcopy(storage[1])
    storage[2]['tooldia'] = 0.5
    return storage


def pads_gerber(app):
    """2 pads 20 mm apart (the second one is not selected in the tests) and a clear flash without 'solid'."""
    gerber = make_gerber(app, name='g')
    pad_a, pad_b = Point(0, 0).buffer(1), Point(20, 0).buffer(1)
    gerber.solid_geometry = [pad_a, pad_b]
    gerber.tools = {10: {'geometry': [{'solid': pad_a, 'follow': Point(0, 0)},
                                      {'solid': pad_b, 'follow': Point(20, 0)},
                                      {'follow': Point(5, 5)}]}}
    return gerber, pad_a, pad_b


def wkb_set(geometries):
    return sorted(g.wkb_hex for g in geometries if not g.is_empty)


@pytest.mark.parametrize('passes', [3])
def test_whole_object_makes_one_geometry_per_pass(tool, app, passes):
    created = run_isolate(tool, app, make_gerber(app, name='g'), passes)

    names = ['g_0.2000_iso'] if passes == 1 else ['g_0.2000_iso%d' % (i + 1) for i in range(passes)]
    assert sorted(created) == names
    for obj in created.values():
        assert len(obj.solid_geometry) > 0
        assert obj.tools[1]['tooldia'] == DIA
        assert obj.tools[1]['solid_geometry'] == obj.solid_geometry


def test_area_selection_with_separate_passes(tool, app):
    gerber = make_gerber(app, name='g')
    xmin, ymin, xmax, ymax = unary_union(gerber.solid_geometry).bounds
    area = box(xmin - 1, ymin - 1, (xmin + xmax) / 2, ymax + 1)

    created = run_isolate(tool, app, gerber, 2, limited_area=area)

    assert sorted(created) == ['g_0.2000_iso1', 'g_0.2000_iso2']
    for obj in created.values():
        assert len(obj.solid_geometry) > 0
        assert unary_union(obj.solid_geometry).within(area.buffer(1e-6))


# ------------------------------------------------------------------------------------------- the run and its tools

def test_no_object_is_made_for_an_empty_selection_a_stop_or_an_empty_area(tool, app, monkeypatch):
    gerber = make_gerber(app, name='g')
    # an empty selection is not the whole board
    for geometry in ([], Polygon()):
        for mode in (dict(), dict(combine=True), dict(combine=True, rest=True)):
            created = run_isolate(tool, app, gerber, 1, geometry=geometry, **mode)
            assert created.result == 'fail'
            assert not created
    # no geometry inside the area
    for combine in (False, True):
        assert not run_isolate(tool, app, gerber, 1, combine=combine, limited_area=box(900, 900, 901, 901))
    # a stop by the user
    monkeypatch.setattr(tool.app, 'abort_flag', True)
    for combine in (False, True):
        created = run_isolate(tool, app, gerber, 2, combine=combine)
        assert created.result == 'fail'
        assert not created


# ------------------------------------------------------------------------------------------------------- pad passes

def test_pad_passes_follow_only_the_isolated_pads_and_start_after_the_last_pass(tool, app):
    gerber, pad_a, pad_b = pads_gerber(app)

    for combine in (False, True):
        (obj,) = run_isolate(tool, app, gerber, 1, geometry=[pad_a], combine=combine, e_passes=2).values()
        # the pass and the 2 pad rings of the first pad, nothing around the second pad
        assert len(obj.solid_geometry) == 3
        assert unary_union(obj.solid_geometry).bounds[2] < 3

    created = run_isolate(tool, app, gerber, 3, geometry=[pad_a], e_passes=1)
    first, second, last = (created['g_0.2000_iso%d' % n] for n in (1, 2, 3))
    assert len(first.solid_geometry) == len(second.solid_geometry) == 1
    assert len(last.solid_geometry) == 2
    # pass offsets 0.1, 0.28, 0.46 and the pad ring after them 0.64
    assert unary_union(second.solid_geometry).bounds[2] == pytest.approx(1.28, abs=0.02)
    assert unary_union(last.solid_geometry).bounds[2] == pytest.approx(1.64, abs=0.02)


# ------------------------------------------------------------------------------------------------- rest machining

def test_rest_machining_uses_the_selected_tools_largest_first_and_survives_a_failed_geometry(tool, app, monkeypatch):
    gerber, __, __ = pads_gerber(app)

    # the smaller tool first, as in the Tool Table: the 0.5 tool still goes first and isolates both pads
    for geometry in (None, MultiPolygon([Point(0, 0).buffer(1), Point(20, 0).buffer(1)])):
        created = run_isolate(tool, app, gerber, 1, storage=two_tools(), sel_tools=[1, 2], geometry=geometry,
                              combine=True, rest=True, forced_rest=False, order=1)

        (obj,) = created.values()
        assert list(obj.tools) == [2]
        assert unary_union(obj.solid_geometry).bounds[2] == pytest.approx(21.25, abs=0.02)

    monkeypatch.setattr(tool, 'generate_rest_geometry', Mock(return_value=('fail', 'fail')))
    created = run_isolate(tool, app, gerber, 1, combine=True, rest=True, forced_rest=False, order=0)
    assert created.result == 'fail'
    assert not created


# ------------------------------------------------------------------ several Gerber objects in one run

@pytest.mark.parametrize('multi, selection, expected', [
    (True, 0, ['a_0.2000_iso', 'b_0.2000_iso']),
    (True, 3, ['a_0.2000_iso', 'b_0.2000_iso']),
    (False, 0, ['a_0.2000_iso']),
])
def test_selected_gerbers_are_isolated_in_one_run(tool, app, multi, selection, expected):
    gerber_a, gerber_b = make_gerber(app, name='a'), make_gerber(app, name='b')
    created = Created()

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        if initialize(obj, app) == 'fail':
            return 'fail'
        created[name] = obj
        return 'ok'

    objects = {'a': gerber_a, 'b': gerber_b}
    tool.app.app_obj.new_object = Mock(side_effect=new_object)
    tool.app.worker_task.emit = lambda task: task['fcn'](*task['params'])
    tool.app.collection.get_by_name = lambda name: objects.get(name)
    tool.app.collection.get_selected = lambda: [gerber_a, gerber_b]
    tool.iso_tools = tools_storage(1)
    tool.build_ui()
    tool.ui.tools_table.selectAll()
    tool.ui.object_combo.currentText = lambda: 'a'
    tool.ui.valid_cb.set_value(False)
    tool.ui.combine_passes_cb.set_value(False)
    tool.ui.rest_cb.set_value(False)
    tool.ui.except_cb.set_value(False)
    tool.ui.simplify_cb.set_value(False)
    tool.ui.pad_passes_entry.set_value(0)
    tool.ui.select_combo.set_value(selection)
    tool.ui.multi_objects_cb.set_value(multi)
    if selection == 3:
        tool.ui.reference_combo.get_value = lambda: 'ref'
        objects['ref'] = make_gerber(app, name='ref')
        objects['ref'].solid_geometry = []

    tool.on_iso_button_click()

    assert sorted(created) == expected


@pytest.mark.parametrize('change', ['edit'])
def test_generate_uses_the_tools_of_the_click(tool, app, change):
    expected = run_isolate(tool, app, make_gerber(app, name='a'), 1, storage=tools_storage(1), combine=True)
    (reference,) = expected.values()

    gerber = make_gerber(app, name='a')
    created = Created()

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        if initialize(obj, app) == 'fail':
            return 'fail'
        created[name] = obj
        return 'ok'

    tasks = []
    tool.app.app_obj.new_object = Mock(side_effect=new_object)
    tool.app.worker_task.emit = tasks.append
    tool.app.collection.get_by_name = lambda name: gerber
    tool.app.collection.get_selected = lambda: []
    tool.iso_tools = tools_storage(1)
    tool.build_ui()
    tool.ui.tools_table.selectAll()
    tool.ui.object_combo.currentText = lambda: 'a'
    for cb, value in ((tool.ui.valid_cb, False), (tool.ui.combine_passes_cb, True), (tool.ui.rest_cb, False),
                      (tool.ui.except_cb, False), (tool.ui.simplify_cb, False), (tool.ui.pad_passes_entry, 0),
                      (tool.ui.select_combo, 0), (tool.ui.multi_objects_cb, False)):
        cb.set_value(value)

    tool.on_iso_button_click()

    # the Tool Table changes between the Generate click and the worker run
    if change == 'edit':
        tool.iso_tools[1]['tooldia'] = 0.8
    else:
        tool.iso_tools.clear()

    (task,) = tasks
    task['fcn'](*task['params'])

    (obj,) = created.values()
    assert obj.tools[1]['tooldia'] == DIA
    assert wkb_set(obj.solid_geometry) == wkb_set(reference.solid_geometry)
