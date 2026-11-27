# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Job time of the GRBL Sender: the duration of each stream line, the time that is left, the clock text."""

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
    :return: 1 value (s) per line, or None when no estimate is possible (a job with an A word, a line that is not read)
    """
    parts = line_parts(lines, rapid_mm_min, units)
    return None if parts is None else [feed_s + rapid_s + dwell_s for feed_s, rapid_s, dwell_s in parts]


def line_parts(lines, rapid_mm_min, units):
    """
    The duration of each stream line in 3 parts: the seconds that the feed override scales (G1/G2/G3, G93 blocks),
    the seconds that the rapid override scales (G0 rapids) and the seconds that no override scales (G4 dwell).

    :return: 1 (feed seconds, rapid seconds, dwell seconds) per line, or None when no estimate is possible
        (a job with an A word, a line that is not read)
    """
    state = _ModalState(pos=(0.0, 0.0, 0.0))
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
        info = state.advance(line)
        dwell_s = dwell_seconds(line)
        feed_s = 0.0
        rapid_s = 0.0
        if info is not None:
            if 'A' in info['letters']:
                return None
            for letter, value in info['words']:
                if letter == 'F':
                    feed = value
            # a native full circle has no axis word: its end is its start
            full_circle = state.motion in (2, 3) and any(letter in 'IJK' for letter, _value in info['words'])
            if (info['axes'] or full_circle) and not info['blocked'] and state.inverse \
                    and state.motion in (1, 2, 3):
                # G93: the block takes 60 / F seconds, with the F word of that block
                block_feed = [value for letter, value in info['words'] if letter == 'F']
                if block_feed and block_feed[-1] > 0.0:
                    feed_s += 60.0 / block_feed[-1]
            elif (info['axes'] or full_circle) and not info['blocked'] and state.motion in (0, 1, 2, 3):
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
                if length > 0.0 and rate > 0.0:
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
