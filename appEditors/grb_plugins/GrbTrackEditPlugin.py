"""Gerber track editing: clip, clip and replace with a pad, stretch and elbows.

The operations are plain functions of the editor (1 call is 1 undo step). The DrawTool classes at the end are the
canvas side. The tools call the module functions directly, so they don't depend on the editor delegates.
"""

import math
import gettext
import builtins

from shapely import LineString, Point, Polygon, box
from shapely.affinity import translate
from shapely.errors import GEOSException

from appEditors.grb_plugins.GrbCommon import DrawTool, DrawToolShape, DrawToolUtilityShape, numeric_aperture_codes, \
    gerber_quad
from appEditors.grb_plugins.GrbFollowPlugin import commit
from appEditors.grb_plugins.GrbHistory import gerber_operation, mark_flashes
from appEditors.grb_plugins.GrbTrackBend import sweep_parameters
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

EPS = 1e-12
ERRORS = (ValueError, TypeError, OverflowError, GEOSException)


# ---------------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------------
def _records(editor):
    return [(apid, index, shape) for apid, aperture in editor.storage_dict.items()
            for index, shape in enumerate(aperture.get('geometry', ()))]


def _kind(aperture, shape):
    """'region' | 'pad' | 'track' | None"""
    if aperture.get('type') == 'REG':
        return 'region'
    follow = shape.geo.get('follow')
    if isinstance(follow, Point):
        return None if follow.is_empty else 'pad'
    if isinstance(follow, LineString) and not follow.is_empty:
        return 'track'
    return None


def _xy(point):
    point = tuple(float(value) for value in point)[:2]
    if len(point) != 2 or not all(math.isfinite(value) for value in point):
        raise ValueError(_("Choose finite X and Y coordinates."))
    return point


def _bounds(bounds):
    xmin, ymin, xmax, ymax = (float(value) for value in bounds)
    if not all(math.isfinite(value) for value in (xmin, ymin, xmax, ymax)):
        raise ValueError(_("The rectangle must have finite coordinates."))
    xmin, xmax = min(xmin, xmax), max(xmin, xmax)
    ymin, ymax = min(ymin, ymax), max(ymin, ymax)
    return xmin, ymin, xmax, ymax


def _inside(bounds, x, y):
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def _sweep(editor, apid, shape):
    """Radius and circle steps of a circular-aperture track; a closed track keeps the preference steps."""
    aperture = editor.storage_dict[apid]
    try:
        return sweep_parameters(editor, aperture, shape)
    except ValueError:
        pass
    width = float(aperture.get('size', 0))
    if (aperture.get('type') != 'C' or not math.isfinite(width) or width <= 0 or
            not getattr(shape.geo.get('clear'), 'is_empty', True)):
        raise ValueError(_("Only circular-aperture tracks without clear geometry can be rebuilt."))
    return width / 2, gerber_quad(editor.app, width / 2)


def _track(editor, apid, shape, follow):
    radius, steps = _sweep(editor, apid, shape)
    return DrawToolShape(dict(shape.geo, follow=follow, solid=follow.buffer(radius, steps)))


def _line_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'LineString':
        return [geometry] if geometry.length > EPS else []
    if hasattr(geometry, 'geoms'):
        return [part for item in geometry.geoms for part in _line_parts(item)]
    return []


def _polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry] if geometry.area > EPS else []
    if hasattr(geometry, 'geoms'):
        return [part for item in geometry.geoms for part in _polygons(item)]
    return []


def _ordered(follow, parts):
    """Orient the pieces like the track and sort them along it."""
    result = []
    for part in parts:
        if follow.project(Point(part.coords[0])) > follow.project(Point(part.coords[-1])):
            part = LineString(list(part.coords)[::-1])
        result.append(part)
    return sorted(result, key=lambda part: follow.project(Point(part.coords[0])))


def _dedupe(coords):
    result = []
    for coord in coords:
        if not result or result[-1] != coord:
            result.append(coord)
    return result


