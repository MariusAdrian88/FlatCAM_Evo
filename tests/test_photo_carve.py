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
    params = model.JobParams(image_px=(3000, 3000), width=3000, res=1, spacing=1,
                            mode='relief', strategy='x', tool_kind='flat', tool_dia=0.1)
    frame = model.mode_frame(params)
    depth = np.full((3000, 3000), 0.5, dtype=np.float32)
    paths, certificate = model.relief_paths(depth, params, params.tool())
    assert len(certificate) == 3000 and all(len(record['z']) == 3000 for record in certificate)
    assert model.certify_relief(paths, certificate, frame) >= -1e-9
    paths[-1] = paths[-1].copy()
    corner = np.argmax(paths[-1][:, 0])
    paths[-1][corner, 2] -= 0.01
    with pytest.raises(model.PhotoCarveError, match='gouge|Gouge'):
        model.certify_relief(paths, certificate, frame)


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


def _writer_job(pp='default', laser=False, **changes):
    from tests.test_cncjob_gcode_speed import gcode_job
    from defaults import AppDefaults
    model = _pure('PhotoCarveModel')
    objects = _pure('PhotoCarveObject')
    params = model.JobParams(mode='laser' if laser else 'relief')
    paths = [np.array([[0.0049, 0, 0 if laser else -0.105], [2, 0, 1 if laser else -0.205]]),
             np.array([[4, 0, 0 if laser else -0.3], [5, 0, 0.5 if laser else -0.4]])]
    part = model.Pass('finish', model.ToolShape('flat', 0.1), paths, z_is_power=laser)
    data = dict(AppDefaults.factory_defaults)
    data.update(tools_mill_ppname_g=pp, tools_mill_feedrate=1000, tools_mill_feedrate_z=100,
                tools_mill_travelz=5, tools_mill_startz=1, tools_mill_toolchangez=15,
                tools_mill_toolchangexy='10, 20', tools_mill_laser_on='M4',
                tools_mill_min_power=10, tools_mill_spindlespeed=1000)
    data.update(changes)
    built = objects.build_geometry(model.JobResult([part], [], {}), params, data)
    job = gcode_job(pp)
    job.units = params.units
    return job, built


@pytest.mark.parametrize('pp', ['default', 'GRBL_11', 'GRBL_11_no_M6', 'grblHAL', 'Marlin'])
@pytest.mark.parametrize('toolchange', [False, True])
def test_writer_milling_executed_blocks_and_safe_toolchange(pp, toolchange):
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    from copy import deepcopy
    import re
    job, built = _writer_job(pp)
    built['tools'][2] = deepcopy(built['tools'][1])
    built['photocarve']['tools']['2'] = deepcopy(built['photocarve']['tools']['1'])
    job._bed_offset_x = 10
    result = write_tool_gcode(job, 1, built['tools'], (0, 0), (0, 0), 0,
                              True, False, toolchange, True, built['photocarve'])
    assert result != 'fail'
    code, header = result
    second, second_header = write_tool_gcode(job, 2, built['tools'], (5, 0), (0, 0), 0,
                                             False, True, toolchange, True, built['photocarve'])
    assert second_header == ''
    code += second
    assert header.endswith('\n') and code.endswith('\n')
    assert 'X10.0049' in code
    z, feed = 0, 0
    cuts = []
    for line in code.splitlines():
        if not line.strip() or line.lstrip().startswith(('(', ';')):
            continue
        words = dict(re.findall(r'([XYZF])\s*(-?\d+(?:\.\d+)?)', line))
        if 'F' in words:
            feed = float(words['F'])
        if 'X' in words or 'Y' in words:
            if re.match(r'G0?0\b', line):
                assert z >= 5, line
            else:
                assert feed == 1000, line
                cuts.append(float(words['Z']))
        elif 'Z' in words and float(words['Z']) < 0:
            assert feed == 100
        if 'Z' in words:
            z = float(words['Z'])
    assert cuts == [-0.205, -0.4] * 2
    assert not re.search(r'Z\S+G', code)


@pytest.mark.parametrize('pp', ['GRBL_laser', 'grblHAL_laser'])
def test_writer_laser_only_powers_cutting_motion(pp):
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    import re
    job, built = _writer_job(pp, laser=True)
    job._bed_offset_x = 10
    code, header = write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                                    True, True, False, True, built['photocarve'])
    assert 'M5\nM4 S0\n' in code
    assert code.endswith('S0\nM5\n')
    assert ' S1000\n' in code and ' S505\n' in code
    assert 'X10.0049' in code
    assert all(line.startswith(('G1 ', 'G01 ')) for line in code.splitlines()
               if re.search(r'\bS[1-9]\d*', line))
    assert ' Z' not in code
    assert all(code.splitlines()[i - 1] == 'S0' for i, line in enumerate(code.splitlines())
               if line.startswith(('G0 ', 'G00 ')))
    model, laser, sim = _pure('PhotoCarveModel'), _pure('PhotoCarveLaser'), _pure('PhotoCarveSim')
    params = model.JobParams(image_px=(10, 2), width=10, res=1, spacing=1,
                            mode='laser', strategy='gray', lift_gap=3, overscan=5)
    power = np.array([[0, 0.2, 0.2, 0, 0, 0, 0, 0.7, 1, 0]] * 2, dtype=np.float32)
    from shapely import LineString
    job._bed_offset_x = 0
    built['tools'][1]['solid_geometry'] = [LineString(path) for path in laser.laser_paths(power, params)]
    code, header = write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                                    True, True, False, True, built['photocarve'])
    spans = sim.interpret_gcode(header + code, laser=True)
    burn = sim.burn_image(model.mode_frame(params), spans, maximum=1000)
    expected = np.where(power > 0, (10 + power * 990) / 1000, 0)
    np.testing.assert_allclose(burn, expected, atol=1e-7)


def test_writer_rejects_unsafe_toolchange_and_precision_and_rounds_once():
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    job, built = _writer_job(tools_mill_toolchangez=2)
    args = (job, 1, built['tools'], (0, 0), None, 0, True, True, True, True, built['photocarve'])
    assert write_tool_gcode(*args) == 'fail'
    built['tools'][1]['data']['tools_mill_toolchangez'] = 15
    job.coords_decimals = 2
    assert write_tool_gcode(*args) == 'fail'
    built['photocarve']['xy_err'] = 0.01
    job._bed_offset_x = 0.0049
    code, _ = write_tool_gcode(*args)
    assert 'X0.01 Y0.00' in code and 'Z-0.20' in code


