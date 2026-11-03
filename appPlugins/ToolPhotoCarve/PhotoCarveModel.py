"""Pure photo engraving contracts, physical image sampling and resource limits."""

import math
from dataclasses import dataclass, fields, replace

import numpy as np
from PIL import Image, ImageFilter, ImageOps


MAX_CELLS = 25_000_000
MAX_KERNEL = 40_000
MAX_POINTS = 50_000_000
MAX_LEVELS = 10_000

DEFAULTS = {
    'x0': 0.0, 'y0': 0.0, 'width': 100.0, 'res': 0.1, 'mode': 'vcarve', 'tone': 'vbit_area',
    'strategy': 'raster', 'autocontrast': False, 'equalize': False, 'blur': 0.0, 'sharpen': 0.0,
    'brightness': 0.0, 'contrast': 0.0, 'gamma': 1.0, 'invert': False, 'mirror': False,
    'angle': 0.0, 'spacing': 1.0, 'min_depth': 0.0, 'max_depth': 1.5, 'tool_kind': 'vbit',
    'tool_dia': 6.0, 'tool_angle': 60.0, 'tool_tip': 0.0, 'rough': False, 'rough_kind': 'flat',
    'rough_dia': 3.0, 'rough_angle': 90.0, 'rough_tip': 0.0, 'stepdown': 1.0, 'allowance': 0.2,
    'litho_min': 0.8, 'litho_max': 3.0, 'frame': 3.0, 'tolerance': 0.01, 'lift_gap': 3.0,
    'overscan': 2.0, 'levels': 256, 'sim_res': 0.1,
}
PHOTOCARVE_KEYS = tuple('tools_photocarve_' + name for name in DEFAULTS)


class PhotoCarveError(ValueError):
    """A user-readable failure, translated at the application boundary."""


@dataclass(frozen=True)
class ImageFrame:
    x0: float
    y0: float
    width: float
    height: float
    res_x_req: float
    res_y_req: float

    @property
    def cols(self):
        return max(1, round(self.width / self.res_x_req))

    @property
    def rows(self):
        return max(1, round(self.height / self.res_y_req))

    @property
    def pitch_x(self):
        return self.width / self.cols

    @property
    def pitch_y(self):
        return self.height / self.rows

    def center(self, row, col):
        return (self.x0 + (col + 0.5) * self.pitch_x,
                self.y0 + self.height - (row + 0.5) * self.pitch_y)


@dataclass(frozen=True)
class ToolShape:
    kind: str
    diameter: float
    angle_deg: float = 90.0
    tip_diameter: float = 0.0


@dataclass
class Pass:
    role: str
    tool: ToolShape
    paths: list
    z_is_power: bool = False


@dataclass
class JobResult:
    passes: list
    warnings: list
    stats: dict


@dataclass(frozen=True)
class JobParams:
    x0: float = 0.0
    y0: float = 0.0
    width: float = 100.0
    res: float = 0.1
    mode: str = 'vcarve'
    tone: str = 'vbit_area'
    strategy: str = 'raster'
    autocontrast: bool = False
    equalize: bool = False
    blur: float = 0.0
    sharpen: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    gamma: float = 1.0
    invert: bool = False
    mirror: bool = False
    angle: float = 0.0
    spacing: float = 1.0
    min_depth: float = 0.0
    max_depth: float = 1.5
    tool_kind: str = 'vbit'
    tool_dia: float = 6.0
    tool_angle: float = 60.0
    tool_tip: float = 0.0
    rough: bool = False
    rough_kind: str = 'flat'
    rough_dia: float = 3.0
    rough_angle: float = 90.0
    rough_tip: float = 0.0
    stepdown: float = 1.0
    allowance: float = 0.2
    litho_min: float = 0.8
    litho_max: float = 3.0
    frame: float = 3.0
    tolerance: float = 0.01
    lift_gap: float = 3.0
    overscan: float = 2.0
    levels: int = 256
    sim_res: float = 0.1
    image_px: tuple = (1, 1)
    units: str = 'MM'
    decimals: int = 4
    skew: tuple = (0.0, 0.0, 0.0, 0.0)

    @property
    def height(self):
        return self.width * self.image_px[1] / self.image_px[0]

    @classmethod
    def from_options(cls, options, image_px, units, decimals, skew):
        values = {name: options.get(key, DEFAULTS[name])
                  for name, key in zip(DEFAULTS, PHOTOCARVE_KEYS)}
        return cls(**values, image_px=tuple(image_px), units=units, decimals=decimals, skew=tuple(skew))

    def tool(self, rough=False):
        if rough:
            return ToolShape(self.rough_kind, self.rough_dia, self.rough_angle, self.rough_tip)
        return ToolShape(self.tool_kind, self.tool_dia, self.tool_angle, self.tool_tip)


def mode_frame(params):
    """The only job frame factory; laser spacing is the physical scan-row pitch."""
    return ImageFrame(params.x0, params.y0, params.width, params.height, params.res,
                      params.spacing if params.mode == 'laser' else params.res)


