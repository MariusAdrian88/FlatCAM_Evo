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


def _model():
    path = Path(__file__).resolve().parents[1] / 'appPlugins/ToolPhotoCarve/PhotoCarveModel.py'
    spec = importlib.util.spec_from_file_location('photo_carve_model', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def model():
    return _model()


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
    oriented = image.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[274] = 6
    filename = tmp_path / 'oriented.png'
    oriented.save(filename, exif=exif)
    loaded = model.load_image(filename)
    assert loaded.size == image.size
    np.testing.assert_array_equal(model.prepare_image(loaded, params), gray)


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
