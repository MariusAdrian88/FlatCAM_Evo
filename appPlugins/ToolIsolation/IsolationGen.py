"""Explicit-data isolation geometry algorithms. No application or widget state lives here."""

from typing import Iterable
import math

import numpy as np
import shapely
from shapely import LineString, MultiLineString, Polygon, MultiPolygon, Point, LinearRing
from shapely.ops import unary_union

from camlib import flatten_shapely_geometry, milling_orient, polygon_gap_pairs, clip_paths_to_area, \
    pass_covers_area, isolation_leftover_paths, gerber_quad_segs, PassBuffers, grace


def pass_offset(tooldia, nr_pass, overlap, negative_dia=None, denominator=2.0000001):
    """Overlap is a fraction; the rest strategy retains its original 2.0 denominator."""
    offset = tooldia * ((2 * nr_pass + 1) / denominator) - (nr_pass * overlap * tooldia)
    return -offset if negative_dia else offset


class AdaptivePassBuffers(PassBuffers):
    """PassBuffers whose passes follow the Gerber circle preferences: each pass buffers with the quad_segs of its own
    offset (the radius of its round corners and caps)."""

    def __init__(self, geometry, offsets, options, units='MM', abort=None, workers=None):
        super().__init__(geometry, offsets, 1, abort=abort, workers=workers)
        self._options = options
        self._units = units

    def _buffer(self, nr_pass):
        if self._abort is not None and self._abort():
            raise grace
        geoms = shapely.from_wkb(self._wkb)
        offset = self._offsets[nr_pass]
        if offset == 0:
            return unary_union(geoms)
        quad_segs = gerber_quad_segs(self._options, offset, self._units)
        return unary_union(shapely.buffer(geoms, offset, quad_segs=quad_segs, join_style='round'))


def object_units(obj, app):
    """The units of the geometry of a Gerber object (the units of the application if the object has none)."""
    return getattr(obj, 'units', None) or app.app_units


def pass_buffers(obj, offsets, app, geometry=None):
    """The passes of an isolation of a Gerber object, buffered ahead (see Geometry.isolation_buffers())."""
    working_geo = geometry if geometry is not None else obj.solid_geometry
    return AdaptivePassBuffers(working_geo, offsets, app.options, object_units(obj, app),
                               abort=lambda: app.abort_flag)


def v_effective_diameter(data):
    """Effective diameter: tip + 2 * |cut_z| * tan(angle / 2); None for an angle outside (0, 180), a negative tip
    or a diameter that is not positive."""
    try:
        tip = float(data['tools_mill_vtipdia'])
        angle = float(data['tools_mill_vtipangle'])
        cut_z = abs(float(data['tools_mill_cutz']))
        if not 0 < angle < 180 or tip < 0:
            return None
        dia = tip + (2 * cut_z * math.tan(math.radians(angle / 2)))
    except (KeyError, TypeError, ValueError):
        return None
    return dia if math.isfinite(dia) and dia > 0 else None


def aperture_geometry(aperture_storage, abort=None):
    total_geo = []
    for ap in list(aperture_storage.keys()):
        if 'geometry' in aperture_storage[ap]:
            for geo_el in aperture_storage[ap]['geometry']:
                if abort is not None:
                    abort()
                if 'solid' in geo_el and geo_el['solid'] is not None and geo_el['solid'].is_valid:
                    total_geo.append(geo_el['solid'])
    # Measure the same geometry as Optimal; a positive buffer changes the rounded gap.
    # (unary_union: a solid can be a MultiPolygon)
    return flatten_shapely_geometry(unary_union(total_geo).buffer(0))


def safe_tooldia(geometry, decimals):
    """The largest tool that fits between the polygons, truncated for the rounding of the distances.

    Without a gap to measure (fewer than 2 polygons) no tool is safe: the result is not positive, like for touching
    polygons, and the callers report 'Could not find a safe tool diameter'.
    """
    gaps = polygon_gap_pairs(geometry, decimals)
    if not gaps:
        return -10 ** -decimals
    return min(gaps) - 10 ** -decimals


def poly2rings(poly):
    return [poly.exterior] + [interior for interior in poly.interiors]


def combined_passes(pass_envelopes, offsets, tooldia, options, invert, clear_leftovers=False, boundary=None,
                    units='MM'):
    """Collect lazy pass geometry, stopping at the boundary without requesting unused passes."""
    solid_geo = []
    envelopes = []
    leftover_geo = []
    prev_geo = None
    passes_made = 0 if boundary is not None else len(offsets)
    for nr_pass, iso_geo in enumerate(pass_envelopes):
        if iso_geo == 'fail':
            continue
        if clear_leftovers:
            if prev_geo is not None:
                leftover_geo += isolation_leftover_paths(prev_geo, iso_geo, tooldia / 2.0,
                                                         gerber_quad_segs(options, tooldia / 2.0, units),
                                                         invert=invert)
            prev_geo = iso_geo

        if boundary is not None:
            clipped = clip_paths_to_area(iso_geo, boundary)
            if not clipped:
                # Disconnected areas can still be reached by a later pass.
                if isinstance(boundary, MultiPolygon) and not (
                        offsets[nr_pass] > 0 and pass_covers_area(iso_geo, boundary)):
                    continue
                break
            passes_made = nr_pass + 1
            envelopes.extend(flatten_shapely_geometry(iso_geo))
            solid_geo.extend(clipped)
            continue
        solid_geo.extend(flatten_shapely_geometry(iso_geo))
    return solid_geo, envelopes, leftover_geo, passes_made


def pad_passes(pads, envelope_geo, tooldia, first_pass, passes, overlap, options, negative_dia=None, units='MM'):
    solid_geo_union = unary_union(envelope_geo)
    pad_geo = []
    pad_array = np.empty(len(pads), dtype=object)
    pad_array[:] = list(pads)
    for nr_pass in range(first_pass, passes + first_pass):
        iso_offset = pass_offset(tooldia, nr_pass, overlap, negative_dia)
        pad_pass_geo = shapely.buffer(pad_array, iso_offset, quad_segs=gerber_quad_segs(options, iso_offset, units))
        pad_geo.append(unary_union(pad_pass_geo).difference(solid_geo_union))
    return pad_geo


def poly2ext(poly):
    return [poly.exterior]


def poly2ints(poly):
    return [interior for interior in poly.interiors]


def _paths_of(geo):
    """The rings of the polygons and the lines of a geometry, in order."""
    paths = []
    for geo_elem in flatten_shapely_geometry(geo):
        if isinstance(geo_elem, Polygon):
            paths += poly2rings(geo_elem)
        elif isinstance(geo_elem, LineString):
            paths.append(geo_elem)
    path_array = np.empty(len(paths), dtype=object)
    path_array[:] = paths
    return path_array


def _paths_touching(path_array, mask):
    """Boolean flags of the paths that intersect the mask (an index query with the mask as the prepared side)."""
    touching = np.zeros(len(path_array), dtype=bool)
    if len(path_array) and not mask.is_empty:
        touching[shapely.STRtree(path_array).query(mask, predicate='intersects')] = True
    return touching


def area_subtraction(geo, subtraction_geo):
    paths = _paths_of(geo)
    sub_union = unary_union(subtraction_geo)
    touching = _paths_touching(paths, sub_union)
    # a path that does not touch the mask is kept as it is
    result = paths.copy()
    result[touching] = shapely.difference(paths[touching], sub_union)
    return [new_geo for new_geo in result if not new_geo.is_empty]


def area_intersection(geo, intersection_geo):
    paths = _paths_of(geo)
    intersect_union = unary_union(intersection_geo)
    touching = _paths_touching(paths, intersect_union)
    # a path that does not touch the mask has no common part with it
    return [new_geo for new_geo in shapely.intersection(paths[touching], intersect_union) if not new_geo.is_empty]


