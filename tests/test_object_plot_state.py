"""Stage 5 item 1 (free parts): plot() reads its state from obj_options, not from UI widgets.

Set the environment variable STAGE5_OLD_CODE=1 to run the batch-equality tests on the code before the change: the
widgets then get real values (Mock widgets) instead of the stub that raises on any access.
"""
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6 import QtGui, QtWidgets

from appGUI.GUIElements import FCButton, FCCheckBox
from appGUI.VisPyVisuals import ShapeGroup
from appObjects.ExcellonObject import ExcellonObject
from tests.helpers_objects import _headless_qt, make_app, make_excellon  # noqa: F401,E402

OLD_CODE = os.environ.get('STAGE5_OLD_CODE') == '1'


class RaisingUI:
    """Any access to the UI is a failure: plot() must not need it."""

    def __getattr__(self, name):
        raise AssertionError('plot() touched the UI: %s' % name)


def _widget(value):
    w = Mock()
    w.get_value.return_value = value
    return w


def _ui_for(settings):
    """The UI of the object: a raising stub on the new code, real widget values on the old code."""
    if not OLD_CODE:
        return RaisingUI()
    ui = Mock()
    ui.multicolored_cb = _widget(settings.get('multicolored', False))
    ui.solid_cb = _widget(settings.get('solid', False))
    ui.plot_cb = _widget(settings.get('plot', True))
    ui.follow_cb = _widget(settings.get('follow', False))
    return ui


def _counter_colors():
    state = {'n': 0}

    def random_color():
        state['n'] += 1
        return 'c%d' % state['n']
    return random_color


def _valid_counter_colors():
    state = {'n': 0}

    def random_color():
        state['n'] += 1
        return (state['n'] / 255, 0.0, 0.0, 1.0)
    return random_color


def _prepare_excellon(settings, with_ui=True):
    obj = make_excellon(make_app())
    obj.obj_options.update(settings)
    obj.ui = _ui_for(settings) if with_ui else None
    obj.random_color = _counter_colors()
    obj.fill_color = '#fill'
    obj.outline_color = '#line'
    obj.add_shapes_batch = Mock(side_effect=lambda batch, **kw: list(range(100, 100 + len(batch))))
    counter = {'n': 0}

    def add_shape(**kw):
        counter['n'] += 1
        return counter['n']
    obj.add_shape = Mock(side_effect=add_shape)
    obj.shapes = Mock(_indexes=[])
    return obj


def _batch_of(obj):
    return [(d['shape'].wkt, d['color'], d['face_color'], d['layer']) for d in obj.add_shapes_batch.call_args.args[0]]


def test_a_tool_color_survives_a_full_plot_and_an_invalid_one_is_replaced():
    obj = _prepare_excellon({'solid': True, 'multicolored': True, 'plot': True})
    obj.random_color = _valid_counter_colors()
    selected = (0.1, 0.2, 0.3, 1.0)
    obj.tools[1]['multicolor'] = selected

    obj.plot()
    random_colors = {tool: data['multicolor'] for tool, data in obj.tools.items() if tool != 1}
    obj.plot()

    # a color chosen with the tool swatch (and a random color of the first plot) is kept by a full replot
    assert obj.tools[1]['multicolor'] == selected
    assert all(obj.tools[tool]['multicolor'] == color for tool, color in random_colors.items())
    assert selected in [entry[1] for entry in _batch_of(obj)]

    # an invalid stored value gets a new random color instead of breaking the plot
    obj.tools[1]['multicolor'] = (2.0, 0.0, 0.0, 1.0)
    obj.plot()
    assert obj.tools[1]['multicolor'] != (2.0, 0.0, 0.0, 1.0)
    assert ExcellonObject._valid_tool_color(obj.tools[1]['multicolor'])
    assert obj.tools[1]['multicolor'] in [entry[1] for entry in _batch_of(obj)]


def _color_dialog(color, accepted):
    class Dialog:
        ColorDialogOption = QtWidgets.QColorDialog.ColorDialogOption

        def __init__(self, *args, **kwargs):
            pass

        def setOption(self, *args):
            pass

        def setCurrentColor(self, color):
            pass

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted if accepted else QtWidgets.QDialog.DialogCode.Rejected

        def selectedColor(self):
            return color
    return Dialog


