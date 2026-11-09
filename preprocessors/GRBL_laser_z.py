# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# https://github.com/MariusAdrian88/FlatCAM_Evo            #
# File Author: Matthieu Berthomé                           #
# Date: 5/26/2017                                          #
# Modified: Marius Stanciu                                 #
# Date: 01/19/2022                                         #
# MIT Licence                                              #
# ##########################################################

from appPreProcessor import PreProc

# This post processor is configured to output code that
# is compatible with almost any version of Grbl.


class GRBL_laser_z(PreProc):

    include_header = True
    coordinate_format = "%.*f"
    feedrate_format = '%.*f'

    def start_code(self, p):
        units = ' ' + str(p['units']).lower()
        gcode = '(This preprocessor is used with a motion controller loaded with GRBL firmware. )\n'
        gcode += '(It is for the case when it is used together with a LASER connected on the SPINDLE connector.)\n'
        gcode += '(On toolchange event the laser will move to a defined Z height to change the laser dot size.)\n\n'

        xmin = '%.*f' % (p.coords_decimals, p['obj_options']['xmin'])
        xmax = '%.*f' % (p.coords_decimals, p['obj_options']['xmax'])
        ymin = '%.*f' % (p.coords_decimals, p['obj_options']['ymin'])
        ymax = '%.*f' % (p.coords_decimals, p['obj_options']['ymax'])

        if p['use_ui'] is True and p['multigeo']:
            def tool_value(val, *keys):
                # the Milling keys, then the Drilling keys; '-' if the tool has none of them
                data = val.get('data') or {}
                for key in keys:
                    if key in data:
                        return data[key]
                return '-'

            gcode += '\n(TOOLS DIAMETER: )\n'
            for tool, val in p['tools'].items():
                gcode += '(Tool: %s -> ' % str(tool) + 'Dia: %s' % str(val.get("tooldia", '-')) + ')\n'
            gcode += '\n(FEEDRATE: )\n'
            for tool, val in p['tools'].items():
                gcode += '(Tool: %s -> ' % str(tool) + 'Feedrate: %s' % \
                         str(tool_value(val, "tools_mill_feedrate", "tools_drill_feedrate_z")) + ')\n'
            gcode += '\n(Z FOCUS: )\n'
            for tool, val in p['tools'].items():
                gcode += '(Tool: %s -> ' % str(tool) + 'Z: %s' % \
                         str(tool_value(val, "tools_mill_travelz", "tools_drill_travelz")) + ')\n'
            gcode += '\n(LASER POWER: )\n'
            for tool, val in p['tools'].items():
                gcode += '(Tool: %s -> ' % str(tool) + 'Power: %s' % \
                         str(tool_value(val, "tools_mill_spindlespeed", "tools_drill_spindlespeed")) + ')\n'
        else:
            gcode += '(Feedrate: ' + str(p['feedrate']) + units + '/min' + ')\n'
            gcode += '(Z Focus: ' + str(p['z_move']) + units + ')\n'
            gcode += '(Laser Power: %s)\n' % str(p['spindlespeed'])
            gcode += '(Laser Minimum Power: %s)\n' % str(p['laser_min_power'])

        gcode += '\n'
        gcode += '(X-Y End: ' + str(p['xy_end']) + units + ')\n'
        gcode += '(Z End: ' + str(p['z_end']) + units + ')\n'
        gcode += '(Steps per circle: ' + str(p['steps_per_circle']) + ')\n'

        if str(p['obj_options']['type']) == 'Excellon' or str(p['obj_options']['type']) == 'Excellon Geometry':
            gcode += '(Preprocessor Excellon: ' + str(p['pp_excellon_name']) + ')\n'
        else:
            gcode += '(Preprocessor Geometry: ' + str(p['pp_geometry_name']) + ')\n' + '\n'

        gcode += '(X range: ' + '{: >9s}'.format(xmin) + ' ... ' + '{: >9s}'.format(xmax) + ' ' + units + ')\n'
        gcode += '(Y range: ' + '{: >9s}'.format(ymin) + ' ... ' + '{: >9s}'.format(ymax) + ' ' + units + ')\n\n'

        gcode += 'G20\n' if p.units.upper() == 'IN' else 'G21\n'
        gcode += 'G90\n'
        gcode += 'G17\n'
        gcode += 'G94'

        return gcode

    def startz_code(self, p):
        gcode = 'G0 Z' + self.coordinate_format % (p.coords_decimals, p.z_move)
        return gcode

    def lift_code(self, p):
        if float(p.laser_min_power or 0) > 0.0:
            # the formatted text: laser OFF must always be like this else the plotting will not be done correctly
            return '%s S%s (laser OFF)\n' % (str(p.laser_on_code), str(p.laser_min_power))
        else:
            return 'M5'

    def down_code(self, p):
        if p.spindlespeed:
            return '%s S%s' % (str(p.laser_on_code), str(p.spindlespeed))
        else:
            return '%s S0' % str(p.laser_on_code)

    def toolchange_code(self, p):
        return 'G0 Z' + self.coordinate_format % (p.coords_decimals, p.z_move)

    def up_to_zero_code(self, p):
        return ''

    def rapid_code(self, p):
        return 'G0 ' + self.position_code(p)

    def linear_code(self, p):
        return 'G1 ' + self.position_code(p) + ' F' + str(self.feedrate_format % (p.fr_decimals, p.feedrate))

    def end_code(self, p):
        # End Z and the end position; the X-Y move is not made below the Travel Z
        gcode = self.end_moves_code(p, rapid='G0')
        return gcode

    def feedrate_code(self, p):
        return 'G1 F' + str(self.feedrate_format % (p.fr_decimals, p.feedrate))

    def z_feedrate_code(self, p):
        return 'G1 F' + str(self.feedrate_format % (p.fr_decimals, p.z_feedrate))

    def spindle_code(self, p):
        if p.spindlespeed:
            return '%s S%s' % (str(p.laser_on_code), str(p.spindlespeed))
        else:
            return '%s S0' % str(p.laser_on_code)

    def dwell_code(self, p):
        return ''

    def spindle_stop_code(self, p):
        return 'M5'
