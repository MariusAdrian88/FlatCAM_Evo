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


_WCS_P_NUMBERS = {'G54': 1, 'G55': 2, 'G56': 3, 'G57': 4, 'G58': 5, 'G59': 6, 'G59.1': 7, 'G59.2': 8, 'G59.3': 9}


def grbl_wcs_p_number(wcs):
    """The G10 P number of a work coordinate system: G54..G59 -> 1..6, G59.1..G59.3 -> 7..9.

    :raises ValueError: for any other name
    """
    try:
        return _WCS_P_NUMBERS[wcs]
    except KeyError:
        raise ValueError("Unknown work coordinate system: %s" % (wcs,)) from None


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
_SIGNALS_RE = re.compile(r'^\s*\[SIGNALS:([^\]]*)\]')
_AUX_IO_RE = re.compile(r'^\s*\[AUX IO:([^\]]*)\]')

# the axis letters of the machines that this application drives (XYZ only)
GRBL_XYZ_AXES = 'XYZ'

# the size of the GRBL serial receive buffer (GRBL 1.1 on an ATmega328P), used when OPT does not report it
GRBL_RX_BUFFER = 127
# LINE_BUFFER_SIZE of GRBL 1.1: the longest line GRBL accepts, with the line end
GRBL_MAX_LINE = 80
# LINE_BUFFER_SIZE of grblHAL (core/protocol.h): 256 characters plus the terminator
GRBLHAL_MAX_LINE = 257