@pytest.mark.parametrize('solid, multicolored, hidden', [(True, False, False), (False, True, True)])
@pytest.mark.parametrize('deferred', [True])
def test_tool_color_swatch_updates_the_tool_color_and_its_shapes(monkeypatch, solid, multicolored, hidden, deferred):
    from appGUI.VisPyVisuals import ShapeCollection
    from vispy.gloo.context import FakeCanvas

    canvas = FakeCanvas()
    obj = make_excellon(make_app())
    obj.obj_options.update(solid=solid, multicolored=multicolored, plot=True)
    obj.ui = None
    obj.fill_color = '#00FF00FF'
    obj.outline_color = '#000000FF'
    collection = ShapeCollection(pool=None, deferred=deferred,
                                 fcoptions={'global_graphic_engine_3d_no_mp': True})
    obj.shapes = ShapeGroup(collection)
    obj.plot()
    collection.flush()
    tool_no = list(obj.tools)[1]

    def painted_colors():
        # These are the merged arrays consumed by VisPy, not the per-shape storage.
        return ([line._color.copy() if line.visible else np.empty((0, 4)) for line in collection._lines],
                [mesh.vertex_colors.copy() if mesh.visible else np.empty((0, 4))
                 for mesh in collection._meshes])

    before_lines, before_meshes = painted_colors()
    expected_lines = [colors.copy() for colors in before_lines]
    expected_meshes = [colors.copy() for colors in before_meshes]
    line_offsets, mesh_offsets = [0] * 3, [0] * 3
    selected = np.array((10 / 255, 20 / 255, 30 / 255, 128 / 255))
    for key, data in collection.data.items():
        layer = data['layer']
        n_lines, n_faces = len(data['line_pts']), len(data['mesh_vertices'])
        if key in obj.shape_indexes_dict[tool_no]:
            expected_lines[layer][line_offsets[layer]:line_offsets[layer] + n_lines] = selected
            expected_meshes[layer][mesh_offsets[layer]:mesh_offsets[layer] + n_faces] = selected
        line_offsets[layer] += n_lines
        mesh_offsets[layer] += n_faces

    swatch = FCButton()
    monkeypatch.setattr(QtWidgets, 'QColorDialog', _color_dialog(QtGui.QColor(10, 20, 30, 128), True))
    if hidden:
        obj.shapes.visible = False
        collection.flush()

    ExcellonObject.on_tool_color_clicked(obj, tool_no, swatch)

    assert obj.tools[tool_no]['multicolor'] == pytest.approx(selected)
    assert 'rgba(10, 20, 30, 128)' in swatch.styleSheet()
    # a bare QPushButton rule would also style the buttons of the color dialog, which is parented to the swatch
    assert 'QPushButton {' not in swatch.styleSheet()

    def assert_painted_colors():
        lines, meshes = painted_colors()
        for actual, expected in zip(lines + meshes, expected_lines + expected_meshes):
            np.testing.assert_allclose(actual, expected, atol=1e-6)

    if hidden:
        assert not any(line.visible for line in collection._lines)
        assert not any(mesh.visible for mesh in collection._meshes)
        obj.shapes.visible = True
        collection.flush()
    assert_painted_colors()
    obj.shapes.visible = False
    obj.shapes.visible = True
    collection.flush()
    assert_painted_colors()
    obj.plot()
    collection.flush()
    assert_painted_colors()

    # Explicitly switching multicolor off still restores the ordinary object colors.
    checkbox = FCCheckBox()
    obj.form_fields['multicolored'] = checkbox
    obj.obj_options['multicolored'] = True
    checkbox.set_value(False)
    obj.muted_ui = False
    monkeypatch.setattr(obj, 'build_ui', Mock())
    obj.on_multicolored_cb_click(0)
    collection.flush()
    assert not obj.obj_options['multicolored']
    assert all(data['multicolor'] is None for data in obj.tools.values())
    lines, meshes = painted_colors()
    if solid:
        assert np.all(lines[2] == (0.0, 0.0, 0.0, 1.0))
        assert np.all(meshes[2] == (0.0, 1.0, 0.0, 1.0))
    else:
        assert np.all(lines[1] == (1.0, 0.0, 0.0, 1.0))


# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------


