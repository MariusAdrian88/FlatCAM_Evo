"""Gerber X2/X3 attributes: splitting, dictionary state, typed views (spec 5.x, 6.2, 8.4)."""

import hashlib
import json

import pytest

from appParsers.gerber_attributes import (
    AttributeState, check_mandatory, component_of, file_attributes, md5_matches, nets_of,
    parse_aper_function, parse_file_function, split_attribute,
)


@pytest.mark.parametrize("body, expected", [
    ("MyAttr,a b , c\r\n", ("MyAttr", ["a b ", " c"])),          # spaces are significant, CR/LF are not
    (".N,a\\u002Cb,\\u00A9,\\U0001F600,\\uD83D\\uDE00", (".N", ["a,b", "©", "\U0001F600", "\U0001F600"])),
    (".N,bad\\uZZZZ", (".N", ["bad\\uZZZZ"])),                      # broken escape stays raw
    ("CustAttr ", ("CustAttr", [])),                                # spec 5.8: "%TDCustAttr *%"
])
def test_split_attribute(body, expected):
    assert split_attribute(body) == expected


def test_state_dictionary_semantics():
    st = AttributeState()
    st.apply("TF", ".FileFunction,Copper,L1,Top")
    st.apply("TF", ".FileFunction,Copper,L2,Inr")      # later TF of same name replaces
    st.apply("TA", ".AperFunction,SMDPad,CuDef")
    st.apply("TA", "Custom,1")
    st.apply("TO", ".N,GND")
    assert st.as_legacy() == {
        "TF": {".FileFunction": ["Copper", "L2", "Inr"]},
        "TA": {".AperFunction": ["SMDPad", "CuDef"], "Custom": ["1"]},
        "TO": {".N": ["GND"]},
    }
    st.apply("TD", ".AperFunction")
    st.apply("TD", ".N")
    assert st.as_legacy()["TA"] == {"Custom": ["1"]} and st.as_legacy()["TO"] == {}
    st.apply("TA", ".AperFunction,Conductor")
    st.apply("TO", ".N,X")
    st.apply("TD", "")                                  # no name: all TA and TO, never TF
    st.apply("TX", "foo,1")                             # unknown command ignored
    st.apply("TD", "NotThere")                          # deleting a missing name is a no-op
    assert st.as_legacy() == {"TF": {".FileFunction": ["Copper", "L2", "Inr"]}, "TA": {}, "TO": {}}


def test_to_interning_is_stable_and_json_friendly():
    st = AttributeState()
    assert st.to_id == 0
    st.apply("TO", ".N,A")
    a = st.to_id
    st.apply("TO", ".N,B")
    b = st.to_id
    st.apply("TO", ".N,A")
    assert a != 0 and b != a and st.to_id == a
    st.apply("TD", "")
    assert st.to_id == 0
    table = st.to_table()
    assert table == {a: {".N": ["A"]}, b: {".N": ["B"]}}
    assert json.loads(json.dumps(table)) == {str(a): {".N": ["A"]}, str(b): {".N": ["B"]}}


def test_ta_snapshot_keeps_earlier_aperture_state():
    st = AttributeState()
    st.apply("TA", ".AperFunction,ViaPad")
    s1 = st.ta_snapshot()
    assert s1 == {".AperFunction": ["ViaPad"]}
    st.apply("TA", ".AperFunction,Conductor")
    assert s1 == {".AperFunction": ["ViaPad"]}          # earlier aperture keeps its attributes
    assert st.ta_snapshot() == {".AperFunction": ["Conductor"]}


@pytest.mark.parametrize("values, expect", [
    (["Copper", "L2", "Inr", "Plane"], dict(kind="Copper", layer=2, side="Inr", type="Plane")),
    (["Legend", "Bot", "2"], dict(kind="Legend", side="Bot", index=2, extra=[])),
])
def test_parse_file_function_layers(values, expect):
    got = parse_file_function(values)
    for key, value in expect.items():
        assert got[key] == value, key