def xy_budget(decimals, skew):
    """Maximum model-space error of one final machine-space XY rounding."""
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 15:
        raise PhotoCarveError('Coordinate decimals must be an integer between 0 and 15.')
    if len(skew) != 4 or not all(math.isfinite(v) for v in skew):
        raise PhotoCarveError('Bed calibration must contain four finite values.')
    sx, sy, lx, ly = skew
    if (sx and ly <= 0) or (sy and lx <= 0):
        raise PhotoCarveError('Nonzero bed skew requires a positive corresponding bed limit.')
    a, b = sx / ly if sx else 0.0, sy / lx if sy else 0.0
    det = 1 - a * b
    if not math.isfinite(det) or abs(det) <= 1e-9:
        raise PhotoCarveError('Bed calibration is singular; correct the skew and bed limits.')
    # Largest singular value of [[1, -a], [-b, 1]] / det, using scalar arithmetic.
    scale = max(1.0, abs(a), abs(b))
    u, v, w = 1 / scale, a / scale, b / scale
    trace = 2 * u * u + v * v + w * w
    delta = math.hypot(v * v - w * w, 2 * u * (v + w))
    gain = scale / abs(det) * math.sqrt((trace + delta) / 2)
    budget = gain * 10.0 ** -decimals * math.sqrt(2) / 2
    if not math.isfinite(budget):
        raise PhotoCarveError('Bed calibration produces a non-finite coordinate error.')
    return budget


def quantize_z(z, decimals):
    q = 10.0 ** -decimals
    return np.round(np.ceil(np.asarray(z, dtype=np.float64) / q - 1e-9) * q, decimals)


def vbit_limit(params):
    return max(0.0, (min(params.spacing, params.tool_dia) - params.tool_tip) /
               (2 * math.tan(math.radians(params.tool_angle) / 2)))


def _ceil_ratio(length, pitch, label):
    ratio = length / pitch
    if not math.isfinite(ratio):
        raise PhotoCarveError('%s exceeds the resource limit; increase resolution or spacing.' % label)
    return math.ceil(ratio)


def kernel_count(radius, res_x, res_y, xy_err=0.0, axis='point', check_abort=None):
    """Exact rectangle/axis-segment footprint count, without a per-cell loop or array."""
    reach = radius + xy_err
    nr = 2 * _ceil_ratio(reach, res_y, 'Tool kernel') + 3
    nc = 2 * _ceil_ratio(reach, res_x, 'Tool kernel') + 3
    if nr * nc > 2 * MAX_KERNEL:
        raise PhotoCarveError('Tool kernel bound has %d cells; limit is %d. Increase resolution or reduce tool diameter.'
                              % (nr * nc, MAX_KERNEL))
    hx = res_x * (1.5 if axis == 'x' else 0.5)
    hy = res_y * (1.5 if axis == 'y' else 0.5)
    count = 0
    row_limit = math.ceil((reach + hy) / res_y)
    for row in range(-row_limit, row_limit + 1):
        if check_abort is not None and row % 64 == 0:
            check_abort()
        dy = max(abs(row * res_y) - hy, 0.0)
        if dy >= reach:
            continue
        half = (hx + math.sqrt(max(0.0, reach * reach - dy * dy))) / res_x
        # Strict radius membership, including exact tangencies.
        last = math.ceil(half) - 1
        count += 2 * last + 1
        if count > MAX_KERNEL:
            raise PhotoCarveError('Tool kernel has more than %d cells; increase resolution or reduce tool diameter.'
                                  % MAX_KERNEL)
    return count


