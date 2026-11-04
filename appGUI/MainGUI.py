# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

# ##########################################################
# File Modified (major mod): Marius Adrian Stanciu         #
# Date: 3/10/2019                                          #
# ##########################################################
from PyQt6.QtCore import QSettings

from appGUI.GUIElements import *
from appGUI.TitleBar import TitleBar
from appGUI import shortcuts
from appDatabase import _database_option

from appGUI.preferences.cncjob.CNCJobPreferencesUI import CNCJobPreferencesUI
from appGUI.preferences.excellon.ExcellonPreferencesUI import ExcellonPreferencesUI
from appGUI.preferences.general.GeneralPreferencesUI import GeneralPreferencesUI
from appGUI.preferences.geometry.GeometryPreferencesUI import GeometryPreferencesUI
from appGUI.preferences.gerber.GerberPreferencesUI import GerberPreferencesUI
from appEditors.appGeoEditor import AppGeoEditor, FCShapeTool

import webbrowser

from appGUI.preferences.tools.PluginsPreferencesUI import PluginsPreferencesUI
from appGUI.preferences.tools.Plugins2PreferencesUI import Plugins2PreferencesUI
from appGUI.preferences.tools.PluginsEngravingPreferencesUI import PluginsEngravingPreferencesUI

from appGUI.preferences.utilities.UtilPreferencesUI import UtilPreferencesUI
from appObjects.ObjectCollection import EventSensitiveListView

import subprocess
import os
import platform
import sys
import gettext
import appTranslation as fcTranslate
import builtins
import traceback

import darkdetect

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def cursor_screen():
    """
    :return: The screen under the mouse cursor, or the primary screen when no screen holds the cursor.
    """
    return QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()


