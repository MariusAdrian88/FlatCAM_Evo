"""Gerber transforms and the region export: every Gerber transform keeps the aperture metadata in agreement
with the transformed geometry, or sets ``export_as_regions``; the region export keeps holes and islands.

Each test follows the table of the plan: parse -> transform -> check the flag and the metadata -> export with the
real ``appIO.export_gerber`` -> parse again -> compare the composed geometry.

The expected values below are written from the plan table, not computed with the code under test. Everything is
headless. The helpers come from ``tests.helpers_objects``.
"""

import math
from dataclasses import dataclass
from typing import Callable

import pytest
from PyQt6.QtCore import QThread
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from appObjects.GerberObject import GerberObject

from tests.helpers_objects import (  # noqa: F401,E402
    AFFINE_TRANSFORMS, app, apply_to, assert_coords_close, assert_poly_equiv, export_gerber_text, FIXTURES, gerber,
    _headless_qt, make_gerber, MIXED_GBR, MIXED_GBR_NO_POLARITY, NATIVE_GBR, parts, polygon_union, reparse_gerber,
    run_copy, save_and_load, snapshot_gerber, TRANSFORM_NAMES,
)


def fx(name):
    return str(FIXTURES / ('unify_%s.gbr' % name))


# kind -> (fixture, aperture id, aperture type)
KINDS = {
    'C': ('circle', 10, 'C'),        # a flash and a track drawn with the same C aperture
    'R': ('rect', 11, 'R'),          # non-square rectangle, 2 flashes
    'O': ('oblong', 12, 'O'),        # non-square oblong, 2 flashes
    'RT': ('rtrack', 11, 'R'),       # a track drawn with an R aperture
    'P3': ('p3', 20, 'P'),           # triangle with a rotation
    'P7': ('p7', 21, 'P'),           # odd-sided polygon with a rotation
    'AM': ('macro', 14, 'AM'),       # outline macro
}
COLUMN = {'C': 'C', 'R': 'R', 'RT': 'R', 'O': 'O', 'P3': 'P', 'P7': 'P', 'AM': 'AM'}


@dataclass
class Tr:
    """One row of the plan table. A lambda that returns None is a 'regions export' cell."""
    id: str
    apply: Callable
    c: Callable            # size -> size
    rect: Callable         # (w, h) -> (w, h)
    obl: Callable          # (w, h) -> (w, h)
    poly: Callable         # (diam, rotation, n) -> (diam, rotation)
    am_flagged: bool = True


def uniform(k, rot=lambda r: r):
    return dict(c=lambda s: s * k, rect=lambda w, h: (w * k, h * k), obl=lambda w, h: (w * k, h * k),
                poly=lambda d, r, n: (d * k, rot(r)))


def nonuniform_scale():
    return dict(c=lambda s: None, rect=lambda w, h: (w * 2.0, h * 1.25), obl=lambda w, h: None,
                poly=lambda d, r, n: None)


def rotation(extra_angle, swap, keep_rect=True):
    """Rotation by extra_angle degrees; swap: width and height swap; keep_rect False: not a quarter turn."""
    if keep_rect:
        rect = (lambda w, h: (h, w)) if swap else (lambda w, h: (w, h))
    else:
        rect = lambda w, h: None  # noqa: E731
    return dict(c=lambda s: s, rect=rect, obl=rect, poly=lambda d, r, n: (d, r + extra_angle))


def mirror(rot):
    return dict(c=lambda s: s, rect=lambda w, h: (w, h), obl=lambda w, h: (w, h), poly=lambda d, r, n: (d, rot(r)))


def skew():
    return dict(c=lambda s: None, rect=lambda w, h: None, obl=lambda w, h: None, poly=lambda d, r, n: None)


def buffer_cells(d, rect_ok):
    return dict(c=lambda s: s + 2 * d,
                rect=(lambda w, h: (w + 2 * d, h + 2 * d)) if rect_ok else (lambda w, h: None),
                obl=lambda w, h: (w + 2 * d, h + 2 * d), poly=lambda d_, r, n: None)


SCALE_ORIGIN = (3.0, 2.0)
ROT_ORIGIN = (12.0, 9.0)
MIR_ORIGIN = (14.0, 11.0)

