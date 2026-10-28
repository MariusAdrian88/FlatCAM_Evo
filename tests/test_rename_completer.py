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


if __name__ == "__main__":
    unittest.main()
