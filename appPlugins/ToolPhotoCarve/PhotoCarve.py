"""Photo carving MVC controller. Workers receive detached inputs, never form widgets."""

import os
from copy import deepcopy

from PyQt6 import QtCore, QtGui, QtWidgets
from PIL.ImageQt import ImageQt
from shapely import box

from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from .PhotoCarveUI import PhotoCarveUI, CHOICES
from .PhotoCarveModel import DEFAULTS, PHOTOCARVE_KEYS, JobParams, PhotoCarveError, load_image, vbit_limit
from .PhotoCarveGen import PhotoCarveGen, capture_machine

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolPhotoCarve(AppTool):
    pluginName = _("Photo Carve")
    preview_done_sig = QtCore.pyqtSignal(int, object)
    generation_message_sig = QtCore.pyqtSignal(int, str, str)
    generation_done_sig = QtCore.pyqtSignal(int)
    sim_step_sig = QtCore.pyqtSignal(int, bool, object)
    # The parameters of the 3D simulation only: a change of them keeps the generated geometry.
    SIM_KEYS = ('tools_photocarve_sim_material', 'tools_photocarve_sim_thickness', 'tools_photocarve_sim_res')

    def __init__(self, app):
        super().__init__(app)
        self.decimals = app.decimals
        self.ui = None
        self.gen = None
        self.form_fields = {}
        self.image = None
        self.image_filename = None
        self.result_obj_name = None
        self._preview_token = 0
        self._sim_session = 0
        self._gen_token = 0
        self._gen_running = None
        self._preview_timer = None
        self._preview_shapes = None
        # the 1st preview after the plugin opens or an image loads fits the 2D view to its outline
        self._fit_preview = True
        self._view = None
        self.preview_done_sig.connect(self.on_preview_done)
        self.generation_message_sig.connect(self.on_generation_message)
        self.generation_done_sig.connect(self.on_generation_done)
        self.sim_step_sig.connect(self.on_sim_step, QtCore.Qt.ConnectionType.QueuedConnection)

    def install(self, icon=None, separator=None, **kwargs):
        super().install(icon=icon, separator=separator, shortcut='Alt+V', **kwargs)
        self.app.cleanup.connect(self.on_plugin_cleanup)

    def run(self, toggle=True):
        self.app.defaults.report_usage('ToolPhotoCarve()')
        if self.app.plugin_tab_locked is True:
            return
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])
        notebook = self.app.ui.notebook
        found = next((idx for idx in range(notebook.count())
                      if notebook.widget(idx).objectName() == 'plugin_tab'), None)
        if found is None:
            try:
                notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
            except RuntimeError:
                self.app.ui.plugin_tab = QtWidgets.QWidget()
                self.app.ui.plugin_tab.setObjectName('plugin_tab')
                self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)
                self.app.ui.plugin_scroll_area = VerticalScrollArea()
                self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
        current = self.app.ui.plugin_scroll_area.widget()
        if toggle and found is not None and current is self and notebook.currentWidget() is self.app.ui.plugin_tab:
            self.on_plugin_cleanup()
            notebook.setCurrentWidget(self.app.ui.properties_tab)
            notebook.removeTab(found)
            if not self.app.collection.get_list():
                self.app.ui.splitter.setSizes([0, 1])
            return
        super().run()
        self.set_tool_ui()
        notebook.setTabText(notebook.indexOf(self.app.ui.plugin_tab), self.pluginName)

    def set_tool_ui(self):
        self.on_plugin_cleanup()
        self.clear_ui(self.layout)
        self.ui = PhotoCarveUI(self.layout, self.app)
        self.gen = PhotoCarveGen(self)
        self.bind_form()
        self.storage_to_form(self.app.options)
        self.ui.level.setChecked(self.app.options.get('global_app_level', 'b') == 'a')
        self.on_level_changed(self.ui.level.isChecked())
        self.connect_signals()
        self.update_image_labels()

    def bind_form(self):
        self.form_fields = {key: getattr(self.ui, name + '_entry')
                            for name, key in zip(DEFAULTS, PHOTOCARVE_KEYS)}

    def storage_to_form(self, storage):
        for name, key in zip(DEFAULTS, PHOTOCARVE_KEYS):
            widget = self.form_fields[key]
            blocker = QtCore.QSignalBlocker(widget)
            widget.set_value(storage.get(key, DEFAULTS[name]))
            del blocker

    def form_values(self):
        return {key: widget.get_value() for key, widget in self.form_fields.items()}

    def form_to_storage(self, *args):
        self.app.options.update(self.form_values())

    def connect_signals(self):
        if self._preview_timer is None:
            self._preview_timer = QtCore.QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(300)
            self._preview_timer.timeout.connect(self.on_preview)
        for key, widget in self.form_fields.items():
            if isinstance(widget, QtWidgets.QComboBox):
                signal = widget.currentIndexChanged
            elif isinstance(widget, QtWidgets.QCheckBox):
                signal = widget.toggled
            else:
                signal = widget.valueChanged
            signal.connect(self.form_to_storage if key in self.SIM_KEYS else self.on_value_changed)
        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.load_button.clicked.connect(self.on_load_image)
        self.ui.mirror_x_button.clicked.connect(lambda: self.on_mirror_image('x'))
        self.ui.mirror_y_button.clicked.connect(lambda: self.on_mirror_image('y'))
        self.ui.preview_button.clicked.connect(self.on_preview)
        self.ui.generate_button.clicked.connect(self.on_generate)
        self.ui.milling_button.clicked.connect(self.on_open_milling)
        self.ui.sim_button.clicked.connect(self.on_simulate)
        self.ui.stop_button.clicked.connect(self.on_stop_button)

    def update_mode(self):
        mode = self.ui.mode_entry.get_value()
        strategies = {'vcarve': ('raster', 'spiral'), 'halftone': ('-',), 'relief': ('x', 'y', 'xy'),
                      'lithophane': ('x', 'y', 'xy'), 'laser': ('gray', 'bayer', 'floyd')}
        tones = {'vcarve': ('linear', 'perceptual', 'vbit_area'), 'relief': ('linear', 'perceptual'),
                 'lithophane': ('lithophane',), 'halftone': ('linear',), 'laser': ('linear',)}
        kinds = ('vbit',) if mode in ('vcarve', 'halftone') else ('vbit', 'flat', 'ball')
        for name, allowed in (('strategy', strategies[mode]), ('tone', tones[mode]), ('tool_kind', kinds)):
            widget = getattr(self.ui, name + '_entry')
            value = widget.get_value()
            blocker = QtCore.QSignalBlocker(widget)
            widget.clear()
            for text, identifier in CHOICES[name]:
                if identifier in allowed:
                    widget.add_choice(text, identifier)
            widget.set_value(value if value in allowed else allowed[0])
            del blocker
        self.ui.tool_kind_entry.setEnabled(mode not in ('vcarve', 'halftone'))
        self.ui.tone_entry.setEnabled(mode != 'lithophane')
        self.ui.apply_visibility(mode, self.ui.strategy_entry.get_value(), self.ui.tool_kind_entry.get_value(),
                                 bool(self.ui.rough_entry.get_value()), self.ui.rough_kind_entry.get_value(),
                                 self.ui.level.isChecked())
        try:
            params = JobParams.from_options(self.form_values(), (1, 1), 'MM', self.decimals, (0, 0, 0, 0))
            self.ui.vbit_limit_label.setText(_("V-bit depth limit: %s") %
                                           ('%.*f' % (self.decimals, vbit_limit(params))))
        except (ValueError, ZeroDivisionError):
            self.ui.vbit_limit_label.setText(_("Invalid V-bit parameters"))

    def on_level_changed(self, checked):
        """Beginner hides the advanced fields; the values stay and are still used."""
        self.ui.level.setText(_('Advanced') if checked else _('Beginner'))
        self.ui.level.setStyleSheet('QToolButton { color: %s; }' % ('red' if checked else 'green'))
        self.update_mode()

    def on_value_changed(self, *args):
        """A process parameter (or the image) changed: the old geometry and the old preview do not match it."""
        self.update_mode()
        self.form_to_storage()
        self.update_image_labels()
        self.reset_result()
        self.clear_preview()
        if self.ui is not None:
            self.ui.preview_label.clear()
            self.ui.preview_label.setVisible(False)
        self._preview_timer.start()

    def reset_result(self):
        """Stop a running generation, close the simulation and delete the geometry of the old parameters."""
        self._gen_token += 1
        self.close_3d()
        name, self.result_obj_name = self.result_obj_name, None
        obj = self.app.collection.get_by_name(name) if name else None
        if obj is None or not obj.obj_options.get('photocarve'):
            return
        from appHandlers.appHistory import history_of
        history_of(self.app).record_delete(self.pluginName, [obj])
        # The user types in a field of this plugin: each key press changes the value. The deletion of the last
        # object must not hide the notebook ("global_project_autohide"), because the hidden field loses the
        # keyboard focus and the next key presses are lost.
        focus = QtWidgets.QApplication.focusWidget()
        self.app.collection.delete_by_name(name, select_project=False, autohide=False)
        if focus is not None and QtWidgets.QApplication.focusWidget() is not focus:
            focus.setFocus()
        self.app.inform.emit('[WARNING_NOTCL] %s' %
                             _("The parameters changed: the old Photo Carve geometry was deleted. Generate again."))

    def close_3d(self):
        """Stop the simulation and close the Plot 3D tab, but only if this plugin shows its simulation there."""
        shown = self._view is not None
        self.on_stop()
        canvas = getattr(self.app, 'plotcanvas3d', None)
        if not shown or canvas is None:
            return
        tabs = self.app.ui.plot_tab_area
        index = tabs.indexOf(self.app.area_3d_tab)
        if index >= 0:
            tabs.closeTab(index)

    def update_image_labels(self):
        if self.image is None or self.ui is None:
            return
        width, height = self.image.size
        self.ui.file_label.setText(os.path.basename(self.image_filename or ''))
        self.ui.px_label.setText(_("Pixels: %d × %d") % (width, height))
        self.ui.height_label.setText(_("Height: %s") %
                                     ('%.*f' % (self.decimals, self.ui.width_entry.get_value() * height / width)))

    def on_load_image(self, checked=False, filename=None):
        if filename is None:
            filename, _filter = QtWidgets.QFileDialog.getOpenFileName(caption=_("Load Image"))
        if not filename:
            return
        try:
            image = load_image(filename)
        except PhotoCarveError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(str(err)))
            return 'fail'
        self.image = image
        self.image_filename = filename
        self._fit_preview = True
        self.on_value_changed()

    def open_source(self, obj):
        """Open the plugin with the image and the job parameters that a Photo Carve geometry keeps.

        The geometry stays the result of the plugin: a parameter change deletes it, as for a new job."""
        from .PhotoCarveObject import read_source
        try:
            source = read_source(obj.obj_options.get('photocarve'), self.app.units)
        except PhotoCarveError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(str(err)))
            return 'fail'
        if source is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("This Photo Carve object has no source image. "
                                                          "Load the image again in the Photo Carve plugin."))
            return 'fail'
        image, filename, options = source
        self.run(toggle=False)
        if self.ui is None:
            return 'fail'
        self._gen_token += 1
        self.result_obj_name = None
        self.storage_to_form(options)
        self.image, self.image_filename = image, filename
        self._fit_preview = True
        self.on_value_changed()
        self.result_obj_name = obj.obj_options['name']
        self.app.inform.emit('[success] %s' % _("Photo Carve opened with the image and the parameters of the object."))

    def on_mirror_image(self, axis):
        """Mirror the loaded image in place: 'x' left to right, 'y' top to bottom. The preview is made again."""
        if self.image is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Load an image first."))
            return
        from PIL import ImageOps
        self.image = ImageOps.mirror(self.image) if axis == 'x' else ImageOps.flip(self.image)
        self.on_value_changed()
        self.on_preview()

    def capture_params(self):
        if self.image is None:
            raise PhotoCarveError(_("Load an image first."))
        units, decimals, skew = capture_machine(self.app)
        return JobParams.from_options(self.form_values(), self.image.size, units, decimals, skew)

    def on_preview(self, *args):
        """Timer and button. Only the button reports, so a value change does not fill the status bar."""
        self._preview_timer.stop()
        manual = self.sender() is self.ui.preview_button if self.ui is not None else False
        if manual:
            self.close_3d()
        if self.image is None:
            if manual:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Load an image first."))
            return
        self.clear_preview()
        try:
            params = self.capture_params()
            image = self.image.copy()
        except (PhotoCarveError, ValueError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(str(err)))
            return
        self._preview_aspect = params.height / params.width
        self._preview_manual = manual
        self.app.worker_task.emit({'fcn': self.gen.preview, 'params': [self._preview_token, image, params]})
        store = self._preview_store()
        if store is not None:
            store.add(box(params.x0, params.y0, params.x0 + params.width, params.y0 + params.height),
                      color='#00AA00FF', face_color='#00AA0022', update=False, layer=0, tolerance=None)
            store.redraw()
            if self._fit_preview:
                self._fit_preview = False
                self.fit_to_outline(params)

    def fit_to_outline(self, params):
        """Zoom the 2D view to the area of the job. Later previews keep the zoom of the user."""
        from vispy.geometry import Rect
        try:
            self.app.plotcanvas.fit_view(rect=Rect(params.x0, params.y0, params.width, params.height))
        except Exception as err:
            self.app.log.error('ToolPhotoCarve.fit_to_outline() --> %s', err)

    def on_preview_done(self, token, result):
        if token != self._preview_token or self.ui is None:
            return
        if isinstance(result, str):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(result))
            return
        pixmap = QtGui.QPixmap.fromImage(ImageQt(result.convert('RGBA')))
        # The grid cells are not square in Laser mode (rows = line spacing): show the physical aspect.
        box = self.ui.preview_label.size()
        aspect = getattr(self, '_preview_aspect', result.height / max(result.width, 1))
        width = min(box.width(), box.height() / aspect) if aspect > 0 else box.width()
        height = width * aspect
        self.ui.preview_label.setPixmap(pixmap.scaled(max(1, round(width)), max(1, round(height)),
                                                       QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                                       QtCore.Qt.TransformationMode.SmoothTransformation))
        self.ui.preview_label.setVisible(True)
        if getattr(self, '_preview_manual', False):
            self.app.inform.emit('[success] %s' % _("Preview of the job: the area is shown on the canvas."))

    def _preview_store(self):
        if self._preview_shapes is None:
            try:
                self._preview_shapes = self.app.plotcanvas.new_shape_collection(layers=1)
            except Exception as err:
                self.app.log.error('ToolPhotoCarve._preview_store() --> %s', err)
        return self._preview_shapes

    def clear_preview(self):
        self._preview_token += 1
        if self._preview_shapes is not None:
            self._preview_shapes.clear(update=True)

    def on_generate(self, *args):
        self.close_3d()
        try:
            params = self.capture_params()
            image = self.image.copy()
            milldefaults = deepcopy({key: value for key, value in self.app.options.items()
                                     if key.startswith('tools_mill_')})
            outname = os.path.splitext(os.path.basename(self.image_filename or _("photo")))[0] + '_photocarve'
            self._gen_token += 1
        except (PhotoCarveError, ValueError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(str(err)))
            return 'fail'
        self._gen_running = self._gen_token
        self.set_generating(True)
        self.app.inform.emit('%s...' % _("Working"))
        self.app.worker_task.emit({'fcn': self.gen.generate,
                                   'params': [self._gen_token, image, params, milldefaults, outname,
                                              self.image_filename]})

    def set_generating(self, busy):
        """The Generate button shows that the worker runs, and a 2nd click cannot start the same job again."""
        if self.ui is None:
            return
        self.ui.generate_button.setEnabled(not busy)
        self.ui.generate_button.setText(_("Generating...") if busy else _("Generate Geometry"))

    def on_generation_done(self, token):
        # an older worker that ends after a new Generate must not enable the button of the running one
        if token == self._gen_running:
            self._gen_running = None
            self.set_generating(False)

    def on_generation_message(self, token, level, message):
        if token == self._gen_token:
            self.app.inform.emit('%s %s' % (level, _(message)))

    def on_open_milling(self):
        if not self.result_obj_name or self.app.collection.get_by_name(self.result_obj_name) is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Generate photo engraving geometry first."))
            return 'fail'
        self.app.collection.set_all_inactive()
        self.app.collection.set_active(self.result_obj_name)
        self.app.milling_tool.run(toggle=False)

    def on_simulate(self):
        return self.gen.simulate()

    def on_sim_step(self, session_id, ok, payload):
        if self.gen is not None:
            self.gen.on_sim_step(session_id, ok, payload)

    def on_stop_button(self):
        """Stop the animation and keep the stock that is cut so far, without the paths and the tool."""
        self._sim_session += 1
        if self.gen is not None:
            self.gen.stop_simulation()
        if self._view is not None:
            self._view.finish()

    def on_stop(self):
        """Stop the simulation and remove its visuals."""
        self._sim_session += 1
        if self.ui is not None:
            self.ui.sim_progress.setValue(0)
        if self.gen is not None:
            self.gen.stop_simulation()
        if self._view is not None:
            self._view.clear()
            self._view = None

    def on_plugin_cleanup(self):
        self._fit_preview = True
        self.clear_preview()
        self.on_stop()
        if self._preview_timer is not None:
            self._preview_timer.stop()
        if self.ui is not None:
            self.ui.preview_label.clear()
            self.ui.preview_label.setVisible(False)

    def on_project_reset(self):
        self._gen_token += 1
        self.result_obj_name = None
        self.on_plugin_cleanup()