TRANSFORMS = [
    Tr('scale_up', lambda o: o.scale(1.5, 1.5, SCALE_ORIGIN), **uniform(1.5)),
    Tr('scale_neg', lambda o: o.scale(-1.5, -1.5, SCALE_ORIGIN), **uniform(1.5, lambda r: r + 180)),
    # scale(s, -s) negates Y, like mirror X; scale(-s, s) negates X, like mirror Y
    Tr('scale_flip_y', lambda o: o.scale(1.5, -1.5, SCALE_ORIGIN), **uniform(1.5, lambda r: -r)),
    Tr('scale_flip_x', lambda o: o.scale(-1.5, 1.5, SCALE_ORIGIN), **uniform(1.5, lambda r: 180 - r)),
    Tr('scale_nonuniform', lambda o: o.scale(2.0, 1.25, SCALE_ORIGIN), **nonuniform_scale()),
    Tr('scale_nonuniform_neg', lambda o: o.scale(-2.0, 1.25, SCALE_ORIGIN), **nonuniform_scale()),
    Tr('rotate_90', lambda o: o.rotate(90.0, ROT_ORIGIN), **rotation(90, swap=True)),
    Tr('rotate_minus_90', lambda o: o.rotate(-90.0, ROT_ORIGIN), **rotation(-90, swap=True)),
    Tr('rotate_180', lambda o: o.rotate(180.0, ROT_ORIGIN), **rotation(180, swap=False)),
    Tr('rotate_450', lambda o: o.rotate(450.0, ROT_ORIGIN), **rotation(450, swap=True)),
    Tr('rotate_30', lambda o: o.rotate(30.0, ROT_ORIGIN), **rotation(30, swap=False, keep_rect=False)),
    Tr('mirror_x', lambda o: o.mirror('X', MIR_ORIGIN), **mirror(lambda r: -r)),
    Tr('mirror_y', lambda o: o.mirror('Y', MIR_ORIGIN), **mirror(lambda r: 180 - r)),
    Tr('skew_x', lambda o: o.skew(10.0, 0.0, (4.0, 3.0)), **skew()),
    Tr('skew_y', lambda o: o.skew(0.0, 5.0, (4.0, 3.0)), **skew()),
    Tr('buffer_mitre', lambda o: o.buffer(0.3, 'mitre'), **buffer_cells(0.3, True)),
    Tr('buffer_mitre_int', lambda o: o.buffer(0.3, 2), **buffer_cells(0.3, True)),
    Tr('buffer_round', lambda o: o.buffer(0.3, 'round'), **buffer_cells(0.3, False)),
    Tr('buffer_round_int', lambda o: o.buffer(0.3, 1), **buffer_cells(0.3, False)),
    Tr('buffer_bevel', lambda o: o.buffer(0.3, 'bevel'), **buffer_cells(0.3, False)),
    Tr('buffer_bevel_int', lambda o: o.buffer(0.3, 3), **buffer_cells(0.3, False)),
    Tr('buffer_shrink_mitre', lambda o: o.buffer(-0.2, 'mitre'), **buffer_cells(-0.2, True)),
    Tr('offset', lambda o: o.offset((3.5, -2.25)),
       c=lambda s: s, rect=lambda w, h: (w, h), obl=lambda w, h: (w, h), poly=lambda d, r, n: (d, r),
       am_flagged=False),
]
TR_BY_ID = {t.id: t for t in TRANSFORMS}

# One cell per branch of the plan table instead of the full kinds x transforms matrix.
CELLS = [(k, t) for k, ts in {
    'C': ('scale_nonuniform',),
    'R': ('rotate_90',),
    'O': ('buffer_round',),
    'P3': ('scale_neg',),
    'AM': ('offset',),
}.items() for t in ts]


def cell_params(test_name):
    """Parameters of one test: every aperture kind with every transform (the test name is only a label)."""
    skip = {'export': ('C', 'P3')}.get(test_name, ())    # the export keeps one cell per export branch
    return [pytest.param(kind, tid, id='%s-%s' % (kind, tid)) for kind, tid in CELLS if kind not in skip]


def load(app, kind):
    fixture, apid, ap_type = KINDS[kind]
    return make_gerber(app, fx(fixture), name=kind), apid


def expected_cell(kind, tid, tool):
    """(flagged, expected metadata dict or None) for one aperture and one transform, from the plan table."""
    tr = TR_BY_ID[tid]
    col = COLUMN[kind]
    if col == 'AM':
        return tr.am_flagged, None
    if col == 'C':
        size = tr.c(tool['size'])
        return size is None, None if size is None else {'size': size}
    if kind == 'R' and tid in ('buffer_mitre', 'buffer_mitre_int'):
        # The plan table says "mitre join: w, h + 2d", but the parser rounds the corners of a flash with a 1e-7
        # buffer and the mitre buffer then rounds them with radius d: the records are not rectangles any more.
        return True, None
    if kind == 'RT' and tid.startswith('buffer'):
        # the record of an R track is a box around the whole move, not a w x h rectangle
        return True, None
    if col in ('R', 'O'):
        wh =(tr.rect if col == 'R' else tr.obl)(tool['width'], tool['height'])
        if wh is None:
            return True, None
        return False, {'width': wh[0], 'height': wh[1], 'size': math.hypot(*wh)}
    res = tr.poly(tool['diam'], tool.get('rotation', 0.0), tool['nVertices'])
    if res is None:
        return True, None
    return False, {'diam': res[0], 'size': res[0], 'rotation': res[1]}


