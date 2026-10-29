import math
import sys
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest
from shapely import Point, LineString, box
from shapely.ops import unary_union

from appParsers.feature_access import iter_features
from appPlugins.ToolPunchGerber.PunchGerber import ToolPunchGerber
from appPlugins.ToolPunchGerber.PunchGerberGen import punch_geometry
from tests.helpers_objects import _headless_qt, make_app, make_gerber  # noqa: F401

PUNCH_MODULE = sys.modules['appPlugins.ToolPunchGerber.PunchGerber']
ALL_ON = [True] * 5


def _circle_ap(x, y, size=1.0):
    pt = Point(x, y)
    return {'type': 'C', 'size': size, 'geometry': [{'follow': pt, 'solid': pt.buffer(size / 2, quad_segs=16)}]}


def _obround_ap(x, y, w=1.0, h=0.6):
    pt = Point(x, y)
    return {'type': 'O', 'size': math.hypot(w, h), 'width': w, 'height': h,
            'geometry': [{'follow': pt, 'solid': box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)}]}


def _am_ap(x, y, w=1.0, h=0.8):
    pt = Point(x, y)
    return {'type': 'AM', 'geometry': [{'follow': pt, 'solid': box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)}]}


def _solid(tools):
    return unary_union([r['solid'] for t in tools.values() for r in t['geometry'] if 'solid' in r])


def _clears(apertures):
    return [(a['size'], r['clear']) for a in apertures.values() for r in a['geometry'] if 'clear' in r]


def _run(tools, method='fixed', value=0.4, selected=None, **kw):
    return punch_geometry(_solid(tools), tools, list(tools) if selected is None else selected, method, value, 4,
                          ALL_ON, **kw)


def _exc(drills=(), slots=(), dia=0.4):
    tool = {'tooldia': dia, 'drills': list(drills), 'slots': list(slots)}
    geo = [p.buffer(dia / 2) for p in drills] + [LineString(s).buffer(dia / 2) for s in slots]
    return geo, {1: tool}


class FakeCollection:
    def __init__(self):
        self.items = []

    def get_list(self):
        return list(self.items)

    def get_active(self):
        return None

    def get_by_name(self, name):
        return next((o for o in self.items if o.obj_options['name'] == name), None)

    def index(self, row, column, parent=None):
        return SimpleNamespace(internalPointer=lambda: SimpleNamespace(obj=self.items[row]))


class Entry:
    def __init__(self, value=None):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value


class FakeTable:
    def __init__(self, apids):
        self.apids = apids

    def selectedItems(self):
        return [SimpleNamespace(text=lambda a=a: str(a)) for a in self.apids]

    def rowCount(self):
        return 0


@pytest.fixture
def setup():
    app = make_app()
    app.collection = FakeCollection()
    app.event_is_dragging = False
    app.plotcanvas = Mock()
    app.plotcanvas.translate_coords = lambda pos: pos
    app.on_mouse_click_over_plot = Mock()
    app.on_mouse_click_release_over_plot = Mock()
    app.ui = MagicMock()
    app.ui.grid_snap_btn.isChecked.return_value = False
    app.options['global_selection_shape'] = True
    counter = iter(range(1, 1000))
    app.tool_shapes = Mock()
    app.tool_shapes.add = Mock(side_effect=lambda **kw: next(counter))
    app.created = []

    def new_object(kind, name, initialize, **kwargs):
        obj = SimpleNamespace(obj_options={'name': name}, tools={}, solid_geometry=None,
                              regenerate_source=lambda name=None: None)
        initialize(obj, app)
        app.created.append(obj)
        return obj

    app.app_obj.new_object = new_object
    source = make_gerber(app, name='src')
    source.drawing_tolerance = 0.01
    source.clear_plot_apertures = Mock()
    app.collection.items.append(source)

    tool = ToolPunchGerber.__new__(ToolPunchGerber)
    tool.app = app
    tool.decimals = 4
    tool.old_name = ''
    tool.grb_obj = None
    tool.grid_status_memory = False
    tool.poly_sel_disconnect_flag = False
    tool.poly_dict = {}
    tool.manual_pads = []
    tool.old_selection_status = None
    tool.delete_moving_selection_shape = Mock()
    tool.delete_tool_selection_shape = Mock()
    tool.ui = SimpleNamespace(
        punch_type_radio=Entry('m'), method_punch=Entry('fixed'), dia_entry=Entry(0.1),
        circular_ring_entry=Entry(0.1), oblong_ring_entry=Entry(0.1), square_ring_entry=Entry(0.1),
        rectangular_ring_entry=Entry(0.1), other_ring_entry=Entry(0.1), factor_entry=Entry(50.0),
        circular_cb=Entry(True), oblong_cb=Entry(True), square_cb=Entry(True), rectangular_cb=Entry(True),
        other_cb=Entry(True), apertures_table=FakeTable(list(source.tools)),
        gerber_object_combo=SimpleNamespace(currentIndex=lambda: 0, rootModelIndex=lambda: None),
        exc_combo=SimpleNamespace(currentIndex=lambda: 0, rootModelIndex=lambda: None))
    tool.grb_obj = source
    return tool, app, source


