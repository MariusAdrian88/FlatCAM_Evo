#!/usr/bin/env python
# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing          #
# Pure interpolation helpers for the autolevelling feature.  #
#                                                              #
# No Qt imports, no app imports - standard library only, so   #
# these functions can be unit tested without the GUI stack.   #
# ##########################################################

import bisect
import math
import re


def build_bilinear_grid(points, tol=1e-6):
    """
    Build a rectangular bilinear-interpolation grid out of a cloud of
    probed (x, y, height) points.

    X values within `tol` of each other are grouped into a single grid
    column, and the same is done for Y values into grid rows (probe
    coordinates are rounded by dec_format, so small floating point noise
    like 10.0 vs 10.0000001 must still land in the same column/row).

    :param points: list of (x, y, height) tuples, in any order.
    :param tol: tolerance used to group close X/Y values together.
    :return: a tuple (xs, ys, grid) where xs and ys are sorted ascending
        lists of the grouped X/Y values and grid[j][i] is the height at
        (xs[i], ys[j]); or None if the points do not form a full
        rectangular grid (missing cell, duplicate cell, fewer than 2
        distinct X or Y values, or a point that does not snap to any
        group).
    """
    if not points:
        return None

    xs = _group_values(sorted(set(p[0] for p in points)), tol)
    ys = _group_values(sorted(set(p[1] for p in points)), tol)

    if len(xs) < 2 or len(ys) < 2:
        return None

    grid = [[None] * len(xs) for _ in range(len(ys))]

    for x, y, z in points:
        i = _snap_index(xs, x, tol)
        j = _snap_index(ys, y, tol)
        if i is None or j is None:
            return None
        if grid[j][i] is not None:
            # duplicate cell
            return None
        grid[j][i] = z

    for row in grid:
        for cell in row:
            if cell is None:
                # missing cell
                return None

    return xs, ys, grid


def _group_values(sorted_unique_values, tol):
    """
    Group sorted unique values that lie within `tol` of each other,
    collapsing each group to its mean.

    :param sorted_unique_values: ascending list of unique values.
    :param tol: grouping tolerance.
    :return: ascending list of grouped (mean) values.
    """
    groups = []
    current = []
    for v in sorted_unique_values:
        if current and (v - current[0]) > tol:
            groups.append(sum(current) / len(current))
            current = []
        current.append(v)
    if current:
        groups.append(sum(current) / len(current))
    return groups


def _snap_index(grouped_values, value, tol):
    """
    Find the index of the grouped value that `value` belongs to.

    :param grouped_values: ascending list of grouped values.
    :param value: value to snap.
    :param tol: grouping tolerance.
    :return: index into grouped_values, or None if no group is within tol.
    """
    idx = bisect.bisect_left(grouped_values, value)
    candidates = []
    if 0 <= idx < len(grouped_values):
        candidates.append(idx)
    if 0 <= idx - 1 < len(grouped_values):
        candidates.append(idx - 1)

    best_idx = None
    best_dist = None
    for c in candidates:
        d = abs(grouped_values[c] - value)
        if d <= tol and (best_dist is None or d < best_dist):
            best_idx = c
            best_dist = d
    return best_idx


def bilinear_offset(grid_data, x, y):
    """
    Compute the bilinearly interpolated height offset at (x, y) using a
    grid built by build_bilinear_grid.

    x and y are clamped into the grid's range before interpolation.

    :param grid_data: tuple (xs, ys, grid) as returned by
        build_bilinear_grid.
    :param x: query X coordinate.
    :param y: query Y coordinate.
    :return: interpolated height offset (float).
    """
    xs, ys, grid = grid_data

    x = min(max(x, xs[0]), xs[-1])
    y = min(max(y, ys[0]), ys[-1])

    i = bisect.bisect_right(xs, x) - 1
    i = min(max(i, 0), len(xs) - 2)
    j = bisect.bisect_right(ys, y) - 1
    j = min(max(j, 0), len(ys) - 2)

    x0, x1 = xs[i], xs[i + 1]
    y0, y1 = ys[j], ys[j + 1]

    tx = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    ty = 0.0 if y1 == y0 else (y - y0) / (y1 - y0)

    z00 = grid[j][i]
    z10 = grid[j][i + 1]
    z01 = grid[j + 1][i]
    z11 = grid[j + 1][i + 1]

    return (
        (1 - tx) * (1 - ty) * z00
        + tx * (1 - ty) * z10
        + (1 - tx) * ty * z01
        + tx * ty * z11
    )


def _catmull_rom(p0, p1, p2, p3, t):
    """Catmull-Rom spline value between p1 (t=0) and p2 (t=1)."""
    return p1 + 0.5 * t * (p2 - p0 + t * (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3 +
                                          t * (3.0 * (p1 - p2) + p3 - p0)))


def _neighbours(values, i):
    """
    Return the 4 values (p0, p1, p2, p3) around the interval [i, i+1] of a
    sequence. A missing neighbour is made by linear extrapolation, so a plane
    stays exact at the border: 2 * v[0] - v[1] before the first value and
    2 * v[n-1] - v[n-2] after the last one.
    """
    n = len(values)
    p1 = values[i]
    p2 = values[i + 1]
    p0 = values[i - 1] if i - 1 >= 0 else 2.0 * values[0] - values[1]
    p3 = values[i + 2] if i + 2 <= n - 1 else 2.0 * values[n - 1] - values[n - 2]
    return p0, p1, p2, p3


def bicubic_offset(grid_data, x, y):
    """
    Compute the bicubic (Catmull-Rom) interpolated height offset at (x, y)
    using a grid built by build_bilinear_grid.

    x and y are clamped into the grid's range, so the spline parameter is
    always in [0, 1] and the value is never extrapolated. At the border of
    the grid the missing neighbour is made by linear extrapolation of the
    2 nearest values, so a plane is reproduced exactly. The spline is
    applied on each row (X direction) and then on the 4 row results
    (Y direction).

    :param grid_data: tuple (xs, ys, grid) as returned by build_bilinear_grid.
        It must have at least 4 values on each axis (see bicubic_supported()).
    :param x: query X coordinate.
    :param y: query Y coordinate.
    :return: interpolated height offset (float).
    """
    xs, ys, grid = grid_data

    x = min(max(x, xs[0]), xs[-1])
    y = min(max(y, ys[0]), ys[-1])

    i = bisect.bisect_right(xs, x) - 1
    i = min(max(i, 0), len(xs) - 2)
    j = bisect.bisect_right(ys, y) - 1
    j = min(max(j, 0), len(ys) - 2)

    tx = 0.0 if xs[i + 1] == xs[i] else (x - xs[i]) / (xs[i + 1] - xs[i])
    ty = 0.0 if ys[j + 1] == ys[j] else (y - ys[j]) / (ys[j + 1] - ys[j])

    # only the rows around the interval [j, j+1] are needed
    lo = max(j - 1, 0)
    hi = min(j + 2, len(ys) - 1)
    row_values = [_catmull_rom(*_neighbours(r, i), tx) for r in grid[lo:hi + 1]]
    return _catmull_rom(*_neighbours(row_values, j - lo), ty)


def _equal_spacing(values, rel_tol):
    steps = [b - a for a, b in zip(values, values[1:])]
    mean_step = sum(steps) / len(steps)
    return all(abs(step - mean_step) <= rel_tol * mean_step for step in steps)


def bicubic_supported(grid_data, rel_tol=1e-6):
    """
    :return: True if the grid has at least 4 values on each axis and equal
        spacing on each axis.
    """
    if grid_data is None:
        return False
    xs, ys = grid_data[0], grid_data[1]
    if len(xs) < 4 or len(ys) < 4:
        return False
    return _equal_spacing(xs, rel_tol) and _equal_spacing(ys, rel_tol)


def nearest_offset(points, x, y):
    """
    Return the height of the point in `points` nearest to (x, y).

    :param points: list of (x, y, height) tuples.
    :param x: query X coordinate.
    :param y: query Y coordinate.
    :return: height (float) of the nearest point. If two points are at
        equal distance, the first one in the list wins.
    :raises ValueError: if `points` is empty.
    """
    if not points:
        raise ValueError("nearest_offset() requires a non-empty list of points")

    best_z = None
    best_dist = None
    for px, py, pz in points:
        d = math.hypot(px - x, py - y)
        if best_dist is None or d < best_dist:
            best_dist = d
            best_z = pz
    return best_z


def _bicubic_control_bounds(grid_data):
    """
    :return: (min, max) of the Catmull-Rom Bezier control values of every
        X-direction piece of the grid, or (None, None) for an empty grid.
        The spline stays inside the convex hull of these values (also at the
        borders, where _neighbours() extrapolates a missing value).
    """
    xs, _ys, grid = grid_data
    low = high = None
    for row in grid:
        for i in range(len(xs) - 1):
            p0, p1, p2, p3 = _neighbours(row, i)
            for value in (p1, p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2):
                if low is None or value < low:
                    low = value
                if high is None or value > high:
                    high = value
    return low, high


def conservative_ceiling(points=None, grid_data=None, bicubic=False):
    """
    Return a provable upper bound of the interpolated height over the whole
    map, or None if there is no map data.

    The bound is used for the XY plane of an expanded canned cycle: every
    point of the segment between 2 holes must be cleared, so a bound that is
    only sampled at the ends (or at a few points) is not enough.

    - nearest point: the highest probe point (the nearest height is always
      the height of 1 of the points);
    - bilinear: the highest grid value (a bilinear patch is monotone along
      each axis, so its maximum over a cell is at a corner);
    - bicubic (Catmull-Rom): the spline stays inside the convex hull of its
      Bezier control values. The Y direction can go past the highest X
      control by at most 1/3 of their range (also with the border
      extrapolation of _neighbours()), so half the range is a safe margin.

    :param points: list of (x, y, height) probe points, or None.
    :param grid_data: (xs, ys, grid) as returned by build_bilinear_grid, or None.
    :param bicubic: True if bicubic_offset() is the interpolation in use.
    :return: a float upper bound, or None.
    """
    if grid_data is not None:
        xs, _ys, grid = grid_data
        values = [value for row in grid for value in row]
        if not values:
            return None
        if bicubic:
            low, high = _bicubic_control_bounds(grid_data)
            if high is None:
                return None
            return high + (high - low) / 2.0
        return max(values)
    if points:
        return max(point[2] for point in points)
    return None


