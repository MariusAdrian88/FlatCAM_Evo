"""Panelize plugin (appPlugins/ToolPanelize.py), driven headless: Panelize.on_panelize runs on a stub instance (no
GUI, the ui is a namespace of tiny value holders), the worker task is executed at once and ``new_object`` runs the
init callback on a real, empty object. The plugin and the Tcl command must give the same panel."""

from types import SimpleNamespace
from unittest.mock import Mock

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
    app, assert_coords_close, _headless_qt, make_excellon, make_gerber, NATIVE_GBR, parts, polygon_union,
)
from test_panelize_tcl_characterization import run_panelize


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
               constrain=None, before=None, name=None, box_name=None, fail_job=False, reference=None):
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
        constrain_cb=Value(bool(constrain)), optimization_cb=Value(optimize), panel_type_radio=Value(panel_type))
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


@pytest.mark.parametrize('path', [None, NATIVE_GBR], ids=['mixed', 'native'])
def test_gerber_plugin_equals_tcl(app, path):
    src = make_gerber(app, name='g') if path is None else make_gerber(app, path, name='g')
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
    assert any('Too big for the constrain area' in text and '2 columns and 1 rows' in text for text in informs)


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
    assert any('Optimizing the overlapping paths' in t for t in informs)
    assert any('Optimization complete' in t for t in informs)
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
