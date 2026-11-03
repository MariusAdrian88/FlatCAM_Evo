"""Pure column-stock simulation, laser dose integration and closed-subset G-code motion."""

import math
import re
from dataclasses import dataclass, field

import numpy as np

from .PhotoCarveModel import MAX_CELLS, MAX_KERNEL, MAX_POINTS, ImageFrame, PhotoCarveError, kernel_count, tool_lift
from .PhotoCarveLaser import format_power


@dataclass
class Stock:
    frame: ImageFrame
    height: np.ndarray = field(init=False)

    def __post_init__(self):
        if self.frame.rows * self.frame.cols > MAX_CELLS:
            raise PhotoCarveError('Simulation stock exceeds the cell limit; increase simulation resolution.')
        self.height = np.zeros((self.frame.rows, self.frame.cols), dtype=np.float32)

    @property
    def bounds(self):
        return (self.frame.x0, self.frame.y0, self.frame.x0 + self.frame.width,
                self.frame.y0 + self.frame.height)


@dataclass(frozen=True)
class Span:
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float
    rapid: bool = False
    laser_on: bool = False
    s: float = 0.0


def _samples(path, step, check_abort):
    path = np.asarray(path, dtype=np.float64)
    if path.ndim != 2 or path.shape[1] != 3 or not np.isfinite(path).all():
        raise PhotoCarveError('Simulation path must contain finite XYZ points.')
    if len(path) == 1:
        yield path
    total = 0
    for a, b in zip(path, path[1:]):
        if check_abort is not None:
            check_abort()
        n = max(1, math.ceil(float(np.linalg.norm(b[:2] - a[:2])) / step))
        total += n + 1
        if total > MAX_POINTS:
            raise PhotoCarveError('Simulation sampling exceeds the point limit.')
        yield np.linspace(a, b, n + 1)


