"""The project save makes the WKT of all the geometries at once (camlib.geometry_wkt_cache()).

The saved file must keep the format that older versions read (WKT in {"__class__": "Shply", "__inst__": ...}) and
read back to exactly the same coordinates. appIO.save_project() must not call shapely.wkt.dumps() once per
geometry.
"""

import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shapely
from shapely import affinity, wkt
from shapely.geometry import GeometryCollection, LinearRing, LineString, MultiPolygon, Point, Polygon

from PyQt6 import QtWidgets

import camlib
from appCommon.ProjectFile import load_project_file, save_project_file
from appHandlers.appIO import appIO
from camlib import ApertureMacro, dict2obj, geometry_wkt_cache, project_json_default
from defaults import AppDefaults

# 16 fixed decimals keep only 15 significant digits of this number: shapely.wkt.dumps() loses its last bits
SMALL = 0.012345678901234567


def geometries():
    square = Polygon([(12.445678, 3.1), (13.2, 3.1), (13.2, 4.75), (12.445678, 4.75)],
                     holes=[[(12.6, 3.3), (12.9, 3.3), (12.9, 3.6)]])
    circle = Point(1.0 / 3.0, 2.0 / 7.0).buffer(0.1234567)
    return [
        square,
        circle,
        LineString([(SMALL, 0.2), (0.3, 0.4), (1e-9, 123456.789)]),
        LinearRing([(0, 0), (1, 0), (1, SMALL)]),
        MultiPolygon([square, circle.buffer(5).difference(circle.buffer(4))]),
        Polygon(),
        GeometryCollection([Point(1, 2), LineString([(0, 0), (2, 2)])]),
    ]


def project(geoms):
    macro = ApertureMacro(name='M1')
    macro.raw = '21,1,0.5,0.5,0,0,0'
    return {
        "objs": [
            {"options": {"name": "a"}, "solid_geometry": geoms[:4],
             "tools": {1: {"solid_geometry": [geoms[1]]}}, "macros": {"M1": macro}},
            {"options": {"name": "b"}, "solid_geometry": geoms[4:], "value": 1.5},
        ],
        "options": {"units": "MM"},
        "version": 9.0,
    }


def read_back_geometries(loaded):
    return loaded['objs'][0]['solid_geometry'] + loaded['objs'][1]['solid_geometry']


class TestGeometryWktCache(unittest.TestCase):

    def setUp(self):
        self.geoms = geometries()
        self.data = project(self.geoms)


    def test_geometry_replaced_during_the_save_is_written_as_it_is_now(self):
        # the save dict shares the tool dicts of the objects (to_dict() is a shallow copy) and the project can
        # change while it is saved: a rotate replaces the geometries, the freed ones leave their id() to new ones
        tool = {'solid_geometry': [LineString([(i, 0), (i, 1)]) for i in range(50)]}
        data = {'objs': [{'tools': {1: tool}}]}
        cache = geometry_wkt_cache(data)
        for _turn in range(2):
            tool['solid_geometry'] = [affinity.rotate(geo, 90, origin=(0, 0)) for geo in tool['solid_geometry']]

        text = json.dumps(data, default=project_json_default(cache))
        written = json.loads(text, object_hook=dict2obj)['objs'][0]['tools']['1']['solid_geometry']
        self.assertEqual(len(written), len(tool['solid_geometry']))
        for now, read in zip(tool['solid_geometry'], written):
            self.assertTrue(shapely.equals_exact(now, read, 0))

class TestProjectFileWithWktCache(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.filename = os.path.join(self.tmp.name, 'p.FlatPrj')
        self.geoms = geometries()
        self.data = project(self.geoms)

    def save(self, data, compression_level):
        save_project_file(self.filename, data, default=project_json_default(geometry_wkt_cache(data)),
                          compression_level=compression_level)

    def test_round_trip_is_exact(self):
        for level in (None, 3):
            with self.subTest(level=level):
                self.save(self.data, level)
                loaded = load_project_file(self.filename, object_hook=dict2obj)
                got = read_back_geometries(loaded)
                self.assertEqual(len(got), len(self.geoms))
                for original, read in zip(self.geoms, got):
                    self.assertEqual(original.geom_type, read.geom_type)
                    self.assertTrue(shapely.equals_exact(original, read, 0), original.geom_type)
                tool_geo = loaded['objs'][0]['tools']['1']['solid_geometry'][0]
                self.assertTrue(shapely.equals_exact(tool_geo, self.geoms[1], 0))
                macro = loaded['objs'][0]['macros']['M1']
                self.assertIsInstance(macro, ApertureMacro)
                self.assertEqual(macro.raw, '21,1,0.5,0.5,0,0,0')

    def test_geometry_table_holds_plain_wkt(self):
        self.save(self.data, None)
        with open(self.filename, 'r', encoding='utf-8') as f:
            text = f.read()
        raw = json.loads(text)
        item = raw['objs'][0]['solid_geometry'][0]
        self.assertEqual(item['__class__'], 'Shply')
        # plain WKT in the table, read by shapely.wkt.loads()
        self.assertTrue(shapely.equals_exact(wkt.loads(raw['geometry_table'][item['__ref__']]), self.geoms[0], 0))
        # the shortest text of each number, not 16 fixed decimals
        self.assertIn('12.445678 3.1', text)
        self.assertNotIn('12.4456779999999991', text)

    def test_compressed_save_with_inf_keeps_the_geometries(self):
        # the second write (inf and nan replaced by null) copies the dicts and lists, not the geometries
        self.data['options']['bad'] = [float('inf'), 1.5]
        self.save(self.data, 3)
        loaded = load_project_file(self.filename, object_hook=dict2obj)
        self.assertEqual(loaded['options']['bad'], [None, 1.5])
        self.assertTrue(shapely.equals_exact(read_back_geometries(loaded)[2], self.geoms[2], 0))


class TestSaveProject(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.filename = os.path.join(self.tmp.name, 'p.FlatPrj')
        self.geoms = geometries()
        data = project(self.geoms)
        defaults = AppDefaults()
        self.app = SimpleNamespace(
            log=Mock(), inform=Mock(), splash=None, worker_task=Mock(), options=dict(defaults.defaults),
            defaults=defaults, app_units='MM', version='test', new_project_signal=Mock(),
            proc_container=SimpleNamespace(new=lambda text: nullcontext()),
            collection=Mock(), save_in_progress=False, start_delayed_quit=Mock())
        self.app.collection.get_active.return_value = None
        self.app.collection.get_list.return_value = [SimpleNamespace(to_dict=lambda d=obj: d) for obj in data['objs']]
        self.io = appIO(self.app)

if __name__ == '__main__':
    unittest.main()
