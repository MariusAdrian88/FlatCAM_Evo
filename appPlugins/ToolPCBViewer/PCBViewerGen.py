"""Detached PCB geometry and unit-height meshes. No widgets or OpenGL in the worker."""

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import shapely
from shapely import Polygon, box
from shapely.geometry.base import BaseGeometry

from appCommon import clipper_ops


def parts(geometry):
    if isinstance(geometry, BaseGeometry):
        if geometry.is_empty:
            return
        if hasattr(geometry, 'geoms'):
            for geo in geometry.geoms:
                yield from parts(geo)
        else:
            yield geometry
    elif geometry is not None:
        for geo in geometry:
            yield from parts(geo)


@dataclass(eq=False)
class Source:
    solid: object
    follow: object = ()
    units: str = 'MM'

    def __post_init__(self):
        # Shapely geometries are immutable; detach containers, not every coordinate of the board.
        self.solid = tuple(parts(self.solid))
        self.follow = tuple(parts(self.follow))


@dataclass
class Layer:
    name: str
    geometry: object
    vertices: np.ndarray
    faces: np.ndarray
    origin: tuple


def layer_styles(layers, values, units):
    """Physical stack and RGBA materials shared by the preview and detached export."""
    names = {layer.name for layer in layers}
    factor = 1 / 25.4 if units.upper() == 'IN' else 1
    board = values['board_thickness'] * factor
    copper = values['copper_thickness'] / 1000 * factor
    mask = values['mask_thickness'] / 1000 * factor
    ink = 0.001 * factor
    heights = {'board': (0, board)}
    if 'plating' in names:
        bottom = copper if 'copper_bottom' in names else 0
        top = copper if 'copper_top' in names else 0
        heights['plating'] = (-bottom, board + bottom + top)
    for side in ('top', 'bottom'):
        top = side == 'top'
        z = board if top else 0
        for name, thickness in (('copper', copper), ('soldermask', mask), ('overlay', ink)):
            key = name + '_' + side
            if key not in names:
                continue
            heights[key] = (z if top else z - thickness, thickness)
            z += thickness if top else -thickness
    styles = {}
    for name in names:
        color_key = 'copper' if name == 'plating' else name.split('_')[0]
        text = values[color_key + '_color']
        if len(text) not in (7, 9) or not text.startswith('#'):
            raise ValueError('Use a color in #RRGGBB or #RRGGBBAA format.')
        try:
            color = np.array([int(text[index:index + 2], 16) / 255 for index in (1, 3, 5)] +
                             [int(text[7:9], 16) / 255 if len(text) == 9 else 1])
        except ValueError:
            raise ValueError('Use a color in #RRGGBB or #RRGGBBAA format.') from None
        if name == 'board':
            color[3] = values['board_alpha'] / 255
        z, thickness = heights[name]
        styles[name] = dict(z=z, thickness=thickness, color=color)
    return styles


def polygons(geometry):
    return [geo for geo in parts(geometry) if isinstance(geo, Polygon)]


def boolean(subject, clip=(), operation='union'):
    subject, clip = polygons(subject), polygons(clip)
    result = clipper_ops.boolean_geometry(subject, clip, operation)
    if result is not None:
        return result
    left = shapely.union_all(subject)
    if operation == 'union':
        return left if not clip else left.union(shapely.union_all(clip))
    right = shapely.union_all(clip)
    return left.difference(right) if operation == 'difference' else left.intersection(right)


