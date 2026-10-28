"""
appIO.export_svg(): the viewBox of the SVG has to contain the drawing.

The exported shapes keep their FlatCAM coordinates and are put in a group with ``transform="scale(1,-1)"`` so that
the Y axis points up in Inkscape and similar programs. After the flip the drawing spans y from ``-ymax`` to ``-ymin``,
so the viewBox has to start at ``-ymax``. It started at ``ymin - height``, which is the same value only when
``ymin == 0``: for every other drawing the viewBox did not contain the drawing and a viewer showed an empty page.
"""

import re
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, Mock

import pytest
from PyQt6 import QtWidgets
from shapely.geometry import LineString, box
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appParsers.SVG.reader import svg_to_shapes
from camlib import Geometry
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402

NUMBER = re.compile(r'-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?')


@pytest.fixture(scope='module')
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _app_with_geometry(geometry, kind='geometry'):
    """A headless app whose collection holds one object named 'geo': a Geometry, or a Gerber for kind='gerber'."""
    app = make_app()
    app.new_project_signal = Mock()
    app.file_opened = Mock()
    app.file_saved = Mock()
    app.proc_container = MagicMock()

    obj = Geometry(app)
    obj.kind, obj.multigeo = kind, False          # set by GeometryObject / GerberObject
    obj.units = 'MM'
    obj.solid_geometry = geometry
    app.collection = Mock()
    app.collection.get_by_name.return_value = obj
    return app


def _export(qt_app, geometry, tmp_path, kind='geometry'):
    """Export a Geometry (or a Gerber) with appIO.export_svg() and return the path of the file."""
    app = _app_with_geometry(geometry, kind)
    path = tmp_path / 'out.svg'
    assert appIO(app).export_svg('geo', str(path)) != 'fail'
    return path


def _view_box(path):
    root = ET.parse(str(path)).getroot()
    x, y, width, height = (float(v) for v in root.get('viewBox').split())
    return x, y, width, height


def _flipped_content_bounds(path):
    """The bounds of the drawing after the group transform scale(1,-1): what a viewer shows."""
    root = ET.parse(str(path)).getroot()
    group = [e for e in root.iter() if e.tag.endswith('}g') or e.tag == 'g'][0]
    assert group.get('transform') == 'scale(1,-1)'
    xs, ys = [], []
    for element in group.iter():
        data = element.get('d') or element.get('points') or ''
        numbers = [float(v) for v in NUMBER.findall(data)]
        xs += numbers[0::2]
        ys += numbers[1::2]
    return min(xs), -max(ys), max(xs), -min(ys)


# (xmin, ymin, xmax, ymax); the 2nd and the 3rd start above and below y = 0, the last one is the only old working case
BOXES = [(10, 20, 40, 30), (-5, -30, 7, -10), (0, 0, 10, 5), (134.4, 73.79, 154.6, 124.16)]


@pytest.mark.parametrize('bounds', BOXES)
def test_the_view_box_contains_the_drawing(qt_app, tmp_path, bounds):
    path = _export(qt_app, [box(*bounds)], tmp_path)

    vx, vy, vw, vh = _view_box(path)
    cx0, cy0, cx1, cy1 = _flipped_content_bounds(path)

    assert vw == pytest.approx(bounds[2] - bounds[0])
    assert vh == pytest.approx(bounds[3] - bounds[1])
    assert vx == pytest.approx(cx0)
    assert vy == pytest.approx(cy0)
    assert vx + vw == pytest.approx(cx1)
    assert vy + vh == pytest.approx(cy1)


def test_a_line_drawing_is_inside_the_view_box_too(qt_app, tmp_path):
    path = _export(qt_app, [LineString([(100, 200), (150, 260)])], tmp_path)

    vx, vy, vw, vh = _view_box(path)
    cx0, cy0, cx1, cy1 = _flipped_content_bounds(path)

    assert (vx, vy, vx + vw, vy + vh) == pytest.approx((cx0, cy0, cx1, cy1))


@pytest.mark.parametrize('bounds', BOXES)
def test_the_exported_file_is_read_back_with_the_same_size_and_orientation(qt_app, tmp_path, bounds):
    # a rectangle with a small box at its lower left corner and one at its upper right corner: not symmetric, so a
    # mirrored or moved drawing is noticed
    original = box(*bounds).union(box(bounds[0], bounds[1], bounds[0] + 1, bounds[1] + 3))
    original = original.union(box(bounds[2] - 2, bounds[3] - 1, bounds[2], bounds[3]))
    path = _export(qt_app, [original], tmp_path)

    # a Geometry object is exported as its outlines
    imported = unary_union(svg_to_shapes(str(path), object_type='geometry', units='MM'))

    # a FlatCAM export keeps the position: the drawing comes back at its original place, not mirrored
    expected = original.boundary
    assert imported.bounds == pytest.approx(expected.bounds, abs=1e-6)
    assert imported.hausdorff_distance(expected) == pytest.approx(0, abs=1e-6)