def orient_envelope(geom_shp, invert, debug, error):
    if isinstance(geom_shp, str):
        # 'fail' of the envelope: not a geometry to iterate
        return geom_shp
    if isinstance(geom_shp, (MultiPolygon, MultiLineString)):
        geom = geom_shp.geoms
    else:
        geom = geom_shp

    if invert:
        try:
            pl = []
            for p in geom:
                if p is not None:
                    if isinstance(p, Polygon):
                        pl.append(milling_orient(p, conventional=True))
                    elif isinstance(p, LinearRing):
                        pl.append(Polygon(p.coords[::-1]))
            geom = MultiPolygon(pl)
        except TypeError:
            if isinstance(geom, Polygon) and geom is not None:
                geom = milling_orient(geom, conventional=True)
            elif isinstance(geom, LinearRing) and geom is not None:
                geom = Polygon(geom.coords[::-1])
            else:
                debug("ToolIsolation.generate_envelope() Error --> Unexpected Geometry %s" % type(geom))
        except Exception as e:
            error("ToolIsolation.generate_envelope() Error --> %s" % str(e))
            return 'fail'
    return geom


def generate_rest_geometry(geometry, tooldia, passes, overlap, invert, env_iso_type=2, negative_dia=None,
                           forced_rest=False, prog_plot="normal", prog_plot_handler=None, plot=False,
                           error_handler=None):
    """Return ordered isolation paths and polygons left for the next tool.

    Overlap is a fraction. Plot/error callbacks carry notifications only, not controller state.
    """
    isolated_geo = []
    not_isolated_geo = []
    work_geo = []

    # a Polygon, a MultiPolygon or a (nested) list of them
    geometry = [geo for geo in flatten_shapely_geometry(geometry) if isinstance(geo, Polygon)]
    geo_len = len(geometry)
    if geo_len == 0:
        return isolated_geo, not_isolated_geo
    geo_array = np.empty(geo_len, dtype=object)
    geo_array[:] = geometry
    # Sparse passes use bulk queries; dense passes stop at the first collision instead of collecting every pair.
    geo_indexes = np.arange(geo_len)
    geo_tree = None
    pass_offsets = []
    pass_intersects = []
    first_pass_iso = [None] * geo_len
    abandoned = np.zeros(geo_len, dtype=bool)
    for nr_pass in range(passes):
        iso_offset = pass_offset(tooldia, nr_pass, overlap, negative_dia, denominator=2.0)

        buf_chek = iso_offset * 1.99999
        # (quad_segs=16: as geo.buffer())
        check_geo = shapely.buffer(geo_array[geo_indexes], buf_chek, quad_segs=16)
        check_tree = shapely.STRtree(check_geo)
        intersect_flags = np.zeros(geo_len, dtype=bool)
        # ponytail: cap each bulk query at 65536 possible pairs (or one query polygon for a larger index).
        # A missed dense cluster can cost extra queries, but cannot create a quadratic temporary array.
        batch_size = max(1, 65536 // max(1, len(check_geo)))
        sample_indexes = np.linspace(0, geo_len, min(16, batch_size, geo_len), endpoint=False, dtype=int)
        other_idx, check_idx = check_tree.query(geo_array[sample_indexes], predicate='intersects')
        check_idx = geo_indexes[check_idx]
        intersect_flags[check_idx[check_idx != sample_indexes[other_idx]]] = True

        if len(check_idx) > 8 * len(sample_indexes):
            if geo_tree is None:
                geo_tree = shapely.STRtree(geo_array)
            # Keep collisions already found by the sample; only unresolved check buffers need a search.
            for check_pos in np.flatnonzero(~intersect_flags[geo_indexes]):
                idx = geo_indexes[check_pos]
                check = check_geo[check_pos]
                for other_idx in geo_tree.query(check):
                    if other_idx != idx and check.intersects(geo_array[other_idx]):
                        intersect_flags[idx] = True
                        break
        else:
            query_mask = np.ones(geo_len, dtype=bool)
            query_mask[sample_indexes] = False
            query_indexes = np.flatnonzero(query_mask)
            # The index holds check buffers and the original polygons are the prepared queries, so a large
            # ground pour is prepared once per query rather than walked for every nearby check buffer.
            for start in range(0, len(query_indexes), batch_size):
                batch = query_indexes[start:start + batch_size]
                other_idx, check_idx = check_tree.query(geo_array[batch], predicate='intersects')
                check_idx = geo_indexes[check_idx]
                intersect_flags[check_idx[check_idx != batch[other_idx]]] = True
        pass_offsets.append(iso_offset)
        pass_intersects.append(intersect_flags)

        if nr_pass != 0:
            continue

        # if we had an intersection do nothing, else the geo is isolated in the first pass
        free_indexes = np.flatnonzero(~intersect_flags)
        free_buffers = shapely.buffer(geo_array[free_indexes], iso_offset, quad_segs=16)
        for idx, temp_geo in zip(free_indexes.tolist(), free_buffers):
            geo = geometry[idx]
            first_pass_iso[idx] = temp_geo
            # Check interiors on the first pass only; abandon polygons whose holes cannot be isolated.
            if forced_rest is False:
                len_interiors = len(geo.interiors)
                if len_interiors >= 1:
                    total_poly_len = 1 + len_interiors
                    if isinstance(temp_geo, Polygon):
                        temp_geo_len = len([1] + list(temp_geo.interiors))
                        if total_poly_len != temp_geo_len:
                            abandoned[idx] = True
                    else:
                        try:
                            temp_geo_len = len(temp_geo)
                            if total_poly_len != temp_geo_len:
                                abandoned[idx] = True
                        except TypeError:
                            abandoned[idx] = True

        # the next passes are tested only for the geos that are not abandoned
        geo_indexes = np.flatnonzero(~abandoned)

    # the later passes of the polygons that are not abandoned and have no neighbor in the pass, buffered in bulk
    later_pass_iso = [None]
    for nr_pass in range(1, passes):
        idxs = np.flatnonzero(~pass_intersects[nr_pass] & ~abandoned)
        buffered = shapely.buffer(geo_array[idxs], pass_offsets[nr_pass], quad_segs=16)
        later_pass_iso.append(dict(zip(idxs.tolist(), buffered)))

    for idx, geo in enumerate(geometry):
        good_pass_iso = []
        if not abandoned[idx]:
            for nr_pass in range(passes):
                if not pass_intersects[nr_pass][idx]:
                    temp_geo = first_pass_iso[idx] if nr_pass == 0 else later_pass_iso[nr_pass][idx]
                    good_pass_iso.append(temp_geo)
                    if plot and prog_plot == 'progressive':
                        prog_plot_handler(temp_geo)

        if good_pass_iso:
            work_geo += good_pass_iso
        else:
            not_isolated_geo.append(geo)

    work_geo_shp = flatten_shapely_geometry(work_geo)
    if invert:
        try:
            is_polygon = [isinstance(p, Polygon) for p in work_geo_shp]
            polygons = np.empty(sum(is_polygon), dtype=object)
            polygons[:] = [p for p in work_geo_shp if isinstance(p, Polygon)]
            oriented = iter(shapely.orient_polygons(polygons, exterior_cw=False))
            pl = []
            for p, polygon in zip(work_geo_shp, is_polygon):
                if polygon:
                    pl.append(next(oriented))
                elif isinstance(p, LinearRing):
                    pl.append(Polygon(p.coords[::-1]))
            work_geo_shp = MultiPolygon(pl)
        except Exception as e:
            if error_handler is not None:
                error_handler("ToolIsolation.generate_rest_geometry() Error --> %s" % str(e))
            return 'fail', 'fail'

    actual_geo = flatten_shapely_geometry(work_geo_shp)
    if env_iso_type == 0:
        for geo in actual_geo:
            isolated_geo.append(geo.exterior)
    elif env_iso_type == 1:
        for geo in actual_geo:
            isolated_geo += [interior for interior in geo.interiors]
    else:
        for geo in actual_geo:
            isolated_geo += [geo.exterior] + [interior for interior in geo.interiors]
    return isolated_geo, not_isolated_geo


def get_selected_interior(poly: Polygon, point: tuple) -> [Polygon, None]:
    try:
        ints = [Polygon(x) for x in poly.interiors]
    except AttributeError:
        return None
    for poly in ints:
        if poly.contains(Point(point)):
            return poly
    return None


def find_polygon_ignore_interiors(point, geoset):
    """Find the first containing polygon, treating its holes as filled."""
    try:
        for sub_geo in geoset:
            p = find_polygon_ignore_interiors(point, geoset=sub_geo)
            if p is not None:
                return p
    except TypeError:
        try:
            if isinstance(geoset, LinearRing):
                geoset = Polygon(geoset)
            poly_ext = Polygon(geoset.exterior)
            if poly_ext.contains(Point(point)):
                return geoset
        except AttributeError:
            return None
    return None


def flatten_list(obj_list):
    for item in obj_list:
        if isinstance(item, Iterable) and not isinstance(item, (str, bytes)):
            yield from flatten_list(item)
        else:
            yield item


# ----------------------------------------------------------------------------------------------- Voronoi isolation

# The sampling step is user controlled. The site cap bounds diagram size; over it the run fails
# and asks for a larger spacing instead of quietly lowering the requested fidelity.
VORONOI_MAX_SITES = 20000
# Bounded local refinement: only the boundary intervals named by a failed check are bisected.
VORONOI_REFINE_ROUNDS = 3
# Interior points of a Voronoi edge where the two nearest sites must name the same owner pair.
VORONOI_PROBE_FRACTIONS = (0.15, 0.5, 0.85)
# Deterministic cap on the nearest-site candidates inspected for one probe.
VORONOI_MAX_PROBE_CANDIDATES = 256


class VoronoiIsolationError(Exception):
    """A Voronoi isolation cannot be made safely; the message says why."""

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics) if diagnostics else {}


