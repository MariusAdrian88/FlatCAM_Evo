"""Gerber Editor selection and edit operations: invert, filter by kind, chain of tracks, step and repeat, transcode
and snap to grid."""

import math
import gettext
import builtins

from shapely import LineString, Point, STRtree
from shapely.affinity import translate
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry

from PyQt6 import QtCore, QtWidgets

from appEditors.grb_plugins.GrbCommon import DrawTool, DrawToolShape, gerber_quad, gerber_steps
from appEditors.grb_plugins.GrbFollowPlugin import commit, selected_entries, circular_aperture
from appEditors.grb_plugins.GrbHistory import gerber_operation, mark_flashes
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def record_kind(aperture, shape):
    """'pad' (flash), 'track' (open line) or 'region'. A region is identified by the type of its aperture."""
    if aperture.get('type') == 'REG':
        return 'region'
    follow = shape.geo.get('follow')
    if isinstance(follow, Point):
        return 'pad'
    if isinstance(follow, LineString):
        return 'track'
    return 'region'


def all_records(editor):
    return [(apid, aperture, shape) for apid, aperture in editor.storage_dict.items()
            for shape in aperture.get('geometry', ())]


def refresh_selection(editor):
    """A selection change is not a history step: only the aperture rows and the selection colors are updated."""
    chosen = set(editor.selected)
    editor.sync_aperture_rows(apid for apid, aperture in editor.storage_dict.items()
                              if any(shape in chosen for shape in aperture.get('geometry', ())))
    editor.plot_selection()


def invert_selection(editor):
    chosen = {id(shape) for shape in editor.selected}
    editor.selected = [shape for apid, aperture, shape in all_records(editor) if id(shape) not in chosen]
    refresh_selection(editor)


def filter_selection(editor, kind):
    if editor.selected:
        pool = {id(shape) for shape in editor.selected}
    else:
        pool = None
    editor.selected = [shape for apid, aperture, shape in all_records(editor)
                       if (pool is None or id(shape) in pool) and record_kind(aperture, shape) == kind]
    refresh_selection(editor)


