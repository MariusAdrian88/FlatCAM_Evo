"""Photo engraving: physical image sampling, safety limits and calibrated precision."""

import importlib.util
import math
import sys
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


def test_runtime_messages_translate_templates_before_substitution(model, monkeypatch):
    import builtins
    gen = _pure('PhotoCarveGen')
    laser = _pure('PhotoCarveLaser')
    sim = _pure('PhotoCarveSim')
    objects = _pure('PhotoCarveObject')
    translations = {
        '%s must be positive.': 'Positif requis: %s.',
        'Image size changed after the job parameters were captured.': 'Dimensions modifiées.',
        'Laser path power must be between 0 and 1.': 'Puissance hors limites.',
        'Unsupported G-code syntax on line %d.': 'Syntaxe incorrecte à la ligne %d.',
        'The image produced no engraving paths.': 'Aucun parcours.',
        'Maximum depth clamped to the V-bit width limit %.12g.': 'Profondeur limitée à %.12g.',
    }
    monkeypatch.setattr(builtins, '_', lambda text: translations.get(text, text), raising=False)
    with pytest.raises(model.PhotoCarveError) as error:
        model.validate(model.JobParams(width=0))
    assert str(error.value) == 'Positif requis: width.'
    with pytest.raises(model.PhotoCarveError) as error:
        gen.run_job(Image.new('L', (2, 2)), model.JobParams())
    assert str(error.value) == 'Dimensions modifiées.'
    with pytest.raises(model.PhotoCarveError) as error:
        laser.format_power(2, 0, 1000)
    assert str(error.value) == 'Puissance hors limites.'
    with pytest.raises(model.PhotoCarveError) as error:
        sim.interpret_gcode('(header)\nG1 Xnan')
    assert str(error.value) == 'Syntaxe incorrecte à la ligne 2.'
    with pytest.raises(model.PhotoCarveError) as error:
        objects.build_geometry(model.JobResult([], [], {}), model.JobParams(), {})
    assert str(error.value) == 'Aucun parcours.'
    params = model.JobParams()
    checked, warnings = model.validate(params)
    assert checked.max_depth == model.vbit_limit(params)
    assert warnings == ['Profondeur limitée à %.12g.' % checked.max_depth]
    monkeypatch.delattr(builtins, '_')
    with pytest.raises(model.PhotoCarveError) as error:
        model.validate(model.JobParams(width=0))
    assert str(error.value) == 'width must be positive.'


def test_writer_runtime_messages_translate_and_keep_required_precision(monkeypatch):
    import builtins
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    job, built = _writer_job()
    translations = {
        'The certified photo engraving preprocessor is not installed.': 'Préprocesseur absent.',
        'Photo engraving requires at least %d coordinate decimals.': 'Précision requise: %d décimales.',
    }
    monkeypatch.setattr(builtins, '_', lambda text: translations.get(text, text), raising=False)
    args = (job, 1, built['tools'], (0, 0), None, 0, True, True, False, True, built['photocarve'])
    post = job.app.preprocessors.pop('default')
    assert write_tool_gcode(*args) == 'fail'
    assert job.app.inform.emit.call_args.args[0] == '[ERROR_NOTCL] Préprocesseur absent.'
    job.app.preprocessors['default'] = post
    job.coords_decimals = 2
    assert write_tool_gcode(*args) == 'fail'
    assert job.app.inform.emit.call_args.args[0] == '[ERROR_NOTCL] Précision requise: 4 décimales.'


