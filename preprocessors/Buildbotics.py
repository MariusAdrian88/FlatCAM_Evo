# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.TinyG import TinyG


class Buildbotics(TinyG):
    """Buildbotics CNC Controller milling/drilling profile for restricted 3-axis linear work and manual tool changes.

    Only the dynamic unit selection (G20/G21), G90, G17 and G94 are written at the start. No work coordinate system,
    reference return, path blending or tool length compensation is chosen: the operator selects the work zero and the
    path control on the controller. The controller runs its configured program-start, tool-change and program-end
    macros. A tool change is manual: the spindle stops, the tool retracts to the safe Z, the operator changes the tool,
    reestablishes the work Z and resumes at M0. No T or M6 is written because a Buildbotics M6 runs the configured
    tool-change macro, and the controller tool number changes only with T and M6.
    """

    include_footer = True

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is used with a TinyG or g2core (G2core) motion controller.)',
            '(This preprocessor is used with a Buildbotics CNC Controller.)')
        return self.patch(
            gcode,
            '(Restricted to 3-axis linear milling and drilling; the tool change is manual.)',
            '(Restricted to 3-axis linear milling and drilling; the tool change is manual.)\n'
            '(The controller runs its configured program-end macro on M02.)\n'
            '(A Buildbotics M6 runs the configured tool-change macro; this profile writes no M6.)')

    def toolchange_code(self, p):
        return self.patch(
            super().toolchange_code(p),
            'reestablish the work Z before resume.)',
            'reestablish the work Z before resume.)\n'
            '(The Buildbotics tool number changes only with T and M6; this manual change does not update it.)')
