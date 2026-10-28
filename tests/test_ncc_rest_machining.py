"""Exercise the NCC plugin without starting the application or plotting: the workers (GUI, Tcl and the geometry
kernels), then the controller and panel (tool table, area selection, exclusion zones, preview, several objects, Optimal
button) built headless with offscreen Qt and a MagicMock app."""

import functools
import os
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6 import QtWidgets  # noqa: E402
from PyQt6.QtCore import QEvent, QStringListModel  # noqa: E402
from shapely.geometry import LineString, MultiPolygon, Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from appMain import App  # noqa: E402
from appPlugins.ToolNCC import NccModel  # noqa: E402
from appPlugins.ToolNCC.Ncc import ToolNcc  # noqa: E402
from appPlugins.ToolNCC.NccGen import NccGen  # noqa: E402
from defaults import AppDefaults  # noqa: E402
from test_plugin_geometry_bases import make_tool_app  # noqa: E402

TOOL_DATA = {
    'tools_ncc_overlap': 20, 'tools_ncc_method': 0, 'tools_ncc_connect': False, 'tools_ncc_contour': True,
    'tools_ncc_offset_choice': False, 'tools_ncc_offset_value': 0.0,
}


def make_gen(tools, known=None, options=None, units='MM'):
    gen = NccGen.__new__(NccGen)
    defaults = {'tools_ncc_plotting': 'normal', 'tools_ncc_cutz': -0.1, 'tools_ncc_tools': '(4, 1)'}
    defaults.update(options or {})
    app = SimpleNamespace(
        units=units, app_units=units, options=defaults,
        defaults=SimpleNamespace(get=defaults.get, factory_defaults=defaults, report_usage=Mock()),
        log=Mock(), inform=Mock(), inform_shell=Mock(), proc_container=Mock(), abort_flag=False,
        dec_format=lambda value, decimals: round(value, decimals),
    )
    gen.app = app
    gen.decimals = 4
    gen.circle_steps = 32
    gen.obj_name = 'board'
    gen.check_validity = False
    ncc_tools = {
        uid: {'tooldia': dia, 'data': dict(TOOL_DATA), 'solid_geometry': []}
        for uid, dia in enumerate(tools if known is None else known, 1)
    }
    gen.ui = SimpleNamespace(**{
        name: SimpleNamespace(get_value=lambda value=value: value)
        for name, value in {
            'order_combo': 0, 'select_method_combo': 0, 'rest_cb': True,
            'margin_entry': 1, 'rest_margin_entry': 3, 'rest_connect_cb': False,
            'rest_contour_cb': True, 'rest_offset_choice_cb': False, 'rest_offset_entry': 0,
        }.items()
    })
    output = SimpleNamespace(kind='geometry', tools={}, obj_options={})

    def new_object(kind, name, worker, **kwargs):
        gen.worker_result = worker(output, app)
        return gen.worker_result

    gen.parent_tool = SimpleNamespace(new_object=new_object, ncc_tools=ncc_tools)
    return gen, output


def run_rest(area, tools, clear, tcl=False, source=None, rest=True, known=None, options=None, engines=None,
             real_area=False, boundary=None, handler_kwargs=None, **tcl_extra):
    """Run the GUI handler (or the Tcl command) with the area and the clearing replaced by the given ones."""
    gen, output = make_gen(tools, known, options)
    app = gen.app
    gen.ui.rest_cb = SimpleNamespace(get_value=lambda: rest)
    source = source or SimpleNamespace(kind='geometry', solid_geometry=[box(2, 2, 3, 3)])
    gen.compute_clipping_region = Mock(return_value=(area, 'geometry'))
    gen.apply_margin_to_clipping_region = Mock(return_value=area if boundary is None else boundary)
    if not real_area:
        gen.compute_area_to_clear = Mock(return_value=(area, 0))
    if engines is None:
        gen.clear_polygon_worker = Mock(side_effect=lambda **kw: clear(**dict(kw, tooldia=round(kw['tooldia'], 4))))
    else:
        for name, engine in engines.items():
            setattr(gen.parent_tool, 'clear_polygon_' + name, engine)
    if tcl:
        app.app_obj = gen.parent_tool
        gen._compute_area_to_clear_handler = Mock(return_value=area)
        kwargs = dict(
            ncc_tooldia=tools, iso_tooldia=[], margin=0, has_offset=True, offset=0.1,
            select_method=1, areas_to_clear_list=[box(0, 0, 10, 10)], overlap=0.2,
            connect=False, contour=True, order=0 if not rest else 2, method=0, rest=rest, run_threaded=False)
        kwargs.update(tcl_extra)
        gen.result = gen.clear_copper_tcl(source, **kwargs)
    else:
        gen.ncc_handler(source, tools, [], run_threaded=False, **(handler_kwargs or {}))
    return gen, output


