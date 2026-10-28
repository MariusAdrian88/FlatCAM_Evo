"""Excellon drill-file parser: modes (drill / route), units, zeros, tools, number formats, plated flags."""
import math
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appParsers.ParseExcellon import Excellon  # noqa: E402
from camlib import object_circle_steps  # noqa: E402
from tests.helpers_objects import _headless_qt, make_app_0b  # noqa: F401,E402


@pytest.fixture(autouse=True)
def factory_excellon_defaults(monkeypatch):
    # a headless App earlier in the same worker propagates its preferences into the class defaults
    monkeypatch.setattr(Excellon, 'defaults', {
        "zeros": "L", "excellon_format_upper_mm": '3', "excellon_format_lower_mm": '3',
        "excellon_format_upper_in": '2', "excellon_format_lower_in": '4', "excellon_units": 'INCH',
        "excellon_circle_steps": '32'})


def parse(text, **kwargs):
    app = make_app_0b()
    exc = Excellon(app, **kwargs)
    assert exc.parse_lines(text.splitlines(keepends=True)) is None
    return exc, app


def dump(exc):
    return {t: (round(v['tooldia'], 5),
                [(round(p.x, 4), round(p.y, 4)) for p in v['drills']],
                [((round(a.x, 4), round(a.y, 4)), (round(b.x, 4), round(b.y, 4))) for a, b in v['slots']])
            for t, v in exc.tools.items()}


METRIC = "M48\nMETRIC\nT1C1.0\n%\nT1\n"

# text, preference kwargs, units, tools: {tool: (diameter, drills, slots)}
CASES = {
    'route then drills with another tool': (
        "M48\nMETRIC\nT1C1.0\nT2C0.8\n%\nG90\nT1\nG00X10.0Y10.0\nM15\nG01X15.0Y10.0\nM16\nG05\nT2\nX1.0Y1.0\nX2.0Y2.0\nM30\n",
        {}, 'MM', {1: (1.0, [], [((10, 10), (15, 10))]), 2: (0.8, [(1, 1), (2, 2)], [])}),
    'M72 in the header, tools of an inch file': (
        "M48\nM72\nT1C0.0300\nT2C0.1250\nT3C0.1500\n%\nT1\nX1.0Y1.0\nM30\n",
        {'excellon_units': 'METRIC'}, 'IN', {1: (0.03, [(1, 1)], []), 2: (0.125, [], []), 3: (0.15, [], [])}),
    'units switch in the body converts what was read': (
        "M48\nINCH,TZ\nT1C0.0350\n%\nT1\nX1.0Y1.0\nM71\nX25.4Y25.4\nM30\n",
        {}, 'MM', {1: (0.889, [(25.4, 25.4), (25.4, 25.4)], [])}),
    'repeat with decimal coordinates': (
        METRIC + "X0.0Y0.0\nR3X1.0\nX5.0\nM30\n",
        {}, 'MM', {1: (1.0, [(0, 0), (1, 0), (2, 0), (3, 0), (5, 0)], [])}),
    'M80 / M70 mirror or swap only the pattern copy of their M02 line': (
        METRIC + "M25\nX1.0Y1.0\nX2.0Y1.0\nM01\nM02X10.0Y0.0M80\nM02X0.0Y10.0M70\nX3.0Y3.0\nM30\n",
        {}, 'MM', {1: (1.0, [(1, 1), (2, 1), (9, 1), (8, 1), (1, 11), (1, 12), (3, 3)], [])}),
    'G85 slot sets the modal coordinates and a slot can start from them': (
        METRIC + "X1.0Y1.0\nX2.0G85X4.0\nX6.0Y6.0\nM30\n",
        {}, 'MM', {1: (1.0, [(1, 1), (6, 6)], [((2, 1), (4, 1))])}),
    'incremental coordinates': (
        METRIC + "G91\nX1.0Y1.0\nX1.0Y1.0\nG90\nX9.0Y9.0\nM30\n",
        {}, 'MM', {1: (1.0, [(1, 1), (2, 2), (9, 9)], [])}),
    'Allegro tools and units from the comments': (
        ";HEADER:\n;Holesize 1. = 35.000000 Tolerance = +0.000000/-0.000000 PLATED MILS Quantity = 1\n"
        ";Holesize 2. = 125.000000 Tolerance = +0.000000/-0.000000 NON_PLATED MILS Quantity = 1\n%\n"
        "G90\nX01000Y01000\nT02\nX02000Y02000\nM30\n",
        {}, 'IN', {1: (0.035, [(1, 1)], []), 2: (0.125, [(2, 2)], [])}),
}


