from appHandlers.appHistory import history_of
import sys
import re
import math
import ast

import abc
import contextlib
import collections
from PyQt6 import QtCore

import gettext
import appTranslation as fcTranslate
import builtins
from appCommon.StartDir import resolve_user_path

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class TclCommandFailure(str):
    """Legacy Python text result that must be translated to an error at the Tcl boundary."""


class TclCommand(object):

    # appMain class is held here
    app = None

    # Logger
    log = None

    # List of all command aliases, to be able use  old names
    # for backward compatibility (add_poly, add_polygon)
    aliases = []

    # Dictionary of types from Tcl command, needs to be ordered
    # OrderedDict should be like collections.OrderedDict([(key,value),(key2,value2)])
    arg_names = collections.OrderedDict([
        ('name', str)
    ])

    # dictionary of types from Tcl command, needs to be ordered.
    # This  is  for options  like -optionname value.
    # OrderedDict should be like collections.OrderedDict([(key,value),(key2,value2)])
    option_types = collections.OrderedDict()

    # Options allowed without a value; execute receives None for command-specific flag/default handling.
    # All other options require an explicit value, including boolean options.
    valueless_options = frozenset()

    # List of mandatory options for current Tcl command: required = {'name','outname'}
    required = ['name']

    # Names of the arguments / options that hold a file or folder name. A relative name is resolved against the folder
    # FlatCAM was started from: the application itself runs from its own folder (see appCommon/StartDir.py).
    path_args = ('filename', 'path', 'dir')

    # Structured help for current command, args needs to be ordered
    # OrderedDict should be like collections.OrderedDict([(key,value),(key2,value2)])
    help = {
        'main': "undefined help.",
        'args': collections.OrderedDict([
            ('argumentname', 'undefined help.'),
            ('optionname', 'undefined help.')
        ]),
        'examples': []
    }

    # Original incoming arguments into command
    original_args = None
    returns_value = False

    def __init__(self, app):
        self.app = app

        if self.app is None:
            raise TypeError('Expected app to be appMain instance.')

        # if not isinstance(self.app, appMain.App):
        #     raise TypeError('Expected appMain, got %s.' % type(app))

        self.log = self.app.log
        self.error_info = None
        self.error = None

    def _app_option(self, key, fallback=None):
        defaults = getattr(self.app, "defaults", None)
        if defaults is None:
            return self.app.options.get(key, fallback)

        factory_defaults = getattr(defaults, "factory_defaults", {})
        return self.app.options.get(key, defaults.get(key, factory_defaults.get(key, fallback)))

    def raise_tcl_error(self, text):
        """
        This method pass exception from python into TCL as error
        so we get stacktrace and reason.

        This is only redirect to self.app.raise_tcl_error

        :param text: text of error
        :return: none
        """

        self.app.shell.raise_tcl_error(text)
        # A shell adapter must never let validation fall through if its error reporter returns.
        raise ValueError(text)

    @staticmethod
    def parse_bool(value):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ('1', 'true', 'yes', 'on', 't', 'y'):
            return True
        if text in ('0', 'false', 'no', 'off', 'f', 'n'):
            return False
        raise ValueError('Expected a boolean (0/1, true/false, yes/no, on/off), got: %s' % value)

    @staticmethod
    def parse_coords(value):
        points = ast.literal_eval(str(value))

        def validate(coords):
            if not isinstance(coords, (tuple, list)):
                raise ValueError('Expected numeric coordinates')
            for item in coords:
                if isinstance(item, (tuple, list)):
                    validate(item)
                elif isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                    raise ValueError('Expected finite numeric coordinates')

        validate(points)
        return points

    def check_result(self, result):
        # None is a successful return for many existing handlers; ordinary text is also a valid result.
        if isinstance(result, TclCommandFailure):
            self.raise_tcl_error(str(result))
        if result is False or (isinstance(result, str) and result == 'fail'):
            self.raise_tcl_error('Command failed: %s' % self.aliases[0])
        return result

    def get_current_command(self):
        """
        Get current command, we are not able to get it from TCL we have to reconstruct it.

        :return: current command
        """
        command_string = [self.aliases[0]]

        if self.original_args is not None:
            for arg in self.original_args:
                command_string.append(arg)

        return " ".join(command_string)

    def get_decorated_help(self):
        """
        Decorate help for TCL console output.

        :return: decorated help from structure
        """

        def get_decorated_command(alias_name):

            command_string = []

            for arg_key, arg_type in list(self.help['args'].items()):
                command_string.append(get_decorated_argument(arg_key, arg_type, True))

            return "> " + alias_name + " " + " ".join(command_string)

        def get_decorated_argument(help_key, help_text, in_command=False):
            """

            :param help_key: Name of the argument.
            :param help_text:
            :param in_command:
            :return:
            """
            option_symbol = ''

            if help_key in self.arg_names:
                arg_type = self.arg_names[help_key]
                type_name = str(arg_type.__name__)
                # in_command_name = help_key + "<" + type_name + ">"
                in_command_name = help_key

            elif help_key in self.option_types:
                option_symbol = '-'
                arg_type = self.option_types[help_key]
                type_name = str(arg_type.__name__)
                in_command_name = option_symbol + help_key + " <" + type_name + ">"
                if help_key in self.valueless_options and help_key not in self.required:
                    in_command_name = option_symbol + help_key + " [<" + type_name + ">]"

            else:
                option_symbol = ''
                type_name = '?'
                in_command_name = option_symbol + help_key + " <" + type_name + ">"

            if in_command:
                if help_key in self.required:
                    return in_command_name
                else:
                    return '[' + in_command_name + "]"
            else:
                if help_key in self.option_types:
                    if help_key in self.required:
                        return "\t" + in_command_name + ": " + help_text
                    return "\t[" + in_command_name + ": " + help_text + "]"
                if help_key in self.required:
                    return "\t" + option_symbol + help_key + " <" + type_name + ">: " + help_text
                else:
                    return "\t[" + option_symbol + help_key + " <" + type_name + ">: " + help_text + "]"

        def get_decorated_example(example_item):
            return "> " + example_item

        help_string = [self.help['main']]
        for alias in self.aliases:
            help_string.append(get_decorated_command(alias))

        if any(key in self.valueless_options and key not in self.required
               for key in self.help['args'] if key in self.option_types):
            help_string.append("[-option [<type>]]: the option may be omitted; when present, "
                               "its value may also be omitted (bare option). See defaults below.")

        for key, value in list(self.help['args'].items()):
            help_string.append(get_decorated_argument(key, value))

        # timeout is unique for signaled commands (this is not best oop practice, but much easier for now)
        if isinstance(self, TclCommandSignaled):
            help_string.append("\t[-timeout <int>: Max wait for job timeout before error.]")

        for example in self.help['examples']:
            help_string.append(get_decorated_example(example))

        return "\n".join(help_string)

    @staticmethod
    def parse_arguments(args):
        """
        Pre-processes arguments to detect '-keyword value' pairs into dictionary
        and standalone parameters into list.

        :param args: arguments from tcl to parse
        :return: arguments, options
        """

        options = {}
        arguments = []
        n = len(args)

        option_name = None

        for i in range(n):
            match = re.search(r'^-([a-zA-Z].*)', args[i])
            if match:
                # assert option_name is None
                if option_name is not None:
                    options[option_name] = None

                option_name = match.group(1)
                continue

            init_arg = args[i]
            if option_name is None:
                arguments.append(init_arg)
            else:
                options[option_name] = init_arg
                option_name = None

        if option_name is not None:
            options[option_name] = None

        return arguments, options

    def check_args(self, args):
        """
        Check arguments and options for right types

        :param args: arguments from tcl to check
        :return: named_args, unnamed_args
        """

        arguments, options = self.parse_arguments(args)
        named_args = {}
        unnamed_args = []

        # check arguments
        idx = 0
        arg_names_items = list(self.arg_names.items())
        for argument in arguments:
            if len(self.arg_names) > idx:
                key, arg_type = arg_names_items[idx]

                try:
                    named_args[key] = arg_type(argument)
                    if arg_type is float and not math.isfinite(named_args[key]):
                        raise ValueError('Expected a finite number')
                except Exception as e:
                    self.raise_tcl_error("Cannot cast named argument '%s' to type %s  with exception '%s'."
                                         % (key, arg_type, str(e)))
            else:
                unnamed_args.append(argument)
            idx += 1

        # check options
        for key in options:
            if key not in self.option_types and not (key == 'timeout' and isinstance(self, TclCommandSignaled)):
                self.raise_tcl_error('Unknown parameter: %s' % key)
            arg_type = int if key == 'timeout' else self.option_types[key]
            try:
                if options[key] is None:
                    if key not in self.valueless_options:
                        raise ValueError('Missing option value')
                    named_args[key] = None
                else:
                    named_args[key] = arg_type(options[key])
                    if arg_type is float and not math.isfinite(named_args[key]):
                        raise ValueError('Expected a finite number')
                if key == 'timeout' and not 0 < named_args[key] <= 2147483647:
                    raise ValueError('Timeout must be between 1 and 2147483647 milliseconds')
            except Exception as e:
                self.raise_tcl_error("Cannot cast argument '-%s' to type '%s' with exception '%s'."
                                     % (key, arg_type, str(e)))

        # relative file and folder names are relative to where FlatCAM was started from
        for key in self.path_args:
            if named_args.get(key):
                named_args[key] = resolve_user_path(named_args[key])

        # check required arguments
        for key in self.required:
            if key not in named_args or named_args[key] is None:
                self.raise_tcl_error("Missing required argument '%s'." % key)

        return named_args, unnamed_args

    def _collection_objects(self):
        """The objects of all open projects, as they are now. A switch of the active project creates no object."""
        collections = [ctx.collection for ctx in getattr(getattr(self.app, 'projects', None), 'contexts', None) or []]
        active = getattr(self.app, 'collection', None)
        if all(active is not collection for collection in collections):
            collections.append(active)
        objects = []
        for collection in collections:
            try:
                objects.extend(collection.get_list())
            except (AttributeError, TypeError):
                pass
        return objects

    def _report_new_objects(self, result, before):
        """
        Give the command result its final form: a command that returned nothing returns the names of the objects it
        created, so a script can chain them: set g [paint ...]. The new objects are found by identity, not by name.

        :param result:  the checked result of the command
        :param before:  the objects of the collection before the command; this list keeps them alive, so an id()
                        cannot be reused by a new object
        :return:        the result; the name of the new object; or a tuple of names (a Tcl list)
        """
        if self.returns_value:
            return result
        known = {id(obj) for obj in before}
        new_objects = [obj for obj in self._collection_objects() if id(obj) not in known]
        for obj in new_objects:
            requested = getattr(obj, 'requested_name', None)
            if requested:
                # the collection renamed it: the next commands of a script that use the name get the old object, so
                # a script run again in the same session would write the G-code of its first run
                self.app.inform.emit('[WARNING_NOTCL] %s' % (
                    _("An object named '%s' already exists. The new object is named '%s'; "
                      "the commands that use '%s' get the old one.") % (requested, obj.obj_options['name'], requested)))
        if result is not None:
            return result
        names = [obj.obj_options['name'] for obj in new_objects]
        for name in names:
            if name.lower() in ('fail', 'failed'):
                self.app.inform.emit('[WARNING_NOTCL] %s' % (
                    _("Object name '%s' looks like a failure result; rename it.") % name))
        if not names:
            return None
        return names[0] if len(names) == 1 else tuple(names)

    def _history_suspended(self):
        """Context manager: the app Undo history records nothing while a Tcl command runs."""
        history = history_of(self.app)
        return history.suspended() if history is not None else contextlib.nullcontext()

    def raise_tcl_unknown_error(self, unknown_exception):
        """
        raise Exception if is different type  than TclErrorException
        this is here mainly to show unknown errors inside TCL shell console

        :param unknown_exception:
        :return:
        """

        raise unknown_exception

    def execute_wrapper(self, *args):
        """
        Command which is called by tcl console when current commands aliases are hit.
        Main catch(except) is implemented here.
        This method should be reimplemented only when initial checking sequence differs

        :param args: arguments passed from tcl command console
        :return: None, output text or exception
        """

        # self.worker_task.emit({'fcn': self.exec_command_test, 'params': [text, False]})
        try:
            with self._history_suspended():
                self.log.debug("TCL command '%s' executed." % str(type(self).__name__))
                self.original_args = args
                args, unnamed_args = self.check_args(args)
                before = self._collection_objects()
                result = self.execute(args, unnamed_args)
                if self.returns_value:
                    return result
                return self._report_new_objects(self.check_result(result), before)
        except Exception as unknown:
            error_info = sys.exc_info()
            self.log.error("TCL command '%s' failed. Error text: %s" % (str(self), str(unknown)))
            self.app.shell.display_tcl_error(unknown, error_info)
            self.raise_tcl_unknown_error(unknown)

    @abc.abstractmethod
    def execute(self, args, unnamed_args):
        """
        Direct execute of command, this method should be implemented in each descendant.
        No main catch should be implemented here.

        :param args: array of known named arguments and options
        :param unnamed_args: array of other values which were passed into command
            without -somename and  we do not have them in known arg_names
        :return: None, output text or exception
        """

        return "Not Implemented Error -> Incorrect call."


