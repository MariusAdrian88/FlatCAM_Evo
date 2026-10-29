"""Etch compensation: offset maths and geometry/aperture compensation (Qt-free Gen module)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from appPlugins.ToolEtchCompensation import ToolEtchCompensation
from test_tcl_audit_generation import factory_app
from test_tcl_audit_regressions import FCShell
from appPlugins.ToolEtchCompensation.EtchCompensationGen import compensate_geometry, lateral_offset_um

FIXED_16 = {'gerber_circle_steps_mode': 'fixed', 'gerber_circle_steps': 16}


@pytest.mark.parametrize('kwargs', [
    dict(mode='factor', factor=0)])
def test_invalid_inputs_raise(kwargs):
    thickness = kwargs.pop('thickness_um', 18.0)
    with pytest.raises(ValueError):
        lateral_offset_um(thickness, **kwargs)


def _aperture_set():
    return {
        '10': {'type': 'C', 'size': 1.0, 'geometry': [
            {'solid': Point(0, 0).buffer(0.5), 'follow': Point(0, 0), 'clear': box(-0.1, -0.1, 0.1, 0.1)}]},
        '11': {'type': 'R', 'width': 2.0, 'height': 1.0, 'size': 2.2, 'geometry': [{'solid': box(0, 0, 2, 1)}]},
    }


def test_aperture_dimensions_grow_by_twice_the_offset():
    _, aps = compensate_geometry([], _aperture_set(), 0.1, FIXED_16)
    assert aps['10']['size'] == pytest.approx(1.2)
    assert aps['11']['width'] == pytest.approx(2.2)
    assert aps['11']['height'] == pytest.approx(1.2)
    assert aps['11']['size'] == pytest.approx((2.2 ** 2 + 1.2 ** 2) ** 0.5)


# ---------------------------------------------------------------------------------------------------------------
# the controller, called unbound on a namespace so that no window is built; the worker job runs synchronously
# ---------------------------------------------------------------------------------------------------------------

_METHODS = ('collect', 'read_values', 'offset_of', '_opt', '_set_busy', '_start', 'on_compensate', 'worker_job',
            'on_job_done', 'finish', 'store_values', 'draw_preview', 'clear_preview')


def _source(name, side=2.0):
    return SimpleNamespace(kind='gerber', obj_options={'name': name}, solid_geometry=[box(0, 0, side, side)],
                           tools={}, fill_color='#fff', outline_color='#000')


def _make_tool(sources, units='MM', multi=False, **ui_values):
    app = MagicMock()
    app.app_units = units
    app.defaults = None
    app.options = {'gerber_circle_steps_mode': 'fixed', 'gerber_circle_steps': 64}
    app.collection.get_names.return_value = []
    app.collection.get_selected.return_value = sources
    app.collection.get_by_name.side_effect = lambda n: next((s for s in sources if s.obj_options['name'] == n), None)
    values = dict(thick_entry=30.0, ratio_radio='factor', factor_entry=3.0, etchants_combo=None, offset_entry=0.0,
                  multi_cb=multi)
    values.update(ui_values)
    ui = MagicMock()
    for key, value in values.items():
        getattr(ui, key).get_value.return_value = value
    ui.etchants_combo.currentData.return_value = 'cucl2'
    ui.gerber_combo.currentText.return_value = sources[0].obj_options['name']
    tool = SimpleNamespace(app=app, ui=ui, decimals=4, pluginName='Etch', _busy=False, _preview_drawn=False,
                           job_done=MagicMock(), created=[])
    for name in _METHODS:
        setattr(tool, name, getattr(ToolEtchCompensation, name).__get__(tool))
    tool.job_done.emit.side_effect = tool.on_job_done
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])

    def new_object(kind, name, init, **kwargs):
        obj = SimpleNamespace(obj_options={}, regenerate_source=lambda **kw: None)
        init(obj, app)
        tool.created.append((name, obj))
        return obj

    tool.new_object = new_object
    return tool


def _messages(tool):
    return [c.args[0] for c in tool.app.inform.emit.call_args_list if c.args]


@pytest.mark.parametrize('units, thickness, side_effect', [('MM', 30.0, 0.01)])
def test_controller_grows_copper_by_thickness_over_factor(units, thickness, side_effect):
    # F = 1 for IN: 254 um = 0.01 in; F = 3 for MM: 30 um / 3 = 10 um = 0.01 mm
    src = _source('a')
    tool = _make_tool([src], units=units, thick_entry=thickness, factor_entry=3.0 if units == 'MM' else 1.0)
    tool.on_compensate()
    (name, obj), = tool.created
    assert name == 'a_comp' and obj.obj_options['name'] == 'a_comp'
    minx, miny, maxx, maxy = unary_union(obj.solid_geometry).bounds
    assert (minx, maxx) == pytest.approx((-side_effect, 2 + side_effect))
    assert all(isinstance(g, Polygon) for g in obj.solid_geometry)
    assert any(m.startswith('[success]') and 'a_comp' in m for m in _messages(tool))


def test_controller_multi_select_makes_one_output_per_gerber():
    a, b = _source('a'), _source('b', 4.0)
    other = SimpleNamespace(kind='geometry', obj_options={'name': 'g'})
    tool = _make_tool([a, b, other], multi=True)
    tool.on_compensate()
    assert sorted(name for name, _obj in tool.created) == ['a_comp', 'b_comp']
    # nothing selected: an error and no object
    tool = _make_tool([a], multi=True)
    tool.app.collection.get_selected.return_value = [other]
    tool.on_compensate()
    assert tool.created == [] and any(m.startswith('[ERROR_NOTCL]') for m in _messages(tool))


# ---------------------------------------------------------------------------------------------------------------
# the Tcl command etch_compensate
# ---------------------------------------------------------------------------------------------------------------

def etch_source():
    geo = box(0, 0, 10, 10)
    return SimpleNamespace(kind='gerber', obj_options={'name': 'cu', 'units': 'MM'}, solid_geometry=[geo],
                           tools={10: {'type': 'C', 'size': 1.0, 'geometry': [{'solid': geo, 'follow': geo.exterior}]}})


def run_etch(args):
    from tclCommands.TclCommandEtchCompensate import TclCommandEtchCompensate
    source = etch_source()
    app = factory_app(source)
    TclCommandEtchCompensate(app).execute({'name': 'cu', **args}, [])
    return app, source


@pytest.mark.parametrize('args', [{'thickness': 30, 'mode': 'factor', 'factor': 3}])
def test_etch_compensate_creates_the_output_with_the_grown_copper(args):
    app, source = run_etch(args)  # 10 um lateral in every mode
    kind, name, init = app.app_obj.new_object.call_args[0]
    assert (kind, name) == ('gerber', 'cu_comp') and app.app_obj.new_object.call_args[1]['plot'] is False
    new = Mock()
    new.obj_options = {}
    init(new, app)
    area = sum(p.area for p in new.solid_geometry)
    assert 100 + 4 * 10 * 0.01 < area < 100 + 4 * 10 * 0.01 + 0.01
    assert new.tools[10]['size'] == pytest.approx(1.02)
    assert source.solid_geometry[0].area == 100


@pytest.mark.parametrize('args', [{'mode': 'etchant', 'etchant': 'nope'}])
def test_etch_compensate_bad_input_is_a_tcl_error_and_creates_nothing(args):
    from tclCommands.TclCommandEtchCompensate import TclCommandEtchCompensate
    app = factory_app(etch_source())
    with pytest.raises(FCShell.TclErrorException):
        TclCommandEtchCompensate(app).execute({'name': 'cu', **args}, [])
    app.app_obj.new_object.assert_not_called()
