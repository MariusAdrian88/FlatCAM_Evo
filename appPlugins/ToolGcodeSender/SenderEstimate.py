# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Job time of the GRBL Sender: the duration of each stream line, the time that is left, the clock text."""

import itertools
import math

from appPlugins.Common.gcode_segment import _ModalState, _COMMENT_RE, _words
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


def _arc_length(start, end, words, motion):
    """:return: the length of a G2/G3 arc in the XY plane, or 0.0 if it can not be told."""
    values = {}
    for letter, value in words:
        values[letter] = value
    if start['X'] is None or start['Y'] is None or end['X'] is None or end['Y'] is None:
        return 0.0
    chord = math.hypot(end['X'] - start['X'], end['Y'] - start['Y'])
    if 'I' in values or 'J' in values:
        cx = start['X'] + values.get('I', 0.0)
        cy = start['Y'] + values.get('J', 0.0)
        radius = math.hypot(start['X'] - cx, start['Y'] - cy)
        if radius == 0.0:
            return 0.0
        a0 = math.atan2(start['Y'] - cy, start['X'] - cx)
        a1 = math.atan2(end['Y'] - cy, end['X'] - cx)
        sweep = (a0 - a1) if motion == 2 else (a1 - a0)
        sweep %= 2.0 * math.pi
        if sweep < 1e-9:
            sweep = 2.0 * math.pi
        return radius * sweep
    if 'R' in values:
        radius = abs(values['R'])
        if radius == 0.0 or chord > 2.0 * radius + 1e-9:
            return 0.0
        angle = 2.0 * math.asin(min(1.0, chord / (2.0 * radius)))
        if values['R'] < 0.0:
            angle = 2.0 * math.pi - angle
        return radius * angle
    return 0.0


def line_seconds(lines, rapid_mm_min, units):
    """
    The duration of each stream line, with no acceleration model: length / feed. An unknown move counts 0.

    :param lines: the stream lines
    :param rapid_mm_min: the rapid rate (mm/min)
    :param units: 'MM' or 'IN', the units before the first G20/G21
    :return: 1 value (s) per line, or None when no estimate is possible (a job with an A word)
    """
    state = _ModalState(pos=(0.0, 0.0, 0.0))
    state.units = 20 if str(units).upper().startswith('IN') else 21
    feed = 0.0
    out = []
    for line in lines:
        masked = _COMMENT_RE.sub(' ', line).split(';')[0]
        new_units = None
        for letter, value in _words(masked):
            if letter == 'G' and value in (20.0, 21.0):
                new_units = int(value)
        if new_units is not None and new_units != state.units:
            # the known position changes its unit with the units
            factor = 25.4 if state.units == 20 else 1.0 / 25.4
            for axis, value in state.pos.items():
                if value is not None:
                    state.pos[axis] = value * factor
            feed *= factor              # the modal feed is in the units of the block that gave the F word
            state.units = new_units
        info = state.advance(line)
        seconds = dwell_seconds(line)
        if info is not None:
            if 'A' in info['letters']:
                return None
            for letter, value in info['words']:
                if letter == 'F':
                    feed = value
            # a native full circle has no axis word: its end is its start
            full_circle = state.motion in (2, 3) and any(letter in 'IJ' for letter, _value in info['words'])
            if (info['axes'] or full_circle) and not info['blocked'] and state.inverse \
                    and state.motion in (1, 2, 3):
                # G93: the block takes 60 / F seconds, with the F word of that block
                block_feed = [value for letter, value in info['words'] if letter == 'F']
                if block_feed and block_feed[-1] > 0.0:
                    seconds += 60.0 / block_feed[-1]
            elif (info['axes'] or full_circle) and not info['blocked'] and state.motion in (0, 1, 2, 3):
                start, end = info['start'], state.pos
                delta = [end[a] - start[a] for a in 'XYZ' if end[a] is not None and start[a] is not None]
                length = math.sqrt(sum(d * d for d in delta))
                if state.motion in (2, 3):
                    arc = _arc_length(start, end, info['words'], state.motion)
                    if arc:
                        dz = (end['Z'] - start['Z']) if end['Z'] is not None and start['Z'] is not None else 0.0
                        length = math.hypot(arc, dz)
                rate = feed
                if state.motion == 0:
                    rate = rapid_mm_min / 25.4 if state.units == 20 else rapid_mm_min
                if length > 0.0 and rate > 0.0:
                    seconds += length / rate * 60.0
        out.append(seconds)
    return out


def cumulative(seconds):
    """:return: the running total of `seconds` (None stays None)."""
    return None if seconds is None else list(itertools.accumulate(seconds))


def remaining(cumulative_list, done, feed_override_pct):
    """
    :param cumulative_list: the running total of the line durations
    :param done: the number of lines that are done
    :param feed_override_pct: the feed override (percent)
    :return: the seconds that are left, scaled by the feed override
    """
    if not cumulative_list:
        return 0.0
    total = cumulative_list[-1]
    spent = cumulative_list[min(max(done, 0), len(cumulative_list)) - 1] if done > 0 else 0.0
    pct = feed_override_pct if feed_override_pct and feed_override_pct > 0 else 100
    return max(total - spent, 0.0) * 100.0 / pct


def clock_text(seconds):
    """:return: 'hh:mm:ss', or '--:--:--' when `seconds` is None"""
    if seconds is None:
        return '--:--:--'
    seconds = int(round(max(seconds, 0.0)))
    return '%02d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60)
