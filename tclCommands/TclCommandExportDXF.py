from tclCommands.TclCommand import TclCommand

import collections
import os

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class TclCommandExportDXF(TclCommand):
    """
    Tcl shell command to export a Geometry Object as an DXF File.

    example:
        export_dxf path/my_geometry filename
    """

    # List of all command aliases, to be able to use old names for backward compatibility (add_poly, add_polygon)
    aliases = ['export_dxf', 'edxf']

    description = '%s %s' % ("--", "Export Gerber, Excellon, Geometry or CNCJob objects as a DXF File.")

    # Dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([
        ('name', str),
        ('filename', str)
    ])

    # Dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
    ])

    # array of mandatory options for current Tcl command: required = ['name','outname']
    required = ['name']

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Export Gerber, Excellon, Geometry or CNCJob objects as a DXF File.",
        'args': collections.OrderedDict([
            ('name', 'Name of the Gerber, Excellon, Geometry or CNCJob object to export.\n'
                     'A comma-separated list of names exports them all into one file.'),
            ('filename', 'Absolute path to file to export.\n'
                         'WARNING: no spaces are allowed. If unsure enclose the entire path with quotes.\n'
                         'The .dxf extension is added when missing.'),
        ]),
        'examples': ['export_dxf my_geo path/my_file.dxf', 'export_dxf my_geo D:/my_file.dxf', 'export_dxf geo1,gerber1 D:/all.dxf']
    }

    def execute(self, args, unnamed_args):
        """

        :param args:
        :param unnamed_args:
        :return:
        """
        if 'name' not in args:
            return "Failed. The Geometry object name to be exported was not provided."

        names = [n.strip() for n in str(args['name']).split(',') if n.strip()]
        if not names:
            return "Failed. The object name to be exported was not provided."
        source_obj_name = names if len(names) > 1 else names[0]

        if 'filename' not in args:
            filename = self._app_option("global_last_save_folder") + '/' + names[0]
        else:
            filename = args['filename']
        if not os.path.splitext(filename)[1]:
            filename += '.dxf'

        return self.app.f_handlers.export_dxf(obj_name=source_obj_name, filename=filename, local_use=None, use_thread=False)
