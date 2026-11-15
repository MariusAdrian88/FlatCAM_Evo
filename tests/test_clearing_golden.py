"""Golden contract of the NCC and Paint plugins (stage 0 of docs/superpowers/plans/2026-11-14-ncc-paint-shared-engine.md).

Real entry points run headless on a cropped board window (fixtures/clearing/altium_crop.gbr, 7.1 mm window of the
Altium CircuitMaker Signal_Top layer of fixtures/altium_cs/gerbers_x2) and on fixtures/unify/unify_mixed.gbr:

- NCC: ``on_ncc_click`` (the GUI resolution through ``ncc_handler``) and ``clear_copper_tcl``.
- Paint: ``paint_poly_all``, ``paint_poly`` (click), ``paint_poly_area_worker``, ``paint_poly_ref``.

Each case has one golden in ``fixtures/clearing/golden_<case>.json`` with two parts:

1. ``tools``: the extraction contract, compared EXACTLY. For every output tool, in output order: the diameter, the
   sorted ``data`` keys, the cleanup marker (``ncc_cleanup_start`` / ``paint_cleanup_start``) and the paths. A path is
   its geometry type and its coordinates rounded to 6 decimals (``ROUND``), in the stored order and direction. The
   direction and the order of the paths are NOT normalized: a refactor must reproduce them.
2. ``metrics``: the cut correctness, compared with explicit tolerances (``AREA_ABS`` mm2, ``AREA_REL`` of the region):
   the swept area (union of path.buffer(dia / 2)) inside the region, the intrusion of the sweep into the forbidden
   zone (copper for NCC, the painted-polygons complement for Paint, exclusion zones where used) and the residual
   (region minus the union of all sweeps).

Goldens are written only with the environment variable UPDATE_GOLDEN=1; a missing golden fails the test.
"""

import functools
import json
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6 import QtWidgets  # noqa: E402
from PyQt6.QtCore import QEvent, QStringListModel  # noqa: E402
from shapely.geometry import LinearRing, LineString, MultiPolygon, Point, Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from appMain import App  # noqa: E402
from appParsers.feature_access import fused_solid  # noqa: E402
from appPlugins.ToolNCC.Ncc import ToolNcc  # noqa: E402
from appPlugins.ToolPaint.Paint import ToolPaint  # noqa: E402
from defaults import AppDefaults  # noqa: E402
from helpers_objects import MIXED_GBR, make_app, make_gerber  # noqa: E402
from test_plugin_geometry_bases import make_tool_app  # noqa: E402

FIX = Path(__file__).parent / 'fixtures' / 'clearing'
CROP_GBR = str(FIX / 'altium_crop.gbr')
UPDATE = os.environ.get('UPDATE_GOLDEN') == '1'

ROUND = 6            # decimals of the stored coordinates
AREA_ABS = 2e-3      # mm2, absolute tolerance of a stored area
AREA_REL = 5e-3      # of the region area, relative tolerance of a stored area
SWEEP_QUAD_SEGS = 8


# ---------------------------------------------------------------------------------------------------------------
# headless tools
# ---------------------------------------------------------------------------------------------------------------
class Collection(QStringListModel):
    def __init__(self):
        super().__init__()
        self.objects = {}
        self.selected = []

    def get_selected(self):
        return self.selected

    def get_active(self):
        return None

    def get_list(self):
        return list(self.objects.values())

    def get_names(self):
        return list(self.objects)

    def get_by_name(self, name):
        return self.objects.get(name)

    def promise(self, name):
        pass

    def remove_promise(self, name):
        pass


class Defaults(dict):
    factory_defaults = AppDefaults.factory_defaults
    report_usage = staticmethod(lambda *args: None)


@pytest.fixture(scope='module')
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope='module')
def boards(qapp):
    """The 2 inputs, parsed once; the plugins never change them."""
    from unittest.mock import patch
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        app = make_app()
        yield {'crop': make_gerber(app, CROP_GBR, 'crop'), 'mixed': make_gerber(app, MIXED_GBR, 'mixed')}