def pads_of(tool, source):
    tool.grb_obj = source
    sel = [int(i.text()) for i in tool.ui.apertures_table.selectedItems()]
    return [(r.item_key, r.feature_index, r.solid.representative_point()) for r in iter_features(source)
            if r.item_key in sel and r.polarity == 'dark' and isinstance(r.follow, Point)]


def click(tool, point, button=1):
    tool.on_single_poly_mouse_release(SimpleNamespace(pos=(point.x, point.y), button=button))


def start_manual(tool):
    tool.on_punch_object_click()


def notebook_disabled(app):
    return app.ui.notebook.setDisabled.call_args[0][0]


def test_fixed_punches_circle_and_records_clear_and_aperture():
    tools = {10: _circle_ap(0, 0)}
    solid, aps = _run(tools, value=0.4)
    assert solid.area == pytest.approx(math.pi * (0.25 - 0.04), rel=0.02)
    assert not solid.intersects(Point(0, 0))
    assert not aps[10]['geometry'][0]['solid'].intersects(Point(0, 0))
    assert tools[10]['geometry'][0]['solid'].intersects(Point(0, 0))   # input untouched
    clears = _clears(aps)
    assert len(clears) == 1 and clears[0][0] == 0.4 and clears[0][1].equals(Point(0, 0))
    # hole almost as big as the pad still gets its clear record
    _, aps = _run(tools, value=0.99)
    assert len(_clears(aps)) == 1


@pytest.mark.parametrize('method,value,code', [
    ('fixed', 0.9, 'size'),            # smaller than diagonal 1.166 but not than the 0.6 side
    ('ring', [0.5] * 5, 'zero'),
])
def test_obround_error_codes(method, value, code):
    with pytest.raises(ValueError) as err:
        _run({10: _obround_ap(0, 0)}, method=method, value=value)
    assert err.value.args[0] == code


def test_ring_and_prop_hole_sizes():
    tools = {10: _circle_ap(0, 0)}
    _, aps = _run(tools, method='ring', value=[0.1] * 5)
    assert _clears(aps)[0][0] == pytest.approx(0.8)
    _, aps = _run(tools, method='prop', value=0.5)
    assert _clears(aps)[0][0] == pytest.approx(0.5)


def test_excellon_keeps_clear_features_cuts_only_selected_and_clears_slots():
    tools = {10: _circle_ap(0, 0), 11: _circle_ap(5, 0)}
    tools[12] = {'type': 'C', 'size': 1.0, 'geometry': [{'clear': box(8, 8, 9, 9)}]}
    drills = [Point(0, 0), Point(5, 0)]
    geo, dtools = _exc(drills, slots=[(Point(-0.2, 0), Point(0.2, 0))])
    src = unary_union([_solid({10: tools[10], 11: tools[11]}), box(20, 20, 21, 21)])
    solid, aps = punch_geometry(src, tools, [10], 'exc', None, 4, ALL_ON, drill_geometry=geo, drill_tools=dtools)
    assert not solid.intersects(Point(0, 0)) and solid.intersects(Point(5, 0))
    assert solid.contains(Point(20.5, 20.5))
    clears = [c for c in _clears(aps) if c[0] == 0.4]
    assert any(isinstance(g, Point) for _, g in clears) and any(isinstance(g, LineString) for _, g in clears)
    assert len(clears) == 2
    assert not aps[10]['geometry'][0]['solid'].intersects(Point(0, 0))


def test_right_click_with_pads_creates_punched_and_exits(setup):
    tool, app, source = setup
    pads = pads_of(tool, source)
    start_manual(tool)
    assert notebook_disabled(app) is True
    click(tool, pads[0][2])
    click(tool, pads[0][2], button=2)
    assert [o.obj_options['name'] for o in app.created] == ['src_punched']
    assert notebook_disabled(app) is False
    assert app.options['global_selection_shape'] is True
    app.plotcanvas.graph_event_connect.assert_any_call('mouse_press', app.on_mouse_click_over_plot)
    app.plotcanvas.graph_event_connect.assert_any_call('mouse_release', app.on_mouse_click_release_over_plot)