class TclCommandSignaled(TclCommand):
    """
        !!! I left it here only  for demonstration !!!
        Go to TclCommandCncjob and  into class definition put
            class TclCommandCncjob(TclCommandSignaled):
        also change
            obj.generatecncjob(use_thread = False, **args)
        to
            obj.generatecncjob(use_thread = True, **args)


        This class is  child of  TclCommand and is used for commands  which create  new objects
        it handles  all necessary stuff about blocking and passing exceptions
    """

    @abc.abstractmethod
    def execute(self, args, unnamed_args):
        return "Not Implemented Error -> Incorrect call."

    output = None

    def execute_wrapper(self, *args):
        """
        Command which is called by tcl console when current commands aliases are hit.
        Main catch(except) is implemented here.
        This method should be reimplemented only when initial checking sequence differs

        :param args: arguments passed from tcl command console
        :return: None, output text or exception
        """

        # Each invocation owns its result and completion token, including work that outlives a timeout.
        state = {'output': None, 'error': None, 'error_info': None, 'finished': False}
        self.output = self.error = self.error_info = None
        try:
            with self._history_suspended():
                self.log.debug("TCL command '%s' executed." % str(type(self).__name__))
                self.original_args = args
                args, unnamed_args = self.check_args(args)
                passed_timeout = args.pop('timeout', self._app_option('global_background_timeout'))
                if passed_timeout is not None:
                    passed_timeout = int(passed_timeout)
                    if not 0 < passed_timeout <= 2147483647:
                        self.raise_tcl_error('Timeout must be between 1 and 2147483647 milliseconds')

                # set detail for processing, it will be there until next open or close
                self.app.shell.open_processing(self.get_current_command())
                before = self._collection_objects()

                loop = QtCore.QEventLoop()
                timer = QtCore.QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(loop.quit)

                def handle_finished(invocation):
                    if invocation is state:
                        loop.quit()

                def execute_call():
                    try:
                        state['output'] = self.check_result(self.execute(args, unnamed_args))
                    except Exception as error:
                        state['error'] = error
                        state['error_info'] = sys.exc_info()
                    finally:
                        state['finished'] = True
                        self.app.shell_command_finished.emit(state)

                self.app.shell_command_finished.connect(handle_finished)
                try:
                    if passed_timeout is not None:
                        timer.start(passed_timeout)
                    self.app.worker_task.emit({'fcn': execute_call, 'params': []})
                    if not state['finished']:
                        loop.exec()
                    if not state['finished']:
                        pending = getattr(self.app, 'script_pending_work', None)
                        if pending is None:
                            pending = self.app.script_pending_work = []
                        pending.append(state)
                        self.raise_tcl_error('Operation timed out. Queued or running work has not been cancelled. '
                                             'Consider increasing -timeout <milliseconds>.')
                    self.error_info = state['error_info']
                    self.error = state['error']
                    if self.error is not None:
                        raise self.error
                    # objects made in the worker join the collection by a queued signal, sent before the finished
                    # one; the state can be set before the loop ran, so deliver the queued calls now
                    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.MetaCall.value)
                    self.output = state['output']
                    if not self.returns_value:
                        self.output = self._report_new_objects(self.output, before)
                    return self.output
                finally:
                    timer.stop()
                    self.app.shell_command_finished.disconnect(handle_finished)
                    timer.timeout.disconnect(loop.quit)

        except Exception as unknown:
            # if error happens inside thread execution, then pass correct error_info to display
            if self.error_info is not None:
                error_info = self.error_info
            else:
                error_info = sys.exc_info()
            self.log.error("TCL command '%s' failed." % str(self))
            self.app.shell.display_tcl_error(unknown, error_info)
            self.raise_tcl_unknown_error(unknown)