class Capture:
    """The inline ``new_object``: runs the worker now and keeps the output object it filled."""

    def __init__(self):
        self.outputs = []

    def __call__(self, kind, name, worker, **kwargs):
        output = SimpleNamespace(kind='geometry', tools={}, obj_options={}, solid_geometry=[])
        result = worker(output, self.app)
        self.outputs.append((name, output, result))
        return 'fail' if result == 'fail' or result is False else output


def prepare_app(tmp_path, options):
    app = make_tool_app()
    app.collection = Collection()
    app.options = deepcopy(AppDefaults.factory_defaults)
    app.options.update(options)
    app.defaults = Defaults(AppDefaults.factory_defaults)
    app.dec_format = functools.partial(App.dec_format, app)
    app.tools_database_path.return_value = str(tmp_path / 'missing_tools_db.json')
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    app.abort_flag = False
    app.plotcanvas.translate_coords.side_effect = lambda pos: pos
    app.grid_status.return_value = False
    app.plotcanvas.new_shape_collection.side_effect = lambda **kwargs: MagicMock()
    return app


class Plugin:
    """A real ToolNcc or ToolPaint with its panel, the project objects and the inline ``new_object``."""

    def __init__(self, qapp, tmp_path, kind, source, dias, options=None, tool_data=None, objects=None):
        self.qapp = qapp
        key = 'tools_%s_tools' % kind if kind == 'ncc' else 'tools_paint_tooldia'
        options = dict(options or {})
        options[key] = ', '.join(str(d) for d in dias)
        self.app = prepare_app(tmp_path, options)
        self.capture = Capture()
        self.capture.app = self.app
        self.app.app_obj = SimpleNamespace(new_object=self.capture)
        self.tool = ToolNcc(self.app) if kind == 'ncc' else ToolPaint(self.app)
        self.tool.decimals = 4
        self.tool.set_tool_ui()
        if kind == 'paint':
            self.tool.build_ui()
        self.tool.new_object = self.capture
        self.tools = self.tool.ncc_tools if kind == 'ncc' else self.tool.paint_tools
        prefix = 'tools_%s_' % kind
        for uid, row in self.tools.items():
            for name, value in ((tool_data or {}).get(round(row['tooldia'], 4)) or {}).items():
                row['data'][prefix + name] = value
        self.tool.ui.tools_table.selectAll()
        self.source = source
        self.app.collection.objects['board'] = source
        for name, obj in (objects or {}).items():
            self.app.collection.objects[name] = obj
        self.tool.ui.obj_combo.currentText = lambda: 'board'
        self.tool.ui.reference_combo.currentText = lambda: 'ref'

    def close(self):
        self.tool.deleteLater()
        self.qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)

    def result(self):
        assert len(self.capture.outputs) == 1, self.capture.outputs
        name, output, result = self.capture.outputs[0]
        assert result != 'fail'
        return output


# ---------------------------------------------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------------------------------------------
def leaves(geometry):
    """The simple geometries of a (nested) list / Multi* / collection, in the stored order."""
    if geometry is None:
        return
    if isinstance(geometry, (list, tuple)):
        for item in geometry:
            yield from leaves(item)
    elif hasattr(geometry, 'geoms'):
        for item in geometry.geoms:
            yield from leaves(item)
    else:
        yield geometry


def rounded(coords):
    return [[round(x, ROUND) + 0.0, round(y, ROUND) + 0.0] for x, y in coords]


def path_record(geometry):
    if isinstance(geometry, Polygon):
        return ['Polygon', rounded(geometry.exterior.coords)] + [rounded(r.coords) for r in geometry.interiors]
    return [geometry.geom_type, rounded(geometry.coords)]


MARKERS = ('ncc_cleanup_start', 'paint_cleanup_start')


def tool_snapshot(tool):
    paths = [path_record(g) for g in leaves(tool['solid_geometry'])]
    return {
        'dia': round(float(tool['tooldia']), ROUND),
        'data_keys': sorted(tool['data']),
        'markers': {key: tool[key] for key in MARKERS if key in tool},
        'paths': paths,
    }


def tools_snapshot(output):
    """The tools in output order. A ``data_keys`` list equal to the one of an earlier tool is stored as 'same as N'."""
    snapshot = [tool_snapshot(tool) for tool in output.tools.values()]
    first = []      # (keys, index of the first tool that holds them)
    for index, tool in enumerate(snapshot):
        keys = tool['data_keys']
        for known, where in first:
            if known == keys:
                tool['data_keys'] = 'same as %d' % where
                break
        else:
            first.append((keys, index))
    return snapshot