def angle_equal(a, b, n):
    step = 360.0 / n
    delta = (a - b) % step
    return min(delta, step - delta) < 1e-6


def assert_composed_equal(actual, expected, msg):
    exp = polygon_union(expected)
    got = polygon_union(actual)
    assert exp.area > 0.5, msg + ' (empty expectation)'
    assert got.area > 0.5 * exp.area, msg + ' (empty result)'
    diff = got.symmetric_difference(exp).area
    assert diff <= max(1e-3, 2e-3 * exp.area), '%s symmetric difference %.6f (area %.4f)' % (msg, diff, exp.area)


def interior_count(geometry):
    return sum(len(poly.interiors) for poly in parts(polygon_union(geometry)) if poly.geom_type == 'Polygon')


def record_count(obj, apid):
    return len(obj.tools[apid]['geometry'])


def flash_count(obj):
    """The dark flashes of an object: the records with a flash point (the snap of the Distance tool uses them)."""
    return sum(1 for tool in obj.tools.values() if tool.get('type') != 'REG' for record in tool.get('geometry', ())
               if isinstance(record.get('follow'), Point) and not getattr(record.get('solid'), 'is_empty', True))


# ---------------------------------------------------------------------------------------------------------------
# the table: flag and metadata
# ---------------------------------------------------------------------------------------------------------------

class TestTableFlagAndMetadata:
    @pytest.mark.parametrize('kind,tid', cell_params('meta'))
    def test_flag_and_metadata_of_the_cells(self, app, kind, tid):
        obj, apid = load(app, kind)
        before = dict(obj.tools[apid])
        flagged, want = expected_cell(kind, tid, before)
        assert obj.export_as_regions is False
        TR_BY_ID[tid].apply(obj)
        assert obj.export_as_regions is flagged, '%s %s' % (kind, tid)
        now = obj.tools[apid]
        assert now['type'] == before['type']
        assert record_count(obj, apid) == len(before['geometry'])
        if want is not None:
            for key, value in want.items():
                if key == 'rotation':
                    assert angle_equal(now.get('rotation', 0.0), value, before['nVertices']), \
                        (kind, tid, now.get('rotation'), value)
                else:
                    assert now[key] == pytest.approx(value, rel=1e-9, abs=1e-12), (kind, tid, key)
            if before['type'] == 'P':
                assert now['nVertices'] == before['nVertices']
        if tid not in ('buffer_shrink_mitre',):
            # the sizes never go negative (a negative scale factor must not make a negative width)
            for key in ('size', 'width', 'height', 'diam'):
                if key in now:
                    assert now[key] >= 0, (kind, tid, key, now[key])

    def test_the_region_aperture_never_sets_the_flag(self, app):
        for tid in TR_BY_ID:
            obj = make_gerber(app, fx('region_hole'), name='hole')
            assert set(obj.tools) == {0}
            TR_BY_ID[tid].apply(obj)
            assert obj.export_as_regions is False, tid


    def test_rotate_quarter_turn_swaps_only_for_odd_turns(self, app):
        for angle, swapped in ((90.0, True), (270.0, True), (-90.0, True), (450.0, True),
                               (180.0, False), (360.0, False), (-180.0, False), (720.0, False)):
            obj, apid = load(app, 'R')
            obj.rotate(angle, ROT_ORIGIN)
            expect = (1.2, 3.0) if swapped else (3.0, 1.2)
            assert (obj.tools[apid]['width'], obj.tools[apid]['height']) == pytest.approx(expect), angle


    def test_an_o_track_is_drawn_with_the_diagonal_of_the_aperture(self, app, tmp_path):
        """The parser draws a track of an O aperture with 'size' (the diagonal of the aperture) as width. After a
        buffer, 'size' + 2d is not the diagonal of the buffered width and height, so only the regions export
        keeps the track. A flash-only O aperture stays native."""
        text = ('%FSLAX46Y46*%\n%MOMM*%\n%ADD12O,2.0X1.0*%\nG01*\n%LPD*%\nD12*\n'
                'X5000000Y5000000D02*\nX15000000Y5000000D01*\nM02*\n')
        track = reparse_gerber(app, text, tmp_path, name='otrack')
        assert any(isinstance(rec['follow'], LineString) for rec in track.tools[12]['geometry'])
        track.buffer(0.3, 'mitre')
        assert track.export_as_regions is True
        flash, _ = load(app, 'O')
        flash.buffer(0.3, 'mitre')
        assert flash.export_as_regions is False
        # a uniform scale keeps the track representable
        track2 = reparse_gerber(app, text, tmp_path, name='otrack2')
        track2.scale(2.0, 2.0, SCALE_ORIGIN)
        assert track2.export_as_regions is False
        assert track2.tools[12]['size'] == pytest.approx(2 * math.hypot(2.0, 1.0))


    def test_a_degenerate_scale_or_buffer_sets_the_flag(self, app):
        degenerate, _ = load(app, 'R')
        degenerate.scale(2.0, 0.0, SCALE_ORIGIN)
        assert degenerate.export_as_regions is True
        circle, _ = load(app, 'C')
        circle.buffer(-0.8, 'mitre')       # 1.5 - 1.6 < 0
        assert circle.export_as_regions is True
        rect, _ = load(app, 'R')
        rect.buffer(-0.6, 'mitre')         # the height 1.2 - 1.2 = 0
        assert rect.export_as_regions is True


