#!/usr/bin/env python
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from appPlugins.Common.grbl.grbl_protocol import (
    grbl_capabilities, parse_grbl_active_wcs, parse_grbl_identity, parse_grbl_offsets,
    parse_grbl_status_report, parse_grbl_work_offset, work_position)


class TestStatusReport(unittest.TestCase):
    def test_run_mpos_fs_ov(self):
        rep = parse_grbl_status_report(
            '<Run|MPos:1.000,2.000,-0.500|FS:500,10000|Ov:100,100,100|WCO:0.000,0.000,0.000>')
        self.assertEqual(rep['state'], 'Run')
        self.assertEqual(rep['mpos'], (1.0, 2.0, -0.5))
        self.assertEqual(rep['feed'], 500.0)
        self.assertEqual(rep['spindle'], 10000.0)
        self.assertEqual(rep['ov'], (100, 100, 100))

    def test_idle_wpos(self):
        rep = parse_grbl_status_report('<Idle|WPos:0.000,0.000,0.000|FS:0,0>')
        self.assertEqual(rep['state'], 'Idle')
        self.assertEqual(rep['wpos'], (0.0, 0.0, 0.0))

    def test_hold_substate(self):
        self.assertEqual(parse_grbl_status_report('<Hold:0|MPos:0,0,0>')['state'], 'Hold')

    def test_not_a_report(self):
        for text in ('ok', '', None, '<Idle|MPos:1,2>'):
            self.assertIsNone(parse_grbl_status_report(text))

    def test_work_position_from_wco(self):
        self.assertEqual(work_position({'mpos': (10.0, 10.0, 0.0)}, last_wco=(1.0, 2.0, 3.0)),
                         (9.0, 8.0, -3.0))

    def test_work_position_unknown(self):
        self.assertIsNone(work_position({'mpos': (1, 2, 3)}))

    def test_tool_state_with_the_tool_change_protocol(self):
        rep = parse_grbl_status_report('<Tool|MPos:0.000,0.000,0.000|FS:0,0,0|A:T|MPG:0>')
        self.assertEqual(rep['state'], 'Tool')
        self.assertTrue(rep['tool_pending'])
        self.assertEqual(rep['spindle_actual'], 0.0)
        self.assertFalse(rep['mpg'])

    def test_actual_spindle_alarm_substate_and_extra_axes(self):
        rep = parse_grbl_status_report('<Run|MPos:0,0,0|FS:500,10000,9800>')
        self.assertEqual((rep['spindle'], rep['spindle_actual']), (10000.0, 9800.0))
        rep = parse_grbl_status_report('<Alarm:4|MPos:0,0,0>')
        self.assertEqual((rep['state'], rep['substate']), ('Alarm', '4'))
        self.assertIsNone(parse_grbl_status_report('<Idle|MPos:0,0,0,0>'))


class TestGrblHalIdentity(unittest.TestCase):
    ANSWER = ('[OPT:VN,35,128,3,0]\n'
              '[AXS:3:XYZ]\n'
              '[NEWOPT:ENUMS,RT-,TC,HOME,SD]\n'
              '[FIRMWARE:grblHAL]\n'
              '[SIGNALS:P,D,H,R,S]\n'
              'ok\n')

    def test_identity_tags_are_read(self):
        identity = parse_grbl_identity(self.ANSWER)
        self.assertEqual(identity['firmware'], 'grblHAL')
        self.assertEqual(identity['axes'], 3)
        self.assertEqual(identity['axis_letters'], 'XYZ')
        self.assertIn('TC', identity['options'])
        self.assertIn('RT-', identity['options'])
        for text in ('', None, 'error:3', '[MSG:no report]\nok'):
            self.assertIsNone(parse_grbl_identity(text))

    def test_capabilities_of_an_xyz_manual_tool_change_controller(self):
        caps = grbl_capabilities(parse_grbl_identity(self.ANSWER))
        self.assertTrue(caps['grblhal'])
        self.assertTrue(caps['xyz_only'])
        self.assertTrue(caps['manual_tool_change'])
        self.assertFalse(caps['automatic_tool_change'])
        self.assertTrue(caps['probe'])
        self.assertTrue(caps['top_bit_realtime'])

    def test_extra_axes_no_probe_and_automatic_tool_change(self):
        text = '[AXS:4:XYZU]\n[NEWOPT:RT+,NOPROBE,ATC=1]\n[FIRMWARE:grblHAL]\nok\n'
        caps = grbl_capabilities(parse_grbl_identity(text))
        self.assertFalse(caps['xyz_only'])
        self.assertFalse(caps['probe'])
        self.assertFalse(caps['top_bit_realtime'])
        self.assertFalse(caps['manual_tool_change'])
        self.assertTrue(caps['automatic_tool_change'])

    def test_opt_axis_fallback(self):
        for count, expected in (('3', True), ('4', False), ('2', False),
                                ('', None), ('bad', None), ('0', None), ('3.0', None)):
            with self.subTest(count=count):
                identity = parse_grbl_identity('[OPT:VN,35,128,%s,0]' % count)
                self.assertIs(grbl_capabilities(identity)['xyz_only'], expected)
                self.assertFalse(grbl_capabilities(identity)['grblhal'])
        self.assertIsNone(grbl_capabilities(parse_grbl_identity('[OPT:VN,35,128]'))['xyz_only'])
        # older builds do not report AXS: the axes are unknown, not 3
        caps = grbl_capabilities(parse_grbl_identity('[NEWOPT:RT+]\n[FIRMWARE:grblHAL]\nok\n'))
        self.assertIsNone(caps['xyz_only'])

    def test_malformed_or_conflicting_axis_records_are_not_safe_defaults(self):
        for text in ('[AXS:bad]\n[OPT:VN,35,128,3,0]',
                     '[OPT:VN,35,128,3,0]\n[OPT:VN,35,128,4,0]',
                     '[OPT:VN,35,128,4,0]\n[OPT:VN,35,128,3,0]',
                     '[OPT:VN,35,128,3,0]trailing',
                     '[OPT:VN,bad,128,3,0]', '[OPT:VN,35,128,3,bad]',
                     '[AXS:4:XYZU]\n[AXS:bad]'):
            with self.subTest(text=text):
                self.assertIsNot(grbl_capabilities(parse_grbl_identity(text))['xyz_only'], True)
        for text in ('[OPT:VN,35,128,3,0]\n[AXS:4:XYZU]', '[AXS:3:XYU]\n[OPT:VN,35,128,3,0]',
                     '[AXS:4:XYZU]\n[AXS:3:XYZ]'):
            with self.subTest(text=text):
                self.assertIs(grbl_capabilities(parse_grbl_identity(text))['xyz_only'], False)
        identity = parse_grbl_identity('[OPT:VN,35,128,4,0]\n[AXS:3:XYZ]')
        self.assertEqual((identity['axes'], identity['axis_letters']), (3, 'XYZ'))


