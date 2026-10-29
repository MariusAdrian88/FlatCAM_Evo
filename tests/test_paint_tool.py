"""Paint plugin controller: tool table, parameters copy, selection state."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from shapely import box

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6 import QtWidgets

from appPlugins.ToolPaint.Paint import ToolPaint


def _item(text='', row=0):
    item = MagicMock()
    item.text.return_value = str(text)
    item.row.return_value = row
    return item


def _stub(tools, **extra):
    """A ToolPaint stand-in: `tools` is {uid: diameter}; the table rows follow the dict order."""
    paint_tools = {uid: {'tooldia': dia, 'data': {'tools_mill_tool_shape': 0}, 'solid_geometry': []}
                   for uid, dia in tools.items()}
    uids = list(tools)
    table = MagicMock()
    table.rowCount.return_value = len(uids)
    cells = {}
    for row, uid in enumerate(uids):
        cells[(row, 1)] = _item(tools[uid], row)
        cells[(row, 3)] = _item(uid, row)
    table.item.side_effect = lambda r, c: cells[(r, c)]
    app = MagicMock()
    app.dec_format.side_effect = lambda v, d: float('%.*f' % (d, v))
    stub = SimpleNamespace(
        app=app, decimals=4, paint_tools=paint_tools, blockSignals=MagicMock(), build_ui=MagicMock(),
        clear_preview=MagicMock(), ui=SimpleNamespace(tools_table=table), cells=cells)
    for key, value in extra.items():
        setattr(stub, key, value)
    return stub


def _edit(stub, row, text):
    stub.cells[(row, 1)].text.return_value = text
    ToolPaint.on_tool_edit(stub, stub.cells[(row, 1)])


def test_tool_edit_rejects_a_duplicate_of_any_other_tool():
    stub = _stub({1: 2.0, 2: 1.0, 3: 0.5})
    _edit(stub, 0, '0.5')   # duplicates the last tool, not the first one checked
    assert stub.paint_tools[1]['tooldia'] == 2.0
    _edit(stub, 0, '1.0')   # duplicates a middle tool
    assert stub.paint_tools[1]['tooldia'] == 2.0
    _edit(stub, 0, '1.5')
    assert stub.paint_tools[1]['tooldia'] == 1.5
    stub.build_ui.assert_called()


def test_tool_edit_rejects_non_positive_and_non_finite_diameters():
    stub = _stub({1: 2.0, 2: 1.0})
    for text in ('0', '-1', 'nan', 'inf', 'abc'):
        _edit(stub, 0, text)
        assert stub.paint_tools[1]['tooldia'] == 2.0, text
    assert stub.blockSignals.call_args_list[-1].args == (False,)


def test_apply_to_all_copies_only_the_form_parameters_of_the_selected_row():
    stub = _stub({1: 2.0, 2: 1.0})
    stub.paint_tools[1]['data'].update(tools_paint_overlap=0.1, extra_key='one')
    stub.paint_tools[2]['data'].update(tools_paint_overlap=0.7, extra_key='two', tools_mill_tool_shape=3)
    stub.form_fields = {'tools_paint_overlap': MagicMock()}
    stub.ui.tools_table.selectedItems.return_value = [_item(row=1)]
    stub.ui.tools_table.currentRow.return_value = 0     # differs from the row the form shows
    ToolPaint.on_apply_param_to_all_clicked(stub)
    assert stub.paint_tools[1]['data']['tools_paint_overlap'] == 0.7
    assert stub.paint_tools[1]['data']['extra_key'] == 'one'
    assert stub.paint_tools[1]['data']['tools_mill_tool_shape'] == 0
    stub.build_ui.assert_called()


# ----------------------------------------------------------------------------------------------------------------------
# Preview: the plugin is built headless (offscreen Qt, MagicMock app) like the NCC panel tests
# ----------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def painter(qapp, tmp_path):
    import functools
    from copy import deepcopy
    from PyQt6.QtCore import QStringListModel
    from appMain import App
    from defaults import AppDefaults
    from test_plugin_geometry_bases import make_tool_app

    class Collection(QStringListModel):
        def __init__(self):
            super().__init__()
            self.objects, self.selected = {}, []

        def get_selected(self):
            return self.selected

        def get_active(self):
            return None

        def get_list(self):
            return list(self.objects.values())

        def get_names(self):
            return list(self.objects)

        def get_by_name(self, name):
            return self.objects.get(name)

    class Defaults(dict):
        report_usage = staticmethod(lambda *args: None)

    app = make_tool_app()
    app.collection = Collection()
    app.options = deepcopy(AppDefaults.factory_defaults)
    app.defaults = Defaults(AppDefaults.factory_defaults)
    app.dec_format = functools.partial(App.dec_format, app)
    app.tools_database_path.return_value = str(tmp_path / "missing_tools_db.json")
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    app.abort_flag = False
    app.plotcanvas.new_shape_collection.side_effect = lambda **kwargs: MagicMock()
    app.options["tools_paint_tooldia"] = "1.0"
    t = ToolPaint(app)
    t.decimals = 4
    t.set_tool_ui()
    t.build_ui()
    t.ui.tools_table.selectAll()
    board = SimpleNamespace(kind='geometry', solid_geometry=[box(0, 0, 4, 4), box(10, 0, 14, 4)],
                            obj_options={'name': 'board'}, multigeo=False, tools={})
    ref = SimpleNamespace(kind='geometry', solid_geometry=[box(-1, -1, 5, 5)], obj_options={'name': 'ref'})
    app.collection.objects.update(board=board, ref=ref)
    t.ui.obj_combo.currentText = lambda: 'board'
    t.ui.reference_combo.currentText = lambda: 'ref'
    return t


def _drawn_area(t):
    t.on_preview()
    store = t._preview_shapes
    if store is None:
        return 0.0
    area = sum(call.args[0].area for call in store.add.call_args_list)
    store.add.reset_mock()
    return area


def test_preview_draws_the_area_to_paint_after_the_offset_and_is_cleared(painter):
    painter.ui.select_method_combo.set_value(0)     # All
    assert _drawn_area(painter) == pytest.approx(32.0)
    for tool in painter.paint_tools.values():
        tool['data']['tools_paint_offset'] = 0.5
    assert _drawn_area(painter) == pytest.approx(18.0)

    # Reference Object: only the board polygons inside the reference
    painter.ui.select_method_combo.set_value(3)
    assert _drawn_area(painter) == pytest.approx(9.0)

    # Area Selection previews the zones of the last run: none yet
    painter.ui.select_method_combo.set_value(2)
    painter.app.worker_task.emit.reset_mock()
    painter.on_preview()
    assert painter.app.inform.emit.call_args.args[0].startswith('[WARNING_NOTCL]')
    painter.app.worker_task.emit.assert_not_called()
    # the zones of the last Area Selection run: only the part of the board inside them
    painter.last_zones = [box(-1, -1, 2, 5)]
    assert _drawn_area(painter) == pytest.approx(3.0)

    # a result that arrives after the object changed is not drawn
    painter.ui.select_method_combo.set_value(0)
    tasks = []
    painter.app.worker_task.emit.side_effect = tasks.append
    painter.on_preview()
    painter.on_source_changed()
    tasks[0]['fcn'](*tasks[0]['params'])
    painter._preview_shapes.add.assert_not_called()

    # a Reset clears it
    painter._preview_shapes.clear.reset_mock()
    painter._preview_drawn = True
    painter.set_tool_ui()
    painter._preview_shapes.clear.assert_called()


def test_selected_objects_do_not_need_the_source_combo(painter):
    # two Gerbers selected in the Project tab, no Geometry object: the source combo is empty
    first = SimpleNamespace(kind='gerber', solid_geometry=[box(0, 0, 2, 2)],
                            obj_options={'name': 'g1'}, tools={})
    second = SimpleNamespace(kind='gerber', solid_geometry=[box(3, 0, 5, 2)],
                             obj_options={'name': 'g2'}, tools={})
    painter.app.collection.objects.update(g1=first, g2=second)
    painter.app.collection.selected = [first, second]
    painter.app.collection.promise = MagicMock()
    painter.ui.obj_combo.currentText = lambda: ''
    painter.ui.multi_objects_cb.set_value(True)
    painter.ui.select_method_combo.set_value(0)     # All

    tasks = []
    painter.app.worker_task.emit.side_effect = tasks.append
    painter.gen.on_paint_button_click()
    assert len(tasks) == 2      # one paint job per selected object

    painter.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    assert _drawn_area(painter) == pytest.approx(8.0)
    assert not any('Object not found' in str(call.args[0]) for call in painter.app.inform.emit.call_args_list)

