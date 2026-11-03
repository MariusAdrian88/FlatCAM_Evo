import collections
import os
from copy import deepcopy

from tclCommands.TclCommand import TclCommand
from appPlugins.ToolPhotoCarve import PhotoCarveGen
from appPlugins.ToolPhotoCarve.PhotoCarveModel import DEFAULTS, PHOTOCARVE_KEYS, JobParams, PhotoCarveError, load_image
from appPlugins.ToolPhotoCarve.PhotoCarveObject import build_geometry

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class TclCommandPhotoCarve(TclCommand):
    aliases = ['photocarve']
    description = '-- ' + _("Create photo engraving geometry from an image.")
    arg_names = collections.OrderedDict([('filename', str)])
    option_types = collections.OrderedDict(
        [(name, str if isinstance(value, bool) else type(value)) for name, value in DEFAULTS.items()] +
        [('outname', str)])
    required = ['filename']
    help = {
        'main': _("Create photo engraving geometry without plotting. Uses the Photo Carve preferences."),
        'args': collections.OrderedDict(
            [('filename', _("Image file to load.")), ('outname', _("Output Geometry object name."))] +
            [(name, _("Photo Carve parameter: %s") % name) for name in DEFAULTS]),
        'examples': ['photocarve image.png -width 100 -strategy spiral -outname engraving'],
    }

    def execute(self, args, unnamed_args):
        try:
            values = {}
            for name, key in zip(DEFAULTS, PHOTOCARVE_KEYS):
                value = args.get(name, self._app_option(key, DEFAULTS[name]))
                if isinstance(DEFAULTS[name], bool):
                    value = self.parse_bool(value)
                values[key] = value
            image = load_image(args['filename'])
            units, decimals, skew = PhotoCarveGen.capture_machine(self.app)
            milldefaults = deepcopy({key: value for key, value in self.app.options.items()
                                     if key.startswith('tools_mill_')})
            params = JobParams.from_options(values, image.size, units, decimals, skew)
            result = PhotoCarveGen.run_job(image, params)
            for warning in result.warnings:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(warning))
            outname = args.get('outname', os.path.splitext(os.path.basename(args['filename']))[0] + '_photocarve')
            failure = None

            def obj_init(geo, app):
                nonlocal failure
                try:
                    built = build_geometry(result, params, milldefaults)
                except PhotoCarveError as err:
                    failure = _(str(err))
                    return 'fail'
                geo.tools = built['tools']
                geo.solid_geometry = built['solid_geometry']
                geo.multigeo = True
                geo.multitool = True
                geo.obj_options['photocarve'] = built['photocarve']

            created = self.app.app_obj.new_object('geometry', outname, obj_init, plot=False)
            return failure if failure is not None else created
        except (PhotoCarveError, ValueError) as err:
            return _(str(err))