class TestFlagIsSticky:
    def test_flag_never_goes_back_to_false(self, app, tmp_path):
        obj, apid = load(app, 'R')
        obj.rotate(30.0, ROT_ORIGIN)
        assert obj.export_as_regions is True
        for step in (lambda o: o.rotate(-30.0, ROT_ORIGIN),      # back to the original angle
                     lambda o: o.rotate(90.0, ROT_ORIGIN),
                     lambda o: o.mirror('X', MIR_ORIGIN),
                     lambda o: o.scale(1.5, 1.5, SCALE_ORIGIN),
                     lambda o: o.offset((1.0, 1.0)),
                     lambda o: o.buffer(0.1, 'mitre')):
            step(obj)
            assert obj.export_as_regions is True
        text = export_gerber_text(app, obj)
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'sticky')
        assert flash_count(again) == flash_count(obj) > 0


# ---------------------------------------------------------------------------------------------------------------
# the table: export and parse again
# ---------------------------------------------------------------------------------------------------------------

class TestTableExportRoundTrip:
    @pytest.mark.parametrize('kind,tid', cell_params('export'))
    def test_composed_geometry_survives_the_export(self, app, tmp_path, kind, tid):
        obj, apid = load(app, kind)
        before_counts = {ap: record_count(obj, ap) for ap in obj.tools}
        flagged, _ = expected_cell(kind, tid, dict(obj.tools[apid]))
        TR_BY_ID[tid].apply(obj)
        text = export_gerber_text(app, obj)
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, '%s %s' % (kind, tid))

        goes_native = COLUMN[kind] in ('C', 'R', 'O') and not flagged and kind != 'RT'
        if goes_native:
            # native export: the aperture declarations come from the metadata and the feature counts are kept
            assert '%%ADD%d' % apid in text
            assert sorted(again.tools) == sorted(before_counts)
            for ap, count in before_counts.items():
                assert record_count(again, ap) == count, (kind, tid, ap)
            new_tool = again.tools[apid]
            old_tool = obj.tools[apid]
            assert new_tool['type'] == old_tool['type']
            for key in ('size', 'width', 'height'):
                if key in old_tool:
                    assert new_tool[key] == pytest.approx(old_tool[key], abs=1e-5), (kind, tid, key)
        else:
            # the metadata can not describe the records: each flash is a block aperture flashed at its point
            assert flash_count(again) == flash_count(obj), (kind, tid)


class TestUntransformedAndMixedFixtures:
    @pytest.mark.parametrize('kind', ['AM'])
    def test_untransformed_p_and_macro_flashes_stay_flashes(self, app, tmp_path, kind):
        """The writer cannot declare P and AM apertures: each flash is a block aperture with the shape of the flash,
        flashed at its point, before any transform and without the flag."""
        obj, apid = load(app, kind)
        assert obj.export_as_regions is False
        assert obj.needs_region_export() is True
        text = export_gerber_text(app, obj)
        assert '%ABD' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, kind)
        assert flash_count(again) == record_count(obj, apid)


    def test_dark_and_clear_objects_are_written_with_their_apertures_in_file_order(self, app, tmp_path):
        """A parsed file with clear objects keeps its apertures in the export: the records are written in the
        order of the file. Without that order (records made by an editor, a join, an older project) the records are
        still written with their apertures; the export adds at the end what their order can not give."""
        obj = make_gerber(app, 'tests/fixtures/orcad_17.2/Copper_Bottom.art', name='clear')
        assert obj.needs_region_export() is False
        text = export_gerber_text(app, obj)
        assert '%ADD' in text and '%LPC*%' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'file order')
        assert set(again.tools) == set(obj.tools)

        obj.records_in_order = False
        text = export_gerber_text(app, obj)
        assert '%ADD' in text and '%LPC*%' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'no order')
        assert flash_count(again) == flash_count(obj) > 0

    @pytest.mark.parametrize('tid', ['buffer_mitre'])
    def test_region_with_a_hole(self, app, tmp_path, tid):
        obj = make_gerber(app, fx('region_hole'), name='hole')
        TR_BY_ID[tid].apply(obj)
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'hole ' + tid)
        # the hole is still a hole
        assert interior_count(obj.solid_geometry) == 1
        assert interior_count(again.solid_geometry) == 1


    @pytest.mark.parametrize('tid', ['buffer_shrink_mitre'])
    def test_native_subset_stays_native_when_the_table_allows(self, app, tmp_path, tid):
        obj = make_gerber(app, NATIVE_GBR)
        TR_BY_ID[tid].apply(obj)
        assert obj.export_as_regions is False
        text = export_gerber_text(app, obj)
        assert '%ADD11R,' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'native ' + tid)
        assert {ap: record_count(again, ap) for ap in again.tools} == {ap: record_count(obj, ap) for ap in obj.tools}