def _clip_plan(editor, bounds):
    """[(apid, index, [(record, is_inside), ...], was_cut)] for the records that matter; the rest is outside."""
    window = box(*bounds)
    plan = []
    for apid, index, shape in _records(editor):
        kind = _kind(editor.storage_dict[apid], shape)
        follow = shape.geo.get('follow')
        if kind == 'pad':
            plan.append((apid, index, [(shape, _inside(bounds, *follow.coords[0][:2]))], False))
        elif kind == 'track':
            parts_in = _line_parts(follow.intersection(window))
            if not parts_in:
                continue
            parts_out = _line_parts(follow.difference(window))
            if not parts_out:
                plan.append((apid, index, [(shape, True)], False))
                continue
            pieces = [(part, True) for part in _ordered(follow, parts_in)] + \
                     [(part, False) for part in _ordered(follow, parts_out)]
            pieces.sort(key=lambda item: follow.project(Point(item[0].coords[0])))
            plan.append((apid, index, [(_track(editor, apid, shape, part), inside) for part, inside in pieces], True))
        elif kind == 'region':
            solid = shape.geo.get('solid')
            if solid is None or solid.is_empty:
                continue
            polygons_in = _polygons(solid.intersection(window))
            if not polygons_in:
                continue
            polygons_out = _polygons(solid.difference(window))
            if not polygons_out:
                plan.append((apid, index, [(shape, True)], False))
                continue
            if not getattr(shape.geo.get('clear'), 'is_empty', True):
                raise ValueError(_("Regions with clear geometry can't be clipped."))
            pieces = [(polygon, False) for polygon in polygons_out] + [(polygon, True) for polygon in polygons_in]
            plan.append((apid, index, [
                (DrawToolShape(dict(shape.geo, solid=polygon, follow=LineString(polygon.exterior.coords))), inside)
                for polygon, inside in pieces], True))
    return plan


def _fail(editor, err):
    editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
    return 'fail'


# ---------------------------------------------------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------------------------------------------------
@gerber_operation(_('Clip'))
def apply_clip(editor, bounds):
    """Cut the tracks and the regions at the edge of bounds = (xmin, ymin, xmax, ymax); select the inside records."""
    try:
        bounds = _bounds(bounds)
        if bounds[2] - bounds[0] <= 0 or bounds[3] - bounds[1] <= 0:
            raise ValueError(_("The clip rectangle must have a width and a height."))
        plan = _clip_plan(editor, bounds)
    except ERRORS as err:
        return _fail(editor, err)
    replacements, selected, created = {}, [], []
    for apid, index, parts, cut in plan:
        if cut:
            replacements.setdefault(apid, {})[index] = [record for record, inside in parts]
            created.extend(record for record, inside in parts)
        selected.extend(record for record, inside in parts if inside)
    editor.history.native_shapes.update(created)
    editor.history.translated_shapes.update(record for record in created if _is_pad(record))
    commit(editor, replacements, selected)
    return selected


def _is_pad(record):
    return isinstance(record.geo.get('follow'), Point)


