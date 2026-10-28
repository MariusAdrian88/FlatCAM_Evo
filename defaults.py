import os
import re
import stat
import sys
import tempfile
from copy import deepcopy
from appCommon.Common import LoudDict
from camlib import to_dict, Geometry
import json
import logging
import gettext
import appTranslation as fcTranslate
import builtins

from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber

from services.updater.digi.transport import DEFAULT_UPDATE_URL as LEGACY_DIGI_UPDATE_URL
from services.updater.common import TransportError
from services.updater.github.transport import DEFAULT_REPOSITORY as DEFAULT_UPDATE_URL, normalize_repository

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext
# log = logging.getLogger('AppDefaults')
log = logging.getLogger('base')
_MISSING = object()


# Keys outside the "tools_" family that belong to the project.
PROJECT_KEYS = frozenset((
    'units',
    'gerber_noncoppermargin', 'gerber_noncopperrounded', 'gerber_bboxmargin', 'gerber_bboxrounded',
    'gerber_follow',
    'excellon_drill_tooldia', 'excellon_slot_tooldia',
    'geometry_seg_x', 'geometry_seg_y',
))
# "tools_" keys that belong to the application.
APP_TOOL_PREFIXES = ('tools_grbl_', 'tools_calc_')
APP_TOOL_SUFFIXES = ('_plotting', '_big_cursor', '_preprocessor_list')
APP_TOOL_KEYS = frozenset((
    'tools_al_controller', 'tools_gcode_fit_view', 'tools_dist_snap_center',
    'tools_opt_precision', 'tools_qrcode_sel_limit', 'tools_qrcode_min_module',
))
# Application keys that Import Preferences (File -> Backup) does not take from the file: the source of the updates. A
# received preferences file must not point Check for Updates and the updater to another repository or share.
UPDATE_SOURCE_KEYS = frozenset(('global_update_provider', 'global_update_url', 'global_legacy_update_url'))


def is_project_key(key):
    """Return True if the key is a project option, False if it is an application setting."""
    if key in PROJECT_KEYS:
        return True
    if not isinstance(key, str) or not key.startswith('tools_'):
        return False
    if key.startswith(APP_TOOL_PREFIXES) or key.endswith(APP_TOOL_SUFFIXES):
        return False
    return key not in APP_TOOL_KEYS


def project_options(options):
    """Return a dict with only the project keys of "options", for the project file."""
    return {k: v for k, v in options.items() if is_project_key(k)}


def normalize_units(value):
    """Return 'MM' or 'IN' for a units value, or None if the value is not a valid unit."""
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    if value in ('MM', 'METRIC'):
        return 'MM'
    if value in ('IN', 'INCH', 'INCHES'):
        return 'IN'
    return None


_LENGTH_KEY_TAIL = re.compile(
    r'(tooldia|_dia|margin|cutz|travelz|toolchangez|endz|startz|depth|feedrate\w*|offset)$')


def convert_length_options(options, from_units, to_units):
    """
    Return the entries of "options" that hold a length (a diameter, a margin, a depth, a feed rate), converted from
    "from_units" to "to_units". The values of the project file are in the units of the project and need no conversion:
    this is for the values that come from the application defaults, which are in the units of the defaults.
    """
    from_units, to_units = normalize_units(from_units), normalize_units(to_units)
    if from_units is None or to_units is None or from_units == to_units:
        return {}
    factor = 25.4 if to_units == 'MM' else 1 / 25.4
    return {k: v * factor for k, v in options.items()
            if isinstance(k, str) and _LENGTH_KEY_TAIL.search(k) and isinstance(v, (int, float))
            and not isinstance(v, bool)}


def clean_project_options(options):
    """
    Return the project keys of the options of a project file, ready to be applied: the application keys are left out
    and the units are 'MM' or 'IN'. A units value that is not valid is left out, so the default stays.
    """
    clean = {}
    for key, value in options.items():
        if not is_project_key(key):
            continue
        if key == 'units':
            value = normalize_units(value)
            if value is None:
                continue
        clean[key] = value
    return clean