@pytest.mark.parametrize('name', list(CASES))
def test_parse_result(name):
    text, kwargs, units, tools = CASES[name]
    exc, _app = parse(text, **kwargs)
    assert exc.units == units
    assert dump(exc) == {t: (dia, drills, [((a[0], a[1]), (b[0], b[1])) for a, b in slots])
                         for t, (dia, drills, slots) in tools.items()}


def test_route_half_circle_is_cut_as_short_segments():
    # G03 (counterclockwise) with the radius A, G02 (clockwise) with the centre offset I/J
    for command, clockwise in (('G03X10.0Y0.0A5.0', False), ('G02X10.0Y0.0I5.0J0.0', True)):
        exc, _app = parse(METRIC + "G00X0.0Y0.0\nM15\n%s\nM16\nM30\n" % command)
        slots = exc.tools[1]['slots']
        assert len(slots) >= 8
        assert (slots[0][0].x, slots[0][0].y) == (0, 0)
        assert (round(slots[-1][1].x, 9), round(slots[-1][1].y, 9)) == (10, 0)
        for (_start, stop), (next_start, _next_stop) in zip(slots, slots[1:]):
            assert (stop.x, stop.y) == (next_start.x, next_start.y)
        for _start, stop in slots:
            assert math.hypot(stop.x - 5, stop.y) == pytest.approx(5, abs=1e-9)
        # counterclockwise from (0, 0) to (10, 0) around (5, 0) passes under the centre, clockwise over it
        assert (min(s[1].y for s in slots) < -4.9) is (not clockwise)
        assert (max(s[1].y for s in slots) > 4.9) is clockwise


def test_plated_flag_per_tool():
    cases = [
        ("; #@! TF.FileFunction,NonPlated,1,2,NPTH\nM48\nMETRIC\n; #@! TA.AperFunction,Plated,PTH,ViaDrill\nT1C0.3\n"
         "; #@! TA.AperFunction,NonPlated,NPTH,MechanicalDrill\nT2C3.0\n; #@! TD\nT3C1.0\n%\nT1\nX1.0Y1.0\n",
         {1: True, 2: False, 3: False}),
        (";TYPE=PLATED\nM48\nMETRIC\nT1C0.3\nT2C0.5\n%\nT1\nX1.0Y1.0\n", {1: True, 2: True}),
        (CASES['Allegro tools and units from the comments'][0], {1: True, 2: False}),
        ("M48\nMETRIC\nT1C0.3\n%\nT1\nX1.0Y1.0\n", {}),
    ]
    for text, tool_plated in cases:
        exc, _app = parse(text)
        assert {t: v['plated'] for t, v in exc.tools.items() if 'plated' in v} == tool_plated


def test_equal_diameters_are_merged_only_on_request():
    text = "M48\nMETRIC\nT1C1.0\nT2C1.0\nT3C0.5\n%\nT1\nX1.0Y1.0\nT2\nX2.0Y2.0\nX3.0Y3.0G85X4.0Y3.0\nT3\nX9.0Y9.0\nM30\n"
    exc, _app = parse(text)
    assert sorted(exc.tools) == [1, 2, 3]

    merged = Excellon(make_app_0b())
    merged.merge_equal_tools = True
    merged.parse_lines(text.splitlines(keepends=True))
    assert dump(merged) == {1: (1.0, [(1, 1), (2, 2)], [((3, 3), (4, 3))]), 3: (0.5, [(9, 9)], [])}


def test_a_file_with_a_utf8_bom_is_read(tmp_path):
    path = tmp_path / 'bom.drl'
    path.write_bytes(b"\xef\xbb\xbfM48\r\nMETRIC\r\nT1C1.0\r\n%\r\nT1\r\nX1.0Y1.0\r\nM30\r\n")
    app = make_app_0b()
    exc = Excellon(app)
    assert exc.parse_file(str(path)) is None
    assert dump(exc) == {1: (1.0, [(1, 1)], [])}


