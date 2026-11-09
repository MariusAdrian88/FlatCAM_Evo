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
from appPlugins.ToolFilm.FilmUI import FilmUI
from appPlugins.ToolFilm import FilmGen
from shapely.ops import unary_union
from reportlab.lib.units import inch, mm
from reportlab.lib.pagesizes import landscape, portrait

import logging
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class Film(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.decimals = self.app.decimals
        self.units = self.app.app_units
        self.ui = FilmUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

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
        self.app.defaults.report_usage("ToolFilm()")
        if toggle:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])
            found_idx = None
            for idx in range(self.app.ui.notebook.count()):
                if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                    found_idx = idx
                    break
            if not found_idx:
                try:
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                except RuntimeError:
                    self.app.ui.plugin_tab = QtWidgets.QWidget()
                    self.app.ui.plugin_tab.setObjectName("plugin_tab")
                    self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                    self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)
                    self.app.ui.plugin_scroll_area = VerticalScrollArea()
                    self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(2)
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])
        super().run()
        self.set_tool_ui()
        self.app.ui.notebook.setTabText(2, _("Film"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+L', **kwargs)

    def connect_signals_at_init(self):
        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.film_object_button.clicked.connect(self.on_film_creation)
        self.ui.tf_type_obj_combo.activated_custom.connect(self.on_type_obj_index_changed)
        self.ui.tf_type_box_combo.activated_custom.connect(self.on_type_box_index_changed)
        self.ui.film_type.activated_custom.connect(self.ui.on_film_type)
        self.ui.source_punch.activated_custom.connect(self.ui.on_punch_source)
        self.ui.file_type_radio.activated_custom.connect(self.ui.on_file_type)
        self.app.proj_selection_changed.connect(self.on_object_selection_changed)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def set_tool_ui(self):
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
        if obj:
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
        self.app.log.debug("ToolFilm.Film.on_film_creation() started ...")
        try:
            name = self.ui.tf_object_combo.currentText()
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s %s' %
                                 (_("No object is selected."), _("Load an object for Film and retry.")))
            return
        try:
            boxname = self.ui.tf_box_combo.currentText()
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s %s' %
                                 (_("No object is selected."), _("Load an object for Box and retry.")))
            return
        if name == '' or boxname == '':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return
        scale_stroke_width = float(self.ui.film_scale_stroke_entry.get_value())
        source = self.ui.source_punch.get_value()
        file_type = self.ui.file_type_radio.get_value()
        self.app.inform.emit(_("Generating Film ..."))
        if self.ui.film_type.get_value() == "pos":
            if self.ui.punch_cb.get_value() is False:
                self.generate_positive_normal_film(name, boxname, factor=scale_stroke_width, ftype=file_type)
            else:
                self.generate_positive_punched_film(name, boxname, source, factor=scale_stroke_width, ftype=file_type)
        else:
            self.generate_negative_film(name, boxname, factor=scale_stroke_width, ftype=file_type)

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

    def get_film_filename(self, caption, name, ftype):
        filter_ext = {
            'svg': "SVG Files (*.SVG);;All Files (*.*)",
            'png': "PNG Files (*.PNG);;All Files (*.*)",
        }.get(ftype, "PDF Files (*.PDF);;All Files (*.*)")
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=caption, directory=self.app.get_last_save_folder() + '/' + name + '_film',
                ext_filter=filter_ext)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(caption=caption, ext_filter=filter_ext)
        return str(filename)

    def generate_positive_normal_film(self, name, boxname, factor, ftype='svg'):
        self.app.log.debug("ToolFilm.Film.generate_positive_normal_film() started ...")
        params = self.read_transform_params()
        filename = self.get_film_filename(_("Export positive film"), name, ftype)
        if filename == "":
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        self.export_positive_handler(name, boxname, filename, scale_stroke_factor=factor, opacity_val=1.0,
                                     ftype=ftype, **params)

    def generate_positive_punched_film(self, name, boxname, source, factor, ftype='svg'):
        film_obj = self.app.collection.get_by_name(name)
        if source == 'exc':
            self.app.log.debug("ToolFilm.Film.generate_positive_punched_film() with Excellon source started ...")
            exc_obj = self.app.collection.get_by_name(self.ui.exc_combo.currentText())
            if exc_obj is None:
                self.app.inform.emit('[ERROR_NOTCL] %s' %
                                     _("No Excellon object selected. Load an object for punching reference and retry."))
                return 'fail'
            punched_solid_geometry = FilmGen.punch_geometry(
                film_obj.solid_geometry, film_obj.tools, holes=exc_obj.solid_geometry)
        else:
            self.app.log.debug("ToolFilm.Film.generate_positive_punched_film() with Pad center source started ...")
            try:
                punched_solid_geometry = FilmGen.punch_geometry(
                    film_obj.solid_geometry, film_obj.tools,
                    punch_size=float(self.ui.punch_size_spinner.get_value()))
            except FilmGen.PunchTooBigError:
                self.app.inform.emit('[ERROR_NOTCL] %s' %
                                     _("Failed. Punch hole size "
                                       "is bigger than some of the apertures in the Gerber object."))
                return 'fail'
            if punched_solid_geometry is None:
                self.app.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Failed. The new object geometry "
                                       "is the same as the one in the source object geometry..."))
                return 'fail'

        def init_func(new_obj, app_obj):
            new_obj.solid_geometry = punched_solid_geometry

        new_obj = self.new_object('gerber', name + "_punched", init_func, source=film_obj)
        if isinstance(new_obj, str):
            return 'fail'
        self.generate_positive_normal_film(new_obj.obj_options['name'], boxname, factor=factor, ftype=ftype)

    def generate_negative_film(self, name, boxname, factor, ftype='svg'):
        self.app.log.debug("ToolFilm.Film.generate_negative_film() started ...")
        params = self.read_transform_params()
        border = self.ui.boundary_entry.get_value() or 0
        filename = self.get_film_filename(_("Export negative film"), name, ftype)
        if filename == "":
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        self.export_negative_handler(name, boxname, filename, border, scale_stroke_factor=factor, ftype=ftype,
                                     use_convex_hull=self.ui.convex_box_cb.get_value(),
                                     rounded_box=self.ui.rounded_cb.get_value(), **params)

    def export_negative_handler(self, obj_name, box_name, filename, boundary,
                                scale_stroke_factor=0.00,
                                scale_factor_x=None, scale_factor_y=None, scale_reference='center', scale_type=0,
                                skew_factor_x=None, skew_factor_y=None, skew_reference='center', skew_type=0,
                                mirror=None, opacity_val=1.0,
                                use_thread=True, ftype='svg', use_convex_hull=False, rounded_box=False):
        self.app.defaults.report_usage("export_negative_handler()")
        self.app.log.debug("Film.export_negative_handler()")
        transform = {
            'scale_factor_x': scale_factor_x, 'scale_factor_y': scale_factor_y,
            'scale_reference': scale_reference, 'scale_type': scale_type,
            'skew_factor_x': skew_factor_x, 'skew_factor_y': skew_factor_y,
            'skew_reference': skew_reference, 'skew_type': skew_type, 'mirror': mirror
        }

        def make_document(svg_geo, box_geo, color, svg_units, source_geo):
            document = self.create_negative_svg(svg_geo=svg_geo, box_bounds=box_geo.bounds, r_box=rounded_box,
                                                box_geo=box_geo, c_hull=use_convex_hull, margin=boundary,
                                                color=color, opacity=opacity_val, svg_units=svg_units)
            background = FilmGen.negative_background_geometry(box_geo, rounded_box, use_convex_hull, boundary)
            trace_bounds = source_geo.bounds
            background_bounds = background.bounds
            if source_geo.is_empty:
                visible_bounds = background_bounds
            elif background.is_empty:
                visible_bounds = trace_bounds
            else:
                visible_bounds = (min(trace_bounds[0], background_bounds[0]),
                                  min(trace_bounds[1], background_bounds[1]),
                                  max(trace_bounds[2], background_bounds[2]),
                                  max(trace_bounds[3], background_bounds[3]))
            return document, visible_bounds

        return self.export_film(obj_name, box_name, filename, make_document, scale_stroke_factor, transform,
                                use_thread, ftype, margin=boundary)

    def export_positive_handler(self, obj_name, box_name, filename,
                                scale_stroke_factor=0.00,
                                scale_factor_x=None, scale_factor_y=None, scale_reference='center', scale_type=0,
                                skew_factor_x=None, skew_factor_y=None, skew_reference='center', skew_type=0,
                                mirror=None, opacity_val=1.0, use_thread=True, ftype='svg'):
        self.app.defaults.report_usage("export_positive_handler()")
        self.app.log.debug("Film.export_positive_handler()")
        transform = {
            'scale_factor_x': scale_factor_x, 'scale_factor_y': scale_factor_y,
            'scale_reference': scale_reference, 'scale_type': scale_type,
            'skew_factor_x': skew_factor_x, 'skew_factor_y': skew_factor_y,
            'skew_reference': skew_reference, 'skew_type': skew_type, 'mirror': mirror
        }
        margin = self.ui.boundary_entry.get_value() or 0

        def make_document(svg_geo, box_geo, color, svg_units, source_geo):
            document = self.create_positive_svg(svg_geo=svg_geo, box_bounds=box_geo.bounds, margin=margin, color=color,
                                                opacity=opacity_val, svg_units=svg_units)
            return document, source_geo.bounds

        return self.export_film(obj_name, box_name, filename, make_document, scale_stroke_factor, transform,
                                use_thread, ftype, margin=margin)

    def export_film(self, obj_name, box_name, filename, make_document, scale_stroke_factor, transform,
                    use_thread, ftype, margin=0.0):
        if filename is None:
            filename = self.app_option("global_last_save_folder")
        obj = self.app.collection.get_by_name(str(obj_name))
        if obj is None:
            return "Could not retrieve object: %s" % obj_name
        box_obj = self.app.collection.get_by_name(str(box_name))
        if box_obj is None:
            self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("No object Box. Using instead"), obj_name))
            box_obj = obj
        p_size = self.ui.pagesize_combo.get_value()
        orientation = self.ui.orientation_radio.get_value()
        color = obj.obj_options.get('tools_film_color') or self.app_option('tools_film_color') or '#000000'
        png_dpi = self.ui.png_dpi_spinner.get_value() if ftype == 'png' else 0
        if ftype == 'png' and png_dpi <= 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed. Invalid PNG DPI value."))
            return 'fail'
        open_style = self.app_option("global_open_style")

        def make_film():
            self.app.log.debug("FilmTool.export_film().make_film()")
            obj_geo = self.get_film_geometry(obj)
            if box_obj is obj:
                box_geo = transformed_obj_geo = self.transform_geometry([obj_geo], **transform)[0]
            else:
                box_geo, transformed_obj_geo = self.transform_geometry(
                    [self.get_film_geometry(box_obj), obj_geo], **transform)
            svg_geo = self.create_svg_geometry(transformed_obj_geo, scale_stroke_factor=scale_stroke_factor)
            doc_final, source_bounds = make_document(svg_geo, box_geo, color, obj.units.lower(), transformed_obj_geo)
            ret = self.write_output_file(content2save=doc_final, filename=filename, file_type=ftype, p_size=p_size,
                                         orientation=orientation, source_bounds=source_bounds,
                                         box_bounds=box_geo.bounds, dpi=png_dpi, margin=margin)
            if ret == 'fail':
                return 'fail'
            if open_style is False:
                self.app.file_opened.emit("SVG", filename)
            self.app.file_saved.emit("SVG", filename)
            self.app.inform.emit('[success] %s: %s' % (_("Film file exported to"), filename))

        if use_thread is True:
            def job_thread_film():
                with self.app.proc_container.new(_("Working...")):
                    try:
                        make_film()
                    except Exception as e:
                        self.app.log.error("Film.export_film() process -> %s" % str(e))
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            self.app.worker_task.emit({'fcn': job_thread_film, 'params': []})
        else:
            make_film()

    svg_style_re = FilmGen.svg_style_re

    def create_svg_document(self, svg_geo, box_bounds, margin, color, opacity, svg_units, background=''):
        return FilmGen.create_svg_document(svg_geo, box_bounds, margin, color, opacity, svg_units, background)

    def create_negative_svg(self, svg_geo, box_bounds, r_box, box_geo, c_hull, margin, color, opacity, svg_units):
        return FilmGen.create_negative_svg(svg_geo, box_bounds, r_box, box_geo, c_hull, margin, color, opacity, svg_units)

    @staticmethod
    def get_complementary(color_param):
        return FilmGen.get_complementary(color_param)

    def create_positive_svg(self, svg_geo, box_bounds, margin, color, opacity, svg_units):
        return FilmGen.create_positive_svg(svg_geo, box_bounds, margin, color, opacity, svg_units)

    def write_output_file(self, content2save, filename, file_type, p_size, orientation, source_bounds, box_bounds,
                          dpi=72, margin=0.0):
        try:
            page_size = None
            if file_type not in ('svg', 'png'):
                unit = inch if self.units == 'IN' else mm
                xmin, ymin, xmax, ymax = source_bounds
                if p_size != 'Bounds':
                    page_size = portrait(self.ui.pagesize[p_size]) if orientation == 'p' else \
                        landscape(self.ui.pagesize[p_size])
                    page_xmax, page_ymax = page_size[0] / unit, page_size[1] / unit
                    # the rendered document starts at box_bounds[0], box_bounds[1] and already
                    # contains the border, so the artwork lands 'margin' from its original position
                    xmin, xmax = xmin + margin, xmax + margin
                    ymin, ymax = ymin + margin, ymax + margin
                else:
                    # 'Bounds' page is the drawing itself: origin at the box minimum minus the border,
                    # size is the box plus a border on each side
                    page_xmax = (box_bounds[2] - box_bounds[0]) + 2 * margin
                    page_ymax = (box_bounds[3] - box_bounds[1]) + 2 * margin
                    xmin, xmax = xmin - box_bounds[0] + margin, xmax - box_bounds[0] + margin
                    ymin, ymax = ymin - box_bounds[1] + margin, ymax - box_bounds[1] + margin
                if xmax < 0 or ymax < 0 or xmin > page_xmax or ymin > page_ymax:
                    self.app.inform.emit('[ERROR_NOTCL] %s %s' % (
                        _("Failed."),
                        _("The artwork has to intersect the selected page size in order to be visible.\n"
                          "For 'Bounds' page size, it has to intersect the box plus border.")))
                    return 'fail'
            FilmGen.write_output_file(content2save, filename, file_type, page_size, self.units, box_bounds, dpi)
        except PermissionError:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Permission denied, saving not possible.\n"
                                                     "Most likely another app is holding the file open and not accessible."))
            return 'fail'
        except Exception as e:
            if file_type == 'svg':
                raise
            self.app.log.error("FilmTool.write_output_file() --> %s output --> %s" %
                               ('PNG' if file_type == 'png' else 'PDF', str(e)))
            return 'fail'

    @staticmethod
    def get_film_geometry(obj):
        if obj.kind.lower() == 'geometry' and obj.multigeo:
            flat_geo = []
            for tool in obj.tools:
                flat_geo += obj.flatten(obj.tools[tool]['solid_geometry'])
            return unary_union(flat_geo)
        return unary_union(obj.flatten())

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
