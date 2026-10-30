# ############################################################
# FlatCAM EVO: Gerber X2/X3 attributes (TF, TA, TO, TD)      #
# Spec: Gerber Layer Format Specification rev. 2021.11,      #
# chapter 5 (attributes), 6.2 (mandatory), 8.4 (deprecated)  #
# ############################################################
"""Gerber X2/X3 attribute support: no Qt, no parser state beyond :class:`AttributeState`.

Older files stay supported: deprecated and unknown values are never rejected, they are normalized when
the meaning is clear and kept raw otherwise.
"""

from __future__ import annotations

import hashlib
import re

_ESCAPE = re.compile(r'\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})')
_SURROGATES = re.compile('[\ud800-\udfff]')


def _unescape(text: str) -> str:
    """Decode the \\uXXXX and \\UXXXXXXXX Unicode escapes of spec 3.4.3; a broken escape stays raw."""
    if '\\' not in text:
        return text

    def repl(m: re.Match) -> str:
        code = int(m.group(1) or m.group(2), 16)
        try:
            return chr(code)
        except ValueError:
            return m.group(0)

    out = _ESCAPE.sub(repl, text)
    if _SURROGATES.search(out):  # 😀 is one character
        try:
            out = out.encode('utf-16', 'surrogatepass').decode('utf-16')
        except UnicodeError:
            pass
    return out


def split_attribute(body: str) -> tuple[str, list[str]]:
    """Split the text after TF/TA/TO/TD (e.g. '.N,GND,VCC') into (name, values).

    Commas separate fields before the escapes are decoded, so '\\u002C' stays inside a value. Spaces inside
    values are kept (spec 3.4.4); only the name is stripped, and a trailing CR/LF is dropped.
    """
    body = body.rstrip('\r\n')
    if not body:
        return '', []
    fields = body.split(',')
    return _unescape(fields[0]).strip(), [_unescape(f) for f in fields[1:]]


class AttributeState:
    """The attribute dictionary of spec chapter 5 while a file is read.

    Keeps the file attributes (TF), the current aperture attributes (TA) and the current object attributes (TO).
    """

    def __init__(self) -> None:
        self.tf: dict[str, list[str]] = {}
        self.ta: dict[str, list[str]] = {}
        self.to: dict[str, list[str]] = {}
        self._to_ids: dict[tuple, int] = {}
        self._to_states: dict[int, dict[str, list[str]]] = {}
        self._ta_cache: dict[tuple, dict[str, list[str]]] = {}

    def apply(self, key: str, body: str) -> None:
        """Run one attribute command: key is 'TF', 'TA', 'TO' or 'TD'; anything else is ignored."""
        name, values = split_attribute(body)
        if key == 'TF':
            if name:
                self.tf[name] = values
        elif key == 'TA':
            if name:
                self.ta[name] = values
        elif key == 'TO':
            if name:
                self.to[name] = values
        elif key == 'TD':
            if not name:  # no name: clear the whole dictionary but the file attributes
                self.ta.clear()
                self.to.clear()
            else:
                self.ta.pop(name, None)
                self.to.pop(name, None)

    @staticmethod
    def _freeze(state: dict[str, list[str]]) -> tuple:
        return tuple(sorted((k, tuple(v)) for k, v in state.items()))

    @property
    def to_id(self) -> int:
        """0 for an empty TO state, else the id of the current state (equal states share an id within a file)."""
        if not self.to:
            return 0
        key = self._freeze(self.to)
        ident = self._to_ids.get(key)
        if ident is None:
            ident = len(self._to_ids) + 1
            self._to_ids[key] = ident
            self._to_states[ident] = {k: list(v) for k, v in self.to.items()}
        return ident

    def to_table(self) -> dict[int, dict[str, list[str]]]:
        """All TO states seen so far, by id (plain dicts and lists; JSON turns the int keys into strings)."""
        return {i: {k: list(v) for k, v in st.items()} for i, st in self._to_states.items()}

    def ta_snapshot(self) -> dict[str, list[str]]:
        """The current TA state for an aperture. Equal states return the same object: the caller must not mutate it."""
        key = self._freeze(self.ta)
        snap = self._ta_cache.get(key)
        if snap is None:
            snap = self._ta_cache[key] = {k: list(v) for k, v in self.ta.items()}
        return snap

    def as_legacy(self) -> dict[str, dict[str, list[str]]]:
        """Copy in the shape of Gerber.attributes: {'TF': {}, 'TA': {}, 'TO': {}}."""
        return {'TF': {k: list(v) for k, v in self.tf.items()},
                'TA': {k: list(v) for k, v in self.ta.items()},
                'TO': {k: list(v) for k, v in self.to.items()}}


