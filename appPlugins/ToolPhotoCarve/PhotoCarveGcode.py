"""Certified XYZ/incoming-power paths at the existing Milling CNC-job boundary."""

import math
import re
from copy import deepcopy

from camlib import AttrDict, grace
from appParsers.ParseGCode import parse_gcode

from .PhotoCarveModel import PhotoCarveError, xy_budget, _
from .PhotoCarveObject import validate_tools
from .PhotoCarveLaser import format_power


MILL_POSTS = {'default', 'GRBL_11', 'GRBL_11_no_M6', 'grblHAL', 'Marlin'}
LASER_POSTS = {'GRBL_laser', 'grblHAL_laser'}


def is_dynamic_laser(code):
    """True for M4 in any form that a G-code reader accepts: "M4", "M04", "m04 "."""
    return re.fullmatch(r'M0*4', str(code).strip().upper()) is not None


def write_tool_gcode(cnc, tool, tools, first_pt, last_pt, tolerance, is_first, is_last,
                     toolchange, use_ui, mark):
    """Emit complete blocks without sorting, simplifying or re-rounding certified paths."""
    try:
        laser = mark['z_is_power']
        name = tools[tool]['data']['tools_mill_ppname_g']
        allowed = LASER_POSTS if laser else MILL_POSTS
        if name not in allowed:
            raise PhotoCarveError(_('The preprocessor "%s" is not certified for photo engraving. In the Milling plugin, '
                                   'select one of: %s.') % (name, ', '.join(sorted(allowed))))
        validate_tools(tools, mark)
        if name not in cnc.app.preprocessors:
            raise PhotoCarveError(_('The certified photo engraving preprocessor is not installed.'))
        p = cnc.load_mill_tool_params(tool, tools, last_pt, tolerance, is_first, use_ui)
        if p == 'fail':
            return 'fail'
        skew = tuple(getattr(cnc, key) for key in ('_bed_skew_x', '_bed_skew_y', '_bed_limit_x', '_bed_limit_y'))
        budget = xy_budget(cnc.coords_decimals, skew)
        if budget > mark['xy_err'] + 1e-12:
            needed = math.ceil(-math.log10(mark['xy_err'] / xy_budget(0, skew)))
            raise PhotoCarveError(_('Photo engraving requires at least %d coordinate decimals.') % needed)
        cnc.startz = None  # A saved low Start Z must never replace the safe toolchange height.
        cnc.toolchange = toolchange
        if not laser and toolchange and (cnc.z_toolchange in (None, '') or float(cnc.z_toolchange) < cnc.z_move):
            raise PhotoCarveError(_('Toolchange Z must be at least the Travel Z for photo engraving.'))
        if laser:
            # the Milling combo gives "M03" / "M04"; M4 and M04 are the same command
            if not is_dynamic_laser(cnc.laser_on_code):
                raise PhotoCarveError(_('Photo laser engraving needs the dynamic laser power. In the Milling plugin, '
                                       'set the Laser ON code to M04.'))
            format_power(0, cnc.laser_min_power, cnc.spindlespeed)
        attrs = AttrDict(cnc.postdata)
        attrs.update(x=first_pt[0], y=first_pt[1])
        parts = []

        def block(value):
            return '' if value is None or value == '' else value.rstrip('\n') + '\n'

        def emit(value):
            parts.append(block(value))

        def up_z(z):
            q = 10.0 ** -cnc.coords_decimals
            return math.ceil(z / q - 1e-9) * q

        header = block(p.start_code(attrs)) if is_first else ''
        emit(p.feedrate_code(attrs))
        if laser:
            emit('M5')
            emit('M4 S0')
        else:
            emit(p.lift_code(attrs))
            if toolchange:
                emit(p.toolchange_code(attrs))
                emit(p.lift_code(attrs))
            emit(p.spindle_code(attrs))
            if cnc.dwell:
                emit(p.dwell_code(attrs))
        current = first_pt
        count = 0
        for geometry in tools[tool]['solid_geometry']:
            points = list(geometry.coords)
            if not points:
                continue
            for index, point in enumerate(points):
                if index % 1000 == 0 and cnc.app.abort_flag:
                    raise grace
                if len(point) != 3 or not all(math.isfinite(v) for v in point):
                    raise PhotoCarveError(_('Photo engraving requires finite XYZ paths.'))
            attrs.update(x=points[0][0], y=points[0][1])
            if laser:
                emit('S0')
            emit(p.rapid_code(attrs))
            if not laser:
                attrs.z_cut = up_z(points[0][2])
                emit(p.z_feedrate_code(attrs))
                emit(p.down_code(attrs))
                emit(p.feedrate_code(attrs))
            for x, y, z in points[1:]:
                count += 1
                if count % 1000 == 0 and cnc.app.abort_flag:
                    raise grace
                attrs.update(x=x, y=y)
                suffix = (' S%d' % format_power(z, cnc.laser_min_power, cnc.spindlespeed) if laser else
                          ' Z' + p.coord(attrs, up_z(z)))
                emit(p.linear_code(attrs) + suffix)
            current = points[-1][:2]
            emit('S0' if laser else p.lift_code(attrs))
        if laser:
            emit('S0')
            emit('M5')
            if is_last:
                emit(p.end_code(attrs))
        else:
            emit(cnc.mill_tool_end_gcode(p, current, is_last))
        cnc.gcode = ''.join(parts)
        return cnc.gcode, header
    except PhotoCarveError as error:
        cnc.app.inform.emit('[ERROR_NOTCL] %s' % str(error))
        return 'fail'


