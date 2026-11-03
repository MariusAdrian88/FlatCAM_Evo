"""Isolation passes buffered ahead in parallel threads (camlib.PassBuffers) give the same paths as one pass at a time.

Geometry.isolation_geometry() buffers the features of a pass one by one in a Python loop, and the passes were made one
after the other, on one processor core. PassBuffers buffers a batch of passes in threads (the vectorized Shapely calls
release the GIL). The result must be identical, so the G-code does not change. The object tests use the headless
fixtures of tests.helpers_objects.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from appCommon.Common import GracefulException
from appObjects.GeometryObject import GeometryObject
from appPlugins.ToolIsolation import ToolIsolation, IsolationGen
from camlib import PassBuffers, flatten_shapely_geometry

from tests.helpers_objects import app, _headless_qt, make_gerber  # noqa: F401,E402

DIA = 0.2
OVERLAP = 10  # percent
OFFSETS = [0.1 * ((2 * nr + 1) / 2.0) - nr * 0.1 * 0.1 for nr in range(20)]


def features():
    # pads and traces close to each other, so the buffers of the passes merge
    geo = [Point(x * 1.3, y * 1.1).buffer(0.4, 16) for x in range(12) for y in range(9)]
    geo += [LineString([(0, y * 1.1), (14, y * 1.1 + 0.3)]).buffer(0.1, 16) for y in range(9)]
    geo.append(box(20, 0, 26, 3).difference(box(21, 1, 25, 2)))
    return geo


def one_pass(geo, offset, quad_segs=16):
    """The loop of Geometry.isolation_geometry() over the features."""
    return unary_union([g.buffer(offset, quad_segs, join_style='round') for g in geo])


def wkb_list(geometries):
    return [g.wkb for g in flatten_shapely_geometry(geometries)]


@pytest.fixture
def three_threads():
    """3 threads (batches of 3 passes); yields the mock that counts the passes taken from PassBuffers."""
    real_get = PassBuffers.get
    with patch('camlib.os.cpu_count', return_value=3), \
            patch.object(PassBuffers, 'get', autospec=True, side_effect=real_get) as get:
        yield get


# ---------------------------------------------------------------------------------------------------------------
# PassBuffers
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('workers', [1, 3])
def test_same_geometry_as_one_pass_at_a_time(workers):
    geo = features()
    buffers = PassBuffers(geo, OFFSETS, 16, workers=workers)
    for nr_pass, offset in enumerate(OFFSETS):
        assert buffers.get(nr_pass).wkb == one_pass(geo, offset).wkb, 'pass %d' % nr_pass


def test_abort_is_raised_in_the_caller():
    buffers = PassBuffers(features(), OFFSETS, 16, workers=4, abort=lambda: True)
    with pytest.raises(GracefulException):
        buffers.get(0)


# ---------------------------------------------------------------------------------------------------------------
# GerberObject.isolate() (Tcl isolate)
# ---------------------------------------------------------------------------------------------------------------

def run_isolate(app, gerber, **kwargs):
    """Calls isolate() and returns {object name: Geometry object built by the init callback}."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    gerber.isolate(dia=DIA, overlap=OVERLAP, iso_type=2, milling_type='cl', plot=False, **kwargs)
    return created


@pytest.mark.parametrize('combine', [False])
def test_isolate_makes_the_same_paths_as_one_pass_at_a_time(app, three_threads, combine):
    app.options['gerber_circle_steps_mode'] = 'fixed'     # the reference passes use the fixed steps of the object
    gerber = make_gerber(app, name='g')
    passes = 7
    expected = []
    for nr in range(passes):
        offset = DIA * ((2 * nr + 1) / 2.0) - nr * (OVERLAP / 100.0) * DIA
        # milling_type='cl' (climb): the envelope is not inverted
        expected += wkb_list(gerber.generate_envelope(offset, 0, geometry=gerber.solid_geometry, nr_passes=nr))
    assert three_threads.call_count == 0

    created = run_isolate(app, gerber, passes=passes, combine=combine)

    assert three_threads.call_count == passes
    assert len(created) == (1 if combine else passes)
    assert [w for obj in created.values() for w in wkb_list(obj.solid_geometry)] == expected