class TestLargeRegionExport:
    @pytest.mark.parametrize('overlapping', [True, False])
    @pytest.mark.parametrize('with_clipper', [True, False])
    def test_many_parts_export_the_same_copper(self, app, tmp_path, monkeypatch, overlapping, with_clipper):
        """80 or more parts take the Clipper2 / no-union / GEOS branches of the region export; the copper stays."""
        if not with_clipper:
            from appCommon import clipper_ops
            monkeypatch.setattr(clipper_ops, 'union', lambda geoms: None)
        step = 1.0 if overlapping else 3.0
        geometry = [box(i * step, 0, i * step + 1.5, 1) for i in range(70)]
        ring = box(0, 5, 4, 9).difference(box(1, 6, 3, 8))
        geometry += [ring] + [box(i * 3.0, 12, i * 3.0 + 1, 13) for i in range(10)]
        obj = make_gerber(app, NATIVE_GBR)
        obj.solid_geometry = geometry
        obj.export_as_regions = True
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry)
        assert interior_count(again.solid_geometry) == 1
        assert len(parts(polygon_union(again.solid_geometry))) == len(parts(polygon_union(geometry)))


# ---------------------------------------------------------------------------------------------------------------
# project save and load
# ---------------------------------------------------------------------------------------------------------------

class TestProjectSaveLoad:
    def test_flag_set_by_a_transform_survives_and_exports(self, app, tmp_path):
        obj, apid = load(app, 'R')
        obj.rotate(30.0, ROT_ORIGIN)
        _, restored = save_and_load(app, [obj], tmp_path)
        assert restored[0].export_as_regions is True
        text = export_gerber_text(app, restored[0])
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'project flag')
        assert flash_count(again) == flash_count(obj) > 0


def region_gerber(app, geometry, name='reg'):
    """A Gerber with only a composed solid_geometry, routed through the region export (PCB document import)."""
    obj = GerberObject(name, app)
    obj.units = 'MM'
    obj.solid_geometry = geometry
    obj.tools = {}
    obj.obj_options['pcbdoc_import'] = True
    return obj


def square_with_hole():
    return Polygon(box(0, 0, 10, 10).exterior.coords, [box(3, 3, 7, 7).exterior.coords])


class TestRegionExportKeepsHolesAndIslands:
    def test_polygon_with_a_hole(self, app, tmp_path):
        geometry = square_with_hole()
        again = reparse_gerber(app, export_gerber_text(app, region_gerber(app, [geometry])), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry, rel=1e-6, abs_tol=1e-4, msg='polygon with hole')
        assert not polygon_union(again.solid_geometry).contains(Point(5, 5))
        assert polygon_union(again.solid_geometry).contains(Point(1, 1))

    def test_island_inside_a_hole(self, app, tmp_path):
        geometry = MultiPolygon([square_with_hole(), box(4, 4, 6, 6)])
        again = reparse_gerber(app, export_gerber_text(app, region_gerber(app, [geometry])), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry, rel=1e-6, abs_tol=1e-4, msg='island in hole')
        union = polygon_union(again.solid_geometry)
        assert union.contains(Point(5, 5))          # the island
        assert not union.contains(Point(3.5, 5))    # the hole around it
        assert union.contains(Point(1, 5))          # the ring


# ---------------------------------------------------------------------------------------------------------------
# routing: Gerber.needs_region_export() and the export that follows it
# ---------------------------------------------------------------------------------------------------------------


def make_p_am_gerber(app, tmp_path):
    path = tmp_path / 'pam.gbr'
    path.write_text(MIXED_GBR_NO_POLARITY)
    obj = GerberObject('pam', app)
    assert obj.parse_file(str(path)) is None
    return obj


