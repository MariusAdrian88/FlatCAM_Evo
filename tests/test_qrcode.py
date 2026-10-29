"""QR Code plugin: the Qt-free model (QRCodeGen)."""

import datetime

import pytest
from shapely import box
from shapely.affinity import translate
from shapely.ops import unary_union

from appPlugins.ToolQRCode import QRCodeGen as gen

MM = 25.4


def _qr(text='HELLO', version=1, error='L', box_size=10, border=2, units='MM', **kw):
    return gen.qr_geometry(text, version, error, box_size, border, units, **kw)


# --- sizes ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('units, div', [('IN', MM)])
def test_qr_geometry_size_in_project_units(units, div):
    modules, utility, info = _qr(units=units)
    assert info['version'] == 1 and info['modules'] == 21
    a, b, c, d = utility.bounds
    assert (c - a) == pytest.approx(21 / div, rel=1e-3) and (d - b) == pytest.approx(21 / div, rel=1e-3)
    assert info['width'] == pytest.approx(25 / div, rel=1e-3)
    assert unary_union(modules).area == pytest.approx(utility.area, rel=1e-3)


# --- version -------------------------------------------------------------------------------------------------------
def test_fixed_version_overflow_raises_and_auto_grows():
    long_text = 'X' * 60
    with pytest.raises(gen.QRDataOverflow):
        _qr(text=long_text, version=1)
    _, _, info = _qr(text=long_text, version=None)
    assert info['version'] > 1
    assert gen.qr_info(long_text, None, 'L')['version'] == info['version']
    assert gen.qr_info('A', 3, 'M') == {'version': 3, 'modules': 29}


# --- insert_geometry -----------------------------------------------------------------------------------------------
def test_negative_polarity_and_outline():
    modules, utility, _ = _qr()
    base = dict(border_size=2, box_size=10, rounded='s', units='MM')
    (solid, _tools, outline) = gen.insert_geometry([], {}, modules, utility, (5, 5), polarity='neg',
                                                   with_outline=True, **base)
    mask = translate(box(*utility.bounds).buffer(2.0, join_style='mitre'), 5, 5)
    assert unary_union(solid).area == pytest.approx(mask.area - utility.area, rel=1e-3)
    assert unary_union(outline).equals_exact(translate(utility, 5, 5), 1e-6) or \
        unary_union(outline).symmetric_difference(translate(utility, 5, 5)).area < 1e-6


def test_new_gerber_geometry_and_mask_opening():
    modules, utility, _ = _qr()
    base = dict(border_size=2, box_size=10, rounded='s', units='MM')
    solid, tools = gen.new_gerber_geometry(modules, utility, (3, 4), polarity='pos', **base)
    assert unary_union(solid).symmetric_difference(translate(utility, 3, 4)).area < 1e-6
    assert sorted(tools) == [10] and len(tools[10]['geometry']) == len(modules)
    solid, tools = gen.new_gerber_geometry(modules, utility, (3, 4), polarity='neg', **base)
    mask = gen.mask_opening(utility, (3, 4), **base)
    assert unary_union(solid).area == pytest.approx(mask.area - utility.area, rel=1e-3)
    assert mask.bounds[0] == pytest.approx(utility.bounds[0] - 2 + 3)


