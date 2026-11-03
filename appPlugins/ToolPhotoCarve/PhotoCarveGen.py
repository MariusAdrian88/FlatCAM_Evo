"""Photo engraving orchestration with captured parameters and no worker-side GUI reads."""

import math

import numpy as np

from .PhotoCarveModel import (
    MAX_POINTS, JobResult, Pass, PhotoCarveError, certify_relief, depth_grid, groove_paths,
    halftone_paths, mode_frame, prepare_image, quantize_z, relief_paths, rough_paths, validate,
)
from .PhotoCarveLaser import laser_paths, power_grid


def capture_machine(app):
    """Call on the GUI thread before dispatching generation."""
    options = app.options
    return (app.units, int(options['cncjob_coords_decimals']),
            tuple(float(options[key]) for key in ('cncjob_bed_skew_x', 'cncjob_bed_skew_y',
                                                  'cncjob_bed_max_x', 'cncjob_bed_max_y')))


def run_job(image, params, check_abort=None):
    if check_abort is not None:
        check_abort()
    params, warnings = validate(params, check_abort)
    if image.size != params.image_px:
        raise PhotoCarveError('Image size changed after the job parameters were captured.')
    frame = mode_frame(params)
    gray = prepare_image(image, params)
    passes, margins = [], []
    if params.mode == 'laser':
        power = power_grid(gray, params, check_abort)
        passes.append(Pass('laser', params.tool(), laser_paths(power, params, check_abort), True))
    else:
        depth = depth_grid(gray, params)
        if params.rough:
            paths, certificate = rough_paths(depth, params, check_abort)
            for path in paths:
                path[:, 2] = quantize_z(path[:, 2], params.decimals)
            margins.append(certify_relief(paths, certificate, frame, check_abort))
            passes.append(Pass('rough', params.tool(True), paths))
        if params.mode in ('relief', 'lithophane'):
            paths, certificate = relief_paths(depth, params, params.tool(), check_abort)
            for path in paths:
                path[:, 2] = quantize_z(path[:, 2], params.decimals)
            margins.append(certify_relief(paths, certificate, frame, check_abort))
        else:
            paths = (halftone_paths(gray, params, check_abort) if params.mode == 'halftone' else
                     groove_paths(depth, params, check_abort))
            for path in paths:
                if check_abort is not None:
                    check_abort()
                path[:, 2] = quantize_z(path[:, 2], params.decimals)
                depths = -path[:, 2]
                widths = params.tool_tip + 2 * depths * math.tan(math.radians(params.tool_angle) / 2)
                if (np.any(depths < -1e-9) or np.any(depths > params.max_depth + 1e-9) or
                        np.any(widths > min(params.spacing, params.tool_dia) + 1e-9)):
                    raise PhotoCarveError('V-bit paths exceed their certified width or depth limit.')
        passes.append(Pass('finish', params.tool(), paths))
    points = sum(len(path) for part in passes for path in part.paths)
    if points > MAX_POINTS:
        raise PhotoCarveError('Combined passes exceed the point limit; increase spacing, resolution or stepdown.')
    return JobResult(passes, warnings, {'points': points, 'paths': sum(len(part.paths) for part in passes),
                                       'cells': frame.rows * frame.cols, 'max_gouge': min(margins, default=0.0)})


def preview_map(image, params):
    """The adjusted physical image, using the same preflight as generation."""
    from PIL import Image
    params, _warnings = validate(params)
    gray = prepare_image(image, params)
    return Image.fromarray(np.rint(gray * 255).astype(np.uint8))


class PhotoCarveGen:
    def __init__(self, tool):
        self.tool = tool
        self.app = tool.app

    def preview(self, token, image, params):
        result = None
        try:
            result = preview_map(image, params)
        except Exception as err:
            result = str(err)
        finally:
            self.tool.preview_done_sig.emit(token, result)

    def generate(self, token, image, params, milldefaults, outname):
        from camlib import grace
        from .PhotoCarveObject import build_geometry

        def current():
            return token == self.tool._gen_token and self.app.units == params.units

        def check_abort():
            if self.app.abort_flag or not current():
                raise grace

        try:
            check_abort()
            with self.app.proc_container.new(self.tool.pluginName):
                result = run_job(image, params, check_abort)
                check_abort()
                for warning in result.warnings:
                    self.tool.generation_message_sig.emit(token, '[WARNING_NOTCL]', warning)

                def obj_init(geo, app):
                    if not current():
                        return 'fail'
                    try:
                        built = build_geometry(result, params, milldefaults)
                    except PhotoCarveError as err:
                        self.tool.generation_message_sig.emit(token, '[ERROR_NOTCL]', str(err))
                        return 'fail'
                    geo.tools = built['tools']
                    geo.solid_geometry = built['solid_geometry']
                    geo.multigeo = True
                    geo.multitool = True
                    geo.obj_options['photocarve'] = built['photocarve']
                    geo._publish_guard = current

                    def after_publish(obj):
                        if current():
                            self.tool.result_obj_name = obj.obj_options['name']

                    geo._after_publish = after_publish

                check_abort()
                return self.app.app_obj.new_object('geometry', outname, obj_init)
        except grace:
            return 'fail'
        except Exception as err:
            self.app.log.error('PhotoCarveGen.generate() --> %s', err)
            self.tool.generation_message_sig.emit(token, '[ERROR_NOTCL]', str(err))
            return 'fail'
