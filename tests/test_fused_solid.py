"""Paint and NCC retain composed copper, overlapping features and both lobes of invalid polygons."""

import pytest
from shapely.ops import unary_union
from fused_consumer_cases import area_diff, case_geometries
from tests.helpers_objects import app, gerber, _headless_qt

def obj_with(gerber, name):
    gerber.solid_geometry = case_geometries(gerber.solid_geometry)[name]
    return gerber

@pytest.fixture
def new_fn():
    from appPlugins.ToolPaint.PaintGen import gerber_copper_for_paint
    return gerber_copper_for_paint

class TestNew:

    def test_overlapping_list_matches_the_area_branch(self, gerber, new_fn):
        obj = obj_with(gerber, 'overlapping_list')
        assert area_diff(unary_union(obj.solid_geometry), new_fn(obj)) < 1e-6

    def test_self_intersecting_no_longer_raises_and_keeps_both_lobes(self, gerber, new_fn):
        geo = new_fn(obj_with(gerber, 'self_intersecting'))
        assert geo.area == pytest.approx(8.0 + 4.0)      # two bowtie triangles (4 each) + the 2 x 2 square


@pytest.fixture
def new_fn_fused_solid_ncc():
    from appPlugins.ToolNCC.NccGen import ncc_gerber_copper
    return ncc_gerber_copper

class TestNew_fused_solid_ncc:

    def test_clear_polarity_is_kept(self, gerber, new_fn_fused_solid_ncc):
        # the composed geometry already has the clear area removed; the union of it must not fill it again
        geo = new_fn_fused_solid_ncc(gerber)
        assert area_diff(geo, gerber.solid_geometry) < 1e-6

    def test_self_intersecting_is_repaired(self, gerber, new_fn_fused_solid_ncc):
        obj = obj_with(gerber, 'self_intersecting')
        geo = new_fn_fused_solid_ncc(obj)
        assert geo.is_valid
        # both lobes of the bowtie survive (buffer(0) would keep only one of them)
        assert geo.area == pytest.approx(12.0)
