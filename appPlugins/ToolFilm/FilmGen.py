"""Film geometry, SVG documents and file rendering from explicit data."""

import math
import re
import xml.etree.ElementTree as ElementTree
from io import StringIO, BytesIO

from shapely import LineString, Point, Polygon, LinearRing
from shapely.affinity import scale, skew
from shapely.ops import unary_union
from camlib import flatten_shapely_geometry, circle_quad_segs
from reportlab.graphics import renderPDF
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch, mm
from reportlab.lib.pagesizes import landscape, portrait
from svglib.svglib import svg2rlg


svg_style_re = re.compile(r' (fill|stroke)="#[0-9a-fA-F]+"| opacity="[^"]*"')


class FilmError(ValueError):
    """A failure with a code; the controller maps the code to a translated text."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class PunchTooBigError(FilmError):
    """The punch hole is not smaller than an aperture of the film object."""

    def __init__(self, message='Punch hole size is bigger than an aperture'):
        super().__init__('punch_too_big', message)


def transform_geometry(geo_list, scale_factor_x=None, scale_factor_y=None, scale_reference='center', scale_type=0,
                       skew_factor_x=None, skew_factor_y=None, skew_reference='center', skew_type=0, mirror=None):
    """Transform all geometries about the frame (the first geometry), not their individual centers."""
    geo_list = list(geo_list)
    if scale_factor_x or scale_factor_y:
        xmin, ymin, xmax, ymax = geo_list[0].bounds
        ref_scale_val = {
            'topleft': (xmin, ymax), 'bottomleft': (xmin, ymin),
            'topright': (xmax, ymax), 'bottomright': (xmax, ymin)
        }.get(scale_reference, ((xmin + xmax) / 2, (ymin + ymax) / 2))

        def scale_value(factor, size):
            if not factor:
                return 1.0
            if scale_type == 1:
                return factor
            return (size + factor) / size if size else 1.0

        val_x = scale_value(scale_factor_x, xmax - xmin)
        val_y = scale_value(scale_factor_y, ymax - ymin)
        geo_list = [scale(geo, val_x, val_y, origin=ref_scale_val) for geo in geo_list]

    if skew_factor_x or skew_factor_y:
        xmin, ymin, xmax, ymax = geo_list[0].bounds
        if skew_reference == 'bottomleft':
            ref_skew_val = (xmin, ymin)
            lever = 1.0
        else:
            ref_skew_val = ((xmin + xmax) / 2, (ymin + ymax) / 2)
            lever = 0.5

        def skew_angle(factor, size, other_size):
            if not factor:
                return 0.0
            if skew_type == 1:
                return factor
            if skew_type == 0:
                offset = factor
            else:
                offset = size * factor - size
            return math.degrees(math.atan2(offset, other_size * lever))

        skew_angle_x = skew_angle(skew_factor_x, xmax - xmin, ymax - ymin)
        skew_angle_y = skew_angle(skew_factor_y, ymax - ymin, xmax - xmin)
        geo_list = [skew(geo, skew_angle_x, skew_angle_y, origin=ref_skew_val) for geo in geo_list]

    if mirror in ('x', 'y', 'both'):
        xmin, ymin, xmax, ymax = geo_list[0].bounds
        center = ((xmin + xmax) / 2, (ymin + ymax) / 2)
        fact_x = -1.0 if mirror in ('y', 'both') else 1.0
        fact_y = -1.0 if mirror in ('x', 'both') else 1.0
        geo_list = [scale(geo, fact_x, fact_y, origin=center) for geo in geo_list]
    return geo_list


def create_svg_geometry(geom, scale_stroke_factor):
    if scale_stroke_factor <= 0:
        scale_stroke_factor = 0.01
    return geom.svg(scale_factor=scale_stroke_factor)


def create_svg_document(svg_geo, box_bounds, margin, color, opacity, svg_units, background=''):
    new_attr = {
        'fill': ' fill="%s"' % color,
        'stroke': ' stroke="%s"' % color,
        None: ' opacity="%s"' % opacity
    }
    features = svg_style_re.sub(lambda match: new_attr[match.group(1)], svg_geo)
    features = features.replace('><', '>\n<')
    xmin, ymin, xmax, ymax = box_bounds
    svgwidth = (xmax - xmin) + (2 * margin)
    svgheight = (ymax - ymin) + (2 * margin)
    return '<?xml version="1.0" ?>\n' \
           '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" xmlns:xlink="http://www.w3.org/1999/xlink" ' \
           'width="%s%s" height="%s%s" viewBox="%s %s %s %s">\n' \
           '<g transform="scale(1,-1)">\n%s%s\n</g>\n</svg>\n' % (
               svgwidth, svg_units, svgheight, svg_units,
               xmin - margin, -(ymax + margin), svgwidth, svgheight, background, features)


def get_complementary(color_param):
    return "#%06X" % (0xFFFFFF ^ int(color_param[1:], 16))


def negative_background_geometry(box_geo, r_box, c_hull, margin):
    join_s = "round" if r_box else "mitre"
    neg_geo = box_geo.convex_hull if c_hull else box_geo.envelope
    if margin:
        neg_geo = neg_geo.buffer(margin, join_style=join_s)
    return neg_geo


def create_negative_svg(svg_geo, box_bounds, r_box, box_geo, c_hull, margin, color, opacity, svg_units):
    neg_geo = negative_background_geometry(box_geo, r_box, c_hull, margin)
    points_container = ' '.join('%s, %s' % (x, y) for x, y in neg_geo.exterior.coords[:])
    background = '<polygon points="%s" id="neg_rect" style="fill:%s;opacity:1.0;stroke-width:0.0"/>\n' % \
                 (points_container, color)
    return create_svg_document(svg_geo, box_bounds, margin, get_complementary(color), opacity,
                               svg_units, background=background)


def create_positive_svg(svg_geo, box_bounds, margin, color, opacity, svg_units):
    return create_svg_document(svg_geo, box_bounds, margin, str(color), opacity, svg_units)


def punch_geometry(solid_geometry, tools, punch_size=None, holes=None):
    """Return punched copper; None means pad-center punching found no intersecting holes."""
    film_geo = unary_union(flatten_shapely_geometry(solid_geometry))
    if holes is not None:
        return film_geo.difference(unary_union(flatten_shapely_geometry(holes)))
    punching_geo = []
    for aperture in tools.values():
        aperture_type = aperture.get('type', '')
        if aperture_type == 'REG':
            continue
        # only the flashed pads are punched, so an aperture used only for tracks is not checked
        flashes = [elem for elem in aperture.get('geometry', []) if isinstance(elem.get('follow'), Point)]
        if not flashes:
            continue
        if aperture_type == 'C':
            too_big = punch_size >= float(aperture.get('size', 0.0))
        elif 'width' in aperture and 'height' in aperture:
            too_big = punch_size >= float(aperture['width']) or punch_size >= float(aperture['height'])
        else:
            # a macro (AM) or polygon (P) aperture has no width / height: the hole has to fit in each flashed pad
            too_big = any(punch_size >= 2 * elem['solid'].boundary.distance(elem['follow']) for elem in flashes
                          if elem.get('solid') is not None and elem['solid'].contains(elem['follow']))
        if too_big:
            raise PunchTooBigError('Punch hole size is bigger than an aperture')
        for elem in flashes:
            punching_geo.append(elem['follow'].buffer(punch_size / 2))
    punching_geo = unary_union(punching_geo)
    if punching_geo.is_empty or not film_geo.intersects(punching_geo):
        return None
    return film_geo.difference(punching_geo)


svg_size_re = re.compile(r'<svg\b[^>]*?\swidth="([\d.eE+-]+)(mm|in|cm|pt|px)?"'
                         r'[^>]*?\sheight="([\d.eE+-]+)(mm|in|cm|pt|px)?"')
svg_unit_inches = {'mm': 1 / 25.4, 'cm': 1 / 2.54, 'in': 1.0, 'pt': 1 / 72, 'px': 1 / 96, None: 1 / 96}


PNG_WORKING_MEMORY_LIMIT = 512 * 1024 * 1024


def png_dimensions(svg_text, dpi):
    """Preflight before allocation: 32 bytes/pixel for render, encode, retained bytes,
    preview decode/pixmap and transient copies, plus 32 MiB and twice the SVG storage.
    The conservative working-memory estimate is limited to 512 MiB and each side to 32767 pixels.
    """
    match = svg_size_re.search(svg_text)
    if match is None or not math.isfinite(dpi) or dpi <= 0:
        raise FilmError('png_invalid', 'Invalid SVG dimensions or PNG DPI.')
    width_in = float(match.group(1)) * svg_unit_inches[match.group(2)]
    height_in = float(match.group(3)) * svg_unit_inches[match.group(4)]
    if not all(math.isfinite(value) and value > 0 for value in (width_in, height_in)):
        raise FilmError('png_invalid', 'Invalid SVG dimensions.')
    width_px = max(1, int(width_in * dpi + 0.5))
    height_px = max(1, int(height_in * dpi + 0.5))
    estimate = width_px * height_px * 32 + 32 * 1024 * 1024 + 2 * len(svg_text.encode('utf-8'))
    if max(width_px, height_px) > 32767 or estimate > PNG_WORKING_MEMORY_LIMIT:
        raise FilmError('png_too_large', 'PNG exceeds the 512 MiB working-memory limit. Reduce DPI or film dimensions.')
    return width_px, height_px


def render_svg_png(svg_text, filename, dpi=72):
    """Render a finished, DPI-tagged PNG; filename=None retains the encoded bytes."""
    from PyQt6 import QtCore, QtGui, QtSvg

    width_px, height_px = png_dimensions(svg_text, dpi)

    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg_text.encode('utf-8')))
    if not renderer.isValid():
        raise FilmError('render_failed', 'The SVG cannot be rendered.')
    image = QtGui.QImage(width_px, height_px, QtGui.QImage.Format.Format_RGB32)
    if image.isNull():
        raise FilmError('png_too_large', 'Cannot allocate PNG image.')
    image.fill(QtGui.QColor('white'))
    painter = QtGui.QPainter(image)
    painter.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    if not painter.isActive():
        raise FilmError('render_failed', 'Cannot start PNG renderer.')
    try:
        renderer.render(painter, QtCore.QRectF(0, 0, width_px, height_px))
    finally:
        painter.end()
    dots_per_meter = int(round(dpi / 0.0254))
    image.setDotsPerMeterX(dots_per_meter)
    image.setDotsPerMeterY(dots_per_meter)
    buffer = QtCore.QBuffer()
    if not buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, 'PNG'):
        raise OSError('Cannot encode PNG.')
    data = bytes(buffer.data())
    buffer.close()
    if filename is not None:
        with open(filename, 'wb') as output:
            output.write(data)
    return data


def render_film(svg_text, file_type, page_size, units, box_bounds, dpi=72):
    """Render and validate the complete artifact without a destination or mutable UI."""
    if file_type == 'svg':
        return svg_text
    if file_type == 'png':
        return render_svg_png(svg_text, None, dpi)
    if file_type != 'pdf':
        raise FilmError('render_failed', 'Unsupported film format.')
    output = BytesIO()
    drawing = svg2rlg(StringIO(svg_text))
    if drawing is None:
        raise FilmError('render_failed', 'Cannot render SVG document.')
    if page_size is None:
        renderPDF.drawToFile(drawing, output)
    else:
        unit = inch if units.upper() == 'IN' else mm
        pdf_canvas = canvas.Canvas(output, pagesize=page_size)
        pdf_canvas.translate(box_bounds[0] * unit, box_bounds[1] * unit)
        renderPDF.draw(drawing, pdf_canvas, 0, 0)
        pdf_canvas.save()
    data = output.getvalue()
    import pikepdf
    with pikepdf.Pdf.open(BytesIO(data)) as document:
        if not document.pages:
            raise FilmError('render_failed', 'Generated PDF has no pages.')
    return data


def svg_stroke_geometry(geometry, width):
    """SVG's default butt caps and miter joins; over-limit joins bevel, not GEOS miter-clip."""
    radius = width / 2
    faces = [geometry.buffer(radius, cap_style='flat', join_style='bevel')]
    for line in flatten_shapely_geometry(geometry):
        points = list(line.coords)
        if len(points) > 2 and points[0] == points[-1]:
            points = [points[-2]] + points
        for first, vertex, last in zip(points, points[1:], points[2:]):
            ax, ay = vertex[0] - first[0], vertex[1] - first[1]
            bx, by = last[0] - vertex[0], last[1] - vertex[1]
            alen, blen = math.hypot(ax, ay), math.hypot(bx, by)
            if not alen or not blen:
                continue
            ax, ay, bx, by = ax / alen, ay / alen, bx / blen, by / blen
            cross, dot = ax * by - ay * bx, ax * bx + ay * by
            if not cross or 1 + dot < 2 / 4 ** 2:
                continue
            side = 1 if cross > 0 else -1
            nx, ny, mx, my = side * ay, -side * ax, side * by, -side * bx
            x, y = vertex[:2]
            faces.append(Polygon([(x + radius * nx, y + radius * ny),
                                  (x + radius * (nx + mx) / (1 + dot),
                                   y + radius * (ny + my) / (1 + dot)),
                                  (x + radius * mx, y + radius * my)]))
    return unary_union(faces)