def parse_height_map_line(line):
    """
    Parse 1 row of a probe file written by a CNC controller.

    MACH3/MACH4 write comma-separated ``X,Y,Z[,A,B,C]`` rows; LinuxCNC's
    PROBEOPEN log writes space-separated ``X Y Z A B C U V W`` rows. Extra
    columns beyond X, Y, Z are ignored.

    :param line: 1 line of text (no trailing newline required).
    :return: (x, y, z) tuple of floats, or None if the line is blank or
        cannot be parsed.
    """
    stripped = line.strip()
    if not stripped:
        return None

    tokens = [t for t in re.split(r'[,\s]+', stripped) if t != '']
    if len(tokens) < 3:
        return None

    try:
        x = float(tokens[0])
        y = float(tokens[1])
        z = float(tokens[2])
    except ValueError:
        return None

    if not all(math.isfinite(value) for value in (x, y, z)):
        return None

    return x, y, z


def match_nearest_point(points, x, y, tol):
    """
    Find the key of the point in `points` nearest to (x, y), within a
    tolerance.

    :param points: dict of {key: (px, py)}.
    :param x: query X coordinate.
    :param y: query Y coordinate.
    :param tol: maximum Euclidean distance to accept a match.
    :return: the key of the nearest point if its distance is <= tol,
        else None. Ties resolve to whichever key comes first in
        `points`'s iteration order.
    """
    best_key = None
    best_dist = None
    for key, (px, py) in points.items():
        d = math.hypot(px - x, py - y)
        if d <= tol and (best_dist is None or d < best_dist):
            best_dist = d
            best_key = key
    return best_key


def match_tolerance(points_xy, cap):
    """
    Compute a tolerance suitable for matching a probed point back to the
    nearest point in `points_xy` (see match_nearest_point()): half of the
    minimum Euclidean distance between any 2 distinct points, capped at
    `cap` so that widely spaced probe points don't produce an overly
    generous tolerance.

    With fewer than 2 points there is no pairwise distance to measure, so
    `cap` is returned directly.

    :param points_xy: iterable of (x, y) tuples.
    :param cap: maximum tolerance to return.
    :return: tolerance (float), always <= cap.
    """
    pts = list(points_xy)
    if len(pts) < 2:
        return cap

    min_dist = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if min_dist is None or d < min_dist:
                min_dist = d

    return min(min_dist / 2, cap)


_MOTION_G_CODES = (0.0, 1.0, 2.0, 3.0)
# Other members of the motion modal group: probing (G38.2/.3/.4/.5,
# G31 - MACH3/MACH4) and G80 (cancel motion). Selecting 1 of these puts
# the motion modal group in a state that is not G0/G1/G2/G3, so a
# following bare "X.. Y.." line must NOT be treated as a levelled G1
# move until a real G0/G1/G2/G3 is seen again.
_MOTION_NO_LEVEL_G_CODES = (31.0, 38.2, 38.3, 38.4, 38.5, 73.0, 76.0, 80.0,
                            81.0, 82.0, 83.0, 84.0, 85.0, 86.0, 87.0, 88.0, 89.0)
# G81/G82/G83 are expanded by _level_cycle(); the other canned cycles above are
# not supported and are counted in state['cycles'].
# Non-modal (one-shot) codes: their X/Y/Z words are not moves to work
# coordinates (machine-coordinate move, coordinate-system offset, ...),
# so they must not update the modal X/Y/Z state, and the line itself is
# never height-compensated.
_NON_MODAL_G_CODES = (10.0, 28.0, 30.0, 53.0, 92.0, 92.1, 92.2, 92.3)
# Modal settings that can stand on the same line as a move (plane, units,
# cutter compensation off, tool length off, work coordinate system, path
# mode, distance mode, feed mode). They do not make the line a non-motion
# command: "G90 X1 Y1" under a modal G1 is still a G1 move.
_MODAL_SETTING_G_CODES = (17.0, 18.0, 19.0, 20.0, 21.0, 40.0, 49.0, 54.0, 55.0, 56.0, 57.0,
                          58.0, 59.0, 59.1, 59.2, 59.3, 61.0, 64.0, 90.0, 90.1, 91.0, 91.1,
                          93.0, 94.0, 41.0, 41.1, 42.0, 42.1, 54.1, 70.0, 71.0)
# The units words (G20/G70 inch, G21/G71 mm; G70/G71 in ISEL, Siemens and
# Heidenhain dialects) and the work coordinate system words (G54..G59.3; also
# Mach3 "G59 P<n>" and Fanuc "G54.1 P<n>"). The height map is valid only in the
# frame of the probing (the units and the coordinate system), see _note_frame_words().
_UNITS_BY_G = {20.0: 20, 21.0: 21, 70.0: 20, 71.0: 21}
_WCS_G_CODES = (54.0, 55.0, 56.0, 57.0, 58.0, 59.0, 59.1, 59.2, 59.3)
# Canned cycles (peck, drill, tap, bore; LinuxCNC G76 threading). G81/G82/G83
# are expanded into explicit motions (see _level_cycle()); the others are not
# compensated and are counted in state['cycles'].
_CANNED_CYCLE_G_CODES = (73.0, 76.0, 81.0, 82.0, 83.0, 84.0, 85.0, 86.0, 87.0, 88.0, 89.0)
# Canned cycles supported by the expansion: drill, dwell and peck.
_SUPPORTED_CYCLE_G_CODES = (81.0, 82.0, 83.0)
# Letters a supported canned cycle block may carry; L (repetitions) is not one.
_CYCLE_BLOCK_LETTERS = frozenset('GXYZRQPFN')
# The upper bound on the pecks of 1 G83 hole (a tiny Q can not make an endless program).
_CYCLE_MAX_PECKS = 10000
# The return-level words of a canned cycle: G98 initial plane, G99 R plane.
_CYCLE_RETURN_G_CODES = (98.0, 99.0)
# G codes that do not move the machine and that need nothing more than their
# own rules: dwell, stored positions, tool length, offsets, scale, rotation,
# subprogram call, exact path, feed and spindle modes, canned cycle return level.
_KNOWN_STILL_G_CODES = (4.0, 28.1, 30.1, 43.0, 43.1, 43.2, 50.0, 51.0, 51.1, 52.0, 61.1, 65.0,
                        68.0, 69.0, 95.0, 96.0, 97.0, 98.0, 99.0, 7.0, 8.0, 15.0, 16.0)
# Codes that change the meaning of X/Y for the rest of the program: scale (G51),
# mirror (G51.1), rotation (G68), polar coordinates (G16), lathe diameter mode (G7).
_FRAME_CHANGE_G_CODES = (7.0, 16.0, 51.0, 51.1, 68.0)
# Probe moves that stop at a contact with the work: LinuxCNC/GRBL G38.2 (an error
# if there is no contact) and Mach3 G31 (Toolchange_Probe_MACH3).
_CONTACT_PROBE_G_CODES = (31.0, 38.2)
# Frame value that no word can ever equal: the frame of the probing is not known
# by name and an optional line made the current frame unknown.
_FRAME_UNKNOWN = 'unknown'
_WORD_RE = re.compile(r'([A-Z])\s*([+\-]?(?:\d+\.?\d*|\.\d+))')
# Start of a line that must stay in front of any G word: whitespace, a block
# delete "/", leading comments and a line number "N<digits>" (RS274NGC wants N first).
_LINE_LEAD_RE = re.compile(r'\s*(?:/\s*)?(?:\([^)]*\)\s*)*(?:N\s*\d+\s*)?')
# A block delete line: "/" is the first character that is not a space (a "/" in
# a comment is not one).
_BLOCK_DELETE_RE = re.compile(r'\s*/')
# Letters an arc line may carry and still be split into line moves.
_ARC_SPLIT_LETTERS = frozenset('GXYZIJKRFP')
# Largest angle of 1 arc segment, and the largest number of segments of 1 arc.
_ARC_MAX_ANGLE = math.pi / 18.0
_ARC_MAX_SEGMENTS = 10000
# A sweep below this angle (radians) is a full circle (GRBL ARC_ANGULAR_TRAVEL_EPSILON).
_ARC_FULL_CIRCLE_EPS = 5e-7
# Modal keys that an optional (block delete) line can change; after such a line
# each changed key is unknown (None), because the controller may skip the line.
_BLOCK_DELETE_MODAL_KEYS = ('abs', 'plane', 'arc_abs', 'inverse_feed', 'G', 'units', 'wcs',
                            'cutter_comp', 'cycle', 'cycle_code', 'cycle_return', 'cycle_r',
                            'cycle_z', 'cycle_q', 'cycle_p', 'cycle_initial_z', 'cycle_initial_phys',
                            'feed')
# Cutter radius compensation on (G41/G42, dynamic G41.1/G42.1): the tool moves
# off the programmed X/Y by its radius, so the map is read at the wrong point.
_CUTTER_COMP_G_CODES = (41.0, 41.1, 42.0, 42.1)
# A subprogram call or flow control: a LinuxCNC O-word with a keyword ("O100 call",
# "O<name> sub", "O101 while", ...) or Mach3/Fanuc "M98". The lines of the file do not
# then run once in their order. A plain program number ("O1234") is not one.
_SUBPROGRAM_CALL_RE = re.compile(
    r'(?:\bO\s*(?:\d+|<[^>]*>)\s*(?:CALL|SUB|ENDSUB|RETURN|WHILE|ENDWHILE|DO|IF|ELSEIF|ELSE|ENDIF'
    r'|REPEAT|ENDREPEAT|BREAK|CONTINUE)\b|(?<![A-Z])M\s*0*98(?![\d.]))')


