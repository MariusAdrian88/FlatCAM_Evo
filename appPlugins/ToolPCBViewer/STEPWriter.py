"""ISO 10303-21 STEP (AP214) writer: extruded PCB layers as colored B-rep solids.

Each layer is one part of a 'PCB' assembly. Each polygon of a layer is one closed solid: a top and a bottom
planar face (with the holes as inner bounds) and one planar wall for each ring edge. The writer needs no CAD
kernel. The faces share their edges, so a CAD kernel reads closed shells and does not heal them.
"""

import datetime
import math
import os
from collections import Counter

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient


def _real(value, digits=6):
    # STEP reals need a decimal point and no bare exponent such as 1e-05.
    text = '%.*f' % (digits, value)
    text = text.rstrip('0')
    return '0.' if text in ('-0.', '0.') else text


def _string(value):
    return "'%s'" % str(value).replace("'", "''").encode('ascii', 'replace').decode('ascii')


class _Entities:
    def __init__(self, stream):
        self.stream = stream
        self.count = 0
        self.buffer = []

    def add(self, text):
        self.count += 1
        self.buffer.append('#%d=%s;\n' % (self.count, text))
        if len(self.buffer) >= 4096:
            self.flush()
        return self.count

    def flush(self):
        self.stream.write(''.join(self.buffer))
        self.buffer.clear()


def _refs(ids):
    return '(' + ','.join('#%d' % item for item in ids) + ')'


# Grid of the exported corners in millimeters. Edges near the STEP tolerance make CAD kernels merge vertices and
# break faces; 0.1 um is far below a PCB feature and keeps the points of an arc on its circle.
GRID_MM = 1e-4
# A run of at least 5 points on one circle, with equal inner chords of at most this angle each, is an arc.
ARC_MAX_STEP = math.radians(30)
# The largest distance in millimeters of an arc point from the fitted circle.
ARC_FIT_MM = 1e-3
# The largest sweep of one arc edge; a full circle needs at least 3 edges.
ARC_MAX_SWEEP = math.radians(170)
# The largest distance in millimeters that the simplification of straight chains moves an outline. Font outlines
# and curved copper arrive as dense chains of short segments; each segment is a face, and CAD imports slow down
# with the face count. The model is for mechanical fit, and 20 um is far below a PCB fabrication tolerance.
SIMPLIFY_MM = 0.02


def _traced(corners, arcs):
    """The ring with a point at least every 10 degrees of each arc: the chord of a long arc can cross a thin
    part of the polygon."""
    points = []
    n = len(corners)
    for k in range(n):
        points.append(corners[k])
        arc = arcs[k]
        if arc is None:
            continue
        cx, cy, r, ccw = arc
        a, b = corners[k] - (cx, cy), corners[(k + 1) % n] - (cx, cy)
        start, end = math.atan2(a[1], a[0]), math.atan2(b[1], b[0])
        sweep = (end - start) % (2 * math.pi)
        if not ccw:
            sweep -= 2 * math.pi
        steps = int(abs(sweep) / math.radians(10))
        for step in range(1, steps + 1):
            angle = start + sweep * step / (steps + 1)
            points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def _simplify_chains(corners, arcs, fixed, tolerance):
    """Simplify the chains of straight edges with Douglas-Peucker; arc ends and fixed corners stay."""
    n = len(corners)
    if n < 4:
        return corners, arcs
    anchor = fixed.copy()
    for k, arc in enumerate(arcs):
        if arc is not None:
            anchor[k] = anchor[(k + 1) % n] = True
    if not anchor.any():
        # A ring without arcs or fixed corners: its first corner stays.
        anchor[0] = True
    start = int(np.flatnonzero(anchor)[0])
    order = np.roll(np.arange(n), -start)
    corners, arcs, anchor = corners[order], [arcs[i] for i in order], anchor[order]
    closed = np.vstack((corners, corners[:1]))
    new_corners, new_arcs, k = [], [], 0
    while k < n:
        if arcs[k] is not None:
            new_corners.append(corners[k])
            new_arcs.append(arcs[k])
            k += 1
            continue
        end = k + 1
        while end < n and not anchor[end]:
            end += 1
        chain = closed[k:end + 1]
        if len(chain) > 2:
            chain = np.asarray(shapely.simplify(shapely.LineString(chain), tolerance).coords)
        new_corners.extend(chain[:-1])
        new_arcs.extend([None] * (len(chain) - 1))
        k = end
    return np.asarray(new_corners), new_arcs


