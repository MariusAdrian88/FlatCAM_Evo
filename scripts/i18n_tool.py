"""
Translations of the application: extract its strings, update the .po files, compile the .mo files, check them.
Standard library only (no gettext tools needed).

    python scripts/i18n_tool.py extract              # locale_template/strings.pot from the source code
    python scripts/i18n_tool.py merge tr [de ...]    # locale/<lang>/LC_MESSAGES/strings.po updated from the code
    python scripts/i18n_tool.py compile [tr ...]     # locale/<lang>/LC_MESSAGES/strings.mo (all languages if none)
    python scripts/i18n_tool.py compile --check      # which .mo files miss changes of their .po (nothing written)
    python scripts/i18n_tool.py check [tr ...]       # untranslated and fuzzy entries, placeholder errors, warnings
    python scripts/i18n_tool.py init ar              # a new language: locale/ar/LC_MESSAGES/strings.po, untranslated
    python scripts/i18n_tool.py stats [--markdown F] # how complete every language is
    python scripts/i18n_tool.py glossary [--out F]   # the key terms (locale_template/glossary_terms.txt) translated

merge keeps the translations of the strings still in the code, adds the new strings untranslated and keeps the strings
no longer in the code as obsolete entries (#~); an obsolete entry whose string comes back is used again.
check, compile and stats work on all languages when none is named. check exits with 1 when a translation makes the
program fail as it formats the message (an error), so it can run in CI; the differences that the program shows as
they are (HTML tags, <argument> names, line breaks, a placeholder written differently) are printed as warnings.
"""

import argparse
import ast
import csv
import datetime
import os
import re
import struct
import sys
from collections import OrderedDict
from string import Formatter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOMAIN = 'strings'
TEMPLATE = os.path.join(ROOT, 'locale_template', DOMAIN + '.pot')
# folders whose code is not extracted (and every folder whose name starts with a dot: .git, .venv ...)
SKIP_DIRS = {'venv', '__pycache__', 'libs', 'tests', 'build', 'dist', 'locale', 'locale_template', 'doc', 'docs',
             'assets', 'scripts'}
GLOSSARY_TERMS = os.path.join(ROOT, 'locale_template', 'glossary_terms.txt')
# gettext plural rules; the code has no ngettext() call today, the header is there for the translation tools
PLURAL_FORMS = {
    'ar': 'nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5);',
    'fr': 'nplurals=2; plural=(n > 1);',
    'pt_BR': 'nplurals=2; plural=(n > 1);',
    'ro': 'nplurals=3; plural=(n==1 ? 0 : (n==0 || (n%100 > 0 && n%100 < 20)) ? 1 : 2);',
    'ru': 'nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);',
    'tr': 'nplurals=2; plural=(n != 1);',
    'zh': 'nplurals=1; plural=0;',
}


def po_path(lang):
    return os.path.join(ROOT, 'locale', lang, 'LC_MESSAGES', DOMAIN + '.po')


def languages():
    """The languages that have a .po file (English included: its translations are the English texts)."""
    folder = os.path.join(ROOT, 'locale')
    return sorted(d for d in os.listdir(folder) if os.path.isfile(po_path(d)))


# ---------------------------------------------------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------------------------------------------------

def source_files(root=ROOT):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.'))
        for name in sorted(filenames):
            if name.endswith('.py'):
                yield os.path.join(dirpath, name)


