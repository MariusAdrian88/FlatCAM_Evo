"""Exercise the NCC plugin without starting the application or plotting: the workers (GUI, Tcl and the geometry
kernels), then the controller and panel (tool table, area selection, exclusion zones, preview, several objects, Optimal
button) built headless with offscreen Qt and a MagicMock app."""

import functools
import math
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
import camlib  # noqa: E402
from camlib import flatten_shapely_geometry, rest_area  # noqa: E402
from appPlugins.Common.clearing import regions  # noqa: E402
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
        log=Mock(), inform=Mock(), inform_shell=Mock(), proc_container=Mock(), abort_flag=False, collection=Mock(),
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


@pytest.mark.parametrize('tcl', [False, True])
def test_job_without_geometry_removes_the_promise_and_reports_the_failure_once(tcl):
    gen, output = run_rest(MultiPolygon([box(0, 0, 4, 4)]), [1.0], lambda **kw: None, rest=False, tcl=tcl)
    assert gen.app.collection.remove_promise.call_count == 1
    assert [text for text in messages_of(gen) if text.startswith('[ERROR')] != []
    assert len([text for text in messages_of(gen) if text.startswith('[ERROR')]) == 1
    assert not any(value['solid_geometry'] for value in output.tools.values())


def test_published_output_has_bounds_and_an_incomplete_job_warns():
    from appPlugins.Common.clearing.job import incomplete_text
    area = MultiPolygon([box(0, 0, 4, 4)])
    gen, output = run_rest(area, [1.0], lambda **kw: [LineString([(1, 1), (3, 1)])], rest=True)
    assert (output.obj_options['xmin'], output.obj_options['xmax']) == (1, 3)
    assert gen.app.poly_not_cleared is True
    assert '[WARNING_NOTCL] %s' % incomplete_text() in messages_of(gen)


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
    # The smaller tool clears the unswept stock grown by its diameter, inside the area.
    expected = MultiPolygon(rest_area(area, area.difference(line.buffer(1, 8)), 1.0, 8))
    assert residuals[0].symmetric_difference(expected).area < 1e-10
    assert list(output.tools) == [2, 1]


def test_rest_tools_follow_their_own_connect_and_contour_not_the_panel():
    """The rest widgets say connect off / contour on; each row has its own values and the output follows the rows."""
    area = MultiPolygon([box(0, 0, 10, 10)])
    gen, output = make_gen([2.0, 1.0])
    rows = gen.parent_tool.ncc_tools
    rows[1]['data'].update(tools_ncc_connect=True, tools_ncc_contour=False)
    rows[2]['data'].update(tools_ncc_connect=False, tools_ncc_contour=True)
    seen = {}

    def clear(**kw):
        seen[round(kw['tooldia'], 4)] = (kw['ncc_connect'], kw['ncc_contour'])
        return [LineString([(1, 1), (9, 1)])]

    gen.compute_clipping_region = Mock(return_value=(area, 'geometry'))
    gen.apply_margin_to_clipping_region = Mock(return_value=area)
    gen.compute_area_to_clear = Mock(return_value=(area, 0))
    gen.clear_polygon_worker = clear
    gen.ncc_handler(SimpleNamespace(kind='geometry', solid_geometry=[box(2, 2, 3, 3)]), [2.0, 1.0], [],
                    run_threaded=False)
    assert seen == {2.0: (True, False), 1.0: (False, True)}


def test_rest_connect_and_contour_widgets_apply_to_all_rows(qapp, tmp_path):
    from test_clearing_golden import Plugin
    plugin = Plugin(qapp, tmp_path, 'ncc', SimpleNamespace(kind='gerber'), [0.5, 0.25],
                    tool_data={0.5: dict(connect=False, contour=True), 0.25: dict(connect=True, contour=False)})
    try:
        plugin.tool.connect_signals()
        ui = plugin.tool.ui
        ui.rest_connect_cb.set_value(True)      # a programmatic change does not touch the rows
        ui.rest_contour_cb.set_value(False)
        assert [r['data']['tools_ncc_connect'] for r in plugin.tool.ncc_tools.values()] == [False, True]
        ui.rest_connect_cb.click()              # a user click: on -> off
        ui.rest_contour_cb.click()              # off -> on
        rows = plugin.tool.ncc_tools.values()
        assert [(r['data']['tools_ncc_connect'], r['data']['tools_ncc_contour']) for r in rows] == [(False, True)] * 2
    finally:
        plugin.close()


