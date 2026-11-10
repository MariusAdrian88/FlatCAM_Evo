# ##########################################################
# FlatCAM EVO: canvas frame probe (development tool)       #
# MIT Licence                                              #
# ##########################################################
"""
Measures the 2D canvas pan path. Active only with FLATCAM_CANVAS_PROF=1; otherwise install() does nothing.

  FLATCAM_CANVAS_PROF=1            one line per second while frames occur (prefix CANVASPROF)
  FLATCAM_CANVAS_PROF_PAN=1        scripted right/middle-button pan (300 moves, 4 ms timer) some seconds after the
                                   first paint; prints 'CANVASPROF SUMMARY ...'
  FLATCAM_CANVAS_PROF_PAN_BURST=3  moves per timer tick (default 1), 300 ticks: imitates a faster mouse
  FLATCAM_CANVAS_PROF_PAN_DELAY=8 seconds between the first paint and the pan
  FLATCAM_CANVAS_PROF_SHOT=<f.npy> after the scripted pan: save canvas.render() (RGBA array) with numpy.save
  FLATCAM_CANVAS_PROF_PAINT_PROFILE=<f.txt> cProfile of each of the first 4 paints after the press of the 2D pan
  FLATCAM_CANVAS_PROF_EXIT=1       quit the application after the summary

  FLATCAM_CANVAS_PROF_3D=1         measure the 3D canvas (PlotCanvas3d) instead of the 2D one: some seconds after the
                                   first 2D paint (FLATCAM_CANVAS_PROF_PAN_DELAY, files loaded from the command line) the
                                   PCB 3D Viewer opens, and when its build is ready three scripted drags run through the
                                   real input path (rotate with the left button, pan with the pan button, wheel zoom),
                                   each out and back; one 'SUMMARY3D <action>' block each
  FLATCAM_CANVAS_PROF_3D_SECONDS=3 seconds of a rotate / pan drag (default 3)
  FLATCAM_CANVAS_PROF_3D_HIDE=1    hide all PCB layers during the drags (is the GPU the limit?)
  FLATCAM_CANVAS_PROF_3D_VCPROF=<f.txt>  cProfile of the ViewCube paint events during the run
  FLATCAM_CANVAS_PROF_3D_CPROFILE=<f.txt> cProfile of the rotate moves (Camera_3D._update_rotation), top 25
  FLATCAM_CANVAS_PROF_SHOT=<base>  with 3D: <base>_start.npy, <base>_top.npy, <base>_bottom.npy (canvas.render() with the
                                   view of the start, then the top and the bottom view)
"""

import functools
import logging
import os
import sys
import time
from collections import defaultdict

log = logging.getLogger('base')
_clock = time.perf_counter
PREFIX = 'CANVASPROF'
TIMED = ('paint', 'swap', 'axis_prepare', 'axis_update', 'glir_parse', 'flush', 'camera_view', 'mouse_move')


class Window:
    """Totals of one measuring window (the 1 s line or the scripted pan)."""

    def __init__(self):
        self.t0 = _clock()
        self.total = defaultdict(float)
        self.count = defaultdict(int)
        self.max = defaultdict(float)
        self.series = defaultdict(list)


_windows = []
_state = {'canvas': None, 'inited': False, 'last_start': None, 'last_end': None, 'last_swapped': None,
          'upd': None, 'in_paint': False}


def _series(name, value):
    """Keep every value (seconds) of a series, for histograms and percentiles."""
    for w in _windows:
        w.series[name].append(value)


def _stats(values):
    """mean / p95 / max in ms of a list of seconds."""
    if not values:
        return 'n=0'
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    return 'n=%d mean=%.2f p95=%.2f max=%.2f' % (len(ordered), 1000 * sum(ordered) / len(ordered), 1000 * p95,
                                                1000 * ordered[-1])


def _bands(values):
    """Counts of the frame intervals per band: < 9 ms, 9-13 ms (1 vertical blank at 90 Hz), 13-25 ms (2), > 25 ms."""
    counts = [0, 0, 0, 0]
    for v in values:
        ms = 1000 * v
        counts[0 if ms < 9 else 1 if ms < 13 else 2 if ms < 25 else 3] += 1
    return 'lt9=%d 9to13=%d 13to25=%d gt25=%d' % tuple(counts)


def _record(name, dt):
    for w in _windows:
        w.total[name] += dt
        w.count[name] += 1
        if dt > w.max[name]:
            w.max[name] = dt


def _count(name, k=1):
    for w in _windows:
        w.count[name] += k


def _timed(name, func):
    # functools.wraps: VisPy event callbacks that are methods are looked up again by their __name__
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        t = _clock()
        try:
            return func(*args, **kwargs)
        finally:
            _record(name, _clock() - t)
    return wrapper


def _ms(w, name):
    """Mean ms per call."""
    return 1000.0 * w.total[name] / w.count[name] if w.count[name] else 0.0


def _per_frame(w, name):
    """Total ms of the part per frame."""
    frames = w.count['paint']
    return 1000.0 * w.total[name] / frames if frames else 0.0


def _emit(text):
    line = '%s %s' % (PREFIX, text)
    print(line, flush=True)
    log.info(line)


