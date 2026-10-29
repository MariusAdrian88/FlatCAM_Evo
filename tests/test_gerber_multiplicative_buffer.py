"""Tests of Stage 3 of the Gerber / Excellon unification plan: the Gerber multiplicative buffer
(``Gerber.buffer(f, join, True)``).

Supported: flashes of every aperture type (scaled about their own point), tracks drawn with a C aperture (rebuilt with
the width ``size * f``), regions (unchanged). Rejected with a message and no change: a clear feature anywhere, a track
drawn with any other aperture, a factor that is not positive. Any failure in the middle leaves the object unchanged.

The expected values are written from the plan, not computed with the code under test. Everything is headless.
"""

import copy
import math

import pytest
import shapely
from shapely.geometry import LinearRing, LineString, Point

from appParsers.ParseGerber import Gerber
from camlib import gerber_quad_segs
from tests.helpers_objects import (  # noqa: F401,E402
    app, export_gerber_text, FIXTURES, _headless_qt, make_gerber, MIXED_GBR, parts, polygon_union, reparse_gerber,
)


def fx(name):
    return str(FIXTURES / ('unify_%s.gbr' % name))


def write(tmp_path, name, text):
    path = tmp_path / (name + '.gbr')
    path.write_text(text)
    return str(path)


HEADER = "%FSLAX46Y46*%\n%MOMM*%\n"

# flashes of every type, a C track and a dark region: every supported record kind, no clear feature
ALL_SUPPORTED = HEADER + """%AMOUTL*
4,1,4,0,0,2,0,2,1,0,1,0,0,0*%
%ADD10C,1.0*%
%ADD11R,2.0X1.0*%
%ADD12O,2.0X1.0*%
%ADD13P,2.0X5X30*%
%ADD14OUTL*%
%ADD16C,0.5*%
G01*
%LPD*%
D10*
X5000000Y5000000D03*
D11*
X10000000Y5000000D03*
D12*
X15000000Y5000000D03*
D13*
X20000000Y5000000D03*
D14*
X25000000Y5000000D03*
D16*
X0Y10000000D02*
X30000000Y10000000D01*
X30000000Y15000000D01*
G36*
X0Y20000000D02*
X10000000Y20000000D01*
X10000000Y28000000D01*
X0Y28000000D01*
X0Y20000000D01*
G37*
M02*
"""

# a dark region and a C flash, nothing else
REGION_AND_FLASH = HEADER + """%ADD10C,1.0*%
G01*
%LPD*%
G36*
X0Y0D02*
X14000000Y0D01*
X14000000Y10000000D01*
X0Y10000000D01*
X0Y0D01*
G37*
D10*
X20000000Y5000000D03*
M02*
"""

# a single rectangular region: the distance buffer resolution is visible in the rounded corners
REGION_ONLY = HEADER + """%ADD10C,1.0*%
G01*
%LPD*%
G36*
X0Y0D02*
X10000000Y0D01*
X10000000Y10000000D01*
X0Y10000000D01*
X0Y0D01*
G37*
M02*
"""

# a track drawn with an O aperture (the parser buffers it with the diagonal as its width)
O_TRACK = HEADER + """%ADD12O,2.0X1.0*%
G01*
%LPD*%
D12*
X5000000Y5000000D02*
X15000000Y5000000D01*
M02*
"""

# (fixture, aperture id, aperture type) of the flash fixtures
FLASH_KINDS = {
    'C': ('circle', 10),
    'R': ('rect', 11),
    'O': ('oblong', 12),
    'P3': ('p3', 20),
    'P7': ('p7', 21),
    'AM': ('macro', 14),
}


def multiply(obj, factor):
    """Run the multiplicative buffer. A KeyError (what the code before Stage 3 raised for O, P and AM flashes) is
    turned into an AssertionError, so a test that fails on the old code fails with an assertion."""
    try:
        # Gerber.buffer, not the GerberObject wrapper: the wrapper drops the return value ('fail')
        return Gerber.buffer(obj, factor, 'mitre', True)
    except KeyError as e:
        raise AssertionError('Gerber.buffer raised KeyError %s' % e) from e


def full_state(obj):
    """Everything the operation may change, in a form that compares exactly (WKB for the geometry)."""
    tools = {}
    for apid, tool in obj.tools.items():
        meta = {k: (id(v) if k == 'macro' else v) for k, v in tool.items() if k != 'geometry'}
        records = [{k: (v.wkb, v.geom_type) for k, v in rec.items()} for rec in tool.get('geometry', ())]
        tools[apid] = (meta, records)
    return {
        'tools': tools,
        'solid': (type(obj.solid_geometry).__name__, [g.wkb for g in parts(obj.solid_geometry)]),
        'follow': [g.wkb for g in parts(obj.follow_geometry)],
        'flag': obj.export_as_regions,
        'record_ids': {apid: [id(rec) for rec in tool.get('geometry', ())] for apid, tool in obj.tools.items()},
    }


