"""Tracing mode of the Image import plugin without a browser (appCommon/imagetrace.py).

The tracing used `svgtrace`, which needs headless Chromium through playwright. appCommon.imagetrace runs imagetracer.js in an
embedded QuickJS engine. These tests check the traced geometry, the option handling and that nothing browser related is
imported or required any more.
"""

import ast
import importlib
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from appCommon.imagetrace import options_to_js, trace  # noqa: E402


def numbers(path_data):
    return [float(n) for n in re.findall(r'-?\d+(?:\.\d+)?', path_data)]


def path_bounds(svg):
    """Bounding box (x0, y0, x1, y1) of every coordinate pair of every <path d="..."> of the SVG."""
    xs, ys = [], []
    for d in re.findall(r'<path[^>]*\sd="([^"]*)"', svg):
        values = numbers(d)
        xs += values[0::2]
        ys += values[1::2]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.fixture
def square_png(tmp_path):
    image = Image.new('RGB', (64, 64), 'white')
    ImageDraw.Draw(image).rectangle([20, 20, 43, 43], fill='black')
    path = tmp_path / 'square.png'
    image.save(path)
    return str(path)


@pytest.fixture
def two_color_png(tmp_path):
    image = Image.new('RGB', (80, 40), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle([4, 4, 30, 30], fill=(200, 0, 0))
    draw.rectangle([44, 8, 70, 34], fill=(0, 0, 200))
    path = tmp_path / 'two.png'
    image.save(path)
    return str(path)


@pytest.fixture
def rings_png(tmp_path):
    """Many coloured shapes: the traced SVG is long (several KB), unlike the tiny fixtures above."""
    image = Image.new('RGB', (256, 256), 'white')
    draw = ImageDraw.Draw(image)
    for i in range(12):
        draw.ellipse([8 + i * 9, 8 + i * 6, 120 + i * 10, 90 + i * 12], outline=(20 * i, 255 - 20 * i, 90), width=3)
    path = tmp_path / 'rings.png'
    image.save(path)
    return str(path)


def test_black_and_white_trace_returns_the_exact_outline(square_png):
    svg = trace(square_png, blackAndWhite=True)
    assert svg.startswith('<svg') and 'width="64"' in svg and 'height="64"' in svg
    assert path_bounds(svg) == (20, 20, 44, 44)  # a 24 px square from pixel edge to pixel edge


@pytest.mark.parametrize('mode', ['default', 'curvy', 'smoothed'])
def test_a_long_svg_comes_back_as_a_python_string(rings_png, mode):
    # imagetracer.js builds the SVG by concatenation; quickjs-ng returns a long result of that kind in a form its
    # Python binding rejects ("TypeError: Unknown quickjs tag: -6") unless it is flattened before it is returned
    svg = trace(rings_png, mode=mode)
    assert isinstance(svg, str) and svg.startswith('<svg') and svg.endswith('</svg>')
    assert svg.count('<path') > 10 and len(svg) > 5000


def test_the_black_preset_name_is_the_same_as_black_and_white(square_png):
    assert trace(square_png, mode='black') == trace(square_png, blackAndWhite=True)


def test_colour_trace_makes_a_path_per_colour_region(two_color_png):
    svg = trace(two_color_png)  # preset "default"
    fills = set(re.findall(r'fill="(rgb\([^"]*\))"', svg))
    assert 'rgb(200,0,0)' in fills and 'rgb(0,0,200)' in fills


def test_custom_option_object_as_written_by_the_plugin(two_color_png):
    # ToolImage.get_tracing_options builds this string from the form values, booleans as Python True/False
    options = ('{ ltres:1.0, qtres:1.0, pathomit:8, rightangleenhance:True, colorsampling:2, numberofcolors:8, '
               'mincolorratio:0, colorquantcycles:3, strokewidth:1, linefilter:False, roundcoords:1, '
               'blurradius:0, blurdelta:20 }')
    svg = trace(two_color_png, mode=options)
    assert svg.startswith('<svg') and '<path' in svg


def test_the_number_of_colours_option_is_honoured(two_color_png):
    base = '{ ltres:1.0, qtres:1.0, pathomit:8, colorsampling:2, numberofcolors:%d, mincolorratio:0, colorquantcycles:3, ' \
           'strokewidth:1, linefilter:False, roundcoords:1, blurradius:0, blurdelta:20 }'
    few = trace(two_color_png, mode=base % 2).count('<path')
    many = trace(two_color_png, mode=base % 16).count('<path')
    assert few <= many


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        trace(str(tmp_path / 'nope.png'))


def test_options_to_js():
    assert options_to_js('default') == '"default"'
    assert options_to_js('  posterized1 ') == '"posterized1"'
    assert options_to_js('we"ird\\name') == '"we\\"ird\\\\name"'
    assert options_to_js('{ a:True, b:False, c:Truetype }') == '({ a:true, b:false, c:Truetype })'


def test_missing_quickjs_does_not_break_the_import_and_gives_a_clear_error(square_png):
    # sys.modules['quickjs'] = None makes "import quickjs" raise ImportError, as on a platform where pip could not install it
    code = ("import sys\n"
            "sys.modules['quickjs'] = None\n"
            "sys.path.insert(0, %r)\n"
            "from appCommon.imagetrace import trace\n"
            "try:\n"
            "    trace(%r)\n"
            "except ImportError as err:\n"
            "    print(err)\n" % (str(ROOT), square_png))
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert "needs the 'quickjs-ng' package" in result.stdout and 'pip install -r requirements.txt' in result.stdout


def test_toolimage_shows_the_missing_quickjs_error_instead_of_raising(monkeypatch):
    pytest.importorskip('rasterio')  # the Image import plugin is only loaded when rasterio is installed
    tool_module = importlib.import_module('appPlugins.ToolImage')
    message = "Image tracing needs the 'quickjs-ng' package, which could not be imported."
    monkeypatch.setattr(tool_module, 'trace', Mock(side_effect=ImportError(message)))
    monkeypatch.setattr(tool_module.QtWidgets.QFileDialog, 'getOpenFileName', lambda *args, **kwargs: ('picture.png', ''))
    tool = Mock()
    tool.ui.import_mode_radio.get_value.return_value = 'trace'
    tool.ui.control_radio.get_value.return_value = 'presets'
    tool.ui.presets_combo.get_value.return_value = 'default'

    tool_module.ToolImage.on_file_importimage(tool, threaded=False)

    tool.app.inform.emit.assert_called_once_with('[ERROR_NOTCL] ' + message)
    tool.import_image.assert_not_called()


def test_toolimage_does_not_import_the_browser_stack():
    tree = ast.parse((ROOT / 'appPlugins' / 'ToolImage.py').read_text(encoding='utf-8'))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert not imported & {'svgtrace', 'pyppeteer', 'playwright'}, imported
    assert 'appCommon' in imported


def test_requirements_name_quickjs_not_the_browser_stack():
    for name in ('requirements.txt', 'environment.yml'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert 'quickjs-ng' in text, name
        assert not re.search(r'^\s*-?\s*(svgtrace|pyppeteer|playwright)\b', text, re.M), name


def quickjs_packages(name, environment):
    """Names of the quickjs packages that pip installs from requirements.txt / environment.yml in this environment."""
    from packaging.requirements import Requirement
    selected = []
    for line in (ROOT / name).read_text(encoding='utf-8').splitlines():
        line = line.strip().lstrip('-').strip()
        if line.startswith("'") and line.endswith("'"):  # a quoted YAML scalar
            line = line[1:-1]
        if line.startswith('quickjs'):
            requirement = Requirement(line)
            if requirement.marker is None or requirement.marker.evaluate(environment):
                selected.append(requirement.name)
    return selected


@pytest.mark.parametrize('name', ['requirements.txt', 'environment.yml'])
@pytest.mark.parametrize('sys_platform, machine, expected', [
    ('darwin', 'arm64', 'quickjs-ng'),
    ('darwin', 'x86_64', 'quickjs'),  # quickjs-ng has no wheel for Intel Macs and its sdist does not build
    ('linux', 'x86_64', 'quickjs-ng'),
    ('linux', 'aarch64', 'quickjs-ng'),
    ('win32', 'AMD64', 'quickjs-ng'),
])
def test_exactly_one_quickjs_package_is_installed_per_platform(name, sys_platform, machine, expected):
    environment = {'sys_platform': sys_platform, 'platform_machine': machine}
    assert quickjs_packages(name, environment) == [expected]
