# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Parser fuzz harness                                       #
# Author: Marius Stanciu (c)                                #
# Method after TRACE Boardviewer (MIT)                      #
# https://github.com/trace-boardviewer/trace-boardviewer    #
# MIT Licence                                               #
# ##########################################################

"""
Deterministic mutation fuzzing of the file parsers (the helper of tests/test_parser_fuzz.py).

For each target the seed files are the fixtures of tests/fixtures. A fixed seed derives the inputs: truncation at a
1/64 step, byte flips, byte insertions and deletions, a word replaced by a limit value (0, -1, 2**31, NaN, inf...),
a duplicated chunk, a number replaced by nan / inf / 1e308 / negative, a duplicated or removed line. A container
target (ZIP, OLE) mutates one member or stream and rebuilds the container.

An outcome is accepted when the parser returns, or raises its own named error (``Target.named``). A defect is any other
exception, a NaN or infinite coordinate or an invalid polygon in the result. The failing input is shrunk (delta
debugging) and can be saved to tests/fixtures/fuzz_regressions/<target>-<kind>-<hash8>.bin, the file that the short
test replays. A container target stores ``<member>\\n<content>``, the others the raw input.

The short mode (default) runs ``Target.short_n`` inputs of each target and has no time limit. The long mode is set
by the environment, run it by hand::

    .venv\\Scripts\\python.exe -m pytest tests/test_parser_fuzz.py -s -p no:cacheprovider
        FLATCAM_FUZZ_ITERATIONS=2000      inputs for each target (the long mode, prints the slowest inputs)
        FLATCAM_FUZZ_SEED=7               seed of the derived inputs (default 20261111)
        FLATCAM_FUZZ_TARGETS=allegro,kicad  only these targets
        FLATCAM_FUZZ_SAVE=1               shrink every defect and save it to the regression corpus
        FLATCAM_FUZZ_HANG_SECONDS=60      a single input that runs longer dumps all stacks and ends the process
        FLATCAM_TEST_DIPTRACE=1           also the DipTrace target
"""

import faulthandler
import hashlib
import io
import math
import os
import random
import re
import struct
import sys
import tempfile
import time
import traceback
import warnings
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / 'tests' / 'fixtures'
CORPUS = FIXTURES / 'fuzz_regressions'
DEFAULT_SEED = 20261111

_NUMBER = re.compile(rb'-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?')
_BAD_NUMBERS = (b'nan', b'inf', b'-inf', b'1e308', b'-1e308', b'99999999999999999999', b'-1', b'0', b'2147483648',
                b'1e-320', b'-0', b'0.0000000000000001')
_BAD_INTS = (0, 1, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0xFFFF, 0x10000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF,
             0x7FFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF)
_BAD_FLOATS = (float('nan'), float('inf'), float('-inf'), 3.0e38, -3.0e38, 1e-45, -0.0, 1e30, -1.0)

BINARY_OPS = ('truncate', 'flip', 'word', 'insert', 'delete', 'dupchunk', 'number', 'word')
TEXT_OPS = ('truncate', 'flip', 'number', 'dupline', 'dropline', 'insert', 'delete', 'number', 'dupblock', 'dropblock')


