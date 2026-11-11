# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# MIT Licence                                              #
# ##########################################################

"""
Background rewrite of source_file after a move.

The export of a big Gerber takes longer than the move itself, so Move only schedules it. Rules:

- schedule() marks the objects and bumps their generation; one worker task drains the marked objects (coalescing).
- A result is stored only if the generation did not change while it was made. Every writer of source_file that runs
  the export itself goes through supersede() (called by the local-use exports in appIO), which drops the pending
  entry and bumps the generation, so an old result never overwrites a newer text.
- flush() makes the pending objects now and waits for a running one. It is called before every read of source_file
  that leaves the app or is shown (project save, Save source, View source).
- A copy does not flush (the export of a big board blocks the GUI thread for seconds). It takes the text as it is;
  if the source is waiting() at that moment, the copy is scheduled too and gets its own text in the background.
- Lock order is always _run_lock, then _mutex.
"""

import logging
import threading

import gettext
import builtins

if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

_in_worker = threading.local()


class SourceRegen:
    def __init__(self, app):
        self.app = app
        self._mutex = threading.Lock()
        self._run_lock = threading.Lock()
        self._pending = {}
        self._gen = {}
        self._active = False
        self._running = None

    def waiting(self, obj):
        """True if the source text of obj is not made yet: the object is marked or its export runs now."""
        with self._mutex:
            return id(obj) in self._pending or self._running == id(obj)

    def schedule(self, objects):
        """Mark the objects and start the worker task if none is queued."""
        with self._mutex:
            for obj in objects:
                key = id(obj)
                self._pending[key] = obj
                self._gen[key] = self._gen.get(key, 0) + 1
            start = bool(self._pending) and not self._active
            if start:
                self._active = True
        if start:
            try:
                self.app.worker_task.emit({'fcn': self._drain, 'params': []})
            except Exception:
                with self._mutex:
                    self._active = False
                self.flush()
                raise

    def supersede(self, obj):
        """A fresh export of obj is made now: a pending or running regeneration is obsolete."""
        if getattr(_in_worker, 'active', False):
            return
        with self._mutex:
            key = id(obj)
            self._pending.pop(key, None)
            if key in self._gen:
                self._gen[key] += 1

    def flush(self, objects=None):
        """Regenerate the pending objects now (all, or only the given ones) and wait for a running one."""
        wanted = None if objects is None else {id(obj) for obj in objects}
        with self._run_lock:
            while True:
                with self._mutex:
                    key = next((k for k in self._pending if wanted is None or k in wanted), None)
                    if key is None:
                        return
                    obj = self._pending.pop(key)
                    gen = self._gen[key]
                    self._running = key
                self._regenerate(obj, key, gen)

    def _drain(self):
        _in_worker.active = True
        try:
            while True:
                with self._run_lock:
                    with self._mutex:
                        if not self._pending:
                            self._active = False
                            return
                        key = next(iter(self._pending))
                        obj = self._pending.pop(key)
                        gen = self._gen[key]
                        self._running = key
                    self._regenerate(obj, key, gen)
        finally:
            _in_worker.active = False

    def _regenerate(self, obj, key, gen):
        try:
            self._regenerate_now(obj, key, gen)
        finally:
            with self._mutex:
                self._running = None

    def _regenerate_now(self, obj, key, gen):
        if getattr(obj, 'deleted', False):
            return
        was_active = getattr(_in_worker, 'active', False)
        _in_worker.active = True
        text, error = False, None
        try:
            text = obj.regenerate_source(store=False)
        except Exception as err:
            error = err
        finally:
            _in_worker.active = was_active
        with self._mutex:
            if self._gen.get(key) != gen:
                return  # changed in between: the newer text or the next pass wins
            if text is False or text is None:
                failed = True
            else:
                failed = False
                obj.source_file = text
        if failed:
            log.warning("SourceRegen: source of %s was not rewritten, the old text is kept. %s" %
                        (getattr(obj, 'obj_options', {}).get('name', '?'), error or ''))
            try:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("The source text of the moved object could not be "
                                                              "updated. The old text is kept."))
            except Exception:
                pass


def manager(app):
    """The one SourceRegen of the app, made at first use."""
    found = app.__dict__.get('_source_regen')
    if not isinstance(found, SourceRegen):
        found = SourceRegen(app)
        app.__dict__['_source_regen'] = found
    return found


def schedule(app, objects):
    manager(app).schedule(list(objects))


def waiting(app, obj):
    found = app.__dict__.get('_source_regen')
    return isinstance(found, SourceRegen) and found.waiting(obj)


def flush(app, objects=None):
    found = app.__dict__.get('_source_regen')
    if isinstance(found, SourceRegen):
        found.flush(objects)


def supersede(obj):
    app = getattr(obj, 'app', None)
    found = getattr(app, '__dict__', {}).get('_source_regen')
    if isinstance(found, SourceRegen):
        found.supersede(obj)
