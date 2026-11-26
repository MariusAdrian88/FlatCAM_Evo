import json
import math
import os

import pytest

from appCommon.machine_profile.motion import interpret
from appCommon.machine_profile.records import Setup
from appCommon.machine_profile.profile import (
    MachineProfile, content_hash, delete_profile, from_dict, list_profiles, load_profile, save_profile, to_dict)
from defaults import is_project_key


def _profile():
    return MachineProfile(name='Router 3018', bed_x=300.0, bed_y=180.0, origin_corner='br', z_travel=45.0,
                          work_offset=(-10.0, -20.0, -1.0), toolchange=(5.0, 5.0, -2.0),
                          exclusions=[{'name': 'clamp', 'polygon': [(0, 0), (10, 0), (10, 10)],
                                       'strategy': 'around', 'over_z': None}],
                          calibration={'matrix': [[1.0, 0.001], [0.0, 1.0]], 'date': '2026-11-24',
                                       'residual': 0.02, 'enabled': True},
                          feed_xy_max=1500.0)


def test_file_gate_carries_result_with_violation_points(tmp_path):
    from appCommon.machine_profile.writer import gate_file_text
    save_profile(MachineProfile(name='Small', bed_x=100.0, bed_y=100.0, origin_corner='bl'), str(tmp_path))
    gate = gate_file_text('Small', str(tmp_path), '', 'G21\nG90\nG0 X1 Y1 Z-1\nG1 X500 Y5 Z-1 F100\n', None, 'MM')
    assert gate.result is not None
    out = [v for v in gate.result.violations if v.kind.value == 'out_of_bed']
    assert out and out[0].end_w[:2] == (500.0, 5.0)
    assert gate_file_text('None', str(tmp_path), '', 'G1 X1\n', None, 'MM').result is None


def test_mm_values_are_converted_to_app_units():
    from appCommon.machine_profile.facts import mm_to_app_units
    assert mm_to_app_units(25.4, 'IN') == pytest.approx(1.0)
    assert mm_to_app_units(25.4, 'in') == pytest.approx(1.0)
    assert mm_to_app_units(25.4, 'MM') == 25.4


def test_design_to_machine_conversion_round_trips():
    from appCommon.machine_profile.facts import design_to_machine, machine_to_design
    design = [(1.0, 2.0), (2.0, 2.0), (2.0, 3.0)]
    machine = design_to_machine(design, (-10.0, 5.0, 0.0), 'IN')
    assert machine[0] == pytest.approx((-10.0 + 25.4, 5.0 + 50.8))
    back = machine_to_design(machine, (-10.0, 5.0, 0.0), 'IN')
    assert [c for p in back for c in p] == pytest.approx([c for p in design for c in p])
    assert design_to_machine(design, (0.0, 0.0, 0.0), 'MM') == design


def test_save_load_round_trip_and_hash_stable(tmp_path):
    profile = _profile()
    path = save_profile(profile, str(tmp_path))
    loaded = load_profile(path)
    assert to_dict(loaded) == to_dict(profile)
    assert content_hash(loaded) == content_hash(profile)
    assert [p.name for p in list_profiles(str(tmp_path))] == ['Router 3018']
    delete_profile('Router 3018', str(tmp_path))
    assert list_profiles(str(tmp_path)) == []


def test_hash_changes_with_one_fact():
    profile = _profile()
    other = _profile()
    other.bed_x = 301.0
    assert content_hash(profile) != content_hash(other)


def test_rule_modes_defaults():
    modes = MachineProfile(name='x').rule_modes
    assert modes['out_of_bed'] == 'reject' and modes['unverified'] == 'warn' and modes['feed'] == 'warn'


