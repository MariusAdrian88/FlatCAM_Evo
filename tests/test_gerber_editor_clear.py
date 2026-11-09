"""The Gerber editor and the clear (LPC) features: an object that holds them is loaded as its composed geometry
(regions), so a save does not lose them. Everything is headless."""

import re
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore
from shapely.affinity import rotate
from shapely.geometry import LineString, Point, Polygon, box

from appEditors.appGerberEditor import AppGerberEditor, EraserEditorGrb, ImportEditorGrb
from appEditors.grb_plugins.GrbTransformationPlugin import affine_selection
from camlib import affine_translate_matrix
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appHandlers.appIO import appIO
from appParsers.ParseGerber import Gerber

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
    def test_clear_object_keeps_intact_flashes_and_moves_damaged_material_to_regions(self, app):
        source = make_gerber(app, MIXED_GBR)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry, apertures=True)
        # 17 is a flash the clear slot only touches: it is intact material and keeps its aperture
        assert {11, 12, 17}.issubset(tools)
        assert all(isinstance(rec.get('follow'), Point) for ap in (11, 12, 17)
                   for rec in tools[ap]['geometry'])
        assert any(isinstance(rec.get('follow'), Point) for rec in tools[10]['geometry'])
        assert not tools[16]['geometry'], 'the clear-cut track is region material'
        assert 0 in tools and tools[0]['type'] == 'REG' and tools[0]['size'] == 0.0
        assert all('clear' not in rec for tool in tools.values() for rec in tool['geometry'])
        union = polygon_union([rec['solid'] for tool in tools.values() for rec in tool['geometry']])
        assert sym_diff_area(union, source.solid_geometry) < 1e-6
        assert not union.contains(SLOT)
        assert union.contains(REFILLED)

    def test_clear_object_can_still_be_loaded_as_its_composed_geometry(self, app):
        source = make_gerber(app, MIXED_GBR)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry, apertures=False)
        assert set(tools) == {0}
        assert tools[0]['type'] == 'REG' and tools[0]['size'] == 0.0
        records = tools[0]['geometry']
        assert records and all('solid' in rec and 'follow' in rec and 'clear' not in rec for rec in records)
        union = polygon_union([rec['solid'] for rec in records])
        assert sym_diff_area(union, source.solid_geometry) < 1e-6


