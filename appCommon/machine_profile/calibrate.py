# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Machine Profiles: calibration math (no Qt).

The machine cuts the design square of side L as a parallelogram. With the X axis as the reference, the machine maps
a work point W to M * W, with M = [[sx, sy sin e], [0, sy cos e]]. The correction is C = M^-1: the program carries
C(D), so that the machine cuts D.
"""

import math
from datetime import date

SCALE_MIN, SCALE_MAX = 0.98, 1.02
SKEW_MAX_DEG = 2.0


class CalibrationError(ValueError):
    """The measurements cannot give a trustworthy correction."""


def _triangle_ok(p, q, r):
    return abs(p - q) < r < p + q


def solve_calibration(L, a, b, d1, d2, tol=0.05):
    """
    :param L: nominal side (mm)
    :param a: measured X side
    :param b: measured Y side
    :param d1: diagonal from the zero corner to the opposite corner
    :param d2: the other diagonal
    :param tol: largest accepted parallelogram residual (mm)
    :return: dict with matrix (C), machine_matrix (M), sx, sy, epsilon_deg, residual (mm), date (ISO)
    """
    if min(L, a, b, d1, d2) <= 0:
        raise CalibrationError("Every measured value must be greater than zero.")
    if not (_triangle_ok(a, b, d1) and _triangle_ok(a, b, d2)):
        raise CalibrationError("The measurements are impossible (triangle inequality fails for the sides and a "
                               "diagonal). Measure again.")
    ratio = (d1 * d1 - d2 * d2) / (4.0 * a * b)
    if abs(ratio) > 1.0:
        raise CalibrationError("The diagonals do not fit the sides. Measure again.")
    residual = abs(d1 * d1 + d2 * d2 - 2.0 * (a * a + b * b)) / (d1 + d2)
    if residual > tol:
        raise CalibrationError("The measurements do not form a parallelogram (residual %.4f mm, limit %.4f mm). "
                               "Please take new measurements." % (residual, tol))
    sx, sy = a / L, b / L
    e = math.asin(ratio)
    if abs(math.degrees(e)) > SKEW_MAX_DEG:
        raise CalibrationError("The skew of %.2f degrees is more than %.1f degrees; check the machine or the "
                               "measurements." % (math.degrees(e), SKEW_MAX_DEG))
    if not (SCALE_MIN <= sx <= SCALE_MAX and SCALE_MIN <= sy <= SCALE_MAX):
        raise CalibrationError("The scale (X %.4f, Y %.4f) is outside %.2f..%.2f; check the measurements." %
                               (sx, sy, SCALE_MIN, SCALE_MAX))

    m = [[sx, sy * math.sin(e)], [0.0, sy * math.cos(e)]]
    det = m[0][0] * m[1][1]
    c = [[m[1][1] / det, -m[0][1] / det], [0.0, m[0][0] / det]]
    return {'matrix': c, 'machine_matrix': m, 'sx': sx, 'sy': sy, 'epsilon_deg': math.degrees(e),
            'residual': residual, 'date': date.today().isoformat()}


def apply_matrix(C, x, y):
    return C[0][0] * x + C[0][1] * y, C[1][0] * x + C[1][1] * y


def pattern_square(L):
    """The corners of the calibration square, from the zero corner, counter-clockwise."""
    return [(0, 0), (L, 0), (L, L), (0, L)]


def pattern_gcode(L, z_cut, z_move, feed, decimals=4):
    """
    The G-code (mm, absolute) of the calibration square outline: it starts at the zero corner, cuts the 4 sides at
    `z_cut` with the feed and returns to the zero corner. The text is design G-code: the caller decides about the
    correction (the calibration cut itself is never corrected).
    """
    def num(value):
        return '%.*f' % (decimals, value)

    corners = pattern_square(L)
    lines = ['(Calibration square, side %s mm)' % num(L), 'G21', 'G90', 'G00 Z%s' % num(z_move),
             'G00 X%s Y%s' % (num(0), num(0)), 'G01 Z%s F%s' % (num(z_cut), num(feed))]
    for x, y in corners[1:] + corners[:1]:
        lines.append('G01 X%s Y%s F%s' % (num(x), num(y), num(feed)))
    lines += ['G00 Z%s' % num(z_move), 'M02']
    return '\n'.join(lines) + '\n'
