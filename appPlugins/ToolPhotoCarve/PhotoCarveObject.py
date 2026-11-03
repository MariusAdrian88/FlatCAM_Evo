"""Shapely boundary for captured photo engraving results and certified cutter metadata."""

import math
import re
from copy import deepcopy

import numpy as np
from shapely import LineString, Point, force_2d

from .PhotoCarveModel import Pass, ToolShape, PhotoCarveError, xy_budget


# MillingUI's C1, C2, C3, C4, B, V, L combo identifiers.
MILL_SHAPES = {'flat': 0, 'ball': 4, 'vbit': 5}


def _simulation_tool(uid, record, mark, warnings):
    cutter = mark.get('tools', {}).get(str(uid))
    if cutter is None:
        warnings.append('Unmarked CNC tool simulated as a flat cutter.')
        tool = ToolShape('flat', float(record.get('tooldia', record.get('data', {}).get('tools_mill_tooldia', 0))))
    else:
        tool = ToolShape(**{key: cutter[key] for key in ('kind', 'diameter', 'angle_deg', 'tip_diameter')})
    if (tool.kind not in MILL_SHAPES or not math.isfinite(tool.diameter) or tool.diameter <= 0 or
            not math.isfinite(tool.angle_deg) or not 0 < tool.angle_deg < 180 or
            not math.isfinite(tool.tip_diameter) or not 0 <= tool.tip_diameter <= tool.diameter):
        raise PhotoCarveError('Invalid simulation cutter.')
    return tool


def passes_from_geometry(geo):
    """Detach Shapely coordinates on the GUI thread before starting simulation."""
    mark = geo.obj_options.get('photocarve', {})
    if not mark:
        raise PhotoCarveError('Generate photo engraving geometry first or select a CNCJob.')
    warnings, passes = [], []
    for uid, record in geo.tools.items():
        tool = _simulation_tool(uid, record, mark, warnings)
        paths = [np.array(shape.coords, dtype=np.float64, copy=True) for shape in record['solid_geometry']]
        passes.append(Pass('simulation', tool, paths, bool(mark.get('z_is_power'))))
    return passes, warnings


def passes_from_cncjob(cnc_obj, arc_res):
    """Interpret each tool's printed G-code in machine coordinates, never parsed XY geometry."""
    from .PhotoCarveSim import gcode_motion
    from .PhotoCarveLaser import format_power

    mark = cnc_obj.obj_options.get('photocarve', {})
    if mark.get('units', cnc_obj.units) != cnc_obj.units:
        raise PhotoCarveError('CNCJob and certified cutter units differ; regenerate the engraving.')
    if any(getattr(cnc_obj, key, '') for key in ('edited_source_file', 'prepend_snippet', 'append_snippet')):
        raise PhotoCarveError('Edited or snippet-bearing CNC programs cannot be associated safely with cutters.')
    laser = bool(mark.get('z_is_power'))
    warnings, passes = [], []
    records = cnc_obj.tools or {1: {'gcode': cnc_obj.gcode, 'tooldia': cnc_obj.obj_options.get('tooldia', 0)}}
    header = getattr(cnc_obj, 'gc_start', '')
    expected = header + ''.join(record.get('gcode', '') for record in records.values())

    def executable(text):
        text = re.sub(r'\([^()]*\)|;[^\n]*', '', text)
        return ''.join(text.upper().split())

    source = getattr(cnc_obj, 'source_file', '')
    if source and executable(source) != executable(expected):
        raise PhotoCarveError('Runnable CNC source differs from tool records; simulation refused.')
    program, previous_count = header, 0
    for uid, record in records.items():
        tool = _simulation_tool(uid, record, mark, warnings)
        # Interpret prefixes so modal state and program end never reset at a tool boundary.
        program += record.get('gcode', '')
        motion = gcode_motion(program, cnc_obj.units, arc_res, laser)
        spans, previous_count = motion[previous_count:], len(motion)
        paths, joined = [], []
        data = record.get('data', {})
        minimum = float(data.get('tools_mill_min_power', 0))
        maximum = float(data.get('tools_mill_spindlespeed', 1000))
        if laser:
            format_power(0, minimum, maximum)
        for span in spans:
            if laser:
                if not span.rapid and span.laser_on and span.s > 0:
                    power = max(0.0, min(1.0, (span.s - minimum) / (maximum - minimum)))
                    paths.append(np.array([[span.x0, span.y0, 0], [span.x1, span.y1, power]], dtype=np.float64))
                continue
            a, b = (span.x0, span.y0, span.z0), (span.x1, span.y1, span.z1)
            if span.rapid or (joined and joined[-1] != a):
                if joined:
                    paths.append(np.array(joined, dtype=np.float64))
                    joined = []
            if not span.rapid:
                if not joined:
                    joined.append(a)
                joined.append(b)
        if joined:
            paths.append(np.array(joined, dtype=np.float64))
        if paths:
            passes.append(Pass('machine coordinates', tool, paths, laser))
    if not passes:
        raise PhotoCarveError('The CNCJob contains no supported cutting motion.')
    return passes, warnings