class VoronoiAborted(VoronoiIsolationError):
    """The user stopped the Voronoi isolation."""


class _VoronoiRetry(Exception):
    """An attempt that local refinement may repair; the payload names the owners and the region."""

    def __init__(self, reason, message, owners=(), region=None, min_clearance=None):
        super().__init__(message)
        self.reason = reason
        self.owners = sorted({int(owner) for owner in owners})
        self.region = region
        self.min_clearance = min_clearance


class _VoronoiSite:
    """A boundary sample: position, copper owner and the boundary segment interval it samples."""

    __slots__ = ('x', 'y', 'owner', 'segment')

    def __init__(self, x, y, owner, segment):
        self.x = x
        self.y = y
        self.owner = owner
        self.segment = segment


def _voronoi_failure(reason, message, owners=(), region=None, sites=None, retries=0,
                     spacing=None, tooldia=None, min_clearance=None, units='MM'):
    """An actionable error: a stable code, the involved owners and the center of the region."""
    owners = sorted({int(owner) for owner in owners})
    bounds = None
    parts = []
    if owners:
        parts.append("owners %s" % ', '.join(str(owner) for owner in owners))
    if region is not None:
        try:
            bounds = tuple(float(value) for value in region.bounds)
        except AttributeError:
            bounds = tuple(float(value) for value in region)
        parts.append("near (%.4f, %.4f)" % ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0))
    if parts:
        message = "%s [%s]" % (message.rstrip('.'), '; '.join(parts))
    diagnostics = {
        'reason': reason,
        'owners': owners,
        'region': bounds,
        'sites': sites,
        'retries': retries,
        'spacing': spacing,
        'tooldia': tooldia,
        'min_clearance': min_clearance,
        'units': units,
    }
    if region is not None:
        diagnostics['region_geometry'] = region
    return VoronoiIsolationError(message, diagnostics)


def _voronoi_union(geometry):
    """The non-empty polygons of a geometry or of a nesting of lists / tuples, fused into one area."""
    if geometry is None:
        return None
    parts = [part for part in flatten_shapely_geometry(geometry) if part is not None and not part.is_empty]
    if not parts:
        return None
    if not np.isfinite(shapely.get_coordinates(parts)).all():
        raise VoronoiIsolationError("The Voronoi input geometry contains non-finite coordinates.")
    try:
        return unary_union(parts)
    except Exception:
        # an invalid input must be reported, not crash the worker
        try:
            repaired = [part.buffer(0) for part in parts]
            return unary_union([part for part in repaired if part is not None and not part.is_empty])
        except Exception:
            return None


def _voronoi_polygons(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, (MultiPolygon, shapely.GeometryCollection)):
        return [part for child in geometry.geoms for part in _voronoi_polygons(child)]
    return []


def _voronoi_lines(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, LinearRing):
        return [geometry]
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, (MultiLineString, shapely.GeometryCollection)):
        return [part for child in geometry.geoms for part in _voronoi_lines(child)]
    return []


def _voronoi_xy(coord, point):
    return (point[0], point[1]) + tuple(coord[2:])


def _dedupe_trail_edges(lines):
    """One cut edge per exact coordinate sequence: a reversed copy is the same edge, not a second cut."""
    unique = []
    seen = set()
    for coords in lines:
        key = tuple(coords)
        reverse = key[::-1]
        canonical = key if key <= reverse else reverse
        if canonical in seen:
            continue
        seen.add(canonical)
        unique.append(coords)
    return unique


def _quantize_line(line, decimals):
    """The trail on the output coordinate grid: consecutive duplicate points dropped, empty trails as None."""
    coords = [(round(float(coord[0]), decimals), round(float(coord[1]), decimals)) + tuple(coord[2:])
              for coord in line.coords]
    clean = [coords[0]]
    for coord in coords[1:]:
        if coord[:2] != clean[-1][:2]:
            clean.append(coord)
    if len(clean) < 2:
        return None
    result = LineString(clean)
    return result if result.length > 0 else None


def _cluster_trail_endpoints(points, tolerance, abort=None):
    """
    The graph nodes of the trail endpoints.

    Exact coordinates are the same node. With a positive tolerance a point joins the fixed representative of a node
    only when it is within the tolerance of that representative and of every point already in the node: no transitive
    chain can move an endpoint farther than the tolerance. A point close to two nodes is ambiguous and is left alone,
    so a connector between two real nodes is never forged. The representative is the smallest point of the node, which
    makes the result independent of the input order. The grid search has no worst-case bound when a cell is dense.

    :return:            (node index for every endpoint, the representative point of every node)
    """
    count = len(points)
    node_of = [0] * count
    if tolerance <= 0:
        exact = {}
        cluster_points = []
        for index in range(count):
            if abort is not None and index % 4096 == 0 and abort():
                raise VoronoiAborted("Voronoi isolation aborted.")
            key = points[index]
            node = exact.get(key)
            if node is None:
                node = len(cluster_points)
                exact[key] = node
                cluster_points.append(key)
            node_of[index] = node
        return node_of, cluster_points

    unique = {}
    for index, point in enumerate(points):
        unique.setdefault(point, []).append(index)
    items = sorted(unique.items(), key=lambda item: (item[0], item[1][0]))
    cell = tolerance
    grid = {}
    cluster_points = []
    members = []
    for position, (point, indices) in enumerate(items):
        if abort is not None and position % 1024 == 0 and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        x, y = point
        grid_x, grid_y = math.floor(x / cell), math.floor(y / cell)
        matches = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for node_nr in grid.get((grid_x + dx, grid_y + dy), ()):
                    rep_x, rep_y = cluster_points[node_nr]
                    if math.hypot(x - rep_x, y - rep_y) > tolerance:
                        continue
                    for member_x, member_y in members[node_nr]:
                        if math.hypot(x - member_x, y - member_y) > tolerance:
                            break
                    else:
                        matches.append(node_nr)
        if len(matches) == 1:
            node_nr = matches[0]
        else:
            node_nr = len(cluster_points)
            cluster_points.append(point)
            members.append([])
            grid.setdefault((grid_x, grid_y), []).append(node_nr)
        members[node_nr].append(point)
        for index in indices:
            node_of[index] = node_nr

    order = sorted(range(len(cluster_points)), key=lambda node_nr: (cluster_points[node_nr], node_nr))
    remap = [0] * len(cluster_points)
    for new_nr, node_nr in enumerate(order):
        remap[node_nr] = new_nr
    cluster_xy = [cluster_points[node_nr] for node_nr in order]
    return [remap[node_nr] for node_nr in node_of], cluster_xy


