# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from defaults import AppDefaults
from appPlugins.ToolCompare.CompareUI import CompareUI
from appPlugins.ToolCompare import CompareGen
from appPlugins.ToolCompare.CompareGen import Cancelled

from collections import deque
from shapely import get_num_coordinates

import logging
import traceback

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

# index of the object type group in the ObjectCollection model and the name of the type
OBJ_TYPES = {'grb': (0, "Gerber"), 'exc': (1, "Excellon"), 'geo': (2, "Geometry")}
KINDS = ('removed', 'added')
POLL_MS = 30
# draw cost of one fill vertex in line vertices when multiprocessing is off: the GUI thread tessellates the fill,
# ~40 us per vertex (2026-11-04), so one collection holds <= ~3000 fill vertices (~120 ms). With multiprocessing on,
# the pool tessellates and small collections only add overhead: weight 1.
FILL_WEIGHT_NO_MP = 70


class _Part:
    """One result collection of one run: its shapes (until submitted), its cost in vertices and its state:
    'new' (queued), 'pending' (batch running), 'ready' (batch done, install queued), 'installed', 'dead'."""
    __slots__ = ('run', 'kind', 'items', 'cost', 'coll', 'state')

    def __init__(self, run, kind, items, cost):
        self.run, self.kind, self.items, self.cost = run, kind, items, cost
        self.coll = None
        self.state = 'new'


def pack_side(side, colors, max_vertices, fill_weight=1):
    """Worker thread: the draw items of one side in groups of a draw cost of at most max_vertices. [(items, cost)]
    Fill pieces: face only, a vertex costs fill_weight. Line paths: the outline color, a vertex costs 1."""
    packs, items, cost = [], [], 0

    def add(shape, color, face_color):
        nonlocal items, cost
        n = int(get_num_coordinates(shape)) * (fill_weight if face_color else 1)
        if items and cost + n > max_vertices:
            packs.append((items, cost))
            items, cost = [], 0
        items.append({'shape': shape, 'color': color, 'face_color': face_color, 'layer': 0, 'tolerance': None})
        cost += n

    for poly in side.fill_pieces:
        add(poly, None, colors['fill'])
    for path in side.line_paths:
        add(path, colors['outline'], None)
    if items:
        packs.append((items, cost))
    return packs


