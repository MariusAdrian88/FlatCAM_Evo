"""App Undo / Redo history on the in-place changes of the plugins and of the object handlers (headless App)."""

import os
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6 import QtWidgets
from shapely import LineString, Point, box
from shapely.ops import unary_union

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import appHandlers.appHistory as appHistory
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.ExcellonObject import ExcellonObject
from appPlugins.ToolCutOut import CutOut
from appPlugins.ToolDblSided import DblSidedTool
from appPlugins.ToolIsolation import ToolIsolation

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'unify')

pytestmark = pytest.mark.slow


@pytest.fixture(scope='module')
def fc():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app = App(qapp=qapp, headless=True)
    # the history records nothing in headless mode; these tests need it
    appHistory.RECORD_IN_HEADLESS = True
    yield app
    appHistory.RECORD_IN_HEADLESS = False


@pytest.fixture
def no_plot(monkeypatch):
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    monkeypatch.setattr(FlatCAMObj, 'plot_transformed', lambda *a, **k: True)
    monkeypatch.setattr(ExcellonObject, 'plot_transformed', lambda *a, **k: True)
    for _ in range(20):
        QtWidgets.QApplication.processEvents()


def _load(fc, kind, filename, name):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, filename))
        if kind == 'excellon':
            obj.create_geometry()
    return fc.app_obj.new_object(kind, name, init, plot=False)


def _settle(fc, timeout=5.0):
    end = time.time() + timeout
    while fc.projects.is_busy() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def _bounds(obj):
    return np.array(obj.bounds())


def _area(obj):
    geo = obj.solid_geometry
    return unary_union(geo if isinstance(geo, list) else [geo]).area


def _geometry(fc, name, multigeo=False):
    """A Geometry object with a closed contour (a 20 x 10 rectangle) and 1 tool."""
    contour = LineString(list(box(0, 0, 20, 10).exterior.coords))

    def init(obj, _app):
        obj.solid_geometry = [contour]
        obj.tools = {1: {'tooldia': 1.0, 'data': {'name': name}, 'solid_geometry': [contour] if multigeo else []}}
        obj.multigeo = multigeo
    return fc.app_obj.new_object('geometry', name, init, plot=False)


def _one_step(fc, before, label_part):
    """The history got exactly 1 new step since `before` (the undo stack length), with `label_part` in its label."""
    assert len(fc.history.undo_stack) == before + 1
    assert label_part in fc.history.undo_label()


def _stack(fc):
    return len(fc.history.undo_stack)


# ---------------------------------------------------------------------------------------------------------------------
# 1. Double-Sided: Mirror
# ---------------------------------------------------------------------------------------------------------------------
def _hole_xy(points):
    return np.asarray([p.coords[0] for p in points])


def _nearest(points, others):
    return np.linalg.norm(points[:, None] - others[None, :], axis=2).min(axis=1)


def _canonical(holes, turn):
    """Holes of a T-B board seen as an L-R board (x and y exchanged)."""
    return holes[:, ::-1] if turn == 'T-B' else holes


@pytest.mark.parametrize('turn', ['L-R', 'T-B'])
def test_dblsided_failsafe_holes_fit_only_the_turn_direction(turn):
    from appPlugins.ToolDblSided import DblSidedGen as gen
    bounds = (0, 0, 100, 60) if turn == 'L-R' else (0, 0, 60, 100)
    holes = _hole_xy(gen.bounds_drills(bounds, 1.0, 2, turn))
    assert len(holes) == 3
    cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    along, across = ((-1, 1), (1, -1)) if turn == 'L-R' else ((1, -1), (-1, 1))
    assert _nearest(holes * along + (2 * cx * (along[0] < 0), 2 * cy * (along[1] < 0)), holes) == \
        pytest.approx(0, abs=1e-9)
    assert _nearest(holes * across + (2 * cx * (across[0] < 0), 2 * cy * (across[1] < 0)), holes).max() > 1
    plain = _hole_xy(gen.bounds_drills(bounds, 1.0, 2, turn, failsafe=False))
    assert len(plain) == 2
    assert _nearest(plain * along + (2 * cx * (along[0] < 0), 2 * cy * (along[1] < 0)), plain) == \
        pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize('turn', ['L-R', 'T-B'])
def test_dblsided_failsafe_holes_stay_keyed_for_a_near_equilateral_board(turn):
    from appPlugins.ToolDblSided import DblSidedGen as gen
    o, dia = 2, 1.0
    bounds = (0, 0, 40, 72.2) if turn == 'L-R' else (0, 0, 72.2, 40)
    holes = _canonical(_hole_xy(gen.bounds_drills(bounds, dia, o, turn)), turn)
    a, b, c = holes
    assert c == pytest.approx((20, 72.2 + o))
    assert a[1] == pytest.approx(b[1]) and a[1] < 36.1
    assert holes[:, 0].min() >= -o - 1e-9 and holes[:, 0].max() <= 40 + o + 1e-9
    assert holes[:, 1].min() >= 0 and holes[:, 1].max() <= 72.2 + o + 1e-9
    assert abs(np.linalg.norm(c - a) - np.linalg.norm(b - a)) >= 2 * dia - 1e-9
    assert _nearest(holes * (1, -1) + (0, 72.2), holes).max() >= 2 * dia
    with pytest.raises(ValueError, match='room'):
        gen.bounds_drills((0, 0, 2, 2), dia, o, turn)
    assert len(gen.bounds_drills((0, 0, 2, 2), dia, o, turn, failsafe=False)) == 2


@pytest.mark.parametrize('bounds,diameter,offset', [
    ((0, 0, 10, 10), 1.0, 0.4), ((0, 0, 10, 10), 0, 2), ((0, 0, 10, 10), 1.0, -1),
    ((0, 0, float('inf'), 10), 1.0, 2)])
def test_dblsided_keyed_holes_refuse_bad_input(bounds, diameter, offset):
    from appPlugins.ToolDblSided import DblSidedGen as gen
    with pytest.raises(ValueError):
        gen.bounds_drills(bounds, diameter, offset)


def test_dblsided_generated_holes_preserve_coincident_user_drills(fc, no_plot):
    from appPlugins.ToolDblSided import DblSidedGen as gen

    def init(obj, app):
        obj.add_tool(1, drills=[Point(-2, 5), Point(4, 4)], slots=[(Point(1, 1), Point(2, 1))])
        obj.create_geometry()
    exc = fc.app_obj.new_object('excellon', 'dbl_append', init, plot=False)
    original = _bounds(exc)
    with fc.history.snapshot('Alignment Drills', [exc]):
        gen.replace_generated(exc, 1.00001, [Point(-2, 5), Point(22, 5)], fc.decimals)
    assert len(exc.tools) == 1
    assert len(exc.tools[1]['drills']) == 4
    assert len(exc.tools[1]['slots']) == 1
    assert gen.source_view(exc).bounds() == pytest.approx(original)
    matrix = (-1, 0, 0, 1, 20, 0)
    with fc.history.snapshot('Mirror', [exc]):
        appHistory.apply_matrix_to_object(exc, matrix)
        gen.replace_generated(exc, 1, [Point(-3, 5), Point(23, 5)], fc.decimals)
    assert len(exc.tools[1]['drills']) == 4
    assert exc.tools[1]['drills'][0].equals(Point(22, 5))
    assert fc.history.undo()
    assert exc.tools[1]['drills'][0].equals(Point(-2, 5))
    assert fc.history.redo()
    assert exc.tools[1]['drills'][0].equals(Point(22, 5))
    # Project/history round-trip retains ownership independently of coordinates.
    state = appHistory.object_state(exc)
    appHistory.restore_object_state(exc, state)
    gen.replace_generated(exc, 2, [Point(50, 50)], fc.decimals)
    assert len(exc.tools[1]['drills']) == 2
    assert len(exc.tools[1]['slots']) == 1
    assert sum(len(t['drills']) for t in exc.tools.values()) == 3
    assert exc.source_file