def make_photocarve_cncjob(new_cncjob_obj, app_obj, context):
    """Milling multi-geometry initializer with captured overrides and certified tool validation."""
    cnc = new_cncjob_obj
    # The CNCJob does not keep the source image: it is in the Geometry, and the project would hold it 2 times.
    mark = {key: deepcopy(value) for key, value in context['geo_obj'].obj_options['photocarve'].items()
            if key != 'source'}
    tools = deepcopy(context['tools_dict'])
    try:
        for record in tools.values():
            record['data'].update(context['overrides'])
        validate_tools(tools, mark)
    except PhotoCarveError as error:
        app_obj.inform.emit('[ERROR_NOTCL] %s' % str(error))
        return 'fail'
    used = sorted(tools, key=lambda uid: mark['tools'][str(uid)]['role'] != 'rough')
    cnc.obj_options.update(context['bounds'])
    cnc.obj_options.update(type='Geometry', photocarve=mark)
    cnc.multitool = cnc.multigeo = True
    cnc.units = context['units']
    cnc.seg_x, cnc.seg_y = context['seg_x'], context['seg_y']
    cnc.coords_decimals = context['options']['cncjob_coords_decimals']
    cnc.fr_decimals = context['options']['cncjob_fr_decimals']
    cnc.z_p_depth = float(context['geo_options']['tools_mill_z_p_depth'])
    cnc.probe_plate = float(context['geo_options'].get('tools_mill_probe_plate', 0))
    cnc.feedrate_probe = float(context['geo_options']['tools_mill_feedrate_probe'])
    cnc.tools.clear()
    cnc.tools.update((uid, tools[uid]) for uid in used)
    cnc.used_tools = used
    total, start, previous = [], '', (0, 0)
    tol = float(context['options']['global_tolerance'])
    if context['units'].lower() == 'in':
        tol /= 20
    for index, uid in enumerate(used):
        record = tools[uid]
        data = record['data']
        cnc.obj_options.update(tooldia=record['tooldia'], tool_dia=record['tooldia'])
        result = write_tool_gcode(cnc, uid, tools, previous, data['tools_mill_endxy'], tol,
                                  index == 0, index == len(used) - 1, context['toolchange'],
                                  not context['from_tcl'], mark)
        if result == 'fail':
            return 'fail'
        res, header = result
        start += header
        total.append(res)
        cnc.gcode = res
        parsed = cnc.gcode_parse(tool_data=data)
        record['gcode'] = res
        record['gcode_parsed'] = list(parsed) if isinstance(parsed, list) else parsed
        previous = list(record['solid_geometry'][-1].coords)[-1][:2]
    cnc.gc_start = start
    cnc.source_file = start + ''.join(total)
    parsed = parse_gcode(cnc.source_file, steps_per_circle=cnc.steps_per_circle,
                         default_tooldia=tools[used[0]]['tooldia'], feedrate_rapid=cnc.feedrate_rapid,
                         target_units=context['units'])
    cnc.travel_distance = parsed['travel_distance']
    cnc.routing_time = parsed['routing_time']
