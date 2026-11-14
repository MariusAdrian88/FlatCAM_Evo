"""Geometry core of the Subtract plugin: subtract / intersect / xor of an area from Gerber, Geometry and Excellon.

Qt free and application free. The objects are duck-typed: only ``kind`` ('gerber' | 'geometry' | 'excellon'),
``tools``, ``solid_geometry`` and ``obj_options`` (a dict that may hold 'units' and 'name') are read.

Data facts this module relies on (checked in the parsers):
 - Gerber: tools = {apid: {'type', 'size', 'geometry': [{'solid'|'clear', 'follow'}...]}}. A region lives in aperture
   0 ('REG', size 0.0) and its 'follow' is the exterior ring of the polygon.
 - Excellon: tools[tid] = {'tooldia', 'drills': [Point], 'slots': [(start, stop)], 'solid_geometry': [polys]} where
   the solid_geometry is the drill circles followed by the slot capsules.
 - Geometry: tools[uid]['solid_geometry'] holds the shapes of each tool.
"""

from dataclasses import dataclass, field

import numpy as np
from shapely import LineString, Point, Polygon, STRtree
from shapely.affinity import scale as _scale
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

OPERATIONS = ('subtract', 'intersect', 'xor')

_REL_TOL = 1e-9


@dataclass
class SubResult:
    """Result of :func:`compute`."""
    kind: str
    tools: dict = field(default_factory=dict)
    solid_geometry: object = None
    follow_geometry: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    empty_area: bool = False


# ----------------------------------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------------------------------
def poly2rings(poly):
    return [poly.exterior] + [interior for interior in poly.interiors]


def _flat(geo):
    """All the non-empty atomic geometries of geo: a geometry, a collection or any nested list of them."""
    out = []
    stack = [geo]
    while stack:
        g = stack.pop()
        if g is None:
            continue
        if isinstance(g, (list, tuple, set)):
            stack.extend(g)
        elif isinstance(g, BaseGeometry):
            if g.is_empty:
                continue
            if hasattr(g, 'geoms'):
                stack.extend(g.geoms)
            else:
                out.append(g)
    return out


def _polys(geo):
    return [g for g in _flat(geo) if g.geom_type == 'Polygon' and g.area > 0]


def _lines(geo):
    return [g for g in _flat(geo) if g.geom_type in ('LineString', 'LinearRing') and g.length > 0]


def _union(parts):
    parts = _flat(parts)
    return unary_union(parts) if parts else Polygon()


def _op(a, b, op):
    try:
        return getattr(a, op)(b)
    except GEOSException:
        return getattr(a.buffer(0), op)(b.buffer(0))


def _obj_array(geoms):
    arr = np.empty(len(geoms), dtype=object)
    for i, g in enumerate(geoms):
        arr[i] = g
    return arr


class _AreaIndex:
    """The polygons of an area with a spatial index; the elements only meet the parts they touch."""

    def __init__(self, area):
        parts = []
        for p in _polys(area):
            parts.append(p if p.is_valid else p.buffer(0))
        self.parts = _obj_array(parts)
        self.tree = STRtree(self.parts) if parts else None
        self.area = _union(parts)

    def hits(self, geoms):
        """{index of geom: sorted part indices that intersect it}."""
        out = {}
        if self.tree is None or not geoms:
            return out
        res = self.tree.query(_obj_array(geoms), predicate='intersects')
        for i, j in zip(res[0].tolist(), res[1].tolist()):
            out.setdefault(i, []).append(j)
        return out

    def local(self, idx):
        if len(idx) == 1:
            return self.parts[idx[0]]
        return unary_union([self.parts[k] for k in idx])


def _same_area(a, b):
    return abs(a - b) <= _REL_TOL * max(1.0, abs(b))


def _check_abort(abort):
    if abort is not None:
        abort()


def _norm_units(u):
    if u is None:
        return None
    u = str(u).strip().upper()
    if u in ('MM', 'METRIC'):
        return 'MM'
    if u in ('IN', 'INCH', 'INCHES'):
        return 'IN'
    return None


def units_factor(target_units, sub_units):
    """Factor that converts sub_units lengths into target_units; unknown units give 1.0."""
    t, s = _norm_units(target_units), _norm_units(sub_units)
    if t is None or s is None or t == s:
        return 1.0
    return 25.4 if (t == 'MM' and s == 'IN') else 1.0 / 25.4


