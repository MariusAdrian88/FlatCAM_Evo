"""
The CNC job of the Milling plugin (and of the Tcl 'cncjob' command) reads the 'tools_mill_*' options from the tool
data. The tool data of a Geometry made by an older app version (e.g. in an older project) lacks the options added
later: 'tools_mill_laser_on' (2023-12) in an older FlatCAM Evo project. A FlatCAM 8.994 project has no 'tools_mill_*'
key: its tool data keeps the settings under older names ('cutz', 'feedrate'...) and the tool itself keeps 'tooldia',
'offset', 'offset_value', 'type' and 'tool_type'. The CNC job failed with a KeyError.

A missing option now gets, in this order: the value of its older name in the tool, the application value. A saved
preprocessor that was renamed gets its current name; one that the app does not have counts as absent. An older combo
box text gets its modern index; a text that is not recognized counts as absent.

make_cnc_job() makes the job from the saved tool data alone, to check the value of every key. make_plugin_cnc_job()
makes it like the Milling plugin, with its form.
"""
import os
from copy import deepcopy
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6 import QtWidgets
from shapely import LineString

from appPlugins.ToolMilling import ToolMilling
from camlib import CNCjob
from defaults import AppDefaults
from preprocessors.GRBL_11 import GRBL_11
from preprocessors.GRBL_laser import GRBL_laser
from preprocessors.GRBL_laser_z import GRBL_laser_z
from preprocessors.Line_xyz import Line_xyz
from preprocessors.default import default
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

MILL_OPTIONS = {k: v for k, v in AppDefaults.factory_defaults.items() if k.startswith('tools_mill_')}
# the options added in 2023-12: the tool data of a project saved before does not have them
ADDED_2023_12 = ('tools_mill_laser_on', 'tools_mill_z_p_depth')

# application options that differ from the settings saved in the FlatCAM 8.994 tools below
OTHER_OPTIONS = dict(tools_mill_cutz=-0.5, tools_mill_multidepth=False, tools_mill_depthperpass=0.3,
                     tools_mill_travelz=5.0, tools_mill_feedrate=300.0, tools_mill_feedrate_z=30.0,
                     tools_mill_ppname_g='GRBL_laser', tools_mill_spindlespeed=500, tools_mill_endz=25.0,
                     tools_mill_tooldia=2.4, tools_mill_offset_type=2, tools_mill_job_type=3, tools_mill_tool_shape=5,
                     tools_mill_laser_on='M4', tools_mill_min_power=7.0, tools_mill_toolchange=False)


class MillingHarness(SimpleNamespace):
    """Run the real controller methods without constructing its application GUI."""
    def __getattr__(self, name):
        method = getattr(ToolMilling, name)
        return method.__get__(self, type(self))


def evo_2023_tool_data():
    data = {k: deepcopy(v) for k, v in MILL_OPTIONS.items() if k not in ADDED_2023_12}
    data.update(tools_mill_tooldia=0.1, tools_mill_offset_type=0, tools_mill_job_type=0, tools_mill_cutz=-0.05,
                tools_mill_travelz=2.0, tools_mill_multidepth=False, tools_mill_toolchange=False,
                tools_mill_spindlespeed=800, tools_mill_optimization_type='R', tools_mill_ppname_g='GRBL_laser')
    # the probe depth had this name before 2023-12
    data['tools_mill_z_pdepth'] = -0.03
    return data


def flatcam_8_994_cutout_tool(**data_changes):
    """A tool of an Edge_Cuts cutout Geometry in a real FlatCAM 8.994 project: no 'tools_mill_*' key."""
    data = {'area_exclusion': False, 'area_overz': 1.0, 'area_shape': 'polygon', 'area_strategy': 'over',
            'cutz': -2.4, 'depthperpass': 1.2, 'dwell': False, 'dwelltime': 1.0, 'endxy': None, 'endz': 15.0,
            'extracut': False, 'extracut_length': 0.1, 'feedrate': 120.0, 'feedrate_rapid': 500.0, 'feedrate_z': 60.0,
            'multidepth': True, 'name': 'board-Edge_Cuts.gbr_cutout', 'optimization_type': 'R', 'plot': True,
            'ppname_g': 'default', 'spindledir': 'CW', 'spindlespeed': 0, 'startz': None, 'toolchange': False,
            'toolchangexy': [0.0, 0.0], 'toolchangez': 15.0, 'tools_cutout_tooldia': 2.4, 'tools_cutout_z': -1.8,
            'travelz': 2.0, 'vtipangle': 30.0, 'vtipdia': 0.1}
    data.update(data_changes)
    return {'offset': 'Path', 'offset_value': 0.0, 'tool_type': 'C1', 'tooldia': '1.75', 'type': 'Rough', 'data': data}


