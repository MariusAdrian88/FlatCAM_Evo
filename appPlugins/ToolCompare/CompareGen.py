"""
Compare plugin model: geometry difference of two Gerber / Geometry / Excellon objects (Qt free, Shapely + numpy only).

An element of Old is unchanged if its distance to New is <= tol (and the same from New to Old). Polygons and lines
are compared with buffers of radius buffer_radius(tol); Excellon holes are matched one to one.
"""
import math
import re
import threading
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely import LineString, STRtree
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry

QS = 8                  # quad_segs of every buffer
V_TILE = 2000           # polygons with more vertices are tiled before the comparison
V_DRAW = 1000           # max vertices of one fill piece / points of one line path
B_COLL = 200000         # draw budget: vertices of one collection (used by the controller)
B_TICK = 50000          # draw budget: vertices of one scheduler tick (used by the controller)
BIG_COMPONENT = 300     # hole components above this size use Hopcroft-Karp instead of the Hungarian algorithm

_HOLE_QS = 16
_TLS = threading.local()
_COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$')


class Cancelled(Exception):
    pass


@dataclass
class SideResult:
    polygons: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    fill_pieces: list = field(default_factory=list)
    line_paths: list = field(default_factory=list)
    holes: list = field(default_factory=list)


@dataclass
class CompareResult:
    removed: SideResult
    added: SideResult
    failed: list = field(default_factory=list)
    state: str = 'complete'
    stats: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------------------------------------------------
def buffer_radius(tol):
    """Radius that makes the polygonal round buffer cover the full tol neighborhood for any join angle."""
    if tol is None or tol <= 0:
        return 0.0
    return float(tol) / math.cos(0.75 * math.pi / (2 * QS))


def normalize_color(value, default):
    if not isinstance(value, str) or not _COLOR_RE.match(value):
        value = default
    rgb = value[:7]
    return {'outline': rgb + 'FF', 'fill': rgb + '60', 'none': rgb + '00', 'swatch': rgb}


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


def _obj_array(geoms):
    arr = np.empty(len(geoms), dtype=object)
    for i, g in enumerate(geoms):
        arr[i] = g
    return arr


def _checker(cancelled):
    def chk():
        if cancelled is not None and cancelled():
            raise Cancelled()
    return chk


def _split_atoms(geoms):
    """Object array of geoms -> atomic non-empty geometries (one level of get_parts repeated until flat)."""
    arr = _obj_array(geoms) if not isinstance(geoms, np.ndarray) else geoms
    while arr.size:
        ids = shapely.get_type_id(arr)
        multi = ids >= 4
        if not multi.any():
            break
        arr = np.concatenate([arr[~multi], shapely.get_parts(arr[multi])])
    if arr.size:
        arr = arr[~shapely.is_empty(arr)]
    return arr


# ----------------------------------------------------------------------------------------------------------------------
# snapshot
# ----------------------------------------------------------------------------------------------------------------------
def _obj_units(obj):
    u = getattr(obj, 'units', None)
    if u is None:
        u = (getattr(obj, 'obj_options', None) or {}).get('units')
    return _norm_units(u) or 'MM'


def snapshot(obj, kind):
    """Plain copy of the geometry of obj for the worker: new lists, no reference to the live object."""
    units = _obj_units(obj)
    if kind == 'exc':
        tools = {}
        for uid, tool in (getattr(obj, 'tools', None) or {}).items():
            drills = [(float(p.x), float(p.y)) for p in (tool.get('drills') or ())]
            slots = []
            for a, b in (tool.get('slots') or ()):
                pa, pb = (float(a.x), float(a.y)), (float(b.x), float(b.y))
                slots.append((pa, pb) if pa <= pb else (pb, pa))
            tools[uid] = {'dia': float(tool.get('tooldia', 0.0) or 0.0), 'drills': drills, 'slots': slots}
        return {'kind': 'exc', 'units': units, 'tools': tools}

    if kind == 'geo' and getattr(obj, 'multigeo', False):
        flat = []
        for tool in (getattr(obj, 'tools', None) or {}).values():
            flat.extend(_flat(tool.get('solid_geometry')))
    else:
        flat = _flat(getattr(obj, 'solid_geometry', None))
    polys, lines = [], []
    for g in flat:
        t = g.geom_type
        if t == 'Polygon':
            polys.append(g)
        elif t == 'LineString':
            if g.length > 0:
                lines.append(g)
        elif t == 'LinearRing':
            if g.length > 0:
                lines.append(LineString(g.coords))
    return {'kind': kind, 'units': units, 'polys': polys, 'lines': lines}


