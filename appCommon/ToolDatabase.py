"""Transactional tool-library storage. No Qt objects or persistent connections."""

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 1
APPLICATION_ID = 0x46435444
OPERATION_PREFIXES = {
    0: ('tools_mill_', 'tools_drill_', 'tools_iso_', 'tools_paint_', 'tools_ncc_', 'tools_cutout_'),
    1: ('tools_mill_',), 2: ('tools_drill_',), 3: ('tools_iso_', 'tools_mill_'),
    4: ('tools_paint_', 'tools_mill_'), 5: ('tools_ncc_', 'tools_mill_'),
    6: ('tools_cutout_', 'tools_mill_')
}


class UnitsMismatchError(ValueError):
    def __init__(self, stored, current):
        super().__init__('Tools database units are %s; current units are %s. '
                         'Switch application units before using this library.' % (stored, current))
        self.stored = stored
        self.current = current


@contextmanager
def _connection(filename, write=False):
    path = Path(filename).resolve()
    try:
        connection = sqlite3.connect(path.as_uri() + ('?mode=rwc' if write else '?mode=ro'),
                                     uri=True, timeout=2.0)
        try:
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise OSError('Tools database: %s' % error) from error


def _check_schema(connection):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    application = connection.execute('PRAGMA application_id').fetchone()[0]
    if version != SCHEMA_VERSION or application != APPLICATION_ID:
        raise ValueError('Unsupported tools database schema: %s' % version)


def _metadata(connection, units=None):
    values = dict(connection.execute('SELECT key, value FROM metadata'))
    stored_units = values.get('units', '')
    if units and stored_units and stored_units != units.upper():
        raise UnitsMismatchError(stored_units, units.upper())
    return values


def read_database(filename, target=None, diameter=None, units=None):
    with _connection(filename) as connection:
        _check_schema(connection)
        metadata = _metadata(connection, units)
        sql = 'SELECT id, record FROM tools'
        clauses = []
        values = []
        if target is not None:
            clauses.append('target IN (0, ?)')
            values.append(target)
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        if diameter is None:
            rows = connection.execute(sql + ' ORDER BY position', values).fetchall()
        else:
            conjunction = ' AND ' if clauses else ' WHERE '
            rows = connection.execute(sql + conjunction + 'diameter = ? ORDER BY position',
                                      values + [diameter]).fetchall()
            if not rows:
                rows = connection.execute(sql + conjunction + 'tol_min <= ? AND tol_max >= ? ORDER BY position',
                                          values + [diameter, diameter]).fetchall()
        records = {str(key): json.loads(payload) for key, payload in rows}
    return records, metadata


def read_library(filename, units=None):
    """Keep the snapshot payloads opaque until their properties are needed."""
    with _connection(filename) as connection:
        _check_schema(connection)
        metadata = _metadata(connection, units)
        records = {}
        payloads = {}
        for key, name, diameter, target, shape, payload in connection.execute(
                'SELECT id, name, diameter, target, shape, record FROM tools ORDER BY position'):
            key = str(key)
            records[key] = {'name': name, 'tooldia': diameter,
                            'data': {'tool_target': target, 'tools_mill_tool_shape': shape}}
            payloads[key] = payload
    return records, payloads, metadata


def write_database(filename, records, units=None, expected_revision=None, retained_ids=None):
    if units is not None and units.upper() not in ('MM', 'IN'):
        raise ValueError('Unsupported tools database units: %s' % units)
    # Serialize before opening a transaction: bad input can't create an empty database.
    retained = list(records if retained_ids is None else retained_ids)
    positions = {key: position for position, key in enumerate(retained)}
    rows = [(int(key), tool['name'], tool['tooldia'], tool['data']['tool_target'],
             tool['data'].get('tol_min', 0.0), tool['data'].get('tol_max', 0.0),
             tool['data'].get('tools_mill_tool_shape', 0), positions[key],
             json.dumps(tool, separators=(',', ':'), ensure_ascii=False, allow_nan=False))
            for key, tool in records.items()]
    # SQLite creates the file before the transaction; a failed first write must not leave it behind.
    path = Path(filename)
    created = not path.exists()
    try:
        return _write_rows(filename, rows, retained, units, expected_revision)
    except BaseException:
        if created:
            for leftover in (path, Path(str(path) + '-journal')):
                leftover.unlink(missing_ok=True)
        raise


