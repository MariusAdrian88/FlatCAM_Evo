
# ###########################################################################################
# THE UNUSED LIBS MAY BE USED FURTHER AWAY BY IMPORTING FROM THIS FILE - DON'T REMOVE THEM
# ###########################################################################################

from PyQt6.QtCore import Qt
from shapely import MultiLineString, Polygon

import numpy as np

import logging

from camlib import gerber_circle_steps, gerber_quad_segs, circle_quad_segs

log = logging.getLogger('base')


def gerber_steps(app, radius):
    """The segments of a full circle of this radius (in app units), as the Gerber circle preferences set them."""
    return gerber_circle_steps(app.options, radius, app.app_units)


def gerber_quad(app, radius):
    """The Shapely quad_segs of a circle of this radius (in app units), as the Gerber circle preferences set them."""
    return gerber_quad_segs(app.options, radius, app.app_units)


def stored_quad_candidates(editor, radius):
    """
    The quad_segs that a stored Gerber circle of this radius can have, to recognise it: the ones of the preference
    now (the first), of the other mode of the preference and of the object (the file may have been loaded with
    other preferences).
    """
    app = editor.app
    units = app.app_units
    options = app.options
    found = [gerber_quad_segs(options, radius, units)]
    for mode in ('fixed', 'adaptive'):
        found.append(gerber_quad_segs(dict(options, gerber_circle_steps_mode=mode), radius, units))
    object_steps = getattr(getattr(editor, 'gerber_obj', None), 'steps_per_circle', None)
    if isinstance(object_steps, int) and not isinstance(object_steps, bool) and object_steps > 0:
        found.append(circle_quad_segs(object_steps))
    return list(dict.fromkeys(found))


def numeric_aperture_codes(keys):
    """
    The aperture keys that are integer codes (or digit strings), as ints. Other keys are skipped.
    """
    codes = []
    for key in keys:
        if isinstance(key, int) and not isinstance(key, bool):
            codes.append(key)
        elif isinstance(key, str) and key.isascii() and key.isdigit():
            codes.append(int(key))
    return codes


class DrawToolShape(object):
    """
    Encapsulates "shapes" under a common class.
    """

    tolerance = None

    @staticmethod
    def get_pts(o):
        """
        Returns a list of all points in the object, where
        the object can be a Polygon, Not a polygon, or a list
        of such. Search is done recursively.

        :param o: geometric object
        :return: List of points
        :rtype: list
        """
        pts = []

        # ## Iterable: descend into each item.
        try:
            for sub_o in o:
                pts += DrawToolShape.get_pts(sub_o)
        # Non-iterable
        except TypeError:
            if o is None:
                return

            # DrawToolShape: descend into .geo.
            if isinstance(o, DrawToolShape):
                pts += DrawToolShape.get_pts(o.geo)
            # ## Descend into .exerior and .interiors
            elif type(o) == Polygon:
                pts += DrawToolShape.get_pts(o.exterior)
                for i in o.interiors:
                    pts += DrawToolShape.get_pts(i)
            elif type(o) == MultiLineString:
                for line in o:
                    pts += DrawToolShape.get_pts(line)
            # ## Has .coords: list them.
            else:
                if DrawToolShape.tolerance is not None:
                    pts += list(o.simplify(DrawToolShape.tolerance).coords)
                else:
                    pts += list(o.coords)
        return pts

    def __init__(self, geo=None):

        # Shapely type or list of such
        self.geo = geo
        self.utility = False


class DrawToolUtilityShape(DrawToolShape):
    """
    Utility shapes are temporary geometry in the editor
    to assist in the creation of shapes. For example it
    will show the outline of a rectangle from the first
    point to the current mouse pointer before the second
    point is clicked and the final geometry is created.
    """

    def __init__(self, geo=None):
        super(DrawToolUtilityShape, self).__init__(geo=geo)
        self.utility = True


class DrawTool(object):
    """
    Abstract Class representing a tool in the drawing
    program. Can generate geometry, including temporary
    utility geometry that is updated on user clicks
    and mouse motion.
    """

    def __init__(self, draw_app):
        self.draw_app = draw_app
        self.complete = False
        self.points = []
        self.geometry = None  # DrawToolShape or None

    def click(self, point):
        """
        :param point: [x, y] Coordinate pair.
        """
        return ""

    def click_release(self, point):
        """
        :param point: [x, y] Coordinate pair.
        """
        return ""

    def on_key(self, key):
        # Jump to coords
        if key == Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

    def utility_geometry(self, data=None):
        return None

    @staticmethod
    def bounds(obj):
        def bounds_rec(o):
            if type(o) is list:
                minx = np.inf
                miny = np.inf
                maxx = -np.inf
                maxy = -np.inf

                for k in o:
                    try:
                        minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                    except Exception as e:
                        log.error("camlib.Gerber.bounds() --> %s" % str(e))
                        return

                    minx = min(minx, minx_)
                    miny = min(miny, miny_)
                    maxx = max(maxx, maxx_)
                    maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's a Shapely object, return it's bounds
                # a clear object (Native load) has only its 'clear' geometry
                geo = o.geo.get('solid')
                if geo is None or geo.is_empty:
                    geo = o.geo.get('clear', geo)
                if geo is not None:
                    return geo.bounds

        return bounds_rec(obj)


class ShapeToolEditorGrb(DrawTool):
    """
    Abstract class for tools that create a shape.
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = None

    def make(self):
        pass
