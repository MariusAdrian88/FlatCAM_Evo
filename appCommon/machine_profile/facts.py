# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Machine Profiles: the machine facts of a job (stages 1-2), the provenance of a job and its stale check.

`resolve_job_facts` returns a job-local snapshot of the options with the facts of the active profile written over
it. The options and the application defaults are never changed. The `cncjob_bed_*` keys keep working as now (D3).
No Qt, no app import.
"""

from typing import Callable, Optional

from shapely.geometry import Polygon

from appCommon.machine_profile.profile import MachineProfile, RESERVED_NAME, active_profile, content_hash, \
    resolve_profiles_folder

PROVENANCE_KEY = 'machine_profile'

# kind -> (toolchange XY key, toolchange Z key, end XY key, end Z key, preprocessor key, profile preprocessor field)
_KEYS = {
    'mill': ('tools_mill_toolchangexy', 'tools_mill_toolchangez', 'tools_mill_endxy', 'tools_mill_endz',
             'tools_mill_ppname_g', 'preprocessor_mill'),
    'photocarve': ('tools_mill_toolchangexy', 'tools_mill_toolchangez', 'tools_mill_endxy', 'tools_mill_endz',
                   'tools_mill_ppname_g', 'preprocessor_mill'),
    'drill': ('tools_drill_toolchangexy', 'tools_drill_toolchangez', 'tools_drill_endxy', 'tools_drill_endz',
              'tools_drill_ppname_e', 'preprocessor_drill'),
    'solderpaste': ('tools_solderpaste_xy_toolchange', 'tools_solderpaste_z_toolchange', None, None,
                    'tools_solderpaste_pp', 'preprocessor_solderpaste'),
}
_BED_KEYS = ('cncjob_bed_offset_x', 'cncjob_bed_offset_y', 'cncjob_bed_skew_x', 'cncjob_bed_skew_y')


def _xy(point) -> str:
    return '%s, %s' % (float(point[0]), float(point[1]))


def _to_work(station, work_offset) -> tuple:
    return tuple(float(m) - float(o) for m, o in zip(station, work_offset))


def profile_facts(kind: str, profile: Optional[MachineProfile]) -> dict:
    """The option keys that the profile sets for this kind of job, with the values in the format of each key."""
    if profile is None:
        return {}
    if kind not in _KEYS:
        raise ValueError('Unknown job kind: %s' % kind)
    tc_xy, tc_z, end_xy, end_z, pp_key, pp_field = _KEYS[kind]
    facts = {}
    # the stations are machine positions (M); the program gets them in the work frame: W = M - work offset
    if profile.toolchange is not None:
        station = _to_work(profile.toolchange, profile.work_offset)
        facts[tc_xy] = _xy(station)
        facts[tc_z] = station[2]
    if profile.end is not None and end_xy is not None:
        station = _to_work(profile.end, profile.work_offset)
        facts[end_xy] = _xy(station)
        facts[end_z] = station[2]
    if getattr(profile, pp_field) is not None:
        facts[pp_key] = getattr(profile, pp_field)
    return facts


def locked_values(profile: Optional[MachineProfile]) -> dict:
    """The option keys that an active profile overrides (all job kinds), with the profile value of each."""
    values = {}
    if profile is not None:
        for kind in _KEYS:
            values.update(profile_facts(kind, profile))
    return values


def locked_keys(profile: Optional[MachineProfile]) -> dict:
    """{option key: note} for each key whose profile field is set; empty without a profile."""
    if profile is None:
        return {}
    note = 'Set by machine profile "%s"' % profile.name
    return {key: note for key in locked_values(profile)}


def resolve_job_facts(kind: str, options: dict, profile: Optional[MachineProfile]) -> dict:
    """A new dict: the options with the machine facts of the profile over them. Profile None: a plain copy."""
    snapshot = dict(options)
    snapshot.update(profile_facts(kind, profile))
    return snapshot


def provenance(profile: MachineProfile, applied_facts: dict, bed_keys: dict) -> dict:
    """What the job records about its profile: {name, hash, facts, bed_legacy}."""
    return {'name': profile.name, 'hash': content_hash(profile), 'facts': dict(applied_facts),
            'bed_legacy': any(bool(bed_keys.get(key)) for key in _BED_KEYS)}


def job_provenance(kind: str, profile: Optional[MachineProfile], options: Callable) -> Optional[dict]:
    """The provenance of a job made now, or None when there is no active profile (nothing is stored then)."""
    if profile is None:
        return None
    # from stage 3 the job does not apply the cncjob_bed_* keys (CNCjob.apply_machine_profile zeroes them): the
    # calibration replaces them, so the record says bed_legacy false whatever the options hold
    return provenance(profile, profile_facts(kind, profile), {key: 0 for key in _BED_KEYS})


def is_stale(obj_options: dict, profile: Optional[MachineProfile]) -> Optional[bool]:
    """True when the profile changed since the job was made; None when the job has no provenance."""
    saved = obj_options.get(PROVENANCE_KEY)
    if not saved:
        return None
    if profile is None:
        return True
    return saved.get('hash') != content_hash(profile)


def mm_to_app_units(value: float, app_units: str) -> float:
    """A length in mm (profile values, check records) in the units of the app ('MM' or 'IN')."""
    return value / 25.4 if str(app_units).upper() == 'IN' else value


def permanent_areas(profile: Optional[MachineProfile], units: str) -> list:
    """
    The permanent exclusion areas of the profile as area records of the travel router, in the job frame and units.

    Stage 2 has no correction, so the job frame is W = M - work offset: each polygon is moved by -work_offset XY and
    the lengths (polygon, Over Z) go from mm to the units of the job. A record: {obj_type, shape, strategy, overz,
    name} with the strategy 'forbid', 'around' or 'over'. An 'over' area without an Over Z can not be crossed, so it
    is a 'forbid' area. Profile None or no areas: [].
    """
    if profile is None:
        return []
    dx, dy = float(profile.work_offset[0]), float(profile.work_offset[1])
    records = []
    for entry in profile.exclusions:
        points = entry.get('polygon', [])
        if len(points) < 3:
            continue
        shape = Polygon([(mm_to_app_units(float(x) - dx, units), mm_to_app_units(float(y) - dy, units))
                         for x, y in points])
        strategy = entry.get('strategy', 'forbid')
        over_z = entry.get('over_z')
        if strategy not in ('forbid', 'around', 'over') or (strategy == 'over' and over_z is None):
            strategy = 'forbid'
        records.append({'obj_type': 'profile', 'shape': shape, 'strategy': strategy,
                        'overz': None if over_z is None else mm_to_app_units(float(over_z), units),
                        'name': entry.get('name', '')})
    return records


def design_to_machine(points, work_offset, units: str) -> list:
    """Points in the design frame (app units) to the machine frame M in mm: M = design + work offset XY."""
    scale = mm_to_app_units(1.0, units)
    return [(x / scale + float(work_offset[0]), y / scale + float(work_offset[1])) for x, y in points]


def machine_to_design(points, work_offset, units: str) -> list:
    """Points in M (mm) to the design frame in app units: design = M - work offset XY."""
    scale = mm_to_app_units(1.0, units)
    return [((x - float(work_offset[0])) * scale, (y - float(work_offset[1])) * scale) for x, y in points]


def active_profile_of(app) -> Optional[MachineProfile]:
    """The active profile named by the app option `global_machine_profile`; None for 'None' or a missing profile.

    Reads the app keys like the file writers do (`app.options`, `app.data_path` only for the default folder).
    """
    name = app.options.get('global_machine_profile')
    if not name or name == RESERVED_NAME:
        return None
    folder = app.options.get('global_machine_profiles_folder') or ''
    return active_profile(name, resolve_profiles_folder(folder, '' if folder else app.data_path))


def project_field(profile: Optional[MachineProfile]) -> Optional[dict]:
    """The `machine_profile` field of a project file; None (nothing is written) without an active profile."""
    return None if profile is None else {'name': profile.name, 'hash': content_hash(profile)}


def project_mismatch(saved: Optional[dict], profile: Optional[MachineProfile]) -> Optional[str]:
    """The status bar line when the profile of a loaded project differs from the active one; None when it does not."""
    if not saved:
        return None
    if profile is None:
        return 'The project was made with the machine profile "%s", no profile is active.' % saved.get('name')
    if saved.get('name') != profile.name or saved.get('hash') != content_hash(profile):
        return 'The project was made with the machine profile "%s", which differs from the active profile "%s".' % (
            saved.get('name'), profile.name)
    return None