def _obj_units(obj):
    opts = getattr(obj, 'obj_options', None) or {}
    return opts.get('units')


def _obj_name(obj):
    opts = getattr(obj, 'obj_options', None) or {}
    return opts.get('name', getattr(obj, 'name', None))


# ----------------------------------------------------------------------------------------------------------------------
# the area
# ----------------------------------------------------------------------------------------------------------------------
def _gerber_area(obj, tools):
    if tools is None:
        solid = getattr(obj, 'solid_geometry', None)
        if solid is not None and _polys(solid):
            return _union(_polys(solid))
        sel = (obj.tools or {}).values()
    else:
        wanted = set(tools)
        sel = [t for k, t in (obj.tools or {}).items() if k in wanted]
    solids, clears = [], []
    for tool in sel:
        for el in tool.get('geometry', ()):
            if 'solid' in el:
                solids.append(el['solid'])
            if 'clear' in el:
                clears.append(el['clear'])
    area = _union(solids)
    if clears and not area.is_empty:
        area = _op(area, _union(clears), 'difference')
    return area


def _geometry_area(obj, margin, tools):
    wanted = None if tools is None else set(tools)
    parts = []
    for uid, tool in (obj.tools or {}).items():
        if wanted is None or uid in wanted:
            parts.extend(_flat(tool.get('solid_geometry')))
    shapes = [g for g in parts if g.geom_type == 'Polygon']
    if margin > 0:
        shapes += [g for g in parts if g.geom_type in ('LineString', 'LinearRing', 'Point')]
    return _union(shapes)


