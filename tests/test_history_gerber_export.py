"""Real Gerber writer/parser round trips after matrix undo and redo."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from appHandlers.appHistory import HistoryManager, transform_metadata, apply_matrix_to_object, states_equal
from camlib import affine_scale_matrix, affine_rotate_matrix, affine_skew_matrix
from test_app_history import fake_app
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_poly_equiv, export_gerber_text, _headless_qt, make_gerber, NATIVE_GBR, reparse_gerber,
)


@pytest.mark.parametrize('operation', ['scale', 'quarter_turn', 'skew'])
def test_export_roundtrip_after_matrix_undo_redo(app, tmp_path, operation):
    # fixed steps: adaptive steps follow the radius, so a scaled coarse circle and a re-parsed bigger circle differ in tessellation
    app.options['gerber_circle_steps_mode'] = 'fixed'
    obj = make_gerber(app, NATIVE_GBR)
    original = obj.solid_geometry
    before = transform_metadata(obj)
    if operation == 'scale':
        matrix = affine_scale_matrix(2, 2, (0, 0))
        obj.scale(2)
    elif operation == 'quarter_turn':
        matrix = affine_rotate_matrix(90, (0, 0))
        obj.rotate(90, (0, 0))
    elif operation == 'nonuniform':
        matrix = affine_scale_matrix(2, 3, (0, 0))
        obj.scale(2, 3)
    elif operation == 'skew':
        matrix = affine_skew_matrix(10, 0, (0, 0))
        obj.skew(10, 0, (0, 0))
    else:
        matrix = affine_rotate_matrix(90, (0, 0))
        apply_matrix_to_object(obj, matrix)
    after = transform_metadata(obj)
    changed = obj.solid_geometry
    obj.plot = Mock()
    obj.plot_transformed = Mock()
    h = HistoryManager(fake_app(), SimpleNamespace(get_list=lambda: [obj]))
    h.app.history = h
    h.app.app_obj = SimpleNamespace(object_changed=Mock())
    h.record_matrix('transform', [(obj, matrix, before)])
    assert h.undo()
    assert states_equal(transform_metadata(obj), before)
    undone = reparse_gerber(app, export_gerber_text(app, obj), tmp_path, 'undone')
    assert_poly_equiv(undone.solid_geometry, original, abs_tol=1e-4)
    assert h.redo()
    assert states_equal(transform_metadata(obj), after)
    redone = reparse_gerber(app, export_gerber_text(app, obj), tmp_path, 'redone')
    assert_poly_equiv(redone.solid_geometry, changed, abs_tol=1e-4)
    if operation in ('quarter_turn', 'alignment'):
        for key, tool in obj.tools.items():
            if tool['type'] in ('R', 'O'):
                assert redone.tools[key]['width'] == pytest.approx(tool['width'])
                assert redone.tools[key]['height'] == pytest.approx(tool['height'])
    if operation == 'skew':
        assert obj.export_as_regions