def new_levelling_state(arc_segment=1.0, units=None, map_bounds=None, home_wcs=None):
    """
    Create a fresh modal G-code state for use with level_gcode_line.

    The position is not known at the start: X, Y and Z are None until
    absolute words set them, so a cutting move is compensated only when
    X, Y and Z are all set (before the line or on it).

    The height map is valid only in the frame of the probing: the units and
    the work coordinate system (WCS). `home_units`/`home_wcs` are the frame
    of the map (None: not known by name), `units`/`wcs` the frame now in
    effect. A cutting move in another frame is not compensated.

    :param arc_segment: largest chord length of 1 segment of a split arc
        (G-code units), must be > 0.
    :param units: the units of the job and of the height map: 'MM', 'IN'
        (any case) or None if they are not known. The program is taken to run
        in these units until a G20/G21 word says otherwise.
    :param map_bounds: (xmin, ymin, xmax, ymax) of the probe points, or None.
        A Z zero change after a contact probe inside these bounds re-references
        the map to the probed point (see _note_z_zero()); with None it can not.
    :param home_wcs: the WCS of the height map as in `_WCS_G_CODES` (the G number,
        e.g. 54.0), or None: the first WCS word of the program declares it. If it
        is given, `state['home_wcs']` and `state['wcs']` are set to it.
    :return: dict with modal X/Y/Z position, modal motion G code, and the
        counters `arcs` (arc moves that were not compensated),
        `relative` (G91 G1 moves that were not compensated),
        `arcs_split` (arcs that were split into G1 segments),
        `unparsed` (lines with words that are not understood),
        `unknown` (cutting moves that were not compensated because the
        position, the distance mode or the motion mode was unknown) and
        `frame` (G1 moves that were not compensated because they are in
        another WCS or in other units, or after a change of the work origin),
        `cutter` (G1 moves under G41/G42) and `cycles` (canned cycle blocks or
        parameters that are not supported, so they were not compensated),
        so the caller can warn/report on them.
    :raises ValueError: if `arc_segment` is not > 0.
    """
    if not arc_segment > 0:
        raise ValueError("arc_segment must be > 0")
    home_units = {'MM': 21, 'IN': 20}.get(str(units).upper()) if units is not None else None
    # abs: True for G90 (absolute), False for G91 (relative)
    # plane: 17/18/19 (G17/G18/G19)
    # arc_abs: True for G90.1 (absolute I/J), False for G91.1 (incremental I/J)
    # inverse_feed: True for G93
    # each of abs/plane/arc_abs/inverse_feed/G is None when it is unknown (after
    #   an optional block delete line that changes it)
    # applied: the Z offset now in effect, (machine Z that the emitted code
    #   reaches) minus (programmed Z, state['Z'])
    # known: the axes whose work position was set by an absolute G0..G3 word
    # g_unknown: True when G is None because the motion mode is unknown (after an
    #   optional or a not understood line), not because of a probe/canned cycle code
    # units: 21 (G21, mm) or 20 (G20, inch); wcs: the G number of the WCS word (54..59.3)
    # home_units/home_wcs: the units and the WCS of the height map; None when not known
    #   by name (then the first word before any move declares it, see _note_frame_words)
    # units/wcs: now in effect; None when unknown (after an optional line that changes them)
    # moved: True after the first line that can move the machine; after it no word
    #   can declare the frame of the map
    # shifted: True after a line that moves the X/Y work origin (G92, G10 L2/L20, ...),
    #   a G word that is not read or a subprogram call; the rest of the program is out
    #   of the frame of the map
    # cutter_comp: True under G41/G42 (None when unknown); cutter: G1 moves not
    #   compensated because of it
    # cycle: True while a canned cycle is the motion mode (None when unknown)
    # cycle_code: 81, 82 or 83 for a supported cycle, the G number of an
    #   unsupported cycle, or None when no cycle is active
    # cycle_return: 98 (initial plane) or 99 (R plane), None when unknown
    # cycle_r/cycle_z/cycle_q/cycle_p: the last resolved (absolute) cycle values
    # cycle_initial_z: the programmed absolute Z of the initial plane of the active cycle
    # cycle_initial_phys: the machine Z of the initial plane, where the cycle started
    # map_ceiling: a provable upper bound of the height map over the whole map, or None;
    #   it lets an expanded cycle clear every point of an XY segment, not just its ends
    # feed: the modal F value, None when it is not known or not positive
    # z_ref: the map height of the point where a Z zero change set the Z reference
    #   (minus the new Z value), subtracted from every offset; 0.0 at the start
    # probe_xy: the X/Y of the last contact probe move, until the next line that moves
    # map_bounds: (xmin, ymin, xmax, ymax) of the probe points, or None
    state = {'X': None, 'Y': None, 'Z': None, 'G': 0, 'arcs': 0, 'abs': True, 'relative': 0,
            'plane': 17, 'arc_abs': False, 'inverse_feed': False, 'applied': 0.0,
            'known': set(), 'arc_segment': float(arc_segment), 'arcs_split': 0, 'unparsed': 0,
            'unknown': 0, 'g_unknown': False, 'frame': 0,
            'home_units': home_units, 'units': home_units, 'home_wcs': None, 'wcs': None,
            'moved': False, 'shifted': False, 'cutter_comp': False, 'cutter': 0,
            'cycle': False, 'cycle_code': None, 'cycle_return': 98.0, 'cycle_r': None,
            'cycle_z': None, 'cycle_q': None, 'cycle_p': None, 'cycle_initial_z': None,
            'cycle_initial_phys': None,
            'cycles': 0, 'feed': None, 'z_ref': 0.0, 'probe_xy': None,
            'map_bounds': tuple(map_bounds) if map_bounds is not None else None,
            'map_ceiling': None}
    if home_wcs is not None:
        state['home_wcs'] = home_wcs
        state['wcs'] = home_wcs
    return state


def _mask_comments(line):
    """
    Replace comment text with spaces of the same length, so word
    positions in the masked string still line up with the original
    line (needed to locate/replace the Z word's number in-place).

    :param line: raw G-code line.
    :return: line with `(...)` and trailing `;...` comments blanked out.
    """
    masked = re.sub(r'\([^)]*\)', lambda m: ' ' * len(m.group(0)), line)
    idx = masked.find(';')
    if idx != -1:
        masked = masked[:idx] + ' ' * (len(masked) - idx)
    return masked


def _comment_text(line):
    """
    Collect the comments of a line: every `(...)` comment before a `;`
    and the `;...` tail, joined with a space.

    :param line: raw G-code line.
    :return: the comment text, or '' if the line has no comment.
    """
    no_parens = re.sub(r'\([^)]*\)', lambda m: ' ' * len(m.group(0)), line)
    idx = no_parens.find(';')
    head = line if idx == -1 else line[:idx]
    parts = re.findall(r'\([^)]*\)', head)
    if idx != -1:
        parts.append(line[idx:].rstrip())
    return ' '.join(parts)


def _fmt(value, decimals):
    """
    Format a number with `decimals` decimals, never as a negative zero.

    :param value: number to format.
    :param decimals: number of decimals.
    :return: the formatted text.
    """
    text = "{0:.{1}f}".format(value, decimals)
    if float(text) == 0.0:
        text = text.lstrip('-')
    return text


def _insert_z_word(line, new_z_str, z_match):
    """
    Write `new_z_str` into `line`: replace the existing Z word's number
    if there is one, else append a new " Z<value>" word after the last word
    of the line (before a trailing comment, after a leading comment).

    :param line: original line text (with its original comment, if
        any, untouched).
    :param new_z_str: formatted new Z value.
    :param z_match: the `_WORD_RE` match object for the line's Z word
        (matched against the comment-masked line, so its span lines up
        with `line`), or None if the line has no Z word.
    :return: the line with the Z value inserted/replaced.
    """
    if z_match is not None:
        start, end = z_match.span(2)
        return line[:start] + new_z_str + line[end:]

    # after the last word, before a trailing comment; a leading comment stays in front
    end = len(_mask_comments(line).rstrip())
    suffix = line[end:]
    if suffix.strip():
        return line[:end] + " Z" + new_z_str + " " + suffix.lstrip()
    return line[:end] + " Z" + new_z_str


def _note_modal_settings(state, g_words):
    """
    Update the distance mode, the plane, the arc distance mode and the
    feed mode of `state` from the G words of a line.

    :param state: modal state dict; mutated in place.
    :param g_words: the G numbers of the line, in order.
    """
    # G90/G91 change the distance mode also on a line with a non-modal
    # code (for example "G91 G28 Z0"), so this runs before anything else
    if 91.0 in g_words:
        state['abs'] = False
    if 90.0 in g_words:
        state['abs'] = True
    for g in g_words:
        if g in (17.0, 18.0, 19.0):
            state['plane'] = int(g)
        elif g == 90.1:
            state['arc_abs'] = True
        elif g == 91.1:
            state['arc_abs'] = False
        elif g == 93.0:
            state['inverse_feed'] = True
        elif g == 94.0:
            state['inverse_feed'] = False
        elif g == 98.0:
            state['cycle_return'] = 98.0
        elif g == 99.0:
            state['cycle_return'] = 99.0


def _lose_axes(state, axes):
    """
    Mark work axes as unknown (None) and no longer known.

    :param state: modal state dict; mutated in place.
    :param axes: iterable of 'X'/'Y'/'Z'.
    """
    for axis in axes:
        state[axis] = None
        state['known'].discard(axis)


def _all_known(state):
    """
    :return: True if X, Y and Z were all set by an absolute word and are not None.
    """
    return all(axis in state['known'] and state[axis] is not None for axis in ('X', 'Y', 'Z'))


def _note_frame_word(state, key, value):
    """
    Apply 1 units word or WCS word to the frame of `state`.

    A first word before any move, when the frame of the map is not known by
    name, declares the frame of the map. A word after a move never declares
    it, also when the position is lost (G28, G53, ...): the moves before the
    word were in the frame that is active at the start. A word that changes
    the frame makes all work positions unknown.

    :param state: modal state dict; mutated in place.
    :param key: 'units' or 'wcs'.
    :param value: the new units (20/21) or WCS (G number) in effect.
    """
    home_key = 'home_' + key
    if state[home_key] is None and state[key] is None and not state['moved']:
        state[home_key] = value
    if value != state[key]:
        _lose_axes(state, ['X', 'Y', 'Z'])
    state[key] = value


