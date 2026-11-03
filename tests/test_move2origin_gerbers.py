"""Exercise Move2Origin geometry, plot payloads and actual Gerber regeneration."""

from unittest.mock import Mock, patch

import pytest
from shapely import affinity
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appHandlers.appObjectOps import AppObjectOps
from appObjects.GerberObject import GerberObject
from tests.helpers_objects import make_app, make_gerber, NATIVE_GBR  # noqa: F401,E402


@pytest.mark.parametrize('threaded', [False, True])
@pytest.mark.parametrize('positions', [
    pytest.param([(10.0, 30.0), (25.0, 5.0)], id='positions1'),
])
def test_selected_gerbers_move_as_group_with_matching_plot_and_source(positions, threaded, tmp_path):
    from PyQt6 import QtWidgets

    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = make_app()
    app.delete_selection_shape = Mock()
    app.f_handlers = appIO(app)
    plotted = {}
    objects = []
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        for index, (x, y) in enumerate(positions):
            obj = make_gerber(app, path=NATIVE_GBR, name='layer_%d' % index)
            xmin, ymin, xmax, ymax = obj.bounds()
            obj.offset((x - xmin, y - ymin))
            obj.obj_options.update(plot=True, follow=False, solid=True, multicolored=False)
            obj.source_file = 'original source'
            objects.append(obj)
            plotted[obj] = []

            def add_batch(batch, target=obj, **kwargs):
                plotted[target].extend(entry['shape'] for entry in batch)

            def add_shape(shape, target=obj, **kwargs):
                plotted[target].append(shape)

            obj.add_shapes_batch = add_batch
            obj.add_shape = add_shape

        before = [unary_union(obj.solid_geometry) for obj in objects]
        before_bounds = [obj.bounds() for obj in objects]
        x = min(bounds[0] for bounds in before_bounds)
        y = min(bounds[1] for bounds in before_bounds)
        app.collection.get_selected.return_value = objects
        ops = AppObjectOps(app)
        ops.on_move2origin(use_thread=threaded)
        if threaded:
            task = app.worker_task.emit.call_args.args[0]
            assert all(obj.bounds() == bounds for obj, bounds in zip(objects, before_bounds))
            task['fcn'](*task['params'])

        for index, obj in enumerate(objects):
            expected = affinity.translate(before[index], xoff=-x, yoff=-y)
            moved = unary_union(obj.solid_geometry)
            # Union may reorder rings after floating-point translation.
            assert moved.hausdorff_distance(expected) < 1e-7
            assert moved.symmetric_difference(expected).area < 1e-7
            displayed = unary_union(plotted[obj])
            assert displayed.hausdorff_distance(expected) < 1e-7
            assert displayed.symmetric_difference(expected).area < 1e-7
            assert tuple(obj.obj_options[key] for key in ('xmin', 'ymin', 'xmax', 'ymax')) == pytest.approx(moved.bounds)
            assert obj.source_file != 'original source'
            filename = tmp_path / ('moved_%d.gbr' % index)
            filename.write_text(obj.source_file)
            parsed = GerberObject('parsed_%d' % index, app)
            assert parsed.parse_file(str(filename)) is None
            assert parsed.bounds() == pytest.approx(moved.bounds, abs=2e-5)

        after_bounds = [obj.bounds() for obj in objects]
        assert min(bounds[0] for bounds in after_bounds) == pytest.approx(0.0)
        assert min(bounds[1] for bounds in after_bounds) == pytest.approx(0.0)
        if len(objects) == 2:
            assert after_bounds[1][0] - after_bounds[0][0] == pytest.approx(before_bounds[1][0] - before_bounds[0][0])
            assert after_bounds[1][1] - after_bounds[0][1] == pytest.approx(before_bounds[1][1] - before_bounds[0][1])
        if threaded:
            app.plotcanvas.fit_view.assert_called_once_with(blocking=False)
        else:
            app.plotcanvas.fit_view.assert_called_once_with()
        app.delete_selection_shape.assert_called_once_with()
        assert app.should_we_save