def _scaled(snap, f):
    if f == 1.0:
        return snap
    out = dict(snap)
    if snap['kind'] == 'exc':
        out['tools'] = {
            uid: {'dia': t['dia'] * f,
                  'drills': [(x * f, y * f) for x, y in t['drills']],
                  'slots': [((a[0] * f, a[1] * f), (b[0] * f, b[1] * f)) for a, b in t['slots']]}
            for uid, t in snap['tools'].items()}
        return out
    for key in ('polys', 'lines'):
        arr = _obj_array(snap[key])
        out[key] = list(shapely.transform(arr, lambda c: c * f)) if arr.size else []
    return out


# ----------------------------------------------------------------------------------------------------------------------
# polygons and lines
# ----------------------------------------------------------------------------------------------------------------------
def _cbuf(c, r):
    cache = getattr(_TLS, 'cache', None)
    if cache is None:
        return c.buffer(r, quad_segs=QS)
    hit = cache.get((id(c), r))
    if hit is None:
        hit = (c, c.buffer(r, quad_segs=QS))
        cache[(id(c), r)] = hit
    return hit[1]


def _diff_one(subject, candidates, r):
    """subject - union(buffer(c, r) for c in candidates); the exact difference when r == 0."""
    if not candidates:
        return subject
    if r > 0:
        bufs = [_cbuf(c, r) for c in candidates]
    elif subject.geom_type == 'Polygon':
        bufs = [c for c in candidates if c.geom_type == 'Polygon']
    else:
        bufs = list(candidates)
    if not bufs:
        return subject
    cover = bufs[0] if len(bufs) == 1 else shapely.union_all(bufs)
    return subject.difference(cover)


def _tile(polys_a, polys_b, chk):
    """Cut the polygons with more than V_TILE vertices on one shared grid; returns the two new polygon lists."""
    if not polys_a or not polys_b:
        return polys_a, polys_b
    allp = _obj_array(polys_a + polys_b)
    counts = shapely.get_num_coordinates(allp)
    big = counts > V_TILE
    if not big.any():
        return polys_a, polys_b
    bb = shapely.bounds(allp[big])
    gx0, gy0 = bb[:, 0].min(), bb[:, 1].min()
    ext = max(bb[:, 2].max() - gx0, bb[:, 3].max() - gy0)
    n_i = np.maximum(2, np.ceil(np.sqrt(counts[big] / float(max(V_TILE, 1)))))
    cell = float((np.maximum(bb[:, 2] - bb[:, 0], bb[:, 3] - bb[:, 1]) / n_i).min())
    cell = max(cell, ext / 32.0)
    if not cell > 0:
        return polys_a, polys_b

    def cut(polys):
        out = []
        cnt = shapely.get_num_coordinates(_obj_array(polys))
        bnds = shapely.bounds(_obj_array(polys))
        for p, c, b in zip(polys, cnt, bnds):
            if c <= V_TILE:
                out.append(p)
                continue
            ix0, ix1 = int(math.floor((b[0] - gx0) / cell)), int(math.floor((b[2] - gx0) / cell))
            iy0, iy1 = int(math.floor((b[1] - gy0) / cell)), int(math.floor((b[3] - gy0) / cell))
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    chk()
                    piece = shapely.clip_by_rect(p, gx0 + ix * cell, gy0 + iy * cell,
                                                 gx0 + (ix + 1) * cell, gy0 + (iy + 1) * cell)
                    for g in _split_atoms([piece]):
                        if g.geom_type == 'Polygon' and g.area > 0:
                            out.append(g)
        return out

    return cut(polys_a), cut(polys_b)