# --- utility geometry ----------------------------------------------------------------------------------------------
# --- transforms ----------------------------------------------------------------------------------------------------
def test_mirror_rotate_and_anchor():
    asym = box(0, 0, 4, 2).union(box(0, 2, 1, 4))          # L shape, bbox 4 x 4
    other = box(10, 0, 12, 1)
    ref = unary_union([asym, other])
    # no transform, anchor bl: bbox lower-left goes to the origin
    out = gen.transform_geometry([asym, [other]], 0, False, 'bl')
    assert unary_union([out[0]] + out[1]).bounds == pytest.approx((0, 0, 12, 4))
    # mirror about the vertical axis keeps the bbox centre and flips x
    m = gen.transform_geometry([asym, [other]], 0, True, 'bl')
    assert m[1][0].bounds == pytest.approx((0, 0, 2, 1))
    assert m[0].bounds[0] == pytest.approx(8)
    # rotation 90 CCW swaps width and height, anchor c puts the bbox centre at the origin
    r = gen.transform_geometry([asym, [other]], 90, False, 'c')
    b = unary_union([r[0]] + r[1]).bounds
    assert (b[2] - b[0], b[3] - b[1]) == pytest.approx((4, 12))
    assert (b[0] + b[2]) / 2 == pytest.approx(0, abs=1e-9) and (b[1] + b[3]) / 2 == pytest.approx(0, abs=1e-9)
    # top-right anchor with margin: the margin-expanded bbox corner is the origin
    t = gen.transform_geometry([asym, [other]], 0, False, 'tr', margin=1.0)
    assert unary_union([t[0]] + t[1]).bounds[2] == pytest.approx(-1.0) and \
        unary_union([t[0]] + t[1]).bounds[3] == pytest.approx(-1.0)
    assert ref.area == pytest.approx(unary_union([asym, other]).area)
    with pytest.raises(ValueError):
        gen.transform_geometry([asym], 45, False, 'bl')


# --- preview -------------------------------------------------------------------------------------------------------
# --- images --------------------------------------------------------------------------------------------------------
# --- tokens and warnings -------------------------------------------------------------------------------------------
def test_expand_tokens():
    ctx = {'date': datetime.date(2026, 10, 24), 'time': datetime.time(9, 5, 7), 'project': 'P1', 'object': 'top',
           'serial': 7}
    assert gen.expand_tokens('{date} {time} {project}/{object} #{serial}', ctx, serial_pad=4) == \
        '2026-10-24 09:05 P1/top #0007'
    assert gen.expand_tokens('{unknown}-{serial}', {'serial': 12}) == '{unknown}-12'
    assert gen.expand_tokens('{date}', {'date': '2026-01-02'}) == '2026-01-02'
    assert gen.expand_tokens('{project}', {}) == '{project}'


# --- the plugin controller (headless App) --------------------------------------------------------------------------
import os
import sys
import time
from types import SimpleNamespace

from PyQt6 import QtWidgets

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import appHandlers.appHistory as appHistory
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appGUI.GUIElements import FCFileSaveDialog

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'unify')


@pytest.fixture(scope='module')
def fc():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app = App(qapp=qapp, headless=True)
    appHistory.RECORD_IN_HEADLESS = True
    yield app
    appHistory.RECORD_IN_HEADLESS = False


@pytest.fixture
def tool(fc, monkeypatch):
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    monkeypatch.setattr(FlatCAMObj, 'plot_transformed', lambda *a, **k: True)
    qr = fc.qrcode_tool
    qr.set_tool_ui()
    # the canvas of a headless App has no coordinates: the event position is the canvas position
    monkeypatch.setattr(qr, 'event_position', lambda pos: pos)
    messages = []
    fc.inform.connect(messages.append)
    qr.messages = messages
    yield qr
    fc.inform.disconnect(messages.append)
    qr.on_exit()
    fc.event_is_dragging = False
    for obj in list(fc.collection.get_list()):
        fc.collection.delete_by_name(obj.obj_options['name'], select_project=False)


def _settle(cond, timeout=5.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    QtWidgets.QApplication.processEvents()


def _gerber(fc, name='top'):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, 'unify_rect.gbr'))
    return fc.app_obj.new_object('gerber', name, init, plot=False)


def _area(obj):
    geo = obj.solid_geometry
    return unary_union(geo if isinstance(geo, list) else [geo]).area


def _setup(tool, obj, text='HELLO', **widgets):
    ui = tool.ui
    ui.grb_object_combo.set_value(obj.obj_options['name'])
    ui.text_data.set_value(text)
    ui.version_auto_cb.set_value(False)
    ui.version_entry.set_value(1)
    ui.border_size_entry.set_value(2)
    ui.bsize_entry.set_value(10)
    ui.pol_radio.set_value('pos')
    for key, value in widgets.items():
        getattr(ui, key).set_value(value)
    tool.update_enabled()


def _arm(tool):
    tool.execute()
    _settle(lambda: tool.armed or any('ERROR' in m for m in tool.messages))


