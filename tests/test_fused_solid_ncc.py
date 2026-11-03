"""NccGen: the Gerber normalization goes through fused_solid()."""

import pytest
import shapely
from shapely.geometry import MultiPolygon, Polygon

from fused_consumer_cases import area_diff, case_geometries
from tests.helpers_objects import app, gerber, _headless_qt  # noqa: F401,E402

CASES = ['mixed_dark_clear_dark', 'overlapping_list']


def obj_with(gerber, name):
    gerber.solid_geometry = case_geometries(gerber.solid_geometry)[name]
    return gerber


@pytest.fixture
def new_fn():
    from appPlugins.ToolNCC.NccGen import ncc_gerber_copper
    return ncc_gerber_copper


class TestNew:
    @pytest.mark.parametrize('name', CASES)
    def test_result_is_valid_polygonal(self, gerber, new_fn, name):
        geo = new_fn(obj_with(gerber, name))
        assert isinstance(geo, (Polygon, MultiPolygon)) and geo.is_valid

    def test_clear_polarity_is_kept(self, gerber, new_fn):
        # the composed geometry already has the clear area removed; the union of it must not fill it again
        geo = new_fn(gerber)
        assert area_diff(geo, gerber.solid_geometry) < 1e-6

    def test_self_intersecting_is_repaired(self, gerber, new_fn):
        obj = obj_with(gerber, 'self_intersecting')
        geo = new_fn(obj)
        assert geo.is_valid
        # both lobes of the bowtie survive (buffer(0) would keep only one of them)
        assert geo.area == pytest.approx(shapely.make_valid(Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])).area + 4.0)