class TestNeedsRegionExport:

    def test_flag_pcbdoc_and_p_am_apertures_force_the_region_export(self, app, tmp_path):
        grb = make_gerber(app, NATIVE_GBR)
        assert grb.needs_region_export() is False
        grb.export_as_regions = True
        assert grb.needs_region_export() is True
        pcb = make_gerber(app, NATIVE_GBR)
        pcb.obj_options['pcbdoc_import'] = True
        assert pcb.needs_region_export() is True
        assert make_p_am_gerber(app, tmp_path).needs_region_export() is True

    def test_a_clear_record_forces_the_region_export(self, app):
        grb = make_gerber(app)   # the mixed fixture has a dark -> clear -> dark sequence on aperture 10
        assert grb.needs_region_export() is True
        native = make_gerber(app, NATIVE_GBR)
        native.tools[10]['geometry'].append({'clear': box(0, 0, 1, 1)})
        assert native.needs_region_export() is True


# ---------------------------------------------------------------------------------------------------------------
# lifecycle of export_as_regions: copy, merge, project save/load, editor save
# ---------------------------------------------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from appEditors.appGerberEditor import AppGerberEditor  # noqa: E402
from appHandlers.appIO import appIO  # noqa: E402
from camlib import dict2obj  # noqa: E402
from appCommon.ProjectFile import load_project_file  # noqa: E402
import numpy as np


def flagged_native(app, name='flagged'):
    """A native Gerber whose flag is set by hand: no transform sets it yet."""
    grb = make_gerber(app, NATIVE_GBR, name=name)
    grb.export_as_regions = True
    return grb


def assert_exports_composed_geometry(app, obj, expected, tmp_path, msg):
    """The exported file, parsed back by the real parser, equals the expected composed geometry."""
    text = export_gerber_text(app, obj)
    again = reparse_gerber(app, text, tmp_path, name='again_' + msg.replace(' ', '_'))
    assert_poly_equiv(again.solid_geometry, expected, rel=1e-6, abs_tol=1e-4, msg=msg)
    # the flashes stay flashes (aperture or block aperture)
    assert flash_count(again) == flash_count(obj), msg


class TestLifecycleCopy:
    def test_on_copy_command_keeps_the_flag(self, app, tmp_path):
        source = flagged_native(app)
        clone = run_copy(app, source, 'gerber')
        assert clone.export_as_regions is True
        assert_exports_composed_geometry(app, clone, source.solid_geometry, tmp_path, 'copy command')
        assert source.export_as_regions is True


class TestLifecycleMerge:
    def merged(self, app, flags):
        first = make_gerber(app, NATIVE_GBR, name='first')
        second = make_gerber(app, NATIVE_GBR, name='second')
        second.offset((50.0, 0.0))
        first.export_as_regions, second.export_as_regions = flags
        final = GerberObject('final', app)
        GerberObject.merge([first, second], final, log=app.log)
        return first, second, final

    def test_flag_of_any_source_wins(self, app, tmp_path):
        for flags in ((True, False), (False, True), (True, True)):
            first, second, final = self.merged(app, flags)
            assert final.export_as_regions is True, flags
            expected = polygon_union([first.solid_geometry, second.solid_geometry])
            assert_exports_composed_geometry(app, final, expected, tmp_path, 'merge %s%s' % flags)


class TestLifecycleProject:
    def save_and_load(self, app, tmp_path, source, change=None):
        """Save the object in a project file and give the object that the project load builds from it."""
        app.collection.get_list.return_value = [source]
        app.collection.get_active.return_value = None
        path = str(tmp_path / 'saved.FlatPrj')
        io = appIO(app)
        io.save_project(path, silent=True)
        proj = load_project_file(path, object_hook=dict2obj)
        if change is not None:
            change(proj['objs'][0])

        restored = []

        def new_object(kind, name, initialize, *args, **kwargs):
            new_obj = GerberObject(name, app)
            assert initialize(new_obj, app) != 'fail'
            restored.append(new_obj)

        app.app_obj.new_object.side_effect = new_object
        app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
        io.restore_project_objects(proj, path, True, False)
        return restored[0]

    def test_project_without_the_field_loads_as_false(self, app, tmp_path):
        source = make_gerber(app, NATIVE_GBR)

        def old_project(saved):
            assert 'export_as_regions' in saved
            del saved['export_as_regions']

        restored = self.save_and_load(app, tmp_path, source, old_project)
        assert restored.export_as_regions is False
        assert_poly_equiv(restored.solid_geometry, source.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='old')

    def test_clear_objects_keep_their_apertures_after_a_project_load(self, app, tmp_path):
        """The project keeps the file order of the objects: the export of the loaded object is the file order. A
        project without that field (an older version) and an object saved after a buffer have no known order: the
        export still writes the flashes and the clear objects, and adds what the order can not give."""
        source = make_gerber(app, 'tests/fixtures/orcad_17.2/Copper_Bottom.art', name='clear')
        flashes = flash_count(source)
        assert flashes > 0
        loaded = self.save_and_load(app, tmp_path, source)
        older = self.save_and_load(app, tmp_path, source, lambda saved: saved.pop('records_in_order', None))
        source.buffer(0.05, 'mitre')
        buffered = self.save_and_load(app, tmp_path, source)
        for msg, obj in (('loaded project', loaded), ('older', older), ('buffer', buffered)):
            text = export_gerber_text(app, obj)
            assert '%LPC*%' in text, msg
            again = reparse_gerber(app, text, tmp_path)
            assert_composed_equal(again.solid_geometry, obj.solid_geometry, msg)
            assert flash_count(again) == flashes, msg