@pytest.mark.parametrize("order, preference, expected", [
    ("fwd", 2, [1, 2]), (1, 2, [1, 2]),
])
def test_tcl_order_accepts_names_numbers_and_the_preference(order, preference, expected):
    area = MultiPolygon([box(0, 0, 10, 10)])
    gen, output = run_rest(
        area, [1.0, 2.0], lambda **kw: [LineString([(1, 1), (2, 1)])], tcl=True, rest=False,
        options={'tools_ncc_order': preference}, order=order)
    assert list(output.tools) == expected


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
        'normal', tools, tools, [1.0, 0.5], {1: area, 3: area}, 'n', 4, 32,
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
    assert regions.complement(box(0, 0, 1, 1), Polygon()).is_empty
    assert regions.complement(box(0, 0, 1, 1), None).is_empty
    assert regions.complement(box(0, 0, 1, 1), box(-1, -1, 2, 2)).area == pytest.approx(8.0)
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
            regions.complement(target, boundary), lambda: None)

    area = run({0.1: small, 0.2: big})[0]
    assert area.area == pytest.approx(100 - 4)
    # a failed or empty last envelope neither fails the job nor uncovers the copper
    assert run({0.1: big, 0.2: 'fail'})[0].area == pytest.approx(100 - 4)
    assert run({0.1: big, 0.2: Polygon()})[0].area == pytest.approx(100 - 4)
    assert run({0.1: 'fail', 0.2: 'fail'})[0] == 'fail'


def test_exclusion_keeps_the_isolation_cutter_out():
    """The exclusion zones protect the isolation paths too, measured with the cutter radius, not only the clearing."""
    tools = {1: line_tool(0.2)}
    copper = box(2, 2, 4, 4)
    exclusion = box(1, 1, 3, 5)
    _area, iso, _broken, _warnings = NccModel.prepare_area(
        copper, box(0, 0, 10, 10), [0.2], tools, 'n', 4, False, 0.0, 1.0, 'cl',
        lambda radius, invert: copper.buffer(radius, 8),
        lambda target, boundary=None: regions.complement(target, boundary), lambda: None, exclusion)
    paths = iso[1]['solid_geometry']
    assert paths
    sweep = unary_union([path.buffer(0.1, 32) for path in paths])
    assert sweep.intersection(exclusion).area == pytest.approx(0, abs=1e-9)


# ---------------------------------------------------------------------------------------------------------------
# clearing engines
# ---------------------------------------------------------------------------------------------------------------
def engine_result(paths):
    return SimpleNamespace(objects=list(paths), get_objects=lambda: list(paths))


def test_tcl_combo_method_and_simplification_are_used():
    wiggle = LineString([(1, 5), (3, 5.01), (5, 5), (7, 5.01), (9, 5)])

    def lines(*args, **kwargs):
        return None

    gen, output = run_rest(
        MultiPolygon([box(0, 0, 10, 10)]), [1.0], None, tcl=True, rest=False,
        engines={'lines': lines, 'seed': lambda *a, **k: engine_result([wiggle]), 'shrink': lambda *a, **k: None},
        method=3, simplification=0.5)
    assert len(output.tools[1]['solid_geometry'][0].coords) == 2


# ---------------------------------------------------------------------------------------------------------------
# crosshatch method
# ---------------------------------------------------------------------------------------------------------------
def crosshatch_table(angle_a=30.0, angle_b=75.0):
    data_a = dict(TOOL_DATA, tools_ncc_method=4, tools_ncc_crosshatch_angle=angle_a,
                  tools_ncc_final_cleanup=True)
    data_b = dict(TOOL_DATA, tools_ncc_method=4, tools_ncc_crosshatch_angle=angle_b,
                  tools_ncc_final_cleanup=True)
    return {1: {'tooldia': 2.0, 'data': data_a, 'solid_geometry': []},
            2: {'tooldia': 1.0, 'data': data_b, 'solid_geometry': []}}


def test_invalid_unused_crosshatch_angle_does_not_break_other_methods():
    tools = crosshatch_table()
    for tool in tools.values():
        tool['data']['tools_ncc_method'] = 0
        tool['data']['tools_ncc_crosshatch_angle'] = 'invalid'
    areas = {1: MultiPolygon([box(0, 0, 10, 10)]), 2: MultiPolygon([box(0, 0, 10, 10)])}
    calls = []

    def clear(**kw):
        calls.append(kw['ncc_method'])
        assert 'angle' not in kw
        return [LineString([(0, 1), (1, 1)])]

    NccModel.generate_tools('normal', tools, NccModel.copy_tools(tools), [2.0, 1.0], areas, 'n', 4, 32,
                            clear, lambda: None, lambda text: None)
    assert calls == [0, 0]


