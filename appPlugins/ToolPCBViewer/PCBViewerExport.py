"""Detached export geometry and an isolated GUI-thread preview poster."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import shapely

from .PCBViewerGen import layer_styles, polygons
from .STEPWriter import SIMPLIFY_MM

# Simplification of the legend outlines in millimeters; legend lines are about 0.15 mm wide.
LEGEND_SIMPLIFY_MM = 0.05


def _plating_tubes(walls, half_width):
    """Copper tubes of the given half width, centered on the plated hole walls.

    The walls arrive as separate segments. A tube made from the closed hole ring, with mitre joins, stays
    concentric with the hole and has no notches, so the STEP writer finds its arcs.
    """
    lines = [part for part in shapely.get_parts(shapely.get_parts(walls)) if isinstance(part, shapely.LineString)]
    if not lines:
        return []
    # The overlay that made the walls leaves sub-micron kinks; they would become notches of the tube.
    merged = shapely.simplify(shapely.line_merge(shapely.MultiLineString(lines)), half_width / 20)
    holes, cuts, dangles, invalid = shapely.polygonize_full(list(shapely.get_parts(merged)))
    tubes = [shapely.difference(shapely.buffer(hole, half_width, join_style='mitre'),
                                shapely.buffer(hole, -half_width, join_style='mitre'))
             for hole in shapely.get_parts(holes)]
    # Open walls, such as a hole that the board edge cuts, keep a buffer of their lines.
    rest = [part for part in shapely.get_parts([cuts, dangles, invalid]) if not part.is_empty]
    if rest:
        tubes.append(shapely.buffer(shapely.union_all(rest), half_width, cap_style='flat', join_style='mitre'))
    return shapely.union_all(tubes)


def export_solids(layers, values, units, board_only=False):
    """Layer polygons in project coordinates with their Z range and RGBA color, for a STEP solid model.

    :param board_only: only the board body with its outline, cutouts and holes, for the fit of a case
    """
    styles = layer_styles(layers, values, units)
    factor = 1 / 25.4 if units.upper() == 'IN' else 1
    solids = []
    for layer in layers:
        if not len(layer.faces) or (board_only and layer.name != 'board'):
            continue
        geometry = layer.geometry
        if layer.name == 'plating':
            # The preview plates the hole walls with zero-thickness faces; a solid needs a copper tube.
            geometry = _plating_tubes(geometry, values['copper_thickness'] / 2000 * factor)
        tolerance = SIMPLIFY_MM
        if layer.name.startswith('overlay'):
            # The legend is ink: its font outlines are dense chains that need no CAD precision.
            tolerance = LEGEND_SIMPLIFY_MM
            geometry = shapely.simplify(geometry, tolerance * factor)
        parts = polygons(geometry)
        if not parts:
            continue
        style = styles[layer.name]
        # The board body stays one solid: it is the part that a case design uses, and tile seams look bad on it.
        solids.append(dict(name=layer.name, polygons=parts, z=float(style['z']), tolerance=tolerance,
                           split=layer.name != 'board',
                           thickness=float(style['thickness']), color=tuple(float(v) for v in style['color'])))
    if not solids:
        raise ValueError('Select a Gerber object with nonempty geometry.')
    return solids


def export_meshes(layers, values, units):
    styles = layer_styles(layers, values, units)
    origin = np.asarray(next(layer.origin for layer in layers if layer.name == 'board'), dtype=np.float64)
    meshes = []
    for layer in layers:
        if not len(layer.faces):
            continue
        style = styles[layer.name]
        positions = layer.vertices.astype(np.float64)
        # Subtract original origins before any float32 GPU/U3D conversion.
        positions[:, :2] += np.asarray(layer.origin, dtype=np.float64) - origin
        positions[:, 2] = positions[:, 2] * style['thickness'] + style['z']
        meshes.append(dict(name=layer.name, positions=positions, faces=layer.faces,
                           color=style['color'][:3], opacity=float(style['color'][3])))
    return meshes


def export_bounds(layers, values, units):
    """Frame actual vertices without allocating transformed copies of all meshes."""
    styles = layer_styles(layers, values, units)
    origin = np.asarray(next(layer.origin for layer in layers if layer.name == 'board'), dtype=np.float64)
    bounds = []
    for layer in layers:
        if not len(layer.faces):
            continue
        style = styles[layer.name]
        limits = np.array([layer.vertices.min(axis=0), layer.vertices.max(axis=0)], dtype=np.float64)
        limits[:, :2] += np.asarray(layer.origin, dtype=np.float64) - origin
        limits[:, 2] = limits[:, 2] * style['thickness'] + style['z']
        bounds.append(limits)
    if not bounds:
        raise ValueError('Select a Gerber object with nonempty geometry.')
    return np.array([np.min(bounds, axis=(0, 1)), np.max(bounds, axis=(0, 1))])


def render_poster(layers, values, units, frame):
    """Render the actual two-sided PCB materials without touching the live canvas."""
    from PIL import Image
    from vispy import scene
    from appGUI.PlotCanvas3d import Camera_3D
    from .PCBViewerView import PCBView

    page = np.asarray(frame['page_size'])
    size = tuple(np.maximum(1, np.rint(page / page.max() * 1600)).astype(int))
    background = (0.75, 0.75, 0.75, 1)
    canvas = scene.SceneCanvas(size=size, bgcolor=background, show=False)
    view = None
    try:
        canvas.unfreeze()
        canvas.view = canvas.central_widget.add_view(bgcolor=background)
        canvas.grid_lines_enabled = False
        canvas.content_bounds = None
        canvas.grid = SimpleNamespace(visible=False)
        canvas.freeze()
        canvas.view.camera = Camera_3D()
        view = PCBView(canvas)
        origin = np.asarray(next(layer.origin for layer in layers if layer.name == 'board'), dtype=np.float64)
        for layer in layers:
            view.set_layer(replace(layer, origin=tuple(np.asarray(layer.origin, dtype=np.float64) - origin)))
        view.update_style(values, units)
        camera = canvas.view.camera
        camera.set_view('iso')
        camera.center = tuple(frame['center'])
        # VisPy scale_factor is the viewport's shorter dimension, not always its height.
        camera.scale_factor = min(page) / frame['scale']
        view.sort_layers()
        # Render native physical pixels to match HiDPI camera/scissor coordinates, then resize RGB.
        poster = canvas.render(alpha=False)
        if poster.shape[:2] != size[::-1]:
            poster = np.asarray(Image.fromarray(poster).resize(size, Image.Resampling.LANCZOS))
        return poster
    finally:
        try:
            if view is not None:
                view.clear()
        finally:
            canvas.close()
