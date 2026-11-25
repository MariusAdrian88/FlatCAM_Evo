# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Motion interpreter of the Machine Profiles feature: G-code text + Setup -> list of MotionRecord.

It follows the modal state of the "Check" column of CONTRACTS.md section 1 (see the package docstring for the
frames). W = M - (active WCS offset + G92 + TLO). Every length in a record is in mm. A value that is not known
stays None; a record whose M start or M end is not complete, or that holds a word outside the matrix, is
UNVERIFIED and keeps the reason in `unverified_reason`.

Only the word split of `appParsers/ParseGCode.py` is reused (comment strip, word regex, tool diameter comments).
No Qt, no Sender/E-Cut/Levelling import.

Conventions:
- `Setup.start` is a position in M.
- `arc_center` is in M (x, y, and the Z of the start of the arc).
- A record is verified only when start_m and end_m are complete (X, Y and Z).
- The dialect is not used, except that GRBL_11 with G90.1 is UNVERIFIED (CONTRACTS section 1).
"""

import math
import re
from typing import Dict, List, Optional, Tuple

from appParsers.ParseGCode import CHANGE_DIA_RE, COMMENT_RE, COMMENT_TEXT_RE, HEADER_DIA_RE, WORD_RE
from appCommon.machine_profile.records import Frame, MotionRecord, Setup

MM_PER_IN = 25.4
TWO_PI = 2.0 * math.pi
AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}
AXIS_NAME = 'XYZ'
WCS_IDS = {54.0: 'G54', 55.0: 'G55', 56.0: 'G56', 57.0: 'G57', 58.0: 'G58', 59.0: 'G59'}
KNOWN_LETTERS = 'GMXYZIJKRFSTPLN'
EXPR_AXIS_RE = re.compile(r'([XYZ])\s*[#\[]')
CANNED = frozenset((73.0, 74.0, 76.0) + tuple(float(x) for x in range(81, 90)))
PROBES = frozenset((38.2, 38.3, 38.4, 38.5))
SUPPORTED = frozenset((0.0, 1.0, 2.0, 3.0, 4.0, 10.0, 17.0, 18.0, 19.0, 20.0, 21.0, 28.0, 30.0, 43.1, 49.0, 53.0,
                       90.0, 91.0, 90.1, 91.1, 92.0, 92.1, 94.0)) | PROBES | frozenset(WCS_IDS)


def _label(value: float) -> str:
    return 'G%d' % value if value == int(value) else 'G%g' % value


class _State:
    def __init__(self, setup: Setup):
        self.units = setup.units
        self.dist = setup.distance_mode
        self.arc_mode = setup.arc_mode
        self.plane = setup.plane
        self.mode = setup.motion_mode
        self.dialect = setup.dialect
        self.wcs: Dict[str, list] = {k: list(v) for k, v in (setup.wcs_offsets or {}).items()}
        self.active = setup.active_wcs
        self.g92 = list(setup.g92) if setup.g92 is not None else [None, None, None]
        self.tlo = setup.tlo
        self.m = list(setup.start) if setup.start is not None else [None, None, None]
        self.w = [None, None, None]
        self.feed: Optional[float] = None
        self.s_val: Optional[float] = None
        self.spindle = False
        self.tool: Optional[int] = None
        self.tool_next: Optional[int] = None
        self.dia: Optional[float] = None
        self.pending: List[str] = []
        self.sticky: Dict[str, str] = {}
        self.recompute_w()

    # ---- units and offsets
    @property
    def f(self) -> Optional[float]:
        return {'mm': 1.0, 'inch': MM_PER_IN}.get(self.units)

    def off(self, a: int) -> Optional[float]:
        slot = self.wcs.get(self.active) if self.active else None
        base = slot[a] if slot is not None else None
        g = self.g92[a]
        t = self.tlo if a == 2 else 0.0
        if base is None or g is None or t is None:
            return None
        return base + g + t

    def sync(self):
        for a in range(3):
            off = self.off(a)
            if off is None:
                continue
            if self.m[a] is None and self.w[a] is not None:
                self.m[a] = self.w[a] + off
            elif self.w[a] is None and self.m[a] is not None:
                self.w[a] = self.m[a] - off

    def recompute_w(self):
        for a in range(3):
            off = self.off(a)
            self.w[a] = None if off is None or self.m[a] is None else self.m[a] - off

    def frame(self) -> Frame:
        slot = self.wcs.get(self.active) if self.active else None
        return Frame(wcs_id=self.active, wcs_offset=tuple(slot) if slot is not None else None,
                     g92=tuple(self.g92), tlo=self.tlo, units=self.units)

    def slot(self, wcs_id: str) -> list:
        return self.wcs.setdefault(wcs_id, [None, None, None])


def _complete(p) -> Optional[Tuple[float, float, float]]:
    return tuple(p) if all(v is not None for v in p) else None


def _unknown_text(p, what: str, state: _State) -> str:
    axes = [AXIS_NAME[a] for a in range(3) if p[a] is None]
    text = '%s position unknown (%s)' % (what, ','.join(axes))
    no_offset = [AXIS_NAME[a] for a in range(3) if p[a] is None and state.off(a) is None]
    if no_offset:
        text += '; offset unknown (%s)' % ','.join(no_offset)
    return text


class _Interpreter:
    def __init__(self, setup: Setup):
        self.s = _State(setup)
        self.records: List[MotionRecord] = []

    # ------------------------------------------------------------ lexer
    def run(self, text: str) -> List[MotionRecord]:
        for number, raw in enumerate(text.splitlines(), start=1):
            if raw.lstrip()[:1] == '%':
                continue
            for m in COMMENT_TEXT_RE.finditer(raw):
                self._comment((m.group(1) if m.group(1) is not None else m.group(2)).upper())
            code = COMMENT_RE.sub(' ', raw).upper()
            words = [(letter, float(value)) for letter, value in WORD_RE.findall(code)]
            leftover = WORD_RE.sub(' ', code).strip()
            if not words and not leftover:
                continue
            self._block(number, raw, words, leftover, code)
        return self.records

    def _comment(self, up: str):
        s = self.s
        m = HEADER_DIA_RE.match(up)
        if m:
            factor = {'MM': 1.0, 'IN': MM_PER_IN}.get(m.group(2), s.f)
            s.dia = float(m.group(1)) * factor if factor else None
            return
        m = CHANGE_DIA_RE.search(up)
        if m:
            s.dia = float(m.group(1)) * s.f if s.f else None

    # ------------------------------------------------------------ one block
    def _block(self, number, raw, words, leftover, code):
        s = self.s
        g, mcodes, vals, ax = [], [], {}, {}
        for letter, v in words:
            if letter == 'G':
                g.append(round(v, 2))
            elif letter == 'M':
                mcodes.append(round(v))
            elif letter in KNOWN_LETTERS:
                vals[letter] = v
                if letter in AXIS_INDEX:
                    ax[AXIS_INDEX[letter]] = v
            else:
                s.pending.append('word %s%g is outside the supported set' % (letter, v))
        unk = set()
        if leftover:
            s.pending.append('expression or unreadable text')
            unk = {AXIS_INDEX[a] for a in EXPR_AXIS_RE.findall(code)}
            for a in unk:
                ax.pop(a, None)

        if 'S' in vals:
            s.s_val = vals['S']
        if 'T' in vals:
            s.tool_next = int(vals['T'])
        for mc in mcodes:
            if mc == 6:
                if s.tool_next is None:
                    s.pending.append('M6 without a T word')
                else:
                    s.tool = s.tool_next
            elif mc in (3, 4):
                s.spindle = True
            elif mc == 5:
                s.spindle = False
            elif mc not in (0, 2, 30):
                s.pending.append('word M%d is outside the supported set' % mc)

        consumer = None
        motion = None
        g53 = False
        for x in g:
            if x == 20.0:
                s.units = 'inch'
            elif x == 21.0:
                s.units = 'mm'
            elif x in (17.0, 18.0, 19.0):
                s.plane = _label(x)
            elif x in (90.0, 91.0):
                s.dist = _label(x)
            elif x in (90.1, 91.1):
                s.arc_mode = _label(x)
            elif x == 94.0:
                s.sticky.pop('G93', None)
            elif x == 93.0:
                s.sticky['G93'] = 'G93 inverse time feed is not verified'
            elif x == 4.0:
                pass
            elif x in WCS_IDS:
                s.sync()
                s.active = WCS_IDS[x]
                s.recompute_w()
            elif x == 53.0:
                g53 = True
            elif x in (0.0, 1.0, 2.0, 3.0) or x in PROBES:
                motion = _label(x)
                s.mode = motion
            elif x in (10.0, 92.0, 92.1, 43.1, 49.0, 28.0, 30.0):
                consumer = x
            elif x in CANNED:
                s.mode = 'unsupported ' + _label(x)
                s.pending.append('%s canned cycle is not verified' % _label(x))
            elif x == 80.0:
                s.mode = None
                s.pending.append('G80 is not verified')
            elif x == 43.0:
                s.sync()
                s.tlo = None
                s.recompute_w()
                s.pending.append('G43 uses the tool table (not verified)')
            else:
                s.pending.append('word %s is outside the supported set' % _label(x))

        # the F word is in the units of its block: a G20/G21 of the same block applies first
        if 'F' in vals:
            s.feed = vals['F'] * s.f if s.f else None

        has_arc_words = any(k in vals for k in 'IJKR')
        if consumer is not None:
            self._consume(number, raw, consumer, vals, ax, unk)
            return
        if ax or unk or (has_arc_words and s.mode in ('G2', 'G3')):
            self._motion(number, raw, s.mode, vals, ax, unk, g53=g53)
        elif has_arc_words or (g53 and not ax):
            s.pending.append('arc word without an arc move')

    # ------------------------------------------------------------ consumers of axis words
    def _consume(self, number, raw, x, vals, ax, unk):
        s = self.s
        f = s.f
        s.sync()
        if x == 92.1:
            s.g92 = [0.0, 0.0, 0.0]
            s.recompute_w()
        elif x == 49.0:
            s.tlo = 0.0
            s.recompute_w()
        elif x == 43.1:
            if set(ax) - {2} or 2 not in ax:
                s.pending.append('G43.1 needs a Z word only')
            s.tlo = ax[2] * f if (2 in ax and f) else None
            s.recompute_w()
        elif x == 92.0:
            if not ax and not unk:
                s.pending.append('G92 without axis words')
            for a in range(3):
                if a not in ax and a not in unk:
                    continue
                slot = s.wcs.get(s.active) if s.active else None
                base = slot[a] if slot is not None else None
                t = s.tlo if a == 2 else 0.0
                if a in unk or f is None or base is None or t is None or s.m[a] is None:
                    s.g92[a] = None
                else:
                    s.g92[a] = s.m[a] - base - t - ax[a] * f
                s.w[a] = ax[a] * f if (a in ax and f) else None
        elif x == 10.0:
            self._g10(vals, ax, unk)
        else:
            self._home(number, raw, 'G28' if x == 28.0 else 'G30', vals, ax, unk)

    def _g10(self, vals, ax, unk):
        s = self.s
        f = s.f
        level = vals.get('L')
        p = vals.get('P')
        if level not in (2.0, 20.0) or p is None or 'R' in vals:
            s.pending.append('G10 form is not verified')
            return
        p = int(p)
        wcs_id = s.active if p == 0 else (WCS_IDS.get(53.0 + p) if 1 <= p <= 6 else None)
        if wcs_id is None:
            s.pending.append('G10 P%d: WCS is unknown' % p)
            return
        slot = s.slot(wcs_id)
        for a in range(3):
            if a not in ax and a not in unk:
                continue
            if a in unk or f is None:
                slot[a] = None
            elif level == 2.0:
                slot[a] = ax[a] * f
            else:
                t = s.tlo if a == 2 else 0.0
                if s.m[a] is None or s.g92[a] is None or t is None:
                    slot[a] = None
                else:
                    slot[a] = s.m[a] - s.g92[a] - t - ax[a] * f
        s.recompute_w()

    def _home(self, number, raw, name, vals, ax, unk):
        s = self.s
        if ax or unk:
            self._motion(number, raw, 'G0', vals, ax, unk)
        s.sync()
        start_w, start_m = tuple(s.w), tuple(s.m)
        reason = '%s position is not stored in the profile' % name
        if _complete(start_m) is None:
            reason += '; start position unknown'
        self._emit(number, raw, name, start_w, (None, None, None), start_m, None, [reason])
        s.m = [None, None, None]
        s.w = [None, None, None]

    # ------------------------------------------------------------ motion
    def _motion(self, number, raw, mode, vals, ax, unk, g53=False):
        s = self.s
        f = s.f
        reasons: List[str] = []
        s.sync()
        start_w, start_m = tuple(s.w), tuple(s.m)
        probe = mode in PROBES_LABELS
        canned = mode is None or mode.startswith('unsupported')
        if canned:
            reasons.append('motion mode unknown' if mode is None else mode)
        elif g53 and mode not in ('G0', 'G1'):
            reasons.append('G53 with %s' % mode)
            canned = True
        if canned:
            end_w = end_m = (None, None, None)
            s.m = [None, None, None]
            s.w = [None, None, None]
            self._emit(number, raw, mode, start_w, end_w, start_m, None, reasons)
            return
        for a in range(3):
            if a in unk:
                s.m[a] = s.w[a] = None
                reasons.append('%s value is an expression' % AXIS_NAME[a])
            elif a in ax:
                if f is None:
                    s.m[a] = s.w[a] = None
                    reasons.append('units unknown')
                    continue
                v = ax[a] * f
                if g53:
                    s.m[a] = v
                    off = s.off(a)
                    s.w[a] = None if off is None else v - off
                elif s.dist == 'G90':
                    off = s.off(a)
                    s.w[a] = v
                    s.m[a] = None if off is None else v + off
                elif s.dist == 'G91':
                    s.m[a] = None if s.m[a] is None else s.m[a] + v
                    s.w[a] = None if s.w[a] is None else s.w[a] + v
                else:
                    s.m[a] = s.w[a] = None
                    reasons.append('distance mode unknown')
        s.sync()
        end_w, end_m = tuple(s.w), tuple(s.m)
        arc = None
        if mode in ('G2', 'G3'):
            arc = self._arc(mode, vals, start_m, end_m, reasons)
        if probe:
            for a in range(3):
                if a in ax or a in unk:
                    if start_m[a] is None or end_m[a] is None or end_m[a] != start_m[a]:
                        s.m[a] = s.w[a] = None
        self._emit(number, raw, mode, start_w, end_w, start_m, end_m, reasons, arc)

    def _arc(self, mode, vals, start_m, end_m, reasons):
        s = self.s
        f = s.f
        if s.plane != 'G17':
            reasons.append('arc in plane %s is not verified' % (s.plane or 'unknown'))
            return None
        if 'K' in vals:
            reasons.append('arc with K word is not verified')
            return None
        if f is None:
            reasons.append('units unknown')
            return None
        if _complete(start_m) is None or _complete(end_m) is None:
            return None
        sx, sy = start_m[0], start_m[1]
        ex, ey = end_m[0], end_m[1]
        coincident = math.hypot(ex - sx, ey - sy) < 1e-9
        cw = mode == 'G2'
        if 'R' in vals:
            if 'I' in vals or 'J' in vals:
                reasons.append('arc with R and I J words')
                return None
            if coincident:
                reasons.append('R arc with an end point equal to the start')
                return None
            r = vals['R'] * f
            d = math.hypot(ex - sx, ey - sy)
            h2 = r * r - d * d / 4.0
            if h2 < 0 and d / 2.0 - abs(r) > 0.01:
                reasons.append('R is smaller than half of the chord')
                return None
            h = math.sqrt(max(h2, 0.0))
            sign = 1.0 if cw else -1.0
            if r < 0:
                sign = -sign
            dx, dy = ex - sx, ey - sy
            cx = (sx + ex) / 2.0 + sign * h * dy / d
            cy = (sy + ey) / 2.0 - sign * h * dx / d
        elif 'I' in vals or 'J' in vals:
            i, j = vals.get('I', 0.0) * f, vals.get('J', 0.0) * f
            if s.arc_mode == 'G91.1':
                cx, cy = sx + i, sy + j
            elif s.arc_mode == 'G90.1':
                if s.dialect == 'GRBL_11':
                    reasons.append('G90.1 is not verified for GRBL_11')
                    return None
                ox, oy = s.off(0), s.off(1)
                if ox is None or oy is None:
                    reasons.append('offset unknown for the G90.1 center')
                    return None
                cx, cy = i + ox, j + oy
            else:
                reasons.append('arc distance mode unknown')
                return None
        else:
            reasons.append('arc without I J or R')
            return None
        r0, r1 = math.hypot(sx - cx, sy - cy), math.hypot(ex - cx, ey - cy)
        if arc_end_off_circle(r0, r1):
            reasons.append('the end point is not on the arc: radius %.4f at the start, %.4f at the end' % (r0, r1))
            return None
        a0 = math.atan2(sy - cy, sx - cx)
        a1 = math.atan2(ey - cy, ex - cx)
        extent = (a0 - a1) % TWO_PI if cw else (a1 - a0) % TWO_PI
        if coincident:
            extent = TWO_PI
        return (cx, cy, start_m[2]), 'cw' if cw else 'ccw', extent

    # ------------------------------------------------------------ record
    def _emit(self, number, raw, mode, start_w, end_w, start_m, end_m, reasons, arc=None):
        s = self.s
        reasons = list(reasons) + s.pending + list(s.sticky.values())
        s.pending = []
        sm = _complete(start_m)
        em = _complete(end_m) if end_m is not None else None
        if sm is None:
            reasons.append(_unknown_text(start_m, 'start', s))
        if end_m is not None and em is None:
            reasons.append(_unknown_text(end_m, 'end', s))
        if arc is not None and not reasons:
            center, direction, extent = arc
        else:
            center = direction = extent = None
        feed = None if 'G93' in s.sticky else s.feed
        self.records.append(MotionRecord(
            line=number, block=raw.strip(), start_w=tuple(start_w), end_w=tuple(end_w), frame=s.frame(),
            start_m=sm, end_m=em, motion_mode=mode, arc_center=center, arc_direction=direction,
            arc_extent=extent, feed=feed, s=s.s_val if s.spindle else None, tool=s.tool, tool_diameter=s.dia,
            unverified_reason='; '.join(dict.fromkeys(reasons)) if reasons else None,
            end_m_axes=None if end_m is None else tuple(end_m)))


PROBES_LABELS = frozenset(_label(x) for x in PROBES)


def arc_end_off_circle(r0: float, r1: float) -> bool:
    """
    True when the end point of an I/J arc is not on its circle (radii in mm), with the rule of GRBL (error 33): the
    radii differ by more than 0.005 mm and by more than 0.5 mm or 0.1 % of the radius. The controller refuses it.
    """
    delta = abs(r1 - r0)
    return delta > 0.005 and (delta > 0.5 or delta > 0.001 * r0)


def interpret(text: str, setup: Setup) -> List[MotionRecord]:
    """Walk the G-code text from the state of `setup` and return one MotionRecord for each motion block."""
    return _Interpreter(setup).run(text)