def directions(paths):
    """The directions of the path segments, in degrees in [0, 180)."""
    angles = set()
    for path in paths:
        coords = list(path.coords)
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            if math.hypot(x1 - x0, y1 - y0) > 1e-6:
                angles.add(round(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0, 6))
    return angles


def real_crosshatch_clear():
    """A clear_paths callback that clears with the real Crosshatch kernel and the angle it receives."""
    from test_clearing_paths import make_geometry
    geometry = make_geometry()

    def clear(pol, tooldia, ncc_method, ncc_overlap, ncc_connect, ncc_contour, angle=0.0, **kw):
        assert ncc_method == 4
        paths, __ = NccModel.clear_polygon(
            pol, tooldia, 4, ncc_overlap, ncc_connect, ncc_contour, False, 64,
            None, None, None, 0.0, crosshatch=geometry.clear_polygon_crosshatch, angle=angle)
        return paths
    return clear


@pytest.mark.parametrize('mode', ['normal', 'rest'])
def test_each_tool_clears_at_its_own_crosshatch_angle(mode):
    tools = crosshatch_table(30.0, 75.0)
    area = MultiPolygon([box(0, 0, 10, 10)])
    generated, domains, *_ = NccModel.generate_tools(
        mode, tools, NccModel.copy_tools(tools), [2.0, 1.0], {1: area, 2: area}, 'n', 4, 32,
        real_crosshatch_clear(), lambda: None, lambda text: None)
    assert {30.0, 120.0} <= directions(generated[1]['solid_geometry'])
    assert not {75.0, 165.0} & directions(generated[1]['solid_geometry'])
    if 2 in generated:
        assert not {30.0, 120.0} & directions(generated[2]['solid_geometry'])
    # the final cleanup uses the angle of its own (smallest) tool
    cleaned, __, ___ = NccModel.finish_cleanup(
        tools, generated, {1: area, 2: area}, 'n',
        lambda domain, dia, used, generate, boundary_first: (generate(domain, dia), None),
        real_crosshatch_clear(), lambda: None)
    cleanup = cleaned[2]['solid_geometry'][cleaned[2]['ncc_cleanup_start']:]
    assert {75.0, 165.0} <= directions(cleanup)


@pytest.mark.parametrize('edge', ['touch', 'strict'])
def test_rotated_crosshatch_simplification_keeps_the_cutter_out_of_curved_copper(edge):
    from shapely.geometry import Point
    from camlib import clearing_simplification
    from test_clearing_paths import make_geometry
    geometry = make_geometry()
    geometry.app.app_units = 'MM'
    geometry.app.options = {'tools_clearing_edge': edge, 'tools_clearing_error': 2.0}
    area = box(0, 0, 12, 10).difference(Point(6, 5).buffer(2, quad_segs=64))
    paths, _ = NccModel.clear_polygon(
        area, 1.0, 4, 0.2, False, True, False, 64, None, None, None,
        clearing_simplification(geometry.app, 0.1), geometry.clear_polygon_crosshatch, 37.5)
    assert paths
    allowance = 0.002 if edge == 'touch' else 0.0
    assert min(area.boundary.distance(path) for path in paths) >= 0.5 - allowance - 1e-9


def test_crosshatch_cleanup_uses_the_same_invalid_angle_fallback_as_clearing():
    from test_clearing_paths import make_geometry
    geometry = make_geometry()
    tools = {1: crosshatch_table(angle_a='invalid')[1]}
    area = box(0, 0, 8, 6)

    def clear(**kw):
        return NccModel.clear_polygon(
            kw['pol'], kw['tooldia'], kw['ncc_method'], kw['ncc_overlap'], kw['ncc_connect'],
            kw['ncc_contour'], False, 64, None, None, None,
            crosshatch=geometry.clear_polygon_crosshatch, angle=kw['angle'])[0]

    generated, domains, *_ = NccModel.generate_tools(
        'normal', tools, tools, [2.0], {1: area}, 'n', 4, 64,
        clear, lambda: None, lambda text: None)
    cleaned, _, __ = NccModel.finish_cleanup(
        tools, generated, domains, 'n',
        lambda domain, dia, previous, generate, boundary_first: (generate(domain, dia), None),
        clear, lambda: None)
    assert cleaned[1]['solid_geometry']
    assert unary_union(cleaned[1]['solid_geometry']).equals(unary_union(generated[1]['solid_geometry']))