def _format(w, dt):
    frames = w.count['paint']
    parts = ' '.join('%s=%.3f' % (n, _per_frame(w, n)) for n in
                     ('axis_prepare', 'axis_update', 'glir_parse', 'flush', 'camera_view'))
    return ('fps=%.1f paint_mean=%.2f paint_max=%.2f swap_mean=%.2f swap_max=%.2f | ms/frame: %s | per s: '
            'moves_in=%.0f moves_dropped=%.0f cam_steps=%.0f draws=%.0f flushes=%.0f glir=%.0f') % (
        frames / dt, _ms(w, 'paint'), 1000 * w.max['paint'], _ms(w, 'swap'), 1000 * w.max['swap'], parts,
        w.count['moves_in'] / dt, w.count['moves_dropped'] / dt, w.count['camera_view'] / dt,
        w.count['draws'] / dt, w.count['flush'] / dt, w.count['glir_cmds'] / dt)


_second = None


def _tick():
    """Called after each 2D paint: print and reset the 1 s window."""
    global _second
    now = _clock()
    dt = now - _second.t0
    if dt >= 1.0:
        _emit(_format(_second, dt))
        _windows.remove(_second)
        _second = Window()
        _windows.append(_second)


def _print_environment(native):
    try:
        from PyQt6.QtGui import QSurfaceFormat
        from vispy.gloo import gl
        screen = native.screen()
        fmt = native.format()
        cfmt = native.context().format()
        canvas = _state['canvas']
        _emit('ENV refresh_hz=%.2f swap_interval_window=%d swap_interval_context=%d default_swap_interval=%d '
              'renderer=%r vendor=%r dpr=%.2f size=%dx%d physical=%s' % (
                  screen.refreshRate(), fmt.swapInterval(), cfmt.swapInterval(),
                  QSurfaceFormat.defaultFormat().swapInterval(),
                  gl.glGetParameter(7937), gl.glGetParameter(7936), native.devicePixelRatioF(),
                  native.width(), native.height(), tuple(canvas.physical_size)))
    except Exception as e:  # a probe must not break the paint
        _emit('ENV error=%r' % (e,))


def _on_swapped():
    now = _clock()
    if _state['last_end'] is not None:
        _series('paint_end_to_swapped', now - _state['last_end'])
    _state['last_swapped'] = now


def _on_first_paint(native):
    _print_environment(native)
    try:
        native.frameSwapped.connect(_on_swapped)
    except Exception as e:
        _emit('frameSwapped error=%r' % (e,))
    if os.environ.get('FLATCAM_CANVAS_PROF_PAN') == '1' and not mode_3d():
        from PyQt6.QtCore import QTimer
        delay = float(os.environ.get('FLATCAM_CANVAS_PROF_PAN_DELAY', '8'))
        QTimer.singleShot(int(delay * 1000), _start_pan)


# ----------------------------------------------------------------------------------------------------------------------
# Scripted pan through the real input path of the canvas widget
# ----------------------------------------------------------------------------------------------------------------------
def _cprofile_camera(path, pan):
    """FLATCAM_CANVAS_PROF_CPROFILE=<f.txt>: cProfile only around Camera.flush_pan (the camera step) during the scripted
    pan. The top 25 functions by cumulative time are written to <f.txt> at the end of the pan (see finish())."""
    import cProfile
    from appGUI.VisPyCanvas import Camera
    profiler = cProfile.Profile()
    original = Camera.flush_pan
    depth = [0]
    if os.environ.get('FLATCAM_CANVAS_PROF_CPROFILE_TARGET') == 'axis':
        # FLATCAM_CANVAS_PROF_CPROFILE_TARGET=axis: profile AxisVisual._prepare_draw (tick computation) instead
        from vispy.visuals.axis import AxisVisual
        original_axis = AxisVisual._prepare_draw

        def axis_prepare(self, view):
            profiler.enable()
            try:
                return original_axis(self, view)
            finally:
                profiler.disable()

        AxisVisual._prepare_draw = axis_prepare
        _state['cprofile_dump'] = lambda: _dump_stats(profiler, path, 'axis prepare calls', pan.count['axis_prepare'])
        return

    def flush_pan(self):
        if depth[0] or self._pan_from is None:
            return original(self)
        depth[0] += 1
        profiler.enable()
        try:
            return original(self)
        finally:
            profiler.disable()
            depth[0] -= 1

    def dump():
        Camera.flush_pan = original
        _dump_stats(profiler, path, 'camera steps (view_changed calls)', pan.count['camera_view'])

    Camera.flush_pan = flush_pan
    _state['cprofile_dump'] = dump


def _dump_stats(profiler, path, label, count):
    import io
    import pstats
    out = io.StringIO()
    stats = pstats.Stats(profiler, stream=out)
    out.write('%s: %d\n' % (label, count))
    stats.sort_stats('cumulative').print_stats(25)
    out.write('\n--- by tottime ---\n')
    stats.sort_stats('tottime').print_stats(25)
    with open(path, 'w') as fh:
        fh.write(out.getvalue())
    _emit('CPROFILE written %s' % path)


