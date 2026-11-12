"""Merged from: test_fused_solid_paint: PaintGen: the progressive-plotting copper of the Gerber geometry goes through fused_solid().; test_fused_solid_ncc: NccGen: the Gerber normalization goes through fused_solid().; test_fused_solid_cutout: ToolCutOut freeform cutout: the Gerber geometry comes from fused_solid().
"""

import pytest
import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from fused_consumer_cases import area_diff, case_geometries
from tests.helpers_objects import app, gerber, _headless_qt


# ==============================================================================================================
# test_fused_solid_paint: PaintGen: the progressive-plotting copper of the Gerber geometry goes through fused_solid().
# ==============================================================================================================

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

    def test_overlapping_list_matches_the_area_branch(self, gerber, new_fn):
        obj = obj_with(gerber, 'overlapping_list')
        assert area_diff(legacy_area(obj), new_fn(obj)) < 1e-6
        assert area_diff(legacy_polygon_and_all(obj), new_fn(obj)) < 1e-6

    def test_self_intersecting_no_longer_raises_and_keeps_both_lobes(self, gerber, new_fn):
        geo = new_fn(obj_with(gerber, 'self_intersecting'))
        assert geo.area == pytest.approx(8.0 + 4.0)      # two bowtie triangles (4 each) + the 2 x 2 square


# ==============================================================================================================
# test_fused_solid_ncc: NccGen: the Gerber normalization goes through fused_solid().
# ==============================================================================================================

CASES = ['mixed_dark_clear_dark', 'overlapping_list']

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
        assert geo.area == pytest.approx(shapely.make_valid(Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])).area + 4.0)


# ==============================================================================================================
# test_fused_solid_cutout: ToolCutOut freeform cutout: the Gerber geometry comes from fused_solid().
# ==============================================================================================================

@pytest.fixture
def new_fn_fused_solid_cutout():
    from appPlugins.ToolCutOut import gerber_cutout_geometry
    return gerber_cutout_geometry

class TestNew_fused_solid_cutout:
    @pytest.mark.parametrize('name, convex', [('mixed_dark_clear_dark', False), ('overlapping_list', True)])
    def test_valid_polygonal(self, gerber, new_fn_fused_solid_cutout, name, convex):
        geo = new_fn_fused_solid_cutout(obj_with(gerber, name), convex)
        assert isinstance(geo, (Polygon, MultiPolygon)) and geo.is_valid
