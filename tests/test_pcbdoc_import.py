import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

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

    @pytest.mark.diptrace
    def test_diptrace_chooser_imports_selected_native_layers_and_holes_in_project_units(self):
        from pathlib import Path
        from appGUI.ImportDialog import ImportDialog

        filename = str(Path(__file__).parent / 'fixtures' / 'diptrace' / 'STM-32_GPSDO_v1.0.dip')
        self.app.ui = QtWidgets.QWidget()
        self.app.get_last_folder = Mock(return_value='')
        self.app.resource_location = 'assets/resources'
        self.app.f_handlers = self.io
        self.app.pdf_tool = self.app.image_tool = None
        dialog = ImportDialog(self.app)
        dialog.select('diptrace')
        self.assertEqual(dialog.ext_label.text(), '.dip')
        dialog.accept()
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(filename, '')):
            dialog.run_selected()
        job = self.app.worker_task.emit.call_args.args[0]
        with patch.object(self.io, 'pcbdoc_parsed') as signal:
            job['fcn'](*job['params'])
        source, parsed = signal.emit.call_args.args
        self.assertEqual(source, filename)

        def choose(selection):
            self.assertEqual(selection.windowTitle(), 'Import DipTrace PCB')
            selected = {'Top (0)', 'Board Outline (Outline)', 'Top Mask (Top Mask)',
                        'Top Paste (Top Paste)', 'Top Silk (Top Silk)', 'PTH', 'NPTH'}
            checkboxes = selection.findChildren(QtWidgets.QCheckBox)
            self.assertTrue(selected.issubset({checkbox.text() for checkbox in checkboxes}))
            for checkbox in checkboxes:
                checkbox.setChecked(checkbox.text() in selected)
            return QtWidgets.QDialog.DialogCode.Accepted

        self.app.app_units = 'IN'
        # Its two routed vias ask the via choice; the preselected Tent needs no reparse.
        with patch.object(QtWidgets.QDialog, 'exec', choose), \
                patch.object(QtWidgets.QInputDialog, 'getItem',
                             side_effect=lambda parent, title, label, items, current, editable:
                             (items[current], True)) as via_prompt:
            self.io.on_pcbdoc_parsed(filename, parsed)
        via_prompt.assert_called_once()
        job = self.app.worker_task.emit.call_args.args[0]
        job['fcn'](*job['params'])
        self.assertEqual([kind for kind, obj in self.objects], ['gerber'] * 5 + ['excellon'] * 2)
        copper, outline, mask, silk, paste, plated, unplated = [obj for kind, obj in self.objects]
        self.assertTrue(copper.obj_options['name'].endswith('_0_Top'))
        self.assertTrue(all(obj.units == 'IN' and obj.source_file for kind, obj in self.objects))
        self.assertAlmostEqual(unary_union(copper.solid_geometry).area,
                               unary_union(parsed.layers[0]['solid_geometry']).area / 25.4 ** 2)
        for obj, layer in ((mask, 'Top Mask'), (silk, 'Top Silk'), (paste, 'Top Paste')):
            self.assertTrue(obj.obj_options['name'].endswith('_%s_%s' % (layer, layer)))
            self.assertAlmostEqual(unary_union(obj.solid_geometry).area,
                                   unary_union(parsed.layers[layer]['solid_geometry']).area / 25.4 ** 2)
        self.assertEqual(sum(len(t['slots']) for t in plated.tools.values()), 3)
        self.assertEqual(sum(len(t['drills']) for t in unplated.tools.values()), 3)
        self.assertAlmostEqual(next(t['tooldia'] for t in plated.tools.values() if t['slots']), 1 / 25.4)
        self.qapp.processEvents()
        self.app.file_opened.emit.assert_called_once_with('diptrace', filename)

    @pytest.mark.diptrace
    def test_legacy_diptrace_source_export_values_create_missing_mask_and_paste(self):
        from pathlib import Path
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.geometry import box

        filename = str(Path(__file__).parent / 'fixtures' / 'diptrace' / 'DAP42v2.dip')
        self.app.ui = QtWidgets.QWidget()
        parsed = DipTraceParser().parse_file(filename)
        values = iter((0.1, -0.05))
        # Respect the real dialog's bounds so a clamped negative value changes
        # the imported paste geometry instead of passing through a permissive mock.
        with patch.object(QtWidgets.QInputDialog, 'getDouble',
                          side_effect=lambda parent, title, label, value, lower, upper, decimals:
                          (max(lower, min(upper, next(values))), True)), \
                patch.object(QtWidgets.QInputDialog, 'getItem',
                             side_effect=lambda parent, title, label, items, current, editable:
                             (items[1 - current], True)), \
                patch.object(QtWidgets.QDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Rejected):
            self.io.on_pcbdoc_parsed(filename, parsed)
        self.assertTrue(self.app.worker_task.emit.called)
        job = self.app.worker_task.emit.call_args.args[0]
        with patch.object(self.io, 'pcbdoc_parsed') as signal:
            job['fcn'](*job['params'])
        source, resolved = signal.emit.call_args.args
        self.assertEqual(source, filename)
        region = box(16.4, 15.1, 17.9, 16.6)
        for layer, offset in (('Top Mask', 0.1), ('Top Paste', 0.05)):
            expected = box(16.645 - offset, 15.325 - offset, 17.645 + offset, 16.425 + offset)
            self.assertLess(unary_union(resolved.layers[layer]['solid_geometry']).intersection(region)
                            .symmetric_difference(expected).area, 1e-8)
        # A resolved reparse must reach layer selection without another prompt.
        with patch.object(QtWidgets.QInputDialog, 'getDouble', side_effect=AssertionError('Repeated settings prompt')), \
                patch.object(QtWidgets.QInputDialog, 'getItem', side_effect=AssertionError('Repeated via prompt')), \
                patch.object(QtWidgets.QDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Rejected):
            self.io.on_pcbdoc_parsed(filename, resolved)
        # Declining unknown source settings must not invent openings or requeue
        # the same incomplete board indefinitely.
        self.app.worker_task.emit.reset_mock()
        with patch.object(QtWidgets.QInputDialog, 'getDouble', return_value=(0, False)), \
                patch.object(QtWidgets.QInputDialog, 'getItem',
                             side_effect=lambda parent, title, label, items, current, editable:
                             (items[current], False)), \
                patch.object(QtWidgets.QDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Rejected):
            self.io.on_pcbdoc_parsed(filename, parsed)
        self.app.worker_task.emit.assert_not_called()
        self.assertNotIn('Top Mask', parsed.layers)
        self.assertEqual(self.objects, [])

        # With explicit pad swells only the tented routed vias inherit the swell:
        # choosing Open must still ask it, then open them on both sides.
        inherit = (1000000000 - 10000000).to_bytes(4, 'big')
        data = Path(filename).read_bytes().replace(inherit * 2, (1000000000 + 3000).to_bytes(4, 'big') + inherit)
        with tempfile.TemporaryDirectory() as directory:
            board = os.path.join(directory, 'DAP42v2.dip')
            Path(board).write_bytes(data)
            with patch.object(QtWidgets.QInputDialog, 'getDouble', return_value=(0.1, True)), \
                    patch.object(QtWidgets.QInputDialog, 'getItem',
                                 side_effect=lambda parent, title, label, items, current, editable:
                                 (items[1 - current], True)), \
                    patch.object(QtWidgets.QDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Rejected):
                self.io.on_pcbdoc_parsed(board, DipTraceParser().parse_file(board))
                job = self.app.worker_task.emit.call_args.args[0]
                with patch.object(self.io, 'pcbdoc_parsed') as signal:
                    job['fcn'](*job['params'])
        resolved = signal.emit.call_args.args[1]
        for side in ('Top Mask', 'Bottom Mask'):
            mask = unary_union(resolved.layers[side]['solid_geometry'])
            for via in ((-2.54, 13.6525), (-10.59, 10.16)):
                self.assertLess(Point(via).buffer(0.6).difference(mask).area, 1e-6)

    def test_diptrace_corrupt_input_reports_failure_without_publishing_a_board(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = os.path.join(directory, 'bad.dip')
            with open(filename, 'wb') as handle:
                handle.write(b'\x07DTBOARD')
            with patch.object(self.io, 'pcbdoc_parsed') as signal:
                self.assertEqual(self.io.parse_diptrace(filename), 'fail')
            signal.emit.assert_not_called()
        self.assertIn('[ERROR_NOTCL]', self.app.inform.emit.call_args.args[0])
        self.assertEqual(self.objects, [])

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

    def _import_proteus_projects(self, names=('Main', 'Power'), members=None, expected=None):
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
            return QtWidgets.QDialog.DialogCode.Accepted

        with patch.object(QtWidgets.QDialog, 'exec', accept):
            io.on_pcbdoc_parsed('design.pdsprj', parsed)
            for index in range(2):
                io._advance_proteus_import()
                self.assertEqual(len(projects.contexts), index + 2)
                context = projects.active
                self.assertEqual(context.name, expected[index])
                job = worker_jobs.pop(0)
                thread = threading.Thread(target=job['fcn'], args=job['params'])
                thread.start()
                thread.join()
                io._advance_proteus_import()
                self.assertIs(projects.active, context)
                self.qapp.processEvents()
                self.qapp.processEvents()
                io._advance_proteus_import()
                self.assertIs(projects.active, context)
                self.assertEqual(len(context.collection.objects), 3)
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
            io._advance_proteus_import()
        self.assertEqual(original.collection.objects, ['existing'])
        self.assertEqual([ctx.name for ctx in projects.contexts[1:]],
                         list(expected))
        self.assertTrue(all(ctx.filename is None for ctx in projects.contexts[1:]))
        self.assertTrue(all(obj.obj_options['name'].startswith(board.name)
                            for board, ctx in zip(boards, projects.contexts[1:])
                            for obj in ctx.collection.objects))
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
