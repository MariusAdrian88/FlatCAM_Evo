import math
import os
import re
import sys
import time

import pytest
import shapely
import shapely.affinity

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appParsers.ParseGCode import parse_gcode, split_tool_blocks, transform_gcode


def _cut_lines(res, tool=1):
    return [g['geom'] for g in res['tools'][tool]['gcode_parsed'] if g['kind'][0] == 'C']


def _cut_bounds(res, tool=1):
    xs = [b for g in _cut_lines(res, tool) for b in g.bounds]
    return min(xs[0::4]), min(xs[1::4]), max(xs[2::4]), max(xs[3::4])


def _end(res, tool=1):
    """Last point of the last cut line of the tool."""
    return tuple(_cut_lines(res, tool)[-1].coords[-1])


def _xvals(text):
    """X values of the plain lines the program runs."""
    from appParsers.ParseGCode import expand_program
    lines, warns = expand_program(text)
    return [float(re.search(r'X(\S+)', t).group(1)) for _, t in lines if 'X' in t], warns


# ---------------------------------------------------------------- split_tool_blocks

def test_split_partition_is_exact_and_keeps_crlf():
    text = "G21\r\nT1\r\nG1 X1 Y1\r\nT2\r\nG1 X2\r\n(T3 comment)\r\nG1 X3"
    blocks = split_tool_blocks(text)
    assert ''.join(b['gcode'] for b in blocks) == text
    assert [b['tool'] for b in blocks] == [1, 2]
    assert blocks[0]['gcode'].startswith("G21\r\nT1\r\n")
    assert '\r\n' in blocks[1]['gcode']
    # no T word outside the comments: 1 block
    text = "G0 X1\nG1 X2 (T9 only a comment)\n"
    assert split_tool_blocks(text) == [{'tool': None, 'gcode': text}]
    # the same tool twice with another between: 3 blocks
    text = "T1\nG1 X1\nT2\nG1 X2\nT1\nG1 X3\nT1\nG1 X4\n"
    blocks = split_tool_blocks(text)
    assert [b['tool'] for b in blocks] == [1, 2, 1]
    assert ''.join(b['gcode'] for b in blocks) == text


# ---------------------------------------------------------------- generic files, modes, units

def test_generic_file_modes_and_units():
    text = (
        "%\nO1000\nN10 g21 g90 (metric)\ng0x0y0z5\ng1 z-1 f100 ; plunge\ng1x10y0\nG54\nx10 y10\nG90\nx0 y10\n"
        "g0 z5\nM30\n%\n"
    )
    res = parse_gcode(text)
    assert res['units'] == 'MM'
    assert res['type'] == 'Geometry'
    assert res['flatcam'] is False
    assert res['motion_count'] == 6
    assert _cut_bounds(res) == (0.0, 0.0, 10.0, 10.0)
    assert res['tools'][1]['z_cut'] == -1.0
    assert res['tools'][1]['z_move'] == 5.0
    assert res['tools'][1]['feedrate'] == 100.0
    assert res['tools'][1]['feedrate_z'] == 100.0
    # several G words on one line; other G codes do not change the motion mode
    res = parse_gcode("G21 G90 G17 G0 X1 Y1\nG1 X5 Y1 F50\n")
    assert res['bounds'] == (1.0, 1.0, 5.0, 1.0) and res['motion_count'] == 2
    res = parse_gcode("G21\nG1 X0 Y0 F10\nG40\nG49\nG94\nG55\nX5 Y0\nG28 G91 Z0\nG90\nX5 Y5\n")
    assert _cut_bounds(res) == (0.0, 0.0, 5.0, 5.0)
    # incremental mode
    res = parse_gcode("G21 G90\nG0 X1 Y1\nG91\nG1 X2 Y0 F10\nG1 X0 Y3\nG90\nG1 X0 Y0\n")
    assert list(_cut_lines(res)[0].coords) == [(1.0, 1.0), (3.0, 1.0), (3.0, 4.0), (0.0, 0.0)]
    # G92 moves the frame without a move
    res = parse_gcode("G21\nG0 X0 Y0\nG1 X5 Y0 F10\nG92 X0 Y0\nG1 X5 Y0\n")
    assert res['bounds'] == (0.0, 0.0, 10.0, 0.0)
    # text that is not G-code has no motion
    assert parse_gcode("Hello world, this is a letter.\nSection 5 is below\n")['motion_count'] == 0
    assert parse_gcode("")['motion_count'] == 0
    # an inch file is scaled to mm
    text = "(T1 D=0.125 CR=0)\nG20 G90\nT1\nS1000\nG0 X0 Y0 Z0.2\nG1 Z-0.05 F10\nG1 X1 Y0 F20\n"
    res = parse_gcode(text, target_units='MM', feedrate_rapid=1000.0)
    t = res['tools'][1]
    assert res['units'] == 'IN'
    assert res['factor'] == pytest.approx(25.4)
    assert res['bounds'] == (0.0, 0.0, pytest.approx(25.4), 0.0)
    assert t['tooldia'] == pytest.approx(3.175)
    assert t['z_cut'] == pytest.approx(-1.27)
    assert t['z_move'] == pytest.approx(5.08)
    assert t['feedrate'] == pytest.approx(508.0)
    assert t['feedrate_z'] == pytest.approx(254.0)
    assert t['spindlespeed'] == 1000.0
    same = parse_gcode(text, target_units='IN')
    assert same['factor'] == 1.0 and same['bounds'][2] == 1.0
    back = parse_gcode("G21\nG1 X25.4 F10\n", target_units='IN')
    assert back['factor'] == pytest.approx(1 / 25.4) and back['bounds'][2] == pytest.approx(1.0)
    # a second units change is ignored with a warning
    res = parse_gcode("G21\nG1 X10 F10\nG20\nG1 X20\n")
    assert res['units'] == 'MM' and res['bounds'][2] == 20.0 and res['warnings']