def test_reserved_name_refused(tmp_path):
    with pytest.raises(ValueError):
        save_profile(MachineProfile(name='None'), str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_unknown_key_refused():
    data = to_dict(_profile())
    data['bogus_key'] = 1
    with pytest.raises(ValueError, match='bogus_key'):
        from_dict(data)


def test_app_keys_are_not_project_keys():
    assert not is_project_key('global_machine_profile')
    assert not is_project_key('global_machine_profiles_folder')


# ---------------------------------------------------------------- motion interpreter (task 1.2)

def _known_setup(**kw):
    base = dict(dialect='default', wcs_offsets={'G54': (0.0, 0.0, 0.0)}, active_wcs='G54', g92=(0.0, 0.0, 0.0),
                tlo=0.0, start=(0.0, 0.0, 0.0), units='mm', distance_mode='G90', arc_mode='G91.1', plane='G17',
                motion_mode='G0')
    base.update(kw)
    return Setup(**base)


def test_wcs_g92_change_inside_program():
    text = 'G54\nG00 X10 Y10\nG55\nG00 X10 Y10\nG92 X0 Y0\nG00 X10 Y10\n'
    setup = _known_setup(wcs_offsets={'G54': (0.0, 0.0, 0.0), 'G55': (100.0, 0.0, 0.0)})
    records = interpret(text, setup)
    assert [r.end_m for r in records] == [(10.0, 10.0, 0.0), (110.0, 10.0, 0.0), (120.0, 20.0, 0.0)]
    assert all(r.verified for r in records)
    # G55 not in the setup: its move and everything that needs the offset after it are unverified
    records = interpret(text, _known_setup())
    assert [r.verified for r in records] == [True, False, False]
    assert records[1].end_m is None and records[1].unverified_reason


def test_g28_with_axis_words_and_unknown_home():
    records = interpret('G28 Z5\nG00 X5\n', _known_setup())
    assert len(records) == 3
    assert records[0].verified and records[0].end_m == (0.0, 0.0, 5.0)
    assert not records[1].verified and records[1].end_m is None
    assert not records[2].verified and records[2].start_m is None and records[2].end_w[0] == 5.0


def test_g10_l2_and_l20_known_and_unknown():
    # L2 sets the G54 offset directly, even with an unknown X/Y start position
    setup = _known_setup(wcs_offsets={}, start=(None, None, 5.0))
    records = interpret('G10 L2 P1 X-50 Y-20\nG00 X0 Y0\n', setup)
    assert records[0].frame.wcs_offset[:2] == (-50.0, -20.0)
    assert records[0].end_m == (-50.0, -20.0, 5.0) and not records[0].verified
    # L20 with an unknown position: the offset stays unknown and the moves after it are unverified
    setup = _known_setup(wcs_offsets={'G54': (7.0, 7.0, 0.0)}, start=(None, None, 0.0))
    records = interpret('G10 L20 P1 X0 Y0\nG00 X1 Y1\n', setup)
    assert records[0].frame.wcs_offset[:2] == (None, None) and records[0].end_m is None
    # L20 with position (10,10,0), G92 (0,0,0), TLO 0: the offset becomes (10,10,0)
    setup = _known_setup(wcs_offsets={'G54': (3.0, 3.0, 0.0)}, start=(10.0, 10.0, 0.0))
    records = interpret('G10 L20 P1 X0 Y0\nG00 X1 Y1\n', setup)
    assert records[0].frame.wcs_offset == (10.0, 10.0, 0.0)
    assert records[0].start_m == (10.0, 10.0, 0.0) and records[0].end_m == (11.0, 11.0, 0.0)
    assert records[0].verified


def test_absolute_move_from_unknown_start():
    records = interpret('G00 X10 Y10\nG00 X20\n', _known_setup(start=(None, None, 0.0)))
    assert records[0].start_m is None and records[0].end_m == (10.0, 10.0, 0.0)
    assert not records[0].verified
    assert records[1].start_m == (10.0, 10.0, 0.0) and records[1].verified


def test_full_circle_with_center_only():
    records = interpret('G17 G90 G91.1\nG00 X10 Y0\nG02 I-10 J0\n', _known_setup())
    assert len(records) == 2
    arc = records[1]
    assert arc.verified and arc.motion_mode == 'G2' and arc.arc_direction == 'cw'
    assert arc.arc_center == (0.0, 0.0, 0.0)
    assert arc.arc_extent == pytest.approx(2 * math.pi)
    assert arc.end_m == (10.0, 0.0, 0.0)


def test_units_incremental_and_arc_forms():
    text = 'G20 G91\nG00 X1 Y0\nG90 G21\nG00 X10 Y0\nG03 X0 Y10 I-10 J0\nG02 X10 Y0 R10\nG18\nG02 X5 Z5 I1 K1\n'
    records = interpret(text, _known_setup())
    assert records[0].end_m == (25.4, 0.0, 0.0) and records[0].frame.units == 'inch'
    assert records[1].frame.units == 'mm' and records[1].end_m == (10.0, 0.0, 0.0)
    quarter = records[2]
    assert quarter.arc_direction == 'ccw' and quarter.arc_extent == pytest.approx(math.pi / 2)
    assert quarter.arc_center == pytest.approx((0.0, 0.0, 0.0))
    radius = records[3]
    assert radius.verified and radius.arc_direction == 'cw' and radius.arc_extent == pytest.approx(math.pi / 2)
    assert radius.arc_center == pytest.approx((0.0, 0.0, 0.0))
    assert not records[4].verified and records[4].arc_extent is None


def test_unknown_words_are_unverified_and_comments_give_diameter():
    text = ('(TOOL DIAMETER: 0.2 mm)\nG00 X1\nG43 H1\nG00 X2\nG93\nG01 X3 F5\nG94\nG81 X4 Y4 Z-1\nG80\nG00 X5\n'
            'G01 X[#1]\n(MSG, Change to Tool Dia = 0.8000)\nG00 X6\n')
    records = interpret(text, _known_setup())
    assert records[0].tool_diameter == 0.2 and records[0].verified
    assert not records[1].verified and 'G43' in records[1].unverified_reason
    assert not records[2].verified and 'G93' in records[2].unverified_reason
    assert not records[3].verified and 'G81' in records[3].unverified_reason
    assert records[-1].tool_diameter == 0.8 and not records[-1].verified


_PREAMBLE = '(Units: MM)\nG21\nG90\nG17\nG94\nG01 F100.00\n\nM5\nG00 Z15.0000\nG00 X0.0000 Y0.0000\n'
_PROGRAMS = {
    'mill': _PREAMBLE + ('T1\nM6\n(MSG, Change to Tool Dia = 0.2000)\nM0\nG00 Z15.0000\n\nM03 S10000.0\nG4 P1.0\n'
                         'G01 F100.00\nG00 X9.6569 Y9.9332\nG01 F50.00\nG01 Z-0.1000\nG01 F100.00\n'
                         'G01 X9.6506 Y10.0112 F100.00\nG01 X10.0000 Y10.3500\nG00 Z2.0000\nM05\nG00 Z2.0000\n'
                         'G00 Z20.0000\nG00 X1.0000 Y2.0000\n'),
    'drill': _PREAMBLE + ('T1\nM6\n(MSG, Change to Tool Dia = 0.2000 ||| Total drills for tool T1 = 2)\nM0\n'
                          'G00 Z15.0000\n\nG01 F50.00\nM03 S10000.0\nG00 X5.0000 Y5.0000\nG01 Z-1.6000\nG01 Z0\n'
                          'G00 Z2.0000\nG00 X10.0000 Y5.0000\nG01 Z-1.6000\nG01 Z0\nG00 Z2.0000\n\nM5\n'
                          'G00 Z15.0000\nT2\nG00 X0.0000 Y0.0000\nM6\n(MSG, Change to Tool Dia = 1.0000)\nM0\n'
                          'G00 Z15.0000\nG01 F50.00\nM03 S10000.0\nG00 X5.0000 Y10.0000\nG01 Z-1.6000\n'
                          'G01 Z0\nG00 Z2.0000\nM05\nG00 Z20.0000\nG00 X1.0000 Y2.0000\n'),
}


@pytest.mark.parametrize('dialect', ['default', 'GRBL_11'])
@pytest.mark.parametrize('kind', ['mill', 'drill'])
def test_generated_programs_have_no_unverified_record(kind, dialect):
    text = _PROGRAMS[kind]
    records = interpret(text, _known_setup(dialect=dialect, plane=None, motion_mode=None, distance_mode=None,
                                           units=None))
    assert len(records) > 8
    assert all(r.verified for r in records), [(r.line, r.unverified_reason) for r in records if not r.verified]
    assert records[-1].end_m == (1.0, 2.0, 20.0)
    assert records[0].tool_diameter is None and 0.2 in {r.tool_diameter for r in records}


# ---------------------------------------------------------------- check and output (tasks 1.3, 1.4)

from appCommon.machine_profile.check import check_motion
from appCommon.machine_profile.output import has_reject, prepare_output, setup_from_profile
from appCommon.machine_profile.records import CheckStatus, Purpose, ViolationKind


def _bed_profile(**kw):
    base = dict(name='Bed', bed_x=100.0, bed_y=50.0, origin_corner='bl', z_travel=40.0)
    base.update(kw)
    return MachineProfile(**base)


def _kinds(text, profile, **setup_kw):
    result = check_motion(interpret(text, _known_setup(**setup_kw)), profile)
    return result, [(v.kind, v.line, v.severity) for v in result.violations]


def test_move_outside_bed_is_rejected_and_inside_is_checked():
    result, found = _kinds('G00 X10 Y10\nG00 X150 Y10\n', _bed_profile())
    assert found == [(ViolationKind.OUT_OF_BED, 2, 'reject')]
    assert result.status is CheckStatus.VIOLATIONS
    assert has_reject(result)
    result, found = _kinds('G00 X10 Y10\nG00 X90 Y40\n', _bed_profile())
    assert found == [] and result.status is CheckStatus.CHECKED
    # a corner of another origin puts the bed on the negative side
    _, found = _kinds('G00 X-10 Y10\n', _bed_profile(origin_corner='br'))
    assert found == []


def test_z_below_travel_and_limits():
    _, found = _kinds('G00 Z-50\n', _bed_profile())
    assert found == [(ViolationKind.Z_TRAVEL, 1, 'reject')]
    profile = _bed_profile(feed_xy_max=1000.0, feed_z_max=100.0, rpm_min=1000.0, rpm_max=10000.0,
                           cut_depth_max=1.0)
    text = 'F2000\nG01 X10\nF200\nG01 Z-0.5\nF50\nG01 Z-2\nM03 S20000\nG01 X20\n'
    _, found = _kinds(text, profile)
    kinds = [k for k, _, _ in found]
    assert (ViolationKind.FEED, 2, 'warn') in found and (ViolationKind.FEED, 4, 'warn') in found
    assert (ViolationKind.CUT_DEPTH, 6, 'warn') in found
    assert (ViolationKind.RPM, 8, 'warn') in found
    assert kinds.count(ViolationKind.FEED) == 2
    # a speed that runs only between 2 moves, and a speed with no move after it
    _, found = _kinds('G01 X5 F100\nM03 S25000\nG04 P2\nS5000\nG01 X10\n', profile)
    assert (ViolationKind.RPM, 2, 'warn') in found
    _, found = _kinds('M03 S25000\nG04 P2\nM05\n', profile)
    assert (ViolationKind.RPM, 1, 'warn') in found


def test_unverified_record_alone_gives_partial():
    result, found = _kinds('G43 H1\nG00 X5\n', _bed_profile())
    assert [k for k, _, _ in found] == [ViolationKind.UNVERIFIED]
    assert result.status is CheckStatus.PARTIAL
    assert result.violations[0].message and not has_reject(result)


def test_unknown_start_keeps_the_known_destination_out_of_the_bed():
    # the path from an unknown start is unverified, but the known end point is outside the bed: reject
    result, found = _kinds('G00 X110 Y2 Z0\n', _bed_profile(), start=None)
    assert (ViolationKind.OUT_OF_BED, 1, 'reject') in found and has_reject(result)
    assert (ViolationKind.UNVERIFIED, 1, 'warn') in found
    result, found = _kinds('G00 X10 Y2 Z0\n', _bed_profile(), start=None)
    assert [k for k, _, _ in found] == [ViolationKind.UNVERIFIED] and not has_reject(result)
    # the tool center ends inside a forbid area: an unknown Z or tool size does not hide it
    for text in ('G00 X45 Y10\n', '(TOOL DIAMETER: 1.0 mm)\nG00 X45 Y10\n', 'G00 X45 Y10 Z0\n'):
        _, found = _exclusion_lines(text, _area_profile(), start=None)
        assert [area for _, area in found] == ['clamp'], text
    _, found = _exclusion_lines('G00 X45 Y10\n', _area_profile('over', over_z=5.0), start=None)
    assert found == []


def test_unknown_axes_do_not_hide_a_known_axis_out_of_its_travel():
    # X is known after G0 X110 (the offset is known), Y and Z stay unknown from the unknown start
    result, found = _kinds('G00 X110\n', _bed_profile(), start=None)
    assert (ViolationKind.OUT_OF_BED, 1, 'reject') in found and has_reject(result)
    result, found = _kinds('G00 Z-50\n', _bed_profile(), start=None)
    assert (ViolationKind.Z_TRAVEL, 1, 'reject') in found
    result, found = _kinds('G00 X90\nG00 Z-10\n', _bed_profile(), start=None)
    assert not has_reject(result)


def test_the_feed_of_a_block_is_read_in_the_units_of_that_block():
    profile = _bed_profile(feed_xy_max=100.0, rule_modes={'feed': 'reject'})
    # 10 in/min = 254 mm/min
    result, found = _kinds('G21\nG20 G1 X2.01 F10\n', profile)
    assert (ViolationKind.FEED, 2, 'reject') in found
    _, found = _kinds('G21\nG1 X2.01 F10\n', profile)
    assert [k for k, _, _ in found] == []


def test_an_arc_whose_end_is_not_on_its_circle_is_unverified_and_not_corrected():
    from appCommon.machine_profile.correct import CorrectionRefused
    profile = _bed_profile(rule_modes={'unverified': 'reject'})
    # from (50, 20) the center is (60, 20): radius 10 at the start, 20 at the end
    result, found = _kinds('G00 X50 Y20\nG02 X80 Y20 I10 J0 F100\n', profile)
    assert (ViolationKind.UNVERIFIED, 2, 'reject') in found and has_reject(result)
    with pytest.raises(CorrectionRefused, match='not on the arc'):
        _correct('G00 X50 Y20\nG02 X80 Y20 I10 J0 F100\n')
    # a valid arc of the same start and center
    _, found = _kinds('G00 X50 Y20\nG02 X70 Y20 I10 J0 F100\n', profile)
    assert found == []


def test_the_probe_preflight_is_no_cut():
    from appCommon.machine_profile.output import check_probe_moves
    profile = _bed_profile(cut_depth_max=1.0, feed_z_max=50.0, rule_modes={'cut_depth': 'reject', 'feed': 'reject'})
    result = check_probe_moves(profile, _known_setup(), [(10.0, 10.0)], 0.0, -2.0, 'MM', 30.0)
    assert not has_reject(result)
    result = check_probe_moves(profile, _known_setup(), [(10.0, 10.0)], 0.0, -2.0, 'MM', 80.0)
    assert [v.kind for v in result.violations if v.severity == 'reject'] == [ViolationKind.FEED]


def test_z_feed_limit_applies_to_the_z_part_of_diagonal_moves_and_probes():
    profile = _bed_profile(feed_z_max=20.0, rule_modes={'feed': 'reject'})
    # a 45 degree X/Z move at F100 has a Z feed of 70.7 mm/min
    result, found = _kinds('G00 X10 Y10 Z0\nG01 X20 Z-10 F100\n', profile)
    assert (ViolationKind.FEED, 2, 'reject') in found and has_reject(result)
    # a probe at F200 is a Z feed move
    result, found = _kinds('G00 X10 Y10 Z0\nG38.2 Z-10 F200\n', profile)
    assert (ViolationKind.FEED, 2, 'reject') in found
    # a flat XY move at F100 has no Z part; a slow ramp is below the limit
    _, found = _kinds('G00 X10 Y10 Z0\nG01 X20 F100\nG01 X120 Z-1 F100\n', _bed_profile(
        bed_x=200.0, feed_z_max=20.0))
    assert [k for k, _, _ in found if k is ViolationKind.FEED] == []
    # unknown X and Y do not hide the Z feed of a pure Z move
    result, found = _kinds('G00 Z0\nG01 Z-10 F200\n', profile, start=None)
    assert (ViolationKind.FEED, 2, 'reject') in found


def test_cut_depth_counts_the_deepest_point_of_the_move():
    profile = _bed_profile(cut_depth_max=1.0, rule_modes={'cut_depth': 'reject'})
    result, found = _kinds('G01 X10 Y10 Z-0.5 F100\nG01 X20 Z-0.5\n', profile, start=(10.0, 10.0, -3.0))
    assert (ViolationKind.CUT_DEPTH, 1, 'reject') in found and has_reject(result)
    # the same with X and Y unknown
    result, found = _kinds('G00 Z-3\nG01 Z-0.5 F100\n', profile, start=None)
    assert (ViolationKind.CUT_DEPTH, 2, 'reject') in found
    _, found = _kinds('G01 X10 Y10 Z-0.5 F100\n', profile, start=(10.0, 10.0, -0.8))
    assert found == []


def test_a_limit_that_can_not_be_checked_is_unverified_not_checked():
    # a rapid cap needs the G0 speed of the controller
    profile = _bed_profile(rapid_max=1000.0, rule_modes={'unverified': 'reject'})
    result, found = _kinds('G00 X10 Y10\n', profile)
    assert result.status is not CheckStatus.CHECKED and has_reject(result)
    result, _found = _kinds('G00 X10 Y10\n', _bed_profile())
    assert result.status is CheckStatus.CHECKED
    # a feed move under a feed cap with no modal feed
    profile = _bed_profile(feed_xy_max=1000.0)
    result, found = _kinds('G01 X10 Y10\n', profile)
    assert [k for k, _, _ in found] == [ViolationKind.UNVERIFIED] and result.status is CheckStatus.PARTIAL
    result, found = _kinds('G01 X10 Y10 F500\n', profile)
    assert found == [] and result.status is CheckStatus.CHECKED


def test_arc_extreme_outside_bed_is_out_of_bed():
    # both ends inside (y = 40), the top of the circle (y = 41.6) is outside the bed (y <= 41)
    text = 'G00 X40 Y40\nG02 X60 Y40 I10 J-30\n'
    _, found = _kinds(text, _bed_profile(bed_y=41.0))
    assert found == [(ViolationKind.OUT_OF_BED, 2, 'reject')]
    # with the extreme inside the bed nothing is reported
    _, found = _kinds(text, _bed_profile(bed_y=42.0))
    assert found == []
    # a quarter arc stays in X 50..60; P2 adds a full turn that reaches X 70
    text = 'G00 X50 Y20\nG02 X60 Y30 I10 J0 P2 F100\n'
    profile = _bed_profile(bed_x=65.0)
    _, found = _kinds(text, profile)
    assert found == [(ViolationKind.OUT_OF_BED, 2, 'reject')]
    _, found = _kinds(text.replace(' P2', ''), profile)
    assert found == []
    # the correction writes every turn as chords, with no P word
    out = _correct(text, C=_IDENTITY)
    assert ' P' not in out and max(_path(out)[0::2]) > 69.9


def test_profile_none_returns_identical_text_and_none():
    text = 'G00 X999 Y999\n'
    out, result = prepare_output(text, Purpose.FILE, _known_setup(), None)
    assert out is text and result is None


def test_prepare_output_keeps_text_and_checks_it():
    profile = _bed_profile(end=(10.0, 10.0, -5.0), work_offset=(1.0, 2.0, 0.0))
    setup = setup_from_profile(profile, 'default', 'mm')
    assert setup.start == (10.0, 10.0, -5.0) and setup.wcs_offsets == {'G54': (1.0, 2.0, 0.0)}
    text = 'G00 X500\n'
    for purpose in Purpose:
        out, result = prepare_output(text, purpose, setup, profile)
        assert out == text and has_reject(result)
        assert result.assumptions


def test_locked_keys_follow_the_fields_the_profile_sets():
    from appCommon.machine_profile.facts import locked_keys, locked_values
    assert locked_keys(None) == {} and locked_values(None) == {}
    profile = MachineProfile(name='Bench', bed_x=300.0, bed_y=180.0, toolchange=(5.0, 6.0, -2.0))
    note = 'Set by machine profile "Bench"'
    keys = locked_keys(profile)
    assert keys['tools_mill_toolchangexy'] == note and keys['tools_drill_toolchangez'] == note
    assert 'tools_mill_endxy' not in keys and 'tools_mill_ppname_g' not in keys
    assert locked_values(profile)['tools_mill_toolchangexy'] == '5.0, 6.0'


class _Widget:
    def __init__(self):
        self.value, self.enabled, self.tip = 'typed', True, ''

    def set_value(self, value):
        self.value = value

    def setEnabled(self, enabled):
        self.enabled = enabled

    def setToolTip(self, tip):
        self.tip = tip


def _locked_stub(**fields):
    from unittest.mock import MagicMock
    stub = MagicMock()
    stub.locked_form_keys = {key: 'Set by machine profile "B"' for key in fields}
    stub.locked_form_values = {key: 'profile' for key in fields}
    return stub


@pytest.mark.parametrize('plugin', ['milling', 'drilling', 'solderpaste'])
def test_plugin_reload_from_storage_keeps_the_locked_widget_on_the_profile_value(plugin):
    from appPlugins.ToolMilling.Milling import ToolMilling
    from appPlugins.ToolDrilling.Drilling import ToolDrilling
    from appPlugins.ToolSolderPaste.SolderPaste import SolderPaste
    widget = _Widget()
    if plugin == 'milling':
        stub = _locked_stub(tools_mill_endz=1)
        stub.form_fields, stub.general_form_fields = {}, {'tools_mill_endz': widget}
        stub.locked_field_maps.return_value = (stub.form_fields, stub.general_form_fields)
        ToolMilling._set_form_fields(stub, {'tools_mill_endz': 3.0})
    elif plugin == 'drilling':
        stub = _locked_stub(tools_drill_endz=1)
        stub.tool_form_fields, stub.general_form_fields = {}, {'tools_drill_endz': widget}
        stub.locked_field_maps.return_value = (stub.tool_form_fields, stub.general_form_fields)
        stub._set_field = lambda wdg, value: wdg.set_value(value)
        ToolDrilling.storage_to_form(stub, {'tools_drill_endz': 3.0})
    else:
        stub = _locked_stub(tools_solderpaste_z_toolchange=1)
        stub.form_fields = {'tools_solderpaste_z_toolchange': widget}
        stub.locked_field_maps.return_value = (stub.form_fields,)
        SolderPaste.set_form(stub, {'tools_solderpaste_z_toolchange': 3.0})
    assert widget.value == 'profile' and not widget.enabled and widget.tip == 'Set by machine profile "B"'


def test_solder_paste_gcode_uses_the_profile_toolchange(tmp_path):
    from shapely import LineString
    from appCommon.machine_profile.profile import save_profile
    from types import SimpleNamespace
    from appPlugins.ToolSolderPaste.SolderPaste import SolderPaste
    from tests.test_solderpaste import make_job, make_tool
    save_profile(MachineProfile(name='bench', bed_x=300.0, bed_y=300.0, toolchange=(77.0, 88.0, 9.0)), str(tmp_path))
    tool = make_tool([])
    app = tool.app
    app.options.update(global_machine_profile='bench', global_machine_profiles_folder=str(tmp_path),
                       tools_solderpaste_xy_toolchange='1.0, 2.0', tools_solderpaste_z_toolchange=15.0)
    app.data_path = str(tmp_path)
    for tool_data in tool.tooltable_tools.values():
        tool_data['data'].update(app.options)
    job = make_job(app)
    job.kind = 'cncjob'
    tool.new_object = lambda kind, name, init, **kw: init(job, app)
    work = SimpleNamespace(obj_options=dict(xmin=0, ymin=0, xmax=20, ymax=20), tools={1: {
        'tooldia': 0.3, 'solid_geometry': [LineString([(10, 10), (12, 10)])], 'data': dict(app.options)}})
    SolderPaste.on_create_gcode(tool, 'paste', work, use_thread=False)
    code = job.gcode
    assert 'X77.0000 Y88.0000' in code and 'X1.0000 Y2.0000' not in code


# ---------------------------------------------------------------- job facts, provenance, project field (task 1.7)

def _facts_profile(**kw):
    values = dict(name='bench', toolchange=(12.5, 7.0, 30.0), end=(1.0, 2.0, 40.0), work_offset=(2.5, 1.0, -10.0),
                  preprocessor_mill='Marlin',
                  preprocessor_drill='GRBL_11', preprocessor_solderpaste='Paste_2')
    values.update(kw)
    return MachineProfile(**values)


def test_profile_none_leaves_the_options_unchanged():
    from appCommon.machine_profile.facts import resolve_job_facts
    options = {'tools_mill_toolchangexy': '0.0, 0.0', 'tools_mill_ppname_g': 'default'}
    snapshot = resolve_job_facts('mill', options, None)
    assert snapshot == options and snapshot is not options


# the stations are machine positions (M); the job gets them in the work frame: W = M - work offset
@pytest.mark.parametrize('kind, expected', [
    ('mill', {'tools_mill_toolchangexy': '10.0, 6.0', 'tools_mill_toolchangez': 40.0,
              'tools_mill_endxy': '-1.5, 1.0', 'tools_mill_endz': 50.0, 'tools_mill_ppname_g': 'Marlin'}),
    ('drill', {'tools_drill_toolchangexy': '10.0, 6.0', 'tools_drill_toolchangez': 40.0,
               'tools_drill_endxy': '-1.5, 1.0', 'tools_drill_endz': 50.0, 'tools_drill_ppname_e': 'GRBL_11'}),
    ('solderpaste', {'tools_solderpaste_xy_toolchange': '10.0, 6.0', 'tools_solderpaste_z_toolchange': 40.0,
                     'tools_solderpaste_pp': 'Paste_2'}),
])
def test_an_active_profile_overrides_the_machine_facts_in_a_new_dict(kind, expected):
    from appCommon.machine_profile.facts import resolve_job_facts
    options = {'unrelated': 5, **{key: 'old' for key in expected}}
    before = dict(options)
    snapshot = resolve_job_facts(kind, options, _facts_profile())
    assert options == before
    assert snapshot == {**before, **expected}


def test_a_fact_the_profile_does_not_set_keeps_the_option():
    from appCommon.machine_profile.facts import resolve_job_facts
    snapshot = resolve_job_facts('mill', {'tools_mill_toolchangexy': '3, 4', 'tools_mill_ppname_g': 'default'},
                                 MachineProfile(name='bare'))
    assert snapshot == {'tools_mill_toolchangexy': '3, 4', 'tools_mill_ppname_g': 'default'}


def test_provenance_records_name_hash_facts_and_legacy_bed_keys():
    from appCommon.machine_profile.facts import provenance
    profile = _facts_profile()
    clean = provenance(profile, {'a': 1}, {'cncjob_bed_offset_x': 0, 'cncjob_bed_skew_y': 0})
    assert clean == {'name': 'bench', 'hash': content_hash(profile), 'facts': {'a': 1}, 'bed_legacy': False}
    assert provenance(profile, {}, {'cncjob_bed_skew_y': 0.5})['bed_legacy'] is True


def test_a_changed_profile_makes_the_job_stale():
    from appCommon.machine_profile.facts import is_stale, job_provenance
    profile = _facts_profile()
    job_options = {'machine_profile': job_provenance('mill', profile, lambda key: 0)}
    assert is_stale(job_options, profile) is False
    assert is_stale(job_options, _facts_profile(toolchange=(1.0, 1.0, 1.0))) is True
    assert is_stale({}, profile) is None


def test_a_job_without_a_profile_stores_no_provenance():
    from appCommon.machine_profile.facts import job_provenance
    assert job_provenance('mill', None, lambda key: 0) is None


def test_the_project_field_survives_save_and_load_and_a_changed_profile_is_reported(tmp_path):
    from appCommon.machine_profile.facts import project_field, project_mismatch
    profile = _facts_profile()
    assert project_field(None) is None
    saved = json.loads(json.dumps({'machine_profile': project_field(profile)}))['machine_profile']
    assert saved == {'name': 'bench', 'hash': content_hash(profile)}
    assert project_mismatch(saved, profile) is None
    assert 'bench' in project_mismatch(saved, _facts_profile(end=(9.0, 9.0, 9.0)))
    assert project_mismatch(None, profile) is None


# ---------------------------------------------------------------- exclusions in the check (task 2.1)

def _area_profile(strategy='forbid', over_z=None):
    return _bed_profile(exclusions=[{'name': 'clamp', 'polygon': [(40, 0), (50, 0), (50, 20), (40, 20)],
                                     'strategy': strategy, 'over_z': over_z}])


def _exclusion_lines(text, profile, **setup_kw):
    result = check_motion(interpret(text, _known_setup(**setup_kw)), profile)
    return result, [(v.line, v.area) for v in result.violations if v.kind is ViolationKind.EXCLUSION]


def test_rapid_crossing_a_forbid_area_with_both_ends_outside_is_an_exclusion():
    result, found = _exclusion_lines('G00 X10 Y10\nG00 X90 Y10\n', _area_profile())
    assert found == [(2, 'clamp')]
    assert [v.severity for v in result.violations] == ['reject']
    assert result.status is CheckStatus.VIOLATIONS


def test_over_area_passes_above_over_z_and_fails_below():
    text = 'G00 Z10\nG00 X10 Y10\nG00 X90 Y10\nG00 Z-1\nG00 X10 Y10\n'
    _, found = _exclusion_lines(text, _area_profile('over', over_z=5.0))
    assert found == [(5, 'clamp')]
    _, found = _exclusion_lines(text, _area_profile('around', over_z=5.0))
    assert found == [(5, 'clamp')]
    _, found = _exclusion_lines(text, _area_profile('forbid'))
    assert [line for line, _ in found] == [3, 5]


def test_tool_envelope_catches_a_move_beside_the_area_edge():
    text = '(TOOL DIAMETER: 0.2 mm)\nG00 X39.95 Y5\nG01 X39.95 Y15 F100\nG01 X39.5 Y15 F100\nG01 X39.5 Y5 F100\n'
    # the move of line 3 runs 0.05 mm beside the edge; the envelope is 0.1 + 0.1 mm wide
    _, found = _exclusion_lines(text, _area_profile())
    assert [line for line, _ in found] == [2, 3, 4]
    _, found = _exclusion_lines(text.replace('39.95', '39.5'), _area_profile())
    assert found == []


def test_unknown_tool_size_near_the_area_is_partial_and_far_is_checked():
    near, found = _exclusion_lines('G00 X39.95 Y5\nG01 X39.95 Y15 F100\n', _area_profile())
    assert found == []
    assert [v.kind for v in near.violations] == [ViolationKind.UNVERIFIED] * 2
    assert 'tool size unknown near area clamp' in near.violations[0].message
    assert near.status is CheckStatus.PARTIAL
    far, _ = _exclusion_lines('G00 X10 Y5\nG01 X10 Y15 F100\n', _area_profile())
    assert far.violations == [] and far.status is CheckStatus.CHECKED


def test_arc_envelope_follows_the_curve_not_the_chord():
    # the chord (30,10)-(60,10) crosses the area (X 40..50, Y 0..20), but this counter-clockwise half circle
    # runs below it (y about -5), so it passes
    profile = _area_profile()
    _, found = _exclusion_lines('(TOOL DIAMETER: 0.2 mm)\nG00 X30 Y10\nG03 X60 Y10 I15 J0\n', profile)
    assert found == []
    # clockwise through the top of the circle (y = 18 at x = 45): the curve runs inside the area
    _, found = _exclusion_lines('(TOOL DIAMETER: 0.2 mm)\nG00 X30 Y10\nG02 X60 Y10 I15 J-10\n', profile)
    assert [line for line, _ in found] == [3]


# ---------------------------------------------------------------- permanent areas in the router (tasks 2.3, 2.4)

def _router():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from appCommon.Common import ExclusionAreas
    return ExclusionAreas(SimpleNamespace(app_units='MM', log=Mock()))


def _record(shape, strategy, overz=None, name='clamp'):
    return dict(obj_type='profile', shape=shape, strategy=strategy, overz=overz, name=name)


def _inside_grown(route, start, shape, tooldia=1.0):
    from shapely.geometry import LineString
    grown = shape.buffer(tooldia / 2.0 + 0.1, join_style='mitre').buffer(-1e-6, join_style='mitre')
    points = [start] + [tuple(xy) for _z, xy in route]
    return any(LineString([a, b]).intersects(grown) for a, b in zip(points, points[1:]) if a != b)


def test_permanent_areas_are_in_the_job_frame_and_units():
    from appCommon.machine_profile.facts import permanent_areas
    profile = MachineProfile(name='P', work_offset=(5.0, 5.0, 0.0), exclusions=[
        {'name': 'clamp', 'polygon': [(10, 10), (20, 10), (20, 20), (10, 20)], 'strategy': 'forbid', 'over_z': None},
        {'name': 'plate', 'polygon': [(30, 10), (40, 10), (40, 20)], 'strategy': 'over', 'over_z': 12.7},
        {'name': 'line', 'polygon': [(0, 0), (1, 1)], 'strategy': 'forbid', 'over_z': None},
        {'name': 'cap', 'polygon': [(30, 30), (40, 30), (40, 40)], 'strategy': 'over', 'over_z': None}])
    assert permanent_areas(None, 'MM') == []
    areas = permanent_areas(profile, 'MM')
    assert [a['name'] for a in areas] == ['clamp', 'plate', 'cap']
    assert areas[0]['shape'].bounds == (5.0, 5.0, 15.0, 15.0)
    assert (areas[1]['strategy'], areas[1]['overz']) == ('over', 12.7)
    # an 'over' area without an Over Z cannot be crossed
    assert areas[2]['strategy'] == 'forbid'
    inch = permanent_areas(profile, 'IN')
    assert inch[0]['shape'].bounds == pytest.approx((5 / 25.4, 5 / 25.4, 15 / 25.4, 15 / 25.4))
    assert inch[1]['overz'] == pytest.approx(0.5)


def test_forbid_area_is_avoided_with_the_job_areas_off_and_never_crossed_over_z():
    from shapely.geometry import box
    area = box(3, -1, 6, 1)
    exc = _router()
    route = exc.travel_coordinates((0, 0), (10, 0), 1.0, permanent_areas=[_record(area, 'forbid', overz=5.0)],
                                   job_areas=False)
    assert len(route) > 1 and all(z is None for z, _xy in route)
    assert tuple(route[-1][1]) == (10, 0)
    assert not _inside_grown(route, (0, 0), area)
    # the job areas are not used when job_areas is off, and an empty permanent set is the straight travel
    exc.exclusion_areas_storage = [dict(obj_type='geometry', shape=area, strategy='around', overz=5.0)]
    assert exc.travel_coordinates((0, 0), (10, 0), 1.0, permanent_areas=[], job_areas=False) == [[None, (10, 0)]]
    assert len(exc.travel_coordinates((0, 0), (10, 0), 1.0)) > 1


def test_over_area_lifts_before_the_entry_and_stays_up_until_the_exit():
    from shapely.geometry import box
    exc = _router()
    area = _record(box(3, -1, 6, 1), 'over', overz=5.0)
    route = exc.travel_coordinates((0, 0), (10, 0), 1.0, permanent_areas=[area], job_areas=False)
    assert route == [[5.0, pytest.approx((2.4, 0))], [None, pytest.approx((6.6, 0))], [None, (10, 0)]]
    # the Over Z is never below the travel Z of the job
    route = exc.travel_coordinates((0, 0), (10, 0), 1.0, permanent_areas=[area], job_areas=False, travel_z=8.0)
    assert route[0][0] == 8.0


def test_no_detour_around_a_forbid_area_names_the_area():
    from shapely.geometry import box
    exc = _router()
    walls = [_record(box(-3, -3, 3, -2), 'forbid', name='north'), _record(box(-3, 2, 3, 3), 'forbid', name='south'),
             _record(box(-3, -2, -2, 2), 'forbid', name='west'), _record(box(2, -2, 3, 2), 'forbid', name='east')]
    with pytest.raises(ValueError, match='cannot go around'):
        exc.travel_coordinates((0, 0), (10, 0), 1.0, permanent_areas=walls, job_areas=False)
    one = [_record(box(3, -1, 6, 1), 'forbid', name='clamp')]
    with pytest.raises(ValueError, match='clamp'):
        exc.travel_coordinates((2.45, 0), (10, 0), 1.0, permanent_areas=one, job_areas=False)


# ---------------------------------------------------------------- calibration math (task 3.1)

def _simulated_machine(L=100.0, sx=1.003, sy=0.998, e_deg=0.3):
    e = math.radians(e_deg)
    m = [[sx, sy * math.sin(e)], [0.0, sy * math.cos(e)]]

    def cut(x, y):
        return (m[0][0] * x + m[0][1] * y, m[1][0] * x + m[1][1] * y)

    c0, cx, cxy, cy = cut(0, 0), cut(L, 0), cut(L, L), cut(0, L)
    dist = math.dist
    return m, cut, (L, dist(c0, cx), dist(c0, cy), dist(c0, cxy), dist(cx, cy))


def test_calibration_recovers_the_inverse_of_the_simulated_machine():
    from appCommon.machine_profile.calibrate import solve_calibration
    m, _cut, args = _simulated_machine()
    result = solve_calibration(*args)
    det = m[0][0] * m[1][1] - m[0][1] * m[1][0]
    inverse = [[m[1][1] / det, -m[0][1] / det], [-m[1][0] / det, m[0][0] / det]]
    for row in range(2):
        assert result['matrix'][row] == pytest.approx(inverse[row], abs=1e-9)
        assert result['machine_matrix'][row] == pytest.approx(m[row], abs=1e-9)
    assert result['sx'] == pytest.approx(1.003) and result['sy'] == pytest.approx(0.998)
    assert result['epsilon_deg'] == pytest.approx(0.3, abs=1e-9)
    assert result['residual'] < 1e-9 and result['date']


def test_corrected_square_is_cut_within_001_mm_after_output_rounding():
    from appCommon.machine_profile.calibrate import apply_matrix, pattern_square, solve_calibration
    _m, cut, args = _simulated_machine()
    matrix = solve_calibration(*args)['matrix']
    for x, y in pattern_square(100.0):
        cx, cy = apply_matrix(matrix, x, y)
        mx, my = cut(round(cx, 4), round(cy, 4))
        assert math.hypot(mx - x, my - y) < 0.01
    assert pattern_square(100.0) == [(0, 0), (100.0, 0), (100.0, 100.0), (0, 100.0)]


@pytest.mark.parametrize('args, text', [
    ((100.0, 0.0, 100.0, 141.0, 141.0), 'greater than zero'),
    ((100.0, 100.0, 100.0, 250.0, 141.0), 'triangle'),
    ((100.0, 100.0, 100.0, 141.0, 250.0), 'triangle'),
    ((100.0, 100.0, 100.0, math.sqrt(21000.0), math.sqrt(19000.0)), 'skew'),
    ((100.0, 95.0, 100.0, math.sqrt(19025.0), math.sqrt(19025.0)), 'scale'),
    ((100.0, 100.0, 100.0, 150.0, 150.0), 'measure'),
])
def test_calibration_refuses_bad_measurements(args, text):
    from appCommon.machine_profile.calibrate import CalibrationError, solve_calibration
    with pytest.raises(CalibrationError, match=text):
        solve_calibration(*args)


# ---------------------------------------------------------------- the correction (task 3.2)

_SHEAR = [[1.0, 0.01], [0.0, 1.0]]
_IDENTITY = [[1.0, 0.0], [0.0, 1.0]]


def _correct(text, C=_SHEAR, decimals=4, **setup_kw):
    from appCommon.machine_profile.correct import correct_program
    return correct_program(text, _known_setup(**setup_kw), C, decimals)


def _path(text):
    return [v for r in interpret(text, _known_setup()) for v in r.end_w[:2]]


def _distance_to_polyline(p, poly):
    best = float('inf')
    for (ax, ay), (bx, by) in zip(poly, poly[1:]):
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        u = 0.0 if ll == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ll))
        best = min(best, math.hypot(p[0] - ax - u * dx, p[1] - ay - u * dy))
    return best


