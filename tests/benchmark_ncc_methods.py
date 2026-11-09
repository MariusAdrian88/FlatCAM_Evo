"""Run: .venv\\Scripts\\python.exe tests/benchmark_ncc_methods.py --mode baseline --cases smoke --output reports/x.json

Baseline quality and performance of NCC rest machining (NccModel.prepare_area / generate_tools with the real
clearing engines of camlib.Geometry). For each case it records the raw residual, the protected-copper violations, the
ordered machining metrics (cut length, rapid XY length, lifts, idealized time) and the generation seconds. Audit
checks run outside the timed section.

Every case runs in a dedicated child process. The parent owns the PID of that child, enforces the overall deadline
(--budget-seconds, 0 = none) and on a timeout stops only that child and its own descendants, marks the output
incomplete and returns nonzero. The modes adaptive and compare and the backends threads and processes do not exist yet.

--mode methods runs the complete job once per method 0 (Standard), 1 (Seed), 2 (Lines) and 3 (Combo, information) with
the connect setting of --connect (default on in this mode) and reports the mixing potential: per tool the best method
and per region (each polygon that generate_tools passes to the engine) the best method by the local ordered time
(machining_cost of the paths of that polygon). The gains of "best method per tool" and of "best method per region" are
shown against the best single method of the job. The regions of a rest tool depend on the method of the earlier
tools, so the per-region analysis uses the regions of the best single method's chain and runs the other methods on
those polygons. The per-region figure is an estimate: the sum of local times ignores the ordering between regions.

Exit codes: 0 complete, 1 a case failed, 2 bad arguments or an unavailable mode / backend, 3 the budget ran out.
"""
import argparse
import hashlib
import json
import math
import os
import platform
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'
for entry in (str(ROOT), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import shapely  # noqa: E402
from shapely import LineString, MultiLineString, MultiPolygon, Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

# the idealized machine: feeds in mm/min, Z in mm
MOTION = {'start': (0.0, 0.0), 'feed': 120.0, 'plunge': 60.0, 'rapid': 600.0,
          'surface_z': 0.0, 'travel_z': 2.0, 'cut_z': -0.1, 'passes': 1}

# the settings of every run (the tool table values of the GUI)
OVERLAP_PERCENT = 20
METHOD = 0                  # 0 standard (shrink): the method of the NCC test tool tables
CONTOUR = True
CONNECT = False
SIMPLIFICATION = 0.0
MARGIN = 1.0
CIRCLE_STEPS = 64           # the steps of the full circle
DECIMALS = 4
NEAR_EQUAL_ERROR_UM = 2.0

SEQUENCES = {
    'coverage': [1.0, 0.4],
    'board': [0.8, 0.4, 0.2],
    'channel': [0.4, 0.2],
    'near_equal': [1.0, 0.99],
}
SMOKE_CASES = ['hole', 'narrow_channel']
ALL_CASES = SMOKE_CASES + ['square', 'neck', 'branch', 'board', 'board_islands', 'near_equal',
                           'gerber_mixed', 'gerber_eagle_top', 'gerber_eagle_bottom']
GERBER_FILES = {
    'gerber_mixed': 'tests/fixtures/unify/unify_mixed.gbr',
    'gerber_eagle_top': 'tests/fixtures/eagle/eagle.GTL',
    'gerber_eagle_bottom': 'tests/fixtures/eagle/eagle.GBL',
}
UNAVAILABLE = {
    'mode': {'adaptive', 'compare'},
    'backend': {'threads', 'processes', 'all'},
}

_lib = None


def lib():
    """The heavy imports, once, and only in a process that generates (the supervisor stays light)."""
    global _lib
    if _lib is None:
        saved = sys.argv
        sys.argv = [sys.argv[0]]
        try:
            import camlib
            from appPlugins.ToolNCC import NccModel
            from appPlugins.ToolPaint import PaintModel
            from defaults import AppDefaults
        finally:
            sys.argv = saved
        _lib = {'camlib': camlib, 'NccModel': NccModel, 'PaintModel': PaintModel, 'AppDefaults': AppDefaults}
    return _lib


# ----------------------------------------------------------------------------------------------------------------
# idealized machining time
# ----------------------------------------------------------------------------------------------------------------

def line_paths(paths):
    """The simple, non-empty lines of a list of paths (LineString / LinearRing / MultiLineString)."""
    lines = []
    for path in paths or []:
        if path is None or path.is_empty:
            continue
        if isinstance(path, MultiLineString):
            lines.extend(part for part in path.geoms if not part.is_empty)
        elif isinstance(path, LineString):
            lines.append(path)
    return lines


def machining_cost(paths, diameter, motion):
    """
    The idealized machining of the paths of one tool: the paths in the order of order_paths() from motion['start']
    (as before G-code), each entered at the end that order_paths() gives and left at its other end. The time is the
    cut length at the XY feed, the rapid XY travel at the rapid feed, and per path one plunge (travel Z to cut Z at
    the plunge feed) and one retract (at the rapid feed). No acceleration, dwell or tool change.

    :return: dict(paths, lifts, cut_mm, rapid_mm, seconds)
    """
    from appCommon.PathOrder import order_paths
    ordered = order_paths(line_paths(paths), start=motion['start'])
    x, y = motion['start']
    cut = rapid = 0.0
    for entry, path in ordered:
        rapid += math.hypot(entry[0] - x, entry[1] - y)
        cut += path.length
        first, last = path.coords[0], path.coords[-1]
        # a closed path ends where it starts
        end = last if (entry[0], entry[1]) == (first[0], first[1]) else first
        x, y = end[0], end[1]
    count = len(ordered)
    depth = (motion['travel_z'] - motion['cut_z']) * motion.get('passes', 1)
    seconds = (cut / motion['feed'] + rapid / motion['rapid']
               + count * (depth / motion['plunge'] + depth / motion['rapid'])) * 60.0
    return {'paths': count, 'lifts': count, 'cut_mm': cut, 'rapid_mm': rapid, 'seconds': seconds}


# ----------------------------------------------------------------------------------------------------------------
# fingerprints
# ----------------------------------------------------------------------------------------------------------------

def fingerprint(geometry):
    """A hash of the WKB of a geometry or of a (nested) list of geometries."""
    digest = hashlib.sha1()

    def walk(item):
        if item is None:
            return
        if isinstance(item, (list, tuple)):
            for part in item:
                walk(part)
        elif hasattr(item, 'wkb'):
            digest.update(item.wkb)

    walk(geometry)
    return digest.hexdigest()[:16]


def paths_hash(paths):
    digest = hashlib.sha1()
    for path in line_paths(paths):
        digest.update(path.wkb)
    return digest.hexdigest()[:16]


# ----------------------------------------------------------------------------------------------------------------
# the real engines and the generation
# ----------------------------------------------------------------------------------------------------------------

def engine_app(clearing_error_um=None):
    """The app options of the engines: the factory edge, error and tolerance (the error replaced when given)."""
    factory = lib()['AppDefaults'].factory_defaults
    options = {key: factory.get(key) for key in ('global_tolerance', 'tools_clearing_error', 'tools_clearing_edge')}
    if clearing_error_um is not None:
        options['tools_clearing_error'] = clearing_error_um
    return options


def make_engines(clearing_error_um=None):
    """
    (clear_paths, clearing_error, settings): the GUI's clear_polygon_worker without the GUI. The clearing is done by
    the real Geometry engines. clearing_error is in mm (None: the circle steps), as NccGen passes it.
    """
    from types import SimpleNamespace
    from unittest.mock import Mock
    camlib, NccModel = lib()['camlib'], lib()['NccModel']
    geometry = camlib.Geometry.__new__(camlib.Geometry)
    options = engine_app(clearing_error_um)
    geometry.app = SimpleNamespace(abort_flag=False, log=Mock(), inform_no_echo=Mock(), proc_container=Mock(),
                                   units='MM', app_units='MM', options=options)

    def clear_paths(pol, tooldia, ncc_method, ncc_overlap, ncc_connect, ncc_contour, prog_plot=False,
                    simplify_tol=0.0, angle=0.0):
        paths, __ = NccModel.clear_polygon(
            pol, tooldia, ncc_method, ncc_overlap, ncc_connect, ncc_contour, prog_plot, CIRCLE_STEPS,
            geometry.clear_polygon_shrink, geometry.clear_polygon_seed, geometry.clear_polygon_lines,
            simplify_tol, geometry.clear_polygon_crosshatch, angle)
        return paths

    settings = {
        'overlap_percent': OVERLAP_PERCENT, 'method': METHOD, 'contour': CONTOUR, 'connect': CONNECT,
        'simplification': SIMPLIFICATION, 'margin_mm': MARGIN, 'circle_steps': CIRCLE_STEPS,
        'clearing_error_um': float(options['tools_clearing_error']),
        'clearing_edge': options['tools_clearing_edge'], 'global_tolerance_mm': float(options['global_tolerance']),
    }
    return clear_paths, camlib.clearing_error(geometry.app), settings, geometry.app


def generate_rest(area, diameters, clear_paths, clearing_error, app=None, copper=None, final_cleanup=False,
                  method=METHOD, connect=CONNECT, calls=None):
    """
    NCC rest machining of one area through NccModel.generate_tools (and finish_cleanup when the cleanup is on).

    :param calls: a list that receives (tool diameter, polygon, paths or None) for each polygon given to the engine
    :return: dict(paths: {diameter: [paths]}, residual, failed, seconds, domains)
    """
    NccModel, camlib = lib()['NccModel'], lib()['camlib']
    data = {'tools_ncc_overlap': OVERLAP_PERCENT, 'tools_ncc_method': method, 'tools_ncc_connect': connect,
            'tools_ncc_contour': CONTOUR, 'tools_ncc_offset_choice': False, 'tools_ncc_offset_value': 0.0,
            'tools_ncc_final_cleanup': bool(final_cleanup)}
    table = {uid: {'tooldia': dia, 'data': dict(data), 'solid_geometry': []}
             for uid, dia in enumerate(diameters, 1)}
    area = area if isinstance(area, MultiPolygon) else MultiPolygon([area]) if isinstance(area, Polygon) else area
    areas = {uid: area for uid in table}
    if calls is not None:
        engine = clear_paths

        def clear_paths(**kwargs):
            paths = engine(**kwargs)
            calls.append((kwargs['tooldia'], kwargs['pol'], paths))
            return paths
    start = time.perf_counter()
    generated, domains, residual, failed, last = NccModel.generate_tools(
        'gui_rest', table, NccModel.copy_tools(table), diameters, areas, 'bench', DECIMALS, CIRCLE_STEPS,
        clear_paths, lambda: None, lambda text: None, connect=connect, contour=CONTOUR,
        simplify_tol=SIMPLIFICATION, initial_tools={}, clearing_error=clearing_error)
    if final_cleanup:
        generated, cleanup_residual = NccModel.finish_cleanup(
            table, generated, domains, 'bench', CIRCLE_STEPS,
            lambda domain, dia, steps, previous, generate: camlib.residual_cleanup(
                app, domain, dia, steps, previous, generate),
            clear_paths, lambda: None, None, copper)
        if cleanup_residual is not None:
            residual = cleanup_residual
    seconds = time.perf_counter() - start
    paths = {dia: list(generated[uid]['solid_geometry']) if uid in generated else []
             for uid, dia in enumerate(diameters, 1)}
    return {'paths': paths, 'residual': residual, 'failed': failed, 'seconds': seconds}


# ----------------------------------------------------------------------------------------------------------------
# the audit (outside the timed section)
# ----------------------------------------------------------------------------------------------------------------

def swept(paths, diameter, quad_segs):
    return lib()['PaintModel'].swept_area(line_paths(paths), diameter, quad_segs)


def audit(variant, run, clearing_error):
    """The quality numbers of a run: the protected copper, what is left reachable, and the channel checks."""
    NccModel, camlib = lib()['NccModel'], lib()['camlib']
    quad = NccModel.quad_segs(CIRCLE_STEPS)
    area, copper = variant['area'], variant['copper']
    cut_tools = [dia for dia, paths in run['paths'].items() if paths]
    everything = unary_union([swept(paths, dia, quad) for dia, paths in run['paths'].items() if paths]
                             or [Polygon()])
    out = {}
    smallest = min(cut_tools) if cut_tools else None
    if smallest is not None:
        # a cut past the outline within twice the accepted error is the normal touch of the edge
        tolerance = 2 * camlib.accepted_error(clearing_error, smallest / 2, quad)
        violation = everything.intersection(copper.buffer(-tolerance, quad_segs=quad)) if copper is not None \
            else Polygon()
        out['copper_violation_area'] = violation.area
        out['copper_violation_tolerance_mm'] = tolerance
        # the material that the smallest cutter can reach and no cut touched
        radius = smallest / 2
        reach = area.buffer(-radius, quad_segs=quad).buffer(radius, quad_segs=quad)
        left = reach.difference(everything)
        out['unreached_area'] = left.area
        out['unreached_thick_area'] = left.buffer(-tolerance, quad_segs=quad).area
    else:
        out['copper_violation_area'] = 0.0
        out['unreached_area'] = area.area
        out['unreached_thick_area'] = area.area
    channel = variant.get('channel')
    if channel is not None:
        residual = run['residual']
        faces = [box(*face).buffer(1e-6) for face in channel['faces']]
        bridges = [part for part in lib()['camlib'].flatten_shapely_geometry(residual)
                   if all(part.intersects(face) for face in faces)] if residual is not None else []
        out['channel_cut'] = bool(not everything.is_empty and everything.buffer(1e-9).covers(channel['centre']))
        out['bridge_components'] = len(bridges)
    return out


def residual_record(residual):
    if residual is None or residual.is_empty:
        return {'area': 0.0, 'components': 0, 'wkb_sha1': fingerprint(residual) if residual is not None else None}
    return {'area': residual.area, 'components': len(getattr(residual, 'geoms', [residual])),
            'wkb_sha1': fingerprint(residual)}


def run_variant(variant, repeat=1, method=METHOD, connect=CONNECT, calls=None):
    """
    Run one input: the generation `repeat` times (timed), then the audit and the metrics of the last run.
    """
    sources = variant['sources']
    before = {key: fingerprint(value) for key, value in sources.items()}
    clear_paths, clearing_error, settings, app = make_engines(variant.get('clearing_error_um'))
    samples = []
    hashes = set()
    run = None
    for __ in range(max(1, repeat)):
        if calls is not None:
            del calls[:]
        run = generate_rest(variant['area'], variant['diameters'], clear_paths, clearing_error, app,
                            variant['copper'], method=method, connect=connect, calls=calls)
        samples.append(run['seconds'])
        hashes.add((paths_hash([p for paths in run['paths'].values() for p in paths]), fingerprint(run['residual'])))
    quality = audit(variant, run, clearing_error)
    tools = []
    totals = {'paths': 0, 'lifts': 0, 'cut_mm': 0.0, 'rapid_mm': 0.0, 'seconds': 0.0}
    for dia in variant['diameters']:
        cost = machining_cost(run['paths'][dia], dia, MOTION)
        cost['diameter'] = dia
        cost['status'] = 'cut' if cost['paths'] else 'no_paths'
        tools.append(cost)
        for key in totals:
            totals[key] += cost[key]
    after = {key: fingerprint(value) for key, value in sources.items()}
    return {
        'label': variant['label'], 'diameters': variant['diameters'],
        'settings': dict(settings, method=method, connect=connect),
        'generation_seconds': {'samples': samples, 'median': statistics.median(samples)},
        'deterministic': len(hashes) == 1,
        'failed': bool(run['failed']),
        'residual': residual_record(run['residual']),
        'tools': tools, 'ideal': totals, 'quality': quality,
        'source': {'before': before, 'after': after, 'unchanged': before == after},
    }


# ----------------------------------------------------------------------------------------------------------------
# --mode methods: one job per method and the mixing potential
# ----------------------------------------------------------------------------------------------------------------

METHOD_NAMES = {0: 'standard', 1: 'seed', 2: 'lines', 3: 'combo'}
MIX_METHODS = (0, 1, 2)         # the engines that a mix may use (Combo is a fallback chain, shown for information)


def region_analysis(variant, calls, best, connect):
    """
    The best method per region (each polygon passed to the engine), by the local ordered time of its paths. The
    regions are those of the best single method's job; the paths of another method on the same polygon come from its
    own job when it cut the identical polygon (the first tool), else from running its engine on the polygon.
    """
    clear_paths = make_engines(variant.get('clearing_error_um'))[0]
    known = {}
    for method in MIX_METHODS:
        for dia, pol, paths in calls[method]:
            if paths is not None:
                known[(method, dia, pol.wkb)] = paths
    diameters = variant['diameters']
    tools = [{'diameter': dia, 'regions': 0, 'single_seconds': 0.0, 'best_seconds': 0.0, 'differs': 0,
              'wins': {METHOD_NAMES[m]: 0 for m in MIX_METHODS}, 'engine_runs': 0} for dia in diameters]
    for dia, pol, paths in calls[best]:
        if paths is None:
            continue
        index = min(range(len(diameters)), key=lambda i: abs(diameters[i] - dia))
        entry = tools[index]
        seconds = {}
        for method in MIX_METHODS:
            got = known.get((method, dia, pol.wkb))
            if got is None and method != best:
                entry['engine_runs'] += 1
                try:
                    got = clear_paths(pol=pol, tooldia=dia, ncc_method=method, ncc_overlap=OVERLAP_PERCENT / 100.0,
                                      ncc_connect=connect, ncc_contour=CONTOUR, prog_plot=False,
                                      simplify_tol=SIMPLIFICATION)
                except Exception:
                    got = None
                known[(method, dia, pol.wkb)] = got
            if got is None:
                continue
            seconds[method] = machining_cost(got, dia, MOTION)['seconds']
        if best not in seconds:
            continue
        winner = min(MIX_METHODS, key=lambda m: seconds.get(m, math.inf))
        if seconds[winner] >= seconds[best] * (1 - 1e-12):
            winner = best
        entry['regions'] += 1
        entry['single_seconds'] += seconds[best]
        entry['best_seconds'] += seconds[winner]
        entry['differs'] += winner != best
        entry['wins'][METHOD_NAMES[winner]] += 1
    return tools


def run_methods(variant, repeat, connect):
    """The record of one input in methods mode: the 4 jobs, the best single method, the mixing potential."""
    jobs, calls = {}, {}
    for method in METHOD_NAMES:
        calls[method] = []
        jobs[method] = run_variant(variant, repeat, method, connect, calls[method])
    best = min(MIX_METHODS, key=lambda m: jobs[m]['ideal']['seconds'])
    single = jobs[best]['ideal']['seconds']
    per_tool = []
    for i, dia in enumerate(variant['diameters']):
        seconds = {METHOD_NAMES[m]: jobs[m]['tools'][i]['seconds'] for m in METHOD_NAMES}
        winner = min(MIX_METHODS, key=lambda m: jobs[m]['tools'][i]['seconds'])
        per_tool.append({'diameter': dia, 'seconds': seconds, 'best': METHOD_NAMES[winner]})
    tool_total = sum(min(jobs[m]['tools'][i]['seconds'] for m in MIX_METHODS) for i in range(len(per_tool)))
    regions = region_analysis(variant, calls, best, connect)
    saved = sum(t['single_seconds'] - t['best_seconds'] for t in regions)
    mixing = {
        'best_single': METHOD_NAMES[best], 'best_single_seconds': single,
        'per_tool': per_tool, 'per_tool_seconds': tool_total, 'per_tool_gain_seconds': single - tool_total,
        'per_tool_gain_percent': 100.0 * (single - tool_total) / single if single else 0.0,
        'regions': regions, 'per_region_seconds': single - saved, 'per_region_gain_seconds': saved,
        'per_region_gain_percent': 100.0 * saved / single if single else 0.0,
        'notes': 'per tool: the best method of each tool taken from the single-method jobs (rest stock differs per '
                 'chain); per region: local times on the regions of the best single method chain, the ordering '
                 'between regions is not modelled',
    }
    return {'label': variant['label'], 'diameters': variant['diameters'], 'connect': connect,
            'jobs': {METHOD_NAMES[m]: jobs[m] for m in METHOD_NAMES}, 'mixing': mixing}


def print_methods(variant):
    print('%s  connect %s' % (variant['label'], 'on' if variant['connect'] else 'off'), flush=True)
    for name, job in variant['jobs'].items():
        ideal = job['ideal']
        print('  %-9s ideal %8.1fs cut %8.1f rapid %7.1f lifts %5d residual %9.5f mm2 %s gen %6.2fs viol %.1e '
              'unreached %.1e' % (
                  name, ideal['seconds'], ideal['cut_mm'], ideal['rapid_mm'], ideal['lifts'],
                  job['residual']['area'], job['residual']['wkb_sha1'], job['generation_seconds']['median'],
                  job['quality']['copper_violation_area'], job['quality']['unreached_thick_area']), flush=True)
    mixing = variant['mixing']
    print('  best single method: %s %.1fs' % (mixing['best_single'], mixing['best_single_seconds']))
    for tool in mixing['per_tool']:
        print('  tool %.3f best %-8s %s' % (tool['diameter'], tool['best'], ' '.join(
            '%s %.1f' % (name, value) for name, value in tool['seconds'].items())))
    for tool in mixing['regions']:
        print('  tool %.3f regions %4d local single %8.1fs best-per-region %8.1fs differs %4d wins %s runs %d' % (
            tool['diameter'], tool['regions'], tool['single_seconds'], tool['best_seconds'], tool['differs'],
            tool['wins'], tool['engine_runs']))
    print('  mixing: per tool %.1fs (%.2f %%)  per region %.1fs (%.2f %%)  vs best single %.1fs' % (
        mixing['per_tool_seconds'], mixing['per_tool_gain_percent'], mixing['per_region_seconds'],
        mixing['per_region_gain_percent'], mixing['best_single_seconds']), flush=True)


# ----------------------------------------------------------------------------------------------------------------
# the inputs (all exist already; nothing is copied)
# ----------------------------------------------------------------------------------------------------------------

def envelope_copper(area, margin=1.0):
    """The synthetic copper: everything around the area (the pads, the outside of the outline)."""
    minx, miny, maxx, maxy = area.bounds
    return box(minx - margin, miny - margin, maxx + margin, maxy + margin).difference(area)


def synthetic(label, area, diameters, clearing_error_um=None, **extra):
    variant = {'label': label, 'area': area, 'copper': envelope_copper(area), 'diameters': diameters,
               'clearing_error_um': clearing_error_um, 'sources': {'area': area}}
    variant.update(extra)
    variant['sources']['copper'] = variant['copper']
    return variant


def gerber_variant(case):
    """The Gerber as production prepares it: the parser of the objects, the composed copper, NccModel.prepare_area."""
    from unittest.mock import patch
    from PyQt6 import QtWidgets
    path = ROOT / GERBER_FILES[case]
    if not path.is_file():
        raise FileNotFoundError('mandatory input missing: %s' % path)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    saved = sys.argv
    sys.argv = [sys.argv[0]]        # the application modules parse the command line when imported
    try:
        patch('appObjects.AppObjectTemplate.ShapeCollection').start()
        from helpers_objects import make_app, make_gerber
        from appPlugins.ToolNCC.NccGen import ncc_gerber_copper
    finally:
        sys.argv = saved
    NccModel = lib()['NccModel']
    obj = make_gerber(make_app(), str(path))
    if str(obj.units).upper() != 'MM':
        obj.convert_units('MM')
    copper = ncc_gerber_copper(obj, None)
    region = NccModel.entire_object(obj.solid_geometry)
    boundary = NccModel.apply_margin(region, None, 0, MARGIN)
    area, __, __, warnings = NccModel.prepare_area(
        copper, boundary, [], {}, 'bench', DECIMALS, False, 0.0, MARGIN, None, None, NccModel.complement,
        lambda: None, None, CIRCLE_STEPS)
    if isinstance(area, str):
        raise RuntimeError('%s: the area to clear is %s' % (case, area))
    return {'label': case, 'area': area, 'copper': copper, 'diameters': SEQUENCES['board'],
            'clearing_error_um': None, 'warnings': warnings,
            'sources': {'solid_geometry': obj.solid_geometry, 'copper': copper, 'area': area}}


def build_variants(case):
    """The inputs of a case: a list of variants (the narrow channel has one for each gap ratio)."""
    import test_clearing_paths as paths_cases
    if case in ('hole', 'square', 'neck', 'branch'):
        area, __ = paths_cases.TestClearingCoverage.SHAPES[case]
        return [synthetic(case, area, SEQUENCES['coverage'])]
    if case == 'narrow_channel':
        maker = paths_cases.TestNarrowChannel('test_every_method_cuts_the_channel')
        maker.setUp()
        variants = []
        for ratio in (1.01, 1.02, 1.04, 1.06):
            area, channel = maker.area_and_channel(ratio)
            gap = maker.tooldia * ratio
            variants.append(synthetic(
                'narrow_channel_%.2f' % ratio, area, SEQUENCES['channel'],
                channel={'faces': [(2, 2, 2, 3), (2 + gap, 2, 2 + gap, 3)],
                         'centre': LineString([(2 + gap / 2, 2.0), (2 + gap / 2, 3.0)])}))
        return variants
    if case in ('board', 'board_islands'):
        import test_clearing_speed as speed_cases
        return [synthetic(case, speed_cases.board(islands=(case == 'board_islands')), SEQUENCES['board'])]
    if case == 'near_equal':
        # the geometry of test_paint_performance: 1.00 then 0.99 mm, 2 um clearing error
        area = box(0, 0, 6, 6).difference(box(2, 2, 4, 4))
        return [synthetic(case, area, SEQUENCES['near_equal'], NEAR_EQUAL_ERROR_UM)]
    if case in GERBER_FILES:
        return [gerber_variant(case)]
    raise ValueError('unknown case %s' % case)


# ----------------------------------------------------------------------------------------------------------------
# child and supervisor
# ----------------------------------------------------------------------------------------------------------------

def child_main(args):
    result = {'case': args.child}
    code = 0
    # the application modules parse the command line when they are imported (also lazily, by the objects)
    sys.argv = [sys.argv[0]]
    try:
        started = time.perf_counter()
        if args.mode == 'methods':
            connect = args.connect != 'off'
            result['variants'] = [run_methods(variant, args.repeat, connect)
                                  for variant in build_variants(args.child)]
        else:
            connect = args.connect == 'on'
            result['variants'] = [run_variant(variant, args.repeat, METHOD, connect)
                                  for variant in build_variants(args.child)]
        result['wall_seconds'] = time.perf_counter() - started
    except BaseException:
        result['error'] = traceback.format_exc()
        code = 1
    Path(args.result).write_text(json.dumps(result), encoding='utf-8')
    return code


def stop_tree(proc):
    """Stop the child this harness started and its own descendants; nothing else."""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=20)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=20)
    except Exception:
        pass


