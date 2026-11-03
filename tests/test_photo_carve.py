"""Photo engraving: physical image sampling, safety limits and calibrated precision."""

import importlib.util
import math
import sys
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def _pure(name):
    root = Path(__file__).resolve().parents[1] / 'appPlugins/ToolPhotoCarve'
    package = '_photo_carve_tests'
    if package not in sys.modules:
        package_spec = importlib.util.spec_from_file_location(package, root / '__init__.py',
                                                             submodule_search_locations=[str(root)])
        assert package_spec is not None and package_spec.loader is not None
        module = importlib.util.module_from_spec(package_spec)
        sys.modules[package] = module
        package_spec.loader.exec_module(module)
    fullname = package + '.' + name
    if fullname in sys.modules:
        return sys.modules[fullname]
    spec = importlib.util.spec_from_file_location(fullname, root / (name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def model():
    return _pure('PhotoCarveModel')


def test_physical_frame_and_mode_pitch(model):
    params = model.JobParams(image_px=(40, 30), width=10, res=3, spacing=2)
    frame = model.mode_frame(params)
    assert (frame.cols, frame.rows) == (3, 2)
    assert frame.pitch_x == 10 / 3
    assert frame.pitch_y == 7.5 / 2
    assert frame.center(0, 0) == pytest.approx((10 / 6, 7.5 - 7.5 / 4))
    laser = model.mode_frame(replace(params, mode='laser', strategy='gray'))
    assert laser.rows == 4
    assert laser.pitch_y == 7.5 / 4


def test_options_capture_is_frozen_and_uses_only_prefixed_keys(model):
    params = model.JobParams.from_options(
        {'tools_photocarve_width': 12, 'width': 900}, (40, 30), 'IN', 6, (0, 0, 0, 0))
    assert params.width == 12
    assert params.height == 9
    assert params.units == 'IN'
    assert set(model.PHOTOCARVE_KEYS) == {'tools_photocarve_' + name for name in model.DEFAULTS}
    with pytest.raises(AttributeError):
        params.width = 20


@pytest.mark.parametrize('changes', [
    {'width': float('nan')}, {'contrast': float('inf')}, {'res': 0}, {'spacing': -1},
    {'sim_res': 0}, {'gamma': 0}, {'levels': 2.5}, {'levels': 1}, {'overscan': -1},
    {'tool_kind': 'unknown'}, {'rough_kind': 'unknown'}, {'tool_tip': 6}, {'tool_angle': 180},
    {'mode': 'relief', 'strategy': 'raster'}, {'mode': 'vcarve', 'tool_kind': 'ball'},
    {'mode': 'halftone', 'strategy': '-', 'tool_kind': 'flat'},
    {'mode': 'lithophane', 'strategy': 'x', 'tone': 'linear'},
    {'rough': True, 'stepdown': 0}, {'rough': True, 'allowance': -1},
    {'decimals': 2, 'tolerance': 0.001},
])
def test_invalid_job_is_rejected_before_arrays(model, changes):
    with pytest.raises(model.PhotoCarveError):
        model.validate(model.JobParams(**changes))


def test_vbit_limit_and_lithophane_depth(model):
    params = model.JobParams(spacing=1, tool_dia=6, tool_tip=0.2, tool_angle=60)
    checked, warnings = model.validate(params)
    limit = 0.8 / (2 * math.tan(math.pi / 6))
    assert checked.max_depth == pytest.approx(limit)
    assert warnings
    with pytest.raises(model.PhotoCarveError, match='minimum|Minimum'):
        model.validate(replace(params, min_depth=limit + 0.01))
    litho = model.JobParams(mode='lithophane', strategy='x', tone='lithophane', tool_kind='flat')
    gray = np.array([[0, 1]], dtype=np.float32)
    depth = model.depth_grid(gray, replace(litho, frame=0))
    assert depth[0].tolist() == pytest.approx([0, 2.2])


@pytest.mark.parametrize('changes', [
    {'res': 1e-9}, {'mode': 'laser', 'strategy': 'gray', 'spacing': 1e-12},
    {'tool_dia': 1e8}, {'spacing': 1e-12}, {'strategy': 'spiral', 'spacing': 1e-12},
    {'mode': 'halftone', 'strategy': '-', 'spacing': 1e-12},
    {'rough': True, 'stepdown': 1e-12},
    {'mode': 'lithophane', 'tone': 'lithophane', 'strategy': 'x', 'rough': True,
     'stepdown': 0.00001, 'litho_max': 100},
    {'mode': 'relief', 'tone': 'linear', 'strategy': 'xy', 'rough': True, 'max_depth': 1,
     'stepdown': 1, 'width': 4001, 'image_px': (4001, 5000), 'res': 1, 'spacing': 2,
     'sim_res': 1, 'tool_kind': 'flat', 'tool_dia': 0.1, 'rough_dia': 0.1},
])
def test_resource_preflight_is_scalar_fast_and_small(model, changes):
    tracemalloc.start()
    start = time.perf_counter()
    try:
        with pytest.raises(model.PhotoCarveError):
            model.validate(model.JobParams(**changes))
        assert time.perf_counter() - start < 1
        assert tracemalloc.get_traced_memory()[1] < 10_000_000
    finally:
        tracemalloc.stop()


def test_kernel_count_matches_rectangle_distance_and_sweeps(model):
    frame = model.ImageFrame(0, 0, 3, 2, 0.3, 0.4)
    radius, error = 0.75, 0.02
    for axis in ('point', 'x', 'y'):
        hx = frame.pitch_x / 2 + (frame.pitch_x if axis == 'x' else 0)
        hy = frame.pitch_y / 2 + (frame.pitch_y if axis == 'y' else 0)
        expected = sum(math.hypot(max(abs(c * frame.pitch_x) - hx, 0),
                                  max(abs(r * frame.pitch_y) - hy, 0)) < radius + error
                       for r in range(-8, 9) for c in range(-8, 9))
        assert model.kernel_count(radius, frame.pitch_x, frame.pitch_y, error, axis) == expected


def test_inverse_calibration_budget_bounds_random_rounding(model):
    rng = np.random.default_rng(7)
    for _ in range(1000):
        sx, sy = rng.uniform(-10, 10, 2)
        lx, ly = rng.uniform(50, 300, 2)
        a = np.array([[1, sx / ly], [sy / lx, 1]])
        xy = rng.uniform(-100, 100, 2)
        printed = np.round(a @ xy, 3)
        error = np.linalg.norm(np.linalg.solve(a, printed) - xy)
        assert error <= model.xy_budget(3, (sx, sy, lx, ly)) + 1e-12
    assert model.xy_budget(4, (0, 0, 0, 0)) == pytest.approx(1e-4 * math.sqrt(2) / 2)
    for skew in ((1, 0, 100, 0), (0, 1, 0, 100), (100, 100, 100, 100)):
        with pytest.raises(model.PhotoCarveError):
            model.xy_budget(4, skew)


def test_quantization_never_cuts_below_requested_z(model):
    z = np.array([-1, -0.995, -0.99999999999999, 0, 0.0049])
    quantized = model.quantize_z(z, 2)
    assert quantized.tolist() == [-1, -0.99, -1, 0, 0.01]
    assert np.all(quantized >= z - 1e-12)


def test_image_gamma_once_alpha_white_and_exif_orientation(model, tmp_path):
    image = Image.new('L', (40, 30), 128)
    params = model.JobParams(image_px=image.size, width=4, res=0.1, gamma=2)
    gray = model.prepare_image(image, params)
    assert gray.dtype == np.float32
    np.testing.assert_allclose(gray, (128 / 255) ** 2, atol=1e-7)
    transparent = Image.new('RGBA', image.size, (0, 0, 0, 0))
    np.testing.assert_array_equal(model.prepare_image(transparent, params), 1)
    pattern = Image.fromarray(np.arange(1200, dtype=np.uint16).reshape(30, 40).astype(np.uint8))
    oriented = pattern.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[274] = 6
    filename = tmp_path / 'oriented.png'
    oriented.save(filename, exif=exif)
    loaded = model.load_image(filename)
    assert loaded.size == image.size
    np.testing.assert_array_equal(model.prepare_image(loaded, params), model.prepare_image(pattern, params))


def test_tone_mapping_and_lithophane_border(model):
    gray = np.array([[0, 0.5, 1]], dtype=np.float32)
    linear = model.depth_grid(gray, model.JobParams(tone='linear', max_depth=1))
    np.testing.assert_allclose(linear, [[1, 0.5, 0]])
    perceptual = model.depth_grid(gray, model.JobParams(tone='perceptual', max_depth=1))
    assert 0.45 < perceptual[0, 1] < 0.48
    params = model.JobParams(image_px=(5, 5), width=5, res=1, mode='lithophane',
                            strategy='x', tone='lithophane', frame=1)
    depth = model.depth_grid(np.ones((5, 5), dtype=np.float32), params)
    assert depth[2, 2] == pytest.approx(2.2)
    assert not depth[0].any() and not depth[:, -1].any()


def test_laser_levels_and_dither(model):
    laser = _pure('PhotoCarveLaser')
    gray = np.tile(np.linspace(0, 1, 8, dtype=np.float32), (8, 1))
    params = model.JobParams(mode='laser', strategy='gray', levels=3)
    np.testing.assert_array_equal(laser.power_grid(gray, params), np.round((1 - gray) * 2) / 2)
    for strategy in ('bayer', 'floyd'):
        power = laser.power_grid(np.full((8, 8), 0.5, dtype=np.float32), replace(params, strategy=strategy))
        assert set(np.unique(power)) == {0, 1}
        assert 0.45 < power.mean() < 0.55
    calls = []
    laser.power_grid(gray, replace(params, strategy='floyd'), lambda: calls.append(True))
    assert len(calls) >= gray.shape[0]


def _exposure(paths):
    return sum(abs(b[0] - a[0]) * b[2] for path in paths for a, b in zip(path, path[1:]))


def test_laser_cell_edges_reverse_incoming_power_and_split_before_overscan(model):
    laser = _pure('PhotoCarveLaser')
    params = model.JobParams(image_px=(10, 2), width=10, res=1, spacing=1,
                            mode='laser', strategy='gray', lift_gap=3, overscan=5)
    power = np.array([[0, 0.2, 0.2, 0, 0, 0, 0, 0.7, 1, 0]] * 2, dtype=np.float32)
    paths = laser.laser_paths(power, params)
    assert len(paths) == 4
    assert all(path.dtype == np.float64 and len(path) >= 2 for path in paths)
    assert paths[0][:, 0].tolist() == [-4, 1, 3, 8]
    assert paths[1][:, 0].tolist() == [2, 7, 8, 9, 14]
    assert paths[2][0, 0] == 14 and paths[2][-1, 0] == 2
    assert paths[0][0, 1] == 1.5 and paths[2][0, 1] == 0.5
    assert _exposure(paths[:2]) == pytest.approx(2.1)
    assert _exposure(paths[2:]) == pytest.approx(2.1)
    for path in paths:
        assert path[0, 2] == 0 and path[1, 2] == 0 and path[-1, 2] == 0
        reversed_path = laser.reverse_laser_path(path)
        assert _exposure([reversed_path]) == pytest.approx(_exposure([path]))
        np.testing.assert_array_equal(laser.reverse_laser_path(reversed_path), path)


def test_laser_real_points_within_preflight_bound(model):
    laser = _pure('PhotoCarveLaser')
    rng = np.random.default_rng(13)
    for cols in range(1, 30):
        params = model.JobParams(image_px=(cols, 7), width=cols, res=1, spacing=1,
                                mode='laser', strategy='gray', lift_gap=0, overscan=5)
        frame = model.mode_frame(params)
        power = rng.integers(0, 4, (7, cols)).astype(np.float32) / 3
        paths = laser.laser_paths(power, params)
        assert sum(map(len, paths)) <= model.work_preflight(params, frame)['points']
        assert _exposure(paths) == pytest.approx(float(power.sum()), abs=1e-5)


def test_laser_power_formatter_zero_and_integer_mapping(model):
    laser = _pure('PhotoCarveLaser')
    assert laser.format_power(0, 100, 1000) == 0
    assert laser.format_power(1 / 3, 100, 1000) == 400
    assert laser.format_power(1, 100, 1000) == 1000
    with pytest.raises(model.PhotoCarveError):
        laser.format_power(0.5, 1000, 1000)


def test_raster_clips_exact_frame_and_spiral_obeys_work_bounds(model):
    params = model.JobParams(image_px=(40, 30), width=4, res=0.1, spacing=0.4, angle=37)
    frame = model.mode_frame(params)
    paths = model.raster_paths(params)
    assert len(paths) > 1
    for path in paths:
        assert np.all(path[:, 0] >= frame.x0 - 1e-12)
        assert np.all(path[:, 0] <= frame.x0 + frame.width + 1e-12)
        assert np.all(path[:, 1] >= frame.y0 - 1e-12)
        assert np.all(path[:, 1] <= frame.y0 + frame.height + 1e-12)
        assert np.all(np.linalg.norm(np.diff(path, axis=0), axis=1) <= 0.1 + 1e-12)
    for spacing in (0.1, 2, 100):
        spiral = replace(params, strategy='spiral', spacing=spacing)
        paths = model.spiral_paths(spiral)
        assert paths
        assert sum(map(len, paths)) <= model.work_preflight(spiral, frame)['points']
        for path in paths:
            assert np.all(path >= [0, 0]) and np.all(path <= [4, 3])


def test_one_sided_simplification_keeps_shallow_middle(model):
    path = np.array([[0, 0, -1], [1, 0, -0.995], [2, 0, -1]], dtype=np.float64)
    np.testing.assert_array_equal(model.safe_rdp(path, 0.01), path)
    path[1, 2] = -1.005
    assert len(model.safe_rdp(path, 0.01)) == 2


def test_relief_single_center_is_a_plunge_in_both_directions(model):
    for axis in ('x', 'y'):
        params = model.JobParams(image_px=(1, 1), width=1, res=1, spacing=1, mode='relief',
                                tone='linear', strategy=axis, tool_kind='flat', tool_dia=0.2,
                                max_depth=0.5, sim_res=0.1)
        depth = np.full((1, 1), 0.5, dtype=np.float32)
        paths, certificate = model.relief_paths(depth, params, params.tool())
        assert len(paths) == 1
        np.testing.assert_array_equal(paths[0], [[0.5, 0.5, -0.5]])
        assert certificate[0]['kind'] == 'point'
        assert model.certify_relief(paths, certificate, model.mode_frame(params)) >= -1e-9


def test_relief_certificate_exact_thirds_reversed_and_gouge(model):
    params = model.JobParams(image_px=(3, 3), width=1, res=1 / 3, spacing=1 / 3,
                            mode='relief', tone='linear', strategy='xy', tool_kind='ball',
                            tool_dia=0.1, sim_res=0.1, max_depth=1)
    depth = np.array([[0.1, 0.7, 0.1], [0.2, 0.9, 0.2], [0.3, 0.4, 0.3]], dtype=np.float32)
    paths, certificate = model.relief_paths(depth, params, params.tool())
    frame = model.mode_frame(params)
    paths = [np.column_stack((path[:, :2], model.quantize_z(path[:, 2], 4))) for path in paths]
    assert model.certify_relief(paths, certificate, frame) >= -1e-9
    assert model.certify_relief([path[::-1] for path in paths], certificate, frame) >= -1e-9
    bad = [path.copy() for path in paths]
    bad[-1][-1, 2] -= 0.1
    with pytest.raises(model.PhotoCarveError, match='gouge|Gouge'):
        model.certify_relief(bad, certificate, frame)


def test_relief_cutter_clearance_against_independent_dense_columns(model):
    rng = np.random.default_rng(11)
    params = model.JobParams(image_px=(7, 7), width=3.5, res=0.5, spacing=0.5, sim_res=0.5,
                            mode='relief', strategy='xy', tone='linear', max_depth=1)
    depth = rng.random((7, 7), dtype=np.float32)
    frame = model.mode_frame(params)
    for tool in (model.ToolShape('flat', 0.6), model.ToolShape('ball', 0.8),
                 model.ToolShape('vbit', 0.8, 60, 0.1)):
        paths, certificate = model.relief_paths(depth, params, tool)
        assert model.certify_relief(paths, certificate, frame) >= -1e-9
        for path in paths:
            for a, b in zip(path, path[1:]):
                for t in np.linspace(0, 1, 9):
                    x, y, z = a + t * (b - a)
                    for row in range(-1, 8):
                        for col in range(-1, 8):
                            cx, cy = frame.center(row, col)
                            rho = math.hypot(max(abs(cx - x) - 0.25, 0), max(abs(cy - y) - 0.25, 0))
                            if rho >= tool.diameter / 2:
                                continue
                            lift = (0 if tool.kind == 'flat' else
                                    tool.diameter / 2 - math.sqrt((tool.diameter / 2) ** 2 - rho ** 2)
                                    if tool.kind == 'ball' else
                                    max(0, rho - tool.tip_diameter / 2) / math.tan(math.radians(tool.angle_deg) / 2))
                            target = -float(depth[row, col]) if 0 <= row < 7 and 0 <= col < 7 else 0
                            assert z + lift >= target - 1e-9


def test_run_job_modes_and_roughing_order(model):
    gen = _pure('PhotoCarveGen')
    image = Image.new('L', (12, 12), 0)
    base = model.JobParams(image_px=image.size, width=3, res=0.25, spacing=0.5,
                           tool_dia=0.5, sim_res=0.25, max_depth=0.5)
    for mode, strategy, tone in (('vcarve', 'raster', 'vbit_area'), ('vcarve', 'spiral', 'linear'),
                                 ('halftone', '-', 'ignored'), ('relief', 'xy', 'linear'),
                                 ('lithophane', 'x', 'lithophane'), ('laser', 'bayer', 'ignored')):
        params = replace(base, mode=mode, strategy=strategy, tone=tone, frame=0)
        result = gen.run_job(image, params)
        assert result.passes and result.passes[-1].paths
        assert result.stats['points'] == sum(len(path) for part in result.passes for path in part.paths)
        frame = model.mode_frame(params)
        assert result.stats['cells'] == frame.rows * frame.cols
        assert result.stats['max_gouge'] >= -1e-9
        if mode != 'laser':
            for path in result.passes[-1].paths:
                assert path.shape[1] == 3 and path.dtype == np.float64
                assert np.min(path[:, 2]) >= -(2.2 if mode == 'lithophane' else 0.5) - 1e-9
        else:
            assert result.passes[-1].z_is_power
    rough = replace(base, mode='relief', tone='linear', strategy='x', rough=True,
                    rough_dia=0.25, stepdown=0.2, allowance=0.1)
    result = gen.run_job(image, rough)
    assert [part.role for part in result.passes] == ['rough', 'finish']
    assert min(path[:, 2].min() for path in result.passes[0].paths) >= -0.4 - 1e-9


def test_generation_abort_is_propagated(model):
    gen = _pure('PhotoCarveGen')

    def abort():
        raise RuntimeError('cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        gen.run_job(Image.new('L', (2, 2)), model.JobParams(image_px=(2, 2)), abort)


def test_halftone_black_dot_width_and_depth_limit(model):
    params = model.JobParams(image_px=(20, 20), width=4, res=0.2, spacing=1, mode='halftone',
                            strategy='-', tool_dia=0.8, tool_tip=0.1, max_depth=0.3)
    paths = model.halftone_paths(np.zeros((20, 20), dtype=np.float32), params)
    assert paths and all(len(path) == 1 for path in paths)
    for path in paths:
        depth = -path[0, 2]
        width = params.tool_tip + 2 * depth * math.tan(math.radians(params.tool_angle) / 2)
        assert width <= min(params.spacing, params.tool_dia) + 1e-9
        assert 0.01 < depth <= params.max_depth


def test_black_groove_respects_width_limit_at_high_z_precision(model):
    gen = _pure('PhotoCarveGen')
    image = Image.new('L', (8, 8), 0)
    params = model.JobParams(image_px=image.size, width=2, res=0.25, spacing=1,
                            tool_dia=1, tool_angle=45, max_depth=2, decimals=12, sim_res=0.25)
    result = gen.run_job(image, params)
    limit = model.vbit_limit(params)
    assert result.passes[-1].paths
    for path in result.passes[-1].paths:
        assert np.max(-path[:, 2]) <= limit + 1e-12


def test_relief_certificate_checks_far_corner_without_subsampling(model):
    frame = model.ImageFrame(0, 0, 3000, 3000, 1, 1)
    indices = np.arange(3000)
    x, y = frame.center(2999, 0)
    certificate = [{'kind': 'x', 'axis': 'x', 'fixed': 2999,
                    'indices': indices, 'z': np.full(3000, -0.5)}]
    path = np.array([[x, y, -0.5], [2999.5, y, -0.51]])
    with pytest.raises(model.PhotoCarveError, match='gouge|Gouge'):
        model.certify_relief([path], certificate, frame)


def test_stock_chunks_match_whole_and_independent_gouge_check(model):
    sim = _pure('PhotoCarveSim')
    frame = model.ImageFrame(-1, -1, 5, 5, 0.25, 0.25)
    tool = model.ToolShape('ball', 0.8)
    path = np.column_stack((np.linspace(0, 3, 25), np.full(25, 1.5), np.full(25, -0.4)))
    whole, chunked = sim.Stock(frame), sim.Stock(frame)
    sim.stamp_path(whole, path, tool)
    for start in range(0, len(path) - 1, 6):
        sim.stamp_path(chunked, path[start:start + 7], tool)
    np.testing.assert_array_equal(whole.height, chunked.height)
    assert whole.height.dtype == np.float32 and whole.height.min() == pytest.approx(-0.4)
    assert np.all(whole.height[0] == 0)
    assert sim.reference_gouge([path], tool, np.full((20, 20), -0.4), frame) <= 1e-7
    assert sim.reference_gouge([path - [0, 0, 0.1]], tool, np.full((20, 20), -0.4), frame) > 0.09


def test_laser_burn_matches_integer_power_and_printed_endpoints(model):
    sim = _pure('PhotoCarveSim')
    frame = model.ImageFrame(0, 0, 1, 1, 0.1, 0.1)
    path = np.array([[0, 0.55, 0], [1 / 3, 0.55, 1 / 3]], dtype=np.float64)
    printer = lambda x, y: (round(x, 4), round(y, 4))
    for minimum in (0, 100):
        moves = sim.laser_moves_from_paths([path], as_s=True, minimum=minimum, maximum=1000, printer=printer)
        expected_s = round(minimum + (1000 - minimum) / 3)
        assert moves[0].x1 == 0.3333 and moves[0].s == expected_s
        burn = sim.burn_image(frame, moves, maximum=1000)
        assert burn.sum() == pytest.approx(0.3333 * expected_s / 1000, abs=1e-7)
        text = 'G90 G21\nM4 S0\nG0 X0 Y0.55\nG1 X0.3333 S%d\nM5' % expected_s
        printed = sim.interpret_gcode(text, laser=True)
        np.testing.assert_allclose(sim.burn_image(frame, printed, maximum=1000), burn, atol=1e-7)
    with pytest.raises(model.PhotoCarveError):
        sim.laser_moves_from_paths([path], as_s=True, minimum=1000, maximum=1000)


def test_motion_interpreter_modal_units_toolchange_and_laser_off(model):
    sim = _pure('PhotoCarveSim')
    text = '(header)\nG90 G17 G21 G94 G54\nG0 Z5\nX0 Y0\nM3 S12000\nG1 Z-0.5 F100\nX1 F1000\nM5\nG0 Z5'
    spans = sim.interpret_gcode(text)
    cut = [span for span in spans if not span.rapid]
    assert len(cut) == 2
    assert cut[-1].x0 == 0 and cut[-1].x1 == 1 and cut[-1].z1 == -0.5
    inch = sim.interpret_gcode('G20\nG0 Z1\nG0 X0 Y0\nG1 X1 Z0')
    assert inch[-1].x1 == pytest.approx(25.4)
    laser = sim.interpret_gcode('G0 X0 Y0\nM4 S100\nG1 X1\nM5\nG1 X2', laser=True)
    assert laser[0].laser_on and not laser[1].laser_on
    assert math.isnan(laser[0].z0)
    with pytest.raises(model.PhotoCarveError, match='position|Position'):
        sim.interpret_gcode('G0 X0 Y0 Z5\nM6\nG1 X1 Y1 Z-1')


def test_motion_interpreter_rejects_unknown_frames_commands_and_unsplit_arcs(model):
    sim = _pure('PhotoCarveSim')
    commands = ('G91', 'G92 X0', 'G28', 'G30', 'G53 G0 X1', 'G10 L2', 'G43 H1', 'G49',
                'G38.2 Z-1', 'G80', 'G81 X1', 'G18', 'G19', 'G93', 'M98 P1', 'M99',
                'G2 X1 Y1 I1 J0', 'G54', 'A1', 'G1 Xnan', 'G4 X1', 'G1 X1 #1')
    for command in commands:
        with pytest.raises(model.PhotoCarveError):
            sim.interpret_gcode('G0 X0 Y0 Z5\n' + command)
    with pytest.raises(model.PhotoCarveError):
        sim.interpret_gcode('G0 X0 Y0\nG1 X1')


def test_marked_geometry_unit_conversion_preserves_cutter_precision_and_laser_power():
    from unittest.mock import Mock, patch
    from shapely import LineString
    from appObjects.GeometryObject import GeometryObject
    from tests.helpers_objects import make_app_0b

    for laser in (False, True):
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            obj = GeometryObject('photo', make_app_0b())
        obj.units, obj.multigeo, obj.ui_disconnect = 'MM', True, Mock()
        mark = {'version': 1, 'mode': 'laser' if laser else 'relief', 'units': 'MM',
                'contract': 'laser' if laser else 'column', 'z_is_power': laser, 'xy_err': 0.0001,
                'tools': {'1': {'kind': 'vbit', 'diameter': 6.0, 'tip_diameter': 0.2,
                                'angle_deg': 60.0, 'role': 'laser' if laser else 'finish'}}}
        obj.obj_options['photocarve'] = mark
        obj.tools = {1: {'tooldia': 6.0, 'solid_geometry': [LineString([(0, 0, 0.5), (25.4, 0, 1.0)])],
                         'data': {'tools_mill_tooldia': 6.0, 'tools_mill_vtipdia': 0.2}}}
        obj.convert_units('IN')
        assert obj.tools[1]['tooldia'] == 6 / 25.4
        assert obj.tools[1]['data']['tools_mill_tooldia'] == 6 / 25.4
        assert obj.tools[1]['data']['tools_mill_vtipdia'] == 0.2 / 25.4
        converted = obj.obj_options['photocarve']
        assert converted['tools']['1']['diameter'] == 6 / 25.4
        assert converted['tools']['1']['tip_diameter'] == 0.2 / 25.4
        assert converted['xy_err'] == 0.0001 / 25.4 and converted['units'] == 'IN'
        assert mark['units'] == 'MM' and mark['tools']['1']['diameter'] == 6
        coords = np.asarray(obj.tools[1]['solid_geometry'][0].coords)
        np.testing.assert_allclose(coords[:, 0], [0, 1])
        np.testing.assert_allclose(coords[:, 2], [0.5, 1.0] if laser else np.array([0.5, 1.0]) / 25.4)


def test_build_geometry_keeps_xyz_orders_rough_first_and_copies_defaults(model):
    import json
    objects = _pure('PhotoCarveObject')
    params = model.JobParams(mode='relief', strategy='x', tone='linear')
    finish = model.Pass('finish', model.ToolShape('vbit', 6, 60, 0.2),
                        [np.array([[0, 0, -0.1], [1, 0, -0.5]])])
    rough = model.Pass('rough', model.ToolShape('flat', 3), [np.array([[0.5, 0.5, -0.2]])])
    defaults = {'tools_mill_feedrate': 100, 'nested': {'keep': [1]}, 'tools_mill_multidepth': True,
                'tools_mill_offset_value': 1, 'tools_mill_extracut': True}
    built = objects.build_geometry(model.JobResult([finish, rough], [], {}), params, defaults)
    assert list(built['tools']) == [1, 2]
    assert built['photocarve']['tools']['1']['role'] == 'rough'
    assert built['photocarve']['tools']['2']['role'] == 'finish'
    assert built['tools'][1]['solid_geometry'][0].geom_type == 'Point'
    assert np.asarray(built['tools'][2]['solid_geometry'][0].coords)[:, 2].tolist() == [-0.1, -0.5]
    assert all(not geometry.has_z for geometry in built['solid_geometry'])
    first, second = built['tools'][1]['data'], built['tools'][2]['data']
    assert first['tools_mill_tool_shape'] == 0 and second['tools_mill_tool_shape'] == 5
    assert second['tools_mill_vtipdia'] == 0.2 and second['tools_mill_vtipangle'] == 60
    assert second['tools_mill_cutz'] == -0.5
    assert not first['tools_mill_multidepth'] and first['tools_mill_offset_value'] == 0
    assert not first['tools_mill_extracut']
    first['nested']['keep'].append(2)
    assert second['nested']['keep'] == [1] and defaults['nested']['keep'] == [1]
    json.dumps(built['photocarve'], allow_nan=False)
    objects.validate_tools(built['tools'], built['photocarve'])
    for field, value in (('tooldia', 5), ('tools_mill_tool_shape', 0), ('tools_mill_tooldia', 5),
                         ('tools_mill_vtipdia', 0.3), ('tools_mill_vtipangle', 90)):
        from copy import deepcopy
        changed = deepcopy(built['tools'])
        (changed[2] if field == 'tooldia' else changed[2]['data'])[field] = value
        with pytest.raises(model.PhotoCarveError):
            objects.validate_tools(changed, built['photocarve'])
    with pytest.raises(model.PhotoCarveError):
        objects.validate_tools({**built['tools'], 3: built['tools'][1]}, built['photocarve'])
