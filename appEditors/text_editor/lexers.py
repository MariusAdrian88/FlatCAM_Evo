"""
Lexers of the text editor. A lexer is a list of highlight rules plus the comment syntax of its language and the
regions of the text that can be folded; the LazyHighlighter applies the rules and takes the colors of the token names
from an EditorPalette.
"""

import re

from PyQt6 import QtCore

# tokens shown in bold
BOLD_TOKENS = frozenset(('gcode_g', 'gcode_m', 'tool', 'feed', 'axis', 'z_axis', 'keyword', 'delimiter', 'param',
                         'gerber_dcode', 'tcl_command'))


def _rx(pattern, inverted_greediness=False):
    rx = QtCore.QRegularExpression(pattern)
    if inverted_greediness:
        rx.setPatternOptions(QtCore.QRegularExpression.PatternOption.InvertedGreedinessOption)
    return rx


class Lexer:
    """
    :ivar name:             name of the language, shown in the status bar
    :ivar rules:            list of (QRegularExpression, token name); the FIRST rule has the priority over the next
    :ivar comment_prefix:   what starts a comment line ('' = the language has no comment)
    :ivar comment_suffix:   what ends it ('' = the comment goes to the end of the line)
    """
    name = 'Text'
    comment_prefix = ''
    comment_suffix = ''

    def __init__(self):
        self.rules = []

    def fold_regions(self, text):
        """
        Regions that can be folded: a sorted list of (first line, last line), 0-based block numbers, both included,
        last > first. Regions can nest.
        """
        return []


class PlainLexer(Lexer):
    """No rules, no comment, no fold regions."""
    name = 'Plain text'


def _lines(text):
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines


def _blocks(starts, count):
    """Regions that run from each start line to the line before the next start (the last one to the end)."""
    regions = []
    for index, start in enumerate(starts):
        end = (starts[index + 1] if index + 1 < len(starts) else count) - 1
        if end > start:
            regions.append((start, end))
    return regions


# ------------------------------------------------------------------------------------------------------------------
# G-code
# ------------------------------------------------------------------------------------------------------------------
_GCODE_TOOL_CHANGE = re.compile(r'[ \t]*(?:N\d+[ \t]*)?(?:(T)\d+|(M)0?6\b)')
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)"


class GCodeLexer(Lexer):
    name = 'G-code'
    comment_prefix = '('
    comment_suffix = ')'

    def __init__(self):
        super().__init__()
        rules = self.rules
        rules.append((_rx(r"\([^)]*(?:\)|$)"), 'comment'))
        rules.append((_rx(r";.*$"), 'comment'))
        rules.append((_rx(r"\"[^\"]*\"?"), 'string'))
        rules.append((_rx(r"^[ \t]*N\d+"), 'line_number'))
        rules.append((_rx(r"^%"), 'delimiter'))
        rules.append((_rx(r"(?<![A-Za-z])G\d+(?:\.\d+)?"), 'gcode_g'))
        rules.append((_rx(r"(?<![A-Za-z])M\d+"), 'gcode_m'))
        rules.append((_rx(r"(?<![A-Za-z])T\d+"), 'tool'))
        rules.append((_rx(r"(?<![A-Za-z])[FS]" + _NUMBER), 'feed'))
        rules.append((_rx(r"(?<![A-Za-z])Z" + _NUMBER), 'z_axis'))
        rules.append((_rx(r"(?<![A-Za-z])[XYIJKABC]" + _NUMBER), 'axis'))

    def fold_regions(self, text):
        lines = _lines(text)
        count = len(lines)
        match = _GCODE_TOOL_CHANGE.match
        starts = []
        last_t_only = -2
        for number, line in enumerate(lines):
            m = match(line)
            if m is None:
                continue
            if m.group(2) and number == last_t_only + 1:
                # 'T1' on a line and 'M6' on the next one: a single tool change
                last_t_only = -2
                continue
            starts.append(number)
            last_t_only = number if m.group(1) and 'M' not in line else -2
        if not starts:
            return []
        regions = []
        if starts[0] > 1:
            regions.append((0, starts[0] - 1))
        regions.extend(_blocks(starts, count))
        return regions