def film_paint_geometry(source, stroke, circle_steps=32):
    """Bake Shapely SVG fill/stroke into world-unit faces, not screen-pixel outlines."""
    painted = []
    quad_segs = circle_quad_segs(circle_steps)
    for geometry in flatten_shapely_geometry(source):
        if geometry.is_empty:
            continue
        element = ElementTree.fromstring(create_svg_geometry(geometry, stroke))
        width = float(element.get('stroke-width', '0'))
        if isinstance(geometry, Point):
            # SVG circles are tessellated just as other curved Geometry faces are.
            face = geometry.buffer(float(element.get('r')) + width / 2, quad_segs=quad_segs)
        elif isinstance(geometry, Polygon):
            face = geometry.union(svg_stroke_geometry(geometry.boundary, width))
        else:
            face = svg_stroke_geometry(geometry, width)
        painted.append(face)
    return unary_union(painted)


def generate_film(snapshot):
    """Generate from immutable geometry and copied settings captured on the GUI thread."""
    source = snapshot['geometry']
    if snapshot.get('punch'):
        source = punch_geometry(source, snapshot['tools'], punch_size=snapshot.get('punch_size'),
                                holes=snapshot.get('holes'))
        if source is None or source.is_empty:
            raise FilmError('punch_unchanged', 'Punching produced no changed film geometry.')
    box_geo, source = transform_geometry([snapshot['box'], source], **snapshot['transform'])
    if box_geo.is_empty or source.is_empty:
        raise FilmError('empty', 'Film or box geometry is empty.')
    margin, color = snapshot['margin'], snapshot['color']
    svg_geo = create_svg_geometry(source, snapshot['stroke'])
    bounds = source.bounds
    if snapshot['negative']:
        svg = create_negative_svg(svg_geo, box_geo.bounds, snapshot['rounded'], box_geo,
                                  snapshot['convex'], margin, color, 1.0, snapshot['units'].lower())
        background = negative_background_geometry(box_geo, snapshot['rounded'], snapshot['convex'], margin)
        bounds = unary_union([source, background]).bounds
    else:
        svg = create_positive_svg(svg_geo, box_geo.bounds, margin, color, 1.0, snapshot['units'].lower())
    page_size = snapshot['page_size']
    if snapshot['format'] == 'pdf':
        xmin, ymin, xmax, ymax = bounds
        if page_size is None:
            page_xmax = box_geo.bounds[2] - box_geo.bounds[0] + 2 * margin
            page_ymax = box_geo.bounds[3] - box_geo.bounds[1] + 2 * margin
            xmin, xmax = xmin - box_geo.bounds[0] + margin, xmax - box_geo.bounds[0] + margin
            ymin, ymax = ymin - box_geo.bounds[1] + margin, ymax - box_geo.bounds[1] + margin
        else:
            unit = inch if snapshot['units'].upper() == 'IN' else mm
            page_xmax, page_ymax = page_size[0] / unit, page_size[1] / unit
            xmin, xmax, ymin, ymax = xmin + margin, xmax + margin, ymin + margin, ymax + margin
        if xmax < 0 or ymax < 0 or xmin > page_xmax or ymin > page_ymax:
            raise FilmError('outside_page', 'The artwork must intersect the selected page or box plus border.')
    data = render_film(svg, snapshot['format'], page_size, snapshot['units'], box_geo.bounds, snapshot['dpi'])
    artifact = {'data': data, 'svg': svg, 'format': snapshot['format'], 'dpi': snapshot['dpi'],
                'name': snapshot['name'] + '_film.' + snapshot['format']}
    if snapshot['format'] == 'svg':
        artwork = film_paint_geometry(source, snapshot['stroke'], snapshot.get('circle_steps', 32))
        paint_color = get_complementary(color) if snapshot['negative'] else color
        geometry = [artwork]
        layers = [{'color': paint_color}]
        if snapshot['negative']:
            # Opaque SVG overpainting: retain complementary artwork OUTSIDE the box too.
            # Disjoint faces avoid relying on coplanar VisPy mesh draw order.
            geometry.insert(0, background.difference(artwork))
            layers.insert(0, {'color': color})
        # the project keeps only the colors; the SVG text stays in artifact['data'] for Save
        artifact.update(geometry=geometry, units=snapshot['units'], film={'layers': layers})
    return artifact


def write_output_file(content2save, filename, file_type, page_size, units, box_bounds, dpi=72):
    """Compatibility entry point for object-image exports; Film saves its retained artifact instead."""
    data = render_film(content2save, file_type, page_size, units, box_bounds, dpi)
    if isinstance(data, str):
        data = data.encode('utf-8')
    with open(filename, 'wb') as output:
        output.write(data)