# ---------------------------------------------------------------- arcs

def test_arcs():
    # I/J quarter arcs with a missing J
    res = parse_gcode("G21 G90\nG0 X1 Y0\nG3 X0 Y1 I-1 F10\nG2 X1 Y0 J-1 F10\n", steps_per_circle=64)
    pts = list(_cut_lines(res)[0].coords)
    assert len(pts) == 1 + 16 + 16
    assert all(abs(math.hypot(x, y) - 1.0) < 1e-9 for x, y in pts)
    assert pts[0] == (1.0, 0.0) and pts[-1] == (1.0, 0.0)
    assert any(abs(x - math.sqrt(0.5)) < 0.05 and y > 0.5 for x, y in pts)
    # full circle and direction
    ccw = parse_gcode("G21\nG0 X1 Y0\nG3 X1 Y0 I-1 J0 F10\n", steps_per_circle=32)
    cw = parse_gcode("G21\nG0 X1 Y0\nG2 X1 Y0 I-1 J0 F10\n", steps_per_circle=32)
    a, b = _cut_lines(ccw)[0].coords, _cut_lines(cw)[0].coords
    assert len(a) == 33 and len(b) == 33
    assert a[1][1] > 0 > b[1][1]
    assert ccw['bounds'][0] == pytest.approx(-1.0)
    # R picks the side: chord (0,0)->(0,2), radius 2: the minor arc bulges to -x for G2 and to +x for G3
    cw = parse_gcode("G21\nG0 X0 Y0\nG2 X0 Y2 R2 F10\n", steps_per_circle=360)
    ccw = parse_gcode("G21\nG0 X0 Y0\nG3 X0 Y2 R2 F10\n", steps_per_circle=360)
    assert cw['bounds'][0] == pytest.approx(math.sqrt(3) - 2, abs=1e-3) and cw['bounds'][2] == 0.0
    assert ccw['bounds'][2] == pytest.approx(2 - math.sqrt(3), abs=1e-3) and ccw['bounds'][0] == 0.0
    assert list(_cut_lines(cw)[0].coords)[-1] == (0.0, 2.0)
    major = parse_gcode("G21\nG0 X0 Y0\nG2 X0 Y2 R-2 F10\n", steps_per_circle=360)     # R < 0: the major arc
    assert _cut_lines(major)[0].length > 2 * _cut_lines(cw)[0].length
    # absolute centre
    res = parse_gcode("G21 G90.1\nG0 X2 Y0\nG3 X0 Y2 I0 J0 F10\n", steps_per_circle=64)
    assert all(abs(math.hypot(x, y) - 2.0) < 1e-9 for x, y in _cut_lines(res)[0].coords)
    # other planes are projected from above
    for plane, line, bounds in (
            # G18 (ZX), half circle from X0 to X10 around X5 Z0, Y linear 0 -> 4
            ("G18", "G2 X10 Y4 Z0 I5 K0", (0.0, 0.0, 10.0, 4.0)),
            # G19 (YZ), full circle around Y5 Z0: from above it goes from Y0 to Y10 and back, X linear 0 -> 3
            ("G19", "G3 X3 Y0 Z0 J5 K0", (0.0, 0.0, 3.0, 10.0))):
        res = parse_gcode("G21 %s\nG0 X0 Y0 Z0\n%s F10\n" % (plane, line), steps_per_circle=64)
        assert res['bounds'] == pytest.approx(bounds, abs=1e-6)
        assert not res['warnings']
        assert res['travel_distance'] > 5 * math.pi       # the 3D length counts the linear axis


# ---------------------------------------------------------------- subprograms and macros

def test_fanuc_subprograms_and_macros():
    from appParsers.ParseGCode import program_features

    for call, count in (('M98 P1000 L3', 3), ('M98 K3 P1000', 3), ('M98 P31000', 3), ('M98 P1000', 1)):
        res = parse_gcode(f"G21 G90\nG0 X0 Y0\nG91 G1 F10\n{call}\nM30\nO1000 (step)\nX1\nM99\n%\n")
        assert res['bounds'] == (0.0, 0.0, float(count), 0.0), call
        assert res['warnings'] == []
    # M99 returns to a line of the caller; M99 in the main program ends it
    xs, warns = _xvals("G21 G90 G0 X0 Y0\nG1 F10\nM98 P2000\nN10 X100\nN20 X7\nM30\nO2000\nG1 X1\nM99 P20\n")
    assert xs == [0.0, 1.0, 7.0] and warns == []
    xs, warns = _xvals("G1 X1\nM99\nG1 X2\n")
    assert xs == [1.0] and warns
    # G65 arguments become local variables and are restored
    res = parse_gcode("G21 G90 G0 X0 Y0\n#1=99\nG65 P9010 A5 B7\nG1 X#1 F10\nM30\nO9010\nG1 X#1 Y#2 F10\n#1=0\nM99\n")
    assert (5.0, 7.0) in [tuple(c) for c in _cut_lines(res)[0].coords]
    assert _end(res) == (99.0, 7.0)
    # the subprogram and macro calls are told to the user of the levelling and the tool blocks
    assert program_features("G0 X1 (#1 [note]) ; M98\n") == {'macros': False, 'subprograms': False}
    assert program_features("#100=5\nG1 X[#100]\n")['macros'] is True
    for text in ("M98 P1000\n", "M99\n", "G65 P9010 A1\n", "o100 call\n", "O<probe> SUB\n"):
        assert program_features(text)['subprograms'] is True, text
    # the tool blocks follow the call site
    text = "G21 G90 G0 X0 Y0\nT1\nM98 P1000\nT2\nM98 P1000\nM98 P1000\nM30\nO1000\nG91 G1 X1 F10\nM99\n"
    res = parse_gcode(text)
    assert [b['tool'] for b in split_tool_blocks(text)] == [1, 2]
    assert ''.join(b['gcode'] for b in split_tool_blocks(text)) == text
    assert res['tools'][1]['travel_distance'] == pytest.approx(1.0)
    assert res['tools'][2]['travel_distance'] == pytest.approx(2.0)
    assert _cut_bounds(res, 2) == (1.0, 0.0, 3.0, 0.0)


