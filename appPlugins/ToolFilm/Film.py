# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from defaults import AppDefaults
from appGUI.GUIElements import VerticalScrollArea, FCFileSaveDialog
from appGUI.ImagePreview import ImagePreview
from camlib import deepcopy_keep_geometry
from appHandlers.appHistory import history_of, object_state, restore_object_state
from appPlugins.ToolFilm.FilmUI import FilmUI
from appPlugins.ToolFilm import FilmGen
from shapely.ops import unary_union
from reportlab.lib.pagesizes import landscape, portrait
from vispy.geometry import Rect

import logging
import gettext
import appTranslation as fcTranslate
import builtins
from pathlib import Path

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class Film(AppTool):
    generation_done = QtCore.pyqtSignal(int, object, str)

    def __init__(self, app):
        AppTool.__init__(self, app)
        self._token = 0
        self._running = False
        self._active = False
        self._artifact = None
        self._save_token = None
        self._inputs = ()
        self._input_stamps = ()
        self._image_preview = None
        self._film_object = None
        self._film_state = None
        self._pdf_document = None
        self._pdf_state = None
        self._workspace_snapshot = None
        self.decimals = self.app.decimals
        self.units = self.app.app_units
        self.ui = FilmUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()
        self.generation_done.connect(self.on_generation_done, QtCore.Qt.ConnectionType.QueuedConnection)
        self.app.cleanup.connect(self.on_plugin_cleanup)
        self.app.app_obj.object_changed.connect(self.on_input_changed)
        self.app.object_status_changed.connect(self.on_input_changed)
        self.app.history_changed.connect(self.on_input_changed)
        self.app.ui.plot_tab_area.tab_closed_signal.connect(self.on_preview_closed)

    def event(self, event):
        result = super().event(event)
        if event.type() in (QtCore.QEvent.Type.Hide, QtCore.QEvent.Type.ParentChange) and \
                getattr(self, '_active', False):
            QtCore.QTimer.singleShot(0, self.cleanup_if_inactive)
        return result

    def cleanup_if_inactive(self):
        ui = self.app.ui
        if self._active and (ui.plugin_scroll_area.widget() is not self or
                             ui.notebook.indexOf(ui.plugin_tab) < 0):
            self.on_plugin_cleanup()

    def invalidate(self, *args):
        self._token += 1
        self._artifact = None
        self._save_token = None
        self.ui.save_button.setEnabled(self._active and not self._running)

    def on_input_changed(self, obj=None, *args):
        current_objects = self.app.collection.get_list()
        # No deep copy on this path: a signal of a tracked object invalidates, any other signal compares the
        # cheap stamps (identities of the geometry containers plus small values) taken at Generate. Undo / Redo
        # replace solid_geometry by a new object and emit object_changed, so both routes catch them.
        if self._film_object is not None and not any(self._film_object is current for current in current_objects):
            self._film_object = None
            self._film_state = None
            if self._artifact is not None and self._artifact['format'] == 'svg':
                self.invalidate()
        if self._artifact is not None and self._artifact['format'] == 'svg' and (
                self._film_object is None or
                not self.stamps_equal(self._film_state, self.film_state(self._film_object))):
            self.invalidate()
        if self._pdf_document is not None and not any(self._pdf_document is current for current in current_objects):
            self._pdf_document = None
            self._pdf_state = None
            if self._artifact is not None and self._artifact['format'] == 'pdf':
                self.invalidate()
        if self._artifact is not None and self._artifact['format'] == 'pdf' and (
                self._pdf_document is None or self._pdf_state != (
                    self._pdf_document.source_file, self._pdf_document.document_type)):
            self.invalidate()
        if any(obj is source for source in self._inputs) or any(
                not any(source is current for current in current_objects) or
                not self.stamps_equal(state, self.input_stamp(source))
                for source, state in zip(self._inputs, self._input_stamps)):
            self.invalidate()

    @staticmethod
    def stamps_equal(first, second):
        """The containers by identity (the stamp keeps them alive), the other values by equality."""
        return first is not None and second is not None and len(first) == len(second) and \
            first[0] is second[0] and first[1] is second[1] and first[2:] == second[2:]

    @staticmethod
    def film_state(obj):
        """Cheap stamp of the generated film object: geometry / tools identity, units, colors, bounds."""
        if not obj.tools:
            return None
        film = obj.obj_options.get('film') or {}
        return (obj.tools[next(iter(obj.tools))]['solid_geometry'], obj.tools, obj.units,
                tuple(layer.get('color') for layer in film.get('layers', ())),
                tuple(obj.obj_options.get(key) for key in ('xmin', 'ymin', 'xmax', 'ymax')),
                tuple((uid, id(tool.get('solid_geometry'))) for uid, tool in obj.tools.items()))

    def input_stamp(self, obj):
        """Cheap stamp of an input: geometry / tools identity, units, name, film colors."""
        geometry = obj.tools if obj.kind == 'geometry' else obj.solid_geometry
        tool_shapes = tuple((uid, id(tool.get('solid_geometry'))) for uid, tool in obj.tools.items()) \
            if obj.kind == 'geometry' else ()
        return (geometry, getattr(obj, 'tools', None), obj.units, obj.obj_options['name'],
                obj.obj_options.get('tools_film_color'), self.app_option('tools_film_color'), tool_shapes)

    @staticmethod
    def input_tools(obj):
        """A copy of the aperture data the punching reads."""
        return deepcopy_keep_geometry({
            key: {field: value for field, value in tool.items()
                  if field in ('type', 'size', 'width', 'height', 'geometry', 'solid_geometry')}
            for key, tool in (getattr(obj, 'tools', None) or {}).items()})

    def on_preview_closed(self, name, index):
        if self.app.ui.plot_tab_area.widget(index) is self._image_preview:
            preview, self._image_preview = self._image_preview, None
            if preview is not None:
                preview.deleteLater()

    def close_image_preview(self):
        if self._image_preview is not None:
            area = self.app.ui.plot_tab_area
            if area.indexOf(self._image_preview) < 0:
                for window in list(area.detachedTabs.values()):
                    if window.contentWidget is self._image_preview and window.isVisible():
                        window.close()
                        break
            index = area.indexOf(self._image_preview)
            if index >= 0:
                area.closeTab(index)

    def on_plugin_cleanup(self):
        # Restore workspace state if we saved it
        if self._workspace_snapshot is not None:
            canvas = self.app.plotcanvas
            workspace_cb = self.app.ui.general_pref_form.general_app_set_group.workspace_cb
            snapshot = self._workspace_snapshot
            self._workspace_snapshot = None
            # Restore original visibility
            if workspace_cb.get_value() != snapshot['visible']:
                self.app.on_workspace_toggle()
            # Restore original camera rect
            try:
                canvas.view.camera.rect = Rect(snapshot['rect'])
            except (TypeError, AttributeError):
                pass
        self._active = False
        self.invalidate()
        self._inputs = ()
        self._input_stamps = ()
        self._film_object = None
        self._film_state = None
        if self._pdf_document is not None:
            self.app.pdf_viewer.close_owned_tabs(self._pdf_document)
        self._pdf_document = None
        self._pdf_state = None
        self.close_image_preview()

    def app_option(self, key):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        return self.app.options.get(key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))

    def on_type_obj_index_changed(self, val):
        obj_type = 2 if val == 'geo' else 0
        self.ui.tf_object_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.tf_object_combo.setCurrentIndex(0)
        self.ui.tf_object_combo.obj_type = {
            "grb": "gerber", "geo": "geometry"
        }[self.ui.tf_type_obj_combo.get_value()]

    def on_type_box_index_changed(self, val):
        obj_type = 2 if val == 'geo' else 0
        self.ui.tf_box_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.tf_box_combo.setCurrentIndex(0)
        self.ui.tf_box_combo.obj_type = {
            "grb": "gerber", "geo": "geometry"
        }[self.ui.tf_type_box_combo.get_value()]

    def on_object_selection_changed(self, current, previous):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break
        if found_idx:
            try:
                name = current.indexes()[0].internalPointer().obj.obj_options['name']
                kind = current.indexes()[0].internalPointer().obj.kind
                if kind in ['gerber', 'geometry']:
                    obj_type = {'gerber': 'grb', 'geometry': 'geo'}[kind]
                    self.ui.tf_type_obj_combo.set_value(obj_type)
                    self.ui.tf_type_box_combo.set_value(obj_type)
                    self.ui.tf_object_combo.set_value(name)
                    self.ui.tf_box_combo.set_value(name)
            except Exception:
                pass

    def run(self, toggle=True):
        if getattr(self.app, 'plugin_tab_locked', False):
            return
        self.app.defaults.report_usage("ToolFilm()")
        if toggle:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])
            found_idx = None
            for idx in range(self.app.ui.notebook.count()):
                if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                    found_idx = idx
                    break
            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx is not None:
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(found_idx)
                        self.on_plugin_cleanup()
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
                        return
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])
        super().run()
        if self.app.ui.notebook.indexOf(self.app.ui.plugin_tab) < 0:
            self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Film"))
            self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
        self.set_tool_ui()
        self._active = True
        self.app.ui.notebook.setTabText(self.app.ui.notebook.indexOf(self.app.ui.plugin_tab), _("Film"))
        # Save workspace state on first activation and show workspace
        if self._workspace_snapshot is None:
            canvas = self.app.plotcanvas
            workspace_cb = self.app.ui.general_pref_form.general_app_set_group.workspace_cb
            # Save original state
            self._workspace_snapshot = {
                'visible': workspace_cb.get_value(),
                'rect': Rect(canvas.view.camera.rect)
            }
            # Show workspace if hidden
            if not workspace_cb.get_value():
                self.app.on_workspace_toggle()
            # Fit camera to workspace bounds
            if canvas.workspace_line is not None and canvas.workspace_line.parent is not None:
                try:
                    left, right = canvas.workspace_line.bounds(axis=0)
                    bottom, top = canvas.workspace_line.bounds(axis=1)
                    fit_rect = Rect(left, bottom, right - left, top - bottom)
                    canvas.fit_view(rect=fit_rect)
                except (TypeError, AttributeError):
                    pass

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+L', **kwargs)

    def connect_signals_at_init(self):
        self.connect_selection_signal()
        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.film_object_button.clicked.connect(self.on_film_creation)
        self.ui.save_button.clicked.connect(self.save_film)
        self.ui.tf_type_obj_combo.activated_custom.connect(self.on_type_obj_index_changed)
        self.ui.tf_type_box_combo.activated_custom.connect(self.on_type_box_index_changed)
        self.ui.film_type.activated_custom.connect(self.ui.on_film_type)
        self.ui.source_punch.activated_custom.connect(self.ui.on_punch_source)
        self.ui.file_type_radio.activated_custom.connect(self.ui.on_file_type)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)
        for widget in self.findChildren(QtWidgets.QComboBox):
            assert isinstance(widget, QtWidgets.QComboBox)
            widget.currentIndexChanged.connect(self.invalidate)
        for widget in self.findChildren(QtWidgets.QAbstractSpinBox):
            assert isinstance(widget, QtWidgets.QAbstractSpinBox)
            widget.valueChanged.connect(self.invalidate)
        for widget in self.findChildren(QtWidgets.QAbstractButton):
            assert isinstance(widget, QtWidgets.QAbstractButton)
            if widget.isCheckable():
                widget.toggled.connect(self.invalidate)
        for widget in self.findChildren(QtWidgets.QLineEdit):
            assert isinstance(widget, QtWidgets.QLineEdit)
            widget.textChanged.connect(self.invalidate)

    def set_tool_ui(self):
        self.invalidate()
        self.close_image_preview()
        self._inputs = ()
        self._input_stamps = ()
        self.clear_ui(self.layout)
        self.ui = FilmUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()
        self.reset_fields()
        app_option = self.app_option
        f_type = app_option("tools_film_polarity") or 'neg'
        self.ui.film_type.set_value(str(f_type))
        self.ui.on_film_type(val=f_type)
        b_entry = app_option("tools_film_boundary") or 0.0
        self.ui.boundary_entry.set_value(float(b_entry))
        scale_stroke_width = app_option("tools_film_scale_stroke") or 0.0
        self.ui.film_scale_stroke_entry.set_value(float(scale_stroke_width))
        self.ui.punch_cb.set_value(False)
        self.ui.source_punch.set_value('exc')
        self.ui.film_scale_cb.set_value(app_option("tools_film_scale_cb"))
        self.ui.film_scalex_entry.set_value(float(app_option("tools_film_scale_x_entry")))
        self.ui.film_scaley_entry.set_value(float(app_option("tools_film_scale_y_entry")))
        self.ui.scale_ref_combo.set_value(app_option("tools_film_scale_ref"))
        self.ui.film_skew_cb.set_value(app_option("tools_film_skew_cb"))
        self.ui.film_skew_type_combo.set_value(app_option("tools_film_skew_type"))
        self.ui.film_skewx_entry.set_value(float(app_option("tools_film_skew_x_entry")))
        self.ui.film_skewy_entry.set_value(float(app_option("tools_film_skew_y_entry")))
        self.ui.skew_ref_combo.set_value(app_option("tools_film_skew_ref"))
        self.ui.film_mirror_cb.set_value(app_option("tools_film_mirror_cb"))
        self.ui.film_mirror_axis.set_value(app_option("tools_film_mirror_axis_radio"))
        self.ui.file_type_radio.set_value(app_option("tools_film_file_type_radio"))
        self.ui.orientation_radio.set_value(app_option("tools_film_orientation"))
        self.ui.pagesize_combo.set_value(app_option("tools_film_pagesize"))
        self.ui.png_dpi_spinner.set_value(app_option("tools_film_png_dpi"))
        self.ui.convex_box_cb.set_value(app_option("tools_film_shape"))
        self.ui.rounded_cb.set_value(app_option("tools_film_rounded"))
        obj = self.app.collection.get_active()
        if obj and obj.kind in ['gerber', 'geometry']:
            obj_name = obj.obj_options['name']
            if obj.kind == 'gerber':
                self.ui.tf_type_obj_combo.set_value('grb')
                self.ui.tf_type_box_combo.set_value('grb')
                self.on_type_obj_index_changed(val='grb')
                self.on_type_box_index_changed(val='grb')
            elif obj.kind == 'geometry':
                self.ui.tf_type_obj_combo.set_value('geo')
                self.ui.tf_type_box_combo.set_value('geo')
                self.on_type_obj_index_changed(val='geo')
                self.on_type_box_index_changed(val='geo')
            self.ui.tf_object_combo.set_value(obj_name)
            self.ui.tf_box_combo.set_value(obj_name)
        else:
            self.ui.tf_type_obj_combo.set_value('grb')
            self.ui.tf_type_box_combo.set_value('grb')
            self.on_type_obj_index_changed(val='grb')
            self.on_type_box_index_changed(val='grb')
        self.change_level(app_option("global_app_level"))
        self.ui.film_object_button.setEnabled(not self._running)
        self.ui.save_button.setEnabled(not self._running)

    def change_level(self, level):
        if level == 'a':
            self.ui.level.setChecked(True)
        else:
            self.ui.level.setChecked(False)
        self.on_level_changed(self.ui.level.isChecked())

    def on_level_changed(self, checked):
        self.ui.on_level_changed(checked)
        if checked:
            app_option = self.app_option
            self.ui.film_scale_cb.set_value(app_option("tools_film_scale_cb"))
            self.ui.film_skew_cb.set_value(app_option("tools_film_skew_cb"))
            self.ui.film_mirror_cb.set_value(app_option("tools_film_mirror_cb"))
            scale_stroke_width = app_option("tools_film_scale_stroke") or 0.0
            self.ui.film_scale_stroke_entry.set_value(float(scale_stroke_width))

    def on_film_creation(self):
        self.cleanup_if_inactive()
        if self._running or not self._active:
            return
        self.invalidate()
        try:
            snapshot = self.capture_snapshot()
        except (ValueError, TypeError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)
            return
        token = self._token
        self._running = True
        self.ui.film_object_button.setEnabled(False)
        self.ui.save_button.setEnabled(False)
        self.app.inform.emit(_("Generating Film ..."))

        def work():
            with self.app.proc_container.new(_("Working...")):
                try:
                    # the unions of a big object take long: they run here, not on the GUI thread
                    shapes, box_shapes = snapshot['geometry'], snapshot['box']
                    snapshot['geometry'] = unary_union(shapes)
                    snapshot['box'] = snapshot['geometry'] if box_shapes is shapes else unary_union(box_shapes)
                    if snapshot['geometry'].is_empty or snapshot['box'].is_empty:
                        raise FilmGen.FilmError('empty', 'Film or box geometry is empty.')
                    artifact = FilmGen.generate_film(snapshot)
                except FilmGen.FilmError as error:
                    self.generation_done.emit(token, None, error.code)
                except Exception as error:
                    self.generation_done.emit(token, None, str(error))
                else:
                    self.generation_done.emit(token, artifact, '')

        self.app.worker_task.emit({'fcn': work, 'params': []})

    @staticmethod
    def error_text(code):
        """The translated text of a FilmGen error code; other texts pass unchanged."""
        texts = {
            'punch_too_big': _("Failed. Punch hole size is bigger than some of the apertures in the Gerber object."),
            'punch_unchanged': _("Failed. The new object geometry is the same as the one in the source object "
                                 "geometry..."),
            'empty': _("Film or box geometry is empty."),
            'outside_page': _("The artwork has to intersect the selected page size in order to be visible.\n"
                              "For 'Bounds' page size, it has to intersect the box plus border."),
            'png_invalid': _("Failed. Invalid PNG DPI value."),
            'png_too_large': _("The PNG is too large. Reduce the DPI or the film dimensions."),
            'render_failed': _("The film cannot be rendered."),
        }
        return texts.get(code, code)

    def capture_snapshot(self):
        file_type = self.ui.file_type_radio.get_value()
        obj = self.app.collection.get_by_name(self.ui.tf_object_combo.currentText())
        box_obj = self.app.collection.get_by_name(self.ui.tf_box_combo.currentText())
        if obj is None or box_obj is None:
            raise ValueError(_("Select a film source and box object."))
        inputs = [obj] if box_obj is obj else [obj, box_obj]
        holes = None
        punch = self.ui.film_type.get_value() == 'pos' and self.ui.punch_cb.get_value()
        if punch and self.ui.source_punch.get_value() == 'exc':
            exc = self.app.collection.get_by_name(self.ui.exc_combo.currentText())
            if exc is None:
                raise ValueError(_("Select an Excellon punching reference."))
            inputs.append(exc)
            holes = deepcopy_keep_geometry(exc.solid_geometry)
        # only the lists of the shapes are taken here; the worker makes the unions
        geometry = self.film_shapes(obj)
        box_geo = geometry if box_obj is obj else self.film_shapes(box_obj)
        if not geometry or not box_geo:
            raise ValueError(_("Film or box geometry is empty."))
        page_size = self.ui.pagesize_combo.get_value()
        page_size = None if page_size == 'Bounds' else (
            portrait(self.ui.pagesize[page_size]) if self.ui.orientation_radio.get_value() == 'p' else
            landscape(self.ui.pagesize[page_size]))
        self._inputs = tuple(inputs)
        self._input_stamps = tuple(self.input_stamp(source) for source in inputs)
        return {
            'geometry': geometry, 'box': box_geo, 'units': obj.units,
            'tools': self.input_tools(obj) if punch else {}, 'holes': holes, 'punch': punch,
            'circle_steps': self.app_option('geometry_circle_steps') or 32,
            'punch_size': self.ui.punch_size_spinner.get_value(),
            'transform': self.read_transform_params(), 'page_size': page_size, 'format': file_type,
            'dpi': self.ui.png_dpi_spinner.get_value(), 'name': obj.obj_options['name'],
            'negative': self.ui.film_type.get_value() == 'neg',
            'margin': self.ui.boundary_entry.get_value() or 0,
            'stroke': self.ui.film_scale_stroke_entry.get_value(),
            'color': obj.obj_options.get('tools_film_color') or self.app_option('tools_film_color') or '#000000',
            'rounded': self.ui.rounded_cb.get_value(), 'convex': self.ui.convex_box_cb.get_value(),
        }

    def on_generation_done(self, token, artifact, error):
        self._running = False
        self.cleanup_if_inactive()
        if not self._active:
            return
        self.ui.film_object_button.setEnabled(True)
        self.ui.save_button.setEnabled(True)
        self.on_input_changed()
        if token != self._token:
            return
        save_requested = self._save_token == token
        self._save_token = None
        if error:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Film generation failed"), self.error_text(error)))
            return
        try:
            if artifact['format'] == 'svg':
                self.commit_svg(artifact)
                self.close_image_preview()
            elif artifact['format'] == 'pdf':
                self.commit_pdf(artifact)
                self.close_image_preview()
            else:
                if self._image_preview is None:
                    preview = ImagePreview(artifact['data'], artifact['name'], artifact['dpi'], self.app)
                    self.app.ui.plot_tab_area.addTab(preview, artifact['name'])
                    self._image_preview = preview
                else:
                    self._image_preview.set_image(artifact['data'], artifact['name'], artifact['dpi'])
                area = self.app.ui.plot_tab_area
                self.app.pdf_viewer.reattach(self._image_preview)
                area.setTabText(area.indexOf(self._image_preview), artifact['name'])
                area.setCurrentWidget(self._image_preview)
        except (ValueError, RuntimeError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Film preview failed"), error))
            return
        if token != self._token or not self._active:
            return
        self._artifact = artifact
        self.ui.save_button.setEnabled(True)
        if save_requested:
            self.save_film()
        else:
            self.app.inform.emit('[success] %s' % _("Film generated. Inspect it, then Save."))

    def commit_pdf(self, artifact):
        """Keep the generated PDF in the project; its viewer belongs to the Document, not the tool."""
        def initialize(obj, app):
            obj.set_pdf(artifact['data'])

        obj = self._pdf_document
        if obj is None:
            obj = self.app.app_obj.new_object('document', artifact['name'].removesuffix('.pdf'), initialize,
                                              autoselected=False, plot=False)
            if obj is None or isinstance(obj, str):
                raise ValueError(_("Film generation failed"))
            obj.build_ui()
        else:
            before = object_state(obj)
            try:
                with history_of(self.app).snapshot(_("Film"), [obj]):
                    initialize(obj, self.app)
                    obj.build_ui()
            except Exception:
                restore_object_state(obj, before)
                obj.build_ui()
                raise
            self.app.should_we_save = True
            self.app.app_obj.object_changed.emit(obj)
        self._pdf_document = obj
        self._pdf_state = (obj.source_file, obj.document_type)

    def commit_svg(self, artifact):
        """Commit a completed worker snapshot on the GUI thread, as one history operation."""
        geometry = deepcopy_keep_geometry(artifact['geometry'])
        film = deepcopy_keep_geometry(artifact['film'])
        bounds = unary_union(geometry).bounds
        if not bounds or len(bounds) != 4:
            raise ValueError(_("Film or box geometry is empty."))

        def initialize(obj, app):
            obj.tools = {1: obj.default_tool()}
            obj.tools[1]['solid_geometry'] = geometry
            obj.units = artifact['units']
            dict.update(obj.obj_options, film=film, **dict(zip(('xmin', 'ymin', 'xmax', 'ymax'), bounds)))

        obj = self._film_object
        if obj is not None and any(obj is source for source in self._inputs):
            raise ValueError(_("Select a source other than the generated film object."))
        if obj is None:
            obj = self.app.app_obj.new_object('geometry', artifact['name'].removesuffix('.svg'), initialize,
                                              autoselected=False)
            if obj is None or isinstance(obj, str):
                raise ValueError(_("Film generation failed"))
        else:
            before = object_state(obj)
            try:
                with history_of(self.app).snapshot(_("Film"), [obj]):
                    initialize(obj, self.app)
                    obj.plot()
            except Exception:
                restore_object_state(obj, before)
                obj.plot()
                raise
            self.app.should_we_save = True
            self.app.app_obj.object_changed.emit(obj)
        self._film_object = obj
        self._film_state = self.film_state(obj)

    def save_film(self):
        self.cleanup_if_inactive()
        if self._running or not self._active:
            return False
        self.on_input_changed()
        artifact = self._artifact
        if artifact is None:
            self.on_film_creation()
            if self._running:
                self._save_token = self._token
            return False
        token = self._token
        suffix = artifact['format']
        filename, _filter = FCFileSaveDialog.get_saved_filename(
            parent=self, caption=_("Save generated film"),
            directory=str(Path(self.app.get_last_save_folder()) / artifact['name']),
            ext_filter='%s Files (*.%s)' % (suffix.upper(), suffix))
        self.cleanup_if_inactive()
        self.on_input_changed()
        if not filename or token != self._token or not self._active:
            return False
        if not filename.lower().endswith('.' + suffix):
            # the dialog already confirmed the overwrite of the name it returned; ask only for the new name
            filename += '.' + suffix
            if Path(filename).exists() and QtWidgets.QMessageBox.question(
                    self, _("Overwrite file?"), _("The file already exists. Replace it?")) != \
                    QtWidgets.QMessageBox.StandardButton.Yes:
                return False
        if token != self._token or not self._active:
            return False
        output = QtCore.QSaveFile(filename)
        data = artifact['data']
        if isinstance(data, str):
            data = data.encode('utf-8')
        if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly) or output.write(data) != len(data):
            output.cancelWriting()
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Cannot save film"), output.errorString()))
            return False
        if not output.commit():
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Cannot save film"), output.errorString()))
            return False
        self.app.file_saved.emit(suffix.upper(), filename)
        self.app.inform.emit('[success] %s: %s' % (_("Film saved"), filename))
        return True

    def read_transform_params(self):
        """Read the scale, skew and mirror settings from the UI."""
        reference_list = ['center', 'bottomleft', 'topleft', 'bottomright', 'topright']
        params = {
            'scale_factor_x': None, 'scale_factor_y': None, 'scale_type': 0,
            'scale_reference': reference_list[int(self.ui.scale_ref_combo.get_value())],
            'skew_factor_x': None, 'skew_factor_y': None, 'skew_type': 0,
            'skew_reference': reference_list[int(self.ui.skew_ref_combo.get_value())], 'mirror': None
        }
        if self.ui.film_scale_cb.get_value():
            scale_type = self.ui.film_scale_type_combo.get_value()
            params['scale_type'] = scale_type
            neutral_val = 0.0 if scale_type == 0 else 1.0
            if self.ui.film_scalex_entry.get_value() != neutral_val:
                params['scale_factor_x'] = self.ui.film_scalex_entry.get_value()
            if self.ui.film_scaley_entry.get_value() != neutral_val:
                params['scale_factor_y'] = self.ui.film_scaley_entry.get_value()
        if self.ui.film_skew_cb.get_value():
            if self.ui.film_skewx_entry.get_value() != 0.0:
                params['skew_factor_x'] = self.ui.film_skewx_entry.get_value()
            if self.ui.film_skewy_entry.get_value() != 0.0:
                params['skew_factor_y'] = self.ui.film_skewy_entry.get_value()
            params['skew_type'] = self.ui.film_skew_type_combo.get_value()
        if self.ui.film_mirror_cb.get_value():
            params['mirror'] = self.ui.film_mirror_axis.get_value()
        return params

    svg_style_re = FilmGen.svg_style_re

    def create_svg_document(self, svg_geo, box_bounds, margin, color, opacity, svg_units, background=''):
        return FilmGen.create_svg_document(svg_geo, box_bounds, margin, color, opacity, svg_units, background)

    def create_negative_svg(self, svg_geo, box_bounds, r_box, box_geo, c_hull, margin, color, opacity, svg_units):
        return FilmGen.create_negative_svg(svg_geo, box_bounds, r_box, box_geo, c_hull, margin, color, opacity,
                                           svg_units)

    @staticmethod
    def get_complementary(color_param):
        return FilmGen.get_complementary(color_param)

    def create_positive_svg(self, svg_geo, box_bounds, margin, color, opacity, svg_units):
        return FilmGen.create_positive_svg(svg_geo, box_bounds, margin, color, opacity, svg_units)

    @staticmethod
    def film_shapes(obj):
        """The flat list of the shapes of the film source (a new list; the shapes are not copied)."""
        if obj.kind.lower() == 'geometry':
            return list(obj.solid_geometry)
        return list(obj.flatten())

    @staticmethod
    def get_film_geometry(obj):
        return unary_union(Film.film_shapes(obj))

    @staticmethod
    def transform_geometry(geo_list, scale_factor_x=None, scale_factor_y=None, scale_reference='center', scale_type=0,
                           skew_factor_x=None, skew_factor_y=None, skew_reference='center', skew_type=0, mirror=None):
        return FilmGen.transform_geometry(geo_list, scale_factor_x, scale_factor_y, scale_reference, scale_type,
                                          skew_factor_x, skew_factor_y, skew_reference, skew_type, mirror)

    @staticmethod
    def create_svg_geometry(geom, scale_stroke_factor):
        return FilmGen.create_svg_geometry(geom, scale_stroke_factor)

    def reset_fields(self):
        self.ui.tf_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ui.tf_box_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
