"""Tcl 8.6/9.0 interruption without calling tkinter from a raw ctypes callback."""

import ctypes
import ctypes.util
import _ctypes
import _tkinter
import os
import re
import sys
import threading
import tkinter as tk

from PyQt6 import QtCore


def _windows_tcl():
    """Use the single loaded Tcl DLL; never search PATH or load another copy.

    Enumerating process modules also works before Python 3.14 and in frozen builds.
    Never search PATH, load a guessed Tcl, or infer a tkapp memory layout.
    """
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.K32EnumProcessModules.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                           ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    kernel.K32EnumProcessModules.restype = ctypes.c_int
    kernel.K32GetModuleFileNameExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_wchar_p, ctypes.c_ulong]
    kernel.K32GetModuleFileNameExW.restype = ctypes.c_ulong
    process = kernel.GetCurrentProcess()
    size = 256
    while True:
        modules = (ctypes.c_void_p * size)()
        needed = ctypes.c_ulong()
        if not kernel.K32EnumProcessModules(process, modules, ctypes.sizeof(modules), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        if needed.value <= ctypes.sizeof(modules):
            break
        size = needed.value // ctypes.sizeof(ctypes.c_void_p) + 1
    paths = {}
    for module in modules[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        path = ctypes.create_unicode_buffer(32768)
        length = kernel.K32GetModuleFileNameExW(process, module, path, len(path))
        if not length or length >= len(path):
            raise RuntimeError('cannot identify a loaded process module')
        name = os.path.basename(path.value).lower()
        # Tk 9.0's Windows DLL is named tcl9tk90.dll, not a Tcl core.
        if re.fullmatch(r'tcl\d+.*\.dll', name) and name != 'tcl9tk90.dll':
            paths[path.value] = module
    if len(paths) != 1 or os.path.basename(next(iter(paths))).lower() not in ('tcl86t.dll', 'tcl90.dll'):
        raise RuntimeError('loaded Tcl core is missing, unsupported or ambiguous')
    path, module = next(iter(paths.items()))
    return ctypes.CDLL(path, handle=module)


def _symbol_owner(function):
    """dladdr's public Dl_info layout, using dlsym addresses (not PLT pointers).

    Linux: man7.org/linux/man-pages/man3/dladdr.3.html
    macOS: developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/dladdr.3.html
    No interpreter memory is inspected or passed to a candidate runtime.
    """
    class DlInfo(ctypes.Structure):
        _fields_ = [('fname', ctypes.c_char_p), ('base', ctypes.c_void_p),
                    ('sname', ctypes.c_char_p), ('saddr', ctypes.c_void_p)]

    dladdr = ctypes.CDLL(None).dladdr
    dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(DlInfo)]
    dladdr.restype = ctypes.c_int
    info = DlInfo()
    if not dladdr(ctypes.cast(function, ctypes.c_void_p), ctypes.byref(info)) or not info.fname:
        raise RuntimeError('cannot identify Tcl symbol owner')
    return os.path.realpath(os.fsdecode(info.fname))


def _posix_tcl():
    """Linux .so and macOS dylib/framework discovery; native use is UNVERIFIED.

    Requires Python 3.14's ctypes.util.dllist and the platform RTLD_NOLOAD flag.
    Older Python, static Tcl, stubs-only builds and unresolved identity fall back.
    dlopen(_tkinter) searches its dependencies with dlsym. Require that its
    Tcl_CreateInterp and every bound Tcl symbol belong to the sole inventoried
    runtime. A matching version alone is NOT ownership evidence.
    RTLD_NOLOAD prevents loading a second installation, even if paths changed.
    """
    inventory = getattr(ctypes.util, 'dllist', None)
    noload = getattr(os, 'RTLD_NOLOAD', None)
    if inventory is None or noload is None:
        raise RuntimeError('POSIX Tcl discovery requires ctypes.util.dllist (Python 3.14+) and RTLD_NOLOAD')
    paths = set()
    for path in inventory():
        if re.fullmatch(r'libtcl[0-9.]*\.so(?:\.[0-9.]+)?|libtcl[0-9.]*\.dylib',
                        os.path.basename(path)) or '/Tcl.framework/' in path and path.endswith('/Tcl'):
            paths.add(os.path.realpath(path))
    if len(paths) != 1:
        raise RuntimeError('loaded Tcl runtime is missing or ambiguous')
    path = paths.pop()
    extension = getattr(_tkinter, '__file__', None)
    if not extension or not os.path.isabs(extension):
        raise RuntimeError('cannot identify the loaded _tkinter extension')
    dll = ctypes.CDLL(path, mode=noload)
    try:
        tkinter_dll = ctypes.CDLL(extension, mode=noload)
        try:
            if _symbol_owner(tkinter_dll.Tcl_CreateInterp) != path or \
                    _symbol_owner(dll.Tcl_CreateInterp) != path:
                raise RuntimeError('Tcl runtime owner does not match _tkinter')
        finally:
            _ctypes.dlclose(tkinter_dll._handle)
    finally:
        # _tkinter (and the live interpreter) retain the runtime. Balance our
        # NOLOAD reference; ctypes function wrappers do not own dlopen handles.
        _ctypes.dlclose(dll._handle)
    return dll


def _loaded_tcl(tcl):
    """Capability gates and Tcl 8.6/9.0 C ABI, independent of discovery."""
    if sys.implementation.name != 'cpython' or sys.platform not in ('win32', 'linux', 'darwin'):
        raise RuntimeError('requires CPython on Windows, Linux or macOS')
    if getattr(sys, '_is_gil_enabled', lambda: True)() is False:
        raise RuntimeError('free-threaded Python Tcl interruption is unverified')
    patchlevel = tuple(map(int, str(tcl.call('info', 'patchlevel')).split('.')))
    family = patchlevel[:2]
    if family not in ((8, 6), (9, 0)) or tk.TclVersion != float('%d.%d' % family):
        raise RuntimeError('requires matching tkinter and Tcl 8.6 or 9.0')
    # Tcl 9 is always threaded and no longer exposes tcl_platform(threaded).
    if family == (8, 6) and not tcl.getboolean(tcl.call('set', 'tcl_platform(threaded)')):
        raise RuntimeError('requires threaded Tcl 8.6')
    if not callable(getattr(tcl.tk, 'interpaddr', None)):
        raise RuntimeError('tkapp.interpaddr is unavailable')
    interp = tcl.tk.interpaddr()
    if not isinstance(interp, int) or not 0 < interp < 1 << (8 * ctypes.sizeof(ctypes.c_void_p)):
        raise RuntimeError('tkapp.interpaddr is not a valid native address')
    dll = _windows_tcl() if sys.platform == 'win32' else _posix_tcl()
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
    # Tcl 9's Tcl_Size is ptrdiff_t; Tcl 8.6 uses int for the script length.
    size_type = ctypes.c_ssize_t if family == (9, 0) else ctypes.c_int
    # Exact public C ABI (cdecl), not the Tcl stubs table.
    signatures = {
        'Tcl_GetVersion': ([ctypes.POINTER(ctypes.c_int)] * 4, None),
        'Tcl_AsyncCreate': ([callback_type, ctypes.c_void_p], ctypes.c_void_p),
        'Tcl_AsyncMark': ([ctypes.c_void_p], None),
        'Tcl_AsyncDelete': ([ctypes.c_void_p], None),
        'Tcl_CancelEval': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
        'Tcl_SaveInterpState': ([ctypes.c_void_p, ctypes.c_int], ctypes.c_void_p),
        'Tcl_RestoreInterpState': ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
        'Tcl_EvalEx': ([ctypes.c_void_p, ctypes.c_char_p, size_type, ctypes.c_int], ctypes.c_int),
    }
    for name, (args, result) in signatures.items():
        function = getattr(dll, name)
        if sys.platform != 'win32' and _symbol_owner(function) != _symbol_owner(dll.Tcl_CreateInterp):
            raise RuntimeError('Tcl export owner does not match _tkinter: %s' % name)
        function.argtypes = args
        function.restype = result
    version = [ctypes.c_int() for _ in range(4)]
    dll.Tcl_GetVersion(*(ctypes.byref(part) for part in version))
    if tuple(part.value for part in version[:3]) != patchlevel:
        raise RuntimeError('loaded Tcl version does not match the interpreter')
    return dll, callback_type


class TclInterrupt:
    """One evaluation, owned and cleaned up on the interpreter/Qt thread."""

    def __init__(self, app, tcl):
        self.app = app
        self.tcl = tcl
        self.cancel = threading.Event()
        self.stop = threading.Event()
        self.dll, callback_type = _loaded_tcl(tcl)
        self.interp = tcl.tk.interpaddr()
        self.owner = threading.get_ident()
        self.command = '::flatcam_async_%x' % id(self)
        self.inside = False
        self.error = None
        self.callback = callback_type(self._async)
        self.token = None
        self.worker = None
        self.command_created = False

    def _pump(self):
        # tkinter releases/reacquires its Python thread state around this registered
        # command. Pumping Qt directly in _async can fatally reenter tkinter.
        self.app._script_event_pump = True
        try:
            self.app.shell.flush_tcl_output()
            QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 5)
        finally:
            self.app._script_event_pump = False
        return ''

    def _async(self, data, interp, code):
        if self.inside or not interp or interp != self.interp:
            return code
        self.inside = True
        try:
            if threading.get_ident() != self.owner:
                raise RuntimeError('Tcl async handler invoked on the wrong thread')
            state = self.dll.Tcl_SaveInterpState(interp, code)
            try:
                if self.dll.Tcl_EvalEx(interp, self.command.encode('ascii'), -1, 0) != 0:
                    raise RuntimeError('Tcl event trampoline failed')
            finally:
                self.dll.Tcl_RestoreInterpState(interp, state)
        except BaseException as error:
            # Never let Python exceptions escape a ctypes callback.
            self.error = str(error)
            self.cancel.set()
        finally:
            if self.cancel.is_set():
                # TCL_CANCEL_UNWIND: Tcl catch must not swallow the stop request.
                self.dll.Tcl_CancelEval(interp, None, None, 0x100000)
            self.inside = False
        return code

    def _mark(self):
        # The marker thread may ONLY mark this handler. No interpreter or Qt access.
        while not self.stop.wait(.01):
            self.dll.Tcl_AsyncMark(self.token)

    def start(self):
        if self.stop.is_set() or self.command_created:
            raise RuntimeError('Tcl interrupt is single-use')
        try:
            self.tcl.createcommand(self.command, self._pump)
            self.command_created = True
            self.token = self.dll.Tcl_AsyncCreate(self.callback, None)
            if not self.token:
                self.token = None
                raise RuntimeError('Tcl_AsyncCreate failed')
            self.worker = threading.Thread(target=self._mark, name='Tcl interrupt marker')
            self.worker.start()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.stop.set()
        if self.worker is not None and self.worker.ident is not None:
            self.worker.join()
        self.worker = None
        if self.token is not None:
            self.dll.Tcl_AsyncDelete(self.token)
            self.token = None
        # Misc.deletecommand expects Tk's _tclCommands list; tkapp does not.
        if self.command_created:
            self.tcl.tk.deletecommand(self.command)
            self.command_created = False
        self.callback = None


