# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 10/21/2026                                         #
# MIT Licence                                              #
# ##########################################################

"""
The Undo / Redo history of a project, outside the editors.

A step does not keep object references: it keeps the history_uid of the objects, so an object that Undo rebuilds
(after a delete) is the same object for the other steps. See docs/superpowers/specs/2026-10-21-app-history-design.md.
"""

import logging
import math
import sys
import threading
from contextlib import contextmanager

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from appCommon.Common import LoudDict
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import (Gerber, update_scale_metadata, update_rotate_metadata,
                                    update_skew_metadata)
from camlib import Geometry, deepcopy_keep_geometry

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

DEFAULT_STEPS = 100
DEFAULT_MEMORY_MB = 256
# the history records nothing in headless mode; the tests of a headless App set this True
RECORD_IN_HEADLESS = False
# bytes for 1 coordinate pair of a Shapely geometry
COORD_BYTES = 16


class HistoryError(Exception):
    """A step can not run (for example, its object does not exist anymore). str(e) is a user message."""


# ##################################################################################################################
# Helpers
# ##################################################################################################################

def invert_matrix(matrix):
    """The inverse of the shapely affine 6-tuple (a, b, d, e, xoff, yoff)."""
    if len(matrix) != 6 or not np.isfinite(matrix).all():
        raise HistoryError(_("The transformation can not be undone."))
    a, b, d, e, xoff, yoff = matrix
    det = a * e - b * d
    if abs(det) <= 1e-12:
        raise HistoryError(_("The transformation can not be undone."))
    ia, ib, id_, ie = e / det, -b / det, -d / det, a / det
    return ia, ib, id_, ie, -(ia * xoff + ib * yoff), -(id_ * xoff + ie * yoff)


def is_rigid(matrix):
    a, b, d, e = matrix[:4]
    return abs(a * a + d * d - 1.0) <= 1e-9 and abs(b * b + e * e - 1.0) <= 1e-9 and abs(a * b + d * e) <= 1e-9


def apply_matrix_to_object(obj, matrix):
    """
    Apply ONE affine matrix (a, b, d, e, xoff, yoff) to the geometry of a Gerber, Excellon or Geometry object,
    through the vectorized paths of the transforms. Updates the bounds in obj_options.
    """

    kind = obj.kind.lower()
    if kind == 'gerber':
        Gerber._affine_all(obj, matrix)
        a, b, d, e = matrix[:4]
        # Reuse the same aperture rules as the ordinary Gerber transforms.
        if abs(b) <= 1e-12 and abs(d) <= 1e-12:
            update_scale_metadata(obj, a, e)
        elif abs(a * b + d * e) <= 1e-12:
            sx = math.hypot(a, d)
            sy = (a * e - b * d) / sx
            update_scale_metadata(obj, sx, sy)
            update_rotate_metadata(obj, math.degrees(math.atan2(d, a)))
        else:
            update_skew_metadata(obj)
        obj._after_transform()
    elif kind == 'excellon':
        a, b, d, e = matrix[:4]
        moved_only = abs(a - 1.0) <= 1e-12 and abs(e - 1.0) <= 1e-12 and abs(b) <= 1e-12 and abs(d) <= 1e-12
        # a translation moves the hole shapes too (as offset does); every other matrix moves the centers and the
        # holes are made again (as rotate, scale and skew do): the circle segments stay aligned to the axes and the
        # tool diameters stay
        Excellon._transform_tools(obj, matrix, transform_solid=moved_only)
        if not moved_only:
            obj.create_geometry()
    elif kind == 'geometry':
        Geometry._affine_solid_geometry(obj, matrix, any_iterable=True)
    else:
        raise HistoryError('%s: %s' % (_("Object type not supported"), obj.kind))

    xmin, ymin, xmax, ymax = obj.bounds()
    obj.obj_options['xmin'] = xmin
    obj.obj_options['ymin'] = ymin
    obj.obj_options['xmax'] = xmax
    obj.obj_options['ymax'] = ymax


def supports_matrix(obj):
    return str(getattr(obj, 'kind', '')).lower() in ('gerber', 'excellon', 'geometry')