def messages_of(gen):
    return [call.args[0] for call in gen.app.inform.emit.call_args_list]


def test_smaller_tool_is_tried_when_largest_cannot_fit():
    gen, output = run_rest(MultiPolygon([box(0, 0, 2, 2)]), [4.0, 1.0],
                           lambda **kw: [LineString([(0.5, 1), (1.5, 1)])])
    assert [call.kwargs['tooldia'] for call in gen.clear_polygon_worker.call_args_list] == [1.0]
    assert output.tools[2]['solid_geometry']


def test_rest_margin_and_exact_swept_area_are_used():
    area = MultiPolygon([box(0, 0, 10, 10)])
    line = LineString([(2, 5), (8, 5)])
    residuals = []

    def clear(**kw):
        if kw['tooldia'] == 2:
            return [line, line]
        residuals.append(kw['pol'])
        return [LineString([(1, 1), (2, 1)])]

    # the swept area follows the circle steps of the source kind (32 steps -> 8 quadrant segments)
    gen, output = run_rest(area, [1.0, 2.0], clear, options={'geometry_circle_steps': 32})
    assert gen.apply_margin_to_clipping_region.call_args.kwargs['margin'] == 3
    assert gen.compute_area_to_clear.call_args.kwargs['margin'] == 3
    assert residuals[0].symmetric_difference(area.difference(line.buffer(1, 8))).area < 1e-10
    assert list(output.tools) == [2, 1]


def test_area_failure_does_not_attempt_clearing():
    gen, output = run_rest('fail', [2.0, 1.0], lambda **kw: pytest.fail('unexpected clearing'))
    gen.clear_polygon_worker.assert_not_called()
    assert not output.tools
    assert gen.worker_result == 'fail'


def test_tcl_smaller_tool_receives_only_unswept_stock():
    area = MultiPolygon([box(0, 0, 10, 10)])
    line = LineString([(2, 5), (8, 5)])
    residuals = []

    def clear(**kw):
        if kw['tooldia'] == 2:
            return [line, line]
        residuals.append(kw['pol'])
        return [LineString([(1, 1), (2, 1)])]

    gen, output = run_rest(area, [1.0, 2.0], clear, tcl=True, options={'geometry_circle_steps': 32})
    assert residuals[0].symmetric_difference(area.difference(line.buffer((2 - 1e-12) / 2, 8))).area < 1e-10
    assert list(output.tools) == [2, 1]


def test_tcl_skipped_unknown_tool_does_not_change_next_tool_offset():
    area = MultiPolygon([box(0, 0, 10, 10)])
    seen = []

    def run(tools, known):
        seen.append([])

        def clear(**kw):
            seen[-1].append(kw['pol'])
            return [LineString([(1, 1), (2, 1)])]

        run_rest(area, tools, clear, tcl=True, rest=False, known=known)

    run([2.0, 1.0], [2.0, 1.0])
    run([2.0, 0.5, 1.0], [2.0, 1.0])
    assert len(seen[0]) == len(seen[1])
    assert seen[0][-1].symmetric_difference(seen[1][-1]).area < 1e-10


def test_tcl_empty_area_reports_why():
    gen, output = run_rest('fail', [2.0], lambda **kw: None, tcl=True)
    assert any(m.startswith('[ERROR_NOTCL]') for m in messages_of(gen))