def stamp_path(stock, path, tool, sub=4, check_abort=None):
    """Densify each segment independently so overlapping chunks reproduce a whole path."""
    if not isinstance(sub, int) or sub < 1:
        raise PhotoCarveError('Simulation subcell sampling must be a positive integer.')
    frame = stock.frame
    radius = tool.diameter / 2
    kernel_count(radius, frame.pitch_x, frame.pitch_y)
    step = min(frame.pitch_x, frame.pitch_y) / (2 * sub)
    nr = math.ceil(radius / frame.pitch_y) + 2
    nc = math.ceil(radius / frame.pitch_x) + 2
    dr, dc = np.meshgrid(np.arange(-nr, nr + 1), np.arange(-nc, nc + 1), indexing='ij')
    dr, dc = dr.ravel(), dc.ravel()
    kernels = {}
    for samples in _samples(path, step, check_abort):
        cc = np.rint(((samples[:, 0] - frame.x0) / frame.pitch_x - 0.5) * sub).astype(np.int64)
        rr = np.rint(((frame.y0 + frame.height - samples[:, 1]) / frame.pitch_y - 0.5) * sub).astype(np.int64)
        phases = (rr % sub) * sub + cc % sub
        for phase in np.unique(phases):
            if check_abort is not None:
                check_abort()
            mask = phases == phase
            bases = np.column_stack((rr[mask] // sub, cc[mask] // sub))
            bases, inverse = np.unique(bases, axis=0, return_inverse=True)
            z = np.full(len(bases), np.inf)
            np.minimum.at(z, inverse, samples[mask, 2])
            if phase not in kernels:
                py, px = divmod(int(phase), sub)
                dx = np.maximum(np.abs((dc - px / sub) * frame.pitch_x) - frame.pitch_x / 2, 0)
                dy = np.maximum(np.abs((dr - py / sub) * frame.pitch_y) - frame.pitch_y / 2, 0)
                rho = np.hypot(dx, dy)
                keep = rho < radius
                if np.count_nonzero(keep) > MAX_KERNEL:
                    raise PhotoCarveError('Simulation cutter kernel exceeds the safety limit.')
                kernels[phase] = list(zip(dr[keep], dc[keep], tool_lift(tool, rho[keep])))
            for index, (row_offset, col_offset, lift) in enumerate(kernels[phase]):
                if check_abort is not None and index % 64 == 0:
                    check_abort()
                rows, cols = bases[:, 0] + row_offset, bases[:, 1] + col_offset
                inside = (rows >= 0) & (rows < frame.rows) & (cols >= 0) & (cols < frame.cols)
                rows, cols = rows[inside], cols[inside]
                stock.height[rows, cols] = np.minimum(stock.height[rows, cols], z[inside] + lift)
    return stock.height


def simulate_paths(stock, paths, tool, sub=4, check_abort=None):
    for path in paths:
        stamp_path(stock, path, tool, sub, check_abort)
    return stock.height


def reference_gouge(paths, tool, target, frame, sub=8):
    """Independent dense rectangle-column oracle for small tests, not a production certificate."""
    if frame.rows > 200 or frame.cols > 200:
        raise PhotoCarveError('Independent gouge reference is limited to 200 by 200 cells.')
    surface = np.zeros((frame.rows, frame.cols), dtype=np.float64)
    radius = tool.diameter / 2
    step = min(frame.pitch_x, frame.pitch_y) / (2 * sub)
    for path in paths:
        for samples in _samples(path, step, None):
            for x, y, z in samples:
                for row in range(frame.rows):
                    cy = frame.y0 + frame.height - (row + 0.5) * frame.pitch_y
                    dy = max(abs(cy - y) - frame.pitch_y / 2, 0)
                    if dy >= radius:
                        continue
                    for col in range(frame.cols):
                        cx = frame.x0 + (col + 0.5) * frame.pitch_x
                        rho = math.hypot(max(abs(cx - x) - frame.pitch_x / 2, 0), dy)
                        if rho >= radius:
                            continue
                        if tool.kind == 'flat':
                            lift = 0.0
                        elif tool.kind == 'ball':
                            lift = radius - math.sqrt(max(0.0, radius * radius - rho * rho))
                        else:
                            lift = max(0, rho - tool.tip_diameter / 2) / math.tan(math.radians(tool.angle_deg) / 2)
                        surface[row, col] = min(surface[row, col], z + lift)
    return max(0.0, float(np.max(np.asarray(target) - surface)))


def laser_moves_from_paths(paths, as_s=False, minimum=0, maximum=1000, printer=None):
    format_power(0, minimum, maximum)
    moves = []
    for path in paths:
        for a, b in zip(path, path[1:]):
            x0, y0 = printer(float(a[0]), float(a[1])) if printer else a[:2]
            x1, y1 = printer(float(b[0]), float(b[1])) if printer else b[:2]
            power = format_power(float(b[2]), minimum, maximum) if as_s else float(b[2])
            moves.append(Span(x0, y0, math.nan, x1, y1, math.nan, False, True, power))
    return moves


def burn_image(frame, moves, maximum=1, image=None, check_abort=None):
    """Accumulate dose (power times traversal length); rapids and disabled laser never burn."""
    if not math.isfinite(maximum) or maximum <= 0:
        raise PhotoCarveError('Maximum laser power must be finite and positive.')
    if frame.rows * frame.cols > MAX_CELLS:
        raise PhotoCarveError('Laser simulation exceeds the cell limit.')
    image = np.zeros((frame.rows, frame.cols), dtype=np.float32) if image is None else image
    step = min(frame.pitch_x, frame.pitch_y) / 4
    for move in moves:
        if check_abort is not None:
            check_abort()
        if move.rapid or not move.laser_on or move.s <= 0:
            continue
        length = math.hypot(move.x1 - move.x0, move.y1 - move.y0)
        if length == 0:
            continue
        n = math.ceil(length / step)
        if n > MAX_POINTS:
            raise PhotoCarveError('Laser simulation sampling exceeds the point limit.')
        t = (np.arange(n) + 0.5) / n
        x, y = move.x0 + t * (move.x1 - move.x0), move.y0 + t * (move.y1 - move.y0)
        col = np.floor((x - frame.x0) / frame.pitch_x).astype(np.intp)
        row = np.floor((frame.y0 + frame.height - y) / frame.pitch_y).astype(np.intp)
        inside = (row >= 0) & (row < frame.rows) & (col >= 0) & (col < frame.cols)
        np.add.at(image, (row[inside], col[inside]), move.s / maximum * length / n)
    return image


_WORD_RE = re.compile(r'([A-Z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))', re.IGNORECASE)
_COMMENT_RE = re.compile(r'\([^()]*\)')


def gcode_motion(lines, units='MM', arc_res=0.1, laser=False, check_abort=None):
    """Split arcs with the Sender's numeric interpolator, then interpret fail-closed."""
    from appPlugins.Common.gcode_arcs import split_arcs

    if units not in ('MM', 'IN') or not math.isfinite(arc_res) or arc_res <= 0:
        raise PhotoCarveError('Known units and a positive arc resolution are required.')
    lines = lines.splitlines() if isinstance(lines, str) else list(lines)
    clean, validation, motion = [], [], None
    for raw in lines:
        line = raw.split(';', 1)[0].upper()
        while _COMMENT_RE.search(line):
            line = _COMMENT_RE.sub('', line)
        if not line.strip() or line.strip() == '%':
            continue
        words = _WORD_RE.findall(line)
        for axis in 'IJR':
            if sum(k == axis for k, _n in words) > 1:
                raise PhotoCarveError('Duplicate arc geometry word: %s.' % axis)
        motions = [float(n) for k, n in words if k == 'G' and float(n) in (0, 1, 2, 3)]
        if motions:
            motion = motions[-1]
        if len(motions) > 1:
            raise PhotoCarveError('Conflicting motion commands.')
        # Validate the original blocks before interpolation can consume any words.
        masked = _WORD_RE.sub(lambda m: ('G1' if m[1].upper() == 'G' and float(m[2]) in (2, 3)
                                        else '' if motion in (2, 3) and m[1].upper() in 'IJR' else m[0]), line)
        validation.append(masked)
        if laser and motion == 0 and any(k in 'XY' for k, _n in words) and not any(k == 'Z' for k, _n in words):
            # Project laser rapids onto XY without inventing a motion before the G54 preamble.
            line += ' Z0'
        clean.append(line)
    interpret_gcode('\n'.join(validation), units, laser, check_abort)
    out, _count, _source, unsplit = split_arcs(clean, units, arc_res)
    if unsplit:
        raise PhotoCarveError('%d arc(s) could not be resolved; simulation refused.' % len(unsplit))
    return interpret_gcode('\n'.join(out), units, laser, check_abort)


def interpret_gcode(text, units='MM', laser=False, check_abort=None):
    """Interpret only known absolute linear motion; callers must split arcs first.

    Unknown frames, offsets, cycles and macros fail closed instead of displaying a misleading simulation.
    """
    if units not in ('MM', 'IN'):
        raise PhotoCarveError('Simulation units must be MM or IN.')
    position = [math.nan, math.nan, math.nan]
    motion, enabled, power, moved = None, False, 0.0, False
    factor = 1.0
    result = []
    allowed_g = {0, 1, 4, 17, 20, 21, 54, 90, 94}
    allowed_m = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 30}
    for line_number, raw in enumerate(text.splitlines(), 1):
        if check_abort is not None and line_number % 1000 == 0:
            check_abort()
        line = raw.split(';', 1)[0]
        while _COMMENT_RE.search(line):
            line = _COMMENT_RE.sub('', line)
        line = line.strip().upper()
        if not line or line == '%':
            continue
        words = _WORD_RE.findall(line)
        if _WORD_RE.sub('', line).strip():
            raise PhotoCarveError('Unsupported G-code syntax on line %d.' % line_number)
        g, m, values = [], [], {}
        for letter, number in words:
            value = float(number)
            if not math.isfinite(value):
                raise PhotoCarveError('Non-finite G-code value on line %d.' % line_number)
            if letter in ('G', 'M'):
                allowed = allowed_g if letter == 'G' else allowed_m
                if value not in allowed:
                    raise PhotoCarveError('Unsupported %s%s on line %d; split arcs and use absolute linear motion.'
                                          % (letter, number, line_number))
                (g if letter == 'G' else m).append(int(value))
            elif letter in ('X', 'Y', 'Z', 'F', 'S', 'T', 'P') and letter not in values:
                values[letter] = value
            else:
                raise PhotoCarveError('Unsupported or duplicate %s word on line %d.' % (letter, line_number))
        for group in ({0, 1, 4}, {20, 21}):
            if len(group.intersection(g)) > 1:
                raise PhotoCarveError('Conflicting modal commands on line %d.' % line_number)
        if 54 in g and moved:
            raise PhotoCarveError('G54 is only supported before the first motion.')
        if 20 in g:
            factor = 25.4 if units.upper() == 'MM' else 1.0
        if 21 in g:
            factor = 1.0 if units.upper() == 'MM' else 1 / 25.4
        for command in m:
            if command in (3, 4):
                enabled = True
            elif command == 5:
                enabled = False
            elif command == 6:
                position = [math.nan, math.nan, math.nan]
        if 'S' in values:
            if values['S'] < 0:
                raise PhotoCarveError('Laser/spindle power must not be negative.')
            power = values['S']
        if 4 in g:
            if set(values) - {'P', 'F', 'S', 'T'}:
                raise PhotoCarveError('Dwell does not accept motion axes.')
            continue
        if 'P' in values:
            raise PhotoCarveError('P words are only supported for dwell.')
        if 0 in g:
            motion = 0
        if 1 in g:
            motion = 1
        if not any(axis in values for axis in 'XYZ'):
            continue
        if motion is None:
            raise PhotoCarveError('Motion axes require a known G0/G1 mode.')
        target = [values[axis] * factor if axis in values else position[index] for index, axis in enumerate('XYZ')]
        if motion == 1 and not all(math.isfinite(v) for v in position[:2] + target[:2]):
            raise PhotoCarveError('Cutting motion requires known starting and ending XY positions.')
        if motion == 1 and not laser and not all(math.isfinite(v) for v in (position[2], target[2])):
            raise PhotoCarveError('Milling motion requires a known Z position.')
        if all(math.isfinite(v) for v in position[:2] + target[:2]):
            if laser or all(math.isfinite(v) for v in (position[2], target[2])):
                result.append(Span(position[0], position[1], position[2], target[0], target[1], target[2],
                                   motion == 0, enabled, power))
        position = target
        moved = True
    return result