def test_fixed_station_keeps_its_machine_position_and_the_same_xy_rapid_is_mapped():
    text = ('(MP:FIXED BEGIN toolchange)\nG00 Z15.0000\nG00 X10.0000 Y10.0000\nT1\nM6\nG00 Z15.0000\n'
            '(MP:FIXED END)\nG00 X10.0000 Y10.0000\n')
    out = _correct(text).splitlines()
    assert out[:7] == text.splitlines()[:7]
    assert out[7] == 'G00 X10.1000 Y10.0000'


# ---------------------------------------------------------------- calibration wizard and blocks (tasks 3.5, 3.6)

_CAL_MATRIX = [[1.0, 0.01], [0.0, 1.0]]


def _cal_app(tmp_path, enabled=True, matrix=True):
    """An app stand-in with a saved profile 'Cal' (its calibration on, off, or none) as the active one."""
    from types import SimpleNamespace
    calibration = {'matrix': _CAL_MATRIX, 'date': '2026-11-24', 'residual': 0.01, 'enabled': enabled} if matrix else {}
    save_profile(MachineProfile(name='Cal', bed_x=300.0, bed_y=300.0, z_travel=40.0, work_offset=(0.0, 0.0, -20.0),
                                calibration=calibration), str(tmp_path))
    return SimpleNamespace(options={'global_machine_profile': 'Cal', 'global_machine_profiles_folder': str(tmp_path)},
                           data_path='', app_units='MM')