def test_hole_and_slot_circles_follow_the_circle_preference():
    for mode in ('fixed', 'adaptive'):
        app = make_app_0b(excellon_circle_steps_mode=mode, excellon_circle_steps=24, excellon_circle_tolerance=5.0)
        exc = Excellon(app)
        text = ("M48\nMETRIC\nT1C0.3\nT2C3.0\n%\nT1\nX0.0Y0.0\nT2\nX5.0Y5.0\nG00X20.0Y0.0\nM15\nG01X30.0Y0.0\nM16\n"
                "M30\n")
        assert exc.parse_lines(text.splitlines(keepends=True)) is None
        exc.create_geometry()
        counts = {t: [len(g.exterior.coords) - 1 for g in v['solid_geometry']] for t, v in exc.tools.items()}
        small, large = (object_circle_steps(app.options, 'excellon', r) for r in (0.15, 1.5))
        if mode == 'fixed':
            assert small == large == 24
        else:
            assert small < large
        assert counts[1] == [small]
        # the drill and the slot of one tool have the same segments: a slot adds the 2 vertices of its straight sides
        assert counts[2] == [large, large + 2]


def test_convert_units_moves_holes_and_diameters():
    exc, _app = parse("M48\nINCH,TZ\nT1C0.0400\n%\nT1\nX1.0Y2.0\nX1.0Y1.0G85X2.0Y1.0\nM30\n")
    exc.convert_units('MM')
    assert exc.units == 'MM'
    assert dump(exc) == {1: (1.016, [(25.4, 50.8)], [((25.4, 25.4), (50.8, 25.4))])}
    assert len(exc.solid_geometry) == 2


@pytest.fixture
def pcb_wizard(_headless_qt):
    from appPlugins.ToolPcbWizard import PcbWizard

    app = MagicMock()
    app.decimals = 4
    app.resource_location = 'assets/resources'
    app.options = {}
    app.defaults = {}
    tool = PcbWizard(app)
    tool.set_tool_ui()
    yield tool
    tool.deleteLater()


def test_pcbwizard_inf_format_is_set_in_the_gui_thread(pcb_wizard, tmp_path, _headless_qt):
    inf = tmp_path / 'board.inf'
    inf.write_text("T1 0.8\nT2 1.2\n4.2 format, metric, absolute\n")
    ui = pcb_wizard.ui
    threads = []
    for widget in (ui.units_radio, ui.int_entry, ui.frac_entry):
        widget.set_value = lambda value, set_value=widget.set_value: (
            threads.append(threading.current_thread()), set_value(value))
    # load_inf() runs in the worker thread of the app
    worker = threading.Thread(target=pcb_wizard.load_inf, args=[str(inf)])
    worker.start()
    worker.join()
    _headless_qt.processEvents()
    # Qt widgets are not thread-safe: load_inf() set the format fields from the worker thread
    assert threads and set(threads) == {threading.main_thread()}
    assert (ui.units_radio.get_value(), ui.int_entry.get_value(), ui.frac_entry.get_value()) == ('METRIC', 4, 2)
    assert pcb_wizard.tools_from_inf == {1: 0.8, 2: 1.2}


def test_pcbwizard_import_uses_the_format_corrected_after_loading(pcb_wizard, tmp_path):
    # neither file has the format: the form keeps 2:4 INCH until the user corrects it
    (tmp_path / 'board.drl').write_text("M48\n%\nT1\nX010000Y020000\nM30\n")
    (tmp_path / 'board.inf').write_text("T1 0.8\n")
    pcb_wizard.load_excellon(str(tmp_path / 'board.drl'))
    pcb_wizard.load_inf(str(tmp_path / 'board.inf'))
    pcb_wizard.ui.units_radio.set_value('METRIC')
    pcb_wizard.ui.int_entry.set_value(3)
    pcb_wizard.ui.frac_entry.set_value(3)
    imported = []

    def new_object(kind, name, initialize, **kwargs):
        exc = Excellon(make_app_0b())
        try:
            result = initialize(exc, pcb_wizard.app)
        except Exception as err:    # not raised in the slot of the button: PyQt would abort the test run
            result = err
        imported.append((result, exc.units, dump(exc)))
    pcb_wizard.app.app_obj.new_object.side_effect = new_object

    # the file was built with the format of the loading; the second Import parsed the same, exhausted StringIO
    pcb_wizard.ui.import_button.click()
    pcb_wizard.ui.import_button.click()
    assert imported == [(None, 'MM', {1: (0.8, [(10, 20)], [])})] * 2
