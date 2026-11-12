"""Native circular FOLLOW edits. Never weld endpoints or convert edited records to regions."""

import math
import gettext
import builtins

from shapely import LineString, Point, STRtree
from shapely.errors import GEOSException
from shapely.ops import linemerge, substring, unary_union

from appEditors.grb_plugins.GrbCommon import DrawTool, DrawToolShape, numeric_aperture_codes, gerber_quad, \
    stored_quad_candidates
from appEditors.grb_plugins.GrbHistory import gerber_operation, metadata
from appEditors.grb_plugins.GrbTrackBend import scene_state, sweep_parameters
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def selected_entries(editor):
    selected = set(editor.selected)
    if not selected or len(selected) != len(editor.selected):
        raise ValueError(_("Select distinct stored features first."))
    owners = {shape: [] for shape in selected}
    for apid, aperture in editor.storage_dict.items():
        for index, shape in enumerate(aperture.get('geometry', ())):
            if shape in owners:
                owners[shape].append((apid, index, shape))
    if any(len(entries) != 1 for entries in owners.values()):
        raise ValueError(_("Each selected feature must have exactly one aperture owner."))
    return [owners[shape][0] for shape in editor.selected]


def circular_aperture(aperture):
    width = float(aperture.get('size', 0))
    if (aperture.get('type') != 'C' or not math.isfinite(width) or width <= 0 or
            any(('hole' in key.lower() or key in ('macro', 'modifiers')) and value
                for key, value in aperture.items() if key != 'geometry')):
        raise ValueError(_("Only plain circular apertures without holes or macros are supported."))
    return width


def check_junctions(editor, entries):
    """An endpoint can have one continuation, never an interior crossing or a third incident path."""
    # a clear object (Native load, no 'solid') is not a track of copper: the FOLLOW tools leave it as it is
    selected = {shape for shape in editor.selected if isinstance(shape.geo.get('follow'), LineString)
                and shape.geo.get('solid') is not None and not shape.geo['solid'].is_empty}
    lines = [shape.geo.get('follow') for aperture in editor.storage_dict.values()
             for shape in aperture.get('geometry', ()) if shape not in selected
             and getattr(shape.geo.get('follow'), 'geom_type', '') in
             ('LineString', 'LinearRing', 'MultiLineString')]
    selected_start = len(lines)
    lines.extend(entry[2].geo['follow'] for entry in entries)
    tree = STRtree(lines)
    for selected_index, (apid, index, shape) in enumerate(entries):
        follow = shape.geo['follow']
        incident = {}
        for other_index in tree.query(follow, predicate='intersects'):
            other = lines[int(other_index)]
            if int(other_index) == selected_start + selected_index:
                continue
            intersection = follow.intersection(other)
            if (intersection.geom_type != 'Point' or type(other) is not LineString or
                    tuple(intersection.coords[0]) not in (follow.coords[0], follow.coords[-1]) or
                    tuple(intersection.coords[0]) not in (other.coords[0], other.coords[-1])):
                raise ValueError(_("The selected FOLLOW contains a branch, crossing or overlap."))
            point = tuple(intersection.coords[0])
            incident[point] = incident.get(point, 0) + 1
            if incident[point] > 1:
                raise ValueError(_("Branching FOLLOW endpoints cannot be edited."))


def same_footprint(before, after, width):
    # Ignore GEOS roundoff only, not tessellation changes or a new bridge's material.
    epsilon = max(1e-12, width * 1e-10)
    return (before.hausdorff_distance(after) <= epsilon and
            before.symmetric_difference(after).area <= epsilon * max(width, before.length))


def edit_state(editor):
    return editor.history.session, editor.geometry_revision, tuple(editor.selected), scene_state(editor)


