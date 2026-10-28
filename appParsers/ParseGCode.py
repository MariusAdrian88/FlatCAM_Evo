# ############################################################
# FlatCAM: 2D Post-processing for Manufacturing              #
# http://flatcam.org                                         #
# File Author: Marius Adrian Stanciu (c)                     #
# Date: 10/25/2026                                           #
# MIT Licence                                                #
# ############################################################

"""
Generic G-code reader. Pure module (numpy + shapely): it turns the text of any G-code file into per-tool
geometry, drill hits, bounds, distances and metadata, without any knowledge of the application.

    split_tool_blocks(text) -> [{'tool', 'gcode'}]
    parse_gcode(text, ...)  -> dict, see the function docstring
"""

import bisect
import math
import re

import numpy as np
import shapely
from shapely import LineString, Point

# a word is a letter followed by a number; the letter must not be the end of a longer word
WORD_RE = re.compile(r'(?<![A-Z])([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))')
COMMENT_RE = re.compile(r'\([^)]*(?:\)|$)|;.*')
COMMENT_TEXT_RE = re.compile(r'\(([^)]*)(?:\)|$)|;(.*)')

TYPE_RE = re.compile(r'TYPE:\s*G-CODE FROM\s+(\w+)', re.I)
PREPROC_RE = re.compile(r'PREPROCESSOR\s+(?:GEOMETRY|EXCELLON)\s*:\s*(\S+)', re.I)
TOOL_DIA_RE = re.compile(r'\b(?:TOOL|T)\s*[:#]?\s*(\d+)\b.*?\b(?:DIAMETER|DIA|D)\s*[=:]?\s*(\d*\.?\d+)')
DIA_ONLY_RE = re.compile(r'^\s*(?:DIAMETER|DIA|D)\s*[=:]?\s*(\d*\.?\d+)\s*$')
TOOL_ZCUT_RE = re.compile(r'\bTOOL\s*[:#]?\s*(\d+)\s*->\s*Z_CUT\s*:\s*(-?\d*\.?\d+)')

MOTION = frozenset((0.0, 1.0, 2.0, 3.0))
CANNED = frozenset((73.0, 81.0, 82.0, 83.0, 85.0, 86.0, 89.0))
NO_MOVE = frozenset((4.0, 28.0, 30.0, 53.0))
TWO_PI = 2.0 * math.pi
MM_PER_IN = 25.4


def safe_comment_text(text):
    """
    Text for the inside of a '( ... )' comment that FlatCAM writes, like the object name in the header. The comment
    ends at the first ')' and can not be nested (GRBL, FluidNC, LinuxCNC, COMMENT_RE), so a name like 'board (1)'
    would end it early and the rest of the line would be read as G-code: '(' and ')' become '[' and ']'. A control
    character (a line break) would end the line: it becomes a space.
    """
    text = str(text).replace('(', '[').replace(')', ']')
    return ''.join(' ' if ch < ' ' or ch == '\x7f' else ch for ch in text)