def join_ends(lines, tolerance):
    """Line ends closer than the tolerance become one point: an imported arc can end one database unit from its line."""
    lines = shapely.get_parts(np.array(lines, dtype=object))
    coords, index = shapely.get_coordinates(lines, return_index=True)
    if not len(coords):
        return lines
    starts = np.r_[0, np.flatnonzero(np.diff(index)) + 1]
    rows = np.r_[starts, np.r_[starts[1:], len(coords)] - 1]
    points = shapely.points(coords[rows])
    left, right = shapely.STRtree(points).query(points, predicate='dwithin', distance=tolerance)
    parent = np.arange(len(rows))

    def root(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for first, second in zip(left[left != right], right[left != right]):
        first, second = root(first), root(second)
        if first != second:
            parent[max(first, second)] = min(first, second)
    coords[rows] = coords[rows[[root(item) for item in range(len(rows))]]]
    return shapely.set_coordinates(lines.copy(), coords)


def extrude_mesh(geometry):
    """Caps and ring walls at Z=0..1; scaling the visual changes thickness without triangulating again."""
    polys = polygons(geometry)
    if not polys:
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.uint32), (0, 0)
    origin = tuple((np.array(geometry.bounds[:2]) + geometry.bounds[2:]) / 2)
    polys = shapely.orient_polygons(polys) if hasattr(shapely, 'orient_polygons') else [
        shapely.geometry.polygon.orient(poly) for poly in polys]
    native = getattr(shapely, 'constrained_delaunay_triangles', None)
    if native is not None:
        try:
            if len(polys) >= 4 and shapely.get_num_coordinates(polys).sum() >= 20000:
                # GEOS releases the GIL: independent panel islands can triangulate together.
                with ThreadPoolExecutor(max_workers=4) as executor:
                    triangles = np.concatenate(list(executor.map(native, np.array_split(polys, 4))))
            else:
                triangles = native(polys)
            xy = shapely.get_coordinates(triangles).reshape(-1, 4, 2)[:, :3].copy()
        except (shapely.errors.GEOSException, shapely.errors.UnsupportedGEOSVersionError):
            xy = None
    else:
        xy = None
    if xy is None:
        from appGUI.VisPyTesselators import GLUTess
        tess = GLUTess()
        triangles = []
        for poly in polys:
            indices, points = tess.triangulate(poly)
            triangles.append(np.asarray(points, dtype=float)[np.asarray(indices).reshape(-1, 3)])
        xy = np.concatenate(triangles)
    a, b = xy[:, 1] - xy[:, 0], xy[:, 2] - xy[:, 0]
    clockwise = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0] < 0
    xy[clockwise, 1], xy[clockwise, 2] = xy[clockwise, 2].copy(), xy[clockwise, 1].copy()
    xy = xy.reshape(-1, 2) - origin
    cap_count = len(xy)
    top = np.column_stack((xy, np.ones(cap_count)))
    bottom = np.column_stack((xy, np.zeros(cap_count)))
    cap_faces = np.arange(cap_count, dtype=np.uint32).reshape(-1, 3)

    rings = shapely.get_rings(polys)
    coords, ring_index = shapely.get_coordinates(rings, return_index=True)
    edges = ring_index[:-1] == ring_index[1:]
    p, q = coords[:-1][edges] - origin, coords[1:][edges] - origin
    walls = np.zeros((len(p), 4, 3), dtype=np.float32)
    walls[:, 0, :2] = walls[:, 3, :2] = p
    walls[:, 1, :2] = walls[:, 2, :2] = q
    walls[:, 2:, 2] = 1
    wall_faces = (np.arange(len(p), dtype=np.uint32)[:, None, None] * 4 +
                  np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)).reshape(-1, 3) + 2 * cap_count
    vertices = np.concatenate((top, bottom, walls.reshape(-1, 3))).astype(np.float32)
    faces = np.concatenate((cap_faces, cap_faces[:, ::-1] + cap_count, wall_faces))
    # Generated faces that collapse at float32 precision have zero area.
    triangles = vertices[faces].astype(np.float64)
    valid = np.any(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]) != 0, axis=1)
    return vertices, faces[valid], origin