def test_tcl_legacy_positional_arguments_still_generate_geometry():
    gen, output = make_gen([1.0])
    gen.app.app_obj = gen.parent_tool
    gen._compute_area_to_clear_handler = lambda **kw: MultiPolygon([box(0, 0, 8, 6)])
    gen.clear_polygon_worker = lambda **kw: [LineString([(1, 1), (7, 1)])]
    source = SimpleNamespace(kind='geometry', solid_geometry=[box(2, 2, 3, 3)])
    result = gen.clear_copper_tcl(
        source, None, [box(0, 0, 8, 6)], [1.0], [], 0, False, 0, 1, 'legacy',
        0.2, False, True, 0, 0, False, gen.ncc_tools, False, False, False)
    assert result != 'fail'
    assert output.tools[1]['solid_geometry'] == [LineString([(1, 1), (7, 1)])]


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
            'rest', tools, tools, [1.0], {1: MultiPolygon([area])}, 'n', 4, 32,
            lambda **kw: result, lambda: None, lambda text: None)

    assert run(disc, [LineString([(0.5, 0.5), (0.5, 0.501)])])[3] is False
    assert run(box(0, 0, 2, 2), [LineString([(1, 1), (1, 1.001)])])[3] is True
    assert run(disc, None)[3] is True


def square_paths(dia, size=4.0):
    """A ring and parallel lines at half the diameter: they sweep the square up to the corner residue."""
    r = dia / 2
    paths = [box(r, r, size - r, size - r).exterior]
    y = r
    while y < size - r + 1e-9:
        paths.append(LineString([(r, y), (size - r, y)]))
        y += r
    return paths


SQUARE = box(0, 0, 4, 4)
BRANCH = box(4, 1.75, 6, 2.25)      # 0.5 wide: a 1.0 tool cannot enter it


@pytest.mark.parametrize('mode', ['normal', 'rest'])
def test_incomplete_flag_follows_the_uncut_stock_and_not_the_corner_residue(mode):
    tools = rest_tools(1.0)

    def failed(area):
        return NccModel.generate_tools(
            mode, tools, tools, [1.0], {1: MultiPolygon(flatten_shapely_geometry(area))}, 'n', 4, 32,
            lambda **kw: square_paths(1.0), lambda: None, lambda text: None)[3]

    # a fully reached square: only the residue of the 4 corners is left
    assert failed(SQUARE) is False
    # a branch narrower than the tool stays uncut
    assert failed(unary_union([SQUARE, BRANCH])) is True


def test_normal_run_measures_the_stock_of_all_tools_and_not_only_of_the_last():
    # ascending order: the small tool clears the narrow branch, the large tool runs last and cannot enter it
    tools = rest_tools(0.4, 1.0)
    area = MultiPolygon(flatten_shapely_geometry(unary_union([SQUARE, BRANCH])))
    small = square_paths(0.4) + [LineString([(3.8, y), (5.8, y)]) for y in (1.95, 2.05)]
    result = NccModel.generate_tools(
        'normal', tools, tools, [0.4, 1.0], {1: area, 2: area}, 'n', 4, 32,
        lambda **kw: small if kw['tooldia'] == 0.4 else square_paths(1.0), lambda: None, lambda text: None)
    assert result[3] is False


def test_rest_leaves_stock_for_the_final_cleanup_of_the_smallest_tool():
    tools = rest_tools(2.0, 1.0)
    for tool in tools.values():
        tool['data']['tools_ncc_final_cleanup'] = True
    area = MultiPolygon([box(0, 0, 10, 10)])
    # each tool clears a single line: the swept stock leaves material, so the cleanup has work
    lines = {2.0: [LineString([(2, 2), (8, 2)])], 1.0: [LineString([(1, 8), (9, 8)])]}
    generated, domains, residual, failed, last = NccModel.generate_tools(
        'rest', tools, tools, [2.0, 1.0], {1: area, 2: area}, 'n', 4, 32,
        lambda **kw: lines[round(kw['tooldia'], 4)], lambda: None, lambda text: None)
    assert failed and not residual.is_empty and sorted(domains) == [1, 2]
    seen = []

    def cleanup(domain, dia, previous, generate, boundary_first):
        seen.append((round(domain.area, 6), dia, boundary_first, [tool['tooldia'] for tool in previous]))
        return [LineString([(5, 5), (6, 5)])], residual

    cleaned, left, ran = NccModel.finish_cleanup(tools, generated, domains, 'out', cleanup, lambda **kw: None,
                                                 lambda: None)
    assert left is residual and ran == 1.0
    assert seen == [(100.0, 1.0, True, [2.0, 1.0])]
    assert list(cleaned) == [1, 2]
    assert cleaned[2]['ncc_cleanup_start'] == 1
    assert cleaned[2]['solid_geometry'] == lines[1.0] + [LineString([(5, 5), (6, 5)])]
    assert cleaned[2]['data']['output_object_name'] == 'out'
    assert 'ncc_cleanup_start' not in cleaned[1]

    # the message of the cleanup: the corner residue of a round tool is silent, a strip that holds a disc warns
    gen, output = make_gen([1.0])
    published = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': [LineString([(0, 0), (1, 0)])]}}
    for stock, warns in ((Polygon([(0, 0), (0.05, 0), (0, 0.05)]), False), (box(0, 0, 5, 0.5), True)):
        gen.app.inform.emit.reset_mock()
        assert gen._publish_result(output, published, 'n', 1.0, False, stock, 0, None, 1.0, 1.0) != 'fail'
        assert any('Final cleanup finished' in text for text in messages_of(gen)) is warns