# ---------------------------------------------------------------------------------------------------------
# .FileFunction (5.6.3)
# ---------------------------------------------------------------------------------------------------------
_COPPER = ('Copper',)
_DRILL = ('Plated', 'NonPlated')
_SIDED = ('Soldermask', 'Legend', 'Paste', 'Glue', 'Carbonmask', 'Goldmask', 'Heatsinkmask', 'Peelablemask',
          'Silvermask', 'Tinmask', 'Depthrout', 'Pads', 'AssemblyDrawing', 'Vcut')
_PLAIN = ('Viafill', 'Drillmap', 'FabricationDrawing', 'Vcutmap', 'ArrayDrawing', 'Profile', 'Component', 'Other',
          'OtherDrawing')
_FILE_KINDS = {k.lower(): k for k in _COPPER + _DRILL + _SIDED + _PLAIN}
_SIDES = {'top': 'Top', 'bot': 'Bot', 'inr': 'Inr', 'bottom': 'Bot', 'inner': 'Inr'}
_COPPER_TYPES = {'plane': 'Plane', 'signal': 'Signal', 'mixed': 'Mixed', 'hatched': 'Hatched'}
_HOLE_TYPES = {'pth': 'PTH', 'npth': 'NPTH', 'blind': 'Blind', 'buried': 'Buried'}


def _layer_no(text: str) -> int | None:
    """'L3' or '3' -> 3."""
    m = re.fullmatch(r'\s*[Ll]?(\d+)\s*', text)
    return int(m.group(1)) if m else None


def parse_file_function(values: list[str]) -> dict:
    """Parse the values of .FileFunction.

    Keys: kind, layer (int|None), side ('Top'|'Bot'|'Inr'|None), type, plated (bool|None), from_to (tuple|None),
    edge ('P'|'NP'|None), index (int|None, mask/legend order), extra (raw values not consumed). Deprecated
    forms (e.g. Profile without P/NP) give None for the missing part; unknown kinds keep the given name.
    """
    out = {'kind': '', 'layer': None, 'side': None, 'type': None, 'plated': None, 'from_to': None,
           'edge': None, 'index': None, 'extra': []}
    if not values:
        return out
    kind = _FILE_KINDS.get(values[0].strip().lower(), values[0])
    out['kind'] = kind
    rest = list(values[1:])

    if kind == 'Copper':
        if rest and _layer_no(rest[0]) is not None:
            out['layer'] = _layer_no(rest.pop(0))
        if rest and rest[0].strip().lower() in _SIDES:
            out['side'] = _SIDES[rest.pop(0).strip().lower()]
        if rest and rest[0].strip().lower() in _COPPER_TYPES:
            out['type'] = _COPPER_TYPES[rest.pop(0).strip().lower()]
    elif kind in _DRILL:
        out['plated'] = kind == 'Plated'
        if len(rest) >= 2 and _layer_no(rest[0]) is not None and _layer_no(rest[1]) is not None:
            out['from_to'] = (_layer_no(rest[0]), _layer_no(rest[1]))
            del rest[:2]
        if rest and rest[0].strip().lower() in _HOLE_TYPES:
            out['type'] = _HOLE_TYPES[rest.pop(0).strip().lower()]
    elif kind == 'Profile':
        if rest and rest[0].strip().upper() in ('P', 'NP'):
            out['edge'] = rest.pop(0).strip().upper()
    elif kind == 'Component':
        if rest and _layer_no(rest[0]) is not None:
            out['layer'] = _layer_no(rest.pop(0))
        if rest and rest[0].strip().lower() in _SIDES:
            out['side'] = _SIDES[rest.pop(0).strip().lower()]
    elif kind in _SIDED:
        if rest and rest[0].strip().lower() in _SIDES:
            out['side'] = _SIDES[rest.pop(0).strip().lower()]
            if kind not in ('Vcut', 'Depthrout', 'Pads', 'Paste', 'Glue', 'AssemblyDrawing') and rest \
                    and rest[0].strip().isdigit():
                out['index'] = int(rest.pop(0))
    out['extra'] = rest
    return out


