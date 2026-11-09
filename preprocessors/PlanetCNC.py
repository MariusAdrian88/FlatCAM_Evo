# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.default import default


class PlanetCNC(default):
    """PlanetCNC TNG milling/drilling profile; M6 may not pause, so M0 is the explicit manual tool-change pause."""

    # PlanetCNC needs a program end (M2); the exporter writes it after the user postamble.
    include_footer = True

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is the default preprocessor.)',
            '(This preprocessor is used with PlanetCNC TNG.)')
        return self.patch(
            gcode,
            '(It is made to work with MACH3 compatible motion controllers.)',
            '(Manual tool-change profile: M6 runs the configured PlanetCNC tool change; M0 is the explicit pause.)\n'
            '(The tool message is shown before M0; dismiss it, then press Cycle Start; if Tool Change > Action also '
            'pauses, resume after each prompt.)')
