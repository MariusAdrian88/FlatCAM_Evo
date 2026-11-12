"""Panelize plugin (appPlugins/ToolPanelize.py), driven headless: Panelize.on_panelize runs on a stub instance (no
GUI, the ui is a namespace of tiny value holders), the worker task is executed at once and ``new_object`` runs the
init callback on a real, empty object. The plugin and the Tcl command must give the same panel."""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import ezdxf
import numpy as np
import pytest
from PyQt6 import QtCore
from shapely.geometry import LineString, box

from appHandlers.appEdit import appEditor
from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appPlugins.ToolPanelize import Panelize

from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_coords_close, _headless_qt, make_excellon, make_gerber, parts, polygon_union, reparse_gerber,
)
from test_panelize_core import run_panelize


class Value:
    """Minimal stand-in of an FC widget."""

    def __init__(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value

    def currentText(self):
        return self.value

    def isChecked(self):
        return bool(self.value)


def run_plugin(app, src, box_obj=None, rows=2, cols=3, spacing=5.0, panel_type='gerber', optimize=False,
               constrain=None, before=None, name=None, box_name=None, fail_job=False, reference=None,
               step_repeat=False):
    """Returns (created, informs). constrain = (max_x, max_y) switches the constrain option on. before: a list that
    receives the messages shown before the worker task runs (the GUI-thread part)."""
    app.f_handlers = appIO(app)
    app.edit_class = appEditor(app)
    created = {}
    informs = []

    def new_object(kind, name, initialize, plot=True, **kwargs):
        if fail_job:
            raise RuntimeError('boom')
        cls = {'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject}[kind]
        obj = cls(name, app)
        initialize(obj, app)
        created[name] = obj
        return 'ok'

    objects = {src.obj_options['name']: src}
    if box_obj is not None:
        objects[box_obj.obj_options['name']] = box_obj
    app.collection.get_by_name = Mock(side_effect=lambda n: objects.get(n))
    app.app_obj.new_object = Mock(side_effect=new_object)
    app.delete_selection_shape = Mock()
    app.inform = SimpleNamespace(emit=lambda text, *a, **k: informs.append(text))
    tasks = []
    app.worker_task = SimpleNamespace(emit=lambda task: tasks.append(task))

    plugin = QtCore.QObject.__new__(Panelize)
    plugin.app = app
    plugin.constrain_flag = False
    plugin.outname = ''
    bx_name = (box_obj or src).obj_options['name']
    plugin.ui = SimpleNamespace(
        object_combo=Value(name or src.obj_options['name']), box_combo=Value(box_name or bx_name),
        reference_radio=Value(reference or ('bbox' if box_obj is None else 'object')),
        spacing_columns=Value(spacing), spacing_rows=Value(spacing), rows=Value(rows), columns=Value(cols),
        x_width_entry=Value(constrain[0] if constrain else 0.0), y_height_entry=Value(constrain[1] if constrain else 0.0),
        constrain_cb=Value(bool(constrain)), optimization_cb=Value(optimize), panel_type_radio=Value(panel_type),
        step_repeat_cb=Value(step_repeat))
    plugin.on_panelize()
    if before is not None:
        before.extend(informs)
    if not tasks:
        return created, informs
    assert len(tasks) == 1
    tasks[0]['fcn'](*tasks[0]['params'])
    return created, informs


def same_tools(pan, other, key):
    assert list(pan.tools) == list(other.tools)
    for apid in pan.tools:
        a, b = pan.tools[apid][key], other.tools[apid][key]
        assert len(a) == len(b)
        for rec_a, rec_b in zip(a, b):
            if isinstance(rec_a, dict):
                assert set(rec_a) == set(rec_b)
                for k in rec_a:
                    assert_coords_close(rec_a[k], rec_b[k], msg='%s %s' % (apid, k))
            else:
                assert_coords_close(rec_a, rec_b)


def test_gerber_plugin_equals_tcl(app):
    src = make_gerber(app, name='g')
    _, tcl = run_panelize(app, src, 'g', '-rows', '2', '-columns', '3', '-spacing_columns', '5',
                          '-spacing_rows', '5', '-outname', 'p')
    created, informs = run_plugin(app, src, panel_type='gerber')
    pan = created['g_panelized']
    same_tools(pan, tcl['p'], 'geometry')
    assert_coords_close(pan.solid_geometry, tcl['p'].solid_geometry)
    assert pan.export_as_regions == tcl['p'].export_as_regions
    assert set(pan.aperture_macros) == set(src.aperture_macros)
    assert pan.source_file.startswith('G04*')


def test_excellon_plugin_equals_tcl(app):
    src = make_excellon(app, name='e')
    _, tcl = run_panelize(app, src, 'e', '-rows', '2', '-columns', '3', '-spacing_columns', '5',
                          '-spacing_rows', '5', '-outname', 'q')
    created, _ = run_plugin(app, src, panel_type='gerber')
    pan, ref = created['e_panelized'], tcl['q']
    assert list(pan.tools) == list(ref.tools)
    for tid in ref.tools:
        assert_coords_close(pan.tools[tid]['drills'], ref.tools[tid]['drills'])
        assert [(a.x, a.y, b.x, b.y) for a, b in pan.tools[tid]['slots']] == \
            pytest.approx(np.array([(a.x, a.y, b.x, b.y) for a, b in ref.tools[tid]['slots']]))
    assert pan.bounds() == pytest.approx(ref.bounds())
    assert pan.source_file.startswith('M48')


def test_constrain_reduces_the_grid(app):
    src = make_gerber(app, name='g')
    xmin, ymin, xmax, ymax = src.bounds()
    w, h = xmax - xmin, ymax - ymin
    # room for 2 columns and 1 row only (spacing 5)
    created, informs = run_plugin(app, src, rows=3, cols=4, constrain=(2 * w + 5.5, h + 1.0))
    pan = created['g_panelized']
    assert len(parts(pan.solid_geometry)) == 2 * len(parts(src.solid_geometry))


def make_geometry(app, multigeo):
    src = GeometryObject('src', app)
    src.multigeo = multigeo
    src.units = 'MM'
    outline = LineString([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    src.solid_geometry = [outline, [box(2, 2, 3, 3)]]
    src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': [outline]}}
    return src


def test_geometry_plugin_multigeo_merges_the_shared_borders(app):
    src = make_geometry(app, True)
    created, informs = run_plugin(app, src, panel_type='geometry', rows=1, cols=2, spacing=0.0, optimize=True)
    pan = created['src_panelized']
    assert pan.multigeo is True
    fused = pan.tools[1]['solid_geometry']
    assert len(fused) == 1 and fused[0].length == pytest.approx(70.0)
    # the composed solid_geometry is flat
    assert all(not isinstance(g, (list, tuple)) for g in pan.solid_geometry) and len(pan.solid_geometry) == 4


@pytest.mark.parametrize('multigeo', [False, True])
def test_geometry_source_to_gerber_panel_solid(app, multigeo):
    src = make_geometry(app, multigeo)
    created, _ = run_plugin(app, src, panel_type='gerber', rows=1, cols=2, spacing=0.0)
    pan = created['src_panelized']
    assert pan.multigeo is True and pan.source_file.startswith('G04*')
    if multigeo:
        # rebuilt from the tool lines: the 2 outlines fused into 1 polygon
        assert polygon_union(pan.solid_geometry).area == pytest.approx(200.0)
    else:
        assert len(parts(pan.solid_geometry)) == 4


def test_source_excellon_without_slots_or_drills_keys_is_not_changed(app):
    src = make_excellon(app, name='e')
    for tool in src.tools.values():
        del tool['slots']
    del src.tools[2]['drills']
    keys_before = {tid: sorted(tool) for tid, tool in src.tools.items()}
    created, _ = run_plugin(app, src)
    assert {tid: sorted(tool) for tid, tool in src.tools.items()} == keys_before
    # the panel still has both keys everywhere
    assert all('drills' in t and 'slots' in t for t in created['e_panelized'].tools.values())


def test_gerber_to_geometry_panel_is_the_translated_copper_with_tools_and_a_dxf_source(app):
    # The panel gets the tool table of the Geometry (like convert_any2geo); every tool holds the translated copper.
    src = make_gerber(app, name='g')
    created, _ = run_plugin(app, src, panel_type='geometry', rows=2, cols=2)
    pan = created['g_panelized']
    src_area = polygon_union(src.solid_geometry).area
    assert pan.multigeo is True
    assert pan.tools
    for tool in pan.tools.values():
        assert polygon_union(tool['solid_geometry']).area == pytest.approx(4 * src_area)
        assert 'tooldia' in tool and isinstance(tool['data'], dict) and tool['data']
    assert polygon_union(pan.solid_geometry).area == pytest.approx(4 * src_area)
    assert all(not isinstance(g, (list, tuple)) for g in pan.solid_geometry)
    assert all(not isinstance(g, (list, tuple)) for tool in pan.tools.values() for g in tool['solid_geometry'])
    assert pan.obj_options['tools_mill_tooldia'] == pan.tools[min(pan.tools)]['tooldia']
    # a Geometry keeps its source as DXF text
    assert pan.source_file and len(ezdxf.read(StringIO(pan.source_file)).modelspace()) > 0


def make_poly_geometry(app, multigeo):
    """A Geometry made of polygons only: 3 elements."""
    src = GeometryObject('src', app)
    src.multigeo = multigeo
    src.units = 'MM'
    polys = [box(0, 0, 4, 4), box(6, 0, 8, 2), box(10, 0, 12, 2)]
    src.solid_geometry = list(polys)
    if multigeo:
        src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': polys[:2]},
                     2: {'tooldia': 2.0, 'data': {}, 'solid_geometry': polys[2:]}}
    else:
        src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': []}}
    return src


@pytest.mark.parametrize('multigeo', [False, True], ids=['single-geo', 'multigeo'])
def test_geometry_to_gerber_panel_keeps_the_records_and_has_a_source(app, multigeo):
    src = make_poly_geometry(app, multigeo)
    created, _ = run_plugin(app, src, panel_type='gerber', rows=2, cols=2)
    pan = created['src_panelized']
    assert pan.tools[0]['type'] == 'REG'
    records = pan.tools[0]['geometry']
    assert len(records) == 4 * 3
    assert polygon_union([r['solid'] for r in records]).symmetric_difference(
        polygon_union(pan.solid_geometry)).area < 1e-6
    assert pan.multigeo is True and pan.multitool is False and pan.follow is False
    assert pan.export_as_regions is False
    assert isinstance(pan.source_file, str) and pan.source_file.strip()


def test_geometry_to_gerber_panel_with_a_line_exports_as_regions(app, tmp_path):
    src = make_poly_geometry(app, False)
    src.solid_geometry = [box(0, 0, 4, 4), LineString([(6, 0), (9, 3)])]
    created, _ = run_plugin(app, src, panel_type='gerber', rows=1, cols=2)
    pan = created['src_panelized']
    assert pan.export_as_regions is True
    assert isinstance(pan.source_file, str) and pan.source_file.strip()
    again = reparse_gerber(app, pan.source_file, tmp_path)
    assert polygon_union(again.solid_geometry).area == pytest.approx(2 * 16)


def test_constraint_smaller_than_one_board_does_not_schedule_a_panel(app):
    src = make_gerber(app, name='g')
    created, informs = run_plugin(app, src, constrain=(0.001, 0.001))
    assert not created
    app.collection.promise.assert_not_called()


@pytest.mark.parametrize('spacing', [5.0, -1.0])       # copies apart (polygons of the source, moved) / overlapping
def test_gerber_panel_exported_as_regions_has_the_copper_of_the_panel(app, tmp_path, spacing):
    from appParsers.ParseGerber import Gerber
    src = make_gerber(app, name='g')
    created, informs = run_plugin(app, src, spacing=spacing)
    pan = created['g_panelized']
    assert Gerber.needs_region_export(pan)
    again = reparse_gerber(app, pan.source_file, tmp_path)
    want = polygon_union(pan.solid_geometry)
    assert polygon_union(again.solid_geometry).symmetric_difference(want).area < 1e-6 * want.area
    assert 'region_polygons_once' not in vars(pan)      # a later export of a changed panel makes its own union


CLEAR_GBR = 'tests/fixtures/orcad_17.2/Copper_Bottom.art'         # dark and clear objects, small


def flash_count(obj):
    return sum(1 for tool in obj.tools.values() if tool.get('type') != 'REG' for record in tool.get('geometry', ())
               if record.get('follow') is not None and record['follow'].geom_type == 'Point'
               and not getattr(record.get('solid'), 'is_empty', True))


def same_copper(app, pan, tmp_path):
    again = reparse_gerber(app, pan.source_file, tmp_path)
    want = polygon_union(pan.solid_geometry)
    assert polygon_union(again.solid_geometry).symmetric_difference(want).area <= 2e-3 * want.area
    return again


@pytest.mark.parametrize('spacing', [5.0, -1.0])
def test_panel_of_a_gerber_with_clear_objects_keeps_its_apertures(app, tmp_path, spacing):
    # copies apart: the records are written in the order of the file, copy after copy, with their apertures.
    # overlapping copies: a clear object of a copy erases another copy; the export adds that copper back as regions
    # at the end. In both cases each flash stays a flash.
    src = make_gerber(app, CLEAR_GBR, name='g')
    created, informs = run_plugin(app, src, spacing=spacing)
    pan = created['g_panelized']
    assert '%ADD' in pan.source_file and '%LPC*%' in pan.source_file
    again = same_copper(app, pan, tmp_path)
    assert flash_count(again) == flash_count(pan) == 6 * flash_count(src)


@pytest.mark.parametrize('spacing, used', [(5.0, True), (-1.0, False)])
def test_step_and_repeat_option_writes_one_copy_in_a_block(app, tmp_path, spacing, used):
    for path in (CLEAR_GBR, None):                    # native records with clear objects / block apertures (AM)
        src = make_gerber(app, path, name='g') if path else make_gerber(app, name='g')
        plain = run_plugin(app, src, spacing=spacing)[0]['g_panelized']
        pan = run_plugin(app, src, spacing=spacing, step_repeat=True)[0]['g_panelized']
        assert ('%SRX3Y2I' in pan.source_file and pan.source_file.count('%SR*%') == 1) is used
        if path:
            assert (len(pan.source_file) < len(plain.source_file) / 3) is used
        # the block apertures are defined before the Step and Repeat block, not in it
        assert pan.source_file.find('%ABD') < pan.source_file.find('%SRX') or '%SRX' not in pan.source_file or \
            '%ABD' not in pan.source_file
        same_copper(app, pan, tmp_path)
        assert_coords_close(pan.solid_geometry, plain.solid_geometry)       # the panel itself is the same


@pytest.mark.parametrize('answer, made', [('Yes', True), ('No', False)])
def test_panel_larger_than_the_free_memory_asks_the_user(app, monkeypatch, answer, made):
    import sys
    module = sys.modules[Panelize.__module__]
    shown = []

    class Box:
        def __init__(self, parent=None):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: None

        def setInformativeText(self, text):
            shown.append(text)

        def addButton(self, text, role):
            return text

        def clickedButton(self):
            return answer

    monkeypatch.setattr(module, 'FCMessageBox', Box)
    monkeypatch.setattr(module, 'free_memory', lambda: 1)
    created, informs = run_plugin(app, make_gerber(app, name='g'))
    assert len(shown) == 1 and '6' in shown[0]          # the question names the 2 x 3 copies
    assert bool(created) is made

    # the free memory is not known: no question, the panel is made
    monkeypatch.setattr(module, 'free_memory', lambda: None)
    created, informs = run_plugin(app, make_gerber(app, name='g'))
    assert len(shown) == 1 and created


def test_free_memory_of_linux_and_macos_text():
    from appCommon.memory import free_memory, parse_meminfo, parse_vm_stat
    meminfo = "MemTotal:       16303428 kB\nMemFree:          812340 kB\nMemAvailable:    9876543 kB\nBuffers: 1 kB\n"
    assert parse_meminfo(meminfo) == 9876543 * 1024
    assert parse_meminfo("MemTotal: 100 kB\nMemFree: 40 kB\n") == 40 * 1024      # kernel without MemAvailable
    assert parse_meminfo("nothing") is None
    vm_stat = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
               "Pages free:                               10000.\n"
               "Pages active:                            500000.\n"
               "Pages inactive:                           20000.\n"
               "Pages speculative:                         3000.\n"
               "Pages purgeable:                           4000.\n")
    assert parse_vm_stat(vm_stat) == 37000 * 16384
    assert parse_vm_stat("nothing") is None
    assert free_memory() is None or free_memory() > 0      # the branch of this operating system


def test_worker_exception_clears_promise_and_reports_error(app):
    src = make_gerber(app, name='g')
    app.collection.promises = set()
    app.collection.promise.side_effect = app.collection.promises.add
    created, informs = run_plugin(app, src, fail_job=True)
    assert not created
    assert not app.collection.promises
