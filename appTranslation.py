# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

import os
import ctypes
import sys
import logging
from pathlib import Path

from PyQt6 import QtWidgets, QtGui
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QSettings

import gettext
import builtins

log = logging.getLogger('base')

if '_' not in builtins.__dict__:
    _ = gettext.gettext

# ISO639-1 codes from here: https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes
# the name of each language is written in that language (the settings keep the name)
languages_dict = {
    'zh': '简体中文',
    'de': 'Deutsch',
    'en': 'English',
    'es': 'Español',
    'fr': 'Français',
    'it': 'Italiano',
    'pt_BR': 'Português do Brasil',
    'ro': 'Română',
    'ru': 'Русский',
    'tr': 'Türkçe',
}

# the languages written from right to left: the whole interface is mirrored for them (set_layout_direction())
rtl_languages = ('ar', 'fa', 'he', 'ur')

translations = {}

languages_path_search = ''


def isAdmin():
    try:
        is_admin = (os.getuid() == 0) or (os.geteuid() == 0)
    except AttributeError:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    return is_admin


def load_languages():
    available_translations = []
    try:
        available_translations = next(os.walk(locale_dir()))[1]
    except StopIteration:
        pass

    for lang in available_translations:
        try:
            if lang in languages_dict.keys():
                translations[lang] = languages_dict[lang]
        except KeyError as e:
            log.debug("FlatCAMTranslations.load_languages() --> %s" % str(e))
    return translations


def languages_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locale')


def languages_dir_cx_freeze():
    return os.path.join(Path(__file__).parents[1], 'locale')


def locale_dir():
    """
    :return: the folder with the translations. From the source it is next to this module; frozen with cx_Freeze this
    module is inside lib/library.zip and the folder is lib/locale.
    """
    folder = languages_dir()
    if os.path.isdir(folder):
        return folder
    return languages_dir_cx_freeze()


def on_language_apply_click(app, restart=False):
    """
    Using instructions from here:
    https://inventwithpython.com/blog/2014/12/20/translate-your-python-3-program-with-the-gettext-module/

    :return:
    """
    name = app.ui.general_pref_form.general_app_group.language_combo.currentText()

    theme_settings = QSettings("Open Source", "FlatCAM_EVO")
    if theme_settings.contains("theme"):
        theme = theme_settings.value('theme', type=str)
    else:
        theme = 'light'

    if theme == 'light':
        resource_loc = 'assets/resources'
    else:
        resource_loc = 'assets/resources/dark_resources'

    # do nothing if trying to apply the language that is the current language (already applied).
    settings = QSettings("Open Source", "FlatCAM_EVO")
    if settings.contains("language"):
        current_language = settings.value('language', type=str)
        if current_language == name:
            return

    if restart:
        msgbox = FCMessageBox(parent=app.ui)
        title = _("The application will restart.")
        txt = '%s %s?' % (_("Are you sure do you want to change the current language to"), name.capitalize())
        msgbox.setWindowTitle('%s ...' % _("Apply Language"))  # taskbar still shows it
        msgbox.setWindowIcon(QtGui.QIcon(resource_loc + '/app128.png'))
        msgbox.setText('<b>%s</b>' % title)
        msgbox.setInformativeText(txt)
        msgbox.setIconPixmap(QtGui.QPixmap(resource_loc + '/language32.png'))

        bt_yes = msgbox.addButton(_("Yes"), QtWidgets.QMessageBox.ButtonRole.YesRole)
        bt_no = msgbox.addButton(_("No"), QtWidgets.QMessageBox.ButtonRole.NoRole)

        msgbox.setDefaultButton(bt_yes)
        msgbox.exec()
        response = msgbox.clickedButton()

        if response == bt_no:
            return
        else:
            settings = QSettings("Open Source", "FlatCAM_EVO")
            saved_language = name
            settings.setValue('language', saved_language)
            # This will write the setting to the platform specific storage.
            del settings

            restart_program(app=app)


def apply_language(domain, lang=None):
    lang_code = ''

    if lang is None:
        settings = QSettings("Open Source", "FlatCAM_EVO")
        if settings.contains("language"):
            name = settings.value('language')
        else:
            name = 'English'
            # in case the 'language' parameter is not in QSettings add it to QSettings and it's value is
            # the default language, English
            settings.setValue('language', 'English')

            # This will write the setting to the platform specific storage.
            del settings
    else:
        name = str(lang)    # we make it a string: "None"

    for lang_code, lang_usable in load_languages().items():
        if lang_usable == name:
            # break and then use the current key as language
            break
    else:
        # an unknown or unavailable language (e.g. a name written by an older version) is English; without this
        # the loop left lang_code at the last language of the list
        if lang_code != '':
            lang_code = 'en'
            name = 'English'
            # write the name back: else Preferences -> Apply of English compares with the old name and restarts
            if lang is None:
                settings = QSettings("Open Source", "FlatCAM_EVO")
                settings.setValue('language', name)
                del settings

    if lang_code == '':
        return "no language"
    else:
        try:
            current_lang = gettext.translation(str(domain), localedir=locale_dir(), languages=[lang_code])
            current_lang.install()
        except Exception as e:
            log.error("FlatCAMTranslation.apply_language() --> %s" % str(e))

        return name


def is_rtl(name):
    """
    :param name:    the name of a language, as saved in the settings (a value of languages_dict)
    :return:        True if the language is written from right to left
    """
    codes = [code for code, lang_name in languages_dict.items() if lang_name == name]
    return bool(codes) and codes[0].split('_')[0] in rtl_languages


