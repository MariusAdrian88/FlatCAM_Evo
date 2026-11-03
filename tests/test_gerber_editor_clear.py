"""The Gerber editor and the clear (LPC) features: an object that holds them is loaded as its composed geometry
(regions), so a save does not lose them. Everything is headless."""

import re
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore
from shapely.geometry import Point, box

from appEditors.appGerberEditor import AppGerberEditor, EraserEditorGrb, ImportEditorGrb
from appEditors.grb_plugins.GrbTransformationPlugin import affine_selection
from camlib import affine_translate_matrix
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appHandlers.appIO import appIO

from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_poly_equiv, export_gerber_text, _headless_qt, make_gerber, MIXED_GBR, NATIVE_GBR, polygon_union,
    reparse_gerber, sym_diff_area,
)

SLOT = Point(15, 33)        # on the track of the 6.0 aperture, cut by the clear slot
REFILLED = Point(15, 35)    # the later dark disc fills the middle again


def snapshot(tools):
    """A comparable text of the tools (the addresses of the macro objects are dropped)."""
    return re.sub(r' at 0x[0-9A-Fa-f]+', '', repr(tools))


def editor_save(app, source, tools, loaded_as_regions=False):
    """The real build_edited_gerber() (the worker step of the save) on a stand-in editor, fed with a storage built
    from ``tools`` (what the editor holds after the load). Returns the detached edited Gerber object."""
    storage = {}
    for ap, tool in tools.items():
        storage[ap] = {k: v for k, v in tool.items() if k != 'geometry'}
        storage[ap]['geometry'] = [SimpleNamespace(geo=dict(rec)) for rec in tool['geometry']]
    app.app_units = 'MM'
    app.f_handlers = appIO(app)
    app.custom_signal = MagicMock()
    app.main_thread = QtCore.QThread.currentThread()
    stand_in = SimpleNamespace(app=app, gerber_obj=source, gerber_obj_options=source.obj_options, results=[1],
                               loaded_as_regions=loaded_as_regions)
    AppGerberEditor.build_edited_gerber(stand_in, 'edited', storage)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'obj' in result, result
    return result['obj']


class TestBuildEditTools:
    def test_clear_object_is_loaded_as_its_composed_geometry(self, app):
        source = make_gerber(app, MIXED_GBR)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry)
        assert set(tools) == {0}
        assert tools[0]['type'] == 'REG' and tools[0]['size'] == 0.0
        records = tools[0]['geometry']
        assert records and all('solid' in rec and 'follow' in rec and 'clear' not in rec for rec in records)
        union = polygon_union([rec['solid'] for rec in records])
        assert sym_diff_area(union, source.solid_geometry) < 1e-6
        assert not union.contains(SLOT)
        assert union.contains(REFILLED)


class TestEditorSaveWithClear:
    @pytest.mark.parametrize('apertures', [True])
    def test_loaded_as_regions_keeps_the_composed_geometry(self, app, tmp_path, apertures):
        source = make_gerber(app, MIXED_GBR)
        before = snapshot(source.tools)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry, apertures=apertures)
        assert snapshot(source.tools) == before
        assert all('clear' not in rec for tool in tools.values() for rec in tool['geometry'])
        edited = editor_save(app, source, tools, loaded_as_regions=True)
        assert_poly_equiv(edited.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='edited')
        union = polygon_union(edited.solid_geometry)
        assert not union.contains(SLOT) and union.contains(REFILLED)
        assert edited.export_as_regions is True
        again = reparse_gerber(app, edited.source_file, tmp_path)
        assert_poly_equiv(again.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='source text')
        exported = reparse_gerber(app, export_gerber_text(app, edited), tmp_path, name='exported')
        assert_poly_equiv(exported.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='export')


    @pytest.mark.parametrize('has_follow, loaded_as_regions', [(True, True)])
    def test_split_region_can_be_saved(self, app, tmp_path, has_follow, loaded_as_regions):
        source = make_gerber(app, MIXED_GBR)
        region = box(0, 0, 10, 10).difference(box(2, 2, 4, 4))
        record = {'solid': region}
        if has_follow:
            record['follow'] = region.exterior
        draw_app = MagicMock()
        draw_app.storage_dict = {0: {'type': 'REG', 'size': 0.0, 'geometry': [DrawToolShape(record)]}}
        eraser = SimpleNamespace(draw_app=draw_app, app=app, destination=(0, 0))
        eraser.utility_geometry = lambda data: SimpleNamespace(geo=[{'solid': box(5, -1, 6, 11)}])
        EraserEditorGrb.make(eraser)
        records = [shape.geo for shape in draw_app.storage_dict[0]['geometry']]
        assert records[0]['solid'].geom_type == 'MultiPolygon'
        expected = polygon_union([rec['solid'] for rec in records])
        edited = editor_save(app, source, {0: {'type': 'REG', 'size': 0.0, 'geometry': records}},
                             loaded_as_regions=loaded_as_regions)
        assert_poly_equiv(edited.solid_geometry, expected)
        again = reparse_gerber(app, edited.source_file, tmp_path)
        assert_poly_equiv(again.solid_geometry, expected, abs_tol=1e-4)


