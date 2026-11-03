"""Drilling tool G-code with a laser preprocessor: the laser is OFF for every travel.

For the 'laser' preprocessors the spindle code is the laser ON command (e.g. 'M3 S800'). The Drilling tool
(CNCjob.excellon_tool_gcode_gen()) wrote it after the start / toolchange block, so the travel to the first hole of a
tool was made with the laser ON. On a controller without the GRBL laser mode ($32=0, a PWM spindle output) this burns
a line across the board. The laser is now ON only at the holes (down code), as in the Tcl drillcncjob path and in the
Milling tool. The G-code of the other preprocessors does not change.

Run from the repository root:
    python -m pytest tests/test_laser_drill_travel.py -q
"""
import re

import pytest
from shapely.geometry import Point

from camlib import CNCjob
from defaults import AppDefaults
from test_laser_safety import LASER_PREPROCESSORS, laser_app

# 2 tools with 2 holes each: travels from the start point, between the holes and after the tool change
DRILLS = {1: (0.8, [(5.0, 5.0), (8.0, 2.0)]), 2: (1.2, [(20.0, 10.0), (25.0, 15.0)])}

LASER_OFF_RE = re.compile(r'^(M0?5|M107|M106 S0)\b')
LASER_ON_RE = re.compile(r'^(M0?[34]|M106)\b')


def drilling_gcode(pp_name, toolchange, min_power=0):
    """
    The G-code of the tool blocks, made as the Drilling tool does: without toolchange 1 call with all the holes, with
    toolchange 1 call per tool (the last point of a tool is the first point of the next one).
    """
    app = laser_app(pp_name)
    job = CNCjob(app, pp_excellon_name=pp_name)
    job.obj_options = {'name': 'job', 'type': 'Excellon', 'xmin': 0, 'xmax': 30, 'ymin': 0, 'ymax': 20}
    job.coords_decimals = 4
    job.fr_decimals = 2

    tools = {}
    for tid, (dia, pts) in DRILLS.items():
        data = dict(AppDefaults.factory_defaults)
        data.update(tools_drill_ppname_e=pp_name, tools_drill_min_power=min_power, tools_drill_spindlespeed=800,
                    tools_drill_dwell=True, tools_drill_dwelltime=1.5)
        tools[tid] = {'tooldia': dia, 'drills': [Point(p) for p in pts], 'data': data}
    # read by the toolchange code of the preprocessors
    job.tools = {tid: {'nr_drills': len(t['drills'])} for tid, t in tools.items()}

    blocks = []
    if toolchange:
        first_pt = (0.0, 0.0)
        for tid in tools:
            job.tool = tid
            gcode, first_pt, __ = job.excellon_tool_gcode_gen(tid, tools[tid]['drills'], tools, first_pt,
                                                              is_first=tid == 1, is_last=tid == 2, opt_type='R',
                                                              toolchange=True)
            blocks.append(gcode)
    else:
        job.tool = 1
        holes = [pt for t in tools.values() for pt in t['drills']]
        gcode, __, __ = job.excellon_tool_gcode_gen(1, holes, tools, (0.0, 0.0), is_first=True, is_last=True,
                                                    opt_type='R', toolchange=False)
        blocks.append(gcode)

    # the travel line to each hole, as the preprocessor writes it
    travels = {job.doformat2(job.pp_excellon.rapid_code, x=x, y=y)
               for __, pts in DRILLS.values() for x, y in pts}
    return blocks, travels


def laser_state(line):
    """True for a laser ON line, False for a laser OFF line (the 'laser OFF' lines of Min Power too), else None."""
    if 'laser OFF' in line or LASER_OFF_RE.match(line):
        return False
    if LASER_ON_RE.match(line):
        return True
    return None


@pytest.mark.parametrize('pp_name,toolchange', [(pp, i % 2 == 1) for i, pp in enumerate(LASER_PREPROCESSORS)])
def test_the_laser_is_off_for_every_travel_and_on_at_each_hole(pp_name, toolchange):
    blocks, travels = drilling_gcode(pp_name, toolchange, 250)

    holes = 0
    for gcode in blocks:
        laser_on = False
        after_travel = False
        for line in gcode.splitlines():
            line = line.strip()
            state = laser_state(line)
            if state is not None:
                if after_travel:
                    # the hole: the down code turns the laser ON
                    assert state is True, gcode
                    holes += 1
                    after_travel = False
                laser_on = state
            elif line in travels:
                assert not laser_on, 'the laser is ON for the travel %r:\n%s' % (line, gcode)
                after_travel = True
    assert holes == sum(len(pts) for __, pts in DRILLS.values())


@pytest.mark.parametrize('toolchange', [False, True])
def test_grbl_laser_does_not_turn_the_laser_on_before_the_first_travel_of_a_tool(toolchange):
    blocks, travels = drilling_gcode('GRBL_laser', toolchange)

    assert len(blocks) == (2 if toolchange else 1)
    for gcode in blocks:
        lines = [line for line in gcode.splitlines() if line]
        first_travel = next(i for i, line in enumerate(lines) if line in travels)
        # before the fix: [..., 'M3 S800'] (laser at full power for the travel)
        assert 'M3 S800' not in lines[:first_travel]
        assert lines[first_travel - 1] == 'M5'
        assert lines[first_travel + 1] == 'M3 S800'


@pytest.mark.parametrize('pp_name,toolchange', [('default', False), ('GRBL_11', True)])
def test_other_preprocessors_still_start_the_spindle_before_the_first_travel(pp_name, toolchange):
    blocks, travels = drilling_gcode(pp_name, toolchange)

    assert len(blocks) == (2 if toolchange else 1)
    for gcode in blocks:
        lines = [line for line in gcode.splitlines() if line]
        first_travel = next(i for i, line in enumerate(lines) if line in travels)
        assert lines[first_travel - 2:first_travel] == ['M03 S800', 'G4 P1.5']