def test_tool_without_geometry_is_reported():
    gen, output = run_rest(MultiPolygon([box(0, 0, 10, 10)]), [2.0], lambda **kw: None, tcl=True, rest=False)
    assert any(m.startswith('[WARNING_NOTCL]') and 'diameter' in m for m in messages_of(gen))


# ---------------------------------------------------------------------------------------------------------------
# what a run publishes and what it leaves in the tool table
# ---------------------------------------------------------------------------------------------------------------
def line_tool(dia, geometry=None):
    return {'tooldia': dia, 'data': dict(TOOL_DATA), 'solid_geometry': list(geometry or [])}


def test_normal_run_publishes_only_the_geometry_of_this_run():
    old = [LineString([(0, 0), (1, 0)])]
    tools = {1: line_tool(1.0, old), 2: line_tool(2.0, old), 3: line_tool(0.5, old)}
    area = MultiPolygon([box(0, 0, 10, 10)])
    new = [LineString([(1, 1), (9, 1)])]
    # tool 1 is selected and clears; tool 3 is selected and clears nothing; tool 2 is not selected
    generated, *_ = NccModel.generate_tools(
        'gui_normal', tools, tools, [1.0, 0.5], {1: area, 3: area}, 'n', 4, 32,
        lambda **kw: new if kw['tooldia'] == 1.0 else None, lambda: None, lambda text: None)
    assert list(generated) == [1]
    assert generated[1]['solid_geometry'] == new


def test_run_does_not_change_the_tool_table():
    gen, output = run_rest(MultiPolygon([box(0, 0, 10, 10)]), [2.0, 1.0],
                           lambda **kw: [LineString([(1, 1), (9, 1)])] if kw['tooldia'] == 2.0 else None, rest=False,
                           options={'tools_ncc_tipdia': 0.2, 'tools_ncc_tipangle': 60})
    assert list(output.tools) == [1]
    # the NCC preferences for Cut Z and the V-tip reach the output tool
    assert {key: output.tools[1]['data'][key] for key in
            ('tools_mill_cutz', 'tools_mill_vtipdia', 'tools_mill_vtipangle')} == {
        'tools_mill_cutz': -0.1, 'tools_mill_vtipdia': 0.2, 'tools_mill_vtipangle': 60}
    assert any('diameter' in m for m in messages_of(gen) if m.startswith('[WARNING_NOTCL]'))
    assert sorted(gen.ncc_tools) == [1, 2]
    assert all(not value['solid_geometry'] for value in gen.ncc_tools.values())


def test_empty_area_to_clear_is_reported_not_replaced_by_the_envelope():
    assert NccModel.complement(box(0, 0, 1, 1), Polygon()).is_empty
    assert NccModel.complement(box(0, 0, 1, 1), None).is_empty
    assert NccModel.complement(box(0, 0, 1, 1), box(-1, -1, 2, 2)).area == pytest.approx(8.0)
    gen, output = run_rest(MultiPolygon([box(0, 0, 10, 10)]), [2.0], lambda **kw: pytest.fail('cleared'),
                           real_area=True, boundary=Polygon(), rest=False)
    assert not output.tools
    assert any(m.startswith('[ERROR_NOTCL]') and 'area to clear is empty' in m for m in messages_of(gen))


def test_every_isolation_tool_counts_for_the_area():
    tools = {1: line_tool(0.2), 2: line_tool(0.4)}
    boundary = box(-5, -5, 5, 5)
    big, small = box(0, 0, 2, 2), box(0, 0, 1, 1)

    def run(envelopes):
        return NccModel.prepare_area(
            box(0, 0, 1, 1), boundary, [0.2, 0.4], tools, 'n', 4, False, 0.0, 1.0, 'cl',
            lambda radius, invert: envelopes[round(radius, 1)], lambda target, boundary=None:
            NccModel.complement(target, boundary), lambda: None)

    area = run({0.1: small, 0.2: big})[0]
    assert area.area == pytest.approx(100 - 4)
    # a failed or empty last envelope neither fails the job nor uncovers the copper
    assert run({0.1: big, 0.2: 'fail'})[0].area == pytest.approx(100 - 4)
    assert run({0.1: big, 0.2: Polygon()})[0].area == pytest.approx(100 - 4)
    assert run({0.1: 'fail', 0.2: 'fail'})[0] == 'fail'