class TestGrblHalOffsets(unittest.TestCase):
    def test_scalar_and_vector_tlo_work_offsets(self):
        base = '[G54:10,20,30]\n[G92:1,2,3]\n'
        for record, tlo, combined in (('', 0.0, (11.0, 22.0, 33.0)),
                                      ('[TLO:4]', 4.0, (11.0, 22.0, 37.0)),
                                      ('[TLO:0,0,0]', (0.0, 0.0, 0.0), (11.0, 22.0, 33.0)),
                                      ('[TLO:4,-5,6]', (4.0, -5.0, 6.0), (15.0, 17.0, 39.0))):
            with self.subTest(record=record):
                parsed = parse_grbl_offsets(base + record)['TLO']
                self.assertEqual(parsed, tlo)
                self.assertIs(type(parsed), type(tlo))
                self.assertEqual(parse_grbl_work_offset(base + record), combined)

    def test_invalid_vector_tlo_and_combined_overflow_are_rejected(self):
        for record in ('[TLO:1,2]', '[TLO:1,2,3,4]', '[TLO:1,,3]', '[TLO:1,no,3]',
                       '[TLO:nan,0,0]', '[TLO:0,inf,0]', '[TLO:0,0,-inf]',
                       '[TLO:0,0,0', '[TLO:0,0,0]\n[TLO:0]', '[TLO:0]\n[TLO:0,0,0]'):
            with self.subTest(record=record), self.assertRaises(ValueError):
                parse_grbl_offsets(record)
        for tlo in ('1e308,0,0', '0,1e308,0', '0,0,1e308', '1e308'):
            with self.subTest(tlo=tlo), self.assertRaises(ValueError):
                parse_grbl_work_offset('[G54:1e308,1e308,1e308]\n[TLO:%s]' % tlo)

    def test_g59_1_to_3_are_work_coordinate_systems(self):
        self.assertEqual(parse_grbl_active_wcs('[GC:G0 G59.3 G17 G21 G90 G94 M5]'), 'G59.3')
        self.assertEqual(parse_grbl_active_wcs('[GC:G0 G59.1 G17 G21 G90 G94 M5]'), 'G59.1')
        records = parse_grbl_offsets('[G59.1:1.000,2.000,3.000]\n[G59.2:0.000,0.000,0.000]\n'
                                     '[G59.3:4.000,5.000,6.000]\n[G92:0.000,0.000,0.000]\n[TLO:7.000]\nok')
        self.assertEqual(records['G59.3'], (4.0, 5.0, 6.0))
        self.assertEqual(parse_grbl_work_offset(
            '[G59.1:1.000,2.000,3.000]\n[G92:0.000,0.000,0.000]\n[TLO:0.000]\nok', 'G59.1'),
            (1.0, 2.0, 3.0))


# ---- merged from test_grbl_bicubic.py ----
import sys
import os

import unittest

from appPlugins.Common.levelling_interp import (
    build_bilinear_grid, bicubic_offset, bicubic_supported, bilinear_offset)


def grid_points(fn, n=5, step=10.0):
    return [(i * step, j * step, fn(i * step, j * step)) for i in range(n) for j in range(n)]


class TestBicubic(unittest.TestCase):
    def test_hits_grid_nodes(self):
        g = build_bilinear_grid(grid_points(lambda x, y: 0.01 * x * y))
        self.assertAlmostEqual(bicubic_offset(g, 20.0, 30.0), 0.01 * 20.0 * 30.0, places=9)

    def test_plane_is_exact(self):
        g = build_bilinear_grid(grid_points(lambda x, y: 0.2 * x - 0.1 * y + 1.0))
        self.assertAlmostEqual(bicubic_offset(g, 13.0, 27.0), 0.2 * 13.0 - 0.1 * 27.0 + 1.0, places=9)

    def test_plane_exact_at_border(self):
        g = build_bilinear_grid(grid_points(lambda x, y: x))
        self.assertAlmostEqual(bicubic_offset(g, 2.5, 20.0), 2.5, places=9)
        self.assertAlmostEqual(bicubic_offset(g, 37.5, 20.0), 37.5, places=9)

    def test_clamped_outside(self):
        g = build_bilinear_grid(grid_points(lambda x, y: x + y))
        self.assertAlmostEqual(bicubic_offset(g, -50.0, -50.0), bicubic_offset(g, 0.0, 0.0), places=9)
        self.assertAlmostEqual(bicubic_offset(g, 500.0, 500.0), bicubic_offset(g, 40.0, 40.0), places=9)

    def test_supported_needs_4x4(self):
        self.assertTrue(bicubic_supported(build_bilinear_grid(grid_points(lambda x, y: 0.0, n=4))))
        self.assertFalse(bicubic_supported(build_bilinear_grid(grid_points(lambda x, y: 0.0, n=3))))
        self.assertFalse(bicubic_supported(None))

    def test_nonuniform_not_supported(self):
        xs = [0.0, 1.0, 3.0, 6.0, 10.0]
        ys = [0.0, 10.0, 20.0, 30.0, 40.0]
        grid = [[0.0] * len(xs) for _ in ys]
        self.assertIs(bicubic_supported((xs, ys, grid)), False)

    def test_close_to_bilinear_on_smooth_surface(self):
        g = build_bilinear_grid(grid_points(lambda x, y: 0.001 * (x - 20.0) ** 2))
        self.assertLess(abs(bicubic_offset(g, 15.0, 15.0) - bilinear_offset(g, 15.0, 15.0)), 0.05)


# ---- merged from test_grbl_toolpath.py ----
# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/15/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Tests of parse_toolpath(), the 3D toolpath with the line index."""

import os
import sys
import unittest


from appPlugins.Common.gcode_segment import parse_toolpath


