# ##########################################################
# FlatCAM EVO                                              #
# MIT Licence                                              #
# ##########################################################

import os

# The xkeyboard-config data that scripts/freeze_release.py copies next to the frozen executable (lib/xkb).
BUNDLED_XKB = os.path.join('lib', 'xkb')
SYSTEM_XKB = ('/usr/share/X11/xkb', '/usr/share/xkeyboard-config-2')


def prepare_environment(environ, platform, frozen, exe_dir):
    """Linux only, before QApplication (issues #90 and #91); environ is changed in place, a value of the user stays.

    XKB: the bundled libxkbcommon looks for its data in the CI build prefix. A frozen app points XKB_CONFIG_ROOT to
    the bundled data, else to the system data.
    Platform: in the AppImage on a Wayland session with XWayland, Qt's Wayland plugin can give VisPy an OpenGL ES
    context (black canvas); xcb gives desktop OpenGL."""
    if not platform.startswith('linux'):
        return
    if frozen and 'XKB_CONFIG_ROOT' not in environ:
        for candidate in (os.path.join(exe_dir, BUNDLED_XKB),) + SYSTEM_XKB:
            if os.path.isfile(os.path.join(candidate, 'rules', 'evdev')):
                environ['XKB_CONFIG_ROOT'] = candidate
                break
    wayland = 'WAYLAND_DISPLAY' in environ or environ.get('XDG_SESSION_TYPE') == 'wayland'
    if 'APPIMAGE' in environ and 'QT_QPA_PLATFORM' not in environ and environ.get('DISPLAY') and wayland:
        environ['QT_QPA_PLATFORM'] = 'xcb'
