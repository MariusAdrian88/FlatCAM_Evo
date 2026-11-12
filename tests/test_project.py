


# ======================================================================
# merged from tests/test_project_file.py
# ======================================================================
import json
import lzma
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shapely.geometry import Point

from appCommon import ProjectFile
from appCommon.ProjectFile import ZSTD_MAGIC, load_project_file, open_project_stream, save_project_file
from camlib import to_dict, dict2obj, geometry_wkt_cache, project_json_default
from appHandlers.appIO import appIO
from types import SimpleNamespace
from unittest.mock import Mock
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_coords_close, assert_poly_equiv, assert_same_tools, _headless_qt, make_app, make_excellon,
    make_gerber, save_and_load,
)

try:
    import zstandard
except ImportError:
    zstandard = None


def project():
    return {"objs": [{"options": {"name": "a"}, "tools": {1: {"solid_geometry": [Point(1, 2).buffer(1)]}}}],
            "options": {"units": "MM"}, "version": 9.0}


class TestProjectFile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.filename = os.path.join(self.tmp.name, 'p.FlatPrj')

    def check_round_trip(self, compression_level):
        save_project_file(self.filename, project(), default=to_dict, compression_level=compression_level)
        self.assertFalse(os.path.exists(self.filename + '.tmp'))
        loaded = load_project_file(self.filename, object_hook=dict2obj)
        geo = loaded['objs'][0]['tools']['1']['solid_geometry'][0]
        self.assertTrue(geo.equals_exact(Point(1, 2).buffer(1), 1e-9))
        self.assertEqual(loaded['options'], {"units": "MM"})
        return loaded


    def test_zstd_round_trip(self):
        self.check_round_trip(3)
        with open(self.filename, 'rb') as f:
            self.assertEqual(f.read(4), ZSTD_MAGIC)


    def test_legacy_lzma_project_loads(self):
        data = json.dumps(project(), default=to_dict, indent=2, sort_keys=True).encode('utf-8')
        with open(self.filename, 'wb') as f:
            f.write(lzma.compress(data, preset=3))
        loaded = load_project_file(self.filename, object_hook=dict2obj)
        self.assertEqual(loaded['options'], {"units": "MM"})

    def test_equal_geometries_are_saved_once_and_loaded_shared(self):
        circle = Point(1, 2).buffer(1)
        data = {"objs": [{"solid_geometry": [circle], "tools": {1: {"solid_geometry": [circle, Point(5, 5).buffer(1)]}},
                          "follow": [Point(1, 2).buffer(1)]}], "version": 9.0}
        for level in (None, 3):
            with self.subTest(level=level):
                save_project_file(self.filename, data, default=project_json_default(geometry_wkt_cache(data)),
                                  compression_level=level)
                with open_project_stream(self.filename) as stream:
                    raw = json.load(stream)
                self.assertEqual(len(raw['geometry_table']), 2)
                obj = load_project_file(self.filename, object_hook=dict2obj)['objs'][0]
                self.assertNotIn('geometry_table', obj)
                self.assertIs(obj['solid_geometry'][0], obj['tools']['1']['solid_geometry'][0])
                self.assertIs(obj['solid_geometry'][0], obj['follow'][0])
                self.assertTrue(obj['solid_geometry'][0].equals_exact(circle, 0))

    def test_project_over_the_decompressed_size_cap_is_refused(self):
        save_project_file(self.filename, project(), default=to_dict, compression_level=3)
        with patch.object(ProjectFile, 'MAX_DECOMPRESSED_BYTES', 50):
            with self.assertRaises(ProjectFile.ProjectTooLargeError):
                load_project_file(self.filename, object_hook=dict2obj)
        self.assertEqual(load_project_file(self.filename, object_hook=dict2obj)['version'], 9.0)

    def test_failed_save_keeps_the_old_file(self):
        save_project_file(self.filename, project(), default=to_dict, compression_level=3)
        with open(self.filename, 'rb') as f:
            old = f.read()
        for level in (None, 3):
            with self.subTest(level=level):
                # to_dict() returns the unknown object, so json reports a circular reference
                with self.assertRaises(ValueError):
                    save_project_file(self.filename, {"objs": [object()]}, default=to_dict, compression_level=level)
                with open(self.filename, 'rb') as f:
                    self.assertEqual(f.read(), old)
                self.assertFalse(os.path.exists(self.filename + '.tmp'))

    def test_bad_content_raises(self):
        with open(self.filename, 'wb') as f:
            f.write(b'not JSON')
        with self.assertRaises(ValueError):
            load_project_file(self.filename)
        with open(self.filename, 'wb') as f:
            f.write(ZSTD_MAGIC + b'broken')
        with self.assertRaises(Exception):
            load_project_file(self.filename)


