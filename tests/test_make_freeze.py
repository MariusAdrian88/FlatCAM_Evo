from __future__ import annotations

import tempfile
import runpy
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

    def test_pruning_keeps_pdf_svg_and_print_runtime_dependencies(self):
        root = Path(self._tempdir.name)
        qt = root / 'lib/PyQt6/Qt6'
        required = [
            qt / 'bin/Qt6Pdf.dll', qt / 'bin/Qt6Svg.dll', qt / 'bin/Qt6PrintSupport.dll',
            qt / 'plugins/imageformats/qpdf.dll', qt / 'plugins/printsupport/windowsprintersupport.dll',
            qt.parent / 'QtPdf.pyd', qt.parent / 'QtPdfWidgets.pyd', qt.parent / 'QtPrintSupport.pyd',
        ]
        for path in required:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'required runtime')
        unused = qt / 'bin/Qt6Unused.dll'
        unused.write_bytes(b'unused runtime')

        def dependencies(path):
            return {
                'QtPdf.pyd': [qt / 'bin/Qt6Pdf.dll'],
                'QtPdfWidgets.pyd': [qt / 'bin/Qt6Pdf.dll'],
                'QtPrintSupport.pyd': [qt / 'bin/Qt6PrintSupport.dll'],
                'Qt6Pdf.dll': [qt / 'bin/Qt6Svg.dll'],
            }.get(Path(path).name, [])

        make_freeze.prune_build_dir(root)
        make_freeze.trim_qt_runtime(root, dependencies=dependencies)
        for path in required:
            self.assertTrue(path.is_file(), str(path.relative_to(root)))
        self.assertFalse(unused.exists())


    def setUp(self):
        import tempfile

        self._tempdir = tempfile.TemporaryDirectory(prefix="flatcam-freeze-test-")

    def tearDown(self):
        self._tempdir.cleanup()


class TestReleasePackaging(unittest.TestCase):

    def test_pdf_runtime_smoke_loads_and_renders_owned_bytes(self):
        from PyQt6 import QtWidgets
        from appCommon.ReleaseSmoke import pdf_runtime_smoke
        application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        pdf_runtime_smoke(application)

    def test_linux_release_accepts_glibc_234_and_rejects_newer_requirements(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/check_glibc.py'
        for version in ('2.34', '2.35'):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as folder:
                (Path(folder) / 'library.so').write_bytes(b'\x7fELF')
                result = SimpleNamespace(stdout='Name: GLIBC_' + version)
                with patch('sys.argv', [str(script), folder]), patch('subprocess.run', return_value=result):
                    if version == '2.34':
                        runpy.run_path(str(script), run_name='__main__')
                    else:
                        with self.assertRaises(SystemExit):
                            runpy.run_path(str(script), run_name='__main__')

    def test_release_bundle_declares_the_document_types_that_the_app_registers(self):
        from scripts import freeze_release
        from appCommon.RegisterFileKeywords import Extensions, RegisterFK
        plist = dict(freeze_release.mac_document_types())
        docs = {d['CFBundleTypeName']: d for d in plist['CFBundleDocumentTypes']}
        self.assertEqual(docs['FlatCAM Project']['LSItemContentTypes'], [RegisterFK.ASSOC_UTI['project']])
        self.assertEqual(docs['FlatCAM Config']['LSHandlerRank'], 'Owner')
        self.assertEqual(docs['Gerber file']['LSHandlerRank'], 'Alternate')
        self.assertEqual(docs['Gerber file']['CFBundleTypeExtensions'], Extensions.grb_list)
        self.assertEqual(docs['Excellon file']['CFBundleTypeExtensions'], Extensions.exc_list)
        self.assertEqual(docs['G-code file']['CFBundleTypeExtensions'], Extensions.gcode_list)
        exported = {e['UTTypeIdentifier']: e['UTTypeTagSpecification']['public.filename-extension']
                    for e in plist['UTExportedTypeDeclarations']}
        self.assertEqual(exported, {'org.flatcam.evo.project': ['flatprj'], 'org.flatcam.evo.config': ['flatconfig']})

    def test_release_rejects_missing_or_corrupt_bundled_resources(self):
        catalog_data = (Path(__file__).resolve().parents[1] / 'locale/de/LC_MESSAGES/strings.mo').read_bytes()
        for state in ('valid', 'missing_preprocessor', 'missing_translation', 'corrupt_translation',
                      'missing_pdf_smoke'):
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
                stdout = 'FLATCAM_RELEASE_SMOKE_OK'
                if state != 'missing_pdf_smoke':
                    stdout += '\nFLATCAM_PDF_RUNTIME_SMOKE_OK\nFLATCAM_FILM_PREVIEW_SMOKE_OK'
                result = SimpleNamespace(returncode=0, stdout=stdout, stderr='')
                with patch('scripts.check_release.subprocess.run', return_value=result):
                    if state == 'valid':
                        check_release(executable)
                    else:
                        with self.assertRaises((RuntimeError, OSError)):
                            check_release(executable)


if __name__ == "__main__":
    unittest.main()
