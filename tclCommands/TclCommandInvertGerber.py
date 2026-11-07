import collections

import gettext
import appTranslation as fcTranslate
import builtins
from copy import deepcopy

from camlib import object_circle_steps
from tclCommands.TclCommand import TclCommand

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

JOIN_CODES = ('r', 's', 'b')


class TclCommandInvertGerber(TclCommand):
    """
    Tcl shell command to invert the copper of a Gerber object into a new Gerber object (the Invert Gerber plugin).
    """

    aliases = ['invert_gerber']

    description = '%s %s' % ("--", "Invert the copper of a Gerber object into a new Gerber object: the area "
                                   "(the box of the object grown by the margin, or a reference area) minus the "
                                   "copper.")

    arg_names = collections.OrderedDict([
        ('name', str),
    ])

    option_types = collections.OrderedDict([
        ('margin', float),
        ('join', str),
        ('reference', str),
        ('ref_area', str),
        ('outname', str),
    ])

    required = ['name']

    help = {
        'main': "Invert the copper of a Gerber object (the Invert Gerber plugin): the new Gerber object holds the "
                "area minus the copper. The area is the bounding box of the object grown by the margin, or the "
                "area of a reference object. The source object is not changed. The values that are not given are "
                "taken from the Invert Gerber plugin defaults.",
        'args': collections.OrderedDict([
            ('name', 'Name of the Gerber object. Required.'),
            ('margin', 'Distance added around the box of the object. Default: the plugin Margin.'),
            ('join', 'Corner style of the margin: r (round), s (square, mitre) or b (bevel). Default: the plugin Join.'),
            ('reference', 'Name of a Gerber or Geometry object whose area is inverted instead of the box.'),
            ('ref_area', 'For -reference: box (bounding box of the reference, default) or exact (its shape).'),
            ('outname', 'Name of the new object. Default: name + _inverted.'),
        ]),
        'examples': ['invert_gerber board.gtl -margin 1 -join s',
                     'invert_gerber board.gtl -reference outline -ref_area exact -outname board_inv']
    }

    ERRORS = {
        'no_bounds': _("The object has no bounds to invert."),
        'empty_area': _("The area to invert is empty."),
        'empty_reference': _("The reference object has no area."),
        'covered': _("The copper covers the whole area: nothing to invert."),
    }

    def _number(self, args, key, pref, default):
        value = args[key] if key in args else self._app_option(pref, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            self.raise_tcl_error('-%s: %s' % (key, _("A number is expected")))

    def _gerber(self, name):
        obj = self.app.collection.get_by_name(name)
        if obj is None:
            self.raise_tcl_error('%s: %s' % (_("Object not found"), name))
        if getattr(obj, 'kind', None) != 'gerber':
            self.raise_tcl_error('%s: %s' % (_("Expected a Gerber object"), name))
        return obj

    def execute(self, args, unnamed_args):
        from appPlugins.ToolInvertGerber import InvertGerberGen as Gen
        from appPlugins.ToolSub.SubGen import units_factor

        name = str(args['name'])
        source = self._gerber(name)

        margin = self._number(args, 'margin', 'tools_invert_margin', 0.1)
        join = str(args.get('join') or self._app_option('tools_invert_join_style', 's')).lower()
        if join not in JOIN_CODES:
            self.raise_tcl_error('%s: %s' % (_("Unknown join style, use r, s or b"), join))
        ref_mode = str(args.get('ref_area') or self._app_option('tools_invert_ref_area', 'box')).lower()
        if ref_mode not in Gen.REFERENCE_MODES:
            self.raise_tcl_error('%s: %s' % (_("Unknown reference area, use box or exact"), ref_mode))

        # the circles of a reference object (a Gerber or Geometry object has none to make: a 1 mm radius is assumed)
        hole_steps = object_circle_steps(self.app.options, 'excellon', 1.0, getattr(self.app, 'app_units', 'MM'))
        outname = str(args.get('outname') or name + '_inverted')
        units = (source.obj_options or {}).get('units')
        # the margin is in app units, the geometry is in the units of the source
        margin *= units_factor(units, getattr(self.app, 'app_units', None))

        area = None
        if 'reference' in args:
            ref_name = str(args['reference'])
            ref = self.app.collection.get_by_name(ref_name)
            if ref is None or getattr(ref, 'kind', None) not in ('gerber', 'geometry'):
                self.raise_tcl_error('%s: %s' % (_("Expected a Gerber or Geometry reference object"), ref_name))
            try:
                area = Gen.reference_area(ref, ref_mode, hole_steps, target_units=units)
            except Gen.InvertError:
                self.raise_tcl_error(self.ERRORS['empty_reference'])

        try:
            bounds = source.bounds()
        except Exception:
            bounds = None

        try:
            polys = Gen.invert_geometry(source.solid_geometry, bounds, margin, self.app.options, join, area=area, units=units)
        except Gen.InvertError as err:
            text = self.ERRORS['no_bounds'] if 'bounds' in str(err) else self.ERRORS['empty_area']
            self.raise_tcl_error(text)
        if not polys:
            self.raise_tcl_error(self.ERRORS['covered'])

        def init(new_obj, app_obj):
            if getattr(new_obj, 'obj_options', None) is None:
                new_obj.obj_options = {}
            for key, value in (source.obj_options or {}).items():
                if key != 'name':
                    new_obj.obj_options[key] = deepcopy(value)
            new_obj.tools = Gen.apertures_from_polygons(polys)
            new_obj.solid_geometry = polys
            new_obj.regenerate_source(name=outname)

        self.check_result(self.app.app_obj.new_object('gerber', outname, init, plot=False))
        self.app.inform.emit('[success] %s' % _("Invert Gerber done."))
        return None
