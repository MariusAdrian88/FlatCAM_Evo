import math

import pytest
from shapely import Point, LineString, box
from shapely.ops import unary_union

from appPlugins.ToolPunchGerber.PunchGerberGen import punch_geometry
from tests.helpers_objects import _headless_qt  # noqa: F401

ALL_ON = [True] * 5


def _circle_ap(x, y, size=1.0):
    pt = Point(x, y)
    return {'type': 'C', 'size': size, 'geometry': [{'follow': pt, 'solid': pt.buffer(size / 2, quad_segs=16)}]}


def _obround_ap(x, y, w=1.0, h=0.6):
    pt = Point(x, y)
    return {'type': 'O', 'size': math.hypot(w, h), 'width': w, 'height': h,
            'geometry': [{'follow': pt, 'solid': box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)}]}


def _am_ap(x, y, w=1.0, h=0.8):
    pt = Point(x, y)
    return {'type': 'AM', 'geometry': [{'follow': pt, 'solid': box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)}]}


def _solid(tools):
    return unary_union([r['solid'] for t in tools.values() for r in t['geometry'] if 'solid' in r])


def _clears(apertures):
    return [(a['size'], r['clear']) for a in apertures.values() for r in a['geometry'] if 'clear' in r]


def _run(tools, method='fixed', value=0.4, selected=None, **kw):
    return punch_geometry(_solid(tools), tools, list(tools) if selected is None else selected, method, value, 4,
                          ALL_ON, **kw)


def _exc(drills=(), slots=(), dia=0.4):
    tool = {'tooldia': dia, 'drills': list(drills), 'slots': list(slots)}
    geo = [p.buffer(dia / 2) for p in drills] + [LineString(s).buffer(dia / 2) for s in slots]
    return geo, {1: tool}


def test_fixed_punches_circle_and_records_clear_and_aperture():
    tools = {10: _circle_ap(0, 0)}
    solid, aps = _run(tools, value=0.4)
    assert solid.area == pytest.approx(math.pi * (0.25 - 0.04), rel=0.02)
    assert not solid.intersects(Point(0, 0))
    assert not aps[10]['geometry'][0]['solid'].intersects(Point(0, 0))
    assert tools[10]['geometry'][0]['solid'].intersects(Point(0, 0))   # input untouched
    clears = _clears(aps)
    assert len(clears) == 1 and clears[0][0] == 0.4 and clears[0][1].equals(Point(0, 0))
    # hole almost as big as the pad still gets its clear record
    _, aps = _run(tools, value=0.99)
    assert len(_clears(aps)) == 1


@pytest.mark.parametrize('method,value,code', [
    ('fixed', 0.9, 'size'),            # smaller than diagonal 1.166 but not than the 0.6 side
])
def test_obround_error_codes(method, value, code):
    with pytest.raises(ValueError) as err:
        _run({10: _obround_ap(0, 0)}, method=method, value=value)
    assert err.value.args[0] == code


def test_ring_and_prop_hole_sizes():
    tools = {10: _circle_ap(0, 0)}
    _, aps = _run(tools, method='ring', value=[0.1] * 5)
    assert _clears(aps)[0][0] == pytest.approx(0.8)
    _, aps = _run(tools, method='prop', value=0.5)
    assert _clears(aps)[0][0] == pytest.approx(0.5)


def test_excellon_keeps_clear_features_cuts_only_selected_and_clears_slots():
    tools = {10: _circle_ap(0, 0), 11: _circle_ap(5, 0)}
    tools[12] = {'type': 'C', 'size': 1.0, 'geometry': [{'clear': box(8, 8, 9, 9)}]}
    drills = [Point(0, 0), Point(5, 0)]
    geo, dtools = _exc(drills, slots=[(Point(-0.2, 0), Point(0.2, 0))])
    src = unary_union([_solid({10: tools[10], 11: tools[11]}), box(20, 20, 21, 21)])
    solid, aps = punch_geometry(src, tools, [10], 'exc', None, 4, ALL_ON, drill_geometry=geo, drill_tools=dtools)
    assert not solid.intersects(Point(0, 0)) and solid.intersects(Point(5, 0))
    assert solid.contains(Point(20.5, 20.5))
    clears = [c for c in _clears(aps) if c[0] == 0.4]
    assert any(isinstance(g, Point) for _, g in clears) and any(isinstance(g, LineString) for _, g in clears)
    assert len(clears) == 2
    assert not aps[10]['geometry'][0]['solid'].intersects(Point(0, 0))

