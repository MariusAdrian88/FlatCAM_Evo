# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# https://github.com/MariusAdrian88/FlatCAM_Evo            #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtCore, QtGui, QtWidgets
from appPlugins.ToolShell.ShellUI import TermWidget
from defaults import AppDefaults
from appCommon.Headless import install_tcl_puts
from appCommon.TclExecution import catch_tcl, completion_code

import traceback

import tkinter as tk
import tclCommands

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class FCShell(TermWidget):

    tcl_error_signal = QtCore.pyqtSignal(object, object)

    def __init__(self, app, version, *args):
        """
        Initialize the TCL Shell. A dock widget that holds the GUI interface to the FlatCAM command line.

        :param app:    When instantiated the sysShell will be actually the FlatCAMApp.App() class
        :param version:     FlatCAM version string
        :param args:        Parameters passed to the TermWidget parent class
        """
        TermWidget.__init__(self, version, *args, app=app)
        self.app = app

        self.tcl_commands_storage = {}
        self.tcl = None

        self.init_tcl()

        app_icon = QtGui.QIcon()
        app_icon.addFile(self.app.resource_location + '/app16.png', QtCore.QSize(16, 16))
        app_icon.addFile(self.app.resource_location + '/app24.png', QtCore.QSize(24, 24))
        app_icon.addFile(self.app.resource_location + '/app32.png', QtCore.QSize(32, 32))

        self.setWindowIcon(app_icon)
        self.setWindowTitle(_("FlatCAM Evo Shell"))
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.resize(*self.app.options.get(
            "global_shell_shape", app_defaults.get("global_shell_shape", AppDefaults.factory_defaults.get("global_shell_shape"))
        ))
        self._append_to_browser('in', "FlatCAM Evo %s - " % version)
        self.append_output('%s\n\n' % _("Type >help< to get started"))

        self.app.ui.shell_dock.setWidget(self)

        # first try to disconnect the signals since within the app the Tcl Tool can be reinitialized
        try:
            self.app.inform_shell[str].disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.app.inform_shell[str, bool].disconnect()
        except (TypeError, AttributeError):
            pass

        # signal for displaying messages in the shell
        self.app.inform_shell[str].connect(self.app.info_shell)
        self.app.inform_shell[str, bool].connect(self.app.info_shell)

        # used to signal that an error happened in the TCL
        self.tcl_error_signal.connect(self.display_tcl_error)

        self._browser.find_text = self.find_text
        self._edit.on_escape_key = self.on_escape_key

    def on_escape_key(self):
        self.app.plotcanvas.native.setFocus()

    def find_text(self):
        edit_cursor = self._edit.textCursor()
        txt = edit_cursor.selectedText()
        clipboard = QtWidgets.QApplication.clipboard()

        searched_txt = txt if txt != '' else clipboard.text()

        r = self._browser.find(str(searched_txt))
        if r is False:
            self._browser.moveCursor(QtGui.QTextCursor.MoveOperation.Start)
            self._browser.find(str(searched_txt))

    def init_tcl(self):
        if hasattr(self, 'tcl') and self.tcl is not None:
            # self.tcl = None
            # new object cannot be used here as it will not remember values created for next passes,
            # because tcl was executed in old instance of TCL
            pass
        else:
            self.tcl = tk.Tcl()
            self.setup_shell()

    def setup_shell(self):
        """
        Creates shell functions. Runs once at startup.

        :return: None
        """

        '''
            How to implement TCL shell commands:

            All parameters passed to command should be possible to set as None and test it afterwards.
            This is because we need to see error caused in tcl,
            if None value as default parameter is not allowed TCL will return empty error.
            Use:
                def mycommand(name=None,...):

            Test it like this:
            if name is None:

                self.raise_tcl_error('Argument name is missing.')

            When error occurred, always use raise_tcl_error, never return "some text" on error,
            otherwise we will miss it and processing will silently continue.
            Method raise_tcl_error  pass error into TCL interpreter, then raise python exception,
            which is caught in exec_command and displayed in TCL shell console with red background.
            Error in console is displayed  with TCL  trace.

            This behavior works only within main thread,
            errors with promissed tasks can be catched and detected only with log.
            TODO: this problem have to be addressed somehow, maybe rewrite promissing to be blocking somehow for
            TCL shell.

            Kamil's comment: I will rewrite existing TCL commands from time to time to follow this rules.

        '''

        # Import/overwrite tcl commands as objects of TclCommand descendants
        # This modifies the variable 'self.tcl_commands_storage'.
        tclCommands.register_all_commands(self.app, self.tcl_commands_storage)

        # Add commands to the tcl interpreter
        for cmd in self.tcl_commands_storage:
            self.tcl.createcommand(cmd, self.tcl_commands_storage[cmd]['fcn'])

        self._tcl_output = []
        self._tcl_output_size = 0
        self._tcl_output_channel = 'stdout'
        install_tcl_puts(self.tcl, self.write_tcl_output)

    def write_tcl_output(self, channel, text):
        if channel != self._tcl_output_channel:
            self.flush_tcl_output()
        self._tcl_output_channel = channel
        self._tcl_output.append(text)
        self._tcl_output_size += len(text)
        if self._tcl_output_size >= 8192 or getattr(self.app, 'script_state', '') != 'running':
            self.flush_tcl_output()
        return ''

    def flush_tcl_output(self):
        if not self._tcl_output:
            return
        text = ''.join(self._tcl_output)
        self._tcl_output.clear()
        self._tcl_output_size = 0
        if self._tcl_output_channel == 'stderr':
            self.append_error(text)
        else:
            self.append_output(text)

    def is_command_complete(self, text):

        # def skipQuotes(txt):
        #     quote = txt[0]
        #     text_val = txt[1:]
        #     endIndex = str(text_val).index(quote)
        #     return text[endIndex:]

        # I'm disabling this because I need to be able to load paths that have spaces by
        # enclosing them in quotes --- Marius Stanciu
        # while text:
        #     if text[0] in ('"', "'"):
        #         try:
        #             text = skipQuotes(text)
        #         except ValueError:
        #             return False
        #     text = text[1:]

        return self.tcl.getboolean(self.tcl.call('info', 'complete', text))

    def child_exec_command(self, text):
        self.exec_command(text)

    def exec_command(self, text, no_echo=False):
        """
        Handles input from the shell. See FlatCAMApp.setup_shell for shell commands.
        Also handles execution in separated threads

        :param text:        FlatCAM TclCommand with parameters
        :param no_echo:     If True it will not try to print to the Shell because most likely the shell is hidden and it
                            will create crashes of the _Expandable_Edit widget
        :return:            output if there was any
        """
        return self.exec_command_test(text, False, no_echo=no_echo)

    def exec_command_test(self, text, reraise=True, no_echo=False):
        """
        Same as exec_command(...) with additional control over  exceptions.
        Handles input from the shell. See FlatCAMApp.setup_shell for shell commands.

        :param text: Input command
        :param reraise: Re-raise TclError exceptions in Python (mostly for unittests).
        :param no_echo: If True it will not try to print to the Shell because most likely the shell is hidden and it
        will create crashes of the _Expandable_Edit widget
        :return: Output from the command
        """

        tcl_command_string = str(text)

        busy_error = None
        if getattr(self.app, 'script_state', '') == 'running':
            busy_error = 'A script is already running.'
        pending = getattr(self.app, 'script_pending_work', None)
        if pending and any(not state['finished'] for state in pending):
            busy_error = 'Timed-out work is still running; changes are not rolled back.'
        if busy_error:
            if reraise:
                raise tk.TclError(busy_error)
            if no_echo is False:
                self.append_error(busy_error + '\n')
            return 'fail'

        self.app.script_state = 'running'
        self.app.script_cancelled = False
        result_var = '::flatcam_shell_result'
        options_var = '::flatcam_shell_options'
        details = ''
        try:
            if no_echo is False:
                self.open_processing()  # Disables input box.

            if not self.tcl.getboolean(self.tcl.call('info', 'complete', tcl_command_string)):
                raise tk.TclError('Incomplete Tcl script; no commands were executed.')
            code = catch_tcl(self.app, self.tcl, tcl_command_string, result_var, options_var)
            result = self.tcl.eval('set ::flatcam_shell_result')
            options = self.tcl.getvar(options_var)
            code = completion_code(self.tcl, code, options)
            if self.app.script_cancelled:
                raise tk.TclError('Script cancelled; completed changes are not rolled back.')
            if code != 0:
                if self.tcl.getboolean(self.tcl.call('dict', 'exists', options, '-errorinfo')):
                    details = str(self.tcl.call('dict', 'get', options, '-errorinfo'))
                raise tk.TclError(result or 'Unexpected Tcl completion code: %s' % code)
            self.flush_tcl_output()
            if result not in ('', 'None') and no_echo is False:
                self.append_output(result + '\n')
            self.app.script_state = 'completed'

        except tk.TclError as e:
            pending = getattr(self.app, 'script_pending_work', None)
            self.app.script_state = ('timed_out_work' if pending and any(not s['finished'] for s in pending)
                                     else 'cancelled' if self.app.script_cancelled else 'failed')
            self.flush_tcl_output()
            # This will display more precise answer if something in TCL shell fails
            result = details or str(e)
            self.app.log.error("Exception on Tcl Command execution: %s" % (result + '\n'))
            if no_echo is False:
                self.append_error('ERROR Report: ' + result + '\n')
            # Show error in console and just return or in test raise exception
            if reraise:
                raise e
        finally:
            self.tcl.call('unset', '-nocomplain', result_var, options_var)
            self.flush_tcl_output()
            if self.app.script_state == 'running':
                self.app.script_state = 'failed'
            if no_echo is False:
                self.close_processing()
            pass
        return result

    def raise_tcl_unknown_error(self, unknownException):
        """
        Raise exception if is different type than TclErrorException
        this is here mainly to show unknown errors inside TCL shell console.

        :param unknownException:
        :return:
        """

        if not isinstance(unknownException, self.TclErrorException):
            self.raise_tcl_error("Unknown error: %s" % str(unknownException))
        else:
            raise unknownException

    def display_tcl_error(self, error: str | Exception | None, error_info=None):
        """
        Escape bracket [ with '\' otherwise there is error
        "ERROR: missing close-bracket" instead of real error

        :param error: it may be text  or exception
        :param error_info: Some informations about the error
        :return: None
        """
        if error is None:
            error = "no error provided"

        if isinstance(error, Exception):
            exc_type, exc_value, exc_traceback = error_info or (type(error), error, error.__traceback__)
            if not isinstance(error, self.TclErrorException):
                show_trace = 1
            else:
                app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
                show_trace = int(self.app.options.get(
                    'global_verbose_error_level',
                    app_defaults.get('global_verbose_error_level', AppDefaults.factory_defaults.get('global_verbose_error_level'))
                ))

            if show_trace > 0:
                trc = traceback.format_list(traceback.extract_tb(exc_traceback))
                trc_formated = []
                for a in reversed(trc):
                    trc_formated.append(a.replace("    ", " > ").replace("\n", ""))
                text = "%s\nPython traceback: %s\n%s" % (exc_value, exc_type, "\n".join(trc_formated))
            else:
                text = "%s" % error
        else:
            text = error

        self.tcl.call('error', str(text))

    def raise_tcl_error(self, text):
        """
        This method  pass exception from python into TCL as error, so we get stacktrace and reason

        :param text: text of error
        :return: raise exception
        """

        raise self.TclErrorException(text)

    class TclErrorException(Exception):
        """
        this exception is defined here, to be able catch it if we successfully handle all errors from shell command
        """
        pass

    # """
    # Code below is unsused. Saved for later.
    # """

    # parts = re.findall(r'([\w\\:\.]+|".*?")+', text)
    # parts = [p.replace('\n', '').replace('"', '') for p in parts]
    # self.log.debug(parts)
    # try:
    #     if parts[0] not in commands:
    #         self.shell.append_error("Unknown command\n")
    #         return
    #
    #     #import inspect
    #     #inspect.getargspec(someMethod)
    #     if (type(commands[parts[0]]["params"]) is not list and len(parts)-1 != commands[parts[0]]["params"]) or \
    #             (type(commands[parts[0]]["params"]) is list and len(parts)-1 not in commands[parts[0]]["params"]):
    #         self.shell.append_error(
    #             "Command %s takes %d arguments. %d given.\n" %
    #             (parts[0], commands[parts[0]]["params"], len(parts)-1)
    #         )
    #         return
    #
    #     cmdfcn = commands[parts[0]]["fcn"]
    #     cmdconv = commands[parts[0]]["converters"]
    #     if len(parts) - 1 > 0:
    #         retval = cmdfcn(*[cmdconv[i](parts[i + 1]) for i in range(len(parts)-1)])
    #     else:
    #         retval = cmdfcn()
    #     retfcn = commands[parts[0]]["retfcn"]
    #     if retval and retfcn(retval):
    #         self.shell.append_output(retfcn(retval) + "\n")
    #
    # except Exception as e:
    #     #self.shell.append_error(''.join(traceback.format_exc()))
    #     #self.shell.append_error("?\n")
    #     self.shell.append_error(str(e) + "\n")
