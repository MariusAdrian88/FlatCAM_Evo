"""Selected contour inspection never changes editor geometry or undo/redo."""
from copy import deepcopy

import pytest
from shapely.geometry import LineString, Polygon, GeometryCollection

from appEditors import appGeoEditor as geo
from tests.test_geo_editor_join import join_editor
from tests.test_geo_editor_history import run_worker
from tests.test_geo_editor_split import qt_application


def inspect(editor, tolerance=0):
    return geo.AppGeoEditor.diagnose_selected(editor, tolerance)


def test_open_closed_duplicates_zero_and_self_intersection():
    editor, shapes = join_editor([
        [(0, 0), (1, 0)], [(1, 0), (0, 0)],
        [(3, 0), (4, 0), (4, 1), (3, 0)],
        [(5, 0), (5, 0)], [(6, 0), (7, 1), (6, 1), (7, 0)]])
    report = inspect(editor)
    assert report['duplicate'] == shapes[:2]
    assert report['zero_length'] == [shapes[3]]
    assert shapes[4] in report['invalid']
    assert report['open'] == [shapes[0], shapes[1], shapes[4]]
    assert len(report['endpoints']) == 6
    assert shapes[2] not in report['open']