def test_exclusion_is_removed_from_the_area():
    gen, _output = make_gen([1.0])
    cleared = SimpleNamespace(kind='geometry', solid_geometry=[box(0, 0, 1, 1)])
    area, iso, _broken = gen.compute_clear_area(
        cleared, clipping_type=1, clipping_areas=[box(-1, -1, 3, 3)], margin=0.0, exclusion=box(2, -1, 3, 3))
    assert area.area == pytest.approx(3 * 4 - 1)
    assert iso == {}


@pytest.mark.parametrize('exclusion', [box(1, 1, 3, 5)])
def test_exclusion_keeps_the_isolation_cutter_out(exclusion):
    """The exclusion zones protect the isolation paths too, measured with the cutter radius, not only the clearing."""
    tools = {1: line_tool(0.2)}
    copper = box(2, 2, 4, 4)
    _area, iso, _broken, _warnings = NccModel.prepare_area(
        copper, box(0, 0, 10, 10), [0.2], tools, 'n', 4, False, 0.0, 1.0, 'cl',
        lambda radius, invert: copper.buffer(radius, 8),
        lambda target, boundary=None: NccModel.complement(target, boundary), lambda: None, exclusion)
    paths = iso[1]['solid_geometry']
    assert paths
    sweep = unary_union([path.buffer(0.1, 32) for path in paths]) if paths else Polygon()
    assert sweep.intersection(exclusion).area == pytest.approx(0, abs=1e-9)


# ---------------------------------------------------------------------------------------------------------------
# clearing engines
# ---------------------------------------------------------------------------------------------------------------
def engine_result(paths):
    return SimpleNamespace(objects=list(paths), get_objects=lambda: list(paths))


def test_combo_tries_every_engine_even_if_one_raises():
    pol = box(0, 0, 10, 10)
    path = LineString([(1, 5), (9, 5)])

    def lines(*args, **kwargs):
        raise ValueError('boom')

    paths, messages = NccModel.clear_polygon(
        pol, 1.0, 3, 0.2, False, True, False, 32, lambda *a, **k: None,
        lambda *a, **k: engine_result([path]), lines)
    assert paths == [path]
    assert len(messages) == 2


def test_tcl_combo_method_and_simplification_are_used():
    wiggle = LineString([(1, 5), (3, 5.01), (5, 5), (7, 5.01), (9, 5)])

    def lines(*args, **kwargs):
        return None

    gen, output = run_rest(
        MultiPolygon([box(0, 0, 10, 10)]), [1.0], None, tcl=True, rest=False,
        engines={'lines': lines, 'seed': lambda *a, **k: engine_result([wiggle]), 'shrink': lambda *a, **k: None},
        method=3, simplification=0.5)
    assert len(output.tools[1]['solid_geometry'][0].coords) == 2


def test_simplification_option_is_in_the_units_of_the_geometry():
    gen, _output = run_rest(MultiPolygon([box(0, 0, 10, 10)]), [1.0], lambda **kw: [LineString([(1, 1), (9, 1)])],
                            options={'tools_ncc_simplification': 0.5}, rest=False)
    assert gen.clear_polygon_worker.call_args.kwargs['simplify_tol'] == pytest.approx(0.5)
    inch, _output = make_gen([1.0], options={'tools_ncc_simplification': 0.5}, units='IN')
    assert inch._simplification() == pytest.approx(0.5 / 25.4)

# ---------------------------------------------------------------------------------------------------------------
# rest machining
# ---------------------------------------------------------------------------------------------------------------
def rest_tools(*dias):
    return {uid: line_tool(dia) for uid, dia in enumerate(dias, 1)}


