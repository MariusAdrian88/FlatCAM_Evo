"""Marker geometry generation with explicit dimensions and point data."""

import math
from copy import deepcopy
from shapely import LineString, Point, MultiPolygon
from shapely.ops import unary_union
from camlib import flatten_shapely_geometry, gerber_quad_segs, deepcopy_keep_geometry


def _flat(geometry):
    return flatten_shapely_geometry(geometry) if geometry else []


def marker_cross(pos, line_thickness, line_length):
    x, y = pos[0], pos[1]
    return [LineString([(x - line_length, y), (x + line_length, y)]),
            LineString([(x, y + line_length), (x, y - line_length)])]


def create_marker_geometry(points_storage, marker_type, line_thickness, line_length, mode, offset_x, offset_y):
    """Return marker lines; line_length is the half length used by the existing tool."""
    if line_thickness <= 0 or line_length <= 0:
        return 'fail'
    geo_list = []
    if mode == 0:   # 'automatic'
        for key in points_storage:
            if key == 'tl':
                pt = points_storage[key]
                x = pt[0] - offset_x - line_thickness / 2.0
                y = pt[1] + offset_y + line_thickness / 2.0
                if marker_type == 's':
                    line_geo_hor = LineString([(x, y), (x + line_length, y)])
                    line_geo_vert = LineString([(x, y), (x, y - line_length)])
                else:
                    line_geo_hor = LineString([(x - line_length, y), (x + line_length, y)])
                    line_geo_vert = LineString([(x, y + line_length), (x, y - line_length)])
                geo_list.append(line_geo_hor)
                geo_list.append(line_geo_vert)
            if key == 'tr':
                pt = points_storage[key]
                x = pt[0] + offset_x + line_thickness / 2.0
                y = pt[1] + offset_y + line_thickness / 2.0
                if marker_type == 's':
                    line_geo_hor = LineString([(x, y), (x - line_length, y)])
                    line_geo_vert = LineString([(x, y), (x, y - line_length)])
                else:
                    line_geo_hor = LineString([(x + line_length, y), (x - line_length, y)])
                    line_geo_vert = LineString([(x, y + line_length), (x, y - line_length)])
                geo_list.append(line_geo_hor)
                geo_list.append(line_geo_vert)
            if key == 'bl':
                pt = points_storage[key]
                x = pt[0] - offset_x - line_thickness / 2.0
                y = pt[1] - offset_y - line_thickness / 2.0
                if marker_type == 's':
                    line_geo_hor = LineString([(x, y), (x + line_length, y)])
                    line_geo_vert = LineString([(x, y), (x, y + line_length)])
                else:
                    line_geo_hor = LineString([(x - line_length, y), (x + line_length, y)])
                    line_geo_vert = LineString([(x, y - line_length), (x, y + line_length)])
                geo_list.append(line_geo_hor)
                geo_list.append(line_geo_vert)
            if key == 'br':
                pt = points_storage[key]
                x = pt[0] + offset_x + line_thickness / 2.0
                y = pt[1] - offset_y - line_thickness / 2.0
                if marker_type == 's':
                    line_geo_hor = LineString([(x, y), (x - line_length, y)])
                    line_geo_vert = LineString([(x, y), (x, y + line_length)])
                else:
                    line_geo_hor = LineString([(x + line_length, y), (x - line_length, y)])
                    line_geo_vert = LineString([(x, y - line_length), (x, y + line_length)])
                geo_list.append(line_geo_hor)
                geo_list.append(line_geo_vert)
    elif mode == 1 or mode == 2:     # 'manual' or 'numeric'
        if 'manual' in points_storage:
            if points_storage['manual']:
                for man_pt in points_storage['manual']:
                    geo_list.extend(marker_cross(man_pt, line_thickness, line_length))
            else:
                return 'fail'
    return geo_list