def _drill_count(obj):
    return sum(len(tool.get('drills') or ()) for tool in obj.tools.values())


def _dbl_editor_save(drills, prepare=None):
    """An Excellon editor round trip, committed with the tool reconstruction of the real save worker."""
    from unittest.mock import Mock
    from test_exc_editor_defects import qt_editor
    from appEditors.appExcEditor import tools_with_data

    _, editor = qt_editor(drills.units)
    editor.edit_fcexcellon(drills)
    if prepare is not None:
        prepare(editor)
    editor._save_excellon_in_place = Mock()
    editor.update_fcexcellon(drills, in_place=True)
    drills.tools = tools_with_data(drills.tools, editor.new_tools, editor.olddia_newdia,
                                   editor.data_defaults, editor.decimals)
    drills.create_geometry()
    drills.regenerate_source()
    return editor


def test_dblsided_editor_round_trip_keeps_generated_holes_owned(fc, no_plot):
    from appPlugins.ToolDblSided import DblSidedGen as gen

    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_editor')
    tool.ui.auto_align_cb.set_value(False)
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    tool.on_process()
    assert _drill_count(drills) == 43
    user = pads + (7, -3)
    holes = _hole_xy([p for tool_dict in drills.tools.values() for p in tool_dict['drills']])
    generated = [p for p in holes if _nearest(np.asarray([p]), user)[0] > 1e-6]
    assert len(generated) == 3

    # A user drill exactly on one of our holes is a separate feature; a save must keep both.
    next(iter(drills.tools.values()))['drills'].append(Point(*generated[0]))
    drills.create_geometry()
    assert drills.regenerate_source()
    assert _drill_count(drills) == 44
    user = np.vstack([user, generated[0]])

    _dbl_editor_save(drills)
    tool.on_process()
    holes = _hole_xy([p for tool_dict in drills.tools.values() for p in tool_dict['drills']])
    assert len(holes) == 44
    # only our 3 holes are regenerated; the 41 user drills (1 of them coincident) are kept
    assert _drill_count(gen.source_view(drills)) == 41
    assert _nearest(user, holes) == pytest.approx(np.zeros(len(user)))
    generated = [p for p in holes if _nearest(np.asarray([p]), user)[0] > 1e-6]
    assert len(generated) == 3

    # Deleting 1 generated hole renumbers the others and moving another must not slide the ownership
    # onto a user drill.
    def remove_and_move(editor):
        storage = next(iter(editor.storage_dict.values()))
        removed = next(shape for shape in storage.get_objects()
                       if np.allclose(shape.geo.centroid.coords[0], generated[1]))
        storage.replace(removed=[removed], inserted=[])
        moved_shape = next(shape for shape in storage.get_objects()
                           if np.allclose(shape.geo.centroid.coords[0], generated[2]))
        editor.selected = [moved_shape]
        assert editor.transform_features('rotate', 90) != 'fail'

    editor = _dbl_editor_save(drills, remove_and_move)
    moved = editor.selected[0].geo.centroid.coords[0]
    tool.on_process()
    holes = _hole_xy([p for tool_dict in drills.tools.values() for p in tool_dict['drills']])
    assert len(holes) == 44
    assert not any(np.allclose(hole, moved) for hole in holes)
    assert _nearest(user, holes) == pytest.approx(np.zeros(len(user)))
    assert _drill_count(gen.source_view(drills)) == 41


def test_dblsided_failed_separate_generation_restores_retired_holes(fc, no_plot, monkeypatch):
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_switch_fail')
    tool.ui.auto_align_cb.set_value(False)
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    tool.on_process()
    assert _drill_count(drills) == 43

    tool.ui.append_drills_cb.set_value(False)
    before = appHistory.object_state(drills)
    collection = list(fc.collection.get_list())
    monkeypatch.setattr(DblSidedTool, 'new_object', lambda *args, **kwargs: None)
    tool.on_process()
    assert appHistory.states_equal(before, appHistory.object_state(drills))
    assert fc.collection.get_list() == collection


@pytest.mark.parametrize('addition', ['none', 'slot'])
def test_dblsided_switching_back_to_append_retires_the_separate_output(fc, no_plot, addition):
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_switch_back_' + addition)
    tool.ui.auto_align_cb.set_value(False)
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    tool.on_process()
    before = list(fc.collection.get_list())
    tool.ui.append_drills_cb.set_value(False)
    tool.ui.offset_entry.set_value(4)
    tool.on_process()
    output = next(obj for obj in fc.collection.get_list() if obj not in before)
    uid = output.history_uid
    assert _drill_count(output) == 3 and _drill_count(drills) == 40

    # A user feature of the output belongs to the object, not to the plugin.
    added_drills = int(addition == 'drill')
    added_slots = int(addition == 'slot')
    if added_drills or added_slots:
        values = next(iter(output.tools.values()))
        if added_drills:
            values['drills'].append(Point(-60, -60))
        else:
            values['slots'].append((Point(-60, -60), Point(-50, -60)))
        output.create_geometry()
        assert output.regenerate_source()

    # Back to append: only our holes leave the output, the user feature stays with it.
    tool.ui.append_drills_cb.set_value(True)
    tool.on_process()
    assert _drill_count(drills) == 43
    if not added_drills and not added_slots:
        # it held only our holes, so it has nothing left to drill
        assert fc.history.find(uid) is None
    else:
        assert fc.history.find(uid) is output
        assert _drill_count(output) == added_drills
        assert sum(len(t.get('slots') or ()) for t in output.tools.values()) == added_slots
        # the exported text is the retained user geometry, not the retired holes
        assert output.source_file
        assert sum(line.startswith('X') for line in output.source_file.splitlines()) == added_drills
        assert sum(line.startswith('G00X') for line in output.source_file.splitlines()) == added_slots

    # One undo restores both outputs, one redo retires the output holes again.
    _settle(fc)
    assert fc.history.undo()
    assert _drill_count(drills) == 40
    if not added_drills and not added_slots:
        restored = fc.history.find(uid)
        assert restored is not None and _drill_count(restored) == 3
    else:
        assert _drill_count(output) == 3 + added_drills
        assert sum(len(t.get('slots') or ()) for t in output.tools.values()) == added_slots
    _settle(fc)
    assert fc.history.redo()
    assert _drill_count(drills) == 43
    if not added_drills and not added_slots:
        assert fc.history.find(uid) is None
    else:
        assert _drill_count(output) == added_drills
        assert sum(len(t.get('slots') or ()) for t in output.tools.values()) == added_slots


@pytest.mark.parametrize('diameter,holes', [(0, '(1,2)'), (1, '(1,2,3)'), (1, 'bad(')])
def test_dblsided_invalid_holes_do_not_create(diameter, holes):
    from unittest.mock import Mock
    tool = Mock()
    tool.ui.align_type_radio.get_value.return_value = 'manual'
    tool.ui.drill_dia.get_value.return_value = diameter
    tool.ui.alignment_holes.text.return_value = holes
    DblSidedTool.on_create_alignment_holes(tool)
    tool.new_object.assert_not_called()