def test_simulation_labels_translate_and_presented_messages_are_not_translated_again(monkeypatch):
    import builtins
    from types import SimpleNamespace
    from unittest.mock import Mock
    from appPlugins.ToolPhotoCarve.PhotoCarve import ToolPhotoCarve
    from appPlugins.ToolPhotoCarve.PhotoCarveView3d import PhotoCarveView3d
    translations = {
        'CNCJob — machine coordinates': 'CNCJob — coordonnées machine',
        'Photo Carve — model coordinates': 'Photo Carve — coordonnées modèle',
        'Déjà traduit.': 'Incorrect second translation.',
    }
    monkeypatch.setattr(builtins, '_', lambda text: translations.get(text, text), raising=False)
    view = SimpleNamespace(path=Mock(), label=SimpleNamespace(),
                           frame=SimpleNamespace(x0=0, y0=0, height=1))
    for machine, expected in ((True, 'CNCJob — coordonnées machine'),
                              (False, 'Photo Carve — coordonnées modèle')):
        PhotoCarveView3d.set_paths(view, [], machine_coords=machine)
        assert view.label.text == expected
    tool = SimpleNamespace(_gen_token=1, _preview_token=1, ui=object(),
                           app=SimpleNamespace(inform=Mock()))
    ToolPhotoCarve.on_generation_message(tool, 1, '[WARNING_NOTCL]', 'Déjà traduit.')
    assert tool.app.inform.emit.call_args.args[0] == '[WARNING_NOTCL] Déjà traduit.'
    ToolPhotoCarve.on_preview_done(tool, 1, 'Déjà traduit.')
    assert tool.app.inform.emit.call_args.args[0] == '[ERROR_NOTCL] Déjà traduit.'


@pytest.mark.parametrize('case', ['warning', 'validation', 'geometry'])
def test_tcl_preserves_translated_messages(case, monkeypatch, tmp_path):
    import builtins
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tclCommands.TclCommandPhotoCarve import TclCommandPhotoCarve
    image = tmp_path / 'photo.png'
    Image.new('L', (2, 2), 255 if case == 'geometry' else 0).save(image)
    geo = SimpleNamespace(obj_options={})

    def new_object(kind, name, initialize, **kwargs):
        return 'fail' if initialize(geo, app) == 'fail' else geo

    app = SimpleNamespace(units='MM', log=Mock(), inform=Mock(),
                          options={'cncjob_coords_decimals': 4, 'cncjob_bed_skew_x': 0,
                                   'cncjob_bed_skew_y': 0, 'cncjob_bed_max_x': 0, 'cncjob_bed_max_y': 0},
                          app_obj=SimpleNamespace(new_object=new_object))
    monkeypatch.setattr(builtins, '_', lambda text: 'traduit: ' + text, raising=False)
    command = TclCommandPhotoCarve(app)
    result = command.execute({'filename': str(image), 'width': 0 if case == 'validation' else 2,
                              'res': 0.5, 'sim_res': 0.5, 'tool_dia': 0.5,
                              'max_depth': 0.1, 'rough': case == 'warning'}, [])
    if case == 'warning':
        assert result is geo
        assert app.inform.emit.call_args.args[0] == (
            '[WARNING_NOTCL] traduit: Roughing is used only for Relief and Lithophane; it was ignored.')
    elif case == 'validation':
        assert result == 'traduit: width must be positive.'
    else:
        assert result == 'traduit: The image produced no engraving paths.'


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


_INVALID_JOBS = [
    {'width': float('nan')}, {'contrast': float('inf')}, {'res': 0}, {'spacing': -1},
    {'sim_res': 0}, {'gamma': 0}, {'levels': 2.5}, {'levels': 1}, {'overscan': -1},
    {'tool_kind': 'unknown'}, {'rough_kind': 'unknown'}, {'tool_tip': 6}, {'tool_angle': 180},
    {'mode': 'relief', 'strategy': 'raster'}, {'mode': 'vcarve', 'tool_kind': 'ball'},
    {'mode': 'halftone', 'strategy': '-', 'tool_kind': 'flat'},
    {'mode': 'lithophane', 'strategy': 'x', 'tone': 'linear'},
    {'mode': 'relief', 'tone': 'linear', 'strategy': 'x', 'rough': True, 'stepdown': 0},
    {'mode': 'relief', 'tone': 'linear', 'strategy': 'x', 'rough': True, 'allowance': -1},
    {'decimals': 2, 'tolerance': 0.001},
]


