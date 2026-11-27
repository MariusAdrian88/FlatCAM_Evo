# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Job time of the GRBL Sender: the duration of each stream line, the time that is left, the clock text."""

import dataclasses
import itertools
import math
import re

from appPlugins.Common.gcode_segment import _ModalState, _COMMENT_RE, _WORD_RE, _words
from appPlugins.Common.grbl.grbl_client import dwell_seconds

DEFAULT_RAPID_MM_MIN = 3000.0


def rapid_rate(settings=None, profile_rapid=None):
    """
    :param settings: the parsed `$$` values of the controller ({110: 5000.0, 111: 5000.0}), or None
    :param profile_rapid: `rapid_max` of the active machine profile (mm/min), or None
    :return: the rapid rate in mm/min: min($110, $111), else the profile, else 3000
    """
    rates = []
    for key in (110, 111):
        try:
            value = float((settings or {}).get(key))
        except (TypeError, ValueError):
            continue
        if value > 0.0:
            rates.append(value)
    if rates:
        return min(rates)
    if profile_rapid and profile_rapid > 0.0:
        return float(profile_rapid)
    return DEFAULT_RAPID_MM_MIN


# G17, G18, G19: the 2 axes of the plane (right-handed, G2 turns clockwise), their center words, the helix axis
_PLANES = {17: ('X', 'Y', 'I', 'J', 'Z'), 18: ('Z', 'X', 'K', 'I', 'Y'), 19: ('Y', 'Z', 'J', 'K', 'X')}


def _arc_length(start, end, words, motion, plane=17):
    """:return: the length of a G2/G3 arc in the plane G17/G18/G19 (no helix part), or 0.0 if it can not be told."""
    values = {}
    for letter, value in words:
        values[letter] = value
    turns = values.get('P', 1.0)
    if not math.isfinite(turns) or turns < 1 or turns != int(turns):
        return 0.0
    # As in the machine profile, each turn after the first adds a full circle.
    extra_sweep = (turns - 1) * math.tau
    first, second, first_word, second_word, _helix = _PLANES[plane]
    if start[first] is None or start[second] is None or end[first] is None or end[second] is None:
        return 0.0
    chord = math.hypot(end[first] - start[first], end[second] - start[second])
    if first_word in values or second_word in values:
        cx = start[first] + values.get(first_word, 0.0)
        cy = start[second] + values.get(second_word, 0.0)
        radius = math.hypot(start[first] - cx, start[second] - cy)
        if radius == 0.0:
            return 0.0
        a0 = math.atan2(start[second] - cy, start[first] - cx)
        a1 = math.atan2(end[second] - cy, end[first] - cx)
        sweep = (a0 - a1) if motion == 2 else (a1 - a0)
        sweep %= 2.0 * math.pi
        if sweep < 1e-9:
            sweep = 2.0 * math.pi
        return radius * (sweep + extra_sweep)
    if 'R' in values:
        radius = abs(values['R'])
        if radius == 0.0 or chord == 0.0 or chord > 2.0 * radius + 1e-9:
            return 0.0
        angle = 2.0 * math.asin(min(1.0, chord / (2.0 * radius)))
        if values['R'] < 0.0:
            angle = 2.0 * math.pi - angle
        return radius * (angle + extra_sweep)
    return 0.0


INCH_PER_MM = 0.0393701     # the constant of the grblHAL source (nuts_bolts.h)


@dataclasses.dataclass(frozen=True)
class ARule:
    """
    How the controller times a move with the rotary A axis (degrees, never scaled for G20).

    :param kind: 'ecut' (the E-Cut engine) or 'grblhal'
    :param a_max_deg_min: the highest A speed (deg/min): $113 of grblHAL, the A max velocity of the E-Cut
    :param a_rapid_deg_min: the A speed of a G0 (deg/min): the same value for both controllers
    :param rotary_fix: grblHAL only, `$701` bit 0: the G94 feed of a move that has XYZ and A measures the XYZ path
        (True), or the XYZ and A path together (False); None: not known
    :param revert: grblHAL only, `$701` bit 1: in G20 a move with A and no rotary fix takes F in in/min back to
        mm/min (True) or keeps F x 25.4 (False); None: not known
    :param xyz_counts_per_mm: E-Cut only: the board counts per mm of X, Y and Z. A G1 with A that changes no count
        of X, Y or Z is an A-only move for the engine (F in deg/min). None: any XYZ length counts.
    """
    kind: str
    a_max_deg_min: float
    a_rapid_deg_min: float
    rotary_fix: object = None
    revert: object = None
    xyz_counts_per_mm: object = None


