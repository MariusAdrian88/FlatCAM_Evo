"""Gerber Editor automatic tools: drawn pads to flashes, teardrops and snowman fillets."""

import math
import gettext
import builtins

from shapely import LineString, Point, Polygon, STRtree, box
from shapely.errors import GEOSException

from appEditors.grb_plugins.GrbCommon import DrawToolShape, numeric_aperture_codes, gerber_quad, gerber_steps
from appEditors.grb_plugins.GrbHistory import gerber_operation, mark_flashes
from appParsers.ParseGerber import Gerber
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

DIM_DECIMALS = 6


def owner_entries(editor, shapes):
    """(aperture code, record) of the stored records among shapes, in storage order."""
    wanted = set(shapes)
    return [(apid, shape) for apid, aperture in editor.storage_dict.items()
            for shape in aperture.get('geometry', ()) if shape in wanted]


def plain_aperture(aperture):
    return not any(aperture.get(key) for key in ('hole', 'macro', 'modifiers'))


def find_or_add_aperture(editor, new_apertures, kind, **dims):
    """The code of a plain aperture of this type and size, an existing one first; a new one is added to new_apertures."""
    def same(aperture):
        return (aperture.get('type') == kind and plain_aperture(aperture) and
                all(math.isclose(float(aperture.get(key, -1)), value, rel_tol=1e-9, abs_tol=1e-9)
                    for key, value in dims.items()))

    for code, aperture in list(editor.storage_dict.items()) + list(new_apertures.items()):
        if same(aperture):
            return code
    code = max([9] + numeric_aperture_codes(list(editor.storage_dict) + list(new_apertures))) + 1
    if kind == 'C':
        new_apertures[code] = {'type': 'C', 'size': dims['size'], 'geometry': []}
    else:
        new_apertures[code] = {'type': kind, 'size': math.hypot(dims['width'], dims['height']),
                               'width': dims['width'], 'height': dims['height'], 'geometry': []}
    return code


def flash_solid(editor, aperture, center):
    """The solid that the editor makes for a flash of a C, R or O aperture (as the Pad tool does)."""
    kind = aperture['type']
    if kind == 'C':
        radius = float(aperture['size']) / 2
        return center.buffer(radius, quad_segs=gerber_quad(editor.app, radius))
    half_width, half_height = float(aperture['width']) / 2, float(aperture['height']) / 2
    x, y = center.x, center.y
    if kind == 'R':
        return Polygon([(x - half_width, y - half_height), (x + half_width, y - half_height),
                        (x + half_width, y + half_height), (x - half_width, y + half_height)])
    steps = gerber_steps(editor.app, min(half_width, half_height))
    return Gerber.create_flash_geometry(editor, center, aperture, steps)


def recognise(solid, tolerance):
    """('C', center, {size}) | ('R'|'O', center, {width, height}) for a solid that is that shape, else None."""
    if not isinstance(solid, Polygon) or solid.is_empty or not solid.is_valid or solid.interiors:
        return None
    limit = tolerance * solid.length
    minx, miny, maxx, maxy = solid.bounds
    width, height = maxx - minx, maxy - miny
    if width <= 0 or height <= 0:
        return None
    center = Point((minx + maxx) / 2, (miny + maxy) / 2)
    ratio = width / height
    if 0.5 < ratio < 2:
        diameter = (width + height) / 2
        if solid.symmetric_difference(center.buffer(diameter / 2, 64)).area <= limit:
            return 'C', center, {'size': round(diameter, DIM_DECIMALS)}
    if solid.symmetric_difference(box(minx, miny, maxx, maxy)).area <= limit:
        return 'R', center, {'width': round(width, DIM_DECIMALS), 'height': round(height, DIM_DECIMALS)}
    short = min(width, height)
    if width > height:
        axis = LineString([(minx + short / 2, center.y), (maxx - short / 2, center.y)])
    else:
        axis = LineString([(center.x, miny + short / 2), (center.x, maxy - short / 2)])
    if solid.symmetric_difference(axis.buffer(short / 2, 64)).area <= limit:
        return 'O', center, {'width': round(width, DIM_DECIMALS), 'height': round(height, DIM_DECIMALS)}
    return None


def finish(editor, selected, touched):
    editor.selected = selected
    editor.touch_geometry()
    editor.build_ui()
    editor.sync_aperture_rows(touched)
    editor.plot_all()


def register(editor, new_apertures):
    editor.storage_dict.update(new_apertures)
    for code in new_apertures:
        editor.oldapcode_newapcode.setdefault(code, code)


