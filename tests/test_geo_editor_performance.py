"""Geometry editor batch edits: real storage and geometry, recording canvas."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtWidgets
import shapely
from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, box
from shapely.ops import unary_union

from appEditors.appGeoEditor import AppGeoEditor, FCMove, FCCopy, FCEraser, DrawToolShape, FCCircle, FCRectangle
from appEditors.geo_plugins.GeoBufferPlugin import BufferSelectionTool
from appEditors.geo_plugins.GeoPaintPlugin import PaintEditorUI, PaintOptionsTool
from appEditors.geo_plugins.GeoTransformationPlugin import TransformEditorTool
from camlib import Geometry, translate_geometry
from test_geo_editor_delete_plot import line, selection_editor
from test_geo_editor_copy import copy_tool, radio


def batch_editor(shapes, selected=()):
    editor = selection_editor(shapes, selected=selected)
    editor.app.proc_container = SimpleNamespace(new=lambda *args: nullcontext())
    editor.app.inform = Mock()
    editor.app.inform_shell = Mock()
    editor.app.worker_task = SimpleNamespace(emit=lambda job: job['fcn'](*job['params']))
    editor.build_ui_sig = Mock()
    editor._collect_shape = lambda shape, dest: AppGeoEditor._collect_shape(editor, shape, dest)
    editor.add_shape = lambda shape, **kwargs: AppGeoEditor.add_shape(editor, shape, **kwargs)
    editor.transform_shapes = lambda *args, **kwargs: AppGeoEditor.transform_shapes(editor, *args, **kwargs)
    editor.storage.replace = Mock(wraps=editor.storage.replace)
    editor.flatten = lambda *args, **kwargs: AppGeoEditor.flatten(editor, *args, **kwargs)
    return editor












def test_copy_2d_array_uses_relative_offsets():
    tool = copy_tool([line(0)], mode='a')
    ui = tool.copy_tool.ui
    ui.array_type_radio = radio('2D')
    ui.placement_radio = radio('o')
    ui.rows, ui.columns = radio(2), radio(2)
    ui.spacing_rows, ui.spacing_columns = radio(0), radio(0)
    ui.offsetx_entry, ui.offsety_entry = radio(10), radio(20)
    tool.sel_limit = 0
    tool.click((5, 5))
    tool.click((8, 9))

    assert [shape.geo.bounds[:2] for shape in tool.geometry] == [(3, 4), (13, 4), (3, 24), (13, 24)]




def test_buffer_accepts_multiline_sources():
    shape = DrawToolShape(MultiLineString([[(0, 0), (1, 0)], [(0, 2), (1, 2)]]))
    editor = batch_editor([shape], selected=[shape])
    editor.app.options.update({'geometry_circle_steps_mode': 'fixed', 'geometry_circle_steps': 32})

    BufferSelectionTool.buffer(SimpleNamespace(app=editor.app, draw_app=editor), 0.1, 'round')

    assert len(list(editor.storage.get_objects())) == 3
    editor.storage.replace.assert_called_once()




















def test_batch_translation_preserves_mixed_dimensions_and_none():
    line_2d = LineString([(0, 0), (1, 1)])
    line_3d = LineString([(0, 0, 7), (1, 1, 8)])

    result = translate_geometry([line_2d, None, line_3d], 2, 3)

    assert list(result[0].coords) == [(2, 3), (3, 4)]
    assert result[1] is None
    assert list(result[2].coords) == [(2, 3, 7), (3, 4, 8)]
    assert list(translate_geometry(line_3d, 2, 3).coords) == [(2, 3, 7), (3, 4, 8)]




def test_buffer_follows_the_adaptive_circle_preference():
    counts = {}
    for mode, distance in (('adaptive', 0.05), ('adaptive', 5.0), ('fixed', 0.05), ('fixed', 5.0)):
        shape = DrawToolShape(LineString([(0, 0), (1, 0)]))
        editor = batch_editor([shape], selected=[shape])
        editor.app.options.update({'geometry_circle_steps_mode': mode, 'geometry_circle_steps': 32})
        BufferSelectionTool.buffer(SimpleNamespace(app=editor.app, draw_app=editor), distance, 'round')
        counts[(mode, distance)] = max(len(s.geo.coords) for s in editor.storage.get_objects()
                                       if s.geo.geom_type in ('LinearRing', 'LineString'))

    assert counts[('adaptive', 0.05)] < counts[('adaptive', 5.0)]
    assert counts[('fixed', 0.05)] == counts[('fixed', 5.0)]


def test_paint_refuses_a_tool_diameter_of_zero_or_less():
    # a negative diameter made the Standard method grow its passes outwards forever; zero failed or painted the outline
    for dia, accepted in ((0.0, False), (-0.5, False), (0.5, True)):
        tool = SimpleNamespace(app=SimpleNamespace(inform=Mock()), paint=Mock(return_value=None),
                               fcdraw=SimpleNamespace(selected=[line(0)], select_tool=Mock()))
        tool.ui = SimpleNamespace(painttooldia_entry=radio(dia), paintoverlap_entry=radio(15.0),
                                  paintmargin_entry=radio(0.0), paintmethod_combo=radio('Standard'),
                                  paintcontour_cb=radio(True), pathconnect_cb=radio(False))

        PaintOptionsTool.on_paint(tool)

        assert tool.paint.called is accepted
        if not accepted:
            assert 'greater than zero' in tool.app.inform.emit.call_args.args[0]


def test_paint_uses_the_method_of_the_preferences():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841 - kept for the widgets
    app = SimpleNamespace(decimals=4, options={},
                          defaults=SimpleNamespace(get=lambda key, default=None: default, factory_defaults={}))
    tool = SimpleNamespace(app=app)
    tool.ui = PaintEditorUI(layout=QtWidgets.QVBoxLayout(), paint_class=tool)
    # the preference is an index: 0 Standard, 1 Seed, 2 Lines; Laser_lines (3) and Combo (4) are not in the editor
    for preference, method in ((0, 'Standard'), (1, 'Seed'), (2, 'Lines'), (4, 'Standard')):
        app.options = {'tools_paint_method': preference}
        PaintOptionsTool.set_tool_ui(tool)
        assert tool.ui.paintmethod_combo.get_value() == method


def test_paint_lines_method_paints_in_the_editor():
    # the editor (not a camlib Geometry) is the self of clear_polygon_lines(): a class constant read through self
    # failed there, nothing was painted and the Paint still said "Done."
    square = DrawToolShape(box(0, 0, 10, 10).exterior)
    editor = batch_editor([square], selected=[square])
    editor.app.abort_flag = False
    editor.app.options.update({'geometry_circle_steps_mode': 'fixed', 'geometry_circle_steps': 32})

    PaintOptionsTool.paint(SimpleNamespace(fcdraw=editor), 1.0, 0.1, 0.0, connect=False, contour=False,
                           method='Lines')

    painted = [shape.geo for shape in editor.storage.get_objects() if shape is not square]
    # the scan lines, 0.5 mm (the tool radius) from the edges and 0.9 mm apart
    assert len(painted) == 11
    assert all(box(0.4, 0.4, 9.6, 9.6).contains(line_geo) for line_geo in painted)
    editor.app.log.error.assert_not_called()