def join_trails(paths, tolerance, abort=None):
    """
    Join validated cut edges into the fewest continuous trails, without adding any cut length.

    Duplicate edges (the same coordinate sequence, forward or reversed) are one cut. Endpoints with exactly the same
    coordinates are one graph node; a positive `tolerance` additionally merges only endpoints that stay within the
    tolerance of a fixed representative, never through a transitive chain. A real edge whose two endpoints fell into
    one node is kept on its own nodes instead of vanishing, so no connector is forged.

    Every edge is used exactly once; each connected component is split into max(1, odd_degree_count / 2) edge-disjoint
    trails, so no edge is retraced and no connector is added. The trail order is deterministic and does not depend on
    the order of `paths`. The edge-use state is allocated once for the whole graph. Endpoint clustering uses a grid;
    a dense cell makes it slower than linear, so it has no worst-case bound. Virtual pairing edges are walked but never
    emitted.

    :param paths:       list of Shapely LineString / LinearRing cut edges
    :param tolerance:   largest endpoint distance that is still the same node, 0 = exact coordinates only
    :param abort:       callable returning True when the user cancelled the run
    :return:            list of Shapely LineString trails
    """
    tolerance = float(tolerance)
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("The trail node tolerance must be a finite, non-negative distance.")

    lines = []
    for line_nr, line in enumerate(paths or []):
        if abort is not None and line_nr % 1024 == 0 and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        if line is None or line.is_empty:
            continue
        coords = list(line.coords)
        if len(coords) < 2 or line.length <= 0:
            continue
        lines.append(coords)
    lines = _dedupe_trail_edges(lines)
    if not lines:
        return []

    points = []
    for coords in lines:
        points.append((float(coords[0][0]), float(coords[0][1])))
        points.append((float(coords[-1][0]), float(coords[-1][1])))
    node_of, cluster_xy = _cluster_trail_endpoints(points, tolerance, abort)

    edges = []
    for line_nr, coords in enumerate(lines):
        start, end = node_of[2 * line_nr], node_of[2 * line_nr + 1]
        original_start = (float(coords[0][0]), float(coords[0][1]))
        original_end = (float(coords[-1][0]), float(coords[-1][1]))
        if start == end and original_start != original_end:
            length = LineString(coords).length
            if length <= tolerance:
                # shorter than the node tolerance: roundoff, not a cut
                continue
            # A real edge must not vanish into one node: leave it separate instead of forging its endpoints.
            start = len(cluster_xy)
            cluster_xy.append(original_start)
            end = len(cluster_xy)
            cluster_xy.append(original_end)
        coords = list(coords)
        coords[0] = _voronoi_xy(coords[0], cluster_xy[start])
        coords[-1] = _voronoi_xy(coords[-1], cluster_xy[end])
        if len(coords) == 2 and coords[0] == coords[1]:
            continue
        edges.append((start, end, coords))
    if not edges:
        return []
    edges.sort(key=lambda edge: (edge[0], edge[1], len(edge[2]), edge[2]))

    node_count = len(cluster_xy)
    adjacency = [[] for _ in range(node_count)]
    degree = [0] * node_count
    for edge_nr, (start, end, _coords) in enumerate(edges):
        adjacency[start].append((end, edge_nr))
        if end == start:
            degree[start] += 2
        else:
            adjacency[end].append((start, edge_nr))
            degree[start] += 1
            degree[end] += 1
    for entries in adjacency:
        entries.sort()

    seen = [False] * node_count
    components = []
    for start in range(node_count):
        if seen[start] or not adjacency[start]:
            continue
        component = []
        stack = [start]
        seen[start] = True
        steps = 0
        while stack:
            steps += 1
            if abort is not None and steps % 4096 == 0 and abort():
                raise VoronoiAborted("Voronoi isolation aborted.")
            node = stack.pop()
            component.append(node)
            for other, _edge_nr in adjacency[node]:
                if not seen[other]:
                    seen[other] = True
                    stack.append(other)
        component.sort()
        components.append(component)

    firsts = []
    for component in components:
        odd = [node for node in component if degree[node] % 2]
        if len(odd) % 2:
            raise VoronoiIsolationError("The Voronoi trail graph has an odd number of odd nodes.")
        for pair_nr in range(0, len(odd), 2):
            start_node, end_node = odd[pair_nr], odd[pair_nr + 1]
            edge_nr = len(edges)
            edges.append((start_node, end_node, None))
            adjacency[start_node].append((end_node, edge_nr))
            adjacency[end_node].append((start_node, edge_nr))
            adjacency[start_node].sort()
            adjacency[end_node].sort()
            degree[start_node] += 1
            degree[end_node] += 1
        firsts.append(odd[0] if odd else component[0])

    # One edge-use allocation for the whole graph, virtual pairing edges included.
    used = bytearray(len(edges))
    trails = []
    for first in firsts:
        reverse = []
        walk = [(first, None)]
        steps = 0
        while walk:
            steps += 1
            if abort is not None and steps % 4096 == 0 and abort():
                raise VoronoiAborted("Voronoi isolation aborted.")
            node, entered = walk[-1]
            entries = adjacency[node]
            while entries and used[entries[-1][1]]:
                entries.pop()
            if entries:
                other, edge_nr = entries[-1]
                used[edge_nr] = 1
                walk.append((other, edge_nr))
            else:
                walk.pop()
                if entered is not None:
                    reverse.append(entered)

        current = first
        circuit = reverse[::-1]
        if degree[first] and any(edges[edge_nr][2] is None for edge_nr in circuit):
            # Start at a virtual edge so the circuit's arbitrary origin cannot split one real trail in two.
            for position, edge_nr in enumerate(circuit):
                start_node, end_node, coords = edges[edge_nr]
                if coords is None:
                    circuit = circuit[position:] + circuit[:position]
                    break
                current = end_node if start_node == current else start_node
        trail = []
        for edge_nr in circuit:
            start_node, end_node, coords = edges[edge_nr]
            if coords is None:
                if len(trail) >= 2:
                    trails.append(LineString(trail))
                trail = []
                current = end_node if start_node == current else start_node
                continue
            if start_node == current:
                sequence = coords
                current = end_node
            elif end_node == current:
                sequence = coords[::-1]
                current = start_node
            else:
                raise VoronoiIsolationError("The Voronoi trail graph is not connected.")
            if trail:
                trail.extend(sequence[1:])
            else:
                trail = list(sequence)
        if len(trail) >= 2:
            trails.append(LineString(trail))
    return trails