def milling_harness(tool, multigeo, **options):
    app = make_app(**options)
    # a plain dict, as in the app: a preprocessor name that it does not have raises a KeyError
    app.preprocessors = {'default': default(), 'GRBL_laser': GRBL_laser(), 'GRBL_11': GRBL_11(),
                         'Line_xyz': Line_xyz(), 'GRBL_laser_z': GRBL_laser_z()}
    app.dec_format = lambda value, decimals: round(value, decimals)
    app.exc_areas = SimpleNamespace(travel_coordinates=lambda **kw: [(None, kw['end_point'])])

    geometry = [LineString([(0, 0), (10, 0)]), LineString([(0, 5), (10, 5)])]
    tools = {1: dict(tool, solid_geometry=geometry)}
    obj_options = dict(MILL_OPTIONS, name='old_geo', xmin=0, ymin=0, xmax=10, ymax=5)
    source = SimpleNamespace(kind='geometry', multigeo=multigeo, tools=tools, solid_geometry=geometry,
                             obj_options=obj_options)
    jobs = []

    def new_object(kind, name, initializer, **kwargs):
        job = CNCjob(app, pp_geometry_name='GRBL_laser')
        job.kind, job.obj_options, job.tools, job.gc_start = kind, {'name': name}, {}, ''
        if initializer(job, app) == 'fail':
            return 'fail'
        jobs.append(job)
        return job

    milling = MillingHarness(app=app, target_obj=source, sel_tools=tools, units='MM', decimals=4, circle_steps=16,
                             new_object=new_object)
    return milling, jobs


def make_cnc_job(tool, multigeo, **options):
    """
    The CNC job from the saved tool data alone: from_tcl=True, without the form of the Milling plugin. No user action
    makes it like this: the Tcl 'cncjob' command first writes its arguments (or the application options) into the main
    keys of each tool (cut Z, feed rates, spindle speed, preprocessor...), and the plugin takes the general fields of a
    multi-geometry job from its form (make_plugin_cnc_job()).
    """
    milling, jobs = milling_harness(tool, multigeo, **options)
    result = milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False, from_tcl=True)
    assert result is None and len(jobs) == 1
    return jobs[0].source_file, milling


class FormField:
    """A field of the Milling plugin form."""
    def __init__(self, value):
        self.value = value

    def get_value(self):
        return deepcopy(self.value)

    def set_value(self, value):
        self.value = deepcopy(value)


# the general fields of the Milling plugin form that its CNC job reads: widget -> the option it shows
PLUGIN_FORM = {'offset_entry': 'tools_mill_offset_value', 'toolchange_cb': 'tools_mill_toolchange',
               'toolchangez_entry': 'tools_mill_toolchangez', 'toolchangexy_entry': 'tools_mill_toolchangexy',
               'endz_entry': 'tools_mill_endz', 'endxy_entry': 'tools_mill_endxy',
               'pdepth_entry': 'tools_mill_z_p_depth', 'feedrate_probe_entry': 'tools_mill_feedrate_probe',
               'exclusion_cb': 'tools_mill_area_exclusion', 'area_shape_radio': 'tools_mill_area_shape',
               'strategy_radio': 'tools_mill_area_strategy', 'over_z_entry': 'tools_mill_area_overz',
               'pp_geo_name_cb': 'tools_mill_ppname_g'}


def make_plugin_cnc_job(tool, multigeo, form=None, **options):
    """
    The CNC job of the Milling plugin (Generate CNCJob): from_tcl=False, with its form. The form shows the application
    values (as when it showed nothing else since the plugin opened), changed by 'form' (option -> value).
    """
    milling, jobs = milling_harness(tool, multigeo, **options)
    values = dict(milling.app.options, **(form or {}))
    milling.ui = SimpleNamespace(**{widget: FormField(values[key]) for widget, key in PLUGIN_FORM.items()})
    result = milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False)
    assert result is None and len(jobs) == 1
    return jobs[0], milling


def load_plugin_target(milling):
    """Load the target through the controller and its real Geometry tools table."""
    from appGUI.GUIElements import FCTable

    milling.app.app_units = 'MM'
    milling.app.ui = SimpleNamespace(notebook=SimpleNamespace(tabText=lambda _index: 'Milling'))
    milling.app.collection = SimpleNamespace(get_by_name=lambda _name: milling.target_obj,
                                             set_all_inactive=lambda: None, set_active=lambda _name: None)
    milling.ui = SimpleNamespace(**{widget: FormField(milling.app.options[key])
                                   for widget, key in PLUGIN_FORM.items()})
    milling.ui.object_combo = QtWidgets.QComboBox()
    milling.ui.object_combo.addItem('old_geo')
    milling.ui.target_radio = FormField('geo')
    milling.ui.tools_table_mill_geo = FCTable()
    milling.ui.tools_table_mill_geo.setColumnCount(5)
    milling.ui.param_frame = QtWidgets.QFrame()
    milling.ui.plot_cb = QtWidgets.QCheckBox()
    milling.ui.generate_cnc_button = QtWidgets.QPushButton()
    milling.ui.tool_data_label = QtWidgets.QLabel()
    milling.form_fields = {key: FormField(milling.app.options[key]) for key in (
        'tools_mill_cutz', 'tools_mill_multidepth', 'tools_mill_depthperpass',
        'tools_mill_travelz', 'tools_mill_feedrate', 'tools_mill_feedrate_z', 'tools_mill_spindlespeed')}
    milling.general_form_fields = {key: getattr(milling.ui, widget) for widget, key in PLUGIN_FORM.items()}
    milling.ui_disconnect = milling.ui_connect = milling.plot_cb_handler = lambda: None
    milling.build_ui = milling.build_ui_mill
    milling.on_object_changed()