class ParseToolpathTest(unittest.TestCase):

    def test_basic(self):
        segs = parse_toolpath(['G0 X1 Y1', 'G1 Z-1', 'G1 X2'], start=(0, 0, 0))
        self.assertEqual([s[0] for s in segs], [0, 1, 2])
        self.assertEqual([s[3] for s in segs], [True, False, False])
        self.assertEqual(segs[-1][2], (2.0, 1.0, -1.0))

    def test_relative(self):
        segs = parse_toolpath(['G91', 'G1 X1', 'G1 X1'], start=(0, 0, 0))
        self.assertEqual(segs[-1][2], (2.0, 0.0, 0.0))

    def test_no_move_no_segment(self):
        self.assertEqual(parse_toolpath(['G1 X0'], start=(0, 0, 0)), [])

    def test_skips_g28_g92(self):
        self.assertEqual(parse_toolpath(['G92 X5', 'G28 X0'], start=(0, 0, 0)), [])

    def test_unknown_start_no_segment(self):
        self.assertEqual(parse_toolpath(['G1 X5 F100', 'G1 Y5', 'G1 X9']), [])

    def test_segments_start_after_all_axes_known(self):
        segs = parse_toolpath(['G1 X5 F100', 'G1 Y5', 'G1 Z2', 'G1 X9'])
        self.assertEqual([s[0] for s in segs], [3])
        self.assertEqual(segs[0][1:3], ((5.0, 5.0, 2.0), (9.0, 5.0, 2.0)))

    def test_g92_then_unknown(self):
        segs = parse_toolpath(['G1 X1 Y1 Z1', 'G92 X0', 'G1 X5'], start=(0, 0, 0))
        self.assertEqual([s[0] for s in segs], [0])

    def test_probe_then_unknown(self):
        segs = parse_toolpath(['G38.2 Z-5 F10', 'G1 X5', 'G1 Y5'], start=(0, 0, 0))
        self.assertEqual(segs, [])

    def test_arc_is_chord(self):
        segs = parse_toolpath(['G2 X2 Y0 I1 J0'], start=(0, 0, 0))
        self.assertEqual(segs, [(0, (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), False)])


# ---- merged from test_grbl_sender_gen.py ----
import sys
import os

import unittest
from types import SimpleNamespace

from appPlugins.ToolGcodeSender import SenderGen


class TestCleanLines(unittest.TestCase):
    def test_removes_comments(self):
        self.assertEqual(SenderGen.clean_lines('G1 X1 (cut)\n; note\nG0 Z5 ;up\n%\n\n'), ['G1 X1', 'G0 Z5'])


    def test_numbered_keeps_source_line(self):
        self.assertEqual(SenderGen.clean_lines_numbered('(a)\nG0 X1\n\n; c\nG1 X2\n'), [(2, 'G0 X1'), (5, 'G1 X2')])


class TestNeedsSegmentation(unittest.TestCase):
    def test_flag_true(self):
        self.assertFalse(SenderGen.needs_segmentation(SimpleNamespace(is_segmented_gcode=True)))

    def test_flag_false_or_missing(self):
        self.assertTrue(SenderGen.needs_segmentation(SimpleNamespace(is_segmented_gcode=False)))
        self.assertTrue(SenderGen.needs_segmentation(SimpleNamespace()))


class TestSplitArcs(unittest.TestCase):
    def test_arc_split_keeps_z(self):
        lines = ['G90', 'G21', 'G0 X0 Y0 Z-0.1', 'G2 X10 Y0 I5 J0']
        out, count, source_index, unsplit = SenderGen.split_arcs(lines, 'MM', 1.0)
        self.assertGreater(len(out), 5)
        self.assertEqual(count, 1)
        self.assertEqual(unsplit, [])
        new_lines = [line for line in out if line.startswith('G1')]
        self.assertTrue(new_lines)
        self.assertTrue(all('Z-0.1' in line for line in new_lines))
        # the first 3 lines are the source lines; every new line comes from the arc line
        self.assertEqual(source_index[:3], [0, 1, 2])
        self.assertEqual(set(source_index[3:]), {3})
        self.assertEqual(len(source_index), len(out))

    def test_no_arcs(self):
        lines = ['G90', 'G21', 'G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1 F100']
        out, count, source_index, unsplit = SenderGen.split_arcs(lines, 'MM', 1.0)
        self.assertEqual(out, lines)
        self.assertEqual(count, 0)
        self.assertEqual(source_index, list(range(len(lines))))
        self.assertEqual(unsplit, [])

    def test_modal_arc_split(self):
        # the second arc line is a bare modal line (no G word)
        lines = ['G0 X0 Y0 Z0', 'G2 X10 Y0 I5 J0 F100', 'X20 Y0 I5 J0']
        out, count, source_index, unsplit = SenderGen.split_arcs(lines, 'MM', 1.0)
        self.assertEqual(count, 2)
        self.assertEqual(unsplit, [])
        self.assertNotIn('X20 Y0 I5 J0', out)
        self.assertEqual(set(source_index), {0, 1, 2})

    def test_unsplit_modal_arc(self):
        # The parser (level_gcode_line) does not split an arc in the G18 plane: it counts both
        # arc lines (the 2nd is modal) in state['arcs'], and leaves them as they are. So both
        # source indexes are in `unsplit`, the count is 0 and the output is the source list.
        lines = ['G18', 'G0 X0 Y0 Z0', 'G2 X10 Z0 I5 K0', 'X20 Z0 I5 K0']
        out, count, source_index, unsplit = SenderGen.split_arcs(lines, 'MM', 1.0)
        self.assertEqual(unsplit, [2, 3])
        self.assertEqual(count, 0)
        self.assertEqual(out, lines)
        self.assertEqual(source_index, [0, 1, 2, 3])


# ---- merged from test_grbl_segment.py ----
import sys
import os

import unittest

from appPlugins.Common.gcode_segment import split_long_moves, _words


