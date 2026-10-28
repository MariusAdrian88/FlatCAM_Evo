#!/usr/bin/env python
"""
Tests for the open-file dialog filters. On Linux the Qt file dialog matches the filter patterns case-sensitive,
so each pattern must also be there in upper case (EasyEDA writes e.g. 'board.GTL', 'board.DRL').
Also the Gerber save dialog: each file type needs a '*.ext' pattern and 'All Files (*.*)' must be the last group.
And the name that the save dialog gives: the extension of the chosen filter is added only to a name that has none
of the extensions of that filter, in any case.

Run: python tests/test_file_filters.py
"""

import os
import sys
import unittest
from fnmatch import fnmatchcase
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets

from appGUI.GUIElements import add_case_variants


def patterns_of(ext_filter):
    """Return the patterns of each filter group, keyed by the group name."""
    groups = {}
    for group in ext_filter.split(';;'):
        name, _sep, patterns = group.rpartition('(')
        groups[name.strip()] = patterns.rstrip(')').split()
    return groups


def matches(ext_filter, filename):
    """True if a file type group, not the 'All Files (*.*)' group, shows the file."""
    return any(fnmatchcase(filename, p)
               for group in patterns_of(ext_filter).values() if '*.*' not in group
               for p in group)


class AddCaseVariantsTests(unittest.TestCase):

    def test_adds_upper_case_after_each_pattern(self):
        result = add_case_variants("Gerber Files (*.gbr *.gtl);;All Files (*.*)")
        self.assertEqual("Gerber Files (*.gbr *.GBR *.gtl *.GTL);;All Files (*.*)", result)

class DialogFilterTests(unittest.TestCase):
    """Run the real dialog callbacks with a patched QFileDialog and check the filter they pass to Qt."""

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def captured_filter(self, call):
        stub = SimpleNamespace(log=MagicMock(), inform=MagicMock(), worker_task=MagicMock(),
                               app=MagicMock(), splash=MagicMock())
        stub.app.get_last_folder.return_value = ''
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileNames', return_value=([], '')) as dialog:
            call(stub)
        return dialog.call_args.kwargs['filter']

    def test_gerber_dialog_shows_upper_and_lower_case_files(self):
        from appHandlers.appIO import appIO
        ext_filter = self.captured_filter(lambda stub: appIO.on_file_open_gerber(stub))
        for filename in ('board.gtl', 'board.GTL', 'board.GBR', 'board.gme', 'board.GME', 'board.GKO'):
            self.assertTrue(matches(ext_filter, filename), filename)
        # every group gets the upper case, not only the first one
        self.assertIn('*.ART', patterns_of(ext_filter)['Allegro Files'])

    def test_excellon_gcode_and_hpgl2_dialogs_show_upper_case_files(self):
        from appHandlers.appIO import appIO
        cases = ((appIO.on_file_open_excellon, 'board.DRL'),
                 (appIO.on_file_open_gcode, 'job.NC'),
                 (appIO.on_file_open_hpgl2, 'plot.PLT'))
        for callback, filename in cases:
            with self.subTest(callback=callback.__name__):
                ext_filter = self.captured_filter(lambda stub: callback(stub))
                self.assertTrue(matches(ext_filter, filename))
                self.assertTrue(matches(ext_filter, filename.lower()))

class GerberSaveFilterTests(unittest.TestCase):
    """Run the real Gerber export callback with a patched save dialog and check the filter it passes to Qt."""

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @staticmethod
    def make_stub():
        from appObjects.GerberObject import GerberObject
        from defaults import AppDefaults
        stub = SimpleNamespace(log=MagicMock(), inform=MagicMock(), export_gerber=MagicMock(), app=MagicMock())
        stub.app.options = {}
        stub.app.defaults = dict(AppDefaults.factory_defaults)
        stub.app.get_last_save_folder.return_value = ''
        obj = GerberObject.__new__(GerberObject)
        obj.obj_options = {'name': 'board'}
        obj.app = stub.app
        stub.app.collection.get_active.return_value = obj
        return stub

    @staticmethod
    def export(stub, file_type):
        """The user types 'board' and picks 'file_type'. Return the filter shown and the file name exported."""
        from appHandlers.appIO import appIO

        def save_dialog(**kwargs):
            return 'board', next(g for g in kwargs['filter'].split(';;') if g.startswith(file_type))

        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', side_effect=save_dialog) as dialog:
            appIO.on_file_export_gerber(stub)
        return dialog.call_args.kwargs['filter'], stub.export_gerber.call_args.args[1]

    def test_each_file_type_shows_its_files_and_all_files_is_last(self):
        ext_filter, _filename = self.export(self.make_stub(), 'Gerber File .gbr ')
        groups = ext_filter.split(';;')
        self.assertEqual('All Files (*.*)', groups[-1])
        for group in groups[:-1]:
            # 'Gerber File .gtl (*.gtl)' must show 'board.gtl'
            filename = 'board' + group.split()[2]
            self.assertTrue(matches(group, filename), group)

    def test_the_chosen_file_type_adds_its_extension(self):
        # .art is the last file type; it was joined to 'All Files' and saved 'board' without an extension
        for file_type, filename in (('Gerber File .gtl ', 'board.gtl'), ('Gerber File .art ', 'board.art')):
            self.assertEqual(filename, self.export(self.make_stub(), file_type)[1])


class SaveDialogExtensionTests(unittest.TestCase):
    """FCFileSaveDialog adds the extension of the chosen filter only to a name that has none of its extensions."""

    @staticmethod
    def saved_name(picked, chosen_filter):
        from appGUI.GUIElements import FCFileSaveDialog
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(picked, chosen_filter)):
            return FCFileSaveDialog.get_saved_filename(caption='Export Code ...', ext_filter=chosen_filter)[0]

    def test_a_name_with_an_extension_of_the_filter_in_any_case_is_kept(self):
        # View Source of a CNC job saves with "(*.NC)": over 'board_cnc.nc' it wrote 'board_cnc.nc.NC'
        for picked, chosen_filter in (('C:/w/board_cnc.nc', 'GCode Files .nc (*.NC)'),
                                      ('C:/w/TOP.NC', 'G-Code Files .nc (*.nc)'),
                                      ('C:/w/board.gbr', 'Gerber Files (*.gbr *.GBR)')):
            self.assertEqual(picked, self.saved_name(picked, chosen_filter))

    def test_other_names_get_the_extension_as_before(self):
        for picked, chosen_filter, expected in (('C:/w/board', 'GCode Files .nc (*.NC)', 'C:/w/board.NC'),
                                                ('C:/w/top.gcode', 'G-Code Files .nc (*.nc)', 'C:/w/top.gcode.nc'),
                                                ('C:/w/board.txt', 'All Files (*.*)', 'C:/w/board.txt'),
                                                ('', 'G-Code Files .nc (*.nc)', '')):
            self.assertEqual(expected, self.saved_name(picked, chosen_filter))


if __name__ == '__main__':
    unittest.main(verbosity=2)
