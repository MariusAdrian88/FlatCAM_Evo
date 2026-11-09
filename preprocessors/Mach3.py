# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# MIT Licence                                              #
# ##########################################################

from preprocessors.default import default


class Mach3(default):
    """Mach3 milling/drilling profile for manual tool changes; M0 is the explicit pause."""

    # Mach3 needs a program end (M2 or M30); the exporter writes it after the user postamble.
    include_footer = True

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is the default preprocessor.)',
            '(This preprocessor is used with Mach3.)')
        return self.patch(
            gcode,
            '(It is made to work with MACH3 compatible motion controllers.)',
            '(Manual tool-change profile: M6 runs the configured Mach3 tool-change action and M0 is the explicit pause.)\n'
            '(If Mach3 is set to stop at M6, resume twice; set Config > General Logic > Dwell to seconds.)')