def _square_path(text):
    return [(r.end_w[0], r.end_w[1]) for r in interpret(text, _known_setup()) if r.end_w[0] is not None]


def test_pattern_gcode_is_the_square_from_the_zero_corner():
    from appCommon.machine_profile.calibrate import pattern_gcode
    text = pattern_gcode(100.0, -0.1, 2.0, 120.0, 4)
    records = interpret(text, _known_setup())
    assert all(r.verified for r in records)
    cut = [(r.end_w[0], r.end_w[1]) for r in records if r.end_w[2] == -0.1 and r.motion_mode == 'G1']
    assert cut == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    assert min(r.end_w[2] for r in records) == -0.1 and records[-1].end_w[2] == 2.0
    assert 'F120' in text


def test_calibration_file_is_never_corrected_and_the_check_cut_is(tmp_path):
    from appCommon.machine_profile.calibrate import apply_matrix, pattern_gcode
    from appCommon.machine_profile.writer import calibration_file_text, gate_file_text
    app = _cal_app(tmp_path)
    folder = app.options['global_machine_profiles_folder']
    text = pattern_gcode(100.0, -0.1, 2.0, 120.0, 4)
    gate = calibration_file_text('Cal', folder, '', text, None, 'MM')
    assert gate.ok and gate.text.splitlines()[0] == '(Machine profile: Cal, uncorrected (calibration))'
    assert gate.text.split('\n', 1)[1] == text
    assert _square_path(gate.text) == _square_path(text)
    # the check cut: the same square as a file output is corrected by the matrix
    check = gate_file_text('Cal', folder, '', text, None, 'MM', correct=True, uncorrected_source=True)
    assert check.ok and check.text is not None
    for (x, y), (cx, cy) in zip(_square_path(text), _square_path(check.text)):
        assert (cx, cy) == pytest.approx(apply_matrix(_CAL_MATRIX, x, y), abs=1e-4)