@pytest.mark.parametrize("values, plated, span, kind_type, extra", [
    (["Plated", "1", "4", "PTH"], True, (1, 4), "PTH", []),
    (["NonPlated", "8", "7", "Blind"], False, (8, 7), "Blind", []),        # from/to order is kept as written
])
def test_parse_file_function_drill(values, plated, span, kind_type, extra):
    got = parse_file_function(values)
    assert (got["plated"], got["from_to"], got["type"], got["extra"]) == (plated, span, kind_type, extra)


def test_parse_file_function_profile_and_empty():
    assert parse_file_function(["Profile", "P"])["edge"] == "P"
    deprecated = parse_file_function(["Profile"])       # deprecated form without the P/NP label is still a profile
    assert deprecated["kind"] == "Profile" and deprecated["edge"] is None
    assert parse_file_function([])["kind"] == ""


@pytest.mark.parametrize("values, kind, sub, deprecated", [
    (["SMDPad", "CuDef"], "SMDPad", ["CuDef"], False),
    (["MechanicalDrill", "BreakOut"], "MechanicalDrill", ["Breakout"], False),
    (["CutOut"], "CutOut", [], True),         # 8.4 deprecated values are accepted and flagged
    (["FutureThing", "a"], "FutureThing", ["a"], False),
])
def test_parse_aper_function(values, kind, sub, deprecated):
    assert parse_aper_function(values) == {"kind": kind, "sub": sub, "deprecated": deprecated}


def test_file_attributes_typed_view_and_mandatory():
    st = AttributeState()
    for body in (
        ".FileFunction,Soldermask,Top",
        ".FilePolarity,negative",
        ".Part,Single",
        ".SameCoordinates,f81d4fae",
        ".CreationDate,2015-02-23T15:59:51+01:00",
        ".GenerationSoftware,Ucamco,UcamX,2017.04",
        ".ProjectId,My PCB,f81d4fae-7dec-11d0-a765-00a0c91e6bf6,2",
        ".MD5,6ab9e892830469cdff7e3e346331d404",
    ):
        st.apply("TF", body)
    got = file_attributes(st.as_legacy()["TF"])
    assert got["file_function"]["kind"] == "Soldermask" and got["file_function"]["side"] == "Top"
    assert got["file_polarity"] == "Negative" and got["part"] == "Single"
    assert got["same_coordinates"] == "f81d4fae"
    assert got["creation_date"] == "2015-02-23T15:59:51+01:00"
    assert got["generation_software"] == {"vendor": "Ucamco", "application": "UcamX", "version": "2017.04"}
    assert got["project_id"] == {"name": "My PCB", "guid": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "revision": "2"}
    assert got["md5"] == "6ab9e892830469cdff7e3e346331d404"
    assert check_mandatory(st.as_legacy()["TF"]) == []


def test_file_attributes_old_file_without_attributes():
    got = file_attributes({})
    assert got["file_function"] is None and got["file_polarity"] is None and got["same_coordinates"] is None
    assert check_mandatory({}) == [".FileFunction", ".FilePolarity", ".SameCoordinates"]
    assert check_mandatory({".FileFunction": ["Profile"]}) == [".FilePolarity", ".SameCoordinates"]


def test_nets_and_components():
    st = AttributeState()
    st.apply("TO", ".N,GND")
    st.apply("TO", ".P,IC12,1,GND")
    pin1 = st.to_id
    st.apply("TO", ".P,IC12,2,TRIG")
    pin2 = st.to_id
    st.apply("TO", ".N,Clk,Other")                     # a pad split over two nets
    multi = st.to_id
    st.apply("TD", "")
    st.apply("TO", ".C,IC12")
    st.apply("TO", ".CRot,90.5")
    st.apply("TO", ".CMnt,SMD")
    st.apply("TO", ".CSup,Digi,DK-1,Mouser,M-2")
    main = st.to_id
    table = st.to_table()
    nets = nets_of(table)
    assert nets["GND"] == [pin1, pin2] and nets["Clk"] == [multi] and nets["Other"] == [multi]
    comp = component_of(table)["IC12"]
    assert comp["CRot"] == 90.5 and comp["CMnt"] == "SMD"
    assert comp["CSup"] == [("Digi", "DK-1"), ("Mouser", "M-2")]
    assert comp["pins"] == {"1": "GND", "2": "TRIG"} and main in comp["to_ids"]
    # after a JSON round trip the ids come back as string keys
    roundtrip = {"3": {".N": ["A"], ".C": ["R1"]}}
    assert nets_of(roundtrip) == {"A": [3]} and "R1" in component_of(roundtrip)