def test_invalid_job_is_rejected_before_arrays(model):
    for changes in _INVALID_JOBS:
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


def test_quantization_never_cuts_below_requested_z(model):
    z = np.array([-1, -0.995, -0.99999999999999, 0, 0.0049])
    quantized = model.quantize_z(z, 2)
    assert quantized.tolist() == [-1, -0.99, -1, 0, 0.01]
    assert np.all(quantized >= z - 1e-12)
    path = np.array([[0, 0, -1], [1, 0, -0.995], [2, 0, -1]], dtype=np.float64)
    np.testing.assert_array_equal(model.safe_rdp(path, 0.01), path)
    path[1, 2] = -1.005
    assert len(model.safe_rdp(path, 0.01)) == 2


def test_image_preparation_tone_mapping_and_lithophane_border(model, tmp_path):
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
    gray = np.array([[0, 0.5, 1]], dtype=np.float32)
    linear = model.depth_grid(gray, model.JobParams(tone='linear', max_depth=1))
    np.testing.assert_allclose(linear, [[1, 0.5, 0]])
    perceptual = model.depth_grid(gray, model.JobParams(tone='perceptual', max_depth=1))
    assert 0.45 < perceptual[0, 1] < 0.48
    # V-bit area: a 50 % gray covers half of the line spacing (90° V-bit, spacing 1 -> width 0.5, depth 0.25)
    area = model.depth_grid(gray, model.JobParams(tone='vbit_area', spacing=1, tool_angle=90, max_depth=1))
    np.testing.assert_allclose(area, [[0.5, 0.25, 0]], atol=1e-6)
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
    assert laser.format_power(0, 100, 1000) == 0
    assert laser.format_power(1 / 3, 100, 1000) == 400
    assert laser.format_power(1, 100, 1000) == 1000
    with pytest.raises(model.PhotoCarveError):
        laser.format_power(0.5, 1000, 1000)


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
            # The Milling tool of a laser job is the laser dot, not the hidden cutter diameter.
            params = replace(params, tool_dia=6.0, laser_dia=0.15)
            built = _pure('PhotoCarveObject').build_geometry(gen.run_job(image, params), params, {})
            assert {tool['tooldia'] for tool in built['tools'].values()} == {0.15}
            assert {tool['data']['tools_mill_tooldia'] for tool in built['tools'].values()} == {0.15}
            with pytest.raises(model.PhotoCarveError):
                gen.run_job(image, replace(params, laser_dia=0))
    rough = replace(base, mode='relief', tone='linear', strategy='x', rough=True,
                    rough_dia=0.25, stepdown=0.2, allowance=0.1)
    result = gen.run_job(image, rough)
    assert [part.role for part in result.passes] == ['rough', 'finish']
    assert min(path[:, 2].min() for path in result.passes[0].paths) >= -0.4 - 1e-9
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
    gen = _pure('PhotoCarveGen')
    image = Image.new('L', (8, 8), 0)
    params = model.JobParams(image_px=image.size, width=2, res=0.25, spacing=1,
                            tool_dia=1, tool_angle=45, max_depth=2, decimals=12, sim_res=0.25)
    result = gen.run_job(image, params)
    limit = model.vbit_limit(params)
    assert result.passes[-1].paths
    for path in result.passes[-1].paths:
        assert np.max(-path[:, 2]) <= limit + 1e-12


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
    sim = _pure('PhotoCarveSim')
    commands = ('G91', 'G92 X0', 'G28', 'G30', 'G53 G0 X1', 'G10 L2', 'G43 H1', 'G49',
                'G38.2 Z-1', 'G80', 'G81 X1', 'G18', 'G19', 'G93', 'M98 P1', 'M99',
                'G2 X1 Y1 I1 J0', 'G54', 'A1', 'G1 Xnan', 'G4 X1', 'G1 X1 #1')
    for command in commands:
        with pytest.raises(model.PhotoCarveError):
            sim.interpret_gcode('G0 X0 Y0 Z5\n' + command)
    with pytest.raises(model.PhotoCarveError):
        sim.interpret_gcode('G0 X0 Y0\nG1 X1')
    for end in ['M2', 'M30']:
        sim = _pure('PhotoCarveSim')
        text = 'G0 X0 Y0 Z0\nG1 X1\nM0\nG1 X2\nM1\nG1 X3\n' + end + '\nG1 X99'
        assert sim.interpret_gcode(text)[-1].x1 == 3
        assert sim.gcode_motion(text)[-1].x1 == 3
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
    # Milling passes only the tools selected in its table: the finishing tool alone is a valid job
    objects.validate_tools({2: built['tools'][2]}, built['photocarve'])
    with pytest.raises(model.PhotoCarveError):
        objects.validate_tools({}, built['photocarve'])


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


