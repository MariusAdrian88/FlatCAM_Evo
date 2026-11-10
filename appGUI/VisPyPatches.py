# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# File Author: Dennis Hayrullin                            #
# Date: 2/5/2016                                           #
# MIT Licence                                              #
# ##########################################################

import gc
import logging
import os
import weakref

from PyQt6 import QtCore
from vispy.visuals import markers, InfiniteLineVisual
from vispy.app.backends._pyqt6 import CanvasBackendDesktop
from vispy.app.base import BaseCanvasBackend
from vispy.geometry import Rect
from vispy.scene.cameras.panzoom import PanZoomCamera
from vispy.visuals.axis import AxisVisual, Ticker, _get_ticks_talbot
from vispy.visuals.transforms import MatrixTransform
from vispy.util import transforms
from vispy.scene.widgets import Grid
import numpy as np


log = logging.getLogger('base')


def new_patches_enabled():
    """False with FLATCAM_VISPY_PATCHES=0: the newer canvas speed-ups (input, camera) are off to compare or to bisect."""
    return os.environ.get('FLATCAM_VISPY_PATCHES') != '0'


def _can_patch(owner, name):
    """True for VisPy 0.17.x when owner.name exists; otherwise logs a warning and the original stays."""
    import vispy
    if not vispy.__version__.startswith('0.17.') or not hasattr(owner, name):
        log.warning("VisPyPatches: VisPy %s has no matching %s.%s, the patch is not applied" % (
            vispy.__version__, getattr(owner, '__name__', owner), name))
        return False
    return True


def s5_enabled():
    """False with FLATCAM_VISPY_S5=0 (or FLATCAM_VISPY_PATCHES=0): the GLIR, shader and line draw-call speed-ups are off."""
    return new_patches_enabled() and os.environ.get('FLATCAM_VISPY_S5') != '0'


def hold_gc_during_drag(canvas):
    """A full collection of the Python garbage collector takes about 200 ms with a large board loaded, and it can
    start in the middle of a pan. While a mouse button is down, the objects that exist at the press are set aside
    (gc.freeze), so a collection only looks at the objects made during the drag. The release puts them back."""
    if not new_patches_enabled():
        return
    canvas.events.mouse_press.connect(lambda event: gc.freeze())
    canvas.events.mouse_release.connect(lambda event: gc.unfreeze())


def s4_enabled():
    """False with FLATCAM_VISPY_S4=0 (or FLATCAM_VISPY_PATCHES=0): the camera-step and ruler speed-ups are off."""
    return new_patches_enabled() and os.environ.get('FLATCAM_VISPY_S4') != '0'


def _patch_glir_draw_check():
    """GlirProgram.draw calls gl.check_error (glGetError, a driver round trip) before and after each draw call. The
    checks are kept with FLATCAM_GL_DEBUG=1 only; a GL error would only be reported later, by the next check."""
    from vispy.gloo import glir
    if not s5_enabled() or os.environ.get('FLATCAM_GL_DEBUG') == '1' \
            or not _can_patch(glir.GlirProgram, 'draw') or getattr(glir.GlirProgram.draw, 'flatcam_patch', False):
        return
    gl = glir.gl
    as_enum = glir.as_enum

    def draw(self, mode, selection, instances=1):
        # same as vispy 0.17 GlirProgram.draw, without gl.check_error
        if not self._linked:
            raise RuntimeError('Cannot draw program if code has not been set')
        try:
            mode = as_enum(mode)
        except ValueError:
            if mode == 'lines_adjacency' or mode == 'line_strip_adjacency':
                raise RuntimeError(gl.current_backend.__name__ +
                                   " backend does not support lines_adjacency"
                                   " and line_strip_adjacency primitives."
                                   " Try gloo.gl.use_gl('gl+').")
            raise
        if len(selection) == 3:
            id_, gtype, count = selection
            if count:
                self._pre_draw()
                ibuf = self._parser.get_object(id_)
                ibuf.activate()
                if instances > 1:
                    gl.glDrawElementsInstanced(mode, count, as_enum(gtype), None, instances)
                else:
                    gl.glDrawElements(mode, count, as_enum(gtype), None)
                ibuf.deactivate()
        else:
            first, count = selection
            if count:
                self._pre_draw()
                if instances > 1:
                    gl.glDrawArraysInstanced(mode, first, count, instances)
                else:
                    gl.glDrawArrays(mode, first, count)
        self._post_draw()

    draw.flatcam_patch = True
    glir.GlirProgram.draw = draw