def test_calibration_file_without_a_profile_is_the_text_unchanged():
    from appCommon.machine_profile.writer import calibration_file_text
    gate = calibration_file_text('None', '', '', 'G00 X1\n', None, 'MM')
    assert gate.ok and gate.text is None


def test_correction_on_follows_an_active_profile_with_an_enabled_calibration(tmp_path):
    from types import SimpleNamespace
    from appGUI.machine_profile_ui import correction_on
    (tmp_path / 'on').mkdir()
    assert correction_on(_cal_app(tmp_path / 'on', enabled=True)) is True
    (tmp_path / 'off').mkdir()
    assert correction_on(_cal_app(tmp_path / 'off', enabled=False)) is False
    (tmp_path / 'none').mkdir()
    assert correction_on(_cal_app(tmp_path / 'none', matrix=False)) is False
    assert correction_on(SimpleNamespace(options={'global_machine_profile': 'None'}, data_path='')) is False


def test_levelled_design_text_keeps_every_z_when_corrected():
    # R10: levelling changed Z per point; the XY correction must not touch one Z value
    profile = MachineProfile(name='Lv', calibration={'matrix': _CAL_MATRIX, 'enabled': True})
    text = ('G21\nG90\nG00 Z2.0000\nG00 X0.0000 Y0.0000\nG01 Z-0.0830 F100\nG01 X10.0000 Y5.0000 Z-0.0912\n'
            'G01 X20.0000 Y5.0000 Z-0.1047\nG01 X20.0000 Y15.0000 Z-0.0766\nG00 Z2.0000\n')
    out, _result = prepare_output(text, Purpose.FILE, setup_from_profile(profile, None, 'mm'), profile)
    before = [r.end_w for r in interpret(text, _known_setup())]
    after = [r.end_w for r in interpret(out, _known_setup())]
    assert [z for _x, _y, z in after] == [z for _x, _y, z in before]
    assert [(x, y) for x, y, _z in after] != [(x, y) for x, y, _z in before]


