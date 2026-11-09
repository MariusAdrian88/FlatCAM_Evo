# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

import re
from decimal import Decimal

from preprocessors.GRBL_11 import GRBL_11


class Heidenhain_ISO(GRBL_11):
    """HEIDENHAIN TNC 640 DIN/ISO milling/drilling profile.

    Baseline: TNC 640, NC software 34059x-01, DIN/ISO programming (not the latest firmware). This is not a FANUC
    ISO dialect. Restricted to 3-axis linear milling and drilling with a manual tool change. The datum, the active
    tool and its length, and the untilted working plane are operator setup at the control. The program envelope is a
    plain DIN/ISO main program with the fixed program name FLATCAM. TNC DIN/ISO main programs conventionally use the
    .I extension; FlatCAM does not add a file extension to the export name. A user preamble or postamble must be
    written in the TNC DIN/ISO dialect.
    """

    include_header = False
    # the finalizer adds M02 only when the user postamble has no program end of its own
    include_footer = False
    end_block = 'N99999999'
    block_number = re.compile(r'^N(\d+)\b', re.IGNORECASE)
    envelope = re.compile(r'^%FLATCAM\s+(G7[01])\s*\*$', re.IGNORECASE)

    @staticmethod
    def _comment(text):
        text = str(text).replace('\r', ' ').replace('\n', ' ').replace('*', ' ').strip()
        # the last character of a TNC comment block must not be a tilde
        return '; ' + text.rstrip('~').rstrip()

    @staticmethod
    def _unit_word(p):
        return 'G70' if str(p['units']).upper() == 'IN' else 'G71'

    @staticmethod
    def _feed(p, value):
        # G70 programs the feed in 1/10 inch/min; G71 programs it in mm/min
        return float(value) * (10.0 if str(p['units']).upper() == 'IN' else 1.0)

    @staticmethod
    def _seconds(value):
        text = str(value)
        # keep a tiny dwell time exact instead of rounding it to zero, without an exponent the control may not read
        return format(Decimal(text), 'f') if 'e' in text.lower() else text

    def start_code(self, p):
        units = ' ' + str(p['units']).lower()
        coords_xy = p['xy_toolchange']
        end_coords_xy = p['xy_end']
        gcode = '%%FLATCAM %s *\n' % self._unit_word(p)
        gcode += self._comment('FlatCAM HEIDENHAIN TNC 640 DIN/ISO program; restricted to 3-axis linear milling '
                               'and drilling.') + '\n'
        gcode += self._comment('The datum, the active tool length and the untilted working plane are set at the '
                               'control.') + '\n'

        xmin = '%.*f' % (p.coords_decimals, p['obj_options']['xmin'])
        xmax = '%.*f' % (p.coords_decimals, p['obj_options']['xmax'])
        ymin = '%.*f' % (p.coords_decimals, p['obj_options']['ymin'])
        ymax = '%.*f' % (p.coords_decimals, p['obj_options']['ymax'])

        if str(p['obj_options']['type']) == 'Geometry':
            gcode += self._comment('TOOL DIAMETER: ' + str(p['obj_options']['tool_dia']) + units) + '\n'
            gcode += self._comment('Feedrate_XY: ' + str(p['feedrate']) + units + '/min') + '\n'
            gcode += self._comment('Feedrate_Z: ' + str(p['z_feedrate']) + units + '/min') + '\n'
            gcode += self._comment('Feedrate rapids ' + str(p['feedrate_rapid']) + units + '/min') + '\n'
            gcode += self._comment('Z_Cut: ' + str(p['z_cut']) + units) + '\n'
            if p['multidepth'] is True:
                gcode += self._comment('DepthPerCut: ' + str(p['z_depthpercut']) + units) + '\n'
            gcode += self._comment('Z_Move: ' + str(p['z_move']) + units) + '\n'

        elif str(p['obj_options']['type']) == 'Excellon' and p['use_ui'] is True:
            for tool, val in p['tools'].items():
                gcode += self._comment('Tool: %s -> Dia: %s' % (str(tool), str(val['tooldia']))) + '\n'
            for tool, val in p['tools'].items():
                gcode += self._comment('Tool: %s -> Feedrate Z: %s' %
                                       (str(tool), str(val['data']['tools_drill_feedrate_z']))) + '\n'
            for tool, val in p['tools'].items():
                gcode += self._comment('Tool: %s -> Z_Cut: %s' % (str(tool), str(val['data']['tools_drill_cutz']))) + '\n'
            for tool, val in p['tools'].items():
                gcode += self._comment('Tool: %s -> Z_Move: %s' %
                                       (str(tool), str(val['data']['tools_drill_travelz']))) + '\n'

        if p['toolchange'] is True:
            gcode += self._comment('Z Toolchange: ' + str(p['z_toolchange']) + units) + '\n'
            if coords_xy is not None:
                gcode += self._comment('X,Y Toolchange: ' + '%.*f, %.*f' % (p.decimals, coords_xy[0],
                                                                             p.decimals, coords_xy[1]) + units) + '\n'
            else:
                gcode += self._comment('X,Y Toolchange: None' + units) + '\n'

        gcode += self._comment('Z Start: ' + str(p['startz']) + units) + '\n'
        gcode += self._comment('Z End: ' + str(p['z_end']) + units) + '\n'
        if end_coords_xy is not None:
            gcode += self._comment('X,Y End: ' + '%.*f, %.*f' % (p.decimals, end_coords_xy[0],
                                                                 p.decimals, end_coords_xy[1]) + units) + '\n'
        else:
            gcode += self._comment('X,Y End: None' + units) + '\n'

        if str(p['obj_options']['type']) in ('Excellon', 'Excellon Geometry'):
            gcode += self._comment('Preprocessor Excellon: ' + str(p['pp_excellon_name'])) + '\n'
        else:
            gcode += self._comment('Preprocessor Geometry: ' + str(p['pp_geometry_name'])) + '\n'

        gcode += self._comment('X range: ' + '{: >9s}'.format(xmin) + ' ... ' + '{: >9s}'.format(xmax) + ' ' +
                               units) + '\n'
        gcode += self._comment('Y range: ' + '{: >9s}'.format(ymin) + ' ... ' + '{: >9s}'.format(ymax) + ' ' +
                               units) + '\n'
        gcode += self._comment('Spindle Speed: %s RPM' % str(p['spindlespeed'])) + '\n'

        # G17 X/Y plane with Z as the tool axis, absolute dimensions, no radius compensation. The TNC G20 is a
        # working plane, not the inch unit word; G49/G80/G94 are not the FANUC meaning and are not written.
        gcode += 'G17\nG90\nG40\n'
        gcode += self._comment('SETUP: select the datum, the actual tool and its length, and the untilted working '
                               'plane; then press Cycle Start.') + '\n'
        gcode += 'M00'
        return gcode

    def toolchange_code(self, p):
        z_toolchange = p.z_toolchange
        toolchangexy = p.xy_toolchange
        tool_dia = '%.*f' % (p.decimals, p.toolC)
        units = ' ' + str(p['units']).lower()

        if p.startz is not None and p.is_first_tool:
            z_toolchange = p.startz

        gcode = 'M05\n'
        gcode += 'G00 Z' + self.coordinate_format % (p.coords_decimals, z_toolchange) + '\n'
        if toolchangexy is not None:
            gcode += 'G00 X' + self.coordinate_format % (p.coords_decimals, toolchangexy[0]) + \
                     ' Y' + self.coordinate_format % (p.coords_decimals, toolchangexy[1]) + '\n'
        gcode += self._comment('Change to FlatCAM tool %s, Dia = %s%s; update the actual tool in the control and '
                               'reestablish the tool tip reference.' % (str(p.tool), tool_dia, units)) + '\n'
        gcode += 'M00'
        return gcode

    def rapid_code(self, p):
        return 'G00 ' + self.position_code(p)

    def linear_code(self, p):
        return 'G01 ' + self.position_code(p) + ' F' + \
               str(self.feedrate_format % (p.fr_decimals, self._feed(p, p.feedrate)))

    def down_code(self, p):
        return 'G01 Z' + self.coord(p, p.z_cut) + ' F' + \
               str(self.feedrate_format % (p.fr_decimals, self._feed(p, p.z_feedrate)))

    def up_to_zero_code(self, p):
        return 'G01 Z0 F' + str(self.feedrate_format % (p.fr_decimals, self._feed(p, p.z_feedrate)))

    def feedrate_code(self, p):
        # the G01 moves carry their own F word; a coordinate-free feed block is not useful
        return ''

    def z_feedrate_code(self, p):
        return ''

    def spindle_code(self, p):
        sdir = {'CW': 'M03', 'CCW': 'M04'}[p.spindledir]
        if p.spindlespeed:
            # the TNC DIN/ISO tool call T<n> G17 S<rpm> sets the speed; the tool is the one named in the tool change
            return 'T%d G17 S%s\n%s' % (int(p.tool), str(p.spindlespeed), sdir)
        return sdir

    def dwell_code(self, p):
        # G04 F is seconds; it is not scaled by the unit of the program
        if p.dwelltime:
            return 'G04 F%s' % self._seconds(p.dwelltime)
        return ''

    def spindle_stop_code(self, p):
        return 'M05'

    @classmethod
    def _program_end(cls, line):
        text = cls.block_number.sub('', str(line).strip(), count=1).split(';', 1)[0]
        text = text.strip().rstrip('*').strip().upper()
        return text in ('M02', 'M2', 'M30')

    @classmethod
    def _has_program_end(cls, lines):
        for line in reversed(lines):
            text = cls.block_number.sub('', str(line).strip(), count=1).strip()
            if not text or text.startswith((';', '(')):
                continue
            return cls._program_end(text)
        return False

    def finalize_gcode(self, gcode):
        """
        The whole file after the header, the user snippets and the footer: number the DIN/ISO blocks and add the
        program envelope. The active unit word comes from the start block written into the saved job, not from the
        application settings at export time.
        """
        lines = [line.strip() for line in str(gcode).replace('\r\n', '\n').replace('\r', '\n').split('\n') if
                 line.strip()]
        start = None
        unit_word = None
        for line in lines:
            match = self.envelope.match(line)
            if match:
                start = line
                unit_word = match.group(1).upper()
                break
        if start is not None:
            lines.remove(start)

        if not self._has_program_end(lines):
            lines.append('M02')

        used = set()
        for line in lines:
            match = self.block_number.match(line)
            if match:
                used.add(int(match.group(1)))

        numbered = []
        next_number = 10
        for line in lines:
            if self.block_number.sub('', line, count=1).lstrip().startswith(';'):
                # a comment is a block of its own: no '*' or trailing '~' inside it
                line = line.replace('*', ' ').rstrip().rstrip('~').rstrip()
            if line.startswith('%'):
                numbered.append(line if line.endswith('*') else line + ' *')
                continue
            match = self.block_number.match(line)
            if match:
                numbered.append(line if line.endswith('*') else line + ' *')
                continue
            while next_number in used or next_number == 99999999:
                next_number += 10
            used.add(next_number)
            numbered.append('N%d %s%s' % (next_number, line, '' if line.endswith('*') else ' *'))
            next_number += 10

        if start is not None:
            numbered.insert(0, start)
            numbered.append('%s %%FLATCAM %s *' % (self.end_block, unit_word))
        return '\n'.join(numbered) + '\n'
