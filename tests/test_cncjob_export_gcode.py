"""
CNCJobObject.export_gcode() must keep the G-code of every tool of a multi-tool job, whatever the preprocessor
and whatever the kind of the source object (Excellon or Geometry).

The preprocessors Roland_MDX_20, Roland_MDX_540 and ISEL_ICP_CNC have ``include_header = False``. For them the
per-tool G-code was added only for Geometry jobs, so the file of an Excellon (drill) job held the start and the end
code and no drill moves at all.
"""

import importlib
import os
import pkgutil
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

import appPreProcessor
import preprocessors
from appGUI.ObjectUI import CNCObjectUI
from appObjects.CNCJobObject import CNCJobObject

for _module in pkgutil.iter_modules(preprocessors.__path__):
    importlib.import_module('preprocessors.' + _module.name)
# a copy: other tests of the same worker change the global registry
PREPROCESSORS = dict(appPreProcessor.preprocessors)

HEADERLESS = ('ISEL_ICP_CNC', 'Roland_MDX_20', 'Roland_MDX_540')

# the key of the preprocessor name inside the tool's options depends on the kind of the source object
PP_KEY = {'Excellon': 'tools_drill_ppname_e', 'Geometry': 'tools_mill_ppname_g'}


class _Log:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = debug


def _job(kind, ppname):
    app = SimpleNamespace(preprocessors=PREPROCESSORS, log=_Log())
    job = SimpleNamespace(
        app=app,
        obj_options={'type': kind, 'name': 'job'},
        multitool=True,
        tools={uid: {'data': {PP_KEY[kind]: ppname}, 'gcode': code}
               for uid, code in enumerate(('BODY_TOOL_1\n', 'BODY_TOOL_2\n'), 1)},
        gcode='', gc_start='START_CODE\n', gc_header='HEADER\n',
    )
    options = {'cncjob_footer': False, 'cncjob_prepend': '', 'cncjob_append': '', 'cncjob_line_ending': False}
    job._app_option = lambda key, fallback=None: options.get(key, fallback)
    job.gcode_footer = lambda: ''
    return job


def _export(job):
    return CNCJobObject.export_gcode(job, to_file=True).getvalue()


@pytest.mark.parametrize('kind', ['Excellon', 'Geometry'])
def test_every_preprocessor_keeps_the_code_of_every_tool(kind):
    dropped = []
    for ppname in sorted(appPreProcessor.preprocessors):
        text = _export(_job(kind, ppname))
        if not ('BODY_TOOL_1' in text and 'BODY_TOOL_2' in text
                and text.index('BODY_TOOL_1') < text.index('BODY_TOOL_2')):
            dropped.append(ppname)

    assert dropped == []


@pytest.mark.parametrize('ppname', HEADERLESS)
def test_headerless_drill_job_has_start_code_tool_code_and_no_page_header(ppname):
    text = _export(_job('Excellon', ppname))

    assert text.startswith('START_CODE')
    assert text.index('START_CODE') < text.index('BODY_TOOL_1') < text.index('BODY_TOOL_2')
    # include_header = False: the gc_header block is not written
    assert 'HEADER' not in text


def test_hpgl_drill_job_is_detected_from_tools_drill_ppname_e():
    job = _job('Excellon', 'hpgl')
    job.tools = {1: {'data': {'tools_drill_ppname_e': 'hpgl'}, 'gcode': 'PA12.7,3.5;\n'}}

    text = _export(job)

    # HPGL output rounds the PA coordinates to whole units
    assert 'PA12,3;' in text
    assert 'PA12.7' not in text




@pytest.mark.parametrize('kind', ['Excellon', 'Geometry'])
def test_unknown_preprocessor_of_a_saved_job_exports_with_the_default_one(kind):
    job = _job(kind, 'Gone_Machine')
    job.app.inform = Mock()
    job.app.options = {}

    text = _export(job)

    assert 'BODY_TOOL_1' in text and 'BODY_TOOL_2' in text
    assert 'Gone_Machine' in job.app.inform.emit.call_args[0][0]