# The most holes in one solid. CAD kernels check the loops of a face against each other, so a soldermask or a
# copper pour with hundreds of holes imports very slowly; adjacent tiles import fast and look the same.
MAX_HOLES = 32


def _tiles(polygon):
    """Split a polygon with many holes at the median of its hole centers, until each tile has few holes."""
    if len(polygon.interiors) <= MAX_HOLES:
        yield polygon
        return
    centers = np.array([ring.centroid.coords[0] for ring in polygon.interiors])
    xmin, ymin, xmax, ymax = polygon.bounds
    axis = 0 if xmax - xmin >= ymax - ymin else 1
    cut = float(np.median(centers[:, axis]))
    if not (polygon.bounds[axis] < cut < polygon.bounds[axis + 2]):
        yield polygon
        return
    halves = (shapely.box(xmin, ymin, cut, ymax), shapely.box(cut, ymin, xmax, ymax)) if axis == 0 else \
        (shapely.box(xmin, ymin, xmax, cut), shapely.box(xmin, cut, xmax, ymax))
    for half in halves:
        piece = shapely.intersection(polygon, half)
        parts = [part for part in shapely.get_parts(piece) if isinstance(part, Polygon) and not part.is_empty]
        if len(parts) == 1 and len(parts[0].interiors) == len(polygon.interiors):
            # The cut separated nothing: stop instead of looping.
            yield parts[0]
            continue
        for part in parts:
            yield from _tiles(part)


def _snapped(polygons, scale, split=True):
    """Valid polygons with their corners on the export grid; snapping can split or remove a sliver.

    :param split: cut polygons with many holes into tiles
    """
    for polygon in polygons:
        snapped = shapely.set_precision(polygon, GRID_MM / scale)
        for part in getattr(snapped, 'geoms', (snapped,)):
            if isinstance(part, Polygon) and not part.is_empty:
                if split:
                    yield from _tiles(part)
                else:
                    yield part


