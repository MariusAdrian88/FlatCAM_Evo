# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 10/21/2026                                         #
# MIT Licence                                              #
# ##########################################################

"""
Model of the Align Objects plugin: the snap index and the solvers. No Qt here.

A matrix is the shapely 6-tuple (a, b, d, e, xoff, yoff):
    x' = a * x + b * y + xoff
    y' = d * x + e * y + yoff
"""

import math
import logging

import numpy as np
import shapely
from shapely import Point, STRtree
from shapely.geometry.base import BaseGeometry

from appParsers.feature_access import iter_features


class AlignError(Exception):
    """A user message: the points can not give a valid alignment."""


class SnapIndex:
    """
    Snap targets of one object.

    tree:       STRtree of the feature solids (None if there are none)
    centers:    (N, 2) array, the snap point of each solid, in the order of the tree
    areas:      (N,) array, the area of each solid
    vertices:   (M, 2) array, the fallback snap points
    kind:       object type; mixed Gerber/Excellon alignment always scores drill coverage
    """

    def __init__(self, solids, centers, vertices, kind=None):
        self.kind = kind
        self.tree = STRtree(solids) if solids else None
        self.centers = np.asarray(centers, dtype=float).reshape(-1, 2)
        self.areas = shapely.area(np.asarray(solids, dtype=object)) if solids else np.empty(0)
        self.vertices = np.asarray(vertices, dtype=float).reshape(-1, 2)


def _flatten(geo, out):
    """Collect every shapely geometry of a nested list / dict-free structure into out."""
    if geo is None:
        return out
    if isinstance(geo, BaseGeometry):
        if not geo.is_empty:
            out.append(geo)
        return out
    try:
        for g in geo:
            _flatten(g, out)
    except TypeError:
        pass
    return out


def _coordinates(geoms):
    if not geoms:
        return np.empty((0, 2))
    return shapely.get_coordinates(np.asarray(geoms, dtype=object))


def build_snap_index(obj):
    """
    Build the snap index of a Gerber, Excellon or Geometry object.

    Gerber:     the dark flashes snap to the flash point; the vertices of all dark solids are the fallback
    Excellon:   a drill snaps to the drill point, a slot to the middle of the slot; vertices are the fallback
    Geometry:   only vertices
    """
    kind = str(getattr(obj, 'kind', '')).lower()
    solids, centers, outlines = [], [], []

    if kind in ('gerber', 'excellon'):
        for rec in iter_features(obj):
            if rec.polarity != 'dark' or rec.solid is None or rec.solid.is_empty:
                continue
            outlines.append(rec.solid)
            if kind == 'gerber':
                if not isinstance(rec.follow, Point):
                    continue
                center = rec.follow
            else:
                center = rec.follow if isinstance(rec.follow, Point) else rec.follow.centroid
            solids.append(rec.solid)
            centers.append((center.x, center.y))
    else:
        tools = getattr(obj, 'tools', None)
        if getattr(obj, 'multigeo', False) and isinstance(tools, dict):
            for tool in tools.values():
                _flatten(tool.get('solid_geometry'), outlines)
        else:
            _flatten(getattr(obj, 'solid_geometry', None), outlines)

    return SnapIndex(solids, centers, _coordinates(outlines), kind=kind)


def snap(index, x, y, tol):
    """
    The snap point for a click at (x, y), or None.

    1. the click is in one or more solids: the center of the smallest one
    2. else the nearest vertex, if it is not farther than tol
    """
    if index.tree is not None:
        hits = index.tree.query(Point(x, y), predicate='intersects')
        if len(hits):
            best = hits[int(np.argmin(index.areas[hits]))]
            cx, cy = index.centers[best]
            return float(cx), float(cy)

    if len(index.vertices):
        d2 = (index.vertices[:, 0] - x) ** 2 + (index.vertices[:, 1] - y) ** 2
        i = int(np.argmin(d2))
        if d2[i] <= tol * tol:
            vx, vy = index.vertices[i]
            return float(vx), float(vy)
    return None


def _points(P, Q, min_pairs):
    P = np.asarray(P, dtype=float).reshape(-1, 2)
    Q = np.asarray(Q, dtype=float).reshape(-1, 2)
    if len(P) != len(Q):
        raise AlignError("The number of START points and DESTINATION points is not the same.")
    if len(P) < min_pairs:
        raise AlignError("At least %d point pairs are needed." % min_pairs)
    if not (np.isfinite(P).all() and np.isfinite(Q).all()):
        raise AlignError("A point is not a finite number.")
    return P, Q


def _eps(P, Q):
    return 1e-9 * max(1.0, float(np.abs(P).max()), float(np.abs(Q).max()))


def solve_translate(P, Q):
    """Move the first START point to the first DESTINATION point."""
    P, Q = _points(P, Q, 1)
    dx, dy = Q[0] - P[0]
    return 1.0, 0.0, 0.0, 1.0, float(dx), float(dy)


def _rotation_fit(P, Q, with_scale):
    """Least-squares rotation (+ uniform scale) and translation, by Umeyama with atan2. No reflection."""
    P, Q = _points(P, Q, 2)
    mp, mq = P.mean(axis=0), Q.mean(axis=0)
    pc, qc = P - mp, Q - mq
    var_p = float((pc ** 2).sum())
    if math.sqrt(var_p) <= _eps(P, Q):
        raise AlignError("The START points are all at the same position.")

    num = float((pc[:, 0] * qc[:, 1] - pc[:, 1] * qc[:, 0]).sum())
    den = float((pc[:, 0] * qc[:, 0] + pc[:, 1] * qc[:, 1]).sum())
    theta = math.atan2(num, den)

    if with_scale:
        s = math.hypot(num, den) / var_p
        if not math.isfinite(s) or s <= _eps(P, Q):
            raise AlignError("The scale factor is not valid.")
    else:
        s = 1.0

    c, sn = s * math.cos(theta), s * math.sin(theta)
    a, b, d, e = c, -sn, sn, c
    xoff = mq[0] - (a * mp[0] + b * mp[1])
    yoff = mq[1] - (d * mp[0] + e * mp[1])
    return a, b, d, e, float(xoff), float(yoff)


def solve_rigid(P, Q):
    """Move + rotate, least squares, 2 or more pairs."""
    return _rotation_fit(P, Q, with_scale=False)


