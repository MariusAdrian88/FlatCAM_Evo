"""Film geometry, SVG documents and file rendering from explicit data."""

import math
import re
from io import StringIO

from shapely import LineString, Point, Polygon, LinearRing
from shapely.affinity import scale, skew
from shapely.ops import unary_union
from camlib import flatten_shapely_geometry
from reportlab.graphics import renderPDF, renderPM
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch, mm
from reportlab.lib.pagesizes import landscape, portrait
from svglib.svglib import svg2rlg


svg_style_re = re.compile(r' (fill|stroke)="#[0-9a-fA-F]+"| opacity="[^"]*"')


class PunchTooBigError(Exception):
    """The punch hole is not smaller than an aperture of the film object."""


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
    if isinstance(box_geo, (LineString, LinearRing)):
        neg_geo = Polygon(box_geo)
    elif isinstance(box_geo, Polygon):
        neg_geo = Polygon(box_geo.exterior)
    elif c_hull:
        neg_geo = box_geo.convex_hull
    else:
        neg_geo = box_geo.envelope
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


def write_output_file(content2save, filename, file_type, page_size, units, box_bounds, dpi=72):
    """Render to disk. The controller owns validation, errors and notifications."""
    if file_type == 'svg':
        with open(filename, 'w') as fp:
            fp.write(content2save)
    elif file_type == 'png':
        drawing = svg2rlg(StringIO(content2save))
        renderPM.drawToFile(drawing, fn=filename, fmt='PNG', dpi=dpi)
    else:
        unit = inch if units == 'IN' else mm
        drawing = svg2rlg(StringIO(content2save))
        if page_size is None:
            renderPDF.drawToFile(drawing, filename)
        else:
            my_canvas = canvas.Canvas(filename, pagesize=page_size)
            my_canvas.translate(box_bounds[0] * unit, box_bounds[1] * unit)
            renderPDF.draw(drawing, my_canvas, 0, 0)
            my_canvas.save()