# ---------------------------------------------------------------------------------------------------------
# .AperFunction (5.6.10, deprecated values 8.4)
# ---------------------------------------------------------------------------------------------------------
_APER_KINDS = (
    'ViaDrill', 'BackDrill', 'ComponentDrill', 'MechanicalDrill', 'CastellatedDrill', 'OtherDrill',
    'ComponentPad', 'SMDPad', 'BGAPad', 'ConnectorPad', 'HeatsinkPad', 'ViaPad', 'TestPad', 'CastellatedPad',
    'FiducialPad', 'ThermalReliefPad', 'WasherPad', 'AntiPad', 'OtherPad', 'Conductor', 'EtchedComponent',
    'NonConductor', 'CopperBalancing', 'Border', 'OtherCopper', 'ComponentMain', 'ComponentOutline',
    'ComponentPin', 'Profile', 'NonMaterial', 'Material', 'Other')
_APER_DEPRECATED = ('Drawing', 'CutOut', 'Slot', 'Cavity')
_APER_LOOKUP = {k.lower(): k for k in _APER_KINDS + _APER_DEPRECATED}
_APER_SUBS = {s.lower(): s for s in (
    'Ia', 'Ib', 'IIa', 'IIb', 'IIIa', 'IIIb', 'IVa', 'IVb', 'V', 'VI', 'VII', 'None', 'PressFit', 'Tooling',
    'Breakout', 'Other', 'CuDef', 'SMDef', 'Local', 'Global', 'Panel', 'Body', 'Lead2Lead', 'Footprint',
    'Courtyard')}
# the free text after these kinds is a description, not a keyword
_FREE_TEXT_KINDS = ('OtherDrill', 'OtherPad', 'OtherCopper', 'Other')


def parse_aper_function(values: list[str]) -> dict:
    """Parse the values of .AperFunction into {'kind', 'sub' (list), 'deprecated' (bool)}.

    Known keywords get their canonical spelling ('BreakOut' -> 'Breakout'); the deprecated kinds of 8.4
    (Drawing, CutOut, Slot, Cavity) are kept and flagged; unknown kinds and sub values stay raw.
    """
    if not values:
        return {'kind': '', 'sub': [], 'deprecated': False}
    kind = _APER_LOOKUP.get(values[0].strip().lower(), values[0])
    if kind in _FREE_TEXT_KINDS:
        sub = list(values[1:])
    else:
        sub = [_APER_SUBS.get(v.strip().lower(), v) for v in values[1:]]
    return {'kind': kind, 'sub': sub, 'deprecated': kind in _APER_DEPRECATED}