class PCBGeometry:
    def __init__(self):
        self._sources = {}
        self._meshes = {}

    def source_geometry(self, source, units, outline=False):
        key = (source, units, outline)
        if key in self._sources:
            return self._sources[key]
        factor = (25.4 if source.units.upper() == 'IN' else 1) / (25.4 if units.upper() == 'IN' else 1)
        raw = source.follow if outline and source.follow else source.solid
        if factor != 1:
            raw = tuple(shapely.transform(raw, lambda xy: xy * factor))
        valid = shapely.is_valid(raw)
        raw = [geo if ok else shapely.make_valid(geo) for geo, ok in zip(raw, valid)]
        if outline:
            lines = [geo.boundary if isinstance(geo, Polygon) else geo for geo in raw
                     if isinstance(geo, Polygon) or geo.geom_type in ('LineString', 'LinearRing')]
            scale = 25.4 if units.upper() == 'IN' else 1
            lines = join_ends(lines, 1e-3 / scale)
            geometry = shapely.build_area(shapely.union_all(shapely.set_precision(lines, 1e-6 / scale)))
            if geometry.is_empty:
                raise ValueError('The mechanical outline must contain closed contours or filled regions.')
        else:
            geometry = boolean(raw)
        self._sources[key] = geometry
        return geometry

    def board_shape(self, sources, units):
        """
        The outline areas that hold the other layers. Outline layers often carry annotations: a frame or text far from
        the board, and text on the board that would cut holes in it. Without other layers the outline stays as it is.
        """
        outline = sources['outline']
        layers = tuple(src for name, src in sorted(sources.items()) if name != 'outline')
        # Overlay annotations are often outside the board: the other layers decide when they exist.
        weighed = tuple(src for name, src in sorted(sources.items()) if name != 'outline' and
                        not name.startswith('overlay')) or layers
        coppers = tuple(sources[name] for name in ('copper_top', 'copper_bottom') if name in sources)
        key = (outline, units, 'board', layers)
        if key in self._sources:
            return self._sources[key]
        areas = polygons(self.source_geometry(outline, units, outline=True))
        x, y, weights = [], [], []
        for src in weighed:
            parts = np.array(polygons(src.solid), dtype=object)
            if not len(parts):
                continue
            factor = (25.4 if src.units.upper() == 'IN' else 1) / (25.4 if units.upper() == 'IN' else 1)
            xy = shapely.get_coordinates(shapely.point_on_surface(parts)) * factor
            x.append(xy[:, 0])
            y.append(xy[:, 1])
            weights.append(shapely.area(parts) * factor ** 2)
        if not weights:
            self._sources[key] = shapely.union_all(areas)
            return self._sources[key]
        x, y, weights = np.concatenate(x), np.concatenate(y), np.concatenate(weights)
        held = np.zeros(len(areas))
        for index, area in enumerate(areas):
            shapely.prepare(area)
            held[index] = weights[shapely.contains_xy(area, x, y)].sum()
        # A board holds a real share of the other layers; a panel has more than one board.
        kept = [area for area, value in zip(areas, held) if value >= 0.05 * held.max()]
        if held[held >= 0.05 * held.max()].sum() < 0.5 * weights.sum():
            # No board contour on this layer, only annotations: use the bounding box as without an outline.
            self._sources[key] = None
            return None
        shells = [Polygon(area.exterior) for area in kept]
        tree = shapely.STRtree(shells)
        # Areas inside a kept board are islands in its cutouts.
        nested = [area for area, value in zip(areas, held) if value < 0.05 * held.max() and
                  len(tree.query(area.representative_point(), predicate='within'))]
        if coppers:
            copper = boolean([self.source_geometry(src, units) for src in coppers])
            filled = []
            for area in kept:
                # Copper cannot be in a cutout: a hole that holds copper is text drawn on the board.
                holes = [ring for ring in area.interiors
                         if Polygon(ring).intersection(copper).area < 0.1 * Polygon(ring).area]
                filled.append(Polygon(area.exterior, holes) if len(holes) < len(area.interiors) else area)
            kept = filled
        self._sources[key] = boolean(kept + nested)
        return self._sources[key]

    def build(self, sources, units='MM', cancelled=lambda: False):
        """Yield the substrate first, then completed layers in the project's original XY coordinates."""
        sources = {name: src for name, src in sources.items() if src is not None}
        active = set(sources.values())
        self._sources = {key: value for key, value in self._sources.items() if key[0] in active and key[1] == units}
        # Keep the last board's mesh variants, not every board ever opened in this project.
        if len(self._meshes) > 24:
            self._meshes.clear()
        outline = self.board_shape(sources, units) if 'outline' in sources else None
        bounds_list = []
        for name, src in sources.items():
            if cancelled():
                return
            if name == 'outline':
                if outline is not None:
                    bounds_list.append((name, outline.bounds))
                continue
            solid = polygons(src.solid)
            if solid:
                factor = (25.4 if src.units.upper() == 'IN' else 1) / (25.4 if units.upper() == 'IN' else 1)
                bounds_list.append((name, shapely.total_bounds(solid) * factor))
        # Overlay annotations are often outside the board: the other layers set the box when they exist.
        if any(not name.startswith('overlay') for name, value in bounds_list):
            bounds_list = [item for item in bounds_list if not item[0].startswith('overlay')]
        bounds_list = [value for name, value in bounds_list]
        bounds = np.full(4, np.nan)
        if bounds_list:
            array = np.array(bounds_list)
            bounds = np.r_[np.nanmin(array[:, :2], axis=0), np.nanmax(array[:, 2:], axis=0)]
        if not np.isfinite(bounds).all() or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ValueError('Select a Gerber object with nonempty geometry.')
        reference = tuple(bounds)
        drills = tuple(dict.fromkeys(src for name, src in sources.items() if name.startswith('drills')))
        plated = tuple(dict.fromkeys(src for name, src in sources.items() if name.startswith('drills_pth_')))
        # The board shape depends on the other layers when there is an outline.
        board_key = (sources.get('outline'), drills, reference, units,
                     tuple(src for name, src in sorted(sources.items()) if 'outline' in sources and name != 'outline'))
        if board_key not in self._meshes:
            board = outline if outline is not None else box(*bounds)
            if drills:
                board = boolean(board, [self.source_geometry(src, units) for src in drills], 'difference')
            self._meshes[board_key] = (board, *extrude_mesh(board))
        board, vertices, faces, origin = self._meshes[board_key]
        yield Layer('board', board, vertices, faces, origin)
        if plated and not cancelled():
            key = (board_key, 'plating', plated)
            if key not in self._meshes:
                holes = boolean([self.source_geometry(src, units) for src in plated])
                # Only exposed drill walls are plated: never cap a hole or coat the board's outside edge.
                walls = shapely.intersection(shapely.orient_polygons(holes).boundary, board.boundary)
                coords, indices = shapely.get_coordinates(shapely.get_parts(walls), return_index=True)
                edges = indices[:-1] == indices[1:]
                p, q = coords[:-1][edges] - origin, coords[1:][edges] - origin
                vertices = np.zeros((len(p), 4, 3), dtype=np.float32)
                vertices[:, 0, :2] = vertices[:, 3, :2] = p
                vertices[:, 1, :2] = vertices[:, 2, :2] = q
                vertices[:, 2:, 2] = 1
                faces = (np.arange(len(p), dtype=np.uint32)[:, None, None] * 4 +
                         np.array([[0, 2, 1], [0, 3, 2]], dtype=np.uint32)).reshape(-1, 3)
                self._meshes[key] = (walls, vertices.reshape(-1, 3), faces, origin)
            walls, vertices, faces, origin = self._meshes[key]
            yield Layer('plating', walls, vertices, faces, origin)
        for name in ('copper_top', 'copper_bottom', 'soldermask_top', 'soldermask_bottom',
                     'overlay_top', 'overlay_bottom'):
            if cancelled():
                return
            if name not in sources:
                continue
            negative = name.startswith('soldermask')
            key = (board_key, sources[name], negative)
            if key not in self._meshes:
                geometry = self.source_geometry(sources[name], units)
                geometry = boolean(board, geometry, 'difference') if negative else boolean(geometry, board, 'intersection')
                self._meshes[key] = (geometry, *extrude_mesh(geometry))
            geometry, vertices, faces, origin = self._meshes[key]
            yield Layer(name, geometry, vertices, faces, origin)