def work_preflight(params, frame):
    """Conservative point bounds, before creating an image grid or path array."""
    diag = math.hypot(frame.width, frame.height)
    step = min(frame.pitch_x, frame.pitch_y)
    ceil = _ceil_ratio
    if params.mode == 'vcarve' and params.strategy == 'raster':
        lines = ceil(diag, params.spacing, 'Raster lines') + 1
        points = 3 * lines * (ceil(diag, step, 'Raster points') + 1)
    elif params.mode == 'vcarve':
        radius = diag / 2
        length = math.pi * radius * radius / params.spacing + radius
        ns = ceil(length, step, 'Spiral samples') + 1
        nd = ceil(length, step / 4, 'Dense spiral samples') + 1
        points = max(3 * ns, nd)
    elif params.mode == 'halftone':
        points = ((ceil(frame.height, params.spacing * math.sqrt(3) / 2, 'Dot rows') + 1) *
                  (ceil(frame.width, params.spacing, 'Dot columns') + 2))
    elif params.mode == 'laser':
        points = frame.rows * (frame.cols + 1 + 3 * math.ceil(frame.cols / 2))
        if params.strategy == 'floyd' and frame.rows * frame.cols > 4_000_000:
            raise PhotoCarveError('Floyd dithering is limited to 4000000 cells; increase resolution or spacing.')
    else:
        k = max(1, round(params.spacing / frame.pitch_y))
        kx = max(1, round(params.spacing / frame.pitch_x))
        points = 0
        if params.strategy in ('x', 'xy'):
            points += math.ceil(frame.rows / k) * frame.cols
        if params.strategy in ('y', 'xy'):
            points += math.ceil(frame.cols / kx) * frame.rows
    if points > MAX_POINTS:
        raise PhotoCarveError('Job needs up to %d points; limit is %d. Increase spacing or resolution.'
                              % (points, MAX_POINTS))
    rough_points, levels = 0, 0
    if params.rough:
        depth = (params.litho_max - params.litho_min if params.mode == 'lithophane' else
                 min(params.max_depth, vbit_limit(params)) if params.mode in ('vcarve', 'halftone') else
                 params.max_depth)
        levels = ceil(depth, params.stepdown, 'Roughing levels')
        if levels > MAX_LEVELS:
            raise PhotoCarveError('Roughing needs %d levels; limit is %d. Use stepdown at least %.12g.'
                                  % (levels, MAX_LEVELS, depth / MAX_LEVELS))
        k = max(1, round(params.spacing / frame.pitch_y))
        rough_points = 3 * levels * math.ceil(frame.rows / k) * frame.cols
        if rough_points > MAX_POINTS:
            raise PhotoCarveError('Roughing needs up to %d points; limit is %d. Increase stepdown or spacing.'
                                  % (rough_points, MAX_POINTS))
    return {'points': points, 'rough_points': rough_points, 'levels': levels}


def validate(params, check_abort=None):
    """Validate in dependency order and return immutable effective parameters and warnings."""
    for field in fields(params):
        value = getattr(params, field.name)
        if field.type is float:
            try:
                finite = math.isfinite(value)
            except (TypeError, ValueError):
                finite = False
            if not finite:
                raise PhotoCarveError('%s must be a finite number.' % field.name)
    if isinstance(params.levels, bool) or not isinstance(params.levels, (int, np.integer)) or params.levels < 2:
        raise PhotoCarveError('Laser levels must be an integer of at least 2.')
    for name in ('width', 'res', 'spacing', 'sim_res', 'gamma'):
        if getattr(params, name) <= 0:
            raise PhotoCarveError('%s must be positive.' % name)
    for name in ('tolerance', 'lift_gap', 'overscan'):
        if getattr(params, name) < 0:
            raise PhotoCarveError('%s must not be negative.' % name)
    strategies = {'vcarve': ('raster', 'spiral'), 'halftone': ('-',), 'relief': ('x', 'y', 'xy'),
                  'lithophane': ('x', 'y', 'xy'), 'laser': ('gray', 'bayer', 'floyd')}
    if params.mode not in strategies or params.strategy not in strategies[params.mode]:
        raise PhotoCarveError('Select a valid strategy for the engraving mode.')
    tones = {'vcarve': ('linear', 'perceptual', 'vbit_area'), 'relief': ('linear', 'perceptual'),
             'lithophane': ('lithophane',)}
    if params.mode in tones and params.tone not in tones[params.mode]:
        raise PhotoCarveError('Select a valid tone mapping for the engraving mode.')
    for kind in (params.tool_kind, params.rough_kind):
        if kind not in ('vbit', 'ball', 'flat'):
            raise PhotoCarveError('Tool kind must be vbit, ball or flat.')
    if params.mode in ('vcarve', 'halftone') and params.tool_kind != 'vbit':
        raise PhotoCarveError('V-carving and halftone dots require a V-bit.')
    used_tools = ([] if params.mode == 'laser' else [params.tool()]) + ([params.tool(True)] if params.rough else [])
    for tool in used_tools:
        if tool.diameter <= 0 or not 0 <= tool.tip_diameter < tool.diameter:
            raise PhotoCarveError('Tool diameter must be positive and tip diameter must be smaller and nonnegative.')
        if tool.kind == 'vbit' and not 0 < tool.angle_deg < 180:
            raise PhotoCarveError('V-bit angle must be between 0 and 180 degrees.')
    warnings = []
    if params.mode == 'lithophane':
        if not 0 < params.litho_min < params.litho_max or params.frame < 0:
            raise PhotoCarveError('Lithophane needs 0 < minimum < maximum thickness and a nonnegative frame.')
    elif params.mode != 'laser':
        if not 0 <= params.min_depth <= params.max_depth or params.max_depth <= 0:
            raise PhotoCarveError('Depths must satisfy 0 <= minimum <= maximum, with maximum positive.')
        if params.mode in ('vcarve', 'halftone'):
            limit = vbit_limit(params)
            if params.min_depth > limit:
                raise PhotoCarveError('Minimum depth exceeds the V-bit width limit %.12g.' % limit)
            if params.max_depth > limit:
                params = replace(params, max_depth=limit)
                warnings.append('Maximum depth clamped to the V-bit width limit %.12g.' % limit)
    if params.rough and (params.stepdown <= 0 or params.allowance < 0):
        raise PhotoCarveError('Roughing needs positive stepdown and a nonnegative allowance.')
    if (len(params.image_px) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, np.integer)) or v <= 0
                                         for v in params.image_px)):
        raise PhotoCarveError('Image dimensions must be positive integers.')
    if not math.isfinite(params.height) or params.height <= 0:
        raise PhotoCarveError('Physical image height must be finite and positive.')
    xy_err = xy_budget(params.decimals, params.skew)
    if 10.0 ** -params.decimals > params.tolerance:
        raise PhotoCarveError('Coordinate quantum exceeds tolerance; increase coordinate decimals.')
    for length, pitch in ((params.width, params.res),
                          (params.height, params.spacing if params.mode == 'laser' else params.res)):
        _ceil_ratio(length, pitch, 'Image grid')
    frame = mode_frame(params)
    if frame.rows * frame.cols > MAX_CELLS:
        raise PhotoCarveError('Image grid has %d cells; limit is %d. Increase resolution or laser spacing.'
                              % (frame.rows * frame.cols, MAX_CELLS))
    radius = max((tool.diameter / 2 for tool in used_tools), default=0.0)
    sim_cols = max(1, _ceil_ratio(params.width + 2 * radius, params.sim_res, 'Simulation grid'))
    sim_rows = max(1, _ceil_ratio(params.height + 2 * radius, params.sim_res, 'Simulation grid'))
    if sim_cols * sim_rows > MAX_CELLS:
        raise PhotoCarveError('Simulation grid has %d cells; limit is %d. Increase simulation resolution.'
                              % (sim_cols * sim_rows, MAX_CELLS))
    for tool in used_tools:
        for rx, ry in ((frame.pitch_x, frame.pitch_y), (params.sim_res, params.sim_res)):
            for axis in ('point', 'x', 'y'):
                kernel_count(tool.diameter / 2, rx, ry, xy_err, axis, check_abort)
    work_preflight(params, frame)
    return params, warnings


