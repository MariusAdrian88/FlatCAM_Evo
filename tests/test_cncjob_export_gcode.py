"""Merged from: test_cncjob_export_gcode: CNCJobObject.export_gcode() must keep the G-code of every tool of a multi-tool job, whatever the preprocessor; test_cncjob_gcode_speed: CNC job from a Geometry: the faster code gives the same G-code, the same parsed G-code and the same copies.
"""

import importlib
import os
import pkgutil
import re
import pytest
import appPreProcessor
import preprocessors
import json
import math
import numpy as np
import shapely
import camlib
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from appGUI.ObjectUI import CNCObjectUI
from appObjects.CNCJobObject import CNCJobObject
from hashlib import sha256
from shapely import LinearRing, LineString
from shapely.ops import substring
from appCommon.ProjectFile import load_project_file, save_project_file
from camlib import CNCjob
from defaults import AppDefaults
from tests.helpers_objects import make_app_0b as make_app


# ==============================================================================================================
# test_cncjob_export_gcode: CNCJobObject.export_gcode() must keep the G-code of every tool of a multi-tool job, whatever the preprocessor
# ==============================================================================================================

for _module in pkgutil.iter_modules(preprocessors.__path__):
    importlib.import_module('preprocessors.' + _module.name)

# a copy: other tests of the same worker change the global registry
PREPROCESSORS = dict(appPreProcessor.preprocessors)

HEADERLESS = ('ISEL_ICP_CNC', 'Roland_MDX_20', 'Roland_MDX_540')

# the key of the preprocessor name inside the tool's options depends on the kind of the source object
PP_KEY = {'Excellon': 'tools_drill_ppname_e', 'Geometry': 'tools_mill_ppname_g'}

class _Log:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = debug

def _job(kind, ppname):
    app = SimpleNamespace(preprocessors=PREPROCESSORS, log=_Log())
    job = SimpleNamespace(
        app=app,
        obj_options={'type': kind, 'name': 'job'},
        multitool=True,
        tools={uid: {'data': {PP_KEY[kind]: ppname}, 'gcode': code}
               for uid, code in enumerate(('BODY_TOOL_1\n', 'BODY_TOOL_2\n'), 1)},
        gcode='', gc_start='START_CODE\n', gc_header='HEADER\n',
    )
    options = {'cncjob_footer': False, 'cncjob_prepend': '', 'cncjob_append': '', 'cncjob_line_ending': False}
    job._app_option = lambda key, fallback=None: options.get(key, fallback)
    job.gcode_footer = lambda: ''
    return job

def _export(job):
    return CNCJobObject.export_gcode(job, to_file=True).getvalue()

@pytest.mark.parametrize('kind', ['Excellon'])
def test_every_preprocessor_keeps_the_code_of_every_tool(kind):
    dropped = []
    for ppname in sorted(appPreProcessor.preprocessors):
        text = _export(_job(kind, ppname))
        if not ('BODY_TOOL_1' in text and 'BODY_TOOL_2' in text
                and text.index('BODY_TOOL_1') < text.index('BODY_TOOL_2')):
            dropped.append(ppname)

    assert dropped == []

@pytest.mark.parametrize('kind', ['Excellon'])
def test_unknown_preprocessor_of_a_saved_job_exports_with_the_default_one(kind):
    job = _job(kind, 'Gone_Machine')
    job.app.inform = Mock()
    job.app.options = {}

    text = _export(job)

    assert 'BODY_TOOL_1' in text and 'BODY_TOOL_2' in text
    assert 'Gone_Machine' in job.app.inform.emit.call_args[0][0]

@pytest.mark.parametrize('kind', ['Geometry', 'Excellon'])
@pytest.mark.parametrize('ppname,stored_name,multitool,footer_enabled,expected', [
    ('LinuxCNC', 'default', True, False, True),
    ('LinuxCNC', 'default', False, True, True),
    ('default', 'LinuxCNC', True, False, False),
    ('default', 'default', False, True, True),
    ('Roland_MDX_20', 'default', True, False, False),
    ('Roland_MDX_20', 'default', False, True, True),
])
def test_footer_uses_the_saved_tool_profile_and_follows_the_user_postamble(
        kind, ppname, stored_name, multitool, footer_enabled, expected):
    job = _job(kind, ppname)
    job.pp_geometry_name = job.pp_excellon_name = stored_name
    job.multitool = multitool
    job.gcode = 'BODY_SINGLE\n'
    app_option = job._app_option
    job._app_option = lambda key, fallback=None: footer_enabled if key == 'cncjob_footer' else app_option(key, fallback)
    job.gcode_footer = CNCJobObject.gcode_footer

    text = CNCJobObject.export_gcode(job, postamble='M9\n(APPEND: shutdown)', to_file=True).getvalue()

    body = 'BODY_TOOL_1\nBODY_TOOL_2' if multitool else 'BODY_SINGLE'
    assert body in text and text.index(body) < text.index('M9')
    assert text.count('M02') == int(expected)
    assert text.rstrip().endswith('M02' if expected else '(APPEND: shutdown)')

def test_controllers_that_need_a_program_end_get_it_after_the_postamble():
    for ppname in ('Mach3', 'Mach4', 'UCCNC', 'PlanetCNC', 'LinuxCNC'):
        job = _job('Geometry', ppname)
        job.gcode_footer = CNCJobObject.gcode_footer

        text = CNCJobObject.export_gcode(job, postamble='M9', to_file=True).getvalue()

        assert text.rstrip().splitlines()[-2:] == ['M9', 'M02'], ppname

@pytest.mark.parametrize('kind', ['Geometry', 'Excellon'])
def test_fanuc_export_envelope_follows_the_user_postamble(kind):
    job = _job(kind, 'Fanuc')

    text = CNCJobObject.export_gcode(job, postamble='M9\n(APPEND: shutdown)', to_file=True).getvalue()

    lines = text.rstrip('\n').splitlines()
    assert lines[:2] == ['%', 'O0001']
    assert lines[-2:] == ['M30', '%']
    assert text.count('M30') == 1
    assert text.index('BODY_TOOL_1') < text.index('BODY_TOOL_2') < text.index('M9') < text.index('M30')

@pytest.mark.parametrize('footer_enabled', [False, True])
def test_fanuc_program_end_is_written_once(footer_enabled):
    job = _job('Geometry', 'Fanuc')
    app_option = job._app_option
    job._app_option = lambda key, fallback=None: footer_enabled if key == 'cncjob_footer' else app_option(key, fallback)
    job.gcode_footer = CNCJobObject.gcode_footer

    text = CNCJobObject.export_gcode(job, postamble='M9', to_file=True).getvalue()

    # the app option writes its own M02; without it the envelope ends with M30. Never both
    end = 'M02' if footer_enabled else 'M30'
    assert text.count('M30') + text.count('M02') == 1
    assert text.rstrip().splitlines()[-2:] == [end, '%']
    assert text.index('M9') < text.index(end)

def test_fanuc_envelope_is_the_same_in_a_saved_file(tmp_path):
    job = _job('Geometry', 'Fanuc')
    text = CNCJobObject.export_gcode(job, postamble='M9', to_file=True).getvalue()

    path = tmp_path / 'fanuc.nc'
    CNCJobObject.export_gcode(job, filename=str(path), postamble='M9')

    assert path.read_text(encoding='utf-8') == text