# ------------------------------------------------------------------------------------------------------------------
# Gerber
# ------------------------------------------------------------------------------------------------------------------
class GerberLexer(Lexer):
    name = 'Gerber'
    comment_prefix = 'G04 '
    comment_suffix = '*'

    def __init__(self):
        super().__init__()
        rules = self.rules
        rules.append((_rx(r"^[ \t]*G04[^*]*\*?"), 'comment'))
        rules.append((_rx(r"%T[FADO][^*%]*\*?%?"), 'attribute'))
        rules.append((_rx(r"%[^%]*%?"), 'gerber_extended'))
        rules.append((_rx(r"^[^%]*\*%"), 'gerber_extended'))
        rules.append((_rx(r"D\d+(?=\*)"), 'gerber_dcode'))
        rules.append((_rx(r"G\d+"), 'gcode_g'))
        rules.append((_rx(r"M\d+"), 'gcode_m'))
        rules.append((_rx(r"[XYIJ][-+]?\d+(?:\.\d+)?"), 'axis'))
        rules.append((_rx(r"\*"), 'delimiter'))

    def fold_regions(self, text):
        regions = []
        macro_start = None
        region_start = None
        for number, line in enumerate(text.split('\n')):
            if macro_start is not None:
                if '%' in line:
                    if number > macro_start:
                        regions.append((macro_start, number))
                    macro_start = None
                continue
            if '%' in line:
                if line.lstrip().startswith('%AM') and line.count('%') < 2:
                    macro_start = number
                continue
            if 'G3' not in line:
                continue
            if 'G36' in line and not line.lstrip().startswith('G04'):
                region_start = number
            elif 'G37' in line and region_start is not None:
                if number > region_start:
                    regions.append((region_start, number))
                region_start = None
        return regions


# ------------------------------------------------------------------------------------------------------------------
# Excellon
# ------------------------------------------------------------------------------------------------------------------
_EXC_TOOL_LINE = re.compile(r'[ \t]*T\d+')


class ExcellonLexer(Lexer):
    name = 'Excellon'
    comment_prefix = ';'
    comment_suffix = ''

    def __init__(self):
        super().__init__()
        rules = self.rules
        rules.append((_rx(r";.*$"), 'comment'))
        rules.append((_rx(r"^[ \t]*(?:M48|M95|M30|M71|M72|%)[ \t]*$"), 'keyword'))
        rules.append((_rx(r"^[ \t]*(?:METRIC|INCH|FMAT|ICI|VER)\b[^;]*"), 'keyword'))
        rules.append((_rx(r"T\d+(?:C[\d.]+)?(?:[FSB][\d.]+)*"), 'tool'))
        rules.append((_rx(r"G\d+"), 'gcode_g'))
        rules.append((_rx(r"M\d+"), 'gcode_m'))
        rules.append((_rx(r"[XY][-+]?[\d.]+"), 'axis'))

    def fold_regions(self, text):
        lines = text.split('\n')
        regions = []
        body_start = 0
        header_start = None
        for number, line in enumerate(lines):
            stripped = line.strip()
            if header_start is None:
                if stripped == 'M48':
                    header_start = number
                elif _EXC_TOOL_LINE.match(line) and 'C' not in line:
                    break
                continue
            if stripped == '%' or stripped == 'M95':
                if number > header_start:
                    regions.append((header_start, number))
                body_start = number + 1
                break
        starts = []
        end_line = len(lines)
        if lines and lines[-1] == '':
            end_line -= 1
        for number in range(body_start, len(lines)):
            line = lines[number]
            if _EXC_TOOL_LINE.match(line) and 'C' not in line:
                starts.append(number)
            elif line.strip() == 'M30':
                end_line = number
                break
        for index, start in enumerate(starts):
            end = (starts[index + 1] if index + 1 < len(starts) else end_line) - 1
            if end > start:
                regions.append((start, end))
        return regions


# ------------------------------------------------------------------------------------------------------------------
# Tcl / FlatScript
# ------------------------------------------------------------------------------------------------------------------
TCL_BUILTINS = ('set', 'proc', 'if', 'elseif', 'else', 'for', 'foreach', 'while', 'switch', 'break', 'continue',
                'return', 'expr', 'puts', 'incr', 'global', 'upvar', 'catch', 'list', 'lappend', 'llength', 'lindex',
                'string', 'format', 'source', 'after', 'error')

