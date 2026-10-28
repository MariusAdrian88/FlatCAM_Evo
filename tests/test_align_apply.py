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


@pytest.mark.parametrize('kind', ['gerber', 'excellon'])
def test_finish_rewrites_the_source_of_the_aligned_object(app, kind, tmp_path):
    """View Source and Save source write source_file: it has to be the aligned object, not the loaded file."""
    from appHandlers.appIO import appIO
    from appPlugins.ToolAlignObjects.Align import AlignObjects
    from tests.helpers_objects import reparse_excellon, reparse_gerber
    app.f_handlers = appIO(app)
    obj = _make(app, kind)
    assert obj.regenerate_source() is True
    obj.plot_transformed = Mock()
    tool = Mock(start_points=[(0, 0), (10, 0)], dest_points=[(3, 4), (3, 14)], aligned_obj=obj, decimals=4)
    tool._solve = solve_rigid
    tool._after_apply = lambda aligned, matrix: AlignObjects._after_apply(tool, aligned, matrix)
    AlignObjects.finish(tool)
    again = (reparse_gerber if kind == 'gerber' else reparse_excellon)(app, obj.source_file, tmp_path)
    assert np.array(again.bounds()) == pytest.approx(np.array(obj.bounds()), abs=1e-3)