def _voronoi_sample_ring(ring, spacing, sites, owner, seen, abort, max_sites, work_budget=None):
    """Evenly spaced sites on every segment, each keeping its boundary interval for local refinement."""
    coords = list(ring.coords)
    for start, end in zip(coords, coords[1:]):
        ax, ay, bx, by = start[0], start[1], end[0], end[1]
        length = math.hypot(bx - ax, by - ay)
        if length <= 0:
            continue
        intervals = length / spacing
        if intervals > max_sites:
            raise VoronoiIsolationError(
                "The Voronoi site budget is exceeded. Increase the Voronoi spacing.",
                {'reason': 'site_budget', 'sites': len(sites), 'spacing': spacing})
        steps = max(1, int(math.ceil(intervals)))
        if work_budget is not None:
            if steps > work_budget[0]:
                raise VoronoiIsolationError(
                    "The Voronoi sampling work budget is exceeded. Increase the Voronoi spacing.",
                    {'reason': 'site_budget', 'sites': len(sites), 'spacing': spacing})
            work_budget[0] -= steps
        for step in range(steps):
            if abort is not None and len(sites) % 256 == 0 and abort():
                raise VoronoiAborted("Voronoi isolation aborted.")
            t0 = step / steps
            t1 = (step + 1) / steps
            point = (ax + (bx - ax) * t0, ay + (by - ay) * t0)
            key = point
            if key in seen:
                # touching / overlapping bodies are fused first, so a shared vertex keeps one owner
                continue
            seen[key] = owner
            sites.append(_VoronoiSite(point[0], point[1], owner, (ax, ay, bx, by, t0, t1)))
            if len(sites) > max_sites:
                raise VoronoiIsolationError(
                    "The Voronoi site budget is exceeded (%d > %d sites). Increase the Voronoi spacing."
                    % (len(sites), max_sites),
                    {'reason': 'site_budget', 'sites': len(sites), 'spacing': spacing})


def _voronoi_nearest_pair(site_tree, site_xy, site_owners, point, margin, abort):
    """
    The owner pair of the two nearest sites at a point, or None with the competing owners when sites tie.

    The distances use coordinates translated to the probe, so a large absolute translation cannot hide a real
    difference between two nearly equal distances.
    """
    x, y = point
    if abort is not None and abort():
        raise VoronoiAborted("Voronoi isolation aborted.")
    first = int(site_tree.nearest(Point(x, y)))
    first_x, first_y = site_xy[first]
    nearest_distance = math.hypot(first_x - x, first_y - y)
    radius = nearest_distance + margin
    candidates = site_tree.query(shapely.box(x - radius, y - radius, x + radius, y + radius))
    if len(candidates) > VORONOI_MAX_PROBE_CANDIDATES:
        return None, {site_owners[first]}
    distances = sorted((math.hypot(site_xy[int(index)][0] - x, site_xy[int(index)][1] - y), int(index))
                       for index in candidates)
    if len(distances) < 2:
        return None, {site_owners[first]}
    nearest_owner = site_owners[distances[0][1]]
    second_owner = site_owners[distances[1][1]]
    if nearest_owner == second_owner:
        # A bisector between two samples of one body is internal, whether or not the two distances tie exactly.
        return (nearest_owner, second_owner), {nearest_owner}
    second_distance = distances[1][0]
    tied_owners = {site_owners[int(index)] for distance, index in distances
                   if distance <= second_distance + margin}
    if len(tied_owners) > 2:
        # a third cell competes with the border pair; a side probe could hop into it
        return None, tied_owners
    # Two equally near owners are the two cells that share this Voronoi edge: that tie is the certificate.
    return (nearest_owner, second_owner), tied_owners


def _voronoi_classify_edge(edge, site_tree, site_xy, site_owners, margin, abort):
    """The owner pair of one Voronoi edge, certified at several interior points with a nearest-site inequality."""
    owners_seen = set()
    shared_pairs = set()
    internal = False
    for fraction in VORONOI_PROBE_FRACTIONS:
        point = edge.interpolate(fraction, normalized=True)
        pair, here = _voronoi_nearest_pair(site_tree, site_xy, site_owners, (point.x, point.y), margin, abort)
        owners_seen |= here
        if pair is None:
            return None, owners_seen
        if pair[0] == pair[1]:
            internal = True
        else:
            shared_pairs.add(tuple(sorted(pair)))
    if shared_pairs:
        if len(shared_pairs) > 1:
            # different owners are on the other side along the edge: refinement, not a guess
            return None, owners_seen
        return 'shared', owners_seen
    return ('internal' if internal else None), owners_seen


def _voronoi_refine_sites(sites, region, owners, max_sites, abort=None):
    """
    Bisect the sample intervals of the involved owners that touch the failed region.

    Unaffected coarse samples are kept, so only the corridor named by the failure is sampled finer. The result
    never exceeds `max_sites`; `budget_hit` says that some selected intervals were not refined.
    """
    seen = {(site.x, site.y) for site in sites}
    shapely.prepare(region)
    capacity = max(0, int(max_sites) - len(sites))
    selected = []
    budget_hit = False
    for site_nr, site in enumerate(sites):
        if abort is not None and site_nr % 256 == 0 and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        if site.owner not in owners:
            continue
        ax, ay, bx, by, t0, t1 = site.segment
        middle = (t0 + t1) / 2.0
        if middle <= t0 or middle >= t1:
            # no representable midpoint: this interval cannot be refined further
            continue
        px = ax + (bx - ax) * middle
        py = ay + (by - ay) * middle
        if not region.intersects(Point(px, py)):
            continue
        key = (px, py)
        if key in seen:
            continue
        if len(selected) >= capacity:
            budget_hit = True
            break
        seen.add(key)
        selected.append((site_nr, middle, px, py))
    if not selected:
        return list(sites), 0, budget_hit
    selected_by_site = {site_nr: (middle, px, py) for site_nr, middle, px, py in selected}
    result = []
    for site_nr, site in enumerate(sites):
        result.append(site)
        if site_nr not in selected_by_site:
            continue
        middle, px, py = selected_by_site[site_nr]
        ax, ay, bx, by, t0, t1 = site.segment
        site.segment = (ax, ay, bx, by, t0, middle)
        result.append(_VoronoiSite(px, py, site.owner, (ax, ay, bx, by, middle, t1)))
    return result, len(selected), budget_hit


def voronoi_working_bounds(area, tooldia, coord_decimals):
    """Cutter-center enclosure and physical working room for complete partition islands."""
    # Each of endpoint rounding and re-rounded graph intersections can displace a point by half a grid diagonal.
    reserve = 0.0 if coord_decimals is None else 2 * math.sqrt(0.5) * 10.0 ** (-int(coord_decimals))
    # Default round buffers have 16 segments/quadrant: circumscribe both offsets, not the cutter certificate.
    offset = (tooldia / 2.0 + reserve) / math.cos(math.pi / 64)
    centers = area.buffer(offset, quad_segs=16)
    return centers, centers.buffer(offset, quad_segs=16)


