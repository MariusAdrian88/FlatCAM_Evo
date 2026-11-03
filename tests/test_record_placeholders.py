"""Gerber records with [] placeholders (written by older copper thieving code) and record dicts given to the
geometry flattener.

ToolCopperThieving wrote {'solid': [], 'follow': [], 'clear': []} for missing keys. Since Stage 0C the export routing
reads clear.is_empty on every record, so such an object failed to export; projects saved with these records exist.
"""


from appParsers.feature_access import iter_features
from appParsers.ParseGerber import Gerber
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_poly_equiv, export_gerber_text, _headless_qt, make_gerber, NATIVE_GBR, reparse_gerber,
)


def _with_placeholders(grb):
    for tool in grb.tools.values():
        for rec in tool.get('geometry', []):
            rec.setdefault('clear', [])
            rec.setdefault('follow', [])
    return grb


def test_project_load_drops_placeholders_so_the_export_keeps_the_copper(app, tmp_path):
    from appObjects.GerberObject import GerberObject
    saved = _with_placeholders(make_gerber(app, NATIVE_GBR))
    loaded = GerberObject('loaded', app)
    loaded.from_dict(saved.to_dict())
    for tool in loaded.tools.values():
        for rec in tool.get('geometry', []):
            assert all(hasattr(rec[k], 'is_empty') for k in rec if k in ('solid', 'follow', 'clear'))
    again = reparse_gerber(app, export_gerber_text(app, loaded), tmp_path)
    assert_poly_equiv(again.solid_geometry, saved.solid_geometry, rel=1e-6, abs_tol=1e-4)