def test_certified_geometry_lifecycle():
    from unittest.mock import Mock, patch
    from appObjects.GeometryObject import GeometryObject
    from tests.helpers_objects import make_app_0b
    from shapely import force_2d
    job, built = _writer_job()
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        obj = GeometryObject('photo', make_app_0b())
        copied = GeometryObject('copy', obj.app)
    obj.tools, obj.solid_geometry, obj.multigeo = built['tools'], built['solid_geometry'], True
    obj.obj_options['photocarve'] = built['photocarve']
    copied.copy_from(obj)
    assert copied.obj_options['photocarve'] == obj.obj_options['photocarve']
    assert copied.obj_options['photocarve'] is not obj.obj_options['photocarve']
    before = obj.tools[1]['solid_geometry'][0].wkt
    assert obj.scale(2) == 'fail'
    assert obj.skew(10, 0, (0, 0)) == 'fail'
    assert obj.tools[1]['solid_geometry'][0].wkt == before
    assert obj.mtool_gen_cncjob() == 'fail'
    assert obj.generatecncjob() == 'fail'
    assert GeometryObject.merge([obj], copied) == 'fail'
    obj.offset((2, 3))
    obj.rotate(90, (2, 3))
    coords = list(obj.tools[1]['solid_geometry'][0].coords)
    assert coords[0] == pytest.approx((2, 3.0049, -0.105))
    collected = obj._collect_elements(obj.tools[1]['solid_geometry'])
    assert not collected[0]['shape'].has_z
    assert force_2d(obj.tools[1]['solid_geometry'][0]).equals(collected[0]['shape'])


def test_photo_adapter_overrides_parse_each_tool_and_keeps_certificate():
    from tests.test_milling_lifecycle import MillingHarness
    from appParsers.ParseGCode import parse_gcode
    from types import SimpleNamespace
    from copy import deepcopy
    from unittest.mock import Mock
    job, built = _writer_job()
    built['tools'][2] = deepcopy(built['tools'][1])
    from shapely import Point
    built['tools'][2]['solid_geometry'] = [Point(7, 8, -0.12345)]
    built['photocarve']['tools']['2'] = dict(built['photocarve']['tools']['1'], role='rough')
    context = dict(geo_obj=SimpleNamespace(obj_options={'photocarve': built['photocarve']}),
                   tools_dict=built['tools'], bounds={}, seg_x=0, seg_y=0,
                   geo_options={'tools_mill_z_p_depth': -1, 'tools_mill_feedrate_probe': 50},
                   options={'cncjob_coords_decimals': 4, 'cncjob_fr_decimals': 2, 'global_tolerance': 0.01},
                   units='MM', overrides={'tools_mill_feedrate': 1234}, toolchange=False, from_tcl=True)
    context.update(source_obj=context['geo_obj'], outname='photo_cnc', multigeo=True)
    job.tools = {}
    job.gcode_parse = Mock(side_effect=lambda **kw: [{'geom': job.gcode}])
    milling = MillingHarness(app=job.app)
    milling.new_object = lambda kind, name, initializer, **kw: (
        'fail' if initializer(job, job.app) == 'fail' else job)
    assert milling._submit_cnc_job_creation(context, plot=False, use_thread=False) is None
    assert job.used_tools == [2, 1]
    assert job.tools[2]['gcode_parsed'][0]['geom'] == job.tools[2]['gcode']
    assert 'X7.0000 Y8.0000' in job.tools[2]['gcode'] and 'Z-0.1234' in job.tools[2]['gcode']
    assert 'X7.0000 Y8.0000' not in job.tools[1]['gcode']
    assert 'F1234' in job.source_file and job.source_file.startswith(job.gc_start)
    assert job.obj_options['photocarve'] == built['photocarve']
    assert job.obj_options['photocarve'] is not built['photocarve']
    parsed = parse_gcode(job.source_file, feedrate_rapid=job.feedrate_rapid, target_units='MM')
    assert job.travel_distance == parsed['travel_distance'] and job.routing_time == parsed['routing_time']
    context['overrides']['tools_mill_tooldia'] = 99
    assert milling._submit_cnc_job_creation(context, plot=False, use_thread=False) == 'fail'
    context['overrides'].clear()
    context['tools_dict'][3] = deepcopy(context['tools_dict'][1])
    assert milling._submit_cnc_job_creation(context, plot=False, use_thread=False) == 'fail'


def test_writer_fails_closed_for_missing_post_and_laser_settings():
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    job, built = _writer_job('GRBL_laser', laser=True)

    def write():
        return write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                                 True, True, False, True, built['photocarve'])

    data = built['tools'][1]['data']
    data['tools_mill_laser_on'] = 'M3'
    assert write() == 'fail'
    data['tools_mill_laser_on'] = 'M4'
    data['tools_mill_min_power'] = 1000
    assert write() == 'fail'
    data['tools_mill_min_power'] = 0
    del job.app.preprocessors['GRBL_laser']
    assert write() == 'fail'


def test_writer_cancellation_stops_a_long_path():
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    from shapely import LineString
    from camlib import grace
    from unittest.mock import patch
    job, built = _writer_job()
    built['tools'][1]['solid_geometry'] = [LineString([(x, 0, -0.1) for x in range(1005)])]
    post = job.app.preprocessors['default']
    linear = post.linear_code

    def cancel(point):
        job.app.abort_flag = True
        return linear(point)

    with patch.object(post, 'linear_code', side_effect=cancel):
        with pytest.raises(grace):
            write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                              True, True, False, True, built['photocarve'])


