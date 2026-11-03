"""Photo engraving orchestration with captured parameters and no worker-side GUI reads."""

import math
from types import SimpleNamespace

import numpy as np

from .PhotoCarveModel import (
    MAX_CELLS, MAX_POINTS, ImageFrame, JobResult, Pass, PhotoCarveError, certify_relief, depth_grid, groove_paths,
    halftone_paths, mode_frame, prepare_image, quantize_z, relief_paths, rough_paths, validate,
    kernel_count,
)
from .PhotoCarveLaser import laser_paths, power_grid
from .PhotoCarveSim import Stock, stamp_path, burn_image, laser_moves_from_paths


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
                raise PhotoCarveError('Generate photo engraving geometry first or select a CNCJob.')
            if source.units != self.app.units:
                raise PhotoCarveError('Simulation source units differ from the project units.')
            res = float(self.tool.ui.sim_res_entry.get_value())
            if not math.isfinite(res) or res <= 0:
                raise PhotoCarveError('Simulation resolution must be finite and positive.')
            passes, warnings = (passes_from_cncjob(source, res) if machine else passes_from_geometry(source))
            kind = 'laser' if passes and passes[0].z_is_power else 'mill'
            if not passes or any(part.z_is_power != (kind == 'laser') for part in passes):
                raise PhotoCarveError('Simulation passes have inconsistent power/depth semantics.')
            low, high, points, bottom = np.full(2, np.inf), np.full(2, -np.inf), 0, 0.0
            for part in passes:
                part.paths = [np.array(path, dtype=np.float64, copy=True) for path in part.paths]
                for path in part.paths:
                    if path.ndim != 2 or path.shape[1] != 3 or not len(path) or not np.isfinite(path).all():
                        raise PhotoCarveError('Simulation requires finite XYZ/power paths.')
                    points += len(path)
                    low = np.minimum(low, path[:, :2].min(axis=0))
                    high = np.maximum(high, path[:, :2].max(axis=0))
                    if kind == 'mill':
                        bottom = min(bottom, float(path[:, 2].min()))
            if not points or points > MAX_POINTS:
                raise PhotoCarveError('Simulation path point limit exceeded or no paths available.')
            radius = max(part.tool.diameter / 2 for part in passes) if kind == 'mill' else res / 2
            low, high = low - radius, high + radius
            frame = ImageFrame(*low, *(high - low), res, res)
            if frame.rows * frame.cols > MAX_CELLS:
                raise PhotoCarveError('Simulation exceeds the cell limit; increase simulation resolution.')
            if kind == 'mill':
                for part in passes:
                    kernel_count(part.tool.diameter / 2, frame.pitch_x, frame.pitch_y)
            session = SimpleNamespace(id=self.tool._sim_session, kind=kind, frame=frame,
                                      busy=False, cancelled=False, passes=passes)
            if kind == 'laser':
                session.burn = np.zeros((frame.rows, frame.cols), dtype=np.float32)
            else:
                session.stock = Stock(frame)
            session.chunks = self.sim_chunks(passes, kind)
            self.app.on_3d_area()
            canvas = self.app.plotcanvas3d
            canvas.view.camera.set_view('iso')
            self.tool._view = PhotoCarveView3d(canvas)
            if kind == 'laser':
                self.tool._view.set_burn(frame, session.burn)
            else:
                self.tool._view.set_stock(session.stock)
            self.tool._view.set_paths(passes, machine_coords=machine)
            canvas.fit_view(points=np.array([[x, y, z] for x in (low[0], high[0])
                                             for y in (low[1], high[1]) for z in (bottom, 0)]))
            self.session = session
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
    def sim_chunks(passes, kind):
        for part in passes:
            for path in part.paths:
                for start in range(0, max(1, len(path) - 1), 255):
                    chunk = path[start:start + 256]
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
                    raise PhotoCarveError('Simulation stopped.')

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
        if session.kind == 'laser':
            self.tool._view.set_burn(session.frame, image)
            if last is not None:
                self.tool._view.set_marker(last)
        else:
            self.tool._view.update_stock(image)
            if last is not None:
                self.tool._view.set_tool(cutter, last)
        if done:
            self.sim_timer.stop()

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
