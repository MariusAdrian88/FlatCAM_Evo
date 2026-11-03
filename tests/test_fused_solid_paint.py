"""PaintGen: the progressive-plotting copper of the Gerber geometry goes through fused_solid()."""

import pytest
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from fused_consumer_cases import area_diff, case_geometries
from tests.helpers_objects import app, gerber, _headless_qt  # noqa: F401,E402


def legacy_polygon_and_all(obj):
    """Paint 'normal' and 'all' workers, tools_paint_plotting == 'progressive' (the result was stored on the object)."""
    if isinstance(obj.solid_geometry, list):
        return MultiPolygon(obj.solid_geometry).buffer(0)
    return obj.solid_geometry.buffer(0)


def legacy_area(obj):
    """Paint 'area' worker, progressive."""
    return unary_union(obj.solid_geometry).buffer(0)


def obj_with(gerber, name):
    gerber.solid_geometry = case_geometries(gerber.solid_geometry)[name]
    return gerber


# 'multipolygon' and 'single_polygon' only: MultiPolygon(list_of_overlapping_polygons) is itself invalid input
VALID = ['mixed_dark_clear_dark', 'single_polygon', 'multipolygon']


@pytest.fixture
def new_fn():
    from appPlugins.ToolPaint.PaintGen import gerber_copper_for_paint
    return gerber_copper_for_paint


class TestNew:
    @pytest.mark.parametrize('name', ['mixed_dark_clear_dark', 'multipolygon'])
    def test_valid_polygonal(self, gerber, new_fn, name):
        geo = new_fn(obj_with(gerber, name))
        assert isinstance(geo, (Polygon, MultiPolygon)) and geo.is_valid

    def test_overlapping_list_matches_the_area_branch(self, gerber, new_fn):
        obj = obj_with(gerber, 'overlapping_list')
        assert area_diff(legacy_area(obj), new_fn(obj)) < 1e-6
        assert area_diff(legacy_polygon_and_all(obj), new_fn(obj)) < 1e-6

    def test_self_intersecting_no_longer_raises_and_keeps_both_lobes(self, gerber, new_fn):
        geo = new_fn(obj_with(gerber, 'self_intersecting'))
        assert geo.area == pytest.approx(8.0 + 4.0)      # two bowtie triangles (4 each) + the 2 x 2 square
