# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_11 import GRBL_11


class LinuxCNC(GRBL_11):
    """LinuxCNC milling/drilling profile for manual or touch-off tool changes; no tool-table ATC or G43."""

    include_footer = True

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is used with a motion controller loaded with GRBL firmware.)',
            '(This preprocessor is used with LinuxCNC.)')
        gcode = self.patch(
            gcode,
            '(It is configured to be compatible with almost any version of GRBL firmware.)',
            '(Manual/touch-off workflow: M6 uses the machine tool-change configuration.)\n'
            '(G49 cancels any tool length offset; no G43 is written.)')
        # Leave path control to the operator; a bare G64 would blend without a tolerance.
        return self.patch(gcode, 'G94\n', 'G94\nG40\nG49\n')

    def toolchange_code(self, p):
        # LinuxCNC M6 performs the tool change (hal_manualtoolchange stops the spindle and prompts); a following
        # M0 would ask for a second Cycle Start.
        return self.patch(super().toolchange_code(p), '\nM0\n', '\n')
