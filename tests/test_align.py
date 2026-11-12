"""Merged from: test_align_auto: Tests of the automatic alignment (AlignGen.auto_align) on synthetic boards: holes must fall into pads.; test_align_apply: Align Objects: one matrix applied to real Gerber / Excellon / Geometry objects, and the Undo by the inverse matrix.
"""

import math
import os
import sys
import time
import unittest
import numpy as np
import pytest
import shapely
from shapely import Point, Polygon, box
from appPlugins.ToolAlignObjects.AlignGen import AlignError, SnapIndex, auto_align, is_ambiguous, apply_matrix, invert, build_snap_index, snap, solve_translate, solve_rigid, solve_similarity, solve_affine
from types import SimpleNamespace
from unittest.mock import Mock
from shapely.geometry import LineString
from appObjects.GeometryObject import GeometryObject
from appHandlers.appHistory import HistoryManager, apply_matrix_to_object, transform_metadata
from test_app_history import fake_app
from tests.helpers_objects import app, _headless_qt, make_excellon, make_gerber


# ==============================================================================================================
# test_align_auto: Tests of the automatic alignment (AlignGen.auto_align) on synthetic boards: holes must fall into pads.
# ==============================================================================================================

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def board(seed=1, vias=60):
    """Pad centers of a board: 3 DIP footprints, 2 rows of headers, random vias. Not symmetric."""
    rng = np.random.default_rng(seed)
    pts = []
    for ox, oy, n in ((5, 5, 8), (30, 8, 14), (12, 40, 4)):
        for i in range(n):
            pts.append((ox + i * 2.54, oy))
            pts.append((ox + i * 2.54, oy + 7.62))
    for i in range(10):
        pts.append((60, 5 + i * 2.54))
    pts.extend(rng.uniform((0, 0), (80, 60), (vias, 2)).tolist())
    return np.array(pts)

def pad_index(centers, size=1.6):
    solids = [box(x - size / 2, y - size / 2, x + size / 2, y + size / 2) for x, y in centers]
    return SnapIndex(solids, centers, np.empty((0, 2)))

def hole_index(centers, dia=0.8):
    return SnapIndex([Point(x, y).buffer(dia / 2) for x, y in centers], centers, np.empty((0, 2)))

def rot(angle_deg, dx, dy, s=1.0):
    t = math.radians(angle_deg)
    return s * math.cos(t), -s * math.sin(t), s * math.sin(t), s * math.cos(t), dx, dy

def smd_board():
    """Sparse irregular through holes surrounded by many much closer, unrelated SMD flashes."""
    through = np.array([(0, 0), (8, 1), (21, 0), (3, 12), (15, 9), (31, 7),
                        (1, 25), (13, 22), (26, 19), (38, 28), (8, 37), (29, 39)], dtype=float)
    smd = np.vstack([p + np.random.default_rng(i).uniform((1, 1), (3, 3), (20, 2))
                     for i, p in enumerate(through)])
    centers = np.vstack([through, smd])
    pads = SnapIndex([Point(p).buffer(0.8) for p in through] +
                    [box(x - 0.1, y - 0.15, x + 0.1, y + 0.15) for x, y in smd], centers, [])
    pads.kind = 'gerber'
    matrix = rot(37, 57, -23)
    # Two non-plated holes have no copper pad; neither should invalidate the twelve real correspondences.
    holes = hole_index(apply_matrix(invert(matrix), np.vstack([through, [(70, 65), (-25, 60)]])))
    holes.kind = 'excellon'
    return through, pads, holes, matrix

def partial_smd_board():
    """Mounting/tooling holes and SMD pads are both mostly unmatched; only twelve points are common."""
    through, pads, holes, matrix = smd_board()
    candidates = np.random.default_rng(81).uniform((-20, -20), (60, 60), (200, 2))
    distances = np.linalg.norm(candidates[:, None, :] - pads.centers[None, :, :], axis=2)
    mounting = candidates[distances.min(axis=1) > 1.5][:30]
    holes = hole_index(np.vstack([holes.centers, apply_matrix(invert(matrix), mounting)]))
    holes.kind = 'excellon'
    return through, pads, holes, matrix