@pytest.mark.parametrize('pp,toolchange', [('default', False), ('default', True), ('GRBL_11_no_M6', True)])
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


@pytest.mark.parametrize('pp', ['GRBL_laser'])
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


def test_writer_fails_closed_for_missing_post_and_laser_settings():
    from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
    job, built = _writer_job('GRBL_laser', laser=True)

    def write():
        return write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                                 True, True, False, True, built['photocarve'])

    data = built['tools'][1]['data']
    # a new laser geometry starts with the dynamic power; the Milling combo writes it as M04
    assert data['tools_mill_laser_on'] == 'M04'
    data['tools_mill_laser_on'] = 'M03'
    assert write() == 'fail'
    data['tools_mill_laser_on'] = 'M04'
    data['tools_mill_min_power'] = 1000
    assert write() == 'fail'
    data['tools_mill_min_power'] = 0
    assert write() != 'fail'
    del job.app.preprocessors['GRBL_laser']
    assert write() == 'fail'
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
    objects, model = _pure('PhotoCarveObject'), _pure('PhotoCarveModel')
    with Image.open(Path(__file__).parent / 'fixtures' / 'image.png') as picture:
        image = picture.copy()
    params = model.JobParams(image_px=image.size, width=40, spacing=0.3, mode='laser', strategy='floyd')
    built['photocarve']['source'] = objects.embed_source(image, 'D:/photos/image.png', params)
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
    # The project holds the source image and the job parameters: Photo Carve opens them again, also in inch.
    picture, filename, options = objects.read_source(restored_obj.obj_options['photocarve'], 'MM')
    assert filename == 'image.png'
    assert np.array_equal(np.asarray(picture), np.asarray(image))
    assert (options['tools_photocarve_width'], options['tools_photocarve_strategy']) == (40, 'floyd')
    _picture, _filename, inch = objects.read_source(restored_obj.obj_options['photocarve'], 'IN')
    assert inch['tools_photocarve_width'] == pytest.approx(40 / 25.4)
    assert inch['tools_photocarve_spacing'] == pytest.approx(0.3 / 25.4)
    assert inch['tools_photocarve_gamma'] == 1.0
    assert objects.read_source(built['photocarve'] | {'source': None}, 'MM') is None


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
    from types import SimpleNamespace
    from appPlugins.ToolPhotoCarve.PhotoCarveObject import passes_from_cncjob
    job = SimpleNamespace(units='MM', obj_options={}, tools={
        1: {'tooldia': 1, 'gcode': 'G20\nG0 X0 Y0 Z0\nG1 X1\nM30\n'},
        2: {'tooldia': 2, 'gcode': 'G0 X0 Y0 Z0\nG1 X99\n'}})
    passes, _ = passes_from_cncjob(job, 0.1)
    assert len(passes) == 1
    assert passes[0].paths[-1][-1, 0] == pytest.approx(25.4)
    job.tools[1]['gcode'] = job.tools[1]['gcode'].replace('M30\n', '')
    passes, _ = passes_from_cncjob(job, 0.1)
    assert passes[-1].paths[-1][-1, 0] == pytest.approx(99 * 25.4)