def test_photo_milling_ui_locks_and_restores_certified_fields():
    from PyQt6 import QtCore, QtGui, QtWidgets
    from appPlugins.ToolMilling import ToolMilling
    from appPlugins.ToolMilling.MillingUI import MillingUI
    from tests.test_milling_lifecycle import MillingHarness
    from tests.helpers_objects import make_app_0b
    from types import SimpleNamespace
    from copy import deepcopy
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = make_app_0b()
    app.collection = QtGui.QStandardItemModel()
    app.resource_location = 'assets/resources'
    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    ui = MillingUI(layout, app, 'Milling')
    _, built = _writer_job()
    milling = MillingHarness(app=app, ui=ui, layout=layout, target_obj=SimpleNamespace(
        obj_options={'photocarve': built['photocarve']}, tools=built['tools']))
    ui.tools_table_mill_geo.setRowCount(1)
    item = QtWidgets.QTableWidgetItem('0.1')
    ui.tools_table_mill_geo.setItem(0, 1, item)
    before = deepcopy(built['tools'])
    milling.set_photocarve_ui()
    for name in ('offset_type_combo', 'mpass_cb', 'maxdepth_entry', 'extracut_cb', 'cutz_entry',
                 'exclusion_cb', 'tool_shape_combo', 'tipdia_entry', 'tipangle_entry',
                 'search_and_add_btn', 'deltool_btn', 'addtool_from_db_btn'):
        assert not getattr(ui, name).isEnabled(), name
    assert not item.flags() & QtCore.Qt.ItemFlag.ItemIsEditable
    for method, args in (('on_tool_add', ()), ('on_tool_default_add', ()),
                         ('on_tool_copy', ()), ('on_tool_delete', ()),
                         ('on_tool_edit', (item,)), ('on_tool_from_db_inserted', ({},))):
        assert getattr(milling, method)(*args) == 'fail'
    assert built['tools'] == before
    milling.target_obj.obj_options = {}
    milling.set_photocarve_ui()
    assert ui.tool_shape_combo.isEnabled()
    assert item.flags() & QtCore.Qt.ItemFlag.ItemIsEditable


def test_photo_edit_and_join_are_refused_before_side_effects():
    from appMain import App
    from appHandlers.appEdit import appEditor
    from types import SimpleNamespace
    from unittest.mock import Mock
    obj = SimpleNamespace(kind='geometry', multigeo=True, obj_options={'photocarve': {'version': 1}})
    harness = SimpleNamespace(defaults=Mock(), call_source='app', inform=Mock(),
                              collection=SimpleNamespace(get_active=lambda: obj,
                                                         get_selected=lambda: [obj, obj]))
    assert App.on_editing_start(harness) == 'fail'
    assert appEditor.on_edit_join(harness) == 'fail'
    assert appEditor.on_join_as(harness, 'geometry') == 'fail'


def test_photo_cutter_real_form_signals_do_not_change_certificate(monkeypatch):
    import sys
    import traceback
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda typ, value, tb: errors.append(''.join(traceback.format_exception(typ, value, tb))))
    from tests.test_milling_target_settings import plugin, select, make_object
    from PyQt6 import QtWidgets
    from copy import deepcopy
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _, built = _writer_job()
    obj = make_object('photo', tool=built['tools'][1])
    obj.obj_options['photocarve'] = built['photocarve']
    ordinary = make_object('ordinary')
    ordinary_before = deepcopy(ordinary.tools)
    milling = plugin({'photo': obj, 'ordinary': ordinary})
    from unittest.mock import MagicMock
    milling.app.inform = MagicMock()
    select(milling, 'photo')
    before = deepcopy(obj.tools)
    milling.ui.tool_shape_combo.set_value(5)
    milling.ui.tipdia_entry.set_value(0.3)
    milling.ui.tipangle_entry.set_value(90)
    milling.ui.mpass_cb.setChecked(True)
    assert obj.tools == before
    milling.ui.xyfeedrate_entry.set_value(321)
    milling.ui.xyfeedrate_entry.returnPressed.emit()
    assert obj.tools[1]['data']['tools_mill_feedrate'] == 321
    select(milling, 'ordinary')
    assert ordinary.tools == ordinary_before
    assert milling.ui.tool_shape_combo.isEnabled()
    if errors:
        pytest.fail('\n'.join(errors))


def test_photo_save_load_keeps_xyz_and_certificate(tmp_path):
    from appCommon.ProjectFile import save_project_file, load_project_file
    from appObjects.GeometryObject import GeometryObject
    from tests.helpers_objects import make_app_0b
    from unittest.mock import Mock, patch
    from camlib import geometry_wkt_cache, project_json_default
    _, built = _writer_job()
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        obj = GeometryObject('photo', make_app_0b())
        restored_obj = GeometryObject('restored', obj.app)
    obj.tools, obj.solid_geometry, obj.multigeo = built['tools'], built['solid_geometry'], True
    obj.obj_options['photocarve'] = built['photocarve']
    data = {'objs': [obj.to_dict()]}
    path = str(tmp_path / 'photo.FlatPrj')
    save_project_file(path, data, default=project_json_default(geometry_wkt_cache(data)))
    restored = load_project_file(path)['objs'][0]
    restored_obj.from_dict(restored)
    assert restored_obj.obj_options['photocarve'] == built['photocarve']
    assert restored_obj.multigeo
    assert list(restored_obj.tools['1']['solid_geometry'][0].coords) == list(built['tools'][1]['solid_geometry'][0].coords)


def test_writer_physical_skew_error_and_inch_precision():
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    from shapely import LineString
    import re
    job, built = _writer_job()
    job._bed_skew_x, job._bed_skew_y, job._bed_limit_x, job._bed_limit_y = 2, -3, 100, 150
    skew = (2, -3, 100, 150)
    model = _pure('PhotoCarveModel')
    built['photocarve']['xy_err'] = model.xy_budget(4, skew)
    points = np.column_stack([np.random.default_rng(71).uniform(-20, 20, (1000, 2)), np.full(1000, -0.12345)])
    built['tools'][1]['solid_geometry'] = [LineString(points)]
    code, header = write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                                    True, True, False, True, built['photocarve'])
    printed = np.array([[float(x), float(y)] for x, y in re.findall(r'G0?1 X(-?[\d.]+) Y(-?[\d.]+)', code)])
    transform = np.array([[1, 2 / 150], [-3 / 100, 1]])
    model_space = printed @ np.linalg.inv(transform).T
    assert np.linalg.norm(model_space - points[1:, :2], axis=1).max() <= built['photocarve']['xy_err'] + 1e-12
    built['photocarve']['xy_err'] /= 25.4
    job.units = built['photocarve']['units'] = 'IN'
    assert write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                            True, True, False, True, built['photocarve']) == 'fail'
    assert '6 coordinate decimals' in job.app.inform.emit.call_args.args[0]


