# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Pure map of the clearing method ids of the NCC and Paint tools to their names and kernel keys."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MethodMap:
    """Method id -> (name, kernel key). The kernel key names the fill kernel, or the composite that tries several."""
    entries: dict

    def name(self, method_id):
        return self.entries[method_id][0]

    def kernel(self, method_id):
        return self.entries[method_id][1]

    def __contains__(self, method_id):
        return method_id in self.entries

    def __iter__(self):
        return iter(self.entries)


# the ids are the indexes of the method combo of each tool
NCC_METHODS = MethodMap({
    0: ('Standard', 'shrink'),
    1: ('Seed', 'seed'),
    2: ('Lines', 'lines'),
    3: ('Combo', 'combo'),
    4: ('Crosshatch', 'crosshatch'),
})

PAINT_METHODS = MethodMap({
    0: ('Standard', 'shrink'),
    1: ('Seed', 'seed'),
    2: ('Lines', 'lines'),
    3: ('Laser_lines', 'laser_lines'),
    4: ('Combo', 'combo'),
    5: ('Crosshatch', 'crosshatch'),
})