class TestSplitLongMoves(unittest.TestCase):
    def test_long_g1_is_split(self):
        lines = ['G90', 'G0 X0 Y0 Z1', 'G1 Z-0.1 F100', 'G1 X10 Y0']
        out, count = split_long_moves(lines, 2.5)
        self.assertEqual(count, 1)
        self.assertEqual(len(out), 3 + 4)
        self.assertEqual(out[:3], lines[:3])
        self.assertEqual(out[-1], 'G1 X10.0000 Y0.0000 Z-0.1000')

    def test_short_g1_unchanged(self):
        lines = ['G0 X0 Y0 Z-0.1', 'G1 X1 Y0']
        out, count = split_long_moves(lines, 2)
        self.assertEqual(out, lines)
        self.assertEqual(count, 0)

    def test_g0_unchanged(self):
        lines = ['G0 X0 Y0 Z1', 'G0 X100']
        out, count = split_long_moves(lines, 2)
        self.assertEqual(out, lines)
        self.assertEqual(count, 0)

    def test_g91_unchanged(self):
        lines = ['G90', 'G1 X0 Y0 Z0', 'G91', 'G1 X50']
        out, count = split_long_moves(lines, 2)
        self.assertEqual(out, lines)
        self.assertEqual(count, 0)

    def test_unknown_start_unchanged(self):
        out, count = split_long_moves(['G1 X50 Y0'], 2)
        self.assertEqual(out, ['G1 X50 Y0'])
        self.assertEqual(count, 0)

    def test_feed_on_first_piece_only(self):
        out, count = split_long_moves(['G0 X0 Y0 Z0', 'G1 X10 F300'], 2)
        pieces = out[1:]
        self.assertEqual(count, 1)
        self.assertEqual(len(pieces), 5)
        self.assertTrue(pieces[0].endswith('F300.00'))
        for piece in pieces[1:]:
            self.assertNotIn('F', piece)

    def test_other_words_unchanged(self):
        lines = ['G0 X0 Y0 Z0', 'G1 X50 S1000']
        out, count = split_long_moves(lines, 2)
        self.assertEqual(out, lines)
        self.assertEqual(count, 0)

    def test_bad_max_len(self):
        with self.assertRaises(ValueError):
            split_long_moves(['G1 X1'], 0)


class TestSplitStateRules(unittest.TestCase):
    """The state rules of A6, the same as level_gcode_line()."""

    def _unchanged(self, lines, max_len=2):
        out, count = split_long_moves(lines, max_len)
        self.assertEqual(out, lines)
        self.assertEqual(count, 0)


    def test_same_block_g90_kept(self):
        # the G90 in the block of the move is the distance mode of the move
        lines = ['G90 G0 X0 Y0 Z0', 'G91', 'G90 G1 X10 Y0 F100']
        out, count = split_long_moves(lines, 2.5)
        self.assertEqual(count, 1)
        self.assertEqual(out[:2], lines[:2])
        pieces = out[2:]
        self.assertEqual(len(pieces), 4)
        # the non-motion G words stay on the first piece; the others start with G1
        self.assertEqual(pieces[0], 'G90 G1 X2.5000 Y0.0000 Z0.0000 F100.00')
        self.assertTrue(all(p.startswith('G1 ') for p in pieces[1:]))

    def test_probe_makes_position_unknown(self):
        self._unchanged(['G0 X0 Y0 Z0', 'G38.2 Z-5 F10', 'G1 X10 Y0 F100'])
        # the axis word of the probe does not make Z known; a later absolute move does, axis by axis
        lines = ['G0 X0 Y0 Z0', 'G38.2 Z-5 F10', 'G1 X10 Y0 F100', 'G1 X20 Y0 Z-1', 'G1 X30 Y0']
        out, count = split_long_moves(lines, 5)
        self.assertEqual(out[:3], lines[:3])
        self.assertEqual(out[3], lines[3])
        self.assertEqual(count, 1)
        self.assertEqual(len(out), 4 + 2)

    def test_tlo_makes_z_unknown(self):
        self._unchanged(['G0 X0 Y0 Z0', 'G43.1 Z1', 'G1 X10 Y0 F100'])
        self._unchanged(['G0 X0 Y0 Z0', 'G49', 'G1 X10 Y0 F100'])

    def test_g53_makes_position_unknown(self):
        self._unchanged(['G0 X0 Y0 Z0', 'G53 G0 Z0', 'G1 X10 Y0 F100'])

    def test_wcs_change_makes_position_unknown(self):
        self._unchanged(['G0 X0 Y0 Z0', 'G55', 'G1 X10 Y0 F100'])
        self._unchanged(['G0 X0 Y0 Z0', 'G59.1', 'G1 X10 Y0 F100'])

    def test_units_change_makes_position_unknown(self):
        self._unchanged(['G21', 'G0 X0 Y0 Z0', 'G20', 'G1 X10 Y0 F100'])
        # the same units again keep the position
        out, count = split_long_moves(['G21', 'G0 X0 Y0 Z0', 'G21', 'G1 X10 Y0 F100'], 5)
        self.assertEqual(count, 1)

    def test_inverse_time_not_split(self):
        self._unchanged(['G0 X0 Y0 Z0', 'G93', 'G1 X10 Y0 F100'])
        # G94 allows the split again
        out, count = split_long_moves(['G0 X0 Y0 Z0', 'G93', 'G1 X10 Y0 F100', 'G94', 'G1 X20 Y0'], 5)
        self.assertEqual(count, 1)
        self.assertEqual(len(out), 4 + 2)

    def test_unknown_g_makes_position_unknown(self):
        self._unchanged(['G0 X0 Y0 Z0', 'G12 I5', 'G1 X10 Y0 F100'])

    def test_modal_g1_without_word_split(self):
        lines = ['G1 X0 Y0 Z0 F100', 'X50']
        out, count = split_long_moves(lines, 10)
        self.assertEqual(count, 1)
        self.assertEqual(out[0], lines[0])
        self.assertEqual(len(out), 1 + 5)
        self.assertEqual(out[-1], 'G1 X50.0000 Y0.0000 Z0.0000')

    def test_inherited_g91_not_split(self):
        self._unchanged(['G0 X0 Y0 Z0', 'G91', 'G1 X10 F100'])

    def test_other_g_words_in_block_not_split(self):
        # only G1, G17, G90 and G94 are allowed in the block of a split move
        self._unchanged(['G0 X0 Y0 Z0', 'G18 G1 X10 Y0 F100'])
        self._unchanged(['G0 X0 Y0 Z0', 'G61 G1 X10 Y0 F100'])

    def test_program_end_and_dollar_lines(self):
        self._unchanged(['G0 X0 Y0 Z0', 'M30', 'G1 X10 Y0 F100'])
        self._unchanged(['G0 X0 Y0 Z0', '$H', 'G1 X10 Y0 F100'])

    def test_axis_word_of_unknown_block_not_known(self):
        # G92 names X and Y: they are not known after it, even if G1 is the modal mode
        self._unchanged(['G1 X0 Y0 Z0 F100', 'G92 X0 Y0', 'G1 X10 Y0'])
        self._unchanged(['G1 X0 Y0 Z0 F100', 'G92 X0 Y0', 'G1 X10 Y0', 'G1 X20 Y0'])