def voronoi_isolation(geometry, boundary, tooldia, spacing, units='MM', simplify_tol=0.0,
                      max_sites=VORONOI_MAX_SITES, coord_decimals=None, abort=None, isolation_area=None, report=None):
    """
    Inter-body isolation from the point Voronoi diagram of the copper boundaries.

    Each connected copper body is an owner. Its exterior and interior boundaries are sampled at intervals no larger
    than `spacing` (not a bound on the resulting toolpath error). A Voronoi edge is kept only when the nearest sites
    at several interior points certify the same pair of different owners: this is the merged-per-owner border, without
    the bisectors inside one body. An ambiguous edge is not guessed; only the boundary intervals of the involved owners
    near the ambiguous region are bisected, at most `VORONOI_REFINE_ROUNDS` times and without exceeding `max_sites`.

    The borders are clipped to the tool-center free region `boundary.buffer(-tooldia / 2) - copper.buffer(tooldia / 2)`
    and the result is checked against the real cutter sweep: no copper is cut, the sweep stays in the boundary, and the
    remaining board has no component that touches two owners. The difference is reported as a failure, never as a
    successful isolation. Clearance is checked by distances, not polygonal buffer areas. Only floating-point roundoff is
    allowed, independently of the sampling step. The separation sweep is inscribed in the real cutter sweep.
    The validated cut edges are finally joined into edge-disjoint Euler trails: only shared endpoints are merged,
    so the cut length does not change and no connector is added, while the machine lifts far less often.

    With `coord_decimals` the trails are placed on that output coordinate grid before the final checks, so the
    returned geometry is checked at the full cutter radius, never radius minus a grid allowance.
    Rounding displacement is reserved outside copper, not taken from cutter clearance. Board contacts that cannot
    be represented safely on the grid are rejected. The returned tool paths must not be simplified again.

    Cancellation is polled between stages and in Python loops; individual GEOS calls cannot be interrupted.

    :param geometry:    copper geometry: Polygon / MultiPolygon or a nesting of lists of them
    :param boundary:    the machining boundary: Polygon / MultiPolygon
    :param tooldia:     effective cutter diameter, positive
    :param spacing:     sampling step along the copper boundaries, in the object units
    :param units:       the units of the values, for the error messages only
    :param simplify_tol: simplification tolerance applied before the final cutter-sweep check, 0 = off
    :param max_sites:   the largest accepted number of sampling sites
    :param coord_decimals: the output coordinate decimals, None = no output grid
    :param abort:       callable returning True when the user cancelled the run
    :param isolation_area: saved intended island enclosure, when boundary includes an artificial working collar;
                           its compensated perimeter is cut to close the partition islands;
                           None means boundary is a physical board and its whole area must be separated
    :param report:       optional dict; it gets "spacing", the sampling step used (see below)
    :return:            list of Shapely LineString / LinearRing tool paths

    A spacing that would give more than max_sites sites is coarsened to fit, up to half the tool diameter (a site
    spacing that fine is much denser than the cutter needs); the used spacing is in eport. Beyond that limit
    the run fails and asks for a larger spacing.
    """
    try:
        tooldia = float(tooldia)
        spacing = float(spacing)
    except (TypeError, ValueError):
        raise VoronoiIsolationError("The tool diameter and the Voronoi spacing must be numbers.")
    if not math.isfinite(tooldia) or tooldia <= 0:
        raise VoronoiIsolationError("The Voronoi strategy needs a positive tool diameter.")
    if not math.isfinite(spacing) or spacing <= 0:
        raise VoronoiIsolationError("The Voronoi spacing must be a positive distance (in %s)." % units)
    if max_sites is None:
        max_sites = VORONOI_MAX_SITES

    boundary = _voronoi_union(boundary)
    if boundary is None or boundary.is_empty or boundary.area <= 0:
        raise VoronoiIsolationError(
            "Voronoi isolation needs a machining boundary with an area: select the board outline object.")
    if not boundary.is_valid:
        boundary = boundary.buffer(0)
    if boundary.is_empty or boundary.area <= 0:
        raise VoronoiIsolationError("The machining boundary has no usable area.")

    copper = _voronoi_union(geometry)
    if copper is None or copper.is_empty or copper.area <= 0:
        raise VoronoiIsolationError("There is no copper to isolate.")
    if not copper.is_valid:
        copper = copper.buffer(0)
    owners = _voronoi_polygons(copper)
    if len(owners) <= 1:
        raise VoronoiIsolationError(
            "There is only one copper body: the Voronoi strategy isolates different bodies from each other and "
            "makes no contour pass around a single body.")

    scale = max(tooldia, *(abs(value) for value in boundary.bounds))
    tolerance = min(tooldia * 1e-10, 64 * math.ulp(scale))
    if not boundary.covers(copper):
        raise VoronoiIsolationError(
            "The copper extends outside the machining boundary. Move the boundary or the copper so that every "
            "copper body is inside it; Voronoi isolation does not ignore copper outside the board.")

    if boundary.difference(copper).area <= 0:
        raise _voronoi_failure(
            'invalid_boundary',
            "Selected boundary contains no machining area outside the copper. "
            "Select a board-outline object or draw a closed machining area.",
            sites=0, spacing=spacing, tooldia=tooldia, units=units)

    if isolation_area is None:
        isolation_area = boundary
    else:
        isolation_area = _voronoi_union(isolation_area)
        if isolation_area is None or isolation_area.is_empty or not isolation_area.is_valid or \
                not boundary.covers(isolation_area) or not isolation_area.covers(copper):
            raise VoronoiIsolationError("The intended island enclosure must contain all copper and fit in the boundary.")

    perimeter = sum(ring.length for owner in owners for ring in [owner.exterior] + list(owner.interiors))
    if perimeter / spacing > 0.8 * max_sites:
        coarse = perimeter / (0.8 * max_sites)
        if coarse <= max(spacing, tooldia / 2.0):
            spacing = coarse
    if report is not None:
        report['spacing'] = spacing

    sites = []
    seen = {}
    # Repeated sample coordinates consume work even when they don't consume an active site.
    work_budget = [max_sites * 8]
    try:
        for owner_nr, owner in enumerate(owners):
            for ring in [owner.exterior] + list(owner.interiors):
                _voronoi_sample_ring(ring, spacing, sites, owner_nr, seen, abort, max_sites, work_budget)
            if len(sites) > max_sites:
                raise VoronoiIsolationError(
                    "The Voronoi site budget is exceeded (%d > %d sites). Increase the Voronoi spacing (current %.4f %s)."
                    % (len(sites), max_sites, spacing, units),
                    {'reason': 'site_budget', 'sites': len(sites), 'spacing': spacing, 'tooldia': tooldia,
                     'retries': 0, 'units': units})
    except VoronoiIsolationError as err:
        if err.diagnostics.get('reason') == 'site_budget':
            err.diagnostics.update({'tooldia': tooldia, 'units': units, 'retries': 0})
        raise
    if len(sites) < 2:
        raise VoronoiIsolationError("The copper boundaries give too few sample points for a Voronoi diagram.")

    retries = 0
    while True:
        if abort is not None and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        try:
            return _voronoi_attempt(sites, boundary, copper, owners, tooldia, spacing, units,
                                    simplify_tol, coord_decimals, tolerance, abort, isolation_area)
        except _VoronoiRetry as retry:
            if retry.region is None or retries >= VORONOI_REFINE_ROUNDS:
                raise _voronoi_failure(retry.reason, str(retry), owners=retry.owners, region=retry.region,
                                       sites=len(sites), retries=retries, spacing=spacing, tooldia=tooldia,
                                       min_clearance=retry.min_clearance, units=units)
            region = retry.region.buffer(max(spacing * 2.0, tolerance))
            refined, added, budget_hit = _voronoi_refine_sites(sites, region, set(retry.owners), max_sites, abort)
            if not added:
                stop = 'site_budget' if budget_hit else 'refinement_exhausted'
                message = "The Voronoi site budget prevents further local refinement." if budget_hit else \
                    "No boundary interval in the failed region can be refined further."
                failure = _voronoi_failure(
                    retry.reason, "%s %s" % (str(retry), message),
                    owners=retry.owners, region=retry.region, sites=len(sites), retries=retries,
                    spacing=spacing, tooldia=tooldia, min_clearance=retry.min_clearance, units=units)
                failure.diagnostics['refinement_stop'] = stop
                raise failure
            sites = refined
            retries += 1


def _closest_region(paths, other, radius):
    """A disk around the place where the paths come closest to another geometry, for the error location."""
    near = shapely.ops.nearest_points(paths, other)[0]
    return near.buffer(max(radius, 0.0) or 1e-6)