@pytest.mark.parametrize('ppname', ['Fanuc', 'Haas'])
@pytest.mark.parametrize('footer_enabled', [False, True])
def test_industrial_envelope_preserves_the_user_program_end(ppname, footer_enabled):
    job = _job('Geometry', ppname)
    app_option = job._app_option
    job._app_option = lambda key, fallback=None: footer_enabled if key == 'cncjob_footer' else app_option(key, fallback)
    job.gcode_footer = CNCJobObject.gcode_footer
    postamble = 'M9\nM30'

    text = CNCJobObject.export_gcode(job, postamble=postamble, to_file=True).getvalue()

    assert postamble in text
    assert text.rstrip().endswith(('M02\n%' if footer_enabled else 'M30\n%'))

def test_export_supports_legacy_profiles_and_does_not_hide_formatter_errors():
    job = _job('Geometry', 'custom')
    pp = SimpleNamespace(include_header=True)
    job.app.preprocessors = dict(PREPROCESSORS, custom=pp)
    assert 'BODY_TOOL_1\nBODY_TOOL_2' in _export(job)

    pp.finalize_gcode = Mock(side_effect=ValueError('invalid program'))
    with pytest.raises(ValueError, match='invalid program'):
        _export(job)

@pytest.mark.parametrize('kind', ['Geometry', 'Excellon'])
def test_haas_export_envelope_follows_the_user_postamble(kind):
    job = _job(kind, 'Haas')

    text = CNCJobObject.export_gcode(job, postamble='M9\n(APPEND: shutdown)', to_file=True).getvalue()

    lines = text.rstrip('\n').splitlines()
    assert lines[:2] == ['%', 'O00001']
    assert lines[-2:] == ['M30', '%']
    assert text.count('M30') == 1
    assert text.index('BODY_TOOL_1') < text.index('BODY_TOOL_2') < text.index('M9') < text.index('M30')

def test_sinumerik_complete_file_starts_at_g291_and_ends_after_the_postamble():
    from tests.test_preprocessors import params

    job = _job('Geometry', 'Sinumerik')
    job.multitool = False
    job.gcode = 'G00 X1.0000 Y2.0000\n'
    job.gc_start = PREPROCESSORS['Sinumerik'].start_code(params()) + '\n'
    job.gcode_footer = CNCJobObject.gcode_footer

    text = CNCJobObject.export_gcode(job, preamble='(PREAMBLE)', postamble='M9\n(APPEND: shutdown)',
                                     to_file=True).getvalue()

    lines = text.rstrip('\n').splitlines()
    assert lines[0] == 'G291'
    assert 'HEADER' not in text
    assert '%' not in text and not re.search(r'^O\d+', text, re.M)
    assert 'M02' not in text and 'G290' not in text
    assert text.count('M30') == 1
    assert lines[-1] == 'M30'
    assert text.index('G291') < text.index('(PREAMBLE)') < text.index('G00 X1.0000') < text.index('M9') < \
        text.index('M30')

def test_sinumerik_saved_file_matches_the_export_and_ends_after_the_postamble(tmp_path):
    from tests.test_preprocessors import params

    job = _job('Geometry', 'Sinumerik')
    job.multitool = False
    job.gcode = 'G00 X1.0000 Y2.0000\n'
    job.gc_start = PREPROCESSORS['Sinumerik'].start_code(params()) + '\n'
    job.gcode_footer = CNCJobObject.gcode_footer

    text = CNCJobObject.export_gcode(job, postamble='M9', to_file=True).getvalue()
    path = tmp_path / 'job.MPF'
    CNCJobObject.export_gcode(job, filename=str(path), postamble='M9')
    saved = path.read_text(encoding='utf-8')

    assert saved == text
    assert saved.startswith('G291\n') and saved.rstrip().endswith('M30')

def test_tinyg_complete_file_ends_with_m02_after_the_postamble():
    from tests.test_preprocessors import params

    job = _job('Geometry', 'TinyG')
    job.multitool = False
    job.gcode = 'G00 X1.0000 Y2.0000\n'
    job.gc_start = PREPROCESSORS['TinyG'].start_code(params()) + '\n'
    job.gcode_footer = CNCJobObject.gcode_footer

    text = CNCJobObject.export_gcode(job, preamble='(PREAMBLE)', postamble='M9\n(APPEND: shutdown)',
                                     to_file=True).getvalue()

    lines = text.rstrip('\n').splitlines()
    # plain text: no TinyG queue-control '%' and no program number
    assert '%' not in text and not re.search(r'^O\d+', text, re.M)
    assert text.count('M02') == 1
    assert lines[-1] == 'M02'
    assert text.index('G21') < text.index('(PREAMBLE)') < text.index('G00 X1.0000') < text.index('M9') < \
        text.index('M02')

def test_buildbotics_complete_file_ends_with_m02_after_the_postamble():
    from tests.test_preprocessors import params

    job = _job('Geometry', 'Buildbotics')
    job.multitool = False
    job.gcode = 'G00 X1.0000 Y2.0000\nM5\n'
    job.gc_start = PREPROCESSORS['Buildbotics'].start_code(params()) + '\n'
    job.gcode_footer = CNCJobObject.gcode_footer

    text = CNCJobObject.export_gcode(job, preamble='(PREAMBLE)', postamble='M9\n(APPEND: shutdown)',
                                     to_file=True).getvalue()

    lines = text.rstrip('\n').splitlines()
    # a plain .nc program: no queue-control '%' and no program number
    assert '%' not in text and not re.search(r'^O\d+', text, re.M)
    # one executable program end, after the postamble; the start comment also names M02
    assert sum(1 for line in lines if line.strip() == 'M02') == 1
    assert lines[-1] == 'M02'
    assert text.index('G21') < text.index('(PREAMBLE)') < text.index('G00 X1.0000') < text.index('M5') < \
        text.index('M9') < text.rindex('M02')

@pytest.mark.parametrize('units,word,feed,plunge', [('MM', 'G71', '250.00', '60.00'),
                                                    ('IN', 'G70', '2500.00', '600.00')])
def test_heidenhain_iso_complete_file_envelope_feeds_and_restarts(units, word, feed, plunge):
    from tests.test_preprocessors import params

    pp = PREPROCESSORS['Heidenhain_ISO']
    p = params(units=units, feedrate=250.0, z_feedrate=60.0, xy_toolchange=[5.0, 7.0])
    body = '\n'.join([pp.startz_code(p), pp.toolchange_code(p), pp.spindle_code(p), pp.rapid_code(p),
                      pp.down_code(p), pp.linear_code(p), pp.up_to_zero_code(p), pp.lift_code(p),
                      pp.spindle_stop_code(p), pp.end_code(p)])
    job = _job('Geometry', 'Heidenhain_ISO')
    job.multitool = False
    job.gcode = body + '\n'
    job.gc_start = pp.start_code(p) + '\n'
    # the export uses the unit word written into the saved start block, not the settings of the application
    job.units = 'IN' if units == 'MM' else 'MM'
    job.gcode_footer = CNCJobObject.gcode_footer

    text = CNCJobObject.export_gcode(job, preamble='; PREAMBLE', postamble='; POSTAMBLE', to_file=True).getvalue()
    lines = text.rstrip('\n').splitlines()

    assert lines[0] == '%%FLATCAM %s *' % word
    assert lines[-1] == 'N99999999 %%FLATCAM %s *' % word
    assert text.index('; PREAMBLE') < text.index('; POSTAMBLE') < text.rindex('M02') < text.index('N99999999')
    assert len(re.findall(r'^N\d+ M02 \*$', text, re.M)) == 1
    # the X-Y and both Z cutting moves carry the feed; in G70 it is programmed in 1/10 inch/min
    assert re.search(r'^N\d+ G01 X\S+ Y\S+ F%s \*$' % re.escape(feed), text, re.M)
    assert re.search(r'^N\d+ G01 Z-1.5000 F%s \*$' % re.escape(plunge), text, re.M)
    assert re.search(r'^N\d+ G01 Z0 F%s \*$' % re.escape(plunge), text, re.M)
    # inch coordinates stay inches; only the feed is multiplied by ten
    assert 'X1.0000 Y2.0000' in text
    # every interior block is numbered and ends with the DIN/ISO block delimiter
    for line in lines[1:]:
        assert re.match(r'^N\d+ |^N99999999 ', line), line
        assert line.endswith('*'), line
    # the mandatory stop is followed by the tool call (number and speed of the changed tool) and the spindle start
    assert text.index('M00') < text.index('T1 G17 S10000') < text.index('M03') < text.index('G01 X')
    assert re.findall(r'(?<![A-Za-z])T\d+[^*]*', text) == ['T1 G17 S10000 ']
    assert not re.search(r'(?<![A-Za-z])M0?6\b', text)