def _split_lines(text):
    parts = text.split('\n')
    lines = [p + '\n' for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _prepare(text):
    """
    One pass over the text: lines (with their line endings), the words of every line (comments removed),
    the comments and the tool blocks.

    :return: lines, words, comments [(line index, text)], blocks [[first line, T number]], macro (True if a
             line holds a macro variable or an expression outside the comments)
    """
    lines = _split_lines(text)

    words = []
    comments = []
    blocks = [[0, None]]
    active = None
    macro = False
    for i, line in enumerate(lines):
        if '(' in line or ';' in line:
            for m in COMMENT_TEXT_RE.finditer(line):
                txt = m.group(1) if m.group(1) is not None else m.group(2)
                comments.append((i, txt))
            line = COMMENT_RE.sub(' ', line)
        if line.lstrip()[:1] == '%':
            words.append(())
            continue
        if '#' in line or '[' in line:
            macro = True
        found = WORD_RE.findall(line.upper())
        words.append(found)
        tool = None
        for letter, val in found:
            if letter == 'T':
                tool = int(float(val))
        if tool is not None and tool != active:
            if active is None:
                blocks[0][1] = tool
            else:
                blocks.append([i, tool])
            active = tool
    return lines, words, comments, blocks, macro


def split_tool_blocks(text):
    """
    Partition the text into tool blocks. A new block starts at the line that holds a T word (outside comments)
    whose value differs from the active tool. Lines before the first T word belong to the first block.

    :param text: the G-code
    :return: [{'tool': int | None, 'gcode': str}] in file order; the 'gcode' parts joined give back the text
    """
    lines, _, _, blocks, _ = _prepare(text)
    return _block_slices(lines, blocks)


# program flow outside the comments: subprogram calls/returns (Fanuc M98/M99, G65 macro call, LinuxCNC o-words)
SUBPROGRAM_RE = re.compile(r'\bM\s*0*9[89]\b|\bG\s*65\b|\bO\s*(?:\d+|<[^>]*>)\s+(?:SUB|CALL|ENDSUB|RETURN)\b')


def program_features(text):
    """
    What the program uses that is more than plain moves. A program with macros or subprograms can be read (see
    parse_gcode), but its text can not be rewritten word by word (transform, levelling).

    :param text: the G-code
    :return:     {'macros': bool, 'subprograms': bool}
    """
    macros = False
    subprograms = False
    for line in text.splitlines():
        if '(' in line or ';' in line:
            line = COMMENT_RE.sub(' ', line)
        if not macros and ('#' in line or '[' in line):
            macros = True
        if not subprograms and SUBPROGRAM_RE.search(line.upper()):
            subprograms = True
        if macros and subprograms:
            break
    return {'macros': macros, 'subprograms': subprograms}


def _block_slices(lines, blocks):
    out = []
    for n, (first, tool) in enumerate(blocks):
        last = blocks[n + 1][0] if n + 1 < len(blocks) else len(lines)
        out.append({'tool': tool, 'gcode': ''.join(lines[first:last])})
    return out


def _arc_centre_r(cw, sx, sy, ex, ey, r):
    """Centre of the arc given by its radius R (R < 0: the arc over 180 degrees); None for a zero chord."""
    dx, dy = ex - sx, ey - sy
    d = math.hypot(dx, dy)
    if d == 0.0:
        return None
    h = math.sqrt(max(r * r - d * d / 4.0, 0.0))
    ux, uy = dx / d, dy / d
    # clockwise: the centre is right of the chord for R > 0; counter-clockwise: left; R < 0 flips it
    side = -1.0 if cw else 1.0
    if r < 0:
        side = -side
    return sx + dx / 2.0 + side * h * -uy, sy + dy / 2.0 + side * h * ux


def _arc_points(cw, sx, sy, ex, ey, cx, cy, steps):
    """XY points (start excluded, end exact) of the arc around the centre; start == end is a full circle."""
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    sweep = a1 - a0
    if cw:
        if sweep > -1e-9:
            sweep -= TWO_PI
    elif sweep < 1e-9:
        sweep += TWO_PI
    r0 = math.hypot(sx - cx, sy - cy)
    r1 = math.hypot(ex - cx, ey - cy)
    n = max(1, math.ceil(steps * abs(sweep) / TWO_PI))
    k = np.arange(1, n + 1) / n
    ang = a0 + sweep * k
    rad = r0 + (r1 - r0) * k
    xs = cx + rad * np.cos(ang)
    ys = cy + rad * np.sin(ang)
    xs[-1], ys[-1] = ex, ey
    return list(zip(xs.tolist(), ys.tolist()))


# the plane of an arc: its 2 axes (in the order that gives the arc direction), their centre words, the linear axis
PLANE_AXES = {17: (0, 1, 'I', 'J', 2), 18: (2, 0, 'K', 'I', 1), 19: (1, 2, 'J', 'K', 0)}


def _arc_points_3d(cw, plane, s, t, words, arc_absolute, steps, off=(0.0, 0.0, 0.0)):
    """
    The arc in its plane (G17 XY with I J, G18 ZX with K I, G19 YZ with J K), its third axis linear (a helix).

    :param s, t:         start and end, 3 coordinates (None: not known)
    :param words:        {letter: value} of the I J K R words of the line
    :param arc_absolute: True for G90.1 (the centre words are a point, less the G92 offset 'off')
    :return:             3D points [x, y, z] (start excluded, end exact), or None when the arc can not be built
    """
    a, b, wa, wb, la = PLANE_AXES[plane]
    if s[a] is None or s[b] is None or t[a] is None or t[b] is None:
        return None
    sa, sb, ea, eb = s[a], s[b], t[a], t[b]
    if 'R' in words and wa not in words and wb not in words:
        c = _arc_centre_r(cw, sa, sb, ea, eb, words['R'])
        if c is None:
            return None
        ca, cb = c
    elif wa in words or wb in words:
        i, j = words.get(wa, 0.0), words.get(wb, 0.0)
        if arc_absolute:
            ca, cb = i - off[a], j - off[b]
        else:
            ca, cb = sa + i, sb + j
    else:
        return None

    sl = s[la] if s[la] is not None else 0.0
    el = t[la] if t[la] is not None else sl
    plane_pts = _arc_points(cw, sa, sb, ea, eb, ca, cb, steps)
    n = len(plane_pts)
    out = []
    for k, (pa, pb) in enumerate(plane_pts, start=1):
        p = [0.0, 0.0, 0.0]
        p[a], p[b], p[la] = pa, pb, sl + (el - sl) * k / n
        out.append(p)
    return out


class _Interpreter:
    """
    Walks the words of the lines, keeps the modal state and collects the moves per tool block.
    """

    def __init__(self, nblocks, steps):
        self.steps = max(steps, 4)
        self.acc = [self._new_acc() for _ in range(nblocks)]
        self.blk = 0
        self.items = []     # runs [kind, block, points, tag] and hits ['H', block, (x, y), None], in file order
        self.run = None
        self.warnings = []
        self.units = None
        self.bounds = None
        self.motion_count = 0
        self.cut_xy = 0

        self.cur = [None, None, None]   # machine position
        self.off = [0.0, 0.0, 0.0]      # G92 offset: program = machine + offset
        self.absolute = True
        self.arc_absolute = False
        self.plane = 17
        self.motion = None
        self.f = None
        self.g99 = False
        self.cyc_r = None
        self.cyc_z = None
        self.cyc_init = None
        self.pend = None                # xy of a Z-only plunge that may become a drill hit or a slot
        self.in_slot = False            # XY feed moves after a plunge: a slot if it is 1 straight move
        self.slot_moves = 0
        self.slot_runs = []
        self.last_hit = None

    @staticmethod
    def _new_acc():
        return {'dist': 0.0, 'rapid': 0.0, 'ftime': 0.0, 'z_cut': None, 'z_move': None, 'feedrate': None,
                'feedrate_z': None, 'spindle': None, 'drills': [], 'slots': 0}

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # ------------------------------------------------------------------ line

    def line(self, found):
        acc = self.acc[self.blk]
        gl = []
        w = {}
        for letter, val in found:
            if letter == 'G':
                gl.append(float(val))
            elif letter in 'XYZIJKRFSQP':
                w[letter] = float(val)
        if 'F' in w:
            self.f = w['F']
        if 'S' in w and acc['spindle'] is None:
            acc['spindle'] = w['S']

        move_ok = True
        cycle_start = False
        motion = self.motion
        for g in gl:
            if g in MOTION:
                motion = g
                self.motion = g
            elif g in CANNED:
                motion = g
                self.motion = g
                cycle_start = True
            elif g == 80.0:
                motion = None
                self.motion = None
            elif g in NO_MOVE:
                move_ok = False
            elif g == 20.0 or g == 21.0:
                units = 'IN' if g == 20.0 else 'MM'
                if self.units is None:
                    self.units = units
                elif units != self.units:
                    self.warn('Units change inside the file ignored: the first units are kept.')
            elif g == 90.0:
                self.absolute = True
            elif g == 91.0:
                self.absolute = False
            elif g == 90.1:
                self.arc_absolute = True
            elif g == 91.1:
                self.arc_absolute = False
            elif g in (17.0, 18.0, 19.0):
                self.plane = int(g)
            elif g == 98.0:
                self.g99 = False
            elif g == 99.0:
                self.g99 = True
            elif g == 92.0:
                for i, a in enumerate('XYZ'):
                    if a in w:
                        c = self.cur[i] if self.cur[i] is not None else 0.0
                        self.off[i] = w[a] - c
                move_ok = False
            elif g == 92.1:
                self.off = [0.0, 0.0, 0.0]

        if not move_ok or motion is None:
            return
        if 'X' not in w and 'Y' not in w and 'Z' not in w and not (cycle_start and motion in CANNED):
            return
        if motion in CANNED:
            self._canned(w, cycle_start)
        else:
            self._motion(motion, w)

    # ------------------------------------------------------------------ moves

    def _target(self, w):
        """Machine target of the X Y Z words (an axis without a word keeps its value, None when unknown)."""
        t = []
        for i, a in enumerate('XYZ'):
            v = w.get(a)
            c = self.cur[i]
            if v is None:
                t.append(c)
            elif self.absolute:
                t.append(v - self.off[i])
            else:
                t.append((c if c is not None else 0.0) + v)
        return t

    def _motion(self, g, w):
        t = self._target(w)
        self.motion_count += 1
        if None in (self.cur[0], self.cur[1], t[0], t[1]):
            # the XY start of the move is not known (the start position of the machine): no segment and no
            # assumed origin. A move with an X or Y word takes 0 for the other axis if it is not known yet; a Z-only
            # move keeps X and Y unknown but its height counts.
            if 'X' in w or 'Y' in w:
                t = [v if v is not None or i == 2 else 0.0 for i, v in enumerate(t)]
                self._bound(t[0], t[1])
            if t[2] is not None:
                acc = self.acc[self.blk]
                if g == 0.0:
                    if acc['z_move'] is None or t[2] > acc['z_move']:
                        acc['z_move'] = t[2]
                elif acc['z_cut'] is None or t[2] < acc['z_cut']:
                    acc['z_cut'] = t[2]
            self.cur = t
            return
        # a Z that is not known yet stays None (it is not a cut or a travel height)
        s = list(self.cur)
        if s[2] is None:
            s[2] = t[2]

        pts = None
        length = None
        if g in (2.0, 3.0):
            arc = self._arc(g == 2.0, s, t, w)
            if arc is None:
                if self.plane != 17:
                    self.warn('Arcs in the G18/G19 planes with an unknown position are drawn as straight lines.')
            else:
                pts, length = arc
        self._emit(g == 0.0, s, t, pts, length)
        self.cur = t

    def _arc(self, cw, s, t, w):
        """
        The arc in its plane (G17 XY with I J, G18 ZX with K I, G19 YZ with J K), its third axis linear (a helix),
        seen from above: the XY points (start excluded, end exact) and the 3D length.

        :return: (XY points, length), or None when the arc cannot be built
        """
        pts3 = _arc_points_3d(cw, self.plane, s, t, w, self.arc_absolute, self.steps, self.off)
        if pts3 is None:
            return None
        length = 0.0
        prev = (s[0], s[1], s[2] if s[2] is not None else 0.0)
        for p in pts3:
            length += math.dist(prev, p)
            prev = p
        return [(p[0], p[1]) for p in pts3], length

    def _emit(self, rapid, s, e, pts=None, arc_length=None):
        """
        Account and store one move from s to e (machine 3-tuples); pts are the XY points of an arc and arc_length
        its 3D length.
        """
        acc = self.acc[self.blk]
        xy = s[0] != e[0] or s[1] != e[1]
        dz = e[2] - s[2] if e[2] is not None and s[2] is not None else 0.0
        kind = 'T' if rapid else 'C'

        if pts is None:
            length = math.sqrt((e[0] - s[0]) ** 2 + (e[1] - s[1]) ** 2 + dz * dz)
        else:
            length = arc_length
            # an arc that comes back to its XY start (a full circle) still moves in XY
            xy = xy or any(p != (s[0], s[1]) for p in pts)
        acc['dist'] += length

        if xy:
            self.last_hit = None
            if rapid:
                self._close_pending()
            elif self.pend is not None and not self.in_slot:
                self._flush()
                self.in_slot = True
                self.slot_moves = 0
                self.slot_runs = []
            if self.in_slot and not rapid:
                # an arc is never a slot
                self.slot_moves += 1 if pts is None else 2
        if rapid:
            acc['rapid'] += length
            if e[2] is not None and (acc['z_move'] is None or e[2] > acc['z_move']):
                acc['z_move'] = e[2]
        else:
            if self.f:
                acc['ftime'] += length / self.f
            if e[2] is not None and (acc['z_cut'] is None or e[2] < acc['z_cut']):
                acc['z_cut'] = e[2]
            if xy:
                if acc['feedrate'] is None:
                    acc['feedrate'] = self.f
                if not self.in_slot:
                    self.cut_xy += 1
            elif dz != 0.0 and acc['feedrate_z'] is None:
                acc['feedrate_z'] = self.f

        if xy:
            new = pts if pts is not None else [(e[0], e[1])]
            self._bound(s[0], s[1])
            for x, y in new:
                self._bound(x, y)
            self._add(kind, (s[0], s[1]), new)
        elif self.run is not None and self.run[0] != kind:
            self._flush()

        if not xy and dz != 0.0:
            if dz < 0.0:
                if not rapid and self.pend is None and not self.in_slot:
                    self.pend = (e[0], e[1])
            else:
                self._close_pending()

    def _bound(self, x, y):
        b = self.bounds
        if b is None:
            self.bounds = [x, y, x, y]
            return
        if x < b[0]:
            b[0] = x
        elif x > b[2]:
            b[2] = x
        if y < b[1]:
            b[1] = y
        elif y > b[3]:
            b[3] = y

    def _add(self, kind, start, new):
        tag = 'S' if self.in_slot else None
        r = self.run
        if r is not None and (r[0] != kind or r[1] != self.blk or r[3] != tag or r[2][-1] != start):
            self._flush()
            r = None
        if r is None:
            r = self.run = [kind, self.blk, [start], tag]
            if tag == 'S':
                self.slot_runs.append(r)
        r[2].extend(new)

    def _flush(self):
        r = self.run
        if r is not None:
            if len(r[2]) > 1:
                self.items.append(r)
            self.run = None

    def _close_pending(self):
        """
        A Z move up ends a plunge: a drill hit when it never moved in XY, a slot after 1 straight XY feed move,
        a milled path otherwise.
        """
        if self.in_slot:
            if self.slot_moves == 1:
                self.acc[self.blk]['slots'] += 1
            else:
                self.cut_xy += 1
                for r in self.slot_runs:
                    r[3] = None
            self._flush()
            self.in_slot = False
            self.slot_runs = []
        elif self.pend is not None:
            self._hit(self.pend)
        self.pend = None

    def _hit(self, xy):
        if xy == self.last_hit:
            return
        self.last_hit = xy
        self._flush()
        self.acc[self.blk]['drills'].append(xy)
        self.items.append(['H', self.blk, xy, None])
        self._bound(xy[0], xy[1])

    def _canned(self, w, start):
        """One drill hit per XY position of a canned cycle."""
        self.motion_count += 1
        cur = self.cur
        if 'R' in w:
            self.cyc_r = w['R']
        if 'Z' in w:
            self.cyc_z = w['Z']
        if start or self.cyc_init is None:
            self.cyc_init = cur[2]
        t = self._target({'X': w['X'], 'Y': w['Y']} if 'X' in w and 'Y' in w else
                         {a: w[a] for a in 'XY' if a in w})
        cx = cur[0] if cur[0] is not None else 0.0
        cy = cur[1] if cur[1] is not None else 0.0
        cz = cur[2] if cur[2] is not None else 0.0
        tx = t[0] if t[0] is not None else cx
        ty = t[1] if t[1] is not None else cy
        r = self.cyc_r if self.cyc_r is not None else cz
        depth = self.cyc_z if self.cyc_z is not None else r
        if not self.absolute:
            r = cz + r
            depth = r + depth
        init = self.cyc_init if self.cyc_init is not None else cz
        retract = r if self.g99 else max(init, r)

        self._close_pending()
        self._emit(True, [cx, cy, cz], [tx, ty, cz])
        acc = self.acc[self.blk]
        d = abs(r - depth)
        acc['dist'] += 2.0 * d
        acc['rapid'] += d
        if self.f:
            acc['ftime'] += d / self.f
        if acc['z_cut'] is None or depth < acc['z_cut']:
            acc['z_cut'] = depth
        if acc['z_move'] is None or retract > acc['z_move']:
            acc['z_move'] = retract
        if acc['feedrate_z'] is None:
            acc['feedrate_z'] = self.f
        self.last_hit = None
        self._hit((tx, ty))
        self.cur = [tx, ty, retract]

    def finish(self):
        self._close_pending()
        self._flush()


def _tool_comments(comments, words):
    """Header and tool metadata from the comments."""
    meta = {'type': None, 'flatcam': False, 'preprocessor': None, 'dia': {}, 'zcut': {}}
    for i, txt in comments:
        up = txt.upper()
        if 'FLATCAM' in up:
            meta['flatcam'] = True
        m = TYPE_RE.search(txt)
        if m:
            meta['flatcam'] = True
            kind = m.group(1).upper()
            meta['type'] = 'Excellon' if kind == 'EXCELLON' else 'Geometry'
        m = PREPROC_RE.search(txt)
        if m:
            meta['flatcam'] = True
            if meta['preprocessor'] is None:
                meta['preprocessor'] = m.group(1)
        m = TOOL_ZCUT_RE.search(up)
        if m:
            meta['zcut'].setdefault(int(m.group(1)), float(m.group(2)))
        m = TOOL_DIA_RE.search(up)
        if m:
            meta['dia'].setdefault(int(m.group(1)), float(m.group(2)))
            continue
        m = DIA_ONLY_RE.match(up)
        if m:
            # a "(D0.8)" comment on the line of a T word
            for letter, val in words[i]:
                if letter == 'T':
                    meta['dia'].setdefault(int(float(val)), float(m.group(1)))
    return meta


# ---------------------------------------------------------------------------------------------------- programs

# token: number | <name> | ** | letters | any other character
TOK_RE = re.compile(r'\s*(?:(\d+\.?\d*|\.\d+)|<([^>]*)>|(\*\*)|([A-Z]+)|(\S))')
OWORD_RE = re.compile(r'O\s*(?:(\d+)|<([^>]*)>)\s*(SUB|ENDSUB|CALL|RETURN|IF|ELSEIF|ELSE|ENDIF|WHILE|ENDWHILE|DO|'
                      r'REPEAT|ENDREPEAT|BREAK|CONTINUE)\b')
FHEAD_RE = re.compile(r'[O:]\s*(\d+)$')
LABEL_RE = re.compile(r'/?\s*N\s*0*(\d+)')

PREC = {'OR': 1, 'XOR': 1, 'AND': 1, 'EQ': 2, 'NE': 2, 'GT': 2, 'GE': 2, 'LT': 2, 'LE': 2, '+': 3, '-': 3,
        '*': 4, '/': 4, 'MOD': 4, '**': 5}
FUNCS = frozenset(('SIN', 'COS', 'TAN', 'ASIN', 'ACOS', 'ATAN', 'SQRT', 'ABS', 'ROUND', 'FIX', 'FUP', 'LN', 'EXP',
                   'POW', 'ADP', 'BIN', 'BCD', 'EXISTS'))
FUNC1 = {
    'SIN': lambda a: math.sin(math.radians(a)), 'COS': lambda a: math.cos(math.radians(a)),
    'TAN': lambda a: math.tan(math.radians(a)),
    'ASIN': lambda a: math.degrees(math.asin(a)), 'ACOS': lambda a: math.degrees(math.acos(a)),
    'SQRT': math.sqrt, 'ABS': abs, 'LN': math.log, 'EXP': math.exp, 'ADP': lambda a: a,
    'ROUND': lambda a: math.copysign(math.floor(abs(a) + 0.5), a),
    'FIX': lambda a: float(math.floor(a)), 'FUP': lambda a: float(math.ceil(a)),
    'BIN': lambda a: float(int(str(int(abs(a))), 16)), 'BCD': lambda a: float(int('%x' % int(abs(a)))),
}
# Fanuc argument specification I: letter -> local variable
G65_ARGS = {'A': 1, 'B': 2, 'C': 3, 'I': 4, 'J': 5, 'K': 6, 'D': 7, 'E': 8, 'F': 9, 'H': 11, 'M': 13, 'Q': 17,
            'R': 18, 'S': 19, 'T': 20, 'U': 21, 'V': 22, 'W': 23, 'X': 24, 'Y': 25, 'Z': 26}
COORD = frozenset('XYZIJKR')
ALL_KW = ('SUB', 'ENDSUB', 'CALL', 'RETURN', 'IF', 'ELSEIF', 'ELSE', 'ENDIF', 'WHILE', 'ENDWHILE', 'DO', 'REPEAT',
          'ENDREPEAT', 'BREAK', 'CONTINUE')
SYSTEM_NAMES = frozenset((
    '_X', '_Y', '_Z', '_A', '_B', '_C', '_U', '_V', '_W', '_I', '_J', '_K', '_VMAJOR', '_VMINOR', '_LINE',
    '_MOTION_MODE', '_PLANE', '_FEED', '_SPEED', '_COOLANT_MIST', '_COOLANT_FLOOD', '_FEED_OVERRIDE',
    '_RAPID_OVERRIDE', '_SPINDLE_OVERRIDE', '_SPINDLE_ON', '_SELECTED_POCKET', '_CURRENT_TOOL', '_CURRENT_POCKET',
    '_TOOL_OFFSET', '_ABSOLUTE', '_INCREMENTAL', '_METRIC', '_IMPERIAL', '_INVERSE_TIME', '_UNITS_PER_MINUTE',
    '_UNITS_PER_REV', '_CUTTER_COMP_OFF', '_CUTTER_COMP_LEFT', '_CUTTER_COMP_RIGHT', '_TOOL_LENGTH_OFFSET'))
MAX_CALL_DEPTH = 16


class _Bad(Exception):
    """A line that can not be read."""


class _Halt(Exception):
    """The expansion stops here (the message is the warning)."""


class _Unknown:
    """The value of a machine variable: not known offline."""


UNKNOWN = _Unknown()


def _tokens(s):
    out = []
    for m in TOK_RE.finditer(s):
        n, name, pw, ident, ch = m.groups()
        if n is not None:
            out.append(('n', float(n)))
        elif name is not None:
            out.append(('<', name.replace(' ', '')))
        elif pw:
            out.append(('**', None))
        elif ident:
            out.append(('i', ident))
        else:
            out.append((ch, None))
    out.append(('$', None))
    return out


def _num(v):
    return v if v.__class__ is float else 0.0


def _word_text(letter, v):
    s = '%.6f' % v
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    if s in ('-0', ''):
        s = '0'
    return letter + s


class _Frame:
    """A call level: local variables, where to return, repeat count, loop counters."""
    __slots__ = ('loc', 'prev', 'ret', 'ret_rng', 'reps', 'start', 'cnt')

    def __init__(self, prev=None):
        self.loc = {}
        self.prev = prev
        self.ret = 0
        self.ret_rng = None
        self.reps = 0
        self.start = 0
        self.cnt = {}


class _Expander:
    """
    Runs the program flow (subprograms, macro variables, expressions, loops, branches) of a G-code text and
    collects the plain lines the machine would run.
    """

    def __init__(self, lines, text, max_lines, max_loops):
        self.n = len(lines)
        self.max_lines = max_lines
        self.max_loops = max_loops
        clean = []
        for ln in lines:
            if '(' in ln or ';' in ln:
                ln = COMMENT_RE.sub(' ', ln)
            ln = ln.strip().upper()
            clean.append('' if ln[:1] == '%' else ln)
        self.clean = clean
        self.inf = [None] * self.n
        self.jmp = {}
        self.labels = {}
        self.out = []
        self.warnings = []
        self.glob = {}
        self.top = 0
        self.taint = False
        self.stop = False
        self.t = None
        self.p = 0
        self.lcnc = '#<' in text
        self.subs = {}
        self.skip = {}
        self.main = (0, self.n)
        self._scan()
        self.nloc = 30 if self.lcnc else 33
        self.frame = _Frame()
        self.rng = self.main
        self.depth = 0
        self.pc = 0
        self.next = 0

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def _scan(self):
        subs, skip, heads, opens = self.subs, self.skip, [], []
        for i, s in enumerate(self.clean):
            c = s[:1]
            if c == 'O':
                m = OWORD_RE.match(s)
                if m:
                    self.lcnc = True
                    kw = m.group(3)
                    if kw == 'SUB':
                        opens.append((int(m.group(1)) if m.group(1) else m.group(2).replace(' ', ''), i))
                    elif kw == 'ENDSUB' and opens:
                        key, st = opens.pop()
                        subs[key] = (st + 1, i)
                        skip[st] = i + 1
                    continue
            if c == 'O' or c == ':':
                m = FHEAD_RE.match(s)
                if m:
                    heads.append((int(m.group(1)), i))
        first = heads[0][1] if heads else self.n
        bounds = [h[1] for h in heads] + [self.n]
        for k, (key, i) in enumerate(heads):
            subs.setdefault(key, (i + 1, bounds[k + 1]))
        if heads and not any(self.clean[:first]):
            self.main = (heads[0][1] + 1, bounds[1])
        else:
            self.main = (0, first)

    # ------------------------------------------------------------------ lines

    def _info(self, i):
        inf = self.inf[i]
        if inf is None:
            inf = self.inf[i] = self._compile(i)
        return inf

    def _compile(self, i):
        """A plain line: its text. A line with macros, flow or calls: (tokens, index of its first word)."""
        s = self.clean[i]
        toks = _tokens(s)
        p = 1 if toks[0][0] == '/' else 0
        if toks[p] == ('i', 'N') and toks[p + 1][0] == 'n':
            p += 2
        k = p
        while True:
            kind, v = toks[k]
            if kind == '$':
                return s
            if kind == 'i':
                if len(v) > 1 or v == 'O':
                    break
                nxt = toks[k + 1]
                if nxt[0] == 'n' and ((v == 'G' and 65.0 <= nxt[1] < 68.0) or
                                      (v == 'M' and nxt[1] in (2.0, 30.0, 98.0, 99.0))):
                    break
            elif kind != 'n' and kind != '-' and kind != '+':
                break
            k += 1
        return toks, p

    def _label(self, n):
        rng = self.rng
        d = self.labels.get(rng)
        if d is None:
            d = self.labels[rng] = {}
            for i in range(rng[0], rng[1]):
                m = LABEL_RE.match(self.clean[i])
                if m:
                    d.setdefault(int(m.group(1)), i)
        return d.get(n)

    def emit(self, text):
        if len(self.out) >= self.max_lines:
            raise _Halt('The program expands to more than %d lines: the expansion stops here.' % self.max_lines)
        self.out.append((self.top, text))

    def run(self):
        steps = 0
        max_steps = max(self.max_lines * 10, 100000)
        pc = self.main[0]
        try:
            while True:
                if pc >= self.rng[1]:
                    if self.frame.prev is None:
                        break
                    self._return()
                    pc = self.next
                    continue
                steps += 1
                if steps > max_steps:
                    raise _Halt('The program runs more than %d steps: the expansion stops here.' % max_steps)
                if self.frame.prev is None:
                    self.top = pc
                self.pc = pc
                self.next = pc + 1
                try:
                    self._exec(pc)
                except _Bad:
                    self.warn('Line %d is not understood: skipped.' % (pc + 1))
                if self.stop:
                    break
                pc = self.next
        except _Halt as h:
            self.warn(str(h))
        return self.out

    def _exec(self, pc):
        inf = self._info(pc)
        if inf.__class__ is str:
            if inf:
                self.emit(inf)
            return
        toks, p = inf
        kind, kw = toks[p]
        if kind == 'i':
            if kw == 'O':
                return self._oword(pc, toks, p)
            if kw == 'IF':
                return self._if(pc, toks, p + 1)
            if kw == 'WHILE':
                return self._while(pc, toks, p + 1)
            if kw == 'GOTO':
                return self._goto(pc, toks, p + 1)
            if kw == 'END' and toks[p + 1][0] == 'n':
                return self._end(pc, toks[p + 1][1])
            if kw == 'DO' and toks[p + 1][0] == 'n':
                return
        elif kind == ':':
            return
        self._words(toks, p)

    # ------------------------------------------------------------------ words

    def _words(self, toks, p):
        self.t = toks
        words = []
        assigns = []
        while True:
            tk = toks[p]
            kind = tk[0]
            if kind == '$':
                break
            if kind == '#':
                self.p = p + 1
                key = self._key()
                if toks[self.p][0] != '=':
                    raise _Bad
                self.p += 1
                self.taint = False
                v = self.expr()
                assigns.append((key, UNKNOWN if self.taint else v))
                p = self.p
            elif kind == 'i' and len(tk[1]) == 1:
                self.p = p + 1
                self.taint = False
                v = self.unary()
                words.append((tk[1], v, self.taint))
                p = self.p
            else:
                raise _Bad

        gs = [v for l, v, t in words if l == 'G' and v.__class__ is float]
        ms = [v for l, v, t in words if l == 'M' and v.__class__ is float]
        action = None
        if 65.0 in gs:
            action = 'G65'
        elif 66.0 in gs or 66.1 in gs:
            self.warn('Modal macro calls (G66) are not followed.')
            action = 'skip'
        elif 98.0 in ms:
            action = 'M98'
        elif 99.0 in ms:
            action = 'M99'
        elif 30.0 in ms or 2.0 in ms:
            action = 'END'

        if action is None or action == 'END' or action == 'M99':
            out = []
            skip_line = False
            for l, v, t in words:
                if t and l in COORD:
                    skip_line = True
                if v.__class__ is not float or t:
                    continue
                if (l == 'M' and v in (2.0, 30.0, 99.0)) or (l == 'G' and v == 67.0) or \
                        (action == 'M99' and l == 'P'):
                    continue
                out.append(_word_text(l, v))
            if out and not skip_line:
                self.emit(' '.join(out))
        for key, val in assigns:
            self._set(key, val)
        if action is None or action == 'skip':
            return
        if action == 'END':
            self.stop = True
            return
        wd = {l: v for l, v, t in words if v.__class__ is float}
        if action == 'M99':
            self._return(int(wd['P']) if 'P' in wd else None)
            return
        if 'P' not in wd:
            self.warn('Line %d: the call has no program number (P): skipped.' % (self.pc + 1))
            return
        pnum = int(wd['P'])
        rep = wd.get('L', wd.get('K'))
        if action == 'M98' and pnum > 9999:
            if rep is None:
                rep = pnum // 10000
            pnum %= 10000
        rep = 1 if rep is None else int(rep)
        if rep < 1:
            return
        args = {}
        if action == 'G65':
            args = {G65_ARGS[l]: v for l, v in wd.items() if l in G65_ARGS}
        self._call(pnum, args, rep)

    # ------------------------------------------------------------------ calls

    def _call(self, key, args, rep):
        sub = self.subs.get(key)
        if sub is None:
            self.warn('Subprogram %s is not in the file: the call is skipped.' %
                      (('O%d' % key) if key.__class__ is int else 'O<%s>' % key))
            return
        if self.depth >= MAX_CALL_DEPTH:
            self.warn('Subprogram calls are nested more than %d levels: a call is skipped.' % MAX_CALL_DEPTH)
            return
        fr = _Frame(self.frame)
        fr.loc = args
        fr.ret = self.pc + 1
        fr.ret_rng = self.rng
        fr.reps = rep - 1
        fr.start = sub[0]
        self.frame = fr
        self.depth += 1
        self.rng = sub
        self.next = sub[0]

    def _return(self, label=None):
        fr = self.frame
        if fr.prev is None:
            raise _Halt('A return in the main program ends the expansion (the program would loop to its start).')
        if label is None and fr.reps > 0:
            fr.reps -= 1
            self.next = fr.start
            return
        self.frame = fr.prev
        self.depth -= 1
        self.rng = fr.ret_rng
        self.next = fr.ret
        if label is not None:
            line = self._label(label)
            if line is None:
                self.warn('Return label N%d not found: returned to the line after the call.' % label)
            else:
                self.next = line

    # ------------------------------------------------------------------ Fanuc flow

    def _cond(self, toks, p):
        self.t = toks
        self.p = p
        return _num(self.expr()) != 0.0

    def _if(self, pc, toks, p):
        c = self._cond(toks, p)
        nxt = toks[self.p]
        if nxt == ('i', 'GOTO'):
            if c:
                self._goto(pc, toks, self.p + 1)
        elif nxt == ('i', 'THEN'):
            if c:
                self._words(toks, self.p + 1)
        else:
            raise _Bad

    def _goto(self, pc, toks, p):
        self.t = toks
        self.p = p
        n = int(_num(self.unary()))
        cnt = self.frame.cnt
        k = -pc - 1
        cnt[k] = cnt.get(k, 0) + 1
        if cnt[k] > self.max_loops:
            raise _Halt('A GOTO loop ran more than %d times: the expansion stops here.' % self.max_loops)
        line = self._label(n)
        if line is None:
            self.warn('Label N%d not found: GOTO skipped.' % n)
        else:
            self.next = line

    def _loop_count(self, pc):
        cnt = self.frame.cnt
        n = cnt.get(pc, 0) + 1
        if n > self.max_loops:
            raise _Halt('A loop ran more than %d times: the expansion stops here.' % self.max_loops)
        cnt[pc] = n

    def _while(self, pc, toks, p):
        c = self._cond(toks, p)
        if toks[self.p] != ('i', 'DO') or toks[self.p + 1][0] != 'n':
            raise _Bad
        m = toks[self.p + 1][1]
        if c:
            self._loop_count(pc)
            return
        self.frame.cnt.pop(pc, None)
        key = ('e', pc)
        j = self.jmp.get(key)
        if j is None:
            for q in range(pc + 1, self.rng[1]):
                inf = self._info(q)
                if inf.__class__ is not str and inf[0][inf[1]] == ('i', 'END') and inf[0][inf[1] + 1] == ('n', m):
                    j = self.jmp[key] = q
                    break
            else:
                raise _Bad
        self.next = j + 1

    def _end(self, pc, m):
        key = ('b', pc)
        j = self.jmp.get(key)
        if j is None:
            for q in range(pc - 1, self.rng[0] - 1, -1):
                inf = self._info(q)
                if inf.__class__ is str:
                    continue
                toks = inf[0]
                if any(toks[k] == ('i', 'DO') and toks[k + 1] == ('n', m) for k in range(inf[1], len(toks) - 1)):
                    j = self.jmp[key] = q
                    break
            else:
                raise _Bad
        self.next = j

    # ------------------------------------------------------------------ LinuxCNC flow

    def _find_o(self, pc, key, kws, back=False):
        """The nearest line after (or before) pc that is an o-word of the same id with one of the keywords."""
        ck = (pc, key, kws, back)
        if ck in self.jmp:
            return self.jmp[ck]
        rr = range(pc - 1, self.rng[0] - 1, -1) if back else range(pc + 1, self.rng[1])
        res = None
        for j in rr:
            inf = self._info(j)
            if inf.__class__ is str:
                continue
            toks, p = inf
            if toks[p] == ('i', 'O') and toks[p + 2][0] == 'i' and toks[p + 2][1] in kws and \
                    self._okey(toks[p + 1]) == key:
                res = (j, toks[p + 2][1], p + 3)
                break
        self.jmp[ck] = res
        return res

    @staticmethod
    def _okey(tk):
        if tk[0] == 'n':
            return int(tk[1])
        if tk[0] == '<':
            return tk[1]
        raise _Bad

    def _oword(self, pc, toks, p):
        key = self._okey(toks[p + 1])
        kt = toks[p + 2]
        if kt[0] != 'i':
            return      # the header of a program
        kw = kt[1]
        q = p + 3
        cnt = self.frame.cnt
        if kw == 'SUB':
            end = self.skip.get(pc)
            if end is not None:
                self.next = end
        elif kw == 'ENDSUB' or kw == 'RETURN':
            if toks[q][0] == '[':
                self.t = toks
                self.p = q
                self.glob['_VALUE'] = _num(self.unary())
            self._return()
        elif kw == 'CALL':
            self.t = toks
            self.p = q
            args = {}
            i = 1
            while toks[self.p][0] == '[':
                args[i] = self.unary()
                i += 1
            self._call(key, args, 1)
        elif kw == 'IF' or kw == 'ELSEIF':
            if kw == 'ELSEIF' or not self._cond(toks, q):
                if kw == 'ELSEIF':
                    # reached by running through the true branch: to the end
                    r = self._find_o(pc, key, ('ENDIF',))
                    if r is None:
                        raise _Bad
                    self.next = r[0] + 1
                    return
                i = pc
                while True:
                    r = self._find_o(i, key, ('ELSEIF', 'ELSE', 'ENDIF'))
                    if r is None:
                        raise _Bad
                    i = r[0]
                    if r[1] == 'ELSEIF' and not self._cond(self._info(i)[0], r[2]):
                        continue
                    self.next = i + 1
                    break
        elif kw == 'ELSE':
            r = self._find_o(pc, key, ('ENDIF',))
            if r is None:
                raise _Bad
            self.next = r[0] + 1
        elif kw == 'WHILE':
            back = self._find_o(pc, key, ALL_KW, True)
            c = self._cond(toks, q)
            if back is not None and back[1] == 'DO':
                if c:
                    self._loop_count(pc)
                    self.next = back[0] + 1
                else:
                    cnt.pop(pc, None)
            elif c:
                self._loop_count(pc)
            else:
                cnt.pop(pc, None)
                r = self._find_o(pc, key, ('ENDWHILE',))
                if r is None:
                    raise _Bad
                self.next = r[0] + 1
        elif kw == 'ENDWHILE':
            r = self._find_o(pc, key, ('WHILE',), True)
            if r is None:
                raise _Bad
            self.next = r[0]
        elif kw == 'REPEAT':
            r = self._find_o(pc, key, ('ENDREPEAT',))
            if r is None:
                raise _Bad
            ck = ('r', pc)
            if ck not in cnt:
                self.t = toks
                self.p = q
                n = int(_num(self.expr()))
                if n > self.max_loops:
                    self.warn('A repeat loop is limited to %d times.' % self.max_loops)
                    n = self.max_loops
                cnt[ck] = n
            if cnt[ck] > 0:
                cnt[ck] -= 1
            else:
                del cnt[ck]
                self.next = r[0] + 1
        elif kw == 'ENDREPEAT':
            r = self._find_o(pc, key, ('REPEAT',), True)
            if r is None:
                raise _Bad
            self.next = r[0]
        elif kw == 'BREAK' or kw == 'CONTINUE':
            e = self._find_o(pc, key, ('ENDWHILE', 'ENDREPEAT', 'WHILE'))
            if e is None:
                raise _Bad
            if kw == 'CONTINUE':
                self.next = e[0]
                return
            self.next = e[0] + 1
            if e[1] == 'WHILE':
                cnt.pop(e[0], None)
            else:
                r = self._find_o(e[0], key, ('WHILE' if e[1] == 'ENDWHILE' else 'REPEAT',), True)
                if r is not None:
                    cnt.pop(r[0] if e[1] == 'ENDWHILE' else ('r', r[0]), None)
        elif kw != 'DO' and kw != 'ENDIF':
            raise _Bad

    # ------------------------------------------------------------------ variables

    def _machine(self, k):
        if self.lcnc:
            return 5061 <= k <= 5070 or 5161 <= k <= 5390 or 5400 <= k <= 5403 or 5420 <= k <= 5428
        return 2000 <= k <= 5999

    @staticmethod
    def _vname(key):
        return '#%d' % key if key.__class__ is int else '#<%s>' % key

    def _get(self, key):
        if key.__class__ is int:
            if key <= 0:
                return None
            if key <= self.nloc:
                return self.frame.loc.get(key)
            if self._machine(key):
                self.warn('The value of %s is not known offline: the moves that use it are skipped.' %
                          self._vname(key))
                self.taint = True
                return UNKNOWN
            v = self.glob.get(key)
        elif key[:1] == '_':
            if key in SYSTEM_NAMES:
                self.warn('The value of %s is not known offline: the moves that use it are skipped.' %
                          self._vname(key))
                self.taint = True
                return UNKNOWN
            v = self.glob.get(key)
        else:
            v = self.frame.loc.get(key)
        if v is UNKNOWN:
            self.taint = True
        return v

    def _set(self, key, val):
        if val.__class__ is not float and val is not UNKNOWN:
            val = None
        if key.__class__ is int:
            if key <= 0:
                return
            if key <= self.nloc:
                self.frame.loc[key] = val
                return
            if self._machine(key):
                if key == 3000 and not self.lcnc:
                    raise _Halt('#3000 was set (an alarm): the expansion stops here.')
                self.warn('Writing the machine variable %s is ignored.' % self._vname(key))
                return
            self.glob[key] = val
        elif key[:1] == '_':
            if key in SYSTEM_NAMES:
                self.warn('Writing the machine variable %s is ignored.' % self._vname(key))
                return
            self.glob[key] = val
        else:
            self.frame.loc[key] = val

    def _exists(self, key):
        if key.__class__ is int:
            d = self.frame.loc if 0 < key <= self.nloc else self.glob
        else:
            d = self.glob if key[:1] == '_' else self.frame.loc
        return d.get(key) is not None

    # ------------------------------------------------------------------ expressions

    def _key(self):
        """The variable after a '#': its number (int) or its name."""
        tk = self.t[self.p]
        kind = tk[0]
        self.p += 1
        if kind == 'n':
            return int(tk[1])
        if kind == '<':
            return tk[1]
        if kind == '[':
            v = _num(self.expr())
            self._close()
            return int(v)
        if kind == '#':
            return int(_num(self._get(self._key())))
        raise _Bad

    def _close(self):
        if self.t[self.p][0] != ']':
            raise _Bad
        self.p += 1

    def expr(self, minp=1):
        left = self.unary()
        t = self.t
        while True:
            tk = t[self.p]
            op = tk[1] if tk[0] == 'i' else tk[0]
            prec = PREC.get(op)
            if prec is None or prec < minp:
                return left
            self.p += 1
            right = self.expr(prec + 1)
            a = _num(left)
            b = _num(right)
            try:
                if op == '+':
                    left = a + b
                elif op == '-':
                    left = a - b
                elif op == '*':
                    left = a * b
                elif op == '/':
                    left = a / b
                elif op == 'MOD':
                    left = math.fmod(a, b)
                elif op == '**':
                    left = a ** b
                    if left.__class__ is not float:
                        left = 0.0
                # LinuxCNC: logical operators; Fanuc: bit by bit on the integer values (the same for 0 / 1 conditions)
                elif op == 'AND':
                    left = (1.0 if a != 0.0 and b != 0.0 else 0.0) if self.lcnc else float(int(a) & int(b))
                elif op == 'OR':
                    left = (1.0 if a != 0.0 or b != 0.0 else 0.0) if self.lcnc else float(int(a) | int(b))
                elif op == 'XOR':
                    left = (1.0 if (a != 0.0) != (b != 0.0) else 0.0) if self.lcnc else float(int(a) ^ int(b))
                elif op == 'EQ':
                    left = 1.0 if abs(a - b) < 1e-9 else 0.0
                elif op == 'NE':
                    left = 1.0 if abs(a - b) >= 1e-9 else 0.0
                elif op == 'GT':
                    left = 1.0 if a > b else 0.0
                elif op == 'GE':
                    left = 1.0 if a >= b - 1e-9 else 0.0
                elif op == 'LT':
                    left = 1.0 if a < b else 0.0
                else:
                    left = 1.0 if a <= b + 1e-9 else 0.0
            except (ZeroDivisionError, ValueError, OverflowError):
                raise _Halt('A macro expression can not be calculated (division by zero or invalid value): '
                            'the expansion stops here.')

    def unary(self):
        tk = self.t[self.p]
        kind = tk[0]
        self.p += 1
        if kind == 'n':
            return tk[1]
        if kind == '-':
            return -_num(self.unary())
        if kind == '+':
            return self.unary()
        if kind == '[':
            v = self.expr()
            self._close()
            return v
        if kind == '#':
            return self._get(self._key())
        if kind == 'i' and tk[1] in FUNCS:
            return self._func(tk[1])
        raise _Bad

    def _func(self, name):
        t = self.t
        if t[self.p][0] != '[':
            raise _Bad
        self.p += 1
        if name == 'EXISTS':
            if t[self.p][0] != '#':
                raise _Bad
            self.p += 1
            r = 1.0 if self._exists(self._key()) else 0.0
            self._close()
            return r
        a = _num(self.expr())
        b = None
        if name == 'POW':
            if t[self.p][0] != ',':
                raise _Bad
            self.p += 1
            b = _num(self.expr())
        self._close()
        try:
            if name == 'ATAN':
                if t[self.p][0] == '/' and t[self.p + 1][0] == '[':
                    self.p += 2
                    b = _num(self.expr())
                    self._close()
                else:
                    b = 1.0
                r = math.degrees(math.atan2(a, b))
                return r % 360.0 if not self.lcnc else r
            if name == 'POW':
                r = a ** b
                return r if r.__class__ is float else 0.0
            return float(FUNC1[name](a))
        except (ZeroDivisionError, ValueError, OverflowError):
            raise _Halt('A macro function (%s) can not be calculated: the expansion stops here.' % name)


def expand_program(text, max_lines=200000, max_loops=10000):
    """
    Runs the program flow of a G-code text: subprogram calls (Fanuc M98/M99/G65 with O-numbers, LinuxCNC o-subs),
    macro variables and expressions, GOTO/IF/WHILE and o-word loops. The result is the plain lines the machine
    would run, with the numbers substituted.

    :param text:      the G-code
    :param max_lines: the expansion stops (with a warning) after this many lines
    :param max_loops: the expansion stops (with a warning) when a loop runs more often
    :return: ([(index of the line of the main program that produced the line, plain line)], [warning]);
             the index counts the lines of the text, a subprogram line gets the index of its call site
    """
    ex = _Expander(_split_lines(text), text, max_lines, max_loops)
    out = ex.run()
    return out, ex.warnings


def parse_gcode(text, steps_per_circle=64, default_tooldia=0.4, feedrate_rapid=None, target_units=None):
    """
    Parse the G-code text of any machine/CAM into per-tool geometry and metadata.

    :param text:            the G-code
    :param steps_per_circle: segments of a whole circle (arcs, drill rings)
    :param default_tooldia: tool diameter used for drill rings/slots when the file does not give one
                            (in target units)
    :param feedrate_rapid:  rapid feed rate in target units/min; the rapid time is skipped when None
    :param target_units:    'MM' or 'IN': every returned number is converted to it
    :return: dict with units, factor, type, flatcam, preprocessor, tools {1..n: {...}}, bounds,
             travel_distance, routing_time, warnings, motion_count
    """
    lines, words, comments, blocks, macro = _prepare(text)
    meta = _tool_comments(comments, words)

    it = _Interpreter(len(blocks), steps_per_circle)
    flow = False
    if '#' in text or '[' in text or SUBPROGRAM_RE.search(text.upper()):
        feats = program_features(text)
        flow = feats['macros'] or feats['subprograms']
    if flow:
        # subprograms and macros: the lines the machine would run, each in the tool block of its call site
        expanded, ewarn = expand_program(text)
        starts = [b[0] for b in blocks]
        last_top = -1
        for top, ltext in expanded:
            if top != last_top:
                it.blk = max(bisect.bisect_right(starts, top) - 1, 0)
                last_top = top
            found = WORD_RE.findall(ltext)
            if found:
                it.line(found)
        for msg in ewarn:
            it.warn(msg)
    else:
        bi = 0
        next_first = blocks[1][0] if len(blocks) > 1 else None
        for i, found in enumerate(words):
            if next_first is not None and i == next_first:
                bi += 1
                it.blk = bi
                next_first = blocks[bi + 1][0] if bi + 1 < len(blocks) else None
            if found:
                it.line(found)
    it.finish()

    units = it.units
    factor = 1.0
    if target_units and units:
        tu = target_units.upper()
        if tu in ('MM', 'IN') and tu != units:
            factor = MM_PER_IN if units == 'IN' else 1.0 / MM_PER_IN

    hits = sum(len(a['drills']) for a in it.acc)
    slots = sum(a['slots'] for a in it.acc)
    if meta['type'] is not None:
        gtype = meta['type']
    elif hits > 0 and it.cut_xy == 0 and slots <= hits:
        gtype = 'Excellon'
    else:
        gtype = 'Geometry'

    q = max(1, int(steps_per_circle) // 4)
    slices = _block_slices(lines, blocks)
    spindle0 = next((a['spindle'] for a in it.acc if a['spindle'] is not None), None)
    tools = {}
    for n, (blk, sl) in enumerate(zip(blocks, slices)):
        tnum = blk[1]
        acc = it.acc[n]
        dia = meta['dia'].get(tnum)
        dia = dia * factor if dia is not None else None
        zc = acc['z_cut']
        if zc is None and tnum in meta['zcut']:
            zc = meta['zcut'][tnum]
        ft = acc['feedrate']
        fz = acc['feedrate_z']
        sp = acc['spindle'] if acc['spindle'] is not None else spindle0
        tools[n + 1] = {
            'tool': tnum, 'tooldia': dia, 'gcode': sl['gcode'], 'gcode_parsed': [], 'solid_geometry': [],
            'z_cut': zc * factor if zc is not None else None,
            'z_move': acc['z_move'] * factor if acc['z_move'] is not None else None,
            'feedrate': ft * factor if ft is not None else None,
            'feedrate_z': fz * factor if fz is not None else None,
            'spindlespeed': sp,
            'nr_drills': len(acc['drills']), 'nr_slots': acc['slots'],
            'drills': [(x * factor, y * factor) for x, y in acc['drills']],
            'travel_distance': acc['dist'] * factor,
            'routing_time': acc['ftime'] + (acc['rapid'] * factor / feedrate_rapid if feedrate_rapid else 0.0),
        }

    for item in it.items:
        tool = tools[item[1] + 1]
        d = tool['tooldia'] if tool['tooldia'] else default_tooldia
        if item[0] == 'H':
            x, y = item[2]
            pt = Point(x * factor, y * factor)
            if gtype == 'Excellon':
                geom = shapely.buffer(pt, d / 2.0, quad_segs=q).exterior
                kind = ['C', 'F']
            else:
                geom = pt
                kind = ['C', 'S']
        else:
            geom = LineString(np.asarray(item[2]) * factor)
            kind = [item[0], 'F' if item[0] == 'T' else 'S']
            if gtype == 'Excellon' and item[3] == 'S':
                geom = shapely.buffer(geom, d / 2.0, quad_segs=q).exterior
                kind = ['C', 'F']
        tool['gcode_parsed'].append({'geom': geom, 'kind': kind})
        tool['solid_geometry'].append(geom)

    bounds = tuple(v * factor for v in it.bounds) if it.bounds else None
    return {
        'units': units, 'factor': factor, 'type': gtype, 'flatcam': meta['flatcam'],
        'preprocessor': meta['preprocessor'], 'tools': tools, 'bounds': bounds,
        'travel_distance': sum(a['dist'] for a in it.acc) * factor,
        'routing_time': sum(t['routing_time'] for t in tools.values()),
        'warnings': it.warnings, 'motion_count': it.motion_count,
    }


# ---------------------------------------------------------------------------------------------------- transform

WORD_ANY_RE = re.compile(r'(?<![A-Za-z])([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))')
AFFINE_ID = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _fmt(v, decimals):
    """Fixed decimals, never an exponent and never '-0.0000'."""
    s = '%.*f' % (decimals, v)
    if float(s) == 0.0:
        s = '%.*f' % (decimals, 0.0)
    return s


def _case(letter, ref):
    return letter.lower() if ref.islower() else letter


class _Transformer:
    """
    Rewrites the lines of a G-code text under an affine matrix, keeping the modal state of the program (G90/G91,
    G90.1/G91.1, motion mode, plane, position) to know what every X Y I J R word means.
    """

    def __init__(self, matrix, decimals, steps):
        self.a, self.b, self.d, self.e, self.xo, self.yo = matrix
        self.dec = max(int(decimals), 0)
        self.steps = max(int(steps), 4)
        self.mixing = self.b != 0.0 or self.d != 0.0
        a, b, d, e = self.a, self.b, self.d, self.e
        det = a * e - b * d
        n1, n2 = a * a + d * d, b * b + e * e
        top = max(n1, n2)
        self.similar = top > 0.0 and abs(a * b + d * e) <= 1e-9 * top and abs(n1 - n2) <= 1e-9 * top
        self.scale = math.sqrt(abs(det))
        self.reflect = det < 0.0
        self.linear_id = (a, b, d, e) == (1.0, 0.0, 0.0, 1.0)
        self.warnings = []
        self.absolute = True
        self.arc_absolute = False
        self.plane = 17
        self.motion = None
        self.cur = [None, None, None]

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def pt(self, x, y):
        return self.a * x + self.b * y + self.xo, self.d * x + self.e * y + self.yo

    def vec(self, x, y):
        return self.a * x + self.b * y, self.d * x + self.e * y

    # ------------------------------------------------------------------ writing

    def _put(self, body, w, edits, l1, l2, n1, n2):
        """Writes the new values of a pair of words (X Y or I J); with mixed axes both words are always written."""
        for letter, nv in ((l1, n1), (l2, n2)):
            wd = w.get(letter)
            if wd is not None and nv != wd[0]:
                edits.append((wd[1], wd[2], body[wd[1]] + _fmt(nv, self.dec)))
        if self.mixing:
            w1, w2 = w.get(l1), w.get(l2)
            if w1 is None and w2 is not None:
                edits.append((w2[1], w2[1], _case(l1, body[w2[1]]) + _fmt(n1, self.dec) + ' '))
            elif w2 is None and w1 is not None:
                edits.append((w1[2], w1[2], ' ' + _case(l2, body[w1[1]]) + _fmt(n2, self.dec)))

    def _target(self, w, absolute):
        """Old target (x, y) of the X Y words, or None when the line holds none."""
        hx, hy = 'X' in w, 'Y' in w
        if not (hx or hy):
            return None
        cx, cy = self.cur[0], self.cur[1]
        if absolute:
            tx = w['X'][0] if hx else cx
            ty = w['Y'][0] if hy else cy
            if self.mixing and (tx is None or ty is None):
                self.warn('A coordinate was missing and the position is unknown: 0 was used.')
            return (0.0 if tx is None else tx), (0.0 if ty is None else ty)
        return (0.0 if cx is None else cx) + (w['X'][0] if hx else 0.0), \
            (0.0 if cy is None else cy) + (w['Y'][0] if hy else 0.0)

    def _endpoint(self, body, w, edits, absolute):
        """Transforms the X Y words of a move (point when absolute, vector when incremental)."""
        t = self._target(w, absolute)
        if t is None:
            return
        if absolute:
            self._put(body, w, edits, 'X', 'Y', *self.pt(*t))
        else:
            cx, cy = self.cur[0] or 0.0, self.cur[1] or 0.0
            self._put(body, w, edits, 'X', 'Y', *self.vec(t[0] - cx, t[1] - cy))
        self.cur[0], self.cur[1] = t

    def _z(self, w, cycle):
        if cycle or 'Z' not in w:
            return
        if self.absolute:
            self.cur[2] = w['Z'][0]
        else:
            self.cur[2] = (self.cur[2] or 0.0) + w['Z'][0]

    # ------------------------------------------------------------------ line

    def line(self, raw):
        body = raw.rstrip('\r\n')
        eol = raw[len(body):]
        if body.lstrip()[:1] == '%':
            return raw
        masked = COMMENT_RE.sub(lambda m: ' ' * len(m.group(0)), body)
        found = [(m.group(1).upper(), float(m.group(2)), m.start(), m.end()) for m in WORD_ANY_RE.finditer(masked)]
        if not found:
            return raw
        if '#' in masked or '[' in masked:
            self.warn('Lines with macro variables or expressions are not transformed.')
            return raw

        gl = []
        w = {}
        for letter, val, s, e in found:
            if letter == 'G':
                gl.append((val, s, e))
            elif letter in 'XYZIJKRF' and letter not in w:
                w[letter] = (val, s, e)

        edits = []
        no_move = False
        dwell = False
        point_only = False      # a non-modal move (G38.x) whose X Y are points
        g92 = False
        for g, s, e in gl:
            ig = int(g)
            if g == 90.0:
                self.absolute = True
            elif g == 91.0:
                self.absolute = False
            elif g == 90.1:
                self.arc_absolute = True
            elif g == 91.1:
                self.arc_absolute = False
            elif g in (17.0, 18.0, 19.0):
                self.plane = ig
            elif g in MOTION or g in CANNED:
                self.motion = g
            elif g == 80.0:
                self.motion = None
            elif g == 92.0:
                g92 = True
            elif ig == 4:
                dwell = True
            elif ig in (10, 28, 30, 53):
                no_move = True
            elif ig == 38:
                point_only = True

        if self.reflect and self.similar and self.plane == 17:
            for g, s, e in gl:
                if g in (2.0, 3.0):
                    t = body[s:e]
                    edits.append((s, e, t[:-1] + ('3' if t[-1] == '2' else '2')))

        if dwell or no_move:
            if no_move and ('X' in w or 'Y' in w):
                self.warn('G53/G28/G30/G10 lines with X/Y are not transformed.')
            return self._apply(body, eol, edits, [])
        if g92:
            t = self._target(w, True)
            if t is not None:
                self._put(body, w, edits, 'X', 'Y', *self.pt(*t))
                self.cur[0], self.cur[1] = t
            return self._apply(body, eol, edits, [])

        motion = self.motion
        if motion is None and not point_only:
            return self._apply(body, eol, edits, [])
        is_arc = motion in (2.0, 3.0) and not point_only and ('I' in w or 'J' in w or 'K' in w or 'R' in w)
        if is_arc and self.plane != 17:
            if self.linear_id:
                # a translation: the arc keeps its plane; only a centre given as a point (G90.1) moves
                if self.arc_absolute:
                    for letter, shift in (('I', self.xo), ('J', self.yo)):
                        wd = w.get(letter)
                        if wd is not None and shift != 0.0 and letter in PLANE_AXES[self.plane][2:4]:
                            edits.append((wd[1], wd[2], body[wd[1]] + _fmt(wd[0] + shift, self.dec)))
                self._endpoint(body, w, edits, self.absolute)
                self._z(w, False)
                return self._apply(body, eol, edits, [])
            # any other transform takes the arc out of its plane: straight segments in 3D
            extra = self._linearize_3d(body, w, gl, edits)
            if extra is not None:
                self.warn('Arcs in the G18/G19 planes were replaced by straight segments.')
                return self._apply(body, eol, edits, extra)
            self.warn('An arc in the G18/G19 planes starts at an unknown position: it is not transformed.')
            is_arc = False
        if not is_arc:
            self._endpoint(body, w, edits, self.absolute)
            self._z(w, motion in CANNED)
            return self._apply(body, eol, edits, [])

        if self.similar:
            self._endpoint(body, w, edits, self.absolute)
            if 'R' in w and 'I' not in w and 'J' not in w:
                if self.scale != 1.0:
                    s, e = w['R'][1], w['R'][2]
                    edits.append((s, e, body[s] + _fmt(w['R'][0] * self.scale, self.dec)))
            else:
                i = w['I'][0] if 'I' in w else 0.0
                j = w['J'][0] if 'J' in w else 0.0
                ni, nj = self.pt(i, j) if self.arc_absolute else self.vec(i, j)
                self._put(body, w, edits, 'I', 'J', ni, nj)
            self._z(w, False)
            return self._apply(body, eol, edits, [])

        self.warn('Arcs were replaced by straight segments: the transform is not a rotation, scale or mirror.')
        extra = self._linearize(body, w, gl, edits)
        return self._apply(body, eol, edits, extra)

    def _linearize(self, body, w, gl, edits):
        """Replaces the arc of this line by G1 segments. Returns the text of the additional lines."""
        sx, sy = self.cur[0], self.cur[1]
        t = self._target(w, self.absolute)
        cw = self.motion == 2.0
        if t is None:
            t = (0.0, 0.0) if sx is None or sy is None else (sx, sy)
        if sx is None or sy is None:
            sx, sy = t
            self.warn('The start of an arc was unknown: its target was used.')
        ex, ey = t
        if 'R' in w and 'I' not in w and 'J' not in w:
            c = _arc_centre_r(cw, sx, sy, ex, ey, w['R'][0])
            pts = [(ex, ey)] if c is None else _arc_points(cw, sx, sy, ex, ey, c[0], c[1], self.steps)
        else:
            i = w['I'][0] if 'I' in w else 0.0
            j = w['J'][0] if 'J' in w else 0.0
            c = (i, j) if self.arc_absolute else (sx + i, sy + j)
            pts = _arc_points(cw, sx, sy, ex, ey, c[0], c[1], self.steps)

        z0 = self.cur[2] if self.cur[2] is not None else 0.0
        has_z = 'Z' in w
        z1 = z0
        if has_z:
            z1 = w['Z'][0] if self.absolute else z0 + w['Z'][0]
        n = len(pts)
        dec = self.dec
        prev = tuple(float(_fmt(v, dec)) for v in self.pt(sx, sy))
        pz = z0
        segs = []
        for k, (px, py) in enumerate(pts):
            nx, ny = (float(_fmt(v, dec)) for v in self.pt(px, py))
            z = z0 + (z1 - z0) * (k + 1) / n
            if self.absolute:
                words = 'X%s Y%s' % (_fmt(nx, dec), _fmt(ny, dec))
                if has_z:
                    words += ' Z%s' % _fmt(z, dec)
            else:
                words = 'X%s Y%s' % (_fmt(nx - prev[0], dec), _fmt(ny - prev[1], dec))
                if has_z:
                    words += ' Z%s' % _fmt(z - pz, dec)
            prev = (nx, ny)
            pz = z
            segs.append(words)

        self.cur[0], self.cur[1] = ex, ey
        self._z(w, False)
        return self._replace_arc(body, w, gl, edits, segs)

    def _linearize_3d(self, body, w, gl, edits):
        """
        Replaces the G18/G19 arc of this line by G1 segments with X Y Z. Returns the text of the additional lines,
        None if the start or the end of the arc is not known.
        """
        s = list(self.cur)
        if None in s:
            return None
        t_xy = self._target(w, self.absolute) or (s[0], s[1])
        z1 = s[2]
        if 'Z' in w:
            z1 = w['Z'][0] if self.absolute else s[2] + w['Z'][0]
        t = [t_xy[0], t_xy[1], z1]
        pts = _arc_points_3d(self.motion == 2.0, self.plane, s, t, {k: v[0] for k, v in w.items()},
                             self.arc_absolute, self.steps)
        if pts is None:
            return None

        dec = self.dec
        prev = tuple(float(_fmt(v, dec)) for v in self.pt(s[0], s[1])) + (s[2],)
        segs = []
        for p in pts:
            nx, ny = (float(_fmt(v, dec)) for v in self.pt(p[0], p[1]))
            if self.absolute:
                segs.append('X%s Y%s Z%s' % (_fmt(nx, dec), _fmt(ny, dec), _fmt(p[2], dec)))
            else:
                segs.append('X%s Y%s Z%s' % (_fmt(nx - prev[0], dec), _fmt(ny - prev[1], dec),
                                            _fmt(p[2] - prev[2], dec)))
            prev = (nx, ny, p[2])

        self.cur = t
        return self._replace_arc(body, w, gl, edits, segs)

    @staticmethod
    def _replace_arc(body, w, gl, edits, segs):
        """The first segment goes on the line in place of the arc words, the others are new G1 lines."""
        remove = [wd for key, wd in w.items() if key in 'XYZIJKR']
        first_pos = min(wd[1] for wd in remove)
        garc = next(((s, e) for g, s, e in gl if g in (2.0, 3.0)), None)
        for wd in remove:
            s, e = wd[1], wd[2]
            if s == first_pos:
                edits.append((s, e, ('' if garc else 'G1 ') + segs[0]))
            else:
                while s > 0 and body[s - 1] in ' \t':
                    s -= 1
                edits.append((s, e, ''))
        if garc:
            edits.append((garc[0], garc[1], body[garc[0]] + '1'))
        return ['G1 ' + sgm for sgm in segs[1:]]

    @staticmethod
    def _apply(body, eol, edits, extra):
        for s, e, t in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
            body = body[:s] + t + body[e:]
        if extra:
            sep = '\r\n' if eol == '\r\n' else '\n'
            body = body + sep + sep.join(extra)
        return body + eol


def transform_gcode(text, matrix, decimals=4, steps_per_circle=64):
    """
    Applies an affine transform to the X/Y coordinates of a G-code text. Comments, line endings, line order and
    every word that is not a coordinate of the transform are kept as they are.

    :param text:            the G-code
    :param matrix:          (a, b, d, e, xoff, yoff) as shapely.affinity.affine_transform:
                            x' = a*x + b*y + xoff, y' = d*x + e*y + yoff (in the units of the G-code)
    :param decimals:        decimals of the rewritten coordinates
    :param steps_per_circle: segments of a whole circle for an arc that has to be replaced by straight segments
    :return:                (new text, [warning])
    """
    m = tuple(float(v) for v in matrix)
    if m == AFFINE_ID:
        return text, []
    tr = _Transformer(m, decimals, steps_per_circle)
    parts = text.split('\n')
    out = [tr.line(p + '\n') for p in parts[:-1]]
    if parts[-1]:
        out.append(tr.line(parts[-1]))
    return ''.join(out), tr.warnings
