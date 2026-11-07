
from PyQt6 import QtGui, QtWidgets
from appTool import AppToolEditor, AppTool
from appEditors.grb_plugins.GrbCommon import gerber_quad
from appCommon.SafeEval import safe_eval
from appGUI.GUIElements import VerticalScrollArea, FCLabel, FCButton, FCFrame, GLay, FCDoubleSpinner, \
    FCInputDoubleSpinner, FCComboBox, NumericalEvalTupleEntry, FCCheckBox, OptionalInputSection

import numpy as np
import shapely
import math

from camlib import (affine_geometry_many, affine_rotate_matrix, affine_scale_matrix, affine_skew_matrix,
                    affine_mirror_matrix, affine_translate_matrix)

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


GEO_KEYS = ('solid', 'follow', 'clear')


def selection_bounds(shapes):
    """
    Bounds (xmin, ymin, xmax, ymax) of the 'solid' of the shapes, in ONE vectorized call.

    :return: the tuple, or None if there is no geometry
    """
    solids = [shape.geo['solid'] for shape in shapes if shape.geo.get('solid') is not None]
    if not solids:
        return None
    arr = np.empty(len(solids), dtype=object)
    arr[:] = solids
    bounds = shapely.total_bounds(arr)
    if not np.all(np.isfinite(bounds)):
        return None
    return tuple(float(v) for v in bounds)


def _apply_geometry_updates(updates, allow_empty=False):
    """Validate the whole result before assigning any solid, follow or clear geometry."""
    for key, owners, new_geos in updates:
        if not np.all(np.isfinite(shapely.get_coordinates(new_geos))):
            raise ValueError(_("Transformation produced nonfinite coordinates."))
        z_geos = [geo for geo in new_geos if shapely.has_z(geo)]
        if z_geos and not np.all(np.isfinite(shapely.get_coordinates(z_geos, include_z=True))):
            raise ValueError(_("Transformation produced nonfinite coordinates."))
        if not np.all(shapely.is_valid(new_geos)):
            raise ValueError(_("Transformation produced invalid geometry."))
        if not allow_empty and any(geo.is_empty and not shape.geo[key].is_empty
                                   for shape, geo in zip(owners, new_geos)):
            raise ValueError(_("Transformation collapsed the geometry."))
    for key, owners, new_geos in updates:
        for shape, geo in zip(owners, new_geos):
            shape.geo[key] = geo


def affine_selection(shapes, matrix):
    """
    Applies 1 affine matrix to the 'solid', 'follow' and 'clear' of all the shapes. 1 vectorized call per key. The
    shapes are changed only after all the keys are computed.
    """
    updates = []
    for key in GEO_KEYS:
        owners = [shape for shape in shapes if shape.geo.get(key) is not None]
        if owners:
            with np.errstate(over='ignore', invalid='ignore'):
                new_geos = affine_geometry_many([shape.geo[key] for shape in owners], matrix)
            updates.append((key, owners, new_geos))
    _apply_geometry_updates(updates)


def scale_selection_about_centers(shapes, factor):
    """
    Scales every shape by factor around the center of the bounds of its own 'solid' (of its own geometry if it has
    no solid). All the keys of a shape use the same center.
    """
    updates = []
    for key in GEO_KEYS:
        owners = [shape for shape in shapes if shape.geo.get(key) is not None]
        if not owners:
            continue
        geos = np.empty(len(owners), dtype=object)
        geos[:] = [shape.geo[key] for shape in owners]
        solids = np.empty(len(owners), dtype=object)
        solids[:] = [next((shape.geo[name] for name in GEO_KEYS
                           if shape.geo.get(name) is not None and not shape.geo[name].is_empty), shape.geo[key])
                     for shape in owners]
        bounds = shapely.bounds(solids)
        centers = bounds[:, :2] * 0.5 + bounds[:, 2:] * 0.5
        coords, index = shapely.get_coordinates(geos, return_index=True)
        with np.errstate(over='ignore', invalid='ignore'):
            new_coords = centers[index] + (coords - centers[index]) * factor
        if not np.all(np.isfinite(new_coords)):
            raise ValueError(_("Transformation produced nonfinite coordinates."))
        updates.append((key, owners, shapely.set_coordinates(geos.copy(), new_coords)))
    _apply_geometry_updates(updates)


def buffer_selection(shapes, value, join, quad_segs):
    """Buffers the 'solid' and the 'clear' of the shapes (the 'follow' is not changed), 1 vectorized call per key."""
    updates = []
    for key in ('solid', 'clear'):
        owners = [shape for shape in shapes if shape.geo.get(key) is not None]
        if owners:
            geos = np.empty(len(owners), dtype=object)
            geos[:] = [shape.geo[key] for shape in owners]
            updates.append((key, owners, shapely.buffer(geos, value, quad_segs=quad_segs, join_style=join)))
    _apply_geometry_updates(updates, allow_empty=value < 0)


