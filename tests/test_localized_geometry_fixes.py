"""Behavioral regressions for milling, isolation, deletion, export and editor cuts."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6 import QtWidgets, sip
from shapely.geometry import LineString, LinearRing, MultiLineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from appEditors.appGeoEditor import AppGeoEditor
from appHandlers.appObjectOps import AppObjectOps
from appObjects.CNCJobObject import CNCJobObject
from appPlugins.ToolIsolation import ToolIsolation
from camlib import CNCjob, Geometry
from defaults import AppDefaults
from tclCommands.TclCommandInteriors import TclCommandInteriors
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402


class TestLocalizedGeometryFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_milling_uses_current_tool_depths(self):
        app = make_app()
        job = CNCjob(app)
        job.obj_options = {'name': 'job'}
        job.z_cut, job.z_move, job.z_depthpercut = 0, 0, 99
        job.doformat = Mock(return_value='')
        job.create_gcode_single_pass = Mock(return_value='cut\n')
        job.create_gcode_multi_pass = Mock(side_effect=lambda geo, *a, **k: ('cut\n', geo))
        data = dict(AppDefaults().defaults)
        data.update(tools_mill_tooldia=1, tools_mill_offset_value=0,
                    tools_mill_optimization_type='R', tools_mill_cutz=0.8,
                    tools_mill_travelz=-2, tools_mill_depthperpass=2,
                    tools_mill_multidepth=True)
        tools = {1: {'data': data, 'solid_geometry': [LineString([(0, 0), (2, 0)])]}}

        def generate(tool=1):
            return job.geometry_tool_gcode_gen(tool, tools, (0, 0), (0, 0), 0.001)

        self.assertNotEqual(generate(), 'fail')
        self.assertEqual((job.z_cut, job.z_move, job.z_depthpercut), (-0.8, 2, 0.8))
        job.create_gcode_multi_pass.assert_called_once()
        self.assertEqual(job.create_gcode_multi_pass.call_args.kwargs['z_move'], 2)
        data = dict(data)
        tools[2] = {'data': data, 'solid_geometry': tools[1]['solid_geometry']}
        data.update(tools_mill_cutz=-0.3, tools_mill_travelz=3, tools_mill_depthperpass=0.1)
        self.assertNotEqual(generate(2), 'fail')
        self.assertEqual((job.z_cut, job.z_move, job.z_depthpercut), (-0.3, 3, 0.1))
        tools[1]['data'] = data
        data['tools_mill_travelz'] = 0
        self.assertEqual(generate(), 'fail')
        data.update(tools_mill_travelz=2, tools_mill_cutz=0)
        self.assertEqual(generate(), 'fail')
        data['tools_mill_ppname_g'] = 'default_laser'
        self.assertNotEqual(generate(), 'fail')
        data['tools_mill_cutz'] = None
        self.assertNotEqual(generate(), 'fail')
        data['tools_mill_ppname_g'] = 'default'
        self.assertEqual(generate(), 'fail')
        data.update(tools_mill_cutz=-1, tools_mill_travelz=None)
        self.assertEqual(generate(), 'fail')

    def test_interiors_and_isolation_direction(self):
        geo = Geometry(make_app())
        polygon = Polygon(box(0, 0, 10, 10).exterior,
                          [box(1, 1, 2, 2).exterior.coords, box(5, 5, 7, 7).exterior.coords])
        geo.solid_geometry = [polygon, box(20, 20, 21, 21)]
        rings = geo.get_interiors()
        self.assertEqual(len(rings), 2)
        self.assertTrue(all(isinstance(ring, LinearRing) for ring in rings))
        self.assertEqual(geo.get_interiors(box(0, 0, 1, 1)), [])
        self.assertEqual(len(geo.get_interiors(MultiPolygon(geo.solid_geometry))), 2)
        tool = SimpleNamespace(app=geo.app)
        normal = ToolIsolation.generate_envelope(tool, geo, 0, False, env_iso_type=1)
        reversed_geo = ToolIsolation.generate_envelope(tool, geo, 0, True, env_iso_type=1)
        self.assertEqual(len(reversed_geo.geoms), 2)
        for ring, poly in zip(normal, reversed_geo.geoms):
            self.assertEqual(list(ring.coords)[::-1], list(poly.exterior.coords))
        geo.app.collection.get_by_name.return_value = geo
        output = SimpleNamespace()
        geo.app.app_obj.new_object.side_effect = lambda kind, name, init, **kwargs: init(output, geo.app)
        TclCommandInteriors.execute(SimpleNamespace(app=geo.app), {'name': 'source'}, [])
        self.assertTrue(all(isinstance(ring, LinearRing) for ring in output.solid_geometry))
        self.assertEqual(len(output.solid_geometry), 2)

    def make_job(self):
        app = make_app()
        app.regFK, app.ui = Mock(), Mock()
        app.file_opened, app.file_saved = Mock(), Mock()
        # no other object in the project: a rename keeps the name
        app.collection.get_list.return_value = []
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            job = CNCJobObject('own_job', app)
        job.source_file = 'G0 X1\nM2\n'
        job.update_filters = Mock()
        job.read_form = Mock()
        return job

    def test_export_rename_without_live_properties(self):
        for ui_state in ('absent', 'deleted', 'live'):
            with self.subTest(ui=ui_state), tempfile.TemporaryDirectory() as folder:
                job = self.make_job()
                if ui_state == 'absent':
                    job.ui = None
                else:
                    entry = QtWidgets.QLineEdit()
                    entry.set_value = entry.setText
                    entry.get_value = entry.text
                    job.ui = SimpleNamespace(name_entry=entry)
                    job.form_fields = {'name': entry}
                    if ui_state == 'deleted':
                        sip.delete(entry)
                filename = os.path.join(folder, 'renamed.nc')
                job.export_gcode_handler(filename)
                with open(filename) as stream:
                    self.assertEqual(stream.read(), job.source_file)
                self.assertEqual(job.obj_options['name'], 'renamed')
                self.assertEqual(job.default_data['name'], 'renamed')
                job.app.collection.update_view.assert_called_once_with(job)
                job.app.regFK.remove_keyword.assert_called_once_with('own_job', update=False)
                job.app.regFK.prepend_keyword.assert_called_once_with('renamed')
                if ui_state == 'live':
                    self.assertEqual(entry.text(), 'renamed')
                    job.app.collection.update_view.reset_mock()
                    job.on_name_activate()
                    job.app.collection.update_view.assert_not_called()
                job.export_gcode_handler('')
                self.assertEqual(job.obj_options['name'], 'renamed')
                job.export_gcode_handler(os.path.join(folder, 'other.nc'), rename_object=False)
                self.assertEqual(job.obj_options['name'], 'renamed')

    def cut(self, target, cutter):
        shapes = [SimpleNamespace(geo=target), SimpleNamespace(geo=cutter)]
        app = make_app()
        app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
        editor = SimpleNamespace(app=app, selected=shapes, get_selected=lambda: shapes,
                                 delete_shape=Mock(), plot_changes=Mock(),
                                 build_ui_sig=Mock(), flat_geo=[])
        editor.flatten = lambda *a, **k: AppGeoEditor.flatten(editor, *a, **k)
        editor._collect_shape = lambda shape, dest: AppGeoEditor._collect_shape(editor, shape, dest)
        AppGeoEditor.cutpath(editor)
        editor.delete_shape.assert_called_once()
        self.assertIs(editor.delete_shape.call_args.args[0], shapes[0])
        self.assertEqual(shapes[1].geo, cutter)
        return [shape.geo for shape in editor.delete_shape.call_args.kwargs['inserted']]

    def test_cut_closed_masks_and_multipart_results(self):
        target = LineString([(-2, 0), (2, 0)])
        mask = box(-1, -1, 1, 1)
        for cutter in (mask, mask.exterior, LineString(mask.exterior.coords)):
            result = self.cut(target, cutter)
            self.assertEqual(len(result), 2)
            self.assertTrue(all(isinstance(part, LineString) for part in result))
            self.assertTrue(unary_union(result).equals(target.difference(mask)))
        multi = MultiLineString([target, LineString([(-2, 0.5), (2, 0.5)])])
        self.assertTrue(unary_union(self.cut(multi, mask.exterior)).equals(multi.difference(mask)))
    def test_circle_steps_count_the_whole_circle(self):
        steps = 32
        geo = Geometry(make_app(), geo_steps_per_circle=steps)
        geo.tools = {1: {'solid_geometry': []}}
        geo.add_circle((0, 0), 1.0)
        self.assertAlmostEqual(len(geo.solid_geometry[0].exterior.coords) - 1, steps, delta=2)

        geo.solid_geometry = [LineString([(0, 0), (4, 0)])]
        geo.multigeo = False
        geo.buffer(1.0, 'round', None)
        # a buffered line is two round caps: about one full circle of vertices, not four
        self.assertLessEqual(len(geo.solid_geometry[0].exterior.coords) - 1, steps + 4)

if __name__ == '__main__':
    unittest.main()
