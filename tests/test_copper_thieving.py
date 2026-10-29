"""Copper Thieving plugin: the generated geometry and the controller, headless (offscreen Qt, MagicMock app)."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from shapely import GeometryCollection, MultiPolygon, Point, box
from shapely.ops import unary_union

from appParsers.ParseGerber import Gerber  # noqa: E402
from appObjects.GerberObject import GerberObject
from appPlugins.ToolCopperThieving.CopperThievingGen import copper_fill, plating_mask, robber_bar_data
from appPlugins.ToolCopperThieving import ToolCopperThieving
from tests.helpers_objects import make_app, export_gerber_text

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_copper_line_grid_respects_clearance():
    source = box(0, 0, 4, 4)
    fill, merged, tools = copper_fill([source], {}, None, None, 0, 'rect', 0.5, 0.1, 0,
                                      3, 1, 1, 1, 1, 2, 1, 32)
    added = unary_union(fill)
    assert added.intersection(source.buffer(0.5, quad_segs=8)).area < 1e-9


def test_plating_clearance_export_records_describe_actual_openings():
    pad = Point(0, 0).buffer(1)
    tools = {10: {'type': 'C', 'size': 2, 'geometry': [{'solid': pad, 'follow': Point(0, 0)}]}}
    solid, apertures, follows, area = plating_mask([pad], tools, [Point(0, 0)], [], [], [],
                                                  0.25, 3, False)
    exported = []
    for apid, aperture in apertures.items():
        for record in aperture.get('geometry', []):
            exported.append(record['solid'] if apid == 0 else
                            record['follow'].buffer(aperture['size'] / 2))
    assert unary_union(exported).symmetric_difference(solid).area < 1e-9
    assert tools[10]['size'] == 2
    assert area == pytest.approx(solid.area)
    app = make_app()
    code = GerberObject.export_gerber(SimpleNamespace(app=app, tools=apertures), 4, 6)
    parsed = Gerber(app)
    parsed.parse_lines(['%FSLAX46Y46*%', '%MOMM*%', '%ADD10C,2*%', 'D10*', 'G01*'] +
                       code.splitlines() + ['M02*'])
    assert unary_union(parsed.solid_geometry).symmetric_difference(solid).area < 1e-5


@pytest.mark.parametrize("clearance, choice", [(0.1, 1), (0, 1), (-0.1, 1),
                                               (0, 0), (0.1, 2), (0, 3)])
def test_plating_export_preserves_nested_openings(clearance, choice):
    center = Point(5, 5)
    pad = center.buffer(2)
    # Thin enclosing rings have less copper area than the islands they enclose.
    source_ring = box(2, 2, 8, 8).difference(box(2.25, 2.25, 7.75, 7.75))
    thieving = box(0, 0, 10, 10).difference(box(0.25, 0.25, 9.75, 9.75))
    robber_line = box(-1, -1, 11, 11).exterior
    robber = robber_line.buffer(0.15)
    tools = {10: {'type': 'C', 'size': 4, 'geometry': [{'solid': pad, 'follow': center}]},
             20: {'type': 'P', 'size': 0, 'geometry': [
                 {'solid': source_ring, 'follow': source_ring.exterior}]}}
    solid, apertures, follows, area = plating_mask(
        [pad, source_ring], tools, [center, source_ring.exterior], [thieving], robber, robber_line,
        clearance, choice, False)
    expected = [pad.buffer(clearance), source_ring.buffer(clearance)]
    if choice in (0, 1):
        expected.append(thieving.buffer(clearance))
    if choice in (0, 2):
        expected.append(robber.buffer(clearance))
    expected = unary_union(expected)
    assert solid.symmetric_difference(expected).area < 1e-9
    assert area == pytest.approx(expected.area)
    assert len(tools[10]['geometry']) == len(tools[20]['geometry']) == 1
    app = make_app()
    code = GerberObject.export_gerber(SimpleNamespace(app=app, tools=apertures), 4, 6)
    definitions = [f'%ADD{apid}C,{aperture["size"]}*%' for apid, aperture in apertures.items()
                   if apid and aperture['type'] == 'C']
    parsed = Gerber(app)
    parsed.parse_lines(['%FSLAX46Y46*%', '%MOMM*%', '%ADD99C,0.01*%'] + definitions +
                       ['D99*', 'G01*'] + code.splitlines() + ['M02*'])
    assert unary_union(parsed.solid_geometry).symmetric_difference(expected).area < 1e-5


def test_plating_export_keeps_pad_inside_buffered_thieving_hole():
    center = Point(5, 5)
    pad = center.buffer(1)
    thieving = box(0, 0, 10, 10).difference(pad.buffer(0.5))
    tools = {2: {'type': 'C', 'size': 2, 'geometry': [{'solid': pad, 'follow': center}]}}
    solid, apertures, follows, area = plating_mask(
        pad, tools, [center], thieving, None, None, 0.1, 1, True)
    expected = unary_union([pad.buffer(0.1), thieving.buffer(0.1)])
    assert solid.equals(expected)
    assert area == pytest.approx(expected.area)
    assert len(follows) == 2 and follows[0].equals(center) and follows[1].equals(thieving.exterior)
    app = make_app()
    code = GerberObject.export_gerber(SimpleNamespace(app=app, tools=apertures), 4, 6)
    parsed = Gerber(app)
    parsed.parse_lines(['%FSLAX46Y46*%', '%MOMM*%', '%ADD10C,2*%', 'D10*', 'G01*'] +
                       code.splitlines() + ['M02*'])
    assert unary_union(parsed.solid_geometry).symmetric_difference(expected).area < 1e-5


def test_robber_bar_preserves_collection_source_and_single_follow():
    source = box(0, 0, 4, 4)
    follow = source.exterior
    line, bar, solid, tools, follows = robber_bar_data(GeometryCollection([source]), {}, follow, 1, 0.5)
    assert solid.equals(unary_union([source, bar]))
    assert len(follows) == 2
    assert follows[0].equals(follow)


def test_plating_negative_clearance_can_split_thieving_openings():
    thieving = unary_union([box(0, 0, 2, 2), box(3, 0, 5, 2), box(1, 0.8, 4, 1.2)])
    expected = thieving.buffer(-0.3)
    solid, tools, follows, area = plating_mask([], {}, [], [thieving], [], [], -0.3, 1, False)
    assert isinstance(solid, MultiPolygon)
    assert solid.equals(expected)
    assert unary_union([record['solid'] for record in tools[0]['geometry']]).equals(expected)
    assert area == pytest.approx(expected.area)


# ---------------------------------------------------------------------------------------------------------------
# the controller, called unbound on a namespace so that no window is built
# ---------------------------------------------------------------------------------------------------------------

METHODS = ('_selected_object', '_end_area_selection', 'on_exit', 'on_plugin_cleanup', 'connect_event_handlers',
           'disconnect_event_handlers', 'delete_tool_selection_shape', 'on_mouse_release', 'on_mouse_move',
           'on_add_copper_thieving_click', 'on_add_robber_bar_click', 'on_add_ppm_click', 'copper_thieving')


def make_tool(source=None, **ui_values):
    """A tool with the real methods, a mock app and a mock UI whose entries return ui_values."""
    app = MagicMock()
    app.abort_flag = False
    app.event_is_dragging = False
    app.grid_status.return_value = False
    app.call_source = "app"
    app.plotcanvas.translate_coords.side_effect = lambda pos: (pos[0] / 10, pos[1] / 10)
    ui = MagicMock()
    values = dict(reference_combo=0, bbox_type_radio='rect', area_entry=0, fill_type_combo=0, rb_margin_entry=1,
                  rb_thickness_entry=0.5, clearance_entry=0.5, margin_entry=2, clearance_ppm_entry=0,
                  ppm_choice_combo=3, only_pads_cb=False, dot_dia_entry=1, dot_spacing_entry=1,
                  square_size_entry=1, squares_spacing_entry=1, line_size_entry=1, lines_spacing_entry=1)
    values.update(ui_values)
    for name, value in values.items():
        getattr(ui, name).get_value.return_value = value
    tool = SimpleNamespace(app=app, ui=ui, units='MM', geo_steps_per_circle=32, handlers_connected=False,
                           sel_rect=[], first_click=False, cursor_pos=(0, 0), grb_object=source, sm_object=None,
                           ref_obj=None, thief_solid_geometry=[], robber_geo=None, robber_line=None,
                           clearance_val=0.5, margin_val=2, plated_area_ready=MagicMock())
    for name in METHODS:
        setattr(tool, name, getattr(ToolCopperThieving, name).__get__(tool))
    tool.draw_tool_selection_shape = lambda **kw: None
    app.collection.index.return_value.internalPointer.return_value.obj = source
    return tool


def source_gerber():
    pad = box(4, 4, 6, 6)
    ring = box(2, 2, 8, 8).difference(box(2.1, 2.1, 7.9, 7.9))
    return SimpleNamespace(obj_options={'name': 'board'}, solid_geometry=[pad, ring], follow=False,
                           tools={0: {'type': 'REG', 'size': 0, 'geometry': [
                               {'solid': poly, 'follow': poly.exterior} for poly in [pad, ring]]}},
                           follow_geometry=[pad.exterior, ring.exterior], kind='gerber')


def record_objects(tool, export_app=None):
    outputs = []

    def new_object(kind, name, initialize, **kwargs):
        obj = SimpleNamespace(app=export_app, export_as_regions=False)
        obj.export_gerber = lambda *args, **kw: GerberObject.export_gerber(obj, *args, **kw)
        obj.regenerate_source = lambda **kw: outputs.append((obj, export_gerber_text(export_app, obj)
                                                              if export_app else None))
        initialize(obj, tool.app)

    tool.new_object = new_object
    return outputs


def messages(tool):
    return [call.args[0] for call in tool.app.inform.emit.call_args_list]


def test_copper_generation_export_preserves_source_region_islands():
    source = source_gerber()
    export_app = make_app()
    tool = make_tool(source)
    outputs = record_objects(tool, export_app)
    for clearance in (0.5, 0):
        tool.copper_thieving(source, c_val=clearance, margin=2, run_threaded=False)
    tool.on_add_robber_bar_click()
    assert len(outputs) == 3
    differences = []
    for obj, code in outputs:
        parsed = Gerber(export_app)
        parsed.parse_lines(code.splitlines())
        differences.append(unary_union(parsed.solid_geometry).symmetric_difference(
            unary_union(obj.solid_geometry)).area)
        assert obj.tools[0]['geometry'][0]['solid'].equals(source.solid_geometry[0])
    # the thief and the robber follow geometry keeps the source and adds the new shapes
    assert len(outputs[0][0].follow_geometry) > len(source.follow_geometry)
    assert len(outputs[2][0].follow_geometry) == len(source.follow_geometry) + 1
    assert len(source.tools[0]['geometry']) == 2
    assert differences == pytest.approx([0, 0, 0], abs=1e-5)


def test_zero_grid_reports_parameter_and_creates_nothing():
    source = source_gerber()
    tool = make_tool(source, fill_type_combo=1, dot_dia_entry=0)
    outputs = record_objects(tool)
    tool.copper_thieving(source, c_val=0.5, margin=1, run_threaded=False)
    assert any(m.startswith('[ERROR_NOTCL]') and 'Grid size' in m for m in messages(tool))
    assert not any('No object available' in m for m in messages(tool))
    assert outputs == []


@pytest.mark.parametrize("case", ['empty source', 'robber thickness', 'nothing to fill', 'no reference object'])
def test_nothing_is_created_and_user_is_warned(case):
    source = source_gerber()
    ui_values = {}
    if case == 'empty source':
        source.solid_geometry = []
    elif case == 'robber thickness':
        ui_values = dict(rb_thickness_entry=0)
    elif case == 'nothing to fill':
        ui_values = dict(fill_type_combo=1, dot_dia_entry=100)
    else:
        ui_values = dict(reference_combo=2)
    tool = make_tool(source, **ui_values)
    outputs = record_objects(tool)
    tool.ui.ref_combo.currentText.return_value = ''
    tool.app.collection.get_by_name.return_value = None
    tool.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    if case == 'robber thickness':
        tool.on_add_robber_bar_click()
    else:
        tool.on_add_copper_thieving_click()
    assert outputs == []
    assert any(m.startswith(('[WARNING_NOTCL]', '[ERROR_NOTCL]')) for m in messages(tool))
    assert not any(m.startswith('[success]') for m in messages(tool))


def test_area_selection_ignores_a_drag_and_uses_world_coordinates():
    tool = make_tool(source_gerber(), reference_combo=1)
    # the app drag flag is not updated while the plugin owns the canvas events: only the event counts
    tool.app.event_is_dragging = True
    tool.on_mouse_release(SimpleNamespace(button=1, pos=(100, 200), is_dragging=True))
    assert tool.first_click is False
    tool.on_mouse_release(SimpleNamespace(button=1, pos=(100, 200), is_dragging=False))
    assert tool.first_click is True and tool.cursor_pos == (10, 20)