def test_linuxcnc_subprograms_and_parameters():
    text = ("G21 G90 G0 X0 Y0\no100 sub\n  G1 X[#1] Y[#2] F10\n  o100 return [#1+#2]\no100 endsub\n"
            "o100 call [3] [4]\nG1 X[#<_value>] Y0\nM2\n")
    res = parse_gcode(text)
    assert res['warnings'] == []
    assert [tuple(c) for c in _cut_lines(res)[0].coords] == [(0.0, 0.0), (3.0, 4.0), (7.0, 0.0)]
    text = ("G21 G90 G0 X0 Y0\n#<_g> = 4\no200 sub\n#<l> = 9\n#<_h> = [#<l> + 1]\no200 endsub\no200 call\n"
            "G1 X#<_g> Y#<_h> F10\nG1 X#<l>\nG1 X[#<L>+#<_G>+1]\n")
    res = parse_gcode(text)
    # the local #<l> is not known outside the sub: its word is omitted (vacant), 0 + #<_g> in the expression
    assert [tuple(c) for c in _cut_lines(res)[0].coords] == [(0.0, 0.0), (4.0, 10.0), (5.0, 10.0)]


def test_flow_control():
    # GOTO loop draws squares
    text = ("G21 G90\n#1=0\nN10 IF [#1 GE 3] GOTO 20\nG0 X[#1*10] Y0\nG91 G1 F10 X5\nY5\nX-5\nY-5\nG90\n"
            "#1=#1+1\nGOTO 10\nN20 M30\n")
    res = parse_gcode(text, feedrate_rapid=1000)
    assert len(_cut_lines(res)) == 3
    assert _cut_bounds(res) == (0.0, 0.0, 25.0, 5.0)
    assert res['warnings'] == []
    # nested WHILE/DO/END and IF THEN
    xs, warns = _xvals("G21 G90 G0 X0 Y0\n#1=1\nWHILE [#1 LE 3] DO1\n#2=1\nWHILE [#2 LE 2] DO2\nG1 X[#1*10] Y[#2] F10\n"
                       "#2=#2+1\nEND2\n#1=#1+1\nIF [#1 EQ 3] THEN #1=#1+10\nEND1\n")
    assert xs == [0.0, 10.0, 10.0, 20.0, 20.0] and warns == []
    # LinuxCNC if / elseif / else
    for v, x in ((1, 1.0), (2, 2.0), (5, 3.0)):
        xs, warns = _xvals(f"#1={v}\no1 if [#1 EQ 1]\nG1 X1\no1 elseif [#1 EQ 2]\nG1 X2\no1 else\nG1 X3\no1 endif\nG1 Y1\n")
        assert xs == [x] and warns == []
    # LinuxCNC loops with break and continue
    for body, count in (
            ("#1=0\no1 while [#1 LT 10]\n#1=[#1+1]\no2 if [#1 EQ 4]\no1 break\no2 endif\nG1 X[#1]\no1 endwhile\n", 3),
            ("o3 repeat [4]\n#1=[#1+1]\no4 if [#1 EQ 2]\no3 continue\no4 endif\nG1 X[#1]\no3 endrepeat\n", 3),
            ("#1=0\no5 do\n#1=[#1+1]\nG1 X[#1]\no5 while [#1 LT 5]\n", 5)):
        xs, warns = _xvals(body)
        assert len(xs) == count and warns == []


EXPRESSIONS = [
    ('2+3*4', 14), ('2**3*2', 16), ('10 MOD 4', 2), ('SIN[30]', 0.5), ('ATAN[1]/[1]', 45), ('-3+5', 2),
    ('SQRT[16]+ABS[-2]', 6), ('ROUND[2.5]+FIX[-1.5]+FUP[1.2]', 3), ('1 LT 2 AND 3 GT 2', 1), ('8/4/2', 1),
    ('LN[EXP[2]]', 2), ('POW[2,10]', 1024), ('COS[60]*2', 1), ('EXISTS[#<nope>]', 0), ('[1+2]*[3+4]', 21),
    ('1 OR 0 XOR 1', 0), ('#0+4', 4), ('BIN[25]', 37), ('BCD[37]', 25),
    # Fanuc: bit by bit; LinuxCNC (a named parameter makes the program LinuxCNC): logical
    ('12 AND 10', 8), ('12 AND 10 + #<n>', 1),
]