def test_rest_reports_failure_only_when_copper_free_area_remains():
    disc = box(0, 0, 1, 1).centroid.buffer(0.5, 8)
    tools = rest_tools(1.0)

    def run(area, result):
        return NccModel.generate_tools(
            'gui_rest', tools, tools, [1.0], {1: MultiPolygon([area])}, 'n', 4, 32,
            lambda **kw: result, lambda: None, lambda text: None)

    assert run(disc, [LineString([(0.5, 0.5), (0.5, 0.501)])])[3] is False
    assert run(box(0, 0, 2, 2), [LineString([(1, 1), (1, 1.001)])])[3] is True
    assert run(disc, None)[3] is True


def test_tcl_rest_does_not_send_slivers_to_the_clearing():
    area = MultiPolygon([box(0, 0, 10, 10), box(20, 0, 20.1, 5)])
    seen = []

    def clear(**kw):
        seen.append(kw['pol'].bounds)
        return [LineString([(2, 5), (8, 5)])]

    run_rest(area, [2.0], clear, tcl=True)
    assert seen == [(0, 0, 10, 10)]


# ---------------------------------------------------------------------------------------------------------------
# Tcl inputs
# ---------------------------------------------------------------------------------------------------------------
def test_tcl_matches_tool_diameters_by_rounding():
    gen, output = run_rest(MultiPolygon([box(0, 0, 10, 10)]), [0.1 + 0.2], lambda **kw: [LineString([(1, 1), (9, 1)])],
                           tcl=True, rest=False, known=[0.3])
    assert list(output.tools) == [1]


def test_tcl_itself_uses_the_object_and_a_gerber_reference_uses_both_hulls():
    area = MultiPolygon([box(0, 0, 10, 10)])
    source = SimpleNamespace(kind='geometry', solid_geometry=[box(0, 0, 4, 4)])

    def boundary_of(**extra):
        gen, _output = run_rest(area, [1.0], lambda **kw: [LineString([(1, 1), (2, 1)])], tcl=True, rest=False,
                                source=source, **extra)
        return gen._compute_area_to_clear_handler.call_args.kwargs['boundary'].bounds

    other = SimpleNamespace(kind='geometry', solid_geometry=[box(50, 50, 60, 60)])
    assert boundary_of(select_method=0, sel_obj=other) == (0, 0, 4, 4)
    reference = SimpleNamespace(kind='gerber', solid_geometry=[box(2, 2, 8, 8)])
    assert boundary_of(select_method=2, sel_obj=reference) == (2, 2, 4, 4)


def test_tcl_missing_values_come_from_the_options():
    gen, output = run_rest(MultiPolygon([box(0, 0, 10, 10)]), [1.0], lambda **kw: [LineString([(1, 1), (9, 1)])],
                           tcl=True, rest=False, margin=None, has_offset=None, offset=None, overlap=None,
                           connect=None, contour=None, method=None, order=None,
                           options={'tools_ncc_overlap': 30})
    assert list(output.tools) == [1]
    assert gen.clear_polygon_worker.call_args.kwargs['ncc_overlap'] == pytest.approx(0.3)


def test_tcl_final_cleanup_reaches_the_tools_and_exclusion_the_area():
    area = MultiPolygon([box(0, 0, 10, 10)])
    gen2, output2 = run_rest(area, [1.0], lambda **kw: [LineString([(1, 1), (9, 1)])], tcl=True, rest=False,
                             final_cleanup=True, exclusion_areas=[box(0, 0, 5, 10)], real_area=False)
    # the area handed to the clearing is the one without the excluded part
    assert gen2._compute_area_to_clear_handler.called
    final_data = gen2.parent_tool.ncc_tools[1]['data']
    assert 'tools_ncc_final_cleanup' not in final_data      # the table itself is not written
    assert output2.tools[1]['data']['tools_ncc_final_cleanup'] is True
    pol = gen2.clear_polygon_worker.call_args.kwargs['pol']
    assert pol.bounds[0] >= 5 - 1e-9