def catch_tcl(app, tcl, text, result_var, options_var):
    """A catch boundary is required to clear Tcl's unwind state before reuse."""
    interrupt = None
    app.script_cancelled = False
    qapp = QtCore.QCoreApplication.instance()
    if not getattr(app, 'headless', False) and qapp is not None:
        if QtCore.QThread.currentThread() != qapp.thread():
            raise tk.TclError('Tcl execution must run on the GUI/interpreter thread.')
        try:
            interrupt = TclInterrupt(app, tcl)
            interrupt.start()
        except (RuntimeError, AttributeError, OSError, KeyError, ValueError, tk.TclError) as error:
            if not getattr(app, '_tcl_interrupt_warning', False):
                app.log.warning('Native Tcl interruption unavailable: %s. Pure Tcl loops cannot be interrupted.' % error)
                app._tcl_interrupt_warning = True
            interrupt = None
    app.script_interrupt = interrupt
    try:
        return int(tcl.call('catch', text, result_var, options_var))
    finally:
        if interrupt is not None:
            interrupt.close()
            app.script_cancelled = interrupt.cancel.is_set()
            if app.script_cancelled:
                workers = getattr(app, 'workers', None)
                pending = getattr(app, 'script_pending_work', [])
                if not any(not state['finished'] for state in pending) and \
                        (workers is None or workers.pending_count() == 0):
                    app.abort_flag = False
            if interrupt.error:
                app.log.error('Tcl interruption failed: %s' % interrupt.error)
        app.script_interrupt = None


def completion_code(tcl, code, options):
    """Resolve top-level return, including return -code error, consistently."""
    if code == 2:
        code = int(tcl.call('dict', 'get', options, '-code'))
        level = int(tcl.call('dict', 'get', options, '-level'))
        if level > 1:
            return 2
    return code
