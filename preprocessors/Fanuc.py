# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_11 import GRBL_11


class Fanuc(GRBL_11):
    """FANUC 0i-MC ISO milling/drilling profile for restricted 3-axis linear work and manual tool changes."""

    def start_code(self, p):
        gcode = self.patch(
            super().start_code(p),
            '(This preprocessor is used with a motion controller loaded with GRBL firmware.)',
            '(This preprocessor is used with a FANUC 0i-MC ISO machining center.)')
        gcode = self.patch(
            gcode,
            '(It is configured to be compatible with almost any version of GRBL firmware.)',
            '(Restricted to 3-axis linear milling and drilling; the tool change is manual.)')
        select = 'G20' if p.units.upper() == 'IN' else 'G21'
        setup = '(SETUP: select the work coordinate system and set the tool tip reference)\n' \
                '(with length compensation canceled by G49, then press Cycle Start.)\n' \
                'M00'
        return self.patch(
            gcode,
            '%s\nG90\nG17\nG94\n' % select,
            '%s\nG17\nG90\nG94\nG40\nG49\nG80\nM05\n%s\n' % (select, setup))

    def toolchange_code(self, p):
        units = ' ' + str(p['units']).lower()
        z_toolchange = p.z_toolchange
        toolchangexy = p.xy_toolchange
        tool_dia = '%.*f' % (p.decimals, p.toolC)

        if p.startz is not None and p.is_first_tool:
            z_toolchange = p.startz

        gcode = 'M05\n'
        gcode += 'G00 Z' + self.coordinate_format % (p.coords_decimals, z_toolchange) + '\n'
        if toolchangexy is not None:
            gcode += 'G00 X' + self.coordinate_format % (p.coords_decimals, toolchangexy[0]) + \
                     ' Y' + self.coordinate_format % (p.coords_decimals, toolchangexy[1]) + '\n'
        gcode += '(Change to FlatCAM tool %s, Dia = %s%s; reestablish the tool tip reference with length ' \
                 'compensation canceled.)\n' % (str(p.tool), tool_dia, units)
        gcode += 'M00'
        return gcode

    def dwell_code(self, p):
        # FANUC G04 X is a decimal number of seconds; its P form is an internal integer unit.
        if p.dwelltime:
            return 'G04 X' + self.seconds_text(p.dwelltime)
        return ''

    def finalize_gcode(self, gcode):
        """
        The FANUC program envelope: percent, program number, the assembled program, program end and percent. O0001 is a
        fixed FlatCAM fallback, not a vendor default: rename it in the control when O0001 is already in use.
        """
        body = gcode.rstrip('\n')
        lines = body.splitlines()
        # Preserve the user's program end; M30 ends the program and rewinds it, unlike M02.
        end = '' if lines and lines[-1].strip().upper() in ('M02', 'M2', 'M30') else '\nM30'
        return '%%\nO0001\n%s%s\n%%\n' % (body, end)
