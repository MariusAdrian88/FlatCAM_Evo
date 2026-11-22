"""cx_Freeze configuration for the Linux and macOS release packages.

Run from the repository root: python scripts/freeze_release.py bdist_appimage
or, on macOS: python scripts/freeze_release.py bdist_dmg
"""

import ast
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def bundle_metadata(source=ROOT / 'appMain.py'):
    """Read the tagged application's identity without importing its GUI or parsing build arguments."""
    tree = ast.parse(Path(source).read_text(encoding='utf-8'))
    app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'App')
    fields = {
        target.id: ast.literal_eval(node.value)
        for node in app.body if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in ('version', 'version_date')
    }
    version = str(fields['version']).split('.')
    if not 1 <= len(version) <= 3 or not all(part.isdecimal() for part in version):
        raise ValueError('App.version must have one to three numeric components for macOS packaging')
    date = datetime.strptime(fields['version_date'], '%Y/%m/%d')
    return {
        'CFBundleName': 'FlatCAM Evo',
        'CFBundleDisplayName': 'FlatCAM Evo',
        'CFBundleIdentifier': 'org.flatcam.evo',
        'CFBundleShortVersionString': '.'.join(version + ['0'] * (3 - len(version))),
        'CFBundleVersion': f'{date.year}.{date.month}.{date.day}',
    }