def _patch_set_state():
    """gloo.set_state deep-copies its keyword arguments on each call (14 per frame). The copy is only made to pop keys,
    and the values are never changed, so a copy of the dict is enough. The rest is the vispy 0.17 function."""
    from types import MethodType
    from vispy import gloo
    from vispy.gloo import wrappers
    if not s5_enabled() or not _can_patch(wrappers.BaseGlooFunctions, 'set_state') \
            or getattr(wrappers.BaseGlooFunctions.set_state, 'flatcam_patch', False):
        return
    GL_PRESETS = wrappers.GL_PRESETS
    _check_valid = wrappers._check_valid
    _to_args = wrappers._to_args
    _setters = wrappers._setters

    def set_state(self, preset=None, **kwargs):
        # kwargs is a new dict for each call: no copy needed, the values are only read
        if preset is not None:
            _check_valid('preset', preset, tuple(list(GL_PRESETS.keys())))
            for key, val in GL_PRESETS[preset].items():
                if key not in kwargs:
                    kwargs[key] = val  # as before, the preset values are the shared ones only read below

        if 'cull_face' in kwargs:
            cull_face = kwargs.pop('cull_face')
            if isinstance(cull_face, bool):
                funcname = 'glEnable' if cull_face else 'glDisable'
                self.glir.command('FUNC', funcname, 'cull_face')
            else:
                self.glir.command('FUNC', 'glEnable', 'cull_face')
                self.set_cull_face(*_to_args(cull_face))

        if 'line_width' in kwargs:
            line_width = kwargs.pop('line_width')
            self.glir.command('FUNC', 'glLineWidth', line_width)
        if 'line_smooth' in kwargs:
            line_smooth = kwargs.pop('line_smooth')
            funcname = 'glEnable' if line_smooth else 'glDisable'
            self.glir.command('FUNC', funcname, 2848)  # GL_LINE_SMOOTH

        for key, val in kwargs.items():
            if key in _setters:
                args = _to_args(val)
                if key in ('blend_color', 'clear_color') and not isinstance(args[0], str):
                    args = [args]
                getattr(self, 'set_' + key)(*args)
            else:
                funcname = 'glEnable' if val else 'glDisable'
                self.glir.command('FUNC', funcname, key)

    set_state.flatcam_patch = True
    wrappers.BaseGlooFunctions.set_state = set_state
    # the module functions are methods of one instance, bound when vispy was imported
    bound = MethodType(set_state, wrappers.global_gloo_functions)
    wrappers.set_state = bound
    gloo.set_state = bound


def _patch_update_variables():
    """ModularProgram.update_variables (once per draw call): the variables are sorted by their shader name only when
    the program is rebuilt (a rebuild makes a new self._variables list), and the debug log text is made only when the
    DEBUG level is on. Every variable is still compared with its state_id, because a Variable value change sends no
    event to the program, so no dirty flag is possible."""
    import logging as _logging
    from vispy.visuals.shaders import program as sprogram
    if not s5_enabled() or not _can_patch(sprogram.ModularProgram, 'update_variables') \
            or getattr(sprogram.ModularProgram.update_variables, 'flatcam_patch', False):
        return
    logger = sprogram.logger

    def update_variables(self):
        variables = self._variables
        cached = self.__dict__.get('_flatcam_sorted')
        if cached is None or cached[0] is not variables:
            compiler = self.compiler
            cached = (variables, [(compiler[d], d) for d in sorted(variables, key=lambda d: compiler[d])])
            self.__dict__['_flatcam_sorted'] = cached
        debug = logger.isEnabledFor(_logging.DEBUG)
        if debug:
            logger.debug("Apply variables:")
        variable_cache = self._variable_cache
        for name, dep in cached[1]:
            state_id = dep.state_id
            if variable_cache.get(dep, None) != state_id:
                self[name] = dep.value
                variable_cache[dep] = state_id
                if debug:
                    logger.debug("    %s = %s **", name, dep.value)
            elif debug:
                logger.debug("    %s = %s", name, dep.value)

        self._process_pending_variables()
        if debug:
            logger.debug("Discarding unused variables before draw: %s" % self._pending_variables.keys())
        self._pending_variables = {}

    update_variables.flatcam_patch = True
    sprogram.ModularProgram.update_variables = update_variables


