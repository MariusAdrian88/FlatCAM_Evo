"""Gerber editor transactions: aperture metadata and changed records, never retained board snapshots."""

from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from threading import get_ident
import math
import builtins

from shapely import Point
import gettext

from appHandlers.appHistory import HistoryManager, geometry_sizes, is_rigid
from appEditors.grb_plugins.GrbCommon import DrawToolShape, stored_quad_candidates
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def metadata(aperture):
    return {key: deepcopy(value) if isinstance(value, (dict, list)) else value
            for key, value in aperture.items() if key != 'geometry'}


def is_translation(matrix):
    """True if the affine matrix (a, b, d, e, xoff, yoff) only moves the geometry."""
    a, b, d, e = matrix[:4]
    return abs(a - 1.0) <= 1e-12 and abs(e - 1.0) <= 1e-12 and abs(b) <= 1e-12 and abs(d) <= 1e-12


def mark_flashes(editor, shapes):
    """Tells the history of the running operation that these records are flashes of their aperture (built with the
    aperture shape at their Point follow), so they save as flashes also in a new or a changed aperture."""
    history = getattr(editor, 'history', None)
    if isinstance(history, GerberHistory):
        history.flash_shapes.update(shape for shape in shapes if isinstance(shape.geo.get('follow'), Point))


def gerber_operation(label, all_apertures=False):
    def decorate(method):
        @wraps(method)
        def operation(self, *args, **kwargs):
            editor = self if hasattr(self, 'storage_dict') else getattr(self, 'draw_app', getattr(self, 'editor', None))
            history = getattr(editor, 'history', None)
            if not isinstance(history, GerberHistory):
                return method(self, *args, **kwargs)
            with history.operation(_(label)) as accepted:
                if not accepted:
                    return 'fail'
                history.scope(self, method.__name__, args, kwargs, all_apertures)
                result = method(self, *args, **kwargs)
                if result == 'fail':
                    history.failed = True
                return result
        return operation
    return decorate


