
from PyQt6.QtCore import pyqtSignal
from PyQt6 import QtCore

from dataclasses import dataclass
import ctypes
from copy import deepcopy
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import typing
import xml.etree.ElementTree as ET

if sys.platform == 'win32':
    import winreg

if typing.TYPE_CHECKING:
    import appMain


def _tcl_command_keywords():
    """The Tcl command names and the option names, read from the command classes; none if they can not be read."""
    try:
        import tclCommands
        return tclCommands.command_keywords()
    except Exception:
        return [], []


def _unique(words):
    """The words without the repeated ones, in their order."""
    return list(dict.fromkeys(words))


_TCL_WORD = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def tcl_language_words(interp):
    """
    The words of the Tcl language as the installed Tcl knows them: its commands (the FlatCAM commands and the procs
    included), its global variables and the math functions of expr.

    :param interp:  the Tcl interpreter of the shell (None: no words)
    :return:        sorted list of words; empty if the interpreter can not be read
    """
    if interp is None:
        return []
    try:
        words = list(interp.splitlist(interp.eval('info commands')))
        words += list(interp.splitlist(interp.eval('info globals')))
        words += [name.rsplit('::', 1)[-1]
                  for name in interp.splitlist(interp.eval('info commands ::tcl::mathfunc::*'))]
    except Exception:
        return []
    # the internal callbacks of tkinter have names that start with digits
    return sorted({word for word in words if isinstance(word, str) and _TCL_WORD.match(word)})


