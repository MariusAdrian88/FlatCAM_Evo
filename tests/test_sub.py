"""Subtract plugin core (appPlugins/ToolSub/SubGen.py): Gerber, Geometry and Excellon with Shapely only."""
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from shapely import LineString, MultiPolygon, Point
from shapely.geometry import box
from shapely.ops import unary_union

# SubGen is Qt free; load it by path so the plugin package (which imports Qt) is not needed
_path = Path(__file__).resolve().parent.parent / 'appPlugins' / 'ToolSub' / 'SubGen.py'
_spec = importlib.util.spec_from_file_location('SubGen_under_test', _path)
sg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sg)


def flash(x, y, w=2.0, h=2.0):
    poly = box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
    return {'solid': poly, 'follow': Point(x, y)}


def gerber(tools, units='MM', name='grb'):
    solid = unary_union([el['solid'] for t in tools.values() for el in t['geometry'] if 'solid' in el])
    clears = [el['clear'] for t in tools.values() for el in t['geometry'] if 'clear' in el]
    if clears:
        solid = solid.difference(unary_union(clears))          # the app keeps solid_geometry polarity resolved
    return NS(kind='gerber', tools=tools, solid_geometry=solid, multigeo=False,
              obj_options={'units': units, 'name': name})


def geometry(shapes, units='MM', name='geo'):
    return NS(kind='geometry', tools={}, solid_geometry=shapes, multigeo=False,
              obj_options={'units': units, 'name': name})


def multigeo(shapes_by_uid, units='MM'):
    tools = {uid: {'solid_geometry': s} for uid, s in shapes_by_uid.items()}
    return NS(kind='geometry', tools=tools, solid_geometry=[], multigeo=True, obj_options={'units': units})


def excellon(tools, units='MM', name='exc'):
    return NS(kind='excellon', tools=tools, solid_geometry=[], multigeo=False,
              obj_options={'units': units, 'name': name})


def exc_tool(dia, drills=(), slots=()):
    drills = [Point(*d) for d in drills]
    slots = [(Point(*a), Point(*b)) for a, b in slots]
    shapes = [d.buffer(dia / 2) for d in drills]
    shapes += [LineString([(a.x, a.y), (b.x, b.y)]).buffer(dia / 2) for a, b in slots]
    return {'tooldia': dia, 'drills': drills, 'slots': slots, 'solid_geometry': shapes}


def area_of(res):
    return sum(g.area for g in sg._flat(res))


# ---------------------------------------------------------------------------------------------------- Gerber target
def test_pad_split_in_two_gives_two_regions():
    tools = {'10': {'type': 'R', 'size': 0.0, 'geometry': [flash(0, 0, 10, 2)]}}
    cutter = gerber({'11': {'type': 'R', 'size': 0.0, 'geometry': [flash(0, 0, 2, 10)]}})
    res = sg.compute(gerber(tools), [cutter])
    assert '10' not in res.tools
    regions = res.tools[0]['geometry']
    assert len(regions) == 2 and res.tools[0]['type'] == 'REG'
    assert all(r['follow'].geom_type == 'LinearRing' for r in regions)
    assert res.solid_geometry.area == pytest.approx(20 - 4)


def test_intersect_keeps_inside_and_clips_partial():
    inside, partial, outside = flash(0, 0, 2, 2), flash(10, 0, 4, 2), flash(50, 0)
    tools = {'10': {'type': 'R', 'size': 0.0, 'geometry': [inside, partial, outside]}}
    res = sg.compute(gerber(tools), [geometry([box(-5, -5, 11, 5)])], op='intersect')
    assert res.tools['10']['geometry'] == [inside]
    assert res.tools[0]['geometry'][0]['solid'].area == pytest.approx(3 * 2)
    assert res.stats == {'kept': 1, 'changed': 1, 'removed': 1, 'added': 0}


def test_units_mix_inch_subtractor_on_mm_target():
    target = gerber({'10': {'type': 'R', 'size': 0.0, 'geometry': [flash(25.4, 0, 2, 2)]}}, units='MM')
    cutter = geometry([box(0.9, -0.5, 1.1, 0.5)], units='IN')     # centered at x = 25.4 mm
    res = sg.compute(target, [cutter])
    assert res.solid_geometry.area == pytest.approx(0.0, abs=1e-9)  # the 5.08 mm wide cut removes the pad
    assert not res.tools


# --------------------------------------------------------------------------------------------------- Geometry target
@pytest.mark.parametrize('multi', [True])
def test_geometry_bare_multipolygon_gives_result(multi):
    mp = MultiPolygon([box(0, 0, 4, 4), box(10, 0, 14, 4)])
    target = multigeo({1: mp}) if multi else geometry(mp)
    res = sg.compute(target, [geometry([box(-1, -1, 2, 5)])], close_paths=True)
    assert res.multigeo is multi
    shapes = sg._flat(res.tools[1]['solid_geometry'] if multi else res.solid_geometry)
    assert sum(s.area for s in shapes) == pytest.approx(8 + 16)


