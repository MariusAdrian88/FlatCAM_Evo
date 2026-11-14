# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Pure shared settings of the NCC and Paint clearing jobs: the tool and the job as frozen data."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from shapely.geometry.base import BaseGeometry


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


@dataclass(frozen=True)
class JobSpec:
    """A fully resolved clearing job: no widget or option reads after this point."""
    tools: list
    rest: bool
    order: int
    selection: Literal["all", "area", "reference", "click"]
    margin: float
    simplification: float
    simplify_rule: Literal["covers", "dwithin"]
    plot: bool
    outname: str
    options: Mapping
    kernel_steps: Callable[[float, str], int]
    sweep_steps: Callable[[float, str], int]
    areas: list | None = None
    reference: object | None = None
    points: list | None = None
    exclusion: BaseGeometry | None = None
    edge_error: float | None = None