def test_a_name_with_parentheses_stays_inside_the_header_comment():
    # GRBL, FluidNC and LinuxCNC end a '( ... )' comment at the first ')': 'board (1)' left G-code text on the line
    def header(name, ppname):
        job = _job('Geometry', ppname)
        job.obj_options['name'] = name
        job.units = 'MM'
        job.app.version = job.app.version_date = '1'
        return CNCJobObject.gcode_header(job)

    for ppname in ('default', 'Toolchange_Probe_MACH3'):
        for name, written in (('board (1)-F_Cu.gbr_iso_cnc', 'board [1]-F_Cu.gbr_iso_cnc'),
                              ('job\nG0 X0', 'job G0 X0')):
            text = header(name, ppname)
            assert '(Name: %s)\n' % written in text
            # what the controller reads outside the comments
            assert [line for line in text.splitlines() if re.sub(r'\([^)]*\)', '', line).strip()] == []
    # a ';' comment runs to the end of the line: it keeps the name as it is
    assert ';Name: board (1)\n' in header('board (1)', 'Marlin')




# ------------------------------------------------------------------------------------------------------------
# File > Open G-Code: the job is built like a native one and its text stays the text of the file
# ------------------------------------------------------------------------------------------------------------

MILL_FILE = ("G21\r\nG90\r\n(Tool: 1 -> Dia: 0.8)\r\nT1\r\nM3 S10000\r\nG0 Z2\r\nG0 X1 Y1\r\n"
             "G1 Z-0.1 F100\r\nG1 X10 Y1 F200\r\nG0 Z2\r\nM5\r\nT2\r\nG0 X3 Y3\r\nG1 Z-0.1 F100\r\nG1 X3 Y9 F200\r\n"
             "G0 Z2\r\n")
DRILL_FILE = ("(Type: G-code from Excellon)\n(Tool: 1 -> Dia: 0.8)\nG21\nG90\nT1\nM6\nG0 Z2\nG0 X1 Y1\nG1 Z-1.7\n"
              "G0 Z2\nG0 X5 Y1\nG1 Z-1.7\nG0 Z2\n")
# the keys of a tool of a native job: Milling (Geometry) and Drilling (Excellon)
MILL_KEYS = {'tooldia', 'data', 'gcode', 'gcode_parsed', 'solid_geometry'}
DRILL_KEYS = MILL_KEYS | {'drills', 'slots', 'nr_drills', 'nr_slots', 'offset'}


def _open_gcode(tmp_path, content, units='MM', name='job.nc'):
    from unittest.mock import patch
    from appHandlers.appIO import appIO
    from tests.helpers_objects import make_app_0b

    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    app = make_app_0b()
    app.app_units = units
    app.new_project_signal = Mock()
    app.file_opened = Mock()
    app.file_saved = Mock()
    jobs = []

    def new_object(kind, job_name, initialize, *args, **kwargs):
        job = CNCJobObject(job_name, app)
        if initialize(job, app) == 'fail':
            return 'fail'
        jobs.append(job)
        return job

    app.app_obj.new_object.side_effect = new_object
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        result = appIO(app).open_gcode(str(path), plot=False)
    return app, (jobs[0] if jobs else result)


