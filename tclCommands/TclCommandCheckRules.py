import collections
import logging

import gettext
import appTranslation as fcTranslate
import builtins

from tclCommands.TclCommand import TclCommand

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

LAYER_KEYS = ('copper_top', 'copper_bottom', 'sm_top', 'sm_bottom', 'silk_top', 'silk_bottom', 'outline',
              'exc_1', 'exc_2')
EXCELLON_KEYS = ('exc_1', 'exc_2')
RULE_KEYS = ('trace_size', 'c2c', 'c2o', 's2s', 's2sm', 's2o', 'sm2sm', 'ri', 'h2h', 'dh', 'slot', 'h2o', 'h2c',
             'iso')


class TclCommandCheckRules(TclCommand):
    """
    Tcl shell command to run the design rules check on Gerber and Excellon objects.
    """

    aliases = ['check_rules']

    description = '%s %s' % ("--", "Run the design rules check (Check Rules) on the given objects.")

    arg_names = collections.OrderedDict()

    option_types = collections.OrderedDict(
        [(key, str) for key in LAYER_KEYS] +
        [(key, float) for key in RULE_KEYS] +
        [('outname', str), ('csv', str)]
    )

    required = []

    help = {
        'main': "Run the design rules check. A rule runs only when its value is given. At least one rule is "
                "required and each rule needs its objects: a rule without them is an error. The result is one line "
                "per rule: PASSED, FAILED (n), NOTHING TO CHECK or ERROR: message, and a last line with the total "
                "number of violations.",
        'args': collections.OrderedDict([
            ('copper_top', 'Name of the top copper Gerber object.'),
            ('copper_bottom', 'Name of the bottom copper Gerber object.'),
            ('sm_top', 'Name of the top solder mask Gerber object.'),
            ('sm_bottom', 'Name of the bottom solder mask Gerber object.'),
            ('silk_top', 'Name of the top silkscreen Gerber object.'),
            ('silk_bottom', 'Name of the bottom silkscreen Gerber object.'),
            ('outline', 'Name of the board outline Gerber object.'),
            ('exc_1', 'Name of the first Excellon object.'),
            ('exc_2', 'Name of the second Excellon object.'),
            ('trace_size', 'Minimum trace size.'),
            ('c2c', 'Minimum copper to copper clearance.'),
            ('c2o', 'Minimum copper to outline clearance.'),
            ('s2s', 'Minimum silk to silk clearance.'),
            ('s2sm', 'Minimum silk to solder mask clearance.'),
            ('s2o', 'Minimum silk to outline clearance.'),
            ('sm2sm', 'Minimum solder mask sliver.'),
            ('ri', 'Minimum annular ring.'),
            ('h2h', 'Minimum hole to hole clearance.'),
            ('dh', 'Minimum drill size.'),
            ('slot', 'Minimum slot width.'),
            ('h2o', 'Minimum hole to outline clearance.'),
            ('h2c', 'Minimum hole to copper clearance.'),
            ('iso', 'Isolation tool diameter: reports copper gaps narrower than the tool.'),
            ('outname', 'Optional. Name of a Document object that receives the report.'),
            ('csv', 'Optional. Path of a CSV file with the columns rule, layer, x, y, value, limit, status.'),
        ]),
        'examples': ['check_rules -copper_top a.GTL -outline a.GKO -exc_1 a.DRL -c2c 0.15 -c2o 0.3 -dh 0.3 '
                     '-outname drc_report -csv drc.csv']
    }

    def execute(self, args, unnamed_args):
        from appPlugins.ToolRulesCheck import RulesCheckGen as Gen

        rule_keys = [key for key in RULE_KEYS if key in args]
        if not rule_keys:
            self.raise_tcl_error(_("No rule given. Give at least one rule value, e.g. -c2c 0.15."))
            return 'fail'

        layers = {key: None for key in LAYER_KEYS}
        for key in LAYER_KEYS:
            if key not in args:
                continue
            obj = self.app.collection.get_by_name(str(args[key]))
            if obj is None:
                self.raise_tcl_error('%s: %s' % (_("Object not found"), args[key]))
                return 'fail'
            if key in EXCELLON_KEYS:
                if getattr(obj, 'kind', 'excellon') != 'excellon':
                    self.raise_tcl_error('%s: %s' % (_("Not an Excellon object"), args[key]))
                    return 'fail'
                layers[key] = Gen.excellon_layer(obj)
            else:
                if getattr(obj, 'kind', 'gerber') != 'gerber':
                    self.raise_tcl_error('%s: %s' % (_("Not a Gerber object"), args[key]))
                    return 'fail'
                layers[key] = Gen.gerber_layer(obj)

        config = {key: {'enabled': True, 'value': float(args[key])} for key in rule_keys}

        problems = Gen.validate_config(config, layers)
        if problems:
            self.raise_tcl_error('\n'.join(problems))
            return 'fail'

        results = Gen.run_jobs_sync(Gen.build_jobs(config, layers))

        lines = []
        total = 0
        for res in results:
            count = len(res['violations'])
            total += count
            text = Gen.results_status(res)
            if text == 'ERROR':
                text = 'ERROR: %s' % res['error']
            elif text == 'FAILED':
                text = 'FAILED (%d)' % count
            lines.append('%s: %s' % (res['rule'], text))
        lines.append('%s: %d' % (_("Total violations"), total))

        if 'csv' in args:
            try:
                Gen.results_to_csv(results, args['csv'])
            except OSError as err:
                self.raise_tcl_error('%s: %s' % (_("Cannot write the CSV file"), err))
                return 'fail'

        if 'outname' in args:
            report = Gen.render_report(results, getattr(self.app, 'decimals', 4), getattr(self.app, 'app_units', ''))

            def init(new_obj, app_obj):
                new_obj.source_file = report
                new_obj.document_type = 'html'
                new_obj.read_only = True

            self.check_result(self.app.app_obj.new_object('document', str(args['outname']), init, plot=False))

        return '\n'.join(lines)