def run_case(case, args, deadline, workdir):
    """Run one case in its own child process. Returns (record, timed_out)."""
    result_file = Path(workdir) / (case + '.json')
    log_file = Path(workdir) / (case + '.log')
    command = [sys.executable, str(Path(__file__).resolve()), '--child', case, '--repeat', str(args.repeat),
               '--result', str(result_file), '--mode', args.mode, '--connect', args.connect]
    kwargs = {'start_new_session': True} if os.name != 'nt' else {}
    with open(log_file, 'wb') as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT), **kwargs)
        timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_tree(proc)
            return {'case': case, 'error': 'timeout: the budget of %s s ran out' % args.budget_seconds}, True
    if result_file.is_file():
        return json.loads(result_file.read_text(encoding='utf-8')), False
    tail = log_file.read_text(encoding='utf-8', errors='replace')[-2000:]
    return {'case': case, 'error': 'the child exited with %s and no result\n%s' % (proc.returncode, tail)}, False


def print_summary(record):
    if 'error' in record:
        print('%-22s ERROR %s' % (record['case'], record['error'].strip().splitlines()[-1]))
        return
    for variant in record['variants']:
        if 'jobs' in variant:
            print_methods(variant)
            continue
        ideal, quality = variant['ideal'], variant['quality']
        print('%-22s gen %7.2fs residual %9.5f mm2 cut %8.1f rapid %7.1f lifts %5d ideal %8.1fs violation %.2e' % (
            variant['label'], variant['generation_seconds']['median'], variant['residual']['area'],
            ideal['cut_mm'], ideal['rapid_mm'], ideal['lifts'], ideal['seconds'],
            quality['copper_violation_area']), flush=True)


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--mode', choices=['baseline', 'methods', 'adaptive', 'compare'], default='baseline')
    parser.add_argument('--backend', choices=['serial', 'threads', 'processes', 'all'], default='serial')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--cases', choices=['smoke', 'all'], default='smoke')
    parser.add_argument('--case', action='append', choices=ALL_CASES, help='run only this case (repeatable)')
    parser.add_argument('--connect', choices=['on', 'off', 'default'], default='default',
                        help='connect the paths: default is on in --mode methods and off otherwise')
    parser.add_argument('--output', default=None)
    parser.add_argument('--budget-seconds', type=float, default=30.0)
    parser.add_argument('--child', help=argparse.SUPPRESS)
    parser.add_argument('--result', help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    if args.child:
        return child_main(args)
    for option in ('mode', 'backend'):
        if getattr(args, option) in UNAVAILABLE[option]:
            print('error: --%s %s is not available yet (only baseline, methods and serial exist)' % (
                option, getattr(args, option)), file=sys.stderr)
            return 2
    if args.repeat < 1 or args.workers < 1 or args.budget_seconds < 0:
        print('error: --repeat and --workers must be at least 1, --budget-seconds at least 0', file=sys.stderr)
        return 2
    cases = args.case or (SMOKE_CASES if args.cases == 'smoke' else ALL_CASES)
    deadline = None if args.budget_seconds == 0 else time.monotonic() + args.budget_seconds
    report = {
        'schema': 1, 'mode': args.mode, 'backend': args.backend, 'workers': args.workers, 'repeat': args.repeat,
        'cases_selected': args.case or args.cases, 'connect': args.connect, 'budget_seconds': args.budget_seconds, 'complete': False,
        'environment': {'python': sys.version.split()[0], 'shapely': shapely.__version__,
                        'geos': shapely.geos_version_string, 'platform': platform.platform()},
        'motion': MOTION, 'sequences': SEQUENCES, 'cases': [],
    }
    status = 0
    timed_out = False
    with tempfile.TemporaryDirectory(prefix='ncc_overlap_') as workdir:
        for case in cases:
            record, timed_out = run_case(case, args, deadline, workdir)
            report['cases'].append(record)
            print_summary(record)
            if 'error' in record:
                status = 3 if timed_out else max(status, 1)
            if timed_out:
                break
    report['complete'] = status == 0 and len(report['cases']) == len(cases)
    report['skipped_cases'] = cases[len(report['cases']):]
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1), encoding='utf-8')
    if not report['complete']:
        print('INCOMPLETE: %s' % ('the budget ran out' if timed_out else 'a case failed'), file=sys.stderr)
    return status


if __name__ == '__main__':
    sys.exit(main())