def test_opened_gcode_is_a_native_like_multitool_job_with_the_text_of_the_file(tmp_path):
    from tests.helpers_objects import _headless_qt  # noqa: F401

    app, job = _open_gcode(tmp_path, MILL_FILE)

    assert job.gcode_imported is True and job.multitool is True
    assert job.obj_options['type'] == 'Geometry' and job.obj_options['gcode_units'] == 'MM'
    assert job.used_tools == [1, 2]
    assert MILL_KEYS <= set(job.tools[1])
    assert job.tools[1]['tooldia'] == pytest.approx(0.8)
    assert job.tools[1]['data']['tools_mill_cutz'] == pytest.approx(-0.1)
    assert job.tools[1]['data']['tools_mill_ppname_g'] == 'default'
    # the second tool has no diameter in the file: the default mill tool
    assert job.tools[2]['tooldia'] == pytest.approx(float(str(app.options['tools_mill_tooldia']).split(',')[0]))
    assert len(job.gcode_parsed) == len(job.tools[1]['gcode_parsed']) + len(job.tools[2]['gcode_parsed']) > 0
    assert job.solid_geometry
    # the blocks are the slices of the file; the text is the file, byte for byte (the CRLF too)
    assert job.tools[1]['gcode'] + job.tools[2]['gcode'] == MILL_FILE
    assert job.gcode == MILL_FILE and job.source_file == MILL_FILE
    assert job.export_gcode(to_file=True).getvalue() == MILL_FILE

    # in other units than the application the job is converted but its text is not
    _app, inch_job = _open_gcode(tmp_path, MILL_FILE, units='IN')
    assert inch_job.units.upper() == 'IN' and inch_job.obj_options['gcode_units'] == 'MM'
    assert inch_job.tools[1]['tooldia'] == pytest.approx(0.8 / 25.4)
    assert inch_job.obj_options['xmax'] == pytest.approx(10 / 25.4)
    assert inch_job.gcode == MILL_FILE

    # the export writes the bytes of the file
    out = tmp_path / 'out.nc'
    job.export_gcode(filename=str(out))
    assert out.read_bytes() == MILL_FILE.encode()
    out2 = tmp_path / 'out2.nc'
    job.export_gcode_handler(str(out2), rename_object=False)
    assert out2.read_bytes() == MILL_FILE.encode()

    # the snippets are added only when the checkbox is checked
    job.prepend_snippet, job.append_snippet = 'PRE', 'POST'
    for checked, expected in ((True, 'PRE\n' + MILL_FILE + '\nPOST'), (False, MILL_FILE)):
        job.ui = SimpleNamespace(snippets_cb=Mock(get_value=Mock(return_value=checked)))
        job.on_update_source_file()
        assert job.source_file == expected
    assert job.export_gcode(to_file=True).getvalue() == MILL_FILE


def test_opened_drill_gcode_is_an_excellon_job_with_the_drill_counts(tmp_path):
    _app, job = _open_gcode(tmp_path, DRILL_FILE)

    assert job.obj_options['type'] == 'Excellon'
    assert DRILL_KEYS <= set(job.tools[1])
    assert job.tools[1]['nr_drills'] == 2 and len(job.tools[1]['drills']) == 2
    assert job.tools[1]['data']['tools_drill_cutz'] == pytest.approx(-1.7)


def test_file_that_is_not_utf8_opens_and_a_file_without_moves_fails_with_one_message(tmp_path):
    latin = "(caf\xe9 board)\nG21\nT1\nG0 X1 Y1\nG1 Z-0.1 F100\nG1 X5\n".encode('latin-1')
    _app, job = _open_gcode(tmp_path, latin)
    assert job.gcode_imported is True and job.tools[1]['gcode_parsed']
    # it is written back in its own encoding
    out = tmp_path / 'latin_out.nc'
    job.export_gcode(filename=str(out))
    assert out.read_bytes() == latin

    app, result = _open_gcode(tmp_path, 'just some text\nnot gcode at all\n')
    assert result == 'fail'
    errors = [call.args[0] for call in app.inform.emit.call_args_list if call.args[0].startswith('[ERROR')]
    assert len(errors) == 1 and 'not GCODE' in errors[0]


@pytest.mark.parametrize("content, kind, dia_option, cutz_option", [
    ("G0 X0 Y0\nG1 X10 Y0 F100\n", 'Geometry', 'tools_mill_tooldia', 'tools_mill_cutz'),
    ("G0 X0 Y0 Z1\nG1 Z-1 F50\nG0 Z1\nG0 X5\nG1 Z-1\nG0 Z1\n", 'Excellon', 'excellon_drill_tooldia', None),
])
def test_gcode_without_tool_data_opens_with_defaults_and_warns_once(tmp_path, content, kind, dia_option,
                                                                    cutz_option):
    app, job = _open_gcode(tmp_path, content)

    default = float(str(app.options[dia_option]).split(',')[0])
    assert job.obj_options['type'] == kind
    assert list(job.tools) == [1] and job.tools[1]['tooldia'] == pytest.approx(default) and default > 0
    if cutz_option:
        assert job.tools[1]['data'][cutz_option] == app.options[cutz_option]
    warnings = [c.args[0] for c in app.inform.emit.call_args_list
                if c.args[0].startswith('[WARNING_NOTCL]') and 'tool data' in c.args[0]]
    assert len(warnings) == 1 and '%g' % default in warnings[0] and 'G20/G21' in warnings[0]


