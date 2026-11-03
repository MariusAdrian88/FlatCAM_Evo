"""Shapely boundary for captured photo engraving results and certified cutter metadata."""

import math
from copy import deepcopy

import numpy as np
from shapely import LineString, Point, force_2d

from .PhotoCarveModel import PhotoCarveError, xy_budget


# MillingUI's C1, C2, C3, C4, B, V, L combo identifiers.
MILL_SHAPES = {'flat': 0, 'ball': 4, 'vbit': 5}


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