def sweep_of(tool, dia=None):
    radius = float(dia or tool['tooldia']) / 2
    paths = [g for g in leaves(tool['solid_geometry']) if not g.is_empty]
    if not paths:
        return Polygon()
    return unary_union([g.buffer(radius, quad_segs=SWEEP_QUAD_SEGS) for g in paths])


def metrics_of(output, region, forbidden):
    """Per tool and total: the swept area inside the region, the intrusion into the forbidden zone, the residual."""
    sweeps = []
    per_tool = []
    for tool in output.tools.values():
        sweep = sweep_of(tool)
        sweeps.append(sweep)
        per_tool.append({
            'dia': round(float(tool['tooldia']), ROUND),
            'swept': round(sweep.intersection(region).area, 6),
            'intrusion': round(sweep.intersection(forbidden).area, 6),
        })
    total = unary_union(sweeps) if sweeps else Polygon()
    return {
        'region_area': round(region.area, 6),
        'tools': per_tool,
        'swept': round(total.intersection(region).area, 6),
        'intrusion': round(total.intersection(forbidden).area, 6),
        'residual': round(region.difference(total).area, 6),
    }


def close_enough(actual, expected, region_area):
    return abs(actual - expected) <= AREA_ABS + AREA_REL * region_area


def compare_metrics(actual, expected, label):
    area = expected['region_area']
    assert close_enough(actual['region_area'], area, area), '%s: region_area' % label
    assert len(actual['tools']) == len(expected['tools']), '%s: number of tools' % label
    pairs = [(actual, expected, label)] + [
        (a, e, '%s tool %s' % (label, e['dia'])) for a, e in zip(actual['tools'], expected['tools'])]
    for a, e, where in pairs:
        assert a['dia'] == e['dia'] if 'dia' in e else True, where
        for key in ('swept', 'intrusion', 'residual'):
            if key in e:
                assert close_enough(a[key], e[key], area), '%s: %s %s != %s' % (where, key, a[key], e[key])


def first_difference(actual, expected):
    for index, (a, e) in enumerate(zip(actual, expected)):
        if a == e:
            continue
        for key in a:
            if a[key] == e[key]:
                continue
            if key == 'paths':
                for number, (pa, pe) in enumerate(zip(a['paths'], e['paths'])):
                    if pa != pe:
                        return 'tool %d path %d differs: %s != %s' % (index, number, str(pa)[:200], str(pe)[:200])
                return 'tool %d: %d paths != %d' % (index, len(a['paths']), len(e['paths']))
            return 'tool %d %s: %s != %s' % (index, key, a[key], e[key])
    return 'number of tools: %d != %d' % (len(actual), len(expected))


def check_golden(case, tools, metrics):
    path = FIX / ('golden_%s.json' % case)
    if UPDATE:
        FIX.mkdir(parents=True, exist_ok=True)
        text = '{\n "metrics": %s,\n "tools": [\n%s\n ]\n}\n' % (
            json.dumps(metrics, separators=(',', ':')),
            ',\n'.join('  ' + json.dumps(tool, separators=(',', ':')) for tool in tools))
        path.write_text(text, newline='\n')
        return
    assert path.exists(), 'missing golden %s (regenerate with UPDATE_GOLDEN=1)' % path.name
    golden = json.loads(path.read_text())
    assert [t['dia'] for t in tools] == [t['dia'] for t in golden['tools']], 'order of the output tools'
    assert tools == golden['tools'], first_difference(tools, golden['tools'])
    compare_metrics(metrics, golden['metrics'], case)


# ---------------------------------------------------------------------------------------------------------------
# NCC
# ---------------------------------------------------------------------------------------------------------------
def ncc_regions(board, margin, offset=0.0, exclusion=None):
    """The independent region of an NCC run (extent plus margin, minus the copper with the offset) and its zones."""
    copper = fused_solid(board)
    minx, miny, maxx, maxy = copper.bounds
    extent = box(minx - margin, miny - margin, maxx + margin, maxy + margin)
    kept_out = copper.buffer(offset) if offset else copper
    region = extent.difference(kept_out)
    forbidden = kept_out
    if exclusion is not None:
        region = region.difference(exclusion)
        forbidden = unary_union([forbidden, exclusion])
    return region, forbidden