def _start_pan():
    from PyQt6.QtCore import QTimer, Qt, QPointF
    from PyQt6.QtGui import QMouseEvent
    canvas = _state['canvas']
    native = canvas.native
    button_id = int(canvas.view.camera.pan_button_setting)
    qbutton = {1: Qt.MouseButton.LeftButton, 2: Qt.MouseButton.RightButton, 3: Qt.MouseButton.MiddleButton}[button_id]
    no_mod = Qt.KeyboardModifier.NoModifier
    cx, cy = native.width() / 2.0, native.height() / 2.0
    amp = min(native.width(), native.height()) / 4.0
    n_moves = 300 * max(1, int(os.environ.get('FLATCAM_CANVAS_PROF_PAN_BURST', '1')))
    half = n_moves // 2

    def qevent(kind, x, y, buttons):
        p = QPointF(x, y)
        return QMouseEvent(kind, p, p, qbutton, buttons, no_mod)

    def position(i):
        k = i if i <= half else n_moves - i
        f = k / float(half)
        return cx + amp * f, cy + 0.5 * amp * f

    canvas.view.camera.flush_pan()
    from vispy.geometry import Rect
    # a collection of the Python garbage collector longer than 5 ms during the pan gets a line
    import gc
    gc_start = [0.0]

    def gc_event(phase, info):
        if phase == 'start':
            gc_start[0] = time.perf_counter()
        else:
            ms = (time.perf_counter() - gc_start[0]) * 1000.0
            if ms > 5:
                _emit('GC gen=%d ms=%.1f collected=%d' % (info['generation'], ms, info['collected']))

    gc.callbacks.append(gc_event)
    _emit('GC counts=%r thresholds=%r objects=%d' % (gc.get_count(), gc.get_threshold(), len(gc.get_objects())))
    # FLATCAM_CANVAS_PROF_ZOOM=<n>: zoom in n times about the view center first (a board larger than the viewport)
    zoom_in = float(os.environ.get('FLATCAM_CANVAS_PROF_ZOOM', '1'))
    if zoom_in != 1:
        canvas.view.camera.zoom(1.0 / zoom_in)
        _emit('ZOOM %g rect=%r' % (zoom_in, canvas.view.camera.rect))
    start_rect = Rect(canvas.view.camera.rect)
    _emit('PAN start button=%d size=%dx%d' % (button_id, native.width(), native.height()))
    pan = Window()
    _windows.append(pan)
    # the first interval would span the idle time before the pan
    _state['last_start'] = _state['last_swapped'] = _state['upd'] = None
    _state['pan_paint_idx'] = 0
    native._vispy_mouse_press(native=qevent(QMouseEvent.Type.MouseButtonPress, cx, cy, qbutton), pos=(cx, cy),
                              button=button_id, buttons=[button_id], modifiers=())
    step = [0]
    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(4)

    burst = max(1, int(os.environ.get('FLATCAM_CANVAS_PROF_PAN_BURST', '1')))
    # 'time' mode: a 1 ms timer, one move per tick, the position follows the clock (a mouse that reports at its own
    # rate, not at the rate of the probe timer); the old 'count' mode sends 300*burst moves, 4 ms apart
    time_mode = os.environ.get('FLATCAM_CANVAS_PROF_PAN_MODE', 'count') == 'time'
    seconds = float(os.environ.get('FLATCAM_CANVAS_PROF_PAN_SECONDS', '3'))
    t_start = [_clock()]
    last_tick = [None]
    if time_mode:
        timer.setInterval(int(os.environ.get('FLATCAM_CANVAS_PROF_PAN_INTERVAL', '1')))
        n_moves = 1 << 30
        _emit('PAN mode=time seconds=%.1f' % seconds)

    def time_position(now):
        s = (now - t_start[0]) / seconds
        k = s if s <= 0.5 else 1.0 - s
        f = max(0.0, 2.0 * k)
        return cx + amp * f, cy + 0.5 * amp * f

    def on_timer():
        now = _clock()
        if last_tick[0] is not None:
            _series('tick', now - last_tick[0])
        last_tick[0] = now
        if time_mode:
            x, y = time_position(now)
            _count('moves_sent')
            native._vispy_mouse_move(native=qevent(QMouseEvent.Type.MouseMove, x, y, qbutton), pos=(x, y),
                                     buttons=[button_id], modifiers=())
            if now - t_start[0] >= seconds:
                step[0] = n_moves
                x, y = cx, cy
        else:
            # burst > 1: several moves per timer tick, to imitate a mouse that reports faster than the timer fires
            for _ in range(burst):
                if step[0] >= n_moves:
                    break
                step[0] += 1
                x, y = position(step[0])
                _count('moves_sent')
                native._vispy_mouse_move(native=qevent(QMouseEvent.Type.MouseMove, x, y, qbutton), pos=(x, y),
                                         buttons=[button_id], modifiers=())
        i = step[0]
        if i >= n_moves:
            timer.stop()
            native._vispy_mouse_release(native=qevent(QMouseEvent.Type.MouseButtonRelease, x, y,
                                                      Qt.MouseButton.NoButton), pos=(x, y), button=button_id,
                                        buttons=[], modifiers=())
            QTimer.singleShot(300, finish)

    def finish():
        _windows.remove(pan)
        dt = _clock() - pan.t0
        frames = pan.count['paint']
        parts = ' '.join('%s_ms_per_frame=%.3f' % (n, _per_frame(pan, n)) for n in
                         ('axis_prepare', 'axis_update', 'glir_parse', 'flush', 'camera_view', 'mouse_move',
                          'set_state', 'update_vars', 'pixel_scale', 'gl_state', 'axis_view', 'matrix_set',
                          'text_prepare', 'text_map')) + \
            ' set_state_calls=%.1f update_vars_calls=%.1f pixel_scale_calls=%.1f gl_state_calls=%.1f' % tuple(
                pan.count[n] / frames if frames else 0.0 for n in ('set_state', 'update_vars', 'pixel_scale', 'gl_state'))
        _emit('SUMMARY frames=%d duration_s=%.2f fps=%.1f paint_mean_ms=%.2f paint_max_ms=%.2f swap_mean_ms=%.2f '
              'swap_max_ms=%.2f %s moves_sent=%d moves_received=%d moves_dropped=%d camera_steps=%d '
              'camera_steps_per_frame=%.2f draws_per_frame=%.1f glir_cmds_per_frame=%.1f draw_cmds_per_frame=%.1f' % (
                  frames, dt, frames / dt, _ms(pan, 'paint'), 1000 * pan.max['paint'], _ms(pan, 'swap'),
                  1000 * pan.max['swap'], parts, pan.count['moves_sent'], pan.count['moves_in'],
                  pan.count['moves_dropped'], pan.count['camera_view'],
                  pan.count['camera_view'] / frames if frames else 0.0,
                  pan.count['draws'] / frames if frames else 0.0,
                  pan.count['glir_cmds'] / frames if frames else 0.0,
                  pan.count['glir_draw'] / frames if frames else 0.0))
        _emit('TEXT per_frame: text_collections_with_text=%.2f empty=%.2f get_transform_calls=%.2f '
              'text_map_get_ms=%.4f text_prepare_ms=%.4f' % (
                  pan.count['text_with_text'] / max(1, frames), pan.count['text_empty'] / max(1, frames),
                  pan.count['text_map'] / max(1, frames), _per_frame(pan, 'text_map_get'),
                  _per_frame(pan, 'text_prepare')))
        if _state.get('cprofile_dump'):
            _state.pop('cprofile_dump')()
        if _state.get('paint_profiles'):
            import io
            import pstats
            out = io.StringIO()
            for number, ms, prof in _state['paint_profiles']:
                out.write('=== paint %d after the press: %.1f ms (profiled) ===\n' % (number, 1000 * ms))
                pstats.Stats(prof, stream=out).sort_stats('cumulative').print_stats(22)
                out.write('--- by tottime ---\n')
                pstats.Stats(prof, stream=out).sort_stats('tottime').print_stats(12)
            out.write('\n=== visual draw time (inclusive, ms) by paint number and visual class, > 0.5 ms ===\n')
            for (number, name), seconds in sorted(_state.get('visual_times', {}).items()):
                if seconds > 0.0005:
                    out.write('paint %d  %-28s %.2f\n' % (number, name, 1000 * seconds))
            for number, ms, prof in _state['paint_profiles'][1:2]:
                out.write('\n=== callers of _build / set_data in paint %d ===\n' % number)
                pstats.Stats(prof, stream=out).print_callers('_build|set_data')
            with open(os.environ['FLATCAM_CANVAS_PROF_PAINT_PROFILE'], 'w') as fh:
                fh.write(out.getvalue())
            _emit('PAINT_PROFILE written')
        intervals = pan.series['interval']
        if intervals:
            # 'fps' of the SUMMARY counts the 300 ms wait after the release; this one only the frames of the pan
            _emit('FPS_ACTIVE %.1f (frames / sum of the paint-start intervals)' % (len(intervals) / sum(intervals)))
        _emit('INTERVALS paint_start_to_start: %s | %s' % (_bands(intervals), _stats(intervals)))
        _emit('LATE frames (interval > 13 ms) frame#:interval/prev_paint/prev_end_to_swapped/swapped_to_start in ms: ' +
              ' '.join('%d:%.1f/%.1f/%.1f/%.1f' % (i, 1000 * a, 1000 * b, 1000 * c, 1000 * d)
                       for i, a, b, c, d in pan.series['late'][:16]))
        for name in ('paint_end_to_swapped', 'swapped_to_paint', 'update_to_paint', 'tick'):
            _emit('SERIES %s %s' % (name, _stats(pan.series[name])))
        ticks = pan.series['tick']
        if ticks:
            _emit('TICKS late_gt_2x_interval=%d of %d' % (
                sum(1 for v in ticks if v > 2 * timer.interval() / 1000.0), len(ticks)))
        shot = os.environ.get('FLATCAM_CANVAS_PROF_SHOT')
        if shot:
            try:
                import numpy
                # the pan leaves the view some float bits away from the start: set the start rect back, so that the
                # pictures of two runs are comparable pixel by pixel
                camera = _state['canvas'].view.camera
                camera.rect = start_rect
                numpy.save(shot, _state['canvas'].render())
                _emit('SHOT saved %s' % shot)
            except Exception as e:
                _emit('SHOT error=%r' % (e,))
        if os.environ.get('FLATCAM_CANVAS_PROF_EXIT') == '1':
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()

    cprofile_path = os.environ.get('FLATCAM_CANVAS_PROF_CPROFILE')
    if cprofile_path:
        _cprofile_camera(cprofile_path, pan)

    _state['timer'] = timer
    timer.timeout.connect(on_timer)
    timer.start()