@gerber_operation(_('Drawn pads to flashes'), all_apertures=True)
def apply_pads_to_flashes(editor, tolerance):
    try:
        tolerance = float(tolerance)
        if not math.isfinite(tolerance) or tolerance <= 0:
            raise ValueError(_("The tolerance must be a finite positive number."))
    except (ValueError, TypeError) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    candidates = owner_entries(editor, editor.selected) if editor.selected else \
        [(apid, shape) for apid, aperture in editor.storage_dict.items() for shape in aperture.get('geometry', ())]
    new_apertures, removed, added, converted, skipped = {}, set(), {}, {}, 0
    for apid, shape in candidates:
        follow = shape.geo.get('follow')
        solid = shape.geo.get('solid')
        if type(follow) is Point or solid is None:
            continue
        if not getattr(shape.geo.get('clear'), 'is_empty', True):
            skipped += 1
            continue
        is_region = editor.storage_dict[apid].get('type') == 'REG'
        if not is_region:
            # only a short track: a drawn pad is a track of no more than its own width
            width = solid.bounds[2] - solid.bounds[0], solid.bounds[3] - solid.bounds[1]
            if not isinstance(follow, LineString) or follow.length > min(width):
                skipped += 1
                continue
        try:
            found = recognise(solid, tolerance)
        except GEOSException:
            found = None
        if found is None:
            skipped += 1
            continue
        kind, center, dims = found
        code = find_or_add_aperture(editor, new_apertures, kind, **dims)
        aperture = new_apertures.get(code, editor.storage_dict.get(code))
        result = DrawToolShape(dict(shape.geo, solid=flash_solid(editor, dict(aperture, type=kind), center),
                                    follow=center))
        removed.add(shape)
        added.setdefault(code, []).append(result)
        converted[shape] = result
    if converted:
        # flash_solid made each result with the shape of its aperture at its center
        mark_flashes(editor, converted.values())
        register(editor, new_apertures)
        for aperture in editor.storage_dict.values():
            if any(shape in removed for shape in aperture.get('geometry', ())):
                aperture['geometry'][:] = [shape for shape in aperture['geometry'] if shape not in removed]
        for code, shapes in added.items():
            editor.storage_dict[code]['geometry'].extend(shapes)
        selected = [converted.get(shape, shape) for shape in editor.selected] if editor.selected \
            else list(converted.values())
        finish(editor, selected, set(added) | {apid for apid, _shape in candidates})
        editor.app.inform.emit("[success] %s" % _("{n} drawn pads converted to flashes").format(n=len(converted)))
    return len(converted), skipped


def pad_ends(editor):
    """(pad, center, radius, track record, ends of the track inside the pad) for each track ending in a round flash."""
    flashes = [(apid, shape) for apid, shape in owner_entries(editor, editor.selected)
               if type(shape.geo.get('follow')) is Point and editor.storage_dict[apid].get('type') == 'C']
    if not flashes:
        flashes = [(apid, shape) for apid, aperture in editor.storage_dict.items()
                   if aperture.get('type') == 'C' for shape in aperture.get('geometry', ())
                   if type(shape.geo.get('follow')) is Point]
    tracks = [shape for aperture in editor.storage_dict.values() if aperture.get('type') == 'C'
              for shape in aperture.get('geometry', ())
              if type(shape.geo.get('follow')) is LineString and isinstance(shape.geo.get('solid'), Polygon)]
    found = []
    if not tracks:
        return found
    # a spatial index: each pad tests only the tracks near it, not all the tracks of the layer
    tree = STRtree([track.geo['follow'] for track in tracks])
    for apid, pad in flashes:
        center = pad.geo['follow']
        radius = float(editor.storage_dict[apid]['size']) / 2
        solid = pad.geo.get('solid')
        if radius <= 0 or solid is None:
            continue
        for index in tree.query(solid, predicate='intersects'):
            track = tracks[int(index)]
            line = track.geo['follow']
            if line.is_empty or line.length <= 0 or line.is_ring:
                continue
            for oriented in (line, LineString(line.coords[::-1])):
                end = Point(oriented.coords[0])
                if solid.contains(end) and not solid.contains(Point(oriented.coords[-1])):
                    found.append((center, radius, track, oriented, end))
    return found


def exit_distance(line, center, radius):
    """The distance along line (that starts in the pad) where it leaves the circle, or None."""
    crossing = line.intersection(center.buffer(radius, 256).exterior)
    points = [part for part in getattr(crossing, 'geoms', [crossing]) if isinstance(part, Point)]
    if not points:
        return None
    return min(line.project(point) for point in points)


