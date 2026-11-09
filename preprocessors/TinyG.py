# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_11 import GRBL_11


class TinyG(GRBL_11):
    """TinyG / g2core (G2core) milling/drilling profile for restricted 3-axis linear work and manual tool changes.

    Only the dynamic unit selection (G20/G21), G90, G17 and G94 are written at the start. No work coordinate system,
    reference return, path blending or tool length compensation is chosen: the operator selects the work zero and the
    path control on the controller. TinyG has no universal tool offset support, and an M6 neither changes the tool nor
    guarantees a stop, so every tool change is manual. The spindle stops, the tool retracts to the safe Z, the operator
    changes the tool, reestablishes the work Z and resumes at M0.
    """

    include_footer = True

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is used with a motion controller loaded with GRBL firmware.)',
            '(This preprocessor is used with a TinyG or g2core (G2core) motion controller.)')
        return self.patch(
            gcode,
            '(It is configured to be compatible with almost any version of GRBL firmware.)',
            '(Restricted to 3-axis linear milling and drilling; the tool change is manual.)')

    def toolchange_code(self, p):
        units = ' ' + str(p['units']).lower()
        z_toolchange = p.z_toolchange
        toolchangexy = p.xy_toolchange
        tool_dia = '%.*f' % (p.decimals, p.toolC)

        if p.startz is not None and p.is_first_tool:
            z_toolchange = p.startz

        gcode = 'M5\n'
        gcode += 'G00 Z' + self.coordinate_format % (p.coords_decimals, z_toolchange) + '\n'
        if toolchangexy is not None:
            gcode += 'G00 X' + self.coordinate_format % (p.coords_decimals, toolchangexy[0]) + \
                     ' Y' + self.coordinate_format % (p.coords_decimals, toolchangexy[1]) + '\n'
        gcode += '(Change to FlatCAM tool %s, Dia = %s%s; reestablish the work Z before resume.)\n' % (
            str(p.tool), tool_dia, units)
        gcode += 'M0'
        return gcode

    def spindle_code(self, p):
        sdir = {'CW': 'M3', 'CCW': 'M4'}[p.spindledir]
        if p.spindlespeed:
            return '%s S%s' % (sdir, str(p.spindlespeed))
        else:
            return sdir

    def dwell_code(self, p):
        # TinyG reads G4 P as decimal seconds; the dwell is not scaled by the unit mode.
        if p.dwelltime:
            return 'G4 P' + self.seconds_text(p.dwelltime, max(3, p.decimals))
        return ''

    def spindle_stop_code(self, p):
        return 'M5'
