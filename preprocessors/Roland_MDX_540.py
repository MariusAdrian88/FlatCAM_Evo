# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from appPreProcessor import PreProc


# for Roland Preprocessors it is mandatory for the preprocessor name (python file and class name, both of them must be
# the same) to contain the following keyword, case-sensitive: 'Roland' without the quotes.
class Roland_MDX_540(PreProc):

    include_header = False
    coordinate_format = "%d"
    feedrate_format = '%.1f'
    feedrate_rapid_format = '%.1f'

    # RML-1 unit of the MDX-540 is 0.01mm on all axes (100 units per mm)
    units_per_mm = 100
    # valid feedrate for MDX-540 is between 0.1mm/sec and 125mm/sec (6mm/min to 7500mm/min)
    feed_min = 0.1
    feed_max = 125.0

    def to_rml(self, p, value):
        """
        A length of the job as integer RML-1 units (the job units are converted to mm first).
        """
        value = float(value)
        if p.units.upper() == 'IN':
            value *= 25.4
        return int(round(value * self.units_per_mm))

    def feed_code(self, p, feedrate):
        """
        The 'V' command: the feedrate of the job (in units per minute) as mm/sec, clamped to the valid range.
        """
        fr_mm_min = float(feedrate)
        if p.units.upper() == 'IN':
            fr_mm_min *= 25.4
        fr_sec = min(max(fr_mm_min / 60, self.feed_min), self.feed_max)
        return 'V' + str(self.feedrate_format % fr_sec) + ';'

    def start_code(self, p):
        gcode = ';;^IN;' + '\n'
        gcode += '^PA;'
        return gcode

    def startz_code(self, p):
        return ''

    def lift_code(self, p):
        gcode = self.feedrate_rapid_code(p) + '\n'
        gcode += self.position_code(p) + ',' + str(self.to_rml(p, p.z_move)) + ';'
        return gcode

    def down_code(self, p):
        # the plunge is made at the Z feedrate
        gcode = self.z_feedrate_code(p) + '\n'
        gcode += self.position_code(p) + ',' + str(self.to_rml(p, p.z_cut)) + ';'
        return gcode

    def toolchange_code(self, p):
        # RML-1 has no operator pause (no M0/M6 equivalent), so there is nothing to output
        return ''

    def up_to_zero_code(self, p):
        gcode = self.feedrate_code(p) + '\n'
        gcode += self.position_code(p) + ',' + str(self.to_rml(p, 0)) + ';'
        return gcode

    def position_code(self, p):
        # the bed offset and skew are applied in job units, then the result is converted to mm and RML-1 units
        x, y = self.bed_xy(p)
        return ('Z' + self.coordinate_format + ',' + self.coordinate_format) % (
            self.to_rml(p, x), self.to_rml(p, y))

    def rapid_code(self, p):
        gcode = self.feedrate_rapid_code(p) + '\n'
        gcode += self.position_code(p) + ',' + str(self.to_rml(p, p.z_move)) + ';'
        return gcode

    def linear_code(self, p):
        gcode = self.feedrate_code(p) + '\n'
        gcode += self.position_code(p) + ',' + str(self.to_rml(p, p.z_cut)) + ';'
        return gcode

    def end_code(self, p):
        # p.x, p.y is the end position passed by the generator
        gcode = self.feedrate_rapid_code(p) + '\n'
        gcode += self.position_code(p) + ',' + str(self.to_rml(p, p.z_end)) + ';'
        return gcode

    def feedrate_code(self, p):
        return self.feed_code(p, p.feedrate)

    def z_feedrate_code(self, p):
        return self.feed_code(p, p.z_feedrate)

    def feedrate_rapid_code(self, p):
        return self.feed_code(p, p.feedrate_rapid)

    def spindle_code(self, p):
        gcode = '!MC1;'
        # without a spindle speed the spindle is only switched on
        if not p.spindlespeed:
            return gcode

        speed = int(float(p.spindlespeed))
        if speed > 8388607:
            speed = 8388607
        if speed < 0:
            speed = 0

        gcode += '\n!RC%d;' % speed
        return gcode

    def dwell_code(self, p):
        return ''

    def spindle_stop_code(self, p):
        return '!MC0;'