def _note_frame_words(state, g_words, p_word=None, p_seen=False, p_known=True):
    """
    Update the units and the work coordinate system of `state` from the
    G words of a line.

    Mach3 "G59 P<n>" and Fanuc "G54.1 P<n>" select more coordinate systems:
    each P value is its own WCS. If the P value can not be read, the WCS is
    a new value that no other word can equal.

    :param state: modal state dict; mutated in place.
    :param g_words: the G numbers of the line, in order.
    :param p_word: the value of the P word of the line, or None.
    :param p_seen: True if the line has a P letter.
    :param p_known: False if the line is not understood (the P value can be an
        expression).
    """
    for g in g_words:
        if g in _UNITS_BY_G:
            _note_frame_word(state, 'units', _UNITS_BY_G[g])
        elif g == 54.1 or (g == 59.0 and p_seen):
            value = (g, p_word) if p_known and p_word is not None else (g, object())
            _note_frame_word(state, 'wcs', value)
        elif g in _WCS_G_CODES:
            _note_frame_word(state, 'wcs', g)


def _in_map(state, point):
    """
    :return: True if `point` (x, y) is inside the bounds of the probe points.
    """
    bounds = state['map_bounds']
    if bounds is None:
        return False
    xmin, ymin, xmax, ymax = bounds
    eps = 1e-9 * max(1.0, abs(xmax - xmin), abs(ymax - ymin))
    return xmin - eps <= point[0] <= xmax + eps and ymin - eps <= point[1] <= ymax + eps


def _referenced(offset_fn, z_ref):
    """
    :return: the height map `offset_fn` minus `z_ref` (see _note_z_zero()).
    """
    def referenced(x, y):
        return offset_fn(x, y) - z_ref
    return referenced


def _note_z_zero(state, z_value, offset_fn):
    """
    Apply a Z zero change: the Z position now in effect becomes `z_value`
    (G92 Z, G10 L20 Z).

    If the change comes right after a contact probe move (G38.2, G31) at a
    known X/Y inside the map, the probe touched the work surface at that point,
    whose map height is offset_fn(point). From now on a point of the surface is
    at offset_fn(x, y) - offset_fn(point) + z_value, so `state['z_ref']` gets
    offset_fn(point) - z_value. Else the Z reference of the map is lost and the
    rest of the program is out of the frame of the map.

    :param state: modal state dict; mutated in place.
    :param z_value: the new Z value, or None if it can not be read.
    :param offset_fn: the height map, not re-referenced.
    """
    point = state['probe_xy']
    # the probe and the zero must be in the units and the WCS of the map: "G20 G92 Z2"
    # sets 2 inch, and G92 on another WCS gives an unknown Z offset to the map WCS
    if (z_value is not None and point is not None and _in_map(state, point)
            and _in_frame(state)):
        state['z_ref'] = offset_fn(point[0], point[1]) - z_value
    else:
        state['shifted'] = True


def _in_frame(state):
    """
    :return: True if the units and the WCS in effect are those of the height map
        and no line moved the X/Y work origin.
    """
    return (state['units'] == state['home_units'] and state['wcs'] == state['home_wcs']
            and not state['shifted'])


def _arc_centre(start, end, words, clockwise, arc_abs, decimals):
    """
    Centre of an arc in the XY plane.

    :param start: (x, y, z) start point.
    :param end: (x, y, z) end point.
    :param words: dict letter -> value of the arc line.
    :param clockwise: True for G2.
    :param arc_abs: True if I/J are absolute coordinates (G90.1).
    :param decimals: number of decimals of the output.
    :return: (cx, cy), or None if the line gives no valid centre (no I/J
        and no R, both of them, an R arc with equal ends, or an R too
        small for the chord).
    """
    has_ij = 'I' in words or 'J' in words
    if has_ij and 'R' in words:
        return None
    if has_ij:
        i_val, j_val = words.get('I', 0.0), words.get('J', 0.0)
        if arc_abs:
            return i_val, j_val
        return start[0] + i_val, start[1] + j_val
    if 'R' not in words:
        return None

    radius = words['R']
    dx, dy = end[0] - start[0], end[1] - start[1]
    chord = math.hypot(dx, dy)
    if chord <= 10 ** -decimals or radius == 0.0:
        return None
    if chord > 2.0 * abs(radius) + max(0.001 * abs(radius), 5 * 10 ** -decimals):
        return None
    height = math.sqrt(max(0.0, radius * radius - chord * chord / 4.0))
    # the centre of the short arc is to the left of the chord for G3 and to
    # the right for G2; a negative R selects the long arc (the other side)
    side = -1.0 if clockwise else 1.0
    if radius < 0.0:
        side = -side
    return (start[0] + dx / 2.0 - side * height * dy / chord,
            start[1] + dy / 2.0 + side * height * dx / chord)


def _arc_sweep(start, end, centre, clockwise, decimals):
    """
    Radius and signed sweep angle of an arc.

    :param start: (x, y, z) start point.
    :param end: (x, y, z) end point.
    :param centre: (cx, cy).
    :param clockwise: True for G2.
    :param decimals: number of decimals of the output.
    :return: (radius, sweep) with sweep < 0 for G2, or None if the start
        radius is about 0 or differs from the end radius by more than
        max(0.1 %, 5 * 10**-decimals). An arc whose sweep is below
        _ARC_FULL_CIRCLE_EPS (equal start and end angles) is a full circle,
        as in GRBL; the output resolution never decides this, because ends
        1 output unit apart are a short arc.
    """
    r_start = math.hypot(start[0] - centre[0], start[1] - centre[1])
    r_end = math.hypot(end[0] - centre[0], end[1] - centre[1])
    if r_start < 10 ** -decimals:
        return None
    if abs(r_start - r_end) > max(0.001 * r_start, 5 * 10 ** -decimals):
        return None
    a_start = math.atan2(start[1] - centre[1], start[0] - centre[0])
    a_end = math.atan2(end[1] - centre[1], end[0] - centre[0])
    full = 2.0 * math.pi
    if clockwise:
        sweep = (a_start - a_end) % full
    else:
        sweep = (a_end - a_start) % full
    if sweep < _ARC_FULL_CIRCLE_EPS:
        sweep = full
    return r_start, -sweep if clockwise else sweep


def _arc_points(start, end, centre, radius, sweep, arc_segment):
    """
    The points of an arc cut into segments of at most `arc_segment` length
    and at most 10 degrees (and at most 10000 segments).

    :param start: (x, y, z) start point.
    :param end: (x, y, z) end point; Z is interpolated linearly (helix).
    :param centre: (cx, cy).
    :param radius: radius of the circle through the start point.
    :param sweep: signed sweep angle, negative for clockwise.
    :param arc_segment: largest chord length of 1 segment.
    :return: list of (x, y, z), the last one is exactly `end`.
    """
    count = max(1,
                math.ceil(radius * abs(sweep) / arc_segment - 1e-9),
                math.ceil(abs(sweep) / _ARC_MAX_ANGLE - 1e-9))
    count = min(count, _ARC_MAX_SEGMENTS)
    a_start = math.atan2(start[1] - centre[1], start[0] - centre[0])
    points = []
    for i in range(1, count):
        angle = a_start + sweep * i / count
        points.append((centre[0] + radius * math.cos(angle),
                       centre[1] + radius * math.sin(angle),
                       start[2] + (end[2] - start[2]) * i / count))
    points.append((end[0], end[1], end[2]))
    return points


def _arc_lines(points, start, z_actual, absolute, offset_fn, decimals):
    """
    Format the points of an arc as G1 lines with height compensation.

    In G91 the deltas are taken between the ROUNDED positions, so the
    rounding errors do not accumulate; the Z delta of each segment brings
    the machine to the compensated height of its point.

    :param points: list of (x, y, z) from _arc_points().
    :param start: (x, y, z) start point (programmed).
    :param z_actual: machine Z at the start.
    :param absolute: True for G90.
    :param offset_fn: callable (x, y) -> height offset.
    :param decimals: number of decimals of the output.
    :return: (list of "G1 X.. Y.. Z.." texts, machine Z after the last one).
    """
    lines = []
    prev_x, prev_y = float(_fmt(start[0], decimals)), float(_fmt(start[1], decimals))
    for x, y, z in points:
        desired = z + offset_fn(x, y)
        if absolute:
            x_str, y_str, z_str = _fmt(x, decimals), _fmt(y, decimals), _fmt(desired, decimals)
            z_actual = float(z_str)
        else:
            cur_x, cur_y = float(_fmt(x, decimals)), float(_fmt(y, decimals))
            x_str, y_str = _fmt(cur_x - prev_x, decimals), _fmt(cur_y - prev_y, decimals)
            z_step = float(_fmt(desired - z_actual, decimals))
            z_str = _fmt(z_step, decimals)
            z_actual += z_step
            prev_x, prev_y = cur_x, cur_y
        lines.append("G1 X%s Y%s Z%s" % (x_str, y_str, z_str))
    return lines, z_actual


def _arc_splittable(info, state):
    """
    Check the conditions of the line and of the state that allow splitting
    an arc: the frame of the height map (units and WCS), G17 plane, no inverse
    feed, only the words G X Y Z I J K R F P (P only as P1) and G words that
    are 2/3 or modal settings.

    :param info: parsed words of the line, as made by level_gcode_line.
    :param state: modal state dict.
    :return: True if the arc may be split.
    """
    if not _in_frame(state) or state['cutter_comp'] is not False:
        return False
    # an unknown (None) arc distance mode or feed mode blocks the split too
    if (state['plane'] != 17 or state['inverse_feed'] is not False or state['arc_abs'] is None
            or state['abs'] is None):
        return False
    if not info['letters'] <= _ARC_SPLIT_LETTERS:
        return False
    if 'P' in info['words'] and info['words']['P'] != 1.0:
        return False
    return all(g in (2.0, 3.0) or g in _MODAL_SETTING_G_CODES for g in info['g_words'])


