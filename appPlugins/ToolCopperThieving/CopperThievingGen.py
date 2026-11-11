from copy import deepcopy

from shapely import Polygon, MultiPolygon, Point, LineString, box
from shapely.affinity import translate
from shapely.ops import unary_union

from appCommon.Common import GracefulException
from camlib import flatten_shapely_geometry, deepcopy_keep_geometry


class GridParameterError(ValueError):
    """The fill grid size or spacing is out of range."""


def _flat(geometry):
    """Flat list of the shapes of a geometry, [] for None."""
    return [] if geometry is None else flatten_shapely_geometry(geometry)


def _quad_segs(circle_steps):
    """The app counts the steps of the whole circle, Shapely counts the segments of a quarter."""
    return max(1, int(circle_steps) // 4)


def robber_bar(solid_geometry, margin, thickness, circle_steps=64):
    quad_segs = _quad_segs(circle_steps)
    outline = unary_union(_flat(solid_geometry)).envelope
    line = outline.buffer(margin + thickness / 2.0, quad_segs=quad_segs).exterior
    return line, line.buffer(thickness / 2.0, quad_segs=quad_segs)


def robber_bar_data(solid_geometry, tools, follow_geometry, margin, thickness, circle_steps=64):
    """Return the bar outputs and independent merged Gerber geometry and apertures."""
    line, bar = robber_bar(solid_geometry, margin, thickness, circle_steps)
    apertures = deepcopy_keep_geometry(tools)
    aperture_found = None
    for ap_id, ap_val in tools.items():
        if ap_val['type'] == 'C' and ap_val['size'] == thickness:
            aperture_found = ap_id
            break
    if aperture_found:
        apertures[aperture_found]['geometry'].append({'solid': bar, 'follow': line})
    else:
        # Gerber aperture ids start at 10; the REG key 0 of the generated apertures does not count
        new_apid = max([10] + [int(key) + 1 for key in apertures])
        apertures[new_apid] = {
            'type': 'C', 'size': deepcopy(thickness),
            'geometry': [{'solid': bar, 'follow': line}]}
    geometry = deepcopy_keep_geometry(_flat(solid_geometry)) + [bar]
    follows = deepcopy_keep_geometry(_flat(follow_geometry)) + [line]
    return line, bar, unary_union(geometry), apertures, follows


def copper_fill(solid_geometry, tools, reference_geometry, reference_kind, reference, box_type,
                clearance, margin, min_area, fill_type, dot_dia, dot_spacing, square_size,
                square_spacing, line_size, line_spacing, circle_steps, abort=None, progress=None,
                target_geometry=None):
    """Return fill, merged copper and independent aperture records from explicit geometry inputs."""
    def check_abort():
        if abort is not None and abort():
            raise GracefulException

    def report(stage, percent=None):
        if progress is not None:
            progress(stage, percent)

    if fill_type in (1, 2, 3):
        size, spacing = {1: (dot_dia, dot_spacing), 2: (square_size, square_spacing),
                         3: (line_size, line_spacing)}[fill_type]
        if size <= 0 or spacing < 0:
            raise GridParameterError('Grid size must be positive and spacing must be non-negative.')

    geometry = flatten_shapely_geometry(solid_geometry)
    if target_geometry is None:
        target_geometry = solid_geometry
    steps = _quad_segs(circle_steps)
    buffered = []
    for index, poly in enumerate(geometry):
        check_abort()
        buffered.append(poly.buffer(clearance, steps))
        report('Thieving', int((index + 1) * 100 / len(geometry)))
    report('Buffering')
    clearance_geometry = unary_union(buffered)
    report('Working...')

    if reference == 0:
        target = unary_union(flatten_shapely_geometry(target_geometry))
        envelope = target.convex_hull if box_type == 'min' else target.envelope
        boundary = envelope.buffer(margin, join_style='mitre')
    elif reference == 1:
        areas = []
        for poly in flatten_shapely_geometry(reference_geometry):
            check_abort()
            areas.append(poly.buffer(margin, join_style='mitre'))
        boundary = unary_union(areas)
    elif reference_kind in ('geometry', 'excellon'):
        areas = []
        for poly in flatten_shapely_geometry(reference_geometry):
            check_abort()
            areas.append(poly.buffer(margin, join_style='mitre'))
        boundary = unary_union(areas)
    elif reference_kind == 'gerber':
        boundary = unary_union(flatten_shapely_geometry(target_geometry)).convex_hull.intersection(
            unary_union(flatten_shapely_geometry(reference_geometry)).convex_hull).buffer(
            margin, join_style='mitre')
    else:
        raise ValueError('The reference object type is not supported.')

    fill = [poly for poly in flatten_shapely_geometry(boundary.difference(clearance_geometry))
            if poly.area >= min_area]
    boundary = boundary.envelope
    x0, y0, x1, y1 = boundary.bounds
    report('Create geometry')

    if fill_type in (1, 2):
        size, spacing = (dot_dia, dot_spacing) if fill_type == 1 else (square_size, square_spacing)
        half_size = size / 2.0
        patterns = []
        x = x0 + half_size
        y = y0 + half_size
        while x <= x1 - half_size:
            check_abort()
            while y <= y1 - half_size:
                check_abort()
                if fill_type == 1:
                    pattern = Point(x, y).buffer(half_size, quad_segs=steps)
                else:
                    pattern = box(x - half_size, y - half_size, x + half_size, y + half_size)
                patterns.append(pattern)
                y += size + spacing
            x += size + spacing
            y = y0 + half_size
        pattern_geometry = MultiPolygon(patterns)
        if patterns:
            pattern_geometry = translate(pattern_geometry, xoff=boundary.centroid.x - pattern_geometry.centroid.x,
                                         yoff=boundary.centroid.y - pattern_geometry.centroid.y)
        fill = [pattern for pattern in flatten_shapely_geometry(pattern_geometry)
                for poly in fill if pattern.within(poly)]

    if fill_type == 3 and fill:
        allowed = unary_union(fill)
        half_size = line_size / 2.0
        outlines = []
        for poly in geometry:
            check_abort()
            outlines.append(poly.buffer(clearance + half_size, steps))
        outlines = unary_union([poly.exterior.buffer(half_size, quad_segs=steps)
                               for poly in flatten_shapely_geometry(unary_union(outlines))])
        inner_box = box(x0, y0, x1, y1).buffer(-half_size)
        border = inner_box.exterior.buffer(half_size, quad_segs=steps)
        bx0, by0, bx1, by1 = inner_box.bounds
        lines = []
        x, y = bx0, by0
        while x <= x1 - half_size:
            check_abort()
            lines.append(LineString([(x, by0), (x, by1)]).buffer(half_size, quad_segs=steps))
            x += line_size + line_spacing
        while y <= y1 - half_size:
            check_abort()
            lines.append(LineString([(bx0, y), (bx1, y)]).buffer(half_size, quad_segs=steps))
            y += line_size + line_spacing
        fill = [poly for candidate in [outlines, border] + lines
                for poly in flatten_shapely_geometry(candidate.intersection(allowed))
                if isinstance(poly, Polygon) and not poly.is_empty]

    report('Append geometry')
    apertures = deepcopy_keep_geometry(tools)
    if 0 not in apertures:
        apertures[0] = {'type': 'REG', 'size': 0.0, 'geometry': []}
    for poly in flatten_shapely_geometry(fill):
        apertures[0]['geometry'].append({'solid': poly, 'follow': poly.exterior})
    merged = MultiPolygon(geometry + flatten_shapely_geometry(fill)).buffer(0.0000001).buffer(-0.0000001)
    return fill, merged, apertures


def plating_mask(solid_geometry, tools, follow_geometry, thieving_geometry, robber_geometry, robber_follow,
                 clearance, choice, only_pads, circle_steps=64):
    quad_segs = _quad_segs(circle_steps)
    geo_list = []
    if only_pads is False:
        geo_list = deepcopy_keep_geometry(_flat(solid_geometry))
    else:
        for aperture in tools.values():
            for elem in aperture.get('geometry', []):
                if 'follow' in elem and isinstance(elem['follow'], Point) and 'solid' in elem:
                    geo_list.append(elem['solid'])
    if clearance != 0:
        geo_list = [geo.buffer(clearance, quad_segs=quad_segs) for geo in geo_list]
    thieving = deepcopy_keep_geometry(_flat(thieving_geometry))
    robber = unary_union(deepcopy_keep_geometry(_flat(robber_geometry)))
    if clearance != 0:
        robber = robber.buffer(clearance, quad_segs=quad_segs)
    robber_line = unary_union(deepcopy_keep_geometry(_flat(robber_follow)))
    follows = deepcopy_keep_geometry(_flat(follow_geometry))
    if only_pads:
        follows = [elem['follow'] for aperture in tools.values()
                   for elem in aperture.get('geometry', [])
                   if isinstance(elem.get('follow'), Point) and 'solid' in elem]
    if thieving and choice in [0, 1]:
        for poly in thieving:
            geo_list.append(poly.buffer(clearance, quad_segs=quad_segs))
            if isinstance(poly, Polygon) and not poly.is_empty:
                follows.append(poly.exterior)
    if not robber.is_empty and choice in [0, 2]:
        follows.append(robber_line)
        geo_list.append(robber)
    solid = unary_union(geo_list)
    # Export the final union only: separate source flashes/strokes can be erased by region holes.
    # Enclosing exteriors must precede islands, even when a thin ring has less copper area.
    regions = sorted((poly for poly in flatten_shapely_geometry(solid)
                      if isinstance(poly, Polygon) and not poly.is_empty),
                     key=lambda poly: Polygon(poly.exterior).area, reverse=True)
    apertures = {0: {'type': 'REG', 'size': 0.0, 'geometry': [
        {'solid': poly, 'follow': poly.exterior} for poly in regions]}}
    return solid, apertures, follows, solid.area
