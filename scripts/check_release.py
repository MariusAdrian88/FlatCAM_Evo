"""Launch a frozen FlatCAM from outside its source tree before publishing it."""

import gettext
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def check_release(executable):
    executable = str(Path(executable).resolve(strict=True))
    lib_dir = Path(executable).parent / 'lib'
    if not (lib_dir / 'preprocessors' / 'default.py').is_file():
        raise RuntimeError(f'Bundled default preprocessor is missing from {lib_dir}')
    catalogs = sorted((lib_dir / 'locale').glob('*/LC_MESSAGES/strings.mo'))
    if not catalogs:
        raise RuntimeError(f'Bundled translations are missing from {lib_dir}')
    for catalog in catalogs:
        try:
            with catalog.open('rb') as stream:
                gettext.GNUTranslations(stream)
        except (OSError, EOFError, UnicodeError, struct.error) as error:
            raise RuntimeError(f'Invalid bundled translation: {catalog}') from error

    command = (
        'if {[lsearch -exact [list_pp] default] < 0} '
        '{error "Bundled default preprocessor was not loaded"}; '
        'if {[llength [glob -nocomplain -- locale/*/LC_MESSAGES/strings.mo]] == 0} '
        '{error "Bundled translations are not accessible from the application directory"}; '
        'puts FLATCAM_RELEASE_SMOKE_OK'
    )
    with tempfile.TemporaryDirectory(prefix='flatcam-release-') as folder:
        env = os.environ.copy()
        for name in ('PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
                     'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'TCL_LIBRARY', 'TK_LIBRARY'):
            env.pop(name, None)
        env['QT_QPA_PLATFORM'] = 'offscreen'
        env['FLATCAM_DATA_PATH'] = str(Path(folder) / 'settings')
        env['FLATCAM_RELEASE_PDF_SMOKE'] = '1'
        result = subprocess.run(
            [executable, '--headless', f'--command={command}'],
            cwd=folder, env=env, capture_output=True, text=True, timeout=180,
        )
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        if result.returncode or any(marker not in result.stdout for marker in
                                    ('FLATCAM_RELEASE_SMOKE_OK', 'FLATCAM_PDF_RUNTIME_SMOKE_OK',
                                     'FLATCAM_FILM_PREVIEW_SMOKE_OK')):
            raise RuntimeError(f'Frozen FlatCAM startup failed (exit code {result.returncode})')


if __name__ == '__main__':
    check_release(sys.argv[1])
