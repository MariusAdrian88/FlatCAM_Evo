
from appHandlers.appHistory import history_of
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from appEditors.appExcEditor import AppExcEditor
from appEditors.appGeoEditor import AppGeoEditor
from appEditors.appGerberEditor import AppGerberEditor

from appGUI.GUIElements import FCFileSaveDialog, FCMessageBox, add_case_variants
from appObjects.ProjectContext import same_path
from camlib import dict2obj, ParseError, geometry_wkt_cache, project_json_default
from defaults import project_options, clean_project_options, normalize_units
from appParsers.ParseHPGL2 import HPGL2
from appParsers.ParseGerber import Gerber

from appObjects.ObjectCollection import GerberObject, ExcellonObject, GeometryObject, ScriptObject, CNCJobObject
from appObjects.AppObject import ObjectLoadBatch

from reportlab.graphics import renderPDF
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch, mm
from reportlab.lib.pagesizes import landscape, portrait
from svglib.svglib import svg2rlg
from xml.dom.minidom import parseString as parse_xml_string
from lxml import etree as ET

import html
import time
import sys
import os
from types import SimpleNamespace
from copy import deepcopy
import re
from shapely.affinity import scale
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

import numpy as np
try:
    from numpy import Inf
except ImportError:
    from numpy import inf as Inf

from datetime import datetime

from appCommon.Common import LoudDict
from appCommon.ProjectFile import load_project_file, save_project_file

from vispy.gloo.util import _screenshot
from vispy.io import write_png

import traceback
from io import StringIO

# App Translation
import gettext
import appTranslation as fcTranslate
import builtins

import typing

if typing.TYPE_CHECKING:
    import appMain

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class _NoSplash:
    """Stands in for the splash screen when the app starts with a file: the splash is not created then."""
    def showMessage(self, *args, **kwargs):
        pass


class ProjectLoadError(Exception):
    """A project file cannot be loaded. The text of the exception is shown to the user."""


