# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 09/27/2019                                         #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolOptimal.OptimalUI import OptimalUI
from appPlugins.ToolOptimal.OptimalGen import prepare_geometry, find_distances
from defaults import AppDefaults
from camlib import grace
from appCommon.SafeEval import safe_eval

import logging

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolOptimal(AppTool):

    update_text = QtCore.pyqtSignal(list)
    update_sec_distances = QtCore.pyqtSignal(dict)
    # (safe tool diameter, how many times the minimum distance occurs): the entries are set in the GUI thread
    update_result = QtCore.pyqtSignal(str, str)

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.units = self.app.app_units.upper()
        self.decimals = self.app.decimals

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = OptimalUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # this is the line selected in the textbox with the locations of the minimum
        self.selected_text = ''

        # this is the line selected in the textbox with the locations of the other distances found in the Gerber object
        self.selected_locations_text = ''

        # dict to hold the distances between every two elements in Gerber as keys and the actual locations where that
        # distances happen as values
        self.min_dict = {}

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+O', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolOptimal()")

        if toggle:
            # if the splitter is hidden, display it
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

            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    # if the Tool Tab is not focused, focus on it
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        # focus on Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        # else remove the Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(2)

                        # if there are no objects loaded in the app then hide the Notebook widget
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()

        self.app.ui.notebook.setTabText(2, _("Find Optimal"))

    def connect_signals_at_init(self):
        self.update_text.connect(self.on_update_text)
        self.update_sec_distances.connect(self.on_update_sec_distances_txt)
        self.update_result.connect(self.on_update_result)

        self.ui.calculate_button.clicked.connect(self.find_minimum_distance)
        self.ui.locate_button.clicked.connect(self.on_locate_position)
        self.ui.locations_textb.cursorPositionChanged.connect(self.on_textbox_clicked)

        self.ui.locate_sec_button.clicked.connect(self.on_locate_sec_position)
        self.ui.distances_textb.cursorPositionChanged.connect(self.on_distances_textb_clicked)
        self.ui.locations_sec_textb.cursorPositionChanged.connect(self.on_locations_sec_clicked)

        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def disconnect_signals(self):
        try:
            self.update_text.disconnect(self.on_update_text)
        except (TypeError, AttributeError):
            pass

        try:
            self.update_sec_distances.disconnect(self.on_update_sec_distances_txt)
        except (TypeError, AttributeError):
            pass

        try:
            self.update_result.disconnect(self.on_update_result)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.calculate_button.clicked.disconnect(self.find_minimum_distance)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.locate_button.clicked.disconnect(self.on_locate_position)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.locations_textb.cursorPositionChanged.disconnect(self.on_textbox_clicked)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.locate_sec_button.clicked.disconnect(self.on_locate_sec_position)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.distances_textb.cursorPositionChanged.disconnect(self.on_distances_textb_clicked)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.locations_sec_textb.cursorPositionChanged.disconnect(self.on_locations_sec_clicked)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.reset_button.clicked.disconnect(self.set_tool_ui)
        except (TypeError, AttributeError):
            pass

    def set_tool_ui(self):
        self.clear_ui(self.layout)
        self.ui = OptimalUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.disconnect_signals()
        self.connect_signals_at_init()

        self.ui.result_entry.set_value(0.0)
        self.ui.freq_entry.set_value(0)

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.ui.precision_spinner.set_value(int(self.app.options.get(
            "tools_opt_precision",
            app_defaults.get("tools_opt_precision", AppDefaults.factory_defaults.get("tools_opt_precision"))
        )))
        self.ui.locations_textb.clear()
        self.ui.clear_highlight(self.ui.locations_textb)

        self.ui.locations_textb.setVisible(False)
        self.ui.locate_button.setVisible(False)

        self.ui.result_entry.set_value(0.0)
        self.ui.freq_entry.set_value(0)
        self.reset_fields()

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj and obj.kind == 'gerber':
            obj_name = obj.obj_options['name']
            self.ui.gerber_object_combo.set_value(obj_name)

    def find_minimum_distance(self):
        self.units = self.app.app_units.upper()
        self.decimals = int(self.ui.precision_spinner.get_value())
        self.ui.result_entry.set_value('')
        self.ui.freq_entry.set_value(0)
        for text_box in (self.ui.locations_textb, self.ui.distances_textb, self.ui.locations_sec_textb):
            with QtCore.QSignalBlocker(text_box):
                text_box.clear()
        self.selected_text = ''
        self.selected_locations_text = ''
        self.ui.locate_button.setDisabled(True)
        self.ui.locate_sec_button.setDisabled(True)
        self.min_dict = {}

        selection_index = self.ui.gerber_object_combo.currentIndex()

        model_index = self.app.collection.index(selection_index, 0, self.ui.gerber_object_combo.rootModelIndex())
        try:
            fcobj = model_index.internalPointer().obj
        except Exception as e:
            self.app.log.error("ToolOptimal.find_minimum_distance() --> %s" % str(e))
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Gerber object loaded ..."))
            return

        if fcobj.kind != 'gerber':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Only Gerber objects can be evaluated."))
            return

        proc = self.app.proc_container.new('%s...' % _("Working"))

        def job_thread(app_obj, plugin_instance):
            app_obj.inform.emit(_("Optimal Tool. Started to search for the minimum distance between copper features."))
            try:
                app_obj.proc_container.update_view_text(' %d%%' % 0)
                total_geo = []

                for ap in list(fcobj.tools.keys()):
                    if 'geometry' in fcobj.tools[ap]:
                        app_obj.inform.emit(
                            '%s: %s' % (_("Optimal Tool. Parsing geometry for aperture"), str(ap)))

                        for geo_el in fcobj.tools[ap]['geometry']:
                            if self.app.abort_flag:
                                # graceful abort requested by the user
                                raise grace

                            if 'solid' in geo_el and geo_el['solid'] is not None and geo_el['solid'].is_valid:
                                total_geo.append(geo_el['solid'])

                app_obj.inform.emit(
                    _("Optimal Tool. Creating a buffer for the object geometry."))
                total_geo = prepare_geometry(total_geo)

                geo_len = len(total_geo)
                if geo_len <= 1:
                    app_obj.inform.emit('[ERROR_NOTCL] %s' %
                                        _("The Gerber object has one Polygon as geometry.\n"
                                          "There are no distances between geometry elements to be found."))
                    return 'fail'

                app_obj.inform.emit(_("Optimal Tool. Finding the minimum distance."))

                # The distances between neighbor polygons only (each polygon has at least its nearest polygon); the
                # minimum distance, its frequency and its locations are exact. A check of every pair is O(n^2).
                plugin_instance.min_dict = find_distances(total_geo, plugin_instance.decimals)
                app_obj.proc_container.update_view_text(' %d%%' % 100)

                min_list = list(plugin_instance.min_dict.keys())
                min_dist = min(min_list)
                rep_min_dist = min_dist - 10**-self.decimals  # make sure that this will work for isolation case
                safe_dia = app_obj.dec_format(rep_min_dist, self.decimals)
                if safe_dia <= 0:
                    app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Could not find a safe tool diameter."))
                    return 'fail'
                min_dist_string = str(safe_dia)

                freq = len(plugin_instance.min_dict[min_dist])
                freq = '%d' % int(freq)
                # this runs in a worker thread: the widgets are changed only in the GUI thread
                self.update_result.emit(min_dist_string, freq)

                min_locations = plugin_instance.min_dict.pop(min_dist)

                self.update_text.emit(min_locations)
                self.update_sec_distances.emit(plugin_instance.min_dict)

                app_obj.inform.emit('[success] %s' % _("Optimal Tool. Finished successfully."))
            except Exception as ee:
                app_obj.log.error(str(ee))
                return
            finally:
                proc.done()

        self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app, self]})

    def on_locate_position(self):
        # cursor = self.locations_textb.textCursor()
        # self.selected_text = cursor.selectedText()

        try:
            if self.selected_text != '':
                loc = safe_eval(self.selected_text)
            else:
                return 'fail'
        except Exception as e:
            self.app.log.error("ToolOptimal.on_locate_position() --> first try %s" % str(e))
            self.app.inform.emit("[ERROR_NOTCL] The selected text is no valid location in the format "
                                 "((x0, y0), (x1, y1)).")
            return

        try:
            loc_1 = loc[0]
            loc_2 = loc[1]
            dx = loc_1[0] - loc_2[0]
            dy = loc_1[1] - loc_2[1]
            loc = (float('%.*f' % (self.decimals, (min(loc_1[0], loc_2[0]) + (abs(dx) / 2)))),
                   float('%.*f' % (self.decimals, (min(loc_1[1], loc_2[1]) + (abs(dy) / 2)))))
            self.app.on_jump_to(custom_location=loc)
        except Exception as e:
            self.app.log.error("ToolOptimal.on_locate_position() --> sec try %s" % str(e))
            return

    def on_update_text(self, data):
        self.ui.on_update_text(data)

    def on_update_result(self, min_dist_string, freq):
        self.ui.result_entry.set_value(min_dist_string)
        self.ui.freq_entry.set_value(freq)

    def on_textbox_clicked(self):
        self.selected_text = self.ui.highlight_current_line(self.ui.locations_textb)

    def on_update_sec_distances_txt(self, data):
        self.ui.on_update_sec_distances_txt(data)

    def on_distances_textb_clicked(self):
        distance_text = self.ui.highlight_current_line(self.ui.distances_textb)
        # the empty line after the last distance (or an empty box) is no distance
        try:
            key_in_min_dict = safe_eval(distance_text)
        except ValueError:
            return
        if key_in_min_dict not in self.min_dict:
            return
        self.on_update_locations_text(dist=key_in_min_dict)

    def on_update_locations_text(self, dist):
        self.ui.on_update_locations_text(self.min_dict[dist])

    def on_locations_sec_clicked(self):
        self.selected_locations_text = self.ui.highlight_current_line(self.ui.locations_sec_textb)

    def on_locate_sec_position(self):
        try:
            if self.selected_locations_text != '':
                loc = safe_eval(self.selected_locations_text)
            else:
                return
        except Exception as e:
            self.app.log.error("ToolOptimal.on_locate_sec_position() --> first try %s" % str(e))
            self.app.inform.emit("[ERROR_NOTCL] The selected text is no valid location in the format "
                                 "((x0, y0), (x1, y1)).")
            return

        try:
            loc_1 = loc[0]
            loc_2 = loc[1]
            dx = loc_1[0] - loc_2[0]
            dy = loc_1[1] - loc_2[1]
            loc = (float('%.*f' % (self.decimals, (min(loc_1[0], loc_2[0]) + (abs(dx) / 2)))),
                   float('%.*f' % (self.decimals, (min(loc_1[1], loc_2[1]) + (abs(dy) / 2)))))
            self.app.on_jump_to(custom_location=loc)
        except Exception as e:
            self.app.log.error("ToolOptimal.on_locate_sec_position() --> sec try %s" % str(e))
            return

    def reset_fields(self):
        self.ui.gerber_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ui.gerber_object_combo.setCurrentIndex(0)
