"""Distance math and geometry-only snap selection."""

import math
import numpy as np
import shapely
from shapely import Point, MultiLineString, LineString, LinearRing, Polygon
from shapely.strtree import STRtree
from appParsers.feature_access import iter_features
from camlib import flatten_shapely_geometry


def measurement_angle(dx, dy):
    angle = math.degrees(math.atan2(dy, dx))
    if angle < 0:
        angle += 360
    return angle


def point_deltas(pos, previous):
    return pos[0] - previous[0], pos[1] - previous[1]


def point_distance(pos, previous):
    dx, dy = point_deltas(pos, previous)
    return math.sqrt(dx ** 2 + dy ** 2)


# Click state machine. The tool stays active across measurements:
#   READY (no open measurement)  left -> NEW            right -> EXIT
#   OPEN, single, 1 point        left -> COMPLETE       right -> CANCEL
#   OPEN, single, 2 points       left -> NEW            right -> CLOSE
#   OPEN, multipoint             left -> ADD            right -> CLOSE (CANCEL with < 2 points)
NEW, ADD, COMPLETE, CLOSE, CANCEL, EXIT = 'new', 'add', 'complete', 'close', 'cancel', 'exit'


def click_action(is_open, button, multipoint, n_points):
    """Action for a mouse click (button 1 = left, 2 = right); None for other buttons."""
    if button == 1:
        if not is_open or n_points == 0 or (not multipoint and n_points >= 2):
            return NEW
        return ADD if multipoint else COMPLETE
    if button == 2:
        if not is_open:
            return EXIT
        return CLOSE if n_points >= 2 else CANCEL
    return None


def preview_active(is_open, multipoint, n_points):
    """True while the rubber-band segment from the last point to the cursor is shown."""
    return is_open and n_points > 0 and (multipoint or n_points == 1)


def format_length(value, decimals):
    return '%.*f' % (decimals, abs(value))


def dimension_segments(points, decimals, cursor=None):
    """(p0, p1, length text) of each committed segment, plus the preview to the cursor when given.

    Zero-length segments are left out: they have no direction to draw.
    """
    pts = [(p[0], p[1]) for p in points]
    pairs = list(zip(pts, pts[1:]))
    if cursor is not None and pts:
        pairs.append((pts[-1], (cursor[0], cursor[1])))
    return [(a, b, format_length(point_distance(b, a), decimals)) for a, b in pairs if a != b]


def app_snap_centers(kind, features, current_pt):
    clicked_geo = []
    for feature in features:
        # A clear (LPC) feature is not a pad.
        if feature.polarity != 'dark' or not feature.solid.covers(current_pt):
            continue
        if kind == 'gerber':
            if feature.kind == 'flash':
                clicked_geo.append(feature.follow)
        else:
            clicked_geo.append(feature.solid.centroid)
    return clicked_geo


def closest_center(points, current_pt):
    return min(points, key=current_pt.distance)


def exc_snap_position(geometry, pos):
    current_pt = Point(pos)
    if isinstance(geometry, MultiLineString):
        radius = geometry.geoms[0].length / 2.0
        center_pt = geometry.centroid
        geo_buffered = center_pt.buffer(radius)
        if geo_buffered.covers(current_pt):
            pos = (center_pt.x, center_pt.y)
    elif isinstance(geometry, Polygon):
        geo_buffered = geometry.buffer(0)
        center_pt = geo_buffered.centroid
        if geo_buffered.covers(current_pt):
            pos = (center_pt.x, center_pt.y)
    return pos


def trace_half_width(size):
    """Half the width of a trace: half the aperture 'size' (the diameter of a C aperture), 0 when it is unknown."""
    return size / 2.0 if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0 else 0.0


class VertexIndex:
    """End points and vertices of the Gerber traces, queried with an STRtree.

    ``vertices`` is a list of ``(coordinates, half width)``. A vertex snaps when the cursor is not farther than
    ``max(half width of its trace, tolerance)``; the tolerance is the screen tolerance in data units. With
    ``copper`` given, only the vertices that lie on the composed copper are kept: a vertex a clear object
    erased is not a target.
    """

    def __init__(self, vertices, copper=None):
        coords = np.array([v[0] for v in vertices], dtype=float).reshape(-1, 2)
        halves = np.array([v[1] for v in vertices], dtype=float)
        if copper is not None and len(coords):
            hits = STRtree(copper).query(shapely.points(coords), predicate='covered_by')[0]
            keep = np.unique(hits)
            coords, halves = coords[keep], halves[keep]
        self.coords, self.halves = coords, halves
        self.max_half = float(halves.max()) if len(halves) else 0.0
        self.tree = STRtree(shapely.points(coords)) if len(coords) else None

    def closest(self, current_pt, tolerance=0.0):
        if self.tree is None:
            return None
        tolerance = tolerance if tolerance == tolerance and tolerance > 0 else 0.0
        hits = self.tree.query(current_pt, predicate='dwithin', distance=max(self.max_half, tolerance))
        best = None
        for i in hits:
            d = math.hypot(self.coords[i][0] - current_pt.x, self.coords[i][1] - current_pt.y)
            if d <= max(self.halves[i], tolerance) and (best is None or (d, int(i)) < best):
                best = (d, int(i))
        return None if best is None else Point(self.coords[best[1]])