def test_arc_becomes_explicit_g01_chords_within_the_tolerance_and_the_inheriting_block_is_not_g01():
    text = 'G00 X0 Y10\nG02 X10 Y0 I0 J-10\nX0 Y-10 I-10 J0\nG00 Z5\n'
    out = _correct(text)
    lines = out.splitlines()
    assert lines[0] == 'G00 X0.1000 Y10.0000' and lines[-1] == 'G00 Z5'
    assert len(lines) > 6 and not any(w in out for w in ('I', 'J', 'G02', 'G2 '))
    assert all(line.startswith('G01 ') for line in lines[1:-1])
    records = interpret(out, _known_setup())
    assert all(r.motion_mode == 'G1' for r in records[1:-1])
    poly = [r.end_w[:2] for r in records[:-1]]
    for a0, a1 in ((90.0, 0.0), (0.0, -90.0)):
        for k in range(201):
            ang = math.radians(a0 + (a1 - a0) * k / 200.0)
            x, y = 10 * math.cos(ang), 10 * math.sin(ang)
            assert _distance_to_polyline((x + 0.01 * y, y), poly) <= 0.005
    assert poly[-1] == pytest.approx((-0.1, -10.0))


def test_y_only_move_under_a_shear_gets_an_x_word():
    out = _correct('G00 X10 Y10\nG01 Y20 F100\n')
    assert _path(out) == pytest.approx([10.1, 10.0, 10.2, 20.0])
    assert 'X10.2000' in out.splitlines()[1] and 'F100' in out.splitlines()[1]


@pytest.mark.parametrize('text, line, word', [
    ('G00 X1 Y1\nG91\nG00 X1\n', 2, 'G91'),
    ('G00 X1 Y1\nG00 X2\nG93\n', 3, 'G93'),
    ('G00 X1 Y1\nG02 X5 Y5 R5\n', 2, 'R5'),
    ('G00 X1 Y1\nG18\n', 2, 'G18'),
    ('G00 X1\nG54\nG00 X2\n', 2, 'G54'),
    ('G00 X1\nG01 X[#1]\n', 2, 'X[#1]'),
])
def test_a_refused_form_names_its_line_and_word(text, line, word):
    from appCommon.machine_profile.correct import CorrectionRefused
    with pytest.raises(CorrectionRefused) as info:
        _correct(text)
    assert (info.value.line, info.value.word) == (line, word)


def test_too_few_decimals_refuse_an_arc():
    from appCommon.machine_profile.correct import CorrectionRefused
    with pytest.raises(CorrectionRefused, match='decimals'):
        _correct('G00 X0 Y10\nG02 X10 Y0 I0 J-10\n', decimals=1)


def test_non_motion_lines_keep_their_text_and_order():
    text = ('(Units: MM)\nG21\nG90\nG17\nG94\nG01 F100.00\nM5\nG00 Z15.0000\nT1\nM6\n(MSG, Tool change)\nM0\n'
            'M03 S10000.0\nG4 P1.0\nM8\nG01 X5.5 Y5.5\n; a note\nG00 Z2.0\nM05\nM9\nM30\n')
    out = _correct(text).splitlines()
    src = text.splitlines()
    assert len(out) == len(src)
    assert [o for o, s in zip(out, src) if o != s] == ['G01 X5.5550 Y5.5000']


def test_identity_matrix_leaves_the_motion_values_unchanged():
    text = 'G00 X1.5 Y2.5\nG01 X3 F100\nG01 Y4\nG03 X0 Y4 I-1.5 J0\n'
    out = _correct(text, C=_IDENTITY)
    assert _path(out)[:6] == pytest.approx(_path(text)[:6])
    assert _path(out)[-2:] == pytest.approx([0.0, 4.0])


# ---------------------------------------------------------------- markers and correction on (tasks 3.3, 3.4)

def _solder_code(tmp_path, profile_name):
    from shapely import LineString
    from types import SimpleNamespace
    from appPlugins.ToolSolderPaste.SolderPaste import SolderPaste
    from tests.test_solderpaste import make_job, make_tool
    save_profile(MachineProfile(name='bench', bed_x=300.0, bed_y=300.0, toolchange=(77.0, 88.0, 9.0),
                                calibration={'enabled': True, 'matrix': _SHEAR}), str(tmp_path))
    tool = make_tool([])
    app = tool.app
    app.options.update(global_machine_profile=profile_name, global_machine_profiles_folder=str(tmp_path),
                       tools_solderpaste_xy_toolchange='1.0, 2.0', tools_solderpaste_z_toolchange=15.0)
    app.data_path = str(tmp_path)
    for tool_data in tool.tooltable_tools.values():
        tool_data['data'].update(app.options)
    job = make_job(app)
    job.kind = 'cncjob'
    job._bed_offset_x, job._bed_skew_y = 3.0, 0.5
    tool.new_object = lambda kind, name, init, **kw: init(job, app)
    work = SimpleNamespace(obj_options=dict(xmin=0, ymin=0, xmax=20, ymax=20), tools={1: {
        'tooldia': 0.3, 'solid_geometry': [LineString([(10, 10), (12, 10)])], 'data': dict(app.options)}})
    SolderPaste.on_create_gcode(tool, 'paste', work, use_thread=False)
    return job


def test_generators_mark_the_fixed_sequences_only_under_a_profile_and_drop_the_bed_keys(tmp_path):
    job = _solder_code(tmp_path, 'bench')
    lines = job.gcode.splitlines()
    begin = lines.index('(MP:FIXED BEGIN toolchange)')
    assert 'X77.0000 Y88.0000' in '\n'.join(lines[begin:lines.index('(MP:FIXED END)', begin)])
    assert '(MP:FIXED BEGIN end)' in lines
    assert lines.count('(MP:FIXED END)') == 2
    assert job.obj_options['machine_profile']['bed_legacy'] is False
    assert (job._bed_offset_x, job._bed_skew_y) == (0.0, 0.0)
    plain = _solder_code(tmp_path, 'None')
    assert 'MP:FIXED' not in plain.gcode and 'machine_profile' not in plain.obj_options
    assert (plain._bed_offset_x, plain._bed_skew_y) == (3.0, 0.5)


def _calibrated(**kw):
    return MachineProfile(name='Cal', bed_x=300.0, bed_y=300.0, z_travel=50.0, work_offset=(0.0, 0.0, -20.0),
                          calibration={'enabled': True, 'matrix': _SHEAR, **kw})


_MARKED = ('(MP:FIXED BEGIN toolchange)\nG00 Z15.0000\nG00 X10.0000 Y10.0000\nT1\nM6\nG00 Z15.0000\n'
           '(MP:FIXED END)\nG00 X10.0000 Y10.0000\nG01 X20.0000 Y10.0000 F100\n')


def test_prepare_output_corrects_only_a_file_with_the_calibration_on():
    profile = _calibrated()
    setup = setup_from_profile(profile, 'default', 'mm')
    text, result = prepare_output(_MARKED, Purpose.FILE, setup, profile)
    lines = text.splitlines()
    assert lines[0].startswith('(Machine profile: Cal, %s, XY corrected)' % content_hash(profile)[:12])
    assert lines[1:8] == _MARKED.splitlines()[:7]
    assert lines[8:] == ['G00 X10.1000 Y10.0000', 'G01 X20.1000 Y10.0000 F100']
    assert not has_reject(result) and 'XY corrected' in ' '.join(result.assumptions)
    for purpose in (Purpose.DESIGN, Purpose.CALIBRATION):
        assert prepare_output(_MARKED, purpose, setup, profile)[0] == _MARKED
    assert prepare_output(_MARKED, Purpose.STREAM, setup, profile)[0] == text      # the stream is corrected like a file
    assert prepare_output(_MARKED, Purpose.STREAM, setup, profile, correct=False)[0] == _MARKED
    off = _calibrated(enabled=False)
    assert prepare_output(_MARKED, Purpose.FILE, setup, off)[0] == _MARKED
    assert prepare_output(_MARKED, Purpose.FILE, setup, profile, correct=False)[0] == _MARKED


def test_a_percent_line_stays_first_and_the_final_check_reads_the_corrected_text():
    profile = MachineProfile(name='Cal', bed_x=10.2, bed_y=300.0, z_travel=50.0, work_offset=(0.0, 0.0, -20.0),
                             calibration={'enabled': True, 'matrix': _SHEAR})
    setup = setup_from_profile(profile, 'default', 'mm')
    text, result = prepare_output('%\nG00 X10 Y10\n%\n', Purpose.FILE, setup, profile)
    assert text.splitlines()[0] == '%' and text.splitlines()[1].startswith('(Machine profile')
    assert not has_reject(result)
    # X 10.2 is on the bed edge; after the correction it is 10.3, outside: the check reads the corrected X
    edge = 'G00 Z5\nG00 X5 Y5\nG00 X10.2 Y10\n'
    _, result = prepare_output(edge, Purpose.FILE, setup, profile)
    assert has_reject(result)
    assert not has_reject(prepare_output(edge, Purpose.FILE, setup, profile, correct=False)[1])