def load_image(filename):
    """Apply EXIF orientation before size capture and detach from the source file."""
    try:
        with Image.open(filename) as image:
            return ImageOps.exif_transpose(image).copy()
    except (OSError, ValueError, Image.DecompressionBombError) as err:
        raise PhotoCarveError('Cannot load image: %s' % err) from err


def prepare_image(image, params):
    """White composite, physical filtering and a single floating-point gamma operation."""
    frame = mode_frame(params)
    rgba = image.convert('RGBA')
    white = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
    gray = Image.alpha_composite(white, rgba).convert('L')
    if params.autocontrast:
        gray = ImageOps.autocontrast(gray, cutoff=1)
    if params.equalize:
        gray = ImageOps.equalize(gray)
    if params.mirror:
        gray = ImageOps.mirror(gray)
    gray = gray.resize((frame.cols, frame.rows), Image.Resampling.LANCZOS)
    pitch = min(frame.pitch_x, frame.pitch_y)
    if params.blur > 0:
        gray = gray.filter(ImageFilter.GaussianBlur(params.blur / pitch))
    if params.sharpen > 0:
        gray = gray.filter(ImageFilter.UnsharpMask(radius=params.sharpen / pitch))
    grid = np.asarray(gray, dtype=np.float32) / np.float32(255)
    grid += np.float32(params.brightness)
    grid = (grid - np.float32(0.5)) * np.float32(1 + params.contrast) + np.float32(0.5)
    np.clip(grid, 0, 1, out=grid)
    np.power(grid, params.gamma, out=grid)
    if params.invert:
        grid = 1 - grid
    return grid


def depth_grid(gray, params):
    """Positive removal depth; row zero corresponds to the frame's maximum Y."""
    gray = np.asarray(gray, dtype=np.float32)
    if params.tone == 'lithophane':
        transmission = np.clip(gray, 0.02, 1)
        thickness = params.litho_min + (params.litho_max - params.litho_min) * np.log(transmission) / math.log(0.02)
        depth = np.asarray(params.litho_max - thickness, dtype=np.float32)
        frame = mode_frame(params)
        nx, ny = math.ceil(params.frame / frame.pitch_x), math.ceil(params.frame / frame.pitch_y)
        if nx:
            depth[:, :nx] = depth[:, -nx:] = 0
        if ny:
            depth[:ny, :] = depth[-ny:, :] = 0
        return depth
    darkness = 1 - gray
    if params.tone == 'perceptual':
        luminance = np.where(gray <= 0.04045, gray / 12.92, ((gray + 0.055) / 1.055) ** 2.4)
        lightness = np.where(luminance > (6 / 29) ** 3,
                             116 * np.cbrt(luminance) - 16, (29 / 3) ** 3 * luminance)
        darkness = 1 - lightness / 100
    if params.tone == 'vbit_area':
        width = np.sqrt(darkness) * min(params.spacing, params.tool_dia)
        depth = (width - params.tool_tip) / (2 * math.tan(math.radians(params.tool_angle) / 2))
        return np.asarray(np.clip(depth, params.min_depth, params.max_depth), dtype=np.float32)
    return np.asarray(params.min_depth + darkness * (params.max_depth - params.min_depth), dtype=np.float32)


