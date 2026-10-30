from tclCommands.TclCommand import *
from shapely import Point


class TclCommandAddSlot(TclCommandSignaled):
    """
    Tcl shell command to add a rectange to the given Geometry object.
    """

    # array of all command aliases, to be able use  old names for backward compatibility (add_poly, add_polygon)
    aliases = ['add_slot']

    description = '%s %s' % ("--", "Adds a slot in the given Excellon object, if it does not exist already.")

    # Dictionary of types from Tcl command, needs to be ordered.
    # For positional arguments
    arg_names = collections.OrderedDict([
        ('name', str)
    ])

    # Dictionary of types from Tcl command, needs to be ordered.
    # For options like -optionname value
    option_types = collections.OrderedDict([
        ('dia', float),
        ('startx', float),
        ('starty', float),
        ('stopx', float),
        ('stopy', float),
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = ['name', 'dia', 'startx', 'starty', 'stopx', 'stopy']

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Adds a slot hole in the given Excellon object.",
        'args': collections.OrderedDict([
            ('name', 'Name of the Excellon object in which to add the slot.'),
            ('dia', 'The tool diameter. Required.'),
            ('startx', "The X coordinate for the slot start point. Required."),
            ('starty', "The Y coordinate for the slot stop point. Required."),
            ('stopx', "The X coordinate for the slot start point. Required."),
            ('stopy', "The Y coordinate for the slot stop point. Required."),
        ]),
        'examples': ["add_slot excellon_name -dia 0.8 -startx 1 -starty 1 -stopx 1.3 -stopy 1"]
    }

    def execute(self, args, unnamed_args):
        """
        execute current TCL shell command

        :param args:            array of known named arguments and options
        :param unnamed_args:    array of other values which were passed into command
                                without -somename and  we do not have them in known arg_names
        :return:                None or exception
        """

        if unnamed_args:
            self.raise_tcl_error(
                "Too many arguments. Correct format: %s" %
                '["add_drill excellon_name -dia -x -y"]')

        name = args['name']
        try:
            obj = self.app.collection.get_by_name(str(name))
        except Exception:
            self.raise_tcl_error("Could not retrieve object: %s" % name)
        if obj is None:
            self.raise_tcl_error("Object not found: %s" % name)
        if obj.kind != 'excellon':
            self.raise_tcl_error('Expected Excellon, got %s %s.' % (name, type(obj)))

        for key in self.required:
            if key not in args or args[key] is None:
                self.raise_tcl_error('Missing required value: %s' % key)

        new_dia = args['dia']
        slot_start_x = args['startx']
        slot_start_y = args['starty']
        slot_stop_x = args['stopx']
        slot_stop_y = args['stopy']
        if new_dia <= 0 or not all(math.isfinite(v) for v in (
                new_dia, slot_start_x, slot_start_y, slot_stop_x, slot_stop_y)):
            self.raise_tcl_error('Diameter must be positive and coordinates must be finite.')
        if (slot_start_x, slot_start_y) == (slot_stop_x, slot_stop_y):
            self.raise_tcl_error('A slot requires distinct start and stop coordinates.')

        dia_tool_dict = {diam['tooldia']: tool for tool, diam in list(obj.tools.items())}

        new_data = {}
        kind = 'excellon'
        for option in self.app.options:
            if option.find(kind + "_") == 0:
                oname = option[len(kind) + 1:]
                new_data[oname] = self._app_option(option)

            if option.find('tools_drill_') == 0:
                new_data[option] = self._app_option(option)

        new_slot = (
            Point(slot_start_x, slot_start_y),
            Point(slot_stop_x, slot_stop_y)
        )

        if not dia_tool_dict:
            obj.add_tool(new_dia, slots=[new_slot], data=new_data)
        elif new_dia not in list(dia_tool_dict.keys()):
            obj.add_tool(new_dia, slots=[new_slot], data=new_data)

            # sort the updated tools dict
            new_tools = {}
            tools_sorted = sorted(obj.tools.items(), key=lambda x: x[1]['tooldia'])
            for idx, tool in enumerate(tools_sorted, start=1):
                new_tools[idx] = tool[1]
            obj.tools = new_tools
        elif new_dia in list(dia_tool_dict.keys()):
            if new_slot not in obj.tools[dia_tool_dict[new_dia]]['slots']:
                obj.tools[dia_tool_dict[new_dia]]['slots'].append(new_slot)
                if 'slots_to' in obj.tools[dia_tool_dict[new_dia]]:
                    obj.tools[dia_tool_dict[new_dia]]['slots_to'].append(0)
            else:
                return TclCommandFailure('Slot with the given coordinates is already in the object.')

        return obj.create_geometry()
