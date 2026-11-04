"""Compare model core (appPlugins/ToolCompare/CompareGen.py): Gerber, Geometry and Excellon with Shapely only."""
import importlib.util
import itertools
import math
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import shapely
from shapely import LineString, Point, Polygon
from shapely.errors import GEOSException
from shapely.geometry import box

# CompareGen is Qt free; load it by path so the plugin package (which imports Qt) is not needed
_path = Path(__file__).resolve().parent.parent / 'appPlugins' / 'ToolCompare' / 'CompareGen.py'
_spec = importlib.util.spec_from_file_location('CompareGen_under_test', _path)
cg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg)

TOL = 0.001


def grb(shapes, units='MM'):
    return NS(units=units, solid_geometry=shapes)


def geo(shapes, units='MM'):
    return NS(units=units, multigeo=False, solid_geometry=shapes)


def exc(dia, drills=(), slots=(), units='MM'):
    return NS(units=units, tools={'1': {'tooldia': dia, 'drills': [Point(p) for p in drills],
                                        'slots': [(Point(a), Point(b)) for a, b in slots]}})


def plane(top):
    """Plane x 0..100, y -10..top with 103 vertices (tiled once V_TILE is lowered to 50)."""
    return Polygon([(0, -10), (100, -10)] + [(100 - i, top) for i in range(101)])


def outer_bisector(e1, e2, turn):
    """Unit vector pointing away from the inside of a bend (turn > 0 is a left turn)."""
    n1 = (e1[1], -e1[0])
    n2 = (e2[1], -e2[0])
    sx, sy = n1[0] + n2[0], n1[1] + n2[1]
    norm = math.hypot(sx, sy)
    s = math.copysign(1.0, turn)
    return s * sx / norm, s * sy / norm


def compare(old, new, kind, tol=TOL, cancelled=None):
    return cg.compare(cg.snapshot(old, kind), cg.snapshot(new, kind), kind, tol, cancelled)


def counts(res):
    return res.stats['removed']['count'], res.stats['added']['count']


def centers(polys):
    return sorted((round(p.centroid.x, 3), round(p.centroid.y, 3)) for p in polys)


# ------------------------------------------------------------------------------------- unchanged / tolerance
def test_same_snapshot_is_empty():
    shapes = [box(0, 0, 2, 2), box(10, 0, 12, 2)]
    res = compare(grb(shapes), grb(shapes), 'grb')
    assert res.state == 'complete' and counts(res) == (0, 0)


def test_big_square_with_inner_square_equals_big_square():
    big = box(0, 0, 100, 100)
    res = compare(grb([big, box(40, 40, 60, 60)]), grb([big]), 'grb')
    assert counts(res) == (0, 0)


def test_shift_below_tol_across_grid_boundary_is_empty(monkeypatch):
    monkeypatch.setattr(cg, 'V_TILE', 50)
    res = compare(grb([plane(0.00049)]), grb([plane(0.00051)]), 'grb')
    assert counts(res) == (0, 0)


BEND = LineString([(-10, 0), (0, 0), (10 * math.cos(math.radians(30)), 10 * math.sin(math.radians(30)))])
E2_30 = (math.cos(math.radians(30)), math.sin(math.radians(30)))
D_NEAR = 0.99 * TOL
Q_NEAR = tuple(D_NEAR * c for c in outer_bisector((1, 0), E2_30, 1))
BEND_EXTRA = {
    'short line on arm 1': LineString([(-0.5, -D_NEAR), (-0.01, -D_NEAR)]),
    'short line at the bend': LineString([Q_NEAR, (Q_NEAR[0] + 1e-7 * E2_30[0], Q_NEAR[1] + 1e-7 * E2_30[1])]),
    'tiny polygon at the bend': Point(Q_NEAR).buffer(1e-7),
}


@pytest.mark.parametrize('extra', list(BEND_EXTRA.values()), ids=list(BEND_EXTRA))
def test_feature_at_099_tol_of_bend_is_not_reported(extra):
    old, new = geo([BEND]), geo([BEND, extra])
    assert counts(compare(old, new, 'geo')) == (0, 0)
    assert counts(compare(new, old, 'geo')) == (0, 0)


