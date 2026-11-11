"""Run: .venv/Scripts/python.exe -m pytest tests/test_dimension_visual.py -q -p no:cacheprovider"""

import math
import os
import sys
import unittest

if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appGUI.DimensionVisual import dimension_layout


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


class TestDimensionLayout(unittest.TestCase):
    def test_long_segment_breaks_the_line_around_the_text(self):
        # 100 units at 10 px/unit = 1000 px: 40 px of text fits easily
        lay = dimension_layout((0, 0), (100, 0), 10.0, 40.0)
        self.assertTrue(lay['gap'])
        self.assertEqual(len(lay['lines']), 2)
        self.assertAlmostEqual(lay['text_pos'][0], 50.0)
        self.assertAlmostEqual(lay['text_pos'][1], 0.0)
        # the gap holds the text and the pad on both sides (data units = px / px_per_unit)
        gap = _dist(lay['lines'][0][1], lay['lines'][1][0])
        self.assertAlmostEqual(gap, (40.0 + 2 * 4.0) / 10.0)
        self.assertEqual(lay['lines'][0][0], (0.0, 0.0))
        self.assertEqual(lay['lines'][1][1], (100.0, 0.0))

    def test_short_segment_keeps_the_line_whole_and_moves_the_text_off(self):
        # 3 units at 10 px/unit = 30 px: no room for 2 arrows and the text
        lay = dimension_layout((0, 0), (3, 0), 10.0, 40.0)
        self.assertFalse(lay['gap'])
        self.assertEqual(lay['lines'], [((0.0, 0.0), (3.0, 0.0))])
        self.assertAlmostEqual(lay['text_pos'][0], 1.5)
        # above the line (reading direction up), text_offset_px away
        self.assertAlmostEqual(lay['text_pos'][1], 10.0 / 10.0)

    def test_arrow_tips_sit_on_the_endpoints_and_point_outward(self):
        p0, p1 = (1.0, 2.0), (31.0, 42.0)
        lay = dimension_layout(p0, p1, 5.0, 30.0)
        self.assertEqual(len(lay['arrows']), 2)
        tips = [tri[0] for tri in lay['arrows']]
        for tip, end in zip(tips, (p0, p1)):
            self.assertAlmostEqual(tip[0], end[0])
            self.assertAlmostEqual(tip[1], end[1])
        for tri, end, other in zip(lay['arrows'], (p0, p1), (p1, p0)):
            base_mid = ((tri[1][0] + tri[2][0]) / 2, (tri[1][1] + tri[2][1]) / 2)
            # the base is inside the segment: the tip is the outer end
            self.assertLess(_dist(base_mid, other), _dist(end, other))

    def test_text_angle_is_never_upside_down(self):
        cases = {(1, 0): 0.0, (-1, 0): 0.0, (0, 1): 90.0, (0, -1): 90.0, (-1, -1): 45.0, (-1, 1): -45.0}
        for (dx, dy), expected in cases.items():
            lay = dimension_layout((0, 0), (dx * 50, dy * 50), 10.0, 20.0)
            self.assertAlmostEqual(lay['text_angle'], expected, msg=str((dx, dy)))
            self.assertTrue(-90.0 < lay['text_angle'] <= 90.0)

    def test_zero_length_has_no_lines_and_no_arrows(self):
        lay = dimension_layout((5, 5), (5, 5), 10.0, 20.0)
        self.assertEqual(lay['lines'], [])
        self.assertEqual(lay['arrows'], [])
        self.assertFalse(lay['gap'])

    def test_arrow_and_gap_sizes_stay_constant_in_pixels_at_every_zoom(self):
        for ppu in (2.0, 50.0):
            lay = dimension_layout((0, 0), (0, 400), ppu, 30.0)
            self.assertTrue(lay['gap'])
            tri = lay['arrows'][0]
            base_mid = ((tri[1][0] + tri[2][0]) / 2, (tri[1][1] + tri[2][1]) / 2)
            self.assertAlmostEqual(_dist(tri[0], base_mid) * ppu, 10.0)
            self.assertAlmostEqual(_dist(tri[1], tri[2]) * ppu, 7.0)
            gap = _dist(lay['lines'][0][1], lay['lines'][1][0])
            self.assertAlmostEqual(gap * ppu, 38.0)


class TestDimensionLinesCanvas(unittest.TestCase):
    def test_set_data_zoom_and_clear_run_on_the_2d_and_the_headless_canvas(self):
        from PyQt6 import QtWidgets
        from vispy import app
        app.use_app('pyqt6')
        from appGUI.VisPyCanvas import VisPyCanvas
        from appGUI.NullCanvas import NullCanvas
        from appGUI.DimensionVisual import DimensionLines

        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = VisPyCanvas()
        self.addCleanup(canvas.close)
        canvas.size = (400, 300)
        segments = [((0, 0), (50, 20), '53.852'), ((10, 10), (10.1, 10), '0.1')]
        for target in (canvas, NullCanvas()):
            dim = DimensionLines(target)
            dim.set_data(segments)
            self.assertEqual(dim.segments, segments)
            if target is canvas:
                canvas.view.camera.zoom(0.5)
                canvas.render()
            dim.set_color('#FF0000')
            dim.refresh()
            dim.clear()
            self.assertEqual(dim.segments, [])
            dim.delete()


if __name__ == '__main__':
    unittest.main()