def geometry_on_screen(geometry: QtCore.QRect, available: QtCore.QRect) -> QtCore.QRect:
    """
    :param geometry: The saved geometry of the window.
    :param available: The available geometry of the screen where the window opens.
    :return: The saved geometry when its center is on that screen; else the same size, made to fit and centered
    on that screen.
    """
    if available.contains(geometry.center()):
        return QtCore.QRect(geometry)
    width = min(geometry.width(), available.width())
    height = min(geometry.height(), available.height())
    return QtCore.QRect(available.x() + (available.width() - width) // 2,
                        available.y() + (available.height() - height) // 2, width, height)


class MainGUI(QtWidgets.QMainWindow):
    final_save = QtCore.pyqtSignal(name='saveBeforeExit')
    # screenChanged = QtCore.pyqtSignal(QtGui.QScreen, QtGui.QScreen)

    # Mapping of colors used for text on Light theme to
    # similar colors safe for use on Dark theme
    # 'input_color': (light_color, dark_color),
    theme_safe_colors = {
        "blue": "#66B3FF",
        "brown": "#CC9966",
        "darkgreen": "#008015",
        "darkorange": "darkorange",
        "green": "#00CC22",
        "indigo": "#9457EB",
        "magenta": "magenta",
        "orange": "orange",
        "purple": "#B284BE",
        "red": "salmon",
        "teal": "teal",
        "tomato": "tomato",
    }

    def theme_safe_color(self, color):
        """
        Some colors do not work well with light or dark backgrounds making them unreadable in the wrong
        theme. For an approved color value this will return a similar color better suited for the current theme.

        :param color: color to be replaced
        :return: similar color better suited for dark or light theme
        """

        normalized_color = color.lower() if isinstance(color, str) else color
        if normalized_color in ('#0000ff', '#00f'):
            normalized_color = 'blue'

        if normalized_color in self.theme_safe_colors:
            if _database_option(self.app, 'global_theme') in ['default', 'light']:
                return color
            else:
                return self.theme_safe_colors[normalized_color]
        else:
            return color

    # https://www.w3.org/TR/SVG11/types.html#ColorKeywords
    def __init__(self, app):
        super(MainGUI, self).__init__()

        self.app = app
        self.decimals = self.app.decimals

        FCLabel.patching_text_color = self.theme_safe_color
        FCButton.patching_text_color = self.theme_safe_color

        # Divine icon pack by Ipapun @ finicons.com

        # Store the hidden toolbars here for fullScreen action
        self.hidden_toolbars = []
        self.original_window_flags = self.windowFlags()

        # #######################################################################
        # ############ BUILDING THE GUI IS EXECUTED HERE ########################
        # #######################################################################

        # #######################################################################
        # ###################### Menu BUILDING ##################################
        # #######################################################################
        self.menu = QtWidgets.QMenuBar(self)

        self.menu_toggle_nb = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/notebook32.png'), _("Toggle Panel"))
        self.menu_toggle_nb.setToolTip(
            _("Toggle Panel")
        )
        # self.menu_toggle_nb = QtGui.QAction("NB")

        self.menu_toggle_nb.setCheckable(True)
        self.menu.addAction(self.menu_toggle_nb)

        # ########################################################################
        # ########################## File # ######################################
        # ########################################################################
        self.menufile = self.menu.addMenu(_('File'))
        self.menufile.setToolTipsVisible(True)

        # New Project
        self.menufilenewproject = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/file16.png'),
                                                '%s...\t%s' % (_('New Project'), _("Ctrl+N")), self)
        self.menufilenewproject.setToolTip(
            _("Will create a new, blank project")
        )
        self.menufile.addAction(self.menufilenewproject)

        # New Category (Excellon, Geometry)
        self.menufilenew = self.menufile.addMenu(QtGui.QIcon(self.app.resource_location + '/file16.png'), _('New'))
        self.menufilenew.setToolTipsVisible(True)

        self.menufilenewgeo = self.menufilenew.addAction(
            QtGui.QIcon(self.app.resource_location + '/new_file_geo16.png'), '%s\t%s' % (_('Geometry'), _('N')))
        self.menufilenewgeo.setToolTip(
            _("Will create a new, empty Geometry Object.")
        )
        self.menufilenewgrb = self.menufilenew.addAction(
            QtGui.QIcon(self.app.resource_location + '/new_file_grb16.png'), '%s\t%s' % (_('Gerber'), _('B')))
        self.menufilenewgrb.setToolTip(
            _("Will create a new, empty Gerber Object.")
        )
        self.menufilenewexc = self.menufilenew.addAction(
            QtGui.QIcon(self.app.resource_location + '/new_file_exc16.png'), '%s\t%s' % (_('Excellon'), _('L')))
        self.menufilenewexc.setToolTip(
            _("Will create a new, empty Excellon Object.")
        )
        self.menufilenew.addSeparator()

        self.menufilenewdoc = self.menufilenew.addAction(
            QtGui.QIcon(self.app.resource_location + '/notes16_1.png'), '%s\t%s' % (_('Document'), _('D')))
        self.menufilenewdoc.setToolTip(
            _("Will create a new, empty Document Object.")
        )

        self.menufile_open = self.menufile.addMenu(
            QtGui.QIcon(self.app.resource_location + '/folder32.png'), '%s' % _('Open'))
        self.menufile_open.setToolTipsVisible(True)

        # Open Project ...
        self.menufileopenproject = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/folder16.png'), '%s...\t%s' % (_('Open Project'), _('Ctrl+O')),
            self)
        self.menufile_open.addAction(self.menufileopenproject)
        self.menufile_open.addSeparator()

        # Open Gerber ...
        self.menufileopengerber = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/open_gerber32.png'),
                                                '%s...\t%s' % (_('Open Gerber'), _('Ctrl+G')), self)
        self.menufile_open.addAction(self.menufileopengerber)

        # Open Excellon ...
        self.menufileopenexcellon = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/open_excellon32.png'),
                                                  '%s...\t%s' % (_('Open Excellon'), _('Ctrl+E')), self)
        self.menufile_open.addAction(self.menufileopenexcellon)

        # Open G-Code ...
        self.menufileopengcode = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/code.png'), '%s...\t%s' % (_('Open G-Code'), ''), self)
        self.menufile_open.addAction(self.menufileopengcode)

        self.menufile_open.addSeparator()

        # Open Config File...
        self.menufileopenconfig = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/folder16.png'), '%s...\t%s' % (_('Open Config'), ''), self)
        self.menufile_open.addAction(self.menufileopenconfig)

        # Recent
        self.recent_projects = self.menufile.addMenu(
            QtGui.QIcon(self.app.resource_location + '/recent_files.png'), _("Recent projects"))
        self.recent = self.menufile.addMenu(
            QtGui.QIcon(self.app.resource_location + '/recent_files.png'), _("Recent files"))

        # SAVE category
        self.menufile_save = self.menufile.addMenu(QtGui.QIcon(self.app.resource_location + '/save_as.png'), _('Save'))

        # Save Project
        self.menufilesaveproject = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/floppy16.png'), '%s...\t%s' % (_('Save Project'), _('Ctrl+S')),
            self)
        self.menufile_save.addAction(self.menufilesaveproject)

        # Save Project As ...
        self.menufilesaveprojectas = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/floppy16.png'),
                                                   '%s...\t%s' % (_('Save Project As'), _('Ctrl+Shift+S')), self)
        self.menufile_save.addAction(self.menufilesaveprojectas)

        # Save Project Copy ...
        # self.menufilesaveprojectcopy = QtGui.QAction(
        #     QtGui.QIcon(self.app.resource_location + '/floppy16.png'), _('Save Project Copy ...'), self)
        # self.menufile_save.addAction(self.menufilesaveprojectcopy)

        self.menufile_save.addSeparator()

        # Separator
        self.menufile.addSeparator()

        # Scripting
        self.menufile_scripting = self.menufile.addMenu(
            QtGui.QIcon(self.app.resource_location + '/script16.png'), _('Scripting'))
        self.menufile_scripting.setToolTipsVisible(True)

        self.menufilenewscript = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/script_new16.png'),
                                               '%s...\t%s' % (_('New Script'), ''), self)
        self.menufileopenscript = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/open_script32.png'),
                                                '%s...\t%s' % (_('Open Script'), ''), self)
        self.menufileopenscriptexample = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/open_script32.png'),
            '%s...\t%s' % (_('Open Example'), ''), self)
        self.menufilerunscript = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/script16.png'),
            '%s...\t%s' % (_('Run Script'), _('Shift+S')), self)
        self.menufilerunscript.setToolTip(
            _("Will run the opened Tcl Script thus\n"
              "enabling the automation of certain\n"
              "functions of FlatCAM.")
        )
        self.menufile_scripting.addAction(self.menufilenewscript)
        self.menufile_scripting.addAction(self.menufileopenscript)
        self.menufile_scripting.addAction(self.menufileopenscriptexample)
        self.menufile_scripting.addSeparator()
        self.menufile_scripting.addAction(self.menufilerunscript)

        # Separator
        self.menufile.addSeparator()

        # Import ...
        self.menufileimport = self.menufile.addMenu(
            QtGui.QIcon(self.app.resource_location + '/import.png'), _('Import'))
        self.menufileimportsvg = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/svg16.png'),
            '%s...\t%s' % (_('SVG as Geometry Object'), ''), self)
        self.menufileimport.addAction(self.menufileimportsvg)
        self.menufileimportsvg_as_gerber = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/svg16.png'),
            '%s...\t%s' % (_('SVG as Gerber Object'), ''), self)
        self.menufileimport.addAction(self.menufileimportsvg_as_gerber)
        self.menufileimport.addSeparator()

        self.menufileimportdxf = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/dxf16.png'),
            '%s...\t%s' % (_('DXF as Geometry Object'), ''), self)
        self.menufileimport.addAction(self.menufileimportdxf)
        self.menufileimportdxf_as_gerber = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/dxf16.png'),
            '%s...\t%s' % (_('DXF as Gerber Object'), ''), self)
        self.menufileimport.addAction(self.menufileimportdxf_as_gerber)
        self.menufileimport.addSeparator()
        self.menufileimport_hpgl2_as_geo = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/dxf16.png'),
            '%s...\t%s' % (_('HPGL2 as Geometry Object'), ''), self)
        self.menufileimport.addAction(self.menufileimport_hpgl2_as_geo)
        self.menufileimport.addSeparator()

        self.menufileimportpcbdoc = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'),
            '%s...' % _('Altium PCB (.PcbDoc)'), self)
        self.menufileimport.addAction(self.menufileimportpcbdoc)
        self.menufileimportproteus = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'),
            '%s...' % _('Proteus PCB (.pdsprj)'), self)
        self.menufileimport.addAction(self.menufileimportproteus)
        self.menufileimportkicad = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'),
            '%s...' % _('KiCad PCB (.kicad_pcb)'), self)
        self.menufileimport.addAction(self.menufileimportkicad)
        self.menufileimportsprint = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'),
            '%s...' % _('Sprint Layout 6 (.lay6)'), self)
        self.menufileimport.addAction(self.menufileimportsprint)
        self.menufileimporteagle = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'),
            '%s...' % _('Eagle Board (.brd)'), self)
        self.menufileimport.addAction(self.menufileimporteagle)
        self.menufileimportallegro = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'),
            '%s...' % _('OrCAD/Allegro Board (.brd)'), self)
        self.menufileimport.addAction(self.menufileimportallegro)
        self.menufileimportdiptrace = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'),
            '%s...' % _('DipTrace PCB (.dip)'), self)
        self.menufileimport.addAction(self.menufileimportdiptrace)
        self.menufileimport.addSeparator()

        # Export ...
        self.menufileexport = self.menufile.addMenu(
            QtGui.QIcon(self.app.resource_location + '/export.png'), _('Export'))
        self.menufileexport.setToolTipsVisible(True)

        self.menufileexportsvg = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_svg32.png'),
            '%s...\t%s' % (_('Export SVG'), ''), self)
        self.menufileexport.addAction(self.menufileexportsvg)

        self.menufileexportdxf = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_dxf32.png'),
            '%s...\t%s' % (_('Export DXF'), ''), self)
        self.menufileexport.addAction(self.menufileexportdxf)

        self.menufileexport.addSeparator()

        self.menufileexportpng = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_png32.png'),
            '%s...\t%s' % (_('Export PNG'), ''), self)
        self.menufileexportpng.setToolTip(
            _("Will export an image in PNG format,\n"
              "the saved image will contain the visual \n"
              "information currently in FlatCAM Plot Area.")
        )
        self.menufileexport.addAction(self.menufileexportpng)

        self.menufileexport.addSeparator()

        self.menufileexportexcellon = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_excellon32.png'),
            '%s...\t%s' % (_('Export Excellon'), ''), self)
        self.menufileexportexcellon.setToolTip(
            _("Will export an Excellon Object as Excellon file,\n"
              "the coordinates format, the file units and zeros\n"
              "are set in Preferences -> Excellon Export.")
        )
        self.menufileexport.addAction(self.menufileexportexcellon)

        self.menufileexportgerber = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_gerber32.png'),
            '%s...\t%s' % (_('Export Gerber'), ''), self)
        self.menufileexportgerber.setToolTip(
            _("Will export an Gerber Object as Gerber file,\n"
              "the coordinates format, the file units and zeros\n"
              "are set in Preferences -> Gerber Export.")
        )
        self.menufileexport.addAction(self.menufileexportgerber)

        # Separator
        self.menufile.addSeparator()

        self.menufile_backup = self.menufile.addMenu(
            QtGui.QIcon(self.app.resource_location + '/backup24.png'), _('Backup'))

        # Import Preferences
        self.menufileimportpref = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/backup_import24.png'),
            '%s...\t%s' % (_('Import Preferences from file'), ''), self
        )
        self.menufile_backup.addAction(self.menufileimportpref)

        # Export Preferences
        self.menufileexportpref = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/backup_export24.png'),
            '%s...\t%s' % (_('Export Preferences to file'), ''), self)
        self.menufile_backup.addAction(self.menufileexportpref)

        # Separator
        self.menufile_backup.addSeparator()

        # Save Defaults
        self.menufilesavedefaults = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/defaults.png'),
            '%s\t%s' % (_('Save Preferences'), ''), self)
        self.menufile_backup.addAction(self.menufilesavedefaults)

        # Separator
        self.menufile.addSeparator()
        self.menufile_print = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/printer32.png'),
            '%s\t%s' % (_('Print (PDF)'), _('Ctrl+P')))
        self.menufile.addAction(self.menufile_print)

        # Separator
        self.menufile.addSeparator()

        # Quit
        self.menufile_exit = QtGui.QAction(
            QtGui.QIcon(self.app.resource_location + '/power16.png'),
            '%s\t%s' % (_('Exit'), ''), self)
        # QuitRole + the standard Quit shortcut: on macOS they route the application menu's Quit item and Cmd+Q to this
        # action, which requests the guarded window-close path.
        self.menufile_exit.setMenuRole(QtGui.QAction.MenuRole.QuitRole)
        self.menufile_exit.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Quit))
        # exitAction.setStatusTip('Exit application')
        self.menufile.addAction(self.menufile_exit)

        # ########################################################################
        # ########################## Edit # ######################################
        # ########################################################################
        self.menuedit = self.menu.addMenu(_('Edit'))
        self.menuedit_undo = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/undo64.png'),
            '%s	%s' % (_('Undo'), _('Ctrl+Z')))
        self.menuedit_redo = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/redo64.png'),
            '%s	%s' % (_('Redo'), _('Ctrl+Shift+Z')))
        self.menuedit_undo.setEnabled(False)
        self.menuedit_redo.setEnabled(False)
        # the Undo / Redo buttons of the title bar (made once: a toolbar rebuild does not replace them)
        self.undo_btn = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/undo64.png'), _("Undo"), self)
        self.redo_btn = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/redo64.png'), _("Redo"), self)
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        # Separator
        self.menuedit.addSeparator()
        self.menueditedit = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/edit16.png'),
            '%s\t%s' % (_('Edit Object'), _('E')))
        self.menueditok = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/power16.png'),
            '%s\t%s' % (_('Exit Editor'), _('Ctrl+S')))

        # adjust the initial state of the menu entries related to the editor
        self.menueditedit.setDisabled(False)
        self.menueditok.setDisabled(True)
        # 'Exit Editor' starts with 'Exit', so Qt's text heuristic gives it QuitRole on macOS. It is disabled outside
        # the editors and the application menu's Quit item then showed that disabled state. It is not a quit action.
        self.menueditok.setMenuRole(QtGui.QAction.MenuRole.NoRole)

        # ############################ EDIT -> CONVERSION ######################################################
        # Separator
        self.menuedit.addSeparator()
        self.menuedit_convert = self.menuedit.addMenu(
            QtGui.QIcon(self.app.resource_location + '/convert24.png'), _('Conversion'))

        self.menuedit_convert_sg2mg = self.menuedit_convert.addAction(
            QtGui.QIcon(self.app.resource_location + '/convert24.png'),
            '%s\t%s' % (_('Convert Single to MultiGeo'), ''))
        self.menuedit_convert_sg2mg.setToolTip(
            _("Will convert a Geometry object from single_geometry type\n"
              "to a multi_geometry type.")
        )
        self.menuedit_convert_mg2sg = self.menuedit_convert.addAction(
            QtGui.QIcon(self.app.resource_location + '/convert24.png'),
            '%s\t%s' % (_('Convert Multi to SingleGeo'), ''))
        self.menuedit_convert_mg2sg.setToolTip(
            _("Will convert a Geometry object from multi_geometry type\n"
              "to a single_geometry type.")
        )
        # Separator
        self.menuedit_convert.addSeparator()
        self.menueditconvert_any2geo = self.menuedit_convert.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy_geo.png'),
            '%s\t%s' % (_('Convert Any to Geo'), ''))
        self.menueditconvert_any2gerber = self.menuedit_convert.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy_geo.png'),
            '%s\t%s' % (_('Convert Any to Gerber'), ''))
        self.menueditconvert_any2excellon = self.menuedit_convert.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy_geo.png'),
            '%s\t%s' % (_('Convert Any to Excellon'), ''))
        self.menuedit_convert.setToolTipsVisible(True)

        # ############################ EDIT -> JOIN        ######################################################
        self.menuedit_join = self.menuedit.addMenu(
            QtGui.QIcon(self.app.resource_location + '/join16.png'), _('Join Objects'))
        self.menuedit_join2geo = self.menuedit_join.addAction(
            QtGui.QIcon(self.app.resource_location + '/join16.png'),
            '%s\t%s' % (_('Join Geo/Gerber/Exc -> Geo'), ''))
        self.menuedit_join2geo.setToolTip(
            _("Merge a selection of objects, which can be of type:\n"
              "- Gerber\n"
              "- Excellon\n"
              "- Geometry\n"
              "into a new combo Geometry object.")
        )
        self.menuedit_join_exc2exc = self.menuedit_join.addAction(
            QtGui.QIcon(self.app.resource_location + '/join16.png'),
            '%s\t%s' % (_('Join Excellon(s) -> Excellon'), ''))
        self.menuedit_join_exc2exc.setToolTip(
            _("Merge a selection of Excellon objects into a new combo Excellon object.")
        )
        self.menuedit_join_grb2grb = self.menuedit_join.addAction(
            QtGui.QIcon(self.app.resource_location + '/join16.png'),
            '%s\t%s' % (_('Join Gerber(s) -> Gerber'), ''))
        self.menuedit_join_grb2grb.setToolTip(
            _("Merge a selection of Gerber objects into a new combo Gerber object.")
        )
        self.menuedit_join.setToolTipsVisible(True)

        # ############################ EDIT -> COPY        ######################################################
        # Separator
        self.menuedit.addSeparator()
        self.menueditcopyobject = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy.png'),
            '%s\t%s' % (_('Copy'), _('Ctrl+C')))

        # Separator
        self.menuedit.addSeparator()
        self.menueditdelete = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash16.png'),
            '%s\t%s' % (_('Delete'), _('DEL')))

        # Separator
        self.menuedit.addSeparator()
        self.menuedit_numeric_move = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32_bis.png'),
            '%s\t%s' % (_('Num Move'), ''))
        self.menueditorigin = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/origin16.png'),
            '%s\t%s' % (_('Set Origin'), _('O')))
        self.menuedit_move2origin = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/move2origin32.png'),
            '%s\t%s' % (_('Move to Origin'), _('Shift+O')))
        self.menuedit_center_in_origin = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/custom_origin32.png'),
            '%s\t%s' % (_('Custom Origin'), ''))

        self.menueditjump = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/jump_to32.png'),
            '%s\t%s' % (_('Jump to Location'), _('J')))
        self.menueditlocate = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/locate16.png'),
            '%s\t%s' % (_('Locate in Object'), _('Shift+J')))

        # Separator
        self.menuedit.addSeparator()
        self.menueditselectall = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/select_all.png'),
            '%s\t%s' % (_('Select All'), _('Ctrl+A')))

        # Separator
        self.menuedit.addSeparator()
        self.menueditpreferences = self.menuedit.addAction(
            QtGui.QIcon(self.app.resource_location + '/pref.png'),
            '%s\t%s' % (_('Preferences'), _('Shift+P')))

        # #############################################################################################################
        # ########################################### OPTIONS # ######################################################
        # #############################################################################################################

        self.menuoptions = self.menu.addMenu(_('Options'))
        self.menuoptions_transform_rotate = self.menuoptions.addAction(
            QtGui.QIcon(self.app.resource_location + '/rotate.png'),
            '%s\t%s' % (_("Rotate Selection"), _('Shift+(R)')))
        # Separator
        self.menuoptions.addSeparator()

        self.menuoptions_transform_skewx = self.menuoptions.addAction(
            QtGui.QIcon(self.app.resource_location + '/skewX.png'),
            '%s\t%s' % (_("Skew on X axis"), _('Shift+X')))
        self.menuoptions_transform_skewy = self.menuoptions.addAction(
            QtGui.QIcon(self.app.resource_location + '/skewY.png'),
            '%s\t%s' % (_("Skew on Y axis"), _('Shift+Y')))

        # Separator
        self.menuoptions.addSeparator()
        self.menuoptions_transform_flipx = self.menuoptions.addAction(
            QtGui.QIcon(self.app.resource_location + '/flipx.png'),
            '%s\t%s' % (_("Flip on X axis"), _('X')))
        self.menuoptions_transform_flipy = self.menuoptions.addAction(
            QtGui.QIcon(self.app.resource_location + '/flipy.png'),
            '%s\t%s' % (_("Flip on Y axis"), _('Y')))
        # Separator
        self.menuoptions.addSeparator()

        self.menuoptions_view_source = self.menuoptions.addAction(
            QtGui.QIcon(self.app.resource_location + '/source32.png'),
            '%s\t%s' % (_("View source"), _('Alt+S')))
        self.menuoptions_tools_db = self.menuoptions.addAction(
            QtGui.QIcon(self.app.resource_location + '/database32.png'),
            '%s\t%s' % (_("Tools Database"), _('Ctrl+D')))
        # Separator
        self.menuoptions.addSeparator()

        # ########################### Options ->Experimental ##########################################################
        self.menuoptions_experimental = self.menuoptions.addMenu(
            QtGui.QIcon(self.app.resource_location + '/experiment32.png'), _('Experimental'))

        self.menuoptions_experimental_3D_area = self.menuoptions_experimental.addAction(
            QtGui.QIcon(self.app.resource_location + '/3D_area32.png'),
            '%s\t%s' % (_('3D Area'), ''))
        # Separator
        self.menuoptions.addSeparator()

        # #############################################################################################################
        # ################################## View # ###################################################################
        # #############################################################################################################
        self.menuview = self.menu.addMenu(_('View'))
        self.menuview.addAction(QtGui.QIcon(self.app.resource_location + '/restore_layout.png'),
                                _('Restore Layout')).triggered.connect(lambda: self.app.on_layout())
        self.menuview.addSeparator()
        self.menuviewenable = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/replot16.png'),
            '%s\t%s' % (_('Enable all'), _('Alt+1')))
        self.menuviewdisableall = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/clear_plot16.png'),
            '%s\t%s' % (_('Disable all'), _('Alt+2')))
        self.menuviewenableother = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/replot16.png'),
            '%s\t%s' % (_('Enable non-selected'), _('Alt+3')))
        self.menuviewdisableother = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/clear_plot16.png'),
            '%s\t%s' % (_('Disable non-selected'), _('Alt+4')))

        # Separator
        self.menuview.addSeparator()
        self.menuview_zoom_fit = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_fit32.png'),
            '%s\t%s' % (_("Zoom Fit"), _('V')))
        self.menuview_zoom_in = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_in32.png'),
            '%s\t%s' % (_("Zoom In"), _('=')))
        self.menuview_zoom_out = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_out32.png'),
            '%s\t%s' % (_("Zoom Out"), _('-')))
        self.menuview.addSeparator()

        # Replot all
        self.menuview_replot = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/replot32.png'),
            '%s\t%s' % (_("Redraw All"), _('F5')))
        self.menuview.addSeparator()

        self.menuview_toggle_code_editor = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/code_editor32.png'),
            '%s\t%s' % (_('Toggle Code Editor'), _('Shift+E')))
        self.menuview.addSeparator()
        self.menuview_toggle_fscreen = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/fscreen32.png'),
            '%s\t%s' % (_("Toggle FullScreen"), _('Alt+F10')))
        self.menuview_toggle_parea = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/plot32.png'),
            '%s\t%s' % (_("Toggle Plot Area"), _('Ctrl+F10')))
        self.menuview_toggle_notebook = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/notebook32.png'),
            '%s\t%s' % (_("Toggle Project/Properties/Tool"), _('`')))
        self.menuview.addSeparator()
        self.menuview_toggle_grid = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/grid32.png'),
            '%s\t%s' % (_("Toggle Grid Snap"), _('G')))
        self.menuview_toggle_grid_lines = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/grid32.png'),
            '%s\t%s' % (_("Toggle Grid Lines"), _('Shift+G')))
        self.menuview_toggle_axis = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/axis32.png'),
            '%s\t%s' % (_("Toggle Axis"), _('Shift+A')))
        self.menuview_toggle_workspace = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/workspace24.png'),
            '%s\t%s' % (_("Toggle Workspace"), _('Shift+W')))
        self.menuview_toggle_hud = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/hud_32.png'),
            '%s\t%s' % (_("Toggle HUD"), _('Shift+H')))

        self.menuview.addSeparator()
        self.menuview_show_log = self.menuview.addAction(
            QtGui.QIcon(self.app.resource_location + '/log32.png'),
            '%s\t%s' % (_("Error Log"), ''))

        # ########################################################################
        # ########################## Objects # ###################################
        # ########################################################################
        self.menuobjects = self.menu.addMenu(_('Objects'))
        self.menuobjects.addSeparator()
        self.menuobjects_selall = self.menuobjects.addAction(
            QtGui.QIcon(self.app.resource_location + '/select_all.png'),
            '%s\t%s' % (_('Select All'), ''))
        self.menuobjects_unselall = self.menuobjects.addAction(
            QtGui.QIcon(self.app.resource_location + '/deselect_all32.png'),
            '%s\t%s' % (_('Deselect All'), ''))

        # ########################################################################
        # ########################## Plugins # ######################################
        # ########################################################################
        self.menu_plugins = QtWidgets.QMenu(_('Plugins'))
        self.menu_plugins_action = self.menu.addMenu(self.menu_plugins)
        self.menu_plugins_shell = self.menu_plugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/shell16.png'),
            '%s\t%s' % (_('Command Line'), _('S')))

        # ########################################################################
        # ########################## Help # ######################################
        # ########################################################################
        self.menuhelp = self.menu.addMenu(_('Help'))
        self.menuhelp_manual = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/globe16.png'),
            '%s\t%s' % (_('Obsolete Online Help'), _('F1')))
        self.menuhelp_check_updates = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/refresh16.png'),
            '%s\t%s' % (_('Check for Updates'), ''))
        self.menuhelp_revert_update = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/undo16.png'),
            '%s\t%s' % (_('Revert to Previous Version'), ''))
        self.menuhelp_revert_update.setEnabled(False)

        self.menuhelp_bookmarks = self.menuhelp.addMenu(
            QtGui.QIcon(self.app.resource_location + '/bookmarks16.png'), _('Bookmarks'))
        self.menuhelp_bookmarks.addSeparator()
        self.menuhelp_bookmarks_manager = self.menuhelp_bookmarks.addAction(
            QtGui.QIcon(self.app.resource_location + '/bookmarks16.png'),
            '%s\t%s' % (_('Bookmarks Manager'), ''))

        self.menuhelp.addSeparator()
        self.menuhelp_report_bug = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/bug16.png'),
            '%s\t%s' % (_('Report a bug'), ''))
        self.menuhelp.addSeparator()
        self.menuhelp_exc_spec = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/pdf_link16.png'),
            '%s\t%s' % (_('Excellon Specification'), ''))
        self.menuhelp_gerber_spec = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/pdf_link16.png'),
            '%s\t%s' % (_('Gerber Specification'), ''))

        self.menuhelp.addSeparator()

        self.menuhelp_shortcut_list = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/shortcuts24.png'),
            '%s\t%s' % (_('Shortcuts List'), _('F3')))
        self.menuhelp_videohelp = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/youtube32.png'),
            '%s\t%s' % (_('YouTube Channel'), _('F4')))

        self.menuhelp.addSeparator()

        self.menuhelp_donate = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/paypal32.png'),
            '%s\t%s' % (_('Donate'), ''))

        self.menuhelp_readme = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/warning.png'),
            '%s\t%s' % (_("How To"), ''))

        self.menuhelp_about = self.menuhelp.addAction(
            QtGui.QIcon(self.app.resource_location + '/about32.png'),
            '%s\t%s' % (_('About'), ''))

        # ########################################################################
        # ########################## GEOMETRY EDITOR # ###########################
        # ########################################################################
        self.geo_editor_menu = QtWidgets.QMenu('>%s<' % _('Geo Editor'))
        self.menu.addMenu(self.geo_editor_menu)

        self.geo_add_circle_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/circle32.png'),
            '%s\t%s' % (_('Circle'), _('O'))
        )
        self.geo_add_arc_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/arc16.png'),
            '%s\t%s' % (_('Arc'), _('A')))
        self.geo_editor_menu.addSeparator()
        self.geo_add_rectangle_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'),
            '%s\t%s' % (_('Rectangle'), _('R'))
        )
        self.geo_add_polygon_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/polygon32.png'),
            '%s\t%s' % (_('Polygon'), _('N'))
        )
        self.geo_add_path_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/path32.png'),
            '%s\t%s' % (_('Path'), _('P')))
        self.geo_editor_menu.addSeparator()
        self.geo_add_text_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/text32.png'),
            '%s\t%s' % (_('Text'), _('T')))
        self.geo_editor_menu.addSeparator()
        self.geo_union_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/union16.png'),
            '%s\t%s' % (_('Union'), _('U')))
        self.geo_polygonize_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/poligonize32.png'), _('Polygonize'))
        self.geo_intersection_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/intersection16.png'),
            '%s\t%s' % (_('Intersection'), _('E')))
        self.geo_subtract_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract16.png'),
            '%s\t%s' % (_('Subtraction'), _('S'))
        )
        self.geo_subtract_alt_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract16.png'),
            '%s\t%s' % (_('Alt Subtraction'), '')
        )
        self.geo_editor_menu.addSeparator()
        self.geo_cutpath_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/cutpath16.png'),
            '%s\t%s' % (_('Cut Path'), _('X')))
        # self.move_menuitem = self.menu.addAction(
        #   QtGui.QIcon(self.app.resource_location + '/move16.png'), "Move Objects 'm'")
        self.geo_copy_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy16.png'),
            '%s\t%s' % (_("Copy"), _('C')))
        self.geo_import_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'),
            '%s\t%s' % (_("Import"), ''))
        self.geo_delete_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/deleteshape16.png'),
            '%s\t%s' % (_("Delete"), _('DEL'))
        )
        self.geo_editor_menu.addSeparator()
        self.geo_move_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'),
            '%s\t%s' % (_("Move"), _('M')))
        self.geo_buffer_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/buffer32.png'),
            '%s\t%s' % (_("Buffer"), _('B'))
        )
        self.geo_simplification_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/simplify32.png'),
            '%s\t%s' % (_("Simplification"), '')
        )
        self.geo_paint_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/paint32.png'),
            '%s\t%s' % (_("Paint"), _('I'))
        )
        self.geo_transform_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/transform.png'),
            '%s\t%s' % (_("Transform"), _('Alt+R'))
        )
        self.geo_editor_menu.addSeparator()
        self.geo_cornersnap_menuitem = self.geo_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/corner32.png'),
            '%s\t%s' % (_("Toggle Corner Snap"), _('K'))
        )

        # ########################################################################
        # ########################## EXCELLON Editor # ###########################
        # ########################################################################
        self.exc_editor_menu = QtWidgets.QMenu('>%s<' % _('Excellon Editor'))
        self.menu.addMenu(self.exc_editor_menu)

        self.exc_add_array_drill_menuitem = self.exc_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'),
            '%s\t%s' % (_('Drill Array'), _('A')))
        self.exc_add_drill_menuitem = self.exc_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/plus16.png'),
            '%s\t%s' % (_('Drill'), _('D')))
        self.exc_editor_menu.addSeparator()

        self.exc_add_array_slot_menuitem = self.exc_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/slot_array26.png'),
            '%s\t%s' % (_('Slot Array'), _('Q')))
        self.exc_add_slot_menuitem = self.exc_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/slot26.png'),
            '%s\t%s' % (_('Slot'), _('W')))
        self.exc_editor_menu.addSeparator()

        self.exc_resize_drill_menuitem = self.exc_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/resize16.png'),
            '%s\t%s' % (_('Resize Drill'), _('R'))
        )
        self.exc_copy_drill_menuitem = self.exc_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'),
            '%s\t%s' % (_('Copy'), _('C')))
        self.exc_delete_drill_menuitem = self.exc_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/deleteshape32.png'),
            '%s\t%s' % (_('Delete'), _('DEL'))
        )
        self.exc_editor_menu.addSeparator()

        self.exc_move_drill_menuitem = self.exc_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'),
            '%s\t%s' % (_('Move'), _('M')))

        # ########################################################################
        # ########################## GERBER Editor # #############################
        # ########################################################################
        self.grb_editor_menu = QtWidgets.QMenu('>%s<' % _('Gerber Editor'))
        self.menu.addMenu(self.grb_editor_menu)

        self.grb_add_rectangle_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'), _("Rect. Reg"))
        self.grb_add_copper_pour_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/copper_pour.png'), _("Copper Pour"))
        self.grb_add_text_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/text32.png'), _("Text Reg"))
        self.grb_add_arc_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/arc32.png'), _("Arc Follow"))
        self.grb_difference_keep_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract_alt32.png'), _("Diff. Keep"))

        self.grb_add_pad_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/aperture16.png'),
            '%s\t%s' % (_('Pad'), _('P')))
        self.grb_add_pad_array_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/padarray32.png'),
            '%s\t%s' % (_('Pad Array'), _('A')))
        self.grb_add_track_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/track32.png'),
            '%s\t%s' % (_('Track'), _('T')))
        self.grb_add_region_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'),
            '%s\t%s' % (_('Region'), _('N')))
        self.grb_editor_menu.addSeparator()

        self.grb_convert_poly_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/poligonize32.png'),
            '%s\t%s' % (_("Poligonize"), _('Alt+N')))
        self.grb_add_semidisc_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/semidisc32.png'),
            '%s\t%s' % (_("SemiDisc"), _('E')))
        self.grb_add_disc_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/disc32.png'),
            '%s\t%s' % (_("Disc"), _('D')))
        self.grb_add_buffer_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/buffer32.png'),
            '%s\t%s' % (_('Buffer'), _('B')))
        self.grb_simplification_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/simplify32.png'),
            '%s\t%s' % (_("Simplification"), '')
        )
        self.grb_add_scale_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/scale32.png'),
            '%s\t%s' % (_('Scale'), _('S')))
        self.grb_add_markarea_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/markarea32.png'),
            '%s\t%s' % (_('Mark Area'), _('Alt+A')))
        self.grb_add_eraser_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/eraser26.png'),
            '%s\t%s' % (_('Eraser'), _('Ctrl+E')))
        self.grb_transform_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/transform.png'),
            '%s\t%s' % (_("Transform"), _('Alt+R')))
        self.grb_editor_menu.addSeparator()

        res = self.app.resource_location
        self.grb_select_menu = self.grb_editor_menu.addMenu(QtGui.QIcon(res + '/select_all.png'), _("Select"))
        self.grb_invert_selection_menuitem = self.grb_select_menu.addAction(
            QtGui.QIcon(res + '/invert32.png'), _("Invert Selection"))
        self.grb_select_pads_menuitem = self.grb_select_menu.addAction(
            QtGui.QIcon(res + '/aperture32.png'), _("Pads Only"))
        self.grb_select_tracks_menuitem = self.grb_select_menu.addAction(
            QtGui.QIcon(res + '/track32.png'), _("Tracks Only"))
        self.grb_select_regions_menuitem = self.grb_select_menu.addAction(
            QtGui.QIcon(res + '/polygon32.png'), _("Regions Only"))
        self.grb_select_chain_menuitem = self.grb_select_menu.addAction(
            QtGui.QIcon(res + '/link32.png'), _("Select Chain"))
        self.grb_step_repeat_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(res + '/padarray32.png'), _("Step and Repeat"))
        self.grb_transcode_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(res + '/replace32.png'), _("Transcode"))
        self.grb_snap_grid_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(res + '/grid32.png'), _("Snap to Grid"))

        self.grb_track_edit_menu = self.grb_editor_menu.addMenu(QtGui.QIcon(res + '/track32.png'), _("Track Edit"))
        self.grb_clip_menuitem = self.grb_track_edit_menu.addAction(
            QtGui.QIcon(res + '/cut32.png'), _("Clip"))
        self.grb_clip_pad_menuitem = self.grb_track_edit_menu.addAction(
            QtGui.QIcon(res + '/aperture32.png'), _("Clip and Replace with Pad"))
        self.grb_stretch_menuitem = self.grb_track_edit_menu.addAction(
            QtGui.QIcon(res + '/move32.png'), _("Stretch"))
        self.grb_elbow_menuitem = self.grb_track_edit_menu.addAction(
            QtGui.QIcon(res + '/track32.png'), _("Insert Elbow"))
        self.grb_elbow90_menuitem = self.grb_track_edit_menu.addAction(
            QtGui.QIcon(res + '/track32.png'), _("Insert 90\u00b0 Elbow"))

        self.grb_aperture_menu = self.grb_editor_menu.addMenu(QtGui.QIcon(res + '/aperture32.png'), _("Apertures"))
        self.grb_delete_unused_menuitem = self.grb_aperture_menu.addAction(
            QtGui.QIcon(res + '/deleteshape32.png'), _("Delete Unused"))
        self.grb_renumber_menuitem = self.grb_aperture_menu.addAction(
            QtGui.QIcon(res + '/desc_sort32.png'), _("Renumber"))
        self.grb_merge_duplicates_menuitem = self.grb_aperture_menu.addAction(
            QtGui.QIcon(res + '/join32.png'), _("Merge Duplicates"))
        self.grb_round_sizes_menuitem = self.grb_aperture_menu.addAction(
            QtGui.QIcon(res + '/scale32.png'), _("Round Sizes"))
        self.grb_select_same_type_menuitem = self.grb_aperture_menu.addAction(
            QtGui.QIcon(res + '/select_all.png'), _("Select Same Type"))

        self.grb_auto_menu = self.grb_editor_menu.addMenu(QtGui.QIcon(res + '/replace32.png'), _("Automatic"))
        self.grb_pads_to_flashes_menuitem = self.grb_auto_menu.addAction(
            QtGui.QIcon(res + '/aperture32.png'), _("Drawn Pads to Flashes"))
        self.grb_teardrops_menuitem = self.grb_auto_menu.addAction(
            QtGui.QIcon(res + '/semidisc32.png'), _("Teardrops"))
        self.grb_snowman_menuitem = self.grb_auto_menu.addAction(
            QtGui.QIcon(res + '/disc32.png'), _("Snowman Fillets"))
        self.grb_editor_menu.addSeparator()

        self.grb_copy_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'),
            '%s\t%s' % (_('Copy'), _('C')))
        self.grb_delete_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/deleteshape32.png'),
            '%s\t%s' % (_('Delete'), _('DEL')))
        self.grb_editor_menu.addSeparator()

        self.grb_move_menuitem = self.grb_editor_menu.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'),
            '%s\t%s' % (_('Move'), _('M')))

        self.grb_editor_menu.menuAction().setVisible(False)
        self.grb_editor_menu.setDisabled(True)

        self.geo_editor_menu.menuAction().setVisible(False)
        self.geo_editor_menu.setDisabled(True)

        self.exc_editor_menu.menuAction().setVisible(False)
        self.exc_editor_menu.setDisabled(True)

        # ########################################################################
        # ########################## Project Tab Context Menu # ##################
        # ########################################################################
        self.menuproject = QtWidgets.QMenu()

        self.menuprojectenable = self.menuproject.addAction(
            QtGui.QIcon(self.app.resource_location + '/replot32.png'), _('Enable Plot'))
        self.menuprojectdisable = self.menuproject.addAction(
            QtGui.QIcon(self.app.resource_location + '/clear_plot32.png'), _('Disable Plot'))
        self.menuproject.addSeparator()

        self.menuprojectcolor = self.menuproject.addMenu(
            QtGui.QIcon(self.app.resource_location + '/set_color32.png'), _('Set Color'))

        self.menuproject_red = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/red32.png'), _('Red'))

        self.menuproject_blue = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/blue32.png'), _('Blue'))

        self.menuproject_yellow = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/yellow32.png'), _('Yellow'))

        self.menuproject_green = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/green32.png'), _('Green'))

        self.menuproject_purple = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/violet32.png'), _('Purple'))

        self.menuproject_brown = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/brown32.png'), _('Brown'))

        self.menuproject_brown = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/indigo32.png'), _('Indigo'))

        self.menuproject_brown = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/white32.png'), _('White'))

        self.menuproject_brown = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/black32.png'), _('Black'))

        self.menuprojectcolor.addSeparator()

        self.menuproject_custom = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/set_color32.png'), _('Custom'))

        self.menuprojectcolor.addSeparator()

        self.menuproject_custom = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/set_color32.png'), _('Opacity'))

        self.menuproject_custom = self.menuprojectcolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/set_color32.png'), _('Default'))

        self.menuproject.addSeparator()

        self.menuprojectviewsource = self.menuproject.addAction(
            QtGui.QIcon(self.app.resource_location + '/source32.png'), _('View Source'))

        self.menuprojectedit = self.menuproject.addAction(
            QtGui.QIcon(self.app.resource_location + '/edit_ok32.png'), _('Edit'))
        self.menuprojectcopy = self.menuproject.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'), _('Copy'))
        self.menuprojectdelete = self.menuproject.addAction(
            QtGui.QIcon(self.app.resource_location + '/delete32.png'), _('Delete'))
        self.menuprojectsave = self.menuproject.addAction(
            QtGui.QIcon(self.app.resource_location + '/save_as.png'), _('Save'))
        self.menuprojectexport = self.menuproject.addMenu(
            QtGui.QIcon(self.app.resource_location + '/export.png'), _('Export'))

        self.menuprojectexport_dxf = self.menuprojectexport.addAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_dxf32.png'),
            _('DXF')
        )
        self.menuprojectexport_svg = self.menuprojectexport.addAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_svg32.png'),
            _('SVG')
        )
        self.menuprojectexport_png = self.menuprojectexport.addAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_png32.png'),
            _('PNG')
        )
        self.menuprojectexport_pdf = self.menuprojectexport.addAction(
            QtGui.QIcon(self.app.resource_location + '/project_export_pdf32.png'),
            _('PDF')
        )

        self.menuprojectjoin = self.menuproject.addMenu(
            QtGui.QIcon(self.app.resource_location + '/join16.png'), _('Join'))
        self.menuprojectjoin_gerber = self.menuprojectjoin.addAction(
            QtGui.QIcon(self.app.resource_location + '/gerber16_project.png'), _('As Gerber'))
        self.menuprojectjoin_excellon = self.menuprojectjoin.addAction(
            QtGui.QIcon(self.app.resource_location + '/excellon16_project.png'), _('As Excellon'))
        self.menuprojectjoin_geometry = self.menuprojectjoin.addAction(
            QtGui.QIcon(self.app.resource_location + '/geometry16_project.png'), _('As Geometry'))
        self.menuproject.addSeparator()

        self.menuprojectproperties = self.menuproject.addAction(
            QtGui.QIcon(self.app.resource_location + '/properties32.png'), _('Properties'))

        # ########################################################################
        # ####################### Central Widget -> Splitter # ##################
        # ########################################################################

        # IMPORTANT #
        # The order: SPLITTER -> NOTEBOOK -> SNAP TOOLBAR is important and without it the GUI will not be initialized as
        # desired.
        self.splitter = QtWidgets.QSplitter()
        self.setCentralWidget(self.splitter)

        # self.notebook = QtWidgets.QTabWidget()
        self.notebook = FCDetachableTab2(protect=True, protect_by_name=[_("Project"), _("Properties")], parent=self)
        # self.notebook.setTabsClosable(False)
        self.notebook.useOldIndex(True)
        self.notebook.auto_remove_closed_tab = False

        self.splitter.addWidget(self.notebook)

        self.splitter_left = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.splitter_left)
        self.splitter_left.addWidget(self.notebook)
        self.splitter_left.setHandleWidth(0)

        # ########################################################################
        # ########################## ToolBAR # ###################################
        # ########################################################################

        # ## TOOLBAR INSTALLATION ###
        self.setIconSize(self.iconSize() * 0.8)
        toolbar_style = "QToolBar{spacing:2px;padding:1px;border:0px;} QToolBar > QToolButton{padding:2px;}"
        self.toolbarfile = QtWidgets.QToolBar(_('File Toolbar'))
        self.toolbarfile.setObjectName('File_TB')
        self.toolbarfile.setStyleSheet(toolbar_style)
        self.addToolBar(self.toolbarfile)
        # off by default; a saved GUI state can show it again
        self.toolbarfile.setVisible(False)

        self.toolbaredit = QtWidgets.QToolBar(_('Edit Toolbar'))
        self.toolbaredit.setObjectName('Edit_TB')
        self.toolbaredit.setStyleSheet(toolbar_style)
        self.addToolBar(self.toolbaredit)

        self.toolbarview = QtWidgets.QToolBar(_('View Toolbar'))
        self.toolbarview.setObjectName('View_TB')
        self.toolbarview.setStyleSheet(toolbar_style)
        self.addToolBar(self.toolbarview)

        self.toolbarshell = QtWidgets.QToolBar(_('Shell Toolbar'))
        self.toolbarshell.setObjectName('Shell_TB')
        self.toolbarshell.setStyleSheet(toolbar_style)
        self.addToolBar(self.toolbarshell)

        self.toolbarplugins = QtWidgets.QToolBar(_('Plugin Toolbar'))
        self.toolbarplugins.setObjectName('Plugins_TB')
        self.toolbarplugins.setStyleSheet(toolbar_style)
        self.addToolBar(self.toolbarplugins)

        # Editor Toolbars
        if _database_option(self.app, 'global_theme') == 'dark':
            checked_edit_bg = '#1F4E79'  # deep blue for dark theme
        else:
            checked_edit_bg = '#4169E1'  # royal blue for light/default

        self.exc_edit_toolbar = QtWidgets.QToolBar(_('Excellon Editor Toolbar'))
        self.exc_edit_toolbar.setObjectName('ExcEditor_TB')
        self.exc_edit_toolbar.setStyleSheet(
            toolbar_style + """
                    QToolBar > QToolButton:checked {
                        background-color: %s;
                    }
                    """ % checked_edit_bg)
        self.addToolBar(self.exc_edit_toolbar)

        self.addToolBarBreak()

        self.geo_edit_toolbar = QtWidgets.QToolBar(_('Geometry Editor Toolbar'))
        self.geo_edit_toolbar.setObjectName('GeoEditor_TB')
        self.geo_edit_toolbar.setStyleSheet(
            toolbar_style + """
                    QToolBar > QToolButton:checked {
                        background-color: %s;
                    }
                    """ % checked_edit_bg)
        self.addToolBar(self.geo_edit_toolbar)

        self.grb_edit_toolbar = QtWidgets.QToolBar(_('Gerber Editor Toolbar'))
        self.grb_edit_toolbar.setObjectName('GrbEditor_TB')
        self.grb_edit_toolbar.setStyleSheet(
            toolbar_style + """
                    QToolBar > QToolButton:checked {
                        background-color: %s;
                    }
                    """ % checked_edit_bg)
        self.addToolBar(self.grb_edit_toolbar)

        # ### INFOBAR TOOLBARS ###################################################
        self.delta_coords_toolbar = CoordsToolbar(_('Delta Coordinates Toolbar'))
        self.delta_coords_toolbar.setObjectName('Delta_Coords_TB')
        self.delta_coords_toolbar.setStyleSheet("QToolBar{spacing:0px;}")

        self.coords_toolbar = CoordsToolbar(_('Coordinates Toolbar'))
        self.coords_toolbar.setObjectName('Coords_TB')
        self.coords_toolbar.setStyleSheet("QToolBar{spacing:0px;}")

        self.grid_toolbar = QtWidgets.QToolBar(_('Grid Toolbar'))
        self.grid_toolbar.setObjectName('Snap_TB')
        self.grid_toolbar.setStyleSheet(
            """
            QToolBar { padding: 0; }
            QToolBar QToolButton { padding: -2; margin: -2; }
            """
        )
        self.grid_toolbar.setStyleSheet("QToolBar{spacing:0px;}")

        self.status_toolbar = QtWidgets.QToolBar(_('Status Toolbar'))
        self.status_toolbar.setStyleSheet(
            """
            QToolBar { padding: 0; }
            QToolBar QToolButton { padding: -2; margin: -2; }
            """
        )
        self.status_toolbar.setStyleSheet("QToolBar{spacing:0px;}")

        # ########################################################################
        # ########################## File Toolbar# ###############################
        # ########################################################################
        self.file_open_gerber_btn = self.toolbarfile.addAction(
            QtGui.QIcon(self.app.resource_location + '/open_gerber32.png'), _("Gerber"))
        self.file_open_gerber_btn.setToolTip(_("Open a Gerber file."))
        self.file_open_excellon_btn = self.toolbarfile.addAction(
            QtGui.QIcon(self.app.resource_location + '/open_excellon32.png'), _("Excellon"))
        self.file_open_excellon_btn.setToolTip(_("Open a Excellon file."))
        # the project commands are in the File menu and in the menu of the project combobox

        # ########################################################################
        # ########################## Edit Toolbar# ###############################
        # ########################################################################
        self.editor_start_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/edit_file32.png'), _("Editor"))

        self.editor_exit_btn = QtWidgets.QToolButton()
        self.editor_exit_btn.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.editor_exit_btn.setText(_("Exit Edit"))
        # expand vertically to fill the toolbar height
        self.editor_exit_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding)
        # https://www.w3.org/TR/SVG11/types.html#ColorKeywords
        self.editor_exit_btn.setStyleSheet("""
                                          QToolButton
                                          {
                                              background-color: %s;
                                              border: none;
                                              border-radius: 4px;
                                              padding: 2px;
                                          }
                                          """ % checked_edit_bg)
        self.editor_exit_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/power16.png'))
        self.editor_exit_btn.setToolTip(_("Exit from Editor."))
        # in order to hide it we hide the returned action
        self.editor_exit_btn_ret_action = self.toolbaredit.addWidget(self.editor_exit_btn)

        self.toolbaredit.addSeparator()
        self.copy_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy_file32.png'), _("Copy"))
        self.copy_btn.setToolTip(_("Copy a selection of objects."))
        self.delete_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash32.png'), _("Delete"))
        self.delete_btn.setToolTip(_("Delete a selection of objects."))
        self.toolbaredit.addSeparator()
        self.distance_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/distance32.png'), _("Distance"))
        self.distance_btn.setToolTip(_("Measure a distance between two or more points."))
        # self.distance_min_btn = self.toolbaredit.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/distance_min32.png'), _("Min Distance"))
        # self.distance_min_btn.setToolTip(_("Measure the minimum distance between two objects."))
        self.origin_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/origin32.png'), _('Set Origin'))
        self.origin_btn.setToolTip(_("Set an origin to a custom location."))
        # self.move2origin_btn = self.toolbaredit.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/move2origin32.png'), _('To Orig.'))
        # self.move2origin_btn.setToolTip(_("Move selected objects to the origin."))
        # self.center_in_origin_btn = self.toolbaredit.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/custom_origin32.png'), _('C Origin'))
        # self.center_in_origin_btn.setToolTip(_("Move the selected objects to custom positions."))

        self.jmp_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/jump_to32.png'), _('Jump to'))
        self.jmp_btn.setToolTip(_("Move the mouse cursor to a defined position and center into view."))
        self.locate_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/locate32.png'), _('Locate'))
        self.locate_btn.setToolTip(_("Locate a predefined position on a selected object."))

        # ########################################################################
        # ########################## View Toolbar# ###############################
        # ########################################################################
        self.replot_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/replot32.png'), _("Replot"))
        self.clear_plot_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/clear_plot32.png'), _("Clear Plot"))
        self.zoom_in_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_in32.png'), _("Zoom In"))
        self.zoom_out_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_out32.png'), _("Zoom Out"))
        self.zoom_fit_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_fit32.png'), _("Zoom Fit"))

        # self.toolbarview.setVisible(False)

        # ########################################################################
        # ########################## Shell Toolbar# ##############################
        # ########################################################################
        self.shell_btn = self.toolbarshell.addAction(
            QtGui.QIcon(self.app.resource_location + '/shell32.png'), _("Command Line"))
        self.new_script_btn = self.toolbarshell.addAction(
            QtGui.QIcon(self.app.resource_location + '/script_new24.png'), '%s ...' % _('New Script'))
        self.open_script_btn = self.toolbarshell.addAction(
            QtGui.QIcon(self.app.resource_location + '/open_script32.png'), '%s ...' % _('Open Script'))
        self.run_script_btn = self.toolbarshell.addAction(
            QtGui.QIcon(self.app.resource_location + '/script16.png'), '%s ...' % _('Run Script'))

        # ########################################################################
        # ########################## Tools Toolbar# ##############################
        # ########################################################################
        self.drill_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/drill32.png'), _("Drilling"))
        self.mill_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/milling_tool32.png'), _("Milling"))

        self.toolbarplugins.addSeparator()

        self.isolation_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/iso32.png'), _("Isolation"))
        self.ncc_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/ncc32.png'), _("NCC"))
        self.paint_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/paint32.png'), _("Paint"))
        self.photocarve_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/photo_carving64.png'), _("Carve"))
        self.photocarve_btn.setToolTip(_("Carve a loaded picture into a material."))

        self.toolbarplugins.addSeparator()

        self.cutout_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/cut32.png'), _("Cutout"))
        self.panelize_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/panelize32.png'), _("Panel"))
        self.film_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/film32.png'), _("Film"))
        self.dblsided_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/doubleside32.png'), _("2-Sided"))

        self.toolbarplugins.addSeparator()

        self.align_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/align32.png'), _("Align"))
        # self.sub_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/sub32.png'), _("Subtract Tool"))

        # self.extract_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/extract32.png'), _("Extract"))
        self.copperfill_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/copperfill32.png'), _("Thieving"))
        self.markers_tool_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/corners_32.png'), _("Markers"))
        self.punch_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/punch32.png'), _("Punch"))
        self.calculators_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/calculator32.png'), _("Calculators"))

        self.toolbarplugins.addSeparator()

        # self.solder_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/solderpastebis32.png'), _("SolderPaste"))
        # self.sub_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/sub32.png'), _("Subtract"))
        # self.rules_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/rules32.png'), _("Rules"))
        # self.optimal_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/open_excellon32.png'), _("Optimal"))
        # self.transform_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/transform.png'), _("Transform"))
        # self.qrcode_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/qrcode32.png'), _("QRCode"))
        # self.align_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/align32.png'), _("Align Objects"))
        # self.fiducials_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/fiducials_32.png'), _("Fiducials"))
        # self.cal_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/calibrate_32.png'), _("Calibration"))

        # self.invert_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/invert32.png'), _("Invert Gerber"))
        # self.etch_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/etch_32.png'), _("Etch Compensation"))

        # ########################################################################
        # ########################## Excellon Editor Toolbar# ####################
        # ########################################################################
        self.select_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/pointer32.png'), _("Select"))
        self.add_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/plus16.png'), _('Add Drill'))
        self.add_drill_array_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/addarray16.png'), _('Add Drill Array'))
        self.add_slot_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/slot26.png'), _('Add Slot'))
        self.add_slot_array_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/slot_array26.png'), _('Add Slot Array'))
        self.resize_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/resize16.png'), _('Resize Drill'))
        self.exc_edit_toolbar.addSeparator()

        self.copy_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'), _('Copy'))
        self.delete_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash32.png'), _("Delete"))

        self.exc_edit_toolbar.addSeparator()
        self.move_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'), _("Move"))

        # ########################################################################
        # ########################## Geometry Editor Toolbar# ####################
        # ########################################################################
        self.geo_select_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/pointer32.png'), _("Select"))
        self.geo_add_circle_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/circle32.png'), _('Circle'))
        self.geo_add_arc_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/arc32.png'), _('Arc'))
        self.geo_add_rectangle_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'), _('Rectangle'))

        self.geo_edit_toolbar.addSeparator()
        self.geo_add_path_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/path32.png'), _('Path'))
        self.geo_add_polygon_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/polygon32.png'), _('Polygon'))
        self.geo_edit_toolbar.addSeparator()
        self.geo_add_text_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/text32.png'), _('Text'))
        self.geo_add_simplification_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/simplify32.png'), _('Simplify'))
        self.geo_add_buffer_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/buffer32.png'), _('Buffer'))
        self.geo_add_paint_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/paint32.png'), _('Paint'))
        self.geo_eraser_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/eraser26.png'), _('Eraser'))

        self.geo_edit_toolbar.addSeparator()
        self.geo_union_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/union32.png'), _('Union'))
        self.geo_join_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/join32.svg'), _('Join'))
        self.geo_join_btn.setToolTip(_('Join selected lines into polylines. Uses the Join tolerance in Properties.'))
        self.geo_polygonize_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/poligonize32.png'), _('Polygonize'))
        self.geo_polygonize_btn.setToolTip(
            _('Create polygons from selected closed paths or enclosing segments.\n'
              'Unused open segments are kept. Endpoints must meet exactly.'))
        self.geo_explode_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/explode32.png'), _('Explode'))

        self.geo_intersection_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/intersection32.png'), _('Intersection'))
        self.geo_subtract_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract32.png'), _('Subtraction'))
        self.geo_subtract_btn.setToolTip(
            _('Polygon Subtraction. First selected is the target.\n'
              'The rest of the selected is subtracted from the first.\n'
              'First selected is replaced by the result.'))
        self.geo_alt_subtract_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract_alt32.png'), _('Alt Subtraction'))
        self.geo_alt_subtract_btn.setToolTip(
            _('Alt Subtraction. First selected is the target.\n'
              'The rest of the selected is subtracted from the first.\n'
              'First selected is kept besides the result.'))

        self.geo_edit_toolbar.addSeparator()
        self.geo_cutpath_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/cutpath32.png'), _('Cut Path'))
        self.geo_copy_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'), _("Copy"))
        self.geo_import_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'), _("Import"))

        self.geo_delete_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash32.png'), _("Delete"))
        self.geo_transform_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/transform.png'), _("Transform"))
        self.geo_edit_toolbar.addSeparator()
        self.geo_move_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'), _("Move"))

        # ########################################################################
        # ########################## Gerber Editor Toolbar# ######################
        # ########################################################################
        self.grb_select_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/pointer32.png'), _("Select"))
        self.grb_add_pad_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/aperture32.png'), _("Pad"))
        self.add_pad_ar_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/padarray32.png'), _('Pad Array'))
        self.grb_add_track_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/track32.png'), _("Track"))
        self.grb_join_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/join32.svg'), _('Join'))
        self.grb_join_btn.setToolTip(_('Join selected connected track sections into one track.'))
        self.grb_add_region_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/polygon32.png'), _("Region"))
        self.grb_convert_poly_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/poligonize32.png'), _("Poligonize"))

        self.grb_add_semidisc_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/semidisc32.png'), _("SemiDisc"))
        self.grb_add_disc_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/disc32.png'), _("Disc"))
        self.grb_add_rectangle_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'), _("Rect. Reg"))
        self.grb_add_rectangle_btn.setToolTip(_("Rectangular Region"))

        self.grb_add_copper_pour_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/copper_pour.png'), _("Copper Pour"))

        self.grb_add_text_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/text32.png'), _("Text Reg"))
        self.grb_add_text_btn.setToolTip(_("Text Region"))

        self.grb_add_arc_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/arc32.png'), _("Arc Follow"))
        self.grb_add_arc_btn.setToolTip(_("Arc: Circular Follow"))

        self.grb_difference_keep_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract_alt32.png'), _("Diff. Keep"))
        self.grb_difference_keep_btn.setToolTip(_("Difference: Keep Cutters"))
        self.grb_edit_toolbar.addSeparator()

        self.aperture_buffer_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/buffer32.png'), _('Buffer'))
        self.aperture_simplify_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/simplify32.png'), _('Simplification'))
        self.aperture_scale_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/scale32.png'), _('Scale'))
        self.aperture_markarea_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/markarea32.png'), _('Mark Area'))
        self.grb_import_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'), _('Import'))

        self.aperture_eraser_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/eraser26.png'), _('Eraser'))

        self.grb_edit_toolbar.addSeparator()
        self.aperture_copy_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'), _("Copy"))
        self.aperture_delete_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash32.png'), _("Delete"))
        self.grb_transform_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/transform.png'), _("Transform"))
        self.grb_edit_toolbar.addSeparator()
        self.aperture_move_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'), _("Move"))

        # ########################################################################
        # ########################## GRID Toolbar# ###############################
        # ########################################################################

        # Snap GRID toolbar is always active to facilitate usage of measurements done on GRID
        grid_icon = QtGui.QIcon(self.app.resource_location + '/grid18.png')
        grid_icon.addFile(self.app.resource_location + '/grid18_status.png', QtCore.QSize(),
                          QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.On)
        self.grid_snap_btn = self.grid_toolbar.addAction(grid_icon, _('Snap to grid'))
        self.grid_gap_x_entry = FCEntry2()
        self.grid_gap_x_entry.setMaximumWidth(70)
        self.grid_gap_x_entry.setToolTip(_("Grid X snapping distance"))
        self.grid_toolbar.addWidget(self.grid_gap_x_entry)

        self.grid_toolbar.addWidget(FCLabel(" "))
        self.grid_gap_link_cb = FCCheckBox()
        self.grid_gap_link_cb.setToolTip(_("When active, value on Grid_X\n"
                                           "is copied to the Grid_Y value."))
        self.grid_toolbar.addWidget(self.grid_gap_link_cb)
        self.grid_toolbar.addWidget(FCLabel(" "))

        self.grid_gap_y_entry = FCEntry2()
        self.grid_gap_y_entry.setMaximumWidth(70)
        self.grid_gap_y_entry.setToolTip(_("Grid Y snapping distance"))

        self.grid_gap_y_entry.setVisible(False)

        self.gridy_entry_action = self.grid_toolbar.addWidget(self.grid_gap_y_entry)
        self.grid_toolbar.addWidget(FCLabel(" "))

        # self.ois_grid = OptionalInputSection(self.grid_gap_link_cb, [self.grid_gap_y_entry], logic=False)
        self.grid_gap_link_cb.clicked.connect(
            lambda x: self.gridy_entry_action.setVisible(False) if x else self.gridy_entry_action.setVisible(True))
        self.corner_snap_btn = self.grid_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/corner32.png'), _('Snap to corner'))

        self.snap_max_dist_entry = FCEntry()
        self.snap_max_dist_entry.setMaximumWidth(70)
        self.snap_max_dist_entry.setToolTip(_("Max. magnet distance"))
        self.snap_magnet = self.grid_toolbar.addWidget(self.snap_max_dist_entry)

        self.corner_snap_btn.setVisible(False)
        self.snap_magnet.setVisible(False)

        # ########################################################################
        # ########################## Status Toolbar ##############################
        # ########################################################################
        self.axis_status_label = FCLabel()
        self.axis_status_label.setToolTip(_("Toggle the display of axis on canvas"))
        self.axis_status_label.setPixmap(QtGui.QPixmap(self.app.resource_location + '/axis18.png'))
        self.status_toolbar.addWidget(self.axis_status_label)
        self.status_toolbar.addWidget(FCLabel(" "))

        self.pref_status_label = FCLabel()
        self.pref_status_label.setToolTip(_("Preferences"))
        self.pref_status_label.setPixmap(QtGui.QPixmap(self.app.resource_location + '/settings18.png'))
        self.status_toolbar.addWidget(self.pref_status_label)
        self.status_toolbar.addWidget(FCLabel(" "))

        self.hud_label = FCLabel()
        self.hud_label.setToolTip(_("HUD (Heads up display)"))
        self.hud_label.setPixmap(QtGui.QPixmap(self.app.resource_location + '/hud18.png'))
        self.status_toolbar.addWidget(self.hud_label)
        self.status_toolbar.addWidget(FCLabel(" "))

        self.wplace_label = FCLabel("A4", color='tomato')
        self.wplace_label.setToolTip(_("Draw a delimiting rectangle on canvas.\n"
                                       "The purpose is to illustrate the limits for our work.")
                                     )
        self.wplace_label.setMargin(2)
        self.status_toolbar.addWidget(self.wplace_label)
        self.status_toolbar.addWidget(FCLabel(" "))

        # #######################################################################
        # ####################### Delta Coordinates TOOLBAR #####################
        # #######################################################################
        self.rel_position_label = FCLabel(
            "<b>Dx</b>: 0.0&nbsp;&nbsp;   <b>Dy</b>: 0.0&nbsp;&nbsp;&nbsp;&nbsp;")
        self.rel_position_label.setMinimumWidth(110)
        self.rel_position_label.setToolTip(_("Relative measurement.\nReference is last click position"))
        self.delta_coords_toolbar.addWidget(self.rel_position_label)

        # #######################################################################
        # ####################### Coordinates TOOLBAR ###########################
        # #######################################################################
        self.position_label = FCLabel("&nbsp;<b>X</b>: 0.0&nbsp;&nbsp;   <b>Y</b>: 0.0&nbsp;")
        self.position_label.setMinimumWidth(110)
        self.position_label.setToolTip(_("Absolute measurement.\n"
                                         "Reference is (X=0, Y= 0) position"))
        self.coords_toolbar.addWidget(self.position_label)

        # #######################################################################
        # ####################### TCL Shell DOCK ################################
        # #######################################################################
        self.shell_dock = FCDock(_("TCL Shell"), close_callback=self.toggle_shell_ui)
        self.shell_dock.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app24.png'))
        self.shell_dock.setObjectName('Shell_DockWidget')
        self.shell_dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.AllDockWidgetAreas)
        self.shell_dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable |
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.shell_dock)

        # ####################### GRBL Console DOCK ##############################
        self.grbl_console_dock = FCDock(_("GCode Console"), close_callback=self.on_grbl_console_closed)
        self.grbl_console_dock.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app24.png'))
        self.grbl_console_dock.setObjectName('GrblConsole_DockWidget')
        self.grbl_console_dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.AllDockWidgetAreas)
        self.grbl_console_dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable |
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self.grbl_console_dock)
        self.grbl_console_dock.hide()

        # ########################################################################
        # ########################## Notebook # ##################################
        # ########################################################################

        # ########################################################################
        # ########################## PROJECT Tab # ###############################
        # ########################################################################
        self.project_tab = QtWidgets.QWidget()
        self.project_tab.setObjectName("project_tab")

        self.project_frame_lay = QtWidgets.QVBoxLayout(self.project_tab)
        self.project_frame_lay.setContentsMargins(0, 0, 0, 0)

        self.project_frame = QtWidgets.QFrame()
        self.project_frame.setContentsMargins(0, 0, 0, 0)
        self.project_frame_lay.addWidget(self.project_frame)

        self.project_tab_layout = QtWidgets.QVBoxLayout(self.project_frame)
        self.project_tab_layout.setContentsMargins(2, 2, 2, 2)
        self.notebook.addTab(self.project_tab, _("Project"))
        self.notebook.protectTab(0)
        self.project_frame.setDisabled(False)

        # ########################################################################
        # ########################## SELECTED Tab # ##############################
        # ########################################################################
        self.properties_tab = QtWidgets.QWidget()
        # self.properties_tab.setMinimumWidth(270)
        self.properties_tab.setObjectName("properties_tab")
        self.properties_tab_layout = QtWidgets.QVBoxLayout(self.properties_tab)
        self.properties_tab_layout.setContentsMargins(2, 2, 2, 2)

        self.properties_scroll_area = VerticalScrollArea()
        # self.properties_scroll_area.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
        self.properties_tab_layout.addWidget(self.properties_scroll_area)
        self.notebook.addTab(self.properties_tab, _("Properties"))
        self.notebook.protectTab(1)

        # ########################################################################
        # ########################## TOOL Tab # ##################################
        # ########################################################################
        self.plugin_tab = QtWidgets.QWidget()
        self.plugin_tab.setObjectName("plugin_tab")
        self.plugin_tab_layout = QtWidgets.QVBoxLayout(self.plugin_tab)
        self.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)
        # self.notebook.addTab(self.plugin_tab, _("Tool"))

        self.plugin_scroll_area = VerticalScrollArea()
        # self.plugin_scroll_area.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
        self.plugin_tab_layout.addWidget(self.plugin_scroll_area)

        # ########################################################################
        # ########################## RIGHT Widget # ##############################
        # ########################################################################
        self.right_widget = QtWidgets.QWidget()
        self.right_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)
        self.splitter.addWidget(self.right_widget)

        self.right_lay = QtWidgets.QVBoxLayout()
        self.right_lay.setContentsMargins(0, 0, 0, 0)
        self.right_widget.setLayout(self.right_lay)

        # ########################################################################
        # ########################## PLOT AREA Tab # #############################
        # ########################################################################
        self.plot_tab_area = FCDetachableTab2(protect=False, protect_by_name=[_('Plot Area')], parent=self)
        self.plot_tab_area.useOldIndex(True)

        self.right_lay.addWidget(self.plot_tab_area)
        self.plot_tab_area.setTabsClosable(True)

        self.plot_tab = PlotTabWithDragDrop(app=self.app)
        self.plot_tab.setObjectName("plotarea_tab")
        self.plot_tab_area.addTab(self.plot_tab, _("Plot Area"))

        self.right_layout = QtWidgets.QVBoxLayout()
        self.right_layout.setObjectName("right_layout")
        self.right_layout.setContentsMargins(2, 2, 2, 2)
        self.plot_tab.setLayout(self.right_layout)

        # remove the close button from the Plot Area tab (first tab index = 0) as this one will always be ON
        self.plot_tab_area.protectTab(0)

        if _database_option(self.app, "global_theme") not in ['default', 'light']:
            self.plot_tab_area.setStyleSheet(
                """
                QTabWidget::pane {
                border: 0px solid rgba(255.000, 00.000, 00.000, 1.000);
                }
                """
            )

        # ########################################################################
        # ########################## PREFERENCES WINDOW ##########################
        # ########################################################################
        # built on the first opening of the Preferences: AppUIActions.on_preferences()
        self.preferences_window = None

        # ########################################################################
        # #################### SHORTCUT LIST AREA Tab # ##########################
        # ########################################################################
        self.shortcuts_tab = ShortcutsTab()

        # ########################################################################
        # ########################## PLOT AREA CONTEXT MENU  # ###################
        # ########################################################################
        self.popMenu = FCMenu()

        # View
        self.cmenu_viewmenu = self.popMenu.addMenu(
            QtGui.QIcon(self.app.resource_location + '/view64.png'), _("View"))
        self.popmenu_disable = self.cmenu_viewmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/disable32.png'), _("Toggle Visibility"))
        self.popmenu_panel_toggle = self.cmenu_viewmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/notebook16.png'), _("Toggle Panel"))
        self.cmenu_viewmenu.addSeparator()
        self.zoomfit = self.cmenu_viewmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_fit32.png'), _("Zoom Fit"))
        self.clearplot = self.cmenu_viewmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/clear_plot32.png'), _("Clear Plot"))
        self.replot = self.cmenu_viewmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/replot32.png'), _("Replot"))

        self.popMenu.addSeparator()
        self.cmenu_newmenu = self.popMenu.addMenu(
            QtGui.QIcon(self.app.resource_location + '/file32.png'), _("New"))
        self.popmenu_new_geo = self.cmenu_newmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/new_file_geo16.png'), _("Geometry"))
        self.popmenu_new_grb = self.cmenu_newmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/new_file_grb16.png'), "Gerber")
        self.popmenu_new_exc = self.cmenu_newmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/new_file_exc16.png'), _("Excellon"))
        self.cmenu_newmenu.addSeparator()
        self.popmenu_new_prj = self.cmenu_newmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/file16.png'), _("Project"))
        self.popMenu.addSeparator()

        # Grids
        self.cmenu_gridmenu = self.popMenu.addMenu(
            QtGui.QIcon(self.app.resource_location + '/grid32_menu.png'), _("Grids"))

        self.popMenu.addSeparator()

        # Set colors
        self.pop_menucolor = self.popMenu.addMenu(
            QtGui.QIcon(self.app.resource_location + '/set_color32.png'), _('Set Color'))

        self.pop_menu_red = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/red32.png'), _('Red'))

        self.pop_menu_blue = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/blue32.png'), _('Blue'))

        self.pop_menu_yellow = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/yellow32.png'), _('Yellow'))

        self.pop_menu_green = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/green32.png'), _('Green'))

        self.pop_menu_purple = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/violet32.png'), _('Purple'))

        self.pop_menu_brown = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/brown32.png'), _('Brown'))

        self.pop_menu_brown = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/indigo32.png'), _('Indigo'))

        self.pop_menu_brown = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/white32.png'), _('White'))

        self.pop_menu_brown = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/black32.png'), _('Black'))

        self.pop_menucolor.addSeparator()

        self.pop_menu_custom = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/set_color32.png'), _('Custom'))

        self.pop_menucolor.addSeparator()

        self.pop_menu_custom = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/set_color32.png'), _('Opacity'))

        self.pop_menu_custom = self.pop_menucolor.addAction(
            QtGui.QIcon(self.app.resource_location + '/set_color32.png'), _('Default'))

        self.popMenu.addSeparator()

        self.g_editor_cmenu = self.popMenu.addMenu(
            QtGui.QIcon(self.app.resource_location + '/draw32.png'), _("Geo Editor"))
        self.draw_line = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/path32.png'), _("Path"))
        self.draw_rect = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'), _("Rectangle"))
        self.g_editor_cmenu.addSeparator()
        self.draw_circle = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/circle32.png'), _("Circle"))
        self.draw_poly = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/polygon32.png'), _("Polygon"))
        self.draw_arc = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/arc32.png'), _("Arc"))
        self.g_editor_cmenu.addSeparator()

        self.draw_text = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/text32.png'), _("Text"))
        self.draw_simplification = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/simplify32.png'), _("Simplification"))
        self.draw_buffer = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/buffer32.png'), _("Buffer"))
        self.draw_paint = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/paint32.png'), _("Paint"))
        self.draw_eraser = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/eraser26.png'), _("Eraser"))
        self.g_editor_cmenu.addSeparator()

        self.draw_union = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/union32.png'), _("Union"))
        self.draw_polygonize = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/poligonize32.png'), _('Polygonize'))
        self.draw_intersect = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/intersection32.png'), _("Intersection"))
        self.draw_substract = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract32.png'), _("Subtraction"))
        self.draw_substract_alt = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract_alt32.png'), _("Alt Subtraction"))
        self.draw_cut = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/cutpath32.png'), _("Cut"))
        self.draw_transform = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/transform.png'), _("Transform"))

        self.g_editor_cmenu.addSeparator()
        self.draw_move = self.g_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'), _("Move"))

        self.grb_editor_cmenu = self.popMenu.addMenu(
            QtGui.QIcon(self.app.resource_location + '/draw32.png'), _("Gerber Editor"))
        self.grb_draw_pad = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/aperture32.png'), _("Pad"))
        self.grb_draw_pad_array = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/padarray32.png'), _("Pad Array"))
        self.grb_editor_cmenu.addSeparator()

        self.grb_draw_track = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/track32.png'), _("Track"))
        self.grb_draw_region = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/polygon32.png'), _("Region"))
        self.grb_draw_poligonize = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/poligonize32.png'), _("Poligonize"))
        self.grb_draw_semidisc = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/semidisc32.png'), _("SemiDisc"))
        self.grb_draw_disc = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/disc32.png'), _("Disc"))
        self.grb_editor_cmenu.addSeparator()

        self.grb_draw_buffer = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/buffer32.png'), _("Buffer"))
        self.grb_draw_scale = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/scale32.png'), _("Scale"))
        self.grb_draw_markarea = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/markarea32.png'), _("Mark Area"))
        self.grb_draw_eraser = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/eraser26.png'), _("Eraser"))
        self.grb_editor_cmenu.addSeparator()

        self.grb_draw_transformations = self.grb_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/transform.png'), _("Transform"))
        self.grb_editor_cmenu.addSeparator()
        # the same menus and actions as in the Gerber Editor menu
        for submenu in (self.grb_select_menu, self.grb_track_edit_menu, self.grb_aperture_menu, self.grb_auto_menu):
            self.grb_editor_cmenu.addMenu(submenu)
        self.grb_editor_cmenu.addAction(self.grb_step_repeat_menuitem)
        self.grb_editor_cmenu.addAction(self.grb_transcode_menuitem)
        self.grb_editor_cmenu.addAction(self.grb_snap_grid_menuitem)

        self.e_editor_cmenu = self.popMenu.addMenu(
            QtGui.QIcon(self.app.resource_location + '/drill32.png'), _("Exc Editor"))
        self.drill = self.e_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/open_excellon32.png'), _("Drill"))
        self.drill_array = self.e_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/addarray32.png'), _("Drill Array"))
        self.e_editor_cmenu.addSeparator()
        self.slot = self.e_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/slot26.png'), _("Slot"))
        self.slot_array = self.e_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/slot_array26.png'), _("Slot Array"))
        self.e_editor_cmenu.addSeparator()
        self.drill_resize = self.e_editor_cmenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/resize16.png'), _("Resize Drill"))

        self.popMenu.addSeparator()
        self.popmenu_copy = self.popMenu.addAction(QtGui.QIcon(self.app.resource_location + '/copy32.png'), _("Copy"))
        self.popmenu_delete = self.popMenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/delete32.png'), _("Delete"))
        self.popmenu_edit = self.popMenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/edit32.png'), _("Edit"))
        self.popmenu_save = self.popMenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/power16.png'), _("Exit Editor"))
        self.popmenu_save.setVisible(False)
        self.popMenu.addSeparator()

        self.popmenu_numeric_move = self.popMenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32_bis.png'), _("Num Move"))
        self.popmenu_move2origin = self.popMenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/move2origin32.png'), _("Move2Origin"))
        self.popmenu_move = self.popMenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'), _("Move"))
        self.popmenu_properties = self.popMenu.addAction(
            QtGui.QIcon(self.app.resource_location + '/properties32.png'), _("Properties"))

        # ########################################################################
        # ########################## INFO BAR # ##################################
        # ########################################################################
        self.infobar = self.statusBar()
        self.infobar.setSizeGripEnabled(False)
        self.fcinfo = AppInfoBar(app=self.app)

        self.infobar.addWidget(self.fcinfo, stretch=1)

        self.infobar.addWidget(self.delta_coords_toolbar)
        self.delta_coords_toolbar.setVisible(_database_option(self.app, "global_delta_coords_bar_show"))

        self.infobar.addWidget(self.coords_toolbar)
        self.coords_toolbar.setVisible(_database_option(self.app, "global_coords_bar_show"))

        self.grid_toolbar.setMaximumHeight(24)
        self.infobar.addWidget(self.grid_toolbar)
        self.grid_toolbar.setVisible(_database_option(self.app, "global_grid_bar_show"))

        self.status_toolbar.setMaximumHeight(24)
        self.infobar.addWidget(self.status_toolbar)
        self.status_toolbar.setVisible(_database_option(self.app, "global_statusbar_show"))

        self.units_label = FCLabel("[mm]")
        self.units_label.setToolTip(_("The application dimensional units is millimeter."))
        self.units_label.setMargin(2)
        self.units_label.setLayoutDirection(QtCore.Qt.LayoutDirection.LeftToRight)
        self.infobar.addWidget(self.units_label)

        # this used to be done in the APP.__init__()
        self.activity_view = FlatCAMActivityView(
            icon_location=self.app.resource_location,
            icon_kind=_database_option(self.app, "global_activity_icon"),
            replot_callback=self.app.on_toolbar_replot
        )
        self.infobar.addWidget(self.activity_view)
        self.compact_infobar()

        # disabled
        # self.progress_bar = QtWidgets.QProgressBar()
        # self.progress_bar.setMinimum(0)
        # self.progress_bar.setMaximum(100)
        # infobar.addWidget(self.progress_bar)

        # ########################################################################
        # ########################## SET GUI Elements # ##########################
        # ########################################################################
        self.app_icon = QtGui.QIcon()
        self.app_icon.addFile(self.app.resource_location + '/app16.png', QtCore.QSize(16, 16))
        self.app_icon.addFile(self.app.resource_location + '/app24.png', QtCore.QSize(24, 24))
        self.app_icon.addFile(self.app.resource_location + '/app32.png', QtCore.QSize(32, 32))
        self.app_icon.addFile(self.app.resource_location + '/app48.png', QtCore.QSize(48, 48))
        self.app_icon.addFile(self.app.resource_location + '/app64.png', QtCore.QSize(64, 64))
        self.app_icon.addFile(self.app.resource_location + '/app128.png', QtCore.QSize(128, 128))
        self.app_icon.addFile(self.app.resource_location + '/app256.png', QtCore.QSize(256, 256))
        self.setWindowIcon(self.app_icon)

        self.build_titlebar()
        self.set_ui_title('')

        self.filename = ""
        self.units = ""

        self.setAcceptDrops(True)

        # ########################################################################
        # ########################## Build GUI # #################################
        # ########################################################################
        self.grid_snap_btn.setCheckable(True)
        self.corner_snap_btn.setCheckable(True)
        self.editor_exit_btn_ret_action.setVisible(False)

        # start with GRID activated
        self.grid_snap_btn.trigger()

        self.g_editor_cmenu.menuAction().setVisible(False)
        self.grb_editor_cmenu.menuAction().setVisible(False)
        self.e_editor_cmenu.menuAction().setVisible(False)

        # ########################################################################
        # construct the Toolbar Lock menu entry to the context menu of the QMainWindow
        # ########################################################################
        self.lock_action = QtGui.QAction()
        self.lock_action.setText(_("Lock Toolbars"))
        self.lock_action.setCheckable(True)

        # ########################################################################
        # construct the Show Text menu entry to the context menu of the QMainWindow
        # ########################################################################
        self.show_text_action = QtGui.QAction()
        self.show_text_action.setText(_("Show Text"))
        self.show_text_action.setCheckable(True)

        # ########################################################################
        # ######################## BUILD PREFERENCES #############################
        # ########################################################################
        self.general_pref_form = GeneralPreferencesUI(app=self.app)
        self.gerber_pref_form = GerberPreferencesUI(app=self.app)
        self.excellon_pref_form = ExcellonPreferencesUI(app=self.app)
        self.geo_pref_form = GeometryPreferencesUI(app=self.app)
        self.cncjob_pref_form = CNCJobPreferencesUI(app=self.app)
        self.plugin_pref_form = PluginsPreferencesUI(app=self.app)
        self.plugin2_pref_form = Plugins2PreferencesUI(app=self.app)
        self.plugin_eng_pref_form = PluginsEngravingPreferencesUI(app=self.app)

        self.util_pref_form = UtilPreferencesUI(app=self.app)

        QtCore.QCoreApplication.instance().installEventFilter(self)

        # ########################################################################
        # ################## RESTORE UI from QSettings #################
        # ########################################################################
        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        if q_settings.contains("saved_gui_state"):
            self.restoreState(q_settings.value('saved_gui_state'), 0)
            self.grbl_console_dock.hide()
        tb_lock_state = q_settings.value('toolbar_lock', True, type=bool)
        show_text_state = q_settings.value('menu_show_text', True, type=bool)
        win_geo = q_settings.value('window_geometry', (100, 100, 800, 400))
        splitter_left = int(q_settings.value('splitter_left', 1))

        if q_settings.contains("layout"):
            layout = q_settings.value('layout', type=str)
            self.exc_edit_toolbar.setDisabled(True)
            self.geo_edit_toolbar.setDisabled(True)
            self.grb_edit_toolbar.setDisabled(True)

            self.app.log.debug("MainGUI.__init__() --> UI layout restored from QSettings. Layout = %s" % str(layout))
        else:
            self.exc_edit_toolbar.setDisabled(True)
            self.geo_edit_toolbar.setDisabled(True)
            self.grb_edit_toolbar.setDisabled(True)

            q_settings.setValue('layout', "standard")
            # This will write the setting to the platform specific storage.
            del q_settings
            self.app.log.debug("MainGUI.__init__() --> UI layout restored from options. QSettings set to 'standard'")

        self.setGeometry(win_geo[0], win_geo[1], win_geo[2], win_geo[3])
        self.app.log.debug("MainGUI.__init__() --> UI state restored from QSettings.")

        self.splitter.setSizes([splitter_left, 0])

        self.lock_toolbar(lock=tb_lock_state)
        self.show_text_under_action(show_text=show_text_state)

        self.corner_snap_btn.setVisible(False)
        self.snap_magnet.setVisible(False)

        self.lock_action.triggered[bool].connect(self.lock_toolbar)
        self.show_text_action.triggered.connect(self.show_text_under_action)

        self.wplace_label.clicked.connect(self.app.on_workspace_toggle)
        self.fcinfo.clicked.connect(self.toggle_shell_ui)
        self.coords_toolbar.clicked.connect(self.app.on_gui_coords_clicked)
        self.delta_coords_toolbar.clicked.connect(self.app.on_gui_coords_clicked)

        # to be used in the future
        # self.plot_tab_area.tab_attached.connect(lambda x: print(x))
        # self.plot_tab_area.tab_detached.connect(lambda x: print(x))

        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # %%%%%%%%%%%%%%%%% GUI Building FINISHED %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

        # Variable to store the status of the fullscreen event
        self.toggle_f_screen = False
        self.x_pos = None
        self.y_pos = None
        self.width = None
        self.height = None
        self.titlebar_height = None

        self.final_save.connect(self.app.final_save)

        # Notebook and Plot Tab Area signals
        # make the right click on the notebook tab and plot tab area tab raise a menu
        self.notebook.tabBar.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.ActionsContextMenu)
        self.plot_tab_area.tabBar.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.ActionsContextMenu)
        self.on_tab_setup_context_menu()
        # activate initial state
        self.on_detachable_tab_rmb_click(_database_option(self.app, "global_tabs_detachable"))

        # status bar activation/deactivation
        self.infobar.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.ActionsContextMenu)
        self.build_infobar_context_menu()
        self.plot_tab_area.currentChanged.connect(self.on_plot_area_tab_changed)
        self.on_plot_area_tab_changed()

        self.plot_tab_area.tab_detached.connect(self.on_tab_detached)

        # self.screenChanged.connect(self.on_screen_change)

    # def on_screen_change(self, old_screen, new_screen):
    #     """
    #     Handler of a signal that emits when screens are changed in a multi-monitor setup
    #
    #     :param old_screen:  QtGui.QScreen where the app windows was located before move
    #     :param new_screen:  QtGui.QScreen where the app windows is located after move
    #     :return:
    #     """
    #     old_pixel_ratio = old_screen.devicePixelRatio()
    #     new_pixel_ratio = new_screen.devicePixelRatio()
    #
    #     if old_pixel_ratio != 1.0 or new_pixel_ratio != 1.0:
    #         # update canvas dpi
    #         ratio = new_pixel_ratio / old_pixel_ratio
    #         self.app.plotcanvas.dpi = self.app.plotcanvas.Pdpi * ratio

    def update_location_labels(self, dx, dy, x, y):
        """
        Update the text of the location labels from InfoBar

        :param x:   X location
        :type x:    float
        :param y:   Y location
        :type y:    float
        :param dx:  Delta X location
        :type dx:   float
        :param dy:  Delta Y location
        :type dy:   float
        :return:
        :rtype:     None
        """

        self._location_values = (dx, dy, x, y)
        if not hasattr(self, '_location_update_timer'):
            self._location_update_timer = QtCore.QTimer(self)
            self._location_update_timer.setSingleShot(True)
            self._location_update_timer.setInterval(33)
            self._location_update_timer.timeout.connect(self._update_location_labels)
        if dx is None and dy is None and x is None and y is None:
            self._location_update_timer.stop()
            self._update_location_labels()
            return
        if not self._location_update_timer.isActive():
            self._location_update_timer.start()

    def _update_location_labels(self):
        dx, dy, x, y = self._location_values

        # Set the position label
        if x is None or y is None:
            self.position_label.setText("")
        else:
            x_dec = str(self.app.dec_format(x, self.app.decimals)) if x else '0.0'
            y_dec = str(self.app.dec_format(y, self.app.decimals)) if y else '0.0'
            self.position_label.setText("&nbsp;<b>X</b>: %s&nbsp;&nbsp;   "
                                        "<b>Y</b>: %s&nbsp;" % (x_dec, y_dec))

        # Set the Delta position label
        if dx is None or dy is None or (dx is None and dy is None):
            self.rel_position_label.setText("")
        else:
            dx_dec = str(self.app.dec_format(dx, self.app.decimals)) if dx else '0.0'
            dy_dec = str(self.app.dec_format(dy, self.app.decimals)) if dy else '0.0'

            self.rel_position_label.setText("<b>Dx</b>: %s&nbsp;&nbsp;  <b>Dy</b>: "
                                            "%s&nbsp;&nbsp;&nbsp;&nbsp;" % (dx_dec, dy_dec))

    def on_tab_detached(self, tab_detached, tab_detached_name):
        if tab_detached_name == 'FlatCAM Plot Area':
            pass
            # print(tab_detached_name)
            # self.app.plotcanvas.unfreeze()
            # self.app.plotcanvas.native.setParent(tab_detached)
            # self.app.plotcanvas.freeze()

    def build_titlebar(self):
        self.title_bar = None
        self.title_toolbar = None
        if _database_option(self.app, 'global_custom_titlebar'):
            self.title_bar = TitleBar(self, self.menu, self.menu_toggle_nb,
                                      history_actions=(self.undo_btn, self.redo_btn),
                                      hamburger=_database_option(self.app, 'global_hamburger_menu'))
            self.title_toolbar = self.title_bar.toolbar
            self.setMenuWidget(self.title_bar)
        else:
            self.setMenuBar(self.menu)

    def set_ui_title(self, name):
        """
        Sets the title of the main window.

        :param name: String that store the project path and project name
        :return: None
        """
        # the title shows the name of the active project instead of its file path
        projects = getattr(self.app, 'projects', None)
        if projects is not None and (not name or name == self.app.project_filename or
                                     name == _("New Project - Not saved")):
            name = projects.active.name

        if self.title_bar is None:
            title = (
                f"FlatCAM Evo {self.app.version} "
                f"{'BETA' if self.app.beta else ''} - {platform.architecture()[0]} - "
                f"{name}"
            )
            self.setWindowTitle(title)
            return

        if not name or name == _("New Project - Not saved"):
            name = _("Untitled")
        self.title_bar.project_title = name
        self.setWindowTitle('FlatCAM Evo %s' % self.app.version)
        self.title_bar.update_title()

    def place_on_cursor_screen(self):
        """
        Moves the window, before the first show, to the screen of the mouse cursor. A maximized window is
        maximized on the screen that holds its normal geometry, so it also opens on that screen.
        """
        screen = cursor_screen()
        if screen is None:
            return
        self.setScreen(screen)
        self.setGeometry(geometry_on_screen(self.geometry(), screen.availableGeometry()))

    def on_toggle_gui(self):
        if self.isHidden():
            mgui_settings = QSettings("Open Source", "FlatCAM_EVO")
            if mgui_settings.contains("maximized_gui"):
                maximized_ui = mgui_settings.value('maximized_gui', type=bool)
                if maximized_ui is True:
                    self.showMaximized()
                else:
                    self.show()
            else:
                self.show()
        else:
            self.hide()

    def on_tab_setup_context_menu(self):
        initial_checked = _database_option(self.app, "global_tabs_detachable")
        action_name = str(_("Detachable Tabs"))
        action = QtGui.QAction(self)
        action.setCheckable(True)
        action.setText(action_name)
        action.setChecked(initial_checked)

        self.notebook.tabBar.addAction(action)
        self.plot_tab_area.tabBar.addAction(action)

        try:
            action.triggered.disconnect()
        except TypeError:
            pass
        action.triggered.connect(self.on_detachable_tab_rmb_click)

    def on_detachable_tab_rmb_click(self, checked):
        self.notebook.set_detachable(val=checked)
        self.app.defaults["global_tabs_detachable"] = checked

        self.plot_tab_area.set_detachable(val=checked)
        self.app.defaults["global_tabs_detachable"] = checked

    def compact_infobar(self):
        """Reduce status-bar spacing without shrinking its fonts or clipping text."""
        self.infobar.ensurePolished()
        QtCore.QCoreApplication.sendEvent(self.infobar, QtCore.QEvent(QtCore.QEvent.Type.LayoutRequest))
        target_height = round(self.infobar.sizeHint().height() * 0.75)
        status_style = """
            QStatusBar QLineEdit {
                padding-top: 0px; padding-bottom: 0px;
                margin-top: 0px; margin-bottom: 0px;
                min-height: 0px;
            }
        """
        if _database_option(self.app, 'global_theme') == 'dark':
            status_style += 'QStatusBar::item { border: none; }'
        self.infobar.setStyleSheet(status_style)
        for toolbar in (self.delta_coords_toolbar, self.coords_toolbar,
                        self.grid_toolbar, self.status_toolbar):
            toolbar.setStyleSheet("""
                QToolBar { spacing: 0px; padding: 0px; margin: 0px; border: 0px; }
                QToolBar QToolButton { padding: 0px; margin: 0px; border: 0px; }
                QToolBar QCheckBox { padding: 0px; margin: 0px; border: 0px; }
                QToolBar QCheckBox::indicator { margin: 0px; width: 16px; height: 16px; }
            """)
            toolbar.setIconSize(QtCore.QSize(16, 16))
            toolbar.setMaximumHeight(16777215)
        self.units_label.setMargin(0)
        self.activity_view.icon.setFixedSize(16, 16)
        self.activity_view.icon.setScaledContents(True)
        self.activity_view.movie.setScaledSize(QtCore.QSize(16, 16))
        for label in self.status_toolbar.findChildren(QtWidgets.QLabel):
            if not label.pixmap().isNull():
                label.setFixedSize(16, 16)
                label.setScaledContents(True)
        # Refresh Qt's cached child sizes before choosing a clipping-safe minimum.
        QtCore.QCoreApplication.sendEvent(self.infobar, QtCore.QEvent(QtCore.QEvent.Type.LayoutRequest))
        self.infobar.setMinimumHeight(max(target_height, self.infobar.minimumSizeHint().height()))

    def build_infobar_context_menu(self):
        for toolbar, callback in ((self.delta_coords_toolbar, self.toggle_delta_coords),
                                   (self.coords_toolbar, self.toggle_coords),
                                   (self.grid_toolbar, self.toggle_gridbar),
                                   (self.status_toolbar, self.toggle_statusbar)):
            action = toolbar.toggleViewAction()
            self.infobar.addAction(action)
            action.triggered.connect(callback)

    def on_plot_area_tab_changed(self, index=None):
        plot_active = self.plot_tab_area.currentWidget() is self.plot_tab
        self.coords_toolbar.setVisible(plot_active and _database_option(self.app, "global_coords_bar_show"))
        self.delta_coords_toolbar.setVisible(
            plot_active and _database_option(self.app, "global_delta_coords_bar_show"))

    def toggle_coords(self, checked):
        self.app.options["global_coords_bar_show"] = checked
        self.coords_toolbar.setVisible(checked and self.plot_tab_area.currentWidget() is self.plot_tab)

    def toggle_delta_coords(self, checked):
        self.app.options["global_delta_coords_bar_show"] = checked
        self.delta_coords_toolbar.setVisible(
            checked and self.plot_tab_area.currentWidget() is self.plot_tab
        )

    def toggle_gridbar(self, checked):
        self.app.options["global_grid_bar_show"] = checked
        self.grid_toolbar.setVisible(checked)

    def toggle_statusbar(self, checked):
        self.app.options["global_statusbar_show"] = checked
        self.status_toolbar.setVisible(checked)

    def on_preferences_open_folder(self):
        """
        Will open an Explorer window set to the folder path where the FlatCAM preferences files are usually saved.

        :return: None
        """

        if sys.platform == 'win32':
            subprocess.Popen('explorer %s' % self.app.data_path)
        elif sys.platform == 'darwin':
            os.system('open "%s"' % self.app.data_path)
        else:
            subprocess.Popen(['xdg-open', self.app.data_path])
        self.app.inform.emit('[success] %s' % _("FlatCAM Preferences Folder opened."))

    def on_gui_clear(self, forced_clear=False):
        """
        Will clear the settings that are stored in QSettings.
        """
        self.app.log.debug("Clearing the settings in QSettings. GUI settings cleared.")

        response = None
        bt_yes = None
        if forced_clear is False:
            msgbox = FCMessageBox(parent=self)
            title = _("Clear GUI Settings")
            txt = _("Are you sure you want to delete the GUI Settings? \n")
            msgbox.setWindowTitle(title)  # taskbar still shows it
            msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
            msgbox.setText('<b>%s</b>' % title)
            msgbox.setInformativeText(txt)
            msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/trash32.png'))

            bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
            bt_no = msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)

            msgbox.setDefaultButton(bt_no)
            msgbox.exec()
            response = msgbox.clickedButton()

        if forced_clear is True or response == bt_yes:
            q_settings = QSettings("Open Source", "FlatCAM_EVO")
            for key in q_settings.allKeys():
                q_settings.remove(key)
            q_settings.setValue('theme', 'light')
            # This will write the setting to the platform specific storage.
            del q_settings

    def populate_toolbars(self):
        """
        Will populate the App Toolbars with their actions

        :return: None
        """
        self.app.log.debug(" -> Add actions to new Toolbars")

        # ########################################################################
        # ##################### File Toolbar #####################################
        # ########################################################################
        self.file_open_gerber_btn = self.toolbarfile.addAction(
            QtGui.QIcon(self.app.resource_location + '/open_gerber32.png'), _("Gerber"))
        self.file_open_gerber_btn.setToolTip(_("Open a Gerber file."))
        self.file_open_excellon_btn = self.toolbarfile.addAction(
            QtGui.QIcon(self.app.resource_location + '/open_excellon32.png'), _("Excellon"))
        self.file_open_excellon_btn.setToolTip(_("Open a Excellon file."))
        # the project commands are in the File menu and in the menu of the project combobox

        # ########################################################################
        # ######################### Edit Toolbar #################################
        # ########################################################################
        self.editor_start_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/edit_file32.png'), _("Editor"))

        self.editor_exit_btn = QtWidgets.QToolButton()
        self.editor_exit_btn.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.editor_exit_btn.setText(_("Exit Edit"))
        # expand vertically to fill the toolbar height
        self.editor_exit_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding)

        # https://www.w3.org/TR/SVG11/types.html#ColorKeywords
        self.editor_exit_btn.setStyleSheet("""
                                          QToolButton
                                          {
                                              color: white;
                                              background-color: #4169E1;
                                              border: none;
                                              border-radius: 4px;
                                              padding: 2px;
                                          }
                                          """)
        self.editor_exit_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/power16.png'))
        self.editor_exit_btn.setToolTip(_("Exit from Editor."))
        # in order to hide it we hide the returned action
        self.editor_exit_btn_ret_action = self.toolbaredit.addWidget(self.editor_exit_btn)

        self.copy_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy_file32.png'), _("Copy"))
        self.copy_btn.setToolTip(_("Copy a selection of objects."))
        self.delete_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash32.png'), _("Delete"))
        self.delete_btn.setToolTip(_("Delete a selection of objects."))
        self.toolbaredit.addSeparator()
        self.distance_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/distance32.png'), _("Distance"))
        self.distance_btn.setToolTip(_("Measure a distance between two or more points."))
        # self.distance_min_btn = self.toolbaredit.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/distance_min32.png'), _("Min Distance"))
        # self.distance_min_btn.setToolTip(_("Measure the minimum distance between two objects."))
        self.origin_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/origin32.png'), _('Set Origin'))
        self.origin_btn.setToolTip(_("Set an origin to a custom location."))
        # self.move2origin_btn = self.toolbaredit.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/move2origin32.png'), _('To Orig.'))
        # self.move2origin_btn.setToolTip(_("Move selected objects to the origin."))
        # self.center_in_origin_btn = self.toolbaredit.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/custom_origin32.png'), _('C Origin'))
        # self.center_in_origin_btn.setToolTip(_("Move the selected objects to custom positions."))

        self.jmp_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/jump_to32.png'), _('Jump to'))
        self.jmp_btn.setToolTip(_("Move the mouse cursor to a defined position and center into view."))
        self.locate_btn = self.toolbaredit.addAction(
            QtGui.QIcon(self.app.resource_location + '/locate32.png'), _('Locate'))
        self.locate_btn.setToolTip(_("Locate a predefined position on a selected object."))

        # ########################################################################
        # ########################## View Toolbar# ###############################
        # ########################################################################
        self.replot_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/replot32.png'), _("Replot"))
        self.clear_plot_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/clear_plot32.png'), _("Clear Plot"))
        self.zoom_in_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_in32.png'), _("Zoom In"))
        self.zoom_out_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_out32.png'), _("Zoom Out"))
        self.zoom_fit_btn = self.toolbarview.addAction(
            QtGui.QIcon(self.app.resource_location + '/zoom_fit32.png'), _("Zoom Fit"))

        # ########################################################################
        # ########################## Shell Toolbar# ##############################
        # ########################################################################
        self.shell_btn = self.toolbarshell.addAction(
            QtGui.QIcon(self.app.resource_location + '/shell32.png'), _("Command Line"))
        self.new_script_btn = self.toolbarshell.addAction(
            QtGui.QIcon(self.app.resource_location + '/script_new24.png'), '%s ...' % _('New Script'))
        self.open_script_btn = self.toolbarshell.addAction(
            QtGui.QIcon(self.app.resource_location + '/open_script32.png'), '%s ...' % _('Open Script'))
        self.run_script_btn = self.toolbarshell.addAction(
            QtGui.QIcon(self.app.resource_location + '/script16.png'), '%s ...' % _('Run Script'))

        # #########################################################################
        # ######################### Tools Toolbar #################################
        # #########################################################################
        self.drill_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/drill32.png'), _("Drilling"))
        self.mill_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/milling_tool32.png'), _("Milling"))

        self.toolbarplugins.addSeparator()

        self.isolation_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/iso32.png'), _("Isolation"))

        self.ncc_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/ncc32.png'), _("NCC"))
        self.paint_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/paint32.png'), _("Paint"))
        self.photocarve_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/photo_carving64.png'), _("Carve"))
        self.photocarve_btn.setToolTip(_("Carve a loaded picture into a material."))

        self.toolbarplugins.addSeparator()

        self.cutout_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/cut32.png'), _("Cutout"))
        self.panelize_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/panelize32.png'), _("Panel"))
        self.film_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/film32.png'), _("Film"))
        self.dblsided_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/doubleside32.png'), _("2-Sided"))

        self.toolbarplugins.addSeparator()

        self.align_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/align32.png'), _("Align"))
        # self.sub_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/sub32.png'), _("Subtract"))

        # self.extract_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/extract32.png'), _("Extract"))
        self.copperfill_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/copperfill32.png'), _("Thieving"))
        self.markers_tool_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/corners_32.png'), _("Markers"))
        self.punch_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/punch32.png'), _("Punch Gerber"))
        self.calculators_btn = self.toolbarplugins.addAction(
            QtGui.QIcon(self.app.resource_location + '/calculator32.png'), _("Calculators"))

        self.toolbarplugins.addSeparator()

        # self.solder_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/solderpastebis32.png'), _("SolderPaste"))
        # self.sub_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/sub32.png'), _("Subtract"))
        # self.rules_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/rules32.png'), _("Rules"))
        # self.optimal_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/open_excellon32.png'), _("Optimal"))
        # self.transform_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/transform.png'), _("Transform"))
        # self.qrcode_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/qrcode32.png'), _("QRCode"))
        # self.align_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/align32.png'), _("Align Objects"))
        # self.fiducials_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/fiducials_32.png'), _("Fiducials"))
        # self.cal_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/calibrate_32.png'), _("Calibration"))

        # self.invert_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/invert32.png'), _("Invert Gerber"))
        # self.etch_btn = self.toolbarplugins.addAction(
        #     QtGui.QIcon(self.app.resource_location + '/etch_32.png'), _("Etch Compensation"))

        # ########################################################################
        # ################### Excellon Editor Toolbar ############################
        # ########################################################################
        self.select_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/pointer32.png'), _("Select"))
        self.add_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/plus16.png'), _('Add Drill'))
        self.add_drill_array_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/addarray16.png'), _('Add Drill Array'))
        self.resize_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/resize16.png'), _('Resize Drill'))
        self.add_slot_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/slot26.png'), _('Add Slot'))
        self.add_slot_array_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/slot_array26.png'), _('Add Slot Array'))
        self.exc_edit_toolbar.addSeparator()

        self.copy_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'), _('Copy'))
        self.delete_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash32.png'), _("Delete"))

        self.exc_edit_toolbar.addSeparator()
        self.move_drill_btn = self.exc_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'), _("Move"))

        # ########################################################################
        # ################### Geometry Editor Toolbar ############################
        # ########################################################################
        self.geo_select_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/pointer32.png'), _("Select"))
        self.geo_add_circle_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/circle32.png'), _('Circle'))
        self.geo_add_arc_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/arc32.png'), _('Arc'))
        self.geo_add_rectangle_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'), _('Rectangle'))

        self.geo_edit_toolbar.addSeparator()
        self.geo_add_path_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/path32.png'), _('Path'))
        self.geo_add_polygon_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/polygon32.png'), _('Polygon'))
        self.geo_edit_toolbar.addSeparator()
        self.geo_add_text_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/text32.png'), _('Text'))
        self.geo_add_buffer_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/buffer32.png'), _('Buffer'))
        self.geo_add_paint_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/paint32.png'), _('Paint'))
        self.geo_eraser_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/eraser26.png'), _('Eraser'))

        self.geo_edit_toolbar.addSeparator()
        self.geo_union_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/union32.png'), _('Union'))
        self.geo_join_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/join32.svg'), _('Join'))
        self.geo_join_btn.setToolTip(_('Join selected lines into polylines. Uses the Join tolerance in Properties.'))
        self.geo_polygonize_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/poligonize32.png'), _('Polygonize'))
        self.geo_polygonize_btn.setToolTip(
            _('Create polygons from selected closed paths or enclosing segments.\n'
              'Unused open segments are kept. Endpoints must meet exactly.'))
        self.geo_explode_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/explode32.png'), _('Explode'))

        self.geo_intersection_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/intersection32.png'), _('Intersection'))
        self.geo_subtract_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract32.png'), _('Subtraction'))
        self.geo_alt_subtract_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract_alt32.png'), _('Alt Subtraction'))

        self.geo_edit_toolbar.addSeparator()
        self.geo_cutpath_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/cutpath32.png'), _('Cut Path'))
        self.geo_copy_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'), _("Copy"))
        self.geo_import_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'), _("Import"))
        self.geo_delete_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash32.png'), _("Delete"))
        self.geo_transform_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/transform.png'), _("Transform"))

        self.geo_edit_toolbar.addSeparator()
        self.geo_move_btn = self.geo_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'), _("Move"))

        # ########################################################################
        # ################### Gerber Editor Toolbar ##############################
        # ########################################################################
        self.grb_select_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/pointer32.png'), _("Select"))
        self.grb_add_pad_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/aperture32.png'), _("Pad"))
        self.add_pad_ar_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/padarray32.png'), _('Pad Array'))
        self.grb_add_track_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/track32.png'), _("Track"))
        self.grb_join_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/join32.svg'), _('Join'))
        self.grb_join_btn.setToolTip(_('Join selected connected track sections into one track.'))
        self.grb_add_region_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/polygon32.png'), _("Region"))
        self.grb_convert_poly_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/poligonize32.png'), _("Poligonize"))

        self.grb_add_semidisc_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/semidisc32.png'), _("SemiDisc"))
        self.grb_add_disc_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/disc32.png'), _("Disc"))
        self.grb_add_rectangle_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/rectangle32.png'), _("Rect. Reg"))
        self.grb_add_rectangle_btn.setToolTip(_("Rectangular Region"))

        self.grb_add_copper_pour_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/copper_pour.png'), _("Copper Pour"))

        self.grb_add_text_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/text32.png'), _("Text Reg"))
        self.grb_add_text_btn.setToolTip(_("Text Region"))

        self.grb_add_arc_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/arc32.png'), _("Arc Follow"))
        self.grb_add_arc_btn.setToolTip(_("Arc: Circular Follow"))

        self.grb_difference_keep_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/subtract_alt32.png'), _("Diff. Keep"))
        self.grb_difference_keep_btn.setToolTip(_("Difference: Keep Cutters"))
        self.grb_edit_toolbar.addSeparator()

        self.aperture_buffer_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/buffer32.png'), _('Buffer'))
        self.aperture_simplify_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/simplify32.png'), _('Simplification'))
        self.aperture_scale_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/scale32.png'), _('Scale'))
        self.aperture_markarea_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/markarea32.png'), _('Mark Area'))
        self.grb_import_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/import.png'), _('Import Shape'))

        self.aperture_eraser_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/eraser26.png'), _('Eraser'))

        self.grb_edit_toolbar.addSeparator()
        self.aperture_copy_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/copy32.png'), _("Copy"))
        self.aperture_delete_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/trash32.png'), _("Delete"))
        self.grb_transform_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/transform.png'), _("Transform"))
        self.grb_edit_toolbar.addSeparator()
        self.aperture_move_btn = self.grb_edit_toolbar.addAction(
            QtGui.QIcon(self.app.resource_location + '/move32.png'), _("Move"))

        self.corner_snap_btn.setVisible(False)
        self.snap_magnet.setVisible(False)
        self.editor_exit_btn_ret_action.setVisible(False)

        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        if q_settings.contains("layout"):
            layout = q_settings.value('layout', type=str)

            # on 'minimal' layout only some toolbars are active
            if layout != 'minimal':
                self.exc_edit_toolbar.setVisible(True)
                self.exc_edit_toolbar.setDisabled(True)
                self.geo_edit_toolbar.setVisible(True)
                self.geo_edit_toolbar.setDisabled(True)
                self.grb_edit_toolbar.setVisible(True)
                self.grb_edit_toolbar.setDisabled(True)

    def on_shortcut_list(self):
        # add the tab if it was closed
        self.plot_tab_area.addTab(self.shortcuts_tab, _("Key Shortcut List"))

        # delete the absolute and relative position and messages in the infobar
        # self.ui.position_label.setText("")
        # self.ui.rel_position_label.setText("")
        # hide coordinates toolbars in the infobar while in DB
        self.coords_toolbar.hide()
        self.delta_coords_toolbar.hide()

        # Switch plot_area to preferences page
        self.plot_tab_area.setCurrentWidget(self.shortcuts_tab)
        # self.show()

    def on_select_tab(self, name):
        # if the splitter is hidden, display it, else hide it but only if the current widget is the same
        if self.splitter.sizes()[0] == 0:
            self.splitter.setSizes([1, 1])
        else:
            if self.notebook.currentWidget().objectName() == name + '_tab':
                self.splitter.setSizes([0, 1])

        if name == 'project':
            self.notebook.setCurrentWidget(self.project_tab)
        elif name == 'properties':
            self.notebook.setCurrentWidget(self.properties_tab)
        elif name == 'tool':
            self.notebook.setCurrentWidget(self.plugin_tab)

    def createPopupMenu(self):
        menu = super().createPopupMenu()

        menu.addSeparator()
        menu.addAction(self.lock_action)
        menu.addAction(self.show_text_action)
        return menu

    def lock_toolbar(self, lock=False):
        """
        Used to (un)lock the toolbars of the app.

        :param lock: boolean, will lock all toolbars in place when set True
        :return: None
        """

        self.lock_action.setChecked(lock)
        if lock:
            for widget in self.children():
                if isinstance(widget, QtWidgets.QToolBar):
                    widget.setMovable(False)
        else:
            for widget in self.children():
                if isinstance(widget, QtWidgets.QToolBar):
                    widget.setMovable(True)

        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        q_settings.setValue('toolbar_lock', lock)
        # This will write the setting to the platform specific storage.
        del q_settings

    def show_text_under_action(self, show_text=True):
        self.show_text_action.setChecked(show_text)
        if show_text:
            for widget in self.children():
                if isinstance(widget, QtWidgets.QToolBar):
                    widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        else:
            for widget in self.children():
                if isinstance(widget, QtWidgets.QToolBar):
                    widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        q_settings.setValue('menu_show_text', show_text)
        # This will write the setting to the platform specific storage.
        del q_settings

    # def on_full_screen_toggled(self, disable=False):
    #     """
    #     Toggles the full screen mode of the window.
    #
    #     :param disable: Whether to disable full screen mode. Defaults to False.
    #     """
    #     flags = self.windowFlags()
    #     if self.toggle_f_screen is False and disable is False:
    #         # self.ui.showFullScreen()
    #         self.setWindowFlags(flags | Qt.WindowType.FramelessWindowHint)
    #         a = self.geometry()
    #         self.x_pos = a.x()
    #         self.y_pos = a.y()
    #         self.width = a.width()
    #         self.height = a.height()
    #         self.titlebar_height = self.app.qapp.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_TitleBarHeight)
    #
    #         self.app.ui.showFullScreen()
    #
    #         # hide all Toolbars
    #         for tb in self.findChildren(QtWidgets.QToolBar):
    #             if isinstance(tb, QtWidgets.QToolBar):
    #                 tb.setVisible(False)
    #
    #         self.coords_toolbar.setVisible(self.app.defaults["global_coords_bar_show"])
    #         self.delta_coords_toolbar.setVisible(self.app.defaults["global_delta_coords_bar_show"])
    #         self.grid_toolbar.setVisible(self.app.defaults["global_grid_bar_show"])
    #         self.status_toolbar.setVisible(self.app.defaults["global_statusbar_show"])
    #
    #         self.splitter.setSizes([0, 1])
    #         self.toggle_f_screen = True
    #     elif self.toggle_f_screen is True or disable is True:
    #         self.setWindowFlags(flags & ~Qt.WindowType.FramelessWindowHint)
    #         # the additions are made to account for the pixels we subtracted/added above in the (x, y, h, w)
    #         # self.setGeometry(self.x_pos+1, self.y_pos+self.titlebar_height+4, self.width, self.height)
    #         self.setGeometry(self.x_pos+1, self.y_pos+self.titlebar_height+13, self.width, self.height)
    #
    #         self.showNormal()
    #         self.toggle_f_screen = False

    def on_full_screen_toggled(self, disable=False):
        """
        Toggles the full screen mode of the window.

        :param disable: Whether to disable full screen mode. Defaults to False.
        """

        if self.isFullScreen() or disable:
            self.setWindowFlags(self.original_window_flags)

            # Restore visibility of all toolbars
            for toolbar in self.hidden_toolbars:
                toolbar.setVisible(True)
            # Clear the list
            self.hidden_toolbars = []

            # Leave fullscreen before restoring Qt's normal-window geometry.
            self.showNormal()
            # Native state changes must finish before setting the restore rectangle.
            QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            self.setGeometry(
                self.x_pos,
                self.y_pos + self.titlebar_height,
                self.width,
                self.height
            )

            if self._fullscreen_maximized:
                self.showMaximized()

            return

        # Save and hide all visible toolbars
        self.hidden_toolbars = []
        for toolbar in self.findChildren(QtWidgets.QToolBar):
            if toolbar.isVisible():
                self.hidden_toolbars.append(toolbar)
                toolbar.setVisible(False)

        self.original_window_flags = self.windowFlags()
        self._fullscreen_maximized = self.isMaximized()
        current_geometry = self.normalGeometry() if self._fullscreen_maximized else self.geometry()

        # save the current geometry
        self.x_pos = current_geometry.x()
        self.y_pos = current_geometry.y()
        self.width = current_geometry.width()
        self.height = current_geometry.height()
        self.titlebar_height = 0

        # activate FullScreen
        self.app.ui.showFullScreen()

        self.coords_toolbar.setVisible(_database_option(self.app, "global_coords_bar_show"))
        self.delta_coords_toolbar.setVisible(_database_option(self.app, "global_delta_coords_bar_show"))
        self.grid_toolbar.setVisible(_database_option(self.app, "global_grid_bar_show"))
        self.status_toolbar.setVisible(_database_option(self.app, "global_statusbar_show"))
        
    def on_toggle_plotarea(self):
        """

        :return:
        """
        try:
            name = self.plot_tab_area.widget(0).objectName()
        except AttributeError:
            self.plot_tab_area.addTab(self.plot_tab, _("Plot Area"))
            # remove the close button from the Plot Area tab (first tab index = 0) as this one will always be ON
            self.plot_tab_area.protectTab(0)
            return

        if name != 'plotarea_tab':
            self.plot_tab_area.insertTab(0, self.plot_tab, _("Plot Area"))
            # remove the close button from the Plot Area tab (first tab index = 0) as this one will always be ON
            self.plot_tab_area.protectTab(0)
        else:
            self.plot_tab_area.closeTab(0)

    def on_toggle_notebook(self):
        """

        :return:
        """
        if self.splitter.sizes()[0] == 0:
            self.splitter.setSizes([1, 1])
            self.menu_toggle_nb.setChecked(True)
        else:
            self.splitter.setSizes([0, 1])
            self.menu_toggle_nb.setChecked(False)

    def on_toggle_grid(self):
        """

        :return:
        """
        self.grid_snap_btn.trigger()

    def on_toggle_grid_lines(self):
        canvas3d = self.app.plotcanvas3d
        if canvas3d is not None and (self.plot_tab_area.currentWidget() is self.app.area_3d_tab or
                                     canvas3d.native.hasFocus()):
            canvas3d.toggle_grid()
        else:
            self.app.plotcanvas.on_toggle_grid_lines()

    def on_grbl_console_closed(self):
        """The user closed the GRBL console dock with its own button."""
        sender = getattr(self.app, 'grbl_sender_tool', None)
        if sender is None:
            self.grbl_console_dock.hide()
        else:
            sender.hide_console()
        self.app.options["tools_grbl_console_visible"] = False
        if sender is not None and sender.ui is not None:
            sender.ui.console_cb.set_value(False)

    def toggle_shell_ui(self):
        """
        Toggle shell dock: if is visible close it, if it is closed then open it

        :return: None
        """

        if self.shell_dock.isVisible():
            self.shell_dock.hide()
            self.app.plotcanvas.native.setFocus()
        else:
            self.shell_dock.show()

            # I want to take the focus and give it to the Tcl Shell when the Tcl Shell is run
            # self.shell._edit.setFocus()
            QtCore.QTimer.singleShot(0, lambda: self.shell_dock.widget()._edit.setFocus())

            # HACK - simulate a mouse click - alternative
            # no_km = QtCore.Qt.KeyboardModifier(QtCore.Qt.KeyboardModifier.NoModifier)    # no KB modifier
            # pos = QtCore.QPoint((self.shell._edit.width() - 40), (self.shell._edit.height() - 2))
            # e = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, pos, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
            #                       no_km)
            # QtCore.QCoreApplication.instance().sendEvent(self.shell._edit, e)
            # f = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, pos, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
            #                       no_km)
            # QtCore.QCoreApplication.instance().sendEvent(self.shell._edit, f)

    def on_history_changed(self, undo_label, redo_label):
        """The Edit menu items and the toolbar buttons show the next Undo / Redo step."""
        for action, label, name, aid in (
                (self.menuedit_undo, undo_label, _('Undo'), 'app.undo'),
                (self.menuedit_redo, redo_label, _('Redo'), 'app.redo_alt')):
            text = '%s %s' % (name, label) if label else name
            action.setText('%s	%s' % (text, shortcuts.native_text(shortcuts.registry().key(aid))))
            action.setEnabled(bool(label))
        for button, label, name in ((self.undo_btn, undo_label, _('Undo')), (self.redo_btn, redo_label, _('Redo'))):
            button.setToolTip('%s %s' % (name, label) if label else name)
            button.setEnabled(bool(label))

    @staticmethod
    def app_focus_in_text():
        """True if the keyboard focus is in a text or number field (Ctrl+Z belongs to the field then)."""
        focus = QtWidgets.QApplication.focusWidget()
        while focus is not None:
            if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit,
                                  QtWidgets.QAbstractSpinBox)):
                return True
            focus = focus.parentWidget()
        return False

    def keyPressEvent(self, event):
        """
        Key event handler for the entire app: the keys of the plot area and of the Project tab. The action of a key
        comes from the registry of the shortcuts (appGUI/shortcuts.py, the table in appGUI/shortcut_actions.py).
        Some key events are also treated locally in the FlatCAM editors

        :param event: QT event
        :return:
        """

        modifiers = QtWidgets.QApplication.keyboardModifiers()

        # events out of the self.app.collection view (it's about Project Tab) are of type int
        if type(event) is int:
            key = event
        # events from the GUI are of type QKeyEvent
        elif type(event) == QtGui.QKeyEvent:
            #     key = event.key()     # commented due changes started in VisPy 0.15 in regards of Qt events handling
            return
        else:

            # The native object is the QKeyEvent from PyQt/PySide
            native_event = event.native
            if hasattr(native_event, 'isAutoRepeat') and native_event.isAutoRepeat():
                return

            # VISPY events
            key = event.key

        try:
            if event.isAutoRepeat():
                return
        except AttributeError:
            pass

        # A Selection Tool drag must be cancellable while its Shift/Alt modifier is still held.
        if key in (QtCore.Qt.Key.Key_Escape, 'Escape') and self.app.call_source in ('geo_editor', 'grb_editor'):
            editor = getattr(self.app, self.app.call_source)
            grips = getattr(editor, 'vertex_grips', None)
            if grips is not None and grips.segment_drag.pressed is not None:
                grips.cancel()
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                return

        # the action of the key in the context of the application (the editor or the plugin that is active)
        return shortcuts.dispatch(self, self.app.call_source, key, modifiers)

    def goto_text_line(self):
        """
        Will scroll a text to the specified text line.

        :return: None
        """
        editor_widget = self.plot_tab_area.currentWidget()
        try:
            c_editor = editor_widget.code_editor  # noqa
        except AttributeError:
            return

        dia_box = Dialog_box(title=_("Go to Line ..."),
                             label='%s:' % _("Line"),
                             icon=QtGui.QIcon(self.app.resource_location + '/jump_to32.png'),
                             initial_text='')
        if not dia_box.ok:
            return

        # get the document() of the AppTextEditor
        doc = c_editor.document()
        try:
            line = int(dia_box.location) - 1
        except (ValueError, TypeError):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Wrong value format entered, use a number."))
            return
        if not 0 <= line < doc.blockCount():
            self.app.inform.emit('[WARNING_NOTCL] %s: [%d, %d]' % (_("Edited value is out of range"), 1,
                                                                   doc.blockCount()))
            return

        if hasattr(c_editor, 'goto_line'):
            c_editor.goto_line(line + 1)
            return

        # make sure to move first the cursor at the end so after finding the line, the line will be positioned
        # at the top of the window
        c_editor.moveCursor(QTextCursor.MoveOperation.End)
        # create a Text Cursor based on the searched line: the line numbers shown are the block numbers, while
        # findBlockByLineNumber() counts each wrapped part of a long line as a line
        cursor = QTextCursor(doc.findBlockByNumber(line))
        # set cursor of the code editor with the cursor at the searched line
        c_editor.setTextCursor(cursor)

    def normalGeometry(self):
        title_bar = getattr(self, 'title_bar', None)
        if (title_bar is not None and title_bar.native_filter is not None and
                (self.isMaximized() or self.isMinimized() or self.isFullScreen())):
            geometry = title_bar.native_filter.normal_geometry
            if geometry is not None:
                # Qt otherwise subtracts the maximized native top border from the restored caption.
                return QtCore.QRect(geometry)
        return QtWidgets.QMainWindow.normalGeometry(self)

    def nativeEvent(self, event_type, message):
        title_bar = getattr(self, 'title_bar', None)
        if title_bar is not None and title_bar.native_filter is not None:
            # Qt bypasses application-wide native filters for mouse input.
            handled, result = title_bar.native_filter.nativeEventFilter(event_type, message)
            if handled:
                return handled, result
        return False, 0

    def eventFilter(self, obj, event):
        """
        Handle shared view shortcuts and filter ToolTips based on Preferences.

        :param obj:
        :param event: QT event to filter
        :return:
        """
        if (event.type() == QtCore.QEvent.Type.KeyPress and
                isinstance(obj, QtWidgets.QWidget) and (obj is self or self.isAncestorOf(obj))):
            # the keys of the 'global' context of the shortcuts (the View toggles): caught before the canvas, the
            # editor or the plugin handlers can consume them
            action = shortcuts.registry().global_action(event.key(), event.modifiers())
            if action is not None and not self.app_focus_in_text():
                if not event.isAutoRepeat():
                    action.run(self, event.key())
                event.accept()
                return True

        if _database_option(self.app, "global_toggle_tooltips") is False:
            if event.type() == QtCore.QEvent.Type.ToolTip:
                return True
            else:
                return False

        return False

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        # the same path as the files of the command line: projects open in order, the rest by file type
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        event.setDropAction(QtCore.Qt.DropAction.CopyAction)
        event.accept()
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        paths = [path for path in paths if path]
        if not paths:
            self.app.inform.emit("[WARNING_NOTCL] %s" % _("Cancelled."))
            return
        self.app.open_files_request(paths)

    def closeEvent(self, event):
        updater_active = getattr(self.app, "_check_in_progress", False)
        updater_active = updater_active or getattr(self.app, "_check_queued", False)
        is_operation_active = getattr(self.app, "_update_operation_active", None)
        if callable(is_operation_active):
            updater_active = updater_active or is_operation_active()
        if updater_active:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("An updater operation is active. Cancel it or wait for it to finish before closing FlatCAM.")
            )
            event.ignore()
            return
        if self.app.save_in_progress:
            # the app does not quit before the project file is written: it closes when the save ends
            self.app.close_after_save = True
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Saving the project. FlatCAM closes when the save ends."))
        else:
            g_rect = self.geometry()
            gui_state = getattr(self.app, '_editor_gui_state', None)
            if getattr(self.app, 'call_source', 'app') == 'app' or not isinstance(gui_state, QtCore.QByteArray):
                gui_state = self.saveState(0)

            q_settings = QSettings("Open Source", "FlatCAM_EVO")
            q_settings.setValue('saved_gui_state', gui_state)
            q_settings.setValue('toolbar_lock', self.lock_action.isChecked())
            q_settings.setValue('menu_show_text', self.show_text_action.isChecked())
            if not self.isMaximized():
                q_settings.setValue('window_geometry', (g_rect.x(), g_rect.y(), g_rect.width(), g_rect.height()))
            q_settings.setValue('splitter_left', self.splitter.sizes()[0])
            # This will write the setting to the platform specific storage.
            del q_settings
            try:
                self.final_save.emit()
            except SystemError:
                QtWidgets.QApplication.quit()
                # sys.exit(0)
        event.ignore()

    # def moveEvent(self, event):
    #     oldScreen = QtWidgets.QApplication.screenAt(event.oldPos())
    #     newScreen = QtWidgets.QApplication.screenAt(event.pos())
    #
    #     if not oldScreen == newScreen:
    #         self.screenChanged.emit(oldScreen, newScreen)
    #
    #     return super().moveEvent(event)