def test_md5_matches_follows_spec_rule():
    body = "D11*\r\nX1500000Y2875000D03*\r\nX2000000D03*\r\nD19*\r\nX2875000Y2875000D03*\r\n"
    digest = hashlib.md5(body.replace("\r", "").replace("\n", "").encode()).hexdigest()
    text = body + f"%TF.MD5,{digest}*%\r\nM02*\r\n"
    assert md5_matches(text, digest) is True
    assert md5_matches(text.replace("\r\n", "\n"), digest.upper()) is True   # platform line ends do not matter
    assert md5_matches(text.replace("D19", "D20"), digest) is False
    assert md5_matches(body + "M02*\n", digest) is None                    # no .MD5 command in the text
    assert md5_matches(text, "not-a-hash") is None


# ---------------------------------------------------------------------------------------------------------------
# Parser integration
# ---------------------------------------------------------------------------------------------------------------

KICAD_HEADER = (
    "%TF.GenerationSoftware,KiCad,Pcbnew,7.0.1*%\n%TF.CreationDate,2024-01-01T00:00:00+00:00*%\n"
    "%TF.ProjectId,demo,00000000-0000-0000-0000-000000000000,rev1*%\n%TF.SameCoordinates,Original*%\n"
    "%TF.FileFunction,Copper,L1,Top*%\n%TF.FilePolarity,Positive*%\n%FSLAX46Y46*%\n%MOMM*%\n")


def _records(gbr):
    return [rec for tool in gbr.tools.values() for rec in tool.get('geometry', ())]


def test_to_id_per_object_and_td_clears(tmp_path):
    from tests.test_gerber_parser import parse_gerber_text
    text = (KICAD_HEADER + "%ADD10C,1.0*%\n%TO.N,GND*%\nD10*\nX0Y0D03*\nX0Y0D02*\nX5000000Y0D01*\n"
            "G36*\nX0Y3000000D02*\nX2000000Y3000000D01*\nX2000000Y5000000D01*\nX0Y3000000D01*\nG37*\n"
            "%TO.N,VCC*%\nX9000000Y0D03*\n%TD*%\nX12000000Y0D03*\nM02*\n")
    gbr, ret, _ = parse_gerber_text(tmp_path, text)
    assert ret is None
    nets = nets_of(gbr.to_attributes)
    assert set(nets) == {'GND', 'VCC'}
    gnd, vcc = nets['GND'][0], nets['VCC'][0]
    by_to = [rec.get('to') for rec in _records(gbr)]
    assert by_to.count(gnd) == 3          # flash, draw, region
    assert by_to.count(vcc) == 1
    assert by_to.count(None) == 1         # the flash after %TD*% has no TO id


def test_ta_on_aperture_and_legacy_shape(tmp_path):
    from tests.test_gerber_parser import parse_gerber_text
    text = (KICAD_HEADER + "%TA.AperFunction,SMDPad,CuDef*%\n%ADD10R,1X1*%\n%TD*%\n%ADD11C,1.0*%\n"
            "D10*\nX0Y0D03*\nD11*\nX1000000Y0D03*\nM02*\n")
    gbr, _, _ = parse_gerber_text(tmp_path, text)
    assert gbr.tools[10]['attributes'] == {'.AperFunction': ['SMDPad', 'CuDef']}
    assert 'attributes' not in gbr.tools[11]
    assert gbr.attributes['TF']['.FileFunction'] == ['Copper', 'L1', 'Top']
    assert gbr.attributes['TA'] == {} and gbr.attributes['TO'] == {}


