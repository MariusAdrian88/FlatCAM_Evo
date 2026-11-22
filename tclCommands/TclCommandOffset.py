from tclCommands.TclCommand import TclCommand

import collections
import math

from defaults import AppDefaults
from appPlugins.ToolTransform.TransformGen import (alt_bounds, bounds_reference_point, parse_point, AFFINE_KINDS,
                                                   MOVETO_REFS)


class TclCommandOffset(TclCommand):
    """
    Tcl shell command to change the position of the object.

    example:
        offset my_geometry 1.2 -0.3
    """

    # List of all command aliases, to be able use old names for backward compatibility (add_poly, add_polygon)
    aliases = ['offset']

    description = '%s %s' % ("--", "Will offset the geometry of named objects (Gerber, Excellon, Geometry or CNCJob). "
                                   "Does not create a new object.")

    # Dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([

    ])

    # Dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
        ('x', float),
        ('y', float),
        ('to', str),
        ('ref', str)
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = []

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Changes the position of the named object(s) on X and/or Y axis.\n"
                "The names of the objects to be offset will be entered after the command,\n"
                "separated by spaces. See the example below.\n"
                "Works on Gerber, Excellon, Geometry and CNCJob objects (the G-code of a CNCJob is rewritten).\n"
                "WARNING: if the name of an object has spaces, enclose the name with quotes.",
        'args': collections.OrderedDict([
            ('x', 'Offset distance in the X axis. If it is not used it will be assumed to be 0.0'),
            ('y', 'Offset distance in the Y axis. If it is not used it will be assumed to be 0.0'),
            ('to', 'A point "X,Y". All the named objects are moved by the same vector, so that the reference\n'
                   'of their combined bounding box is on this point. Can not be used with -x or -y.'),
            ('ref', 'The reference of the combined bounding box used by -to: tl (top left), tr (top right),\n'
                    'bl (bottom left), br (bottom right) or c (center). If it is not used, the Move to\n'
                    'reference of the Transform plugin preferences is used.')
        ]),
        'examples': ['offset my_object_1 "my object_1" -x 1.2 -y -0.3', 'offset my_object -x 1.0',
                     'offset top bottom drill -to "6,-9" -ref tr']
    }

    def execute(self, args, unnamed_args):
        """

        :param args:
        :param unnamed_args:
        :return:
        """

        move_to = args['to'] if 'to' in args else None
        if move_to is not None and ('x' in args or 'y' in args):
            self.raise_tcl_error('-to can not be used together with -x or -y.')
        if move_to is None and 'ref' in args:
            self.raise_tcl_error('-ref is used only together with -to.')

        off_x = args['x'] if 'x' in args else 0.0
        off_y = args['y'] if 'y' in args else 0.0

        x, y = float(off_x), float(off_y)

        if move_to is None and (x, y) == (0.0, 0.0):
            self.app.log.warning("Offset by 0.0. Nothing to be done.")
            return

        obj_names = unnamed_args
        if not obj_names:
            self.app.log.error("Missing objects to be offset. Exiting.")
            return "fail"

        objects = []
        for name in obj_names:
            obj = self.app.collection.get_by_name(str(name))
            if obj is None or obj == '':
                self.app.log.error("Object not found: %s" % name)
                return "fail"
            if obj.kind not in ('geometry', 'gerber', 'excellon', 'cncjob'):
                self.raise_tcl_error('Only Gerber, Excellon, Geometry and CNCJob objects can be offset.')
            # an imported G-code with macros or subprograms can not be rewritten: an error, not a silent no-op
            blocked = obj.gcode_flow_blocked('transform') if obj.kind == 'cncjob' else None
            if blocked:
                self.raise_tcl_error(blocked.replace('[ERROR_NOTCL] ', '', 1))
            objects.append(obj)

        if move_to is not None:
            point = parse_point(move_to)
            if point is None:
                self.raise_tcl_error('Incorrect format for -to. Needs format "X,Y".')
            ref = args['ref'] if 'ref' in args else self.app.options.get(
                'tools_transform_moveto_ref', AppDefaults.factory_defaults['tools_transform_moveto_ref'])
            if ref not in MOVETO_REFS:
                self.raise_tcl_error('Unknown -ref: %s. Use one of: %s.' % (ref, ', '.join(MOVETO_REFS)))
            bounds = alt_bounds(objects, AFFINE_KINDS)
            if not all(math.isfinite(value) for value in bounds):
                self.raise_tcl_error('The named objects have no geometry.')
            ref_x, ref_y = bounds_reference_point(bounds, ref)
            x, y = point[0] - ref_x, point[1] - ref_y
            if (x, y) == (0.0, 0.0):
                self.app.log.warning("Already at the location. Nothing to be done.")
                return

        for obj in objects:
            obj.offset((x, y))
            # the exported text (source_file) must follow the geometry
            regenerate = getattr(obj, 'regenerate_source', None)
            if regenerate is not None and regenerate() is False:
                self.raise_tcl_error('Source regeneration failed: %s' % obj.obj_options.get('name', ''))

            try:
                xmin, ymin, xmax, ymax = obj.bounds()
                obj.obj_options['xmin'] = xmin
                obj.obj_options['ymin'] = ymin
                obj.obj_options['xmax'] = xmax
                obj.obj_options['ymax'] = ymax
            except Exception as e:
                self.app.log.error("TclCommandOffset -> The object has no bounds properties. %s" % str(e))
                return "fail"