def trace_vertices(follow, size):
    """The ``(xy, half width)`` entries of a trace follow line; nothing for any other geometry."""
    if not isinstance(follow, (LineString, MultiLineString, LinearRing)) or follow.is_empty:
        return []
    half = trace_half_width(size)
    return [((float(x), float(y)), half) for x, y in shapely.get_coordinates(follow)]


class SnapIndex:
    """A session snapshot: footprint containment first, nearest center second.

    ``covered_by`` includes the exact footprint boundary. Gerber copper is queried
    separately: aperture records don't preserve the global polarity order.
    """

    def __init__(self, footprints, centers, copper=None):
        self.tree = STRtree(footprints)
        self.centers = centers
        self.copper = None if copper is None else STRtree(copper)
        self.editor_shapes: list | None = None
        self.vertex_source = None     # callable that builds the VertexIndex on first use
        self._vertices = None

    def closest_vertex(self, current_pt, tolerance=0.0):
        if self._vertices is None and self.vertex_source is not None:
            self._vertices = self.vertex_source()
        return None if self._vertices is None else self._vertices.closest(current_pt, tolerance)

    def closest(self, current_pt):
        if self.copper is not None and not self.copper.query(current_pt, predicate='covered_by').size:
            return None
        hits = self.tree.query(current_pt, predicate='covered_by')
        if not hits.size:
            return None
        # Stable tie breaking, without constructing another tree for a handful of hits.
        if self.editor_shapes is not None:
            # AppRTreeStorage.remove() leaves a tombstone without changing list length.
            # Check only spatial hits, not every stored drill on every hover.
            live_hits = []
            for i in hits:
                storage, shape = self.editor_shapes[i]
                obj_index = storage.indexes.get(id(shape))
                if obj_index is not None and storage.objects[obj_index] is shape:
                    live_hits.append(i)
            hits = live_hits
        index = min(hits, key=lambda i: (current_pt.distance(self.centers[i]), int(i)), default=None)
        return self.centers[index] if index is not None else None


def app_snap_index(obj):
    footprints, centers = [], []
    for feature in iter_features(obj):
        if feature.polarity != 'dark' or feature.solid.is_empty:
            continue
        if obj.kind == 'gerber' and (feature.kind != 'flash' or not isinstance(feature.follow, Point)):
            continue
        footprints.append(feature.solid)
        centers.append(feature.follow if obj.kind == 'gerber' else feature.solid.centroid)
    copper = flatten_shapely_geometry(obj.solid_geometry) if obj.kind == 'gerber' else None
    index = SnapIndex(footprints, centers, copper=copper)
    if obj.kind == 'gerber':
        def build():
            vertices = []
            for feature in iter_features(obj):
                if feature.kind == 'route' and feature.polarity == 'dark':
                    vertices.extend(trace_vertices(feature.follow, feature.size))
            return VertexIndex(vertices, copper)
        index.vertex_source = build
    return index


def editor_snap_index(source, storage_dict):
    footprints, centers = [], []
    editor_shapes = []
    for storage in storage_dict.values():
        shapes = storage.get_objects() if source == 'exc_editor' else storage.get('geometry', ())
        for shape in shapes:
            geometry = shape.geo
            if source == 'exc_editor':
                if isinstance(geometry, MultiLineString) and len(geometry.geoms):
                    center = geometry.centroid
                    solid = center.buffer(geometry.geoms[0].length / 2.0)
                elif isinstance(geometry, Polygon):
                    solid = geometry.buffer(0)
                    center = solid.centroid
                else:
                    continue
            else:
                # The editor displays edited solids. LPC imports are already composed
                # into regions by AppGerberEditor.build_edit_tools().
                solid, center = geometry.get('solid'), geometry.get('follow')
                if not isinstance(center, Point) or solid is None:
                    continue
            if not solid.is_empty and not center.is_empty:
                footprints.append(solid)
                centers.append(center)
                if source == 'exc_editor':
                    editor_shapes.append((storage, shape))
    index = SnapIndex(footprints, centers)
    if source == 'exc_editor':
        index.editor_shapes = editor_shapes
    else:
        def build():
            vertices = []
            for key, storage in storage_dict.items():
                if key == 0 or storage.get('type') == 'REG':
                    continue
                for shape in storage.get('geometry', ()):
                    geometry = shape.geo
                    solid = geometry.get('solid')
                    if solid is not None and not solid.is_empty:
                        vertices.extend(trace_vertices(geometry.get('follow'), storage.get('size')))
            return VertexIndex(vertices)
        index.vertex_source = build
    return index