@gerber_operation(_('Clip and replace with pad'), all_apertures=True)
def apply_clip_replace_pad(editor, bounds):
    """As apply_clip, then the inside records are deleted and 1 rectangular flash of the bounds size is added."""
    try:
        bounds = _bounds(bounds)
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if width <= 0 or height <= 0:
            raise ValueError(_("The clip rectangle must have a width and a height."))
        plan = _clip_plan(editor, bounds)
        target = next((key for key, aperture in editor.storage_dict.items()
                       if aperture.get('type') == 'R' and
                       math.isclose(float(aperture.get('width', math.nan)), width, rel_tol=1e-9, abs_tol=1e-12) and
                       math.isclose(float(aperture.get('height', math.nan)), height, rel_tol=1e-9, abs_tol=1e-12) and
                       not any('hole' in str(key).lower() and value for key, value in aperture.items())), None)
    except ERRORS as err:
        return _fail(editor, err)
    center = Point((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    # the solid the Pad tool makes for an 'R' aperture
    p1 = (center.x - width / 2, center.y - height / 2)
    p2 = (center.x + width / 2, center.y - height / 2)
    p3 = (center.x + width / 2, center.y + height / 2)
    p4 = (center.x - width / 2, center.y + height / 2)
    pad = DrawToolShape({'solid': Polygon([p1, p2, p3, p4, p1]), 'follow': center})
    new_apertures = {}
    if target is None:
        target = max([9] + numeric_aperture_codes(editor.storage_dict)) + 1
        new_apertures[target] = {'type': 'R', 'size': math.hypot(width, height), 'width': width, 'height': height,
                                 'geometry': [pad]}
    else:
        editor.storage_dict[target]['geometry'].append(pad)
    replacements, created = {}, []
    for apid, index, parts, cut in plan:
        kept = [record for record, inside in parts if not inside]
        if len(kept) != len(parts):
            replacements.setdefault(apid, {})[index] = kept
        if cut:
            created.extend(kept)
    editor.history.native_shapes.update(created + [pad])
    editor.history.translated_shapes.update([pad] + [record for record in created if _is_pad(record)])
    # the pad is the 'R' shape of its aperture at its center
    mark_flashes(editor, [pad])
    commit(editor, replacements, [pad], new_apertures)
    return [pad]


@gerber_operation(_('Stretch'))
def apply_stretch(editor, bounds, dx, dy):
    """Move the track vertices and the flash centers inside bounds by (dx, dy). Regions stay unchanged."""
    try:
        bounds = _bounds(bounds)
        dx, dy = float(dx), float(dy)
        if not math.isfinite(dx) or not math.isfinite(dy) or (dx == 0 and dy == 0):
            raise ValueError(_("Choose a finite, nonzero stretch distance."))
        replacements, moved, pads = {}, [], []
        for apid, index, shape in _records(editor):
            kind = _kind(editor.storage_dict[apid], shape)
            follow = shape.geo.get('follow')
            if kind == 'pad' and _inside(bounds, *follow.coords[0][:2]):
                result = DrawToolShape(dict(shape.geo, follow=translate(follow, dx, dy),
                                            solid=translate(shape.geo['solid'], dx, dy)))
                pads.append(result)
            elif kind == 'track':
                coords = [tuple(coord[:2]) for coord in follow.coords]
                new = [(x + dx, y + dy) if _inside(bounds, x, y) else (x, y) for x, y in coords]
                if new == coords:
                    continue
                new = _dedupe(new)
                if len(new) < 2:
                    raise ValueError(_("The stretch would collapse a track."))
                result = _track(editor, apid, shape, LineString(new))
            else:
                continue
            replacements.setdefault(apid, {})[index] = [result]
            moved.append(result)
        if not moved:
            raise ValueError(_("Nothing to stretch inside the rectangle."))
    except ERRORS as err:
        return _fail(editor, err)
    editor.history.native_shapes.update(moved)
    editor.history.translated_shapes.update(pads)
    # a moved flash keeps the aperture shape at its new follow point
    mark_flashes(editor, pads)
    commit(editor, replacements, moved)
    return moved


def _distance_to_segment(point, a, b):
    ax, ay = a[:2]
    bx, by = b[:2]
    length2 = (bx - ax) ** 2 + (by - ay) ** 2
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((point[0] - ax) * (bx - ax) + (point[1] - ay) * (by - ay)) / length2))
    return math.hypot(point[0] - (ax + t * (bx - ax)), point[1] - (ay + t * (by - ay)))


def nearest_segment(editor, point, tolerance):
    """(apid, index, shape, segment index) of the track segment nearest to point within tolerance, or None."""
    best, best_distance = None, None
    for apid, index, shape in _records(editor):
        if _kind(editor.storage_dict[apid], shape) != 'track':
            continue
        coords = list(shape.geo['follow'].coords)
        for number, (a, b) in enumerate(zip(coords, coords[1:])):
            distance = _distance_to_segment(point, a, b)
            if distance <= tolerance and (best_distance is None or distance < best_distance):
                best, best_distance = (apid, index, shape, number), distance
    return best