@pytest.mark.parametrize('connect, copper, expected', [
    (False, None, (1.0, True)),                                # Paint rule: boundary first unless the row connects
    (True, None, (1.0, False)),
    (False, box(-1, -1, 3, 3), (2.0, True)),                   # the smallest tool has no domain: the next one runs
])
def test_final_cleanup_follows_the_paint_rules(connect, copper, expected):
    tools = rest_tools(2.0, 1.0)
    for tool in tools.values():
        tool['data']['tools_ncc_final_cleanup'] = True
    tools[2]['data']['tools_ncc_connect'] = connect
    domains = {1: MultiPolygon([box(0, 0, 10, 10)]), 2: MultiPolygon([box(0, 0, 2, 2)])}
    seen = []

    def cleanup(domain, dia, previous, generate, boundary_first):
        seen.append((dia, boundary_first))
        return [LineString([(5, 5), (6, 5)])], Polygon()

    ran = NccModel.finish_cleanup(tools, NccModel.copy_tools(tools), domains, 'out', cleanup, lambda **kw: None,
                                  lambda: None, copper)[2]
    assert seen == [expected]
    assert ran == expected[0]       # the diameter of the tool that ran: the threshold of the message follows it


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


def test_tcl_final_cleanup_reaches_the_tools_and_exclusion_the_area():
    area = MultiPolygon([box(0, 0, 10, 10)])
    gen2, output2 = run_rest(area, [1.0], lambda **kw: [LineString([(1, 1), (9, 1)])], tcl=True, rest=False,
                             final_cleanup=True, exclusion_areas=[box(0, 0, 5, 10)], real_area=True)
    # the area handed to the clearing is the one without the excluded part
    assert gen2._compute_area_to_clear_handler.called
    final_data = gen2.parent_tool.ncc_tools[1]['data']
    assert 'tools_ncc_final_cleanup' not in final_data      # the table itself is not written
    assert output2.tools[1]['data']['tools_ncc_final_cleanup'] is True
    pol = gen2.clear_polygon_worker.call_args.kwargs['pol']
    assert pol.bounds[0] >= 5 - 1e-9


class NoWidgets:
    """The panel of a Tcl run: any read is a defect."""

    def __getattr__(self, name):
        raise AssertionError('the Tcl job read the widget %s' % name)


@pytest.mark.parametrize('rest', [False, True])
def test_tcl_follows_only_its_arguments_not_the_panel_or_the_tool_table(rest):
    """The panel says rest, margin 3, other tools and other methods; the Tcl arguments decide."""
    gen, output = make_gen([1.0], known=[4.0, 2.0])
    gen.ui = NoWidgets()
    for row in gen.parent_tool.ncc_tools.values():
        row['data'].update(tools_ncc_method=2, tools_ncc_overlap=90, tools_ncc_connect=True, tools_ncc_contour=False,
                           tools_ncc_offset_choice=True, tools_ncc_offset_value=0.7, tools_ncc_final_cleanup=True)
    table_before = NccModel.copy_tools(gen.parent_tool.ncc_tools)
    gen.app.app_obj = gen.parent_tool
    seen = []

    def clear(**kw):
        seen.append(kw)
        return [LineString([(1, 5), (9, 5)])]

    gen.clear_polygon_worker = clear
    source = SimpleNamespace(kind='geometry', solid_geometry=[box(4, 4, 6, 6)])
    result = gen.clear_copper_tcl(
        source, None, [box(0, 0, 10, 10)], [1.0], None, 0.5, False, 0.0, 1, 'tcl_out', 0.2, False, True, 0, 0, rest,
        None, False, False, False)
    assert result != 'fail'
    assert seen and all(kw['tooldia'] == 1.0 for kw in seen)
    for kw in seen:
        assert (kw['ncc_method'], kw['ncc_overlap'], kw['ncc_connect'], kw['ncc_contour']) == (
            0, pytest.approx(0.2), False, True)
    assert list(output.tools) == [1]
    data = output.tools[1]['data']
    assert data['tools_ncc_final_cleanup'] is False and data['tools_ncc_offset_choice'] is False
    assert gen.parent_tool.ncc_tools == table_before


