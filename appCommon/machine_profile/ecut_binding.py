# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/25/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The binding of the E-Cut axis fields of a machine profile to the E-Cut axis configuration.

Pure: it takes plain values (axis name -> {field: number}), no E-Cut import. The E-Cut side converts its own axis
configuration to and from these values.
"""

from typing import Dict

from appCommon.machine_profile.profile import AXIS_NAMES, MachineProfile

ECUT_FIELDS = ('axis_velocity_max', 'axis_accel_max', 'soft_limit_min', 'soft_limit_max')


def ecut_axis_updates(profile: MachineProfile, current: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """The values that the profile sets, per axis of `current`: {axis: {field: new value}}.

    A field that the profile leaves unset, and an axis that `current` does not have, are not in the result.
    """
    updates = {}
    for axis in AXIS_NAMES:
        if axis not in current:
            continue
        changes = {name: getattr(profile, name)[axis] for name in ECUT_FIELDS
                   if getattr(profile, name) is not None and axis in getattr(profile, name)}
        if changes:
            updates[axis] = changes
    return updates
