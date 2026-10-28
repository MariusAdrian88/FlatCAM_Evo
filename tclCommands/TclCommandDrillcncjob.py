from tclCommands.TclCommand import TclCommandSignaled

import collections
import math
from types import SimpleNamespace

from shapely.geometry import box

from appPlugins.ToolDrilling import DrillingGen

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class TclCommandDrillcncjob(TclCommandSignaled):
    """
    Tcl shell command to Generates a Drill CNC Job from a Excellon Object.
    """

    # array of all command aliases, to be able use  old names for backward compatibility (add_poly, add_polygon)
    aliases = ['drillcncjob']
    # These options explicitly handle None by selecting object/application defaults.
    valueless_options = frozenset({'dpp', 'toolchangez', 'dwelltime', 'drillz', 'startz', 'endz'})

    description = '%s %s' % ("--", "Generates a Drill CNC Job object from a Excellon Object.")

    # dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([
        ('name', str)
    ])

    # dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
        ('drilled_dias', str),
        ('objects', str),
        ('area', str),
        ('drillz', float),
        ('dpp', float),
        ('travelz', float),
        ('feedrate_z', float),
        ('feedrate_rapid', float),
        ('spindlespeed', int),
        ('toolchangez', float),
        ('toolchangexy', str),
        ('startz', float),
        ('endz', float),
        ('endxy', str),
        ('dwelltime', float),
        ('las_power', float),
        ('las_min_pwr', float),
        ('min_power', float),
        ('laser_on', str),
        ('toolchange', str),
        ('order', str),
        ('f_plunge', str),
        ('f_retract', str),
        ('spindledir', str),
        ('offset', float),
        ('pdepth', float),
        ('pplate', float),
        ('pfeed', float),
        ('dwell', str),
        ('dwell_bottom', str),
        ('peck', str),
        ('peck_depth', float),
        ('peck_retract', float),
        ('spot', str),
        ('spot_dia', float),
        ('spot_cutz', float),
        ('spot_feedrate', float),
        ('job_offset', str),
        ('mirror', str),
        ('mirror_point', str),
        ('array', str),
        ('array_pitch', str),
        ('drill_slots', str),
        ('overlap', float),
        ('last_drill', str),
        ('pp', str),
        ('opt_type', str),
        ('diatol', float),
        ('muted', str),
        ('outname', str)
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = ['name']

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Generates a Drill CNC Job from a Excellon Object.",
        'args': collections.OrderedDict([
            ('name', 'Name of the source object.'),
            ('drilled_dias',
             'Comma separated tool diameters of the drills to be drilled (example: 0.6,1.0 or 3.125). '
             'WARNING: No space allowed. Can also take the value "all" which will drill the holes for all tools.'),
            ('objects', 'Comma separated names of more Excellon objects that are drilled in the same job '
                        '(example: board2,board3; no spaces). Their tools are merged with the tools of the object by '
                        'diameter: the data of a tool come from the object, or from the extra object when the '
                        'object has no tool of that diameter. The objects are not changed.'),
            ('area', 'Drill only the holes inside this rectangle x0,y0,x1,y1 (example: 0,0,50,30; no spaces).'),
            ('drillz', 'Drill depth into material (example: -2.0) for all the tools. Bare or omitted -drillz uses '
                       'the Cut Z of each tool of the object (tools_drill_cutz).'),
            ('dpp', 'Depth per drilling pass (example: 0.7). Enables multidepth; bare -dpp uses the absolute '
                    'object tools_drill_depthperpass with application fallback.'),
            ('travelz', 'Travel distance above material (example: 2.0).'),
            ('feedrate_z', 'Drilling feed rate. It is the speed on the Z axis.'),
            ('feedrate_rapid', 'Rapid drilling feed rate.'),
            ('spindlespeed', 'Speed of the spindle in rpm (example: 4000). Laser preprocessors use -las_power; '
                             'without -las_power, -spindlespeed is the laser power, default 0.0.'),
            ('toolchangez', 'Z distance for toolchange (example: 30.0). Enables toolchange; '
                            'bare -toolchangez uses object tools_drill_toolchangez.'),
            ('toolchangexy', 'The X,Y coordinates at Toolchange event in format (x, y) (example: (30.0, 15.2) or '
                             'without parenthesis like: 0.3,1.0). WARNING: no spaces allowed in the value.'),
            ('startz', 'Z coordinate at job start. Bare or omitted -startz uses application tools_drill_startz '
                       'if truthy, otherwise there is no start Z move (as in the Drilling plugin): the first '
                       'toolchange is made at the toolchange Z and the first travel at the travel Z.'),
            ('endz', 'Z coordinate at job end. Bare or omitted -endz uses application tools_drill_endz.'),
            ('endxy', 'The X,Y coordinates at job end in format (x, y) (example: (2.0, 1.2) or without parenthesis'
                      'like: 0.3,1.0). WARNING: no spaces allowed in the value.'),
            ('dwelltime', 'Spindle startup pause time. Enables dwell; bare -dwelltime uses application '
                          'tools_drill_dwelltime. If the option is omitted, application tools_drill_dwell '
                          'and tools_drill_dwelltime are used.'),
            ('las_power', 'Used with "laser" preprocessors. Set the laser power when cutting'),
            ('las_min_pwr', 'Used with "laser" preprocessors. Set the laser power when not cutting, travelling'),
            ('min_power', 'Same as -las_min_pwr: the laser power when not cutting (Min Power of the Drilling plugin).'),
            ('laser_on', 'Used with "laser" preprocessors: the code that turns the laser on (example: M3 or M4).'),
            ('toolchange', 'Toolchange on (1) or off (0). Overrides the application option; -toolchangez and '
                           '-toolchangexy turn it on when this option is omitted.'),
            ('order', 'Order of the tools: fwd (small to big diameter, the default), rev (big to small) or no '
                      '(as they are in the object).'),
            ('f_plunge', 'Fast plunge (1 or 0): after the toolchange the tool goes down to the travel Z '
                         'with a rapid move. The default is the value of each tool.'),
            ('f_retract', 'Fast retract (1 or 0): the tool goes up to the travel Z in one move; otherwise it goes up to '
                          'Z0 first. The default is the value of each tool.'),
            ('spindledir', 'Spindle direction: CW or CCW. The default is the value of each tool.'),
            ('offset', 'Offset added to the depth of the drill (example: 0.1). The default is the value of each tool.'),
            ('pdepth', 'Probe depth, used by the preprocessors with probing (example: -0.02). The default is the '
                       'value of each tool.'),
            ('pplate', 'Thickness of the touch plate that the probe touches on top of the material, used by the '
                       'preprocessors with probing (example: 1.5). The default is the value of each tool.'),
            ('pfeed', 'Probe feed rate, used by the preprocessors with probing. The default is the value of each '
                      'tool.'),
            ('dwell', 'Spindle startup pause on (1) or off (0). The default is the value of each tool.'),
            ('dwell_bottom', 'Dwell at the bottom of each hole (1 or 0), for the Dwelltime. The default is the value '
                             'of each tool.'),
            ('peck', 'Peck drilling on (1) or off (0): each hole is drilled in steps of -peck_depth; after each step '
                     'the tool goes up to the travel Z, then fast down to -peck_retract above the last depth. It '
                     'replaces the multidepth. Ignored by the laser preprocessors. The default is the value of each '
                     'tool.'),
            ('peck_depth', 'Depth of one peck step (example: 0.5). The default is the value of each tool.'),
            ('peck_retract', 'Clearance above the last depth to which the tool goes down fast before it feeds on '
                             '(example: 0.2). The default is the value of each tool.'),
            ('spot', 'Spot drilling pass on (1) or off (0): the job starts with one extra tool, with its own tool '
                     'number (the next free one), that drills all the holes of the drilled tools with the spot drill. '
                     'It needs the toolchange: without it the pass is left out with a warning. Ignored by the laser '
                     'preprocessors. The default is the application option.'),
            ('spot_dia', 'Diameter of the spot drill (example: 0.5). The default is the application option.'),
            ('spot_cutz', 'Cut Z of the spot holes (example: -0.3). The default is the application option.'),
            ('spot_feedrate', 'Feedrate Z of the spot holes. The default is the application option.'),
            ('job_offset', 'Move all the drill points of the job by dx,dy (example: 10,-5; no spaces). Done after the '
                           'mirror and before the array. The Excellon object is not changed. The default is the '
                           'application option.'),
            ('mirror', 'Mirror all the drill points of the job: no, x (the Y values flip), y (the X values flip) or '
                       'xy. The default is the application option.'),
            ('mirror_point', 'The point x,y the mirror axes pass through (example: 0,0). Without it, the center of '
                             'the bounding box of the drilled holes.'),
            ('array', 'Repeat the drill points as an array: columns,rows (example: 3,2). 1,1 is no array. Needs '
                      '-array_pitch.'),
            ('array_pitch', 'The distance px,py between the copies of the array (example: 10,20).'),
            ('drill_slots', 'Drill the slots as holes (1) or leave them out (0). The default is the value of each '
                            'tool.'),
            ('overlap', 'Overlap in percent of the holes that drill a slot (0 ... 100). The default is the value of '
                        'each tool.'),
            ('last_drill', 'Drill a hole at the end of the slot (1 or 0). The default is the value of each tool.'),
            ('pp', 'This is the Excellon preprocessor name: case_sensitive, no_quotes'),
            ('opt_type', 'Name of move optimization type: R for the RTree walk (the default is the application option '
                         'Excellon optimization), B for Basic OR-Tools, M for Metaheuristic OR-Tools, T for '
                         'Travelling Salesman Algorithm, No for the order of the holes. B and M work only with '
                         'OR-Tools installed.'),
            ('diatol', 'Tolerance. Percentange (0.0 ... 100.0) within which dias in drilled_dias will be judged to be '
                       'the same as the ones in the tools from the Excellon object. E.g: if in drill_dias we have a '
                       'diameter with value 1.0, in the Excellon we have a tool with dia = 1.05 and we set a tolerance '
                       'diatol = 5.0 then the drills with the dia = (0.95 ... 1.05) '
                       'in Excellon will be processed. Float number.'),
            ('muted', 'It will not put errors in the Shell or status bar. Can be True (1) or False (0).'),
            ('outname', 'Name of the resulting Geometry object.')
        ]),
        'examples': ['drillcncjob test.TXT -drillz -1.5 -travelz 14 -feedrate_z 222 -feedrate_rapid 456 '
                     '-spindlespeed 777 -toolchangez 33 -endz 22 -pp Marlin\n'
                     'Usage of -feedrate_rapid matter only when the preprocessor is using it, like -Marlin-.\n',
                     'drillcncjob test.DRL -drillz -1.7 -dpp 0.5 -travelz 2 -feedrate_z 800 -endxy 3,3\n',
                     'drillcncjob test.DRL -drilled_dias "all" -drillz -1.7 -dpp 0.5 -travelz 2 -feedrate_z 800 '
                     '-endxy 3,3',
                     'drillcncjob test.DRL -dpp -toolchangez -dwelltime -drillz -startz -endz',
                     'drillcncjob test.DRL -toolchange 1 -order rev -f_plunge 1 -f_retract 0 -spindledir CCW '
                     '-dwell 1 -dwelltime 1.5 -offset 0.1 -opt_type No\n'
                     'Without -drillz, -travelz, -feedrate_z, -spindlespeed ... each tool uses its own values.\n',
                     'drillcncjob test.DRL -drillz -3 -peck 1 -peck_depth 1 -peck_retract 0.3 -dwell_bottom 1 '
                     '-dwelltime 0.5\n',
                     'drillcncjob test.DRL -toolchange 1 -spot 1 -spot_dia 0.5 -spot_cutz -0.3 -spot_feedrate 100\n',
                     'drillcncjob test.DRL -mirror y -job_offset 10,0 -array 2,2 -array_pitch 50,40\n',
                     'drillcncjob test.DRL -objects test2.DRL,test3.DRL -area 0,0,50,30\n',
                     'drillcncjob test.DRL -drill_slots 1-overlap 20 -last_drill 1 -pdepth -0.02 -pfeed 75'
                     ]
    }

    def execute(self, args, unnamed_args):
        """
        execute current TCL shell command

        :param args: array of known named arguments and options
        :param unnamed_args: array of other values which were passed into command
            without -somename and  we do not have them in known arg_names
        :return: None or exception
        """

        name = args['name']

        obj = self.app.collection.get_by_name(name)

        if 'outname' not in args:
            args['outname'] = name + "_cnc"

        if 'muted' in args:
            try:
                par = args['muted'].capitalize()
            except AttributeError:
                par = args['muted']
            muted = self.parse_bool(par)
        else:
            muted = False

        if obj is None:
            if muted is False:
                self.raise_tcl_error("Object not found: %s" % name)
            self.app.log.error("Object not found: %s" % name)
            return "fail"

        if obj.kind != 'excellon':
            if muted is False:
                self.raise_tcl_error('Expected ExcellonObject, got %s %s.' % (name, type(obj)))
            self.app.log.error('Expected ExcellonObject, got %s %s.' % (name, type(obj)))
            return "fail"

        def fail(message):
            if muted is False:
                self.raise_tcl_error(message)
            self.app.log.error(message)
            return "fail"

        # more Excellon objects in the job (-objects): their tools are merged by diameter with those of the object
        src_tools = obj.tools
        extra_objs = []
        for extra_name in [n.strip() for n in str(args.get('objects') or '').split(',') if n.strip()]:
            extra = self.app.collection.get_by_name(extra_name)
            if extra is None or extra.kind != 'excellon':
                return fail("-objects: %s is not an Excellon object of the project." % extra_name)
            if extra is not obj and extra not in extra_objs:
                extra_objs.append(extra)
        if extra_objs:
            src_tools, conflicts = DrillingGen.merge_object_tools(obj.tools, [extra.tools for extra in extra_objs])
            for dia, ids, keys in conflicts:
                self.app.inform.emit('[WARNING_NOTCL] %s' % (
                    _("Tools {ids} have the same diameter ({dia}) but different parameters ({keys}).")
                    .format(ids=', '.join(str(i) for i in ids), dia=dia, keys=', '.join(keys))))
        job_objs = [obj] + extra_objs
        xmin = min(o.obj_options['xmin'] for o in job_objs)
        ymin = min(o.obj_options['ymin'] for o in job_objs)
        xmax = max(o.obj_options['xmax'] for o in job_objs)
        ymax = max(o.obj_options['ymax'] for o in job_objs)

        # only the holes inside this rectangle (-area x0,y0,x1,y1) are drilled
        holes_area = None
        if args.get('area') is not None:
            try:
                x0, y0, x1, y1 = [float(v) for v in str(args['area']).replace('(', '').replace(')', '').split(',')]
            except ValueError:
                return fail("The -area option has to be four numbers: x0,y0,x1,y1.")
            holes_area = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

        def flag(key):
            """The value of a boolean option; None if the option is not given."""
            if key not in args:
                return None
            try:
                return self.parse_bool(args[key])
            except ValueError as e:
                self.raise_tcl_error("Bad value of -%s: %s" % (key, str(e)))
                raise

        def job_init(cnc_job_obj, app_obj):
            # tools = args["tools"] if "tools" in args else 'all'
            use_tools = []

            # drilled tools diameters
            try:
                if 'drilled_dias' in args and args['drilled_dias'] != 'all':
                    diameters = [x.strip() for x in args['drilled_dias'].split(",") if x != '']
                    matched_diameters = set()

                    req_tools = set()
                    for tool in src_tools:
                        obj_dia_form = float('%.*f' % (obj.decimals, float(src_tools[tool]["tooldia"])))
                        for req_dia in diameters:
                            req_dia_form = float('%.*f' % (obj.decimals, float(req_dia)))

                            if 'diatol' in args:
                                tolerance = args['diatol'] / 100

                                tolerance = 0.0 if tolerance < 0.0 else tolerance
                                tolerance = 1.0 if tolerance > 1.0 else tolerance
                                if math.isclose(obj_dia_form, req_dia_form, rel_tol=tolerance):
                                    req_tools.add(str(tool))
                                    matched_diameters.add(req_dia)
                                    use_tools.append(tool)
                            else:
                                if obj_dia_form == req_dia_form:
                                    req_tools.add(str(tool))
                                    matched_diameters.add(req_dia)
                                    use_tools.append(tool)

                    if not req_tools:
                        self.raise_tcl_error(
                            f"No tools selected. drilled_dias was: {args['drilled_dias']}"\
                            f" diameters are: {diameters}"
                        )

                    if len(matched_diameters) != len(set(diameters)):
                        if muted is False:
                            self.raise_tcl_error("One or more tool diameters of the drills to be drilled passed to the "
                                                 "TclCommand are not actual tool diameters in the Excellon object.")
                        else:
                            app_obj.log.error("One or more tool diameters of the drills to be drilled passed to the "\
                                              "TclCommand are not actual tool diameters in the Excellon object.")
                            return "fail"

                    # make a string of diameters separated by comma; this is what tcl_gcode_from_excellon_by_tool() is
                    # expecting as tools parameter
                    tools = ','.join(req_tools)

                    # no longer needed
                    # del args['drilled_dias']
                    args.pop('drilled_dias', None)
                    args.pop('diatol', None)

                    # Split and put back. We are passing the whole dictionary later.
                    # args['milled_dias'] = [x.strip() for x in args['tools'].split(",")]
                else:
                    tools = 'all'
            except Exception as e:
                tools = 'all'

                if muted is False:
                    self.raise_tcl_error("Bad tools: %s" % str(e))

                self.app.log.error("Bad tools: %s" % str(e))
                return "fail"

            use_tools = list(dict.fromkeys(use_tools))
            # populate the information's list for used tools
            if tools == 'all':
                sort = []
                for k, v in list(src_tools.items()):
                    sort.append((k, v.get('tooldia')))
                sorted_tools = sorted(sort, key=lambda t1: t1[1])
                use_tools = [i[0] for i in sorted_tools]
            if not use_tools:
                return 'fail'

            drillz = args["drillz"] if "drillz" in args and args["drillz"] is not None else \
                obj.obj_options["tools_drill_cutz"]

            toolchange = self._app_option("tools_drill_toolchange")
            if "toolchangez" in args:
                toolchange = True
                if args["toolchangez"] is not None:
                    toolchangez = args["toolchangez"]
                else:
                    toolchangez = obj.obj_options["tools_drill_toolchangez"]
            else:
                toolchangez = float(self._app_option("tools_drill_toolchangez"))

            if "toolchangexy" in args and args["toolchangexy"]:
                toolchange = True
                xy_toolchange = args["toolchangexy"]
            else:
                if self._app_option("tools_drill_toolchangexy"):
                    xy_toolchange = str(self._app_option("tools_drill_toolchangexy"))
                else:
                    xy_toolchange = '0, 0'
            if len(self.parse_coords(xy_toolchange)) != 2:
                self.raise_tcl_error("The entered value for 'toolchangexy' needs to have the format x,y or "
                                     "in format (x, y) - no spaces allowed. But always two comma separated values.")
                self.app.log.error("The entered value for 'toolchangexy' needs to have the format x,y or "
                                   "in format (x, y) - no spaces allowed. But always two comma separated values.")
                return "fail"

            if flag("toolchange") is not None:
                toolchange = flag("toolchange")

            endz = args["endz"] if "endz" in args and args["endz"] is not None else \
                self._app_option("tools_drill_endz")

            if "endxy" in args and args["endxy"]:
                xy_end = args["endxy"]
            else:
                if self._app_option("tools_drill_endxy"):
                    xy_end = str(self._app_option("tools_drill_endxy"))
                else:
                    xy_end = '0, 0'

            if len(self.parse_coords(xy_end)) != 2:
                self.raise_tcl_error("The entered value for 'xy_end' needs to have the format x,y or "
                                     "in format (x, y) - no spaces allowed. But always two comma separated values.")
                self.app.log.error("The entered value for 'xy_end' needs to have the format x,y or "
                                   "in format (x, y) - no spaces allowed. But always two comma separated values.")
                return "fail"

            # Path optimization
            opt_type = args["opt_type"] if "opt_type" in args and args["opt_type"] else \
                self._app_option("excellon_optimization_type", 'R')

            order = str(args.get("order") or 'fwd').lower()
            if order not in ('no', 'fwd', 'rev'):
                self.raise_tcl_error("The -order option has to be no, fwd or rev, not: %s" % args["order"])
                return "fail"

            spindledir = None
            if args.get("spindledir"):
                spindledir = str(args["spindledir"]).upper()
                if spindledir not in ('CW', 'CCW'):
                    self.raise_tcl_error("The -spindledir option has to be CW or CCW, not: %s" % args["spindledir"])
                    return "fail"

            # ##########################################################################################
            # ################# Set parameters #########################################################
            # ##########################################################################################
            cnc_job_obj.obj_options['type'] = 'Excellon'
            cnc_job_obj.multigeo = True
            cnc_job_obj.multitool = True
            cnc_job_obj.used_tools = use_tools

            # preprocessor
            pp_excellon_name = args["pp"] if "pp" in args and args["pp"] else self._app_option("tools_drill_ppname_e")
            cnc_job_obj.pp_excellon_name = pp_excellon_name
            cnc_job_obj.obj_options['ppname_e'] = pp_excellon_name

            # The job-wide values: the arguments of the command. A tool that is drilled uses these; without an argument
            # it uses its own value (the tool data of the object). The attributes of the job set below are what is
            # used when the tool has no such value.
            overrides = {}

            # multidepth
            cnc_job_obj.multidepth = self._app_option("tools_drill_multidepth")
            cnc_job_obj.z_depthpercut = abs(float(obj.obj_options.get(
                'tools_drill_depthperpass', self._app_option('tools_drill_depthperpass'))))
            if 'dpp' in args:
                cnc_job_obj.multidepth = True
                overrides['tools_drill_multidepth'] = True
                if args['dpp'] is not None:
                    cnc_job_obj.z_depthpercut = abs(float(args['dpp']))
                    overrides['tools_drill_depthperpass'] = cnc_job_obj.z_depthpercut
            # travel Z
            cnc_job_obj.z_move = self._app_option("tools_drill_travelz")
            if args.get("travelz") is not None:
                cnc_job_obj.z_move = overrides['tools_drill_travelz'] = float(args["travelz"])
            # Feedrate
            cnc_job_obj.feedrate = cnc_job_obj.z_feedrate = self._app_option("tools_drill_feedrate_z")
            if args.get("feedrate_z"):
                cnc_job_obj.feedrate = cnc_job_obj.z_feedrate = float(args["feedrate_z"])
                overrides['tools_drill_feedrate_z'] = cnc_job_obj.z_feedrate
            cnc_job_obj.feedrate_rapid = self._app_option("tools_drill_feedrate_rapid")
            if args.get("feedrate_rapid"):
                cnc_job_obj.feedrate_rapid = overrides['tools_drill_feedrate_rapid'] = float(args["feedrate_rapid"])

            # SpindleSpped / Laser Power
            cnc_job_obj.spindlespeed = self._app_option("tools_drill_spindlespeed")
            if 'laser' not in pp_excellon_name:
                if args.get("spindlespeed") is not None:
                    cnc_job_obj.spindlespeed = overrides['tools_drill_spindlespeed'] = float(args["spindlespeed"])
            else:
                # the laser power; without -las_power use -spindlespeed, as the scripts of older versions do
                cnc_job_obj.spindlespeed = overrides['tools_drill_spindlespeed'] = \
                    float(args["las_power"]) if "las_power" in args else float(args.get("spindlespeed", 0.0))

            # laser minimum power and the code that turns the laser on
            cnc_job_obj.laser_min_power = self._app_option("tools_drill_min_power", 0.0)
            for key in ('min_power', 'las_min_pwr'):
                if args.get(key) is not None:
                    cnc_job_obj.laser_min_power = overrides['tools_drill_min_power'] = float(args[key])
                    break
            if args.get("laser_on"):
                cnc_job_obj.laser_on_code = overrides['tools_drill_laser_on'] = str(args["laser_on"])

            # spindle direction
            cnc_job_obj.spindledir = self._app_option("tools_drill_spindledir")
            if spindledir:
                cnc_job_obj.spindledir = overrides['tools_drill_spindledir'] = spindledir
            # fast plunge and fast retract
            cnc_job_obj.f_plunge = self._app_option("tools_drill_f_plunge")
            cnc_job_obj.f_retract = self._app_option("tools_drill_f_retract")
            for key in ('f_plunge', 'f_retract'):
                if flag(key) is not None:
                    setattr(cnc_job_obj, key, flag(key))
                    overrides['tools_drill_' + key] = flag(key)
            # dwell and dwelltime
            cnc_job_obj.dwell = self._app_option("tools_drill_dwell")
            cnc_job_obj.dwelltime = float(self._app_option("tools_drill_dwelltime"))
            if 'dwelltime' in args:
                cnc_job_obj.dwell = True
                overrides['tools_drill_dwell'] = True
                if args['dwelltime'] is not None:
                    cnc_job_obj.dwelltime = overrides['tools_drill_dwelltime'] = float(args['dwelltime'])
            if flag('dwell') is not None:
                cnc_job_obj.dwell = overrides['tools_drill_dwell'] = flag('dwell')
            # peck drilling and the dwell at the bottom of the holes
            cnc_job_obj.peck = self._app_option("tools_drill_peck", False)
            cnc_job_obj.peck_depth = self._app_option("tools_drill_peck_depth", 0.0)
            cnc_job_obj.peck_retract = self._app_option("tools_drill_peck_retract", 0.0)
            cnc_job_obj.dwell_bottom = self._app_option("tools_drill_dwell_bottom", False)
            for key in ('peck', 'dwell_bottom'):
                if flag(key) is not None:
                    setattr(cnc_job_obj, key, flag(key))
                    overrides['tools_drill_' + key] = flag(key)
            for key in ('peck_depth', 'peck_retract'):
                if args.get(key) is not None:
                    setattr(cnc_job_obj, key, float(args[key]))
                    overrides['tools_drill_' + key] = float(args[key])
            # the spot drilling pass (job options)
            if flag('spot') is not None:
                overrides['tools_drill_spot'] = flag('spot')
            for key, option in (('spot_dia', 'tools_drill_spot_dia'), ('spot_cutz', 'tools_drill_spot_cutz'),
                                ('spot_feedrate', 'tools_drill_spot_feedrate_z')):
                if args.get(key) is not None:
                    overrides[option] = float(args[key])
            # the job XY transform of the drill points: mirror -> offset -> array (job options)
            for key, option in (('job_offset', 'tools_drill_job_offset_xy'), ('mirror', 'tools_drill_mirror'),
                                ('mirror_point', 'tools_drill_mirror_point'), ('array_pitch', 'tools_drill_array_pitch')):
                if args.get(key) is not None:
                    overrides[option] = str(args[key])
            if args.get("array") is not None:
                try:
                    columns, rows = [int(float(v)) for v in str(args["array"]).replace('(', '').replace(')', '')
                                     .split(',')]
                except ValueError:
                    self.raise_tcl_error("The -array option has to be two whole numbers: columns,rows.")
                    return "fail"
                overrides['tools_drill_array_columns'] = columns
                overrides['tools_drill_array_rows'] = rows
            try:
                DrillingGen.job_transform_params({
                    key: overrides[key] if key in overrides else self._app_option(key)
                    for key in ('tools_drill_job_offset_xy', 'tools_drill_mirror', 'tools_drill_mirror_point',
                                'tools_drill_array_columns', 'tools_drill_array_rows', 'tools_drill_array_pitch')})
            except ValueError as e:
                self.raise_tcl_error(str(e))
                return "fail"
            # offset of the depth and the probe
            if args.get("offset") is not None:
                overrides['tools_drill_offset'] = float(args["offset"])
            cnc_job_obj.z_p_depth = self._app_option("tools_drill_z_p_depth")
            cnc_job_obj.probe_plate = self._app_option("tools_drill_probe_plate", 0.0)
            cnc_job_obj.feedrate_probe = self._app_option("tools_drill_feedrate_probe")
            if args.get("pdepth") is not None:
                cnc_job_obj.z_p_depth = overrides['tools_drill_z_p_depth'] = float(args["pdepth"])
            if args.get("pplate") is not None:
                cnc_job_obj.probe_plate = overrides['tools_drill_probe_plate'] = float(args["pplate"])
            if args.get("pfeed") is not None:
                cnc_job_obj.feedrate_probe = overrides['tools_drill_feedrate_probe'] = float(args["pfeed"])
            # the slots drilled as holes
            if flag('drill_slots') is not None:
                overrides['tools_drill_drill_slots'] = flag('drill_slots')
            if args.get("overlap") is not None:
                overrides['tools_drill_drill_overlap'] = float(args["overlap"])
            if flag('last_drill') is not None:
                overrides['tools_drill_last_drill'] = flag('last_drill')
            # the Cut Z: the job-wide value, else of each tool
            if args.get("drillz") is not None:
                overrides['tools_drill_cutz'] = float(args["drillz"])

            cnc_job_obj.coords_decimals = int(self._app_option("cncjob_coords_decimals"))
            cnc_job_obj.fr_decimals = int(self._app_option("cncjob_fr_decimals"))

            cnc_job_obj.obj_options['xmin'] = xmin
            cnc_job_obj.obj_options['ymin'] = ymin
            cnc_job_obj.obj_options['xmax'] = xmax
            cnc_job_obj.obj_options['ymax'] = ymax

            # Cut Z
            cnc_job_obj.z_cut = float(drillz)
            # toolchange
            cnc_job_obj.toolchange = toolchange
            # toolchange X-Y location
            cnc_job_obj.xy_toolchange = xy_toolchange
            # toolchange Z location
            cnc_job_obj.z_toolchange = float(toolchangez)
            # start Z
            if "startz" in args and args["startz"] is not None:
                cnc_job_obj.startz = float(args["startz"])
            else:
                if self._app_option("tools_drill_startz"):
                    cnc_job_obj.startz = self._app_option("tools_drill_startz")
                else:
                    # no start Z move, as in the Drilling plugin: the preprocessors make the first toolchange at the
                    # Start Z, so the Travel Z of the Preferences put it (and, without toolchange, the first travel)
                    # at that height instead of the toolchange Z (the travel Z) of the job
                    cnc_job_obj.startz = None
            # end Z
            cnc_job_obj.z_end = float(endz)
            # end X-Y location
            cnc_job_obj.xy_end = self.parse_coords(xy_end)
            # Excellon optimization
            cnc_job_obj.excellon_optimization_type = opt_type

            ret_val = cnc_job_obj.tcl_gcode_from_excellon_by_tool(
                obj if not extra_objs else SimpleNamespace(tools=src_tools), tools, order=order, is_first=True,
                overrides=overrides, holes_area=holes_area)
            if ret_val == 'fail':
                return 'fail'

            cnc_job_obj.source_file = ret_val[1] + ret_val[0]
            cnc_job_obj.gc_start = ret_val[1]

            total_gcode_parsed = []
            if cnc_job_obj.toolchange is True:
                if tools == "all":
                    processed_tools = list(cnc_job_obj.tools.keys())
                else:
                    processed_tools = list(use_tools)
                if cnc_job_obj.spot_tool_id is not None and cnc_job_obj.spot_tool_id not in processed_tools:
                    # the spot drilling tool is not in the Excellon object
                    processed_tools.insert(0, cnc_job_obj.spot_tool_id)

                # from Excellon attribute self.tools
                for t_item in processed_tools:
                    cnc_job_obj.tools[t_item]['data']['tools_drill_offset'] = \
                        float(cnc_job_obj.tools[t_item]['offset_z']) + float(drillz)
                    cnc_job_obj.tools[t_item]['data']['tools_drill_ppname_e'] = cnc_job_obj.obj_options['ppname_e']

                    used_tooldia = src_tools[t_item]['tooldia'] if t_item in src_tools else \
                        cnc_job_obj.tools[t_item]['tooldia']
                    cnc_job_obj.tools[t_item]['tooldia'] = used_tooldia
                    tool_gcode = cnc_job_obj.tools[t_item]['gcode']
                    first_drill_point = cnc_job_obj.tools[t_item]['last_point']
                    gcode_parsed = cnc_job_obj.excellon_tool_gcode_parse(used_tooldia, gcode=tool_gcode,
                                                                         start_pt=first_drill_point)
                    total_gcode_parsed += gcode_parsed
                    cnc_job_obj.tools[t_item]['gcode_parsed'] = gcode_parsed
            else:
                first_tool = cnc_job_obj.tool

                cnc_job_obj.tools[first_tool]['data']['tools_drill_offset'] = \
                    float(cnc_job_obj.tools[first_tool]['offset_z']) + float(drillz)
                cnc_job_obj.tools[first_tool]['data']['tools_drill_ppname_e'] = cnc_job_obj.obj_options['ppname_e']

                used_tooldia = src_tools[first_tool]['tooldia']
                cnc_job_obj.tools[first_tool]['tooldia'] = used_tooldia
                tool_gcode = cnc_job_obj.tools[first_tool]['gcode']
                first_drill_point = cnc_job_obj.tools[first_tool]['last_point']
                gcode_parsed = cnc_job_obj.excellon_tool_gcode_parse(used_tooldia, gcode=tool_gcode,
                                                                     start_pt=first_drill_point)
                total_gcode_parsed += gcode_parsed
                cnc_job_obj.tools[first_tool]['gcode_parsed'] = gcode_parsed

            cnc_job_obj.gcode_parsed = total_gcode_parsed
            # cnc_job_obj.gcode_parse()
            cnc_job_obj.create_geometry()

        self.check_result(self.app.app_obj.new_object("cncjob", args['outname'], job_init, plot=False))