def _patch_pixel_scale():
    """TransformSystem.pixel_scale maps 2 points through the canvas transform on each call (line visuals, once per
    draw). The value is kept until the canvas transform emits 'changed': it is a chain of STTransforms, and every
    change of one of them (configure() on resize, DPI/FBO change, canvas_transform setter) emits it."""
    from vispy.visuals.transforms import transform_system
    system = transform_system.TransformSystem
    if not s5_enabled() or not _can_patch(system, 'pixel_scale') \
            or getattr(system.pixel_scale.fget, 'flatcam_patch', False):
        return

    def pixel_scale(self):
        tr = self._canvas_transform
        entry = self.__dict__.get('_flatcam_pixel_scale')
        if entry is None or entry[0] is not tr:
            entry = [tr, None]

            def invalidate(event=None, entry=entry):
                entry[1] = None

            tr.changed.connect(invalidate)
            self.__dict__['_flatcam_pixel_scale'] = entry
        if entry[1] is None:
            entry[1] = (tr.map((1, 0)) - tr.map((0, 0)))[0]
        return entry[1]

    pixel_scale.flatcam_patch = True
    system.pixel_scale = property(pixel_scale)


_scene_draw_depth = [0]
_camera_step = [0]


_axis_pending = weakref.WeakKeyDictionary()  # AxisVisual -> AxisWidget whose linked view changed since the last read


def _resolve_axis_domain(axis):
    """The domain of a linked axis is computed from the view transform when it is needed (the tick computation of the
    draw, or a read of AxisVisual.domain), with the same formula as AxisWidget._view_changed."""
    widget = _axis_pending.pop(axis, None)
    if widget is not None:
        # the maps of node_transform(scene).map(), without building the ChainTransform (shader rebuild, events)
        points = widget._axis_ends()
        for tr in reversed(widget.node_path_transforms(widget._linked_view.scene)):
            points = tr.map(points)
        p1, p2 = points
        if widget.orientation in ('left', 'right'):
            axis.domain = (p1[1], p2[1])
        else:
            axis.domain = (p1[0], p2[0])


def _patch_axis_deferred_domain():
    """AxisWidget._view_changed runs for each camera step and builds a transform chain and its inverse to get the new
    domain (two axes). It only marks the axis now; the domain is computed once, when it is read or when the axis is
    drawn. The ticks are computed from the domain as before. An explicit domain set after the mark wins, as before."""
    from vispy.scene.widgets.axis import AxisWidget
    from vispy.visuals.visual import updating_property
    if not s4_enabled() or not _can_patch(AxisWidget, '_view_changed') \
            or not _can_patch(AxisVisual, 'domain') or getattr(AxisWidget._view_changed, 'flatcam_patch', False):
        return

    def _view_changed(self, event=None):
        _axis_pending[self.axis] = self

    class DeferredDomain(updating_property):
        def __get__(self, obj, objtype=None):
            if obj is not None:
                _resolve_axis_domain(obj)
            return super().__get__(obj, objtype)

        def __set__(self, obj, value):
            _axis_pending.pop(obj, None)
            super().__set__(obj, value)

    original = AxisVisual.__dict__['domain']
    _view_changed.flatcam_patch = True
    AxisWidget._view_changed = _view_changed
    AxisVisual.domain = DeferredDomain(original.fget, original.fset, original.__doc__)


def _patch_shader_value_changes():
    """ShaderObject.changed(value_changed=True) (a uniform got a new value, for example the camera matrix) walks the
    whole dependency graph up to every ModularProgram (about 55 calls and 80 program notifications per camera step,
    14 visuals). Only code changes need that walk: ModularProgram finds the new value by comparing the state_id of each
    variable in update_variables, and ModularProgram.changed has no listener here (only VisPy's own collections connect
    to it, which are not used)."""
    from vispy.visuals.shaders import shader_object
    if not s4_enabled() or not _can_patch(shader_object.ShaderObject, 'changed') \
            or getattr(shader_object.ShaderObject.changed, 'flatcam_patch', False):
        return
    original = shader_object.ShaderObject.changed

    def changed(self, code_changed=False, value_changed=False):
        if code_changed:
            original(self, code_changed, value_changed)

    changed.flatcam_patch = True
    shader_object.ShaderObject.changed = changed