def _hole_shapes(obj, tools, steps):
    """The drill circles and the slot capsules of the (selected) tools of an Excellon."""
    quad = max(1, steps // 4)
    wanted = None if tools is None else set(tools)
    shapes = []
    for tid, tool in (obj.tools or {}).items():
        if wanted is not None and tid not in wanted:
            continue
        radius = float(tool.get('tooldia', 0.0)) / 2.0
        if radius <= 0:
            continue
        for drill in tool.get('drills', ()):
            shapes.append(Point(drill.x, drill.y).buffer(radius, quad_segs=quad))
        for start, stop in tool.get('slots', ()):
            shapes.append(LineString([(start.x, start.y), (stop.x, stop.y)]).buffer(radius, quad_segs=quad))
    return shapes


def object_area(obj, margin=0.0, steps=64, tools=None, factor=1.0):
    """
    The area an object covers, as a Polygon / MultiPolygon (possibly empty).

    :param tools:   aperture ids (Gerber) / tool ids (Excellon, Geometry) to use; None = all
    :param factor:  scales the area about (0, 0) before the margin is applied
    :param margin:  > 0 grows the area (lines of a Geometry only give an area with a margin)
    :param steps:   circle steps of the whole circle; the buffer uses quad_segs = steps // 4
    """
    kind = getattr(obj, 'kind', None)
    if kind == 'gerber':
        area = _gerber_area(obj, tools)
    elif kind == 'excellon':
        area = _union(_hole_shapes(obj, tools, steps))
    else:
        area = _geometry_area(obj, margin, tools)
    if area.is_empty:
        return Polygon()
    if factor != 1.0:
        area = _scale(area, factor, factor, origin=(0, 0))
    if margin > 0:
        area = area.buffer(margin, quad_segs=max(1, steps // 4))
    return area


def subtractor_area(objects, margin=0.0, steps=64, tools_by_name=None, target_units=None):
    """Union of the areas of the objects, each converted from its own units to target_units."""
    tools_by_name = tools_by_name or {}
    areas = []
    for obj in objects:
        factor = units_factor(target_units, _obj_units(obj))
        area = object_area(obj, margin=margin, steps=steps, tools=tools_by_name.get(_obj_name(obj)), factor=factor)
        if not area.is_empty:
            areas.append(area)
    return unary_union(areas) if areas else Polygon()


# ----------------------------------------------------------------------------------------------------------------------
# Gerber
# ----------------------------------------------------------------------------------------------------------------------
def _compose(tools):
    solids, clears = [], []
    for tool in tools.values():
        for el in tool.get('geometry', ()):
            if 'solid' in el:
                solids.append(el['solid'])
            if 'clear' in el:
                clears.append(el['clear'])
    solid = _union(solids)
    if clears and not solid.is_empty:
        solid = _op(solid, _union(clears), 'difference')
    return solid


def _region(key, poly):
    return {key: poly, 'follow': poly.exterior}


def gerber_operation(tools, target_solid, area, op='subtract', apertures=None, abort=None):
    """
    Apply op between the area and the Gerber apertures. The input is not changed.

    Untouched elements keep their aperture and follow. Changed elements become aperture 0 ('REG') regions whose
    follow is the exterior ring. Elements that vanish and apertures left empty are dropped.
    The caller still has to call regenerate_source on the object built from the result.

    :return: (new_tools, solid_geometry, follow_geometry, stats) with stats = {'kept', 'changed', 'removed',
             'added'}; changed / removed count source elements, added the new xor regions.
    """
    if op not in OPERATIONS:
        raise ValueError("unknown operation: %s" % op)
    index = _AreaIndex(area)
    wanted = None if apertures is None else set(apertures)
    stats = {'kept': 0, 'changed': 0, 'removed': 0, 'added': 0}
    new_tools = {}
    to_zero = []
    processed = set()

    for apid, tool in tools.items():
        _check_abort(abort)
        elements = tool.get('geometry', [])
        if wanted is not None and apid not in wanted:
            new_tools[apid] = dict(tool, geometry=list(elements))
            stats['kept'] += len(elements)
            continue
        processed.add(apid)
        keys = []
        mains = []
        for el in elements:
            key = 'solid' if 'solid' in el else ('clear' if 'clear' in el else None)
            keys.append(key)
            mains.append(el[key] if key else Polygon())
        hits = index.hits(mains)
        kept = []
        for i, el in enumerate(elements):
            key, geom = keys[i], mains[i]
            if key is None or geom.is_empty:
                kept.append(el)
                stats['kept'] += 1
                continue
            idx = hits.get(i)
            if op == 'intersect':
                if not idx:
                    stats['removed'] += 1
                    continue
                res = _op(geom, index.local(idx), 'intersection')
            else:
                if not idx:
                    kept.append(el)
                    stats['kept'] += 1
                    continue
                res = _op(geom, index.local(idx), 'difference')
            pieces = _polys(res)
            res_area = sum(p.area for p in pieces)
            if not pieces:
                stats['removed'] += 1
            elif _same_area(res_area, geom.area):
                kept.append(el)
                stats['kept'] += 1
            else:
                stats['changed'] += 1
                to_zero.extend(_region(key, p) for p in pieces)
        new_tools[apid] = dict(tool, geometry=kept)

    if op == 'xor':
        if apertures is None and target_solid is not None and _polys(target_solid):
            base = _union(_polys(target_solid))
        else:
            base = _compose({k: t for k, t in tools.items() if wanted is None or k in wanted})
        added = _polys(_op(index.area, base, 'difference')) if not base.is_empty else _polys(index.area)
        stats['added'] += len(added)
        to_zero.extend(_region('solid', p) for p in added)

    if to_zero:
        zero = new_tools.get(0)
        if zero is None:
            zero = {'type': 'REG', 'size': 0.0, 'geometry': []}
        zero = dict(zero, geometry=list(zero['geometry']) + to_zero)
        new_tools[0] = zero
        processed.add(0)
    for apid in processed:
        if apid in new_tools and not new_tools[apid]['geometry']:
            del new_tools[apid]

    base_solid = _union(_polys(target_solid)) if target_solid is not None else Polygon()
    if apertures is None and not base_solid.is_empty:
        if op == 'subtract':
            solid = _op(base_solid, index.area, 'difference')
        elif op == 'intersect':
            solid = _op(base_solid, index.area, 'intersection')
        else:
            solid = _op(base_solid, index.area, 'symmetric_difference')
    else:
        solid = _compose(new_tools)
    follow = [el['follow'] for t in new_tools.values() for el in t['geometry'] if 'follow' in el]
    return new_tools, solid, follow, stats


# ----------------------------------------------------------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------------------------------------------------------
def _clip(elements, index, op, stats):
    """Subtract / intersect every element with the area; returns the non-empty atomic results."""
    out = []
    hits = index.hits(elements)
    for i, el in enumerate(elements):
        idx = hits.get(i)
        if not idx:
            if op == 'subtract':
                out.append(el)
                stats['kept'] += 1
            else:
                stats['removed'] += 1
            continue
        res = _op(el, index.local(idx), 'difference' if op == 'subtract' else 'intersection')
        pieces = _polys(res) if el.geom_type == 'Polygon' else _lines(res)
        if not pieces:
            stats['removed'] += 1
        elif _same_measure(el, pieces):
            out.append(el)
            stats['kept'] += 1
        else:
            out.extend(pieces)
            stats['changed'] += 1
    return out


def _same_measure(el, pieces):
    if el.geom_type == 'Polygon':
        return _same_area(sum(p.area for p in pieces), el.area)
    return _same_area(sum(p.length for p in pieces), el.length)


def geometry_operation(geo, area, op='subtract', close_paths=False):
    """
    Apply op between the area and a Geometry's shapes.

    close_paths True:  the polygons are united and the op gives polygons; lines stay lines.
    close_paths False: polygons are split into their rings, rings and lines get the op.
    xor adds area minus the polygonal part of geo (as polygons, or as rings when close_paths is False).

    :return: (list of geometries, stats) with stats = {'kept', 'changed', 'removed', 'added'}
    """
    if op not in OPERATIONS:
        raise ValueError("unknown operation: %s" % op)
    index = _AreaIndex(area)
    flat = _flat(geo)
    polys = [g for g in flat if g.geom_type == 'Polygon']
    lines = [g for g in flat if g.geom_type in ('LineString', 'LinearRing')]
    clip_op = 'intersect' if op == 'intersect' else 'subtract'

    if close_paths:
        united = _polys(unary_union(polys)) if polys else []
        elements = united + lines
    else:
        rings = [LineString(r.coords) for p in polys for r in poly2rings(p)]
        elements = rings + lines
    stats = {'kept': 0, 'changed': 0, 'removed': 0, 'added': 0}
    result = _clip(elements, index, clip_op, stats)

    if op == 'xor':
        rest = _op(index.area, unary_union(polys), 'difference') if polys else index.area
        added = _polys(rest)
        if not close_paths:
            added = [LineString(r.coords) for p in added for r in poly2rings(p)]
        result.extend(added)
        stats['added'] = len(added)
    return result, stats


# ----------------------------------------------------------------------------------------------------------------------
# Excellon
# ----------------------------------------------------------------------------------------------------------------------
def excellon_operation(tools, area, op='subtract', steps=64, abort=None):
    """
    Remove (subtract) or keep only (intersect) the drills and slots whose hole touches the area. xor is invalid.

    The tool 'solid_geometry' is rebuilt as the parser does (drill circles, then slot capsules); the shapes already
    stored are reused when they match the holes. Tools left without drills and slots are dropped.

    :return: (new_tools, stats) with stats counting holes
    """
    if op == 'xor' or op not in OPERATIONS:
        raise ValueError("operation not valid for Excellon: %s" % op)
    index = _AreaIndex(area)
    quad = max(1, steps // 4)
    new_tools = {}
    stats = {'kept': 0, 'changed': 0, 'removed': 0, 'added': 0}
    for tid, tool in tools.items():
        _check_abort(abort)
        drills = list(tool.get('drills', ()))
        slots = list(tool.get('slots', ()))
        radius = float(tool.get('tooldia', 0.0)) / 2.0
        old = tool.get('solid_geometry') or []
        if len(old) == len(drills) + len(slots) and all(isinstance(g, BaseGeometry) for g in old):
            shapes = list(old)
        else:
            shapes = [Point(d.x, d.y).buffer(radius, quad_segs=quad) for d in drills]
            shapes += [LineString([(s.x, s.y), (e.x, e.y)]).buffer(radius, quad_segs=quad) for s, e in slots]
        hit = set(index.hits(shapes))
        keep_hit = (op == 'intersect')
        keep = [(i in hit) == keep_hit for i in range(len(shapes))]
        nd = len(drills)
        new_drills = [d for i, d in enumerate(drills) if keep[i]]
        new_slots = [s for i, s in enumerate(slots) if keep[nd + i]]
        new_shapes = [g for i, g in enumerate(shapes) if keep[i]]
        stats['kept'] += sum(keep)
        stats['removed'] += len(keep) - sum(keep)
        if new_drills or new_slots:
            new_tools[tid] = dict(tool, drills=new_drills, slots=new_slots, solid_geometry=new_shapes)
    return new_tools, stats


# ----------------------------------------------------------------------------------------------------------------------
# entry points
# ----------------------------------------------------------------------------------------------------------------------
def compute(target, subtractors, op='subtract', margin=0.0, steps=64, close_paths=False, target_tools=None,
            sub_tools=None, abort=None):
    """
    Run op on target with the area of the subtractors.

    :param target_tools:    Gerber apertures / Excellon tools / Geometry tools to affect (None = all)
    :param sub_tools:       {subtractor name: tool ids used for its area}
    :param abort:           callable that raises to cancel; called between tools
    :raises ValueError:     target among the subtractors, no subtractor, bad op or op not valid for the kind
    """
    subtractors = list(subtractors or [])
    if op not in OPERATIONS:
        raise ValueError("unknown operation: %s" % op)
    if not subtractors:
        raise ValueError("no subtractor object")
    if any(s is target for s in subtractors):
        raise ValueError("the target object can not be a subtractor")
    kind = target.kind
    if kind == 'excellon' and op == 'xor':
        raise ValueError("xor is not valid for Excellon")
    if kind not in ('gerber', 'geometry', 'excellon'):
        raise ValueError("unsupported target kind: %s" % kind)

    area = subtractor_area(subtractors, margin=margin, steps=steps, tools_by_name=sub_tools,
                           target_units=_obj_units(target))
    empty = area.is_empty
    if empty:
        # nothing to cut: the target is returned unchanged
        op_area, op_used = Polygon(), 'subtract'
    else:
        op_area, op_used = area, op
    tools_in = target.tools or {}

    if kind == 'gerber':
        tools, solid, follow, stats = gerber_operation(tools_in, getattr(target, 'solid_geometry', None), op_area,
                                                       op_used, target_tools, abort)
        return SubResult('gerber', tools, solid, follow, stats, empty)

    if kind == 'excellon':
        wanted = None if target_tools is None else set(target_tools)
        part = {k: v for k, v in tools_in.items() if wanted is None or k in wanted}
        new_part, stats = excellon_operation(part, op_area, op_used, steps, abort)
        tools = {k: v for k, v in tools_in.items() if wanted is not None and k not in wanted}
        tools.update(new_part)
        tools = {k: tools[k] for k in tools_in if k in tools}
        solid = [g for t in tools.values() for g in t.get('solid_geometry', ())]
        return SubResult('excellon', tools, solid, [], stats, empty)

    stats = {'kept': 0, 'changed': 0, 'removed': 0, 'added': 0}

    def add(st):
        for k in stats:
            stats[k] += st.get(k, 0)

    wanted = None if target_tools is None else set(target_tools)
    tools, solid = {}, []
    for uid, tool in tools_in.items():
        _check_abort(abort)
        if wanted is not None and uid not in wanted:
            tools[uid] = dict(tool)
        else:
            geos, st = geometry_operation(tool.get('solid_geometry'), op_area, op_used, close_paths)
            add(st)
            tools[uid] = dict(tool, solid_geometry=geos)
        solid.extend(_flat(tools[uid].get('solid_geometry')))
    return SubResult('geometry', tools, solid, [], stats, empty)


def apply_result(new_obj, result, target):
    """
    obj_init helper: put the result on a new object and copy the options of the target except 'name' (the caller
    sets the name). For a Gerber the caller still has to call regenerate_source afterwards.
    """
    from copy import deepcopy
    opts = getattr(new_obj, 'obj_options', None)
    if opts is None:
        new_obj.obj_options = opts = {}
    for key, value in (getattr(target, 'obj_options', None) or {}).items():
        if key != 'name':
            opts[key] = deepcopy(value)
    new_obj.tools = result.tools
    if result.kind != 'geometry':
        new_obj.solid_geometry = result.solid_geometry
    if result.kind == 'gerber':
        new_obj.follow_geometry = result.follow_geometry