def queue_transform(tool, kind, params):
    """Capture the selection on the GUI thread; workers only change detached wrappers."""
    if kind in ('rotate', 'flip', 'skew', 'scale', 'offset'):
        try:
            params = list(params)
            if kind != 'offset':
                params[-1] = tuple(float(value) for value in params[-1])
                if len(params[-1]) != 2:
                    raise ValueError
            if kind == 'rotate':
                params[0] = float(params[0])
            elif kind in ('skew', 'scale'):
                params[1:3] = [float(value) for value in params[1:3]]
            elif kind == 'offset':
                params[1] = float(params[1])
            values = params if kind == 'rotate' else params[1:]
            numbers = [float(v) for value in values for v in
                       (value if isinstance(value, (tuple, list)) else (value,))]
            if not all(math.isfinite(value) for value in numbers):
                raise ValueError
            if kind == 'skew' and any(abs(math.cos(math.radians(float(value)))) < 1e-12
                                      for value in params[1:3]):
                raise ValueError
            if kind == 'scale' and (params[1] == 0 or params[2] == 0):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            tool.app.inform.emit('[WARNING_NOTCL] %s' % _("Transform values and reference must be finite and nonsingular."))
            return 'fail'
    if kind == 'rotate':
        value, point = params
        matrix = affine_rotate_matrix(-value, point)
    elif kind == 'flip':
        axis, point = params
        matrix = affine_mirror_matrix(axis, point)
    elif kind == 'skew':
        axis, xvalue, yvalue, point = params
        matrix = affine_skew_matrix(xvalue, yvalue, point)
    elif kind == 'scale':
        axis, xvalue, yvalue, point = params
        matrix = affine_scale_matrix(xvalue, yvalue, point)
    elif kind == 'offset':
        axis, value = params
        matrix = affine_translate_matrix(value if axis == 'X' else 0, value if axis == 'Y' else 0)
    else:
        value, join = params[:2]
        if not math.isfinite(float(value)):
            tool.app.inform.emit('[WARNING_NOTCL] %s' % _("Buffer value must be finite."))
            return 'fail'
        factor = len(params) == 3 and params[2]
        if factor and float(value) <= 0:
            tool.app.inform.emit('[WARNING_NOTCL] %s' % _("Buffer factor must be greater than -100%."))
            return 'fail'
        quad_segs = gerber_quad(tool.app, value)

        def compute(shapes):
            if factor:
                scale_selection_about_centers(shapes, value)
            else:
                buffer_selection(shapes, value, join, quad_segs)

        success = _("Done.") if isinstance(tool, TransformEditorTool) else None
        return tool.draw_app.submit_geometry(_("Buffer"), compute, success=success)
    a, b, d, e = matrix[:4]
    if b == 0 and d == 0:
        nonsingular = a != 0 and e != 0
    else:
        xnorm, ynorm = max(abs(a), abs(b)), max(abs(d), abs(e))
        nonsingular = xnorm > 0 and ynorm > 0 and abs((a / xnorm) * (e / ynorm) -
                                                     (b / xnorm) * (d / ynorm)) > 1e-12
    if not all(math.isfinite(v) for v in matrix) or not nonsingular:
        tool.app.inform.emit('[WARNING_NOTCL] %s' % _("Transform matrix must be finite and nonsingular."))
        return 'fail'
    success = _("Done.") if isinstance(tool, TransformEditorTool) else None
    label = {'rotate': _("Rotate"), 'flip': _("Flip"), 'skew': _("Skew"),
             'scale': _("Scale"), 'offset': _("Offset")}[kind]
    return tool.draw_app.submit_geometry(label, lambda shapes: affine_selection(shapes, matrix), matrix,
                                         success=success)