def test_heidenhain_iso_saved_file_matches_the_stringio_and_keeps_an_explicit_end(tmp_path):
    from tests.test_preprocessors import params

    pp = PREPROCESSORS['Heidenhain_ISO']
    p = params(units='IN', feedrate=10.0, z_feedrate=6.0)
    job = _job('Geometry', 'Heidenhain_ISO')
    job.multitool = False
    job.gcode = pp.linear_code(p) + '\n'
    job.gc_start = pp.start_code(p) + '\n'
    job.gcode_footer = CNCJobObject.gcode_footer

    text = CNCJobObject.export_gcode(job, postamble='M30', to_file=True).getvalue()
    path = tmp_path / 'job.I'
    CNCJobObject.export_gcode(job, filename=str(path), postamble='M30')
    saved = path.read_text(encoding='utf-8')

    assert saved == text
    assert saved.splitlines()[0] == '%FLATCAM G70 *'
    assert saved.rstrip().endswith('N99999999 %FLATCAM G70 *')
    # the user's M30 is the program end: no second M02 is added before the envelope
    assert not re.search(r'^N\d+ M02 \*$', saved, re.M)
    assert re.search(r'^N\d+ M30 \*$', saved, re.M)

@pytest.mark.parametrize('postamble,ends', [
    ('N700 M30 *\nN710 ; finished *', 0),
    ('N700 M30 ; finished\n; trailing note', 0),
    ('; M30\nN710 ; M02 *', 1),
    ('FN 0: Q1 = +30\n; M30 is not a command', 1),
])
def test_heidenhain_iso_snippet_end_detection(postamble, ends):
    job = _job('Geometry', 'Heidenhain_ISO')
    job.gc_start = '%FLATCAM G71 *\n'
    text = CNCJobObject.export_gcode(job, postamble=postamble, to_file=True).getvalue()
    assert len(re.findall(r'^N\d+ M02 \*$', text, re.M)) == ends
    if not ends:
        assert 'N700 M30' in text

def test_heidenhain_iso_numbered_comments_do_not_split_blocks_or_change_expressions():
    job = _job('Geometry', 'Heidenhain_ISO')
    job.gc_start = '%FLATCAM G71 *\n'
    text = CNCJobObject.export_gcode(
        job, preamble='N700 ; a * b~ *\nN710 FN 3: Q1 = +2 * +3 *', to_file=True).getvalue()
    comment = next(line for line in text.splitlines() if line.startswith('N700 '))
    assert comment.count('*') == 1 and not comment[:-1].rstrip().endswith('~')
    assert 'N710 FN 3: Q1 = +2 * +3 *' in text

@pytest.mark.parametrize('units,multidepth,extracut', [('MM', False, False), ('IN', True, True)])
def test_heidenhain_iso_generated_milling_restarts_and_keeps_motion_feeds(units, multidepth, extracut):
    from shapely import LinearRing

    job = gcode_job('Heidenhain_ISO')
    job.units = units
    job.app.options['tools_mill_optimization_type'] = 'R'
    geo = SimpleNamespace(solid_geometry=[LinearRing([(1, 1), (2, 1), (2, 2), (1, 1)])],
                          obj_options={'name': 'g'})
    body, start = job.generate_from_geometry_2(
        geo, tooldia=0.1, z_cut=-0.2, z_move=2, feedrate=30, feedrate_z=6, feedrate_rapid=100,
        spindlespeed=10000, toolchange=True, toolchangez=15, toolchangexy='5, 7',
        multidepth=multidepth, depthpercut=0.1, extracut=extracut, extracut_length=0.1,
        endz=1, endxy='8, 9', pp_geometry_name='Heidenhain_ISO', is_first=True)
    exported = _job('Geometry', 'Heidenhain_ISO')
    exported.multitool = False
    exported.gcode, exported.gc_start = body, start
    text = _export(exported)
    spindle_on = False
    cuts = 0
    for line in text.splitlines()[1:-1]:
        code = re.sub(r'^N\d+\s+', '', line).split(';', 1)[0].rstrip(' *')
        if code in ('M00', 'M05'):
            spindle_on = False
        elif code in ('M03', 'M04'):
            spindle_on = True
        elif code.startswith('G01 '):
            assert spindle_on, line
            feed = 6 if ' Z' in code else 30
            assert code.endswith(' F%.2f' % (feed * (10 if units == 'IN' else 1))), line
            cuts += 1
    assert cuts > 3
    assert text.index('G00 Z2.0000', text.index('M05', text.index('M03'))) < text.index('G00 X8.0000 Y9.0000')
    assert text.index('G00 X8.0000 Y9.0000') < text.index('G00 Z1.0000')

def test_a_name_with_parentheses_stays_inside_the_header_comment():
    # GRBL, FluidNC and LinuxCNC end a '( ... )' comment at the first ')': 'board (1)' left G-code text on the line
    def header(name, ppname):
        job = _job('Geometry', ppname)
        job.obj_options['name'] = name
        job.units = 'MM'
        job.app.version = job.app.version_date = '1'
        return CNCJobObject.gcode_header(job)

    for ppname in ('default', 'Toolchange_Probe_MACH3', 'Fanuc', 'Haas', 'FluidNC', 'LinuxCNC', 'Mach3', 'Mach4',
                   'PlanetCNC', 'UCCNC'):
        for name, written in (('board (1)-F_Cu.gbr_iso_cnc', 'board [1]-F_Cu.gbr_iso_cnc'),
                              ('job\nG0 X0', 'job G0 X0')):
            text = header(name, ppname)
            assert '(Name: %s)\n' % written in text
            # what the controller reads outside the comments
            assert [line for line in text.splitlines() if re.sub(r'\([^)]*\)', '', line).strip()] == []
    # a ';' comment runs to the end of the line: it keeps the name as it is
    assert ';Name: board (1)\n' in header('board (1)', 'Marlin')

MILL_FILE = ("G21\r\nG90\r\n(Tool: 1 -> Dia: 0.8)\r\nT1\r\nM3 S10000\r\nG0 Z2\r\nG0 X1 Y1\r\n"
             "G1 Z-0.1 F100\r\nG1 X10 Y1 F200\r\nG0 Z2\r\nM5\r\nT2\r\nG0 X3 Y3\r\nG1 Z-0.1 F100\r\nG1 X3 Y9 F200\r\n"
             "G0 Z2\r\n")

DRILL_FILE = ("(Type: G-code from Excellon)\n(Tool: 1 -> Dia: 0.8)\nG21\nG90\nT1\nM6\nG0 Z2\nG0 X1 Y1\nG1 Z-1.7\n"
              "G0 Z2\nG0 X5 Y1\nG1 Z-1.7\nG0 Z2\n")

# the keys of a tool of a native job: Milling (Geometry) and Drilling (Excellon)
MILL_KEYS = {'tooldia', 'data', 'gcode', 'gcode_parsed', 'solid_geometry'}