# ---------------------------------------------------------------------------------------------------------
# File attributes view (5.6) and checks (6.2)
# ---------------------------------------------------------------------------------------------------------
def file_attributes(tf: dict[str, list[str]]) -> dict:
    """Typed view of the file attributes; a missing attribute gives None.

    Keys: part (str), file_function (parsed dict), file_polarity ('Positive'|'Negative', raw if unknown),
    same_coordinates (None if absent, else the ident, '' when there is none), creation_date (raw ISO 8601 str),
    generation_software {vendor, application, version}, project_id {name, guid, revision}, md5.
    """
    def get(name: str) -> list[str] | None:
        return tf.get(name)

    def field(vals: list[str], i: int) -> str:
        return vals[i] if i < len(vals) else ''

    part = get('.Part')
    func = get('.FileFunction')
    pol = get('.FilePolarity') or get('.Polarity')   # .Polarity: spelling seen in the spec's drill example
    same = get('.SameCoordinates')
    date = get('.CreationDate')
    gen = get('.GenerationSoftware')
    pid = get('.ProjectId')
    md5 = get('.MD5')

    polarity = None
    if pol:
        polarity = {'positive': 'Positive', 'negative': 'Negative'}.get(pol[0].strip().lower(), pol[0])

    return {
        'part': ','.join(part) if part else None,
        'file_function': parse_file_function(func) if func else None,
        'file_polarity': polarity,
        'same_coordinates': (field(same, 0) if same is not None else None),
        'creation_date': date[0] if date else None,
        'generation_software': ({'vendor': field(gen, 0), 'application': field(gen, 1), 'version': field(gen, 2)}
                                if gen else None),
        'project_id': ({'name': field(pid, 0), 'guid': field(pid, 1), 'revision': field(pid, 2)}
                       if pid else None),
        'md5': md5[0].strip() if md5 else None,
    }


_MANDATORY = ('.FileFunction', '.FilePolarity', '.SameCoordinates')


def check_mandatory(tf: dict[str, list[str]]) -> list[str]:
    """Names of the attributes spec 6.2 wants in fabrication data and the file lacks. For an info message only."""
    return [n for n in _MANDATORY if n not in tf and not (n == '.FilePolarity' and '.Polarity' in tf)]


# ---------------------------------------------------------------------------------------------------------
# Object attributes: nets, components (5.6.13 - 5.6.16)
# ---------------------------------------------------------------------------------------------------------
_COMP_TEXT = ('CMfr', 'CMPN', 'CVal', 'CMnt', 'CFtp', 'CPgN', 'CPgD', 'CLbN', 'CLbD')
_COMP_NUM = ('CRot', 'CHgt')


def nets_of(to_table: dict) -> dict[str, list[int]]:
    """Net name (.N) -> ids of the TO states carrying it. Reserved names ('' and 'N/C') are included."""
    nets: dict[str, list[int]] = {}
    for ident, state in to_table.items():
        for net in state.get('.N', []):
            ids = nets.setdefault(net, [])
            if int(ident) not in ids:
                ids.append(int(ident))
    return nets


def _to_float(text: str) -> float | str:
    try:
        return float(text)
    except ValueError:
        return text


def component_of(to_table: dict) -> dict[str, dict]:
    """Reference designator -> merged component data from the TO states.

    Keys: to_ids, pins ({pin number: function}) from .P, and the X3 fields present among CRot and CHgt (float),
    CMfr, CMPN, CVal, CMnt, CFtp, CPgN, CPgD, CLbN, CLbD (str), CSup (list of (supplier, part number) tuples).
    """
    comps: dict[str, dict] = {}

    def comp(refdes: str, ident: int) -> dict:
        c = comps.setdefault(refdes, {'to_ids': [], 'pins': {}})
        if ident not in c['to_ids']:
            c['to_ids'].append(ident)
        return c

    for ident, state in sorted(to_table.items(), key=lambda kv: int(kv[0])):
        ident = int(ident)
        pin = state.get('.P')
        if pin and pin[0]:
            c = comp(pin[0], ident)
            if len(pin) > 1 and pin[1] != '':
                c['pins'][pin[1]] = pin[2] if len(pin) > 2 else ''
        ref = state.get('.C')
        if not ref or not ref[0]:
            continue
        c = comp(ref[0], ident)
        for name in _COMP_NUM:
            if state.get('.' + name):
                c[name] = _to_float(state['.' + name][0])
        for name in _COMP_TEXT:
            if state.get('.' + name):
                c[name] = state['.' + name][0]
        sup = state.get('.CSup')
        if sup:
            c['CSup'] = [(sup[i], sup[i + 1] if i + 1 < len(sup) else '') for i in range(0, len(sup), 2)]
    return comps


# ---------------------------------------------------------------------------------------------------------
# .MD5 (5.6.9)
# ---------------------------------------------------------------------------------------------------------
_MD5_HEX = re.compile(r'[0-9a-fA-F]{32}')


