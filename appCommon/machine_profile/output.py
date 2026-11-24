# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Machine Profiles: the single entry for an output (stage 1: check only, the text is never changed).

No Qt, no app import.
"""

import hashlib
import json
import re
from typing import List, Optional, Tuple

from appCommon.machine_profile.calibrate import apply_matrix
from appCommon.machine_profile.check import check_motion
from appCommon.machine_profile.correct import correct_program
from appCommon.machine_profile.motion import interpret
from appCommon.machine_profile.profile import MachineProfile, content_hash
from appCommon.machine_profile.records import CheckResult, Purpose, Setup


def has_reject(result: Optional[CheckResult]) -> bool:
    return result is not None and any(v.severity == 'reject' for v in result.violations)


def _setup_assumptions(setup: Setup) -> list:
    items = (('start position', setup.start), ('units', setup.units), ('distance mode', setup.distance_mode),
             ('arc mode', setup.arc_mode), ('plane', setup.plane), ('motion mode', setup.motion_mode),
             ('work offsets', setup.wcs_offsets), ('active WCS', setup.active_wcs), ('G92', setup.g92),
             ('TLO', setup.tlo))
    lines = ['%s: %s' % (name, 'unknown' if value is None else 'declared %s' % (value,)) for name, value in items]
    if setup.source == 'controller':
        lines.append('setup: live state of the controller')
    return lines


def setup_from_controller(profile: MachineProfile, dialect: Optional[str], units: Optional[str], machine_pos,
                          wcs_offsets, active_wcs: Optional[str], g92, tlo) -> Setup:
    """
    The setup of a check from the live state of the controller. `machine_pos` is the machine position (M) that the
    controller reports; the start of the check is that position. Every argument may be None (= unknown).
    """
    return Setup(profile_hash=content_hash(profile), dialect=dialect,
                 wcs_offsets=None if wcs_offsets is None else {k: tuple(v) for k, v in wcs_offsets.items()},
                 active_wcs=active_wcs, g92=None if g92 is None else tuple(g92), tlo=tlo,
                 start=None if machine_pos is None else tuple(machine_pos), units=units, source='controller')


def setup_from_profile(profile: MachineProfile, dialect: Optional[str], units: str) -> Setup:
    """The declared setup of a file output: the profile work offset as G54, zero G92 and TLO."""
    offset = tuple(profile.work_offset)
    start = None
    if profile.end is not None:
        # the last job is assumed to have ended at the end station; Setup.start is a machine position (M)
        start = tuple(float(m) for m in profile.end)
    return Setup(profile_hash=content_hash(profile), dialect=dialect, wcs_offsets={'G54': offset},
                 active_wcs='G54', g92=(0.0, 0.0, 0.0), tlo=0.0, start=start, units=units, distance_mode='G90',
                 arc_mode='G91.1', plane='G17', motion_mode='G0')


def correction_matrix(profile: Optional[MachineProfile]):
    """The correction matrix C of the profile when its calibration is on; None otherwise."""
    if profile is None or not profile.calibration:
        return None
    if not profile.calibration.get('enabled') or not profile.calibration.get('matrix'):
        return None
    return profile.calibration['matrix']


def calibration_hash(profile: Optional[MachineProfile]) -> Optional[str]:
    """12 hex digits that identify the correction matrix in force; None when the correction is off."""
    matrix = correction_matrix(profile)
    if matrix is None:
        return None
    text = json.dumps([[round(float(value), 12) for value in row] for row in matrix])
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]


def map_point(profile: Optional[MachineProfile], x: float, y: float) -> Tuple[float, float]:
    """A design point to the work point that the machine is commanded to: W = C(D). Identity without a correction."""
    matrix = correction_matrix(profile)
    if matrix is None:
        return x, y
    return apply_matrix(matrix, x, y)


def unmap_point(profile: Optional[MachineProfile], x: float, y: float) -> Tuple[float, float]:
    """The inverse of `map_point`: the design point of a reported work point, D = C^-1(W)."""
    matrix = correction_matrix(profile)
    if matrix is None:
        return x, y
    det = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    if det == 0:
        raise ValueError('The correction matrix has no inverse.')
    inverse = [[matrix[1][1] / det, -matrix[0][1] / det], [-matrix[1][0] / det, matrix[0][0] / det]]
    return apply_matrix(inverse, x, y)


PROBE_HEADER = re.compile(r'\(MP:PROBE\s+hash=(\S+)\s+units=(\S+)\s+frame=(\S+?)\)')


def probe_header(profile: Optional[MachineProfile], units: str, frame: str = 'G54') -> str:
    """The header comment of a probing program that holds mapped points ('' when the correction is off)."""
    digest = calibration_hash(profile)
    if digest is None:
        return ''
    return '(MP:PROBE hash=%s units=%s frame=%s)\n' % (digest, 'inch' if str(units).upper() in ('IN', 'INCH') else 'mm',
                                                       frame)


def parse_probe_header(text: str) -> Optional[dict]:
    """The `(MP:PROBE ...)` data of a probe log or a probing program: {hash, units, frame}, or None without it."""
    found = PROBE_HEADER.search(text)
    if found is None:
        return None
    return {'hash': found.group(1), 'units': found.group(2), 'frame': found.group(3)}


def check_probe_moves(profile: MachineProfile, setup: Setup, points, travel_z: float, depth: float,
                      units: str) -> CheckResult:
    """
    The check of the moves of a probe run: for each work point the lift to `travel_z`, the rapid to the point and
    the plunge to `depth` (the probe move itself). The points are the commanded work points (already mapped).
    """
    lines = ['G20' if str(units).upper() in ('IN', 'INCH') else 'G21', 'G90']
    for x, y in points:
        lines += ['G0 Z%.4f' % travel_z, 'G0 X%.4f Y%.4f' % (x, y), 'G1 Z%.4f' % depth]
    lines.append('G0 Z%.4f' % travel_z)
    _text, result = prepare_output('\n'.join(lines) + '\n', Purpose.CALIBRATION, setup, profile)
    return result


def _with_header(text: str, profile: MachineProfile) -> str:
    """One comment line near the top: the profile, its hash and the correction."""
    name = re.sub(r'[()\r\n;]', ' ', profile.name)
    line = '(Machine profile: %s, %s, XY corrected)\n' % (name, content_hash(profile)[:12])
    if text[:1] == '%':
        end = text.find('\n') + 1
        if end == 0:
            return text + '\n' + line
        return text[:end] + line + text[end:]
    return line + text


def prepare_output(text: str, purpose: Purpose, setup: Setup, profile: Optional[MachineProfile],
                   decimals: int = 4, correct: bool = True,
                   line_map: Optional[List[int]] = None) -> Tuple[str, Optional[CheckResult]]:
    """
    The output text and its check. A FILE or STREAM output of a profile with the calibration on is corrected
    (`correct` False switches that off for a source of unknown provenance or an already prepared program) and the
    check reads the corrected text, the exact output. DESIGN and CALIBRATION are never corrected.

    :param line_map: optional out-parameter, filled only when the text is corrected: for each line of the returned
        text the 1-based source line that it comes from (the header line maps to line 1).
    :raises CorrectionRefused: the program holds a form that the correction does not rewrite
    """
    if profile is None:
        return text, None
    matrix = correction_matrix(profile)
    corrected = purpose in (Purpose.FILE, Purpose.STREAM) and correct and matrix is not None
    if corrected:
        mapped: List[int] = []
        text = _with_header(correct_program(text, setup, matrix, decimals, line_map=mapped), profile)
        if line_map is not None:
            at = 1 if text[:1] == '%' else 0
            mapped.insert(at, 1)
            line_map.extend(mapped)
    result = check_motion(interpret(text, setup), profile)
    result.assumptions = _setup_assumptions(setup) + result.assumptions
    if setup.start is not None and profile.end is not None:
        result.assumptions.append('start: the last job ended at the profile end station')
    if corrected:
        result.assumptions.append('XY corrected by the calibration of the profile')
    return text, result
