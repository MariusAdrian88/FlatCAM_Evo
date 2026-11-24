# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The machine profile: passive facts of one machine (mm, machine frame M) and its storage, one JSON file per profile.

No Qt, no app import. The folder of the profiles is resolved by the caller (see `resolve_profiles_folder`).
"""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field, fields
from typing import Dict, List, Optional, Tuple

from appCommon.machine_profile.records import ViolationKind

RESERVED_NAME = 'None'
CORNERS = ('bl', 'br', 'tl', 'tr')
_REJECT_BY_DEFAULT = (ViolationKind.OUT_OF_BED, ViolationKind.EXCLUSION, ViolationKind.Z_TRAVEL)
_XYZ_FIELDS = ('work_offset', 'toolchange', 'end')


def _default_rule_modes() -> Dict[str, str]:
    return {k.value: 'reject' if k in _REJECT_BY_DEFAULT else 'warn' for k in ViolationKind}


@dataclass
class MachineProfile:
    name: str = ''
    bed_x: float = 0.0
    bed_y: float = 0.0
    origin_corner: str = 'bl'
    z_travel: float = 0.0
    work_offset: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    toolchange: Optional[Tuple[float, float, float]] = None
    end: Optional[Tuple[float, float, float]] = None
    probe_plate: Optional[Tuple[float, float]] = None
    preprocessor_mill: Optional[str] = None
    preprocessor_drill: Optional[str] = None
    preprocessor_solderpaste: Optional[str] = None
    feed_xy_max: Optional[float] = None
    feed_z_max: Optional[float] = None
    rapid_max: Optional[float] = None
    rpm_min: Optional[float] = None
    rpm_max: Optional[float] = None
    cut_depth_max: Optional[float] = None
    exclusions: List[dict] = field(default_factory=list)
    calibration: Optional[dict] = None
    rule_modes: Dict[str, str] = field(default_factory=_default_rule_modes)
    # E-Cut axis limits, axis name ('X', 'Y', 'Z') -> value. None: not set. Units: mm/min, mm/s2, mm (machine frame).
    axis_velocity_max: Optional[Dict[str, float]] = None
    axis_accel_max: Optional[Dict[str, float]] = None
    soft_limit_min: Optional[Dict[str, float]] = None
    soft_limit_max: Optional[Dict[str, float]] = None


_FIELD_NAMES = tuple(f.name for f in fields(MachineProfile))
AXIS_NAMES = ('X', 'Y', 'Z')
# Left out of the dict while unset, so the hash of a profile made before these fields existed does not change.
_OPTIONAL_KEYS = ('axis_velocity_max', 'axis_accel_max', 'soft_limit_min', 'soft_limit_max')


def to_dict(profile: MachineProfile) -> dict:
    """The JSON-ready dict of a profile (tuples become lists)."""
    return json.loads(json.dumps({n: getattr(profile, n) for n in _FIELD_NAMES
                                  if n not in _OPTIONAL_KEYS or getattr(profile, n) is not None}))


def from_dict(data: dict) -> MachineProfile:
    """Build a profile. A key that is not a field is refused with a ValueError that names it."""
    for key in data:
        if key not in _FIELD_NAMES:
            raise ValueError("Unknown machine profile key: %s" % key)
    values = dict(data)
    for key in _XYZ_FIELDS + ('probe_plate',):
        if values.get(key) is not None:
            values[key] = tuple(values[key])
    for key in _OPTIONAL_KEYS:
        if values.get(key) is not None:
            if not isinstance(values[key], dict) or any(axis not in AXIS_NAMES for axis in values[key]):
                raise ValueError("%s must map the axis names X, Y, Z to numbers." % key)
            values[key] = {axis: float(number) for axis, number in values[key].items()}
    if 'exclusions' in values:
        values['exclusions'] = [dict(e, polygon=[tuple(p) for p in e.get('polygon', [])]) for e in values['exclusions']]
    if 'rule_modes' in values:
        values['rule_modes'] = {**_default_rule_modes(), **values['rule_modes']}
    return MachineProfile(**values)


def content_hash(profile: MachineProfile) -> str:
    """sha256 of the canonical JSON (sorted keys, no whitespace) of the whole profile."""
    canonical = json.dumps(to_dict(profile), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _safe_filename(name: str) -> str:
    safe = re.sub(r'[^\w\- .]', '_', name, flags=re.ASCII).strip(' .')
    return (safe or 'profile') + '.json'


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("The machine profile needs a name.")
    if name == RESERVED_NAME:
        raise ValueError("The name '%s' is reserved." % RESERVED_NAME)


def resolve_profiles_folder(configured: str, data_path: str) -> str:
    """The profiles folder: the configured one, or the app data folder + 'machine_profiles' when it is empty."""
    return configured if configured else os.path.join(data_path, 'machine_profiles')


def save_profile(profile: MachineProfile, folder: str) -> str:
    """Write the profile as one JSON file in the folder (made if missing). Return the path."""
    _check_name(profile.name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _safe_filename(profile.name))
    fd, tmp = tempfile.mkstemp(dir=folder, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(to_dict(profile), stream, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


def load_profile(path: str) -> MachineProfile:
    with open(path, 'r', encoding='utf-8') as stream:
        return from_dict(json.load(stream))


def active_profile(name: str, folder: str) -> Optional[MachineProfile]:
    """The profile of this name, or None: the name is empty or "None", or the file is missing or unreadable."""
    if not name or name == RESERVED_NAME:
        return None
    path = os.path.join(folder, _safe_filename(name))
    if not os.path.isfile(path):
        return None
    try:
        return load_profile(path)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def list_profiles(folder: str) -> List[MachineProfile]:
    """All the readable profiles of the folder, sorted by name. A file that is not a profile is skipped."""
    if not os.path.isdir(folder):
        return []
    found = []
    for entry in os.listdir(folder):
        if not entry.lower().endswith('.json'):
            continue
        try:
            found.append(load_profile(os.path.join(folder, entry)))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return sorted(found, key=lambda p: p.name)


def delete_profile(name: str, folder: str) -> bool:
    """Delete the profile file of this name. Return True if a file was removed."""
    path = os.path.join(folder, _safe_filename(name))
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False