def _ring_edges(xy, keep):
    """Find the arcs of a ring in millimeters.

    Gerber circles and arcs arrive as polygons: a round pad has about 64 points. One CIRCLE edge and one cylinder
    wall for each arc make the model smaller, faster to import and true to the design.

    :param xy: ring corners without the closing point
    :param keep: a boolean mask of the points that must stay corners
    :return: (corners, arcs): arcs[k] is None for a straight edge from corner k to corner k + 1, else
        (center_x, center_y, radius, counterclockwise)
    """
    n = len(xy)
    a, c = np.roll(xy, 1, axis=0) - xy, np.roll(xy, -1, axis=0) - xy
    turn = np.arctan2(-a[:, 0] * c[:, 1] + a[:, 1] * c[:, 0], -(a * c).sum(axis=1))
    # An inner point of an arc turns a little, always to the same side.
    candidate = (np.abs(turn) <= ARC_MAX_STEP) & (np.abs(turn) > 1e-9) & ~keep
    if candidate.sum() < 2:
        return xy, [None] * n

    def circle(points, refine=False):
        """The circle of the points if they are a polygonized arc, else None.

        :param refine: fit a least-squares circle; it costs more, so only an accepted arc gets it
        """
        chords = np.hypot(*np.diff(points, axis=0).T)
        if len(chords) >= 3:
            # A polygonized arc has equal inner chords; its 2 end chords can be shorter, as a cut arc.
            inner = chords[1:-1]
            if inner.max() > 1.3 * inner.min() or max(chords[0], chords[-1]) > 1.3 * inner.max():
                return None
        p, q, s = points[0], points[len(points) // 2], points[-1]
        u, v = q - p, s - p
        d = 2 * (u[0] * v[1] - u[1] * v[0])
        if abs(d) < 1e-12:
            return None
        su, sv = u @ u, v @ v
        center = p + np.array([(v[1] * su - u[1] * sv) / d, (u[0] * sv - v[0] * su) / d])
        distance = np.hypot(*(points - center).T)
        r = distance.mean()
        if r > 1e4 or np.abs(distance - r).max() > ARC_FIT_MM:
            return None
        if refine:
            # A least-squares circle is closer to the design than the circle through 3 snapped points.
            local = points - p
            solution = np.linalg.lstsq(np.column_stack((2 * local, np.ones(len(local)))),
                                       (local * local).sum(axis=1), rcond=None)[0]
            fitted = p + solution[:2]
            fitted_distance = np.hypot(*(points - fitted).T)
            if np.abs(fitted_distance - fitted_distance.mean()).max() <= np.abs(distance - r).max():
                center, distance = fitted, fitted_distance
                r = distance.mean()
        w = points - center
        steps = np.arctan2(w[:-1, 0] * w[1:, 1] - w[:-1, 1] * w[1:, 0], (w[:-1] * w[1:]).sum(axis=1))
        # The points must go around the center in one direction, in small steps.
        if np.any(np.abs(steps) > ARC_MAX_STEP + 1e-6) or not (np.all(steps > 0) or np.all(steps < 0)):
            return None
        angles = math.atan2(w[0, 1], w[0, 0]) + np.r_[0.0, np.cumsum(steps)]
        return center, r, angles

    runs = []
    whole = circle(np.vstack((xy, xy[:1])), refine=True) if candidate.all() else None
    if whole is not None:
        runs.append((np.r_[np.arange(n), 0], whole))
    else:
        # Start at a corner, so that no arc wraps over the start.
        start = int(np.flatnonzero(~candidate)[0]) if not candidate.all() else 0
        order = np.roll(np.arange(n), -start)
        xy, candidate = xy[order], candidate[order]
        closed = np.vstack((xy, xy[:1]))
        first = 0
        while first < n - 2:
            last, fit = first + 1, None
            # Grow the arc while its inner points are candidates and every point stays on one circle.
            while last + 1 <= n and candidate[last % n]:
                trial = circle(closed[first:last + 2])
                if trial is None:
                    break
                last, fit = last + 1, trial
            if fit is not None and last - first >= 4:
                runs.append((np.arange(first, last + 1), circle(closed[first:last + 1], refine=True) or fit))
                first = last
            else:
                # A shorter run that starts inside the failed one fails at the same point: skip ahead.
                first = max(first + 1, last - 1)
    arcs_at = {}
    for run, (center, r, angles) in runs:
        piece = 0
        for j in range(1, len(run)):
            if abs(angles[j] - angles[piece]) > ARC_MAX_SWEEP:
                arcs_at[int(run[piece])] = (int(run[j - 1]), center, r, angles[-1] > angles[0])
                piece = j - 1
        arcs_at[int(run[piece])] = (int(run[-1]), center, r, angles[-1] > angles[0])
    if not arcs_at:
        return xy, [None] * n
    corners, arcs, index = [], [], 0
    while True:
        corners.append(index)
        if index in arcs_at:
            end, center, r, ccw = arcs_at[index]
            arcs.append((center[0], center[1], r, ccw))
            index = end % n
        else:
            arcs.append(None)
            index = (index + 1) % n
        if index == 0:
            break
    result = xy[corners].copy()
    # Put the ends of each arc on its circle; an end between 2 arcs follows the first arc.
    moved = np.zeros(len(result), dtype=bool)
    for k, arc in enumerate(arcs):
        if arc is None:
            continue
        for m in (k, (k + 1) % len(result)):
            if not moved[m]:
                offset = result[m] - arc[:2]
                result[m] = np.asarray(arc[:2]) + offset * (arc[2] / np.hypot(*offset))
                moved[m] = True
    return result, arcs


class _SolidBuilder:
    def __init__(self, entities):
        self.e = entities
        self.z_dir = entities.add("DIRECTION('',(0.,0.,1.))")
        self.nz_dir = entities.add("DIRECTION('',(0.,0.,-1.))")
        self.x_dir = entities.add("DIRECTION('',(1.,0.,0.))")
        self.planes = {}

    def cap_plane(self, z, up):
        key = (_real(z), up)
        if key not in self.planes:
            point = self.e.add("CARTESIAN_POINT('',(0.,0.,%s))" % key[0])
            axis = self.e.add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (
                point, self.z_dir if up else self.nz_dir, self.x_dir))
            self.planes[key] = self.e.add("PLANE('',#%d)" % axis)
        return self.planes[key]

    @staticmethod
    def ring_points(ring, scale, shared=frozenset()):
        """Ring corners without the closing point, equal points or collinear points, as (xy, formatted xy).

        :param shared: formatted points that another ring touches; they stay even when collinear
        """
        xy = np.asarray(ring.coords, dtype=np.float64)[:-1, :2] * scale
        for _pass in range(2):
            if len(xy) < 3:
                return xy, []
            # Points that become equal after formatting give zero-length edges.
            text = [(_real(x), _real(y)) for x, y in xy]
            keep = [i for i in range(len(text)) if text[i] != text[i - 1]]
            xy = xy[keep]
            if len(xy) < 3:
                return xy, []
            # A point on a straight line only adds a wall face; CAD kernels merge such faces slowly.
            before, after = xy - np.roll(xy, 1, axis=0), np.roll(xy, -1, axis=0) - xy
            cross = before[:, 0] * after[:, 1] - before[:, 1] * after[:, 0]
            dot = (before * after).sum(axis=1)
            straight = (np.abs(cross) <= 1e-9 * np.hypot(*before.T) * np.hypot(*after.T)) & (dot > 0)
            if shared:
                straight &= np.array([(_real(x), _real(y)) not in shared for x, y in xy])
            if not straight.any():
                break
            xy = xy[~straight]
        return xy, [(_real(x), _real(y)) for x, y in xy]

    def rings(self, polygon, scale, tolerance):
        """The rings of an oriented polygon as [(corners, arcs, formatted corners), ...], exterior first.

        :return: None if the exterior collapses
        """
        rings = (polygon.exterior, *polygon.interiors)
        shared = frozenset()
        if len(rings) > 1:
            seen = Counter(point for ring in rings for point in
                           {(_real(x), _real(y)) for x, y in np.asarray(ring.coords)[:-1, :2] * scale})
            shared = frozenset(point for point, count in seen.items() if count > 1)
        result = []
        for index, ring in enumerate(rings):
            xy, text = self.ring_points(ring, scale, shared)
            if len(text) < 3:
                if index == 0:
                    return None
                continue
            fixed = np.array([point in shared for point in text], dtype=bool)
            corners_xy, arcs = _ring_edges(xy, fixed)
            if tolerance:
                fixed = np.array([(_real(x), _real(y)) in shared for x, y in corners_xy], dtype=bool)
                corners_xy, arcs = _simplify_chains(corners_xy, arcs, fixed, tolerance)
            text = [(_real(x), _real(y)) for x, y in corners_xy]
            if len(text) < 3 or any(text[k] == text[k - 1] for k in range(len(text))):
                # Moving the arc ends onto their circles merged 2 corners: keep the straight edges.
                corners_xy, arcs = xy, [None] * len(xy)
                text = [(_real(x), _real(y)) for x, y in xy]
            result.append((corners_xy, arcs, text))
        if tolerance:
            # Chains are simplified one at a time: a thin web between 2 rings can close.
            outline = Polygon(_traced(*result[0][:2]), [_traced(*ring[:2]) for ring in result[1:]])
            if not outline.is_valid:
                return self.rings(polygon, scale, 0)
        return result

    def solid(self, name, polygon, z0, z1, scale, tolerance=SIMPLIFY_MM):
        """One MANIFOLD_SOLID_BREP: each edge is one EDGE_CURVE that its 2 faces share, so no healing is needed."""
        e = self.e
        polygon = orient(polygon, 1.0)
        bottom_z, top_z = _real(z0 * scale), _real(z1 * scale)
        rise = e.add("VECTOR('',#%d,%s)" % (self.z_dir, _real((z1 - z0) * scale)))
        faces, top_loops, bottom_loops = [], [], []
        # Rings of a valid polygon can touch at a point: they must share that vertex.
        corners = {}

        def corner(x, y, z):
            if (x, y, z) not in corners:
                point = e.add("CARTESIAN_POINT('',(%s,%s,%s))" % (x, y, z))
                corners[(x, y, z)] = point, e.add("VERTEX_POINT('',#%d)" % point)
            return corners[(x, y, z)]

        rings = self.rings(polygon, scale, tolerance)
        if rings is None:
            return None
        for xy, arcs, text in rings:
            count = len(text)
            bottom_points, bottom = zip(*(corner(x, y, bottom_z) for x, y in text))
            top_points, top = zip(*(corner(x, y, top_z) for x, y in text))
            # Vertical edge i runs up from corner i; bottom and top edge i run from corner i to corner i + 1.
            vertical = [e.add("EDGE_CURVE('',#%d,#%d,#%d,.T.)" % (
                bottom[i], top[i], e.add("LINE('',#%d,#%d)" % (bottom_points[i], rise)))) for i in range(count)]
            lower, upper, walls = [], [], []
            for i in range(count):
                j = (i + 1) % count
                arc = arcs[i]
                if arc is None:
                    dx, dy = xy[j] - xy[i]
                    length = math.hypot(dx, dy)
                    direction = e.add("DIRECTION('',(%s,%s,0.))" % (_real(dx / length, 12), _real(dy / length, 12)))
                    vector = e.add("VECTOR('',#%d,%s)" % (direction, _real(length)))
                    lower.append(e.add("EDGE_CURVE('',#%d,#%d,#%d,.T.)" % (
                        bottom[i], bottom[j], e.add("LINE('',#%d,#%d)" % (bottom_points[i], vector)))))
                    upper.append(e.add("EDGE_CURVE('',#%d,#%d,#%d,.T.)" % (
                        top[i], top[j], e.add("LINE('',#%d,#%d)" % (top_points[i], vector)))))
                    # The rings are oriented, so (dy, -dx) points out of the material for the shell and the holes.
                    normal = e.add("DIRECTION('',(%s,%s,0.))" % (_real(dy / length, 12), _real(-dx / length, 12)))
                    axis = e.add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (bottom_points[i], normal, self.z_dir))
                    walls.append((e.add("PLANE('',#%d)" % axis), True))
                else:
                    cx, cy, r, ccw = arc
                    sense = '.T.' if ccw else '.F.'
                    axes = {}
                    for z in (bottom_z, top_z):
                        center = e.add("CARTESIAN_POINT('',(%s,%s,%s))" % (_real(cx), _real(cy), z))
                        axes[z] = e.add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (center, self.z_dir, self.x_dir))
                    radius = _real(r)
                    lower.append(e.add("EDGE_CURVE('',#%d,#%d,#%d,%s)" % (
                        bottom[i], bottom[j], e.add("CIRCLE('',#%d,%s)" % (axes[bottom_z], radius)), sense)))
                    upper.append(e.add("EDGE_CURVE('',#%d,#%d,#%d,%s)" % (
                        top[i], top[j], e.add("CIRCLE('',#%d,%s)" % (axes[top_z], radius)), sense)))
                    # A counterclockwise arc has the material inside: the cylinder normal points out of it.
                    walls.append((e.add("CYLINDRICAL_SURFACE('',#%d,%s)" % (axes[bottom_z], radius)), ccw))
            for i in range(count):
                j = (i + 1) % count
                surface, same_sense = walls[i]
                # Counterclockwise seen from outside: bottom i, up at j, top i backwards, down at i.
                edges = [self.oriented(lower[i], True), self.oriented(vertical[j], True),
                         self.oriented(upper[i], False), self.oriented(vertical[i], False)]
                loop = e.add("EDGE_LOOP('',%s)" % _refs(edges))
                bound = e.add("FACE_OUTER_BOUND('',#%d,.T.)" % loop)
                faces.append(e.add("ADVANCED_FACE('',(#%d),#%d,%s)" % (bound, surface,
                                                                      '.T.' if same_sense else '.F.')))
            top_loops.append([self.oriented(edge, True) for edge in upper])
            bottom_loops.append([self.oriented(edge, False) for edge in reversed(lower)])
        for loops, plane in ((top_loops, self.cap_plane(z1 * scale, True)),
                             (bottom_loops, self.cap_plane(z0 * scale, False))):
            bounds = []
            for index, edges in enumerate(loops):
                loop = e.add("EDGE_LOOP('',%s)" % _refs(edges))
                bounds.append(e.add("%s('',#%d,.T.)" % ('FACE_OUTER_BOUND' if index == 0 else 'FACE_BOUND', loop)))
            faces.append(e.add("ADVANCED_FACE('',%s,#%d,.T.)" % (_refs(bounds), plane)))
        shell = e.add("CLOSED_SHELL('',%s)" % _refs(faces))
        return e.add("MANIFOLD_SOLID_BREP(%s,#%d)" % (_string(name), shell))

    def oriented(self, edge, forward):
        return self.e.add("ORIENTED_EDGE('',*,*,#%d,%s)" % (edge, '.T.' if forward else '.F.'))