def import_tool(app, source):
    source.obj_options['plot'] = True
    app.collection.get_list.return_value = [source]
    app.tool_shapes = MagicMock()
    draw_app = MagicMock()
    draw_app.gerber_obj = SimpleNamespace(obj_options={'name': 'target'})
    draw_app.storage_dict = {}
    draw_app.loaded_as_regions = False
    draw_app.get_sel_color.return_value = '#FF0000'
    tool = SimpleNamespace(app=app, draw_app=draw_app, sel_storage=[])
    tool.get_selected_geos = MethodType(ImportEditorGrb.get_selected_geos, tool)
    tool.get_selected_shape_id = MethodType(ImportEditorGrb.get_selected_shape_id, tool)
    return tool


class TestImportWithClear:

    @pytest.mark.parametrize('selection_type', [False])
    def test_box_import_preserves_clear_and_refilled_areas(self, app, tmp_path, selection_type):
        source = make_gerber(app, MIXED_GBR)
        tool = import_tool(app, source)
        ImportEditorGrb.selection_area_handler(tool, (-100, -100), (100, 100), selection_type)
        assert tool.sel_storage
        # Import islands before their enclosing regions, as independent clicks can do.
        tool.sel_storage.reverse()
        ImportEditorGrb.import_shapes(tool)
        tools = {ap: {**value, 'geometry': [shape.geo for shape in value['geometry']]}
                 for ap, value in tool.draw_app.storage_dict.items()}
        edited = editor_save(app, source, tools, loaded_as_regions=tool.draw_app.loaded_as_regions)
        assert_poly_equiv(edited.solid_geometry, source.solid_geometry)
        again = reparse_gerber(app, edited.source_file, tmp_path)
        assert_poly_equiv(again.solid_geometry, source.solid_geometry, abs_tol=1e-3)

    @pytest.mark.parametrize('selection', ['click'])
    def test_transforming_import_does_not_change_donor(self, app, selection):
        source = make_gerber(app, NATIVE_GBR)
        before = snapshot(source.tools)
        tool = import_tool(app, source)
        if selection == 'click':
            solid = next(rec['solid'] for value in source.tools.values() for rec in value['geometry']
                         if 'solid' in rec)
            point = solid.representative_point()
            ImportEditorGrb.select_handler(tool, (point.x, point.y))
        else:
            ImportEditorGrb.selection_area_handler(tool, (-100, -100), (100, 100), selection == 'enclose')
        assert tool.sel_storage
        ImportEditorGrb.import_shapes(tool)
        tool.draw_app.selected = [shape for value in tool.draw_app.storage_dict.values()
                                  for shape in value['geometry']]
        original = tool.draw_app.selected[0].geo['solid']
        affine_selection(tool.draw_app.selected, affine_translate_matrix(100, 0))
        assert tool.draw_app.selected[0].geo['solid'].bounds[0] == pytest.approx(original.bounds[0] + 100)
        assert snapshot(source.tools) == before
