# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 09/27/2019                                         #
# MIT Licence                                              #
# ##########################################################

# Pure algorithms of the Check Rules plugin: no Qt, all functions are top level so they can be pickled for a Pool.
# Every check returns the dict built by make_result() and never raises.

import builtins
import functools
from html import escape

import numpy as np
import shapely

from shapely import STRtree, Polygon, LineString, Point, unary_union
from shapely.ops import nearest_points


def _(text):
    # translate through the app gettext when it is installed, plain text otherwise (tests, Tcl)
    func = builtins.__dict__.get('_')
    return func(text) if func else text


def make_result(rule, limit, names):
    return {
        'rule': rule,
        'limit': limit,
        'names': list(names),
        'violations': [],
        'checked': 0,
        'error': None,
    }


def _error(rule, limit, names, message):
    result = make_result(rule, limit, names)
    result['error'] = message
    return result


def _safe(func):
    # a check must not raise: any failure becomes an 'error' result (rule and size are the last two arguments)
    @functools.wraps(func)
    def wrapper(*args):
        try:
            return func(*args)
        except Exception as err:
            return _error(args[-1], args[-2], [], '%s: %s' % (type(err).__name__, err))
    return wrapper


# ------------------------------------------------------------------------------------------------------------------
# geometry helpers
# ------------------------------------------------------------------------------------------------------------------
def _polygons(geom):
    # flatten any shapely geometry (or nested list of them) into a list of non empty Polygons
    if geom is None:
        return []
    if isinstance(geom, (list, tuple)):
        out = []
        for g in geom:
            out += _polygons(g)
        return out
    if isinstance(geom, Polygon):
        return [] if geom.is_empty else [geom]
    if hasattr(geom, 'geoms'):
        out = []
        for g in geom.geoms:
            out += _polygons(g)
        return out
    return []


def _union_polygons(geom):
    # union so touching polygons become one connected area
    flat = _polygons(geom)
    if len(flat) < 2:
        return flat
    try:
        merged = unary_union(flat)
    except Exception:
        merged = unary_union([p.buffer(0) for p in flat])
    return _polygons(merged)


def _layer_polygons(layer):
    return _union_polygons(layer.get('geo') or [])


def _boundaries(polygons):
    # exterior and interior rings of all polygons as LineStrings
    out = []
    for poly in polygons:
        out.append(LineString(poly.exterior.coords))
        for ring in poly.interiors:
            out.append(LineString(ring.coords))
    return out


def _mid(geo_a, geo_b):
    p1, p2 = nearest_points(geo_a, geo_b)
    return float((p1.x + p2.x) / 2), float((p1.y + p2.y) / 2)


def _violation(layer, x, y, value):
    return {'layer': layer, 'x': float(x), 'y': float(y), 'value': float(value)}


def _pairs(first, second, size, same=False):
    # (i, j, distance) for geometries of the two lists closer than size; same=True reports each pair once
    if not first or not second:
        return []
    tree = STRtree(second)
    ids = tree.query(first, predicate='dwithin', distance=size)
    a, b = ids[0], ids[1]
    if same:
        keep = a < b
        a, b = a[keep], b[keep]
    if len(a) == 0:
        return []
    arr_a = np.empty(len(first), dtype=object)
    arr_a[:] = first
    arr_b = np.empty(len(second), dtype=object)
    arr_b[:] = second
    dist = shapely.distance(arr_a[a], arr_b[b])
    return [(int(i), int(j), float(d)) for i, j, d in zip(a, b, dist) if d < size]


def _drill_items(exc_layers, holes=True, slots=True):
    # (layer name, x, y, dia, geometry, is_slot) of all drills and slots
    items = []
    for layer in exc_layers or []:
        if not layer:
            continue
        name = layer.get('name', '')
        if holes:
            for h in layer.get('holes', []):
                items.append((name, h['x'], h['y'], h['dia'], h['geo'], False))
        if slots:
            for s in layer.get('slots', []):
                items.append((name, s['x'], s['y'], s['dia'], s['geo'], True))
    return items


def _layer_name(layer):
    return layer.get('name', '') if layer else ''


def _names(*layers):
    return [_layer_name(layer) for layer in layers if layer]