def _level_arc(line, info, state, offset_fn, decimals, start, was_known, absolute):
    """
    Split a G2/G3 arc line into height-compensated G1 lines, or leave it
    unchanged (counted in `state['arcs']`) if that is not possible.

    The X/Y/Z of `state` already hold the end point. The program keeps its
    modal G2/G3, but the machine is left in G1 by a split arc, so an
    unchanged arc line without a G2/G3 word gets the modal word in front.

    :param line: original line.
    :param info: parsed words of the line, as made by level_gcode_line.
    :param state: modal state dict; mutated in place.
    :param offset_fn: callable (x, y) -> height offset.
    :param decimals: number of decimals of the output.
    :param start: (x, y, z) before the line.
    :param was_known: True if the start point was known before the line.
    :param absolute: True for G90.
    :return: the new text (lines joined with a newline) or the original line.
    """
    points = None
    # in G91 the Z deltas need the Z offset in effect
    if was_known and _arc_splittable(info, state) and (absolute or state['applied'] is not None):
        end = (state['X'], state['Y'], state['Z'])
        clockwise = state['G'] == 2
        centre = _arc_centre(start, end, info['words'], clockwise, state['arc_abs'], decimals)
        geometry = None if centre is None else _arc_sweep(start, end, centre, clockwise, decimals)
        if geometry is not None:
            points = _arc_points(start, end, centre, geometry[0], geometry[1], state['arc_segment'])

    if points is None:
        state['arcs'] += 1
        if absolute and 'Z' in info['words']:
            state['applied'] = 0.0
        if 2.0 not in info['g_words'] and 3.0 not in info['g_words']:
            # the G word goes after a block delete and a line number
            lead = _LINE_LEAD_RE.match(line).end()
            return '%sG%d %s' % (line[:lead], state['G'], line[lead:])
        return line

    # G90 output does not use the start Z of the machine, so an unknown offset counts as 0
    lines, z_actual = _arc_lines(points, start, start[2] + (state['applied'] or 0.0), absolute,
                                 offset_fn, decimals)
    state['applied'] = z_actual - state['Z']
    state['arcs_split'] += 1

    head = [text for g, text in zip(info['g_words'], info['g_texts']) if g not in (2.0, 3.0)]
    head.append(lines[0])
    if info['f_text']:
        head.append(info['f_text'])
    lines[0] = ' '.join(head)
    comment = _comment_text(line)
    if comment:
        lines[0] += ' ' + comment
    if _BLOCK_DELETE_RE.match(line):
        # a block deleted arc: all of its segments are block deleted
        lines = ['/' + text for text in lines]
    return '\n'.join(lines)


def _level_relative(line, z_match, state, offset_fn, decimals, prev_z, was_known):
    """
    Height-compensate a G91 G1 move: the Z word becomes the delta that
    brings the machine to the programmed Z plus the offset of the new X/Y.

    :param line: original line.
    :param z_match: `_WORD_RE` match of the line's Z word, or None.
    :param state: modal state dict, already holding the new X/Y/Z; mutated.
    :param offset_fn: callable (x, y) -> height offset.
    :param decimals: number of decimals of the output.
    :param prev_z: programmed Z before the line.
    :param was_known: True if X, Y and Z were all known before the line.
    :return: the new line, or the original line if the position or the Z
        offset in effect was unknown (counted in `state['relative']`) or
        nothing has to change.
    """
    if not was_known or state['applied'] is None:
        state['relative'] += 1
        return line
    prev_actual = prev_z + state['applied']
    desired = state['Z'] + offset_fn(state['X'], state['Y'])
    z_step = round(desired - prev_actual, decimals)
    if z_step == 0:
        z_step = 0.0
    state['applied'] = prev_actual + z_step - state['Z']
    if z_match is None and z_step == 0:
        return line
    return _insert_z_word(line, _fmt(z_step, decimals), z_match)


def _unparsed_text(masked):
    """
    The text of a comment-masked line that is not a recognized word: an
    expression (`X[1+2]`), a parameter (`X#1`, `#1=5`), a lower-case word,
    an O-word keyword, ... A leading block delete "/" is not part of it.

    :param masked: line with its comments blanked by _mask_comments().
    :return: the text left after removing all words, '' if every part of
        the line is understood.
    """
    rest = _WORD_RE.sub('', masked).strip()
    if rest.startswith('/'):
        rest = rest[1:].strip()
    return rest


def level_gcode_line(line, state, offset_fn, decimals=4):
    """
    Apply height-map compensation to 1 line of G-code, modally tracking
    X/Y/Z/G state across calls. The rules for 1 line are in _level_line().

    An optional line (block delete "/") is levelled as if it runs, so its
    output is right when the controller runs it. But the controller can
    skip it, so after it every modal setting that it changed (distance
    mode, plane, arc distance mode, feed mode, motion mode, units, work
    coordinate system) is unknown (None; with units or WCS unknown the
    moves are out of the frame of the height map and are not compensated
    until a units or WCS word sets them again), each axis whose programmed value or known status it changes
    is unknown, it never clears an unknown motion mode, and if it changes
    the Z, its text or the applied offset the Z offset in effect
    (`state['applied']`) is unknown (None). Start-dependent moves (arcs,
    G91 moves) are then not compensated until absolute words set the
    position again; an absolute compensated G1 or an absolute Z word sets
    the offset again.

    :param line: 1 line of G-code text, no trailing newline.
    :param state: modal state dict, as created by new_levelling_state();
        mutated in place.
    :param offset_fn: callable (x, y) -> height offset (float) to add
        to Z.
    :param decimals: number of decimals used to format the new Z value.
    :return: the (possibly modified) line, without a trailing newline; a
        split arc is several lines joined with a newline.
    """
    if not _BLOCK_DELETE_RE.match(line):
        return _level_line(line, state, offset_fn, decimals)

    modes_before = {key: state[key] for key in _BLOCK_DELETE_MODAL_KEYS}
    axes_before = {axis: state[axis] for axis in ('X', 'Y', 'Z')}
    known_before = set(state['known'])
    applied_before = state['applied']
    g_unknown_before = state['g_unknown']
    z_ref_before = state['z_ref']
    probe_xy_before = state['probe_xy']
    out = _level_line(line, state, offset_fn, decimals)
    if state['z_ref'] != z_ref_before:
        # a skipped Z zero change: the Z reference of the map is not known
        state['shifted'] = True
    if state['probe_xy'] != probe_xy_before:
        state['probe_xy'] = None
    for key, value in modes_before.items():
        if state[key] != value:
            state[key] = None
            if key == 'G':
                state['g_unknown'] = True
            elif key in ('units', 'wcs') and state['home_' + key] is None:
                # an unknown frame must not look like the frame of a map that is not known by name
                state['home_' + key] = _FRAME_UNKNOWN
    # a skipped line can not clear an unknown motion mode
    state['g_unknown'] = state['g_unknown'] or g_unknown_before
    # an axis is uncertain if the line changes its programmed value, or makes it
    # known with the same value (only the run line would set it)
    _lose_axes(state, [axis for axis, value in axes_before.items()
                       if state[axis] != value or (axis in state['known']) != (axis in known_before)])
    if out != line or state['Z'] != axes_before['Z'] or state['applied'] != applied_before:
        # the machine Z of a run line and of a skipped line can differ
        state['applied'] = None
    return out