def test_expression_precedence_and_functions():
    for expr, value in EXPRESSIONS:
        xs, warns = _xvals(f"#1=[{expr}]\nG1 X#1\n")
        assert (expr, xs, warns) == (expr, [pytest.approx(value)], [])


def test_machine_state_missing_subprogram_and_runaway_loops():
    from appParsers.ParseGCode import expand_program

    # a machine state variable skips the move; an alarm stops the program
    res = parse_gcode("G21 G90 G0 X0 Y0\nG1 X#5001 Y5 F10\nG1 X3 Y3\n#100=#5002\nG1 X[#100+1] Y9\n")
    assert _cut_bounds(res) == (0.0, 0.0, 3.0, 3.0)
    assert any('#5001' in w for w in res['warnings'])
    xs, warns = _xvals("G1 X1\n#3000=1\nG1 X2\n")
    assert xs == [1.0] and warns
    # an external subprogram warns only for what is missing
    res = parse_gcode("G21\n#100=5\nG1 X[#100+1] Y2 F10\nM98 P555\nG1 X9\n")
    assert res['bounds'][2] == 9.0
    assert len(res['warnings']) == 1 and '555' in res['warnings'][0]
    # runaway loops stop with a warning; a big expansion is fast
    t0 = time.time()
    for text in ("WHILE [1] DO1\nG1 X1\nEND1\n", "N1 G1 X1\nGOTO 1\n", "o1 sub\no1 call\no1 endsub\no1 call\n"):
        lines, warns = expand_program(text)
        assert warns and len(lines) <= 200000
    assert time.time() - t0 < 5.0
    big = "#1=0\nWHILE [#1 LT 9000] DO1\nG1 X[#1] Y[#1*2]\nG1 X[#1+1]\n" + "G4 P1\n" * 8 + "#1=#1+1\nEND1\n"
    t0 = time.time()
    res = parse_gcode(big)
    assert res['motion_count'] == 18000 and time.time() - t0 < 6.0


# ---------------------------------------------------------------- drills

def test_drills_slots_and_rings():
    # canned cycle: a drill hit until G80
    res = parse_gcode("G21 G90 G99\nT1\nG0 X0 Y0 Z5\nG81 X1 Y1 Z-2 R2 F50\nX3 Y1\nX3 Y3\nG80\nG0 X9 Y9\n")
    t = res['tools'][1]
    assert t['drills'] == [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0)]
    assert t['nr_drills'] == 3 and res['type'] == 'Excellon' and t['z_cut'] == -2.0
    # plunge drills make an Excellon with rings
    text = (
        "(Tool: 1 -> Dia: 0.8)\nG21 G90\nT1\nG0 X0 Y0 Z5\nG1 Z-2 F30\nG0 Z5\n"
        "G0 X4 Y0\nG1 Z-2 F30\nG1 Z5\nG0 X8 Y0\nG1 Z-2\nG0 Z5\n"
    )
    res = parse_gcode(text, steps_per_circle=32)
    t = res['tools'][1]
    assert res['type'] == 'Excellon'
    assert t['nr_drills'] == 3 and t['nr_slots'] == 0
    assert t['drills'] == [(0.0, 0.0), (4.0, 0.0), (8.0, 0.0)]
    rings = [g for g in t['gcode_parsed'] if g['kind'] == ['C', 'F']]
    assert len(rings) == 3
    assert rings[0]['geom'].geom_type == 'LinearRing'
    assert rings[0]['geom'].bounds == pytest.approx((-0.4, -0.4, 0.4, 0.4))
    assert len(rings[0]['geom'].coords) == 33
    assert t['solid_geometry'] == [g['geom'] for g in t['gcode_parsed']]
    # an unknown tool diameter uses the default for the rings
    res = parse_gcode("G21\nT1\nG0 X0 Y0 Z5\nG1 Z-2 F30\nG0 Z5\n", default_tooldia=1.0)
    assert res['tools'][1]['tooldia'] is None
    ring = [g for g in res['tools'][1]['gcode_parsed'] if g['kind'] == ['C', 'F']][0]['geom']
    assert ring.bounds == pytest.approx((-0.5, -0.5, 0.5, 0.5))
    # a slot is counted and is a ring in an Excellon
    text = (
        "(Type: G-code from Excellon)\nG21 G90\nT1\nG0 X0 Y0 Z5\nG1 Z-2 F30\nG1 X4 Y0\nG0 Z5\n"
        "G0 X0 Y3\nG1 Z-2\nG0 Z5\n"
    )
    t = parse_gcode(text, default_tooldia=1.0)['tools'][1]
    assert (t['nr_drills'], t['nr_slots']) == (1, 1)
    slot = [g['geom'] for g in t['gcode_parsed'] if g['kind'] == ['C', 'F'] and g['geom'].bounds[2] > 2][0]
    assert slot.bounds == pytest.approx((-0.5, -0.5, 4.5, 0.5))