def mutate(data, kind, rng, op=None):
    """One derived input of ``data`` (bytes). ``kind`` is 'bin' or 'text'. Returns (op, bytes)."""
    data = bytes(data)
    ops = BINARY_OPS if kind == 'bin' else TEXT_OPS
    op = op or rng.choice(ops)
    n = len(data)
    if n == 0:
        return op, data
    buf = bytearray(data)
    if op == 'truncate':
        return op, data[:n * rng.randrange(0, 64) // 64]
    if op == 'flip':
        for _ in range(rng.choice((1, 1, 2, 4, 8))):
            buf[rng.randrange(n)] = rng.choice((0, 0xFF, 0x7F, 0x80, rng.randrange(256)))
        return op, bytes(buf)
    if op == 'word':
        width = rng.choice((1, 2, 4, 4, 8))
        if n < width:
            return op, data
        pos = rng.randrange(0, n - width + 1)
        if rng.random() < 0.7:
            pos -= pos % width
        if width in (4, 8) and rng.random() < 0.5:
            value = struct.pack('<f' if width == 4 else '<d', rng.choice(_BAD_FLOATS))
        else:
            value = (rng.choice(_BAD_INTS) & ((1 << (8 * width)) - 1)).to_bytes(width, 'little')
        buf[pos:pos + width] = value
        return op, bytes(buf)
    if op == 'insert':
        pos = rng.randrange(n + 1)
        buf[pos:pos] = bytes(rng.randrange(256) for _ in range(rng.choice((1, 2, 4, 8, 32))))
        return op, bytes(buf)
    if op == 'delete':
        pos = rng.randrange(n)
        del buf[pos:pos + rng.choice((1, 2, 4, 8, 64))]
        return op, bytes(buf)
    if op == 'dupchunk':
        pos = rng.randrange(n)
        size = rng.choice((4, 16, 64, 512))
        buf[pos:pos] = data[pos:pos + size]
        return op, bytes(buf)
    if op == 'number':
        start = rng.randrange(n)
        match = _NUMBER.search(data, start) or _NUMBER.search(data)
        if match is None:
            return mutate(data, kind, rng, 'flip')
        return op, data[:match.start()] + rng.choice(_BAD_NUMBERS) + data[match.end():]
    if op in ('dupline', 'dropline', 'dupblock', 'dropblock'):
        lines = data.splitlines(keepends=True)
        if len(lines) < 2:
            return mutate(data, kind, rng, 'flip')
        i = rng.randrange(len(lines))
        size = 1 if op.endswith('line') else rng.randrange(2, 40)
        if op.startswith('dup'):
            lines[i:i] = lines[i:i + size]
        else:
            del lines[i:i + size]
        return op, b''.join(lines)
    raise ValueError(op)


def shrink(content, still_fails, max_runs=300, max_seconds=40.0):
    """Delta debugging: remove chunks of ``content`` while ``still_fails(candidate)`` stays true."""
    started = time.perf_counter()
    runs = 0
    n = 2
    while len(content) >= 2 and runs < max_runs and time.perf_counter() - started < max_seconds:
        chunk = max(1, len(content) // n)
        reduced = False
        for start in range(0, len(content), chunk):
            candidate = content[:start] + content[start + chunk:]
            runs += 1
            if candidate and still_fails(candidate):
                content = candidate
                n = max(n - 1, 2)
                reduced = True
                break
            if runs >= max_runs or time.perf_counter() - started > max_seconds:
                break
        if not reduced:
            if chunk == 1:
                break
            n = min(n * 2, len(content))
    return content


# --- result checks -------------------------------------------------------------------------------------------------

def _finite_bounds(geom):
    if geom.is_empty:
        return True
    return all(math.isfinite(v) for v in geom.bounds)


def _walk_geoms(value, found):
    from shapely.geometry.base import BaseGeometry
    if isinstance(value, BaseGeometry):
        found.append(value)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _walk_geoms(item, found)
    elif isinstance(value, dict):
        for item in value.values():
            _walk_geoms(item, found)


def check_result(result):
    """None, or the kind of a defect in a parse result: NaN / inf coordinates, an invalid polygon."""
    from shapely.geometry.base import BaseGeometry
    objects = [result]
    objects.extend(getattr(result, 'boards', None) or [])
    for obj in objects:
        found = []
        for key in ('layers', 'drill_groups'):
            container = getattr(obj, key, None)
            if isinstance(container, dict):
                for entry in container.values():
                    if isinstance(entry, dict):
                        for field, item in entry.items():
                            if field in ('solid_geometry', 'drills', 'slots', 'geometry') or isinstance(item, dict):
                                _walk_geoms(item, found)
                            elif isinstance(item, float) and not math.isfinite(item):
                                return 'NonFiniteValue'
                        for item in entry.values():
                            if isinstance(item, dict) and isinstance(item.get('tooldia'), float) \
                                    and not math.isfinite(item['tooldia']):
                                return 'NonFiniteValue'
        for key in ('solid_geometry', 'board_outline'):
            value = getattr(obj, key, None)
            if value is not None:
                _walk_geoms(value, found)
        tools = getattr(obj, 'tools', None)
        if isinstance(tools, dict):
            for entry in tools.values():
                if isinstance(entry, dict):
                    dia = entry.get('tooldia')
                    if isinstance(dia, float) and not math.isfinite(dia):
                        return 'NonFiniteValue'
                    for field in ('solid_geometry', 'drills', 'slots'):
                        _walk_geoms(entry.get(field), found)
        for geom in found:
            if isinstance(geom, BaseGeometry):
                if not _finite_bounds(geom):
                    return 'NonFiniteGeometry'
                if geom.geom_type in ('Polygon', 'MultiPolygon') and not geom.is_valid:
                    return 'InvalidGeometry'
    return None


# --- targets -------------------------------------------------------------------------------------------------------

class Target:
    """
    name        used in file names (no '-')
    parts       callable -> list of (ctx, kind, content): the seed pieces that get mutated
    run         callable (ctx, content, tmp) -> parse result; ``tmp`` is a scratch directory
    named       the parser's own error classes (callable -> tuple)
    container   True when a corpus file has the first line ``ctx``
    marker      pytest marker name (None, 'diptrace')
    short_n     inputs of the short mode
    """

    def __init__(self, name, parts, run, named, short_n=8, container=False, marker=None):
        self.name = name
        self._parts = parts
        self.run = run
        self.named = named
        self.short_n = short_n
        self.container = container
        self.marker = marker
        self._cache = None

    def parts(self):
        if self._cache is None:
            self._cache = self._parts()
        return self._cache

    def inputs(self, n, seed):
        parts = self.parts()
        for i in range(n):
            rng = random.Random('%s/%s/%d' % (seed, self.name, i))
            ctx, kind, content = parts[i % len(parts)]
            ops = BINARY_OPS if kind == 'bin' else TEXT_OPS
            op, data = mutate(content, kind, rng, ops[(i + i // len(parts)) % len(ops)])
            yield i, ctx, op, data

    def blob(self, ctx, content):
        return ctx.encode() + b'\n' + content if self.container else content

    def unblob(self, blob):
        if not self.container:
            return '', blob
        ctx, _, content = blob.partition(b'\n')
        return ctx.decode(), content


def run_one(target, ctx, content, tmp):
    """(status, kind, location, message, seconds). status is 'ok', 'named' or 'defect'."""
    started = time.perf_counter()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = target.run(ctx, content, tmp)
        elapsed = time.perf_counter() - started
        problem = check_result(result)
        if problem:
            return 'defect', problem, 'result', problem, elapsed
        return 'ok', '', '', '', elapsed
    except target.named() as error:
        return 'named', type(error).__name__, '', str(error)[:100], time.perf_counter() - started
    except BaseException as error:  # noqa: BLE001 - a harness classifies everything
        if isinstance(error, KeyboardInterrupt):
            raise
        frames = traceback.extract_tb(error.__traceback__)
        inner = [f for f in frames if 'FlatCAM_EVO' in f.filename.replace('\\', '/') or 'appParsers' in f.filename
                 or f.filename.endswith('camlib.py')]
        frame = (inner or frames)[-1]
        location = '%s:%d' % (Path(frame.filename).name, frame.lineno)
        return 'defect', type(error).__name__, location, str(error)[:100], time.perf_counter() - started


class Report:
    def __init__(self, target):
        self.target = target
        self.count = 0
        self.statuses = {'ok': 0, 'named': 0, 'defect': 0}
        self.defects = {}  # (kind, location) -> dict(first=..., count)
        self.timings = []  # (seconds, label)

    def format(self):
        lines = ['%s: %d inputs %s' % (self.target.name, self.count, self.statuses)]
        for (kind, location), info in sorted(self.defects.items()):
            lines.append('  DEFECT %s at %s x%d (%s) input #%d %s: %s' % (
                kind, location, info['count'], info['op'], info['index'], info['hash'], info['message']))
        return '\n'.join(lines)

    def slowest(self, count=5):
        return ['%.2f s %s' % item for item in sorted(self.timings, reverse=True)[:count]]


def fuzz(target, n=None, seed=DEFAULT_SEED, save=False, hang_seconds=0):
    """Run the derived inputs of one target and return a Report."""
    report = Report(target)
    n = target.short_n if n is None else n
    with tempfile.TemporaryDirectory(prefix='flatcam_fuzz_') as tmp:
        tmp = Path(tmp)
        for index, ctx, op, data in target.inputs(n, seed):
            if hang_seconds:
                (tmp / 'current.bin').write_bytes(target.blob(ctx, data))
                faulthandler.dump_traceback_later(hang_seconds, exit=True)
            try:
                status, kind, location, message, seconds = run_one(target, ctx, data, tmp)
            finally:
                if hang_seconds:
                    faulthandler.cancel_dump_traceback_later()
            report.count += 1
            report.statuses[status] += 1
            report.timings.append((seconds, '%s #%d %s' % (target.name, index, op)))
            if status == 'defect':
                key = (kind, location)
                info = report.defects.get(key)
                if info is None:
                    info = report.defects[key] = {
                        'count': 0, 'op': op, 'index': index, 'message': message, 'ctx': ctx, 'data': data,
                        'hash': hashlib.sha1(target.blob(ctx, data)).hexdigest()[:8]}
                info['count'] += 1
        if save:
            for (kind, location), info in report.defects.items():
                save_regression(target, kind, location, info['ctx'], info['data'], tmp)
    return report


def save_regression(target, kind, location, ctx, data, tmp):
    """Shrink the failing input and write it to the regression corpus."""
    def still_fails(candidate):
        result = run_one(target, ctx, candidate, tmp)
        return result[0] == 'defect' and (result[1], result[2]) == (kind, location)

    small = shrink(data, still_fails)
    blob = target.blob(ctx, small)
    CORPUS.mkdir(parents=True, exist_ok=True)
    path = CORPUS / ('%s-%s-%s.bin' % (target.name, re.sub(r'\W', '', kind), hashlib.sha1(blob).hexdigest()[:8]))
    path.write_bytes(blob)
    print('saved %s (%d -> %d bytes)' % (path.name, len(data), len(small)))
    return path


def replay(target, blob, tmp):
    ctx, content = target.unblob(blob)
    return run_one(target, ctx, content, tmp)


# --- seeds and parser entries --------------------------------------------------------------------------------------

def _read(*parts):
    return (FIXTURES.joinpath(*parts)).read_bytes()


def _app():
    from tests.helpers_objects import make_app_0b
    return make_app_0b()


def _temp_file(tmp, content, suffix):
    path = Path(tmp) / ('input' + suffix)
    path.write_bytes(content)
    return path


def _simple(kind, *seeds):
    def parts():
        return [(Path(*p).name, kind, _read(*p)) for p in seeds]
    return parts


def _run_bytes(parser_cls, method='parse_bytes'):
    def run(ctx, content, tmp):
        parser = parser_cls()
        getattr(parser, method)(content)
        return parser
    return run


def _run_file(parser_factory, suffix):
    def run(ctx, content, tmp):
        parser = parser_factory()
        parser.parse_file(_temp_file(tmp, content, suffix))
        return parser
    return run


def _zip_with(members, replaced, content):
    """A ZIP of ``members`` (name -> bytes) with the member ``replaced`` set to ``content``."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, content if name == replaced else data)
    return out.getvalue()


# Allegro
def _t_allegro():
    from appParsers.ParseAllegroBrd import AllegroBrdParser, AllegroBrdParseError
    return Target('allegro', _simple('bin', ('orcad_17.2', 'proiectCAD.brd')), _run_bytes(AllegroBrdParser),
                  lambda: (AllegroBrdParseError,), 24)


def _t_sprint():
    from appParsers.ParseSprintLayout import SprintLayoutParser, SprintLayoutParseError
    return Target('sprint', _simple('bin', ('sprint_layout_6', 'cata.lay6')),
                  _run_bytes(SprintLayoutParser), lambda: (SprintLayoutParseError,), 6)


def _t_padsbin():
    from appParsers.ParsePADS import PADSParseError
    from appParsers.ParsePADSBinary import PADSBinaryReader

    def run(ctx, content, tmp):
        PADSBinaryReader(content).as_ascii()
        return None
    return Target('padsbin', _simple('bin', ('pads', 'LCORE_2.pcb')), run, lambda: (PADSParseError,), 12)


def _t_padsfull():
    """The whole native import (a parse of the seed takes about 8 s): the long mode only."""
    from appParsers.ParsePADS import PADSParser, PADSParseError
    return Target('padsfull', _simple('bin', ('pads', 'LCORE_2.pcb')), _run_file(PADSParser, '.pcb'),
                  lambda: (PADSParseError,), 0)


def _t_padsasc():
    from appParsers.ParsePADS import PADSParser, PADSParseError
    return Target('padsasc', _simple('text', ('pads', 'ClaySight_MK1.asc')),
                  _run_file(PADSParser, '.asc'), lambda: (PADSParseError,), 12)


class _FakeOle:
    """Stands in for olefile.OleFileIO: the streams come from a dict {(storage, stream): bytes}."""

    def __init__(self, streams):
        self.streams = streams

    def __call__(self, filename, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def exists(self, path):
        return tuple(path) in self.streams

    def openstream(self, path):
        return io.BytesIO(self.streams[tuple(path)])

    def listdir(self):
        return [list(key) for key in self.streams]


def _ole_streams(path):
    import olefile
    with olefile.OleFileIO(str(path)) as container:
        return {tuple(e): container.openstream(e).read() for e in container.listdir() if len(e) == 2}


def _ole_target(name, path, parser_module, parser_name, short_n):
    cache = {}

    def streams():
        if not cache:
            cache.update(_ole_streams(path))
        return cache

    def roles():
        """storage -> role of the Data streams."""
        module = __import__(parser_module, fromlist=['x'])
        parser_cls = getattr(module, parser_name)
        by_storage = {k[0]: v for k, v in streams().items() if k[1] == 'Data'}
        if name == 'cspcbdoc':
            return {storage: role for role, storage in parser_cls._stream_roles(by_storage).items()}
        return {storage: storage for storage in by_storage if storage in parser_cls.BINARY
                or storage in parser_cls.PROPERTIES or storage in ('WideStrings6', 'EmbeddedFonts6')}

    def parts():
        wanted = set(roles())
        return [('%s/%s' % key, 'bin', data) for key, data in sorted(streams().items())
                if key[0] in wanted and len(data) < 400000]

    def run(ctx, content, tmp):
        import olefile
        module = __import__(parser_module, fromlist=['x'])
        parser_cls = getattr(module, parser_name)
        # one parse of the whole seed board takes seconds: the record streams stay out unless they are the mutated
        # one, the property streams (board, nets, components, rules) give the context
        context = set(parser_cls.PROPERTIES) | {'WideStrings6', 'Regions6', 'ShapeBasedRegions6', 'BoardRegions'}
        by_role = roles()
        replaced = {key: data for key, data in streams().items() if by_role.get(key[0]) in context}
        storage = ctx.split('/')[0]
        for key, data in streams().items():
            if key[0] == storage:
                replaced[key] = data
        replaced[tuple(ctx.split('/'))] = content
        with mock.patch.object(olefile, 'OleFileIO', _FakeOle(replaced)):
            parser = getattr(module, parser_name)()
            parser.parse_file('fake.PcbDoc')
        return parser

    def named():
        from appParsers.ParsePcbDoc import PcbDocParseError
        return (PcbDocParseError,)
    return Target(name, parts, run, named, short_n, container=True)


def _t_pcbdoc():
    return _ole_target('pcbdoc', FIXTURES / 'altium' / 'I2C_level_shifter.PcbDoc',
                       'appParsers.ParsePcbDoc', 'PcbDocParser', 24)


def _t_cspcbdoc():
    return _ole_target('cspcbdoc', FIXTURES / 'altium_cs' / 'PCI Express_4-Port_Serial_Interface.CSPcbDoc',
                       'appParsers.ParseCSPcbDoc', 'CSPcbDocParser', 6)


def _t_proteus():
    seeds = (('Proteus9', 'pcb 1.pdsprj', 'ROOT.LYT'), ('Proteus', 'TP1_G1.pdsprj', 'BOARD3.LYT'))

    def archive(seed):
        with zipfile.ZipFile(FIXTURES / seed[0] / seed[1]) as z:
            return {'PROJECT.XML': z.read('PROJECT.XML'), seed[2]: z.read(seed[2]),
                    **({'FIRMWARE.XML': z.read('FIRMWARE.XML')} if 'FIRMWARE.XML' in z.namelist() else {})}

    def parts():
        return [(seed[1] + ':' + seed[2], 'bin', archive(seed)[seed[2]]) for seed in seeds]

    def run(ctx, content, tmp):
        from appParsers.ParseProteus import ProteusParser
        seed = next(s for s in seeds if ctx == s[1] + ':' + s[2])
        path = _temp_file(tmp, _zip_with(archive(seed), seed[2], content), '.pdsprj')
        parser = ProteusParser()
        parser.parse_file(path)
        return parser

    def named():
        from appParsers.ParseProteus8 import ProteusParseError
        return (ProteusParseError,)
    return Target('proteus', parts, run, named, 6, container=True)


def _t_odb():
    path = FIXTURES / 'odb++' / 'Easyduino_RP2040-odb.zip'
    wanted = re.compile(r'^(matrix/matrix|misc/info|fonts/standard|steps/pcb/(stephdr|profile|eda/data)|'
                        r'steps/pcb/layers/(b\.cu|drill[^/]*|f\.cu|edge[^/]*)/(features|tools)|'
                        r'steps/pcb/layers/comp_\+_top/components)$')

    def members():
        with zipfile.ZipFile(path) as z:
            return {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}

    cache = {}

    def get():
        if not cache:
            cache.update(members())
        return cache

    def parts():
        out = [(name, 'text', data) for name, data in get().items() if wanted.match(name) and data]
        out.append(('', 'bin', path.read_bytes()[:60000]))
        return out

    def run(ctx, content, tmp):
        from appParsers.ParseODB import ODBParser
        if ctx:
            data = _zip_with(get(), ctx, content)
        else:
            data = content
        parser = ODBParser()
        parser.parse_file(_temp_file(tmp, data, '.zip'))
        return parser

    def named():
        from appParsers.ParseODB import ODBParseError
        return (ODBParseError,)
    return Target('odb', parts, run, named, 2, container=True)


def _t_diptrace():
    from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError
    return Target('diptrace', _simple('bin', ('diptrace', 'rotate4.dip'), ('diptrace', 'DAP42v2.dip')), _run_file(DipTraceParser, '.dip'),
                  lambda: (DipTraceParseError,), 12, marker='diptrace')


def _t_eagle():
    from appParsers.ParseEagleBrd import EagleBrdParser, EagleBrdParseError
    return Target('eagle', _simple('text', ('eagle_cad', 'Pcb39.brd')), _run_file(EagleBrdParser, '.brd'),
                  lambda: (EagleBrdParseError,), 2)


def _t_kicad():
    from appParsers.ParseKiCadPcb import KiCadPcbParser, KiCadPcbParseError
    return Target('kicad', _simple('text', ('kicad', 'Easyduino_RP2040.kicad_pcb')),
                  _run_file(KiCadPcbParser, '.kicad_pcb'), lambda: (KiCadPcbParseError,), 3)


def _t_gerber():
    from appParsers.ParseGerber import Gerber

    def run(ctx, content, tmp):
        gerber = Gerber(_app())
        gerber.parse_file(str(_temp_file(tmp, content, '.gbr')))
        return gerber
    return Target('gerber', _simple('text', ('kicad', 'Gerbers', 'Easyduino_RP2040-F_Mask.gts'),
                                    ('altium', 'Gerber', 'I2C_level_shifter.GTL'),
                                    ('unify', 'unify_macro.gbr'), ('unify', 'unify_mixed.drl')),
                  run, lambda: (), 24)


def _t_excellon():
    from appParsers.ParseExcellon import Excellon

    def run(ctx, content, tmp):
        excellon = Excellon(_app())
        excellon.parse_file(str(_temp_file(tmp, content, '.drl')))
        return excellon
    return Target('excellon', _simple('text', ('kicad', 'Gerbers', 'Easyduino_RP2040.drl'),
                                      ('altium', 'Gerber', 'I2C_level_shifter.TXT'),
                                      ('sprint_layout_6', 'Gerber', 'cata.DRL')), run, lambda: (), 24)


def _t_hpgl2():
    from appParsers.ParseHPGL2 import HPGL2

    def run(ctx, content, tmp):
        app = _app()
        app.options['tools_mill_tooldia'] = 0.1
        hpgl = HPGL2(app)
        hpgl.parse_file(str(_temp_file(tmp, content, '.plt')))
        return hpgl
    return Target('hpgl2', _simple('text', ('orcad_17.2', 'Desktop.plt')), run, lambda: (), 24)


def _t_ipc2581():
    import gzip
    from appParsers.ParseIPC2581 import IPC2581Parser, IPC2581ParseError

    def parts():
        # the plain XML of the gzip fixture: mutating the compressed bytes only tests zlib
        return [('Easyduino_RP2040.xml', 'text', gzip.decompress(_read('ipc2581', 'Easyduino_RP2040.xml.gz')))]
    return Target('ipc2581', parts, _run_bytes(IPC2581Parser), lambda: (IPC2581ParseError,), 2)


def _t_easyeda_pro():
    path = FIXTURES / 'easyeda_pro' / 'Agent-Light-v1-layout.epro2'
    cache = {}

    def get():
        if not cache:
            with zipfile.ZipFile(path) as z:
                cache.update({i.filename: z.read(i) for i in z.infolist() if not i.is_dir()})
        return cache

    def parts():
        # each project member (mutated inside an intact archive), and the raw archive bytes
        out = [(name, 'text', data) for name, data in get().items() if data]
        out.append(('', 'bin', path.read_bytes()))
        return out

    def run(ctx, content, tmp):
        from appParsers.ParseEasyEDAPro import EasyEDAProParser
        parser = EasyEDAProParser()
        parser.parse_file(_temp_file(tmp, _zip_with(get(), ctx, content) if ctx else content, '.epro2'))
        return parser

    def named():
        from appParsers.ParseEasyEDAPro import EasyEDAProParseError
        return (EasyEDAProParseError,)
    return Target('easyeda_pro', parts, run, named, 12, container=True)


FACTORIES = {
    'ipc2581': _t_ipc2581, 'easyeda_pro': _t_easyeda_pro,
    'allegro': _t_allegro, 'sprint': _t_sprint, 'padsbin': _t_padsbin, 'padsfull': _t_padsfull, 'padsasc': _t_padsasc, 'pcbdoc': _t_pcbdoc,
    'cspcbdoc': _t_cspcbdoc, 'proteus': _t_proteus, 'odb': _t_odb, 'diptrace': _t_diptrace, 'eagle': _t_eagle,
    'kicad': _t_kicad, 'gerber': _t_gerber, 'excellon': _t_excellon, 'hpgl2': _t_hpgl2,
}
NAMES = tuple(FACTORIES)


def get_target(name):
    return FACTORIES[name]()


def env_config():
    """(iterations or None, seed, names, save, hang_seconds) from the environment."""
    iterations = os.environ.get('FLATCAM_FUZZ_ITERATIONS')
    names = [n for n in os.environ.get('FLATCAM_FUZZ_TARGETS', '').split(',') if n] or list(NAMES)
    return (int(iterations) if iterations else None, int(os.environ.get('FLATCAM_FUZZ_SEED', DEFAULT_SEED)), names,
            os.environ.get('FLATCAM_FUZZ_SAVE') == '1', int(os.environ.get('FLATCAM_FUZZ_HANG_SECONDS', '0')))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    iterations, seed, names, save, hang = env_config()
    for target_name in names:
        rep = fuzz(get_target(target_name), iterations, seed, save, hang)
        print(rep.format())
        print('  slowest:', '; '.join(rep.slowest(3)))
