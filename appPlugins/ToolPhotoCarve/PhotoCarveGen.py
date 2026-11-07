"""Photo engraving orchestration with captured parameters and no worker-side GUI reads."""

import math
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from .PhotoCarveModel import (
    MATERIALS, MAX_CELLS, MAX_POINTS, ImageFrame, JobResult, Pass, PhotoCarveError, certify_relief, depth_grid, groove_paths,
    halftone_paths, mode_frame, prepare_image, quantize_z, relief_paths, rough_paths, validate,
    kernel_count, _,
)
from .PhotoCarveLaser import laser_paths, power_grid
from .PhotoCarveSim import Stock, stamp_path, burn_image, laser_moves_from_paths

# The number of path points in one simulation chunk; consecutive chunks share 1 point.
SIM_CHUNK = 256


def laser_line_spacing(passes):
    """The distance between the scan lines of a laser job (its rows are horizontal), 0 if it has 1 line.

    It also works for a CNCJob, which has no line spacing in its options."""
    ys = np.unique(np.round(np.concatenate([path[:, 1] for part in passes for path in part.paths]), 6))
    gaps = np.diff(ys)
    gaps = gaps[gaps > 1e-6]
    return float(np.median(gaps)) if len(gaps) else 0.0


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
        raise PhotoCarveError(_('Image size changed after the job parameters were captured.'))
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
                    raise PhotoCarveError(_('V-bit paths exceed their certified width or depth limit.'))
        passes.append(Pass('finish', params.tool(), paths))
    points = sum(len(path) for part in passes for path in part.paths)
    if points > MAX_POINTS:
        raise PhotoCarveError(_('Combined passes exceed the point limit; increase spacing, resolution or stepdown.'))
    return JobResult(passes, warnings, {'points': points, 'paths': sum(len(part.paths) for part in passes),
                                       'cells': frame.rows * frame.cols, 'max_gouge': min(margins, default=0.0)})


PREVIEW_CELLS = 400


def preview_map(image, params):
    """What the job makes, on a coarse grid, with the same pipeline as generation.

    Milling modes: the depth (white = stock top, black = deepest). Halftone: the dots. Laser: the power after the
    quantization or the dither (white = no power). The image has the grid aspect; the caller shows it with the
    physical aspect ``params.height / params.width``.
    """
    from PIL import Image, ImageDraw
    coarse = max(params.res, params.width / PREVIEW_CELLS, params.height / PREVIEW_CELLS)
    changes = {'res': coarse}
    if params.mode == 'laser':
        changes['spacing'] = max(params.spacing, params.height / PREVIEW_CELLS)
    params, _warnings = validate(replace(params, rough=False, **changes))
    gray = prepare_image(image, params)
    if params.mode == 'laser':
        power = power_grid(gray, params)
        return Image.fromarray(np.rint((1 - power) * 255).astype(np.uint8))
    if params.mode == 'halftone':
        frame = mode_frame(params)
        scale = 4                      # pixels per grid cell, so small dots stay round
        picture = Image.new('L', (frame.cols * scale, frame.rows * scale), 255)
        draw = ImageDraw.Draw(picture)
        tangent = math.tan(math.radians(params.tool_angle) / 2)
        for path in halftone_paths(gray, params):
            x, y, z = path[0]
            radius = (params.tool_tip + 2 * -z * tangent) / 2
            cx = (x - frame.x0) / frame.pitch_x * scale
            cy = (frame.y0 + frame.height - y) / frame.pitch_y * scale
            rx, ry = radius / frame.pitch_x * scale, radius / frame.pitch_y * scale
            draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=0)
        return picture
    depth = depth_grid(gray, params)
    deepest = (params.litho_max - params.litho_min if params.mode == 'lithophane' else params.max_depth)
    shade = 1 - np.clip(depth / max(deepest, 1e-9), 0, 1)
    return Image.fromarray(np.rint(shade * 255).astype(np.uint8))