def test_a_levelled_job_keeps_its_levelled_z_after_the_correction():
    profile = _calibrated()
    setup = setup_from_profile(profile, 'default', 'mm')
    levelled = 'G21\nG90\nG00 Z5.0000\nG00 X10.0000 Y10.0000\nG01 X11.0000 Y10.0000 Z-0.1234 F100\nG01 X12.0000 Y10.0000 Z-0.0987\n'
    text, _ = prepare_output(levelled, Purpose.FILE, setup, profile)
    assert text.splitlines()[5:] == ['G01 X11.1000 Y10.0000 Z-0.1234 F100', 'G01 X12.1000 Y10.0000 Z-0.0987']


def _gate(tmp_path, text, **kw):
    from appCommon.machine_profile.writer import gate_file_text
    save_profile(_calibrated(), str(tmp_path))
    return gate_file_text('Cal', str(tmp_path), '', text, 'default', 'MM', correct=True, **kw)


def test_the_gate_corrects_by_the_provenance_of_the_job(tmp_path):
    record = {'name': 'Cal', 'hash': 'x', 'facts': {}, 'bed_legacy': False}
    gate = _gate(tmp_path, 'G00 X10 Y10\n', provenance=record)
    assert gate.ok and gate.text.splitlines()[1] == 'G00 X10.1000 Y10.0000'
    legacy = _gate(tmp_path, 'G00 X10 Y10\n', provenance=dict(record, bed_legacy=True))
    assert not legacy.ok and 'regenerate the job' in legacy.error and legacy.text is None
    unknown = _gate(tmp_path, 'G00 X10 Y10\n')
    assert unknown.ok and unknown.text is None and unknown.ask_source
    assert unknown.source_note == 'not corrected: the source has unknown provenance'
    declared = _gate(tmp_path, 'G00 X10 Y10\n', uncorrected_source=False)
    assert declared.text is None and not declared.ask_source and 'declared as not to correct' in declared.source_note
    assert _gate(tmp_path, 'G00 X10 Y10\n', uncorrected_source=True).text.splitlines()[1] == 'G00 X10.1000 Y10.0000'


def test_a_refused_correction_writes_no_file_and_names_the_line_and_word(tmp_path):
    gate = _gate(tmp_path, 'G00 X1 Y1\nG93\n', provenance={'name': 'Cal', 'hash': 'x', 'facts': {}, 'bed_legacy': False})
    assert not gate.ok and 'line 2, G93' in gate.error


def test_a_writer_that_does_not_ask_for_the_correction_is_only_checked(tmp_path):
    from appCommon.machine_profile.writer import gate_file_text
    save_profile(_calibrated(), str(tmp_path))
    gate = gate_file_text('Cal', str(tmp_path), '', 'G00 X10 Y10\n', 'default', 'MM')
    assert gate.ok and gate.text is None and gate.source_note is None


# ---------------------------------------------------------------- setup from the controller (task 4.6)

def test_setup_from_controller_keeps_unknown_items_none_and_says_live_state():
    from appCommon.machine_profile.output import setup_from_controller
    profile = _bed_profile()
    setup = setup_from_controller(profile, 'GRBL_11', 'mm', (1.0, 2.0, 3.0), {'G54': (5.0, 6.0, 7.0)}, 'G54',
                                  (0.0, 0.0, 0.0), 0.5)
    assert setup.start == (1.0, 2.0, 3.0) and setup.active_wcs == 'G54' and setup.tlo == 0.5
    assert setup.wcs_offsets == {'G54': (5.0, 6.0, 7.0)} and setup.units == 'mm' and setup.dialect == 'GRBL_11'
    assert setup.profile_hash == content_hash(profile)
    unknown = setup_from_controller(profile, None, None, None, None, None, None, None)
    assert (unknown.start, unknown.wcs_offsets, unknown.active_wcs, unknown.g92, unknown.tlo, unknown.units) == \
           (None,) * 6
    _, result = prepare_output('G00 X10\n', Purpose.STREAM, unknown, profile)
    assert 'live state of the controller' in ' '.join(result.assumptions)
    assert 'start position: unknown' in result.assumptions


class _FakeController:
    """Stands in for the Sender: GRBL / E-Cut text answers of the controller."""

    def __init__(self, answers, status='<Idle|MPos:10.000,20.000,5.000|FS:0,0>', connected=True, inches=False):
        self.answers = answers
        self.status = status
        self.asked = []
        self._grbl_connected = connected
        self._grbl_report_inches = inches
        self.grbl_ser_port = object() if connected else None
        self._live_cache = None
        self.live_setup_key = None

    def live_setup(self, op, profile, dialect=None, refresh=False):
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
        return ToolGCodeSender.live_setup(self, op, profile, dialect, refresh)

    def _grbl_transact(self, op, command, timeout=10.0, echo=True, guard=None):
        self.asked.append(command)
        if command not in self.answers:
            return '', 'timeout'
        return self.answers[command], 'ok'

    def _grbl_query_status(self, op, timeout=2.0):
        return self.status


_GRBL_ANSWERS = {
    '$G': '[GC:G0 G55 G17 G21 G90 G94 M5 M9 T0 F0 S0]\nok\n',
    '$#': '[G54:-100.000,-50.000,-20.000]\n[G55:-10.000,-5.000,-2.000]\n[G28:0.000,0.000,0.000]\n'
          '[G92:1.000,2.000,3.000]\n[TLO:0.500]\n[PRB:0.000,0.000,0.000:0]\nok\n',
}


def _live(fake, profile=None, **kw):
    from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
    return ToolGCodeSender.live_setup(fake, None, profile or _bed_profile(), 'GRBL_11', **kw)


def test_sender_live_setup_reads_each_item_of_the_grbl_controller_separately():
    fake = _FakeController(dict(_GRBL_ANSWERS))
    setup = _live(fake)
    assert setup.start == (10.0, 20.0, 5.0) and setup.units == 'mm' and setup.active_wcs == 'G55'
    assert setup.wcs_offsets['G54'] == (-100.0, -50.0, -20.0) and setup.wcs_offsets['G55'] == (-10.0, -5.0, -2.0)
    assert setup.g92 == (1.0, 2.0, 3.0) and setup.tlo == 0.5 and setup.dialect == 'GRBL_11'
    assert fake.live_setup_key is not None


def test_sender_live_setup_requests_the_reports_once_and_a_missing_answer_is_unknown():
    fake = _FakeController(dict(_GRBL_ANSWERS))
    _live(fake)
    _live(fake)
    assert fake.asked.count('$G') == 1 and fake.asked.count('$#') == 1
    silent = _FakeController({'$G': _GRBL_ANSWERS['$G']}, status='')
    setup = _live(silent)
    assert setup.active_wcs == 'G55' and setup.start is None
    assert (setup.wcs_offsets, setup.g92, setup.tlo) == (None, None, None)


def test_sender_live_setup_of_the_ecut_port_and_not_connected():
    ecut = _FakeController({
        '$G': '[GC:G0 G54 G17 G21 G90 G94 M5 M9 T1 F0 S0]\nok\n',
        '$#': '[G54:1.000,2.000,3.000]\n[G55:0.000,0.000,0.000]\n[G28:0.000,0.000,0.000]\n[G30:0.000,0.000,0.000]\n'
              '[G92:0.000,0.000,0.000]\n[TLO:0.000]\n[MSG:Probe result unavailable]\nok\n'})
    setup = _live(ecut)
    assert setup.active_wcs == 'G54' and setup.wcs_offsets['G54'] == (1.0, 2.0, 3.0)
    assert setup.g92 == (0.0, 0.0, 0.0) and setup.tlo == 0.0
    assert _live(_FakeController(dict(_GRBL_ANSWERS), connected=False)) is None


def test_sender_live_setup_key_changes_with_state_and_profile():
    from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
    fake = _FakeController(dict(_GRBL_ANSWERS))
    profile = _bed_profile()
    _live(fake, profile)
    key = fake.live_setup_key
    assert ToolGCodeSender.live_setup_changed(fake, key, None, profile, 'GRBL_11') is False
    fake.answers['$#'] = _GRBL_ANSWERS['$#'].replace('[G92:1.000,2.000,3.000]', '[G92:0.000,0.000,0.000]')
    assert ToolGCodeSender.live_setup_changed(fake, key, None, profile, 'GRBL_11') is True
    assert ToolGCodeSender.live_setup_changed(fake, fake.live_setup_key, None, _bed_profile(bed_x=200.0),
                                              'GRBL_11') is True


@pytest.mark.parametrize('outcome', ['warning_declined', 'reject', 'refused'])
def test_start_profile_outcomes(outcome):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    from appCommon.machine_profile.correct import CorrectionRefused
    from appCommon.machine_profile.records import CheckResult, Violation
    from appPlugins.ToolGcodeSender import Sender as sender_module
    from tests.grbl_stream_helpers import CheckFake, make_stream_tool

    fake = CheckFake()
    tool = make_stream_tool(fake)
    tool._profile_setup = lambda op, profile, target: (Setup(), [], None)
    tool._confirm_profile_stream = MagicMock(return_value=False)
    prepared = []
    tool.job_prepared_sig.emit.side_effect = prepared.append

    def fake_output(text, *args, **kwargs):
        if outcome == 'refused':
            raise CorrectionRefused(3, 'G91', 'relative moves')
        severity = 'reject' if outcome == 'reject' else 'warn'
        return text, CheckResult(violations=[Violation(ViolationKind.FEED, 2, 'too fast', severity=severity)])

    with patch.object(sender_module, 'active_profile_of', return_value=SimpleNamespace(name='P', rapid_max=None)), \
            patch.object(sender_module, 'prepare_output', fake_output), \
            patch.object(sender_module, 'report_gate') as report:
        tool.on_start()
        tool._on_job_prepared(prepared[0])
    assert [c for c in fake.commands() if c.startswith('G')] == []
    assert tool._grbl_op is None
    assert tool.ui.start_button.setDisabled.call_args_list[-1].args[0] is False
    messages = ' '.join(str(call) for call in tool.app.inform.emit.call_args_list)
    if outcome == 'warning_declined':
        tool._confirm_profile_stream.assert_called_once()
        assert report.call_count == 1
    elif outcome == 'reject':
        tool._confirm_profile_stream.assert_not_called()
        assert report.call_count == 1 and 'Line 2' in messages
    else:
        tool._confirm_profile_stream.assert_not_called()
        assert report.call_count == 1 and 'Correction refused at line 3, G91: relative moves' in messages


