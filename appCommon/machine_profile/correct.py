# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Machine Profiles: the correction of a G-code program (no Qt).

`correct_program(text, setup, C, decimals, tol)` maps the XY of every motion block from the design frame to the work
frame (W = C(D), see CONTRACTS.md section 8). Arcs become G01 chords, fixed sequences (`(MP:FIXED BEGIN <role>)` ...
`(MP:FIXED END)`) are not mapped, every other block keeps its text. A program that holds a form outside the "yes"
rows of CONTRACTS.md section 1 is refused as a whole (`CorrectionRefused`), never partly rewritten.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from appParsers.ParseGCode import COMMENT_RE, WORD_RE
from appCommon.machine_profile.calibrate import apply_matrix
from appCommon.machine_profile.motion import arc_end_off_circle, interpret
from appCommon.machine_profile.records import Setup

TWO_PI = 2.0 * math.pi
ALLOWED_LETTERS = 'GMXYZIJFSTP'
ALLOWED_G = frozenset((0.0, 1.0, 2.0, 3.0, 4.0, 17.0, 20.0, 21.0, 90.0, 91.1, 94.0))
ALLOWED_M = frozenset((0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 30))
UNITS_FACTOR = {'mm': 1.0, 'inch': 25.4}
MARKER_RE = re.compile(r'^\s*\(\s*MP:FIXED\s+(?:BEGIN\s+([A-Za-z_]+)|(END))\s*\)\s*$', re.IGNORECASE)
SAMPLES = 7
MAX_CHORDS = 20000


class CorrectionRefused(Exception):
    """The program holds something that the correction does not rewrite. Nothing was rewritten."""

    def __init__(self, line: int, word: str, reason: str):
        super().__init__('line %d, %s: %s' % (line, word, reason))
        self.line = line
        self.word = word
        self.reason = reason


@dataclass
class _Block:
    number: int
    body: str                       # the line without its line ending
    ending: str
    kind: str = 'other'             # 'other', 'begin', 'end', 'motion'
    fixed: bool = False
    mode: Optional[str] = None      # modal motion mode of the block (G0..G3)
    words: Dict[str, 're.Match'] = field(default_factory=dict)
    values: Dict[str, float] = field(default_factory=dict)
    gword: Optional['re.Match'] = None   # the motion G word of the block, if it has one
    f: Optional[float] = None       # mm per program unit


def _label(value: float) -> str:
    return 'G%d' % value if value == int(value) else 'G%g' % value


def _fmt(value: float, decimals: int) -> str:
    text = '%.*f' % (decimals, value)
    return text.lstrip('-') if float(text) == 0.0 else text


def _quant(value: float, decimals: int) -> float:
    return float(_fmt(value, decimals))


def _invert(c):
    det = c[0][0] * c[1][1] - c[0][1] * c[1][0]
    if abs(det) < 1e-12:
        raise CorrectionRefused(1, 'matrix', 'the correction matrix is singular')
    return [[c[1][1] / det, -c[0][1] / det], [-c[1][0] / det, c[0][0] / det]]


def _smin(c) -> float:
    fro = sum(c[i][j] ** 2 for i in range(2) for j in range(2))
    det = abs(c[0][0] * c[1][1] - c[0][1] * c[1][0])
    return (math.sqrt(fro + 2.0 * det) - math.sqrt(max(fro - 2.0 * det, 0.0))) / 2.0


def _split(raw: str):
    masked = COMMENT_RE.sub(lambda m: ' ' * len(m.group(0)), raw).upper()
    words = list(WORD_RE.finditer(masked))
    leftover = WORD_RE.sub(lambda m: ' ' * len(m.group(0)), masked).strip()
    return words, leftover


def _word_text(m) -> str:
    return m.group(1) + m.group(2)