class ShortcutsTab(QtWidgets.QWidget):

    def __init__(self):
        super(ShortcutsTab, self).__init__()

        self.sh_tab_layout = QtWidgets.QVBoxLayout()
        self.sh_tab_layout.setContentsMargins(2, 2, 2, 2)
        self.setLayout(self.sh_tab_layout)

        self.sh_hlay = QtWidgets.QHBoxLayout()

        self.sh_title = QtWidgets.QTextEdit('<b>%s</b>' % _('Shortcut Key List'))
        self.sh_title.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        self.sh_title.setFrameStyle(QtWidgets.QFrame.Shape.NoFrame)
        self.sh_title.setMaximumHeight(30)

        font = self.sh_title.font()
        font.setPointSize(12)
        self.sh_title.setFont(font)

        self.sh_tab_layout.addWidget(self.sh_title)
        self.sh_tab_layout.addLayout(self.sh_hlay)

        # the lists are made from the registry of the shortcuts (appGUI/shortcuts.py): they show the current keys
        self.sh_app = QtWidgets.QTextEdit()
        self.sh_app.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        self.sh_app.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.sh_hlay.addWidget(self.sh_app)

        self.sh_editor = QtWidgets.QTextEdit()
        self.sh_editor.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        self.sh_editor.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.sh_hlay.addWidget(self.sh_editor)

        self.refresh()

    def refresh(self):
        """Show the current keys (after a change of the shortcuts in the Preferences)."""
        shortcut_heading_color = (
            MainGUI.theme_safe_colors['blue']
            if self.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128
            else 'blue'
        )
        self.sh_app.setText(shortcuts.f3_application_html())
        self.sh_editor.setText(shortcuts.f3_editors_html(shortcut_heading_color))

# end of file
