"""Tests for the multiple projects feature: ProjectManager and the handlers that read app.collection."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import QtWidgets


class FakeCollection:
    def __init__(self, objects=None):
        self.view = QtWidgets.QWidget()
        self.objects = list(objects or [])

    def get_list(self):
        return list(self.objects)

    def has_promises(self):
        return False

    def has_plot_promises(self):
        return False


def make_app(collection=None):
    from defaults import AppOptions
    # a merged AppOptions, as in the App: it stores the project keys and sends the other keys to "defaults"
    defaults = {'units': 'MM', 'global_gridx': 1.0}
    return SimpleNamespace(
        collection=collection or FakeCollection(), defaults=defaults,
        options=AppOptions(version='test', baseline=defaults, defaults=defaults), project_filename=None,
        should_we_save=False, all_objects_list=[], inform=Mock(), log=Mock(),
        ui=SimpleNamespace(set_ui_title=Mock()),
        proc_container=SimpleNamespace(get_procs_count=Mock(return_value=0)),
        workers=SimpleNamespace(pending_count=Mock(return_value=0)), call_source='app',
        save_in_progress=False, app_obj=SimpleNamespace(load_batches=set()), plotcanvas=None)


def make_manager(app=None):
    from appHandlers.appProjects import ProjectManager
    app = app or make_app()
    manager = ProjectManager(app, collection_factory=FakeCollection)
    manager.before_switch = Mock()
    manager.deactivate = Mock()
    manager.activate = Mock()
    manager.reset_options = Mock()
    return app, manager


class TestProjectManagerCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_running_script_keeps_its_project_active(self):
        app, manager = make_manager()
        first = manager.active
        second = manager.add_context('Board_B')
        app.script_state = 'running'
        self.assertFalse(manager.switch(second))
        self.assertIs(manager.active, first)
        self.assertIs(app.collection, first.collection)
        app.script_state = 'completed'
        self.assertTrue(manager.switch(second))

    def test_switch_swaps_the_app_state(self):
        app, manager = make_manager()
        first = manager.active
        second = manager.add_context('Board_B')
        second.options = {'units': 'IN'}
        second.filename = 'b.FlatPrj'
        second.should_we_save = True
        app.project_filename = 'a.FlatPrj'
        app.should_we_save = False

        self.assertTrue(manager.switch(second))

        self.assertIs(manager.active, second)
        self.assertIs(app.collection, second.collection)
        self.assertEqual(app.options['units'], 'IN')
        self.assertEqual(app.project_filename, 'b.FlatPrj')
        self.assertTrue(app.should_we_save)
        self.assertEqual(app.all_objects_list, [])
        self.assertIs(manager.stack.currentWidget(), second.collection.view)
        # the old state is written back to the old context; only the project keys are kept
        self.assertEqual(first.options, {'units': 'MM'})
        self.assertEqual(first.filename, 'a.FlatPrj')
        self.assertFalse(first.should_we_save)
        manager.deactivate.assert_called_once_with(first)
        manager.activate.assert_called_once_with(second)
        app.ui.set_ui_title.assert_called_with(name='Board_B')

    def test_switch_is_refused_while_busy(self):
        app, manager = make_manager()
        second = manager.add_context('Board_B')
        app.proc_container.get_procs_count.return_value = 1
        self.assertFalse(manager.switch(second))
        self.assertIsNot(manager.active, second)
        app.inform.emit.assert_called()

    def test_switch_is_refused_while_an_editor_is_open(self):
        app, manager = make_manager()
        second = manager.add_context('Board_B')
        app.call_source = 'geo_editor'
        self.assertTrue(manager.is_busy())
        self.assertFalse(manager.switch(second))
        self.assertIsNot(manager.active, second)
        self.assertIn('Close the editor first', app.inform.emit.call_args[0][0])


class TestProjectManagerLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_new_adds_a_unique_project_and_activates_it(self):
        app, manager = make_manager()
        ctx = manager.new()
        self.assertEqual(manager.names(), ['Project_1', 'Project_2'])
        self.assertIs(manager.active, ctx)
        self.assertIs(app.collection, ctx.collection)

    def test_new_is_refused_while_busy(self):
        app, manager = make_manager()
        app.save_in_progress = True
        self.assertIsNone(manager.new())
        self.assertEqual(manager.names(), ['Project_1'])

    def test_rename(self):
        app, manager = make_manager()
        manager.new()
        self.assertTrue(manager.rename(manager.active, ' Board_B '))
        self.assertEqual(manager.active.name, 'Board_B')
        app.ui.set_ui_title.assert_called_with(name='Board_B')

    def test_rename_refuses_a_used_name(self):
        app, manager = make_manager()
        manager.new()
        self.assertFalse(manager.rename(manager.active, 'Project_1'))
        self.assertEqual(manager.active.name, 'Project_2')

    def test_is_dirty_needs_changes_and_objects(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        self.assertFalse(manager.is_dirty(manager.active))
        app.should_we_save = True
        self.assertTrue(manager.is_dirty(manager.active))
        self.assertEqual(manager.dirty_contexts(), [manager.active])

    def test_saved_project_with_all_objects_deleted_is_dirty(self):
        # the deletion of all the objects of a saved project is a change that must be saved
        app, manager = make_manager()
        app.should_we_save = True
        self.assertFalse(manager.is_dirty(manager.active))
        app.project_filename = os.path.join('folder', 'Project_1.FlatPrj')
        self.assertTrue(manager.is_dirty(manager.active))
        # so the GUI close asks and the Tcl close needs -force
        self.assertFalse(manager.close(manager.active))
        self.assertTrue(manager.close(manager.active, force=True))

    def test_close_active_project_activates_the_previous_one(self):
        app, manager = make_manager()
        first = manager.active
        second = manager.new()
        self.assertTrue(manager.close(second))
        self.assertIs(manager.active, first)
        self.assertEqual(manager.names(), ['Project_1'])

    def test_close_last_project_leaves_one_empty_project(self):
        app, manager = make_manager()
        first = manager.active
        self.assertTrue(manager.close(first))
        self.assertEqual(len(manager.contexts), 1)
        self.assertIsNot(manager.active, first)
        self.assertEqual(manager.active.name, 'Project_2')

    def test_close_refuses_a_dirty_project_without_force(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        app.should_we_save = True
        self.assertFalse(manager.close(manager.active))
        self.assertTrue(manager.close(manager.active, force=True))


class TestProjectManagerDialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, name):
        return os.path.join(self.directory.name, name)

    def test_prepare_open_of_an_open_file_yes_reloads(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        first = manager.active
        app.project_filename = self.path('Board.FlatPrj')
        manager.new()
        manager.ask_yes_no = Mock(return_value=True)
        self.assertTrue(manager.prepare_open(self.path('Board.FlatPrj')))
        self.assertIs(manager.active, first)
        manager.ask_yes_no.assert_called_once()

    def test_prepare_open_of_an_open_file_no_only_switches(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        first = manager.active
        app.project_filename = self.path('Board.FlatPrj')
        manager.new()
        manager.ask_yes_no = Mock(return_value=False)
        self.assertFalse(manager.prepare_open(self.path('Board.FlatPrj')))
        self.assertIs(manager.active, first)

    def test_prepare_open_is_refused_while_busy(self):
        app, manager = make_manager()
        app.save_in_progress = True
        self.assertFalse(manager.prepare_open(self.path('Board.FlatPrj')))

    def test_request_close_yes_saves_first(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        first = manager.active
        app.should_we_save = True
        manager.ask_save = Mock(return_value='yes')
        manager.save_context = Mock(return_value=True)
        self.assertTrue(manager.request_close(first))
        manager.save_context.assert_called_once_with(first)

    def test_request_close_all_stops_on_cancel(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        first = manager.active
        app.should_we_save = True
        second = manager.new()
        manager.ask_save = Mock(return_value='cancel')
        self.assertFalse(manager.request_close_all())
        self.assertEqual(manager.contexts, [first, second])

    def test_save_context_writes_the_target_file(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        app.project_filename = self.path('Project_1.FlatPrj')
        app.should_we_save = True
        app.f_handlers = SimpleNamespace(save_project_checked=Mock(return_value=True))
        manager.rename(manager.active, 'Board_B')

        self.assertTrue(manager.save_context(manager.active))

        target = self.path('Board_B.FlatPrj')
        app.f_handlers.save_project_checked.assert_called_once_with(target, silent=True)
        self.assertEqual(app.project_filename, target)
        self.assertFalse(app.should_we_save)
        self.assertFalse(app.save_in_progress)

    def test_save_context_without_file_asks_for_a_path(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        app.f_handlers = SimpleNamespace(save_project_checked=Mock(return_value=True))
        manager.ask_save_path = Mock(return_value=self.path('Chosen.FlatPrj'))

        self.assertTrue(manager.save_context(manager.active))

        app.f_handlers.save_project_checked.assert_called_once_with(self.path('Chosen.FlatPrj'), silent=True)
        self.assertEqual(manager.active.name, 'Chosen')

    def test_save_context_failure_keeps_the_project_dirty(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        app.project_filename = self.path('Project_1.FlatPrj')
        app.should_we_save = True
        app.f_handlers = SimpleNamespace(save_project_checked=Mock(return_value=False))
        manager.rename(manager.active, 'Board_B')

        self.assertFalse(manager.save_context(manager.active))

        self.assertTrue(app.should_we_save)
        self.assertEqual(app.project_filename, self.path('Project_1.FlatPrj'))
        self.assertFalse(app.save_in_progress)

    def test_request_close_stops_when_the_save_fails(self):
        app, manager = make_manager(make_app(FakeCollection([object()])))
        first = manager.active
        app.project_filename = self.path('Project_1.FlatPrj')
        app.should_we_save = True
        app.f_handlers = SimpleNamespace(save_project_checked=Mock(return_value=False))
        manager.ask_save = Mock(return_value='yes')

        self.assertFalse(manager.request_close(first))

        self.assertIn(first, manager.contexts)
        self.assertTrue(app.should_we_save)

    def test_save_context_of_the_active_project_waits_for_a_pending_save(self):
        # switch() returns at once for the active project, so save_context() must check the pending save itself
        app, manager = make_manager(make_app(FakeCollection([object()])))
        app.project_filename = self.path('Project_1.FlatPrj')
        app.should_we_save = True
        app.project_save_pending = True
        app.f_handlers = SimpleNamespace(save_project_checked=Mock(return_value=True))

        self.assertFalse(manager.save_context(manager.active))

        app.f_handlers.save_project_checked.assert_not_called()
        self.assertTrue(app.should_we_save)
        app.inform.emit.assert_called_once()

class TestExitWithProjects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # keep a reference: a QApplication without one is collected and the next QWidget aborts the process
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_lifecycle(self, dirty):
        from appHandlers.appLifecycle import AppLifecycle
        start = SimpleNamespace(name='Board_A')
        projects = SimpleNamespace(active=start, dirty_contexts=Mock(return_value=dirty),
                                   ask_save=Mock(), save_context=Mock(return_value=True),
                                   switch=Mock(return_value=True), return_to=Mock())
        app = SimpleNamespace(save_in_progress=False, projects=projects, trayIcon=Mock(),
                              inform=Mock(), log=Mock(), should_we_save=False)
        lifecycle = AppLifecycle.__new__(AppLifecycle)
        lifecycle.app = app
        lifecycle.inform = app.inform
        lifecycle.quit_application = Mock(return_value=True)
        return lifecycle, projects, start

    def test_each_dirty_project_is_asked(self):
        first, second = SimpleNamespace(name='A'), SimpleNamespace(name='B')
        lifecycle, projects, start = self.make_lifecycle([first, second])
        projects.ask_save.side_effect = ['yes', 'no']
        lifecycle.final_save()
        self.assertEqual(projects.ask_save.call_count, 2)
        projects.save_context.assert_called_once_with(first, visuals=False)
        lifecycle.quit_application.assert_called_once_with()

    def test_cancel_stops_the_exit_and_restores_the_active_project(self):
        first = SimpleNamespace(name='A')
        lifecycle, projects, start = self.make_lifecycle([first])
        projects.ask_save.return_value = 'cancel'
        lifecycle.final_save()
        lifecycle.quit_application.assert_not_called()
        projects.return_to.assert_called_once_with(start)

    def test_failed_save_stops_the_exit(self):
        first = SimpleNamespace(name='A')
        lifecycle, projects, start = self.make_lifecycle([first])
        projects.ask_save.return_value = 'yes'
        projects.save_context.return_value = False
        lifecycle.final_save()
        lifecycle.quit_application.assert_not_called()

    def test_exit_waits_for_the_completion_of_a_save(self):
        # the worker of a failed save cleared save_in_progress, but the GUI did not restore the dirty flag yet
        lifecycle, projects, start = self.make_lifecycle([])
        lifecycle.app.project_save_pending = True
        lifecycle.final_save()
        lifecycle.quit_application.assert_not_called()
        projects.dirty_contexts.assert_not_called()
        lifecycle.inform.emit.assert_called_once()

    def test_refused_quit_after_the_save_of_an_inactive_project_returns_to_the_start_project(self):
        from types import MethodType
        from appHandlers.appLifecycle import AppLifecycle
        # the real ProjectManager: Board_A is active and on the canvas, Board_B is not active and has changes
        app, manager = make_manager()
        first = manager.active
        second = manager.add_context('Board_B')
        second.filename = 'Board_B.FlatPrj'
        second.should_we_save = True
        app.projects = manager
        app.trayIcon = Mock()
        app.f_handlers = SimpleNamespace(save_project_checked=Mock(return_value=True))
        manager.ask_save = Mock(return_value='yes')
        lifecycle = AppLifecycle.__new__(AppLifecycle)
        lifecycle.app = app
        lifecycle.inform = app.inform
        # the real quit_application(); only the answer of the Tools Database question is mocked
        lifecycle.quit_application = MethodType(AppLifecycle.quit_application, lifecycle)
        lifecycle._confirm_tools_database = Mock(return_value=False)

        lifecycle.final_save()

        # Board_B is saved, then the quit is refused
        app.f_handlers.save_project_checked.assert_called_once_with('Board_B.FlatPrj', silent=True)
        self.assertFalse(second.should_we_save)
        # the live state is Board_A again, the same project that the canvas shows
        self.assertIs(manager.active, first)
        self.assertIs(app.collection, first.collection)
        self.assertIs(manager.stack.currentWidget(), first.collection.view)
        self.assertIs(manager.displayed, first)
        app.trayIcon.hide.assert_not_called()


class TestProjectRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_switch_retains_prepared_shapes_and_close_releases_them(self):
        from appHandlers.appProjects import ProjectManager
        from appHandlers.appPlotManager import AppPlotManager
        from appObjects.AppObjectTemplate import FlatCAMObj
        from appGUI.VisPyVisuals import ShapeCollectionVisual, ShapeGroup
        from shapely.geometry import Polygon
        from vispy.gloo.context import FakeCanvas
        from contextlib import nullcontext
        from unittest.mock import MagicMock

        canvas = FakeCanvas()
        for caching, disable in ((False, False), (True, False), (True, True)):
            with self.subTest(caching=caching, disable=disable):
                app, manager = make_manager()
                app.defaults['global_project_caching'] = caching
                app.setup_default_properties_tab = Mock()
                app.preferencesUiManager = SimpleNamespace(defaults_write_form=Mock())
                app.init_tools = Mock()
                app.plotcanvas = SimpleNamespace(redraw=Mock())
                app.proc_container.new = lambda *args: nullcontext()
                app.clear_pool = Mock()
                app.inform = MagicMock()
                app.worker_task = SimpleNamespace(emit=lambda task: task['fcn'](*task['params']))
                app.plot_manager = AppPlotManager(app)
                app.plot_all = app.plot_manager.plot_all
                manager.deactivate = ProjectManager.deactivate.__get__(manager)
                manager.activate = ProjectManager.activate.__get__(manager)
                shapes = ShapeCollectionVisual(pool=None, fcoptions={'global_graphic_engine_3d_no_mp': True})
                objects = []
                for visible in (True, False):
                    obj = SimpleNamespace(kind='geometry', obj_options={'plot': visible},
                                          shapes=ShapeGroup(shapes), _full_plot_sig=None)
                    for name in ('_shapes_signature', '_has_full_plot', 'mark_full_plot', 'show_plotted_shapes'):
                        setattr(obj, name, getattr(FlatCAMObj, name).__get__(obj))
                    obj.clear = lambda update=False, obj=obj: obj.shapes.clear(update=update)
                    def plot(obj=obj):
                        obj.clear()
                        obj.shapes.add(shape=Polygon([(0, 0), (1, 0), (0, 1)]), color='red',
                                       face_color='red', visible=obj.obj_options['plot'])
                        obj.mark_full_plot()
                    obj.plot = plot
                    obj.plot()
                    objects.append(obj)
                first = manager.active
                first.collection.objects = objects
                keys = list(objects[0].shapes._indexes)
                vertices = shapes.data[keys[0]]['mesh_vertices']
                second = manager.add_context('Other')
                self.assertTrue(manager.switch(second))
                app.defaults['global_stats'] = {'switch_project': 1}
                if caching:
                    self.assertIs(shapes.data[keys[0]]['mesh_vertices'], vertices)
                    self.assertFalse(shapes.data[keys[0]]['visible'])
                else:
                    self.assertFalse(shapes.data)
                self.assertTrue(manager.switch(first))
                if caching:
                    self.assertEqual(objects[0].shapes._indexes, keys)
                    self.assertIs(shapes.data[keys[0]]['mesh_vertices'], vertices)
                self.assertTrue(all(shapes.data[k]['visible'] for k in objects[0].shapes._indexes))
                self.assertTrue(all(not shapes.data[k]['visible'] for k in objects[1].shapes._indexes))
                if caching:
                    manager.switch(second)
                    app.defaults['geometry_plot_line'] = '#123456FF'
                    manager.switch(first)
                    self.assertNotEqual(objects[0].shapes._indexes, keys)
                    # A geometry edit/partial plot clears or replaces the old full plot's shapes.
                    manager.switch(second)
                    objects[0].clear()
                    manager.switch(first)
                    self.assertTrue(objects[0].shapes._indexes)
                    manager.switch(second)
                if disable:
                    app.defaults['global_project_caching'] = False
                    third = manager.add_context('Third')
                    manager.switch(third)
                    self.assertFalse(shapes.data)
                self.assertTrue(manager.close(first, force=True))
                self.assertFalse(shapes.data)


if __name__ == '__main__':
    unittest.main()