def test_printed_relief_random_rounding_dense_oracle(model):
    for kind in ['ball', 'flat', 'vbit']:
        sim = _pure('PhotoCarveSim')
        from appPlugins.ToolPhotoCarve.PhotoCarveGcode import write_tool_gcode
        skew = (0.13, -0.09, 20, 17)
        params = model.JobParams(image_px=(7, 5), width=1, res=0.14, spacing=0.14,
                                mode='relief', strategy='xy', tool_kind=kind, tool_dia=0.25,
                                tool_tip=0.02, tool_angle=60, decimals=4, skew=skew)
        frame = model.mode_frame(params)
        depth = np.random.default_rng(719).random((frame.rows, frame.cols), dtype=np.float32)
        paths, certificate = model.relief_paths(depth, params, params.tool())
        assert model.certify_relief(paths, certificate, frame) >= -1e-9
        job, defaults = _writer_job()
        built = _pure('PhotoCarveObject').build_geometry(
            model.JobResult([model.Pass('finish', params.tool(), paths)], [], {}), params,
            defaults['tools'][1]['data'])
        job._bed_skew_x, job._bed_skew_y, job._bed_limit_x, job._bed_limit_y = skew
        job._bed_offset_x, job._bed_offset_y = 0.00493, -0.00371
        code, header = write_tool_gcode(job, 1, built['tools'], (0, 0), None, 0,
                                        True, True, False, True, built['photocarve'])
        matrix = np.array([[1, skew[0] / skew[3]], [skew[1] / skew[2], 1]])
        printed = []
        for span in sim.gcode_motion(header + code):
            if not span.rapid:
                output = np.array([[span.x0, span.y0, span.z0], [span.x1, span.y1, span.z1]])
                output[:, :2] = np.linalg.solve(matrix, (output[:, :2] - [0.00493, -0.00371]).T).T
                printed.append(output)
        assert sim.reference_gouge(printed, params.tool(), -depth, frame, sub=8) <= 1e-9


def test_relief_singleton_axis_paths_and_certificate(model):
    for size in ((1, 7), (7, 1)):
        params = model.JobParams(image_px=size, width=size[0], res=1, spacing=1,
                                mode='relief', strategy='xy', tool_kind='flat', tool_dia=0.1)
        depth = np.full((size[1], size[0]), 0.5, dtype=np.float32)
        paths, certificate = model.relief_paths(depth, params, params.tool())
        assert any(len(path) == 1 for path in paths)
        assert any(len(path) > 1 for path in paths)
        assert model.certify_relief(paths, certificate, model.mode_frame(params)) >= -1e-9
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


def test_cnc_laser_real_printed_thirds_burn():
    for minimum in [0, 100]:
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


def test_cnc_sim_refuses_uncertified_runnable_source():
    for change in ['edited_source_file', 'source_file', 'prepend_snippet', 'append_snippet', 'units']:
        from types import SimpleNamespace
        from appPlugins.ToolPhotoCarve.PhotoCarveObject import passes_from_cncjob
        code = 'G0 X0 Y0 Z0\nG1 X1 Z-1\n'
        job = SimpleNamespace(units='MM', tools={1: {'tooldia': 1, 'gcode': code}},
                              obj_options={'photocarve': {'units': 'MM'}}, gc_start='', source_file=code)
        assert passes_from_cncjob(job, 0.1)[0]
        if change == 'units':
            job.obj_options['photocarve']['units'] = 'IN'
        else:
            setattr(job, change, 'G91\n' + code)
        with pytest.raises(ValueError):
            passes_from_cncjob(job, 0.1)