def _voronoi_attempt(sites, boundary, copper, owners, tooldia, spacing, units,
                     simplify_tol, coord_decimals, tolerance, abort, isolation_area):
    """One full diagram, classification and cutter-sweep validation; a repairable failure asks for refinement."""
    site_xy = np.asarray([(site.x, site.y) for site in sites], dtype=float)
    site_owners = [site.owner for site in sites]
    site_tree = shapely.STRtree(shapely.points(site_xy))

    precision_margin = 0.0
    if coord_decimals is not None:
        try:
            coord_decimals = int(coord_decimals)
        except (TypeError, ValueError):
            raise VoronoiIsolationError("The output coordinate decimals must be an integer.")
        if coord_decimals < 0:
            raise VoronoiIsolationError("The output coordinate decimals must not be negative.")
        precision_margin = math.sqrt(0.5) * 10.0 ** (-coord_decimals)
    radius = tooldia / 2.0
    complete_islands = not isolation_area.equals(boundary)
    enclosure = voronoi_working_bounds(isolation_area, tooldia, coord_decimals)[0] if complete_islands else None
    free = enclosure if complete_islands else boundary.buffer(-radius)
    if free.is_empty:
        raise _voronoi_failure(
            'boundary_clearance',
            "The tool does not fit inside the machining boundary: increase the boundary or use a smaller tool.",
            sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)
    # Circumscribe the round buffer when reserving grid displacement; polygonal buffers are otherwise inscribed.
    exclusion_radius = (radius + precision_margin) / math.cos(math.pi / 64) if precision_margin else radius
    free = free.difference(copper.buffer(exclusion_radius))
    free = free.buffer(0) if not free.is_valid else free
    if free.is_empty or free.area <= 0:
        raise _voronoi_failure(
            'copper_clearance',
            "No cutter-center area remains inside selected boundary after reserving cutter clearance. "
            "Check boundary and tool diameter.",
            sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)

    try:
        # Padding is construction-only. Clip to the saved cutter-safe domain, never buffer it back inward.
        working_boundary = boundary if isolation_area.equals(boundary) else boundary.buffer(radius + precision_margin)
        diagram = shapely.voronoi_polygons(shapely.MultiPoint(site_xy), extend_to=working_boundary, only_edges=True)
    except Exception as err:
        raise _voronoi_failure('classification_ambiguous',
                               "The point Voronoi diagram could not be built: %s" % err,
                               sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)
    # Edges outside the tool-center free region cannot contribute a cut; clipping them first also keeps the
    # classification probes away from the far cells of the extended diagram. The unclipped edge is kept for the
    # final union so that the original Voronoi nodes stay exact.
    probe_edges = []
    for raw_edge in _voronoi_lines(diagram):
        if raw_edge.length <= tolerance or not raw_edge.intersects(free):
            continue
        for edge in _voronoi_lines(raw_edge.intersection(free)):
            if edge.length > tolerance:
                probe_edges.append((edge, raw_edge))
    edges = [edge for edge, _full in probe_edges]
    if not edges:
        raise _voronoi_failure('classification_ambiguous',
                               "The point Voronoi diagram has no edges (degenerate sample points).",
                               sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)

    # The nearest sites at interior points of an edge are the two cells that share it. A third competing owner is
    # not resolved by a side offset: the involved owners and region go to local refinement.
    margin = max(tolerance * 16.0, spacing * 1e-9)
    shared = []
    ambiguous_edges = []
    ambiguous_owners = set()
    for edge_nr, (edge, full_edge) in enumerate(probe_edges):
        if abort is not None and edge_nr % 256 == 0 and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        pair, owners_seen = _voronoi_classify_edge(edge, site_tree, site_xy, site_owners, margin, abort)
        if pair is None:
            ambiguous_edges.append(edge)
            ambiguous_owners.update(owners_seen)
        elif pair == 'shared':
            shared.append(full_edge)
    if ambiguous_edges:
        region = unary_union([edge.buffer(max(spacing, tolerance)) for edge in ambiguous_edges])
        raise _VoronoiRetry('classification_ambiguous',
                            "The Voronoi border classification is ambiguous.", owners=ambiguous_owners, region=region)
    if not shared:
        raise _voronoi_failure(
            'separation_unresolved',
            "No border between two copper bodies is found inside the boundary. Check that the bodies are distinct "
            "and that the boundary contains them.",
            owners=range(len(owners)), sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)
    shared = [line for line in shared if line.length > tolerance]

    lines = unary_union(shared).intersection(free)
    paths = [line for line in _voronoi_lines(lines) if line.length > tolerance]
    if simplify_tol and simplify_tol > 0:
        paths = [line.simplify(simplify_tol) for line in paths if not line.is_empty]
        paths = [line for line in paths if line.length > tolerance]
    if complete_islands:
        # The enclosure is a real cut edge, not a substitute for one. Node it with all shared borders before rounding.
        # Don't simplify the compensated perimeter: that would consume its reserved copper clearance.
        paths = _voronoi_lines(unary_union([unary_union(paths).intersection(enclosure), enclosure.boundary]))
    edge_union = unary_union(paths)
    paths = _voronoi_lines(edge_union)
    if not paths:
        raise _voronoi_failure(
            'separation_unresolved',
            "No inter-body path fits inside the machining boundary and outside the copper: the gap is narrower "
            "than the tool diameter or the boundary cuts every border.",
            sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)
    if not boundary.covers(edge_union) or edge_union.distance(boundary.boundary) < radius - tolerance:
        raise _voronoi_failure(
            'boundary_clearance',
            "The cutter sweep would leave the machining boundary before output rounding.",
            region=_closest_region(edge_union, boundary.boundary, radius),
            min_clearance=edge_union.distance(boundary.boundary), sites=len(sites), spacing=spacing,
            tooldia=tooldia, units=units)
    if edge_union.distance(copper) < radius - tolerance:
        raise _voronoi_failure(
            'copper_clearance',
            "The cutter sweep would cut copper before output rounding.",
            region=_closest_region(edge_union, copper, radius),
            min_clearance=edge_union.distance(copper), sites=len(sites), spacing=spacing, tooldia=tooldia,
            units=units)
    if coord_decimals is not None:
        paths = [line for line in (_quantize_line(path, coord_decimals) for path in paths) if line is not None]
        if not paths:
            raise _voronoi_failure(
                'separation_unresolved',
                "The validated Voronoi paths collapse at the output coordinate precision. "
                "Increase the coordinate decimals.",
                sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)
        # Snap-node perimeter junctions on the output grid before defining the graph's exact-once edges.
        edge_union = shapely.union_all(paths, grid_size=10.0 ** (-coord_decimals)) if complete_islands else unary_union(paths)
        paths = _voronoi_lines(edge_union)
        if not paths:
            raise _voronoi_failure(
                'separation_unresolved',
                "No inter-body path is left after rounding to the output coordinate precision.",
                sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)
    edge_count = len(paths)
    # Only roundoff endpoints are merged: the tolerance is the same roundoff bound used by the clearance checks.
    paths = join_trails(paths, tolerance, abort=abort)
    if coord_decimals is not None:
        # Noding can introduce off-grid intersections; the checks must see the coordinates actually emitted.
        paths = [line for line in (_quantize_line(path, coord_decimals) for path in paths) if line is not None]

    if abort is not None and abort():
        raise VoronoiAborted("Voronoi isolation aborted.")
    # The joined trails must cover every validated cut edge exactly once: no connector and no retraced length.
    joined = unary_union(paths)
    coverage_tolerance = tolerance + 4 * tolerance * edge_count
    if abs(sum(path.length for path in paths) - edge_union.length) > coverage_tolerance or \
            abs(joined.length - edge_union.length) > coverage_tolerance or \
            joined.symmetric_difference(edge_union).length > coverage_tolerance:
        raise _voronoi_failure(
            'separation_unresolved',
            "The Voronoi trail graph does not cover the validated cut edges exactly. "
            "Increase the coordinate decimals and regenerate.",
            sites=len(sites), spacing=spacing, tooldia=tooldia, units=units)
    centerline = joined
    # Polygonal round buffers underapproximate a disk; distances certify the full circular cutter instead.
    numerical_tolerance = tolerance
    if not boundary.covers(centerline) or centerline.distance(boundary.boundary) < radius - numerical_tolerance:
        raise _voronoi_failure(
            'boundary_clearance',
            "The cutter sweep would leave the machining boundary. Increase the coordinate decimals and regenerate; "
            "the output precision must preserve full cutter clearance.",
            region=_closest_region(centerline, boundary.boundary, radius),
            min_clearance=centerline.distance(boundary.boundary), sites=len(sites), spacing=spacing,
            tooldia=tooldia, units=units)
    if centerline.distance(copper) < radius - numerical_tolerance:
        raise _voronoi_failure(
            'copper_clearance',
            "The cutter sweep would cut copper. Increase the coordinate decimals or reduce the Voronoi spacing.",
            region=_closest_region(centerline, copper, radius),
            min_clearance=centerline.distance(copper), sites=len(sites), spacing=spacing, tooldia=tooldia,
            units=units)

    sweeps = []
    for path_nr, line in enumerate(paths):
        if abort is not None and path_nr % 256 == 0 and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        sweeps.append(line.buffer(radius, quad_segs=64, cap_style='round', join_style='round'))
        # Orient an inscribed end disk at a real tangency; every vertex stays at the actual cutter radius.
        for x, y in (line.coords[0], line.coords[-1]):
            contact = shapely.shortest_line(Point(x, y), boundary.boundary)
            if abs(contact.length - radius) <= tolerance:
                bx, by = contact.coords[-1]
                angle = math.atan2(by - y, bx - x)
                sweeps.append(Polygon([(x + radius * math.cos(angle + i * math.pi / 128),
                                        y + radius * math.sin(angle + i * math.pi / 128)) for i in range(256)]))
    sweep = unary_union(sweeps)
    remaining = boundary.difference(sweep.buffer(tolerance)).buffer(0)
    owner_tree = shapely.STRtree(owners)
    for component_nr, component in enumerate(_voronoi_polygons(remaining)):
        if abort is not None and component_nr % 256 == 0 and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        touched = set(owner_tree.query(component, predicate='intersects').tolist())
        if complete_islands and touched and component.intersects(boundary.boundary):
            raise _VoronoiRetry(
                'separation_unresolved', "A copper island is still connected to the outside material.",
                owners=sorted(touched), region=component)
        if len(touched) > 1:
            # A remaining connection is not automatically "the tool is too big": the nearest owner distance is reported,
            # and local refinement gets its three bounded chances before the run fails.
            min_clearance = None
            touched = sorted(touched)
            for left in range(len(touched)):
                for right in range(left + 1, len(touched)):
                    gap = owners[touched[left]].distance(owners[touched[right]])
                    min_clearance = gap if min_clearance is None else min(min_clearance, gap)
            raise _VoronoiRetry(
                'separation_unresolved',
                "The cutter sweep does not fully separate the copper bodies: the remaining material still connects "
                "them. Reduce the Voronoi spacing or use a smaller tool.",
                owners=touched, region=component, min_clearance=min_clearance)
    if abort is not None and abort():
        raise VoronoiAborted("Voronoi isolation aborted.")
    return paths