# ------------------------------------------------------------------------------------------------------------------
# checks
# ------------------------------------------------------------------------------------------------------------------
@_safe
def check_clearance_within(layer, size, rule):
    if not layer:
        return _error(rule, size, [], _('Layer is missing.'))
    result = make_result(rule, size, _names(layer))
    polys = _layer_polygons(layer)
    result['checked'] = len(polys)
    for i, j, dist in _pairs(polys, polys, size, same=True):
        x, y = _mid(polys[i], polys[j])
        result['violations'].append(_violation(layer['name'], x, y, dist))
    return result


@_safe
def check_clearance_between(layer_a, layer_b, size, rule):
    if not layer_a or not layer_b:
        return _error(rule, size, _names(layer_a, layer_b), _('A layer is missing.'))
    result = make_result(rule, size, _names(layer_a, layer_b))
    polys_a = _layer_polygons(layer_a)
    polys_b = _layer_polygons(layer_b)
    result['checked'] = len(polys_a)
    for i, j, dist in _pairs(polys_a, polys_b, size):
        poly_a, poly_b = polys_a[i], polys_b[j]
        if poly_b.contains(poly_a):
            # filled outline around the copper: measure to its edge
            target = poly_b.boundary
            dist = poly_a.distance(target)
            if dist >= size:
                continue
        else:
            target = poly_b
        x, y = _mid(poly_a, target)
        result['violations'].append(_violation(layer_a['name'], x, y, dist))
    return result


def _track_width(aperture):
    # drawn width of the aperture, None when it cannot be told (macros, regions)
    kind = aperture.get('type')
    if kind == 'C':
        return aperture.get('size')
    if kind in ('R', 'O'):
        values = [v for v in (aperture.get('width'), aperture.get('height')) if v is not None]
        return min(values) if values else None
    if kind == 'P':
        return aperture.get('diam')
    return None


@_safe
def check_trace_size(layers, size, rule):
    layers = [lay for lay in layers or [] if lay]
    if not layers:
        return _error(rule, size, [], _('Layer is missing.'))
    result = make_result(rule, size, _names(*layers))
    skipped = 0
    for layer in layers:
        for aperture in layer.get('apertures', {}).values():
            tracks = [t for t in aperture.get('tracks', []) if t is not None and not t.is_empty]
            if not tracks:
                continue
            width = _track_width(aperture)
            if width is None:
                skipped += len(tracks)
                continue
            width = float(width)
            result['checked'] += len(tracks)
            if width <= 0 or width >= size:
                continue
            for track in tracks:
                if track.geom_type == 'LineString':
                    pt = track.interpolate(0.5, normalized=True)
                else:
                    pt = track.representative_point()
                result['violations'].append(_violation(layer['name'], pt.x, pt.y, width))
    result['skipped'] = skipped
    return result


@_safe
def check_annular_ring(copper_layer, exc_layers, size, rule):
    exc_layers = [lay for lay in exc_layers or [] if lay]
    if not copper_layer or not exc_layers:
        return _error(rule, size, _names(copper_layer, *exc_layers), _('A copper and an Excellon layer are required.'))
    result = make_result(rule, size, _names(copper_layer, *exc_layers))
    polys = _layer_polygons(copper_layer)
    tree = STRtree(polys) if polys else None
    skipped = 0
    for name, x, y, dia, geo, _slot in _drill_items(exc_layers, slots=False):
        center = Point(x, y)
        pad = None
        if tree is not None:
            hits = [polys[k] for k in tree.query(center, predicate='intersects')]
            pad = hits[0] if hits else None
            if pad is None:
                # copper with a pre-cut hole: the centre lies in an interior ring of a pad around it
                for k in tree.query(geo, predicate='intersects'):
                    if any(Polygon(ring).contains(center) for ring in polys[k].interiors):
                        pad = polys[k]
                        break
        if pad is None:
            skipped += 1
            continue
        result['checked'] += 1
        rings = [LineString(pad.exterior.coords)]
        rings += [LineString(r.coords) for r in pad.interiors if not Polygon(r).contains(center)]
        if not Polygon(pad.exterior).contains(geo):
            ring_width = 0.0
            px, py = geo.centroid.x, geo.centroid.y
        else:
            edge = geo.exterior
            ring_width = min(edge.distance(r) for r in rings)
            px = py = None
        if ring_width < size:
            if px is None:
                nearest = min(rings, key=lambda r: geo.exterior.distance(r))
                px, py = _mid(geo.exterior, nearest)
            result['violations'].append(_violation(name, px, py, ring_width))
    result['skipped'] = skipped
    return result