# ---- merged from test_grbl_stream.py ----
import sys
import os

import unittest

import serial

from appPlugins.Common.grbl.grbl_client import (
    stream_lines, validate_stream_lines, dwell_seconds, GRBL_RX_BUFFER, GRBL_MAX_LINE)


class FakeStreamPort:
    """Each written line gets 'ok' after 1 readline(); the receive buffer must never overflow."""

    def __init__(self, rx_size=127, errors=None, alarm_at=None):
        self.rx_size = rx_size
        self.pending = []           # byte counts in the GRBL buffer
        self.replies = []
        self.written = []
        self.polls = 0
        self.max_used = 0
        self.errors = errors or {}  # line index -> 'error:N'
        self.alarm_at = alarm_at
        self.count = 0
        self.status_sent = False

    def write(self, data):
        if data == b'?':
            self.polls += 1
            # answer the first '?' only; more answers would keep readline() busy for ever
            if not self.status_sent:
                self.status_sent = True
                self.replies.append(b'<Run|MPos:0.000,0.000,0.000|FS:0,0>\r\n')
            return 'ok'
        self.written.append(data)
        self.pending.append(len(data))
        used = sum(self.pending)
        self.max_used = max(self.max_used, used)
        assert used <= self.rx_size, "GRBL receive buffer overflow"
        return 'ok'

    def readline(self):
        if self.replies:
            return self.replies.pop(0)
        if not self.pending:
            return b''
        self.pending.pop(0)
        index = self.count
        self.count += 1
        if self.alarm_at == index:
            return b'ALARM:1\r\n'
        return (self.errors.get(index, 'ok') + '\r\n').encode()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TimedPort(FakeStreamPort):
    """
    A GRBL fake with a fake clock. Each readline() advances the clock by `step` s, so no test sleeps.
    The oldest line gets 'ok' when the clock reaches `ack_at` (never if None). Each '?' is answered with
    report(now). A test that never times out fails at `limit` s instead of looping for ever.
    """

    def __init__(self, clock, ack_at=None, report=None, step=1.0, limit=1000.0, **kwargs):
        super().__init__(**kwargs)
        self.clock = clock
        self.ack_at = ack_at
        self.report = report
        self.step = step
        self.limit = limit
        self.queue = []

    def write(self, data):
        if data == b'?':
            self.polls += 1
            if self.report is not None:
                self.queue.append((self.report(self.clock.now) + '\r\n').encode())
            return 'ok'
        return super().write(data)

    def readline(self):
        self.clock.now += self.step
        assert self.clock.now <= self.limit, "the streamer did not time out"
        if self.ack_at is not None and self.clock.now >= self.ack_at and self.pending:
            self.pending.pop(0)
            return b'ok\r\n'
        if self.queue:
            return self.queue.pop(0)
        return b''


def run(port, lines, **kwargs):
    return stream_lines(lines, port.write, port.readline, **kwargs)


