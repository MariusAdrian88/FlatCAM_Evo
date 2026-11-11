"""Cutout contours, bridge intervals and geometry operations, independent of the GUI."""

import builtins
import gettext
import logging
import math
from collections import namedtuple

import shapely
from shapely import Polygon, MultiPolygon, GeometryCollection, box, Point, LineString, MultiLineString, LinearRing
from shapely.ops import unary_union, linemerge, polygonize_full, substring

from camlib import flatten_shapely_geometry, deepcopy_keep_geometry
from appParsers.feature_access import fused_solid
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')
Contour = namedtuple('Contour', ['ring', 'depth', 'side', 'index', 'width'], defaults=(0.0,))
_OPEN_PATH = "open path ignored"


def gerber_cutout_geometry(cutout_obj, convex_box, fused_solid_fn=fused_solid):
    """Return composed Gerber copper, or its convex hull, without changing the source."""
    copper = fused_solid_fn(cutout_obj)
    try:
        return copper.convex_hull if convex_box else copper
    except Exception as err:
        log.error("CutOut.on_freeform_cutout().geo_init() --> %s" % str(err))
        return copper


class CutoutResult:
    """Computed paths, mouse bites and user-facing diagnostics."""

    def __init__(self):
        self.solid_geo = []
        self.gaps_solid_geo = []
        self.holes = []
        self.warnings = []
        self.errors = []
        self.failed = False


def _valid_mouse_bite_step(mb_dia, mb_spacing):
    try:
        mb_dia = float(mb_dia)
        step = mb_dia + float(mb_spacing)
    except (TypeError, ValueError):
        return False
    return math.isfinite(mb_dia) and mb_dia > 0 and math.isfinite(step) and step > 0


def _validate_thin_depth(gap_type, gaps, cutz, thin_depth):
    """Validate Thin-gap depths at the scheduling/mutation boundary."""
    if gap_type != 1 or str(gaps).lower() == 'none':
        return
    try:
        cutz = float(cutz)
        thin_depth = float(thin_depth)
    except (TypeError, ValueError):
        raise ValueError(_("Cut Z and Thin Depth must be finite values."))
    if not math.isfinite(cutz) or cutz >= 0:
        raise ValueError(_("Cut Z must be a finite negative value for Thin gaps."))
    if thin_depth == 0:
        return
    if not math.isfinite(thin_depth) or not (cutz < thin_depth < 0):
        raise ValueError(_("Thin Depth must be zero or satisfy Cut Z < Thin Depth < 0."))


def _sample_path(path, step):
    points = []
    length = path.length
    distance = 0.0
    while distance < length:
        points.append(path.interpolate(distance))
        distance += step
    if not path.is_closed:
        points.append(path.interpolate(length))
    return points


def _cutout_paths(geometry):
    """Return every linear contour in nested cutout geometry."""
    if geometry is None:
        return []
    if isinstance(geometry, (LineString, LinearRing)):
        return [geometry]
    if isinstance(geometry, Polygon):
        return [geometry.exterior, *geometry.interiors]
    if isinstance(geometry, (MultiPolygon, MultiLineString, GeometryCollection)):
        paths = []
        for part in geometry.geoms:
            paths.extend(_cutout_paths(part))
        return paths
    if isinstance(geometry, (list, tuple)):
        paths = []
        for part in geometry:
            paths.extend(_cutout_paths(part))
        return paths
    try:
        paths = []
        for part in geometry:
            paths.extend(_cutout_paths(part))
        return paths
    except TypeError:
        return []


def _cutout_anchor_points(path, gaps):
    mode = str(gaps).lower()
    if mode == 'none':
        return []
    if mode not in {'lr', 'tb', '2lr', '2tb', '4', '8'}:
        raise ValueError(_("Gaps value is not supported."))
    xmin, ymin, xmax, ymax = path.bounds
    xmid = (xmin + xmax) / 2.0
    ymid = (ymin + ymax) / 2.0
    xquarter = (xmin + xmid) / 2.0
    xthreequarter = (xmid + xmax) / 2.0
    yquarter = (ymin + ymid) / 2.0
    ythreequarter = (ymid + ymax) / 2.0
    lr = [(xmin, ymid), (xmax, ymid)]
    tb = [(xmid, ymin), (xmid, ymax)]
    two_lr = [(xmin, yquarter), (xmax, yquarter),
              (xmin, ythreequarter), (xmax, ythreequarter)]
    two_tb = [(xquarter, ymin), (xquarter, ymax),
              (xthreequarter, ymin), (xthreequarter, ymax)]
    if mode == 'lr':
        return lr
    if mode == 'tb':
        return tb
    if mode == '2lr':
        return two_lr
    if mode == '2tb':
        return two_tb
    return two_lr + two_tb if mode == '8' else lr + tb


def _cutout_substring(path, start, end):
    part = substring(path, start, end)
    return None if part.is_empty or part.length <= 1e-9 else part