def test_saved_project_has_only_project_options(tmp_path):
    from defaults import is_project_key
    app = make_app()
    proj, _restored = save_and_load(app, [], tmp_path)
    assert 'units' in proj['options']
    assert 'global_save_compressed' not in proj['options']
    assert all(is_project_key(key) for key in proj['options'])


def test_save_project_reports_success_and_failure(tmp_path):
    app = make_app()
    io = appIO(app)
    app.collection.get_list.return_value = []
    app.collection.get_active.return_value = None
    # a real failure: the folder does not exist, so open() fails
    assert io.save_project(str(tmp_path / 'missing' / 'bad.FlatPrj'), silent=True) is False
    assert app.save_in_progress is False
    assert io.save_project(str(tmp_path / 'good.FlatPrj'), silent=True) is True


def test_save_project_object_serialization_error_is_a_failure(tmp_path):
    app = make_app()
    io = appIO(app)
    app.collection.get_list.return_value = [SimpleNamespace(to_dict=Mock(side_effect=ValueError('bad object')))]
    app.collection.get_active.return_value = None
    path = tmp_path / 'bad_object.FlatPrj'
    assert io.save_project(str(path), silent=True) is False
    assert app.save_in_progress is False
    assert not path.exists()


class TestProjectRoundTrip:
    def test_gerber_and_excellon_survive_save_and_restore(self, app, tmp_path):
        grb = make_gerber(app, name='grb')
        exc = make_excellon(app, name='exc')
        for t, data in ((1, {'cutz': -1.7, 'tag': 'a'}), (2, {'cutz': -2.0}), (3, {'cutz': -0.5, 'multidepth': True})):
            exc.tools[t]['data'] = data
        proj, restored = save_and_load(app, [grb, exc], tmp_path)
        assert [o['kind'] for o in proj['objs']] == ['gerber', 'excellon']
        assert len(restored) == 2
        grb2, exc2 = restored
        assert (grb2.kind, exc2.kind) == ('gerber', 'excellon')

        # Gerber
        assert grb2.units == grb.units == 'MM'
        assert (grb2.int_digits, grb2.frac_digits) == (4, 6)
        assert grb2.source_file == grb.source_file
        assert grb2.obj_options['name'] == 'grb'
        assert_poly_equiv(grb2.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='gerber project')
        assert_coords_close(grb2.follow_geometry, grb.follow_geometry, atol=2e-6)
        assert set(grb2.aperture_macros) == {'OUTL', 'RING'}
        for name, macro in grb.aperture_macros.items():
            assert (grb2.aperture_macros[name].name, grb2.aperture_macros[name].raw) == (macro.name, macro.raw)
        assert_same_tools(grb.tools, grb2.tools, 'gerber')
        assert all(isinstance(k, int) for k in grb2.tools)

        # Excellon
        assert exc2.units == exc.units == 'MM'
        assert exc2.zeros == exc.zeros
        assert exc2.obj_options['name'] == 'exc'
        assert_same_tools(exc.tools, exc2.tools, 'excellon')
        assert exc2.tools[3]['data'] == {'cutz': -0.5, 'multidepth': True}
        assert exc2.tools[1]['data'] == {'cutz': -1.7, 'tag': 'a'}
        assert_poly_equiv(exc2.solid_geometry, exc.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='excellon project')