def commit(editor, replacements, selected, apertures=None):
    """Replace lists in place, retaining unaffected records and aperture insertion order."""
    editor.history.capture(replacements)
    if apertures:
        editor.storage_dict.update(apertures)
    for apid, by_index in replacements.items():
        storage = editor.storage_dict[apid]['geometry']
        storage[:] = [result for index, shape in enumerate(storage)
                      for result in by_index.get(index, [shape])]
    editor.selected = selected
    editor.touch_geometry()
    editor.build_ui()
    chosen = set(selected)
    editor.sync_aperture_rows(apid for apid, aperture in editor.storage_dict.items()
                              if any(shape in chosen for shape in aperture.get('geometry', ())))
    editor.history.needs_plot = True


@gerber_operation(_('Split FOLLOW path'))
def apply_path_split(editor, point, tolerance, target=None, session=None, revision=None, state=None):
    try:
        before = edit_state(editor)
        entries = selected_entries(editor)
        if len(entries) != 1:
            raise ValueError(_("Select exactly one open circular-aperture FOLLOW path to split."))
        apid, index, shape = entries[0]
        if ((target is not None and shape is not target) or
                (session is not None and session != editor.history.session) or
                (revision is not None and revision != editor.geometry_revision) or
                (state is not None and state != scene_state(editor))):
            raise ValueError(_("The selected path or editing session changed. Reopen Split path."))
        width = circular_aperture(editor.storage_dict[apid])
        radius, steps = sweep_parameters(editor, editor.storage_dict[apid], shape)
        check_junctions(editor, entries)
        tolerance = float(tolerance)
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError(_("Split tolerance must be finite and nonnegative."))
        point = tuple(float(value) for value in point)
        if len(point) != 2 or not all(math.isfinite(value) for value in point):
            raise ValueError(_("Choose finite X and Y split coordinates."))
        follow = shape.geo['follow']
        if not math.isfinite(follow.length) or follow.length <= 0:
            raise ValueError(_("The FOLLOW path must have finite positive length."))
        candidate = Point(point)
        position = follow.project(candidate)
        if (follow.distance(candidate) > tolerance or position <= tolerance or
                follow.length - position <= tolerance):
            raise ValueError(_("Click inside the FOLLOW path, within tolerance and away from its endpoints."))
        pieces = [substring(follow, 0, position), substring(follow, position, follow.length)]
        if any(type(piece) is not LineString or not piece.is_valid or piece.length <= 0 for piece in pieces):
            raise ValueError(_("The split must produce two valid nonzero-length FOLLOW paths."))
        results = [DrawToolShape(dict(shape.geo, follow=piece, solid=piece.buffer(radius, steps)))
                   for piece in pieces]
        if not same_footprint(shape.geo['solid'], unary_union([item.geo['solid'] for item in results]), width):
            raise ValueError(_("This split would change the material footprint. Choose another split point."))
        if edit_state(editor) != before:
            raise ValueError(_("Geometry or selection changed during the operation. Select the features again."))
    except (ValueError, TypeError, OverflowError, GEOSException) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    editor.history.native_shapes.update(results)
    commit(editor, {apid: {index: results}}, results)
    return results


