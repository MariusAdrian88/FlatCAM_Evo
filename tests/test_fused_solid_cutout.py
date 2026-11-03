"""ToolCutOut freeform cutout: the Gerber geometry comes from fused_solid()."""

import pytest
from shapely.geometry import MultiPolygon, Polygon

from fused_consumer_cases import case_geometries
from tests.helpers_objects import app, gerber, _headless_qt  # noqa: F401,E402


def obj_with(gerber, name):
    gerber.solid_geometry = case_geometries(gerber.solid_geometry)[name]
    return gerber


@pytest.fixture
def new_fn():
    from appPlugins.ToolCutOut import gerber_cutout_geometry
    return gerber_cutout_geometry


class TestNew:
    @pytest.mark.parametrize('name, convex', [('mixed_dark_clear_dark', False), ('overlapping_list', True)])
    def test_valid_polygonal(self, gerber, new_fn, name, convex):
        geo = new_fn(obj_with(gerber, name), convex)
        assert isinstance(geo, (Polygon, MultiPolygon)) and geo.is_valid

