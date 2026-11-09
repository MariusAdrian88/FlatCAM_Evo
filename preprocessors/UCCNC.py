# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.default import default


class UCCNC(default):
    """UCCNC milling/drilling profile; M6 may not wait, so M0 is the explicit manual tool-change pause."""

    # UCCNC needs a program end (M2 or M30); the exporter writes it after the user postamble.
    include_footer = True

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is the default preprocessor.)',
            '(This preprocessor is used with UCCNC.)')
        return self.patch(
            gcode,
            '(It is made to work with MACH3 compatible motion controllers.)',
            '(Manual tool-change profile: M6 runs the configured UCCNC action or macro; M0 is the explicit pause.)\n'
            '(If M6 is set to stop and wait or its macro also pauses, resume twice; G4 P is in milliseconds by default.)')

    def dwell_code(self, p):
        # UCCNC's default G4 P unit is milliseconds; the 'dwell in seconds' setting is a user override.
        if p.dwelltime:
            return 'G4 P%d' % max(1, int(round(float(p.dwelltime) * 1000)))
        return ''