# ------------------------------------------------------------------------ phase 1: forms and modal state
def _scan(text: str, setup: Setup) -> List[_Block]:
    mode = setup.motion_mode if setup.motion_mode in ('G0', 'G1', 'G2', 'G3') else None
    f = UNITS_FACTOR.get(setup.units)
    dist, arc_mode, plane = setup.distance_mode, setup.arc_mode, setup.plane
    blocks: List[_Block] = []
    fixed = False
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        body = line.rstrip('\r\n')
        block = _Block(number, body, line[len(body):], f=f)
        blocks.append(block)
        mk = MARKER_RE.match(body)
        if mk:
            if mk.group(2):
                if not fixed:
                    raise CorrectionRefused(number, 'MP:FIXED END', 'end of a fixed sequence without a begin')
                fixed = False
                block.kind = 'end'
            else:
                if fixed:
                    raise CorrectionRefused(number, 'MP:FIXED BEGIN', 'a fixed sequence inside a fixed sequence')
                fixed = True
                block.kind = 'begin'
            continue
        if body.lstrip()[:1] == '%':
            continue
        words, leftover = _split(body)
        block.fixed = fixed
        if leftover:
            raise CorrectionRefused(number, leftover.split()[0], 'expression or unreadable text')
        gs: List[Tuple[float, 're.Match']] = []
        for m in words:
            letter, value = m.group(1), float(m.group(2))
            if letter not in ALLOWED_LETTERS:
                raise CorrectionRefused(number, _word_text(m), 'word outside the supported set')
            if letter == 'G':
                value = round(value, 2)
                if value not in ALLOWED_G:
                    raise CorrectionRefused(number, _label(value), 'G word that the correction does not rewrite')
                gs.append((value, m))
            elif letter == 'M':
                if round(value) not in ALLOWED_M:
                    raise CorrectionRefused(number, 'M%d' % round(value), 'word outside the supported set')
            if letter in 'XYIJ' and letter in block.values:
                raise CorrectionRefused(number, _word_text(m), 'word repeated in one block')
            block.values[letter] = value
            block.words[letter] = m
        dwell = False
        for value, m in gs:
            if value in (20.0, 21.0):
                new_f = 1.0 if value == 21.0 else 25.4
                if f is not None and new_f != f:
                    raise CorrectionRefused(number, _label(value), 'units change inside the program')
                f = new_f
            elif value == 17.0:
                plane = 'G17'
            elif value == 90.0:
                dist = 'G90'
            elif value == 91.1:
                arc_mode = 'G91.1'
            elif value == 4.0:
                dwell = True
            elif value in (0.0, 1.0, 2.0, 3.0):
                mode = 'G%d' % value
                block.gword = m
        block.f = f
        block.mode = mode
        axes = [a for a in 'XYIJ' if a in block.values]
        if dwell:
            if any(a in block.values for a in 'XYZ'):
                raise CorrectionRefused(number, 'G4', 'dwell with axis words')
            continue
        arc = mode in ('G2', 'G3')
        if arc and ('Z' in block.values and not axes):
            raise CorrectionRefused(number, 'Z', 'a Z move in an arc mode is not rewritten')
        if not axes:
            continue
        first = block.words[axes[0]]
        if mode is None:
            raise CorrectionRefused(number, _word_text(first), 'motion mode unknown')
        if dist != 'G90':
            raise CorrectionRefused(number, _word_text(first), 'distance mode is not G90')
        if not arc:
            if 'I' in block.values or 'J' in block.values:
                raise CorrectionRefused(number, _word_text(first), 'arc word without an arc move')
        else:
            if 'Z' in block.values:
                raise CorrectionRefused(number, 'Z', 'helical arc is not rewritten')
            if plane != 'G17':
                raise CorrectionRefused(number, mode, 'arc plane is not G17')
            if arc_mode != 'G91.1':
                raise CorrectionRefused(number, mode, 'arc center mode is not G91.1')
            if f is None:
                raise CorrectionRefused(number, mode, 'units unknown')
            if 'I' not in block.values and 'J' not in block.values:
                raise CorrectionRefused(number, mode, 'arc without I J')
        block.kind = 'motion'
    if fixed:
        raise CorrectionRefused(len(blocks), 'MP:FIXED BEGIN', 'a fixed sequence without an end')
    return blocks


# ------------------------------------------------------------------------ edits
def _apply(body: str, edits: List[Tuple[int, int, str]]) -> str:
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        body = body[:start] + new + body[end:]
    return body


def _remove(body: str, m) -> Tuple[int, int, str]:
    start = m.start()
    while start > 0 and body[start - 1] in ' \t':
        start -= 1
    return start, m.end(), ''


