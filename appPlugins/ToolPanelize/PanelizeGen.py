"""Panel conversion data, using the shared panelization algorithms."""

from copy import deepcopy

from shapely import Polygon

from appParsers import panelize_core
from camlib import translate_geometry


def geometry_from_gerber(tools, solid_geometry, offsets, abort=None, progress=None):
    """
    The multigeo tools of a Geometry panel made from a Gerber: every tool holds the copper of every copy.

    The copper is translated once per copy. The tools share the (immutable) translated geometries, but each tool has
    its own list and its own metadata. Empty geometries are dropped from the tools and kept in the composed solid.
    """
    new_tools = {}
    for tid, tool in tools.items():
        new_tool = {k: deepcopy(v) for k, v in tool.items() if k != 'solid_geometry'}
        new_tool['solid_geometry'] = []
        new_tools[tid] = new_tool

    source = panelize_core._split_multi(solid_geometry)
    solid = []
    tool_geometry = []
    for element, (dx, dy) in enumerate(offsets, start=1):
        panelize_core._check_abort(abort)
        moved = translate_geometry(source, dx, dy)
        solid.extend(moved)
        tool_geometry.extend(g for g in moved if not g.is_empty)
        panelize_core._report(progress, element, 1, 1)
    for tool in new_tools.values():
        tool['solid_geometry'] = list(tool_geometry)
    return new_tools, solid


def gerber_from_geometry(tools, solid_geometry, multigeo, offsets, abort=None, progress=None):
    src_tools, src_solid = panelize_core.gerber_tools_from_geometry(tools, solid_geometry, multigeo)
    result_tools, result_solid = panelize_core.panelize_gerber(
        src_tools, src_solid, offsets, abort=abort, progress=progress)
    as_regions = any(not isinstance(rec.get('solid'), Polygon) for rec in result_tools[0]['geometry'])
    return result_tools, result_solid, as_regions
