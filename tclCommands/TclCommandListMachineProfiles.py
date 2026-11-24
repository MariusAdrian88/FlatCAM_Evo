# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

import collections

from appCommon.machine_profile.profile import RESERVED_NAME, list_profiles
from appGUI.machine_profile_ui import profiles_folder
from tclCommands.TclCommand import TclCommand


class TclCommandListMachineProfiles(TclCommand):
    """
    Tcl shell command to list the machine profiles.

    example:
        list_machine_profiles
    """

    aliases = ['list_machine_profiles']

    description = '%s %s' % ("--", "Lists the machine profiles: None first, the active one is marked with *.")

    arg_names = collections.OrderedDict()
    option_types = collections.OrderedDict()
    required = []

    help = {
        'main': "Returns the names of the machine profiles, one per line. \"None\" (no profile) is first and the "
                "active profile is marked with *.",
        'args': collections.OrderedDict(),
        'examples': ['list_machine_profiles']
    }

    def execute(self, args, unnamed_args):
        active = self.app.options.get('global_machine_profile') or RESERVED_NAME
        names = [RESERVED_NAME] + [profile.name for profile in list_profiles(profiles_folder(self.app))]
        return '\n'.join(('* ' if name == active else '') + name for name in names)
