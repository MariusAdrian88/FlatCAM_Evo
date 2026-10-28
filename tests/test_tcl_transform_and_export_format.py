"""Regression tests for the shared bounds in the Tcl mirror / skew commands and the Gerber export number format.

- TclCommandMirror and TclCommandSkew compute the bounds of all named objects 1 time. The bounds refresh of each
  object must not overwrite them, else the next object uses another reference point.
- TclCommandMirror with neither -box nor -origin uses the center of the bounds of all objects.
- GerberObject.export_gerber: with trailing zeros kept ('T') the sign of a negative coordinate stays in front of the
  zero padding; with leading zeros omitted ('L') no padding is added.
"""

from unittest.mock import Mock, patch

import pytest

import tests.helpers_objects as char
from appObjects.GerberObject import GerberObject
from tclCommands.TclCommandMirror import TclCommandMirror
from tclCommands.TclCommandOffset import TclCommandOffset
from tclCommands.TclCommandSkew import TclCommandSkew
from tests.helpers_objects import (  # noqa: F401,E402
    assert_xy_close, drill_xy, make_app_0b as make_app, make_excellon_0b as make_excellon,
)
from tclCommands.TclCommandScale import TclCommandScale


def _run(cmd_cls, objs, *argv):
    app = make_app()
    app.collection.get_by_name.side_effect = lambda n: objs.get(n)
    cmd = cmd_cls(app)
    args, unnamed = cmd.check_args(tuple(objs) + argv)
    return cmd.execute(args, unnamed)


def _two_excellons():
    a = make_excellon(default_data={}, tooldia=0.2, drills=((0.0, 0.0),), slots=())
    b = make_excellon(default_data={}, tooldia=0.2, drills=((4.0, 2.0),), slots=())
    return a, b


def _expected_mirror(drills, internal_axis, point):
    ref = make_excellon(default_data={}, tooldia=0.2, drills=drills, slots=())
    ref.mirror(internal_axis, point)
    return drill_xy(ref)


class TestTclMirror:
    def test_two_objects_share_one_center(self):
        a, b = _two_excellons()
        _run(TclCommandMirror, {'a': a, 'b': b}, '-axis', 'X', '-origin', 'center')
        # the Tcl command swaps the axis name to work like the GUI: -axis X is mirror('Y', ...)
        assert_xy_close(drill_xy(a), _expected_mirror(((0.0, 0.0),), 'Y', [2.0, 1.0]))
        assert_xy_close(drill_xy(b), _expected_mirror(((4.0, 2.0),), 'Y', [2.0, 1.0]))

    def test_box_path_refreshes_the_bounds_options(self):
        a, _ = _two_excellons()
        box = make_excellon(default_data={}, tooldia=0.2, drills=((10.0, 10.0),), slots=())
        app_objs = {'a': a}

        app = make_app()
        app.collection.get_by_name.side_effect = lambda n: {'a': a, 'box': box}.get(n)
        cmd = TclCommandMirror(app)
        args, unnamed = cmd.check_args(tuple(app_objs) + ('-axis', 'X', '-box', 'box'))
        cmd.execute(args, unnamed)

        assert_xy_close(drill_xy(a), _expected_mirror(((0.0, 0.0),), 'Y', [10.0, 10.0]))
        xmin, ymin, xmax, ymax = a.bounds()
        assert a.obj_options['xmin'] == xmin and a.obj_options['ymin'] == ymin
        assert a.obj_options['xmax'] == xmax and a.obj_options['ymax'] == ymax


class TestTclSkew:
    def test_two_objects_share_one_reference_point(self):
        a, b = _two_excellons()
        _run(TclCommandSkew, {'a': a, 'b': b}, '-x', '10', '-y', '0', '-origin', 'center')

        def expected(drills):
            ref = make_excellon(default_data={}, tooldia=0.2, drills=drills, slots=())
            ref.skew(10.0, 0.0, point=(2.0, 1.0))
            return drill_xy(ref)

        assert_xy_close(drill_xy(a), expected(((0.0, 0.0),)), tol=1e-6)
        assert_xy_close(drill_xy(b), expected(((4.0, 2.0),)), tol=1e-6)