TRANSFORM_OPTIONS = ('rotate', 'mirror_x', 'mirror_y', 'scale_x', 'scale_y', 'skew_x', 'skew_y',
                     'offset_x', 'offset_y')


def transform_metadata(obj):
    """Small exact nongeometry state alongside a matrix, including original key presence."""
    state = {'options': {k: obj.obj_options[k] for k in TRANSFORM_OPTIONS if k in obj.obj_options}}
    if str(obj.kind).lower() == 'gerber':
        state['tools'] = {key: {k: v for k, v in tool.items() if k != 'geometry'}
                          for key, tool in obj.tools.items()}
        state['attrs'] = {k: getattr(obj, k) for k in ('export_as_regions',) if hasattr(obj, k)}
    return deepcopy_keep_geometry(state)


def restore_transform_metadata(obj, state):
    state = deepcopy_keep_geometry(state)
    for key in TRANSFORM_OPTIONS:
        obj.obj_options.pop(key, None)
    dict.update(obj.obj_options, state['options'])
    if 'tools' in state:
        for key, metadata in state['tools'].items():
            tool = obj.tools[key]
            geometry = tool.get('geometry')
            has_geometry = 'geometry' in tool
            tool.clear()
            tool.update(metadata)
            if has_geometry:
                tool['geometry'] = geometry
        for key in ('export_as_regions',):
            if key in state['attrs']:
                setattr(obj, key, state['attrs'][key])
            elif hasattr(obj, key):
                delattr(obj, key)


# attributes that are not in ser_attrs (the project file does not keep them) but that an Undo step must keep
HISTORY_EXTRA_ATTRS = {
    # the G-code text: the project load makes it again, but the snippets change it in place
    'cncjob': ('source_file',),
}
EXTRA_KEY = '_history_extra'


def object_state(obj):
    """
    The state of an object as project save makes it (the ser_attrs), but the dicts and lists are copied and the
    Shapely geometries are shared (they are immutable): a later change of the object does not change the state.
    The attributes of HISTORY_EXTRA_ATTRS go under EXTRA_KEY; from_dict() does not read them.
    """

    # a LoudDict holds its change callback, a bound method of the object: deepcopy would copy the whole object
    values = {attr: getattr(obj, attr) for attr in obj.ser_attrs if hasattr(obj, attr)}
    state = deepcopy_keep_geometry({k: dict(v) if isinstance(v, LoudDict) else v for k, v in values.items()})
    state['kind'] = obj.kind
    state['history_uid'] = obj.history_uid
    extra = HISTORY_EXTRA_ATTRS.get(str(obj.kind).lower(), ())
    if extra:
        state[EXTRA_KEY] = {attr: getattr(obj, attr) for attr in extra if hasattr(obj, attr)}
    return state


def restore_extra(obj, state):
    """Set the attributes of HISTORY_EXTRA_ATTRS that the state keeps."""
    for attr, value in state.get(EXTRA_KEY, {}).items():
        setattr(obj, attr, value)


def restore_object_state(obj, state):
    """Restore atomically with respect to option callbacks; callers refresh the UI afterwards."""

    options = obj.obj_options
    callback = getattr(options, 'callback', None)
    if isinstance(options, LoudDict):
        options.set_change_callback(lambda key: None)
    try:
        restored = deepcopy_keep_geometry(state)
        obj.from_dict(restored)
        options.clear()
        dict.update(options, restored.get('obj_options', restored.get('options', {})))
        obj.obj_options = options
        restore_extra(obj, restored)
        obj.history_uid = state['history_uid']
    finally:
        if isinstance(options, LoudDict):
            options.set_change_callback(callback)


def _geometries(item, out):
    stack = [item]
    seen = set()
    while stack:
        cur = stack.pop()
        if isinstance(cur, BaseGeometry):
            out.append(cur)
        elif (isinstance(cur, (dict, list, tuple)) or
              isinstance(cur, np.ndarray) and cur.dtype.hasobject) and id(cur) not in seen:
            seen.add(id(cur))
            stack.extend(cur.values() if isinstance(cur, dict) else cur.flat if isinstance(cur, np.ndarray) else cur)
    return out