class GerberHistory:
    def __init__(self, editor):
        self.editor = editor
        self.gui_thread = get_ident()
        self.session = 0
        self.depth = 0
        self.replaying = False
        self.pending = None
        self.undo_stack = []
        self.redo_stack = []
        self.baseline_evicted = False
        # records of the running operation that kept their native form (set again by every outer operation)
        self.native_shapes = set()
        self.translated_shapes = set()
        # records that the running operation built as a flash of their aperture (the solid is the aperture shape at
        # the Point follow): they save as flashes, also in a new or a changed aperture
        self.flash_shapes = set()
        # retained size of the stacks by step id, so a notify does not recompute the steps it already measured
        self.step_sizes = {}

    def notify(self):
        self.editor.history_changed.emit()

    def ready(self):
        if get_ident() != self.gui_thread or self.pending is not None:
            self.editor.app.inform.emit('[WARNING_NOTCL] %s' % _("Wait for the running editor operation to finish."))
            return False
        return True

    def reset(self):
        self.session += 1
        self.pending = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.step_sizes.clear()
        self.baseline_evicted = False
        self.notify()

    def state(self):
        editor = self.editor
        return {'selection': list(editor.selected), 'last': editor.last_aperture_selected,
                'modified': editor.is_modified, 'regions': bool(getattr(editor, 'loaded_as_regions', False)),
                'order': tuple(editor.storage_dict), 'aliases': dict(editor.oldapcode_newapcode)}

    def capture(self, apertures, mutable=()):
        if not self.depth or self.replaying:
            return
        for apid in apertures:
            if apid in self.registry and apid not in self.before_lists:
                self.before_lists[apid] = tuple(self.editor.storage_dict.get(apid, {}).get('geometry', ()))
                self.before_storages[apid] = self.editor.storage_dict[apid].get('geometry', [])
        for shape in mutable:
            self.before_geos.setdefault(shape, dict(shape.geo))

    def scope(self, owner, name, args, kwargs, all_apertures=False):
        editor = self.editor
        selected = set(editor.selected)
        if name == 'delete_shape':
            shape = kwargs.get('geo_el', args[0] if args else None)
            if shape is not None:
                selected.add(shape)
        if name == 'delete_marked_polygons':
            selected.update(editor.geo_to_delete)
        if name == 'on_key' and hasattr(owner, 'click_shapes'):
            selected.update(shape for shape, storage in owner.click_shapes)
        keys = [key for key, aperture in editor.storage_dict.items()
                if all_apertures or any(shape in selected for shape in aperture.get('geometry', ()))]
        if name == 'on_aperture_delete':
            codes = kwargs.get('ap_code', args[0] if args else None)
            if codes is not None:
                if isinstance(codes, (str, int)):
                    codes = [codes]
                for code in codes:
                    keys.extend(key for key in editor.storage_dict if str(key) == str(code))
            else:
                keys.extend(self.table_apertures())
        if name in ('on_tool_edit', 'on_scale'):
            keys.extend(self.table_apertures())
            if name == 'on_tool_edit':
                old_key = editor.tid2apcode.get(editor.ui.apertures_table.currentRow() + 1)
                if old_key in editor.storage_dict:
                    keys.append(old_key)
                selected.update(shape for key in keys for shape in editor.storage_dict[key].get('geometry', ()))
        if name in ('add_gerber_shape', 'on_grb_shape_complete'):
            offset = 1 if name == 'add_gerber_shape' else 0
            storage = kwargs.get('storage', args[offset] if len(args) > offset else None)
            keys.extend(key for key, aperture in editor.storage_dict.items() if aperture.get('geometry') is storage)
        self.capture(keys, selected)

    def table_apertures(self):
        table = self.editor.ui.apertures_table
        texts = {table.item(index.row(), 1).text() for index in table.selectionModel().selectedRows()
                 if table.item(index.row(), 1) is not None}
        return [key for key in self.editor.storage_dict if str(key) in texts]

    @contextmanager
    def operation(self, label):
        if self.replaying:
            yield True
            return
        if not self.ready():
            yield False
            return
        outer = self.depth == 0
        if outer:
            self.before = self.state()
            self.registry = {key: (aperture, metadata(aperture)) for key, aperture in self.editor.storage_dict.items()}
            self.before_lists = {}
            self.before_storages = {}
            self.before_geos = {}
            self.native_shapes = set()
            self.translated_shapes = set()
            self.flash_shapes = set()
            self.failed = False
            self.needs_plot = False
            self.dirty = False
        self.depth += 1
        try:
            self.scope(self.editor, '', (), {})
            yield True
        except Exception:
            self.failed = True
            raise
        finally:
            self.depth -= 1
            if outer:
                if self.failed:
                    changes = self.delta(label, validate_export=False)
                    if changes['apertures'] or self.before['regions'] != changes['after']['regions']:
                        self.rollback()
                    else:
                        self.restore_state(self.before)
                else:
                    step = self.delta(label)
                    if step['apertures'] or self.before['regions'] != step['after']['regions']:
                        self.editor.is_modified = True
                        if not self.dirty:
                            self.editor.touch_geometry()
                        step['after']['modified'] = True
                        self.undo_stack.append(step)
                        self.redo_stack.clear()
                        self.enforce_limits()
                        # a history refresh must not move the view, only a user selection change does
                        self.editor.update_ui(zoom=False)
                        # the revision changed: the grips of a selected shape must follow
                        self.editor.vertex_grips.sync()
                    else:
                        self.editor.is_modified = self.before['modified']
                    if self.needs_plot:
                        self.editor.plot_all()
                self.before_lists.clear()
                self.before_storages.clear()
                self.before_geos.clear()
                self.registry.clear()
                self.notify()

    def delta(self, label, validate_export=True):
        editor = self.editor
        deltas = {}
        keys = set(self.before_lists) | (set(self.registry) ^ set(editor.storage_dict))
        keys.update(key for key in self.registry if key in editor.storage_dict and
                    self.registry[key][1] != metadata(editor.storage_dict[key]))
        for key in keys:
            old_meta = self.registry[key][1] if key in self.registry else None
            new_meta = metadata(editor.storage_dict[key]) if key in editor.storage_dict else None
            before = self.before_lists.get(key, ())
            after = tuple(editor.storage_dict.get(key, {}).get('geometry', ()))
            old_ids, new_ids = {id(shape) for shape in before}, {id(shape) for shape in after}
            reordered = [id(shape) for shape in before if id(shape) in new_ids] != \
                [id(shape) for shape in after if id(shape) in old_ids]
            removed = [(index, shape, self.before_geos.get(shape, dict(shape.geo)))
                       for index, shape in enumerate(before) if reordered or id(shape) not in new_ids]
            inserted = [(index, shape, dict(shape.geo)) for index, shape in enumerate(after)
                        if reordered or id(shape) not in old_ids]
            changed = [(shape, self.before_geos[shape], dict(shape.geo)) for shape in after
                       if id(shape) in old_ids and shape in self.before_geos and self.before_geos[shape] != shape.geo]
            if removed or inserted or changed or old_meta != new_meta:
                deltas[key] = (old_meta, new_meta, removed, inserted, changed)
        # Geometries no longer described by their aperture must use the existing composed-region save path.
        for key, (old_meta, new_meta, removed, inserted, changed) in deltas.items():
            if not validate_export:
                break
            if getattr(editor, 'loaded_as_regions', False):
                break
            if new_meta is None or new_meta.get('type') == 'REG':
                continue
            records = [(shape, geo) for index, shape, geo in inserted] + [(shape, after) for shape, before, after in changed]
            for shape, geo in records:
                if getattr(editor, 'loaded_as_regions', False):
                    break
                follow, solid = geo.get('follow'), geo.get('solid')
                if solid is None:
                    continue
                if solid.is_empty:
                    if follow is not None and not follow.is_empty:
                        editor.loaded_as_regions = True
                    continue
                if follow is None:
                    editor.loaded_as_regions = True
                    break
                # a pure translation of a record keeps its flash or track semantics, whatever the aperture type
                if shape in self.translated_shapes and old_meta == new_meta:
                    continue
                if shape in self.flash_shapes and isinstance(follow, Point):
                    continue
                if new_meta.get('type') != 'C':
                    editor.loaded_as_regions = True
                    continue
                if shape in self.native_shapes and old_meta == new_meta:
                    continue
                try:
                    width = float(new_meta.get('size', 0))
                except (ValueError, TypeError):
                    width = 0
                if not math.isfinite(width) or width <= 0:
                    editor.loaded_as_regions = True
                    continue
                # Compare spatial footprints, not polygon vertex ordering or circle tessellation count.
                errors = [solid.hausdorff_distance(follow.buffer(width / divisor, quad))
                          for divisor in (2, 1.999) for quad in stored_quad_candidates(editor, width / divisor)]
                if min(errors) > max(width * 0.005, 1e-8):
                    editor.loaded_as_regions = True
        return {'label': label, 'apertures': deltas, 'before': self.before, 'after': self.state()}

    def rollback(self):
        editor = self.editor
        for key in list(editor.storage_dict):
            if key not in self.registry:
                del editor.storage_dict[key]
        for key, shapes in self.before_lists.items():
            aperture, meta = self.registry[key]
            storage = self.before_storages[key]
            storage[:] = shapes
            aperture.clear()
            aperture.update(meta)
            aperture['geometry'] = storage
            editor.storage_dict[key] = aperture
        for key, (aperture, meta) in self.registry.items():
            if key not in self.before_lists:
                geometry = aperture.get('geometry', [])
                aperture.clear()
                aperture.update(meta)
                aperture['geometry'] = geometry
                editor.storage_dict[key] = aperture
        for shape, geo in self.before_geos.items():
            shape.geo = dict(geo)
        self.restore_state(self.before)
        editor.touch_geometry()
        self.refresh()

    def restore_state(self, state):
        editor = self.editor
        editor.selected = list(state['selection'])
        editor.last_aperture_selected = state['last']
        editor.is_modified = state['modified']
        editor.loaded_as_regions = state['regions']
        editor.oldapcode_newapcode = dict(state['aliases'])
        ordered = {key: editor.storage_dict[key] for key in state['order'] if key in editor.storage_dict}
        ordered.update(editor.storage_dict)
        editor.storage_dict.clear()
        editor.storage_dict.update(ordered)
        editor.current_storage = editor.storage_dict.get(state['last'], {}).get('geometry', [])

    def refresh(self):
        editor = self.editor
        editor._pick_cache = None
        editor._render_state = None
        editor.build_ui()
        selected = set(editor.selected)
        editor.sync_aperture_rows(key for key, aperture in editor.storage_dict.items()
                                  if any(shape in selected for shape in aperture.get('geometry', ())))
        editor.update_ui(zoom=False)
        editor.plot_all()

    def replay(self, redo=False):
        if not self.ready() or self.depth:
            return False
        self.enforce_limits()
        source, target = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not source:
            return False
        step = source[-1]
        editor = self.editor
        self.replaying = True
        try:
            tool = editor.active_tool
            if tool is not None and hasattr(tool, 'clean_up'):
                tool.clean_up()
            editor.utility.clear()
            editor.tool_shape.clear(update=False)
            editor.in_action = False
            for key, (before_meta, after_meta, removed, inserted, changed) in step['apertures'].items():
                meta = after_meta if redo else before_meta
                if meta is None:
                    editor.storage_dict.pop(key, None)
                    continue
                aperture = editor.storage_dict.setdefault(key, {'geometry': []})
                shapes = aperture.get('geometry', [])
                discard, restore = (removed, inserted) if redo else (inserted, removed)
                ids = {id(shape) for index, shape, geo in discard}
                shapes[:] = [shape for shape in shapes if id(shape) not in ids]
                for index, shape, geo in restore:
                    shape.geo = dict(geo)
                    shapes.insert(index, shape)
                for shape, before, after in changed:
                    shape.geo = dict(after if redo else before)
                aperture.clear()
                aperture.update(meta)
                aperture['geometry'] = shapes
            self.restore_state(step['after'] if redo else step['before'])
            editor.touch_geometry()
            editor.select_tool('select')
            target.append(source.pop())
        finally:
            self.replaying = False
        self.refresh()
        self.notify()
        return True

    def retained_shapes(self):
        shapes = set()
        for step in self.undo_stack + self.redo_stack:
            for before_meta, after_meta, removed, inserted, changed in step['apertures'].values():
                shapes.update(shape for index, shape, geo in removed + inserted)
                shapes.update(shape for shape, before, after in changed)
        return shapes

    def enforce_limits(self):
        max_steps, max_bytes = HistoryManager(self.editor.app, None).limits()
        if len(self.undo_stack) > max_steps:
            self.baseline_evicted = True
            del self.undo_stack[:-max_steps]
        while len(self.undo_stack) + len(self.redo_stack) > max_steps:
            if self.undo_stack:
                self.undo_stack.pop(0)
                self.baseline_evicted = True
            else:
                self.redo_stack.pop(0)

        def size():
            # the sizes of a step never change once it is recorded: measure each step only once
            steps = self.undo_stack + self.redo_stack
            self.step_sizes = {key: entry for key, entry in self.step_sizes.items()
                               if any(entry[0] is step for step in steps)}
            merged = {}
            for step in steps:
                entry = self.step_sizes.get(id(step))
                if entry is None or entry[0] is not step:
                    geos = []
                    for before_meta, after_meta, removed, inserted, changed in step['apertures'].values():
                        geos.extend(geo for index, shape, geo in removed + inserted)
                        geos.extend(geo for shape, before, after in changed for geo in (before, after))
                    entry = self.step_sizes[id(step)] = (step, geometry_sizes(geos))
                # a geometry that more steps share counts one time, as state_size() does
                merged.update(entry[1])
            return sum(merged.values())

        while len(self.undo_stack) + len(self.redo_stack) > 1 and size() > max_bytes:
            if self.redo_stack:
                self.redo_stack.pop(0)
            else:
                self.undo_stack.pop(0)
                self.baseline_evicted = True

    def reserve(self):
        if not self.ready() or self.depth:
            return None
        token = object()
        self.pending = (token, self.session, self.editor.geometry_revision)
        self.notify()
        return self.pending

    def submit(self, label, compute, matrix=None, shapes=None, success=None, on_commit=None):
        shapes = list(dict.fromkeys(self.editor.selected if shapes is None else shapes))
        if not shapes:
            self.editor.app.inform.emit('[WARNING_NOTCL] %s' % _("No shape selected."))
            return 'fail'
        # An existing transaction must stay atomic; only standalone actions go to the worker queue.
        if self.depth:
            with self.operation(label) as accepted:
                if not accepted:
                    return 'fail'
                selected = set(shapes)
                keys = [key for key, aperture in self.editor.storage_dict.items()
                        if any(shape in selected for shape in aperture.get('geometry', ()))]
                self.capture(keys, shapes)
                compute(shapes)
                if matrix is not None and is_rigid(matrix):
                    self.native_shapes.update(shapes)
                    if is_translation(matrix):
                        self.translated_shapes.update(shapes)
                self.editor.plot_transformed(shapes, matrix)
            return
        pending = self.reserve()
        if pending is None:
            return 'fail'
        clones = [DrawToolShape(dict(shape.geo)) for shape in shapes]

        def work():
            result = {'pending': pending, 'shapes': shapes, 'label': label, 'matrix': matrix,
                      'success': success, 'on_commit': on_commit}
            try:
                compute(clones)
                result['geometry'] = [dict(shape.geo) for shape in clones]
            except Exception as err:
                result['error'] = str(err)
            self.editor.geometry_result.emit(result)

        try:
            self.editor.app.worker_task.emit({'fcn': work, 'params': []})
        except Exception:
            self.pending = None
            self.notify()
            raise
        return pending

    def apply_result(self, result):
        if self.pending is not result['pending']:
            return
        pending = self.pending
        self.pending = None
        try:
            if pending[1:] != (self.session, self.editor.geometry_revision):
                self.editor.app.inform.emit('[WARNING_NOTCL] %s' % _("Geometry changed. Retry the operation."))
                return
            if 'error' in result:
                self.editor.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Action was not executed"), result['error']))
                return
            with self.operation(result['label']) as accepted:
                if accepted:
                    shapes = result['shapes']
                    selected = set(shapes)
                    keys = [key for key, aperture in self.editor.storage_dict.items()
                            if any(shape in selected for shape in aperture.get('geometry', ()))]
                    self.capture(keys, shapes)
                    if result['matrix'] is not None and is_rigid(result['matrix']):
                        self.native_shapes.update(shapes)
                        if is_translation(result['matrix']):
                            self.translated_shapes.update(shapes)
                    for shape, geo in zip(shapes, result['geometry']):
                        shape.geo = geo
                    self.editor.plot_transformed(shapes, result['matrix'])
            if accepted and result.get('success') and self.editor.geometry_revision != pending[2]:
                self.editor.app.inform.emit('[success] %s' % result['success'])
            if accepted and result.get('on_commit'):
                result['on_commit']()
        finally:
            self.notify()