class TestSmallCuts:
    def test_translated_micro_cut_is_not_coordinate_scale_noise(self, app):
        solid = box(1000000, 0, 1000100, 100)
        cut = box(1000050, 50, 1000050.001, 50.05)
        tools = {10: {'type': 'R', 'width': 100.0, 'height': 100.0,
                      'geometry': [{'solid': solid, 'follow': Point(1000050, 50)}]}}
        clipped = AppGerberEditor.clip_edit_tools(tools, solid.difference(cut), [cut])
        union = polygon_union([rec['solid'] for tool in clipped.values() for rec in tool['geometry']])
        assert not union.contains(Point(1000050.0005, 50.025))

    def test_a_micro_cut_inside_a_large_record_is_not_lost(self, app, tmp_path):
        path = tmp_path / 'micro_cut.gbr'
        path.write_text('\n'.join([
            '%FSLAX46Y46*%', '%MOMM*%', '%ADD10R,100.0X100.0*%', '%ADD11R,0.001X0.05*%', 'G01*',
            '%LPD*%', 'D10*', 'X50000000Y50000000D03*',
            '%LPC*%', 'D11*', 'X50000500Y50025000D03*', '%LPD*%', 'M02*']))
        source = make_gerber(app, str(path))
        assert not polygon_union(source.solid_geometry).contains(Point(50.0005, 50.025))
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry, apertures=True)
        union = polygon_union([rec['solid'] for tool in tools.values() for rec in tool.get('geometry', ())])
        # the removed 5e-5 area is a real edit, not a sliver of the 10000 area record
        assert not union.contains(Point(50.0005, 50.025))
        assert sym_diff_area(union, source.solid_geometry) < 1e-6

    def test_a_shallow_edge_cut_of_a_large_record_is_not_lost(self, app, tmp_path):
        path = tmp_path / 'edge_cut.gbr'
        path.write_text('\n'.join([
            '%FSLAX46Y46*%', '%MOMM*%', '%ADD10R,300.0X300.0*%', '%ADD11R,0.000004X100.0*%', 'G01*',
            '%LPD*%', 'D10*', 'X150000000Y150000000D03*',
            '%LPC*%', 'D11*', 'X300000000Y150000000D03*', '%LPD*%', 'M02*']))
        source = make_gerber(app, str(path))
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry, apertures=True)
        union = polygon_union([rec['solid'] for tool in tools.values() for rec in tool.get('geometry', ())])
        # a 2e-6 deep cut is below any area percentage of the 300 mm square but still a real cut
        assert not union.contains(Point(299.9999995, 150))
        assert sym_diff_area(union, source.solid_geometry) < 1e-6

    def test_a_numeric_graze_does_not_cut_a_record(self, app):
        solid = box(0, 0, 100, 100)
        # the clear feature overlaps the record by 1e-9: only the numeric sliver of the boundary is removed
        graze = box(99.999999999, 50, 101, 60)
        tools = {10: {'type': 'R', 'width': 100.0, 'height': 100.0,
                      'geometry': [{'solid': solid, 'follow': Point(50, 50)}]}}
        clipped = AppGerberEditor.clip_edit_tools(tools, solid, [graze])
        record = clipped[10]['geometry'][0]
        assert record['follow'].equals(Point(50, 50))
        assert record['solid'].equals(solid)

    def test_eraser_cuts_a_micro_cut_inside_a_large_record(self, app):
        solid = box(0, 0, 100, 100)
        draw_app = MagicMock()
        draw_app.storage_dict = {0: {'type': 'REG', 'size': 0.0, 'geometry': [
            DrawToolShape({'solid': solid, 'follow': Point(50, 50)})]}}
        eraser = SimpleNamespace(draw_app=draw_app, app=app, destination=(0, 0))
        eraser.utility_geometry = lambda data: SimpleNamespace(geo=[{'solid': box(50, 50, 50.001, 50.05)}])
        EraserEditorGrb.make(eraser)
        union = polygon_union([shape.geo['solid'] for tool in draw_app.storage_dict.values()
                               for shape in tool['geometry']])
        assert not union.contains(Point(50.0005, 50.025))


