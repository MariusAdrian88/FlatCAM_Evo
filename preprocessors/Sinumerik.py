# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.Fanuc import Fanuc
from preprocessors.GRBL_11 import GRBL_11


class Sinumerik(Fanuc):
    """SINUMERIK 840D sl / 828D ISO Dialect M milling/drilling profile for restricted 3-axis linear work,
    manual tool changes and a plain program-manager main program."""

    # G291 must be the first block of the file; the application header would put comments in front of it.
    include_header = False
    # the exporter writes the program end after the user postamble; finalize_gcode turns it into M30.
    include_footer = True

    def start_code(self, p):
        gcode = self.patch(
            GRBL_11.start_code(self, p),
            '(This preprocessor is used with a motion controller loaded with GRBL firmware.)\n'
            '(It is configured to be compatible with almost any version of GRBL firmware.)\n\n', '')
        select = 'G20' if p.units.upper() == 'IN' else 'G21'
        setup = '(SETUP: select the work coordinate system and set the tool tip reference)\n' \
                '(with length compensation canceled by G49, then press Cycle Start.)\n' \
                'M00'
        # the metadata of the base block (tool, feedrates, Z and ranges) follows the mode selection
        metadata = self.patch(gcode, '%s\nG90\nG17\nG94\n' % select, '').strip('\n')
        # G291 selects the ISO dialect in a block of its own; the switch keeps the active tool and the work offsets.
        # It does not turn the ISO T word of a tool call into the ISO M word of a machining center.
        return 'G291\n%s\nG17\nG90\nG94\nG40\nG49\nG80\nM05\n%s\n\n%s' % (select, metadata, setup)

    def finalize_gcode(self, gcode):
        # a plain program-manager main program, not the FANUC percent/O envelope. The ISO dialect manual ends every
        # G291 program with M30 and never switches back with G290 (Reset restores the machine-data default mode), so
        # the exporter's M02 becomes M30 and a missing program end is added.
        lines = gcode.rstrip('\n').splitlines()
        if lines and lines[-1].strip().upper() in ('M02', 'M2', 'M30'):
            lines.pop()
        return '\n'.join(lines + ['M30']) + '\n'