# ---------------------------------------------------------------------------------------------------------------
# Isolation Plugin
# ---------------------------------------------------------------------------------------------------------------

def make_tool(app):
    tool = SimpleNamespace(app=app, decimals=4, validation_status=True, temp_shapes=Mock())
    tool.generate_envelope = lambda *args, **kwargs: ToolIsolation.generate_envelope(tool, *args, **kwargs)
    return tool


def tools_storage(passes, milling_type):
    return {1: {'tooldia': DIA, 'data': {
        'tools_iso_isotype': 'full', 'tools_iso_passes': passes, 'tools_iso_overlap': OVERLAP,
        'tools_iso_milling_type': milling_type, 'tools_mill_offset_type': 0, 'tools_mill_offset_value': 0.0,
        'tools_mill_tool_shape': 0}}}


def plugin_one_pass_at_a_time(tool, gerber, passes, mill_dir, negative_dia=False):
    expected = []
    for nr in range(passes):
        offset = DIA * ((2 * nr + 1) / 2.0000001) - nr * (OVERLAP / 100.0) * DIA
        if negative_dia:
            offset = -offset
        expected += wkb_list(ToolIsolation.generate_envelope(tool, gerber, offset, mill_dir,
                                                             geometry=gerber.solid_geometry, nr_passes=nr))
    return expected


@pytest.mark.parametrize('negative_dia', [False, True])
def test_plugin_combined_passes_are_the_same_as_one_pass_at_a_time(app, three_threads, negative_dia):
    app.options['gerber_circle_steps_mode'] = 'fixed'     # the reference passes use the fixed steps of the object
    gerber = make_gerber(app, name='g')
    tool = make_tool(app)
    tool.new_object = Mock()
    passes = 7
    expected = plugin_one_pass_at_a_time(tool, gerber, passes, 0, negative_dia=negative_dia)

    storage = tools_storage(passes, 'cl')
    ToolIsolation.combined_normal(tool, iso_obj=gerber, iso2geo=gerber.solid_geometry, tools_storage=storage,
                                  lim_area=None, sel_tools=[1], iso_except=False, extra_passes=0,
                                  negative_dia=negative_dia, plot=False, prog_plot='normal')

    assert three_threads.call_count == passes
    assert wkb_list(storage[1]['solid_geometry']) == expected
    tool.new_object.assert_called_once()


# ---------------------------------------------------------------------------------------------------------------
# Edge cases of the isolation geometry
# ---------------------------------------------------------------------------------------------------------------

def test_isolation_geometry_edge_cases(app):
    gerber = make_gerber(app, name='g')
    # an empty selection is not the whole object
    assert gerber.isolation_geometry(0.1, geometry=[]) == []
    assert gerber.isolation_buffers([0.1], geometry=[]).get(0).is_empty
    assert gerber.isolation_geometry(0.1, geometry=None)
    # an unsupported isolation type fails
    assert gerber.isolation_geometry(0.1, iso_type=7) == 'fail'
    assert IsolationGen.orient_envelope('fail', True, Mock(), Mock()) == 'fail'
    # the circle steps count the whole circle
    for steps in (16, 32):
        gerber.geo_steps_per_circle = steps
        pad = [box(0, 0, 0.01, 0.01)]
        rings = [gerber.isolation_geometry(0.1, geometry=pad, iso_type=0)[0],
                 gerber.isolation_geometry(0.1, geometry=pad, iso_type=0,
                                           buffers=gerber.isolation_buffers([0.1], pad))[0]]
        for ring in rings:
            # 4 rounded corners of steps / 4 segments, the ring closes on its first point
            assert len(ring.coords) == 4 * (steps // 4 + 1) + 1
