# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/16/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Job preparation of the GRBL Sender: the lines that are streamed."""

import math
import re
import gettext
import builtins
from types import SimpleNamespace

from appPlugins.Common.gcode_arcs import split_arcs
from appPlugins.Common.gcode_segment import _ModalState, _WORD_RE, _words, _fmt

_COMMENT_PAREN_RE = re.compile(r'\([^)]*\)')
_ = builtins.__dict__.get('_', gettext.gettext)


def clean_lines_numbered(text):
    """
    Make the stream lines out of G-code text: remove the comments ('(...)' and
    ';...'), the '%' lines and the empty lines; strip the spaces.

    :param text: G-code text.
    :return: list of (source line number, str). The source line number is 1-based.
    """
    result = []
    for number, raw in enumerate((text or '').splitlines(), start=1):
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


def restart_preamble(lines, start_line, travel_z, units, decimals=4):
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