def write(filename, solids, units='MM', name='PCB', cancelled=lambda: False, progress=None):
    """Write the solids to a STEP file in millimeters.

    :param solids: a list of dictionaries with name, polygons (Shapely), z, thickness and color (RGBA, 0..1),
        in the project units, an optional tolerance in millimeters for the simplification of straight chains and
        an optional split flag (default True) that cuts polygons with many holes into tiles
    :param progress: an optional callable that gets the done part of the work, 0..1
    :return: False if the cancelled callable stopped the export, else True
    """
    total = max(1, sum(len(solid['polygons']) for solid in solids))
    done = 0
    scale = 25.4 if str(units).upper() == 'IN' else 1.0
    stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
    with open(filename, 'w', encoding='ascii', newline='\n') as stream:
        stream.write("ISO-10303-21;\nHEADER;\n"
                     "FILE_DESCRIPTION(('FlatCAM EVO PCB 3D model'),'2;1');\n"
                     "FILE_NAME(%s,'%s',(''),(''),'FlatCAM EVO','FlatCAM EVO','');\n"
                     "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\n"
                     "ENDSEC;\nDATA;\n" % (_string(os.path.basename(filename)), stamp))
        e = _Entities(stream)
        app_context = e.add("APPLICATION_CONTEXT('automotive design')")
        e.add("APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#%d)" % app_context)
        product_context = e.add("PRODUCT_CONTEXT('',#%d,'mechanical')" % app_context)
        definition_context = e.add("PRODUCT_DEFINITION_CONTEXT('part definition',#%d,'design')" % app_context)
        length = e.add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
        angle = e.add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
        solid_angle = e.add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
        uncertainty = e.add("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-06),#%d,'distance_accuracy_value',"
                            "'confusion accuracy')" % length)
        context = e.add("(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#%d))"
                        "GLOBAL_UNIT_ASSIGNED_CONTEXT((#%d,#%d,#%d))REPRESENTATION_CONTEXT('',''))" % (
                            uncertainty, length, angle, solid_angle))
        origin = e.add("CARTESIAN_POINT('',(0.,0.,0.))")
        builder = _SolidBuilder(e)

        def placement():
            return e.add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (origin, builder.z_dir, builder.x_dir))

        def product(label):
            item = e.add("PRODUCT(%s,%s,'',(#%d))" % (_string(label), _string(label), product_context))
            e.add("PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#%d))" % item)
            formation = e.add("PRODUCT_DEFINITION_FORMATION('','',#%d)" % item)
            definition = e.add("PRODUCT_DEFINITION('design','',#%d,#%d)" % (formation, definition_context))
            shape = e.add("PRODUCT_DEFINITION_SHAPE('','',#%d)" % definition)
            return definition, shape

        root_axis = placement()
        root_definition, root_shape = product(name)
        children, styled = [], []
        for solid in solids:
            if cancelled():
                return False
            breps = []
            for source_number, source in enumerate(solid['polygons']):
                if source_number % 16 == 0:
                    if cancelled():
                        return False
                    if progress is not None:
                        progress((done + source_number) / total)
                for polygon in _snapped((source,), scale, solid.get('split', True)):
                    brep = builder.solid('%s_%d' % (solid['name'], len(breps) + 1), polygon, solid['z'],
                                         solid['z'] + solid['thickness'], scale,
                                         solid.get('tolerance', SIMPLIFY_MM))
                    if brep is not None:
                        breps.append(brep)
            done += len(solid['polygons'])
            if not breps:
                continue
            red, green, blue, alpha = (min(1.0, max(0.0, float(value))) for value in solid['color'])
            colour = e.add("COLOUR_RGB('',%s,%s,%s)" % (_real(red), _real(green), _real(blue)))
            fill = e.add("FILL_AREA_STYLE_COLOUR('',#%d)" % colour)
            area = e.add("FILL_AREA_STYLE('',(#%d))" % fill)
            styles = [e.add("SURFACE_STYLE_FILL_AREA(#%d)" % area)]
            if alpha < 1:
                transparency = e.add("SURFACE_STYLE_TRANSPARENT(%s)" % _real(1 - alpha))
                styles.append(e.add("SURFACE_STYLE_RENDERING_WITH_PROPERTIES(.NORMAL_SHADING.,#%d,(#%d))" % (
                    colour, transparency)))
            side = e.add("SURFACE_SIDE_STYLE('',%s)" % _refs(styles))
            usage = e.add("SURFACE_STYLE_USAGE(.BOTH.,#%d)" % side)
            assignment = e.add("PRESENTATION_STYLE_ASSIGNMENT((#%d))" % usage)
            styled.extend(e.add("STYLED_ITEM('color',(#%d),#%d)" % (assignment, brep)) for brep in breps)
            axis = placement()
            representation = e.add("ADVANCED_BREP_SHAPE_REPRESENTATION(%s,%s,#%d)" % (
                _string(solid['name']), _refs(breps + [axis]), context))
            definition, shape = product(solid['name'])
            e.add("SHAPE_DEFINITION_REPRESENTATION(#%d,#%d)" % (shape, representation))
            children.append((solid['name'], definition, representation, axis))
        if not children:
            raise ValueError('The PCB has no solid to export.')
        child_axes = [placement() for _child in children]
        root_representation = e.add("SHAPE_REPRESENTATION(%s,%s,#%d)" % (
            _string(name), _refs([root_axis] + child_axes), context))
        e.add("SHAPE_DEFINITION_REPRESENTATION(#%d,#%d)" % (root_shape, root_representation))
        for number, ((label, definition, representation, axis), parent_axis) in enumerate(zip(children, child_axes)):
            usage = e.add("NEXT_ASSEMBLY_USAGE_OCCURRENCE('%d',%s,'',#%d,#%d,$)" % (
                number + 1, _string(label), root_definition, definition))
            shape = e.add("PRODUCT_DEFINITION_SHAPE('','',#%d)" % usage)
            transform = e.add("ITEM_DEFINED_TRANSFORMATION('','',#%d,#%d)" % (axis, parent_axis))
            relation = e.add("(REPRESENTATION_RELATIONSHIP('','',#%d,#%d)REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION"
                             "(#%d)SHAPE_REPRESENTATION_RELATIONSHIP())" % (representation, root_representation,
                                                                           transform))
            e.add("CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#%d,#%d)" % (relation, shape))
        e.add("MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',%s,#%d)" % (_refs(styled), context))
        e.flush()
        stream.write("ENDSEC;\nEND-ISO-10303-21;\n")
    return True
