"""camlib.polygon_gap_pairs: the minimum distance between copper polygons without a check of every pair."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from shapely import Point, box
from shapely.ops import nearest_points

import camlib

DEC = 4


def every_pair(polygons, decimals=DEC):
    pairs = {}
    for i, geo in enumerate(polygons):
        for s_geo in polygons[i + 1:]:
            a, b = nearest_points(geo, s_geo)
            pairs.setdefault(float('%.*f' % (decimals, geo.distance(s_geo))), []).append(
                ((float('%.*f' % (decimals, a.x)), float('%.*f' % (decimals, a.y))),
                 (float('%.*f' % (decimals, b.x)), float('%.*f' % (decimals, b.y)))))
    return pairs


def board():
    random.seed(7)
    pads = [Point(random.uniform(0, 30), random.uniform(0, 20)).buffer(random.uniform(0.2, 0.6), quad_segs=8)
            for _ in range(40)]
    # a ground pour with a hole that holds a pad, and 2 pairs at the same distance
    pour = box(-10, -10, 40, -2).union(box(-10, -10, -4, 30)).difference(box(-8, -8, -6, -6))
    tie = [box(32, 0, 33, 1), box(33.5, 0, 34.5, 1), box(32, 3, 33, 4), box(33.5, 3, 34.5, 4)]
    polygons = camlib.flatten_shapely_geometry(camlib.unary_union(pads + tie + [pour, box(-7.5, -7.5, -6.5, -6.5)]))
    return polygons


def test_neighbors_have_the_same_distances_and_pairs_as_every_pair():
    polygons = board()
    old = every_pair(polygons)
    new = camlib.polygon_gap_pairs(polygons, DEC, neighbors=True)
    assert min(new) == min(old)
    for dist, locations in new.items():
        assert len(locations) == len(old[dist])
        for (x1, y1), (x2, y2) in locations:
            assert abs(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 - dist) <= 2 * 10 ** -DEC
    # each polygon keeps its nearest polygon: the largest nearest distance is in the result
    nearest = [min(p.distance(q) for q in polygons if q is not p) for p in polygons]
    assert max(new) == float('%.*f' % (DEC, max(nearest)))


def test_isolation_check_validity_uses_the_minimum():
    from appPlugins.ToolIsolation import ToolIsolation as tool
    storage = {10: {'geometry': [{'solid': g} for g in board()]}}
    msg, dist = tool.find_optim_mp(storage, DEC)
    assert msg == 'ok'
    assert dist == pytest.approx(min(every_pair(board())) - 10 ** -DEC, abs=2 * 10 ** -DEC)