def _level_cycle(line, info, state, offset_fn, decimals, start, was_known, absolute,
                 cycle_was, cycle_code_was):
    """
    Expand a supported G81/G82/G83 canned cycle block into explicit motions,
    height-compensating every feed, or leave it unchanged and count it in
    ``state['cycles']`` when the block is not supported.

    The expansion keeps the hole order, the depth, the feed, the dwell of G82,
    the pecks of G83 and the line lead (N word). Every Z of the cycle is a
    physical plane: the R plane and the depth get the height of the hole, and
    the G98 initial plane is the captured physical Z where the cycle started
    (raised when a higher surface at a hole needs more than the R clearance).
    A G83 re-entry rapids to the bottom of the previous peck, never below it.
    Before a move in X/Y the tool lifts to a plane that clears the whole
    segment at the map ceiling of `state['map_ceiling']`; when that ceiling is
    not known the block is rejected rather than expanded with a guessed
    clearance. Only G81/G82/G83 in G17, in the frame of the height map, with a
    known start point and Z offset, positive feed (and Q for G83, P for G82)
    are expanded; everything else is rejected before any new output is
    produced.

    :param line: original line.
    :param info: parsed words of the line, as made by level_gcode_line.
    :param state: modal state dict; mutated in place.
    :param offset_fn: callable (x, y) -> height offset.
    :param decimals: number of decimals of the output.
    :param start: (x, y, z) before the line.
    :param was_known: True if X, Y and Z were all known before the line.
    :param absolute: True for G90, False for G91, None if unknown.
    :param cycle_was: True if a supported cycle was active before the line.
    :param cycle_code_was: the cycle code active before the line.
    :return: the expanded text (lines joined with a newline) or the original line.
    """
    def reject():
        state['cycles'] += 1
        state['G'] = None
        state['cycle'] = None
        state['applied'] = None
        for axis in ('X', 'Y'):
            value = info['words'].get(axis)
            if value is None:
                continue
            if absolute is True:
                state[axis] = float(value)
                state['known'].add(axis)
            else:
                _lose_axes(state, [axis])
        _lose_axes(state, ['Z'])
        return line

    cycle_gs = [g for g in info['g_words'] if g in _SUPPORTED_CYCLE_G_CODES]
    if len(cycle_gs) > 1:
        return reject()
    explicit = bool(cycle_gs)
    cycle_code = cycle_gs[-1] if explicit else state['cycle_code']
    if cycle_code not in _SUPPORTED_CYCLE_G_CODES:
        return reject()

    # only the plain G17 cycle of the map frame, from a known point, is expanded
    if (_BLOCK_DELETE_RE.match(line) or not was_known or absolute is None
            or not _in_frame(state) or state['cutter_comp'] is not False
            or state['plane'] != 17 or state['inverse_feed'] is not False
            or (absolute is False and state['applied'] is None)):
        return reject()
    if not info['letters'] <= _CYCLE_BLOCK_LETTERS:
        return reject()
    allowed_gs = set(_SUPPORTED_CYCLE_G_CODES) | set(_MODAL_SETTING_G_CODES) | set(_CYCLE_RETURN_G_CODES)
    if any(g not in allowed_gs for g in info['g_words']):
        return reject()
    if 98.0 in info['g_words'] and 99.0 in info['g_words']:
        return reject()

    # F is modal: a block without F uses the feed set by an earlier line
    feed_word = info['words'].get('F')
    if feed_word is not None and (not math.isfinite(feed_word) or feed_word <= 0):
        return reject()
    if state['feed'] is None or not math.isfinite(state['feed']) or state['feed'] <= 0:
        return reject()

    # the physical start Z is part of the modal state: without it a G98
    # initial plane would be a guess (fail closed)
    if state['applied'] is None or not math.isfinite(state['applied']):
        return reject()
    new_cycle = explicit and not (cycle_was is True and cycle_code_was == cycle_code)
    if new_cycle:
        if start[2] is None or not math.isfinite(start[2]):
            return reject()
        initial_z = start[2]
        initial_phys = start[2] + state['applied']
        if 'R' not in info['words'] or 'Z' not in info['words']:
            return reject()
        state['cycle_r'] = state['cycle_z'] = state['cycle_q'] = state['cycle_p'] = None
    else:
        initial_z = state['cycle_initial_z']
        if initial_z is None:
            initial_z = start[2]
        if initial_z is None or not math.isfinite(initial_z):
            return reject()
        initial_phys = state.get('cycle_initial_phys')
        if initial_phys is None or not math.isfinite(initial_phys):
            return reject()

    r_word = info['words'].get('R')
    z_word = info['words'].get('Z')
    if r_word is not None:
        if not math.isfinite(r_word):
            return reject()
        r_abs = r_word if absolute else initial_z + r_word
    else:
        r_abs = state['cycle_r']
    if z_word is not None:
        if not math.isfinite(z_word):
            return reject()
        z_abs = z_word if absolute else r_abs + z_word
    else:
        z_abs = state['cycle_z']
    if (r_abs is None or z_abs is None or not math.isfinite(r_abs)
            or not math.isfinite(z_abs) or z_abs >= r_abs):
        return reject()

    q_word = info['words'].get('Q')
    p_word = info['words'].get('P')
    if cycle_code in (81.0, 82.0) and q_word is not None:
        return reject()
    if cycle_code in (81.0, 83.0) and p_word is not None:
        return reject()
    if q_word is not None:
        if not math.isfinite(q_word) or q_word <= 0:
            return reject()
        q_abs = q_word
    else:
        q_abs = state['cycle_q']
    if cycle_code == 83.0 and (q_abs is None or not math.isfinite(q_abs) or q_abs <= 0):
        return reject()
    if p_word is not None:
        if not math.isfinite(p_word) or p_word < 0:
            return reject()
        p_val = p_word
    else:
        p_val = state['cycle_p']
    if cycle_code == 82.0 and (p_val is None or not math.isfinite(p_val) or p_val < 0):
        return reject()

    retract_mode = state['cycle_return']
    if retract_mode not in _CYCLE_RETURN_G_CODES:
        return reject()
    if retract_mode == 98.0 and initial_z < r_abs:
        return reject()

    x_word = info['words'].get('X')
    y_word = info['words'].get('Y')
    if x_word is not None and not math.isfinite(x_word):
        return reject()
    if y_word is not None and not math.isfinite(y_word):
        return reject()
    if absolute:
        x = x_word if x_word is not None else state['X']
        y = y_word if y_word is not None else state['Y']
    else:
        if state['X'] is None or state['Y'] is None:
            return reject()
        x = state['X'] + x_word if x_word is not None else state['X']
        y = state['Y'] + y_word if y_word is not None else state['Y']
    if x is None or y is None or not math.isfinite(x) or not math.isfinite(y):
        return reject()

    if cycle_code == 83.0:
        pecks = int(math.ceil((r_abs - z_abs) / q_abs - 1e-9))
        if pecks > _CYCLE_MAX_PECKS:
            return reject()

    hole_offset = offset_fn(x, y)
    if not math.isfinite(hole_offset):
        return reject()
    r_phys = r_abs + hole_offset
    if retract_mode == 98.0:
        # G98 returns to the captured physical initial plane; lift it when a
        # higher surface at this hole would leave less than the R clearance
        retract_phys = max(initial_phys, r_phys)
    else:
        retract_phys = r_phys

    actual_start = start[2] + state['applied']
    # the XY segment of a hole is crossed at a plane that clears the whole
    # segment, not only its 2 ends: the caller must give a provable ceiling
    if abs(x - start[0]) > 0 or abs(y - start[1]) > 0:
        ceiling = state.get('map_ceiling')
        if ceiling is None or not math.isfinite(ceiling):
            return reject()
        travel_phys = max(actual_start, ceiling - (state.get('z_ref') or 0.0) + r_abs)
    else:
        travel_phys = actual_start

    state['cycle_initial_z'] = initial_z
    state['cycle_initial_phys'] = initial_phys
    state['cycle_r'] = r_abs
    state['cycle_z'] = z_abs
    state['cycle_q'] = q_abs
    state['cycle_p'] = p_val

    lines = []
    first_feed = [None]
    cur_x, cur_y = start[0], start[1]
    actual_z = actual_start

    def rapid_z(target):
        nonlocal actual_z
        target = float(_fmt(target, decimals))
        if abs(target - actual_z) < 0.5 * 10 ** -decimals:
            actual_z = target
            return
        if absolute:
            actual_z = target
            lines.append('G0 Z%s' % _fmt(target, decimals))
        else:
            step = float(_fmt(target - actual_z, decimals))
            if step == 0:
                return
            actual_z += step
            lines.append('G0 Z%s' % _fmt(step, decimals))

    def rapid_xy(target_x, target_y):
        nonlocal cur_x, cur_y
        target_x = float(_fmt(target_x, decimals))
        target_y = float(_fmt(target_y, decimals))
        if absolute:
            if target_x == cur_x and target_y == cur_y:
                return
            x_text, y_text = _fmt(target_x, decimals), _fmt(target_y, decimals)
            cur_x, cur_y = target_x, target_y
        else:
            dx = float(_fmt(target_x - cur_x, decimals))
            dy = float(_fmt(target_y - cur_y, decimals))
            if dx == 0 and dy == 0:
                return
            x_text, y_text = _fmt(dx, decimals), _fmt(dy, decimals)
            cur_x, cur_y = cur_x + dx, cur_y + dy
        lines.append('G0 X%s Y%s' % (x_text, y_text))

    def feed_z(target):
        nonlocal actual_z
        desired = float(_fmt(target + hole_offset, decimals))
        if absolute:
            if desired == actual_z:
                return
            actual_z = desired
            lines.append('G1 Z%s' % _fmt(desired, decimals))
        else:
            step = float(_fmt(desired - actual_z, decimals))
            if step == 0:
                return
            actual_z += step
            lines.append('G1 Z%s' % _fmt(step, decimals))
        if first_feed[0] is None:
            first_feed[0] = len(lines) - 1

    # lift to a plane that also clears the segment, then go to the hole and
    # down to its R plane; the full retract clears the mapped surface
    rapid_z(travel_phys)
    rapid_xy(x, y)
    rapid_z(r_phys)

    if cycle_code in (81.0, 82.0):
        feed_z(z_abs)
    else:
        depth = r_abs
        while depth > z_abs + 1e-12:
            next_depth = max(depth - q_abs, z_abs)
            feed_z(next_depth)
            cut_actual = actual_z
            if next_depth > z_abs + 1e-12:
                # G83 retracts to the R plane between pecks, then rapids back
                # to the bottom of the previous peck (never below it).
                rapid_z(r_phys)
                rapid_z(cut_actual)
            depth = next_depth
    if cycle_code == 82.0:
        lines.append('G4 P%s' % _fmt(p_val, decimals))
    rapid_z(retract_phys)

    if not lines:
        # every motion of the block rounds away: make the expansion explicit
        # anyway, so the controller never runs the raw (uncompensated) cycle
        lines.append('G0 Z%s' % _fmt(actual_z if absolute else 0.0, decimals))

    state['X'] = cur_x
    state['Y'] = cur_y
    # keep the programmed/physical relation: machine Z = programmed Z + applied
    programmed_retract = initial_z if retract_mode == 98.0 else r_abs
    state['Z'] = programmed_retract
    state['applied'] = actual_z - programmed_retract
    state['G'] = None
    state['cycle'] = True
    state['cycle_code'] = int(cycle_code)

    head = [text for g, text in zip(info['g_words'], info['g_texts'])
            if g not in _SUPPORTED_CYCLE_G_CODES and g not in _CYCLE_RETURN_G_CODES]
    head.append(lines[0])
    lead = info.get('lead', '')
    lines[0] = lead + ' '.join(head) if lead else ' '.join(head)
    if info['f_text'] and first_feed[0] is not None:
        lines[first_feed[0]] += ' ' + info['f_text']
    comment = _comment_text(line)
    if comment:
        lines[0] += ' ' + comment
    return '\n'.join(lines)


