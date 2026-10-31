# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/31/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Calibrated manual tool changes with a GRBL touchplate.

Fixed plate: the reference tool probes a plate whose machine position is recorded.
A replacement tool that protrudes farther touches the same plate at a higher
machine Z; the active work offset Z rises by that difference, so the physical
board Z datum is kept.

Removable plate: the operator places the plate on the PCB, probes it with the
installed tool, and the work offset Z is set so that the contact is exactly the
plate thickness above the board.

No Qt imports and no machine access: the sender reads the machine and feeds the
fresh values in, so the rules are unit tested without the GUI stack.
"""

import math

PLATE_NONE = 'disabled'
PLATE_FIXED = 'fixed'
PLATE_REMOVABLE = 'removable'
PLATE_MODES = (PLATE_NONE, PLATE_FIXED, PLATE_REMOVABLE)

# state of the measurement workflow, for the panel
STATE_OFF = 'off'
STATE_NO_REFERENCE = 'no_reference'
STATE_REFERENCE = 'reference'
STATE_MEASURED = 'measured'
STATE_NEEDS_REMOVAL = 'needs_removal'
STATE_READY = 'ready'

# distance that separates two reported positions that must be the same point
POSITION_EPSILON = 1e-2


def plate_mode(options):
    """:return: 'disabled', 'fixed' or 'removable'; legacy/unknown values disable measurement."""
    value = str((options or {}).get('tools_grbl_toolchange_plate', PLATE_NONE) or PLATE_NONE).strip().lower()
    return value if value in PLATE_MODES else PLATE_NONE


def _positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0.0 else None


def _finite(*values):
    return all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)


class ToolChangeSettings:
    """The operator's finite, positive probe limits, in application units."""

    def __init__(self, probe_distance, clearance, probe_feed, xy_tolerance, plate_thickness):
        self.probe_distance = probe_distance
        self.clearance = clearance
        self.probe_feed = probe_feed
        self.xy_tolerance = xy_tolerance
        self.plate_thickness = plate_thickness

    @classmethod
    def from_options(cls, options, units='MM'):
        """:return: the validated settings, or None when the mode is off or a relevant value is unusable."""
        mode = plate_mode(options)
        if mode == PLATE_NONE:
            return None
        options = options or {}
        stored_units = options.get('tools_grbl_toolchange_units', 'MM')
        if stored_units not in ('MM', 'IN') or units not in ('MM', 'IN'):
            return None
        factor = 1.0 if stored_units == units else (25.4 if units == 'MM' else 1.0 / 25.4)
        probe_distance = _positive(options.get('tools_grbl_toolchange_probe_distance'))
        clearance = _positive(options.get('tools_grbl_toolchange_clearance'))
        probe_feed = _positive(options.get('tools_grbl_toolchange_probe_feed'))
        if probe_distance is None or clearance is None or probe_feed is None:
            return None
        xy_tolerance = _positive(options.get('tools_grbl_toolchange_xy_tolerance'))
        plate_thickness = _positive(options.get('tools_grbl_toolchange_thickness'))
        if mode == PLATE_FIXED and xy_tolerance is None:
            return None
        if mode == PLATE_REMOVABLE and plate_thickness is None:
            return None
        values = tuple(None if value is None else value * factor for value in
                       (probe_distance, clearance, probe_feed, xy_tolerance, plate_thickness))
        if any(value is not None and not math.isfinite(value) for value in values):
            return None
        return cls(*values)

    def signature(self):
        """:return: the values that a measurement must be repeated after when they change."""
        return tuple(None if value is None else round(float(value), 9) for value in (
            self.probe_distance, self.clearance, self.probe_feed, self.xy_tolerance, self.plate_thickness))


def probe_target(start_work_z, settings):
    """:return: the work-frame Z limit of the bounded probe move."""
    return float(start_work_z) - float(settings.probe_distance)


def check_contact(start_work_z, target, contact_work_z, contact_xy, start_xy, epsilon=POSITION_EPSILON):
    """
    Check 1 probe record against the bounded move that produced it.

    :return: (ok, reason code). The contact must be below the start, inside the
        commanded limit, finite and at the pointed XY.
    """
    if not _finite(start_work_z, target, contact_work_z, contact_xy[0], contact_xy[1],
                   start_xy[0], start_xy[1]):
        return False, 'invalid_position'
    if math.hypot(float(contact_xy[0]) - float(start_xy[0]),
                  float(contact_xy[1]) - float(start_xy[1])) > epsilon:
        return False, 'invalid_position'
    if float(contact_work_z) >= float(start_work_z) - epsilon:
        # no downward travel: the tip was already touching, or the plate is above the start
        return False, 'already_triggered'
    if float(contact_work_z) < float(target) - epsilon:
        return False, 'probe_invalid'
    return True, 'ok'