@pytest.mark.parametrize('kind', ['gerber', 'excellon'])
def test_dblsided_advanced_mirror_preserves_source_and_undo(fc, no_plot, monkeypatch, kind):
    if kind == 'gerber':
        grb = _load(fc, 'gerber', 'unify_mixed.gbr', 'g_dbl')
    else:
        def init(obj, app):
            obj.add_tool(1, drills=[Point(1, 2)])
            obj.create_geometry()
        grb = fc.app_obj.new_object('excellon', 'exc_dbl', init, plot=False)
    grb.regenerate_source()
    source0 = grb.source_file
    b0 = _bounds(grb)
    app = SimpleNamespace(
        history=fc.history,
        collection=SimpleNamespace(index=lambda *a: SimpleNamespace(internalPointer=lambda: SimpleNamespace(obj=grb))),
        app_obj=SimpleNamespace(object_changed=SimpleNamespace(emit=lambda o: None)),
        inform=SimpleNamespace(emit=lambda *a: None))
    ui = SimpleNamespace(
        object_combo=SimpleNamespace(currentIndex=lambda: 0, rootModelIndex=lambda: None),
        mirror_axis=SimpleNamespace(get_value=lambda: 'X'),
        axis_location=SimpleNamespace(get_value=lambda: 'point'),
        point_entry=SimpleNamespace(get_value=lambda: (3.0, 4.0)))
    stub = SimpleNamespace(app=app, ui=ui)
    n = _stack(fc)
    DblSidedTool.on_mirror(stub)
    _one_step(fc, n, 'Mirror')
    b1 = _bounds(grb)
    assert not np.allclose(b1, b0)
    source1 = grb.source_file
    assert source1 != source0
    assert source1 == grb.regenerate_source(store=False)
    assert fc.history.undo() and np.allclose(_bounds(grb), b0)
    assert grb.source_file == source0
    assert fc.history.redo() and np.allclose(_bounds(grb), b1)
    assert grb.source_file == source1
    before = appHistory.object_state(grb)
    monkeypatch.setattr(grb, 'regenerate_source', lambda: False)
    n = _stack(fc)
    DblSidedTool.on_mirror(stub)
    after = appHistory.object_state(grb)
    # ApertureMacro has identity equality. Compare its project representation, not copied instance identities.
    import json
    from camlib import to_dict
    assert json.loads(json.dumps(before, default=to_dict)) == json.loads(json.dumps(after, default=to_dict))
    assert _stack(fc) == n


def test_dblsided_alignment_stages_all_sources_and_rejects_ambiguity():
    from appPlugins.ToolDblSided import DblSidedGen as gen
    from appPlugins.ToolAlignObjects.AlignGen import SnapIndex, AlignError, apply_matrix
    rng = np.random.default_rng(42)
    pads = rng.uniform((0, 0), (50, 30), (40, 2))

    def index(points):
        return SnapIndex([Point(p).buffer(0.8) for p in points], points, [])

    angle = 0.3
    matrix = (np.cos(angle), -np.sin(angle), np.sin(angle), np.cos(angle), 7, -3)
    holes = apply_matrix(matrix, pads)
    bottom = pads + (12, 5)
    plans = gen.alignment_matrices([index(pads), index(bottom), index(holes)], [0, 1], 2)
    assert apply_matrix(plans[2], holes) == pytest.approx(pads, abs=0.01)
    assert apply_matrix(plans[1], bottom) == pytest.approx(pads, abs=0.01)
    assert 0 not in plans
    with pytest.raises(AlignError):
        gen.alignment_matrices([index(pads), index([(0, 0)])], [0], 1)
    square = index([(0, 0), (10, 0), (10, 10), (0, 10)])
    with pytest.raises(AlignError, match='ambiguous'):
        gen.alignment_matrices([square, square], [0], 1)


def _dbl_sources(fc, name, reflected=False):
    pads = np.random.default_rng(42).uniform((0, 0), (50, 30), (40, 2))

    def gerber(points, suffix):
        def init(obj, app):
            records = [{'solid': Point(p).buffer(0.8), 'follow': Point(p)} for p in points]
            obj.tools = {10: {'type': 'C', 'size': 1.6, 'geometry': records}}
            obj.solid_geometry = [r['solid'] for r in records]
            obj.follow_geometry = [r['follow'] for r in records]
        return fc.app_obj.new_object('gerber', name + suffix, init, plot=False)

    top = gerber(pads, '_top')
    if reflected:
        from appPlugins.ToolAlignObjects.AlignGen import apply_matrix
        angle = 0.3
        bottom_points = apply_matrix((np.cos(angle), -np.sin(angle), np.sin(angle), np.cos(angle), 60, 8),
                                     pads * (-1, 1))
    else:
        bottom_points = pads + (12, 5)
    bottom = gerber(bottom_points, '_bottom')

    def init_drills(obj, app):
        obj.add_tool(0.8, drills=[Point(p) for p in pads + (7, -3)])
        obj.create_geometry()
    drills = fc.app_obj.new_object('excellon', name + '_drills', init_drills, plot=False)
    tool = DblSidedTool(fc)
    tool.set_tool_ui()
    tool.change_level('b')
    for combo, obj in zip(tool.ui.basic_combos, (top, bottom, drills)):
        combo.set_value(obj.obj_options['name'])
    return tool, top, bottom, drills, pads


def _wait_dbl(tool):
    end = time.monotonic() + 10
    while tool.pending_alignment is not None and time.monotonic() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    assert tool.pending_alignment is None


@pytest.mark.parametrize('auto', [False, True])
def test_dblsided_kicad_flip_preserves_registered_flash_centers(fc, no_plot, auto):
    from appPlugins.ToolAlignObjects.AlignGen import build_snap_index
    fc.history.clear()
    objects = []
    for filename in ['usb_isolator-F_Cu.gtl', 'usb_isolator-B_Cu.gbl']:
        def init(obj, app, filename=filename):
            obj.parse_file(os.path.join(os.path.dirname(__file__), 'fixtures', 'kicad', 'Gerbers_2', filename))
        objects.append(fc.app_obj.new_object('gerber', 'kicad_flip_' + filename, init, plot=False))
    original = [build_snap_index(obj).centers.copy() for obj in objects]
    # These asymmetric layers share exact features, not copper bounding boxes.
    distances = np.linalg.norm(original[1][:, None] - original[0][None, :], axis=2).min(axis=1)
    assert np.count_nonzero(distances < 1e-8) == 38
    xmin = min(obj.bounds()[0] for obj in objects)
    xmax = max(obj.bounds()[2] for obj in objects)
    expected = original[1].copy()
    expected[:, 0] = xmin + xmax - expected[:, 0]
    tool = DblSidedTool(fc)
    tool.set_tool_ui()
    tool.change_level('b')
    for combo, obj in zip(tool.ui.basic_combos, objects):
        combo.set_value(obj.obj_options['name'])
    tool.ui.generate_cb.set_value(False)
    tool.ui.auto_align_cb.set_value(auto)
    before = _stack(fc)
    tool.on_process()
    _wait_dbl(tool)
    _one_step(fc, before, '2-Sided')
    np.testing.assert_allclose(build_snap_index(objects[0]).centers, original[0], atol=1e-8, rtol=0)
    np.testing.assert_allclose(build_snap_index(objects[1]).centers, expected, atol=1e-8, rtol=0)
    sources = [obj.source_file for obj in objects]
    tool.on_process()
    _wait_dbl(tool)
    assert [obj.source_file for obj in objects] == sources
    assert _stack(fc) == before + 1
    assert fc.history.undo()
    np.testing.assert_allclose(build_snap_index(objects[1]).centers, original[1], atol=1e-8, rtol=0)
    tool.deleteLater()