def _patch_camera_step_updates():
    """Node.update and BaseVisual.update only ask for a redraw (events.update has no listener but the compound visuals'
    own forwarding, and SceneCanvas.update ignores its node argument). While the camera writes its scene matrix, the
    transform change reaches every visual through several chains (about 120 update() calls per step), each of them the
    same request. They are skipped inside that write; PanZoomCamera._update_transform ends with
    ViewBox.update() (in _set_scene_transform), which makes the one request."""
    from vispy.scene.node import Node
    from vispy.visuals.visual import BaseVisual, CompoundVisual
    if not s4_enabled() or not _can_patch(Node, 'update') or not _can_patch(BaseVisual, 'update') \
            or getattr(Node.update, 'flatcam_patch', False):
        return
    node_update = Node.update
    visual_update = BaseVisual.update

    def update_node(self):
        if not _camera_step[0]:
            node_update(self)

    def update_visual(self):
        if not _camera_step[0]:
            visual_update(self)

    update_node.flatcam_patch = True
    update_visual.flatcam_patch = True
    Node.update = update_node
    BaseVisual.update = update_visual

    # The handlers of a transform change of a visual's TransformSystem: BaseVisual's only calls update(); the compound
    # visual's first sets the same TransformSystem on its subvisuals again (they got it when they were added, and
    # VisPy returns at once when it is the same object). VisPy connects them by name, hence the same names.
    if _can_patch(BaseVisual, '_transform_changed') and _can_patch(CompoundVisual, '_transform_changed'):
        base_changed = BaseVisual._transform_changed
        compound_changed = CompoundVisual._transform_changed

        def _transform_changed(self, event=None):
            if not _camera_step[0]:
                base_changed(self, event)

        def _compound_transform_changed(self, event=None):
            if not _camera_step[0]:
                compound_changed(self, event)

        _compound_transform_changed.__name__ = '_transform_changed'
        _transform_changed.flatcam_patch = True
        BaseVisual._transform_changed = _transform_changed
        CompoundVisual._transform_changed = _compound_transform_changed