def test_bend_points_at_0999_tol_are_never_reported():
    rng = random.Random(20261104)
    d = 0.999 * TOL
    for case in range(200):
        turn = math.radians(rng.uniform(1, 179)) * rng.choice((1, -1))
        e2 = (math.cos(turn), math.sin(turn))
        u = outer_bisector((1, 0), e2, turn)
        q = (d * u[0], d * u[1])
        p0 = (-rng.uniform(1, 10), 0.0)
        p2 = (rng.uniform(1, 10) * e2[0], rng.uniform(1, 10) * e2[1])
        if case % 2:  # polygon variant: a tiny disc just outside the bend of a triangle
            tri = Polygon([p0, (0, 0), p2])
            old, new, kind = grb([tri]), grb([tri, Point(q).buffer(1e-7)]), 'grb'
        else:  # line variant: a 1e-7 segment just outside the bend of a polyline
            bent = LineString([p0, (0, 0), p2])
            short = LineString([q, (q[0] + 1e-7 * e2[0], q[1] + 1e-7 * e2[1])])
            old, new, kind = geo([bent]), geo([bent, short]), 'geo'
        assert counts(compare(old, new, kind)) == (0, 0), case
        assert counts(compare(new, old, kind)) == (0, 0), case


def test_change_at_105_tol_is_reported():
    shift = 1.05 * TOL
    res = compare(geo([LineString([(0, 0), (10, 0)])]), geo([LineString([(0, shift), (10, shift)])]), 'geo')
    assert counts(res) == (1, 1)
    assert res.stats['removed']['length'] == pytest.approx(10, rel=1e-6)


def test_narrow_rectangle_far_from_old_is_fully_added():
    res = compare(grb([box(0, 0, 1, 1)]), grb([box(0, 0, 1, 1), box(0, 50, 100, 50.0004)]), 'grb')
    assert counts(res) == (0, 1)
    assert res.stats['added']['area'] == pytest.approx(0.04, rel=1e-6)


def test_shortened_line_leaves_cut_part_in_removed():
    res = compare(geo([LineString([(0, 0), (10, 0)])]), geo([LineString([(0, 0), (6, 0)])]), 'geo')
    assert counts(res) == (1, 0)
    assert res.stats['removed']['length'] == pytest.approx(4, abs=0.01)


def test_line_on_tile_edge_is_kept_at_full_length(monkeypatch):
    monkeypatch.setattr(cg, 'V_DRAW', 4)
    line = LineString([(0, i / 10) for i in range(11)])
    res = compare(geo([line]), geo([box(5, 5, 6, 6)]), 'geo')
    assert sum(p.length for p in res.removed.line_paths) == pytest.approx(1, abs=1e-9)


def test_multigeo_change_in_one_tool_only():
    def mg(t2):
        return NS(units='MM', multigeo=True, solid_geometry=[],
                  tools={'1': {'solid_geometry': [box(0, 0, 2, 2)]}, '2': {'solid_geometry': [t2]}})

    res = compare(mg(box(10, 0, 12, 2)), mg(box(10, 0, 11, 2)), 'geo')
    assert counts(res) == (1, 0)
    assert res.stats['removed']['area'] == pytest.approx(2, abs=0.01)


def test_square_and_disjoint_line_vs_empty_gives_fill_outline_and_line():
    res = compare(geo([box(0, 0, 2, 2), LineString([(10, 0), (10, 5)])]), geo([]), 'geo')
    assert res.state == 'complete'
    assert sum(p.area for p in res.removed.fill_pieces) == pytest.approx(4, rel=1e-6)
    assert sum(p.length for p in res.removed.line_paths) == pytest.approx(8 + 5, rel=1e-6)


def test_square_minus_line_uses_line_buffer_for_area():
    r = cg.buffer_radius(0.1)
    res = compare(geo([box(0, 0, 4, 4)]), geo([LineString([(0, 2), (4, 2)])]), 'geo', tol=0.1)
    assert res.stats['removed']['area'] == pytest.approx(16 - 8 * r, rel=1e-6)
    assert res.stats['added']['count'] == 0