@pytest.fixture
def photo_controller():
    from PyQt6 import QtWidgets
    from types import SimpleNamespace
    from unittest.mock import Mock, MagicMock
    from defaults import AppDefaults
    from appPlugins.ToolPhotoCarve.PhotoCarve import ToolPhotoCarve
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = SimpleNamespace(decimals=4, units='MM', options=dict(AppDefaults.factory_defaults),
                          defaults=Mock(), log=Mock(), inform=Mock(), worker_task=Mock(),
                          cleanup=Mock(), proc_container=MagicMock(), abort_flag=False,
                          collection=Mock(), ui=Mock(), milling_tool=Mock())
    tool = ToolPhotoCarve(app)
    tool.set_tool_ui()
    yield tool, app, qapp
    tool.on_plugin_cleanup()
    tool.deleteLater()
    qapp.processEvents()


def test_photo_rebuild_bindings_and_preferences_roundtrip(photo_controller):
    from appPlugins.ToolPhotoCarve.PhotoCarveModel import DEFAULTS, PHOTOCARVE_KEYS
    from appGUI.preferences.tools.ToolsPhotoCarvePrefGroupUI import ToolsPhotoCarvePrefGroupUI
    from defaults import AppDefaults
    tool, app, _ = photo_controller
    prefs = ToolsPhotoCarvePrefGroupUI(app)
    for _ in range(2):
        tool.set_tool_ui()
        assert set(tool.form_fields) == set(PHOTOCARVE_KEYS)
        tool.ui.strategy_entry.set_value('spiral')
        tool.form_to_storage()
        assert app.options['tools_photocarve_strategy'] == 'spiral'
        for key, name in zip(PHOTOCARVE_KEYS, DEFAULTS):
            widget = getattr(prefs, name + '_entry')
            widget.set_value(app.options[key])
            assert widget.get_value() == app.options[key]
            assert AppDefaults.factory_defaults[key] == DEFAULTS[name]
    tool.ui.width_entry.set_value(23)
    assert app.options['tools_photocarve_width'] == 23


def test_photo_preview_drops_stale_and_emits_errors(photo_controller, monkeypatch):
    from appPlugins.ToolPhotoCarve import PhotoCarveGen as gen
    tool, app, _ = photo_controller
    tool.image = Image.new('RGB', (3, 2), 'black')
    tool.on_preview()
    old = app.worker_task.emit.call_args.args[0]
    tool.on_preview()
    new = app.worker_task.emit.call_args.args[0]
    new['fcn'](*new['params'])
    shown = tool.ui.preview_label.pixmap().cacheKey()
    old['fcn'](*old['params'])
    assert tool.ui.preview_label.pixmap().cacheKey() == shown
    monkeypatch.setattr(gen, 'preview_map', lambda *a: (_ for _ in ()).throw(ValueError('preview error')))
    tool.on_preview()
    task = app.worker_task.emit.call_args.args[0]
    task['fcn'](*task['params'])
    assert 'preview error' in app.inform.emit.call_args.args[0]


@pytest.mark.parametrize('kind', ['geometry', 'gerber'])
def test_photo_publication_guard_and_ordinary_creation(kind):
    from appObjects.AppObject import AppObject
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    app = SimpleNamespace(log=Mock(), inform=Mock(), collection=Mock(), ui=Mock(), worker_task=Mock())
    app.ui.notebook.currentWidget.return_value = app.ui.plugin_tab
    obj = SimpleNamespace(kind=kind, obj_options={'name': 'result'}, status_color=None,
                          has_plot_colors=False, _record_history=True)
    holder = SimpleNamespace(app=app)
    with patch('appObjects.AppObject.history_of') as history:
        obj._publish_guard = lambda: False
        AppObject.on_object_created(holder, obj, False, False, None, [])
        app.collection.append.assert_not_called()
        history.return_value.record_create.assert_not_called()
        callback = Mock()
        AppObject.on_object_created(holder, obj, False, False, callback, ['arg'])
        app.collection.append.assert_called_once_with(obj)
        history.return_value.record_create.assert_called_once()
        assert app.worker_task.emit.call_args.args[0] == {'fcn': callback, 'params': ['arg']}


@pytest.mark.parametrize('invalidate', ['reset', 'units', 'newer', 'after_init', 'units_after_init'])
def test_photo_generation_captured_publication(photo_controller, invalidate):
    from appObjects.AppObject import AppObject
    from types import SimpleNamespace
    tool, app, _ = photo_controller
    tool.image = Image.new('RGB', (2, 2), 'black')
    tool.image_filename = 'photo.png'
    tool.ui.width_entry.set_value(2)
    tool.ui.res_entry.set_value(1)
    pending = []

    def create(kind, name, init, **kwargs):
        obj = SimpleNamespace(kind=kind, obj_options={'name': name}, status_color=None, has_plot_colors=False)
        if init(obj, app) != 'fail':
            pending.append(obj)
        return obj

    app.app_obj = SimpleNamespace(new_object=create)
    app.collection.get_by_name.return_value = object()
    app.ui.notebook.currentWidget.return_value = app.ui.plugin_tab
    tool.on_generate()
    first = app.worker_task.emit.call_args.args[0]
    if invalidate == 'reset':
        tool.on_project_reset()
    elif invalidate == 'units':
        app.units = 'IN'
    elif invalidate == 'newer':
        tool.on_generate()
        latest = app.worker_task.emit.call_args.args[0]
        latest['fcn'](*latest['params'])
    first['fcn'](*first['params'])
    if invalidate == 'after_init':
        assert len(pending) == 1
        tool.on_project_reset()
    elif invalidate == 'units_after_init':
        assert len(pending) == 1
        app.units = 'IN'
    for obj in pending:
        obj.obj_options['name'] = 'photo_engrave_2'
        AppObject.on_object_created(SimpleNamespace(app=app), obj, False, False, None, [])
    if invalidate == 'newer':
        assert tool.result_obj_name == 'photo_engrave_2'
        tool.on_open_milling()
        app.collection.set_active.assert_called_with('photo_engrave_2')
        app.milling_tool.run.assert_called_once()
        tool.on_project_reset()
        assert tool.result_obj_name is None
    else:
        app.collection.append.assert_not_called()
        assert tool.result_obj_name is None