@pytest.mark.parametrize('composed', [False, True])
def test_dblsided_registered_bounds_ignore_cleared_copper(fc, no_plot, composed):
    from appPlugins.ToolDblSided.DblSidedGen import bounds_drills
    from appPlugins.ToolAlignObjects.AlignGen import apply_matrix
    fc.history.clear()
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_clear_' + str(composed))
    removed = box(100, 5, 110, 15)
    records = bottom.tools[10]['geometry']
    records.extend([{'solid': removed, 'follow': removed.centroid}, {'clear': removed}])
    copper = unary_union([r['solid'] for r in records if 'solid' in r]).difference(removed)
    bottom.solid_geometry = copper if composed else list(copper.geoms)
    bounds = top.bounds()
    flip = (-1, 0, 0, 1, bounds[0] + bounds[2], 0)
    mirrored = apply_matrix(flip, pads)
    holes = _hole_xy(bounds_drills(bounds, 0.8, 2))
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    for _ in range(2):
        tool.on_process()
        _wait_dbl(tool)
        for obj, expected in ((top, pads), (bottom, mirrored)):
            assert np.asarray([r['follow'].coords[0] for r in obj.tools[10]['geometry'][:40]]) == \
                pytest.approx(expected)
            assert obj.bounds() == pytest.approx(bounds)
        actual = np.asarray([p.coords[0] for t in drills.tools.values() for p in t['drills']])
        assert actual.shape == (43, 2)
        assert actual[:40] == pytest.approx(pads)
        assert actual[40:] == pytest.approx(holes)
    tool.deleteLater()


def test_dblsided_process_is_one_undo_and_survives_reload(fc, no_plot):
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_flip', reflected=True)
    xmin, ymin, xmax, ymax = drills.bounds()
    appHistory.apply_matrix_to_object(drills, (-1, 0, 0, 1, xmin + xmax, 0))
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    originals = [_bounds(obj) for obj in (top, bottom, drills)]
    expected = pads.copy()
    expected[:, 0] = _bounds(top)[0] + _bounds(top)[2] - expected[:, 0]
    n = _stack(fc)
    tool.on_process()
    _wait_dbl(tool)
    _one_step(fc, n, '2-Sided')
    assert np.asarray([rec['follow'].coords[0] for rec in top.tools[10]['geometry']]) == pytest.approx(pads)
    assert np.asarray([p.coords[0] for p in drills.tools[1]['drills'][:40]]) == pytest.approx(pads)
    assert np.asarray([rec['follow'].coords[0] for rec in bottom.tools[10]['geometry']]) == pytest.approx(expected)
    assert len(drills.tools[1]['drills']) == 43
    assert fc.history.undo()
    assert len(drills.tools[1]['drills']) == 40
    for obj, original in zip((top, bottom, drills), originals):
        assert _bounds(obj) == pytest.approx(original)
    assert fc.history.redo()
    assert np.asarray([rec['follow'].coords[0] for rec in bottom.tools[10]['geometry']]) == pytest.approx(expected)
    holes = np.asarray([p.coords[0] for p in drills.tools[1]['drills'][40:]])
    sources = [obj.source_file for obj in (top, bottom, drills)]
    import json
    from camlib import to_dict, dict2obj
    for obj in (top, bottom, drills):
        state = json.loads(json.dumps(appHistory.object_state(obj), default=to_dict), object_hook=dict2obj)
        appHistory.restore_object_state(obj, state)
    tool.set_tool_ui()
    for combo, obj in zip(tool.ui.basic_combos, (top, bottom, drills)):
        combo.set_value(obj.obj_options['name'])
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    tool.on_process()
    _wait_dbl(tool)
    assert np.asarray([rec['follow'].coords[0] for t in bottom.tools.values() for rec in t['geometry']]) == \
        pytest.approx(expected)
    restored_drills = [p.coords[0] for t in drills.tools.values() for p in t['drills']]
    assert len(restored_drills) == 43
    assert np.asarray(restored_drills[40:]) == pytest.approx(holes)
    assert [obj.source_file for obj in (top, bottom, drills)] == sources
    bottom.offset((4, -7))
    tool.on_process()
    _wait_dbl(tool)
    # External edits invalidate the manufacturing marker: re-register the actual input, then mirror it once.
    assert np.asarray([rec['follow'].coords[0] for t in bottom.tools.values() for rec in t['geometry']]) == \
        pytest.approx(expected)
    tool.deleteLater()


def test_dblsided_failed_append_export_rolls_back(fc, no_plot, monkeypatch):
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_export')
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.auto_align_cb.set_value(False)
    before = [appHistory.object_state(obj) for obj in (top, bottom, drills)]
    n = _stack(fc)
    monkeypatch.setattr(drills, 'regenerate_source', lambda: False)
    tool.on_process()
    assert all(appHistory.states_equal(state, appHistory.object_state(obj))
               for obj, state in zip((top, bottom, drills), before))
    assert _stack(fc) == n
    assert tool.ui.process_button.isEnabled()
    tool.deleteLater()


def test_dblsided_failed_source_export_does_not_publish_drills(fc, no_plot, monkeypatch):
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_export_separate')
    tool.ui.generate_cb.set_value(True)
    before = [appHistory.object_state(obj) for obj in (top, bottom, drills)]
    collection = list(fc.collection.get_list())
    n = _stack(fc)
    monkeypatch.setattr(bottom, 'regenerate_source', lambda: False)
    tool.on_process()
    _wait_dbl(tool)
    assert fc.collection.get_list() == collection
    assert _stack(fc) == n
    assert all(appHistory.states_equal(state, appHistory.object_state(obj))
               for obj, state in zip((top, bottom, drills), before))
    tool.deleteLater()


@pytest.mark.parametrize('auto,append', [(False, False), (True, True)])
def test_dblsided_holes_use_registered_frozen_frame(fc, no_plot, append, auto):
    from appPlugins.ToolDblSided.DblSidedGen import combined_bounds, bounds_drills
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_frozen')
    bounds = top.bounds() if auto else combined_bounds([top, bottom, drills])
    points = np.asarray([p.coords[0] for p in bounds_drills(bounds, 0.8, 2)])
    tool.ui.generate_cb.set_value(True)
    tool.ui.auto_align_cb.set_value(auto)
    tool.ui.append_drills_cb.set_value(append)
    tool.ui.basic_drill_dia.set_value(0.8)
    collection = list(fc.collection.get_list())
    tool.on_process()
    _wait_dbl(tool)
    output = drills if append else next(o for o in fc.collection.get_list() if o not in collection)
    actual = np.asarray([p.coords[0] for t in output.tools.values() for p in t['drills']])[-3:]
    assert actual == pytest.approx(points)
    assert np.asarray([p.coords[0] for p in drills.tools[1]['drills'][:40]]) == \
        pytest.approx(pads if auto else pads + (7, -3))
    tool.on_process()
    _wait_dbl(tool)
    assert np.asarray([p.coords[0] for t in output.tools.values() for p in t['drills']])[-3:] == pytest.approx(actual)
    assert len(drills.tools[1]['drills']) == (43 if append else 40)
    tool.deleteLater()