def voronoi_pad_isolation(paths, source, boundary, tooldia, coord_decimals=None, abort=None, isolation_area=None):
    """Add partial exterior halos of surviving dark flashes to certified Voronoi paths.

    Original copper is authoritative, including traces, pours and composed clear polarity. A Point follow is flash
    provenance, not proof of a component pad. Region-only and negative-image data cannot identify dark flashed pads.
    No holes, spokes or original copper connections are cut. The base trails are retained without renoding or rounding;
    only the added halos are grid-noded and joined. CNC optimization may reorder the resulting combined paths.
    Returns (paths, counts of unique surviving flashes / flashes with added cuts / skipped flashes).
    """
    copper = _voronoi_union(source.solid_geometry)
    boundary = _voronoi_union(boundary)
    isolation_area = boundary if isolation_area is None else _voronoi_union(isolation_area)
    if isolation_area is None or isolation_area.is_empty or not isolation_area.is_valid or \
            not boundary.covers(isolation_area) or not isolation_area.covers(copper):
        raise VoronoiIsolationError("The intended island enclosure must contain all copper and fit in the boundary.")
    radius = float(tooldia) / 2.0
    if not math.isfinite(radius) or radius <= 0:
        raise VoronoiIsolationError("Pad isolation needs a positive, finite effective tool diameter.")
    grid = 0.0 if coord_decimals is None else 10.0 ** (-int(coord_decimals))
    scale = max(tooldia, *(abs(value) for value in boundary.bounds))
    tolerance = min(tooldia * 1e-10, 64 * math.ulp(scale))
    reserve = math.sqrt(0.5) * grid + tolerance * 16
    pads = {}
    # IPNEG reverses the meaning of the stored dark flashes; do not infer pads from the inverted image.
    if getattr(source, 'image_domain', None) is None:
        for aperture in source.tools.values():
            for record in aperture.get('geometry', []):
                if abort is not None and abort():
                    raise VoronoiAborted("Voronoi isolation aborted.")
                solid = record.get('solid')
                if not isinstance(record.get('follow'), Point) or solid is None or solid.is_empty:
                    continue
                surviving = _voronoi_union(solid).intersection(copper)
                if surviving.area > tolerance * tolerance:
                    pads.setdefault(surviving.normalize().wkb, surviving)
    report = {'candidates': len(pads), 'cut': 0, 'skipped': len(pads)}
    if not pads:
        return list(paths), report

    # Circumscribed buffers reserve full disk clearance plus both clipping and output-grid displacements.
    exclusion = (radius + 2 * reserve) / math.cos(math.pi / 64)
    halo_radius = (radius + 4 * reserve) / math.cos(math.pi / 64)
    free = boundary.buffer(-(radius + 2 * reserve)).difference(copper.buffer(exclusion))
    base = unary_union(paths)
    if not base.is_empty:
        # Avoid retracing a base cut; the tiny interruptions remain covered by its unchanged cutter sweep.
        free = free.difference(base.buffer(4 * reserve))
    additions = []
    for pad in pads.values():
        if abort is not None and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        outlines = [Polygon(poly.exterior).buffer(halo_radius).exterior for poly in _voronoi_polygons(pad)]
        halo = unary_union(outlines).intersection(free)
        lines = _voronoi_lines(halo)
        if grid:
            lines = [line for line in (_quantize_line(line, coord_decimals) for line in lines) if line is not None]
        lines = [line for line in lines if line.length > tolerance]
        if lines:
            additions.extend(lines)
            report['cut'] += 1
    report['skipped'] -= report['cut']
    if not additions:
        return list(paths), report

    added = shapely.union_all(additions, grid_size=grid) if grid else unary_union(additions)
    extra_paths = join_trails(_voronoi_lines(added), 0.0, abort=abort)
    result = list(paths) + extra_paths
    final = unary_union(result)
    if not boundary.covers(final) or final.distance(boundary.boundary) < radius - tolerance:
        raise VoronoiIsolationError("The final pad isolation cutter sweep would leave the machining boundary.")
    if final.distance(copper) < radius - tolerance:
        raise VoronoiIsolationError("The final pad isolation cutter sweep would cut original copper.")
    if abs(sum(line.length for line in result) - final.length) > tolerance * max(1, len(result)):
        raise VoronoiIsolationError("The final pad isolation paths would retrace a cut segment.")

    sweeps = []
    for line in result:
        if abort is not None and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        sweeps.append(line.buffer(radius, quad_segs=64))
    remaining = boundary.difference(unary_union(sweeps).buffer(tolerance)).buffer(0)
    owner_tree = shapely.STRtree(_voronoi_polygons(copper))
    for component in _voronoi_polygons(remaining):
        if abort is not None and abort():
            raise VoronoiAborted("Voronoi isolation aborted.")
        touched = owner_tree.query(component, predicate='intersects')
        if not isolation_area.equals(boundary) and len(touched) and component.intersects(boundary.boundary):
            raise VoronoiIsolationError("The final pad isolation sweep leaves a copper island connected outside.")
        if len(touched) > 1:
            raise VoronoiIsolationError("The final pad isolation sweep does not separate the original copper bodies.")
    return result, report