def geometry_sizes(*states):
    """
    {id(geometry): bytes} for the geometries in the states, each geometry 1 time. The states keep a reference to
    each geometry, so the ids stay valid while the step exists. A geometry that more states share (the states of
    1 object before and after a parameter edit) is counted 1 time.
    """
    geoms = []
    for state in states:
        _geometries(state, geoms)
    unique = list({id(g): g for g in geoms}.values())
    if not unique:
        return {}
    sizes = shapely.get_num_coordinates(np.asarray(unique, dtype=object)) * COORD_BYTES
    return {id(g): int(n) for g, n in zip(unique, sizes)}


def state_size(*states):
    """Estimated bytes that the states keep alive: the coordinates of their geometries, each geometry 1 time."""
    return sum(geometry_sizes(*states).values())


def states_equal(left, right):
    """Exact, conservative snapshot equality. Unknown equality must retain the edit."""
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    try:
        if isinstance(left, dict):
            return left.keys() == right.keys() and all(states_equal(left[k], right[k]) for k in left)
        if isinstance(left, (list, tuple)):
            return len(left) == len(right) and all(states_equal(a, b) for a, b in zip(left, right))
        if isinstance(left, np.ndarray):
            return left.dtype == right.dtype and left.shape == right.shape and bool(np.array_equal(left, right))
        if isinstance(left, BaseGeometry):
            return left.wkb == right.wkb
        result = left == right
        return isinstance(result, (bool, np.bool_)) and bool(result)
    except Exception:
        return False


def retained_sizes(*states):
    """Estimated retained storage, identity-deduplicated; not an RSS measurement."""
    sizes = geometry_sizes(*states)
    stack = list(states)
    seen = set()
    while stack:
        value = stack.pop()
        key = id(value)
        if key in seen or isinstance(value, BaseGeometry):
            continue
        seen.add(key)
        sizes[('storage', key)] = sys.getsizeof(value)
        if isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, (list, tuple, set, frozenset)):
            stack.extend(value)
        elif isinstance(value, np.ndarray):
            if value.base is not None:
                stack.append(value.base)
            if value.dtype.hasobject:
                stack.extend(value.flat)
    return sizes


# ##################################################################################################################
# Steps
# ##################################################################################################################

class HistoryStep:
    """One undoable action. undo() / redo() raise HistoryError if they can not run."""

    label = ''
    size = 0

    def uids(self):
        return set()

    def geometry_sizes(self):
        """{id(geometry): bytes} of the geometries that the step keeps, or None: the step counts as self.size."""
        return None

    def undo(self, history):
        raise NotImplementedError

    def redo(self, history):
        raise NotImplementedError


class MatrixStep(HistoryStep):
    """An affine matrix applied to objects. Undo applies the inverse matrices in reverse order."""

    def __init__(self, label, items, metadata=None):
        self.label = label
        self.items = [(uid, tuple(float(v) for v in m)) for uid, m in items]
        self.metadata = metadata or {}

    def geometry_sizes(self):
        return retained_sizes(self.metadata, self.items, self.label)

    def uids(self):
        return {uid for uid, _m in self.items}

    def _run(self, history, inverse):
        items = list(reversed(self.items)) if inverse else self.items
        objs = [history.find_or_fail(uid) for uid, _m in items]
        for obj, (_uid, matrix) in zip(objs, items):
            m = invert_matrix(matrix) if inverse else matrix
            apply_matrix_to_object(obj, m)
            if _uid in self.metadata:
                restore_transform_metadata(obj, self.metadata[_uid][0 if inverse else 1])
            history.after_change(obj, m)

    def undo(self, history):
        self._run(history, True)

    def redo(self, history):
        self._run(history, False)


