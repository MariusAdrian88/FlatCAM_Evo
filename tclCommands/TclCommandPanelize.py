from appParsers import panelize_core
from tclCommands.TclCommand import TclCommand

import logging
import collections
from copy import deepcopy

import gettext
import appTranslation as fcTranslate
import builtins

log = logging.getLogger('base')

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def parse_bool(value):
    """
    Safe parse of a Tcl boolean option: True / False for 1/0, true/false, yes/no, on/off, t/f, y/n (any case),
    None for anything else. (The option used to go through eval().)
    """
    try:
        return TclCommand.parse_bool(value)
    except ValueError:
        return None


class TclCommandPanelize(TclCommand):
    """
    Tcl shell command to panelize an object.

    example:

    """

    # List of all command aliases, to be able use old names for backward compatibility (add_poly, add_polygon)
    aliases = ['panelize', 'pan', 'panel']

    description = '%s %s' % ("--", "Create a new object with an array of duplicates of the original geometry, "
                                   "arranged in a grid.")

    # Dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([
        ('name', str),
    ])

    # Dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
        ('rows', int),
        ('columns', int),
        ('spacing_columns', float),
        ('spacing_rows', float),
        ('box', str),
        ('outname', str),
        ('use_thread', str)
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = ['name']

    # structured help for current command, args needs to be ordered
    help = {
        'main': 'Create a new object with an array of duplicates of the original geometry, arranged in a grid.',
        'args': collections.OrderedDict([
            ('name', 'Name of the object to panelize.'),
            ('box', 'Name of object which acts as box (cutout for example.)'
                    'for cutout boundary. Object from name is used if not specified.'),
            ('spacing_columns', 'Spacing between columns.'),
            ('spacing_rows', 'Spacing between rows.'),
            ('columns', 'Number of columns. 1 if not given (the command needs -columns or -rows or both).'),
            ('rows', 'Number of rows. 1 if not given (the command needs -columns or -rows or both).'),
            ('outname', 'Name of the new geometry object.'),
            ('use_thread', 'False (0) = non-threaded execution or True (1) = threaded execution')
        ]),
        'examples': [
            'panelize obj_name',

            'panel obj_name -rows 2 -columns 2 -spacing_columns 0.4 -spacing_rows 1.3 -box box_obj_name '
            '-outname panelized_name',

            'panel obj_name -columns 2 -box box_obj_name -outname panelized_name',
        ]
    }

    def execute(self, args, unnamed_args):
        """

        :param args:
        :param unnamed_args:
        :return:
        """

        name = args['name']

        # Get source object.
        try:
            obj = self.app.collection.get_by_name(str(name))
        except Exception:
            self.app.log.error("Could not retrieve object: %s" % name)
            return "fail"

        if obj is None:
            self.app.log.error("Object not found: %s" % name)
            return "fail"
        if obj.kind not in ('geometry', 'gerber', 'excellon'):
            self.app.log.error('Unsupported object kind for panelize: %s' % obj.kind)
            return 'fail'

        if 'box' in args:
            boxname = args['box']
            try:
                box = self.app.collection.get_by_name(boxname)
            except Exception:
                self.app.log.error("Could not retrieve object: %s" % boxname)
                return "fail"

            if box is None:
                self.app.log.error("Box object not found: %s" % boxname)
                return "fail"
        else:
            box = obj

        if 'columns' in args:
            columns = int(args['columns'])
        else:
            columns = 1

        if 'rows' in args:
            rows = int(args['rows'])
        else:
            rows = 1

        if 'columns' not in args and 'rows' not in args:
            self.app.log.error("ERROR: Specify either -columns or -rows. The one not specified it will assumed to be 1")
            return "fail"

        if columns < 1 or rows < 1:
            self.app.log.error("ERROR: -columns and -rows must be positive integers. Got columns=%d, rows=%d." %
                               (columns, rows))
            return "fail"

        if 'outname' in args:
            outname = args['outname']
        else:
            outname = name + '_panelized'

        if 'use_thread' in args:
            threaded = parse_bool(args['use_thread'])
            if threaded is None:
                self.app.log.error("ERROR: -use_thread expects 0/1 or True/False. Got: %s" % args['use_thread'])
                return "fail"
        else:
            threaded = False

        if 'spacing_columns' in args:
            spacing_columns = float(args['spacing_columns'])
        else:
            spacing_columns = 5

        if 'spacing_rows' in args:
            spacing_rows = float(args['spacing_rows'])
        else:
            spacing_rows = 5

        offsets = panelize_core.grid_offsets(box.bounds(), rows, columns, spacing_columns, spacing_rows)

        def abort():
            return self.app.abort_flag

        def panelize_handler():
            self.app.inform.emit("Generating panel ... Please wait.")

            def job_init_excellon(obj_fin, app_obj):
                obj_fin.multitool = True
                obj_fin.tools = panelize_core.panelize_excellon(obj.tools, offsets, abort=abort)
                obj_fin.solid_geometry = []
                # the holes keep their TO ids, so the table of the ids goes with them
                for name in ('attributes', 'file_attributes', 'to_attributes'):
                    if name in vars(obj):
                        setattr(obj_fin, name, deepcopy(vars(obj)[name]))

                for option in obj.obj_options:
                    if option != 'name':
                        try:
                            obj_fin.obj_options[option] = obj.obj_options[option]
                        except Exception as e:
                            app_obj.log.error("Failed to copy option: %s" % str(option))
                            app_obj.log.error(
                                "TclCommandPanelize.execute().panelize2.job_init_excellon() Options:--> %s" %
                                str(e))

                obj_fin.create_geometry()
                obj_fin.zeros = obj.zeros
                obj_fin.units = obj.units
                app_obj.inform.emit('%s' % _("Generating panel ... Adding the source code."))
                obj_fin.regenerate_source(name=outname)

            def job_init_geometry(obj_fin, app_obj):
                obj_fin.multigeo = obj.multigeo
                obj_fin.tools, obj_fin.solid_geometry = panelize_core.panelize_geometry(
                    obj.tools, obj.solid_geometry, obj.multigeo, offsets, abort=abort)
                obj_fin.regenerate_source(name=outname)

            def job_init_gerber(obj_fin, app_obj):
                obj_fin.tools, obj_fin.solid_geometry = panelize_core.panelize_gerber(
                    obj.tools, obj.solid_geometry, offsets, abort=abort)
                panelize_core.copy_gerber_attributes(obj_fin, obj)
                obj_fin.regenerate_source(name=outname)

            if obj.kind == 'excellon':
                return self.app.app_obj.new_object("excellon", outname, job_init_excellon, plot=False, autoselected=True)
            elif obj.kind == 'geometry':
                return self.app.app_obj.new_object("geometry", outname, job_init_geometry, plot=False, autoselected=True)
            else:
                return self.app.app_obj.new_object("gerber", outname, job_init_gerber, plot=False, autoselected=True)

        if threaded is True:
            proc = self.app.proc_container.new('%s...' % _("Working"))

            def job_thread(app_obj):
                try:
                    result = panelize_handler()
                    if result == 'fail' or result is False:
                        app_obj.collection.promises.discard(outname)
                        app_obj.inform.emit('[ERROR_NOTCL] Panelize failed: %s' % outname)
                        return
                    app_obj.inform.emit('[success] %s' % _("Done."))
                except Exception as err:
                    app_obj.log.error('TclCommandPanelize.execute.job_thread() -> %s' % str(err))
                    app_obj.collection.promises.discard(outname)
                    app_obj.inform.emit('[ERROR_NOTCL] Panelize failed: %s' % err)
                    return
                finally:
                    proc.done()

            self.app.collection.promise(outname)
            self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})
        else:
            try:
                result = panelize_handler()
                if result == 'fail' or result is False:
                    return 'fail'
                self.app.inform.emit('[success] %s' % _("Done."))
            except Exception as ee:
                self.app.log.error('TclCommandPanelize.execute() non-threaded -> %s' % str(ee))
                return 'fail'