DRILL_KEYS = MILL_KEYS | {'drills', 'slots', 'nr_drills', 'nr_slots', 'offset'}

def _open_gcode(tmp_path, content, units='MM', name='job.nc'):
    from unittest.mock import patch
    from appHandlers.appIO import appIO
    from tests.helpers_objects import make_app_0b

    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    app = make_app_0b()
    app.app_units = units
    app.new_project_signal = Mock()
    app.file_opened = Mock()
    app.file_saved = Mock()
    jobs = []

    def new_object(kind, job_name, initialize, *args, **kwargs):
        job = CNCJobObject(job_name, app)
        if initialize(job, app) == 'fail':
            return 'fail'
        jobs.append(job)
        return job

    app.app_obj.new_object.side_effect = new_object
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        result = appIO(app).open_gcode(str(path), plot=False)
    return app, (jobs[0] if jobs else result)

def test_opened_gcode_is_a_native_like_multitool_job_with_the_text_of_the_file(tmp_path):
    from tests.helpers_objects import _headless_qt  # noqa: F401

    app, job = _open_gcode(tmp_path, MILL_FILE)

    assert job.gcode_imported is True and job.multitool is True
    assert job.obj_options['type'] == 'Geometry' and job.obj_options['gcode_units'] == 'MM'
    assert job.used_tools == [1, 2]
    assert MILL_KEYS <= set(job.tools[1])
    assert job.tools[1]['tooldia'] == pytest.approx(0.8)
    assert job.tools[1]['data']['tools_mill_cutz'] == pytest.approx(-0.1)
    assert job.tools[1]['data']['tools_mill_ppname_g'] == 'default'
    # the second tool has no diameter in the file: the default mill tool
    assert job.tools[2]['tooldia'] == pytest.approx(float(str(app.options['tools_mill_tooldia']).split(',')[0]))
    assert len(job.gcode_parsed) == len(job.tools[1]['gcode_parsed']) + len(job.tools[2]['gcode_parsed']) > 0
    assert job.solid_geometry
    # the blocks are the slices of the file; the text is the file, byte for byte (the CRLF too)
    assert job.tools[1]['gcode'] + job.tools[2]['gcode'] == MILL_FILE
    assert job.gcode == MILL_FILE and job.source_file == MILL_FILE
    assert job.export_gcode(to_file=True).getvalue() == MILL_FILE

    # in other units than the application the job is converted but its text is not
    _app, inch_job = _open_gcode(tmp_path, MILL_FILE, units='IN')
    assert inch_job.units.upper() == 'IN' and inch_job.obj_options['gcode_units'] == 'MM'
    assert inch_job.tools[1]['tooldia'] == pytest.approx(0.8 / 25.4)
    assert inch_job.obj_options['xmax'] == pytest.approx(10 / 25.4)
    assert inch_job.gcode == MILL_FILE

    # the export writes the bytes of the file
    out = tmp_path / 'out.nc'
    job.export_gcode(filename=str(out))
    assert out.read_bytes() == MILL_FILE.encode()
    out2 = tmp_path / 'out2.nc'
    job.export_gcode_handler(str(out2), rename_object=False)
    assert out2.read_bytes() == MILL_FILE.encode()

    # the snippets are added only when the checkbox is checked
    job.prepend_snippet, job.append_snippet = 'PRE', 'POST'
    for checked, expected in ((True, 'PRE\n' + MILL_FILE + '\nPOST'), (False, MILL_FILE)):
        job.ui = SimpleNamespace(snippets_cb=Mock(get_value=Mock(return_value=checked)))
        job.on_update_source_file()
        assert job.source_file == expected
    assert job.export_gcode(to_file=True).getvalue() == MILL_FILE

def test_file_that_is_not_utf8_opens_and_a_file_without_moves_fails_with_one_message(tmp_path):
    latin = "(caf\xe9 board)\nG21\nT1\nG0 X1 Y1\nG1 Z-0.1 F100\nG1 X5\n".encode('latin-1')
    _app, job = _open_gcode(tmp_path, latin)
    assert job.gcode_imported is True and job.tools[1]['gcode_parsed']
    # it is written back in its own encoding
    out = tmp_path / 'latin_out.nc'
    job.export_gcode(filename=str(out))
    assert out.read_bytes() == latin

    app, result = _open_gcode(tmp_path, 'just some text\nnot gcode at all\n')
    assert result == 'fail'
    errors = [call.args[0] for call in app.inform.emit.call_args_list if call.args[0].startswith('[ERROR')]
    assert len(errors) == 1 and 'not GCODE' in errors[0]

@pytest.mark.parametrize("content, kind, dia_option, cutz_option", [
    ("G0 X0 Y0\nG1 X10 Y0 F100\n", 'Geometry', 'tools_mill_tooldia', 'tools_mill_cutz'),
    ("G0 X0 Y0 Z1\nG1 Z-1 F50\nG0 Z1\nG0 X5\nG1 Z-1\nG0 Z1\n", 'Excellon', 'excellon_drill_tooldia', None),
])
def test_gcode_without_tool_data_opens_with_defaults_and_warns_once(tmp_path, content, kind, dia_option,
                                                                    cutz_option):
    app, job = _open_gcode(tmp_path, content)

    default = float(str(app.options[dia_option]).split(',')[0])
    assert job.obj_options['type'] == kind
    assert list(job.tools) == [1] and job.tools[1]['tooldia'] == pytest.approx(default) and default > 0
    if cutz_option:
        assert job.tools[1]['data'][cutz_option] == app.options[cutz_option]
    warnings = [c.args[0] for c in app.inform.emit.call_args_list
                if c.args[0].startswith('[WARNING_NOTCL]') and 'tool data' in c.args[0]]
    assert len(warnings) == 1 and '%g' % default in warnings[0] and 'G20/G21' in warnings[0]

BODY = 'G0 X1 Y1\nG1 Z-0.1 F100\nG1 X10 Y1 F200\nG0 Z2\nM5\n'

OLD_FILE = b'OLD G-CODE, STILL GOOD\r\n'

def _ui_app(**options):
    from tests.helpers_objects import make_app_0b

    app = make_app_0b(**options)
    app.preprocessors = PREPROCESSORS
    app.version, app.version_date = 'test', 'test'
    app.ui = MagicMock()
    app.resource_location = 'assets/resources'
    app.file_opened, app.file_saved = Mock(), Mock()
    app.dec_format = round
    return app

def _ui_job(app, init=None):
    """A Milling job as AppObject.new_object() makes it: the init function, then set_ui()."""
    from PyQt6 import QtWidgets

    # the Properties panel needs a QApplication; it is kept (a collected one ends the process)
    _ui_job.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        job = CNCJobObject('job', app)
    job.obj_options['type'] = 'Geometry'
    job.multitool = True
    job.used_tools = [1]
    job.tools = {1: {'tooldia': 0.1, 'data': {'tools_mill_ppname_g': 'default'}, 'gcode': BODY}}
    job.gc_start = 'G21\nG90\n'
    if init is not None:
        init(job)
    job.set_ui(CNCObjectUI(app=app))
    return job

def _save_both_ways(job, folder):
    """Save CNC Code into 'button.nc' and the Tcl write_gcode into 'tcl.nc': their results."""
    return (job.export_gcode_handler(str(folder / 'button.nc'), rename_object=False),
            job.export_gcode(filename=str(folder / 'tcl.nc')))