def build_geometry(result, params, milldefaults):
    """Build independent multigeometry records using captured inputs only.

    The aggregate is XY for plotting; each tool keeps its XYZ or incoming laser power in Z.
    The caller stores ``photocarve`` in the geometry object's obj_options.
    """
    laser = params.mode == 'laser'
    mark = {'version': 1, 'mode': params.mode, 'z_is_power': laser, 'units': params.units,
            'contract': {'vcarve': 'groove', 'halftone': 'dot', 'laser': 'laser'}.get(params.mode, 'column'),
            'xy_err': xy_budget(params.decimals, params.skew), 'tools': {}}
    tools, aggregate = {}, []
    passes = sorted(result.passes, key=lambda part: part.role != 'rough')
    for part in passes:
        if part.z_is_power != laser:
            raise PhotoCarveError('Pass power/depth semantics do not match the job mode.')
        if not part.paths:
            continue
        uid = len(tools) + 1
        geometry, minimum = [], 0.0
        for raw_path in part.paths:
            path = np.asarray(raw_path, dtype=np.float64)
            if (path.ndim != 2 or path.shape[1] != 3 or not len(path) or not np.isfinite(path).all() or
                    (laser and (len(path) < 2 or np.any(path[:, 2] < 0) or np.any(path[:, 2] > 1)))):
                raise PhotoCarveError('Invalid photo engraving path.')
            geometry.append(Point(path[0]) if len(path) == 1 else LineString(path))
            minimum = min(minimum, float(np.min(path[:, 2])))
        cutter = {'kind': part.tool.kind, 'diameter': float(part.tool.diameter),
                  'angle_deg': float(part.tool.angle_deg), 'tip_diameter': float(part.tool.tip_diameter),
                  'role': part.role}
        data = deepcopy(milldefaults)
        data.update(tools_mill_tooldia=cutter['diameter'], tools_mill_cutz=0.0 if laser else minimum,
                    tools_mill_multidepth=False, tools_mill_offset_type=0, tools_mill_offset_value=0.0,
                    tools_mill_extracut=False, tools_mill_area_exclusion=False, tools_mill_polish=False,
                    tools_mill_tool_shape=6 if laser else MILL_SHAPES[cutter['kind']],
                    tools_mill_vtipdia=cutter['tip_diameter'], tools_mill_vtipangle=cutter['angle_deg'])
        tools[uid] = {'tooldia': cutter['diameter'], 'data': data, 'solid_geometry': geometry}
        aggregate.extend(force_2d(item) for item in geometry)
        mark['tools'][str(uid)] = cutter
    if not tools:
        raise PhotoCarveError('The image produced no engraving paths.')
    return {'tools': tools, 'solid_geometry': aggregate, 'photocarve': mark}


def validate_tools(tools, mark):
    """Call after all Milling overrides; no added tools or changed certified cutters are allowed."""
    if mark.get('version') != 1 or set(map(str, tools)) != set(mark['tools']):
        raise PhotoCarveError('Photo engraving tools differ from the certified job; regenerate the engraving.')
    for uid, tool in tools.items():
        cutter = mark['tools'][str(uid)]
        data = tool.get('data', {})
        expected_shape = 6 if mark['z_is_power'] else MILL_SHAPES.get(cutter['kind'])
        if data.get('tools_mill_tool_shape') != expected_shape:
            raise PhotoCarveError('Certified cutter shape changed; regenerate the photo engraving.')
        values = ((tool.get('tooldia'), cutter['diameter']),
                  (data.get('tools_mill_tooldia'), cutter['diameter']),
                  (data.get('tools_mill_vtipdia'), cutter['tip_diameter']),
                  (data.get('tools_mill_vtipangle'), cutter['angle_deg']))
        for actual, expected in values:
            try:
                valid = math.isfinite(actual) and abs(actual - expected) <= 1e-9
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise PhotoCarveError('Certified cutter dimensions changed; regenerate the photo engraving.')