def _keys(geoms, chk):
    out = []
    for s in range(0, len(geoms), 1000):
        chk()
        out.extend(shapely.to_wkb(shapely.normalize(geoms[s:s + 1000])).tolist())
    return out


def _side_fragments(subj, tree_geoms, r, chk, failed):
    """Pieces of the subjects that are farther than r from the tree geometries."""
    if subj.size == 0:
        return []
    if tree_geoms.size == 0:
        return list(subj)
    tree = STRtree(tree_geoms)
    pairs = tree.query(subj, predicate='dwithin', distance=r) if r > 0 else tree.query(subj, predicate='intersects')
    order = np.lexsort((pairs[1], pairs[0]))
    si, ti = pairs[0][order], pairs[1][order]
    uniq, start = np.unique(si, return_index=True)
    ends = np.append(start[1:], len(si))
    has = np.zeros(len(subj), dtype=bool)
    has[uniq] = True
    frags = list(subj[~has])
    isline = shapely.get_dimensions(subj) == 1
    _TLS.cache = {}
    try:
        for k in range(len(uniq)):
            if k % 200 == 0:
                chk()
            i = int(uniq[k])
            subject = subj[i]
            cands = list(tree_geoms[ti[start[k]:ends[k]]])
            try:
                res = _diff_one(subject, cands, r)
            except GEOSException:
                if isline[i]:
                    failed.append(subject)
                    continue
                try:
                    res = _diff_one(shapely.make_valid(subject),
                                    [shapely.make_valid(c) if c.geom_type == 'Polygon' else c for c in cands], r)
                except GEOSException:
                    failed.append(subject)
                    continue
            if not res.is_empty:
                frags.append(res)
    finally:
        _TLS.cache = None
    return frags


def _fill_pieces(polys, chk):
    out = []
    stack = []
    vmax = max(V_DRAW, 5)   # a rectangle already has 5 coordinates; a smaller limit could never be reached
    counts = shapely.get_num_coordinates(_obj_array(polys)) if polys else []
    for p, c in zip(polys, counts):
        if c <= vmax:
            out.append(p)
        else:
            stack.append((p, 0))
    while stack:
        chk()
        p, depth = stack.pop()
        if shapely.get_num_coordinates(p) <= vmax or depth > 40:
            out.append(p)
            continue
        x0, y0, x1, y1 = p.bounds
        if x1 - x0 >= y1 - y0:
            mid = (x0 + x1) / 2.0
            halves = (shapely.clip_by_rect(p, x0, y0, mid, y1), shapely.clip_by_rect(p, mid, y0, x1, y1))
        else:
            mid = (y0 + y1) / 2.0
            halves = (shapely.clip_by_rect(p, x0, y0, x1, mid), shapely.clip_by_rect(p, x0, mid, x1, y1))
        for h in halves:
            for g in _split_atoms([h]):
                if g.geom_type == 'Polygon' and g.area > 0:
                    stack.append((g, depth + 1))
    return out


def _line_paths(linear, chk):
    """Paths of <= V_DRAW points; the neighbors of a cut line share the cut point."""
    out = []
    if len(linear) == 0:
        return out
    arr = _obj_array(linear)
    counts = shapely.get_num_coordinates(arr)
    vmax = max(2, V_DRAW)
    small = counts <= vmax
    out.extend(arr[small])
    step = vmax - 1
    for g in arr[~small]:
        chk()
        coords = shapely.get_coordinates(g)
        for s in range(0, len(coords) - 1, step):
            out.append(LineString(coords[s:s + vmax]))
    return out