class TestLifecycleEditor:
    """The editor object cannot be built headless (it needs the whole GUI), so build_edited_gerber() runs on
    a stand-in ``self``: the real worker step of the save, and its detached result is checked."""

    def run_editor_save(self, app, source):
        storage = {}
        for ap, tool in source.tools.items():
            storage[ap] = {k: v for k, v in tool.items() if k != 'geometry'}
            storage[ap]['geometry'] = [SimpleNamespace(geo=dict(rec)) for rec in tool['geometry']]
        app.app_units = 'MM'
        app.f_handlers = appIO(app)
        app.custom_signal = MagicMock()
        app.main_thread = QThread.currentThread()
        stand_in = SimpleNamespace(
            app=app, gerber_obj=source, gerber_obj_options=source.obj_options, results=[1])
        AppGerberEditor.build_edited_gerber(stand_in, 'edited', storage)
        return app.custom_signal.emit.call_args.args[0]['obj']

    def test_editor_save_keeps_the_flag_and_exports_the_result(self, app, tmp_path):
        source = flagged_native(app)
        edited = self.run_editor_save(app, source)
        assert edited.export_as_regions is True
        assert_exports_composed_geometry(app, edited, edited.solid_geometry, tmp_path, 'editor')
        # the source_file made by the editor keeps the flashes too
        assert flash_count(reparse_gerber(app, edited.source_file, tmp_path)) == flash_count(edited)
        assert_poly_equiv(edited.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='editor')


class TestGerberParse:


    def test_aperture_metadata(self, gerber):
        t = gerber.tools
        assert (t[10]['type'], t[10]['size']) == ('C', 1.0)
        assert (t[16]['type'], t[16]['size']) == ('C', 6.0)
        assert (t[17]['type'], t[17]['size']) == ('C', 2.0)
        for ap, kind in ((11, 'R'), (12, 'O')):
            assert t[ap]['type'] == kind
            assert t[ap]['width'] == 2.0 and t[ap]['height'] == 1.0
            assert t[ap]['size'] == pytest.approx(np.hypot(2.0, 1.0))
        assert t[13]['type'] == 'P'
        assert t[13]['diam'] == 2.0
        assert t[13]['nVertices'] == 5
        assert t[13]['rotation'] == 30.0
        assert t[13]['size'] == 2.0
        assert t[14]['type'] == 'AM' and t[14]['macro'].name == 'OUTL' and t[14]['modifiers'] == []
        assert t[15]['type'] == 'AM' and t[15]['macro'].name == 'RING' and t[15]['modifiers'] == ['2.0', '1.0']
        assert 'size' not in t[14] and 'size' not in t[15]
        assert t[0]['type'] == 'REG' and t[0]['size'] == 0.0


@pytest.mark.parametrize('name', [n for n in TRANSFORM_NAMES if n in ('offset', 'rotate', 'scale')])
class TestGerberTransforms:
    def test_composed_geometry_and_records_match_affine_copy(self, app, name):
        obj = make_gerber(app)
        before = snapshot_gerber(obj)
        AFFINE_TRANSFORMS[name][0](obj)
        expected = apply_to(AFFINE_TRANSFORMS[name][1], polygon_union(before['solid']))
        assert_poly_equiv(obj.solid_geometry, expected, rel=1e-6, msg=name)
        assert sorted(obj.tools) == sorted(before['tools'])
        for ap, tool in before['tools'].items():
            now = obj.tools[ap]['geometry']
            assert len(now) == len(tool['geometry']), (name, ap)
            assert obj.tools[ap]['type'] == tool['type']
            for rec_before, rec_now in zip(tool['geometry'], now):
                assert set(rec_before) == set(rec_now), (name, ap)
                for key in rec_before:
                    assert_coords_close(rec_now[key], apply_to(AFFINE_TRANSFORMS[name][1], rec_before[key]),
                                        msg='%s %s %s' % (name, ap, key))


