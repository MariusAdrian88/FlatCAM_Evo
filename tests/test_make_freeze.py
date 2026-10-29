from __future__ import annotations

import ast
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

try:
    import make_freeze
except ImportError:
    # make_freeze.py is local only (git-ignored); skip the tests when it is absent
    make_freeze = None

from services.updater.archive import build_deterministic_zip
from scripts.check_release import check_release


class FakeExecutable:
    def __init__(self, script, **kwargs):
        self.script = script
        self.kwargs = kwargs


def _write_recovery_runtime(target: Path) -> None:
    library = target / "lib" / "library.zip"
    library.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(library, "w") as archive:
        archive.writestr("services/updater/recovery.pyc", b"recovery")


def _fake_freezer_class(calls, *, include_recovery=True, include_executable=True):
    class FakeFreezer:
        def __init__(self, *, executables, target_dir, **kwargs):
            self.executables = executables
            self.target_dir = Path(target_dir)
            self.kwargs = kwargs
            calls.append(self)

        def freeze(self):
            self.target_dir.mkdir(parents=True, exist_ok=True)
            if include_executable:
                for executable in self.executables:
                    (self.target_dir / executable.kwargs["target_name"]).write_bytes(b"executable")
            if self.target_dir.name == "updater" and include_recovery:
                _write_recovery_runtime(self.target_dir)

    return FakeFreezer


@unittest.skipIf(make_freeze is None, "make_freeze.py is not present")
class TestMakeFreeze(unittest.TestCase):

    def test_windows_has_a_console_executable_for_headless_runs(self):
        created = []

        def fake_executable(script, **kwargs):
            created.append((Path(script).name, kwargs))
            return SimpleNamespace(script=script, kwargs=kwargs)

        with patch.object(make_freeze, 'Executable', fake_executable),                 patch.object(make_freeze.sys, 'platform', 'win32'):
            executables = make_freeze.make_main_executables(make_freeze.ROOT)

        self.assertEqual(
            [('flatcam.py', {'target_name': 'FlatCAM.exe', 'base': 'gui'}),
             ('flatcam.py', {'target_name': 'FlatCAM-cli.exe'})],
            created)
        self.assertEqual(2, len(executables))

    def test_incomplete_updater_is_rejected_without_touching_existing_final_runtime(self):
        calls = []
        fake_freezer = _fake_freezer_class(calls, include_recovery=False)
        build_dir = Path(self._tempdir.name) / "build"
        stable_updater = build_dir / "updater"
        stable_updater.mkdir(parents=True)
        (build_dir / "FlatCAM.exe").write_bytes(b"old-main")
        (stable_updater / "FlatCAMUpdater.exe").write_bytes(b"old-updater")

        with patch.object(make_freeze, "Executable", FakeExecutable), patch.object(
            make_freeze, "Freezer", fake_freezer
        ):
            with self.assertRaisesRegex(RuntimeError, "services.updater.recovery"):
                make_freeze.build(build_dir)

        self.assertEqual(b"old-main", (build_dir / "FlatCAM.exe").read_bytes())
        self.assertEqual(
            b"old-updater", (build_dir / "updater" / "FlatCAMUpdater.exe").read_bytes()
        )

    def test_final_assembly_keeps_complete_updater_runtime_in_fixed_subfolder(self):
        main_stage = Path(self._tempdir.name) / "main-stage"
        updater_stage = Path(self._tempdir.name) / "updater-stage"
        build_dir = Path(self._tempdir.name) / "build"
        (main_stage / "lib").mkdir(parents=True)
        (main_stage / "FlatCAM.exe").write_bytes(b"main")
        (main_stage / "FlatCAM-cli.exe").write_bytes(b"main-cli")
        (main_stage / "lib" / "library.zip").write_bytes(b"main-runtime")
        updater_stage.mkdir()
        (updater_stage / "FlatCAMUpdater.exe").write_bytes(b"updater")
        _write_recovery_runtime(updater_stage)
        (updater_stage / "runtime" / "support.dat").parent.mkdir(parents=True)
        (updater_stage / "runtime" / "support.dat").write_bytes(b"support")

        make_freeze.assemble_final_build(main_stage, updater_stage, build_dir)

        self.assertEqual(b"main", (build_dir / "FlatCAM.exe").read_bytes())
        self.assertEqual(
            b"updater", (build_dir / "updater" / "FlatCAMUpdater.exe").read_bytes()
        )
        self.assertEqual(
            b"support", (build_dir / "updater" / "runtime" / "support.dat").read_bytes()
        )
        self.assertTrue((build_dir / "updater" / "lib" / "library.zip").is_file())
        self.assertFalse((build_dir / "FlatCAMUpdater.exe").exists())

    def test_release_zip_excludes_build_script_and_keeps_updater_runtime(self):
        root = Path(self._tempdir.name) / "release-root"
        root.mkdir()
        (root / "flatcam.py").write_text("payload", encoding="utf-8")
        (root / "make_freeze.py").write_text("build", encoding="utf-8")
        (root / "make_freezed.py").write_text("build", encoding="utf-8")
        (root / "build.py").write_text("build", encoding="utf-8")
        (root / "updater" / "FlatCAMUpdater.exe").parent.mkdir()
        (root / "updater" / "FlatCAMUpdater.exe").write_bytes(b"stable")

        archive_path = Path(self._tempdir.name) / "release.zip"
        build_deterministic_zip(root, archive_path)

        with ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        self.assertIn("flatcam.py", names)
        self.assertNotIn("make_freeze.py", names)
        self.assertNotIn("make_freezed.py", names)
        self.assertNotIn("build.py", names)
        self.assertIn("updater/FlatCAMUpdater.exe", names)


    def setUp(self):
        import tempfile

        self._tempdir = tempfile.TemporaryDirectory(prefix="flatcam-freeze-test-")

    def tearDown(self):
        self._tempdir.cleanup()


class TestReleasePackaging(unittest.TestCase):

    def test_release_rejects_missing_or_corrupt_bundled_resources(self):
        catalog_data = (Path(__file__).resolve().parents[1] / 'locale/de/LC_MESSAGES/strings.mo').read_bytes()
        for state in ('valid', 'missing_preprocessor', 'missing_translation', 'corrupt_translation'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                executable = root / 'FlatCAM'
                executable.touch()
                if state != 'missing_preprocessor':
                    preprocessor = root / 'lib/preprocessors/default.py'
                    preprocessor.parent.mkdir(parents=True)
                    preprocessor.write_text('# bundled preprocessor', encoding='utf-8')
                if state != 'missing_translation':
                    catalog = root / 'lib/locale/de/LC_MESSAGES/strings.mo'
                    catalog.parent.mkdir(parents=True)
                    catalog.write_bytes(b'corrupt' if state == 'corrupt_translation' else catalog_data)
                result = SimpleNamespace(returncode=0, stdout='FLATCAM_RELEASE_SMOKE_OK', stderr='')
                with patch('scripts.check_release.subprocess.run', return_value=result):
                    if state == 'valid':
                        check_release(executable)
                    else:
                        with self.assertRaises((RuntimeError, OSError)):
                            check_release(executable)


if __name__ == "__main__":
    unittest.main()