def test_photo_preferences_manager_all_keys(photo_controller, tmp_path):
    from unittest.mock import MagicMock
    from defaults import AppDefaults
    from appGUI.preferences.PreferencesUIManager import PreferencesUIManager
    from appGUI.preferences.tools.PluginsEngravingPreferencesUI import PluginsEngravingPreferencesUI
    from appPlugins.ToolPhotoCarve.PhotoCarveModel import DEFAULTS, PHOTOCARVE_KEYS
    tool, app, _ = photo_controller
    ui = MagicMock()
    ui.app = app
    ui.plugin_eng_pref_form = PluginsEngravingPreferencesUI(app)
    manager = PreferencesUIManager(AppDefaults(), str(tmp_path), ui, app.inform, app.options)
    fields = {key: manager.defaults_form_fields[key] for key in PHOTOCARVE_KEYS}
    manager.defaults_form_fields = fields
    for key in fields:
        manager.defaults_write_form_field(key)
        assert fields[key].get_value() == app.options[key]
    fields['tools_photocarve_strategy'].set_value('spiral')
    manager.defaults_read_form()
    assert app.options['tools_photocarve_strategy'] == 'spiral'
    assert manager.defaults['tools_photocarve_strategy'] == 'spiral'
    assert len(fields) == len(DEFAULTS)


def test_photo_tcl_shared_generation_and_invalid_cutter(photo_controller, tmp_path, monkeypatch):
    from tclCommands.TclCommandPhotoCarve import TclCommandPhotoCarve
    from appPlugins.ToolPhotoCarve import PhotoCarveGen
    from appPlugins.ToolPhotoCarve.PhotoCarveModel import DEFAULTS
    from defaults import AppDefaults
    from types import SimpleNamespace
    tool, app, _ = photo_controller
    app.defaults = AppDefaults()
    path = tmp_path / 'photo.png'
    Image.new('RGB', (2, 2), 'black').save(path)
    captured = []
    original = PhotoCarveGen.run_job

    def run(image, params, check_abort=None):
        captured.append(params)
        return original(image, params, check_abort)

    monkeypatch.setattr(PhotoCarveGen, 'run_job', run)
    created = []

    def create(kind, name, init, **kwargs):
        assert kind == 'geometry' and kwargs['plot'] is False
        obj = SimpleNamespace(obj_options={'name': name})
        init(obj, app)
        assert not hasattr(obj, '_publish_guard') and not hasattr(obj, '_after_publish')
        created.append(obj)
        return obj

    app.app_obj = SimpleNamespace(new_object=create)
    command = TclCommandPhotoCarve(app)
    assert set(command.option_types) == set(DEFAULTS) | {'outname'}
    command.execute({'filename': str(path), 'outname': 'scripted', 'width': 2., 'res': 1.}, [])
    assert len(captured) == len(created) == 1
    assert created[0].obj_options['name'] == 'scripted'
    assert created[0].obj_options['photocarve']['mode'] == 'vcarve'
    assert created[0].tools[1]['solid_geometry']
    error = command.execute({'filename': str(path), 'tool_angle': 0.}, [])
    assert isinstance(error, str) and error
    assert len(created) == 1


@pytest.mark.parametrize('mode,strategy,kind,tone', [
    ('vcarve', 'spiral', 'vbit', 'vbit_area'), ('halftone', '-', 'vbit', 'linear'),
    ('relief', 'xy', 'ball', 'perceptual'), ('lithophane', 'y', 'flat', 'lithophane'),
    ('laser', 'floyd', 'flat', 'linear'),
])
def test_photo_modes_and_image_capture(photo_controller, tmp_path, mode, strategy, kind, tone):
    tool, app, _ = photo_controller
    path = tmp_path / 'oriented.tif'
    image = Image.new('RGB', (2, 3))
    exif = image.getexif()
    exif[274] = 6
    image.save(path, exif=exif)
    tool.on_load_image(filename=str(path))
    assert tool.image.size == (3, 2)
    assert tool.ui.file_label.text() == 'oriented.tif'
    tool.ui.mode_entry.set_value(mode)
    tool.ui.strategy_entry.set_value(strategy)
    tool.ui.tool_kind_entry.set_value(kind)
    tool.ui.tone_entry.set_value(tone)
    params = tool.capture_params()
    assert (params.mode, params.strategy, params.tool_kind, params.tone) == (mode, strategy, kind, tone)
    assert tool.ui.tool_kind_entry.isEnabled() == (mode not in ('vcarve', 'halftone'))
    assert tool.ui.sections['rough'].isHidden() == (mode == 'laser')
    assert tool.ui.tone_entry.isHidden() == (mode in ('laser', 'halftone'))


def test_photo_generations_final_name_and_reset_no_callback(photo_controller):
    from appObjects.AppObject import AppObject
    from types import SimpleNamespace
    tool, app, _ = photo_controller
    tool.image = Image.new('RGB', (2, 2), 'black')
    tool.image_filename = 'photo.png'
    tool.ui.width_entry.set_value(2)
    tool.ui.res_entry.set_value(1)
    app.ui.notebook.currentWidget.return_value = app.ui.plugin_tab
    objects = []

    def create(kind, name, init, **kwargs):
        assert 'callback' not in kwargs
        obj = SimpleNamespace(kind=kind, obj_options={'name': name}, status_color=None, has_plot_colors=False)
        if init(obj, app) != 'fail':
            AppObject.on_object_created(SimpleNamespace(app=app), obj, False, False, None, [])
            objects.append(obj)
        return obj

    def append(obj):
        obj.obj_options['name'] += '_%d' % (len(objects) + 1)

    app.app_obj = SimpleNamespace(new_object=create)
    app.collection.append.side_effect = append
    for expected in ('photo_photocarve_1', 'photo_photocarve_2'):
        tool.on_generate()
        task = app.worker_task.emit.call_args.args[0]
        task['fcn'](*task['params'])
        assert tool.result_obj_name == expected
    tool.on_open_milling()
    app.collection.set_active.assert_called_with('photo_photocarve_2')
    tool.on_project_reset()
    assert tool.result_obj_name is None


def test_photo_latest_finishes_before_old_running_job(photo_controller, monkeypatch):
    import threading
    from appPlugins.ToolPhotoCarve import PhotoCarveGen as gen
    from types import SimpleNamespace
    tool, app, _ = photo_controller
    tool.image = Image.new('RGB', (2, 2), 'black')
    tool.ui.width_entry.set_value(2)
    tool.ui.res_entry.set_value(1)
    started, finish = threading.Event(), threading.Event()
    real_run = gen.run_job
    results = []

    def run(image, params, check_abort):
        result = real_run(image, params, check_abort)
        if threading.current_thread() is not threading.main_thread():
            started.set()
            assert finish.wait(5)
        return result

    def create(kind, name, init, **kwargs):
        obj = SimpleNamespace(obj_options={'name': name})
        if init(obj, app) != 'fail':
            results.append(obj)

    monkeypatch.setattr(gen, 'run_job', run)
    app.app_obj = SimpleNamespace(new_object=create)
    tool.on_generate()
    first = app.worker_task.emit.call_args.args[0]
    worker = threading.Thread(target=lambda: first['fcn'](*first['params']))
    worker.start()
    try:
        assert started.wait(5)
        tool.on_generate()
        latest = app.worker_task.emit.call_args.args[0]
        latest['fcn'](*latest['params'])
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive()
    assert len(results) == 1