def _patch_batched_flush():
    """Program.draw flushes the GLIR queue after each draw call. Inside SceneCanvas._draw_scene (the draw event and
    SceneCanvas.render) the queue is flushed once, when the scene is drawn: all commands stay in the same order, only
    the parse runs in one batch. A draw outside _draw_scene still flushes at once."""
    from vispy.gloo import program as gprogram
    from vispy.scene.canvas import SceneCanvas
    if not s5_enabled() or not _can_patch(gprogram.Program, 'draw') \
            or not _can_patch(SceneCanvas, '_draw_scene') \
            or getattr(gprogram.Program.draw, 'flatcam_patch', False):
        return

    check_enum = gprogram.check_enum
    logger = gprogram.logger
    DataBuffer = gprogram.DataBuffer
    IndexBuffer = gprogram.IndexBuffer
    get_current_canvas = gprogram.get_current_canvas
    modes = ('points', 'lines', 'line_strip', 'line_loop', 'lines_adjacency', 'line_strip_adjacency', 'triangles',
             'triangle_strip', 'triangle_fan')
    gltypes = {np.dtype(np.uint8): 'UNSIGNED_BYTE', np.dtype(np.uint16): 'UNSIGNED_SHORT',
               np.dtype(np.uint32): 'UNSIGNED_INT'}

    def draw(self, mode='triangles', indices=None, check_error=True):
        # same as vispy 0.17 Program.draw, without the flush per call and without eager log formatting
        self._buffer = None
        mode = check_enum(mode)
        if mode not in modes:
            raise ValueError('Invalid draw mode: %r' % mode)
        for name in self._pending_variables:
            logger.warn('Value provided for %r, but this variable was not found in the shader program.' % name)
        self._pending_variables = {}

        attributes = [vbo for vbo in self._user_variables.values() if isinstance(vbo, DataBuffer)]
        attrs = [a for a in attributes if getattr(a, 'divisor', None) is None]
        if len(attrs) < 1:
            raise RuntimeError('Must have at least one attribute')
        sizes = [a.size for a in attrs]
        if not all(s == sizes[0] for s in sizes[1:]):
            msg = '\n'.join([f'{str(a)}: {a.size}' for a in attrs])
            raise RuntimeError(f'All attributes must have the same size, got:\n{msg}')

        attrs_with_div = [a for a in attributes if a not in attrs]
        if attrs_with_div:
            sizes = [a.size for a in attrs_with_div]
            divs = [a.divisor for a in attrs_with_div]
            instances = sizes[0] * divs[0]
            if not all(s * d == instances for s, d in zip(sizes, divs)):
                msg = '\n'.join([f'{str(a)}: {a.size} * {a.divisor} = {a.size * a.divisor}' for a in attrs_with_div])
                raise RuntimeError(
                    f'All attributes with divisors must have the same size as the number of instances, got:\n{msg}')
        else:
            instances = 1

        canvas = get_current_canvas()
        assert canvas is not None
        canvas.context.glir.associate(self.glir)

        if isinstance(indices, IndexBuffer):
            canvas.context.glir.associate(indices.glir)
            selection = indices.id, gltypes[indices.dtype], indices.size
            canvas.context.glir.command('DRAW', self._id, mode, selection, instances)
        elif indices is None:
            selection = 0, attrs[0].size
            canvas.context.glir.command('DRAW', self._id, mode, selection, instances)
        else:
            raise TypeError("Invalid index: %r (must be IndexBuffer)" % indices)

        if not _scene_draw_depth[0]:
            canvas.context.flush_commands()

    draw.flatcam_patch = True
    gprogram.Program.draw = draw

    original_draw_scene = SceneCanvas._draw_scene

    def _draw_scene(self, *args, **kwargs):
        _scene_draw_depth[0] += 1
        try:
            return original_draw_scene(self, *args, **kwargs)
        finally:
            _scene_draw_depth[0] -= 1
            if not _scene_draw_depth[0]:
                self.context.flush_commands()

    _draw_scene.flatcam_patch = True
    SceneCanvas._draw_scene = _draw_scene


def _patch_perspective_camera():
    """The 3D cameras (ArcballCamera: Camera_3D) rebuild two things on each view update that do not change between two
    updates: the axis mapping matrix of Base3DRotationCamera._update_camera_pos (affine_map solves a linear system; its
    inputs are the constant up / forward / right vectors) and the viewbox mapping (STTransform.set_mapping, which depends
    on the viewbox size only). Both are kept; the numbers that reach the camera matrices are the same bits. The event
    cascade of an update is one chain change (measured: 1 effective change per update), so there is nothing to merge."""
    from vispy.scene.cameras import perspective
    from vispy.util import transforms
    rotation = perspective.Base3DRotationCamera
    if not new_patches_enabled() or not _can_patch(rotation, '_update_camera_pos') \
            or not _can_patch(perspective.PerspectiveCamera, '_update_transform') \
            or getattr(rotation._update_camera_pos, 'flatcam_patch', False):
        return
    basis_cache = {}

    def _update_camera_pos(self):
        # same as vispy 0.17 Base3DRotationCamera._update_camera_pos, with the constant matrix kept
        ch_em = self.events.transform_change
        with ch_em.blocker(self._update_transform):
            up, forward, right = self._get_dim_vectors()
            key = (np.asarray(up).tobytes(), np.asarray(forward).tobytes(), np.asarray(right).tobytes())
            basis = basis_cache.get(key)
            if basis is None:
                pp1 = np.array([(0, 0, 0), (0, 0, -1), (1, 0, 0), (0, 1, 0)])
                pp2 = np.array([(0, 0, 0), forward, right, up])
                basis = basis_cache[key] = transforms.affine_map(pp1, pp2).T
            pos = -self._actual_distance * forward
            scale = [1.0 / a for a in self._flip_factors]

            self.transform.matrix = np.linalg.multi_dot((
                basis,
                transforms.translate(pos),
                self._get_rotation_tr(),
                transforms.scale(scale),
                transforms.translate(self.center)
            ))

    _update_camera_pos.flatcam_patch = True
    rotation._update_camera_pos = _update_camera_pos

    original = perspective.PerspectiveCamera._update_transform
    if getattr(original, 'flatcam_patch', False):
        return

    def _update_transform(self, event=None):
        # same as vispy 0.17 PerspectiveCamera._update_transform; the viewbox mapping is set again only for a new size
        if self._viewbox is None:
            return
        if self._resetting:  # base camera linking operation
            return
        fx = fy = self._scale_factor
        w, h = self._viewbox.size
        if (w == 0) or (h == 0):
            return
        if w / h > 1:
            fx *= w / h
        else:
            fy *= h / w
        self._update_projection_transform(fx, fy)
        size = (w, h)
        if self.__dict__.get('_flatcam_viewbox_size') != size:
            self._viewbox_tr.set_mapping([[-1, 1], [1, -1]], [[0, 0], self._viewbox.size])
            self.__dict__['_flatcam_viewbox_size'] = size
        transforms_ = [n.transform for n in self._viewbox.scene.node_path_to_child(self)[1:]]
        camera_tr = self._transform_cache.get(transforms_).inverse
        full_tr = self._transform_cache.get([self._viewbox_tr, self._projection, camera_tr])
        self._transform_cache.roll()
        self._set_scene_transform(full_tr)

    _update_transform.flatcam_patch = True
    perspective.PerspectiveCamera._update_transform = _update_transform