class TestStream(unittest.TestCase):
    def test_m6_delayed_reply_is_separated_by_temporary_stream_fence(self):
        for reply in (b'ok\n', b'error:20\n'):
            with self.subTest(reply=reply):
                clock = FakeClock()
                written, queue, progress, completed = [], [], [], []
                marker_seen = fence_acked = resumed = False

                def write(data):
                    nonlocal resumed
                    written.append(data)
                    if data == b'?':
                        queue.append(b'<Idle>\n' if resumed else b'<Tool|A:T>\n')
                    elif data == b'\xa3':
                        queue.extend([b'', b'', reply])
                    elif data == b'$G\n':
                        queue.extend([b'[GC:G0 G54 G17 G21 G90 G94 M5]\n', b'ok\n'])
                    elif data == b'~':
                        resumed = True
                    elif data != b'M6\n':
                        queue.append(b'ok\n')
                    return 'ok'

                def readline():
                    nonlocal marker_seen, fence_acked
                    clock.now += 1
                    self.assertLess(clock.now, 60)
                    chunk = queue.pop(0) if queue else b''
                    if chunk.startswith(b'[GC:'):
                        marker_seen = True
                    elif marker_seen and chunk == b'ok\n':
                        fence_acked = True
                    return chunk

                def tool_command():
                    self.assertTrue(fence_acked, 'operator command requested before the reply fence')
                    self.assertEqual(written.count(b'$G\n'), 1)
                    return 'G10 L20 P1 Z0' if not completed else None

                def command_done(text):
                    completed.append(text)
                    self.assertEqual(progress, [(1, 2)])
                    write(b'~')

                res = stream_lines(['M6', 'G1 X2'], write, readline, tool_change_ack=True,
                                   poll_status=True, poll_interval=5, clock=clock, stall_timeout=15,
                                   on_tool_change=lambda: self.assertTrue(fence_acked),
                                   tool_command=tool_command, on_tool_command_done=command_done,
                                   on_progress=lambda d, t: progress.append((d, t)))
                self.assertEqual(res['status'], 'finished' if reply == b'ok\n' else 'error')
                self.assertEqual(completed, ['ok'] if reply == b'ok\n' else [])
                if reply.startswith(b'error:'):
                    self.assertNotIn(b'G10 L20 P1 Z0\n', written)
                    self.assertNotIn(b'G1 X2\n', written)

    def test_temporary_stream_fence_failure_never_enables_operator_commands(self):
        for answer, expected in ((b'error:20\n', 'error'),
                                 (b'[GC:G0 G54 G21 G90 G94]\nerror:20\n', 'error'),
                                 (b'ok\nok\n', 'error'),
                                 (b'[GC:G0]\n[GC:G0]\nok\n', 'error'),
                                 (b'[GC:G0', 'timeout'),
                                 (b'[GC:G0]\n', 'timeout')):
            with self.subTest(answer=answer):
                clock = FakeClock()
                written, queue, notified, completed = [], [], [], []

                def write(data):
                    written.append(data)
                    if data == b'?':
                        queue.append(b'<Tool|A:T>\n')
                    elif data == b'$G\n':
                        queue.append(answer)
                    return 'ok'

                def readline():
                    clock.now += 1
                    return b'ALARM:1\n' if clock.now > 40 else (queue.pop(0) if queue else b'')

                res = stream_lines(['M6', 'G1 X2'], write, readline, tool_change_ack=True,
                                   poll_status=True, poll_interval=5, clock=clock, stall_timeout=10,
                                   on_tool_change=lambda: notified.append(True),
                                   tool_command=lambda: 'G10 L20 P1 Z0',
                                   on_tool_command_done=completed.append)
                self.assertEqual(res['status'], expected)
                self.assertEqual(res.get('tool_command'), '$G')
                self.assertEqual(notified, [])
                self.assertEqual(completed, [])
                self.assertNotIn(b'G10 L20 P1 Z0\n', written)
                self.assertNotIn(b'G1 X2\n', written)

    def test_temporary_command_error_does_not_unblock_resume(self):
        clock = FakeClock()
        written, queue, completed = [], [], []

        def write(data):
            written.append(data)
            if data == b'?':
                queue.append(b'<Tool|A:T>\n')
            elif data == b'$G\n':
                queue.append(b'[GC:G0 G54 G17 G21 G90 G94 M5]\nok\n')
            elif data == b'G10 L20 P1 Z0\n':
                queue.append(b'error:33\n')
            return 'ok'

        def readline():
            clock.now += 1
            return b'ALARM:1\n' if clock.now > 30 else (queue.pop(0) if queue else b'')

        res = stream_lines(['M6', 'G1 X2'], write, readline, tool_change_ack=True,
                           poll_status=True, poll_interval=5, clock=clock, stall_timeout=10,
                           tool_command=lambda: 'G10 L20 P1 Z0',
                           on_tool_command_done=lambda text: (completed.append(text), write(b'~')))
        self.assertEqual(res['status'], 'error')
        self.assertEqual(res['last_text'], 'error:33')
        self.assertEqual(res.get('tool_command'), 'G10 L20 P1 Z0')
        self.assertEqual(res['done'], 0)
        self.assertEqual(completed, [])
        self.assertNotIn(b'~', written)
        self.assertNotIn(b'G1 X2\n', written)

    def test_m6_temporary_commands_before_cycle_start(self):
        for ack_timing in ('after_resume', 'before_tool', 'after_a3', 'missing'):
            with self.subTest(ack_timing=ack_timing):
                early_ack = ack_timing in ('before_tool', 'after_a3')
                missing_ack = ack_timing == 'missing'
                clock = FakeClock()
                written, queue, progress, completed = [], [], [], []
                commands = ['$J=G91 G21 Z1 F100', 'G10 L20 P1 Z0']
                changing = False
                resumed = False
                notified = []

                def write(data):
                    nonlocal changing, resumed
                    written.append(data)
                    if data == b'?':
                        state = 'Tool' if changing and not resumed else 'Idle'
                        queue.append(('<%s|MPos:0,0,0>\n' % state).encode())
                    elif data == b'\xa3':
                        self.assertTrue(changing)
                        if ack_timing == 'after_a3':
                            queue.extend([b'o', b'k\n'])
                    elif data == b'$G\n':
                        queue.extend([b'[GC:G0 G54 G17 G21 G90 G94 M5]\n', b'o', b'k\n'])
                    elif data == b'~':
                        resumed = True
                        if not early_ack and not missing_ack:
                            queue.extend([b'o', b'k\n'])
                    elif data == b'M6\n':
                        self.assertEqual(progress, [(1, 3)])
                        changing = True
                        if ack_timing == 'before_tool':
                            queue.extend([b'o', b'k\n'])
                    else:
                        if data == b'G1 X2\n':
                            self.assertTrue(resumed)
                            self.assertFalse(missing_ack)
                        elif changing:
                            self.assertIn(b'\xa3', written)
                            self.assertFalse(resumed)
                            if data.startswith(b'$J='):
                                queue.extend([b'<Jog|MPos:0,0,1|A:T>\n', b'<Tool|A:T>\n'])
                            else:
                                queue.append(b'<Idle|MPos:0,0,1|A:T>\n')
                        queue.extend([b'o', b'k\n'])
                    return 'ok'

                def readline():
                    clock.now += 1
                    if clock.now > 80:
                        return b'ALARM:1\n'
                    return queue.pop(0) if queue else b''

                def tool_command():
                    self.assertEqual(len(notified), 1)
                    return commands.pop(0) if commands else None

                def command_done(text):
                    completed.append(text)
                    self.assertEqual(progress, [(1, 3), (2, 3)] if early_ack else [(1, 3)])
                    if len(completed) == 2:
                        write(b'~')

                res = stream_lines(['G1 X1', 'M6', 'G1 X2'], write, readline,
                                   tool_change_ack=True, poll_status=True, poll_interval=5,
                                   clock=clock, stall_timeout=10, tool_command=tool_command,
                                   on_tool_change=lambda: notified.append(True),
                                   on_tool_command_done=command_done,
                                   on_progress=lambda d, t: progress.append((d, t)))
                self.assertEqual(completed, ['ok', 'ok'])
                self.assertEqual(written.count(b'\xa3'), 1)
                self.assertEqual(res['status'], 'timeout' if missing_ack else 'finished')
                self.assertEqual(res['done'], 1 if missing_ack else 3)
                if missing_ack:
                    self.assertNotIn(b'G1 X2\n', written)

    def test_m6_drains_earlier_lines_and_waits_after_early_ok(self):
        clock = FakeClock()
        written, queue = [], []
        pending = []
        acknowledged = False

        def write(data):
            nonlocal acknowledged
            written.append(data)
            if data == b'?':
                if b'M6\n' in written:
                    state = 'Tool' if clock.now < 12 else 'Idle'
                    queue.append(('<%s|MPos:0,0,0>\n' % state).encode())
                else:
                    queue.append(b'<Run|MPos:0,0,0>\n')
            elif data == b'\xa3':
                acknowledged = True
            elif data == b'$G\n':
                queue.append(b'[GC:G0 G54 G17 G21 G90 G94 M5]\nok\n')
            else:
                if data == b'M6\n':
                    self.assertFalse(pending)
                if data == b'G1 X2\n':
                    self.assertTrue(acknowledged)
                    self.assertGreaterEqual(clock.now, 12)
                pending.append(data)
            return 'ok'

        def readline():
            clock.now += 1
            self.assertLess(clock.now, 50)
            if pending and (pending[0] != b'G1 X1\n' or clock.now >= 8):
                pending.pop(0)
                return b'ok\n'
            return queue.pop(0) if queue else b''

        res = stream_lines(['G1 X1', 'M6', 'G1 X2'], write, readline,
                           tool_change_ack=True, poll_status=True, clock=clock)
        self.assertEqual(res['status'], 'finished')
        self.assertEqual(written.count(b'\xa3'), 1)

    def test_a_lost_status_reply_does_not_stop_polling(self):
        clock = FakeClock()
        written, queue, dropped = [], [], []

        def write(data):
            written.append(data)
            if data == b'?':
                if not dropped:
                    dropped.append(True)  # the first status reply never arrives
                else:
                    queue.append(b'<Idle|MPos:0,0,0>\n')
            else:
                queue.append(b'ok\n')
            return 'ok'

        def readline():
            clock.now += 0.1
            self.assertLess(clock.now, 100)
            return queue.pop(0) if queue else b''

        res = stream_lines(['G1 X1', 'M6'], write, readline, poll_status=True, poll_interval=0.2,
                           clock=clock, stall_timeout=5, tool_change_ack=True)
        self.assertEqual(res['status'], 'finished')
        self.assertIn(b'M6\n', written)

    def test_a_tool_state_without_a_manual_ack_clears_on_idle(self):
        clock = FakeClock()
        written, queue, notified = [], [], []

        def write(data):
            written.append(data)
            if data == b'?':
                if notified:
                    queue.append(b'<Idle|MPos:0,0,0>\n')
                    queue.append(b'ok\n')  # the M6 is acknowledged after the change
                else:
                    queue.append(b'<Tool|MPos:0,0,0>\n')
            elif data != b'M6\n':
                queue.append(b'ok\n')
            return 'ok'

        def readline():
            clock.now += 0.1
            self.assertLess(clock.now, 100)
            return queue.pop(0) if queue else b''

        res = stream_lines(['M6', 'G1 X2'], write, readline, poll_status=True, poll_interval=0.2,
                           clock=clock, stall_timeout=5,
                           on_tool_change=lambda: notified.append(True))
        self.assertEqual(res['status'], 'finished')
        self.assertEqual(notified, [True])
        self.assertIn(b'G1 X2\n', written)

    def test_all_lines_sent_in_order(self):
        lines = ['G1 X%d Y%d F100' % (i, i) for i in range(200)]
        port = FakeStreamPort()
        res = run(port, lines)
        self.assertEqual(res['status'], 'finished')
        self.assertEqual(res['done'], 200)
        self.assertEqual(res['in_flight'], 0)
        self.assertEqual([d.decode().strip() for d in port.written], lines)

    def test_buffer_never_overflows_and_is_used(self):
        lines = ['G1 X%d.123456 Y%d.654321 F1000' % (i, i) for i in range(100)]
        port = FakeStreamPort()
        run(port, lines)
        self.assertLessEqual(port.max_used, 127)
        self.assertGreater(port.max_used, 60)

    def test_buffer_fills_to_the_last_byte(self):
        # 2 lines of 10 bytes fit in 20 bytes: the fill condition is 'used + len > rx_size'
        port = FakeStreamPort(rx_size=20)
        res = run(port, ['G1 X1 Y12'] * 6, rx_size=20)
        self.assertEqual(res['status'], 'finished')
        self.assertEqual(port.max_used, 20)

    def test_error_stops(self):
        port = FakeStreamPort(errors={5: 'error:20', 8: 'error:33'})
        res = run(port, ['G1 X%d' % i for i in range(20)])
        self.assertEqual(res['status'], 'error')
        self.assertEqual(res['errors'], [(5, 'error:20')])

    def test_check_mode_counts_errors(self):
        port = FakeStreamPort(errors={2: 'error:20', 7: 'error:33'})
        res = run(port, ['G1 X%d' % i for i in range(10)], check_mode=True)
        self.assertEqual(res['status'], 'finished')
        self.assertEqual([e[0] for e in res['errors']], [2, 7])

    def test_alarm_stops(self):
        port = FakeStreamPort(alarm_at=3)
        res = run(port, ['G1 X%d' % i for i in range(10)])
        self.assertEqual(res['status'], 'alarm')

    def test_stop_request(self):
        port = FakeStreamPort()
        calls = []

        def write(data):
            calls.append(data)
            if len(calls) > 3:
                return 'cancelled'
            return port.write(data)

        res = stream_lines(['G1 X%d' % i for i in range(500)], write, port.readline)
        self.assertEqual(res['status'], 'cancelled')
        # nothing is written after the status 'cancelled'
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(port.written), 3)

    def test_stale_at_the_first_write(self):
        calls = []

        def write(data):
            calls.append(data)
            return 'stale'

        def readline():
            raise AssertionError("nothing was written, so nothing is read")

        res = stream_lines(['G1 X1', 'G1 X2'], write, readline, poll_status=True)
        self.assertEqual(res['status'], 'stale')
        self.assertEqual(res['done'], 0)
        self.assertEqual(res['in_flight'], 0)
        self.assertEqual(len(calls), 1)

    def test_port_error_after_writes(self):
        port = FakeStreamPort()
        calls = []

        def write(data):
            calls.append(data)
            if len(calls) == 4:
                return 'port_error'
            return port.write(data)

        res = stream_lines(['G1 X%d' % i for i in range(20)], write, port.readline)
        self.assertEqual(res['status'], 'port_error')
        self.assertEqual(len(calls), 4)
        self.assertEqual(res['in_flight'], 3)

    def test_port_error_on_poll_write(self):
        port = FakeStreamPort()

        def write(data):
            return 'port_error' if data == b'?' else port.write(data)

        res = stream_lines(['G1 X1'], write, port.readline, poll_status=True)
        self.assertEqual(res['status'], 'port_error')

    def test_readline_exception_is_port_error(self):
        port = FakeStreamPort()

        def readline():
            raise serial.SerialException("device lost")

        self.assertEqual(stream_lines(['G1 X1'], port.write, readline)['status'], 'port_error')

        def readline_os():
            raise OSError("device lost")

        self.assertEqual(stream_lines(['G1 X1'], port.write, readline_os)['status'], 'port_error')

    def test_progress_and_status(self):
        progress, status = [], []
        port = FakeStreamPort()
        run(port, ['G1 X1', 'G1 X2'], on_progress=lambda d, t: progress.append((d, t)),
            poll_status=True, poll_interval=0.0, on_status=status.append)
        self.assertEqual(progress[-1], (2, 2))
        self.assertTrue(status)
        self.assertGreaterEqual(port.polls, 1)

    def test_on_line_has_job_lines_and_replies_only(self):
        seen = []
        port = FakeStreamPort()
        run(port, ['G1 X1', 'G1 X2'], on_line=seen.append, poll_status=True, poll_interval=0.0)
        self.assertEqual(seen.count('G1 X1'), 1)
        self.assertEqual(seen.count('G1 X2'), 1)
        self.assertEqual(seen.count('ok'), 2)
        self.assertEqual(len(seen), 4)

    def test_long_line_rejected(self):
        lines = ['G1 X%d' % i for i in range(10)]
        lines[5] = 'G1 X1 ' + 'Y' * 80
        port = FakeStreamPort()
        res = run(port, lines)
        self.assertEqual(res['status'], 'line_too_long')
        self.assertEqual(res['line_index'], 5)
        # the check is before the first write
        self.assertEqual(port.written, [])
        self.assertEqual(port.polls, 0)

    def test_line_limit_counts_the_line_end(self):
        port = FakeStreamPort()
        self.assertEqual(run(port, ['G' * 79])['status'], 'finished')
        self.assertEqual(run(FakeStreamPort(), ['G' * 80])['status'], 'line_too_long')

    def test_validate_stream_lines(self):
        self.assertIsNone(validate_stream_lines(['G1 X1', 'G' * 79]))
        self.assertEqual(validate_stream_lines(['G1 X1', 'G' * 80, 'G' * 90]), 1)
        # the limit is min(rx_size, GRBL_MAX_LINE)
        self.assertEqual(validate_stream_lines(['G1 X1 Y1'], rx_size=8), 0)
        self.assertIsNone(validate_stream_lines(['G1 X1 Y1'], rx_size=9))
        self.assertEqual(validate_stream_lines(['G' * 80], rx_size=500), 0)
        # encoded bytes, not characters
        self.assertEqual(validate_stream_lines(['(' + '\u00e9' * 40 + ')']), 0)
        self.assertEqual((GRBL_RX_BUFFER, GRBL_MAX_LINE), (127, 80))

    def test_dwell_seconds(self):
        self.assertEqual(dwell_seconds('G4 P60'), 60.0)
        self.assertEqual(dwell_seconds('G04 P0.5'), 0.5)
        self.assertEqual(dwell_seconds('g4p2.5 (wait)'), 2.5)
        self.assertEqual(dwell_seconds('G4'), 0.0)
        self.assertEqual(dwell_seconds('G1 X1 P60'), 0.0)
        self.assertEqual(dwell_seconds('G41 P60'), 0.0)
        self.assertEqual(dwell_seconds('M3 S1000'), 0.0)
        self.assertEqual(dwell_seconds('(G4 P60)'), 0.0)

    def test_timeout(self):
        clock = FakeClock()
        port = TimedPort(clock)
        res = run(port, ['G1 X1'], clock=clock, stall_timeout=30.0)
        self.assertEqual(res['status'], 'timeout')
        self.assertEqual(res['in_flight'], 1)
        self.assertGreater(clock.now, 30.0)
        self.assertLessEqual(clock.now, 32.0)

    def test_status_does_not_refresh_stall(self):
        # an Idle report and an unchanged Run report are not progress
        for report in (lambda now: '<Idle|MPos:0.000,0.000,0.000|FS:0,0>',
                       lambda now: '<Run|MPos:5.000,0.000,0.000|FS:500,0>'):
            clock = FakeClock()
            port = TimedPort(clock, report=report)
            res = run(port, ['G1 X1'], clock=clock, stall_timeout=30.0, poll_status=True)
            self.assertEqual(res['status'], 'timeout')
            self.assertLessEqual(clock.now, 33.0)

    def test_moving_run_refreshes_stall(self):
        clock = FakeClock()
        port = TimedPort(clock, ack_at=100.0, report=lambda now: '<Run|MPos:%.3f,0.000,0.000|FS:500,0>' % now)
        res = run(port, ['G1 X500'], clock=clock, stall_timeout=30.0, poll_status=True)
        self.assertEqual(res['status'], 'finished')


    def test_hold_refreshes_stall(self):
        for state in ('Hold:0', 'Door:1', 'Jog', 'Home'):
            clock = FakeClock()
            port = TimedPort(clock, ack_at=100.0, report=lambda now, s=state: '<%s|MPos:0.000,0.000,0.000>' % s)
            res = run(port, ['G1 X1'], clock=clock, stall_timeout=30.0, poll_status=True)
            self.assertEqual(res['status'], 'finished', state)

    def test_long_dwell_polled(self):
        clock = FakeClock()
        port = TimedPort(clock, ack_at=70.0, report=lambda now: '<Idle|MPos:0.000,0.000,0.000|FS:0,0>')
        res = run(port, ['G4 P60'], clock=clock, stall_timeout=30.0, poll_status=True)
        self.assertEqual(res['status'], 'finished')

    def test_dwell_extension_is_set_once_when_polled(self):
        clock = FakeClock()
        port = TimedPort(clock, report=lambda now: '<Idle|MPos:0.000,0.000,0.000|FS:0,0>')
        res = run(port, ['G4 P60'], clock=clock, stall_timeout=30.0, poll_status=True)
        self.assertEqual(res['status'], 'timeout')
        self.assertGreater(clock.now, 90.0)
        self.assertLessEqual(clock.now, 93.0)

    def test_long_dwell_not_polled(self):
        clock = FakeClock()
        port = TimedPort(clock, ack_at=70.0)
        res = run(port, ['G4 P60'], clock=clock, stall_timeout=30.0, poll_status=False)
        self.assertEqual(res['status'], 'finished')
        self.assertEqual(port.polls, 0)
        clock = FakeClock()
        port = TimedPort(clock, ack_at=100.0)
        res = run(port, ['G4 P60'], clock=clock, stall_timeout=30.0, poll_status=False)
        self.assertEqual(res['status'], 'timeout')
        self.assertGreater(clock.now, 90.0)
        self.assertLessEqual(clock.now, 92.0)

    def test_dwell_starts_when_the_g4_is_the_oldest_line(self):
        # not polled: line 0 is acknowledged at 20 s, then the G4 gets its 60 s from that time
        clock = FakeClock()

        class Port(TimedPort):
            def readline(self):
                self.clock.now += self.step
                assert self.clock.now <= self.limit, "the streamer did not time out"
                if self.pending and self.clock.now in (20.0, 100.0):
                    self.pending.pop(0)
                    return b'ok\r\n'
                return b''

        res = run(Port(clock), ['G1 X1', 'G4 P60'], clock=clock, stall_timeout=30.0, poll_status=False)
        self.assertEqual(res['status'], 'finished')


if __name__ == '__main__':
    unittest.main()