def test_milling_with_a_plunge_stays_geometry():
    res = parse_gcode("G21\nT1\nG0 X0 Y0 Z5\nG1 Z-1 F30\nG1 X5 Y0 F100\nG1 X5 Y5\nG0 Z5\n")
    t = res['tools'][1]
    assert res['type'] == 'Geometry'
    assert t['nr_slots'] == 0 and t['nr_drills'] == 0
    assert _cut_lines(res)[0].length == pytest.approx(10.0)
    # a contour that starts with a plunge is a milled path, not a slot: the drills do not make it an Excellon
    res = parse_gcode("G21\nT1\nG0 X0 Y0 Z5\nG1 Z-1 F30\nG1 X5 F100\nY5\nX0\nY0\nG0 Z5\n"
                      "T2\nG0 X10 Y0\nG81 R2 Z-2 F50\nX12\nX14\nG80\n")
    assert res['type'] == 'Geometry'
    assert (res['tools'][1]['nr_slots'], res['tools'][2]['nr_drills']) == (0, 3)
    assert _cut_lines(res)[0].length == pytest.approx(20.0)
    # the first moves give only Z, then XY: no travel line from (0, 0), the bounds do not hold the origin
    res = parse_gcode("G21 G90\nG0 Z5\nG0 X10 Y10\nG1 Z-1 F50\nG1 X20\nG0 Z5\n")
    assert res['bounds'] == (10.0, 10.0, 20.0, 10.0)
    assert all(min(g['geom'].bounds[:2]) >= 10.0 for g in res['tools'][1]['gcode_parsed'])
    assert res['tools'][1]['z_move'] == 5.0


# ---------------------------------------------------------------- headers, tools, distance

def test_tool_headers_blocks_and_travel():
    for comment, dia in (("(Tool: 1 -> Dia: 0.8)", 0.8), ("(T1 D=3.175 CR=0 - ZMIN=-2 - flat end mill)", 3.175),
                         (";T1 D=0.8", 0.8), ("(T1 Dia 0.125)", 0.125), ("(Tool 1, Dia 0.8)", 0.8),
                         ("(Tool 1 diameter: 2.0)", 2.0)):
        res = parse_gcode("%s\nG21\nT1\nG1 X1 F10\n" % comment)
        assert (comment, res['tools'][1]['tooldia']) == (comment, pytest.approx(dia))
    # a diameter comment after the tool word; the tools are mapped by the tool number
    res = parse_gcode("G21\nT1 (D0.8)\nG1 X1 F10\nT2 M6 (D=2)\nG1 X2\nT1\nG1 X3\n")
    assert [res['tools'][k]['tool'] for k in (1, 2, 3)] == [1, 2, 1]
    assert [res['tools'][k]['tooldia'] for k in (1, 2, 3)] == [0.8, 2.0, 0.8]
    # the FlatCAM header: type, preprocessor and Z cut
    res = parse_gcode(
        "(G-CODE GENERATED BY FLATCAM v8.5 - www.flatcam.org)\n(Type: G-code from Geometry)\n"
        "(Preprocessor Geometry: GRBL_11)\n(Tool: 1 -> Dia: 0.4)\n(Tool: 1 -> Z_Cut: -0.1)\nG21\nT1\nG0 X0 Y0\n"
        "G1 Z-0.1 F20\nG1 X2\n")
    assert res['flatcam'] is True and res['type'] == 'Geometry' and res['preprocessor'] == 'GRBL_11'
    assert res['tools'][1]['z_cut'] == pytest.approx(-0.1)
    exc = parse_gcode("(Type: G-code from Excellon)\n(Preprocessor Excellon: default)\nG21\nG1 X1 F10\n")
    assert exc['type'] == 'Excellon' and exc['preprocessor'] == 'default'
    # per tool blocks keep text and geometry apart
    text = "G21\nT1\nG0 X0 Y0\nG1 X1 F10\nT2\nG1 X1 Y1\nG1 X2 Y1\n"
    res = parse_gcode(text)
    assert ''.join(res['tools'][k]['gcode'] for k in res['tools']) == text
    assert _cut_bounds(res, 1) == (0.0, 0.0, 1.0, 0.0)
    assert _cut_bounds(res, 2) == (1.0, 0.0, 2.0, 1.0)
    # cut and travel kinds are joined per kind
    res = parse_gcode("G21\nG0 X0 Y0\nG0 X1 Y0\nG1 X2 Y0 F10\nG1 X2 Y1\nG0 X0 Y1\n")
    assert [g['kind'] for g in res['tools'][1]['gcode_parsed']] == [['T', 'F'], ['C', 'S'], ['T', 'F']]
    assert len(res['tools'][1]['gcode_parsed'][1]['geom'].coords) == 3
    # travel distance and routing time
    text = "G21\nG0 X0 Y0 Z0\nG0 X3 Y4\nG1 X3 Y10 F60\nG1 Z-3 F30\n"
    res = parse_gcode(text, feedrate_rapid=100.0)
    assert res['travel_distance'] == pytest.approx(5 + 6 + 3)
    assert res['routing_time'] == pytest.approx(5 / 100 + 6 / 60 + 3 / 30)
    assert res['bounds'] == (0.0, 0.0, 3.0, 10.0)
    assert parse_gcode(text)['routing_time'] == pytest.approx(6 / 60 + 3 / 30)
    assert res['tools'][1]['travel_distance'] == pytest.approx(14.0)


def test_dwell_in_seconds_is_included_in_estimated_time():
    res = parse_gcode('G21\nG0 X0 Y0\nG1 X10 F60\nG4 S30\n')
    assert res['routing_time'] == pytest.approx(10 / 60 + 30 / 60)


@pytest.mark.parametrize('preprocessor, expected', [('default', 0.5), ('Marlin', 0.0005),
                                                  ('Repetier', 0.0005)])
