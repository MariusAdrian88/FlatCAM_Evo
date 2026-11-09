# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 4/24/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appCommon.Common import GracefulException
from appHandlers.appHistory import history_of
from appObjects.ProjectContext import unique_name
from defaults import AppDefaults
from appPlugins.ToolSub.SubUI import SubUI
from appPlugins.ToolSub import SubGen

import logging
import traceback

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

KINDS = ('gerber', 'geometry', 'excellon')
CHECKED = QtCore.Qt.CheckState.Checked
UNCHECKED = QtCore.Qt.CheckState.Unchecked
ID_ROLE = QtCore.Qt.ItemDataRole.UserRole


class ToolSub(AppTool):
    """
    Threads: the widgets, the object collection, the Undo history and the messages are used in the GUI thread only.
    One click puts ONE job on the app worker (the geometry work: unions, buffers, the operation) with the values that
    were read at click time. The job ends with exactly one job_done signal, which the GUI thread receives in
    on_job_done(): there the result object is created and the deleted objects are recorded as one Undo step.
    """

    # (kind, payload): 'preview' area | 'result' (params, SubResult) | 'cancelled' | 'invalid' text | 'error' text
    job_done = QtCore.pyqtSignal(str, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)

        self._busy = False
        self._loading = False
        self._name_edited = False
        self._current_sub = None
        self._target_tools_of = None
        self._sub_tool_state = {}
        self._preview_drawn = False

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = SubUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_ui()

        # the signals of the tool and of the app are connected ONCE; only the widgets are connected again
        self.job_done.connect(self.on_job_done)
        self.app.object_status_changed.connect(self.on_objects_changed)
        self.app.proj_selection_changed.connect(self.on_object_selection_changed)

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+W', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolSub()")

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

        self.app.ui.notebook.setTabText(2, _("Subtract"))

    def on_plugin_cleanup(self):
        self.clear_preview()

    # ------------------------------------------------------------------------------------------------------------
    # the UI
    # ------------------------------------------------------------------------------------------------------------
    def _opt(self, key):
        return self.app.options.get(key, AppDefaults.factory_defaults.get(key))

    def _is_open(self):
        try:
            return self.app.ui.plugin_scroll_area.widget() is self
        except (AttributeError, RuntimeError):
            return False

    def connect_ui(self):
        """Connect the widgets of the current SubUI. Called each time the UI is built."""
        ui = self.ui
        ui.level.toggled.connect(self.on_level_changed)
        ui.type_combo.currentIndexChanged.connect(self.on_type_changed)
        ui.target_combo.currentIndexChanged.connect(self.on_target_changed)
        ui.op_combo.currentIndexChanged.connect(self.on_op_changed)
        ui.subs_list.currentRowChanged.connect(self.on_sub_row_changed)
        ui.sub_tools_list.itemChanged.connect(self.on_sub_tool_changed)
        ui.out_entry.textEdited.connect(self.on_name_edited)
        ui.preview_button.clicked.connect(self.on_preview)
        ui.apply_button.clicked.connect(self.on_apply)
        ui.reset_button.clicked.connect(self.set_tool_ui)

    def set_tool_ui(self, *args):
        self.clear_preview()
        self._name_edited = False
        self._current_sub = None
        self._target_tools_of = None
        self._sub_tool_state = {}

        self.clear_ui(self.layout)
        self.ui = SubUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_ui()

        ui = self.ui
        ui.tools_frame.show()
        ui.close_paths_cb.setChecked(bool(self._opt("tools_sub_close_paths")))
        ui.keep_target_cb.setChecked(bool(self._opt("tools_sub_keep_target")))
        ui.delete_subs_cb.setChecked(bool(self._opt("tools_sub_delete_subtractors")))
        ui.margin_entry.set_value(float(self._opt("tools_sub_margin") or 0.0))
        idx = ui.op_combo.findData(self._opt("tools_sub_operation"))
        ui.op_combo.setCurrentIndex(idx if idx >= 0 else 0)

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj is not None and obj.kind in KINDS:
            self.apply_selection(obj, [])
        else:
            self.apply_selection(None, [])

        self.on_op_changed()
        self.change_level(self._opt("global_app_level"))

    def change_level(self, level):
        """
        :param level:   application level: either 'b' or 'a'
        :type level:    str
        """
        self.ui.level.setChecked(level == 'a')
        self.on_level_changed(self.ui.level.isChecked())

    def on_level_changed(self, checked):
        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("QToolButton { color: green; }")
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("QToolButton { color: red; }")

        for widget in self.ui.advanced_widgets:
            widget.setVisible(bool(checked))

    # ------------------------------------------------------------------------------------------------------------
    # the lists
    # ------------------------------------------------------------------------------------------------------------
    def _objects(self, kind):
        return [o for o in self.app.collection.get_list() if o.kind == kind]

    def tool_entries(self, obj):
        """[(id, label)] of the apertures / tools that can be selected on an object."""
        if obj is None or (obj.kind == 'geometry' and not getattr(obj, 'multigeo', False)):
            return []
        out = []
        for tid, tool in (getattr(obj, 'tools', None) or {}).items():
            if obj.kind == 'gerber':
                desc = ' '.join(str(v) for v in (tool.get('type', ''), tool.get('size', '')) if v != '')
            elif obj.kind == 'excellon':
                desc = '%.*f' % (self.decimals, float(tool.get('tooldia', 0.0) or 0.0))
            else:
                desc = str(tool.get('tooldia', ''))
            out.append((tid, ('%s  %s' % (tid, desc)).strip()))
        return out

    @staticmethod
    def _fill_checklist(widget, entries, state=None):
        widget.blockSignals(True)
        widget.clear()
        for tid, label in entries:
            item = QtWidgets.QListWidgetItem(label)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(ID_ROLE, tid)
            item.setCheckState(CHECKED if (state is None or state.get(tid, True)) else UNCHECKED)
            widget.addItem(item)
        widget.blockSignals(False)

    @staticmethod
    def _checked_ids(widget):
        """The checked ids, or None when all of them are checked (or there are none)."""
        ids = [widget.item(i).data(ID_ROLE) for i in range(widget.count())
               if widget.item(i).checkState() == CHECKED]
        return None if len(ids) == widget.count() else ids

    def fill_targets(self, select=None):
        ui = self.ui
        names = [o.obj_options['name'] for o in self._objects(ui.type_combo.currentData())]
        self._loading = True
        try:
            ui.target_combo.clear()
            ui.target_combo.addItems(names)
            if select in names:
                ui.target_combo.setCurrentIndex(names.index(select))
        finally:
            self._loading = False

    def fill_subtractors(self, checked=None):
        """All the objects of the 3 kinds but the target. checked: the names to check (None: keep the checks)."""
        ui = self.ui
        target = ui.target_combo.currentText()
        if checked is None:
            checked = {ui.subs_list.item(i).text() for i in range(ui.subs_list.count())
                       if ui.subs_list.item(i).checkState() == CHECKED}
        current = self._current_sub
        ui.subs_list.blockSignals(True)
        ui.subs_list.clear()
        row = -1
        for obj in self.app.collection.get_list():
            name = obj.obj_options['name']
            if obj.kind not in KINDS or name == target:
                continue
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(CHECKED if name in checked else UNCHECKED)
            item.setToolTip(obj.kind)
            ui.subs_list.addItem(item)
            if name == current:
                row = ui.subs_list.count() - 1
        ui.subs_list.setCurrentRow(row)
        ui.subs_list.blockSignals(False)
        self.on_sub_row_changed()

    def apply_selection(self, target, others):
        """Make an object the target and check the others as subtractors (target None: the first of the type)."""
        ui = self.ui
        self._loading = True
        try:
            if target is not None:
                idx = ui.type_combo.findData(target.kind)
                if idx >= 0:
                    ui.type_combo.setCurrentIndex(idx)
        finally:
            self._loading = False
        self.fill_targets(select=target.obj_options['name'] if target is not None else None)
        self.on_target_changed()
        if others:
            self.fill_subtractors(checked={o.obj_options['name'] for o in others})

    def on_type_changed(self, *args):
        if self._loading:
            return
        self.fill_targets()
        self.on_target_changed()

    def on_target_changed(self, *args):
        if self._loading:
            return
        ui = self.ui
        name = ui.target_combo.currentText()
        kind = ui.type_combo.currentData()
        target = self.app.collection.get_by_name(name) if name else None

        # XOR does not exist for Excellon; Close paths is for a Geometry target
        ui.op_combo.model().item(2).setEnabled(kind != 'excellon')
        if kind == 'excellon' and ui.op_combo.currentData() == 'xor':
            ui.op_combo.setCurrentIndex(0)
        ui.close_paths_cb.setEnabled(kind == 'geometry')

        if name != self._target_tools_of:
            self._fill_checklist(ui.target_tools_list, self.tool_entries(target))
            self._target_tools_of = name
        if not self._name_edited:
            ui.out_entry.setText((name + str(self._opt("tools_sub_suffix") or '')) if name else '')
        self.fill_subtractors()

    def on_op_changed(self, *args):
        labels = {'subtract': _("Subtract"), 'intersect': _("Intersect"), 'xor': _("XOR")}
        self.ui.apply_button.setText(labels.get(self.ui.op_combo.currentData(), _("Subtract")))

    def on_name_edited(self, *args):
        self._name_edited = True

    def on_sub_row_changed(self, *args):
        ui = self.ui
        row = ui.subs_list.currentRow()
        name = ui.subs_list.item(row).text() if row >= 0 else None
        self._current_sub = name
        obj = self.app.collection.get_by_name(name) if name else None
        self._fill_checklist(ui.sub_tools_list, self.tool_entries(obj), self._sub_tool_state.get(name))

    def on_sub_tool_changed(self, *args):
        name = self._current_sub
        if name is None:
            return
        widget = self.ui.sub_tools_list
        self._sub_tool_state[name] = {widget.item(i).data(ID_ROLE): widget.item(i).checkState() == CHECKED
                                      for i in range(widget.count())}

    def on_objects_changed(self, obj, action, name):
        """Objects were added, deleted or renamed: the lists follow (after the collection finished the change)."""
        if self._is_open():
            QtCore.QTimer.singleShot(0, self.refresh_objects)

    def refresh_objects(self):
        if not self._is_open():
            return
        try:
            self.fill_targets(select=self.ui.target_combo.currentText())
            self.on_target_changed()
        except RuntimeError:
            pass

    def on_object_selection_changed(self, current, previous):
        """Project tree selection while the plugin is open: the first is the target, the others are subtractors."""
        if not self._is_open() or self._busy:
            return
        try:
            selected = [o for o in self.app.collection.get_selected() if o.kind in KINDS]
        except (AttributeError, RuntimeError):
            return
        if selected:
            self.apply_selection(selected[0], selected[1:])

    # ------------------------------------------------------------------------------------------------------------
    # the click: read the values (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def _set_busy(self, busy):
        self._busy = busy
        try:
            self.ui.apply_button.setEnabled(not busy)
            self.ui.preview_button.setEnabled(not busy)
        except RuntimeError:
            pass

    def _error(self, text):
        self.app.inform.emit('[ERROR_NOTCL] %s' % text)
        return None

    def collect(self, for_preview=False):
        """Read every value of the UI and look the objects up. Returns a dict, or None after a message."""
        ui = self.ui
        collection = self.app.collection

        target_name = ui.target_combo.currentText()
        if target_name == '':
            return self._error(_("No Target object loaded."))
        target = collection.get_by_name(target_name)
        if target is None:
            return self._error('%s: %s' % (_("Could not retrieve object"), target_name))

        subs = []
        for i in range(ui.subs_list.count()):
            item = ui.subs_list.item(i)
            if item.checkState() != CHECKED:
                continue
            sub = collection.get_by_name(item.text())
            if sub is None:
                return self._error('%s: %s' % (_("Could not retrieve object"), item.text()))
            subs.append(sub)
        if not subs:
            return self._error(_("No Subtractor object selected."))
        if any(s is target for s in subs):
            return self._error(_("The target object can not be a subtractor."))

        op = ui.op_combo.currentData()
        kind = target.kind
        if not for_preview and kind == 'excellon' and op == 'xor':
            return self._error(_("XOR is not valid for an Excellon target."))

        advanced = ui.level.isChecked()
        target_tools = self._checked_ids(ui.target_tools_list) if advanced else None
        sub_tools = {}
        if advanced:
            for sub in subs:
                state = self._sub_tool_state.get(sub.obj_options['name'])
                if not state:
                    continue
                ids = [tid for tid, label in self.tool_entries(sub)]
                selected = [tid for tid in ids if state.get(tid, True)]
                if len(selected) != len(ids):
                    sub_tools[sub.obj_options['name']] = selected

        base = ui.out_entry.text().strip() or (target_name + str(self._opt("tools_sub_suffix") or ''))
        return {
            'target': target, 'target_name': target_name, 'subs': subs, 'op': op, 'kind': kind,
            'margin': float(ui.margin_entry.get_value()),
            'steps': int(self._opt('%s_circle_steps' % kind) or 64),
            'close_paths': bool(ui.close_paths_cb.isChecked()) and kind == 'geometry',
            'target_tools': target_tools, 'sub_tools': sub_tools,
            'units': (target.obj_options or {}).get('units'),
            'out_base': base,
            'keep_target': bool(ui.keep_target_cb.isChecked()),
            'delete_subs': bool(ui.delete_subs_cb.isChecked()),
        }

    def _start(self, params, mode):
        self._set_busy(True)
        self.app.inform.emit('%s' % _("Working..."))
        self.app.worker_task.emit({'fcn': self.worker_job, 'params': [params, mode]})

    def on_apply(self, *args):
        if self._busy:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Another Subtract run is still working."))
            return
        params = self.collect()
        if params is None:
            return
        self.clear_preview()
        self._start(params, 'apply')

    def on_preview(self, *args):
        if self._busy:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Another Subtract run is still working."))
            return
        params = self.collect(for_preview=True)
        if params is None:
            return
        self._start(params, 'preview')

    # ------------------------------------------------------------------------------------------------------------
    # the job (worker thread): geometry only, no Qt widgets, no collection changes
    # ------------------------------------------------------------------------------------------------------------
    def check_abort(self):
        if getattr(self.app, 'abort_flag', False):
            raise GracefulException()

    def worker_job(self, params, mode):
        kind, payload = 'error', ''
        try:
            with self.app.proc_container.new('%s...' % _("Working")):
                if mode == 'preview':
                    area = SubGen.subtractor_area(params['subs'], margin=params['margin'], steps=params['steps'],
                                                  tools_by_name=params['sub_tools'], target_units=params['units'])
                    kind, payload = 'preview', area
                else:
                    result = SubGen.compute(
                        params['target'], params['subs'], params['op'], params['margin'], params['steps'],
                        params['close_paths'], params['target_tools'], params['sub_tools'], abort=self.check_abort)
                    kind, payload = 'result', (params, result)
        except GracefulException:
            kind, payload = 'cancelled', None
        except ValueError as e:
            kind, payload = 'invalid', str(e)
        except Exception as e:
            log.error("ToolSub.worker_job() --> %s\n%s" % (str(e), traceback.format_exc()))
            kind, payload = 'error', str(e)
        self.job_done.emit(kind, payload)

    # ------------------------------------------------------------------------------------------------------------
    # the end of the job (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def on_job_done(self, kind, payload):
        self._set_busy(False)
        try:
            if kind == 'preview':
                self.draw_preview(payload)
            elif kind == 'cancelled':
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            elif kind == 'invalid':
                self._error(str(payload))
            elif kind == 'result':
                self.finish(*payload)
            else:
                self._error('%s: %s' % (_("Subtract failed"), payload))
        except Exception as e:
            log.error("ToolSub.on_job_done() --> %s\n%s" % (str(e), traceback.format_exc()))
            self._error('%s: %s' % (_("Subtract failed"), str(e)))

    def finish(self, params, result):
        """Create the result and delete what was asked, as ONE Undo step."""
        if result.empty_area:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "The subtractor has no area (a Geometry of lines needs a Margin above zero). Nothing was created."))
            return

        collection = self.app.collection
        target = params['target']
        if collection.get_by_name(params['target_name']) is not target:
            self._error(_("The target object does not exist anymore."))
            return

        keep = params['keep_target']
        to_delete = [] if keep else [target]
        if params['delete_subs']:
            to_delete += [s for s in params['subs'] if collection.get_by_name(s.obj_options['name']) is s]
        # the result is made first (under a free name): a failed creation must not lose the deleted objects
        outname = params['target_name'] if not keep else unique_name(params['out_base'], collection.get_names())
        create_name = outname if keep else unique_name(params['out_base'], collection.get_names())

        def obj_init(new_obj, app_obj):
            SubGen.apply_result(new_obj, result, target)
            if result.kind in ('gerber', 'excellon') and new_obj.regenerate_source(name=outname) is False:
                log.warning("ToolSub: the source text of %s could not be regenerated." % outname)

        history = history_of(self.app)
        label = self.pluginName
        with history.group(label):
            ret = self.new_object(result.kind, create_name, obj_init, source=target, autoselected=False)
            if ret is None or (isinstance(ret, str) and ret == 'fail'):
                self._error(_("Generating new object failed."))
                return
            if to_delete:
                history.record_delete(label, to_delete)
                self.app.object_ops.delete_objects_now(to_delete)
            new_obj = collection.get_by_name(create_name)
            if new_obj is not None and create_name != outname:
                collection.rename_object(new_obj, outname)
                history.record_rename(new_obj, create_name, outname)

        stats = result.stats
        self.app.inform.emit('[success] %s: %s. %s %d, %s %d, %s %d, %s %d' % (
            _("Created"), outname, _("Kept"), stats.get('kept', 0), _("changed"), stats.get('changed', 0),
            _("removed"), stats.get('removed', 0), _("added"), stats.get('added', 0)))

    # ------------------------------------------------------------------------------------------------------------
    # the preview (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def draw_preview(self, area):
        self.clear_preview()
        shapes = self.app.tool_shapes
        if shapes is None:
            return
        if area.is_empty:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The subtractor has no area."))
            return
        for poly in (area.geoms if hasattr(area, 'geoms') else [area]):
            if not poly.is_empty:
                shapes.add(poly, color='#00AA00FF', face_color='#00AA0050', update=False, layer=0, tolerance=None)
        shapes.redraw()
        self._preview_drawn = True

    def clear_preview(self):
        if self._preview_drawn and self.app.tool_shapes is not None:
            self.app.tool_shapes.clear(update=True)
        self._preview_drawn = False
