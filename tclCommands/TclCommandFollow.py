import collections
import logging
import os

import gettext
import appTranslation as fcTranslate
import builtins

from tclCommands.TclCommand import TclCommand

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

DIA_MODES = ('default', 'aperture')


class TclCommandFollow(TclCommand):
    """
    Tcl shell command to create a Geometry object that follows the paths of a Gerber object.
    """

    aliases = ['follow']

    description = '%s %s' % ("--", "Creates a Geometry object following Gerber paths.")

    arg_names = collections.OrderedDict([
        ('name', str)
    ])

    option_types = collections.OrderedDict([
        ('outname', str),
        ('union', str),
        ('simplify', str),
        ('tolerance', float),
        ('merge', str),
        ('box', str),
        ('apertures', str),
        ('dia_mode', str),
        ('tooldia', float),
    ])

    required = ['name']

    help = {
        'main': "Creates a Geometry object following the paths of a Gerber object (the Follow plugin). The values "
                "that are not given are taken from the Follow plugin defaults.",
        'args': collections.OrderedDict([
            ('name', 'Name of the Gerber object to follow. Required.'),
            ('outname', 'Name of the resulting Geometry object. Default: the name + the plugin suffix.'),
            ('union', '1 unions the paths (nodes the crossing lines).'),
            ('simplify', '1 simplifies the paths with the tolerance.'),
            ('tolerance', 'Simplification tolerance, in the units of the application.'),
            ('merge', '1 joins the paths that touch each other into longer paths.'),
            ('box', 'Follow only the part of the paths inside this area: xmin,ymin,xmax,ymax.'),
            ('apertures', 'Comma separated ids of the apertures to follow. Default all.'),
            ('dia_mode', 'default: one tool with the tooldia; aperture: one tool per aperture size.'),
            ('tooldia', 'Tool diameter. Default: the first diameter of the Milling tool diameters.'),
        ]),
        'examples': ['follow copper.gtl',
                     'follow copper.gtl -outname copper_follow -union 1 -simplify 1 -tolerance 0.02',
                     'follow copper.gtl -box 0,0,20,10 -tooldia 0.2',
                     'follow copper.gtl -apertures 10,11 -dia_mode aperture']
    }

    def _flag(self, args, key, pref, default):
        value = args[key] if key in args else self._app_option(pref, default)
        try:
            return self.parse_bool(value)
        except ValueError as err:
            self.raise_tcl_error('-%s: %s' % (key, err))

    def execute(self, args, unnamed_args):
        from shapely.geometry import box as shapely_box
        from appPlugins.ToolFollow import FollowGen

        name = str(args['name'])
        source = self.app.collection.get_by_name(name)
        if source is None:
            self.raise_tcl_error('%s: %s' % (_("Object not found"), name))
            return 'fail'
        if getattr(source, 'kind', None) != 'gerber':
            self.raise_tcl_error('%s: %s' % (_("Expected a Gerber object"), name))
            return 'fail'

        union = self._flag(args, 'union', 'tools_follow_union', False)
        simplify = self._flag(args, 'simplify', 'tools_follow_simplification', False)
        merge = self._flag(args, 'merge', 'tools_follow_merge', True)

        try:
            tolerance = float(args['tolerance'] if 'tolerance' in args else
                              self._app_option('tools_follow_tolerance', 0.01))
        except (TypeError, ValueError):
            self.raise_tcl_error(_("The tolerance must be a number."))
            return 'fail'
        if tolerance < 0:
            self.raise_tcl_error(_("The tolerance can not be negative."))
            return 'fail'

        dia_mode = str(args['dia_mode'] if 'dia_mode' in args else
                       self._app_option('tools_follow_dia_mode', 'default')).strip().lower()
        if dia_mode not in DIA_MODES:
            self.raise_tcl_error('%s: %s' % (_("Unknown diameter mode, use default or aperture"), dia_mode))
            return 'fail'

        try:
            if 'tooldia' in args:
                tooldia = float(args['tooldia'])
                if tooldia <= 0:
                    raise ValueError(_("The tool diameter must be greater than 0."))
            else:
                tooldia = FollowGen.tool_dias(self._app_option('tools_mill_tooldia', 0.1))[0]
        except (TypeError, ValueError) as err:
            self.raise_tcl_error(str(err))
            return 'fail'

        selection = None
        if args.get('box') not in (None, ''):
            try:
                xmin, ymin, xmax, ymax = [float(v) for v in str(args['box']).replace(' ', '').split(',')]
            except ValueError:
                self.raise_tcl_error(_("The box must be four numbers: xmin,ymin,xmax,ymax."))
                return 'fail'
            if xmax <= xmin or ymax <= ymin:
                self.raise_tcl_error(_("The box has no area."))
                return 'fail'
            selection = [shapely_box(xmin, ymin, xmax, ymax)]

        apertures = None
        if args.get('apertures') not in (None, ''):
            keys = {str(k) for k in (source.tools or {})}
            apertures = []
            for token in str(args['apertures']).split(','):
                token = token.strip()
                if not token:
                    continue
                if token not in keys:
                    self.raise_tcl_error('%s: %s' % (_("Unknown aperture id"), token))
                    return 'fail'
                apertures.append(token)

        params = {'union': union, 'simplify': simplify, 'tolerance': tolerance, 'merge': merge,
                  'selection': selection, 'apertures': apertures, 'dia_mode': dia_mode, 'tooldia': tooldia}
        groups = FollowGen.follow_groups(source, params)
        if not groups:
            self.raise_tcl_error(_("No paths to follow in %s.") % name)
            return 'fail'

        suffix = str(self._app_option('tools_follow_suffix', '_follow') or '')
        outname = str(args.get('outname') or (os.path.splitext(name)[0] + suffix))
        data = FollowGen.geometry_data(self.app.options, outname)
        tools = FollowGen.follow_tools(groups, data, int(getattr(self.app, 'decimals', 4)))

        def init(new_obj, app_obj):
            new_obj.obj_options['tools_mill_tooldia'] = groups[0][0]
            new_obj.tools = tools

        self.check_result(self.app.app_obj.new_object('geometry', outname, init, plot=False))
        return None