def _side_result(frags, chk):
    side = SideResult()
    atoms = _split_atoms(list(frags)) if len(frags) else np.empty(0, dtype=object)
    if atoms.size:
        ids = shapely.get_type_id(atoms)
        atoms = atoms[(ids != 0) & (ids != 4)]
    chk()
    if atoms.size:
        u = shapely.union_all(atoms)
        parts = _split_atoms([u])
    else:
        parts = np.empty(0, dtype=object)
    ids = shapely.get_type_id(parts) if parts.size else np.empty(0, dtype=int)
    polys = parts[ids == 3] if parts.size else parts
    if polys.size:
        polys = polys[shapely.area(polys) > 0]
    lines = parts[(ids == 1) | (ids == 2)] if parts.size else parts
    side.polygons = list(polys)
    side.lines = list(lines)
    side.fill_pieces = _fill_pieces(side.polygons, chk)
    rings = list(shapely.get_rings(polys)) if polys.size else []
    side.line_paths = _line_paths(rings + side.lines, chk)
    return side


def _side_stats(side):
    area = float(shapely.area(_obj_array(side.polygons)).sum()) if side.polygons else 0.0
    length = float(shapely.length(_obj_array(side.lines)).sum()) if side.lines else 0.0
    return {'count': len(side.polygons) + len(side.lines), 'area': area, 'length': length}


def _compare_shapes(old, new, tol, chk):
    r = buffer_radius(tol)
    chk()
    polys_o, polys_n = _tile(list(old['polys']), list(new['polys']), chk)
    items_o = _obj_array(polys_o + list(old['lines']))
    items_n = _obj_array(polys_n + list(new['lines']))
    keys_o, keys_n = _keys(items_o, chk), _keys(items_n, chk)
    set_o, set_n = set(keys_o), set(keys_n)
    subj_o = items_o[np.fromiter((k not in set_n for k in keys_o), dtype=bool, count=len(keys_o))] \
        if len(keys_o) else items_o
    subj_n = items_n[np.fromiter((k not in set_o for k in keys_n), dtype=bool, count=len(keys_n))] \
        if len(keys_n) else items_n
    failed = []
    frag_rem = _side_fragments(subj_o, items_n, r, chk, failed)
    frag_add = _side_fragments(subj_n, items_o, r, chk, failed)
    chk()
    removed = _side_result(frag_rem, chk)
    added = _side_result(frag_add, chk)
    return CompareResult(removed=removed, added=added, failed=failed,
                         state='incomplete' if failed else 'complete',
                         stats={'removed': _side_stats(removed), 'added': _side_stats(added)})


# ----------------------------------------------------------------------------------------------------------------------
# Excellon holes
# ----------------------------------------------------------------------------------------------------------------------
def _holes(snap):
    """Holes of a snapshot in canonical order: arrays kind (0 drill, 1 slot), dia, x1, y1, x2, y2."""
    rows = []
    for tool in snap['tools'].values():
        dia = tool['dia']
        for x, y in tool['drills']:
            rows.append((0, dia, x, y, x, y))
        for a, b in tool['slots']:
            rows.append((1, dia, a[0], a[1], b[0], b[1]))
    arr = np.array(rows, dtype=float).reshape(-1, 6)
    if len(arr):
        arr = arr[np.lexsort((arr[:, 5], arr[:, 4], arr[:, 3], arr[:, 2], arr[:, 1], arr[:, 0]))]
    return arr