class AppDefaults:

    factory_defaults = {
        # Global
        "first_run": True,
        "root_folder_path": '',

        "global_serial": 0,
        "global_stats": dict(),
        "global_tabs_detachable": False,

        "global_coords_bar_show": True,
        "global_delta_coords_bar_show": False,
        "global_statusbar_show": True,

        "global_jump_ref": 'abs',
        "global_locate_pt": 'bl',

        "global_move_ref": 'abs',

        "global_background_timeout": 300000,  # Default value is 5 minutes
        "global_verbose_error_level": 0,  # Shell verbosity 0 = default
        # (python trace only for unknown errors),
        # 1 = show trace(show trace always),
        # 2 = (For the future).

        "global_grid_context_menu": {
            'in': [0.01, 0.02, 0.025, 0.05, 0.1],
            'mm': [0.1, 0.2, 0.5, 1, 2.54]
        },

        # Persistence
        "global_last_folder": None,
        "global_last_save_folder": None,

        # Constants...
        "global_defaults_save_period_ms": 20000,  # Time between default saves.
        "global_shell_shape": [500, 300],  # Shape of the shell in pixels.
        "global_recent_limit": 10,  # Max. items in recent list.

        "fit_key": 'V',
        "zoom_out_key": '-',
        "zoom_in_key": '=',
        "grid_toggle_key": 'G',
        "global_zoom_ratio": 1.5,
        "global_point_clipboard_format": "(%.*f, %.*f)",

        "global_tcl_path": '',

        # General APP Preferences
        "units": "MM",
        "units_precision": 4,
        "global_graphic_engine_3d_no_mp": False,
        "global_backface_culling": True,
        "global_app_level": 'b',

        "global_log_verbose": 2,
        "global_portable": False,

        "global_languages": ['English'],
        "global_language_current": 'English',

        "global_systray_icon": True,
        "global_shell_at_startup": False,  # Show the shell at startup.
        "global_project_at_startup": False,
        "global_project_caching": False,
        "global_version_check": True,
        "global_update_url": DEFAULT_UPDATE_URL,
        "global_update_provider": "github",  # "github" or "digi" (legacy)
        "global_legacy_update_url": LEGACY_DIGI_UPDATE_URL,
        "global_send_stats": True,
        "global_worker_number": min(4, max(2, int((os.cpu_count()) / 2))) if os.cpu_count() else 2,
        "global_process_number": int((os.cpu_count()) / 4) if os.cpu_count() > 4 else 1,
        "global_tolerance": 0.005,

        "global_save_compressed": True,
        "global_compression_level": 3,

        "global_tpdf_tmargin": 15.0,
        "global_tpdf_bmargin": 10.0,
        "global_tpdf_lmargin": 20.0,
        "global_tpdf_rmargin": 20.0,

        "pdf_python_parser": True,

        # General GUI Preferences
        "global_appearance": 'light',
        "global_styling": 'default',
        "global_dark_canvas": False,
        "global_dim_text": True,
        "global_theme": 'light',
        # the custom title bar is on by default only on Windows; macOS and Linux keep the native window decorations
        "global_custom_titlebar": sys.platform == "win32",
        # the custom title bar collapses its menu into a hamburger button; when False the menu bar is always shown
        "global_hamburger_menu": True,

        "global_layout": "compact",
        "global_hover_shape": False,
        "global_selection_shape": True,
        "global_selection_shape_as_line": False,
        "global_joined_redraws": True,

        "global_sel_fill": '#a5a5ffbf',
        "global_sel_line": '#0000ffbf',
        "global_alt_sel_fill": '#BBF268BF',
        "global_alt_sel_line": '#006E20BF',
        "global_draw_color": '#00000080',
        "global_sel_draw_color": '#0000FF80',

        # Project Items colors
        "global_proj_item_color_light": '#000000FF',
        "global_proj_item_dis_color_light": '#b7b7cbFF',
        "global_proj_item_color_dark": '#AAAAAAFF',
        "global_proj_item_dis_color_dark": '#4a4a4aFF',

        # UI icon colors (Project Tab icons and status indicators are separate)
        "global_icon_color_light": '#4d4d4d',
        "global_icon_color_dark": '#9AA0A6',

        "global_project_autohide": True,
        "global_object_autohide": True,
        "global_object_autoswitch": True,

        # General App Settings
        "global_grid_bar_show": True,
        "global_gridx": 1.0,
        "global_gridy": 1.0,
        "global_snap_max": 0.05,

        "global_workspace": False,
        "global_workspaceT": "A4",
        "global_workspace_orientation": 'p',
        "global_axis": True,
        "global_axis_color": '#AA0000FF',
        "global_axis_width": 1,
        "global_hud": False,
        "global_hud_position": 'TL',
        "global_hud_text_color_enabled": False,
        "global_hud_text_color": '#FFFFFFFF',
        "global_hud_bg_color": '#80808040',
        "global_hud_offset": 10,
        "global_grid_lines": True,
        "global_grid_snap": True,

        "global_cursor_type": "small",
        "global_cursor_size": 20,
        "global_cursor_width": 1,
        "global_cursor_color": '#0055FFFF',
        "global_cursor_color_enabled": True,

        "global_pan_button": '2',
        "global_mselect_key": 'Control',

        "global_delete_confirmation": True,
        "global_history_steps": 100,
        "global_history_memory_mb": 256,
        "global_allow_edit_in_project_tab": False,
        "global_open_style": True,
        "global_toggle_tooltips": True,
        "global_bookmarks": dict(),
        "global_bookmarks_limit": 10,
        "global_activity_icon": 'Ball green',

        # Gerber General
        "gerber_plot": True,
        "gerber_solid": True,
        "gerber_multicolored": False,
        "gerber_color_list": [],
        "gerber_store_color_list": True,

        "gerber_circle_steps": 64,
        # 'adaptive': the segments of each circle follow its radius and "gerber_circle_tolerance" (micrometers);
        # 'fixed': every circle has "gerber_circle_steps" segments
        "gerber_circle_steps_mode": 'adaptive',
        "gerber_circle_tolerance": 2.0,
        "gerber_use_buffer_for_union": True,
        "gerber_clean_apertures": True,
        "gerber_extra_buffering": False,
        "gerber_plot_on_select": True,

        "gerber_plot_fill": '#BBF268BF',
        "gerber_plot_line_enable": True,
        "gerber_plot_line": '#006E20BF',

        "gerber_def_units": 'IN',
        "gerber_def_zeros": 'L',
        "gerber_save_filters": "Gerber File .gbr (*.gbr);;Gerber File .bot (*.bot);;Gerber File .bsm (*.bsm);;"
                               "Gerber File .cmp (*.cmp);;Gerber File .crc (*.crc);;Gerber File .crs (*.crs);;"
                               "Gerber File .gb0 (*.gb0);;Gerber File .gb1 (*.gb1);;Gerber File .gb2 (*.gb2);;"
                               "Gerber File .gb3 (*.gb3);;Gerber File .gb4 (*.gb4);;Gerber File .gb5 (*.gb5);;"
                               "Gerber File .gb6 (*.gb6);;Gerber File .gb7 (*.gb7);;Gerber File .gb8 (*.gb8);;"
                               "Gerber File .gb9 (*.gb9);;Gerber File .gbd (*.gbd);;Gerber File .gbl (*.gbl);;"
                               "Gerber File .gbo (*.gbo);;Gerber File .gbp (*.gbp);;Gerber File .gbs (*.gbs);;"
                               "Gerber File .gdl (*.gdl);;Gerber File .gdo (*.gdo);;Gerber File .ger (*.ger);;"
                               "Gerber File .gko (*.gko);;Gerber File .gm1 (*.gm1);;Gerber File .gm2 (*.gm2);;"
                               "Gerber File .gm3 (*.gm3);;Gerber File .gme (*.gme);;Gerber File .grb (*.grb);;"
                               "Gerber File .gtl (*.gtl);;Gerber File .gto (*.gto);;Gerber File .gtp (*.gtp);;"
                               "Gerber File .gts (*.gts);;Gerber File .ly15 (*.ly15);;Gerber File .ly2 (*.ly2);;"
                               "Gerber File .mil (*.mil);;"
                               "Gerber File .outline (*.outline);;Gerber File .pho (*.pho);;"
                               "Gerber File .plc (*.plc);;Gerber File .pls (*.pls);;Gerber File .smb (*.smb);;"
                               "Gerber File .smt (*.smt);;Gerber File .sol (*.sol);;Gerber File .spb (*.spb);;"
                               "Gerber File .spt (*.spt);;Gerber File .ssb (*.ssb);;Gerber File .sst (*.sst);;"
                               "Gerber File .stc (*.stc);;Gerber File .sts (*.sts);;Gerber File .top (*.top);;"
                               "Gerber File .tsm (*.tsm);;Gerber File .art (*.art);;"
                               "All Files (*.*)",

        # Gerber Options
        "gerber_noncoppermargin": 0.1,
        "gerber_noncopperrounded": False,
        "gerber_bboxmargin": 0.1,
        "gerber_bboxrounded": False,

        # Gerber Advanced Options
        "gerber_aperture_display": False,
        "gerber_aperture_scale_factor": 1.0,
        "gerber_aperture_buffer_factor": 0.0,
        "gerber_follow": False,
        "gerber_buffering": "full",
        "gerber_delayed_buffering": True,
        "gerber_simplification": False,
        "gerber_simp_tolerance": 0.0005,

        # Gerber Export
        "gerber_exp_units": 'IN',
        "gerber_exp_integer": 2,
        "gerber_exp_decimals": 4,
        "gerber_exp_zeros": 'L',

        # Gerber Editor
        "gerber_editor_sel_limit": 30,
        "gerber_editor_newcode": 10,
        "gerber_editor_newsize": 0.8,
        "gerber_editor_newtype": 'C',
        "gerber_editor_newdim": "0.5, 0.5",
        # 0 = Regions, 1 = Apertures: how the editor loads an object with clear features
        "gerber_editor_load": 0,
        "gerber_editor_array_size": 5,
        "gerber_editor_lin_dir": 'X',
        "gerber_editor_lin_pitch": 0.1,
        "gerber_editor_lin_angle": 0.0,
        "gerber_editor_circ_dir": 'CW',
        "gerber_editor_circ_angle": 0.0,
        "gerber_editor_scale_f": 1.0,
        "gerber_editor_buff_f": 0.1,
        "gerber_editor_ma_low": 0.0,
        "gerber_editor_ma_high": 1.0,

        # Excellon General
        "excellon_plot": True,
        "excellon_circle_steps": 32,
        # 'adaptive' or 'fixed', as "gerber_circle_steps_mode"
        "excellon_circle_steps_mode": 'adaptive',
        "excellon_circle_tolerance": 5.0,
        "excellon_solid": True,
        "excellon_multicolored": False,
        "excellon_color": None,
        "excellon_merge_fuse_tools": True,
        "excellon_format_upper_in": 2,
        "excellon_format_lower_in": 4,
        "excellon_format_upper_mm": 3,
        "excellon_format_lower_mm": 3,
        "excellon_zeros": "T",
        "excellon_units": "INCH",
        "excellon_update": True,

        "excellon_optimization_type": 'B',

        "excellon_search_time": 3,
        "excellon_save_filters": "Excellon File .txt (*.txt);;Excellon File .drd (*.drd);;"
                                 "Excellon File .drill (*.drill);;"
                                 "Excellon File .drl (*.drl);;Excellon File .exc (*.exc);;"
                                 "Excellon File .ncd (*.ncd);;Excellon File .tap (*.tap);;"
                                 "Excellon File .xln (*.xln);;All Files (*.*)",
        "excellon_plot_fill": '#C40000BF',
        "excellon_plot_line": '#750000BF',

        # Excellon Options
        "excellon_drill_tooldia": 0.8,
        "excellon_slot_tooldia": 1.8,

        # Excellon Advanced options
        "excellon_tools_table_display": True,
        "excellon_autoload_db": False,

        # Excellon Export
        "excellon_exp_units": 'INCH',
        "excellon_exp_format": 'dec',
        "excellon_exp_integer": 2,
        "excellon_exp_decimals": 4,
        "excellon_exp_zeros": 'LZ',
        "excellon_exp_slot_type": 'routing',

        # Excellon Editor
        "excellon_editor_sel_limit": 30,
        "excellon_editor_newdia": 1.0,
        "excellon_editor_array_size": 5,
        "excellon_editor_lin_dir": 'X',
        "excellon_editor_lin_pitch": 2.54,
        "excellon_editor_lin_angle": 0.0,
        "excellon_editor_circ_dir": 'CW',
        "excellon_editor_circ_angle": 12,
        # Excellon Slots
        "excellon_editor_slot_direction": 'X',
        "excellon_editor_slot_angle": 0.0,
        "excellon_editor_slot_length": 5.0,
        # Excellon Slot Array
        "excellon_editor_slot_array_size": 5,
        "excellon_editor_slot_lin_dir": 'X',
        "excellon_editor_slot_lin_pitch": 2.54,
        "excellon_editor_slot_lin_angle": 0.0,
        "excellon_editor_slot_circ_dir": 'CW',
        "excellon_editor_slot_circ_angle": 12,

        # Geometry General
        "geometry_plot": True,
        "geometry_multicolored": False,
        "geometry_circle_steps": 32,
        # 'adaptive' or 'fixed', as "gerber_circle_steps_mode"
        "geometry_circle_steps_mode": 'adaptive',
        "geometry_circle_tolerance": 5.0,
        "geometry_merge_fuse_tools": True,
        "geometry_plot_line": "#FF0000",

        # Geometry Options
        "geometry_seg_x": 0.0,
        "geometry_seg_y": 0.0,

        # Geometry Export
        "geometry_dxf_format": 'R2010',
        "geometry_paths_only": True,

        # Geometry Editor
        "geometry_editor_sel_limit": 30,
        "geometry_editor_milling_type": "cl",
        "geometry_editor_parameters": False,

        # CNC Job General
        "cncjob_plot": True,
        "cncjob_tooldia": 0.125,
        "cncjob_coords_type": "G90",
        "cncjob_coords_decimals": 4,
        "cncjob_fr_decimals": 2,

        # bed square compensation
        "cncjob_bed_max_x": 300,
        "cncjob_bed_max_y": 400,
        "cncjob_bed_offset_x": 0,
        "cncjob_bed_offset_y": 0,
        "cncjob_bed_skew_x": 0,
        "cncjob_bed_skew_y": 0,

        "cncjob_steps_per_circle": 16,
        "cncjob_footer": False,
        "cncjob_line_ending": False,
        "cncjob_save_filters": "G-Code Files .nc (*.nc);;G-Code Files .din (*.din);;G-Code Files .dnc (*.dnc);;"
                               "G-Code Files .ecs (*.ecs);;G-Code Files .eia (*.eia);;G-Code Files .fan (*.fan);;"
                               "G-Code Files .fgc (*.fgc);;G-Code Files .fnc (*.fnc);;G-Code Files . gc (*.gc);;"
                               "G-Code Files .gcd (*.gcd);;G-Code Files .gcode (*.gcode);;G-Code Files .h (*.h);;"
                               "G-Code Files .hnc (*.hnc);;G-Code Files .i (*.i);;G-Code Files .min (*.min);;"
                               "G-Code Files .mpf (*.mpf);;G-Code Files .mpr (*.mpr);;G-Code Files .cnc (*.cnc);;"
                               "G-Code Files .ncc (*.ncc);;G-Code Files .ncg (*.ncg);;G-Code Files .ncp (*.ncp);;"
                               "G-Code Files .ngc (*.ngc);;G-Code Files .out (*.out);;G-Code Files .ply (*.ply);;"
                               "G-Code Files .sbp (*.sbp);;G-Code Files .tap (*.tap);;G-Code Files .xpi (*.xpi);;"
                               "All Files (*.*)",
        "cncjob_plot_line": '#4650BDFF',
        "cncjob_plot_fill": '#5E6CFFFF',
        "cncjob_travel_line": '#B5AB3A4C',
        "cncjob_travel_fill": '#F0E24D4C',

        # CNC Job Options
        "cncjob_plot_kind": 'all',
        "cncjob_annotation": True,

        # CNC Job Advanced Options
        "cncjob_annotation_fontsize": 9,
        "cncjob_annotation_fontcolor": '#990000',

        # CNC Job (GCode) Editor
        "cncjob_prepend": "",
        "cncjob_append": "",

        # Follow Tool
        "tools_follow_simplification": False,
        "tools_follow_tolerance": 0.01,
        "tools_follow_union": False,
        "tools_follow_merge": True,
        "tools_follow_select": 'all',
        "tools_follow_area_shape": 'square',
        "tools_follow_dia_mode": 'default',
        "tools_follow_suffix": '_follow',

        # Isolation Routing Plugin
        "tools_iso_tooldia": "0.1",
        "tools_iso_order": 2,   # Reverse
        "tools_iso_newdia": 0.1,

        "tools_iso_tool_shape": 0,  # "C1"
        "tools_iso_cutz": -0.07,
        "tools_iso_vtipdia": 0.1,
        "tools_iso_vtipangle": 30,

        "tools_iso_passes": 1,
        "tools_iso_pad_passes": 0,
        "tools_iso_overlap": 10,
        "tools_iso_milling_type": "cl",
        "tools_iso_isotype": "full",

        "tools_iso_rest":           False,
        "tools_iso_combine_passes": True,
        "tools_iso_clear_leftovers": False,
        "tools_iso_check_valid":    False,
        "tools_iso_isoexcept":      False,
        "tools_iso_selection":      0,
        "tools_iso_poly_ints":      False,
        "tools_iso_force":          True,
        "tools_iso_area_shape":     "square",
        "tools_iso_simplification":     True,
        "tools_iso_simplification_tol": 0.001,
        "tools_iso_plotting":       'normal',
        "tools_iso_multi_objects":  False,

        # Drilling Plugin
        "tools_drill_tool_order": 'no',
        "tools_drill_cutz": -1.7,
        "tools_drill_multidepth": False,
        "tools_drill_depthperpass": 0.7,
        "tools_drill_travelz": 2,
        "tools_drill_endz": 15,
        "tools_drill_endxy": None,

        "tools_drill_feedrate_z": 300,
        "tools_drill_spindlespeed": 0,
        "tools_drill_dwell": False,
        "tools_drill_dwelltime": 1,
        "tools_drill_dwell_bottom": False,
        "tools_drill_peck": False,
        "tools_drill_peck_depth": 0.5,
        "tools_drill_peck_retract": 0.2,
        # Spot drilling pass (job options): one extra tool pass over all the selected holes before the drills
        "tools_drill_spot": False,
        "tools_drill_spot_dia": 1.0,
        "tools_drill_spot_cutz": -0.3,
        "tools_drill_spot_feedrate_z": 300,
        # Job XY transforms of the drill points (job options): mirror -> offset -> array
        "tools_drill_job_offset_xy": None,
        "tools_drill_mirror": 'no',
        "tools_drill_mirror_point": None,
        "tools_drill_array_columns": 1,
        "tools_drill_array_rows": 1,
        "tools_drill_array_pitch": None,
        # Which holes are drilled: all of them or only those inside the areas drawn on the canvas (Advanced)
        "tools_drill_holes_selection": 'all',
        # Laser parameters
        "tools_drill_min_power": 0.0,
        "tools_drill_laser_on": "M3",

        "tools_drill_toolchange": False,
        "tools_drill_toolchangez": 15,
        "tools_drill_preprocessor_list": ['default'],
        "tools_drill_ppname_e": 'default',

        "tools_drill_drill_slots": False,
        "tools_drill_drill_overlap": 0.0,
        "tools_drill_last_drill": True,

        # Advanced Options
        "tools_drill_offset": 0.0,
        "tools_drill_toolchangexy": "0.0, 0.0",
        "tools_drill_startz": None,
        "tools_drill_feedrate_rapid": 1500,
        "tools_drill_z_p_depth": -0.02,
        "tools_drill_probe_plate": 0.0,
        "tools_drill_feedrate_probe": 75,
        "tools_drill_spindledir": 'CW',
        "tools_drill_f_plunge": False,
        "tools_drill_f_retract": False,

        "tools_drill_area_exclusion": False,
        "tools_drill_area_shape": "polygon",
        "tools_drill_area_strategy": "over",
        "tools_drill_area_overz": 1.0,

        # Milling Plugin Options
        "tools_mill_tooldia": "2.4",
        "tools_mill_offset_type": 0,  # _('Path')
        "tools_mill_offset_value": 0.0,
        "tools_mill_job_type": 0,  # 'Roughing'
        "tools_mill_tool_shape": 0,  # 'C1'

        "tools_mill_cutz": -2.4,
        "tools_mill_vtipdia": 0.1,
        "tools_mill_vtipangle": 30,
        "tools_mill_multidepth": False,
        "tools_mill_depthperpass": 0.8,
        "tools_mill_travelz": 2,

        "tools_mill_toolchange": False,
        "tools_mill_toolchangez": 15.0,
        "tools_mill_endz": 15.0,
        "tools_mill_endxy": None,

        "tools_mill_feedrate": 120,
        "tools_mill_feedrate_z": 60,
        # Laser parameters
        "tools_mill_min_power": 0.0,
        "tools_mill_laser_on": "M3",

        "tools_mill_spindlespeed": 0,
        "tools_mill_dwell": False,
        "tools_mill_dwelltime": 1,
        "tools_mill_preprocessor_list": ['default'],
        "tools_mill_ppname_g": 'default',

        # Milling Plugin Advanced Options
        "tools_mill_toolchangexy": "0.0, 0.0",
        "tools_mill_startz": None,
        "tools_mill_feedrate_rapid": 1500,
        "tools_mill_extracut": False,
        "tools_mill_extracut_length": 0.1,
        "tools_mill_z_p_depth": -0.02,
        "tools_mill_probe_plate": 0.0,
        "tools_mill_feedrate_probe": 75,
        "tools_mill_f_plunge": False,
        "tools_mill_spindledir": 'CW',

        "tools_mill_area_exclusion": False,
        "tools_mill_area_shape": "polygon",
        "tools_mill_area_strategy": "over",
        "tools_mill_area_overz": 1.0,

        # Polish
        "tools_mill_polish": False,
        "tools_mill_polish_margin": 0.0,
        "tools_mill_polish_overlap": 5,
        "tools_mill_polish_method": 0,

        # Milling Plugin - Excellon Milling
        "tools_mill_milling_type": 'both',
        "tools_mill_milling_dia": 0.1,
        "tools_mill_milling_overlap": 10,   # in percentage
        "tools_mill_milling_connect": True,

        "tools_mill_optimization_type": 'R',
        "tools_mill_search_time": 3,

        # Autolevelling Plugin
        "tools_al_plot_points": False,
        "tools_al_avoid_exc_holes": False,
        "tools_al_avoid_exc_holes_size": 0.5,
        "tools_al_status": False,
        "tools_al_mode": 'grid',
        "tools_al_method": 'v',
        "tools_al_rows": 4,
        "tools_al_columns": 4,
        "tools_al_travel_z": 2.0,
        "tools_al_probe_depth": -1.0,
        "tools_al_probe_fr": 120,
        "tools_al_probe_tip_dia": 0.3,
        "tools_al_controller": 'MACH3',

        # GRBL Sender Plugin
        "tools_grbl_jog_step": 5,
        "tools_grbl_jog_fr": 1500,
        "tools_grbl_travelz": 15.0,
        "tools_grbl_baudrate": '115200',
        "tools_grbl_console_visible": False,
        "tools_gcode_fit_view": True,
        "tools_grbl_arc_mode": 'split',
        "tools_grbl_arc_segment": 0.5,
        "tools_grbl_seg_len": 1.0,
        "tools_grbl_status_ms": 200,

        # NCC Plugin
        "tools_ncc_tools": "0.5",
        "tools_ncc_order": 2,   # "Reverse"
        "tools_ncc_operation": 'clear',
        "tools_ncc_overlap": 40,
        "tools_ncc_margin": 1.0,
        "tools_ncc_method": 1,  # SEED
        "tools_ncc_connect": True,
        "tools_ncc_contour": True,
        "tools_ncc_rest": False,
        "tools_ncc_final_cleanup": False,
        "tools_ncc_offset_choice": False,
        "tools_ncc_offset_value": 0.0000,
        "tools_ncc_ref": 0,     # ITSELF
        "tools_ncc_area_shape": "square",
        "tools_ncc_milling_type": 'cl',
        "tools_ncc_cutz": -0.05,
        "tools_ncc_tipdia": 0.1,
        "tools_ncc_tipangle": 30,
        "tools_ncc_newdia": 0.1,
        "tools_ncc_plotting": 'normal',
        "tools_ncc_check_valid": True,
        "tools_ncc_simplification": 0.01,  # millimetres, like global_tolerance
        "tools_ncc_multi_objects": False,

        # Cutout Tool
        "tools_cutout_tooldia": 2.4,
        "tools_cutout_kind": "single",
        "tools_cutout_margin": 0.1,
        "tools_cutout_z": -1.8,
        "tools_cutout_depthperpass": 0.6,
        "tools_cutout_mdepth": True,
        "tools_cutout_gapsize": 4,
        "tools_cutout_gaps_ff": "4",
        "tools_cutout_convexshape": False,
        "tools_cutout_inner": False,
        "tools_cutout_inner_gaps": False,
        "tools_cutout_big_cursor": True,
        "tools_cutout_gap_type": 0,     # "Basic Gap"
        "tools_cutout_gap_depth": -1.0,
        "tools_cutout_mb_dia": 0.6,
        "tools_cutout_mb_spacing": 0.3,
        "tools_cutout_drill_dia": 1.0,
        "tools_cutout_drill_pitch": 1.0,
        "tools_cutout_drill_margin": 0.0,

        # Paint Plugin
        "tools_paint_tooldia": 0.3,
        "tools_paint_order": 2,     # "Reverse"
        "tools_paint_overlap": 20,
        "tools_paint_offset": 0.0,
        "tools_paint_method": 0,
        "tools_paint_selectmethod": 0,
        "tools_paint_area_shape": "square",
        "tools_paint_connect": True,
        "tools_paint_contour": True,
        "tools_paint_final_cleanup": False,
        "tools_paint_plotting": 'normal',
        "tools_paint_rest": False,
        "tools_paint_rest_offset": 0.0,
        "tools_paint_simplification": 0.01,  # millimetres, like global_tolerance
        "tools_paint_multi_objects": False,
        "tools_paint_check_valid": False,
        "tools_paint_cutz": -0.05,
        "tools_paint_tipdia": 0.1,
        "tools_paint_tipangle": 30,
        "tools_paint_newdia": 0.1,

        # 2-Sided Tool
        "tools_2sided_mirror_axis": "X",
        "tools_2sided_axis_loc": "point",
        "tools_2sided_drilldia": 3.125,
        "tools_2sided_align_type": "X",

        # Film Tool
        "tools_film_shape": False,
        "tools_film_rounded": False,
        "tools_film_polarity": 'neg',
        "tools_film_boundary": 1.0,
        "tools_film_scale_stroke": 0,
        "tools_film_color": '#000000',

        "tools_film_scale_cb": False,
        "tools_film_scale_type": 0,  # "length"

        "tools_film_scale_x_entry": 0.0,
        "tools_film_scale_y_entry": 0.0,
        "tools_film_scale_ref": 1,  # "bottom left"

        "tools_film_skew_cb": False,
        "tools_film_skew_type": 0,  # "length"

        "tools_film_skew_x_entry": 0.0,
        "tools_film_skew_y_entry": 0.0,
        "tools_film_skew_ref": 1,  # "bottom left"

        "tools_film_mirror_cb": False,
        "tools_film_mirror_axis_radio": 'x',
        "tools_film_file_type_radio": 'svg',
        "tools_film_orientation": 'p',
        "tools_film_pagesize": 'A4',
        "tools_film_png_dpi": 96,

        # Panel Tool
        "tools_panelize_spacing_columns": 0.0,
        "tools_panelize_spacing_rows": 0.0,
        "tools_panelize_columns": 1,
        "tools_panelize_rows": 1,
        "tools_panelize_optimization": True,
        "tools_panelize_constrain": False,
        "tools_panelize_constrainx": 200.0,
        "tools_panelize_constrainy": 290.0,
        "tools_panelize_panel_type": 'gerber',

        # Calculators Tool
        "tools_calc_vshape_tip_dia": 0.2,
        "tools_calc_vshape_tip_angle": 30,
        "tools_calc_vshape_cut_z": -0.05,
        "tools_calc_electro_length": 10.0,
        "tools_calc_electro_width": 10.0,
        "tools_calc_electro_area": 100.0,
        "tools_calc_electro_cdensity": 13.0,
        "tools_calc_electro_growth": 10.0,

        # Transform Tool
        "tools_transform_reference": _("Selection"),
        "tools_transform_ref_object": _("Gerber"),
        "tools_transform_ref_point": "0, 0",

        "tools_transform_rotate": 90,
        "tools_transform_skew_x": 0.0,
        "tools_transform_skew_y": 0.0,
        "tools_transform_skew_link": True,

        "tools_transform_scale_x": 1.0,
        "tools_transform_scale_y": 1.0,
        "tools_transform_scale_link": True,

        "tools_transform_offset_x": 0.0,
        "tools_transform_offset_y": 0.0,

        "tools_transform_buffer_dis": 0.0,
        "tools_transform_buffer_factor": 100.0,
        "tools_transform_buffer_corner": True,

        # SolderPaste Tool
        "tools_solderpaste_tools": "1.0, 0.3",
        "tools_solderpaste_new": 0.3,
        "tools_solderpaste_margin": 0.0,
        "tools_solderpaste_z_start": 0.05,
        "tools_solderpaste_z_dispense": 0.1,
        "tools_solderpaste_z_stop": 0.05,
        "tools_solderpaste_z_travel": 2.0,
        "tools_solderpaste_z_toolchange": 5.0,
        "tools_solderpaste_xy_toolchange": "0.0, 0.0",
        "tools_solderpaste_frxy": 150,
        "tools_solderpaste_fr_rapids": 1500,
        "tools_solderpaste_frz": 150,
        "tools_solderpaste_frz_dispense": 1.0,
        "tools_solderpaste_speedfwd": 300,
        "tools_solderpaste_dwellfwd": 1,
        "tools_solderpaste_speedrev": 200,
        "tools_solderpaste_dwellrev": 1,
        "tools_solderpaste_preprocessor_list": ['Paste_1', 'Paste_GRBL', 'Paste_Marlin'],
        "tools_solderpaste_pp": 'Paste_1',
        "tools_solderpaste_controller": 'Mach3',
        "tools_solderpaste_drive": 'Motor',
        "tools_solderpaste_volume_mode": 'Thickness',
        "tools_solderpaste_thickness": 0.1,
        "tools_solderpaste_volume": 0.05,
        "tools_solderpaste_flow_rate": 0.0,
        "tools_solderpaste_calibration": 1.0,
        "tools_solderpaste_syringe_dia": 10.0,
        "tools_solderpaste_pitch": 1.0,
        "tools_solderpaste_gear_ratio": 1.0,
        "tools_solderpaste_axis": 'A',
        "tools_solderpaste_axis_mode": 'Rotary',
        "tools_solderpaste_plunger_feed": 60.0,
        "tools_solderpaste_placement": 'Auto',
        "tools_solderpaste_overlap": 30.0,
        "tools_solderpaste_dot_dia": 0.2,
        "tools_solderpaste_prime": 0.0,
        "tools_solderpaste_retract": 0.02,
        "tools_solderpaste_z_rise": False,
        "tools_solderpaste_mach3_dwell": 'Seconds',
        "tools_solderpaste_grbl_mode": 'Spindle',
        "tools_solderpaste_marlin_cold": False,

        # Subtract Tool
        "tools_sub_close_paths": True,
        "tools_sub_keep_target": True,
        "tools_sub_delete_subtractors": False,
        "tools_sub_operation": 'subtract',
        "tools_sub_margin": 0.0,
        "tools_sub_suffix": '_sub',

        # Distance Tool
        "tools_dist_snap_center": False,
        "tools_dist_big_cursor": True,

        # Markers Tool
        "tools_markers_thickness": 0.1,
        "tools_markers_length": 3.0,
        "tools_markers_reference": "e",     # from edge
        "tools_markers_offset_x": 0.0,
        "tools_markers_offset_y": 0.0,
        "tools_markers_type": 's',
        "tools_markers_drill_dia": 0.5,
        "tools_markers_mode": 0,            # 'automatic'
        "tools_markers_big_cursor": True,

        # ########################################################################################################
        # ################################ TOOLS 2 ###############################################################
        # ########################################################################################################

        # Optimal Tool
        "tools_opt_precision": 4,

        # Check Rules Tool
        "tools_cr_trace_size": True,
        "tools_cr_trace_size_val": 0.25,
        "tools_cr_c2c": True,
        "tools_cr_c2c_val": 0.25,
        "tools_cr_c2o": True,
        "tools_cr_c2o_val": 1.0,
        "tools_cr_s2s": True,
        "tools_cr_s2s_val": 0.25,
        "tools_cr_s2sm": True,
        "tools_cr_s2sm_val": 0.25,
        "tools_cr_s2o": True,
        "tools_cr_s2o_val": 1.0,
        "tools_cr_sm2sm": True,
        "tools_cr_sm2sm_val": 0.25,
        "tools_cr_ri": True,
        "tools_cr_ri_val": 0.3,
        "tools_cr_h2h": True,
        "tools_cr_h2h_val": 0.3,
        "tools_cr_dh": True,
        "tools_cr_dh_val": 0.3,
        "tools_cr_slot": True,
        "tools_cr_slot_val": 0.5,
        "tools_cr_h2o": True,
        "tools_cr_h2o_val": 0.5,
        "tools_cr_h2c": True,
        "tools_cr_h2c_val": 0.2,
        "tools_cr_iso": False,
        "tools_cr_iso_val": 0.1,

        # QRCode Tool
        "tools_qrcode_version": 1,
        "tools_qrcode_error": 'L',
        "tools_qrcode_box_size": 3,
        "tools_qrcode_border_size": 4,
        "tools_qrcode_qrdata": '',
        "tools_qrcode_polarity": 'pos',
        "tools_qrcode_rounded": 's',
        "tools_qrcode_fill_color": '#000000',
        "tools_qrcode_back_color": '#FFFFFF',
        "tools_qrcode_sel_limit": 330,
        "tools_qrcode_version_auto": True,
        "tools_qrcode_mask_pattern": 'Auto',
        "tools_qrcode_min_module": 0.15,
        "tools_qrcode_rotation": 0,
        "tools_qrcode_mirror": False,
        "tools_qrcode_placement": 'click',
        "tools_qrcode_anchor": 'bl',
        "tools_qrcode_multi": False,
        "tools_qrcode_serial_start": 1,
        "tools_qrcode_serial_step": 1,
        "tools_qrcode_serial_pad": 0,
        "tools_qrcode_output": 'source',
        "tools_qrcode_margin": 0.0,

        # Copper Thieving Tool
        "tools_copper_thieving_clearance": 0.25,
        "tools_copper_thieving_margin": 1.0,
        "tools_copper_thieving_area": 0.1,
        "tools_copper_thieving_reference": 0,
        "tools_copper_thieving_box_type": 'rect',
        "tools_copper_thieving_circle_steps": 16,
        "tools_copper_thieving_fill_type": 0,
        "tools_copper_thieving_dots_dia": 1.0,
        "tools_copper_thieving_dots_spacing": 2.0,
        "tools_copper_thieving_squares_size": 1.0,
        "tools_copper_thieving_squares_spacing": 2.0,
        "tools_copper_thieving_lines_size": 0.25,
        "tools_copper_thieving_lines_spacing": 2.0,
        "tools_copper_thieving_rb_margin": 1.0,
        "tools_copper_thieving_rb_thickness": 1.0,
        "tools_copper_thieving_only_apds": True,
        "tools_copper_thieving_mask_clearance": 0.0,
        "tools_copper_thieving_geo_choice": 0,

        # Fiducials Tool
        "tools_fiducials_dia": 1.0,
        "tools_fiducials_margin": 1.0,
        "tools_fiducials_mode": 'auto',
        "tools_fiducials_second_pos": 'up',
        "tools_fiducials_type": 0,
        "tools_fiducials_line_thickness": 0.25,
        "tools_fiducials_big_cursor": True,

        # Drills Extraction Tool
        "tools_extract_hole_type": 'fixed',
        "tools_extract_hole_fixed_dia": 0.5,
        "tools_extract_hole_prop_factor": 80.0,
        "tools_extract_circular_ring": 0.2,
        "tools_extract_oblong_ring": 0.2,
        "tools_extract_square_ring": 0.2,
        "tools_extract_rectangular_ring": 0.2,
        "tools_extract_others_ring": 0.2,
        "tools_extract_circular": True,
        "tools_extract_oblong": False,
        "tools_extract_square": False,
        "tools_extract_rectangular": False,
        "tools_extract_others": False,
        "tools_extract_sm_clearance": 0.1,
        "tools_extract_cut_margin": 0.1,
        "tools_extract_cut_thickness": 0.1,
        "tools_extract_oblong_slots": False,
        "tools_extract_include_regions": False,
        "tools_extract_cut_shape": 'rect',
        "tools_extract_all_selected": False,

        # Punch Gerber Tool
        "tools_punch_hole_type": 'exc',
        "tools_punch_hole_fixed_dia": 0.5,
        "tools_punch_hole_prop_factor": 80.0,
        "tools_punch_circular_ring": 0.2,
        "tools_punch_oblong_ring": 0.2,
        "tools_punch_square_ring": 0.2,
        "tools_punch_rectangular_ring": 0.2,
        "tools_punch_others_ring": 0.2,
        "tools_punch_circular": True,
        "tools_punch_oblong": False,
        "tools_punch_square": True,
        "tools_punch_rectangular": False,
        "tools_punch_others": False,

        # Align Objects Tool
        "tools_align_objects_align_type": 'sp',
        "tools_align_objects_fit_model": 'rigid',
        "tools_align_objects_allow_mirror": False,

        # Invert Gerber Tool
        "tools_invert_margin": 0.1,
        "tools_invert_join_style": 's',
        "tools_invert_source": 'object',
        "tools_invert_reference": 'itself',
        "tools_invert_ref_area": 'box',

        # Etch Compensation Tool
        "tools_etch_thickness": 18.0,
        "tools_etch_ratio": 'factor',
        "tools_etch_factor": 3.0,
        "tools_etch_etchant": 'cucl2',
        "tools_etch_offset": 0.0,
        "tools_etch_suffix": '_comp',

        # Utilities
        # file associations
        "fa_excellon": 'drd, drill, drl, exc, ncd, tap, xln',
        "fa_gcode": 'cnc, din, dnc, ecs, eia, fan, fgc, fnc, gc, gcd, gcode, h, hnc, i, min, mpf, mpr, nc, ncc, '
                    'ncg, ncp, ngc, out, ply, rol, sbp, tap, xpi',
        "fa_gerber": 'art, bot, bsm, cmp, crc, crs, dim, gb0, gb1, gb2, gb3, gb4, gb5, gb6, gb7, gb8, gb9, gbd, '
                     'gbl, gbo, gbp, gbr, gbs, gdo, ger, gko, gm1, gm2, gm3, grb, gtl, gto, gtp, gts, ly15, ly2, '
                     'mil, outline, pho, plc, pls, smb, smt, sol, spb, spt, ssb, sst, stc, sts, top, tsm',
        # Keyword list
        "util_autocomplete_keywords": 'Berta_CNC, Check_points, Default_no_M6, Desktop, Documents, FlatConfig, '
                                      'FlatPrj, False, '
                                      'GRBL_11, GRBL_11_no_M6, GRBL_laser, grbl_laser_eleks_drd, GRBL_laser_z, '
                                      'ISEL_CNC, ISEL_ICP_CNC, '
                                      'Line_xyz, Marlin, Marlin_laser_FAN_pin, '
                                      'Marlin_laser_Spindle_pin, Marlin_laser_z, NCCAD9, '
                                      'Marius, My Documents, Paste_1, Paste_GRBL, Paste_Marlin, '
                                      'default_laser, '
                                      'Repetier, Roland_MDX_20, Roland_MDX_540,'
                                      'Toolchange_Manual, Toolchange_Probe_MACH3, True, '
                                      'Users, all, auto, axis, axisoffset, '
                                      'box, center_x, center_y, center, columns, combine, connect, contour, default, '
                                      'depthperpass, dia, diatol, dist, drilled_dias, drillz, dpp, dwelltime, '
                                      'endxy, endz, extracut_length, f, factor, feedrate, '
                                      'feedrate_z, gridoffsety, gridx, gridy, has_offset, '
                                      'holes, hpgl, iso_type, join, '
                                      'las_min_pwr, las_power, keep_scripts, margin, marlin, method, milled_dias, '
                                      'minoffset, min_bounds, name, offset, opt_type, order, '
                                      'outname, overlap, obj_name, '
                                      'p_coords, passes, postamble, pp, ppname_e, ppname_g, preamble, radius, ref, '
                                      'rest, '
                                      'rows, shellvar_, scale_factor, spacing_columns, spacing_rows, spindlespeed, '
                                      'startz, startxy, toolchange_xy, toolchangez, '
                                      'tooldia, travelz, use_threads, value, '
                                      'x, x0, x1, x_dist, y, y0, y1, y_dist, z_cut, '
                                      'z_move',
        "script_autocompleter": True,
        "script_text": "",
        "script_plot": True,
        "script_source_file": "",

        "document_autocompleter": False,
        "document_text": "",
        "document_plot": True,
        "document_source_file": "",
        "document_font_color": '#000000FF',
        "document_sel_color": '#0055ffFF',
        "document_font_size": 6,
        "document_tab_size": 80,
        "document_font_sizes": [
            '6', '7', '8', '9', '10', '11', '12', '13', '14',
            '15', '16', '18', '20', '22', '24', '26', '28',
            '32', '36', '40', '44', '48', '54', '60', '66',
            '72', '80', '88', '96'
        ]
    }

    @classmethod
    def save_factory_defaults(cls, file_path: str, version: (float, str)):
        """Writes the factory defaults to a file at the given path, ensuring it's up-to-date."""
        # ponytail: version param is ignored. File is written if missing or different.
        needs_write = True
        if os.path.isfile(file_path):
            try:
                with open(file_path, "r") as file:
                    f_defaults = json.loads(file.read())
                if f_defaults == cls.factory_defaults:
                    needs_write = False
            except Exception:
                pass

        if not needs_write:
            return

        # ponytail: transactional write — serialize to temp in same dir, then
        # os.replace. Existing file bytes survive serialization or replacement
        # failure. (Post-replace chmod failure already changes bytes.)
        dest_dir = os.path.dirname(file_path) or '.'
        temp_path = None
        original_mode = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=dest_dir,
                                             prefix='.factory_tmp_',
                                             delete=False) as tmp_f:
                temp_path = tmp_f.name
                json.dump(cls.factory_defaults, tmp_f,
                                default=to_dict, indent=2, sort_keys=True)

            # Make destination writable for os.replace (Windows read-only guard)
            if os.path.isfile(file_path):
                original_mode = os.stat(file_path).st_mode
                try:
                    os.chmod(file_path, stat.S_IRWXO | stat.S_IWRITE | stat.S_IWGRP)
                except OSError:
                    pass

            os.replace(temp_path, file_path)
            temp_path = None  # replace consumed the temp file

            os.chmod(file_path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            log.debug("FlatCAM factory defaults written to: %s" % file_path)
        except Exception as e:
            log.error("save_factory_defaults() -> %s" % str(e))
            if original_mode is not None:
                try:
                    os.chmod(file_path, original_mode)
                except OSError:
                    pass
        finally:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def __init__(self, callback=lambda x: None, beta=True, version=8.9):
        """
        Class that holds the default parameters used throughout the app.

        :param callback:    A method called each time that one of the values are changed in the "self.defaults" LouDict
        """
        self.defaults = LoudDict()

        self.beta = beta
        self.version = version

        self.defaults.update(deepcopy(self.factory_defaults))
        self.current_defaults = deepcopy(self.factory_defaults)  # copy used for restoring after cancelled prefs changes
        self.old_defaults_found = False

        self.defaults.set_change_callback(callback)

    # #### Pass-through to the defaults LoudDict #####
    def __len__(self):
        return self.defaults.__len__()

    def __getitem__(self, item):
        return self.defaults.__getitem__(item)

    def __setitem__(self, key, value):
        return self.defaults.__setitem__(key, value)

    def __delitem__(self, key):
        return self.defaults.__delitem__(key)

    def __iter__(self):
        return self.defaults.__iter__()

    def __getattr__(self, item):
        # Unfortunately this method alone is not enough to pass through the other magic methods above.
        return self.defaults.__getattribute__(item)

    def get(self, key, default=_MISSING):
        """Return an application preference with its factory-default baseline."""
        if key in self.defaults:
            return self.defaults[key]
        if default is not _MISSING:
            return default
        return deepcopy(self.factory_defaults.get(key))

    # #### Additional Methods #####
    def write(self, filename: str):
        """Saves the defaults to a file on disk"""
        # ponytail: strip obsolete 'version' field before saving
        data = dict(self.defaults)
        data.pop('version', None)
        with open(filename, "w") as file:
            json.dump(data, file, default=to_dict, indent=2, sort_keys=True)

    @staticmethod
    def find_legacy_defaults_file(data_path: str, current_version: str) -> str:
        """
        Find a legacy versioned defaults file for migration.
        
        Priority:
        1. Exact current version file (e.g., current_defaults_1.0.FlatConfig)
        2. Most recently modified file (by mtime), with filename as tiebreaker
        
        :param data_path: Directory containing defaults files
        :param current_version: Current app version string
        :return: Path to legacy file or None
        """
        import glob
        import re
        
        # Pattern: current_defaults_{version}.FlatConfig
        pattern = os.path.join(data_path, 'current_defaults_*.FlatConfig')
        legacy_files = glob.glob(pattern)
        
        if not legacy_files:
            return None
        
        # Filter out non-versioned files and extract versions
        version_pattern = re.compile(r'current_defaults_(.+)\.FlatConfig$')
        versioned_files = []
        for f in legacy_files:
            match = version_pattern.search(f)
            if match:
                version = match.group(1)
                versioned_files.append((version, f))
        
        if not versioned_files:
            return None
        
        # Priority 1: exact current version
        for version, path in versioned_files:
            if version == current_version:
                return path
        
        # Priority 2: most recently modified file, with filename as tiebreaker
        # Use mtime to determine which file was saved most recently
        versioned_files_with_mtime = []
        for version, path in versioned_files:
            try:
                mtime = os.path.getmtime(path)
                versioned_files_with_mtime.append((mtime, path, version))
            except OSError:
                # If we can't get mtime, skip this file
                pass
        
        if not versioned_files_with_mtime:
            return None
        
        # Sort by mtime descending, then by filename descending as tiebreaker
        versioned_files_with_mtime.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return versioned_files_with_mtime[0][1]

    def load(self, filename: str, inform):
        """
        Loads the defaults from a file on disk, merging with factory defaults.

        :param filename:    a path to the file that is to be loaded
        :param inform:      a pyqtSignal used to display information's in the StatusBar of the GUI
        """

        # Read in the file
        try:
            f = open(filename)
            options = f.read()
            f.close()
        except IOError:
            log.error("Could not load defaults file.")
            inform.emit('[ERROR] %s' % _("Could not load the file."))
            return

        # Parse the JSON
        try:
            defaults = json.loads(options)
        except Exception:
            log.error(str(sys.exc_info()[0]))
            inform.emit('[ERROR] %s' % _("Failed to parse defaults file."))
            return
        if defaults is None:
            return

        # ponytail: strip obsolete 'version' field from loaded data
        defaults.pop('version', None)
        defaults.pop('global_graphic_engine', None)
        defaults.pop('global_autosave', None)
        defaults.pop('global_autosave_timeout', None)

        # Migrate before merging: the old appearance also selected the stylesheet.
        if 'global_styling' not in defaults:
            appearance = defaults.get('global_appearance', 'default')
            defaults['global_styling'] = 'default' if appearance == 'default' else 'custom'
            defaults['global_appearance'] = 'light' if appearance == 'default' else appearance
        
        # The saved Gerber save filters had no separate 'All Files (*.*)' group (it was joined to the '.art' type)
        # and, later, no '*' in the patterns. They were saved with the preferences at each exit, also in the files of
        # the older versions (find_legacy_defaults_file()). Drop them so the factory value is used.
        saved_filters = defaults.get('gerber_save_filters')
        if saved_filters is not None and 'All Files (*.*)' not in str(saved_filters).split(';;'):
            defaults.pop('gerber_save_filters')

        # The Subtract plugin had one 'delete sources' choice (target and subtractor); it is now 2 choices.
        if 'tools_sub_delete_sources' in defaults:
            delete_sources = bool(defaults.pop('tools_sub_delete_sources'))
            defaults.setdefault('tools_sub_keep_target', not delete_sources)
            defaults.setdefault('tools_sub_delete_subtractors', delete_sources)

        # ponytail: merge saved data over factory defaults. Missing keys get factory values,
        # user overrides (including falsy values) are preserved. No version gating.
        self.old_defaults_found = False
        self.defaults.clear()
        self.defaults.update(deepcopy(self.factory_defaults))
        self.defaults.update(defaults)

        # Exact legacy Digi share default migrates to the GitHub repository; custom URLs survive.
        if self.defaults.get("global_update_url") == LEGACY_DIGI_UPDATE_URL:
            self.defaults["global_update_url"] = DEFAULT_UPDATE_URL
            # The Digi address stays available for the explicit legacy provider.
            self.defaults["global_legacy_update_url"] = LEGACY_DIGI_UPDATE_URL
        elif "global_update_provider" not in defaults and self._is_custom_digi_update_url(
                self.defaults.get("global_update_url")):
            # Settings saved before the provider choice held a custom Digi share in global_update_url:
            # keep it for the legacy provider and give the GitHub provider its own address.
            self.defaults["global_legacy_update_url"] = self.defaults.get("global_update_url")
            self.defaults["global_update_url"] = DEFAULT_UPDATE_URL

        self.current_defaults = deepcopy(dict(self.defaults))

        # log.debug("App defaults loaded from: %s" % filename)

    @staticmethod
    def _is_custom_digi_update_url(url) -> bool:
        """Return whether a saved update URL is a non-empty address that is not a GitHub repository."""
        if not isinstance(url, str) or not url.strip():
            return False
        try:
            normalize_repository(url)
        except TransportError:
            return True
        return False

    def reset_to_factory_defaults(self):
        self.defaults.clear()
        self.defaults.update(deepcopy(self.factory_defaults))
        self.current_defaults = deepcopy(self.factory_defaults)
        self.old_defaults_found = False

    def propagate_defaults(self):
        """
        This method is used to set default values in classes. It's
        an alternative to project options but allows the use
        of values invisible to the user.
        """
        # log.debug("propagate_defaults()")

        # Which objects to update the given parameters.
        routes = {
            "excellon_zeros": Excellon,
            "excellon_format_upper_in": Excellon,
            "excellon_format_lower_in": Excellon,
            "excellon_format_upper_mm": Excellon,
            "excellon_format_lower_mm": Excellon,
            "excellon_units": Excellon,
            "gerber_use_buffer_for_union": Gerber,
            "geometry_multidepth": Geometry
        }

        for param in routes:
            if param not in self.defaults and param not in self.factory_defaults:
                continue

            if param in routes[param].defaults:
                try:
                    routes[param].defaults[param] = self.get(param)
                except KeyError:
                    # log.error("AppDefaults.propagate_defaults() --> ERROR: " + param + " not in defaults.")
                    pass
            else:
                # Try extracting the name:
                # classname_param here is param in the object
                if param.find(routes[param].__name__.lower() + "_") == 0:
                    p = param[len(routes[param].__name__) + 1:]
                    if p in routes[param].defaults:
                        routes[param].defaults[p] = self.get(param)

    def report_usage(self, resource):
        """
        Increments usage counter for the given resource
        in "self.defaults['global_stats']".

        :param resource: Name of the resource.
        :return: None
        """

        stats = deepcopy(self.defaults.get('global_stats', self.factory_defaults.get('global_stats', {})))
        if resource in stats:
            stats[resource] += 1
        else:
            stats[resource] = 1
        self.defaults['global_stats'] = stats


# A marker for a missing key. Do not use _MISSING for this: AppDefaults.get(key, _MISSING) returns the factory value.
_ABSENT = object()


class AppOptions:
    def __init__(self, version, callback=lambda x: None, baseline=None, defaults=None):
        """
        Class that holds the options parameters used throughout the app.

        :param callback:    A method called each time that one of the values are changed in the self.defaults LouDict
        :param baseline:    The values used to fill the options at the start
        :param defaults:    The live AppDefaults. If it is set, only the project keys are stored here and the
                            application keys are read from and written to "defaults".
        """
        self.options = LoudDict()
        self.current_options = {}  # copy used for restoring after cancelled prefs changes
        self.version = version
        self.app_defaults = defaults
        self.baseline = deepcopy(dict(baseline)) if baseline is not None else deepcopy(AppDefaults.factory_defaults)
        self.baseline.pop('global_graphic_engine', None)
        if baseline is not None:
            self.options.update(deepcopy(self._own(self.baseline)))
            self.current_options = deepcopy(dict(self.options))
        # set after the first fill, so the callback runs only for later changes
        self.options.set_change_callback(callback)

    def _is_app_key(self, key):
        """Return True if "key" is read from and written to the linked defaults."""
        return self.app_defaults is not None and not is_project_key(key)

    def _own(self, values):
        """Return the part of "values" that this object stores itself."""
        if self.app_defaults is None:
            return dict(values)
        return {k: v for k, v in values.items() if is_project_key(k)}

    # #### Pass-through to the defaults LoudDict #####
    def __len__(self):
        return len(self.keys())

    def __getitem__(self, item):
        if self._is_app_key(item):
            return self.app_defaults[item]
        return self.options.__getitem__(item)

    def __setitem__(self, key, value):
        if self._is_app_key(key):
            current = self.app_defaults.get(key, _ABSENT)
            if current is not _ABSENT and current == value:
                return
            self.app_defaults[key] = value
            self.options.callback(key)
            return
        return self.options.__setitem__(key, value)

    def __delitem__(self, key):
        if self._is_app_key(key):
            del self.app_defaults[key]
            return
        return self.options.__delitem__(key)

    def __contains__(self, key):
        if self._is_app_key(key):
            return self.app_defaults.get(key, _ABSENT) is not _ABSENT
        return key in self.options

    def __iter__(self):
        return iter(self.keys())

    def __getattr__(self, item):
        # Unfortunately this method alone is not enough to pass through the other magic methods above.
        return self.options.__getattribute__(item)

    def keys(self):
        if self.app_defaults is None:
            return self.options.keys()
        return list(self.options.keys()) + [k for k in self.app_defaults if not is_project_key(k)]

    def items(self):
        if self.app_defaults is None:
            return self.options.items()
        return [(k, self[k]) for k in self.keys()]

    def values(self):
        if self.app_defaults is None:
            return self.options.values()
        return [self[k] for k in self.keys()]

    def copy(self):
        return dict(self.items())

    def update(self, *args, **kwargs):
        if len(args) > 1:
            raise TypeError("update expected at most 1 arguments, got %d" % len(args))
        other = dict(*args, **kwargs)
        for key in other:
            self[key] = other[key]

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key, *default):
        if self._is_app_key(key):
            return self.app_defaults.pop(key, *default)
        return self.options.pop(key, *default)

    def get(self, key, default=_MISSING):
        """Return an option or its copied baseline when the option is missing."""
        if key in self:
            return self[key]
        if default is not _MISSING:
            return default
        return deepcopy(self.baseline.get(key))

    def reset_from_defaults(self):
        """Copy each project key from the linked defaults, or from the baseline if no defaults are linked."""
        source = self.app_defaults if self.app_defaults is not None else self.baseline
        for key in list(source):
            if is_project_key(key):
                self.options[key] = deepcopy(source[key])

    def load_project(self, project_options):
        """Set the project keys from a project file. The application keys in the file are ignored."""
        self.reset_from_defaults()
        clean = clean_project_options(project_options)
        # a key that the file does not have keeps the default value, which is in the units of the defaults
        source = self.app_defaults if self.app_defaults is not None else self.baseline
        for key, value in convert_length_options(
                {k: v for k, v in self.options.items() if k not in clean}, source.get('units'),
                clean.get('units')).items():
            self.options[key] = value
        for key, value in clean.items():
            self.options[key] = value

    def load(self, filename: str, inform, baseline=None, include_app_keys=False):
        """
        Loads the options from a file on disk, performing migration if required.

        :param filename:            a path to the file that is to be loaded
        :param inform:              a pyqtSignal used to display information's in the StatusBar of the GUI
        :param baseline:            the values used for the keys that the file does not have
        :param include_app_keys:    if defaults are linked, also write the application keys of the file
                                    into the defaults, except the update source (UPDATE_SOURCE_KEYS)
        """

        # Read in the file
        try:
            f = open(filename)
            options = f.read()
            f.close()
        except IOError:
            log.error("Could not load defaults file.")
            inform.emit('[ERROR] %s' % _("Could not load the file."))
            return

        # Parse the JSON
        try:
            options = json.loads(options)
        except Exception:
            # in case the defaults file can't be loaded, show all toolbars
            log.error(str(sys.exc_info()[0]))
            inform.emit('[ERROR] %s' % _("Failed to parse defaults file."))
            return
        if options is None:
            return

        if baseline is not None:
            self.baseline = deepcopy(dict(baseline))
        self.baseline.pop('global_graphic_engine', None)
        options.pop('global_graphic_engine', None)

        # Save the resulting defaults
        self.options.clear()
        self.options.update(deepcopy(self._own(self.baseline)))
        if self.app_defaults is None:
            self.options.update(options)
        else:
            for key, value in options.items():
                if is_project_key(key):
                    self.options[key] = value
                elif include_app_keys and key not in UPDATE_SOURCE_KEYS:
                    self[key] = value
        self.current_options = deepcopy(dict(self.options))