# ----------------------------------------------------------------------------------------------------------------------
# 3D canvas: PCB 3D Viewer opened from the probe, scripted rotate / pan / wheel zoom
# ----------------------------------------------------------------------------------------------------------------------
def mode_3d():
    return os.environ.get('FLATCAM_CANVAS_PROF_3D') == '1'


def _install_gc_hook():
    """A collection of the Python garbage collector longer than 5 ms gets a GC line."""
    import gc
    gc_start = [0.0]

    def gc_event(phase, info):
        if phase == 'start':
            gc_start[0] = time.perf_counter()
        else:
            ms = (time.perf_counter() - gc_start[0]) * 1000.0
            if ms > 5:
                _emit('GC gen=%d ms=%.1f collected=%d' % (info['generation'], ms, info['collected']))

    gc.callbacks.append(gc_event)
    _emit('GC counts=%r thresholds=%r objects=%d' % (gc.get_count(), gc.get_threshold(), len(gc.get_objects())))


def _wrap_3d_classes():
    """Wrap the classes that only exist once the 3D canvas module is imported (counters of the 3D summaries)."""
    from PyQt6.QtWidgets import QWidget  # noqa: F401  (the ViewCube module needs the Qt GUI classes)
    from vispy.scene.cameras.perspective import PerspectiveCamera
    from appGUI.PlotCanvas3d import Camera_3D
    from appGUI.ViewCube import ViewCube
    from appPlugins.ToolPCBViewer.PCBViewerView import PCBView
    PerspectiveCamera._update_transform = _timed('cam_ut', PerspectiveCamera._update_transform)
    Camera_3D._update_rotation = _timed('rot_move', Camera_3D._update_rotation)
    ViewCube.paintEvent = _timed('vc_paint', ViewCube.paintEvent)
    vc_profile = os.environ.get('FLATCAM_CANVAS_PROF_3D_VCPROF')
    if vc_profile:
        # cProfile of the ViewCube paint events during the whole run, written at the end (see finish())
        import cProfile
        profiler = cProfile.Profile()
        wrapped = ViewCube.paintEvent

        def profiled_paint(self, event):
            profiler.enable()
            try:
                return wrapped(self, event)
            finally:
                profiler.disable()

        ViewCube.paintEvent = profiled_paint
        _state['vc_dump'] = lambda: _dump_stats(profiler, vc_profile, 'ViewCube paint events', 0)
    PCBView.sort_layers = _timed('sort', PCBView.sort_layers)