# ---------------------------------------------------------------------------------------------------------------
# Check validity
# ---------------------------------------------------------------------------------------------------------------
def gerber_with_gap(gap):
    apertures = {10: {'geometry': [{'solid': box(0, 0, 1, 1)}, {'solid': box(1 + gap, 0, 2 + gap, 1)}]}}
    return SimpleNamespace(kind='gerber', tools=apertures, solid_geometry=[])


def test_check_validity_names_the_tools_that_are_too_big():
    source = gerber_with_gap(0.3)
    assert NccModel.check_tools_fit(source.tools, [0.2], [], 4)[:3] == ('ok', 0.2999, [])
    level, gap, too_big, _note = NccModel.check_tools_fit(source.tools, [0.2, 0.5], [], 4)
    assert (level, too_big) == ('warning', [0.5])
    assert NccModel.check_tools_fit(source.tools, [0.2], [0.5], 4)[::2] == ('error', [0.5])
    assert NccModel.check_tools_fit(source.tools, [0.5, 0.6], [], 4)[::2] == ('error', [0.5, 0.6])


def test_check_validity_with_a_multi_part_solid():
    # a shape split by a negative Etch Compensation is 1 MultiPolygon solid: MultiPolygon(list) raised ValueError
    solids = [box(0, 0, 1, 1), MultiPolygon([box(0, 2, 1, 3), box(1.15, 2, 2, 3)])]
    apertures = {10: {'geometry': [{'solid': solid} for solid in solids]}}
    assert NccModel.smallest_gerber_gap(apertures, 4) == (None, pytest.approx(0.1499))
    assert NccModel.check_tools_fit(apertures, [0.1], [0.2], 4)[::2] == ('error', [0.2])


def test_check_validity_stops_the_job_before_the_generation():
    gen, output = run_rest(MultiPolygon([box(0, 0, 10, 10)]), [0.5], lambda **kw: pytest.fail('cleared'),
                           source=gerber_with_gap(0.3), rest=False, handler_kwargs={'check_validity': True})
    assert not output.tools and gen.worker_result == 'fail'
    gen.compute_area_to_clear.assert_not_called()
    assert any(m.startswith('[ERROR_NOTCL]') and '0.5' in m for m in messages_of(gen))


# ---------------------------------------------------------------------------------------------------------------
# GUI thread and controller state
# ---------------------------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------------------------
# Controller and panel
# ---------------------------------------------------------------------------------------------------------------
class Collection(QStringListModel):
    def __init__(self):
        super().__init__()
        self.objects = {}
        self.selected = []

    def get_selected(self):
        return self.selected

    def get_active(self):
        return None

    def get_list(self):
        return list(self.objects.values())

    def get_names(self):
        return list(self.objects)

    def get_by_name(self, name):
        return self.objects.get(name)


@pytest.fixture(scope="session")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tool(qapp, tmp_path):
    app = make_tool_app()
    app.collection = Collection()
    app.options = deepcopy(AppDefaults.factory_defaults)
    app.defaults = AppDefaults.factory_defaults
    app.dec_format = functools.partial(App.dec_format, app)
    app.tools_database_path.return_value = str(tmp_path / "missing_tools_db.json")
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    app.options["tools_ncc_tools"] = "2.0, 1.0"
    t = ToolNcc(app)
    t.decimals = 4
    t.set_tool_ui()
    yield t
    # Qt deletes the panel now: a garbage collection of it in a later test can crash the process
    t.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def messages(t):
    return [c.args[0] for c in t.app.inform.emit.call_args_list]


def dias_in_table_order(t):
    return [float(t.ui.tools_table.item(row, 1).text()) for row in range(t.ui.tools_table.rowCount())]


def test_drag_drop_reorder_holds_and_keeps_the_tool_dict(tool):
    storage = tool.ncc_tools
    tool.ui.order_combo.set_value(2)   # Reverse sorts by diameter
    tool.build_ui()
    assert dias_in_table_order(tool) == [2.0, 1.0]
    # the user drags the second tool up: the uids in the table are swapped
    tool.ui_disconnect()
    tool.ui.tools_table.item(0, 3).setText("2")
    tool.ui.tools_table.item(1, 3).setText("1")

    tool.rebuild_ui()
    tool.build_ui()

    assert tool.ncc_tools is storage
    assert dias_in_table_order(tool) == [1.0, 2.0]
    assert tool.ui.order_combo.get_value() == 0