@pytest.mark.parametrize('reverse,noise', [(False, 0), (True, 0.01)])
def test_smd_alignment_counts_holes_and_maps_preview_in_motion_direction(reverse, noise):
    through, pads, holes, matrix = smd_board()
    if noise:
        holes.centers += np.random.default_rng(9).normal(0, noise, holes.centers.shape)
    results = auto_align(pads, holes) if reverse else auto_align(holes, pads)
    best = results[0]
    assert best.total == len(holes.centers), (best.matched, best.total)
    assert best.matched == len(through), (best.matched, best.total, best.matrix)
    assert best.matched == len(best.P) == len(best.Q)
    assert not is_ambiguous(results)
    want = invert(matrix) if reverse else matrix
    moving = pads.centers[:len(through)] if reverse else holes.centers[:len(through)]
    np.testing.assert_allclose(apply_matrix(best.matrix, moving), apply_matrix(want, moving), atol=0.02)
    np.testing.assert_allclose(apply_matrix(best.matrix, best.P), best.Q, atol=0.03)

@pytest.mark.parametrize('mirrored,parallel', [(False, False), (True, True)])
def test_dblsided_smd_alignment_uses_drill_coverage_for_both_layers(mirrored, parallel):
    from appPlugins.ToolDblSided.DblSidedGen import alignment_matrices
    through, pads, holes, matrix = partial_smd_board()
    bottom_matrix = rot(-19, 11, 6)
    if mirrored:
        bottom_matrix = (-bottom_matrix[0], bottom_matrix[1], -bottom_matrix[2], *bottom_matrix[3:])
    from shapely.affinity import affine_transform
    bottom = SnapIndex([affine_transform(g, bottom_matrix) for g in pads.tree.geometries],
                       apply_matrix(bottom_matrix, pads.centers), [])
    bottom.kind = 'gerber'
    original = [index.centers.copy() for index in (pads, bottom, holes)]
    matrices = alignment_matrices([pads, bottom, holes], [0, 1], 2, parallel=parallel)
    np.testing.assert_allclose(apply_matrix(matrices[2], holes.centers[:len(through)]), through, atol=0.02)
    np.testing.assert_allclose(apply_matrix(matrices[1], bottom.centers[:len(through)]), through, atol=0.02)
    for index, centers in zip((pads, bottom, holes), original):
        np.testing.assert_array_equal(index.centers, centers)

def test_smd_alignment_rejects_genuinely_repeated_hole_patterns():
    from appPlugins.ToolDblSided.DblSidedGen import alignment_matrices
    through, pads, holes, matrix = partial_smd_board()
    repeated = through + (100, 0)
    pads = SnapIndex(list(pads.tree.geometries) + [Point(p).buffer(0.8) for p in repeated],
                     np.vstack([pads.centers, repeated]), [], kind='gerber')
    results = auto_align(holes, pads)
    assert results[0].matched == len(through)
    assert is_ambiguous(results)
    with pytest.raises(AlignError, match='ambiguous'):
        alignment_matrices([pads, holes], [0], 1)

@pytest.mark.parametrize('mirrored', [True])
def test_bounded_registration_partial_features_and_original_snapshots(mirrored):
    from appPlugins.ToolAlignObjects.AlignGen import bounded_align
    through, pads, holes, matrix = partial_smd_board()
    if mirrored:
        holes = hole_index(holes.centers * (-1, 1))
        holes.kind = 'excellon'
    original = holes.centers.copy()
    results = bounded_align(holes, pads, allow_mirror=True)
    assert results[0].matched == len(through)
    assert not is_ambiguous(results)
    np.testing.assert_allclose(apply_matrix(results[0].matrix, original[:12]), through, atol=0.02)
    np.testing.assert_array_equal(holes.centers, original)