@_safe
def check_hole_clearance(exc_layers, size, rule):
    items = _drill_items(exc_layers)
    if not [lay for lay in exc_layers or [] if lay]:
        return _error(rule, size, [], _('An Excellon layer is required.'))
    result = make_result(rule, size, _names(*exc_layers))
    result['checked'] = len(items)
    geos = [it[4] for it in items]
    for i, j, dist in _pairs(geos, geos, size, same=True):
        x, y = _mid(geos[i], geos[j])
        result['violations'].append(_violation(items[i][0], x, y, dist))
    return result


@_safe
def check_hole_size(exc_layers, size, rule):
    if not [lay for lay in exc_layers or [] if lay]:
        return _error(rule, size, [], _('An Excellon layer is required.'))
    result = make_result(rule, size, _names(*exc_layers))
    seen = set()
    for name, x, y, dia, _geo, _slot in _drill_items(exc_layers, slots=False):
        key = (round(x, 4), round(y, 4), round(dia, 4))
        if key in seen:
            continue
        seen.add(key)
        result['checked'] += 1
        if dia < size:
            result['violations'].append(_violation(name, x, y, dia))
    return result


@_safe
def check_slot_size(exc_layers, size, rule):
    if not [lay for lay in exc_layers or [] if lay]:
        return _error(rule, size, [], _('An Excellon layer is required.'))
    result = make_result(rule, size, _names(*exc_layers))
    for name, x, y, dia, _geo, _slot in _drill_items(exc_layers, holes=False):
        result['checked'] += 1
        if dia < size:
            result['violations'].append(_violation(name, x, y, dia))
    return result


@_safe
def check_hole_to_outline(exc_layers, outline_layer, size, rule):
    if not outline_layer or not [lay for lay in exc_layers or [] if lay]:
        return _error(rule, size, _names(outline_layer), _('An Excellon and an Outline layer are required.'))
    result = make_result(rule, size, _names(*exc_layers, outline_layer))
    items = _drill_items(exc_layers)
    result['checked'] = len(items)
    edges = _boundaries(_layer_polygons(outline_layer))
    geos = [it[4] for it in items]
    best = {}
    for i, j, dist in _pairs(geos, edges, size):
        if i not in best or dist < best[i][0]:
            best[i] = (dist, j)
    for i in sorted(best):
        dist, j = best[i]
        x, y = _mid(geos[i], edges[j])
        result['violations'].append(_violation(items[i][0], x, y, dist))
    return result


@_safe
def check_hole_to_copper(exc_layers, copper_layer, size, rule):
    if not copper_layer or not [lay for lay in exc_layers or [] if lay]:
        return _error(rule, size, _names(copper_layer), _('An Excellon and a copper layer are required.'))
    result = make_result(rule, size, _names(*exc_layers, copper_layer))
    items = _drill_items(exc_layers)
    result['checked'] = len(items)
    polys = _layer_polygons(copper_layer)
    geos = [it[4] for it in items]
    for i, j, dist in _pairs(geos, polys, size):
        # the pad around the hole is its own net
        if polys[j].contains(Point(items[i][1], items[i][2])):
            continue
        x, y = _mid(geos[i], polys[j])
        result['violations'].append(_violation(items[i][0], x, y, dist))
    return result


def check_missing(message, size, rule):
    # placeholder job for an enabled rule whose layers are missing
    return _error(rule, size, [], message)


# ------------------------------------------------------------------------------------------------------------------
# layer builders (GUI thread, from app objects)
# ------------------------------------------------------------------------------------------------------------------
def _object_name(obj):
    try:
        return str(obj.obj_options['name'])
    except Exception:
        return str(getattr(obj, 'name', ''))


def _flat_geometry(geom):
    # solid_geometry may be a geometry, a list, or nested lists
    if geom is None:
        return []
    if isinstance(geom, (list, tuple)):
        out = []
        for g in geom:
            out += _flat_geometry(g)
        return out
    return [geom]


