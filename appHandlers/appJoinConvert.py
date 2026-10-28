"""
Conversion of Gerber, Excellon and Geometry objects for Project context menu -> Join -> As Gerber / As Excellon /
As Geometry.

join_sources() returns, for each selected object, a source that the merge() of the target kind accepts: an object of
the target kind is used as it is, an object of another kind is converted in memory (the project is not changed).

Conversion rules:
- To Excellon: a flash of a circular aperture is a drill, a track of a circular aperture is a slot for each segment.
  A shape (a region, a flash of another aperture, a Geometry polygon or closed path) is a drill if it is a circle and
  a slot if it is an obround; other shapes are not converted. A Geometry point is a drill and a Geometry open path is a
  slot for each segment, with the diameter of the Geometry tool.
- To Gerber: an Excellon drill is a flash and a slot is a track of a circular aperture with the tool diameter.
  A Geometry polygon is a region. A Geometry path or point with a tool diameter is a track or a flash of a circular
  aperture with that diameter; a closed path without a tool diameter is a region.
- To Geometry: the shapes of a Gerber or of an Excellon object, with the default Geometry tools. If one of the
  Geometry objects is MultiGeo, the result is MultiGeo and the SingleGeo objects give their shapes to their tools.
"""

import math
from copy import deepcopy
from types import SimpleNamespace

from shapely.geometry import LinearRing, LineString, Point, Polygon

from camlib import flatten_shapely_geometry

JOIN_KINDS = ('gerber', 'excellon', 'geometry')

# a shape is a drill or a slot if no point of it is farther than this part of the hole radius from the ideal hole
HOLE_TOLERANCE = 0.1


def parts(geo):
    """The simple shapes (Polygon, LineString, LinearRing, Point) of a geometry, a list or None."""
    if geo is None:
        return []
    return [g for g in flatten_shapely_geometry(geo) if not g.is_empty]


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def geometry_shapes(obj):
    """
    :param obj: a Geometry object
    :return:    [(tool diameter, shapes)]: of each tool for a MultiGeo object, else the shapes of the object with the
                diameter of its first tool (0.0 if it has no tool)
    """
    if getattr(obj, 'multigeo', False):
        return [(as_float(tool.get('tooldia')), parts(tool.get('solid_geometry'))) for tool in obj.tools.values()]
    dias = [as_float(tool.get('tooldia')) for tool in (obj.tools or {}).values()]
    return [(dias[0] if dias else 0.0, parts(obj.solid_geometry))]


def segments(line):
    coords = list(line.coords)
    return [(Point(a), Point(b)) for a, b in zip(coords, coords[1:]) if a != b]


def is_closed(line):
    return isinstance(line, LinearRing) or (len(line.coords) > 3 and line.coords[0] == line.coords[-1])


def classify_hole(poly, tolerance=HOLE_TOLERANCE):
    """
    :param poly:        a shapely Polygon
    :param tolerance:   the most a point of the polygon can be off the ideal hole, as a part of the hole radius
    :return:            ('drill', center Point, diameter), ('slot', (start Point, stop Point), width) or None if the
                        polygon is not a circle and not an obround
    """
    if not isinstance(poly, Polygon) or poly.is_empty or poly.area <= 0 or poly.interiors:
        return None
    rect = poly.minimum_rotated_rectangle
    if not isinstance(rect, Polygon):
        return None
    c = list(rect.exterior.coords)
    edges = [(c[0], c[1]), (c[1], c[2])]
    lengths = [math.dist(a, b) for a, b in edges]
    long_edge = 0 if lengths[0] >= lengths[1] else 1
    length, width = lengths[long_edge], lengths[1 - long_edge]
    if width <= 0:
        return None
    radius = width / 2.0
    center = rect.centroid

    # the vertices of a circle (or of an obround) made of segments are on the circle: the farthest vertex from the
    # center (or from the center line) gives the radius
    if poly.hausdorff_distance(center.buffer(radius, quad_segs=64)) <= tolerance * radius:
        return 'drill', center, 2.0 * center.hausdorff_distance(poly.exterior)

    (ax, ay), (bx, by) = edges[long_edge]
    ux, uy = (bx - ax) / length, (by - ay) / length
    half = (length - width) / 2.0
    start = Point(center.x - ux * half, center.y - uy * half)
    stop = Point(center.x + ux * half, center.y + uy * half)
    center_line = LineString([start, stop])
    if poly.hausdorff_distance(center_line.buffer(radius, quad_segs=64)) <= tolerance * radius:
        return 'slot', (start, stop), 2.0 * center_line.hausdorff_distance(poly.exterior)
    return None


