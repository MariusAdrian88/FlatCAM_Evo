# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/15/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Pure G-code helpers of the GRBL Sender: split long G1 moves, and make the
3D toolpath with the line index of each segment.

No Qt imports, no app imports - standard library only.
"""

import math
import re

_WORD_RE = re.compile(r'([A-Z])\s*([+\-]?(?:\d+\.?\d*|\.\d+))')
_COMMENT_RE = re.compile(r'\([^)]*\)')

# G words of a block that can be split (G1 is the split move itself)
_SPLIT_G_KEYS = ('1.0', '17.0', '90.0', '94.0')
# no effect on the position, the distance mode or the units
_NO_EFFECT_G_KEYS = ('4.0', '17.0', '18.0', '19.0', '40.0', '61.0', '64.0', '98.0', '99.0')
_MOTION_G_KEYS = ('0.0', '1.0', '2.0', '3.0')
# X, Y, Z are unknown after them (the one-shot and frame codes of level_gcode_line)
_LOSE_G_KEYS = ('10.0', '28.0', '30.0', '53.0', '54.0', '55.0', '56.0', '57.0', '58.0', '59.0',
                '59.1', '59.2', '59.3', '92.0', '92.1')


def _words(line):
    """:return: list of (letter, float) of the words of `line`; G numbers keep their decimals (38.2)."""
    return [(m.group(1), float(m.group(2))) for m in _WORD_RE.finditer(line.upper())]


def _fmt(value, decimals):
    text = '%.*f' % (decimals, value)
    return '0.' + '0' * decimals if text.startswith('-') and float(text) == 0.0 else text


def _g_key(g):
    """The G number as a rounded string: 38.2 -> '38.2', 1 -> '1.0'."""
    return '%.1f' % g


class _ModalState:
    """
    The modal state of a G-code stream, with the rules of level_gcode_line():
    the motion mode, G90/G91, G93/G94, the units and X/Y/Z. An axis that is
    None is not known. Used by split_long_moves() and parse_toolpath().
    """

    def __init__(self, pos=None):
        self.pos = {'X': None, 'Y': None, 'Z': None} if pos is None else dict(zip('XYZ', pos))
        self.motion = 0         # modal G0..G3 (as level_gcode_line), None: not known
        self.dist = 90          # 90, 91, or None: not known
        self.inverse = False    # G93 True, G94 False, None: not known
        self.units = None       # 20, 21, or None: not known

    def lose(self, axes=('X', 'Y', 'Z')):
        for axis in axes:
            self.pos[axis] = None

    def advance(self, line):
        """
        Apply 1 line to the state.

        :return: None if the line does not change the state (blank, comment, "%"),
            else a dict with the keys: words, g_words, letters, axes, keys, start
            (the position before the line), blocked (the block makes a position
            unknown).
        """
        pos = self.pos
        if line.strip().startswith('$'):
            # a GRBL setting or command line
            self.lose()
            self.motion = None
            return None
        masked = _COMMENT_RE.sub(' ', line)
        idx = masked.find(';')
        if idx != -1:
            masked = masked[:idx]
        if not masked.strip() or masked.strip() == '%':
            return None
        if masked.strip().startswith('/'):
            # an optional line: the controller can skip it, so every modal setting that
            # it could change is not known
            self.lose()
            self.motion = self.dist = self.inverse = self.units = None
            return None

        upper = masked.upper()
        words = _words(masked)
        # text that is not a word (an expression, a parameter) or lower case letters
        unparsed = (masked != upper) or bool(re.sub(r'[\s%]', '', _WORD_RE.sub('', upper)))
        g_words = [v for k, v in words if k == 'G']
        letters = {k for k, _v in words}
        axes = {k: v for k, v in words if k in 'XYZ'}
        keys = [_g_key(g) for g in g_words]
        start = dict(pos)
        # `blocked`: the block makes a position unknown
        blocked = False
        for key in keys:
            if key in _MOTION_G_KEYS:
                self.motion = int(float(key))
            elif key == '80.0':
                self.motion = None
            elif key in _NO_EFFECT_G_KEYS:
                pass
            elif key == '90.0':
                self.dist = 90
            elif key == '91.0':
                self.dist = 91
            elif key == '93.0':
                self.inverse = True
            elif key == '94.0':
                self.inverse = False
            elif key in ('20.0', '21.0'):
                value = int(float(key))
                if value != self.units:
                    if self.units is not None or any(v is not None for v in pos.values()):
                        blocked = True
                    self.lose()
                self.units = value
            elif key in ('43.1', '49.0'):
                blocked = True
                self.lose(['Z'])
            elif key in ('38.2', '38.3', '38.4', '38.5'):
                blocked = True
                self.lose()
                self.motion = None
            elif key in _LOSE_G_KEYS:
                blocked = True
                self.lose()
            else:
                # any other G code: it can be a motion, a frame or a mode change
                blocked = True
                self.lose()
                self.motion = None
        # M2 and M30 end the program and reset the modal state
        if any(k == 'M' and v in (2.0, 30.0) for k, v in words):
            blocked = True
            self.lose()
            self.motion = None
            self.dist = 90
            self.inverse = False
        if unparsed:
            blocked = True
            self.lose()
            self.motion = None
            if upper.count('G') > len(g_words):
                # a G word that is not read can be any G code
                self.dist = self.inverse = self.units = None

        # the axis words: only an absolute G0..G3 move makes an axis known
        if axes:
            if blocked or self.motion not in (0, 1, 2, 3):
                self.lose(axes.keys())
            elif self.dist == 90:
                pos.update(axes)
            elif self.dist == 91:
                for k, v in axes.items():
                    if pos[k] is not None:
                        pos[k] += v
            else:
                self.lose(axes.keys())
        return {'words': words, 'g_words': g_words, 'letters': letters, 'axes': axes,
                'keys': keys, 'start': start, 'blocked': blocked}


def split_long_moves(lines, max_len, decimals=4):
    """
    Split each absolute G1 move with X or Y that is longer than `max_len`
    into equal G1 moves, so a height map can level the cut between its ends.

    The modal state is tracked with the rules of level_gcode_line(): the
    motion mode, G90/G91, G93/G94, the units and X/Y/Z. A line is left
    unchanged if it is not a G1 move, if the distance mode after its G words
    is not a confirmed G90, if the start or the end point is not known, if
    its G words are not in {G1, G17, G90, G94} (a line with no G word is
    split when the modal motion is G1), or if it has other words than G, X,
    Y, Z, F, N. A line that makes a position unknown (probe, G53, G92,
    G10, G28, G30, a work coordinate system or tool length change, a units
    change, an unknown G code, M2, M30, a "$" line) makes it unknown: only a
    later absolute G0..G3 move makes an axis known again, axis by axis. The
    first piece starts with the non-motion G words of the block, the other
    pieces start with G1. The feed word stays on the first piece only (it is
    modal).

    :param lines:    list of str (clean lines, see SenderGen.clean_lines()).
    :param max_len:  the longest XY length of 1 piece (> 0).
    :param decimals: decimals of the new X/Y/Z words.
    :return: (new_lines, count of split moves)
    """
    if not max_len > 0:
        raise ValueError("max_len must be > 0")
    state = _ModalState()
    pos = state.pos
    out = []
    split_count = 0

    for line in lines:
        info = state.advance(line)
        if info is None:
            out.append(line)
            continue
        words = info['words']
        letters = info['letters']
        axes = info['axes']
        keys = info['keys']
        start = info['start']
        blocked = info['blocked']
        nonmotion_keys = [key for key in keys if key != '1.0']

        can_split = (
            not blocked and
            state.motion == 1 and state.dist == 90 and state.inverse is False and
            ('X' in axes or 'Y' in axes) and
            letters <= set('GXYZFN') and all(k in _SPLIT_G_KEYS for k in keys) and
            None not in (start['X'], start['Y'], start['Z']) and
            None not in (pos['X'], pos['Y'], pos['Z'])
        )
        if not can_split:
            out.append(line)
            continue
        length = math.hypot(pos['X'] - start['X'], pos['Y'] - start['Y'])
        pieces = int(math.ceil(length / max_len - 1e-9))
        if pieces <= 1:
            out.append(line)
            continue
        feed = [v for k, v in words if k == 'F']
        prefix = ''.join('G%d ' % int(float(key)) for key in nonmotion_keys)
        for n in range(1, pieces + 1):
            t = n / pieces
            x = start['X'] + (pos['X'] - start['X']) * t
            y = start['Y'] + (pos['Y'] - start['Y']) * t
            z = start['Z'] + (pos['Z'] - start['Z']) * t
            piece = 'G1 X%s Y%s Z%s' % (_fmt(x, decimals), _fmt(y, decimals), _fmt(z, decimals))
            if n == 1:
                piece = prefix + piece
                if feed:
                    piece += ' F%s' % _fmt(feed[0], 2)
            out.append(piece)
        split_count += 1
    return out, split_count


def parse_toolpath(lines, start=None):
    """
    The 3D toolpath of stream lines: 1 straight segment for each G0..G3 line
    that moves. Arcs must be split before (SenderGen.split_arcs()); a G2/G3
    line gives 1 straight segment (the chord) from its start to its end point.

    The position is tracked with the rules of split_long_moves(). A segment
    is made only if the start and the end point are known (all 3 axes): after
    a line that makes the position unknown (probe, G53, G92, G28, ...) there
    is no segment until X, Y and Z are known again.

    :param lines: list of str.
    :param start: the start position (x, y, z), or None if it is not known.
    :return: list of (line_index, (x0, y0, z0), (x1, y1, z1), rapid) where
        rapid is True for G0.
    """
    state = _ModalState(start)
    pos = state.pos
    segments = []
    for index, line in enumerate(lines):
        info = state.advance(line)
        if info is None or not info['axes'] or info['blocked']:
            continue
        if state.motion not in (0, 1, 2, 3):
            continue
        p0 = (info['start']['X'], info['start']['Y'], info['start']['Z'])
        p1 = (pos['X'], pos['Y'], pos['Z'])
        if None in p0 or None in p1 or p0 == p1:
            continue
        segments.append((index, p0, p1, state.motion == 0))
    return segments