def test_notch_in_big_tiled_plane_is_the_only_change(monkeypatch):
    monkeypatch.setattr(cg, 'V_TILE', 50)
    old = plane(100)
    new = old.difference(box(40, 99, 41, 101))
    res = compare(grb([old]), grb([new]), 'grb')
    assert res.stats['removed']['area'] == pytest.approx(1, abs=0.02)
    assert res.stats['added']['count'] == 0
    assert min(p.bounds[0] for p in res.removed.polygons) >= 39.98
    assert max(p.bounds[2] for p in res.removed.polygons) <= 41.02


def test_one_side_empty_complex_polygon_is_split_without_seams(monkeypatch):
    monkeypatch.setattr(cg, 'V_TILE', 50)
    monkeypatch.setattr(cg, 'V_DRAW', 40)
    poly = Point(0, 0).buffer(50, quad_segs=32).difference(Point(0, 0).buffer(10, quad_segs=32))
    res = compare(grb([poly]), grb([]), 'grb')
    assert res.state == 'complete'
    assert all(shapely.get_num_coordinates(p) <= 40 for p in res.removed.fill_pieces)
    assert sum(p.area for p in res.removed.fill_pieces) == pytest.approx(poly.area, rel=1e-6)
    assert sum(p.length for p in res.removed.line_paths) == pytest.approx(poly.length, rel=1e-6)


# ------------------------------------------------------------------------------------------- Excellon holes
def test_drills_within_tol_of_each_other_are_matched():
    res = compare(exc(0.5, drills=[(0, 0), (-0.9, 0)]), exc(0.5, drills=[(-0.1, 0), (0.9, 0)]), 'exc', tol=1.0)
    assert res.state == 'complete' and counts(res) == (0, 0)


def test_matching_does_not_depend_on_input_order():
    removed = set()
    for perm in itertools.permutations([(-0.5, 0), (0.5, 0)]):
        res = compare(exc(0.5, drills=perm), exc(0.5, drills=[(0, 0)]), 'exc', tol=1.0)
        assert counts(res) == (1, 0)
        removed.add(res.removed.holes[0]['p1'])
    assert len(removed) == 1


def test_drill_diameter_change_beyond_tol_is_removed_and_added():
    assert counts(compare(exc(1.0, drills=[(0, 0)]), exc(1.5, drills=[(0, 0)]), 'exc')) == (1, 1)


def test_slot_with_reversed_end_points_is_empty():
    res = compare(exc(0.5, slots=[((0, 0), (2, 0))]), exc(0.5, slots=[((2, 0), (0, 0))]), 'exc')
    assert counts(res) == (0, 0)


def test_slot_moved_or_resized_is_removed_and_added():
    old = exc(0.5, slots=[((0, 0), (2, 0))])
    for slot in [((0, 1), (2, 1)), ((0, 0), (3, 0))]:
        assert counts(compare(old, exc(0.5, slots=[slot]), 'exc')) == (1, 1)


def test_mm_and_inch_objects_of_same_board_are_equal():
    mm = grb([box(0, 0, 25.4, 12.7), box(50.8, 0, 76.2, 25.4)], units='MM')
    inch = grb([box(0, 0, 1, 0.5), box(2, 0, 3, 1)], units='IN')
    assert counts(compare(mm, inch, 'grb')) == (0, 0)


# ------------------------------------------------------------------------------- errors, cancel, snapshot
def test_line_geos_error_marks_incomplete_and_keeps_other_results(monkeypatch):
    old_line = LineString([(10, 0), (10, 5)])
    real_diff = cg._diff_one

    def flaky(subject, candidates, r):
        if subject.equals(old_line):
            raise GEOSException('injected')
        return real_diff(subject, candidates, r)

    monkeypatch.setattr(cg, '_diff_one', flaky)
    res = compare(geo([box(0, 0, 4, 4), old_line]), geo([LineString([(10, 0), (10, 4)])]), 'geo')
    assert res.state == 'incomplete'
    assert len(res.failed) == 1
    assert res.stats['removed']['area'] == pytest.approx(16, rel=1e-6)
    assert not res.removed.lines


def _big_diff():
    old = grb([box(3 * i, 0, 3 * i + 2, 2) for i in range(400)])
    new = grb([box(3 * i + 1, 0, 3 * i + 3, 2) for i in range(400)])
    return old, new, 'grb', TOL


