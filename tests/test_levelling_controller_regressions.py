"""Controller-level Levelling Tool regressions: import apply, probing code, avoid step."""
import re
import threading
from unittest.mock import MagicMock, patch

import pytest
from shapely import Point, Polygon

from tests.test_levelling_audit import make_audit_tool
from appPlugins.ToolLevelling import ToolLevelling
import appPlugins.Common.levelling_core as lc_module


def import_tool():
    target = MagicMock()
    target.kind = 'cncjob'
    target.obj_options = {'name': 'job'}
    storage = {
        1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.0},
        2: {'point': Point(10.0, 0.0), 'geo': None, 'height': 0.0},
    }
    tool = make_audit_tool(storage=storage, cls=ToolLevelling)
    tool._al_target_name = 'job'
    tool._al_target_obj = target
    tool.app.collection.get_by_name.return_value = target
    tool.apply_autolevel = MagicMock()
    return tool


def ctx_result(tool, kind, **extra):
    result = {'kind': kind, 'context': tool._al_capture_context()}
    result.update(extra)
    return result


# ---------------------------------------------------------------- defect 1
def test_valid_height_map_import_applies_autolevel():
    tool = import_tool()
    tool._on_al_result_ready(ctx_result(tool, 'import', heights={1: 0.1, 2: 0.2}))
    assert tool.al_heights_valid
    tool.apply_autolevel.assert_called_once_with(target_obj=tool._al_target_obj)


def test_incomplete_height_map_import_does_not_apply():
    tool = import_tool()
    tool._on_al_result_ready(ctx_result(tool, 'import', heights={1: 0.1}))
    assert not tool.al_heights_valid
    tool.apply_autolevel.assert_not_called()




def test_stale_import_does_not_apply():
    tool = import_tool()
    result = ctx_result(tool, 'import', heights={1: 0.1, 2: 0.2})
    tool._al_bump_generation()
    tool._on_al_result_ready(result)
    tool.apply_autolevel.assert_not_called()


# ---------------------------------------------------------------- defect 2
GCODE_LINE = re.compile(r'^(\(.*\)|[GMO#%]\S*.*)$')


def gcode_tool(controller):
    tool = make_audit_tool(cls=ToolLevelling)
    target = MagicMock()
    target.obj_options = {'name': 'job'}
    target.coords_decimals = 4
    target.fr_decimals = 4
    tool.app.collection.get_by_name.return_value = target
    tool.app.dec_format.side_effect = lambda value, dec=4: round(value, 4)
    tool.app.version = '1'
    tool.app.version_date = 'x'
    tool.ui.al_method_radio.get_value.return_value = 'v'
    tool.ui.al_controller_combo.get_value.return_value = controller
    tool.ui.ptravelz_entry.get_value.return_value = 2.0
    tool.ui.feedrate_probe_entry.get_value.return_value = 50.0
    tool.ui.pdepth_entry.get_value.return_value = -1.0
    return tool


def storage():
    return {1: {'point': Point(1.0, 2.0), 'geo': None, 'height': 0.0}}


@pytest.mark.parametrize('controller', ['MACH3', 'MACH4', 'LinuxCNC'])
def test_probing_code_has_no_bare_prose(controller):
    tool = gcode_tool(controller)
    text = tool.probing_gcode(storage=storage())
    for line in text.splitlines():
        if not line.strip():
            continue
        assert GCODE_LINE.match(line.strip()), 'prose line in G-code: %r' % line
        if line.startswith('('):
            assert '(' not in line[1:-1] and ')' not in line[1:-1]


def test_probing_code_keeps_a_name_with_parentheses_inside_its_comment():
    # a ')' in the object name ended the '(Name: ...)' comment early; the rest of the line was read as G-code
    tool = gcode_tool('LinuxCNC')
    tool.app.collection.get_by_name.return_value.obj_options = {'name': 'board (1)-F_Cu.gbr_iso_cnc'}
    text = tool.probing_gcode(storage=storage())
    assert '(Name: board [1]-F_Cu.gbr_iso_cnc)\n' in text
    for line in text.splitlines():
        if line.startswith('('):
            assert '(' not in line[1:-1] and ')' not in line[1:-1]


# ---------------------------------------------------------------- defect 4
@pytest.mark.parametrize('new', ['LinuxCNC', 'MACH4'])
def test_controller_change_drops_old_controller_code(new):
    tool = gcode_tool('MACH3')
    tool.on_controller_change_alter_ui = MagicMock()
    tool.ui.object_combo.get_value.return_value = 'job'
    tool.probing_gcode_text = tool.probing_gcode(storage=storage())
    assert 'G31' in tool.probing_gcode_text
    tool.al_voronoi_geo_storage = storage()
    tool.ui.al_controller_combo.get_value.return_value = new
    tool.on_controller_change()
    text = tool.probing_gcode_text
    if new == 'LinuxCNC':
        assert 'G38.2' in text
        assert 'G31' not in text and 'M40' not in text and 'M41' not in text
    else:
        assert 'G31' in text


# ---------------------------------------------------------------- defect 3
def grid_tool(step):
    tool = make_audit_tool()
    values = {
        'avoid_exc_holes_cb': True, 'avoid_exc_holes_size_entry': step,
        'probe_tip_dia_entry': 0.2, 'al_columns_entry': 3, 'al_rows_entry': 3,
    }
    for name, value in values.items():
        getattr(tool.ui, name).get_value.return_value = value
    tool.ui.al_method_radio.get_value.return_value = 'v'
    tool.solid_geo = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    tool.app.dec_format.side_effect = lambda value, dec=4: round(value, 4)
    tool.generate_voronoi_geometry = MagicMock()
    tool.plot_probing_geo = MagicMock()
    tool.plot_probing_geo = MagicMock()
    tool.probing_gcode = MagicMock(return_value='')
    hole = Polygon([(4, 4), (6, 4), (6, 6), (4, 6)])
    tool.check_point_over_excellon = lambda pol, check: pol.intersection(hole) if pol.intersects(hole) \
        else Polygon()
    return tool


def run_guarded(tool):
    counter = {'n': 0}
    real_contains = Polygon.contains

    def guard_contains(self, other):
        counter['n'] += 1
        if counter['n'] > 200000:
            raise RuntimeError('avoidance loop did not terminate')
        return real_contains(self, other)

    errors = []

    def run():
        try:
            with patch.object(lc_module, 'box', wraps=lc_module.box) as _b:
                tool.on_add_grid_points()
        except BaseException as err:
            errors.append(err)

    # limit by wrapping check_point_over_excellon, which runs once per iteration
    inner = tool.check_point_over_excellon

    def counting(pol, check):
        counter['n'] += 1
        if counter['n'] > 5000:
            raise RuntimeError('avoidance loop did not terminate')
        return inner(pol, check)

    tool.check_point_over_excellon = counting
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), 'on_add_grid_points hung'
    if errors:
        raise errors[0]


def test_zero_avoid_step_is_rejected_without_points():
    tool = grid_tool(0.0)
    run_guarded(tool)
    assert tool.al_voronoi_geo_storage == {}
    messages = [c.args[0] for c in tool.app.inform.emit.call_args_list]
    assert any(m.startswith('[ERROR_NOTCL]') for m in messages)


def test_positive_avoid_step_still_adds_points():
    tool = grid_tool(0.5)
    run_guarded(tool)
    assert len(tool.al_voronoi_geo_storage) >= 8