def test_dblsided_failed_alignment_does_not_mutate(fc, no_plot, monkeypatch):
    import appPlugins.ToolDblSided.DblSided as controller
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_reject')
    before = [appHistory.object_state(obj) for obj in (top, bottom, drills)]

    def reject(*args):
        raise ValueError('Ambiguous alignment; inspect in Align Objects.')
    monkeypatch.setattr(controller, 'alignment_matrices', reject)
    n = _stack(fc)
    tool.ui.generate_cb.set_value(True)
    objects = list(fc.collection.get_list())
    tool.on_process()
    _wait_dbl(tool)
    assert fc.collection.get_list() == objects
    assert all(appHistory.states_equal(state, appHistory.object_state(obj))
               for obj, state in zip((top, bottom, drills), before))
    assert _stack(fc) == n
    assert tool.ui.process_button.isEnabled()
    tool.deleteLater()


@pytest.mark.parametrize('change', ['reset', 'selection', 'geometry'])
def test_dblsided_stale_worker_does_not_move_or_generate(fc, no_plot, monkeypatch, change):
    import threading
    import appPlugins.ToolDblSided.DblSided as controller
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_stale_' + change)
    tool.ui.generate_cb.set_value(True)
    entered, release = threading.Event(), threading.Event()
    finished = []
    tool.alignment_finished.connect(lambda *args: finished.append(True))

    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return {1: (1, 0, 0, 1, 500, 500)}
    monkeypatch.setattr(controller, 'alignment_matrices', delayed)
    before = _bounds(bottom)
    objects = list(fc.collection.get_list())
    tool.on_process()
    try:
        assert entered.wait(5)
        assert _bounds(bottom) == pytest.approx(before)
        if change == 'reset':
            tool.set_tool_ui()
        elif change == 'selection':
            tool.ui.top_combo.setCurrentIndex(0)
        else:
            appHistory.apply_matrix_to_object(top, (1, 0, 0, 1, 1, 0))
    finally:
        release.set()
    end = time.monotonic() + 5
    while not finished and time.monotonic() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    assert finished
    assert _bounds(bottom) == pytest.approx(before)
    assert fc.collection.get_list() == objects
    tool.deleteLater()


def test_dblsided_alignment_drills_use_box_reference_group(fc):
    from unittest.mock import Mock
    from appParsers.ParseExcellon import Excellon
    output = Excellon(fc)
    output.regenerate_source = Mock()
    box_obj = SimpleNamespace(bounds=lambda: (0, 10, 20, 30))
    wrong_obj = SimpleNamespace(bounds=lambda: (100, 100, 110, 110))
    tool = Mock()
    tool.ui.align_type_radio.get_value.return_value = 'X'
    tool.ui.axis_location.get_value.return_value = 'box'
    tool.ui.box_combo.rootModelIndex.return_value = 'box'
    tool.ui.drill_dia.get_value.return_value = 1
    tool.ui.alignment_holes.text.return_value = '(1,2)'
    tool.app.collection.index.side_effect = lambda row, col, root: SimpleNamespace(
        internalPointer=lambda: SimpleNamespace(obj=box_obj if root == 'box' else wrong_obj))

    def create(kind, name, init, **kwargs):
        init(output, fc)
        return output
    tool.new_object.side_effect = create
    DblSidedTool.on_create_alignment_holes(tool)
    assert [p.coords[0] for p in output.tools[1]['drills']] == [(1, 2), (1, 38)]


@pytest.mark.parametrize('mirror_axis,holes,warned', [('Y', '(1,2)', True), ('X', '(1,2)', False),
                                                      ('Y', '(3,2)', False)])
def test_dblsided_warns_when_the_pins_do_not_fit_after_the_flip(mirror_axis, holes, warned):
    from unittest.mock import Mock
    tool = Mock(decimals=4)
    tool.ui.align_type_radio.get_value.return_value = 'X'
    tool.ui.mirror_axis.get_value.return_value = mirror_axis
    tool.ui.axis_location.get_value.return_value = 'point'
    tool.ui.point_entry.get_value.return_value = (3.0, 4.0)
    tool.ui.drill_dia.get_value.return_value = 1
    tool.ui.alignment_holes.text.return_value = holes
    DblSidedTool.on_create_alignment_holes(tool)
    messages = [c.args[0] for c in tool.app.inform.emit.call_args_list]
    # the holes are made in each case; a hole on the Mirror axis (x = 3) fits whatever the Type is
    tool.new_object.assert_called_once()
    assert messages[0].startswith('[success]')
    assert any(m.startswith('[WARNING_NOTCL]') for m in messages) == warned


@pytest.mark.parametrize('failure', ['geometry', 'export'])
def test_dblsided_advanced_failed_generation_not_published(fc, no_plot, monkeypatch, failure):
    from unittest.mock import Mock
    tool = SimpleNamespace(app=fc, ui=Mock(), new_object=fc.app_obj.new_object)
    tool.ui.align_type_radio.get_value.return_value = 'manual'
    tool.ui.drill_dia.get_value.return_value = 1
    tool.ui.alignment_holes.text.return_value = '(1,2)'
    if failure == 'geometry':
        # Even if geometry exists, an explicit failure must prevent publication.
        original = ExcellonObject.create_geometry
        def fail(obj):
            original(obj)
            return 'fail'
        monkeypatch.setattr(ExcellonObject, 'create_geometry', fail)
    else:
        monkeypatch.setattr(ExcellonObject, 'regenerate_source', lambda obj: False)
    objects = list(fc.collection.get_list())
    n = _stack(fc)
    DblSidedTool.on_create_alignment_holes(tool)
    assert fc.collection.get_list() == objects
    assert _stack(fc) == n


@pytest.mark.parametrize('turn', ['L-R', 'T-B'])
def test_dblsided_bottom_is_mirrored_about_the_whole_bbox_with_keyed_holes(fc, no_plot, turn):
    from appPlugins.ToolDblSided.DblSidedGen import combined_bounds, bounds_drills
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_pivot')
    tool.ui.auto_align_cb.set_value(False)
    tool.ui.type_combo.setCurrentIndex(['L-R', 'T-B'].index(turn))
    tool.ui.generate_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    originals = {obj: _bounds(obj) for obj in (top, bottom, drills)}
    bounds = combined_bounds([top, bottom, drills])
    before = list(fc.collection.get_list())
    n = _stack(fc)
    tool.on_process()
    _settle(fc)
    _one_step(fc, n, '2-Sided')
    assert _bounds(top) == pytest.approx(originals[top])
    assert _bounds(drills) == pytest.approx(originals[drills])
    old = originals[bottom]
    if turn == 'L-R':
        mirrored_bounds = (bounds[0] + bounds[2] - old[2], old[1], bounds[0] + bounds[2] - old[0], old[3])
    else:
        mirrored_bounds = (old[0], bounds[1] + bounds[3] - old[3], old[2], bounds[1] + bounds[3] - old[1])
    assert _bounds(bottom) == pytest.approx(mirrored_bounds)
    output = next(o for o in fc.collection.get_list() if o not in before)
    expected = _hole_xy(bounds_drills(bounds, 0.8, 2, turn))
    assert _hole_xy([p for t in output.tools.values() for p in t['drills']]) == pytest.approx(expected)
    uid = output.history_uid
    assert fc.history.undo()
    for obj, original in originals.items():
        assert _bounds(obj) == pytest.approx(original)
    assert output not in fc.collection.get_list()
    assert fc.history.redo()
    _settle(fc)
    output = next(o for o in fc.collection.get_list() if o.history_uid == uid)
    mirrored = _bounds(bottom)
    state = [appHistory.object_state(obj) for obj in (top, bottom, drills, output)]
    tool.on_process()
    _settle(fc)
    assert _bounds(bottom) == pytest.approx(mirrored)
    assert all(appHistory.states_equal(s, appHistory.object_state(obj))
               for obj, s in zip((top, bottom, drills), state))
    assert _hole_xy([p for t in output.tools.values() for p in t['drills']]) == pytest.approx(expected)
    tool.deleteLater()


