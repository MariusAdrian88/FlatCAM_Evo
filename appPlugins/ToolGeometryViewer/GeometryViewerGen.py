"""Detached Geometry paths and worker-side preview meshes."""

from dataclasses import dataclass, replace
from math import isfinite

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from appPlugins.ToolPCBViewer.PCBViewerGen import extrude_mesh


def shapes(value, multipart=False):
    if isinstance(value, BaseGeometry):
        if not value.is_empty:
            if multipart and hasattr(value, 'geoms'):
                yield from shapes(tuple(value.geoms), multipart=True)
            else:
                yield value
    elif isinstance(value, (list, tuple, dict)):
        for part in value.values() if isinstance(value, dict) else value:
            yield from shapes(part, multipart=multipart)


def has_geometry(obj):
    return any(next(shapes(tool.get('solid_geometry')), None) is not None for tool in obj.tools.values())


def snapshot(obj, units):
    """Shapely shapes are immutable; copy the containers, not their coordinates."""
    factor = 1 if obj.units.upper() == units.upper() else (25.4 if units.upper() == 'MM' else 1 / 25.4)
    tools = []
    for uid, tool in tuple(obj.tools.items()):
        paths = tuple(shapes(tool.get('solid_geometry')))
        if paths:
            diameter = float(tool['tooldia']) * factor
            if not isfinite(diameter) or diameter <= 0:
                raise ValueError('Tool diameter must be positive and finite.')
            tools.append((uid, diameter, paths, factor))
    bounds = tuple(float(coord) * factor for coord in obj.bounds())
    if not tools or not all(map(isfinite, bounds)):
        raise ValueError('No usable stored tool paths or geometry bounds.')
    return bounds, tuple(tools)


@dataclass(frozen=True)
class PreviewMesh:
    geometry: BaseGeometry
    vertices: np.ndarray
    faces: np.ndarray
    origin: tuple
    tool: object = None
    lines: object = None
    copper: 'PreviewMesh | None' = None


def board_mesh(bounds, margin):
    xmin, ymin, xmax, ymax = bounds
    if not isfinite(margin) or margin < 0:
        raise ValueError('Board margin must be nonnegative and finite.')
    board = shapely.box(xmin - margin, ymin - margin, xmax + margin, ymax + margin)
    mesh = extrude_mesh(board) if board.area else (
        np.empty((0, 3)), np.empty((0, 3), dtype=np.uint32), (0, 0))
    return PreviewMesh(board, *mesh)


def with_remaining_copper(board, cuts, cancelled):
    swept = shapely.union_all([cut.geometry for cut in cuts])
    if cancelled():
        return board
    remaining = board.geometry.difference(swept)
    if cancelled():
        return board
    # The remaining copper is a volume: its cap sits above the substrate and its walls bound the swept channels.
    return replace(board, copper=PreviewMesh(remaining, *extrude_mesh(remaining)))


def top_cap(geometry):
    vertices, faces, origin = extrude_mesh(geometry)
    # Only upward faces: the tool coverage is drawn as a flat surface.
    cap = faces[(vertices[faces, 2] == 1).all(axis=1)]
    xy = vertices[cap, :2]
    ab, ac = xy[:, 1] - xy[:, 0], xy[:, 2] - xy[:, 0]
    cap = cap[ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0] > 0]
    return vertices[:int(cap.max()) + 1].copy() if len(cap) else vertices[:0].copy(), cap, origin


def footprints(tools, cancelled=lambda: False):
    layers = []
    for uid, diameter, paths, factor in tools:
        if cancelled():
            break
        paths = [part for path in shapes(paths, multipart=True)
                 for part in shapes(path.boundary if path.geom_type == 'Polygon' else path, multipart=True)]
        # A collapsed XY line is vertical travel, not a stored point operation.
        paths = [path for path in paths if path.geom_type == 'Point' or path.length > 0]
        if factor != 1:
            paths = shapely.transform(paths, lambda xy: xy * factor)
        footprint = shapely.union_all(shapely.buffer(paths, diameter / 2, quad_segs=16))
        if footprint.is_empty or cancelled():
            continue
        vertices, faces, origin = top_cap(footprint)
        segments = []
        for path in paths:
            coords = shapely.get_coordinates(path) - origin
            if len(coords) > 1:
                start, end = coords[:-1], coords[1:]
                moves = np.any(start != end, axis=1)
                segments.append(np.stack((start[moves], end[moves]), axis=1).reshape(-1, 2))
        lines = np.concatenate(segments) if segments else np.empty((0, 2))
        layers.append(PreviewMesh(footprint, vertices, faces, origin, uid, lines))
    return tuple(layers)