def run_ncc_click(plugin, rest=False, margin=0.5, rest_margin=0.5, exclusion=None, rest_offset=None):
    ui = plugin.tool.ui
    ui.valid_cb.set_value(False)
    ui.rest_cb.set_value(rest)
    ui.margin_entry.set_value(margin)
    ui.rest_margin_entry.set_value(rest_margin)
    if rest_offset is not None:
        ui.rest_offset_choice_cb.set_value(True)
        ui.rest_offset_entry.set_value(rest_offset)
    ui.select_method_combo.set_value(0)
    if exclusion is not None:
        plugin.source.add_exclusion_zones('ncc', [exclusion])
        plugin.tool.change_level('a')
        ui.excl_cb.set_value(True)
    plugin.tool.gen.on_ncc_click()


def run_ncc_tcl(plugin, tools, **kwargs):
    kwargs.setdefault('outname', 'board_ncc')
    kwargs.setdefault('plot', False)
    plugin.tool.gen.result = plugin.tool.gen.clear_copper_tcl(plugin.source, ncc_tooldia=tools, **kwargs)


# per-tool settings (keys without the "tools_ncc_" prefix) of the 5 methods: mixed method, overlap, connect, contour
NCC_METHOD_TOOLS = {
    0.5: dict(method=0, overlap=20, connect=True, contour=True),
    0.4: dict(method=1, overlap=30, connect=False, contour=True),
    0.3: dict(method=2, overlap=25, connect=True, contour=False),
    0.25: dict(method=3, overlap=40, connect=False, contour=True),
    0.2: dict(method=4, overlap=35, connect=True, contour=True, crosshatch_angle=30.0),
}


def ncc_case_methods(boards, qapp, tmp_path):
    """Click, Itself, normal: the 5 methods of NCC, one per tool, with mixed overlap, connect, contour and angle."""
    plugin = Plugin(qapp, tmp_path, 'ncc', boards['crop'], list(NCC_METHOD_TOOLS), tool_data=NCC_METHOD_TOOLS)
    run_ncc_click(plugin, margin=0.5)
    return plugin, ncc_regions(boards['crop'], 0.5)


def ncc_case_rest(boards, qapp, tmp_path):
    """Click, Itself, rest machining: the rest margin, the rest Connect / Contour of the form, the 5 methods."""
    data = {dia: dict(row, final_cleanup=True) for dia, row in NCC_METHOD_TOOLS.items()}
    plugin = Plugin(qapp, tmp_path, 'ncc', boards['crop'], list(data), tool_data=data)
    plugin.tool.ui.rest_connect_cb.set_value(True)
    plugin.tool.ui.rest_contour_cb.set_value(True)
    run_ncc_click(plugin, rest=True, rest_margin=0.4)
    return plugin, ncc_regions(boards['crop'], 0.4)


def ncc_case_cleanup_offsets(boards, qapp, tmp_path):
    """Click, normal, per-tool offsets and the final cleanup (the cleanup markers), on the second input."""
    data = {1.0: dict(method=0, overlap=20, connect=True, contour=True, offset_choice=True, offset_value=0.1,
                      final_cleanup=True),
            0.4: dict(method=2, overlap=30, connect=False, contour=True, offset_choice=True, offset_value=0.05,
                      final_cleanup=True)}
    plugin = Plugin(qapp, tmp_path, 'ncc', boards['mixed'], list(data), tool_data=data)
    run_ncc_click(plugin, margin=2.0)
    return plugin, ncc_regions(boards['mixed'], 2.0, offset=0.05)


def ncc_case_isolation(boards, qapp, tmp_path):
    """Click, normal, a clearing tool and an isolation tool (the isolation passes of the NCC)."""
    data = {0.4: dict(method=1, overlap=30, connect=True, contour=True),
            0.1: dict(operation='iso', overlap=20)}
    plugin = Plugin(qapp, tmp_path, 'ncc', boards['crop'], list(data), tool_data=data)
    run_ncc_click(plugin, margin=0.5)
    return plugin, ncc_regions(boards['crop'], 0.5)