def test_edit_to_a_duplicate_or_nonpositive_diameter_is_rejected(tool):
    tool.on_tool_add(custom_dia=0.5)
    assert sorted(dias_in_table_order(tool)) == [0.5, 1.0, 2.0]
    for text in ("1.00004", "2.0", "0", "-1"):
        row = dias_in_table_order(tool).index(0.5)
        tool.ui.tools_table.item(row, 1).setText(text)
        assert sorted(t['tooldia'] for t in tool.ncc_tools.values()) == [0.5, 1.0, 2.0], text
        assert sorted(dias_in_table_order(tool)) == [0.5, 1.0, 2.0], text


def test_tool_from_database_is_merged_with_defaults_and_selected(tool):
    db_tool = {'tooldia': 0.3, 'data': {'tool_target': 5, 'tools_ncc_overlap': 7.0}}
    uid = tool.on_ncc_tool_from_db_inserted(tool=db_tool)
    assert uid == max(tool.ncc_tools)
    data = tool.ncc_tools[uid]['data']
    assert data['tools_ncc_overlap'] == 7.0
    assert 'tools_mill_tool_shape' in data and 'tools_ncc_operation' in data
    selected = {int(tool.ui.tools_table.item(i.row(), 3).text()) for i in tool.ui.tools_table.selectedIndexes()}
    assert selected == {uid}


def test_beginner_mode_turns_rest_machining_off(tool):
    tool.ui.rest_cb.set_value(True)
    tool.change_level('b')
    assert tool.ui.rest_cb.get_value() is False
    assert all(not v['data']['tools_ncc_rest'] for v in tool.ncc_tools.values())


def prepare_run(t, **objects):
    """Let Generate run: the objects are in the project, every tool is selected, the app has real defaults."""
    t.app.defaults = SimpleNamespace(get=AppDefaults.factory_defaults.get, report_usage=lambda *args: None,
                                     factory_defaults=AppDefaults.factory_defaults)
    t.app.collection.objects.update(objects)
    t.ui.obj_combo.currentText = lambda: 'board'
    t.ui.reference_combo.currentText = lambda: 'board'
    t.ui.tools_table.selectAll()
    t.app.plotcanvas.translate_coords.side_effect = lambda pos: pos
    t.app.grid_status.return_value = False
    t.app.plotcanvas.new_shape_collection.side_effect = lambda **kwargs: MagicMock()


def geometry_object(name, *boxes):
    return SimpleNamespace(kind='geometry', solid_geometry=list(boxes), obj_options={'name': name})


def cleared_domain(source, margin=1.0):
    """The area Preview draws for one geometry source: its margin boundary minus its own copper."""
    boundary = NccModel.apply_margin(NccModel.entire_object(source.solid_geometry), 'geometry', 0, margin)
    return NccModel.complement(unary_union(source.solid_geometry), boundary)


def test_exclusion_zones_reach_the_handler_in_every_mode(tool):
    prepare_run(tool, board=geometry_object('board', box(0, 0, 2, 2)))
    zone = box(5, 5, 6, 6)
    tool.exclusion_list.append(zone)
    tool.ui.excl_cb.set_value(True)
    tool.gen.ncc_handler = Mock()
    for method in (0, 2):
        tool.ui.select_method_combo.set_value(method)
        tool.gen.on_ncc_click()
        assert tool.gen.ncc_handler.call_args.kwargs['exclusion_areas'] == [zone], method
    tool.gen.clear_ncc_area_selection_option([box(0, 0, 3, 3)])
    assert tool.gen.ncc_handler.call_args.kwargs['exclusion_areas'] == [zone]
    tool.ui.excl_cb.set_value(False)
    tool.gen.on_ncc_click()
    assert tool.gen.ncc_handler.call_args.kwargs['exclusion_areas'] is None


