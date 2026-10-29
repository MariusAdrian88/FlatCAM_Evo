"""Invert Gerber: the geometry core (InvertGerberGen)."""

import pytest
from shapely import make_valid
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from appPlugins.ToolInvertGerber.InvertGerberGen import (
    apertures_from_polygons,
    invert_geometry,
    reference_area,
)


FIXED_16 = {'gerber_circle_steps_mode': 'fixed', 'gerber_circle_steps': 16}


def _area(polys):
    return unary_union(polys).area if polys else 0.0


def test_result_is_the_margin_box_minus_the_copper():
    copper = [box(0, 0, 1, 1), box(3, 3, 4, 4)]
    result = invert_geometry(copper, (0, 0, 4, 4), 1.0, FIXED_16, 'mitre')
    assert all(p.geom_type == 'Polygon' for p in result)
    assert _area(result) == pytest.approx(36.0 - 2.0)


def test_overlapping_and_invalid_copper_is_unioned_and_repaired():
    bow_tie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    result = invert_geometry([bow_tie, box(1, 1, 3, 3), box(1, 1, 3, 3)], (0, 0, 3, 3), 0.0, FIXED_16, 'mitre')
    copper = unary_union([make_valid(bow_tie), box(1, 1, 3, 3)])
    assert _area(result) == pytest.approx(9.0 - copper.area, abs=1e-6)


class _Obj:
    def __init__(self, kind, solid, units='MM'):
        self.kind = kind
        self.solid_geometry = solid
        self.tools = {}
        self.multigeo = False
        self.obj_options = {'units': units}


def test_reference_area_box_and_exact_modes():
    ring = LineString([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    outline = _Obj('geometry', [ring])
    assert reference_area(outline, 'exact', 16).area == pytest.approx(100.0)
    tri = _Obj('gerber', [Polygon([(0, 0), (4, 0), (0, 4)])])
    assert reference_area(tri, 'box', 16).area == pytest.approx(16.0)
    assert reference_area(tri, 'exact', 16).area == pytest.approx(8.0)


# ------------------------------------------------------------------------------------------------ plugin controller
@pytest.fixture(scope='module')
def fc():
    import os
    import sys
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6 import QtWidgets
    from appMain import App
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    return App(qapp=qapp, headless=True)


def _invert(fc, name):
    import time
    from PyQt6 import QtWidgets
    plugin = fc.invert_tool
    plugin.run(toggle=False)
    plugin.ui.gerber_combo.setCurrentText(name)
    plugin.ui.invert_btn.click()
    end = time.time() + 10
    while time.time() < end and (plugin._busy or fc.projects.is_busy()):
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        QtWidgets.QApplication.processEvents()


@pytest.mark.slow
def test_invert_makes_a_new_object_and_leaves_the_source_alone(fc, monkeypatch):
    from appObjects.AppObjectTemplate import FlatCAMObj
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    for name in list(fc.collection.get_names()):
        fc.collection.delete_by_name(name)

    def init(obj, app):
        obj.solid_geometry = [box(2, 2, 4, 4)]
        obj.tools = apertures_from_polygons(obj.solid_geometry)
    fc.app_obj.new_object('gerber', 'src', init, plot=False)
    before = list(fc.collection.get_by_name('src').solid_geometry)
    _invert(fc, 'src')
    assert sorted(fc.collection.get_names()) == ['src', 'src_inverted']
    assert fc.collection.get_by_name('src').solid_geometry == before
    assert fc.collection.get_by_name('src_inverted').solid_geometry