def _click(tool, x, y, button=1):
    tool.on_mouse_release(SimpleNamespace(pos=(x, y), button=button))


@pytest.mark.slow
def test_export_keeps_undo_available_and_rejects_bad_colors(fc, tool, tmp_path, monkeypatch):
    png, svg = str(tmp_path / 'a.png'), str(tmp_path / 'a.svg')
    paths = iter([png, svg])
    monkeypatch.setattr(FCFileSaveDialog, 'get_saved_filename', lambda **k: (next(paths), ''))
    tool.ui.text_data.set_value('HELLO')
    tool.ui.fill_color_entry.set_value('#112233')
    tool.ui.back_color_entry.set_value('transparent')
    tool.export_png_file()
    tool.export_svg_file()
    _settle(lambda: os.path.exists(png) and os.path.exists(svg))
    assert os.path.getsize(png) > 0 and '#112233' in open(svg).read()
    assert fc.call_source == 'app'
    assert any('[success]' in m for m in tool.messages)
    tool.ui.fill_color_entry.set_value('notacolor')
    assert tool.export_png_file() == 'fail'
    assert any('ERROR' in m for m in tool.messages)


@pytest.mark.slow
def test_drag_release_does_not_insert_and_a_click_does(fc, tool):
    obj = _gerber(fc)
    _setup(tool, obj)
    before = _area(obj)
    _arm(tool)
    fc.event_is_dragging = True
    _click(tool, 30, 30)
    assert tool.armed and _area(obj) == before
    fc.event_is_dragging = False
    _click(tool, 30, 30)
    assert not tool.armed and fc.call_source == 'app' and _area(obj) != before


@pytest.mark.slow
def test_keep_placing_increments_the_serial_and_stays_armed(fc, tool):
    obj = _gerber(fc)
    _setup(tool, obj, text='S{serial}', multi_cb=True, serial_start_entry=5, serial_step_entry=2,
           serial_pad_entry=3)
    tool.on_serial_start_changed(5)
    _arm(tool)
    assert tool.plan.text == 'S005'
    _click(tool, 30, 30)
    assert tool.armed and tool.serial == 7 and tool.plan.text == 'S007'
    _click(tool, 60, 30)
    assert tool.armed and tool.serial == 9
    _click(tool, 0, 0, button=2)
    assert not tool.armed and fc.call_source == 'app'


@pytest.mark.slow
def test_soldermask_opening_is_added_to_the_other_gerber(fc, tool):
    obj, mask = _gerber(fc, 'top'), _gerber(fc, 'mask')
    _setup(tool, obj, placement_radio='coords', x_entry=50.0, y_entry=40.0, anchor_radio='bl', mask_cb=True)
    tool.ui.mask_object_combo.set_value('mask')
    before = mask.solid_geometry
    tool.execute()
    _settle(lambda: mask.solid_geometry is not before)
    assert mask.bounds()[2] >= 50.0 + 25 and mask.bounds()[3] >= 40.0 + 25


@pytest.mark.slow
def test_undo_of_an_insert_restores_the_gerber(fc, tool):
    obj = _gerber(fc)
    _setup(tool, obj)
    area, keys = _area(obj), sorted(obj.tools)
    _arm(tool)
    _click(tool, 30, 30)
    assert _area(obj) != area
    _settle(lambda: not fc.projects.is_busy())
    assert fc.history.undo(), fc.history.blocked_reason()
    _settle(lambda: not fc.projects.is_busy())
    assert _area(obj) == pytest.approx(area) and sorted(obj.tools) == keys



@pytest.mark.slow
def test_pcbdoc_object_gets_the_qr_in_its_own_units(fc, tool):
    obj = _gerber(fc)
    obj.units = 'IN'
    obj.obj_options['pcbdoc_import'] = True
    _setup(tool, obj, placement_radio='coords', x_entry=0.0, y_entry=0.0)
    before = obj.solid_geometry
    tool.execute()
    _settle(lambda: obj.solid_geometry is not before)
    added = unary_union([g for g in obj.solid_geometry if g not in before])
    assert added.bounds[2] - added.bounds[0] == pytest.approx(21 / MM, rel=0.02)