@pytest.mark.parametrize('multigeo, edit_depth', [(False, False), (True, True)])
def test_legacy_form_displays_the_generated_parameters(multigeo, edit_depth):
    tool = flatcam_8_994_cutout_tool()
    milling, jobs = milling_harness(tool, multigeo, **OTHER_OPTIONS)

    load_plugin_target(milling)

    expected = {'tools_mill_cutz': -2.4, 'tools_mill_multidepth': True, 'tools_mill_depthperpass': 1.2,
                'tools_mill_travelz': 2.0, 'tools_mill_feedrate': 120.0, 'tools_mill_feedrate_z': 60.0}
    assert {key: milling.form_fields[key].get_value() for key in expected} == expected
    assert milling.ui.pp_geo_name_cb.get_value() == 'default'
    assert milling.ui.endz_entry.get_value() == 15.0

    if edit_depth:
        field = milling.form_fields['tools_mill_cutz']
        field.set_value(-0.8)
        field.objectName = lambda: 'cutz'
        milling.sender = lambda: field
        milling.name2option = {'cutz': 'tools_mill_cutz'}
        milling.form_to_storage()

    milling.generate_cnc_job_handler(tools_dict=deepcopy(milling.target_obj.tools),
                                     toolchange=False, plot=False, use_thread=False)

    gcode = jobs[0].source_file
    assert ('G01 Z-0.8000' if edit_depth else 'G01 Z-2.4000') in gcode
    assert 'G01 F120.00' in gcode and 'G01 F60.00' in gcode
    assert 'G00 Z2.0000' in gcode and 'G00 Z15.00' in gcode
    assert jobs[0].pp_geometry_name == milling.ui.pp_geo_name_cb.get_value()
    assert 'Z-0.5000' not in gcode


@pytest.mark.parametrize('multigeo', [False, True])
def test_milling_plugin_uses_the_saved_depth_passes_and_feed_rates(multigeo):
    # Generate CNCJob in the Milling plugin, with the 'default' preprocessor in the application options and in the form;
    # the application cut Z, passes, travel Z and feed rates differ from the saved ones
    tool = flatcam_8_994_cutout_tool()

    job, _milling = make_plugin_cnc_job(tool, multigeo, **dict(OTHER_OPTIONS, tools_mill_ppname_g='default'))

    gcode = job.source_file
    assert job.pp_geometry_name == 'default'
    assert 'G00 Z2.0000' in gcode and 'G01 Z-1.2000' in gcode and 'G01 Z-2.4000' in gcode
    assert 'G01 F120.00' in gcode and 'G01 F60.00' in gcode
    for value in ('Z-0.5000', 'Z-0.3000', 'Z5.0000', 'F300.00', 'F30.00'):
        assert value not in gcode
    # a multi-geometry job takes the general fields (preprocessor, end Z, toolchange...) from the form, as for every
    # project; a single-geometry job takes the saved ones
    assert ('G00 Z25.00' if multigeo else 'G00 Z15.00') in gcode
    assert tool['data']['tools_mill_endz'] == (OTHER_OPTIONS['tools_mill_endz'] if multigeo else 15.0)


def make_cnc_job_with_preprocessor(tool, multigeo, **options):
    """The CNC job, its G-code and the warnings that name the preprocessor saved in the tool."""
    saved = tool['data']['ppname_g']
    milling, jobs = milling_harness(tool, multigeo, **options)
    milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False, from_tcl=True)
    assert len(jobs) == 1
    warnings = [c.args[0] for c in milling.app.inform.emit.call_args_list if saved in str(c.args[0])]
    return jobs[0], milling.app.preprocessors, warnings


@pytest.mark.parametrize('saved, app_value, expected', [
    ('Seed', 2, 1),
    # not recognized, so the application value: a translated text (here Spanish), an index that does not exist, a bool
    ('Semilla', 2, 2), (7, 1, 1), (True, 2, 2)])
def test_saved_polish_method(saved, app_value, expected):
    # the development builds of 21-28.10.2020 saved the text of the polish method combo box; the FlatCAM 8.994 release
    # and FlatCAM Evo save the index
    tool = flatcam_8_994_cutout_tool(polish_method=saved)

    gcode, _milling = make_cnc_job(tool, True, **dict(OTHER_OPTIONS, tools_mill_polish_method=app_value))

    assert tool['data']['tools_mill_polish_method'] == expected
    assert type(tool['data']['tools_mill_polish_method']) is int
    # the other saved settings are kept
    assert 'G01 Z-2.4000' in gcode and 'G01 F120.00' in gcode

