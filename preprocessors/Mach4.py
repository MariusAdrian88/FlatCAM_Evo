# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# MIT Licence                                              #
# ##########################################################

import re

from preprocessors.default import default


class Mach4(default):
    """Mach4 milling/drilling profile for manual tool changes; M00 carries the tool message."""

    # Mach4 needs a program end (M2 or M30); the exporter writes it after the user postamble.
    include_footer = True

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is the default preprocessor.)',
            '(This preprocessor is used with Mach4.)')
        return self.patch(
            gcode,
            '(It is made to work with MACH3 compatible motion controllers.)',
            '(Manual tool-change profile: M6 runs the configured Mach4 tool-change action.)\n'
            '(M00 is the explicit pause and shows the tool message; G4 P uses decimal seconds.)\n'
            '(If the Mach4 M6 macro also pauses, resume twice.)')

    def toolchange_code(self, p):
        # Mach4 shows the text in the parentheses of an M00 block; it does not use the '(MSG, ...)' form.
        gcode, count = re.subn(r'\(MSG, ([^)]*)\)\nM0', r'M00 (\1)', super().toolchange_code(p))
        if not count:
            raise ValueError('The parent preprocessor text changed, the expected (MSG, ...) pause is missing')
        return gcode

    def dwell_code(self, p):
        # Mach4 reads P with a decimal point as seconds (without one as milliseconds).
        if p.dwelltime:
            return 'G4 P' + self.seconds_text(p.dwelltime)
        return ''
