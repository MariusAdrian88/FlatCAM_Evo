# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtGui, QtCore
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, RadioSet, \
    FCDoubleSpinner, FCComboBox2, OptionalInputSection, FCCheckBox

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class CutoutUI:
    pluginName = _("Cutout")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)

        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        self.title_box = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(self.title_box)

        # Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        title_label.setToolTip(
            _("Create a Geometry object with toolpaths\n"
              "for cutting out the object from the surrounding material.")
        )
        self.title_box.addWidget(title_label)

        # App Level label
        self.level = QtWidgets.QToolButton()
        self.level.setToolTip(
            _(
                "Beginner Mode - many parameters are hidden.\n"
                "Advanced Mode - full control.\n"
                "Permanent change is done in 'Preferences' menu."
            )
        )
        # self.level.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        self.object_label = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.object_label.setToolTip('%s.' % _("Object to be cutout"))
        self.tools_box.addWidget(self.object_label)

        # #############################################################################################################
        # Object Frame
        # #############################################################################################################
        obj_frame = FCFrame()
        self.tools_box.addWidget(obj_frame)

        # Grid Layout
        obj_grid = GLay(v_spacing=5, h_spacing=3)
        obj_frame.setLayout(obj_grid)

        # Object kind
        self.kindlabel = FCLabel('%s:' % _('Kind'))
        self.kindlabel.setToolTip(
            _("Choice of what kind the object we want to cutout is.\n"
              "- Single: contain a single PCB Gerber outline object.\n"
              "- Panel: a panel PCB Gerber object, which is made\n"
              "out of many individual PCB outlines.")
        )
        self.obj_kind_combo = RadioSet([
            {"label": _("Single"), "value": "single"},
            {"label": _("Panel"), "value": "panel"},
        ])
        obj_grid.addWidget(self.kindlabel, 2, 0)
        obj_grid.addWidget(self.obj_kind_combo, 2, 1)

        # Type of object to be cutout
        self.type_obj_radio = RadioSet([
            {"label": _("Gerber"), "value": "grb"},
            {"label": _("Geometry"), "value": "geo"},
        ])

        self.type_obj_combo_label = FCLabel('%s:' % _("Type"))
        self.type_obj_combo_label.setToolTip(
            _("Specify the type of object to be cutout.\n"
               "It can be of type: Gerber or Geometry.\n"
               "Geometry paths are already machining centerlines and receive no radius compensation.\n"
               "Gerber outlines receive cutter-radius compensation.\n"
               "What is selected here will dictate the kind\n"
              "of objects that will populate the 'Object' combobox.")
        )

        obj_grid.addWidget(self.type_obj_combo_label, 4, 0)
        obj_grid.addWidget(self.type_obj_radio, 4, 1)

        # Object to be cutout
        self.obj_combo = FCComboBox()
        self.obj_combo.setModel(self.app.collection)
        self.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.obj_combo.is_last = False

        obj_grid.addWidget(self.obj_combo, 6, 0, 1, 2)

        self.tool_sel_label = FCLabel('%s' % _("Cutout Tool"), color='indigo', bold=True)
        self.tools_box.addWidget(self.tool_sel_label)

        # #############################################################################################################
        # Tool Frame
        # #############################################################################################################
        tool_frame = FCFrame()
        self.tools_box.addWidget(tool_frame)

        # Grid Layout
        tool_grid = GLay(v_spacing=5, h_spacing=3)
        tool_frame.setLayout(tool_grid)

        # Tool Diameter
        self.dia = FCDoubleSpinner(callback=self.confirmation_message)
        self.dia.set_precision(self.decimals)
        self.dia.set_range(-10000.0000, 10000.0000)

        self.dia_label = FCLabel('%s:' % _("Tool Dia"))
        self.dia_label.setToolTip(
            _("Diameter of the tool used to cutout\n"
              "the PCB shape out of the surrounding material.")
        )
        tool_grid.addWidget(self.dia_label, 0, 0)
        tool_grid.addWidget(self.dia, 0, 1)

        hlay = QtWidgets.QHBoxLayout()

        # Search and Add new Tool
        self.add_newtool_button = FCButton(_('Search and Add'))
        self.add_newtool_button.setIcon(QtGui.QIcon(self.app.resource_location + '/plus16.png'))
        self.add_newtool_button.setToolTip(
            _("Add a new tool to the Tool Table\n"
              "with the diameter specified above.\n"
              "This is done by a background search\n"
              "in the Tools Database. If nothing is found\n"
              "in the Tools DB then a default tool is added.")
        )
        hlay.addWidget(self.add_newtool_button)

        # Pick from DB new Tool
        self.addtool_from_db_btn = FCButton(_('Pick from DB'))
        self.addtool_from_db_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/search_db32.png'))
        self.addtool_from_db_btn.setToolTip(
            _("Add a new tool to the Tool Table\n"
              "from the Tools Database.\n"
              "Tools database administration in in:\n"
              "Menu: Options -> Tools Database")
        )
        hlay.addWidget(self.addtool_from_db_btn)

        tool_grid.addLayout(hlay, 2, 0, 1, 2)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # obj_grid.addWidget(separator_line, 18, 0, 1, 2)

        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.tools_box.addWidget(self.param_label)

        # #############################################################################################################
        # Tool Params Frame
        # #############################################################################################################
        tool_par_frame = FCFrame()
        self.tools_box.addWidget(tool_par_frame)

        # Grid Layout
        param_grid = GLay(v_spacing=5, h_spacing=3)
        tool_par_frame.setLayout(param_grid)

        # Convex Shape
        # Surrounding convex box shape
        self.convex_box_label = FCLabel('%s:' % _("Convex Shape"))
        self.convex_box_label.setToolTip(
            _("Create a convex shape surrounding the entire PCB.\n"
              "Used only if the source object type is Gerber.")
        )
        self.convex_box_cb = FCCheckBox()
        self.convex_box_cb.setToolTip(
            _("Create a convex shape surrounding the entire PCB.\n"
              "Used only if the source object type is Gerber.")
        )
        param_grid.addWidget(self.convex_box_label, 0, 0)
        param_grid.addWidget(self.convex_box_cb, 0, 1)

        # Cut Z
        cutzlabel = FCLabel('%s:' % _('Cut Z'))
        cutzlabel.setToolTip(
            _("Cutting depth (negative)\n"
              "below the copper surface.")
        )
        self.cutz_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutz_entry.set_precision(self.decimals)
        self.cutz_entry.setRange(-10000.0000, 10000.0000)

        self.cutz_entry.setSingleStep(0.1)

        param_grid.addWidget(cutzlabel, 2, 0)
        param_grid.addWidget(self.cutz_entry, 2, 1)

        # Multi-pass
        self.mpass_cb = FCCheckBox('%s:' % _("Multi-Depth"))
        self.mpass_cb.setToolTip(
            _("Use multiple passes to limit\n"
              "the cut depth in each pass. Will\n"
              "cut multiple times until Cut Z is\n"
              "reached.")
        )

        self.maxdepth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.maxdepth_entry.set_precision(self.decimals)
        self.maxdepth_entry.setRange(0, 10000.0000)
        self.maxdepth_entry.setSingleStep(0.1)

        self.maxdepth_entry.setToolTip(_("Depth of each pass (positive)."))

        param_grid.addWidget(self.mpass_cb, 4, 0)
        param_grid.addWidget(self.maxdepth_entry, 4, 1)

        self.ois_mpass_geo = OptionalInputSection(self.mpass_cb, [self.maxdepth_entry])

        # Margin
        self.margin = FCDoubleSpinner(callback=self.confirmation_message)
        self.margin.set_range(-10000.0000, 10000.0000)
        self.margin.setSingleStep(0.1)
        self.margin.set_precision(self.decimals)

        self.margin_label = FCLabel('%s:' % _("Margin"))
        self.margin_label.setToolTip(
            _("Margin over bounds. A positive value here\n"
              "will make the cutout of the PCB further from\n"
              "the actual PCB border")
        )
        param_grid.addWidget(self.margin_label, 6, 0)
        param_grid.addWidget(self.margin, 6, 1)

        # Board outline: cut the inner contours too
        self.is_outline_cb = FCCheckBox('%s' % _("Source is board outline"))
        self.is_outline_cb.setToolTip(
            _("Check only for a board outline (profile, Edge.Cuts, GKO/GM1) file.\n"
              "Every hole and every inner closed contour in it is cut as a cutout.\n"
              "Not saved: it is unchecked again when the object changes.")
        )
        param_grid.addWidget(self.is_outline_cb, 8, 0, 1, 2)

        self.inner_cb = FCCheckBox('%s' % _("Cut inside contours"))
        self.inner_cb.setToolTip(
            _("Cut the slots and the inner contours of the board outline as well as the outer edge.\n"
              "Active only when the source is a board outline and the shape is 'Any'.")
        )
        param_grid.addWidget(self.inner_cb, 10, 0, 1, 2)

        self.inner_gaps_cb = FCCheckBox('%s' % _("Gaps on inner contours"))
        self.inner_gaps_cb.setToolTip(
            _("Add the gaps, thin gaps and mouse bites also to the inner contours.\n"
              "Off: the inner contours are cut without gaps.")
        )
        param_grid.addWidget(self.inner_gaps_cb, 12, 0, 1, 2)

        self.gaps_label = FCLabel('%s' % _("Gaps"), color='green', bold=True)
        self.tools_box.addWidget(self.gaps_label)

        # #############################################################################################################
        # Gaps Frame
        # #############################################################################################################
        gaps_frame = FCFrame()
        self.tools_box.addWidget(gaps_frame)

        # Grid Layout
        gaps_grid = GLay(v_spacing=5, h_spacing=3)
        gaps_frame.setLayout(gaps_grid)

        # Gapsize
        self.gapsize_label = FCLabel('%s:' % _("Size"))
        self.gapsize_label.setToolTip(
            _("The size of the bridge gaps in the cutout\n"
              "is the remaining bridge width on straight sections, measured along contour.\n"
              "used to keep the board connected to\n"
              "the surrounding material (the one \n"
              "from which the PCB is cutout).")
        )

        self.gapsize = FCDoubleSpinner(callback=self.confirmation_message)
        self.gapsize.setRange(0.0000, 10000.0000)
        self.gapsize.set_precision(self.decimals)

        gaps_grid.addWidget(self.gapsize_label, 2, 0)
        gaps_grid.addWidget(self.gapsize, 2, 1)

        # Gap Type
        self.gaptype_label = FCLabel('%s:' % _("Type"))
        self.gaptype_label.setToolTip(
            _("The type of gap:\n"
              "- Bridge -> the cutout will be interrupted by bridges\n"
              "- Thin -> same as 'bridge' but it will be thinner by partially milling the gap\n"
              "- M-Bites -> 'Mouse Bites' - same as 'bridge' but covered with drill holes")
        )

        # self.gaptype_combo = RadioSet(
        #     [
        #         {'label': _('Bridge'), 'value': 'b'},
        #         {'label': _('Thin'), 'value': 'bt'},
        #         {'label': "M-Bites", 'value': 'mb'}
        #     ],
        #     compact=True
        # )
        self.gaptype_combo = FCComboBox2()
        self.gaptype_combo.addItems([_('Bridge'), _('Thin'), _("Mouse Bytes")])

        gaps_grid.addWidget(self.gaptype_label, 4, 0)
        gaps_grid.addWidget(self.gaptype_combo, 4, 1)

        # Thin gaps Depth
        self.thin_depth_label = FCLabel('%s:' % _("Depth"))
        self.thin_depth_label.setToolTip(
            _("Thin Depth is a negative Z depth, not remaining material thickness.\n"
              "Use Cut Z < Thin Depth < 0. Zero leaves a full-thickness bridge and creates no thin-gap tool.")
        )
        self.thin_depth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.thin_depth_entry.set_precision(self.decimals)
        self.thin_depth_entry.setRange(-10000.0000, 10000.0000)
        self.thin_depth_entry.setSingleStep(0.1)

        gaps_grid.addWidget(self.thin_depth_label, 6, 0)
        gaps_grid.addWidget(self.thin_depth_entry, 6, 1)

        # Mouse Bites Tool Diameter
        self.mb_dia_label = FCLabel('%s:' % _("Tool Dia"))
        self.mb_dia_label.setToolTip(
            _("The drill hole diameter when doing mouse bites.")
        )
        self.mb_dia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.mb_dia_entry.set_precision(self.decimals)
        self.mb_dia_entry.setRange(0, 10000.0000)

        gaps_grid.addWidget(self.mb_dia_label, 8, 0)
        gaps_grid.addWidget(self.mb_dia_entry, 8, 1)

        # Mouse Bites Holes Spacing
        self.mb_spacing_label = FCLabel('%s:' % _("Spacing"))
        self.mb_spacing_label.setToolTip(
            _("Edge-to-edge spacing between mouse-bite holes.\n"
              "Center pitch equals drill diameter plus this spacing.")
        )
        self.mb_spacing_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.mb_spacing_entry.set_precision(self.decimals)
        self.mb_spacing_entry.setRange(0, 10000.0000)

        gaps_grid.addWidget(self.mb_spacing_label, 10, 0)
        gaps_grid.addWidget(self.mb_spacing_entry, 10, 1)

        self.separator_line = QtWidgets.QFrame()
        self.separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self.separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        gaps_grid.addWidget(self.separator_line, 12, 0, 1, 2)

        # ##############################################################################################################
        # ######################################## Type of CUTOUT ######################################################
        # ##############################################################################################################
        self.cutout_type_label = FCLabel('%s:' % _("Bridge"))
        self.cutout_type_label.setToolTip(
            _("Selection of the type of cutout.")
        )

        self.cutout_type_radio = RadioSet([
            {"label": _("Automatic"), "value": "a"},
            {"label": _("Manual"), "value": "m"},
        ])

        gaps_grid.addWidget(self.cutout_type_label, 14, 0)
        gaps_grid.addWidget(self.cutout_type_radio, 14, 1)

        # Gaps
        # How gaps wil be rendered:
        # lr    - left + right
        # tb    - top + bottom
        # 4     - left + right +top + bottom
        # 2lr   - 2*left + 2*right
        # 2tb   - 2*top + 2*bottom
        # 8     - 2*left + 2*right +2*top + 2*bottom
        self.gaps_label = FCLabel('%s:' % _('Gaps'))
        self.gaps_label.setToolTip(
            _("Number of gaps used for the Automatic cutout.\n"
              "There can be maximum 8 bridges/gaps.\n"
              "LR/TB use left-right or top-bottom center anchors; 2LR/2TB use quarter anchors.\n"
              "4 means two LR plus two TB gaps; 8 means four LR plus four TB gaps.\n"
              "Each contour must fit the exact count and width or the operation fails.\n"
              "The choices are:\n"
              "- None  - no gaps\n"
              "- lr    - left + right\n"
              "- tb    - top + bottom\n"
              "- 4     - left + right +top + bottom\n"
              "- 2lr   - 2*left + 2*right\n"
              "- 2tb  - 2*top + 2*bottom\n"
              "- 8     - 2*left + 2*right +2*top + 2*bottom")
        )
        # gaps_label.setMinimumWidth(60)

        self.gaps = FCComboBox()
        gaps_items = ['None', 'LR', 'TB', '4', '2LR', '2TB', '8']
        for it in gaps_items:
            self.gaps.addItem(it)
            # self.gaps.setStyleSheet('background-color: rgb(255,255,255)')
        gaps_grid.addWidget(self.gaps_label, 16, 0)
        gaps_grid.addWidget(self.gaps, 16, 1)

        # Type of generated cutout: Rectangular or Any Form
        self.cutout_shape_label = FCLabel('%s:' % _("Shape"))
        self.cutout_shape_label.setToolTip(
            _("Checked: the cutout shape is rectangular.\n"
              "Unchecked: any-form cutout shape.")
        )
        self.cutout_shape_cb = FCCheckBox('%s' % _("Any"))

        gaps_grid.addWidget(self.cutout_shape_label, 18, 0)
        gaps_grid.addWidget(self.cutout_shape_cb, 18, 1)

        # #############################################################################################################
        # Manual Gaps Frame
        # #############################################################################################################
        self.man_frame = QtWidgets.QFrame()
        self.man_frame.setContentsMargins(0, 0, 0, 0)
        gaps_grid.addWidget(self.man_frame, 20, 0, 1, 2)

        man_grid = GLay(v_spacing=5, h_spacing=3)
        man_grid.setContentsMargins(0, 0, 0, 0)
        self.man_frame.setLayout(man_grid)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        man_grid.addWidget(separator_line, 0, 0, 1, 2)

        # Big Cursor
        self.big_cursor_label = FCLabel('%s:' % _("Big cursor"))
        self.big_cursor_label.setToolTip(
            _("Use a big cursor when adding manual gaps."))
        self.big_cursor_cb = FCCheckBox()

        man_grid.addWidget(self.big_cursor_label, 2, 0)
        man_grid.addWidget(self.big_cursor_cb, 2, 1)

        # Manual Geo Object
        self.man_object_combo = FCComboBox()
        self.man_object_combo.setModel(self.app.collection)
        self.man_object_combo.setRootModelIndex(self.app.collection.index(2, 0, QtCore.QModelIndex()))
        self.man_object_combo.is_last = True
        self.man_object_combo.obj_type = "Geometry"

        self.man_object_label = FCLabel('%s:' % _("Manual cutout Geometry"))
        self.man_object_label.setToolTip(
            _("Geometry object used to create the manual cutout.")
        )
        # self.man_object_label.setMinimumWidth(60)

        man_grid.addWidget(self.man_object_label, 4, 0, 1, 2)
        man_grid.addWidget(self.man_object_combo, 6, 0, 1, 2)

        # #############################################################################################################
        # Buttons
        # #############################################################################################################

        man_hlay = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(man_hlay)

        # Generate a surrounding Geometry object Button
        self.man_geo_creation_btn = FCButton(_("Manual Geometry"))
        self.man_geo_creation_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/rectangle32.png'))
        self.man_geo_creation_btn.setToolTip(
            _("Generate a Geometry to be used as cutout.")
        )
        # self.man_geo_creation_btn.setStyleSheet("""
        #                         QPushButton
        #                         {
        #                             font-weight: bold;
        #                         }
        #                         """)

        # Manual Add of Gaps Button
        self.man_gaps_creation_btn = QtWidgets.QToolButton()
        self.man_gaps_creation_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.man_gaps_creation_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/plus32.png'))
        self.man_gaps_creation_btn.setText(_("Gaps"))
        self.man_gaps_creation_btn.setToolTip(
            _("Left-click a contour on the selected Geometry object to add a bridge.\n"
              "Click a bridge placed in this session to select it. Drag its orange grip\n"
              "to preview a move and release to apply. You can also click a destination\n"
              "or press Delete to remove it. Right-click keeps the current bridges and finishes.\n"
              "Escape restores the object to its state before this session.\n"
              "Bridges from completed sessions cannot be selected or edited.")
        )
        man_hlay.addWidget(self.man_geo_creation_btn)
        man_hlay.addWidget(self.man_gaps_creation_btn)

        # Generate Geometry Button
        self.generate_cutout_btn = FCButton(_("Generate Geometry"), bold=True)
        self.generate_cutout_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/irregular32.png'))
        self.generate_cutout_btn.setToolTip(
            _("Generate the cutout geometry.")
        )
        self.tools_box.addWidget(self.generate_cutout_btn)

        # self.tool_param_separator_line = QtWidgets.QFrame()
        # self.tool_param_separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # self.tool_param_separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # obj_grid.addWidget(self.tool_param_separator_line, 60, 0, 1, 2)

        # obj_grid.addWidget(FCLabel(""), 62, 0, 1, 2)

        # Cut by Drilling Title
        self.title_drillcut_label = FCLabel('%s' % _("Cut by Drilling"), color='red', bold=True)
        self.title_drillcut_label.setToolTip(_("Create a series of drill holes following a geometry line."))
        self.tools_box.addWidget(self.title_drillcut_label)

        # #############################################################################################################
        # Cut by Drilling Frame
        # #############################################################################################################
        self.drill_cut_frame = FCFrame()
        self.tools_box.addWidget(self.drill_cut_frame)

        # Grid Layout
        drill_cut_grid = GLay(v_spacing=5, h_spacing=3)
        self.drill_cut_frame.setLayout(drill_cut_grid)

        # Drilling Geo Object Label
        self.drillcut_object_lbl = FCLabel('%s:' % _("Geometry"))
        self.drillcut_object_lbl.setToolTip(
            _("Geometry object used to create the manual cutout.")
        )

        drill_cut_grid.addWidget(self.drillcut_object_lbl, 0, 0, 1, 2)

        # Drilling Geo Object
        self.drillcut_object_combo = FCComboBox()
        self.drillcut_object_combo.setModel(self.app.collection)
        self.drillcut_object_combo.setRootModelIndex(self.app.collection.index(2, 0, QtCore.QModelIndex()))
        self.drillcut_object_combo.is_last = False
        self.drillcut_object_combo.obj_type = "Geometry"

        drill_cut_grid.addWidget(self.drillcut_object_combo, 2, 0, 1, 2)

        # Drill Tool Diameter
        self.drill_dia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_dia_entry.set_precision(self.decimals)
        self.drill_dia_entry.set_range(0.0000, 10000.0000)

        self.drill_dia_label = FCLabel('%s:' % _("Drill Dia"))
        self.drill_dia_label.setToolTip(
            _("Diameter of the tool used to cutout\n"
              "the PCB by drilling.")
        )
        drill_cut_grid.addWidget(self.drill_dia_label, 4, 0)
        drill_cut_grid.addWidget(self.drill_dia_entry, 4, 1)

        # Drill Tool Pitch
        self.drill_pitch_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_pitch_entry.set_precision(self.decimals)
        self.drill_pitch_entry.set_range(0.0000, 10000.0000)

        self.drill_pitch_label = FCLabel('%s:' % _("Pitch"))
        self.drill_pitch_label.setToolTip(
            _("Distance between the center of\n"
              "two neighboring drill holes.")
        )
        drill_cut_grid.addWidget(self.drill_pitch_label, 6, 0)
        drill_cut_grid.addWidget(self.drill_pitch_entry, 6, 1)

        # Drill Tool Margin
        self.drill_margin_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_margin_entry.set_precision(self.decimals)
        self.drill_margin_entry.set_range(0.0000, 10000.0000)

        self.drill_margin_label = FCLabel('%s:' % _("Margin"))
        self.drill_margin_label.setToolTip(
            _("Margin over bounds. A positive value here\n"
              "will make the cutout of the PCB further from\n"
              "the actual PCB border")
        )
        drill_cut_grid.addWidget(self.drill_margin_label, 8, 0)
        drill_cut_grid.addWidget(self.drill_margin_entry, 8, 1)

        GLay.set_common_column_size([obj_grid, tool_grid, param_grid, man_grid, drill_cut_grid, gaps_grid], 0)

        # Drill Cut Button
        self.drillcut_btn = FCButton(_("Cut by Drilling"), bold=True)
        self.drillcut_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/drill16.png'))
        self.drillcut_btn.setToolTip(
            _("Create a series of drill holes following a geometry line.")
        )
        self.tools_box.addWidget(self.drillcut_btn)

        self.layout.addStretch()

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.layout.addWidget(self.reset_button)

        self.gaptype_combo.currentIndexChanged.connect(self.on_gap_type_radio)

        # ############################ FINSIHED GUI ###################################
        # #############################################################################

    def update_inner_state(self):
        outline = bool(self.is_outline_cb.get_value())
        any_shape = not self.cutout_shape_cb.get_value()
        self.inner_cb.setEnabled(outline and any_shape)
        # Manual bridges are placed by clicking a path, not by the automatic gap pattern.
        self.inner_gaps_cb.setEnabled(outline and any_shape and bool(self.inner_cb.get_value()) and
                                      self.cutout_type_radio.get_value() == 'a')

    def on_cutout_type(self, val):
        if val == 'a':
            self.gaps_label.show()
            self.gaps.show()
            self.generate_cutout_btn.show()
            self.man_geo_creation_btn.hide()
            self.man_gaps_creation_btn.hide()
            self.man_frame.hide()
        else:
            self.gaps_label.hide()
            self.gaps.hide()
            self.generate_cutout_btn.hide()
            self.man_geo_creation_btn.show()
            self.man_gaps_creation_btn.show()
            self.man_frame.show()
        self.update_inner_state()

    def on_cutout_shape_changed(self, state):
        if state:
            self.generate_cutout_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/rectangle32.png'))
            self.man_geo_creation_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/rectangle32.png'))
            self.cutout_shape_cb.setText('%s' % _("Rectangular"))
        else:
            self.generate_cutout_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/irregular32.png'))
            self.man_geo_creation_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/irregular32.png'))
            self.cutout_shape_cb.setText('%s' % _("Any"))

    def on_level_changed(self, checked):
        if not checked:
            self.level.setText('%s' % _('Beginner'))
            self.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: green;
                                        }
                                        """)
        else:
            self.level.setText('%s' % _('Advanced'))
            self.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)
        for widget in (self.convex_box_label, self.convex_box_cb, self.add_newtool_button,
                       self.addtool_from_db_btn, self.gaptype_label, self.gaptype_combo,
                       self.cutout_type_label, self.cutout_type_radio, self.separator_line,
                       self.drill_cut_frame, self.title_drillcut_label, self.drillcut_btn):
            widget.setVisible(bool(checked))
        self.cutout_type_radio.set_value('a')

    def on_gap_type_radio(self, index):
        if index == 0:    # Normal gap
            self.thin_depth_label.hide()
            self.thin_depth_entry.hide()
            self.mb_dia_label.hide()
            self.mb_dia_entry.hide()
            self.mb_spacing_label.hide()
            self.mb_spacing_entry.hide()
        elif index == 1:  # "Thin gaps"
            self.thin_depth_label.show()
            self.thin_depth_entry.show()
            self.mb_dia_label.hide()
            self.mb_dia_entry.hide()
            self.mb_spacing_label.hide()
            self.mb_spacing_entry.hide()
        elif index == 2:  # "Mouse Bytes"
            self.thin_depth_label.hide()
            self.thin_depth_entry.hide()
            self.mb_dia_label.show()
            self.mb_dia_entry.show()
            self.mb_spacing_label.show()
            self.mb_spacing_entry.show()

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