def test_file_function_and_typed_file_attributes(tmp_path):
    from tests.test_gerber_parser import parse_gerber_text
    text = KICAD_HEADER + "%ADD10C,1.0*%\nD10*\nX0Y0D03*\nM02*\n"
    gbr, _, _ = parse_gerber_text(tmp_path, text)
    assert gbr.file_function == ['Copper', 'L1', 'Top']
    assert gbr.file_attributes == file_attributes(gbr.attributes['TF'])
    assert gbr.file_attributes
    # a later %TF replaces the earlier value (spec 5.3)
    again = text.replace("%FSLAX46", "%TF.FileFunction,Legend,Top*%\n%FSLAX46")
    gbr2, _, _ = parse_gerber_text(tmp_path, again)
    assert gbr2.file_function == ['Legend', 'Top']


def test_attribute_values_are_unescaped(tmp_path):
    from tests.test_gerber_parser import parse_gerber_text
    text = "%FSLAX26Y26*%\n%MOMM*%\n%ADD10C,1*%\n%TO.N,a\\u002Cb*%\nD10*\nX0Y0D03*\nM02*\n"
    gbr, _, _ = parse_gerber_text(tmp_path, text)
    assert nets_of(gbr.to_attributes) == {'a,b': [_records(gbr)[0]['to']]}


def test_file_without_attributes_and_old_project_defaults(tmp_path):
    from tests.test_gerber_parser import parse_gerber_text, MockApp
    from appParsers.ParseGerber import Gerber
    gbr, _, _ = parse_gerber_text(tmp_path, "%FSLAX26Y26*%\n%MOMM*%\n%ADD10C,1*%\nD10*\nX0Y0D03*\nM02*\n")
    assert gbr.to_attributes == {} and not any(gbr.file_attributes.values()) and gbr.file_function == []
    assert all('to' not in rec for rec in _records(gbr))
    fresh = Gerber(MockApp())      # what an old project holds: no such keys, the defaults stay
    assert fresh.to_attributes == {} and fresh.file_attributes == {}
    assert {'attributes', 'to_attributes', 'file_attributes'} <= set(fresh.ser_attrs)


def test_block_keeps_ta_and_nested_to(tmp_path):
    from tests.test_gerber_parser import parse_gerber_text
    text = ("%FSLAX26Y26*%\n%MOMM*%\n%ADD10R,2X2*%\n%TA.AperFunction,Conductor*%\n%ABD12*%\n%TO.N,GND*%\nD10*\n"
            "X0Y0D03*\n%TD.N*%\n%AB*%\n%TD*%\nD12*\nX5000000Y0D03*\nM02*\n")
    gbr, _, union = parse_gerber_text(tmp_path, text)
    assert gbr.tools[12]['attributes'] == {'.AperFunction': ['Conductor']}
    inner = [rec for _, rec, _ in gbr.tools[12]['records']]
    assert nets_of(gbr.to_attributes)['GND'] == [inner[0]['to']]
    assert union.area == pytest.approx(4.0)


def test_panelize_copy_keeps_attributes():
    from types import SimpleNamespace
    from appParsers.panelize_core import copy_gerber_attributes
    src = SimpleNamespace(export_as_regions=False, aperture_macros={},
                          attributes={'TF': {'.FileFunction': ['Copper']}, 'TA': {}, 'TO': {}},
                          to_attributes={1: {'.N': ['GND']}}, file_attributes={'function': 'x'})
    dst = SimpleNamespace()
    copy_gerber_attributes(dst, src)
    assert dst.attributes == src.attributes and dst.to_attributes == src.to_attributes
    assert dst.file_attributes == src.file_attributes
    assert dst.to_attributes is not src.to_attributes