def _hole_chain():
    return exc(0.5, drills=[(i, 0) for i in range(6)]), exc(0.5, drills=[(i + 0.4, 0) for i in range(6)]), 'exc', 1.0


def _complex_polygon_vs_empty():
    poly = Point(0, 0).buffer(50, quad_segs=32).difference(Point(0, 0).buffer(10, quad_segs=32))
    return grb([poly]), grb([]), 'grb', TOL


@pytest.mark.parametrize('build', [_big_diff, _hole_chain, _complex_polygon_vs_empty],
                         ids=['differences', 'hole matching', 'draw splitting'])
def test_cancel_after_first_check_raises_cancelled(monkeypatch, build):
    monkeypatch.setattr(cg, 'V_TILE', 50)
    monkeypatch.setattr(cg, 'V_DRAW', 40)
    old, new, kind, tol = build()
    checks = []

    def cancelled():
        checks.append(1)
        return len(checks) > 1

    with pytest.raises(cg.Cancelled):
        cg.compare(cg.snapshot(old, kind), cg.snapshot(new, kind), kind, tol, cancelled)


MULTIGEO = NS(units='MM', multigeo=True, solid_geometry=[],
              tools={'1': {'solid_geometry': box(0, 0, 1, 1)},
                     '2': {'solid_geometry': LineString([(0, 0), (1, 1)])}})


@pytest.mark.parametrize('obj, kind, polys, lines', [
    pytest.param(grb(box(0, 0, 1, 1)), 'grb', 1, 0, id='scalar-polygon'),
    pytest.param(grb(LineString([(0, 0), (1, 1)])), 'grb', 0, 1, id='scalar-line'),
    pytest.param(grb([[box(0, 0, 1, 1)], [LineString([(0, 0), (1, 1)])]]), 'grb', 1, 1, id='nested-list'),
    pytest.param(MULTIGEO, 'geo', 1, 1, id='multigeo-tools'),
])
def test_snapshot_flattens_each_input_shape(obj, kind, polys, lines):
    snap = cg.snapshot(obj, kind)
    assert (len(snap['polys']), len(snap['lines'])) == (polys, lines)


def test_snapshot_does_not_share_the_source_list():
    src = [box(0, 0, 1, 1)]
    snap = cg.snapshot(grb(src), 'grb')
    src.append(box(5, 5, 6, 6))
    assert len(snap['polys']) == 1


@pytest.mark.parametrize('value', ['#800020FF', '#800020'])
def test_normalize_color_accepts_rrggbb_with_or_without_alpha(value):
    assert cg.normalize_color(value, '#00FF00FF') == {
        'outline': '#800020FF', 'fill': '#80002060', 'none': '#80002000', 'swatch': '#800020'}


def test_normalize_color_uses_default_for_bad_value():
    assert cg.normalize_color('bad', '#00FF00FF') == {
        'outline': '#00FF00FF', 'fill': '#00FF0060', 'none': '#00FF0000', 'swatch': '#00FF00'}


# ------------------------------------------------------------------------------- Gerber pads: removed/added/moved
def test_gerber_pad_removed_added_and_moved():
    old = grb([box(-1, -1, 1, 1), box(9, -1, 11, 1), box(19, -1, 21, 1)])
    new = grb([box(-1, -1, 1, 1), box(9, 4, 11, 6), box(29, -1, 31, 1)])
    res = compare(old, new, 'grb')
    assert res.state == 'complete'
    assert counts(res) == (2, 2)
    assert centers(res.removed.polygons) == [(10.0, 0.0), (20.0, 0.0)]
    assert centers(res.added.polygons) == [(10.0, 5.0), (30.0, 0.0)]
    assert sum(p.area for p in res.removed.polygons) == pytest.approx(8, rel=1e-6)
    assert sum(p.area for p in res.added.polygons) == pytest.approx(8, rel=1e-6)


# ------------------------------------------------------------------------------- controller (app fixture fc)
OLD_SHAPES = [box(0, 0, 2, 2)]
NEW_SHAPES = [box(0, 0, 2, 2), box(50, 0, 52, 2)]


