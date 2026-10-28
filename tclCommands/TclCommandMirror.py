from tclCommands.TclCommand import TclCommandSignaled

import collections


class TclCommandMirror(TclCommandSignaled):
    """
    Tcl shell command to mirror an object.
    """

    # array of all command aliases, to be able use
    # old names for backward compatibility (add_poly, add_polygon)
    aliases = ['mirror']

    description = '%s %s' % ("--", "Will mirror the geometry of a named object (Gerber, Excellon, Geometry or CNCJob). "
                                   "Does not create a new object.")

    # Dictionary of types from Tcl command, needs to be ordered.
    # For positional arguments
    arg_names = collections.OrderedDict([

    ])

    # Dictionary of types from Tcl command, needs to be ordered.
    # For options like -optionname value
    option_types = collections.OrderedDict([
        ('axis', str),
        ('box', str),
        ('origin', str)
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = []

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Will mirror the geometry of a named object. Does not create a new object.\n"
                "The names of the objects to be scaled will be entered after the command,\n"
                "separated by spaces. See the example below.\n"
                "Works on Gerber, Excellon, Geometry and CNCJob objects (the G-code of a CNCJob is rewritten).\n"
                "WARNING: if the name of an object has spaces, enclose the name with quotes.",
        'args': collections.OrderedDict([
            ('axis', 'Mirror axis parallel to the X or Y axis.'),
            ('box', 'Name of object which act as box (cutout for example.)'),
            ('origin', 'Reference point . It is used only if the box is not used. Format (x,y).\n'
                       'The reference point can be:\n'
                       '- "origin" which means point (0, 0)\n'
                       '- "min_bounds" which means the lower left point of the bounding box made for all objects\n'
                       '- "center" which means the center point of the bounding box made for all objects.\n'
                       '- a point in format (x,y) with the X and Y coordinates separated by a comma. NO SPACES ALLOWED\n'
                       'If neither -box nor -origin is used, the reference point is "center".')
        ]),
        'examples': ['mirror obj_name -box box_geo -axis X',
                     'mirror obj_name -axis X -origin 3.2,4.7']
    }

    def execute(self, args, unnamed_args):
        """
        Execute this TCL shell command

        :param args: array of known named arguments and options
        :param unnamed_args: array of other values which were passed into command
            without -somename and  we do not have them in known arg_names
        :return: None or exception
        """

        obj_names = unnamed_args
        if not obj_names:
            self.app.log.error("Missing objects to be offset. Exiting.")
            return "fail"

        if str(args.get('axis', 'X')).upper() not in ('X', 'Y'):
            self.raise_tcl_error('Mirror axis must be X or Y.')
        # calculate the bounds
        objects = {}
        minx_lst = []
        miny_lst = []
        maxx_lst = []
        maxy_lst = []
        for name in obj_names:
            obj = self.app.collection.get_by_name(str(name))
            if obj is None or obj == '':
                self.app.log.error("Object not found: %s" % name)
                return "fail"
            if obj.kind not in ('geometry', 'gerber', 'excellon', 'cncjob'):
                self.raise_tcl_error('Only Gerber, Excellon, Geometry and CNCJob objects can be mirrored.')
            # an imported G-code with macros or subprograms can not be rewritten: an error, not a silent no-op
            blocked = obj.gcode_flow_blocked('transform') if obj.kind == 'cncjob' else None
            if blocked:
                self.raise_tcl_error(blocked.replace('[ERROR_NOTCL] ', '', 1))
            objects[name] = obj
            a, b, c, d = obj.bounds()
            minx_lst.append(a)
            miny_lst.append(b)
            maxx_lst.append(c)
            maxy_lst.append(d)
        xmin = min(minx_lst)
        ymin = min(miny_lst)
        xmax = max(maxx_lst)
        ymax = max(maxy_lst)

        for name in obj_names:
            # Get source object.
            try:
                obj = objects[name]
            except Exception:
                self.app.log.error("Could not retrieve object: %s" % name)
                return "fail"

            if obj is None:
                self.app.log.error("Object not found: %s" % name)
                return "fail"

            if obj.kind not in ('geometry', 'gerber', 'excellon', 'cncjob'):
                self.app.log.error("ERROR: Only Gerber, Excellon, Geometry and CNCJob objects can be mirrored.")
                return "fail"

            # Axis
            if 'axis' in args:
                try:
                    axis = args['axis'].upper()
                    # fix so the Tcl command works just like in GUI
                    axis = 'X' if axis == 'Y' else 'Y'
                except KeyError:
                    axis = 'Y'
            else:
                axis = 'Y'

            # Box
            if 'box' in args:
                try:
                    box = self.app.collection.get_by_name(args['box'])
                except Exception:
                    self.app.log.error("Could not retrieve object: %s" % args['box'])
                    return "fail"

                if box is None:
                    self.app.log.error("Object box not found: %s" % args['box'])
                    return "fail"

                try:
                    xmin_b, ymin_b, xmax_b, ymax_b = box.bounds()
                except Exception as e:
                    self.app.log.error("Operation failed: %s" % str(e))
                    return "fail"
                x = 0.5 * (xmin_b + xmax_b)
                y = 0.5 * (ymin_b + ymax_b)
            # Origin
            elif 'origin' in args:
                if args['origin'] == 'origin':
                    x, y = (0, 0)
                elif args['origin'] == 'min_bounds':
                    x, y = (xmin, ymin)
                elif args['origin'] == 'center':
                    c_x = xmin + (xmax - xmin) / 2
                    c_y = ymin + (ymax - ymin) / 2
                    x, y = (c_x, c_y)
                else:
                    try:
                        origin_val = self.parse_coords(args['origin'])
                        if len(origin_val) != 2:
                            self.raise_tcl_error('Origin requires exactly two coordinates.')
                        x = float(origin_val[0])
                        y = float(origin_val[1])
                    except KeyError:
                        x, y = (0, 0)
                    except ValueError:
                        self.app.log.error("Invalid distance: %s" % str(args['origin']))
                        return "fail"
            else:
                # no -box and no -origin: use the center of the bounding box made for all objects
                x = xmin + (xmax - xmin) / 2
                y = ymin + (ymax - ymin) / 2

            try:
                obj.mirror(axis, [x, y])
            except Exception as e:
                self.app.log.error("Operation failed: %s" % str(e))
                return "fail"
            # the exported text (source_file) must follow the geometry
            regenerate = getattr(obj, 'regenerate_source', None)
            if regenerate is not None and regenerate() is False:
                self.raise_tcl_error('Source regeneration failed: %s' % name)

            # use other names: xmin, ymin, xmax, ymax are the bounds of all objects, used for the next object
            try:
                o_xmin, o_ymin, o_xmax, o_ymax = obj.bounds()
                obj.obj_options['xmin'] = o_xmin
                obj.obj_options['ymin'] = o_ymin
                obj.obj_options['xmax'] = o_xmax
                obj.obj_options['ymax'] = o_ymax
            except Exception as e:
                self.app.log.error("TclCommandMirror -> The object has no bounds properties. %s" % str(e))
                return "fail"
