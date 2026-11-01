"""Helpers of tests/test_grbl_stream_abort.py and tests/test_grbl_check_mode.py (no tests here)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6 import QtWidgets
from shapely import Point

from tests.test_levelling_audit import make_audit_tool, FakeGrbl, emitted


def gcode(count):
    """`count` short G-code lines: 22 characters, 23 bytes with the line end (5 fit in the GRBL buffer)."""
    return '\n'.join('G1 X%02d.000 Y0.000 F100' % i for i in range(count)) + '\n'


def make_stream_tool(fake, source=None, check_mode=False):
    """A ToolGcodeSender (make_audit_tool) on the fake port. The stream worker runs inline in on_start()."""
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool.app.options = {'tools_grbl_status_ms': 20}
    tool.app.worker_task.emit.side_effect = lambda task: task['fcn']()
    tool._start_stream_thread = lambda fn: fn()
    tool.job_lines = []
    tool.job_line_numbers = []
    tool.job_target_name = None
    tool._last_wco = None
    tool._stream_result = {}
    target = SimpleNamespace(kind='cncjob', source_file=source if source is not None else gcode(10),
                             obj_options={'name': 'job'}, units='MM', coords_decimals=4)
    tool.app.collection.get_by_name.return_value = target
    tool.ui.check_mode_cb.get_value.return_value = check_mode
    tool._choose_start_line = MagicMock(return_value=1)
    # the GUI slot runs at once, so the messages of the end of the job can be checked
    tool.stream_ended_sig.emit.side_effect = tool._on_stream_ended
    return tool


def ended_how(tool):
    """The `how` of the last stream_ended_sig.emit()."""
    return tool.stream_ended_sig.emit.call_args.args[0]


def has_success(tool):
    return any('[success]' in text for text in emitted(tool))


class CheckFake(FakeGrbl):
    """A FakeGrbl with the $C command of GRBL 1.1.

    $C enters check mode ([MSG:Enabled], state Check). In check mode, $C leaves it: GRBL resets and
    answers `exit_lines`; the state is then `after_exit_state`. `one_chunk`: the exit lines come in 1 chunk.
    `mute_status_after_exit`: no status report after the exit. `exiting`: True after the exit command."""

    def __init__(self, in_check=False, **kwargs):
        super().__init__(**kwargs)
        self.in_check = in_check
        if in_check:
            self.state = 'Check'
        self.after_exit_state = 'Idle'
        self.exit_lines = ['[MSG:Disabled]', 'ok', "Grbl 1.1h ['$' for help]"]
        self.one_chunk = False
        self.mute_status_after_exit = False
        self.exiting = False

    def _answer(self, cmd):
        if cmd == '$C':
            if not self.in_check:
                self.in_check = True
                self.state = 'Check'
                self._send('[MSG:Enabled]', 'ok')
                return
            self.in_check = False
            self.exiting = True
            self.state = self.after_exit_state
            if self.one_chunk:
                self.rx.append(b''.join(line.encode('utf-8') + b'\r\n' for line in self.exit_lines))
            else:
                self._send(*self.exit_lines)
            return
        if cmd == '?' and self.exiting and self.mute_status_after_exit:
            return
        super()._answer(cmd)


# ---------------------------------------------------------------------------------------------
# The levelled Start (T16b)
# ---------------------------------------------------------------------------------------------
FRAME = ('G54', (0.0, 0.0, 0.0), False)

# a segmented job with comments, a header and cutting moves inside the map (0..10)
JOB = (
    "(FlatCAM Gcode Generator)\nG21\nG90\nG94\nF300.0000\nM03 S10000\n"
    "G00 Z2.0000 (start)\nG00 X1.0000 Y1.0000\nG01 Z-0.1000 F300.0000\n"
    "G01 X3.0000 Y1.0000\nG01 X5.0000 Y2.0000\nG01 X7.0000 Y4.0000\nG01 X9.0000 Y6.0000\n"
    "G00 Z2.0000\nG00 X0.0000 Y0.0000\nM05\nM2\n"
)


def make_storage():
    """3 x 3 grid over 0..10 with a plane and a bump in the middle."""
    storage = {}
    idx = 0
    for x in (0.0, 5.0, 10.0):
        for y in (0.0, 5.0, 10.0):
            height = 0.01 * x + 0.02 * y + (0.05 if (x, y) == (5.0, 5.0) else 0.0)
            storage[idx] = {'point': Point(x, y), 'geo': None, 'height': height}
            idx += 1
    return storage


def make_job_target(text, segmented=True):
    return SimpleNamespace(
        kind='cncjob', source_file=text, units='MM', coords_decimals=4, obj_options={'name': 'job'},
        tools={1: {'gcode': text, 'data': {}}}, is_segmented_gcode=segmented,
        pp_geometry_name='default', pp_excellon_name='default', pp_solderpaste_name=None,
        seg_x=0, seg_y=0)


def make_levelled_stream_tool(fake, text=JOB, frame=FRAME, imported=False, check_mode=False):
    """
    A ToolGcodeSender on the fake port with a height map (probed in `frame`, or imported and not bound),
    'Level the G-code' on, and the workers and the frame signal running inline.

    The dialog of an imported map is a MagicMock that answers Yes (`tool._confirm_imported_frame`).
    :return: (tool, target)
    """
    tool = make_stream_tool(fake, source=text, check_mode=check_mode)
    target = make_job_target(text)
    tool.app.collection.get_by_name.return_value = target
    tool.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task.get('params', []))
    tool.al_voronoi_geo_storage = make_storage()
    tool._al_data_units = 'MM'
    tool.al_heights_valid = True
    tool._al_target_name = 'job'
    tool._al_target_obj = target
    tool._al_map_frame = None if imported else frame
    tool._al_map_imported = imported
    tool.ui.autolevel_cb.get_value.return_value = True
    tool.ui.al_method_radio.get_value.return_value = 'b'
    tool.ui.object_combo.get_value.return_value = 'job'
    tool.frame_ready_sig = MagicMock()
    tool.frame_ready_sig.emit.side_effect = lambda req, result: tool._on_frame_ready(req, result)
    tool._confirm_imported_frame = MagicMock(return_value=True)
    tool._choose_height_map = MagicMock(return_value='existing')
    return tool, target


def spy_reserve(tool):
    """Record the names of the reserved operations: returns the list."""
    names = []
    real = tool._grbl_reserve

    def reserve(name, *args, **kwargs):
        names.append(name)
        return real(name, *args, **kwargs)
    tool._grbl_reserve = reserve
    return names


def import_map_b(tool, directory):
    """The user imports another map for the same points (the import runs inline in the tests)."""
    path = os.path.join(directory, 'map_b.txt')
    with open(path, 'w') as f:
        for value in tool.al_voronoi_geo_storage.values():
            f.write('%s,%s,%s\n' % (value['point'].x, value['point'].y, 0.5))
    with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(path, '')):
        tool.on_import_height_map()


class HookFake(CheckFake):
    """A CheckFake that runs `actions[(command, n)]` after its n-th answer to `command`.

    The action runs at the next readline(). The operation lock is free there, so an action can call
    the events of the tool. `counts` holds the number of commands seen."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.actions = {}
        self.counts = {}
        self.pending = []

    def _answer(self, cmd):
        self.counts[cmd] = self.counts.get(cmd, 0) + 1
        action = self.actions.get((cmd, self.counts[cmd]))
        if action is not None:
            self.pending.append(action)
        super()._answer(cmd)

    def readline(self):
        while self.pending:
            self.pending.pop(0)()
        return super().readline()


def drop_console_event_filters():
    """Take the application-wide event filter off every live GrblConsole.

    A GrblConsole installs the filter in its constructor and a closed tool does not remove it. A dead filter makes
    every later Qt event of the process slower (about 0.3 s for each console after a few dozen), so a test that
    builds many tools calls this in its tear-down.
    """
    qapp = QtWidgets.QApplication.instance()
    for widget in qapp.allWidgets():
        if type(widget).__name__ == 'GrblConsole':
            qapp.removeEventFilter(widget)