# ======================================================================
# merged from tests/test_project_save_wkt.py
# ======================================================================
"""The project_m2 save makes the WKT of all the geometries at once (camlib.geometry_wkt_cache()).

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


def project_m2(geoms):
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
        self.data = project_m2(self.geoms)


    def test_geometry_replaced_during_the_save_is_written_as_it_is_now(self):
        # the save dict shares the tool dicts of the objects (to_dict() is a shallow copy) and the project_m2 can
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
        self.data = project_m2(self.geoms)

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
        data = project_m2(self.geoms)
        defaults = AppDefaults()
        self.app = SimpleNamespace(
            log=Mock(), inform=Mock(), splash=None, worker_task=Mock(), options=dict(defaults.defaults),
            defaults=defaults, app_units='MM', version='test', new_project_signal=Mock(),
            proc_container=SimpleNamespace(new=lambda text: nullcontext()),
            collection=Mock(), save_in_progress=False, start_delayed_quit=Mock())
        self.app.collection.get_active.return_value = None
        self.app.collection.get_list.return_value = [SimpleNamespace(to_dict=lambda d=obj: d) for obj in data['objs']]
        self.io = appIO(self.app)


# ======================================================================
# merged from tests/test_project_load_cleanup.py
# ======================================================================
"""Project parse failures must clean up on the GUI thread."""

import json
import os
import tempfile
import threading
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from PyQt6 import QtCore, QtGui, QtWidgets, QtTest

from appHandlers.appIO import appIO
from defaults import AppOptions


class ProjectSignals(QtCore.QObject):
    new_project = QtCore.pyqtSignal()
    restore = QtCore.pyqtSignal(object, str, bool, bool, bool, bool)
    objects = QtCore.pyqtSignal(object, str, bool, bool)
    opened = QtCore.pyqtSignal(str, str)


class TestProjectLoadCleanup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.window = QtWidgets.QWidget()
        self.addCleanup(self.window.close)
        self.window.set_ui_title = Mock()
        self.signals = ProjectSignals()
        self.tasks = []
        self.app = SimpleNamespace(
            log=Mock(), inform=Mock(), splash=Mock(), app_units='MM', preferencesUiManager=Mock(),
            options=AppOptions(version='test', baseline={}, defaults={}), defaults={},
            ui=self.window, new_project_signal=self.signals.new_project,
            restore_project=self.signals.restore, restore_project_objects_sig=self.signals.objects,
            worker_task=SimpleNamespace(emit=self.tasks.append),
            proc_container=SimpleNamespace(new=lambda text: nullcontext()),
            project_filename='previous.FlatPrj', resource_location='assets/resources',
            app_obj=SimpleNamespace(load_batches=set()), history=Mock())
        self.io = appIO(self.app)
        self.signals.restore.connect(self.io.restore_project_handler)
        self.io.on_file_new_project = Mock()
        # an interactive load shows a message box on a failure
        critical = patch.object(QtWidgets.QMessageBox, 'critical')
        self.critical = critical.start()
        self.addCleanup(critical.stop)
        warning = patch.object(QtWidgets.QMessageBox, 'warning')
        self.warning = warning.start()
        self.addCleanup(warning.stop)
        self.done = []
        self.io.project_load_done.connect(lambda ok, name, failed: self.done.append((ok, name, failed)))

    def wait_for(self, predicate):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < 2000:
            QtTest.QTest.qWait(1)
        self.assertTrue(predicate(), 'GUI completion timed out')

    def parse(self, text, unreadable=False, request_from_worker=False):
        filename = os.path.join(self.directory.name, 'project.FlatPrj')
        with open(filename, 'w') as stream:
            stream.write(text)
        if request_from_worker:
            request = threading.Thread(target=lambda: self.io.open_project(filename, cli=None))
            request.start()
            request.join(2)
            self.assertFalse(request.is_alive())
            self.assertFalse(self.tasks)
        else:
            self.io.open_project(filename)
        self.wait_for(lambda: bool(self.tasks))
        task = self.tasks.pop()
        errors = []

        def run():
            try:
                task['fcn'](*task['params'])
            except Exception as error:
                errors.append(error)

        if unreadable:
            with patch('builtins.open', side_effect=PermissionError('denied')):
                worker = threading.Thread(target=run)
                worker.start()
                worker.join(2)
        else:
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)

    def test_worker_request_starts_and_cleans_up_on_gui_thread(self):
        gui_thread = QtCore.QThread.currentThread()
        title_threads = []
        self.window.set_ui_title.side_effect = lambda **kwargs: title_threads.append(QtCore.QThread.currentThread())
        self.parse('bad project', request_from_worker=True)
        self.wait_for(lambda: len(title_threads) >= 2)
        self.assertEqual(title_threads, [gui_thread, gui_thread])

    def test_bad_project_keeps_the_current_project_and_leaves_no_batch(self):
        cases = (('not JSON or LZMA', False), ('{}', True), ('[]', False), ('{}', False),
                 ('{"objs":null,"options":{}}', False), ('{"objs":[null],"options":{}}', False),
                 ('{"objs":[],"options":[]}', False))
        for text, unreadable in cases:
            with self.subTest(text=text, unreadable=unreadable):
                self.window.set_ui_title.reset_mock()
                self.parse(text, unreadable=unreadable)
                # the failure restores the title of the current project on the GUI thread
                self.wait_for(lambda: self.window.set_ui_title.call_args_list[-1:] == [call(name='previous.FlatPrj')])
                self.assertEqual(self.app.project_filename, 'previous.FlatPrj')
                self.assertFalse(self.app.app_obj.load_batches)
                # one result for each request, with the reason shown to the user
                self.assertEqual([item[0] for item in self.done], [False])
                self.assertEqual(self.critical.call_count, 1)
                self.done.clear()
                self.critical.reset_mock()


    def restore_objects(self, objs):
        """Restore objects with a factory that builds simple objects. Non-interactive: no dialogs."""
        class Obj:
            def __init__(self, kind, name):
                self.kind = kind
                self.obj_options = {}
                if kind != 'script':
                    self.tools = {}

            def from_dict(self, d):
                if d.get('boom'):
                    raise ValueError('bad data')
                self.obj_options = dict(d['obj_options'])
                if hasattr(self, 'tools'):
                    self.tools = d.get('tools', {})

            def on_options_change(self, *args):
                pass

        created = []

        def new_object(kind, name, initialize, **kwargs):
            obj = Obj(kind, name)
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            created.append((name, obj))
            return obj

        self.app.app_obj = SimpleNamespace(load_batches=set(), new_object=new_object)
        self.app.file_opened = self.signals.opened
        self.app.should_we_save = False
        self.app.project_filename = None
        self.io.restore_project_objects({'objs': objs, 'options': {}}, 'board.FlatPrj', True, False)
        task = self.tasks.pop()
        task['fcn'](*task['params'])
        return created

    def test_script_and_bad_objects_do_not_stop_the_load(self):
        opened = Mock()
        self.signals.opened.connect(opened)
        created = self.restore_objects([
            {'kind': 'script', 'obj_options': {'name': 'run'}},
            {'kind': 'gerber', 'boom': True, 'obj_options': {'name': 'broken'}},
            {'kind': 'gerber'},
            {'kind': 'document', 'obj_options': {'name': 'notes'}},
            {'kind': 'gerber', 'obj_options': {'name': 'top'}, 'tools': {'10': {}}},
        ])
        self.assertEqual([name for name, _obj in created], ['run', 'notes', 'top'])
        self.assertEqual(list(created[2][1].tools), [10])
        self.assertEqual(self.done, [(True, 'board.FlatPrj', ['broken', '#3'])])
        # the file is not the project file: a Save must ask for a path; the recent list still gets it
        self.assertIsNone(self.app.project_filename)
        self.assertTrue(self.app.should_we_save)
        self.assertEqual(opened.call_count, 1)


    def test_empty_project_parses_and_finishes_batch(self):
        from appObjects.AppObject import AppObject

        tree = QtWidgets.QTreeView()
        # An empty real selection model is enough; no object UI is constructed.
        model = QtGui.QStandardItemModel()
        tree.setModel(model)
        self.addCleanup(tree.close)
        self.app.collection = Mock()
        self.app.collection.view = tree
        self.app.collection.get_list.return_value = []
        self.app.collection.get_active.return_value = None
        self.app.regFK = Mock()
        self.app.flush_recent_items = Mock()
        self.app.on_notebook_tab_changed = Mock()
        self.app.on_zoom_fit = Mock()
        self.app.on_plots_updated = Mock()
        self.app.file_opened = self.signals.opened
        self.app.app_obj = AppObject(self.app)
        self.window.notebook = Mock()
        self.window.properties_tab = object()
        self.signals.objects.connect(self.io.restore_project_objects)
        opened = Mock()
        self.signals.opened.connect(opened)
        dialog = Mock()
        no = object()
        dialog.clickedButton.return_value = no
        with patch('appHandlers.appIO.FCMessageBox', return_value=dialog):
            dialog.addButton.side_effect = [object(), no]
            self.app.should_we_save = True
            self.parse(json.dumps({'objs': [], 'options': {}}))
            self.wait_for(lambda: bool(self.tasks))
            self.assertTrue(self.app.should_we_save)
            task = self.tasks.pop()
            worker = threading.Thread(target=lambda: task['fcn'](*task['params']))
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.wait_for(lambda: not self.app.app_obj.load_batches)
            self.assertFalse(self.app.should_we_save)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(self.app.flush_recent_items.call_count, 1)


# ======================================================================
# merged from tests/test_project_context.py
# ======================================================================
"""Tests for ProjectContext and its helper functions."""

import os
import unittest

from appObjects.ProjectContext import ProjectContext, unique_name, same_path, name_from_filename, \
    is_valid_name


class TestProjectContext(unittest.TestCase):

    def test_unique_name_adds_number(self):
        self.assertEqual(unique_name('Board', ['Board']), 'Board_1')

    def test_same_path(self):
        self.assertTrue(same_path('a/b/../c.FlatPrj', 'a/c.FlatPrj'))
        self.assertFalse(same_path('a/c.FlatPrj', 'a/d.FlatPrj'))
        self.assertFalse(same_path(None, 'a/d.FlatPrj'))


    def test_is_valid_name(self):
        self.assertTrue(is_valid_name('Board A'))
        self.assertFalse(is_valid_name(''))
        self.assertFalse(is_valid_name('   '))
        for char in '\\/:*?"<>|':
            self.assertFalse(is_valid_name('Bo%sard' % char))


# ======================================================================
# merged from tests/test_project_context_menu.py
# ======================================================================
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


# ======================================================================
# merged from tests/test_projects_appio.py
# ======================================================================
"""appIO follows the multiple projects rules: New, Open, Save and Save As."""

import os
import tempfile
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from PyQt6 import QtCore, QtWidgets

from appHandlers.appHistory import HistoryManager, HistoryStep
from appHandlers.appIO import appIO
from defaults import AppOptions


class ProjectSignals_m6(QtCore.QObject):
    new_project = QtCore.pyqtSignal()
    restore = QtCore.pyqtSignal(object, str, bool, bool, bool, bool)
    objects = QtCore.pyqtSignal(object, str, bool, bool)


class Defaults(dict):
    # on_file_save_project_as() reads app.defaults.factory_defaults
    factory_defaults = {}


class AppIOTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.window = QtWidgets.QWidget()
        self.addCleanup(self.window.close)
        self.window.set_ui_title = Mock()
        self.signals = ProjectSignals_m6()
        self.tasks = []
        self.projects = Mock()
        defaults = Defaults()
        self.app = SimpleNamespace(
            log=Mock(), inform=Mock(), splash=Mock(), defaults=defaults, app_units='MM',
            options=AppOptions(version='test', baseline={}, defaults=defaults), preferencesUiManager=Mock(),
            ui=self.window, new_project_signal=self.signals.new_project,
            restore_project=self.signals.restore, restore_project_objects_sig=self.signals.objects,
            worker_task=SimpleNamespace(emit=self.tasks.append),
            proc_container=SimpleNamespace(new=lambda text: nullcontext()),
            project_filename=None, resource_location='assets/resources',
            app_obj=SimpleNamespace(load_batches=set()), projects=self.projects,
            file_opened=Mock(), file_saved=Mock(), should_we_save=True,
            get_last_save_folder=Mock(return_value=self.directory.name))
        self.io = appIO(self.app)

    def path(self, name):
        return os.path.join(self.directory.name, name)


class TestNewAndOpen(AppIOTestBase):
    def test_reset_clears_only_active_history_in_both_branches(self):
        for keep_scripts in (False, True):
            app = MagicMock()
            app.headless = False
            app.call_source = 'app'
            app.collection.get_list.return_value = []
            app.history = HistoryManager(app, app.collection)
            app.history.push(HistoryStep())
            inactive, _unused = __import__('test_app_history').manager()
            inactive.push(HistoryStep())
            io = appIO(app)
            io.on_file_new_project(cli=True, use_thread=True, keep_scripts=keep_scripts)
            self.assertFalse(app.history.can_undo())
            self.assertTrue(inactive.can_undo())
            app.history_changed.emit.assert_called_with('', '')


    def test_double_click_open_follows_the_gui_rules_and_reports_once(self):
        filename = self.path('Board.FlatPrj')
        with open(filename, 'w') as stream:
            stream.write('{}')
        self.projects.prepare_open.return_value = False
        self.app.splash = None
        done = []
        self.io.project_load_done.connect(lambda ok, name, failed: done.append((ok, name, failed)))

        self.io._open_project(filename, True, True, False, False)

        self.projects.prepare_open.assert_called_once_with(filename)
        self.assertEqual(self.tasks, [])
        self.assertEqual(done, [(False, filename, [])])
        # the load is over: the next request is accepted
        self.projects.prepare_open.return_value = True
        self.io._open_project(filename, True, True, False, False)
        self.assertEqual(len(self.tasks), 1)

class TestSave(AppIOTestBase):
    def run_queued_save(self, ok):
        """Run the queued save task with a save_project() that returns ok."""
        from unittest.mock import patch
        task = self.tasks[-1]
        with patch.object(appIO, 'save_project', return_value=ok):
            task['fcn'](*task['params'])

    def test_save_after_rename_writes_the_new_file(self):
        self.app.project_filename = self.path('Board_A.FlatPrj')
        self.projects.save_target.return_value = self.path('Board_B.FlatPrj')

        self.io.on_file_save_project()

        self.assertEqual(self.tasks[-1]['params'][0], self.path('Board_B.FlatPrj'))
        # the file name does not change before the save ends; the save blocks the exit
        self.assertEqual(self.app.project_filename, self.path('Board_A.FlatPrj'))
        self.assertTrue(self.app.save_in_progress)
        self.projects.adopt_filename.assert_not_called()
        self.app.file_saved.emit.assert_not_called()

        self.run_queued_save(True)

        self.assertEqual(self.app.project_filename, self.path('Board_B.FlatPrj'))
        self.projects.adopt_filename.assert_called_once_with(self.path('Board_B.FlatPrj'))
        self.assertFalse(self.app.should_we_save)
        self.app.file_saved.emit.assert_called_once_with('project', self.path('Board_B.FlatPrj'))

    def test_failed_save_keeps_the_old_state(self):
        self.app.project_filename = self.path('Board_A.FlatPrj')
        self.projects.save_target.return_value = self.path('Board_B.FlatPrj')

        self.io.on_file_save_project()
        self.run_queued_save(False)

        self.assertEqual(self.app.project_filename, self.path('Board_A.FlatPrj'))
        self.projects.adopt_filename.assert_not_called()
        self.assertTrue(self.app.should_we_save)
        self.app.file_saved.emit.assert_not_called()

    def test_change_during_the_save_keeps_the_project_dirty(self):
        self.app.project_filename = self.path('Board_A.FlatPrj')
        self.projects.save_target.return_value = self.path('Board_A.FlatPrj')
        self.io.on_file_save_project()
        # the user deletes an object while the worker writes the earlier state
        self.app.should_we_save = True

        self.run_queued_save(True)

        self.assertTrue(self.app.should_we_save)
        self.app.file_saved.emit.assert_called_once_with('project', self.path('Board_A.FlatPrj'))

    def test_second_save_is_refused_while_the_first_is_running(self):
        self.app.project_filename = self.path('Board_A.FlatPrj')
        self.projects.save_target.return_value = self.path('Board_A.FlatPrj')
        self.io.on_file_save_project()
        self.io.on_file_save_project()
        self.assertEqual(len(self.tasks), 1)
        self.assertTrue(self.app.project_save_pending)

        self.run_queued_save(True)

        self.assertFalse(self.app.project_save_pending)
        self.io.on_file_save_project()
        self.assertEqual(len(self.tasks), 2)

    def test_save_after_rename_asks_before_overwrite(self):
        self.app.project_filename = self.path('Board_A.FlatPrj')
        with open(self.path('Board_B.FlatPrj'), 'w') as stream:
            stream.write('x')
        self.projects.save_target.return_value = self.path('Board_B.FlatPrj')
        self.projects.ask_yes_no.return_value = False

        self.io.on_file_save_project()

        self.assertEqual(self.tasks, [])
        self.assertEqual(self.app.project_filename, self.path('Board_A.FlatPrj'))


# ======================================================================
# merged from tests/test_restart_project_save.py
# ======================================================================
"""A restart (language, font size, preferences that need a restart) saves the projects first and waits for the save.

