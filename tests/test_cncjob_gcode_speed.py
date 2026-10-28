"""CNC job from a Geometry: the faster code gives the same G-code, the same parsed G-code and the same copies.

Run from the repository root:
    .venv/Scripts/python -m pytest tests/test_cncjob_gcode_speed.py -q
"""
import re
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import shapely
from shapely import LinearRing, LineString, MultiLineString, Point
from shapely.ops import substring

import camlib
from camlib import CNCjob, deepcopy_keep_geometry, simplify_paths
from defaults import AppDefaults
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402


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


class TestDeepcopyKeepGeometry:
    def test_geometries_shared_containers_copied(self):
        line = LineString([(0, 0), (1, 1)])
        lines = [line, Point(2, 2)]
        src = {'solid_geometry': lines, 'again': lines, 'pair': (line, [3]), 'data': {'a': [1, 2], 'b': 'x'},
               'multi': MultiLineString([[(0, 0), (1, 0)]])}
        src['self'] = src
        dup = deepcopy_keep_geometry(src)

        assert dup['solid_geometry'][0] is line
        assert dup['pair'][0] is line
        assert dup['multi'] is src['multi']
        assert dup['solid_geometry'] is not lines
        # a container referenced twice is copied once, as deepcopy() does
        assert dup['again'] is dup['solid_geometry']
        assert dup['self'] is dup
        dup['data']['a'].append(3)
        dup['pair'][1].append(4)
        dup['solid_geometry'].append(line)
        assert src['data']['a'] == [1, 2]
        assert src['pair'][1] == [3]
        assert len(lines) == 2

class TestSimplifyPaths:
    @pytest.mark.parametrize('count', [5, 300])
    def test_same_as_one_by_one(self, count):
        paths = noisy_paths(count)
        paths += [paths[1], Point(1, 2)]    # a path twice and a point
        simplified = simplify_paths(paths, 0.005)
        assert len(simplified) == len(paths)
        for path, result in zip(paths, simplified):
            assert result.geom_type == path.simplify(0.005).geom_type
            assert result.wkb == path.simplify(0.005).wkb
        assert simplified[1] is simplified[-2]

