"""Follow plugin core (appPlugins/ToolFollow/FollowGen.py): Gerber follow paths to milling paths with Shapely only."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from shapely import LineString, Point
from shapely.geometry import box

from test_tcl_audit_generation import factory_app
from test_tcl_audit_regressions import FCShell

# FollowGen is Qt free; load it by path so the plugin package (which imports Qt) is not needed
_path = Path(__file__).resolve().parent.parent / 'appPlugins' / 'ToolFollow' / 'FollowGen.py'
_spec = importlib.util.spec_from_file_location('FollowGen_under_test', _path)
fg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fg)


def total_length(lines):
    return sum(g.length for g in lines)


def source(tools, follow=None):
    if follow is None:
        follow = [el['follow'] for t in tools.values() for el in t['geometry']]
    return NS(follow_geometry=follow, tools=tools)


def params(**kw):
    base = dict(union=False, simplify=False, tolerance=0.0, merge=True, selection=None, apertures=None,
                dia_mode='default', tooldia=0.1)
    base.update(kw)
    return base


TOOLS = {
    10: {'size': 0.2, 'geometry': [{'follow': LineString([(0, 0), (5, 0)])}, {'follow': Point(9, 9)}]},
    11: {'size': 0.4, 'geometry': [{'follow': LineString([(0, 5), (5, 5)])}]},
    12: {'size': 0.2, 'geometry': [{'follow': LineString([(0, 8), (5, 8)])}]},
    0: {'size': 0.0, 'geometry': [{'follow': LineString([(0, 10), (5, 10)])}]},
}


def test_merge_joins_touching_lines():
    lines = [LineString([(0, 0), (1, 0)]), LineString([(1, 0), (2, 0)]), LineString([(5, 5), (6, 5)])]
    assert len(fg.clean_paths(lines, False, False, 0.0, merge=True)) == 2
    assert len(fg.clean_paths(lines, False, False, 0.0, merge=False)) == 3


def test_selection_keeps_inside_clips_crossing_drops_outside():
    inside = LineString([(1, 1), (2, 1)])
    crossing = LineString([(1, 5), (9, 5)])
    outside = LineString([(20, 20), (30, 20)])
    result = fg.select_paths([inside, crossing, outside], [box(0, 0, 4, 10)])
    assert total_length(result) == pytest.approx(1 + 3)
    assert fg.select_paths([outside], [box(0, 0, 4, 10)]) == []
    assert len(fg.select_paths([inside, outside], None)) == 2


def test_aperture_filter_uses_only_the_chosen_apertures():
    groups = fg.follow_groups(source(TOOLS), params(apertures=[11]))
    assert len(groups) == 1
    assert groups[0][0] == 0.1
    assert total_length(groups[0][1]) == pytest.approx(5.0)


def test_aperture_dia_mode_groups_by_size_and_regions_use_default_dia():
    groups = fg.follow_groups(source(TOOLS), params(dia_mode='aperture'))
    by_dia = {dia: total_length(lines) for dia, lines in groups}
    assert by_dia == {0.1: pytest.approx(5.0), 0.2: pytest.approx(10.0), 0.4: pytest.approx(5.0)}


@pytest.mark.parametrize('dia_mode', ['aperture'])
def test_a_polygon_follow_geometry_without_aperture_data_is_followed(dia_mode):
    """A Gerber from the DXF import keeps a Polygon in follow_geometry and has no aperture follow data."""
    groups = fg.follow_groups(source({}, follow=box(0, 0, 2, 2)),
                              params(union=True, simplify=True, tolerance=0.01, dia_mode=dia_mode))
    assert len(groups) == 1 and groups[0][0] == 0.1
    assert total_length(groups[0][1]) == pytest.approx(8.0)


# ---------------------------------------------------------------------------------------------------------------
# the controller (appPlugins/ToolFollow/Follow.py)
# ---------------------------------------------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from appPlugins.ToolFollow import ToolFollow

_METHODS = ('collect', '_opt', '_combo_object', '_set_busy', '_start', 'on_generate_geometry_click', 'worker_job',
            'check_abort', 'on_job_done', 'finish', 'store_values', 'draw_preview', 'clear_preview',
            'start_area_selection', 'end_area_selection', 'finish_area_selection', 'on_key_press',
            'on_mouse_release', 'on_mouse_move')


def gerber(name):
    follow = [LineString([(0, 0), (5, 0)]), LineString([(5, 0), (5, 5)])]
    return NS(kind='gerber', obj_options={'name': name}, follow_geometry=follow,
              tools={10: {'type': 'C', 'size': 0.2, 'geometry': [{'follow': f} for f in follow]}})


def make_tool(sources, selected=None, select='all', **ui_values):
    app = MagicMock()
    app.defaults = None
    app.options = {'tools_mill_tooldia': 0.1, 'geometry_cutz': -0.1, 'tools_mill_feedrate': 50.0,
                   'tools_follow_suffix': '_follow', 'global_cursor_width': 2, 'global_cursor_size': 20}
    app.collection.get_names.return_value = ['a_follow']
    app.collection.get_selected.return_value = sources if selected is None else selected
    app.collection.get_by_name.side_effect = lambda n: next((s for s in sources if s.obj_options['name'] == n), None)
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    app.proc_container.new.side_effect = lambda *a: contextmanager(lambda: (yield))()
    app.abort_flag = False
    app.plotcanvas.translate_coords.return_value = (0, 0)
    app.grid_status.return_value = False
    values = dict(simplify_cb=False, tol_entry=0.0, union_cb=False, merge_cb=True, dia_mode_radio='default',
                  select_method_radio=select, area_shape_radio='square')
    values.update(ui_values)
    ui = MagicMock()
    for key, value in values.items():
        getattr(ui, key).get_value.return_value = value
    ui.object_combo.currentText.return_value = sources[0].obj_options['name']
    ui.selected_apertures.return_value = None
    tool = NS(app=app, ui=ui, decimals=4, pluginName='Follow', _busy=False, _preview_drawn=False, selecting=False,
              pending_params=None, first_click=False, cursor_pos=None, points=[], poly_drawn=False, sel_rect=[],
              mouse_is_dragging=False, cursor_width=1, cursor_size=20, job_done=MagicMock(), created=[],
              in_group=False)
    for name in _METHODS:
        setattr(tool, name, getattr(ToolFollow, name).__get__(tool))
    tool.delete_moving_selection_shape = MagicMock()
    tool.delete_tool_selection_shape = MagicMock()
    tool.job_done.emit.side_effect = tool.on_job_done

    def new_object(kind, name, init, **kwargs):
        obj = NS(obj_options={}, in_group=tool.in_group)
        init(obj, app)
        tool.created.append((kind, name, obj))
        return obj

    tool.new_object = new_object
    return tool


def messages(tool):
    return [c.args[0] for c in tool.app.inform.emit.call_args_list if c.args]


def test_generate_makes_one_geometry_per_source_in_one_undo_step_with_unique_names():
    a, b = gerber('a.gbr'), gerber('b')
    tool = make_tool([a, b])
    steps = []

    @contextmanager
    def group(name):
        steps.append(name)
        tool.in_group = True
        yield
        tool.in_group = False

    with patch('appPlugins.ToolFollow.Follow.history_of') as history:
        history.return_value.group = group
        tool.on_generate_geometry_click()
    assert steps == ['Follow']
    assert [(k, n) for k, n, _o in tool.created] == [('geometry', 'a_follow_1'), ('geometry', 'b_follow')]
    assert all(o.in_group for _k, _n, o in tool.created)
    obj = tool.created[1][2]
    assert obj.multigeo is True and obj.obj_options['tools_mill_tooldia'] == 0.1
    tool_1 = obj.tools[1]
    assert tool_1['tooldia'] == 0.1 and tool_1['offset'] == 'Path'
    assert tool_1['data']['name'] == 'b_follow' and tool_1['data']['cutz'] == -0.1
    assert total_length(obj.solid_geometry) == pytest.approx(10.0)
    assert any(m.startswith('[success]') and 'b_follow' in m for m in messages(tool))


def test_area_job_uses_the_zones_copied_in_the_gui_thread():
    tool = make_tool([gerber('a')], select='area')
    tool.on_generate_geometry_click()
    assert tool.selecting and not tool.app.worker_task.emit.called
    tool.sel_rect.append(box(-1, -1, 6, 1))
    tool.on_mouse_release(NS(pos=(0, 0), button=2))
    assert not tool.selecting and tool.sel_rect == []
    (_kind, _name, obj), = tool.created
    # the horizontal path is inside the zone, the vertical one is clipped to its first unit
    assert total_length(obj.solid_geometry) == pytest.approx(6.0)


# Tcl follow


def follow_source():
    # two touching segments (one path after the merge), a flash (a Point) and a second aperture size
    tools = {
        10: {'size': 0.2, 'geometry': [{'follow': LineString([(0, 0), (5, 0)])},
                                       {'follow': LineString([(5, 0), (10, 0)])},
                                       {'follow': Point(20, 20)}]},
        11: {'size': 0.4, 'geometry': [{'follow': LineString([(0, 5), (10, 5)])}]},
    }
    follow = [el['follow'] for t in tools.values() for el in t['geometry']]
    return NS(kind='gerber', obj_options={'name': 'cu.gtl'}, follow_geometry=follow, tools=tools)


def run_follow(args, source=None):
    from tclCommands.TclCommandFollow import TclCommandFollow
    app = factory_app(source or follow_source())
    app.decimals = 4
    TclCommandFollow(app).execute({'name': 'cu.gtl', **args}, [])
    return app


def created(app):
    kind, name, init = app.app_obj.new_object.call_args[0]
    new = Mock()
    new.obj_options = {}
    init(new, app)
    return kind, name, new


def test_default_call_makes_a_geometry_with_merged_lines_and_no_points():
    app = run_follow({})
    kind, name, new = created(app)
    assert (kind, name) == ('geometry', 'cu_follow')
    assert app.app_obj.new_object.call_args[1]['plot'] is False
    assert new.multigeo is True
    assert all(isinstance(g, LineString) for g in new.solid_geometry)
    assert sorted(g.length for g in new.solid_geometry) == pytest.approx([10.0, 10.0])
    assert new.tools[1]['tooldia'] == pytest.approx(2.4)
    assert new.tools[1]['solid_geometry'] == new.solid_geometry


def test_box_clips_the_paths():
    _kind, _name, new = created(run_follow({'box': '0,-1,4,1', 'outname': 'part'}))
    assert [g.bounds for g in new.solid_geometry] == [pytest.approx((0, 0, 4, 0))]


@pytest.mark.parametrize('args', [{'box': '100,100,110,110'}, {'dia_mode': 'nope'}])
def test_bad_input_or_empty_result_is_a_tcl_error_and_creates_nothing(args):
    from tclCommands.TclCommandFollow import TclCommandFollow
    app = factory_app(follow_source())
    with pytest.raises(FCShell.TclErrorException):
        TclCommandFollow(app).execute({'name': 'cu.gtl', **args}, [])
    app.app_obj.new_object.assert_not_called()