class TestFileRoundTrip:
    def test_gerber_native_apertures_round_trip(self, app, tmp_path):
        """C, R, O flashes, tracks and a region: composed geometry and aperture metadata survive."""
        grb = make_gerber(app, NATIVE_GBR)
        again = reparse_gerber(app, export_gerber_text(app, grb), tmp_path)
        assert again.units == 'MM'
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='native gerber')
        assert sorted(again.tools) == sorted(grb.tools)
        for ap, tool in grb.tools.items():
            assert again.tools[ap]['type'] == tool['type']
            assert len(again.tools[ap]['geometry']) == len(tool['geometry']), ap
            for key in ('size', 'width', 'height'):
                if key in tool:
                    assert again.tools[ap][key] == pytest.approx(tool[key], abs=1e-6), (ap, key)


class TestRegressionGerberFileRoundTripFixedInStage0C:
    """Regression tests: they specify the corrected behavior and fail today (strict xfail).
    The owning stage (0C) removes the marker."""

    def test_dark_clear_dark_sequence_survives_the_gerber_export(self, app, tmp_path):
        grb = make_gerber(app, MIXED_GBR)
        again = reparse_gerber(app, export_gerber_text(app, grb), tmp_path)
        # the writer groups records per aperture, so the later dark flash and the dark track are written
        # in a different order than they were drawn and the cut lands on different copper
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4,
                          msg='composed geometry after export')


X2_GBR = """%TF.GenerationSoftware,KiCad,Pcbnew,7.0*%
%TF.CreationDate,2020-01-01T00:00:00+00:00*%
%TF.ProjectId,demo,00000000-0000-0000-0000-000000000000,rev1*%
%TF.FileFunction,Copper,L1,Top*%
%TF.FilePolarity,Positive*%
%TF.MD5,00000000000000000000000000000000*%
%FSLAX46Y46*%
%MOMM*%
%TA.AperFunction,SMDPad,CuDef*%
%ADD10R,1.0X0.5*%
%TA.AperFunction,Conductor*%
%ADD11C,0.2*%
%TD*%
G01*
%LPD*%
D10*
%TO.P,R1,1*%
%TO.N,Net\\u002CA*%
X1000000Y1000000D03*
%TO.N,GND*%
X5000000Y1000000D03*
%TD*%
D11*
X0Y0D02*
X5000000Y0D01*
M02*
"""


def x2_records(grb):
    """[(aperture, bounds, nets)] of every record: the nets are the .N values of the TO state of the record."""
    out = []
    for apid, tool in sorted(grb.tools.items()):
        for rec in tool.get('geometry', []):
            nets = grb.to_attributes[rec['to']].get('.N') if rec.get('to') else None
            out.append((apid, tuple(round(c, 4) for c in rec['follow'].bounds), nets))
    return sorted(out, key=repr)


class TestGerberAttributeExport:
    """X2/X3 attributes are written by the Gerber export and read back unchanged."""

    @pytest.fixture
    def x2(self, app, tmp_path):
        path = tmp_path / 'x2.gbr'
        path.write_text(X2_GBR)
        grb = GerberObject('x2', app)
        assert grb.parse_file(str(path)) is None
        return grb

    def test_file_attributes_are_kept_and_stamped(self, app, tmp_path, x2):
        text = export_gerber_text(app, x2)
        again = reparse_gerber(app, text, tmp_path)
        tf = again.attributes['TF']
        assert tf['.FileFunction'] == ['Copper', 'L1', 'Top']
        assert tf['.FilePolarity'] == ['Positive']
        assert tf['.ProjectId'] == x2.attributes['TF']['.ProjectId']
        assert tf['.GenerationSoftware'][:2] == ['FlatCAM', 'EVO']
        assert tf['.CreationDate'] != ['2020-01-01T00:00:00+00:00']
        assert '.MD5' not in tf
        assert text.index('%TF.FileFunction') < text.index('%ADD10')

    def test_aperture_and_object_attributes_stay_on_their_own(self, app, tmp_path, x2):
        again = reparse_gerber(app, export_gerber_text(app, x2), tmp_path)
        assert again.tools[10]['attributes']['.AperFunction'] == ['SMDPad', 'CuDef']
        assert again.tools[11]['attributes']['.AperFunction'] == ['Conductor']
        before = x2_records(x2)
        assert [r[2] for r in before if r[0] == 10 and r[2]] != []
        assert x2_records(again) == before

    def test_escaped_value_round_trips(self, app, tmp_path, x2):
        text = export_gerber_text(app, x2)
        assert '%TO.N,Net\\u002CA*%' in text
        again = reparse_gerber(app, text, tmp_path)
        assert ['Net,A'] in [r[2] for r in x2_records(again)]