@pytest.mark.parametrize('model,matrix,limit', [
    ('affine', (1.01, 0.015, -0.01, 0.99, 0.2, -0.1), 3),
])
def test_pair_limit_fits_selected_model_and_verifies_every_feature(model, matrix, limit, monkeypatch):
    import appPlugins.ToolAlignObjects.AlignGen as gen
    fits = []
    for name in ('solve_rigid', 'solve_similarity', 'solve_affine'):
        solver = getattr(gen, name)
        def capped(P, Q, solver=solver):
            assert len(P) == len(Q) <= limit
            fits.append(len(P))
            return solver(P, Q)
        monkeypatch.setattr(gen, name, capped)
    points = board(vias=15)
    result = auto_align(hole_index(points), pad_index(apply_matrix(matrix, points)),
                        model=model, pair_limit=limit)[0]
    assert result.matched == result.total == len(points)
    assert len(result.P) == len(result.errors) == len(points)
    assert len(result.fit_P) == len(result.fit_Q) == limit
    assert fits
    np.testing.assert_allclose(apply_matrix(result.matrix, points), apply_matrix(matrix, points), atol=1e-7)
    # The actual solver evidence spans the board, not the first footprint in input order.
    assert np.ptp(result.fit_P, axis=0).max() > 40
    solver = {'rigid': gen.solve_rigid, 'similarity': gen.solve_similarity, 'affine': gen.solve_affine}[model]
    np.testing.assert_allclose(solver(result.fit_P, result.fit_Q), result.matrix, atol=1e-7)

class TestAutoAlign(unittest.TestCase):

    def check(self, holes, pads, want, **kw):
        """want: the matrix that maps the holes onto the pads."""
        t0 = time.perf_counter()
        res = auto_align(hole_index(holes), pad_index(pads), **kw)
        took = time.perf_counter() - t0
        best = res[0]
        err = np.hypot(*(apply_matrix(best.matrix, holes) - apply_matrix(want, holes)).T)
        self.assertLess(float(err.max()), 0.05, 'max error %.4f' % err.max())
        self.assertLess(took, 5.0)
        return best

    def test_rotations(self):
        pads = board()
        for ang in (0, 37, 90, 180, -123.4):
            with self.subTest(angle=ang):
                m = rot(ang, 15.0, -7.5)
                holes = apply_matrix(invert(m), pads)
                best = self.check(holes, pads, m)
                self.assertEqual(best.matched, len(holes))
                self.assertFalse(best.mirrored)

    def test_mirror(self):
        pads = board()
        m = rot(25, 100.0, 3.0)
        mirrored = (-m[0], m[1], -m[2], m[3], m[4], m[5])    # m after x -> -x
        holes = apply_matrix(invert(mirrored), pads)
        best = self.check(holes, pads, mirrored, allow_mirror=True)
        self.assertTrue(best.mirrored)
        with self.assertRaises(AssertionError):
            self.check(holes, pads, mirrored, allow_mirror=False)

    def test_outliers_and_missing_pads(self):
        rng = np.random.default_rng(5)
        pads = board(vias=80)
        m = rot(-71, -20.0, 40.0)
        holes = apply_matrix(invert(m), pads)
        holes = holes[rng.permutation(len(holes))[: int(0.85 * len(holes))]]     # pads with no hole
        extra = rng.uniform(holes.min(axis=0), holes.max(axis=0), (15, 2))        # holes with no pad
        noisy = np.vstack([holes, extra]) + rng.normal(0, 0.02, (len(holes) + 15, 2))
        best = self.check(noisy, pads, m)
        self.assertGreaterEqual(best.matched, len(holes) - 2)
        self.assertLess(best.rms, 0.06)

    def test_errors(self):
        with self.assertRaises(AlignError):
            auto_align(hole_index(np.array([(0.0, 0.0)])), pad_index(board()))
        with self.assertRaises(AlignError):
            auto_align(hole_index(board()), SnapIndex([], [], np.empty((0, 2))))

