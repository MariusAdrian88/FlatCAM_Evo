from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCEntry, FCComboBox2

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ObjectDistanceUI:

    pluginName = _("Object Distance")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout
        self.units = self.app.app_units.lower()

        # ## Title
        title_label = FCLabel("<font size=4><b>%s</b></font><br>" % self.pluginName)
        self.layout.addWidget(title_label)

        # Parameters Frame
        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.param_label.setToolTip(
            _("Parameters used for this tool.")
        )
        self.layout.addWidget(self.param_label)

        par_frame = FCFrame()
        self.layout.addWidget(par_frame)
        param_grid = GLay(v_spacing=5, h_spacing=3)
        par_frame.setLayout(param_grid)

        self.distance_type_label = FCLabel("%s:" % _("Type"))
        self.distance_type_label.setToolTip(
            _("The type of distance to be calculated.\n"
              "- Nearest points - minimal distance between objects\n"
              "- Center points - distance between the center of the bounding boxes")
        )
        self.distance_type_combo = FCComboBox2()
        self.distance_type_combo.addItems([_("Nearest points"), _("Center points")])
        param_grid.addWidget(self.distance_type_label, 0, 0)
        param_grid.addWidget(self.distance_type_combo, 0, 1)

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
        coords_grid.addWidget(FCLabel("%s" % self.units), 0, 2)

        self.stop_label = FCLabel("%s:" % _('End point'))
        self.stop_label.setToolTip(_("This is the measuring Stop point coordinates."))
        self.stop_entry = FCEntry()
        self.stop_entry.setReadOnly(True)
        self.stop_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.stop_entry.setToolTip(_("This is the measuring Stop point coordinates."))
        coords_grid.addWidget(self.stop_label, 2, 0)
        coords_grid.addWidget(self.stop_entry, 2, 1)
        coords_grid.addWidget(FCLabel("%s" % self.units), 2, 2)

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
        res_grid.addWidget(FCLabel("%s" % self.units), 0, 2)

        self.distance_y_label = FCLabel('%s:' % _("Dy"))
        self.distance_y_label.setToolTip(_("This is the distance measured over the Y axis."))
        self.distance_y_entry = FCEntry()
        self.distance_y_entry.setReadOnly(True)
        self.distance_y_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.distance_y_entry.setToolTip(_("This is the distance measured over the Y axis."))
        res_grid.addWidget(self.distance_y_label, 2, 0)
        res_grid.addWidget(self.distance_y_entry, 2, 1)
        res_grid.addWidget(FCLabel("%s" % self.units), 2, 2)

        self.angle_label = FCLabel('%s:' % _("Angle"))
        self.angle_label.setToolTip(_("This is orientation angle of the measuring line."))
        self.angle_entry = FCEntry()
        self.angle_entry.setReadOnly(True)
        self.angle_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.angle_entry.setToolTip(_("This is orientation angle of the measuring line."))
        res_grid.addWidget(self.angle_label, 4, 0)
        res_grid.addWidget(self.angle_entry, 4, 1)
        res_grid.addWidget(FCLabel("%s" % "°"), 4, 2)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        res_grid.addWidget(separator_line, 6, 0, 1, 3)

        self.total_distance_label = FCLabel('%s:' % _('DISTANCE'), bold=True)
        self.total_distance_label.setToolTip(_("This is the point to point Euclidian distance."))
        self.total_distance_entry = FCEntry()
        self.total_distance_entry.setReadOnly(True)
        self.total_distance_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight |
                                               QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.total_distance_entry.setToolTip(_("This is the point to point Euclidian distance."))
        res_grid.addWidget(self.total_distance_label, 8, 0)
        res_grid.addWidget(self.total_distance_entry, 8, 1)
        res_grid.addWidget(FCLabel("%s" % self.units), 8, 2)

        self.half_point_label = FCLabel('%s:' % _('Half Point'), bold=True)
        self.half_point_label.setToolTip(_("This is the middle point of the point to point Euclidean distance."))
        self.half_point_entry = FCEntry()
        self.half_point_entry.setReadOnly(True)
        self.half_point_entry.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.half_point_entry.setToolTip(_("This is the middle point of the point to point Euclidean distance."))
        res_grid.addWidget(self.half_point_label, 10, 0)
        res_grid.addWidget(self.half_point_entry, 10, 1)
        res_grid.addWidget(FCLabel("%s" % self.units), 10, 2)

        self.measure_btn = FCButton(_("Measure"))
        self.layout.addWidget(self.measure_btn)
        self.jump_hp_btn = FCButton(_("Jump to Half Point"))
        self.jump_hp_btn.setDisabled(True)
        self.layout.addWidget(self.jump_hp_btn)
        GLay.set_common_column_size([param_grid, coords_grid, res_grid], 0)
        self.layout.addStretch(1)

        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.layout.addWidget(self.reset_button)

    def reset_measurement(self):
        self.start_entry.set_value('(0, 0)')
        self.stop_entry.set_value('(0, 0)')
        self.distance_x_entry.set_value('0.0')
        self.distance_y_entry.set_value('0.0')
        self.angle_entry.set_value('0.0')
        self.total_distance_entry.set_value('0.0')
        self.half_point_entry.set_value('(0, 0)')
        self.jump_hp_btn.setDisabled(True)

    @staticmethod
    def format_point(pt, dec_format, decimals):
        return dec_format(pt.x, decimals), dec_format(pt.y, decimals)

    def display_angle(self, val, dec_format, decimals):
        if val is not None:
            self.angle_entry.set_value(str(dec_format(val, decimals)))

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
        # a 0 (touching objects) is written too: the previous pair's distance stayed on screen
        if val is not None:
            self.total_distance_entry.set_value('%.*f' % (decimals, abs(val)))

    def display_half_distance(self, val, dec_format, decimals):
        if val:
            new_val = (dec_format(val[0], decimals), dec_format(val[1], decimals))
            self.half_point_entry.set_value(str(new_val))

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