def gerber_layer(obj):
    geo = _union_polygons([p for g in _flat_geometry(obj.solid_geometry) for p in _polygons(g)])
    apertures = {}
    for apid, tool in (getattr(obj, 'tools', None) or {}).items():
        tracks = []
        for element in tool.get('geometry', []):
            follow = element.get('follow') if isinstance(element, dict) else None
            if isinstance(follow, LineString) or getattr(follow, 'geom_type', '') == 'MultiLineString':
                if not follow.is_empty:
                    tracks.append(follow)
        if not tracks:
            continue
        apertures[apid] = {
            'type': tool.get('type'),
            'size': tool.get('size'),
            'width': tool.get('width'),
            'height': tool.get('height'),
            'diam': tool.get('diam'),
            'tracks': tracks,
        }
    return {'name': _object_name(obj), 'geo': geo, 'apertures': apertures}


def excellon_layer(obj):
    holes = []
    slots = []
    for tool in (getattr(obj, 'tools', None) or {}).values():
        dia = float(tool.get('tooldia', 0.0))
        for pt in tool.get('drills', []):
            holes.append({'x': float(pt.x), 'y': float(pt.y), 'dia': dia, 'geo': Point(pt.x, pt.y).buffer(dia / 2)})
        for start, end in tool.get('slots', []):
            line = LineString([(start.x, start.y), (end.x, end.y)])
            mid = line.interpolate(0.5, normalized=True)
            slots.append({'x': float(mid.x), 'y': float(mid.y), 'dia': dia, 'geo': line.buffer(dia / 2)})
    return {'name': _object_name(obj), 'holes': holes, 'slots': slots}


# ------------------------------------------------------------------------------------------------------------------
# job builder shared by the GUI and Tcl
# ------------------------------------------------------------------------------------------------------------------
SIDES = (('TOP', 'top'), ('BOTTOM', 'bottom'))


def _rule_value(config, key):
    entry = config.get(key) or {}
    return bool(entry.get('enabled')), float(entry.get('value', 0.0))


def _exc_list(layers):
    return [layers[k] for k in ('exc_1', 'exc_2') if layers.get(k)]


def _plan(config, layers):
    """List of (rule key, function, args) for the enabled rules, plus problem messages."""
    jobs = []
    problems = []

    def side_rule(key, title, func, make_args):
        # make_args(side_suffix) -> tuple of layers or None when a layer is missing
        on, value = _rule_value(config, key)
        if not on:
            return
        found = False
        for label, suffix in SIDES:
            args = make_args(suffix)
            if args is None:
                continue
            found = True
            jobs.append((key, func, args + (value, '%s -> %s' % (label, title))))
        if not found:
            msg = _('Rule "%s" is enabled but its layers are not selected.') % title
            problems.append(msg)
            jobs.append((key, check_missing, (msg, value, title)))

    def one_rule(key, title, func, args):
        on, value = _rule_value(config, key)
        if not on:
            return
        if args is None:
            msg = _('Rule "%s" is enabled but its layers are not selected.') % title
            problems.append(msg)
            jobs.append((key, check_missing, (msg, value, title)))
        else:
            jobs.append((key, func, args + (value, title)))

    def get(*keys):
        vals = [layers.get(k) for k in keys]
        return None if not all(vals) else tuple(vals)

    coppers = [layers[k] for k in ('copper_top', 'copper_bottom') if layers.get(k)]
    excs = _exc_list(layers)

    one_rule('trace_size', _('Trace size'), check_trace_size, ((coppers,) if coppers else None))
    side_rule('c2c', _('Copper to Copper clearance'), check_clearance_within, lambda s: get('copper_' + s))
    side_rule('c2o', _('Copper to Outline clearance'), check_clearance_between,
              lambda s: get('copper_' + s, 'outline'))
    side_rule('s2s', _('Silk to Silk clearance'), check_clearance_within, lambda s: get('silk_' + s))
    side_rule('s2sm', _('Silk to Solder Mask clearance'), check_clearance_between,
              lambda s: get('silk_' + s, 'sm_' + s))
    side_rule('s2o', _('Silk to Outline clearance'), check_clearance_between, lambda s: get('silk_' + s, 'outline'))
    side_rule('sm2sm', _('Minimum Solder Mask Sliver'), check_clearance_within, lambda s: get('sm_' + s))
    side_rule('ri', _('Minimum Annular Ring'), check_annular_ring,
              lambda s: (layers['copper_' + s], excs) if layers.get('copper_' + s) and excs else None)
    one_rule('h2h', _('Hole to Hole clearance'), check_hole_clearance, ((excs,) if excs else None))
    one_rule('dh', _('Minimum Drill Size'), check_hole_size, ((excs,) if excs else None))
    one_rule('slot', _('Minimum Slot Width'), check_slot_size, ((excs,) if excs else None))
    one_rule('h2o', _('Hole to Outline clearance'), check_hole_to_outline,
             ((excs, layers['outline']) if excs and layers.get('outline') else None))
    side_rule('h2c', _('Hole to Copper clearance'), check_hole_to_copper,
              lambda s: (excs, layers['copper_' + s]) if excs and layers.get('copper_' + s) else None)
    side_rule('iso', _('Copper gap vs isolation tool'), check_clearance_within, lambda s: get('copper_' + s))
    return jobs, problems