def test_a_failed_gcode_save_keeps_the_old_file_and_says_why(tmp_path):
    import errno
    import appObjects.CNCJobObject as cnc_module

    real_open = open

    def full_card_open(file, *args, **kwargs):
        # the SD card is full after a few characters
        handle = real_open(file, *args, **kwargs)
        real_write = handle.write

        def write(text):
            real_write(text[:20])
            raise OSError(errno.ENOSPC, 'No space left on device')

        handle.write = write
        return handle

    job = _ui_job(_ui_app())
    for name in ('button.nc', 'tcl.nc'):
        (tmp_path / name).write_bytes(OLD_FILE)

    with patch.object(cnc_module, 'open', full_card_open, create=True):
        assert _save_both_ways(job, tmp_path) == ('fail', 'fail')

    # before: the files were cut (the end code was missing) and the error went to the crash dialog
    assert sorted(p.name for p in tmp_path.iterdir()) == ['button.nc', 'tcl.nc']
    assert (tmp_path / 'button.nc').read_bytes() == OLD_FILE and (tmp_path / 'tcl.nc').read_bytes() == OLD_FILE
    errors = [c.args[0] for c in job.app.inform.emit.call_args_list if c.args[0].startswith('[ERROR_NOTCL]')]
    assert len(errors) == 2 and all('Could not save the file' in e and 'No space' in e for e in errors)
    job.app.file_saved.emit.assert_not_called()

EDIT = '\nM5 (laser off, added in the editor)\n'

def _editor_save(job, text):
    """The save of the G-code Editor (App.on_editing_finished -> AppGCodeEditor.update_fcgcode)."""
    from appEditors.appGCodeEditor import AppGCodeEditor

    editor = SimpleNamespace(app=job.app, gcode_obj=job, ui=MagicMock(), deactivate=Mock())
    editor.ui.gcode_editor_tab.code_editor.toPlainText.return_value = text
    AppGCodeEditor.update_fcgcode(editor, job)

def test_gcode_saved_in_the_editor_survives_a_new_panel_the_project_and_undo(tmp_path):
    from appCommon.ProjectFile import load_project_file, save_project_file
    from appHandlers.appHistory import object_state, restore_object_state
    from camlib import dict2obj, geometry_wkt_cache, project_json_default

    job = _ui_job(_ui_app())
    edited = job.source_file + EDIT
    _editor_save(job, edited)

    # before: set_ui() made the G-code again from the tools; the panel is made again when Qt deleted it
    job.set_ui(CNCObjectUI(app=job.app))
    assert job.source_file == edited
    assert job.export_gcode_handler(str(tmp_path / 'out.nc'), rename_object=False) is None
    assert (tmp_path / 'out.nc').read_text() == edited

    # project save and open (appIO.open_project), and the undo of a delete (HistoryManager.rebuild)
    data = {'objs': [job.to_dict()]}
    path = str(tmp_path / 'project.FlatPrj')
    save_project_file(path, data, default=project_json_default(geometry_wkt_cache(data)))
    entry = load_project_file(path, object_hook=dict2obj)['objs'][0]
    state = object_state(job)

    def open_init(new_job):
        new_job.from_dict(entry)
        new_job.tools = {int(k): v for k, v in new_job.tools.items()}
        new_job.is_loaded_from_project = True

    def undo_init(new_job):
        restore_object_state(new_job, state)
        new_job.is_loaded_from_project = True

    for init in (open_init, undo_init):
        assert _ui_job(_ui_app(), init).source_file == edited, init.__name__

def test_making_the_edited_gcode_again_asks_first_or_says_that_the_edits_are_lost():
    job = _ui_job(_ui_app())
    edited = job.source_file + EDIT
    _editor_save(job, edited)
    job.prepend_snippet = '(PRE)'

    # the snippets checkbox: No keeps the edits and puts the box back, Yes makes the G-code again
    job.ask_to_discard_edited_gcode = Mock(return_value=False)
    job.ui.snippets_cb.set_value(True)
    assert job.source_file == edited
    assert job.ui.snippets_cb.get_value() is False and job.snippets_enabled is False
    job.ask_to_discard_edited_gcode = Mock(return_value=True)
    job.ui.snippets_cb.set_value(True)
    assert '(PRE)' in job.source_file and EDIT not in job.source_file

    # a move (Set Origin, Move to Origin): the G-code must follow the new coordinates, the user is told
    _editor_save(job, edited)
    job.tools[1]['gcode'] = BODY.replace('X1 ', 'X6 ')
    assert job.regenerate_source() is True
    assert 'X6 ' in job.source_file and EDIT not in job.source_file
    warnings = [c.args[0] for c in job.app.inform.emit.call_args_list if c.args[0].startswith('[WARNING]')]
    assert len(warnings) == 1 and 'GCode Editor' in warnings[0]

PRE, POST = '(PREPEND: G28 home)', '(APPEND: M30)'

def test_no_snippets_in_the_gcode_while_the_snippets_checkbox_is_off():
    job = _ui_job(_ui_app(cncjob_prepend=PRE, cncjob_append=POST))
    assert job.ui.snippets_cb.get_value() is True and PRE in job.source_file and POST in job.source_file

    # before: an empty snippet was replaced by the one of the Preferences, so they stayed
    job.ui.snippets_cb.set_value(False)
    assert PRE not in job.source_file and POST not in job.source_file and BODY in job.source_file
    # a move, in a worker: snippets_enabled is the state of the box
    assert job.regenerate_source() is True
    assert PRE not in job.source_file and POST not in job.source_file
    job.ui.snippets_cb.set_value(True)
    assert PRE in job.source_file and POST in job.source_file

    # a job of an opened project made without snippets, now that the Preferences have snippets
    made = _ui_job(_ui_app())
    entry = made.to_dict()

    def open_init(new_job):
        new_job.from_dict(entry)
        new_job.is_loaded_from_project = True

    opened = _ui_job(_ui_app(cncjob_prepend=PRE, cncjob_append=POST), open_init)
    assert opened.ui.snippets_cb.get_value() is False and opened.source_file == made.source_file

def test_saving_several_jobs_writes_each_file_in_the_chosen_folder(tmp_path, monkeypatch):
    # Save in the project context menu with several CNC jobs selected: each file is named after its job, and the
    # names of a project file can hold a path
    from appMain import App

    written = []

    class Job:
        kind = 'cncjob'

        def __init__(self, name):
            self.obj_options = {'name': name}

        def read_form(self):
            pass

        def export_gcode_handler(self, filename, is_gcode=True, rename_object=True):
            written.append(filename)

    names = ['board', '..\\..\\x', '../y', 'C:\\z', 'a:b', 'a_b', 'NUL']
    folder = tmp_path / 'chosen'
    app = SimpleNamespace(collection=SimpleNamespace(get_selected=lambda: [Job(name) for name in names]),
                          options={'cncjob_save_filters': 'G-Code Files .nc (*.nc)'}, defaults={},
                          get_last_save_folder=lambda: str(tmp_path))
    monkeypatch.setattr('appMain.FCFileSaveDialog', SimpleNamespace(
        get_saved_filename=lambda **kwargs: (folder.as_posix() + '/multi_save.nc', '')))

    App.on_project_context_save(app)

    assert [os.path.dirname(os.path.abspath(path)) for path in written] == [os.path.abspath(folder)] * len(names)
    files = [os.path.basename(path) for path in written]
    # a plain name is kept; 2 names that become the same file name get 2 files
    assert files[0] == 'board.nc' and len(set(files)) == len(names)


# ==============================================================================================================
# test_cncjob_gcode_speed: CNC job from a Geometry: the faster code gives the same G-code, the same parsed G-code and the same copies.
# ==============================================================================================================

