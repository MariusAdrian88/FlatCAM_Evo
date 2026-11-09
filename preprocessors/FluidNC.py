# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_11 import GRBL_11


class FluidNC(GRBL_11):
    """FluidNC milling/drilling profile for manual tool changes; M6 may run a configured ATC/m6 macro."""

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is used with a motion controller loaded with GRBL firmware.)',
            '(This preprocessor is used with a motion controller loaded with FluidNC firmware.)')
        return self.patch(
            gcode,
            '(It is configured to be compatible with almost any version of GRBL firmware.)',
            '(Manual tool-change profile: FluidNC M6 selects the tool/spindle or runs a configured ATC/m6 macro.)\n'
            '(M0 pauses for the manual tool change.)')

    def toolchange_code(self, p):
        # FluidNC shows a comment with 'MSG ' (no comma) in the console; M6 selects the tool and returns at once,
        # so the M0 of GRBL_11 is kept for the manual tool change.
        return self.patch(super().toolchange_code(p), '(MSG, ', '(MSG ')