def ncc_case_exclusion(boards, qapp, tmp_path):
    """Click, normal, an exclusion zone over a part of the window: the cutter stays out of it."""
    data = {0.3: dict(method=0, overlap=25, connect=True, contour=True),
            0.2: dict(method=2, overlap=30, connect=False, contour=True)}
    zone = box(1.5, 1.5, 4.5, 4.0)
    plugin = Plugin(qapp, tmp_path, 'ncc', boards['crop'], list(data), tool_data=data)
    run_ncc_click(plugin, margin=0.5, exclusion=zone)
    boards['crop'].set_exclusion_zones({})      # the board is shared by the cases
    return plugin, ncc_regions(boards['crop'], 0.5, exclusion=zone)


def ncc_case_reference(boards, qapp, tmp_path):
    """Click, Reference Object: the clearing is limited to the area of a Geometry object."""
    data = {0.3: dict(method=0, overlap=25, connect=True, contour=True)}
    area = box(1.0, 1.0, 5.0, 5.5)
    reference = SimpleNamespace(kind='geometry', solid_geometry=[area], obj_options={'name': 'ref'},
                                tools={1: {'solid_geometry': [area]}})
    plugin = Plugin(qapp, tmp_path, 'ncc', boards['crop'], list(data), tool_data=data, objects={'ref': reference})
    ui = plugin.tool.ui
    ui.select_method_combo.set_value(2)
    ui.valid_cb.set_value(False)
    ui.rest_cb.set_value(False)
    ui.margin_entry.set_value(0.2)
    plugin.tool.gen.on_ncc_click()
    copper = fused_solid(boards['crop'])
    region = box(0.8, 0.8, 5.2, 5.7).difference(copper)
    return plugin, (region, copper)


def ncc_case_adaptive(boards, qapp, tmp_path):
    """Click, rest, 2 diameters (2.0 and 0.2) with adaptive circle accuracy of 1 um and a Clearing Error of 1 um."""
    options = {'gerber_circle_steps_mode': 'adaptive', 'gerber_circle_tolerance': 1.0,
               'geometry_circle_steps_mode': 'adaptive', 'geometry_circle_tolerance': 1.0,
               'tools_clearing_error': 1.0}
    data = {2.0: dict(method=0, overlap=20, connect=True, contour=True),
            0.2: dict(method=0, overlap=30, connect=True, contour=True)}
    plugin = Plugin(qapp, tmp_path, 'ncc', boards['mixed'], list(data), options=options, tool_data=data)
    run_ncc_click(plugin, rest=True, rest_margin=3.0)
    return plugin, ncc_regions(boards['mixed'], 3.0)


# the Tcl jobs and the GUI settings that mean the same: the Tcl arguments, and the row of every tool of the GUI table
TCL_JOBS = {
    'normal': dict(
        dias=[0.4, 0.2], margin=0.5, offset=0.03, rest=False,
        args=dict(margin=0.5, has_offset=True, offset=0.03, select_method=0, overlap=0.3, connect=True, contour=True,
                  order=1, method=2, rest=False, final_cleanup=True),
        row=dict(method=2, overlap=30, connect=True, contour=True, offset_choice=True, offset_value=0.03,
                 final_cleanup=True)),
    'rest': dict(
        dias=[0.5, 0.25, 0.2], margin=0.4, offset=0.0, rest=True,
        args=dict(margin=0.4, select_method=0, overlap=0.35, connect=True, contour=True, method=4, angle=30.0,
                  rest=True, final_cleanup=True),
        row=dict(method=4, overlap=35, connect=True, contour=True, crosshatch_angle=30.0, final_cleanup=True)),
}


def ncc_case_tcl(boards, qapp, tmp_path, job):
    job = TCL_JOBS[job]
    plugin = Plugin(qapp, tmp_path, 'ncc', boards['crop'], job['dias'])
    run_ncc_tcl(plugin, job['dias'], **job['args'])
    return plugin, ncc_regions(boards['crop'], job['margin'], offset=job['offset'])


def ncc_case_tcl_normal(boards, qapp, tmp_path):
    """Tcl, normal: 2 diameters, a nonzero offset, the Tcl method, overlap and margin, the final cleanup."""
    return ncc_case_tcl(boards, qapp, tmp_path, 'normal')


