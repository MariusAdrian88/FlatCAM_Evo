# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/30/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Drill-referenced camera calibration.

The thin drill in the spindle marks a reference point; the camera then looks at
the same point. The camera mount offset is the spindle position minus the camera
position of that point, both read in the GRBL machine frame:

    offset = spindle_reference - camera_reference
    spindle_equivalent = camera_position + offset

No Qt imports and no machine access: the sender feeds fresh status reports in,
so the rules are unit tested without the GUI stack.
"""

import math

CAMERA_NONE = 'none'
CAMERA_STORED = 'stored'
CAMERA_VERIFIED = 'verified'
CAMERA_STALE = 'stale'
CAMERA_Z_MISMATCH = 'z_mismatch'

# default repeatability of the marked point and allowed change of the viewing height;
# the user selects the limits and they are persisted with their units
VERIFY_TOLERANCE_MM = 0.05
VIEW_Z_TOLERANCE_MM = 0.5

ORIENTATIONS = (0, 90, 180, 270)

_KEYS = (
    'tools_camera_calibrated',
    'tools_camera_offset_x',
    'tools_camera_offset_y',
    'tools_camera_view_z',
    'tools_camera_reference_x',
    'tools_camera_reference_y',
    'tools_camera_units',
    'tools_camera_cal_device',
    'tools_camera_cal_rotation',
    'tools_camera_cal_mirror',
    'tools_camera_verify_tolerance',
    'tools_camera_view_tolerance',
)


def _positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0.0 else None


def verify_tolerance(units, value=None):
    """:return: the user repeatability limit in `units`, or the default when unset/invalid."""
    limit = _positive(value)
    if limit is not None:
        return limit
    return VERIFY_TOLERANCE_MM / 25.4 if str(units).upper() == 'IN' else VERIFY_TOLERANCE_MM


def view_z_tolerance(units, value=None):
    """:return: the user viewing-height limit in `units`, or the default when unset/invalid."""
    limit = _positive(value)
    if limit is not None:
        return limit
    return VIEW_Z_TOLERANCE_MM / 25.4 if str(units).upper() == 'IN' else VIEW_Z_TOLERANCE_MM


def offset_from_references(spindle_xy, camera_xy):
    """:return: spindle XY minus camera XY, the camera mount offset."""
    return float(spindle_xy[0]) - float(camera_xy[0]), float(spindle_xy[1]) - float(camera_xy[1])


def camera_to_spindle(camera_xy, offset):
    """:return: the spindle XY that a camera-centered point resolves to."""
    return float(camera_xy[0]) + float(offset[0]), float(camera_xy[1]) + float(offset[1])


def _finite(*values):
    return all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)


def _option_float(options, key, default=0.0):
    """:return: a finite stored number, or None when the stored value is unusable."""
    raw = options.get(key, default)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _option_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _units(value):
    text = str(value).upper()
    return text if text in ('MM', 'IN') else None


def _rotation(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(value):
        return 0
    return int(value) % 360


class CameraCalibration:
    """The stored camera calibration and its per-session validity."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.has_calibration = False
        self.offset = (0.0, 0.0)
        self.view_z = 0.0
        self.reference = (0.0, 0.0)
        self.units = None
        self.camera_id = ''
        self.rotation = 0
        self.mirror = False
        self._pending = None
        self.verified = False
        self._verified_session = None
        self._verified_tolerance = None
        self._verified_view_tolerance = None
        self._session_id = None

    @classmethod
    def from_options(cls, options):
        calibration = cls()
        if not options or not _option_bool(options.get('tools_camera_calibrated', False)):
            return calibration
        offset_x = _option_float(options, 'tools_camera_offset_x')
        offset_y = _option_float(options, 'tools_camera_offset_y')
        view_z = _option_float(options, 'tools_camera_view_z')
        reference_x = _option_float(options, 'tools_camera_reference_x')
        reference_y = _option_float(options, 'tools_camera_reference_y')
        units = _units(options.get('tools_camera_units', None))
        camera_id = str(options.get('tools_camera_cal_device', '') or '')
        if None in (offset_x, offset_y, view_z, reference_x, reference_y) or not units or not camera_id:
            # corrupt or incomplete stored values: never offer them as a calibration
            return calibration
        calibration.has_calibration = True
        calibration.offset = (offset_x, offset_y)
        calibration.view_z = view_z
        calibration.reference = (reference_x, reference_y)
        calibration.units = units
        calibration.camera_id = camera_id
        calibration.rotation = _rotation(options.get('tools_camera_cal_rotation', 0))
        calibration.mirror = _option_bool(options.get('tools_camera_cal_mirror', False))
        return calibration

    def save(self, options):
        options['tools_camera_calibrated'] = bool(self.has_calibration)
        options['tools_camera_offset_x'] = float(self.offset[0])
        options['tools_camera_offset_y'] = float(self.offset[1])
        options['tools_camera_view_z'] = float(self.view_z)
        options['tools_camera_reference_x'] = float(self.reference[0])
        options['tools_camera_reference_y'] = float(self.reference[1])
        options['tools_camera_units'] = self.units
        options['tools_camera_cal_device'] = self.camera_id
        options['tools_camera_cal_rotation'] = int(self.rotation)
        options['tools_camera_cal_mirror'] = bool(self.mirror)

    def _clear_verification(self):
        self.verified = False
        self._verified_session = None
        self._verified_tolerance = None
        self._verified_view_tolerance = None

    def start_session(self, session_id):
        """A new connection or reference: a verified calibration is not blessed again."""
        if self._session_id == session_id:
            return False
        self._session_id = session_id
        self._pending = None
        self._clear_verification()
        return True

    def settings_changed(self, camera_id, rotation, mirror):
        """The camera or its orientation changed: the next use needs revalidation."""
        changed = (str(camera_id or '') != self.camera_id or _rotation(rotation) != self.rotation or
                   bool(mirror) != self.mirror)
        if not changed:
            return False
        self._clear_verification()
        if self._pending is not None and (
                str(camera_id or '') != self._pending['camera_id'] or
                _rotation(rotation) != self._pending['rotation'] or bool(mirror) != self._pending['mirror']):
            self._pending = None
        return True

    def tolerances_changed(self, tolerance=None, view_tolerance=None):
        """A different verification limit needs a new verification; the saved calibration stays."""
        if not self.verified or self._verified_tolerance is None:
            return False
        repeatability = verify_tolerance(self.units, tolerance)
        viewing = view_z_tolerance(self.units, view_tolerance)
        if (abs(repeatability - self._verified_tolerance) <= 1e-12 and
                self._verified_view_tolerance is not None and
                abs(viewing - self._verified_view_tolerance) <= 1e-12):
            return False
        self._clear_verification()
        return True

    @property
    def pending_spindle(self):
        """:return: the captured spindle reference XY while the camera capture is pending."""
        return None if self._pending is None else (self._pending['x'], self._pending['y'])

    def capture(self, role, machine_xyz, units, session_id, camera_id, rotation, mirror):
        """
        Capture one half of the sequence from a fresh machine position.

        :param role: 'spindle' (the drill at the mark) or 'camera' (the camera at the mark).
        :return: (ok, reason code).
        """
        units = _units(units)
        if units is None or not _finite(*machine_xyz):
            return False, 'invalid_position'
        if not str(camera_id or ''):
            return False, 'no_camera'
        if role not in ('spindle', 'camera'):
            return False, 'invalid_role'

        if self._pending is not None and self._pending['session'] != session_id:
            self._pending = None

        if role == 'spindle':
            # a new reference replaces a pending one and always restarts the sequence
            self._pending = {
                'session': session_id,
                'x': float(machine_xyz[0]),
                'y': float(machine_xyz[1]),
                'units': units,
                'camera_id': str(camera_id),
                'rotation': _rotation(rotation),
                'mirror': bool(mirror),
            }
            self._clear_verification()
            return True, 'ok'

        if self._pending is None:
            return False, 'no_spindle_reference'

        pending = self._pending
        if (pending['units'] != units or pending['camera_id'] != str(camera_id) or
                pending['rotation'] != _rotation(rotation) or pending['mirror'] != bool(mirror)):
            self._pending = None
            return False, 'capture_sequence_changed'

        self.offset = offset_from_references((pending['x'], pending['y']), machine_xyz)
        self.reference = (pending['x'], pending['y'])
        self.view_z = float(machine_xyz[2])
        self.units = units
        self.camera_id = pending['camera_id']
        self.rotation = pending['rotation']
        self.mirror = pending['mirror']
        self.has_calibration = True
        self._clear_verification()
        self._pending = None
        return True, 'ok'

    def state(self, session_id, units, camera_id, rotation, mirror, machine_z=None, view_tolerance=None,
              tolerance=None):
        """
        :return: (state, reason code) for the current camera, orientation and viewing height.
        """
        if not self.has_calibration:
            return CAMERA_NONE, 'no_calibration'
        if _units(units) != self.units:
            return CAMERA_STALE, 'units_changed'
        if (str(camera_id or '') != self.camera_id or _rotation(rotation) != self.rotation or
                bool(mirror) != self.mirror):
            return CAMERA_STALE, 'camera_changed'
        if view_tolerance is not None and _positive(view_tolerance) is None:
            return CAMERA_Z_MISMATCH, 'invalid_tolerance'
        if machine_z is not None:
            if not _finite(machine_z):
                return CAMERA_Z_MISMATCH, 'invalid_position'
            if abs(float(machine_z) - self.view_z) > view_z_tolerance(units, view_tolerance):
                return CAMERA_Z_MISMATCH, 'view_z_mismatch'
        if self.verified and self._verified_tolerance is not None:
            # a different limit accepts a different calibration: verify again
            if abs(verify_tolerance(units, tolerance) - self._verified_tolerance) > 1e-12:
                return CAMERA_STORED, 'tolerance_changed'
            if (self._verified_view_tolerance is not None and
                    abs(view_z_tolerance(units, view_tolerance) - self._verified_view_tolerance) > 1e-12):
                return CAMERA_STORED, 'tolerance_changed'
        if self.verified and self._verified_session == session_id:
            return CAMERA_VERIFIED, 'verified'
        return CAMERA_STORED, 'not_verified_this_session'

    def verify(self, machine_xy, machine_z, units, session_id, camera_id, rotation, mirror,
               tolerance=None, view_tolerance=None):
        """
        Re-center the same marked point under the camera and check the offset.

        :return: (ok, 'verified' or a reason code, distance in units or None).
        """
        if not _finite(machine_xy[0], machine_xy[1], machine_z):
            return False, 'no_fresh_z' if not _finite(machine_z) else 'invalid_position', None
        if tolerance is not None and _positive(tolerance) is None:
            return False, 'invalid_tolerance', None
        state, reason = self.state(
            session_id, units, camera_id, rotation, mirror, machine_z=machine_z,
            view_tolerance=view_tolerance, tolerance=tolerance)
        if state not in (CAMERA_STORED, CAMERA_VERIFIED):
            return False, reason, None
        predicted = camera_to_spindle(machine_xy, self.offset)
        distance = math.hypot(predicted[0] - self.reference[0], predicted[1] - self.reference[1])
        limit = verify_tolerance(units, tolerance)
        if distance <= limit + 1e-12:
            self.verified = True
            self._verified_session = session_id
            self._verified_tolerance = limit
            self._verified_view_tolerance = view_z_tolerance(units, view_tolerance)
            return True, 'verified', distance
        self._clear_verification()
        return False, 'repeatability_failed', distance

    def observe(self, machine_xy, machine_z, units, session_id, camera_id, rotation, mirror, wco=None,
                tolerance=None, view_tolerance=None):
        """
        Resolve a camera-centered point to the spindle position in the current frame.

        :param wco: the work coordinate offset in application units, or None when unknown.
        :return: dict with ok, state and reason; on success x/y, machine_x/machine_y, frame,
            units, camera identity, orientation and viewing Z.
        """
        if not _finite(machine_xy[0], machine_xy[1]):
            return {'ok': False, 'state': CAMERA_NONE, 'reason': 'invalid_position'}
        state, reason = self.state(
            session_id, units, camera_id, rotation, mirror, machine_z=machine_z,
            view_tolerance=view_tolerance, tolerance=tolerance)
        if not _finite(machine_z):
            return {'ok': False, 'state': state, 'reason': 'no_fresh_z'}
        if state != CAMERA_VERIFIED:
            return {'ok': False, 'state': state, 'reason': reason}

        machine = camera_to_spindle(machine_xy, self.offset)
        result = {
            'ok': True,
            'state': CAMERA_VERIFIED,
            'reason': 'verified',
            'machine_x': machine[0],
            'machine_y': machine[1],
            'x': machine[0],
            'y': machine[1],
            'frame': 'machine',
            'units': self.units,
            'view_z': self.view_z,
            'camera_id': self.camera_id,
            'rotation': self.rotation,
            'mirror': self.mirror,
            'session': session_id,
            'offset': self.offset,
            'coarse_tolerance': verify_tolerance(self.units, tolerance),
        }
        if wco is not None and _finite(wco[0], wco[1]):
            result['x'] = machine[0] - float(wco[0])
            result['y'] = machine[1] - float(wco[1])
            result['frame'] = 'work'
            result['wco'] = (float(wco[0]), float(wco[1]))
        return result