class SnapshotStep(HistoryStep):
    """The state of objects before and after an action that is not affine."""

    def __init__(self, label, items, merge_key=None):
        self.label = label
        self.items = items      # [(uid, before, after)]
        # consecutive snapshots with the same key (for example the edits of 1 field) become 1 step
        self.merge_key = merge_key
        self._geoms = retained_sizes(*[s for _uid, before, after in items for s in (before, after)])
        self.size = sum(self._geoms.values())

    def geometry_sizes(self):
        return self._geoms

    def merge(self, newer):
        """Join a newer snapshot of the same objects: keep this 'before', take the 'after' of the newer one."""
        if (self.merge_key is None or newer.merge_key != self.merge_key or
                [uid for uid, _b, _a in self.items] != [uid for uid, _b, _a in newer.items]):
            return False
        self.items = [(uid, before, after) for (uid, before, _old), (_uid, _b, after) in zip(self.items, newer.items)]
        self._geoms = retained_sizes(*[s for _uid, before, after in self.items for s in (before, after)])
        self.size = sum(self._geoms.values())
        return True

    def uids(self):
        return {uid for uid, _b, _a in self.items}

    def _run(self, history, use_before):
        objs = [history.find_or_fail(uid) for uid, _b, _a in self.items]
        for obj, (_uid, before, after) in zip(objs, self.items):
            history.restore_state(obj, before if use_before else after)

    def undo(self, history):
        self._run(history, True)

    def redo(self, history):
        self._run(history, False)


class CreateStep(HistoryStep):
    """New objects. Undo deletes them and keeps their state; Redo rebuilds them with the same history_uid."""

    def __init__(self, label, uids):
        self.label = label
        self._uids = list(uids)
        self.states = {}
        self._geoms = {}

    def uids(self):
        return set(self._uids)

    def geometry_sizes(self):
        return self._geoms

    def undo(self, history):
        objs = [history.find_or_fail(uid) for uid in self._uids]
        self.states = {obj.history_uid: object_state(obj) for obj in objs}
        self._geoms = retained_sizes(*self.states.values())
        self.size = sum(self._geoms.values())
        history.delete_objects(objs)

    def redo(self, history):
        for uid in self._uids:
            history.rebuild(self.states[uid])
        self.states = {}
        self._geoms = {}
        self.size = 0


class DeleteStep(HistoryStep):
    """Deleted objects. Undo rebuilds them with the same history_uid; Redo deletes them again."""

    def __init__(self, label, states):
        self.label = label
        self.states = list(states)
        self._geoms = retained_sizes(*self.states)
        self.size = sum(self._geoms.values())

    def uids(self):
        return {s['history_uid'] for s in self.states}

    def geometry_sizes(self):
        return self._geoms

    def undo(self, history):
        for state in self.states:
            history.rebuild(state)

    def redo(self, history):
        history.delete_objects([history.find_or_fail(s['history_uid']) for s in self.states])


class RenameStep(HistoryStep):
    def __init__(self, label, items):
        self.label = label
        self.items = items      # [(uid, old, new)]

    def uids(self):
        return {uid for uid, _o, _n in self.items}

    def geometry_sizes(self):
        return retained_sizes(self.items, self.label)

    def undo(self, history):
        for uid, old, _new in self.items:
            history.rename(history.find_or_fail(uid), old)

    def redo(self, history):
        for uid, _old, new in self.items:
            history.rename(history.find_or_fail(uid), new)


class GroupStep(HistoryStep):
    """More steps as 1 history entry."""

    def __init__(self, label, steps):
        self.label = label
        self.steps = list(steps)

    @property
    def size(self):
        return sum(s.size for s in self.steps)

    def geometry_sizes(self):
        out = {}
        for s in self.steps:
            sizes = s.geometry_sizes()
            if sizes is None:
                if s.size:
                    out[('step', id(s))] = s.size
            else:
                out.update(sizes)
        return out

    def uids(self):
        out = set()
        for s in self.steps:
            out |= s.uids()
        return out

    def undo(self, history):
        for s in reversed(self.steps):
            s.undo(history)

    def redo(self, history):
        for s in self.steps:
            s.redo(history)


# ##################################################################################################################
# Manager
# ##################################################################################################################