def _a_move_seconds(motion, length_mm, d_a, feed_mm, a_feed, imperial, rule, rapid_mm_min, moves_xyz=None):
    """
    The seconds of a G0/G1/G2/G3 block (G94) that changes A by `d_a` degrees, with no acceleration model.

    :param length_mm: the XYZ path of the block (mm, the helix of an arc included)
    :param feed_mm: the modal F in mm/min (F x 25.4 when it was given in G20)
    :param a_feed: the modal F as it was written (deg/min for an A-only move of the E-Cut)
    :param moves_xyz: True when X, Y or Z moves; None: when the XYZ length is not 0
    :return: the seconds, or None when a setting that the rule needs is not known
    """
    if moves_xyz is None:
        moves_xyz = length_mm > 1e-9
    if motion == 0:
        seconds = d_a / rule.a_rapid_deg_min * 60.0
        if moves_xyz and rapid_mm_min > 0.0:
            seconds = max(seconds, length_mm / rapid_mm_min * 60.0)
        return seconds
    if rule.kind == 'ecut':
        # the engine: F is the speed along the XYZ path when X, Y or Z moves, else deg/min as written
        rate, path = (feed_mm, length_mm) if moves_xyz else (a_feed, d_a)
    else:
        if moves_xyz:
            if rule.rotary_fix is None:
                return None
            fixed = rule.rotary_fix
        else:
            fixed = False
        rate = feed_mm
        if not fixed and imperial:
            if rule.revert is None:
                return None
            if rule.revert:
                rate = feed_mm * INCH_PER_MM
        path = length_mm if fixed else math.hypot(length_mm, d_a)
    seconds = path / rate * 60.0 if rate > 0.0 else 0.0
    if seconds > 0.0:
        seconds = max(seconds, d_a / rule.a_max_deg_min * 60.0)
    return seconds


def line_seconds(lines, rapid_mm_min, units, a_rule=None):
    """
    The duration of each stream line, with no acceleration model: length / feed. An unknown move counts 0.

    :param lines: the stream lines
    :param rapid_mm_min: the rapid rate (mm/min)
    :param units: 'MM' or 'IN', the units before the first G20/G21
    :param a_rule: the ARule of the controller, or None when its rule for the A axis is not known
    :return: 1 value (s) per line, or None when no estimate is possible (a job with an A word and no `a_rule`, a
        setting that the rule needs is not known, a line that is not read)
    """
    parts = line_parts(lines, rapid_mm_min, units, a_rule)
    return None if parts is None else [feed_s + rapid_s + dwell_s for feed_s, rapid_s, dwell_s in parts]