def test_all_selected_objects_each_get_an_output_with_a_unique_name(tool):
    first = geometry_object('a', box(0, 0, 1, 1))
    second = geometry_object('b', box(0, 0, 1, 1))
    excellon = SimpleNamespace(kind='excellon', obj_options={'name': 'e'})
    prepare_run(tool, a=first, b=second, a_ncc=geometry_object('a_ncc'), board=first)
    tool.gen.ncc_handler = Mock()
    tool.ui.multi_cb.set_value(True)
    tool.app.collection.selected = [first, excellon, second]
    tool.gen.on_ncc_click()
    calls = tool.gen.ncc_handler.call_args_list
    assert [c.kwargs['cleared_object'] for c in calls] == [first, second]
    assert [c.kwargs['outname'] for c in calls] == ['a_ncc_1', 'b_ncc']
    assert calls[0].kwargs['clear_tooldia_list'] == calls[1].kwargs['clear_tooldia_list']

    # Area Selection works with one object only
    tool.gen.ncc_handler.reset_mock()
    tool.ui.select_method_combo.set_value(1)
    tool.gen.on_ncc_click()
    tool.gen.ncc_handler.assert_not_called()
    assert messages(tool)[-1].startswith('[ERROR_NOTCL]')

    # nothing valid selected: the object of the combo box is used
    tool.ui.select_method_combo.set_value(0)
    tool.app.collection.selected = [excellon]
    tool.gen.on_ncc_click()
    assert tool.gen.ncc_handler.call_args.kwargs['cleared_object'] is first


def test_preview_uses_the_selected_sources_and_rejects_several_area_sources(tool):
    board = geometry_object('board', box(50, 50, 51, 51))
    first = geometry_object('a', box(0, 0, 1, 1))
    second = geometry_object('b', box(5, 5, 6, 6))
    prepare_run(tool, board=board, a=first, b=second)
    tool.ui.rest_cb.set_value(False)
    tool.ui.margin_entry.set_value(1)
    tool.ui.multi_cb.set_value(True)
    tool.app.collection.selected = [first, second]

    tool.on_preview()
    drawn = unary_union([call.args[0] for call in tool._preview_shapes.add.call_args_list])
    assert drawn.bounds == pytest.approx((-1, -1, 7, 7))
    expected = unary_union([cleared_domain(source) for source in (first, second)])
    assert drawn.symmetric_difference(expected).area < 1e-9

    # Area Selection works with one source only, like Generate
    tool.ui.select_method_combo.set_value(1)
    tool.last_zones = [box(0, 0, 1, 1)]
    tool._preview_shapes.reset_mock()
    tool.on_preview()
    tool._preview_shapes.add.assert_not_called()
    assert messages(tool)[-1].startswith('[ERROR_NOTCL]')

    # with one source the object of the combo box is still the source
    tool.ui.multi_cb.set_value(False)
    tool.ui.select_method_combo.set_value(0)
    tool._preview_shapes.reset_mock()
    tool.on_preview()
    drawn = unary_union([call.args[0] for call in tool._preview_shapes.add.call_args_list])
    assert drawn.bounds == pytest.approx((49, 49, 52, 52))


def run_optimal(t, storage):
    t.app.collection.objects['board'] = SimpleNamespace(kind='gerber', tools=storage)
    t.ui.obj_combo.currentText = lambda: 'board'
    t.app.abort_flag = False
    t.app.inform.emit.reset_mock()
    t.on_find_optimal_tooldia()


def test_optimal_proposes_a_tool_below_the_smallest_gap(tool):
    pads = [box(0, 0, 1, 1), box(1.15996, 0, 2, 1)]   # the gap 0.15996 is rounded to 0.16
    found = []
    tool.optimal_found_sig.connect(found.append)
    run_optimal(tool, {10: {'geometry': [{'solid': p} for p in pads]}})
    assert found == [pytest.approx(0.1599)]
    assert any(m.startswith('[success]') and '0.1599' in m for m in messages(tool))