def _cutout_intervals(path, cut_diameter, gaps, gap_length):
    """Split a contour into cutter paths and exact arclength gap paths."""
    cut_diameter = abs(float(cut_diameter))
    gap_length = float(gap_length)
    cut_diameter = abs(cut_diameter)
    if not math.isfinite(cut_diameter) or cut_diameter == 0:
        raise ValueError(_("Tool diameter must be a positive finite number."))
    if not math.isfinite(gap_length) or gap_length <= 0:
        raise ValueError(_("Gap width must be a positive finite number."))
    anchors = _cutout_anchor_points(path, gaps)
    if not anchors:
        return [path], []
    length = float(path.length)
    if length <= 1e-9 or len(anchors) * gap_length >= length - 1e-9:
        raise ValueError(_("Requested gaps do not fit on the selected contour."))
    closed = bool(path.is_closed)
    intervals = []
    centers = []
    for anchor in anchors:
        center = float(path.project(Point(anchor)))
        centers.append(center)
        start = center - gap_length / 2.0
        end = center + gap_length / 2.0
        if not closed:
            if start < -1e-9 or end > length + 1e-9:
                raise ValueError(_("Requested gap does not fit on the selected open contour."))
            intervals.append([(max(0.0, start), min(length, end))])
            continue
        start %= length
        end = start + gap_length
        if end <= length + 1e-9:
            intervals.append([(start, min(length, end))])
        else:
            intervals.append([(start, length), (0.0, end - length)])
    tolerance = 1e-9
    for index, center in enumerate(centers):
        for previous in centers[:index]:
            distance = abs(center - previous)
            if closed:
                distance = min(distance, length - distance)
            if distance <= gap_length + tolerance:
                raise ValueError(_("Requested gaps overlap or touch on the selected contour."))
    for index, current in enumerate(intervals):
        for previous in intervals[:index]:
            for current_start, current_end in current:
                for previous_start, previous_end in previous:
                    if min(current_end, previous_end) - max(current_start, previous_start) >= -tolerance:
                        raise ValueError(_("Requested gaps overlap or touch on the selected contour."))
    ranges = sorted(part for interval in intervals for part in interval)
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + tolerance:
            raise ValueError(_("Requested gaps overlap or touch on the selected contour."))
        merged.append((start, end))
    gap_parts = []
    for interval in intervals:
        parts = [_cutout_substring(path, start, end) for start, end in interval]
        gap_parts.extend(part for part in parts if part is not None)
    cut_ranges = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor + tolerance:
            cut_ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length - tolerance:
        cut_ranges.append((cursor, length))
    cut_parts = []
    for start, end in cut_ranges:
        part = _cutout_substring(path, start, end)
        if part is not None:
            cut_parts.append(part)
    if not cut_parts:
        raise ValueError(_("Requested gaps would remove the complete contour."))
    return cut_parts, gap_parts


def _manual_gap_geometry(geometry, position, cut_diameter, gap_width):
    """Return the selected contour, its gap interval, and cutter footprint."""
    paths = _cutout_paths(geometry)
    if not paths:
        raise ValueError(_("No contour is available for a manual gap."))
    try:
        cut_diameter = float(cut_diameter)
        gap_width = float(gap_width)
    except (TypeError, ValueError):
        raise ValueError(_("Tool diameter and gap width must be finite values."))
    cut_diameter = abs(cut_diameter)
    if not math.isfinite(cut_diameter) or cut_diameter == 0:
        raise ValueError(_("Tool diameter must be a positive finite number."))
    if not math.isfinite(gap_width) or gap_width < 0:
        raise ValueError(_("Gap width must be a non-negative finite number."))
    point = Point(position)
    selected = min(paths, key=point.distance)
    center = float(selected.project(point))
    gap_length = gap_width + cut_diameter
    if not math.isfinite(gap_length) or gap_length <= 0 or gap_length >= selected.length - 1e-9:
        raise ValueError(_("Requested manual gap does not fit on the selected contour."))
    start = center - gap_length / 2.0
    if selected.is_closed:
        if start < 0:
            parts = [_cutout_substring(selected, start + selected.length, selected.length),
                     _cutout_substring(selected, 0.0, center + gap_length / 2.0)]
        elif start + gap_length > selected.length:
            parts = [_cutout_substring(selected, start, selected.length),
                     _cutout_substring(selected, 0.0, start + gap_length - selected.length)]
        else:
            parts = [_cutout_substring(selected, start, start + gap_length)]
        parts = [part for part in parts if part is not None]
        gap_line = unary_union(parts)
    else:
        if start < -1e-9 or start + gap_length > selected.length + 1e-9:
            raise ValueError(_("Requested manual gap does not fit on the selected contour."))
        gap_line = _cutout_substring(selected, max(0.0, start), min(selected.length, start + gap_length))
    if gap_line is None or gap_line.is_empty:
        raise ValueError(_("Requested manual gap does not fit on the selected contour."))
    return selected, gap_line, gap_line.buffer(cut_diameter / 2.0, cap_style='flat')


def _manual_material_side(geometry, path):
    """Return -1 for a contour enclosing material and 1 for a hole boundary."""
    probe = path.interpolate(0.5, normalized=True)
    depth = 0
    for other in _cutout_paths(geometry):
        if not other.is_closed or other.equals(path):
            continue
        if Polygon(other).contains(probe):
            depth += 1
    return -1.0 if depth % 2 == 0 else 1.0