def line_parts(lines, rapid_mm_min, units, a_rule=None):
    """
    The duration of each stream line in 3 parts: the seconds that the feed override scales (G1/G2/G3, G93 blocks),
    the seconds that the rapid override scales (G0 rapids) and the seconds that no override scales (G4 dwell).

    A job with an A word needs `a_rule`: the A axis is in degrees in G20 too (it is never scaled). The A start is 0
    like X, Y and Z. The block time is the XYZ path / F (the E-Cut, the grblHAL rotary fix), or the XYZ and A path
    together / F, or dA / F for an A-only move; A is also capped by its speed, and a G0 takes the longer of the
    XYZ time and the A time.

    :param a_rule: the ARule of the controller, or None
    :return: 1 (feed seconds, rapid seconds, dwell seconds) per line, or None when no estimate is possible
        (a job with an A word and no `a_rule`, a needed setting that is not known, a line that is not read)
    """
    state = _ModalState(pos=(0.0, 0.0, 0.0))
    state.a = 0.0
    a_feed = 0.0                        # the F word as it was written, for an A-only move of the E-Cut
    state.units = 20 if str(units).upper().startswith('IN') else 21
    feed = 0.0
    plane = 17
    out = []
    for raw in lines:
        # the controller reads lower case words: read them as the stream does
        line = _COMMENT_RE.sub(' ', raw).split(';')[0].upper()
        if not line.lstrip().startswith(('$', '/')) and re.sub(r'[\s%]', '', _WORD_RE.sub('', line)):
            return None
        masked = line
        new_units = None
        for letter, value in _words(masked):
            if letter == 'G' and value in (20.0, 21.0):
                new_units = int(value)
            elif letter == 'G' and value in (17.0, 18.0, 19.0):
                plane = int(value)
        if new_units is not None and new_units != state.units:
            # the known position changes its unit with the units
            factor = 25.4 if state.units == 20 else 1.0 / 25.4
            for axis, value in state.pos.items():
                if value is not None:
                    state.pos[axis] = value * factor
            feed *= factor              # the modal feed is in the units of the block that gave the F word
            state.units = new_units
        a_before = state.a
        info = state.advance(line)
        dwell_s = dwell_seconds(line)
        feed_s = 0.0
        rapid_s = 0.0
        if info is not None:
            d_a = 0.0
            if 'A' in info['letters']:
                if a_rule is None:
                    return None
                if a_before is not None and state.a is not None:
                    d_a = abs(state.a - a_before)
            for letter, value in info['words']:
                if letter == 'F':
                    feed = value
                    a_feed = value
            # a native full circle has no axis word: its end is its start
            full_circle = state.motion in (2, 3) and any(letter in 'IJK' for letter, _value in info['words'])
            moved = bool(info['axes']) or full_circle or d_a > 0.0
            if moved and not info['blocked'] and state.inverse and state.motion in (1, 2, 3):
                # G93: the block takes 60 / F seconds, with the F word of that block
                block_feed = [value for letter, value in info['words'] if letter == 'F']
                if block_feed and block_feed[-1] > 0.0:
                    feed_s += 60.0 / block_feed[-1]
                    if d_a > 0.0:
                        feed_s = max(feed_s, d_a / a_rule.a_max_deg_min * 60.0)
            elif moved and not info['blocked'] and state.motion in (0, 1, 2, 3):
                start, end = info['start'], state.pos
                delta = [end[a] - start[a] for a in 'XYZ' if end[a] is not None and start[a] is not None]
                length = math.sqrt(sum(d * d for d in delta))
                if state.motion in (2, 3):
                    arc = _arc_length(start, end, info['words'], state.motion, plane)
                    if arc:
                        helix = _PLANES[plane][4]       # the third axis of the plane
                        dh = (end[helix] - start[helix]) if end[helix] is not None and start[helix] is not None \
                            else 0.0
                        length = math.hypot(arc, dh)
                rate = feed
                if state.motion == 0:
                    rate = rapid_mm_min / 25.4 if state.units == 20 else rapid_mm_min
                if d_a > 0.0:
                    imperial = state.units == 20
                    moves_xyz = None
                    if a_rule.xyz_counts_per_mm is not None and state.motion == 1:
                        # the engine: a G1 that moves X, Y and Z by less than half a board count changes no count
                        # (the work offset is not known here: half a count is the threshold)
                        factor = 25.4 if imperial else 1.0
                        moves_xyz = any(abs(end[a] - start[a]) * factor * counts >= 0.5
                                        for a, counts in zip('XYZ', a_rule.xyz_counts_per_mm)
                                        if end[a] is not None and start[a] is not None)
                    seconds = _a_move_seconds(state.motion, length * (25.4 if imperial else 1.0), d_a,
                                              feed * (25.4 if imperial else 1.0), a_feed, imperial, a_rule,
                                              rapid_mm_min, moves_xyz)
                    if seconds is None:
                        return None
                    if state.motion == 0:
                        rapid_s += seconds
                    else:
                        feed_s += seconds
                elif length > 0.0 and rate > 0.0:
                    if state.motion == 0:
                        rapid_s += length / rate * 60.0     # the feed override does not change a rapid
                    else:
                        feed_s += length / rate * 60.0
        out.append((feed_s, rapid_s, dwell_s))
    return out


def cumulative(parts):
    """:return: the running totals (feed seconds, rapid seconds, dwell seconds) of the `line_parts` (None stays None)."""
    if parts is None:
        return None
    return list(zip(itertools.accumulate(feed_s for feed_s, _rapid, _dwell in parts),
                    itertools.accumulate(rapid_s for _feed, rapid_s, _dwell in parts),
                    itertools.accumulate(dwell_s for _feed, _rapid, dwell_s in parts)))


def remaining(cumulative_list, done, feed_override_pct, rapid_override_pct=100):
    """
    :param cumulative_list: the running totals (feed seconds, rapid seconds, dwell seconds) of the lines
    :param done: the number of lines that are done
    :param feed_override_pct: the feed override (percent), None or <= 0 counts 100
    :param rapid_override_pct: the rapid override (percent), None or <= 0 counts 100
    :return: the seconds that are left: the feed seconds are scaled by the feed override, the rapid seconds by the
        rapid override, the dwell is not scaled
    """
    if not cumulative_list:
        return 0.0
    index = min(max(done, 0), len(cumulative_list))
    spent_feed, spent_rapid, spent_dwell = cumulative_list[index - 1] if index > 0 else (0.0, 0.0, 0.0)
    feed_pct = feed_override_pct if feed_override_pct and feed_override_pct > 0 else 100
    rapid_pct = rapid_override_pct if rapid_override_pct and rapid_override_pct > 0 else 100
    feed_left = max(cumulative_list[-1][0] - spent_feed, 0.0)
    rapid_left = max(cumulative_list[-1][1] - spent_rapid, 0.0)
    dwell_left = max(cumulative_list[-1][2] - spent_dwell, 0.0)
    return feed_left * 100.0 / feed_pct + rapid_left * 100.0 / rapid_pct + dwell_left


def clock_text(seconds):
    """:return: 'hh:mm:ss', or '--:--:--' when `seconds` is None"""
    if seconds is None:
        return '--:--:--'
    seconds = int(round(max(seconds, 0.0)))
    return '%02d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60)
