"""Pure vertex edits addressed as (part, ring, index). No Qt."""
import math

import pytest
from shapely import BufferJoinStyle
from shapely.geometry import LineString, LinearRing, Point, Polygon, MultiLineString, MultiPolygon, box

from appEditors.vertex_ops import (vertex_grips, vertex_address, ring_after_edit, edit_vertex, check_vertex_result,
                                   contrast_color, circle_center, arc_middle, arc_points, find_arcs, ring_skeleton,
                                   expand_skeleton)

SQUARE = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]
HOLE = [(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)]


def kinds(grips, kind):
    return [(address, point) for k, address, point in grips if k == kind]


def test_grips_of_open_line_polygon_hole_and_multipolygon():
    grips = vertex_grips(LineString([(0, 0), (2, 0), (2, 2)]))
    assert kinds(grips, 'vertex') == [((0, 0, 0), (0, 0)), ((0, 0, 1), (2, 0)), ((0, 0, 2), (2, 2))]
    assert kinds(grips, 'mid') == [((0, 0, 0), (1, 0)), ((0, 0, 1), (2, 1))]
    grips = vertex_grips(MultiPolygon([Polygon(SQUARE, [HOLE]), box(10, 10, 11, 11)]))
    rings = {address[:2] for address, __ in kinds(grips, 'vertex')}
    assert rings == {(0, 0), (0, 1), (1, 0)}


def test_vertex_address():
    assert vertex_address(LineString([(0, 0), (1, 0)]), 1) == (0, 0, 1)
    assert vertex_address(LineString([(0, 0), (1, 0)]), (0, 0, 1)) == (0, 0, 1)
    with pytest.raises(ValueError, match='LineString'):
        vertex_address(MultiLineString([[(0, 0), (1, 0)], [(2, 0), (3, 0)]]), 0)


def test_edit_open_line_and_hole():
    line = LineString([(0, 0), (2, 0), (4, 0)])
    assert list(edit_vertex(line, 'move', (0, 0, 1), (2, 1)).coords) == [(0, 0), (2, 1), (4, 0)]
    assert list(edit_vertex(line, 'insert', (0, 0, 0), (1, 0)).coords) == [(0, 0), (1, 0), (2, 0), (4, 0)]
    assert list(edit_vertex(line, 'remove', (0, 0, 1), None).coords) == [(0, 0), (4, 0)]
    # a hole is edited without touching the exterior
    result = edit_vertex(Polygon(SQUARE, [HOLE]), 'move', (0, 1, 0), (1.5, 0.5))
    assert list(result.exterior.coords) == SQUARE
    assert list(result.interiors[0].coords)[0] == (1.5, 0.5)


def test_move_vertex_zero_or_closing_alias_moves_both_ends():
    for index in (0, 4):
        result = edit_vertex(Polygon(SQUARE), 'move', (0, 0, index), (-1, -1))
        coords = list(result.exterior.coords)
        assert coords[0] == coords[-1] == (-1, -1) and len(coords) == 5


def test_insert_off_segment_needs_on_segment_false():
    line = LineString([(0, 0), (2, 0)])
    with pytest.raises(ValueError):
        edit_vertex(line, 'insert', (0, 0, 0), (1, 1), on_segment=True)
    assert list(edit_vertex(line, 'insert', (0, 0, 0), (1, 1)).coords) == [(0, 0), (1, 1), (2, 0)]


def test_bad_edits_raise():
    short, line = LineString([(0, 0), (1, 0)]), LineString([(0, 0), (2, 0)])
    for geometry, action, address, point in ((short, 'remove', (0, 0, 0), None), (line, 'move', (0, 0, 0), (1,)),
                                             (line, 'move', (0, 1, 0), (1, 0)), (line, 'unknown', (0, 0, 0), (1, 0))):
        with pytest.raises(ValueError):
            edit_vertex(geometry, action, address, point)