class ToolCompare(AppTool):
    """
    Threads: the widgets, the collections and the messages are used in the GUI thread only. One click puts ONE job on
    the app worker with snapshots of the 2 objects. The job ends with exactly one job_done signal; a result of an old
    run (run id) is dropped. The result is drawn in many small collections, a vertex budget per timer tick.
    """

    # (run id, payload): payload is ('ok', CompareResult, packs) | ('cancel',) | ('abort',) | ('error', text)
    job_done = QtCore.pyqtSignal(int, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)

        self._run_id = 0
        self._active = False
        self._loading = False
        self._run_objs = None
        self._want = {'removed': True, 'added': True}
        self._parts = []                # the parts of the current run
        self._pending = []              # parts whose batch is running
        self._retired = []              # detached collections whose batch is still running
        self._queue = deque()           # (op, part): 'submit' | 'install' | 'vis'

        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(0)
        self._tick_timer.timeout.connect(self._tick)
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll)

        self.ui = CompareUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_ui()

        # the signals of the tool and of the app are connected ONCE; only the widgets are connected again
        self.job_done.connect(self.on_job_done)
        self.app.object_status_changed.connect(self.on_object_status)
        self.app.app_obj.object_changed.connect(self.on_object_changed)
        self._connect_collection(self.app.collection)

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+5', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolCompare()")

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
        self._active = True
        self.set_tool_ui()

        self.app.ui.notebook.setTabText(2, _("Compare"))

    def on_plugin_cleanup(self):
        self._active = False
        self._invalidate()

    def _cleanup_if_inactive(self):
        ui = self.app.ui
        if ui.plugin_scroll_area.widget() is not self or ui.notebook.indexOf(ui.plugin_tab) < 0:
            self.on_plugin_cleanup()

    def event(self, event):
        result = super().event(event)
        if event.type() in (QtCore.QEvent.Type.Hide, QtCore.QEvent.Type.ParentChange) and \
                getattr(self, '_active', False):
            # Wait for takeWidget()/removeTab() to finish; hiding a notebook page isn't a close.
            QtCore.QTimer.singleShot(0, self._cleanup_if_inactive)
        return result

    # ------------------------------------------------------------------------------------------------------------
    # the UI
    # ------------------------------------------------------------------------------------------------------------
    def _opt(self, key):
        return self.app.options.get(key, AppDefaults.factory_defaults.get(key))

    def _colors(self, kind):
        key = 'tools_compare_%s_color' % kind
        return CompareGen.normalize_color(self._opt(key), AppDefaults.factory_defaults.get(key))

    def connect_ui(self):
        """Connect the widgets of the current CompareUI. Called each time the UI is built."""
        ui = self.ui
        ui.type_radio.activated_custom.connect(self.on_type_changed)
        ui.old_combo.currentIndexChanged.connect(self.on_selection_changed)
        ui.new_combo.currentIndexChanged.connect(self.on_selection_changed)
        ui.tol_entry.valueChanged.connect(self.on_selection_changed)
        ui.removed_cb.toggled.connect(lambda state: self._set_want('removed', state))
        ui.added_cb.toggled.connect(lambda state: self._set_want('added', state))
        ui.removed_swatch.clicked.connect(lambda: self.on_color('removed'))
        ui.added_swatch.clicked.connect(lambda: self.on_color('added'))
        ui.compare_button.clicked.connect(self.on_compare)
        ui.reset_button.clicked.connect(self.set_tool_ui)

    def on_color(self, kind):
        color = QtWidgets.QColorDialog.getColor(initial=QtGui.QColor(self._colors(kind)['swatch']), parent=self)
        if not color.isValid():
            return
        key = 'tools_compare_%s_color' % kind
        value = color.name() + 'FF'
        self.app.options[key] = value
        self.app.defaults[key] = value
        self.app.defaults.current_defaults[key] = value
        self.app.preferencesUiManager.save_defaults(silent=True)
        self.ui.set_swatch(getattr(self.ui, kind + '_swatch'), color.name())
        self._set_want(kind, self._want[kind])

    def set_tool_ui(self, *args):
        self._invalidate()

        self.clear_ui(self.layout)
        self.ui = CompareUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_ui()

        ui = self.ui
        self._loading = True
        try:
            kind = self._opt('tools_compare_type')
            ui.type_radio.set_value(kind if kind in OBJ_TYPES else 'grb')
            self._apply_type(ui.type_radio.get_value())
            ui.tol_entry.set_value(float(self._opt('tools_compare_tolerance') or 0.0))
            self._want = {'removed': bool(self._opt('tools_compare_show_removed')),
                          'added': bool(self._opt('tools_compare_show_added'))}
            ui.removed_cb.setChecked(self._want['removed'])
            ui.added_cb.setChecked(self._want['added'])
        finally:
            self._loading = False
        ui.set_swatch(ui.removed_swatch, self._colors('removed')['swatch'])
        ui.set_swatch(ui.added_swatch, self._colors('added')['swatch'])

    def _apply_type(self, val):
        obj_type, obj_name = OBJ_TYPES[val]
        for combo in (self.ui.old_combo, self.ui.new_combo):
            combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
            combo.setCurrentIndex(0)
            combo.obj_type = obj_name

    def on_type_changed(self, val):
        self._apply_type(val)
        self._invalidate()

    def on_selection_changed(self, *args):
        if not self._loading:
            self._invalidate()

    def _object(self, combo):
        index = self.app.collection.index(combo.currentIndex(), 0, combo.rootModelIndex())
        try:
            return index.internalPointer().obj
        except AttributeError:
            return None

    def _set_summary(self, text):
        try:
            self.ui.summary_label.setText(text)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------------------------------------------------
    # the run token
    # ------------------------------------------------------------------------------------------------------------
    def _invalidate(self):
        """The result of the running (or shown) run is not valid anymore: stop the drawing, detach the collections."""
        self._run_id += 1
        self._run_objs = None
        self._tick_timer.stop()
        self._queue.clear()
        self._pending.clear()
        for part in self._parts:
            self._retire(part)
        self._parts.clear()
        self._set_summary('')

    def _retire(self, part):
        """Detach the collection of a part from the scene. One with a batch still running waits in self._retired."""
        coll, part.state, part.items = part.coll, 'dead', None
        part.coll = None
        if coll is None:
            return
        coll.enabled = False
        if coll.pending_ready():
            coll.clear()
        else:
            self._retired.append(coll)
            self._poll_timer.start()

    def _connect_collection(self, collection):
        collection.rowsRemoved.connect(self.on_collection_changed)
        collection.modelReset.connect(self.on_collection_changed)

    def set_project_collection(self, old_collection, collection):
        if old_collection is collection:
            return
        super().set_project_collection(old_collection, collection)
        for name in ('rowsRemoved', 'modelReset'):
            try:
                getattr(old_collection, name).disconnect(self.on_collection_changed)
            except (TypeError, RuntimeError):
                pass
        self._connect_collection(collection)
        self._invalidate()

    def on_collection_changed(self, *args):
        """A source object that is not in the project anymore: the result is not valid."""
        if self._run_objs is None:
            return
        present = {id(o) for o in self.app.collection.get_list()}
        if any(id(o) not in present for o in self._run_objs):
            self._invalidate()

    def on_object_status(self, obj, action, name):
        if self._run_objs is None:
            return
        if action == 'delete_all' or (action in ('delete', 'rename') and any(obj is o for o in self._run_objs)):
            self._invalidate()

    def on_object_changed(self, obj):
        if self._run_objs is not None and any(obj is o for o in self._run_objs):
            self._invalidate()

    # ------------------------------------------------------------------------------------------------------------
    # the click: read the values and take the snapshots (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def on_compare(self, *args):
        self._invalidate()
        ui = self.ui
        old, new = self._object(ui.old_combo), self._object(ui.new_combo)
        if old is None or new is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Select the Old and the New object."))
            return
        if old is new:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Old and New are the same object."))
            return

        kind = ui.type_radio.get_value()
        try:
            snap_old = CompareGen.snapshot(old, kind)
            snap_new = CompareGen.snapshot(new, kind)
        except Exception as e:
            log.error("ToolCompare.on_compare() --> %s\n%s" % (str(e), traceback.format_exc()))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Compare failed"), str(e)))
            return

        params = {'kind': kind, 'tol': float(ui.tol_entry.get_value()),
                  'colors': {k: self._colors(k) for k in KINDS},
                  'fill_weight': FILL_WEIGHT_NO_MP if self._opt('global_graphic_engine_3d_no_mp') else 1}
        ui.set_swatch(ui.removed_swatch, params['colors']['removed']['swatch'])
        ui.set_swatch(ui.added_swatch, params['colors']['added']['swatch'])
        self._run_objs = (old, new)
        self._set_summary(_("Working..."))
        self.app.inform.emit('%s' % _("Working..."))
        self.app.worker_task.emit({'fcn': self.worker_job, 'params': [self._run_id, snap_old, snap_new, params]})

    # ------------------------------------------------------------------------------------------------------------
    # the job (worker thread): geometry only, no Qt widgets, no collections
    # ------------------------------------------------------------------------------------------------------------
    def worker_job(self, run_id, snap_old, snap_new, params):
        payload = ('error', '')

        def cancelled():
            return bool(getattr(self.app, 'abort_flag', False)) or run_id != self._run_id

        try:
            with self.app.proc_container.new('%s...' % _("Comparing")):
                result = CompareGen.compare(snap_old, snap_new, params['kind'], params['tol'], cancelled)
                packs = {k: pack_side(getattr(result, k), params['colors'][k], CompareGen.B_COLL,
                                      params.get('fill_weight', 1)) for k in KINDS}
            payload = ('ok', result, packs)
        except Cancelled:
            payload = ('abort',) if getattr(self.app, 'abort_flag', False) else ('cancel',)
        except Exception as e:
            log.error("ToolCompare.worker_job() --> %s\n%s" % (str(e), traceback.format_exc()))
            payload = ('error', str(e))
        self.job_done.emit(run_id, payload)

    # ------------------------------------------------------------------------------------------------------------
    # the end of the job (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def on_job_done(self, run_id, payload):
        if run_id != self._run_id or not self._active:
            return
        state = payload[0]
        try:
            if state == 'ok':
                self._show_result(payload[1], payload[2])
                return
            self._set_summary('')
            if state == 'abort':
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            elif state == 'error':
                self._set_summary(_("Compare failed"))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Compare failed"), payload[1]))
        except Exception as e:
            log.error("ToolCompare.on_job_done() --> %s\n%s" % (str(e), traceback.format_exc()))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Compare failed"), str(e)))

    def _side_text(self, label, stats):
        parts = ['%s: %d' % (label, stats['count'])]
        if stats.get('area'):
            parts.append('%s %.*f' % (_("area"), self.decimals, stats['area']))
        if stats.get('length'):
            parts.append('%s %.*f' % (_("length"), self.decimals, stats['length']))
        return ', '.join(parts)

    def _show_result(self, result, packs):
        lines = [self._side_text(_("Removed"), result.stats['removed']),
                 self._side_text(_("Added"), result.stats['added'])]
        incomplete = result.state == 'incomplete'
        if incomplete:
            text = '%s: %d %s' % (_("Incomplete"), len(result.failed), _("features could not be compared"))
            lines.append(text)
            self.app.inform.emit('[WARNING_NOTCL] %s' % text)
        else:
            self.app.inform.emit('[success] %s' % _("Compare done."))
        self._set_summary('\n'.join(lines))

        for kind in KINDS:
            for items, cost in packs[kind]:
                part = _Part(self._run_id, kind, items, cost)
                self._parts.append(part)
                self._queue.append(('submit', part))
        if self._queue:
            self._tick_timer.start()

    # ------------------------------------------------------------------------------------------------------------
    # the drawing (GUI thread): a vertex budget per tick; never a wait for a running batch
    # ------------------------------------------------------------------------------------------------------------
    def _set_want(self, kind, state):
        """A checkbox: the desired state of a kind. Installed collections of the kind follow; the others read it later."""
        self._want[kind] = bool(state)
        if self._loading:
            return
        self._queue = deque(op for op in self._queue if not (op[0] == 'vis' and op[1].kind == kind))
        for part in self._parts:
            if part.kind == kind and part.state == 'installed':
                self._queue.append(('vis', part))
        if self._queue:
            self._tick_timer.start()

    def _tick(self):
        used = 0
        while self._queue and used < CompareGen.B_TICK:
            op, part = self._queue.popleft()
            if part.run != self._run_id or part.state == 'dead':
                continue
            try:
                self._do(op, part)
            except Exception as e:
                log.error("ToolCompare._tick() --> %s\n%s" % (str(e), traceback.format_exc()))
            used += part.cost
        if not self._queue:
            self._tick_timer.stop()

    def _do(self, op, part):
        want = self._want[part.kind]
        if op == 'submit':
            coll = self.app.plotcanvas.new_shape_collection(layers=1)
            if not coll:
                # headless (NullCanvas): nothing to draw
                part.state, part.items = 'dead', None
                return
            part.coll = coll
            coll.add_batch(part.items, layer=0, tolerance=None, visible=want)
            part.items = None
            part.state = 'pending'
            self._pending.append(part)
            self._poll_timer.start()
        elif op == 'install' or part.state == 'installed':
            part.coll.update_visibility(want)
            part.coll.redraw()
            if want:
                colors = self._colors(part.kind)
                part.coll.redraw(update_colors=(colors['fill'], colors['outline']))
            part.state = 'installed'

    def _poll(self):
        """Only checks the batches (non-blocking) and queues the install; no draw work here."""
        for part in list(self._pending):
            if part.state == 'pending' and part.coll.pending_ready():
                self._pending.remove(part)
                part.state = 'ready'
                self._queue.append(('install', part))
                self._tick_timer.start()
        for coll in list(self._retired):
            if coll.pending_ready():
                self._retired.remove(coll)
                coll.clear()
        if not self._pending and not self._retired:
            self._poll_timer.stop()