def build_jobs(config, layers):
    jobs, _problems = _plan(config, layers)
    return [(func, args) for _key, func, args in jobs]


def validate_config(config, layers):
    return _plan(config, layers)[1]


def run_jobs_sync(jobs):
    results = []
    for func, args in jobs:
        try:
            results.append(func(*args))
        except Exception as err:
            results.append(_error(args[-1], args[-2] if len(args) > 1 else 0.0, [],
                                  '%s: %s' % (type(err).__name__, err)))
    return results


# ------------------------------------------------------------------------------------------------------------------
# CSV export (GUI and Tcl)
# ------------------------------------------------------------------------------------------------------------------
CSV_COLUMNS = ['rule', 'layer', 'x', 'y', 'value', 'limit', 'status']


def results_status(res):
    """'ERROR', 'FAILED', 'NOTHING TO CHECK' or 'PASSED' for a result dict."""
    if res.get('error'):
        return 'ERROR'
    if res.get('violations'):
        return 'FAILED'
    if not res.get('checked'):
        return 'NOTHING TO CHECK'
    return 'PASSED'


# ------------------------------------------------------------------------------------------------------------------
# HTML report (GUI document and Tcl -outname)
# ------------------------------------------------------------------------------------------------------------------
def _num(value, decimals):
    try:
        return '%.*f' % (decimals, float(value))
    except (TypeError, ValueError):
        return escape(str(value))


def _badge(text, color):
    return '<span style="color:white;background-color:%s">&nbsp;%s&nbsp;</span>' % (color, escape(text))


def _render_result(el, decimals, units):
    if not isinstance(el, dict):
        return '<b>%s</b>&nbsp;&nbsp;%s<BR>' % (_("RULE NAME:"), escape(str(el)))

    units = escape(str(units or ''))
    violations = el.get('violations') or []
    if not isinstance(violations, (list, tuple)):
        violations = []
    names = el.get('names') or []
    if not isinstance(names, (list, tuple)):
        names = [names]

    txt = '<b>%s</b>&nbsp;&nbsp;&nbsp;&nbsp;%s<BR>' % (_("RULE NAME:"), escape(str(el.get('rule', '')).upper()))
    for name in names:
        txt += '%s: %s<BR>' % (_("File name"), escape(str(name)))
    if el.get('limit') is not None:
        txt += '%s: %s %s<BR>' % (_("Limit"), _num(el.get('limit'), decimals), units)

    if el.get('error'):
        txt += '%s: %s.<BR>%s<BR>' % (_("STATUS"), _badge(_("ERROR"), 'darkorange'), escape(str(el['error'])))
    elif violations:
        status = _badge('%s (%d %s)' % (_("FAILED"), len(violations), _("violations")), 'red')
        txt += '%s: %s.<BR>' % (_("STATUS"), status)
        for vio in violations:
            if isinstance(vio, dict):
                txt += '&nbsp;&nbsp;%s: %s, (%s, %s), %s: %s %s<BR>' % (
                    _("Layer"), escape(str(vio.get('layer', ''))), _num(vio.get('x'), decimals),
                    _num(vio.get('y'), decimals), _("value"), _num(vio.get('value'), decimals), units)
            else:
                txt += '&nbsp;&nbsp;%s<BR>' % escape(str(vio))
    elif not el.get('checked'):
        txt += '%s: %s.<BR>' % (_("STATUS"), _badge(_("NOTHING TO CHECK"), 'gray'))
    else:
        txt += '%s: %s.<BR>%s<BR>' % (_("STATUS"), _badge(_("PASSED"), 'green'),
                                      _("Violations: There are no violations for the current rule."))
    return txt