def noisy_paths(count, seed=3):
    """Open and closed paths with many points close to straight lines (they simplify to a few points)."""
    rng = np.random.default_rng(seed)
    paths = []
    for k in range(count):
        x0, y0 = (k % 20) * 3.0, (k // 20) * 3.0
        t = np.linspace(0, 1, 40 + k % 30)
        xs = x0 + 2 * t + rng.normal(0, 0.0004, len(t))
        ys = y0 + np.where(t < 0.5, t, 1 - t) + rng.normal(0, 0.0004, len(t))
        coords = np.column_stack([xs, ys])
        if k % 3 == 0:
            paths.append(LinearRing(np.vstack([coords, [[x0 + 1, y0 - 1]]])))
        else:
            paths.append(LineString(coords))
    return paths

def gcode_job(pp_name, app=None):
    app = app or make_app()
    module = __import__('preprocessors.%s' % pp_name, fromlist=[pp_name])
    app.preprocessors[pp_name] = getattr(module, pp_name)()
    app.exc_areas = SimpleNamespace(travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    job = CNCjob(app, pp_geometry_name=pp_name)
    # the start code reads the bounds and the tool diameter (the CNC job tools set them before the G-code is made)
    job.obj_options = {'name': 'job', 'type': 'Geometry', 'xmin': 0, 'ymin': 0, 'xmax': 10, 'ymax': 10,
                       'tool_dia': 0.1}
    # as CNCJobObject sets it; GRBL_laser_z reads it in its start code
    job.multigeo = False
    job.coords_decimals = 4
    job.fr_decimals = 2
    job.z_cut = -0.1
    job.z_move = 2.0
    return job

def milling_tools(paths, **data_changes):
    data = dict(AppDefaults.factory_defaults)
    data.update(tools_mill_tooldia=0.1, tools_mill_offset_value=0.0, tools_mill_optimization_type='R',
                tools_mill_multidepth=False, tools_mill_extracut=False, tools_mill_cutz=-0.05,
                tools_mill_travelz=2.0, tools_mill_feedrate=300.0, tools_mill_spindlespeed=1000.0)
    data.update(data_changes)
    return {1: {'tooldia': 0.1, 'data': data, 'solid_geometry': paths}}

# ---------------------------------------------------------------------------------------------------------------------
# Milling G-code: travel Z, depth step, dwell, path ends, exclusion areas, first tool, lost paths
# ---------------------------------------------------------------------------------------------------------------------
BOX = shapely.box(4.0, -1.0, 6.0, 1.0)

TWO_LINES = [LineString([(0, 0), (10, 0)]), LineString([(10, 1), (20, 1)])]

def recording_exclusion(job, areas=()):
    calls = []

    def travel_coordinates(**kw):
        calls.append(kw)
        return [(None, kw['end_point'])]

    job.app.exc_areas = SimpleNamespace(exclusion_areas_storage=list(areas), travel_coordinates=travel_coordinates)
    return calls

def messages(job):
    return [str(call.args[0]) for call in job.app.inform.emit.call_args_list]

def single_path_gcode(job, paths, **changes):
    job.app.options['tools_mill_optimization_type'] = changes.pop('opt_type', 'R')
    geo = SimpleNamespace(solid_geometry=paths, obj_options={'name': 'g'})
    kwargs = dict(tooldia=0.1, z_cut=-0.1, z_move=2.0, feedrate=300.0, feedrate_z=100.0, feedrate_rapid=1000.0,
                  pp_geometry_name='default', is_first=True, toolchangez=15.0, startz=None, endz=2.0)
    kwargs.update(changes)
    result = job.generate_from_geometry_2(geo, **kwargs)
    return result if result == 'fail' else result[0]

def multi_geo_gcode(paths, pp_name='default', job=None, uid=1, data=None, **kwargs):
    job = job or gcode_job(pp_name)
    tools = milling_tools(paths, tools_mill_ppname_g=pp_name, **(data or {}))
    tools[uid] = tools.pop(1)
    kwargs.setdefault('is_first', True)
    kwargs.setdefault('is_last', True)
    result = job.geometry_tool_gcode_gen(uid, tools, (0, 0), (0, 0), 0.005, **kwargs)
    return job, (result if result == 'fail' else result[0])

def run_with_deadline(function, seconds=15):
    import threading
    box = []
    thread = threading.Thread(target=lambda: box.append(function()), daemon=True)
    thread.start()
    thread.join(seconds)
    assert box, 'the G-code generation never ended'
    return box[0]

class TestMillingGcodeFixes:

    @pytest.mark.parametrize('pp_name', ['default', 'Roland_MDX_20'])
    def test_generated_estimate_counts_motion_and_supported_dwell(self, pp_name):
        job, code = multi_geo_gcode([LineString([(0, 0), (10, 0)])], pp_name=pp_name,
                                    data={'tools_mill_dwell': True, 'tools_mill_dwelltime': 30,
                                          'tools_mill_endxy': None, 'tools_mill_endz': 2,
                                          'tools_mill_feedrate_z': 100, 'tools_mill_feedrate_rapid': 600})
        if pp_name in ('hpgl', 'GRBL_laser'):
            assert job.travel_distance == pytest.approx(20)
            assert job.routing_time == pytest.approx(10 / 300 + 10 / 600)
        elif pp_name == 'GRBL_laser_z':
            assert job.travel_distance == pytest.approx(22)
        elif pp_name == 'Check_points':
            assert job.travel_distance == pytest.approx(25.9)
            assert job.routing_time == pytest.approx(23.95 / 600 + 1.95 / 100)
        elif pp_name == 'Roland_MDX_20':
            assert job.travel_distance == pytest.approx(26.1, abs=0.001)
            # The MDX-20 clamps all three configured rates to 15 mm/s, even in an inch job.
            assert job.routing_time == pytest.approx(26.1 / (900 / 25.4), abs=0.001)
        else:
            assert job.travel_distance == pytest.approx(26.1)
            assert job.routing_time == pytest.approx(14.05 / 600 + 2.05 / 100 + 10 / 300 + 0.5)

    @pytest.mark.parametrize('multidepth', [True])
    def test_single_geometry_negative_travel_z_lifts_up(self, multidepth):
        job = gcode_job('default')
        gcode = single_path_gcode(job, TWO_LINES, z_move=-2.0, multidepth=multidepth, depthpercut=0.05)
        assert 'Z-2.0' not in gcode
        assert 'G00 Z2.0000' in gcode

    def test_multi_depth_with_zero_step_cuts_in_one_pass(self):
        __, gcode = run_with_deadline(lambda: multi_geo_gcode(
            [TWO_LINES[0]], data=dict(tools_mill_multidepth=True, tools_mill_depthperpass=0.0,
                                      tools_mill_cutz=-0.3)))
        assert 'Z-0.3000' in gcode

    @pytest.mark.parametrize('step', [0])
    def test_multi_pass_with_zero_step_ends(self, step):
        job = gcode_job('default')
        job.z_depthpercut = step
        gcode, __ = run_with_deadline(
            lambda: job.create_gcode_multi_pass(TWO_LINES[0], 0.1, False, 0.0, 0, job.pp_geometry, 2.0))
        assert 'Z-0.1000' in gcode
        assert json.loads(json.dumps(job.to_dict(), default=camlib.to_dict))['z_depthpercut'] == step

    @pytest.mark.parametrize('single', [True, False])
    def test_multi_depth_job_can_be_saved_and_loaded(self, single, tmp_path):
        job = gcode_job('default')
        if single:
            gcode = single_path_gcode(job, TWO_LINES, z_cut=-2.0, multidepth=True, depthpercut=0.7)
        else:
            job, gcode = multi_geo_gcode(TWO_LINES, job=job, data=dict(
                tools_mill_multidepth=True, tools_mill_depthperpass=0.7, tools_mill_cutz=-2.0))
        assert re.findall(r'G01 Z(-?[\d.]+)', gcode) == ['-0.7000', '-1.4000', '-2.0000'] * 2
        data = {'objs': [job.to_dict()]}
        filename = str(tmp_path / 'multi_depth.FlatPrj')
        save_project_file(filename, data, default=camlib.project_json_default(camlib.geometry_wkt_cache(data)))
        loaded = load_project_file(filename, object_hook=camlib.dict2obj)['objs'][0]
        assert loaded['z_depthpercut'] == 0.7
        assert loaded['z_cut'] == -2.0
        assert loaded['gcode'] == gcode

    def test_extra_cut_goes_on_along_the_path_after_the_start_point(self):
        # the isolation ring of a 0.3 x 0.3 mm pad for a 0.1 mm tool, Extra Cut 1.0: before, the tool went from the
        # start point straight to a later vertex (across the corner, into the pad) and walked back
        pad = shapely.box(0, 0, 0.3, 0.3)
        ring = LinearRing(pad.buffer(0.05).exterior.coords)
        job = gcode_job('default')
        job.z_cut = -0.05
        lines = job.linear2gcode_extra(ring, 0.1, 1.0, up=False).splitlines()
        # the extra cut: from the rapid move to its start, after the cut of the whole path
        extra = lines[max(i for i, line in enumerate(lines) if line.startswith('G00 X')):]
        cut = LineString([tuple(map(float, xy)) for line in extra
                          for xy in re.findall(r'X(-?[\d.]+) Y(-?[\d.]+)', line)])
        assert ring.buffer(2e-4).contains(cut)
        assert cut.distance(pad) >= 0.05 - 2e-4
        assert substring(ring, 0, 0.5).difference(cut.buffer(2e-4)).length < 1e-6
        # it ends at the start point, where the next pass of a multi-depth cut goes on
        assert cut.coords[-1] == pytest.approx(ring.coords[0], abs=2e-4)

    def test_dwell_with_zero_time_makes_no_dwell_line(self):
        job, gcode = multi_geo_gcode([TWO_LINES[0]], data=dict(tools_mill_dwell=True, tools_mill_dwelltime=0.0))
        assert gcode != 'fail'
        assert 'G4' not in gcode
        assert gcode == multi_geo_gcode([TWO_LINES[0]], data=dict(tools_mill_dwell=False))[1]

        job, with_time = multi_geo_gcode([TWO_LINES[0]], data=dict(tools_mill_dwell=True, tools_mill_dwelltime=1.5))
        assert 'G4 P1.5' in with_time


    @pytest.mark.parametrize('flag', [True, False])
    def test_exclusion_flag_of_tool_is_honored(self, flag):
        job = gcode_job('default')
        calls = recording_exclusion(job)
        multi_geo_gcode(TWO_LINES, job=job, data=dict(tools_mill_area_exclusion=flag))
        assert bool(calls) is flag


    def test_first_tool_uses_start_z_by_position_not_by_uid(self):
        data = dict(tools_mill_startz=1.5, tools_mill_toolchangez=15.0)
        job, first = multi_geo_gcode([TWO_LINES[0]], uid=5, toolchange=True, is_first=True, data=data)
        assert 'G00 Z1.5000\nG00 X0.0000 Y0.0000\nT5' in first
        job, later = multi_geo_gcode([TWO_LINES[0]], uid=1, toolchange=True, is_first=False, data=data)
        assert 'G00 Z15.0000\nG00 X0.0000 Y0.0000\nT1' in later

    @pytest.mark.parametrize('opt_type, single', [('T', False)])
    def test_paths_with_the_same_start_point_are_all_cut(self, opt_type, single):
        paths = [LineString([(0, 0), (5, 0)]), LineString([(0, 0), (0, 5)]), LineString([(8, 8), (9, 9)])]
        with patch.object(camlib, 'HAS_ORTOOLS', True):
            if single:
                job = gcode_job('default')
                recording_exclusion(job)
                gcode = single_path_gcode(job, paths, opt_type=opt_type)
            else:
                __, gcode = multi_geo_gcode(paths, data=dict(tools_mill_optimization_type=opt_type))
        for end in ('X5.0000 Y0.0000', 'X0.0000 Y5.0000', 'X9.0000 Y9.0000'):
            assert end in gcode

# ---------------------------------------------------------------------------------------------------------------------
# Validated Voronoi paths: the certified geometry reaches the G-code grid unsimplified
# ---------------------------------------------------------------------------------------------------------------------
VALIDATED = dict(tools_iso_validated_paths=True, tools_iso_voronoi_coord_decimals=4,
                 tools_iso_voronoi_precision_margin=0.0)

NEAR_LINE = [(0, 0), (5, 0.001), (10, 0)]

def validated_data(paths, diameter=0.1, units='IN'):
    return dict(VALIDATED, tools_iso_voronoi_path_signature=sha256(
        shapely.GeometryCollection(paths).normalize().wkb).hexdigest(),
        tools_iso_voronoi_units=units, tools_iso_voronoi_tooldia=diameter)

@pytest.mark.parametrize('change', ['edit', 'units', 'diameter', 'bed', 'legacy', 'segment', 'extra-cut'])
@pytest.mark.parametrize('single', [False, True])
def test_voronoi_generation_data_cannot_authorize_changed_output(change, single):
    paths = [LineString(NEAR_LINE)]
    data = dict(VALIDATED, tools_iso_voronoi_path_signature=sha256(
        shapely.GeometryCollection(paths).normalize().wkb).hexdigest(),
        tools_iso_voronoi_units='MM', tools_iso_voronoi_tooldia=0.1)
    job = gcode_job('default')
    job.units = 'MM'
    diameter = 0.1
    if change == 'edit':
        paths = [shapely.affinity.scale(paths[0], xfact=0.5, yfact=0.5, origin=(0, 0))]
    elif change == 'units':
        job.units = 'IN'
    elif change == 'diameter':
        diameter = 0.2
    elif change == 'bed':
        job._bed_skew_x = 0.01
    elif change == 'segment':
        job.seg_x = 0.25
    elif change == 'extra-cut':
        data.update(tools_mill_extracut=True, tools_mill_extracut_length=0.5)
    else:
        data.pop('tools_iso_voronoi_path_signature')
    data['tools_mill_tooldia'] = diameter
    if single:
        geo = SimpleNamespace(solid_geometry=paths, obj_options={'name': 'g'}, tools={1: {'data': data}})
        result = job.generate_from_geometry_2(
            geo, tooldia=diameter, pp_geometry_name='default',
            extracut=data.get('tools_mill_extracut', False),
            extracut_length=data.get('tools_mill_extracut_length', 0.0))
    else:
        __, result = multi_geo_gcode(paths, job=job, data=data)
    assert result == 'fail'

def test_validated_paths_are_not_simplified_again():
    paths = [LineString(NEAR_LINE)]
    __, code = multi_geo_gcode(paths, data=validated_data(paths))
    assert 'X5.0000 Y0.0010' in code

    __, plain = multi_geo_gcode([LineString(NEAR_LINE)])
    assert 'X5.0000 Y0.0010' not in plain

@pytest.mark.parametrize('single', [False, True])
def test_voronoi_rejects_output_with_an_uncertified_coordinate_grid(single):
    paths = [LineString(NEAR_LINE)]
    data = validated_data(paths)
    job = gcode_job('Roland_MDX_20')
    if single:
        geo = SimpleNamespace(solid_geometry=paths, obj_options={'name': 'g'}, tools={1: {'data': data}})
        result = job.generate_from_geometry_2(
            geo, tooldia=0.1, z_cut=-0.05, z_move=2.0, pp_geometry_name='Roland_MDX_20')
    else:
        __, result = multi_geo_gcode(paths, pp_name='Roland_MDX_20', job=job, data=data)
    assert result == 'fail'

@pytest.mark.parametrize('data', [
    dict(VALIDATED, tools_mill_multidepth=True, tools_mill_depthperpass=0.01),
    dict(VALIDATED, tools_mill_extracut=True, tools_mill_extracut_length=0.0),
], ids=['multidepth', 'extra-cut'])
def test_validated_paths_are_not_simplified_in_multidepth_and_extra_cut(data):
    ring = LinearRing([(0, 0), (5, 0.001), (10, 0), (10, 10), (0, 10)])
    settings = validated_data([ring])
    settings.update(data)
    __, code = multi_geo_gcode([ring], data=settings)
    assert 'X5.0000 Y0.0010' in code

def test_validated_paths_refuse_a_coarser_output_grid():
    job = gcode_job('default')
    job.coords_decimals = 3
    __, result = multi_geo_gcode([LineString([(0, 0), (1, 0)])], job=job, data=dict(VALIDATED))
    assert result == 'fail'
    assert any('coordinate decimals' in str(call.args[0]) for call in job.app.inform.emit.call_args_list)

def test_single_geometry_entry_does_not_simplify_validated_paths():
    job = gcode_job('default')
    recording_exclusion(job)
    job.app.options['tools_mill_optimization_type'] = 'R'
    geo = SimpleNamespace(solid_geometry=[LineString(NEAR_LINE)], obj_options={'name': 'g'},
                          tools={1: {'data': validated_data([LineString(NEAR_LINE)])}})
    result = job.generate_from_geometry_2(
        geo, tooldia=0.1, tolerance=0.005, z_cut=-0.1, z_move=2.0, feedrate=300.0, feedrate_z=100.0,
        feedrate_rapid=1000.0, pp_geometry_name='default', tool_no=1, is_first=True)
    assert result != 'fail'
    assert 'X5.0000 Y0.0010' in result[0]

# ---------------------------------------------------------------------------------------------------------------------
# Parse of any G-code (not only the FlatCAM made one): arcs, modal words, G91, comments, many drills, imported jobs
# ---------------------------------------------------------------------------------------------------------------------
def parsed_job(text, typ='Geometry', exc_tools=None, imported=False):
    job = gcode_job('default')
    job.units = 'MM'
    job.obj_options['type'] = typ
    job.pp_excellon_name = 'default'
    job.exc_tools = exc_tools or {}
    job.decimals = 4
    job.steps_per_circle = 64
    job.xy_toolchange = '0,0'
    job.gcode = text
    if imported:
        job.gcode_imported = True
    job.gcode_parse(tool_data={})
    return job

def points_of(job):
    return [pt for item in job.gcode_parsed for pt in item['geom'].coords]

class TestGcodeParseAnyGcode:
    def test_arcs(self):
        # a missing I or J is 0; R-format half circle
        for arc_line, end in (('G2 X10 Y0 I5', (10, 0)), ('G3 X0 Y10 J5', (0, 10)), ('G3 X10 Y0 R5', (10, 0))):
            assert points_of(parsed_job('G1 X0 Y0\n%s' % arc_line))[-1] == pytest.approx(end, abs=1e-9), arc_line
        # R > 0: the short arc (half circle here) on the side of its direction
        for arc_word, bulge_y in (('G2', 5), ('G3', -5)):
            ys = [y for __, y in points_of(parsed_job('G1 X0 Y0\n%s X10 Y0 R5' % arc_word))]
            assert (max(ys) if bulge_y > 0 else min(ys)) == pytest.approx(bulge_y, abs=0.05)
        # R < 0: the long arc
        short = parsed_job('G1 X0 Y0\nG3 X6 Y0 R5')
        long = parsed_job('G1 X0 Y0\nG3 X6 Y0 R-5')
        assert long.gcode_parsed[-1]['geom'].length > short.gcode_parsed[-1]['geom'].length
        # full circle
        job = parsed_job('G1 X0 Y0\nG2 X0 Y0 I5 J0')
        xs = [x for x, __ in points_of(job)]
        assert max(xs) - min(xs) == pytest.approx(10, abs=0.05)
        assert job.gcode_parsed[-1]['geom'].length == pytest.approx(2 * np.pi * 5, rel=0.01)

    def test_modal_words_incremental_moves_and_comments(self):
        assert points_of(parsed_job('G1 X1 Y1\nG90\nG54\nG17\nX2 Y2\nX3 Y3'))[-2:] == [(2, 2), (3, 3)]
        # the units line keeps its other words
        job = parsed_job('G21 G90 G1 X4 Y5')
        assert job.units == 'MM' and points_of(job)[-1] == (4, 5)
        job = parsed_job('G20 G91 G1 X1 Y1\nG90 G0 X0 Y0')
        assert job.units == 'IN' and points_of(job) == [(0, 0), (1, 1), (0, 0)]
        job = parsed_job('G90 G1 X1 Y1\nG91\nX1 Y2\nX1 Y2\nG90\nX0 Y0')
        assert points_of(job) == [(0, 0), (1, 1), (2, 3), (3, 5), (0, 0)]
        assert points_of(parsed_job('g1 x3 y4 (x9) ; y9\n(g1 x7 y7)\n;g1 x8')) == [(0, 0), (3, 4)]

class TestImportedJobParse:
    def test_imported_job_uses_the_import_parser_and_a_unit_change_scales_the_geometry_not_the_gcode(self):
        from appParsers import ParseGCode as module
        text = 'G21\nG90\nT1\nG0 X1 Y1\nG1 X5 Y1 F100\nT2\nG0 X0 Y0\nG1 X0 Y3 F100\n'
        job = parsed_job(text, imported=True)
        expected = module.parse_gcode(text, steps_per_circle=64, default_tooldia=0.4, target_units='MM')
        flat = [g for k in expected['tools'] for g in expected['tools'][k]['gcode_parsed']]
        assert [g['geom'].wkt for g in job.gcode_parsed] == [g['geom'].wkt for g in flat]
        assert job.gcode == text
        text = 'G21\nG90\nG0 X0 Y0\nG1 X10 Y0 F100\n'
        job = parsed_job(text, imported=True)
        job.solid_geometry = [g['geom'] for g in job.gcode_parsed]
        job.convert_units('IN')
        assert job.gcode == text
        assert max(x for x, __ in points_of(job)) == pytest.approx(10 / 25.4)

def test_long_line_is_segmented_for_autolevelling():
    # the autolevelling segments were made by a recursion, one level for each new point: a 300 mm line in steps of
    # 0.25 mm failed with RecursionError and the CNC job was not made
    job = gcode_job('default')
    job.seg_x = job.seg_y = 0.25
    gcode = job.linear2gcode(LineString([(0, 0), (300, 0)]), 0.1, tolerance=0)
    xs = [float(x) for x in re.findall(r'G01 X([-\d.]+) Y0\.0000', gcode)]
    assert len(xs) == 1200 and xs[-1] == 300.0
    assert np.diff([0.0] + xs) == pytest.approx(0.25)
    # a short line keeps its points
    assert np.array(job.segment([(0, 0), (1, 0.6)])) == pytest.approx(
        np.array([(0, 0), (0.25, 0.15), (0.5, 0.3), (0.75, 0.45), (1, 0.6)]))
    assert np.array(job.segment([(0, 0), (0.3, -1.2)])) == pytest.approx(
        np.array([(0, 0), (0.0625, -0.25), (0.125, -0.5), (0.1875, -0.75), (0.24375, -0.975), (0.3, -1.2)]))
