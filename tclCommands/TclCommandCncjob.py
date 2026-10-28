from tclCommands.TclCommand import TclCommandSignaled

import collections
from copy import deepcopy

from camlib import deepcopy_keep_geometry


class TclCommandCncjob(TclCommandSignaled):
    """
    Tcl shell command to Generates a CNC Job from a Geometry Object.

    example:
        set_sys units MM
        new
        open_gerber tests/gerber_files/simple1.gbr -outname margin
        isolate margin -dia 3
        cncjob margin_iso
    """

    # array of all command aliases, to be able use  old names for backward compatibility (add_poly, add_polygon)
    aliases = ['cncjob']
    # Bare feature options use defaults; startz/spindlespeed also support an explicit None sentinel.
    valueless_options = frozenset({'dpp', 'toolchangez', 'dwelltime', 'extracut_length',
                                   'startz', 'spindlespeed'})

    description = '%s %s' % ("--", "Generates a CNC Job object from a Geometry Object.")

    # dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([
        ('name', str)
    ])

    # dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
        ('dia', float),
        ('z_cut', float),
        ('z_move', float),
        ('feedrate', float),
        ('feedrate_z', float),
        ('feedrate_rapid', float),
        ('extracut_length', float),
        ('dpp', float),
        ('toolchangez', float),
        ('toolchangexy', str),
        ('startz', float),
        ('endz', float),
        ('endxy', str),
        ('spindlespeed', int),
        ('dwelltime', float),
        ('las_power', float),
        ('las_min_pwr', float),
        ('pp', str),
        ('muted', str),
        ('outname', str),
        ('offset_type', str),
        ('offset_value', float),
        ('job_type', str),
        ('polish_margin', float),
        ('polish_overlap', float),
        ('polish_method', str),
        ('optimization', str),
        ('search_time', int),
        ('area_exclusion', str),
        ('tools', str),
        ('mill_dia', float),
        ('milling_type', str),
        ('overlap', float),
        ('connect', str)
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = []

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Generates a CNC Job object from a Geometry Object.",
        'args': collections.OrderedDict([
            ('name', 'Name of the source object.'),
            ('dia', 'Tool diameter. For a multi-tool Geometry it is ignored (with a warning): every tool keeps its '
                    'own diameter. For a single-tool Geometry it replaces the diameter.'),
            ('z_cut', 'Z-axis cutting position.'),
            ('z_move', 'Z-axis moving position.'),
            ('feedrate', 'Moving speed on X-Y plane when cutting.'),
            ('feedrate_z', 'Moving speed on Z plane when cutting.'),
            ('feedrate_rapid', 'Rapid moving at speed when cutting.'),
            ('extracut_length', 'Extra cut length past the first point of the path. Enables extra cutting; '
                               'bare -extracut_length uses 0.0, not the configured extra length.'),
            ('dpp', 'Positive depth per pass. Enables multidepth cutting; bare -dpp uses application '
                    'tools_mill_depthperpass.'),
            ('toolchangez', 'Z distance for toolchange (example: 30.0). Enables toolchange; '
                            'bare -toolchangez uses application tools_mill_toolchangez.'),
            ('toolchangexy', 'The X,Y coordinates at Toolchange event in format (x, y) (example: (30.0, 15.2) or '
                             'without parenthesis like: 0.3,1.0). WARNING: no spaces allowed in the value.'),
            ('startz', 'Height before the first move. Bare -startz preserves None; '
                       'omitting the option uses application tools_mill_startz.'),
            ('endz', 'Height where the last move will park.'),
            ('endxy', 'The X,Y coordinates at job end in format (x, y) (example: (2.0, 1.2) or without parenthesis'
                      'like: 0.3,1.0). WARNING: no spaces allowed in the value.'),
            ('spindlespeed', 'Spindle speed in rpm (example: 4000). Bare or omitted -spindlespeed uses None '
                            'for nonlaser preprocessors. Laser preprocessors use -las_power; without -las_power, '
                            '-spindlespeed is the laser power (bare: 0.0). Without both, the laser power of the '
                            'object (tools_mill_spindlespeed) or of each tool is used.'),
            ('dwelltime', 'Spindle startup pause time. Enables dwell; bare -dwelltime uses object '
                          'tools_mill_dwelltime with application fallback. If the option is omitted, '
                          'application tools_mill_dwell and tools_mill_dwelltime are used.'),
            ('outname', 'Name of the resulting Geometry object.'),
            ('las_power', 'Used with "laser" preprocessors. Set the laser power when cutting'),
            ('las_min_pwr', 'Used with "laser" preprocessors. Set the laser power when not cutting, travelling'),
            ('pp', 'Name of the Geometry preprocessor. No quotes, case sensitive'),
            ('muted', 'It will not put errors in the Shell. Can be True (1) or False (0)'),
            ('offset_type', 'Tool offset: 0 or path, 1 or in, 2 or out, 3 or custom. A multi-tool Geometry keeps '
                            'its own offset type of each tool unless this is given.'),
            ('offset_value', 'Offset value for the custom offset type. Without -offset_type it selects custom (3).'),
            ('job_type', 'Job type: 0 or roughing, 1 or finishing, 2 or isolation, 3 or polishing. Polishing '
                         'paints the whole bounding box of the object (see -polish_*).'),
            ('polish_margin', 'Polishing: margin added around the bounding box.'),
            ('polish_overlap', 'Polishing: overlap in percent (0 - 99).'),
            ('polish_method', 'Polishing: 0 or standard, 1 or seed, 2 or lines.'),
            ('optimization', 'Path optimization: M (OR-Tools metaheuristic), B (OR-Tools basic), '
                             'T (travelling salesman), R (RTree), N (none). Without OR-Tools, R is used.'),
            ('search_time', 'OR-Tools search time in seconds (integer, 1 or more).'),
            ('area_exclusion', 'Use the exclusion areas of the project (1/0). 0 ignores them; the default uses '
                               'them when there are any.'),
            ('tools', 'Excellon only: comma separated tool numbers to mill (default all).'),
            ('mill_dia', 'Excellon only: diameter of the milling tool.'),
            ('milling_type', 'Excellon only: drills, slots or both.'),
            ('overlap', 'Excellon only: overlap in percent for clearing the holes.'),
            ('connect', 'Excellon only: connect the clearing paths (1/0).')
        ]),
        'examples': ['cncjob exc_name -mill_dia 0.8 -milling_type drills -overlap 10 -connect 1 -z_cut -1.8',
                     'cncjob geo_name -offset_type out -job_type polishing -polish_margin 1 -optimization T',
                     'cncjob geo_name -dia 0.5 -z_cut -1.8 -dpp 0.6 -z_move 2 -feedrate 120 -pp default',
                     'cncjob geo_name -dpp -toolchangez -dwelltime -extracut_length -startz -spindlespeed']
    }

    def execute(self, args, unnamed_args):
        """
        execute current TCL shell command

        :param args: array of known named arguments and options
        :param unnamed_args: array of other values which were passed into command
            without -somename and  we do not have them in known arg_names
        :return: None or exception
        """

        name = ''

        if 'muted' in args:
            try:
                par = args['muted'].capitalize()
            except AttributeError:
                par = args['muted']
            muted = self.parse_bool(par)
        else:
            muted = False

        try:
            name = args['name']
        except KeyError:
            if muted is False:
                self.raise_tcl_error("Object name is missing")
                return "fail"
            else:
                return "fail"

        if 'outname' not in args:
            args['outname'] = str(name) + "_cnc"

        obj = self.app.collection.get_by_name(str(name), isCaseSensitive=False)

        if obj is None:
            if muted is False:
                self.raise_tcl_error("Object not found: %s" % str(name))
                return "fail"
            else:
                return "fail"

        if obj.kind not in ('geometry', 'excellon'):
            if muted is False:
                self.raise_tcl_error('Expected Geometry or Excellon object, got %s %s.' % (str(name), type(obj)))
                return "fail"
            else:
                return 'fail'

        # the options that the user gave: the others keep the settings of the tools
        given = set(args)
        try:
            extra = self._parse_milling_options(args)
        except ValueError as err:
            if muted is False:
                self.raise_tcl_error(str(err))
            return "fail"

        args["dia"] = args.get('dia', self._app_option('tools_mill_tooldia'))

        args["z_cut"] = args["z_cut"] if "z_cut" in args else self._app_option("tools_mill_cutz")
        args["z_move"] = args["z_move"] if "z_move" in args else \
            self._app_option("tools_mill_travelz")

        args["pp"] = args["pp"] if "pp" in args and isinstance(args["pp"], str) else \
            self._app_option("tools_mill_ppname_g")

        args["feedrate"] = args["feedrate"] if "feedrate" in args else \
            self._app_option("tools_mill_feedrate")

        if 'laser' in args["pp"] and "feedrate_z" not in args:
            args["feedrate_z"] = args["feedrate"]
        else:
            args["feedrate_z"] = args["feedrate_z"] if "feedrate_z" in args else \
                self._app_option("tools_mill_feedrate_z")
        args["feedrate_rapid"] = args["feedrate_rapid"] if "feedrate_rapid" in args else \
            self._app_option("tools_mill_feedrate_rapid")

        if "extracut_length" in args:
            args["extracut"] = True
            if args["extracut_length"] is None:
                args["extracut_length"] = 0.0
            else:
                args["extracut_length"] = float(args["extracut_length"])
        else:
            args["extracut"] = self._app_option("tools_mill_extracut")
            args["extracut_length"] = self._app_option("tools_mill_extracut_length")

        if "dpp" in args:
            args["multidepth"] = True
            if args["dpp"] is None:
                args["dpp"] = self._app_option("tools_mill_depthperpass")
            else:
                args["dpp"] = float(args["dpp"])
        else:
            args["multidepth"] = self._app_option("tools_mill_multidepth")
            args["dpp"] = self._app_option("tools_mill_depthperpass")

        args["startz"] = args["startz"] if "startz" in args else \
            self._app_option("tools_mill_startz")
        args["endz"] = args["endz"] if "endz" in args else self._app_option("tools_mill_endz")

        if "endxy" in args and args["endxy"]:
            args["endxy"] = args["endxy"]
        else:
            if self._app_option("tools_mill_endxy"):
                args["endxy"] = str(self._app_option("tools_mill_endxy"))
            else:
                args["endxy"] = '0, 0'
        if len(self.parse_coords(args["endxy"])) != 2:
            self.raise_tcl_error("The entered value for 'endxy' needs to have the format x,y or "
                                 "in format (x, y) - no spaces allowed. But always two comma separated values.")

        # Spindle speed
        if 'laser' not in args["pp"]:
            args["spindlespeed"] = args.get('spindlespeed')
        elif "las_power" in args or "spindlespeed" in args:
            # the laser power; without -las_power use -spindlespeed, as the scripts of older versions do
            args["spindlespeed"] = args["las_power"] if "las_power" in args else (args.get('spindlespeed') or 0.0)
        else:
            # neither option: the laser power of the object, as in the Milling plugin and as each tool of a
            # multi-geometry object keeps its own
            args["spindlespeed"] = obj.obj_options.get('tools_mill_spindlespeed') or 0.0

        # Laser minimum power
        args["las_min_pwr"] = args["las_min_pwr"] if "las_min_pwr" in args else 0.0

        if 'dwelltime' in args:
            args["dwell"] = True
            if args['dwelltime'] is None:
                args["dwelltime"] = float(obj.obj_options.get(
                    'tools_mill_dwelltime', self._app_option('tools_mill_dwelltime')))
            else:
                args["dwelltime"] = float(args['dwelltime'])
        else:
            args["dwell"] = self._app_option("tools_mill_dwell")
            args["dwelltime"] = self._app_option("tools_mill_dwelltime")

        if "toolchangez" in args:
            args["toolchange"] = True
            if args["toolchangez"] is not None:
                args["toolchangez"] = args["toolchangez"]
            else:
                args["toolchangez"] = self._app_option("tools_mill_toolchangez")
        else:
            args["toolchange"] = self._app_option("tools_mill_toolchange")
            args["toolchangez"] = self._app_option("tools_mill_toolchangez")

        if "toolchangexy" in args and args["toolchangexy"]:
            args["toolchangexy"] = args["toolchangexy"]
        else:
            if self._app_option("tools_mill_toolchangexy"):
                args["toolchangexy"] = str(self._app_option("tools_mill_toolchangexy"))
            else:
                args["toolchangexy"] = '0, 0'
        if len(self.parse_coords(args["toolchangexy"])) != 2:
            self.raise_tcl_error("The entered value for 'toolchangexy' needs to have the format x,y or "
                                 "in format (x, y) - no spaces allowed. But always two comma separated values.")
            return "fail"

        args.pop('name', None)
        args.pop('muted', None)

        for arg in args:
            if arg == "toolchange_xy" or arg == "spindlespeed" or arg == "startz":
                continue
            else:
                if args[arg] is None:
                    self.app.log.error("None parameters: %s is None" % arg)
                    if muted is False:
                        self.raise_tcl_error('One of the command parameters that have to be not None, is None.\n'
                                             'The parameter that is None is in the default values found in the list \n'
                                             'generated by the TclCommand "list_sys geom". or in the arguments.')
                        return "fail"
                    else:
                        return "fail"

        if obj.kind == 'excellon':
            return self._excellon_job(obj, args, given, extra)

        for key in [k for k in extra if k == 'tools' or k.startswith('tools_mill_milling_')]:
            extra.pop(key)   # Excellon only

        if not obj.multigeo and not extra:
            return self.check_result(obj.generatecncjob(use_thread=False, plot=False, **args))

        if not obj.multigeo:
            # The options of the Milling plugin (offset, polishing, optimization...) are made by the plugin: the
            # single-geometry object goes in as a one-tool multi-geometry object.
            geo_obj = self._single_as_multigeo(obj, args, given)
            local_tools_dict = geo_obj.tools
            for tool in local_tools_dict.values():
                tool['data']['tools_mill_spindlespeed'] = args["spindlespeed"]
                tool['data']['tools_mill_min_power'] = args['las_min_pwr']
                tool['data']['tools_mill_tooldia'] = args["dia"]
        else:
            geo_obj = obj
            local_tools_dict = deepcopy_keep_geometry(obj.tools)
            single_tool = len(local_tools_dict) == 1
            if 'dia' in given and not single_tool:
                self.app.inform.emit('[WARNING_NOTCL] cncjob: -dia is ignored for an object with several tools, '
                                     'every tool keeps its own diameter.')
            for tool in local_tools_dict.values():
                if 'data' not in tool:
                    continue
                self._apply_to_tool(tool['data'], args, given)
                if 'dia' in given and single_tool:
                    tool['data']['tools_mill_tooldia'] = args["dia"]
        for tool in local_tools_dict.values():
            tool['data'].update(extra)

        return self._run_milling_handler(obj, geo_obj, local_tools_dict, args, extra)

    # ---------------------------------------------------------------------------------------------------------------
    _OFFSET_TYPES = {'path': 0, 'in': 1, 'out': 2, 'custom': 3}
    _JOB_TYPES = {'roughing': 0, 'finishing': 1, 'isolation': 2, 'polishing': 3}
    _POLISH_METHODS = {'standard': 0, 'seed': 1, 'lines': 2}

    def _parse_milling_options(self, args):
        """Remove the milling options from args, return them as tools_mill_* keys. Raises ValueError."""
        def choice(name, names, count):
            value = str(args.pop(name)).strip().lower()
            if value in names:
                return names[value]
            if value.isdigit() and int(value) < count:
                return int(value)
            raise ValueError("The value of -%s needs to be one of: %s." % (
                name, ', '.join(list(names) + [str(i) for i in range(count)])))

        extra = {}
        if 'offset_type' in args:
            extra['tools_mill_offset_type'] = choice('offset_type', self._OFFSET_TYPES, 4)
        if 'offset_value' in args:
            extra['tools_mill_offset_value'] = float(args.pop('offset_value'))
            extra.setdefault('tools_mill_offset_type', 3)
        if 'job_type' in args:
            extra['tools_mill_job_type'] = choice('job_type', self._JOB_TYPES, 4)
        if 'polish_margin' in args:
            extra['tools_mill_polish_margin'] = float(args.pop('polish_margin'))
        if 'polish_overlap' in args:
            overlap = float(args.pop('polish_overlap'))
            if not 0 <= overlap < 100:
                raise ValueError("The value of -polish_overlap needs to be in the range 0 - 99.")
            extra['tools_mill_polish_overlap'] = overlap
        if 'polish_method' in args:
            extra['tools_mill_polish_method'] = choice('polish_method', self._POLISH_METHODS, 3)
        if 'optimization' in args:
            value = str(args.pop('optimization')).strip().upper()
            if value not in ('M', 'B', 'T', 'R', 'N'):
                raise ValueError("The value of -optimization needs to be one of: M, B, T, R, N.")
            extra['tools_mill_optimization_type'] = value
        if 'search_time' in args:
            search_time = args.pop('search_time')
            if search_time is None or int(search_time) < 1:
                raise ValueError("The value of -search_time needs to be an integer of 1 or more seconds.")
            extra['tools_mill_search_time'] = int(search_time)
        if 'area_exclusion' in args:
            extra['tools_mill_area_exclusion'] = self.parse_bool(args.pop('area_exclusion'))
        # the Excellon options
        if 'tools' in args:
            extra['tools'] = [int(t) for t in str(args.pop('tools')).replace(';', ',').split(',') if t.strip()]
        if 'mill_dia' in args:
            extra['tools_mill_milling_dia'] = float(args.pop('mill_dia'))
        if 'milling_type' in args:
            value = str(args.pop('milling_type')).strip().lower()
            if value not in ('drills', 'slots', 'both'):
                raise ValueError("The value of -milling_type needs to be one of: drills, slots, both.")
            extra['tools_mill_milling_type'] = value
        if 'overlap' in args:
            extra['tools_mill_milling_overlap'] = float(args.pop('overlap'))
        if 'connect' in args:
            extra['tools_mill_milling_connect'] = self.parse_bool(args.pop('connect'))
        return extra

    @staticmethod
    def _mill_data(args, given):
        """The tools_mill_* settings of the arguments in `given`."""
        data = {}
        for option, key in (('z_cut', 'tools_mill_cutz'), ('z_move', 'tools_mill_travelz'),
                            ('feedrate', 'tools_mill_feedrate'), ('feedrate_z', 'tools_mill_feedrate_z'),
                            ('feedrate_rapid', 'tools_mill_feedrate_rapid'), ('startz', 'tools_mill_startz'),
                            ('endz', 'tools_mill_endz'), ('endxy', 'tools_mill_endxy'),
                            ('toolchangexy', 'tools_mill_toolchangexy'), ('pp', 'tools_mill_ppname_g'),
                            ('las_min_pwr', 'tools_mill_min_power')):
            if option in given:
                data[key] = args[option]
        if 'extracut_length' in given:
            data['tools_mill_extracut'] = args["extracut"]
            data['tools_mill_extracut_length'] = args["extracut_length"]
        if 'dpp' in given:
            data['tools_mill_multidepth'] = args["multidepth"]
            data['tools_mill_depthperpass'] = args["dpp"]
        if 'toolchangez' in given:
            data['tools_mill_toolchange'] = args["toolchange"]
            data['tools_mill_toolchangez'] = args["toolchangez"]
        if 'dwelltime' in given:
            data['tools_mill_dwell'] = args["dwell"]
            data['tools_mill_dwelltime'] = args["dwelltime"]
        return data

    def _apply_to_tool(self, data, args, given):
        """Write the arguments that the user gave into the data of one tool; the rest of the tool is kept."""
        data.setdefault('seg_x', self._app_option('geometry_seg_x'))
        data.setdefault('seg_y', self._app_option('geometry_seg_y'))
        new = self._mill_data(args, given)
        data.update(new)
        pp = str(data.get('tools_mill_ppname_g', args["pp"]))
        laser = 'laser' in pp
        if laser and 'feedrate' in given and 'feedrate_z' not in given:
            data['tools_mill_feedrate_z'] = args["feedrate"]
        if 'las_power' in given or 'spindlespeed' in given:
            if laser:
                data['tools_mill_spindlespeed'] = args["las_power"] if "las_power" in given else \
                    (args.get('spindlespeed') or 0.0)
            else:
                data['tools_mill_spindlespeed'] = args.get('spindlespeed')

    def _single_as_multigeo(self, obj, args, given):
        from appPlugins.ToolMilling.Milling import HybridGeoExc
        geo = HybridGeoExc(app=self.app)
        geo.obj_options.update(deepcopy(dict(obj.obj_options)))
        geo.solid_geometry = obj.solid_geometry
        data = {key: deepcopy(val) for key, val in obj.obj_options.items() if key.startswith('tools_mill_')}
        data['seg_x'] = self._app_option('geometry_seg_x')
        data['seg_y'] = self._app_option('geometry_seg_y')
        data.update(self._mill_data(args, set(args)))
        data['tools_mill_toolchange'] = args["toolchange"]
        data['tools_mill_dwell'] = args["dwell"]
        data['tools_mill_dwelltime'] = args["dwelltime"]
        data['tools_mill_multidepth'] = args["multidepth"]
        data['tools_mill_depthperpass'] = args["dpp"]
        data['tools_mill_extracut'] = args["extracut"]
        data['tools_mill_extracut_length'] = args["extracut_length"]
        data['tools_mill_toolchangez'] = args["toolchangez"]
        geo.tools = {1: {'tooldia': args["dia"], 'data': data,
                         'solid_geometry': deepcopy_keep_geometry(obj.solid_geometry)}}
        return geo

    def _run_milling_handler(self, obj, geo_obj, tools_dict, args, extra, disable_offset=False):
        milling = self.app.milling_tool
        milling.target_obj = obj
        # the 'area_exclusion' option is in the data of the tools: each tool uses the exclusion areas, or not
        return self.check_result(milling.generate_cnc_job_handler(
            geo_obj=geo_obj, outname=args['outname'], tools_dict=tools_dict, tools_in_use=[],
            toolchange=args["toolchange"], use_thread=False, plot=False, disable_offset=disable_offset,
            from_tcl=True))

    def _excellon_job(self, obj, args, given, extra):
        """Mill the holes of an Excellon like the Milling plugin does (clearing each hole with the milling tool)."""
        milling = self.app.milling_tool
        milling.target_obj = obj
        wanted = extra.pop('tools', None)
        tools = {}
        for uid, source in obj.tools.items():
            if wanted is not None and int(uid) not in wanted:
                continue
            tool = deepcopy(source)
            data = tool.setdefault('data', {})
            milling._complete_milling_tool_data({uid: tool}, tool_pp_used=False, warn=False)
            self._apply_to_tool(data, args, given)
            data.update(extra)
            tools[uid] = tool
        if not tools:
            self.raise_tcl_error("No tool of %s has a geometry that can be milled." % obj.obj_options.get('name'))
            return "fail"
        return self.check_result(milling.paint_excellon_cncjpb(
            tools_dict=tools, outname=args['outname'], from_tcl=True, plot=False, use_thread=False,
            toolchange=args["toolchange"]))