def gcode_job(pp_name, app=None):
    app = app or make_app()
    module = __import__('preprocessors.%s' % pp_name, fromlist=[pp_name])
    app.preprocessors[pp_name] = getattr(module, pp_name)()
    app.exc_areas = SimpleNamespace(travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    job = CNCjob(app, pp_geometry_name=pp_name)
    job.obj_options = {'name': 'job', 'type': 'Geometry'}
    job.coords_decimals = 4
    job.fr_decimals = 2
    job.z_cut = -0.1
    job.z_move = 2.0
    return job


PREPROCESSORS = ['default', 'GRBL_laser', 'GRBL_11', 'Marlin', 'Line_xyz', 'hpgl', 'Roland_MDX_20', 'ISEL_ICP_CNC']


class TestLinear2Gcode:
    @pytest.mark.parametrize('pp_name', PREPROCESSORS[::4])
    def test_cut_lines_same_as_doformat(self, pp_name):
        job = gcode_job(pp_name)
        p = job.pp_geometry
        path = noisy_paths(2)[1]
        gcode = job.linear2gcode(path, 0.1, tolerance=0)

        coords = list(path.coords)
        cuts = [job.doformat(p.linear_code, x=x, y=y, z_cut=job.z_cut) for x, y in coords[1:]]
        assert all(line.strip() for line in cuts)
        assert ''.join(cuts) in gcode
        last_x, last_y = coords[-1]
        assert gcode.endswith(job.doformat(p.lift_code, x=last_x, y=last_y, z_move=job.z_move))

def milling_tools(paths, **data_changes):
    data = dict(AppDefaults.factory_defaults)
    data.update(tools_mill_tooldia=0.1, tools_mill_offset_value=0.0, tools_mill_optimization_type='R',
                tools_mill_multidepth=False, tools_mill_extracut=False, tools_mill_cutz=-0.05,
                tools_mill_travelz=2.0, tools_mill_feedrate=300.0, tools_mill_spindlespeed=1000.0)
    data.update(data_changes)
    return {1: {'tooldia': 0.1, 'data': data, 'solid_geometry': paths}}


class TestGeometryToolGcode:
    @pytest.mark.parametrize('workers', [2])
    @pytest.mark.parametrize('count', [5])
    @pytest.mark.parametrize('cancel_before', [False, True])
    def test_cancel_stops_simplification(self, count, cancel_before, workers):
        job = gcode_job('default')
        tools = milling_tools(noisy_paths(count))
        original_simplify = shapely.simplify

        def emit(message):
            if cancel_before and 'Starting G-Code for tool with diameter' in message:
                job.app.abort_flag = True

        def simplify(piece, tolerance):
            job.app.abort_flag = True
            return original_simplify(piece, tolerance)

        with patch.object(job.app.inform, 'emit', side_effect=emit), \
                patch.object(camlib.os, 'cpu_count', return_value=workers), \
                patch.object(shapely, 'simplify', side_effect=simplify) as mock:
            with pytest.raises(camlib.grace):
                job.geometry_tool_gcode_gen(1, tools, (0, 0), (0, 0), 0.005, is_first=True, is_last=True)
        assert mock.call_count == (0 if cancel_before else 1)
        if not cancel_before and count == 300:
            assert len(mock.call_args.args[0]) < count


def old_codes_split(job, gline):
    """CNCjob.codes_split() before the patterns were compiled once: the reference."""
    command = {}

    if job.pp_solderpaste_name is not None:
        if gline.strip() in ('(PASTE_ON)', ';PASTE_ON'):
            return {'Z': 0}
        if gline.strip() in ('(PASTE_OFF)', ';PASTE_OFF'):
            return {'Z': 1}
        dot = re.match(r'^[;(]PASTE_DOT X([+\-\d.]+) Y([+\-\d.]+) D([+\-\d.]+)', gline)
        if dot:
            return {'X': float(dot[1]), 'Y': float(dot[2]), '_paste_dot': float(dot[3])}

    if 'Roland' in job.pp_excellon_name or 'Roland' in job.pp_geometry_name:
        match_z = re.search(r"^Z(\s*-?\d+\.\d+?),(\s*\s*-?\d+\.\d+?),(\s*\s*-?\d+\.\d+?)*;$", gline)
        if match_z:
            command['G'] = 0
            command['X'] = float(match_z.group(1).replace(" ", "")) * 0.01
            command['Y'] = float(match_z.group(2).replace(" ", "")) * 0.01
            command['Z'] = float(match_z.group(3).replace(" ", "")) * 0.025
    elif 'hpgl' in job.pp_excellon_name or 'hpgl' in job.pp_geometry_name:
        match_pa = re.search(r"^PA(\s*-?\d+\.\d+?),(\s*\s*-?\d+\.\d+?)*;$", gline)
        if match_pa:
            command['G'] = 0
            command['X'] = float(match_pa.group(1).replace(" ", "")) / 40
            command['Y'] = float(match_pa.group(2).replace(" ", "")) / 40
        match_pen = re.search(r"^(P[U|D])", gline)
        if match_pen:
            command['Z'] = 1 if match_pen.group(1) == 'PU' else 0
        if re.search(r"^SP\d*", gline):
            command['Z'] = 1
    elif 'laser' in job.pp_excellon_name.lower() or 'laser' in job.pp_geometry_name.lower() or \
            (job.pp_solderpaste_name is not None and 'paste' in job.pp_solderpaste_name.lower()):
        match_lsr = re.search(r"X([+-]?\d+.[+-]?\d+)\s*Y([+-]?\d+.[+-]?\d+)", gline)
        if match_lsr:
            command['X'] = float(match_lsr.group(1).replace(" ", ""))
            command['Y'] = float(match_lsr.group(2).replace(" ", ""))
        match_lsr_pos = re.search(r"^(M0?[3-5])", gline)
        if match_lsr_pos:
            command['Z'] = 1 if ('M05' in match_lsr_pos.group(1) or 'M5' in match_lsr_pos.group(1)) else 0
        match_lsr_pos_2 = re.search(r"^(M10[6|7])", gline)
        if match_lsr_pos_2:
            command['Z'] = 1 if 'M107' in match_lsr_pos_2.group(1) else 0
        match_lsr_pos_3 = re.search(r"^.*(laser OFF).*", gline)
        if match_lsr_pos_3:
            command['Z'] = 1 if 'laser OFF' in match_lsr_pos_3.group(1) else 0
    elif job.pp_solderpaste_name is not None:
        if 'Paste' in job.pp_solderpaste_name:
            match_paste = re.search(r"X([+-]?\d+.[+-]?\d+)\s*Y([+-]?\d+.[+-]?\d+)", gline)
            if match_paste:
                command['X'] = float(match_paste.group(1).replace(" ", ""))
                command['Y'] = float(match_paste.group(2).replace(" ", ""))
    else:
        match = re.search(r'^\s*([A-Z])\s*([+\-.\d\s]+)', gline)
        while match:
            command[match.group(1)] = float(match.group(2).replace(" ", ""))
            gline = gline[match.end():]
            match = re.search(r'^\s*([A-Z])\s*([+\-.\d\s]+)', gline)
    return command


LINES = [
    'G0 X1.0000 Y2.0000', 'G1 X-1.5 Y2.5 F1000', 'G01 X1.2 Y-3.4 Z0.5', '  G1X1Y2', 'G00 Z2.0000', 'T1', 'M6',
    'M3 S500', 'M03 S500', 'M4', 'M5', 'M05', ' M5', 'M106 S255', 'M107', 'M3 S0 (laser OFF)', 'G94', 'G21',
    '(comment X1 Y2)', '', 'X1.0 Y2.0', 'G1 X 1.25 Y 2.5', 'PA10.0,20.0;', 'PU;', 'PD;', 'SP1;',
    'Z100.0,200.0,-50.0;', 'Z1.5,2.5;', 'G1 X1.0000Y2.0000', 'M5 (laser OFF)', 'G0 X1e3 Y2', 'G1 X1 Y2 ; laser OFF',
    '(PASTE_ON)', ';PASTE_OFF', ';PASTE_DOT X1.5 Y-2 D0.4', 'G4 P1.5', 'G1 X1.2.3', 'MOVEABS 1000, 2000',
]
NAMES = [
    ('default', 'default', None), ('default', 'GRBL_laser', None), ('GRBL_laser_z', 'default', None),
    ('default', 'Roland_MDX_20', None), ('default', 'hpgl', None), ('default', 'default', 'Paste_1'),
    ('default', 'default', 'other'),
]


class TestCodesSplit:
    @pytest.mark.parametrize('names', NAMES)
    def test_same_as_before(self, names):
        job = SimpleNamespace(pp_excellon_name=names[0], pp_geometry_name=names[1], pp_solderpaste_name=names[2])
        for line in LINES:
            try:
                expected = old_codes_split(job, line)
            except ValueError:
                with pytest.raises(ValueError):
                    CNCjob.codes_split(job, line)
                continue
            assert CNCjob.codes_split(job, line) == expected, (names, line)



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
    @pytest.mark.parametrize('multidepth', [False, True])
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

    @pytest.mark.parametrize('step', [0, None])
    def test_multi_pass_with_zero_step_ends(self, step):
        job = gcode_job('default')
        job.z_depthpercut = step
        gcode, __ = run_with_deadline(
            lambda: job.create_gcode_multi_pass(TWO_LINES[0], 0.1, False, 0.0, 0, job.pp_geometry, 2.0))
        assert 'Z-0.1000' in gcode

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

    @pytest.mark.parametrize('keyword', [{}, {'area_exclusion': True}, {'area_exclusion': False}])
    def test_single_geometry_exclusion_keyword(self, keyword):
        job = gcode_job('default')
        calls = recording_exclusion(job)
        single_path_gcode(job, TWO_LINES, **keyword)
        assert bool(calls) is keyword.get('area_exclusion', True)


    def test_first_tool_uses_start_z_by_position_not_by_uid(self):
        data = dict(tools_mill_startz=1.5, tools_mill_toolchangez=15.0)
        job, first = multi_geo_gcode([TWO_LINES[0]], uid=5, toolchange=True, is_first=True, data=data)
        assert 'G00 Z1.5000\nG00 X0.0000 Y0.0000\nT5' in first
        job, later = multi_geo_gcode([TWO_LINES[0]], uid=1, toolchange=True, is_first=False, data=data)
        assert 'G00 Z15.0000\nG00 X0.0000 Y0.0000\nT1' in later

    @pytest.mark.parametrize('paths, changes', [([], {}), ([TWO_LINES[0]], {'tools_mill_offset_value': -5.0})])
    def test_tool_without_usable_geometry_tells_the_user(self, paths, changes):
        job, gcode = multi_geo_gcode(paths, data=dict(changes, tools_mill_tooldia=0.25))
        assert gcode == 'fail'
        notices = [m for m in messages(job) if m.startswith(('[ERROR_NOTCL]', '[WARNING_NOTCL]'))]
        assert notices and '0.25' in notices[-1]


    @pytest.mark.parametrize('opt_type, single', [('T', False), ('N', True)])
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

    def test_travelling_salesman_keeps_the_input_and_the_order(self):
        rng = np.random.default_rng(7)
        points = [tuple(p) for p in rng.integers(0, 40, size=(60, 2)).astype(float)]
        original = list(points)

        def reference(pts, start=None):
            start = pts[0] if start is None else start
            left, path = list(pts), [start]
            while left:
                nearest = min(left, key=lambda x: camlib.distance(path[-1], x))
                if nearest != path[-1]:
                    path.append(nearest)
                left.remove(nearest)
            return path

        assert CNCjob.optimized_travelling_salesman(points) == reference(original)
        assert points == original


class TestPathOptimizationArgument:
    PATHS = [LineString([(30, 0), (40, 0)]), LineString([(0, 0), (10, 0)]), LineString([(20, 0), (25, 0)])]

    def test_the_tool_optimization_wins_over_the_application_option(self):
        by_option = single_path_gcode(gcode_job('default'), self.PATHS, opt_type='N')
        job = gcode_job('default')
        job.app.options['tools_mill_optimization_type'] = 'R'
        geo = SimpleNamespace(solid_geometry=self.PATHS, obj_options={'name': 'g'})
        by_argument = job.generate_from_geometry_2(
            geo, tooldia=0.1, z_cut=-0.1, z_move=2.0, feedrate=300.0, feedrate_z=100.0, feedrate_rapid=1000.0,
            pp_geometry_name='default', is_first=True, toolchangez=15.0, startz=None, endz=2.0, opt_type='N')[0]
        assert by_argument == by_option
        assert by_argument != single_path_gcode(gcode_job('default'), self.PATHS, opt_type='R')


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

    def test_many_drills_are_found_fast(self):
        import time
        count = 3000
        drills = [Point((k % 60) * 1.0, (k // 60) * 1.0) for k in range(count)]
        lines = ['G21', 'G90']
        for p in drills:
            lines += ['G0 X%.4f Y%.4f' % (p.x, p.y), 'G1 Z-1.0000', 'G0 Z2.0000']
        started = time.perf_counter()
        job = parsed_job('\n'.join(lines), 'Excellon', {1: {'tooldia': 0.8, 'drills': drills}})
        assert time.perf_counter() - started < 10
        rings = [g for g in job.gcode_parsed if g['geom'].geom_type == 'LinearRing']
        assert len(rings) == count
        assert rings[0]['geom'].bounds == pytest.approx((-0.4, -0.4, 0.4, 0.4), abs=0.01)


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
