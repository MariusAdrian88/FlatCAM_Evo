# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/16/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Job preparation of the GRBL Sender: the lines that are streamed."""

import dataclasses
import math
import re
import gettext
import builtins
from types import SimpleNamespace

from appCommon.machine_profile.correct import MARKER_RE
from appPlugins.Common.grbl.grbl_client import validate_stream_lines
from appPlugins.Common.gcode_arcs import split_arcs
from appPlugins.Common.gcode_segment import _ModalState, _WORD_RE, _words, _fmt, split_long_moves

_COMMENT_PAREN_RE = re.compile(r'\([^)]*\)')
_ = builtins.__dict__.get('_', gettext.gettext)


def clean_lines_numbered(text, keep_markers=False):
    """
    Make the stream lines out of G-code text: remove the comments ('(...)' and
    ';...'), the '%' lines and the empty lines; strip the spaces.

    :param text: G-code text.
    :param keep_markers: keep the machine profile markers '(MP:FIXED BEGIN <role>)' / '(MP:FIXED END)' as lines
        (the levelling passes them through; the final clean removes them).
    :return: list of (source line number, str). The source line number is 1-based.
    """
    result = []
    for number, raw in enumerate((text or '').splitlines(), start=1):
        if keep_markers and MARKER_RE.match(raw):
            result.append((number, raw.strip()))
            continue
        line = _COMMENT_PAREN_RE.sub('', raw)
        line = line.split(';', 1)[0].strip()
        if not line or line == '%':
            continue
        result.append((number, line))
    return result


def clean_lines(text):
    """
    Make the stream lines out of G-code text: remove the comments ('(...)' and
    ';...'), the '%' lines and the empty lines; strip the spaces.

    :param text: G-code text.
    :return: list of str.
    """
    return [line for _number, line in clean_lines_numbered(text)]


def display_lines(lines, units, arc_segment, decimals, split_for_display):
    """
    The lines for the 3D display. In the mode "send" for arcs, the stream holds the arcs: a copy is split
    for the display only, and each display line maps to its stream line.

    :param split_for_display: False when the stream lines are the display lines already
    :return: (lines, stream_index, chord_lines). stream_index[i] is the stream line index of lines[i]
        (None: lines[i] is the stream line i). chord_lines: the stream line indexes of the arcs that
        could not be split: the display draws their chords in grey.
    """
    if not split_for_display:
        return lines, None, ()
    lines, _count, source_index, unsplit = split_arcs(list(lines), units, arc_segment, decimals=decimals)
    return lines, source_index, unsplit


