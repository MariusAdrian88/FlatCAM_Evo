"""GerberObject.isolate(combine=True) must keep every pass.

The combine branch assigned geo_obj.solid_geometry inside the loop over the passes, so only the last pass survived:
-passes 2 produced only the outer ring, -passes 3 only the outermost one. The non-combine branch (one object per pass)
was correct. The tests use the headless fixtures of tests.helpers_objects.
"""

from unittest.mock import Mock

import pytest
from shapely.ops import unary_union

from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject

from tests.helpers_objects import app, _headless_qt, make_gerber  # noqa: F401,E402

DIA = 0.2
OVERLAP = 10  # percent


def run_isolate(app, gerber, **kwargs):
    """Calls isolate() and returns {object name: Geometry object built by the init callback}."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        assert kind == 'geometry'
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    gerber.isolate(dia=DIA, overlap=OVERLAP, iso_type=2, plot=False, **kwargs)
    return created


def wkb_set(geometries):
    return sorted(g.wkb_hex for g in geometries if not g.is_empty)


def expected_offsets(passes):
    """Offset of the centre line of every pass from the copper, as isolate() computes it."""
    return [DIA * ((2 * n + 1) / 2.0) - n * (OVERLAP / 100.0) * DIA for n in range(passes)]


@pytest.mark.parametrize('passes', [3])
def test_combined_result_is_the_union_of_the_separate_passes(app, passes):
    gerber = make_gerber(app, name='g')
    combined = run_isolate(app, gerber, passes=passes, combine=True, outname='combined')
    separate = run_isolate(app, gerber, passes=passes, combine=False)

    combined_geo = combined['combined'].solid_geometry
    separate_geo = [g for obj in separate.values() for g in obj.solid_geometry]

    assert len(separate) == passes
    assert wkb_set(combined_geo) == wkb_set(separate_geo)


def test_tool_data_of_the_combined_object_holds_all_passes(app):
    gerber = make_gerber(app, name='g')
    combined = run_isolate(app, gerber, passes=2, combine=True, outname='combined')['combined']
    assert combined.tools[1]['solid_geometry'] == combined.solid_geometry
    assert len(combined.solid_geometry) > 0


@pytest.mark.parametrize('combine, rest', [(False, False), (True, False), (True, True)])
def test_generated_tools_carry_the_isolation_centerline_marker(app, combine, rest):
    created = run_isolate(app, make_gerber(app, name='g'), passes=1 if rest else 2,
                          combine=combine, rest=rest)
    assert created
    for obj in created.values():
        assert obj.tools
        for tool in obj.tools.values():
            assert tool['data'].get('tools_iso_centerline') is True


def test_the_marker_of_the_real_isolation_output_is_consumed_by_milling(app):
    # the real output carries the key Milling reads: a would-be second offset is dropped
    from tests.test_milling_single_geometry_tools import milling_job, xy_cuts

    created = run_isolate(app, make_gerber(app, name='g'), passes=1, combine=True)
    (obj,) = created.values()
    data = dict(obj.tools[1]['data'], tools_mill_offset_type=1)
    __, job = milling_job(1, multigeo=False, from_tcl=True, data=data)
    __, plain = milling_job(1, multigeo=False, from_tcl=True, data=dict(tools_mill_offset_type=0))
    assert job != 'fail' and plain != 'fail'
    assert xy_cuts(job.tools[1]['gcode']) == xy_cuts(plain.tools[1]['gcode'])