class TransformEditorTool(AppToolEditor):
    """
    The Transformation plugin of the Gerber Editor. The Rotate, Flip, Skew, Scale, Offset and Buffer actions change
    the whole selection in a few vectorized calls, plot once and inform once.
    """

    pluginName = _("Transformation")

    def __init__(self, app, draw_app):
        AppToolEditor.__init__(self, app)

        self.app = app
        self.draw_app = draw_app
        self.decimals = self.app.decimals

        self.ui = TransformationEditorUI(layout=self.layout, transform_class=self)

        self.connect_signals_at_init()
        self.set_tool_ui()

    def connect_signals_at_init(self):
        # Signals
        self.ui.point_button.clicked.connect(lambda: self.on_add_coords())

        self.ui.rotate_button.clicked.connect(lambda: self.on_rotate())

        self.ui.skewx_button.clicked.connect(lambda: self.on_skewx())
        self.ui.skewy_button.clicked.connect(lambda: self.on_skewy())

        self.ui.scalex_button.clicked.connect(lambda: self.on_scalex())
        self.ui.scaley_button.clicked.connect(lambda: self.on_scaley())

        self.ui.offx_button.clicked.connect(lambda: self.on_offx())
        self.ui.offy_button.clicked.connect(lambda: self.on_offy())

        self.ui.flipx_button.clicked.connect(lambda: self.on_flipx())
        self.ui.flipy_button.clicked.connect(lambda: self.on_flipy())

        self.ui.buffer_button.clicked.connect(lambda: self.on_buffer_by_distance())
        self.ui.buffer_factor_button.clicked.connect(lambda: self.on_buffer_by_factor())

    def run(self, toggle=True):
        self.app.defaults.report_usage("Gerber Editor Transform Tool()")

        # if the splitter us hidden, display it
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        # if the Tool Tab is hidden display it, else hide it but only if the objectName is the same
        found_idx = None
        for idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                found_idx = idx
                break
        # show the Tab
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
            # focus on Tool Tab
            self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

        # self.app.ui.notebook.callback_on_close = self.on_tab_close

        if toggle:
            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName:
                    self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                else:
                    self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
            except AttributeError:
                pass

        super().run()
        self.set_tool_ui()

        self.app.ui.notebook.setTabText(2, _("Transformation"))

    def on_tab_close(self):
        self.draw_app.select_tool("select")
        self.app.ui.notebook.callback_on_close = lambda: None

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+R', **kwargs)

    def set_tool_ui(self):
        # Initialize form
        ref_val = self.app.options.get(
            "tools_transform_reference",
            self.app.defaults.get(
                "tools_transform_reference",
                self.app.defaults.factory_defaults.get("tools_transform_reference")
            )
        )
        if ref_val == _("Object"):
            ref_val = _("Selection")
        elif ref_val == _("Minimum"):
            ref_val = _("Minimum (lower left)")
        self.ui.ref_combo.set_value(ref_val)
        self.ui.point_entry.set_value(self.app.options.get(
            "tools_transform_ref_point",
            self.app.defaults.get(
                "tools_transform_ref_point",
                self.app.defaults.factory_defaults.get("tools_transform_ref_point")
            )
        ))
        self.ui.rotate_entry.set_value(self.app.options.get(
            "tools_transform_rotate",
            self.app.defaults.get(
                "tools_transform_rotate",
                self.app.defaults.factory_defaults.get("tools_transform_rotate")
            )
        ))

        self.ui.skewx_entry.set_value(self.app.options.get(
            "tools_transform_skew_x",
            self.app.defaults.get(
                "tools_transform_skew_x",
                self.app.defaults.factory_defaults.get("tools_transform_skew_x")
            )
        ))
        self.ui.skewy_entry.set_value(self.app.options.get(
            "tools_transform_skew_y",
            self.app.defaults.get(
                "tools_transform_skew_y",
                self.app.defaults.factory_defaults.get("tools_transform_skew_y")
            )
        ))
        self.ui.skew_link_cb.set_value(self.app.options.get(
            "tools_transform_skew_link",
            self.app.defaults.get(
                "tools_transform_skew_link",
                self.app.defaults.factory_defaults.get("tools_transform_skew_link")
            )
        ))

        self.ui.scalex_entry.set_value(self.app.options.get(
            "tools_transform_scale_x",
            self.app.defaults.get(
                "tools_transform_scale_x",
                self.app.defaults.factory_defaults.get("tools_transform_scale_x")
            )
        ))
        self.ui.scaley_entry.set_value(self.app.options.get(
            "tools_transform_scale_y",
            self.app.defaults.get(
                "tools_transform_scale_y",
                self.app.defaults.factory_defaults.get("tools_transform_scale_y")
            )
        ))
        self.ui.scale_link_cb.set_value(self.app.options.get(
            "tools_transform_scale_link",
            self.app.defaults.get(
                "tools_transform_scale_link",
                self.app.defaults.factory_defaults.get("tools_transform_scale_link")
            )
        ))

        self.ui.offx_entry.set_value(self.app.options.get(
            "tools_transform_offset_x",
            self.app.defaults.get(
                "tools_transform_offset_x",
                self.app.defaults.factory_defaults.get("tools_transform_offset_x")
            )
        ))
        self.ui.offy_entry.set_value(self.app.options.get(
            "tools_transform_offset_y",
            self.app.defaults.get(
                "tools_transform_offset_y",
                self.app.defaults.factory_defaults.get("tools_transform_offset_y")
            )
        ))

        self.ui.buffer_entry.set_value(self.app.options.get(
            "tools_transform_buffer_dis",
            self.app.defaults.get(
                "tools_transform_buffer_dis",
                self.app.defaults.factory_defaults.get("tools_transform_buffer_dis")
            )
        ))
        self.ui.buffer_factor_entry.set_value(self.app.options.get(
            "tools_transform_buffer_factor",
            self.app.defaults.get(
                "tools_transform_buffer_factor",
                self.app.defaults.factory_defaults.get("tools_transform_buffer_factor")
            )
        ))
        self.ui.buffer_rounded_cb.set_value(self.app.options.get(
            "tools_transform_buffer_corner",
            self.app.defaults.get(
                "tools_transform_buffer_corner",
                self.app.defaults.factory_defaults.get("tools_transform_buffer_corner")
            )
        ))

        self.ui.on_reference_changed(self.ui.ref_combo.currentIndex())

    def template(self):
        if not self.draw_app.selected:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("No shape selected.")))
            return

        self.draw_app.select_tool("select")
        self.app.ui.notebook.setTabText(2, "Plugins")
        self.app.ui.notebook.setCurrentWidget(self.app.ui.project_tab)

        self.app.ui.splitter.setSizes([0, 1])

    def on_calculate_reference(self, ref_index=None):
        if ref_index is not None:
            ref_val = ref_index
        else:
            ref_val = self.ui.ref_combo.currentIndex()

        if ref_val == 0:  # "Origin" reference
            return 0, 0
        elif ref_val == 1:  # "Selection" reference
            sel_list = self.draw_app.selected
            if sel_list and self.alt_bounds(sel_list) is not None:
                xmin, ymin, xmax, ymax = self.alt_bounds(sel_list)
                px = (xmax + xmin) * 0.5
                py = (ymax + ymin) * 0.5
                return px, py
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No shape selected."))
                return "fail"
        elif ref_val == 2:  # "Point" reference
            point_val = self.ui.point_entry.get_value()
            try:
                px, py = (float(value) for value in safe_eval('{}'.format(point_val)))
                if not all(math.isfinite(value) for value in (px, py)):
                    raise ValueError
                return px, py
            except Exception:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Incorrect format for Point value. Needs format X,Y"))
                return "fail"
        else:
            sel_list = self.draw_app.selected
            if sel_list and self.alt_bounds(sel_list) is not None:
                xmin, ymin, xmax, ymax = self.alt_bounds(sel_list)
                if ref_val == 3:
                    return xmin, ymin  # lower left corner
                elif ref_val == 4:
                    return xmax, ymin  # lower right corner
                elif ref_val == 5:
                    return xmax, ymax  # upper right corner
                else:
                    return xmin, ymax  # upper left corner
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No shape selected."))
                return "fail"

    def on_add_coords(self):
        val = self.app.clipboard.text()
        self.ui.point_entry.set_value(val)

    def on_rotate(self, val=None, ref=None):
        value = float(self.ui.rotate_entry.get_value()) if val is None else val
        if value == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Rotate transformation can not be done for a value of 0."))
            return
        point = self.on_calculate_reference() if ref is None else self.on_calculate_reference(ref_index=ref)
        if point == 'fail':
            return
        queue_transform(self, 'rotate', [value, point])

    def on_flipx(self, ref=None):
        axis = 'Y'
        point = self.on_calculate_reference() if ref is None else self.on_calculate_reference(ref_index=ref)
        if point == 'fail':
            return
        queue_transform(self, 'flip', [axis, point])

    def on_flipy(self, ref=None):
        axis = 'X'
        point = self.on_calculate_reference() if ref is None else self.on_calculate_reference(ref_index=ref)
        if point == 'fail':
            return
        queue_transform(self, 'flip', [axis, point])

    def on_skewx(self, val=None, ref=None):
        xvalue = float(self.ui.skewx_entry.get_value()) if val is None else val

        if xvalue == 0:
            return

        yvalue = xvalue if self.ui.skew_link_cb.get_value() else 0

        axis = 'X'
        point = self.on_calculate_reference() if ref is None else self.on_calculate_reference(ref_index=ref)
        if point == 'fail':
            return

        queue_transform(self, 'skew', [axis, xvalue, yvalue, point])

    def on_skewy(self, val=None, ref=None):
        xvalue = 0
        yvalue = float(self.ui.skewy_entry.get_value()) if val is None else val

        if yvalue == 0:
            return

        axis = 'Y'
        point = self.on_calculate_reference() if ref is None else self.on_calculate_reference(ref_index=ref)
        if point == 'fail':
            return

        queue_transform(self, 'skew', [axis, xvalue, yvalue, point])

    def on_scalex(self, val=None, ref=None):
        xvalue = float(self.ui.scalex_entry.get_value()) if val is None else val

        if xvalue == 0 or xvalue == 1:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Scale transformation can not be done for a factor of 0 or 1."))
            return

        yvalue = xvalue if self.ui.scale_link_cb.get_value() else 1

        axis = 'X'
        point = self.on_calculate_reference() if ref is None else self.on_calculate_reference(ref_index=ref)
        if point == 'fail':
            return

        queue_transform(self, 'scale', [axis, xvalue, yvalue, point])

    def on_scaley(self, val=None, ref=None):
        xvalue = 1
        yvalue = float(self.ui.scaley_entry.get_value()) if val is None else val

        if yvalue == 0 or yvalue == 1:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Scale transformation can not be done for a factor of 0 or 1."))
            return

        axis = 'Y'
        point = self.on_calculate_reference() if ref is None else self.on_calculate_reference(ref_index=ref)
        if point == 'fail':
            return

        queue_transform(self, 'scale', [axis, xvalue, yvalue, point])

    def on_offx(self, val=None):
        value = float(self.ui.offx_entry.get_value()) if val is None else val
        if value == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Offset transformation can not be done for a value of 0."))
            return
        axis = 'X'

        queue_transform(self, 'offset', [axis, value])

    def on_offy(self, val=None):
        value = float(self.ui.offy_entry.get_value()) if val is None else val
        if value == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Offset transformation can not be done for a value of 0."))
            return
        axis = 'Y'

        queue_transform(self, 'offset', [axis, value])

    def on_buffer_by_distance(self):
        value = self.ui.buffer_entry.get_value()
        join = 1 if self.ui.buffer_rounded_cb.get_value() else 2

        queue_transform(self, 'buffer', [value, join])

    def on_buffer_by_factor(self):
        value = 1 + (self.ui.buffer_factor_entry.get_value() / 100.0)
        join = 1 if self.ui.buffer_rounded_cb.get_value() else 2

        # tell the buffer method to use the factor
        factor = True

        queue_transform(self, 'buffer', [value, join, factor])

    def on_rotate_key(self):
        val_box = FCInputDoubleSpinner(title=_("Rotate ..."),
                                       text='%s:' % _('Enter an Angle Value (degrees, CW)'),
                                       min=-359.9999, max=360.0000, decimals=self.decimals,
                                       init_val=float(self.app.options.get(
                                           'tools_transform_rotate',
                                           self.app.defaults.get(
                                               'tools_transform_rotate',
                                               self.app.defaults.factory_defaults.get('tools_transform_rotate')
                                           )
                                       )),
                                       parent=self.app.ui)
        val_box.set_icon(QtGui.QIcon(self.app.resource_location + '/rotate.png'))

        val, ok = val_box.get_value()
        if ok:
            self.on_rotate(val=val, ref=1)
            return
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Rotate cancelled"))

    def on_offx_key(self):
        units = self.app.app_units.lower()

        val_box = FCInputDoubleSpinner(title=_("Offset on X axis ..."),
                                       text='%s: (%s)' % (_('Enter a distance Value'), str(units)),
                                       min=-10000.0000, max=10000.0000, decimals=self.decimals,
                                       init_val=float(self.app.options.get(
                                           'tools_transform_offset_x',
                                           self.app.defaults.get(
                                               'tools_transform_offset_x',
                                               self.app.defaults.factory_defaults.get('tools_transform_offset_x')
                                           )
                                       )),
                                       parent=self.app.ui)
        val_box.set_icon(QtGui.QIcon(self.app.resource_location + '/offsetx32.png'))

        val, ok = val_box.get_value()
        if ok:
            self.on_offx(val=val)
            return
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Offset X cancelled"))

    def on_offy_key(self):
        units = self.app.app_units.lower()

        val_box = FCInputDoubleSpinner(title=_("Offset on Y axis ..."),
                                       text='%s: (%s)' % (_('Enter a distance Value'), str(units)),
                                       min=-10000.0000, max=10000.0000, decimals=self.decimals,
                                       init_val=float(self.app.options.get(
                                           'tools_transform_offset_y',
                                           self.app.defaults.get(
                                               'tools_transform_offset_y',
                                               self.app.defaults.factory_defaults.get('tools_transform_offset_y')
                                           )
                                       )),
                                       parent=self.app.ui)
        val_box.set_icon(QtGui.QIcon(self.app.resource_location + '/offsety32.png'))

        val, ok = val_box.get_value()
        if ok:
            self.on_offy(val=val)
            return
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Offset on the Y axis canceled"))

    def on_skewx_key(self):
        val_box = FCInputDoubleSpinner(title=_("Skew on X axis ..."),
                                       text='%s:' % _('Enter an Angle Value (degrees)'),
                                       min=-359.9999, max=360.0000, decimals=self.decimals,
                                       init_val=float(self.app.options.get(
                                           'tools_transform_skew_x',
                                           self.app.defaults.get(
                                               'tools_transform_skew_x',
                                               self.app.defaults.factory_defaults.get('tools_transform_skew_x')
                                           )
                                       )),
                                       parent=self.app.ui)
        val_box.set_icon(QtGui.QIcon(self.app.resource_location + '/skewX.png'))

        val, ok = val_box.get_value()
        if ok:
            self.on_skewx(val=val, ref=3)
            return
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Skew on X axis canceled"))

    def on_skewy_key(self):
        val_box = FCInputDoubleSpinner(title=_("Skew on Y axis ..."),
                                       text='%s:' % _('Enter an Angle Value (degrees)'),
                                       min=-359.9999, max=360.0000, decimals=self.decimals,
                                       init_val=float(self.app.options.get(
                                           'tools_transform_skew_y',
                                           self.app.defaults.get(
                                               'tools_transform_skew_y',
                                               self.app.defaults.factory_defaults.get('tools_transform_skew_y')
                                           )
                                       )),
                                       parent=self.app.ui)
        val_box.set_icon(QtGui.QIcon(self.app.resource_location + '/skewY.png'))

        val, ok = val_box.get_value()
        if ok:
            self.on_skewy(val=val, ref=3)
            return
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Skew on Y axis canceled"))

    @staticmethod
    def alt_bounds(shapelist):
        """
        Returns coordinates of rectangular bounds of a selection of shapes
        """
        return selection_bounds(shapelist)