class HoleTools:
    """The tools of an Excellon source, 1 tool for each diameter."""

    def __init__(self, decimals):
        self.decimals = decimals
        self.tools = {}

    def tool(self, dia):
        dia = round(float(dia), self.decimals)
        for tool in self.tools.values():
            if tool['tooldia'] == dia:
                return tool
        tool = {'tooldia': dia, 'drills': [], 'slots': [], 'solid_geometry': []}
        self.tools[len(self.tools) + 1] = tool
        return tool

    def drill(self, point, dia):
        self.tool(dia)['drills'].append(Point(point.x, point.y))

    def slot(self, start, stop, dia):
        self.tool(dia)['slots'].append((Point(start.x, start.y), Point(stop.x, stop.y)))

    def path(self, line, dia):
        for start, stop in segments(line):
            self.slot(start, stop, dia)

    def shape(self, poly):
        """:return: True if the polygon is a drill or a slot"""
        hole = classify_hole(poly)
        if hole is None:
            return False
        if hole[0] == 'drill':
            self.drill(hole[1], hole[2])
        else:
            self.slot(hole[1][0], hole[1][1], hole[2])
        return True


class Apertures:
    """The apertures of a Gerber source: the region aperture 0 and 1 circular aperture for each diameter."""

    def __init__(self, decimals, quad_segs):
        self.decimals = decimals
        self.quad_segs = quad_segs
        self.tools = {}
        self.solid = []
        self.follow = []
        self.has_holes = False

    def add(self, aperture, solid, follow):
        aperture['geometry'].append({'solid': solid, 'follow': follow})
        self.solid.append(solid)
        self.follow.append(follow)

    def circle(self, dia):
        dia = round(float(dia), self.decimals)
        for ap_id, aperture in self.tools.items():
            if ap_id != 0 and aperture['size'] == dia:
                return aperture
        ap_id = max([10] + [k + 1 for k in self.tools])
        self.tools[ap_id] = {'type': 'C', 'size': dia, 'geometry': []}
        return self.tools[ap_id]

    def region(self, poly):
        aperture = self.tools.setdefault(0, {'type': 'REG', 'size': 0.0, 'geometry': []})
        self.has_holes = self.has_holes or bool(poly.interiors)
        self.add(aperture, poly, poly.exterior)

    def flash(self, point, dia):
        self.add(self.circle(dia), point.buffer(dia / 2.0, quad_segs=self.quad_segs), point)

    def track(self, line, dia):
        self.add(self.circle(dia), line.buffer(dia / 2.0, quad_segs=self.quad_segs), line)


def gerber_to_excellon(obj, holes):
    """:return: the number of shapes that are not a drill and not a slot"""
    skipped = 0
    for aperture in (obj.tools or {}).values():
        size = as_float(aperture.get('size'))
        circular = aperture.get('type') == 'C' and size > 0
        for element in aperture.get('geometry') or []:
            solid = element.get('solid')
            if solid is None or solid.is_empty:
                # an element of clear polarity only
                continue
            follow = element.get('follow')
            if circular and isinstance(follow, Point):
                holes.drill(follow, size)
                continue
            if circular and isinstance(follow, LineString) and segments(follow):
                holes.path(follow, size)
                continue
            for part in parts(solid):
                if not holes.shape(part):
                    skipped += 1
    return skipped


def geometry_to_excellon(obj, holes):
    skipped = 0
    for dia, shapes in geometry_shapes(obj):
        for shape in shapes:
            if isinstance(shape, Point):
                if dia > 0:
                    holes.drill(shape, dia)
                    continue
            elif isinstance(shape, Polygon):
                if holes.shape(shape):
                    continue
            elif is_closed(shape):
                if len(shape.coords) > 3 and holes.shape(Polygon(shape.coords)):
                    continue
            elif dia > 0 and segments(shape):
                holes.path(shape, dia)
                continue
            skipped += 1
    return skipped


