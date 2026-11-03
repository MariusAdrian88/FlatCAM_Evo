"""The final X-Y move to the end position must not be made below the Travel Z.

end_code() of the preprocessors went to End Z first and then made the rapid X-Y move to the End X-Y position. With an
End Z lower than the Travel Z, that rapid move went over the workpiece and the clamps below the Travel Z (Line_xyz went
down while it moved). Now the X-Y move is made at the Travel Z and the tool goes down to End Z after it. With an End Z
at or above the Travel Z (the defaults are 15 and 2) the output is the same as before.
"""

import importlib
import re

import pytest

from appPreProcessor import PreProc
from camlib import AttrDict

PREPROCESSORS = ['Berta_CNC', 'Check_points', 'default', 'Default_no_M6', 'GRBL_11', 'GRBL_11_no_M6', 'GRBL_laser_z',
                 'ISEL_CNC', 'Line_xyz', 'Marlin', 'Marlin_laser_z', 'NCCAD9', 'Repetier', 'Toolchange_Manual',
                 'Toolchange_Probe_MACH3']

TRAVEL_Z = 2.0
LAST_POINT = (3.0, 4.0)     # where the job ends, at the Travel Z
END_XY = (10.0, 20.0)


def preprocessor(name):
    return getattr(importlib.import_module('preprocessors.' + name), name)()


def params(z_end, z_move=TRAVEL_Z, xy_end=END_XY, x=LAST_POINT[0], y=LAST_POINT[1]):
    # camlib calls end_code() with x, y = the last point (Geometry) or 0, 0 (Excellon)
    return AttrDict(z_end=z_end, z_move=z_move, xy_end=xy_end, x=x, y=y, fr_decimals=4, coords_decimals=4,
                    feedrate_rapid=1500.0, _bed_offset_x=0.0, _bed_offset_y=0.0, _bed_skew_x=0.0, _bed_skew_y=0.0,
                    _bed_limit_x=100.0, _bed_limit_y=100.0)


def rapid_moves(gcode, start):
    """The rapid moves of the G-code as (from, to) points (x, y, z), starting at the start point."""
    moves = []
    pos = start
    for line in gcode.splitlines():
        if not re.match(r'G0?0\s', line):
            continue
        axes = dict(re.findall(r'([XYZ])(-?\d+(?:\.\d+)?)', line))
        new = tuple(float(axes[a]) if a in axes else pos[i] for i, a in enumerate('XYZ'))
        moves.append((pos, new))
        pos = new
    return moves


@pytest.mark.parametrize('name', PREPROCESSORS[::5])
def test_end_xy_move_is_not_made_below_the_travel_z(name):
    gcode = preprocessor(name).end_code(params(z_end=0.5))
    moves = rapid_moves(gcode, LAST_POINT + (TRAVEL_Z,))

    xy_moves = [(a, b) for a, b in moves if a[:2] != b[:2]]
    assert xy_moves, gcode
    for a, b in xy_moves:
        assert min(a[2], b[2]) >= TRAVEL_Z, '%s: X-Y move from %s to %s\n%s' % (name, a, b, gcode)
    # parked at the end position and at End Z
    assert moves[-1][1] == END_XY + (0.5,), gcode


@pytest.mark.parametrize('name', ['default', 'Line_xyz'])
def test_low_end_z_moves(name):
    expected = {
        'default': 'G00 Z2.0000\nG00 X10.0000 Y20.0000\nG00 Z0.5000\n',
        'Marlin': 'G0 Z2.0000 F1500.0000\nG0 X10.0000 Y20.0000 F1500.0000\nG0 Z0.5000 F1500.0000\n',
        'NCCAD9': 'G00 Z2.0000 F9000.0\nG0 X10.0000 Y20.0000 F9000.0\nG00 Z0.5000 F9000.0\n\nG77',
        'Line_xyz': 'G00 X10.0000 Y20.0000 Z2.0000\nG00 X10.0000 Y20.0000 Z0.5000',
    }[name]
    gcode = preprocessor(name).end_code(params(z_end=0.5))
    assert gcode.startswith(expected), gcode


def test_line_xyz_without_end_position_goes_down_at_the_end():
    # Excellon jobs call end_code() with x=0, y=0, so Line_xyz moves to X0 Y0 at the end
    gcode = preprocessor('Line_xyz').end_code(params(z_end=0.5, xy_end=None, x=0.0, y=0.0))
    assert gcode == 'G00 X0.0000 Y0.0000 Z2.0000\nG00 X0.0000 Y0.0000 Z0.5000'