def test_check_vertex_result_rejects_invalid():
    for geometry in (LineString([(0, 0), (2, 0), (2, 2), (1, -1)]),
                     Polygon(SQUARE, [[(3, 3), (5, 3), (5, 5), (3, 5)]])):
        with pytest.raises(ValueError):
            check_vertex_result(geometry, (0, 0, 0))


ROUNDED = box(1, 1, 9, 5).buffer(1, join_style=BufferJoinStyle.round)   # 4 corner arcs of radius 1
CORNERS = [(1, 1), (9, 1), (9, 5), (1, 5)]


def ring_vertices(ring):
    coords = [tuple(coord) for coord in ring.coords]
    return coords[:-1] if coords[0] == coords[-1] else coords


def s_curve():
    """An open line: 1 straight segment, 2 quarter arcs that share the vertex (1, 1), 1 straight segment."""
    half = math.sqrt(0.5)
    first = arc_points((0, 0), (1 - half, half), (1, 1), 64)
    second = arc_points((1, 1), (1 + half, 2 - half), (2, 2), 64)
    return [(-3, 0)] + first + second[1:] + [(2, 5)]


def test_arc_middle_and_arc_points():
    through = (math.cos(0.3), math.sin(0.3))
    assert arc_middle((1, 0), through, (0, 1)) == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))
    assert arc_middle((0, 0), (1, 0), (2, 0)) == (1, 0)   # on 1 line: the middle of the segment
    points = arc_points((1, 0), (math.sqrt(0.5), math.sqrt(0.5)), (0, 1), 32)
    assert len(points) == 9   # a quarter of 32 steps
    assert points[0] == (1, 0) and points[-1] == (0, 1)
    assert all(math.dist(point, (0, 0)) == pytest.approx(1) for point in points)
    assert len(arc_points((1, 0), (math.cos(0.05), math.sin(0.05)), (math.cos(0.1), math.sin(0.1)), 32)) == 4
    assert len(arc_points((1, 0), (0, 1), (-1, 0), 4)) == 7   # 4 steps: 2 segments, but at most 30 degrees each
    assert arc_points((0, 0), (1, 0), (2, 0), 32) == [(0, 0), (2, 0)]


def test_find_arcs_rounded_rectangle_and_s_curve():
    vertices = ring_vertices(ROUNDED.exterior)
    arcs = find_arcs(vertices, True)
    assert len(arcs) == 4 and not any(arc.full for arc in arcs)
    for arc in arcs:
        center = circle_center(vertices[arc.start], arc.middle, vertices[arc.end])
        assert math.dist(center, vertices[arc.start]) == pytest.approx(1)
        assert min(math.dist(center, corner) for corner in CORNERS) == pytest.approx(0, abs=1e-9)
    # an open S curve: the 2 arcs share a vertex
    vertices = s_curve()
    arcs = find_arcs(vertices, False)
    assert len(arcs) == 2
    assert arcs[0].end == arcs[1].start
    assert vertices[arcs[0].end] == (1, 1)


def test_find_arcs_finds_no_arc():
    for vertices, closed in (([(0, 0), (1, 0), (1, 1), (0, 1)], True),                 # a square
                             ([(0, 0), (4, 0), (5, 2), (3, 5), (0, 3)], True),         # a plain polygon
                             ([(0, 0), (1, 0), (1.99, 0.14)], False),                  # only 2 equal segments
                             ([(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)], False)):       # a straight run
        assert find_arcs(vertices, closed) == []


def test_skeleton_round_trip_of_a_rounded_rectangle_and_others():
    vertices = ring_vertices(ROUNDED.exterior)
    nodes, links = ring_skeleton(vertices, True)
    assert len(nodes) == 8 and len(links) == 8
    assert sum(1 for link in links if link is not None) == 4
    rebuilt = expand_skeleton([vertices[node] for node in nodes], links, True, 64)
    assert rebuilt[0] == rebuilt[-1]
    assert Polygon(rebuilt).symmetric_difference(ROUNDED).area < 1e-9
    # an open S curve and a full circle
    vertices = s_curve()
    nodes, links = ring_skeleton(vertices, False)
    assert nodes == [0, 1, 17, 33, 34]
    assert [link is None for link in links] == [True, False, False, True]
    assert ring_skeleton(ring_vertices(Point(0, 0).buffer(2).exterior), True) == ([], [])