def solve_similarity(P, Q):
    """Move + rotate + uniform scale, least squares, 2 or more pairs."""
    return _rotation_fit(P, Q, with_scale=True)


def solve_affine(P, Q):
    """Full affine (move, rotate, X/Y scale, skew), least squares, 3 or more pairs not on one line."""
    P, Q = _points(P, Q, 3)
    pc = P - P.mean(axis=0)
    sv = np.linalg.svd(pc, compute_uv=False)
    if sv[0] <= _eps(P, Q) or sv[1] <= 1e-9 * sv[0]:
        raise AlignError("The START points are on one line.")

    A = np.column_stack([P, np.ones(len(P))])
    sol_x = np.linalg.lstsq(A, Q[:, 0], rcond=None)[0]
    sol_y = np.linalg.lstsq(A, Q[:, 1], rcond=None)[0]
    a, b, xoff = (float(v) for v in sol_x)
    d, e, yoff = (float(v) for v in sol_y)
    if abs(a * e - b * d) <= 1e-12:
        raise AlignError("The transformation is singular.")
    return a, b, d, e, xoff, yoff


def apply_matrix(matrix, P):
    """The points P after the matrix, as an (N, 2) array."""
    a, b, d, e, xoff, yoff = matrix
    P = np.asarray(P, dtype=float).reshape(-1, 2)
    return np.column_stack([a * P[:, 0] + b * P[:, 1] + xoff, d * P[:, 0] + e * P[:, 1] + yoff])


def residuals(matrix, P, Q):
    """(errors, rms): the distance from each transformed START point to its DESTINATION point."""
    Q = np.asarray(Q, dtype=float).reshape(-1, 2)
    if len(Q) == 0:
        return [], 0.0
    err = np.hypot(*(apply_matrix(matrix, P) - Q).T)
    return [float(v) for v in err], float(math.sqrt((err ** 2).mean()))


def invert(matrix):
    """The inverse matrix."""
    a, b, d, e, xoff, yoff = matrix
    det = a * e - b * d
    if abs(det) <= 1e-12:
        raise AlignError("The transformation is singular.")
    ia, ib, id_, ie = e / det, -b / det, -d / det, a / det
    return ia, ib, id_, ie, -(ia * xoff + ib * yoff), -(id_ * xoff + ie * yoff)


# ##################################################################################################################
# Automatic alignment
# ##################################################################################################################

class AutoResult:
    """One candidate: matrix and pairs in requested motion direction, with hole-based counts for mixed types.

    score_error keeps the normalized hole-fit ranking error even when the matrix and preview are inverted.
    """

    def __init__(self, matrix, matched, total, P, Q, errors, rms, mirrored, score_error=None):
        self.matrix = matrix
        self.matched = matched
        self.total = total
        self.P = P
        self.Q = Q
        self.errors = errors
        self.rms = rms
        self.mirrored = mirrored
        self.score_error = score_error
        self.fit_P = None
        self.fit_Q = None


_FLIP = np.array([-1.0, 1.0])


def _after_mirror(matrix):
    """The matrix applied after a mirror about the Y axis (x -> -x)."""
    a, b, d, e, xoff, yoff = matrix
    return -a, b, -d, e, xoff, yoff


def _nn_vectors(pts, sample):
    """The vector from each sampled point to its nearest other point: (angle mod pi, length)."""
    tree = STRtree(shapely.points(pts))
    idx_in, idx_tree = tree.query_nearest(shapely.points(pts[sample]), exclusive=True, all_matches=False)
    v = pts[idx_tree] - pts[sample[idx_in]]
    return np.mod(np.arctan2(v[:, 1], v[:, 0]), math.pi), np.hypot(v[:, 0], v[:, 1])


def _spread_indices(points, count):
    """Deterministic farthest-point sampling, with coordinate-order tie breaking."""
    if len(points) <= count:
        return np.arange(len(points))
    order = np.lexsort((points[:, 1], points[:, 0]))
    pts = points[order]
    # Include the interior before spreading out, rather than spending every anchor on remote mounting holes.
    chosen = [int(np.argmin(np.sum((pts - pts.mean(axis=0)) ** 2, axis=1)))]
    distance = np.full(len(pts), np.inf)
    for _ in range(1, count):
        distance = np.minimum(distance, np.sum((pts - pts[chosen[-1]]) ** 2, axis=1))
        distance[chosen] = -1
        chosen.append(int(distance.argmax()))
    return order[chosen]


def _angle_candidates(src, dst, rng, peaks=4, pair_limit=0):
    """Rotation angles (radians) from a histogram of the angle differences of the nearest neighbor vectors."""
    s_idx = _spread_indices(src, pair_limit) if pair_limit else rng.choice(len(src), min(len(src), 300), replace=False)
    d_idx = np.arange(len(dst)) if pair_limit else rng.choice(len(dst), min(len(dst), 1500), replace=False)
    a_s, l_s = _nn_vectors(src, s_idx)
    a_d, l_d = _nn_vectors(dst, d_idx)

    same_len = np.abs(l_s[:, None] - l_d[None, :]) <= 0.05 * l_s[:, None] + 1e-12
    theta = np.mod(a_d[None, :] - a_s[:, None], math.pi)[same_len]
    if len(theta) == 0:
        return [0.0, math.pi]

    bins = 360
    hist = np.bincount(np.minimum((theta / math.pi * bins).astype(int), bins - 1), minlength=bins).astype(float)
    smooth = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))
    is_peak = (smooth >= np.roll(smooth, 1)) & (smooth > np.roll(smooth, -1)) & (smooth > 0)
    found = [int(i) for i in np.argsort(-smooth) if is_peak[i]][:peaks]

    width = math.pi / bins
    angles = []
    for i in found:
        # the mean of the differences in the peak bin and its 2 neighbors (circular, mod pi)
        center = (i + 0.5) * width
        delta = np.mod(theta - center + math.pi / 2, math.pi) - math.pi / 2
        near = delta[np.abs(delta) <= 1.5 * width]
        angles.append(center + (float(near.mean()) if len(near) else 0.0))
    # the nearest neighbor vectors have no direction: every angle is also tested + 180 degrees
    return angles + [a + math.pi for a in angles]