class TestSolvers(unittest.TestCase):
    """The Align Objects solvers and the snap index (folded from test_align_objects.py)."""

    P = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [2.0, 7.0], [-3.0, 4.0]])

    @staticmethod
    def _rot(angle_deg, s=1.0, dx=0.0, dy=0.0):
        t = math.radians(angle_deg)
        c, sn = s * math.cos(t), s * math.sin(t)
        return c, -sn, sn, c, dx, dy

    def test_solvers_recover_the_matrix(self):
        P = self.P
        np.testing.assert_allclose(solve_translate([(1, 2)], [(4, -1)]), (1, 0, 0, 1, 3, -3), atol=1e-9)
        for ang in (0, 90, -179):
            want = self._rot(ang, dx=12.5, dy=-3.25)
            for n in (2, 5):
                np.testing.assert_allclose(solve_rigid(P[:n], apply_matrix(want, P[:n])), want, atol=1e-9)
        want = self._rot(30, s=1.02, dx=1, dy=2)
        np.testing.assert_allclose(solve_similarity(P, apply_matrix(want, P)), want, atol=1e-9)
        want = (1.01, 0.02, -0.03, 0.97, 5.0, -7.0)
        np.testing.assert_allclose(solve_affine(P, apply_matrix(want, P)), want, atol=1e-9)
        np.testing.assert_allclose(solve_affine(P[:3], apply_matrix(want, P[:3])), want, atol=1e-9)

    def test_snap_to_gerber_and_excellon_features(self):
        region = Polygon([(10, 10), (14, 10), (14, 12), (10, 12)])
        gerber = _FakeObj('gerber', tools={
            10: {'size': 4.0, 'geometry': [{'solid': Point(0, 0).buffer(2.0), 'follow': Point(0, 0)}]},
            11: {'size': 2.0, 'geometry': [{'solid': Point(0.5, 0).buffer(1.0), 'follow': Point(0.5, 0)}]},
            0: {'type': 'REG', 'geometry': [{'solid': region, 'follow': region.exterior}]},
        })
        idx = build_snap_index(gerber)
        self.assertEqual(snap(idx, 0.6, 0.1, 0.1), (0.5, 0.0))
        self.assertEqual(snap(idx, -1.5, 0.0, 0.1), (0.0, 0.0))
        exc = _FakeObj('excellon', tools={
            1: {'tooldia': 1.0, 'drills': [Point(3, 3)], 'slots': [((10, 0), (14, 0))]},
        })
        idx = build_snap_index(exc)
        self.assertEqual(snap(idx, 3.2, 3.1, 0.1), (3.0, 3.0))
        self.assertEqual(snap(idx, 13.0, 0.2, 0.1), (12.0, 0.0))

class _FakeObj:
    def __init__(self, kind, tools=None, solid_geometry=None, multigeo=False):
        self.kind = kind
        self.tools = tools or {}
        self.solid_geometry = solid_geometry
        self.multigeo = multigeo
        self.circle_steps = lambda radius: 64

if __name__ == '__main__':
    unittest.main()


# ==============================================================================================================
# test_align_apply: Align Objects: one matrix applied to real Gerber / Excellon / Geometry objects, and the Undo by the inverse matrix.
# ==============================================================================================================

MATRICES = {
    'rigid': solve_rigid([(0, 0), (10, 0)], [(3, 4), (3, 14)]),                      # 90 deg + move
    'similarity': solve_similarity([(0, 0), (10, 0)], [(1, 1), (-9.2, 1.4)]),      # about 180 deg, scale 1.02
    'affine': solve_affine([(0, 0), (10, 0), (0, 10)], [(1, 2), (11.1, 2.2), (0.9, 11.8)]),
}

def _geometry(app):
    obj = GeometryObject('geo', app)
    obj.solid_geometry = [LineString([(0, 0), (5, 0), (5, 3)]), LineString([(1, 1), (2, 7)])]
    return obj

def _make(app, kind):
    return {'gerber': make_gerber, 'excellon': make_excellon, 'geometry': _geometry}[kind](app)

@pytest.mark.parametrize('kind,model', [('gerber', 'rigid'), ('excellon', 'similarity'), ('geometry', 'affine')])
def test_apply_and_undo(app, kind, model):
    obj = _make(app, kind)
    matrix = MATRICES[model]
    before = np.array(obj.bounds())

    apply_matrix_to_object(obj, matrix)
    after = np.array(obj.bounds())
    assert after == pytest.approx([obj.obj_options[k] for k in ('xmin', 'ymin', 'xmax', 'ymax')])
    assert not np.allclose(after, before)

    if kind == 'geometry' or model == 'rigid':
        # the bounds of the transformed corners hold the new bounds for the shapes that the matrix maps exactly
        corners = apply_matrix(matrix, [before[[0, 1]], before[[2, 1]], before[[2, 3]], before[[0, 3]]])
        assert after[:2] == pytest.approx(corners.min(axis=0), abs=0.6)

    apply_matrix_to_object(obj, invert(matrix))
    assert np.array(obj.bounds()) == pytest.approx(before, abs=1e-6)

