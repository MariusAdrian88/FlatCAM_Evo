# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Types of the Machine Profiles feature (Stage 0): frames, setup, motion records, violations, check result.

No Qt, no app import. A value of None means unknown. All lengths are in mm in the records (see the package
docstring for the frame contract).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

XYZ = Tuple[Optional[float], Optional[float], Optional[float]]


@dataclass(frozen=True)
class Frame:
    """The effective offset frame of a record: W = M - (wcs_offset + g92 + tlo)."""
    wcs_id: Optional[str] = None            # 'G54'..'G59'
    wcs_offset: Optional[XYZ] = None
    g92: Optional[XYZ] = None
    tlo: Optional[float] = None             # Z only
    units: Optional[str] = None             # 'mm' or 'inch'


@dataclass
class Setup:
    """The starting state of a check. Every field is optional: None = unknown."""
    profile_hash: Optional[str] = None
    dialect: Optional[str] = None           # preprocessor name
    wcs_offsets: Optional[Dict[str, XYZ]] = None   # per WCS id
    active_wcs: Optional[str] = None
    g92: Optional[XYZ] = None
    tlo: Optional[float] = None
    start: Optional[XYZ] = None
    units: Optional[str] = None
    distance_mode: Optional[str] = None     # 'G90' or 'G91'
    arc_mode: Optional[str] = None          # 'G90.1' or 'G91.1'
    plane: Optional[str] = None             # 'G17', 'G18' or 'G19'
    motion_mode: Optional[str] = None       # 'G0', 'G1', 'G2', 'G3'
    source: Optional[str] = None            # 'controller' = the live state of the controller
    axes: Optional[str] = None              # the axis letters of the controller, 'XYZ' or 'XYZA'; None = 'XYZ'


@dataclass(frozen=True)
class MotionRecord:
    """One interpreted motion block. Start/end in M are None when unknown."""
    line: int
    block: str
    start_w: XYZ
    end_w: XYZ
    frame: Frame
    start_m: Optional[XYZ] = None
    end_m: Optional[XYZ] = None
    motion_mode: Optional[str] = None
    arc_center: Optional[Tuple[float, float, float]] = None
    arc_direction: Optional[str] = None     # 'cw' or 'ccw'
    arc_extent: Optional[float] = None      # radians, full circle = 2*pi
    feed: Optional[float] = None
    s: Optional[float] = None
    laser_power: Optional[float] = None
    tool: Optional[int] = None
    tool_diameter: Optional[float] = None
    fixed_role: Optional[str] = None        # 'toolchange', 'end', 'probe'; None if not fixed
    unverified_reason: Optional[str] = None
    # the end in M for each axis, None for an unknown axis (end_m is None as soon as one axis is unknown)
    end_m_axes: Optional[Tuple[Optional[float], Optional[float], Optional[float]]] = None
    # (line, S) of each speed that the running spindle got since the record before, this block included; the
    # speed of a block without a move (M3 S25000, a dwell, then S5000) is checked from here
    s_events: Tuple[Tuple[int, float], ...] = ()
    # True when the block moves no XY (G0/G1/probe without X and Y words): the XY length is 0 also when XY is unknown
    no_xy_motion: bool = False

    @property
    def verified(self) -> bool:
        return self.unverified_reason is None


class ViolationKind(Enum):
    OUT_OF_BED = 'out_of_bed'
    EXCLUSION = 'exclusion'
    FEED = 'feed'
    RAPID = 'rapid'
    RPM = 'rpm'
    LASER_POWER = 'laser_power'
    CUT_DEPTH = 'cut_depth'
    Z_TRAVEL = 'z_travel'
    UNVERIFIED = 'unverified'


@dataclass(frozen=True)
class Violation:
    kind: ViolationKind
    line: int
    message: str
    area: Optional[str] = None              # name of the exclusion area, if any
    severity: str = 'warn'                  # 'warn' or 'reject'
    start_w: Optional[XYZ] = None           # the move of the record in W (the marker and the red move of the GUI)
    end_w: Optional[XYZ] = None


class CheckStatus(Enum):
    CHECKED = 'checked'
    PARTIAL = 'partial'
    VIOLATIONS = 'violations'


@dataclass
class CheckResult:
    violations: List[Violation] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)

    @property
    def status(self) -> CheckStatus:
        if any(v.kind is not ViolationKind.UNVERIFIED for v in self.violations):
            return CheckStatus.VIOLATIONS
        if self.violations:
            return CheckStatus.PARTIAL
        return CheckStatus.CHECKED


class Purpose(Enum):
    DESIGN = 'design'
    FILE = 'file'
    STREAM = 'stream'
    CALIBRATION = 'calibration'