def restart_preamble(lines, start_line, travel_z, units, decimals=4, spindle_dwell=0.0):
    """Restore the state BEFORE a 1-based console line, without replaying skipped moves.

    Only deterministic GRBL modes are restored. Probes, tool changes, offset writes,
    expressions and controller commands in the skipped section require Beginning.
    Travel Z is in the CNCJob's units; the preamble uses the program's active units.

    :return: (preparation lines, resumed lines). The resumed lines keep their row count;
        an inherited arc mode is made explicit on its first motion block.
    """
    if not 1 <= start_line <= len(lines):
        raise ValueError(_("Select a line within the program."))
    if start_line == 1:
        return [], list(lines)
    if travel_z is None or not math.isfinite(travel_z) or travel_z <= 0:
        raise ValueError(_("The CNCJob needs a finite, positive travel Z to start from a line."))
    if units not in ('MM', 'IN'):
        raise ValueError(_("The CNCJob units are not known."))
    state = _ModalState()
    state.units = 20 if units == 'IN' else 21
    plane, wcs = None, None
    spindle, speed, feed = 5, 0.0, None
    coolant = set()
    tool = None
    offset, offset_z = None, 0.0
    allowed_g = {0, 1, 2, 3, 4, 17, 18, 19, 20, 21, 40, 43.1, 49,
                 54, 55, 56, 57, 58, 59, 61, 80, 90, 91, 91.1, 94}
    for number, raw in enumerate(lines[:start_line - 1], 1):
        line = _COMMENT_PAREN_RE.sub('', raw).split(';', 1)[0].strip().upper()
        words = _words(line)
        if (not words or re.sub(r'\s', '', _WORD_RE.sub('', line)) or
                any(not math.isfinite(v) or k not in 'NGXYZIJKRFSPMT' for k, v in words) or
                any(k == 'G' and v not in allowed_g for k, v in words) or
                any(k == 'M' and v not in (0, 1, 3, 4, 5, 7, 8, 9) for k, v in words)):
            raise ValueError(_("Cannot reconstruct the state at program line %d. Start from Beginning.") % number)
        # G91.1 is GRBL's incremental arc-centre mode; it doesn't affect endpoint tracking.
        state.advance(' '.join('%s%s' % (k, v) for k, v in words if (k, v) != ('G', 91.1)))
        for k, value in words:
            if k == 'G':
                if value in (17, 18, 19):
                    plane = int(value)
                elif 54 <= value <= 59:
                    wcs = int(value)
                elif value == 49:
                    offset = 'G49'
                elif value == 43.1:
                    z = dict(words).get('Z')
                    if z is None:
                        raise ValueError(_("The tool length offset has no Z value at line %d.") % number)
                    offset = 'G43.1'
                    offset_z = z * (25.4 if state.units == 20 else 1.0)
            elif k == 'F':
                feed = value * (25.4 if state.units == 20 else 1.0)
            elif k == 'S':
                speed = value
            elif k == 'T':
                tool = value
            elif k == 'M':
                if value in (3, 4, 5):
                    spindle = int(value)
                elif value in (7, 8):
                    coolant.add(int(value))
                elif value == 9:
                    coolant.clear()
    if state.dist not in (90, 91) or state.motion not in (0, 1, 2, 3, None):
        raise ValueError(_("The motion mode before the selected line is not known."))
    if any(v is None or not math.isfinite(v) for v in state.pos.values()):
        raise ValueError(_("The X, Y and Z position before the selected line is not known. Start from Beginning."))
    scale = 25.4 if state.units == 20 else 1.0
    travel = travel_z * (25.4 if units == 'IN' else 1.0) / scale
    x, y, z = (float(state.pos[k]) for k in 'XYZ')
    # Never lower to travel Z if the preceding position was above it.
    clearance = max(travel, z)
    if (feed is None and z < clearance) or (feed is not None and feed <= 0):
        raise ValueError(_("A positive feed rate is needed before the selected line."))
    if speed < 0 or (spindle in (3, 4) and speed <= 0):
        raise ValueError(_("A positive spindle speed is needed before the selected line."))
    out = ['M5', 'M9', 'G%d' % state.units, 'G90', 'G94', 'G91.1', 'G40', 'G61']
    if plane is not None:
        out.append('G%d' % plane)
    if wcs is not None:
        out.append('G%d' % wcs)
    if offset is not None:
        out.append(offset + (' Z%s' % _fmt(offset_z / scale, decimals) if offset == 'G43.1' else ''))
    if tool is not None:
        out.append('T%s' % _fmt(tool, 0))
    out.extend(['G0 Z%s' % _fmt(clearance, decimals),
                'G0 X%s Y%s' % (_fmt(x, decimals), _fmt(y, decimals))])
    out.append('M%d S%s' % (spindle, _fmt(speed, decimals)))
    if spindle in (3, 4) and spindle_dwell > 0:
        # G4 P is in seconds; '%g' gives 3 and 1.5, never an exponent for 0..60
        out.append('G4 P%g' % spindle_dwell)
    out.extend('M%d' % m for m in sorted(coolant))
    if feed is not None:
        out.append('G1 Z%s F%s' % (_fmt(z, decimals), _fmt(feed / scale, decimals)))
    out.append('G%d' % state.dist)
    resumed = list(lines[start_line - 1:])
    if state.motion in (2, 3):
        # GRBL rejects a bare G2/G3. Restore it on the first inherited motion instead.
        for index, line in enumerate(resumed):
            words = _words(line)
            g_codes = {v for k, v in words if k == 'G'}
            if g_codes & {0, 1, 2, 3, 38.2, 38.3, 38.4, 38.5, 80}:
                break
            if (any(k in 'XYZ' for k, v in words) and
                    not g_codes & {10, 28, 30, 43.1, 49, 92}):
                resumed[index] = 'G%d %s' % (state.motion, line)
                break
    else:
        out.append('G%d' % state.motion if state.motion is not None else 'G80')
    return out, resumed


