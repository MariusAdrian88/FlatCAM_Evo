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
