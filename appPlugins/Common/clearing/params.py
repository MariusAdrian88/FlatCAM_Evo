# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Pure shared settings of the NCC and Paint clearing jobs: the tool and the job as frozen data."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from shapely.geometry.base import BaseGeometry

from appCommon.ExclusionZones import distinct_zones, zones_of


@dataclass(frozen=True)
class ToolSpec:
    """One clearing tool with every setting resolved."""
    uid: int
    dia: float
    method_id: int
    overlap: float
    angle: float
    connect: bool
    contour: bool
    offset: float
    cleanup: bool
    direction: Literal["none", "climb", "conv"] = "none"
    data: dict = field(default_factory=dict)


def _number(data, key, default=0.0):
    try:
        return float(data.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def tool_direction(data, prefix):
    """The clearing direction of a tool row: 'climb', 'conv' or 'none' (the key is ``<prefix>clear_milling``)."""
    value = str(data.get(prefix + 'clear_milling', 'none')).lower() if data else 'none'
    return value if value in ('climb', 'conv') else 'none'


def tool_spec(uid, dia, data, prefix, offset_key):
    """
    The ToolSpec of one row of a tool table. ``prefix`` is ``tools_ncc_`` or ``tools_paint_``; the overlap is kept
    as the table stores it (percent).
    """
    from camlib import deepcopy_keep_geometry
    return ToolSpec(
        uid=uid, dia=float(dia), method_id=int(_number(data, prefix + 'method')),
        overlap=_number(data, prefix + 'overlap'), angle=_number(data, prefix + 'crosshatch_angle'),
        connect=bool(data.get(prefix + 'connect', False)), contour=bool(data.get(prefix + 'contour', True)),
        offset=_number(data, prefix + offset_key), cleanup=bool(data.get(prefix + 'final_cleanup', False)),
        direction=tool_direction(data, prefix), data=deepcopy_keep_geometry(data))


def copy_geometry(geometry):
    """A copy of the containers of a geometry (nested lists); the shapely objects are immutable and shared."""
    if isinstance(geometry, (list, tuple)):
        return [copy_geometry(item) for item in geometry]
    return geometry


@dataclass(frozen=True)
class SourceSnapshot:
    """
    The attributes of a source object that a clearing job reads, copied when the job is resolved: a later edit of
    the object does not change a queued job.
    """
    kind: str | None
    units: str | None
    solid_geometry: object
    tools: Mapping
    obj_options: Mapping

    @classmethod
    def of(cls, obj):
        if obj is None:
            return None
        if isinstance(obj, cls):
            return obj
        from camlib import deepcopy_keep_geometry
        return cls(
            kind=getattr(obj, 'kind', None),
            units=getattr(obj, 'units', None),
            solid_geometry=copy_geometry(getattr(obj, 'solid_geometry', None)),
            tools=deepcopy_keep_geometry(getattr(obj, 'tools', None) or {}),
            obj_options=dict(getattr(obj, 'obj_options', None) or {}),
        )


def freeze_options(app) -> Mapping:
    """
    A read-only copy of the options a job reads: the factory defaults, the application defaults and the project
    options, in this order of precedence (a missing key takes the next source, like the `_option` lookups do).
    """
    merged = {}
    defaults = getattr(app, 'defaults', None)
    for source in (getattr(defaults, 'factory_defaults', None), defaults, getattr(app, 'options', None)):
        if isinstance(source, Mapping):
            merged.update({key: value for key, value in source.items() if value is not None or key not in merged})
    return MappingProxyType(merged)


def object_zones(obj, plugin):
    """The exclusion zones of one plugin stored on a source object (none for an object that has no zones)."""
    return zones_of(getattr(obj, 'exclusion_zones', None), plugin)


@dataclass(frozen=True)
class JobSpec:
    """
    A fully resolved clearing job: no widget or option reads after this point.

    The containers (``storage``, ``tool_table``, ``areas``, ``points``, ``source``, ``options``) are private copies
    made by the resolver. ``frozen`` only protects the fields: a run takes its own copies of the tool dictionaries.
    """
    tools: list
    rest: bool
    order: int
    selection: Literal["all", "area", "reference", "click"]
    margin: float
    simplification: float
    plot: bool
    outname: str
    options: Mapping
    areas: list | None = None
    points: tuple | None = None                         # NCC click selection: the click points
    exclusion: BaseGeometry | None = None
    exclusion_zones: tuple = ()                         # the zones that made `exclusion`: the output keeps a copy
    # --- what the 2 plugins resolve besides the sketch above ---
    source: SourceSnapshot | None = None                # the cleared / painted object
    origin: object = None                               # the live source: only the link of the output object
    reference_source: SourceSnapshot | None = None      # NCC reference object
    selection_fn: Callable | None = None                # Paint: the selection that runs inside the job
    geometry: object = None                             # Paint: the selected polygons when no selection_fn
    storage: Mapping = field(default_factory=dict)      # the tool rows the job works on (private copy)
    tool_table: Mapping = field(default_factory=dict)   # NCC GUI: the tool table of the plugin (private copy)
    diameters: tuple = ()
    iso_diameters: tuple = ()
    milling_type: str | None = None
    has_offset: bool = False
    offset: float = 0.0
    method: int | None = None
    check_validity: bool = False
    prog_plot: bool = False
    mill_data: Mapping = field(default_factory=dict)
    circle_steps: int | None = None
    decimals: int = 4
    units: str = "MM"
    app_units: str = "MM"
    method_explicit: bool = False                       # Paint: the caller forced one method for every tool
    rest_offset: float = 0.0