def ncc_case_tcl_rest(boards, qapp, tmp_path):
    """Tcl, rest machining with the Crosshatch angle and the final cleanup (the cleanup markers)."""
    return ncc_case_tcl(boards, qapp, tmp_path, 'rest')


NCC_CASES = {
    'ncc_click_methods': ncc_case_methods,
    'ncc_click_rest': ncc_case_rest,
    'ncc_click_cleanup_offsets': ncc_case_cleanup_offsets,
    'ncc_click_isolation': ncc_case_isolation,
    'ncc_click_exclusion': ncc_case_exclusion,
    'ncc_click_reference': ncc_case_reference,
    'ncc_click_adaptive_rest': ncc_case_adaptive,
    'ncc_tcl_normal': ncc_case_tcl_normal,
    'ncc_tcl_rest': ncc_case_tcl_rest,
}


# ---------------------------------------------------------------------------------------------------------------
# Paint
# ---------------------------------------------------------------------------------------------------------------
def paint_regions(board, offset=0.0, areas=None):
    """The independent region of a Paint run (the copper, inside the areas, shrunk by the offset) and its zones."""
    copper = fused_solid(board)
    selected = copper if areas is None else copper.intersection(unary_union(areas))
    region = selected.buffer(-offset) if offset else selected
    minx, miny, maxx, maxy = copper.bounds
    # a negative offset grows the painted polygons: the cutter may then leave the copper by that much
    allowed = copper.buffer(-offset) if offset < 0 else copper
    forbidden = box(minx - 2, miny - 2, maxx + 2, maxy + 2).difference(allowed)
    return region, forbidden


def run_paint(plugin, entry, *args, **kwargs):
    dias = [row['tooldia'] for row in plugin.tools.values()]
    kwargs.setdefault('tooldia', dias)
    kwargs.setdefault('outname', 'board_paint')
    kwargs.update(plot=False, run_threaded=False)
    result = getattr(plugin.tool.gen, entry)(plugin.source, *args, **kwargs)
    assert result != 'fail'


# per-tool settings of the Paint methods 0, 1, 2, 4, 5; 3 (laser lines) needs a Gerber with apertures: paint_case_laser
PAINT_METHOD_TOOLS = {
    0.2: dict(method=0, overlap=20, connect=True, contour=True),
    0.15: dict(method=1, overlap=30, connect=False, contour=True),
    0.12: dict(method=2, overlap=25, connect=True, contour=False),
    0.1: dict(method=4, overlap=40, connect=False, contour=True),
    0.08: dict(method=5, overlap=35, connect=True, contour=True, crosshatch_angle=30.0),
}


def paint_case_all_methods(boards, qapp, tmp_path):
    """paint_poly_all, normal: the Paint methods 0, 1, 2, 4, 5, one per tool, mixed overlap, connect and angle."""
    plugin = Plugin(qapp, tmp_path, 'paint', boards['crop'], list(PAINT_METHOD_TOOLS), tool_data=PAINT_METHOD_TOOLS)
    run_paint(plugin, 'paint_poly_all', rest=False)
    return plugin, paint_regions(boards['crop'])


def paint_case_rest_cleanup(boards, qapp, tmp_path):
    """paint_poly_all, rest machining with a rest offset and the final cleanup (the cleanup markers)."""
    data = {0.2: dict(method=0, overlap=20, connect=True, contour=True, final_cleanup=True),
            0.12: dict(method=1, overlap=25, connect=True, contour=True, final_cleanup=True)}
    plugin = Plugin(qapp, tmp_path, 'paint', boards['crop'], list(data), tool_data=data)
    run_paint(plugin, 'paint_poly_all', rest=True, rest_offset=0.01)
    return plugin, paint_regions(boards['crop'])


