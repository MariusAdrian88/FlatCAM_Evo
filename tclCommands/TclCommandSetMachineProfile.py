# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

import collections

from appCommon.machine_profile.profile import RESERVED_NAME, list_profiles
from appGUI.machine_profile_ui import apply_machine_profile_locks, profiles_folder
from tclCommands.TclCommand import TclCommand


class TclCommandSetMachineProfile(TclCommand):
    """
    Tcl shell command to set the active machine profile.

    example:
        set_machine_profile Router3018
    """

    aliases = ['set_machine_profile']

    description = '%s %s' % ("-- <name>", "Sets the active machine profile (None: no profile).")

    arg_names = collections.OrderedDict([
        ('name', str),
    ])
    option_types = collections.OrderedDict()
    required = ['name']

    help = {
        'main': "Sets the active machine profile. Use None for no profile.",
        'args': collections.OrderedDict([
            ('name', 'None or the name of an existing machine profile (see list_machine_profiles). Required.'),
        ]),
        'examples': ['set_machine_profile Router3018', 'set_machine_profile None']
    }

    def execute(self, args, unnamed_args):
        name = str(args['name'])
        if name != RESERVED_NAME and name not in [p.name for p in list_profiles(profiles_folder(self.app))]:
            self.raise_tcl_error('Machine profile not found: %s' % name)
        self.app.defaults['global_machine_profile'] = name
        self.app.options['global_machine_profile'] = name
        if self.app.headless is not True:       # a headless run has no preferences form and no plugin fields
            apply_machine_profile_locks(self.app)
