"""Explicit-data drilling geometry and tool calculations."""

import builtins
import gettext
from copy import deepcopy

import numpy as np
import shapely
from shapely import LineString
from shapely.ops import unary_union

from camlib import flatten_shapely_geometry
from defaults import AppDefaults
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def merge_same_diameter_tools(tools):
    """
    Merge equal diameters, keeping the tool id (the key of the first tool of each diameter), the data of the first tool
    and all the features of the merged tools. When the merged tools have different data, see diameter_conflicts().
    """
    diameters = [tool_val['tooldia'] for tool_val in tools.values()]
    if len(diameters) == len(set(diameters)):
        return tools

    merged = {}
    id_by_dia = {}
    for tool_id, tool_val in tools.items():
        dia = tool_val['tooldia']
        if dia not in id_by_dia:
            id_by_dia[dia] = tool_id
            merged[tool_id] = deepcopy(tool_val)
            continue

        target = merged[id_by_dia[dia]]
        for key in ('drills', 'slots'):
            if tool_val.get(key):
                target[key] = list(target.get(key) or []) + list(tool_val[key])
        if tool_val.get('solid_geometry'):
            target['solid_geometry'] = flatten_shapely_geometry(
                [target.get('solid_geometry') or [], tool_val['solid_geometry']])
    return merged


def next_tool_id(tools):
    """The next free tool id: one after the highest id of the tools."""
    return max((int(tool_id) for tool_id in tools), default=0) + 1


def spot_tool(data, spot_dia=None, spot_cutz=None, spot_feedrate_z=None):
    """
    The tool of the spot drilling pass, in the format of the Excellon tools (it has no drills of its own: the pass
    drills the holes of all the selected tools). Its data are the data of a drilling tool, with the spot Cut Z and
    Feedrate Z; peck, multidepth, dwell at the bottom and the depth offset do not apply to the spot holes.

    :param data:    the data of a drilling tool (the job-wide values of the spot are read from it, as the other
                    parameters of the tool come from it); the arguments give the values that are not in the data
    """
    def option(key, value):
        if value is not None:
            return value
        return data.get(key) if data.get(key) is not None else AppDefaults.factory_defaults[key]

    spot_data = dict(data)
    spot_data.update({
        'tools_drill_cutz': option('tools_drill_spot_cutz', spot_cutz),
        'tools_drill_feedrate_z': option('tools_drill_spot_feedrate_z', spot_feedrate_z),
        'tools_drill_multidepth': False,
        'tools_drill_peck': False,
        'tools_drill_dwell_bottom': False,
        'tools_drill_offset': 0.0,
    })
    return {'tooldia': float(option('tools_drill_spot_dia', spot_dia)), 'drills': [], 'slots': [],
            'solid_geometry': [], 'data': spot_data}


def diameter_conflicts(tools):
    """
    The tools that merge_same_diameter_tools() puts together although their data differs (Cut Z, Drill slots ...): the
    merged tool keeps the data of the first one.

    :param tools:   the tools before the merge
    :return:        list of (diameter, [tool ids], [keys of the data that differ]) for each diameter with a conflict
    """
    by_dia = {}
    for tool_id, tool_val in tools.items():
        by_dia.setdefault(tool_val['tooldia'], []).append(tool_id)

    conflicts = []
    for dia, ids in by_dia.items():
        if len(ids) < 2:
            continue
        first = tools[ids[0]].get('data') or {}
        keys = set()
        for other_id in ids[1:]:
            other = tools[other_id].get('data') or {}
            keys.update(k for k in set(first) | set(other) if first.get(k) != other.get(k))
        if keys:
            conflicts.append((dia, ids, sorted(keys)))
    return conflicts


def tool_setting_problems(data, laser):
    """
    The settings of a drilling tool that make dangerous or wrong G-code: a list of messages, empty when the job can be
    made. A laser preprocessor is not checked: its Travel Z is the focus height and it has no Z feed.
    """
    def number(key):
        try:
            return float(data[key])
        except (KeyError, TypeError, ValueError):
            return None

    problems = []
    if laser:
        return problems
    travelz = number('tools_drill_travelz')
    if travelz is not None and travelz <= 0:
        problems.append(_("Travel Z must be above zero: the moves between the holes would be made with the bit in "
                          "or on the material."))
    feedrate_z = number('tools_drill_feedrate_z')
    if feedrate_z is not None and feedrate_z <= 0:
        problems.append(_("Feedrate Z must be above zero."))
    return problems