def test_photo_menu_toggle_and_shortcut(photo_controller, monkeypatch):
    from PyQt6 import QtCore, QtWidgets
    from appGUI.MainGUI import MainGUI, ShortcutsTab
    from appGUI.GUIElements import VerticalScrollArea
    from unittest.mock import Mock, patch
    from types import SimpleNamespace
    tool, app, _ = photo_controller
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda typ, value, tb: errors.append(str(value)))
    app.plugin_tab_locked = False
    app.call_source = 'app'
    app.photocarve_tool = tool
    app.ui.menu_plugins = QtWidgets.QMenu()
    app.ui.notebook = QtWidgets.QTabWidget()
    app.ui.properties_tab = QtWidgets.QWidget()
    app.ui.plugin_tab = QtWidgets.QWidget()
    app.ui.plugin_tab.setObjectName('plugin_tab')
    app.ui.notebook.addTab(QtWidgets.QWidget(), 'Project')
    app.ui.notebook.addTab(app.ui.properties_tab, 'Properties')
    app.ui.notebook.addTab(app.ui.plugin_tab, 'Plugin')
    app.ui.plugin_scroll_area = VerticalScrollArea()
    app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(app.ui.plugin_tab)
    app.ui.plugin_tab_layout.addWidget(app.ui.plugin_scroll_area)
    app.collection.get_list.return_value = []
    app.ui.splitter.sizes.return_value = [1, 1]
    tool.install()
    tool.menuAction.trigger()
    assert not errors, errors
    assert app.ui.plugin_scroll_area.widget() is tool
    assert app.ui.notebook.currentWidget() is app.ui.plugin_tab
    tool.menuAction.trigger()
    assert not errors, errors
    assert app.ui.notebook.indexOf(app.ui.plugin_tab) == -1
    with patch.object(QtWidgets.QApplication, 'keyboardModifiers',
                      return_value=QtCore.Qt.KeyboardModifier.AltModifier):
        MainGUI.keyPressEvent(SimpleNamespace(app=app), int(QtCore.Qt.Key.Key_V))
    assert app.ui.notebook.currentWidget() is app.ui.plugin_tab
    app.on_zoom_fit = Mock()
    with patch.object(QtWidgets.QApplication, 'keyboardModifiers',
                      return_value=QtCore.Qt.KeyboardModifier.NoModifier):
        MainGUI.keyPressEvent(SimpleNamespace(app=app), int(QtCore.Qt.Key.Key_V))
    app.on_zoom_fit.assert_called_once()
    shortcuts = ShortcutsTab()
    assert any('Alt+V' in w.toPlainText() for w in shortcuts.findChildren(QtWidgets.QTextEdit))
    app.ui.plugin_scroll_area.takeWidget()
    app.ui.notebook.deleteLater()


def test_sim_motion_arcs_and_closed_subset(model):
    sim = _pure('PhotoCarveSim')
    from appPlugins.ToolGcodeSender.SenderGen import split_arcs
    lines = ['G21 G90 G17', 'G0 X0 Y0 Z0', 'G2 X2 Y0 I1 J0 F100']
    split, count, _, unsplit = split_arcs(lines, 'MM', 0.1)
    assert count == 1 and not unsplit
    assert sim.gcode_motion(lines, 'MM', 0.1) == sim.interpret_gcode('\n'.join(split))
    for code in ('G2 X1 Y1 I1', 'G91', 'G92 X0', 'G28', 'G81 X1', 'G1 X1'):
        with pytest.raises(model.PhotoCarveError):
            sim.gcode_motion(['G0 X0 Y0', code], 'MM', 0.1)
    with pytest.raises(model.PhotoCarveError):
        sim.gcode_motion(['G0 X0 Y0 Z0', 'G2 X2 Y0 I1 I1 J0'], 'MM', 0.1)
    with pytest.raises(model.PhotoCarveError, match='1 arc'):
        sim.gcode_motion(['G0 X0 Y0 Z0', 'G2 X2 Y0 I0 J0'], 'MM', 0.1)
    laser = sim.gcode_motion(['G54', 'G0 X0 Y0', 'M4 S500', 'G2 X2 Y0 I1 J0'], 'MM', 0.1, laser=True)
    assert len(laser) > 10 and laser[-1].x1 == 2 and laser[-1].s == 500


def test_cnc_sim_passes_milling_and_laser(model):
    from types import SimpleNamespace
    from appPlugins.ToolPhotoCarve.PhotoCarveObject import passes_from_cncjob
    job = SimpleNamespace(units='MM', obj_options={}, tools={1: {
        'tooldia': 1, 'data': {}, 'gcode': 'G0 X0 Y0 Z2\nG1 Z-1\nX1\nG0 Z2\n'}})
    passes, warnings = passes_from_cncjob(job, 0.1)
    assert warnings and passes[0].tool.kind == 'flat'
    np.testing.assert_array_equal(passes[0].paths[0], [[0, 0, 2], [0, 0, -1], [1, 0, -1]])
    job.obj_options['photocarve'] = {'z_is_power': True}
    job.tools[1].update(data={'tools_mill_min_power': 100, 'tools_mill_spindlespeed': 1000},
                         gcode='G0 X0 Y0.5\nM4 S400\nG1 X0.3333\nM5\nG1 X1')
    passes, warnings = passes_from_cncjob(job, 0.1)
    assert passes[0].z_is_power and len(passes[0].paths) == 1
    assert passes[0].paths[0][-1, 2] == pytest.approx(1 / 3)
    job.tools[1]['data']['tools_mill_min_power'] = 1000
    with pytest.raises(ValueError):
        passes_from_cncjob(job, 0.1)