def parse_grbl_identity(text):
    """
    Parse the identity tags of the ``$I+`` answer of grblHAL (``$I`` has only the options line).

    :param text: raw text of the answer, for example '[AXS:3:XYZ]', '[NEWOPT:ENUMS,RT-,TC]',
        '[FIRMWARE:grblHAL]'.
    :return: dict with 'firmware' (str or None), 'axes' (int or None), 'axis_letters' (str or None) and
        'options' (set of str, the NEWOPT list), 'opt_flags' (set of the legacy OPT letters, for example
        'V', 'M', 'D'; None without a well-formed OPT line), 'signals' (set of the SIGNALS letters, or None)
        'rx_buffer' (int, the 3rd OPT field, or None) and 'aux_io' (tuple of 4 ints: digital in, digital out,
        analog in, analog out; or None); None if the
        answer holds none of these tags.
    """
    if not text:
        return None
    identity = {'firmware': None, 'axes': None, 'axis_letters': None, 'options': set(),
                'opt_flags': None, 'signals': None, 'aux_io': None, 'rx_buffer': None}
    axs_records, opt_records = [], []
    seen = False
    for line in text.splitlines():
        line = line.strip()
        match = _SIGNALS_RE.match(line)
        if match:
            identity['signals'] = {ch for ch in match.group(1) if ch.isalnum()}
            seen = True
            continue
        match = _AUX_IO_RE.match(line)
        if match:
            parts = [part.strip() for part in match.group(1).split(',')]
            if len(parts) == 4 and all(re.fullmatch(r'[0-9]+', part) for part in parts):
                identity['aux_io'] = tuple(int(part) for part in parts)
            seen = True
            continue
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
            if match and identity['opt_flags'] is None:
                identity['opt_flags'] = {ch for ch in parts[0].upper() if ch.isalpha()}
            if (match and identity['rx_buffer'] is None and len(parts) >= 3 and
                    re.fullmatch(r'[0-9]+', parts[2]) and int(parts[2]) > 0):
                identity['rx_buffer'] = int(parts[2])
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
        off, use 0x80, 0x82 and 0x81). The IO fields are True / False / None (unknown): 'mist' (OPT 'M'),
        'spindle_dir' (False if OPT 'D': the direction pin is the enable pin), 'control_signals' (set of the
        SIGNALS letters, or None) and 'aux_out' (int, the number of digital outputs, 0 if not reported),
        'rx_buffer' (int or None: the receive buffer that the controller reported) and 'max_line' (int: the
        longest line with its line end, GRBL_MAX_LINE or GRBLHAL_MAX_LINE).
    """
    if not identity:
        return {'grblhal': False, 'xyz_only': None, 'manual_tool_change': False,
                'automatic_tool_change': False, 'probe': None, 'top_bit_realtime': False,
                'mist': None, 'spindle_dir': None, 'control_signals': None, 'aux_out': 0,
                'rx_buffer': None, 'max_line': GRBL_MAX_LINE}
    options = identity['options']
    flags = identity.get('opt_flags')
    aux_io = identity.get('aux_io')
    axes, letters = identity['axes'], identity['axis_letters']
    if axes is None:
        xyz_only = None
    else:
        xyz_only = axes == len(GRBL_XYZ_AXES) and (letters or GRBL_XYZ_AXES).upper() == GRBL_XYZ_AXES
    grblhal = (identity['firmware'] or '').lower() == 'grblhal'
    return {
        'grblhal': grblhal,
        'rx_buffer': identity.get('rx_buffer'),
        'max_line': GRBLHAL_MAX_LINE if grblhal else GRBL_MAX_LINE,
        'xyz_only': xyz_only,
        'manual_tool_change': 'TC' in options,
        'automatic_tool_change': 'ATC' in options or 'ATC=1' in options,
        'probe': False if 'NOPROBE' in options else (True if options else None),
        'top_bit_realtime': 'RT-' in options,
        'mist': None if flags is None else 'M' in flags,
        'spindle_dir': None if flags is None else 'D' not in flags,
        'control_signals': identity.get('signals'),
        'aux_out': aux_io[1] if aux_io else 0,
    }


# Pn letters of GRBL 1.1 and grblHAL -> display name
_PIN_NAMES = {'X': 'X Limit', 'Y': 'Y Limit', 'Z': 'Z Limit', 'P': 'Probe', 'D': 'Door', 'H': 'Hold',
              'R': 'Reset', 'S': 'Cycle Start', 'E': 'E-stop'}
_LIMIT_LETTERS = 'XYZ'
_CONTROL_ORDER = 'DHRSE'
_GRBL_CONTROLS = 'DHRS'
_ACCESSORY_LETTERS = (('S', 'Spindle CW'), ('C', 'Spindle CCW'), ('F', 'Flood'), ('M', 'Mist'))
PIN_REPORT_OFF_REASON = 'pin reporting off ($10)'
PIN_REPORT_UNKNOWN_REASON = 'pin reporting unknown ($10 not read)'


def grbl_io_signals(caps):
    """
    The ordered input signal names of a controller: X/Y/Z limit, Probe (unless the controller reported none),
    then the control signals (the SIGNALS letters if reported, else Door, Hold, Reset, Cycle Start).
    No duplicates.

    :param caps: a dict from grbl_capabilities().
    """
    signals = ['%s Limit' % axis for axis in _LIMIT_LETTERS]
    if caps.get('probe') is not False:
        signals.append(_PIN_NAMES['P'])
    reported = caps.get('control_signals')
    letters = _GRBL_CONTROLS if reported is None else ''.join(
        ch for ch in _CONTROL_ORDER if ch in {c.upper() for c in reported})
    for ch in letters:
        if _PIN_NAMES[ch] not in signals:
            signals.append(_PIN_NAMES[ch])
    return signals


def _as_report(report_or_line):
    if isinstance(report_or_line, dict):
        return report_or_line
    return parse_grbl_status_report(report_or_line)


def decode_grbl_pins(report_or_line, signals, pin_report):
    """
    The input states of 1 status report.

    :param report_or_line: a dict from parse_grbl_status_report(), or the report line.
    :param signals: the names from grbl_io_signals().
    :param pin_report: True if the controller reports the pins (GRBL 1.1, or grblHAL with $10 bit 4).
    :return: (states, reason): states maps each name of `signals` to True, False or None (unknown); reason is
        a str when the states are unknown, else None.
    """
    report = _as_report(report_or_line)
    if report is None:
        return {name: None for name in signals}, 'no status report'
    if 'pins' in report:
        on = {_PIN_NAMES[ch] for ch in report['pins'].upper() if ch in _PIN_NAMES}
        return {name: name in on for name in signals}, None
    if pin_report:
        return {name: False for name in signals}, None
    if pin_report is None:
        return {name: None for name in signals}, PIN_REPORT_UNKNOWN_REASON
    return {name: None for name in signals}, PIN_REPORT_OFF_REASON


def decode_grbl_accessory(report_or_line, is_grblhal, previous=None):
    """
    The accessory output states of 1 status report.

    ``A:`` (also empty) sets all of them. Without it, GRBL reports all off only in a report that has ``Ov:``;
    otherwise (and always for grblHAL) the previous states stay.

    :param previous: dict name -> True / False / None of the last known states, or None.
    :return: dict name -> True / False / None for Spindle CW, Spindle CCW, Flood and Mist.
    """
    names = [name for _letter, name in _ACCESSORY_LETTERS]
    result = {name: (previous or {}).get(name) for name in names}
    report = _as_report(report_or_line)
    if report is None:
        return result
    if 'accessory' in report:
        letters = report['accessory'].upper()
        return {name: letter in letters for letter, name in _ACCESSORY_LETTERS}
    if not is_grblhal and 'ov' in report:
        return {name: False for name in names}
    return result
