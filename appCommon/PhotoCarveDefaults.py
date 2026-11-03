"""Dependency-free photo engraving defaults, also exported by PhotoCarveModel."""

DEFAULTS = {
    'x0': 0.0, 'y0': 0.0, 'width': 100.0, 'res': 0.1, 'mode': 'vcarve', 'tone': 'vbit_area',
    'strategy': 'raster', 'autocontrast': False, 'equalize': False, 'blur': 0.0, 'sharpen': 0.0,
    'brightness': 0.0, 'contrast': 0.0, 'gamma': 1.0, 'invert': False,
    'angle': 0.0, 'spacing': 1.0, 'min_depth': 0.0, 'max_depth': 1.5, 'tool_kind': 'vbit',
    'tool_dia': 6.0, 'tool_angle': 60.0, 'tool_tip': 0.0, 'laser_dia': 0.1, 'rough': False, 'rough_kind': 'flat',
    'rough_dia': 3.0, 'rough_angle': 90.0, 'rough_tip': 0.0, 'stepdown': 1.0, 'allowance': 0.2,
    'litho_min': 0.8, 'litho_max': 3.0, 'frame': 3.0, 'tolerance': 0.01, 'lift_gap': 3.0,
    'overscan': 2.0, 'levels': 256, 'sim_res': 0.1, 'sim_material': 'wood', 'sim_thickness': 10.0,
}
PHOTOCARVE_KEYS = tuple('tools_photocarve_' + name for name in DEFAULTS)
