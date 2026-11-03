#!/usr/bin/env python
"""
Arc (G2/G3) and relative (G91) height compensation in level_gcode_line().

A machine simulator executes the emitted G-code, so the checks look at where the
tool really goes, not only at the text.

Run: python -m pytest tests/test_levelling_arc_g91.py -q
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from appPlugins.Common import levelling_interp as li
import appPlugins.Common.levelling_core as lc_module
from appPlugins.Common.levelling_interp import level_gcode_line, new_levelling_state
import random

DEC = 4
TOL = 10 ** -DEC

_WORD = re.compile(r'([A-Z])\s*([+\-]?(?:\d+\.?\d*|\.\d+))')


def plane(x, y):
    return math.sqrt(2.0) / 100.0 * x - math.pi / 500.0 * y + 0.1


def flat(x, y):
    return 0.0


def strip_comments(line):
    line = re.sub(r'\([^)]*\)', '', line)
    return line.split(';')[0]


def words(line):
    """dict letter -> last value of the line, without comments."""
    return {letter: float(num) for letter, num in _WORD.findall(strip_comments(line))}


class Machine:
    """Executes G0/G1 text with G90/G91, as a controller would (no arcs)."""

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z
        self.absolute = True
        self.g = 0
        self.trace = []

    def feed(self, text):
        for line in text.split('\n'):
            found = _WORD.findall(strip_comments(line))
            gs = [float(n) for letter, n in found if letter == 'G']
            assert 2.0 not in gs and 3.0 not in gs, 'arc left in the output: %r' % line
            if 90.0 in gs:
                self.absolute = True
            if 91.0 in gs:
                self.absolute = False
            if 0.0 in gs:
                self.g = 0
            if 1.0 in gs:
                self.g = 1
            moved = {letter: float(n) for letter, n in found if letter in 'XYZ'}
            if not moved:
                continue
            for axis, value in moved.items():
                attr = axis.lower()
                setattr(self, attr, value if self.absolute else getattr(self, attr) + value)
            self.trace.append((self.x, self.y, self.z, self.g))


def run(state, offset, *lines):
    """Feed lines to level_gcode_line, return the list of the outputs."""
    return [level_gcode_line(line, state, offset, DEC) for line in lines]


def prime(x, y, z, offset=flat, arc_segment=0.5):
    """A state whose X/Y/Z are known after 'G90 G1 X.. Y.. Z..'. Returns (state, output)."""
    state = new_levelling_state(arc_segment=arc_segment)
    out = level_gcode_line('G90 G1 X%s Y%s Z%s' % (x, y, z), state, offset, DEC)
    return state, out


def points_of(out):
    return [(w['X'], w['Y']) for w in map(words, out.split('\n'))]


class TestNewState(unittest.TestCase):

    def test_arc_segment_must_be_positive(self):
        for bad in (0, 0.0, -1.0):
            with self.assertRaises(ValueError):
                new_levelling_state(arc_segment=bad)


class TestArcSplitAbsolute(unittest.TestCase):
    def assert_on_circle(self, pts, centre, r):
        for x, y in pts:
            self.assertAlmostEqual(math.hypot(x - centre[0], y - centre[1]), r, delta=2 * TOL)

    def split(self, start, line, offset=flat, z=-1.0):
        state, _ = prime(start[0], start[1], z, offset)
        out = level_gcode_line(line, state, offset, DEC)
        return state, out

    def test_g3_quarter_arc(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0')
        lines = out.split('\n')
        self.assertGreater(len(lines), 8)
        for ln in lines:
            self.assertTrue(ln.startswith('G1 X'), ln)
            self.assertIsNone(re.search(r'G0?[23]\b', ln))
        pts = points_of(out)
        self.assert_on_circle(pts, (0, 0), 10)
        self.assertEqual(pts[-1], (0.0, 10.0))
        for ln in lines:
            self.assertAlmostEqual(words(ln)['Z'], -1.0, places=DEC)
        self.assertEqual(state['arcs_split'], 1)
        self.assertEqual(state['arcs'], 0)
        self.assertEqual(state['G'], 3)
        self.assertEqual((state['X'], state['Y'], state['Z']), (0.0, 10.0, -1.0))

    def test_g2_quarter_arc(self):
        state, out = self.split((0, 10), 'G2 X10 Y0 I0 J-10')
        for ln in out.split('\n'):
            self.assertTrue(ln.startswith('G1 X'), ln)
        pts = points_of(out)
        self.assert_on_circle(pts, (0, 0), 10)
        self.assertEqual(pts[-1], (10.0, 0.0))
        # clockwise from the top to the right: every point is in the first quadrant
        for x, y in pts:
            self.assertGreaterEqual(x, -TOL)
            self.assertGreaterEqual(y, -TOL)
        self.assertEqual(state['G'], 2)

    def test_segment_length_and_angle_limits(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0')
        pts = [(10.0, 0.0)] + points_of(out)
        for a, b in zip(pts, pts[1:]):
            self.assertLessEqual(math.hypot(b[0] - a[0], b[1] - a[1]), 0.5 + TOL)


    def test_plane_offset_on_every_segment(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0', offset=plane)
        for ln in out.split('\n'):
            w = words(ln)
            self.assertAlmostEqual(w['Z'], -1.0 + plane(w['X'], w['Y']), delta=TOL)
        self.assertAlmostEqual(state['applied'], plane(0, 10), delta=TOL)

    def test_r_format_all_four_cases(self):
        cases = [
            # (line, centre, test of the points)
            ('G3 X0 Y10 R10', (0, 0)),
            ('G3 X0 Y10 R-10', (10, 10)),
            ('G2 X0 Y10 R10', (10, 10)),
            ('G2 X0 Y10 R-10', (0, 0)),
        ]
        sizes = {}
        for line, centre in cases:
            with self.subTest(line=line):
                state, out = self.split((10, 0), line)
                pts = points_of(out)
                self.assert_on_circle(pts, centre, 10)
                self.assertEqual(pts[-1], (0.0, 10.0))
                self.assertEqual(state['arcs_split'], 1)
                sizes[line] = pts
        self.assertGreater(max(x for x, _ in sizes['G3 X0 Y10 R-10']), 19.9)   # the long way, to the right
        self.assertLess(min(x for x, _ in sizes['G2 X0 Y10 R-10']), -9.9)      # the long way, to the left
        for x, y in sizes['G3 X0 Y10 R10']:                                    # the short way
            self.assertGreaterEqual(x, -TOL)
        for x, y in sizes['G2 X0 Y10 R10']:
            self.assertLessEqual(x, 10 + TOL)
            self.assertGreaterEqual(x, -TOL)
        self.assertGreater(len(sizes['G3 X0 Y10 R-10']), 2 * len(sizes['G3 X0 Y10 R10']))


    def test_full_circle_with_endpoint_words(self):
        for line, sign in (('G3 X10 Y0 I-10 J0', 1), ('G2 X10 Y0 I-10 J0', -1), ('G2 I-10 J0', -1)):
            with self.subTest(line=line):
                state, out = self.split((10, 0), line)
                pts = [(10.0, 0.0)] + points_of(out)
                self.assert_on_circle(pts, (0, 0), 10)
                self.assertEqual(pts[-1], (10.0, 0.0))
                self.assertGreaterEqual(len(pts) - 1, 36)
                total = 0.0
                for a, b in zip(pts, pts[1:]):
                    step = math.atan2(b[1], b[0]) - math.atan2(a[1], a[0])
                    step = (step + math.pi) % (2 * math.pi) - math.pi
                    self.assertLessEqual(abs(step), math.radians(10) + 1e-3)
                    total += step
                self.assertAlmostEqual(total, sign * 2 * math.pi, places=3)
                self.assertEqual(state['arcs_split'], 1)

    def test_helical_arc(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 Z-2 I-10 J0')
        zs = [words(ln)['Z'] for ln in out.split('\n')]
        n = len(zs)
        for i, z in enumerate(zs, start=1):
            self.assertAlmostEqual(z, -1.0 - i / n, delta=TOL)
        self.assertEqual(zs[-1], -2.0)
        self.assertEqual(state['Z'], -2.0)


    def test_feed_and_comment_only_on_first_segment(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0 F150 (quarter arc)')
        lines = out.split('\n')
        self.assertTrue(lines[0].startswith('G1 X'))
        self.assertIn(' F150', lines[0])
        self.assertTrue(lines[0].endswith(' (quarter arc)'), lines[0])
        for ln in lines[1:]:
            self.assertNotIn('F', ln)
            self.assertNotIn('(', ln)


class TestArcNotCompensated(unittest.TestCase):
    LINE = 'G3 X0 Y10 I-10 J0'

    def check(self, line, setup=(), primed=True, **expect):
        if primed:
            state, _ = prime(10, 0, -1)
        else:
            state = new_levelling_state()
        for pre in setup:
            run(state, flat, pre)
        out = level_gcode_line(line, state, flat, DEC)
        self.assertEqual(out, line)
        self.assertEqual(state['arcs'], 1)
        self.assertEqual(state['arcs_split'], 0)
        return state

    def test_radius_mismatch(self):
        self.check('G2 X0 Y10 I-9 J0')

    def test_g18_plane(self):
        self.check(self.LINE, setup=['G18'])


    def test_inverse_feed(self):
        self.check('G3 X0 Y10 I-10 J0 F2', setup=['G93'])

    def test_unknown_start(self):
        state = self.check(self.LINE, primed=False)
        self.assertEqual((state['X'], state['Y']), (0.0, 10.0))


    def test_p2(self):
        self.check('G3 X0 Y10 I-10 J0 P2')


    def test_zero_radius(self):
        self.check('G3 X10 Y0 I0 J0')

    def test_state_is_updated_and_applied_cleared(self):
        state = self.check('G2 X0 Y10 Z2 I-9 J0')
        self.assertEqual((state['X'], state['Y'], state['Z']), (0.0, 10.0, 2.0))
        self.assertEqual(state['applied'], 0.0)
        self.assertEqual(state['G'], 2)


    def test_bare_bad_arc_after_split_gets_explicit_word(self):
        for word, line in (('G3', 'G3 X0 Y10 I-10 J0'), ('G2', 'G2 X0 Y10 I0 J-10')):
            with self.subTest(word=word):
                state, _ = prime(10 if word == 'G3' else 0, 0 if word == 'G3' else 10, -1)
                level_gcode_line(line, state, flat, DEC)
                self.assertEqual(state['arcs_split'], 1)
                out = level_gcode_line('X5 Y5 I1 J0', state, flat, DEC)
                self.assertEqual(out, word + ' X5 Y5 I1 J0')
                self.assertEqual(state['arcs'], 1)


class TestAppliedOffset(unittest.TestCase):

    def test_g0_with_z_clears_applied_and_g0_without_z_keeps_it(self):
        state, _ = prime(3, 4, -1, plane)
        before = state['applied']
        run(state, plane, 'G0 X5 Y5')
        self.assertEqual(state['applied'], before)
        run(state, plane, 'G0 Z5')
        self.assertEqual(state['applied'], 0.0)


class TestRelativeG1(unittest.TestCase):
    def test_constant_offset_exact_line(self):
        off = lambda x, y: 0.5
        state, _ = prime(0, 0, -1, off)
        run(state, off, 'G91')
        self.assertEqual(level_gcode_line('G1 X1 Y1 Z-0.5', state, off, DEC), 'G1 X1 Y1 Z-0.5000')
        self.assertAlmostEqual(state['applied'], 0.5)


    def test_z_word_is_added_when_the_surface_moves(self):
        state, _ = prime(0, 0, -1, plane)
        run(state, plane, 'G91')
        out = level_gcode_line('G1 X10 Y0 (cut)', state, plane, DEC)
        self.assertEqual(out, 'G1 X10 Y0 Z%.4f (cut)' % (plane(10, 0) - plane(0, 0)))

    def sequence(self, moves, offset=plane):
        """Prime at (0,0,-1), then relative moves; check machine Z == programmed Z + offset at every cut."""
        state, out = prime(0, 0, -1, offset)
        machine = Machine()
        machine.feed(out)
        run(state, offset, 'G91')
        machine.absolute = False
        px, py, pz = 0.0, 0.0, -1.0
        for dx, dy, dz in moves:
            parts = ['G1']
            if dx is not None:
                parts.append('X%.4f' % dx)
                px += dx
            if dy is not None:
                parts.append('Y%.4f' % dy)
                py += dy
            if dz is not None:
                parts.append('Z%.4f' % dz)
                pz += dz
            out = level_gcode_line(' '.join(parts), state, offset, DEC)
            machine.feed(out)
            self.assertAlmostEqual(machine.z, pz + offset(px, py), delta=TOL,
                                   msg='after %r -> %r' % (parts, out))
        return state, machine

    def test_sequence_follows_the_surface(self):
        moves = [(1.0, 0.5, None), (0.3, None, -0.2), (None, 2.0, None), (-4.0, -1.0, 0.7),
                 (5.0, 5.0, -0.1), (0.0, 0.0, -0.3), (12.5, -3.25, None)]
        state, machine = self.sequence(moves)
        self.assertEqual(state['relative'], 0)


    def test_mixed_absolute_and_relative(self):
        state, out = prime(0, 0, -1, plane)
        machine = Machine()
        machine.feed(out)
        out = level_gcode_line('G1 X5 Y0', state, plane, DEC)          # absolute, compensated
        machine.feed(out)
        self.assertAlmostEqual(machine.z, -1 + plane(5, 0), delta=TOL)
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        machine.feed(level_gcode_line('G1 X1 Y1 Z-0.5', state, plane, DEC))
        self.assertAlmostEqual(machine.z, -1.5 + plane(6, 1), delta=TOL)
        machine.feed(level_gcode_line('G90', state, plane, DEC))
        machine.feed(level_gcode_line('G0 Z5', state, plane, DEC))
        self.assertEqual(state['applied'], 0.0)
        self.assertEqual(machine.z, 5.0)
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        machine.feed(level_gcode_line('G1 X1 Y1 Z-6', state, plane, DEC))
        self.assertAlmostEqual(machine.z, -1.0 + plane(7, 2), delta=TOL)
        self.assertAlmostEqual(state['applied'], plane(7, 2), delta=TOL)

    def test_relative_rapid_is_not_changed(self):
        state, _ = prime(0, 0, -1, plane)
        run(state, plane, 'G91')
        before = state['applied']
        self.assertEqual(level_gcode_line('G0 X5 Y5 Z2', state, plane, DEC), 'G0 X5 Y5 Z2')
        self.assertEqual(state['applied'], before)
        self.assertEqual(state['relative'], 0)


class TestArcsInRelativeMode(unittest.TestCase):
    def check_relative_arc(self, state, machine, out, end, z_end, offset):
        machine.feed(out)
        self.assertAlmostEqual(machine.x, end[0], places=8)
        self.assertAlmostEqual(machine.y, end[1], places=8)
        self.assertAlmostEqual(machine.z, z_end + offset(*end), delta=TOL)

    def test_g91_on_the_arc_line(self):
        state, out = prime(0, 10, -1, plane)
        machine = Machine()
        machine.feed(out)
        arc = level_gcode_line('G91 G2 X10 Y-10 I0 J-10', state, plane, DEC)
        self.assertTrue(arc.startswith('G91 G1 X'), arc.split('\n')[0])
        n_before = len(machine.trace)
        self.check_relative_arc(state, machine, arc, (10.0, 0.0), -1.0, plane)
        for x, y, z, g in machine.trace[n_before:]:
            self.assertAlmostEqual(math.hypot(x, y), 10.0, delta=2 * TOL)
        self.assertFalse(state['abs'])
        self.assertEqual(state['arcs_split'], 1)
        self.assertAlmostEqual(state['applied'], plane(10, 0), delta=TOL)
        # sum of the X/Y deltas is the programmed delta
        sx = sum(words(ln)['X'] for ln in arc.split('\n'))
        sy = sum(words(ln)['Y'] for ln in arc.split('\n'))
        self.assertAlmostEqual(sx, 10.0, places=8)
        self.assertAlmostEqual(sy, -10.0, places=8)


    def test_full_circle_in_g91_mode_returns_to_the_start(self):
        state, out = prime(10, 0, -1, plane)
        machine = Machine()
        machine.feed(out)
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        arc = level_gcode_line('G2 I-10 J0', state, plane, DEC)
        self.check_relative_arc(state, machine, arc, (10.0, 0.0), -1.0, plane)


class TestPositionLoss(unittest.TestCase):
    def setUp(self):
        self.state, _ = prime(1, 2, -1, plane)

    def relative_move_is_counted(self):
        before = self.state['relative']
        run(self.state, plane, 'G91')
        line = 'G1 X1 Y1 Z-1'
        self.assertEqual(level_gcode_line(line, self.state, plane, DEC), line)
        self.assertEqual(self.state['relative'], before + 1)

    def test_g28_z(self):
        self.assertEqual(level_gcode_line('G28 Z0', self.state, plane, DEC), 'G28 Z0')
        self.assertIsNone(self.state['Z'])
        self.assertEqual(self.state['known'], {'X', 'Y'})
        self.assertEqual((self.state['X'], self.state['Y']), (1.0, 2.0))
        self.relative_move_is_counted()


class TestModalWordPlacement(unittest.TestCase):
    """The modal G2/G3 word of an unchanged arc goes after '/' and 'N<digits>'."""

    def check(self, line, expected):
        state, _ = prime(10, 0, -1)
        level_gcode_line('G3 X0 Y10 I-10 J0', state, flat, DEC)        # split: the machine is in G1
        self.assertEqual(state['arcs_split'], 1)
        out = level_gcode_line(line, state, flat, DEC)
        self.assertEqual(out, expected)
        self.assertEqual(state['arcs'], 1)


    def test_line_number(self):
        self.check('N10 X5 Y5 I1 J0', 'N10 G3 X5 Y5 I1 J0')
        self.check('N 10 X5 Y5 I1 J0', 'N 10 G3 X5 Y5 I1 J0')
        self.check('N10X5 Y5 I1 J0', 'N10G3 X5 Y5 I1 J0')


class TestCoordinateSystemLoss(unittest.TestCase):
    """G92 and G10 L2/L20 move the work coordinate system: tracked positions are lost."""

    def lost(self, line):
        state, _ = prime(1, 2, -1, plane)
        self.assertEqual(level_gcode_line(line, state, plane, DEC), line)
        return {axis for axis in 'XYZ' if state[axis] is None}, state

    def test_g92_loses_named_axes(self):
        self.assertEqual(self.lost('G92 Z0')[0], {'Z'})
        self.assertEqual(self.lost('G92 X0 Y0')[0], {'X', 'Y'})
        self.assertEqual(self.lost('G92 X0 Y0 Z0')[0], {'X', 'Y', 'Z'})


    def test_relative_move_is_counted_until_an_absolute_word(self):
        state, _ = prime(1, 2, -1, plane)
        run(state, plane, 'G92 Z0', 'G91')
        line = 'G1 X1 Y1 Z-1'
        self.assertEqual(level_gcode_line(line, state, plane, DEC), line)
        # a G92 Z0 with no probe before it ends the frame of the map (see test_levelling_frame)
        self.assertEqual(state['frame'], 1)

    def test_arc_after_g92_is_not_split(self):
        state, _ = prime(10, 0, -1, plane)
        run(state, plane, 'G92 X0')
        line = 'G3 X0 Y10 I-10 J0'
        self.assertEqual(level_gcode_line(line, state, plane, DEC), line)
        self.assertEqual(state['arcs'], 1)
        self.assertEqual(state['arcs_split'], 0)


class TestToolPassesArcSegment(unittest.TestCase):
    def tool_and_target(self, gcode, units='MM', **target_attrs):
        from tests.test_levelling_audit import make_audit_tool
        from appPlugins.ToolLevelling import ToolLevelling
        # the seg_x / seg_y rule is the one of the mixin; the GRBL Sender has its own arc segment
        tool = make_audit_tool(units=units, cls=ToolLevelling)
        tool._al_prepare_interp = MagicMock(return_value=True)
        tool._al_offset_fn = lambda x, y: 0.5
        tool._al_decimals = 4
        tool._al_points = [(0.0, 0.0, 0.5)]
        target = SimpleNamespace(tools={1: {'gcode': gcode}}, units=units, **target_attrs)
        return tool, target

    def segment_used(self, **kwargs):
        tool, target = self.tool_and_target('G1 Z-1\n', **kwargs)
        with patch.object(lc_module, 'new_levelling_state', wraps=li.new_levelling_state) as spy:
            tool.autolevell_gcode(target, 'v')
        return spy.call_args.kwargs['arc_segment']

    def test_smaller_of_seg_x_and_seg_y(self):
        self.assertEqual(self.segment_used(seg_x=2.0, seg_y=1.5), 1.5)
        self.assertEqual(self.segment_used(seg_x=0.8, seg_y=1.5), 0.8)


    def test_units_fallback(self):
        self.assertEqual(self.segment_used(), 1.0)
        self.assertEqual(self.segment_used(seg_x=0, seg_y=0), 1.0)
        self.assertEqual(self.segment_used(units='IN'), 0.04)


class TestShortArcIsNotAFullCircle(unittest.TestCase):
    # review of 6a302b32: endpoints 1 output unit apart are a short arc, not 360 degrees
    def test_tiny_ccw_arc_stays_short(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G3 X10 Y0.0001 I-10 J0', state, flat, DEC)
        for x, y in points_of(out):
            self.assertGreater(x, 9.99)
            self.assertLess(abs(y), 0.01)
        self.assertEqual(points_of(out)[-1], (10.0, 0.0001))


    def test_equal_ends_are_still_a_full_circle(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G3 X10 Y0 I-10 J0', state, flat, DEC)
        self.assertLess(min(x for x, _y in points_of(out)), -9.9)
        self.assertEqual(state['arcs_split'], 1)


class TestBlockDelete(unittest.TestCase):
    # review of 6a302b32: an optional "/" line may be skipped by the controller
    def test_arc_after_optional_arc_is_not_split(self):
        state, _ = prime(10, 0, -1)
        level_gcode_line('G90.1', state, flat, DEC)
        out = run(state, flat, '/G3 X0 Y10 I0 J0', 'G3 X-10 Y0 I0 J0')
        self.assertTrue(out[0].startswith('/'))
        self.assertEqual(out[1], 'G3 X-10 Y0 I0 J0')
        self.assertEqual(state['arcs'], 1)

    def test_relative_move_after_optional_arc_is_not_compensated(self):
        def saddle(x, y):
            return 0.01 * x * y
        state, _ = prime(10, 0, -1, saddle)
        out = run(state, saddle, '/G3 X0 Y10 I-10 J0', 'G91 G1 X1 Y0')
        self.assertEqual(out[1], 'G91 G1 X1 Y0')
        self.assertEqual(state['relative'], 1)


    def test_optional_mode_change_makes_the_mode_unknown(self):
        state, _ = prime(10, 0, -1, plane)
        out = run(state, plane, '/G91', 'G1 X1 Y1', 'G90 G1 X2 Y2 Z-1', 'G1 X3 Y3')
        self.assertEqual(out[1], 'G1 X1 Y1')
        self.assertIn('Z%.4f' % (-1 + plane(2, 2)), out[2])
        self.assertIn('Z%.4f' % (-1 + plane(3, 3)), out[3])

    def test_optional_plane_and_arc_mode_block_the_split(self):
        for optional in ('/G18', '/G90.1', '/G93'):
            state, _ = prime(10, 0, -1)
            out = run(state, flat, optional, 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0')
            self.assertTrue(out[2].startswith('G3'), optional)
            self.assertEqual(state['arcs'], 1, optional)


class TestUnknownMovesAreCounted(unittest.TestCase):
    # a cutting line that can not be levelled because of an unknown position or mode must be counted
    def counted(self, *lines):
        state = new_levelling_state(arc_segment=0.5)
        out = run(state, plane, 'G90 G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', *lines)
        return state, out

    def test_axis_unknown_after_optional_move(self):
        state, out = self.counted('/G1 X2 Y2 Z-0.5', 'Z-0.4483')
        self.assertEqual(out[-1], 'Z-0.4483')
        self.assertEqual(state['unknown'], 1)


    def test_distance_mode_unknown(self):
        state, out = self.counted('/G91', 'G1 Z-0.5')
        self.assertEqual(out[-1], 'G1 Z-0.5')
        self.assertEqual(state['unknown'], 1)

    def test_g1_after_g53_is_counted(self):
        state, _ = self.counted('G53 G0 Z-5', 'G1 X3 Y3')
        self.assertEqual(state['unknown'], 1)


class TestUnparsedWords(unittest.TestCase):
    # review of 6a302b32: expression or parameter words must not be dropped

    def test_expression_axis_keeps_the_arc(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G3 X[0] Y[10] I-10 J0', state, flat, DEC)
        self.assertEqual(out, 'G3 X[0] Y[10] I-10 J0')
        self.assertEqual(state['arcs_split'], 0)

    def test_parameter_axis_makes_the_position_unknown(self):
        state, _ = prime(10, 0, -1, plane)
        out = run(state, plane, 'G1 X#1 Y5', 'G1 X2 Y2', 'G1 X3 Y3 Z-1')
        self.assertEqual(out[0], 'G1 X#1 Y5')
        self.assertEqual(out[1], 'G1 X2 Y2')
        self.assertIn('Z%.4f' % (-1 + plane(3, 3)), out[2])


TWO_PI = 2.0 * math.pi
BASE_TOL = 0.5 * 10 ** -DEC + 1e-5
HALF_ANGLE = math.pi / 36.0     # half the largest chord angle (10 degrees)
DS_ORIG = 0.02                  # sampling step of arcs of the original path
DS_LEV = 0.05                   # sampling step of the lines


def _strip_comments(line):
    line = re.sub(r'\([^)]*\)', '', line)
    return line.split(';')[0]


def _angle_sweep(a_start, a_end, clockwise):
    """Magnitude of the sweep from a_start to a_end, in [0, 2*pi)."""
    if clockwise:
        return (a_start - a_end) % TWO_PI
    return (a_end - a_start) % TWO_PI


class Sim:
    """
    Executes G-code text. `run()` returns the moves it made:
    SimpleNamespace(kind, g, start, end, pts, rmax, sweep, extra) where kind is
    'rapid', 'cut', 'arc' (G17 arc, pts = dense samples) or 'opaque_arc'
    (an arc in G18/G19: only the end point is known).
    """

    def __init__(self, x=0.0, y=0.0, z=0.0, block_delete=False):
        self.pos = [x, y, z]
        self.absolute = True
        self.arc_abs = False
        self.plane = 17
        self.g = None
        self.block_delete = block_delete

    def run(self, text):
        moves = []
        for raw in text.split('\n'):
            moves.extend(self._line(raw))
        return moves

    def _line(self, raw):
        line = _strip_comments(raw)
        if self.block_delete and line.lstrip().startswith('/'):
            return []
        words = {}
        gs = []
        for letter, num in _WORD.findall(line):
            if letter == 'G':
                gs.append(float(num))
            else:
                words[letter] = float(num)
        for g in gs:
            if g in (0.0, 1.0, 2.0, 3.0):
                self.g = int(g)
            elif g in (17.0, 18.0, 19.0):
                self.plane = int(g)
            elif g == 90.0:
                self.absolute = True
            elif g == 91.0:
                self.absolute = False
            elif g == 90.1:
                self.arc_abs = True
            elif g == 91.1:
                self.arc_abs = False
        has_axis = any(a in words for a in 'XYZ')
        if self.g in (0, 1):
            return self._linear(words) if has_axis else []
        if self.g in (2, 3) and (has_axis or any(a in words for a in 'IJKR')):
            return self._arc(words)
        assert not has_axis, 'axis words without a motion mode: %r' % raw
        return []

    def _target(self, words):
        end = list(self.pos)
        for i, axis in enumerate('XYZ'):
            if axis in words:
                end[i] = words[axis] if self.absolute else end[i] + words[axis]
        return end

    def _linear(self, words):
        start = tuple(self.pos)
        end = tuple(self._target(words))
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        n = max(1, int(math.ceil(length / DS_LEV)))
        pts = [tuple(s + (e - s) * i / n for s, e in zip(start, end)) for i in range(1, n + 1)]
        pts[-1] = end
        self.pos = list(end)
        return [SimpleNamespace(kind='rapid' if self.g == 0 else 'cut', g=self.g, start=start,
                                end=end, pts=pts, rmax=0.0, sweep=0.0, extra=0.0)]

    def _arc(self, words):
        start = tuple(self.pos)
        end = tuple(self._target(words))
        clockwise = self.g == 2
        if self.plane != 17:
            self.pos = list(end)
            return [SimpleNamespace(kind='opaque_arc', g=self.g, start=start, end=end,
                                    pts=[end], rmax=0.0, sweep=0.0, extra=0.0)]
        if 'R' in words:
            centre = self._centre_r(start, end, words['R'], clockwise)
        elif self.arc_abs:
            centre = (words['I'], words['J'])
        else:
            centre = (start[0] + words.get('I', 0.0), start[1] + words.get('J', 0.0))
        a_s = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a_e = math.atan2(end[1] - centre[1], end[0] - centre[0])
        r_s = math.hypot(start[0] - centre[0], start[1] - centre[1])
        r_e = math.hypot(end[0] - centre[0], end[1] - centre[1])
        if math.hypot(end[0] - start[0], end[1] - start[1]) < 1e-6:
            sweep = TWO_PI
        else:
            sweep = _angle_sweep(a_s, a_e, clockwise)
        signed = -sweep if clockwise else sweep
        rmax = max(r_s, r_e)
        n = max(16, int(math.ceil(rmax * sweep / DS_ORIG)))
        pts = []
        for i in range(1, n + 1):
            t = i / n
            ang = a_s + signed * t
            r = r_s + (r_e - r_s) * t
            pts.append((centre[0] + r * math.cos(ang), centre[1] + r * math.sin(ang),
                        start[2] + (end[2] - start[2]) * t))
        pts[-1] = end
        half = min(HALF_ANGLE, sweep / 2.0)
        extra = abs(end[2] - start[2]) / sweep * 0.13 * half ** 3
        self.pos = list(end)
        return [SimpleNamespace(kind='arc', g=self.g, start=start, end=end, pts=pts, rmax=rmax,
                                sweep=sweep, extra=extra)]

    @staticmethod
    def _centre_r(start, end, radius, clockwise):
        """Both candidate centres; the one whose sweep is <= 180 degrees for R > 0, > 180 for R < 0."""
        dx, dy = end[0] - start[0], end[1] - start[1]
        d = math.hypot(dx, dy)
        height = math.sqrt(max(0.0, radius * radius - d * d / 4.0))
        mx, my = start[0] + dx / 2.0, start[1] + dy / 2.0
        candidates = [(mx - height * dy / d, my + height * dx / d),
                      (mx + height * dy / d, my - height * dx / d)]
        for c in candidates:
            a_s = math.atan2(start[1] - c[1], start[0] - c[0])
            a_e = math.atan2(end[1] - c[1], end[0] - c[0])
            sweep = _angle_sweep(a_s, a_e, clockwise)
            if (radius > 0 and sweep <= math.pi + 1e-9) or (radius < 0 and sweep >= math.pi - 1e-9):
                return c
        raise AssertionError('no centre for R%s' % radius)


# ---------------------------------------------------------------- checking

def _segments(poly):
    """(ax, ay, az, vx, vy, vz, len2) of each segment of poly, for _project."""
    segments = []
    for (ax, ay, az), (bx, by, bz) in zip(poly, poly[1:]):
        vx, vy = bx - ax, by - ay
        segments.append((ax, ay, az, vx, vy, bz - az, vx * vx + vy * vy))
    return segments


def _project(segments, ptr, window, x, y):
    """
    Closest point to (x, y) on the segments[ptr:ptr+window] (made by _segments).
    Returns (segment index, Z at the closest point, distance).
    """
    best = None
    best_dist = math.inf
    hypot = math.hypot
    for j in range(ptr, min(len(segments), ptr + window)):
        ax, ay, az, vx, vy, vz, len2 = segments[j]
        if len2 == 0.0:
            t = 0.0
        else:
            t = ((x - ax) * vx + (y - ay) * vy) / len2
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        dist = hypot(x - (ax + t * vx), y - (ay + t * vy))
        if best is None or dist < best_dist - 1e-12:
            best, best_dist = (j, az + vz * t, dist), dist
    return best


def _xy_length(poly):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(poly, poly[1:]))


class Report:
    def __init__(self):
        self.worst_z = 0.0
        self.worst_xy = 0.0
        self.samples = 0


def check_program(test, lines, offset=plane, arc_segment=0.5, block_delete=False, report=None,
                  expect_all_compensated=True, level_offset=None):
    """
    Run `lines` through level_gcode_line and the simulator, check every line against `offset`.
    `level_offset` (default: `offset`) is the offset given to level_gcode_line: a different
    one must make the check fail.
    Returns (state, original sim, levelled sim, list of (line, out)).
    """
    report = report or Report()
    level_offset = level_offset or offset
    state = new_levelling_state(arc_segment=arc_segment)
    orig, lev = Sim(block_delete=block_delete), Sim(block_delete=block_delete)
    pairs = []
    for line in lines:
        arcs_before = state['arcs']
        out = level_gcode_line(line, state, level_offset, DEC)
        pairs.append((line, out))
        o_start, l_start = tuple(orig.pos), tuple(lev.pos)
        o_moves, l_moves = orig.run(line), lev.run(out)
        absolute = orig.absolute
        ctx = '\n  in: %r\n  out: %r' % (line, out)
        if not o_moves:
            test.assertEqual(l_moves, [], 'levelled line moves, the original does not' + ctx)
            continue
        test.assertEqual(len(o_moves), 1, ctx)
        om = o_moves[0]
        test.assertTrue(l_moves, 'levelled line does not move' + ctx)
        if om.kind == 'rapid':
            _check_rapid(test, om, l_moves, o_start, l_start, absolute, line, ctx)
        elif state['arcs'] > arcs_before:
            # an arc that was left alone must still run as the same arc
            test.assertEqual([m.kind for m in l_moves], [om.kind],
                             'the unsplit arc must still run as an arc' + ctx)
            test.assertEqual(l_moves[0].g, om.g, ctx)
            test.assertLessEqual(math.dist(l_moves[0].end[:2], om.end[:2]), 2 * 10 ** -DEC, ctx)
        else:
            _check_cut(test, om, l_moves, o_start, l_start, offset, arc_segment, report, ctx)
    if expect_all_compensated:
        test.assertEqual((state['arcs'], state['relative']), (0, 0), 'not compensated: %r' % (state,))
    return state, orig, lev, pairs


def _check_rapid(test, om, l_moves, o_start, l_start, absolute, line, ctx):
    """A rapid is not changed: absolute axes go to the same value, the others move by the same delta."""
    test.assertEqual([m.kind for m in l_moves], ['rapid'], ctx)
    lm = l_moves[0]
    given = {letter for letter, _ in _WORD.findall(_strip_comments(line))}
    for i, axis in enumerate('XYZ'):
        if absolute and axis in given:
            test.assertEqual(lm.end[i], om.end[i], ctx)
        else:
            test.assertAlmostEqual(lm.end[i] - l_start[i], om.end[i] - o_start[i], delta=1e-9, msg=ctx)


def _check_cut(test, om, l_moves, o_start, l_start, offset, arc_segment, report, ctx):
    test.assertTrue(all(m.kind == 'cut' and m.g == 1 for m in l_moves),
                    'a split arc must leave only G1 moves' + ctx)
    # the start is compensated if the machine is above the original by the offset there
    start_err = abs((l_start[2] - o_start[2]) - offset(l_start[0], l_start[1]))
    pre_ok = start_err <= 2 * BASE_TOL
    poly = [om.start] + list(om.pts)
    length = _xy_length(poly)
    degenerate = length < 1e-9
    segments = _segments(poly)
    if om.kind == 'arc':
        sag = om.rmax * (1.0 - math.cos(min(HALF_ANGLE, arc_segment / (2.0 * om.rmax))))
        xy_bound = sag + 4 * 10 ** -DEC
    else:
        xy_bound = 4 * 10 ** -DEC
    window = 3 * int(math.ceil(DS_LEV / DS_ORIG)) + 8
    # The rounded X/Y of the start and end vertices move the machine sideways of the
    # original path by xy_dev (measured; at most about 10**-decimals). On a steep move that
    # changes Z by slope * xy_dev: part of the tolerance.
    # Every vertex is rounded to 10**-decimals per axis: within 0.71 * 10**-decimals of the exact one.
    xy_dev = max(math.dist(l_start[:2], o_start[:2]), math.dist(l_moves[-1].end[:2], om.end[:2]),
                 0.71 * 10 ** -DEC)
    slope =0.0 if degenerate else abs(om.end[2] - om.start[2]) / length
    ptr = 0
    for index, lm in enumerate(l_moves):
        last_move = index == len(l_moves) - 1
        full = (pre_ok or index > 0) and not degenerate
        # a small error of the start (up to 2 * BASE_TOL, else the first move is only checked
        # at its end) fades out along the first move
        tol = BASE_TOL + om.extra + slope * xy_dev + (start_err if index == 0 and pre_ok else 0.0)
        points = lm.pts if full else [lm.end]
        for k, (x, y, z) in enumerate(points):
            if degenerate or (last_move and k == len(points) - 1):
                z_orig, dist = om.end[2], 0.0
            else:
                ptr, z_orig, dist = _project(segments, ptr, window if full else len(segments), x, y)
            err = abs(z - (z_orig + offset(x, y)))
            report.samples += 1
            report.worst_z = max(report.worst_z, err - om.extra - slope * xy_dev)
            test.assertLessEqual(dist, xy_bound, 'sample off the original path' + ctx)
            test.assertLessEqual(err, tol, 'Z error %.3g (tolerance %.3g) at (%.4f, %.4f)%s'
                                 % (err, tol, x, y, ctx))
        if last_move:
            report.worst_xy = max(report.worst_xy, math.dist(lm.end[:2], om.end[:2]))


# -------------------------------------------------------------- generator

def f6(v, decimals=6):
    text = '%.*f' % (decimals, v)
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if text in ('', '-0') else text


class Gen:
    """Random valid programs; the position is tracked by a Sim of the original program."""

    def __init__(self, rng, decimals=6, g91=True, arcs=True, abs_ij=False, rformat=True,
                 helical=True, chain=True):
        self.rng, self.decimals = rng, decimals
        self.g91, self.arcs, self.abs_ij = g91, arcs, abs_ij
        self.rformat, self.helical, self.chain = rformat, helical, chain
        self.sim = Sim()
        self.lines = []
        self.n_arcs = 0

    def num(self, v):
        return f6(v, self.decimals)

    def emit(self, line):
        self.lines.append(line)
        self.sim.run(line)

    def word(self, axis, target):
        """Axis word that brings the axis to `target` (absolute or as a delta)."""
        value = target
        if not self.sim.absolute:
            value = target - self.sim.pos['XYZ'.index(axis)]
        return axis + self.num(value)

    def prologue(self):
        r = self.rng
        self.emit('G21 G17 G90 G94')
        if self.abs_ij:
            self.emit('G90.1')
        self.emit('G0 X%s Y%s Z%s' % (self.num(r.uniform(-20, 20)), self.num(r.uniform(-20, 20)), self.num(5)))
        self.emit('G1 X%s Y%s Z%s F100' % (self.num(r.uniform(-20, 20)), self.num(r.uniform(-20, 20)),
                                           self.num(r.uniform(-1.5, 0.2))))

    def op_cut(self, motion='G1'):
        r = self.rng
        axes = [a for a in 'XYZ' if r.random() < 0.7] or ['X']
        parts = [motion] if (motion != 'G%d' % self.sim.g or r.random() < 0.5) else []
        for a in axes:
            if a == 'Z':
                target = r.uniform(-2.0, 0.5) if motion == 'G1' else r.uniform(2.0, 6.0)
            else:
                target = self.sim.pos['XYZ'.index(a)] + r.uniform(-12, 12)
            parts.append(self.word(a, target))
        if motion == 'G1' and r.random() < 0.3:
            parts.append('F%d' % r.randint(50, 500))
        self.emit(' '.join(parts))

    def op_mode(self):
        self.emit('G91' if self.sim.absolute else 'G90')

    def op_arc(self, kind=None):
        r = self.rng
        sim = self.sim
        kind = kind or r.choice(['small', 'medium', 'large', 'full'])
        clockwise = r.random() < 0.5
        radius = r.uniform(0.4, 15.0)
        sweep = {'small': lambda: math.radians(r.uniform(3, 30)),
                 'medium': lambda: math.radians(r.uniform(30, 170)),
                 'large': lambda: math.radians(r.uniform(190, 350)),
                 'full': lambda: TWO_PI}[kind]()
        direction = r.uniform(0, TWO_PI)
        sx, sy, sz = sim.pos
        cx, cy = sx + radius * math.cos(direction), sy + radius * math.sin(direction)
        a_s = direction + math.pi
        a_e = a_s - sweep if clockwise else a_s + sweep
        ex, ey = cx + radius * math.cos(a_e), cy + radius * math.sin(a_e)
        ez = sz
        if self.helical and r.random() < 0.4:
            ez = sz + r.uniform(-1.0, 1.0)
        full = sweep == TWO_PI
        parts = []
        if not (self.chain and sim.g == (2 if clockwise else 3) and r.random() < 0.8):
            parts.append('G2' if clockwise else 'G3')
        use_r = (self.rformat and not full and sweep > math.radians(5)
                 and abs(sweep - math.pi) > math.radians(2) and r.random() < 0.4)
        if full:
            # the end is the start: no X/Y word, or the same X/Y in G90
            if sim.absolute and r.random() < 0.5 and float(self.num(sx)) == sx and float(self.num(sy)) == sy:
                parts += ['X' + self.num(sx), 'Y' + self.num(sy)]
        else:
            parts += [self.word('X', ex), self.word('Y', ey)]
        if ez != sz or r.random() < 0.1:
            parts.append(self.word('Z', ez))
        if use_r:
            parts.append('R' + self.num(radius if sweep < math.pi else -radius))
        elif sim.arc_abs:
            parts += ['I' + self.num(cx), 'J' + self.num(cy)]
        else:
            i_val, j_val = cx - sx, cy - sy
            if abs(round(i_val, self.decimals)) < 1e-9 and r.random() < 0.8:
                parts.append('J' + self.num(j_val))
            elif abs(round(j_val, self.decimals)) < 1e-9 and r.random() < 0.8:
                parts.append('I' + self.num(i_val))
            else:
                parts += ['I' + self.num(i_val), 'J' + self.num(j_val)]
        if r.random() < 0.2:
            parts.append('F%d' % r.randint(50, 400))
        text = ' '.join(parts)
        if r.random() < 0.2:
            text += ' (arc)'
        self.emit(text)
        self.n_arcs += 1

    def program(self, n_ops):
        r = self.rng
        self.prologue()
        for _ in range(n_ops):
            choice = r.random()
            if choice < 0.12 and self.g91:
                self.op_mode()
            elif choice < 0.22:
                self.op_cut('G0')
            elif choice < 0.52 or not self.arcs:
                self.op_cut('G1')
            else:
                self.op_arc()
        return self.lines


class SimSelfTest(unittest.TestCase):
    """The simulator itself, against hand-computed geometry."""


    def test_the_checker_is_not_blind(self):
        """Levelling with a slightly different offset than the one checked must fail."""
        def wrong(x, y):
            return plane(x, y) + 0.0003 * x
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0']
        check_program(self, lines)
        with self.assertRaises(AssertionError):
            check_program(self, lines, level_offset=wrong)
        with self.assertRaises(AssertionError):                 # a helix that is flat at the end
            check_program(self, ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', 'G3 X0 Y10 Z-2 I-10 J0'],
                          level_offset=lambda x, y: plane(x, y) + (0.002 if x < 3 else 0.0))


class TestRandomArcs(unittest.TestCase):
    """Random G2/G3 arcs, chained, in G90 and G91, I/J and R, helical and full."""

    def run_many(self, seed, count, ops=14, **kwargs):
        rng = random.Random(seed)
        report = Report()
        for k in range(count):
            gen = Gen(rng, **kwargs)
            lines = gen.program(ops)
            with self.subTest(program=k):
                state, orig, lev, pairs = check_program(self, lines, report=report)
                self.assertEqual(state['arcs_split'], gen.n_arcs)
        return report

    # The quick tests run the first 5 programs of the full tests (same seed, same checks).

    def test_arcs_only_chained_in_g91(self):
        rng = random.Random(4)
        for k in range(3):
            gen = Gen(rng)
            gen.prologue()
            gen.emit('G91')
            for _ in range(10):
                gen.op_arc()
            with self.subTest(program=k):
                check_program(self, gen.lines)

    def test_every_sweep_class_both_modes(self):
        rng = random.Random(5)
        for kind in ('small', 'medium', 'large', 'full'):
            for g91 in (False, True):
                for k in range(2):
                    gen = Gen(rng)
                    gen.prologue()
                    if g91:
                        gen.emit('G91')
                    for _ in range(3):
                        gen.op_arc(kind)
                    with self.subTest(kind=kind, g91=g91, program=k):
                        state = check_program(self, gen.lines)[0]
                        self.assertEqual(state['arcs_split'], 3)


    def test_absolute_ij_switched_in_the_middle(self):
        rng = random.Random(9)
        for k in range(3):
            gen = Gen(rng, rformat=False)
            gen.prologue()
            for _ in range(4):
                gen.op_arc()
            gen.emit('G90.1')
            for _ in range(4):
                gen.op_arc()
            gen.emit('G91.1')
            gen.emit('G91')
            for _ in range(4):
                gen.op_arc()
            with self.subTest(program=k):
                check_program(self, gen.lines)


class TestMixedPrograms(unittest.TestCase):

    def test_retract_in_g90_returns_to_the_original_z(self):
        rng = random.Random(12)
        for k in range(3):
            gen = Gen(rng)
            gen.prologue()
            for _ in range(8):
                gen.op_arc()
                gen.op_cut('G1')
            gen.emit('G90')
            gen.emit('G0 Z%s' % gen.num(7.5))
            with self.subTest(program=k):
                state, orig, lev, pairs = check_program(self, gen.lines)
                self.assertEqual(lev.pos[2], orig.pos[2])
                self.assertAlmostEqual(lev.pos[0], orig.pos[0], delta=4 * 10 ** -DEC)
                self.assertAlmostEqual(lev.pos[1], orig.pos[1], delta=4 * 10 ** -DEC)


    def test_g91_g0_retract_keeps_the_delta_and_the_next_cut_returns_to_the_surface(self):
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', 'G91', 'G0 Z3', 'G0 X5', 'G1 Z-3 X2 Y2',
                 'G3 X0 Y4 I-2 J2', 'G1 X1', 'G90', 'G0 Z6']
        state, orig, lev, pairs = check_program(self, lines)
        self.assertEqual(lev.pos[2], 6.0)


    def test_1000_small_g91_moves_do_not_drift(self):
        rng = random.Random(14)
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X0 Y0 Z-0.5', 'G91']
        for _ in range(400):
            kind = rng.random()
            dx, dy = rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3)
            if kind < 0.6:
                lines.append('G1 X%s Y%s' % (f6(dx, 5), f6(dy, 5)))
            elif kind < 0.8:
                lines.append('G1 X%s Y%s Z%s' % (f6(dx, 5), f6(dy, 5), f6(rng.uniform(-0.02, 0.02), 5)))
            else:
                lines.append('G1 Z%s' % f6(rng.uniform(-0.05, 0.05), 5))
        state = new_levelling_state(arc_segment=0.5)
        orig, lev = Sim(), Sim()
        worst = 0.0
        for line in lines:
            out = level_gcode_line(line, state, plane, DEC)
            orig.run(line)
            lev.run(out)
            if line.startswith('G1'):
                worst = max(worst, abs(lev.pos[2] - (orig.pos[2] + plane(lev.pos[0], lev.pos[1]))))
        self.assertLessEqual(worst, 10 ** -DEC)
        self.assertLessEqual(worst, 0.5 * 10 ** -DEC + 1e-6)
        self.assertEqual((state['arcs'], state['relative']), (0, 0))


class TestUnsplittableAfterSplit(unittest.TestCase):
    SETUP = ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0']

    def test_g18_arc_without_g_word_runs_as_an_arc(self):
        lines = self.SETUP + ['G18', 'X5 Z3 I1 K0']
        state, orig, lev, pairs = check_program(self, lines, expect_all_compensated=False)
        self.assertEqual(state['arcs_split'], 1)
        self.assertEqual(state['arcs'], 1)
        self.assertRegex(pairs[-1][1], r'^\s*G3\b')


    def test_bad_radius_arc_after_split_runs_as_an_arc(self):
        lines = self.SETUP + ['X0 Y0 I-9 J0']
        state, orig, lev, pairs = check_program(self, lines, expect_all_compensated=False)
        self.assertEqual(state['arcs'], 1)
        self.assertTrue(pairs[-1][1].startswith('G3 '))


