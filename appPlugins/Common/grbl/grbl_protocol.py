# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/15/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Pure parsers for the replies of a GRBL 1.1 controller.

No Qt imports, no app imports - standard library only, so these functions
can be unit tested without the GUI stack.
"""

import math
import re


_PRB_RE = re.compile(r'\[PRB:\s*([^\]:]+):\s*([01])\s*\]')


def parse_grbl_probe_output(text):
    """
    Parse GRBL 1.1 probe results out of the raw serial answer stream.

    Every ``G38.2`` probe move is answered by GRBL with a line such as
    ``[PRB:10.000,5.000,-1.234:1]``. Other lines (``ok``, status reports
    ``<Idle|...>``, ``[MSG:...]``, blank lines, ...) are ignored. A PRB
    line can carry more than 3 axis values; only the first 3 (X, Y, Z)
    are kept.

    :param text: raw text captured from the controller.
    :return: list of (x, y, z) tuples, in the order the probes occurred.
    :raises ValueError: if a probe line reports flag ``:0`` (the probe
        did not touch the surface).
    """
    results = []
    for line in text.splitlines():
        m = _PRB_RE.search(line)
        if not m:
            continue

        coords_str, flag = m.group(1), m.group(2)
        parts = [p.strip() for p in coords_str.split(',') if p.strip() != '']
        if len(parts) < 3:
            continue

        x = float(parts[0])
        y = float(parts[1])
        z = float(parts[2])

        if not all(math.isfinite(value) for value in (x, y, z)):
            raise ValueError("Probe result contains non-finite coordinates")

        if flag == '0':
            raise ValueError(
                "Probe did not touch the surface at X={0} Y={1}".format(x, y)
            )

        results.append((x, y, z))

    return results


_ACTIVE_WCS_RE = re.compile(r'(?<![A-Z0-9.])G(5[4-9](?:\.[1-3])?)(?![A-Z0-9.])')
_OFFSET_RE = re.compile(r'\[(G5[4-9](?:\.[1-3])?|G92|TLO)(?=[:\]\s]|$)(.*)')


def parse_grbl_active_wcs(text):
    """Parse the active G54-G59 (or grblHAL G59.1-G59.3) work coordinate system from ``$G`` output."""
    match = _ACTIVE_WCS_RE.search(text)
    if not match:
        raise ValueError("Active G54-G59 work offset not found in $G output")
    return 'G' + match.group(1)


def parse_grbl_offsets(text):
    """
    Parse every offset record of the raw ``$#`` command answer.

    :return: dict with the G54-G59 and G59.1-G59.3 offsets (tuples of 3 floats), plus 'G92'
        (tuple of 3 floats, zero when absent) and 'TLO' (float or XYZ tuple of 3 floats,
        scalar zero when absent).
    :raises ValueError: if an offset record is malformed, duplicated or nonfinite.
    """
    records = {}
    for line in text.splitlines():
        # Recognize the record key even if its payload or closing bracket is
        # truncated. A malformed optional G92/TLO is not an absent zero offset.
        m = _OFFSET_RE.fullmatch(line.strip())
        if not m:
            continue

        key, val = m.group(1), m.group(2)
        if key in records or not val.startswith(':') or not val.endswith(']'):
            raise ValueError("Malformed or duplicate %s offset in $# output" % key)
        parts = [p.strip() for p in val[1:-1].split(',')]
        if len(parts) not in ((1, 3) if key == 'TLO' else (3,)):
            raise ValueError("Invalid %s offset field count in $# output" % key)
        try:
            values = tuple(float(p) for p in parts)
        except ValueError as err:
            raise ValueError("Invalid %s offset number in $# output" % key) from err
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Nonfinite %s offset in $# output" % key)
        records[key] = values[0] if key == 'TLO' and len(values) == 1 else values

    records.setdefault('G92', (0.0, 0.0, 0.0))
    records.setdefault('TLO', 0.0)
    return records


def parse_grbl_work_offset(text, active_wcs='G54'):
    """
    Parse the work-coordinate offset to SUBTRACT from a GRBL machine
    position, out of the raw ``$#`` command answer.

    GRBL reports probe/machine positions in MACHINE coordinates, but
    FlatCAM's probed height-map points are in WORK coordinates (the
    coordinate system the G-code itself is written in). The ``$#``
    command answer holds lines like::

        [G54:-100.000,-50.000,-20.000]
        [G55:0.000,0.000,0.000]
        [G92:0.000,0.000,0.000]
        [TLO:0.000]
        [PRB:0.000,0.000,0.000:0]

    The selected G54-G59 work coordinate system is read; the
    ``[PRB:...]`` line in this output is not a probe result and is
    ignored.

    :param text: raw text captured from the controller after ``$#``.
    :param active_wcs: selected work coordinate system, defaulting to G54.
    :return: (x, y, z) offset, computed as selected WCS + G92 per axis,
        with scalar TLO added to Z or vector TLO added per axis. Missing G92 or TLO count as 0.
    :raises ValueError: if the selected WCS line is missing, an offset record
        is malformed, duplicated or nonfinite, or the combined offset overflows.
    """
    records = parse_grbl_offsets(text)
    if active_wcs not in records:
        raise ValueError("%s work offset not found in $# output" % active_wcs)
    work_offset = records[active_wcs]
    g92 = records['G92']
    tlo = records['TLO']
    if not isinstance(tlo, tuple):
        tlo = (0.0, 0.0, tlo)

    x = work_offset[0] + g92[0] + tlo[0]
    y = work_offset[1] + g92[1] + tlo[1]
    z = work_offset[2] + g92[2] + tlo[2]
    if not all(math.isfinite(value) for value in (x, y, z)):
        raise ValueError("Nonfinite combined work offset in $# output")
    return x, y, z


_GRBL_ERROR_RE = re.compile(r'^\s*((?:error|ALARM)\s*:\s*\d+)', re.IGNORECASE | re.MULTILINE)
_SETTING_RE = re.compile(r'^\s*\$(\d+)\s*=\s*([+\-]?\d*\.?\d+)', re.MULTILINE)
_STATUS_RE = re.compile(r'^\s*<\s*([A-Za-z]+)')
_GC_RE = re.compile(r'\[GC:([^\]]*)\]')


def grbl_reply_error(text):
    """
    Find the first GRBL error or alarm line in a controller reply.

    :param text: decoded reply text; it can hold more than 1 line.
    :return: the text of the first 'error:N' or 'ALARM:N' line, or None.
    """
    if not text:
        return None
    m = _GRBL_ERROR_RE.search(text)
    return m.group(1) if m else None


def probe_timeout_s(travel_z, depth, feedrate, margin_s=5.0):
    """
    Time limit for 1 G38.2 probe move: the time to move from travel_z to
    depth at the probe feedrate (units per minute, G94), times 1.5, plus a margin.

    :return: seconds (float), never less than 10.0.
    """
    try:
        distance = abs(float(travel_z) - float(depth))
        feed = float(feedrate)
    except (TypeError, ValueError):
        return 10.0
    if not math.isfinite(distance) or not math.isfinite(feed) or feed <= 0:
        return 10.0
    return max(10.0, distance / feed * 60.0 * 1.5 + margin_s)


def grbl_report_scale(app_units, report_inches):
    """
    Factor that changes GRBL reported positions ([PRB:], $# offsets) into
    the application units.

    :param app_units: 'MM' or 'IN'.
    :param report_inches: True if the GRBL setting $13 is 1 (reports in inches).
    :return: 1.0, 25.4 or 1 / 25.4.
    """
    app_in = str(app_units).upper() == 'IN'
    if app_in == bool(report_inches):
        return 1.0
    return 1.0 / 25.4 if app_in else 25.4


def parse_grbl_setting(text, number):
    """
    :param text: the reply of the GRBL '$$' command.
    :param number: the setting number (int), for example 13.
    :return: the setting value as float, or None if it is not in the text.
    """
    if not text:
        return None
    for m in _SETTING_RE.finditer(text):
        if int(m.group(1)) == int(number):
            return float(m.group(2))
    return None


def parse_grbl_status_state(line):
    """
    :param line: a GRBL status report, for example '<Idle|MPos:0.000,0.000,0.000|FS:0,0>'.
    :return: the machine state word ('Idle', 'Run', 'Hold', 'Jog', 'Alarm',
        'Door', 'Check', 'Home', 'Sleep'), or None.
    """
    if not line:
        return None
    m = _STATUS_RE.match(line)
    return m.group(1) if m else None


def grbl_modes_confirmed(gc_text, units_cmd):
    """
    :param gc_text: the reply of the GRBL '$G' command.
    :param units_cmd: 'G20' or 'G21'.
    :return: True if the active modes include units_cmd, G90 and G94.
    """
    if not gc_text:
        return False
    m = _GC_RE.search(gc_text)
    if not m:
        return False
    words = m.group(1).split()
    return units_cmd in words and 'G90' in words and 'G94' in words


def parse_grbl_status_report(line):
    """
    Parse a GRBL 1.1 or grblHAL status report, for example
    '<Run|MPos:1.000,2.000,-0.500|FS:500,10000|Ov:100,100,100|WCO:0.000,0.000,0.000>'.

    :param line: 1 line of text.
    :return: dict with 'state' (str), and if they are in the report: 'substate' (str, for example the alarm
        code), 'mpos' and 'wpos' (tuples of 3 floats; a report with more axes is not accepted), 'wco'
        (tuple of 3 floats), 'feed' and 'spindle' (floats), 'spindle_actual' (float, the measured RPM that
        grblHAL adds to FS), 'ov' (tuple of 3 ints: feed, rapid, spindle), 'buffer' (tuple of 2 ints: planner
        blocks, RX bytes available), 'line' (int), 'accessory' (str, for example 'SF': S = spindle CW,
        C = spindle CCW, F = flood, M = mist, T = tool change pending; GRBL sends it with 'Ov', and only
        when an accessory is on), 'tool_pending' (bool), 'pins' (str), 'mpg' (bool: a pendant has the
        stream), 'wcs' (str, for example 'G54'), 'sd' (str, the SD card mount or streaming state) and
        'tool' (int, the selected tool); None if `line` is not a status report.
    """
    text = (line or '').strip()
    if not (text.startswith('<') and text.endswith('>')):
        return None
    fields = text[1:-1].split('|')
    if not fields or not fields[0]:
        return None
    report = {'state': fields[0].split(':')[0]}
    if ':' in fields[0]:
        report['substate'] = fields[0].split(':', 1)[1]

    def floats(value, count):
        parts = value.split(',')
        if len(parts) != count:
            raise ValueError(value)
        result = tuple(float(p) for p in parts)
        if not all(math.isfinite(v) for v in result):
            raise ValueError(value)
        return result

    try:
        for field in fields[1:]:
            key, _sep, value = field.partition(':')
            if key == 'MPos':
                report['mpos'] = floats(value, 3)
            elif key == 'WPos':
                report['wpos'] = floats(value, 3)
            elif key == 'WCO':
                report['wco'] = floats(value, 3)
            elif key == 'FS':
                # grblHAL adds the actual spindle RPM when the spindle has an encoder
                parts = value.split(',')
                if len(parts) not in (2, 3):
                    raise ValueError(value)
                values = tuple(float(p) for p in parts)
                if not all(math.isfinite(v) for v in values):
                    raise ValueError(value)
                report['feed'], report['spindle'] = values[0], values[1]
                if len(values) == 3:
                    report['spindle_actual'] = values[2]
            elif key == 'F':
                report['feed'] = floats(value, 1)[0]
            elif key == 'Ov':
                report['ov'] = tuple(int(v) for v in floats(value, 3))
            elif key == 'Bf':
                report['buffer'] = tuple(int(v) for v in floats(value, 2))
            elif key == 'Ln':
                report['line'] = int(value)
            elif key == 'A':
                report['accessory'] = value
                report['tool_pending'] = 'T' in value
            elif key == 'Pn':
                report['pins'] = value
            elif key == 'MPG':
                report['mpg'] = value.strip() == '1'
            elif key == 'WCS':
                report['wcs'] = value.strip()
            elif key == 'SD':
                report['sd'] = value
            elif key == 'T':
                report['tool'] = int(value)
    except ValueError:
        return None
    return report


def work_position(report, last_wco=None):
    """
    :param report:   a dict from parse_grbl_status_report().
    :param last_wco: the last WCO seen (GRBL sends WCO only in some reports).
    :return: (x, y, z) work position, or None if it can not be computed.
    """
    if report is None:
        return None
    if 'wpos' in report:
        return report['wpos']
    wco = report.get('wco', last_wco)
    if 'mpos' in report and wco is not None:
        return tuple(m - o for m, o in zip(report['mpos'], wco))
    return None


_AXS_RE = re.compile(r'^\s*\[AXS:(\d+)(?::([^\]]*))?\]')
_OPT_RE = re.compile(r'\[OPT:([^\]]*)\]')
_NEWOPT_RE = re.compile(r'^\s*\[NEWOPT:([^\]]*)\]')
_FIRMWARE_RE = re.compile(r'^\s*\[FIRMWARE:([^\]]*)\]')

# the axis letters of the machines that this application drives (XYZ only)
GRBL_XYZ_AXES = 'XYZ'


def parse_grbl_identity(text):
    """
    Parse the identity tags of the ``$I+`` answer of grblHAL (``$I`` has only the options line).

    :param text: raw text of the answer, for example '[AXS:3:XYZ]', '[NEWOPT:ENUMS,RT-,TC]',
        '[FIRMWARE:grblHAL]'.
    :return: dict with 'firmware' (str or None), 'axes' (int or None), 'axis_letters' (str or None) and
        'options' (set of str); None if the answer holds none of these tags.
    """
    if not text:
        return None
    identity = {'firmware': None, 'axes': None, 'axis_letters': None, 'options': set()}
    axs_records, opt_records = [], []
    seen = False
    for line in text.splitlines():
        line = line.strip()
        match = _FIRMWARE_RE.match(line)
        if match:
            identity['firmware'] = match.group(1).strip()
            seen = True
            continue
        if line.startswith('[AXS:'):
            match = _AXS_RE.fullmatch(line)
            axes = int(match.group(1)) if match else 0
            letters = ((match.group(2) or '').strip() or None) if match else None
            axs_records.append((axes or None, letters))
            seen = True
            continue
        if line.startswith('[OPT:'):
            match = _OPT_RE.fullmatch(line)
            parts = [part.strip() for part in match.group(1).split(',')] if match else []
            axes = None
            if (len(parts) in (4, 5) and all(re.fullmatch(r'[0-9]+', part) for part in parts[1:]) and
                    int(parts[1]) > 0 and int(parts[2]) > 0):
                axes = int(parts[3]) or None
            opt_records.append((axes, None))
            seen = True
            continue
        match = _NEWOPT_RE.match(line)
        if match:
            identity['options'] = {option.strip() for option in match.group(1).split(',') if option.strip()}
            seen = True
    # AXS is authoritative. Within duplicate records, never erase explicit non-XYZ evidence.
    records = axs_records or opt_records
    if records:
        axes, letters = records[0]
        for count, names in records:
            if count is None:
                axes, letters = None, None
            elif count != len(GRBL_XYZ_AXES) or (names or GRBL_XYZ_AXES).upper() != GRBL_XYZ_AXES:
                axes, letters = count, names
                break
        identity['axes'], identity['axis_letters'] = axes, letters
    return identity if seen else None


def grbl_capabilities(identity):
    """
    What the controller of a ``$I+`` answer allows.

    :param identity: a dict from parse_grbl_identity(), or None.
    :return: dict with 'grblhal' (bool), 'xyz_only' (True, False, or None for an older build that does not
        report usable AXS or extended OPT), 'manual_tool_change' (bool: M6 waits in the Tool state for the sender),
        'automatic_tool_change' (bool: M6 is transparent to the sender), 'probe' (False if the controller reported no probe input, None
        if it did not report its options) and 'top_bit_realtime' (bool: the printable '?', '!' and '~' are
        off, use 0x80, 0x82 and 0x81).
    """
    if not identity:
        return {'grblhal': False, 'xyz_only': None, 'manual_tool_change': False,
                'automatic_tool_change': False, 'probe': None, 'top_bit_realtime': False}
    options = identity['options']
    axes, letters = identity['axes'], identity['axis_letters']
    if axes is None:
        xyz_only = None
    else:
        xyz_only = axes == len(GRBL_XYZ_AXES) and (letters or GRBL_XYZ_AXES).upper() == GRBL_XYZ_AXES
    return {
        'grblhal': (identity['firmware'] or '').lower() == 'grblhal',
        'xyz_only': xyz_only,
        'manual_tool_change': 'TC' in options,
        'automatic_tool_change': 'ATC' in options or 'ATC=1' in options,
        'probe': False if 'NOPROBE' in options else (True if options else None),
        'top_bit_realtime': 'RT-' in options,
    }
