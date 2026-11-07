"""Detached export geometry and an isolated GUI-thread preview poster."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from .PCBViewerGen import layer_styles


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