def apply_patches():
    # Set before QApplication: keep canvas GL resources when tabs move between windows.
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    # Patch MarkersVisual to have crossed lines marker
    # cross_lines = """
    # float cross(vec2 pointcoord, float size)
    # {
    #     //vbar
    #     float r1 = abs(pointcoord.x - 0.5)*size;
    #     float r2 = abs(pointcoord.y - 0.5)*size - $v_size/2;
    #     float vbar = max(r1,r2);
    #     //hbar
    #     float r3 = abs(pointcoord.y - 0.5)*size;
    #     float r4 = abs(pointcoord.x - 0.5)*size - $v_size/2;
    #     float hbar = max(r3,r4);
    #     return min(vbar, hbar);
    # }
    # """
    #
    # markers._marker_dict['++'] = cross_lines
    # markers.marker_types = tuple(sorted(list(markers._marker_dict.copy().keys())))

    # # Add clear_data method to LineVisual to have possibility of clearing data
    # def clear_data(self):
    #     self._bounds = None
    #     self._pos = None
    #     self._changed['pos'] = True
    #     self.update()
    #
    # LineVisual.clear_data = clear_data

    # Patch VisPy Grid to prevent updating layout on PaintGL, which cause low fps
    def _prepare_draw(self, view):
        pass

    def _update_clipper(self):
        super(Grid, self)._update_clipper()
        try:
            self._update_child_widget_dim()
        except Exception as e:
            print("VisPyPatches.apply_patches._update_clipper() -> %s" % str(e))

    Grid._prepare_draw = _prepare_draw
    Grid._update_clipper = _update_clipper

    # Patch InfiniteLine visual to 1.5px width
    # def _prepare_draw(self, view=None):
    #     """This method is called immediately before each draw.
    #     The *view* argument indicates which view is about to be drawn.
    #     """
    #
    #     self.update_gl_state(line_smooth=False)
    #     px_scale = self.transforms.pixel_scale
    #     width = px_scale * 1.5
    #     self.update_gl_state(line_width=max(width, 1.0))
    #
    #     if self._changed['pos']:
    #         self.pos_buf.set_data(self._pos)
    #         self._changed['pos'] = False
    #
    #     if self._changed['color']:
    #         self._program.vert['color'] = self._color
    #         self._changed['color'] = False
    #
    # InfiniteLineVisual._prepare_draw = _prepare_draw

    # Patch AxisVisual to have less axis labels
    def _get_tick_frac_labels(self):
        """Get the major ticks, minor ticks, and major labels"""
        minor_num = 4  # number of minor ticks per major division
        if self.axis.scale_type == 'linear':
            domain = self.axis.domain
            if domain[1] < domain[0]:
                flip = True
                domain = domain[::-1]
            else:
                flip = False
            offset = domain[0]
            scale = domain[1] - domain[0]

            # trying to solve artifacts at maximum zoom
            if scale == 0 or not scale:
                scale = 0.00000001

            transforms = self.axis.transforms
            length = self.axis.pos[1] - self.axis.pos[0]  # in logical coords
            n_inches = np.sqrt(np.sum(length ** 2)) / transforms.dpi

            # major = np.linspace(domain[0], domain[1], num=11)
            # major = MaxNLocator(10).tick_values(*domain)
            major = _get_ticks_talbot(domain[0], domain[1], n_inches, 1)

            labels = ['%g' % x for x in major]
            majstep = major[1] - major[0]
            minstep = majstep / (minor_num + 1)
            minstart = 0 if self.axis._stop_at_major[0] else -1
            minstop = -1 if self.axis._stop_at_major[1] else 0
            maj = major[0] + np.arange(minstart, len(major) + minstop) * majstep
            minor = np.linspace(maj + minstep, maj + majstep - minstep,
                                minor_num, axis=-1).ravel()

            major_frac = (major - offset) / scale
            major_frac = major_frac[::-1] if flip else major_frac
            use_mask = (major_frac > -0.0001) & (major_frac < 1.0001)
            major_frac = major_frac[use_mask]
            labels = [l for li, l in enumerate(labels) if use_mask[li]]

            minor_frac = (np.array(minor) - offset) / scale
            use_minor_mask = (minor_frac > -0.0001) & (minor_frac < 1.0001)
            minor_frac = minor_frac[use_minor_mask]

            return major_frac, minor_frac, labels
        elif self.axis.scale_type == 'logarithmic':
            return NotImplementedError
        elif self.axis.scale_type == 'power':
            return NotImplementedError

    Ticker._get_tick_frac_labels = _get_tick_frac_labels

    # Keep axis glyph buffers while panning; positions and styles still update normally.
    def _update_subvisuals(self):
        tick_pos, labels, tick_label_pos, anchors, axis_label_pos = self.ticker.get_update()
        # Runs in the draw (AxisVisual._prepare_draw): the redraw requests of the sub-visuals are dropped by the canvas
        # there ('_drawing'), so they are skipped here instead of travelling through the event chains.
        _camera_step[0] += 1
        try:
            self._line.set_data(pos=self.pos, color=self.axis_color)
            self._ticks.set_data(pos=tick_pos, color=self.tick_color)
            labels = list(labels)
            if self._text.text != labels:
                self._text.text = labels
            self._text.pos = tick_label_pos
            if not np.array_equal(self._text.anchors, anchors):
                self._text.anchors = anchors
            if self.axis_label is not None:
                if self._axis_label_vis.text != self.axis_label:
                    self._axis_label_vis.text = self.axis_label
                self._axis_label_vis.pos = axis_label_pos
        finally:
            _camera_step[0] -= 1
        self._need_update = False

    def _prepare_axis_draw(self, view):
        if self._pos is None:
            return False
        _resolve_axis_domain(self)
        if self.axis_label is not None:
            rotation = self._rotation_angle
            # Compare in the setter's radians, without a degree round-trip or tolerance.
            if self._axis_label_vis._rotation != np.asarray(rotation) * np.pi / 180.:
                self._axis_label_vis.rotation = rotation
        if self._need_update:
            self._update_subvisuals()

    AxisVisual._update_subvisuals = _update_subvisuals
    AxisVisual._prepare_draw = _prepare_axis_draw

    # Only guard computed orthographic projections, never the mutable matrix setter.
    def _set_ortho(self, l, r, b, t, n, f):
        matrix = transforms.ortho(l, r, b, t, n, f)
        if not np.array_equal(self.matrix, matrix):
            self.matrix = matrix

    MatrixTransform.set_ortho = _set_ortho

    # Patch CanvasBackendDesktop to suppress EventEmitter re-entrancy on rapid key input
    _original_keyPressEvent = CanvasBackendDesktop.keyPressEvent
    _original_keyReleaseEvent = CanvasBackendDesktop.keyReleaseEvent

    def _safe_keyPressEvent(self, ev):
        try:
            _original_keyPressEvent(self, ev)
        except RuntimeError as e:
            if 'EventEmitter loop' not in str(e):
                raise

    def _safe_keyReleaseEvent(self, ev):
        try:
            _original_keyReleaseEvent(self, ev)
        except RuntimeError as e:
            if 'EventEmitter loop' not in str(e):
                raise

    CanvasBackendDesktop.keyPressEvent = _safe_keyPressEvent
    CanvasBackendDesktop.keyReleaseEvent = _safe_keyReleaseEvent

    # A mouse move while a button is down (a drag) is never dropped: VisPy drops each move that comes less than 10 ms
    # after the previous one, so a pan lost the displacement of the dropped moves in time. Hover moves keep the limit.
    if new_patches_enabled() and _can_patch(BaseCanvasBackend, '_vispy_mouse_move') \
            and not getattr(BaseCanvasBackend._vispy_mouse_move, 'flatcam_patch', False):
        _original_mouse_move = BaseCanvasBackend._vispy_mouse_move

        def _vispy_mouse_move(self, **kwargs):
            if self._vispy_mouse_data['press_event'] is not None:
                self._last_time = 0.0
            return _original_mouse_move(self, **kwargs)

        _vispy_mouse_move.flatcam_patch = True
        BaseCanvasBackend._vispy_mouse_move = _vispy_mouse_move

    # PanZoomCamera._update_transform: same transforms, but the 4x4 scene matrix is written directly. The original
    # builds a MatrixTransform and changes its matrix 3 times, each change rebuilding shader functions.
    if new_patches_enabled() and _can_patch(PanZoomCamera, '_update_transform') \
            and not getattr(PanZoomCamera._update_transform, 'flatcam_patch', False):
        _original_update_transform = PanZoomCamera._update_transform

        def _update_transform(self):
            if self._resetting:  # base camera linking operation
                return
            if self.up != '+z':
                return _original_update_transform(self)

            rect = self.rect
            self._real_rect = Rect(rect)
            vbr = self._viewbox.rect.flipped(x=self.flip[0], y=(not self.flip[1]))
            d = self.depth_value

            # apply scale ratio constraint
            if self._aspect is not None:
                requested_aspect = (rect.width / rect.height if rect.height != 0 else 1)
                view_aspect = vbr.width / vbr.height if vbr.height != 0 else 1
                constrained_aspect = abs(view_aspect / self._aspect)

                if requested_aspect > constrained_aspect:
                    dy = 0.5 * (rect.width / constrained_aspect - rect.height)
                    self._real_rect.top += dy
                    self._real_rect.bottom -= dy
                else:
                    dx = 0.5 * (rect.height * constrained_aspect - rect.width)
                    self._real_rect.left -= dx
                    self._real_rect.right += dx

            self.transform.set_mapping(self._real_rect, vbr, update=False)
            self.transform.zoom((1, 1, 1 / d))

            # scale, then translate: the matrix of STTransform.as_matrix() without the intermediate objects
            scale = self.transform.scale
            translate = self.transform.translate
            matrix = np.zeros((4, 4))
            matrix[0, 0] = scale[0]
            matrix[1, 1] = scale[1]
            matrix[2, 2] = scale[2]
            matrix[3, :3] = translate[:3]
            matrix[3, 3] = 1.0
            _camera_step[0] += 1
            try:
                self.tf_mat.matrix = matrix
            finally:
                _camera_step[0] -= 1
            self._set_scene_transform(self.tf_mat)  # ends with the one ViewBox.update()

        _update_transform.flatcam_patch = True
        PanZoomCamera._update_transform = _update_transform

    _patch_axis_deferred_domain()
    _patch_shader_value_changes()
    _patch_camera_step_updates()
    _patch_batched_flush()
    _patch_glir_draw_check()
    _patch_set_state()
    _patch_update_variables()
    _patch_pixel_scale()
    _patch_perspective_camera()

    # Canvas frame probe (development): installed last, so that it wraps the final functions.
    if os.environ.get('FLATCAM_CANVAS_PROF') == '1':
        from appGUI import VisPyProfile
        VisPyProfile.install()