def merge_object_tools(main_tools, extra_tools_list):
    """
    The tools of a job over more Excellon objects, merged by diameter. The tool ids of the main object stay; a tool of
    an extra object with a new diameter gets the next free id. The data of a tool come from the main object's tool of
    that diameter, or from the extra object's tool when the main object has none. The source tools are not changed.

    :param main_tools:          the tools of the main object
    :param extra_tools_list:    a list of the tools of the extra objects
    :return:                    (merged tools, diameter_conflicts() of the tools before the merge)
    """
    def copy_tool(tool):
        return dict(tool, data=deepcopy(tool.get('data') or {}), drills=list(tool.get('drills') or []),
                    slots=list(tool.get('slots') or []))

    combined = {tool_id: copy_tool(tool) for tool_id, tool in main_tools.items()}
    for extra_tools in extra_tools_list:
        for tool in extra_tools.values():
            combined[next_tool_id(combined)] = copy_tool(tool)
    conflicts = diameter_conflicts(combined)
    merged = merge_same_diameter_tools(combined)

    # the tools of the extra objects that stay get the next free ids one after the other (no gaps)
    result = {tool_id: tool for tool_id, tool in merged.items() if tool_id in main_tools}
    for tool_id, tool in merged.items():
        if tool_id not in main_tools:
            result[next_tool_id(result)] = tool
    return result, conflicts


def points_in_areas(points, areas):
    """
    The drill points that are inside the polygons (covered by them); a tool without a point left is dropped.

    :param points:  {tool id: [Shapely Point]}
    :param areas:   a list of Shapely polygons
    """
    zone = unary_union(list(areas))
    shapely.prepare(zone)
    kept = {}
    for tool_id, pts in points.items():
        if not pts:
            continue
        arr = np.empty(len(pts), dtype=object)
        arr[:] = pts
        inside = shapely.covered_by(arr, zone)
        tool_points = [pt for pt, ok in zip(pts, inside) if ok]
        if tool_points:
            kept[tool_id] = tool_points
    return kept