def _set_xy(block: _Block, x_text: Optional[str], y_text: Optional[str]):
    """
    Edits that put the texts into the X and Y words of the block (None = leave that axis alone). A word that the
    block lacks is inserted next to the other one; when the block has neither, the texts come back as `orphans`.
    """
    edits: List[Tuple[int, int, str]] = []
    orphans: List[str] = []
    xm, ym = block.words.get('X'), block.words.get('Y')
    if xm is not None and x_text is not None:
        edits.append((xm.start(2), xm.end(2), x_text))
    if ym is not None and y_text is not None:
        edits.append((ym.start(2), ym.end(2), y_text))
    if xm is None and x_text is not None:
        if ym is not None:
            edits.append((ym.start(1), ym.start(1), 'X%s ' % x_text))
        else:
            orphans.append('X%s' % x_text)
    if ym is None and y_text is not None:
        if xm is not None:
            edits.append((xm.end(), xm.end(), ' Y%s' % y_text))
        else:
            orphans.append('Y%s' % y_text)
    return edits, orphans


# ------------------------------------------------------------------------ the correction
class _Corrector:
    def __init__(self, c, decimals: int, tol: float, starts):
        self.c = c
        self.inv = _invert(c)
        self.dec = decimals
        self.tol = tol
        self.starts = starts
        self.ready = False
        self.d: List[Optional[float]] = [None, None]
        self.w: List[Optional[float]] = [None, None]

    def q(self, v: float) -> float:
        return _quant(v, self.dec)

    def _init(self, block: _Block):
        self.ready = True
        start = self.starts.get(block.number)
        if start is None or block.f is None:
            return
        self.w = [None if v is None else self.q(v / block.f) for v in start[:2]]
        if None not in self.w:
            self.d = list(apply_matrix(self.inv, *self.w))

    def _set_d_from_w(self):
        if None in self.w:
            self.d = [None, None]
        else:
            self.d = list(apply_matrix(self.inv, *self.w))

    # ---- one motion block
    def run(self, block: _Block) -> str:
        if not self.ready and block.number in self.starts:
            self._init(block)
        if block.fixed:
            self._fixed(block)
            return block.body + block.ending
        if block.mode in ('G2', 'G3'):
            return self._arc(block)
        return self._linear(block)

    def _fixed(self, block: _Block):
        v = block.values
        if block.mode in ('G2', 'G3'):
            ex, ey = v.get('X'), v.get('Y')
            if ex is None and ey is None:
                return
        for a, key in ((0, 'X'), (1, 'Y')):
            if key in v:
                self.w[a] = v[key]
        self._set_d_from_w()

    def _linear(self, block: _Block) -> str:
        v = block.values
        d = []
        for a, key in ((0, 'X'), (1, 'Y')):
            value = v[key] if key in v else self.d[a]
            if value is None:
                raise CorrectionRefused(block.number, key, 'the position of the axis is unknown')
            d.append(value)
        wx, wy = apply_matrix(self.c, d[0], d[1])
        rx, ry = self.q(wx), self.q(wy)
        write_x = 'X' in v or self.w[0] is None or rx != self.w[0]
        write_y = 'Y' in v or self.w[1] is None or ry != self.w[1]
        edits, orphans = _set_xy(block, _fmt(rx, self.dec) if write_x else None,
                                 _fmt(ry, self.dec) if write_y else None)
        self.d, self.w = d, [rx, ry]
        if orphans:
            last = max(m.end() for m in block.words.values())
            edits.append((last, last, ''.join(' ' + o for o in orphans)))
        return _apply(block.body, edits) + block.ending

    # ---- arcs
    def _arc(self, block: _Block) -> str:
        v = block.values
        if None in self.d or None in self.w:
            raise CorrectionRefused(block.number, block.mode, 'the start position of the arc is unknown')
        f = block.f
        tol = self.tol / f
        bound = math.sqrt(2.0) * 0.5 * 10.0 ** (-self.dec)
        if bound > tol:
            raise CorrectionRefused(block.number, block.mode,
                                    'the output decimals (%d) are too few for the tolerance %g mm' % (self.dec, self.tol))
        sx, sy = self.d
        ex = v.get('X', sx)
        ey = v.get('Y', sy)
        cx, cy = sx + v.get('I', 0.0), sy + v.get('J', 0.0)
        r0, r1 = math.hypot(sx - cx, sy - cy), math.hypot(ex - cx, ey - cy)
        if r0 < 1e-9:
            raise CorrectionRefused(block.number, block.mode, 'arc with a zero radius')
        if arc_end_off_circle(r0 * f, r1 * f):
            raise CorrectionRefused(block.number, block.mode, 'the end point is not on the arc')
        cw = block.mode == 'G2'
        a0, a1 = math.atan2(sy - cy, sx - cx), math.atan2(ey - cy, ex - cx)
        extent = (a0 - a1) % TWO_PI if cw else (a1 - a0) % TWO_PI
        if math.hypot(ex - sx, ey - sy) < 1e-9:
            extent = TWO_PI

        def q_at(t):
            ang = a0 - t * extent if cw else a0 + t * extent
            r = r0 + (r1 - r0) * t
            return apply_matrix(self.c, cx + r * math.cos(ang), cy + r * math.sin(ang))

        start_w = tuple(self.w)
        smin = _smin(self.c)
        reff = max(r0, r1) * smin
        lo = 1
        if reff > tol / 2.0:
            theta = 2.0 * math.acos(max(-1.0, 1.0 - tol / reff))
            if theta > 0:
                lo = max(1, math.ceil(extent / theta) - 1)
        n = lo
        while True:
            pts = self._chords(n, q_at, start_w)
            if self._error(n, q_at, pts) <= tol:
                break
            n += 1
            if n > MAX_CHORDS:
                raise CorrectionRefused(block.number, block.mode, 'the tolerance cannot be met')
        lines = []
        for i, (px, py) in enumerate(pts[1:], start=1):
            if i == 1:
                lines.append(self._first_chord(block, px, py))
            else:
                lines.append('G01 X%s Y%s' % (_fmt(px, self.dec), _fmt(py, self.dec)))
        self.d = [ex, ey]
        self.w = [pts[-1][0], pts[-1][1]]
        nl = block.ending or '\n'
        return nl.join(lines) + block.ending

    def _chords(self, n, q_at, start_w):
        pts = [start_w]
        for i in range(1, n + 1):
            x, y = q_at(i / n)
            pts.append((self.q(x), self.q(y)))
        return pts

    @staticmethod
    def _error(n, q_at, pts) -> float:
        worst = 0.0
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            qx, qy = q_at((i + 1) / n)
            worst = max(worst, math.hypot(bx - qx, by - qy))
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            for k in range(1, SAMPLES + 1):
                px, py = q_at((i + k / (SAMPLES + 1.0)) / n)
                u = 0.0 if ll == 0.0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
                worst = max(worst, math.hypot(px - (ax + u * dx), py - (ay + u * dy)))
        return worst

    def _first_chord(self, block: _Block, px: float, py: float) -> str:
        body = block.body
        words = block.words
        ij = [words[a] for a in 'IJ' if a in words]
        edits, orphans = _set_xy(block, _fmt(px, self.dec), _fmt(py, self.dec))
        if orphans:
            # no X and no Y in the text: the XY words take the place of the first I or J word
            at = min(m.start() for m in ij)
            edits.append((at, at, ' '.join(orphans) + ' '))
        edits += [_remove(body, m) for m in ij]
        # last in the list, so that it lands first when another insertion sits at the same position
        if block.gword is not None:
            edits.append((block.gword.start(), block.gword.end(), 'G01'))
        else:
            first = min(m.start() for m in words.values())
            edits.append((first, first, 'G01 '))
        return _apply(body, edits)


