# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/30/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Board registration from camera-centered fiducials.

The design points are picked on the project objects; the measured points are
camera observations in the GRBL work frame. The fit is a rigid move + rotation
only. No Qt and no machine access: the sender feeds fresh observations in, so
the rules are unit tested without the GUI stack.
"""

import logging
import math

import gettext
import appTranslation as fcTranslate
import builtins

from appHandlers.appHistory import (
    apply_matrix_to_object, history_of, object_state, restore_object_state, transform_metadata)
from appPlugins.ToolAlignObjects.AlignGen import residuals, solve_rigid

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

ELIGIBLE_KINDS = ('gerber', 'excellon', 'geometry')
ALIGNMENT_TOLERANCE_MM = 0.1
ALIGNMENT_TOLERANCE_KEY = 'tools_camera_alignment_tolerance'
ALIGNMENT_TOLERANCE_UNITS_KEY = 'tools_camera_alignment_tolerance_units'


class RegistrationError(Exception):
    """A user message: the points or the selection can not give a valid registration."""


class RegistrationFit:
    """A rigid fit and the residual of every design/measured pair."""

    def __init__(self, matrix, design, measured, errors, rms, tolerance):
        self.matrix = tuple(float(value) for value in matrix)
        self.design = list(design)
        self.measured = list(measured)
        self.errors = list(errors)
        self.rms = float(rms)
        self.max_error = max(self.errors) if self.errors else 0.0
        self.tolerance = float(tolerance)
        self.angle = math.degrees(math.atan2(self.matrix[2], self.matrix[0]))
        self.translation = (self.matrix[4], self.matrix[5])


def _positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0.0 else None


def _finite_xy(value):
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        return None
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def alignment_tolerance(units, value=None):
    """:return: the user alignment acceptance limit in `units`, or the default when unset/invalid."""
    limit = _positive(value)
    if limit is not None:
        return limit
    return ALIGNMENT_TOLERANCE_MM / 25.4 if str(units).upper() == 'IN' else ALIGNMENT_TOLERANCE_MM


def _close(a, b):
    return all(abs(float(x) - float(y)) <= 1e-9 for x, y in zip(a, b))


def _identity(observation):
    """The machine reference that a measured point belongs to."""
    return {
        'session': observation.get('session'),
        'units': str(observation.get('units', '')).upper(),
        'camera_id': str(observation.get('camera_id', '') or ''),
        'rotation': int(observation.get('rotation', 0) or 0),
        'mirror': bool(observation.get('mirror', False)),
        'offset': _finite_xy(observation.get('offset') or ()),
        'view_z': observation.get('view_z'),
        'wco': _finite_xy(observation.get('wco') or ()),
    }


class CameraRegistration:
    """The selected objects, the design/measured pairs, the preview fit and the apply guard."""

    def __init__(self):
        self.decimals = 4
        self.reset()

    def reset(self):
        self.objects = []
        self.design = []
        self.measured = []
        self.pending_design = None
        self.design_units = None
        self.identity = None
        self.preview = None
        self.applied = False
        self.last_error = ''

    def set_objects(self, objects):
        """Snapshot the explicitly chosen objects; a later selection change can not retarget Apply."""
        chosen, seen = [], set()
        for obj in objects:
            if obj is None or getattr(obj, 'deleted', False):
                continue
            if str(getattr(obj, 'kind', '')).lower() not in ELIGIBLE_KINDS:
                continue
            key = id(obj)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(obj)
        if not chosen:
            raise RegistrationError(_("Select at least one Gerber, Excellon or Geometry object."))
        self.reset()
        self.objects = chosen
        return chosen

    def add_design(self, x, y, units=None):
        """A design coordinate from a canvas pick or a numeric entry, in the project units."""
        point = _finite_xy((x, y))
        if point is None:
            raise RegistrationError(_("The design coordinates must be finite numbers."))
        units = None if units is None else str(units).upper()
        if units is not None and units not in ('MM', 'IN'):
            raise RegistrationError(_("The application units are not known."))
        if units is not None and self.design_units is not None and units != self.design_units:
            raise RegistrationError(_("The application units changed; restart the registration."))
        if self.pending_design is not None:
            raise RegistrationError(_("Capture the camera point for the pending design point first."))
        for old in self.design:
            if math.hypot(point[0] - old[0], point[1] - old[1]) <= 1e-9:
                raise RegistrationError(_("This design point is already in the list."))
        if units is not None:
            self.design_units = units
        self.pending_design = point
        self.preview = None
        self.applied = False
        return point

    def add_observation(self, observation):
        """
        Pair the pending design point with a fresh camera observation.

        :return: (ok, reason code).
        """
        if not isinstance(observation, dict) or not observation.get('ok'):
            return False, str((observation or {}).get('reason', 'observation_failed'))
        if observation.get('frame') != 'work':
            return False, 'work_frame_required'
        measured = _finite_xy((observation.get('x'), observation.get('y')))
        if measured is None:
            return False, 'invalid_position'
        if self.pending_design is None:
            return False, 'no_design_point'
        if (self.design_units is not None and
                str(observation.get('units', '')).upper() != self.design_units):
            self.invalidate('units_changed')
            return False, 'units_changed'
        identity = _identity(observation)
        if identity['wco'] is None:
            return False, 'no_work_offset'
        mismatch = self._mismatch(identity)
        if mismatch is not None:
            self.invalidate(mismatch)
            return False, mismatch
        for old in self.measured:
            if math.hypot(measured[0] - old[0], measured[1] - old[1]) <= 1e-9:
                return False, 'duplicate_point'
        self.identity = identity
        self.design.append(self.pending_design)
        self.measured.append(measured)
        self.pending_design = None
        self.preview = None
        self.applied = False
        return True, 'ok'

    def _mismatch(self, identity):
        """The reason why a measured point belongs to another reference, or None."""
        if self.identity is None:
            return None
        if identity['session'] != self.identity['session']:
            return 'session_changed'
        if identity['units'] != self.identity['units']:
            return 'units_changed'
        if (identity['camera_id'] != self.identity['camera_id'] or
                identity['rotation'] != self.identity['rotation'] or
                identity['mirror'] != self.identity['mirror']):
            return 'camera_changed'
        if (identity['offset'] is None or self.identity['offset'] is None or
                not _close(identity['offset'], self.identity['offset']) or
                identity['view_z'] is None or self.identity['view_z'] is None or
                not math.isclose(float(identity['view_z']), float(self.identity['view_z']),
                                 rel_tol=0.0, abs_tol=1e-9)):
            return 'calibration_changed'
        if not _close(identity['wco'], self.identity['wco']):
            return 'work_offset_changed'
        return None

    def invalidate(self, reason='', clear_points=True):
        """The current reference or data changed: the preview and the pending registration are old."""
        self.last_error = str(reason or '')
        self.preview = None
        self.applied = False
        if clear_points:
            self.design = []
            self.measured = []
            self.pending_design = None
            self.design_units = None
            self.identity = None

    def remove_pair(self, row):
        """Remove one complete pair, or the pending design point, from the table."""
        self.preview = None
        self.applied = False
        if 0 <= row < len(self.design):
            del self.design[row]
            del self.measured[row]
        elif row == len(self.design) and self.pending_design is not None:
            self.pending_design = None
        else:
            return False
        if not self.measured:
            self.identity = None
            self.design_units = None
        return True

    def fit(self, tolerance):
        """
        Solve the rigid matrix and reject it above the user acceptance tolerance.

        :return: RegistrationFit, or None while there are fewer than two complete pairs.
        """
        limit = _positive(tolerance)
        if limit is None:
            raise RegistrationError(_("The alignment tolerance must be a positive number."))
        if len(self.design) < 2 or len(self.measured) != len(self.design):
            self.preview = None
            return None
        try:
            matrix = solve_rigid(self.design, self.measured)
        except Exception as exc:
            self.preview = None
            raise RegistrationError(_("The points can not give a valid rigid registration.")) from exc
        errors, rms = residuals(matrix, self.design, self.measured)
        max_error = max(errors) if errors else 0.0
        self.preview = None
        if max_error > limit + 1e-12:
            units = self.identity['units'] if self.identity else ''
            raise RegistrationError(_(
                "The largest residual is %.*f %s; it exceeds the alignment tolerance of %.*f %s.") % (
                self.decimals, max_error, units, self.decimals, limit, units))
        self.preview = RegistrationFit(matrix, self.design, self.measured, errors, rms, limit)
        return self.preview

    def _decimals(self):
        return self.decimals

    def check_context(self, observation):
        """:return: (ok, reason) if a fresh observation still belongs to the previewed reference."""
        if not isinstance(observation, dict) or not observation.get('ok'):
            return False, str((observation or {}).get('reason', 'reference_failed'))
        if observation.get('frame') != 'work':
            return False, 'work_frame_required'
        if self.identity is None or self.preview is None:
            return False, 'no_registration'
        if (self.design_units is not None and
                str(observation.get('units', '')).upper() != self.design_units):
            return False, 'units_changed'
        identity = _identity(observation)
        if identity['wco'] is None:
            return False, 'no_work_offset'
        mismatch = self._mismatch(identity)
        return (True, 'ok') if mismatch is None else (False, mismatch)

    def mark_applied(self):
        """One successful apply consumes the fit and the pairs; the same transform can not accumulate."""
        self.preview = None
        self.design = []
        self.measured = []
        self.pending_design = None
        self.design_units = None
        self.identity = None
        self.applied = True


def _validate_objects(app, objects):
    if not objects:
        raise RegistrationError(_("Select at least one Gerber, Excellon or Geometry object."))
    try:
        live = list(app.collection.get_list())
    except Exception:
        live = None
    chosen, seen = [], set()
    for obj in objects:
        if obj is None or getattr(obj, 'deleted', False):
            raise RegistrationError(_("A selected object was deleted."))
        if str(getattr(obj, 'kind', '')).lower() not in ELIGIBLE_KINDS:
            raise RegistrationError(_("Only Gerber, Excellon and Geometry objects can be registered."))
        if live is not None and obj not in live:
            raise RegistrationError(_("A selected object is not in the project anymore."))
        if id(obj) in seen:
            raise RegistrationError(_("The same object is selected twice."))
        seen.add(id(obj))
        chosen.append(obj)
    return chosen


def _after_apply(app, obj, matrix):
    """Bounds, source, Properties and plot after a successful transform."""
    try:
        obj.set_offset_values(bounds=obj.bounds())
    except Exception as exc:
        log.debug("CameraRegistration._after_apply() set_offset_values --> %s" % str(exc))
    if str(getattr(obj, 'kind', '')).lower() in ('gerber', 'excellon'):
        if obj.regenerate_source() is False:
            raise RegistrationError(_("The source of %s could not be regenerated.") %
                                    getattr(obj, 'obj_options', {}).get('name', obj.kind))
    try:
        app.app_obj.object_changed.emit(obj)
    except Exception as exc:
        log.debug("CameraRegistration._after_apply() object_changed --> %s" % str(exc))
    if hasattr(obj, 'plot_transformed'):
        obj.plot_transformed(matrix)
    elif hasattr(obj, 'plot'):
        obj.plot()


def _snapshot(obj):
    """The complete prior state that a failed transform must give back, including the source text."""
    return {
        'state': object_state(obj),
        'has_source': hasattr(obj, 'source_file'),
        'source': getattr(obj, 'source_file', None),
    }


def _restore(app, obj, snapshot):
    """Put back the exact geometry, options, tools, source and plot of one object."""
    restore_object_state(obj, snapshot['state'])
    if snapshot['has_source']:
        obj.source_file = snapshot['source']
    try:
        obj.set_offset_values(bounds=obj.bounds())
    except Exception as exc:
        log.debug("CameraRegistration._restore() set_offset_values --> %s" % str(exc))
    try:
        if hasattr(obj, 'plot'):
            obj.plot()
    except Exception as exc:
        log.error("CameraRegistration._restore() plot --> %s" % str(exc))
    try:
        app.app_obj.object_changed.emit(obj)
    except Exception as exc:
        log.debug("CameraRegistration._restore() object_changed --> %s" % str(exc))


def _rollback(app, objects, before):
    """Restore every object, including the one that failed halfway through its transform."""
    failures = []
    for obj in reversed(objects):
        try:
            _restore(app, obj, before[id(obj)])
        except Exception as exc:
            failures.append(str(exc))
            log.error("CameraRegistration._rollback() --> %s" % str(exc))
    return failures


def apply_registration(app, objects, fit, label=None):
    """
    Apply one rigid fit to every explicitly selected object as ONE Undo step.

    Every object is validated and snapshotted before the first change; a partial
    failure restores the exact prior geometry, source and metadata of all of them.
    """
    chosen = _validate_objects(app, objects)
    before = {id(obj): _snapshot(obj) for obj in chosen}
    before_metadata = {id(obj): transform_metadata(obj) for obj in chosen}
    try:
        for obj in chosen:
            apply_matrix_to_object(obj, fit.matrix)
        for obj in chosen:
            _after_apply(app, obj, fit.matrix)
        history_of(app).record_matrix(
            label or _("Camera registration"),
            [(obj, fit.matrix, before_metadata[id(obj)]) for obj in chosen])
    except Exception as exc:
        failures = _rollback(app, chosen, before)
        if failures:
            raise RegistrationError(_(
                "Registration failed and could not be fully restored: %s") % '; '.join(failures)) from exc
        raise RegistrationError(_("Registration failed and was rolled back: %s") % str(exc)) from exc
    return True