def _open_viewer():
    """Open the PCB 3D Viewer for the loaded objects; start the scripted drags when its build is ready."""
    from PyQt6.QtCore import QTimer
    canvas2d = _state['canvas2d']
    app = canvas2d.fcapp
    tool = app.pcb_viewer_tool
    names = [o.obj_options['name'] for o in app.collection.get_list()]
    _emit('3D3 opening the PCB viewer, %d objects: %s' % (len(names), names))
    _wrap_3d_classes()
    tool.run()
    # FLATCAM_CANVAS_PROF_3D_ROLES=outline=Profile,copper_top=Signal_Top: a role gets the first object whose name
    # contains the text (the viewer picks roles by the usual file extensions only)
    for item in filter(None, os.environ.get('FLATCAM_CANVAS_PROF_3D_ROLES', '').split(',')):
        role, needle = item.split('=', 1)
        match = next((n for n in names if needle.lower() in n.lower()), None)
        if match is not None:
            tool.ui.combos[role].set_value(match)
    _emit('3D roles: %s' % {r: c.currentText() for r, c in tool.ui.combos.items() if c.currentText()})
    tries = [0]

    def poll():
        tries[0] += 1
        canvas = _state['canvas']
        if tool._ready and not tool._pending and not tool._running and canvas is not None and tries[0] > 2:
            QTimer.singleShot(1500, lambda: _start_3d(app, tool))
            return
        if tries[0] > 120:
            _emit('3D ERROR the viewer is not ready after 60 s (ready=%r pending=%r running=%r canvas=%r)' % (
                tool._ready, tool._pending, tool._running, canvas))
            _quit()
            return
        QTimer.singleShot(500, poll)

    QTimer.singleShot(500, poll)


def _quit():
    if os.environ.get('FLATCAM_CANVAS_PROF_EXIT') == '1':
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()


def _mesh_stats(tool):
    layers = tool.view.layers
    parts = []
    total_faces = total_vertices = 0
    for name, (layer, meshes) in layers.items():
        faces = len(layer.faces)
        total_faces += faces * len(meshes)
        total_vertices += faces * 3 * len(meshes)
        parts.append('%s:faces=%d,vertices=%d,meshes=%d' % (name, faces, len(layer.vertices), len(meshes)))
    # a flat-shaded VisPy mesh has no index buffer: 3 vertices per face are sent to the GPU, for each of its 2 meshes
    _emit('MESH3D layers=%d drawn_triangles=%d drawn_vertices=%d | %s' % (len(layers), total_faces, total_vertices,
                                                                          ' '.join(parts)))