class FakeColl:
    """Records the collection calls the controller makes. pending_ready() is controlled by the test."""

    def __init__(self, ready=True):
        self.ready, self.enabled = ready, True
        self.batches, self.vis, self.redraws, self.cleared = [], [], 0, 0

    def add_batch(self, items, layer=0, tolerance=None, visible=True):
        self.batches.append((items, visible))

    def update_visibility(self, state):
        self.vis.append(state)

    def redraw(self, update_colors=None):
        self.redraws += 1

    def clear(self):
        self.cleared += 1

    def pending_ready(self):
        return self.ready


@pytest.fixture(scope='module')
def fc():
    import os
    import sys
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6 import QtWidgets
    from appMain import App
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    return App(qapp=qapp, headless=True)


FAKES = []  # every fake collection of the test session, so a test can finish the batches it left running


@pytest.fixture
def canvas(fc, monkeypatch):
    made, state = [], {'ready': True}

    def new_shape_collection(self, **kwargs):
        coll = FakeColl(state['ready'])
        made.append(coll)
        FAKES.append(coll)
        return coll

    # the headless canvas proxy ignores instance attributes, so the class method is patched
    monkeypatch.setattr(type(fc.plotcanvas), 'new_shape_collection', new_shape_collection)
    return NS(made=made, state=state)


@pytest.fixture
def jobs(fc, monkeypatch):
    """The worker job is queued; jobs.run() executes it on the calling thread."""
    queued = []
    monkeypatch.setattr(fc, 'worker_task', NS(emit=queued.append))

    def run_all():
        while queued:
            job = queued.pop(0)
            job['fcn'](*job['params'])

    yield NS(run=run_all)
    # the plugin is shared by the module: let the batches this test left pending finish
    for coll in FAKES:
        coll.ready = True
    FAKES.clear()
    pump(100)


@pytest.fixture
def msgs(fc, monkeypatch):
    out = []
    monkeypatch.setattr(fc, 'inform', NS(emit=out.append))
    return out


def pump(ms=60):
    """Run the Qt event loop for ms milliseconds so the 0 ms tick and the 30 ms poll timers fire."""
    from PyQt6 import QtWidgets
    end = time.time() + ms / 1000
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.003)


def _cmp_mod(fc):
    """The module of the live plugin class (the CompareGen name that Compare.py uses is patched there)."""
    return sys.modules[type(fc.compare_tool).__module__]


def start(fc, tag, old_shapes, new_shapes, kind='grb'):
    """Two objects named by tag, the plugin tab opened, Old and New selected."""
    for suffix, shapes in (('old', old_shapes), ('new', new_shapes)):
        def init(obj, app, shapes=shapes):
            obj.units = 'MM'
            obj.solid_geometry = shapes
            if kind == 'geo':
                obj.multigeo = False
        fc.app_obj.new_object('geometry' if kind == 'geo' else 'gerber', f'{tag}-{suffix}', init)
    tool = fc.compare_tool
    if fc.ui.notebook.indexOf(fc.ui.plugin_tab) < 0:
        fc.ui.notebook.addTab(fc.ui.plugin_tab, 'Plugin')
    tool.run(toggle=False)
    if kind == 'geo':
        tool.on_type_changed('geo')
    tool.ui.old_combo.setCurrentText(f'{tag}-old')
    tool.ui.new_combo.setCurrentText(f'{tag}-new')
    return tool


def shape_items(coll):
    return [item for items, _ in coll.batches for item in items]


def is_added(coll):
    """The added result sits at x >= 50 in these tests; the removed result at x <= 2."""
    items = shape_items(coll)
    return bool(items) and min(item['shape'].bounds[0] for item in items) > 25


@pytest.mark.parametrize('action', ['reset', 'type', 'old_new', 'cleanup'])
def test_result_after_invalidation_is_not_drawn(fc, canvas, jobs, action):
    tool = start(fc, f'stale-{action}', OLD_SHAPES, NEW_SHAPES)
    tool.on_compare()
    if action == 'reset':
        tool.set_tool_ui()
    elif action == 'type':
        tool.on_type_changed('exc')
    elif action == 'old_new':
        tool.ui.new_combo.setCurrentText(f'stale-{action}-old')
    else:
        tool.on_plugin_cleanup()
    jobs.run()
    pump()
    assert not any(c.batches for c in canvas.made)


