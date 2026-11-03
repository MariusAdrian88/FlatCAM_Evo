"""Characterization of the click-pick rules of the Align Objects snap (AlignGen), ToolDblSided.on_mouse_click_release
and ToolPunchGerber.find_pad. They must pass before and after the move to ``feature_access.iter_features``.

The unbound methods run with a fake ``self``; no GUI is needed."""

from unittest.mock import MagicMock, Mock

import pytest
from shapely.geometry import LineString, Point, box

from appPlugins.ToolAlignObjects.AlignGen import build_snap_index, snap
from appPlugins.ToolDblSided import DblSidedTool
from appPlugins.ToolPunchGerber import ToolPunchGerber

from tests.helpers_objects import app, excellon, gerber, _headless_qt, make_excellon, make_gerber  # noqa: F401,E402


def _event(pos):
    ev = Mock()
    ev.pos = pos
    ev.button = 1
    return ev


def _fake_app():
    fake_app = MagicMock()
    fake_app.selection_type = None
    fake_app.dec_format = lambda value, decimals: round(value, decimals)
    return fake_app


def _gerber_like(tools):
    obj = Mock()
    obj.kind = 'gerber'
    obj.tools = tools
    return obj


def _custom_excellon(app, tools):
    exc = make_excellon(app, name='exc_custom')
    exc.tools = tools
    assert exc.create_geometry() is None
    return exc


# ---------------------------------------------------------------------------------------------------------------
# ToolAlignObjects (AlignGen.build_snap_index + snap)
# ---------------------------------------------------------------------------------------------------------------

def align_click(target, pos):
    """Return the list of snapped points for one click (0 or 1 point)."""
    pt = snap(build_snap_index(target), pos[0], pos[1], 0.01)
    return [] if pt is None else [list(pt)]


class TestAlignGerber:
    @pytest.mark.parametrize('click, center', [
        ((5.2, 5.1), [5.0, 5.0]),         # C flash
        ((15.2, 35.1), [15.0, 35.0]),     # flash after the clear track
    ])
    def test_flash_gives_the_flash_point(self, gerber, click, center):
        assert align_click(gerber, click) == [pytest.approx(center)]

    def test_center_is_the_follow_point_not_the_solid_centroid(self):
        obj = _gerber_like({1: {'geometry': [{'solid': box(0, -1, 2, 1), 'follow': Point(0, 0)}]}})
        assert align_click(obj, (0.5, 0.0)) == [pytest.approx([0.0, 0.0])]

class TestAlignExcellon:
    def test_drill_gives_its_center(self, excellon):
        assert align_click(excellon, (5.1, 12.1)) == [pytest.approx([5.0, 12.0])]

    def test_slot_gives_its_centroid(self, excellon):
        assert align_click(excellon, (24.0, 12.2)) == [pytest.approx([24.0, 12.0])]

# ---------------------------------------------------------------------------------------------------------------
# ToolDblSided
# ---------------------------------------------------------------------------------------------------------------

def dbl_click(exc, pos):
    """Return the list of reference points set in the point entry and the number of on_exit calls."""
    fake = Mock()
    fake.app = _fake_app()
    fake.canvas.translate_coords = lambda p: p
    fake.exc_hole_obj = exc
    fake.decimals = 4
    DblSidedTool.on_mouse_click_release(fake, _event(pos))
    return [c.args[0] for c in fake.ui.point_entry.set_value.call_args_list], fake.on_exit.call_count


class TestDblSided:
    def test_drill_gives_its_center(self, excellon):
        points, exits = dbl_click(excellon, (5.1, 12.1))
        assert points == [pytest.approx((5.0, 12.0))] and exits == 1

    def test_stops_after_the_first_hit_inside_a_tool(self, app):
        exc = _custom_excellon(app, {
            1: {'tooldia': 2.0, 'drills': [Point(5, 5), Point(5, 5.2)], 'slots': [], 'solid_geometry': [],
                'data': {}},
        })
        points, exits = dbl_click(exc, (5.0, 5.1))
        assert points == [pytest.approx((5.0, 5.0))] and exits == 1

# ---------------------------------------------------------------------------------------------------------------
# ToolPunchGerber.find_pad
# ---------------------------------------------------------------------------------------------------------------

def find_pad(grb, point, selected_apids):
    fake = Mock()
    fake.grb_obj = grb
    items = []
    for apid in selected_apids:
        it = Mock()
        it.text.return_value = str(apid)
        items.append(it)
    fake.ui.apertures_table.selectedItems.return_value = items
    return ToolPunchGerber.find_pad(fake, point)


class TestFindPad:
    def test_flash_in_a_selected_aperture(self, gerber):
        assert find_pad(gerber, (5.2, 5.1), [10]) == [{'apid': 10, 'idx': 0}]

    def test_track_and_region_are_not_pads(self, gerber):
        assert find_pad(gerber, (20.0, 10.1), [10, 11, 0]) == []
        assert find_pad(gerber, (5.0, 24.0), [0, 10]) == []

    def test_index_is_the_record_position_after_other_records(self):
        grb = _gerber_like({7: {'geometry': [
            {'solid': box(0, 0, 1, 1), 'follow': LineString([(0, 0), (1, 1)])},
            {'clear': box(5, 5, 6, 6), 'follow': Point(5.5, 5.5)},
            {'solid': box(2, 2, 4, 4), 'follow': Point(3, 3)},
        ]}})
        assert find_pad(grb, (3.0, 3.0), [7]) == [{'apid': 7, 'idx': 2}]
        assert find_pad(grb, (5.5, 5.5), [7]) == []      # clear record
        assert find_pad(grb, (0.5, 0.5), [7]) == []      # route
