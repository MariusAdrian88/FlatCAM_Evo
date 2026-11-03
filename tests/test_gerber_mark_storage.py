"""Marking an aperture that has no key in mark_shapes_storage must not fail (aperture added after build_ui)."""
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest
from shapely import LineString, MultiLineString, MultiPolygon, Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.helpers_objects import _headless_qt, make_app, make_gerber  # noqa: F401,E402


def _gerber():
    app = make_app()
    obj = make_gerber(app)
    obj.mark_shapes = Mock()
    counter = iter(range(1000))
    obj.add_mark_shape = Mock(side_effect=lambda **kw: next(counter))
    obj.mark_shapes.add_batch.side_effect = lambda batch, **kw: [next(counter) for _ in batch]
    return app, obj


def _aperture_with_solid(obj):
    for ap, tool in obj.tools.items():
        if any('solid' in el for el in tool['geometry']):
            return ap
    raise AssertionError('no aperture with solid geometry')


def test_plot_aperture_without_storage_key_stores_shape_keys():
    app, obj = _gerber()
    ap = _aperture_with_solid(obj)
    obj.mark_shapes_storage.clear()

    obj.plot_aperture(run_thread=False, marked_aperture=ap, color='#ff0000')

    assert obj.mark_shapes_storage[ap]
    assert len(obj.mark_shapes_storage[ap]) == len(obj.mark_shapes.add_batch.call_args.args[0])
    app.log.error.assert_not_called()
    obj.mark_shapes.redraw.assert_called_once()


@pytest.mark.parametrize('only_flashes, run_thread', [(False, False), (True, True)])
def test_marking_batches_shapes_and_keeps_aperture_keys(only_flashes, run_thread):
    app, obj = _gerber()
    first = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    second = Polygon([(3, 0), (4, 0), (4, 1), (3, 1)])
    line = LineString([(0, 0), (2, 2)])
    obj.tools = {10: {'geometry': [
        {'solid': MultiPolygon([first, second]), 'follow': Point(0, 0)},
        {'solid': MultiLineString([line]), 'follow': line},
        {'solid': first, 'follow': Point(0, 0)}, {'follow': Point(0, 0)}]}}
    expected = [first, second, first] if only_flashes else [first, second, line, first]
    keys = list(range(100, 100 + len(expected)))
    obj.mark_shapes_storage = {10: ['existing'], 20: ['other']}
    obj.mark_shapes.add_batch.side_effect = None
    obj.mark_shapes.add_batch.return_value = keys

    obj.plot_aperture(only_flashes=only_flashes, run_thread=run_thread,
                      marked_aperture=10, color='#12345678', visible=False)
    if run_thread:
        obj.mark_shapes.add_batch.assert_not_called()
        task = app.worker_task.emit.call_args.args[0]
        task['fcn'](*task['params'])

    obj.mark_shapes.add_batch.assert_called_once()
    batch = obj.mark_shapes.add_batch.call_args.args[0]
    assert [item['shape'].wkb for item in batch] == [shape.wkb for shape in expected]
    assert all(item['color'] == item['face_color'] == '#12345678' for item in batch)
    assert obj.mark_shapes.add_batch.call_args.kwargs == {
        'tolerance': obj.drawing_tolerance, 'layer': 0, 'visible': False}
    assert obj.mark_shapes_storage == {10: ['existing'] + keys, 20: ['other']}
    obj.add_mark_shape.assert_not_called()
    obj.mark_shapes.redraw.assert_called_once()
    app.log.error.assert_not_called()

    obj.clear_plot_apertures(10)
    assert [call.args[0] for call in obj.mark_shapes.remove.call_args_list] == ['existing'] + keys
    assert obj.mark_shapes_storage == {10: [], 20: ['other']}