def direction(line, position):
    """The unit vector of the line at this distance along it."""
    span = min(1e-6 * max(line.length, 1e-9), line.length / 2)
    start = line.interpolate(max(0.0, position - span))
    end = line.interpolate(min(line.length, position + span))
    dx, dy = end.x - start.x, end.y - start.y
    norm = math.hypot(dx, dy)
    return (dx / norm, dy / norm) if norm else None


@gerber_operation(_('Teardrops'), all_apertures=True)
def apply_teardrops(editor, length_pct, width_pct):
    try:
        length_pct, width_pct = float(length_pct), float(width_pct)
        if not (math.isfinite(length_pct) and math.isfinite(width_pct) and length_pct > 0 and 0 < width_pct <= 100):
            raise ValueError(_("The teardrop length must be positive and the width between 0 and 100 percent."))
    except (ValueError, TypeError) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    records = []
    for center, radius, track, line, end in pad_ends(editor):
        diameter = 2 * radius
        track_width = 2 * track.geo['solid'].exterior.distance(end)
        chord = diameter * width_pct / 100
        length = diameter * length_pct / 100
        leave = exit_distance(line, center, radius)
        if leave is None or track_width >= chord or line.length - leave < length:
            continue
        axis = direction(line, leave)
        tip_axis = direction(line, leave + length)
        if axis is None or tip_axis is None:
            continue
        half = chord / 2
        depth = math.sqrt(max(radius ** 2 - half ** 2, 0.0))
        base = (center.x + axis[0] * depth, center.y + axis[1] * depth)
        tip = line.interpolate(leave + length)
        points = [(base[0] - axis[1] * half, base[1] + axis[0] * half),
                  (base[0] + axis[1] * half, base[1] - axis[0] * half),
                  (tip.x - tip_axis[1] * track_width / 2, tip.y + tip_axis[0] * track_width / 2),
                  (tip.x + tip_axis[1] * track_width / 2, tip.y - tip_axis[0] * track_width / 2)]
        drop = Polygon(points).convex_hull
        if not isinstance(drop, Polygon) or drop.is_empty or not drop.is_valid:
            continue
        extra = {'to': track.geo['to']} if track.geo.get('to') is not None else {}
        records.append(DrawToolShape({'solid': drop, 'follow': LineString(drop.exterior.coords), **extra}))
    if not records:
        editor.app.inform.emit("[WARNING_NOTCL] %s" % _("No teardrop could be added."))
        return []
    key = next((code for code, aperture in editor.storage_dict.items() if aperture['type'] == 'REG'), None)
    if key is None:
        key = 0 if not any(str(code) == '0' for code in editor.storage_dict) else \
            max(numeric_aperture_codes(editor.storage_dict), default=0) + 1
    region = editor.storage_dict.setdefault(key, {'type': 'REG', 'size': 0.0, 'geometry': []})
    editor.oldapcode_newapcode.setdefault(key, key)
    region['geometry'].extend(records)
    editor.loaded_as_regions = True
    finish(editor, list(records), {key})
    editor.app.inform.emit("[success] %s" % _("{n} teardrops added").format(n=len(records)))
    return records


@gerber_operation(_('Snowman fillets'), all_apertures=True)
def apply_snowman(editor, size_pct, position_pct):
    try:
        size_pct, position_pct = float(size_pct), float(position_pct)
        if not (math.isfinite(size_pct) and math.isfinite(position_pct) and size_pct > 0 and position_pct >= 0):
            raise ValueError(_("The snowman size must be positive and its position not negative."))
    except (ValueError, TypeError) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    new_apertures, added, records = {}, {}, []
    for center, radius, track, line, end in pad_ends(editor):
        leave = exit_distance(line, center, radius)
        offset = radius * position_pct / 100
        if leave is None or line.length - leave < offset:
            continue
        diameter = round(2 * radius * size_pct / 100, DIM_DECIMALS)
        if diameter <= 0:
            continue
        spot = line.interpolate(leave + offset)
        code = find_or_add_aperture(editor, new_apertures, 'C', size=diameter)
        aperture = new_apertures.get(code, editor.storage_dict.get(code))
        extra = {'to': track.geo['to']} if track.geo.get('to') is not None else {}
        record = DrawToolShape({'solid': flash_solid(editor, aperture, spot), 'follow': spot, **extra})
        added.setdefault(code, []).append(record)
        records.append(record)
    if not records:
        editor.app.inform.emit("[WARNING_NOTCL] %s" % _("No snowman fillet could be added."))
        return []
    mark_flashes(editor, records)
    register(editor, new_apertures)
    for code, shapes in added.items():
        editor.storage_dict[code]['geometry'].extend(shapes)
    finish(editor, list(records), set(added))
    editor.app.inform.emit("[success] %s" % _("{n} snowman fillets added").format(n=len(records)))
    return records
