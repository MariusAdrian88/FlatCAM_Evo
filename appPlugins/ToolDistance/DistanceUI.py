from PyQt6 import QtWidgets, QtCore
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCEntry, FCCheckBox

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class DistanceUI:

    pluginName = _("Distance")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout
        self.units = self.app.app_units.lower()
        # the unit labels of the tab; set_units() follows a units change of the app
        self.unit_labels = []

        title_label = FCLabel("<font size=4><b>%s</b></font><br>" % self.pluginName)
        self.layout.addWidget(title_label)

        # Parameters Frame
        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.layout.addWidget(self.param_label)
        self.par_frame = FCFrame()
        self.layout.addWidget(self.par_frame)
        param_grid = GLay(v_spacing=5, h_spacing=3)
        self.par_frame.setLayout(param_grid)

        self.snap_center_cb = FCCheckBox(_("Snap to center"))
        self.snap_center_cb.setToolTip(
            _("Preview and measure at the center of the visible pad/drill under the cursor,\n"
              "including its boundary. Hovering doesn't move the mouse pointer.")
        )
        param_grid.addWidget(self.snap_center_cb, 0, 0, 1, 2)
        self.snap_vertex_cb = FCCheckBox(_("Snap to vertex"))
        self.snap_vertex_cb.setToolTip(
            _("Snap to the end points and the vertices of the traces (Gerber lines).")
        )
        param_grid.addWidget(self.snap_vertex_cb, 1, 0, 1, 2)
        self.multipoint_cb = FCCheckBox(_("Multi-Point"))
        self.multipoint_cb.setToolTip(
            _("Make a measurement over multiple distance segments.\n"
              "Right click to finish; Backspace removes the last point.\n"
              "Changing this mode restarts the measurement.")
        )
        param_grid.addWidget(self.multipoint_cb, 2, 0, 1, 2)
        self.big_cursor_cb = FCCheckBox('%s' % _("Big cursor"))
        self.big_cursor_cb.setToolTip(
            _("Use a big cursor."))
        param_grid.addWidget(self.big_cursor_cb, 4, 0, 1, 2)

        # Coordinates Frame
        self.coords_label = FCLabel('%s' % _("Coordinates"), color='green', bold=True)
        self.layout.addWidget(self.coords_label)
        coords_frame = FCFrame()
        self.layout.addWidget(coords_frame)
        coords_grid = GLay(v_spacing=5, h_spacing=3)
        coords_frame.setLayout(coords_grid)

        self.start_label = FCLabel("%s:" % _('Start point'))
        self.start_label.setToolTip(_("This is measuring Start point coordinates."))
        self.start_entry = FCEntry()
        self.start_entry.setReadOnly(True)
        self.start_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.start_entry.setToolTip(_("This is measuring Start point coordinates."))
        coords_grid.addWidget(self.start_label, 0, 0)
        coords_grid.addWidget(self.start_entry, 0, 1)
        coords_grid.addWidget(self.unit_label(), 0, 2)

        self.stop_label = FCLabel("%s:" % _('End point'))
        self.stop_label.setToolTip(_("This is the measuring Stop point coordinates."))
        self.stop_entry = FCEntry()
        self.stop_entry.setReadOnly(True)
        self.stop_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.stop_entry.setToolTip(_("This is the measuring Stop point coordinates."))
        coords_grid.addWidget(self.stop_label, 2, 0)
        coords_grid.addWidget(self.stop_entry, 2, 1)
        coords_grid.addWidget(self.unit_label(), 2, 2)

        # Results Frame
        self.res_label = FCLabel('%s' % _("Results"), color='red', bold=True)
        self.layout.addWidget(self.res_label)
        res_frame = FCFrame()
        self.layout.addWidget(res_frame)
        res_grid = GLay(v_spacing=5, h_spacing=3)
        res_frame.setLayout(res_grid)

        self.distance_x_label = FCLabel('%s:' % _("Dx"))
        self.distance_x_label.setToolTip(_("This is the distance measured over the X axis."))
        self.distance_x_entry = FCEntry()
        self.distance_x_entry.setReadOnly(True)
        self.distance_x_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.distance_x_entry.setToolTip(_("This is the distance measured over the X axis."))
        res_grid.addWidget(self.distance_x_label, 0, 0)
        res_grid.addWidget(self.distance_x_entry, 0, 1)
        res_grid.addWidget(self.unit_label(), 0, 2)

        self.distance_y_label = FCLabel('%s:' % _("Dy"))
        self.distance_y_label.setToolTip(_("This is the distance measured over the Y axis."))
        self.distance_y_entry = FCEntry()
        self.distance_y_entry.setReadOnly(True)
        self.distance_y_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.distance_y_entry.setToolTip(_("This is the distance measured over the Y axis."))
        res_grid.addWidget(self.distance_y_label, 2, 0)
        res_grid.addWidget(self.distance_y_entry, 2, 1)
        res_grid.addWidget(self.unit_label(), 2, 2)

        self.angle_label = FCLabel('%s:' % _("Angle"))
        self.angle_label.setToolTip(_("This is orientation angle of the measuring line."))
        self.angle_entry = FCEntry()
        self.angle_entry.setReadOnly(True)
        self.angle_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.angle_entry.setToolTip(_("This is orientation angle of the measuring line."))
        res_grid.addWidget(self.angle_label, 4, 0)
        res_grid.addWidget(self.angle_entry, 4, 1)
        res_grid.addWidget(FCLabel("%s" % "°"), 4, 2)

        self.angle2_label = FCLabel('%s 2:' % _("Angle"))
        self.angle2_label.setToolTip(_("This is orientation angle of the measuring line."))
        self.angle2_entry = FCEntry()
        self.angle2_entry.setReadOnly(True)
        self.angle2_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.angle2_entry.setToolTip(_("This is orientation angle of the measuring line."))
        res_grid.addWidget(self.angle2_label, 6, 0)
        res_grid.addWidget(self.angle2_entry, 6, 1)
        res_grid.addWidget(FCLabel("%s" % "°"), 6, 2)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        res_grid.addWidget(separator_line, 8, 0, 1, 3)

        self.total_distance_label = FCLabel('%s:' % _('DISTANCE'), bold=True)
        self.total_distance_label.setToolTip(_("This is the point to point Euclidian distance."))
        self.total_distance_entry = FCEntry()
        self.total_distance_entry.setReadOnly(True)
        self.total_distance_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight |
                                               QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.total_distance_entry.setToolTip(_("This is the point to point Euclidian distance."))
        res_grid.addWidget(self.total_distance_label, 10, 0)
        res_grid.addWidget(self.total_distance_entry, 10, 1)
        res_grid.addWidget(self.unit_label(), 10, 2)

        self.segment_distance_label = FCLabel('%s:' % _("Segment"))
        self.segment_distance_entry = FCEntry()
        self.segment_distance_entry.setReadOnly(True)
        self.segment_distance_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight |
                                                 QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.segment_distance_entry.setToolTip(_("Current preview segment, or last committed segment when finished."))
        res_grid.addWidget(self.segment_distance_label, 12, 0)
        res_grid.addWidget(self.segment_distance_entry, 12, 1)
        res_grid.addWidget(self.unit_label(), 12, 2)
        self.point_count_label = FCLabel('%s:' % _("Points"))
        self.point_count_entry = FCEntry()
        self.point_count_entry.setReadOnly(True)
        self.point_count_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight |
                                            QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.point_count_entry.setToolTip(_("Number of committed measurement points."))
        res_grid.addWidget(self.point_count_label, 14, 0)
        res_grid.addWidget(self.point_count_entry, 14, 1)

        self.measure_btn = FCButton(_("Measure"))
        self.layout.addWidget(self.measure_btn)
        GLay.set_common_column_size([param_grid, coords_grid, res_grid], 0)
        self.layout.addStretch(1)

    def unit_label(self):
        label = FCLabel("%s" % self.units)
        self.unit_labels.append(label)
        return label

    def set_units(self, units):
        self.units = units
        for label in self.unit_labels:
            label.setText("%s" % units)

    def reset_results(self, decimals):
        self.angle_entry.set_value('%.*f' % (decimals, 0.0))
        self.angle2_entry.set_value('%.*f' % (decimals, 0.0))
        self.distance_x_entry.set_value('%.*f' % (decimals, 0.0))
        self.distance_y_entry.set_value('%.*f' % (decimals, 0.0))
        self.total_distance_entry.set_value('%.*f' % (decimals, 0.0))
        self.segment_distance_entry.set_value('%.*f' % (decimals, 0.0))
        self.point_count_entry.set_value('0')

    def on_multipoint_measurement_changed(self, val):
        if val:
            self.distance_x_label.setDisabled(True)
            self.distance_x_entry.setDisabled(True)
            self.distance_y_label.setDisabled(True)
            self.distance_y_entry.setDisabled(True)
            self.angle_label.setDisabled(True)
            self.angle_entry.setDisabled(True)
            self.angle2_label.setDisabled(True)
            self.angle2_entry.setDisabled(True)
        else:
            self.distance_x_label.setDisabled(False)
            self.distance_x_entry.setDisabled(False)
            self.distance_y_label.setDisabled(False)
            self.distance_y_entry.setDisabled(False)
            self.angle_label.setDisabled(False)
            self.angle_entry.setDisabled(False)
            self.angle2_label.setDisabled(False)
            self.angle2_entry.setDisabled(False)

    @staticmethod
    def format_point(pos, dec_format, decimals):
        return dec_format(pos[0], decimals), dec_format(pos[1], decimals)

    def display_angle(self, val, dec_format, decimals):
        if val is not None:
            self.angle_entry.set_value(str(dec_format(val, decimals)))
            if val > 180:
                val = 360 - val
                val = -val
            self.angle2_entry.set_value(str(dec_format(val, decimals)))

    def display_start(self, val):
        if val:
            self.start_entry.set_value(str(val))

    def display_end(self, val):
        if val:
            self.stop_entry.set_value(str(val))

    def display_deltas(self, dx, dy, dec_format, decimals):
        if dx is not None:
            self.distance_x_entry.set_value(str(dec_format(abs(dx), decimals)))
        if dy is not None:
            self.distance_y_entry.set_value(str(dec_format(abs(dy), decimals)))

    def display_distance(self, val, decimals):
        if val is not None:
            self.total_distance_entry.set_value('%.*f' % (decimals, abs(val)))

    def display_feedback(self, segment, count, decimals):
        self.segment_distance_entry.set_value('%.*f' % (decimals, abs(segment)))
        self.point_count_entry.set_value(str(count))

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