def extract(root=ROOT):
    """
    :return: (OrderedDict msgid -> list of 'file:line', list of 'file:line: problem' for _() calls whose argument is
             not a string literal: they can not be translated)
    """
    strings = OrderedDict()
    problems = []
    for path in source_files(root):
        rel = os.path.relpath(path, root).replace(os.sep, '/')
        try:
            with open(path, encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=rel)
        except (SyntaxError, UnicodeDecodeError) as err:
            problems.append('%s: not parsed: %s' % (rel, err))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # _() and N_() (the marker of texts that are translated later, as in grbl_messages.py)
            if not (isinstance(node.func, ast.Name) and node.func.id in ('_', 'N_')):
                continue
            if len(node.args) != 1:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value:
                    strings.setdefault(arg.value, []).append('%s:%d' % (rel, node.lineno))
            else:
                problems.append('%s:%d: _() of a %s, not a string literal' % (rel, node.lineno, type(arg).__name__))
    # the order of the files then of the lines (ast.walk() is breadth first)
    for msgid in strings:
        strings[msgid].sort(key=lambda ref: (ref.rsplit(':', 1)[0], int(ref.rsplit(':', 1)[1])))
    ordered = OrderedDict(sorted(strings.items(), key=lambda kv: (kv[1][0].rsplit(':', 1)[0],
                                                                  int(kv[1][0].rsplit(':', 1)[1]))))
    return ordered, problems


# ---------------------------------------------------------------------------------------------------------------------
# .po files
# ---------------------------------------------------------------------------------------------------------------------

class Entry:
    def __init__(self, msgid='', msgstr='', refs=None, flags=None, comments=None, obsolete=False, previous=None):
        self.msgid = msgid
        self.msgstr = msgstr
        self.refs = refs or []          # 'file:line'
        self.flags = flags or []        # 'fuzzy', 'python-format', ...
        self.comments = comments or []  # translator comments, without '# '
        self.obsolete = obsolete
        # the '#|' lines, without '#| ': the previous English text of a fuzzy entry (Poedit shows it to the translator)
        self.previous = previous or []

    @property
    def fuzzy(self):
        return 'fuzzy' in self.flags


_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', 'a': '\a', 'b': '\b', 'f': '\f', 'v': '\v'}