def test_geometry_lines_with_margin_cut_gerber_and_excellon_subtractors():
    target = geometry([box(0, 0, 10, 10)])
    by_line = sg.compute(target, [geometry([LineString([(5, -5), (5, 15)])])], margin=1.0, close_paths=True)
    assert area_of(by_line.solid_geometry) == pytest.approx(80, rel=0.01)
    exc = excellon({1: exc_tool(2.0, drills=[(5, 5)])})
    by_exc = sg.compute(target, [exc], close_paths=True)
    assert area_of(by_exc.solid_geometry) < 100 - 3
    grb = gerber({'10': {'type': 'R', 'size': 0.0, 'geometry': [flash(5, 5, 4, 4)]}})
    by_grb = sg.compute(target, [grb], close_paths=True)
    assert area_of(by_grb.solid_geometry) == pytest.approx(84)


# -------------------------------------------------------------------------------------------------- Excellon target
def test_excellon_drops_covered_drills_and_slots_and_empty_tools():
    tools = {1: exc_tool(1.0, drills=[(0, 0), (20, 0)]),
             2: exc_tool(0.8, drills=[(0, 5)], slots=[((0, 8), (4, 8))]),
             3: exc_tool(0.6, drills=[(1, 1)])}
    res = sg.compute(excellon(tools), [geometry([box(-2, -2, 2, 6)])], close_paths=True)
    assert [(p.x, p.y) for p in res.tools[1]['drills']] == [(20, 0)]
    assert len(res.tools[1]['solid_geometry']) == 1
    assert 3 not in res.tools
    assert res.tools[2]['drills'] == [] and len(res.tools[2]['slots']) == 1   # the slot only touches the area
    assert len(res.tools[2]['solid_geometry']) == 1


# ------------------------------------------------------------------------------------------------------- apply/perf
# ------------------------------------------------------------------------------------------------- plugin controller
@pytest.fixture(scope='module')
def fc():
    import os
    import sys
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6 import QtWidgets
    import appHandlers.appHistory as appHistory
    from appMain import App
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app = App(qapp=qapp, headless=True)
    appHistory.RECORD_IN_HEADLESS = True
    yield app
    appHistory.RECORD_IN_HEADLESS = False


@pytest.fixture
def plugin(fc, monkeypatch):
    from appObjects.AppObjectTemplate import FlatCAMObj
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    for name in list(fc.collection.get_names()):
        fc.collection.delete_by_name(name)
    fc.history.clear()
    for shapes, name in ((box(0, 0, 10, 10), 'tgt'), (box(4, 4, 6, 6), 'cut')):
        fc.app_obj.new_object('geometry', name, lambda o, a, s=shapes: setattr(o, 'solid_geometry', [s]), plot=False)
    return fc.sub_tool


def _pump(fc, plugin, timeout=10.0):
    from PyQt6 import QtWidgets
    end = time.time() + timeout
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if not plugin._busy and not fc.projects.is_busy():
            break
        time.sleep(0.02)
    for _ in range(5):
        QtWidgets.QApplication.processEvents()


def _click(fc, plugin, keep=True, delete_subs=False, sub='cut'):
    """Open the plugin the way the user does, pick the objects and press Apply."""
    from PyQt6 import QtCore
    plugin.run(toggle=False)
    ui = plugin.ui
    ui.type_combo.setCurrentIndex(ui.type_combo.findData('geometry'))
    ui.target_combo.setCurrentText('tgt')
    for i in range(ui.subs_list.count()):
        item = ui.subs_list.item(i)
        item.setCheckState(QtCore.Qt.CheckState.Checked if item.text() == sub else QtCore.Qt.CheckState.Unchecked)
    ui.keep_target_cb.setChecked(keep)
    ui.delete_subs_cb.setChecked(delete_subs)
    ui.apply_button.click()
    _pump(fc, plugin)


@pytest.mark.slow
def test_one_click_after_reopening_gives_one_object(fc, plugin):
    plugin.run(toggle=False)
    plugin.set_tool_ui()
    _click(fc, plugin)
    assert sorted(fc.collection.get_names()) == ['cut', 'tgt', 'tgt_sub']
    assert fc.collection.get_by_name('tgt_sub').solid_geometry
    assert fc.collection.get_by_name('tgt_sub').solid_geometry != fc.collection.get_by_name('tgt').solid_geometry


@pytest.mark.slow
def test_undo_brings_back_target_and_subtractors_in_one_step(fc, plugin):
    steps = len(fc.history.undo_stack)
    _click(fc, plugin, keep=False, delete_subs=True)
    assert fc.collection.get_names() == ['tgt']
    assert len(fc.history.undo_stack) == steps + 1
    fc.history.undo()
    _pump(fc, plugin)
    assert sorted(fc.collection.get_names()) == ['cut', 'tgt']
    assert fc.collection.get_by_name('tgt').solid_geometry[0].area == 100.0
    fc.history.redo()
    _pump(fc, plugin)
    assert fc.collection.get_names() == ['tgt']
    assert sum(g.area for g in fc.collection.get_by_name('tgt').solid_geometry) < 100.0

