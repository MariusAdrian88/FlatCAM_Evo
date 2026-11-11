"""Copper and preview geometry for fiducials."""

import math

from shapely import LineString, Point, Polygon, box
from shapely.ops import unary_union

from camlib import flatten_shapely_geometry, gerber_quad_segs, deepcopy_keep_geometry


def make_square_poly(center_pt, side_size):
    half_s = side_size / 2
    x_center, y_center = center_pt
    pt1 = (x_center - half_s, y_center - half_s)
    pt2 = (x_center + half_s, y_center - half_s)
    pt3 = (x_center + half_s, y_center + half_s)
    pt4 = (x_center - half_s, y_center + half_s)
    return Polygon([pt1, pt2, pt3, pt4, pt1])


def auto_points(bounds, margin, sec_position, decimals):
    """
    The fiducial points of the automatic mode: bottom left and top right of the bounds grown by the margin, and
    the second position ('up': top left, 'down': bottom right, 'no': none).
    """
    buf_bbox = box(*bounds).buffer(margin, join_style="mitre")
    x0, y0, x1, y1 = [round(v, decimals) for v in buf_bbox.bounds]
    points = [(x0, y0), (x1, y1)]
    if sec_position == 'up':
        points.append((x0, y1))
    elif sec_position == 'down':
        points.append((x1, y0))
    return points


def fiducial_shapes(pt, fid_size, fid_type, line_thickness, options, units='MM'):
    radius = fid_size / 2.0
    if fid_type == 0:
        return [Point(pt).buffer(radius, gerber_quad_segs(options, radius, units))]
    x, y = pt[0], pt[1]
    if fid_type == 1:
        return [LineString([(x - radius + line_thickness / 2.0, y),
                            (x + radius - line_thickness / 2.0, y)]),
                LineString([(x, y - radius + line_thickness / 2.0),
                            (x, y + radius - line_thickness / 2.0)])]
    return [make_square_poly((x - fid_size / 4, y + fid_size / 4), fid_size / 2),
            make_square_poly((x + fid_size / 4, y - fid_size / 4), fid_size / 2)]


def fiducial_geometry(points_list, tools, solid_geometry, fid_size, fid_type, line_thickness, options, decimals,
                      units='MM'):
    """
    :return:    the new apertures, the union of the old solid geometry and the fiducials, the flat list of the
                fiducial geometries (the center lines for the cross type, the polygons for the others)
    """
    new_apertures = deepcopy_keep_geometry(tools)
    geo_list = []
    for pt in points_list:
        geo_list += fiducial_shapes(pt, fid_size, fid_type, line_thickness, options, units)

    ap_type = 'C' if fid_type in (0, 1) else 'R'
    if fid_type == 0:
        ap_size = fid_size
    elif fid_type == 1:
        ap_size = line_thickness
    else:
        # the parser stores the diagonal of the rectangle as the size of an 'R' aperture
        ap_size = math.hypot(fid_size / 2.0, fid_size / 2.0)
    aperture_found = None
    for ap_id, ap_val in tools.items():
        if ap_val['type'] != ap_type:
            continue
        if fid_type == 2:
            same = (round(ap_val.get('width', 0), ndigits=decimals) == round(fid_size / 2.0, ndigits=decimals) and
                    round(ap_val.get('height', 0), ndigits=decimals) == round(fid_size / 2.0, ndigits=decimals))
        else:
            same = ap_val['size'] == ap_size
        if same:
            aperture_found = ap_id
            break
    if not aperture_found:
        aperture_found = int(max(tools)) + 1 if tools else 10
        new_apertures[aperture_found] = {'type': ap_type, 'size': ap_size, 'geometry': []}
        if fid_type == 2:
            new_apertures[aperture_found].update(width=fid_size / 2.0, height=fid_size / 2.0)

    # the 'follow' geometry is what the Gerber export writes: a flash for the circle and the chess squares,
    # a drawn line for the cross
    solids = []
    for geo in geo_list:
        if fid_type == 1:
            solid, follow = geo.buffer(line_thickness / 2.0, gerber_quad_segs(options, line_thickness / 2.0, units)), geo
        else:
            solid, follow = geo, geo.centroid
        solids.append(solid)
        new_apertures[aperture_found]['geometry'].append({'follow': follow, 'solid': solid})
    flat_geo = flatten_shapely_geometry(solid_geometry)
    s_list = (list(flat_geo) if flat_geo else []) + solids
    return new_apertures, unary_union(s_list), geo_list