def test_tcl_resolver_builds_the_tool_rows_from_the_arguments():
    gen, __ = make_gen([1.0], known=[9.0], options={'tools_ncc_final_cleanup': True})
    gen.ui = NoWidgets()
    source = SimpleNamespace(kind='geometry', solid_geometry=[box(4, 4, 6, 6)])
    spec = gen.resolve_tcl_job(source, None, None, '0.5,0.25', None, 0.5, None, None, 0, 'out', 0.35, True, False,
                               'rev', 4, False, None, False, None, None, None, None, 45.0)
    assert [row['tooldia'] for row in spec.tool_table.values()] == [0.5, 0.25]
    assert spec.diameters == (0.5, 0.25)
    for row in spec.tool_table.values():
        data = row['data']
        assert (data['tools_ncc_method'], data['tools_ncc_overlap'], data['tools_ncc_connect'],
                data['tools_ncc_contour'], data['tools_ncc_crosshatch_angle']) == (4, 35.0, True, False, 45.0)
        assert data['tools_ncc_final_cleanup'] is True      # the option is the fallback of the argument
    assert 9.0 not in {row['tooldia'] for row in spec.tool_table.values()}


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


def test_legacy_tool_angle_does_not_leak_from_previous_tool(tool):
    import json

    data = next(iter(tool.ncc_tools.values()))['data']
    data.pop('tools_ncc_crosshatch_angle', None)
    tool.ui.tools_table.selectRow(0)
    tool.ui.crosshatch_angle_entry.set_value(45.0)
    tool.storage_to_form(data)
    assert tool.ui.crosshatch_angle_entry.get_value() == 0.0
    tool.ui.crosshatch_angle_entry.set_value(135.0)
    tool.ui.crosshatch_angle_entry.returnPressed.emit()
    assert data['tools_ncc_crosshatch_angle'] == 135.0
    tool.storage_to_form(json.loads(json.dumps(data)))
    assert tool.ui.crosshatch_angle_entry.get_value() == 135.0
    # the angle is editable for the Crosshatch method only
    tool.ui.method_combo.set_value(0)
    assert not tool.ui.crosshatch_angle_entry.isEnabled()
    tool.ui.method_combo.set_value(4)
    assert tool.ui.crosshatch_angle_entry.isEnabled()


def test_crosshatch_angle_is_read_from_the_preferences(tool):
    tool.app.options['tools_ncc_crosshatch_angle'] = 37.5
    tool.set_tool_ui()
    assert tool.ui.crosshatch_angle_entry.get_value() == 37.5


def zone_board(tool, name, *zones):
    """A real Geometry object of the project that owns the zones."""
    from appObjects.GeometryObject import GeometryObject
    board = GeometryObject(name, tool.app)
    board.obj_options.update(name=name, plot=True)
    board.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': [box(0, 0, 2, 2)]}}
    board.set_exclusion_zones({'ncc': list(zones)})
    return board


def test_exclusion_zones_of_the_source_reach_the_job_in_every_mode(tool, monkeypatch):
    from appPlugins.Common.clearing.job import ClearingJob
    specs = []
    monkeypatch.setattr(ClearingJob, 'run', lambda self, spec, run_threaded=True: specs.append(spec))
    zone = box(5, 5, 6, 6)
    prepare_run(tool, board=zone_board(tool, 'board', zone))
    tool.change_level('a')
    tool.ui.excl_cb.set_value(True)
    for method in (0, 2):
        tool.ui.select_method_combo.set_value(method)
        tool.gen.on_ncc_click()
    tool.gen.clear_ncc_area_selection_option([box(0, 0, 3, 3)])
    assert [list(spec.exclusion_zones) for spec in specs] == [[zone]] * 3
    tool.ui.excl_cb.set_value(False)
    tool.gen.on_ncc_click()
    assert specs[-1].exclusion is None and not specs[-1].exclusion_zones


# ---------------------------------------------------------------------------------------------------------------
# Polygon Selection (click)
# ---------------------------------------------------------------------------------------------------------------
LEFT_ROOM = box(1, 1, 9.5, 9)
RIGHT_ROOM = box(10.5, 1, 19, 9)


def two_room_board():
    """Copper: a frame and a bar, so the empty area is 2 separate rooms."""
    return geometry_object('board', box(0, 0, 20, 1), box(0, 9, 20, 10), box(0, 0, 1, 10), box(19, 0, 20, 10),
                           box(9.5, 0, 10.5, 10))