def gerber_marker_data(tools, solid_geometry, follow_geometry, marker_geometry, line_thickness, options, units='MM'):
    new_apertures = deepcopy_keep_geometry(tools)
    aperture_found = None
    for ap_id, ap_val in new_apertures.items():
        if ap_val['type'] == 'C' and math.isclose(ap_val['size'], line_thickness, abs_tol=1e-9):
            aperture_found = ap_id
            break

    quad_segs = gerber_quad_segs(options, line_thickness / 2.0, units)
    geo_buff_list = []
    if aperture_found:
        for geo in marker_geometry:
            geo_buff = geo.buffer(line_thickness / 2.0, quad_segs=quad_segs, join_style="mitre")
            geo_buff_list.append(geo_buff)
            dict_el = {'follow': geo, 'solid': geo_buff}
            new_apertures[aperture_found]['geometry'].append(dict_el)
    else:
        ap_keys = list(new_apertures.keys())
        new_apid = int(max(ap_keys)) + 1 if ap_keys else 10
        new_apertures[new_apid] = {'type': 'C', 'size': line_thickness, 'geometry': []}
        for geo in marker_geometry:
            geo_buff = geo.buffer(line_thickness / 2.0, quad_segs=quad_segs, join_style="mitre")
            geo_buff_list.append(geo_buff)
            dict_el = {'follow': geo, 'solid': geo_buff}
            new_apertures[new_apid]['geometry'].append(dict_el)

    s_list = []
    if solid_geometry:
        work_geo = solid_geometry.geoms if isinstance(solid_geometry, MultiPolygon) else solid_geometry
        try:
            for poly in work_geo:
                s_list.append(poly)
        except TypeError:
            s_list.append(work_geo)
    geo_buff_list = MultiPolygon(geo_buff_list)
    geo_buff_list = geo_buff_list.buffer(0)
    geo_buff_list = flatten_shapely_geometry(geo_buff_list)
    try:
        for poly in geo_buff_list:
            s_list.append(poly)
    except TypeError:
        s_list.append(geo_buff_list)
    return (new_apertures, flatten_shapely_geometry(unary_union(s_list)),
            _flat(follow_geometry) + list(marker_geometry))


def geometry_marker_data(tools, solid_geometry, marker_geometry, tooldia, new_data):
    new_tools = deepcopy_keep_geometry(tools)
    tool_found = None
    for tool, tool_dict in new_tools.items():
        if math.isclose(tool_dict['tooldia'], tooldia, abs_tol=1e-9):
            tool_found = tool
            break
    if tool_found:
        new_tools[tool_found]['solid_geometry'] = (_flat(new_tools[tool_found]['solid_geometry']) +
                                                   list(marker_geometry))
    else:
        obj_tools = list(new_tools.keys())
        new_tool = max(obj_tools) + 1 if obj_tools else 1
        new_tools[new_tool] = {'tooldia': tooldia, 'data': deepcopy(new_data), 'solid_geometry': marker_geometry}
        if not new_tools[new_tool]['solid_geometry']:
            new_tools.pop(new_tool)
    s_list = []
    if solid_geometry:
        s_list = flatten_shapely_geometry(solid_geometry)
    try:
        for g_el in marker_geometry:
            s_list.append(g_el)
    except TypeError:
        s_list.append(marker_geometry)
    return new_tools, flatten_shapely_geometry(s_list)


def drill_locations(bounds, states, points, line_thickness, offset_x, offset_y):
    xmin, ymin, xmax, ymax = bounds
    tl_state, tr_state, bl_state, br_state = states
    drill_list = []
    if 'manual' not in points:
        if tl_state:
            drill_list.append(Point((xmin - offset_x - line_thickness / 2.0,
                                     ymax + offset_y + line_thickness / 2.0)))
        if tr_state:
            drill_list.append(Point((xmax + offset_x + line_thickness / 2.0,
                                     ymax + offset_y + line_thickness / 2.0)))
        if bl_state:
            drill_list.append(Point((xmin - offset_x - line_thickness / 2.0,
                                     ymin - offset_y - line_thickness / 2.0)))
        if br_state:
            drill_list.append(Point((xmax + offset_x + line_thickness / 2.0,
                                     ymin - offset_y - line_thickness / 2.0)))
    else:
        for pt in points['manual']:
            drill_list.append(Point((pt[0], pt[1])))
    return drill_list