@pytest.fixture
def visible_ui(fc):
    fc.ui.show()
    yield
    fc.ui.hide()


@pytest.mark.parametrize('action', ['replace', 'close'])
def test_plugin_exit_detaches_results_and_rejects_pending_job(fc, canvas, jobs, visible_ui, action):
    tool = start(fc, f'exit-{action}', OLD_SHAPES, NEW_SHAPES)
    tool.on_compare()
    jobs.run()
    pump()
    results = [c for c in canvas.made if c.batches]
    assert results and all(c.enabled for c in results)
    if action == 'replace':
        fc.sub_tool.run(toggle=False)
        assert fc.ui.plugin_scroll_area.widget() is fc.sub_tool
    else:
        fc.ui.notebook.closeTab(fc.ui.notebook.indexOf(fc.ui.plugin_tab))
        assert fc.ui.notebook.indexOf(fc.ui.plugin_tab) < 0
    pump()
    jobs.run()
    pump()
    assert all(not c.enabled for c in results)
    assert not tool.ui.summary_label.text()
    assert not any(c.enabled and c.batches for c in canvas.made)
    tool = start(fc, f'queued-exit-{action}', OLD_SHAPES, NEW_SHAPES)
    tool.on_compare()
    if action == 'replace':
        fc.sub_tool.run(toggle=False)
    else:
        fc.ui.notebook.closeTab(fc.ui.notebook.indexOf(fc.ui.plugin_tab))
    pump()
    jobs.run()
    pump()
    assert not tool.ui.summary_label.text()
    assert not any(c.enabled and c.batches for c in canvas.made)


def test_notebook_focus_keeps_comparison(fc, canvas, jobs, visible_ui):
    tool = start(fc, 'focus', OLD_SHAPES, NEW_SHAPES)
    tool.on_compare()
    fc.ui.notebook.setCurrentWidget(fc.ui.properties_tab)
    pump()
    jobs.run()
    pump()
    assert tool.ui.summary_label.text()
    results = [c for c in canvas.made if c.batches]
    assert results and all(c.enabled for c in results)
    fc.ui.notebook.setCurrentWidget(fc.ui.plugin_tab)
    pump()
    assert all(c.enabled for c in results)


@pytest.mark.parametrize('source', ['old', 'new'])
def test_source_edit_invalidates_pending_and_displayed_result(fc, canvas, jobs, source):
    tag = f'edit-{source}'
    fc.app_obj.new_object('gerber', f'{tag}-unrelated',
                          lambda obj, app: setattr(obj, 'solid_geometry', OLD_SHAPES))
    tool = start(fc, tag, OLD_SHAPES, NEW_SHAPES)
    obj = fc.collection.get_by_name(f'{tag}-{source}')
    tool.on_compare()
    obj.solid_geometry = [box(100, 100, 102, 102)]
    fc.app_obj.object_changed.emit(obj)
    jobs.run()
    pump()
    assert not tool.ui.summary_label.text()
    assert not any(c.batches for c in canvas.made)
    tool.on_compare()
    jobs.run()
    pump()
    results = [c for c in canvas.made if c.batches]
    assert results and all(c.enabled for c in results)
    fc.app_obj.object_changed.emit(fc.collection.get_by_name(f'{tag}-unrelated'))
    assert tool.ui.summary_label.text()
    assert all(c.enabled for c in results)
    obj.solid_geometry = []
    fc.app_obj.object_changed.emit(obj)
    pump()
    assert not tool.ui.summary_label.text()
    assert all(not c.enabled for c in results)


def test_added_unchecked_before_result_is_hidden_at_submit(fc, canvas, jobs):
    tool = start(fc, 'hide-submit', OLD_SHAPES, NEW_SHAPES)
    tool.ui.added_cb.setChecked(False)
    tool.on_compare()
    jobs.run()
    pump()
    added = [c for c in canvas.made if is_added(c)]
    assert added
    assert all(vis is False for c in added for _, vis in c.batches)


