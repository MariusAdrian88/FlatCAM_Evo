"""Gerber records made from native board features, before layer composition."""

import math

import numpy as np
import shapely
from shapely import prepare
from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

# mm (the board parsers work in mm). A region record overlaps each preserved feature it touches by this much: a flash
# and a region that only touch are not connected in Gerber, and the rounding of the export can open a hairline gap
# between them. 5 um is more than the rounding of the coarsest default export grid (0.0001 in = 2.54 um).
OVERLAP = 0.005


class BoardPrimitives:
    def __init__(self, options=None):
        self.enabled = (options or {}).get('gerber_board_import_mode', 'primitives') == 'primitives'
        self.layers = {}

    def _record(self, layer, tool, solid, follow):
        if self.enabled and not solid.is_empty and solid.is_valid:
            self.layers.setdefault(layer, []).append((tool, {'solid': solid, 'follow': follow}))

    def stroke(self, layer, solid, follow, width):
        if not self.enabled or not math.isfinite(width) or width <= 0:
            return
        if isinstance(follow, LineString) and not follow.is_empty and follow.length == 0:
            follow = Point(follow.coords[0])
        if isinstance(follow, (Point, LineString)) and not follow.is_empty:
            self._record(layer, {'type': 'C', 'size': width}, solid, follow)

    def flash(self, layer, solid, centre, shape, width, height, angle=0.0):
        """Record a source flash; nonstandard source shapes use the block-aperture export plan."""
        if not self.enabled or not all(math.isfinite(v) for v in (width, height, angle)):
            return
        if width <= 0 or height <= 0:
            return
        if shape == 'circle':
            tool = {'type': 'C', 'size': width}
        elif shape in ('rect', 'oval'):
            quarter = round(angle / 90)
            if abs(angle - quarter * 90) > 1e-8:
                tool = {'type': 'AB', 'size': 0.0}
            else:
                if quarter % 2:
                    width, height = height, width
                tool = {'type': 'R' if shape == 'rect' else 'O',
                        'width': width, 'height': height, 'size': math.hypot(width, height)}
        elif shape == 'block':
            tool = {'type': 'AB', 'size': 0.0}
        else:
            return
        self._record(layer, tool, solid, centre if isinstance(centre, Point) else Point(centre))

    def finish(self, layer, result):
        """Keep intact features and use regions for the remaining composed copper (including holes)."""
        if not self.enabled:
            return
        target = self._target(result['solid_geometry'])
        prepare(target)
        # GEOS union can move shared edges by a few ulps; this is not a manufacturing clearance.
        roundoff = 8 * max((math.ulp(v) for v in target.bounds), default=math.ulp(1.0))

        def numeric_sliver(polygon):
            # At ULP distances GEOS erosion can empty even large polygons; bound the lost area first.
            return polygon.area <= roundoff * polygon.length and \
                polygon.buffer(-roundoff).area <= roundoff * roundoff

        parts = self._polygons(target)
        part_tree = STRtree(parts)

        def outside(solid):
            # the part of the feature out of the copper; only the copper polygons near the feature can change it
            near = part_tree.query(solid)
            if not len(near):
                return [solid]
            local = parts[near[0]] if len(near) == 1 else MultiPolygon([parts[i] for i in near])
            return self._polygons(solid.difference(local))

        entries = self.layers.get(layer, [])
        # the features that may stick out of the copper; None: check each feature
        candidates = self._clipped_candidates([record['solid'] for _tool, record in entries], parts)
        tools, apertures, preserved = {}, {}, []
        for index, (tool, record) in enumerate(entries):
            solid = record['solid']
            # A cutout or a negative layer can remove part of a source feature. Do not restore it by flashing.
            if (candidates is None or index in candidates) and not target.covers(solid) and \
                    not all(numeric_sliver(p) for p in outside(solid)):
                continue
            signature = tuple(sorted(tool.items()))
            apid = apertures.get(signature)
            if apid is None:
                apid = len(apertures) + 10
                apertures[signature] = apid
                tools[apid] = dict(tool, geometry=[])
            tools[apid]['geometry'].append(record)
            preserved.append(solid)
        polygons = [p for p in self._remainder(parts, preserved) if not numeric_sliver(p)]
        if polygons:
            tools[0] = {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': p} for p in polygons]}
        # All the records are dark and their union is the composed copper, so any order composes the same image:
        # number them, so the Gerber export trusts the records and does not compare them with solid_geometry.
        seq = 0
        for tool in tools.values():
            for record in tool['geometry']:
                record['seq'] = seq
                seq += 1
        result['tools'] = tools
        result['preserve_primitives'] = True

    @staticmethod
    def _target(solids):
        """
        The composed copper of the layer. The parsers give the polygons of a union already: if they make a valid
        MultiPolygon (no overlap), they are used as they are, because a second union of a large layer is slow.
        """
        if solids and all(isinstance(p, Polygon) for p in solids):
            if len(solids) == 1:
                if solids[0].is_valid:
                    return solids[0]
            else:
                target = MultiPolygon(solids)
                if target.is_valid:
                    return target
        return unary_union(solids)

    def _remainder(self, parts, preserved):
        """
        The region records: the target polygons minus the preserved features, each piece then grown by OVERLAP inside
        the target polygon, so a region overlaps each feature it touches (see OVERLAP). The growth stays inside the
        copper, so the union of the records does not change.
        """
        if not preserved:
            return parts
        fast = self._clipper_remainder(parts, preserved)
        if fast is not None:
            return fast
        tree = STRtree(preserved)
        result = []
        # one target polygon at a time: small unions are fast
        for polygon in parts:
            near = tree.query(polygon, predicate='intersects')
            if not len(near):
                result.append(polygon)
                continue
            pieces = self._polygons(polygon.difference(unary_union([preserved[i] for i in near])))
            for piece in self._without_grid_slivers(pieces):
                result.extend(self._polygons(piece.buffer(OVERLAP, join_style='mitre').intersection(polygon)))
        return result

    @staticmethod
    def _without_grid_slivers(polygons):
        """Drop the slivers a few Clipper2 grid steps wide that the rounding of the coordinates leaves."""
        from appCommon import clipper_ops
        grid = 4.0 / clipper_ops.SCALE
        if not polygons:
            return []
        array = np.empty(len(polygons), dtype=object)
        array[:] = polygons
        thin = shapely.area(array) <= grid * shapely.length(array)
        if thin.any():
            thin[thin] = shapely.is_empty(shapely.buffer(array[thin], -grid))
        return array[~thin].tolist()

    @staticmethod
    def _clipped_candidates(solids, parts):
        """
        The indexes of the features that may stick out of the copper: one Clipper2 difference of all the features
        minus the copper, then the features near a piece that is left. The other features are covered by the copper
        (up to the Clipper2 grid), so they need no exact check.

        :return: set of indexes, or None if Clipper2 can not be used (the caller checks each feature)
        """
        from appCommon import clipper_ops
        if not solids or not clipper_ops.available() or len(solids) + len(parts) < clipper_ops.MIN_SHAPES:
            return None
        pieces = clipper_ops.boolean(solids, parts, 'difference')
        if pieces is None:
            return None
        pieces = BoardPrimitives._without_grid_slivers(pieces)
        if not pieces:
            return set()
        return set(STRtree(solids).query(pieces, predicate='intersects')[1].tolist())

    @staticmethod
    def _clipper_remainder(parts, preserved):
        """
        The same region records as ``_remainder`` with Clipper2, in a few operations for the whole layer (faster than
        GEOS on large pours). Clipper2 rounds the coordinates to its integer grid (1e-9 units), so a feature edge that
        lies on the copper edge can leave a sliver a few grid steps wide: the slivers are dropped.

        :return: list of Polygons, or None if Clipper2 can not be used (the caller then uses GEOS)
        """
        from appCommon import clipper_ops
        if not clipper_ops.available() or len(parts) + len(preserved) < clipper_ops.MIN_SHAPES:
            return None
        exact = clipper_ops.boolean(parts, preserved, 'difference')
        if exact is None:
            return None
        # the slivers go first: a sliver must not mark a feature as touching a region
        exact = BoardPrimitives._without_grid_slivers(exact)
        if not exact:
            return []
        # only the features that touch a region are shrunk: an isolated pad leaves no ring
        touching = set(STRtree(preserved).query(exact, predicate='intersects')[1].tolist())
        others = [solid for index, solid in enumerate(preserved) if index not in touching]
        shrunk = [preserved[index] for index in sorted(touching)]
        overlap = clipper_ops.difference_with_shrunk(parts, others, shrunk, OVERLAP)
        if overlap is None:
            return None
        overlap = BoardPrimitives._without_grid_slivers(overlap)
        array = np.empty(len(overlap), dtype=object)
        array[:] = overlap
        if len(array) and not shapely.is_valid(array).all():
            return None
        return overlap

    @staticmethod
    def _polygons(geometry):
        if geometry.is_empty:
            return []
        if isinstance(geometry, Polygon):
            return [geometry]
        return [polygon for part in getattr(geometry, 'geoms', ())
                for polygon in BoardPrimitives._polygons(part)]