def correct_program(text: str, setup: Setup, C, decimals: int, tol: float = 0.005,
                    line_map: Optional[List[int]] = None) -> str:
    """
    Rewrite a G-code program so that the machine cuts the design: the XY of every motion block is mapped by `C`
    (design -> work), arcs become chords (error <= tol mm after the transform and the rounding to `decimals`),
    fixed sequences and all non-motion blocks keep their text.

    :param line_map: optional out-parameter: a list that receives, for each line of the returned text, the 1-based
        number of the source line that it comes from (every chord of an arc gets the line of the arc).
    :raises CorrectionRefused: a form outside the supported set, or tolerance/decimals that cannot be met
    """
    blocks = _scan(text, setup)
    records = interpret(text, setup)
    starts = {r.line: r.start_w for r in records}
    corrector = _Corrector(C, decimals, tol, starts)
    out = []
    for block in blocks:
        if block.kind == 'motion':
            piece = corrector.run(block)
            out.append(piece)
            if line_map is not None:
                line_map.extend([block.number] * max(1, len(piece.splitlines())))
        else:
            if line_map is not None:
                line_map.append(block.number)
            if block.kind == 'end':
                corrector._set_d_from_w()
            elif not corrector.ready and block.number in starts:
                corrector._init(block)
            out.append(block.body + block.ending)
    return ''.join(out)
