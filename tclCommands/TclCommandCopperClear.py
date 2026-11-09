from tclCommands.TclCommand import TclCommand
from appPlugins.ToolNCC.NccGen import NccGen
from camlib import crosshatch_angle, flatten_shapely_geometry
from shapely.geometry import Polygon, box as shapely_box

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


class TclCommandCopperClear(TclCommand):
    """
    Clear the non-copper areas.
    """

    # Array of all command aliases, to be able to use old names for backward compatibility (add_poly, add_polygon)
    aliases = ['ncc_clear', 'ncc']
    valueless_options = frozenset({'all'})

    description = '%s %s' % ("--", "Clear excess copper.")

    # dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([
        ('name', str),
    ])

    # dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
        ('tooldia', str),
        ('overlap', float),
        ('order', str),
        ('margin', float),
        ('method', str),
        ('angle', float),
        ('connect', str),
        ('contour', str),
        ('offset', float),
        ('rest', str),
        ('all', str),
        ('ref', str),
        ('box', str),
        ('area', str),
        ('exclude', str),
        ('iso_tooldia', str),
        ('milling_type', str),
        ('final_cleanup', str),
        ('simplification', float),
        ('plot', str),
        ('outname', str),
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = ['name']

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Clear excess copper in polygons. Basically it's a negative Paint.",
        'args': collections.OrderedDict([
            ('name', 'Name of the source Geometry object. String.'),
            ('tooldia', 'Diameter of the tool to be used. Can be a comma separated list of diameters.\n'
                        'WARNING: No space is allowed between tool diameters. E.g: correct: 0.5,1 / incorrect: 0.5, 1'),
            ('overlap', 'Percentage of tool diameter to overlap current pass over previous pass. Float [0, 99.9999]\n'
                        'E.g: for a 25% from tool diameter overlap use -overlap 25'),
            ('margin', 'Bounding box margin. Float number.'),
            ('order', 'Can have the values: "no", "fwd" and "rev". String.'
                      'It is useful when there are multiple tools in tooldia parameter.'
                      '"no" -> the order used is the one provided.'
                      '"fwd" -> tools are ordered from smallest to biggest.'
                      '"rev" -> tools are ordered from biggest to smallest.'),
            ('method', 'Algorithm for copper clearing. Can be: "standard", "seed", "lines", "combo" or '
                       '"crosshatch".'),
            ('angle', 'Direction of the first family of lines of the Crosshatch method, in degrees. '
                      'The second family is at 90 degrees from it. Float number.\n'
                      'Used only with the "crosshatch" method; with another method it is ignored with a warning.'),
            ('connect', 'Draw lines to minimize tool lifts. True (1) or False (0)'),
            ('contour', 'Cut around the perimeter of the painting. True (1) or False (0)'),
            ('rest', 'Use rest-machining. True (1) or False (0)'),
            ('offset', 'If used, the copper clearing will finish to a distance from copper features. Float number.\n'
                       'Without it, the Offset preferences are used.'),
            ('iso_tooldia', 'Comma separated diameters of isolation tools. Gerber source object only.\n'
                            'Without it, no isolation is generated.'),
            ('milling_type', 'Milling type of the isolation tools: "cl" (climb) or "cv" (conventional).\n'
                             'Used only together with -iso_tooldia.'),
            ('final_cleanup', 'Remove the residual copper with the smallest tool after clearing. '
                              'True (1) or False (0)'),
            ('simplification', 'Maximum deviation allowed when simplifying the toolpaths, in the units of the '
                               'application. 0 disables it. Float number.'),
            ('exclude', 'Name of a Geometry or Gerber object. Its polygons are never cleared. '
                        'A Geometry object that has only lines is rejected.'),
            ('plot', 'Plot the resulting object. True (1) or False (0). Default: 0'),
            ('all', 'Clear the whole object. Bare -all or -all 1 selects this mode; -all 0 does not. '
                    'Select exactly one mode: -all, -box <value> or -area <value>.'),
            ('box', 'Name of the reference object. Requires a value. Select exactly one mode: '
                    '-all (or -all 1), -box <value> or -area <value>.'),
            ('area', 'Rectangle to clear: "xmin,ymin,xmax,ymax" in application units, no spaces. '
                     'Select exactly one mode: -all, -box <value> or -area <value>.'),
            ('outname', 'Name of the resulting Geometry object. String. No spaces.\n'
                        'Default: <name>_ncc, or <name>_ncc_rm with rest machining.'),
        ]),
        'examples': ["ncc obj_name -tooldia 0.3,1 -overlap 10 -margin 1.0 -method 'lines' -all",
                     "ncc gerber_name -tooldia 0.5 -iso_tooldia 0.2,0.1 -milling_type cl -area 0,0,50,30 "
                     "-exclude keep_out -simplification 0.01 -plot 1"]
    }

    def _bool_arg(self, args, key):
        if key not in args:
            return None
        try:
            return self.parse_bool(args[key])
        except ValueError as err:
            self.raise_tcl_error('-%s: %s' % (key, err))

    def _object_arg(self, name, label):
        try:
            found = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("TclCommandCopperClear.execute() --> %s" % str(e))
            found = None
        if found is None or found.kind not in ('geometry', 'gerber'):
            self.raise_tcl_error('%s not found or unsupported: %s' % (label, name))
        return found

    def _diameters(self, text, label):
        try:
            values = [float(dia) for dia in str(text).split(",") if dia.strip()]
        except ValueError:
            values = []
        if not values or any(not math.isfinite(dia) or dia <= 0 for dia in values):
            self.raise_tcl_error('%s must be positive and finite numbers.' % label)
        return values

    def execute(self, args, unnamed_args):
        """
        execute current TCL shell command

        :param args: array of known named arguments and options
        :param unnamed_args: array of other values which were passed into command
            without -somename, and  we do not have them in known arg_names
        :return: None or exception
        """

        name = args['name']
        if 'ref' in args:
            if 'box' in args:
                self.raise_tcl_error('Use only one reference object: -box or -ref.')
            args['box'] = args.pop('ref')
        all_selected = 'all' in args and (args['all'] is None or self.parse_bool(args['all']))
        if int(all_selected) + ('box' in args) + ('area' in args) != 1:
            self.raise_tcl_error('Expected exactly one of -all, -box <object> or -area <xmin,ymin,xmax,ymax>.')

        obj = self._object_arg(name, 'Expected an existing Gerber or Geometry object')

        if 'tooldia' in args:
            tooldia = str(args['tooldia'])
        else:
            tooldia = str(self._app_option("tools_ncc_tools"))
        tools = self._diameters(tooldia, 'Tool diameters')

        overlap = None
        if 'overlap' in args:
            overlap = float(args['overlap']) / 100.0
            if not math.isfinite(overlap) or not 0 <= overlap < 1:
                self.raise_tcl_error('Overlap must be in [0, 100).')

        order = None
        if 'order' in args:
            orders = {'no': 0, 'fwd': 1, 'rev': 2, '0': 0, '1': 1, '2': 2}
            if str(args['order']).lower() not in orders:
                self.raise_tcl_error('Order must be no, fwd or rev.')
            order = orders[str(args['order']).lower()]

        margin = float(args['margin']) if 'margin' in args else None
        if margin is not None and not math.isfinite(margin):
            self.raise_tcl_error('Margin must be finite.')

        method = None
        if 'method' in args:
            methods = {'standard': 0, 'seed': 1, 'lines': 2, 'combo': 3, 'crosshatch': 4}
            if str(args['method']) not in methods:
                self.raise_tcl_error(
                    "Supported methods are: 'standard', 'seed', 'lines', 'combo' and 'crosshatch'.")
            method = methods[str(args['method'])]

        angle = None
        if (method if method is not None else int(self._app_option('tools_ncc_method', 0))) == 4:
            try:
                angle = float(args['angle'] if 'angle' in args else
                              self._app_option("tools_ncc_crosshatch_angle", 0.0))
            except (TypeError, ValueError):
                self.raise_tcl_error('Crosshatch angle must be a finite number.')
            if not math.isfinite(angle):
                self.raise_tcl_error('Crosshatch angle must be a finite number.')
            angle = crosshatch_angle(angle)
        elif 'angle' in args:
            self.app.inform.emit('[WARNING_NOTCL] %s' % "The -angle is ignored: it applies to the 'crosshatch' "
                                                         "method only.")

        connect =self._bool_arg(args, 'connect')
        contour = self._bool_arg(args, 'contour')
        rest = self._bool_arg(args, 'rest')
        final_cleanup = self._bool_arg(args, 'final_cleanup')
        plot = bool(self._bool_arg(args, 'plot'))

        offset = None
        has_offset = None
        if 'offset' in args:
            offset = float(args['offset'])
            if not math.isfinite(offset) or offset < 0:
                self.raise_tcl_error('Offset must be a finite number, zero or more.')
            has_offset = True

        simplification = None
        if 'simplification' in args:
            simplification = float(args['simplification'])
            if not math.isfinite(simplification) or simplification < 0:
                self.raise_tcl_error('Simplification must be a finite number, zero or more.')

        iso_tooldia = None
        if 'iso_tooldia' in args:
            if obj.kind != 'gerber':
                self.raise_tcl_error('-iso_tooldia needs a Gerber source object.')
            iso_tooldia = self._diameters(args['iso_tooldia'], 'Isolation tool diameters')
        milling_type = None
        if 'milling_type' in args:
            milling_type = str(args['milling_type']).lower()
            if milling_type not in ('cl', 'cv'):
                self.raise_tcl_error("Milling type must be 'cl' (climb) or 'cv' (conventional).")
            if not iso_tooldia:
                self.raise_tcl_error('-milling_type is used only together with -iso_tooldia.')

        exclusion_areas = None
        if 'exclude' in args:
            exclude_obj = self._object_arg(args['exclude'], 'Exclusion object')
            exclusion_areas = [poly for poly in flatten_shapely_geometry(exclude_obj.solid_geometry)
                               if isinstance(poly, Polygon) and not poly.is_empty]
            if not exclusion_areas:
                self.raise_tcl_error('The exclusion object has no polygons: %s' % args['exclude'])

        areas = None
        if 'area' in args:
            try:
                xmin, ymin, xmax, ymax = [float(v) for v in str(args['area']).split(',')]
            except ValueError:
                xmin = ymin = xmax = ymax = math.nan
            if not all(math.isfinite(v) for v in (xmin, ymin, xmax, ymax)) or xmin >= xmax or ymin >= ymax:
                self.raise_tcl_error('-area expects xmin,ymin,xmax,ymax with xmin < xmax and ymin < ymax.')
            areas = [shapely_box(xmin, ymin, xmax, ymax)]

        if 'outname' in args:
            outname = args['outname']
        else:
            rest_on = rest if rest is not None else self.parse_bool(self._app_option('tools_ncc_rest'))
            outname = name + ("_ncc_rm" if rest_on else "_ncc")

        # 0 itself, 1 area selection, 2 reference object
        select = 0 if all_selected else (1 if areas is not None else 2)
        box_obj = self._object_arg(args['box'], 'Reference object') if select == 2 else None

        # per-tool data: the preferences, overridden by the given arguments
        default_data = self.app.options.copy()
        default_data.update({"name": outname, "plot": plot, "tooldia": tooldia, "tools_ncc_ref": select,
                             "seg_x": self._app_option("geometry_seg_x"),
                             "seg_y": self._app_option("geometry_seg_y")})
        for key, value in (("tools_ncc_margin", margin), ("tools_ncc_method", method),
                           ("tools_ncc_connect", connect), ("tools_ncc_contour", contour),
                           ("tools_ncc_overlap", overlap)):
            if value is not None:
                default_data[key] = value
        if angle is not None:
            default_data["tools_ncc_crosshatch_angle"] = angle
        ncc_tools = {}
        for tool_uid, tool in enumerate(tools, 1):
            ncc_tools[tool_uid] = {
                'tooldia':          float('%.*f' % (obj.decimals, tool)),
                'data':             dict(default_data),
                'solid_geometry':   []
            }
            ncc_tools[tool_uid]['data']['tooldia'] = self.app.dec_format(tool, obj.decimals)
        # the isolation tools need their own entries, else their isolation paths have no tool to go to
        clear_dias = {float('%.*f' % (obj.decimals, tool)) for tool in tools}
        for tool in iso_tooldia or []:
            dia = float('%.*f' % (obj.decimals, tool))
            if dia in clear_dias:
                self.raise_tcl_error('A diameter can not be both in -tooldia and in -iso_tooldia: %s' % tool)
            tool_uid = len(ncc_tools) + 1
            ncc_tools[tool_uid] = {'tooldia': dia, 'data': dict(default_data), 'solid_geometry': []}
            ncc_tools[tool_uid]['data'].update({'tooldia': self.app.dec_format(tool, obj.decimals),
                                                'tools_ncc_operation': 'iso'})

        if self.app.ncclear_tool.gen is None:
            self.app.ncclear_tool.gen = NccGen(self.app.ncclear_tool)

        return self.app.ncclear_tool.gen.clear_copper_tcl(
            ncc_obj=obj,
            sel_obj=box_obj,
            areas_to_clear_list=areas,
            select_method=select,
            ncc_tooldia=tooldia,
            iso_tooldia=iso_tooldia,
            overlap=overlap,
            order=order,
            margin=margin,
            has_offset=has_offset,
            offset=offset,
            method=method,
            angle=angle,
            outname=outname,
            connect=connect,
            contour=contour,
            rest=rest,
            tools_storage=ncc_tools,
            plot=plot,
            run_threaded=False,
            final_cleanup=final_cleanup,
            milling_type=milling_type,
            simplification=simplification,
            exclusion_areas=exclusion_areas,
        )