def _manual_subtract_selected(geometry, selected, gap_line, expected_gap_length=None):
    def complement(path):
        ranges = []
        for part in _cutout_paths(gap_line):
            if part.is_empty or part.length <= 1e-9:
                continue
            start = float(path.project(Point(part.coords[0])))
            end = float(path.project(Point(part.coords[-1])))
            if path.is_closed and end < start - 1e-9:
                ranges.append((start, path.length))
                ranges.append((0.0, end))
            else:
                ranges.append((start, min(end, path.length)))
        if not ranges:
            raise ValueError(_("Could not map the manual gap to the selected contour."))
        ranges.sort()
        merged = []
        for start, end in ranges:
            if merged and start <= merged[-1][1] + 1e-9:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        parts = []
        cursor = 0.0
        for start, end in merged:
            if start > cursor + 1e-9:
                part = _cutout_substring(path, cursor, start)
                if part is not None:
                    parts.append(part)
            cursor = max(cursor, end)
        if cursor < path.length - 1e-9:
            part = _cutout_substring(path, cursor, path.length)
            if part is not None:
                parts.append(part)
        if expected_gap_length is not None:
            try:
                expected = float(expected_gap_length)
            except (TypeError, ValueError):
                raise ValueError(_("Manual gap length must be a finite value."))
            removed = sum(end - start for start, end in merged)
            if not math.isfinite(expected) or abs(removed - expected) > 1e-6:
                raise ValueError(_("Could not map the manual gap to the selected contour."))
        return parts

    paths = _cutout_paths(geometry)
    result = []
    selected_used = False
    for path in paths:
        if not selected_used and path.equals(selected):
            result.extend(complement(path))
            selected_used = True
        else:
            result.append(path)
    if not selected_used:
        raise ValueError(_("Could not find the selected contour for the manual gap."))
    return unary_union(result)


def freeform_cutout_paths(cutout_obj, kind, cut_dia, margin, gaps, gapsize, convex_box, gap_type, thin_entry,
                          mb_dia, mb_spacing, handler, inner_active=False, inner_gaps=False,
                          fused_solid_fn=fused_solid):
    """Compute cut paths, thin gaps and mouse bites from source data and explicit parameters."""
    result = CutoutResult()
    has_mouse_bites = gap_type == 2
    if has_mouse_bites and not _valid_mouse_bite_step(mb_dia, mb_spacing):
        result.errors.append(_("Mouse bite diameter and spacing must produce a positive finite step."))
        result.failed = True
        return result
    if inner_active:
        return _inner_contour_paths(cutout_obj, kind, cut_dia, margin, gaps, gapsize, convex_box, gap_type, thin_entry,
                                    mb_dia, mb_spacing, handler, inner_gaps, fused_solid_fn)
    solid_geo = []
    gaps_solid_geo = []
    mouse_bites_geo = []
    holes = []
    geometry_rows = []
    gapsize = gapsize + abs(cut_dia)
    mb_buff_val = mb_dia / 2.0
    if cutout_obj.kind == 'gerber':
        object_geo = gerber_cutout_geometry(cutout_obj, convex_box, fused_solid_fn)
    else:
        if cutout_obj.multigeo is False:
            object_geo = cutout_obj.solid_geometry
        else:
            t_first = list(cutout_obj.tools.keys())[0]
            object_geo = cutout_obj.tools[t_first]['solid_geometry']
    if kind == 'single':
        object_geo = unary_union(object_geo)
        if cutout_obj.kind == 'gerber':
            if isinstance(object_geo, MultiPolygon):
                x0, y0, x1, y1 = object_geo.bounds
                object_geo = box(x0, y0, x1, y1)
            if margin >= 0:
                geo_buf = object_geo.buffer(margin + abs(cut_dia / 2))
                geo = geo_buf.exterior
            else:
                geo_buf = object_geo.buffer(-margin + abs(cut_dia / 2))
                geo = unary_union(geo_buf.interiors)
        else:
            if isinstance(object_geo, (MultiPolygon, MultiLineString)):
                x0, y0, x1, y1 = object_geo.bounds
                object_geo = box(x0, y0, x1, y1)
            if isinstance(object_geo, (LinearRing, LineString)):
                object_geo = Polygon(object_geo)
            if margin >= 0:
                geo_buf = object_geo.buffer(margin)
                if geo_buf.is_empty:
                    geo_buf = object_geo.buffer(margin + 0.0000001)
                geo = geo_buf.exterior
            else:
                geo_buf = object_geo.buffer(0.0000001)
                geo_ext = geo_buf.exterior
                buff_geo_ext = geo_ext.buffer(-margin)
                geo = unary_union(buff_geo_ext.interiors)
        if geo.is_empty:
            log.debug("Cutout.on_freeform_cutout() -> Empty geometry.")
            result.failed = True
            return result
        try:
            solid_geo, rest_geo = handler(geo, abs(cut_dia), gaps, gapsize, margin)
        except Exception as err:
            log.error("Cutout.on_freeform_cutout() -> %s" % str(err))
            result.errors.append(_("Cutout gaps failed: %s") % str(err))
            result.failed = True
            return result
        if gap_type == 1 and thin_entry != 0:
            gaps_solid_geo = rest_geo
    else:
        object_geo = flatten_shapely_geometry(object_geo)
        for geom_struct in object_geo:
            if cutout_obj.kind == 'gerber':
                if margin >= 0:
                    geom_struct = (geom_struct.buffer(margin + abs(cut_dia / 2))).exterior
                else:
                    geom_struct_buff = geom_struct.buffer(-margin + abs(cut_dia / 2))
                    geom_struct = geom_struct_buff.interiors
            else:
                if margin >= 0:
                    geo_buf = geom_struct.buffer(margin)
                    if geo_buf.is_empty:
                        geo_buf = geom_struct.buffer(margin + 0.0000001)
                    geom_struct = geo_buf.exterior
                else:
                    geo_buf = geom_struct.buffer(0.0000001)
                    geo_ext = geo_buf.exterior
                    buff_geo_ext = geo_ext.buffer(-margin)
                    if isinstance(buff_geo_ext, MultiPolygon):
                        log.debug("Cutout.on_freeform_cutout() -> The source geometry cannot be used.")
                        result.errors.append(_("Failed."))
                        unary_union(buff_geo_ext)
                    else:
                        geom_struct = unary_union(buff_geo_ext.interiors)
            if cutout_obj.kind == 'geometry':
                geometry_rows.append(geom_struct)
            try:
                c_geo, r_geo = handler(geom_struct, abs(cut_dia), gaps, gapsize, margin)
            except Exception as err:
                log.error("Cutout.on_freeform_cutout() -> %s" % str(err))
                result.errors.append(_("Cutout gaps failed: %s") % str(err))
                result.failed = True
                continue
            solid_geo += c_geo
            if gap_type == 1 and thin_entry != 0:
                gaps_solid_geo += r_geo
    if not solid_geo:
        log.debug("Cutout.on_freeform_cutout() -> Empty solid geometry.")
        result.failed = True
        return result
    try:
        solid_geo = linemerge(solid_geo)
    except Exception:
        pass
    if has_mouse_bites is True:
        gapsize -= abs(cut_dia) / 2
        mb_object_geo = deepcopy_keep_geometry(object_geo)
        if kind == 'single':
            mb_object_geo = unary_union(mb_object_geo)
            if cutout_obj.kind == 'gerber':
                if isinstance(mb_object_geo, MultiPolygon):
                    x0, y0, x1, y1 = mb_object_geo.bounds
                    mb_object_geo = box(x0, y0, x1, y1)
                if margin >= 0:
                    geo_buf = mb_object_geo.buffer(margin + mb_buff_val)
                    mb_geo = geo_buf.exterior
                else:
                    geo_buf = mb_object_geo.buffer(-margin + mb_buff_val)
                    mb_geo = unary_union(geo_buf.interiors)
            else:
                if isinstance(mb_object_geo, MultiPolygon):
                    x0, y0, x1, y1 = mb_object_geo.bounds
                    mb_object_geo = box(x0, y0, x1, y1)
                mb_geo = geo
            try:
                __, rest_geo = handler(mb_geo, abs(cut_dia), gaps, gapsize, margin)
            except Exception as err:
                result.errors.append(_("Mouse bite gaps failed: %s") % str(err))
                result.failed = True
                return result
            mouse_bites_geo = rest_geo
        else:
            mb_object_geo = flatten_shapely_geometry(mb_object_geo)
            if cutout_obj.kind == 'geometry':
                mb_object_geo = geometry_rows
            for mb_geom_struct in mb_object_geo:
                if cutout_obj.kind == 'gerber':
                    if margin >= 0:
                        mb_geom_struct = mb_geom_struct.buffer(margin + mb_buff_val)
                        mb_geom_struct = mb_geom_struct.exterior
                    else:
                        mb_geom_struct = mb_geom_struct.buffer(-margin + mb_buff_val)
                        mb_geom_struct = mb_geom_struct.interiors
                try:
                    __, mb_r_geo = handler(mb_geom_struct, abs(cut_dia), gaps, gapsize, margin)
                except Exception as err:
                    result.errors.append(_("Mouse bite gaps failed: %s") % str(err))
                    result.failed = True
                    return result
                mouse_bites_geo += mb_r_geo
        holes = mouse_bite_holes(mouse_bites_geo, mb_dia, mb_spacing)
    result.solid_geo = solid_geo
    result.gaps_solid_geo = gaps_solid_geo
    result.holes = holes
    return result


