"""Isolation rest machining uses bounded bulk queries for sparse passes and early exit for dense passes.
The result must be the one of the test of each pass against every other polygon.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from shapely import Polygon, box

from appPlugins.ToolIsolation import ToolIsolation

TOOLDIA = 0.2
OVERLAP = 0.1


def polygons():
    """Pairs of pads at gaps from 0.05 mm to 1.5 mm, a pad with 2 holes too small for the tool and 1 with 2 large
    holes."""
    pads = []
    for column, gap in enumerate((0.05, 0.3, 0.45, 0.6, 0.8, 1.0, 1.5, 0.35, 0.7, 0.25)):
        x = column * 3.0
        pads.append(box(x, 0, x + 1, 1))
        pads.append(box(x, 1 + gap, x + 1, 2 + gap))
    pads.append(Polygon(box(0, 10, 3, 13).exterior.coords,
                        [box(0.5, 11, 0.6, 11.1).exterior.coords, box(2, 11, 2.1, 11.1).exterior.coords]))
    pads.append(Polygon(box(10, 10, 16, 14).exterior.coords,
                        [box(11, 11, 13, 13).exterior.coords, box(14, 11, 15, 13).exterior.coords]))
    return pads


def rest_passes_pairwise(geometry, tooldia, passes, overlap, negative_dia, forced_rest):
    """The passes of generate_rest_geometry() tested against every other polygon (the code before the index)."""
    work_geo, not_isolated = [], []
    for idx, geo in enumerate(geometry):
        good = []
        for nr_pass in range(passes):
            iso_offset = tooldia * ((2 * nr_pass + 1) / 2.0) - (nr_pass * overlap * tooldia)
            if negative_dia:
                iso_offset = -iso_offset
            check_geo = geo.buffer(iso_offset * 1.99999)
            if any(check_geo.intersects(other) for i, other in enumerate(geometry) if i != idx):
                continue
            temp_geo = geo.buffer(iso_offset)
            if nr_pass == 0 and forced_rest is False and len(geo.interiors) >= 1:
                parts = [temp_geo] if isinstance(temp_geo, Polygon) else list(temp_geo.geoms)
                if len(parts) != 1 or len(parts[0].interiors) != len(geo.interiors):
                    break
            good.append(temp_geo)
        if good:
            work_geo += good
        else:
            not_isolated.append(geo)
    isolated = []
    for geo in work_geo:
        for poly in getattr(geo, 'geoms', [geo]):
            if poly.is_empty:
                continue
            isolated += [poly.exterior] + list(poly.interiors)
    return isolated, not_isolated


def run_rest(geometry, passes, negative_dia=False, forced_rest=False, tooldia=TOOLDIA):
    tool = SimpleNamespace(app=SimpleNamespace(log=Mock()))
    return ToolIsolation.generate_rest_geometry(tool, geometry, tooldia, passes, OVERLAP, invert=False,
                                                env_iso_type=2, negative_dia=negative_dia, forced_rest=forced_rest)


@pytest.mark.parametrize('passes, forced_rest, negative_dia', [(3, True, True)])
def test_same_passes_as_the_pairwise_test(passes, forced_rest, negative_dia):
    geometry = polygons()
    isolated, not_isolated = run_rest(geometry, passes, negative_dia, forced_rest)
    expected_isolated, expected_not_isolated = rest_passes_pairwise(
        geometry, TOOLDIA, passes, OVERLAP, negative_dia, forced_rest)
    assert [g.wkb for g in isolated] == [g.wkb for g in expected_isolated]
    assert [g.wkb for g in not_isolated] == [g.wkb for g in expected_not_isolated]


def test_close_pads_and_small_holes_are_left_for_a_smaller_tool():
    geometry = polygons()
    __, not_isolated = run_rest(geometry, 1)
    # the 2 pads 0.05 mm apart (the check buffer of the first pass is 0.2 mm) and the pad with the small holes
    assert [g.wkb for g in not_isolated] == [g.wkb for g in geometry[0:2] + geometry[20:21]]
    __, not_isolated = run_rest(geometry, 1, forced_rest=True)
    assert [g.wkb for g in not_isolated] == [g.wkb for g in geometry[0:2]]


@pytest.mark.parametrize('negative_dia, forced_rest', [(True, True)])
def test_dense_passes_preserve_paths_and_abandoned_polygons(negative_dia, forced_rest):
    geometry = [box((i % 30) * 0.1, (i // 30) * 0.1,
                    (i % 30) * 0.1 + 0.02, (i // 30) * 0.1 + 0.02) for i in range(900)]
    geometry += [Polygon(box(10, 0, 13, 3).exterior.coords,
                         [box(10.5, 1, 10.6, 1.1).exterior.coords, box(12, 1, 12.1, 1.1).exterior.coords]),
                 box(20, 0, 22, 2)]
    actual = run_rest(geometry, 3, negative_dia, forced_rest)
    expected = rest_passes_pairwise(geometry, TOOLDIA, 3, OVERLAP, negative_dia, forced_rest)
    assert [[g.wkb for g in part] for part in actual] == [[g.wkb for g in part] for part in expected]


def test_pad_with_one_hole_that_the_tool_cannot_enter_goes_to_the_smaller_tool():
    pad = Polygon(box(0, 0, 3, 3).exterior.coords, [box(1, 1, 1.2, 1.2).exterior.coords])
    isolated, not_isolated = run_rest([pad], 1, tooldia=0.5)
    assert isolated == [] and [g.wkb for g in not_isolated] == [pad.wkb]

    isolated, not_isolated = run_rest([pad], 1, tooldia=0.05)
    assert not_isolated == []
    assert len(isolated) == 2  # the outside and the hole


