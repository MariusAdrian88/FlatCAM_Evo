from tclCommands.TclCommand import TclCommand
from appPlugins.ToolPaint.PaintGen import PaintGen
from camlib import clearing_simplification

from shapely import box

import collections
import math
import logging

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class TclCommandPaint(TclCommand):
    """
    Paint the interior of polygons
    """

    # Array of all command aliases, to be able use old names for backward compatibility (add_poly, add_polygon)
    aliases = ['paint']
    valueless_options = frozenset({'all'})

    description = '%s %s' % ("--", "Paint polygons in the specified object by covering them with toolpaths.")

    # dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([
        ('name', str),
    ])

    # dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
        ('tooldia', str),
        ('overlap', float),
        ('order', str),
        ('offset', float),
        ('method', str),
        ('connect', str),
        ('contour', str),
        ('rest', str),
        ('rest_offset', float),
        ('simplification', float),
        ('final_cleanup', str),

        ('all', str),
        ('single', str),
        ('ref', str),
        ('box', str),
        ('area', str),
        ('outname', str),
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = ['name']

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Paint polygons in the specified object by covering them with toolpaths.\n"
                "Defaults to all polygons. Use only one of -single, -ref (or -box) or -area to restrict the "
                "selection.",
        'args': collections.OrderedDict([
            ('name', 'Name of the source Geometry object. String.'),
            ('tooldia', 'Diameter of the tools to be used. Can be a comma separated list of diameters.\n'
                        'WARNING: No space is allowed between tool diameters. E.g: correct: 0.5,1 / incorrect: 0.5, 1'),
            ('overlap', 'Percentage of tool diameter to overlap current pass over previous pass. Float [0, 99.9999]\n'
                        'E.g: for a 25% from tool diameter overlap use -overlap 25'),
            ('offset', 'Distance from the polygon border where painting starts. Float number.'),
            ('order', 'Can have the values: "no", "fwd" and "rev". String.\n'
                      'It is useful when there are multiple tools in tooldia parameter.\n'
                      '"no" -> the order used is the one provided.\n'
                      '"fwd" -> tools are ordered from smallest to biggest.\n'
                      '"rev" -> tools are ordered from biggest to smallest.'),
            ('method', 'Algorithm for painting. Can be: "standard", "seed", "lines", "laser_lines", "combo".'),
            ('connect', 'Draw lines to minimize tool lifts. True (1) or False (0)'),
            ('contour', 'Cut around the perimeter of the painting. True (1) or False (0)'),
            ('rest', 'Use rest machining. True (1) or False (0). Default: the Paint preference.'),
            ('rest_offset', 'Distance from the polygon border kept by the rest machining. Float number.\n'
                            'Default: the Paint preference.'),
            ('simplification', 'Tolerance for simplifying the toolpaths, in the units of the object. Float >= 0.\n'
                               'Default: the Paint preference (in millimeters, converted to the units of the app).'),
            ('final_cleanup', 'After painting, use the smallest tool to paint the reachable gaps. True (1) or '
                              'False (0). Default: the Paint preference.'),
            ('all', 'Optional compatibility flag; bare -all is accepted. Its value is ignored, even False. '
                    'All polygons are painted by default unless -single, -ref, -box or -area is used; '
                    '-all overrides none of them.'),
            ('ref', 'Name of the object to be used as paint reference. String.'),
            ('box', 'Same as -ref, kept for backward compatibility. Name of the reference object. String.'),
            ('area', 'Value is in format x0,y0,x1,y1. Example: 0,0,10.5,8\n'
                     'If used will paint the polygons inside this rectangle.\n'
                     'WARNING: No spaces allowed in the value. Use dot decimals separator.'),
            ('single', 'Value is in format x,y or (x,y). Example: 2.0,1.1\n'
                       'If used will paint a single polygon specified by "x" and "y" values.\n'
                       'WARNING: No spaces allowed in the value. Use dot decimals separator.'),
            ('outname', 'Name of the resulting Geometry object. String. No spaces.'),
        ]),
        'examples': ["paint obj_name -tooldia 0.3 -offset 0.1 -method 'seed' -all",
                     "paint obj_name -tooldia 0.3 -offset 0.1 -method 'seed' -single 3.3,2.0",
                     "paint obj_name -tooldia 1,0.3 -rest 1 -rest_offset 0.05 -area 0,0,20,10",
                     "paint obj_name -tooldia 0.3 -ref ref_obj_name -final_cleanup 1 -simplification 0.005"]
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

        # Get source object.
        try:
            obj = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("TclCommandPaint.execute() --> %s" % str(e))
            self.app.log.error("Could not retrieve object: %s" % name)
            self.raise_tcl_error("%s: %s" % (_("Could not retrieve object"), name))
            return "fail"

        if obj is None or obj.kind not in ('geometry', 'gerber'):
            self.raise_tcl_error('Expected an existing Geometry or Gerber object: %s' % name)

        modes = [key for key in ('single', 'ref', 'box', 'area') if key in args]
        if len(modes) > 1:
            self.raise_tcl_error('Use only one of -single, -ref, -box or -area, not %s.' %
                                 ' and '.join('-' + key for key in modes))

        if 'tooldia' in args:
            tooldia = str(args['tooldia'])
        else:
            tooldia = str(self._app_option("tools_paint_tooldia"))

        if 'overlap' in args:
            overlap = float(args['overlap'])
        else:
            overlap = float(self._app_option("tools_paint_overlap"))
        if not math.isfinite(overlap) or not 0 <= overlap < 100:
            self.raise_tcl_error('Overlap must be in [0, 100).')

        if 'order' in args:
            order = args['order']
        else:
            order = str(self._app_option("tools_paint_order"))
        orders = {'no': 0, 'fwd': 1, 'rev': 2, '0': 0, '1': 1, '2': 2}
        if str(order).lower() not in orders:
            self.raise_tcl_error('Order must be no, fwd or rev.')
        order = orders[str(order).lower()]

        if 'offset' in args:
            offset = float(args['offset'])
        else:
            offset = float(self._app_option("tools_paint_offset"))
        if not math.isfinite(offset):
            self.raise_tcl_error('Offset must be a finite number.')

        if 'rest_offset' in args:
            rest_offset = float(args['rest_offset'])
        else:
            rest_offset = float(self._app_option("tools_paint_rest_offset", 0.0))
        if not math.isfinite(rest_offset):
            self.raise_tcl_error('Rest offset must be a finite number.')

        if 'simplification' in args:
            simplification = float(args['simplification'])
        else:
            # the preference is in millimeters, the option is in the units of the object
            simplification = float(self._app_option("tools_paint_simplification"))
            if str(self.app.app_units).upper() == 'IN':
                simplification /= 25.4
        if not math.isfinite(simplification) or simplification < 0:
            self.raise_tcl_error('Simplification must be a finite number >= 0.')
        # the strict clearing edge never simplifies: a simplified chord could cut into the copper
        simplification = clearing_simplification(self.app, simplification)

        if 'method' in args:
            methods = {'standard': 0, 'seed': 1, 'lines': 2, 'laser_lines': 3, 'combo': 4}
            method = methods.get(str(args['method']))
            if method is None:
                msg = "Method not supported or typo.\n" \
                      "Supported methods are: 'standard', 'seed', 'lines', 'laser_lines' and 'combo'."
                self.app.log.error(msg)
                self.raise_tcl_error(msg)
        else:
            method = int(self._app_option("tools_paint_method"))

        connect = self._bool_option(args, 'connect', 'tools_paint_connect')
        contour = self._bool_option(args, 'contour', 'tools_paint_contour')
        rest = self._bool_option(args, 'rest', 'tools_paint_rest')
        final_cleanup = self._bool_option(args, 'final_cleanup', 'tools_paint_final_cleanup')

        if 'outname' in args:
            outname = args['outname']
        else:
            outname = name + "_paint"

        # used only to have correct information's in the obj.tools[tool]['data'] dict
        if "single" in args:
            select = 1      # _("Polygon Selection")
        elif 'area' in args:
            select = 2      # _("Area Selection")
        elif 'ref' in args or 'box' in args:
            select = 3      # _("Reference Object")
        else:
            select = 0      # _("All")

        try:
            tools = [float(dia) for dia in tooldia.split(",") if dia.strip()]
        except ValueError:
            tools = []
        if not tools or any(not math.isfinite(dia) or dia <= 0 for dia in tools):
            self.raise_tcl_error('Tool diameters must be positive and finite.')
        # the same diameter twice, after the rounding, is one tool
        tools = list(dict.fromkeys(self.app.dec_format(dia, self.app.decimals) for dia in tools))

        # store here the default data for Geometry Data
        default_data = self.app.options.copy()
        default_data.update({
            "name":                 outname,
            "plot":                 False,
            "tooldia":              tooldia,
            "tools_mill_cutz":                 self._app_option("tools_mill_cutz"),
            "tools_paint_tipdia":              float(self._app_option("tools_paint_tipdia")),
            "tools_paint_tipangle":            float(self._app_option("tools_paint_tipangle")),
            "tools_paint_offset":       offset,
            "tools_paint_method":       method,
            "tools_paint_selectmethod": select,
            "tools_paint_connect":      connect,
            "tools_paint_contour":      contour,
            "tools_paint_overlap":      overlap,
            "tools_paint_final_cleanup": final_cleanup,
            "seg_x": self._app_option("geometry_seg_x"),
            "seg_y": self._app_option("geometry_seg_y")
        })

        # create a `tools` dict
        paint_tools = {}
        for tooluid, tool in enumerate(tools, 1):
            paint_tools[tooluid] = {
                'tooldia': tool,
                'data': dict(default_data, tooldia=tool),
                'solid_geometry': []
            }

        common = dict(tooldia=",".join(str(tool) for tool in tools),
                      order=order,
                      method=method,
                      outname=outname,
                      tools_storage=paint_tools,
                      plot=False,
                      run_threaded=False,
                      rest=rest,
                      rest_offset=rest_offset,
                      simplification=simplification)

        if self.app.paint_tool.gen is None:
            self.app.paint_tool.gen = PaintGen(self.app.paint_tool)
        gen = self.app.paint_tool.gen

        if select == 0:     # all the polygons
            ret_val = gen.paint_poly_all(obj=obj, **common)
        elif select == 1:   # a single polygon
            point = self._parse_numbers(args['single'], 2, "-single 3.2,0.1")
            ret_val = gen.paint_poly(obj=obj, inside_pt=list(point), **common)
        elif select == 2:   # the polygons inside a rectangle
            x0, y0, x1, y1 = self._parse_numbers(args['area'], 4, "-area x0,y0,x1,y1")
            if x0 == x1 or y0 == y1:
                self.raise_tcl_error('The -area rectangle must have a width and a height.')
            ret_val = gen.paint_poly_area_worker(painted_object=obj, areas_to_paint=box(x0, y0, x1, y1), **common)
        else:               # the polygons found within the reference object
            ref_name = args['ref'] if 'ref' in args else args['box']
            try:
                ref_obj = self.app.collection.get_by_name(str(ref_name))
            except Exception as e:
                self.app.log.error("TclCommandPaint.execute() --> %s" % str(e))
                ref_obj = None
            if ref_obj is None or ref_obj.kind not in ('geometry', 'gerber'):
                self.raise_tcl_error('Reference object not found or unsupported: %s' % ref_name)
            ret_val = gen.paint_poly_ref(obj=obj, sel_obj=ref_obj, **common)

        if ret_val == 'fail':
            self.raise_tcl_error('Paint failed for %s. See the log for the reason.' % name)
        return ret_val

    def _bool_option(self, args, key, pref):
        """The boolean option -key or, when it is absent, the application preference."""
        value = args[key] if key in args else self._app_option(pref)
        try:
            return self.parse_bool(value)
        except ValueError as e:
            self.raise_tcl_error('-%s: %s' % (key, e))

    def _parse_numbers(self, value, count, usage):
        """Parse "x,y[,...]" into a tuple of "count" numbers or raise a Tcl error."""
        try:
            numbers = self.parse_coords(value)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            numbers = None
        if not isinstance(numbers, (tuple, list)) or len(numbers) != count or \
                any(isinstance(item, (tuple, list)) for item in numbers):
            self.raise_tcl_error('Expected %d numbers like %s. Got: %s' % (count, usage, value))
        return tuple(numbers)