def set_layout_direction(name):
    """
    Mirror the whole interface for a right-to-left language. Called once at start-up, before the GUI is built: every
    widget takes the direction of the application, so a new UI needs nothing extra. A change of the language restarts
    the application. For a left-to-right language nothing is changed.

    :param name:    the name of the current language
    """
    if is_rtl(name):
        QtWidgets.QApplication.setLayoutDirection(Qt.LayoutDirection.RightToLeft)


def save_before_restart(app):
    """
    Before a restart, ask about each project with unsaved changes and save it, the same way as the exit of the app
    (AppLifecycle.final_save()). The save runs in this thread and has ended when this function returns: os.execl()
    ends the process at once, so a save in a worker thread would be killed before the file is written.

    :param app: the App
    :return:    True if the restart can go on
    """
    next_start = _("The setting will be applied at the next application start.")
    not_restarted = _("FlatCAM did not restart.")

    # the Geometry Editor writes its result into the source object after the editor closes
    if getattr(getattr(app, 'geo_editor', None), '_source_save_pending', False) is True:
        app.inform.emit('[WARNING_NOTCL] %s %s %s' % (
            _("Geometry save is running. Wait until it ends."), not_restarted, next_start))
        return False

    # a running save (File -> Save, Save As, a Tcl save) is not killed. project_save_pending stays set until the
    # GUI has the result of a File -> Save
    if app.save_in_progress or getattr(app, 'project_save_pending', False):
        app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Saving the project. FlatCAM did not restart."), next_start))
        return False

    projects = app.projects
    start = projects.active
    to_ask = projects.dirty_contexts()
    # save_context() refuses a save while an editor is open or a task runs: do not ask a question that cannot be done
    if to_ask and projects.is_busy():
        if projects.is_editing():
            reason = _("Close the editor first.")
        else:
            reason = _("A task is running. Wait until it ends.")
        app.inform.emit('[WARNING_NOTCL] %s %s %s' % (reason, not_restarted, next_start))
        return False
    try:
        for ctx in to_ask:
            answer = projects.ask_save(ctx)
            if answer == 'cancel' or (answer == 'yes' and not projects.save_context(ctx, visuals=False)):
                app.inform.emit('[WARNING_NOTCL] %s %s' % (
                    _("The project was not saved. FlatCAM did not restart."), next_start))
                return False
    finally:
        # the save of an inactive project made it active, but the canvas still shows start
        projects.return_to(start)
    return True


def restart_program(app):
    """Restarts the current program.
    Note: after a restart this function does not return. The projects with unsaved changes are saved first
    (save_before_restart()); while a project save runs, or when a save is cancelled or fails, there is no restart.

    :return:    False if the app does not restart
    """
    log.debug("FlatCAMTranslation.restart_program()")

    theme_settings = QSettings("Open Source", "FlatCAM_EVO")
    if theme_settings.contains("theme"):
        theme = theme_settings.value('theme', type=str)
    else:
        theme = 'light'

    if theme == 'light':
        resource_loc = 'assets/resources'
    else:
        resource_loc = 'assets/resources/dark_resources'

    # before the ArgsThread is stopped: without a restart the app goes on as it was
    if not save_before_restart(app):
        # on_app_restart() hid the tray icon
        tray = getattr(app, 'trayIcon', None)
        if tray is not None and app.options.get("global_systray_icon", app.defaults.get("global_systray_icon")):
            try:
                tray.show()
            except Exception:
                pass
        return False

    # the new process must find the socket free
    try:
        if app.new_launch is not None:
            app.new_launch.close()
    except Exception as err:
        log.error("FlatCAMTranslation.restart_program() --> %s" % str(err))

    app.defaults.update(app.options)
    app.preferencesUiManager.save_defaults()

    try:
        python = sys.executable
        os.execl(python, python, *sys.argv)
    except Exception:
        # app_run_as_admin = isAdmin()
        msgbox = FCMessageBox(parent=app.ui)
        title = _("The setting will be applied at the next application start.")
        txt = _("The user does not have admin rights or UAC issues.")
        msgbox.setWindowTitle('%s ...' % _("Quit"))  # taskbar still shows it
        msgbox.setWindowIcon(QtGui.QIcon(resource_loc + '/app128.png'))
        msgbox.setText('<b>%s</b>' % title)
        msgbox.setInformativeText(txt)
        msgbox.setIcon(QtWidgets.QMessageBox.Icon.Critical)

        bt_yes = msgbox.addButton(_("Quit"), QtWidgets.QMessageBox.ButtonRole.YesRole)

        msgbox.setDefaultButton(bt_yes)
        msgbox.exec()


# TODO Due of some circular imports issues which I currently can't fix I re-add this class here
#  (mainly is located in appGUI.GUIElements) - required for a consistent look
class FCMessageBox(QtWidgets.QMessageBox):
    """
    Frameless QMessageBox
    """

    def __init__(self, *args, **kwargs):
        super(FCMessageBox, self).__init__(*args, **kwargs)
        self.offset = None
        self.moving = None
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint)

        #   "background-color: palette(base); "
        self.setStyleSheet(
            "QDialog { "
            "border: 1px solid palette(shadow); "
            "}"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.moving = True
            self.offset = event.position()

    def mouseMoveEvent(self, event):
        if self.moving:
            self.move(event.globalPosition().toPoint() - self.offset.toPoint())