def render_report(res, decimals, units=''):
    """HTML report of a list of result dicts (see make_result). Never raises on an odd shape."""
    txt = ''
    for el in (res if isinstance(res, (list, tuple)) else [res]):
        try:
            txt += _render_result(el, decimals, units)
        except Exception as err:
            txt += '<b>%s</b>&nbsp;&nbsp;%s<BR>' % (_("ERROR"), escape(str(err)))
        txt += '<BR><BR>'
    return txt


def results_to_csv(results, path):
    """
    Write the violations as CSV rows (rule, layer, x, y, value, limit, status). A rule that could not run (ERROR) or
    had nothing to check gets one row with the status and no location. Passed rules have no row.

    :raises OSError:    when the file cannot be written
    """
    import csv

    rows = []
    for res in results:
        status = results_status(res)
        if status == 'FAILED':
            for vio in res['violations']:
                rows.append([res['rule'], vio['layer'], vio['x'], vio['y'], vio['value'], res['limit'], status])
        elif status in ('ERROR', 'NOTHING TO CHECK'):
            rows.append([res['rule'], '', '', '', '', res['limit'], status])
    with open(str(path), 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)


# ------------------------------------------------------------------------------------------------------------------
# rule presets, metric (mm). Typical published capabilities, NOT a fab guarantee: always check the current
# specification of the manufacturer. A stem that is not in a preset keeps its value.
# ------------------------------------------------------------------------------------------------------------------
PRESET_CUSTOM = 'Custom'
PRESETS = {
    'Home etching': {
        'trace_size': 0.3, 'c2c': 0.3, 'c2o': 0.5, 'ri': 0.3, 'dh': 0.6, 'h2h': 0.5, 'slot': 1.0, 'h2o': 0.5,
        'h2c': 0.3, 's2s': 0.2, 's2sm': 0.2, 's2o': 0.5, 'sm2sm': 0.2,
    },
    'CNC milling (0.2 mm V-bit)': {
        'trace_size': 0.2, 'c2c': 0.2, 'iso': 0.2, 'c2o': 0.5, 'ri': 0.3, 'dh': 0.6, 'h2h': 0.5, 'slot': 1.0,
        'h2o': 0.5, 'h2c': 0.3,
    },
    'JLCPCB standard': {
        'trace_size': 0.127, 'c2c': 0.127, 'c2o': 0.3, 'ri': 0.13, 'dh': 0.3, 'h2h': 0.5, 'slot': 0.65,
        'h2o': 0.5, 'h2c': 0.254, 's2s': 0.15, 's2sm': 0.15, 's2o': 0.3, 'sm2sm': 0.1,
    },
    'PCBWay standard': {
        'trace_size': 0.127, 'c2c': 0.127, 'c2o': 0.3, 'ri': 0.15, 'dh': 0.3, 'h2h': 0.5, 'slot': 0.8,
        'h2o': 0.3, 'h2c': 0.25, 's2s': 0.15, 's2sm': 0.15, 's2o': 0.3, 'sm2sm': 0.1,
    },
    'OSH Park': {
        'trace_size': 0.152, 'c2c': 0.152, 'c2o': 0.381, 'ri': 0.127, 'dh': 0.254, 'h2h': 0.381, 'slot': 0.5,
        'h2o': 0.381, 'h2c': 0.254,
    },
}


def preset_values(name, units):
    """The values of a preset in the app units ('MM' or 'IN'); {} for Custom or an unknown name."""
    values = PRESETS.get(name, {})
    if str(units).upper() == 'IN':
        return {stem: val / 25.4 for stem, val in values.items()}
    return dict(values)