# ------------------------------------------------------------------------------------------------------------
# The G-code file: Save CNC Code (export_gcode_handler) and the Tcl write_gcode (export_gcode with a file name)
# ------------------------------------------------------------------------------------------------------------

BODY = 'G0 X1 Y1\nG1 Z-0.1 F100\nG1 X10 Y1 F200\nG0 Z2\nM5\n'
OLD_FILE = b'OLD G-CODE, STILL GOOD\r\n'


def _ui_app(**options):
    from tests.helpers_objects import make_app_0b

    app = make_app_0b(**options)
    app.preprocessors = PREPROCESSORS
    app.version, app.version_date = 'test', 'test'
    app.ui = MagicMock()
    app.resource_location = 'assets/resources'
    app.file_opened, app.file_saved = Mock(), Mock()
    app.dec_format = round
    return app


def _ui_job(app, init=None):
    """A Milling job as AppObject.new_object() makes it: the init function, then set_ui()."""
    from PyQt6 import QtWidgets

    # the Properties panel needs a QApplication; it is kept (a collected one ends the process)
    _ui_job.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        job = CNCJobObject('job', app)
    job.obj_options['type'] = 'Geometry'
    job.multitool = True
    job.used_tools = [1]
    job.tools = {1: {'tooldia': 0.1, 'data': {'tools_mill_ppname_g': 'default'}, 'gcode': BODY}}
    job.gc_start = 'G21\nG90\n'
    if init is not None:
        init(job)
    job.set_ui(CNCObjectUI(app=app))
    return job


def _save_both_ways(job, folder):
    """Save CNC Code into 'button.nc' and the Tcl write_gcode into 'tcl.nc': their results."""
    return (job.export_gcode_handler(str(folder / 'button.nc'), rename_object=False),
            job.export_gcode(filename=str(folder / 'tcl.nc')))


def test_a_failed_gcode_save_keeps_the_old_file_and_says_why(tmp_path):
    import errno
    import appObjects.CNCJobObject as cnc_module

    real_open = open

    def full_card_open(file, *args, **kwargs):
        # the SD card is full after a few characters
        handle = real_open(file, *args, **kwargs)
        real_write = handle.write

        def write(text):
            real_write(text[:20])
            raise OSError(errno.ENOSPC, 'No space left on device')

        handle.write = write
        return handle

    job = _ui_job(_ui_app())
    for name in ('button.nc', 'tcl.nc'):
        (tmp_path / name).write_bytes(OLD_FILE)

    with patch.object(cnc_module, 'open', full_card_open, create=True):
        assert _save_both_ways(job, tmp_path) == ('fail', 'fail')

    # before: the files were cut (the end code was missing) and the error went to the crash dialog
    assert sorted(p.name for p in tmp_path.iterdir()) == ['button.nc', 'tcl.nc']
    assert (tmp_path / 'button.nc').read_bytes() == OLD_FILE and (tmp_path / 'tcl.nc').read_bytes() == OLD_FILE
    errors = [c.args[0] for c in job.app.inform.emit.call_args_list if c.args[0].startswith('[ERROR_NOTCL]')]
    assert len(errors) == 2 and all('Could not save the file' in e and 'No space' in e for e in errors)
    job.app.file_saved.emit.assert_not_called()


def test_the_gcode_file_is_written_in_one_write(tmp_path):
    import appObjects.CNCJobObject as cnc_module

    real_open = open
    writes = []

    def counting_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        real_write = handle.write
        handle.write = lambda text: (writes.append(len(text)), real_write(text))[1]
        return handle

    job = _ui_job(_ui_app())
    with patch.object(cnc_module, 'open', counting_open, create=True):
        assert _save_both_ways(job, tmp_path) == (None, None)

    # before: 1 write() per character (Save CNC Code) or per line (Tcl), seconds for a big file
    assert writes == [len(job.source_file)] * 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ['button.nc', 'tcl.nc']
    assert (tmp_path / 'button.nc').read_text() == (tmp_path / 'tcl.nc').read_text() == job.source_file