class TestEditorSaveWithClear:
    @pytest.mark.parametrize('kind', ['C', 'O'])
    def test_save_keeps_mixed_sampling_and_changed_aperture_dimensions(self, app, kind):
        source = make_gerber(app, NATIVE_GBR, name='board')
        source.steps_per_circle = 64
        tools = {}
        for code, size in ((10, 2.0), (11, 4.0)):
            aperture = {'type': kind, 'size': size, 'width': size + 1, 'height': size}
            records = []
            for i, steps in enumerate((64, 128, 52, 128)):
                follow = Point(code * 10 + i * 6, 0)
                records.append({'solid': source.create_flash_geometry(follow, aperture, steps), 'follow': follow})
            tools[code] = dict(aperture, geometry=records)
        for _ in range(2):
            edited = editor_save(app, source, tools)
            assert set(edited.tools) == {10, 11}
            for code in tools:
                assert len(edited.tools[code]['geometry']) == 4
                assert all(saved['solid'].equals(original['solid']) for saved, original in
                           zip(edited.tools[code]['geometry'], tools[code]['geometry']))
            # The next save uses new dimensions and another sampling resolution, not the previous templates.
            for aperture in tools.values():
                aperture['size'] += 0.5
                aperture['width'] += 0.5
                aperture['height'] += 0.5
                for record in aperture['geometry']:
                    record['solid'] = source.create_flash_geometry(record['follow'], aperture, 96)

    @pytest.mark.parametrize('track', [False, True, 'closed'])
    def test_rotated_intact_circle_or_track_keeps_its_stored_resolution(self, app, track):
        source = make_gerber(app, NATIVE_GBR, name='board')
        source.steps_per_circle = 128
        follow = LineString([(0, 0), (3, 0), (3, 3)]) if track else Point(0, 0)
        if track == 'closed':
            follow = LineString([(0, 0), (3, 0), (3, 3), (0, 3), (0, 0)])
        solid = follow.buffer(1, quad_segs=32)
        record = {'solid': rotate(solid, 11, origin=(0, 0)), 'follow': rotate(follow, 11, origin=(0, 0))}
        edited = editor_save(app, source, {10: {'type': 'C', 'size': 2.0, 'geometry': [record]}})
        assert 10 in edited.tools
        assert edited.tools[10]['geometry'][0]['follow'].equals(record['follow'])
        assert 'ADD10C' in edited.source_file
        assert ('D03*' in edited.source_file) == (not track)

    @pytest.mark.parametrize('kind', ['R', 'C', 'O'])
    def test_clipped_outline_vertices_do_not_restore_copper(self, app, tmp_path, kind):
        source = make_gerber(app, NATIVE_GBR, name='board')
        aperture = {'type': kind, 'size': 2.0, 'width': 3.0, 'height': 2.0}
        full = box(-1.5, -1, 1.5, 1) if kind == 'R' else source.create_flash_geometry(Point(0, 0), aperture)
        coords = list(full.exterior.coords)[:-1]
        # Removing one outline vertex replaces two edges by a chord. All surviving vertices are still native.
        clipped = Polygon(coords[1:])
        missing = full.difference(clipped).representative_point()
        edited = editor_save(app, source, {10: dict(aperture, geometry=[
            {'solid': clipped, 'follow': Point(0, 0)}])})
        assert not any(isinstance(rec.get('follow'), Point) for tool in edited.tools.values()
                       for rec in tool['geometry'])
        parsed = reparse_gerber(app, edited.source_file, tmp_path)
        assert not polygon_union(parsed.solid_geometry).contains(missing)

    @pytest.mark.parametrize('gap', [0, 2])
    def test_independent_regions_keep_distinct_object_attributes(self, app, tmp_path, gap):
        source = make_gerber(app, NATIVE_GBR, name='board')
        source.to_attributes = {1: {'.N': ['net_a']}, 2: {'.N': ['net_b']}}
        regions = [box(0, 0, 1, 1), box(1 + gap, 0, 2 + gap, 1)]
        edited = editor_save(app, source, {0: {'type': 'REG', 'size': 0.0, 'geometry': [
            {'solid': solid, 'follow': solid.exterior, 'to': ident}
            for ident, solid in enumerate(regions, 1)]}})
        parsed = reparse_gerber(app, edited.source_file, tmp_path)
        for solid, net in zip(regions, ['net_a', 'net_b']):
            record = next(rec for tool in parsed.tools.values() for rec in tool['geometry']
                          if rec.get('solid') is not None and rec['solid'].contains(solid.centroid))
            assert parsed.to_attributes[record['to']]['.N'] == [net]

    @pytest.mark.parametrize('apertures, loaded_as_regions', [(True, False), (False, True)])
    def test_save_keeps_the_composed_geometry(self, app, tmp_path, apertures, loaded_as_regions):
        source = make_gerber(app, MIXED_GBR)
        before = snapshot(source.tools)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry, apertures=apertures)
        assert snapshot(source.tools) == before
        assert all('clear' not in rec for tool in tools.values() for rec in tool['geometry'])
        edited = editor_save(app, source, tools, loaded_as_regions=loaded_as_regions)
        assert_poly_equiv(edited.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='edited')
        union = polygon_union(edited.solid_geometry)
        assert not union.contains(SLOT) and union.contains(REFILLED)
        # an explicit whole-region load keeps the region export; a mixed storage saves natively
        assert edited.export_as_regions is loaded_as_regions
        again = reparse_gerber(app, edited.source_file, tmp_path)
        assert_poly_equiv(again.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='source text')
        exported = reparse_gerber(app, export_gerber_text(app, edited), tmp_path, name='exported')
        assert_poly_equiv(exported.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='export')

    def test_intact_flashes_mixed_with_clears_survive_save_export_and_reparse(self, app, tmp_path):
        source = make_gerber(app, MIXED_GBR)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry, apertures=True)
        edited = editor_save(app, source, tools, loaded_as_regions=False)
        assert edited.export_as_regions is False
        # the C/R/O apertures whose flashes survived are still declared and drawn as flashes
        assert {10, 11, 12, 17}.issubset(edited.tools)
        assert any(isinstance(rec['follow'], Point) for rec in edited.tools[10]['geometry'])
        assert all(isinstance(rec['follow'], Point) for ap in (11, 12, 17)
                   for rec in edited.tools[ap]['geometry'])
        # the writer-unsupported P and AM apertures and the clear-cut track are written as regions instead
        assert not ({13, 14, 15} & set(edited.tools)) and 16 not in edited.tools
        assert 0 in edited.tools and edited.tools[0]['type'] == 'REG'
        text = edited.source_file
        assert 'ADD11R' in text and 'ADD12O' in text and 'D03*' in text
        assert_poly_equiv(edited.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='edited')
        union = polygon_union(edited.solid_geometry)
        assert not union.contains(SLOT) and union.contains(REFILLED)
        parsed = Gerber(app, steps_per_circle=64)
        assert parsed.parse_lines(text.splitlines()) is None
        assert_poly_equiv(parsed.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='source text')
        exported_text = export_gerber_text(app, edited)
        # the export keeps the same native flashes, not only equivalent copper
        assert 'ADD11R' in exported_text and 'ADD12O' in exported_text and 'D03*' in exported_text
        exported = reparse_gerber(app, exported_text, tmp_path, name='exported')
        assert_poly_equiv(exported.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='export')

    @pytest.mark.parametrize('clear_x', ['15.8', '15.0'])
    def test_cut_flash_leaves_its_aperture_and_the_untouched_flash_survives(self, app, tmp_path, clear_x):
        path = tmp_path / ('cut_%s.gbr' % clear_x)
        path.write_text('\n'.join([
            '%FSLAX46Y46*%', '%MOMM*%', '%ADD10C,2.0*%', '%ADD11C,0.5*%', 'G01*', '%LPD*%', 'D10*',
            'X5000000Y5000000D03*', 'X15000000Y5000000D03*',
            '%LPC*%', 'D11*', 'X%sY5000000D03*' % ('15800000' if clear_x == '15.8' else '15000000'),
            '%LPD*%', 'M02*']))
        source = make_gerber(app, str(path))
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry, apertures=True)
        records = [rec for tool in tools.values() if tool.get('type') != 'REG'
                   for rec in tool.get('geometry', ())]
        # the cut flash is no longer a native flash record of the shared aperture
        assert not any(isinstance(rec.get('follow'), Point) and rec['follow'].equals(Point(15, 5))
                       for rec in records)
        assert any(isinstance(rec.get('follow'), Point) and rec['follow'].equals(Point(5, 5))
                   for rec in records)
        edited = editor_save(app, source, tools, loaded_as_regions=False)
        native = [rec for ap, tool in edited.tools.items() if tool.get('type') in ('C', 'R', 'O')
                  for rec in tool.get('geometry', ()) if isinstance(rec.get('follow'), Point)]
        assert len(native) == 1 and native[0]['follow'].equals(Point(5, 5))
        assert_poly_equiv(edited.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='edited')
        parsed = Gerber(app, steps_per_circle=64)
        assert parsed.parse_lines(edited.source_file.splitlines()) is None
        assert_poly_equiv(parsed.solid_geometry, source.solid_geometry, rel=1e-3, abs_tol=1e-2, msg='source text')
        flashes = [rec['follow'] for tool in parsed.tools.values() if tool.get('type') in ('C', 'R', 'O')
                   for rec in tool.get('geometry', ()) if isinstance(rec.get('follow'), Point)]
        assert [point for point in flashes if point.equals(Point(15, 5))] == []

    def test_save_never_turns_a_cut_flash_with_a_stale_point_into_a_D03(self, app, tmp_path):
        source = make_gerber(app, NATIVE_GBR, name='board')
        full = source.create_flash_geometry(Point(15, 5), {'type': 'C', 'size': 2.0})
        cut = full.difference(Point(15.8, 5).buffer(0.25, 64))
        untouched = source.create_flash_geometry(Point(5, 5), {'type': 'C', 'size': 2.0})
        storage = {10: {'type': 'C', 'size': 2.0, 'geometry': [
            {'solid': cut, 'follow': Point(15, 5)},
            {'solid': untouched, 'follow': Point(5, 5)}]}}
        edited = editor_save(app, source, storage, loaded_as_regions=False)
        assert edited.export_as_regions is False
        native = [rec for tool in edited.tools.values() if tool.get('type') in ('C', 'R', 'O')
                  for rec in tool.get('geometry', ()) if isinstance(rec.get('follow'), Point)]
        assert len(native) == 1 and native[0]['follow'].equals(Point(5, 5))
        assert 0 in edited.tools and edited.tools[0]['type'] == 'REG'
        expected = cut.union(untouched)
        assert polygon_union(edited.solid_geometry).symmetric_difference(expected).area < 1e-6
        assert_poly_equiv(reparse_gerber(app, edited.source_file, tmp_path).solid_geometry, expected,
                          rel=1e-4, abs_tol=1e-3, msg='source text')

    def test_eraser_cut_flash_drops_the_stale_point_and_keeps_the_untouched_flash(self, app, tmp_path):
        source = make_gerber(app, NATIVE_GBR, name='board')
        full = source.create_flash_geometry(Point(15, 5), {'type': 'C', 'size': 2.0})
        untouched = source.create_flash_geometry(Point(5, 5), {'type': 'C', 'size': 2.0})
        draw_app = MagicMock()
        draw_app.storage_dict = {10: {'type': 'C', 'size': 2.0, 'geometry': [
            DrawToolShape({'solid': full, 'follow': Point(15, 5)}),
            DrawToolShape({'solid': untouched, 'follow': Point(5, 5)})]}}
        eraser = SimpleNamespace(draw_app=draw_app, app=app, destination=(0, 0))
        # the eraser cuts the right edge of the first flash but not its center
        eraser.utility_geometry = lambda data: SimpleNamespace(geo=[{'solid': box(15.6, 4.4, 16.4, 5.6)}])
        EraserEditorGrb.make(eraser)
        # the cut record no longer carries the stale flash center, the intact flash keeps its own
        assert all(not isinstance(shape.geo['follow'], Point) for shape in draw_app.storage_dict[0]['geometry'])
        assert any(shape.geo['follow'].equals(Point(5, 5)) for shape in draw_app.storage_dict[10]['geometry'])
        storage = {ap: {**{k: value for k, value in tool.items() if k != 'geometry'},
                        'geometry': [shape.geo for shape in tool['geometry']]}
                   for ap, tool in draw_app.storage_dict.items()}
        edited = editor_save(app, source, storage, loaded_as_regions=False)
        native = [rec['follow'] for tool in edited.tools.values() if tool.get('type') in ('C', 'R', 'O')
                  for rec in tool.get('geometry', ()) if isinstance(rec.get('follow'), Point)]
        assert native == [Point(5, 5)]
        expected = polygon_union([shape.geo['solid'] for tool in draw_app.storage_dict.values()
                                  for shape in tool['geometry']])
        assert_poly_equiv(reparse_gerber(app, edited.source_file, tmp_path).solid_geometry, expected,
                          rel=1e-4, abs_tol=1e-3, msg='source text')

    def test_a_shallow_notch_in_a_flash_is_saved_as_missing_copper(self, app, tmp_path):
        source = make_gerber(app, NATIVE_GBR, name='board')
        aperture = {'type': 'R', 'width': 2.0, 'height': 2.0}
        full = source.create_flash_geometry(Point(0, 0), aperture)
        notched = full.difference(box(0.998, -0.5, 1.1, 0.5))
        record = {'solid': notched, 'follow': Point(0, 0)}
        editor = SimpleNamespace(app=app, gerber_obj=source)
        # the 0.002 mm notch is not the polygon discretization of the aperture flash
        assert AppGerberEditor._record_is_native(editor, source, aperture, record) is False
        storage = {10: dict(aperture, geometry=[record])}
        edited = editor_save(app, source, storage, loaded_as_regions=False)
        assert_poly_equiv(edited.solid_geometry, notched, rel=1e-4, abs_tol=1e-3, msg='edited')
        assert not polygon_union(edited.solid_geometry).contains(Point(0.999, 0))
        parsed = reparse_gerber(app, edited.source_file, tmp_path)
        assert_poly_equiv(parsed.solid_geometry, notched, rel=1e-3, abs_tol=1e-2, msg='source text')
        assert not polygon_union(parsed.solid_geometry).contains(Point(0.999, 0))

    def test_a_chord_cut_with_the_same_vertex_count_is_not_a_native_flash(self, app):
        source = make_gerber(app, NATIVE_GBR, name='board')
        aperture = {'type': 'C', 'size': 2.0}
        coords = list(source.create_flash_geometry(Point(3, 4), aperture).exterior.coords)[:-1]
        # 2 vertices are replaced by the middles of their outer edges: a chord cut, the same vertex count
        middle = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)  # noqa: E731
        coords[5], coords[6] = middle(coords[4], coords[5]), middle(coords[6], coords[7])
        record = {'solid': Polygon(coords), 'follow': Point(3, 4)}
        editor = SimpleNamespace(app=app, gerber_obj=source)
        assert AppGerberEditor._record_is_native(editor, source, aperture, record) is False

    @pytest.mark.parametrize('region_key', [0, 20])
    def test_overlapping_holed_regions_keep_their_copper_and_islands(self, app, tmp_path, region_key):
        source = make_gerber(app, NATIVE_GBR, name='board')
        flash = source.create_flash_geometry(Point(18, 20), {'type': 'C', 'size': 1.0})
        a = box(0, 0, 10, 10).difference(box(2, 2, 4, 4))
        b = box(3, 0, 11, 8).difference(box(5, 2, 7, 4))
        ring = box(15, 15, 25, 25).difference(box(17, 17, 23, 23))
        island = box(19, 19, 21, 21)
        regions = [a, b, ring, island]
        storage = {
            10: {'type': 'C', 'size': 1.0, 'geometry': [{'solid': flash, 'follow': Point(18, 20)}]},
            region_key: {'type': 'REG', 'size': 0.0,
                'geometry': [{'solid': solid, 'follow': solid.exterior} for solid in regions]},
        }
        edited = editor_save(app, source, storage, loaded_as_regions=False)
        expected = polygon_union([flash] + regions)
        assert_poly_equiv(edited.solid_geometry, expected, rel=1e-4, abs_tol=1e-3, msg='edited')
        # the intact flash stays a native flash
        assert any(isinstance(rec.get('follow'), Point) for rec in edited.tools[10]['geometry'])
        assert 'D03*' in edited.source_file
        for text in (edited.source_file, export_gerber_text(app, edited)):
            parsed = Gerber(app, steps_per_circle=64)
            assert parsed.parse_lines(text.splitlines()) is None
            reparsed = polygon_union(parsed.solid_geometry)
            # B's hole lies in A's copper and the ring's hole holds an island: neither may erase the other
            assert reparsed.contains(Point(6, 3))
            assert reparsed.contains(Point(20, 20))
            assert reparsed.contains(Point(18, 20)), 'the region hole must not erase the native flash'
            assert_poly_equiv(parsed.solid_geometry, expected, rel=1e-4, abs_tol=1e-3, msg='source text')

    @pytest.mark.parametrize('layout', ['touching', 'chain', 'containing', 'nested'])
    def test_region_conflict_groups_keep_material_and_independent_attributes(self, app, tmp_path, layout):
        source = make_gerber(app, NATIVE_GBR, name='board')
        source.to_attributes = {1: {'.N': ['shared']}, 2: {'.N': ['independent']}}
        a = box(0, 0, 10, 10).difference(box(2, 2, 8, 8))
        if layout == 'touching':
            regions = [a, box(10, 0, 20, 10).difference(box(12, 2, 18, 8))]
        elif layout == 'chain':
            regions = [a, box(5, 0, 15, 10).difference(box(7, 2, 13, 8)), box(10, 0, 20, 10)]
        elif layout == 'containing':
            regions = [a, box(-1, -1, 11, 11)]
        else:
            regions = [a, box(3, 3, 7, 7).difference(box(4, 4, 6, 6)), box(4.5, 4.5, 5.5, 5.5)]
        independent = box(30, 0, 40, 10).difference(box(32, 2, 38, 8))
        records = [{'solid': solid, 'follow': solid.exterior, 'to': 1} for solid in regions]
        records.append({'solid': independent, 'follow': independent.exterior, 'to': 2})
        edited = editor_save(app, source, {0: {'type': 'REG', 'size': 0.0, 'geometry': records}})
        expected = polygon_union(regions + [independent])
        assert any(rec.get('to') == 2 and rec['solid'].equals(independent)
                   for rec in edited.tools[0]['geometry'])
        for text in (edited.source_file, export_gerber_text(app, edited)):
            parsed = reparse_gerber(app, text, tmp_path)
            assert sym_diff_area(parsed.solid_geometry, expected) < 1e-6
            for record in (rec for tool in parsed.tools.values() for rec in tool['geometry']
                           if rec.get('solid') is not None and not rec['solid'].is_empty):
                net = 'independent' if record['solid'].centroid.x > 25 else 'shared'
                assert parsed.to_attributes[record['to']]['.N'] == [net]

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
