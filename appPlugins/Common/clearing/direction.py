# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Cut direction of the clearing paths (NCC and Paint). Pure: shapely and numpy only.

The rule is "the wall on the right" for a climb cut with an M3 spindle. The wall of a clearing domain is what is
not cleared: outside its outer boundary and inside its holes (islands). So a ring that comes from the outer boundary
of a domain is CCW, a ring around an island (a hole of the domain) is CW. The conventional cut is the opposite.
This equals camlib.milling_orient(domain, conventional=True) as seen from the domain.

Seed arcs follow the same rule for the disc around the seed that they clear: CCW around the seed for a climb cut.
Gap rings follow the rule for the gap polygon that they clear (exterior CCW, interiors CW).
"""

import numpy as np
import shapely

from shapely.geometry import LinearRing, LineString, MultiPolygon, Polygon

CLIMB = 'climb'
CONV = 'conv'


def outer_ccw(direction):
    """True: the rings of the outer boundary are CCW; False: CW; None: the direction is not set."""
    if direction == CLIMB:
        return True
    if direction == CONV:
        return False
    return None


def signed_area(coords):
    """The shoelace area of a ring (positive: CCW); coords is an (n, 2) array."""
    coords = np.asarray(coords, dtype=float)
    if len(coords) < 3:
        return 0.0
    x, y = coords[:, 0], coords[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def is_closed(path):
    return len(path.coords) >= 4 and tuple(path.coords[0]) == tuple(path.coords[-1])


def orient_ring(ring, ccw):
    """The closed ring with the wanted turn (CCW when ccw is True). An open path or a degenerate ring is returned as is."""
    if ccw is None or not is_closed(ring):
        return ring
    area = signed_area(shapely.get_coordinates(ring))
    if area == 0.0 or (area > 0.0) == ccw:
        return ring
    coords = np.asarray(ring.coords)[::-1]
    return LinearRing(coords) if isinstance(ring, LinearRing) else LineString(coords)


def polygon_rings(polygon, direction=None):
    """[exterior, *interiors] of a polygon; with a direction the exterior has the outer turn and the holes the opposite."""
    ccw = outer_ccw(direction)
    rings = [polygon.exterior, *polygon.interiors]
    if ccw is None:
        return rings
    return [orient_ring(ring, ccw if index == 0 else not ccw) for index, ring in enumerate(rings)]


def orient_polygon(polygon, direction):
    """The polygon with the rings of the direction (a Polygon or a MultiPolygon); the input when no direction is set."""
    if outer_ccw(direction) is None:
        return polygon
    if isinstance(polygon, MultiPolygon):
        return MultiPolygon([orient_polygon(part, direction) for part in polygon.geoms])
    if not isinstance(polygon, Polygon) or polygon.is_empty:
        return polygon
    exterior, *interiors = polygon_rings(polygon, direction)
    return Polygon(exterior, interiors)


def orient_sweep(path, center, direction):
    """
    The arc (or ring) of a circle around ``center`` with the turn of the direction: CCW around the center for a
    climb cut. The sign of the sum of cross products about the center is the turn, also for an arc of any length.
    """
    ccw = outer_ccw(direction)
    if ccw is None or path is None or path.is_empty or not isinstance(path, LineString):
        return path
    xy = shapely.get_coordinates(path) - np.asarray(center, dtype=float)
    if len(xy) < 2:
        return path
    turn = float(np.sum(xy[:-1, 0] * xy[1:, 1] - xy[1:, 0] * xy[:-1, 1]))
    if turn == 0.0 or (turn > 0.0) == ccw:
        return path
    coords = np.asarray(path.coords)[::-1]
    return LinearRing(coords) if isinstance(path, LinearRing) else LineString(coords)


def path_turn(path):
    """+1 for a CCW ring, -1 for a CW ring, 0 for an open or degenerate path."""
    if not is_closed(path):
        return 0
    area = signed_area(shapely.get_coordinates(path))
    return int(np.sign(area))