def select_chain(editor, point, tolerance, branching=False, add=False):
    """Select the track nearest to the point and the tracks that meet it end to end. Returns the chain."""
    tracks = [shape for apid, aperture, shape in all_records(editor)
              if record_kind(aperture, shape) == 'track' and not shape.geo['follow'].is_empty]
    probe = Point(point[0], point[1])
    nearest = min(tracks, key=lambda shape: shape.geo['follow'].distance(probe), default=None)
    if nearest is None or nearest.geo['follow'].distance(probe) > tolerance:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % _("No track at this point."))
        return []
    ends = []
    for shape in tracks:
        coords = shape.geo['follow'].coords
        ends.extend((Point(coords[0]), Point(coords[-1])))
    tree = STRtree(ends)
    start = tracks.index(nearest)
    chain, pending = {start}, [start]
    while pending:
        current = pending.pop()
        for end in (2 * current, 2 * current + 1):
            group = [int(j) for j in tree.query(ends[end], predicate='dwithin', distance=tolerance)]
            if len(group) >= 3 and not branching:
                continue
            for j in group:
                if j // 2 not in chain:
                    chain.add(j // 2)
                    pending.append(j // 2)
    result = [tracks[i] for i in sorted(chain)]
    if add:
        keep = {id(shape) for shape in editor.selected}
        editor.selected = list(editor.selected) + [shape for shape in result if id(shape) not in keep]
    else:
        editor.selected = list(result)
    refresh_selection(editor)
    return result


def moved_geo(geo, dx, dy):
    return {key: translate(value, dx, dy) if isinstance(value, BaseGeometry) else value
            for key, value in geo.items()}


def mark_native(editor, shapes):
    """Flashes and tracks that are rebuilt from their aperture keep their flash or track meaning when exported."""
    editor.history.native_shapes.update(shapes)
    editor.history.translated_shapes.update(shapes)
    # the rebuilt flashes are the shape of their aperture at their Point follow
    mark_flashes(editor, shapes)


def flash_geo(editor, aperture, center):
    """The solid that the Pad tool makes for a flash of this aperture."""
    kind = aperture.get('type')
    if kind == 'C':
        radius = float(aperture['size']) / 2
        return {'solid': center.buffer(radius, quad_segs=gerber_quad(editor.app, radius)), 'follow': center}
    if kind == 'R':
        half_w, half_h = float(aperture['width']) / 2, float(aperture['height']) / 2
        x, y = center.x, center.y
        from shapely.geometry import Polygon
        return {'solid': Polygon([(x - half_w, y - half_h), (x + half_w, y - half_h),
                                  (x + half_w, y + half_h), (x - half_w, y + half_h)]), 'follow': center}
    if kind == 'O':
        from appParsers.ParseGerber import Gerber
        steps = gerber_steps(editor.app, min(float(aperture['width']), float(aperture['height'])) / 2)
        return {'solid': Gerber.create_flash_geometry(editor, center, aperture, steps), 'follow': center}
    raise ValueError(_("Incompatible aperture type. Select an aperture with type 'C', 'R' or 'O'."))


def track_solid(editor, follow, width):
    return follow.buffer(width / 2, quad_segs=gerber_quad(editor.app, width / 2))


@gerber_operation(_('Step and Repeat'))
def apply_step_repeat(editor, rows, columns, dx, dy):
    try:
        rows, columns = int(rows), int(columns)
        dx, dy = float(dx), float(dy)
        if rows < 1 or columns < 1 or (rows == 1 and columns == 1):
            raise ValueError(_("Rows and columns must be at least 1, and not both 1."))
        if not (math.isfinite(dx) and math.isfinite(dy)):
            raise ValueError(_("Choose finite X and Y distances."))
        entries = selected_entries(editor)
        additions = {}
        for apid, index, shape in entries:
            for i in range(columns):
                for j in range(rows):
                    if i == 0 and j == 0:
                        continue
                    additions.setdefault(apid, []).append(DrawToolShape(moved_geo(shape.geo, i * dx, j * dy)))
    except (ValueError, TypeError, OverflowError, GEOSException) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    copies = [shape for shapes in additions.values() for shape in shapes]
    mark_native(editor, copies)
    # appended after the last record of each aperture: commit keeps the storage list object
    replacements = {apid: {len(editor.storage_dict[apid]['geometry']) - 1:
                           [editor.storage_dict[apid]['geometry'][-1]] + shapes}
                    for apid, shapes in additions.items()}
    commit(editor, replacements, list(editor.selected) + copies)
    return copies


@gerber_operation(_('Transcode'))
def apply_transcode(editor, target_code):
    try:
        target = next((key for key in editor.storage_dict if str(key) == str(target_code)), None)
        if target is None:
            raise ValueError(_("The target aperture does not exist."))
        aperture = editor.storage_dict[target]
        entries = [entry for entry in selected_entries(editor) if entry[0] != target]
        if not entries:
            raise ValueError(_("Select features that are not in the target aperture."))
        moves, results = {}, {}
        for apid, index, shape in entries:
            kind = record_kind(editor.storage_dict[apid], shape)
            if kind == 'region':
                raise ValueError(_("A region cannot be moved to another aperture."))
            if kind == 'pad':
                geo = flash_geo(editor, aperture, shape.geo['follow'])
                result = DrawToolShape(dict(shape.geo, **geo))
            else:
                width = circular_aperture(aperture)
                result = DrawToolShape(dict(shape.geo, solid=track_solid(editor, shape.geo['follow'], width)))
            if result.geo['solid'].is_empty or not result.geo['solid'].is_valid:
                raise ValueError(_("The target aperture produces invalid geometry."))
            moves.setdefault(apid, {})[index] = []
            results[shape] = result
    except (ValueError, TypeError, OverflowError, KeyError, GEOSException) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    editor.history.capture([target])
    mark_native(editor, results.values())
    aperture['geometry'].extend(results.values())
    commit(editor, moves, [results.get(shape, shape) for shape in editor.selected])
    return list(results.values())


@gerber_operation(_('Snap to Grid'))
def apply_snap_to_grid(editor, grid_x, grid_y):
    try:
        grid_x, grid_y = float(grid_x), float(grid_y)
        if not (math.isfinite(grid_x) and math.isfinite(grid_y)) or grid_x <= 0 or grid_y <= 0:
            raise ValueError(_("The grid must have finite positive steps."))

        def snap(x, y):
            return round(x / grid_x) * grid_x + 0.0, round(y / grid_y) * grid_y + 0.0

        replacements, results = {}, {}
        for apid, index, shape in selected_entries(editor):
            aperture = editor.storage_dict[apid]
            kind = record_kind(aperture, shape)
            follow = shape.geo['follow']
            if kind == 'pad':
                x, y = snap(follow.x, follow.y)
                result = DrawToolShape(moved_geo(shape.geo, x - follow.x, y - follow.y))
                result.geo['follow'] = Point(x, y)
            elif kind == 'track':
                width = circular_aperture(aperture)
                line = LineString([snap(*coord[:2]) for coord in follow.coords])
                if line.is_empty or line.length <= 0:
                    raise ValueError(_("Snapping would collapse a track."))
                result = DrawToolShape(dict(shape.geo, follow=line, solid=track_solid(editor, line, width)))
            else:
                continue
            replacements.setdefault(apid, {})[index] = [result]
            results[shape] = result
        if not results:
            raise ValueError(_("Select flashes or tracks to snap."))
    except (ValueError, TypeError, OverflowError, GEOSException) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    mark_native(editor, results.values())
    commit(editor, replacements, [results.get(shape, shape) for shape in editor.selected])
    return list(results.values())


class ChainSelectEditorGrb(DrawTool):
    """Click a track: the connected chain is selected. Shift adds to the selection. Alt branches over junctions."""
    uses_raw_pick = True

    def __init__(self, draw_app):
        super().__init__(draw_app)
        self.name = 'select_chain'
        draw_app.in_action = False
        draw_app.app.inform.emit(_("Click a track to select the chain of tracks that it belongs to. "
                                   "Shift adds to the selection."))

    def click(self, point):
        from appEditors.appGerberEditor import PICK_TOLERANCE_PX
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        tolerance = PICK_TOLERANCE_PX * self.draw_app.canvas.pixel_size()
        select_chain(self.draw_app, point, tolerance,
                     branching=bool(modifiers & QtCore.Qt.KeyboardModifier.AltModifier),
                     add=bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier))
        return ""

    def clean_up(self):
        pass