_GC_G90 = 'G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0'


def _macro_tool(gc_answers, mpos='10.000,20.000,-5.000', threaded=False):
    """A Sender on a fake controller (the answers of $G in order, the last one repeats), with a 200 mm bed."""
    import threading
    from unittest.mock import MagicMock, patch
    from appPlugins.ToolGcodeSender import Sender as sender_module
    from tests.grbl_stream_helpers import make_stream_tool
    from tests.test_levelling_audit import FakeGrbl

    class Modal(FakeGrbl):
        def _answer(self, cmd):
            if cmd == '$G':
                text = gc_answers.pop(0) if len(gc_answers) > 1 else gc_answers[0]
                self._send('[GC:%s]' % text, 'ok')
            elif cmd == '?':
                self._send('<Idle|MPos:%s|FS:0,0>' % mpos)
            else:
                super()._answer(cmd)

    fake = Modal()
    tool = make_stream_tool(fake)
    tool._plugin_active = True
    tool._grbl_report_inches = False
    tool.op_changed_sig.emit.side_effect = tool._apply_machine_state
    tool.macro_check_sig = MagicMock()
    threads = []
    if threaded:
        tool.app.worker_task.emit.side_effect = lambda task: threads.append(threading.Thread(target=task['fcn']))
    else:
        tool.macro_check_sig.emit.side_effect = tool._on_macro_check
    tool._confirm_profile_stream = MagicMock(return_value=True)
    fake.written.clear()
    patches = (patch.object(sender_module, 'active_profile_of', return_value=_bed_profile(bed_x=200.0)),
               patch.object(sender_module, 'report_gate'))
    return tool, fake, threads, patches


def _macro_written(fake, line):
    return [c for c in fake.commands() if c == line]


@pytest.mark.parametrize('line, gc, mpos, written', [
    ('G0 X500', _GC_G90, '10.000,20.000,-5.000', False),
    ('X500', 'G1 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0', '10.000,20.000,-5.000', False),
    ('X20', 'G0 G54 G17 G21 G91 G94 M5 M9 T0 F0 S0', '190.000,20.000,-5.000', False),
    ('G0 X50', _GC_G90, '10.000,20.000,-5.000', True)])
def test_macro_outside_the_bed_is_refused(line, gc, mpos, written):
    tool, fake, _, patches = _macro_tool([gc], mpos)
    tool._stream_setup, tool._stream_setup_key = 'job setup', 'job key'
    with patches[0], patches[1]:
        tool.run_macro([line])
    assert bool(_macro_written(fake, line)) is written
    assert (tool._stream_setup, tool._stream_setup_key) == ('job setup', 'job key')


def test_macro_refuses_when_the_machine_state_changed_after_the_check():
    tool, fake, _, patches = _macro_tool([_GC_G90, _GC_G90.replace('G54', 'G55')])
    with patches[0], patches[1]:
        tool.run_macro(['G0 X50'])
    assert _macro_written(fake, 'G0 X50') == []
    assert 'machine state changed' in ' '.join(str(c) for c in tool.app.inform.emit.call_args_list)


def test_stop_during_the_approval_releases_the_operation_and_a_late_yes_writes_nothing():
    import time
    tool, fake, threads, patches = _macro_tool([_GC_G90], threaded=True)
    with patches[0], patches[1]:
        tool.run_macro(['G0 X50'])
        thread = threads[0]
        thread.start()
        deadline = time.monotonic() + 30.0
        while not tool.macro_check_sig.emit.called and time.monotonic() < deadline:
            time.sleep(0.01)
        assert tool.macro_check_sig.emit.called
        tool._grbl_op.cancel.set()
        thread.join(30.0)
        assert not thread.is_alive() and tool._grbl_op is None
        tool._on_macro_check(*tool.macro_check_sig.emit.call_args.args)
    assert _macro_written(fake, 'G0 X50') == []


def test_map_point_and_unmap_point_round_trip_and_are_identity_without_the_correction():
    from appCommon.machine_profile.output import calibration_hash, map_point, unmap_point
    profile = _profile()
    profile.calibration['matrix'] = [[1.0, 0.01], [0.0, 0.99]]
    x, y = map_point(profile, 50.0, 20.0)
    assert (x, y) == pytest.approx((50.2, 19.8))
    assert unmap_point(profile, x, y) == pytest.approx((50.0, 20.0))
    profile.calibration['enabled'] = False
    assert map_point(profile, 50.0, 20.0) == (50.0, 20.0)
    assert unmap_point(profile, 50.0, 20.0) == (50.0, 20.0)
    assert map_point(None, 1.0, 2.0) == (1.0, 2.0)
    assert calibration_hash(profile) is None and calibration_hash(None) is None
    profile.calibration['enabled'] = True
    other = _profile()
    other.calibration['matrix'] = [[1.0, 0.02], [0.0, 0.99]]
    assert len(calibration_hash(profile)) == 12 and calibration_hash(profile) != calibration_hash(other)


def test_probe_header_names_the_calibration_and_is_empty_without_it():
    from appCommon.machine_profile.output import calibration_hash, parse_probe_header, probe_header
    profile = _profile()
    header = probe_header(profile, 'MM')
    assert header == '(MP:PROBE hash=%s units=mm frame=G54)\n' % calibration_hash(profile)
    assert parse_probe_header('G21\n' + header + 'G0 X1\n') == {
        'hash': calibration_hash(profile), 'units': 'mm', 'frame': 'G54'}
    assert parse_probe_header('0,0,-0.01\n') is None
    assert probe_header(None, 'MM') == '' and 'units=inch' in probe_header(profile, 'IN')


def test_probe_moves_are_checked_against_the_bed():
    from appCommon.machine_profile.output import check_probe_moves, has_reject, setup_from_profile
    profile = _bed_profile(work_offset=(0.0, 0.0, -20.0))
    setup = setup_from_profile(profile, None, 'mm')
    inside = check_probe_moves(profile, setup, [(10.0, 10.0)], 2.0, -1.0, 'MM')
    assert not has_reject(inside)
    outside = check_probe_moves(profile, setup, [(10.0, 10.0), (900.0, 5.0)], 2.0, -1.0, 'MM')
    assert has_reject(outside)


# ---------------------------------------------------------------- E-Cut axis fields (task 4.5)

_ECUT_CURRENT = {name: {'axis_velocity_max': 3000.0, 'axis_accel_max': 200.0,
                        'soft_limit_min': 0.0, 'soft_limit_max': 300.0} for name in 'XYZ'}


def test_a_profile_without_the_ecut_fields_keeps_its_dict_and_hash(tmp_path):
    profile = _profile()
    assert not any(key in to_dict(profile) for key in (
        'axis_velocity_max', 'axis_accel_max', 'soft_limit_min', 'soft_limit_max'))
    old = to_dict(profile)
    assert from_dict(old) == profile and content_hash(from_dict(old)) == content_hash(profile)
    # the hash of a profile without the fields is the one of before the fields existed
    assert content_hash(MachineProfile(name='A', bed_x=1.0)) == \
        'c1f207dd48c0efb988202e8e435e6df1ec106f1f4b380e4dcef88cb08adbe100'


def test_the_ecut_fields_round_trip_and_change_the_hash(tmp_path):
    profile = _profile()
    with_axes = from_dict({**to_dict(profile), 'axis_velocity_max': {'X': 3000.0, 'Z': 600.0},
                           'soft_limit_min': {'Z': -40.0}, 'soft_limit_max': {'Z': 0.0, 'X': 300.0}})
    path = save_profile(with_axes, str(tmp_path))
    loaded = load_profile(path)
    assert loaded == with_axes
    assert loaded.axis_velocity_max == {'X': 3000.0, 'Z': 600.0} and loaded.axis_accel_max is None
    assert content_hash(loaded) == content_hash(with_axes) != content_hash(profile)


def test_an_unknown_axis_name_in_the_ecut_fields_is_refused():
    with pytest.raises(ValueError, match='axis'):
        from_dict({**to_dict(_profile()), 'axis_accel_max': {'Q': 5.0}})


def test_ecut_axis_updates_gives_only_the_set_fields_and_axes():
    from appCommon.machine_profile.ecut_binding import ecut_axis_updates
    assert ecut_axis_updates(_profile(), _ECUT_CURRENT) == {}
    profile = MachineProfile(name='E', axis_velocity_max={'X': 1800.0}, soft_limit_min={'Z': -40.0},
                             soft_limit_max={'Z': 0.0})
    assert ecut_axis_updates(profile, _ECUT_CURRENT) == {
        'X': {'axis_velocity_max': 1800.0}, 'Z': {'soft_limit_min': -40.0, 'soft_limit_max': 0.0}}
    # an axis that the E-Cut does not have is skipped
    assert ecut_axis_updates(profile, {'X': _ECUT_CURRENT['X']}) == {'X': {'axis_velocity_max': 1800.0}}