def warned(app):
    return any(str(call.args[0]).startswith(('[WARNING_NOTCL]', '[ERROR_NOTCL]'))
               for call in app.inform.emit.call_args_list if call.args)




def assert_composed_equal(actual, expected, msg, tol=2e-3):
    exp = polygon_union(expected)
    got = polygon_union(actual)
    assert exp.area > 0.5, msg + ' (empty expectation)'
    diff = got.symmetric_difference(exp).area
    assert diff <= max(1e-3, tol * exp.area), '%s symmetric difference %.6f (area %.4f)' % (msg, diff, exp.area)


def flash_records(obj, apid):
    return [rec for rec in obj.tools[apid]['geometry'] if isinstance(rec.get('follow'), Point)]


# ---------------------------------------------------------------------------------------------------------------
# flashes
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('factor', [1.5])
@pytest.mark.parametrize('kind', ['C', 'AM'])
class TestFlashes:


    def test_file_round_trip(self, app, tmp_path, kind, factor):
        fixture, apid = FLASH_KINDS[kind]
        obj = make_gerber(app, fx(fixture))
        assert multiply(obj, factor) is None
        native = kind in ('C', 'R', 'O')
        assert obj.needs_region_export() is (not native)
        text = export_gerber_text(app, obj)
        assert ('%ADD' in text) is native
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'round trip')


# ---------------------------------------------------------------------------------------------------------------
# tracks and regions
# ---------------------------------------------------------------------------------------------------------------

class TestTracksAndRegions:
    @pytest.mark.parametrize('factor', [1.5])
    def test_c_track_is_rebuilt_with_the_scaled_width(self, app, tmp_path, factor):
        obj = make_gerber(app, fx('circle'))
        track = [rec for rec in obj.tools[10]['geometry'] if isinstance(rec['follow'], LineString)][0]
        follow = track['follow']
        multiply(obj, factor)
        new_track = [rec for rec in obj.tools[10]['geometry'] if isinstance(rec['follow'], LineString)][0]
        assert obj.tools[10]['size'] == pytest.approx(1.5 * factor)
        assert new_track['follow'].equals(follow)
        expected = follow.buffer(1.5 * factor / 2, gerber_quad_segs(app.options, 1.5 * factor / 2, obj.units))
        assert new_track['solid'].symmetric_difference(expected).area <= 1e-9
        # the width of the track: twice the distance from the axis to the outline, in the middle of a segment
        mid = Point(15.0, 20.0)
        assert shapely.distance(mid, new_track['solid'].exterior) == pytest.approx(1.5 * factor / 2, rel=1e-3)
        # file round trip: a C track and a C flash are native
        assert obj.export_as_regions is False and obj.needs_region_export() is False
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert again.tools[10]['size'] == pytest.approx(1.5 * factor)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'round trip')

    def test_regions_are_unchanged(self, app, tmp_path):
        path = write(tmp_path, 'region_flash', REGION_AND_FLASH)
        obj = make_gerber(app, path)
        region_before = copy.deepcopy(obj.tools[0]['geometry'])
        meta_before = {k: v for k, v in obj.tools[0].items() if k != 'geometry'}
        multiply(obj, 1.5)
        assert len(obj.tools[0]['geometry']) == len(region_before)
        for rec, old in zip(obj.tools[0]['geometry'], region_before):
            assert rec['solid'].equals_exact(old['solid'], 0)
            assert rec['follow'].equals_exact(old['follow'], 0)
        assert {k: v for k, v in obj.tools[0].items() if k != 'geometry'} == meta_before
        flash = obj.tools[10]['geometry'][0]['solid']
        assert flash.area == pytest.approx(math.pi * 0.75 ** 2, rel=1e-2)   # a scaled flash keeps its segments
        assert obj.solid_geometry.contains(region_before[0]['solid'].buffer(-1e-6))
        assert obj.export_as_regions is False
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'round trip')

    @pytest.mark.parametrize('factor', [1.5])
    def test_every_supported_record_kind_together(self, app, tmp_path, factor):
        path = write(tmp_path, 'all', ALL_SUPPORTED)
        obj = make_gerber(app, path)
        before = copy.deepcopy({apid: [(r['solid'], r['follow']) for r in t['geometry']]
                                for apid, t in obj.tools.items()})
        multiply(obj, factor)
        expected = []
        for apid, items in before.items():
            tool = obj.tools[apid]
            for rec, (solid, follow) in zip(tool['geometry'], items):
                if apid == 0:
                    expected.append(solid)
                elif isinstance(follow, Point):
                    assert rec['solid'].area == pytest.approx(solid.area * factor ** 2, rel=1e-9)
                    expected.append(rec['solid'])
                else:
                    expected.append(follow.buffer(0.5 * factor / 2, gerber_quad_segs(app.options, 0.5 * factor / 2, obj.units)))
        assert_composed_equal(obj.solid_geometry, expected, 'composed')
        assert obj.tools[16]['size'] == pytest.approx(0.5 * factor)
        assert obj.export_as_regions is True          # the macro
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'round trip')