@pytest.mark.parametrize('kind', ['gerber', 'excellon'])
def test_a_gerber_or_an_excellon_is_exported_as_black_fills_of_its_real_size(qt_app, tmp_path, kind):
    # a copper area with a hole and a pad (or a slot and a drill): they came out as thin pale green outlines
    copper = box(10, 20, 40, 30).difference(box(24, 24, 26, 26)).union(box(45, 20, 48, 23))
    path = _export(qt_app, [copper], tmp_path, kind=kind)

    text = path.read_text()
    assert 'fill="#000000"' in text
    assert 'fill="none"' not in text
    assert '#66cc99' not in text.lower()

    # read back as a Gerber: the same filled area with its hole, nothing added by a stroke
    imported = unary_union(svg_to_shapes(str(path), object_type='gerber', units='MM'))
    assert imported.symmetric_difference(copper).area == pytest.approx(0, abs=1e-3)


def _render(geometry, tmp_path, ftype):
    """Render a Geometry object with appIO.export_object_image() and return the path of the file."""
    return _render_kind(geometry, tmp_path, ftype, 'geometry')


def _render_kind(geometry, tmp_path, ftype, kind):
    """Render an object of the kind with appIO.export_object_image() and return the path of the file."""
    path = tmp_path / f'out.{ftype}'
    appIO(_app_with_geometry(geometry, kind)).export_object_image('geo', str(path), ftype, use_thread=False)
    return path


def test_png_export_draws_filled_dark_shapes_on_white_at_300_dpi_and_real_size(qt_app, tmp_path):
    # a 30 x 10 mm copper area with a hole: the image has to show the area filled, the hole and the space around it
    # white; it showed only a thin pale outline in an image of 113 x 38 pixels at 96 DPI
    copper = box(10, 20, 40, 30).difference(box(24, 24, 26, 26))
    path = _render([copper], tmp_path, 'png')

    assert path.read_bytes().startswith(b'\x89PNG')
    Image = pytest.importorskip('PIL.Image')
    image = Image.open(str(path))
    dpi = image.info.get('dpi')
    image = image.convert('L')
    width, height = image.size
    # the file records its DPI: the 30 x 10 mm area plus the margin of 1 mm on each side is shown at its real size
    assert dpi is not None
    assert dpi[0] == pytest.approx(300, abs=0.1)
    assert width / dpi[0] * 25.4 == pytest.approx(32.0, abs=0.2)
    assert height / dpi[1] * 25.4 == pytest.approx(12.0, abs=0.2)
    assert image.getpixel((width // 2, height // 2)) > 200                  # the hole
    assert image.getpixel((width // 4, height // 2)) < 50                   # the copper
    assert image.getpixel((1, 1)) > 200                                     # the margin


def test_png_export_keeps_a_narrow_clearance_between_gerber_traces_open(qt_app, tmp_path):
    # 2 traces with a clearance of 0.2 mm (2.4 pixels at 300 DPI): a stroke around the copper closed the clearance
    traces = [box(0, 0, 20, 1), box(0, 1.2, 20, 2.2)]
    path = _render_kind(traces, tmp_path, 'png', 'gerber')

    Image = pytest.importorskip('PIL.Image')
    image = Image.open(str(path)).convert('L')
    dpi = 300 / 25.4
    x = int((1 + 10) * dpi)                         # the margin of 1 mm, then the middle of the traces
    # the PNG has y down: the clearance at y = 1.1 mm is 1 mm + 2.2 mm - 1.1 mm below the top
    y = int((1 + 2.2 - 1.1) * dpi)
    assert image.getpixel((x, y)) > 200
    assert image.getpixel((x, int((1 + 0.5) * dpi))) < 50               # the upper trace


def test_png_export_shows_the_lines_of_a_geometry(qt_app, tmp_path):
    path = _render([LineString([(0, 0), (50, 0)]), LineString([(0, 0), (0, 20)])], tmp_path, 'png')

    Image = pytest.importorskip('PIL.Image')
    image = Image.open(str(path)).convert('L')
    assert image.getextrema()[0] < 50


def test_pdf_export_writes_a_pdf(qt_app, tmp_path):
    path = _render([box(10, 20, 40, 30)], tmp_path, 'pdf')

    assert path.read_bytes().startswith(b'%PDF')