class appIO(QtCore.QObject):
    pcbdoc_parsed = QtCore.pyqtSignal(str, object)
    pcbdoc_outputs_ready = QtCore.pyqtSignal(str)
    project_load_requested = QtCore.pyqtSignal(object, bool, bool, object, bool)
    project_load_failed = QtCore.pyqtSignal(str)
    project_objects_requested = QtCore.pyqtSignal(object, str, object, bool)
    # emitted exactly once for each open_project() request: success, file name, names of the skipped objects
    project_load_done = QtCore.pyqtSignal(bool, str, list)
    # the reason of the last failed request, set before project_load_done is emitted
    last_load_error = ''
    # emitted by the save task of File -> Save and Save As: filename, success, make_copy, was_dirty
    project_save_done = QtCore.pyqtSignal(str, bool, bool, bool)

    def __init__(self, app):
        """
        A class that holds all the menu -> file handlers
        """
        super().__init__()

        self.app = app
        self.log = self.app.log
        self.inform = self.app.inform
        # the project load that runs now (see _open_project); one at a time
        self._load = None
        self._load_reason = ''
        self.worker_task = self.app.worker_task
        self.options = self.app.options
        self.pagesize = {}
        self._proteus_import = None
        self._proteus_timer = QtCore.QTimer(self)
        self._proteus_timer.setInterval(50)
        self._proteus_timer.timeout.connect(self._advance_proteus_import)

        self.app.new_project_signal.connect(self.on_new_project_house_keeping)
        self.pcbdoc_parsed.connect(self.on_pcbdoc_parsed, Qt.ConnectionType.QueuedConnection)
        self.pcbdoc_outputs_ready.connect(self.on_pcbdoc_outputs_ready, Qt.ConnectionType.QueuedConnection)
        self.project_load_requested.connect(self._open_project)
        self.project_load_failed.connect(self._on_project_load_failed, Qt.ConnectionType.QueuedConnection)
        self.project_objects_requested.connect(self.restore_project_objects)
        self.project_save_done.connect(self._on_project_save_done)

    @property
    def app_units(self):
        # the units of the active project; a project load or a project switch changes them
        return self.app.app_units

    @property
    def splash(self):
        # read when it is used: the app creates the splash after this object, and not at all when it starts with a file
        splash = getattr(self.app, 'splash', None)
        return splash if splash is not None else _NoSplash()

    # Native PCB formats imported through the PcbDoc pipeline: kind -> file extension, file dialog filter,
    # name in the logs and whether the layer name goes before its id in the checkboxes and in the object names.
    # The dialog titles are in _pcb_title().
    PCB_FORMATS = {
        'proteus': {
            'ext': '.pdsprj', 'filter': 'Proteus Project (*.pdsprj *.PDSPRJ)',
            'log': 'Proteus PCB', 'name_first': False},
        'pcbdoc': {
            'ext': '.pcbdoc', 'filter': 'Altium PCB (*.PcbDoc *.pcbdoc)',
            'log': 'Altium PCB', 'name_first': True},
        'kicad_pcb': {
            'ext': '.kicad_pcb', 'filter': 'KiCad PCB (*.kicad_pcb)',
            'log': 'KiCad PCB', 'name_first': False},
        'sprint_lay6': {
            'ext': '.lay6', 'filter': 'Sprint Layout 6 (*.lay6)',
            'log': 'Sprint Layout', 'name_first': False},
        'eagle_brd': {
            'ext': '.brd', 'filter': 'Eagle Board (*.brd)',
            'log': 'Eagle Board', 'name_first': False},
        'allegro_brd': {
            'ext': '.brd', 'filter': 'OrCAD / Allegro Board (*.brd)',
            'log': 'Allegro Board', 'name_first': False},
    }

    @staticmethod
    def _pcb_title(kind):
        # literal _() strings so the translation tools find them
        if kind == 'proteus':
            return _('Import Proteus PCB')
        if kind == 'kicad_pcb':
            return _('Import KiCad PCB')
        if kind == 'sprint_lay6':
            return _('Import Sprint Layout')
        if kind == 'eagle_brd':
            return _('Import Eagle Board')
        if kind == 'allegro_brd':
            return _('Import OrCAD/Allegro Board')
        return _('Import Altium PCB')

    @staticmethod
    def _pcb_source_kind(filename):
        """
        Which native PCB format a file imported through the PcbDoc pipeline is: a key of PCB_FORMATS.
        Anything that is not KiCad, Sprint Layout, Eagle or Allegro is Altium. Eagle and Allegro boards both use
        the .brd extension, so the content decides: an Eagle board is XML, an Allegro board starts with a binary
        magic number. A file that cannot be read stays an Eagle board.
        """
        name = str(filename).lower()
        for kind, pcb_format in appIO.PCB_FORMATS.items():
            if name.endswith(pcb_format['ext']):
                if kind in ('eagle_brd', 'allegro_brd'):
                    return appIO._brd_source_kind(filename)
                return kind
        return 'pcbdoc'

    @staticmethod
    def _brd_source_kind(filename):
        try:
            with open(str(filename), 'rb') as handle:
                head = handle.read(64)
        except (OSError, ValueError):
            return 'eagle_brd'
        stripped = head.lstrip(b'\xef\xbb\xbf \t\r\n')
        if stripped and not stripped.startswith(b'<'):
            return 'allegro_brd'
        return 'eagle_brd'

    def _import_pcb_file(self, kind, name, parse_fcn):
        if name is None:
            pcb_format = self.PCB_FORMATS[kind]
            name, _filter = QtWidgets.QFileDialog.getOpenFileName(
                parent=self.app.ui, caption=self._pcb_title(kind),
                directory=self.app.get_last_folder(), filter=pcb_format['filter'])
        if name:
            self.worker_task.emit({'fcn': parse_fcn, 'params': [name]})

    def on_file_import_pcbdoc(self, name=None):
        self._import_pcb_file('pcbdoc', name, self.parse_pcbdoc)

    def on_file_import_proteus(self, name=None):
        if self._proteus_import is not None or self.app.projects.is_busy():
            self.app.projects.inform_busy()
            return
        self._import_pcb_file('proteus', name, self.parse_proteus)

    def parse_proteus(self, filename):
        def make_parser():
            from appParsers.ParseProteus import ProteusParser
            return ProteusParser(app=self.app)
        return self._parse_pcb_file('proteus', filename, make_parser)

    def _advance_proteus_import(self):
        """Advance only on the GUI thread, after construction, insertion and plotting have drained."""
        state = self._proteus_import
        if state is None or state['selecting'] or not state['finished'] or self.app.projects.is_busy():
            return
        if self.app.abort_flag or state['failed'] or not state['boards']:
            self._proteus_timer.stop()
            self._proteus_import = None
            if self.app.abort_flag or state['failed']:
                self.inform.emit('[WARNING_NOTCL] %s' % _(
                    'Proteus import stopped. Previously imported boards remain open; the import is incomplete.'))
            elif state['imported']:
                self.app.file_opened.emit('proteus', state['filename'])
            return

        board = state['boards'].pop(0)
        state['selecting'] = True
        try:
            selection = self.on_pcbdoc_parsed(state['filename'], board)
            if selection is None or self.app.abort_flag:
                state['failed'] = True
                return
            layers, drills = selection
            if not (layers or drills):
                state['failed'] = True
                return
            from appObjects.ProjectContext import unique_name, is_valid_name
            fallback = os.path.splitext(os.path.basename(board.member_name))[0]
            fallback = fallback.replace('\\', '_').replace('/', '_').strip()
            name = (board.name or fallback).strip()
            if not is_valid_name(name):
                name = fallback if is_valid_name(fallback) else 'Proteus board'
            ctx = self.app.projects.new()
            if ctx is None:
                state['failed'] = True
                return
            # Use the manager's native unique-name policy for repeated board titles.
            if not self.app.projects.rename(ctx, unique_name(name, self.app.projects.names())):
                raise ValueError(_('Invalid Proteus board name: %s') % name)
            self.app.should_we_save = True
            ctx.should_we_save = True
            from appObjects.AppObject import ObjectLoadBatch

            def finished(failed):
                state['failed'] = failed
                state['finished'] = True
                state['imported'] = state['imported'] or bool(ctx.collection.get_list())

            # Firmware belongs to the whole Proteus project: open it once, with the first imported board.
            firmware, state['firmware'] = state['firmware'], []
            batch = ObjectLoadBatch(self.app.app_obj, 1, finished, parent=self,
                                    total=len(layers) + len(drills) + len(firmware))
            state['finished'] = False
            self.worker_task.emit({'fcn': batch.run,
                                   'params': [self.import_pcbdoc_objects,
                                              [state['filename'], board, layers, drills, firmware]]})
        except Exception as error:
            self.log.error('Proteus import: %s', error)
            self.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Import failed.'), error))
            state['failed'] = True
        finally:
            state['selecting'] = False

    def on_file_import_kicad_pcb(self, name=None):
        self._import_pcb_file('kicad_pcb', name, self.parse_kicad_pcb)

    def on_file_import_sprint_layout(self, name=None):
        self._import_pcb_file('sprint_lay6', name, self.parse_sprint_layout)

    def on_file_import_eagle_brd(self, name=None):
        self._import_pcb_file('eagle_brd', name, self.parse_eagle_brd)

    def on_file_import_allegro_brd(self, name=None):
        self._import_pcb_file('allegro_brd', name, self.parse_allegro_brd)

    def _parse_pcb_file(self, kind, filename, make_parser):
        if self.app.abort_flag:
            return
        try:
            parsed = make_parser().parse_file(filename)
        except Exception as error:
            self.log.error('%s import: %s', self.PCB_FORMATS[kind]['log'], error)
            self.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Import failed.'), error))
            return 'fail'
        if not self.app.abort_flag:
            self.pcbdoc_parsed.emit(filename, parsed)

    def parse_pcbdoc(self, filename):
        def make_parser():
            from appParsers.ParsePcbDoc import PcbDocParser
            return PcbDocParser(app=self.app)
        return self._parse_pcb_file('pcbdoc', filename, make_parser)

    def parse_kicad_pcb(self, filename):
        def make_parser():
            from appParsers.ParseKiCadPcb import KiCadPcbParser
            return KiCadPcbParser(app=self.app)
        return self._parse_pcb_file('kicad_pcb', filename, make_parser)

    def parse_sprint_layout(self, filename):
        def make_parser():
            from appParsers.ParseSprintLayout import SprintLayoutParser
            return SprintLayoutParser(app=self.app)
        return self._parse_pcb_file('sprint_lay6', filename, make_parser)

    def parse_eagle_brd(self, filename):
        def make_parser():
            from appParsers.ParseEagleBrd import EagleBrdParser
            return EagleBrdParser(app=self.app)
        return self._parse_pcb_file('eagle_brd', filename, make_parser)

    def parse_allegro_brd(self, filename):
        def make_parser():
            from appParsers.ParseAllegroBrd import AllegroBrdParser
            return AllegroBrdParser(app=self.app)
        return self._parse_pcb_file('allegro_brd', filename, make_parser)

    @QtCore.pyqtSlot(str, object)
    def on_pcbdoc_parsed(self, filename, parsed):
        if self.app.abort_flag:
            return
        kind = self._pcb_source_kind(filename)
        if kind == 'proteus' and hasattr(parsed, 'boards'):
            if self._proteus_import is not None:
                self.app.projects.inform_busy()
                return
            warnings = list(parsed.warnings)
            self._proteus_import = {'filename': filename, 'boards': list(parsed.boards),
                                    'warnings': list(dict.fromkeys(warnings)), 'selecting': False,
                                    'firmware': list(getattr(parsed, 'firmware', [])),
                                    'finished': True, 'failed': not bool(parsed.boards), 'imported': False}
            self._proteus_timer.start()
            return
        pcb_format = self.PCB_FORMATS[kind]
        default_layers = getattr(parsed, 'default_layers', None)
        default_drills = getattr(parsed, 'default_drill_groups', None)
        dialog = QtWidgets.QDialog(self.app.ui)
        dialog.setWindowTitle(self._pcb_title(kind))
        if kind == 'proteus':
            dialog.setWindowTitle('%s - %s' % (self._pcb_title(kind), parsed.name))
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel(_('Select layers and drill groups to import:')))
        scroll = QtWidgets.QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        choices = QtWidgets.QVBoxLayout(content)
        layers, drills = {}, {}
        for layer_id, layer in parsed.layers.items():
            if not any(not geo.is_empty for geo in layer['solid_geometry']):
                continue
            if pcb_format['name_first']:
                text = '%s (%s)' % (layer['name'], layer_id)
            else:
                text = str(layer_id) if layer['name'] in (None, '', layer_id) else '%s (%s)' % (layer_id, layer['name'])
            checkbox = QtWidgets.QCheckBox(text)
            checkbox.setChecked(default_layers is None or layer_id in default_layers)
            choices.addWidget(checkbox)
            layers[layer_id] = checkbox
        for group, tools in parsed.drill_groups.items():
            if not any(tool.get('drills') or tool.get('slots') for tool in tools.values()):
                continue
            checkbox = QtWidgets.QCheckBox(group)
            checkbox.setChecked(default_drills is None or group in default_drills)
            choices.addWidget(checkbox)
            drills[group] = checkbox
        scroll.setWidget(content)
        layout.addWidget(scroll)
        warnings = list(parsed.warnings)
        if kind == 'proteus' and self._proteus_import is not None:
            warnings = self._proteus_import['warnings'] + warnings
        if warnings:
            if kind == 'proteus':
                warning = QtWidgets.QPlainTextEdit('\n'.join(dict.fromkeys(warnings)))
                warning.setReadOnly(True)
                warning.setMaximumHeight(160)
            else:
                warning = QtWidgets.QLabel('\n'.join(warnings))
                warning.setWordWrap(True)
            layout.addWidget(warning)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(460, 500)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted or self.app.abort_flag:
            return
        selected_layers = [key for key, checkbox in layers.items() if checkbox.isChecked()]
        selected_drills = [key for key, checkbox in drills.items() if checkbox.isChecked()]
        if kind == 'proteus':
            return selected_layers, selected_drills
        if selected_layers or selected_drills:
            self.worker_task.emit({'fcn': self.import_pcbdoc_objects,
                                   'params': [filename, parsed, selected_layers, selected_drills]})

    def import_pcbdoc_objects(self, filename, parsed, selected_layers, selected_drills, firmware=()):
        if self.app.abort_flag or not (selected_layers or selected_drills):
            return
        units = self.app.app_units.upper()
        factor = 1 / 25.4 if units == 'IN' else 1
        pcb_format = self.PCB_FORMATS[self._pcb_source_kind(filename)]
        proteus = self._pcb_source_kind(filename) == 'proteus'

        def converted(value):
            if isinstance(value, dict):
                return {key: (item * factor if key in ('size', 'width', 'height', 'diam', 'tooldia')
                              and isinstance(item, (int, float)) else converted(item))
                        for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return type(value)(converted(item) for item in value)
            if hasattr(value, 'geom_type'):
                return scale(value, xfact=factor, yfact=factor, origin=(0, 0))
            return deepcopy(value)

        created = False
        failed = False
        try:
            for kind, keys in (('gerber', selected_layers), ('excellon', selected_drills)):
                for key in keys:
                    if self.app.abort_flag:
                        return
                    if kind == 'gerber':
                        data = parsed.layers[key]
                        if not any(not geo.is_empty for geo in data['solid_geometry']):
                            continue
                        if pcb_format['name_first']:
                            label = '%s_%s' % (key, data['name'])
                        else:
                            label = str(key).replace('.', '_')
                    else:
                        data = parsed.drill_groups[key]
                        if not any(tool.get('drills') or tool.get('slots') for tool in data.values()):
                            continue
                        label = key if pcb_format['name_first'] else str(key).replace('.', '_')
                    name = '%s_%s' % (os.path.splitext(os.path.basename(filename))[0], label)
                    source_extension = None
                    if proteus:
                        label = str(key)
                        extension = label.rsplit('.', 1)[-1].upper()
                        identified = kind == 'gerber' and (extension in (
                            'GTL', 'GBL', 'GKO', 'GTS', 'GBS', 'GTP', 'GBP', 'GTO', 'GBO') or
                            (extension.startswith('G') and extension[1:].isdigit()))
                        name = '%s%s%s' % (parsed.name, '.' if identified else '_',
                                          extension if identified else label)
                        if identified:
                            source_extension = '.' + extension

                    def initialize(obj, app, object_kind=kind, source=data, object_name=name,
                                   layer_extension=source_extension):
                        if app.abort_flag:
                            return 'fail'
                        obj.units = units
                        obj.obj_options['pcbdoc_import'] = True
                        if layer_extension is not None:
                            obj.obj_options['proteus_layer_extension'] = layer_extension
                        if object_kind == 'gerber':
                            obj.tools = converted(source['tools'])
                            obj.solid_geometry = converted(source['solid_geometry'])
                            obj.follow_geometry = converted(source['follow_geometry'])
                            obj.source_file = self.export_gerber(
                                object_name, filename=None, local_use=obj, use_thread=False)
                        else:
                            obj.tools = converted(source)
                            if obj.create_geometry() == 'fail':
                                return 'fail'
                            obj.source_file = self.export_excellon(
                                object_name, filename=None, local_use=obj, use_thread=False)
                        if not obj.source_file or obj.source_file == 'fail' or app.abort_flag:
                            return 'fail'

                    result = self.app.app_obj.new_object(kind, name, initialize, plot=proteus, autoselected=False)
                    if result is None or result == 'fail':
                        failed = True
                        break
                    created = True
                if failed:
                    break
            for path, text in ([] if failed else firmware):
                if self.app.abort_flag:
                    return

                def init_document(obj, app, source=text):
                    # Escaped preformatted text: '#include <...>' must not be read as rich text.
                    obj.source_file = '<pre>%s</pre>' % html.escape(source)

                name = '%s_%s' % (parsed.name, path.replace('\\', '/').rsplit('/', 1)[-1])
                result = self.app.app_obj.new_object('document', name, init_document, plot=False,
                                                     autoselected=False)
                if result is None or result == 'fail':
                    failed = True
                    break
                created = True
        except Exception as error:
            self.log.error('%s object import: %s', pcb_format['log'], error)
            failed = True
        finally:
            if created and not proteus:
                if not self.app.abort_flag:
                    self.pcbdoc_outputs_ready.emit(filename)
                self.worker_task.emit({'fcn': self.app.plot_all, 'params': [True, False, True], 'priority': 'low'})
        if failed:
            self.inform.emit('[ERROR_NOTCL] %s' % _('Import failed.'))
            return 'fail'

    @QtCore.pyqtSlot(str)
    def on_pcbdoc_outputs_ready(self, filename):
        if self.app.abort_flag:
            return
        self.app.file_opened.emit(self._pcb_source_kind(filename), filename)

    def on_file_open_gerber(self, name=None):
        """
        File menu callback for opening a Gerber.

        :param name:
        :return: None
        """

        self.log.debug("on_file_open_gerber()")

        _filter_ = "Gerber Files (*.gbr *.ger *.gtl *.gbl *.gts *.gbs *.gtp *.gbp *.gto *.gbo *.gm1 *.gml *.gm3 " \
                   "*.gko *.cmp *.sol *.stc *.sts *.plc *.pls *.crc *.crs *.tsm *.bsm *.ly2 *.ly15 *.dim *.mil *.grb " \
                   "*.top *.bot *.smt *.smb *.sst *.ssb *.spt *.spb *.pho *.gdo *.art *.gbd *.outline *.gdl *.gme);;" \
                   "Protel Files (*.gtl *.gbl *.gts *.gbs *.gto *.gbo *.gtp *.gbp *.gml *.gm1 *.gm3 *.gko " \
                   "*.outline);;" \
                   "Eagle Files (*.cmp *.sol *.stc *.sts *.plc *.pls *.crc *.crs *.tsm *.bsm *.ly2 *.ly15 *.dim " \
                   "*.mil);;" \
                   "OrCAD Files (*.top *.bot *.smt *.smb *.sst *.ssb *.spt *.spb);;" \
                   "Allegro Files (*.art);;" \
                   "Mentor Files (*.pho *.gdo);;" \
                   "All Files (*.*)"
        _filter_ = add_case_variants(_filter_)

        if name is None:
            try:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open Gerber"),
                                                                       directory=self.app.get_last_folder(),
                                                                       filter=_filter_,
                                                                       initialFilter=self.app.last_op_gerber_filter)
            except TypeError:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open Gerber"), filter=_filter_)

            filenames = [str(filename) for filename in filenames]
            self.app.last_op_gerber_filter = _f
        else:
            filenames = [name]
            self.splash.showMessage('%s: %ssec\n%s' % (_("Canvas initialization started.\n"
                                                         "Canvas initialization finished in"),
                                                       '%.2f' % self.app.used_time,
                                                       _("Opening Gerber file.")),
                                    alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                    color=QtGui.QColor("lightgray"))

        if len(filenames) == 0:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            filenames = [filename for filename in filenames if filename]
            batch = ObjectLoadBatch(self.app.app_obj, len(filenames)) if len(filenames) >= 3 else None
            for filename in filenames:
                if filename != '':
                    if batch is None:
                        self.worker_task.emit({'fcn': self.open_gerber, 'params': [filename]})
                    else:
                        self.worker_task.emit({'fcn': batch.run, 'params': [self.open_gerber, [filename]]})

    def on_file_open_excellon(self, name=None):
        """
        File menu callback for opening an Excellon file.

        :param name:
        :return: None
        """

        self.log.debug("on_file_open_excellon()")

        _filter_ = "Excellon Files (*.drl *.txt *.xln *.drd *.tap *.exc *.ncd);;" \
                   "All Files (*.*)"
        _filter_ = add_case_variants(_filter_)
        if name is None:
            try:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open Excellon"),
                                                                       directory=self.app.get_last_folder(),
                                                                       filter=_filter_,
                                                                       initialFilter=self.app.last_op_excellon_filter)
            except TypeError:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open Excellon"), filter=_filter_)
            filenames = [str(filename) for filename in filenames]
            self.app.last_op_excellon_filter = _f
        else:
            filenames = [str(name)]
            self.splash.showMessage('%s: %ssec\n%s' % (_("Canvas initialization started.\n"
                                                         "Canvas initialization finished in"),
                                                       '%.2f' % self.app.used_time,
                                                       _("Opening Excellon file.")),
                                    alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                    color=QtGui.QColor("lightgray"))

        if len(filenames) == 0:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            for filename in filenames:
                if filename != '':
                    self.worker_task.emit({'fcn': self.open_excellon, 'params': [filename]})

    def on_file_open_gcode(self, name=None):
        """

        File menu call back for opening gcode.

        :param name:
        :return:
        """

        self.log.debug("on_file_open_gcode()")

        # https://bobcadsupport.com/helpdesk/index.php?/Knowledgebase/Article/View/13/5/known-g-code-file-extensions
        _filter_ = "G-Code Files (*.txt *.nc *.ncc *.tap *.gcode *.cnc *.ecs *.fnc *.dnc *.ncg *.gc *.fan *.fgc" \
                   " *.din *.xpi *.hnc *.h *.i *.ncp *.min *.gcd *.rol *.knc *.mpr *.ply *.out *.eia *.sbp *.mpf);;" \
                   "All Files (*.*)"
        _filter_ = add_case_variants(_filter_)

        if name is None:
            try:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open G-Code"),
                                                                       directory=self.app.get_last_folder(),
                                                                       filter=_filter_,
                                                                       initialFilter=self.app.last_op_gcode_filter)
            except TypeError:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open G-Code"), filter=_filter_)

            filenames = [str(filename) for filename in filenames]
            self.app.last_op_gcode_filter = _f
        else:
            filenames = [name]
            self.splash.showMessage('%s: %ssec\n%s' % (_("Canvas initialization started.\n"
                                                         "Canvas initialization finished in"),
                                                       '%.2f' % self.app.used_time,
                                                       _("Opening G-Code file.")),
                                    alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                    color=QtGui.QColor("lightgray"))

        if len(filenames) == 0:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            for filename in filenames:
                if filename != '':
                    self.worker_task.emit({'fcn': self.open_gcode, 'params': [filename, None, True]})

    def on_file_open_project(self):
        """
        File menu callback for opening a project.

        :return: None
        """

        self.log.debug("on_file_open_project()")

        _filter_ = "FlatCAM Project (*.FlatPrj);;All Files (*.*)"
        try:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Open Project"),
                                                                 directory=self.app.get_last_folder(), filter=_filter_)
        except TypeError:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Open Project"), filter=_filter_)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            # self.worker_task.emit({'fcn': self.open_project,
            #                        'params': [filename]})
            # The above was failing because open_project() is not
            # thread safe. The new_project()
            self.open_project(filename)

    def on_file_open_hpgl2(self, name=None):
        """
        File menu callback for opening a HPGL2.

        :param name:
        :return:        None
        """
        self.log.debug("on_file_open_hpgl2()")

        _filter_ = "HPGL2 Files (*.plt);;" \
                   "All Files (*.*)"
        _filter_ = add_case_variants(_filter_)

        if name is None:
            try:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open HPGL2"),
                                                                       directory=self.app.get_last_folder(),
                                                                       filter=_filter_)
            except TypeError:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open HPGL2"), filter=_filter_)

            filenames = [str(filename) for filename in filenames]
        else:
            filenames = [name]
            self.splash.showMessage('%s: %ssec\n%s' % (_("Canvas initialization started.\n"
                                                         "Canvas initialization finished in"),
                                                       '%.2f' % self.app.used_time,
                                                       _("Opening HPGL2 file.")),
                                    alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                    color=QtGui.QColor("lightgray"))

        if len(filenames) == 0:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            for filename in filenames:
                if filename != '':
                    self.worker_task.emit({'fcn': self.open_hpgl2, 'params': [filename]})

    def on_file_open_config(self):
        """
        File menu callback for opening a config file.

        :return:        None
        """

        self.log.debug("on_file_open_config()")

        _filter_ = "FlatCAM Config (*.FlatConfig);;FlatCAM Config (*.json);;All Files (*.*)"
        try:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Open Configuration File"),
                                                                 directory=self.app.data_path, filter=_filter_)
        except TypeError:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Open Configuration File"),
                                                                 filter=_filter_)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            self.open_config_file(filename)

    def on_file_export_svg(self):
        """
        Callback for menu item File->Export SVG.

        :return: None
        """
        self.log.debug("on_file_export_svg()")

        obj = self.app.collection.get_active()
        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return

        # Check for more compatible types and add as required
        if (not isinstance(obj, GeometryObject)
                and not isinstance(obj, GerberObject)
                and not isinstance(obj, CNCJobObject)
                and not isinstance(obj, ExcellonObject)):
            msg = _("Only Geometry, Gerber and CNCJob objects can be used.")
            msgbox = FCMessageBox(parent=self.app.ui)
            msgbox.setWindowTitle(msg)  # taskbar still shows it
            msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))

            msgbox.setInformativeText(msg)
            msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/waning.png'))

            bt_ok = msgbox.addButton(_('Ok'), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            msgbox.setDefaultButton(bt_ok)
            msgbox.exec()
            return

        name = obj.obj_options["name"]

        _filter = "SVG File (*.svg);;All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export SVG"),
                directory=self.app.get_last_save_folder() + '/' + str(name) + '_svg',
                ext_filter=_filter)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export SVG"),
                ext_filter=_filter)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            self.export_svg(name, filename)
            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("SVG", filename)
            self.app.file_saved.emit("SVG", filename)

    def on_file_export_png(self):

        self.log.debug("on_file_export_png()")

        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        image = _screenshot(alpha=False)
        data = np.asarray(image)
        if data.ndim != 3 or data.shape[-1] not in (3, 4):
            self.inform.emit('[[WARNING_NOTCL]] %s' % _('Data must be a 3D array with last dimension 3 or 4'))
            return

        filter_ = "PNG File (*.png);;All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export PNG Image"),
                directory=self.app.get_last_save_folder() + '/png_' + date,
                ext_filter=filter_)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export PNG Image"),
                ext_filter=filter_)

        filename = str(filename)

        if filename == "":
            self.inform.emit(_("Cancelled."))
            return
        else:
            write_png(filename, data)   # noqa

            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("png", filename)
            self.app.file_saved.emit("png", filename)

    def on_file_save_gerber(self):
        """
        Callback for menu item in Project context menu.

        :return: None
        """
        self.log.debug("on_file_save_gerber()")

        obj = self.app.collection.get_active()
        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return

        # Check for more compatible types and add as required
        if not isinstance(obj, GerberObject):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed. Only Gerber objects can be saved as Gerber files..."))
            return

        name = self.app.collection.get_active().obj_options["name"]

        _filter = "Gerber File (*.GBR);;Gerber File (*.GRB);;All Files (*.*)"
        extension = obj.obj_options.get('proteus_layer_extension')
        if extension:
            _filter = 'Gerber File (*%s);;%s' % (extension, _filter)
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption="Save Gerber source file",
                directory=self.app.get_last_save_folder() + '/' + name,
                ext_filter=_filter)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Gerber source file"),
                ext_filter=_filter)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            self.save_source_file(name, filename)
            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("Gerber", filename)
            self.app.file_saved.emit("Gerber", filename)

    def on_file_save_script(self):
        """
        Callback for menu item in Project context menu.

        :return: None
        """
        self.log.debug("on_file_save_script()")

        obj = self.app.collection.get_active()
        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return

        # Check for more compatible types and add as required
        if not isinstance(obj, ScriptObject):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed. Only Script objects can be saved as TCL Script files..."))
            return

        name = self.app.collection.get_active().obj_options["name"]

        _filter = "FlatCAM Scripts (*.FlatScript);;All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption="Save Script source file",
                directory=self.app.get_last_save_folder() + '/' + name,
                ext_filter=_filter)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Script source file"),
                ext_filter=_filter)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            if self.save_source_file(name, filename) == 'fail':
                return 'fail'
            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("script", filename)
            self.app.file_saved.emit("script", filename)

    def on_file_save_document(self):
        """
        Callback for menu item in Project context menu.

        :return: None
        """
        self.log.debug("on_file_save_document()")

        obj = self.app.collection.get_active()
        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return

        # Check for more compatible types and add as required
        if not isinstance(obj, ScriptObject):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed. Only Document objects can be saved as Document files..."))
            return

        name = self.app.collection.get_active().obj_options["name"]

        _filter = "FlatCAM Documents (*.FlatDoc);;All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption="Save Document source file",
                directory=self.app.get_last_save_folder() + '/' + name,
                ext_filter=_filter)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Document source file"),
                ext_filter=_filter)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            self.save_source_file(name, filename)
            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("Document", filename)
            self.app.file_saved.emit("Document", filename)

    def on_file_save_excellon(self):
        """
        Callback for menu item in project context menu.

        :return: None
        """
        self.log.debug("on_file_save_excellon()")

        obj = self.app.collection.get_active()
        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return

        # Check for more compatible types and add as required
        if not isinstance(obj, ExcellonObject):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed. Only Excellon objects can be saved as Excellon files..."))
            return

        name = self.app.collection.get_active().obj_options["name"]

        _filter = "Excellon File (*.DRL);;Excellon File (*.TXT);;All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Excellon source file"),
                directory=self.app.get_last_save_folder() + '/' + name,
                ext_filter=_filter)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Excellon source file"), ext_filter=_filter)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            self.save_source_file(name, filename)
            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("Excellon", filename)
            self.app.file_saved.emit("Excellon", filename)

    def on_file_export_excellon(self):
        """
        Callback for menu item File->Export->Excellon.

        :return: None
        """
        self.log.debug("on_file_export_excellon()")

        obj = self.app.collection.get_active()
        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return

        # Check for more compatible types and add as required
        if not isinstance(obj, ExcellonObject):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed. Only Excellon objects can be saved as Excellon files..."))
            return

        name = self.app.collection.get_active().obj_options["name"]

        _filter = self.app.options.get("excellon_save_filters", self.app.defaults.get("excellon_save_filters"))
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Excellon"),
                directory=self.app.get_last_save_folder() + '/' + name,
                ext_filter=_filter)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Excellon"),
                ext_filter=_filter)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            used_extension = filename.rpartition('.')[2]
            obj.update_filters(last_ext=used_extension, filter_string='excellon_save_filters')

            self.export_excellon(name, filename)
            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("Excellon", filename)
            self.app.file_saved.emit("Excellon", filename)

    def on_file_export_gerber(self):
        """
        Callback for menu item File->Export->Gerber.

        :return: None
        """
        self.log.debug("on_file_export_gerber()")

        obj = self.app.collection.get_active()
        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return

        # Check for more compatible types and add as required
        if not isinstance(obj, GerberObject):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed. Only Gerber objects can be saved as Gerber files..."))
            return

        name = self.app.collection.get_active().obj_options["name"]

        _filter_ = self.app.options.get('gerber_save_filters', self.app.defaults.get('gerber_save_filters'))
        extension = obj.obj_options.get('proteus_layer_extension')
        if extension:
            _filter_ = 'Gerber File (*%s);;%s' % (extension, _filter_)
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Gerber"),
                directory=self.app.get_last_save_folder() + '/' + name,
                ext_filter=_filter_)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Gerber"),
                ext_filter=_filter_)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            used_extension = filename.rpartition('.')[2]
            obj.update_filters(last_ext=used_extension, filter_string='gerber_save_filters')

            self.export_gerber(name, filename)
            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("Gerber", filename)
            self.app.file_saved.emit("Gerber", filename)

    def on_file_export_dxf(self):
        """
                Callback for menu item File->Export DXF.

                :return: None
                """
        self.log.debug("on_file_export_dxf()")

        obj = self.app.collection.get_active()
        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return

        # Check for more compatible types and add as required
        if obj.kind != 'geometry':
            msg = _("Only Geometry objects can be used.")
            msgbox = FCMessageBox(parent=self.app.ui)
            msgbox.setWindowTitle(msg)  # taskbar still shows it
            msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))

            msgbox.setInformativeText(msg)
            msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/waning.png'))

            msgbox.setIcon(QtWidgets.QMessageBox.Icon.Warning)

            msgbox.setInformativeText(msg)
            bt_ok = msgbox.addButton(_('Ok'), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            msgbox.setDefaultButton(bt_ok)
            msgbox.exec()
            return

        name = self.app.collection.get_active().obj_options["name"]

        _filter_ = "DXF File .dxf (*.DXF);;All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export DXF"),
                directory=self.app.get_last_save_folder() + '/' + name,
                ext_filter=_filter_)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export DXF"),
                ext_filter=_filter_)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            self.export_dxf(name, filename)
            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
                self.app.file_opened.emit("DXF", filename)
            self.app.file_saved.emit("DXF", filename)

    def on_file_import_svg(self, type_of_obj):
        """
        Callback for menu item File->Import SVG.
        :param type_of_obj: to import the SVG as Geometry or as Gerber
        :type type_of_obj: str
        :return: None
        """
        self.log.debug("on_file_import_svg()")

        _filter_ = add_case_variants("SVG File .svg (*.svg);;All Files (*.*)")
        try:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Import SVG"),
                                                                   directory=self.app.get_last_folder(),
                                                                   filter=_filter_)
        except TypeError:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Import SVG"),
                                                                   filter=_filter_)

        if type_of_obj != "geometry" and type_of_obj != "gerber":
            type_of_obj = "geometry"

        filenames = [str(filename) for filename in filenames]

        if len(filenames) == 0:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            for filename in filenames:
                if filename != '':
                    self.worker_task.emit({'fcn': self.import_svg, 'params': [filename, type_of_obj]})

    def on_file_import_dxf(self, type_of_obj):
        """
        Callback for menu item File->Import DXF.
        :param type_of_obj: to import the DXF as Geometry or as Gerber
        :type type_of_obj: str
        :return: None
        """
        self.log.debug("on_file_import_dxf()")

        _filter_ = add_case_variants("DXF File .dxf (*.dxf);;All Files (*.*)")
        try:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Import DXF"),
                                                                   directory=self.app.get_last_folder(),
                                                                   filter=_filter_)
        except TypeError:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Import DXF"),
                                                                   filter=_filter_)

        if type_of_obj != "geometry" and type_of_obj != "gerber":
            type_of_obj = "geometry"

        filenames = [str(filename) for filename in filenames]

        if len(filenames) == 0:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            if len(filenames) > 1:
                # Batch import: suppress individual plotting, then plot all at end
                # This eliminates N canvas redraws and N zoom-fits, replacing them with 1 each
                for filename in filenames:
                    if filename != '':
                        self.worker_task.emit({'fcn': self.import_dxf,
                                               'params': [filename, type_of_obj, None, False]})  # plot=False
                # After all imports complete, do a single plot_all with zoom fit
                # 'low' priority = barrier: guaranteed to run after ALL imports finish
                self.worker_task.emit({'fcn': self.app.plot_all, 'params': [True, False, True],
                                       'priority': 'low'})  # fit_view=True
            else:
                # Single file: use normal behavior (plot=True is default)
                for filename in filenames:
                    if filename != '':
                        self.worker_task.emit({'fcn': self.import_dxf, 'params': [filename, type_of_obj]})

    def on_file_new_click(self):
        """
        Callback for menu item File -> New.
        Executed on clicking the Menu -> File -> New Project

        :return:
        """
        self.log.debug("on_file_new_click()")

        # the current project stays open in the Project tab, so there is nothing to save here
        self.app.projects.new()

    def on_file_new_project(self, cli=None, reset_tcl=True, use_thread=None, keep_scripts=True):
        """
        Returns the application to its startup state. This method is thread-safe.

        :param cli:         Boolean. If True this method was run from command line
        :param reset_tcl:   Boolean. If False, on new project creation the Tcl instance is not recreated, therefore it
                            will remember all the previous variables. If True then the Tcl is re-instantiated.
        :param use_thread:  Bool. If True some part of the initialization are done threaded
        :param keep_scripts: Bool. If True the Script objects are not deleted when creating a new project
        :return:            None
        """

        self.log.debug("on_file_new_project()")

        pending = getattr(self.app, 'script_pending_work', [])
        if getattr(self.app, '_script_event_pump', False) is True or \
                (reset_tcl and getattr(self.app, 'script_state', '') == 'running') or \
                any(not state['finished'] for state in pending):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Cannot reset Tcl while a script is running."))
            return 'fail'

        t_start_proj = time.time()

        # close any editor that might be open
        if self.app.call_source != 'app':
            if self.app.on_editing_finished(cleanup=True) == 'fail':
                return 'fail'
            # ## EDITOR section
            self.app.geo_editor = AppGeoEditor(self.app)
            self.app.exc_editor = AppExcEditor(self.app)
            self.app.grb_editor = AppGerberEditor(self.app)

        # The reset is now committed. Only this project's history is no longer replayable.
        history_of(self.app).clear()

        for obj in self.app.collection.get_list():
            # delete shapes left drawn from mark shape_collections, if any
            if isinstance(obj, GerberObject):
                try:
                    obj.mark_shapes_storage.clear()
                    obj.mark_shapes.clear(update=True)
                    obj.mark_shapes.enabled = False
                except AttributeError:
                    pass

            # also delete annotation shapes, if any
            elif isinstance(obj, CNCJobObject):
                try:
                    obj.text_col.enabled = False
                    del obj.text_col
                    obj.annotation.clear(update=True)
                    del obj.annotation
                except AttributeError:
                    pass

        # clear the possible drawn probing shapes for Levelling Tool
        try:
            self.app.levelling_tool.probing_shapes.clear(update=True)
        except AttributeError:
            pass

        # clean possible tool shapes for Isolation, NCC, Paint, Punch Gerber Plugins
        try:
            self.app.tool_shapes.clear(update=True)
        except AttributeError:
            pass

        # delete the exclusion areas
        self.app.exc_areas.clear_shapes()

        # delete any selection shape on canvas
        self.app.delete_selection_shape()

        # delete any hover shapes on canvas
        try:
            self.app.hover_shapes.clear(update=True)
        except AttributeError:
            pass

        # delete all App objects
        if keep_scripts is True:
            for prj_obj in self.app.collection.get_list():
                if prj_obj.kind != 'script':
                    self.app.collection.delete_by_name(prj_obj.obj_options['name'], select_project=False)
        else:
            self.app.collection.delete_all()

        self.log.debug('%s: %s %s.' %
                       ("Deleted all the application objects", str(time.time() - t_start_proj), _("seconds")))

        # add in Selected tab an initial text that describe the flow of work in FlatCAm
        self.app.setup_default_properties_tab()

        # Clear project filename
        self.app.project_filename = None

        default_file = self.app.defaults_path()
        # Load the application options
        self.options.load(filename=default_file, inform=self.inform)

        # Re-fresh project options
        self.app.on_defaults2options()

        if use_thread is True:
            self.app.new_project_signal.emit()
        else:
            t0 = time.time()
            # Clear pool
            self.app.clear_pool()

            # Init FlatCAMTools
            if reset_tcl is True:
                self.app.init_tools(init_tcl=True)
            else:
                self.app.init_tools(init_tcl=False)
            self.log.debug(
                '%s: %s %s.' % ("Initiated the MP pool and plugins in: ", str(time.time() - t0), _("seconds")))

            # tcl needs to be reinitialized, otherwise old shell variables etc  remains
            # self.app.shell.init_tcl()

        # Try to close all tabs in the PlotArea but only if the appGUI is active (CLI is None)
        if cli is None:
            # we need to go in reverse because once we remove a tab then the index changes
            # meaning that removing the first tab (idx = 0) then the tab at former idx = 1 will assume idx = 0
            # and so on. Therefore, the deletion should be done in reverse
            wdg_count = self.app.ui.plot_tab_area.tabBar.count() - 1
            retained_tabs = []
            for index in range(wdg_count, -1, -1):
                try:
                    close_result = self.app.ui.plot_tab_area.closeTab(index)
                    if close_result is False:
                        tab_name = self.app.ui.plot_tab_area.tabText(index) or _("A tab")
                        retained_tabs.append(tab_name)
                except Exception as e:
                    self.log.error("App.on_file_new_project() --> %s" % str(e))

            if retained_tabs:
                if len(retained_tabs) == 1:
                    retained_message = _("%s remains open because its closure was canceled.") % retained_tabs[0]
                else:
                    retained_message = _("%d tabs remain open because their closure was canceled.") % len(retained_tabs)
                self.inform.emit('[WARNING_NOTCL] %s' % retained_message)

            # # And then add again the Plot Area
            self.app.ui.plot_tab_area.insertTab(0, self.app.ui.plot_tab, _("Plot Area"))
            self.app.ui.plot_tab_area.protectTab(0)

        # take the focus of the Notebook on Project Tab.
        self.app.ui.notebook.setCurrentWidget(self.app.ui.project_tab)

        self.log.debug('%s: %s %s.' % (_("Project created in"), str(time.time() - t_start_proj), _("seconds")))
        self.app.ui.set_ui_title(name=_("New Project - Not saved"))

        self.inform.emit('[success] %s...' % _("New Project created"))

    def on_new_project_house_keeping(self):
        """
        Do dome of the new project initialization in a threaded way

        :return:
        :rtype:
        """
        t0 = time.time()

        # Clear pool
        self.log.debug("New Project: cleaning multiprocessing pool.")
        self.app.clear_pool()

        # Init FlatCAMTools
        self.log.debug("New Project: initializing the Tools and Tcl Shell.")
        self.app.init_tools(init_tcl=True)
        self.log.debug('%s: %s %s.' % ("Initiated the MP pool and plugins in: ", str(time.time() - t0), _("seconds")))

    def on_file_new_script(self, silent=False):
        """
        Will create a new script file and open it in the Code Editor

        :param silent:  if True will not display status messages
        :return:        None
        """
        self.log.debug("on_file_new_script()")

        if silent is False:
            self.inform.emit('[success] %s' % _("New TCL script file created in Code Editor."))

        # hide coordinates toolbars in the infobar while in DB
        self.app.ui.coords_toolbar.hide()
        self.app.ui.delta_coords_toolbar.hide()

        self.app.app_obj.new_script_object()

    def on_file_open_script(self, name=None, silent=False):
        """
        Will open a Tcl script file into the Code Editor

        :param silent:  if True will not display status messages
        :param name:    name of a Tcl script file to open
        :return:        None
        """

        self.log.debug("on_file_open_script()")

        _filter_ = "TCL script .FlatScript (*.FlatScript);;TCL script .tcl (*.TCL);;TCL script .txt (*.TXT);;" \
                   "All Files (*.*)"

        if name:
            filenames = [name]
        else:
            try:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(
                    caption=_("Open TCL script"), directory=self.app.get_last_folder(), filter=_filter_)
            except TypeError:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open TCL script"), filter=_filter_)

        if len(filenames) == 0:
            if silent is False:
                self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            for filename in filenames:
                if filename != '':
                    self.worker_task.emit({'fcn': self.open_script, 'params': [filename]})

    def on_file_open_script_example(self, name=None, silent=False):
        """
        Will open a Tcl script file into the Code Editor

        :param silent: if True will not display status messages
        :param name: name of a Tcl script file to open
        :return:
        """

        self.log.debug("on_file_open_script_example()")

        _filter_ = "TCL script .FlatScript (*.FlatScript);;TCL script .tcl (*.TCL);;TCL script .txt (*.TXT);;" \
                   "All Files (*.*)"

        # test if the app was frozen and choose the path for the configuration file
        if getattr(sys, "frozen", False) is True:
            example_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__))) + '\\assets\\examples'
        else:
            example_path = os.path.dirname(os.path.realpath(__file__)) + '\\assets\\examples'

        if name:
            filenames = [name]
        else:
            try:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(
                    caption=_("Open TCL script"), directory=example_path, filter=_filter_)
            except TypeError:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open TCL script"), filter=_filter_)

        if len(filenames) == 0:
            if silent is False:
                self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            for filename in filenames:
                if filename != '':
                    self.worker_task.emit({'fcn': self.open_script, 'params': [filename]})

    def on_file_run_script(self, name=None, silent=False):
        """
        File menu callback for loading and running a TCL script.

        :param silent: if True will not display status messages
        :param name: name of a Tcl script file to be run by FlatCAM
        :return: None
        """

        self.log.debug("on_file_runscript()")

        if name:
            filename = name
            if self.splash is not None:
                self.splash.showMessage('%s: %ssec\n%s' %
                                        (_("Canvas initialization started.\n"
                                           "Canvas initialization finished in"), '%.2f' % self.app.used_time,
                                         _("Executing ScriptObject file.")
                                         ),
                                        alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                        color=QtGui.QColor("lightgray"))
        else:
            _filter_ = "TCL script .FlatScript (*.FlatScript);;TCL script .tcl (*.TCL);;TCL script .txt (*.TXT);;" \
                       "All Files (*.*)"
            try:
                filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Run TCL script"),
                                                                     directory=self.app.get_last_folder(),
                                                                     filter=_filter_)
            except TypeError:
                filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Run TCL script"), filter=_filter_)

        # The Qt methods above will return a QString which can cause problems later.
        # So far json.dump() will fail to serialize it.
        filename = str(filename)

        if filename == "":
            if silent is False:
                self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            if self.app.ui.shell_dock.isHidden():
                self.app.ui.shell_dock.show()

            try:
                with open(filename, "r", encoding='utf-8-sig') as tcl_script:
                    cmd_line_shell_file_content = tcl_script.read()
                self.app.script_processing(cmd_line_shell_file_content, origin=filename)

                if silent is False and self.app.script_state == 'completed':
                    self.inform.emit('[success] %s' % _("TCL script file opened in Code Editor and executed."))
                if self.app.script_state != 'completed':
                    return 'fail'
            except Exception as e:
                self.app.log.error("App.on_file_run_cript() -> %s" % str(e))
                self.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to execute script"), str(e)))
                return 'fail'

    def on_file_save_project(self, silent=False):
        """
        Callback for menu item File->Save Project. Saves the project to
        ``self.project_filename`` or calls ``self.on_file_save_project_as()``
        if set to None. The project is saved by calling ``self.save_project()``.

        :param silent: if True will not display status messages
        :return: None
        """
        self.log.debug("on_file_save_project()")

        target = self.app.project_filename
        # after a rename, the project is saved as '<new name>.FlatPrj' in the same folder; the old file stays
        projects = getattr(self.app, 'projects', None)
        if projects is not None and target is not None:
            new_target = projects.save_target()
            if new_target and not same_path(new_target, target):
                if os.path.exists(new_target) and not projects.ask_yes_no(
                        _("Overwrite"), '%s: %s\n%s' % (_("The file already exists"), new_target,
                                                        _("Do you want to overwrite it?"))):
                    self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                    return
                target = new_target

        if target is None:
            self.on_file_save_project_as()
            return

        # the file name and the title change in _on_project_save_done(), after a good save
        self._queue_project_save(target, quit_action=False, silent=silent)

    def _queue_project_save(self, filename, quit_action=False, silent=False, make_copy=False, use_thread=True):
        """
        Start a save of File -> Save or File -> Save As, on the GUI thread.

        The dirty flag is cleared now, before the worker reads the objects. A change while the save runs sets the
        flag again, so a good save does not hide it. A failed save sets the flag back. save_in_progress is set now,
        so the exit waits for a save that is queued but not started.

        Only 1 save runs at a time. project_save_pending is set here and cleared only in _on_project_save_done(),
        both on the GUI thread. While it is set, a new save is refused (a second save could end first; the older
        one would then set an old file name, clear the dirty flag or overwrite a newer file), and the exit and a
        project switch wait (the worker clears save_in_progress before the GUI restores the dirty flag).

        :return:    True if the save is queued
        """
        if getattr(getattr(self.app, 'geo_editor', None), '_source_save_pending', False) is True:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Geometry save is running. Wait until it ends."))
            return False
        if getattr(self.app, 'project_save_pending', False):
            self.inform.emit('[WARNING_NOTCL] %s' % _("A save of the project is running. Wait until it ends."))
            return False
        self.app.project_save_pending = True

        was_dirty = bool(self.app.should_we_save)
        self.app.should_we_save = False
        self.app.save_in_progress = True
        params = [filename, quit_action, silent, make_copy, was_dirty]
        if use_thread is True:
            self.worker_task.emit({'fcn': self._save_project_task, 'params': params})
        else:
            self._save_project_task(*params)
        return True

    def _save_project_task(self, filename, quit_action=False, silent=False, make_copy=False, was_dirty=True):
        """
        The worker task of File -> Save and File -> Save As. It always emits project_save_done, also after an
        error. The state of the project changes only in _on_project_save_done(), on the GUI thread.
        """
        ok = self.save_project_checked(filename, quit_action=quit_action, silent=silent)
        self.project_save_done.emit(str(filename), ok, bool(make_copy), bool(was_dirty))

    def _on_project_save_done(self, filename, ok, make_copy, was_dirty):
        """
        Change the state of the project after a save. After a failed save, the project keeps its file name,
        its name and its unsaved changes.
        """
        # the save is complete for the GUI; a new save, the exit and a switch are allowed again after this method
        self.app.project_save_pending = False
        if not ok:
            self.app.save_in_progress = False
            if was_dirty:
                self.app.should_we_save = True
            # a close during the save does not close the app: the project was not saved
            self.app.close_after_save = False
            self.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The project was not saved"), filename))
            return

        if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style")) is False:
            self.app.file_opened.emit("project", filename)
        self.app.file_saved.emit("project", filename)

        if not make_copy:
            self.app.project_filename = filename
            projects = getattr(self.app, 'projects', None)
            if projects is not None:
                projects.adopt_filename(filename)

        self.app.ui.set_ui_title(name=self.app.project_filename)
        # do not clear should_we_save here: it was cleared when the save was queued, and if it is set now, the
        # project changed while the save ran and that change is not in the file
        if not self.app.save_in_progress and getattr(self.app, 'close_after_save', False):
            # the worker timer can finish before the GUI receives the save result
            self.app.close_after_save = False
            QtCore.QMetaObject.invokeMethod(self.app.ui, 'close', QtCore.Qt.ConnectionType.QueuedConnection)

    def on_file_save_project_as(self, make_copy=False, use_thread=True, quit_action=False):
        """
        Save the project to a given file by opening a file chooser via self.save_project().

        :param make_copy: boolean, whether to make a copy of the project
        :param use_thread: boolean, whether to run in a separate thread
        :param quit_action: boolean, whether to quit the application after
        :return: None """
        self.log.debug("on_file_save_project_as()")

        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        filter_ = "FlatCAM Project .FlatPrj (*.FlatPrj);; All Files (*.*)"
        # the suggested file name is the project name
        projects = getattr(self.app, 'projects', None)
        if projects is not None:
            proj_name = projects.active.name
        else:
            proj_name = '%s_%s' % (_("Project"), date)
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Project As ..."),
                directory='{l_save}/{proj}'.format(l_save=str(self.app.get_last_save_folder()), proj=proj_name),
                ext_filter=filter_
            )
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Project As ..."),
                ext_filter=filter_)

        filename = str(filename)

        if filename == '':
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return

        # the file name and the title change in _on_project_save_done(), after a good save
        self._queue_project_save(filename, quit_action=quit_action, silent=False, make_copy=make_copy,
                                 use_thread=use_thread)

    def on_file_save_objects_pdf(self, use_thread=True):
        self.log.debug("on_file_save_objects_pdf()")

        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        try:
            obj_selection = self.app.collection.get_selected()
            if len(obj_selection) == 1:
                obj_name = str(obj_selection[0].obj_options['name'])
            else:
                obj_name = _("General_print")
        except AttributeError as att_err:
            self.log.debug("App.on_file_save_object_pdf() --> %s" % str(att_err))
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return

        if not obj_selection:
            self.inform.emit(
                '[WARNING_NOTCL] %s %s' % (_("No object is selected."), _("Print everything in the workspace.")))
            obj_selection = self.app.collection.get_list()

        filter_ = "PDF File .pdf (*.PDF);; All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Object as PDF ..."),
                directory='{l_save}/{obj_name}_{date}'.format(l_save=str(self.app.get_last_save_folder()),
                                                              obj_name=obj_name,
                                                              date=date),
                ext_filter=filter_
            )
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save Object as PDF ..."),
                ext_filter=filter_)

        filename = str(filename)

        if filename == '':
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return

        if use_thread is True:
            self.app.proc_container.new(_("Printing PDF ..."))
            self.worker_task.emit({'fcn': self.save_pdf, 'params': [filename, obj_selection]})
        else:
            self.save_pdf(filename, obj_selection)

        # self.save_project(filename)
        if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style", self.app.defaults.factory_defaults.get("global_open_style"))) is False:
            self.app.file_opened.emit("pdf", filename)
        self.app.file_saved.emit("pdf", filename)

    def save_pdf(self, file_name, obj_selection):
        self.log.debug("save_pdf()")

        p_size = self.app.options.get('global_workspaceT', self.app.defaults.get('global_workspaceT'))
        orientation = self.app.options.get('global_workspace_orientation', self.app.defaults.get('global_workspace_orientation'))
        color = 'black'
        transparency_level = 1.0

        self.pagesize.update(
            {
                'Bounds': None,
                'A0': (841 * mm, 1189 * mm),
                'A1': (594 * mm, 841 * mm),
                'A2': (420 * mm, 594 * mm),
                'A3': (297 * mm, 420 * mm),
                'A4': (210 * mm, 297 * mm),
                'A5': (148 * mm, 210 * mm),
                'A6': (105 * mm, 148 * mm),
                'A7': (74 * mm, 105 * mm),
                'A8': (52 * mm, 74 * mm),
                'A9': (37 * mm, 52 * mm),
                'A10': (26 * mm, 37 * mm),

                'B0': (1000 * mm, 1414 * mm),
                'B1': (707 * mm, 1000 * mm),
                'B2': (500 * mm, 707 * mm),
                'B3': (353 * mm, 500 * mm),
                'B4': (250 * mm, 353 * mm),
                'B5': (176 * mm, 250 * mm),
                'B6': (125 * mm, 176 * mm),
                'B7': (88 * mm, 125 * mm),
                'B8': (62 * mm, 88 * mm),
                'B9': (44 * mm, 62 * mm),
                'B10': (31 * mm, 44 * mm),

                'C0': (917 * mm, 1297 * mm),
                'C1': (648 * mm, 917 * mm),
                'C2': (458 * mm, 648 * mm),
                'C3': (324 * mm, 458 * mm),
                'C4': (229 * mm, 324 * mm),
                'C5': (162 * mm, 229 * mm),
                'C6': (114 * mm, 162 * mm),
                'C7': (81 * mm, 114 * mm),
                'C8': (57 * mm, 81 * mm),
                'C9': (40 * mm, 57 * mm),
                'C10': (28 * mm, 40 * mm),

                # American paper sizes
                'LETTER': (8.5 * inch, 11 * inch),
                'LEGAL': (8.5 * inch, 14 * inch),
                'ELEVENSEVENTEEN': (11 * inch, 17 * inch),

                # From https://en.wikipedia.org/wiki/Paper_size
                'JUNIOR_LEGAL': (5 * inch, 8 * inch),
                'HALF_LETTER': (5.5 * inch, 8 * inch),
                'GOV_LETTER': (8 * inch, 10.5 * inch),
                'GOV_LEGAL': (8.5 * inch, 13 * inch),
                'LEDGER': (17 * inch, 11 * inch),
            }
        )

        # make sure that the Excellon objects are drawn on top of everything
        excellon_objs = [obj for obj in obj_selection if obj.kind == 'excellon']
        cncjob_objs = [obj for obj in obj_selection if obj.kind == 'cncjob']
        # reverse the object order such that the first selected is on top
        rest_objs = [obj for obj in obj_selection if obj.kind != 'excellon' and obj.kind != 'cncjob'][::-1]
        obj_selection = rest_objs + cncjob_objs + excellon_objs

        # generate the SVG files from the application objects
        exported_svg = []
        for obj in obj_selection:
            svg_obj = obj.export_svg(scale_stroke_factor=0.0)

            if obj.kind.lower() == 'gerber' or obj.kind.lower() == 'excellon':
                color = obj.fill_color[:-2]
                transparency_level = obj.fill_color[-2:]
            elif obj.kind.lower() == 'geometry':
                color = self.app.options.get("global_draw_color", self.app.defaults.get("global_draw_color", self.app.defaults.factory_defaults.get("global_draw_color")))

            # Change the attributes of the exported SVG
            # We don't need stroke-width
            # We set opacity to maximum
            # We set the colour to WHITE

            try:
                root = ET.fromstring(svg_obj)
            except Exception as e:
                self.log.debug("appIO.save_pdf() -> Missing root node -> %s" % str(e))
                self.app.inform.emit("[ERROR_NOTCL] %s" % _("Failed."))
                return

            for child in root:
                child.set('fill', str(color))
                child.set('opacity', str(transparency_level))
                child.set('stroke', str(color))

            exported_svg.append(ET.tostring(root))

        xmin = Inf
        ymin = Inf
        xmax = -Inf
        ymax = -Inf

        for obj in obj_selection:
            try:
                g_xmin, g_ymin, g_xmax, g_ymax = obj.bounds()
                xmin = min([xmin, g_xmin])
                ymin = min([ymin, g_ymin])
                xmax = max([xmax, g_xmax])
                ymax = max([ymax, g_ymax])
            except Exception as e:
                self.log.error("Tried to get bounds of empty geometry in App.save_pdf(). %s" % str(e))

        # Determine bounding area for svg export
        bounds = [xmin, ymin, xmax, ymax]
        size = bounds[2] - bounds[0], bounds[3] - bounds[1]

        # This contains the measure units
        uom = obj_selection[0].units.lower()

        # Define a boundary around SVG of about 1.0mm (~39mils)
        if uom in "mm":
            boundary = 1.0
        else:
            boundary = 0.0393701

        # Convert everything to strings for use in the xml doc
        svgwidth = str(size[0] + (2 * boundary))
        svgheight = str(size[1] + (2 * boundary))
        minx = str(bounds[0] - boundary)
        miny = str(bounds[1] + boundary + size[1])

        # Add an SVG Header and footer to the svg output from shapely
        # The transform flips the Y Axis so that everything renders
        # properly within svg apps such as inkscape
        svg_header = '<svg xmlns="http://www.w3.org/2000/svg" ' \
                     'version="1.1" xmlns:xlink="http://www.w3.org/1999/xlink" '
        svg_header += 'width="' + svgwidth + uom + '" '
        svg_header += 'height="' + svgheight + uom + '" '
        svg_header += 'viewBox="' + minx + ' -' + miny + ' ' + svgwidth + ' ' + svgheight + '" '
        svg_header += '>'
        svg_header += '<g transform="scale(1,-1)">'
        svg_footer = '</g> </svg>'

        svg_elem = str(svg_header)
        for svg_item in exported_svg:
            svg_elem += str(svg_item)
        svg_elem += str(svg_footer)

        # Parse the xml through a xml parser just to add line feeds
        # and to make it look more pretty for the output
        doc = parse_xml_string(svg_elem)
        doc_final = doc.toprettyxml()

        try:
            if self.app_units.upper() == 'IN':
                unit = inch
            else:
                unit = mm

            doc_final = StringIO(doc_final)
            drawing = svg2rlg(doc_final)

            if p_size == 'Bounds':
                renderPDF.drawToFile(drawing, file_name)
            else:
                if orientation == 'p':
                    page_size = portrait(self.pagesize[p_size])
                else:
                    page_size = landscape(self.pagesize[p_size])

                my_canvas = canvas.Canvas(file_name, pagesize=page_size)
                my_canvas.translate(bounds[0] * unit, bounds[1] * unit)
                renderPDF.draw(drawing, my_canvas, 0, 0)
                my_canvas.save()
        except Exception as e:
            self.log.error("appIO.save_pdf() --> PDF output --> %s" % str(e))
            return 'fail'

        self.inform.emit('[success] %s: %s' % (_("PDF file saved to"), file_name))

    def export_svg(self, obj_name, filename, scale_stroke_factor=0.00):
        """
        Exports a Geometry Object to an SVG file.

        :param obj_name: the name of the FlatCAM object to be saved as SVG
        :param filename: Path to the SVG file to save to.
        :param scale_stroke_factor: factor by which to change/scale the thickness of the features
        :return:
        """
        if filename is None:
            last_save = self.app.options.get("global_last_save_folder", self.app.defaults.get("global_last_save_folder", self.app.defaults.factory_defaults.get("global_last_save_folder")))
            last_folder = self.app.options.get("global_last_folder", self.app.defaults.get("global_last_folder", self.app.defaults.factory_defaults.get("global_last_folder")))
            filename = last_save if last_save is not None else last_folder

        self.log.debug("export_svg()")

        try:
            obj = self.app.collection.get_by_name(str(obj_name))
        except Exception:
            return 'fail'

        with self.app.proc_container.new(_("Exporting ...")):
            exported_svg = obj.export_svg(scale_stroke_factor=scale_stroke_factor)

            # Determine bounding area for svg export
            bounds = obj.bounds()
            size = obj.size()

            # Convert everything to strings for use in the xml doc
            svg_width = str(size[0])
            svg_height = str(size[1])
            minx = str(bounds[0])
            # the content is inside the group that flips the Y axis: it spans y from -ymax to -ymin
            miny = str(-bounds[3])
            uom = obj.units.lower()

            # Add an SVG Header and footer to the svg output from shapely
            # The transform flips the Y Axis so that everything renders
            # properly within svg apps such as inkscape
            svg_header = '<svg xmlns="http://www.w3.org/2000/svg" ' \
                         'version="1.1" xmlns:xlink="http://www.w3.org/1999/xlink" '
            svg_header += 'width="' + svg_width + uom + '" '
            svg_header += 'height="' + svg_height + uom + '" '
            svg_header += 'viewBox="' + minx + ' ' + miny + ' ' + svg_width + ' ' + svg_height + '">'
            svg_header += '<g transform="scale(1,-1)">'
            svg_footer = '</g> </svg>'
            svg_elem = svg_header + exported_svg + svg_footer

            # Parse the xml through a xml parser just to add line feeds
            # and to make it look more pretty for the output
            svg_code = parse_xml_string(svg_elem)
            svg_code = svg_code.toprettyxml()

            try:
                with open(filename, 'w') as fp:
                    fp.write(svg_code)
            except PermissionError:
                self.inform.emit('[WARNING] %s' %
                                 _("Permission denied, saving not possible.\n"
                                   "Most likely another app is holding the file open and not accessible."))
                return 'fail'

            if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style", self.app.defaults.factory_defaults.get("global_open_style"))) is False:
                self.app.file_opened.emit("SVG", filename)
            self.app.file_saved.emit("SVG", filename)
            self.inform.emit('[success] %s: %s' % (_("SVG file exported to"), filename))

    def on_import_preferences(self):
        """
        Loads the application default settings from a saved file into
        ``self.options`` dictionary.

        :return: None
        """

        self.log.debug("App.on_import_preferences()")

        # Show file chooser
        filter_ = "Config File (*.FlatConfig);;All Files (*.*)"
        try:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Import FlatCAM Preferences"),
                                                                 directory=self.app.data_path,
                                                                 filter=filter_)
        except TypeError:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Import FlatCAM Preferences"),
                                                                 filter=filter_)
        filename = str(filename)
        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return

        # Load in the options from the chosen file. The application settings go into the defaults.
        self.options.load(filename=filename, inform=self.inform, baseline=self.app.defaults, include_app_keys=True)
        self.app.preferencesUiManager.defaults_write_form()

        self.app.preferencesUiManager.on_preferences_edited()
        self.inform.emit('[success] %s: %s' % (_("Imported Defaults from"), filename))

    def on_export_preferences(self):
        """
        Save the options dictionary to a file.

        :return: None
        """
        self.log.debug("on_export_preferences()")

        # defaults_file_content = None

        # Show file chooser
        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')
        filter__ = "Config File .FlatConfig (*.FlatConfig);;All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export FlatCAM Preferences"),
                directory=os.path.join(self.app.data_path, 'preferences_%s' % date),
                ext_filter=filter__
            )
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export FlatCAM Preferences"), ext_filter=filter__)
        filename = str(filename)
        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return 'fail'

        # Update options
        self.app.preferencesUiManager.defaults_read_form()
        self.options.propagate_defaults()

        # Save update options
        try:
            self.options.write(filename=filename)
        except Exception:
            self.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed to write defaults to file."), str(filename)))
            return

        if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style", self.app.defaults.factory_defaults.get("global_open_style"))) is False:
            self.app.file_opened.emit("preferences", filename)
        self.app.file_saved.emit("preferences", filename)
        self.inform.emit('[success] %s: %s' % (_("Exported preferences to"), filename))

    def export_excellon(self, obj_name, filename, local_use=None, use_thread=True):
        """
        Exports an Excellon Object to an Excellon file.

        :param obj_name: the name of the FlatCAM object to be saved as Excellon
        :param filename: Path to the Excellon file to save to.
        :param local_use:
        :param use_thread: if to be run in a separate thread
        :return:
        """

        if filename is None:
            last_save = self.app.options.get("global_last_save_folder", self.app.defaults.get("global_last_save_folder", self.app.defaults.factory_defaults.get("global_last_save_folder")))
            last_folder = self.app.options.get("global_last_folder", self.app.defaults.get("global_last_folder", self.app.defaults.factory_defaults.get("global_last_folder")))
            if last_save:
                filename = last_save + '/' + 'exported_excellon'
            else:
                filename = (last_folder or '') + '/' + 'exported_excellon'

        self.log.debug("export_excellon()")

        format_exc = ';FILE_FORMAT=%d:%d\n' % (
            self.app.options.get("excellon_exp_integer", self.app.defaults.get("excellon_exp_integer", self.app.defaults.factory_defaults.get("excellon_exp_integer"))),
            self.app.options.get("excellon_exp_decimals", self.app.defaults.get("excellon_exp_decimals", self.app.defaults.factory_defaults.get("excellon_exp_decimals")))
                                               )

        if local_use is None:
            try:
                obj = self.app.collection.get_by_name(str(obj_name))
            except Exception:
                return "Could not retrieve object: %s" % obj_name
        else:
            obj = local_use

        if not isinstance(obj, ExcellonObject):
            self.inform.emit('[ERROR_NOTCL] %s' %
                             _("Failed. Only Excellon objects can be saved as Excellon files..."))
            return

        # updated units
        e_units = self.app.options.get("excellon_exp_units", self.app.defaults.get("excellon_exp_units", self.app.defaults.factory_defaults.get("excellon_exp_units")))
        e_whole = self.app.options.get("excellon_exp_integer", self.app.defaults.get("excellon_exp_integer", self.app.defaults.factory_defaults.get("excellon_exp_integer")))
        e_fract = self.app.options.get("excellon_exp_decimals", self.app.defaults.get("excellon_exp_decimals", self.app.defaults.factory_defaults.get("excellon_exp_decimals")))
        e_zeros = self.app.options.get("excellon_exp_zeros", self.app.defaults.get("excellon_exp_zeros", self.app.defaults.factory_defaults.get("excellon_exp_zeros")))
        e_format = self.app.options.get("excellon_exp_format", self.app.defaults.get("excellon_exp_format", self.app.defaults.factory_defaults.get("excellon_exp_format")))
        slot_type = self.app.options.get("excellon_exp_slot_type", self.app.defaults.get("excellon_exp_slot_type", self.app.defaults.factory_defaults.get("excellon_exp_slot_type")))

        fc_units = (obj.units if obj.obj_options.get('pcbdoc_import', False) else self.app_units).upper()
        if fc_units == 'MM':
            factor = 1 if e_units == 'METRIC' else 1 / 25.4
        else:
            factor = 25.4 if e_units == 'METRIC' else 1

        def make_excellon():
            try:
                time_str = "{:%A, %d %B %Y at %H:%M}".format(datetime.now())

                header = 'M48\n'
                header += ';EXCELLON GENERATED BY FLATCAM v%s - www.flatcam.org - Version Date: %s\n' % \
                          (str(self.app.version), str(self.app.version_date))

                header += ';Filename: %s' % str(obj_name) + '\n'
                header += ';Created on : %s' % time_str + '\n'

                if e_format == 'dec':
                    has_slots, excellon_code = obj.export_excellon(e_whole, e_fract, factor=factor, slot_type=slot_type)
                    header += e_units + '\n'

                    for tool in obj.tools:
                        if e_units == 'METRIC':
                            header += "T{tool}F00S00C{:.{dec}f}\n".format(float(obj.tools[tool]['tooldia']) * factor,
                                                                          tool=str(tool),
                                                                          dec=max(2, e_fract))
                        else:
                            header += "T{tool}F00S00C{:.{dec}f}\n".format(float(obj.tools[tool]['tooldia']) * factor,
                                                                          tool=str(tool),
                                                                          dec=max(4, e_fract))
                else:
                    if e_zeros == 'LZ':
                        has_slots, excellon_code = obj.export_excellon(e_whole, e_fract,
                                                                       form='ndec', e_zeros='LZ', factor=factor,
                                                                       slot_type=slot_type)
                        header += '%s,%s\n' % (e_units, 'LZ')
                        header += format_exc

                        for tool in obj.tools:
                            if e_units == 'METRIC':
                                header += "T{tool}F00S00C{:.{dec}f}\n".format(
                                    float(obj.tools[tool]['tooldia']) * factor,
                                    tool=str(tool),
                                    dec=max(2, e_fract))
                            else:
                                header += "T{tool}F00S00C{:.{dec}f}\n".format(
                                    float(obj.tools[tool]['tooldia']) * factor,
                                    tool=str(tool),
                                    dec=max(4, e_fract))
                    else:
                        has_slots, excellon_code = obj.export_excellon(e_whole, e_fract,
                                                                       form='ndec', e_zeros='TZ', factor=factor,
                                                                       slot_type=slot_type)
                        header += '%s,%s\n' % (e_units, 'TZ')
                        header += format_exc

                        for tool in obj.tools:
                            if e_units == 'METRIC':
                                header += "T{tool}F00S00C{:.{dec}f}\n".format(
                                    float(obj.tools[tool]['tooldia']) * factor,
                                    tool=str(tool),
                                    dec=max(2, e_fract))
                            else:
                                header += "T{tool}F00S00C{:.{dec}f}\n".format(
                                    float(obj.tools[tool]['tooldia']) * factor,
                                    tool=str(tool),
                                    dec=max(4, e_fract))
                header += '%\n'
                footer = 'M30\n'

                exported_excellon = ''.join([header, excellon_code, footer])

                if local_use is None:
                    try:
                        with open(filename, 'w') as fp:
                            fp.write(exported_excellon)
                    except PermissionError:
                        self.inform.emit('[WARNING] %s' %
                                         _("Permission denied, saving not possible.\n"
                                           "Most likely another app is holding the file open and not accessible."))
                        return 'fail'

                    if obj.obj_options.get('pcbdoc_import', False):
                        obj.source_file = exported_excellon
                    if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style", self.app.defaults.factory_defaults.get("global_open_style"))) is False:
                        self.app.file_opened.emit("Excellon", filename)
                    self.app.file_saved.emit("Excellon", filename)
                    self.inform.emit('[success] %s: %s' % (_("Excellon file exported to"), filename))
                else:
                    if obj.obj_options.get('pcbdoc_import', False):
                        obj.source_file = exported_excellon
                    return exported_excellon
            except Exception as e:
                self.log.error("App.export_excellon.make_excellon() --> %s" % str(e))
                return 'fail'

        if use_thread is True:

            with self.app.proc_container.new(_("Exporting ...")):

                def job_thread_exc(app_obj):
                    ret = make_excellon()
                    if ret == 'fail':
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % _('Could not export.'))
                        return

                self.worker_task.emit({'fcn': job_thread_exc, 'params': [self]})
        else:
            ret_val = make_excellon()
            if ret_val == 'fail':
                self.inform.emit('[ERROR_NOTCL] %s' % _('Could not export.'))
                return 'fail'
            if local_use is not None:
                return ret_val

    def export_gerber(self, obj_name, filename, local_use=None, use_thread=True):
        """
        Exports a Gerber Object to a Gerber file.

        :param obj_name:    the name of the FlatCAM object to be saved as Gerber
        :param filename:    Path to the Gerber file to save to.
        :param local_use:   if the Gerber code is to be saved to a file (None) or used within FlatCAM.
                            When not None, the value will be the actual Gerber object for which to create
                            the Gerber code
        :param use_thread:  if to be run in a separate thread
        :return:
        """
        if filename is None:
            last_save = self.app.options.get("global_last_save_folder", self.app.defaults.get("global_last_save_folder", self.app.defaults.factory_defaults.get("global_last_save_folder")))
            last_folder = self.app.options.get("global_last_folder", self.app.defaults.get("global_last_folder", self.app.defaults.factory_defaults.get("global_last_folder")))
            filename = last_save if last_save is not None else last_folder

        self.log.debug("export_gerber()")

        if local_use is None:
            try:
                obj = self.app.collection.get_by_name(str(obj_name))
            except Exception:
                return 'fail'
        else:
            obj = local_use

        # updated units
        g_units = self.app.options.get("gerber_exp_units", self.app.defaults.get("gerber_exp_units", self.app.defaults.factory_defaults.get("gerber_exp_units")))
        g_whole = self.app.options.get("gerber_exp_integer", self.app.defaults.get("gerber_exp_integer", self.app.defaults.factory_defaults.get("gerber_exp_integer")))
        g_fract = self.app.options.get("gerber_exp_decimals", self.app.defaults.get("gerber_exp_decimals", self.app.defaults.factory_defaults.get("gerber_exp_decimals")))
        g_zeros = self.app.options.get("gerber_exp_zeros", self.app.defaults.get("gerber_exp_zeros", self.app.defaults.factory_defaults.get("gerber_exp_zeros")))

        fc_units = (obj.units if obj.obj_options.get('pcbdoc_import', False) else self.app_units).upper()
        if fc_units == 'MM':
            factor = 1 if g_units == 'MM' else 0.03937
        else:
            factor = 25.4 if g_units == 'MM' else 1

        # P and AM apertures, dark/clear sequences, transformed metadata and PCB-document imports can't be
        # written natively: the composed geometry is exported as regions
        # (called through the class: it only reads attributes, so stand-in objects work too)
        use_regions = Gerber.needs_region_export(obj)

        def make_gerber():
            try:
                time_str = "{:%A, %d %B %Y at %H:%M}".format(datetime.now())

                header = 'G04*\n'
                header += 'G04 RS-274X GERBER GENERATED BY FLATCAM v%s - www.flatcam.org - Version Date: %s*\n' % \
                          (str(self.app.version), str(self.app.version_date))

                header += 'G04 Filename: %s*' % str(obj_name) + '\n'
                header += 'G04 Created on : %s*' % time_str + '\n'
                header += '%%FS%sAX%s%sY%s%s*%%\n' % (g_zeros, g_whole, g_fract, g_whole, g_fract)
                header += "%MO{units}*%\n".format(units=g_units)

                # the region export writes the composed geometry only, so it declares no aperture
                for apid in ({} if use_regions else obj.tools):
                    if obj.tools[apid]['type'] == 'C':
                        header += "%ADD{apid}{type},{size}*%\n".format(
                            apid=str(apid),
                            type='C',
                            size=(factor * obj.tools[apid]['size'])
                        )
                    elif obj.tools[apid]['type'] == 'R':
                        header += "%ADD{apid}{type},{width}X{height}*%\n".format(
                            apid=str(apid),
                            type='R',
                            width=(factor * obj.tools[apid]['width']),
                            height=(factor * obj.tools[apid]['height'])
                        )
                    elif obj.tools[apid]['type'] == 'O':
                        header += "%ADD{apid}{type},{width}X{height}*%\n".format(
                            apid=str(apid),
                            type='O',
                            width=(factor * obj.tools[apid]['width']),
                            height=(factor * obj.tools[apid]['height'])
                        )

                header += '\n'

                # obsolete units but some software may need it
                if g_units == 'IN':
                    header += 'G70*\n'
                else:
                    header += 'G71*\n'

                # Absolute Mode
                header += 'G90*\n'

                header += 'G01*\n'
                # positive polarity
                header += '%LPD*%\n'

                footer = 'M02*\n'

                tools = obj.tools
                try:
                    if use_regions:
                        # Union overlaps so region holes don't clear other solids. Draw islands last.
                        obj.tools = Gerber.region_export_tools(obj)
                        gerber_code = obj.export_gerber(g_whole, g_fract, g_zeros='L', factor=factor)
                        if g_zeros == 'T':
                            # Pad the magnitude, not the sign, for valid negative region coordinates.
                            gerber_code = re.sub(
                                r'([XY])(-?)(\d+)',
                                lambda match: '%s%s%s' % (match[1], match[2], match[3].zfill(g_whole + g_fract)),
                                gerber_code)
                    else:
                        gerber_code = obj.export_gerber(g_whole, g_fract, g_zeros=g_zeros, factor=factor)
                finally:
                    obj.tools = tools
                if obj.obj_options.get('pcbdoc_import', False) and (not gerber_code or gerber_code == 'fail'):
                    return 'fail'

                exported_gerber = ''.join([header, gerber_code, footer])

                if local_use is None:
                    try:
                        with open(filename, 'w') as fp:
                            fp.write(exported_gerber)
                    except PermissionError:
                        self.inform.emit('[WARNING] %s' %
                                         _("Permission denied, saving not possible.\n"
                                           "Most likely another app is holding the file open and not accessible."))
                        return 'fail'

                    if obj.obj_options.get('pcbdoc_import', False):
                        obj.source_file = exported_gerber
                    if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style", self.app.defaults.factory_defaults.get("global_open_style"))) is False:
                        self.app.file_opened.emit("Gerber", filename)
                    self.app.file_saved.emit("Gerber", filename)
                    self.inform.emit('[success] %s: %s' % (_("Gerber file exported to"), filename))
                else:
                    if obj.obj_options.get('pcbdoc_import', False):
                        obj.source_file = exported_gerber
                    return exported_gerber
            except Exception as e:
                self.log.error("App.export_gerber.make_gerber() --> %s" % str(e))
                return 'fail'

        if use_thread is True:
            with self.app.proc_container.new(_("Exporting ...")):

                def job_thread_grb(app_obj):
                    ret = make_gerber()
                    if ret == 'fail':
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % _('Could not export.'))
                        return 'fail'

                self.worker_task.emit({'fcn': job_thread_grb, 'params': [self]})
        else:
            gret = make_gerber()
            if gret == 'fail':
                self.inform.emit('[ERROR_NOTCL] %s' % _('Could not export.'))
                return 'fail'
            if local_use is not None:
                return gret

    def export_dxf(self, obj_name, filename, local_use=None, use_thread=True):
        """
        Exports a Geometry Object to an DXF file.

        :param obj_name:    the name of the FlatCAM object to be saved as DXF
        :param filename:    Path to the DXF file to save to.
        :param local_use:   if the DXF code is to be saved to a file (None) or used within FlatCAM.
                            When not None, the value will be the actual Geometry object for which to create
                            the Geometry/DXF code as serialization-safe text
        :param use_thread:  if to be run in a separate thread
        :return:
        """
        if filename is None:
            last_save = self.app.options.get("global_last_save_folder", self.app.defaults.get("global_last_save_folder", self.app.defaults.factory_defaults.get("global_last_save_folder")))
            last_folder = self.app.options.get("global_last_folder", self.app.defaults.get("global_last_folder", self.app.defaults.factory_defaults.get("global_last_folder")))
            filename = last_save if last_save is not None else last_folder

        self.log.debug("export_dxf()")

        if local_use is None:
            try:
                obj = self.app.collection.get_by_name(str(obj_name))
            except Exception:
                return 'fail'
        else:
            obj = local_use

        def make_dxf():
            try:
                dxf_code = obj.export_dxf()
                if dxf_code is None:
                    return 'fail'
                if local_use is None:
                    try:
                        dxf_code.saveas(filename)
                    except PermissionError:
                        self.inform.emit('[WARNING] %s' %
                                         _("Permission denied, saving not possible.\n"
                                           "Most likely another app is holding the file open and not accessible."))
                        return 'fail'

                    if self.app.options.get("global_open_style", self.app.defaults.get("global_open_style", self.app.defaults.factory_defaults.get("global_open_style"))) is False:
                        self.app.file_opened.emit("DXF", filename)
                    self.app.file_saved.emit("DXF", filename)
                    self.inform.emit('[success] %s: %s' % (_("DXF file exported to"), filename))
                else:
                    with StringIO() as stream:
                        dxf_code.write(stream)
                        return stream.getvalue()
            except Exception as e:
                self.log.error("App.export_dxf.make_dxf() --> %s" % str(e))
                return 'fail'

        if use_thread is True:

            with self.app.proc_container.new(_("Exporting ...")):

                def job_thread_exc(app_obj):
                    ret_dxf_val = make_dxf()
                    if ret_dxf_val == 'fail':
                        app_obj.inform.emit('[WARNING_NOTCL] %s' % _('Could not export.'))
                        return

                self.worker_task.emit({'fcn': job_thread_exc, 'params': [self]})
        else:
            ret = make_dxf()
            if ret == 'fail':
                self.inform.emit('[WARNING_NOTCL] %s' % _('Could not export.'))
                return
            if local_use is not None:
                return ret

    def import_svg(self, filename, geo_type='geometry', outname=None, plot=True):
        """
        Adds a new Geometry Object to the projects and populates
        it with shapes extracted from the SVG file.

        :param plot:        If True then the resulting object will be plotted on canvas
        :param filename:    Path to the SVG file.
        :param geo_type:    Type of FlatCAM object that will be created from SVG
        :param outname:     The name given to the resulting FlatCAM object
        :return:
        """
        self.log.debug("App.import_svg()")
        if not os.path.exists(filename):
            self.inform.emit('[ERROR_NOTCL] %s' % _("File no longer available."))
            return

        obj_type = ""
        if geo_type is None or geo_type == "geometry":
            obj_type = "geometry"
        elif geo_type == "gerber":
            obj_type = "gerber"
        else:
            self.inform.emit('[ERROR_NOTCL] %s' %
                             _("Not supported type is picked as parameter. Only Geometry and Gerber are supported"))
            return

        units = self.app_units.upper()

        def obj_init(geo_obj, app_obj):
            res = geo_obj.import_svg(filename, obj_type, units=units)
            if res == 'fail':
                return 'fail'

            geo_obj.multigeo = True

            # the text of the parsed file, for the source view and the project: a byte that is not UTF-8 is
            # replaced, so the code page of the system can not fail the import
            with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                file_content = f.read()
            geo_obj.source_file = file_content

            # appGUI feedback
            app_obj.inform.emit('[success] %s: %s' % (_("Opened"), filename))

        with self.app.proc_container.new('%s ...' % _("Importing")):

            # Object name
            name = outname or filename.split('/')[-1].split('\\')[-1]

            ret = self.app.app_obj.new_object(obj_type, name, obj_init, autoselected=False, plot=plot)

            if ret == 'fail':
                self.inform.emit('[ERROR_NOTCL]%s' % _('Import failed.'))
                return 'fail'

            # Register recent file
            self.app.file_opened.emit("svg", filename)

    def import_dxf(self, filename, geo_type='geometry', outname=None, plot=True, text_mode='stroke'):
        """
        Adds a new Geometry Object to the projects and populates
        it with shapes extracted from the DXF file.

        :param filename:    Path to the DXF file.
        :param geo_type:    Type of FlatCAM object that will be created from DXF
        :param outname:     Name for the imported Geometry
        :param plot:        If True then the resulting object will be plotted on canvas
        :param text_mode:   'stroke' for CNC engraving paths, 
                            'outline' for filled shapes,
                            'none' to skip text import
        :return:
        """
        self.log.debug(" ********* Importing DXF as: %s ********* " % geo_type.capitalize())
        if not os.path.exists(filename):
            self.inform.emit('[ERROR_NOTCL] %s' % _("File no longer available."))
            return

        obj_type = ""
        if geo_type is None or geo_type == "geometry":
            obj_type = "geometry"
        elif geo_type == "gerber":
            obj_type = geo_type
        else:
            self.inform.emit('[ERROR_NOTCL] %s' %
                             _("Not supported type is picked as parameter. Only Geometry and Gerber are supported"))
            return

        units = self.app_units.upper()

        def obj_init(geo_obj, app_obj):
            if obj_type == "geometry":
                geo_obj.import_dxf_as_geo(filename, units=units, text_mode=text_mode)
            elif obj_type == "gerber":
                geo_obj.import_dxf_as_gerber(filename, units=units, text_mode=text_mode)
            else:
                return "fail"

            # ezdxf read the file in its own code page ($DWGCODEPAGE); the text kept here for the source view and
            # the project replaces a byte that is not UTF-8 (an ANSI DXF), it must not fail the import
            with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                file_content = f.read()
            geo_obj.source_file = file_content

            # appGUI feedback
            app_obj.inform.emit('[success] %s: %s' % (_("Opened"), filename))

        with self.app.proc_container.new('%s ...' % _("Importing")):

            # Object name
            name = outname or filename.split('/')[-1].split('\\')[-1]

            ret = self.app.app_obj.new_object(obj_type, name, obj_init, autoselected=False, plot=plot)

            if ret == 'fail':
                self.inform.emit('[ERROR_NOTCL]%s' % _('Import failed.'))
                return 'fail'

            # Register recent file
            self.app.file_opened.emit("dxf", filename)

    def import_pdf(self, filename):
        self.app.pdf_tool.periodic_check(1000)
        self.worker_task.emit({'fcn': self.app.pdf_tool.open_pdf, 'params': [filename]})

    def open_gerber(self, filename, outname=None, plot=True, from_tcl=False):
        """
        Opens a Gerber file, parses it and creates a new object for
        it in the program. Thread-safe.

        :param outname:     Name of the resulting object. None causes the
                            name to be that of the file. Str.
        :param filename:    Gerber file filename
        :type filename:     str
        :param plot:        boolean, to plot or not the resulting object
        :param from_tcl:    True if run from Tcl Shell
        :return: None
        """

        # How the object should be initialized
        def obj_init(gerber_obj, app_obj):

            assert isinstance(gerber_obj, GerberObject), \
                "Expected to initialize a GerberObject but got %s" % type(gerber_obj)

            # Opening the file happens here
            try:
                parse_ret_val = gerber_obj.parse_file(filename)
            except IOError:
                app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to open file"), filename))
                return "fail"
            except ParseError as parse_err:
                app_obj.inform.emit('[ERROR_NOTCL] %s: %s. %s' % (_("Failed to parse file"), filename, str(parse_err)))
                app_obj.log.error(str(parse_err))
                return "fail"
            except Exception as e:
                app_obj.log.error("App.open_gerber() --> %s" % str(e))
                msg = '[ERROR] %s' % _("An internal error has occurred. See shell.\n")
                msg += traceback.format_exc()
                app_obj.inform.emit(msg)
                return "fail"

            if gerber_obj.is_empty():
                app_obj.inform.emit('[ERROR_NOTCL] %s' %
                                    _("Object is not Gerber file or empty. Aborting object creation."))
                return "fail"

            gerber_obj.obj_options['source_path'] = os.path.abspath(filename)

            if parse_ret_val:
                return parse_ret_val

        self.log.debug("open_gerber()")
        if from_tcl and not os.path.exists(filename):
            filename = os.path.join(self.app.options.get('global_tcl_path', self.app.defaults.get('global_tcl_path')),
                                    os.path.basename(filename.replace('\\', '/')))
        if not os.path.exists(filename):
            self.inform.emit('[ERROR_NOTCL] %s. %s' % (filename, _("File no longer available.")))
            return 'fail' if from_tcl else None

        with self.app.proc_container.new('%s...' % _("Opening")):
            # Object name
            name = outname or filename.split('/')[-1].split('\\')[-1]

            # # ## Object creation # ##
            ret_val = self.app.app_obj.new_object("gerber", name, obj_init, autoselected=False, plot=plot)
            if ret_val == "defective":
                message = '[ERROR] %s' % \
                          _('The Gerber file is DAMAGED. We could open it but the file parsing PARTIALLY FAILED.\n'
                            '!!! CHECK THE FILE !!! --- SOME copper features (pads, traces etc) ARE MISSING !!!\n'
                            '!!! CHECK THE FILE !!! --- SOME copper features (pads, traces etc) ARE MISSING !!!\n'
                            '!!! CHECK THE FILE !!! --- SOME copper features (pads, traces etc) ARE MISSING !!!\n')
                self.inform.emit(message)
                return 'fail' if from_tcl else None
            if ret_val == 'fail':
                if from_tcl:
                    filename = os.path.join(self.app.options.get('global_tcl_path', self.app.defaults.get('global_tcl_path')),
                                            os.path.basename(filename.replace('\\', '/')))
                    ret_val = self.app.app_obj.new_object("gerber", name, obj_init, autoselected=False, plot=plot)
                if ret_val == 'fail':
                    self.inform.emit('[ERROR_NOTCL] %s' % _('Open Gerber failed. Probable not a Gerber file.'))
                    return 'fail'

            # Register recent file
            self.app.file_opened.emit("gerber", filename)

            # appGUI feedback
            self.app.inform.emit('[success] %s: %s' % (_("Opened"), filename))

    def open_excellon(self, filename, outname=None, plot=True, from_tcl=False):
        """
        Opens an Excellon file, parses it and creates a new object for
        it in the program. Thread-safe.

        :param outname:     Name of the resulting object. None causes the name to be that of the file.
        :param filename:    Excellon file filename
        :type filename:     str
        :param plot:        boolean, to plot or not the resulting object
        :param from_tcl:    True if run from Tcl Shell
        :return:            None
        """

        self.log.debug("open_excellon()")

        if from_tcl and not os.path.exists(filename):
            filename = os.path.join(self.app.options.get('global_tcl_path', self.app.defaults.get('global_tcl_path')),
                                    os.path.basename(filename.replace('\\', '/')))
        if not os.path.exists(filename):
            self.inform.emit('[ERROR_NOTCL] %s. %s' % (filename, _("File no longer available.")))
            return 'fail' if from_tcl else None

        # How the object should be initialized
        def obj_init(excellon_obj, app_obj):
            # populate excellon_obj.tools dict
            try:
                ret = excellon_obj.parse_file(filename=filename)
                if ret == "fail":
                    app_obj.log.debug("Excellon parsing failed.")
                    self.inform.emit('[ERROR_NOTCL] %s' % _("This is not Excellon file."))
                    return "fail"
            except IOError:
                app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Cannot open file"), filename))
                app_obj.log.debug("Could not open Excellon object.")
                return "fail"
            except Exception:
                msg = '[ERROR_NOTCL] %s' % _("An internal error has occurred. See shell.\n")
                msg += traceback.format_exc()
                app_obj.inform.emit(msg)
                return "fail"

            excellon_obj.obj_options['source_path'] = os.path.abspath(filename)

            # populate excellon_obj.solid_geometry list; if the object is converted to the app units right after
            # this, convert_units() makes the geometry, so it is not made twice
            app_units = str(app_obj.options.get('units', app_obj.app_units)).upper()
            converted_next = str(excellon_obj.units).upper() != app_units
            if not converted_next:
                ret = excellon_obj.create_geometry()
                if ret == 'fail':
                    app_obj.log.debug("Could not create geometry for Excellon object.")
                    return "fail"

            for tool in excellon_obj.tools:
                if excellon_obj.tools[tool]['solid_geometry'] or \
                        (converted_next and (excellon_obj.tools[tool]['drills'] or excellon_obj.tools[tool]['slots'])):
                    return
            app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("No geometry found in file"), filename))
            return "fail"

        with self.app.proc_container.new('%s...' % _("Opening")):
            # Object name
            name = outname or filename.split('/')[-1].split('\\')[-1]
            ret_val = self.app.app_obj.new_object("excellon", name, obj_init, autoselected=False, plot=plot)
            if ret_val == 'fail':
                if from_tcl:
                    filename = os.path.join(self.app.options.get('global_tcl_path', self.app.defaults.get('global_tcl_path')),
                                            os.path.basename(filename.replace('\\', '/')))
                    ret_val = self.app.app_obj.new_object("excellon", name, obj_init, autoselected=False, plot=plot)
                if ret_val == 'fail':
                    self.inform.emit('[ERROR_NOTCL] %s' %
                                     _('Open Excellon file failed. Probable not an Excellon file.'))
                    return 'fail'

            # Register recent file
            self.app.file_opened.emit("excellon", filename)

            # appGUI feedback
            self.inform.emit('[success] %s: %s' % (_("Opened"), filename))

    def open_gcode(self, filename, outname=None, force_parsing=None, plot=True, from_tcl=False):
        """
        Opens a G-code file and creates a CNCJob object for it, built like a native one (Drilling / Milling): the
        tools, the parsed geometry and the options come from the parsing of the text, the text itself is kept as it is.
        Thread-safe.

        :param filename:        G-code file filename
        :param outname:         Name of the resulting object. None causes the name to be that of the file.
        :param force_parsing:   not used (kept for the callers)
        :param plot:            If True, then plot the object on canvas
        :param from_tcl:        True if run from Tcl Shell
        :return:                None, 'fail' on failure
        """
        self.log.debug("open_gcode()")

        if from_tcl and not os.path.exists(filename):
            filename = os.path.join(self.app.options.get('global_tcl_path', self.app.defaults.get('global_tcl_path')),
                                    os.path.basename(filename.replace('\\', '/')))
        if not os.path.exists(filename):
            self.inform.emit('[ERROR_NOTCL] %s' % _("File no longer available."))
            return 'fail' if from_tcl else None

        # the error that obj_init told the user about
        reported = []
        # what the job assumes because the file does not say it; told to the user once, after the job is made
        notes = []
        default_dia_tools = []
        default_values = []

        def obj_init(job_obj, app_obj_: "appMain.App"):
            """
            :param job_obj: the resulting object
            :type app_obj_: App
            """
            from appParsers.ParseGCode import parse_gcode

            def fail(message):
                reported.append(message)
                app_obj_.inform.emit('[ERROR_NOTCL] %s' % message)      # noqa
                return 'fail'

            app_obj_.inform.emit('%s...' % _("Reading GCode file"))     # noqa
            try:
                with open(filename, 'rb') as f:
                    raw = f.read()
            except OSError:
                return fail('%s: %s' % (_("Failed to open"), filename))
            # the text is kept exactly (line endings too); a file that is not UTF-8 is read as latin-1
            encoding = 'utf-8'
            try:
                gcode = raw.decode(encoding)
            except UnicodeDecodeError:
                encoding = 'latin-1'
                gcode = raw.decode(encoding)
            job_obj.gcode_encoding = encoding

            options = app_obj_.options
            units = str(app_obj_.app_units).upper()

            def option_dia(key, fallback_mm):
                # the first diameter of the option (it can be a list of diameters); the option is in the app units
                try:
                    dia = float(str(options.get(key)).split(',')[0])
                except (TypeError, ValueError):
                    dia = 0.0
                if dia <= 0:
                    dia = fallback_mm if units == 'MM' else fallback_mm / 25.4
                return dia

            # a tool without a diameter in the file: the default drill or mill tool of the application
            drill_tooldia = option_dia('excellon_drill_tooldia', 0.8)
            mill_tooldia = option_dia('tools_mill_tooldia', 2.4)
            try:
                feedrate_rapid = float(options.get('tools_mill_feedrate_rapid'))
            except (TypeError, ValueError):
                feedrate_rapid = None

            try:
                # (the default diameter is used only for the drill rings of an Excellon job)
                parsed = parse_gcode(gcode, steps_per_circle=job_obj.steps_per_circle,
                                     default_tooldia=drill_tooldia, feedrate_rapid=feedrate_rapid,
                                     target_units=units)
            except Exception as err:
                self.log.error("open_gcode() --> %s" % str(err))
                return fail(_("This is not GCODE"))
            if not parsed or not parsed.get('motion_count') or not parsed.get('tools'):
                return fail(_("This is not GCODE"))

            if parsed.get('warnings'):
                self.log.warning("open_gcode() --> %s" % '; '.join(parsed['warnings']))
                shown = '; '.join(parsed['warnings'][:3])
                if len(parsed['warnings']) > 3:
                    shown += ' (+%d)' % (len(parsed['warnings']) - 3)
                notes.append('%s: %s' % (_("The G-code has unsupported parts"), shown))
            if not parsed.get('units'):
                notes.append(_("no G20/G21 in the file, the units of the application are assumed"))

            is_exc = parsed['type'] == 'Excellon'
            prefix = 'tools_drill_' if is_exc else 'tools_mill_'
            default_tooldia = drill_tooldia if is_exc else mill_tooldia
            pp_name = parsed.get('preprocessor')
            if pp_name not in getattr(app_obj_, 'preprocessors', {}):
                pp_name = 'default'

            job_obj.units = units
            job_obj.multitool = True
            job_obj.multigeo = False
            job_obj.gcode_imported = True
            job_obj.obj_options['type'] = parsed['type']
            job_obj.obj_options['gcode_units'] = parsed.get('units') or ''
            if is_exc:
                job_obj.pp_excellon_name = pp_name
            else:
                job_obj.pp_geometry_name = pp_name

            job_obj.tools = {}
            total_parsed = []
            for key, tool in parsed['tools'].items():
                tooldia = tool.get('tooldia')
                if tooldia is None or float(tooldia) <= 0:
                    tooldia = default_tooldia
                    default_dia_tools.append(str(key))
                else:
                    tooldia = float(tooldia)
                # the data of a native tool: the application defaults of the tool, then what the G-code says
                data = {k: deepcopy(v) for k, v in options.items() if k.startswith(prefix)}
                found = {'cutz': tool.get('z_cut'), 'travelz': tool.get('z_move'),
                         'feedrate_z': tool.get('feedrate_z'), 'spindlespeed': tool.get('spindlespeed')}
                if not re.search(r'[Zz]\s*[-+.\d]', tool['gcode']):
                    # no Z move in the block: the Z values are not from the file, the defaults of the tool stay
                    found['cutz'] = found['travelz'] = None
                if is_exc:
                    data[prefix + 'ppname_e'] = pp_name
                else:
                    found['feedrate'] = tool.get('feedrate')
                    data[prefix + 'tooldia'] = tooldia
                    data[prefix + 'offset_value'] = 0.0
                    data[prefix + 'ppname_g'] = pp_name
                data.update({prefix + name: value for name, value in found.items() if value is not None})
                for name, value in found.items():
                    if value is None and name not in default_values:
                        default_values.append(name)

                entry = {
                    'tool': tool.get('tool') if tool.get('tool') is not None else key,
                    'tooldia': tooldia,
                    'data': data,
                    'gcode': tool['gcode'],
                    'gcode_parsed': tool['gcode_parsed'],
                    'solid_geometry': tool['solid_geometry'],
                }
                if is_exc:
                    entry.update({
                        'drills': [Point(x, y) for x, y in tool.get('drills') or []],
                        'slots': [],
                        'nr_drills': int(tool.get('nr_drills') or 0),
                        'nr_slots': int(tool.get('nr_slots') or 0),
                        'offset': 0.0,
                    })
                job_obj.tools[key] = entry
                total_parsed += tool['gcode_parsed']
            if default_values:
                labels = {'cutz': _("Cut Z"), 'travelz': _("Travel Z"), 'feedrate': _("Feedrate X-Y"),
                          'feedrate_z': _("Feedrate Z"), 'spindlespeed': _("Spindle speed")}
                notes.insert(0, '%s: %s' % (_("Not in the file, the default values are used"),
                                            ', '.join(labels[name] for name in default_values)))
            if default_dia_tools:
                notes.insert(0, _("No tool diameter in the file for the tools %s: the default %s is used") % (
                    ', '.join(default_dia_tools), '%g %s' % (default_tooldia, units.lower())))
            job_obj.used_tools = list(job_obj.tools.keys())

            first_dia = next(iter(job_obj.tools.values()))['tooldia']
            job_obj.obj_options['tooldia'] = first_dia
            job_obj.obj_options['tool_dia'] = first_dia
            job_obj.tooldia = first_dia
            for attr, name, pick in (('z_cut', 'z_cut', min), ('z_move', 'z_move', max),
                                     ('feedrate', 'feedrate', None), ('z_feedrate', 'feedrate_z', None),
                                     ('spindlespeed', 'spindlespeed', None)):
                values = [t[name] for t in parsed['tools'].values() if t.get(name) is not None]
                if values:
                    setattr(job_obj, attr, pick(values) if pick else values[0])
            if feedrate_rapid is not None:
                job_obj.feedrate_rapid = feedrate_rapid

            bounds = parsed.get('bounds') or (0.0, 0.0, 0.0, 0.0)
            (job_obj.obj_options['xmin'], job_obj.obj_options['ymin'],
             job_obj.obj_options['xmax'], job_obj.obj_options['ymax']) = bounds

            job_obj.coords_decimals = int(options.get('cncjob_coords_decimals', 4))
            job_obj.fr_decimals = int(options.get('cncjob_fr_decimals', 2))
            job_obj.travel_distance = parsed.get('travel_distance') or 0.0
            job_obj.routing_time = parsed.get('routing_time') or 0.0

            job_obj.gcode = gcode
            job_obj.source_file = gcode
            job_obj.gcode_parsed = total_parsed
            job_obj.create_geometry()

        with self.app.proc_container.new('%s...' % _("Opening")):

            # Object name
            name = outname or filename.split('/')[-1].split('\\')[-1]

            # New object creation and file processing
            ret_val = self.app.app_obj.new_object("cncjob", name, obj_init, autoselected=False, plot=plot)
            if ret_val == 'fail':
                if not reported:
                    self.inform.emit('[ERROR_NOTCL] %s' % _("Failed to create the CNCJob object from the G-code file."))
                return "fail"

            # Register recent file
            self.app.file_opened.emit("cncjob", filename)

            # appGUI feedback
            self.inform.emit('[success] %s: %s' % (_("Opened"), filename))
            if notes:
                self.inform.emit('[WARNING_NOTCL] %s: %s' % (_("G-code without complete tool data"),
                                                              '; '.join(notes)))

    def open_hpgl2(self, filename, outname=None):
        """
        Opens a HPGL2 file, parses it and creates a new object for
        it in the program. Thread-safe.

        :param outname:     Name of the resulting object. None causes the name to be that of the file.
        :param filename:    HPGL2 file filename
        :return:            None
        """
        filename = filename

        # How the object should be initialized
        def obj_init(geo_obj, app_obj):

            assert isinstance(geo_obj, GeometryObject), \
                "Expected to initialize a GeometryObject but got %s" % type(geo_obj)

            # Opening the file happens here
            obj = HPGL2(self.app)
            try:
                HPGL2.parse_file(obj, filename)
            except IOError:
                app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to open file"), filename))
                return "fail"
            except ParseError as parse_err:
                app_obj.inform.emit('[ERROR_NOTCL] %s: %s. %s' % (_("Failed to parse file"), filename, str(parse_err)))
                app_obj.log.error(str(parse_err))
                return "fail"
            except Exception as e:
                app_obj.log.error("App.open_hpgl2() --> %s" % str(e))
                msg = '[ERROR] %s' % _("An internal error has occurred. See shell.\n")
                msg += traceback.format_exc()
                app_obj.inform.emit(msg)
                return "fail"

            geo_obj.multigeo = True
            geo_obj.solid_geometry = deepcopy(obj.solid_geometry)
            geo_obj.tools = deepcopy(obj.tools)
            geo_obj.source_file = deepcopy(obj.source_file)

            del obj

            if not geo_obj.solid_geometry:
                app_obj.inform.emit('[ERROR_NOTCL] %s' %
                                    _("Object is not HPGL2 file or empty. Aborting object creation."))
                return "fail"

        self.log.debug("open_hpgl2()")

        with self.app.proc_container.new('%s...' % _("Opening")):
            # Object name
            name = outname or filename.split('/')[-1].split('\\')[-1]

            # # ## Object creation # ##
            ret = self.app.app_obj.new_object("geometry", name, obj_init, autoselected=False)
            if ret == 'fail':
                self.inform.emit('[ERROR_NOTCL]%s' % _('Failed. Probable not a HPGL2 file.'))
                return 'fail'

            # Register recent file
            self.app.file_opened.emit("geometry", filename)

            # appGUI feedback
            self.inform.emit('[success] %s: %s' % (_("Opened"), filename))

    def open_script(self, filename, outname=None, silent=False):
        """
        Opens a Script file, parses it and creates a new object for
        it in the program. Thread-safe.

        :param outname:     Name of the resulting object. None causes the name to be that of the file.
        :param filename:    Script file filename
        :param silent:      If True there will be no messages printed to StatusBar
        :return:            None
        """

        def obj_init(script_obj, app_obj):

            assert isinstance(script_obj, ScriptObject), \
                "Expected to initialize a ScriptObject but got %s" % type(script_obj)

            if silent is False:
                app_obj.inform.emit('[success] %s' % _("TCL script file opened in Code Editor."))

            try:
                script_obj.parse_file(filename)
            except IOError:
                app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to open file"), filename))
                return "fail"
            except ParseError as parse_err:
                app_obj.inform.emit('[ERROR_NOTCL] %s: %s. %s' % (_("Failed to parse file"), filename, str(parse_err)))
                app_obj.log.error(str(parse_err))
                return "fail"
            except Exception as e:
                app_obj.log.error("App.open_script() -> %s" % str(e))
                msg = '[ERROR] %s' % _("An internal error has occurred. See shell.\n")
                msg += traceback.format_exc()
                app_obj.inform.emit(msg)
                return "fail"

        self.log.debug("open_script()")
        if not os.path.exists(filename):
            self.inform.emit('[ERROR_NOTCL] %s' % _("File no longer available."))
            return

        with self.app.proc_container.new('%s...' % _("Opening")):

            # Object name
            script_name = outname or filename.split('/')[-1].split('\\')[-1]

            # Object creation
            ret_val = self.app.app_obj.new_object("script", script_name, obj_init, autoselected=False, plot=False)
            if ret_val == 'fail':
                filename = self.app.options.get('global_tcl_path', self.app.defaults.get('global_tcl_path')) + '/' + script_name
                ret_val = self.app.app_obj.new_object("script", script_name, obj_init, autoselected=False, plot=False)
                if ret_val == 'fail':
                    self.inform.emit('[ERROR_NOTCL]%s' % _('Failed to open TCL Script.'))
                    return 'fail'

            # Register recent file
            self.app.file_opened.emit("script", filename)

            # appGUI feedback
            self.inform.emit('[success] %s: %s' % (_("Opened"), filename))

    def on_file_open_document(self, name=None):
        """
        Ask for FlatDoc files and open each one as a Document object.

        :param name:    name of a FlatDoc file to open, no dialog if given
        :return:        None
        """
        self.log.debug("on_file_open_document()")

        _filter_ = "FlatCAM Documents (*.FlatDoc);;All Files (*.*)"

        if name:
            filenames = [name]
        else:
            try:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(
                    caption=_("Open Document"), directory=self.app.get_last_folder(), filter=_filter_)
            except TypeError:
                filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open Document"), filter=_filter_)

        if len(filenames) == 0:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return

        for filename in filenames:
            if filename != '':
                self.worker_task.emit({'fcn': self.open_document, 'params': [filename]})

    def open_document(self, filename, outname=None):
        """
        Open a FlatDoc file and make a Document object with its text. Thread-safe.

        :param filename:    FlatDoc file
        :param outname:     name of the new object, None takes the name of the file
        :return:            'fail' on error
        """
        self.log.debug("open_document()")
        if not os.path.exists(filename):
            self.inform.emit('[ERROR_NOTCL] %s' % _("File no longer available."))
            return 'fail'

        try:
            with open(filename, "r", encoding="utf-8") as opened_doc:
                content = opened_doc.read()
        except (IOError, UnicodeDecodeError) as e:
            self.inform.emit('[ERROR_NOTCL] %s: %s. %s' % (_("Failed to open file"), filename, str(e)))
            return 'fail'

        def obj_init(doc_obj, app_obj):
            doc_obj.source_file = content

        with self.app.proc_container.new('%s...' % _("Opening")):
            doc_name = outname or filename.split('/')[-1].split('\\')[-1]
            ret_val = self.app.app_obj.new_object("document", doc_name, obj_init, autoselected=False, plot=False)
            if ret_val == 'fail':
                self.inform.emit('[ERROR_NOTCL]%s' % _('Failed to open Document.'))
                return 'fail'

            self.app.file_opened.emit("document", filename)
            self.inform.emit('[success] %s: %s' % (_("Opened"), filename))

    def on_file_open_geometry(self):
        """
        Ask for SVG or DXF files and import each one as a Geometry object, by the extension of the file.

        :return: None
        """
        self.log.debug("on_file_open_geometry()")

        _filter_ = add_case_variants("Geometry Files (*.svg *.dxf);;SVG File .svg (*.svg);;DXF File .dxf (*.dxf);;"
                                     "All Files (*.*)")
        try:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(
                caption=_("Open Geometry"), directory=self.app.get_last_folder(), filter=_filter_)
        except TypeError:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open Geometry"), filter=_filter_)

        if len(filenames) == 0:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext == '.svg':
                self.worker_task.emit({'fcn': self.import_svg, 'params': [filename, "geometry"]})
            elif ext == '.dxf':
                self.worker_task.emit({'fcn': self.import_dxf, 'params': [filename, "geometry"]})
            elif filename != '':
                self.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Unsupported file type"), filename))

    def open_config_file(self, filename, run_from_arg=None):
        """
        Loads a config file from the specified file.

        :param filename:        Name of the file from which to load.
        :param run_from_arg:    if True the FlatConfig file will be open as a command line argument
        :return:                None
        """
        self.log.debug("Opening config file: " + filename)

        if run_from_arg:
            self.splash.showMessage('%s: %ssec\n%s' % (_("Canvas initialization started.\n"
                                                         "Canvas initialization finished in"),
                                                       '%.2f' % self.app.used_time,
                                                       _("Opening FlatCAM Config file.")),
                                    alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                    color=QtGui.QColor("lightgray"))
        # # add the tab if it was closed
        # self.ui.plot_tab_area.addTab(self.ui.text_editor_tab, _("Code Editor"))
        # # first clear previous text in text editor (if any)
        # self.ui.text_editor_tab.code_editor.clear()
        #
        # # Switch plot_area to CNCJob tab
        # self.ui.plot_tab_area.setCurrentWidget(self.ui.text_editor_tab)

        # close the Code editor if already open
        if self.app.toggle_codeeditor:
            self.app.on_toggle_code_editor()

        self.app.on_toggle_code_editor()

        try:
            if filename:
                f = QtCore.QFile(filename)
                if f.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
                    stream = QtCore.QTextStream(f)
                    code_edited = stream.readAll()
                    self.app.text_editor_tab.load_text(code_edited, clear_text=True, move_to_start=True)
                    f.close()
        except IOError:
            self.log.error("Failed to open config file: %s" % filename)
            self.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed."), filename))
            return

    def open_project(self, filename, run_from_arg=False, plot=True, cli=False, from_tcl=False):
        """
        Loads a project from the specified file.

        1) Refuses the request if another project load runs
        2) Selects the project that receives the file. GUI, double-click and second launch use the rules of the
           Project tab (busy check, already open, new project). Tcl and the command line replace the active project.
        3) Loads and parses the file in a worker
        4) Checks the version and the options, clears the project and applies the options
        5) Calls app_obj.new_object() with the object's from_dict() as init method, for each object. An object that
           fails is skipped and the others still load.
        6) Registers the file as recently opened

        project_load_done(success, filename, skipped_names) is emitted exactly once for each request, also when
        the file is missing, the load is refused or declined, the parse or the restore fails.
        success is True when the project was restored and at least one object loaded (or the file has no objects).
        A partial load is a success with the names of the skipped objects in the list. The project has no file name
        then and stays modified, so Save asks for a path and does not overwrite the source file.
        success is False when nothing loaded, the list is empty and the current project is kept (a failure before
        the clearing step) or is already cleared (a failure after it).

        :param filename:        Name of the file from which to load.
        :param run_from_arg:    True if run for arguments (double-click, second launch); then the splash shows a message
        :param plot:            If True plot all objects in the project
        :param cli:             Run from command line
        :param from_tcl:        True if run from Tcl Shell
        :return:                True: the request is queued, project_load_done tells its end
        """

        self.project_load_requested.emit(filename, run_from_arg, plot, cli, from_tcl)
        return True

    def _is_interactive(self, cli, from_tcl):
        """True if the user can answer dialogs: not Tcl, not the command line, not headless."""
        return not (cli or from_tcl) and getattr(self.app, 'headless', False) is not True

    def _end_load(self, load, success, failed=()):
        """The terminal step of a request: the only place that emits project_load_done."""
        if load.done:
            return
        load.done = True
        if self._load is load:
            self._load = None
        # the reason of a failed request is read by the slots of project_load_done (the Tcl command)
        reason = getattr(load, 'reason', '')
        if not success and not reason and failed:
            reason = '; '.join(str(name) for name in failed)
        self.last_load_error = '' if success else reason
        self.project_load_done.emit(bool(success), load.filename, list(failed))

    def missing_sources(self):
        """Objects whose imported source file ('source_path' in the options) is not on the disk: [(obj, path)]."""
        missing = []
        collection = getattr(self.app, 'collection', None)
        for obj in collection.get_list() if collection is not None else []:
            path = getattr(obj, 'obj_options', {}).get('source_path')
            if path and not os.path.isfile(path):
                missing.append((obj, path))
        return missing

    def relink_sources(self, folder):
        """Points each missing source file to the file of the same name in the folder. Returns the number of links."""
        count = 0
        for obj, path in self.missing_sources():
            candidate = os.path.join(folder, os.path.basename(path.replace('\\', '/')))
            if os.path.isfile(candidate):
                obj.obj_options['source_path'] = candidate
                count += 1
        if count:
            self.app.should_we_save = True
            self.inform.emit('[success] %s: %d' % (_("Source files relinked"), count))
        return count

    def check_missing_sources(self, interactive):
        """After a project load: tells the user which source files are missing; an interactive load can relink them."""
        missing = self.missing_sources()
        if not missing:
            return
        lines = ['%s: %s' % (obj.obj_options.get('name', ''), path) for obj, path in missing]
        text = '%s\n%s' % (_("These source files were not found"), '\n'.join(lines))
        self.inform.emit('[WARNING_NOTCL] %s' % text.replace('\n', ' '))
        if not interactive:
            return

        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Icon.Warning, _("Missing Source Files"), text,
                                    parent=self.app.ui)
        locate = box.addButton(_("Locate folder..."), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton(_("Close"), QtWidgets.QMessageBox.ButtonRole.RejectRole)

        def on_click(button):
            if button is not locate:
                return
            folder = QtWidgets.QFileDialog.getExistingDirectory(self.app.ui, _("Locate the folder of the source files"))
            if folder:
                self.relink_sources(folder)

        box.buttonClicked.connect(on_click)
        # not modal: the user keeps working while the notice is open
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.show()

    def _fail(self, reason):
        self._load_reason = reason
        return False

    def _on_project_load_failed(self, reason='', load=None):
        load = load if load is not None else self._load
        filename = load.filename if load is not None else ''
        if load is not None:
            load.reason = reason
        else:
            self.last_load_error = reason
        if reason:
            text = '%s: %s\n%s' % (_("Failed to open project file"), filename, reason)
            self.inform.emit('[ERROR_NOTCL] %s' % text)
            if load is not None and load.interactive:
                QtWidgets.QMessageBox.critical(self.app.ui, _("Open Project"), text)

        projects = getattr(self.app, 'projects', None)
        abandoned = False
        if load is not None and load.created is not None and projects is not None:
            # the tab that the open added is empty: remove it and show the previous project again
            abandoned = projects.abandon_open(load.created, load.previous)
        if not abandoned:
            if projects is not None:
                projects.update_title()
            else:
                self.app.ui.set_ui_title(name=self.app.project_filename)

        if load is not None:
            self._end_load(load, False)
        else:
            self.project_load_done.emit(False, '', [])

    def _open_project(self, filename, run_from_arg, plot, cli, from_tcl):
        project_filename = str(filename)

        self.log.debug("Opening project: " + project_filename)
        if self._load is not None or getattr(getattr(self.app, 'app_obj', None), 'load_batches', None):
            self.inform.emit('[WARNING_NOTCL] %s' % _("A project is loading. Wait until it ends."))
            self.last_load_error = _("A project is loading. Wait until it ends.")
            self.project_load_done.emit(False, project_filename, [])
            return

        interactive = self._is_interactive(cli, from_tcl)
        load = SimpleNamespace(filename=project_filename, interactive=interactive, previous=None, created=None,
                               skipped=[], done=False)
        self._load = load

        # an open from the GUI, a double-click or a second launch adds a project to the Project tab;
        # Tcl and the command line replace the active one
        projects = getattr(self.app, 'projects', None)
        if projects is not None and not (cli or from_tcl):
            load.previous = projects.active
            if not projects.prepare_open(project_filename):
                self._end_load(load, False)
                return
            load.created = projects.last_created

        if interactive:
            self.app.ui.set_ui_title(name=_("Loading Project ... Please Wait ..."))

        if run_from_arg:
            self.splash.showMessage('%s: %ssec\n%s' % (_("Canvas initialization started.\n"
                                                         "Canvas initialization finished in"),
                                                       '%.2f' % getattr(self.app, 'used_time', 0.0),
                                                       _("Opening FlatCAM Project file.")),
                                    alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                    color=QtGui.QColor("lightgray"))

        def parse_worker(prj_filename):
            with self.app.proc_container.new('%s' % _("Parsing...")):
                # a bare file name from Tcl is searched in the Tcl folder
                if from_tcl and not os.path.isfile(prj_filename):
                    name = prj_filename.split('/')[-1].split('\\')[-1]
                    prj_filename = os.path.join(
                        self.app.options.get('global_tcl_path', self.app.defaults.get('global_tcl_path')), name)

                # the check is here, not on the GUI thread: a network path can block
                if not os.path.isfile(prj_filename):
                    raise ProjectLoadError(_("File no longer available."))

                # Open and parse a Project file: Zstandard, LZMA (older app versions) or uncompressed
                try:
                    d = load_project_file(prj_filename, object_hook=dict2obj)
                except Exception as e:
                    self.log.error("Failed to open project file: %s with error: %s" % (prj_filename, str(e)))
                    raise ProjectLoadError(str(e) or e.__class__.__name__)

                if not isinstance(d, dict) or not isinstance(d.get('objs'), list) or \
                        not all(isinstance(obj, dict) for obj in d['objs']) or \
                        not isinstance(d.get('options'), dict):
                    raise ProjectLoadError(_("Invalid project structure"))

                self.app.restore_project.emit(d, prj_filename, run_from_arg, from_tcl, cli, plot)
                return True

        def parse_task(prj_filename):
            handed_off = False
            reason = ''
            try:
                handed_off = parse_worker(prj_filename) is True
            except ProjectLoadError as e:
                reason = str(e)
            except Exception as e:
                self.log.error(traceback.format_exc())
                reason = str(e) or e.__class__.__name__
            finally:
                if not handed_off:
                    self.project_load_failed.emit(reason or _("Unknown error"))

        self.app.worker_task.emit({'fcn': parse_task, 'params': [project_filename]})

    def restore_project_handler(self, proj_dict, filename, run_from_arg, from_tcl, cli, plot):
        reason = ''
        try:
            if self._restore_project_handler(proj_dict, filename, run_from_arg, from_tcl, cli, plot):
                return
            reason = self._load_reason
        except Exception as e:
            self.log.error(traceback.format_exc())
            reason = str(e) or e.__class__.__name__
        self._on_project_load_failed(reason)

    @staticmethod
    def _version_tuple(value):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return ()
        return tuple(int(n) for n in re.findall(r'\d+', str(value)))

    def _apply_units(self):
        """The units of the loaded project are the units of the app: the app value and the status bar follow."""
        units = normalize_units(self.app.options.get('units'))
        if units is None or units == self.app.app_units:
            return
        self.app.app_units = units
        label = getattr(getattr(self.app, 'ui', None), 'units_label', None)
        if label is not None:
            label.setText('[%s]' % ('mm' if units == 'MM' else 'in'))
        self.inform.emit('[WARNING_NOTCL] %s: %s' % (_("The units of the project are now used"), units))

    def _restore_project_handler(self, proj_dict, filename, run_from_arg, from_tcl, cli, plot):
        """
        Checks the file, clears the active project and applies the options of the file. Runs on the GUI thread.

        :return:    True if the restore of the objects is queued. False if the load stops; the reason is in
                    self._load_reason ('' if the user declined).
        """
        interactive = self._is_interactive(cli, from_tcl)
        if getattr(getattr(self.app, 'geo_editor', None), '_source_save_pending', False) is True:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Geometry save is running. Wait until it ends."))
            return self._fail('')

        file_version = self._version_tuple(proj_dict.get('version'))
        app_version = self._version_tuple(getattr(self.app, 'version', None))
        if file_version and app_version and file_version > app_version:
            text = '%s: %s\n%s' % (_("The project was made with a newer app version"), proj_dict.get('version'),
                                   _("It may not load correctly."))
            self.inform.emit('[WARNING_NOTCL] %s' % text.replace('\n', ' '))
            if interactive:
                QtWidgets.QMessageBox.warning(self.app.ui, _("Newer Project"), text)

        # Legacy approval belongs on the GUI thread, before clearing the current project.
        found_older_project = any(
            'cnc_tools' in obj or 'exc_cnc_tools' in obj or 'apertures' in obj for obj in proj_dict['objs'])
        if found_older_project:
            if interactive:
                msgbox = FCMessageBox(parent=self.app.ui)
                title = _("Legacy Project")
                txt = _("The project was made with an older app version.\n"
                        "It may not load correctly.\n\n"
                        "Do you want to continue?")
                msgbox.setWindowTitle(title)
                msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
                msgbox.setText('<b>%s</b>' % title)
                msgbox.setInformativeText(txt)
                msgbox.setIcon(QtWidgets.QMessageBox.Icon.Question)
                bt_ok = msgbox.addButton(_('Ok'), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
                msgbox.addButton(_('Cancel'), QtWidgets.QMessageBox.ButtonRole.RejectRole)
                msgbox.setDefaultButton(bt_ok)
                msgbox.exec()
                if msgbox.clickedButton() != bt_ok:
                    return self._fail('')
            else:
                # no dialog without a user
                self.inform.emit('[WARNING_NOTCL] %s %s' % (_("The project was made with an older app version."),
                                                           _("It may not load correctly.")))

        # The legacy-project modal dialog can process a geometry Save request.
        if getattr(getattr(self.app, 'geo_editor', None), '_source_save_pending', False) is True:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Geometry save is running. Wait until it ends."))
            return self._fail('')

        # The options are made ready before the current project is cleared. The application settings in the file
        # are ignored.
        file_options = clean_project_options(proj_dict['options'])
        if 'units' in proj_dict['options'] and 'units' not in file_options:
            self.inform.emit('[WARNING_NOTCL] %s' % _("The units of the project are not valid. They are ignored."))

        # Clear the current project. Tcl and the command line replace it without questions; the Tcl shell of the
        # running script is kept.
        if self.on_file_new_project(cli=None if interactive else True, reset_tcl=interactive,
                                    keep_scripts=False) == 'fail':
            return self._fail('')

        # The Preferences form shows the options of the project when it is opened (show_preferences_gui).
        self.app.options.load_project(file_options)
        self._apply_units()

        # The file name is set when all the objects are loaded; until then a Save must not overwrite the file.
        projects = getattr(self.app, 'projects', None)
        if projects is not None:
            projects.adopt_filename(filename, keep_file=False)

        self.app.restore_project_objects_sig.emit(proj_dict, filename, cli, plot)
        return True

    def restore_project_objects(self, proj_dict, filename, cli, plot):
        if QtCore.QThread.currentThread() is not self.thread():
            self.project_objects_requested.emit(proj_dict, filename, cli, plot)
            return

        load = self._load
        # the objects of a real request are released one by one while they load
        release = load is not None
        if load is None:
            load = SimpleNamespace(filename=str(filename), interactive=not cli, previous=None, created=None,
                                   skipped=[], done=False)
        batch_interactive = not (cli is True and plot is False)
        objs = proj_dict['objs']
        total = len(objs)

        def finished(batch_failed):
            skipped = load.skipped
            success = total == 0 or len(skipped) < total
            try:
                projects = getattr(self.app, 'projects', None)
                partial = bool(skipped) or batch_failed
                if partial:
                    # the file has not all the objects: a Save must not overwrite it
                    self.app.project_filename = None
                    if projects is not None:
                        projects.active.filename = None
                    self.app.should_we_save = True
                    lines = ['%s: %s' % (name, reason) for name, reason in skipped] or \
                            [_("Some objects could not be added.")]
                    if batch.cancelled:
                        lines.insert(0, _("Loading was cancelled."))
                    text = '%s: %s\n%s' % (_("Project loaded without some objects"), filename, '\n'.join(lines))
                    self.inform.emit('[WARNING_NOTCL] %s' % text)
                    self.app.file_opened.emit("project", filename)
                    if load.interactive and batch_interactive:
                        QtWidgets.QMessageBox.warning(self.app.ui, _("Open Project"), text)
                else:
                    self.inform.emit('[success] %s: %s' % (_("Project loaded from"), filename))
                    self.app.project_filename = filename
                    if projects is not None:
                        projects.active.filename = filename
                    self.app.should_we_save = False
                    self.app.file_opened.emit("project", filename)
                if load.interactive and batch_interactive:
                    if projects is not None:
                        projects.update_title()
                    else:
                        self.app.ui.set_ui_title(name=self.app.project_filename or _("New Project - Not saved"))
                history_of(self.app).clear()
                self.check_missing_sources(load.interactive and batch_interactive)
                self.log.debug(" **************** Finished PROJECT loading... **************** ")
            finally:
                self._end_load(load, success, [name for name, reason in skipped])

        try:
            batch = ObjectLoadBatch(self.app.app_obj, 1, finished, parent=self, interactive=batch_interactive,
                                    total=total or None)
        except Exception as e:
            self.log.error(traceback.format_exc())
            self._on_project_load_failed(str(e) or e.__class__.__name__, load)
            return

        def skip(name, reason):
            load.skipped.append((name, reason))
            batch.batch_event.emit('error', 'Project object creation failed: %s: %s' % (name, reason))

        def make_init(entry, problems):
            def obj_init(new_obj, app_inst):
                try:
                    new_obj.from_dict(entry)
                except Exception as except_error:
                    app_inst.log.error('appIO.open_project() --> ' + str(except_error))
                    problems.append(str(except_error))
                    return 'fail'

                # make the 'obj_options' dict a LoudDict
                new_obj_options = LoudDict()
                try:
                    new_obj_options.update(new_obj.obj_options)
                except AttributeError:
                    new_obj_options.update(new_obj.options)
                except Exception as except_error:
                    app_inst.log.error('appIO.open_project() make a LoudDict--> ' + str(except_error))
                    problems.append(str(except_error))
                    return 'fail'

                # the new dict has no callback; set it like the object __init__ does
                new_obj_options.set_change_callback(new_obj.on_options_change)
                new_obj.obj_options = new_obj_options

                # Scripts and Documents have no tools
                if hasattr(new_obj, 'tools'):
                    # #########################################################################################
                    # for older projects loading try to convert the 'apertures' or 'cnc_tools' or
                    # 'exc_cnc_tools' attributes, if found, to 'tools'
                    # #########################################################################################
                    if 'apertures' in entry:
                        new_obj.tools = entry['apertures']
                    if 'cnc_tools' in entry and entry['cnc_tools']:
                        new_obj.tools = entry['cnc_tools']
                    if 'exc_cnc_tools' in entry and entry['exc_cnc_tools']:
                        new_obj.tools = entry['exc_cnc_tools']
                        # add the used_tools (all of them will be used)
                        new_obj.used_tools = [float(k) for k in new_obj.tools.keys()]
                        # add a missing key, 'tooldia' used for plotting CNCJob objects
                        for td in new_obj.tools:
                            new_obj.tools[td]['tooldia'] = float(td)

                    # try to make the keys in the tools dictionary to be integers
                    # JSON serialization makes them strings
                    try:
                        new_obj.tools = {
                            int(tool): tool_dict for tool, tool_dict in list(new_obj.tools.items())
                        }
                    except ValueError:
                        # for older loaded projects
                        new_obj.tools = {
                            float(tool): tool_dict for tool, tool_dict in list(new_obj.tools.items())
                        }
                    except Exception as other_error_msg:
                        app_inst.log.error('appIO.open_project() keys to int--> ' + str(other_error_msg))
                        problems.append(str(other_error_msg))
                        return 'fail'

                # #############################################################################################
                # for older loaded projects
                # ony older CNCJob objects hold those
                if 'cnc_tools' in entry:
                    new_obj.obj_options['type'] = 'Geometry'
                if 'exc_cnc_tools' in entry:
                    new_obj.obj_options['type'] = 'Excellon'
                # #############################################################################################

                if new_obj.kind == 'cncjob':
                    # some attributes are serialized, so we need to take this into consideration in
                    # CNCJob.set_ui()
                    new_obj.is_loaded_from_project = True
            # new_object() fills only the options that the project entry does not have
            obj_init.provided_options = entry.get('obj_options', entry.get('options')) or {}
            return obj_init

        def worker_task():
            with self.app.proc_container.new('%s' % _("Loading...")):
                # Re-create objects, each one on its own: a bad object does not stop the others
                self.log.debug(" **************** Started PROJECT loading... **************** ")
                for index in range(total):
                    if batch.cancelled:
                        break
                    entry = objs[index]
                    if release:
                        objs[index] = None
                    label = '#%d' % (index + 1)
                    try:
                        kind = entry['kind']
                        try:
                            obj_name = entry['obj_options']['name']
                        except KeyError:
                            # allowance for older projects
                            obj_name = entry['options']['name']
                    except (KeyError, TypeError):
                        skip(label, _("The name or the kind of the object is missing."))
                        continue
                    label = str(obj_name)
                    self.app.log.debug(f"Recreating from opened project an {str(kind).capitalize()} object: {obj_name}")

                    problems = []
                    try:
                        ret = self.app.app_obj.new_object(kind, obj_name, make_init(entry, problems), plot=plot,
                                                          record_history=False)
                    except Exception as e:
                        self.log.error(traceback.format_exc())
                        skip(label, str(e) or e.__class__.__name__)
                        continue
                    if ret == 'fail':
                        skip(label, problems[0] if problems else _("The object could not be created."))
                    entry = None

        def restore_task():
            batch.run(worker_task, [])

        self.app.worker_task.emit({'fcn': restore_task, 'params': []})

    def save_project(self, filename, quit_action=False, silent=False, from_tcl=False):
        """
        Saves the current project to the specified file.

        :param filename:        Name of the file in which to save.
        :type filename:         str
        :param quit_action:     if the project saving will be followed by an app quit; boolean
        :param silent:          if True will not display status messages
        :param from_tcl         True is run from Tcl Shell
        :return:                True if the file is written and verified, else False
        """
        if getattr(getattr(self.app, 'geo_editor', None), '_source_save_pending', False) is True:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Geometry save is running. Wait until it ends."))
            return False
        self.log.debug("save_project() -> Saving Project")
        self.app.save_in_progress = True

        if from_tcl:
            self.log.debug("appIO.save_project() -> Project saved from TCL command.")

        with self.app.proc_container.new(_("Saving Project ...")):
            # Capture the latest changes
            # Current object
            try:
                current_object = self.app.collection.get_active()
                if current_object:
                    current_object.read_form()
            except Exception as e:
                self.log.error("save_project() --> There was no active object. Skipping read_form. %s" % str(e))

            # Only the project keys go into the project file. The application settings stay in the defaults.
            app_options = project_options(self.app.options)
            try:
                objs = [obj.to_dict() for obj in self.app.collection.get_list()]
            except Exception as e:
                self.log.error("Failed to serialize the objects for: %s because: %s" % (str(filename), str(e)))
                self.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                self.app.save_in_progress = False
                return False
            d = {
                "objs":             objs,
                "options":          app_options,
                "version":          self.app.version
            }

            compressed = self.app.options.get(
                "global_save_compressed", self.app.defaults.get("global_save_compressed")) is True
            compression_level = None
            if compressed:
                compression_level = int(self.app.options.get(
                    'global_compression_level', self.app.defaults.get('global_compression_level')))

            # the JSON is written as a stream (compressed with Zstandard if requested) to a temporary file
            # that replaces the project file only after a complete write
            try:
                # the WKT of all the geometries is made before the JSON (see camlib.geometry_wkt_cache())
                json_default = project_json_default(geometry_wkt_cache(d))
                save_project_file(filename, d, default=json_default, compression_level=compression_level)
            except PermissionError:
                self.log.error("Failed to open file for saving: %s", str(filename))
                self.inform.emit('[ERROR_NOTCL] %s' % _("The object is used by another application."))
                self.app.save_in_progress = False
                return False
            except Exception as e:
                self.log.error("Failed to save the project file: %s because: %s" % (str(filename), str(e)))
                self.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                self.app.save_in_progress = False
                return False

            if compressed:
                self.inform.emit('[success] %s: %s' % (_("Project saved to"), str(filename)))
            else:
                # verification of the saved project: open and parse it
                try:
                    saved_d = load_project_file(filename)
                except Exception:
                    saved_d = None
                if not saved_d or 'version' not in saved_d:
                    if silent is False:
                        self.inform.emit('[ERROR_NOTCL] %s: %s %s' %
                                         (_("Failed to parse saved project file"),
                                          str(filename),
                                          _("Retry to save it.")))  # noqa
                    self.app.save_in_progress = False
                    return False

                if silent is False:
                    self.inform.emit('[success] %s: %s' % (_("Project saved to"), str(filename)))

            # if quit:
            # t = threading.Thread(target=lambda: self.check_project_file_size(1, filename=filename))
            # t.start()
            self.app.start_delayed_quit(delay=500, filename=filename, should_quit=quit_action)
            return True

    def save_project_checked(self, filename, quit_action=False, silent=False):
        """
        Run save_project() and return True only after a written and verified file. It never raises: an
        unexpected error is logged, the save state is reset and the result is False.
        """
        try:
            return self.save_project(filename, quit_action=quit_action, silent=silent) is True
        except Exception as err:
            self.log.error("appIO.save_project_checked() --> %s" % str(err))
            self.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to save the project"), str(filename)))
            self.app.save_in_progress = False
            return False

    def save_source_file(self, obj_name, filename):
        """
        Exports a FlatCAM Object to a Gerber/Excellon file.

        :param obj_name: the name of the FlatCAM object for which to save its embedded source file
        :param filename: Path to the Gerber file to save to.
        :return:
        """

        if filename is None:
            last_save = self.app.options.get("global_last_save_folder", self.app.defaults.get("global_last_save_folder"))
            last_folder = self.app.options.get("global_last_folder", self.app.defaults.get("global_last_folder"))
            filename = last_save or last_folder

        self.log.debug("save_source_file()")

        obj = self.app.collection.get_by_name(obj_name)

        if isinstance(obj, ScriptObject):
            return obj.save_file(filename)

        if not obj.source_file:
            msg = _("Save cancelled because source file is empty. Try to export the file.")
            self.inform.emit('[ERROR_NOTCL] %s' % msg)  # noqa
            return 'fail'

        time_string = "{:%A, %d %B %Y at %H:%M}".format(datetime.now())

        try:
            with open(filename, 'w') as file:
                file.write('G04*\n')
                file.write('G04 %s (RE)GENERATED BY FLATCAM v%s - www.flatcam.org - Version Date: %s*\n' %
                           (obj.kind.upper(), str(self.app.version), str(self.app.version_date)))
                file.write('G04 Filename: %s*\n' % str(obj_name))
                file.write('G04 Created on : %s*\n' % time_string)
                file.write(obj.source_file)
        except PermissionError:
            self.inform.emit('[WARNING] %s' %
                             _("Permission denied, saving not possible.\n"
                               "Most likely another app is holding the file open and not accessible."))  # noqa
            return 'fail'

    def on_file_save_defaults(self):
        """
        Callback for menu item File->Save Defaults. Saves application default options
        ``self.options`` to current_defaults.FlatConfig.

        :return: None
        """
        self.app.defaults.update(self.app.options)
        self.app.preferencesUiManager.save_defaults()
