"""Gerber records made from native board features, before layer composition."""

import math

from shapely import prepare
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union


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
        target = unary_union(result['solid_geometry'])
        prepare(target)
        # GEOS union can move shared edges by a few ulps; this is not a manufacturing clearance.
        roundoff = 8 * max((math.ulp(v) for v in target.bounds), default=math.ulp(1.0))

        def numeric_sliver(polygon):
            # At ULP distances GEOS erosion can empty even large polygons; bound the lost area first.
            return polygon.area <= roundoff * polygon.length and \
                polygon.buffer(-roundoff).area <= roundoff * roundoff

        tools, apertures, preserved = {}, {}, []
        for tool, record in self.layers.get(layer, []):
            solid = record['solid']
            # A cutout or a negative layer can remove part of a source feature. Do not restore it by flashing.
            if not target.covers(solid) and not all(
                    numeric_sliver(p) for p in self._polygons(solid.difference(target))):
                continue
            signature = tuple(sorted(tool.items()))
            apid = apertures.get(signature)
            if apid is None:
                apid = len(apertures) + 10
                apertures[signature] = apid
                tools[apid] = dict(tool, geometry=[])
            tools[apid]['geometry'].append(record)
            preserved.append(solid)
        remainder = target.difference(unary_union(preserved)) if preserved else target
        polygons = [p for p in self._polygons(remainder) if not numeric_sliver(p)]
        if polygons:
            tools[0] = {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': p} for p in polygons]}
        result['tools'] = tools
        result['preserve_primitives'] = True

    @staticmethod
    def _polygons(geometry):
        if geometry.is_empty:
            return []
        if isinstance(geometry, Polygon):
            return [geometry]
        return [polygon for part in getattr(geometry, 'geoms', ())
                for polygon in BoardPrimitives._polygons(part)]