def test_dblsided_same_object_as_top_and_bottom_is_refused(fc, no_plot):
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_same')
    tool.ui.bottom_combo.set_value(top.obj_options['name'])
    tool.ui.generate_cb.set_value(True)
    before = [appHistory.object_state(obj) for obj in (top, bottom, drills)]
    objects, n = list(fc.collection.get_list()), _stack(fc)
    tool.on_process()
    assert fc.collection.get_list() == objects
    assert _stack(fc) == n
    assert all(appHistory.states_equal(state, appHistory.object_state(obj))
               for obj, state in zip((top, bottom, drills), before))
    tool.deleteLater()


def test_dblsided_macro_source_worker_is_not_falsely_stale(fc, no_plot, monkeypatch):
    import appPlugins.ToolDblSided.DblSided as controller
    grb = _load(fc, 'gerber', 'unify_mixed.gbr', 'dbl_macro')
    def init(obj, app):
        obj.add_tool(1, drills=[Point(1, 2), Point(3, 4)])
        obj.create_geometry()
    exc = fc.app_obj.new_object('excellon', 'dbl_macro_exc', init, plot=False)
    tool = DblSidedTool(fc)
    tool.set_tool_ui()
    tool.ui.top_combo.set_value(grb.obj_options['name'])
    tool.ui.drills_combo.set_value(exc.obj_options['name'])
    tool.ui.generate_cb.set_value(True)
    monkeypatch.setattr(controller, 'alignment_matrices', lambda *args: {1: (1, 0, 0, 1, 0, 0)})
    before = list(fc.collection.get_list())
    tool.on_process()
    _wait_dbl(tool)
    assert len(fc.collection.get_list()) == len(before) + 1
    tool.deleteLater()


def test_dblsided_frame_detects_lossless_geometry_and_macro_edits_after_reload():
    import json
    from camlib import ApertureMacro, dict2obj, to_dict
    macro = ApertureMacro('frame_macro')
    macro.raw = '1,1,1,0,0'
    obj = SimpleNamespace(tools={10: {'macro': macro, 'geometry': [{'solid': box(0, 0, 2, 3)}]}},
                          solid_geometry=[Point(0, 0)], obj_options={}, kind='gerber',
                          history_uid='signature-test', ser_attrs=['tools', 'solid_geometry'])
    tool = SimpleNamespace(_geometry_signature=lambda obj: DblSidedTool._geometry_signature(None, obj))
    matrix = [1, 0, 0, -1, 0, 3]
    signature = tool._geometry_signature(obj)
    obj.obj_options['_2sided_frame'] = {'signature': signature, 'matrix': matrix}
    source_signature = DblSidedTool._source_signature(None, obj)
    state = json.loads(json.dumps({'tools': obj.tools, 'solid_geometry': obj.solid_geometry,
                                  'obj_options': obj.obj_options}, default=to_dict), object_hook=dict2obj)
    obj.__dict__.update(state)
    assert DblSidedTool._presentation(tool, obj) == matrix
    assert DblSidedTool._source_signature(None, obj) == source_signature
    obj.tools['10']['macro'].raw += '*1,1,2,0,0'
    assert DblSidedTool._presentation(tool, obj) is None
    assert DblSidedTool._source_signature(None, obj) != source_signature
    obj.tools['10']['macro'].raw = macro.raw
    obj.solid_geometry = [Point(1e-20, 0)]
    obj.obj_options['_2sided_frame']['signature'] = tool._geometry_signature(obj)
    obj.solid_geometry = [Point(2e-20, 0)]
    assert DblSidedTool._presentation(tool, obj) is None


def _smd_sources(fc, name):
    from test_align_auto import partial_smd_board
    through, pads, holes, matrix = partial_smd_board()
    def init_pads(obj, app):
        records = [{'solid': solid, 'follow': Point(center)}
                   for solid, center in zip(pads.tree.geometries, pads.centers)]
        obj.tools = {10: {'type': 'C', 'size': 1.6, 'geometry': records[:len(through)]},
                     11: {'type': 'R', 'width': 0.2, 'height': 0.3, 'size': float(np.hypot(0.2, 0.3)),
                          'geometry': records[len(through):]}}
        obj.solid_geometry = [r['solid'] for r in records]
        obj.follow_geometry = [r['follow'] for r in records]
    def init_drills(obj, app):
        obj.add_tool(0.8, drills=[Point(p) for p in holes.centers])
        obj.create_geometry()
    grb = fc.app_obj.new_object('gerber', name + '_pads', init_pads, plot=False)
    exc = fc.app_obj.new_object('excellon', name + '_holes', init_drills, plot=False)
    return grb, exc, through, holes.centers.copy(), matrix


@pytest.mark.parametrize('reverse,limited', [(False, False), (True, True)])
def test_auto_align_smd_ui_preview_and_apply_follow_requested_motion(fc, no_plot, monkeypatch, reverse, limited):
    from appPlugins.ToolAlignObjects.Align import AlignObjects
    from appPlugins.ToolAlignObjects.AlignGen import apply_matrix
    grb, exc, through, holes, matrix = _smd_sources(fc, 'auto_smd_' + str(reverse))
    if limited:
        # Most spread landmarks are unmatched mounting holes. A nearby seed must still fit six
        # actual correspondences, not spend the solver budget on those unmatched landmarks.
        from appHandlers.appHistory import apply_matrix_to_object
        apply_matrix_to_object(exc, matrix)
        holes = apply_matrix(matrix, holes)
    tool = AlignObjects(fc)
    tool.set_tool_ui()
    tool.change_level('a')
    assert tool.ui.limit_pairs_entry.get_value() == 6
    assert not tool.ui.limit_pairs_cb.isChecked()
    assert not tool.ui.limit_pairs_entry.isEnabled()
    tool.ui.limit_pairs_cb.set_value(limited)
    assert tool.ui.limit_pairs_entry.isEnabled() == limited
    monkeypatch.setattr(tool, 'draw_markers', lambda *args, **kwargs: None)
    tool.ui.a_type_radio.set_value('auto')
    tool.ui.set_fit_model('rigid')
    tool.ui.allow_mirror_cb.set_value(False)
    tool.on_type_obj_changed('grb' if reverse else 'exc')
    tool.on_type_aligner_changed('exc' if reverse else 'grb')
    tool.ui.object_combo.set_value((grb if reverse else exc).obj_options['name'])
    tool.ui.aligner_object_combo.set_value((exc if reverse else grb).obj_options['name'])
    tool.on_find()
    end = time.monotonic() + 10
    while not tool.ui.find_button.isEnabled() and time.monotonic() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    assert tool.auto_results
    result = tool.auto_results[0]
    assert (result.matched, result.total) == (12, 44)
    np.testing.assert_allclose(apply_matrix(result.matrix, result.P), result.Q, atol=0.02)
    assert '12 / 44' in tool.ui.result_label.text()
    assert tool.ui.points_table.rowCount() == (6 if limited else 12)
    n = _stack(fc)
    tool.apply_auto()
    _one_step(fc, n, 'Align')
    if reverse:
        np.testing.assert_allclose([r['follow'].coords[0] for r in grb.tools[10]['geometry']], holes[:12], atol=0.02)
    else:
        np.testing.assert_allclose([p.coords[0] for p in exc.tools[1]['drills'][:12]], through, atol=0.02)
    assert fc.history.undo()
    np.testing.assert_allclose([r['follow'].coords[0] for r in grb.tools[10]['geometry']], through, atol=0.02)
    np.testing.assert_allclose([p.coords[0] for p in exc.tools[1]['drills']], holes, atol=0.02)
    tool.set_tool_ui()
    assert not tool.ui.limit_pairs_cb.isChecked()
    assert tool.ui.limit_pairs_entry.get_value() == 6
    tool.deleteLater()