def _hole_edges(O, N, tol, chk):
    """Valid (old, new, cost) pairs, sorted by (old, new); built in small chunks (bounded temporary memory)."""
    empty = np.empty(0, dtype=np.int32)
    if not len(O) or not len(N):
        return empty, empty, np.empty(0)
    tree = STRtree(shapely.points((N[:, 2] + N[:, 4]) / 2, (N[:, 3] + N[:, 5]) / 2))
    cx, cy = (O[:, 2] + O[:, 4]) / 2, (O[:, 3] + O[:, 5]) / 2
    rq = tol * (1 + 1e-9) + 1e-12
    ok_k, ok_d = O[:, 0], O[:, 1]
    ox1, oy1, ox2, oy2 = (np.ascontiguousarray(O[:, i]) for i in (2, 3, 4, 5))
    nk, nd = N[:, 0], N[:, 1]
    nx1, ny1, nx2, ny2 = (np.ascontiguousarray(N[:, i]) for i in (2, 3, 4, 5))
    n_n = len(N)
    drills_only = not (ok_k.any() or nk.any())
    step = 256
    eo_l, en_l, d_l = [], [], []
    s = 0
    while s < len(O):
        chk()
        pr = tree.query(shapely.points(cx[s:s + step], cy[s:s + step]), predicate='dwithin', distance=rq)
        i, j = pr[0].astype(np.int64) + s, pr[1].astype(np.int64)
        s += step
        if i.size > 400000 and step > 16:
            step //= 2
        elif i.size < 50000 and step < 8192:
            step *= 2
        if not i.size:
            continue
        if i.size > 1:
            key = (i << 32) | j
            if not (key[1:] > key[:-1]).all():
                key.sort()
                i, j = key >> 32, key & 0xFFFFFFFF
        d = np.hypot(ox1[i] - nx1[j], oy1[i] - ny1[j])
        if not drills_only:
            d = np.maximum(d, np.hypot(ox2[i] - nx2[j], oy2[i] - ny2[j]))
            d_swap = np.maximum(np.hypot(ox1[i] - nx2[j], oy1[i] - ny2[j]), np.hypot(ox2[i] - nx1[j], oy2[i] - ny1[j]))
            d = np.minimum(d, d_swap)
        ok = (ok_k[i] == nk[j]) & (np.abs(ok_d[i] - nd[j]) <= tol) & (d <= tol)
        i, j, d = i[ok], j[ok], d[ok]
        eo_l.append(i.astype(np.int32))
        en_l.append(j.astype(np.int32))
        d_l.append(d)
    if not eo_l:
        return empty, empty, np.empty(0)
    return np.concatenate(eo_l), np.concatenate(en_l), np.concatenate(d_l)


def _components(n_o, n_n, eo, en):
    """Component label (smallest node id) of every node; old nodes are 0..n_o-1, new nodes follow."""
    lab = np.arange(n_o + n_n, dtype=np.int32)
    a, b = eo.astype(np.int32, copy=False), en + np.int32(n_o)
    if a.size:
        np.minimum.at(lab, b, a)   # first pass: an old node keeps its own id (smaller than any new id)
    while a.size:
        while True:
            nl = lab[lab]
            if np.array_equal(nl, lab):
                break
            lab = nl
        la, lb = lab[a], lab[b]
        keep = la != lb
        a, b, la, lb = a[keep], b[keep], la[keep], lb[keep]
        if not a.size:
            break
        m = np.minimum(la, lb)
        np.minimum.at(lab, la, m)
        np.minimum.at(lab, lb, m)
    return lab


def _hungarian(cost, chk):
    """Min cost full assignment of the rows of cost (rows <= cols); returns the column of every row."""
    n, m = cost.shape
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=np.int64)
    way = np.zeros(m + 1, dtype=np.int64)
    inf = float('inf')
    for i in range(1, n + 1):
        chk()
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, inf)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            cur = cost[i0 - 1] - u[i0] - v[1:]
            free = ~used[1:]
            upd = free & (cur < minv[1:])
            minv[1:][upd] = cur[upd]
            way[1:][upd] = j0
            tmp = np.where(free, minv[1:], inf)
            j1 = int(np.argmin(tmp)) + 1
            delta = tmp[j1 - 1]
            u[p[used]] += delta
            v[used] -= delta
            minv[1:][free] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    col = np.full(n, -1, dtype=np.int64)
    for j in range(1, m + 1):
        if p[j]:
            col[p[j] - 1] = j - 1
    return col


