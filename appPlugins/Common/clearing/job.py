# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The shared clearing job: run a resolved JobSpec headless or threaded, publish the GeometryObject, and report a
failure. NCC and Paint give it their driver (the generator object of the plugin) and a JobProfile.

The job reads only the JobSpec: the driver it runs sees a snapshot of the application (the options of the spec, the
live services) and the tool of its kernels sees the same snapshot. The tool-specific publishing stays in the driver
(``prepare_job`` and ``generate_output``).
"""

import traceback
from copy import copy
from dataclasses import dataclass
from types import FunctionType, MethodType
from typing import Literal

import builtins
import gettext

from PyQt6 import QtCore, QtWidgets

import appTranslation as fcTranslate
from camlib import grace

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def on_gui_thread():
    app = QtWidgets.QApplication.instance()
    return app is None or QtCore.QThread.currentThread() is app.thread()


class SnapshotDefaults:
    """The defaults of a snapshot: every lookup goes to the frozen options; the services go to the live ones."""

    def __init__(self, live, options):
        object.__setattr__(self, '_live', live)
        object.__setattr__(self, '_options', options)

    def get(self, key, default=None):
        return self._options.get(key, default)

    @property
    def factory_defaults(self):
        return self._options

    def __getattr__(self, name):
        return getattr(self._live, name)


class AppSnapshot:
    """The application as a job sees it: the options and the units of the spec; the live services and the abort flag."""
    _own = ('_app', 'options', 'defaults', 'units', 'app_units')

    def __init__(self, app, spec):
        object.__setattr__(self, '_app', app)
        object.__setattr__(self, 'options', spec.options)
        object.__setattr__(self, 'defaults', SnapshotDefaults(getattr(app, 'defaults', None), spec.options))
        object.__setattr__(self, 'units', spec.units)
        object.__setattr__(self, 'app_units', spec.app_units)

    def __getattr__(self, name):
        return getattr(self._app, name)

    def __setattr__(self, name, value):
        # a state flag of the application (poly_not_cleared) is not an option: it goes to the live application
        if name in self._own:
            object.__setattr__(self, name, value)
        else:
            setattr(self._app, name, value)


class SnapshotSelf:
    """
    A view of a tool whose own methods run with the snapshot as ``app``: the camlib kernels read the options
    (global_tolerance, the clearing edge and error, the circle steps) from ``self.app``.
    """

    def __init__(self, base, app):
        object.__setattr__(self, '_base', base)
        object.__setattr__(self, 'app', app)

    def __getattr__(self, name):
        base = self._base
        member = getattr(type(base), name, None)
        if isinstance(member, FunctionType):
            return MethodType(member, self)
        return getattr(base, name)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


@dataclass(frozen=True)
class JobProfile:
    """
    How a tool publishes and reports today. Stage 3.5 unifies these; until then each tool keeps its behavior.
    """
    name: str
    factory: Literal['tool', 'app'] = 'tool'            # new_object of the controller, or of the application
    plot_to_factory: bool = False                       # pass the plot flag to new_object
    link_source: bool = True                            # source=, autoselected=False
    done_message: bool = True
    return_result: bool = False                         # return the object, else None on success
    exception_inform: bool = False                      # '[ERROR] Failed.' at once
    exception_log: Literal['debug', 'error'] = 'debug'
    exception_trace: Literal['none', 'stack', 'format'] = 'none'
    failure_tail: bool = False                          # remove the promise, report the cancel / the failure
    process_events: Literal['always', 'gui'] = 'always'
    clear_temp_shapes: bool = False                     # the progressive plotting shapes
    user_errors: tuple = ()


NCC_GUI = JobProfile('ncc_gui', exception_inform=True, clear_temp_shapes=True)
NCC_TCL = JobProfile('ncc_tcl', factory='app', plot_to_factory=True, link_source=False, done_message=False,
                     exception_log='error', exception_trace='stack')
PAINT = JobProfile('paint', plot_to_factory=True, return_result=True,
                   exception_log='error', exception_trace='format', failure_tail=True, process_events='gui')


class ClearingJob:
    """
    Plain class used by composition. ``driver`` is the NCC or Paint generator; it provides:

    - ``prepare_job(spec) -> (ok, prepared)``: the steps inside the job before the output exists (Paint selection),
    - ``generate_output(spec, prepared, output, app_obj)``: fill the output object; 'fail' on failure,
    - ``parent_tool.new_object`` and the live services (``app``).
    """

    def __init__(self, driver, profile: JobProfile):
        self.driver = driver
        self.profile = profile

    def snapshot_driver(self, spec):
        """A copy of the driver that sees only the spec: no widgets, the snapshot app and the snapshot tool."""
        driver = self.driver
        runner = copy(driver)
        snapshot = AppSnapshot(driver.app, spec)
        runner.app = snapshot
        runner.ui = None
        if driver.parent_tool is not None:
            runner.parent_tool = SnapshotSelf(driver.parent_tool, snapshot)
        if spec.circle_steps is not None:
            runner.circle_steps = spec.circle_steps
        if spec.circle_kind is not None:
            runner.circle_kind = spec.circle_kind
        runner.decimals = spec.decimals
        return runner

    def run(self, spec, run_threaded=True):
        """Run the job; threaded: queue it on the worker. Returns 'fail' (not threaded) or the result of the profile."""
        profile = self.profile
        driver = self.driver
        app = driver.app
        runner = self.snapshot_driver(spec)
        name = spec.outname

        proc = None
        if run_threaded:
            proc = app.proc_container.new('%s...' % _("Working"))
        else:
            app.proc_container.view.set_busy('%s...' % _("Working"))
            if profile.process_events == 'always' or on_gui_thread():
                QtWidgets.QApplication.processEvents()

        def job_thread(app_instance, proc=None):
            ret = None
            runner._failure_reported = False
            try:
                ok, prepared = runner.prepare_job(spec)
                if not ok:
                    ret = 'fail'
                else:
                    factory = driver.parent_tool if profile.factory == 'tool' else app.app_obj
                    options = {}
                    if profile.plot_to_factory:
                        options['plot'] = spec.plot
                    if profile.link_source:
                        options.update(source=spec.origin, autoselected=False)
                    ret = factory.new_object(
                        'geometry', name, lambda out, application: runner.generate_output(
                            spec, prepared, out, application), **options)
                    if ret == 'fail' or ret is False:
                        ret = 'fail'
            except grace:
                app_instance.log.debug("%s -> Graceful exit." % profile.name)
                ret = 'fail'
            except profile.user_errors as err:
                app.inform.emit('[ERROR_NOTCL] %s' % str(err))
                runner._failure_reported = True
                ret = 'fail'
            except Exception as err:
                message = "%s -> Exception: %s" % (profile.name, str(err))
                if profile.exception_log == 'error':
                    app_instance.log.error(message)
                else:
                    app_instance.log.debug(message)
                if profile.exception_trace == 'stack':
                    traceback.print_stack()
                elif profile.exception_trace == 'format':
                    app_instance.log.debug(traceback.format_exc())
                if profile.exception_inform:
                    app.inform.emit('[ERROR] %s' % _("Failed."))
                ret = 'fail'
            finally:
                if profile.clear_temp_shapes and spec.prog_plot:
                    runner.temp_shapes.clear(update=True)
                if proc is not None:
                    proc.done()
                else:
                    app_instance.proc_container.view.set_idle()

            if profile.failure_tail:
                if ret == 'fail' or ret is None:
                    # The promised name will never come: do not leave the app waiting for it.
                    app.collection.remove_promise(name)
                    if app.abort_flag:
                        app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                    elif not runner._failure_reported:
                        app.inform.emit('[ERROR] %s' % _("Failed."))
                    return 'fail'
            elif ret == 'fail':
                return 'fail'

            if profile.done_message:
                app.inform.emit('[success] %s' % _("Done."))
            return ret if profile.return_result else None

        if run_threaded:
            app.collection.promise(name)
            app.worker_task.emit({'fcn': job_thread, 'params': [app, proc]})
        else:
            return job_thread(app, proc)
