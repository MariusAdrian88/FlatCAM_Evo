# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Machine Profiles: the check of interpreted motion records against a profile (bed, Z travel, process limits).

No Qt, no app import. Exclusion areas are checked against the swept path buffered by the tool radius + margin.
"""

import math
from typing import List, Optional, Tuple

from appCommon.machine_profile.profile import MachineProfile
from appCommon.machine_profile.records import CheckResult, MotionRecord, Violation, ViolationKind

from shapely.geometry import LineString, Point, Polygon

EPS = 1e-6
# the margin that AppExclusionAreas.grown_shape adds to the half tool diameter (mm)
EXCLUSION_MARGIN = 0.1
UNKNOWN_TOOL_REACH = 5.0
ARC_CHORD_ERROR = 0.001
TWO_PI = 2.0 * math.pi
FEED_MODES = ('G1', 'G2', 'G3')


def bed_rectangle(profile: MachineProfile) -> Tuple[float, float, float, float]:
    """The bed in M as (xmin, xmax, ymin, ymax)."""
    corner = profile.origin_corner
    x0, x1 = (-profile.bed_x, 0.0) if corner in ('br', 'tr') else (0.0, profile.bed_x)
    y0, y1 = (-profile.bed_y, 0.0) if corner in ('tl', 'tr') else (0.0, profile.bed_y)
    return x0, x1, y0, y1


def _in_sweep(angle: float, a0: float, extent: float, cw: bool) -> bool:
    delta = (a0 - angle) % TWO_PI if cw else (angle - a0) % TWO_PI
    return delta <= extent + 1e-12


def swept_bounds(record: MotionRecord) -> Tuple[float, float, float, float, float, float]:
    """Bounds (xmin, xmax, ymin, ymax, zmin, zmax) of the swept path of a verified record, in M."""
    sx, sy, sz = record.start_m
    ex, ey, ez = record.end_m
    xs, ys = [sx, ex], [sy, ey]
    if record.arc_center is not None and record.arc_extent is not None:
        cx, cy = record.arc_center[0], record.arc_center[1]
        radius = math.hypot(sx - cx, sy - cy)
        a0 = math.atan2(sy - cy, sx - cx)
        cw = record.arc_direction == 'cw'
        for k in range(4):
            angle = k * math.pi / 2.0
            if _in_sweep(angle, a0, record.arc_extent, cw):
                xs.append(cx + radius * math.cos(angle))
                ys.append(cy + radius * math.sin(angle))
    return min(xs), max(xs), min(ys), max(ys), min(sz, ez), max(sz, ez)


def swept_path(record: MotionRecord):
    """The XY centerline of a verified record as a shapely geometry (an arc is a polyline, chord error <= 0.001)."""
    sx, sy = record.start_m[0], record.start_m[1]
    ex, ey = record.end_m[0], record.end_m[1]
    if record.arc_center is None or record.arc_extent is None:
        return Point(sx, sy) if math.hypot(ex - sx, ey - sy) < EPS else LineString([(sx, sy), (ex, ey)])
    cx, cy = record.arc_center[0], record.arc_center[1]
    radius = math.hypot(sx - cx, sy - cy)
    if radius < EPS:
        return Point(sx, sy)
    step = 2.0 * math.acos(max(0.0, 1.0 - ARC_CHORD_ERROR / radius))
    count = max(2, int(math.ceil(record.arc_extent / max(step, 1e-3))))
    a0 = math.atan2(sy - cy, sx - cx)
    sign = -1.0 if record.arc_direction == 'cw' else 1.0
    points = [(cx + radius * math.cos(a0 + sign * record.arc_extent * k / count),
               cy + radius * math.sin(a0 + sign * record.arc_extent * k / count)) for k in range(1, count)]
    return LineString([(sx, sy)] + points + [(ex, ey)])


def check_motion(records: List[MotionRecord], profile: MachineProfile) -> CheckResult:
    result = CheckResult()
    modes = profile.rule_modes

    current = [None]

    def add(kind: ViolationKind, line: int, message: str, area: Optional[str] = None):
        record = current[0]
        result.violations.append(Violation(kind=kind, line=line, message=message, area=area,
                                           severity=modes.get(kind.value, 'warn'),
                                           start_w=record.start_w, end_w=record.end_w))

    areas = [(e.get('name', ''), Polygon(e['polygon']), e.get('strategy', 'forbid'), e.get('over_z'))
             for e in profile.exclusions if len(e.get('polygon', [])) >= 3]
    known_tools = [r.tool_diameter for r in records if r.tool_diameter]
    unknown_reach = EXCLUSION_MARGIN + (max(known_tools) if known_tools else UNKNOWN_TOOL_REACH)

    bx0, bx1, by0, by1 = bed_rectangle(profile)
    for r in records:
        current[0] = r
        if not r.verified:
            add(ViolationKind.UNVERIFIED, r.line, r.unverified_reason)
            continue
        if r.start_m is None or r.end_m is None or None in r.start_m or None in r.end_m:
            add(ViolationKind.UNVERIFIED, r.line, 'machine position unknown')
            continue
        xmin, xmax, ymin, ymax, zmin, zmax = swept_bounds(r)
        if xmin < bx0 - EPS or xmax > bx1 + EPS or ymin < by0 - EPS or ymax > by1 + EPS:
            add(ViolationKind.OUT_OF_BED, r.line,
                'path leaves the bed: X %.3f..%.3f, Y %.3f..%.3f (bed X %.3f..%.3f, Y %.3f..%.3f)' %
                (xmin, xmax, ymin, ymax, bx0, bx1, by0, by1))
        if profile.z_travel and (zmin < -profile.z_travel - EPS or zmax > EPS):
            add(ViolationKind.Z_TRAVEL, r.line,
                'Z %.3f..%.3f is outside the travel 0..-%.3f' % (zmin, zmax, profile.z_travel))
        if areas:
            path = swept_path(r)
            low_z = min(r.start_m[2], r.end_m[2])
            for name, polygon, strategy, over_z in areas:
                if strategy != 'forbid' and over_z is not None and low_z >= over_z - EPS:
                    continue
                distance = path.distance(polygon)
                if r.tool_diameter is None and distance > EPS:
                    # the centerline itself is outside: the violation depends on the unknown tool size
                    if distance <= unknown_reach:
                        add(ViolationKind.UNVERIFIED, r.line, 'tool size unknown near area %s' % name)
                    continue
                if distance <= (r.tool_diameter or 0.0) / 2.0 + EXCLUSION_MARGIN:
                    add(ViolationKind.EXCLUSION, r.line, 'the tool path enters the exclusion area %s' % name,
                        area=name)
        if r.motion_mode in FEED_MODES and r.feed is not None:
            z_only = (math.hypot(r.end_m[0] - r.start_m[0], r.end_m[1] - r.start_m[1]) < EPS
                      and abs(r.end_m[2] - r.start_m[2]) > EPS and r.arc_center is None)
            if z_only:
                if profile.feed_z_max is not None and r.feed > profile.feed_z_max + EPS:
                    add(ViolationKind.FEED, r.line,
                        'Z feed %.3f is above the limit %.3f' % (r.feed, profile.feed_z_max))
            elif profile.feed_xy_max is not None and r.feed > profile.feed_xy_max + EPS:
                add(ViolationKind.FEED, r.line, 'XY feed %.3f is above the limit %.3f' % (r.feed, profile.feed_xy_max))
        if r.s is not None:
            if profile.rpm_min is not None and r.s < profile.rpm_min - EPS:
                add(ViolationKind.RPM, r.line, 'S%.0f is below the minimum %.0f' % (r.s, profile.rpm_min))
            elif profile.rpm_max is not None and r.s > profile.rpm_max + EPS:
                add(ViolationKind.RPM, r.line, 'S%.0f is above the maximum %.0f' % (r.s, profile.rpm_max))
        if (profile.cut_depth_max is not None and r.motion_mode in FEED_MODES and r.end_w[2] is not None
                and r.end_w[2] < -profile.cut_depth_max - EPS):
            add(ViolationKind.CUT_DEPTH, r.line,
                'cut depth %.3f is below the limit -%.3f' % (r.end_w[2], profile.cut_depth_max))
    if profile.rapid_max is not None:
        result.assumptions.append('rapid speed not checked: no controller data')
    return result
