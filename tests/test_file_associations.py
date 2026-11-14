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


def first_start(first_run=True, new_version=False):
    """RegisterFK as App makes it at the first start: before defaults_write_form() fills the Preferences lists."""
    ui = mock.MagicMock()
    ui.util_pref_form.fa_excellon_group.exc_list_text = ListText()
    ui.util_pref_form.fa_gcode_group.gco_list_text = ListText()
    ui.util_pref_form.fa_gerber_group.grb_list_text = ListText()
    options = {"first_run": first_run, "util_autocomplete_keywords": "a, b",
               "fa_excellon": "drl", "fa_gcode": "nc", "fa_gerber": "gbr, gtl"}
    return RegisterFK(ui=ui, inform_sig=mock.MagicMock(), options_dict=options, shell=mock.MagicMock(),
                      log=mock.MagicMock(), keywords=rfk.KeyWords(), extensions=rfk.Extensions(),
                      new_version=new_version)


def test_the_auto_completer_knows_every_tcl_command_and_its_options():
    reg = first_start(first_run=False)
    words = reg.myKeywords
    # the commands and their options come from the Tcl command classes, the user words from the Preferences
    for word in ('isolate', 'geocutout', 'db_tool', 'trace_boundary', 'ref_obj', 'a', 'b'):
        assert word in words
    assert len(words) == len(set(words))
    # the Tcl language words come from the interpreter of the shell, as the installed Tcl knows them
    import tkinter
    reg.shell = SimpleNamespace(tcl=tkinter.Tcl(), command_line=mock.MagicMock())
    reg.register_keywords()
    words = reg.myKeywords
    for word in ('proc', 'foreach', 'lsort', 'tcl_platform', 'sqrt', 'isolate', 'db_tool', 'a'):
        assert word in words
    assert not any(word.startswith('Tcl_') for word in words)
    assert len(words) == len(set(words))
    # a rebuild from the Preferences keeps them, and it has the preprocessors of the app (the user's own included)
    reg.preprocessors['My_machine'] = object()
    reg.rebuild_keywords(['c'])
    assert 'db_tool' in reg.myKeywords and 'c' in reg.myKeywords and 'a' not in reg.myKeywords
    assert 'My_machine' in reg.myKeywords and 'proc' in reg.myKeywords
    # the booleans, the file types of the app, the file extensions and the folders of this computer are read too
    from pathlib import Path
    import getpass
    for word in ('True', 'False', 'FlatPrj', 'FlatConfig', 'FlatScript', 'FlatDoc', 'gbr', 'drl', 'nc',
                 Path.home().name, getpass.getuser()):
        assert word in reg.myKeywords
    assert '' not in reg.myKeywords
    # the preference names (for set_sys / get_sys) are completed, but the script editor does not color them
    assert 'fa_gerber' in reg.myKeywords and 'fa_gerber' not in reg.color_keywords
    assert 'isolate' in reg.color_keywords and 'proc' in reg.color_keywords
    # so the Preferences list is only for the user's own words: empty in the factory defaults
    from defaults import AppDefaults
    assert AppDefaults.factory_defaults['util_autocomplete_keywords'] == ''
    assert rfk.KeyWords.default_keywords == []


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
        icon = self.val("Software\\Classes\\FlatCAM.Project\\DefaultIcon")
        self.assertTrue(icon.endswith("app256.ico,0"), icon)  # the application icon, not the old FlatCAM one
        self.assertTrue(os.path.isfile(icon[:-2]))
        self.notify.assert_called()

    def test_unregister_keeps_foreign_association(self):
        self.reg.on_register_files('project')
        self.fake.keys[("HKCU", "software\\classes\\.flatprj")] = "OtherApp.File"
        self.reg.on_unregister_files('project')
        self.assertEqual(self.val("Software\\Classes\\.flatprj"), "OtherApp.File")
        self.assertIsNone(self.val("Software\\Classes\\FlatCAM.Project\\shell\\open\\command"))

    def test_first_run_registers_the_lists_of_the_options(self):
        first_start()
        for ext in ("drl", "nc", "gbr", "gtl"):
            self.assertEqual(self.val("Software\\Classes\\.%s" % ext), "FlatCAM")
        self.assertIsNotNone(self.val("Software\\Classes\\FlatCAM\\shell\\open\\command"))

    def test_failed_registry_writes_report_error_not_success(self):
        for kind in ('project', 'config', 'gerber', 'excellon', 'gcode', None):
            with self.subTest(kind=kind), mock.patch.object(self.reg, 'set_reg', return_value=False):
                self.reg.inform.emit.reset_mock()
                self.reg.on_register_files(kind)
                messages = [c.args[0] for c in self.reg.inform.emit.call_args_list]
                self.assertTrue(any(m.startswith('[ERROR') for m in messages))
                self.assertFalse(any(m.startswith('[success]') for m in messages))

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
        self.tools = {"update-mime-database", "update-desktop-database", "xdg-mime", "duti", "gtk-update-icon-cache"}
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

    def test_unregister_preserves_replaced_icon(self):
        self.reg.on_register_files()
        icon = self.reg.linux_paths()['icon']
        with open(icon, 'wb') as fh:
            fh.write(b'foreign icon')
        self.reg.on_unregister_files()
        with open(icon, 'rb') as fh:
            self.assertEqual(fh.read(), b'foreign icon')

    def test_icon_copy_failure_leaves_no_partial_registration_or_changed_user_files(self):
        paths = self.reg.linux_paths()
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    for key in ('xml', 'desktop', 'icon'):
                        os.makedirs(os.path.dirname(paths[key]), exist_ok=True)
                        with open(paths[key], 'wb') as fh:
                            fh.write(b'user file')
                with mock.patch.object(rfk.shutil, 'copyfile', side_effect=OSError('icon unavailable')):
                    self.reg.on_register_files('project')
                for key in ('xml', 'desktop', 'icon'):
                    if existing:
                        with open(paths[key], 'rb') as fh:
                            self.assertEqual(fh.read(), b'user file')
                    else:
                        self.assertFalse(os.path.exists(paths[key]))
        # A failure after the icon was copied also restores the earlier installation.
        with mock.patch.object(rfk.ET.ElementTree, 'write', side_effect=OSError('MIME write denied')):
            self.reg.on_register_files('project')
        for key in ('xml', 'desktop', 'icon'):
            with open(paths[key], 'rb') as fh:
                self.assertEqual(fh.read(), b'user file')

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
        self.assertIn("\nIcon=flatcam-evo\n", text)
        data = os.path.join(self.home, ".local", "share")
        self.assertTrue(os.path.isfile(os.path.join(data, "icons", "hicolor", "256x256", "apps", "flatcam-evo.png")))
        self.assertTrue(self.called("update-mime-database", os.path.join(data, "mime")))
        self.assertTrue(self.called("update-desktop-database", os.path.join(data, "applications")))
        self.assertTrue(self.called("xdg-mime", "default", "flatcam-evo.desktop", "application/x-flatcam-gerber"))

    def test_appimage_launches_the_file_not_its_temporary_mount(self):
        image = os.path.join(self.home, "FlatCAM_EVO.AppImage")
        open(image, "wb").close()
        mount = os.path.join(self.home, "tmp", ".mount_abc", "FlatCAM")
        with mock.patch.object(rfk.sys, "frozen", True, create=True), \
                mock.patch.object(rfk.sys, "executable", mount), \
                mock.patch.dict(os.environ, {"APPIMAGE": image}):
            self.reg.on_register_files()
        with open(self.paths()[1], encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Exec=%s %%F\n" % self.reg.desktop_quote(image), text)
        self.assertNotIn(".mount_", text)
        data = os.path.join(self.home, ".local", "share")
        icons = os.path.join(data, "icons", "hicolor")
        icon = os.path.join(icons, "256x256", "apps", "flatcam-evo.png")
        self.assertTrue(os.path.isfile(icon))
        self.assertTrue(self.called("gtk-update-icon-cache", "-f", "-t", icons))
        self.reg.on_unregister_files()
        self.assertFalse(os.path.exists(icon))

    def test_first_start_leaves_out_an_extension_that_the_system_maps_elsewhere(self):
        class Mime:
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

        class Database:
            def mimeTypesForFileName(self, name):
                return {"x.ply": [Mime("application/x-ply")], "x.gbr": [Mime("application/octet-stream")]}.get(
                    name, [])

        self.reg.ui.util_pref_form.fa_gcode_group.gco_list_text.get_value = lambda: "nc, ply"
        self.reg.ui.util_pref_form.fa_gerber_group.grb_list_text.get_value = lambda: "gbr"
        with mock.patch.object(rfk.QtCore, "QMimeDatabase", Database):
            self.reg.on_register_files(keep_foreign=True)
            first = self.reg.linux_read_state(self.paths()[0])
            self.reg.on_register_files()
            applied = self.reg.linux_read_state(self.paths()[0])
        self.assertEqual(first["gcode"], ["nc"])
        self.assertEqual(first["gerber"], ["gbr"])
        self.assertEqual(first["project"], ["flatprj"])
        self.assertEqual(applied["gcode"], ["nc", "ply"])

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

    def test_legacy_source_launcher_is_adopted_and_can_be_removed(self):
        self.reg.mac_write(self.bundle, {'gerber': ['gtl']})
        path = os.path.join(self.bundle, 'Contents', 'Info.plist')
        info = self.info()
        info['CFBundleIdentifier'] = self.reg.MAC_BUNDLE_ID
        with open(path, 'wb') as fh:
            plistlib.dump(info, fh)
        self.reg.on_register_files('project')
        self.assertEqual(self.info()['CFBundleIdentifier'], self.reg.MAC_STUB_ID)
        self.assertEqual(self.info()['FlatCAMAssociations']['gerber'], ['gtl'])
        info['FlatCAMAssociations'] = {'gerber': ['gtl']}
        with open(path, 'wb') as fh:
            plistlib.dump(info, fh)
        self.reg.on_unregister_files()
        self.assertFalse(os.path.exists(self.bundle))

    def test_failed_plist_serialization_preserves_readable_bundle(self):
        self.reg.mac_write(self.bundle, {'gerber': ['gtl']})
        before = self.info()
        with mock.patch.object(rfk.plistlib, 'dump', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.reg.mac_write(self.bundle, {'project': ['flatprj']})
        self.assertEqual(self.info(), before)

    def test_legacy_identifier_does_not_adopt_release_binary_or_linked_launcher(self):
        self.reg.mac_write(self.bundle, {'gerber': ['gtl']})
        info = self.info()
        info['CFBundleIdentifier'] = self.reg.MAC_BUNDLE_ID
        path = os.path.join(self.bundle, 'Contents', 'Info.plist')
        script = os.path.join(self.bundle, 'Contents', 'MacOS', 'flatcam-evo')
        with open(path, 'wb') as fh:
            plistlib.dump(info, fh)
        with mock.patch.object(rfk.os.path, 'islink', side_effect=lambda p: p == script):
            self.reg.on_register_files()
            self.reg.on_unregister_files()
            self.reg.mac_remove_own_stub()
        self.assertEqual(self.info(), info)
        with open(script, 'wb') as fh:
            fh.write(b'\xcf\xfa\xed\xfe\x00release binary')
        self.reg.on_register_files()
        self.reg.on_unregister_files()
        self.reg.mac_remove_own_stub()
        self.assertEqual(self.info(), info)
        with open(script, 'rb') as fh:
            self.assertEqual(fh.read(), b'\xcf\xfa\xed\xfe\x00release binary')

    def test_register_writes_bundle_and_binds_types(self):
        self.reg.on_register_files()
        info = self.info()
        self.assertEqual(info["CFBundleIdentifier"], "org.flatcam.evo.launcher")
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
        self.assertTrue(self.called("duti", "-s", "org.flatcam.evo.launcher", ".gbr", "all"))
        self.assertTrue(self.called("duti", "-s", "org.flatcam.evo.launcher", "org.flatcam.evo.project", "all"))

    def test_release_bundle_is_never_taken_over_or_removed_by_a_source_run(self):
        os.makedirs(os.path.join(self.bundle, "Contents"))
        with open(os.path.join(self.bundle, "Contents", "Info.plist"), "wb") as fh:
            plistlib.dump({"CFBundleIdentifier": "org.flatcam.evo", "FlatCAMAssociations": {"gerber": ["gtl"]}}, fh)
        self.reg.on_register_files("project")
        self.reg.on_unregister_files()
        self.reg.mac_remove_own_stub()
        info = self.info()
        self.assertEqual(info["CFBundleIdentifier"], "org.flatcam.evo")
        self.assertEqual(info["FlatCAMAssociations"]["gerber"], ["gtl"])
        with self.assertRaises(OSError):
            self.reg.mac_write(self.bundle, {'project': ['flatprj']})

    def test_release_app_is_registered_itself_and_no_stub_is_written(self):
        real = os.path.join(self.home, "Downloads", "FlatCAM_EVO.app")
        os.makedirs(os.path.join(real, "Contents", "MacOS"))
        # Only the dedicated source launcher can be removed.
        os.makedirs(os.path.join(self.bundle, "Contents"))
        with open(os.path.join(self.bundle, "Contents", "Info.plist"), "wb") as fh:
            plistlib.dump({"CFBundleIdentifier": "org.flatcam.evo.launcher"}, fh)
        with mock.patch.object(rfk.sys, "frozen", True, create=True), \
                mock.patch.object(rfk.sys, "executable", os.path.join(real, "Contents", "MacOS", "FlatCAM")):
            self.reg.on_register_files()
            self.assertFalse(os.path.exists(self.bundle))
            self.assertTrue(self.called(self.ls, "-f", real))
            self.assertTrue(self.called("duti", "-s", "org.flatcam.evo", ".gbr", "all"))
            self.assertTrue(self.called("duti", "-s", "org.flatcam.evo", "org.flatcam.evo.project", "all"))
            self.reg.on_unregister_files()
        self.assertTrue(os.path.isdir(os.path.join(real, "Contents", "MacOS")))

    def test_running_bundle_at_the_stub_path_is_never_removed(self):
        os.makedirs(os.path.join(self.bundle, 'Contents', 'MacOS'))
        with open(os.path.join(self.bundle, 'Contents', 'Info.plist'), 'wb') as fh:
            plistlib.dump({'CFBundleIdentifier': self.reg.MAC_STUB_ID}, fh)
        with mock.patch.object(RegisterFK, 'mac_running_bundle', return_value=self.bundle):
            self.reg.on_register_files()
            self.reg.on_unregister_files()
            self.reg.mac_remove_own_stub()
        self.assertEqual(self.info()['CFBundleIdentifier'], self.reg.MAC_STUB_ID)

    def test_unreadable_user_bundle_is_not_replaced_or_removed(self):
        os.makedirs(os.path.join(self.bundle, 'Contents'))
        marker = os.path.join(self.bundle, 'Contents', 'user-data')
        with open(marker, 'wb') as fh:
            fh.write(b'keep')
        self.reg.on_register_files()
        self.reg.on_unregister_files()
        self.reg.mac_remove_own_stub()
        self.assertTrue(os.path.isfile(marker))
        self.assertFalse(os.path.exists(os.path.join(self.bundle, 'Contents', 'Info.plist')))

    def test_foreign_bundle_is_left_alone(self):
        os.makedirs(os.path.join(self.bundle, "Contents"))
        with open(os.path.join(self.bundle, "Contents", "Info.plist"), "wb") as fh:
            plistlib.dump({"CFBundleIdentifier": "com.other.app"}, fh)
        self.reg.on_register_files("project")
        self.reg.on_unregister_files()
        self.assertEqual(self.info()["CFBundleIdentifier"], "com.other.app")


if __name__ == "__main__":
    unittest.main()
