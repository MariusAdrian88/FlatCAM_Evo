# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/12/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The plan of the Gerber export of an object that the per-aperture writer can not write as it is: an object with clear
(LPC) objects, with apertures the writer can not declare (AM, P, AB, an aperture with a hole), with tracks of an R
aperture, with region holes, or with aperture metadata that no longer describes the records (after a transform or a
buffer).

Each record stays one object of the file:

* a flash or a track of a C, R or O aperture whose metadata describes it: that aperture, flashed or drawn. The flash
  point and the centre line stay;
* any other flash: a block aperture (%AB) that holds the shape of the record around its flash point, flashed at the
  flash point. The flash point stays: a reader makes a flash record with its centre;
* any other record (a region, a track that no aperture describes): regions without holes. A hole of a region would be
  a clear object of the whole image, so a polygon with holes is cut into parts without holes. Only these records lose
  their centre line.

The records are written in the order of their 'seq' numbers (the order of the file), the records without one after
them. If this order is not known to make ``solid_geometry`` (``Gerber.records_in_file_order`` is None), the plan
composes the image of the records and compares it with ``solid_geometry``. The differences go at the end: a clear
region where the records make copper that ``solid_geometry`` does not have, then a dark region where
``solid_geometry`` has copper that the records do not make. The file then gives ``solid_geometry``, and all the
flashes and tracks stay.
"""

from types import SimpleNamespace

import numpy as np
import shapely
from shapely.geometry import LinearRing, LineString, MultiLineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from camlib import flatten_shapely_geometry

# the aperture types the writer declares (the same as Gerber.NATIVE_WRITER_APERTURE_TYPES)
NATIVE_TYPES = ('C', 'R', 'O')
# a difference between the records and solid_geometry thinner than 2 x this width (in mm) is numeric noise
NOISE_WIDTH_MM = 0.0005
# the growth (in mm) of a region of the differences before it is cut with the target copper
PATCH_GROW_MM = 0.02
# the grid (in the units of the object) of the key that finds equal block shapes
BLOCK_KEY_GRID = 1e-7


def hole_free_parts(polygon):
    """
    Parts without holes whose union is the polygon. Each pass cuts the polygon in strips at a point inside each hole,
    so each hole opens into the strips on both sides of the cut. The strips share their edges exactly.

    :param polygon: a valid Polygon
    :return:        list of Polygons; a part that still has holes after 4 passes stays as it is (it does not happen
                    for a valid polygon)
    """
    if not polygon.interiors:
        return [polygon]
    done, pending = [], [polygon]
    for axis in (0, 1, 0, 1):
        next_pending = []
        for part in pending:
            if not part.interiors:
                done.append(part)
                continue
            cuts = sorted({Polygon(ring).representative_point().coords[0][axis] for ring in part.interiors})
            minx, miny, maxx, maxy = part.bounds
            edges = [minx if axis == 0 else miny] + cuts + [maxx if axis == 0 else maxy]
            strips = [box(a, miny, b, maxy) if axis == 0 else box(minx, a, maxx, b)
                      for a, b in zip(edges, edges[1:]) if b > a]
            for piece in shapely.get_parts(shapely.intersection(part, np.array(strips, dtype=object))):
                for sub in shapely.get_parts(piece):
                    if isinstance(sub, Polygon) and not sub.is_empty and sub.area > 0:
                        next_pending.append(sub)
        pending = next_pending
        if not pending:
            break
    return done + pending


def polygon_parts(geometry):
    """The non-empty Polygons of a geometry, made valid first."""
    if geometry is None or geometry.is_empty:
        return []
    if not geometry.is_valid:
        geometry = shapely.make_valid(geometry)
    return [part for part in flatten_shapely_geometry(geometry)
            if isinstance(part, Polygon) and not part.is_empty and part.area > 0]


def valid_polygons(geometries):
    """The non-empty Polygons of many geometries, each one made valid first (a few vectorized calls)."""
    array = np.array([g for g in geometries if g is not None], dtype=object)
    if not len(array):
        return []
    invalid = ~shapely.is_valid(array)
    if invalid.any():
        array[invalid] = shapely.make_valid(array[invalid])
    parts = shapely.get_parts(shapely.get_parts(array))
    keep = (shapely.get_type_id(parts) == 3) & ~shapely.is_empty(parts)
    parts = parts[keep]
    return list(parts[shapely.area(parts) > 0])


def boolean(subject, clip, op):
    """
    'union' (of subject and clip), 'difference' (subject minus clip) of lists of valid Polygons, as a list of
    Polygons. Many polygons go to Clipper2 (much faster than GEOS); GEOS if Clipper2 is not there or its result is not
    valid.
    """
    from appCommon import clipper_ops
    if op == 'difference' and not subject:
        return []
    if len(subject) + len(clip) >= clipper_ops.MIN_SHAPES:
        result = clipper_ops.boolean(subject + clip if op == 'union' else subject,
                                     None if op == 'union' else clip, op)
        if result is not None:
            # a Clipper2 ring can touch itself: only those polygons are repaired
            return valid_polygons(result)
    a = shapely.union_all(np.array(subject + (clip if op == 'union' else []) or [Polygon()], dtype=object))
    if op == 'difference' and clip:
        a = a.difference(shapely.union_all(np.array(clip, dtype=object)))
    return valid_polygons([a])


def clip_to(parts, container, inside=True):
    """
    The parts cut to the container (inside=True) or the parts minus the container (inside=False), in GEOS (exact).
    Only the polygons of the container near each part are used.

    :param parts:       list of Polygons (few)
    :param container:   list of valid Polygons (many)
    :param inside:      True: intersection, False: difference
    :return:            list of Polygons
    """
    if not parts:
        return []
    if not container:
        return [] if inside else list(parts)
    container = np.array(container, dtype=object)
    tree = shapely.STRtree(container)
    pieces = []
    for part in parts:
        near = container[tree.query(part, predicate='intersects')]
        if not len(near):
            if not inside:
                pieces.append(part)
            continue
        near = shapely.union_all(near)
        pieces.append(part.intersection(near) if inside else part.difference(near))
    return valid_polygons(pieces)


def compose(images):
    """
    The image of objects drawn in order: a dark one is added, a clear one is cut. Consecutive objects of the same
    polarity are joined in one operation.

    :param images:  list of (geometry, True for a clear object)
    :return:        list of Polygons
    """
    result = []
    run, run_clear = [], False
    for geometry, is_clear in images + [(None, None)]:
        if is_clear != run_clear and run:
            parts = valid_polygons(run)
            result = boolean(result, parts, 'difference' if run_clear else 'union')
            run = []
        run_clear = bool(is_clear)
        if geometry is not None:
            run.append(geometry)
    return result


def _not_noise(parts, width):
    """The parts that are wider than 2 x width (a thinner part is numeric noise)."""
    if not parts:
        return []
    array = np.array(parts, dtype=object)
    keep = ~shapely.is_empty(shapely.buffer(array, -width, quad_segs=1, join_style='mitre'))
    return [part for part, kept in zip(parts, keep.tolist()) if kept]


def _c_radius(tool):
    try:
        radius = float(tool.get('size', 0)) / 2
    except (TypeError, ValueError):
        return 0.0
    return radius if np.isfinite(radius) and radius > 0 else 0.0


def _fitting_c_records(tools, ordered):
    """
    The records of C apertures whose image is still the circle or the round track of the aperture size, for an
    object whose aperture metadata is not sure (``export_as_regions``: a transform or a buffer that one aperture
    could not follow). The buffer grows each C aperture, so its records still fit. A difference not wider than 2 % of
    the radius (the circle steps of the parser) is a fit. A few vectorized calls for all the records.

    :return: set of (id(record), clear?) that fit
    """
    picked = []
    for apid, record, is_clear in ordered:
        tool = tools.get(apid) or {}
        if tool.get('type') != 'C' or tool.get('hole'):
            continue
        image, follow, radius = record.get('clear' if is_clear else 'solid'), record.get('follow'), _c_radius(tool)
        if radius and isinstance(image, BaseGeometry) and not image.is_empty and \
                isinstance(follow, (Point, LineString, LinearRing)) and not follow.is_empty:
            picked.append((record, is_clear, image, follow, radius))
    if not picked:
        return set()
    images = np.array([p[2] for p in picked], dtype=object)
    follows = np.array([p[3] for p in picked], dtype=object)
    radii = np.array([p[4] for p in picked], dtype=float)
    try:
        difference = shapely.symmetric_difference(shapely.make_valid(images), shapely.buffer(follows, radii,
                                                                                             quad_segs=16))
        fits = shapely.is_empty(shapely.buffer(difference, -0.02 * radii, quad_segs=1, join_style='mitre'))
    except shapely.errors.GEOSException:
        return set()
    return {(id(p[0]), p[1]) for p, fit in zip(picked, fits.tolist()) if fit}


def _is_native(tool, image, follow, metadata_ok, rectangle_flash):
    """
    True if the aperture of the tool, flashed at or drawn along follow, makes this record. With aperture metadata
    that is not sure, see _fitting_c_records().
    """
    kind = tool.get('type')
    if kind not in NATIVE_TYPES or tool.get('hole') or not metadata_ok:
        return False
    if not isinstance(follow, (Point, LineString, LinearRing)) or follow.is_empty:
        return False
    if kind == 'R':
        # the parser draws a track of an R aperture as a box around its end points: only flashes are native
        return isinstance(follow, Point) and rectangle_flash(tool, {'solid': image})
    return True


def _block_key(shape):
    coords = shapely.get_coordinates(shape)
    rings = tuple(len(ring.coords) for part in flatten_shapely_geometry(shape) if isinstance(part, Polygon)
                  for ring in [part.exterior, *part.interiors])
    return rings, np.round(coords / BLOCK_KEY_GRID).astype(np.int64).tobytes()


def build_export_plan(obj, first_block_code=None):
    """
    Plan the export of the records of a Gerber object (see the module comment). The object is only read.

    :param obj:                 a Gerber object (or an object with the same attributes)
    :param first_block_code:    the first D code of the block apertures; default: after the codes of obj.tools
    :return:                    SimpleNamespace with
                                ``items``: list of ('ap', aperture id, follow, clear?, record) |
                                ('block', D code, flash point, clear?, record) | ('region', Polygon, None, clear?, record);
                                record is None for the regions of the differences;
                                ``blocks``: {D code: list of hole-free Polygons around the origin};
                                ``trusted``: True if the order of the records is the order of the file;
                                ``patched``: count of the regions added for the differences
    """
    from appParsers.ParseGerber import Gerber, _is_rectangle_flash

    tools = getattr(obj, 'tools', None) or {}
    # a region with holes is written as hole-free pieces below, so it does not make the order unknown
    ordered = Gerber.records_in_file_order(obj, allow_region_holes=True)
    # export_as_regions: the records may no longer describe the copper, solid_geometry is the truth
    trusted = ordered is not None and not getattr(obj, 'export_as_regions', False)
    if ordered is None:
        entries = []
        for apid, tool in tools.items():
            for record in tool.get('geometry') or ():
                for key, is_clear in (('solid', False), ('clear', True)):
                    geometry = record.get(key)
                    if isinstance(geometry, BaseGeometry) and not geometry.is_empty:
                        entries.append((apid, record, is_clear))
        # stable: the records with a 'seq' in that order, then the others in the order of the apertures
        order = sorted(range(len(entries)), key=lambda i: (0, entries[i][1]['seq'], i)
                       if type(entries[i][1].get('seq')) is int else (1, 0, i))
        ordered = [entries[i] for i in order]

    metadata_ok = not getattr(obj, 'export_as_regions', False)
    fitting = set() if metadata_ok else _fitting_c_records(tools, ordered)
    codes = [int(code) for code in tools if str(code).lstrip('-').isdigit()]
    next_code = first_block_code if first_block_code is not None else max(codes + [9]) + 1
    blocks, block_codes = {}, {}
    items, images = [], []

    for apid, record, is_clear in ordered:
        image = record.get('clear' if is_clear else 'solid')
        if not isinstance(image, BaseGeometry) or image.is_empty:
            continue
        tool = tools.get(apid) or {}
        follow = record.get('follow')
        images.append((image, is_clear))
        region_tool = tool.get('type') == 'REG'
        if not region_tool and ((id(record), is_clear) in fitting or
                                _is_native(tool, image, follow, metadata_ok, _is_rectangle_flash)):
            items.append(('ap', apid, follow, is_clear, record))
            continue
        parts = polygon_parts(image)
        if not region_tool and isinstance(follow, Point) and not follow.is_empty and parts:
            shape = shapely.transform(image if image.is_valid else shapely.make_valid(image),
                                      lambda coords: coords - (follow.x, follow.y))
            key = _block_key(shape)
            code = block_codes.get(key)
            if code is None:
                code = block_codes[key] = next_code
                next_code += 1
                blocks[code] = [piece for part in polygon_parts(shape) for piece in hole_free_parts(part)]
            items.append(('block', code, follow, is_clear, record))
            continue
        for part in parts:
            for piece in hole_free_parts(part):
                items.append(('region', piece, None, is_clear, record))

    patched = 0
    # an object without solid_geometry (a stand-in that holds only the apertures) has no copper to compare with:
    # its records are the image
    if not trusted and getattr(obj, 'solid_geometry', None) is not None:
        made = compose(images)
        target = Gerber.region_export_polygons(obj)
        units = str(getattr(obj, 'units', 'MM') or 'MM').upper()
        width = NOISE_WIDTH_MM if units == 'MM' else NOISE_WIDTH_MM / 25.4
        # The written flashes and tracks are made again by the reader with its own circle steps: a patch the size of
        # the difference would leave slivers at its edges. A patch is grown, then cut with the target: a clear patch
        # minus the target never erases copper of solid_geometry, a dark patch inside the target never adds copper.
        grow = PATCH_GROW_MM if units == 'MM' else PATCH_GROW_MM / 25.4
        extra = _not_noise(boolean(made, target, 'difference'), width)
        missing = _not_noise(boolean(target, made, 'difference'), width)
        if extra:
            extra = _not_noise(clip_to(valid_polygons(shapely.buffer(np.array(extra, dtype=object), grow,
                                                                     join_style='mitre')), target, inside=False),
                               width)
        if missing:
            missing = _not_noise(clip_to(valid_polygons(shapely.buffer(np.array(missing, dtype=object), grow,
                                                                       join_style='mitre')), target), width)
        for parts, is_clear in ((extra, True), (missing, False)):
            for part in parts:
                for piece in hole_free_parts(part):
                    items.append(('region', piece, None, is_clear, None))
                    patched += 1
    return SimpleNamespace(items=items, blocks=blocks, trusted=trusted, patched=patched)