class ToolChangeMeasurement:
    """The session-only fixed reference and the current measurement of 1 tool."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.mode = PLATE_NONE
        self._settings_key = None
        self.session = None
        self.units = None
        self.reference = None
        self.measurement = None
        self.plate_removed = False
        self.applied = False

    def start_session(self, session_id):
        """A new connection, reset or home: no fixed reference survives it."""
        if self.session == session_id:
            return False
        self.session = session_id
        self.reference = None
        self.measurement = None
        self.plate_removed = False
        self.applied = False
        return True

    def configure(self, mode, settings):
        """A mode or setting change retires the pending measurement and the fixed reference."""
        key = None if settings is None else settings.signature()
        if self.mode == mode and self._settings_key == key:
            return False
        self.mode = mode
        self._settings_key = key
        self.reference = None
        self.measurement = None
        self.plate_removed = False
        self.applied = False
        return True

    def record_reference(self, contact_machine_z, contact_xy, offset_z, wcs, units, session, settings,
                         offset_xy=(0.0, 0.0)):
        """Record the fixed plate contact of the reference tool, in application units."""
        if not _finite(contact_machine_z, contact_xy[0], contact_xy[1], offset_z,
                       offset_xy[0], offset_xy[1]):
            return False
        self.reference = {
            'contact_machine_z': float(contact_machine_z),
            'x': float(contact_xy[0]),
            'y': float(contact_xy[1]),
            'offset_z': float(offset_z),
            'offset_x': float(offset_xy[0]),
            'offset_y': float(offset_xy[1]),
            'wcs': str(wcs),
        }
        self.measurement = None
        self.session = session
        self.units = str(units)
        self.plate_removed = False
        self.applied = False
        return True

    def record_measurement(self, contact_machine_z, contact_xy, offset_z, wcs, units, session,
                           offset_xy=(0.0, 0.0)):
        """Record the contact of the installed tool, in application units."""
        if not _finite(contact_machine_z, contact_xy[0], contact_xy[1], offset_z,
                       offset_xy[0], offset_xy[1]):
            return False
        self.measurement = {
            'contact_machine_z': float(contact_machine_z),
            'x': float(contact_xy[0]),
            'y': float(contact_xy[1]),
            'offset_z': float(offset_z),
            'offset_x': float(offset_xy[0]),
            'offset_y': float(offset_xy[1]),
            'wcs': str(wcs),
            'session': session,
            'units': str(units),
        }
        self.session = session
        self.units = str(units)
        self.plate_removed = False
        self.applied = False
        return True

    def _stale_reason(self, session, units, settings):
        if session != self.session or units != self.units:
            return 'stale_reference'
        if settings is None or settings.signature() != self._settings_key:
            return 'settings_changed'
        return None

    def fixed_delta(self, settings, session, units):
        """
        :return: (ok, reason, delta) where delta is the contact difference
            replacement minus reference, in machine units.
        """
        if self.mode != PLATE_FIXED or self.reference is None or self.measurement is None:
            return False, 'no_reference', None
        reason = self._stale_reason(session, units, settings)
        if reason is not None:
            return False, reason, None
        if settings.xy_tolerance is None:
            return False, 'settings_changed', None
        if (self.measurement['wcs'] != self.reference['wcs'] or
                abs(self.measurement['offset_x'] - self.reference['offset_x']) > 1e-4 or
                abs(self.measurement['offset_y'] - self.reference['offset_y']) > 1e-4 or
                abs(self.measurement['offset_z'] - self.reference['offset_z']) > 1e-4):
            # a board Z zero, WCS or offset change after the reference calibration
            return False, 'frame_changed', None
        distance = math.hypot(self.measurement['x'] - self.reference['x'],
                              self.measurement['y'] - self.reference['y'])
        if distance > settings.xy_tolerance + 1e-12:
            return False, 'plate_moved', None
        delta = float(self.measurement['contact_machine_z']) - float(self.reference['contact_machine_z'])
        if not math.isfinite(delta):
            return False, 'invalid_position', None
        return True, 'ok', delta

    def fixed_offset(self, delta):
        """:return: the new active WCS Z offset for the fixed plate correction."""
        return float(self.reference['offset_z']) + float(delta)

    def removable_offset(self, settings):
        """:return: the WCS Z offset that makes the contact exactly 1 plate thickness above the board."""
        if self.measurement is None or settings is None or settings.plate_thickness is None:
            return None
        return float(self.measurement['contact_machine_z']) - float(settings.plate_thickness)

    def applied_offset(self, settings, session, units):
        """
        :return: (ok, reason, new_offset_z) for the measurement that is ready to apply.
        """
        if settings is None:
            return False, 'settings_changed', None
        if self.measurement is None:
            return False, ('no_reference' if self.mode == PLATE_FIXED else 'no_measurement'), None
        if session != self.measurement['session'] or str(units) != self.measurement['units']:
            return False, 'stale_measurement', None
        if settings.signature() != self._settings_key:
            return False, 'settings_changed', None
        if self.mode == PLATE_FIXED:
            ok, reason, delta = self.fixed_delta(settings, session, units)
            if not ok:
                return False, reason, None
            return True, 'ok', self.fixed_offset(delta)
        offset = self.removable_offset(settings)
        if offset is None or not math.isfinite(offset):
            return False, 'invalid_position', None
        return True, 'ok', offset

    def mark_applied(self, new_offset_z):
        """
        Rebase the fixed reference to the tool that was just calibrated: the plate did
        not move, and the next tool is compared with this contact and offset.

        The removable measurement stays: the same plate must still be removed.
        """
        if self.mode == PLATE_FIXED and self.reference is not None and self.measurement is not None:
            self.reference['contact_machine_z'] = self.measurement['contact_machine_z']
            self.reference['x'] = self.measurement['x']
            self.reference['y'] = self.measurement['y']
            self.reference['offset_z'] = float(new_offset_z)
            self.measurement = None
        self.applied = True
        self.plate_removed = False

    def mark_plate_removed(self):
        if self.measurement is None or not self.applied:
            return False
        self.plate_removed = True
        return True

    def forget(self):
        """Discard the fixed reference and the measurement; the session and the mode stay."""
        self.reference = None
        self.measurement = None
        self.plate_removed = False
        self.applied = False

    def state(self):
        if self.mode == PLATE_NONE:
            return STATE_OFF
        if self.mode == PLATE_FIXED:
            if self.reference is None:
                return STATE_NO_REFERENCE
            return STATE_REFERENCE if self.measurement is None else STATE_MEASURED
        if self.measurement is None:
            return STATE_NO_REFERENCE
        if not self.applied:
            return STATE_MEASURED
        return STATE_READY if self.plate_removed else STATE_NEEDS_REMOVAL
