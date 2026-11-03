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
        self._preview_timer = None
        self._preview_shapes = None
        self._view = None
        self.preview_done_sig.connect(self.on_preview_done)
        self.generation_message_sig.connect(self.on_generation_message)

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
        self.update_mode()
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
        for widget in self.form_fields.values():
            if isinstance(widget, QtWidgets.QComboBox):
                signal = widget.currentIndexChanged
            elif isinstance(widget, QtWidgets.QCheckBox):
                signal = widget.toggled
            else:
                signal = widget.valueChanged
            signal.connect(self.on_value_changed)
        self.ui.load_button.clicked.connect(self.on_load_image)
        self.ui.preview_button.clicked.connect(self.on_preview)
        self.ui.generate_button.clicked.connect(self.on_generate)
        self.ui.milling_button.clicked.connect(self.on_open_milling)
        self.ui.sim_button.clicked.connect(self.on_simulate)
        self.ui.stop_button.clicked.connect(self.on_stop)

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
        for name in ('tone_entry', 'tone_label'):
            getattr(self.ui, name).setVisible(mode not in ('laser', 'halftone'))
        self.ui.sections['tool_kind'].setVisible(mode != 'laser')
        self.ui.sections['rough'].setVisible(mode != 'laser')
        self.ui.sections['litho_min'].setVisible(mode == 'lithophane')
        self.ui.spacing_label.setText(_("Line spacing") if mode == 'laser' else _("Spacing"))
        self.ui.vbit_limit_label.setVisible(mode in ('vcarve', 'halftone'))
        try:
            params = JobParams.from_options(self.form_values(), (1, 1), 'MM', self.decimals, (0, 0, 0, 0))
            self.ui.vbit_limit_label.setText(_("V-bit depth limit: %s") %
                                           ('%.*f' % (self.decimals, vbit_limit(params))))
        except (ValueError, ZeroDivisionError):
            self.ui.vbit_limit_label.setText(_("Invalid V-bit parameters"))

    def on_value_changed(self, *args):
        self.update_mode()
        self.form_to_storage()
        self.update_image_labels()
        self.clear_preview()
        self._preview_timer.start()

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
        self.on_value_changed()

    def capture_params(self):
        if self.image is None:
            raise PhotoCarveError(_("Load an image first."))
        units, decimals, skew = capture_machine(self.app)
        return JobParams.from_options(self.form_values(), self.image.size, units, decimals, skew)

    def on_preview(self, *args):
        if self.image is None:
            return
        self._preview_timer.stop()
        self.clear_preview()
        try:
            params = self.capture_params()
            image = self.image.copy()
        except (PhotoCarveError, ValueError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(str(err)))
            return
        self.app.worker_task.emit({'fcn': self.gen.preview, 'params': [self._preview_token, image, params]})
        store = self._preview_store()
        if store is not None:
            store.add(box(params.x0, params.y0, params.x0 + params.width, params.y0 + params.height),
                      color='#00AA00FF', face_color='#00AA0022', update=False, layer=0, tolerance=None)
            store.redraw()

    def on_preview_done(self, token, result):
        if token != self._preview_token or self.ui is None:
            return
        if isinstance(result, str):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(result))
            return
        pixmap = QtGui.QPixmap.fromImage(ImageQt(result.convert('RGBA')))
        self.ui.preview_label.setPixmap(pixmap.scaled(256, 256, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                                       QtCore.Qt.TransformationMode.SmoothTransformation))

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
        self.app.worker_task.emit({'fcn': self.gen.generate,
                                   'params': [self._gen_token, image, params, milldefaults, outname]})

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
        simulate = getattr(self.gen, 'simulate', None)
        if simulate is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The 3D simulation view is not available yet."))
            return
        simulate()

    def on_stop(self):
        self._sim_session += 1
        if self._view is not None:
            self._view.close()
            self._view = None

    def on_plugin_cleanup(self):
        self.clear_preview()
        self.on_stop()
        if self._preview_timer is not None:
            self._preview_timer.stop()
        if self.ui is not None:
            self.ui.preview_label.clear()

    def on_project_reset(self):
        self._gen_token += 1
        self.result_obj_name = None
        self.on_plugin_cleanup()
