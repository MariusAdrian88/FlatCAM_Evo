#!/usr/bin/env python
"""
Unit tests for the autolevelling interpolation and parsing helpers.

Run: python tests/test_levelling.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from appPlugins.Common.levelling_interp import (
    build_bilinear_grid,
    bilinear_offset,
    nearest_offset,
    _group_values,
    parse_height_map_line,
    match_nearest_point,
    match_tolerance,
    new_levelling_state,
    level_gcode_line,
)
from appPlugins.Common.grbl.grbl_protocol import (
    parse_grbl_probe_output,
    parse_grbl_active_wcs,
    parse_grbl_work_offset,
)
from types import SimpleNamespace
from unittest.mock import MagicMock
from appPlugins.Common.levelling_interp import level_gcode_line, new_levelling_state


def plane(x, y):
    return 0.01 * x - 0.02 * y + 0.1


class TestBilinear(unittest.TestCase):
    def setUp(self):
        xs = [0, 10, 20]
        ys = [0, 5, 10]
        points = []
        for x in xs:
            for y in ys:
                points.append((x, y, plane(x, y)))
        # shuffle deterministically
        self.points = [
            points[4], points[0], points[7], points[1], points[8],
            points[2], points[5], points[3], points[6],
        ]
        self.grid = build_bilinear_grid(self.points)
        self.assertIsNotNone(self.grid)

    def test_corners(self):
        for x, y in [(0, 0), (20, 0), (0, 10), (20, 10)]:
            self.assertAlmostEqual(
                bilinear_offset(self.grid, x, y), plane(x, y), places=9
            )


class TestGridDetection(unittest.TestCase):
    def setUp(self):
        self.xs = [0, 10, 20]
        self.ys = [0, 5, 10]

    def _points(self):
        points = []
        for x in self.xs:
            for y in self.ys:
                points.append((x, y, plane(x, y)))
        return points

    def test_off_grid_point_returns_none(self):
        points = self._points()
        # move one point off-grid in X
        x, y, z = points[0]
        points[0] = (10.3 if x == 0 else x, y, z)
        result = build_bilinear_grid(points)
        self.assertIsNone(result)


    def test_noise_still_builds_grid(self):
        points = self._points()
        noisy = [(x + 1e-9, y - 1e-9, z) for (x, y, z) in points]
        result = build_bilinear_grid(noisy)
        self.assertIsNotNone(result)
        xs, ys, grid = result
        self.assertEqual(len(xs), 3)
        self.assertEqual(len(ys), 3)


    def test_duplicate_cell_returns_none(self):
        points = self._points()
        # add an extra point that snaps to an already-filled cell
        points.append((points[0][0], points[0][1], points[0][2]))
        result = build_bilinear_grid(points)
        self.assertIsNone(result)


class TestGroupValues(unittest.TestCase):
    def test_no_chaining_across_tol_span(self):
        # 0, 0.4e-6, 0.8e-6, 1.2e-6 with tol=1e-6: each consecutive pair
        # is within tol of its neighbor, but the span from 0 to 1.2e-6
        # exceeds tol, so this must NOT collapse into a single group.
        # Anchoring to the first value of the group (not the previous
        # value) gives 2 groups: {0, 0.4e-6, 0.8e-6} and {1.2e-6}.
        groups = _group_values([0, 0.4e-6, 0.8e-6, 1.2e-6], 1e-6)
        self.assertEqual(len(groups), 2)


class TestNearest(unittest.TestCase):
    def setUp(self):
        self.points = [
            (0, 0, 1.0),
            (10, 0, 2.0),
            (0, 10, 3.0),
            (10, 10, 4.0),
            (5, 5, 5.0),
        ]


    def test_query_near_point(self):
        self.assertEqual(nearest_offset(self.points, 9.9, 0.1), 2.0)


    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            nearest_offset([], 0, 0)


class TestParseHeightMapLine(unittest.TestCase):
    def test_mach_comma(self):
        self.assertEqual(parse_height_map_line("1.0,2.0,-0.05"), (1.0, 2.0, -0.05))


    def test_linuxcnc_space_separated(self):
        self.assertEqual(
            parse_height_map_line("1.0 2.0 -0.05 0 0 0 0 0 0"),
            (1.0, 2.0, -0.05),
        )


    def test_non_numeric_tokens(self):
        self.assertIsNone(parse_height_map_line("abc,def,ghi"))


class TestParseGrbl(unittest.TestCase):
    def test_probe_output_in_order(self):
        text = (
            "ok\n"
            "<Idle|MPos:0.000,0.000,0.000|FS:0,0>\n"
            "[PRB:1.000,2.000,-0.100:1]\n"
            "ok\n"
            "[PRB:3.000,4.000,-0.200:1]\n"
            "[MSG:Probe complete]\n"
            "[PRB:5.000,6.000,-0.300:1]\n"
            "ok\n"
        )
        result = parse_grbl_probe_output(text)
        self.assertEqual(
            result,
            [
                (1.0, 2.0, -0.1),
                (3.0, 4.0, -0.2),
                (5.0, 6.0, -0.3),
            ],
        )


    def test_probe_output_flag_zero_raises(self):
        text = "[PRB:1.000,2.000,-0.100:0]\n"
        with self.assertRaises(ValueError) as ctx:
            parse_grbl_probe_output(text)
        msg = str(ctx.exception)
        self.assertIn("1.0", msg)
        self.assertIn("2.0", msg)


    def test_work_offset_full_sample(self):
        text = (
            "[G54:-100.000,-50.000,-20.000]\n"
            "[G55:0.000,0.000,0.000]\n"
            "[G92:1.000,2.000,3.000]\n"
            "[TLO:0.500]\n"
            "[PRB:0.000,0.000,0.000:0]\n"
            "ok\n"
        )
        result = parse_grbl_work_offset(text)
        self.assertAlmostEqual(result[0], -99.0)
        self.assertAlmostEqual(result[1], -48.0)
        self.assertAlmostEqual(result[2], -20.0 + 3.0 + 0.5)


    def test_work_offset_missing_g54_raises(self):
        text = "[G55:0.000,0.000,0.000]\nok\n"
        with self.assertRaises(ValueError):
            parse_grbl_work_offset(text)

    def test_work_offset_uses_selected_g55(self):
        text = (
            "[G54:-100.000,-50.000,-20.000]\n"
            "[G55:10.000,20.000,30.000]\n"
            "[G92:1.000,2.000,3.000]\n"
            "[TLO:0.500]\n"
        )
        result = parse_grbl_work_offset(text, active_wcs='G55')
        self.assertEqual(result, (11.0, 22.0, 33.5))


    def test_work_offset_malformed_records_raise_value_error(self):
        for key in ('G54', 'G55', 'G92', 'TLO'):
            payloads = ('', ' ', '0,', '0,,0', '0,0,0,0', 'bad', 'nan', 'inf', '-inf', '1e309')
            if key != 'TLO':
                payloads += ('0', '0,0', '0,0,0,', '0,0,nan', '0,inf,0', '1e309,0,0')
            else:
                payloads += ('0,0', '0,nan')
            for payload in payloads:
                with self.subTest(key=key, payload=payload):
                    prefix = '' if key == 'G54' else '[G54:0,0,0]\n'
                    with self.assertRaises(ValueError):
                        parse_grbl_work_offset(prefix + '[%s:%s]\nok\n' % (key, payload))
            for record in ('[%s:0,0,0', '[%s:]', '[%s]', '[%s:0]junk'):
                with self.subTest(key=key, record=record):
                    with self.assertRaises(ValueError):
                        parse_grbl_work_offset('[G54:0,0,0]\n' + record % key)


    def test_work_offset_sum_must_remain_finite(self):
        with self.assertRaises(ValueError):
            parse_grbl_work_offset('[G54:1e308,0,0]\n[G92:1e308,0,0]')


class TestMatchNearestPoint(unittest.TestCase):
    def setUp(self):
        self.points = {
            'a': (0, 0),
            'b': (10, 0),
            'c': (0, 10),
        }

    def test_within_tolerance(self):
        self.assertEqual(match_nearest_point(self.points, 0.5, 0.5, 1.0), 'a')

    def test_outside_tolerance(self):
        self.assertIsNone(match_nearest_point(self.points, 5, 5, 1.0))


class TestMatchTolerance(unittest.TestCase):
    def test_half_min_distance_below_cap(self):
        # closest pair is 1.0 apart -> half = 0.5, capped at 5.0 -> 0.5
        points = [(0, 0), (1, 0), (10, 10)]
        self.assertAlmostEqual(match_tolerance(points, cap=5.0), 0.5)

    def test_capped_when_points_far_apart(self):
        # closest pair is 10 apart -> half = 5.0, but cap is 0.5
        points = [(0, 0), (10, 0)]
        self.assertAlmostEqual(match_tolerance(points, cap=0.5), 0.5)


class TestLevelGcodeLine(unittest.TestCase):
    @staticmethod
    def offset_fn(x, y):
        return 0.01 * x + 0.1

    def test_basic_linear_move(self):
        state = new_levelling_state()
        line = "G01 X10.0 Y5.0 Z-0.1"
        result = level_gcode_line(line, state, self.offset_fn)
        expected_z = -0.1 + self.offset_fn(10.0, 5.0)
        self.assertEqual(result, "G01 X10.0 Y5.0 Z{0:.4f}".format(expected_z))

    def test_modal_z_appended_on_next_line(self):
        state = new_levelling_state()
        level_gcode_line("G01 Z-0.1", state, self.offset_fn)
        self.assertEqual(state['Z'], -0.1)
        result = level_gcode_line("X10.0 Y5.0", state, self.offset_fn)
        expected_z = -0.1 + self.offset_fn(10.0, 5.0)
        self.assertEqual(result, "X10.0 Y5.0 Z{0:.4f}".format(expected_z))
        self.assertEqual(state['Z'], -0.1)


    def test_rapid_move_unchanged(self):
        state = new_levelling_state()
        line = "G00 X10 Y5 Z-0.1"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)

    def test_rapid_then_positive_z_feed_is_compensated(self):
        # every G1 move is compensated, also above Z0 (policy since 2026-10-12)
        state = new_levelling_state()
        level_gcode_line("G00 Z2.0", state, lambda x, y: 0.5)
        result = level_gcode_line("G01 X1 Y1", state, lambda x, y: 0.5)
        self.assertEqual(result, "G01 X1 Y1 Z2.5000")

    def test_arc_move_unchanged_and_counted(self):
        state = new_levelling_state()
        state['G'] = 1
        state['Z'] = -0.1
        line = "G02 X10 Y5 I1 J0"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)
        self.assertEqual(state['arcs'], 1)


    def test_non_motion_g_words_no_state_change(self):
        state = new_levelling_state()
        result = level_gcode_line("G21 G90 G17 G94", state, self.offset_fn)
        self.assertEqual(result, "G21 G90 G17 G94")
        self.assertEqual(state['G'], 0)

        result = level_gcode_line("G01 X1 Y1 Z-0.1", state, self.offset_fn)
        self.assertEqual(state['G'], 1)
        expected_z = -0.1 + self.offset_fn(1.0, 1.0)
        self.assertEqual(result, "G01 X1 Y1 Z{0:.4f}".format(expected_z))


    def test_g38_2_then_bare_move_not_levelled(self):
        state = new_levelling_state()
        level_gcode_line("G01 Z-0.1", state, self.offset_fn)
        level_gcode_line("G38.2 Z-2 F50", state, self.offset_fn)
        self.assertIsNone(state['G'])
        line = "X1 Y1"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)
        self.assertIsNone(state['G'])


#!/usr/bin/env python


DEC = 4
UP = 0.5


def up(x, y):
    """A height map that raises everything by 0.5, so a compensated line always differs."""
    return UP


def spatial(x, y):
    """Different physical XY positions have different height offsets."""
    return 0.01 * x * y + 0.5


def run(state, *lines):
    return [level_gcode_line(line, state, up, DEC) for line in lines]


def fresh(units='MM', *lines):
    """A state of the given job units, positioned by 'G90 G0 X0 Y0 Z5' after the given lines."""
    state = new_levelling_state(units=units)
    run(state, *lines)
    run(state, 'G90 G0 X0 Y0 Z5')
    return state


class TestUnitsState(unittest.TestCase):

    def test_units_argument(self):
        for text, code in (('MM', 21), ('IN', 20), ('mm', 21), ('in', 20), (None, None)):
            state = new_levelling_state(units=text)
            self.assertEqual(state['home_units'], code)
            self.assertEqual(state['units'], code)


class TestUnits(unittest.TestCase):
    def test_header_then_cuts_are_compensated(self):
        state = new_levelling_state(units='MM')
        out = run(state, 'G21', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1')
        self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(state['frame'], 0)

    def test_other_units_stop_the_compensation_and_the_same_come_back(self):
        state = fresh('MM')
        out = run(state, 'G1 X1 Y1 Z-1', 'G20', 'G1 X2 Y2 Z-1', 'X3 Y3', 'G91 G1 X1', 'G90')
        self.assertEqual(out[0], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(out[2:5], ['G1 X2 Y2 Z-1', 'X3 Y3', 'G91 G1 X1'])
        self.assertEqual(state['frame'], 3)
        out = run(state, 'G21', 'G1 X4 Y4 Z-1')
        self.assertEqual(out[-1], 'G1 X4 Y4 Z-0.5000')
        self.assertEqual(state['frame'], 3)


    def test_job_in_inches_with_a_metric_program(self):
        state = new_levelling_state(units='IN')
        out = run(state, 'G21', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1')
        self.assertEqual(out[-2:], ['G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1'])
        self.assertEqual(state['frame'], 2)


    def test_unknown_job_units_a_different_later_word_stops_the_compensation(self):
        state = new_levelling_state()
        out = run(state, 'G21', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', 'G20', 'G1 X2 Y2 Z-1',
                  'G21', 'G1 X3 Y3 Z-1')
        self.assertEqual(state['home_units'], 21)
        self.assertEqual(out[3], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(out[5], 'G1 X2 Y2 Z-1')
        self.assertEqual(out[7], 'G1 X3 Y3 Z-0.5000')
        self.assertEqual(state['frame'], 1)


class TestWcs(unittest.TestCase):

    def test_other_wcs_stops_the_compensation_and_the_same_comes_back(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G55', 'G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1', 'G54', 'G1 X3 Y3 Z-1')
        self.assertEqual(out[1:3], ['G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1'])
        self.assertEqual(out[4], 'G1 X3 Y3 Z-0.5000')
        self.assertEqual(state['frame'], 2)

    def test_wcs_word_loses_the_axes(self):
        state = fresh('MM', 'G54')
        run(state, 'G55')
        for axis in 'XYZ':
            self.assertIsNone(state[axis])
        self.assertEqual(state['known'], set())
        out = run(state, 'G54', 'G1 X5')
        self.assertEqual(out[1], 'G1 X5')
        self.assertEqual(state['unknown'], 1)

    def test_wcs_and_move_on_one_line(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G55 G1 X1 Y1 Z-1')
        self.assertEqual(out[0], 'G55 G1 X1 Y1 Z-1')
        self.assertEqual(state['frame'], 1)


    def test_every_wcs_word_is_a_frame_word(self):
        for code in ('G55', 'G56', 'G57', 'G58', 'G59', 'G59.1', 'G59.2', 'G59.3'):
            state = fresh('MM', 'G54')
            out = run(state, code, 'G1 X1 Y1 Z-1')
            self.assertEqual(out[1], 'G1 X1 Y1 Z-1', code)
            self.assertEqual(state['frame'], 1, code)


class TestOutOfFrameMoves(unittest.TestCase):
    def test_arc_is_not_split_and_keeps_its_g_word(self):
        state = fresh('MM', 'G54')
        run(state, 'G1 X10 Y0 Z-1')
        out = run(state, 'G55', 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0', 'X-10 Y0 I0 J-10')
        self.assertEqual(out[2], 'G3 X0 Y10 I-10 J0')
        self.assertEqual(out[3], 'G3 X-10 Y0 I0 J-10')
        self.assertEqual(state['arcs'], 2)
        self.assertEqual(state['arcs_split'], 0)
        self.assertEqual(state['frame'], 1)


    def test_relative_moves_are_not_changed_and_are_counted(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G55', 'G91', 'G1 X1 Y1 Z-1', 'G1 Z-0.5')
        self.assertEqual(out[2:], ['G1 X1 Y1 Z-1', 'G1 Z-0.5'])
        self.assertEqual(state['frame'], 2)
        self.assertEqual(state['relative'], 0)


class TestBlockDelete(unittest.TestCase):
    def test_optional_units_word_makes_the_units_unknown(self):
        state = fresh('MM')
        out = run(state, '/G20', 'G21', 'G1 X1 Y1 Z-1')
        self.assertEqual(out[0], '/G20')
        self.assertEqual(out[2], 'G1 X1 Y1 Z-0.5000')
        state = fresh('MM')
        out = run(state, '/G20', 'G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1')
        self.assertIsNone(state['units'])
        self.assertEqual(out[1:], ['G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1'])
        self.assertEqual(state['frame'], 2)


    def test_optional_wcs_word_makes_the_wcs_unknown(self):
        state = fresh('MM', 'G54')
        out = run(state, '/G55', 'G1 X1 Y1 Z-1')
        self.assertIsNone(state['wcs'])
        self.assertEqual(out[1], 'G1 X1 Y1 Z-1')
        self.assertEqual(state['frame'], 1)
        out = run(state, 'G54', 'G1 X2 Y2 Z-1')
        self.assertEqual(out[1], 'G1 X2 Y2 Z-0.5000')


class TestNoDefaultStart(unittest.TestCase):

    def test_cuts_with_an_unknown_axis_are_not_changed_and_are_counted(self):
        state = new_levelling_state()
        out = run(state, 'G1 Z-1', 'X10')
        self.assertEqual(out, ['G1 Z-1', 'X10'])
        self.assertEqual(state['unknown'], 2)


    def test_arc_from_an_unknown_start(self):
        state = new_levelling_state()
        out = run(state, 'G3 X0 Y10 I-10 J0')
        self.assertEqual(out[0], 'G3 X0 Y10 I-10 J0')
        self.assertEqual(state['arcs'], 1)


class TestToolPassesTheUnits(unittest.TestCase):
    def tool_and_target(self, gcode, units='MM', **target_attrs):
        from tests.test_levelling_audit import make_audit_tool
        tool = make_audit_tool(units=units or 'MM')
        tool._al_prepare_interp = MagicMock(return_value=True)
        tool._al_offset_fn = up
        tool._al_decimals = 4
        tool._al_points = [(0.0, 0.0, 0.5)]
        target = SimpleNamespace(tools={1: {'gcode': gcode}}, **target_attrs)
        if units is not None:
            target.units = units
        return tool, target


    def test_inch_job_with_a_metric_program_warns(self):
        from tests.test_levelling_audit import emitted
        tool, target = self.tool_and_target('G21\nG0 X0 Y0 Z5\nG1 X1 Y1 Z-1\n', units='IN')
        tool.autolevell_gcode(target, 'v')
        self.assertTrue(any('another coordinate system or units' in m and m.endswith(': 1')
                            for m in emitted(tool)))


class TestNoDeclarationAfterAMove(unittest.TestCase):
    """A WCS or units word after a move never declares the frame, also when the position is lost."""

    def check_after(self, *lost):
        state = new_levelling_state(units='MM')
        out = run(state, 'G0 X10 Y10 Z5', 'G1 Z-1', *lost, 'G55', 'G0 X10 Y10 Z5', 'G1 Z-1')
        self.assertEqual(out[1], 'G1 Z-0.5000')
        self.assertEqual(out[-1], 'G1 Z-1')
        self.assertIsNone(state['home_wcs'])
        self.assertEqual(state['frame'], 1)

    def test_after_g28(self):
        self.check_after('G28')


    def test_units_word_after_a_lost_position(self):
        state = new_levelling_state()
        out = run(state, 'G0 X10 Y10 Z5', 'G1 Z-1', 'G28', 'G20', 'G0 X1 Y1 Z1', 'G1 Z-0.1')
        self.assertIsNone(state['home_units'])
        self.assertEqual(out[-1], 'G1 Z-0.1')
        self.assertEqual(state['frame'], 1)


class TestWorkOriginShift(unittest.TestCase):
    """A line that moves the X/Y work origin puts the rest of the program out of the frame."""

    def check_shift(self, shift):
        # the G54 header makes the later 'G54' a return to the frame of the map
        state = fresh('MM', 'G54')
        out = run(state, 'G1 Z-1', shift, 'G0 X10 Y10 Z5', 'G1 Z-1', 'G54', 'G0 X1 Y1 Z5', 'G1 Z-1')
        self.assertEqual(out[0], 'G1 Z-0.5000')
        self.assertEqual(out[3], 'G1 Z-1')
        self.assertEqual(out[6], 'G1 Z-1')
        self.assertEqual(state['frame'], 2)

    def test_g92_xy(self):
        self.check_shift('G92 X0 Y0')


    def test_g10_l2(self):
        self.check_shift('G10 L2 P1 X5 Y5')


    def test_z_zero_without_a_probe_ends_the_frame(self):
        # the Z reference of the map is lost: no later word can restore it
        for shift in ('G92 Z0', 'G10 L2 P1 Z-3', 'G10 L20 P0 Z0', 'G52 Z1'):
            with self.subTest(shift=shift):
                state = fresh('MM', 'G54')
                out = run(state, shift, 'G54', 'G0 X1 Y1 Z5', 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])
                self.assertEqual(state['frame'], 1)


class TestFrameAuditRegressions(unittest.TestCase):
    def test_unresolved_origin_shift_cannot_resume_after_absolute_positions(self):
        for shift in ('G92 X[0] Y[0]', 'G92 X#1', 'G10 L20 P0 X[0] Y[0]',
                      'G10 L2 P1 X#1', 'G10 L2 P1 R[30]', 'G10 L[20] P1 X0',
                      'G10 L#1 P1 X0', '/G92 X[0]'):
            with self.subTest(shift=shift):
                state = new_levelling_state(units='MM')
                lines = ('G54', 'G0 X10 Y10 Z5', 'G1 Z-1', shift,
                         'G0 X10 Y10 Z5', 'G1 Z-1', 'G54', 'G0 X20 Y20 Z5', 'G1 Z-1')
                out = [level_gcode_line(line, state, spatial, DEC) for line in lines]
                self.assertEqual(out[2], 'G1 Z0.5000')
                self.assertEqual(out[3], shift)
                self.assertEqual(out[5], 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])
                self.assertEqual(state['frame'], 2)
                self.assertEqual(state['unparsed'], 1)


    def test_g10_targets_map_not_just_active_wcs(self):
        wcs_words = ('G54', 'G55', 'G56', 'G57', 'G58', 'G59', 'G59.1', 'G59.2', 'G59.3')
        for home_p, home in enumerate(wcs_words, 1):
            for active in (home, 'G55' if home == 'G54' else 'G54'):
                for p in range(10):
                    for change in ('L2 X5 Y5', 'L20 X0 Y0', 'L2 R30', 'L20 X[0] Y#1'):
                        with self.subTest(home=home, active=active, p=p, change=change):
                            state = fresh('MM', home)
                            level_gcode_line(active, state, spatial, DEC)
                            shift = 'G10 P%d %s' % (p, change)
                            self.assertEqual(level_gcode_line(shift, state, spatial, DEC), shift)
                            shifted = (active == home if p == 0 else p == home_p)
                            out = [level_gcode_line(line, state, spatial, DEC)
                                   for line in (home, 'G0 X10 Y10 Z5', 'G1 Z-1')]
                            self.assertEqual(out[-1], 'G1 Z-1' if shifted else 'G1 Z0.5000')
                            self.assertEqual(state['shifted'], shifted)
                            self.assertEqual(state['frame'], int(shifted))


    def test_expression_z_zero_ends_the_frame(self):
        # the new Z value can not be read, so the map can not be re-referenced
        for shift in ('G92 Z[0]', 'G10 L2 P1 Z#1', 'G10 L20 P0 Z[0]'):
            with self.subTest(shift=shift):
                # the probe is inside the map, so only the expression stops the re-reference
                state = new_levelling_state(units='MM', map_bounds=(0, 0, 20, 20))
                run(state, 'G54', 'G0 X10 Y10 Z5', 'G38.2 Z-5')
                out = [level_gcode_line(line, state, spatial, DEC)
                       for line in (shift, 'G0 X10 Y10 Z5', 'G1 Z-1')]
                self.assertEqual(out[0], shift)
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])


class TestHiddenGWords(unittest.TestCase):
    """A G word that the tool can not read (lower case, expression) can be any G code."""

    def test_hidden_g_word_never_comes_back(self):
        # G92.1/.2/.3 reset the X/Y origin with no X/Y word; G21 G54 can not undo that
        for hidden in ('g20', 'G[20]', 'G#1', 'g55', 'g92.1', 'G[92.1]', 'g92.2', 'g92.3',
                       'g92 x0 y0', 'G[92] X0 Y0', 'G#5 Y0', 'g10 l20 p1 x0', 'g10 l2 p1 r30',
                       'g68 r30', 'G1 g52 x1'):
            with self.subTest(hidden=hidden):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', hidden, 'G21 G54', 'G0 X10 Y10 Z5', 'G1 Z-1')
                self.assertEqual(out[0], 'G1 Z-0.5000')
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])
                self.assertEqual(state['frame'], 1)
                self.assertEqual(state['unparsed'], 1)


class TestSubprogramCall(unittest.TestCase):
    """The tool does not see the codes of a subprogram, so a call ends the frame."""

    def test_call_ends_the_frame(self):
        for call in ('O100 call', 'o100 call', 'O<probe> CALL', 'M98 P100', 'M98P100 L2',
                     'M098 P1', 'G65 P9001', 'N10 M98 P100', 'O100 sub', 'O100 endsub',
                     'o101 while [#1 LT 3]', 'O102 if [#1 GT 0]', 'O103 repeat [5]',
                     'O104 do', 'O100 return'):
            with self.subTest(call=call):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', call, 'G21 G54', 'G0 X10 Y10 Z5', 'G1 Z-1')
                self.assertEqual(out[0], 'G1 Z-0.5000')
                self.assertEqual(out[1], call)
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])


class TestCutterCompensation(unittest.TestCase):
    """Under G41/G42 the tool is off the programmed X/Y, so the map is read at the wrong point."""

    def test_moves_under_g41_g42_are_not_compensated(self):
        for comp in ('G41', 'G41 D1', 'G42 D1', 'G41.1 D2', 'G42.1 D2 L3'):
            with self.subTest(comp=comp):
                state = fresh('MM', 'G21 G54')
                out = run(state, comp, 'G1 X1 Y1 Z-1', 'X2 Y2', 'G91 G1 X1 Z-0.1', 'G90',
                          'G40', 'G1 X3 Y3 Z-1')
                self.assertEqual(out[1:4], ['G1 X1 Y1 Z-1', 'X2 Y2', 'G91 G1 X1 Z-0.1'])
                self.assertEqual(out[-1], 'G1 X3 Y3 Z-0.5000')
                self.assertEqual(state['cutter'], 3)
                self.assertEqual(state['relative'], 0)


    def test_arcs_under_comp_are_not_split(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G0 X10 Y0 Z-1', 'G41 D1', 'G3 X0 Y10 I-10 J0')
        self.assertNotIn('\n', out[-1])
        self.assertEqual(state['arcs'], 1)
        self.assertEqual(state['arcs_split'], 0)


    def test_optional_comp_words_make_comp_unknown(self):
        for lines in (('/G41 D1',), ('G41 D1', '/G40')):
            with self.subTest(lines=lines):
                state = fresh('MM', 'G21 G54')
                out = run(state, *lines, 'G1 X1 Y1 Z-1')
                self.assertEqual(out[-1], 'G1 X1 Y1 Z-1')
                self.assertIsNone(state['cutter_comp'])
                self.assertEqual(state['cutter'], 1)
                out = run(state, 'G40', 'G1 X1 Y1 Z-1')
                self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')


class TestLeadingComment(unittest.TestCase):
    """A comment at the start of a line can not hide the commands after it."""


    def test_move_after_a_leading_comment_is_compensated(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, '(cut) G1 X10 Y10 Z-1', '(next) X20 Y20', '(c) X30 (d) Y30 ; e')
        self.assertEqual(out, ['(cut) G1 X10 Y10 Z-0.5000', '(next) X20 Y20 Z-0.5000',
                               '(c) X30 (d) Y30 Z-0.5000 ; e'])


class TestSlashInComment(unittest.TestCase):
    """A "/" in a comment is not a block delete; only a "/" at the start of the line is."""

    def arc(self, line):
        state = fresh('MM', 'G21 G54')
        run(state, 'G0 X10 Y0 Z-1')
        return state, level_gcode_line(line, state, up, DEC).split('\n')


    def test_real_block_delete_arc_is_still_optional(self):
        for line in ('/(arc cut) G3 X0 Y10 I-10 J0', ' / G3 X0 Y10 I-10 J0 (a / b)'):
            with self.subTest(line=line):
                state, out = self.arc(line)
                self.assertGreater(len(out), 1)
                self.assertTrue(all(text.startswith('/') for text in out))
                self.assertIsNone(state['applied'])


def slope(x, y):
    """A sloped map: h(0,0) = 0, h(5,0) = 0.1, h(10,10) = 0.5."""
    return 0.02 * x + 0.03 * y


def run_on(fn, state, *lines):
    return [level_gcode_line(line, state, fn, DEC) for line in lines]


def mapped(*lines):
    """A state with a 20 x 20 map, in G54, at X0 Y0 Z5."""
    state = new_levelling_state(units='MM', map_bounds=(0, 0, 20, 20))
    run_on(slope, state, 'G21 G54', 'G0 X0 Y0 Z5', *lines)
    return state


class TestZZero(unittest.TestCase):
    """A Z zero change after a contact probe re-references the map to the probed point."""


    def test_grbl_probe_and_g10_l20(self):
        for zero in ('G10 L20 P0 Z0', 'G10 L20 P1 Z0', 'G92 Z0'):
            with self.subTest(zero=zero):
                state = mapped()
                out = run_on(slope, state, 'G0 X5 Y0', 'G38.2 Z-10 F50', zero, 'G0 Z2',
                             'G0 X10 Y10', 'G1 Z-0.1')
                self.assertEqual(out[-1], 'G1 Z0.3000')


    def test_no_reference_ends_the_frame(self):
        cases = {
            'probe outside the map': ('G0 X30 Y0', 'G38.2 Z-10', 'G92 Z0'),
            'probe with X/Y words': ('G0 X5 Y0', 'G38.2 X6 Z-10', 'G92 Z0'),
            'a move after the probe': ('G0 X5 Y0', 'G38.2 Z-10', 'G0 Z1', 'G92 Z0'),
            'probe without a contact check': ('G0 X5 Y0', 'G38.3 Z-10', 'G92 Z0'),
            'no probe': ('G0 X5 Y0', 'G92 Z0'),
            'G10 L2 sets the offset by value': ('G0 X5 Y0', 'G38.2 Z-10', 'G10 L2 P1 Z-3'),
            'optional zero': ('G0 X5 Y0', 'G38.2 Z-10', '/G92 Z0'),
            'optional probe': ('G0 X5 Y0', '/G38.2 Z-10', 'G92 Z0'),
            'probe at an unknown X/Y': ('G28', 'G38.2 Z-10', 'G92 Z0'),
        }
        for name, lines in cases.items():
            with self.subTest(name):
                state = mapped()
                out = run_on(slope, state, *lines, 'G0 Z5', 'G0 X10 Y10 Z5', 'G1 Z-0.1')
                self.assertEqual(out[-1], 'G1 Z-0.1')
                self.assertTrue(state['shifted'])

    def test_mach3_preprocessor_output(self):
        # the real start code and tool change code of Toolchange_Probe_MACH3
        from camlib import AttrDict
        from preprocessors.Toolchange_Probe_MACH3 import Toolchange_Probe_MACH3
        p = AttrDict(
            units='MM', decimals=4, coords_decimals=4, fr_decimals=2, tool=2, toolC=0.1,
            xy_toolchange=(5.0, 0.0), z_toolchange=15.0, startz=None, z_end=15.0, xy_end=None,
            z_move=2.0, z_p_depth=-10.0, feedrate_probe=100.0, f_plunge=False, toolchange=True,
            feedrate=100.0, z_feedrate=50.0, feedrate_rapid=500.0, z_cut=-0.1, multidepth=False,
            steps_per_circle=16, pp_geometry_name='Toolchange_Probe_MACH3', spindlespeed=10000,
            obj_options={'type': 'Geometry', 'tool_dia': 0.1, 'xmin': 0.0, 'xmax': 20.0,
                         'ymin': 0.0, 'ymax': 20.0})
        pp = Toolchange_Probe_MACH3()
        code = pp.start_code(p) + pp.toolchange_code(p)
        self.assertIn('G31 Z-10.0000', code)
        self.assertIn('G92 Z0', code)
        state = new_levelling_state(units='MM', map_bounds=(0, 0, 20, 20))
        run_on(slope, state, *code.splitlines())
        out = run_on(slope, state, 'G00 X10 Y10', 'G01 Z-0.1', 'G01 X0 Y0')
        # the probe touched the surface at (5, 0): -0.1 + h(x, y) - h(5, 0)
        self.assertEqual(out[1:], ['G01 Z0.3000', 'G01 X0 Y0 Z-0.2000'])
        self.assertEqual((state['frame'], state['shifted']), (0, False))


class TestExtraWcs(unittest.TestCase):
    """Mach3 "G59 P<n>" and Fanuc "G54.1 P<n>": each P value is its own coordinate system."""

    def test_each_p_value_is_a_wcs(self):
        for home, other in (('G59 P7', 'G59 P8'), ('G54.1 P1', 'G54.1 P2'), ('G59', 'G59 P7'),
                            ('G59 P7', 'G59')):
            with self.subTest(home=home, other=other):
                state = fresh('MM', home)
                out = run(state, 'G1 Z-1', other, 'G0 X0 Y0 Z5', 'G1 Z-1', home, 'G0 X0 Y0 Z5', 'G1 Z-1')
                self.assertEqual(out[0], 'G1 Z-0.5000')
                self.assertEqual(out[3], 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-0.5000')
                self.assertEqual(state['frame'], 1)


    def test_g10_on_an_extra_wcs_is_conservative(self):
        state = fresh('MM', 'G59 P7')
        out = run(state, 'G10 L2 P7 X5', 'G0 X0 Y0 Z5', 'G1 Z-1')
        self.assertEqual(out[-1], 'G1 Z-1')
        self.assertTrue(state['shifted'])


class TestCannedCycles(unittest.TestCase):
    """Canned cycle moves are not compensated and are reported."""

    def test_cycle_moves_are_counted(self):
        for cycle in ('G81', 'G82', 'G83', 'G73', 'G84', 'G85', 'G89', 'G98 G81', 'G99 G83'):
            with self.subTest(cycle=cycle):
                state = fresh('MM', 'G21 G54')
                out = run(state, cycle + ' X1 Y1 Z-1 R1 F100', 'X2 Y2', 'Y3', 'G80', 'G0 X4 Y4 Z5',
                          'G1 Z-1')
                self.assertEqual(out[:3], [cycle + ' X1 Y1 Z-1 R1 F100', 'X2 Y2', 'Y3'])
                self.assertEqual(out[-1], 'G1 Z-0.5000')
                self.assertEqual(state['cycles'], 3)
                self.assertEqual(state['unknown'], 0)

    def test_z_is_unknown_after_a_cycle(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G81 X1 Y1 Z-1 R1', 'G80', 'G1 X2 Y2')
        self.assertEqual(out[-1], 'G1 X2 Y2')
        self.assertEqual(state['unknown'], 1)
        self.assertEqual((state['X'], state['Y']), (2.0, 2.0))


class TestUnknownGCodes(unittest.TestCase):
    """A G code that the tool does not know can move the machine."""

    def test_unknown_code_makes_the_position_unknown(self):
        for code in ('G77', 'G76', 'G12 I5', 'G13 I5', 'G5 X1 Y1 I1 J1 P1 Q1', 'G33 Z-1 K1',
                     'G5.2 X1 Y1', 'G123'):
            with self.subTest(code=code):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', code, 'G1 Z-1', 'G0 X1 Y1 Z5', 'G1 Z-1')
                self.assertEqual(out[0], 'G1 Z-0.5000')
                self.assertEqual(out[2], 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-0.5000')


    def test_tool_length_offset_makes_z_unknown(self):
        # G43.1 Z1 is a tool length, not a position: before the fix it gave 'G1 X1 Y1 Z1.5000'
        for code in ('G43 H1', 'G43.1 Z1', 'G43.2 H2', 'G49'):
            with self.subTest(code=code):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', code, 'G1 X1 Y1', 'G0 Z5', 'G1 Z-1')
                self.assertEqual(out[2], 'G1 X1 Y1')
                self.assertEqual(state['unknown'], 1)
                self.assertEqual(out[-1], 'G1 Z-0.5000')


    def test_codes_that_change_the_meaning_of_x_y_end_the_frame(self):
        for code in ('G16', 'G7', 'G51.1 X0', 'G51 P2', 'G68 R30'):
            with self.subTest(code=code):
                state = fresh('MM', 'G21 G54')
                out = run(state, code, 'G0 X1 Y1 Z5', 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])


class TestG53Regressions(unittest.TestCase):
    @staticmethod
    def offset_fn(x, y):
        return 0.01 * x + 0.02 * y + 0.5

    def setUp(self):
        self.state = new_levelling_state()
        level_gcode_line("G1 X0 Y0 Z-1", self.state, self.offset_fn)


    def test_g53_g1_does_not_reuse_stale_work_z(self):
        line = "G53 G1 Z0"
        self.assertEqual(level_gcode_line(line, self.state, self.offset_fn), line)
        self.assertEqual(
            level_gcode_line("X1 Y1", self.state, self.offset_fn), "X1 Y1"
        )
        self.assertEqual(
            level_gcode_line("G1 X2 Y3 Z-1", self.state, self.offset_fn),
            "G1 X2 Y3 Z-0.4200"
        )
        self.assertEqual(
            level_gcode_line("X3 Y4", self.state, self.offset_fn),
            "X3 Y4 Z-0.3900"
        )

    def test_g53_xy_requires_known_work_xy_before_compensation(self):
        line = "G53 G1 X100 Y200"
        self.assertEqual(level_gcode_line(line, self.state, self.offset_fn), line)
        self.assertEqual(
            level_gcode_line("G1 Z-2", self.state, self.offset_fn), "G1 Z-2"
        )
        self.assertEqual(
            level_gcode_line("X1", self.state, self.offset_fn), "X1"
        )
        self.assertEqual(
            level_gcode_line("Y2", self.state, self.offset_fn), "Y2 Z-1.4500"
        )


class TestG53Compatibility(unittest.TestCase):
    @staticmethod
    def offset_fn(x, y):
        return 0.5


    def test_ordinary_rapids_and_positive_z_feed_policy(self):
        state = new_levelling_state()
        line = "G0 X0 Y0 Z2"
        self.assertEqual(level_gcode_line(line, state, self.offset_fn), line)
        self.assertEqual(
            level_gcode_line("G1 X1 Y1", state, self.offset_fn),
            "G1 X1 Y1 Z2.5000"
        )
        # relative moves from a known position are compensated too (G91 delta)
        self.assertEqual(
            level_gcode_line("G91 G1 X1 Y1 Z1", state, self.offset_fn),
            "G91 G1 X1 Y1 Z1.0000"
        )
        self.assertEqual(
            level_gcode_line("G90 G1 X3 Y3", state, self.offset_fn),
            "G90 G1 X3 Y3 Z3.5000"
        )