def excellon_to_gerber(obj, apertures):
    for tool in (obj.tools or {}).values():
        dia = as_float(tool.get('tooldia'))
        if dia <= 0:
            continue
        for drill in tool.get('drills') or []:
            apertures.flash(drill, dia)
        for start, stop in tool.get('slots') or []:
            apertures.track(LineString([start, stop]), dia)
    return 0


def geometry_to_gerber(obj, apertures):
    skipped = 0
    for dia, shapes in geometry_shapes(obj):
        for shape in shapes:
            if isinstance(shape, Polygon):
                apertures.region(shape)
            elif isinstance(shape, Point) and dia > 0:
                apertures.flash(shape, dia)
            elif isinstance(shape, LineString) and dia > 0:
                apertures.track(shape, dia)
            elif isinstance(shape, LineString) and is_closed(shape) and Polygon(shape.coords).area > 0:
                apertures.region(Polygon(shape.coords))
            else:
                skipped += 1
    return skipped


def object_shapes(obj):
    """The shapes of a Gerber or an Excellon object."""
    if obj.kind == 'excellon':
        shapes = [g for tool in (obj.tools or {}).values() for g in parts(tool.get('solid_geometry'))]
        return shapes or parts(obj.solid_geometry)
    return parts(obj.solid_geometry)


def geometry_source(shapes, multigeo, geo_tools):
    """A SingleGeo or a MultiGeo source with the shapes in the object and in each of its tools."""
    tools = deepcopy(geo_tools)
    for tool in tools.values():
        tool['solid_geometry'] = list(shapes)
    return SimpleNamespace(kind='geometry', obj_options={}, solid_geometry=list(shapes), tools=tools,
                           multigeo=multigeo)


def join_sources(objs, kind, decimals=4, quad_segs=16, geo_tools=None):
    """
    :param objs:        the selected Gerber, Excellon and Geometry objects
    :param kind:        'gerber', 'excellon' or 'geometry': the kind of the joined object
    :param decimals:    the decimals of the tool and aperture diameters
    :param quad_segs:   the segments in a quarter of a circle of a Gerber flash or track
    :param geo_tools:   the default tools of a converted Geometry (their 'solid_geometry' is replaced)
    :return:            (sources, skipped, multigeo): the sources for the merge() of the kind, the number of shapes
                        that can not be converted, and for a Geometry if the result is MultiGeo
    """
    multigeo = any(o.kind == 'geometry' and getattr(o, 'multigeo', False) for o in objs)
    sources = []
    skipped = 0

    for obj in objs:
        if obj.kind == kind and (kind != 'geometry' or getattr(obj, 'multigeo', False) == multigeo):
            sources.append(obj)
        elif kind == 'geometry':
            if obj.kind == 'geometry':
                # a SingleGeo object in a MultiGeo join: its tools get its shapes
                shapes = parts(obj.solid_geometry)
                tools = obj.tools if obj.tools else geo_tools or {}
                source = geometry_source(shapes, True, tools)
                source.obj_options = obj.obj_options
            else:
                shapes = object_shapes(obj)
                source = geometry_source(shapes, multigeo, geo_tools or {})
            if shapes:
                sources.append(source)
        elif kind == 'excellon':
            holes = HoleTools(decimals)
            convert = gerber_to_excellon if obj.kind == 'gerber' else geometry_to_excellon
            skipped += convert(obj, holes)
            if holes.tools:
                sources.append(SimpleNamespace(kind='excellon', obj_options={}, tools=holes.tools,
                                               solid_geometry=[], zeros=None, units=None))
        else:
            apertures = Apertures(decimals, quad_segs)
            convert = excellon_to_gerber if obj.kind == 'excellon' else geometry_to_gerber
            skipped += convert(obj, apertures)
            if apertures.tools:
                sources.append(SimpleNamespace(kind='gerber', obj_options={}, tools=apertures.tools,
                                               solid_geometry=apertures.solid, follow_geometry=apertures.follow,
                                               export_as_regions=apertures.has_holes))

    return sources, skipped, multigeo