class HistoryManager:
    """
    The Undo / Redo stacks of 1 project. push() is thread safe; undo() / redo() run on the GUI thread.
    app.history is the manager of the active project; only that one emits app.history_changed.
    """

    def __init__(self, app, collection):
        self.app = app
        self.collection = collection
        self.undo_stack = []
        self.redo_stack = []
        self._lock = threading.RLock()
        self._suspend = 0
        # [label, steps, depth] while a group() is open
        self._group = None

    # ---------------------------------------------------------------------------------------------------------
    # Limits and state
    # ---------------------------------------------------------------------------------------------------------
    def _option(self, key, default):
        try:
            return int(self.app.options.get(key, default))
        except (AttributeError, TypeError, ValueError):
            return default

    def limits(self):
        steps = max(1, self._option('global_history_steps', DEFAULT_STEPS))
        mem = max(1, self._option('global_history_memory_mb', DEFAULT_MEMORY_MB)) * 1024 * 1024
        return steps, mem

    def _headless(self):
        return getattr(self.app, 'headless', False) and not RECORD_IN_HEADLESS

    def is_recording(self):
        return not self._headless() and self._suspend == 0

    def can_undo(self):
        return bool(self.undo_stack)

    def can_redo(self):
        return bool(self.redo_stack)

    def undo_label(self):
        return self.undo_stack[-1].label if self.undo_stack else ''

    def redo_label(self):
        return self.redo_stack[-1].label if self.redo_stack else ''

    def total_size(self, exclude=frozenset()):
        """
        Estimated retained coordinates and nongeometry payload, identity-deduplicated over both stacks.
        exclude: ids of geometries that do not count (the project still uses them). This is not an RSS cap.
        """
        seen = {}
        extra = 0
        for step in self.undo_stack + self.redo_stack:
            sizes = step.geometry_sizes()
            if sizes is None:
                extra += step.size
            else:
                seen.update(sizes)
        return sum(n for key, n in seen.items() if key not in exclude) + extra

    def _live_geometry_ids(self):
        """The ids of the geometries that the objects of the project use now: the history does not add them."""
        geoms = []
        try:
            for obj in self.collection.get_list():
                for attr in getattr(obj, 'ser_attrs', ()):
                    _geometries(getattr(obj, attr, None), geoms)
        except Exception as e:
            log.debug("HistoryManager._live_geometry_ids() --> %s" % str(e))
        return {id(g) for g in geoms}

    def _enforce(self, protect=None):
        max_steps, max_bytes = self.limits()
        del self.undo_stack[:-max_steps]
        if self.total_size() <= max_bytes:
            return
        # only above the limit: the walk of the live objects costs time on a big project
        live = self._live_geometry_ids()
        if protect is None:
            protect = self.undo_stack[-1] if self.undo_stack else self.redo_stack[-1] if self.redo_stack else None
        while self.redo_stack and self.total_size(live) > max_bytes:
            # the redo step that is farthest away goes first
            candidate = next((s for s in self.redo_stack if s is not protect), None)
            if candidate is None:
                break
            self.redo_stack.remove(candidate)
        while self.undo_stack and self.total_size(live) > max_bytes:
            candidate = next((s for s in self.undo_stack if s is not protect), None)
            if candidate is None:
                break
            self.undo_stack.remove(candidate)
        # Keep the newest action even when oversized: an estimated budget, not an RSS cap.

    def notify(self):
        if getattr(self.app, 'history', None) is not self:
            return
        try:
            self.app.history_changed.emit(self.undo_label(), self.redo_label())
        except Exception as e:
            log.debug("HistoryManager.notify() --> %s" % str(e))

    # ---------------------------------------------------------------------------------------------------------
    # Record
    # ---------------------------------------------------------------------------------------------------------
    def push(self, step):
        if step is None or not self.is_recording():
            return
        with self._lock:
            if self._group is not None:
                self._group[1].append(step)
                return
            top = self.undo_stack[-1] if self.undo_stack and not self.redo_stack else None
            if not (isinstance(top, SnapshotStep) and isinstance(step, SnapshotStep) and top.merge(step)):
                self.undo_stack.append(step)
            self.redo_stack.clear()
            self._enforce()
        self.notify()

    @contextmanager
    def group(self, label):
        """The steps pushed inside become 1 GroupStep. Nested groups join the outer one."""
        with self._lock:
            outer = self._group is None
            if outer:
                self._group = [label, [], 0]
            self._group[2] += 1
        try:
            yield
        finally:
            with self._lock:
                self._group[2] -= 1
                done = self._group if self._group[2] == 0 else None
                if done is not None:
                    self._group = None
            if done is not None and done[1]:
                self.push(done[1][0] if len(done[1]) == 1 and done[1][0].label == done[0]
                          else GroupStep(done[0], done[1]))

    @contextmanager
    def suspended(self):
        """push() does nothing inside (project load, the rebuild of an object, a Tcl command)."""
        with self._lock:
            self._suspend += 1
        try:
            yield
        finally:
            with self._lock:
                self._suspend -= 1

    def record_matrix(self, label, pairs):
        """pairs: [(obj, matrix, before_metadata)] after applying; legacy two-tuples are accepted."""
        items, metadata = [], {}
        for pair in pairs:
            obj, matrix = pair[:2]
            if not supports_matrix(obj):
                continue
            items.append((obj.history_uid, matrix))
            if len(pair) == 3:
                metadata[obj.history_uid] = (pair[2], transform_metadata(obj))
        if items:
            self.push(MatrixStep(label, items, metadata))

    @contextmanager
    def snapshot(self, label, objects, merge_key=None):
        """
        The state of the objects before the body and after it, as 1 SnapshotStep. merge_key: consecutive snapshots
        with the same key and the same objects become 1 step (the edits of 1 field, typed or spun in small changes).
        """
        if not self.is_recording():
            yield
            return
        objects = list(objects)
        before = [object_state(o) for o in objects]
        yield
        items = [(o.history_uid, b, object_state(o)) for o, b in zip(objects, before)]
        items = [(uid, b, a) for uid, b, a in items if not states_equal(b, a)]
        if items:
            self.push(SnapshotStep(label, items, merge_key=merge_key))

    def record_create(self, label, objects):
        uids = [o.history_uid for o in objects]
        if uids:
            self.push(CreateStep(label, uids))

    def record_delete(self, label, objects):
        """Call it BEFORE the objects are deleted."""
        if not self.is_recording():
            return
        states = [object_state(o) for o in objects]
        if states:
            self.push(DeleteStep(label, states))

    def record_rename(self, obj, old, new):
        if old != new:
            self.push(RenameStep('%s: %s' % (_("Rename"), new), [(obj.history_uid, old, new)]))

    # ---------------------------------------------------------------------------------------------------------
    # Undo / Redo
    # ---------------------------------------------------------------------------------------------------------
    def blocked_reason(self):
        if self._headless():
            return _("Undo / Redo is not available in headless mode.")
        if getattr(self.app, 'call_source', 'app') != 'app':
            return _("Close the editor first. The editor has its own Undo / Redo.")
        projects = getattr(self.app, 'projects', None)
        if projects is not None and projects.is_busy():
            return _("Wait for the running task to finish.")
        return None

    def undo(self):
        return self._run(self.undo_stack, self.redo_stack, True)

    def redo(self):
        return self._run(self.redo_stack, self.undo_stack, False)

    def _run(self, source, target, undo):
        reason = self.blocked_reason()
        if reason:
            self.app.inform.emit('[WARNING_NOTCL] %s' % reason)
            return False
        with self._lock:
            if not source:
                return False
            step = source[-1]

        name = _("Undo") if undo else _("Redo")
        checkpoint = None
        bookkeeping = []
        saved = getattr(self.app, 'should_we_save', False)
        try:
            self._prevalidate(step, undo)
            checkpoint = {uid: object_state(obj) if (obj := self.find(uid)) is not None else None
                          for uid in step.uids()}
            for child in self._steps(step):
                if isinstance(child, CreateStep):
                    bookkeeping.append((child, child.states, child._geoms, child.size))
            with self.suspended():
                step.undo(self) if undo else step.redo(self)
        except Exception as e:
            log.error("HistoryManager._run() %s --> %s" % (step.label, str(e)))
            if checkpoint is not None:
                try:
                    with self.suspended():
                        self._rollback(checkpoint)
                    for child, states, sizes, size in bookkeeping:
                        child.states, child._geoms, child.size = states, sizes, size
                    self.app.should_we_save = saved
                except Exception as recovery:
                    log.error("History rollback incomplete --> %s" % str(recovery))
                    self.clear()
                    self.app.should_we_save = True
                    self.app.inform.emit('[ERROR_NOTCL] %s: %s' %
                                         (_("History recovery incomplete; Undo / Redo cleared"), str(recovery)))
            self.app.inform.emit('[ERROR_NOTCL] %s %s: %s' % (name, step.label, str(e)))
            self.notify()
            return False

        with self._lock:
            source.pop()
            target.append(step)
            self._enforce(protect=step)
        self.app.should_we_save = True
        self.notify()
        self.app.inform.emit('[success] %s: %s' % (name, step.label))
        return True

    def _steps(self, step):
        if isinstance(step, GroupStep):
            for child in step.steps:
                yield from self._steps(child)
        else:
            yield step

    def _prevalidate(self, step, undo):
        """Simulate membership and names before the first mutation, including grouped operations."""
        names = {o.history_uid: o.obj_options.get('name') for o in self.collection.get_list()}

        def run(child):
            if isinstance(child, GroupStep):
                for item in reversed(child.steps) if undo else child.steps:
                    run(item)
                return
            rebuild = isinstance(child, DeleteStep) and undo or isinstance(child, CreateStep) and not undo
            if rebuild:
                states = child.states if isinstance(child, DeleteStep) else [child.states[u] for u in child._uids]
                for state in states:
                    uid = state['history_uid']
                    name = state.get('obj_options', state.get('options', {})).get('name', state['kind'])
                    if uid in names or name in names.values():
                        raise HistoryError(_("Another object has this name") + ': ' + str(name))
                    names[uid] = name
            else:
                if not child.uids().issubset(names):
                    raise HistoryError(_("The object of this step does not exist anymore."))
                if isinstance(child, MatrixStep):
                    for _uid, matrix in child.items:
                        invert_matrix(matrix)
                elif isinstance(child, (CreateStep, DeleteStep)):
                    for uid in child.uids():
                        del names[uid]
                elif isinstance(child, RenameStep):
                    for uid, old, new in child.items:
                        name = old if undo else new
                        if any(other != uid and value == name for other, value in names.items()):
                            raise HistoryError(_("Another object has this name") + ': ' + name)
                        names[uid] = name
                elif isinstance(child, SnapshotStep):
                    for uid, before, after in child.items:
                        state = before if undo else after
                        name = state.get('obj_options', state.get('options', {})).get('name', names[uid])
                        if any(other != uid and value == name for other, value in names.items()):
                            raise HistoryError(_("Another object has this name") + ': ' + str(name))
                        names[uid] = name
        run(step)

    def _rollback(self, checkpoint):
        for uid, state in checkpoint.items():
            obj = self.find(uid)
            if state is None and obj is not None:
                self.delete_objects([obj])
        # Release changed names first so a partially replayed rename cannot obstruct recovery.
        for uid, state in checkpoint.items():
            obj = self.find(uid)
            if state is not None and obj is not None:
                name = state.get('obj_options', state.get('options', {})).get('name')
                if obj.obj_options.get('name') != name:
                    self.rename(obj, '__history_recovery_' + str(uid))
        for uid, state in checkpoint.items():
            if state is None:
                continue
            obj = self.find(uid)
            if obj is None:
                self.rebuild(state)
            elif not states_equal(object_state(obj), state):
                name = state.get('obj_options', state.get('options', {})).get('name')
                if obj.obj_options.get('name') != name:
                    self.rename(obj, name)
                self.restore_state(obj, state)

    def clear(self):
        with self._lock:
            self.undo_stack.clear()
            self.redo_stack.clear()
        self.notify()

    def forget(self, uid):
        """Drop every step that touches the object (its steps can not give a correct result anymore)."""
        with self._lock:
            self.undo_stack[:] = [s for s in self.undo_stack if uid not in s.uids()]
            self.redo_stack[:] = [s for s in self.redo_stack if uid not in s.uids()]
        self.notify()

    # ---------------------------------------------------------------------------------------------------------
    # Object access for the steps (the GUI thread)
    # ---------------------------------------------------------------------------------------------------------
    def find(self, uid):
        for obj in self.collection.get_list():
            if getattr(obj, 'history_uid', None) == uid:
                return obj
        return None

    def find_or_fail(self, uid):
        obj = self.find(uid)
        if obj is None:
            raise HistoryError(_("The object of this step does not exist anymore."))
        return obj

    def after_change(self, obj, matrix=None):
        """Bounds, the Properties offset, object_changed and the plot after a geometry change."""
        try:
            obj.set_offset_values(bounds=obj.bounds())
        except Exception as e:
            log.debug("HistoryManager.after_change() set_offset_values --> %s" % str(e))
        try:
            self.app.app_obj.object_changed.emit(obj)
        except Exception as e:
            log.debug("HistoryManager.after_change() object_changed --> %s" % str(e))
        if (matrix is not None and str(getattr(obj, 'kind', '')).lower() in ('gerber', 'excellon', 'cncjob') and
                getattr(self.app, 'f_handlers', None) is not None):
            # the source text is not part of a matrix step: make it again for the geometry that is shown now
            if obj.regenerate_source() is False:
                raise HistoryError(_("Source regeneration failed"))
        if matrix is not None and hasattr(obj, 'plot_transformed'):
            obj.plot_transformed(matrix)
        else:
            obj.plot()

    def restore_state(self, obj, state):
        changed_keys = set(obj.obj_options)
        restore_object_state(obj, state)
        options = obj.obj_options
        # Refresh dependent widgets only after all geometry, options and identity are restored.
        if getattr(obj, 'ui', None) is not None and hasattr(obj, 'to_form'):
            obj.to_form()
        if 'plot' in options and hasattr(obj, 'shapes'):
            # Replay is synchronous. The visible property queues a worker and blocks the next Redo.
            obj.shapes.visible = options['plot']
            if hasattr(obj, 'annotation'):
                obj.annotation.visible = options['plot']
        if hasattr(obj, 'optionChanged'):
            for key in changed_keys | set(options):
                obj.optionChanged.emit(key)
        self.after_change(obj, None)

    def delete_objects(self, objects):
        """Delete without a confirmation and without a DeleteStep (see AppObjectOps.delete_objects_now)."""
        self.app.object_ops.delete_objects_now(objects)

    def rebuild(self, state):
        """Make the object again from its state, with the same history_uid (the same path as project open)."""

        def obj_init(new_obj, app_obj):
            if not isinstance(new_obj.obj_options, LoudDict):
                new_obj.obj_options = LoudDict(new_obj.obj_options)
                new_obj.obj_options.set_change_callback(new_obj.on_options_change)
            restore_object_state(new_obj, state)
            if state['kind'] == 'cncjob':
                # as on project open: keep the object's own header and snippets (not the Preferences ones)
                new_obj.is_loaded_from_project = True

        name = state.get('obj_options', state.get('options', {})).get('name', state['kind'])
        ret = self.app.app_obj.new_object(state['kind'], name, obj_init, plot=True, autoselected=False,
                                          record_history=False)
        if ret == 'fail' or self.find(state['history_uid']) is None:
            raise HistoryError(_("The object could not be made again."))

    def rename(self, obj, name):
        for other in self.collection.get_list():
            if other is not obj and other.obj_options.get('name') == name:
                raise HistoryError(_("Another object has this name") + ": " + name)
        self.collection.rename_object(obj, name)


class NullHistory:
    """
    A history that records nothing, with the recording API of HistoryManager. history_of() returns it when the app
    has no history.
    """

    def push(self, step):
        pass

    @contextmanager
    def group(self, label):
        yield

    @contextmanager
    def snapshot(self, label, objects, merge_key=None):
        yield

    @contextmanager
    def suspended(self):
        yield

    def record_matrix(self, label, pairs):
        pass

    def record_create(self, label, objects):
        pass

    def record_delete(self, label, objects):
        pass

    def record_rename(self, obj, old, new):
        pass

    def forget(self, uid):
        pass

    def clear(self):
        pass

    def notify(self):
        pass

    def is_recording(self):
        return False

    def undo(self):
        return False

    def redo(self):
        return False

    def can_undo(self):
        return False

    def can_redo(self):
        return False

    def undo_label(self):
        return ''

    def redo_label(self):
        return ''


NULL_HISTORY = NullHistory()


def history_of(app):
    """
    The history to record into: app.history if it is a HistoryManager, else NULL_HISTORY. The app has no history yet
    (start-up) or it is a test stub.
    """
    history = getattr(app, 'history', None)
    return history if isinstance(history, HistoryManager) else NULL_HISTORY