def process_slot_as_drills(slot, overlap, add_last_pt=False):
    """
    The drill points of a slot: its start, then a point every `overlap` (the distance between the drills) along it,
    then its end when add_last_pt is set. A distance of zero or less gives only the start and the end.
    """
    start_pt = slot[0]
    stop_pt = slot[1]
    drills_list = [start_pt]

    spacing = max(float(overlap), 0.0)
    if spacing > 0.0:
        slot_line = LineString([start_pt, stop_pt])
        length = slot_line.length
        count = int(length // spacing)
        if count:
            distances = spacing * np.arange(1, count + 1)
            # only the points inside the slot, as before: the end is added below
            distances = distances[distances < length]
            if len(distances):
                drills_list += list(shapely.line_interpolate_point(slot_line, distances))

    if add_last_pt and stop_pt.distance(drills_list[-1]) >= spacing / 10:
        drills_list.append(stop_pt)
    return drills_list


def create_drill_points(selected_tools, selected_sorted_tools, excellon_tools,
                        drill_overlap=None, add_last_pt=None, slot_processor=process_slot_as_drills):
    """
    Group selected drills and converted slots without changing source tool records.

    The slots are drilled for the tools whose data has 'tools_drill_drill_slots'. drill_overlap and add_last_pt are
    taken for each tool from its data ('tools_drill_drill_overlap', 'tools_drill_last_drill') when they are None.
    """
    points = {}
    sorted_dia = dict(selected_sorted_tools)
    for tool_key, tl_dict in excellon_tools.items():
        if tool_key not in selected_tools:
            continue

        tool_points = list(tl_dict.get('drills') or [])

        data = tl_dict.get('data') or {}
        if data.get('tools_drill_drill_slots') and tl_dict.get('slots'):
            overlap = drill_overlap if drill_overlap is not None else data.get('tools_drill_drill_overlap', 0.0)
            last_pt = add_last_pt if add_last_pt is not None else data.get('tools_drill_last_drill', True)
            slot_tool_dia = sorted_dia.get(tool_key, tl_dict.get('tooldia', 0.0))
            spacing = max((1 - (float(overlap or 0.0) / 100.0)) * float(slot_tool_dia), 0.0)
            for slot in tl_dict['slots']:
                tool_points += slot_processor(slot=slot, overlap=spacing, add_last_pt=last_pt)

        if tool_points:
            points[tool_key] = tool_points
    return points


def parse_xy(value, label):
    """
    A pair of numbers from a tuple, a list or a text like "1.5, -2" or "(1.5,-2)"; None for a blank value.

    :raises ValueError: the value is not a pair of numbers (the message names `label`); a number, a bool or any
        other value that is not a pair (not iterable, or a bool part) is not a pair of numbers
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.replace('(', '').replace(')', '').replace('[', '').replace(']', '').strip()
        if not text:
            return None
        parts = text.split(',')
    else:
        try:
            parts = list(value)
        except TypeError:
            parts = []
    try:
        pair = () if any(isinstance(part, bool) for part in parts) else tuple(float(part) for part in parts)
    except (TypeError, ValueError):
        pair = ()
    if len(pair) != 2:
        raise ValueError("%s: %s" % (label, _("use two numbers: x, y")))
    return pair


def job_transform_params(data):
    """
    The job XY transform from the job options (the keys tools_drill_job_offset_xy, tools_drill_mirror,
    tools_drill_mirror_point, tools_drill_array_columns, tools_drill_array_rows, tools_drill_array_pitch). A missing
    key has the default of the preferences.

    :return:    the keyword arguments of transform_points()
    :raises ValueError: a value is not valid; the message says which one
    """
    def option(key):
        return data[key] if data.get(key) is not None else AppDefaults.factory_defaults.get(key)

    offset = parse_xy(option('tools_drill_job_offset_xy'), _('Job offset X,Y')) or (0.0, 0.0)
    mirror = str(option('tools_drill_mirror') or 'no').lower()
    if mirror not in ('no', 'x', 'y', 'xy'):
        raise ValueError(_("Mirror: use no, x, y or xy"))
    mirror_point = parse_xy(option('tools_drill_mirror_point'), _('Mirror point X,Y'))
    try:
        columns = int(option('tools_drill_array_columns'))
        rows = int(option('tools_drill_array_rows'))
    except (TypeError, ValueError):
        raise ValueError(_("Array: columns and rows have to be whole numbers")) from None
    if columns < 1 or rows < 1:
        raise ValueError(_("Array: columns and rows have to be 1 or more"))
    pitch = parse_xy(option('tools_drill_array_pitch'), _('Array pitch X,Y'))
    if (columns > 1 and not (pitch and pitch[0])) or (rows > 1 and not (pitch and pitch[1])):
        raise ValueError(_("Array pitch X,Y: needed (not zero) for the columns and rows of the array"))
    return {'offset': offset, 'mirror': mirror, 'mirror_point': mirror_point, 'columns': columns, 'rows': rows,
            'pitch': pitch or (0.0, 0.0)}


def transform_points(points, offset=(0.0, 0.0), mirror='no', mirror_point=None, columns=1, rows=1, pitch=(0.0, 0.0)):
    """
    The drill points of a job moved: first mirrored, then offset, then repeated as an array. The points are not changed.

    :param points:          {tool id: [Shapely Point]}
    :param offset:          (dx, dy)
    :param mirror:          'no', 'x' (the Y values flip, the mirror axis is the X axis), 'y' (the X values flip) or 'xy'
    :param mirror_point:    (x, y) the mirror axes pass through; None: the center of the bounding box of all the points
    :param columns, rows:   the array: copies along X and along Y, `pitch` = (pitch X, pitch Y) apart; 1 x 1 is no array
    :return:                a new {tool id: [Shapely Point]}
    """
    if mirror == 'no' and not columns - 1 and not rows - 1 and not any(offset):
        return points

    coords = {tool: shapely.get_coordinates(np.array(pts, dtype=object)) for tool, pts in points.items() if pts}
    if not coords:
        return points

    if mirror != 'no':
        if mirror_point is None:
            every = np.concatenate(list(coords.values()))
            low, high = every.min(axis=0), every.max(axis=0)
            mirror_point = tuple((low + high) / 2.0)
        flip = np.array([-1.0 if 'y' in mirror else 1.0, -1.0 if 'x' in mirror else 1.0])
        center = np.array(mirror_point, dtype=float)
        coords = {tool: center + (xy - center) * flip for tool, xy in coords.items()}

    shifts = np.array([[col * pitch[0] + offset[0], row * pitch[1] + offset[1]]
                       for row in range(rows) for col in range(columns)], dtype=float)
    return {tool: list(shapely.points((xy[None, :, :] + shifts[:, None, :]).reshape(-1, 2)))
            for tool, xy in coords.items()}


def transform_job_points(points, data):
    """transform_points() with the job options of `data` (see job_transform_params()); ValueError if they are invalid."""
    return transform_points(points, **job_transform_params(data))


def distance(pt1, pt2):
    return np.sqrt((pt1[0] - pt2[0]) ** 2 + (pt1[1] - pt2[1]) ** 2)


def distance_euclidian(x1, y1, x2, y2):
    return np.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)