# ---------------------------------------------------------------------------------------------------------------
# rejected input: message, 'fail', nothing changed
# ---------------------------------------------------------------------------------------------------------------

def load_rejected(app, tmp_path, name):
    if name == 'dark_clear_dark':
        return make_gerber(app, MIXED_GBR)
    if name == 'region_hole':
        return make_gerber(app, fx('region_hole'))
    if name == 'r_track':
        return make_gerber(app, fx('rtrack'))
    if name == 'o_track':
        return make_gerber(app, write(tmp_path, 'otrack', O_TRACK))
    raise AssertionError(name)


@pytest.mark.parametrize('name', ['dark_clear_dark'])
class TestRejected:
    def test_nothing_changes_and_the_user_is_told(self, app, tmp_path, name):
        obj = load_rejected(app, tmp_path, name)
        before = full_state(obj)
        app.inform.emit.reset_mock()
        assert multiply(obj, 1.5) == 'fail'
        assert full_state(obj) == before
        assert warned(app)
        assert not any('Gerber Buffer done' in str(c.args[0]) for c in app.inform.emit.call_args_list if c.args)


def test_tcl_buffer_returns_fail_on_a_rejection(app):
    from tclCommands.TclCommandBuffer import TclCommandBuffer
    obj = make_gerber(app, MIXED_GBR)
    before = full_state(obj)
    app.collection.get_by_name.return_value = obj
    cmd = TclCommandBuffer(app)
    args, unnamed = cmd.check_args(('grb', '-dist', '1.5', '-factor', 'True'))
    assert cmd.execute(args, unnamed) == 'fail'
    assert full_state(obj) == before


class TestInvalidInput:
    @pytest.mark.parametrize('distance, factor', [
        (-1.5, True),                 # a factor that is not positive
    ])
    def test_it_is_rejected_without_a_change(self, app, distance, factor):
        obj = make_gerber(app, fx('circle'))
        before = full_state(obj)
        app.inform.emit.reset_mock()
        assert Gerber.buffer(obj, distance, 'mitre', factor) == 'fail'
        assert full_state(obj) == before
        assert warned(app)


# ---------------------------------------------------------------------------------------------------------------
# distance buffer
# ---------------------------------------------------------------------------------------------------------------

class TestDistanceBuffer:
    def test_it_uses_the_quarter_circle_resolution(self, app, tmp_path):
        obj = make_gerber(app, write(tmp_path, 'region_only', REGION_ONLY))
        before = obj.tools[0]['geometry'][0]['solid']
        assert Gerber.buffer(obj, 0.5, 'round') is None
        after = obj.tools[0]['geometry'][0]['solid']
        expected = before.buffer(0.5, quad_segs=gerber_quad_segs(app.options, 0.5, obj.units), join_style="round")
        assert len(after.exterior.coords) == len(expected.exterior.coords)
        assert after.area == pytest.approx(expected.area)

    def test_it_keeps_the_solid_follow_and_clear_records(self, app, tmp_path):
        obj = make_gerber(app, MIXED_GBR)
        clear = [rec for rec in obj.tools[10]['geometry'] if 'clear' in rec][0]
        follow, clear_geometry = clear['follow'], clear['clear']
        keys_before = [sorted(rec) for tool in obj.tools.values() for rec in tool.get('geometry', ())]
        assert Gerber.buffer(obj, 0.3, 'mitre') is None
        keys_after = [sorted(rec) for tool in obj.tools.values() for rec in tool.get('geometry', ())]
        assert keys_after == keys_before
        new_clear = [rec for rec in obj.tools[10]['geometry'] if 'clear' in rec][0]
        assert new_clear['follow'].equals(follow)
        assert new_clear['clear'].area > clear_geometry.area
        assert obj.tools[10]['size'] == pytest.approx(1.6)
        # the result round trips through the file
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'distance round trip')

    def test_a_negative_buffer_of_a_closed_line_keeps_the_gerber_semantics(self, app):
        obj = make_gerber(app, fx('circle'))
        obj.solid_geometry = [LinearRing([(0, 0), (4, 0), (4, 4), (0, 4)])]
        assert Gerber.buffer(obj, -0.1, 'mitre') is None
        # Gerber keeps the plain line buffer (an empty polygon), not the Geometry closed-contour erosion
        assert obj.solid_geometry == []


