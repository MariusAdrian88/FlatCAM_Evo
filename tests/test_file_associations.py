"""File associations: what RegisterFK writes to the Windows registry (winreg is faked, the real registry is never touched)."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import builtins
import plistlib
import tempfile
import xml.etree.ElementTree as ET
from PyQt6 import QtCore

import appCommon.RegisterFileKeywords as rfk
from appCommon.RegisterFileKeywords import RegisterFK
from defaults import AppDefaults

if not hasattr(builtins, "_"):
    builtins._ = lambda s: s


class FakeWinreg:
    HKEY_CLASSES_ROOT = "HKCR"
    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_WRITE = 2
    REG_SZ = 1

    def __init__(self):
        self.keys = {}

    def CreateKey(self, root, path):
        self.keys.setdefault((root, path.lower()), None)

    def OpenKey(self, root, path, reserved=0, access=0):
        if (root, path.lower()) not in self.keys:
            raise FileNotFoundError(path)
        return mock.MagicMock(__enter__=lambda s: (root, path.lower()), __exit__=lambda s, *a: False)

    def SetValueEx(self, key, name, reserved, typ, value):
        self.keys[key] = value

    def QueryValueEx(self, key, name):
        if self.keys.get(key) is None:
            raise FileNotFoundError(name)
        return self.keys[key], 1

    def DeleteKey(self, root, path):
        if (root, path.lower()) not in self.keys:
            raise FileNotFoundError(path)
        del self.keys[(root, path.lower())]


class ListText:
    """The text box of an association list in Preferences -> Utilities."""

    def __init__(self, text=""):
        self.text = text

    def get_value(self):
        return self.text

    def set_value(self, text):
        self.text = text


def make_reg():
    reg = RegisterFK.__new__(RegisterFK)
    QtCore.QObject.__init__(reg)
    reg.log = mock.MagicMock()
    reg.inform = mock.MagicMock()
    reg.prj_list = ['flatprj']
    reg.conf_list = ['flatconfig']
    reg.options = {"fa_excellon": "drl", "fa_gcode": "nc", "fa_gerber": "gbr"}
    form = SimpleNamespace(
        fa_excellon_group=SimpleNamespace(exc_list_text=mock.MagicMock(get_value=lambda: "drl")),
        fa_gcode_group=SimpleNamespace(gco_list_text=mock.MagicMock(get_value=lambda: "nc")),
        fa_gerber_group=SimpleNamespace(grb_list_text=mock.MagicMock(get_value=lambda: "gbr")))
    reg.ui = SimpleNamespace(util_pref_form=form)
    return reg


def first_start():
    """RegisterFK as App makes it at the first start: before defaults_write_form() fills the Preferences lists."""
    ui = mock.MagicMock()
    ui.util_pref_form.fa_excellon_group.exc_list_text = ListText()
    ui.util_pref_form.fa_gcode_group.gco_list_text = ListText()
    ui.util_pref_form.fa_gerber_group.grb_list_text = ListText()
    options = {"first_run": True, "util_autocomplete_keywords": "a, b",
               "fa_excellon": "drl", "fa_gcode": "nc", "fa_gerber": "gbr, gtl"}
    return RegisterFK(ui=ui, inform_sig=mock.MagicMock(), options_dict=options, shell=mock.MagicMock(),
                      log=mock.MagicMock(), keywords=rfk.KeyWords(), extensions=rfk.Extensions())


class TestFileAssociations(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(rfk.sys, "platform", "win32")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fake = FakeWinreg()
        patcher = mock.patch.object(rfk, "winreg", self.fake, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(RegisterFK, "_root_path", staticmethod(lambda: "HKCU"))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(RegisterFK, "notify_assoc_changed")
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)
        self.reg = make_reg()

    def val(self, path):
        return self.fake.keys.get(("HKCU", path.lower()))

    def test_project_double_click_has_a_command(self):
        self.reg.on_register_files('project')
        self.assertEqual(self.val("Software\\Classes\\.flatprj"), "FlatCAM.Project")
        command = self.val("Software\\Classes\\FlatCAM.Project\\shell\\open\\command")
        self.assertTrue(command.endswith('"%1"'))
        self.assertIn("flatcam.py", command)
        self.assertTrue(self.val("Software\\Classes\\FlatCAM.Project\\DefaultIcon").endswith(".ico,0"))
        self.notify.assert_called()

    def test_unregister_removes_what_was_written(self):
        self.reg.on_register_files()
        self.reg.on_unregister_files()
        self.assertEqual(self.fake.keys, {})

    def test_unregister_keeps_foreign_association(self):
        self.reg.on_register_files('project')
        self.fake.keys[("HKCU", "software\\classes\\.flatprj")] = "OtherApp.File"
        self.reg.on_unregister_files('project')
        self.assertEqual(self.val("Software\\Classes\\.flatprj"), "OtherApp.File")
        self.assertIsNone(self.val("Software\\Classes\\FlatCAM.Project\\shell\\open\\command"))

    def test_unregister_keeps_foreign_progid(self):
        self.fake.keys[("HKCU", "software\\classes\\flatcam.project")] = "X"
        self.fake.keys[("HKCU", "software\\classes\\flatcam.project\\shell\\open\\command")] = '"C:\\other.exe" "%1"'
        self.reg.on_unregister_files('project')
        self.assertIsNotNone(self.val("Software\\Classes\\FlatCAM.Project\\shell\\open\\command"))

    def test_first_run_registers_the_lists_of_the_options(self):
        first_start()
        for ext in ("drl", "nc", "gbr", "gtl"):
            self.assertEqual(self.val("Software\\Classes\\.%s" % ext), "FlatCAM")
        self.assertIsNotNone(self.val("Software\\Classes\\FlatCAM\\shell\\open\\command"))

    def test_apply_unregisters_only_what_points_to_flatcam(self):
        # .nc and .gbr are in the saved lists, no longer in the Preferences lists; .nc belongs to another program now
        self.fake.keys[("HKCU", "software\\classes\\.nc")] = "OtherApp.File"
        self.fake.keys[("HKCU", "software\\classes\\.gbr")] = "FlatCAM"
        self.reg.ui.util_pref_form.fa_gcode_group.gco_list_text = ListText()
        self.reg.ui.util_pref_form.fa_gerber_group.grb_list_text = ListText()
        self.reg.on_register_files()
        self.assertEqual(self.val("Software\\Classes\\.nc"), "OtherApp.File")
        self.assertIsNone(self.val("Software\\Classes\\.gbr"))
        self.assertEqual(self.val("Software\\Classes\\.drl"), "FlatCAM")

    def test_first_run_leaves_the_extensions_of_other_programs(self):
        # HKEY_CLASSES_ROOT: the merged view of HKCU and HKLM; .gbr was registered by an older FlatCAM
        self.fake.keys[("HKCR", ".nc")] = "OtherApp.File"
        self.fake.keys[("HKCR", ".gbr")] = "FlatCAM"
        reg = first_start()
        self.assertIsNone(self.val("Software\\Classes\\.nc"))
        for ext in ("drl", "gbr", "gtl"):
            self.assertEqual(self.val("Software\\Classes\\.%s" % ext), "FlatCAM")
        # Apply is the user's choice: it takes the extension
        reg.on_register_files("gcode")
        self.assertEqual(self.val("Software\\Classes\\.nc"), "FlatCAM")

    def test_c_header_and_preprocessed_c_are_not_associated_by_default(self):
        default = AppDefaults.factory_defaults["fa_gcode"].replace(" ", "").split(",")
        self.reg.gcode_list = rfk.Extensions.gcode_list
        self.reg.ui.util_pref_form.fa_gcode_group.gco_list_text = ListText()
        self.reg.restore_extensions("gcode")
        restored = self.reg.ui.util_pref_form.fa_gcode_group.gco_list_text.get_value().replace(" ", "").split(",")
        for exts in (default, restored):
            self.assertNotIn("h", exts)
            self.assertNotIn("i", exts)
            self.assertIn("tap", exts)
        # a .h or .i G-code file still opens (drop on the plot area, file dialog)
        self.assertIn("h", rfk.Extensions.gcode_list)


class FakeHome(unittest.TestCase):
    """A fake home folder and fake system tools: nothing outside the temp folder is touched or run."""

    platform = None

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        patcher = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("XDG_DATA_HOME", None)
        patcher = mock.patch.object(rfk.sys, "platform", self.platform)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tools = {"update-mime-database", "update-desktop-database", "xdg-mime", "duti"}
        self.calls = []
        patcher = mock.patch.object(rfk.shutil, "which",
                                    lambda n: n if n in self.tools or n.startswith("/") else None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(rfk.subprocess, "run", lambda cmd, **kw: self.calls.append(cmd))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.reg = make_reg()

    def called(self, *cmd):
        return list(cmd) in self.calls


class TestLinuxAssociations(FakeHome):
    platform = "linux"

    def paths(self):
        data = os.path.join(self.home, ".local", "share")
        return (os.path.join(data, "mime", "packages", "flatcam-evo.xml"),
                os.path.join(data, "applications", "flatcam-evo.desktop"))

    def test_register_all_writes_mime_and_desktop(self):
        self.reg.on_register_files()
        xml_path, desktop_path = self.paths()
        root = ET.parse(xml_path).getroot()
        types = {n.get("type"): [g for g in n if g.tag.endswith("glob")] for n in root}
        self.assertEqual([g.get("pattern") for g in types["application/x-flatcam-project"]], ["*.flatprj"])
        self.assertEqual([g.get("pattern") for g in types["application/x-flatcam-gerber"]], ["*.gbr"])
        self.assertEqual(types["application/x-flatcam-gerber"][0].get("case-sensitive"), "false")
        with open(desktop_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("flatcam.py", text)
        self.assertRegex(text, r"Exec=.* %F\n")
        self.assertIn("application/x-flatcam-config;", text)
        icon = next(line[5:] for line in text.splitlines() if line.startswith("Icon="))
        self.assertTrue(os.path.isabs(icon) and os.path.isfile(icon))
        data = os.path.join(self.home, ".local", "share")
        self.assertTrue(self.called("update-mime-database", os.path.join(data, "mime")))
        self.assertTrue(self.called("update-desktop-database", os.path.join(data, "applications")))
        self.assertTrue(self.called("xdg-mime", "default", "flatcam-evo.desktop", "application/x-flatcam-gerber"))

    def test_exec_is_quoted_for_paths_with_spaces(self):
        argv = staticmethod(lambda: ["/opt/my app/python", "/a b/flatcam.py"])
        with mock.patch.object(RegisterFK, "launcher_argv", argv):
            self.reg.on_register_files("project")
        with open(self.paths()[1], encoding="utf-8") as fh:
            self.assertIn('Exec="/opt/my app/python" "/a b/flatcam.py" %F', fh.read())

    def test_unregister_removes_only_requested_then_everything(self):
        self.reg.on_register_files()
        self.reg.on_unregister_files("gerber")
        self.assertNotIn("gerber", self.reg.linux_read_state(self.paths()[0]))
        self.assertTrue(os.path.isfile(self.paths()[1]))
        self.reg.on_unregister_files()
        self.assertFalse(os.path.exists(self.paths()[0]))
        self.assertFalse(os.path.exists(self.paths()[1]))


class TestMacAssociations(FakeHome):
    platform = "darwin"

    def setUp(self):
        super().setUp()
        self.ls = "/fake/lsregister"
        patcher = mock.patch.object(RegisterFK, "mac_lsregister", lambda s: self.ls)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bundle = os.path.join(self.home, "Applications", "FlatCAM EVO.app")

    def info(self):
        with open(os.path.join(self.bundle, "Contents", "Info.plist"), "rb") as fh:
            return plistlib.load(fh)

    def test_register_writes_bundle_and_binds_types(self):
        self.reg.on_register_files()
        info = self.info()
        self.assertEqual(info["CFBundleIdentifier"], "org.flatcam.evo")
        docs = {d["CFBundleTypeName"]: d for d in info["CFBundleDocumentTypes"]}
        self.assertEqual(docs["FlatCAM Project"]["LSHandlerRank"], "Owner")
        self.assertEqual(docs["Gerber file"]["LSHandlerRank"], "Alternate")
        self.assertEqual(docs["Gerber file"]["CFBundleTypeExtensions"], ["gbr"])
        exts = [e["UTTypeTagSpecification"]["public.filename-extension"] for e in info["UTExportedTypeDeclarations"]]
        self.assertEqual(sorted(sum(exts, [])), ["flatconfig", "flatprj"])
        with open(os.path.join(self.bundle, "Contents", "MacOS", "flatcam-evo"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertTrue(content.startswith("#!/bin/sh\nexec "))
        self.assertIn("flatcam.py", content)
        self.assertTrue(self.called(self.ls, "-f", self.bundle))
        self.assertTrue(self.called("duti", "-s", "org.flatcam.evo", ".gbr", "all"))
        self.assertTrue(self.called("duti", "-s", "org.flatcam.evo", "org.flatcam.evo.project", "all"))

    def test_first_run_does_not_make_flatcam_the_default_program(self):
        first_start()
        self.assertEqual(self.info()["FlatCAMAssociations"]["gerber"], ["gbr", "gtl"])
        self.assertFalse([cmd for cmd in self.calls if cmd[0] == "duti" and cmd[3].startswith(".")])
        self.assertTrue(self.called("duti", "-s", "org.flatcam.evo", "org.flatcam.evo.project", "all"))

    def test_unregister_last_type_removes_bundle(self):
        self.reg.on_register_files("project")
        self.reg.on_register_files("gerber")
        self.reg.on_unregister_files("gerber")
        self.assertEqual(sorted(self.info()["FlatCAMAssociations"]), ["project"])
        self.reg.on_unregister_files("project")
        self.assertFalse(os.path.exists(self.bundle))
        self.assertTrue(self.called(self.ls, "-u", self.bundle))

    def test_foreign_bundle_is_left_alone(self):
        os.makedirs(os.path.join(self.bundle, "Contents"))
        with open(os.path.join(self.bundle, "Contents", "Info.plist"), "wb") as fh:
            plistlib.dump({"CFBundleIdentifier": "com.other.app"}, fh)
        self.reg.on_register_files("project")
        self.reg.on_unregister_files()
        self.assertEqual(self.info()["CFBundleIdentifier"], "com.other.app")

    def test_packaging_template_matches_generated_project_types(self):
        path = os.path.join(os.path.dirname(__file__), "..", "assets", "macos", "Info.plist")
        with open(path, "rb") as fh:
            template = plistlib.load(fh)
        generated = self.reg.mac_info({"project": ["flatprj"], "config": ["flatconfig"]})
        for key in ("CFBundleIdentifier", "CFBundleDocumentTypes", "UTExportedTypeDeclarations"):
            self.assertEqual(template[key], generated[key])


if __name__ == "__main__":
    unittest.main()