def test_dwell_p_uses_the_known_controller_units(preprocessor, expected):
    res = parse_gcode('(Preprocessor Geometry: %s)\nG21\nG4 P30\n' % preprocessor)
    assert res['routing_time'] == pytest.approx(expected)


def test_unknown_rapid_rate_and_dwell_units_report_partial_estimates():
    res = parse_gcode('G21\nG0 X0 Y0\nG0 X10\nG4 P30\n')
    assert res['warnings']
    assert any('rapid' in w.lower() for w in res['warnings'])
    assert any('dwell' in w.lower() for w in res['warnings'])


def test_known_z_motion_counts_before_the_first_xy_position():
    res = parse_gcode('G21\nG0 Z5\nG1 Z-2 F60\nG0 X0 Y0\n', feedrate_rapid=600)
    assert res['travel_distance'] == pytest.approx(7)
    assert res['routing_time'] == pytest.approx(7 / 60)


# ---------------------------------------------------------------- transform_gcode

OFFSET = (1, 0, 0, 1, 5, 7)
ROT90 = (0, -1, 1, 0, 0, 0)          # affine_rotate_matrix(90, (0, 0))
MIRROR_Y = (-1, 0, 0, 1, 0, 0)       # the X coordinates change sign
SKEW = (1, 0.5, 0, 1, 0, 0)


def test_transform_rewrites_the_coordinates_and_keeps_the_rest_of_the_text():
    # integer coordinates move, the incremental ones stay; a G28 with an axis is not transformed and said so
    out, warns = transform_gcode("G0 X10 Y2\nG91\nG1 X1 Y1\nG90\nG1 X20\nG28 X0\n", OFFSET)
    assert out.splitlines() == ["G0 X15.0000 Y9.0000", "G91", "G1 X1 Y1", "G90", "G1 X25.0000", "G28 X0"]
    assert len(warns) == 1
    # a rotation writes the axis the line did not have
    out, _ = transform_gcode("G90\nG0 X10 Y2\nG1 Y5\n(X7 stays)\nG91\nG1 X3\n", ROT90, decimals=2)
    assert out.splitlines() == ["G90", "G0 X-2.00 Y10.00", "G1 X-5.00 Y10.00", "(X7 stays)", "G91", "G1 X0.00 Y3.00"]
    # comments, CRLF and the words it does not change stay
    text = "%\r\n(Tool: 1 -> Dia: 0.8 X5)\r\nN10 G21 G90 ; X1\r\nG1 X1 Y1 F100 S500\r\nM3\r\n"
    out, _ = transform_gcode(text, OFFSET)
    assert out == "%\r\n(Tool: 1 -> Dia: 0.8 X5)\r\nN10 G21 G90 ; X1\r\nG1 X6.0000 Y8.0000 F100 S500\r\nM3\r\n"
    assert transform_gcode(text, (1, 0, 0, 1, 0, 0)) == (text, [])


def test_transform_of_arcs():
    # the mirror swaps the arc direction and the centre offset
    out, warns = transform_gcode("G90\nG0 X5 Y0\nG2 X0 Y5 I-5 J0 F100\n", MIRROR_Y)
    assert out.splitlines()[2] == "G3 X0 Y5 I5.0000 J0 F100" and warns == []
    out, _ = transform_gcode("G90\nG0 X5 Y0\nG2 X0 Y5 R5\n", (2, 0, 0, 2, 0, 0))
    assert out.splitlines()[2] == "G2 X0 Y10.0000 R10.0000"
    # a skew replaces the arc by straight segments and warns
    text = "G90\nG0 X5 Y0\nG3 X0 Y5 I-5 J0 F100 (quarter)\nG0 Z5\n"
    out, warns = transform_gcode(text, SKEW, steps_per_circle=16)
    lines = out.splitlines()
    assert len(warns) == 1 and 'G2' not in out and 'G3' not in out
    assert lines[2].startswith("G1 ") and "F100" in lines[2] and "(quarter)" in lines[2]
    assert lines[-1] == "G0 Z5" and lines[-2].startswith("G1 X2.5000 Y5")      # (0, 5) skewed
    arc = parse_gcode(text, steps_per_circle=16)['tools'][1]['gcode_parsed'][-1]['geom']
    new = parse_gcode(out, steps_per_circle=16)['tools'][1]['gcode_parsed'][-1]['geom']
    assert new.bounds == pytest.approx(shapely.affinity.affine_transform(arc, SKEW).bounds, abs=1e-3)
    # an arc in another plane: a translation keeps it, a rotation takes it out of its plane
    text = "G21 G90 G18\nG0 X0 Y0 Z0\nG2 X10 Y4 Z0 I5 K0 F10\n"
    out, warns = transform_gcode(text, OFFSET)
    assert out.splitlines()[2] == "G2 X15.0000 Y11.0000 Z0 I5 K0 F10" and warns == []
    out, warns = transform_gcode(text, ROT90, steps_per_circle=64)
    assert warns and not re.search(r'G[23]\b', out)
    old = parse_gcode(text, steps_per_circle=64)
    new = parse_gcode(out, steps_per_circle=64)
    rotated = shapely.affinity.affine_transform(old['tools'][1]['gcode_parsed'][-1]['geom'], ROT90)
    assert new['tools'][1]['gcode_parsed'][-1]['geom'].bounds == pytest.approx(rotated.bounds, abs=1e-3)
    assert new['travel_distance'] == pytest.approx(old['travel_distance'], rel=1e-3)