def test_marked_cnc_transforms_refuse_before_mutation():
    job, built = _writer_job()
    job.obj_options['photocarve'] = built['photocarve']
    job.gcode = 'G0 X0 Y0 Z2\nG1 X10 Z-1'
    job.multitool, job.gcode_parsed = False, []
    before = job.gcode
    assert job.scale(0.1) == 'fail'
    assert job.skew(10, 0, (0, 0)) == 'fail'
    assert job.gcode == before
    job.tools = built['tools']
    job.convert_units('IN')
    assert job.tools[1]['tooldia'] == pytest.approx(0.1 / 25.4)
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tclCommands.TclCommandScale import TclCommandScale
    job, built = _writer_job()
    job.kind = 'cncjob'
    job.obj_options['photocarve'] = built['photocarve']
    job.bounds = lambda: (0, 0, 10, 10)
    job.gcode_flow_blocked = lambda action: None
    job.regenerate_source = Mock()
    app = SimpleNamespace(collection=SimpleNamespace(get_by_name=lambda name: job), log=Mock())
    command = TclCommandScale(app)
    command.raise_tcl_error = Mock(side_effect=ValueError('certified engraving'))
    with pytest.raises(ValueError):
        command.execute({'factor': 0.1}, ['photo'])
    job.regenerate_source.assert_not_called()


def test_marked_cnc_unit_conversion_motion_and_certificate():
    for laser in [False, True]:
        from unittest.mock import Mock, patch
        from appObjects.CNCJobObject import CNCJobObject
        from tests.helpers_objects import make_app_0b
        from copy import deepcopy
        _, built = _writer_job('GRBL_laser' if laser else 'default', laser=laser)
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            job = CNCJobObject('photo', make_app_0b())
        job.units, job.multitool = 'MM', True
        job.gcode_parsed = []
        job.tools = deepcopy(built['tools'])
        job.tools[1]['data'].update(tools_mill_vtipdia=0.02, tools_mill_endxy=[25.4, 50.8])
        built['photocarve']['tools']['1']['tip_diameter'] = 0.02
        job.tools[1]['gcode'] = 'G21\nG0 X0 Y0 Z25.4\nM4 S505\nG1 X25.4 Z-2.54'
        job.gc_start = 'G21\n'
        job.source_file = job.gc_start + job.tools[1]['gcode']
        job.obj_options['photocarve'] = deepcopy(built['photocarve'])
        mark = deepcopy(built['photocarve'])
        for units, factor in [('IN', 1 / 25.4), ('IN', 1 / 25.4), ('MM', 1)]:
            job.convert_units(units)
            assert job.tools[1]['tooldia'] == pytest.approx(0.1 * factor, abs=1e-12)
            assert job.tools[1]['data']['tools_mill_tooldia'] == pytest.approx(0.1 * factor)
            converted = job.obj_options['photocarve']
            assert converted['units'] == units
            assert converted['tools']['1']['diameter'] == pytest.approx(0.1 * factor)
            assert converted['tools']['1']['tip_diameter'] == pytest.approx(0.02 * factor)
            assert job.tools[1]['data']['tools_mill_vtipdia'] == pytest.approx(0.02 * factor)
            assert job.tools[1]['data']['tools_mill_endxy'] == pytest.approx([25.4 * factor, 50.8 * factor])
            assert converted['xy_err'] == pytest.approx(mark['xy_err'] * factor)
            # the text is rounded again at the new unit: the job is no longer the certified program
            assert converted['certified'] is False
            spans = _pure('PhotoCarveSim').gcode_motion(job.source_file, units, laser=laser)
            assert spans[-1].x1 == pytest.approx(25.4 * factor, abs=0.003)
            assert spans[-1].z1 == pytest.approx(-2.54 * factor, abs=0.003)
            assert spans[-1].s == 505