def needs_segmentation(target_obj):
    """:return: True if the G-code of the CNCJob was not made with segmented lines."""
    return getattr(target_obj, 'is_segmented_gcode', False) is not True


def job_proxy(target_obj, lines):
    """
    A stand-in for the CNCJob that LevellingCoreMixin.autolevell_gcode() can level:
    1 tool with the stream text.
    """
    return SimpleNamespace(
        tools={'job': {'gcode': '\n'.join(lines) + '\n'}},
        units=target_obj.units,
        coords_decimals=target_obj.coords_decimals,
        seg_x=getattr(target_obj, 'seg_x', 0) or 0,
        seg_y=getattr(target_obj, 'seg_y', 0) or 0,
        obj_options=target_obj.obj_options,
    )


@dataclasses.dataclass
class _ProfileOutcome:
    """The result of the machine profile computation of a stream (a worker): nothing here touches the GUI."""
    out: str = None             # the corrected text
    result: object = None       # the CheckResult
    refused: object = None      # the CorrectionRefused, if the correction was refused
    setup: object = None        # the Setup of the check (the GUI thread stores it)
    setup_key: object = None    # the key of the live state, or None


@dataclasses.dataclass
class _StartInputs:
    """What a Start reads from the widgets and the options (GUI thread), for the worker of the preparation."""
    op: object                  # the reserved stream operation
    target: object
    source: str                 # the G-code text of the target
    check_mode: bool
    autolevel: bool
    arc_mode: str               # 'split' or 'send'
    arc_segment: float
    units: str
    decimals: int
    profile: object             # the active machine profile, or None
    poll_ms: int
    seq: int                    # the start sequence number
    generation: int             # the safety generation
    level: object = None        # a _LevelInput for a levelled job, else None


@dataclasses.dataclass
class _LevelInput:
    """What a levelled job reads from the widgets, the height map and the options (GUI thread)."""
    method: str                 # 'v', 'b' or 'c'
    snapshot: tuple             # (points, units, tolerance) of the height map
    seg_len: float              # the longest piece of a split G1 move
    home_wcs: float = None      # the WCS of the bound map frame as a G number
    keep_markers: bool = False  # the marker lines of the fixed sequences stay (an active profile)
    generation: int = None      # the generation of the height map
    request: object = None      # the _StartRequest of the Start, or None


@dataclasses.dataclass
class _PreparedJob:
    """The result of prepare_job() and prepare_levelled_job()."""
    diagnostics: list = dataclasses.field(default_factory=list)
    inputs: _StartInputs = None
    lines: list = dataclasses.field(default_factory=list)
    numbers: list = dataclasses.field(default_factory=list)
    line_map: list = dataclasses.field(default_factory=list)
    error: str = None
    profile: _ProfileOutcome = None
    split_count: int = 0


@dataclasses.dataclass
class _PreparedRestart:
    """The result of prepare_restart(); `context` is for the GUI slot."""
    preamble: list = dataclasses.field(default_factory=list)
    resumed: list = dataclasses.field(default_factory=list)
    error: str = None
    outcome: _ProfileOutcome = None
    context: object = None


def prepare_job(inputs, profile_compute):
    """
    The full-file steps of a Start, with no GUI call (a worker): the machine profile computation, the clean, the
    line check and the arc split.

    :param inputs: _StartInputs
    :param profile_compute: callable(text, target, profile, op, line_map) -> _ProfileOutcome
    :return: _PreparedJob. A refused correction leaves no lines: the GUI thread tells the user.
    """
    prepared = _PreparedJob(inputs=inputs)
    text = inputs.source
    if inputs.profile is not None and inputs.source.strip():
        outcome = prepared.profile = profile_compute(inputs.source, inputs.target, inputs.profile, inputs.op,
                                                     prepared.line_map)
        if outcome.refused is not None:
            return prepared
        text = outcome.out
    numbered = clean_lines_numbered(text)
    lines = [line for _number, line in numbered]
    bad_index = validate_stream_lines(lines)
    if bad_index is not None:
        prepared.error = _("Line %d of the G-code is longer than GRBL accepts. The job was not made.") % \
                         numbered[bad_index][0]
        return prepared
    numbers = [number for number, _line in numbered]
    if prepared.line_map:
        # the corrected text has other lines than the source: each line maps to the line that it comes from
        line_map = prepared.line_map
        numbers = [line_map[number - 1] if 0 < number <= len(line_map) else number for number in numbers]
    elif text != inputs.source:
        numbers = []
    if inputs.arc_mode == 'split':
        lines, count, source_index, unsplit = split_arcs(lines, inputs.units, inputs.arc_segment,
                                                         decimals=inputs.decimals)
        if unsplit:
            prepared.error = _("This arc can not be split (plane, frame or position not known). Line %d.") % (
                numbers[unsplit[0]] if numbers else unsplit[0] + 1)
            return prepared
        if numbers:
            numbers = [numbers[i] for i in source_index]
        prepared.split_count = count
    prepared.lines = lines
    prepared.numbers = numbers
    return prepared


