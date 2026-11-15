from tclCommands.TclCommand import TclCommandSignaled
from camlib import boundary_area, grace

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

import collections

# the tool shapes of the Isolation plugin, the index is what is stored in the tool data (tools_mill_tool_shape)
TOOL_SHAPES = ["C1", "C2", "C3", "C4", "B", "V", "L"]
V_SHAPE = TOOL_SHAPES.index("V")
MILLING_TYPES = {'cl': 'cl', 'climb': 'cl', 'cv': 'cv', 'conventional': 'cv'}
ISO_TYPES = {0: 'ext', 1: 'int', 2: 'full'}


class TclCommandIsolate(TclCommandSignaled):
    """
    Tcl shell command to Creates isolation routing geometry for the given Gerber.
    It runs the same job as the Isolation plugin (without its UI), so the result objects have the same names.

    example:
        set_sys units MM
        new
        open_gerber tests/gerber_files/simple1.gbr -outname margin
        isolate margin -dia 3
        cncjob margin_iso_combined
    """

    # array of all command aliases, to be able use  old names for backward compatibility (add_poly, add_polygon)
    aliases = ['isolate']

    description = '%s %s' % ("--", "Creates isolation routing Geometry for the specified Gerber object.")

    # dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([
        ('name', str)
    ])

    # dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
        ('dia', str),
        ('passes', int),
        ('overlap', float),
        ('combine', str),
        ('outname', str),
        ('iso_type', int),
        ('clear_leftovers', str),
        ('boundary', str),
        ('trace_boundary', str),
        ('milling_type', str),
        ('pad_passes', int),
        ('tool_shape', str),
        ('vtipdia', float),
        ('vtipangle', float),
        ('cutz', float),
        ('simplify', float),
        ('except_obj', str),
        ('use_zones', str),
        ('rest', str),
        ('forced_rest', str),
        ('ref_obj', str),
        ('area', str),
        ('db_tool', str),
        ('check_valid', str),
        ('strategy', str),
        ('voronoi_spacing', float),
        ('pad_isolation', str)
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = ['name']

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Creates isolation routing Geometry for the specified Gerber object. It runs the job of the "
                "Isolation plugin: a value that is not given is the one of the Isolation preferences, and the "
                "result objects have the names of the plugin (e.g. <gerber>_iso_combined, <gerber>_iso_rest, "
                "<gerber>_<dia>_iso1). The command never plots.",
        'args': collections.OrderedDict([
            ('name', 'Name of the Gerber source object to be isolated. Required. Several Gerber objects can be '
                     'given as a comma separated list (no spaces); each one gets its own result.'),
            ('dia', 'Tool diameter, not 0. A comma separated list of diameters (no spaces), e.g. 0.4,0.2,0.1, '
                    'makes one tool for each: without -rest each tool isolates on its own (as the tool table of '
                    'the plugin), with -rest True it is rest machining. A negative diameter isolates inside the '
                    'copper features (the tool diameter stored in the result is positive); all diameters must '
                    'have the same sign. Default: the Isolation preference. It is not used when the tool is '
                    'V-shaped.'),
            ('passes', 'Passes of tool width. Integer, at least 1.'),
            ('overlap', 'Percentage of tool diameter to overlap current pass over previous pass. Float [0, 99.9999]\n'
                        'E.g: for a 25% from tool diameter overlap use -overlap 25'),
            ('combine', 'Combine all passes into one geometry. Can be True (1) or False (0). With False each pass '
                        'is a Geometry object of its own. Rest machining is always combined.'),
            ('outname', 'Name of the resulting Geometry object. If the run makes several objects (passes that are '
                        'not combined, several tools) the name of each one is <outname> and the ending of the '
                        'plugin name. With several Gerber objects the name of each Gerber is added: '
                        '<outname>_<gerber name>.'),
            ('iso_type', 'A value of 0 will isolate exteriors, a value of 1 will isolate interiors '
                         'and a value of 2 will do full isolation.'),
            ('clear_leftovers', 'Also add paths that clear the copper left between the passes where the passes of '
                                'two features meet (full isolation with combined passes). '
                                'Can be True (1) or False (0)'),
            ('boundary', 'Name of a Gerber or Geometry boundary object. Voronoi uses its convex hull and cuts '
                         'the compensated outer perimeter to enclose complete islands, with extra working room '
                         'for the full cutter. Offset uses the area enclosed by its outline (the concave hull if '
                         'the object has flashes): paths outside it are cut off and passes stop when one is '
                         'completely outside it.'),
            ('trace_boundary', 'Also add one path along the boundary outline. Can be True (1) or False (0). '
                               'Default: the Isolation preference.'),
            ('milling_type', 'cl for climb milling or cv for conventional milling.'),
            ('pad_passes', 'Number of extra passes around the pads only, after the last pass.'),
            ('pad_isolation', 'With Voronoi, add partial halos around surviving flashed pads using the same cutter '
                              'and depth. Original traces and polygons are preserved. Pads with no safe room and '
                              'region-only or negative images are reported and skipped. True (1) or False (0). '
                              'Ignored by Offset. Normal CNC path ordering applies; not a separate machining phase.'),
            ('tool_shape', 'Shape of the tool tip: C1, C2, C3, C4, B, V or L. For V the tool diameter is the '
                           'effective one, computed from -vtipdia, -vtipangle and -cutz.'),
            ('vtipdia', 'Tip diameter of the V-shaped tool. Giving it selects the V shape.'),
            ('vtipangle', 'Tip angle of the V-shaped tool in degrees, between 0 and 180.'),
            ('cutz', 'Cut depth, used with the V-shaped tool to find its effective diameter.'),
            ('simplify', 'Simplification tolerance of the result paths; 0 switches the simplification off. '
                         'Default: the Isolation preferences.'),
            ('except_obj', 'Name of the Keepout object: a Gerber or Geometry object whose area is excluded. '
                           'The isolation paths are cut off inside it.'),
            ('use_zones', 'Also apply the exclusion zones of Isolation stored on the isolated object. True (1) or '
                          'False (0). Default: 0. The zones of NCC and Paint never apply.'),
            ('rest', 'Rest machining: the Gerber is isolated with every tool of the -dia list, the biggest first, '
                     'and the next tool isolates what the previous one could not. One Geometry object with all '
                     'the tools results. Can be True (1) or False (0). Default: the Isolation preference.'),
            ('forced_rest', 'With rest machining: isolate the polygons even if their interiors can not be '
                            'isolated. Can be True (1) or False (0). Default: the Isolation preference.'),
            ('ref_obj', 'Name of a Gerber or Geometry object (the Reference Object selection): the copper that '
                        'is not covered by it is isolated.'),
            ('area', 'Isolate only an area (the Area selection). Two points x1,y1,x2,y2 are a rectangle; three or '
                     'more points x1,y1,x2,y2,x3,y3,... are a polygon. In the units of the application.'),
            ('db_tool', 'Name of an Isolation (or General) tool of the Tools Database: its diameter and values '
                        'are used. The options given in the command win over them (one -dia value replaces the '
                        'diameter). Without it the Tools Database is not used, so a script gives the same result '
                        'on every computer.'),
            ('check_valid', 'Check that the tools fit between the copper features, as the Check validity of the '
                            'plugin does. A problem is reported and the isolation goes on. Can be True (1) or '
                            'False (0). Default: the Isolation preference.'),
            ('strategy', 'offset (default) or voronoi. Voronoi isolates different copper bodies from each other: '
                         'it needs -boundary, one positive pass and full isolation. Rest machining, exclusions, '
                         'pad passes and leftovers are unsupported. Boundary tracing is ignored.'),
            ('voronoi_spacing', 'Sampling step along the copper boundaries for the Voronoi strategy, in the '
                                'application units. This bounds sample intervals, not the toolpath error. Default: the '
                                'Isolation preferences.')
        ]),
        'examples': ['isolate my_gerber -dia 0.1 -passes 2 -overlap 10 -combine True -iso_type 2 -outname out_geo',
                     'isolate my_gerber -dia 0.1 -passes 20 -overlap 10 -clear_leftovers True',
                     'isolate my_gerber -dia 0.1 -passes 50 -overlap 10 -boundary my_edge_cuts -trace_boundary True',
                     'isolate my_gerber -dia 0.4,0.2,0.1 -rest True -forced_rest True',
                     'isolate my_gerber -dia 0.4,0.2 -combine False',
                     'isolate my_gerber -dia 0.2 -ref_obj my_other_gerber',
                     'isolate my_gerber -dia 0.2 -area 0,0,20,10',
                     'isolate my_gerber -dia 0.2 -check_valid True',
                     'isolate my_gerber -db_tool "V 30 deg" -passes 2',
                     'isolate my_gerber -tool_shape V -vtipdia 0.1 -vtipangle 30 -cutz -0.07 -milling_type cv',
                     'isolate my_gerber -dia 0.1 -passes 1 -strategy voronoi -boundary my_edge_cuts '
                     '-voronoi_spacing 0.05',
                     'isolate gerber_top,gerber_bottom -dia 0.1 -pad_passes 2 -except_obj keep_out']
    }

    def _bool(self, args, key, default):
        """The boolean option as True / False (0, 1, true, false, yes, no ...); a Tcl error for anything else."""
        if key not in args:
            return default
        try:
            return self.parse_bool(args[key])
        except ValueError:
            self.raise_tcl_error("The value of -%s must be True (1) or False (0), got: %s" % (key, args[key]))

    def _object(self, name, kinds):
        obj = self.app.collection.get_by_name(name)
        if obj is None:
            self.raise_tcl_error("Object not found: %s" % name)
        if obj.kind not in kinds:
            self.raise_tcl_error('Expected %s, got %s %s.' % (' or '.join(kinds), name, type(obj)))
        return obj

    def _diameters(self, text):
        """The tool diameters as a list of floats (a comma separated text, or a number)."""
        message = "The diameter must be a number or a comma separated list of numbers: %s" % text
        try:
            if isinstance(text, (int, float)):
                dias = [float(text)]
            else:
                dias = [float(item) for item in str(text).replace(';', ',').split(',') if item.strip()]
        except ValueError:
            self.raise_tcl_error(message)
        if not dias:
            self.raise_tcl_error(message)
        if any(dia == 0 or dia != dia or abs(dia) == float('inf') for dia in dias):
            self.raise_tcl_error("The tool diameter cannot be 0.")
        if len({dia < 0 for dia in dias}) > 1:
            self.raise_tcl_error("The tool diameters must be all positive or all negative.")
        return dias

    def _area(self, text):
        """The area of -area as (a Shapely polygon, 'square' or 'polygon')."""
        message = "The area must be x1,y1,x2,y2 (a rectangle) or x1,y1,x2,y2,x3,y3,... (a polygon): %s" % text
        try:
            numbers = [float(item) for item in text.replace(';', ',').replace(' ', ',').split(',') if item.strip()]
        except ValueError:
            self.raise_tcl_error(message)
        if len(numbers) < 4 or len(numbers) % 2:
            self.raise_tcl_error(message)
        points = list(zip(numbers[0::2], numbers[1::2]))
        if len(points) == 2:
            (x1, y1), (x2, y2) = points
            area = box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            shape = 'square'
        else:
            area = Polygon(points)
            shape = 'polygon'
        if not area.is_valid or area.is_empty or area.area == 0:
            self.raise_tcl_error("The area encloses nothing: %s" % text)
        return area, shape

    def _db_tool(self, name):
        """The Isolation (or General) tool of the Tools Database with this name; a Tcl error if there is none."""
        from appDatabase import load_tools_database
        from appPlugins.ToolIsolation import IsolationGen
        try:
            db_tools = load_tools_database(self.app.tools_database_path(), self.app.options, target=3,
                                           units=getattr(self.app, 'app_units', None))
        except (OSError, TypeError, ValueError) as error:
            self.raise_tcl_error("Could not load the Tools Database: %s" % error)
        try:
            return IsolationGen.find_db_tool(db_tools, name)
        except ValueError as error:
            self.raise_tcl_error(str(error))

    def _check_valid(self, gerber_obj, tools, decimals):
        """
        The tool validity check of the plugin. A problem is reported but the run goes on, as in the plugin.

        :return:    True if all the tools are valid
        """
        from appPlugins.ToolIsolation import IsolationGen

        def check_abort():
            if self.app.abort_flag:
                raise grace

        self.app.inform.emit("Checking tools for validity.")
        try:
            status, __ = IsolationGen.check_tools(
                gerber_obj.tools, {uid: tool['tooldia'] for uid, tool in tools.items()}, decimals,
                abort=check_abort)
        except grace:
            raise
        except Exception as error:
            self.app.log.error(str(error))
            self.app.inform.emit('[ERROR_NOTCL] Tool validation failed: %s' % error)
            return False
        if status != 'ok':
            self.app.inform.emit(IsolationGen.validity_message(status))
            return False
        self.app.inform.emit("Tool validation passed.")
        return True

    def execute(self, args, unnamed_args):
        """
        execute current TCL shell command

        :param args: array of known named arguments and options
        :param unnamed_args: array of other values which were passed into command
            without -somename and  we do not have them in known arg_names
        :return: None or exception
        """
        from appPlugins.ToolIsolation import IsolationGen
        from appPlugins.ToolIsolation.IsolationJob import IsolationJob

        names = [item.strip() for item in args.pop('name').split(',') if item.strip()]
        if not names:
            self.raise_tcl_error("Missing required argument 'name'.")

        # evaluate this parameter so True, False, 0 and 1 works
        settings = {}
        for key in ('combine', 'clear_leftovers', 'trace_boundary', 'pad_isolation', 'rest', 'forced_rest'):
            if key in args:
                settings[key] = self._bool(args, key, None)
        use_zones = self._bool(args, 'use_zones', False)
        db_tool = args.get('db_tool')
        check_valid = self._bool(args, 'check_valid', bool(self._app_option('tools_iso_check_valid', False)))

        if 'strategy' in args:
            strategy = args['strategy'].strip().lower()
            if strategy not in ('offset', 'voronoi'):
                self.raise_tcl_error("The strategy must be offset or voronoi.")
            settings['strategy'] = strategy
        if 'voronoi_spacing' in args:
            if args['voronoi_spacing'] <= 0:
                self.raise_tcl_error("The Voronoi spacing must be positive.")
            settings['voronoi_spacing'] = args['voronoi_spacing']
        if settings.get('strategy') == 'voronoi' and 'boundary' not in args:
            self.raise_tcl_error("The Voronoi strategy needs -boundary (the board outline).")

        # the values that replace the Isolation preferences in the data of every tool
        data = {}
        if 'passes' in args:
            if args['passes'] < 1:
                self.raise_tcl_error("The number of passes must be at least 1.")
            data['tools_iso_passes'] = args['passes']
        if 'overlap' in args:
            if not 0 <= args['overlap'] < 100:
                self.raise_tcl_error("The overlap must be in [0, 99.9999] percent.")
            data['tools_iso_overlap'] = args['overlap']
        if 'iso_type' in args:
            if args['iso_type'] not in ISO_TYPES:
                self.raise_tcl_error("The iso_type must be 0 (exteriors), 1 (interiors) or 2 (full).")
            data['tools_iso_isotype'] = ISO_TYPES[args['iso_type']]
        if 'pad_passes' in args:
            if args['pad_passes'] < 0:
                self.raise_tcl_error("The number of pad passes cannot be negative.")
            settings['e_passes'] = args['pad_passes']
            data['tools_iso_pad_passes'] = args['pad_passes']
        if 'simplify' in args:
            if args['simplify'] < 0:
                self.raise_tcl_error("The simplification tolerance cannot be negative; 0 switches it off.")
            settings['iso_simplification'] = args['simplify'] > 0
            if args['simplify'] > 0:
                settings['simplification_tol'] = args['simplify']
        if 'milling_type' in args:
            milling = MILLING_TYPES.get(args['milling_type'].lower())
            if milling is None:
                self.raise_tcl_error("The milling_type must be cl (climb) or cv (conventional).")
            data['tools_iso_milling_type'] = milling

        shape = None
        if 'tool_shape' in args:
            shape_name = args['tool_shape'].strip().upper()
            if shape_name not in TOOL_SHAPES:
                self.raise_tcl_error("The tool_shape must be one of %s." % ', '.join(TOOL_SHAPES))
            shape = TOOL_SHAPES.index(shape_name)
        elif 'vtipdia' in args or 'vtipangle' in args:
            shape = V_SHAPE
        if shape is not None:
            settings['tip_shape'] = shape
            data['tools_mill_tool_shape'] = data['tools_iso_tool_shape'] = shape
        if 'vtipdia' in args:
            if args['vtipdia'] <= 0:
                self.raise_tcl_error("The tip diameter of the V-shaped tool must be positive.")
            data['tools_mill_vtipdia'] = data['tools_iso_vtipdia'] = args['vtipdia']
        if 'vtipangle' in args:
            if not 0 < args['vtipangle'] < 180:
                self.raise_tcl_error("The tip angle of the V-shaped tool must be between 0 and 180 degrees.")
            data['tools_mill_vtipangle'] = data['tools_iso_vtipangle'] = args['vtipangle']
        if 'cutz' in args:
            data['tools_mill_cutz'] = data['tools_iso_cutz'] = args['cutz']
        if shape is None:
            shape = self._app_option('tools_iso_tool_shape', 0)
        use_v = shape == V_SHAPE

        # the diameters: the V-shaped tool takes its effective diameter, else -dia, else the Isolation preference
        negative_dia = False
        db_dia = None
        if db_tool is not None:
            # the database tool gives the diameter; one -dia value replaces it
            if 'dia' in args:
                dias = self._diameters(args['dia'])
                if len(dias) > 1:
                    self.raise_tcl_error("-db_tool is one tool: give one -dia value.")
                negative_dia = dias[0] < 0
                db_dia = abs(dias[0])
        elif use_v:
            if 'dia' in args:
                negative_dia = self._diameters(args['dia'])[0] < 0
            diameters = [None]
        else:
            dias = self._diameters(args['dia'] if 'dia' in args else self._app_option('tools_iso_tooldia', 0.1))
            negative_dia = dias[0] < 0
            diameters = [abs(dia) for dia in dias]

        objects = [self._object(name, ('gerber',)) for name in names]

        boundary = None
        if 'boundary' in args:
            boundary_obj = self.app.collection.get_by_name(args['boundary'])
            if boundary_obj is None:
                self.raise_tcl_error("Object not found: %s" % args['boundary'])
                return "fail"
            boundary = boundary_area(boundary_obj)
            if boundary is None:
                self.raise_tcl_error("The boundary object %s encloses no area." %
                                     boundary_obj.obj_options['name'])
                return "fail"

        exc_geometry = None
        if 'except_obj' in args:
            exc_geometry = self._object(args['except_obj'], ('gerber', 'geometry')).solid_geometry
        ref_geometry = None
        if 'ref_obj' in args:
            ref_geometry = self._object(args['ref_obj'], ('gerber', 'geometry')).solid_geometry
        limited_area = None
        if 'area' in args:
            limited_area, settings['area_shape'] = self._area(args['area'])

        decimals = self.app.decimals
        try:
            if db_tool is not None:
                tools = IsolationGen.iso_db_tool(
                    self.app.options, self._db_tool(db_tool), decimals, data=data, dia=db_dia,
                    defaults=getattr(self.app, 'defaults', None))
            else:
                tools = IsolationGen.iso_tools(
                    self.app.options, diameters, decimals, data=data, defaults=getattr(self.app, 'defaults', None),
                    warn=lambda message: self.app.inform.emit('[WARNING_NOTCL] %s' % message))
        except ValueError as error:
            self.raise_tcl_error(str(error))
        if not tools:
            self.raise_tcl_error("There is no tool to isolate with.")
        if 'tip_shape' not in settings:
            # the job gives every tool the tip shape of the run: keep the shape of the (database) tools
            shapes = {tool['data'].get('tools_mill_tool_shape') for tool in tools.values()}
            if len(shapes) == 1:
                settings['tip_shape'] = shapes.pop()

        outname = args.get('outname')
        failed = False
        for obj in objects:
            obj_exclusion = exc_geometry
            if use_zones and obj.zones_of('isolation'):
                obj_exclusion = unary_union((exc_geometry or []) + obj.zones_of('isolation'))
            if outname is not None and len(objects) > 1:
                obj_outname = "%s_%s" % (outname, obj.obj_options['name'])
            else:
                obj_outname = outname
            # as in the plugin: a failed check is reported, the run goes on
            try:
                valid = self._check_valid(obj, tools, decimals) if check_valid else True
            except grace:
                self.app.inform.emit('[WARNING_NOTCL] Tool validation aborted.')
                failed = True
                continue
            if IsolationJob.run_headless(
                    self.app, obj, tools, settings=settings, ref_geometry=ref_geometry, limited_area=limited_area,
                    negative_dia=negative_dia, exc_geometry=obj_exclusion, boundary=boundary, outname=obj_outname,
                    plot=False, validation_status=valid) == 'fail':
                failed = True
        return 'fail' if failed else None