def paint_case_click(boards, qapp, tmp_path):
    """paint_poly: the click inside one polygon, normal, with per-tool offsets."""
    data = {0.15: dict(method=0, overlap=20, connect=True, contour=True, offset=0.02),
            0.08: dict(method=5, overlap=30, connect=True, contour=True, offset=0.01, crosshatch_angle=45.0)}
    copper = fused_solid(boards['crop'])
    polygon = max(copper.geoms, key=lambda p: p.area)
    point = polygon.representative_point()
    plugin = Plugin(qapp, tmp_path, 'paint', boards['crop'], list(data), tool_data=data)
    run_paint(plugin, 'paint_poly', [point.x, point.y], rest=False)
    return plugin, (polygon, paint_regions(boards['crop'])[1])


def paint_case_area(boards, qapp, tmp_path):
    """paint_poly_area_worker: the polygons inside 2 areas, normal, with a negative tool offset (grows)."""
    data = {0.12: dict(method=4, overlap=25, connect=True, contour=True, offset=-0.01),
            0.08: dict(method=0, overlap=30, connect=False, contour=True, offset=-0.01)}
    areas = [box(0.5, 0.5, 3.5, 3.5), box(3.0, 3.0, 6.5, 6.5)]
    plugin = Plugin(qapp, tmp_path, 'paint', boards['crop'], list(data), tool_data=data)
    run_paint(plugin, 'paint_poly_area_worker', areas, rest=False)
    return plugin, paint_regions(boards['crop'], offset=-0.01, areas=areas)


def paint_case_reference(boards, qapp, tmp_path):
    """paint_poly_ref: the polygons inside a reference Geometry object, rest machining."""
    data = {0.2: dict(method=0, overlap=20, connect=True, contour=True),
            0.1: dict(method=2, overlap=30, connect=True, contour=True)}
    area = box(1.0, 1.0, 6.0, 6.0)
    reference = SimpleNamespace(kind='geometry', solid_geometry=[area], obj_options={'name': 'ref'})
    plugin = Plugin(qapp, tmp_path, 'paint', boards['crop'], list(data), tool_data=data)
    run_paint(plugin, 'paint_poly_ref', reference, rest=True)
    return plugin, paint_regions(boards['crop'], areas=[area])


def paint_case_laser(boards, qapp, tmp_path):
    """paint_poly_all, normal: Laser Lines (method 3) on a Gerber with apertures, next to a Standard tool."""
    data = {0.3: dict(method=3, overlap=20, connect=True, contour=True),
            0.2: dict(method=0, overlap=25, connect=True, contour=True)}
    plugin = Plugin(qapp, tmp_path, 'paint', boards['mixed'], list(data), tool_data=data)
    run_paint(plugin, 'paint_poly_all', rest=False)
    return plugin, paint_regions(boards['mixed'])


def paint_case_adaptive(boards, qapp, tmp_path):
    """paint_poly_all, normal, 2 diameters (2.0 and 0.2) with adaptive circle accuracy and a Clearing Error of 1 um."""
    options = {'gerber_circle_steps_mode': 'adaptive', 'gerber_circle_tolerance': 1.0,
               'geometry_circle_steps_mode': 'adaptive', 'geometry_circle_tolerance': 1.0,
               'tools_clearing_error': 1.0}
    data = {2.0: dict(method=0, overlap=20, connect=True, contour=True),
            0.2: dict(method=0, overlap=30, connect=True, contour=True)}
    plugin = Plugin(qapp, tmp_path, 'paint', boards['mixed'], list(data), options=options, tool_data=data)
    run_paint(plugin, 'paint_poly_all', rest=False)
    return plugin, paint_regions(boards['mixed'])


PAINT_CASES = {
    'paint_all_methods': paint_case_all_methods,
    'paint_all_rest_cleanup': paint_case_rest_cleanup,
    'paint_click': paint_case_click,
    'paint_area': paint_case_area,
    'paint_reference_rest': paint_case_reference,
    'paint_laser_lines': paint_case_laser,
    'paint_adaptive': paint_case_adaptive,
}

CASES = {**NCC_CASES, **PAINT_CASES}


@pytest.mark.parametrize('case', list(CASES))
def test_clearing_golden(case, boards, qapp, tmp_path):
    plugin, (region, forbidden) = CASES[case](boards, qapp, tmp_path)
    try:
        output = plugin.result()
        tools = tools_snapshot(output)
        assert tools, 'the run made no tool'
        metrics = metrics_of(output, region, forbidden)
    finally:
        plugin.close()
    check_golden(case, tools, metrics)


