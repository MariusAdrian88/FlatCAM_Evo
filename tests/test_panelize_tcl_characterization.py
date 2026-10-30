"""Characterization of the Tcl ``panelize`` command (Stage 5 item 3), captured BEFORE the panelize core refactor.

TclCommandPanelize.execute is driven headless: the mock app's ``new_object`` runs the init callback on a real, empty
GerberObject / ExcellonObject (same approach as test_stage4_join_gerber_tcl.py). The numbers below were captured from
the pre-refactor code (2 rows x 3 columns, spacing 5 / 5 for Gerber, default spacing for Excellon).

Only the nesting of a Geometry panel's solid_geometry may change in the refactor (not covered here: no Geometry
fixture); everything asserted here must stay the same.
"""

from unittest.mock import Mock

import pytest

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from tclCommands.TclCommandPanelize import TclCommandPanelize

from tests.helpers_objects import (  # noqa: F401,E402
    app, _headless_qt, make_excellon, make_gerber, NATIVE_GBR, parts, polygon_union,
)

ROWS, COLS = 2, 3


def run_panelize(app, src, *argv):
    """Runs the command. Returns (result, created) where created = {name: object built by the init callback}."""
    app.f_handlers = appIO(app)
    created = {}

    def new_object(kind, name, initialize, plot=True, **kwargs):
        cls = {'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject}[kind]
        obj = cls(name, app)
        initialize(obj, app)
        created[name] = obj
        return 'ok'

    app.collection.get_by_name = Mock(side_effect=lambda n: src if n == src.obj_options['name'] else None)
    app.app_obj.new_object = Mock(side_effect=new_object)
    cmd = TclCommandPanelize(app)
    args, unnamed = cmd.check_args(tuple(argv))
    return cmd.execute(args, unnamed), created


GERBER_CASES = {
    # path: (source bounds, panel bounds, source union area, panel union area, solid parts, records per aperture)
    'mixed': (None,
              (-0.5002501250625312, 4.0, 31.0, 38.001500750375186),
              (-0.5002501250625312, 4.0, 104.00050025012507, 77.00300150075037),
              271.27037912815194, 1627.6222747689121, 54,
              {10: 18, 11: 6, 12: 6, 13: 6, 14: 6, 15: 6, 16: 6, 17: 6, 0: 6}),
    'native': (NATIVE_GBR,
               (-0.500250125, 4.4999999, 30.500250125, 38.00150075),
               (-0.500250125, 4.4999999, 102.501250625, 76.5030016),
               268.6339022546699, 1611.8034135280186, 36,
               {10: 12, 11: 6, 12: 6, 16: 6, 0: 6}),
}


# ---------------------------------------------------------------------------------------------------------------
# Changed on purpose by the panelize core (Stage 5): not part of the pre-refactor capture above
# ---------------------------------------------------------------------------------------------------------------

def test_gerber_panel_inherits_export_as_regions_and_macros(app):
    src = make_gerber(app, name='g')
    src.export_as_regions = True
    res, created = run_panelize(app, src, 'g', '-rows', '1', '-columns', '2', '-outname', 'p')
    pan = created['p']
    assert pan.export_as_regions is True
    assert set(pan.aperture_macros) == set(src.aperture_macros) and pan.aperture_macros


def make_geometry(app, multigeo):
    from shapely.geometry import LineString, box
    src = GeometryObject('src', app)
    src.multigeo = multigeo
    src.units = 'MM'
    src.solid_geometry = [box(0, 0, 10, 10), [LineString([(0, 0), (10, 10)]), [box(2, 2, 3, 3)]]]
    src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': [LineString([(0, 0), (10, 0)])]}}
    return src



def test_excellon_panel_keeps_the_table_of_the_hole_ids(app):
    from appParsers.ParseExcellon import hole_ids, to_state
    src = ExcellonObject('e', app)
    text = "M48\nMETRIC\nT1C1.0\n%\nG05\nT1\n; #@! TO.C,R1\nX1.0Y1.0\n; #@! TD\nX2.0Y2.0\nM30\n"
    assert src.parse_lines(text.splitlines(keepends=True)) is None
    src.create_geometry()
    src.obj_options['name'] = 'e'
    res, created = run_panelize(app, src, 'e', '-rows', '1', '-columns', '2', '-outname', 'p')
    pan = created['p']
    ids = hole_ids(pan.tools[1], 'drills')
    assert ids is not None and len(ids) == 4
    assert [to_state(pan.to_attributes, i) for i in ids].count({'.C': ['R1']}) == 2
    assert pan.to_attributes == src.to_attributes and pan.to_attributes is not src.to_attributes