GERBER_NEGATIVE_FLASH = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD10C,1.0*%", "D10*", "X15000Y-22500D03*", "M02*"]


def _exported_body(g_zeros):
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        obj = GerberObject('g', make_app())
    obj.parse_lines(GERBER_NEGATIVE_FLASH)
    return obj.export_gerber(whole=2, fract=4, g_zeros=g_zeros)


class TestGerberExportNumberFormat:
    def test_trailing_zeros_keep_the_sign_in_front_of_the_padding(self):
        assert 'X015000Y-022500D03' in _exported_body('T')

    @pytest.mark.parametrize('g_zeros', ['L', 'T'])
    @pytest.mark.parametrize('whole, fract', [(2, 4), (3, 6), (4, 6)])
    def test_real_export_round_trips_negative_and_small_coordinates(self, g_zeros, whole, fract, tmp_path):
        """appIO.export_gerber -> parser: the Gerber spec 4.2.2 (L) and 8.2.1.1 (T, deprecated) formats."""
        app = char.make_app()
        app.options.update({'gerber_exp_zeros': g_zeros, 'gerber_exp_integer': whole, 'gerber_exp_decimals': fract})
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            grb = char.make_gerber(app, char.NATIVE_GBR)
            # negative coordinates on both axes and a fraction with leading decimal zeros
            grb.offset((-20.05, -20.003))
            text = char.export_gerber_text(app, grb)
            assert '%%FS%sAX%d%dY%d%d*%%' % (g_zeros, whole, fract, whole, fract) in text
            again = char.reparse_gerber(app, text, tmp_path)
        assert again.bounds()[0] < 0 and again.bounds()[1] < 0
        char.assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=10 ** -fract * 50,
                               msg='%s %d.%d' % (g_zeros, whole, fract))


def _flat(items):
    return [c for item in items for c in item]


class TestExcellonExportNumberFormat:
    """appIO.export_excellon -> parser, without the decimal point (Excellon CNC-7 rules):
    LZ: the leading zeros are written, the reader adds the trailing zeros, so the whole part has 'whole' digits;
    TZ: the trailing zeros are written, the reader counts the decimals from the right, so no padding."""

    @pytest.mark.parametrize('slot_type', ['routing', 'drilling'])
    @pytest.mark.parametrize('e_zeros', ['LZ', 'TZ'])
    @pytest.mark.parametrize('whole, fract', [(3, 3), (2, 4)])
    def test_real_export_round_trips_negative_and_small_coordinates(self, e_zeros, whole, fract, slot_type,
                                                                     tmp_path):
        app = char.make_app()
        app.options.update({'excellon_exp_format': 'ndec', 'excellon_exp_zeros': e_zeros,
                            'excellon_exp_integer': whole, 'excellon_exp_decimals': fract,
                            'excellon_exp_units': 'METRIC', 'excellon_exp_slot_type': slot_type})
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            exc = char.make_excellon(app)
            # negative coordinates on both axes, values below 1 and a fraction with leading decimal zeros
            exc.offset((-9.95, -10.05))
            text = char.export_excellon_text(app, exc)
            assert 'METRIC,%s' % e_zeros in text
            again = char.reparse_excellon(app, text, tmp_path)

        tol = 10 ** -fract
        assert sorted(again.tools) == sorted(exc.tools)
        for t, tool in exc.tools.items():
            msg = '%s %d.%d %s T%s' % (e_zeros, whole, fract, slot_type, t)
            # compare the numbers directly (a drill has zero area); the counts must match first
            assert len(again.tools[t]['drills']) == len(tool['drills']), msg
            assert _flat(char.pts(again.tools[t]['drills'])) == pytest.approx(_flat(char.pts(tool['drills'])),
                                                                              abs=tol), msg
            assert len(again.tools[t].get('slots', [])) == len(tool['slots']), msg
            assert _flat(char.slot_flat(again.tools[t].get('slots', []))) == \
                pytest.approx(_flat(char.slot_flat(tool['slots'])), abs=tol), msg