def _level_line(line, state, offset_fn, decimals):
    """
    Apply height-map compensation to 1 line of G-code, modally tracking
    X/Y/Z/G state across calls.

    Every linear move (modal G1), at any Z with known work X/Y/Z, gets
    its Z height-compensated, if it is in the frame of the height map: the
    units and the work coordinate system (WCS) of the probing. The frame
    follows the G20/G21 and G54..G59.3 words (see _note_frame_words()); each
    word that changes it makes all 3 axes unknown. A line that moves the X/Y
    work origin (G92 or G10 L20 with an X/Y word, G10 L2 with an X/Y/R word,
    G92.1/G92.2/G92.3) puts the rest of the program out of the frame
    (`state['shifted']`), also with unresolved expressions. G10 only
    invalidates the map if its P target is the map WCS or is unknown;
    a Z-only shift (a tool length re-zero) does not. G52 with an X/Y word,
    G51 and G68 shift the frame too, and so do a not understood line with a
    G word that is not read (lower case, expression: it can be any G code)
    and a subprogram call or flow control (O-word keyword, M98, G65). Under cutter radius
    compensation (G41/G42 until G40) a G1 move is not changed and is counted
    in `state['cutter']`, and an arc is not split. A Z zero change (G92 Z,
    G10 L20 Z) right after a contact probe (G38.2, G31) at a known X/Y inside
    the map re-references the map to the probed point (`state['z_ref']`, see
    _note_z_zero()); any other Z zero change of the map WCS (also G10 L2 Z and
    G52 Z) puts the rest of the program out of the frame. G70/G71 are units
    words, Mach3 "G59 P<n>" and Fanuc "G54.1 P<n>" are coordinate systems. A
    supported canned cycle (G81, G82, G83, also a bare hole under a modal
    supported cycle) in G17 and in the map frame is expanded into explicit
    motions, with every feed height-compensated and every XY move at or above
    the cycle's retract plane (G98 initial plane, G99 R plane); its repeats,
    G80 and any other motion-group word cancel it. Return-mode words are consumed
    by this cycle-expanding API, including standalone settings, not sent to GRBL.
    A canned cycle or a cycle
    parameter that is not supported (G73, G76, G84..G89, another plane, a
    missing/invalid R, Z, Q or P, a bad feed, a repetition L, or a cycle block
    that mixes a motion code) is not changed and is counted in
    `state['cycles']`. A G code that the tool
    does not know makes the position and the motion mode unknown. An axis word
    of a line that is not a G0..G3 move (a probe, G43.1 Z, ...) makes its axis
    unknown, and G43/G49 make Z unknown. A G1 move with X/Y/Z
    words in another frame (absolute or relative) is not changed and is
    counted in `state['frame']`; an absolute one with a Z word sets the Z
    offset in effect (`state['applied']`) to 0, the machine goes to the
    programmed Z. An arc in another frame is not split (counted in
    `state['arcs']`). The axes are tracked as usual. X/Y/Z are None at the
    start, so a G1 move needs all 3 axes set (before the line or on it);
    else it is not changed and is counted in `state['unknown']`. In G91 a G1 move is compensated when X/Y/Z
    were all set by absolute words before: its Z word becomes the delta
    to the compensated height (`state['applied']` is the offset in effect).
    An arc (modal G2/G3) in the G17 plane with a known start point is split
    into G1 segments (returned as 1 string, lines joined with a newline);
    other arcs are left untouched and counted in `state['arcs']`. Left
    untouched: rapids (G0); other members of the motion modal
    group that are never cutting moves (G31, G38.2/.3/.4/.5, G80) -
    selecting 1 of these sets `state['G']` to None so a following bare
    "X.. Y.." line is not mistaken for a continuing G1 move until a
    real G0/G1/G2/G3 reappears; non-modal one-shot codes (G10, G28,
    G30, G92) whose X/Y/Z words are not work-coordinate moves, so
    they also leave `state['G']` untouched. They change `state['X']`/
    `['Y']`/`['Z']` only by marking axes unknown (None, and no longer in
    `state['known']`): G28 and G30 the axes they move (all 3 if none is
    named); G92 and G10 L20 the axes named on the line; G92.1, G92.2,
    G92.3 and G10 L2 all 3 axes (they change the work coordinate system,
    so the tracked work positions are no longer valid). Other G10 forms
    (L1, L10, ...) change nothing. The line itself is never changed;
    G53 machine-coordinate moves, which track explicit G0/G1 but mark
    moved work axes unknown (None) until restored by absolute work words;
    non-motion modal codes (G4, G17, G20, G21, G90, G94, ...); and
    comment-only/blank lines.

    Words are matched with upper-case letters and plain numbers only
    (`[A-Z]` and `_WORD_RE`), matching what FlatCAM's preprocessors emit.
    A line with any other text (an expression `X[1]`, a parameter `X#1`,
    lower-case words such as `g01 x10`, ...) is not understood: it passes
    through unchanged, it is counted in `state['unparsed']`, all 3 axes
    and the motion mode become unknown, and the modal settings of its
    recognized G words still apply. A `%` line is understood. If the
    distance mode is unknown (None), the axes that a line names become
    unknown and the line is not changed.

    :param line: 1 line of G-code text, no trailing newline.
    :param state: modal state dict, as created by new_levelling_state();
        mutated in place.
    :param offset_fn: callable (x, y) -> height offset (float) to add
        to Z.
    :param decimals: number of decimals used to format the new Z value.
    :return: the (possibly modified) line, without a trailing newline; a
        split arc is several lines joined with a newline.
    """
    stripped = line.strip()
    if not stripped or stripped == '%':
        return line

    masked = _mask_comments(line)
    if not masked.strip():
        # a comment line; a leading comment can not hide commands ("(c) G41 D1")
        return line

    raw_fn = offset_fn
    # Instrument compensation only, not the raw lookup used to re-reference a Z zero.
    offset_fn = state.get('coverage_offset', raw_fn)
    if state['z_ref']:
        offset_fn = _referenced(offset_fn, state['z_ref'])

    x_val = y_val = z_val = None
    z_match = None
    g_words = []
    info = {'words': {}, 'letters': set(), 'g_words': g_words, 'g_texts': [], 'f_text': None,
            'lead': _LINE_LEAD_RE.match(masked).group(0)}

    for m in _WORD_RE.finditer(masked):
        letter, num = m.group(1), m.group(2)
        info['letters'].add(letter)
        info['words'][letter] = float(num)
        if letter == 'X':
            x_val = float(num)
        elif letter == 'Y':
            y_val = float(num)
        elif letter == 'Z':
            z_val = float(num)
            z_match = m
        elif letter == 'G':
            g_words.append(float(num))
            info['g_texts'].append('G' + num)
        elif letter == 'F':
            info['f_text'] = 'F' + num

    has_xyz = x_val is not None or y_val is not None or z_val is not None

    if 'F' in info['words']:
        feed_value = info['words']['F']
        state['feed'] = feed_value if math.isfinite(feed_value) and feed_value > 0 else None

    _note_modal_settings(state, g_words)
    unparsed = _unparsed_text(masked)
    # also on a line that is not understood: its G words still apply
    _note_frame_words(state, g_words, info['words'].get('P'), 'P' in masked.upper(), not unparsed)
    non_modal_gs = [g for g in g_words if g in _NON_MODAL_G_CODES]
    # G codes that the tool does not know: they can move the machine (NCCAD9 G77,
    # Mach3 G12/G13 circle pocket, LinuxCNC G5 spline, G33 spindle synchronized, ...)
    unknown_gs = [g for g in g_words
                  if g not in _MOTION_G_CODES and g not in _MOTION_NO_LEVEL_G_CODES
                  and g not in _MODAL_SETTING_G_CODES and g not in _NON_MODAL_G_CODES
                  and g not in _KNOWN_STILL_G_CODES]
    setup_gs = (4.0, 10.0, 92.0, 92.1, 92.2, 92.3)
    is_setup = (any(g in setup_gs for g in g_words)
                and all(g in setup_gs or g in _MODAL_SETTING_G_CODES for g in g_words))
    # after the frame words: a WCS word on the first move still declares the frame
    if not is_setup and (has_xyz or any(letter in info['words'] for letter in 'IJKR')
                         or 28.0 in g_words or 30.0 in g_words or 76.0 in g_words
                         or unknown_gs or unparsed):
        state['moved'] = True

    # Inspect origin changes before the unparsed return: restoring absolute
    # positions cannot restore the original map after an unresolved shift.
    xy_word = any(letter in masked.upper() for letter in 'XY')
    z_word = 'Z' in masked.upper()
    if any(g in non_modal_gs for g in (92.1, 92.2, 92.3)) or (92.0 in non_modal_gs and xy_word):
        state['shifted'] = True
    elif 92.0 in non_modal_gs and z_word:
        # G92 Z<v>: the Z position now in effect becomes v
        _note_z_zero(state, None if unparsed else z_val, raw_fn)
    if 10.0 in non_modal_gs:
        setup_words = {}
        for m in _WORD_RE.finditer(masked):
            letter = m.group(1)
            if letter not in 'LP' or masked.upper().count(letter) != 1:
                continue
            tail = masked[m.end():].lstrip()
            # A numeric prefix of an expression is not a literal L/P value.
            if not tail or 'A' <= tail[0] <= 'Z':
                setup_words[letter] = float(m.group(2))
        l_word = setup_words.get('L')
        p_word = setup_words.get('P')
        target_wcs = None
        if p_word == 0.0:
            target_wcs = state['wcs']
        elif p_word is not None and p_word.is_integer() and 1 <= p_word <= len(_WCS_G_CODES):
            target_wcs = _WCS_G_CODES[int(p_word) - 1]
        map_target = (target_wcs is None or state['home_wcs'] not in _WCS_G_CODES
                      or target_wcs == state['home_wcs'])
        if (l_word in (None, 2.0, 20.0)
                and (xy_word or (l_word in (None, 2.0) and 'R' in masked.upper()))
                and map_target):
            state['shifted'] = True
        elif l_word in (None, 2.0, 20.0) and z_word and map_target:
            # G10 L20 on the active WCS: the Z position now in effect becomes Z<v>;
            # G10 L2 sets an offset by value, so the Z reference of the map is lost
            active = p_word == 0.0 or (target_wcs is not None and target_wcs == state['wcs'])
            if l_word == 20.0 and active and not unparsed:
                _note_z_zero(state, z_val, raw_fn)
            else:
                state['shifted'] = True
    # G52 (local offset, also in Z) and scale, mirror, rotation, polar or diameter mode
    if ((52.0 in g_words and (xy_word or z_word))
            or any(g in _FRAME_CHANGE_G_CODES for g in g_words)):
        state['shifted'] = True
    if unparsed and masked.upper().count('G') > len(g_words):
        # A G word that is not read (lower case "g92.1", "G[92]", "G#1") can be any
        # G code: a units or WCS change, or an origin reset without an X/Y word
        # (G92.1/.2/.3). A later explicit word can not undo it.
        state['shifted'] = True
    if 65.0 in g_words or _SUBPROGRAM_CALL_RE.search(masked.upper()):
        # The tool does not see the codes of a subprogram (O-word call, M98, G65), and
        # the lines of an O-word block (sub, while, if, repeat) run 0 or many times.
        state['shifted'] = True
    for g in g_words:
        if g in _CUTTER_COMP_G_CODES:
            state['cutter_comp'] = True
        elif g == 40.0:
            state['cutter_comp'] = False
    if any(g in (43.0, 43.1, 43.2, 49.0) for g in g_words):
        # a tool length offset change: the machine does not move, but the Z of the
        # tool tip in work coordinates changes
        _lose_axes(state, ['Z'])
    # the X/Y of a contact probe move (the Z zero change comes on a later line);
    # any other line that can move or change the frame ends it, also a units or
    # WCS word that leaves the frame of the map
    if (any(g in _CONTACT_PROBE_G_CODES for g in g_words) and x_val is None and y_val is None
            and not unparsed and not unknown_gs and _in_frame(state)
            and {'X', 'Y'} <= state['known'] and state['X'] is not None and state['Y'] is not None):
        state['probe_xy'] = (state['X'], state['Y'])
    elif (has_xyz or unparsed or not _in_frame(state)
          or any(g not in _MODAL_SETTING_G_CODES for g in g_words)):
        state['probe_xy'] = None

    if unparsed:
        # Part of the line is not understood (it can hold an axis word), so
        # nothing after it can trust the tracked position or motion mode.
        state['unparsed'] += 1
        state['G'] = None
        state['g_unknown'] = True
        _lose_axes(state, ['X', 'Y', 'Z'])
        return line

    return_gs = [g for g in g_words if g in _CYCLE_RETURN_G_CODES]
    if return_gs and info['letters'] <= {'G', 'N', 'F'} and all(
            g in _CYCLE_RETURN_G_CODES or g in _MODAL_SETTING_G_CODES for g in g_words):
        if len(return_gs) != 1 or _BLOCK_DELETE_RE.match(line):
            state['cycles'] += 1
            state['cycle_return'] = None
            return line
        # Only consume a plain cycle-return setting; leave unrelated commands
        # and unsupported blocks intact so strict levelling can reject them.
        for match in reversed(list(_WORD_RE.finditer(masked))):
            if match.group(1) == 'G' and float(match.group(2)) in _CYCLE_RETURN_G_CODES:
                line = line[:match.start()] + line[match.end():]
        return line

    if non_modal_gs:
        # One-shot code: X/Y/Z here are not work-coordinate moves. A motion or
        # a coordinate change also ends a canned cycle (the next bare hole is
        # not a continuation of it).
        state['cycle'] = False
        state['cycle_code'] = None
        state['cycle_initial_z'] = None
        state['cycle_r'] = state['cycle_z'] = state['cycle_q'] = state['cycle_p'] = None
        moved = [axis for axis, value in [('X', x_val), ('Y', y_val), ('Z', z_val)]
                 if value is not None]
        if 53.0 in non_modal_gs:
            motion_gs = [g for g in g_words if g in (0.0, 1.0)]
            if motion_gs:
                state['G'] = int(motion_gs[-1])
            # The work offset is unknown, so machine positions cannot
            # establish work coordinates for the moved axes.
            _lose_axes(state, moved)
        elif 28.0 in non_modal_gs or 30.0 in non_modal_gs:
            # Go to the predefined position: the axes it moves are unknown
            # after it (all 3 if none is named).
            _lose_axes(state, moved or ['X', 'Y', 'Z'])
        elif any(g in non_modal_gs for g in (92.1, 92.2, 92.3)):
            # The axis offsets are cleared or restored: every work position changes.
            _lose_axes(state, ['X', 'Y', 'Z'])
        elif 92.0 in non_modal_gs:
            # G92 makes the current position the given values: the work
            # coordinates of the named axes are no longer the tracked ones.
            _lose_axes(state, moved)
        elif 10.0 in non_modal_gs:
            l_word = info['words'].get('L')
            if l_word == 2.0:
                # Keep position tracking conservative for coordinate-system writes.
                _lose_axes(state, ['X', 'Y', 'Z'])
            elif l_word == 20.0:
                _lose_axes(state, moved)
        return line

    motion_gs = [g for g in g_words if g in _MOTION_G_CODES]
    no_level_gs = [g for g in g_words if g in _MOTION_NO_LEVEL_G_CODES]
    other_gs = [
        g for g in g_words
        if g not in _MOTION_G_CODES and g not in _MOTION_NO_LEVEL_G_CODES
        and g not in _MODAL_SETTING_G_CODES
    ]

    cycle_was = state['cycle'] is True
    cycle_code_was = state['cycle_code']
    supported_cycle_gs = [g for g in g_words if g in _SUPPORTED_CYCLE_G_CODES]
    unsupported_cycle_gs = [g for g in g_words if g in _CANNED_CYCLE_G_CODES
                            and g not in _SUPPORTED_CYCLE_G_CODES]
    cycle_gs = supported_cycle_gs + unsupported_cycle_gs
    mixed_cycle = (len(cycle_gs) > 1
                   or (cycle_gs and (motion_gs or any(
                       g in _MOTION_NO_LEVEL_G_CODES and g not in _CANNED_CYCLE_G_CODES
                       for g in no_level_gs))))
    if mixed_cycle:
        state['cycles'] += 1
        state['G'] = None
        state['cycle'] = None
        state['applied'] = None
        _lose_axes(state, ['X', 'Y', 'Z'])
        return line

    is_non_motion_cmd = False
    if motion_gs or no_level_gs:
        state['g_unknown'] = False
    if motion_gs:
        state['G'] = int(motion_gs[-1])
        state['cycle'] = False
        state['cycle_code'] = None
        state['cycle_initial_z'] = None
        state['cycle_r'] = state['cycle_z'] = state['cycle_q'] = state['cycle_p'] = None
    elif no_level_gs:
        state['G'] = None
        state['cycle'] = no_level_gs[-1] in _CANNED_CYCLE_G_CODES
        state['cycle_code'] = no_level_gs[-1] if state['cycle'] else None
        if not state['cycle']:
            state['cycle_initial_z'] = None
            state['cycle_r'] = state['cycle_z'] = state['cycle_q'] = state['cycle_p'] = None
        is_non_motion_cmd = True
    elif other_gs:
        is_non_motion_cmd = True
    # else: no G word at all - keep the previous modal G unchanged.

    if unknown_gs or 76.0 in no_level_gs:
        # The machine can be anywhere after it (G76: LinuxCNC threading, NCCAD9
        # reference travel). An unknown code can also be a motion mode.
        _lose_axes(state, ['X', 'Y', 'Z'])
        state['applied'] = None
        if unknown_gs:
            state['G'] = None
            state['g_unknown'] = True
            # an unknown code can be a motion or a cycle change: no hole after it
            # is a continuation of the cycle that was active before it
            state['cycle'] = None
        if has_xyz or any(letter in info['words'] for letter in 'IJKR'):
            if state['cycle'] is not False:
                state['cycles'] += 1
            else:
                state['unknown'] += 1
        return line

    if (state['cycle'] is True and state['cycle_code'] in _SUPPORTED_CYCLE_G_CODES
            and not motion_gs and not unparsed and not unknown_gs
            and (supported_cycle_gs or has_xyz or 'R' in info['words']
                 or 'Q' in info['words'] or 'P' in info['words'])):
        return _level_cycle(line, info, state, offset_fn, decimals,
                            (state['X'], state['Y'], state['Z']), _all_known(state),
                            state['abs'], cycle_was, cycle_code_was)

    if state['cycle'] is not False and not motion_gs and (has_xyz or 'R' in info['words']):
        # An unsupported canned cycle (also a bare "X5 Y5" hole under it): its
        # depth is not compensated, and the Z after it is the retract level.
        state['cycles'] += 1
        for axis, value in (('X', x_val), ('Y', y_val)):
            if value is not None and state['abs'] is True:
                state[axis] = value
            elif value is not None:
                _lose_axes(state, [axis])
        _lose_axes(state, ['Z'])
        state['applied'] = None
        return line

    absolute = state.get('abs', True)
    start = (state['X'], state['Y'], state['Z'])
    was_known = _all_known(state)
    is_motion = state['G'] in _MOTION_G_CODES and not is_non_motion_cmd
    for axis, value in [('X', x_val), ('Y', y_val), ('Z', z_val)]:
        if value is not None:
            if absolute is None or not is_motion:
                # unknown distance mode: the word can be a position or a delta;
                # not a G0..G3 move: the word is not a work position (G43.1 Z is a
                # tool length, a probe stops at an unknown point, ...)
                _lose_axes(state, [axis])
                continue
            if absolute:
                state[axis] = value
                state['known'].add(axis)
            elif state[axis] is not None:
                state[axis] += value

    if (state['G'] in (2, 3) and not is_non_motion_cmd
            and (has_xyz or any(letter in info['words'] for letter in 'IJR'))):
        return _level_arc(line, info, state, offset_fn, decimals, start, was_known, absolute)

    if not has_xyz:
        return line

    if is_non_motion_cmd:
        if absolute and z_val is not None:
            state['applied'] = 0.0
        return line

    if state['G'] is None:
        if state['g_unknown']:
            # a bare move under an unknown motion mode may be a cut
            state['unknown'] += 1
        if absolute and z_val is not None:
            state['applied'] = 0.0
        return line

    if absolute is None:
        if state['G'] == 1:
            state['unknown'] += 1
        return line

    if state['G'] == 1 and not _in_frame(state):
        # another WCS or other units than those of the height map
        state['frame'] += 1
        if absolute and z_val is not None:
            state['applied'] = 0.0
        return line

    if state['G'] == 1 and state['cutter_comp'] is not False:
        # G41/G42: the tool is off the programmed X/Y, the map would be read at the wrong point
        state['cutter'] += 1
        if absolute and z_val is not None:
            state['applied'] = 0.0
        return line

    if not absolute:
        if state['G'] == 1:
            return _level_relative(line, z_match, state, offset_fn, decimals, start[2], was_known)
        return line

    if state['G'] == 1 and all(state[axis] is not None for axis in ('X', 'Y', 'Z')):
        new_z = state['Z'] + offset_fn(state['X'], state['Y'])
        new_z_str = "{0:.{1}f}".format(new_z, decimals)
        state['applied'] = float(new_z_str) - state['Z']
        return _insert_z_word(line, new_z_str, z_match)

    if state['G'] == 1:
        # a cut at an unknown work position
        state['unknown'] += 1
    if z_val is not None:
        state['applied'] = 0.0
    return line
