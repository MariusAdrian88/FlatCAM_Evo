"""The clearing direction through the shared engine: the kernels get it, the laser lines do not, and the final G-code
cuts every ring of a polygon with a hole (and every depth) in the chosen direction, with Connect off and on."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shapely.geometry import Point, box  # noqa: E402

from appPlugins.Common.clearing import engine  # noqa: E402
from appPlugins.Common.clearing.methods import NCC_METHODS, PAINT_METHODS  # noqa: E402
from test_clearing_paths import make_geometry, turn  # noqa: E402
from test_cncjob_export_gcode import gcode_tokens, multi_geo_gcode  # noqa: E402

CENTER = (10.0, 10.0)
AREA = box(0, 0, 20, 20).difference(Point(CENTER).buffer(4, quad_segs=16))


def fill(method, direction, connect, **kwargs):
    geometry = make_geometry()
    geometry.app.units = 'MM'
    geometry.app.options = {'global_tolerance': 0}
    kernels = engine.FillKernels(shrink=geometry.clear_polygon_shrink, seed=geometry.clear_polygon_seed,
                                 lines=geometry.clear_polygon_lines, crosshatch=geometry.clear_polygon_crosshatch,
                                 fill_lines=getattr(geometry, 'fill_with_lines', None))
    result = engine.fill_polygon(AREA, 1.0, method, 0.5, connect, True, False, kernels, NCC_METHODS,
                                 lambda dia, kind: 32, direction=direction, **kwargs)
    return list(result.get_objects())


def rings(paths):
    return [np.asarray(path.coords) for path in paths if len(path.coords) > 3 and path.coords[0] == path.coords[-1]]


def role(ring):
    """'outer' for a ring of the outer boundary of the area, 'hole' for a ring around the round hole."""
    dist = np.hypot(*(ring - CENTER).T)
    return 'hole' if dist.max() < 7.0 else 'outer' if dist.min() > 7.0 else None


def ring_runs(segment, ring):
    """The run of a cut along the ring: from its start point to the next time the cut is there (a ring is closed)."""
    at = np.flatnonzero(np.all(np.isclose(segment, ring[0], atol=2e-4), axis=1))
    return [segment[first:second + 1] for first, second in zip(at[::2], at[1::2])]


@pytest.mark.parametrize('connect', [False, True])
@pytest.mark.parametrize('method', [0, 1], ids=['standard', 'seed'])
@pytest.mark.parametrize('direction', ['climb', 'conv'])
def test_every_ring_is_cut_in_the_direction_at_every_depth(direction, method, connect):
    paths = fill(method, direction, connect)
    expected = {'outer': 1 if direction == 'climb' else -1}
    expected['hole'] = -expected['outer']
    # the rings as the kernel made them, before the join
    kernel_rings = [ring for ring in rings(fill(method, direction, False)) if role(ring) is not None]
    assert len(kernel_rings) >= 2
    __, gcode = multi_geo_gcode(paths, data=dict(
        tools_mill_keep_direction=True, tools_mill_multidepth=True, tools_mill_depthperpass=0.1, tools_mill_cutz=-0.2))
    tokens, cuts, low_rapids = gcode_tokens(gcode)
    assert low_rapids == []
    found = 0
    for ring in kernel_rings:
        for cut in cuts:
            for run in ring_runs(np.asarray(cut), ring):
                assert turn(run) == expected[role(ring)], (direction, method, connect, role(ring))
                found += 1
    # every ring is cut at both depths
    assert found == 2 * len(kernel_rings)


def test_direction_none_calls_the_kernels_as_before_and_the_laser_lines_get_none():
    seen = {}

    def kernel(pol, tooldia, **kwargs):
        seen.update(kwargs)
        return None

    def laser(*args):
        seen['laser_args'] = len(args)
        return None

    kernels = engine.FillKernels(shrink=kernel, seed=kernel, lines=kernel, crosshatch=kernel, fill_lines=kernel)
    for direction in ('none', 'climb'):
        for method in range(5):
            seen.clear()
            engine.fill_polygon(AREA, 1.0, method, 0.5, False, True, False, kernels, PAINT_METHODS,
                                lambda dia, kind: 32, laser=laser, direction=direction)
            has = 'direction' in seen
            if PAINT_METHODS.kernel(method) == 'laser_lines':
                assert not has and seen['laser_args'] == 13
            else:
                assert has is (direction == 'climb')
