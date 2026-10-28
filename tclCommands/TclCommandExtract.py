import collections
import logging

import gettext
import appTranslation as fcTranslate
import builtins

from tclCommands.TclCommand import TclCommand
from appPlugins.ToolExtract import ExtractGen
from appHandlers.appJoinConvert import geometry_shapes

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

TYPES = ('drills', 'soldermask', 'cutout')
METHODS = ('fixed', 'ring', 'prop')
SUFFIXES = {'drills': 'extracted', 'soldermask': 'esm', 'cutout': 'ecut'}
# kind -> the app option suffix (the 'other' kind is stored as 'others')
KIND_OPTIONS = {'circular': 'circular', 'oblong': 'oblong', 'square': 'square', 'rectangular': 'rectangular',
                'other': 'others'}


class TclCommandExtract(TclCommand):
    """
    Tcl shell command to extract drills, a soldermask or a cutout outline from a Gerber object.
    """

    aliases = ['extract']

    description = '%s %s' % ("--", "Extract drills (Excellon), a soldermask or a cutout outline (Gerber) from a "
                                   "Gerber object into a new object.")

    arg_names = collections.OrderedDict([
        ('object', str),
    ])

    option_types = collections.OrderedDict([
        ('type', str),
        ('method', str),
        ('dia', float),
        ('ring', float),
        ('factor', float),
        ('apertures', str),
        ('kinds', str),
        ('slots', str),
        ('regions', str),
        ('clearance', float),
        ('margin', float),
        ('thickness', float),
        ('shape', str),
        ('outname', str),
    ])

    required = ['object', 'type']

    help = {
        'main': "Extract drills (an Excellon object), a soldermask or a cutout outline (Gerber objects) from the "
                "pads of a Gerber object (the Extract plugin). The values that are not given are taken from the "
                "Extract plugin defaults.",
        'args': collections.OrderedDict([
            ('object', 'Name of the Gerber object (a Geometry object for drills: its circles). Required.'),
            ('type', 'drills, soldermask or cutout. Required.'),
            ('method', 'drills: fixed, ring or prop (proportional to the pad size).'),
            ('dia', 'drills, fixed method: the hole diameter.'),
            ('ring', 'drills, ring method: the annular ring, the same for all pad kinds.'),
            ('factor', 'drills, prop method: the hole size as a percent of the pad size.'),
            ('apertures', 'Comma separated aperture codes to use. Default all.'),
            ('kinds', 'Comma separated pad kinds to use: circular, oblong, square, rectangular, other.'),
            ('slots', 'drills: 1 makes slots from the oblong pads.'),
            ('regions', 'soldermask: 1 includes the regions.'),
            ('clearance', 'soldermask: grows every pad by this value.'),
            ('margin', 'cutout: the distance between the board and the cutout outline.'),
            ('thickness', 'cutout: the width of the outline line.'),
            ('shape', 'cutout: rect (bounding box, default) or convex (convex hull).'),
            ('outname', 'Name of the new object. Default: object name + _extracted, _esm or _ecut.'),
        ]),
        'examples': ['extract board.gbr -type drills -method fixed -dia 0.8',
                     'extract board.gbr -type drills -method ring -ring 0.25 -kinds "circular,oblong" -slots 1',
                     'extract board.gbr -type soldermask -clearance 0.1 -regions 1',
                     'extract board.gbr -type cutout -margin 1 -thickness 0.2 -shape convex -outname outline']
    }

    ERRORS = {
        ExtractGen.ERR_NO_APERTURES: "There are no apertures to use. Check the apertures and the pad kinds.",
        ExtractGen.ERR_NO_PADS: "No pads were found for the selected apertures and pad kinds.",
        ExtractGen.ERR_BAD_DIAMETER: "The hole diameter must be greater than zero.",
        ExtractGen.ERR_BAD_THICKNESS: "The thickness must be greater than zero.",
        ExtractGen.ERR_NO_CIRCLES: "The object has no circular shapes.",
        ExtractGen.ERR_NO_GEOMETRY: "The object has no geometry.",
        ExtractGen.ERR_EMPTY_SHAPE: "The resulting outline is empty.",
    }

    SKIPPED = {
        ExtractGen.SKIP_NON_POSITIVE: "pads skipped: the hole would have no size",
        ExtractGen.SKIP_DUPLICATE: "duplicate holes removed",
        ExtractGen.SKIP_UNKNOWN_SIZE: "pads skipped: unknown size",
        ExtractGen.SKIP_EMPTY: "pads skipped: empty shape",
    }

    def _flag(self, args, key, pref, default):
        value = args[key] if key in args else self._app_option(pref, default)
        try:
            return self.parse_bool(value)
        except ValueError as err:
            self.raise_tcl_error('-%s: %s' % (key, err))

    def _number(self, args, key, pref, default):
        if key not in args:
            return self._pref_number(pref, default)
        try:
            return float(args[key])
        except (TypeError, ValueError):
            self.raise_tcl_error('-%s: %s' % (key, _("A number is expected")))

    def _pref_number(self, pref, default):
        try:
            return float(self._app_option(pref, default))
        except (TypeError, ValueError):
            self.raise_tcl_error('-%s: %s' % (pref, _("A number is expected")))

    def _kinds(self, args):
        if args.get('kinds') not in (None, ''):
            kinds = []
            for token in str(args['kinds']).split(','):
                token = token.strip().lower()
                if not token:
                    continue
                token = 'other' if token == 'others' else token
                if token not in KIND_OPTIONS:
                    self.raise_tcl_error('%s: %s' % (_("Unknown pad kind"), token))
                kinds.append(token)
            return kinds
        return [k for k, opt in KIND_OPTIONS.items() if self._app_option('tools_extract_%s' % opt, k == 'circular')]

    def _codes(self, gerber, args):
        if args.get('apertures') in (None, ''):
            return None
        keys = {str(k): k for k in (gerber.tools or {})}
        codes = []
        for token in str(args['apertures']).split(','):
            token = token.strip()
            if not token:
                continue
            if token not in keys:
                self.raise_tcl_error('%s: %s' % (_("Unknown aperture code"), token))
            codes.append(keys[token])
        return codes

    def _fail_text(self, result):
        text = self.ERRORS.get(result.error)
        return _(text) if text else '%s: %s' % (_("Extract failed"), result.error)

    def _skipped_text(self, result):
        parts = ['%d %s' % (count, _(self.SKIPPED.get(reason, reason))) for reason, count in result.skipped.items()
                 if count]
        return ' (%s)' % ', '.join(parts) if parts else ''

    def execute(self, args, unnamed_args):
        name = str(args['object'])
        kind_type = str(args['type']).lower()
        if kind_type not in TYPES:
            self.raise_tcl_error('%s: %s' % (_("Unknown type, use drills, soldermask or cutout"), kind_type))
            return 'fail'

        gerber = self.app.collection.get_by_name(name)
        if gerber is None:
            self.raise_tcl_error('%s: %s' % (_("Object not found"), name))
            return 'fail'
        from_geometry = getattr(gerber, 'kind', None) == 'geometry' and kind_type == 'drills'
        if getattr(gerber, 'kind', None) != 'gerber' and not from_geometry:
            self.raise_tcl_error('%s: %s' % (_("Expected a Gerber object"), name))
            return 'fail'

        decimals = getattr(self.app, 'decimals', 4)
        outname = str(args.get('outname') or ExtractGen.out_name(name, SUFFIXES[kind_type]))

        if from_geometry:
            method = str(args.get('method') or self._app_option('tools_extract_hole_type', 'fixed')).lower()
            if method not in METHODS:
                self.raise_tcl_error('%s: %s' % (_("Unknown method, use fixed, ring or prop"), method))
                return 'fail'
            shapes = [shape for _dia, parts in geometry_shapes(gerber) for shape in parts]
            result = ExtractGen.extract_drills_geometry(
                shapes, method, decimals,
                fixed_dia=self._number(args, 'dia', 'tools_extract_hole_fixed_dia', 0.5),
                ring=self._number(args, 'ring', 'tools_extract_circular_ring', 0.2),
                factor=self._number(args, 'factor', 'tools_extract_hole_prop_factor', 80.0) / 100.0)
        elif kind_type == 'cutout':
            margin = self._number(args, 'margin', 'tools_extract_cut_margin', 0.1)
            thickness = self._number(args, 'thickness', 'tools_extract_cut_thickness', 0.1)
            shape = str(args.get('shape') or self._app_option('tools_extract_cut_shape', 'rect')).lower()
            if shape not in ('rect', 'convex'):
                self.raise_tcl_error('%s: %s' % (_("Unknown shape, use rect or convex"), shape))
                return 'fail'
            result = ExtractGen.extract_cutout(gerber.solid_geometry, margin, thickness, shape=shape)
        else:
            kinds = self._kinds(args)
            if not kinds:
                self.raise_tcl_error(_("Select at least one pad kind."))
                return 'fail'
            sources = [(gerber.tools, self._codes(gerber, args))]
            if kind_type == 'soldermask':
                clearance = self._number(args, 'clearance', 'tools_extract_sm_clearance', 0.1)
                regions = self._flag(args, 'regions', 'tools_extract_include_regions', False)
                result = ExtractGen.extract_soldermask(sources, kinds, clearance, decimals, include_regions=regions)
            else:
                method = str(args.get('method') or self._app_option('tools_extract_hole_type', 'fixed')).lower()
                if method not in METHODS:
                    self.raise_tcl_error('%s: %s' % (_("Unknown method, use fixed, ring or prop"), method))
                    return 'fail'
                fixed_dia = self._number(args, 'dia', 'tools_extract_hole_fixed_dia', 0.5)
                factor = self._number(args, 'factor', 'tools_extract_hole_prop_factor', 80.0) / 100.0
                if 'ring' in args:
                    ring = self._number(args, 'ring', 'tools_extract_circular_ring', 0.2)
                    rings = {k: ring for k in KIND_OPTIONS}
                else:
                    rings = {k: self._pref_number('tools_extract_%s_ring' % opt, 0.2)
                             for k, opt in KIND_OPTIONS.items()}
                slots = self._flag(args, 'slots', 'tools_extract_oblong_slots', False)
                result = ExtractGen.extract_drills(sources, method, kinds, decimals, fixed_dia=fixed_dia,
                                                   rings=rings, factor=factor, oblong_slots=slots)

        if result.error:
            self.raise_tcl_error(self._fail_text(result))
            return 'fail'

        def init(new_obj, app_obj):
            if result.kind == 'excellon':
                new_obj.tools = result.tools
                # the Gerber options do not apply to an Excellon object, so they are not copied
                if new_obj.create_geometry() == 'fail':
                    return 'fail'
                new_obj.regenerate_source(name=outname)
                return None
            from copy import deepcopy
            if getattr(new_obj, 'obj_options', None) is None:
                new_obj.obj_options = {}
            for key, value in (gerber.obj_options or {}).items():
                if key != 'name':
                    new_obj.obj_options[key] = deepcopy(value)
            ExtractGen.apply_result(new_obj, result)
            try:
                a, b, c, d = new_obj.bounds()
                new_obj.obj_options['xmin'] = a
                new_obj.obj_options['ymin'] = b
                new_obj.obj_options['xmax'] = c
                new_obj.obj_options['ymax'] = d
            except Exception as err:
                log.error("TclCommandExtract bounds error --> %s" % str(err))
            new_obj.regenerate_source(name=outname)
            return None

        self.check_result(self.app.app_obj.new_object(result.kind, outname, init, plot=False))
        self.app.inform.emit('[success] %s%s' % (_("Extract done."), self._skipped_text(result)))
        return None