def test_added_unchecked_while_pending_is_hidden_after_install(fc, canvas, jobs):
    canvas.state['ready'] = False
    tool = start(fc, 'hide-pending', OLD_SHAPES, NEW_SHAPES)
    tool.on_compare()
    jobs.run()
    pump()
    assert canvas.made
    tool.ui.added_cb.setChecked(False)
    for c in canvas.made:
        c.ready = True
    pump(150)
    added = [c for c in canvas.made if is_added(c)]
    assert added and all(c.vis[-1] is False for c in added)


def test_fatal_result_draws_nothing_and_reports_error(fc, canvas, jobs, msgs, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError('boom')

    monkeypatch.setattr(_cmp_mod(fc).CompareGen, 'compare', boom)
    tool = start(fc, 'fatal', OLD_SHAPES, NEW_SHAPES)
    tool.on_compare()
    jobs.run()
    pump()
    assert not any(c.batches for c in canvas.made)
    assert any('ERROR_NOTCL' in m and 'boom' in m for m in msgs)


def test_incomplete_result_draws_good_pieces_and_warns(fc, canvas, jobs, msgs, monkeypatch):
    cg_real = _cmp_mod(fc).CompareGen
    old_line = LineString([(10, 0), (10, 5)])
    real_diff = cg_real._diff_one

    def flaky(subject, candidates, r):
        if subject.equals(old_line):
            raise GEOSException('injected')
        return real_diff(subject, candidates, r)

    monkeypatch.setattr(cg_real, '_diff_one', flaky)
    tool = start(fc, 'incomplete', [box(0, 0, 4, 4), old_line], [LineString([(10, 0), (10, 4)])], kind='geo')
    tool.on_compare()
    jobs.run()
    pump()
    drawn = [item['shape'] for c in canvas.made for items, _ in c.batches for item in items]
    assert sum(s.area for s in drawn) == pytest.approx(16, rel=1e-6)
    assert any('WARNING_NOTCL' in m and 'Incomplete' in m for m in msgs)


def test_five_runs_reset_and_cleanup_detach_every_collection(fc, canvas, jobs):
    tool = start(fc, 'five', OLD_SHAPES, NEW_SHAPES)
    for _ in range(5):
        tool.on_compare()
        jobs.run()
        pump()
    results = [c for c in canvas.made if c.batches]  # the objects' own collections have no batch
    assert len(results) >= 5
    tool.set_tool_ui()
    tool.on_plugin_cleanup()
    pump()
    assert all(c.enabled is False and c.cleared == 1 for c in results)


def test_collections_pending_at_retire_are_cleared_once_ready(fc, canvas, jobs):
    canvas.state['ready'] = False
    tool = start(fc, 'pending', OLD_SHAPES, NEW_SHAPES)
    for _ in range(5):
        tool.on_compare()
        jobs.run()
        pump(30)
    tool.set_tool_ui()
    tool.on_plugin_cleanup()
    pump(30)
    results = [c for c in canvas.made if c.batches]
    assert results and all(c.enabled is False and c.cleared == 0 for c in results)
    for c in results:
        c.ready = True
    pump(100)
    assert all(c.cleared == 1 for c in results)


def test_real_null_canvas_run_finishes_and_poll_stops(fc, jobs, msgs):
    tool = start(fc, 'null', OLD_SHAPES, NEW_SHAPES)
    tool.on_compare()
    jobs.run()
    pump()
    assert any('Compare done' in m for m in msgs)
    assert not tool._poll_timer.isActive()


@pytest.mark.parametrize('stage', ['queued', 'pending', 'installed', 'hidden'])
def test_swatch_colors_are_saved_and_used_by_current_and_next_comparison(fc, jobs, monkeypatch, tmp_path, stage):
    import json
    import threading
    from multiprocessing.pool import ThreadPool
    from PyQt6 import QtCore, QtGui, QtTest, QtWidgets
    from vispy.gloo.context import FakeCanvas, set_current_canvas
    from appGUI.VisPyVisuals import ShapeCollection

    gl_canvas = FakeCanvas()
    set_current_canvas(gl_canvas)
    keys = ('tools_compare_removed_color', 'tools_compare_added_color')
    for key in keys:
        monkeypatch.setitem(fc.options, key, fc.options[key])
        monkeypatch.setitem(fc.defaults, key, fc.defaults[key])
        monkeypatch.setitem(fc.defaults.current_defaults, key, fc.defaults.current_defaults[key])
    monkeypatch.setattr(fc.preferencesUiManager, 'data_path', str(tmp_path))
    tool = start(fc, 'colors-' + stage, OLD_SHAPES, [box(50, 0, 52, 2)])
    made = []
    ready = threading.Event()
    pool = ThreadPool(1)
    pool.apply_async(ready.wait)
    if stage != 'pending':
        ready.set()

    def collection(self, **kwargs):
        coll = ShapeCollection(pool=pool, fcoptions={'global_backface_culling': True}, **kwargs)
        made.append(coll)
        return coll

    monkeypatch.setattr(type(fc.plotcanvas), 'new_shape_collection', collection)
    try:
        tool.on_compare()
        if stage != 'queued':
            jobs.run()
            pump(150)
            assert len(made) == 2
        if stage == 'hidden':
            tool.ui.removed_cb.setChecked(False)
            tool.ui.added_cb.setChecked(False)
            pump()

        for kind, rgb in (('removed', '#123456'), ('added', '#abcdef')):
            monkeypatch.setattr(QtWidgets.QColorDialog, 'getColor', lambda *a, rgb=rgb, **kw: QtGui.QColor(rgb))
            QtTest.QTest.mouseClick(getattr(tool.ui, kind + '_swatch'), QtCore.Qt.MouseButton.LeftButton)
        saved_file = tmp_path / 'current_defaults.FlatConfig'
        assert saved_file.exists(), 'Clicking a color swatch must save the selected color'
        saved = json.loads(saved_file.read_text())
        assert saved[keys[0]] == '#123456FF'
        assert saved[keys[1]] == '#abcdefFF'

        ready.set()
        pool.close()
        pool.join()
        jobs.run()
        pump(150)
        if stage == 'hidden':
            assert all(not d['visible'] for c in made for d in c.data.values())
            tool.ui.removed_cb.setChecked(True)
            tool.ui.added_cb.setChecked(True)
        pump(150)

        def assert_colors(collections):
            assert len(collections) == 2
            for coll in collections:
                for data in coll.data.values():
                    vertices = data['mesh_vertices'] if len(data['mesh_vertices']) else data['line_pts']
                    rgb = '#abcdef' if vertices[:, 0].min() > 25 else '#123456'
                    if len(data['mesh_tris']):
                        assert data['face_color'] == rgb + '60'
                    if len(data['line_pts']):
                        assert data['color'] == rgb + 'FF'

        assert_colors(made)
        tool.set_tool_ui()
        assert '#123456' in tool.ui.removed_swatch.styleSheet()
        assert '#abcdef' in tool.ui.added_swatch.styleSheet()
        tool.ui.old_combo.setCurrentText('colors-' + stage + '-old')
        tool.ui.new_combo.setCurrentText('colors-' + stage + '-new')
        # The pool is closed; the renderer's synchronous fallback still uses the saved colors.
        made.clear()
        tool.on_compare()
        jobs.run()
        pump(150)
        assert_colors(made)
    finally:
        ready.set()
        pool.close()
        pool.join()
        tool.on_plugin_cleanup()


def test_cancel_color_picker_keeps_the_comparison_and_settings(fc, canvas, jobs, monkeypatch, tmp_path):
    from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

    monkeypatch.setattr(fc.preferencesUiManager, 'data_path', str(tmp_path))
    tool = start(fc, 'cancel-color', OLD_SHAPES, NEW_SHAPES)
    tool.on_compare()
    jobs.run()
    pump(150)
    before = tool.ui.summary_label.text()
    color = fc.options['tools_compare_added_color']
    monkeypatch.setattr(QtWidgets.QColorDialog, 'getColor', lambda *a, **kw: QtGui.QColor())
    QtTest.QTest.mouseClick(tool.ui.added_swatch, QtCore.Qt.MouseButton.LeftButton)
    pump()
    assert fc.options['tools_compare_added_color'] == color
    assert tool.ui.summary_label.text() == before
    assert all(c.enabled for c in canvas.made if c.batches)
    assert not (tmp_path / 'current_defaults.FlatConfig').exists()
