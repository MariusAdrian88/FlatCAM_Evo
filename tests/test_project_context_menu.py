"""
The context menu of the Project tab shows the entries that apply to the selected objects, and View Source does not
open an empty Source Editor for an object without a source (a Geometry made by a tool).

    python tests/test_project_context_menu.py
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtWidgets                        # noqa: E402

from appObjects.ObjectCollection import ObjectCollection   # noqa: E402

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make_ui():
    """The entries of the Project tab context menu, as MainGUI builds them."""
    menu = QtWidgets.QMenu()
    ui = SimpleNamespace(menuproject=menu)
    ui.menuprojectenable = menu.addAction('Enable Plot')
    ui.menuprojectdisable = menu.addAction('Disable Plot')
    ui.menuprojectcolor = menu.addMenu('Set Color')
    ui.menuprojectcolor.addAction('Red')
    ui.menuprojectviewsource = menu.addAction('View Source')
    ui.menuprojectedit = menu.addAction('Edit')
    ui.menuprojectcopy = menu.addAction('Copy')
    ui.menuprojectdelete = menu.addAction('Delete')
    ui.menuprojectsave = menu.addAction('Save')
    ui.menuprojectproperties = menu.addAction('Properties')
    ui.menuprojectexport = menu.addMenu('Export')
    ui.menuprojectexport_dxf = ui.menuprojectexport.addAction('DXF')
    ui.menuprojectexport_svg = ui.menuprojectexport.addAction('SVG')
    ui.menuprojectexport_png = ui.menuprojectexport.addAction('PNG')
    ui.menuprojectexport_pdf = ui.menuprojectexport.addAction('PDF')
    ui.menuprojectjoin = menu.addMenu('Join')
    return ui


def make_collection():
    app = mock.MagicMock()
    app.resource_location = "."
    app.options = {
        "global_allow_edit_in_project_tab": False,
        "global_proj_item_color_dark": "#FFFFFFFF",
        "global_proj_item_dis_color_dark": "#808080FF",
        "global_proj_item_color_light": "#000000FF",
        "global_proj_item_dis_color_light": "#808080FF",
    }
    col = ObjectCollection(app)
    app.ui = make_ui()
    return col


def obj(kind, name='obj', plot=True, **attrs):
    return SimpleNamespace(kind=kind, obj_options={'name': name, 'plot': plot}, **attrs)


class TestHasSource(unittest.TestCase):

    def test_kinds(self):
        self.assertTrue(ObjectCollection.has_source(obj('cncjob')))
        self.assertTrue(ObjectCollection.has_source(obj('gerber', source_file='G04 test*\nM02*\n')))
        self.assertTrue(ObjectCollection.has_source(obj('excellon', source_file='M48\nM30\n')))
        self.assertTrue(ObjectCollection.has_source(obj('geometry', source_file='0\nSECTION\n')))

class TestMenuRequest(unittest.TestCase):

    def setUp(self):
        self.col = make_collection()
        self.ui = self.col.app.ui
        self.ui.menuproject.popup = mock.Mock()
        self.col.view.indexAt = lambda pos: QtCore.QModelIndex()

    def tearDown(self):
        self.ui.menuproject.deleteLater()

    def request(self, *selected):
        self.col.get_selected = lambda: list(selected)
        self.col.on_menu_request(QtCore.QPoint(0, 0))
        self.ui.menuproject.popup.assert_called()

    def shown(self):
        ui = self.ui
        names = {
            'enable': ui.menuprojectenable, 'disable': ui.menuprojectdisable,
            'color': ui.menuprojectcolor.menuAction(), 'view_source': ui.menuprojectviewsource,
            'edit': ui.menuprojectedit, 'copy': ui.menuprojectcopy, 'delete': ui.menuprojectdelete,
            'save': ui.menuprojectsave, 'properties': ui.menuprojectproperties
        }
        return {name for name, action in names.items() if action.isVisible()}

    def test_geometry_made_by_a_tool_has_no_view_source(self):
        self.request(obj('geometry'))
        self.assertEqual(self.shown(), {'disable', 'edit', 'copy', 'delete', 'save', 'properties'})

    def test_gerber_with_a_source(self):
        self.request(obj('gerber', source_file='G04 test*\nM02*\n'))
        self.assertEqual(self.shown(), {'disable', 'color', 'view_source', 'edit', 'copy', 'delete', 'save',
                                        'properties'})

    def test_enable_and_disable_follow_the_plot_state(self):
        self.request(obj('gerber', 'a', plot=True), obj('gerber', 'b', plot=False))
        self.assertTrue({'enable', 'disable'} <= self.shown())
        self.request(obj('gerber', 'a', plot=False), obj('gerber', 'b', plot=False))
        self.assertIn('enable', self.shown())
        self.assertNotIn('disable', self.shown())

    def test_export_submenu_hidden_for_a_cncjob_and_for_several_objects(self):
        self.request(obj('cncjob'))
        self.assertFalse(self.ui.menuprojectexport.menuAction().isVisible())
        self.request(obj('gerber', 'a'), obj('gerber', 'b'))
        self.assertFalse(self.ui.menuprojectexport.menuAction().isVisible())

    def test_join_submenu_only_for_several_gerber_excellon_geometry_objects(self):
        join = self.ui.menuprojectjoin.menuAction()
        self.request(obj('gerber', 'a'), obj('excellon', 'b'), obj('geometry', 'c'))
        self.assertTrue(join.isVisible())
        self.request(obj('gerber', 'a'))
        self.assertFalse(join.isVisible())
        self.request(obj('gerber', 'a'), obj('cncjob', 'b'))
        self.assertFalse(join.isVisible())


def shapes_of(tools, key):
    return [g for t in tools.values() for g in t.get(key) or []]


class TestJoinAs(unittest.TestCase):
    """Project context menu -> Join -> As Gerber / As Excellon / As Geometry on a mixed selection."""

    def join(self, kind, *selected):
        from appHandlers.appEdit import appEditor
        from appParsers.ParseExcellon import Excellon
        from tests.helpers_objects import make_app_0b

        app = make_app_0b(geometry_merge_fuse_tools=False, excellon_merge_fuse_tools=False)
        app.collection.get_selected = lambda: list(selected)
        made = {}

        def new_object(obj_kind, name, initialize, *args, **kwargs):
            if obj_kind == 'excellon':
                target = Excellon(app)
                target.obj_options = {}
            else:
                target = SimpleNamespace(kind=obj_kind, solid_geometry=None, tools={}, obj_options={},
                                         multigeo=False)
            initialize(target, app)
            made.update(kind=obj_kind, obj=target)

        app.app_obj.new_object = new_object
        appEditor(app).on_join_as(kind)
        self.assertEqual(made.get('kind'), kind)
        return app, made['obj']

    def test_as_excellon(self):
        from shapely.geometry import LineString, Point, box

        grb = obj('gerber', 'grb', tools={
            10: {'type': 'C', 'size': 1.0, 'geometry': [
                {'solid': Point(0, 0).buffer(0.5), 'follow': Point(0, 0)},
                {'solid': LineString([(0, 5), (4, 5), (4, 9)]).buffer(0.5),
                 'follow': LineString([(0, 5), (4, 5), (4, 9)])}]},
            11: {'type': 'O', 'size': 0.8, 'geometry': [
                {'solid': LineString([(10, 0), (12, 0)]).buffer(0.4), 'follow': Point(11, 0)}]},
            0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': box(20, 0, 22, 1)}]},
        })
        geo = obj('geometry', 'geo', multigeo=False,
                  solid_geometry=[Point(30, 0).buffer(1.5), LineString([(40, 0), (42, 0)])],
                  tools={1: {'tooldia': 0.5, 'data': {}, 'solid_geometry': []}})
        app, exc = self.join('excellon', grb, geo)

        drills = sorted((round(t['tooldia'], 3), round(p.x, 3), round(p.y, 3))
                        for t in exc.tools.values() for p in t['drills'])
        slots = sorted((round(t['tooldia'], 3), round(a.x, 3), round(a.y, 3), round(b.x, 3), round(b.y, 3))
                       for t in exc.tools.values() for a, b in t['slots'])
        self.assertEqual(drills, [(1.0, 0.0, 0.0), (3.0, 30.0, 0.0)])
        self.assertEqual(slots, [(0.5, 40.0, 0.0, 42.0, 0.0), (0.8, 10.0, 0.0, 12.0, 0.0),
                                 (1.0, 0.0, 5.0, 4.0, 5.0), (1.0, 4.0, 5.0, 4.0, 9.0)])
        # the rectangle is no drill and no slot: the user is told
        self.assertTrue(any('[WARNING' in c[0][0] for c in app.inform.emit.call_args_list))

    def test_as_gerber(self):
        from shapely.geometry import LineString, Point, box

        exc = obj('excellon', 'exc', tools={1: {'tooldia': 1.0, 'drills': [Point(0, 0)],
                                                'slots': [(Point(0, 2), Point(4, 2))],
                                                'solid_geometry': [Point(0, 0).buffer(0.5)]}})
        geo = obj('geometry', 'geo', multigeo=True, solid_geometry=[],
                  tools={1: {'tooldia': 0.2, 'data': {},
                             'solid_geometry': [LineString([(0, 10), (5, 10)]), box(20, 0, 22, 1)]}})
        grb = obj('gerber', 'grb', solid_geometry=[box(30, 0, 31, 1)], follow_geometry=[],
                  tools={10: {'type': 'C', 'size': 0.3, 'geometry': [{'solid': box(30, 0, 31, 1)}]}})
        __, res = self.join('gerber', exc, geo, grb)

        follows = sorted((ap['type'], round(ap['size'], 3), el['follow'].geom_type)
                         for ap in res.tools.values() for el in ap['geometry'] if 'follow' in el)
        self.assertIn(('C', 1.0, 'Point'), follows)
        self.assertIn(('C', 1.0, 'LineString'), follows)
        self.assertIn(('C', 0.2, 'LineString'), follows)
        self.assertTrue(any(ap['type'] == 'REG' for ap in res.tools.values()))
        for x, y in ((0, 0), (2, 2), (2.5, 10), (21, 0.5), (30.5, 0.5)):
            self.assertTrue(res.solid_geometry.contains(Point(x, y)), (x, y))

    def test_as_geometry_keeps_single_and_multi_geo_shapes(self):
        from shapely.geometry import LineString, Point, box
        from shapely import unary_union

        data = {'tools_mill_job_type': 'Rough', 'tools_mill_tool_shape': 'C1'}
        single = obj('geometry', 'single', multigeo=False, solid_geometry=[LineString([(0, 0), (5, 0)])],
                     tools={1: {'tooldia': 0.5, 'data': dict(data), 'solid_geometry': []}})
        multi = obj('geometry', 'multi', multigeo=True, solid_geometry=[],
                    tools={1: {'tooldia': 1.0, 'data': dict(data), 'solid_geometry': [box(10, 0, 11, 1)]}})
        grb = obj('gerber', 'grb', solid_geometry=[box(20, 0, 21, 1)], tools={})
        __, res = self.join('geometry', single, multi, grb)

        self.assertTrue(res.multigeo)
        joined = unary_union(shapes_of(res.tools, 'solid_geometry'))
        for x, y in ((2.5, 0), (10.5, 0.5), (20.5, 0.5)):
            self.assertTrue(joined.intersects(Point(x, y)), (x, y))


class TestViewSource(unittest.TestCase):

    def view_source(self, selected):
        from appMain import App

        col = make_collection()
        col.get_active = lambda: selected
        app = mock.MagicMock()
        app.collection = col
        with mock.patch('appMain.AppTextEditor'):
            result = App.on_view_source(app)
        return app, result

    def test_object_without_a_source_does_not_open_an_empty_editor(self):
        app, result = self.view_source(obj('geometry', 'iso_geo'))
        self.assertEqual(result, 'fail')
        app.ui.plot_tab_area.addTab.assert_not_called()
        app.proc_container.view.set_busy.assert_not_called()
        message = app.inform.emit.call_args[0][0]
        self.assertTrue(message.startswith('[WARNING_NOTCL]'))
        self.assertIn('iso_geo', message)


if __name__ == '__main__':
    unittest.main()