def _summary3d(name, w, extra=''):
    """One block of lines for a scripted action of the 3D canvas."""
    frames = w.count['paint']
    intervals = w.series['interval']
    per = lambda n: w.count[n] / frames if frames else 0.0  # noqa: E731
    _emit('SUMMARY3D %s frames=%d paint_mean_ms=%.2f paint_max_ms=%.2f swap_mean_ms=%.2f %s' % (
        name, frames, _ms(w, 'paint'), 1000 * w.max['paint'], _ms(w, 'swap'), extra))
    if intervals:
        _emit('FPS_ACTIVE %s %.1f (frames / sum of the paint-start intervals)' % (name, len(intervals) / sum(intervals)))
    _emit('INTERVALS %s paint_start_to_start: %s | %s' % (name, _bands(intervals), _stats(intervals)))
    if w.count['gpu_finish']:
        _emit('GPU %s glFinish after the paint: mean=%.2f ms max=%.2f ms (paint_mean_ms above includes it)' % (
            name, _ms(w, 'gpu_finish'), 1000 * w.max['gpu_finish']))
    _emit('PERFRAME %s view_changed=%.2f (%.3f ms) update_transform=%.2f (%.3f ms) rotate_move_handler_ms=%.3f '
          'sort_layers=%.2f (%.3f ms) viewcube_paints=%.2f (%.3f ms) draws=%.1f mouse_move_ms=%.3f' % (
              name, per('camera_view'), _per_frame(w, 'camera_view'), per('cam_ut'), _per_frame(w, 'cam_ut'),
              _per_frame(w, 'rot_move'), per('sort'), _per_frame(w, 'sort'), per('vc_paint'),
              _per_frame(w, 'vc_paint'), per('draws'), _per_frame(w, 'mouse_move')))
    _emit('MOVES %s sent=%d received=%d dropped=%d (inclusive times: view_changed contains update_transform, '
          'sort_layers and the ViewCube update calls it triggers)' % (
              name, w.count['moves_sent'], w.count['moves_in'], w.count['moves_dropped']))
    _emit('LATE %s (interval > 13 ms) frame#:interval/prev_paint/prev_end_to_swapped/swapped_to_start in ms: %s' % (
        name, ' '.join('%d:%.1f/%.1f/%.1f/%.1f' % (i, 1000 * a, 1000 * b, 1000 * c, 1000 * d)
                       for i, a, b, c, d in w.series['late'][:12])))
    _emit('SERIES %s paint_end_to_swapped %s' % (name, _stats(w.series['paint_end_to_swapped'])))


def _start_3d(app, tool):
    from PyQt6.QtCore import QTimer, Qt, QPointF, QPoint
    from PyQt6.QtGui import QMouseEvent, QWheelEvent
    canvas = _state['canvas']
    native = canvas.native
    camera = canvas.view.camera
    no_mod = Qt.KeyboardModifier.NoModifier
    cx, cy = native.width() / 2.0, native.height() / 2.0
    amp = min(native.width(), native.height()) / 4.0
    seconds = float(os.environ.get('FLATCAM_CANVAS_PROF_3D_SECONDS', '3'))
    start_state = camera.get_state()
    _emit('3D3 start size=%dx%d camera_reference=%s pan_button=%r fov=%r scale=%.4g' % (
        native.width(), native.height(), canvas.camera_reference, camera.pan_button_setting, camera.fov,
        camera.scale_factor))
    _mesh_stats(tool)
    _install_gc_hook()
    if os.environ.get('FLATCAM_CANVAS_PROF_3D_HIDE') == '1':
        # item 6: no layer is drawn (the rest of the scene and all input / camera / ViewCube work stay)
        tool.view.root.visible = False
        _emit('3D3 all PCB layers hidden')
    cprofile_path = os.environ.get('FLATCAM_CANVAS_PROF_3D_CPROFILE')
    profiler = None
    if cprofile_path:
        import cProfile
        from appGUI.PlotCanvas3d import Camera_3D
        profiler = cProfile.Profile()
        wrapped = Camera_3D._update_rotation

        def profiled(self, event):
            profiler.enable()
            try:
                return wrapped(self, event)
            finally:
                profiler.disable()

        Camera_3D._update_rotation = profiled

    def begin():
        w = Window()
        _windows.append(w)
        # the first interval would span the idle time before the action
        _state['last_start'] = _state['last_swapped'] = _state['upd'] = None
        return w

    def end(name, w, extra=''):
        _windows.remove(w)
        _summary3d(name, w, extra)

    def drag(name, button_id, qbutton, next_step):
        w = begin()

        def qevent(kind, x, y, buttons):
            p = QPointF(x, y)
            return QMouseEvent(kind, p, p, qbutton, buttons, no_mod)

        native._vispy_mouse_press(native=qevent(QMouseEvent.Type.MouseButtonPress, cx, cy, qbutton), pos=(cx, cy),
                                  button=button_id, buttons=[button_id], modifiers=())
        timer = QTimer()
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(int(os.environ.get('FLATCAM_CANVAS_PROF_3D_INTERVAL', '2')))
        t_start = [_clock()]
        _state['timer_' + name] = timer

        def on_timer():
            now = _clock()
            s = (now - t_start[0]) / seconds
            k = s if s <= 0.5 else 1.0 - s
            f = max(0.0, 2.0 * k)
            x, y = cx + amp * f, cy + 0.5 * amp * f
            if s >= 1.0:
                x, y = cx, cy
            _count('moves_sent')
            native._vispy_mouse_move(native=qevent(QMouseEvent.Type.MouseMove, x, y, qbutton), pos=(x, y),
                                     buttons=[button_id], modifiers=())
            if s >= 1.0:
                timer.stop()
                native._vispy_mouse_release(native=qevent(QMouseEvent.Type.MouseButtonRelease, x, y,
                                                          Qt.MouseButton.NoButton), pos=(x, y), button=button_id,
                                            buttons=[], modifiers=())
                QTimer.singleShot(300, lambda: (end(name, w), next_step()))

        timer.timeout.connect(on_timer)
        timer.start()

    def wheel(next_step):
        w = begin()
        steps = int(os.environ.get('FLATCAM_CANVAS_PROF_3D_WHEEL_STEPS', '100'))
        px, py = cx + amp / 2.0, cy
        timer = QTimer()
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(8)
        count = [0]
        _state['timer_wheel'] = timer

        def on_timer():
            i = count[0]
            if i >= 2 * steps:
                timer.stop()
                QTimer.singleShot(300, lambda: (end('wheel', w, 'wheel_events=%d' % (2 * steps)), next_step()))
                return
            count[0] += 1
            sign = 1 if i < steps else -1
            pos = QPointF(px, py)
            event = QWheelEvent(pos, QPointF(native.mapToGlobal(QPoint(int(px), int(py)))), QPoint(0, 0),
                                QPoint(0, 12 * sign), Qt.MouseButton.NoButton, no_mod, Qt.ScrollPhase.NoScrollPhase,
                                False)
            _count('moves_sent')
            native.wheelEvent(event)

        timer.timeout.connect(on_timer)
        timer.start()

    def finish():
        if profiler is not None:
            _dump_stats(profiler, cprofile_path, 'rotate moves (Camera_3D._update_rotation)', 0)
        if _state.get('vc_dump'):
            _state.pop('vc_dump')()
        shot = os.environ.get('FLATCAM_CANVAS_PROF_SHOT')
        if shot:
            try:
                import numpy
                # canvas.render() sends no draw event: the layer sort that the draw event runs is called by hand
                camera.set_state(start_state)
                tool.view.sort_layers()
                numpy.save(shot + '_start.npy', canvas.render())
                for view_name in ('top', 'bottom'):
                    camera.set_view(view_name)
                    tool.view.sort_layers()
                    numpy.save('%s_%s.npy' % (shot, view_name), canvas.render())
                _emit('SHOT saved %s_{start,top,bottom}.npy' % shot)
            except Exception as e:
                import traceback
                _emit('SHOT error=%r %s' % (e, traceback.format_exc().replace('\n', ' | ')))
        _quit()

    rotate_button = 1
    pan_button = int(camera.pan_button_setting)
    qpan = {1: Qt.MouseButton.LeftButton, 2: Qt.MouseButton.RightButton, 3: Qt.MouseButton.MiddleButton}[pan_button]
    drag('rotate', rotate_button, Qt.MouseButton.LeftButton,
         lambda: drag('pan', pan_button, qpan, lambda: wheel(finish)))