@gerber_operation(_('Join FOLLOW paths'))
def apply_path_join(editor, tolerance):
    try:
        before = edit_state(editor)
        tolerance = float(tolerance)
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError(_("Join tolerance must be finite and nonnegative."))
        entries = selected_entries(editor)
        retained = {entry[2] for entry in entries if not isinstance(entry[2].geo.get('follow'), LineString)
                    or entry[2].geo['follow'].is_empty}
        entries = [entry for entry in entries if entry[2] not in retained]
        if len(entries) < 2 or len({entry[0] for entry in entries}) != 1:
            raise ValueError(_("Select at least two open FOLLOW paths belonging to the same circular aperture."))
        apid = entries[0][0]
        aperture = editor.storage_dict[apid]
        circular_aperture(aperture)
        parameters = [sweep_parameters(editor, aperture, entry[2]) for entry in entries]
        if len(set(parameters)) != 1:
            raise ValueError(_("The selected paths use incompatible circular sweeps."))
        unique, duplicates = {}, {}
        for entry in entries:
            shape = entry[2]
            key = (shape.geo['follow'].normalize().wkb, shape.geo['solid'].normalize().wkb)
            if key in unique:
                original = unique[key][2]
                if ({k: v for k, v in shape.geo.items() if k not in ('solid', 'follow')} !=
                        {k: v for k, v in original.geo.items() if k not in ('solid', 'follow')}):
                    raise ValueError(_("Joined FOLLOW records must have compatible components and polarity."))
                duplicates[entry[1]] = []
            else:
                unique[key] = entry
        entries = list(unique.values())
        check_junctions(editor, entries)
        shapes = [entry[2] for entry in entries]
        if any(not math.isfinite(shape.geo['follow'].length) or shape.geo['follow'].length <= 0
               for shape in shapes):
            raise ValueError(_("The FOLLOW paths must have finite positive lengths."))
        extra = {key: value for key, value in shapes[0].geo.items() if key not in ('solid', 'follow')}
        if any({key: value for key, value in shape.geo.items() if key not in ('solid', 'follow')} != extra
               for shape in shapes[1:]):
            raise ValueError(_("Joined FOLLOW records must have compatible components and polarity."))
        endpoints = [Point(coord) for shape in shapes
                     for coord in (shape.geo['follow'].coords[0], shape.geo['follow'].coords[-1])]
        tree = STRtree(endpoints)
        adjacency = [set() for shape in shapes]
        for i, point in enumerate(endpoints):
            candidates = [int(j) for j in tree.query(point, predicate='dwithin', distance=tolerance)
                          if int(j) // 2 != i // 2]
            if len(candidates) > 1:
                raise ValueError(_("Ambiguous or branching endpoints. Reduce tolerance or selection."))
            for j in candidates:
                if not point.equals(endpoints[j]):
                    raise ValueError(_("Gerber joins require coincident endpoints; bridging a gap changes material."))
                adjacency[i // 2].add(j // 2)
        replacements, selected, visited = duplicates, {}, set()
        for start in range(len(shapes)):
            if start in visited:
                continue
            component, pending = set(), [start]
            while pending:
                member = pending.pop()
                if member not in component:
                    component.add(member)
                    pending.extend(adjacency[member] - component)
            visited.update(component)
            members = sorted(component, key=lambda i: entries[i][1])
            if len(members) == 1:
                selected[start] = shapes[start]
                continue
            follow = linemerge([shapes[i].geo['follow'] for i in members])
            if (type(follow) is not LineString or not follow.is_simple or follow.is_closed or
                    not follow.is_valid or not math.isclose(follow.length,
                        sum(shapes[i].geo['follow'].length for i in members), rel_tol=1e-12, abs_tol=1e-12)):
                raise ValueError(_("Join would create a ring, branch, overlap or invalid FOLLOW."))
            solid = unary_union([shapes[i].geo['solid'] for i in members])
            result = DrawToolShape(dict(shapes[members[0]].geo, follow=follow, solid=solid))
            for i in members:
                replacements[entries[i][1]] = [result] if i == members[0] else []
                selected[i] = result
        if not replacements:
            raise ValueError(_("No selected endpoints can be joined."))
        results = list(dict.fromkeys(selected[i] for i in range(len(shapes))))
        results.extend(shape for shape in editor.selected if shape in retained)
        if edit_state(editor) != before:
            raise ValueError(_("Geometry or selection changed during the operation. Select the features again."))
    except (ValueError, TypeError, OverflowError, GEOSException) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    editor.history.native_shapes.update(results)
    commit(editor, {apid: replacements}, results)
    return results


@gerber_operation(_('Resize circular flashes'))
def apply_resize_circles(editor, diameter):
    try:
        before = edit_state(editor)
        diameter = float(diameter)
        if not math.isfinite(diameter) or diameter <= 0:
            raise ValueError(_("The new diameter must be a finite positive number."))
        entries = selected_entries(editor)
        replacements, additions, new_apertures, selected = {}, {}, {}, {}
        next_code = max([9] + numeric_aperture_codes(editor.storage_dict)) + 1
        for apid, index, shape in entries:
            follow = shape.geo.get('follow')
            if type(follow) is not Point:
                continue
            aperture = editor.storage_dict[apid]
            width = circular_aperture(aperture)
            solid = shape.geo.get('solid')
            if (follow.is_empty or follow.has_z or getattr(follow, 'has_m', False) or
                    not all(math.isfinite(value) for value in follow.coords[0]) or
                    solid is None or solid.is_empty or not solid.is_valid or
                    not getattr(shape.geo.get('clear'), 'is_empty', True)):
                raise ValueError(_("Select unmodified circular flashes without clear geometry."))
            # the circle of the solid has the steps of the preference now or of the preference when it was made
            steps = next((quad for quad in stored_quad_candidates(editor, width / 2)
                          if solid.equals_exact(follow.buffer(width / 2, quad),
                                                max(1e-10, width * 1e-8), normalize=True)), None)
            if steps is None:
                raise ValueError(_("The flash solid does not match a plain circular aperture. Resize cancelled."))
            if diameter == width:
                continue
            target_meta = metadata(aperture)
            target_meta['size'] = diameter
            target = next((key for key, value in list(editor.storage_dict.items()) + list(new_apertures.items())
                           if metadata(value) == target_meta), None)
            if target is None:
                target = next_code
                next_code += 1
                new_apertures[target] = dict(target_meta, geometry=[])
            result = DrawToolShape(dict(shape.geo, solid=follow.buffer(diameter / 2, gerber_quad(editor.app, diameter / 2))))
            if result.geo['solid'].is_empty or not result.geo['solid'].is_valid:
                raise ValueError(_("The new diameter produces invalid circular geometry."))
            replacements.setdefault(apid, {})[index] = []
            additions.setdefault(target, []).append(result)
            selected[shape] = result
        if not selected:
            raise ValueError(_("No circular flash needs resizing. Select circular flashes with a different diameter."))
        if edit_state(editor) != before:
            raise ValueError(_("Geometry or selection changed during the operation. Select the features again."))
    except (ValueError, TypeError, OverflowError, GEOSException) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    editor.history.capture(additions)
    for apid, shapes in additions.items():
        aperture = new_apertures.get(apid, editor.storage_dict.get(apid))
        # Append after the existing records; their relative order is unchanged.
        end = len(aperture['geometry'])
        if end:
            by_index = replacements.setdefault(apid, {})
            by_index[end - 1] = by_index.get(end - 1, [aperture['geometry'][-1]]) + shapes
        else:
            aperture['geometry'].extend(shapes)
    commit(editor, replacements, [selected.get(shape, shape) for shape in editor.selected], new_apertures)
    editor.app.inform.emit(_("{n} circular flashes resized").format(n=len(selected)))
    return list(selected.values())


class FollowSplitEditorGrb(DrawTool):
    uses_raw_pick = True

    def __init__(self, draw_app):
        super().__init__(draw_app)
        self.name = 'split'
        self.target = draw_app.selected[0] if len(draw_app.selected) == 1 else None
        self.session = draw_app.history.session
        self.revision = draw_app.geometry_revision
        self.state = scene_state(draw_app)
        self.committed = False
        draw_app.in_action = False
        draw_app.app.inform.emit(_("Select one circular FOLLOW path, then click its interior to split it."))

    def click(self, point):
        if self.target is None:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' % _("Select one FOLLOW path and reopen Split path."))
            return 'fail'
        if self.draw_app.apply_path_split(point, self.draw_app.ui.split_tolerance_entry.text(),
                                          target=self.target, session=self.session, revision=self.revision,
                                          state=self.state) == 'fail':
            return 'fail'
        # Keep this tool until release so the new Select tool doesn't repick just one piece.
        self.committed = True

    def click_release(self, point):
        if self.committed:
            self.draw_app.select_tool('select')

    def clean_up(self):
        pass