# ---------------------------------------------------------------- CNCjob native transforms

_WORD = re.compile(r'([XYZIJF])(-?\d+(?:\.\d+)?)')


def _native_words(line):
    return {k: float(v) for k, v in _WORD.findall(line)}


def _native_job(text, multitool):
    """A CNCjob holding ``text`` as native (not imported) G-code, single tool or multitool."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import shapely.geometry
    import camlib
    from appParsers.ParseGCode import parse_gcode

    app = SimpleNamespace(
        decimals=4, app_units='MM',
        plotcanvas=SimpleNamespace(new_shape_collection=lambda layers=1: None),
        options={'cncjob_steps_per_circle': 64, 'cncjob_coords_type': 'G90', 'cncjob_bed_max_x': 0,
                 'cncjob_bed_max_y': 0, 'cncjob_bed_offset_x': 0, 'cncjob_bed_offset_y': 0,
                 'cncjob_bed_skew_x': 0, 'cncjob_bed_skew_y': 0},
        preprocessors={'default': object()}, inform=MagicMock(), log=MagicMock(),
        proc_container=SimpleNamespace(new_text=''),
    )
    job = camlib.CNCjob(app=app, units='MM', pp_geometry_name='default', pp_excellon_name='default',
                        steps_per_circle=64)
    job.obj_options = {'type': 'Geometry', 'name': 'job1'}
    job.gcode_imported = False
    job.gc_start = job.prepend_snippet = job.append_snippet = ''
    job.is_segmented_gcode = True
    parsed = parse_gcode(text, steps_per_circle=64)['tools'][1]['gcode_parsed']
    job.gcode = text
    job.gcode_parsed = parsed
    job.solid_geometry = shapely.geometry.LineString([(0, 0), (1, 1)])
    job.multitool = multitool
    if multitool:
        job.tools = {1: {'gcode': text, 'gcode_parsed': parsed, 'solid_geometry': job.solid_geometry,
                         'tooldia': 0.4, 'data': {}}}
        job.used_tools = [1]
    else:
        job.tools = {}
        job.used_tools = []
    return job


def _native_text(job):
    return job.tools[1]['gcode'] if job.multitool else job.gcode


@pytest.mark.parametrize('multitool', [False, True])
def test_native_scale_moves_xy_about_the_point_and_keeps_z_and_feed(multitool):
    old = "G21 G90\nG0 X0 Y0 Z5 F600\nG1 X2 Y3 Z-0.1 F100\nG1 X-1 Y4\nG0 Z5\n"
    job = _native_job(old, multitool)
    job.scale(2, 3, point=(1, 1))
    new_lines = _native_text(job).splitlines()
    old_lines = old.splitlines()
    assert len(new_lines) == len(old_lines)
    for o, n in zip(old_lines, new_lines):
        wo, wn = _native_words(o), _native_words(n)
        assert set(wn) == set(wo)
        if 'X' in wo:
            assert wn['X'] == pytest.approx(1 + 2 * (wo['X'] - 1))
        if 'Y' in wo:
            assert wn['Y'] == pytest.approx(1 + 3 * (wo['Y'] - 1))
        for k in 'ZF':
            if k in wo:
                assert wn[k] == pytest.approx(wo[k])


@pytest.mark.parametrize('multitool', [False, True])
def test_native_scale_doubles_arc_offsets_and_non_uniform_scale_linearizes_the_arc(multitool):
    text = "G21 G90\nG0 X0 Y0\nG2 X2 Y0 I1 J0 F100\n"
    job = _native_job(text, multitool)
    job.scale(2, 2, point=(0, 0))
    arc = _native_text(job).splitlines()[2]
    assert re.search(r'\bG2\b', arc)
    w = _native_words(arc)
    assert w['X'] == pytest.approx(4) and w['Y'] == pytest.approx(0)
    assert w['I'] == pytest.approx(2) and w['J'] == pytest.approx(0)
    assert w['F'] == pytest.approx(100)

    job = _native_job(text, multitool)
    job.scale(2, 1, point=(0, 0))
    out_lines = _native_text(job).splitlines()
    assert not any(re.search(r'\bG[23]\b', line) for line in out_lines)
    last = _native_words(out_lines[-1])
    assert last['X'] == pytest.approx(4) and last['Y'] == pytest.approx(0)


@pytest.mark.parametrize('multitool', [False, True])
def test_native_offset_moves_absolute_xy_and_nothing_else(multitool):
    text = "G21 G90\nG0 X0 Y0\nG1 X5 Y5 F100\nG91\nG1 X1 Y1\nG90\nG53 G0 X0 Y0\n(X1.0 Y2.0)\n"
    # (source line, expected words after the offset; None = the line must stay byte for byte)
    cases = [
        ("G21 G90", None),
        ("G0 X0 Y0", {'X': 10, 'Y': 5}),
        ("G1 X5 Y5 F100", {'X': 15, 'Y': 10, 'F': 100}),
        ("G91", None),
        ("G1 X1 Y1", {'X': 1, 'Y': 1}),          # incremental: the offset must not touch it
        ("G90", None),
        ("G53 G0 X0 Y0", {'X': 0, 'Y': 0}),      # machine coordinates: not shifted
        ("(X1.0 Y2.0)", None),
    ]
    job = _native_job(text, multitool)
    job.offset((10, 5))
    out_lines = _native_text(job).splitlines()
    assert len(out_lines) == len(cases)
    for line, (src, want) in zip(out_lines, cases):
        if want is None:
            assert line == src
            continue
        got = _native_words(line)
        assert set(got) == set(want)
        for k in want:
            assert got[k] == pytest.approx(want[k])


def test_native_convert_units_in_to_mm_scales_lengths_and_feed_and_switches_g20():
    text = "G20\n(Spindle Speed: 1000)\nG1 X1 Y2 Z-0.1 F10\nG2 X2 Y2 I0.5 J0\n"
    job = _native_job(text, multitool=False)
    job.units = 'IN'
    job.convert_units('MM')
    out = job.gcode
    lines = out.splitlines()
    assert re.search(r'\bG21\b', lines[0]) and not re.search(r'\bG20\b', out)
    assert lines[1] == "(Spindle Speed: 1000)"
    g1 = _native_words(lines[2])
    assert g1 == pytest.approx({'X': 25.4, 'Y': 50.8, 'Z': -2.54, 'F': 254}, abs=1e-3)
    g2 = _native_words(lines[3])
    assert g2 == pytest.approx({'X': 50.8, 'Y': 50.8, 'I': 12.7, 'J': 0}, abs=1e-3)


@pytest.mark.parametrize('transform', ['rotate', 'offset'])
def test_native_transforms_keep_the_toolchange_and_end_positions(transform):
    text = "G21 G90\nG00 X5 Y6\nM6\nG00 X1 Y2\nG1 Z-0.1 F50\nG1 X3 Y4 F100\nG00 Z5\nG00 X-7 Y8\n"
    job = _native_job(text, multitool=False)
    job.xy_toolchange = [5, 6]
    job.xy_end = [-7, 8]
    if transform == 'rotate':
        job.rotate(90, (0, 0))
        moved = lambda x, y: (-y, x)
    else:
        job.offset((10, 5))
        moved = lambda x, y: (x + 10, y + 5)
    lines = job.gcode.splitlines()
    assert len(lines) == 8
    tc = _native_words(lines[1])
    assert tc['X'] == pytest.approx(5) and tc['Y'] == pytest.approx(6)
    end = _native_words(lines[7])
    assert end['X'] == pytest.approx(-7) and end['Y'] == pytest.approx(8)
    # the approach move after the tool change and the cut follow the transform
    for idx, (x, y) in ((3, (1, 2)), (5, (3, 4))):
        w = _native_words(lines[idx])
        ex, ey = moved(x, y)
        assert w['X'] == pytest.approx(ex) and w['Y'] == pytest.approx(ey)


_SQUARE_TWO_PASSES = (
    "G21 G90\nG00 Z1\nG00 X0 Y0\nG01 Z-0.1 F50\nG01 X4 Y0 F100\nG01 X4 Y4\nG01 X0 Y4\nG01 X0 Y0\nG00 Z1\n"
    "G00 X0 Y0\nG01 Z-0.2 F50\nG01 X4 Y0 F100\nG01 X4 Y4\nG01 X0 Y4\nG01 X0 Y0\nG00 Z5\nG00 X0 Y0\nM05\n"
)


def test_native_transform_moves_every_approach_move_before_a_cut():
    # the end position is the start of the closed path, as when End X,Y is empty (xy_end = last point)
    job = _native_job(_SQUARE_TWO_PASSES, multitool=False)
    job.xy_toolchange = None
    job.xy_end = [0, 0]
    job.offset((10, 5))
    lines = _native_text(job).splitlines()
    approach, cut_xy = [], []
    for i, line in enumerate(lines):
        words = _native_words(line)
        if line.startswith('G00') and ('X' in words or 'Y' in words):
            nxt = next((n for n in lines[i + 1:] if re.search(r'[XYZ]', n)), '')
            if nxt.startswith('G01') and 'Z' in nxt and 'X' not in nxt and 'Y' not in nxt:
                approach.append(words)
        if line.startswith('G01') and ('X' in line or 'Y' in line):
            cut_xy.append((words['X'], words['Y']))
    # both approach moves (before each plunge) are shifted
    assert len(approach) == 2
    for w in approach:
        assert w['X'] == pytest.approx(10) and w['Y'] == pytest.approx(5)
    # every cut move is shifted
    expected = [(4, 0), (4, 4), (0, 4), (0, 0)] * 2
    assert len(cut_xy) == len(expected)
    for (x, y), (ex, ey) in zip(cut_xy, expected):
        assert x == pytest.approx(ex + 10) and y == pytest.approx(ey + 5)
    # the end move after the last cut keeps the end position
    assert lines[-1] == 'M05' and lines[-2].startswith('G00')
    end = _native_words(lines[-2])
    assert end['X'] == pytest.approx(0) and end['Y'] == pytest.approx(0)
    # the plot follows the text: the cuts are on the shifted square, the last travel goes to the end position
    corners = [(10, 5), (14, 5), (14, 9), (10, 9)]
    cuts = [g['geom'] for g in job.gcode_parsed if g['kind'][0] == 'C']
    travels = [g['geom'] for g in job.gcode_parsed if g['kind'][0] == 'T']
    assert cuts
    for geom in cuts:
        for x, y in geom.coords:
            assert any(abs(x - cx) < 1e-6 and abs(y - cy) < 1e-6 for cx, cy in corners)
    assert travels and tuple(travels[-1].coords[-1][:2]) == pytest.approx((0, 0))