def _hopcroft_karp(lefts, st, en, nb, n_n, match_l, match_r, chk):
    """Maximum cardinality matching of the old nodes in lefts, greedy start in canonical order, then augmenting
    paths found by vectorized BFS (edge order = canonical order). The neighbors of old node u are nb[st[u]:en[u]].
    Right nodes visited by a failed search stay dead: no later augmenting path can pass them."""
    for u in lefts:
        s = nb[st[u]:en[u]]
        free = match_r[s] < 0
        if free.any():
            v = int(s[int(np.argmax(free))])
            match_l[u], match_r[v] = v, u
    dead = np.zeros(n_n, dtype=bool)
    parent = np.zeros(n_n, dtype=np.int64)
    lim = 1 << 19
    for u0 in lefts[match_l[lefts] < 0]:
        chk()
        frontier = np.array([u0], dtype=np.int64)
        touched = []
        end_v = -1
        while frontier.size and end_v < 0:
            nxt = []
            cnt = en[frontier] - st[frontier]
            cum = np.cumsum(cnt)
            a = 0
            while a < frontier.size and end_v < 0:
                b = int(np.searchsorted(cum, (cum[a - 1] if a else 0) + lim, side='right'))
                b = max(b, a + 1)
                f, c = frontier[a:b], cnt[a:b]
                a = b
                tot = int(c.sum())
                if not tot:
                    continue
                off = np.cumsum(c) - c
                pos = np.arange(tot) + np.repeat(st[f] - off, c)
                vs = nb[pos]
                us = np.repeat(f, c)
                m = ~dead[vs]
                vs, us = vs[m], us[m]
                if not vs.size:
                    continue
                parent[vs[::-1]] = us[::-1]
                vs = vs[parent[vs] == us]
                dead[vs] = True
                touched.append(vs)
                mr = match_r[vs]
                fr = mr < 0
                if fr.any():
                    end_v = int(vs[int(np.argmax(fr))])
                else:
                    nxt.append(mr)
            frontier = np.concatenate(nxt) if nxt else np.empty(0, dtype=np.int64)
        if end_v >= 0:
            v = end_v
            while True:
                u = int(parent[v])
                prev = int(match_l[u])
                match_l[u], match_r[v] = v, u
                if prev < 0:
                    break
                v = prev
            for t in touched:
                dead[t] = False


def _match_holes(O, N, tol, chk):
    """(matched old mask, matched new mask)."""
    n_o, n_n = len(O), len(N)
    mo, mn = np.zeros(n_o, dtype=bool), np.zeros(n_n, dtype=bool)
    eo, en, cost = _hole_edges(O, N, tol, chk)
    if not eo.size:
        return mo, mn
    deg_o, deg_n = np.bincount(eo, minlength=n_o), np.bincount(en, minlength=n_n)
    single = (deg_o[eo] == 1) & (deg_n[en] == 1)
    if single.any():
        mo[eo[single]] = True
        mn[en[single]] = True
        keep = ~single
        eo, en, cost = eo[keep], en[keep], cost[keep]
        if not eo.size:
            return mo, mn
    del single
    lab = _components(n_o, n_n, eo, en)
    comp = lab[eo]
    if comp[0] != comp[-1] or (comp != comp[0]).any():
        order = np.argsort(comp, kind='stable')
        eo, en, cost, comp = eo[order], en[order], cost[order], comp[order]
    uc, cs = np.unique(comp, return_index=True)
    ce = np.append(cs[1:], len(comp))
    match_l = np.full(n_o, -1, dtype=np.int64)
    match_r = np.full(n_n, -1, dtype=np.int64)
    st = ed = None
    for k in range(len(uc)):
        s, e = cs[k], ce[k]
        ceo, cen, cc = eo[s:e], en[s:e], cost[s:e]
        ol, nl = np.unique(ceo), np.unique(cen)
        if len(ol) + len(nl) > BIG_COMPONENT:
            if st is None:
                st, ed = np.zeros(n_o, dtype=np.int64), np.zeros(n_o, dtype=np.int64)
            st[ol] = np.searchsorted(ceo, ol, side='left')
            ed[ol] = np.searchsorted(ceo, ol, side='right')
            _hopcroft_karp(ol.astype(np.int64), st, ed, cen, n_n, match_l, match_r, chk)
            continue
        big = float(cc.sum()) + 1.0
        mat = np.full((len(ol), len(nl)), big)
        mat[np.searchsorted(ol, ceo), np.searchsorted(nl, cen)] = cc
        if len(ol) <= len(nl):
            col = _hungarian(mat, chk)
            for i, j in enumerate(col):
                if j >= 0 and mat[i, j] < big:
                    match_l[ol[i]] = nl[j]
                    match_r[nl[j]] = ol[i]
        else:
            col = _hungarian(mat.T.copy(), chk)
            for j, i in enumerate(col):
                if i >= 0 and mat[i, j] < big:
                    match_l[ol[i]] = nl[j]
                    match_r[nl[j]] = ol[i]
    mo |= match_l >= 0
    mn |= match_r >= 0
    return mo, mn