def test_sim_sessions_burn_isolated_restart_and_error(photo_controller, monkeypatch):
    import threading
    from unittest.mock import Mock
    from types import SimpleNamespace
    from shapely import LineString
    from appPlugins.ToolPhotoCarve import PhotoCarveGen as genmod
    from appPlugins.ToolPhotoCarve import PhotoCarveView3d as viewmod
    tool, app, qapp = photo_controller
    view = Mock()
    monkeypatch.setattr(viewmod, 'PhotoCarveView3d', Mock(return_value=view))
    geo = SimpleNamespace(kind='geometry', units='MM', obj_options={'photocarve': {
        'z_is_power': True, 'tools': {'1': {'kind': 'flat', 'diameter': 1,
                                          'angle_deg': 90, 'tip_diameter': 0}}}},
        tools={1: {'solid_geometry': [LineString([[0, 0, 0], [1, 0, 1]])]}})
    app.collection.get_active.return_value = geo
    app.on_3d_area = Mock()
    app.plotcanvas3d = Mock()
    tool.on_simulate()
    old = tool.gen.session
    assert old.kind == 'laser' and old.burn.dtype == np.float32
    monkeypatch.setattr(genmod, 'stamp_path', Mock(side_effect=AssertionError('laser used stock')))
    monkeypatch.setattr(genmod, 'Stock', Mock(side_effect=AssertionError('laser allocated stock')))
    tool.gen.sim_tick()
    task = app.worker_task.emit.call_args.args[0]
    assert old.busy
    count = app.worker_task.emit.call_count
    tool.gen.sim_tick()
    assert app.worker_task.emit.call_count == count
    entered, release = threading.Event(), threading.Event()
    original_burn = genmod.burn_image

    def held_burn(frame, moves, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_burn(frame, moves, image=kwargs['image'])

    monkeypatch.setattr(genmod, 'burn_image', held_burn)
    worker = threading.Thread(target=task['fcn'], args=task['params'])
    worker.start()
    assert entered.wait(5)
    tool.on_stop()
    tool.on_simulate()
    current = tool.gen.session
    release.set()
    worker.join(5)
    assert not worker.is_alive() and old.burn.sum() == pytest.approx(1)
    qapp.processEvents()
    assert not current.burn.any()
    assert not view.update_stock.called
    monkeypatch.setattr(genmod, 'burn_image', original_burn)
    tool.ui.sim_speed_entry.set_value(100)
    tool.gen.sim_tick()
    task = app.worker_task.emit.call_args.args[0]
    task['fcn'](*task['params'])
    qapp.processEvents()
    assert current.burn.sum() == pytest.approx(1)
    assert not current.busy and view.set_burn.called
    assert not tool.gen.sim_timer.isActive()
    tool.on_simulate()
    monkeypatch.setattr(genmod, 'burn_image', Mock(side_effect=ValueError('bad burn')))
    tool.gen.sim_tick()
    task = app.worker_task.emit.call_args.args[0]
    task['fcn'](*task['params'])
    qapp.processEvents()
    assert not tool.gen.session.busy and not tool.gen.sim_timer.isActive()
    assert 'bad burn' in app.inform.emit.call_args.args[0]


def test_sim_milling_session_detaches_paths_and_updates_on_gui(photo_controller, monkeypatch):
    from unittest.mock import Mock
    from types import SimpleNamespace
    from shapely import LineString
    from appPlugins.ToolPhotoCarve import PhotoCarveView3d as viewmod
    from appPlugins.ToolPhotoCarve.PhotoCarveSim import Stock, stamp_path
    tool, app, qapp = photo_controller
    view = Mock()
    monkeypatch.setattr(viewmod, 'PhotoCarveView3d', Mock(return_value=view))
    shape = LineString([[0, 0, -0.25], [1, 0, -0.5]])
    geo = SimpleNamespace(kind='geometry', units='MM', obj_options={'photocarve': {
        'z_is_power': False, 'tools': {'1': {'kind': 'ball', 'diameter': 0.5,
                                           'angle_deg': 90, 'tip_diameter': 0}}}},
        tools={1: {'solid_geometry': [shape]}})
    app.collection.get_active.return_value = geo
    app.on_3d_area = Mock()
    app.plotcanvas3d = Mock()
    tool.on_simulate()
    session = tool.gen.session
    assert session.stock.bounds == (-0.25, -0.25, 1.25, 0.25)
    geo.tools[1]['solid_geometry'] = []
    tool.ui.sim_speed_entry.set_value(100)
    tool.gen.sim_tick()
    task = app.worker_task.emit.call_args.args[0]
    task['fcn'](*task['params'])
    assert not view.update_stock.called
    qapp.processEvents()
    expected = Stock(session.frame)
    stamp_path(expected, np.asarray(shape.coords), session.passes[0].tool)
    np.testing.assert_array_equal(view.update_stock.call_args.args[0], expected.height)
    view.set_tool.assert_called_once()
    assert not session.busy and not tool.gen.sim_timer.isActive()
    tool.on_plugin_cleanup()
    assert tool.gen.session is None and tool._view is None
    geo.kind = 'cncjob'
    geo.tools[1]['gcode'] = 'G0 X0 Y0 Z2\nG1 Z-0.5\nG1 X1\nG0 Z2'
    tool.on_simulate()
    assert view.set_paths.call_args.kwargs['machine_coords'] is True


@pytest.mark.parametrize('rows', [6, 1201])
def test_sim_stock_visual_physical_mesh_updates_without_gpu(rows):
    from unittest.mock import Mock, patch
    from types import SimpleNamespace
    from appPlugins.ToolPhotoCarve.PhotoCarveView3d import PhotoCarveView3d
    from appPlugins.ToolPhotoCarve.PhotoCarveModel import ImageFrame
    from appPlugins.ToolPhotoCarve.PhotoCarveSim import Stock
    stock = Stock(ImageFrame(10, 20, 8, 6, 2, 6 / rows))
    canvas = SimpleNamespace(view=SimpleNamespace(scene=Mock()))
    with patch('appPlugins.ToolPhotoCarve.PhotoCarveView3d.scene.visuals'):
        view = PhotoCarveView3d(canvas)
        view.set_stock(stock)
        assert np.ptp(view.vertices[:, 0]) == 8
        assert np.ptp(view.vertices[:, 1]) == 6
        faces = view.faces
        step = math.ceil(max(stock.frame.rows, stock.frame.cols) / 600)
        assert len(faces) == 2 * math.ceil(stock.frame.rows / step) * math.ceil(stock.frame.cols / step)
        stock.height[:] = -0.5
        view.update_stock(stock.height)
        assert view.faces is faces
        assert np.all(view.vertices[:, 2] == -0.5)
        view.clear()


@pytest.mark.parametrize('kind', ['ball', 'flat', 'vbit'])
def test_printed_relief_random_rounding_dense_oracle(model, kind):
    sim = _pure('PhotoCarveSim')
    params = model.JobParams(image_px=(7, 5), width=1, res=0.14, spacing=0.14,
                            mode='relief', strategy='xy', tool_kind=kind, tool_dia=0.25,
                            tool_tip=0.02, tool_angle=60, decimals=4)
    frame = model.mode_frame(params)
    depth = np.random.default_rng(719).random((frame.rows, frame.cols), dtype=np.float32)
    paths, certificate = model.relief_paths(depth, params, params.tool())
    assert model.certify_relief(paths, certificate, frame) >= -1e-9
    printed = []
    for path in paths:
        output = path.copy()
        output[:, :2] = np.round(output[:, :2], 4)
        output[:, 2] = model.quantize_z(output[:, 2], 4)
        printed.append(output)
    assert sim.reference_gouge(printed, params.tool(), -depth, frame, sub=8) <= 1e-9


@pytest.mark.parametrize('size', [(1, 7), (7, 1)])
def test_relief_singleton_axis_paths_and_certificate(model, size):
    params = model.JobParams(image_px=size, width=size[0], res=1, spacing=1,
                            mode='relief', strategy='xy', tool_kind='flat', tool_dia=0.1)
    depth = np.full((size[1], size[0]), 0.5, dtype=np.float32)
    paths, certificate = model.relief_paths(depth, params, params.tool())
    assert any(len(path) == 1 for path in paths)
    assert any(len(path) > 1 for path in paths)
    assert model.certify_relief(paths, certificate, model.mode_frame(params)) >= -1e-9


@pytest.mark.parametrize('width,spacing', [(40, 0.3), (200, 8)])
def test_spiral_sampled_and_dense_work_bounds(model, monkeypatch, width, spacing):
    params = model.JobParams(image_px=(40, 30), width=width, res=0.5, spacing=spacing, strategy='spiral')
    frame = model.mode_frame(params)
    diag = math.hypot(frame.width, frame.height)
    radius, b = diag / 2, spacing / (2 * math.pi)
    theta = radius / b
    exact_length = b / 2 * (theta * math.hypot(1, theta) + math.asinh(theta))
    dense = math.ceil(exact_length / (min(frame.pitch_x, frame.pitch_y) / 4)) + 1
    sampled = math.ceil(exact_length / min(frame.pitch_x, frame.pitch_y)) + 1
    bound = model.work_preflight(params, frame)['points']
    assert bound >= dense and bound >= 3 * sampled
    paths = model.spiral_paths(params)
    assert sum(map(len, paths)) <= bound
    assert all(np.all(path >= [0, 0]) and np.all(path <= [frame.width, frame.height]) for path in paths)
    monkeypatch.setattr(model, 'MAX_POINTS', dense - 1)
    with pytest.raises(model.PhotoCarveError):
        model.spiral_paths(params)


def test_laser_seeded_splits_and_overscan_only_preflight_rejection(model, monkeypatch):
    laser = _pure('PhotoCarveLaser')
    rng = np.random.default_rng(31)
    for gap in (0, 1, 4):
        for overscan in (0, 7):
            params = model.JobParams(image_px=(31, 7), width=31, res=1, spacing=1,
                                    mode='laser', strategy='gray', lift_gap=gap, overscan=overscan)
            power = rng.integers(0, 4, (7, 31)).astype(np.float32) / 3
            paths = laser.laser_paths(power, params)
            assert sum(map(len, paths)) <= model.work_preflight(params, model.mode_frame(params))['points']
    params = replace(params, image_px=(3, 1), width=3, overscan=100)
    frame = model.mode_frame(params)
    assert frame.rows * (frame.cols + 1) < model.work_preflight(params, frame)['points']
    monkeypatch.setattr(model, 'MAX_POINTS', frame.rows * (frame.cols + 1))
    with pytest.raises(model.PhotoCarveError):
        model.work_preflight(params, frame)


@pytest.mark.parametrize('minimum', [0, 100])
def test_cnc_laser_real_printed_thirds_burn(minimum):
    from types import SimpleNamespace
    from shapely import LineString
    from camlib import AttrDict
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    from appPlugins.ToolPhotoCarve.PhotoCarveObject import passes_from_cncjob
    model, sim = _pure('PhotoCarveModel'), _pure('PhotoCarveSim')
    job, built = _writer_job('GRBL_laser', laser=True, tools_mill_min_power=minimum)
    path = np.array([[0, 0.5, 0], [1 / 3, 0.5, 1 / 3]], dtype=np.float64)
    built['tools'][1]['solid_geometry'] = [LineString(path)]
    code, header = write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                                    True, True, False, True, built['photocarve'])
    pp = job.app.preprocessors['GRBL_laser']
    attrs = AttrDict(job.postdata)

    def printer(x, y):
        return tuple(float(pp.coord(attrs, value)) for value in pp.bed_xy(attrs, x, y))

    source = sim.laser_moves_from_paths([path], as_s=True, minimum=minimum, maximum=1000, printer=printer)
    printed = sim.gcode_motion(header + code, 'MM', laser=True)
    cuts = [move for move in printed if not move.rapid and move.laser_on and move.s > 0]
    assert cuts[0].x1 == 0.3333 and cuts[0].s == source[0].s
    frame = model.ImageFrame(0, 0, 1, 1, 1 / 3, 1)
    np.testing.assert_allclose(sim.burn_image(frame, cuts, 1000), sim.burn_image(frame, source, 1000), atol=1e-7)
    unprinted = sim.laser_moves_from_paths([path], as_s=True, minimum=minimum, maximum=1000)
    assert abs(sum(move.s * abs(move.x1 - move.x0) for move in unprinted) -
               sum(move.s * abs(move.x1 - move.x0) for move in source)) <= 1000 * 1e-4
    built['tools'][1]['gcode'] = header + code
    cnc = SimpleNamespace(units='MM', tools=built['tools'], obj_options={'photocarve': built['photocarve']})
    passes, _ = passes_from_cncjob(cnc, 0.1)
    assert passes[0].paths[0][-1, 2] == pytest.approx((source[0].s - minimum) / (1000 - minimum))
