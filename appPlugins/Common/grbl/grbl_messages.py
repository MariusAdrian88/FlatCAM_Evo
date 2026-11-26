# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Texts of the error and alarm codes of GRBL and grblHAL.

GRBL texts: the "Error Message in v1.0-" / "Alarm Message in v1.0-" column of
github.com/gnea/grbl doc/csv/error_codes_en_US.csv and alarm_codes_en_US.csv. Where that column only says
"Invalid gcode ID:NN", the description column is used. Codes that only grblHAL has: the descriptions of
github.com/grblHAL/core errors.c and alarms.c.

The texts are marked with N_ (no translation at import) and go through _() in code_text().
"""

import builtins
import re
import gettext


def N_(text):
    """Marker for the translation tools: returns the text unchanged."""
    return text


_REPLY_RE = re.compile(r'^\s*(error|alarm):(\d+)\s*$', re.IGNORECASE)

ERROR_TEXTS = {
    1: N_("Expected command letter"),
    2: N_("Bad number format"),
    3: N_("Invalid statement"),
    4: N_("Value < 0"),
    5: N_("Setting disabled"),
    6: N_("Value < 3 usec"),
    7: N_("EEPROM read fail. Using defaults"),
    8: N_("Not idle"),
    9: N_("G-code lock"),
    10: N_("Homing not enabled"),
    11: N_("Line overflow"),
    12: N_("Step rate > 30kHz"),
    13: N_("Check Door"),
    14: N_("Line length exceeded"),
    15: N_("Travel exceeded"),
    16: N_("Invalid jog command"),
    17: N_("Laser mode requires PWM output."),
    18: N_("Reset asserted"),
    19: N_("Non positive value"),
    20: N_("Unsupported command"),
    21: N_("Modal group violation"),
    22: N_("Undefined feed rate"),
    23: N_("G-code command in block requires an integer value."),
    24: N_("More than one g-code command that requires axis words found in block."),
    25: N_("Repeated g-code word found in block."),
    26: N_("No axis words found in block for g-code command or current modal state which requires them."),
    27: N_("Line number value is invalid."),
    28: N_("G-code command is missing a required value word."),
    29: N_("G59.x work coordinate systems are not supported."),
    30: N_("G53 only allowed with G0 and G1 motion modes."),
    31: N_("Axis words found in block when no command or current modal state uses them."),
    32: N_("G2 and G3 arcs require at least one in-plane axis word."),
    33: N_("Motion command target is invalid."),
    34: N_("Arc radius value is invalid."),
    35: N_("G2 and G3 arcs require at least one in-plane offset word."),
    36: N_("Unused value words found in block."),
    37: N_("G43.1 dynamic tool length offset is not assigned to configured tool length axis."),
    38: N_("Tool number greater than max supported value or undefined tool selected."),
    # grblHAL only
    39: N_("Value out of range."),
    40: N_("G-code command not allowed when tool change is pending."),
    41: N_("Spindle not running when motion commanded in CSS or spindle sync mode."),
    42: N_("Plane must be ZX for threading and lathe cycles."),
    43: N_("Max. feed rate exceeded."),
    44: N_("RPM out of range."),
    45: N_("Only homing is allowed when a limit switch is engaged."),
    46: N_("Home machine to continue."),
    47: N_("ATC: current tool is not set. Set current tool with M61."),
    48: N_("Value word conflict."),
    49: N_("Power on self test failed. A hard reset is required."),
    50: N_("Emergency stop active."),
    51: N_("Motor fault."),
    52: N_("Setting value is out of range."),
    53: N_("Setting is not available, possibly due to limited driver support."),
    54: N_("Retract position is less than drill depth."),
    55: N_("Attempt to home two auto squared axes at the same time."),
    56: N_("Coordinate system is locked."),
    57: N_("Unexpected file demarcation."),
    58: N_("Port is not available"),
    71: N_("Unknown operation found in expression."),
    72: N_("Divide by zero in expression attempted."),
    73: N_("Too large or too small argrument provided."),
    74: N_("Argument is not valid for the operation"),
    75: N_("Expression is not valid."),
    76: N_("Either NAN (not a number) or infinity was returned from expression."),
    77: N_("Authentication required."),
    78: N_("Access denied."),
    79: N_("Not allowed while critical event is active."),
    80: N_("Flow statement only allowed in filesystem macro."),
    81: N_("Unknown or invalid flow statement."),
    82: N_("Stack overflow while executing flow statement."),
    83: N_("Out of memory while executing flow statement."),
    84: N_("Could not open file."),
    86: N_("Port is not usable."),
    87: N_("Tool in spindle."),
    88: N_("No tool in spindle."),
    90: N_("Cutter compensation is already active."),
    91: N_("Command not allowed while cutter compensation is active."),
    92: N_("Cutter compensation is not valid for current move."),
}

ALARM_TEXTS = {
    1: N_("Hard limit"),
    2: N_("Soft limit"),
    3: N_("Abort during cycle"),
    4: N_("Probe fail"),
    5: N_("Probe fail"),
    6: N_("Homing fail"),
    7: N_("Homing fail"),
    8: N_("Homing fail"),
    9: N_("Homing fail"),
    # grblHAL only. Code 10 differs by dialect: see GRBLHAL_ALARM_TEXTS.
    10: N_("Homing fail"),
    11: N_("Homing required. Execute homing command ($H) to continue."),
    12: N_("Limit switch engaged. Clear before continuing."),
    13: N_("Probe protection triggered. Clear before continuing."),
    14: N_("Spindle at speed timeout. Clear before continuing."),
    15: N_("Homing fail. Could not find second limit switch for auto squared axis within search distances. "
           "Try increasing max travel, decreasing pull-off distance, or check wiring."),
    16: N_("Power on selftest (POS) failed."),
    17: N_("Motor fault."),
    18: N_("Homing fail. Bad configuration."),
    19: N_("Modbus exception. Timeout or message error."),
    20: N_("I/O expander communication failed."),
    21: N_("Non Volatile Storage (EEPROM) failure."),
    22: N_("Buffer overflow."),
}


# codes with a different meaning on grblHAL (the shared tables hold the GRBL text); the rest is shared
GRBLHAL_ERROR_TEXTS = {
    6: N_("Step pulse time must be greater or equal to 2 microseconds."),
}
GRBLHAL_ALARM_TEXTS = {
    10: N_("EStop asserted. Clear and reset"),
}


# setting number -> (description, units): GRBL 1.1 from github.com/gnea/grbl doc/csv/setting_codes_en_US.csv
# ("Setting" and "Units" columns); the grblHAL ids are listed in github.com/grblHAL/core settings.h (no units)
SETTING_TEXTS = {
    0: (N_("Step pulse time"), N_("microseconds")),
    1: (N_("Step idle delay"), N_("milliseconds")),
    2: (N_("Step pulse invert"), N_("mask")),
    3: (N_("Step direction invert"), N_("mask")),
    4: (N_("Invert step enable pin"), N_("boolean")),
    5: (N_("Invert limit pins"), N_("boolean")),
    6: (N_("Invert probe pin"), N_("boolean")),
    10: (N_("Status report options"), N_("mask")),
    11: (N_("Junction deviation"), N_("millimeters")),
    12: (N_("Arc tolerance"), N_("millimeters")),
    13: (N_("Report in inches"), N_("boolean")),
    20: (N_("Soft limits enable"), N_("boolean")),
    21: (N_("Hard limits enable"), N_("boolean")),
    22: (N_("Homing cycle enable"), N_("boolean")),
    23: (N_("Homing direction invert"), N_("mask")),
    24: (N_("Homing locate feed rate"), N_("mm/min")),
    25: (N_("Homing search seek rate"), N_("mm/min")),
    26: (N_("Homing switch debounce delay"), N_("milliseconds")),
    27: (N_("Homing switch pull-off distance"), N_("millimeters")),
    30: (N_("Maximum spindle speed"), N_("RPM")),
    31: (N_("Minimum spindle speed"), N_("RPM")),
    32: (N_("Laser-mode enable"), N_("boolean")),
    100: (N_("X-axis travel resolution"), N_("step/mm")),
    101: (N_("Y-axis travel resolution"), N_("step/mm")),
    102: (N_("Z-axis travel resolution"), N_("step/mm")),
    110: (N_("X-axis maximum rate"), N_("mm/min")),
    111: (N_("Y-axis maximum rate"), N_("mm/min")),
    112: (N_("Z-axis maximum rate"), N_("mm/min")),
    120: (N_("X-axis acceleration"), N_("mm/sec^2")),
    121: (N_("Y-axis acceleration"), N_("mm/sec^2")),
    122: (N_("Z-axis acceleration"), N_("mm/sec^2")),
    130: (N_("X-axis maximum travel"), N_("millimeters")),
    131: (N_("Y-axis maximum travel"), N_("millimeters")),
    132: (N_("Z-axis maximum travel"), N_("millimeters")),
    # grblHAL (names of the Setting_ ids of settings.h)
    7: (N_("Spindle PWM behaviour"), ""),
    8: (N_("Ganged direction invert"), N_("mask")),
    9: (N_("Spindle PWM options"), ""),
    14: (N_("Control signals invert"), N_("mask")),
    15: (N_("Coolant invert"), N_("mask")),
    16: (N_("Spindle invert"), N_("mask")),
    17: (N_("Control signals pull-up disable"), N_("mask")),
    18: (N_("Limit pins pull-up disable"), N_("mask")),
    19: (N_("Probe pull-up disable"), N_("boolean")),
    28: (N_("G73 retract"), ""),
    29: (N_("Pulse delay"), N_("microseconds")),
    33: (N_("Spindle PWM frequency"), ""),
    34: (N_("Spindle PWM off value"), ""),
    35: (N_("Spindle PWM min value"), ""),
    36: (N_("Spindle PWM max value"), ""),
    37: (N_("Steppers deenergize mask"), N_("mask")),
    38: (N_("Spindle pulses per revolution"), ""),
    39: (N_("Enable legacy realtime commands"), N_("boolean")),
    40: (N_("Jog soft limited"), N_("boolean")),
    41: (N_("Parking enable"), ""),
    42: (N_("Parking axis"), ""),
    43: (N_("Homing locate cycles"), ""),
    60: (N_("Restore overrides"), ""),
    61: (N_("Safety door options"), ""),
    62: (N_("Sleep enable"), N_("boolean")),
    63: (N_("Feed hold actions"), ""),
    64: (N_("Force init alarm"), N_("boolean")),
    65: (N_("Probing flags"), ""),
}


def setting_text(number):
    """
    Description and units of a setting, translated.

    :param number: the setting number, int or str.
    :return: (description, units); ('', '') if the number is not known.
    """
    try:
        description, units = SETTING_TEXTS[int(number)]
    except (KeyError, ValueError):
        return '', ''
    translate = builtins.__dict__.get('_', gettext.gettext)
    return translate(description), (translate(units) if units else '')


def code_text(reply, grblhal=False):
    """
    The reply of a controller with the text of its code.

    :param reply: 'error:20' or 'ALARM:1'.
    :param grblhal: True for a grblHAL controller: its own text is used for the codes that differ.
    :return: 'error:20 - Unsupported command'; the reply unchanged if the code is not known.
    """
    match = _REPLY_RE.match(reply or '')
    if match is None:
        return reply
    is_error = match.group(1).lower() == 'error'
    number = int(match.group(2))
    text = None
    if grblhal:
        text = (GRBLHAL_ERROR_TEXTS if is_error else GRBLHAL_ALARM_TEXTS).get(number)
    if text is None:
        text = (ERROR_TEXTS if is_error else ALARM_TEXTS).get(number)
    if text is None:
        return reply
    translate = builtins.__dict__.get('_', gettext.gettext)
    return '%s - %s' % (reply.strip(), translate(text))