@pytest.mark.parametrize('level,checked,value,expected', [
    ('a', False, 1, 0), ('a', True, 0, 0), ('b', True, 1, 0), ('a', True, 6, 6)
])
def test_auto_pair_limit_worker_uses_snapshot(fc, no_plot, monkeypatch, level, checked, value, expected):
    import appPlugins.ToolAlignObjects.Align as module
    tool = module.AlignObjects(fc)
    tool.set_tool_ui()
    tool.change_level(level)
    assert tool.ui.advanced_frame.isHidden() == (level == 'b')
    tool.ui.limit_pairs_cb.set_value(checked)
    tool.ui.limit_pairs_entry.set_value(value)
    tool.ui.set_fit_model('similarity')
    tool.ui.allow_mirror_cb.set_value(True)
    tool.aligned_obj = tool.aligner_obj = object()
    monkeypatch.setattr(tool, '_read_objects', lambda: True)
    monkeypatch.setattr(module, 'build_snap_index', lambda obj: object())
    calls, jobs = [], []
    monkeypatch.setattr(module, 'auto_align', lambda *args, **kwargs: calls.append(kwargs) or [])
    monkeypatch.setattr(fc, 'worker_task', SimpleNamespace(emit=jobs.append))
    tool.on_find()
    tool.ui.limit_pairs_entry.set_value(1)
    tool.ui.limit_pairs_cb.set_value(not checked)
    tool.ui.set_fit_model('affine')
    tool.ui.allow_mirror_cb.set_value(False)
    jobs[0]['fcn']()
    assert calls[0]['pair_limit'] == expected
    assert calls[0]['model'] == 'similarity'
    assert calls[0]['allow_mirror'] is True
    tool.deleteLater()


def test_dblsided_smd_process_registers_drills_and_holes_idempotent(fc, no_plot):
    grb, exc, through, holes, matrix = _smd_sources(fc, 'dbl_smd')
    tool = DblSidedTool(fc)
    tool.set_tool_ui()
    tool.ui.top_combo.set_value(grb.obj_options['name'])
    tool.ui.drills_combo.set_value(exc.obj_options['name'])
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    n = _stack(fc)
    tool.on_process()
    _wait_dbl(tool)
    _one_step(fc, n, '2-Sided')
    expected = [r['follow'].coords[0] for r in grb.tools[10]['geometry']]
    np.testing.assert_allclose([p.coords[0] for p in exc.tools[1]['drills'][:12]], expected, atol=0.02)
    assert len(exc.tools[1]['drills']) == len(holes) + 3
    tool.on_process()
    _wait_dbl(tool)
    assert len(exc.tools[1]['drills']) == len(holes) + 3
    np.testing.assert_allclose([p.coords[0] for p in exc.tools[1]['drills'][:12]], expected, atol=0.02)
    tool.deleteLater()


# ---------------------------------------------------------------------------------------------------------------------
# 3. Cutout: manual gap, 1 step per click
# ---------------------------------------------------------------------------------------------------------------------
def _cutout_stub(fc, geo):
    ui = SimpleNamespace(
        dia=SimpleNamespace(get_value=lambda: 2.0),
        gapsize=SimpleNamespace(get_value=lambda: 1.0),
        gaptype_combo=SimpleNamespace(get_value=lambda: 1),          # thin gaps -> tool 99
        cutz_entry=SimpleNamespace(get_value=lambda: -1.0),
        thin_depth_entry=SimpleNamespace(get_value=lambda: -0.5),
        mpass_cb=SimpleNamespace(get_value=lambda: False),
        maxdepth_entry=SimpleNamespace(get_value=lambda: 0.5))
    fc.geo_editor = SimpleNamespace(snap=lambda x, y: (x, y))
    tool_dict = {'tooldia': 1.0, 'data': {}, 'solid_geometry': []}
    stub = SimpleNamespace(app=fc, ui=ui, man_cutout_obj=geo, cut_tool_dict=tool_dict, mb_manual_solid_geo=None,
                           mb_manual_cuts=[])
    return CutOut, stub


def test_manual_cutout_click_is_a_snapshot_step(fc, no_plot):
    geo = _geometry(fc, 'geo_cut')
    geo.plot = lambda *a, **k: None
    CutOut, stub = _cutout_stub(fc, geo)
    keys0 = sorted(geo.tools)
    n = _stack(fc)
    CutOut.on_manual_cutout(stub, (10.0, 0.0))
    _one_step(fc, n, 'Manual Cutout')
    assert geo.multigeo is True and 99 in geo.tools
    solid1 = geo.tools[1]['solid_geometry']
    assert fc.history.undo()
    assert geo.multigeo is False and sorted(geo.tools) == keys0 and geo.tools[1]['solid_geometry'] == []
    assert fc.history.redo()
    assert geo.multigeo is True and 99 in geo.tools
    assert geo.tools[1]['solid_geometry'].equals(solid1) if hasattr(solid1, 'equals') else True