def test_merge_renumbers_to_ids():
    from appObjects.GerberObject import GerberObject
    from types import SimpleNamespace as NS
    from shapely.geometry import box

    def part(net, ident):
        return NS(obj_options={}, solid_geometry=[box(0, 0, 1, 1)], follow_geometry=[], export_as_regions=False,
                  to_attributes={ident: {'.N': [net]}},
                  tools={10: {'type': 'C', 'size': 1.0, 'geometry': [{'solid': box(0, 0, 1, 1), 'to': ident}]}})

    final = NS(obj_options={}, solid_geometry=None, follow_geometry=None, tools={}, export_as_regions=False,
               to_attributes={})
    GerberObject.merge([part('GND', 1), part('VCC', 1), part('GND', 1)], final)
    nets = nets_of(final.to_attributes)
    assert set(nets) == {'GND', 'VCC'} and len(final.to_attributes) == 2
    used = sorted(rec['to'] for tool in final.tools.values() for rec in tool['geometry'])
    assert sorted(nets['GND'] * 2 + nets['VCC']) == used


def _report_obj(**kw):
    from types import SimpleNamespace as NS
    base = dict(file_attributes={}, attributes={}, to_attributes={}, tools={})
    base.update(kw)
    return NS(**base)


def test_report_rows_show_attributes_and_net_counts():
    from appPlugins.ToolReport.ReportGen import gerber_attribute_rows
    obj = _report_obj(
        file_attributes={'part': 'Single', 'file_function': {'kind': 'Copper', 'layer': 1, 'side': 'Top'},
                         'file_polarity': 'Positive', 'md5': None},
        to_attributes={1: {'.N': ['GND']}, '2': {'.N': ['VCC']}, 3: {'.N': ['N/C']},
                       4: {'.C': ['R1'], '.CVal': ['10k'], '.CFtp': ['0603'], '.CRot': ['90']}},
        tools={10: {'attributes': {'.AperFunction': ['SMDPad', 'CuDef']},
                    'geometry': [{'to': 1}, {'to': 1}, {'to': 2}, {'to': 3}, {}]},
               11: {'attributes': {}, 'geometry': [{'to': 4}]}})
    rows = gerber_attribute_rows(obj)
    by = {(s, n): v for s, n, v in rows}
    assert any(s == 'attributes' and 'Copper' in str(v) for s, n, v in rows)
    assert not any(v is None for s, n, v in rows)
    assert by[('apertures', 'SMDPad')] == '1'
    assert by[('nets', 'GND')] == '2' and by[('nets', 'VCC')] == '1' and ('nets', 'N/C') not in by
    assert [v for s, n, v in rows if s == 'nets' and n != 'GND' and n != 'VCC'] == ['2']   # net count row
    assert by[('components', 'R1')] == '10k, 0603, 90.0'


def test_report_rows_of_an_excellon_count_nets_from_the_hole_ids():
    from appPlugins.ToolReport.ReportGen import gerber_attribute_rows
    obj = _report_obj(
        attributes={'TF': {'.FileFunction': ['Plated', '1', '2', 'PTH']}},
        to_attributes={1: {'.N': ['GND']}, '2': {'.N': ['VCC']}},
        tools={1: {'attributes': {'.AperFunction': ['Plated', 'PTH', 'ViaDrill']}, 'drills': [0, 0, 0],
                   'drills_to': [1, 1, 0], 'slots': [0], 'slots_to': [2]},
               2: {'drills': [0], 'slots': []}})
    by = {(s, n): v for s, n, v in gerber_attribute_rows(obj)}
    assert by[('attributes', '.FileFunction')] == 'Plated,1,2,PTH'
    assert by[('apertures', 'Plated')] == '1'
    assert by[('nets', 'GND')] == '2' and by[('nets', 'VCC')] == '1'


def test_report_rows_empty_without_attributes():
    from appPlugins.ToolReport.ReportGen import gerber_attribute_rows
    assert gerber_attribute_rows(_report_obj(file_attributes={'part': None})) == []
    assert gerber_attribute_rows(object()) == []