class TestTclBufferJoin:
    """-join 2 / square is the square corner join: the Shapely 'mitre' join ('square' is a cap style)."""

    @pytest.mark.parametrize('join', ['2', 'square', 'mitre'])
    def test_square_join_buffers_the_gerber(self, join):
        from appParsers.ParseGerber import Gerber
        from tclCommands.TclCommandBuffer import TclCommandBuffer
        grb = Gerber(make_app())
        grb.parse_lines(["%FSLAX24Y24*%", "%MOMM*%", "%ADD10R,2.0X1.0*%", "D10*", "X100000Y50000D03*", "M02*"])
        grb.obj_options = {}
        grb.kind = 'gerber'
        xmin, ymin, xmax, ymax = grb.bounds()

        app = make_app()
        app.collection.get_by_name.return_value = grb
        cmd = TclCommandBuffer(app)
        args, unnamed = cmd.check_args(('grb', '-dist', '0.1', '-join', join))
        assert cmd.execute(args, unnamed) != 'fail'

        assert grb.bounds() == pytest.approx((xmin - 0.1, ymin - 0.1, xmax + 0.1, ymax + 0.1), abs=1e-6)


def _tcl_scale(objs, *argv):
    """objs: dict name -> object. Runs ``scale <names> <argv>``."""
    app = make_app()
    app.collection.get_by_name.side_effect = lambda n: objs.get(n)
    cmd = TclCommandScale(app)
    args, unnamed = cmd.check_args(tuple(objs) + argv)
    return cmd.execute(args, unnamed)


def _scale_target():
    return make_excellon(default_data={}, tooldia=0.2, drills=((1.0, 1.0), (3.0, 2.0)), slots=())


class TestTclScale_0B5:
    def test_x_alone_scales_x_only(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-x', '2', '-origin', 'origin')
        assert_xy_close(drill_xy(ex), [(2.0, 1.0), (6.0, 2.0)])


    def test_x_and_y_scale_both_axes(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-x', '2', '-y', '3', '-origin', 'origin')
        assert_xy_close(drill_xy(ex), [(2.0, 3.0), (6.0, 6.0)])

    def test_axis_path_refreshes_bounds_options(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-x', '2', '-y', '2', '-origin', 'origin')
        assert 'xmax' in ex.obj_options
        assert ex.obj_options['xmax'] == pytest.approx(ex.bounds()[2])


    def test_two_objects_axis_path_share_one_center(self):
        # default origin = center of the bounds of ALL named objects, computed once
        a = make_excellon(default_data={}, tooldia=0.2, drills=((0.0, 0.0),), slots=())
        b = make_excellon(default_data={}, tooldia=0.2, drills=((4.0, 0.0),), slots=())
        _tcl_scale({'a': a, 'b': b}, '-x', '2', '-y', '2')
        assert_xy_close(drill_xy(a), [(-2.0, 0.0)])
        assert_xy_close(drill_xy(b), [(6.0, 0.0)])


@pytest.mark.parametrize('cmd_cls, argv, moved', [
    (TclCommandOffset, ('-x', '10', '-y', '5'), r'G0 X11(\.0*)? Y6'),
    (TclCommandMirror, ('-axis', 'X', '-origin', 'origin'), r'G0 X-1(\.0*)? Y1'),
    (TclCommandScale, ('-x', '2', '-origin', 'origin'), r'G0 X2(\.0*)? Y1'),
    (TclCommandSkew, ('-x', '10', '-y', '0', '-origin', 'origin'), r'G0 X1\.17\d* Y1'),
])
def test_tcl_transform_of_a_gcode_job_moves_its_exported_text(cmd_cls, argv, moved, tmp_path):
    """A Tcl transform of an opened G-code job: source_file (the exported text) holds the moved coordinates."""
    import re
    from tests.test_cncjob_export_gcode import MILL_FILE, _open_gcode

    app, job = _open_gcode(tmp_path, MILL_FILE)
    assert 'G0 X1 Y1' in job.source_file
    app.collection.get_by_name.side_effect = lambda n: {'job': job}.get(n)
    cmd = cmd_cls(app)
    args, unnamed = cmd.check_args(('job',) + argv)
    cmd.execute(args, unnamed)

    assert re.search(moved, job.source_file), job.source_file
    assert 'G0 X1 Y1' not in job.source_file