@gerber_operation(_('Insert elbow'))
def apply_insert_elbow(editor, point, vertex, tolerance, orthogonal=False):
    """Replace the track segment nearest to point (A to B) by A-vertex-B, or A-(vx, ay)-vertex-(bx, vy)-B."""
    try:
        tolerance = float(tolerance)
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError(_("The tolerance must be finite and nonnegative."))
        point, vertex = _xy(point), _xy(vertex)
        hit = nearest_segment(editor, point, tolerance)
        if hit is None:
            raise ValueError(_("No track near the clicked point."))
        apid, index, shape, number = hit
        coords = [tuple(coord[:2]) for coord in shape.geo['follow'].coords]
        a, b = coords[number], coords[number + 1]
        inserted = [(vertex[0], a[1]), vertex, (b[0], vertex[1])] if orthogonal else [vertex]
        new = _dedupe(coords[:number + 1] + inserted + coords[number + 1:])
        if new == coords or len(new) < 2:
            raise ValueError(_("The elbow doesn't change the track."))
        result = _track(editor, apid, shape, LineString(new))
    except ERRORS as err:
        return _fail(editor, err)
    editor.history.native_shapes.add(result)
    commit(editor, {apid: {index: [result]}}, [result])
    return [result]


# ---------------------------------------------------------------------------------------------------------------------
# canvas tools
# ---------------------------------------------------------------------------------------------------------------------
def _tolerance(draw_app):
    """The pick tolerance in app units: `track_edit_tolerance` of the editor (from the option
    gerber_editor_track_edit_tolerance)."""
    return draw_app.track_edit_tolerance


def _snap(draw_app, point):
    app = draw_app.app
    try:
        if app.grid_status():
            return tuple(app.geo_editor.snap(point[0], point[1]))
    except (AttributeError, TypeError):
        pass
    return tuple(point[:2])


def _utility(polygon=None, line=None):
    if polygon is not None:
        return DrawToolUtilityShape({'solid': polygon, 'follow': LineString(polygon.exterior.coords)})
    return DrawToolUtilityShape({'solid': line, 'follow': line})


class TrackEditTool(DrawTool):
    """Base of the tools: the click picks, so it uses the raw position; the tool stays active after each action."""
    uses_raw_pick = True
    clear_utility_on_none = True

    def __init__(self, draw_app, name, message):
        super().__init__(draw_app)
        self.name = name
        self.press = None
        self.cursor = None
        draw_app.in_action = False
        self.inform(message)

    def inform(self, message):
        self.draw_app.app.inform.emit(message)

    def warn(self, message):
        self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' % message)

    def done(self, result):
        if result != 'fail':
            self.inform('[success] %s' % _("Done."))

    def is_drag(self, start, end):
        return start is not None and math.hypot(end[0] - start[0], end[1] - start[1]) > _tolerance(self.draw_app)

    def click(self, point):
        self.press = tuple(point[:2])

    def clean_up(self):
        self.press = None

    def utility_geometry(self, data=None):
        if data is not None:
            self.cursor = tuple(data[:2])
        return self.preview()

    def preview(self):
        return None


class RectangleTool(TrackEditTool):
    """Drag a rectangle; the operation runs on release."""

    def click_release(self, point):
        start, self.press = self.press, None
        end = tuple(point[:2])
        if not self.is_drag(start, end):
            self.warn(_("Drag a rectangle."))
            return 'fail'
        bounds = (min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1]))
        self.done(self.run(bounds))

    def preview(self):
        if self.press is None or self.cursor is None:
            return None
        x1, y1, x2, y2 = self.press[0], self.press[1], self.cursor[0], self.cursor[1]
        if x1 == x2 or y1 == y2:
            return None
        return _utility(polygon=box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))

    def run(self, bounds):
        raise NotImplementedError


class ClipEditorGrb(RectangleTool):
    def __init__(self, draw_app):
        super().__init__(draw_app, 'clip', _("Drag a rectangle to clip the tracks and the regions."))

    def run(self, bounds):
        return apply_clip(self.draw_app, bounds)


class ClipPadEditorGrb(RectangleTool):
    def __init__(self, draw_app):
        super().__init__(draw_app, 'clip_pad', _("Drag a rectangle to clip and replace its content with a pad."))

    def run(self, bounds):
        return apply_clip_replace_pad(self.draw_app, bounds)