def test_manual_cutout_session_cancel_and_finish_preserve_history(fc, no_plot):
    from unittest.mock import Mock
    from test_cutout_inner_contours import make_tool

    geo = _geometry(fc, 'geo_cut_session')
    tool = make_tool()
    app = tool.test_app
    app.history = fc.history
    app.collection.objs[geo.obj_options['name']] = geo
    app.ui.keyPressEvent = Mock()
    app.ui.notebook = Mock()
    app.geo_editor = SimpleNamespace(snap=lambda x, y: (x, y), tool_shape=Mock())
    app.on_mouse_click_over_plot = Mock()
    app.on_mouse_click_release_over_plot = Mock()
    app.on_mouse_move_over_plot = Mock()
    app.plotcanvas.translate_coords = lambda pos: pos
    app.plotcanvas.pixel_size = lambda: 0.1
    app.grid_status = lambda: False
    tool.ui.man_object_combo = SimpleNamespace(currentText=lambda: geo.obj_options['name'])
    tool.ui.big_cursor_cb.set_value(False)
    tool.ui.dia.set_value(2.0)
    tool.ui.gapsize.set_value(1.0)
    tool.ui.cutz_entry.set_value(-1.0)
    tool.ui.thin_depth_entry.set_value(-0.5)
    tool.ui.gaptype_combo.set_value(1)
    original = appHistory.object_state(geo)
    n = _stack(fc)
    tool.on_manual_gap_click()
    tool.on_manual_cutout((10, 0))
    tool.on_manual_cutout((20, 5))
    tool.on_mouse_click_release(SimpleNamespace(pos=(10, 0), button=1))
    tool.on_mouse_click_press(SimpleNamespace(pos=(10, 0), button=1))
    tool.on_mouse_move(SimpleNamespace(pos=(5, 10), button=1, is_dragging=True))
    tool.on_mouse_click_release(SimpleNamespace(pos=(5, 10), button=1))
    tool.on_mouse_click_release(SimpleNamespace(pos=(20, 5), button=1))
    tool.on_key_press(SimpleNamespace(key='Delete'))
    tool.on_key_press(SimpleNamespace(key='Escape'))
    assert appHistory.states_equal(original, appHistory.object_state(geo))
    assert _stack(fc) == n
    # Cancelling must leave the preceding action undoable, not redo cancelled gaps.
    assert fc.history.undo()
    assert fc.collection.get_by_name('geo_cut_session') is None
    assert fc.history.redo()
    _settle(fc)
    geo = fc.collection.get_by_name('geo_cut_session')
    app.collection.objs[geo.obj_options['name']] = geo
    tool.on_manual_gap_click()
    tool.on_manual_cutout((10, 0))
    tool.on_manual_cutout((20, 5))
    tool.on_mouse_click_release(SimpleNamespace(pos=(10, 0), button=1))
    tool.on_mouse_click_press(SimpleNamespace(pos=(10, 0), button=1))
    tool.on_mouse_move(SimpleNamespace(pos=(5, 10), button=1, is_dragging=True))
    tool.on_mouse_click_release(SimpleNamespace(pos=(5, 10), button=1))
    tool.on_mouse_click_release(SimpleNamespace(pos=(20, 5), button=1))
    tool.on_key_press(SimpleNamespace(key='Delete'))
    geo.obj_options['session_note'] = 'keep'
    tool.on_mouse_click_release(SimpleNamespace(pos=(0, 0), button=2))
    _one_step(fc, n, 'Manual Cutout')
    finished = unary_union(geo.solid_geometry)
    assert 99 in geo.tools
    assert finished.distance(Point(10, 0)) < 1e-7
    assert finished.distance(Point(5, 10)) > 1.0
    thin = unary_union(geo.tools[99]['solid_geometry'])
    assert thin.length == pytest.approx(3.0)
    assert fc.history.undo(), fc.history.blocked_reason()
    assert 99 not in geo.tools and not geo.multigeo
    assert unary_union(geo.solid_geometry).length == pytest.approx(60)
    assert geo.obj_options['session_note'] == 'keep'
    assert fc.history.redo()
    assert unary_union(geo.solid_geometry).equals(finished) and 99 in geo.tools
    assert unary_union(geo.tools[99]['solid_geometry']).equals(thin)


# ---------------------------------------------------------------------------------------------------------------------
# 4 / 5. Single-geo <-> Multi-geo
# ---------------------------------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------------------------------
# 6. Simplify (runs in the worker)
# ---------------------------------------------------------------------------------------------------------------------
def test_simplify_is_a_snapshot_step(fc, no_plot, monkeypatch):
    # a rectangle contour with many collinear vertices
    pts = [(x, 0.0) for x in np.linspace(0, 20, 41)] + [(20.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    contour = LineString(pts)

    def init(obj, _app):
        obj.solid_geometry = [contour]
        obj.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': [contour]}}
        obj.multigeo = True
    geo = fc.app_obj.new_object('geometry', 'geo_simp', init, plot=False)
    monkeypatch.setattr(fc, 'worker_task', SimpleNamespace(emit=lambda d: d['fcn'](*d['params'])))
    monkeypatch.setattr(fc, 'plot_all', lambda *a, **k: None)
    geo.ui = SimpleNamespace(geo_tol_entry=SimpleNamespace(get_value=lambda: 0.01))
    geo.on_calculate_vertex_points = lambda: None

    def count(g):
        return sum(len(p.coords) for p in g.tools[1]['solid_geometry'])
    c0 = count(geo)
    n = _stack(fc)
    geo.on_simplify_geometry()
    _one_step(fc, n, 'Simplify')
    assert count(geo) < c0
    assert fc.history.undo() and count(geo) == c0
    assert fc.history.redo() and count(geo) < c0


# ---------------------------------------------------------------------------------------------------------------------
# 7. Gerber buffer, 8. Isolation buffer
# ---------------------------------------------------------------------------------------------------------------------
def test_isolation_generate_buffer_is_a_snapshot_step(fc, no_plot, monkeypatch):
    grb = _load(fc, 'gerber', 'unify_mixed.gbr', 'g_iso_buf')
    grb.solid_geometry = list(grb.solid_geometry.geoms) if hasattr(grb.solid_geometry, 'geoms') \
        else list(grb.solid_geometry)
    monkeypatch.setattr(type(grb), 'plot_single_object', SimpleNamespace(emit=lambda: None), raising=False)
    monkeypatch.setattr(fc, 'worker_task', SimpleNamespace(emit=lambda d: d['fcn'](*d['params'])))
    stub = SimpleNamespace(app=fc, ui=SimpleNamespace(object_combo=SimpleNamespace(currentText=lambda: 'g_iso_buf')))
    n = _stack(fc)
    ToolIsolation.on_generate_buffer(stub)
    _one_step(fc, n, 'Buffer')
    assert not isinstance(grb.solid_geometry, list)          # the list became 1 buffered geometry
    assert fc.history.undo() and isinstance(grb.solid_geometry, list)
    assert fc.history.redo() and not isinstance(grb.solid_geometry, list)


# ---------------------------------------------------------------------------------------------------------------------
# 9. CNCJob source file, 10 / 11. Rename
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def cncjob(fc, no_plot):
    _load(fc, 'gerber', 'unify_mixed.gbr', 'g_job')
    fc.shell.tcl.eval('isolate g_job -dia 0.2 -passes 1 -outname g_job_iso')
    _settle(fc)
    fc.shell.tcl.eval('cncjob g_job_iso -dia 0.2 -z_cut -0.1 -z_move 2 -feedrate 100 -outname job_hist')
    _settle(fc)
    for _ in range(20):
        QtWidgets.QApplication.processEvents()
    job = fc.collection.get_by_name('job_hist')
    assert job is not None
    return job


def test_cncjob_update_source_file_is_a_snapshot_step(fc, cncjob):
    src0 = cncjob.source_file
    cncjob.prepend_snippet = '(SNIPPET-START)\n'
    cncjob.append_snippet = '(SNIPPET-END)\n'
    cncjob.ui.snippets_cb.blockSignals(True)
    cncjob.ui.snippets_cb.set_value(True)
    cncjob.ui.snippets_cb.blockSignals(False)
    n = _stack(fc)
    cncjob.on_update_source_file()
    _one_step(fc, n, 'Source')
    src1 = cncjob.source_file
    assert src1 != src0 and '(SNIPPET-START)' in src1
    assert fc.history.undo() and cncjob.source_file == src0
    assert fc.history.redo() and cncjob.source_file == src1