def clip_segment(a, b, frame):
    """Liang-Barsky clipping in model coordinates, with exact frame boundary endpoints."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    delta = b - a
    lo, hi = 0.0, 1.0
    for p, q in ((-delta[0], a[0] - frame.x0), (delta[0], frame.x0 + frame.width - a[0]),
                 (-delta[1], a[1] - frame.y0), (delta[1], frame.y0 + frame.height - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return None
    bounds = ([frame.x0, frame.y0], [frame.x0 + frame.width, frame.y0 + frame.height])
    return np.clip(np.array([a + lo * delta, a + hi * delta]), *bounds)


def raster_paths(params, check_abort=None):
    frame = mode_frame(params)
    work_preflight(params, frame)
    angle = math.radians(params.angle)
    direction = np.array([math.cos(angle), math.sin(angle)])
    normal = np.array([-direction[1], direction[0]])
    center = np.array([frame.x0 + frame.width / 2, frame.y0 + frame.height / 2])
    extent = (abs(normal[0]) * frame.width + abs(normal[1]) * frame.height) / 2
    diag = math.hypot(frame.width, frame.height)
    step = min(frame.pitch_x, frame.pitch_y)
    first = math.ceil((-extent - params.spacing / 2) / params.spacing)
    last = math.floor((extent - params.spacing / 2) / params.spacing)
    paths, count = [], 0
    for k in range(first, last + 1):
        if check_abort is not None:
            check_abort()
        origin = center + normal * (params.spacing / 2 + k * params.spacing)
        segment = clip_segment(origin - diag * direction, origin + diag * direction, frame)
        if segment is None or np.linalg.norm(segment[1] - segment[0]) < 1e-12:
            continue
        n = max(2, math.ceil(float(np.linalg.norm(segment[1] - segment[0])) / step) + 1)
        count += n
        if count > MAX_POINTS:
            raise PhotoCarveError('Raster paths exceed the point limit; increase spacing or resolution.')
        path = np.linspace(segment[0], segment[1], n, dtype=np.float64)
        paths.append(path[::-1].copy() if len(paths) % 2 else path)
    return paths


def spiral_paths(params, check_abort=None):
    frame = mode_frame(params)
    work_preflight(params, frame)
    step = min(frame.pitch_x, frame.pitch_y)
    radius = math.hypot(frame.width, frame.height) / 2
    b = params.spacing / (2 * math.pi)
    theta_max = radius / b
    length = b / 2 * (theta_max * math.hypot(1, theta_max) + math.asinh(theta_max))
    nd = math.ceil(length / (step / 4)) + 1
    if nd > MAX_POINTS:
        raise PhotoCarveError('Dense spiral exceeds the point limit; increase spacing or resolution.')
    s = np.linspace(0, length, nd)
    theta = np.minimum(s / b, np.sqrt(2 * s / b))
    for _ in range(8):
        if check_abort is not None:
            check_abort()
        root = np.hypot(1, theta)
        theta -= (b / 2 * (theta * root + np.arcsinh(theta)) - s) / (b * root)
    center = np.array([frame.x0 + frame.width / 2, frame.y0 + frame.height / 2])
    dense = np.column_stack((b * theta * np.cos(theta), b * theta * np.sin(theta))) + center
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))]
    ns = math.ceil(float(distance[-1]) / step) + 1
    sample = np.linspace(0, distance[-1], ns)
    points = np.column_stack((np.interp(sample, distance, dense[:, 0]),
                              np.interp(sample, distance, dense[:, 1])))
    paths, current, count = [], [], 0
    for index, (a, end) in enumerate(zip(points, points[1:])):
        if check_abort is not None and index % 1000 == 0:
            check_abort()
        segment = clip_segment(a, end, frame)
        if segment is None:
            if current:
                paths.append(np.asarray(current, dtype=np.float64))
                current = []
            continue
        if current and not np.allclose(current[-1], segment[0], rtol=0, atol=1e-12):
            paths.append(np.asarray(current, dtype=np.float64))
            current = []
        if not current:
            current.append(segment[0])
            count += 1
        if np.linalg.norm(segment[1] - current[-1]) > 1e-12:
            current.append(segment[1])
            count += 1
        if count > MAX_POINTS:
            raise PhotoCarveError('Clipped spiral exceeds the point limit; increase spacing or resolution.')
    if current:
        paths.append(np.asarray(current, dtype=np.float64))
    return [path for path in paths if len(path) > 1]


def bilinear(grid, xy, frame):
    xy = np.asarray(xy, dtype=np.float64)
    col = np.clip((xy[:, 0] - frame.x0) / frame.pitch_x - 0.5, 0, frame.cols - 1)
    row = np.clip((frame.y0 + frame.height - xy[:, 1]) / frame.pitch_y - 0.5, 0, frame.rows - 1)
    c, r = np.floor(col).astype(np.intp), np.floor(row).astype(np.intp)
    c1, r1 = np.minimum(c + 1, frame.cols - 1), np.minimum(r + 1, frame.rows - 1)
    u, v = col - c, row - r
    return (grid[r, c] * (1 - u) * (1 - v) + grid[r, c1] * u * (1 - v) +
            grid[r1, c] * (1 - u) * v + grid[r1, c1] * u * v)


def safe_rdp(path, tolerance, check_abort=None):
    """3D RDP that never lowers the chord below an input vertex."""
    path = np.asarray(path, dtype=np.float64)
    if len(path) <= 2:
        return path.copy()
    keep = np.zeros(len(path), dtype=bool)
    keep[0] = keep[-1] = True
    pending = [(0, len(path) - 1)]
    while pending:
        if check_abort is not None:
            check_abort()
        first, last = pending.pop()
        if last - first < 2:
            continue
        a, b = path[first], path[last]
        interior = path[first + 1:last]
        delta = b - a
        norm = float(delta @ delta)
        t = np.clip((interior - a) @ delta / norm, 0, 1) if norm else np.zeros(len(interior))
        distance = np.linalg.norm(interior - (a + t[:, None] * delta), axis=1)
        xy_norm = float(delta[:2] @ delta[:2])
        u = np.clip((interior[:, :2] - a[:2]) @ delta[:2] / xy_norm, 0, 1) if xy_norm else t
        gouge = interior[:, 2] - (a[2] + u * delta[2])
        if np.max(gouge) > 1e-12:
            split = first + 1 + int(np.argmax(gouge))
        elif np.max(distance) > tolerance:
            split = first + 1 + int(np.argmax(distance))
        else:
            continue
        keep[split] = True
        pending.extend(((first, split), (split, last)))
    return path[keep].copy()


def tool_lift(tool, rho):
    rho = np.asarray(rho, dtype=np.float64)
    radius = tool.diameter / 2
    if tool.kind == 'flat':
        return np.zeros_like(rho)
    if tool.kind == 'ball':
        return radius - np.sqrt(np.maximum(0, radius * radius - np.minimum(rho, radius) ** 2))
    return np.maximum(0, rho - tool.tip_diameter / 2) / math.tan(math.radians(tool.angle_deg) / 2)


def relief_kernel(tool, frame, xy_err, axis, check_abort=None):
    """Minimum column-rectangle distance to a stationary center or next-center segment."""
    radius = tool.diameter / 2
    kernel_count(radius, frame.pitch_x, frame.pitch_y, xy_err, axis, check_abort)
    nr = math.ceil((radius + xy_err) / frame.pitch_y) + 2
    nc = math.ceil((radius + xy_err) / frame.pitch_x) + 2
    columns = np.arange(-nc, nc + 1)
    x = columns * frame.pitch_x
    dx = np.maximum(np.maximum(x - frame.pitch_x / 2 - (frame.pitch_x if axis == 'x' else 0),
                               -x - frame.pitch_x / 2), 0)
    result = []
    for row in range(-nr, nr + 1):
        if check_abort is not None and row % 64 == 0:
            check_abort()
        y = row * frame.pitch_y
        dy = max(y - frame.pitch_y / 2 - (frame.pitch_y if axis == 'y' else 0), -y - frame.pitch_y / 2, 0)
        rho = np.maximum(0, np.hypot(dx, dy) - xy_err)
        mask = rho < radius
        lifts = tool_lift(tool, rho[mask])
        result.extend((row, int(col), float(lift)) for col, lift in zip(columns[mask], lifts))
    if len(result) > MAX_KERNEL:
        raise PhotoCarveError('Tool kernel exceeds the safety limit.')
    return result


def _requirements(depth, frame, kernel, row, col, axis, check_abort):
    """A row/column block; outside the image is uncut stock at Z zero."""
    n = (frame.cols if axis == 'x' else frame.rows) if axis != 'point' else 1
    if axis != 'point':
        n -= 1
    positions = np.arange(n)
    base_rows = row + (positions if axis == 'y' else 0)
    base_cols = col + (positions if axis == 'x' else 0)
    safe = np.full(n, -np.inf, dtype=np.float64)
    for index, (dr, dc, lift) in enumerate(kernel):
        if check_abort is not None and index % 64 == 0:
            check_abort()
        rr, cc = base_rows + dr, base_cols + dc
        valid = (rr >= 0) & (rr < frame.rows) & (cc >= 0) & (cc < frame.cols)
        target = np.zeros(n, dtype=np.float64)
        if axis == 'x':
            target[valid] = -depth[int(rr), cc[valid]] if 0 <= rr < frame.rows else 0
        elif axis == 'y':
            target[valid] = -depth[rr[valid], int(cc)] if 0 <= cc < frame.cols else 0
        elif bool(valid):
            target[0] = -depth[int(rr), int(cc)]
        np.maximum(safe, target - lift, out=safe)
    return safe


def relief_paths(depth, params, tool, check_abort=None):
    frame = mode_frame(params)
    xy_err = xy_budget(params.decimals, params.skew)
    paths, certificate, total = [], [], 0
    for axis in ('x', 'y'):
        if params.strategy not in (axis, 'xy'):
            continue
        varying = frame.cols if axis == 'x' else frame.rows
        fixed = frame.rows if axis == 'x' else frame.cols
        pitch = frame.pitch_y if axis == 'x' else frame.pitch_x
        stride = max(1, round(params.spacing / pitch))
        kind = axis if varying > 1 else 'point'
        kernel = relief_kernel(tool, frame, xy_err, kind, check_abort)
        for index, fixed_index in enumerate(range(0, fixed, stride)):
            if check_abort is not None:
                check_abort()
            row, col = (fixed_index, 0) if axis == 'x' else (0, fixed_index)
            segments = _requirements(depth, frame, kernel, row, col, kind, check_abort)
            if varying == 1:
                z = segments
            else:
                z = np.r_[segments[0], np.maximum(segments[:-1], segments[1:]), segments[-1]]
            indices = np.arange(varying)
            x, y = frame.center(row + (indices if axis == 'y' else 0), col + (indices if axis == 'x' else 0))
            path = np.column_stack((np.broadcast_to(x, varying), np.broadcast_to(y, varying), z))
            record = {'kind': kind, 'axis': axis, 'fixed': fixed_index, 'indices': indices.copy(), 'z': z.copy()}
            if kind == 'point':
                record.update(x=float(path[0, 0]), y=float(path[0, 1]))
            if index % 2:
                path = path[::-1].copy()
            path = safe_rdp(path, params.tolerance, check_abort)
            total += len(path)
            if total > MAX_POINTS:
                raise PhotoCarveError('Relief paths exceed the point limit; increase resolution or spacing.')
            paths.append(path)
            certificate.append(record)
    return paths, certificate


def certify_relief(paths, certificate, frame, check_abort=None):
    """Exhaustive center and output-vertex verification; no stride or sampled corners."""
    if len(paths) != len(certificate):
        raise PhotoCarveError('Relief certificate does not match the output paths.')
    margin = math.inf
    for path, record in zip(paths, certificate):
        if check_abort is not None:
            check_abort()
        path = np.asarray(path, dtype=np.float64)
        if not len(path) or path.shape[1] != 3 or not np.isfinite(path).all():
            raise PhotoCarveError('Relief path contains invalid coordinates.')
        if record['kind'] == 'point':
            if len(path) != 1 or np.max(np.abs(path[0, :2] - [record['x'], record['y']])) > 1e-9:
                raise PhotoCarveError('Relief plunge does not match its certified center.')
            difference = path[0, 2] - record['z'][0]
            where = path[0, :2]
        else:
            axis = record['axis']
            dimension = 0 if axis == 'x' else 1
            x, y = frame.center(record['fixed'] if axis == 'x' else 0,
                                0 if axis == 'x' else record['fixed'])
            fixed_coordinate = y if axis == 'x' else x
            if np.max(np.abs(path[:, 1 - dimension] - fixed_coordinate)) > 1e-9:
                raise PhotoCarveError('Relief path left its certified raster line.')
            indices = ((path[:, 0] - frame.x0) / frame.pitch_x - 0.5 if axis == 'x' else
                       (frame.y0 + frame.height - path[:, 1]) / frame.pitch_y - 0.5)
            rounded = np.rint(indices)
            centers = (frame.x0 + (rounded + 0.5) * frame.pitch_x if axis == 'x' else
                       frame.y0 + frame.height - (rounded + 0.5) * frame.pitch_y)
            if np.max(np.abs(path[:, dimension] - centers)) > 1e-9:
                raise PhotoCarveError('Relief vertices must be exact model raster centers.')
            z = path[:, 2]
            if len(indices) > 1 and indices[-1] < indices[0]:
                indices, z = indices[::-1], z[::-1]
            if len(indices) < 2 or np.any(np.diff(indices) <= 0):
                raise PhotoCarveError('Relief raster indices must be strictly increasing.')
            certified_indices = record['indices']
            if abs(indices[0] - certified_indices[0]) > 1e-9 or abs(indices[-1] - certified_indices[-1]) > 1e-9:
                raise PhotoCarveError('Relief path coverage differs from its certificate.')
            differences = np.interp(certified_indices, indices, z) - record['z']
            # Vertices are checked as well as every covered center, even after simplification.
            vertex_differences = z - np.interp(indices, certified_indices, record['z'])
            difference = min(float(np.min(differences)), float(np.min(vertex_differences)))
            bad = int(np.argmin(differences))
            row, col = ((record['fixed'], certified_indices[bad]) if axis == 'x' else
                        (certified_indices[bad], record['fixed']))
            where = frame.center(row, col)
        margin = min(margin, float(difference))
        if difference < -1e-9:
            raise PhotoCarveError('Relief gouge %.12g at X %.12g Y %.12g.' % (-difference, *where))
    return 0.0 if margin == math.inf else margin


def split_white(path, lift_gap):
    """Remove long white interiors, retaining their boundary points on both sides."""
    lit = np.flatnonzero(path[:, 2] < 0)
    if not len(lit):
        return []
    lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(path[:, :2], axis=0), axis=1))]
    start = max(0, int(lit[0]) - 1)
    result = []
    for a, b in zip(lit, lit[1:]):
        if b > a + 1 and lengths[b - 1] - lengths[a + 1] > lift_gap:
            result.append(path[start:min(len(path), int(a) + 2)].copy())
            start = int(b) - 1
    result.append(path[start:min(len(path), int(lit[-1]) + 2)].copy())
    return result


def groove_paths(depth, params, check_abort=None):
    frame = mode_frame(params)
    xy_paths = (spiral_paths(params, check_abort) if params.strategy == 'spiral' else
                raster_paths(params, check_abort))
    paths, count = [], 0
    for xy in xy_paths:
        if check_abort is not None:
            check_abort()
        z = -bilinear(depth, xy, frame)
        for piece in split_white(np.column_stack((xy, z)), params.lift_gap):
            path = safe_rdp(piece, params.tolerance, check_abort)
            count += len(path)
            if count > MAX_POINTS:
                raise PhotoCarveError('V-carve paths exceed the point limit; increase resolution or spacing.')
            paths.append(path)
    return paths


def halftone_paths(gray, params, check_abort=None):
    frame = mode_frame(params)
    work_preflight(params, frame)
    image = Image.fromarray(np.rint(np.clip(gray, 0, 1) * 255).astype(np.uint8))
    image = image.filter(ImageFilter.BoxBlur(params.spacing / (2 * min(frame.pitch_x, frame.pitch_y))))
    means = np.asarray(image, dtype=np.float32) / 255
    pitch_y = params.spacing * math.sqrt(3) / 2
    area = math.sqrt(3) / 2 * params.spacing ** 2
    tangent = math.tan(math.radians(params.tool_angle) / 2)
    paths, count = [], 0
    for row in range(math.ceil(frame.height / pitch_y)):
        if check_abort is not None:
            check_abort()
        y = frame.y0 + frame.height - (row + 0.5) * pitch_y
        if y < frame.y0:
            continue
        start = frame.x0 + params.spacing * (1 if row % 2 else 0.5)
        n = max(0, math.floor((frame.x0 + frame.width - start) / params.spacing) + 1)
        xy = np.column_stack((start + np.arange(n) * params.spacing, np.full(n, y)))
        darkness = 1 - bilinear(means, xy, frame)
        width = np.minimum(min(params.spacing, params.tool_dia), np.sqrt(4 * darkness * area / math.pi))
        depth = np.clip((width - params.tool_tip) / (2 * tangent), 0, params.max_depth)
        for point, z in zip(xy, depth):
            if z > 0.01:
                count += 1
                if count > MAX_POINTS:
                    raise PhotoCarveError('Halftone paths exceed the point limit; increase spacing.')
                paths.append(np.array([[point[0], point[1], -z]], dtype=np.float64))
    return paths


def rough_paths(depth, params, check_abort=None):
    """Level-limited row passes on the allowance surface, retaining only newly cut runs."""
    frame = mode_frame(params)
    rough_depth = np.maximum(depth - params.allowance, 0)
    base_params = replace(params, strategy='x')
    _, base_certificate = relief_paths(rough_depth, base_params, params.tool(True), check_abort)
    maximum = float(np.max(rough_depth))
    levels = math.ceil(maximum / params.stepdown)
    if levels > MAX_LEVELS:
        raise PhotoCarveError('Roughing exceeds the level limit; increase stepdown.')
    paths, certificate, count = [], [], 0
    for level_index in range(1, levels + 1):
        level = -min(maximum, level_index * params.stepdown)
        previous = -(level_index - 1) * params.stepdown
        for row_index, record in enumerate(base_certificate):
            if check_abort is not None:
                check_abort()
            zsafe = record['z']
            retain = zsafe < previous
            edges = np.flatnonzero(np.diff(np.r_[False, retain, False]))
            for first, last in zip(edges[::2], edges[1::2]):
                indices = record['indices'][first:last]
                z = np.maximum(level, zsafe[first:last])
                x, y = frame.center(record['fixed'], indices)
                path = np.column_stack((x, np.full(len(indices), y), z))
                cert = {**record, 'indices': indices.copy(), 'z': zsafe[first:last].copy()}
                if len(indices) == 1:
                    cert.update(kind='point', x=float(path[0, 0]), y=float(path[0, 1]))
                if row_index % 2:
                    path = path[::-1].copy()
                path = safe_rdp(path, params.tolerance, check_abort)
                count += len(path)
                if count > MAX_POINTS:
                    raise PhotoCarveError('Roughing paths exceed the point limit; increase stepdown or spacing.')
                paths.append(path)
                certificate.append(cert)
    return paths, certificate