class StretchEditorGrb(TrackEditTool):
    """Click an endpoint then the target, or drag a rectangle then click 2 reference points."""

    def __init__(self, draw_app):
        super().__init__(draw_app, 'stretch', _("Click a track end or drag a rectangle to stretch."))
        self.reset()

    def reset(self):
        self.state = 'pick'
        self.bounds = None
        self.origin = None

    def nearest_end(self, point):
        tolerance, best, best_distance = _tolerance(self.draw_app), None, None
        for apid, index, shape in _records(self.draw_app):
            kind = _kind(self.draw_app.storage_dict[apid], shape)
            if kind == 'pad':
                candidates = [shape.geo['follow'].coords[0]]
            elif kind == 'track':
                coords = shape.geo['follow'].coords
                candidates = [coords[0], coords[-1]]
            else:
                continue
            for coord in candidates:
                distance = math.hypot(coord[0] - point[0], coord[1] - point[1])
                if distance <= tolerance and (best_distance is None or distance < best_distance):
                    best, best_distance = tuple(coord[:2]), distance
        return best

    def click_release(self, point):
        start, self.press = self.press, None
        point = tuple(point[:2])
        if self.state == 'pick':
            if self.is_drag(start, point):
                self.bounds = (min(start[0], point[0]), min(start[1], point[1]),
                               max(start[0], point[0]), max(start[1], point[1]))
                self.state = 'ref1'
                self.inform(_("Click the reference point."))
                return
            end = self.nearest_end(point)
            if end is None:
                self.warn(_("No track end near the click."))
                return 'fail'
            half = max(_tolerance(self.draw_app) * 0.05, 1e-9)
            self.bounds = (end[0] - half, end[1] - half, end[0] + half, end[1] + half)
            self.origin = end
            self.state = 'target'
            self.inform(_("Click the new position."))
        elif self.state == 'ref1':
            self.origin = _snap(self.draw_app, point)
            self.state = 'target'
            self.inform(_("Click the new position."))
        else:
            target = _snap(self.draw_app, point)
            bounds, origin = self.bounds, self.origin
            self.reset()
            self.done(apply_stretch(self.draw_app, bounds, target[0] - origin[0], target[1] - origin[1]))
            self.inform(_("Click a track end or drag a rectangle to stretch."))

    def clean_up(self):
        super().clean_up()
        self.reset()

    def preview(self):
        if self.cursor is None:
            return None
        if self.state == 'pick' and self.press is not None:
            x1, y1, x2, y2 = self.press[0], self.press[1], self.cursor[0], self.cursor[1]
            if x1 != x2 and y1 != y2:
                return _utility(polygon=box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        elif self.state == 'target' and self.origin != tuple(self.cursor):
            return _utility(line=LineString([self.origin, self.cursor]))
        return None


class ElbowEditorGrb(TrackEditTool):
    """Click the track, then click the vertex of the elbow."""
    orthogonal = False

    def __init__(self, draw_app, name='elbow', message=None):
        super().__init__(draw_app, name, message or _("Click the track, then the elbow vertex."))
        self.first = None

    def click(self, point):
        point = tuple(point[:2])
        if self.first is None:
            if nearest_segment(self.draw_app, point, _tolerance(self.draw_app)) is None:
                self.warn(_("No track near the click."))
                return 'fail'
            self.first = point
            self.inform(_("Click the elbow vertex."))
            return
        first, self.first = self.first, None
        self.done(apply_insert_elbow(self.draw_app, first, _snap(self.draw_app, point),
                                     _tolerance(self.draw_app), self.orthogonal))
        self.inform(_("Click the track, then the elbow vertex."))

    def clean_up(self):
        super().clean_up()
        self.first = None

    def preview(self):
        if self.first is None or self.cursor is None or self.first == tuple(self.cursor):
            return None
        return _utility(line=LineString([self.first, self.cursor]))


class Elbow90EditorGrb(ElbowEditorGrb):
    orthogonal = True

    def __init__(self, draw_app):
        super().__init__(draw_app, 'elbow90', _("Click the track, then the 90 degree elbow vertex."))


# The tools that need click_release from the editor and no rubber band selection.
TRACK_EDIT_TOOLS = (ClipEditorGrb, ClipPadEditorGrb, StretchEditorGrb, ElbowEditorGrb)