class PhotoCarveGen:
    def __init__(self, tool):
        self.tool = tool
        self.app = tool.app
        self.session = None
        self.sim_timer = None

    def simulate(self):
        from PyQt6 import QtCore
        from .PhotoCarveObject import passes_from_cncjob, passes_from_geometry
        from .PhotoCarveView3d import PhotoCarveView3d

        self.tool.on_stop()
        try:
            source = self.app.collection.get_active()
            machine = source is not None and source.kind == 'cncjob'
            if not machine and self.tool.result_obj_name:
                source = self.app.collection.get_by_name(self.tool.result_obj_name)
            if source is None or source.kind not in ('geometry', 'cncjob'):
                raise PhotoCarveError(_('Generate photo engraving geometry first or select a CNCJob.'))
            if source.units != self.app.units:
                raise PhotoCarveError(_('Simulation source units differ from the project units.'))
            res = float(self.tool.ui.sim_res_entry.get_value())
            if not math.isfinite(res) or res <= 0:
                raise PhotoCarveError(_('Simulation resolution must be finite and positive.'))
            thickness = float(self.tool.ui.sim_thickness_entry.get_value())
            if not math.isfinite(thickness) or thickness <= 0:
                raise PhotoCarveError(_('Simulation thickness must be finite and positive.'))
            material = self.tool.ui.sim_material_entry.get_value()
            if material not in MATERIALS:
                raise PhotoCarveError(_('Unknown simulation material.'))
            passes, warnings = (passes_from_cncjob(source, res) if machine else passes_from_geometry(source))
            kind = 'laser' if passes and passes[0].z_is_power else 'mill'
            if not passes or any(part.z_is_power != (kind == 'laser') for part in passes):
                raise PhotoCarveError(_('Simulation passes have inconsistent power/depth semantics.'))
            low, high, points, bottom = np.full(2, np.inf), np.full(2, -np.inf), 0, 0.0
            for part in passes:
                part.paths = [np.array(path, dtype=np.float64, copy=True) for path in part.paths]
                for path in part.paths:
                    if path.ndim != 2 or path.shape[1] != 3 or not len(path) or not np.isfinite(path).all():
                        raise PhotoCarveError(_('Simulation requires finite XYZ/power paths.'))
                    points += len(path)
                    low = np.minimum(low, path[:, :2].min(axis=0))
                    high = np.maximum(high, path[:, :2].max(axis=0))
                    if kind == 'mill':
                        bottom = min(bottom, float(path[:, 2].min()))
            if not points or points > MAX_POINTS:
                raise PhotoCarveError(_('Simulation path point limit exceeded or no paths available.'))
            radius = max(part.tool.diameter / 2 for part in passes) if kind == 'mill' else res / 2
            low, high = low - radius, high + radius
            frame = ImageFrame(*low, *(high - low), res, res)
            if frame.rows * frame.cols > MAX_CELLS:
                raise PhotoCarveError(_('Simulation exceeds the cell limit; increase simulation resolution.'))
            if kind == 'mill':
                for part in passes:
                    kernel_count(part.tool.diameter / 2, frame.pitch_x, frame.pitch_y)
            session = SimpleNamespace(id=self.tool._sim_session, kind=kind, frame=frame,
                                      busy=False, cancelled=False, passes=passes, done_chunks=0,
                                      total_chunks=self.chunk_count(passes))
            if kind == 'laser':
                session.burn = np.zeros((frame.rows, frame.cols), dtype=np.float32)
            else:
                session.stock = Stock(frame, thickness)
                if bottom < -thickness:
                    warnings.append(_('The cuts are deeper than the stock thickness; they go through the stock.'))
            session.chunks = self.sim_chunks(passes, kind)
            self.app.on_3d_area()
            canvas = self.app.plotcanvas3d
            canvas.view.camera.set_view('iso')
            spot = laser_line_spacing(passes) if kind == 'laser' else 0.0
            self.tool._view = PhotoCarveView3d(canvas, material, thickness, depth_ref=-bottom, spot=spot)
            if self.tool._hooked_canvas is not canvas:
                # a closed Plot 3D tab deletes its canvas; a new tab has a new canvas
                self.tool._hooked_canvas = canvas
                canvas.native.destroyed.connect(lambda *args, c=canvas: self.tool.on_canvas_destroyed(c))
            if kind == 'laser':
                self.tool._view.set_burn(frame, session.burn)
            else:
                self.tool._view.set_stock(session.stock)
            self.tool._view.set_paths(passes, machine_coords=machine)
            canvas.fit_view(points=np.array([[x, y, z] for x in (low[0], high[0])
                                             for y in (low[1], high[1]) for z in (-thickness, 0)]))
            self.session = session
            self.tool.ui.sim_progress.setValue(0)
            if self.sim_timer is None:
                self.sim_timer = QtCore.QTimer(self.tool)
                self.sim_timer.setInterval(30)
                self.sim_timer.timeout.connect(self.sim_tick)
            self.sim_timer.start()
            for warning in warnings:
                self.app.inform.emit('[WARNING_NOTCL] %s' % warning)
        except Exception as err:
            self.tool.on_stop()
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return 'fail'

    @staticmethod
    def chunk_count(passes):
        """The number of items that sim_chunks() yields, for the progress bar."""
        step = SIM_CHUNK - 1
        return sum(-(-max(1, len(path) - 1) // step) for part in passes for path in part.paths)

    @staticmethod
    def sim_chunks(passes, kind):
        for part in passes:
            for path in part.paths:
                for start in range(0, max(1, len(path) - 1), SIM_CHUNK - 1):
                    chunk = path[start:start + SIM_CHUNK]
                    yield part.tool, laser_moves_from_paths([chunk]) if kind == 'laser' else chunk

    def sim_tick(self):
        session = self.session
        if session is None or session.busy or session.cancelled:
            return
        speed = int(self.tool.ui.sim_speed_entry.get_value())
        session.busy = True
        self.app.worker_task.emit({'fcn': self.sim_step, 'params': [session, None if speed == 100 else speed]})

    def sim_step(self, session, count):
        ok, payload = False, None
        try:
            done, last, cutter, index = False, None, None, 0

            def check_abort():
                if session.cancelled:
                    raise PhotoCarveError(_('Simulation stopped.'))

            while count is None or index < count:
                check_abort()
                item = next(session.chunks, None)
                if item is None:
                    done = True
                    break
                cutter, chunk = item
                if session.kind == 'laser':
                    burn_image(session.frame, chunk, image=session.burn, check_abort=check_abort)
                    if chunk:
                        last = (chunk[-1].x1, chunk[-1].y1, 0)
                else:
                    stamp_path(session.stock, chunk, cutter, check_abort=check_abort)
                    last = tuple(chunk[-1])
                index += 1
            session.done_chunks += index
            image = session.burn if session.kind == 'laser' else session.stock.height
            payload = (image.copy(), cutter, last, done)
            ok = True
        except Exception as err:
            payload = str(err)
        finally:
            self.tool.sim_step_sig.emit(session.id, ok, payload)

    def on_sim_step(self, session_id, ok, payload):
        session = self.session
        if session is None or session_id != self.tool._sim_session or session_id != session.id:
            return
        session.busy = False
        if not ok:
            self.sim_timer.stop()
            session.cancelled = True
            self.app.inform.emit('[ERROR_NOTCL] %s' % payload)
            return
        image, cutter, last, done = payload
        view = self.tool._view
        if view is None or not view.alive:
            # the 3D tab is closed: stop the animation, there is no scene to update
            self.stop_simulation()
            return
        if session.kind == 'laser':
            view.set_burn(session.frame, image)
            if last is not None:
                view.set_marker(last)
        else:
            view.update_stock(image)
            if last is not None:
                view.set_tool(cutter, last)
        total = max(1, session.total_chunks)
        self.tool.ui.sim_progress.setValue(100 if done else min(99, 100 * session.done_chunks // total))
        if done:
            self.sim_timer.stop()
            # the result alone: the path lines and the tool hide the cuts
            view.finish()
            self.app.inform.emit('[success] %s' % _("Simulation finished."))

    def stop_simulation(self):
        if self.sim_timer is not None:
            self.sim_timer.stop()
            self.sim_timer.deleteLater()
            self.sim_timer = None
        if self.session is not None:
            self.session.cancelled = True
            self.session = None

    def preview(self, token, image, params):
        result = None
        try:
            result = preview_map(image, params)
        except Exception as err:
            result = str(err)
        finally:
            self.tool.preview_done_sig.emit(token, result)

    def generate(self, token, image, params, milldefaults, outname, filename=None):
        from camlib import grace
        from .PhotoCarveObject import build_geometry, embed_source

        def current():
            return token == self.tool._gen_token and self.app.units == params.units

        def check_abort():
            if self.app.abort_flag or not current():
                raise grace

        try:
            check_abort()
            with self.app.proc_container.new('%s...' % _("Working")):
                result = run_job(image, params, check_abort)
                check_abort()
                for warning in result.warnings:
                    self.tool.generation_message_sig.emit(token, '[WARNING_NOTCL]', warning)
                source = embed_source(image, filename, params)
                check_abort()

                def obj_init(geo, app):
                    if not current():
                        return 'fail'
                    try:
                        built = build_geometry(result, params, milldefaults, source)
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
                created = self.app.app_obj.new_object('geometry', outname, obj_init)
                if created != 'fail':
                    self.tool.generation_message_sig.emit(
                        token, '[success]', _("Photo Carve geometry generated: %d paths, %d points.") % (
                            result.stats.get('paths', 0), result.stats.get('points', 0)))
                return created
        except grace:
            return 'fail'
        except Exception as err:
            self.app.log.error('PhotoCarveGen.generate() --> %s', err)
            self.tool.generation_message_sig.emit(token, '[ERROR_NOTCL]', str(err))
            return 'fail'
        finally:
            self.tool.generation_done_sig.emit(token)