# ------------------------------------------------------------------------------------------------------------
# The G-code saved in the G-code Editor is what the job exports, until the user makes the G-code again
# ------------------------------------------------------------------------------------------------------------

EDIT = '\nM5 (laser off, added in the editor)\n'


def _editor_save(job, text):
    """The save of the G-code Editor (App.on_editing_finished -> AppGCodeEditor.update_fcgcode)."""
    from appEditors.appGCodeEditor import AppGCodeEditor

    editor = SimpleNamespace(app=job.app, gcode_obj=job, ui=MagicMock(), deactivate=Mock())
    editor.ui.gcode_editor_tab.code_editor.toPlainText.return_value = text
    AppGCodeEditor.update_fcgcode(editor, job)


def test_gcode_saved_in_the_editor_survives_a_new_panel_the_project_and_undo(tmp_path):
    from appCommon.ProjectFile import load_project_file, save_project_file
    from appHandlers.appHistory import object_state, restore_object_state
    from camlib import dict2obj, geometry_wkt_cache, project_json_default

    job = _ui_job(_ui_app())
    edited = job.source_file + EDIT
    _editor_save(job, edited)

    # before: set_ui() made the G-code again from the tools; the panel is made again when Qt deleted it
    job.set_ui(CNCObjectUI(app=job.app))
    assert job.source_file == edited
    assert job.export_gcode_handler(str(tmp_path / 'out.nc'), rename_object=False) is None
    assert (tmp_path / 'out.nc').read_text() == edited

    # project save and open (appIO.open_project), and the undo of a delete (HistoryManager.rebuild)
    data = {'objs': [job.to_dict()]}
    path = str(tmp_path / 'project.FlatPrj')
    save_project_file(path, data, default=project_json_default(geometry_wkt_cache(data)))
    entry = load_project_file(path, object_hook=dict2obj)['objs'][0]
    state = object_state(job)

    def open_init(new_job):
        new_job.from_dict(entry)
        new_job.tools = {int(k): v for k, v in new_job.tools.items()}
        new_job.is_loaded_from_project = True

    def undo_init(new_job):
        restore_object_state(new_job, state)
        new_job.is_loaded_from_project = True

    for init in (open_init, undo_init):
        assert _ui_job(_ui_app(), init).source_file == edited, init.__name__


def test_making_the_edited_gcode_again_asks_first_or_says_that_the_edits_are_lost():
    job = _ui_job(_ui_app())
    edited = job.source_file + EDIT
    _editor_save(job, edited)
    job.prepend_snippet = '(PRE)'

    # the snippets checkbox: No keeps the edits and puts the box back, Yes makes the G-code again
    job.ask_to_discard_edited_gcode = Mock(return_value=False)
    job.ui.snippets_cb.set_value(True)
    assert job.source_file == edited
    assert job.ui.snippets_cb.get_value() is False and job.snippets_enabled is False
    job.ask_to_discard_edited_gcode = Mock(return_value=True)
    job.ui.snippets_cb.set_value(True)
    assert '(PRE)' in job.source_file and EDIT not in job.source_file

    # a move (Set Origin, Move to Origin): the G-code must follow the new coordinates, the user is told
    _editor_save(job, edited)
    job.tools[1]['gcode'] = BODY.replace('X1 ', 'X6 ')
    assert job.regenerate_source() is True
    assert 'X6 ' in job.source_file and EDIT not in job.source_file
    warnings = [c.args[0] for c in job.app.inform.emit.call_args_list if c.args[0].startswith('[WARNING]')]
    assert len(warnings) == 1 and 'GCode Editor' in warnings[0]