def _hole_side(H):
    side = SideResult()
    if not len(H):
        return side, {'count': 0, 'area': 0.0, 'length': 0.0}
    shapes = np.empty(len(H), dtype=object)
    drill = H[:, 0] == 0
    if drill.any():
        d = H[drill]
        shapes[drill] = shapely.buffer(shapely.points(d[:, 2], d[:, 3]), d[:, 1] / 2.0, quad_segs=_HOLE_QS)
    if (~drill).any():
        s = H[~drill]
        coords = np.stack([s[:, [2, 3]], s[:, [4, 5]]], axis=1)
        shapes[~drill] = shapely.buffer(shapely.linestrings(coords), s[:, 1] / 2.0, quad_segs=_HOLE_QS)
    for row in H:
        side.holes.append({'kind': 'drill' if row[0] == 0 else 'slot', 'dia': float(row[1]),
                           'p1': (float(row[2]), float(row[3])),
                           'p2': None if row[0] == 0 else (float(row[4]), float(row[5]))})
    shapes = shapes[~shapely.is_empty(shapes)]
    side.polygons = list(shapes)
    side.fill_pieces = list(shapes)
    side.line_paths = list(shapely.get_exterior_ring(shapes)) if shapes.size else []
    area = float(shapely.area(shapes).sum()) if shapes.size else 0.0
    length = float(shapely.length(_obj_array(side.line_paths)).sum()) if side.line_paths else 0.0
    return side, {'count': len(H), 'area': area, 'length': length}


def _compare_holes(old, new, tol, chk):
    chk()
    O, N = _holes(old), _holes(new)
    mo, mn = _match_holes(O, N, tol, chk)
    removed, st_r = _hole_side(O[~mo])
    added, st_a = _hole_side(N[~mn])
    return CompareResult(removed=removed, added=added, failed=[], state='complete',
                         stats={'removed': st_r, 'added': st_a})


# ----------------------------------------------------------------------------------------------------------------------
# entry
# ----------------------------------------------------------------------------------------------------------------------
def compare(old_snap, new_snap, kind, tol, cancelled=None):
    """Geometry of Old not in New (removed) and of New not in Old (added). tol is in the units of Old."""
    chk = _checker(cancelled)
    tol = max(0.0, float(tol or 0.0))
    new_snap = _scaled(new_snap, units_factor(old_snap['units'], new_snap['units']))
    if kind == 'exc':
        return _compare_holes(old_snap, new_snap, tol, chk)
    return _compare_shapes(old_snap, new_snap, tol, chk)
