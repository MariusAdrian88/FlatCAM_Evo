# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 10/24/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appTool import AppTool
from defaults import AppDefaults
from appCommon.Common import LoudDict
from appGUI.GUIElements import VerticalScrollArea, FCFileSaveDialog
from appHandlers.appHistory import history_of
from appPlugins.ToolQRCode.QRCodeUI import QRcodeUI
from appPlugins.ToolQRCode import QRCodeGen

import datetime
import logging
import os
from collections import namedtuple
from copy import deepcopy
from shapely import box
from camlib import flatten_shapely_geometry, object_circle_steps
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

# modules / utility: the transformed geometry (lower-left anchor handled, insert at the placement point);
# box_poly: bounding box of the utility geometry; info: the qr_geometry info; text: the expanded data
QRPlan = namedtuple('QRPlan', ['modules', 'utility', 'box_poly', 'info', 'text'])


class QRCode(AppTool):

    # (plan or None, error message, request token): the geometry job hands its result back to the GUI thread
    plan_ready = QtCore.pyqtSignal(object, str, int)

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.app = app
        self.canvas = self.app.plotcanvas
        self.decimals = self.app.decimals
        self.units = ''
        self.grb_object = None
        self.grb_name = ''
        self.plan = None
        self.armed = False
        self.handlers_connected = False
        self.prev_call_source = None
        self.serial = 0
        self.proc = None
        self.shapes = self.app.sel_shapes
        self.old_back_color = ''
        self.pending_params = None
        self.request_id = 0

        self.preview_timer = QtCore.QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(300)
        self.preview_timer.timeout.connect(self.refresh_preview)
        self.plan_ready.connect(self.on_plan_ready)

        self.ui = QRcodeUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

    def run(self, toggle=True):
        self.app.defaults.report_usage("QRCode()")
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
        self.app.ui.notebook.setTabText(2, _("QRCode"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+Q', **kwargs)

    def connect_signals_at_init(self):
        ui = self.ui
        ui.level.toggled.connect(self.on_level_changed)
        ui.qrcode_button.clicked.connect(self.execute)
        ui.export_png_button.clicked.connect(self.export_png_file)
        ui.export_svg_button.clicked.connect(self.export_svg_file)
        ui.fill_color_entry.editingFinished.connect(self.on_qrcode_fill_color_entry)
        ui.fill_color_button.clicked.connect(self.on_qrcode_fill_color_button)
        ui.back_color_entry.editingFinished.connect(self.on_qrcode_back_color_entry)
        ui.back_color_button.clicked.connect(self.on_qrcode_back_color_button)
        ui.transparent_cb.stateChanged.connect(self.on_transparent_back_color)
        ui.reset_button.clicked.connect(self.set_tool_ui)

        ui.version_auto_cb.stateChanged.connect(self.update_enabled)
        ui.placement_radio.activated_custom.connect(self.update_enabled)
        ui.mask_cb.stateChanged.connect(self.update_enabled)
        ui.serial_start_entry.valueChanged.connect(self.on_serial_start_changed)

        # everything that changes the rendered preview, the info and the warnings
        ui.text_data.textChanged.connect(self.schedule_preview)
        ui.version_entry.valueChanged.connect(self.schedule_preview)
        ui.version_auto_cb.stateChanged.connect(self.schedule_preview)
        ui.error_radio.activated_custom.connect(self.schedule_preview)
        ui.mask_pattern_combo.currentIndexChanged.connect(self.schedule_preview)
        ui.bsize_entry.valueChanged.connect(self.schedule_preview)
        ui.border_size_entry.valueChanged.connect(self.schedule_preview)
        ui.serial_pad_entry.valueChanged.connect(self.schedule_preview)
        ui.serial_start_entry.valueChanged.connect(self.schedule_preview)

    def app_option(self, key):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        return self.app.options.get(key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))

    def set_tool_ui(self):
        # a Reset (or a new run) ends a running placement
        self.on_exit()
        self.plan = None
        self.units = self.app.app_units
        self.clear_ui(self.layout)
        self.ui = QRcodeUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()
        ui = self.ui
        option = self.app_option

        ui.version_entry.set_value(int(option("tools_qrcode_version")))
        ui.version_auto_cb.set_value(bool(option("tools_qrcode_version_auto")))
        ui.error_radio.set_value(option("tools_qrcode_error"))
        pattern = str(option("tools_qrcode_mask_pattern")).strip().lower()
        ui.mask_pattern_combo.setCurrentIndex(int(pattern) + 1 if pattern.isdigit() and int(pattern) < 8 else 0)
        ui.bsize_entry.set_value(int(option("tools_qrcode_box_size")))
        ui.border_size_entry.set_value(int(option("tools_qrcode_border_size")))
        ui.pol_radio.set_value(option("tools_qrcode_polarity"))
        ui.bb_radio.set_value(option("tools_qrcode_rounded"))
        ui.rotation_radio.set_value(int(option("tools_qrcode_rotation")))
        ui.mirror_cb.set_value(bool(option("tools_qrcode_mirror")))
        ui.margin_entry.set_value(float(option("tools_qrcode_margin")))
        ui.text_data.set_value(option("tools_qrcode_qrdata"))
        ui.placement_radio.set_value(option("tools_qrcode_placement"))
        ui.anchor_radio.set_value(option("tools_qrcode_anchor"))
        ui.multi_cb.set_value(bool(option("tools_qrcode_multi")))
        ui.x_entry.set_value(0.0)
        ui.y_entry.set_value(0.0)
        ui.serial_start_entry.set_value(int(option("tools_qrcode_serial_start")))
        ui.serial_step_entry.set_value(int(option("tools_qrcode_serial_step")))
        ui.serial_pad_entry.set_value(int(option("tools_qrcode_serial_pad")))
        ui.output_radio.set_value(option("tools_qrcode_output"))
        ui.mask_cb.set_value(False)
        self.serial = ui.serial_start_entry.get_value()

        fill_color = option('tools_qrcode_fill_color')
        back_color = option('tools_qrcode_back_color')
        ui.fill_color_entry.set_value(fill_color)
        ui.fill_color_button.setStyleSheet("background-color:%s;" % str(fill_color)[:7])
        ui.back_color_entry.set_value(back_color)
        ui.back_color_button.setStyleSheet("background-color:%s;" % str(back_color)[:7])
        self.old_back_color = ui.back_color_entry.get_value()

        obj = self.app.collection.get_active()
        if obj and obj.kind == 'gerber':
            ui.grb_object_combo.set_value(obj.obj_options['name'])
        self.update_enabled()
        self.change_level(option("global_app_level"))
        self.schedule_preview()

    def change_level(self, level):
        if level == 'a':
            self.ui.level.setChecked(True)
        else:
            self.ui.level.setChecked(False)
        self.on_level_changed(self.ui.level.isChecked())

    def on_level_changed(self, checked):
        self.ui.on_level_changed(checked)

    def update_enabled(self, *args):
        ui = self.ui
        ui.version_entry.setEnabled(not ui.version_auto_cb.get_value())
        coords = ui.placement_radio.get_value() == 'coords'
        for widget in (ui.x_entry, ui.y_entry, ui.x_label, ui.y_label, ui.x_units_label, ui.y_units_label):
            widget.setEnabled(coords)
        ui.multi_cb.setEnabled(not coords)
        ui.mask_object_combo.setEnabled(ui.mask_cb.get_value())
        ui.mask_object_label.setEnabled(ui.mask_cb.get_value())

    def on_serial_start_changed(self, value):
        self.serial = int(value)

    # ------------------------------------------------------------------------------------------------------------
    # parameters and geometry
    # ------------------------------------------------------------------------------------------------------------
    def get_params(self):
        """The values of the widgets (GUI thread only). The geometry code works on this dict."""
        ui = self.ui
        self.units = self.app.app_units
        pattern_idx = ui.mask_pattern_combo.currentIndex()
        margin = float(ui.margin_entry.get_value())
        return {
            'text': ui.text_data.get_value(),
            'version': None if ui.version_auto_cb.get_value() else int(ui.version_entry.get_value()),
            'error': ui.error_radio.get_value(),
            'mask_pattern': None if pattern_idx <= 0 else pattern_idx - 1,
            'box_size': int(ui.bsize_entry.get_value()),
            'border': int(ui.border_size_entry.get_value()),
            'polarity': ui.pol_radio.get_value(),
            'rounded': ui.bb_radio.get_value(),
            'rotation': int(ui.rotation_radio.get_value()),
            'mirror': bool(ui.mirror_cb.get_value()),
            'margin': margin if margin > 0 else None,
            'placement': ui.placement_radio.get_value(),
            'x': float(ui.x_entry.get_value()),
            'y': float(ui.y_entry.get_value()),
            'anchor': ui.anchor_radio.get_value(),
            'multi': bool(ui.multi_cb.get_value()),
            'serial_step': int(ui.serial_step_entry.get_value()),
            'serial_pad': int(ui.serial_pad_entry.get_value()),
            'output': ui.output_radio.get_value(),
            'mask_opening': bool(ui.mask_cb.get_value()),
            'mask_name': ui.mask_object_combo.get_value(),
            'units': self.units,
            # the round features of the code are not smaller than half of a module
            'circle_steps': object_circle_steps(self.app.options, 'geometry',
                                                float(self.app_option('tools_qrcode_min_module')) / 2, self.units),
            'min_module': float(self.app_option('tools_qrcode_min_module')),
        }

    def token_context(self, serial, obj_name):
        project = getattr(self.app, 'project_filename', None)
        now = datetime.datetime.now()
        return {'date': now, 'time': now, 'serial': serial, 'object': obj_name or '',
                'project': os.path.splitext(os.path.basename(project))[0] if project else ''}

    @staticmethod
    def build_plan(params, text, units=None):
        """The transformed QR geometry for the expanded text. Pure: no widgets, safe in a worker thread."""
        units = units or params['units']
        modules, utility, info = QRCodeGen.qr_geometry(
            text, params['version'], params['error'], params['box_size'], params['border'], units,
            params['mask_pattern'], params['circle_steps'])
        margin = QRCodeGen.effective_margin(params['margin'], params['border'], params['box_size'], units)
        modules, utility = QRCodeGen.transform_geometry(
            [modules, utility], params['rotation'], params['mirror'], params['anchor'], margin)
        box_poly = box(*utility.bounds)
        return QRPlan(modules, utility, box_poly, info, text)

    def plan_for(self, params, serial, obj_name):
        text = QRCodeGen.expand_tokens(params['text'], self.token_context(serial, obj_name), params['serial_pad'])
        return self.build_plan(params, text)

    def target_object(self):
        """The Gerber selected in the Source Object combo, or None."""
        name = self.ui.grb_object_combo.get_value()
        obj = self.app.collection.get_by_name(name) if name else None
        return obj if obj is not None and obj.kind == 'gerber' else None

    # ------------------------------------------------------------------------------------------------------------
    # arming
    # ------------------------------------------------------------------------------------------------------------
    def execute(self):
        # no stale geometry, no double arming
        self.on_exit()
        self.plan = None
        params = self.get_params()
        if params['text'].strip() == '':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Cancelled. There is no QRCode Data in the text box."))
            return
        obj = self.target_object()
        if obj is None and params['output'] != 'new':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Gerber object loaded ..."))
            return
        if self.app.call_source not in ('app', 'qrcode_tool'):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Close the running tool or editor first."))
            return
        self.grb_object = obj
        self.grb_name = name = obj.obj_options['name'] if obj is not None else ''
        if obj is not None and obj.obj_options.get('pcbdoc_import', False):
            # the objects of a PCB document stay in their own units (see appIO)
            params['units'] = str(obj.units).upper()
        self.pending_params = params
        serial = self.serial
        token = self.request_id

        def job_thread_qr():
            with self.app.proc_container.new('%s...' % _("Working")) as self.proc:
                try:
                    plan = self.plan_for(params, serial, name)
                except (ValueError, QRCodeGen.QRDataOverflow) as err:
                    self.plan_ready.emit(None, str(err), token)
                    return
                except Exception as err:
                    log.error("QRCode.execute() --> %s" % str(err))
                    self.plan_ready.emit(None, str(err), token)
                    return
            self.plan_ready.emit(plan, '', token)
        self.app.worker_task.emit({'fcn': job_thread_qr, 'params': []})

    def on_plan_ready(self, plan, message, token):
        """GUI thread: the geometry is ready (or failed). Arm the canvas, or insert at the given coordinates."""
        if token != self.request_id:
            return      # a Reset, a close or a newer Insert came in the meantime
        if plan is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % (message or _("The QRCode could not be created.")))
            return
        params = self.pending_params
        if params is None:
            return
        self.plan = plan
        if params['placement'] == 'coords':
            self.make((params['x'], params['y']))
            return
        self.arm()

    def arm(self):
        if self.armed:
            return
        if self.app.call_source not in ('app', 'qrcode_tool'):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Close the running tool or editor first."))
            return
        self.prev_call_source = self.app.call_source
        self.app.call_source = 'qrcode_tool'
        canvas = self.app.plotcanvas
        canvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        canvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        canvas.graph_event_connect('mouse_move', self.on_mouse_move)
        canvas.graph_event_connect('mouse_release', self.on_mouse_release)
        self.handlers_connected = True
        self.armed = True
        self.app.inform.emit(_("Click on the DESTINATION point ..."))

    def hideEvent(self, event):
        # another plugin took the place of this one while the placement was running
        super().hideEvent(event)
        try:
            if self.armed and self.app.ui.plugin_scroll_area.widget() is not self:
                self.on_exit(cancelled=True)
        except (AttributeError, RuntimeError):
            pass

    def on_plugin_cleanup(self):
        self.preview_timer.stop()
        if self.armed:
            self.on_exit(cancelled=True)
        else:
            self.request_id += 1
            self.pending_params = None

    def on_exit(self, cancelled=False):
        """End the placement: events, preview shapes and the call_source go back to what they were."""
        was_armed = self.armed
        # a result of the geometry job that is still on its way is stale from here on
        self.request_id += 1
        self.pending_params = None
        if self.handlers_connected:
            canvas = self.app.plotcanvas
            canvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
            canvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
            canvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
            canvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
            self.handlers_connected = False
        self.armed = False
        self.delete_utility_geo()
        if was_armed:
            if self.app.call_source == 'qrcode_tool':
                self.app.call_source = self.prev_call_source or 'app'
            self.prev_call_source = None
            if cancelled:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))

    # ------------------------------------------------------------------------------------------------------------
    # placement
    # ------------------------------------------------------------------------------------------------------------
    def draw_utility_geo(self, pos):
        if self.plan is None:
            return
        offset_geo = QRCodeGen.preview_geometry(self.plan.modules, self.plan.utility, self.plan.box_poly, pos,
                                                self.app_option("tools_qrcode_sel_limit"))
        for shp in offset_geo:
            self.shapes.add(shp, color='#0000FFAF', update=False, layer=0, tolerance=None)
        self.shapes.redraw()

    def delete_utility_geo(self):
        self.shapes.clear(update=True)

    def event_position(self, event_pos):
        pos_canvas = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            return self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
        return pos_canvas

    def on_mouse_move(self, event):
        if not self.armed:
            return
        try:
            x = float(event.pos[0])
            y = float(event.pos[1])
        except TypeError:
            return
        pos = self.event_position((x, y))
        # one redraw per move: clear without an update, then draw
        self.shapes.clear(update=False)
        self.draw_utility_geo((pos[0], pos[1]))

    def on_mouse_release(self, event):
        if not self.armed or self.app.event_is_dragging:
            return
        if event.button == 1:
            pos = self.event_position(event.pos)
            self.make(pos=(pos[0], pos[1]))
        elif event.button == 2:
            self.on_exit(cancelled=True)

    # ------------------------------------------------------------------------------------------------------------
    # insert
    # ------------------------------------------------------------------------------------------------------------
    def valid_target(self):
        """The target Gerber if it can still be changed, else None (the reason is reported)."""
        obj = self.grb_object
        if obj is None or self.app.collection.get_by_name(self.grb_name) is not obj:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The target Gerber object no longer exists."))
            return None
        if obj.kind != 'gerber':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The target object is not a Gerber object."))
            return None
        if self.app.call_source not in ('app', 'qrcode_tool'):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The target Gerber is being edited. Close the editor first."))
            return None
        return obj

    def make(self, pos):
        params = self.pending_params
        plan = self.plan
        if params is None or plan is None:
            self.on_exit()
            return
        obj = None
        if self.grb_object is not None or params['output'] != 'new':
            obj = self.valid_target()
            if obj is None:
                self.on_exit()
                return
        name = self.grb_name

        mask_obj = None
        if params['mask_opening']:
            mask_obj = self.app.collection.get_by_name(params['mask_name'])
            if mask_obj is None or mask_obj.kind != 'gerber':
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("The soldermask Gerber was not found. Skipped."))
                mask_obj = None
            elif mask_obj is obj:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("The soldermask Gerber is the target object. Skipped."))
                mask_obj = None

        try:
            if params['output'] == 'new':
                self.write_new_object(obj, plan, pos, params)
                changed = [mask_obj] if mask_obj is not None else []
            else:
                changed = [obj] + ([mask_obj] if mask_obj is not None else [])
            if changed:
                # everything is computed first: no object changes when one of the computations fails
                results = []
                if params['output'] != 'new':
                    results.append((obj, self.qrcode_result(obj, plan, pos, params)))
                if mask_obj is not None:
                    results.append((mask_obj, self.opening_result(mask_obj, plan, pos, params)))
                with history_of(self.app).snapshot(_("QRCode"), changed):
                    for changed_obj, result in results:
                        self.apply_result(changed_obj, *result)
                for changed_obj in changed:
                    self.refresh_object(changed_obj)
        except Exception as err:
            log.error("QRCode.make() --> %s" % str(err))
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            self.on_exit()
            return

        self.serial += params['serial_step']
        self.app.inform.emit('[success] %s' % _("QRCode Tool done."))
        if self.armed and params['multi']:
            # stay armed with the next serial number
            try:
                self.plan = self.plan_for(params, self.serial, name)
            except (ValueError, QRCodeGen.QRDataOverflow) as err:
                self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
                self.on_exit()
        else:
            self.on_exit()

    def qrcode_result(self, obj, plan, pos, params):
        """(solid geometry, tools, follow geometry) of the Gerber with the QRCode inserted. The object is not changed."""
        solid_geometry, tools = QRCodeGen.insert_geometry(
            obj.solid_geometry, obj.tools, plan.modules, plan.utility, pos, params['border'], params['box_size'],
            params['rounded'], params['polarity'], units=params['units'], margin=params['margin'])
        follow = obj.follow_geometry
        if params['polarity'] == 'pos' and follow:
            follow = flatten_shapely_geometry(follow) + \
                [geo.centroid for geo in QRCodeGen.plan_insertion(
                    plan.modules, plan.utility, pos, params['border'], params['box_size'], params['rounded'],
                    params['polarity'], params['units'], params['margin']).modules]
        return solid_geometry, tools, follow

    def opening_result(self, obj, plan, pos, params):
        """(solid geometry, tools, follow geometry) of a (soldermask) Gerber with the clearance opening added."""
        opening = QRCodeGen.mask_opening(plan.utility, pos, params['border'], params['box_size'], params['rounded'],
                                         params['units'], params['margin'])
        solid_geometry, tools = QRCodeGen.add_region(obj.solid_geometry, obj.tools, opening)
        return solid_geometry, tools, obj.follow_geometry

    def apply_result(self, obj, solid_geometry, tools, follow_geometry):
        """Apply a computed result to the Gerber (within the history snapshot) and regenerate its source."""
        obj.solid_geometry = solid_geometry
        obj.tools = tools
        obj.follow_geometry = follow_geometry
        self.update_bounds(obj)
        obj.regenerate_source()

    def write_new_object(self, src, plan, pos, params):
        """A new Gerber object that holds only the QRCode."""
        solid_geometry, tools = QRCodeGen.new_gerber_geometry(
            plan.modules, plan.utility, pos, params['border'], params['box_size'], params['rounded'],
            params['polarity'], units=params['units'], margin=params['margin'])
        follow = []
        if params['polarity'] == 'pos':
            follow = [geo['follow'] for apr in tools.values() for geo in apr['geometry'] if 'follow' in geo]
        outname = '%s_%s' % (str(src.obj_options['name']), 'qrcode') if src is not None else 'qrcode'

        def initialize(grb_obj, app_obj):
            if src is not None:
                grb_obj.obj_options = LoudDict()
                for opt in src.obj_options:
                    if opt != 'name':
                        grb_obj.obj_options[opt] = deepcopy(src.obj_options[opt])
            grb_obj.obj_options['name'] = outname
            grb_obj.units = params['units']
            grb_obj.multitool = False
            grb_obj.multigeo = False
            grb_obj.follow = False
            grb_obj.tools = tools
            grb_obj.solid_geometry = solid_geometry
            grb_obj.follow_geometry = follow
            grb_obj.export_as_regions = True
            self.update_bounds(grb_obj)
            grb_obj.regenerate_source(name=outname)

        ret_val = self.new_object('gerber', outname, initialize, source=src, plot=True, autoselected=False)
        if ret_val == 'fail':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))

    def update_bounds(self, obj):
        try:
            a, b, c, d = obj.bounds()
            obj.obj_options['xmin'] = a
            obj.obj_options['ymin'] = b
            obj.obj_options['xmax'] = c
            obj.obj_options['ymax'] = d
        except Exception as e:
            self.app.log.error("QRCode.update_bounds() --> %s" % str(e))

    def refresh_object(self, obj):
        """The aperture table, the apertures marks and the plot belong to the old apertures."""
        obj.ui_build = False
        obj.marked_rows = []
        try:
            obj.clear_plot_apertures()
            obj.build_ui()
        except Exception as e:
            self.app.log.debug("QRCode.refresh_object() --> %s" % str(e))
        self.replot(obj=obj)
        try:
            self.app.app_obj.object_changed.emit(obj)
        except Exception as e:
            self.app.log.debug("QRCode.refresh_object() --> %s" % str(e))

    def replot(self, obj):
        def worker_task():
            with self.app.proc_container.new('%s ...' % _("Plotting")):
                obj.plot()
        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    # ------------------------------------------------------------------------------------------------------------
    # info, warnings and the rendered preview
    # ------------------------------------------------------------------------------------------------------------
    def schedule_preview(self, *args):
        self.preview_timer.start()

    def refresh_preview(self):
        ui = self.ui
        params = self.get_params()
        units = params['units']
        module = QRCodeGen.module_size(params['box_size'], units)
        ui.module_size_label.setText('= %.*f %s' % (self.decimals, module, units.lower()))
        messages = {
            'module_below_min': _("The box is smaller than the minimum module size."),
            'quiet_zone': _("A border below 4 boxes breaks the quiet zone of the QR standard."),
        }
        warnings = [messages[key] for key in
                    QRCodeGen.size_warnings(module, units, params['min_module'], params['border'])]
        ui.warning_label.setText('\n'.join(warnings))
        ui.warning_label.setVisible(bool(warnings))

        text = QRCodeGen.expand_tokens(params['text'], self.token_context(self.serial, self.current_object_name()),
                                       params['serial_pad'])
        if not text:
            ui.info_label.setText('')
            ui.preview_label.clear()
            return
        try:
            # one encoding for the info and the picture; the picture border is capped (the info keeps the real one)
            qr, img = QRCodeGen.build_qr(text, params['version'], params['error'], 4, min(params['border'], 8),
                                         'png', '#000000', '#FFFFFF', params['mask_pattern'])
            modules = qr.modules_count
            side = (modules + 2 * params['border']) * module
            ui.info_label.setText(_("Version %d, %dx%d modules, %.*f x %.*f %s") % (
                qr.version, modules, modules, self.decimals, side, self.decimals, side, units.lower()))
            img = getattr(img, 'get_image', lambda: img)().convert('RGBA')
            qimage = QtGui.QImage(img.tobytes(), img.width, img.height, 4 * img.width,
                                  QtGui.QImage.Format.Format_RGBA8888).copy()
            pixmap = QtGui.QPixmap.fromImage(qimage)
            ui.preview_label.setPixmap(pixmap.scaled(
                160, 160, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.FastTransformation))
        except (ValueError, QRCodeGen.QRDataOverflow, MemoryError) as err:
            ui.info_label.setText(str(err))
            ui.preview_label.clear()

    def current_object_name(self):
        obj = self.target_object()
        return obj.obj_options['name'] if obj is not None else ''

    # ------------------------------------------------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------------------------------------------------
    def export_png_file(self):
        return self.export_file('png')

    def export_svg_file(self):
        return self.export_file('svg')

    def export_file(self, file_type):
        params = self.get_params()
        if params['text'].strip() == '':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Cancelled. There is no QRCode Data in the text box."))
            return 'fail'
        fill_color = self.ui.fill_color_entry.get_value()
        back_color = self.ui.back_color_entry.get_value()
        if not QRCodeGen.valid_color(fill_color):
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The fill color is not valid"), fill_color))
            return 'fail'
        if not QRCodeGen.valid_color(back_color, allow_transparent=True):
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The back color is not valid"), back_color))
            return 'fail'
        text = QRCodeGen.expand_tokens(params['text'], self.token_context(self.serial, self.current_object_name()),
                                       params['serial_pad'])

        if file_type == 'png':
            caption = _("Export PNG")
            ext_filter = "%s (*.png);;%s (*.*)" % (_("PNG File"), _("All Files"))
            folder = 'qr_code_png'
        else:
            caption = _("Export SVG")
            ext_filter = "%s (*.svg);;%s (*.*)" % (_("SVG File"), _("All Files"))
            folder = 'qr_code_svg'

        def job_thread_qr_export(fname):
            # the export does not touch call_source: Undo / Redo and the other tools stay available
            try:
                img = QRCodeGen.make_qr_image(text, params['version'], params['error'], params['box_size'],
                                              params['border'], file_type, fill_color, back_color,
                                              params['mask_pattern'])
                img.save(fname)
            except Exception as err:
                log.error("QRCode.export_file() --> %s" % str(err))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The QRCode could not be exported"), str(err)))
                return
            self.app.inform.emit('[success] %s: %s' % (_("QRCode saved"), fname))

        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=caption, directory=self.app.get_last_save_folder() + '/' + folder, ext_filter=ext_filter)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(caption=caption, ext_filter=ext_filter)
        filename = str(filename)
        if filename == "":
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        self.app.worker_task.emit({'fcn': job_thread_qr_export, 'params': [filename]})

    def on_qrcode_fill_color_entry(self):
        self.ui.on_qrcode_fill_color_entry()

    def on_qrcode_fill_color_button(self):
        self.ui.on_qrcode_fill_color_button()

    def on_qrcode_back_color_entry(self):
        self.ui.on_qrcode_back_color_entry()

    def on_qrcode_back_color_button(self):
        self.ui.on_qrcode_back_color_button()

    def on_transparent_back_color(self, state):
        if state:
            self.old_back_color = self.ui.back_color_entry.get_value()
        self.ui.on_transparent_back_color(state, self.old_back_color)