def _registration_fit(points=((0.0, 0.0), (10.0, 0.0))):
    from appPlugins.ToolGcodeSender.CameraRegistration import CameraRegistration

    model = CameraRegistration()
    for (x, y), (qx, qy) in zip(points, ((3.0, 4.0), (3.0, 14.0))):
        model.add_design(x, y)
        ok, reason = model.add_observation({
            'ok': True, 'frame': 'work', 'x': qx, 'y': qy, 'wco': (10.0, 20.0),
            'session': 1, 'units': 'MM', 'camera_id': 'cam-A', 'rotation': 0, 'mirror': False,
            'offset': (30.0, -10.0), 'view_z': -5.0,
        })
        assert ok, reason
    return model.fit(0.001)

def _registration_history(objects, app):
    from appHandlers.appHistory import HistoryManager

    app.collection = SimpleNamespace(get_list=lambda: list(objects))
    app.history = HistoryManager(app, collection=app.collection)
    return app.history

def test_registration_applies_to_all_selected_objects_as_one_undo_step(app):
    from appPlugins.ToolGcodeSender.CameraRegistration import apply_registration

    gerber = make_gerber(app)
    excellon = make_excellon(app)
    objects = [gerber, excellon]
    for obj in objects:
        obj.plot_transformed = Mock()
        obj.regenerate_source = Mock(return_value=True)
    history = _registration_history(objects, app)
    before = {id(obj): np.array(obj.bounds()) for obj in objects}

    apply_registration(app, objects, _registration_fit())
    assert all(not np.allclose(np.array(obj.bounds()), before[id(obj)]) for obj in objects)
    assert history.can_undo()
    assert len(history.undo_stack) == 1

    assert history.undo()
    for obj in objects:
        assert np.array(obj.bounds()) == pytest.approx(before[id(obj)], abs=1e-6)
    assert history.redo()
    assert all(not np.allclose(np.array(obj.bounds()), before[id(obj)]) for obj in objects)

def test_registration_applies_the_previewed_overdetermined_fit(app):
    from appPlugins.ToolGcodeSender.CameraRegistration import CameraRegistration, apply_registration

    model = CameraRegistration()
    for (x, y), (qx, qy) in (
            ((0.0, 0.0), (0.0, 0.0)),
            ((10.0, 0.0), (10.0, 0.0)),
            ((0.0, 10.0), (0.0, 10.5))):
        model.add_design(x, y)
        ok, reason = model.add_observation({
            'ok': True, 'frame': 'work', 'x': qx, 'y': qy, 'wco': (10.0, 20.0),
            'session': 1, 'units': 'MM', 'camera_id': 'cam-A', 'rotation': 0, 'mirror': False,
            'offset': (30.0, -10.0), 'view_z': -5.0,
        })
        assert ok, reason
    fit = model.fit(0.05)
    assert fit is model.preview
    assert fit.max_error > 0.05

    obj = make_gerber(app)
    obj.plot_transformed = Mock()
    obj.regenerate_source = Mock(return_value=True)
    history = _registration_history([obj], app)
    before = np.array(obj.bounds())
    apply_registration(app, [obj], fit)
    assert not np.allclose(np.array(obj.bounds()), before)
    assert len(history.undo_stack) == 1

    apply_matrix_to_object(obj, invert(fit.matrix))
    assert np.array(obj.bounds()) == pytest.approx(before, abs=1e-6)