def md5_matches(text: str, md5_value: str) -> bool | None:
    """Check the .MD5 signature of a whole Gerber text.

    Hashed: everything before the last '%TF.MD5' command (the command itself and the closing M02* are left
    out), with all CR and LF removed, as UTF-8. None when the value is not 32 hex digits or the text has no
    .MD5 command to cut at.
    """
    value = (md5_value or '').strip()
    if not _MD5_HEX.fullmatch(value):
        return None
    cut = text.rfind('%TF.MD5')
    if cut < 0:
        return None
    content = text[:cut].replace('\r', '').replace('\n', '')
    return hashlib.md5(content.encode('utf-8')).hexdigest() == value.lower()


def merge_to_table(dst: dict, src: dict) -> dict[int, int]:
    """Add the TO states of src to dst (changed in place) and return {id in src: id in dst}.

    Equal states share an id. The keys may be strings (a project file read back from JSON).
    """
    known = {AttributeState._freeze(state): int(ident) for ident, state in dst.items()}
    mapping = {}
    for ident, state in src.items():
        key = AttributeState._freeze(state)
        new_id = known.get(key)
        if new_id is None:
            new_id = max([int(i) for i in dst] + [0]) + 1
            dst[new_id] = {k: list(v) for k, v in state.items()}
            known[key] = new_id
        mapping[int(ident)] = new_id
    return mapping


def remap_to_ids(tool: dict, mapping: dict[int, int]) -> None:
    """Change the 'to' ids of the records of an aperture (its geometry list and the records of a block)."""
    if not mapping:
        return
    for record in tool.get('geometry') or ():
        if 'to' in record:
            record['to'] = mapping.get(int(record['to']), record['to'])
    for item in tool.get('records') or ():
        record = item[1]
        if 'to' in record:
            record['to'] = mapping.get(int(record['to']), record['to'])


# ---------------------------------------------------------------------------------------------------------
# Writer (spec 5.1, 3.4.3): the inverse of split_attribute

_PLAIN_CHAR = re.compile(r'[ -~]')
_SPECIAL = '%*,\\'


def escape_attribute_value(text: str) -> str:
    """Escape a value for a TF/TA/TO field: '%', '*', ',' and '\\' and every non-printable or non-ASCII
    character become \\uXXXX (\\UXXXXXXXX above the BMP)."""
    out = []
    for ch in str(text):
        if ch in _SPECIAL or not _PLAIN_CHAR.match(ch):
            code = ord(ch)
            out.append('\\u%04X' % code if code <= 0xFFFF else '\\U%08X' % code)
        else:
            out.append(ch)
    return ''.join(out)


def format_attribute(key: str, name: str, values: list[str] | tuple = ()) -> str:
    """One attribute command, e.g. ``%TF.FileFunction,Copper,L1,Top*%`` (no line end)."""
    fields = [escape_attribute_value(name)] + [escape_attribute_value(v) for v in values]
    return '%%%s%s*%%' % (key, ','.join(fields))


def attribute_changes(key: str, old: dict, new: dict) -> list[str]:
    """The commands (no line ends) that turn the dictionary ``old`` of TA or TO into ``new``:
    a bare TD when ``new`` is empty, else TD for the dropped names and the key command for the changed ones."""
    if old == new:
        return []
    if not new:
        return ['%TD*%']
    lines = [format_attribute('TD', name) for name in old if name not in new]
    lines += [format_attribute(key, name, values) for name, values in new.items() if old.get(name) != values]
    return lines


def file_attribute_lines(tf: dict, software: str, version: str, when: str) -> list[str]:
    """The TF commands of an export. Empty when the object has no file attributes. The generation software and the
    creation date are ours, the MD5 is dropped (the content changed)."""
    if not tf:
        return []
    out = {name: values for name, values in tf.items() if name not in ('.MD5', '.GenerationSoftware', '.CreationDate')}
    out['.GenerationSoftware'] = ['FlatCAM', software, version]
    out['.CreationDate'] = [when]
    return [format_attribute('TF', name, values) for name, values in out.items()]
