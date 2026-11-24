# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

import collections

from appCommon.machine_profile.records import CheckStatus
from appObjects.CNCJobObject import _profile_gate
from tclCommands.TclCommand import TclCommand

_STATUS_WORDS = {CheckStatus.CHECKED: 'checked', CheckStatus.PARTIAL: 'partially checked',
                 CheckStatus.VIOLATIONS: 'violations'}


class TclCommandCheckGcode(TclCommand):
    """
    Tcl shell command to check the G-code of a CNC Job against the active machine profile.

    example:
        check_gcode cnc
    """

    aliases = ['check_gcode']

    description = '%s %s' % ("-- <name>", "Checks the G-code of a CNC Job against the active machine profile.")

    arg_names = collections.OrderedDict([
        ('name', str),
    ])
    option_types = collections.OrderedDict()
    required = ['name']

    help = {
        'main': "Checks the G-code that write_gcode would write for a CNC Job against the active machine profile, "
                "without writing it. Returns the status (checked, partially checked or violations), one line per "
                "violation (line N: KIND severity: message) and the assumptions of the check.",
        'args': collections.OrderedDict([
            ('name', 'Name of the CNC Job object. Required.'),
        ]),
        'examples': ['check_gcode cnc']
    }

    def execute(self, args, unnamed_args):
        obj_name = args['name']
        obj = self.app.collection.get_by_name(str(obj_name))
        if obj is None or obj.kind != 'cncjob':
            self.raise_tcl_error('Expected an existing CNCJob object: %s' % obj_name)
        try:
            text = obj.export_gcode(preamble='', postamble='', to_file=True)
            text = text.getvalue() if hasattr(text, 'getvalue') else text
        except Exception as e:
            self.raise_tcl_error("Operation failed: %s" % str(e))
        gate = _profile_gate(obj, text)
        result = gate.result
        if result is None:
            if gate.note:
                return gate.note
            return 'Machine profile: None (not checked)'
        lines = ['Machine profile check: %s' % _STATUS_WORDS[result.status]]
        lines += ['line %d: %s %s: %s' % (v.line, v.kind.name, v.severity, v.message) for v in result.violations]
        if result.assumptions:
            lines.append('Assumptions:')
            lines += result.assumptions
        return '\n'.join(lines)