os.execl() ends the process at once, so a project save that still runs in a worker thread is lost. The tests drive
the real restart_program() with os.execl() replaced, the real save (appIO.save_project(), slowed down) and the real
ProjectManager, and check the state of the project file at the moment of the restart.
"""

import json
import os
import sys
import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtWidgets

import appTranslation
import appHandlers.appIO as appio_module
from appGUI.GUIElements import FCFileSaveDialog
from appHandlers.appIO import appIO
from appHandlers.appLifecycle import AppLifecycle
from appHandlers.appProjects import ProjectManager
from defaults import AppOptions

YES = QtWidgets.QMessageBox.ButtonRole.YesRole
NO = QtWidgets.QMessageBox.ButtonRole.NoRole
CANCEL = QtWidgets.QMessageBox.ButtonRole.RejectRole


@pytest.fixture(scope='module')
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeSettings:
    """QSettings without the registry or the settings file of the user."""
    values = {}

    def __init__(self, *args):
        pass

    def contains(self, key):
        return key in self.values

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class Defaults_m7(dict):
    factory_defaults = {}


class FakeCollection:
    def __init__(self, objects=()):
        self.view = QtWidgets.QWidget()
        self.objects = list(objects)

    def get_list(self):
        return list(self.objects)

    def get_active(self):
        return None

    def has_promises(self):
        return False

    def has_plot_promises(self):
        return False


class Board:
    """An object of a project; save_project() writes its dict."""

    def __init__(self, name):
        self.name = name

    def to_dict(self):
        return {'kind': 'geometry', 'obj_options': {'name': self.name}}


class Workers:
    """app.worker_task: runs each task in a thread, like the workers of the App."""

    def __init__(self):
        self.threads = []

    def emit(self, task):
        thread = threading.Thread(target=task['fcn'], args=task['params'], daemon=True)
        self.threads.append(thread)
        thread.start()

    def running(self):
        return any(thread.is_alive() for thread in self.threads)

    def join(self):
        for thread in self.threads:
            thread.join(10)


class Dialogs(QtCore.QObject):
    """Answers the modal message boxes with the button roles in self.answers, in order."""

    def __init__(self):
        super().__init__()
        self.answers = []
        self.asked = 0
        self.unexpected = []
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def poll(self):
        box = QtWidgets.QApplication.activeModalWidget()
        if not isinstance(box, QtWidgets.QMessageBox) or not box.isVisible() or box.property('answered'):
            return
        box.setProperty('answered', True)
        self.asked += 1
        role = self.answers.pop(0) if self.answers else None
        button = next((b for b in box.buttons() if box.buttonRole(b) == role), None)
        if button is None:
            self.unexpected.append((role, [b.text() for b in box.buttons()]))
            box.done(0)
        else:
            button.click()


def project_names(filename):
    with open(filename, 'r') as stream:
        return [obj['obj_options']['name'] for obj in json.load(stream)['objs']]


@pytest.fixture
def restart(qt_app, tmp_path, monkeypatch):
    FakeSettings.values = {}
    monkeypatch.setattr(appTranslation, 'QSettings', FakeSettings)

    window = QtWidgets.QWidget()
    window.set_ui_title = Mock()
    window.general_pref_form = SimpleNamespace(general_app_group=SimpleNamespace(
        language_combo=SimpleNamespace(currentText=lambda: 'Deutsch')))
    defaults = Defaults_m7({'units': 'MM', 'global_systray_icon': True})
    workers = Workers()
    app = SimpleNamespace(
        log=Mock(), inform=Mock(), splash=Mock(), defaults=defaults, app_units='MM', version='test',
        options=AppOptions(version='test', baseline=defaults, defaults=defaults), ui=window,
        new_project_signal=Mock(), worker_task=workers, workers=SimpleNamespace(pending_count=lambda: 0),
        proc_container=SimpleNamespace(new=lambda text: nullcontext(), get_procs_count=lambda: 0),
        call_source='app', app_obj=SimpleNamespace(load_batches=set()), plotcanvas=None,
        collection=FakeCollection([Board('Top')]), all_objects_list=[], project_filename=None,
        should_we_save=True, save_in_progress=False, project_save_pending=False, close_after_save=False,
        resource_location='assets/resources', file_opened=Mock(), file_saved=Mock(),
        get_last_save_folder=lambda: str(tmp_path), start_delayed_quit=Mock(), preferencesUiManager=Mock(),
        new_launch=Mock(), trayIcon=Mock())
    app.f_handlers = appIO(app)
    projects = ProjectManager(app, collection_factory=FakeCollection)
    # the steps that change the canvas and the plugins
    projects.before_switch = Mock()
    projects.deactivate = Mock()
    projects.activate = Mock()
    projects.reset_options = Mock()
    app.projects = projects

    # the Save As dialog
    chosen = {'path': str(tmp_path / 'Board.FlatPrj')}
    save_dialog = Mock(side_effect=lambda *args, **kwargs: (chosen['path'], ''))
    monkeypatch.setattr(FCFileSaveDialog, 'get_saved_filename', save_dialog)

    # a slow project write, as for a big project
    real_write = appio_module.save_project_file

    def slow_write(filename, *args, **kwargs):
        time.sleep(0.3)
        return real_write(filename, *args, **kwargs)

    monkeypatch.setattr(appio_module, 'save_project_file', slow_write)

    # the restart: the state of the save when os.execl() is called
    restarts = []

    def execl(*args):
        path = chosen['path']
        restarts.append(SimpleNamespace(
            args=args, saving=workers.running(), save_pending=app.project_save_pending,
            names=project_names(path) if os.path.exists(path) else None))

    monkeypatch.setattr(os, 'execl', execl)

    dialogs = Dialogs()
    yield SimpleNamespace(app=app, projects=projects, chosen=chosen, save_dialog=save_dialog, restarts=restarts,
                          dialogs=dialogs, workers=workers, tmp_path=tmp_path)
    dialogs.timer.stop()
    workers.join()
    QtWidgets.QApplication.processEvents()
    window.deleteLater()


def assert_no_restart(s):
    assert s.restarts == []
    # the app goes on: the listener of the second instances is not stopped, the settings file is not written
    s.app.new_launch.close.assert_not_called()
    s.app.preferencesUiManager.save_defaults.assert_not_called()
    assert s.app.inform.emit.call_args[0][0].startswith('[WARNING_NOTCL]')


@pytest.mark.parametrize('has_file', [False, True], ids=['save-as', 'save'])
def test_yes_restarts_only_after_the_project_file_is_written(restart, has_file):
    s = restart
    if has_file:
        # an older version of the project file; Save writes the same file without a dialog
        with open(s.chosen['path'], 'w') as stream:
            json.dump({'objs': [], 'options': {}, 'version': 'old'}, stream)
        s.app.project_filename = s.chosen['path']
        s.projects.adopt_filename(s.chosen['path'])
    s.dialogs.answers = [YES]

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.asked == 1 and s.dialogs.unexpected == []
    assert len(s.restarts) == 1
    done = s.restarts[0]
    assert done.args[0] == sys.executable
    # the save had ended and the file holds the project when the process was replaced
    assert not done.saving and not done.save_pending
    assert done.names == ['Top']
    assert s.app.should_we_save is False
    assert s.app.project_filename == s.chosen['path']
    assert s.save_dialog.call_count == (0 if has_file else 1)


def test_cancelled_save_dialog_does_not_restart(restart):
    s = restart
    s.chosen['path'] = ''
    s.dialogs.answers = [YES]

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.unexpected == []
    s.save_dialog.assert_called_once()
    assert_no_restart(s)
    assert s.app.should_we_save is True


def test_failed_save_does_not_restart(restart, monkeypatch):
    s = restart

    def denied(*args, **kwargs):
        raise PermissionError('the file is open in another application')

    monkeypatch.setattr(appio_module, 'save_project_file', denied)
    s.dialogs.answers = [YES]

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.unexpected == []
    s.workers.join()
    assert_no_restart(s)
    assert not os.path.exists(s.chosen['path'])
    assert s.app.should_we_save is True
    assert s.app.save_in_progress is False


def test_changed_inactive_project_is_saved_before_the_restart(restart):
    # the same rule as the exit of the app: each project with changes is asked about, not only the active one
    s = restart
    s.app.should_we_save = False
    start = s.projects.active
    other = s.projects.add_context('Board_B')
    other.collection.objects = [Board('Bottom')]
    other.should_we_save = True
    s.chosen['path'] = str(s.tmp_path / 'Board_B.FlatPrj')
    s.dialogs.answers = [YES]

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.asked == 1 and s.dialogs.unexpected == []
    assert len(s.restarts) == 1
    assert s.restarts[0].names == ['Bottom']
    assert other.should_we_save is False
    # the canvas still shows the first project: it is the active one again
    assert s.projects.active is start
    assert s.app.collection is start.collection


@pytest.mark.parametrize('entry, state', [('restart_program', 'worker'), ('restart_program', 'result'),
                                          ('language', 'worker'), ('preferences', 'result')])
def test_running_save_is_not_killed_by_a_restart(restart, monkeypatch, entry, state):
    """Every caller of restart_program(): a save that runs (File -> Save, Save As) is never cut by a restart."""
    s = restart
    # _queue_project_save() cleared the dirty flag, the worker writes the file
    s.app.should_we_save = False
    s.app.project_save_pending = True
    # the worker ended but the GUI does not have the result yet
    s.app.save_in_progress = state == 'worker'
    s.dialogs.answers = [YES]

    if entry == 'restart_program':
        appTranslation.restart_program(app=s.app)
    elif entry == 'language':
        FakeSettings.values['language'] = 'English'
        appTranslation.on_language_apply_click(app=s.app, restart=True)
        assert s.dialogs.asked == 1
        assert FakeSettings.values['language'] == 'Deutsch'
    elif entry == 'font_size':
        import appGUI.preferences.general.GeneralGUIPrefGroupUI as gui_prefs
        monkeypatch.setattr(gui_prefs, 'QSettings', FakeSettings)
        gui_prefs.GeneralGUIPrefGroupUI.handle_font_size(s.app, 12)
        assert FakeSettings.values['font_size'] == '12'
    else:
        # Preferences -> Save with a new appearance, style or title bar, and the icon color
        AppLifecycle(s.app).on_app_restart()
        s.app.trayIcon.hide.assert_called_once_with()

    assert s.dialogs.unexpected == []
    assert_no_restart(s)
    # the tray icon is shown again when the app does not restart
    s.app.trayIcon.show.assert_called_once_with()
    s.save_dialog.assert_not_called()


@pytest.mark.parametrize('busy', ['task'])
def test_busy_app_with_changes_does_not_ask_and_does_not_restart(restart, busy):
    # save_context() cannot save now, so the question is not asked and the reason is the last message
    s = restart
    if busy == 'editor':
        s.app.call_source = 'geo_editor'
    else:
        s.app.proc_container = SimpleNamespace(new=lambda text: nullcontext(), get_procs_count=lambda: 1)

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.asked == 0
    assert_no_restart(s)
    text = s.app.inform.emit.call_args[0][0]
    assert ('Close the editor first.' if busy == 'editor' else 'A task is running.') in text
    assert 'FlatCAM did not restart.' in text