def capture_outputs(t):
    outputs = []

    def new_object(kind, name, worker, **kwargs):
        output = SimpleNamespace(kind='geometry', tools={}, obj_options={}, solid_geometry=[])
        result = worker(output, t.app)
        outputs.append(output)
        return 'fail' if result == 'fail' or result is False else output

    t.new_object = new_object
    t.app.collection.promise = t.app.collection.remove_promise = lambda name: None
    t.app.app_obj = SimpleNamespace(new_object=new_object)
    return outputs


def paths_of(output):
    found = []

    def walk(item):
        if isinstance(item, (list, tuple)):
            for sub in item:
                walk(sub)
        elif hasattr(item, 'geoms'):
            for sub in item.geoms:
                walk(sub)
        else:
            found.append(item)

    for tool_data in output.tools.values():
        walk(tool_data['solid_geometry'])
    return found


def click_event(button, x=0.0, y=0.0):
    return SimpleNamespace(button=button, pos=(x, y))


def click_ncc(t, points):
    """Generate in the Polygon Selection mode: click the points, then right click to run."""
    t.app.event_is_dragging = False
    t.ui.select_method_combo.set_value(3)
    t.gen.on_ncc_click()
    for x, y in points:
        t.selector.on_click_release(click_event(1, x, y))
    t.selector.on_click_release(click_event(2))


def test_empty_faces_are_the_copper_free_regions():
    from appPlugins.Common.clearing.regions import empty_faces
    board = unary_union(two_room_board().solid_geometry)
    faces = empty_faces(box(*board.bounds), board)
    assert len(faces) == 2
    assert sorted(round(face.area, 6) for face in faces) == sorted([LEFT_ROOM.area, RIGHT_ROOM.area])
    assert not unary_union(faces).intersects(board.buffer(-1e-6))


def test_click_in_an_empty_region_clears_that_face_only(tool):
    tool.app.options['tools_ncc_tools'] = '0.4'
    tool.set_tool_ui()
    prepare_run(tool, board=two_room_board())
    outputs = capture_outputs(tool)
    click_ncc(tool, [(5, 5)])
    assert tool.ui.select_method_combo.get_value() == 3
    assert len(outputs) == 1
    paths = paths_of(outputs[0])
    assert paths
    assert all(path.bounds[2] < 9.5 for path in paths)
    assert unary_union(paths).within(LEFT_ROOM.buffer(1e-6))

    # a second click on the same face removes it: nothing is left to clear
    outputs.clear()
    click_ncc(tool, [(5, 5), (6, 6)])
    assert not outputs
    assert any('No area' in m for m in messages(tool))


def test_tcl_single_point_gives_the_same_paths_as_the_click(tool):
    tool.app.options['tools_ncc_tools'] = '0.4'
    tool.set_tool_ui()
    prepare_run(tool, board=two_room_board())
    outputs = capture_outputs(tool)
    click_ncc(tool, [(15, 5)])
    tcl = tool.gen.clear_copper_tcl(
        tool.app.collection.objects['board'], ncc_tooldia='0.4', select_method=3, click_points=[(15.0, 5.0)],
        outname='board_ncc', plot=False, run_threaded=False, margin=tool.ui.margin_entry.get_value())
    assert tcl != 'fail'
    assert len(outputs) == 2
    gui, scripted = (sorted(round(c, 6) for p in paths_of(o) for c in p.bounds) for o in outputs)
    assert gui and gui == scripted
    assert all(path.bounds[0] > 10.5 for path in paths_of(outputs[0]))


