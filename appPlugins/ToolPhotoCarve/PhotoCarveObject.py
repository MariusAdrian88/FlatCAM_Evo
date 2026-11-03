"""Shapely boundary for captured photo engraving results and certified cutter metadata."""

import base64
import io
import math
import ntpath
import re
from copy import deepcopy

import numpy as np
from PIL import Image
from shapely import LineString, Point, force_2d

from .PhotoCarveModel import DEFAULTS, Pass, ToolShape, PhotoCarveError, xy_budget

# The job parameters that hold a length: they change with the units of the application.
LENGTH_FIELDS = ('x0', 'y0', 'width', 'res', 'spacing', 'laser_dia', 'min_depth', 'max_depth', 'tool_dia',
                 'tool_tip', 'rough_dia', 'rough_tip', 'stepdown', 'allowance', 'litho_min', 'litho_max', 'frame',
                 'blur', 'sharpen', 'tolerance', 'lift_gap', 'overscan', 'sim_res', 'sim_thickness')
# The image modes that PNG keeps as they are. Another mode (CMYK, YCbCr, ...) is stored as RGBA, as the job reads it.
PNG_MODES = ('1', 'L', 'LA', 'P', 'RGB', 'RGBA', 'I', 'I;16')


def embed_source(image, filename, params):
    """The source record of the mark: the image as PNG data and the job parameters, so that a saved project
    can open the job again in Photo Carve, also on another computer."""
    if image.mode not in PNG_MODES:
        image = image.convert('RGBA')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return {'png': base64.b64encode(buffer.getvalue()).decode('ascii'),
            'filename': ntpath.basename(filename or ''),
            'units': params.units,
            'params': {name: getattr(params, name) for name in DEFAULTS}}


def read_source(mark, units):
    """(image, file name, plugin options) of the source of a Photo Carve mark, or None if it has no source.

    The lengths are converted to "units"; a parameter that the record does not have takes its default value."""
    source = (mark or {}).get('source')
    if not source:
        return None
    try:
        with Image.open(io.BytesIO(base64.b64decode(source['png']))) as picture:
            image = picture.copy()
    except (KeyError, ValueError, OSError) as err:
        raise PhotoCarveError('Cannot read the source image of the Photo Carve geometry: %s' % err) from err
    saved, units = str(source.get('units', units)).upper(), str(units).upper()
    factor = 1.0 if saved == units else (25.4 if units == 'MM' else 1 / 25.4)
    options = {}
    for name, default in DEFAULTS.items():
        value = source.get('params', {}).get(name, default)
        if name in LENGTH_FIELDS and factor != 1.0:
            value = float(value) * factor
        options['tools_photocarve_' + name] = value
    return image, source.get('filename', ''), options


# MillingUI's C1, C2, C3, C4, B, V, L combo identifiers.
MILL_SHAPES = {'flat': 0, 'ball': 4, 'vbit': 5}

# What the user does when the cutter of a Photo Carve geometry no longer matches its paths. The Reset Tool button of
# the Milling plugin does not help: it resets the form, not the tools of the geometry.
REGENERATE = ('Open the Photo Carve plugin and click "Generate Geometry" to make the geometry again; then make the '
              'CNCJob from the new geometry.')


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
        raise PhotoCarveError('The units of the CNCJob and of the photo engraving differ. Make the CNCJob again '
                              'in the Milling plugin.')
    if any(getattr(cnc_obj, key, '') for key in ('edited_source_file', 'prepend_snippet', 'append_snippet')):
        raise PhotoCarveError('Edited or snippet-bearing CNC programs cannot be associated safely with cutters.')
    laser = bool(mark.get('z_is_power'))
    warnings, passes = [], []
    if mark and mark.get('certified') is False:
        warnings.append('This CNCJob was converted to other units and is not certified; '
                        'the simulation shows its converted G-code.')
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


def build_geometry(result, params, milldefaults, source=None):
    """Build independent multigeometry records using captured inputs only.

    The aggregate is XY for plotting; each tool keeps its XYZ or incoming laser power in Z.
    The caller stores ``photocarve`` in the geometry object's obj_options.
    ``source`` is the record of embed_source(): the mark keeps it, so that the job opens again in Photo Carve.
    """
    from .PhotoCarveGcode import LASER_POSTS, MILL_POSTS

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
        # Start with values that the writer accepts: the user's preprocessor if it is certified, and the dynamic
        # laser power (M04). The application defaults are a milling preprocessor and M03.
        allowed = LASER_POSTS if laser else MILL_POSTS
        if data.get('tools_mill_ppname_g') not in allowed:
            data['tools_mill_ppname_g'] = 'GRBL_laser' if laser else 'default'
        if laser:
            data['tools_mill_laser_on'] = 'M04'
        tools[uid] = {'tooldia': cutter['diameter'], 'data': data, 'solid_geometry': geometry}
        aggregate.extend(force_2d(item) for item in geometry)
        mark['tools'][str(uid)] = cutter
    if not tools:
        raise PhotoCarveError('The image produced no engraving paths.')
    if source:
        mark['source'] = source
    return {'tools': tools, 'solid_geometry': aggregate, 'photocarve': mark}


def validate_tools(tools, mark):
    """Call after all Milling overrides; no added tools or changed certified cutters are allowed.

    The Milling job holds only the tools selected in its table: each certified pass is safe on its own, so a
    subset of the marked tools (for example only the finishing tool) is accepted.
    """
    if mark.get('version') != 1 or not tools or not set(map(str, tools)) <= set(mark['tools']):
        raise PhotoCarveError('The tools differ from the tools of the photo engraving. ' + REGENERATE)
    for uid, tool in tools.items():
        cutter = mark['tools'][str(uid)]
        data = tool.get('data', {})
        expected_shape = 6 if mark['z_is_power'] else MILL_SHAPES.get(cutter['kind'])
        if data.get('tools_mill_tool_shape') != expected_shape:
            raise PhotoCarveError('The shape of the cutter changed. ' + REGENERATE)
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
                raise PhotoCarveError('The dimensions of the cutter changed. ' + REGENERATE)