def prepare_levelled_job(inputs, profile_compute, autolevell, tlo_line):
    """
    The full-file steps of a levelled Start, with no widget access (a worker): clean, the tool length check,
    the segmentation, the levelling, the machine profile computation, clean and line check.

    :param inputs: _StartInputs with `level` set
    :param profile_compute: callable(text, target, profile, op, line_map) -> _ProfileOutcome
    :param autolevell: callable(proxy, method, snapshot=, diagnostics=, home_wcs=, strict=) -> {tooluid: text} | None
    :param tlo_line: callable(lines) -> index of the first tool length offset line, or None
    :return: _PreparedJob. An empty `error` means that the levelling told the user already.
    """
    level = inputs.level
    prepared = _PreparedJob(inputs=inputs)
    target = inputs.target
    numbered = clean_lines_numbered(inputs.source, level.keep_markers)
    lines = [line for _number, line in numbered]
    bad_index = validate_stream_lines(lines)
    if bad_index is not None:
        prepared.error = _("Line %d of the G-code is longer than GRBL accepts. The job was not made.") % \
                         numbered[bad_index][0]
        return prepared
    tlo_index = tlo_line(lines)
    if tlo_index is not None:
        prepared.error = (_("The program changes the tool length offset (line %d). A levelled job can not do this. "
                            "Remove it, or make a new height map after the change.") % numbered[tlo_index][0])
        return prepared
    if needs_segmentation(target):
        lines, count = split_long_moves(lines, level.seg_len, decimals=target.coords_decimals)
        if count:
            prepared.diagnostics.append('%s: %d' % (_("Long G1 moves split for the levelling"), count))
    result = autolevell(job_proxy(target, lines), level.method, snapshot=level.snapshot,
                        diagnostics=prepared.diagnostics, home_wcs=level.home_wcs, strict=True)
    if result is None:
        prepared.error = ''
        return prepared
    if inputs.profile is None:
        job_lines = [line for line in result['job'].splitlines() if line.strip()]
    else:
        outcome = prepared.profile = profile_compute(result['job'], target, inputs.profile, inputs.op, None)
        if outcome.refused is not None:
            return prepared
        job_lines = [line for _number, line in clean_lines_numbered(outcome.out)]
    bad_index = validate_stream_lines(job_lines)
    if bad_index is not None:
        prepared.error = _("Line %d of the levelled G-code is longer than GRBL accepts. The job was not made.") % \
                         (bad_index + 1)
        return prepared
    prepared.lines = job_lines
    return prepared


def prepare_restart(lines, start_line, travel_z, units, decimals, profile_compute=None, spindle_dwell=0.0):
    """
    The restart preamble of a start from a line and its profile computation (a worker).

    :param profile_compute: callable(text) -> _ProfileOutcome for the preamble and the resumed part, or None
    :param spindle_dwell: seconds to wait after the spindle start (G4 P), 0 for none
    :return: _PreparedRestart
    """
    try:
        preamble, resumed = restart_preamble(lines, start_line, travel_z, units, decimals,
                                                  spindle_dwell)
        if validate_stream_lines(preamble + resumed) is not None:
            raise ValueError(_("The restart preparation exceeds the GRBL line length limit."))
    except ValueError as err:
        return _PreparedRestart(error=str(err))
    outcome = None if profile_compute is None else profile_compute('\n'.join(preamble + resumed) + '\n')
    return _PreparedRestart(preamble=preamble, resumed=resumed, outcome=outcome)