# ---------------------------------------------------------------------------------------------------------------
# clearing direction
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('direction', ['climb', 'conv'])
def test_final_cleanup_rings_have_the_direction_in_the_gcode(direction):
    import numpy as np
    from shapely.geometry import Point
    from test_clearing_engine import ring_runs
    from test_clearing_paths import make_geometry, swept_around, turn
    from test_cncjob_export_gcode import gcode_tokens, multi_geo_gcode

    geometry = make_geometry()
    geometry.app.units = 'MM'
    geometry.app.options = {'global_tolerance': 0}
    app = SimpleNamespace(options={'global_tolerance': 0.005}, abort_flag=False, units='MM', app_units='MM', log=Mock())
    area = MultiPolygon([box(0, 0, 30, 30).difference(Point(15, 15).buffer(5, quad_segs=16))])
    tools = rest_tools(3.0, 1.0)
    tools[2]['data'].update(tools_ncc_method=0, tools_ncc_final_cleanup=True, tools_ncc_clear_milling=direction)
    # each tool clears a single line: the stock that is left goes to the final cleanup of the smallest tool
    lines = {3.0: [LineString([(3, 3), (27, 3)])], 1.0: [LineString([(1, 29), (29, 29)])]}
    generated, domains, *__ = NccModel.generate_tools(
        'rest', tools, NccModel.copy_tools(tools), [3.0, 1.0], {1: area, 2: area}, 'n', 4, 32,
        lambda **kw: lines[round(kw['tooldia'], 4)], lambda: None, lambda text: None)

    def clear_paths(**kw):
        return NccModel.clear_polygon(
            kw['pol'], kw['tooldia'], kw['ncc_method'], kw['ncc_overlap'], kw['ncc_connect'], kw['ncc_contour'],
            False, 32, geometry.clear_polygon_shrink, geometry.clear_polygon_seed, geometry.clear_polygon_lines,
            direction=kw.get('direction', 'none'))[0]

    cleaned, __, ___ = NccModel.finish_cleanup(
        tools, generated, domains, 'out',
        lambda domain, dia, previous, generate, boundary_first, direction='none': camlib.residual_cleanup(
            app, domain, dia, 32, previous, generate, physical=True, boundary_first=boundary_first,
            direction=direction),
        clear_paths, lambda: None)
    paths = cleaned[2]['solid_geometry'][cleaned[2]['ncc_cleanup_start']:]
    outer = 1 if direction == 'climb' else -1

    # the islands of the stock: the copper disc and the band that the first tool swept
    islands = [Point(15, 15).buffer(5, quad_segs=16), LineString([(3, 3), (27, 3)]).buffer(1.5)]

    def expected(coords):
        # a ring that hugs an island (an inset of the stock around it) is a hole ring: CW for a climb cut
        ring = Polygon(coords)
        around = any(ring.contains(island) and ring.exterior.distance(island) < 3.0 for island in islands)
        return -outer if around else outer

    rings = [np.asarray(path.coords) for path in paths if len(path.coords) > 3 and path.coords[0] == path.coords[-1]]
    assert len(rings) >= 4 and {expected(ring) for ring in rings} == {1, -1}
    assert all(turn(ring) == expected(ring) for ring in rings)
    # the pieces of the boundary of the cutter-center area around the hole (made by the cleanup itself) turn like
    # the rings around the island: CW for a climb cut
    pieces = [path for path in paths if path.coords[0] != path.coords[-1]
              and np.hypot(*(np.asarray(path.coords) - (15, 15)).T).max() < 5.6]
    assert len(pieces) >= 3
    assert {swept_around(piece, (15, 15)) for piece in pieces} == {-outer}

    # the final G-code: the cleanup rings keep the direction at both depths
    __, gcode = multi_geo_gcode(cleaned[2]['solid_geometry'], data=dict(
        tools_mill_keep_direction=True, tools_mill_multidepth=True, tools_mill_depthperpass=0.1,
        tools_mill_cutz=-0.2))
    cuts = gcode_tokens(gcode)[1]
    for ring in rings:
        runs = [run for cut in cuts for run in ring_runs(np.asarray(cut), ring)]
        assert len(runs) == 2 and {turn(run) for run in runs} == {expected(ring)}


def test_tcl_resolver_carries_the_clearing_direction_and_fails_on_a_bad_one():
    gen, __ = make_gen([1.0], known=[], options={'tools_ncc_clear_milling': 'conv'})
    gen.ui = NoWidgets()
    source = SimpleNamespace(kind='geometry', solid_geometry=[box(4, 4, 6, 6)])
    args = (source, None, None, '0.5', None, 0.5, None, None, 0, 'out', 0.35, True, False, 'rev', 0, False, None,
            False, None, None, None, None, None, None)
    assert [r['data']['tools_ncc_clear_milling'] for r in gen.resolve_tcl_job(*args).tool_table.values()] == ['conv']
    spec = gen.resolve_tcl_job(*args, 'climb')
    assert [r['data']['tools_ncc_clear_milling'] for r in spec.tool_table.values()] == ['climb']
    assert gen.resolve_tcl_job(*args, 'left') == 'fail'


@pytest.mark.parametrize('kind', ['ncc', 'paint'])
def test_direction_widget_goes_into_the_row_data(qapp, tmp_path, kind):
    from test_clearing_golden import Plugin
    plugin = Plugin(qapp, tmp_path, kind, SimpleNamespace(kind='gerber'), [0.5, 0.25])
    key = 'tools_%s_clear_milling' % kind
    try:
        plugin.tool.connect_signals()
        ui = plugin.tool.ui
        assert [r['data'][key] for r in plugin.tools.values()] == ['none', 'none']
        ui.clear_dir_radio.set_value('climb')
        ui.clear_dir_radio.activated_custom.emit('climb')
        assert [r['data'][key] for r in plugin.tools.values()] == ['climb', 'climb']
    finally:
        plugin.close()