# ----------------------------------------------------------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------------------------------------------------------
def _is_2d(native):
    canvas = getattr(native, '_vispy_canvas', None)
    return canvas is not None and any(c.__name__ == 'VisPyCanvas' for c in type(canvas).__mro__)


def _is_target(native):
    """The canvas that is measured: the 2D canvas, or the 3D canvas with FLATCAM_CANVAS_PROF_3D=1."""
    if not mode_3d():
        return _is_2d(native)
    canvas = getattr(native, '_vispy_canvas', None)
    return canvas is not None and any(c.__name__ == 'PlotCanvas3d' for c in type(canvas).__mro__)


def install():
    """Wrap the VisPy functions. Call after all other patches. Does nothing without FLATCAM_CANVAS_PROF=1."""
    global _second
    if os.environ.get('FLATCAM_CANVAS_PROF') != '1' or _state['inited']:
        return
    _state['inited'] = True

    from vispy.app.backends._pyqt6 import CanvasBackendDesktop
    from vispy.app.base import BaseCanvasBackend
    from vispy.gloo.glir import GlirParser
    from vispy.gloo.program import Program
    from vispy.gloo.context import GLContext
    from vispy.scene.cameras.base_camera import BaseCamera
    from vispy.visuals.axis import AxisVisual

    _second = Window()
    _windows.append(_second)

    orig_paint = CanvasBackendDesktop.paintGL
    finish_gl = os.environ.get('FLATCAM_CANVAS_PROF_3D_FINISH') == '1'
    orig_swap = CanvasBackendDesktop._vispy_swap_buffers

    def paintGL(self):
        if mode_3d() and not _state.get('opener') and _is_2d(self):
            # the first paint of the 2D canvas: the files are loaded; open the PCB viewer some seconds later
            from PyQt6.QtCore import QTimer
            _state['opener'] = True
            _state['canvas2d'] = self._vispy_canvas
            QTimer.singleShot(int(float(os.environ.get('FLATCAM_CANVAS_PROF_PAN_DELAY', '8')) * 1000), _open_viewer)
        if not _is_target(self):
            return orig_paint(self)
        if not _state['inited'] or _state['canvas'] is None:
            _state['canvas'] = self._vispy_canvas
            _on_first_paint(self)
            _second.t0 = _clock()
        t = _clock()
        if _state['last_start'] is not None:
            _series('interval', t - _state['last_start'])
            if t - _state['last_start'] > 0.013:
                # a late frame: its index, the interval, the previous paint and the wait of the previous frame for the
                # swap (paint end to frameSwapped) and for this paint (frameSwapped to start)
                prev = _state.get('prev_paint', 0.0)
                swapped = _state['last_swapped']
                for w in _windows:
                    w.series['late'].append((len(w.series['interval']), t - _state['last_start'], prev,
                                             (swapped - _state['last_end']) if swapped and _state['last_end'] else -1.0,
                                             (t - swapped) if swapped else -1.0))
        if _state['last_swapped'] is not None:
            _series('swapped_to_paint', t - _state['last_swapped'])
            _state['last_swapped'] = None
        if _state['upd'] is not None:
            _series('update_to_paint', t - _state['upd'])
            _state['upd'] = None
        _state['last_start'] = t
        _state['in_paint'] = True
        prof = None
        idx = _state.get('pan_paint_idx')
        if idx is not None and idx < 4 and os.environ.get('FLATCAM_CANVAS_PROF_PAINT_PROFILE') and not mode_3d():
            # Part C: a cProfile of each of the first paints after the press of the scripted 2D pan
            import cProfile
            _state['pan_paint_idx'] = idx + 1
            prof = cProfile.Profile()
            prof.enable()
        try:
            result = orig_paint(self)
            if finish_gl and mode_3d():
                # the GPU time of the frame: glFinish blocks until all commands of the paint are executed
                from vispy.gloo import gl
                t_finish = _clock()
                gl.glFinish()
                _record('gpu_finish', _clock() - t_finish)
            return result
        finally:
            if prof is not None:
                prof.disable()
                _state.setdefault('paint_profiles', []).append((idx + 1, _clock() - t, prof))
            _state['in_paint'] = False
            _state['last_end'] = _clock()
            _state['prev_paint'] = _state['last_end'] - t
            _record('paint', _state['last_end'] - t)
            _tick()

    def swap(self):
        if not _is_target(self):
            return orig_swap(self)
        t = _clock()
        try:
            return orig_swap(self)
        finally:
            _record('swap', _clock() - t)

    orig_update = CanvasBackendDesktop._vispy_update

    def update_request(self):
        if _is_target(self) and _state['upd'] is None and not _state['in_paint']:
            _state['upd'] = _clock()
        return orig_update(self)

    CanvasBackendDesktop.paintGL = paintGL
    CanvasBackendDesktop._vispy_swap_buffers = swap
    CanvasBackendDesktop._vispy_update = update_request

    orig_move = BaseCanvasBackend._vispy_mouse_move

    def mouse_move(self, **kwargs):
        _count('moves_in')
        t = _clock()
        try:
            result = orig_move(self, **kwargs)
        finally:
            _record('mouse_move', _clock() - t)
        if result is None:
            _count('moves_dropped')
        return result

    BaseCanvasBackend._vispy_mouse_move = mouse_move

    BaseCamera.view_changed = _timed('camera_view', BaseCamera.view_changed)
    AxisVisual._prepare_draw = _timed('axis_prepare', AxisVisual._prepare_draw)
    AxisVisual._update_subvisuals = _timed('axis_update', AxisVisual._update_subvisuals)
    GLContext.flush_commands = _timed('flush', GLContext.flush_commands)

    from vispy import gloo
    from vispy.visuals.shaders.program import ModularProgram
    from vispy.visuals.transforms.transform_system import TransformSystem
    from vispy.visuals.visual import Visual
    gloo.set_state = _timed('set_state', gloo.set_state)
    ModularProgram.update_variables = _timed('update_vars', ModularProgram.update_variables)
    TransformSystem.pixel_scale = property(_timed('pixel_scale', TransformSystem.pixel_scale.fget))
    Visual.update_gl_state = _timed('gl_state', Visual.update_gl_state)

    from vispy.scene.widgets.axis import AxisWidget
    from vispy.visuals.transforms import MatrixTransform
    AxisWidget._view_changed = _timed('axis_view', AxisWidget._view_changed)
    MatrixTransform.matrix = property(MatrixTransform.matrix.fget, _timed('matrix_set', MatrixTransform.matrix.fset))
    try:
        from appGUI.VisPyVisuals import TextCollectionVisual
        text_prepare = _timed('text_prepare', TextCollectionVisual._prepare_draw)
        orig_get_transform = TransformSystem.get_transform
        in_text = [False]

        def get_transform(self, *args, **kwargs):
            if in_text[0]:
                _count('text_map')
                t0 = _clock()
                try:
                    return orig_get_transform(self, *args, **kwargs)
                finally:
                    _record('text_map_get', _clock() - t0)
            return orig_get_transform(self, *args, **kwargs)

        def text_wrapper(self, view):
            # counts the collections drawn (text_prepare calls) and those that reach the 4-point map (text_map)
            in_text[0] = True
            _count('text_with_text' if len(self.text) else 'text_empty')
            try:
                return text_prepare(self, view)
            finally:
                in_text[0] = False

        TransformSystem.get_transform = get_transform
        TextCollectionVisual._prepare_draw = text_wrapper
    except Exception as e:
        _emit('text collection hook error=%r' % (e,))

    orig_parse = GlirParser.parse

    def parse(self, commands):
        _count('glir_cmds', len(commands))
        _count('glir_draw', sum(1 for c in commands if c[0] == 'DRAW'))
        t = _clock()
        try:
            return orig_parse(self, commands)
        finally:
            _record('glir_parse', _clock() - t)

    GlirParser.parse = parse

    orig_draw = Program.draw

    def draw(self, *args, **kwargs):
        _count('draws')
        return orig_draw(self, *args, **kwargs)

    Program.draw = draw

    if os.environ.get('FLATCAM_CANVAS_PROF_PAINT_PROFILE') and not mode_3d():
        # Part C: the time of each visual draw (inclusive) in the first paints after the press, by visual class
        from vispy.visuals.visual import Visual, CompoundVisual
        times = _state.setdefault('visual_times', {})

        def wrap_draw(cls):
            original = cls.draw

            def visual_draw(self, *args, **kwargs):
                idx = _state.get('pan_paint_idx')
                if idx is None or idx > 4:
                    return original(self, *args, **kwargs)
                t0 = _clock()
                try:
                    return original(self, *args, **kwargs)
                finally:
                    key = (idx, type(self).__name__)
                    times[key] = times.get(key, 0.0) + _clock() - t0

            cls.draw = visual_draw

        wrap_draw(Visual)
        wrap_draw(CompoundVisual)
    _emit('installed')