def unescape(text):
    out = []
    k = 0
    while k < len(text):
        c = text[k]
        if c == '\\' and k + 1 < len(text):
            nxt = text[k + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                k += 2
                continue
            if nxt in '01234567':
                m = re.match(r'[0-7]{1,3}', text[k + 1:])
                out.append(chr(int(m.group(0), 8)))
                k += 1 + len(m.group(0))
                continue
        out.append(c)
        k += 1
    return ''.join(out)


def escape(text):
    return text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\t', '\\t') \
        .replace('\r', '\\r')


def read_po(path):
    """:return: (header Entry, list of Entry) in the file order (obsolete entries included)"""
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()
    entries = []
    current = None
    field = None

    def finish():
        nonlocal current, field
        if current is not None and (current.msgid or current.msgstr or entries == []):
            entries.append(current)
        current, field = None, None

    def parse_string(text, line_number):
        if not re.fullmatch(r'"(?:[^"\\]|\\.)*"', text):
            raise ValueError('%s:%d: invalid PO string: %s' % (path, line_number, text))
        return unescape(text[1:-1])

    for line_number, raw in enumerate(lines + [''], 1):
        line = raw.strip()
        if not line:
            if current is not None and field is not None:
                finish()
            continue
        obsolete = line.startswith('#~')
        if obsolete:
            line = line[2:].strip()
            if not line:
                continue
            if line.startswith('|'):
                # '#~|': the previous English text of an obsolete entry
                line = '#' + line
        if current is None:
            current = Entry(obsolete=obsolete)
        if line.startswith('#:'):
            current.refs.extend(line[2:].split())
        elif line.startswith('#,'):
            current.flags.extend(fl.strip() for fl in line[2:].split(',') if fl.strip())
        elif line.startswith('#|'):
            current.previous.append(line[2:].strip())
        elif line.startswith('#.'):
            pass
        elif line.startswith('#'):
            current.comments.append(line[1:].strip())
        elif line.startswith('msgid '):
            if field == 'msgstr':
                # a new entry without a blank line before it
                finish()
                current = Entry(obsolete=obsolete)
            # (the comments of an obsolete entry are not marked with #~, its msgid is)
            current.obsolete = obsolete
            field = 'msgid'
            current.msgid = parse_string(line[6:].strip(), line_number)
        elif line.startswith('msgstr '):
            field = 'msgstr'
            current.msgstr = parse_string(line[7:].strip(), line_number)
        elif line.startswith('"') and field:
            setattr(current, field, getattr(current, field) + parse_string(line, line_number))
        else:
            raise ValueError('%s:%d: invalid or unsupported PO line: %s' % (path, line_number, raw))
    finish()
    header = entries[0] if entries and entries[0].msgid == '' else Entry()
    body = entries[1:] if entries and entries[0].msgid == '' else entries
    return header, body


def _po_string(keyword, text, prefix=''):
    """The lines of a msgid / msgstr: one line, or "" and a line per text line, long lines wrapped at spaces."""
    if '\n' not in text[:-1] and len(keyword) + len(escape(text)) + 3 <= 79:
        return ['%s%s "%s"' % (prefix, keyword, escape(text))]
    out = ['%s%s ""' % (prefix, keyword)]
    for piece in re.split(r'(?<=\n)', text):
        if not piece:
            continue
        esc = escape(piece)
        while len(esc) > 76:
            cut = esc.rfind(' ', 0, 76)
            if cut <= 0:
                cut = 76
            else:
                cut += 1
            # never cut an escape sequence in two
            while cut > 1 and esc[cut - 1] == '\\' and (len(esc[:cut]) - len(esc[:cut].rstrip('\\'))) % 2 == 1:
                cut -= 1
            out.append('%s"%s"' % (prefix, esc[:cut]))
            esc = esc[cut:]
        out.append('%s"%s"' % (prefix, esc))
    return out


def write_po(path, header, entries):
    lines = []
    for c in header.comments:
        lines.append(('# ' + c).rstrip())
    if header.flags:
        lines.append('#, ' + ', '.join(header.flags))
    lines += _po_string('msgid', '')
    lines += _po_string('msgstr', header.msgstr)
    for e in entries:
        lines.append('')
        prefix = '#~ ' if e.obsolete else ''
        for c in e.comments:
            lines.append(('# ' + c).rstrip())
        if e.refs and not e.obsolete:
            ref_line = '#:'
            for ref in e.refs:
                if len(ref_line) + 1 + len(ref) > 79 and ref_line != '#:':
                    lines.append(ref_line)
                    ref_line = '#:'
                ref_line += ' ' + ref
            lines.append(ref_line)
        if e.flags:
            lines.append('#, ' + ', '.join(e.flags))
        for p in e.previous:
            lines.append(('#~| ' if e.obsolete else '#| ') + p)
        lines += _po_string('msgid', e.msgid, prefix)
        lines += _po_string('msgstr', e.msgstr, prefix)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def set_header_field(header, name, value):
    fields = header.msgstr.split('\n')
    for k, field in enumerate(fields):
        if field.startswith(name + ':'):
            fields[k] = '%s: %s' % (name, value)
            break
    else:
        fields.insert(len(fields) - 1 if fields and fields[-1] == '' else len(fields), '%s: %s' % (name, value))
    header.msgstr = '\n'.join(fields)


def now_stamp():
    return datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M%z')


def write_template(strings, path=TEMPLATE):
    header = Entry(msgstr='Project-Id-Version: FlatCAM Evo\nPOT-Creation-Date: %s\nMIME-Version: 1.0\n'
                          'Content-Type: text/plain; charset=UTF-8\nContent-Transfer-Encoding: 8bit\n'
                          'Generated-By: scripts/i18n_tool.py\n' % now_stamp(),
                   comments=['Strings of FlatCAM Evo. Made by scripts/i18n_tool.py extract.'])
    entries = [Entry(msgid=msgid, refs=refs) for msgid, refs in strings.items()]
    write_po(path, header, entries)
    return len(entries)


def merge(lang, strings):
    """
    Updates the .po of the language with the strings of the code.

    :return: dict of counts
    """
    path = po_path(lang)
    header, old = read_po(path)
    active = {e.msgid: e for e in old if not e.obsolete}
    obsolete = {e.msgid: e for e in old if e.obsolete}
    entries = []
    counts = {'kept': 0, 'revived': 0, 'new': 0, 'obsolete': 0}
    for msgid, refs in strings.items():
        prev = active.pop(msgid, None)
        if prev is None and msgid in obsolete:
            prev = obsolete.pop(msgid)
            counts['revived'] += 1
        elif prev is not None:
            counts['kept'] += 1
        if prev is not None:
            entries.append(Entry(msgid, prev.msgstr, refs, [f for f in prev.flags if f != 'python-format'],
                                 prev.comments, previous=prev.previous))
        else:
            entries.append(Entry(msgid, '', refs))
            counts['new'] += 1
    # strings no longer in the code: kept as obsolete entries (their translation may be useful again)
    for e in list(active.values()) + list(obsolete.values()):
        if e.msgstr:
            e.obsolete = True
            e.refs = []
            entries.append(e)
            counts['obsolete'] += 1
    set_header_field(header, 'PO-Revision-Date', now_stamp())
    write_po(path, header, entries)
    return counts


def init_po(lang, template=TEMPLATE):
    """
    A new language: its .po from the template, every string untranslated.

    :return: path of the new file
    """
    path = po_path(lang)
    if os.path.exists(path):
        raise FileExistsError('%s exists; use "merge %s" to update it' % (os.path.relpath(path, ROOT), lang))
    __, entries = read_po(template)
    header = Entry(msgstr='Project-Id-Version: FlatCAM Evo\nPO-Revision-Date: %s\nLast-Translator: \n'
                          'Language-Team: \nLanguage: %s\nMIME-Version: 1.0\n'
                          'Content-Type: text/plain; charset=UTF-8\nContent-Transfer-Encoding: 8bit\n'
                          'Plural-Forms: %s\n' % (now_stamp(), lang,
                                                  PLURAL_FORMS.get(lang, 'nplurals=2; plural=(n != 1);')),
                   comments=['Translation of FlatCAM Evo. Started with scripts/i18n_tool.py init %s.' % lang])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_po(path, header, [Entry(e.msgid, '', e.refs) for e in entries if not e.obsolete])
    return path


# ---------------------------------------------------------------------------------------------------------------------
# .mo files
# ---------------------------------------------------------------------------------------------------------------------

def po_messages(po_file):
    """:return: dict msgid -> msgstr of the translated, not fuzzy entries of a .po, and of its header (msgid '')"""
    header, entries = read_po(po_file)
    messages = {'': header.msgstr}
    for e in entries:
        if e.obsolete or e.fuzzy or not e.msgstr:
            continue
        messages[e.msgid] = e.msgstr
    return messages


def mo_bytes(messages):
    """:return: the GNU .mo content of the messages (dict msgid -> msgstr)"""
    keys = sorted(messages, key=lambda k: k.encode('utf-8'))
    ids = b''
    strs = b''
    offsets = []
    for k in keys:
        kb, vb = k.encode('utf-8'), messages[k].encode('utf-8')
        offsets.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b'\0'
        strs += vb + b'\0'
    n = len(keys)
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + len(ids)
    koffsets, voffsets = [], []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    output = struct.pack('<Iiiiiii', 0x950412de, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    output += struct.pack('<%di' % len(koffsets), *koffsets)
    output += struct.pack('<%di' % len(voffsets), *voffsets)
    output += ids + strs
    return output


def compile_mo(po_file, mo_file):
    """GNU .mo of the translated, not fuzzy entries (and the header). :return: number of translations"""
    messages = po_messages(po_file)
    with open(mo_file, 'wb') as f:
        f.write(mo_bytes(messages))
    return len(messages) - 1


def read_mo(mo_file):
    """:return: dict msgid -> msgstr of a GNU .mo file (made by this tool, by msgfmt or by Poedit)"""
    with open(mo_file, 'rb') as f:
        data = f.read()
    order = '<' if data[:4] == b'\xde\x12\x04\x95' else '>'
    n, koffset, voffset = struct.unpack_from(order + '3I', data, 8)
    messages = {}
    for k in range(n):
        klen, kpos = struct.unpack_from(order + '2I', data, koffset + 8 * k)
        vlen, vpos = struct.unpack_from(order + '2I', data, voffset + 8 * k)
        messages[data[kpos:kpos + klen].decode('utf-8')] = data[vpos:vpos + vlen].decode('utf-8')
    return messages


def mo_is_current(lang):
    """
    :return: True if the language's .mo holds exactly the translations of its .po. The headers are not compared:
             Poedit and msgfmt leave some header fields out of the .mo.
    """
    mo_file = po_path(lang)[:-3] + '.mo'
    if not os.path.isfile(mo_file):
        return False
    have = read_mo(mo_file)
    want = po_messages(po_path(lang))
    have.pop('', None)
    want.pop('', None)
    return have == want


# ---------------------------------------------------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------------------------------------------------

PRINTF = re.compile(r'%(?:\([A-Za-z_]\w*\))?[#0\- +]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[sdifeEgGxXorcu%]')
# HTML tags (not text markers like <<WARNING>>, and not <center>: an argument of the Tcl scale and skew commands)
TAGS = re.compile(r'</?(?:b|i|u|s|br|p|span|font|a|h[1-6]|div|ul|ol|li|table|tr|td|th|small|big|strong|em|sup|sub|'
                  r'pre|code|hr|img|tt|html|body|head|style)\b[^<>]*>', re.IGNORECASE)
# argument placeholders of the Tcl help texts: <value>, <command_name> (best kept in English)
ARGS = re.compile(r'<[a-z][a-z0-9_]*>')


def placeholders(text):
    printf = sorted(p for p in PRINTF.findall(text) if p != '%%')
    braces = sorted('{%s%s%s}' % (field, '!' + conversion if conversion else '', ':' + spec if spec else '')
                    for __, field, spec, conversion in Formatter().parse(text) if field is not None)
    return printf, braces


def format_values(msgid):
    """
    :return: values that msgid can be formatted with by the % operator (a tuple, or a dict for %(name)s), or None if
             msgid does not format
    """
    sample = {'s': 'x', 'r': 'x', 'a': 'x', 'c': 'x', 'd': 1, 'i': 1, 'u': 1, 'o': 1, 'x': 1, 'X': 1,
              'e': 1.0, 'E': 1.0, 'f': 1.0, 'F': 1.0, 'g': 1.0, 'G': 1.0}
    found = [p for p in PRINTF.findall(msgid) if p != '%%']
    named = [p for p in found if p.startswith('%(')]
    if named:
        values = {}
        for p in named:
            values[p[2:p.index(')')]] = sample.get(p[-1], 'x')
    else:
        values = tuple(sample.get(p[-1], 'x') for p in found)
    try:
        msgid % values
    except (TypeError, ValueError, KeyError):
        return None
    return values


def check_entry(msgid, msgstr):
    """
    :return: (errors, warnings) of a translation, two lists (empty if it is fine). An error makes the program fail
             when it formats the message: the translation does not format with the values of the English text, or its
             {name} placeholders differ. A warning is a difference that the program shows as it is.
    """
    errors, warnings = [], []
    if not msgstr:
        return errors, warnings
    try:
        p1, b1 = placeholders(msgid)
        p2, b2 = placeholders(msgstr)
    except ValueError as err:
        return ['invalid brace formatting (%s)' % err], warnings
    format_error = None
    if p1:
        # a text formatted with %: the translation must format with the values the English text formats with
        # ('% 100' raises "unsupported format character", a lost '%s' raises "not all arguments converted")
        values = format_values(msgid)
        if values is not None:
            try:
                msgstr % values
            except (TypeError, ValueError, KeyError) as err:
                format_error = err
    if format_error is not None:
        errors.append('formatting with %% fails (%s): placeholders %s, translated %s' % (format_error, p1, p2))
    else:
        # e.g. '% s' for '%s' (it still formats), or a '%s' added to a text that is not formatted (shown as it is)
        if p1 != p2:
            warnings.append('printf placeholders %s != %s' % (p1, p2))
        if (p1 or '%%' in msgid) and '%' in PRINTF.sub('', msgstr):
            warnings.append('a "%" that is not a placeholder: write "%%"')
    if b1 != b2:
        errors.append('brace placeholders %s != %s' % (b1, b2))
    if sorted(t.lower() for t in TAGS.findall(msgid)) != sorted(t.lower() for t in TAGS.findall(msgstr)):
        warnings.append('HTML tags differ')
    arg_names = [a for a in ARGS.findall(msgid) if not TAGS.fullmatch(a)]
    if sorted(arg_names) != sorted(a for a in ARGS.findall(msgstr) if not TAGS.fullmatch(a)):
        warnings.append('argument placeholders <...> differ')
    if msgid.endswith('\n') != msgstr.endswith('\n'):
        warnings.append('final newline differs')
    if msgid.startswith('\n') != msgstr.startswith('\n'):
        warnings.append('leading newline differs')
    return errors, warnings


def check(lang):
    """:return: (active entries, untranslated, fuzzy, list of (entry, errors), list of (entry, warnings))"""
    header, entries = read_po(po_path(lang))
    active = [e for e in entries if not e.obsolete]
    untranslated = [e for e in active if not e.msgstr]
    fuzzy = [e for e in active if e.fuzzy and e.msgstr]
    errors, warnings = [], []
    for e in active:
        if e.msgstr and not e.fuzzy:
            entry_errors, entry_warnings = check_entry(e.msgid, e.msgstr)
            if entry_errors:
                errors.append((e, entry_errors))
            if entry_warnings:
                warnings.append((e, entry_warnings))
    return active, untranslated, fuzzy, errors, warnings


def stats(lang):
    """:return: dict of counts of the language's active entries"""
    active, untranslated, fuzzy, errors, warnings = check(lang)
    used = [e for e in active if e.msgstr and not e.fuzzy]     # the translations the program shows
    return {'strings': len(active), 'translated': len(used), 'untranslated': len(untranslated), 'fuzzy': len(fuzzy),
            'errors': len(errors), 'warnings': len(warnings), 'mo_current': mo_is_current(lang)}


def stats_markdown(rows):
    """:param rows: list of (lang, stats dict)"""
    lines = ['| Language | Strings | Translated | Untranslated | Fuzzy | Placeholder errors | Warnings |'
             ' .mo up to date |', '|---|---|---|---|---|---|---|---|']
    for lang, s in rows:
        done = 100.0 * s['translated'] / s['strings'] if s['strings'] else 0.0
        lines.append('| %s | %d | %d (%.0f %%) | %d | %d | %d | %d | %s |' % (
            lang, s['strings'], s['translated'], done, s['untranslated'], s['fuzzy'], s['errors'], s['warnings'],
            'yes' if s['mo_current'] else 'no'))
    return '\n'.join(lines) + '\n'


def read_terms(path=GLOSSARY_TERMS):
    """:return: list of (term, note) from the terms file: one term per line, a TAB, then a note for the translators"""
    terms = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line.strip() or line.startswith('#'):
                continue
            term, __, note = line.partition('\t')
            terms.append((term.strip(), note.strip()))
    return terms


def glossary(terms, langs):
    """
    The current translation of each term in each language: the translation of the entry whose English text is the
    term (case and a final ':' ignored).

    :return: list of rows [term, note, translation per language ('' if the term has no entry of its own)]
    """
    def key(text):
        return text.strip().rstrip(':').strip().casefold()

    found = {}
    for lang in langs:
        __, entries = read_po(po_path(lang))
        table = {}
        for e in entries:
            if not e.obsolete and e.msgstr and not e.fuzzy:
                table.setdefault(key(e.msgid), e.msgstr.strip().rstrip(':').rstrip('：').strip())
        found[lang] = table
    return [[term, note] + [found[lang].get(key(term), '') for lang in langs] for term, note in terms]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['extract', 'merge', 'compile', 'check', 'init', 'stats', 'glossary'])
    ap.add_argument('langs', nargs='*')
    ap.add_argument('--check', action='store_true', help='compile: only report the .mo files that are not up to date')
    ap.add_argument('--markdown', metavar='FILE', help='stats: also write the table to FILE')
    ap.add_argument('--out', metavar='FILE', help='glossary: write a CSV file (UTF-8) instead of printing')
    args = ap.parse_intermixed_args(argv)
    # translations printed to a console whose code page can not show them must not crash
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')
    translated = [lang for lang in languages() if lang != 'en']

    if args.command == 'extract':
        strings, problems = extract()
        n = write_template(strings)
        print('%d strings -> %s' % (n, os.path.relpath(TEMPLATE, ROOT)))
        for p in problems:
            print('  not translatable: ' + p)
    elif args.command == 'merge':
        strings, __ = extract()
        for lang in args.langs:
            print('%s: %s' % (lang, merge(lang, strings)))
    elif args.command == 'compile':
        langs = args.langs or languages()
        if args.check:
            stale = [lang for lang in langs if not mo_is_current(lang)]
            for lang in stale:
                print('%s: strings.mo is not up to date: run "python scripts/i18n_tool.py compile %s"' % (lang, lang))
            if not stale:
                print('all .mo files are up to date')
            return 1 if stale else 0
        for lang in langs:
            n = compile_mo(po_path(lang), po_path(lang)[:-3] + '.mo')
            print('%s: %d translations compiled' % (lang, n))
    elif args.command == 'init':
        for lang in args.langs:
            path = init_po(lang)
            print('%s: %s made (%d strings, untranslated)' % (lang, os.path.relpath(path, ROOT), len(read_po(path)[1])))
        print('Next: translate, then "compile %s"; add the language to languages_dict in appTranslation.py.' %
              ' '.join(args.langs))
    elif args.command == 'stats':
        rows = [(lang, stats(lang)) for lang in (args.langs or translated)]
        table = stats_markdown(rows)
        print(table, end='')
        if args.markdown:
            with open(args.markdown, 'w', encoding='utf-8', newline='\n') as f:
                f.write('# Translation status\n\n'
                        'Made by `python scripts/i18n_tool.py stats --markdown <file>` on %s.\n\n' % now_stamp()[:10] +
                        table)
    elif args.command == 'glossary':
        langs = args.langs or translated
        rows = glossary(read_terms(), langs)
        if args.out:
            with open(args.out, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(['English', 'Note'] + langs)
                w.writerows(rows)
            print('%d terms x %d languages -> %s' % (len(rows), len(langs), args.out))
        else:
            for row in rows:
                print(' | '.join(row))
    elif args.command == 'check':
        failed = False
        for lang in args.langs or translated:
            active, untranslated, fuzzy, errors, warnings = check(lang)
            print('%s: %d strings, %d untranslated, %d fuzzy, %d placeholder errors, %d warnings' %
                  (lang, len(active), len(untranslated), len(fuzzy), len(errors), len(warnings)))
            for kind, found in (('error', errors), ('warning', warnings)):
                for e, problems in found:
                    print('  %s: %r: %s' % (kind, e.msgid[:70], '; '.join(problems)))
            failed = failed or bool(errors)
        return 1 if failed else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