# the boolean values of the Tcl commands
BOOLEAN_WORDS = ['True', 'False']
# the file types of the application
APP_FILE_TYPES = ['FlatPrj', 'FlatConfig', 'FlatScript', 'FlatDoc']
# environment variables that hold important folders (Windows, Linux, macOS)
_FOLDER_VARIABLES = ('USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData',
                     'PUBLIC', 'HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'TMPDIR')


def _split_words(text):
    """The words of a comma separated list; a word can have spaces (e.g. Program Files)."""
    return [word.strip() for word in str(text or '').split(',') if word.strip()]


def folder_words(extra_paths=()):
    """
    The name of the current user and the names of the important folders of this computer, as this operating system
    has them: each part of the paths
    of the home, desktop, documents, downloads, application data, configuration and temporary folders and of the
    folder environment variables (e.g. Users, AppData, Roaming, Program Files on Windows; home, .config on Linux;
    Users, Library, Application Support on macOS).

    :param extra_paths: more paths whose parts are added (e.g. the data folder of the application)
    :return:            sorted list of folder names
    """
    paths = list(extra_paths)
    try:
        from PyQt6.QtCore import QStandardPaths
        location = QStandardPaths.StandardLocation
        for kind in (location.HomeLocation, location.DesktopLocation, location.DocumentsLocation,
                     location.DownloadLocation, location.PicturesLocation, location.GenericDataLocation,
                     location.GenericConfigLocation, location.TempLocation):
            # only the main folder of each kind: the other ones include the folder of the running program
            paths.append(QStandardPaths.writableLocation(kind))
    except Exception:
        pass
    paths += [os.environ[name] for name in _FOLDER_VARIABLES if os.environ.get(name)]
    words = set()
    # the name of the current user (the home folder can have another name, e.g. after an account rename)
    try:
        import getpass
        words.add(getpass.getuser())
    except Exception:
        pass
    for path in paths:
        for part in Path(path).parts:
            name = part.strip('\\/')
            # the drive (C:) and the root (/) are not folder names
            if name and not name.endswith(':'):
                words.add(name)
    return sorted(words)


@dataclass
class KeyWords:
    """
    Holds the keywords recognized by the application
    """

    # All the words of the auto-completer are read when the app starts (see RegisterFK): the Tcl language words, the
    # Tcl commands with their options, the preprocessors, the booleans, the file types and extensions and the folders
    # of the computer. The Preferences list (self.options["util_autocomplete_keywords"]) is only for the user's own
    # words, so its default is empty.
    default_keywords = []


@dataclass
class Extensions:
    """
    Holds the file extensions recognized by the application
    """

    # Protel / Altium: gtl..gko, gm1..gm16 (mechanical), g1..g16 (mid layers), gp1..gp8 (planes), gpt / gpb (pad
    # masters), gd1 / gg1 (drill drawing and guide); KiCad: gta / gba (adhesive), g2.. (inner layers);
    # Seeed / JLC: gl2..gl5 (inner layers); Eagle: cmp..mil; OrCAD: top..spb; Allegro: art; Mentor: pho, gdo
    grb_list = [
        'art', 'bot', 'bsm', 'cmp', 'crc', 'crs', 'dim',
        'g1', 'g2', 'g3', 'g4', 'g5', 'g6', 'g7', 'g8', 'g9', 'g10', 'g11', 'g12', 'g13', 'g14', 'g15', 'g16',
        'gb0', 'gb1', 'gb2', 'gb3', 'gb4', 'gb5', 'gb6', 'gb7', 'gb8', 'gb9', 'gba', 'gbd', 'gbl', 'gbo', 'gbp',
        'gbr', 'gbs', 'gd1', 'gdl', 'gdo', 'ger', 'gg1', 'gko', 'gl2', 'gl3', 'gl4', 'gl5',
        'gm1', 'gm2', 'gm3', 'gm4', 'gm5', 'gm6', 'gm7', 'gm8', 'gm9', 'gm10', 'gm11', 'gm12', 'gm13', 'gm14',
        'gm15', 'gm16', 'gme', 'gml', 'gp1', 'gp2', 'gp3', 'gp4', 'gp5', 'gp6', 'gp7', 'gp8', 'gpb', 'gpt',
        'grb', 'gta', 'gtl', 'gto', 'gtp', 'gts', 'ly15', 'ly2', 'mil', 'outline',
        'pho', 'plc', 'pls', 'smb', 'smt', 'sol', 'spb', 'spt', 'ssb', 'sst', 'stc', 'sts', 'top',
        'tsm'
    ]

    exc_list = ['drd', 'drl', 'drill', 'exc', 'ncd', 'tap', 'txt', 'xln', 'xnc']

    gcode_list = [
        'cnc', 'din', 'dnc', 'ecs', 'eia', 'fan', 'fgc', 'fnc', 'gc', 'gcd', 'gco', 'gcode', 'h', 'hnc',
        'i', 'knc', 'min', 'mpf', 'mpr', 'nc', 'ncc', 'ncg', 'ngc', 'ncp', 'out', 'ply', 'rol',
        'sbp', 'tap', 'xpi'
    ]
    svg_list = ['svg']
    dxf_list = ['dxf']
    pdf_list = ['pdf']
    hpgl_list = ['hgl', 'hpg', 'hpgl', 'plt']
    prj_list = ['flatprj']
    conf_list = ['flatconfig']
    script_list = ['flatscript', 'tcl', 'txt']
    doc_list = ['flatdoc', 'htm', 'html', 'pdf', 'txt']


# the start of an Excellon file: the header (M48) or a tool definition such as 'T1C0.8' or 'T01F00S00C0.0300'
EXCELLON_START_RE = re.compile(r'^[ \t]*(?:M48\b|T\d+(?:[FS][\d.]*)*C[\d.]+)', re.MULTILINE | re.IGNORECASE)


def starts_like_excellon(path):
    """
    For an extension of both Excellon and G-code files ('tap'): True if the file starts like an Excellon file.

    :param path:    path of the file
    :return:        True for an Excellon file, and for a file that cannot be read (Excellon was always tried first)
    """
    try:
        with open(path, 'r', errors='replace') as f:
            head = f.read(8192)
    except OSError:
        return True
    return EXCELLON_START_RE.search(head) is not None


class RegisterFK(QtCore.QObject):
    """
    Will associate the application with some files with certain extensions. It will also update the Qt autocompleter.
    """

    def __init__(self,
                 ui: 'appMain.MainGUI',
                 inform_sig: pyqtSignal,
                 options_dict: 'appMain.AppOptions',
                 shell: 'appMain.FCShell',
                 log: ('appMain.AppLogging', 'appMain.logging'),
                 keywords: KeyWords,
                 extensions: Extensions,
                 new_version: bool = False,
                 preprocessors=None):
        """
        :param new_version:     True at the first start of a new version: the file associations that an earlier
                                version registered get the current icon
        :param preprocessors:   the dictionary of the loaded preprocessors (name -> preprocessor), for the
                                auto-completer
        """

        super().__init__()
        self.ui = ui
        self.shell = shell
        self.log = log
        self.inform = inform_sig
        self.options = options_dict
        self.keywords = keywords

        # The words are read, never listed by hand: the Tcl language words from the interpreter of the shell, the
        # command names and their options from the Tcl command classes, the preprocessors as they were loaded
        self.tcl_keywords = tcl_language_words(getattr(shell, 'tcl', None))
        self.tcl_commands_list, self.tcl_options_list = _tcl_command_keywords()
        # the dictionary of the app (the user preprocessors included): the names are read at each rebuild
        self.preprocessors = preprocessors if preprocessors is not None else {}
        # the folders of this computer, read once
        self.folder_names = folder_words()
        self.default_keywords = self.keywords.default_keywords

        self.exc_list = extensions.exc_list
        self.grb_list = extensions.grb_list
        self.gcode_list = extensions.gcode_list
        self.svg_list = extensions.svg_list
        self.dxf_list = extensions.dxf_list
        self.pdf_list = extensions.pdf_list
        self.prj_list = extensions.prj_list
        self.conf_list = extensions.conf_list

        # the names of the objects (prepend_keyword() / remove_keyword()), kept apart: a rebuild of the keywords from
        # the Preferences must not drop them from the auto-completer
        self.object_names = []

        # the user's own words, from the Preferences
        self.user_keywords = _split_words(self.options.get('util_autocomplete_keywords'))
        self.myKeywords = self._all_keywords()

        # initial register of keywords for the shell
        self.register_keywords(self.myKeywords)

        self.connect_signals()

        # ###########################################################################################################
        # ##################################### Register files with FlatCAM;  #######################################
        # ####### Windows: registry; Linux: ~/.local/share (mime + desktop); macOS: ~/Applications app bundle  ########
        # ###########################################################################################################
        if self.options.get("first_run") is True:
            # the registration reads the lists from the Preferences forms, which defaults_write_form() fills only
            # later: empty, they registered nothing and unregistered every extension of the options
            for key, (group, widget) in self.ASSOC_WIDGET.items():
                getattr(getattr(self.ui.util_pref_form, group), widget).set_value(self.options.get('fa_%s' % key))
            # nobody asked for this registration: an extension that another program opens is left to that program
            self.on_register_files(keep_foreign=True)
        elif new_version and sys.platform == 'win32':
            self.refresh_windows_icons()

    def connect_signals(self):
        # ###########################################################################################################
        # ####################################### FILE ASSOCIATIONS SIGNALS #########################################
        # ###########################################################################################################
        self.ui.util_pref_form.fa_excellon_group.restore_btn.clicked.connect(
            lambda: self.restore_extensions(ext_type='excellon'))
        self.ui.util_pref_form.fa_gcode_group.restore_btn.clicked.connect(
            lambda: self.restore_extensions(ext_type='gcode'))
        self.ui.util_pref_form.fa_gerber_group.restore_btn.clicked.connect(
            lambda: self.restore_extensions(ext_type='gerber'))

        self.ui.util_pref_form.fa_excellon_group.del_all_btn.clicked.connect(
            lambda: self.delete_all_extensions(ext_type='excellon'))
        self.ui.util_pref_form.fa_gcode_group.del_all_btn.clicked.connect(
            lambda: self.delete_all_extensions(ext_type='gcode'))
        self.ui.util_pref_form.fa_gerber_group.del_all_btn.clicked.connect(
            lambda: self.delete_all_extensions(ext_type='gerber'))

        self.ui.util_pref_form.fa_excellon_group.add_btn.clicked.connect(
            lambda: self.add_extension(ext_type='excellon'))
        self.ui.util_pref_form.fa_gcode_group.add_btn.clicked.connect(
            lambda: self.add_extension(ext_type='gcode'))
        self.ui.util_pref_form.fa_gerber_group.add_btn.clicked.connect(
            lambda: self.add_extension(ext_type='gerber'))

        self.ui.util_pref_form.fa_excellon_group.del_btn.clicked.connect(
            lambda: self.del_extension(ext_type='excellon'))
        self.ui.util_pref_form.fa_gcode_group.del_btn.clicked.connect(
            lambda: self.del_extension(ext_type='gcode'))
        self.ui.util_pref_form.fa_gerber_group.del_btn.clicked.connect(
            lambda: self.del_extension(ext_type='gerber'))

        # connect the 'Apply' buttons from the Preferences/File Associations
        self.ui.util_pref_form.fa_excellon_group.exc_list_btn.clicked.connect(
            lambda: self.on_register_files(obj_type='excellon'))
        self.ui.util_pref_form.fa_gcode_group.gco_list_btn.clicked.connect(
            lambda: self.on_register_files(obj_type='gcode'))
        self.ui.util_pref_form.fa_gerber_group.grb_list_btn.clicked.connect(
            lambda: self.on_register_files(obj_type='gerber'))

        # ###########################################################################################################
        # ########################################### KEYWORDS SIGNALS ##############################################
        # ###########################################################################################################
        self.ui.util_pref_form.kw_group.restore_btn.clicked.connect(
            lambda: self.restore_extensions(ext_type='keyword'))
        self.ui.util_pref_form.kw_group.del_all_btn.clicked.connect(
            lambda: self.delete_all_extensions(ext_type='keyword'))
        self.ui.util_pref_form.kw_group.add_btn.clicked.connect(
            lambda: self.add_extension(ext_type='keyword'))
        self.ui.util_pref_form.kw_group.del_btn.clicked.connect(
            lambda: self.del_extension(ext_type='keyword'))

    def _extension_words(self):
        """The file extensions the app opens: its lists and the file associations of the Preferences."""
        words = self.grb_list + self.exc_list + self.gcode_list + self.svg_list + self.dxf_list + self.pdf_list
        for key in ('fa_gerber', 'fa_excellon', 'fa_gcode'):
            words = words + _split_words(self.options.get(key))
        # one letter extensions (h, i) would only disturb the typing
        return [word for word in words if len(word) > 1]

    def _color_words(self):
        # the object names first, where prepend_keyword() puts them
        return _unique(self.object_names + self.tcl_commands_list + self.tcl_options_list + list(self.preprocessors) +
                       BOOLEAN_WORDS + APP_FILE_TYPES + self._extension_words() + self.folder_names +
                       self.user_keywords + self.tcl_keywords)

    def _preference_names(self):
        """The names of the preferences, for set_sys and get_sys."""
        return sorted(key for key in self.options if isinstance(key, str))

    def _all_keywords(self):
        # the preference names last: they are completed, but they are too many to color in the script editor
        return _unique(self._color_words() + self._preference_names())

    @property
    def color_keywords(self):
        """
        The words that the script editor colors: the Tcl words, the Tcl commands of the app with their options and
        the preprocessors, but not the preference names, the file extensions, the folder names and the user name
        (the auto-completer has them).
        """
        tcl_words = set(self.tcl_keywords) | set(self.tcl_commands_list) | set(self.tcl_options_list) | \
            set(self.preprocessors)
        plain = set(self._preference_names()) | set(self._extension_words()) | set(self.folder_names)
        plain -= tcl_words
        return [word for word in self.myKeywords if word not in plain]

    def register_keywords(self, keywords=None):
        if keywords is None:
            # a new shell (or the first one) has its interpreter now: take the Tcl language words from it
            words = tcl_language_words(getattr(self.shell, 'tcl', None))
            if words and words != self.tcl_keywords:
                self.tcl_keywords = words
                self.myKeywords = _unique(self._all_keywords() + self.myKeywords)
            self.shell.command_line().set_model_data(self.myKeywords)
            return
        self.myKeywords = keywords
        self.shell.command_line().set_model_data(self.myKeywords)

    def rebuild_keywords(self, kw_list):
        self.user_keywords = [word for word in kw_list if word]
        self.register_keywords(self._all_keywords())

    def prepend_keyword(self, kw, update=True):
        self.myKeywords.insert(0, kw)
        self.object_names.insert(0, kw)
        if update:
            self.register_keywords(self.myKeywords)

    def append_keyword(self, kw, update=True):
        self.myKeywords.append(kw)
        if update:
            self.register_keywords(self.myKeywords)

    def remove_keyword(self, kw, update=True):
        try:
            self.myKeywords.remove(kw)
        except ValueError:
            # the keyword was never registered; nothing to remove
            pass
        if kw in self.object_names:
            self.object_names.remove(kw)
        if update:
            self.register_keywords(self.myKeywords)

    @staticmethod
    def set_reg(name, root_pth, new_reg_path_par, value):
        # create the keys
        try:
            winreg.CreateKey(root_pth, new_reg_path_par)
            with winreg.OpenKey(root_pth, new_reg_path_par, 0, winreg.KEY_WRITE) as registry_key:
                winreg.SetValueEx(registry_key, name, 0, winreg.REG_SZ, value)
            return True
        except WindowsError:
            return False

    @staticmethod
    def delete_reg(root_pth, reg_path, key_to_del):
        # delete key in registry
        key_to_del_path = reg_path + key_to_del
        try:
            winreg.DeleteKey(root_pth, key_to_del_path)
            return True
        except WindowsError:
            return False

    def on_register_files(self, obj_type=None, keep_foreign=False):
        """
        Called whenever there is a need to register file extensions with FlatCAM.
        Windows writes the registry; Linux and macOS write only into the home folder of the user.

        :param obj_type: the type of object to be register for.
        Can be: 'gerber', 'excellon', 'gcode', 'project' or 'config'. 'geometry' is not used for the moment.
        :param keep_foreign: True for a registration that the user did not ask for (the first start): a Gerber,
        Excellon or G-code extension that another program opens (Windows) or that the system already maps to another
        MIME type (Linux) is not taken, and FlatCAM is not made the default program of these extensions (macOS)

        :return: None
        """
        self.log.debug("Manufacturing files extensions are registered with FlatCAM.")

        if sys.platform == 'win32':
            self._register_windows(obj_type, keep_foreign)
        elif sys.platform == 'darwin':
            self._register_macos(obj_type, keep_foreign)
        elif sys.platform.startswith('linux'):
            self._register_linux(obj_type, keep_foreign)

    def on_unregister_files(self, obj_type=None):
        """
        Remove what on_register_files wrote, only where it still points to FlatCAM.

        :param obj_type: 'gerber', 'excellon', 'gcode', 'project', 'config' or None for all.
        """
        if sys.platform == 'win32':
            self._unregister_windows(obj_type)
        elif sys.platform == 'darwin':
            self._unregister_macos(obj_type)
        elif sys.platform.startswith('linux'):
            self._unregister_linux(obj_type)

    def _register_windows(self, obj_type=None, keep_foreign=False):
        root_path = self._root_path()
        results = []

        if obj_type in (None, 'excellon', 'gcode', 'gerber'):
            results.append(self.register_progid(root_path, 'FlatCAM', 'FlatCAM'))

        if obj_type is None or obj_type == 'excellon':
            results.append(self.register_excellon_extension(root_path, keep_foreign))

        if obj_type is None or obj_type == 'gcode':
            results.append(self.register_gcode_extension(root_path, keep_foreign))

        if obj_type is None or obj_type == 'gerber':
            results.append(self.register_gerber_extension(root_path, keep_foreign))

        if obj_type is None or obj_type == 'project':
            results.append(self.register_progid(root_path, 'FlatCAM.Project', _("FlatCAM Project")))
            results.append(self.register_extensions(root_path, self.prj_list, 'FlatCAM.Project'))

        if obj_type is None or obj_type == 'config':
            results.append(self.register_progid(root_path, 'FlatCAM.Config', _("FlatCAM Config")))
            results.append(self.register_extensions(root_path, self.conf_list, 'FlatCAM.Config'))

        self.notify_assoc_changed()
        if all(results):
            self.inform.emit('[success] %s' % _("Extensions registered."))  # noqa
        else:
            self.inform_error(_("Could not write all file associations to the registry."))

    def _unregister_windows(self, obj_type=None):
        root_path = self._root_path()

        for key in ('excellon', 'gcode', 'gerber'):
            if obj_type is None or obj_type == key:
                ext_str = self.options.get("fa_%s" % key)
                self.unregister_extensions(root_path, [x for x in ext_str.replace(' ', '').split(',') if x],
                                           'FlatCAM')
        if obj_type is None:
            self.unregister_progid(root_path, 'FlatCAM')

        if obj_type is None or obj_type == 'project':
            self.unregister_extensions(root_path, self.prj_list, 'FlatCAM.Project')
            self.unregister_progid(root_path, 'FlatCAM.Project')

        if obj_type is None or obj_type == 'config':
            self.unregister_extensions(root_path, self.conf_list, 'FlatCAM.Config')
            self.unregister_progid(root_path, 'FlatCAM.Config')

        self.notify_assoc_changed()

    # association kinds and their Linux MIME types, macOS UTIs and Preferences widgets
    ASSOC_KEYS = ('project', 'config', 'gerber', 'excellon', 'gcode')
    ASSOC_MIME = {
        'project': 'application/x-flatcam-project',
        'config': 'application/x-flatcam-config',
        'gerber': 'application/x-flatcam-gerber',
        'excellon': 'application/x-flatcam-excellon',
        'gcode': 'application/x-flatcam-gcode',
    }
    ASSOC_UTI = {'project': 'org.flatcam.evo.project', 'config': 'org.flatcam.evo.config'}
    ASSOC_WIDGET = {
        'gerber': ('fa_gerber_group', 'grb_list_text'),
        'excellon': ('fa_excellon_group', 'exc_list_text'),
        'gcode': ('fa_gcode_group', 'gco_list_text'),
    }
    LINUX_XML = 'flatcam-evo.xml'
    LINUX_DESKTOP = 'flatcam-evo.desktop'
    LINUX_ICON = 'flatcam-evo'
    MAC_BUNDLE_ID = 'org.flatcam.evo'  # the id of the release .app
    MAC_STUB_ID = 'org.flatcam.evo.launcher'  # the stub bundle of a source run has its own id
    MAC_LSREGISTER = ('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/'
                      'Support/lsregister')

    @staticmethod
    def assoc_title(key):
        return {'project': _("FlatCAM Project"), 'config': _("FlatCAM Config"), 'gerber': _("Gerber file"),
                'excellon': _("Excellon file"), 'gcode': _("G-code file")}[key]

    def assoc_keys(self, obj_type):
        if obj_type is None:
            return list(self.ASSOC_KEYS)
        return [obj_type] if obj_type in self.ASSOC_KEYS else []

    def assoc_exts(self, key):
        """The extensions of one association kind: fixed for project/config, from the Preferences for the rest."""
        if key == 'project':
            return list(self.prj_list)
        if key == 'config':
            return list(self.conf_list)
        group, widget = self.ASSOC_WIDGET[key]
        text = getattr(getattr(self.ui.util_pref_form, group), widget).get_value()
        exts = []
        for ext in text.replace(' ', '').split(','):
            ext = ext.lstrip('.')
            if ext and ext.isalnum() and ext.lower() not in exts:
                exts.append(ext.lower())
        return exts

    @staticmethod
    def launcher_argv():
        """The command (list) that starts this installation: the AppImage file, the frozen executable, or python +
        flatcam.py."""
        if getattr(sys, 'frozen', False):
            # an AppImage runs from a temporary mount that is gone after the exit: start the .AppImage file itself
            image = os.environ.get('APPIMAGE') if sys.platform.startswith('linux') else None
            if image and os.path.isfile(image):
                return [os.path.abspath(image)]
            return [os.path.abspath(sys.executable)]
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'flatcam.py')
        return [sys.executable, script]

    @staticmethod
    def png_icon_path():
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, 'assets', 'resources', 'app256.png')

    @staticmethod
    def run_tool(name, *args):
        """Run an optional system tool if it is installed; never raises. Returns True if it was started."""
        exe = shutil.which(name)
        if not exe:
            return False
        try:
            subprocess.run([exe, *args], check=False, timeout=30, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            return False
        return True

    def inform_error(self, err):
        self.inform.emit('[ERROR_NOTCL] %s: %s' % (_("File associations failed"), err))  # noqa

    # ################################################ Linux ####################################################
    @staticmethod
    def desktop_quote(arg):
        """Quote one Exec argument per the Desktop Entry specification (and escape backslashes for the file)."""
        arg = arg.replace('%', '%%')
        if arg and not any(c in arg for c in ' \t\n"\'\\<>~|&;$*?#()`'):
            return arg
        for ch in ('\\', '"', '`', '$'):
            arg = arg.replace(ch, '\\' + ch)
        return '"%s"' % arg.replace('\\', '\\\\')

    def linux_paths(self):
        data = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
        mime = os.path.join(data, 'mime')
        apps = os.path.join(data, 'applications')
        icons = os.path.join(data, 'icons', 'hicolor')
        return {'mime': mime, 'xml': os.path.join(mime, 'packages', self.LINUX_XML),
                'apps': apps, 'desktop': os.path.join(apps, self.LINUX_DESKTOP),
                'icons': icons, 'icon': os.path.join(icons, '256x256', 'apps', self.LINUX_ICON + '.png')}

    def linux_foreign_extension(self, ext):
        """True if the system MIME database maps the extension to a type that is not ours (and not 'unknown')."""
        ours = set(self.ASSOC_MIME.values())
        for mime in QtCore.QMimeDatabase().mimeTypesForFileName('x.' + ext):
            if mime.name() not in ours and mime.name() != 'application/octet-stream':
                return True
        return False

    def linux_read_state(self, xml_path):
        """What an earlier registration wrote: {key: [extensions]}."""
        by_mime = {v: k for k, v in self.ASSOC_MIME.items()}
        state = {}
        try:
            root = ET.parse(xml_path).getroot()
        except (OSError, ET.ParseError):
            return state
        for node in root:
            key = by_mime.get(node.get('type'))
            if key is None:
                continue
            patterns = [g.get('pattern', '') for g in node if g.tag.endswith('glob')]
            state[key] = [p[2:] for p in patterns if p.startswith('*.')]
        return state

    def linux_write(self, paths, state):
        previous = {}
        for key in ('icon', 'xml', 'desktop'):
            path = paths[key]
            try:
                with open(path, 'rb') as fh:
                    previous[path] = fh.read()
            except FileNotFoundError:
                previous[path] = None
        try:
            self._linux_write(paths, state)
        except OSError:
            # A failed update must not leave a new association or destroy an earlier user file.
            for path, content in previous.items():
                if content is None:
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass
                elif os.path.isfile(path):
                    with open(path, 'rb') as fh:
                        unchanged = fh.read() == content
                    if not unchanged:
                        with open(path, 'wb') as fh:
                            fh.write(content)
            raise

    def _linux_write(self, paths, state):
        # Validate/copy the icon before publishing MIME types and their desktop handler.
        os.makedirs(os.path.dirname(paths['icon']), exist_ok=True)
        shutil.copyfile(self.png_icon_path(), paths['icon'])
        ns = 'http://www.freedesktop.org/standards/shared-mime-info'
        ET.register_namespace('', ns)
        root = ET.Element('{%s}mime-info' % ns)
        for key in self.ASSOC_KEYS:
            if key not in state:
                continue
            node = ET.SubElement(root, '{%s}mime-type' % ns, type=self.ASSOC_MIME[key])
            ET.SubElement(node, '{%s}comment' % ns).text = self.assoc_title(key)
            for ext in state[key]:
                ET.SubElement(node, '{%s}glob' % ns, pattern='*.%s' % ext, **{'case-sensitive': 'false'})
        os.makedirs(os.path.dirname(paths['xml']), exist_ok=True)
        ET.ElementTree(root).write(paths['xml'], encoding='UTF-8', xml_declaration=True)

        mimes = [self.ASSOC_MIME[k] for k in self.ASSOC_KEYS if k in state]
        exec_line = ' '.join([self.desktop_quote(a) for a in self.launcher_argv()] + ['%F'])
        desktop = '\n'.join([
            '[Desktop Entry]',
            'Type=Application',
            'Name=FlatCAM EVO',
            'GenericName=PCB CAM',
            'Comment=G-code for PCB manufacturing from Gerber and Excellon files',
            'Exec=%s' % exec_line,
            'Icon=%s' % self.LINUX_ICON,
            'Terminal=false',
            'StartupNotify=true',
            'Categories=Graphics;Engineering;Electronics;',
            'MimeType=%s;' % ';'.join(mimes),
            'X-FlatCAM-Evo=true',
            ''])
        os.makedirs(paths['apps'], exist_ok=True)
        with open(paths['desktop'], 'w', encoding='utf-8') as fh:
            fh.write(desktop)

    def linux_refresh(self, paths, default_mimes=()):
        self.run_tool('update-mime-database', paths['mime'])
        self.run_tool('update-desktop-database', paths['apps'])
        self.run_tool('gtk-update-icon-cache', '-f', '-t', paths['icons'])
        for mime in default_mimes:
            self.run_tool('xdg-mime', 'default', self.LINUX_DESKTOP, mime)

    def _register_linux(self, obj_type=None, keep_foreign=False):
        paths = self.linux_paths()
        keys = self.assoc_keys(obj_type)
        state = self.linux_read_state(paths['xml'])
        for key in keys:
            exts = self.assoc_exts(key)
            if keep_foreign and key not in self.ASSOC_UTI:
                # first start: do not take what the system already opens with another type
                exts = [e for e in exts if not self.linux_foreign_extension(e)]
            if exts:
                state[key] = exts
            else:
                state.pop(key, None)
        try:
            if state:
                self.linux_write(paths, state)
            else:
                self.linux_remove(paths)
        except OSError as err:
            self.inform_error(err)
            return
        self.linux_refresh(paths, [self.ASSOC_MIME[k] for k in keys if k in state])
        self.inform.emit('[success] %s' % _("Extensions registered."))  # noqa

    def linux_remove(self, paths):
        owned_icon = False
        if not os.path.islink(paths['icon']):
            try:
                with open(paths['icon'], 'rb') as installed, open(self.png_icon_path(), 'rb') as expected:
                    owned_icon = installed.read() == expected.read()
            except OSError:
                pass
        for path in (paths['xml'], paths['desktop']) + ((paths['icon'],) if owned_icon else ()):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def _unregister_linux(self, obj_type=None):
        paths = self.linux_paths()
        state = self.linux_read_state(paths['xml'])
        for key in self.assoc_keys(obj_type):
            state.pop(key, None)
        try:
            if state:
                self.linux_write(paths, state)
            else:
                self.linux_remove(paths)
        except OSError as err:
            self.inform_error(err)
            return
        self.linux_refresh(paths)
        self.inform.emit('[success] %s' % _("Extensions unregistered."))  # noqa

    # ################################################ macOS ####################################################
    def mac_bundle_path(self):
        return os.path.join(os.path.expanduser('~'), 'Applications', 'FlatCAM EVO.app')

    @staticmethod
    def mac_running_bundle():
        """The .app folder that this frozen program runs from (the release), or None for a source run."""
        if not getattr(sys, 'frozen', False):
            return None
        exe = os.path.abspath(sys.executable)
        head, sep, _tail = exe.partition('.app' + os.sep + 'Contents' + os.sep + 'MacOS' + os.sep)
        return head + '.app' if sep else None

    def mac_lsregister(self):
        return self.MAC_LSREGISTER if os.path.isfile(self.MAC_LSREGISTER) else None

    def mac_remove_own_stub(self):
        """Remove the stub bundle in ~/Applications if FlatCAM wrote it (a bundle of another program is kept)."""
        stub = self.mac_bundle_path()
        running = self.mac_running_bundle()
        if running and os.path.realpath(stub) == os.path.realpath(running):
            return
        if os.path.islink(stub) or not os.path.isdir(stub) or self.mac_read_state(
                os.path.join(stub, 'Contents', 'Info.plist')) is None:
            return
        lsregister = self.mac_lsregister()
        if lsregister:
            self.run_tool(lsregister, '-u', stub)
        shutil.rmtree(stub, ignore_errors=True)

    def mac_read_state(self, plist_path):
        """{key: [extensions]} from our bundle; None if the bundle belongs to somebody else."""
        try:
            with open(plist_path, 'rb') as fh:
                info = plistlib.load(fh)
        except (OSError, plistlib.InvalidFileException, ValueError):
            return None
        if not isinstance(info, dict):
            return None
        if info.get('CFBundleIdentifier') == self.MAC_BUNDLE_ID:
            contents = os.path.dirname(plist_path)
            bundle = os.path.dirname(contents)
            script = os.path.join(contents, 'MacOS', 'flatcam-evo')
            running = self.mac_running_bundle()
            if (any(os.path.islink(path) for path in (bundle, contents, plist_path,
                                                     os.path.dirname(script), script)) or
                    (running and os.path.realpath(bundle) == os.path.realpath(running)) or
                    info.get('CFBundleExecutable') != 'flatcam-evo' or
                    not isinstance(info.get('FlatCAMAssociations'), dict)):
                return None
            try:
                with open(script, encoding='utf-8') as fh:
                    lines = fh.read().splitlines()
                argv = shlex.split(lines[1][5:]) if len(lines) == 2 and lines[1].startswith('exec ') else []
                if (lines[0] != '#!/bin/sh' or len(argv) != 3 or argv[-1] != '$@' or
                        not os.path.isabs(argv[0]) or not os.path.isabs(argv[1]) or
                        not os.path.basename(argv[0]).lower().startswith('python') or
                        os.path.basename(argv[1]) != 'flatcam.py'):
                    return None
            except (OSError, UnicodeError, ValueError, IndexError):
                return None
        elif info.get('CFBundleIdentifier') != self.MAC_STUB_ID:
            return None
        return {k: list(v) for k, v in info.get('FlatCAMAssociations', {}).items() if k in self.ASSOC_KEYS}

    def mac_info(self, state, icon_name=None):
        doc_types = []
        exported = []
        for key in self.ASSOC_KEYS:
            if key not in state:
                continue
            doc = {'CFBundleTypeName': self.assoc_title(key), 'CFBundleTypeRole': 'Editor',
                   'LSHandlerRank': 'Owner' if key in self.ASSOC_UTI else 'Alternate'}
            if key in self.ASSOC_UTI:
                doc['LSItemContentTypes'] = [self.ASSOC_UTI[key]]
                exported.append({'UTTypeIdentifier': self.ASSOC_UTI[key], 'UTTypeDescription': self.assoc_title(key),
                                 'UTTypeConformsTo': ['public.data'],
                                 'UTTypeTagSpecification': {'public.filename-extension': list(state[key])}})
            else:
                doc['CFBundleTypeExtensions'] = list(state[key])
            doc_types.append(doc)
        info = {'CFBundleIdentifier': self.MAC_STUB_ID, 'CFBundleName': 'FlatCAM EVO',
                'CFBundleDisplayName': 'FlatCAM EVO', 'CFBundleExecutable': 'flatcam-evo',
                'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1.0', 'CFBundleShortVersionString': '1.0',
                'NSHighResolutionCapable': True, 'CFBundleDocumentTypes': doc_types,
                'FlatCAMAssociations': {k: list(v) for k, v in state.items()}}
        if exported:
            info['UTExportedTypeDeclarations'] = exported
        if icon_name:
            info['CFBundleIconFile'] = icon_name
        return info

    def mac_write(self, bundle, state):
        contents = os.path.join(bundle, 'Contents')
        running = self.mac_running_bundle()
        if os.path.lexists(bundle) and (os.path.islink(bundle) or
                (running and os.path.realpath(bundle) == os.path.realpath(running)) or
                self.mac_read_state(os.path.join(contents, 'Info.plist')) is None):
            raise OSError(_("%s is not a source launcher bundle.") % bundle)
        os.makedirs(os.path.join(contents, 'MacOS'), exist_ok=True)

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        icon_name = None
        for folder in ('macos', 'resources'):
            folder = os.path.join(base, 'assets', folder)
            icons = sorted(f for f in os.listdir(folder) if f.endswith('.icns')) if os.path.isdir(folder) else []
            if icons:
                os.makedirs(os.path.join(contents, 'Resources'), exist_ok=True)
                shutil.copyfile(os.path.join(folder, icons[0]), os.path.join(contents, 'Resources', icons[0]))
                icon_name = icons[0]
                break

        import tempfile
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=contents, delete=False) as fh:
                temporary = fh.name
                plistlib.dump(self.mac_info(state, icon_name), fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, os.path.join(contents, 'Info.plist'))
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)

        script = os.path.join(contents, 'MacOS', 'flatcam-evo')
        with open(script, 'w', encoding='utf-8') as fh:
            fh.write('#!/bin/sh\nexec %s "$@"\n' % ' '.join(shlex.quote(a) for a in self.launcher_argv()))
        os.chmod(script, 0o755)

    def _register_macos(self, obj_type=None, keep_foreign=False):
        real = self.mac_running_bundle()
        bundle = real or self.mac_bundle_path()
        keys = self.assoc_keys(obj_type)
        # the release has its own Info.plist and is never rewritten; a source run writes a stub bundle
        state = {} if real or not os.path.lexists(bundle) else self.mac_read_state(
            os.path.join(bundle, 'Contents', 'Info.plist'))
        if not real and os.path.islink(bundle):
            state = None
        if state is None:
            self.inform_error(_("%s belongs to another application.") % bundle)
            return
        for key in keys:
            exts = self.assoc_exts(key)
            if exts:
                state[key] = exts
            else:
                state.pop(key, None)
        try:
            if real:
                # Only the dedicated source launcher can be removed; release bundles are never ours to change.
                self.mac_remove_own_stub()
            elif state:
                self.mac_write(bundle, state)
            else:
                self.mac_remove_own_stub()
        except OSError as err:
            self.inform_error(err)
            return

        lsregister = self.mac_lsregister()
        if lsregister and (state or real):
            self.run_tool(lsregister, '-f', bundle)
        if shutil.which('duti'):
            for key in keys:
                if key not in state or (keep_foreign and key not in self.ASSOC_UTI):
                    continue
                targets = [self.ASSOC_UTI[key]] if key in self.ASSOC_UTI else ['.%s' % e for e in state[key]]
                for target in targets:
                    self.run_tool('duti', '-s', self.MAC_BUNDLE_ID if real else self.MAC_STUB_ID, target, 'all')
        else:
            self.inform.emit('[warning] %s' % _(  # noqa
                'To open these files with FlatCAM by default, select one in Finder and use '
                '"Get Info > Open With" followed by "Change All".'))
        self.inform.emit('[success] %s' % _("Extensions registered."))  # noqa

    def _unregister_macos(self, obj_type=None):
        if self.mac_running_bundle():
            # the release bundle is not ours to change, and duti can not remove a default program
            self.mac_remove_own_stub()
            self.inform.emit('[WARNING_NOTCL] %s' % _(  # noqa
                'The FlatCAM application keeps its file types. To open these files with another program, select '
                'one in Finder and use "Get Info > Open With" followed by "Change All".'))
            return
        bundle = self.mac_bundle_path()
        if os.path.islink(bundle):
            return
        state = self.mac_read_state(os.path.join(bundle, 'Contents', 'Info.plist'))
        if not state:
            return
        for key in self.assoc_keys(obj_type):
            state.pop(key, None)
        lsregister = self.mac_lsregister()
        try:
            if state:
                self.mac_write(bundle, state)
                if lsregister:
                    self.run_tool(lsregister, '-f', bundle)
            else:
                self.mac_remove_own_stub()
        except OSError as err:
            self.inform_error(err)
            return
        self.inform.emit('[success] %s' % _("Extensions unregistered."))  # noqa

    @staticmethod
    def _root_path():
        try:
            is_admin = os.getuid() == 0
        except AttributeError:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() == 1
        return winreg.HKEY_LOCAL_MACHINE if is_admin else winreg.HKEY_CURRENT_USER

    @staticmethod
    def get_reg(root_pth, reg_path, name=''):
        try:
            with winreg.OpenKey(root_pth, reg_path) as registry_key:
                return winreg.QueryValueEx(registry_key, name)[0]
        except OSError:
            return None

    @staticmethod
    def launcher_command():
        """
        The command line that opens a file with this installation: the frozen executable, or pythonw + flatcam.py.
        """
        if getattr(sys, 'frozen', False):
            return '"%s" "%%1"' % os.path.normpath(sys.executable)

        exe = sys.executable
        pythonw = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        if os.path.isfile(pythonw):
            exe = pythonw
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'flatcam.py')
        return '"%s" "%s" "%%1"' % (os.path.normpath(exe), os.path.normpath(script))

    @staticmethod
    def icon_path():
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # made from app256.png, as the icon of FlatCAM.exe (make_freeze.make_app_icon)
        return os.path.join(base, 'assets', 'resources', 'app256.ico')

    @staticmethod
    def notify_assoc_changed():
        try:
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
        except (AttributeError, OSError):
            pass

    def register_progid(self, root_path, progid, friendly_name):
        base = 'Software\\Classes\\' + progid
        results = [self.set_reg('', root_pth=root_path, new_reg_path_par=base, value=friendly_name),
                   self.set_reg('', root_pth=root_path, new_reg_path_par=base + '\\DefaultIcon',
                                value=self.icon_path() + ',0'),
                   self.set_reg('', root_pth=root_path, new_reg_path_par=base + '\\shell\\open\\command',
                                value=self.launcher_command())]
        return all(results)

    def refresh_windows_icons(self):
        """
        Point the FlatCAM file types that this installation registered to the current icon: an earlier version
        wrote the old FlatCAM icon. A file type that another installation registered is not changed.
        """
        root_path = self._root_path()
        icon = self.icon_path() + ',0'
        changed = False
        for progid in ('FlatCAM', 'FlatCAM.Project', 'FlatCAM.Config'):
            base = 'Software\\Classes\\' + progid
            if self.get_reg(root_path, base + '\\shell\\open\\command') != self.launcher_command():
                continue
            if self.get_reg(root_path, base + '\\DefaultIcon') != icon:
                self.set_reg('', root_pth=root_path, new_reg_path_par=base + '\\DefaultIcon', value=icon)
                changed = True
        if changed:
            self.notify_assoc_changed()

    def unregister_progid(self, root_path, progid):
        base = 'Software\\Classes\\' + progid
        command = self.get_reg(root_path, base + '\\shell\\open\\command')
        if command is None:
            return
        if command != self.launcher_command() and 'flatcam' not in command.lower():
            return
        for sub in ('\\shell\\open\\command', '\\shell\\open', '\\shell', '\\DefaultIcon', ''):
            self.delete_reg(root_pth=root_path, reg_path=base, key_to_del=sub)

    def register_extensions(self, root_path, ext_list, progid):
        success = True
        for ext in ext_list:
            success = self.set_reg('', root_pth=root_path, new_reg_path_par='Software\\Classes\\.%s' % ext,
                                   value=progid) and success
        return success

    def unregister_extensions(self, root_path, ext_list, progid):
        # only the extensions that still point to our progid
        for ext in ext_list:
            ext_key = 'Software\\Classes\\.%s' % ext
            if self.get_reg(root_path, ext_key) == progid:
                self.delete_reg(root_pth=root_path, reg_path='', key_to_del=ext_key)

    def foreign_extension(self, ext):
        """True if the extension opens with another program (HKEY_CLASSES_ROOT names a class that is not FlatCAM)."""
        return self.get_reg(winreg.HKEY_CLASSES_ROOT, '.%s' % ext) not in (None, '', 'FlatCAM')

    def register_excellon_extension(self, root_path, keep_foreign=False):
        new_reg_path = 'Software\\Classes\\'
        success = True

        exc_list = \
            self.ui.util_pref_form.fa_excellon_group.exc_list_text.get_value().replace(' ', '').split(',')
        exc_list = [x for x in exc_list if x != '']

        # register all keys in the Preferences window
        for ext in exc_list:
            if keep_foreign and self.foreign_extension(ext):
                continue
            new_k = new_reg_path + '.%s' % ext
            success = self.set_reg('', root_pth=root_path, new_reg_path_par=new_k, value='FlatCAM') and success

        # and unregister those that are no longer in the Preferences windows but are in the file, only where they
        # still point to FlatCAM (another program may have the extension now)
        for ext in self.options.get("fa_excellon").replace(' ', '').split(','):
            if ext not in exc_list and self.get_reg(root_path, new_reg_path + '.%s' % ext) == 'FlatCAM':
                self.delete_reg(root_pth=root_path, reg_path=new_reg_path, key_to_del='.%s' % ext)

        # now write the updated extensions to the self.options
        # new_ext = ''
        # for ext in exc_list:
        #     new_ext = new_ext + ext + ', '
        # self.options["fa_excellon"] = new_ext
        return success

    def register_gerber_extension(self, root_path, keep_foreign=False):
        new_reg_path = 'Software\\Classes\\'
        success = True

        grb_list = self.ui.util_pref_form.fa_gerber_group.grb_list_text.get_value().replace(' ', '').split(',')
        grb_list = [x for x in grb_list if x != '']

        # register all keys in the Preferences window
        for ext in grb_list:
            if keep_foreign and self.foreign_extension(ext):
                continue
            new_k = new_reg_path + '.%s' % ext
            success = self.set_reg('', root_pth=root_path, new_reg_path_par=new_k, value='FlatCAM') and success

        # and unregister those that are no longer in the Preferences windows but are in the file, only where they
        # still point to FlatCAM (another program may have the extension now)
        for ext in self.options.get("fa_gerber").replace(' ', '').split(','):
            if ext not in grb_list and self.get_reg(root_path, new_reg_path + '.%s' % ext) == 'FlatCAM':
                self.delete_reg(root_pth=root_path, reg_path=new_reg_path, key_to_del='.%s' % ext)

        return success

    def register_gcode_extension(self, root_path, keep_foreign=False):
        new_reg_path = 'Software\\Classes\\'
        success = True

        gco_list = self.ui.util_pref_form.fa_gcode_group.gco_list_text.get_value().replace(' ', '').split(',')
        gco_list = [x for x in gco_list if x != '']

        # register all keys in the Preferences window
        for ext in gco_list:
            if keep_foreign and self.foreign_extension(ext):
                continue
            new_k = new_reg_path + '.%s' % ext
            success = self.set_reg('', root_pth=root_path, new_reg_path_par=new_k, value='FlatCAM') and success

        # and unregister those that are no longer in the Preferences windows but are in the file, only where they
        # still point to FlatCAM (another program may have the extension now)
        for ext in self.options.get("fa_gcode").replace(' ', '').split(','):
            if ext not in gco_list and self.get_reg(root_path, new_reg_path + '.%s' % ext) == 'FlatCAM':
                self.delete_reg(root_pth=root_path, reg_path=new_reg_path, key_to_del='.%s' % ext)

        return success

    def add_extension(self, ext_type):
        """
        Add a file extension to the list for a specific object

        :param ext_type: type of FlatCAM object: excellon, gerber, geometry and then 'not FlatCAM object' keyword
        :return:
        """

        if ext_type == 'excellon':
            self.add_excellon_extension()
        if ext_type == 'gcode':
            self.add_gcode_extension()
        if ext_type == 'gerber':
            self.add_gerber_extension()
        if ext_type == 'keyword':
            self.add_keyword_in_preferences_gui()

    def add_excellon_extension(self):
        new_ext = self.ui.util_pref_form.fa_excellon_group.ext_entry.get_value()
        if new_ext == '':
            return

        old_val = self.ui.util_pref_form.fa_excellon_group.exc_list_text.get_value().replace(' ', '').split(',')
        if new_ext in old_val:
            return
        old_val.append(new_ext)
        old_val.sort()
        self.ui.util_pref_form.fa_excellon_group.exc_list_text.set_value(', '.join(old_val))

    def add_gerber_extension(self):
        new_ext = self.ui.util_pref_form.fa_gerber_group.ext_entry.get_value()
        if new_ext == '':
            return

        old_val = self.ui.util_pref_form.fa_gerber_group.grb_list_text.get_value().replace(' ', '').split(',')
        if new_ext in old_val:
            return
        old_val.append(new_ext)
        old_val.sort()
        self.ui.util_pref_form.fa_gerber_group.grb_list_text.set_value(', '.join(old_val))

    def add_gcode_extension(self):
        new_ext = self.ui.util_pref_form.fa_gcode_group.ext_entry.get_value()
        if new_ext == '':
            return

        old_val = self.ui.util_pref_form.fa_gcode_group.gco_list_text.get_value().replace(' ', '').split(',')
        if new_ext in old_val:
            return
        old_val.append(new_ext)
        old_val.sort()
        self.ui.util_pref_form.fa_gcode_group.gco_list_text.set_value(', '.join(old_val))

    def add_keyword_in_preferences_gui(self, kw=None):
        new_kw = self.ui.util_pref_form.kw_group.kw_entry.get_value() if kw is None else kw
        if new_kw == '':
            return

        new_kw = new_kw.strip()
        old_val = _split_words(self.ui.util_pref_form.kw_group.kw_list_text.get_value())
        if not new_kw or new_kw in old_val:
            return
        old_val.append(new_kw)
        old_val.sort()
        self.ui.util_pref_form.kw_group.kw_list_text.set_value(', '.join(old_val))

        # update the self.myKeywords so the model is updated
        self.rebuild_keywords(old_val)

    def del_extension(self, ext_type):
        """
        Remove a file extension from the list for a specific object

        :param ext_type: type of FlatCAM object: excellon, gerber, geometry and then 'not FlatCAM object' keyword
        :return:
        """
        if ext_type == 'excellon':
            self.delete_excellon_extension()
        if ext_type == 'gcode':
            self.delete_gcode_extension()
        if ext_type == 'gerber':
            self.delete_gerber_extension()
        if ext_type == 'keyword':
            self.delete_keyword_in_preferences_gui()

    def delete_excellon_extension(self):
        new_ext = self.ui.util_pref_form.fa_excellon_group.ext_entry.get_value()
        if new_ext == '':
            return

        old_val = self.ui.util_pref_form.fa_excellon_group.exc_list_text.get_value().replace(' ', '').split(',')
        if new_ext not in old_val:
            return
        old_val.remove(new_ext)
        old_val.sort()
        self.ui.util_pref_form.fa_excellon_group.exc_list_text.set_value(', '.join(old_val))

    def delete_gerber_extension(self):
        new_ext = self.ui.util_pref_form.fa_gerber_group.ext_entry.get_value()
        if new_ext == '':
            return

        old_val = self.ui.util_pref_form.fa_gerber_group.grb_list_text.get_value().replace(' ', '').split(',')
        if new_ext not in old_val:
            return
        old_val.remove(new_ext)
        old_val.sort()
        self.ui.util_pref_form.fa_gerber_group.grb_list_text.set_value(', '.join(old_val))

    def delete_gcode_extension(self):
        new_ext = self.ui.util_pref_form.fa_gcode_group.ext_entry.get_value()
        if new_ext == '':
            return

        old_val = self.ui.util_pref_form.fa_gcode_group.gco_list_text.get_value().replace(' ', '').split(',')
        if new_ext not in old_val:
            return
        old_val.remove(new_ext)
        old_val.sort()
        self.ui.util_pref_form.fa_gcode_group.gco_list_text.set_value(', '.join(old_val))

    def delete_keyword_in_preferences_gui(self, kw=None):
        new_kw = kw if kw is not None else self.ui.util_pref_form.kw_group.kw_entry.get_value()
        if new_kw == '':
            return

        new_kw = new_kw.strip()
        old_val = _split_words(self.ui.util_pref_form.kw_group.kw_list_text.get_value())
        if new_kw not in old_val:
            return
        old_val.remove(new_kw)
        old_val.sort()
        self.ui.util_pref_form.kw_group.kw_list_text.set_value(', '.join(old_val))

        # update the self.myKeywords so the model is updated
        self.rebuild_keywords(old_val)

    def restore_extensions(self, ext_type):
        """
        Restore all file extensions associations with FlatCAM, for a specific object

        :param ext_type: type of FlatCAM object: excellon, gerber, geometry and then 'not FlatCAM object' keyword
        :return:
        """

        if ext_type == 'excellon':
            # don't add 'txt' to the associations (too many files are .txt and not Excellon) but keep it in the list
            # for the ability to open Excellon files with .txt extension
            new_exc_list = deepcopy(self.exc_list)

            try:
                new_exc_list.remove('txt')
            except ValueError:
                pass
            self.ui.util_pref_form.fa_excellon_group.exc_list_text.set_value(', '.join(new_exc_list))
        if ext_type == 'gcode':
            # don't add 'h' and 'i' to the associations (C header and preprocessed C files) but keep them in the list
            # for the ability to open G-code files with these extensions
            self.ui.util_pref_form.fa_gcode_group.gco_list_text.set_value(
                ', '.join(ext for ext in self.gcode_list if ext not in ('h', 'i')))
        if ext_type == 'gerber':
            self.ui.util_pref_form.fa_gerber_group.grb_list_text.set_value(', '.join(self.grb_list))
        if ext_type == 'keyword':
            self.ui.util_pref_form.kw_group.kw_list_text.set_value(', '.join(self.default_keywords))

            # update the self.myKeywords so the model is updated
            self.rebuild_keywords(self.default_keywords)

    def delete_all_extensions(self, ext_type):
        """
        Delete all file extensions associations with FlatCAM, for a specific object

        :param ext_type: type of FlatCAM object: excellon, gerber, geometry and then 'not FlatCAM object' keyword
        :return:
        """

        if ext_type == 'excellon':
            self.ui.util_pref_form.fa_excellon_group.exc_list_text.set_value('')
        if ext_type == 'gcode':
            self.ui.util_pref_form.fa_gcode_group.gco_list_text.set_value('')
        if ext_type == 'gerber':
            self.ui.util_pref_form.fa_gerber_group.grb_list_text.set_value('')
        if ext_type == 'keyword':
            self.ui.util_pref_form.kw_group.kw_list_text.set_value('')

            # update the self.myKeywords so the model is updated
            self.rebuild_keywords([])