def _translation_candidates(moved, dst, cell, peaks=3):
    """Translations from a vote of the offsets dst - moved (moved: the rotated START sample)."""
    off = (dst[None, :, :] - moved[:, None, :]).reshape(-1, 2)
    keys = np.floor(off / cell).astype(np.int64)
    # 1 integer key for each cell (with 2 cells of margin for the neighbors): a 1D sort is much faster than a row sort
    kmin = keys.min(axis=0) - 2
    width = int(keys[:, 1].max() - kmin[1]) + 3
    flat = np.sort((keys[:, 0] - kmin[0]) * width + (keys[:, 1] - kmin[1]))
    starts = np.flatnonzero(np.r_[True, flat[1:] != flat[:-1]])
    uniq = flat[starts]
    counts = np.diff(np.r_[starts, len(flat)])

    # the count of each cell plus its 8 neighbors: a peak on a cell border is not split
    top = uniq[np.argsort(-counts)[:30]]
    near = (top[:, None] + np.array([i * width + j for i in (-1, 0, 1) for j in (-1, 0, 1)])[None, :]).ravel()
    pos = np.minimum(np.searchsorted(uniq, near), len(uniq) - 1)
    sums = np.where(uniq[pos] == near, counts[pos], 0).reshape(len(top), 9).sum(axis=1)
    scored = sorted(((int(c), int(k // width + kmin[0]), int(k % width + kmin[1])) for c, k in zip(sums, top)),
                    reverse=True)

    out, used = [], []
    for _total, x, y in scored:
        if any(abs(x - ux) <= 2 and abs(y - uy) <= 2 for ux, uy in used):
            continue
        used.append((x, y))
        mask = (np.abs(keys[:, 0] - x) <= 1) & (np.abs(keys[:, 1] - y) <= 1)
        out.append(np.median(off[mask], axis=0))
        if len(out) == peaks:
            break
    return out


def _nearest_pairs(matrix, src, dst_tree, dst, max_dist, center_limits=None):
    """(P, Q): each START point and the nearest aligner center within max_dist after the matrix."""
    moved = apply_matrix(matrix, src)
    idx_in, idx_tree = dst_tree.query_nearest(shapely.points(moved), max_distance=max_dist, all_matches=False)
    if center_limits is not None:
        distances = np.hypot(*(moved[idx_in] - dst[idx_tree]).T)
        keep = distances <= center_limits[idx_tree]
        idx_in, idx_tree, distances = idx_in[keep], idx_tree[keep], distances[keep]
        # A pad supports one correspondence, not several unrelated holes inside broad copper.
        order = np.lexsort((distances, idx_tree))
        _targets, first = np.unique(idx_tree[order], return_index=True)
        idx_in, idx_tree = idx_in[order[first]], idx_tree[order[first]]
    return src[idx_in], dst[idx_tree]


def _refine(matrix, src_fit, dst_tree, dst, radius, model, center_limits=None, pair_limit=0, fit_pairs=None):
    """
    ICP: pair with the nearest center, solve, repeat. src_fit are the START points, already mirrored for a
    mirrored candidate; the matrix maps src_fit.
    """
    solver = {'rigid': solve_rigid, 'similarity': solve_similarity, 'affine': solve_affine}[model]
    previous = None
    for step in range(5):
        P, Q = _nearest_pairs(matrix, src_fit, dst_tree, dst, 2.0 * radius, center_limits)
        if pair_limit:
            selected = _spread_indices(P, pair_limit)
            P, Q = P[selected], Q[selected]
        if previous is not None and (model == 'rigid' or step >= 3) and \
                np.array_equal(P, previous[0]) and np.array_equal(Q, previous[1]):
            break
        previous = P, Q
        try:
            # the first steps are rigid: a scale or a skew from a bad start would grow
            matrix = (solver if step >= 2 else solve_rigid)(P, Q)
            if fit_pairs is not None:
                fit_pairs[:] = [P, Q]
        except AlignError:
            return None
    return matrix


def _score(matrix, src, dst_index, dst, dst_tree, radius, center_limits=None):
    """The START centers that fall inside an aligner solid after the matrix, and their nearest aligner centers."""
    moved = apply_matrix(matrix, src)
    hits = dst_index.tree.query(shapely.points(moved), predicate='intersects')
    inside = np.unique(hits[0]) if hits.size else np.empty(0, dtype=int)
    if len(inside) == 0:
        return 0, src[:0], dst[:0]
    if center_limits is not None:
        P, Q = _nearest_pairs(matrix, src[inside], dst_tree, dst, 2.0 * radius, center_limits)
        return len(P), P, Q
    idx_in, idx_tree = dst_tree.query_nearest(shapely.points(moved[inside]), max_distance=2.0 * radius,
                                              all_matches=False)
    return len(inside), src[inside][idx_in], dst[idx_tree]


def _same(m1, m2, corners, tol):
    return bool(np.all(np.hypot(*(apply_matrix(m1, corners) - apply_matrix(m2, corners)).T) <= tol))


_SEED_KEEP = 512


def _occupancy(dst, radius):
    """Boolean raster of the cells within 2 * radius of a center: (occupied, (origin, cell, dims)) or (None, None)."""
    cell = 2 * radius
    origin = dst.min(axis=0) - cell
    dims = np.ceil((dst.max(axis=0) - origin) / cell).astype(int) + 2
    if int(dims[0]) * int(dims[1]) > 8_000_000:
        return None, None
    hit = np.zeros(tuple(dims), dtype=bool)
    cells = np.floor((dst - origin) / cell).astype(int)
    hit[cells[:, 0], cells[:, 1]] = True
    occupied = hit.copy()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            occupied[max(dx, 0):dims[0] + min(dx, 0), max(dy, 0):dims[1] + min(dy, 0)] |=                 hit[max(-dx, 0):dims[0] + min(-dx, 0), max(-dy, 0):dims[1] + min(-dy, 0)]
    return occupied, (origin, cell, dims)


def _occupied_rank(matrices, probe, occupied, grid):
    """Indices of the _SEED_KEEP matrices that put most probe points next to some pad center."""
    origin, cell, dims = grid
    counts = np.empty(len(matrices), dtype=int)
    for offset in range(0, len(matrices), 4096):
        batch = matrices[offset:offset + 4096]
        x = batch[:, 0, None] * probe[:, 0] + batch[:, 1, None] * probe[:, 1] + batch[:, 4, None]
        y = batch[:, 2, None] * probe[:, 0] + batch[:, 3, None] * probe[:, 1] + batch[:, 5, None]
        ix = np.floor((x - origin[0]) / cell).astype(int)
        iy = np.floor((y - origin[1]) / cell).astype(int)
        valid = (ix >= 0) & (ix < dims[0]) & (iy >= 0) & (iy < dims[1])
        counts[offset:offset + 4096] = (occupied[np.where(valid, ix, 0), np.where(valid, iy, 0)] & valid).sum(axis=1)
    return np.argpartition(-counts, _SEED_KEEP)[:_SEED_KEEP]


def _hole_candidates(src, dst, dst_tree, radius, rng, center_limits, pair_limit=0, report=None):
    """Rigid seeds from drill edges to pad edges, ranked by distinct drill support, not pad density.

    A drill neighbor may have many intervening SMD pads. Pair lengths propose a full transform; the existing
    center tolerance then tests it on the drill sample. Batches bound temporary storage on dense Gerbers.
    """
    sample_ids = _spread_indices(src, pair_limit) if pair_limit else rng.choice(len(src), min(len(src), 50), replace=False)
    sample = src if pair_limit else src[sample_ids]
    if pair_limit:
        edges = [(int(i), int(j)) for k, i in enumerate(sample_ids) for j in sample_ids[k + 1:]]
    else:
        src_tree = STRtree(shapely.points(src))
        origins, neighbors = src_tree.query_nearest(shapely.points(sample), exclusive=True, all_matches=False)
        edges = sorted(set(tuple(sorted((int(sample_ids[i]), int(j)))) for i, j in zip(origins, neighbors)))
    xmin, ymin = src.min(axis=0)
    xmax, ymax = src.max(axis=0)
    corners = np.array([[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]])
    points = shapely.points(dst)
    x_order = np.argsort(dst[:, 0])
    sorted_x = dst[x_order, 0]
    seeds = []
    hypotheses = 0
    done = 0
    strong = 0
    occupied, grid = _occupancy(dst, radius)
    probe = sample[np.linspace(0, len(sample) - 1, min(len(sample), 16)).astype(int)]
    for first, second in edges:
        v = src[second] - src[first]
        length = float(np.hypot(*v))
        if length <= 1e-12:
            continue
        best = []
        found = []
        for start in range(0, len(dst), 1024):
            starts, ends = dst_tree.query(points[start:start + 1024], predicate='dwithin',
                                         distance=length + 2 * radius)
            starts = starts + start
            delta = dst[ends] - dst[starts]
            lengths = np.hypot(delta[:, 0], delta[:, 1])
            keep = (lengths > 1e-12) & (np.abs(lengths - length) <= 2 * radius)
            starts, delta = starts[keep], delta[keep]
            angles = np.arctan2(delta[:, 1], delta[:, 0]) - math.atan2(v[1], v[0])
            c, s = np.cos(angles), np.sin(angles)
            matrices = np.column_stack([c, -s, s, c,
                                        dst[starts, 0] - c * src[first, 0] + s * src[first, 1],
                                        dst[starts, 1] - s * src[first, 0] - c * src[first, 1]])
            found.append(matrices)
        matrices = np.concatenate(found) if found else np.empty((0, 6))
        hypotheses += len(matrices)
        if occupied is not None and len(matrices) > _SEED_KEEP:
            # Cheap raster test on a few holes keeps the strongest hypotheses; the exact scoring below sees only those.
            matrices = matrices[_occupied_rank(matrices, probe, occupied, grid)]
        for offset in range(0, len(matrices), 128):
            batch = matrices[offset:offset + 128]
            x = batch[:, 0, None] * sample[:, 0] + batch[:, 1, None] * sample[:, 1] + batch[:, 4, None]
            y = batch[:, 2, None] * sample[:, 0] + batch[:, 3, None] * sample[:, 1] + batch[:, 5, None]
            moved = np.stack([x, y], axis=-1)
            flat = moved.reshape(-1, 2)
            # Only centers in this X slab can be within the unchanged search radius.
            left = np.searchsorted(sorted_x, flat[:, 0] - 2 * radius, side='left')
            right = np.searchsorted(sorted_x, flat[:, 0] + 2 * radius, side='right')
            width = int((right - left).max())
            if width == 0:
                continue
            if width <= 128:
                active = np.flatnonzero(right > left)
                positions = left[active, None] + np.arange(width)
                valid = positions < right[active, None]
                targets = x_order[np.minimum(positions, len(dst) - 1)]
                d2 = (flat[active, None, 0] - dst[targets, 0]) ** 2
                d2 += (flat[active, None, 1] - dst[targets, 1]) ** 2
                d2[~valid] = np.inf
                nearest = d2.argmin(axis=1)
                distances = np.sqrt(d2[np.arange(len(active)), nearest])
                within = np.flatnonzero(distances <= 2 * radius)
                hits = np.array([active[within], targets[within, nearest[within]]])
                distances = distances[within]
                tied = np.flatnonzero((d2[within] == d2[within, nearest[within], None]).sum(axis=1) > 1)
                if len(tied):
                    tie_hits, tie_distances = dst_tree.query_nearest(shapely.points(flat[hits[0, tied]]),
                        all_matches=False, return_distance=True)
                    hits[1, tied[tie_hits[0]]] = tie_hits[1]
                    distances[tied[tie_hits[0]]] = tie_distances
            else:
                hits, distances = dst_tree.query_nearest(shapely.points(flat),
                    max_distance=2 * radius, all_matches=False, return_distance=True)
            rows = hits[0] // len(sample)
            keep = distances <= center_limits[hits[1]]
            rows, targets, distances = rows[keep], hits[1][keep], distances[keep]
            keys = rows * len(dst) + targets
            order = np.lexsort((distances, keys))
            _keys, unique_indices = np.unique(keys[order], return_index=True)
            rows, distances = rows[order[unique_indices]], distances[order[unique_indices]]
            counts = np.bincount(rows, minlength=len(batch))
            errors = np.sqrt(np.bincount(rows, weights=distances ** 2, minlength=len(batch)) /
                             np.maximum(counts, 1))
            for i in np.lexsort((errors, -counts)):
                quality = (-int(counts[i]), float(errors[i]))
                if counts[i] < 2 or (len(best) == 5 and quality >= best[-1][0]):
                    break
                matrix = tuple(float(x) for x in batch[i])
                equivalent = next((j for j, (_q, m) in enumerate(best)
                                   if _same(matrix, m, corners, radius)), None)
                if equivalent is not None:
                    if best[equivalent][0] <= quality:
                        continue
                    best.pop(equivalent)
                best.append((quality, matrix))
                best.sort(key=lambda item: item[0])
                best = best[:5]
        seeds.extend(matrix for _quality, matrix in best)
        done += 1
        if report is not None:
            report(done, len(edges))
        # Three different edges that each place every sampled hole already give the complete answer.
        strong += bool(best) and best[0][0][0] == -len(sample)
        if strong >= 3:
            break
    logging.getLogger('base').info('[Align Objects broad search] holes=%d pads=%d edges=%d hypotheses=%d seeds=%d',
                                  len(src), len(dst), len(edges), hypotheses, len(seeds))
    return seeds


def _supported_hole_candidates(src, dst, dst_tree, radius, matched, mirrors, stats):
    """Bounded near-exact rigid placements, including alternatives to a supported local fit.

    A rival with 95% of the support can miss at most `missing` holes. Of `missing + 1` disjoint
    edges at least one survives. Long edges stabilize rotation; a tight length annulus and a
    constellation reject seeds before ICP. This is hypothesis generation, not an acceptance test.
    Noisy/low-coverage fits and exhausted budgets still use the original broad search.
    """
    missing = len(src) - math.ceil(0.95 * matched)
    if missing + 1 > min(len(src) // 2, 64):
        stats['fallback'] = 'insufficient disjoint edges'
        return None
    if (missing + 1) * len(dst) ** 2 > 2e9:
        # Each edge scans all pad pairs; on a dense Gerber the broad search is cheaper.
        stats['fallback'] = 'near-exact scan too large'
        return None
    # A candidate tied at normalized residual precision 0.000 has RMS <= 0.0005 * radius.
    # Bound each endpoint error by sqrt(N) times that RMS, with margin for floating point.
    error = 0.0005001 * radius * math.sqrt(len(src)) + _eps(src, dst)
    available = np.ones(len(src), dtype=bool)
    edges = []
    for _ in range(missing + 1):
        first = int(np.flatnonzero(available)[0])
        distances = np.sum((src - src[first]) ** 2, axis=1)
        distances[~available] = -1
        second = int(distances.argmax())
        length = math.sqrt(float(distances[second]))
        if length <= 4 * error:
            stats['fallback'] = 'degenerate constellation'
            return None
        available[[first, second]] = False
        edges.append((first, second, length))
    stats['edges'] = len(edges)
    witness = src[np.linspace(0, len(src) - 1, min(len(src), max(64, 2 * missing + 1)), dtype=int)]
    seeds = []
    for first, second, length in edges:
        bound = error * (2 + 4 * np.linalg.norm(witness - src[first], axis=1) / length)
        for start in range(0, len(dst), 64):
            d2 = (dst[None, :, 0] - dst[start:start + 64, None, 0]) ** 2
            d2 += (dst[None, :, 1] - dst[start:start + 64, None, 1]) ** 2
            rows, ends = np.nonzero((d2 >= (length - 2 * error) ** 2) &
                                   (d2 <= (length + 2 * error) ** 2))
            starts = rows + start
            delta = dst[ends] - dst[starts]
            stats['hypotheses'] += len(starts) * len(mirrors)
            if stats['hypotheses'] > 20000:
                stats['fallback'] = 'near-exact hypothesis budget'
                return None
            for mirrored in mirrors:
                flip = _FLIP if mirrored else 1.0
                vector = (src[second] - src[first]) * flip
                angles = np.arctan2(delta[:, 1], delta[:, 0]) - math.atan2(vector[1], vector[0])
                c, s = np.cos(angles), np.sin(angles)
                origin = src[first] * flip
                matrices = np.column_stack([c, -s, s, c, dst[starts, 0] - c * origin[0] + s * origin[1],
                                             dst[starts, 1] - s * origin[0] - c * origin[1]])
                for offset in range(0, len(matrices), 128):
                    batch = matrices[offset:offset + 128]
                    points = witness * flip
                    x = batch[:, 0, None] * points[:, 0] + batch[:, 1, None] * points[:, 1] + batch[:, 4, None]
                    y = batch[:, 2, None] * points[:, 0] + batch[:, 3, None] * points[:, 1] + batch[:, 5, None]
                    hits, distances = dst_tree.query_nearest(shapely.points(np.stack([x, y], axis=-1).reshape(-1, 2)),
                        max_distance=float(bound.max()), all_matches=False, return_distance=True)
                    keep = distances <= bound[hits[0] % len(witness)]
                    counts = np.bincount(hits[0, keep] // len(witness), minlength=len(batch))
                    for i in np.flatnonzero(counts >= len(witness) - missing):
                        seeds.append((tuple(batch[i]), mirrored))
                        if len(seeds) > 256:
                            stats['fallback'] = 'constellation survivor budget'
                            return None
    stats['survivors'] = len(seeds)
    return seeds


def auto_align(src_index, dst_index, model='rigid', allow_mirror=False, max_results=5, progress=None, pair_limit=0):
    """
    Find the matrices that put the most START centers (pads / holes of the aligned object) inside the features of
    the aligner object. No scipy: STRtree and numpy only.

    1. mixed types: drill-edge -> pad-edge seeds; same types: nearest-neighbor rotation histogram
    2. translation candidates: for each rotation, a vote of the offsets from a START sample to all aligner centers
    3. ICP refine with the selected model, then the overlap score on all START centers

    :param src_index:       SnapIndex of the aligned object
    :param dst_index:       SnapIndex of the aligner object
    :param model:           'rigid', 'similarity' or 'affine', the model of the refine
    :param allow_mirror:    also test the START centers mirrored (a bottom layer)
    :param max_results:     the number of distinct candidates to return
    :param progress:        callable(int percent) or None
    :param pair_limit:      positive source-anchor and actual solver-pair cap; zero keeps the original search
    :return:                list of AutoResult, best first

    A Gerber/Excellon pair is always solved as holes -> pads, then inverted when the Gerber is moving.
    Unrelated SMD pads never enter the coverage denominator.
    """
    if src_index.kind == 'gerber' and dst_index.kind == 'excellon':
        # Always solve holes -> pads, even when the user is moving the Gerber.
        results = auto_align(dst_index, src_index, model=model, allow_mirror=allow_mirror,
                             max_results=max_results, progress=progress, pair_limit=pair_limit)
        reversed_results = []
        for result in results:
            matrix = invert(result.matrix)
            errors, rms = residuals(matrix, result.Q, result.P)
            reversed_results.append(AutoResult(matrix, result.matched, result.total, result.Q, result.P,
                                               errors, rms, result.mirrored, score_error=result.score_error))
            reversed_results[-1].fit_P = result.fit_Q
            reversed_results[-1].fit_Q = result.fit_P
        return reversed_results

    src = src_index.centers
    dst = dst_index.centers
    if len(src) < 2:
        raise AlignError("The aligned object needs at least 2 pads or holes.")
    if len(dst) < 2 or dst_index.tree is None:
        raise AlignError("The aligner object needs at least 2 pads or holes.")
    if model not in ('rigid', 'similarity', 'affine'):
        model = 'rigid'
    minimum = 3 if model == 'affine' else 2
    if pair_limit and pair_limit < minimum:
        raise AlignError("At least %d point pairs are needed for this fit model." % minimum)

    def report(value):
        if progress is not None:
            progress(int(value))

    rng = np.random.default_rng(0)
    radius = float(np.median(np.sqrt(dst_index.areas / math.pi)))
    if not radius > 0:
        raise AlignError("The aligner object needs at least 2 pads or holes.")

    dst_tree = STRtree(shapely.points(dst))
    center_limits = None
    if src_index.kind == 'excellon' and dst_index.kind == 'gerber':
        # Non-overlapping center neighborhoods distinguish dense SMD coincidences from actual hole fits.
        # Retain the existing tolerance, capped by half the nearest distinct pad-center separation.
        neighbors, distances = dst_tree.query_nearest(shapely.points(dst), exclusive=True,
                                                     all_matches=False, return_distance=True)
        center_limits = np.full(len(dst), 2 * radius)
        center_limits[neighbors[0]] = np.minimum(2 * radius, distances / 2)
    sample = src[rng.choice(len(src), min(len(src), 50), replace=False)]
    src_fit = src[rng.choice(len(src), min(len(src), 2000), replace=False)]
    if pair_limit:
        sample = src[_spread_indices(src, pair_limit)]
        src_fit = src

    fitted = {}

    def refine(matrix, points, tree, targets, search_radius, fit_model, limits=None):
        pairs = [] if pair_limit else None
        matrix = _refine(matrix, points, tree, targets, search_radius, fit_model, limits,
                         pair_limit=pair_limit, fit_pairs=pairs)
        if matrix is not None and pairs is not None:
            fitted[matrix] = pairs
        return matrix

    mirrors = (False, True) if allow_mirror else (False,)
    stats = dict(path='broad', hypotheses=0, edges=0, survivors=0, fallback='no supported near-exact fit')
    # the current position is always a candidate: the objects are often almost aligned
    candidates = []
    matrix = refine((1.0, 0.0, 0.0, 1.0, 0.0, 0.0), src_fit, dst_tree, dst, radius, model, center_limits)
    if matrix is not None:
        candidates.append((matrix, False))
    for m_i, mirrored in enumerate(mirrors):
        flip = _FLIP if mirrored else 1.0
        # Geometry bounds include unmatched features. This is a seed, never evidence or a live move.
        src_outline = src_index.vertices if len(src_index.vertices) else src
        dst_outline = dst_index.vertices if len(dst_index.vertices) else dst
        src_center = (src_outline.min(axis=0) + src_outline.max(axis=0)) / 2
        dst_center = (dst_outline.min(axis=0) + dst_outline.max(axis=0)) / 2
        translation = dst_center - src_center * flip
        seed = (1, 0, 0, 1, float(translation[0]), float(translation[1]))
        matrix = refine(seed, src_fit * flip, dst_tree, dst, radius, model, center_limits)
        if matrix is not None:
            candidates.append((_after_mirror(matrix) if mirrored else matrix, mirrored))

    if center_limits is not None and model == 'rigid' and not pair_limit:
        supported = 0
        for matrix, _mirrored in candidates:
            count, P, Q = _score(matrix, src, dst_index, dst, dst_tree, radius, center_limits)
            if count >= 3 and round(residuals(matrix, P, Q)[1] / radius, 3) == 0:
                supported = max(supported, count)
        # Only complete support proves no noisier placement can match MORE holes. The tight constellation
        # search covers near-exact rivals, not stronger noisy fits; partial support must use broad search.
        target = len(src)
        seeds = _supported_hole_candidates(src, dst, dst_tree, radius, target, mirrors, stats)
        if seeds is not None:
            paired_seeds = set()
            for seed, mirrored in seeds:
                flip = _FLIP if mirrored else 1.0
                P, Q = _nearest_pairs(seed, src_fit * flip, dst_tree, dst, 2.0 * radius, center_limits)
                pairs = mirrored, P.tobytes(), Q.tobytes()
                if pairs in paired_seeds:
                    continue
                paired_seeds.add(pairs)
                matrix = refine(seed, src_fit * flip, dst_tree, dst, radius, model, center_limits)
                if matrix is not None:
                    matrix = _after_mirror(matrix) if mirrored else matrix
                    candidates.append((matrix, mirrored))
                    count, P, Q = _score(matrix, src, dst_index, dst, dst_tree, radius, center_limits)
                    if count >= target and round(residuals(matrix, P, Q)[1] / radius, 3) == 0:
                        supported = max(supported, count)
            if supported >= target:
                stats.update(path='local/constellation', fallback='none')
            elif supported:
                stats['fallback'] = 'partial support cannot exclude stronger noisy placements'

    for m_i, mirrored in enumerate(mirrors):
        flip = _FLIP if mirrored else 1.0
        if src_index.kind == 'excellon' and dst_index.kind == 'gerber':
            if stats['path'] == 'local/constellation':
                report(90.0 * (m_i + 1) / len(mirrors))
                continue
            paired_seeds = set()
            for seed in _hole_candidates(src * flip, dst, dst_tree, radius, rng, center_limits, pair_limit=pair_limit,
                                         report=lambda i, n, m_i=m_i: report(90.0 * (m_i + i / n) / len(mirrors))):
                # Identical first ICP pairs give exactly the same solve and all subsequent iterations.
                P, Q = _nearest_pairs(seed, src_fit * flip, dst_tree, dst, 2.0 * radius, center_limits)
                pairs = P.tobytes(), Q.tobytes()
                if pairs in paired_seeds:
                    continue
                paired_seeds.add(pairs)
                matrix = refine(seed, src_fit * flip, dst_tree, dst, radius, model, center_limits)
                if matrix is not None:
                    candidates.append((_after_mirror(matrix) if mirrored else matrix, mirrored))
            report(90.0 * (m_i + 1) / len(mirrors))
            continue
        angles = _angle_candidates(src * flip, dst, rng, pair_limit=pair_limit)
        for a_i, theta in enumerate(angles):
            c, s = math.cos(theta), math.sin(theta)
            moved = (sample * flip) @ np.array([[c, s], [-s, c]])
            for t in _translation_candidates(moved, dst, radius):
                matrix = refine((c, -s, s, c, float(t[0]), float(t[1])), src_fit * flip, dst_tree, dst, radius,
                                 model)
                if matrix is not None:
                    candidates.append((_after_mirror(matrix) if mirrored else matrix, mirrored))
            report(90.0 * (m_i * len(angles) + a_i + 1) / (len(mirrors) * len(angles)))

    xmin, ymin = src.min(axis=0)
    xmax, ymax = src.max(axis=0)
    corners = np.array([[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]])

    results, moves = [], []
    for matrix, mirrored in candidates:
        matched, P, Q = _score(matrix, src, dst_index, dst, dst_tree, radius, center_limits)
        if src_index.kind == 'excellon' and dst_index.kind == 'gerber':
            matched = len(P)  # Count only holes with accepted center pairs, consistently with the preview.
        if matched < 2:
            continue
        errors, rms = residuals(matrix, P, Q)
        score_error = round(rms / radius, 3) if src_index.kind == 'excellon' and dst_index.kind == 'gerber' else None
        results.append(AutoResult(matrix, matched, len(src), P, Q, errors, rms, mirrored, score_error=score_error))
        if pair_limit:
            fit_P, fit_Q = fitted[_after_mirror(matrix) if mirrored else matrix]
            results[-1].fit_P = fit_P * (_FLIP if mirrored else 1.0)
            results[-1].fit_Q = fit_Q
        moves.append(float(np.hypot(*(apply_matrix(matrix, corners) - corners).T).max()))
    # equal scores: the smaller error wins, then the smaller move (the objects are often almost aligned)
    order = sorted(range(len(results)),
                   key=lambda i: (-results[i].matched, round(results[i].rms / radius, 3),
                                  results[i].mirrored, moves[i]))
    results = [results[i] for i in order]

    distinct = []
    for r in results:
        if not any(_same(r.matrix, k.matrix, corners, radius) for k in distinct):
            distinct.append(r)
        if len(distinct) == max_results:
            break
    report(100)

    if center_limits is not None:
        logging.getLogger('base').info(
            '[Align Objects search] holes=%d pads=%d path=%s edges=%d hypotheses=%d survivors=%d '
            'refined=%d results=%d fallback=%s', len(src), len(dst), stats['path'], stats['edges'],
            stats['hypotheses'], stats['survivors'], len(candidates), len(distinct), stats['fallback'])

    if not distinct:
        raise AlignError("No alignment was found.")
    return distinct


def bounded_align(src_index, dst_index, allow_mirror=False):
    """Basic registration: six spread distance-fingerprint landmarks, never a broad-search fallback.

    Bounded evidence isn't a proof of global uniqueness. Exhausted candidate budgets or inadequate
    support require inspection in Align Objects instead of silently accepting an unfinished search.
    """
    if src_index.kind == 'gerber' and dst_index.kind == 'excellon':
        results = bounded_align(dst_index, src_index, allow_mirror)
        return [AutoResult(invert(r.matrix), r.matched, r.total, r.Q, r.P,
                           *residuals(invert(r.matrix), r.Q, r.P), r.mirrored,
                           score_error=r.score_error) for r in results]
    src, dst = src_index.centers, dst_index.centers
    message = 'Bounded alignment is inconclusive. Use Align Objects to inspect candidates.'
    if min(len(src), len(dst)) < 2 or dst_index.tree is None:
        raise AlignError(message)
    # ponytail: quadratic fingerprints capped here; larger jobs belong in Align Objects.
    if max(len(src), len(dst)) > 4096 or len(src) ** 2 * len(dst) > 128000000:
        raise AlignError(message)
    radius = float(np.median(np.sqrt(dst_index.areas / math.pi)))
    if not radius > 0:
        raise AlignError(message)
    tree = STRtree(shapely.points(dst))
    neighbors, distances = tree.query_nearest(shapely.points(dst), exclusive=True,
                                              all_matches=False, return_distance=True)
    limits = np.full(len(dst), 2 * radius)
    limits[neighbors[0]] = np.minimum(2 * radius, distances / 2)
    tolerance = max(0.15 * radius, _eps(src, dst))
    sd = np.linalg.norm(src[:, None] - src[None, :], axis=2)
    dd = np.linalg.norm(dst[:, None] - dst[None, :], axis=2)
    sorted_distances = np.sort(dd, axis=1)
    # Endpoint fingerprints let unmatched mounting holes be skipped before choosing spread anchors.
    lengths, inverse_lengths = np.unique(sd, return_inverse=True)
    partners = np.empty((len(src), len(dst)), dtype=int)
    for target, row in enumerate(sorted_distances):
        positions = np.searchsorted(row, lengths)
        lo, hi = row[np.maximum(positions - 1, 0)], row[np.minimum(positions, len(dst) - 1)]
        compatible = np.minimum(np.abs(lo - lengths), np.abs(hi - lengths)) <= tolerance
        partners[:, target] = compatible[inverse_lengths].reshape(sd.shape).sum(axis=1)
    quality = partners.max(axis=1)
    eligible = np.flatnonzero(quality >= max(3, quality.max() * 0.8))
    if len(eligible) < 2:
        eligible = np.arange(len(src))
    anchors = [int(eligible[np.argmax(quality[eligible])])]
    for _ in range(1, min(6, len(src), len(dst), len(eligible))):
        spread = sd[eligible][:, anchors].min(axis=1)
        spread[np.isin(eligible, anchors)] = -1
        anchors.append(int(eligible[spread.argmax()]))
    edges = []
    for i, first in enumerate(anchors):
        for second in anchors[i + 1:]:
            length = sd[first, second]
            if length > tolerance:
                starts, ends = np.nonzero(np.abs(dd - length) <= 2 * tolerance)
                compatible = (partners[first, starts] >= max(min(3, len(src), len(dst)), quality[first] * 0.8)) & \
                             (partners[second, ends] >= max(min(3, len(src), len(dst)), quality[second] * 0.8))
                starts, ends = starts[compatible], ends[compatible]
                edges.append((len(starts), first, second, starts, ends))
    edges.sort(key=lambda edge: edge[0])
    mirrors = (False, True) if allow_mirror else (False,)
    hypotheses = sum(edge[0] for edge in edges) * len(mirrors)
    if hypotheses > 20000:
        raise AlignError(message + ' Landmark candidate budget exhausted.')
    xmin, ymin = src.min(axis=0)
    xmax, ymax = src.max(axis=0)
    corners = np.array([[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]])
    candidates = []
    outline_src = src_index.vertices if len(src_index.vertices) else src
    outline_dst = dst_index.vertices if len(dst_index.vertices) else dst
    center_src = (outline_src.min(axis=0) + outline_src.max(axis=0)) / 2
    center_dst = (outline_dst.min(axis=0) + outline_dst.max(axis=0)) / 2
    for mirrored in mirrors:
        flip = _FLIP if mirrored else 1.0
        shift = center_dst - center_src * flip
        candidates.append(((1, 0, 0, 1, *shift), mirrored))
    # Verify each landmark hypothesis on all features, in bounded batches, before expensive ICP.
    ranked = []
    for _count, first, second, starts, ends in edges:
        for mirrored in mirrors:
            flip = _FLIP if mirrored else 1.0
            vector = (src[second] - src[first]) * flip
            delta = dst[ends] - dst[starts]
            angles = np.arctan2(delta[:, 1], delta[:, 0]) - math.atan2(vector[1], vector[0])
            c, s = np.cos(angles), np.sin(angles)
            origin = src[first] * flip
            matrices = np.column_stack([c, -s, s, c, dst[starts, 0] - c * origin[0] + s * origin[1],
                                         dst[starts, 1] - s * origin[0] - c * origin[1]])
            for offset in range(0, len(matrices), 64):
                batch = matrices[offset:offset + 64]
                points = src * flip
                x = batch[:, 0, None] * points[:, 0] + batch[:, 1, None] * points[:, 1] + batch[:, 4, None]
                y = batch[:, 2, None] * points[:, 0] + batch[:, 3, None] * points[:, 1] + batch[:, 5, None]
                hits, distances = tree.query_nearest(shapely.points(np.stack([x, y], axis=-1).reshape(-1, 2)),
                    max_distance=2 * radius, all_matches=False, return_distance=True)
                keep = distances <= limits[hits[1]]
                rows, targets, errors = hits[0, keep] // len(src), hits[1, keep], distances[keep]
                keys = rows * len(dst) + targets
                order = np.lexsort((errors, keys))
                _, unique = np.unique(keys[order], return_index=True)
                rows, errors = rows[order[unique]], errors[order[unique]]
                counts = np.bincount(rows, minlength=len(batch))
                squared = np.bincount(rows, weights=errors ** 2, minlength=len(batch))
                for i in np.flatnonzero(counts >= min(3, len(src), len(dst))):
                    ranked.append((-int(counts[i]), float(squared[i] / counts[i]), tuple(batch[i]), mirrored))
    ranked.sort(key=lambda item: item[:2])
    minimum_support = max(min(3, len(src), len(dst)), -ranked[0][0] * 0.8) if ranked else 3
    for _count, _error, matrix, mirrored in ranked:
        if -_count < minimum_support:
            break
        physical = _after_mirror(matrix) if mirrored else matrix
        if any(_same(physical, _after_mirror(m) if f else m, corners, tolerance) for m, f in candidates):
            continue
        candidates.append((matrix, mirrored))
        if len(candidates) > 128:
            raise AlignError(message + ' Refinement candidate budget exhausted.')
    results = []
    for seed, mirrored in candidates:
        flip = _FLIP if mirrored else 1.0
        matrix = _refine(seed, src * flip, tree, dst, radius, 'rigid', limits)
        if matrix is None:
            continue
        matrix = _after_mirror(matrix) if mirrored else matrix
        count, P, Q = _score(matrix, src, dst_index, dst, tree, radius, limits)
        if count < min(3, len(src), len(dst)):
            continue
        # Nearby centers outside target copper can seed ICP, but mustn't bias the accepted fit.
        try:
            matrix = solve_rigid(P * flip, Q)
        except AlignError:
            continue
        matrix = _after_mirror(matrix) if mirrored else matrix
        count, P, Q = _score(matrix, src, dst_index, dst, tree, radius, limits)
        if count < min(3, len(src), len(dst)):
            continue
        errors, rms = residuals(matrix, P, Q)
        results.append(AutoResult(matrix, count, len(src), P, Q, errors, rms, mirrored,
                                  score_error=round(rms / radius, 3)))
    results.sort(key=lambda r: (-r.matched, r.score_error, r.mirrored))
    distinct = []
    for result in results:
        if not any(_same(result.matrix, r.matrix, corners, radius) for r in distinct):
            distinct.append(result)
    logging.getLogger('base').debug('[Basic bounded alignment] features=%d/%d landmarks=%d hypotheses=%d '
                                  'refined=%d results=%d', len(src), len(dst), len(anchors), hypotheses,
                                  len(candidates), len(distinct))
    if not distinct:
        raise AlignError(message)
    return distinct


def is_ambiguous(results):
    """True if a different candidate has almost the same score as the best one."""
    if len(results) < 2 or results[0].matched <= 0:
        return False
    best = results[0]
    # For hole -> pad fits, dense SMD copper can contain holes even for a clearly worse center fit.
    # Honor the same normalized residual precision used for ranking; keep legacy same-type semantics.
    return any(candidate.matched >= 0.95 * best.matched and
               (best.score_error is None or candidate.score_error <= best.score_error)
               for candidate in results[1:])