def class_literals(source, class_name, names):
    """Literal class attributes of a module, read with ast so that no Qt or GUI code is imported at build time."""
    tree = ast.parse(Path(source).read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {
        target.id: ast.literal_eval(node.value)
        for node in cls.body if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in names
    }


def mac_document_types(source=ROOT / 'appCommon' / 'RegisterFileKeywords.py'):
    """The file types of the macOS bundle: the same UTIs and extensions that RegisterFK registers.

    They must be in the Info.plist of the release, because the plist can not be changed after the code signing.
    """
    extensions = class_literals(source, 'Extensions', ('grb_list', 'exc_list', 'gcode_list', 'prj_list', 'conf_list'))
    utis = class_literals(source, 'RegisterFK', ('ASSOC_UTI',))['ASSOC_UTI']
    owned = [('project', 'FlatCAM Project', 'prj_list'), ('config', 'FlatCAM Config', 'conf_list')]
    documents = []
    exported = []
    for key, title, attr in owned:
        documents.append({'CFBundleTypeName': title, 'CFBundleTypeRole': 'Editor', 'LSHandlerRank': 'Owner',
                          'LSItemContentTypes': [utis[key]]})
        exported.append({'UTTypeIdentifier': utis[key], 'UTTypeDescription': title,
                         'UTTypeConformsTo': ['public.data'],
                         'UTTypeTagSpecification': {'public.filename-extension': list(extensions[attr])}})
    for title, attr in (('Gerber file', 'grb_list'), ('Excellon file', 'exc_list'), ('G-code file', 'gcode_list')):
        documents.append({'CFBundleTypeName': title, 'CFBundleTypeRole': 'Editor', 'LSHandlerRank': 'Alternate',
                          'CFBundleTypeExtensions': list(extensions[attr])})
    return {'CFBundleDocumentTypes': documents, 'UTExportedTypeDeclarations': exported}


def xkb_data_dir(prefix=None):
    """The xkeyboard-config data of the build env (conda: share/xkeyboard-config-2, else share/X11/xkb), symlinks
    resolved. The build stops when it has no rules/evdev: an AppImage without it crashes on Wayland (issue #90)."""
    prefix = Path(prefix or sys.prefix)
    for name in ('xkeyboard-config-2', 'X11/xkb'):
        path = Path(os.path.realpath(prefix / 'share' / name))
        if (path / 'rules' / 'evdev').is_file():
            return path
    raise SystemExit('xkeyboard-config data (share/xkeyboard-config-2/rules/evdev) not found in %s: '
                     'conda install xkeyboard-config' % prefix)


def appimage_command():
    """Return a bdist_appimage that passes the AppDir checks of the current appimagetool.

    cx_Freeze 8.7 writes AppRun with mode 0500, links the icons with symlinks and sets an absolute Icon= path.
    The current appimagetool refuses the mode and rejects the .DirIcon symlink.
    """
    import os
    import re
    import shutil
    import stat

    from cx_Freeze.command.bdist_appimage import bdist_appimage

    class ReleaseAppImage(bdist_appimage):
        def save_as_file(self, data, outfile, mode='r'):
            appdir = os.path.dirname(outfile)
            if outfile.endswith('.desktop'):
                # A real icon file at the top level, named as Icon=; appimagetool then makes .DirIcon itself.
                icon = os.path.join(appdir, self.target_name + '.png')
                source = os.path.realpath(icon)
                for link in (icon, os.path.join(appdir, '.DirIcon')):
                    os.unlink(link)
                shutil.copyfile(source, icon)
                data = re.sub(r'^Icon=.*$', f'Icon={self.target_name}', data, flags=re.MULTILINE)
            result = super().save_as_file(data, outfile, mode)
            if os.path.basename(outfile) == 'AppRun':
                # AppRun is the last file written: give all users read access, and execute where the owner has it.
                for root, dirs, files in os.walk(appdir):
                    for path in [root] + [os.path.join(root, name) for name in files]:
                        if os.path.islink(path):
                            continue
                        mode_bits = os.stat(path).st_mode
                        add = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
                        if os.path.isdir(path) or mode_bits & stat.S_IXUSR:
                            add |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                        os.chmod(path, stat.S_IMODE(mode_bits) | add)
            return result

    return ReleaseAppImage


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    from scripts.freeze_compat import patch_scanner

    patch_scanner()
    from cx_Freeze import Executable, setup

    metadata = bundle_metadata()
    # Match make_freeze.py: appMain and the resource lookups live in lib/.
    include_files = [
        (str(ROOT / name), f'lib/{name}')
        for name in ('assets', 'resources', 'locale', 'locales', 'preprocessors', 'config')
        if (ROOT / name).is_dir()
    ]
    if sys.platform == 'linux':
        # The bundled libxkbcommon has the CI conda prefix compiled in; appCommon/LinuxRuntime.py points
        # XKB_CONFIG_ROOT to this copy (issue #90).
        include_files.append((str(xkb_data_dir()), 'lib/xkb'))
    options = {
        'build_exe': {
            'build_exe': 'build/release',
            'path': [str(ROOT), *sys.path],
            'include_files': include_files,
            # These packages load modules by name at runtime.
            'packages': ['OpenGL', 'ortools', 'services.updater'],
            # Qt's cx_Freeze hooks collect the platform-specific PDF/SVG libraries and print plugins.
            'includes': ['PyQt6.QtPdf', 'PyQt6.QtPdfWidgets', 'PyQt6.QtPrintSupport', 'PyQt6.QtSvg'],
            'excludes': ['pandas'],
        },
        'bdist_appimage': {'target_name': 'FlatCAM_EVO'},
        'bdist_mac': {
            'bundle_name': 'FlatCAM_EVO',
            'codesign_identity': '-',
            'plist_items': list(metadata.items()) + list(mac_document_types().items()),
        },
        'bdist_dmg': {'volume_label': 'FlatCAM EVO', 'applications_shortcut': True},
    }
    update_information = os.environ.get('APPIMAGE_UPDATE_INFORMATION')
    if update_information:
        # Embedded for AppImageUpdate, e.g. gh-releases-zsync|owner|repo|latest|FlatCAM_EVO-*-linux-x86_64.AppImage.zsync
        options['bdist_appimage']['updateinformation'] = update_information
    if sys.platform == 'linux':
        # cx_Freeze excludes /usr/lib by default, including Qt's required X11 keyboard and cursor helpers.
        options['build_exe']['bin_includes'] = ['libxkbcommon*.so*', 'libxcb*.so*']
    icon = ROOT / 'assets' / 'resources' / 'app256.png'
    if sys.platform == 'darwin':
        from PIL import Image

        mac_icon = ROOT / 'build' / 'FlatCAM_EVO.icns'
        mac_icon.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(icon) as image:
            image = image.convert('RGBA')
            side = max(image.size)
            square = Image.new('RGBA', (side, side), (0, 0, 0, 0))
            square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
            square.resize((1024, 1024)).save(mac_icon, format='ICNS')
        options['bdist_mac']['iconfile'] = str(mac_icon)

    setup(
        name='FlatCAM_EVO',
        version=metadata['CFBundleShortVersionString'],
        description='FlatCAM EVO PCB CAM application',
        packages=[],
        options=options,
        cmdclass={'bdist_appimage': appimage_command()} if sys.platform == 'linux' else {},
        executables=[Executable(str(ROOT / 'flatcam.py'), base='gui', target_name='FlatCAM',
                                icon=str(icon) if sys.platform == 'linux' else None)],
    )
