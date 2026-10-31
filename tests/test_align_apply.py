"""Align Objects: one matrix applied to real Gerber / Excellon / Geometry objects, and the Undo by the inverse matrix."""


from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString

from appObjects.GeometryObject import GeometryObject
from appHandlers.appHistory import HistoryManager, apply_matrix_to_object, transform_metadata
from appPlugins.ToolAlignObjects.AlignGen import apply_matrix, invert, solve_rigid, solve_similarity, solve_affine

from test_app_history import fake_app
from tests.helpers_objects import app, _headless_qt, make_excellon, make_gerber  # noqa: F401,E402

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


@pytest.mark.parametrize('kind', ['gerber', 'excellon', 'geometry'])
@pytest.mark.parametrize('model', list(MATRICES))
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


def test_excellon_rigid_moves_the_hole_shapes(app):
    exc = make_excellon(app)
    before = [t['solid_geometry'] for t in exc.tools.values()]
    apply_matrix_to_object(exc, MATRICES['rigid'])
    apply_matrix_to_object(exc, invert(MATRICES['rigid']))
    for old, tool in zip(before, exc.tools.values()):
        for g0, g1 in zip(old, tool['solid_geometry']):
            assert g0.equals_exact(g1, 1e-6)


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


@pytest.mark.parametrize('kind', ['gerber', 'excellon'])
@pytest.mark.parametrize('workflow', ['align', 'camera'])
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
