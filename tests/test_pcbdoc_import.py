import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtWidgets
from shapely.ops import unary_union
from shapely.geometry import Polygon, Point, LineString
from shapely.affinity import translate
from appHandlers.appIO import appIO


class TestPcbDocImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app = SimpleNamespace(log=Mock(), inform=Mock(), splash=None, worker_task=Mock(),
                                   options={}, app_units='MM', new_project_signal=Mock(),
                                   abort_flag=False, app_obj=Mock(), plot_all=Mock(), file_opened=Mock())
        self.app.projects = SimpleNamespace(is_busy=lambda: False,
                                            active=SimpleNamespace(collection=SimpleNamespace(get_list=lambda: [])))
        self.app.project_filename, self.app.should_we_save = None, False
        self.io = appIO(self.app)
        polygon = Polygon([(-25.4, 0), (25.4, 0), (25.4, 25.4), (-25.4, 25.4)],
                          [[(-5, 5), (5, 5), (5, 10), (-5, 10)]])
        self.parsed = SimpleNamespace(layers={1: dict(name='Top', solid_geometry=[polygon],
                                      follow_geometry=[LineString([(0, 0), (25.4, 0)])],
                                      tools={10: dict(type='C', size=2.54, geometry=[])}),
                                      32: dict(name='Mechanical', solid_geometry=[polygon],
                                               follow_geometry=[], tools={})},
                                      drill_groups={'PTH': {1: dict(tooldia=2.54,
                                          drills=[Point(25.4, 0)],
                                          slots=[(Point(0, 0), Point(25.4, 0))])}}, warnings=[])
        self.objects = []

        def create(kind, name, initialize, **kwargs):
            obj = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj))
            return obj

        self.app.app_obj.new_object.side_effect = create
        self.io.export_gerber = Mock(return_value='G04*\nM02*\n')
        self.io.export_excellon = Mock(return_value='M48\nM30\n')

    def tearDown(self):
        self.qapp.processEvents()

    def test_current_units_tools_geometry_drills_slots_and_selection(self):
        self.app.app_units = 'IN'
        self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], ['PTH'])
        self.assertEqual([kind for kind, obj in self.objects], ['gerber', 'excellon'])
        gerber, drill = [obj for kind, obj in self.objects]
        self.assertEqual(gerber.units, 'IN')
        self.assertAlmostEqual(gerber.tools[10]['size'], 0.1)
        for actual, expected in zip(gerber.solid_geometry[0].bounds, (-1, 0, 1, 1)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(len(gerber.solid_geometry[0].interiors), 1)
        for actual, expected in zip(gerber.follow_geometry[0].bounds, (0, 0, 1, 0)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(drill.tools[1]['tooldia'], 0.1)
        self.assertAlmostEqual(drill.tools[1]['drills'][0].x, 1)
        self.assertAlmostEqual(drill.tools[1]['slots'][0][1].x, 1)
        drill.create_geometry.assert_called_once()
        self.assertEqual(self.parsed.layers[1]['tools'][10]['size'], 2.54)
        self.assertEqual(self.app.worker_task.emit.call_args.args[0]['priority'], 'low')

    def test_export_failure_does_not_publish_corrupt_object(self):
        self.io.export_gerber.return_value = 'fail'
        self.assertEqual(self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], []), 'fail')
        self.assertEqual(self.objects, [])
        self.qapp.processEvents()
        self.app.file_opened.emit.assert_not_called()

    def test_proteus_identified_extensions_and_unknown_artwork_names(self):
        self.parsed.name = 'Power Board'
        self.parsed.layers = {key: dict(self.parsed.layers[1], name=name) for key, name in
                              (('Top Copper.GTL', 'Top Copper'), ('Inner 1.G1', 'Inner 1'),
                               ('preview', 'Preview artwork'))}
        self.io.import_pcbdoc_objects('design.pdsprj', self.parsed, list(self.parsed.layers), ['PTH'])
        self.assertEqual([obj.obj_options['name'] for kind, obj in self.objects],
                         ['Power Board.GTL', 'Power Board.G1', 'Power Board_preview', 'Power Board_PTH'])
        self.assertTrue(all(obj.source_file for kind, obj in self.objects))

    def test_proteus_source_save_and_export_keep_the_identified_layer_extension(self):
        import appHandlers.appIO as io_module

        self.parsed.name = 'Power Board'
        self.parsed.layers = {'Top Copper.GTL': self.parsed.layers[1]}
        self.io.import_pcbdoc_objects('design.pdsprj', self.parsed, ['Top Copper.GTL'], [])
        obj = self.objects[0][1]
        obj.update_filters = Mock()
        self.app.collection = SimpleNamespace(get_active=lambda: obj)
        self.app.defaults = {'gerber_save_filters': 'Gerber File (*.GBR)'}
        self.app.options = {'global_open_style': True}
        self.app.get_last_save_folder = lambda: 'C:/output'
        self.app.file_saved = Mock()
        self.io.save_source_file = Mock()

        def save_dialog(**kwargs):
            return kwargs['directory'], kwargs['filter'].split(';;')[0]

        with patch.object(io_module, 'GerberObject', SimpleNamespace), \
                patch.object(QtWidgets.QFileDialog, 'getSaveFileName', side_effect=save_dialog):
            self.io.on_file_save_gerber()
            self.io.save_source_file.assert_called_once_with('Power Board.GTL', 'C:/output/Power Board.GTL')
            self.io.on_file_export_gerber()
            self.assertEqual(self.io.export_gerber.call_args.args, ('Power Board.GTL', 'C:/output/Power Board.GTL'))

    def test_sprint_and_kicad_boards_import_into_separate_projects(self):
        from PyQt6.QtTest import QTest
        from test_projects_manager import make_manager

        app, projects = make_manager()
        app.projects = projects
        app.app_units = 'MM'
        app.abort_flag = False
        app.new_project_signal = Mock()
        app.worker_task = Mock()
        app.file_opened = Mock()
        app.plot_all = Mock()
        app.ui = QtWidgets.QWidget()
        app.ui.set_ui_title = Mock()
        app.app_obj = Mock()
        app.app_obj.load_batches = set()
        jobs = []
        busy = [True]
        app.workers.pending_count.side_effect = lambda: int(busy[0])
        app.worker_task.emit.side_effect = jobs.append
        io = appIO(app)
        io.export_gerber = self.io.export_gerber
        io.export_excellon = self.io.export_excellon

        def create(kind, name, initialize, **kwargs):
            obj = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
            if initialize(obj, app) == 'fail':
                return 'fail'
            app.collection.objects.append(obj)
            return obj

        app.app_obj.new_object.side_effect = create
        first = projects.active
        with patch.object(QtWidgets.QDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Accepted):
            for filename in ('cata.lay6', 'Easyduino_RP2040.kicad_pcb'):
                busy[0] = True
                io.on_pcbdoc_parsed(filename, self.parsed)
                QTest.qWait(70)
                self.assertIs(projects.active, first)
                busy[0] = False
                QTest.qWait(100)
                job = jobs.pop(0)
                job['fcn'](*job['params'])
                jobs.clear()  # the low-priority plotting job

        self.assertEqual(len(projects.contexts), 2)
        self.assertIs(projects.contexts[0], first)
        self.assertTrue(all(obj.obj_options['name'].startswith('cata_') for obj in first.collection.objects))
        self.assertTrue(first.collection.objects)
        self.assertTrue(projects.active.collection.objects)
        self.assertTrue(all(obj.obj_options['name'].startswith('Easyduino_RP2040_')
                            for obj in projects.active.collection.objects))
        with patch.object(QtWidgets.QDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Rejected):
            io.on_pcbdoc_parsed('cancelled.kicad_pcb', self.parsed)
        self.assertEqual(len(projects.contexts), 2)

    def test_proteus_boards_stay_in_separate_projects_until_insertion_and_plots_finish(self):
        self._import_proteus_projects()

    def test_proteus_export_failure_stops_remaining_boards_without_publishing_bad_source(self):
        self._import_proteus_projects(stop='failure')

    def test_proteus_abort_drains_pending_objects_without_switching_to_another_board(self):
        self._import_proteus_projects(stop='abort')

    def test_proteus_cancel_keeps_existing_projects_and_creates_no_board_project(self):
        self._import_proteus_projects(stop='cancel')

    def test_proteus_invalid_board_title_falls_back_before_creating_a_project(self):
        self._import_proteus_projects(names=('Power: 5V', 'Power'),
                                      members=('BOARD1.LYT', 'BOARD2.LYT'),
                                      expected=('BOARD1', 'Power'))

    def test_proteus_dialog_checks_only_verified_drill_groups_by_default(self):
        self.app.ui = QtWidgets.QWidget()
        self.parsed.name = 'Power Board'
        self.parsed.default_drill_groups = ['PTH']
        self.parsed.drill_groups['Unverified span 0x3d'] = {
            1: dict(tooldia=0.3, drills=[Point(0, 0)], slots=[])}
        states = {}

        def choose(dialog):
            states.update({box.text(): box.isChecked() for box in dialog.findChildren(QtWidgets.QCheckBox)})
            return QtWidgets.QDialog.DialogCode.Accepted

        with patch.object(QtWidgets.QDialog, 'exec', choose):
            selection = self.io.on_pcbdoc_parsed('design.pdsprj', self.parsed)
        self.assertFalse(states['Unverified span 0x3d'])
        self.assertTrue(states['PTH'])
        self.assertEqual(selection[1], ['PTH'])

        del self.parsed.default_drill_groups
        with patch.object(QtWidgets.QDialog, 'exec', choose):
            self.io.on_pcbdoc_parsed('design.pdsprj', self.parsed)
        self.assertTrue(states['Unverified span 0x3d'])

    def _import_proteus_projects(self, stop=None, names=('Main', 'Power'), members=None, expected=None):
        from test_projects_manager import make_manager, FakeCollection
        from PyQt6.QtGui import QStandardItemModel

        app, projects = make_manager()
        app.projects = projects
        original = projects.active
        original.collection.objects.append('existing')
        app.app_units = 'MM'
        app.abort_flag = False
        app.new_project_signal = Mock()
        app.worker_task = Mock()
        app.file_opened = Mock()
        app.on_notebook_tab_changed = Mock()
        app.on_zoom_fit = Mock()
        app.regFK = Mock()
        app.flush_recent_items = Mock()
        app.ui = QtWidgets.QWidget()
        app.ui.set_ui_title = Mock()
        app.ui.notebook = QtWidgets.QTabWidget()
        app.ui.properties_tab = QtWidgets.QWidget()
        app.plotcanvas = Mock()

        def collection():
            result = FakeCollection()
            result.view = QtWidgets.QTreeView()
            result.view.setModel(QStandardItemModel(result.view))
            return result

        projects.collection_factory = collection
        pending_plots, worker_jobs = [], []

        class Owner(QtCore.QObject):
            def __init__(owner):
                super().__init__()
                owner.app = app
                owner.load_context = threading.local()
                owner.load_batches = set()

            def new_object(owner, kind, name, initialize, **kwargs):
                obj = SimpleNamespace(obj_options={'name': name}, create_geometry=lambda: None, kind=kind)
                if initialize(obj, app) == 'fail':
                    return 'fail'
                batch = owner.load_context.batch
                batch.batch_event.emit('created', (obj, kwargs['plot'], False, None, []))
                return obj

            def on_object_created(owner, obj, plot, *args):
                app.collection.objects.append(obj)
                if plot:
                    obj._load_batch.plots += 1
                    pending_plots.append(obj._load_batch)

        app.app_obj = Owner()
        app.worker_task.emit.side_effect = worker_jobs.append
        app.workers.pending_count.side_effect = lambda: len(worker_jobs)
        io = appIO(app)
        io.export_gerber = self.io.export_gerber
        io.export_excellon = self.io.export_excellon
        boards = [SimpleNamespace(**dict(vars(self.parsed), name=name, member_name=member))
                  for name, member in zip(names, members or tuple(name + '.LYT' for name in names))]
        expected = expected or names
        parsed = SimpleNamespace(boards=boards, warnings=['Proteus 9 native decoder', 'Incomplete fidelity'] * 2,
                                 major_version=9)
        warnings = []

        def accept(dialog):
            warnings.append('\n'.join(widget.toPlainText() for widget in
                                      dialog.findChildren(QtWidgets.QPlainTextEdit)))
            return (QtWidgets.QDialog.DialogCode.Rejected if stop == 'cancel' else
                    QtWidgets.QDialog.DialogCode.Accepted)

        with patch.object(QtWidgets.QDialog, 'exec', accept):
            io.on_pcbdoc_parsed('design.pdsprj', parsed)
            if stop == 'failure':
                io.export_gerber.return_value = 'fail'
            for index in range(2):
                io._advance_proteus_import()
                if stop == 'cancel':
                    io._advance_proteus_import()
                    self.assertEqual(projects.contexts, [original])
                    break
                self.assertEqual(len(projects.contexts), index + 2)
                context = projects.active
                self.assertEqual(context.name, expected[index])
                job = worker_jobs.pop(0)
                thread = threading.Thread(target=job['fcn'], args=job['params'])
                thread.start()
                thread.join()
                io._advance_proteus_import()
                self.assertIs(projects.active, context)
                if stop == 'abort':
                    app.abort_flag = True
                self.qapp.processEvents()
                self.qapp.processEvents()
                io._advance_proteus_import()
                self.assertIs(projects.active, context)
                self.assertEqual(len(context.collection.objects), 0 if stop == 'failure' else 3)
                for batch in pending_plots:
                    batch.batch_event.emit('plotted', None)
                pending_plots.clear()
                self.qapp.processEvents()
                # Batch completion alone isn't permission to switch while another task remains.
                app.workers.pending_count.side_effect = None
                app.workers.pending_count.return_value = 1
                io._advance_proteus_import()
                self.assertIs(projects.active, context)
                app.workers.pending_count.side_effect = lambda: len(worker_jobs)
                if stop:
                    io._advance_proteus_import()
                    self.assertEqual(len(projects.contexts), 2)
                    break
            io._advance_proteus_import()
        self.assertEqual(original.collection.objects, ['existing'])
        self.assertEqual([ctx.name for ctx in projects.contexts[1:]],
                         [] if stop == 'cancel' else list(expected[:1]) if stop else list(expected))
        self.assertTrue(all(ctx.filename is None for ctx in projects.contexts[1:]))
        self.assertTrue(all(obj.obj_options['name'].startswith(board.name)
                            for board, ctx in zip(boards, projects.contexts[1:])
                            for obj in ctx.collection.objects))
        if stop:
            app.file_opened.emit.assert_not_called()
        else:
            app.file_opened.emit.assert_called_once_with('proteus', 'design.pdsprj')
        self.assertTrue(any('Proteus 9 native decoder' in text for text in warnings))
        self.assertFalse(any('using the Proteus 8 decoder' in text for text in warnings))
        self.assertTrue(any(text.count('Incomplete fidelity') == 1 for text in warnings))

    def test_proteus_facade_result_is_queued_and_parse_failure_creates_no_projects(self):
        self.app.ui = QtWidgets.QWidget()
        self.app.projects = Mock()
        self.app.projects.is_busy.return_value = False
        parsed = SimpleNamespace(boards=[], warnings=[], major_version=8)
        parser = Mock()
        parser.return_value.parse_file.return_value = parsed
        with patch.dict('sys.modules', {'appParsers.ParseProteus': SimpleNamespace(ProteusParser=parser)}):
            self.io.on_file_import_proteus('design.pdsprj')
            job = self.app.worker_task.emit.call_args.args[0]
            thread = threading.Thread(target=job['fcn'], args=job['params'])
            thread.start()
            thread.join()
            self.app.projects.new.assert_not_called()
            self.qapp.processEvents()
            self.io._advance_proteus_import()
            self.app.projects.new.assert_not_called()
            parser.return_value.parse_file.side_effect = ValueError('invalid container')
            self.assertEqual(self.io.parse_proteus('bad.pdsprj'), 'fail')
            self.qapp.processEvents()
            self.app.projects.new.assert_not_called()
            self.app.file_opened.emit.assert_not_called()

    def test_proteus_menu_and_recent_action_use_the_container_importer(self):
        import json
        from appMain import App
        from appHandlers.appSignalConnector import AppSignalConnector
        from PyQt6.QtGui import QAction

        app = QtCore.QObject()
        app.ui = Mock()
        app.ui.menufileimportproteus = QAction('Proteus', app)
        app.ui.recent = QtWidgets.QMenu()
        app.ui.recent_projects = QtWidgets.QMenu()
        app.log = Mock()
        app.f_handlers = Mock()
        app.resource_location = ''
        app.image_tool = Mock()
        app.worker_task = Mock()
        connector = AppSignalConnector(app)
        connector.connect_filemenu_signals()
        app.ui.menufileimportproteus.trigger()
        app.f_handlers.on_file_import_proteus.assert_called_once_with()
        app.f_handlers.on_file_import_proteus.reset_mock()
        with tempfile.TemporaryDirectory() as folder:
            app.data_path = folder
            for name, content in (('recent.json', [{'kind': 'proteus', 'filename': 'design.pdsprj'}]),
                                  ('recent_projects.json', [])):
                with open(os.path.join(folder, name), 'w') as handle:
                    json.dump(content, handle)
            App.setup_recent_items(app)
            action = app.ui.recent.actions()[0]
            self.assertEqual(action.text(), 'design.pdsprj')
            action.trigger()
        app.f_handlers.on_file_import_proteus.assert_called_once_with('design.pdsprj')
        app.worker_task.emit.assert_not_called()

    def test_region_export_reimport_preserves_negative_holes_and_islands(self):
        from appObjects.GerberObject import GerberObject
        from appParsers.ParseGerber import Gerber
        from defaults import AppDefaults
        defaults = AppDefaults()
        self.app.defaults = defaults
        self.app.options = dict(defaults.defaults)
        self.app.options['gerber_exp_units'] = 'MM'
        self.app.decimals = 4
        self.app.plotcanvas = Mock()
        self.app.version = 'test'
        self.app.version_date = 'test'
        self.app.proc_container = Mock()
        self.app.proc_container.view = Mock()
        island = Point(0, 7).buffer(1)
        self.parsed.layers[1]['solid_geometry'].append(island)
        self.parsed.layers[1]['solid_geometry'].append(Polygon(
            [(-10, -5), (0, -5), (0, 5), (-10, 5)],
            [[(-8, -2), (-6, -2), (-6, 2), (-8, 2)]]))

        def exported(name, filename, local_use, use_thread):
            local_use.app = self.app
            local_use.export_gerber = lambda *args, **kwargs: GerberObject.export_gerber(local_use, *args, **kwargs)
            return appIO.export_gerber(self.io, name, filename, local_use, use_thread)

        self.io.export_gerber.side_effect = exported
        self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], [])
        self.assertEqual(len(self.objects), 1)
        obj = self.objects[0][1]
        parser = Gerber(self.app)
        parser.parse_lines(obj.source_file.splitlines())
        expected = unary_union(self.parsed.layers[1]['solid_geometry'])
        actual = unary_union(parser.solid_geometry)
        self.assertLess(expected.symmetric_difference(actual).area, 0.01)
        # A later user export must preserve the same geometry, not only source_file.
        later = appIO.export_gerber(self.io, 'Top', None, local_use=obj, use_thread=False)
        parser = Gerber(self.app)
        parser.parse_lines(later.splitlines())
        self.assertLess(expected.symmetric_difference(unary_union(parser.solid_geometry)).area, 0.01)
        obj.solid_geometry = [translate(geo, xoff=-7, yoff=2) for geo in obj.solid_geometry]
        obj.source_file = 'obsolete source cache'
        original_tools = obj.tools
        edited = appIO.export_gerber(self.io, 'Top', None, local_use=obj, use_thread=False)
        parser = Gerber(self.app)
        self.assertNotEqual(parser.parse_lines(edited.splitlines()), 'fail')
        self.assertLess(unary_union(obj.solid_geometry).symmetric_difference(
            unary_union(parser.solid_geometry)).area, 0.01)
        self.assertIs(obj.tools, original_tools)
        self.assertNotIn('obsolete source cache', edited)
        self.assertEqual(obj.source_file, edited)
        self.app.options['gerber_exp_zeros'] = 'T'
        trailing = appIO.export_gerber(self.io, 'Top', None, local_use=obj, use_thread=False)
        parser = Gerber(self.app)
        self.assertNotEqual(parser.parse_lines(trailing.splitlines()), 'fail')
        self.assertLess(unary_union(obj.solid_geometry).symmetric_difference(
            unary_union(parser.solid_geometry)).area, 0.01)

    def test_thin_rings_export_before_larger_nested_islands(self):
        from shapely.geometry import box
        from appObjects.GerberObject import GerberObject
        from appParsers.ParseGerber import Gerber
        from defaults import AppDefaults

        self.app.defaults = AppDefaults()
        self.app.options = dict(self.app.defaults.defaults)
        self.app.options['gerber_exp_units'] = 'MM'
        self.app.decimals = 4
        self.app.plotcanvas = Mock()
        self.app.version = self.app.version_date = 'test'
        self.app.proc_container = Mock()

        def ring(low, high):
            return Polygon(box(low, low, high, high).exterior.coords,
                           [box(low + 0.1, low + 0.1, high - 0.1, high - 0.1).exterior.coords])

        cases = ([ring(0, 10), box(2, 2, 8, 8)],
                 [ring(0, 10), ring(2, 8), box(3, 3, 7, 7)],
                 [ring(0, 10), ring(2, 8), ring(3, 7), box(4, 4, 6, 6)])
        self.assertAlmostEqual(unary_union(cases[0]).area, 39.96)
        for depth, geometries in enumerate(cases, 1):
            self.assertLess(geometries[0].area, geometries[-1].area)
            geometries = [translate(geo, xoff=-5, yoff=-5) for geo in geometries]
            expected = unary_union(geometries)
            obj = SimpleNamespace(app=self.app, units='MM', obj_options={'pcbdoc_import': True},
                                  tools={0: {'type': 'REG', 'size': 0}}, solid_geometry=geometries)
            obj.export_gerber = lambda *args, **kwargs: GerberObject.export_gerber(obj, *args, **kwargs)
            tools = obj.tools
            for zeros in ('L', 'T'):
                with self.subTest(depth=depth, zeros=zeros):
                    self.app.options['gerber_exp_zeros'] = zeros
                    code = appIO.export_gerber(self.io, 'thin_ring', None, local_use=obj, use_thread=False)
                    parser = Gerber(self.app)
                    self.assertNotEqual(parser.parse_lines(code.splitlines()), 'fail')
                    actual = unary_union(parser.solid_geometry)
                    self.assertAlmostEqual(actual.area, expected.area, places=4)
                    self.assertLess(actual.symmetric_difference(expected).area, 0.001)
                    self.assertIs(obj.tools, tools)

    def test_parse_result_is_queued_to_gui_thread_and_parse_errors_are_clean(self):
        self.app.ui = QtWidgets.QWidget()
        parser = Mock()
        parser.return_value.parse_file.return_value = self.parsed
        module = SimpleNamespace(PcbDocParser=parser, PcbDocParseError=ValueError)
        threads = []

        def rejected(dialog):
            threads.append(QtCore.QThread.currentThread())
            return QtWidgets.QDialog.DialogCode.Rejected

        with patch.dict('sys.modules', {'appParsers.ParsePcbDoc': module}), \
                patch.object(QtWidgets.QDialog, 'exec', rejected):
            worker = threading.Thread(target=self.io.parse_pcbdoc, args=('board.PcbDoc',))
            worker.start()
            worker.join()
            self.assertEqual(threads, [])
            self.qapp.processEvents()
            self.assertEqual(threads, [self.qapp.thread()])
            parser.return_value.parse_file.side_effect = ValueError('invalid PCB')
            self.assertEqual(self.io.parse_pcbdoc('bad.PcbDoc'), 'fail')
        self.assertEqual(self.objects, [])
        self.app.worker_task.emit.assert_not_called()

    def test_actual_qt_dialog_selection_and_worker_object_creation(self):
        self.app.ui = QtWidgets.QWidget()
        parser = Mock()
        parser.return_value.parse_file.return_value = self.parsed
        module = SimpleNamespace(PcbDocParser=parser, PcbDocParseError=ValueError)
        gui_threads, creation_threads, parser_threads = [], [], []
        errors = []
        base_create = self.app.app_obj.new_object.side_effect

        def create(*args, **kwargs):
            creation_threads.append(QtCore.QThread.currentThread())
            return base_create(*args, **kwargs)

        def parsed(filename):
            parser_threads.append(QtCore.QThread.currentThread())
            return self.parsed

        parser.return_value.parse_file.side_effect = parsed
        self.app.app_obj.new_object.side_effect = create

        class Task(QtCore.QThread):
            def __init__(task, function, params):
                super().__init__()
                task.function, task.params = function, params

            def run(task):
                try:
                    task.function(*task.params)
                except Exception as error:
                    errors.append(error)

        def choose():
            dialog = self.qapp.activeModalWidget()
            if not isinstance(dialog, QtWidgets.QDialog):
                QtCore.QTimer.singleShot(1, choose)
                return
            gui_threads.append(QtCore.QThread.currentThread())
            for box in dialog.findChildren(QtWidgets.QCheckBox):
                if box.text().startswith('Top'):
                    box.setChecked(False)
            self.app.app_units = 'IN'
            dialog.findChild(QtWidgets.QDialogButtonBox).button(
                QtWidgets.QDialogButtonBox.StandardButton.Ok).click()

        with patch.dict('sys.modules', {'appParsers.ParsePcbDoc': module}):
            task = Task(self.io.parse_pcbdoc, ['board.PcbDoc'])
            task.start()
            self.assertTrue(task.wait(5000))
            QtCore.QTimer.singleShot(0, choose)
            self.qapp.processEvents()
        self.assertEqual(errors, [])
        self.assertEqual(gui_threads, [self.qapp.thread()])
        self.assertNotEqual(parser_threads[0], self.qapp.thread())
        job = self.app.worker_task.emit.call_args.args[0]
        self.assertEqual(job['params'][2:], [[32], ['PTH']])
        render = Task(job['fcn'], job['params'])
        render.start()
        self.assertTrue(render.wait(5000))
        self.assertEqual(errors, [])
        self.assertTrue(all(thread != self.qapp.thread() for thread in creation_threads))
        self.assertEqual([obj.units for kind, obj in self.objects], ['IN', 'IN'])

    def test_real_excellon_export_drills_and_slots_in_current_units(self):
        import appHandlers.appIO as io_module
        from appObjects.ExcellonObject import ExcellonObject
        from appParsers.ParseExcellon import Excellon
        from defaults import AppDefaults
        self.app.defaults = AppDefaults()
        self.app.options = dict(self.app.defaults.defaults)
        self.app.options['excellon_exp_units'] = 'METRIC'
        self.app.options['excellon_exp_format'] = 'dec'
        self.app.decimals = 4
        self.app.plotcanvas = Mock()
        self.app.version = 'test'
        self.app.version_date = 'test'
        self.app.proc_container = Mock()
        self.app.app_units = 'IN'

        class DrillObject(SimpleNamespace):
            def create_geometry(obj):
                return Excellon.create_geometry(obj)

            def circle_steps(obj, radius):
                return obj.excellon_circle_steps

            def export_excellon(obj, *args, **kwargs):
                return ExcellonObject.export_excellon(obj, *args, **kwargs)

        def create(kind, name, initialize, **kwargs):
            obj = DrillObject(app=self.app, obj_options={'name': name},
                              excellon_circle_steps=64, default_data={})
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj))
            return obj

        self.app.app_obj.new_object.side_effect = create
        self.io.export_excellon.side_effect = lambda *args, **kwargs: appIO.export_excellon(self.io, *args, **kwargs)
        with patch.object(io_module, 'ExcellonObject', DrillObject):
            self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [], ['PTH'])
        self.assertEqual(len(self.objects), 1)
        obj = self.objects[0][1]
        self.assertEqual(len(obj.solid_geometry), 2)
        parser = Excellon(self.app)
        self.assertNotEqual(parser.parse_lines(obj.source_file.splitlines()), 'fail')
        tool = parser.tools[1]
        self.assertAlmostEqual(tool['tooldia'], 2.54)
        self.assertAlmostEqual(tool['drills'][0].x, 25.4)
        self.assertAlmostEqual(tool['slots'][0][1].x, 25.4)
        obj.tools[1]['drills'] = [Point(-0.1, 0.2)]
        obj.source_file = 'obsolete drill cache'
        with patch.object(io_module, 'ExcellonObject', DrillObject):
            updated = appIO.export_excellon(self.io, 'PTH', None, local_use=obj, use_thread=False)
        parser = Excellon(self.app)
        self.assertNotEqual(parser.parse_lines(updated.splitlines()), 'fail')
        self.assertAlmostEqual(parser.tools[1]['drills'][0].x, -2.54)
        self.assertEqual(obj.source_file, updated)


if __name__ == '__main__':
    unittest.main()