def _record_width(tool):
    try:
        return float(tool.get('size') or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _closed_loops(lines, width, warnings):
    """Filled closed center-line loops; report ignored open paths."""
    if not lines:
        return []
    snapped = [shapely.set_precision(line, 1e-6) for line in lines]
    noded = unary_union(snapped)
    polygons, __, dangles, __ = polygonize_full(noded)
    if warnings is not None and (not dangles.is_empty):
        warnings.append(_OPEN_PATH)
    loops = []
    seen = []
    for poly in flatten_shapely_geometry(polygons):
        if not isinstance(poly, Polygon) or poly.is_empty:
            continue
        shell = Polygon(poly.exterior)
        if any(shell.equals(other) for other in seen):
            continue
        seen.append(shell)
        loops.append((shell, width))
    return loops


def _gerber_items(obj, warnings, fused_solid_fn=fused_solid):
    """Filled loops and stroke widths from Gerber center lines and region records."""
    groups = {}
    region_steps = []
    has_records = False
    no_line_apertures = set()
    for aperture, tool in obj.tools.items():
        width = _record_width(tool)
        is_region = aperture == 0 or tool.get('type') == 'REG'
        for rec in tool.get('geometry', []):
            if not rec:
                continue
            has_records = True
            if is_region:
                if rec.get('solid') is not None:
                    region_steps.append(('dark', flatten_shapely_geometry(rec['solid'])))
                if rec.get('clear') is not None:
                    region_steps.append(('clear', flatten_shapely_geometry(rec['clear'])))
            else:
                follow = rec.get('follow')
                if not isinstance(follow, (LineString, LinearRing)) or follow.is_empty:
                    no_line_apertures.add(aperture)
                    continue
                if 'solid' not in rec:
                    continue
                groups.setdefault(width, []).append(LineString(follow.coords))
    if no_line_apertures and warnings is not None:
        warnings.append(_("Apertures without a center line were not used as contours: %s. "
                          "Draw the outline with a round aperture.") %
                        ", ".join(str(ap) for ap in sorted(no_line_apertures, key=str)))
    items = []
    for width, lines in groups.items():
        items += _closed_loops(lines, width, warnings)
    region = Polygon()
    for polarity, geos in region_steps:
        if polarity == 'dark':
            region = unary_union([region] + list(geos))
        elif not region.is_empty:
            region = region.difference(unary_union(geos))
    for poly in flatten_shapely_geometry(region):
        if isinstance(poly, Polygon) and not poly.is_empty:
            items.append((poly, 0.0))
    if not items and not has_records:
        for poly in flatten_shapely_geometry(fused_solid_fn(obj)):
            if isinstance(poly, Polygon) and not poly.is_empty:
                items.append((poly, 0.0))
    return items


def _geometry_items(source_geo, warnings):
    items = []
    lines = []
    for geo in flatten_shapely_geometry(source_geo):
        if isinstance(geo, Polygon):
            if not geo.is_empty:
                items.append((geo, 0.0))
        elif isinstance(geo, LinearRing):
            items.append((Polygon(geo), 0.0))
        elif isinstance(geo, LineString) and not geo.is_empty:
            lines.append(geo)
    if lines:
        merged = linemerge(lines)
        for line in flatten_shapely_geometry(merged):
            if line.is_closed and len(line.coords) >= 4:
                items.append((Polygon(line), 0.0))
            elif warnings is not None:
                warnings.append(_OPEN_PATH)
    return items


def _rings_of(area):
    rings = []
    for part in flatten_shapely_geometry(area):
        if not isinstance(part, Polygon) or part.is_empty:
            continue
        rings.append((Polygon(part.exterior), 'outer'))
        for interior in part.interiors:
            rings.append((Polygon(interior), 'inner'))
    return rings


def outline_contours(source_geo, convex_box, kind, warnings=None, fused_solid_fn=fused_solid):
    """Return even-odd outline contours, deepest first, retaining stroke widths."""
    if getattr(source_geo, 'kind', None) == 'gerber':
        items = _gerber_items(source_geo, warnings, fused_solid_fn)
    else:
        items = _geometry_items(source_geo, warnings)
    area = Polygon()
    for poly, __ in items:
        if not poly.is_valid:
            poly = poly.buffer(0)
        area = area.symmetric_difference(poly)
    candidates = []
    for poly, width in items:
        candidates.append((poly.exterior, width))
        candidates += [(interior, 0.0) for interior in poly.interiors if width == 0.0]
    rings = _rings_of(area)
    contours = []
    for index, (ring, side) in enumerate(rings):
        probe = ring.exterior.interpolate(0.5, normalized=True)
        depth = 0
        for other_index, (other, __) in enumerate(rings):
            if other_index != index and other.contains(probe) and other.exterior.distance(probe) > 1e-9:
                depth += 1
        width = 0.0
        best = None
        for line, line_width in candidates:
            dist = shapely.hausdorff_distance(ring.exterior, line)
            if best is None or dist < best:
                best, width = dist, line_width
        if best is None or best > 1e-6:
            width = 0.0
        contours.append(Contour(ring, depth, side, index, width))
    top = [c for c in contours if c.depth == 0]
    groups = []
    if top and convex_box:
        if kind == 'single':
            groups = [(unary_union([c.ring for c in top]).convex_hull, top)]
        else:
            groups = [(c.ring.convex_hull, [c]) for c in top]
    elif len(top) > 1 and kind == 'single':
        if warnings is not None:
            warnings.append(_("Single mode with several outlines: using one bounding box. "
                              "Use Panel to cut each board."))
        groups = [(box(*unary_union([c.ring for c in top]).bounds), top)]
    if groups:
        rest = [c for c in contours if c.depth != 0]
        replaced = [Contour(Polygon(shape.exterior), 0, 'outer', min(c.index for c in members),
                            max(c.width for c in members))
                    for shape, members in groups]
        contours = replaced + rest
    return sorted(contours, key=lambda c: (-c.depth, c.index))


def offset_contours(contours, margin, radius, source_kind):
    """Offset each contour away from material, compensating Gerber stroke and cutter widths."""
    out = []
    for contour in contours:
        e = (contour.width / 2.0 + radius) if source_kind == 'gerber' else 0.0
        dist = margin + e if margin >= 0 else margin - e
        shape = contour.ring.buffer(dist if contour.side == 'outer' else -dist)
        out.append([part.exterior for part in flatten_shapely_geometry(shape)
                    if isinstance(part, Polygon) and not part.is_empty])
    return out


def _inner_contour_paths(cutout_obj, kind, cut_dia, margin, gaps, gapsize, convex_box, gap_type, thin_entry,
                         mb_dia, mb_spacing, handler, inner_gaps, fused_solid_fn=fused_solid):
    result = CutoutResult()
    if gap_type == 2 and not _valid_mouse_bite_step(mb_dia, mb_spacing):
        result.errors.append(_("Mouse bite diameter and spacing must produce a positive finite step."))
        result.failed = True
        return result
    gerber = cutout_obj.kind == 'gerber'
    source_kind = 'gerber' if gerber else 'geometry'
    gapsize = gapsize + abs(cut_dia)
    if gerber:
        source = cutout_obj
    elif cutout_obj.multigeo is False:
        source = cutout_obj.solid_geometry
    else:
        source = cutout_obj.tools[list(cutout_obj.tools.keys())[0]]['solid_geometry']
    contours = outline_contours(source, bool(convex_box) and gerber, kind, result.warnings, fused_solid_fn)
    if not any(c.side == 'inner' for c in contours):
        result.warnings.append(_("No inner contour found. Is this a board outline file?"))
    paths = offset_contours(contours, margin, abs(cut_dia) / 2.0, source_kind)
    solid_geo = []
    gaps_solid_geo = []
    skipped = 0
    cut_with_gaps = []
    for contour, contour_paths in zip(contours, paths):
        with_gaps = contour.side == 'outer' or inner_gaps
        if not contour_paths:
            skipped += contour.side == 'inner'
            continue
        for path in contour_paths:
            if not with_gaps:
                solid_geo.append(path)
                continue
            try:
                c_geo, r_geo = handler(path, abs(cut_dia), gaps, gapsize, margin)
            except Exception as err:
                log.error("Cutout.on_freeform_cutout() -> %s" % str(err))
                result.errors.append(_("Cutout gaps failed: %s") % str(err))
                result.failed = True
                continue
            if contour.side == 'inner' and not c_geo:
                skipped += 1
                continue
            solid_geo += c_geo
            if gap_type == 1 and thin_entry != 0:
                gaps_solid_geo += r_geo
        if with_gaps:
            cut_with_gaps.append(contour)
    if skipped:
        result.warnings.append(
            _("{n} inner contours were skipped: the slot is narrower than the cutter plus margin.").format(n=skipped))
    if not solid_geo:
        log.debug("Cutout.on_freeform_cutout() -> Empty solid geometry.")
        result.failed = True
        return result
    try:
        solid_geo = linemerge(solid_geo)
    except Exception:
        pass
    holes = []
    if gap_type == 2:
        gapsize -= abs(cut_dia) / 2
        mouse_bites_geo = []
        mb_paths = offset_contours(cut_with_gaps, margin, mb_dia / 2.0, source_kind)
        for contour_paths in mb_paths:
            for path in contour_paths:
                try:
                    __, mb_r_geo = handler(path, abs(cut_dia), gaps, gapsize, margin)
                except Exception as err:
                    result.errors.append(_("Mouse bite gaps failed: %s") % str(err))
                    result.failed = True
                    return result
                mouse_bites_geo += mb_r_geo
        holes = mouse_bite_holes(mouse_bites_geo, mb_dia, mb_spacing)
    result.solid_geo = solid_geo
    result.gaps_solid_geo = gaps_solid_geo
    result.holes = holes
    return result


def mouse_bite_holes(paths, mb_dia, mb_spacing):
    holes = []
    for line in paths:
        calc_len = 0
        while calc_len <= line.length:
            holes.append(line.interpolate(calc_len))
            calc_len += mb_dia + mb_spacing
    return holes


def any_cutout_handler(geom, cut_diameter, gaps, gapsize, margin):
    paths = _cutout_paths(geom)
    if not paths:
        raise ValueError(_("No contour is available for automatic gaps."))
    proc_geometry = []
    rest_geometry = []
    for path in paths:
        cut_parts, gap_parts = _cutout_intervals(path, cut_diameter, gaps, gapsize)
        proc_geometry.extend(cut_parts)
        rest_geometry.extend(gap_parts)
    return proc_geometry, rest_geometry


def rect_cutout_handler(geom, cut_dia, gaps, gapsize, margin, xmin, ymin, xmax, ymax, include_gaps=False):
    paths = _cutout_paths(geom)
    if not paths:
        raise ValueError(_("No contour is available for rectangular gaps."))
    proc_geometry = []
    rest_geometry = []
    for path in paths:
        cut_parts, gap_parts = _cutout_intervals(path, cut_dia, gaps, gapsize)
        proc_geometry.extend(cut_parts)
        rest_geometry.extend(gap_parts)
    return (proc_geometry, rest_geometry) if include_gaps else proc_geometry


def flatten(geometry):
    """Expand nested polygons into their nonempty exterior and interior paths."""
    flat_geo = []
    work_geo = geometry.geoms if isinstance(geometry, (MultiPolygon, MultiLineString, GeometryCollection)) else geometry
    try:
        for geo in work_geo:
            if geo:
                flat_geo += flatten(geometry=geo)
    except TypeError:
        if isinstance(work_geo, Polygon) and not work_geo.is_empty:
            flat_geo.append(work_geo.exterior)
            flat_geo += flatten(geometry=work_geo.interiors)
        elif not work_geo.is_empty:
            flat_geo.append(work_geo)
    return flat_geo


def recursive_bounds(geometry):
    def bounds_rec(obj):
        try:
            minx = math.inf
            miny = math.inf
            maxx = -math.inf
            maxy = -math.inf
            work_geo = obj.geoms if isinstance(obj, (MultiPolygon, MultiLineString)) else obj
            for k in work_geo:
                if k.is_empty or not k.is_valid:
                    continue
                minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                minx = min(minx, minx_)
                miny = min(miny, miny_)
                maxx = max(maxx, maxx_)
                maxy = max(maxy, maxy_)
            return minx, miny, maxx, maxy
        except TypeError:
            if obj:
                return obj.bounds
    return bounds_rec(geometry)


def subtract_geo(target_geo, subtractor):
    if target_geo is None:
        target_geo = []
    flat_geometry = flatten(target_geo)
    log.debug("%d paths" % len(flat_geometry))
    toolgeo = unary_union(subtractor)
    diffs = []
    for target in flat_geometry:
        if isinstance(target, (LineString, LinearRing, MultiLineString)):
            d_geo = target.difference(toolgeo)
            if not d_geo.is_empty:
                diffs.append(d_geo)
        else:
            log.warning("Not implemented.")
    return unary_union(diffs)


def subtract_poly_from_geo(solid_geo, pts):
    x0 = pts[0]
    y0 = pts[1]
    x1 = pts[2]
    y1 = pts[3]
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    flat_geometry = flatten(geometry=solid_geo)
    log.debug("%d paths" % len(flat_geometry))
    polygon = Polygon(points)
    toolgeo = unary_union(polygon)
    diffs = []
    for target in flat_geometry:
        if type(target) == LineString or type(target) == LinearRing:
            diffs.append(target.difference(toolgeo))
        else:
            log.warning("Not implemented.")
    return unary_union(diffs)


def intersect_geo(target_geo, second_geo):
    results = []
    target_geo = flatten_shapely_geometry(target_geo)
    for geo in target_geo:
        if second_geo.intersects(geo):
            results.append(second_geo.intersection(geo))
    return flatten(results)


def drill_cut_geometry(solid_geometry, margin, pitch):
    drill_list = []
    cut_geo_solid = unary_union(solid_geometry)
    for geo in flatten_shapely_geometry(cut_geo_solid):
        if isinstance(geo, LineString):
            offset_geo = geo.parallel_offset(margin, side='left')
        elif isinstance(geo, Polygon):
            offset_geo = geo.buffer(margin)
        else:
            continue
        for path in flatten_shapely_geometry(offset_geo):
            if isinstance(path, Polygon):
                path = path.exterior
            if isinstance(path, LineString) and not path.is_empty:
                drill_list.extend(_sample_path(path, pitch))
    return drill_list


def manual_cutout_geometry(solid_geometry, convex_box, kind, dia, margin, shape_type):
    geo_union = unary_union(solid_geometry)
    if convex_box:
        geo = geo_union.convex_hull
        return geo.buffer(margin + abs(dia / 2))
    if kind == 'single':
        if isinstance(geo_union, Polygon) or \
                (isinstance(geo_union, list) and len(geo_union) == 1) or \
                (isinstance(geo_union, MultiPolygon) and len(geo_union.geoms) == 1):
            if dia >= 0:
                buff_geo = geo_union.buffer(margin + abs(dia / 2)).exterior
            else:
                buff_geo = geo_union.buffer(margin + abs(dia / 2)).interiors
                buff_geo = unary_union(buff_geo)
            return buff_geo if shape_type is False else buff_geo.envelope
        if isinstance(geo_union, MultiPolygon):
            if shape_type is False:
                return geo_union.buffer(margin + abs(dia / 2))
            x0, y0, x1, y1 = geo_union.bounds
            geo = box(x0, y0, x1, y1)
            return geo.buffer(margin + abs(dia / 2))
        raise ValueError('%s: %s' % (_("Geometry not supported"), type(geo_union)))
    geo = geo_union.buffer(margin + abs(dia / 2))
    if shape_type is False:
        if isinstance(geo, Polygon):
            return geo.exterior
        if isinstance(geo, MultiPolygon):
            return [poly.exterior for poly in geo.geoms]
    else:
        if isinstance(geo, Polygon):
            return geo.envelope.exterior
        if isinstance(geo, MultiPolygon):
            return [poly.envelope.exterior for poly in geo.geoms]


def rectangular_cutout_paths(object_geo, source_kind, kind, gaps, gapsize, gap_type, cut_dia, margin,
                             mb_dia, mb_spacing, thin_entry, handler):
    """Compute rectangular cutout paths from geometry, without scheduling or object mutation."""
    result = CutoutResult()
    has_mouse_bites = gap_type == 2
    solid_geo = []
    gaps_solid_geo = []
    mouse_bites_geo = []
    rectangular_rows = []

    def rectangular_paths(*args, include_gaps=False):
        try:
            if not include_gaps and gap_type == 1 and thin_entry != 0:
                cut_paths, gap_paths = handler(*args, include_gaps=True)
                gaps_solid_geo.extend(gap_paths)
                return cut_paths
            return handler(*args, include_gaps=include_gaps)
        except ValueError as err:
            result.errors.append(str(err))
            result.failed = True
            return None

    if has_mouse_bites and not _valid_mouse_bite_step(mb_dia, mb_spacing):
        result.errors.append(_("Mouse bite diameter and spacing must produce a positive finite step."))
        result.failed = True
        return result
    mb_buff_val = mb_dia / 2.0
    gapsize = gapsize + abs(cut_dia)
    if kind == 'single':
        object_geo = unary_union(object_geo)
        xmin, ymin, xmax, ymax = object_geo.bounds
        geo = box(xmin, ymin, xmax, ymax)
        if source_kind == 'gerber':
            work_margin = margin + abs(cut_dia / 2) if margin >= 0 else margin - abs(cut_dia / 2)
            geo = geo.buffer(work_margin)
        else:
            if margin >= 0:
                geo_buf = geo.buffer(margin)
                if geo_buf.is_empty:
                    geo_buf = geo.buffer(margin + 0.0000001)
                geo = geo_buf.exterior
            else:
                geo_buf = geo.buffer(0.0000001)
                geo_ext = geo_buf.exterior
                buff_geo_ext = geo_ext.buffer(-margin)
                geo = unary_union(buff_geo_ext.interiors)
        solid_geo = rectangular_paths(geo, abs(cut_dia), gaps, gapsize, margin, xmin, ymin, xmax, ymax)
        if solid_geo is None:
            return result
    else:
        if source_kind == 'geometry':
            object_geo = flatten_shapely_geometry(object_geo)
            for geom_struct in object_geo:
                geom_struct = unary_union(geom_struct)
                xmin, ymin, xmax, ymax = geom_struct.bounds
                geom_struct = box(xmin, ymin, xmax, ymax)
                if margin >= 0:
                    geo_buf = geom_struct.buffer(margin)
                    if geo_buf.is_empty:
                        geo_buf = geom_struct.buffer(margin + 0.0000001)
                    geom_struct = geo_buf.exterior
                else:
                    geo_buf = geom_struct.buffer(0.0000001)
                    geo_ext = geo_buf.exterior
                    buff_geo_ext = geo_ext.buffer(-margin)
                    geom_struct = unary_union(buff_geo_ext.interiors)
                rectangular_rows.append(geom_struct)
                c_geo = rectangular_paths(geom_struct, abs(cut_dia), gaps, gapsize, margin, xmin, ymin, xmax, ymax)
                if c_geo is None:
                    return result
                solid_geo += c_geo
        elif source_kind == 'gerber' and margin >= 0:
            object_geo = flatten_shapely_geometry(object_geo)
            for geom_struct in object_geo:
                geom_struct = unary_union(geom_struct)
                xmin, ymin, xmax, ymax = geom_struct.bounds
                geom_struct = box(xmin, ymin, xmax, ymax)
                geom_struct = geom_struct.buffer(margin + abs(cut_dia / 2))
                c_geo = rectangular_paths(geom_struct, abs(cut_dia), gaps, gapsize, margin, xmin, ymin, xmax, ymax)
                if c_geo is None:
                    return result
                solid_geo += c_geo
        elif source_kind == 'gerber' and margin < 0:
            log.error("Rectangular cutout with negative margin is not possible.")
            result.errors.append(_("Failed."))
            result.failed = True
            return result
    if not solid_geo:
        log.debug("Cutout.on_rectangular_cutout() -> Empty solid geometry.")
        result.errors.append(_("Failed."))
        result.failed = True
        return result
    try:
        solid_geo = linemerge(solid_geo)
    except Exception:
        pass
    if has_mouse_bites is True:
        gapsize -= abs(cut_dia) / 2
        mb_object_geo = deepcopy_keep_geometry(object_geo)
        if kind == 'single':
            mb_object_geo = unary_union(mb_object_geo)
            xmin, ymin, xmax, ymax = mb_object_geo.bounds
            mb_geo = box(xmin, ymin, xmax, ymax)
            if source_kind == 'gerber':
                if margin >= 0:
                    mb_geo = mb_geo.buffer(margin + mb_buff_val)
                else:
                    mb_geo = mb_geo.buffer(margin - mb_buff_val)
            else:
                mb_geo = geo
            mb_result = rectangular_paths(mb_geo, abs(cut_dia), gaps, gapsize, margin,
                                          xmin, ymin, xmax, ymax, include_gaps=True)
            if mb_result is None:
                return result
            __, mouse_bites_geo = mb_result
        else:
            if source_kind == 'geometry':
                mb_object_geo = rectangular_rows
                for mb_geom_struct in mb_object_geo:
                    mb_geom_struct = unary_union(mb_geom_struct)
                    xmin, ymin, xmax, ymax = mb_geom_struct.bounds
                    mb_geom_struct = box(xmin, ymin, xmax, ymax)
                    mb_result = rectangular_paths(mb_geom_struct, abs(cut_dia), gaps, gapsize, margin,
                                                  xmin, ymin, xmax, ymax, include_gaps=True)
                    if mb_result is None:
                        return result
                    __, mb_gaps = mb_result
                    mouse_bites_geo += mb_gaps
            elif source_kind == 'gerber' and margin >= 0:
                mb_object_geo = flatten_shapely_geometry(mb_object_geo)
                for mb_geom_struct in mb_object_geo:
                    mb_geom_struct = unary_union(mb_geom_struct)
                    xmin, ymin, xmax, ymax = mb_geom_struct.bounds
                    mb_geom_struct = box(xmin, ymin, xmax, ymax)
                    mb_geom_struct = mb_geom_struct.buffer(margin + mb_buff_val)
                    mb_result = rectangular_paths(mb_geom_struct, abs(cut_dia), gaps, gapsize, margin,
                                                  xmin, ymin, xmax, ymax, include_gaps=True)
                    if mb_result is None:
                        return result
                    __, mb_gaps = mb_result
                    mouse_bites_geo += mb_gaps
            elif source_kind == 'gerber' and margin < 0:
                log.error("Rectangular cutout with negative margin is not possible.")
                result.errors.append(_("Failed."))
                result.failed = True
                return result
        if mouse_bites_geo:
            mouse_bites_geo = linemerge(unary_union(mouse_bites_geo))
        result.holes = mouse_bite_holes(_cutout_paths(mouse_bites_geo), mb_dia, mb_spacing)
    result.solid_geo = solid_geo
    result.gaps_solid_geo = gaps_solid_geo
    return result