def test_a_job_that_was_not_edited_is_made_again_from_its_tools():
    job = _ui_job(_ui_app())
    # a save of the editor without a change
    _editor_save(job, job.source_file)
    assert job.to_dict().get('edited_source_file', '') == ''

    job.tools[1]['gcode'] = BODY + 'G1 X20\n'
    job.set_ui(CNCObjectUI(app=job.app))
    assert 'G1 X20' in job.source_file

    # the snippets checkbox does not ask and a move gives no warning
    job.ask_to_discard_edited_gcode = Mock()
    job.prepend_snippet = '(PRE)'
    job.ui.snippets_cb.set_value(True)
    job.ask_to_discard_edited_gcode.assert_not_called()
    assert '(PRE)' in job.source_file
    assert job.regenerate_source() is True
    assert not any(c.args[0].startswith('[WARNING]') for c in job.app.inform.emit.call_args_list)


# ------------------------------------------------------------------------------------------------------------
# 'Use CNC Code Snippets' unchecked: no prepend / append snippet in the G-code
# ------------------------------------------------------------------------------------------------------------

PRE, POST = '(PREPEND: G28 home)', '(APPEND: M30)'


def test_no_snippets_in_the_gcode_while_the_snippets_checkbox_is_off():
    job = _ui_job(_ui_app(cncjob_prepend=PRE, cncjob_append=POST))
    assert job.ui.snippets_cb.get_value() is True and PRE in job.source_file and POST in job.source_file

    # before: an empty snippet was replaced by the one of the Preferences, so they stayed
    job.ui.snippets_cb.set_value(False)
    assert PRE not in job.source_file and POST not in job.source_file and BODY in job.source_file
    # a move, in a worker: snippets_enabled is the state of the box
    assert job.regenerate_source() is True
    assert PRE not in job.source_file and POST not in job.source_file
    job.ui.snippets_cb.set_value(True)
    assert PRE in job.source_file and POST in job.source_file

    # a job of an opened project made without snippets, now that the Preferences have snippets
    made = _ui_job(_ui_app())
    entry = made.to_dict()

    def open_init(new_job):
        new_job.from_dict(entry)
        new_job.is_loaded_from_project = True

    opened = _ui_job(_ui_app(cncjob_prepend=PRE, cncjob_append=POST), open_init)
    assert opened.ui.snippets_cb.get_value() is False and opened.source_file == made.source_file


def test_view_source_and_tcl_write_gcode_still_use_the_snippets_of_the_preferences(tmp_path):
    job = _ui_job(_ui_app(cncjob_prepend=PRE, cncjob_append=POST))
    job.ui.snippets_cb.set_value(False)

    # App.on_view_source() and the Tcl write_gcode without -preamble / -postamble
    text = job.export_gcode(to_file=True).getvalue()
    assert PRE in text and POST in text
    job.export_gcode(str(tmp_path / 'tcl.nc'), '', '', from_tcl=True)
    text = (tmp_path / 'tcl.nc').read_text()
    assert PRE in text and POST in text
def test_saving_several_jobs_writes_each_file_in_the_chosen_folder(tmp_path, monkeypatch):
    # Save in the project context menu with several CNC jobs selected: each file is named after its job, and the
    # names of a project file can hold a path
    from appMain import App

    written = []

    class Job:
        kind = 'cncjob'

        def __init__(self, name):
            self.obj_options = {'name': name}

        def read_form(self):
            pass

        def export_gcode_handler(self, filename, is_gcode=True, rename_object=True):
            written.append(filename)

    names = ['board', '..\\..\\x', '../y', 'C:\\z', 'a:b', 'a_b', 'NUL']
    folder = tmp_path / 'chosen'
    app = SimpleNamespace(collection=SimpleNamespace(get_selected=lambda: [Job(name) for name in names]),
                          options={'cncjob_save_filters': 'G-Code Files .nc (*.nc)'}, defaults={},
                          get_last_save_folder=lambda: str(tmp_path))
    monkeypatch.setattr('appMain.FCFileSaveDialog', SimpleNamespace(
        get_saved_filename=lambda **kwargs: (folder.as_posix() + '/multi_save.nc', '')))

    App.on_project_context_save(app)

    assert [os.path.dirname(os.path.abspath(path)) for path in written] == [os.path.abspath(folder)] * len(names)
    files = [os.path.basename(path) for path in written]
    # a plain name is kept; 2 names that become the same file name get 2 files
    assert files[0] == 'board.nc' and len(set(files)) == len(names)
