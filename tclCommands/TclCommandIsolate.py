from tclCommands.TclCommand import TclCommandSignaled
from camlib import boundary_area

import collections

# the tool shapes of the Isolation plugin, the index is what is stored in the tool data (tools_mill_tool_shape)
TOOL_SHAPES = ["C1", "C2", "C3", "C4", "B", "V", "L"]
V_SHAPE = TOOL_SHAPES.index("V")
MILLING_TYPES = {'cl': 'cl', 'climb': 'cl', 'cv': 'cv', 'conventional': 'cv'}


class TclCommandIsolate(TclCommandSignaled):
    """
    Tcl shell command to Creates isolation routing geometry for the given Gerber.

    example:
        set_sys units MM
        new
        open_gerber tests/gerber_files/simple1.gbr -outname margin
        isolate margin -dia 3
        cncjob margin_iso
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
        ('rest', str),
        ('strategy', str),
        ('voronoi_spacing', float),
        ('pad_isolation', str)
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = ['name']

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Creates isolation routing Geometry for the specified Gerber object.",
        'args': collections.OrderedDict([
            ('name', 'Name of the Gerber source object to be isolated. Required. Several Gerber objects can be '
                     'given as a comma separated list (no spaces); each one gets its own result.'),
            ('dia', 'Tool diameter, not 0. A negative diameter isolates inside the copper features (the tool '
                    'diameter stored in the result is positive). With -rest True a comma separated list of '
                    'diameters (no spaces), e.g. 0.4,0.2,0.1. It is not used when the tool is V-shaped.'),
            ('passes', 'Passes of tool width. Integer, at least 1.'),
            ('overlap', 'Percentage of tool diameter to overlap current pass over previous pass. Float [0, 99.9999]\n'
                        'E.g: for a 25% from tool diameter overlap use -overlap 25'),
            ('combine', 'Combine all passes into one geometry. Can be True (1) or False (0). With False each pass '
                        'is a Geometry object of its own, named <outname>_<pass number>.'),
            ('outname', 'Name of the resulting Geometry object. With several Gerber objects the name of each '
                        'Gerber is added: <outname>_<gerber name>.'),
            ('iso_type', 'A value of 0 will isolate exteriors, a value of 1 will isolate interiors '
                         'and a value of 2 will do full isolation.'),
            ('clear_leftovers', 'Also add paths that clear the copper left between the passes where the passes of '
                                'two features meet (full isolation with combined passes). '
                                'Can be True (1) or False (0)'),
            ('boundary', 'Name of a Gerber or Geometry boundary object. Voronoi uses its convex hull and cuts '
                         'the compensated outer perimeter to enclose complete islands, with extra working room '
                         'for the full cutter. Offset uses the area enclosed by its outline (the concave hull if the object has flashes): paths outside it '
                         'are cut off and passes stop when one is completely outside it.'),
            ('trace_boundary', 'Also add one path along the boundary outline. Can be True (1) or False (0).'),
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
            ('except_obj', 'Name of a Gerber or Geometry object whose area is excluded: the isolation paths '
                           'are cut off inside it.'),
            ('rest', 'Rest machining: the Gerber is isolated with every tool of the -dia list, the biggest first, '
                     'and the next tool isolates what the previous one could not. One Geometry object with all '
                     'the tools results. Can be True (1) or False (0).'),
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
                     'isolate my_gerber -dia 0.4,0.2,0.1 -rest True',
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

    def _diameters(self, args, rest):
        """The tool diameters as a list of floats; None when -dia is not given."""
        if 'dia' not in args:
            return None
        try:
            dias = [float(item) for item in args['dia'].replace(';', ',').split(',') if item.strip()]
        except ValueError:
            self.raise_tcl_error("The diameter must be a number (a comma separated list with -rest True): %s" %
                                 args['dia'])
        if not dias:
            self.raise_tcl_error("The diameter must be a number (a comma separated list with -rest True): %s" %
                                 args['dia'])
        if len(dias) > 1 and not rest:
            self.raise_tcl_error("Several diameters need -rest True.")
        if any(dia == 0 or dia != dia or abs(dia) == float('inf') for dia in dias):
            self.raise_tcl_error("The tool diameter cannot be 0.")
        if len({dia < 0 for dia in dias}) > 1:
            self.raise_tcl_error("The tool diameters must be all positive or all negative.")
        return dias

    def execute(self, args, unnamed_args):
        """
        execute current TCL shell command

        :param args: array of known named arguments and options
        :param unnamed_args: array of other values which were passed into command
            without -somename and  we do not have them in known arg_names
        :return: None or exception
        """

        names = [item.strip() for item in args.pop('name').split(',') if item.strip()]
        if not names:
            self.raise_tcl_error("Missing required argument 'name'.")

        # evaluate this parameter so True, False, 0 and 1 works
        if 'combine' in args:
            args['combine'] = self._bool(args, 'combine', None)
        rest = self._bool(args, 'rest', False)
        if 'clear_leftovers' in args:
            args['clear_leftovers'] = self._bool(args, 'clear_leftovers', None)
        if 'trace_boundary' in args:
            args['trace_boundary'] = self._bool(args, 'trace_boundary', None)
        if 'pad_isolation' in args:
            args['pad_isolation'] = self._bool(args, 'pad_isolation', None)
        args['rest'] = rest

        if 'strategy' in args:
            strategy = args['strategy'].strip().lower()
            if strategy not in ('offset', 'voronoi'):
                self.raise_tcl_error("The strategy must be offset or voronoi.")
            args['strategy'] = strategy
        if 'voronoi_spacing' in args and args['voronoi_spacing'] <= 0:
            self.raise_tcl_error("The Voronoi spacing must be positive.")
        if args.get('strategy') == 'voronoi' and 'boundary' not in args:
            self.raise_tcl_error("The Voronoi strategy needs -boundary (the board outline).")

        if 'passes' in args and args['passes'] < 1:
            self.raise_tcl_error("The number of passes must be at least 1.")
        if 'overlap' in args and not 0 <= args['overlap'] < 100:
            self.raise_tcl_error("The overlap must be in [0, 99.9999] percent.")
        if 'iso_type' in args and args['iso_type'] not in (0, 1, 2):
            self.raise_tcl_error("The iso_type must be 0 (exteriors), 1 (interiors) or 2 (full).")
        if 'pad_passes' in args and args['pad_passes'] < 0:
            self.raise_tcl_error("The number of pad passes cannot be negative.")
        if 'simplify' in args and args['simplify'] < 0:
            self.raise_tcl_error("The simplification tolerance cannot be negative; 0 switches it off.")
        if 'simplify' not in args:
            # as in the Isolation plugin
            enabled = self._app_option('tools_iso_simplification', False)
            args['simplify'] = float(self._app_option('tools_iso_simplification_tol', 0.001)) if enabled else 0.0
        if 'milling_type' in args:
            milling = MILLING_TYPES.get(args['milling_type'].lower())
            if milling is None:
                self.raise_tcl_error("The milling_type must be cl (climb) or cv (conventional).")
            args['milling_type'] = milling

        use_v = 'vtipdia' in args or 'vtipangle' in args
        if 'tool_shape' in args:
            shape = args['tool_shape'].strip().upper()
            if shape not in TOOL_SHAPES:
                self.raise_tcl_error("The tool_shape must be one of %s." % ', '.join(TOOL_SHAPES))
            args['tool_shape'] = TOOL_SHAPES.index(shape)
            use_v = args['tool_shape'] == V_SHAPE
        elif use_v:
            args['tool_shape'] = V_SHAPE
        if 'vtipdia' in args and args['vtipdia'] <= 0:
            self.raise_tcl_error("The tip diameter of the V-shaped tool must be positive.")
        if 'vtipangle' in args and not 0 < args['vtipangle'] < 180:
            self.raise_tcl_error("The tip angle of the V-shaped tool must be between 0 and 180 degrees.")

        dias = self._diameters(args, rest)
        if dias is not None:
            args['dia'] = dias if rest else dias[0]
        else:
            args.pop('dia', None)

        objects = [self._object(name, ('gerber',)) for name in names]

        if 'boundary' in args:
            boundary_obj = self.app.collection.get_by_name(args['boundary'])
            if boundary_obj is None:
                self.raise_tcl_error("Object not found: %s" % args['boundary'])
                return "fail"
            args['boundary'] = boundary_area(boundary_obj)
            if args['boundary'] is None:
                self.raise_tcl_error("The boundary object %s encloses no area." %
                                     boundary_obj.obj_options['name'])
                return "fail"

        if 'except_obj' in args:
            args['except_geo'] = self._object(args.pop('except_obj'), ('gerber', 'geometry')).solid_geometry

        outname = args.pop('outname', None)
        failed = False
        for obj in objects:
            if outname is not None and len(objects) > 1:
                obj_outname = "%s_%s" % (outname, obj.obj_options['name'])
            else:
                obj_outname = outname
            if obj.isolate(plot=False, outname=obj_outname, **args) == 'fail':
                failed = True
        return 'fail' if failed else None