class TransformationEditorUI:
    pluginName = _("Transformation")
    rotateName = _("Rotate")
    skewName = _("Skew/Shear")
    scaleName = _("Scale")
    flipName = _("Mirror")
    offsetName = _("Offset")
    bufferName = _("Buffer")

    def __init__(self, layout, transform_class):
        self.transform_class = transform_class
        self.decimals = self.transform_class.app.decimals
        self.layout = layout

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        self.layout.addWidget(title_label)

        # #############################################################################################################
        # PARAMETERS
        # #############################################################################################################
        self.transform_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.layout.addWidget(self.transform_label)

        # #############################################################################################################
        # Reference  Frame
        # #############################################################################################################
        ref_frame = FCFrame()
        self.layout.addWidget(ref_frame)

        ref_grid = GLay(v_spacing=5, h_spacing=3)
        ref_frame.setLayout(ref_grid)

        # Reference
        ref_label = FCLabel('%s:' % _("Reference"))
        ref_label.setToolTip(
            _("The reference point for Rotate, Skew, Scale, Mirror.\n"
              "Can be:\n"
              "- Origin -> it is the 0, 0 point\n"
              "- Selection -> the center of the bounding box of the selected objects\n"
              "- Point -> a custom point defined by X,Y coordinates\n"
              "- Min Selection -> the point (minx, miny) of the bounding box of the selection")
        )
        self.ref_combo = FCComboBox()
        self.ref_items = [_("Origin"), _("Selection"), _("Point"), _("Minimum (lower left)"),
                          _("Lower right"), _("Upper right"), _("Upper left")]
        self.ref_combo.setToolTip(_("Minimum is the lower-left selection corner. Other corners use the selection bounds."))
        self.ref_combo.addItems(self.ref_items)

        ref_grid.addWidget(ref_label, 0, 0)
        ref_grid.addWidget(self.ref_combo, 0, 1, 1, 2)

        self.point_label = FCLabel('%s:' % _("Value"))
        self.point_label.setToolTip(
            _("A point of reference in format X,Y.")
        )
        self.point_entry = NumericalEvalTupleEntry()

        ref_grid.addWidget(self.point_label, 2, 0)
        ref_grid.addWidget(self.point_entry, 2, 1, 1, 2)

        self.point_button = FCButton(_("Add"))
        self.point_button.setToolTip(
            _("Add point coordinates from clipboard.")
        )
        ref_grid.addWidget(self.point_button, 4, 0, 1, 3)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # grid0.addWidget(separator_line, 5, 0, 1, 3)

        # #############################################################################################################
        # Rotate Frame
        # #############################################################################################################
        rotate_title_lbl = FCLabel('%s' % self.rotateName, color='tomato', bold=True)
        self.layout.addWidget(rotate_title_lbl)

        rot_frame = FCFrame()
        self.layout.addWidget(rot_frame)

        rot_grid = GLay(v_spacing=5, h_spacing=3)
        rot_frame.setLayout(rot_grid)

        # ## Rotate Title
        rotate_title_label = FCLabel("<font size=3><b>%s</b></font>" % self.rotateName)
        rot_grid.addWidget(rotate_title_label, 0, 0, 1, 3)

        self.rotate_label = FCLabel('%s:' % _("Angle (CW)"))
        self.rotate_label.setToolTip(
            _("Angle, in degrees.\n"
              "Float number between -360 and 359.\n"
              "Positive numbers for CW motion.\n"
              "Negative numbers for CCW motion.")
        )

        self.rotate_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message)
        self.rotate_entry.set_precision(self.decimals)
        self.rotate_entry.setSingleStep(45)
        self.rotate_entry.setWrapping(True)
        self.rotate_entry.set_range(-360, 360)

        # self.rotate_entry.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        self.rotate_button = FCButton(_("Rotate"))
        self.rotate_button.setToolTip(
            _("Rotate the selected object(s).\n"
              "The point of reference is the middle of\n"
              "the bounding box for all selected objects.")
        )
        self.rotate_button.setMinimumWidth(90)

        rot_grid.addWidget(self.rotate_label, 2, 0)
        rot_grid.addWidget(self.rotate_entry, 2, 1)
        rot_grid.addWidget(self.rotate_button, 2, 2)

        # #############################################################################################################
        # Skew Frame
        # #############################################################################################################
        s_t_lay = QtWidgets.QHBoxLayout()
        self.layout.addLayout(s_t_lay)

        skew_title_lbl = FCLabel('%s' % self.skewName, color='teal', bold=True)
        s_t_lay.addWidget(skew_title_lbl)

        s_t_lay.addStretch()

        # ## Link Skew factors
        self.skew_link_cb = FCCheckBox()
        self.skew_link_cb.setText(_("Link"))
        self.skew_link_cb.setToolTip(
            _("Link the Y entry to X entry and copy its content.")
        )

        s_t_lay.addWidget(self.skew_link_cb)

        skew_frame = FCFrame()
        self.layout.addWidget(skew_frame)

        skew_grid = GLay(v_spacing=5, h_spacing=3)
        skew_frame.setLayout(skew_grid)

        self.skewx_label = FCLabel('%s:' % _("X angle"))
        self.skewx_label.setToolTip(
            _("Angle for Skew action, in degrees.\n"
              "Float number between -360 and 360.")
        )
        self.skewx_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message)
        # self.skewx_entry.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.skewx_entry.set_precision(self.decimals)
        self.skewx_entry.set_range(-360, 360)

        self.skewx_button = FCButton(_("Skew X"))
        self.skewx_button.setToolTip(
            _("Skew/shear the selected object(s).\n"
              "The point of reference is the middle of\n"
              "the bounding box for all selected objects."))
        self.skewx_button.setMinimumWidth(90)

        skew_grid.addWidget(self.skewx_label, 0, 0)
        skew_grid.addWidget(self.skewx_entry, 0, 1)
        skew_grid.addWidget(self.skewx_button, 0, 2)

        self.skewy_label = FCLabel('%s:' % _("Y angle"))
        self.skewy_label.setToolTip(
            _("Angle for Skew action, in degrees.\n"
              "Float number between -360 and 360.")
        )
        self.skewy_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message)
        # self.skewy_entry.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.skewy_entry.set_precision(self.decimals)
        self.skewy_entry.set_range(-360, 360)

        self.skewy_button = FCButton(_("Skew Y"))
        self.skewy_button.setToolTip(
            _("Skew/shear the selected object(s).\n"
              "The point of reference is the middle of\n"
              "the bounding box for all selected objects."))
        self.skewy_button.setMinimumWidth(90)

        skew_grid.addWidget(self.skewy_label, 2, 0)
        skew_grid.addWidget(self.skewy_entry, 2, 1)
        skew_grid.addWidget(self.skewy_button, 2, 2)

        self.ois_sk = OptionalInputSection(self.skew_link_cb, [self.skewy_label, self.skewy_entry, self.skewy_button],
                                           logic=False)

        # #############################################################################################################
        # Scale Frame
        # #############################################################################################################
        sc_t_lay = QtWidgets.QHBoxLayout()
        self.layout.addLayout(sc_t_lay)

        scale_title_lbl = FCLabel('%s' % self.scaleName, color='magenta', bold=True)
        sc_t_lay.addWidget(scale_title_lbl)

        sc_t_lay.addStretch()

        # ## Link Scale factors
        self.scale_link_cb = FCCheckBox(_("Link"))
        self.scale_link_cb.setToolTip(
            _("Link the Y entry to X entry and copy its content.")
        )
        sc_t_lay.addWidget(self.scale_link_cb)

        scale_frame = FCFrame()
        self.layout.addWidget(scale_frame)

        scale_grid = GLay(v_spacing=5, h_spacing=3)
        scale_frame.setLayout(scale_grid)

        self.scalex_label = FCLabel('%s:' % _("X factor"))
        self.scalex_label.setToolTip(
            _("Factor for scaling on X axis.")
        )
        self.scalex_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message)
        # self.scalex_entry.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.scalex_entry.set_precision(self.decimals)
        self.scalex_entry.setMinimum(-1e6)

        self.scalex_button = FCButton(_("Scale X"))
        self.scalex_button.setToolTip(
            _("Scale the selected object(s).\n"
              "The point of reference depends on \n"
              "the Scale reference checkbox state."))
        self.scalex_button.setMinimumWidth(90)

        scale_grid.addWidget(self.scalex_label, 0, 0)
        scale_grid.addWidget(self.scalex_entry, 0, 1)
        scale_grid.addWidget(self.scalex_button, 0, 2)

        self.scaley_label = FCLabel('%s:' % _("Y factor"))
        self.scaley_label.setToolTip(
            _("Factor for scaling on Y axis.")
        )
        self.scaley_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message)
        # self.scaley_entry.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.scaley_entry.set_precision(self.decimals)
        self.scaley_entry.setMinimum(-1e6)

        self.scaley_button = FCButton(_("Scale Y"))
        self.scaley_button.setToolTip(
            _("Scale the selected object(s).\n"
              "The point of reference depends on \n"
              "the Scale reference checkbox state."))
        self.scaley_button.setMinimumWidth(90)

        scale_grid.addWidget(self.scaley_label, 2, 0)
        scale_grid.addWidget(self.scaley_entry, 2, 1)
        scale_grid.addWidget(self.scaley_button, 2, 2)

        self.ois_s = OptionalInputSection(self.scale_link_cb,
                                          [
                                              self.scaley_label,
                                              self.scaley_entry,
                                              self.scaley_button
                                          ], logic=False)

        # #############################################################################################################
        # Mirror Frame
        # #############################################################################################################
        flip_title_label = FCLabel('%s' % self.flipName, color='brown', bold=True)
        self.layout.addWidget(flip_title_label)

        mirror_frame = FCFrame()
        self.layout.addWidget(mirror_frame)

        mirror_grid = GLay(v_spacing=5, h_spacing=3)
        mirror_frame.setLayout(mirror_grid)

        self.flipx_button = FCButton(_("Mirror X coordinates"))
        self.flipx_button.setToolTip(
            _("Reverse X coordinates across the vertical line through the reference point."))

        self.flipy_button = FCButton(_("Mirror Y coordinates"))
        self.flipy_button.setToolTip(
            _("Reverse Y coordinates across the horizontal line through the reference point."))

        hlay0 = QtWidgets.QHBoxLayout()
        mirror_grid.addLayout(hlay0, 0, 0, 1, 3)

        hlay0.addWidget(self.flipx_button)
        hlay0.addWidget(self.flipy_button)

        # #############################################################################################################
        # Offset Frame
        # #############################################################################################################
        offset_title_lbl = FCLabel('%s' % self.offsetName, color='green', bold=True)
        self.layout.addWidget(offset_title_lbl)

        off_frame = FCFrame()
        self.layout.addWidget(off_frame)

        off_grid = GLay(v_spacing=5, h_spacing=3)
        off_frame.setLayout(off_grid)

        self.offx_label = FCLabel('%s:' % _("X val"))
        self.offx_label.setToolTip(
            _("Distance to offset on X axis. In current units.")
        )
        self.offx_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message)
        # self.offx_entry.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.offx_entry.set_precision(self.decimals)
        self.offx_entry.setMinimum(-1e6)

        self.offx_button = FCButton(_("Offset X"))
        self.offx_button.setToolTip(
            _("Offset the selected object(s).\n"
              "The point of reference is the middle of\n"
              "the bounding box for all selected objects.\n"))
        self.offx_button.setMinimumWidth(90)

        off_grid.addWidget(self.offx_label, 0, 0)
        off_grid.addWidget(self.offx_entry, 0, 1)
        off_grid.addWidget(self.offx_button, 0, 2)

        self.offy_label = FCLabel('%s:' % _("Y val"))
        self.offy_label.setToolTip(
            _("Distance to offset on Y axis. In current units.")
        )
        self.offy_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message)
        # self.offy_entry.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.offy_entry.set_precision(self.decimals)
        self.offy_entry.setMinimum(-1e6)

        self.offy_button = FCButton(_("Offset Y"))
        self.offy_button.setToolTip(
            _("Offset the selected object(s).\n"
              "The point of reference is the middle of\n"
              "the bounding box for all selected objects.\n"))
        self.offy_button.setMinimumWidth(90)

        off_grid.addWidget(self.offy_label, 2, 0)
        off_grid.addWidget(self.offy_entry, 2, 1)
        off_grid.addWidget(self.offy_button, 2, 2)

        # #############################################################################################################
        # Buffer Frame
        # #############################################################################################################
        b_t_lay = QtWidgets.QHBoxLayout()
        self.layout.addLayout(b_t_lay)

        buffer_title_lbl = FCLabel('%s' % self.bufferName, color='indigo', bold=True)
        b_t_lay.addWidget(buffer_title_lbl)

        b_t_lay.addStretch()

        self.buffer_rounded_cb = FCCheckBox('%s' % _("Rounded"))
        self.buffer_rounded_cb.setToolTip(
            _("If checked then the buffer will surround the buffered shape,\n"
              "every corner will be rounded.\n"
              "If not checked then the buffer will follow the exact geometry\n"
              "of the buffered shape.")
        )

        b_t_lay.addWidget(self.buffer_rounded_cb)

        buff_frame = FCFrame()
        self.layout.addWidget(buff_frame)

        buff_grid = GLay(v_spacing=5, h_spacing=3)
        buff_frame.setLayout(buff_grid)

        self.buffer_label = FCLabel('%s:' % _("Distance"))
        self.buffer_label.setToolTip(
            _("A positive value will create the effect of dilation,\n"
              "while a negative value will create the effect of erosion.\n"
              "Each geometry element of the object will be increased\n"
              "or decreased with the 'distance'.")
        )

        self.buffer_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message)
        self.buffer_entry.set_precision(self.decimals)
        self.buffer_entry.setSingleStep(0.1)
        self.buffer_entry.setWrapping(True)
        self.buffer_entry.set_range(-10000.0000, 10000.0000)

        self.buffer_button = FCButton(_("Buffer D"))
        self.buffer_button.setToolTip(
            _("Create the buffer effect on each geometry,\n"
              "element from the selected object, using the distance.")
        )
        self.buffer_button.setMinimumWidth(90)

        buff_grid.addWidget(self.buffer_label, 0, 0)
        buff_grid.addWidget(self.buffer_entry, 0, 1)
        buff_grid.addWidget(self.buffer_button, 0, 2)

        self.buffer_factor_label = FCLabel('%s:' % _("Value"))
        self.buffer_factor_label.setToolTip(
            _("A positive value will create the effect of dilation,\n"
              "while a negative value will create the effect of erosion.\n"
              "Each geometry element of the object will be increased\n"
              "or decreased to fit the 'Value'. Value is a percentage\n"
              "of the initial dimension.")
        )

        self.buffer_factor_entry = FCDoubleSpinner(callback=self.transform_class.confirmation_message, suffix='%')
        self.buffer_factor_entry.set_range(-100.0000, 1000.0000)
        self.buffer_factor_entry.set_precision(self.decimals)
        self.buffer_factor_entry.setWrapping(True)
        self.buffer_factor_entry.setSingleStep(1)

        self.buffer_factor_button = FCButton(_("Buffer F"))
        self.buffer_factor_button.setToolTip(
            _("Create the buffer effect on each geometry,\n"
              "element from the selected object, using the factor.")
        )
        self.buffer_factor_button.setMinimumWidth(90)

        buff_grid.addWidget(self.buffer_factor_label, 2, 0)
        buff_grid.addWidget(self.buffer_factor_entry, 2, 1)
        buff_grid.addWidget(self.buffer_factor_button, 2, 2)

        GLay.set_common_column_size(
            [ref_grid, rot_grid, skew_grid, scale_grid, mirror_grid, off_grid, buff_grid], 0)

        self.layout.addStretch()

        self.ref_combo.currentIndexChanged.connect(self.on_reference_changed)

    def on_reference_changed(self, index):
        if index != 2:  # Only Point needs custom coordinates.
            self.point_label.hide()
            self.point_entry.hide()
            self.point_button.hide()
        elif index == 2:  # "Point" reference
            self.point_label.show()
            self.point_entry.show()
            self.point_button.show()
