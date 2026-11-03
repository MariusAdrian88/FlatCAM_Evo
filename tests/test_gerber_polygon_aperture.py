"""The regular polygon aperture P (Gerber Layer Format Specification 2026.05, 4.4.5 Polygon).

"outer_diameter: Diameter of the circle circumscribing the regular polygon, i.e. the circle through the polygon
vertices." "rotation: The rotation angle, in degrees counterclockwise. With rotation angle zero there is a vertex
on the positive X-axis through the aperture center."

So every vertex is on the circle of the outer diameter around the flash point, and 1 vertex is at the rotation
angle. The rotation is about the aperture center, also for an odd number of vertices.
"""

import math

import pytest

from appParsers.ParseGerber import Gerber
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402

FLASH = (10.0, 5.0)


def _flash_polygon(n, rotation=None, diam=2.0):
    ad = '%%ADD10P,%sX%d%s*%%' % (diam, n, '' if rotation is None else 'X%s' % rotation)
    g = Gerber(make_app())
    g.parse_lines(['%FSLAX24Y24*%', '%MOMM*%', ad, 'D10*', 'X100000Y50000D03*', 'M02*'])
    records = g.tools[10]['geometry']
    assert len(records) == 1
    return records[0]['solid']


@pytest.mark.parametrize('n, rotation', [(6, 45)])
def test_vertices_are_on_the_circumscribed_circle_around_the_flash_point(n, rotation):
    ply = _flash_polygon(n, rotation)
    vertices = list(ply.exterior.coords)[:-1]
    assert len(vertices) == n
    for x, y in vertices:
        assert math.hypot(x - FLASH[0], y - FLASH[1]) == pytest.approx(1.0, abs=1e-9)

    # 1 vertex is at the rotation angle (counterclockwise from the positive X-axis through the center)
    angle = math.radians(rotation or 0.0)
    expected = (FLASH[0] + math.cos(angle), FLASH[1] + math.sin(angle))
    assert min(math.hypot(x - expected[0], y - expected[1]) for x, y in vertices) == pytest.approx(0.0, abs=1e-9)
