"""Rename keeps the shell auto-completer in sync, also when the old name was never registered."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore

from appCommon.RegisterFileKeywords import RegisterFK
from appObjects.AppObjectTemplate import FlatCAMObj


def make_reg(keywords):
    reg = RegisterFK.__new__(RegisterFK)
    QtCore.QObject.__init__(reg)
    reg.myKeywords = list(keywords)
    reg.object_names = []
    cmd_line = mock.MagicMock()
    reg.shell = mock.MagicMock()
    reg.shell.command_line.return_value = cmd_line
    return reg, cmd_line


class TestRemoveKeyword(unittest.TestCase):
    def test_missing_keyword_does_not_raise(self):
        reg, _ = make_reg(["a", "b"])
        reg.remove_keyword("zzz")
        reg.remove_keyword("zzz", update=False)
        self.assertEqual(reg.myKeywords, ["a", "b"])

    def test_update_true_registers(self):
        reg, _ = make_reg(["a", "b"])
        with mock.patch.object(reg, "register_keywords") as reg_kw:
            reg.remove_keyword("a", update=True)
        reg_kw.assert_called_once_with(["b"])

class TestRenameCompleter(unittest.TestCase):
    def test_rename_with_unregistered_old_name(self):
        reg, cmd_line = make_reg(["other"])
        code_editor = mock.MagicMock()
        stub = SimpleNamespace(
            obj_options={"name": "old"},
            default_data={"name": "old"},
            ui=mock.MagicMock(),
            app=SimpleNamespace(
                regFK=reg,
                ui=SimpleNamespace(code_editor=code_editor),
                collection=mock.MagicMock(),
                inform=mock.MagicMock(),
                log=mock.MagicMock(),
            ),
        )
        FlatCAMObj.on_name_activate(stub, new_name="new")

        expected = ["new", "other"]
        self.assertEqual(reg.myKeywords, expected)
        cmd_line.set_model_data.assert_called_with(expected)
        code_editor.set_model_data.assert_called_once_with(expected)
        self.assertNotIn("old", reg.myKeywords)
        self.assertEqual(stub.obj_options["name"], "new")


class TestKeywordEdit(unittest.TestCase):
    def test_editing_the_keywords_keeps_the_object_names(self):
        reg, cmd_line = make_reg(["cmd", "kw", "tcl"])
        reg.tcl_commands_list, reg.default_keywords, reg.tcl_keywords = ["cmd"], ["kw"], ["tcl"]
        # the keyword list box of Preferences -> Utilities
        kw_list_text = SimpleNamespace(text="kw")
        kw_list_text.get_value = lambda: kw_list_text.text
        kw_list_text.set_value = lambda text: setattr(kw_list_text, "text", text)
        reg.ui = SimpleNamespace(util_pref_form=SimpleNamespace(kw_group=SimpleNamespace(kw_list_text=kw_list_text)))
        reg.prepend_keyword("top_iso")

        reg.add_keyword_in_preferences_gui("foo")
        self.assertEqual(reg.myKeywords, ["top_iso", "cmd", "foo", "kw", "tcl"])
        # a rename after the edit
        reg.remove_keyword("top_iso", update=False)
        reg.prepend_keyword("top_iso2")
        for edit in (lambda: reg.delete_keyword_in_preferences_gui("foo"), lambda: reg.restore_extensions("keyword"),
                     lambda: reg.delete_all_extensions("keyword")):
            edit()
            self.assertEqual(reg.myKeywords[0], "top_iso2")
            self.assertNotIn("top_iso", reg.myKeywords)
            cmd_line.set_model_data.assert_called_with(reg.myKeywords)
        self.assertEqual(reg.myKeywords, ["top_iso2", "cmd", "tcl"])
def named(name):
    return SimpleNamespace(obj_options={"name": name})


class TestRenameKeepsNamesUnique(unittest.TestCase):
    def test_name_entry_and_gcode_export_number_the_name_of_another_object(self):
        reg, _ = make_reg(["top", "top_cnc"])
        stub = SimpleNamespace(
            obj_options={"name": "top_cnc"},
            default_data={"name": "top_cnc"},
            ui=mock.MagicMock(),
            app=SimpleNamespace(regFK=reg, ui=SimpleNamespace(code_editor=mock.MagicMock()),
                                collection=mock.MagicMock(), inform=mock.MagicMock(), log=mock.MagicMock()),
        )
        stub.app.collection.get_list.return_value = [named("top"), stub]
        # the G-code export renames the CNC job to the base name of the file (top.nc)
        FlatCAMObj.on_name_activate(stub, silent=True, new_name="top")
        self.assertEqual(stub.obj_options["name"], "top_1")
        self.assertEqual(reg.myKeywords, ["top_1", "top"])
        FlatCAMObj.on_name_activate(stub, new_name="")
        self.assertEqual(stub.obj_options["name"], "top_1")

    def test_project_tree_and_rename_object_number_the_name_of_another_object(self):
        from appObjects.ObjectCollection import ObjectCollection
        other, renamed = named("top"), named("top_cnc")
        collection = SimpleNamespace(app=mock.MagicMock(), get_list=lambda: [other, renamed],
                                     dataChanged=mock.MagicMock(), update_view=mock.MagicMock())
        index = mock.MagicMock()
        index.internalPointer.return_value.obj = renamed
        ObjectCollection.setData(collection, index, "top")
        self.assertEqual(renamed.obj_options["name"], "top_1")
        renamed.obj_options["name"] = "top_cnc"
        self.assertEqual(ObjectCollection.rename_object(collection, renamed, "top"), "top_1")
        self.assertEqual((other.obj_options["name"], renamed.obj_options["name"]), ("top", "top_1"))


class TestTclReset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6 import QtWidgets
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_tcl_reset_deletes_the_old_shell_and_gives_the_new_one_the_keywords(self):
        from PyQt6 import QtWidgets, sip
        from appMain import App
        dock = QtWidgets.QDockWidget()

        class Shell(QtWidgets.QWidget):
            def __init__(self, app=None, version=None):
                super().__init__()
                self.tcl = object()
                self.line = mock.MagicMock()
                dock.setWidget(self)    # as FCShell does

            def command_line(self):
                return self.line

        class StopAfterShell(Exception):
            pass

        old = Shell()
        reg, _ = make_reg(["drill.drl", "cncjob"])
        reg.shell = old
        app = SimpleNamespace(shell=old, regFK=reg, version="1.0", log=mock.MagicMock(), inform=mock.MagicMock())
        # Open Project in the GUI (on_file_new_project(reset_tcl=True)); the plugins come after the shell
        with mock.patch("appMain.FCShell", Shell), mock.patch("appMain.Distance", side_effect=StopAfterShell):
            with self.assertRaises(StopAfterShell):
                App.install_tools(app, init_tcl=True)
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(old))
        self.assertEqual([child for child in dock.findChildren(QtWidgets.QWidget) if isinstance(child, Shell)],
                         [app.shell])
        self.assertIs(reg.shell, app.shell)
        app.shell.line.set_model_data.assert_called_with(["drill.drl", "cncjob"])


if __name__ == "__main__":
    unittest.main()