def test_registration_failure_rolls_back_every_touched_object(app, monkeypatch):
    from appPlugins.ToolGcodeSender import CameraRegistration

    gerber = make_gerber(app)
    excellon = make_excellon(app)
    objects = [gerber, excellon]
    for obj in objects:
        obj.plot_transformed = Mock()
        obj.regenerate_source = Mock(return_value=True)
        obj.plot = Mock()
    history = _registration_history(objects, app)
    before = {id(obj): (np.array(obj.bounds()), obj.solid_geometry,
                        getattr(obj, 'source_file', None)) for obj in objects}
    real = CameraRegistration.apply_matrix_to_object
    calls = []

    def fail_after_mutating_second(obj, matrix):
        # the second object is changed and only then raises, as _after_transform / bounds / create_geometry can
        calls.append(obj)
        real(obj, matrix)
        obj.source_file = 'partially rewritten source'
        if len(calls) == 2:
            raise RuntimeError('boom')
        return None

    monkeypatch.setattr(CameraRegistration, 'apply_matrix_to_object', fail_after_mutating_second)
    with pytest.raises(Exception):
        CameraRegistration.apply_registration(app, objects, _registration_fit())
    for obj in objects:
        bounds, solid, source = before[id(obj)]
        assert np.array(obj.bounds()) == pytest.approx(bounds, abs=1e-6)
        assert obj.solid_geometry == solid
        assert getattr(obj, 'source_file', None) == source
    assert not history.can_undo()

def test_a_failing_history_step_restores_the_transformed_objects(app, monkeypatch):
    from appPlugins.ToolGcodeSender import CameraRegistration
    from appHandlers.appIO import appIO

    app.f_handlers = appIO(app)
    gerber = make_gerber(app)
    objects = [gerber]
    gerber.plot_transformed = Mock()
    assert gerber.regenerate_source() is True
    gerber.plot = Mock()
    history = _registration_history(objects, app)
    before = np.array(gerber.bounds())
    source = getattr(gerber, 'source_file', None)

    def fail_record(label, pairs):
        assert gerber.source_file != source
        raise RuntimeError('history')

    monkeypatch.setattr(history, 'record_matrix', fail_record)
    with pytest.raises(Exception):
        CameraRegistration.apply_registration(app, objects, _registration_fit())
    assert np.array(gerber.bounds()) == pytest.approx(before, abs=1e-6)
    assert getattr(gerber, 'source_file', None) == source
    assert not history.can_undo()

def test_registration_validates_every_object_before_mutating_any(app):
    from appPlugins.ToolGcodeSender.CameraRegistration import apply_registration

    gerber = make_gerber(app)
    deleted = make_excellon(app)
    deleted.deleted = True
    gerber.plot_transformed = Mock()
    gerber.regenerate_source = Mock(return_value=True)
    _registration_history([gerber, deleted], app)
    before = np.array(gerber.bounds())
    with pytest.raises(Exception):
        apply_registration(app, [gerber, deleted], _registration_fit())
    assert np.array(gerber.bounds()) == pytest.approx(before)

@pytest.mark.parametrize('kind,workflow', [('gerber', 'align'), ('excellon', 'camera')])
def test_finish_rewrites_the_source_of_the_aligned_object(app, kind, workflow, tmp_path):
    """View Source and Save source write source_file: it has to be the aligned object, not the loaded file."""
    from appHandlers.appIO import appIO
    from appPlugins.ToolAlignObjects.Align import AlignObjects
    from tests.helpers_objects import reparse_excellon, reparse_gerber
    app.f_handlers = appIO(app)
    obj = _make(app, kind)
    assert obj.regenerate_source() is True
    obj.plot_transformed = Mock()
    if workflow == 'camera':
        from appPlugins.ToolGcodeSender.CameraRegistration import apply_registration
        _registration_history([obj], app)
        apply_registration(app, [obj], _registration_fit())
    else:
        tool = Mock(start_points=[(0, 0), (10, 0)], dest_points=[(3, 4), (3, 14)], aligned_obj=obj, decimals=4)
        tool._solve = solve_rigid
        tool._after_apply = lambda aligned, matrix: AlignObjects._after_apply(tool, aligned, matrix)
        AlignObjects.finish(tool)
    again = (reparse_gerber if kind == 'gerber' else reparse_excellon)(app, obj.source_file, tmp_path)
    assert np.array(again.bounds()) == pytest.approx(np.array(obj.bounds()), abs=1e-3)