def _write_rows(filename, rows, retained, units, expected_revision):
    with _connection(filename, write=True) as connection:
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version == 0 and not connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchone():
            connection.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            connection.execute('''CREATE TABLE tools (
                id INTEGER PRIMARY KEY CHECK(id > 0), name TEXT NOT NULL,
                diameter REAL NOT NULL CHECK(diameter > 0),
                target INTEGER NOT NULL CHECK(target BETWEEN 0 AND 6),
                tol_min REAL NOT NULL CHECK(tol_min >= 0),
                tol_max REAL NOT NULL CHECK(tol_max >= tol_min),
                shape INTEGER NOT NULL CHECK(shape BETWEEN 0 AND 6),
                position INTEGER NOT NULL, record TEXT NOT NULL)''')
            connection.execute('CREATE INDEX tools_target_diameter ON tools(target, diameter)')
            connection.execute('CREATE INDEX tools_target_tolerance ON tools(target, tol_min, tol_max)')
            connection.executemany('INSERT INTO metadata VALUES (?, ?)',
                                   [('revision', '0'), ('units', '')])
            connection.execute('PRAGMA application_id = %d' % APPLICATION_ID)
            connection.execute('PRAGMA user_version = %d' % SCHEMA_VERSION)
        _check_schema(connection)
        metadata = _metadata(connection, units)
        revision = int(metadata['revision'])
        if expected_revision is not None and revision != expected_revision:
            raise ValueError('The tools database changed outside this editor. '
                             'Export your edits, then reopen the library before saving.')
        connection.execute('CREATE TEMP TABLE retained_ids (id INTEGER PRIMARY KEY)')
        connection.executemany('INSERT INTO retained_ids VALUES (?)', ((int(key),) for key in retained))
        connection.execute('DELETE FROM tools WHERE id NOT IN (SELECT id FROM retained_ids)')
        connection.executemany('''INSERT INTO tools VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, diameter=excluded.diameter,
            target=excluded.target, tol_min=excluded.tol_min, tol_max=excluded.tol_max,
            shape=excluded.shape, position=excluded.position, record=excluded.record
            WHERE tools.record != excluded.record OR tools.position != excluded.position''', rows)
        connection.execute("UPDATE metadata SET value=? WHERE key='revision'", (str(revision + 1),))
        if units:
            connection.execute("UPDATE metadata SET value=? WHERE key='units'", (units.upper(),))
    return revision + 1


def matching_tools(records, target=None, diameter=None):
    """Exact diameter wins over tolerance; specific operations win over General.

    Multiple equally suitable records are returned so callers can reject ambiguity.
    """
    candidates = {key: tool for key, tool in records.items()
                  if target is None or tool['data']['tool_target'] in (0, target)}
    if diameter is not None:
        exact = {key: tool for key, tool in candidates.items() if tool['tooldia'] == diameter}
        candidates = exact or {key: tool for key, tool in candidates.items()
                               if tool['data'].get('tol_min', 0.0) <= diameter <=
                               tool['data'].get('tol_max', 0.0)}
        if target is not None:
            specific = {key: tool for key, tool in candidates.items()
                        if tool['data']['tool_target'] == target}
            candidates = specific or candidates
    return candidates


def normalize_apertures(records):
    """Store plain editor aperture definitions, never board geometry or object attributes."""
    if not isinstance(records, dict):
        raise ValueError('Aperture presets must be a dictionary.')
    normalized = {}
    for code, aperture in records.items():
        if type(code) is not int or not 10 <= code <= 2147483647:
            raise ValueError('Aperture code must be an integer between 10 and 2147483647.')
        if not isinstance(aperture, dict):
            raise ValueError('Aperture %s: expected a definition dictionary.' % code)
        kind = aperture.get('type')
        if kind not in ('C', 'R', 'O') or aperture.get('hole'):
            raise ValueError('Aperture %s: only plain C, R and O apertures can be saved as presets.' % code)
        if aperture.get('units') not in ('MM', 'IN') or type(aperture.get('autoload')) is not bool:
            raise ValueError('Aperture %s: invalid units or Autoload value.' % code)
        record = {'type': kind, 'units': aperture['units'], 'autoload': aperture['autoload']}
        for field in (('size',) if kind == 'C' else ('width', 'height')):
            value = aperture.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('Aperture %s: %s must be finite and positive.' % (code, field))
            record[field] = value
        if kind != 'C':
            record['size'] = math.hypot(record['width'], record['height'])
            if not math.isfinite(record['size']):
                raise ValueError('Aperture %s: dimensions are too large.' % code)
        normalized[code] = record
    return normalized


def _read_apertures(connection):
    # Existing libraries have no aperture table until the first preset is saved.
    if not connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='apertures'").fetchone():
        return {}
    return normalize_apertures({code: json.loads(payload) for code, payload in connection.execute(
        'SELECT code, record FROM apertures ORDER BY code')})


def read_apertures(filename):
    with _connection(filename) as connection:
        _check_schema(connection)
        return _read_apertures(connection)


def write_apertures(filename, records, *, expected):
    records = normalize_apertures(records)
    rows = [(code, json.dumps(record, separators=(',', ':'), allow_nan=False))
            for code, record in records.items()]
    with _connection(filename, write=True) as connection:
        _check_schema(connection)
        if _read_apertures(connection) != expected:
            raise ValueError('The aperture database changed outside this window. Reopen it before saving.')
        connection.execute('CREATE TABLE IF NOT EXISTS apertures (code INTEGER PRIMARY KEY, record TEXT NOT NULL)')
        connection.execute('DELETE FROM apertures')
        connection.executemany('INSERT INTO apertures VALUES (?, ?)', rows)
    return records
