# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# MIT Licence                                              #
# ##########################################################

"""Calculator formulas. No Qt here."""

import math
import builtins


def _(message):
    translate_fcn = getattr(builtins, '_', None)
    return translate_fcn(message) if translate_fcn else message


MM_PER_INCH = 25.4
G_PER_OZ = 28.349523125
ML_PER_FLOZ = 29.5735295625     # US fluid ounce (liquid volume)

# Tinning recipes, quantities per 1 mL of solution
TIN_RECIPES = {
    'sol1': {'sncl2': 0.005, 'thiourea': 0.02, 'sulfamic': 0.03, 'water': 1.0, 'soap': 0.001, 'hypo': 0.015},
    'sol2': {'sncl2': 0.02, 'thiourea': 0.075, 'sulfamic': 0.09, 'water': 1.0, 'soap': 0.001, 'hypo': 0.015},
}


def v_shape_diameter(tip_diameter, tip_angle, cut_depth):
    if tip_angle < 0 or tip_angle >= 180:
        raise ValueError(_("Tip angle must be between 0 and 180 degrees."))
    return tip_diameter + 2 * abs(cut_depth) * math.tan(math.radians(tip_angle / 2.0))


def v_shape_depth(tip_diameter, tip_angle, tool_diameter):
    if tip_angle <= 0 or tip_angle >= 180:
        raise ValueError(_("Tip angle must be between 0 and 180 degrees."))
    if tip_diameter > tool_diameter:
        raise ValueError(_("Tool diameter (cut width) cannot be smaller than the tip diameter."))
    return -(tool_diameter - tip_diameter) / (2 * math.tan(math.radians(tip_angle / 2.0)))


def plating_current(area_cm2, sides, density):
    return area_cm2 * sides / 929.0304 * density


def plating_time(growth, density):
    return growth * 2.142857142857143 * (20 / density) if density else 0.0


def tinning_quantities(solution_type, volume):
    recipe = TIN_RECIPES.get(solution_type, TIN_RECIPES['sol2'])
    return {name: amount * volume for name, amount in recipe.items()}


def solve_feeds_speeds(target, diameter, flutes, rpm, chip_load, feed, surface, units):
    """Solve the chosen quantity in mm/mm per minute/m per minute or in/in per minute/ft per minute.

    Return the solved quantity and the related feed/surface speed. The unknown is ignored.
    """
    if units not in ('MM', 'IN'):
        raise ValueError(_("Unknown feed and speed units."))
    if target not in ('feed', 'chip', 'rpm_feed', 'surface', 'rpm_surface'):
        raise ValueError(_("Unknown feed and speed calculation."))

    values = {'diameter': diameter, 'flutes': flutes, 'rpm': rpm, 'chip_load': chip_load,
              'feed': feed, 'surface': surface}
    required = {
        'feed': ('diameter', 'flutes', 'rpm', 'chip_load'),
        'chip': ('diameter', 'flutes', 'rpm', 'feed'),
        'rpm_feed': ('diameter', 'flutes', 'chip_load', 'feed'),
        'surface': ('diameter', 'rpm'),
        'rpm_surface': ('diameter', 'surface'),
    }
    for name in required[target]:
        value = values[name]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(_("Required inputs must be finite numbers greater than zero."))
    if 'flutes' in required[target] and int(flutes) != flutes:
        raise ValueError(_("Flute count must be a whole number."))

    divisor = 1000.0 if units == 'MM' else 12.0
    result = {}
    if target == 'rpm_feed':
        rpm = feed / flutes / chip_load
        result['rpm'] = rpm
    elif target == 'rpm_surface':
        rpm = surface * divisor / math.pi / diameter
        result['rpm'] = rpm
    elif target == 'chip':
        result['chip_load'] = feed / rpm / flutes

    if target in ('feed', 'rpm_feed'):
        result['feed'] = rpm * flutes * chip_load
    result['surface'] = math.pi * diameter * rpm / divisor
    if any(not math.isfinite(value) or value <= 0 for value in result.values()):
        raise ValueError(_("Calculated result is outside the supported range."))
    return result
