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

from appCommon.machine_profile.motion import PROBES_LABELS, SPINDLE_ONLY
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


def _axis_text(value: Optional[float]) -> str:
    return 'unknown' if value is None else '%.3f' % value


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

    def check_extent(r, xmin, xmax, ymin, ymax, zmin, zmax, what):
        if xmin < bx0 - EPS or xmax > bx1 + EPS or ymin < by0 - EPS or ymax > by1 + EPS:
            add(ViolationKind.OUT_OF_BED, r.line,
                '%s leaves the bed: X %.3f..%.3f, Y %.3f..%.3f (bed X %.3f..%.3f, Y %.3f..%.3f)' %
                (what, xmin, xmax, ymin, ymax, bx0, bx1, by0, by1))
        if profile.z_travel and (zmin < -profile.z_travel - EPS or zmax > EPS):
            add(ViolationKind.Z_TRAVEL, r.line,
                'Z %.3f..%.3f is outside the travel 0..-%.3f' % (zmin, zmax, profile.z_travel))

    def check_cut_depth(r, z_values):
        z_values = [z for z in z_values if z is not None]
        if profile.cut_depth_max is None or r.motion_mode not in FEED_MODES or not z_values:
            return
        deepest = min(z_values)
        if deepest < -profile.cut_depth_max - EPS:
            add(ViolationKind.CUT_DEPTH, r.line,
                'cut depth %.3f is below the limit -%.3f' % (deepest, profile.cut_depth_max))

    def check_rpm(line, value):
        if profile.rpm_min is not None and value < profile.rpm_min - EPS:
            add(ViolationKind.RPM, line, 'S%.0f is below the minimum %.0f' % (value, profile.rpm_min))
        elif profile.rpm_max is not None and value > profile.rpm_max + EPS:
            add(ViolationKind.RPM, line, 'S%.0f is above the maximum %.0f' % (value, profile.rpm_max))

    def check_spindle(r):
        if r.s is not None:
            check_rpm(r.line, r.s)
        for line, value in r.s_events:
            # a speed equal to the speed of the move is reported on the move
            if value != r.s:
                check_rpm(line, value)

    feed_limited = profile.feed_xy_max is not None or profile.feed_z_max is not None

    def check_feed(r, xy_length, z_length, verified):
        if not feed_limited or (r.motion_mode not in FEED_MODES and r.motion_mode not in PROBES_LABELS):
            return
        if r.feed is None:
            if verified:
                add(ViolationKind.UNVERIFIED, r.line, 'feed unknown: the feed limit is not checked')
            return
        if xy_length is None or z_length is None:
            return
        # F is the feed along the path: each axis group moves at its share of it
        length = math.hypot(xy_length, z_length)
        if length <= EPS:
            return
        z_feed = r.feed * z_length / length
        xy_feed = r.feed * xy_length / length
        if profile.feed_z_max is not None and z_feed > profile.feed_z_max + EPS:
            add(ViolationKind.FEED, r.line, 'Z feed %.3f is above the limit %.3f' % (z_feed, profile.feed_z_max))
        if profile.feed_xy_max is not None and xy_feed > profile.feed_xy_max + EPS:
            add(ViolationKind.FEED, r.line, 'XY feed %.3f is above the limit %.3f' % (xy_feed, profile.feed_xy_max))

    def known_lengths(r):
        """The XY and Z lengths of a move from its W values (None = unknown): W - M is constant along a move."""
        sw, ew = r.start_w, r.end_w
        z_length = None if sw[2] is None or ew[2] is None else abs(ew[2] - sw[2])
        if r.no_xy_motion:
            xy_length = 0.0
        elif r.motion_mode in ('G2', 'G3') or None in (sw[0], sw[1], ew[0], ew[1]):
            xy_length = None
        else:
            xy_length = math.hypot(ew[0] - sw[0], ew[1] - sw[1])
        return xy_length, z_length

    first_rapid = None
    for r in records:
        current[0] = r
        if r.motion_mode == SPINDLE_ONLY:
            check_spindle(r)
            continue
        if r.motion_mode == 'G0' and first_rapid is None:
            first_rapid = r
        start_known = r.start_m is not None and None not in r.start_m
        end_known = r.end_m is not None and None not in r.end_m
        if not r.verified or not start_known or not end_known:
            # the path is unknown, but each known value is still checked: an unknown start or axis does not hide it
            add(ViolationKind.UNVERIFIED, r.line, r.unverified_reason or 'machine position unknown')
            axes = r.end_m if end_known else r.end_m_axes
            ex, ey, ez = axes if axes is not None else (None, None, None)
            if end_known:
                check_extent(r, ex, ex, ey, ey, ez, ez, 'end point')
            else:
                if ((ex is not None and (ex < bx0 - EPS or ex > bx1 + EPS)) or
                        (ey is not None and (ey < by0 - EPS or ey > by1 + EPS))):
                    add(ViolationKind.OUT_OF_BED, r.line, 'end point leaves the bed: X %s, Y %s (bed X %.3f..%.3f, '
                        'Y %.3f..%.3f)' % (_axis_text(ex), _axis_text(ey), bx0, bx1, by0, by1))
                if profile.z_travel and ez is not None and (ez < -profile.z_travel - EPS or ez > EPS):
                    add(ViolationKind.Z_TRAVEL, r.line,
                        'Z %.3f is outside the travel 0..-%.3f' % (ez, profile.z_travel))
            if ex is not None and ey is not None:
                # the tool is at least 0 wide: an end within the margin is in the area for every tool size
                reach = (r.tool_diameter or 0.0) / 2.0 + EXCLUSION_MARGIN
                for name, polygon, strategy, over_z in areas:
                    if strategy != 'forbid' and over_z is not None and (ez is None or ez >= over_z - EPS):
                        continue
                    if Point(ex, ey).distance(polygon) <= reach:
                        add(ViolationKind.EXCLUSION, r.line, 'the end point is in the exclusion area %s' % name,
                            area=name)
            xy_length, z_length = known_lengths(r)
            check_feed(r, xy_length, z_length, False)
            check_spindle(r)
            check_cut_depth(r, [r.start_w[2], r.end_w[2]])
            continue
        xmin, xmax, ymin, ymax, zmin, zmax = swept_bounds(r)
        check_extent(r, xmin, xmax, ymin, ymax, zmin, zmax, 'path')
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
        if r.arc_center is not None and r.arc_extent is not None:
            radius = math.hypot(r.start_m[0] - r.arc_center[0], r.start_m[1] - r.arc_center[1])
            xy_length = radius * r.arc_extent
        else:
            xy_length = math.hypot(r.end_m[0] - r.start_m[0], r.end_m[1] - r.start_m[1])
        check_feed(r, xy_length, abs(r.end_m[2] - r.start_m[2]), True)
        check_spindle(r)
        # Z is linear along a line and a helix: the deepest point of the move is one of its 2 ends
        check_cut_depth(r, [r.start_w[2], r.end_w[2]])
    if profile.rapid_max is not None and first_rapid is not None:
        # the G0 speed is a controller setting: a rapid cap can not be checked from the program alone
        current[0] = first_rapid
        add(ViolationKind.UNVERIFIED, first_rapid.line, 'rapid speed not checked: no controller data')
    return result