def grips_of(geometry, kind):
    return [(address, point) for k, address, point in vertex_grips(geometry) if k == kind]


def on_some_arc(ring, point, closed=True):
    """True if point is on the circle of 1 of the arcs of ring. A point on the circle is not always on the chords."""
    vertices = ring_vertices(ring)
    for arc in find_arcs(vertices, closed):
        center = circle_center(vertices[arc.start], arc.middle, vertices[arc.end])
        if abs(math.dist(center, point) - math.dist(center, vertices[arc.start])) < 1e-9:
            return True
    return False


def test_grips_of_a_rounded_rectangle_and_a_full_circle():
    assert len(grips_of(ROUNDED, 'arc_end')) == 8
    assert grips_of(ROUNDED, 'vertex') == []
    assert len(grips_of(ROUNDED, 'mid')) == 4
    arc_mids = grips_of(ROUNDED, 'arc_mid')
    assert len(arc_mids) == 4 and all(len(address) == 4 for address, __ in arc_mids)
    assert all(on_some_arc(ROUNDED.exterior, point) for __, point in arc_mids)
    # a full circle
    radius_grips = grips_of(Point(0, 0).buffer(2), 'radius')
    assert [address for address, __ in radius_grips] == [(0, 0, 0)] * 4
    assert [value for __, point in radius_grips for value in point] == pytest.approx([2, 0, 0, 2, -2, 0, 0, -2])
    assert len(vertex_grips(Point(0, 0).buffer(2))) == 4


def test_arc_mid_and_arc_end_edits():
    address, middle = grips_of(ROUNDED, 'arc_mid')[0]
    cursor = (middle[0] - 0.3, middle[1] - 0.3)
    result = edit_vertex(ROUNDED, 'arc_mid', address, cursor, steps=64)
    check_vertex_result(result, address)
    assert on_some_arc(result.exterior, cursor)
    assert len(find_arcs(ring_vertices(result.exterior), True)) == 4

    (address, point), = [(a, p) for a, p in grips_of(ROUNDED, 'arc_end') if p == (1.0, 0.0)]
    middle = arc_middle((1, 0), (1 - math.sqrt(0.5), 1 - math.sqrt(0.5)), (0, 1))
    result = edit_vertex(ROUNDED, 'arc_end', address, (1.3, 0), steps=64)
    check_vertex_result(result, address)
    assert (1.3, 0) in list(result.exterior.coords)
    assert on_some_arc(result.exterior, middle)
    assert len(find_arcs(ring_vertices(result.exterior), True)) == 4

    # an end shared by 2 arcs changes both
    line = LineString(s_curve())
    result = edit_vertex(line, 'arc_end', (0, 0, 17), (1.2, 1), steps=64)
    arcs = find_arcs(list(result.coords), False)
    assert len(arcs) == 2 and arcs[0].end == arcs[1].start
    assert result.coords[arcs[0].end] == (1.2, 1)


def test_arc_radius_scales_a_full_circle():
    circle = Point(1, 1).buffer(2)
    result = edit_vertex(circle, 'arc_radius', (0, 0, 0), (4, 1))
    assert len(result.exterior.coords) == len(circle.exterior.coords)
    assert all(math.dist(coord, (1, 1)) == pytest.approx(3) for coord in result.exterior.coords)


def test_bad_arc_edits_raise():
    for geometry, action, address, point in ((box(0, 0, 4, 4), 'arc_end', (0, 0, 1), (5, 0)),
                                             (ROUNDED, 'arc_mid', (0, 0, 1), (0, 0)),
                                             (ROUNDED, 'arc_mid', (0, 0, 1, 17), None)):
        with pytest.raises(ValueError):
            edit_vertex(geometry, action, address, point)