@pytest.mark.parametrize('job', list(TCL_JOBS))
def test_ncc_tcl_gives_the_paths_of_the_gui_for_the_same_inputs(job, boards, qapp, tmp_path):
    """Tcl and the GUI run one generation path: the same settings give the same tools and paths."""
    spec = TCL_JOBS[job]
    plugin, __ = ncc_case_tcl(boards, qapp, tmp_path / 'tcl', job)
    try:
        tcl = [(t['dia'], t['paths'], t['markers']) for t in tools_snapshot(plugin.result())]
    finally:
        plugin.close()
    rows = {dia: spec['row'] for dia in spec['dias']}
    plugin = Plugin(qapp, tmp_path / 'gui', 'ncc', boards['crop'], spec['dias'], tool_data=rows)
    try:
        ui = plugin.tool.ui
        ui.rest_connect_cb.set_value(spec['row']['connect'])
        ui.rest_contour_cb.set_value(spec['row']['contour'])
        ui.order_combo.set_value(spec['args'].get('order', 0))
        run_ncc_click(plugin, rest=spec['rest'], margin=spec['margin'], rest_margin=spec['margin'])
        gui = [(t['dia'], t['paths'], t['markers']) for t in tools_snapshot(plugin.result())]
    finally:
        plugin.close()
    assert tcl and tcl == gui


# ---------------------------------------------------------------------------------------------------------------
# queued jobs: a resolved job owns its settings (plan: "What fully resolved means")
# ---------------------------------------------------------------------------------------------------------------
QUEUED_DATA = {0.4: dict(method=0, overlap=25, connect=True, contour=True),
               0.25: dict(method=2, overlap=30, connect=False, contour=True)}


def _spoil(plugin, kind, board):
    """Everything a later edit can change: options, the tool table and the source geometry."""
    plugin.app.options.update({'tools_clearing_edge': 'strict', 'tools_clearing_error': 50.0,
                               'global_tolerance': 0.5, 'gerber_circle_steps_mode': 'fixed',
                               'gerber_circle_steps': 8, 'geometry_circle_steps_mode': 'fixed',
                               'geometry_circle_steps': 8})
    for row in plugin.tools.values():
        row['data']['tools_%s_method' % kind] = 1
        row['data']['tools_%s_overlap' % kind] = 80
        row['data']['tools_%s_connect' % kind] = False
    board.solid_geometry = box(0, 0, 2, 2)


def _run_queued(boards, qapp, tmp_path, kind, start):
    """The tools of the job run at once, and of the same job queued, then spoiled, then run."""
    board = boards['crop']
    plugin = Plugin(qapp, tmp_path / 'direct', kind, board, list(QUEUED_DATA), tool_data=QUEUED_DATA)
    try:
        start(plugin, threaded=False)
        expected = tools_snapshot(plugin.result())
    finally:
        plugin.close()
    plugin = Plugin(qapp, tmp_path / 'queued', kind, board, list(QUEUED_DATA), tool_data=QUEUED_DATA)
    tasks = []
    plugin.app.worker_task.emit.side_effect = tasks.append
    saved = board.solid_geometry
    try:
        start(plugin, threaded=True)
        assert len(tasks) == 1
        _spoil(plugin, kind, board)
        tasks[0]['fcn'](*tasks[0]['params'])
        actual = tools_snapshot(plugin.result())
    finally:
        board.solid_geometry = saved
        plugin.close()
    assert expected and actual == expected


def test_ncc_queued_job_ignores_later_changes(boards, qapp, tmp_path):
    def start(plugin, threaded):
        if threaded:
            run_ncc_click(plugin, margin=0.5)
        else:
            plugin.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
            run_ncc_click(plugin, margin=0.5)
    _run_queued(boards, qapp, tmp_path, 'ncc', start)


def test_paint_queued_job_ignores_later_changes(boards, qapp, tmp_path):
    def start(plugin, threaded):
        dias = [row['tooldia'] for row in plugin.tools.values()]
        result = plugin.tool.gen.paint_poly_all(
            plugin.source, tooldia=dias, outname='board_paint', plot=False, run_threaded=threaded, rest=False)
        assert result != 'fail'
    _run_queued(boards, qapp, tmp_path, 'paint', start)
