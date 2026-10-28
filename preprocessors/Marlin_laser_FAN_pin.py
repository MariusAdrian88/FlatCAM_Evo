# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# Website:      http://flatcam.org                         #
# File Author:  Marius Adrian Stanciu (c)                  #
# Date:         8-Feb-2020                                 #
# License:      MIT Licence                                #
# ##########################################################

from appPreProcessor import PreProc


def power_255(value):
    """M106 takes 0-255."""
    return max(0, min(255, int(round(float(value)))))


class Marlin_laser_FAN_pin(PreProc):

    include_header = True
    coordinate_format = "%.*f"
    feedrate_format = '%.*f'
    feedrate_rapid_format = feedrate_format

    def start_code(self, p):
        units = ' ' + str(p['units']).lower()
        end_coords_xy = p['xy_end']
        gcode = ';This preprocessor is used with a motion controller loaded with MARLIN firmware.\n'
        gcode += ';It is for the case when it is used together with a LASER connected on one of the FAN pins.\n'\
                 ';This preprocessor makes no moves on the Z axis it will only move horizontally.\n' \
                 ';It assumes a manually focused laser.\n' \
                 ';The laser is started and stopped with M106 command.\n\n'

        xmin = '%.*f' % (p.coords_decimals, p['obj_options']['xmin'])
        xmax = '%.*f' % (p.coords_decimals, p['obj_options']['xmax'])
        ymin = '%.*f' % (p.coords_decimals, p['obj_options']['ymin'])
        ymax = '%.*f' % (p.coords_decimals, p['obj_options']['ymax'])

        gcode += ';Feedrate: ' + str(p['feedrate']) + units + '/min' + '\n'
        gcode += ';Feedrate rapids: ' + str(p['feedrate_rapid']) + units + '/min' + '\n\n'

        gcode += ';Z Focus: ' + str(p['z_move']) + units + '\n'

        gcode += ';Steps per circle: ' + str(p['steps_per_circle']) + '\n'

        if str(p['obj_options']['type']) == 'Excellon' or str(p['obj_options']['type']) == 'Excellon Geometry':
            gcode += ';Preprocessor Excellon: ' + str(p['pp_excellon_name']) + '\n'
        else:
            gcode += ';Preprocessor Geometry: ' + str(p['pp_geometry_name']) + '\n'
        if end_coords_xy is not None:
            gcode += ';X,Y End: ' + "%.*f, %.*f" % (p.decimals, end_coords_xy[0],
                                                    p.decimals, end_coords_xy[1]) + units + '\n'
        else:
            gcode += ';X,Y End: ' + "None" + units + '\n'
        gcode += ';Steps per circle: ' + str(p['steps_per_circle']) + '\n'

        gcode += ';X range: ' + '{: >9s}'.format(xmin) + ' ... ' + '{: >9s}'.format(xmax) + ' ' + units + '\n'
        gcode += ';Y range: ' + '{: >9s}'.format(ymin) + ' ... ' + '{: >9s}'.format(ymax) + ' ' + units + '\n\n'

        gcode += ';Laser Power (Spindle Speed): %s\n' % str(p['spindlespeed'])
        gcode += ';Laser Minimum Power: %s\n\n' % str(p['laser_min_power'])

        gcode += 'G20\n' if p.units.upper() == 'IN' else 'G21\n'
        gcode += 'G90'

        return gcode

    def startz_code(self, p):
        return ''

    def lift_code(self, p):
        if float(p.laser_min_power) > 0.0:
            # the formatted text: laser OFF must always be like this else the plotting will not be done correctly
            return 'M106 S%d ;laser OFF\n' % power_255(p.laser_min_power)
        else:
            gcode = 'M400\n'
            gcode += 'M106 S0'
            return gcode

    def down_code(self, p):
        if p.spindlespeed:
            return 'M106 S%d' % power_255(p.spindlespeed)
        else:
            # a bare M106 runs the pin at full output (255)
            return 'M106 S0'

    def toolchange_code(self, p):
        return ''

    def up_to_zero_code(self, p):
        return ''

    def rapid_code(self, p):
        return 'G0 ' + self.position_code(p) + " " + self.feedrate_rapid_code(p)

    def linear_code(self, p):
        return 'G1 ' + self.position_code(p) + " " + self.inline_feedrate_code(p)

    def end_code(self, p):
        gcode = ''
        coords_xy = p['xy_end']
        if coords_xy:
            gcode = 'G0 X' + self.coord(p, coords_xy[0]) + ' Y' + self.coord(p, coords_xy[1]) + " " + \
                    self.feedrate_rapid_code(p) + "\n"

        return gcode

    def feedrate_code(self, p):
        return 'G1 F' + str(self.feedrate_format % (p.fr_decimals, p.feedrate))

    def z_feedrate_code(self, p):
        return 'G1 F' + str(self.feedrate_format % (p.fr_decimals, p.z_feedrate))

    def inline_feedrate_code(self, p):
        return 'F' + self.feedrate_format % (p.fr_decimals, p.feedrate)

    def feedrate_rapid_code(self, p):
        return 'F' + self.feedrate_rapid_format % (p.fr_decimals, p.feedrate_rapid)

    def spindle_code(self, p):
        if p.spindlespeed:
            return 'M106 S%d' % power_255(p.spindlespeed)
        else:
            # a bare M106 runs the pin at full output (255)
            return 'M106 S0'

    def dwell_code(self, p):
        return ''

    def spindle_stop_code(self, p):
        gcode = 'M400\n'
        gcode += 'M106 S0'
        return gcode
