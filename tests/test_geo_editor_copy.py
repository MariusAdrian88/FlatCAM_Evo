"""Geometry Editor: the Copy tool makes the copies also when the cursor did not move after the reference click."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from shapely.affinity import rotate, translate

from shapely.geometry import LineString, box

from appEditors.appGeoEditor import FCCopy, DrawToolShape


def radio(value):
    return SimpleNamespace(get_value=lambda: value)


def copy_tool(selected, mode='n'):
    """An FCCopy with the shapes already selected, without the Copy plugin GUI."""
    editor = SimpleNamespace(selected=list(selected), app=SimpleNamespace(inform=Mock(), jump_signal=Mock()))
    editor.get_selected = lambda: editor.selected

    tool = FCCopy.__new__(FCCopy)
    tool.draw_app = editor
    tool.complete = False
    tool.points = []
    tool.geometry = None
    tool.origin = None
    tool.destination = None
    tool.util_geo = None
    tool.clicked_postion = None
    tool.has_selection = True
    tool.sel_limit = 30
    tool.draw_cursor_data = Mock()
    tool.copy_tool = SimpleNamespace(ui=SimpleNamespace(
        mode_radio=radio(mode), array_type_radio=radio('linear'), axis_radio=radio('X'), pitch_entry=radio(10.0),
        linear_angle_spinner=radio(0.0), array_size_entry=radio(3)))
    tool.selection_shape = tool.selection_bbox()
    return tool


class TestGeoEditorCopy(unittest.TestCase):

    def setUp(self):
        self.square = DrawToolShape(box(0, 0, 1, 1))
        self.line = DrawToolShape(LineString([(0, 0), (2, 0)]))

    def test_second_click_without_cursor_move_copies_to_the_destination(self):
        tool = copy_tool([self.square, self.line])
        tool.click((0.0, 0.0))      # reference point
        tool.click((3.0, 1.0))      # destination, no mouse move event in between
        self.assertTrue(tool.complete)
        self.assertEqual([shp.geo.bounds for shp in tool.geometry], [(3.0, 1.0, 4.0, 2.0), (3.0, 1.0, 5.0, 1.0)])
        # the source shapes are not changed
        self.assertEqual(self.square.geo.bounds, (0.0, 0.0, 1.0, 1.0))


@pytest.mark.parametrize('direction,sign', [('CW', -1)])
def test_circular_copy_rotates_radially_at_final_destination(direction, sign):
    source = DrawToolShape(LineString([(2, 3), (4, 3)]))
    tool = copy_tool([source], 'a')
    ui = tool.copy_tool.ui
    ui.array_type_radio = radio('circular')
    ui.array_dir_radio, ui.angle_entry = radio(direction), radio(90)
    tool.click((2, 3))
    tool.util_geo = DrawToolShape(box(100, 100, 101, 101))
    tool.click((7, 3))
    for i, result in enumerate(tool.geometry):
        import math
        angle = sign * 90 * i
        expected = translate(rotate(source.geo, angle, origin=(2, 3)),
                             5 * math.cos(math.radians(angle)), 5 * math.sin(math.radians(angle)))
        assert result.geo.equals_exact(expected, 1e-12)
    assert list(source.geo.coords) == [(2, 3), (4, 3)]


if __name__ == '__main__':
    unittest.main()