_TCL_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|\\[^\n]|(?:^|;)[ \t]*#[^\n]*|\{(?:[ \t]*#[^\n]*)?|\}|\n',
                         re.MULTILINE | re.DOTALL)


class TclLexer(Lexer):
    name = 'Tcl'
    comment_prefix = '#'
    comment_suffix = ''

    def __init__(self, keywords=None):
        super().__init__()
        rules = self.rules
        rules.append((_rx(r"(?:^|;)[ \t]*#.*$"), 'comment'))
        rules.append((_rx(r"\"(?:[^\"\\]|\\.)*\"?"), 'string'))
        rules.append((_rx(r"\$\{?[A-Za-z_][\w:]*\}?"), 'variable'))
        words = sorted({str(w) for w in (keywords or ()) if str(w).strip()}, key=lambda w: (-len(w), w))
        if words:
            rules.append((_rx(r"(?<![\w-])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w-])"),
                          'tcl_command'))
        rules.append((_rx(r"(?<![\w-])(?:" + "|".join(TCL_BUILTINS) + r")(?![\w-])"), 'keyword'))
        rules.append((_rx(r"(?<![\w-])-[A-Za-z_]\w*"), 'param'))
        rules.append((_rx(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])"), 'number'))
        rules.append((_rx(r"[{}\[\]]"), 'delimiter'))

    def fold_regions(self, text):
        regions = []
        stack = []
        line = 0
        for match in _TCL_TOKENS.finditer(text):
            token = match.group(0)
            first = token[0]
            if first == '\n':
                line += 1
            elif first == '{':
                stack.append(line)
            elif first == '}':
                if stack:
                    start = stack.pop()
                    if line > start:
                        regions.append((start, line))
            elif first == '"':
                line += token.count('\n')
        regions.sort(key=lambda region: (region[0], -region[1]))
        return regions


# ------------------------------------------------------------------------------------------------------------------
# lookup and detection
# ------------------------------------------------------------------------------------------------------------------
LEXER_CLASSES = {
    'gcode': GCodeLexer,
    'gerber': GerberLexer,
    'excellon': ExcellonLexer,
    'tcl': TclLexer,
    'plain': PlainLexer,
}


def get_lexer(language=None, keywords=None):
    """
    The lexer of a language name: 'gcode', 'gerber', 'excellon', 'tcl' (`keywords` = the Tcl command names of the
    app) or 'plain'. An unknown name or None gives the plain text lexer.
    """
    if language == 'tcl':
        return TclLexer(keywords)
    return LEXER_CLASSES.get(language, PlainLexer)()


_DETECT = (
    ('gerber', re.compile(r"%FS|%MO|%ADD\d|%TF|%LP|^G04 |D\d+\*|^M02\*", re.MULTILINE)),
    ('excellon', re.compile(r"^M48|^(?:METRIC|INCH)\b|^T\d+C[\d.]+", re.MULTILINE)),
    ('gcode', re.compile(r"^(?![^\n]*\*[ \t]*$)[ \t]*(?:N\d+[ \t]*)?(?:G0?[0-3]\b[^\n]*[XYZ]-?[\d.]"
                         r"|M0?[35]\b|G(?:20|21|90|91|17)\b)", re.MULTILINE)),
    ('tcl', re.compile(r"^[ \t]*(?:(?:set|proc|puts|foreach|while|expr|source|open_\w+|new_\w+|export_\w+|add_\w+"
                       r"|import_\w+|isolate|cncjob|drillcncjob|paint|ncc|write_gcode|save_project)[ \t]"
                       r"|\w+[ \t][^\n]*[ \t]-[A-Za-z_]\w*)", re.MULTILINE)),
)


def detect_language(text):
    """
    The language of a text from its first 200 lines: 'gerber', 'excellon', 'gcode', 'tcl' or 'plain'.
    """
    head = '\n'.join(text.split('\n', 200)[:200])
    best = 'plain'
    best_score = 0
    for language, rx in _DETECT:
        score = len(rx.findall(head))
        if score > best_score:
            best, best_score = language, score
    return best
