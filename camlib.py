# ########################################################## ##
# FlatCAM: 2D Post-processing for Manufacturing               #
# http://flatcam.org                                          #
# Author: Juan Pablo Caram (c)                                #
# Date: 2/5/2014                                              #
# MIT Licence                                                 #
# ########################################################## ##
import shapely
from PyQt6 import QtWidgets     # noqa
from PyQt6.QtCore import QThread

from appCommon.Common import GracefulException as grace
from appCommon.SafeEval import safe_eval
from appCommon.PathOrder import order_paths, order_points
from appCommon import RouteSolver

# from scipy.spatial import KDTree, Delaunay
# from scipy.spatial import Delaunay

from appParsers.SVG import svg_to_shapes, SvgImportError
from appParsers.DXF import dxf_to_shapes

from numpy.linalg import solve

import os
import platform
import traceback
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from copy import deepcopy
from collections.abc import Iterable
from copy import copy

from rtree import index as rtindex
from io import StringIO
import ezdxf
from ezdxf import recover

import math
from time import monotonic

# See: http://toblerity.org/shapely/manual.html
from shapely import (
    Polygon,
    Point,
    LinearRing,
    MultiPoint,
    MultiLineString,
    MultiPolygon,
    LineString,
    GeometryCollection,
)

from shapely import box as shply_box
from shapely.ops import unary_union, substring, linemerge
import shapely.affinity as affinity
from shapely.wkt import loads as sloads
from shapely.wkt import dumps as sdumps
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry, GeometrySequence
from shapely import union, difference

from typing import Any

import logging
import re
import numpy as np

import gettext
import appTranslation as fcTranslate
import builtins


def _ortools_available(arch):
    """
    OR-Tools is used only on a 64-bit Python and only if it can be imported.

    :param arch: the first item of platform.architecture(), e.g. '64bit'
    :return: True if the OR-Tools modules were imported (they are then module globals), False otherwise
    """
    if arch != '64bit':
        return False
    global pywrapcp, routing_enums_pb2
    try:
        from ortools.constraint_solver import pywrapcp      # noqa
        from ortools.constraint_solver import routing_enums_pb2     # noqa
    except ImportError:
        return False
    return True


HAS_ORTOOLS = _ortools_available(platform.architecture()[0])

fcTranslate.apply_language('strings')

log = logging.getLogger('base2')
log.setLevel(logging.DEBUG)

formatter = logging.Formatter('[%(levelname)s] %(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
log.addHandler(handler)

if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ParseError(Exception):
    pass


class ApertureMacro:
    """
    Syntax of aperture macros.

    <AM command>:           AM<Aperture macro name>*<Macro content>
    <Macro content>:        {{<Variable definition>*}{<Primitive>*}}
    <Variable definition>:  $K=<Arithmetic expression>
    <Primitive>:            <Primitive code>,<Modifier>{,<Modifier>}|<Comment>
    <Modifier>:             $M|< Arithmetic expression>
    <Comment>:              0 <Text>
    """

    # ## Regular expressions
    am1_re = re.compile(r'^%AM([^*]+)\*(.+)?(%)?$')
    am2_re = re.compile(r'(.*)%$')
    am_comm_re = re.compile(r'^0(.*)')
    am_prim_re = re.compile(r'^[1-9].*')
    am_var_re = re.compile(r'^\$([0-9a-zA-Z]+)=(.*)')

    def __init__(self, name=None):
        self.name = name
        self.raw = ""

        # ## These below are recomputed for every aperture
        # ## definition, in other words, are temporary variables.
        self.primitives = []
        self.loc_vars = {}
        self.geometry = None

    def to_dict(self):
        """
        Returns the object in a serializable form. Only the name and
        raw are required.

        :return: Dictionary representing the object. JSON ready.
        :rtype: dict
        """

        return {
            'name': self.name,
            'raw': self.raw
        }

    def from_dict(self, d):
        """
        Populates the object from a serial representation created
        with ``self.to_dict()``.

        :param d: Serial representation of an ApertureMacro object.
        :return: None
        """
        for attr in ['name', 'raw']:
            setattr(self, attr, d[attr])

    def parse_content(self):
        """
        Creates numerical lists for all primitives in the aperture
        macro (in ``self.raw``) by replacing all variables by their
        values iteratively and evaluating expressions. Results
        are stored in ``self.primitives``.

        :return: None
        """
        # Cleanup
        self.raw = self.raw.replace('\n', '').replace('\r', '').strip(" *")
        self.primitives = []

        # Separate parts
        parts = self.raw.split('*')

        # ### Every part in the macro ####
        for part in parts:
            # ## Comments. Ignored.
            match = ApertureMacro.am_comm_re.search(part)
            if match:
                continue

            # Blanks are not significant; 'x' (or 'X') is the multiplication
            part = re.sub(r'\s+', '', part)
            part = re.sub(r'[xX]', "*", part)

            # ## Variables
            # These are variables defined locally inside the macro. They can be
            # numerical constant or defined in terms of previously define
            # variables, which can be defined locally or in an aperture
            # definition. All replacements occur here.
            match = ApertureMacro.am_var_re.search(part)
            if match:
                # Eval() and store.
                self.loc_vars[match.group(1)] = safe_eval(self.replace_variables(match.group(2)))
                continue

            # ## Primitives
            # Each is an array. The first identifies the primitive, while the
            # rest depend on the primitive. All are strings representing a
            # number and may contain variable definition. The values of these
            # variables are defined in an aperture definition.
            match = ApertureMacro.am_prim_re.search(part)
            if match:
                part = self.replace_variables(part)
                self.primitives.append([safe_eval(x) for x in part.split(",")])

                continue

            if part:
                log.warning("Unknown syntax of aperture macro part: %s" % str(part))

    def replace_variables(self, text):
        """
        Replaces every $n in the text by its value (in brackets, so a negative value or a division is safe).
        A variable that was not defined is 0.

        :param text:    Expression or list of expressions of a macro.
        :return:        The text without variables.
        """
        return re.sub(r'\$([0-9a-zA-Z]+)', lambda m: '(%r)' % (self.loc_vars.get(m.group(1), 0),), text)

    def append(self, data):
        """
        Appends a string to the raw macro.

        :param data: Part of the macro.
        :type data: str
        :return: None
        """
        self.raw += data

    @staticmethod
    def default2zero(n, mods):
        """
        Pads the ``mods`` list with zeros resulting in a
        list of length n.

        :param n:       Length of the resulting list.
        :type n:        int
        :param mods:    List to be padded.
        :type mods:     list
        :return:        Zero-padded list.
        :rtype:         list
        """

        x = [0.0] * n
        na = len(mods)
        x[0:na] = mods
        return x

    @staticmethod
    def make_circle(mods, quad_segs=16):
        """

        :param mods: (Exposure 0/1, Diameter >=0, X-coord, Y-coord, rotation angle around origin in degrees)
        :param quad_segs: Segments per quarter of a circle
        :return:
        """
        val = ApertureMacro.default2zero(5, mods)
        pol = val[0]
        dia = val[1]
        x = val[2]
        y = val[3]
        angle = val[4]
        circle = affinity.rotate(Point(x, y), angle, origin=(0, 0)).buffer(dia / 2, quad_segs=quad_segs)
        return {"pol": int(pol), "geometry": circle}

    @staticmethod
    def make_vector_line(mods):
        """

        :param mods: (Exposure 0/1, Line width >= 0, X-start, Y-start, X-end, Y-end,
            rotation angle around origin in degrees)
        :return:
        """
        val = ApertureMacro.default2zero(7, mods)
        pol = val[0]
        width = val[1]
        xs = val[2]
        ys = val[3]
        xe = val[4]
        ye = val[5]
        angle = val[6]
        # pol, width, xs, ys, xe, ye, angle = ApertureMacro.default2zero(7, mods)

        line = LineString([(xs, ys), (xe, ye)])
        box = line.buffer(width / 2, cap_style="flat")
        box_rotated = affinity.rotate(box, angle, origin=(0, 0))

        return {"pol": int(pol), "geometry": box_rotated}

    @staticmethod
    def make_center_line(mods):
        """

        :param mods: (Exposure 0/1, width >=0, height >=0, x-center, y-center,
            rotation angle around origin in degrees)
        :return:
        """

        val = ApertureMacro.default2zero(6, mods)
        pol = val[0]
        width = val[1]
        height = val[2]
        x = val[3]
        y = val[4]
        angle = val[5]

        box = shply_box(x - width / 2, y - height / 2, x + width / 2, y + height / 2)
        box_rotated = affinity.rotate(box, angle, origin=(0, 0))

        return {"pol": int(pol), "geometry": box_rotated}

    @staticmethod
    def make_lower_left_line(mods):
        """

        :param mods: (exposure 0/1, width >=0, height >=0, x-lowerleft, y-lowerleft,
            rotation angle around origin in degrees)
        :return:
        """

        # pol, width, height, x, y, angle = ApertureMacro.default2zero(6, mods)
        val = ApertureMacro.default2zero(6, mods)
        pol = val[0]
        width = val[1]
        height = val[2]
        x = val[3]
        y = val[4]
        angle = val[5]

        box = shply_box(x, y, x + width, y + height)
        box_rotated = affinity.rotate(box, angle, origin=(0, 0))

        return {"pol": int(pol), "geometry": box_rotated}

    @staticmethod
    def make_outline(mods):
        """

        :param mods:
        :return:
        """

        pol = mods[0]

        # ---------------------------
        # added to fix the issue on Allegro 17.2 Gerber's which have fewer points than declared
        # discard first 2 values (exposure and vertex points number); the rotation is the last value when the
        # modifiers have an odd count (it is optional: the pairs of coordinates alone make an even count)
        if len(mods) % 2 == 0:
            vertex_list = mods[2:]
            angle = 0.0
        else:
            vertex_list = mods[2:-1]
            angle = mods[-1]
        # vertex points number is second value
        vtx_nr = mods[1]
        n = int(len(vertex_list) / 2)
        points = [(0, 0)] * n

        for i in range(n):
            start = 2 * i
            stop = (2 * i) + 2
            points[i] = vertex_list[start:stop]

        # Fix for KiCAD 7.0.7 who is too lazy to respect the Gerber specification which says
        # that the last point should always be the first
        if len(points) < vtx_nr:
            points.append(points[0])
        # ---------------------------

        poly = Polygon(points)
        poly_rotated = affinity.rotate(poly, angle, origin=(0, 0))

        return {"pol": int(pol), "geometry": poly_rotated}

    @staticmethod
    def make_polygon(mods):
        """
        Note: Specs indicate that rotation is only allowed if the center
        (x, y) == (0, 0). I will tolerate breaking this rule.

        :param mods: (exposure 0/1, n_verts 3<=n<=12, x-center, y-center,
            diameter of circumscribed circle >=0, rotation angle around origin)
        :return:
        """

        # pol, nverts, x, y, dia, angle = ApertureMacro.default2zero(6, mods)
        val = ApertureMacro.default2zero(6, mods)
        pol = val[0]
        nverts = val[1]
        x = val[2]
        y = val[3]
        dia = val[4]
        angle = val[5]

        # the number of vertices can be a float (it comes from a variable)
        nverts = int(round(nverts))
        points = [(0, 0)] * nverts

        for i in range(nverts):
            points[i] = (x + 0.5 * dia * np.cos(2 * np.pi * i / nverts),
                         y + 0.5 * dia * np.sin(2 * np.pi * i / nverts))

        poly = Polygon(points)
        poly_rotated = affinity.rotate(poly, angle, origin=(0, 0))

        return {"pol": int(pol), "geometry": poly_rotated}

    @staticmethod
    def make_moire(mods, quad_segs=16):
        """
        Note: Specs indicate that rotation is only allowed if the center
        (x, y) == (0, 0). I will tolerate breaking this rule.

        :param mods: (x-center, y-center, outer_dia_outer_ring, ring thickness,
            gap, max_rings, crosshair_thickness, crosshair_len (total length of each line), rotation
            angle around origin in degrees)
        :param quad_segs: Segments per quarter of a circle
        :return:
        """

        # x, y, dia, thickness, gap, nrings, cross_th, cross_len, angle = ApertureMacro.default2zero(9, mods)
        val = ApertureMacro.default2zero(9, mods)
        x = val[0]
        y = val[1]
        dia = val[2]
        thickness = val[3]
        gap = val[4]
        nrings = val[5]
        cross_th = val[6]
        cross_len = val[7]
        angle = val[8]

        r = dia / 2 - thickness / 2
        if r <= 0:
            # the ring is thicker than the circle: a disk
            result = Point((x, y)).buffer(dia / 2.0, quad_segs=quad_segs)
        else:
            result = Point((x, y)).buffer(r, quad_segs=quad_segs).exterior.buffer(thickness / 2.0, quad_segs=quad_segs)
            ring = result

            i = 1  # Number of rings created so far

            # ## If the ring does not have an interior it means that it is
            # ## a disk. Then stop.
            while len(ring.interiors) > 0 and i < nrings:
                r -= thickness + gap
                if r <= 0:
                    break
                ring = Point((x, y)).buffer(r, quad_segs=quad_segs).exterior.buffer(thickness / 2.0,
                                                                                    quad_segs=quad_segs)
                result = unary_union([result, ring])
                i += 1

        # ## Crosshair
        half = cross_len / 2.0
        hor = LineString([(x - half, y), (x + half, y)]).buffer(cross_th / 2.0, cap_style="flat")
        ver = LineString([(x, y - half), (x, y + half)]).buffer(cross_th / 2.0, cap_style="flat")
        result = affinity.rotate(unary_union([result, hor, ver]), angle, origin=(0, 0))

        return {"pol": 1, "geometry": result}

    @staticmethod
    def make_thermal(mods, quad_segs=16):
        """
        Note: Specs indicate that rotation is only allowed if the center
        (x, y) == (0, 0). I will tolerate breaking this rule.

        :param mods: [x-center, y-center, diameter-outside, diameter-inside,
            gap-thickness, rotation angle around origin]
        :param quad_segs: Segments per quarter of a circle
        :return:
        """

        # x, y, dout, din, t, angle = ApertureMacro.default2zero(6, mods)
        val = ApertureMacro.default2zero(6, mods)
        x = val[0]
        y = val[1]
        dout = val[2]
        din = val[3]
        t = val[4]
        angle = val[5]

        ring = Point((x, y)).buffer(dout / 2.0, quad_segs=quad_segs).difference(
            Point((x, y)).buffer(din / 2.0, quad_segs=quad_segs))
        hline = LineString([(x - dout / 2.0, y), (x + dout / 2.0, y)]).buffer(t / 2.0, cap_style="square")
        vline = LineString([(x, y - dout / 2.0), (x, y + dout / 2.0)]).buffer(t / 2.0, cap_style="square")
        thermal = affinity.rotate(ring.difference(hline.union(vline)), angle, origin=(0, 0))

        return {"pol": 1, "geometry": thermal}

    @staticmethod
    def polygonal(geo):
        """
        The polygons of a geometry: a union or difference with a primitive that has no area can be a
        GeometryCollection that holds a line or a point as well.

        :param geo: Shapely geometry
        :return: Polygon or MultiPolygon (empty Polygon if there is no area)
        """
        if not isinstance(geo, GeometryCollection) or isinstance(geo, (Polygon, MultiPolygon)):
            return geo
        polygons = [g for g in shapely.get_parts(geo) if isinstance(g, (Polygon, MultiPolygon))]
        return unary_union(polygons) if polygons else Polygon()

    def make_geometry(self, modifiers: list, steps_per_circle=None):
        """
        Runs the macro for the given modifiers and generates
        the corresponding geometry.

        :param modifiers: Modifiers (parameters) for this macro
        :type modifiers: list
        :param steps_per_circle: Segments of a whole circle; None for the default
        :type steps_per_circle: int | None
        :return: Shapely geometry
        :rtype: shapely.geometry.polygon
        """

        # ## Primitive makers
        makers = {
            "1": ApertureMacro.make_circle,
            "2": ApertureMacro.make_vector_line,
            "20": ApertureMacro.make_vector_line,
            "21": ApertureMacro.make_center_line,
            "22": ApertureMacro.make_lower_left_line,
            "4": ApertureMacro.make_outline,
            "5": ApertureMacro.make_polygon,
            "6": ApertureMacro.make_moire,
            "7": ApertureMacro.make_thermal
        }

        # ## Store modifiers as local variables
        modifiers = modifiers or []
        modifiers = [float(m) for m in modifiers]
        self.loc_vars = {}
        for i in range(0, len(modifiers)):
            self.loc_vars[str(i + 1)] = modifiers[i]

        # ## Parse
        self.primitives = []  # Cleanup
        self.geometry = Polygon()
        self.parse_content()

        # ## Make the geometry
        circle_args = {} if steps_per_circle is None else {"quad_segs": circle_quad_segs(steps_per_circle)}
        for primitive in self.primitives:
            # Make the primitive
            code = str(int(primitive[0]))
            if code not in makers:
                log.warning("Unknown primitive %s in the aperture macro %s." % (code, self.name))
                continue
            if code in ("1", "6", "7"):
                prim_geo = makers[code](primitive[1:], **circle_args)
            else:
                prim_geo = makers[code](primitive[1:])

            # A primitive without size (a width or height of 0) adds or cuts nothing
            minx, miny, maxx, maxy = prim_geo['geometry'].bounds
            if not (maxx > minx and maxy > miny):
                continue

            # Add it (according to polarity)
            if prim_geo['pol'] == 1:
                if self.geometry.is_empty:
                    self.geometry = self.polygonal(prim_geo['geometry'])
                    continue
                # The union function can produce a GeometryCollection when a primitive has no area (a line width or a
                # size of 0), see https://bitbucket.org/marius_stanciu/flatcam_beta/issues/59. Only the polygons are
                # kept: the other primitives are not moved.
                self.geometry = self.polygonal(union(self.geometry, prim_geo['geometry']))
                continue
            if prim_geo['pol'] == 0:
                self.geometry = self.polygonal(difference(self.geometry, prim_geo['geometry']))
                continue
        return self.geometry


def process_gui_events():
    """Let the GUI redraw during a long loop; does nothing in a worker thread or when there is no QApplication."""
    app_instance = QtWidgets.QApplication.instance()
    if app_instance is not None and QThread.currentThread() == app_instance.thread():
        QtWidgets.QApplication.processEvents()


class Geometry(object):
    """
    Base geometry class.
    """

    defaults = {
        "units": 'mm',
        # "geo_steps_per_circle": 128
    }

    # clear_polygon_lines() cuts the scan lines from bands of this many lines (measured: 8 is the fastest)
    SCAN_BAND_LINES = 8
    # clear_polygon_seed() makes and cuts this many rings at a time (measured: 8 and 16 are the fastest); a margin
    # of at least SEED_BAND_MIN_COORDS coordinates is cut without the parts that the rings cannot touch
    SEED_BAND_RINGS = 16
    SEED_BAND_MIN_COORDS = 2000

    obj_options: dict[str, Any]
    kind: str
    multigeo: bool

    def __init__(self, app, geo_steps_per_circle=None):
        if self.__dict__.get('_Geometry_inited'):
            return

        # Units (in or mm)
        self.app = app
        self.units = self.app.app_units
        self.decimals = self.app.decimals

        self.drawing_tolerance = 0.0
        self.tools: dict[str | int, dict[str, Any] | str] = {}

        # Final geometry: MultiPolygon or list (of geometry constructs)
        self.solid_geometry = None

        # Final geometry: MultiLineString or list (of LineString or Points)
        self.follow_geometry = None

        # Flattened geometry (list of paths only)
        self.flat_geometry = []

        # this is the calculated conversion factor when the file units are different than the ones in the app
        self.file_units_factor = 1

        # Index
        self.index = None

        self.geo_steps_per_circle = geo_steps_per_circle

        # variables to display the percentage of work done
        self.geo_len = 0
        self.old_disp_number = 0
        self.el_count = 0

        self.temp_shapes = self.app.plotcanvas.new_shape_collection(layers=1)

        # Attributes to be included in serialization
        self.ser_attrs = ["units", 'solid_geometry', 'follow_geometry', 'tools']

        self._Geometry_inited = True

    def _app_option(self, key, fallback=None):
        return app_option(self.app, key, fallback)

    def circle_quad_segs_for(self, radius):
        """
        The Shapely buffer quad_segs of a circle of this radius. An object that was given a fixed
        geo_steps_per_circle (a CNC job) keeps it; the other objects follow the preferences "Circles" of their kind.

        :param radius:  the radius of the circle (for a buffer: the buffer distance), in the units of the object
        :return:        the segments of a quarter circle
        """
        if self.geo_steps_per_circle is not None:
            return circle_quad_segs(self.geo_steps_per_circle)
        kind = getattr(self, 'kind', None)
        return object_quad_segs(self.app.options, kind if kind in ('gerber', 'excellon') else 'geometry', radius,
                                self.units)

    def plot_temp_shapes(self, element: BaseGeometry | list[BaseGeometry], color='red'):

        try:
            for sub_el in element:
                self.plot_temp_shapes(sub_el)
        except TypeError:  # Element is not iterable...
            # self.add_shape(shape=element, color=color, visible=visible, layer=0)
            self.temp_shapes.add(
                shape=element,
                tolerance=float(self._app_option("global_tolerance")),
                color=color,
                visible=True,
                layer=0,
            )

    def make_index(self):
        self.flatten()
        self.index = AppRTree()

        for i, g in enumerate(self.flat_geometry):
            self.index.insert(i, g)

    def add_circle(self, origin, radius, tool=None):
        """
        Adds a circle to the object.

        :param origin:  Center of the circle.
        :param radius:  Radius of the circle.
        :param tool:    A tool in the Tools dictionary attribute of the object
        :return: None
        """

        if self.solid_geometry is None:
            self.solid_geometry = []

        new_circle = Point(origin).buffer(radius, self.circle_quad_segs_for(radius))
        if not new_circle.is_valid:
            return "fail"

        # add to the solid_geometry
        try:
            self.solid_geometry.append(new_circle)
        except TypeError:
            try:
                self.solid_geometry = self.solid_geometry.union(new_circle)
            except Exception as e:
                self.app.log.error("Failed to run union on polygons. %s" % str(e))
                return "fail"

        # add in tools solid_geometry
        if tool is None or tool not in self.tools:
            tool = 1
        self.tools[tool]['solid_geometry'].append(new_circle)

        # calculate bounds
        try:
            xmin, ymin, xmax, ymax = self.bounds()

            self.obj_options['xmin'] = xmin
            self.obj_options['ymin'] = ymin
            self.obj_options['xmax'] = xmax
            self.obj_options['ymax'] = ymax
        except Exception as e:
            self.app.log.error("Failed. The object has no bounds properties. %s" % str(e))

    def add_polygon(self, points, tool=None):
        """
        Adds a polygon to the object (by union)

        :param points:  The vertices of the polygon.
        :param tool:    A tool in the Tools dictionary attribute of the object
        :return:        None
        """
        if self.solid_geometry is None:
            self.solid_geometry = []

        new_poly = Polygon(points)
        if not new_poly.is_valid:
            return "fail"

        # add to the solid_geometry
        if type(self.solid_geometry) is list:
            self.solid_geometry.append(new_poly)
        else:
            try:
                self.solid_geometry = self.solid_geometry.union(Polygon(points))
            except Exception as e:
                self.app.log.error("Failed to run union on polygons. %s" % str(e))
                return "fail"

        # add in tools solid_geometry
        if tool is None or tool not in self.tools:
            tool = 1
        self.tools[tool]['solid_geometry'].append(new_poly)

        # calculate bounds
        try:
            xmin, ymin, xmax, ymax = self.bounds()

            self.obj_options['xmin'] = xmin
            self.obj_options['ymin'] = ymin
            self.obj_options['xmax'] = xmax
            self.obj_options['ymax'] = ymax
        except Exception as e:
            self.app.log.error("Failed. The object has no bounds properties. %s" % str(e))

    def add_polyline(self, points, tool=None):
        """
        Adds a polyline to the object (by union)

        :param points:  The vertices of the polyline.
        :param tool:    A tool in the Tools dictionary attribute of the object
        :return:        None
        """
        if self.solid_geometry is None:
            self.solid_geometry = []

        new_line = LineString(points)
        if not new_line.is_valid:
            return "fail"

        # add to the solid_geometry
        if type(self.solid_geometry) is list:
            self.solid_geometry.append(new_line)
        else:
            try:
                self.solid_geometry = self.solid_geometry.union(new_line)
            except Exception as e:
                self.app.log.error("Failed to run union on polylines. %s" % str(e))
                return "fail"

        # add in tools solid_geometry
        if tool is None or tool not in self.tools:
            tool = 1
        self.tools[tool]['solid_geometry'].append(new_line)

        # calculate bounds
        try:
            xmin, ymin, xmax, ymax = self.bounds()

            self.obj_options['xmin'] = xmin
            self.obj_options['ymin'] = ymin
            self.obj_options['xmax'] = xmax
            self.obj_options['ymax'] = ymax
        except Exception as e:
            self.app.log.error("Failed. The object has no bounds properties. %s" % str(e))

    def is_empty(self):
        if isinstance(self.solid_geometry, BaseGeometry) or isinstance(self.solid_geometry, Polygon) or \
                isinstance(self.solid_geometry, MultiPolygon):
            return self.solid_geometry.is_empty

        if isinstance(self.solid_geometry, list):
            return len(self.solid_geometry) == 0

        self.app.inform.emit('[ERROR_NOTCL] %s' % _("self.solid_geometry is neither BaseGeometry or list."))
        return

    def subtract_polygon(self, points):
        """
        Subtract polygon from the given object. This only operates on the paths in the original geometry,
        i.e. it converts polygons into paths.

        :param points: The vertices of the polygon.
        :return: none
        """
        if not isinstance(points, Polygon):
            polygon = Polygon(points)
        else:
            polygon = points
        if not polygon.is_valid or polygon.is_empty:
            return 'fail'

        def subtract(geometry):
            # Preserve path subtraction: polygons become exterior and interior paths, not filled areas.
            paths = self.flatten(geometry=geometry, pathonly=True)
            return unary_union([path.difference(polygon) for path in paths
                                if isinstance(path, (LineString, LinearRing, MultiLineString))])

        if getattr(self, 'multigeo', False) and self.tools:
            updated = {uid: [subtract(tool.get('solid_geometry', []))] for uid, tool in self.tools.items()}
            for uid, geometry in updated.items():
                self.tools[uid]['solid_geometry'] = geometry
            self.solid_geometry = unary_union([g for geometry in updated.values() for g in geometry])
        else:
            self.solid_geometry = subtract(self.solid_geometry if self.solid_geometry is not None else [])
            for tool in self.tools.values():
                tool['solid_geometry'] = [self.solid_geometry]
        if hasattr(self, 'obj_options'):
            self.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), self.bounds()))

    def bounds(self, flatten=False):
        """
        Returns coordinates of rectangular bounds
        of geometry: (xmin, ymin, xmax, ymax).
        :param flatten: will flatten the solid_geometry if True
        :return:
        """
        # fixed issue of getting bounds only for one level lists of objects
        # now it can get bounds for nested lists of objects

        self.app.log.debug("camlib.Geometry.bounds()")

        def bounds_rec(obj):
            if type(obj) is list:
                gminx = np.inf
                gminy = np.inf
                gmaxx = -np.inf
                gmaxy = -np.inf

                for k in obj:
                    if type(k) is dict:
                        for key in k:
                            minx_, miny_, maxx_, maxy_ = bounds_rec(k[key])
                            gminx = min(gminx, minx_)
                            gminy = min(gminy, miny_)
                            gmaxx = max(gmaxx, maxx_)
                            gmaxy = max(gmaxy, maxy_)
                    else:
                        try:
                            if k.is_empty:
                                continue
                        except Exception:
                            pass

                        minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                        gminx = min(gminx, minx_)
                        gminy = min(gminy, miny_)
                        gmaxx = max(gmaxx, maxx_)
                        gmaxy = max(gmaxy, maxy_)
                return gminx, gminy, gmaxx, gmaxy
            else:
                # it's a Shapely object, return its bounds
                return obj.bounds

        _bounds_rec_slow = bounds_rec

        def bounds_rec(obj):
            res = list_total_bounds(obj)
            return _bounds_rec_slow(obj) if res is None else res

        if self.multigeo is True:
            minx_list = []
            miny_list = []
            maxx_list = []
            maxy_list = []

            for tool in self.tools:
                working_geo = self.tools[tool]['solid_geometry']
                if not working_geo:
                    continue

                if flatten:
                    self.flatten(geometry=working_geo, reset=True)
                    working_geo = self.flat_geometry

                minx, miny, maxx, maxy = bounds_rec(working_geo)
                minx_list.append(minx)
                miny_list.append(miny)
                maxx_list.append(maxx)
                maxy_list.append(maxy)

            if not minx_list and not miny_list and not maxx_list and not maxy_list:
                self.app.log.debug("solid_geometry is None")
                return 0, 0, 0, 0

            return min(minx_list), min(miny_list), max(maxx_list), max(maxy_list)
        else:
            if self.solid_geometry is None:
                self.app.log.debug("solid_geometry is None")
                return 0, 0, 0, 0

            if flatten:
                self.flatten(reset=True)
                self.solid_geometry = self.flat_geometry

            bounds_coords = bounds_rec(self.solid_geometry)
            return bounds_coords

        # try:
        #     # from here: http://rightfootin.blogspot.com/2006/09/more-on-python-flatten.html
        #     def flatten(l, ltypes=(list, tuple)):
        #         ltype = type(l)
        #         l = list(l)
        #         i = 0
        #         while i < len(l):
        #             while isinstance(l[i], ltypes):
        #                 if not l[i]:
        #                     l.pop(i)
        #                     i -= 1
        #                     break
        #                 else:
        #                     l[i:i + 1] = l[i]
        #             i += 1
        #         return ltype(l)
        #
        #     log.debug("Geometry->bounds()")
        #     if self.solid_geometry is None:
        #         log.debug("solid_geometry is None")
        #         return 0, 0, 0, 0
        #
        #     if type(self.solid_geometry) is list:
        #         if len(self.solid_geometry) == 0:
        #             log.debug('solid_geometry is empty []')
        #             return 0, 0, 0, 0
        #         return unary_union(flatten(self.solid_geometry)).bounds
        #     else:
        #         return self.solid_geometry.bounds
        # except Exception as e:
        #     self.app.inform.emit("[ERROR_NOTCL] Error cause: %s" % str(e))

        # log.debug("Geometry->bounds()")
        # if self.solid_geometry is None:
        #     log.debug("solid_geometry is None")
        #     return 0, 0, 0, 0
        #
        # if type(self.solid_geometry) is list:
        #     if len(self.solid_geometry) == 0:
        #         log.debug('solid_geometry is empty []')
        #         return 0, 0, 0, 0
        #     return unary_union(self.solid_geometry).bounds
        # else:
        #     return self.solid_geometry.bounds

    def find_polygon(self, point, geoset=None) -> shapely.Polygon | None:
        """
        Find an object that object.contains(Point(point)) in
        poly, which can can be iterable, contain iterable of, or
        be itself an implementer of .contains().

        :param point: See description
        :param geoset: a polygon or list of polygons where to find if the param point is contained
        :return: Polygon containing point or None.
        """

        if geoset is None:
            geoset = self.solid_geometry

        try:  # Iterable
            for sub_geo in geoset:
                p = self.find_polygon(point, geoset=sub_geo)
                if p is not None:
                    return p
        except TypeError:  # Non-iterable
            try:  # Implements .contains()
                if isinstance(geoset, LinearRing):
                    geoset = Polygon(geoset)
                if geoset.contains(Point(point)):
                    return geoset
            except AttributeError:  # Does not implement .contains()
                return None

        return None

    def get_interiors(self, geometry=None):

        interiors = []

        if geometry is None:
            geometry = self.solid_geometry

        w_geo = flatten_shapely_geometry(geometry)
        for geo in w_geo:
            try:
                interiors.extend(geo.interiors)
            except Exception:
                continue

        return interiors

    def get_exteriors(self, geometry=None):
        """
        Returns all exteriors of polygons in geometry. Uses
        ``self.solid_geometry`` if geometry is not provided.

        :param geometry: Shapely type or list, or list of list of such.
        :return: List of paths constituting the exteriors
           of polygons in geometry.
        """

        exteriors = []

        if geometry is None:
            geometry = self.solid_geometry

        w_geo = flatten_shapely_geometry(geometry)
        for geo in w_geo:
            try:
                exteriors.append(geo.exterior)
            except Exception:
                continue

        return exteriors

    def flatten(self, geometry: BaseGeometry | list[BaseGeometry] | None = None, reset=True, pathonly=False):
        """
        Creates a list of non-iterable linear geometry objects.
        Polygons are expanded into its exterior and interiors if specified.

        Results are placed in self.flat_geometry

        :param geometry: Shapely type, or list, or a list of lists of such.
        :param reset: Clears the contents of self.flat_geometry.
        :param pathonly: Expands polygons into linear elements.
        """

        if geometry is None:
            geometry = self.solid_geometry

        if reset:
            self.flat_geometry = []

        # ## If iterable, expand recursively.
        try:
            work_geo = geometry.geoms if isinstance(geometry, (MultiPolygon, MultiLineString)) else geometry
            for geo in work_geo:
                if geo is not None:
                    self.flatten(geometry=geo,
                                 reset=False,
                                 pathonly=pathonly)

        # ## Not iterable, do the actual indexing and add.
        except TypeError:
            if pathonly and isinstance(geometry, Polygon):
                ext_geo = geometry.exterior
                ints_geo = geometry.interiors
                if ext_geo is not None and not ext_geo.is_empty:
                    self.flat_geometry.append(ext_geo)
                self.flatten(geometry=ints_geo, reset=False,  pathonly=True)
            else:
                if geometry is not None and not geometry.is_empty:
                    self.flat_geometry.append(geometry)

        return self.flat_geometry

    def flatten_exterior_interiors(self, geometry=None):
        """
        Creates a list of non-iterable linear geometry objects.
        Polygons are expanded into its exterior and interiors.


        :param geometry: Shapely type, or a list, or a list of lists of such.
        """

        flat_geo_ext = []
        flat_geo_ints = []

        if geometry is None:
            geometry = self.solid_geometry
        # ## If iterable, expand recursively.
        try:
            work_geo = geometry.geoms if isinstance(geometry, (MultiPolygon, MultiLineString)) else geometry
            for geo in work_geo:
                if geo is not None:
                    ext, ints = self.flatten_exterior_interiors(geo)
                    flat_geo_ext += ext
                    flat_geo_ints += ints
        # ## Not iterable, do the actual indexing and add.
        except TypeError:
            if isinstance(geometry, Polygon):
                ext_geo = geometry.exterior
                ints_geo = geometry.interiors
                if ext_geo is not None and not ext_geo.is_empty:
                    flat_geo_ext.append(ext_geo)
                for i_geo in ints_geo:
                    if i_geo is not None and not i_geo.is_empty:
                        flat_geo_ints.append(i_geo)
            elif isinstance(geometry, (LineString, LinearRing)):
                if geometry is not None and not geometry.is_empty:
                    flat_geo_ext.append(geometry)

        return flat_geo_ext, flat_geo_ints

    # def make2Dstorage(self):
    #
    #     self.flatten()
    #
    #     def get_pts(o):
    #         pts = []
    #         if type(o) == Polygon:
    #             g = o.exterior
    #             pts += list(g.coords)
    #             for i in o.interiors:
    #                 pts += list(i.coords)
    #         else:
    #             pts += list(o.coords)
    #         return pts
    #
    #     storage = AppRTreeStorage()
    #     storage.get_points = get_pts
    #     for shape in self.flat_geometry:
    #         storage.insert(shape)
    #     return storage

    # def flatten_to_paths(self, geometry=None, reset=True):
    #     """
    #     Creates a list of non-iterable linear geometry elements and
    #     indexes them in rtree.
    #
    #     :param geometry: Iterable geometry
    #     :param reset: Either to clear (True) or append (False) to self.flat_geometry
    #     :return: self.flat_geometry, self.flat_geometry_rtree
    #     """
    #
    #     if geometry is None:
    #         geometry = self.solid_geometry
    #
    #     if reset:
    #         self.flat_geometry = []
    #
    #     # ## If iterable, expand recursively.
    #     try:
    #         for geo in geometry:
    #             self.flatten_to_paths(geometry=geo, reset=False)
    #
    #     # ## Not iterable, do the actual indexing and add.
    #     except TypeError:
    #         if type(geometry) == Polygon:
    #             g = geometry.exterior
    #             self.flat_geometry.append(g)
    #
    #             # ## Add first and last points of the path to the index.
    #             self.flat_geometry_rtree.insert(len(self.flat_geometry) - 1, g.coords[0])
    #             self.flat_geometry_rtree.insert(len(self.flat_geometry) - 1, g.coords[-1])
    #
    #             for interior in geometry.interiors:
    #                 g = interior
    #                 self.flat_geometry.append(g)
    #                 self.flat_geometry_rtree.insert(len(self.flat_geometry) - 1, g.coords[0])
    #                 self.flat_geometry_rtree.insert(len(self.flat_geometry) - 1, g.coords[-1])
    #         else:
    #             g = geometry
    #             self.flat_geometry.append(g)
    #             self.flat_geometry_rtree.insert(len(self.flat_geometry) - 1, g.coords[0])
    #             self.flat_geometry_rtree.insert(len(self.flat_geometry) - 1, g.coords[-1])
    #
    #     return self.flat_geometry, self.flat_geometry_rtree

    def isolation_buffers(self, offsets, geometry=None):
        """
        Buffers the geometry for several isolation passes ahead, in parallel threads. It is given to
        isolation_geometry() as 'buffers', which takes the buffered geometry of its pass from it.

        :param offsets:     the offset distance of each pass
        :type offsets:      list
        :param geometry:    the geometry to work with, as for isolation_geometry()
        :return:            the buffered geometry of each pass: PassBuffers.get(pass number)
        :rtype:             PassBuffers
        """
        # an empty given geometry gives empty passes, it does not fall back to the whole object
        working_geo = geometry if geometry is not None else self.solid_geometry

        # the steps count the whole circle
        quad_segs = max(1, int(self.geo_steps_per_circle) // 4)
        return PassBuffers(working_geo, offsets, quad_segs, abort=lambda: self.app.abort_flag)

    def isolation_geometry(self, offset, geometry=None, iso_type=2, corner=None, passes=0, prog_plot=False,
                           buffers=None):
        """
        Creates contours around geometry at a given
        offset distance.

        :param offset:      Offset distance.
        :type offset:       float
        :param geometry     The geometry to work with
        :param iso_type:    type of isolation, can be 0 = exteriors or 1 = interiors or 2 = both (complete)
        :param corner:      type of corner for the isolation:
                            0 = round; 1 = square; 2= beveled (line that connects the ends)
        :param passes:      current pass out of possible multiple passes for which the isolation is done
        :param prog_plot:   type of plotting: "normal" or "progressive"
        :param buffers:     the passes buffered ahead by 'offset' (isolation_buffers()), or None
        :return:            The buffered geometry.
        :rtype:             Shapely.MultiPolygon or Shapely.Polygon
        """

        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        if iso_type not in (0, 1, 2):
            self.app.log.debug("Geometry.isolation_geometry() --> Type of isolation not supported")
            return "fail"

        if buffers is not None:
            # this pass is buffered with the next ones, in parallel threads
            self.app.proc_container.update_view_text(' %s %d' % (_("Pass"), int(passes + 1)))
            geo_iso = buffers.get(passes)
        else:
            geo_iso = []

            # an empty given geometry gives an empty result, it does not fall back to the whole object
            working_geo = geometry if geometry is not None else self.solid_geometry

            working_geo_shp = flatten_shapely_geometry(working_geo)
            geo_len = len(working_geo_shp)
            # the steps count the whole circle
            quad_segs = self.circle_quad_segs_for(offset) if offset != 0 else 1

            old_disp_number = 0
            pol_nr = 0
            # yet, it can be done by issuing a unary_union in the end, thus getting rid of the overlapping geo
            for pol in working_geo_shp:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace
                if offset == 0:
                    temp_geo = pol
                else:
                    corner_type = "round" if corner is None else corner
                    temp_geo = pol.buffer(offset, quad_segs, join_style=corner_type)

                geo_iso.append(temp_geo)

                pol_nr += 1

                # activity view update
                disp_number = int(np.interp(pol_nr, [0, geo_len], [0, 100]))
                if old_disp_number < disp_number <= 100:
                    self.app.proc_container.update_view_text(' %s %d: %d%%' %
                                                             (_("Pass"), int(passes + 1), int(disp_number)))
                    old_disp_number = disp_number

            self.app.proc_container.update_view_text(' %s' % _("Buffering"))
            geo_iso = unary_union(geo_iso)

        self.app.proc_container.update_view_text('')
        # end of replaced block

        if iso_type == 2:
            ret_geo = flatten_shapely_geometry(geo_iso)
        elif iso_type == 0:
            self.app.proc_container.update_view_text(' %s' % _("Get Exteriors"))
            ret_geo = self.get_exteriors(geo_iso)
        elif iso_type == 1:
            self.app.proc_container.update_view_text(' %s' % _("Get Interiors"))
            ret_geo = self.get_interiors(geo_iso)

        if prog_plot == 'progressive':
            for elem in ret_geo:
                self.plot_temp_shapes(elem)

        return ret_geo

    def flatten_list(self, obj_list):
        for item in obj_list:
            if isinstance(item, Iterable) and not isinstance(item, (str, bytes)):
                yield from self.flatten_list(item)
            else:
                yield item

    def import_svg(self, filename, object_type=None, flip=True, units=None):
        """
        Imports shapes from an SVG file into the object's geometry.

        :param filename:    Path to the SVG file.
        :type filename:     str
        :param object_type: parameter passed further along
        :param flip:        Flip vertically.
        :type flip:         bool
        :param units:       FlatCAM units
        :return:            None
        """

        self.app.log.debug("camlib.Geometry.import_svg()")

        units = self.app.app_units if units is None else units
        # every circle gets the steps of its own radius (in the output units)
        options = getattr(self.app, 'options', None)
        res = lambda radius: object_circle_steps(options, 'geometry', radius, units)

        # Parse into a flat list of shapely objects (units conversion, flip and text are handled by the importer)
        try:
            geos = svg_to_shapes(filename, object_type, units=units, resolution=res, flip=flip, app=self.app)
        except SvgImportError as e:
            self.app.log.error("camlib.Geometry.import_svg(). %s" % str(e))
            self.app.inform.emit("[ERROR_NOTCL] %s" % _("Failed."))
            return 'fail'

        self.app.log.debug("camlib.Geometry.import_svg(). Finished parsing the SVG geometry.")

        # trying to optimize the resulting geometry by merging contiguous lines
        geos = list(self.flatten_list(geos))
        geos_polys = []
        geos_lines = []
        for g in geos:
            if isinstance(g, Polygon):
                geos_polys.append(g)
            else:
                geos_lines.append(g)

        try:
            merged_lines = linemerge(geos_lines)
        except Exception:
            merged_lines = geos_lines
            self.app.log.error(
                'camlib.Geometry.import_svg(). Could not merge the lines, working with the original SVG geometry.')

        geos = geos_polys
        try:
            w_geo = merged_lines.geoms if isinstance(merged_lines, (MultiPolygon, MultiLineString)) else merged_lines
            for ml in w_geo:
                geos.append(ml)
        except TypeError:
            geos.append(merged_lines)

        # Add to object
        if self.solid_geometry is None:
            self.solid_geometry = []

        if type(self.solid_geometry) is list:
            if type(geos) is list:
                self.solid_geometry += geos
            else:
                self.solid_geometry.append(geos)
        else:  # It's shapely geometry
            self.solid_geometry = [self.solid_geometry, geos]

        # flatten the self.solid_geometry list for import_svg() to import SVG as Gerber
        self.solid_geometry = list(self.flatten_list(self.solid_geometry))

        tooldia = float(self._app_option("tools_mill_tooldia"))
        tooldia = float('%.*f' % (self.decimals, tooldia))

        new_data = {k: v for k, v in self.obj_options.items()}

        self.tools.update({
            1: {
                'tooldia': tooldia,
                'offset': 'Path',
                'offset_value': 0.0,
                'type': 'Rough',
                'tool_type': 'C1',
                'data': deepcopy(new_data),
                'solid_geometry': self.solid_geometry
            }
        })

        self.tools[1]['data']['name'] = self.obj_options['name']

    def import_dxf_as_geo(self, filename, units='MM', text_mode='stroke'):
        """
        Imports shapes from an DXF file into the object's geometry.

        :param filename:    Path to the DXF file.
        :type filename:     str
        :param units:       Application units
        :param text_mode:   'stroke' for CNC paths, 'outline' for filled shapes, 'none' to skip
        :return: None
        """
        self.app.log.debug("Parsing DXF file geometry into a Geometry object solid geometry.")

        # Multi-geo Geometry Object
        self.multigeo = True

        # Parse into list of shapely objects
        try:
            dxf = ezdxf.readfile(filename)
        except (KeyError, Exception) as e:
            self.app.log.warning("Standard DXF read failed (%s), trying recovery mode..." % str(e))
            dxf, auditor = recover.readfile(filename)
        geos = dxf_to_shapes(dxf, text_mode=text_mode, options=getattr(self.app, 'options', None), kind='geometry')

        # trying to optimize the resulting geometry by merging contiguous lines
        geos = list(self.flatten_list(geos))
        geos_polys = []
        geos_lines = []
        for g in geos:
            if isinstance(g, (Polygon, MultiPolygon)):
                geos_polys.append(g)
            else:
                geos_lines.append(g)

        merged_lines = linemerge(geos_lines)
        geos = geos_polys

        try:
            w_geo = merged_lines.geoms if isinstance(merged_lines, MultiLineString) else merged_lines
            for ml in w_geo:
                geos.append(ml)
        except TypeError:
            geos.append(merged_lines)

        # Add to object
        if self.solid_geometry is None:
            self.solid_geometry = []

        if type(self.solid_geometry) is list:
            if type(geos) is list:
                self.solid_geometry += geos
            else:
                self.solid_geometry.append(geos)
        else:  # It's shapely geometry
            self.solid_geometry = [self.solid_geometry, geos]

        tooldia = float(self._app_option("tools_mill_tooldia"))
        tooldia = float('%.*f' % (self.decimals, tooldia))

        new_data = {k: v for k, v in self.obj_options.items()}

        self.tools.update({
            1: {
                'tooldia': tooldia,
                'offset': 'Path',
                'offset_value': 0.0,
                'type': 'Rough',
                'tool_type': 'C1',
                'data': deepcopy(new_data),
                'solid_geometry': self.solid_geometry
            }
        })

        self.tools[1]['data']['name'] = self.obj_options['name']

    def size(self):
        """
        Returns (width, height) of rectangular
        bounds of geometry.
        """
        if self.solid_geometry is None:
            log.warning("Solid_geometry not computed yet.")
            return 0
        bounds = self.bounds()
        return bounds[2] - bounds[0], bounds[3] - bounds[1]

    def get_empty_area(self, boundary=None):
        """
        Returns the complement of self.solid_geometry within
        the given boundary polygon. If not specified, it defaults to
        the rectangular bounding box of self.solid_geometry.
        """
        if boundary is None:
            boundary = self.solid_geometry.envelope
        return boundary.difference(self.solid_geometry)

    def clear_polygon_shrink(
            self,
            polygon,
            tooldia,
            steps_per_circle,
            overlap=0.15,
            connect=True,
            contour=True,
            prog_plot=False
    ):
        """
        Creates geometry inside a polygon for a tool to cover
        the whole area.

        This algorithm shrinks the edges of the polygon and takes
        the resulting edges as toolpaths.

        :param polygon:             Polygon to clear.
        :param tooldia:             Diameter of the tool.
        :param steps_per_circle:    number of linear segments to be used to approximate a circle
        :param overlap:             Overlap of toolpasses.
        :param connect:             Draw lines between disjoint segments to
                                    minimize tool lifts.
        :param contour:             Paint around the edges. Inconsequential in
                                    this painting method.
        :param prog_plot:           boolean; if Ture use the progressive plotting
        :return:
        """

        # log.debug("camlib.clear_polygon_shrink()")

        # Shapely uses segments per quadrant, not per full circle.
        quad_segs = circle_quad_segs(steps_per_circle)

        # Bound every inset's chord error by the configured cutter-radius circle
        # error, and by Geo Tolerance when supplied. Use the same IN convention
        # as NCC and Milling; display/G-code decimal counts are not tolerances.
        chord_error = tooldia * math.sin(math.pi / (8 * quad_segs)) ** 2
        tolerance = float(getattr(self.app, 'options', {}).get('global_tolerance', 0))
        if getattr(self.app, 'units', 'MM').upper() == 'IN':
            tolerance /= 20
        if tolerance > 0:
            chord_error = min(chord_error, tolerance)
        step = tooldia * (1 - overlap)

        # The toolpaths
        # Index first and last points in paths
        def get_pts(o):
            return [o.coords[0], o.coords[-1]]

        geoms = AppRTreeStorage()
        geoms.get_points = get_pts
        # the paths are collected and go in the storage in 1 batch (bulk load) when all are made
        new_paths = []

        # Can only result in a Polygon or MultiPolygon
        # NOTE: The resulting polygon can be "empty".
        current = polygon.buffer((-tooldia / 2), quad_segs=quad_segs)
        current = flatten_shapely_geometry(current)

        for p in current:
            new_paths.append(p.exterior)
            for i in p.interiors:
                new_paths.append(i)

        last_update = 0.0
        for cl_pol in current:
            pass_index = 1
            while True:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace
                # provide the app with a way to process the GUI events when in a blocking loop
                process_gui_events()
                if self.app.abort_flag:
                    raise grace
                if prog_plot and monotonic() - last_update >= 0.05:
                    self.temp_shapes.redraw()
                    last_update = monotonic()

                # Offset the original margin, not the previous tessellated inset.
                # GEOS rounds fillet segment counts: short arcs can span up to
                # 1.5 nominal increments. Combined with 1 - cos(x) <= x*x/2,
                # this bounds chord error without compounding round-join vertices.
                distance = pass_index * step
                inset_quad_segs = max(
                    quad_segs, math.ceil(3 * math.pi / 8 * math.sqrt(distance / (2 * chord_error))))
                inset = cl_pol.buffer(-distance, quad_segs=inset_quad_segs)
                cl_pol_list = flatten_shapely_geometry(inset)

                added_flag = False
                for tiny_pol in cl_pol_list:
                    if tiny_pol.area > 0:
                        added_flag = True
                        new_paths.append(tiny_pol.exterior)
                        if prog_plot:
                            self.plot_temp_shapes(tiny_pol.exterior)

                        for i in tiny_pol.interiors:
                            new_paths.append(i)
                            if prog_plot:
                                self.plot_temp_shapes(i)
                if added_flag is False:
                    break

                pass_index += 1

                # if isinstance(cl_pol, MultiPolygon):
                #     cl_pol = flatten_shapely_geometry(cl_pol)
                #
                #     added_flag = False
                #     for tiny_pol in cl_pol:
                #         if tiny_pol.area > 0:
                #             added_flag = True
                #             geoms.insert(tiny_pol.exterior)
                #             if prog_plot:
                #                 self.plot_temp_shapes(tiny_pol.exterior)
                #
                #             for i in tiny_pol.interiors:
                #                 geoms.insert(i)
                #                 if prog_plot:
                #                     self.plot_temp_shapes(i)
                #     if added_flag is False:
                #         break
                #
                #     cl_pol = MultiPolygon(cl_pol)
                # else:
                #     if cl_pol.area > 0:
                #         geoms.insert(cl_pol.exterior)
                #         if prog_plot:
                #             self.plot_temp_shapes(cl_pol.exterior)
                #
                #         for i in cl_pol.interiors:
                #             geoms.insert(i)
                #             if prog_plot:
                #                 self.plot_temp_shapes(i)
                #     else:
                #         break

        geoms.insert_many(new_paths)
        if not geoms.objects:
            self.app.log.debug("camlib.Geometry.clear_polygon_shrink() --> Current Area is zero")
            return

        if prog_plot:
            self.temp_shapes.redraw()

        # Optimization: Reduce lifts
        if connect:
            # log.debug("Reducing tool lifts...")
            self.app.inform_no_echo.emit(_("Connect: reducing tool lifts. This may take a while, please wait..."))
            self.app.proc_container.update_view_text(' %s' % _("Connecting..."), clear=True)
            geoms = Geometry.paint_connect(geoms, polygon, tooldia, int(steps_per_circle))

        return geoms

    def clear_polygon_seed(
            self,
            polygon_to_clear: Polygon,
            tooldia: int | float | str,
            steps_per_circle: int,
            seedpoint: Point | None = None,
            overlap: float = 0.15,
            connect: bool = True,
            contour: bool = True,
            simplify_tol: float = 0.0,
            prog_plot: bool = False,
    ):
        """
        Creates geometry inside a polygon for a tool to cover
        the whole area.

        This algorithm starts with a seed point inside the polygon
        and draws circles around it. Arcs inside the polygons are
        valid cuts. Finalizes by cutting around the inside edge of
        the polygon.

        :param polygon_to_clear:    Shapely.geometry.Polygon
        :param steps_per_circle:    how many linear segments to use to approximate a circle
        :param tooldia:             Diameter of the tool
        :param seedpoint:           Shapely.geometry.Point or None
        :param overlap:             Tool fraction overlap between passes
        :param connect:             Connect disjoint segment to minimize tool lifts
        :param contour:             Cut contour inside the polygon.
        :param simplify_tol:        Simplification tolerance
        :param prog_plot:           boolean; if True use the progressive plotting
        :return:                    List of toolpaths covering polygon.
        :rtype:                     AppRTreeStorage | None
        """

        # log.debug("camlib.clear_polygon_seed()")

        # Shapely uses segments per quadrant, not per full circle.
        quad_segs = circle_quad_segs(steps_per_circle)

        # Current buffer radius
        radius = tooldia / 2 * (1 - overlap)

        # ## The toolpaths
        # Index first and last points in paths
        def get_pts(o):
            return [o.coords[0], o.coords[-1]]

        geom_elems = AppRTreeStorage()
        geom_elems.get_points = get_pts
        # the paths are collected and go in the storage in 1 batch (bulk load) when all are made
        new_paths = []

        # Path margin
        buffered_margin = polygon_to_clear.buffer(-tooldia / 2, quad_segs=quad_segs)
        path_margin = flatten_shapely_geometry(buffered_margin, simplify_tolerance=simplify_tol)
        path_margin = MultiPolygon(path_margin)

        if path_margin.is_empty or path_margin is None:
            return None

        # Estimate good seedpoint if not provided.
        if seedpoint is None:
            seedpoint = path_margin.representative_point()

        last_update = 0.0
        # The rings are made and cut SEED_BAND_RINGS at a time, up to the first one that does not touch the margin
        # and not beyond the farthest vertex of the margin. The cut of a ring costs O(size of the margin): a large
        # margin (the area of a board has a hole for each copper feature) is cut without the polygons and holes that
        # no ring of the band can touch (seed_band_margin()). The same paths, bit for bit.
        step = tooldia * (1 - overlap)
        seed_point = Point(seedpoint)
        seed_xy = shapely.get_coordinates(seed_point)[0]
        margin_xy = shapely.get_coordinates(path_margin)
        farthest = float(np.max(np.hypot(margin_xy[:, 0] - seed_xy[0], margin_xy[:, 1] - seed_xy[1])))
        band_margin = None
        if len(margin_xy) >= Geometry.SEED_BAND_MIN_COORDS:
            band_margin = seed_band_margin(path_margin, seed_point)
        # A ring vertex is on its circle and an edge is inside it by at most 1 - cos(pi / (4 * quad_segs)) of the
        # radius; the simplification moves the edges by at most simplify_tol. tooldia is added as a safe distance.
        inner_ratio = math.cos(math.pi / (4 * quad_segs))
        touches = True
        # Grow from seed until outside the box. The polygons will
        # never have an interior, so take the exterior LinearRing.
        while touches:
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            # provide the app with a way to process the GUI events when in a blocking loop
            process_gui_events()
            if self.app.abort_flag:
                raise grace
            if prog_plot and monotonic() - last_update >= 0.05:
                self.temp_shapes.redraw()
                last_update = monotonic()

            radii = []
            while len(radii) < Geometry.SEED_BAND_RINGS and radius * inner_ratio - simplify_tol - tooldia <= farthest:
                radii.append(radius)
                radius += step
            if not radii:
                # the next ring is beyond the farthest vertex of the margin: it does not touch the margin
                break
            rings = shapely.get_exterior_ring(shapely.buffer(seed_point, radii, quad_segs=quad_segs))
            rings = shapely.simplify(rings, simplify_tol)

            band = path_margin
            if band_margin is not None:
                band = band_margin(radii[0] * inner_ratio - simplify_tol - tooldia, radii[-1] + tooldia)

            for path in shapely.intersection(rings, band):
                # Touches polygon?
                if path.is_empty:
                    touches = False
                    break

                # path can be a collection of paths.
                path_geometry = flatten_shapely_geometry(path, simplify_tolerance=simplify_tol)
                for p in path_geometry:
                    new_paths.append(p)
                    if prog_plot:
                        self.plot_temp_shapes(p)

        # Clean inside edges (contours) of the original polygon
        if contour:
            buffered_poly = autolist(buffered_margin)
            buffered_poly: list[Polygon] = [x.simplify(simplify_tol) for x in buffered_poly]
            outer_edges = [x.exterior for x in buffered_poly]

            inner_edges = []
            # Over resulting polygons
            for x in buffered_poly:
                for y in x.interiors:  # Over interiors of each polygon
                    inner_edges.append(y)

            for g in outer_edges + inner_edges:
                if g and not g.is_empty:
                    new_paths.append(g)
                    if prog_plot:
                        self.plot_temp_shapes(g)

        geom_elems.insert_many(new_paths)

        if prog_plot:
            self.temp_shapes.redraw()

        # Optimization connect touching paths
        # log.debug("Connecting paths...")
        # geom_elems = Geometry.path_connect(geom_elems)

        # Optimization: Reduce lifts
        if connect:
            # log.debug("Reducing tool lifts...")
            self.app.inform_no_echo.emit(_("Connect: reducing tool lifts. This may take a while, please wait..."))
            self.app.proc_container.update_view_text(' %s' % _("Connecting..."), clear=True)
            geoms_conn = Geometry.paint_connect(geom_elems, polygon_to_clear, tooldia, steps_per_circle)
            if geoms_conn:
                return geoms_conn

        return geom_elems

    def clear_polygon_lines(
            self,
            polygon: Polygon,
            tooldia: int | float | str,
            steps_per_circle: int,
            overlap: float = 0.15,
            connect: bool = True,
            contour: bool = True,
            simplify_tol: float = 0.0,
            prog_plot: bool = False,
    ):
        """
        Creates geometry inside a polygon for a tool to cover
        the whole area.

        This algorithm draws horizontal lines inside the polygon.

        :param polygon:             The polygon being painted.
        :type polygon:              shapely.geometry.Polygon
        :param tooldia:             Tool diameter.
        :param steps_per_circle:    how many linear segments to use to approximate a circle
        :param overlap:             Tool path overlap percentage.
        :param connect:             Connect lines to avoid tool lifts.
        :param contour:             Paint around the edges.
        :param simplify_tol:        Simplification tolerance
        :param prog_plot:           boolean; if to use the progressive plotting
        :return:
        """

        # log.debug("camlib.clear_polygon_lines()")
        quad_segs = circle_quad_segs(steps_per_circle)
        # The distance of the margin from the boundary and the distance of the first and last scan line from the
        # edge of the bounding box. The scan lines at the edge must lie inside the margin, so they use the same value.
        margin_dist = tooldia / 1.99999999
        edge_dist = tooldia / 1.999999999
        if not isinstance(polygon, Polygon):
            self.app.log.debug("camlib.Geometry.clear_polygon_lines() --> Not a Polygon but %s" % str(type(polygon)))
            return None

        # The toolpaths
        # Index first and last points in paths
        def get_pts(o):
            return [o.coords[0], o.coords[-1]]

        geoms = AppRTreeStorage()
        geoms.get_points = get_pts
        # the paths are collected and go in the storage in 1 batch (bulk load) when all are made
        new_paths = []

        lines_trimmed = []

        # Bounding box
        left, bot, right, top = polygon.bounds

        try:
            margin_poly = polygon.buffer(-margin_dist, quad_segs=quad_segs)
            margin_poly = margin_poly.simplify(simplify_tol)
        except Exception:
            self.app.log.debug("camlib.Geometry.clear_polygon_lines() --> Could not buffer the Polygon")
            return None

        last_update = 0.0
        # The cut of a scan line costs O(size of the polygon). Each line is cut from a band of SCAN_BAND_LINES lines,
        # clipped from the margin polygon: about 12x faster on a board polygon of 14600 vertices (same result).
        step = tooldia * (1 - overlap)
        band = None
        # decide the direction of the lines
        if abs(left - right) >= abs(top - bot):
            # First line
            try:
                y = top - margin_dist
                band_end = y
                while y > bot + edge_dist:
                    if self.app.abort_flag:
                        # graceful abort requested by the user
                        raise grace

                    # provide the app with a way to process the GUI events when in a blocking loop
                    process_gui_events()
                    if self.app.abort_flag:
                        raise grace
                    if prog_plot and monotonic() - last_update >= 0.05:
                        self.temp_shapes.redraw()
                        last_update = monotonic()

                    if band is None or y < band_end:
                        band_end = y - self.SCAN_BAND_LINES * step
                        band = shapely.clip_by_rect(margin_poly, left - 1, band_end - step, right + 1, y + step)
                    line = LineString([(left, y), (right, y)])
                    line = line.intersection(band)
                    line = flatten_shapely_geometry(line, simplify_tolerance=simplify_tol)
                    lines_trimmed += line
                    y -= step
                    if prog_plot:
                        self.plot_temp_shapes(line)

                # Last line
                y = bot + margin_dist
                line = LineString([(left, y), (right, y)])
                line = line.intersection(margin_poly)

                lines_geometry = flatten_shapely_geometry(line, simplify_tolerance=simplify_tol)
                for ll in lines_geometry:
                    lines_trimmed.append(ll)
                    if prog_plot:
                        self.plot_temp_shapes(ll)
            except grace:
                raise
            except Exception as e:
                self.app.log.error('camlib.Geometry.clear_polygon_lines() Processing poly --> %s' % str(e))
                return None
        else:
            # First line
            try:
                x = left + margin_dist
                band_end = x
                while x < right - edge_dist:
                    if self.app.abort_flag:
                        # graceful abort requested by the user
                        raise grace

                    # provide the app with a way to process the GUI events when in a blocking loop
                    process_gui_events()
                    if self.app.abort_flag:
                        raise grace
                    if prog_plot and monotonic() - last_update >= 0.05:
                        self.temp_shapes.redraw()
                        last_update = monotonic()

                    if band is None or x > band_end:
                        band_end = x + self.SCAN_BAND_LINES * step
                        band = shapely.clip_by_rect(margin_poly, x - step, bot - 1, band_end + step, top + 1)
                    line = LineString([(x, top), (x, bot)])
                    line = line.intersection(band)
                    line = flatten_shapely_geometry(line, simplify_tolerance=simplify_tol)
                    lines_trimmed += line
                    x += step
                    if prog_plot:
                        self.plot_temp_shapes(line)

                # Last line
                x = right - margin_dist
                line = LineString([(x, top), (x, bot)])
                line = line.intersection(margin_poly)

                lines_geometry = flatten_shapely_geometry(line, simplify_tolerance=simplify_tol)
                for ll in lines_geometry:
                    lines_trimmed.append(ll)
                    if prog_plot:
                        self.plot_temp_shapes(ll)
            except grace:
                raise
            except Exception as e:
                self.app.log.error('camlib.Geometry.clear_polygon_lines() Processing poly --> %s' % str(e))
                return None

        if prog_plot:
            self.temp_shapes.redraw()

        lines_trimmed = unary_union(lines_trimmed)

        # Add lines to storage
        lines_t_geo = flatten_shapely_geometry(lines_trimmed, simplify_tolerance=simplify_tol)
        for line in lines_t_geo:
            if isinstance(line, LineString) or isinstance(line, LinearRing):
                if not line.is_empty:
                    new_paths.append(line)
            else:
                self.app.log.debug("camlib.Geometry.clear_polygon_lines(). Not a line: %s" % str(type(line)))

        # Add margin (contour) to storage
        if contour:
            margin_poly_geo = flatten_shapely_geometry(margin_poly, simplify_tolerance=simplify_tol)
            for poly in margin_poly_geo:
                if isinstance(poly, Polygon) and not poly.is_empty:
                    new_paths.append(poly.exterior)
                    if prog_plot:
                        self.plot_temp_shapes(poly.exterior)
                    for ints in poly.interiors:
                        new_paths.append(ints)
                        if prog_plot:
                            self.plot_temp_shapes(ints)

        geoms.insert_many(new_paths)

        if prog_plot:
            self.temp_shapes.redraw()

        # Optimization: Reduce lifts
        if connect:
            # log.debug("Reducing tool lifts...")
            self.app.inform_no_echo.emit(_("Connect: reducing tool lifts. This may take a while, please wait..."))
            self.app.proc_container.update_view_text(' %s' % _("Connecting..."), clear=True)
            geoms_conn = Geometry.paint_connect(geoms, polygon, tooldia, steps_per_circle)
            if geoms_conn:
                return geoms_conn

        return geoms

    def fill_with_lines(self, line, aperture_size, tooldia, steps_per_circle, overlap=0.15, connect=True, contour=True,
                        prog_plot=False):
        """
        Creates geometry of lines inside a polygon for a tool to cover
        the whole area.

        This algorithm draws parallel lines inside the polygon.

        :param line:                The target line that create painted polygon.
        :param aperture_size:       the size of the aperture that is used to draw the 'line' as a polygon
        :type line:                 shapely.geometry.LineString or shapely.geometry.MultiLineString
        :param tooldia:             Tool diameter.
        :param steps_per_circle:    how many linear segments to use to approximate a circle
        :param overlap:             Tool path overlap percentage.
        :param connect:             Connect lines to avoid tool lifts.
        :param contour:             Paint around the edges.
        :param prog_plot:           boolean; if to use the progressive plotting
        :return:
        """

        # log.debug("camlib.fill_with_lines()")
        quad_segs = circle_quad_segs(steps_per_circle)
        if not isinstance(line, LineString):
            self.app.log.debug(
                "camlib.Geometry.fill_with_lines() --> Not a LineString/MultiLineString but %s" % str(type(line)))
            return None

        # The toolpaths
        # Index first and last points in paths
        def get_pts(o):
            return [o.coords[0], o.coords[-1]]

        # def get_extrapolated_line(p1, p2):
        #     """
        #     Creates a line extrapolated in p1->p2 direction.
        #     Get a line that extends the 'line' LineString toward our side.
        #     """
        #     EXTRAPOLATION_RATIO = 10
        #     a = p1
        #     b = (p1[0] + EXTRAPOLATION_RATIO * (p2[0] - p1[0]), p1[1] + EXTRAPOLATION_RATIO * (p2[1] - p1[1]))
        #     return [a, b]

        def extend_line(p1: tuple, p2: tuple, extension_distance: float) -> list:
            # Compute the vector represented by the line
            vector = (p2[0] - p1[0], p2[1] - p1[1])

            # Compute the length of the vector
            length = math.sqrt(vector[0] ** 2 + vector[1] ** 2)

            # Normalize the vector
            normalized_vector = (vector[0] / length, vector[1] / length)

            # Compute extended points
            extended_point1 = (
            p1[0] - extension_distance * normalized_vector[0], p1[1] - extension_distance * normalized_vector[1])
            extended_point2 = (
            p2[0] + extension_distance * normalized_vector[0], p2[1] + extension_distance * normalized_vector[1])

            return [extended_point1, extended_point2]

        geoms = AppRTreeStorage()
        geoms.get_points = get_pts
        # the paths are collected and go in the storage in 1 batch (bulk load) when all are made
        new_paths = []

        lines_trimmed = []

        polygon = line.buffer(aperture_size / 2.0, quad_segs=quad_segs)
        if isinstance(line, LineString):
            line_coords = list(line.coords)
        else:
            line_coords = []
            for l in line.geoms:
                line_coords += list(l.coords)

        # # #### the line (as parameter) needs to be extended by half of tool diameter such that the cleared area will
        # # include also the rounded part of the trace if the trace diameter is much higher than the clearing diameter
        # first_line_coords = get_extrapolated_line(line_coords[1], line_coords[0])
        # second_line_coords = get_extrapolated_line(line_coords[-2], line_coords[-1])
        #
        # sum_coords = first_line_coords + line_coords + second_line_coords
        # sum_set = set(tuple(x) for x in sum_coords)
        # simplified_coords = [x for x in sum_set]
        # combo_line = LineString(simplified_coords)
        # prepared_line = combo_line.intersection(polygon)
        # if isinstance(prepared_line, MultiLineString):
        #     prepared_line = linemerge(prepared_line)
        #     if isinstance(prepared_line, MultiLineString):
        #         prepared_line = unary_union(prepared_line).simplify(0)
        #         if isinstance(prepared_line, MultiLineString):
        #             t_coords = []
        #             for m_l in prepared_line.geoms:
        #                 t_coords += list(m_l.coords)
        #             sum_set = set(tuple(x) for x in t_coords)
        #             simplified_coords = [x for x in sum_set]
        #             try:
        #                 prepared_line = LineString(simplified_coords)
        #             except Exception as err:
        #                 print(str(err))

        prepared_line_coords = extend_line(line_coords[0], line_coords[-1], extension_distance=1.5 * tooldia)
        prepared_line = LineString(prepared_line_coords)

        if isinstance(prepared_line, LineString):
            prepared_line = prepared_line.simplify(0)
        # ##########################################################################
        try:
            margin_poly: BaseGeometry | list[BaseGeometry] = polygon.buffer(
                -tooldia / 2.0, quad_segs=quad_segs)
        except Exception:
            self.app.log.debug(
                "camlib.Geometry.fill_with_lines() --> Could not buffer the Polygon, tool diameter too high")
            return None

        # First line
        try:
            delta = 0
            while delta < aperture_size / 2:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                # provide the app with a way to process the GUI events when in a blocking loop
                QtWidgets.QApplication.processEvents()

                new_line = prepared_line.parallel_offset(distance=delta, side='left', resolution=quad_segs)
                new_line = new_line.intersection(margin_poly)
                lines_trimmed.append(new_line) if not new_line.is_empty else None

                delta += tooldia * (1 - overlap)
                if prog_plot:
                    self.plot_temp_shapes(new_line)
                    self.temp_shapes.redraw()

                new_line = prepared_line.parallel_offset(distance=delta, side='right', resolution=quad_segs)
                new_line = new_line.intersection(margin_poly)
                lines_trimmed.append(new_line) if not new_line.is_empty else None

                delta += tooldia * (1 - overlap)
                if prog_plot:
                    self.plot_temp_shapes(new_line)
                    self.temp_shapes.redraw()

            # Last line
            # The last line must lie inside the margin, so it uses the margin distance.
            delta = (aperture_size / 2) - (tooldia / 1.99999999)

            new_line = prepared_line.parallel_offset(distance=delta, side='left', resolution=quad_segs)
            new_line = new_line.intersection(margin_poly)
        except Exception as e:
            self.app.log.error('camlib.Geometry.fill_with_lines() Processing poly --> %s' % str(e))
            return None

        if new_line and not new_line.is_empty:
            lines_geometry = new_line.geoms if isinstance(new_line, MultiLineString) else new_line
            try:
                for ll in lines_geometry:
                    lines_trimmed.append(ll)
                    if prog_plot:
                        self.plot_temp_shapes(ll)
            except TypeError:
                lines_trimmed.append(lines_geometry)
                if prog_plot:
                    self.plot_temp_shapes(lines_geometry)

        new_line = prepared_line.parallel_offset(distance=delta, side='right', resolution=quad_segs)
        new_line = new_line.intersection(margin_poly)

        if new_line and not new_line.is_empty:
            lines_geometry = new_line.geoms if isinstance(new_line, MultiLineString) else new_line
            try:
                for ll in lines_geometry:
                    lines_trimmed.append(ll)
                    if prog_plot:
                        self.plot_temp_shapes(ll)
            except TypeError:
                lines_trimmed.append(lines_geometry)
                if prog_plot:
                    self.plot_temp_shapes(lines_geometry)

            if prog_plot:
                self.temp_shapes.redraw()

        lines_trimmed = unary_union(lines_trimmed)

        # Add lines to storage
        lines_geometry: BaseGeometry | list[BaseGeometry] = (
            lines_trimmed.geoms
            if isinstance(lines_trimmed, MultiLineString)
            else lines_trimmed
        )

        try:
            for line_g in lines_geometry:
                if isinstance(line_g, LineString) or isinstance(line_g, LinearRing):
                    new_paths.append(line_g)
                else:
                    self.app.log.debug("camlib.Geometry.fill_with_lines(). Not a line: %s" % str(type(line_g)))
        except TypeError:
            # in case lines_trimmed are not iterable (Linestring, LinearRing)
            new_paths.append(lines_geometry) if lines_geometry and not lines_geometry.is_empty else None

        # Add margin (contour) to storage
        if contour:
            try:
                for poly in margin_poly:
                    if isinstance(poly, Polygon) and not poly.is_empty:
                        new_paths.append(poly.exterior)
                        if prog_plot:
                            self.plot_temp_shapes(poly.exterior)
                        for ints in poly.interiors:
                            new_paths.append(ints)
                            if prog_plot:
                                self.plot_temp_shapes(ints)
            except TypeError:
                if isinstance(margin_poly, Polygon) and not margin_poly.is_empty:
                    marg_ext = margin_poly.exterior
                    new_paths.append(marg_ext)
                    if prog_plot:
                        self.plot_temp_shapes(margin_poly.exterior)
                    for ints in margin_poly.interiors:
                        new_paths.append(ints)
                        if prog_plot:
                            self.plot_temp_shapes(ints)

        geoms.insert_many(new_paths)

        if prog_plot:
            self.temp_shapes.redraw()

        # Optimization: Reduce lifts
        if connect:
            # log.debug("Reducing tool lifts...")
            self.app.inform_no_echo.emit(_("Connect: reducing tool lifts. This may take a while, please wait..."))
            self.app.proc_container.update_view_text(' %s' % _("Connecting..."), clear=True)
            geoms_conn = Geometry.paint_connect(geoms, polygon, tooldia, steps_per_circle)
            if geoms_conn:
                return geoms_conn

        return geoms

    def scale(self, xfactor, yfactor, point=None):
        """
        Scales all the object's geometry by a given factor. Override
        this method.
        :param xfactor: Number by which to scale on X axis.
        :type xfactor: float
        :param yfactor: Number by which to scale on Y axis.
        :type yfactor: float
        :param point: point to be used as reference for scaling; a tuple
        :return: None
        :rtype: None
        """
        return

    def offset(self, vect):
        """
        Offset the geometry by the given vector. Override this method.

        :param vect: (x, y) vector by which to offset the object.
        :type vect: tuple
        :return: None
        """
        return

    @staticmethod
    def paint_connect(storage, boundary, tooldia, steps_per_circle, max_walk=None):
        """
        Connects paths that results in a connection segment that is
        within the paint area. This avoids unnecessary tool lifting.

        :param storage: Geometry to be optimized.
        :type storage: AppRTreeStorage
        :param boundary: Polygon defining the limits of the paintable area.
        :type boundary: Polygon
        :param tooldia: Tool diameter.
        :rtype tooldia: float
        :param steps_per_circle: how many linear segments to use to approximate a circle
        :param max_walk: Maximum allowable distance without lifting tool.
        :type max_walk: float or None
        :return: Optimized geometry.
        :rtype: AppRTreeStorage
        """

        # If max_walk is not specified, the maximum allowed is
        # 10 times the tool diameter
        max_walk = max_walk or 10 * tooldia

        # Assuming geo list is a flat list of flat elements

        # ## Index first and last points in paths
        def get_pts(o):
            return [o.coords[0], o.coords[-1]]

        # storage = AppRTreeStorage()
        # storage.get_points = get_pts
        #
        # for shape in geolist:
        #     if shape is not None:
        #         # Make LinearRing's into LineString's otherwise
        #         # When chaining the coordinates path is messed up.
        #         storage.insert(LineString(shape))
        #         #storage.insert(shape)

        # ## Iterate over geometry paths getting the nearest each time.
        # optimized_paths = []
        optimized_paths = AppRTreeStorage()
        optimized_paths.get_points = get_pts
        # the joined paths go in the storage in 1 batch (bulk load) at the end
        joined_paths = []
        path_count = 0
        current_pt = (0, 0)
        try:
            pt, geo = storage.nearest(current_pt)
        except StopIteration:
            log.debug("camlib.Geometry.paint_connect(). Storage empty")
            return None

        storage.remove(geo)

        # ponytail: accumulate coordinates; materialize once per completed path.
        # The coordinates are kept as NumPy arrays: a LineString made from 1 array is several times faster than one
        # made from a list of coordinate tuples, and get_coordinates() is faster than the coords tuples.
        include_z = geo.has_z
        geo_coords = [shapely.get_coordinates(geo, include_z=include_z)]
        current_pt = tuple(geo_coords[-1][-1].tolist())
        boundary_edge = boundary.boundary
        # Each walk is tested against the full boundary: prepared geometries test it in O(log n), not O(n).
        shapely.prepare(boundary)
        shapely.prepare(boundary_edge)
        min_clearance = tooldia / 2 - 1e-9
        try:
            while True:
                path_count += 1
                # log.debug("Path %d" % path_count)

                pt, candidate = storage.nearest(current_pt)
                storage.remove(candidate)

                candidate_coords = shapely.get_coordinates(candidate, include_z=include_z)

                # If last point in geometry is the nearest
                # then reverse coordinates.
                # but prefer the first one if last == first
                if pt != tuple(candidate_coords[0].tolist()) and pt == tuple(candidate_coords[-1].tolist()):
                    candidate_coords = candidate_coords[::-1]

                # Straight line from current_pt to pt.
                # Is the toolpath inside the geometry?
                walk_path = LineString([current_pt, pt]) if current_pt != pt else Point(pt)

                # A walk inside the boundary and at the real tool radius (or more) from its edge keeps the
                # full cutter inside, also across holes; no polygonal buffer of the cutter is needed.
                # The paths of the first pass are at exactly the tool radius from the
                # boundary, so a rounding error must not reject the walk.
                if (walk_path.length < max_walk and boundary.contains(walk_path)
                        and not shapely.dwithin(walk_path, boundary_edge, min_clearance)):
                    # log.debug("Walk to path #%d is inside. Joining." % path_count)

                    # Completely inside. Append...
                    geo_coords.append(candidate_coords)

                else:

                    # Have to lift tool. End path.
                    # log.debug("Path #%d not within boundary. Next." % path_count)
                    # optimized_paths.append(geo)
                    joined_paths.append(LineString(np.concatenate(geo_coords)))
                    geo_coords = [candidate_coords]

                current_pt = tuple(geo_coords[-1][-1].tolist())

                # Next
                # pt, geo = storage.nearest(current_pt)

        except StopIteration:  # Nothing left in storage.
            # pass
            joined_paths.append(LineString(np.concatenate(geo_coords)))

        optimized_paths.insert_many(joined_paths)
        return optimized_paths

    @staticmethod
    def path_connect(storage, origin=(0, 0)):
        """
        Simplifies paths in the FlatCAMRTreeStorage storage by
        connecting paths that touch on their endpoints.

        :param storage:     Storage containing the initial paths.
        :rtype storage:     FlatCAMRTreeStorage
        :param origin:      tuple; point from which to calculate the nearest point
        :return:            Simplified storage.
        :rtype:             AppRTreeStorage
        """

        # log.debug("path_connect()")

        # ## Index first and last points in paths
        def get_pts(o):
            return [o.coords[0], o.coords[-1]]

        #
        # storage = AppRTreeStorage()
        # storage.get_points = get_pts
        #
        # for shape in pathlist:
        #     if shape is not None:
        #         storage.insert(shape)

        path_count = 0
        pt, geo = storage.nearest(origin)
        storage.remove(geo)
        # optimized_geometry = [geo]
        optimized_geometry = AppRTreeStorage()
        optimized_geometry.get_points = get_pts
        # the joined paths go in the storage in 1 batch (bulk load) at the end
        joined_paths = []
        # optimized_geometry.insert(geo)
        try:
            while True:
                path_count += 1
                _, left = storage.nearest(geo.coords[0])

                # If left touches geo, remove left from original
                # storage and append to geo.
                if isinstance(left, LineString):
                    if left.coords[0] == geo.coords[0]:
                        storage.remove(left)
                        # geo.coords = list(geo.coords)[::-1] + list(left.coords)   # Shapely 2.0
                        geo = LineString(list(geo.coords)[::-1] + list(left.coords))
                        continue

                    if left.coords[-1] == geo.coords[0]:
                        storage.remove(left)
                        # geo.coords = list(left.coords) + list(geo.coords)  # Shapely 2.0
                        geo = LineString(list(geo.coords)[::-1] + list(left.coords))
                        continue

                    if left.coords[0] == geo.coords[-1]:
                        storage.remove(left)
                        # geo.coords = list(geo.coords) + list(left.coords) # Shapely 2.0
                        geo = LineString(list(geo.coords) + list(left.coords))
                        continue

                    if left.coords[-1] == geo.coords[-1]:
                        storage.remove(left)
                        # geo.coords = list(geo.coords) + list(left.coords)[::-1] # Shapely 2.0
                        geo = LineString(list(geo.coords) + list(left.coords)[::-1])
                        continue

                _, right = storage.nearest(geo.coords[-1])

                # If right touches geo, remove left from original
                # storage and append to geo.
                if isinstance(right, LineString):
                    if right.coords[0] == geo.coords[-1]:
                        storage.remove(right)
                        # geo.coords = list(geo.coords) + list(right.coords)  # Shapely 2.0
                        geo = LineString(list(geo.coords) + list(right.coords))
                        continue

                    if right.coords[-1] == geo.coords[-1]:
                        storage.remove(right)
                        # geo.coords = list(geo.coords) + list(right.coords)[::-1]    # Shapely 2.0
                        geo = LineString(list(geo.coords) + list(right.coords)[::-1])
                        continue

                    if right.coords[0] == geo.coords[0]:
                        storage.remove(right)
                        # geo.coords = list(geo.coords)[::-1] + list(right.coords)    # Shapely 2.0
                        geo = LineString(list(geo.coords)[::-1] + list(right.coords))
                        continue

                    if right.coords[-1] == geo.coords[0]:
                        storage.remove(right)
                        # geo.coords = list(left.coords) + list(geo.coords)   # Shapely 2.0
                        geo = LineString(list(left.coords) + list(geo.coords))
                        continue

                # right is either a LinearRing or it does not connect
                # to geo (nothing left to connect to geo), so we continue
                # with right as geo.
                storage.remove(right)

                if isinstance(right, LinearRing):
                    joined_paths.append(right)
                else:
                    # Cannot extend geo any further. Put it away.
                    joined_paths.append(geo)

                    # Continue with right.
                    geo = right

        except StopIteration:  # Nothing found in storage.
            joined_paths.append(geo)

        # print path_count
        # log.debug("path_count = %d" % path_count)

        optimized_geometry.insert_many(joined_paths)
        return optimized_geometry

    def convert_units(self, obj_units):
        """
        Converts the units of the object to ``units`` by scaling all
        the geometry appropriately. This call ``scale()``. Don't call
        it again in descendants.

        :param obj_units:   "IN" or "MM"
        :type obj_units:    str
        :return:            Scaling factor resulting from unit change.
        :rtype:             float
        """

        if obj_units.upper() == self.units.upper():
            self.app.log.debug("camlib.Geometry.convert_units() --> Factor: 1")
            return 1.0

        if obj_units.upper() == "MM":
            factor = 25.4
            self.app.log.debug("camlib.Geometry.convert_units() --> Factor: 25.4")
        elif obj_units.upper() == "IN":
            factor = 1 / 25.4
            self.app.log.debug("camlib.Geometry.convert_units() --> Factor: %s" % str(1 / 25.4))
        else:
            self.app.log.error("Unsupported units: %s" % str(obj_units))
            self.app.log.debug("camlib.Geometry.convert_units() --> Factor: 1")
            return 1.0

        self.units = obj_units
        self.scale(factor, factor)
        self.file_units_factor = factor
        return factor

    def to_dict(self):
        """
        Returns a representation of the object as a dictionary.
        Attributes to include are listed in ``self.ser_attrs``.

        :return:    A dictionary-encoded copy of the object.
        :rtype:     dict
        """
        d = {attr: copy(getattr(self, attr)) for attr in self.ser_attrs}
        if getattr(self, 'special_group', None) == 'solder_paste_tool':
            d['special_group'] = self.special_group
            if hasattr(self, 'pp_solderpaste_name'):
                d['pp_solderpaste_name'] = self.pp_solderpaste_name
        return d

    def from_dict(self, d):
        """
        Sets object's attributes from a dictionary.
        Attributes to include are listed in ``self.ser_attrs``.
        This method will look only for only and all the
        attributes in ``self.ser_attrs``. They must all
        be present. Use only for deserializing saved
        objects.

        :param d:   Dictionary of attributes to set in the object.
        :type d:    dict
        :return:    None
        """
        for attr in self.ser_attrs:
            setattr(self, attr, d[attr])
        from appCommon.solderpaste import restore_paste_metadata
        restore_paste_metadata(self, d)

    def union(self):
        """
        Runs a unary_union on the list of objects in
        solid_geometry.

        :return: None
        """
        if getattr(self, 'multigeo', False) and self.tools:
            for tool in self.tools.values():
                tool['solid_geometry'] = [unary_union(self.flatten(tool.get('solid_geometry', [])))]
            self.solid_geometry = [unary_union([g for tool in self.tools.values() for g in tool['solid_geometry']])]
        else:
            self.solid_geometry = [unary_union(self.flatten())]
            for tool in self.tools.values():
                tool['solid_geometry'] = list(self.solid_geometry)
        if hasattr(self, 'obj_options'):
            self.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), self.bounds()))

    def export_svg(self, scale_stroke_factor=0.00,
                   scale_factor_x=None, scale_factor_y=None,
                   skew_factor_x=None, skew_factor_y=None,
                   skew_reference='center', scale_reference='center', mirror_reference='center',
                   mirror=None):
        """
        Exports the Geometry Object as an SVG Element

        :return: SVG Element
        """

        # Make sure we see a Shapely Geometry class and not a list
        if self.kind.lower() == 'geometry':
            flat_geo = []
            if self.multigeo:
                for tool in self.tools:
                    flat_geo += self.flatten(self.tools[tool]['solid_geometry'],
                                             pathonly=self._app_option("geometry_paths_only"))
                geom_svg = unary_union(flat_geo)
            else:
                geom_svg = unary_union(self.flatten(pathonly=self._app_option("geometry_paths_only")))
        else:
            geom_svg = unary_union(self.flatten(pathonly=self._app_option("geometry_paths_only")))

        xmin, ymin, xmax, ymax = geom_svg.bounds

        skew_ref = 'center'
        if skew_reference == 'topleft':
            skew_ref = (xmin, ymax)
        elif skew_reference == 'bottomleft':
            skew_ref = (xmin, ymin)
        elif skew_reference == 'topright':
            skew_ref = (xmax, ymax)
        elif skew_reference == 'bottomright':
            skew_ref = (xmax, ymin)

        scale_ref = 'center'
        if scale_reference == 'topleft':
            scale_ref = (xmin, ymax)
        elif scale_reference == 'bottomleft':
            scale_ref = (xmin, ymin)
        elif scale_reference == 'topright':
            scale_ref = (xmax, ymax)
        elif scale_reference == 'bottomright':
            scale_ref = (xmax, ymin)

        mirror_ref = 'center'
        if mirror_reference == 'topleft':
            mirror_ref = (xmin, ymax)
        elif mirror_reference == 'bottomleft':
            mirror_ref = (xmin, ymin)
        elif mirror_reference == 'topright':
            mirror_ref = (xmax, ymax)
        elif mirror_reference == 'bottomright':
            mirror_ref = (xmax, ymin)

        geom = geom_svg

        if scale_factor_x and not scale_factor_y:
            geom = affinity.scale(geom, scale_factor_x, 1.0, origin=scale_ref)
        elif not scale_factor_x and scale_factor_y:
            geom = affinity.scale(geom, 1.0, scale_factor_y, origin=scale_ref)
        elif scale_factor_x and scale_factor_y:
            geom = affinity.scale(geom, scale_factor_x, scale_factor_y, origin=scale_ref)

        if skew_factor_x and not skew_factor_y:
            geom = affinity.skew(geom, skew_factor_x, 0.0, origin=skew_ref)
        elif not skew_factor_x and skew_factor_y:
            geom = affinity.skew(geom, 0.0, skew_factor_y, origin=skew_ref)
        elif skew_factor_x and skew_factor_y:
            geom = affinity.skew(geom, skew_factor_x, skew_factor_y, origin=skew_ref)

        if mirror:
            if mirror == 'x':
                geom = affinity.scale(geom, 1.0, -1.0, origin=mirror_ref)
            if mirror == 'y':
                geom = affinity.scale(geom, -1.0, 1.0, origin=mirror_ref)
            if mirror == 'both':
                geom = affinity.scale(geom, -1.0, -1.0, origin=mirror_ref)

        # scale_factor is a multiplication factor for the SVG stroke-width used within shapely's svg export
        # If 0 or less which is invalid then default to 0.01
        # This value appears to work for zooming, and getting the output svg line width
        # to match that viewed on screen with FlatCam
        # MS: I choose a factor of 0.01 so the scale is right for PCB UV film
        if scale_stroke_factor <= 0:
            scale_stroke_factor = 0.01

        # Convert to an SVG element
        svg_elem = geom.svg(scale_factor=scale_stroke_factor)
        return svg_elem

    def _affine_solid_geometry(self, matrix, any_iterable=False):
        """
        Applies one affine matrix to the solid geometry: for a multigeo object to the 'solid_geometry' of all the tools
        in ONE vectorized call, otherwise to self.solid_geometry. The container shape is kept.
        """
        if self.multigeo is True:
            keys = list(self.tools)
            results = affine_geometry_many([self.solid_geometry] +
                                           [self.tools[k].get('solid_geometry', []) for k in keys],
                                           matrix, any_iterable)
            self.solid_geometry = results[0]
            for k, res in zip(keys, results[1:]):
                self.tools[k]['solid_geometry'] = res
        else:
            self.solid_geometry = affine_geometry(self.solid_geometry, matrix, any_iterable)

    def mirror(self, axis, point):
        """
        Mirrors the object around a specified axis passign through
        the given point.

        :param axis:    "X" or "Y" indicates around which axis to mirror.
        :type axis:     str
        :param point:   [x, y] point belonging to the mirror axis.
        :type point:    list
        :return:        None
        """
        self.app.log.debug("camlib.Geometry.mirror()")

        px, py = point
        matrix = affine_mirror_matrix(axis, (px, py))

        try:
            Geometry._affine_solid_geometry(self, matrix)
            self.app.inform.emit('[success] %s...' % _('Object was mirrored'))
        except AttributeError:
            self.app.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), _("No object is selected.")))

        self.app.proc_container.new_text = ''

    def rotate(self, angle, point):
        """
        Rotate an object by an angle (in degrees) around the provided coordinates.

        :param angle:
        The angle of rotation are specified in degrees (default). Positive angles are
        counter-clockwise and negative are clockwise rotations.

        :param point:
        The point of origin can be a keyword 'center' for the bounding box
        center (default), 'centroid' for the geometry's centroid, a Point object
        or a coordinate tuple (x0, y0).

        See shapely manual for more information: http://toblerity.org/shapely/manual.html#affine-transformations
        """
        self.app.log.debug("camlib.Geometry.rotate()")

        px, py = point
        matrix = affine_rotate_matrix(angle, (px, py))

        try:
            Geometry._affine_solid_geometry(self, matrix, any_iterable=True)
            self.app.inform.emit('[success] %s...' % _('Object was rotated'))
        except AttributeError:
            self.app.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), _("No object is selected.")))

        self.app.proc_container.new_text = ''

    def skew(self, angle_x, angle_y, point):
        """
        Shear/Skew the geometries of an object by angles along x and y dimensions.

        :param angle_x:
        :param angle_y:
        angle_x, angle_y : float, float
            The shear angle(s) for the x and y axes respectively. These can be
            specified in either degrees (default) or radians by setting
            use_radians=True.

        :param point:   Origin point for Skew
        point: tuple of coordinates (x,y)

        See shapely manual for more information: http://toblerity.org/shapely/manual.html#affine-transformations
        """
        self.app.log.debug("camlib.Geometry.skew()")

        px, py = point
        matrix = affine_skew_matrix(angle_x, angle_y, (px, py))

        try:
            Geometry._affine_solid_geometry(self, matrix, any_iterable=True)
            self.app.inform.emit('[success] %s...' % _('Object was skewed'))
        except AttributeError:
            self.app.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), _("No object is selected.")))

        self.app.proc_container.new_text = ''

    def buffer(
            self,
            distance: float,
            join,
            factor: float,
            only_exterior: bool = False,
            muted: bool = False,
    ):
        """

        :param distance:        if 'factor' is True then distance is the scale factor for each geometric element
        :param join:            The kind of join used by the shapely buffer method: round, mitre or bevel
        :param factor:          True or False (None)
        :param only_exterior:   Bool. If True, the LineStrings are buffered only on the outside
        :param muted:           Bool. If True no messages are created.
        :return:
        """

        self.app.log.debug("camlib.Geometry.buffer()")

        try:
            finite = bool(np.isfinite(distance))
        except (TypeError, ValueError, OverflowError):
            finite = False

        if factor:
            try:
                valid_factor = finite and distance > 0
            except (TypeError, ValueError):
                valid_factor = False
            if not valid_factor:
                if muted is False:
                    self.app.inform.emit('[WARNING_NOTCL] %s: %s' %
                                         (_("Geometry buffer by a factor was not done"),
                                          _("The factor must be a positive number.")))
                return 'fail'
        else:
            if not finite:
                if muted is False:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("The buffer distance is not a number."))
                return 'fail'
            if distance == 0:
                return

        quad_segs = self.circle_quad_segs_for(distance)

        try:
            # build every result first: a failed or empty buffer leaves the object unchanged
            keys = list(self.tools) if self.multigeo is True else []
            new_tools = {}
            if factor:
                if self.app.abort_flag:
                    if muted is False:
                        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                    return 'fail'
                for tool in keys:
                    new_tools[tool] = flatten_shapely_geometry(
                        scale_geometries_by_center(self.tools[tool].get('solid_geometry', []), distance))
                result = scale_geometries_by_center(self.solid_geometry, distance)
            else:
                if self.app.abort_flag:
                    if muted is False:
                        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                    return 'fail'
                # the aggregate and every tool in one vectorized call
                structures = [self.solid_geometry] + [
                    self.tools[k].get('solid_geometry', []) for k in keys]
                results = buffer_geometry_many(structures, distance, quad_segs, join,
                                               only_exterior=only_exterior)
                result = results[0]
                new_tools = {k: flatten_shapely_geometry(r) for k, r in zip(keys, results[1:])}

            if self.app.abort_flag:
                if muted is False:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                return 'fail'
            buff_geo = flatten_shapely_geometry(result)

            if not buff_geo and not any(new_tools.values()):
                if muted is False:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                self.app.log.error("camlib.Geometry.buffer() -> Failed.")
                return 'fail'

            if self.multigeo is True:
                for tool in new_tools:
                    self.tools[tool]['solid_geometry'] = new_tools[tool]
            # a multigeo object may hold its geometry only in its tools; the absent aggregate stays absent
            if buff_geo:
                self.solid_geometry = buff_geo
            if muted is False:
                self.app.inform.emit('[success] %s...' % _('Object was buffered'))
        except AttributeError:
            self.app.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), _("No object is selected.")))
            return 'fail'
        except Exception as err:
            self.app.log.error("camlib.Geometry.buffer() -> %s" % str(err))
            if muted is False:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return 'fail'


class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self


# G-code read back by CNCjob.codes_split(), compiled once: it is called for every line of the G-code
GCODE_ROLAND_Z_RE = re.compile(r"^Z(\s*-?\d+\.\d+?),(\s*\s*-?\d+\.\d+?),(\s*\s*-?\d+\.\d+?)*;$")
GCODE_HPGL_PA_RE = re.compile(r"^PA(\s*-?\d+\.\d+?),(\s*\s*-?\d+\.\d+?)*;$")
GCODE_HPGL_PEN_RE = re.compile(r"^(P[U|D])")
GCODE_HPGL_SP_RE = re.compile(r"^SP\d*")
GCODE_XY_RE = re.compile(r"X([+-]?\d+.[+-]?\d+)\s*Y([+-]?\d+.[+-]?\d+)")
GCODE_LASER_M345_RE = re.compile(r"^(M0?[3-5])")
GCODE_LASER_M10X_RE = re.compile(r"^(M10[6|7])")
# 1 word (letter and number), matched at a position of the line
GCODE_WORD_RE = re.compile(r'\s*([A-Z])\s*([+\-.\d\s]+)')
# the same, in upper or lower case
GCODE_WORD_ANYCASE_RE = re.compile(r'\s*([A-Za-z])\s*([+\-.\d\s]+)')
# a (...) comment of a G-code line
GCODE_PAREN_COMMENT_RE = re.compile(r'\([^)]*\)')


def get_preprocessor(app, name):
    """
    The preprocessor with this name. A name that is not installed (a project from another computer) gives the
    'default' preprocessor and a warning.

    :param app:     the App
    :param name:    the name of the preprocessor
    :return:        the preprocessor object
    """
    preprocessors = app.preprocessors
    if name in preprocessors:
        return preprocessors[name]
    try:
        app.inform.emit('[WARNING_NOTCL] %s: %s. %s' % (
            _("Preprocessor not found"), str(name), _("The 'default' preprocessor is used.")))
    except Exception:
        pass
    return preprocessors['default']


class CNCjob(Geometry):
    """
    Represents work to be done by a CNC machine.

    *ATTRIBUTES*

    * ``gcode_parsed`` (list): Each is a dictionary:

    =====================  =========================================
    Key                    Value
    =====================  =========================================
    geom                   (Shapely.LineString) Tool path (XY plane)
    kind                   (string) "AB", A is "T" (travel) or
                           "C" (cut). B is "F" (fast) or "S" (slow).
    =====================  =========================================
    """

    defaults = {
        "pp_geometry_name": 'default',
        "pp_excellon_name": 'default',
        "excellon_optimization_type": "B",
    }

    multitool: bool

    def __init__(self, app,
                 units="in", kind="generic", tooldia=0.0,
                 z_cut=-0.002, z_move=0.1,
                 feedrate=3.0, feedrate_z=3.0, feedrate_rapid=3.0, feedrate_probe=3.0,
                 pp_geometry_name='default', pp_excellon_name='default',
                 depthpercut=0.1, z_p_depth=-0.02,
                 spindlespeed=None, spindledir='CW', dwell=True, dwelltime=1000,
                 toolchangez=0.787402, toolchange_xy='0.0,0.0',
                 endz=2.0, endxy='',
                 seg_x=None,
                 seg_y=None,
                 steps_per_circle=None):
        if self.__dict__.get('_CNCjob_inited'):
            return

        self.app = app
        self.decimals = self.app.decimals

        # Used when parsing G-code arcs
        self.steps_per_circle = steps_per_circle if steps_per_circle is not None else \
            int(self._app_option('cncjob_steps_per_circle'))

        Geometry.__init__(self, geo_steps_per_circle=self.steps_per_circle, app=app)

        self.kind = kind
        self.units = units

        self.z_cut = z_cut
        self.multidepth = False
        self.z_depthpercut = depthpercut
        self.z_move = z_move

        self.feedrate = feedrate
        self.z_feedrate = feedrate_z
        self.feedrate_rapid = feedrate_rapid

        self.tooldia = tooldia
        self.toolC = tooldia
        self.toolchange = False
        self.z_toolchange = toolchangez
        self.xy_toolchange = toolchange_xy

        self.startz = None
        self.z_end = endz
        self.xy_end = endxy

        self.extracut = False
        self.extracut_length = None

        self.tolerance = self.drawing_tolerance

        # used by the self.tcl_gcode_from_excellon_by_tool() method
        # but set directly before the actual usage of the method with obj.excellon_optimization_type = value
        self.excellon_optimization_type = 'No'

        # if set True then the GCode generation will use UI; used in Excellon Gcode for now
        self.use_ui = False

        self.unitcode = {"IN": "G20", "MM": "G21"}

        self.feedminutecode = "G94"
        # self.absolutecode = "G90"
        # self.incrementalcode = "G91"
        self.coordinates_type = self._app_option("cncjob_coords_type")

        self.gcode = ""
        self.gcode_parsed = None

        self.pp_geometry_name = pp_geometry_name
        self.pp_geometry = get_preprocessor(self.app, self.pp_geometry_name)

        self.pp_excellon_name = pp_excellon_name
        self.pp_excellon = get_preprocessor(self.app, self.pp_excellon_name)

        self.pp_solderpaste_name = None

        # Controls if the move from Z_Toolchange to Z_Move is done fast with G0 or normally with G1
        self.f_plunge = None

        # Controls if the move from Z_Cut to Z_Move is done fast with G0 or G1 until zero and then G0 to Z_move
        self.f_retract = None

        # how much depth the probe can probe before error
        self.z_p_depth = z_p_depth if z_p_depth else None
        # the thickness of the touch plate that lies on the material
        self.probe_plate = 0.0

        # the feedrate(speed) with which the probe travel while probing
        self.feedrate_probe = feedrate_probe if feedrate_probe else None

        self.spindlespeed = spindlespeed
        self.spindledir = spindledir
        self.dwell = dwell
        self.dwelltime = dwelltime
        self.laser_min_power = 0.0
        self.laser_on_code = "M03"

        # Drilling: peck drilling (plain moves) and the dwell at the bottom of each hole
        self.peck = False
        self.peck_depth = 0.0
        self.peck_retract = 0.0
        self.dwell_bottom = False

        # For Autolevelling
        self.seg_x = float(seg_x) if seg_x is not None else 0.0
        self.seg_y = float(seg_y) if seg_y is not None else 0.0
        # tells if the generated Gcode is segmented for autolevelling
        self.is_segmented_gcode = False

        self.input_geometry_bounds = None

        # compensation for CNC bed not square
        self._bed_limit_x = self._app_option("cncjob_bed_max_x")
        self._bed_limit_y = self._app_option("cncjob_bed_max_y")

        self._bed_offset_x = self._app_option("cncjob_bed_offset_x")
        self._bed_offset_y = self._app_option("cncjob_bed_offset_y")
        self._bed_skew_x = self._app_option("cncjob_bed_skew_x")
        self._bed_skew_y = self._app_option("cncjob_bed_skew_y")

        # coordinates used by the preprocessors position_code() method; updated when creating gcode
        self.x = 0.0
        self.y = 0.0

        self.oldx = None
        self.oldy = None

        self.tool = 0.0
        # True while the tool being processed is the first one of the job (by position, not by its number): the
        # preprocessors use the Start Z instead of the Toolchange Z for it
        self.is_first_tool = False

        # the milling travels are routed around the exclusion areas (the Area Exclusion option of the tool)
        self.area_exclusion = True
        # the warning about a cut through an exclusion area is given once per job
        self.exclusion_cut_warned = False

        self.measured_distance = 0.0
        self.measured_down_distance = 0.0
        self.measured_up_to_zero_distance = 0.0
        self.measured_lift_distance = 0.0

        # here store the travelled distance
        self.travel_distance = 0.0
        # here store the routing time
        self.routing_time = 0.0

        # store here the Excellon source object tools to be accessible locally
        self.exc_tools = {}

        # search for toolchange parameters in the Toolchange Custom Code
        self.re_toolchange_custom = re.compile(r'(%[a-zA-Z0-9\-_]+%)')

        # search for toolchange code: M6
        self.re_toolchange = re.compile(r'^\s*(M6)$')

        # Attributes to be included in serialization
        # Always append to it because it carries contents
        # from Geometry.
        self.ser_attrs += ['kind', 'z_cut', 'z_move', 'z_toolchange', 'feedrate', 'z_feedrate', 'feedrate_rapid',
                           'tooldia', 'gcode', 'input_geometry_bounds', 'gcode_parsed', 'steps_per_circle',
                           'z_depthpercut', 'spindlespeed', 'dwell', 'dwelltime', 'travel_distance', 'routing_time']

        self._CNCjob_inited = True

    @property
    def postdata(self):
        """
        This will return all the attributes of the class in the form of a dictionary

        :return:    Class attributes
        :rtype:     dict
        """
        return self.__dict__

    def convert_units(self, units):
        """
        Will convert the parameters in the class that are relevant, from metric to imperial and reverse

        :param units:   FlatCAM units
        :type units:    str
        :return:        conversion factor
        :rtype:         float
        """
        self.app.log.debug("camlib.CNCJob.convert_units()")

        if getattr(self, 'gcode_imported', False):
            factor = self.convert_units_imported(units)
        else:
            factor = Geometry.convert_units(self, units)

        self.z_cut = float(self.z_cut) * factor
        self.z_move *= factor
        self.feedrate *= factor
        self.z_feedrate *= factor
        self.feedrate_rapid *= factor
        self.tooldia *= factor
        self.z_toolchange *= factor
        self.z_end *= factor
        self.z_depthpercut = float(self.z_depthpercut) * factor

        return factor

    def convert_units_imported(self, units):
        """
        Units change of a job made from an imported G-code file: the parsed geometry is scaled, the G-code text of the
        file is never rewritten.

        :param units:   "MM" or "IN"
        :return:        the scaling factor
        """
        if units.upper() == self.units.upper():
            return 1.0
        factor = {"MM": 25.4, "IN": 1 / 25.4}.get(units.upper())
        if factor is None:
            self.app.log.error("Unsupported units: %s" % str(units))
            return 1.0

        self.units = units
        # the same dict can be in the list of the job and in the list of a tool: scale it once
        unique = {}
        for parsed in [self.gcode_parsed] + [tool.get('gcode_parsed') or [] for tool in self.tools.values()]:
            for item in parsed or ():
                unique[id(item)] = item
        self._affine_gcode_parsed(list(unique.values()), affine_scale_matrix(factor, factor, (0, 0)))
        for tool in self.tools.values():
            if tool.get('gcode_parsed'):
                tool['solid_geometry'] = [item['geom'] for item in tool['gcode_parsed']]
        self.create_geometry()
        self.file_units_factor = factor
        return factor

    def count_depth_passes(self):
        """
        The number of passes a multi depth cut makes down to Cut Z. A zero (or missing) step is a single pass.
        """
        depth = abs(float(self.z_cut))
        step = abs(float(self.z_depthpercut)) if self.z_depthpercut else 0.0
        if step <= 0.0:
            return 1 if depth > 0 else 0
        nr_cuts = 0
        while depth > 0:
            nr_cuts += 1
            depth -= step
        return nr_cuts

    def no_usable_geometry_notice(self, tooldia):
        """
        Tell the user that the tool made no G-code because it has no path to cut.
        """
        try:
            dia = '%.*f' % (self.decimals, float(tooldia))
        except (TypeError, ValueError):
            dia = str(tooldia)
        self.app.inform.emit('[ERROR_NOTCL] %s: %s%s.' % (
            _("No usable geometry to cut for the tool with diameter (is the geometry empty or is the tool offset "
              "too large?)"), dia, self.units))

    def warn_cut_through_exclusion(self, paths, tooldia):
        """
        The exclusion areas steer only the travel moves: a cut path that crosses one is cut through. Warn about it,
        once for the whole job.
        """
        if not self.area_exclusion or self.exclusion_cut_warned:
            return
        storage = getattr(getattr(self.app, 'exc_areas', None), 'exclusion_areas_storage', None)
        if not storage:
            return
        try:
            radius = float(tooldia) / 2.0
            areas = unary_union([area['shape'].buffer(radius) for area in storage])
            if any(areas.intersects(path) for path in paths if path is not None):
                self.exclusion_cut_warned = True
                self.app.inform.emit('[WARNING] %s' % _(
                    "A cut path crosses an exclusion area. The exclusion areas change only the travel moves: "
                    "the cut goes through the area."))
        except Exception as e:
            self.app.log.error("camlib.CNCJob.warn_cut_through_exclusion() --> %s" % str(e))

    def milling_travels(self, start_point, end_point, tooldia, travel_z=None):
        """
        The XY route of a travel move of the milling: around the exclusion areas if the tool uses them, else straight.

        :param travel_z:    the travel Z of the move; the tool never goes over an area below it
        :return:            list of [Z or None, (x, y)] as AppExclusionAreas.travel_coordinates()
        """
        if not self.area_exclusion:
            return [(None, (end_point[0], end_point[1]))]
        return self.app.exc_areas.travel_coordinates(start_point=start_point, end_point=end_point, tooldia=tooldia,
                                                     travel_z=travel_z)

    def doformat(self, fun, **kwargs):
        attributes = AttrDict()
        attributes.update(self.postdata)
        attributes.update(kwargs)
        line = self.doformat_attributes(fun, attributes)
        # a preprocessor method with nothing to emit (e.g. dwell with a zero time, the tool change of a laser) returns
        # None or '': no line at all, not an empty line
        return line + "\n" if line else ""

    def doformat2(self, fun, **kwargs):
        """
        This method will call one of the current preprocessor methods having as parameters all the attributes of
        current class to which will add the kwargs parameters

        :param fun:     One of the methods inside the preprocessor classes which get loaded here in the 'p' object
        :type fun:      class 'function'
        :param kwargs:  keyword args which will update attributes of the current class
                        if some parameters are not yet attributes of the class they are added
        :type kwargs:   dict
        :return:        Gcode line
        :rtype:         str
        """
        attributes = AttrDict()
        attributes.update(self.postdata)
        attributes.update(kwargs)
        ret = self.doformat_attributes(fun, attributes)
        return '' if ret is None else ret

    def doformat_attributes(self, fun, attributes):
        """
        Like doformat2() but with the attributes made by the caller: an AttrDict of the attributes of the current class
        updated with the values of the line. For many lines that differ only in a few values (the points of a path),
        the attributes are copied once instead of for every line.

        :param fun:         One of the methods inside the preprocessor classes which get loaded here in the 'p' object
        :type fun:          class 'function'
        :param attributes:  the attributes given to the preprocessor method
        :type attributes:   AttrDict
        :return:            Gcode line
        :rtype:             str
        """
        try:
            ret = fun(attributes)
            return ret
        except Exception as err:
            self.app.log.error('Exception occurred within a preprocessor: ' + traceback.format_exc())
            if getattr(self, 'pp_solderpaste_name', None):
                raise
            # the G-code of this call is missing: tell the user once for each preprocessor method of the job
            method = getattr(fun, '__qualname__', str(fun))
            warned = self.__dict__.setdefault('pp_errors_warned', set())
            if method not in warned:
                warned.add(method)
                try:
                    self.app.inform.emit('[WARNING] %s: %s - %s' % (
                        _("Preprocessor error, G-code is missing"), method, str(err)))
                except Exception:
                    pass
            return ''

    def parse_custom_toolchange_code(self, data):
        """
        Will parse a text and get a toolchange sequence in text format suitable to be included in a Gcode file.
        The '%' symbol is used to surround class variables name and must be removed in the returned string.
        After that, the class variables (attributes) are replaced with the current values. The result is returned.

        :param data:    Toolchange sequence
        :type data:     str
        :return:        Processed toolchange sequence
        :rtype:         str
        """
        text = data
        match_list = self.re_toolchange_custom.findall(text)

        if match_list:
            for match in match_list:
                command = match.strip('%')
                try:
                    value = getattr(self, command)
                except AttributeError:
                    self.app.inform.emit('[ERROR] %s: %s' %
                                         (_("There is no such parameter"), str(match)))
                    self.app.log.debug("CNCJob.parse_custom_toolchange_code() --> AttributeError ")
                    return 'fail'
                text = text.replace(match, str(value))
            return text

    ORTOOLS_DISTANCE_SCALE = RouteSolver.DISTANCE_SCALE
    ORTOOLS_DEFAULT_SEARCH_TIME = RouteSolver.DEFAULT_SEARCH_TIME

    @staticmethod
    def ortools_distance_matrix(locs, scale=RouteSolver.DISTANCE_SCALE):
        return RouteSolver.distance_matrix(locs, scale)

    @staticmethod
    def ortools_set_time_limit(search_parameters, opt_time):
        RouteSolver.set_time_limit(search_parameters, opt_time)

    def _ortools_solve(self, locations, depot, metaheuristic, opt_time):
        """(route, objective) of the OR-Tools search; in the process pool if the app has one."""
        pool = getattr(self.app, 'pool', None)
        if pool is None:
            route, objective = RouteSolver.solve_route(locations, depot, metaheuristic, opt_time)
        else:
            res = pool.apply_async(RouteSolver.solve_route, (locations, depot, metaheuristic, opt_time))
            while not res.ready():
                res.wait(0.1)
                if self.app.abort_flag:
                    # the child ends its search on its own
                    raise grace
            route, objective = res.get()
        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace
        return route, objective

    @staticmethod
    def create_tool_data_array(points):
        # Create the data.
        return [(pt.coords.xy[0][0], pt.coords.xy[1][0]) for pt in points]

    def optimized_ortools_meta(self, locations, start=None, opt_time=0):
        optimized_path = []

        tsp_size = len(locations)
        depot = 0 if start is None else start

        if tsp_size == 0:
            self.app.log.warning('OR-tools metaheuristics - Specify an instance greater than 0.')
            return optimized_path

        route, objective = self._ortools_solve(locations, depot, True, opt_time)
        if route is not None:
            self.app.log.info("OR-tools metaheuristics - Total distance: %s" % str(objective))
            optimized_path = route
        else:
            self.app.log.warning('OR-tools metaheuristics - No solution found.')

        return optimized_path
        # ############################################# ##

    def optimized_ortools_basic(self, locations, start=None, opt_time=0):
        optimized_path = []

        tsp_size = len(locations)
        if tsp_size == 0:
            self.app.log.warning('Specify an instance greater than 0.')
            return []
        depot = 0 if start is None else start

        route, objective = self._ortools_solve(locations, depot, False, opt_time)
        if route is not None:
            self.app.log.info("Total distance: {}".format(objective))
            optimized_path = route
        else:
            self.app.log.warning('No solution found.')

        return optimized_path
        # ############################################# ##

    @staticmethod
    def optimized_travelling_salesman(points, start=None):
        """
        As solving the problem in the brute force way is too slow,
        this function implements a simple heuristic: always
        go to the nearest city.

        Even if this algorithm is extremely simple, it works pretty well
        giving a solution only about 25%% longer than the optimal one (cit. Wikipedia),
        and runs very fast in O(N^2) time complexity.

        optimized_travelling_salesman([[i,j] for i in range(5) for j in range(5)])
        [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4], [1, 4], [1, 3], [1, 2], [1, 1], [1, 0], [2, 0], [2, 1], [2, 2],
        [2, 3], [2, 4], [3, 4], [3, 3], [3, 2], [3, 1], [3, 0], [4, 0], [4, 1], [4, 2], [4, 3], [4, 4]]
        optimized_travelling_salesman([[0,0],[10,0],[6,0]])
        [[0, 0], [6, 0], [10, 0]]

        :param points:  List of tuples with x, y coordinates
        :type points:   list
        :param start:   a tuple with the x,y coordinates of the start point
        :type start:    tuple
        :return:        List of points ordered in an optimized way
        :rtype:         list
        """

        if start is None:
            start = points[0]
        must_visit = list(points)
        path = [start]
        if not must_visit:
            return path

        # the points that are left are kept in the original order (the first nearest point wins a tie)
        xy = np.asarray(must_visit, dtype=np.float64).reshape(-1, 2)
        xs = xy[:, 0]
        ys = xy[:, 1]
        left = np.ones(len(xy), dtype=bool)
        last = start
        for __ in range(len(xy)):
            dist = np.sqrt((last[0] - xs) ** 2 + (last[1] - ys) ** 2)
            dist[~left] = np.inf
            idx = int(np.argmin(dist))
            left[idx] = False
            nearest = must_visit[idx]
            # fixed the Travelling Salesman algorithm such that it does not add duplicated points
            # that are next to each other
            if nearest != last:
                path.append(nearest)
            last = path[-1]
        return path

    def geo_optimized_rtree(self, geometry: BaseGeometry | list[BaseGeometry]):
        """
        The order to cut the paths of the geometry: a nearest neighbour walk from (0, 0) (see appCommon.PathOrder), then
        a pass that shortens the rapid travel. A path can be cut from its first or its last point.

        :return: list of (entry point, path) or 'fail' if there is no path
        """
        self.app.log.debug("Ordering the paths before generating G-Code...")
        self.app.inform.emit(_("Indexing geometry before generating G-Code..."))

        work_geo = geometry.geoms if isinstance(geometry, (MultiPolygon, MultiLineString)) else geometry
        new_shapes = []
        try:
            for geo_shape in work_geo:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                if geo_shape is not None:
                    new_shapes.append(geo_shape)
        except TypeError:
            if work_geo is not None:
                new_shapes.append(work_geo)

        # if there are no locations then go to the next tool
        locations = order_paths(new_shapes, start=(0.0, 0.0))
        if not locations:
            return 'fail'

        return locations

    def drill_order(self, points, opt_type, start=(0.0, 0.0)):
        """
        The order to drill the holes, the same for every optimization type: the exact duplicate holes are dropped and
        the path starts at the hole nearest to `start` (where the machine is).

        :param points:      shapely Points
        :param opt_type:    'M' / 'B' (OR-Tools), 'T' (Travelling Salesman), 'R' (nearest neighbour walk made shorter,
                            see appCommon.PathOrder), anything else: the order of the points
        :param start:       (x, y) of the machine
        :return:            list of (x, y), empty if there are no holes
        """
        self.app.log.debug("Ordering the drills before generating G-Code...")
        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        points = list(points) if points is not None else []
        if not points:
            return []

        try:
            holes = np.empty(len(points), dtype=object)
            holes[:] = points
            xy = shapely.get_coordinates(holes)
        except Exception:
            # a hole without a point is left out
            rows = []
            for geo_shape in points:
                try:
                    rows.append((geo_shape.x, geo_shape.y))
                except Exception:
                    continue
            xy = np.asarray(rows, dtype=np.float64).reshape(-1, 2)
        if not len(xy):
            return []

        # the same hole twice is drilled once; the first one stays where it is in the list
        __, first = np.unique(xy, axis=0, return_index=True)
        first.sort()
        xy = xy[first]

        if not HAS_ORTOOLS and opt_type in ('M', 'B'):
            opt_type = 'R'

        sx, sy = start
        nearest = int(np.argmin((xy[:, 0] - sx) ** 2 + (xy[:, 1] - sy) ** 2))
        locations = [tuple(row) for row in xy.tolist()]

        if opt_type in ('M', 'B'):
            opt_time = self._app_option("excellon_search_time")
            if opt_type == 'M':
                route = self.optimized_ortools_meta(locations=locations, start=nearest, opt_time=opt_time)
            else:
                route = self.optimized_ortools_basic(locations=locations, start=nearest, opt_time=opt_time)
            if route:
                return [locations[i] for i in route]
            opt_type = 'R'

        if opt_type == 'T':
            return self.optimized_travelling_salesman([locations[nearest]] + locations[:nearest] +
                                                      locations[nearest + 1:])
        if opt_type == 'R':
            return [locations[i] for i in order_points(xy, start=(sx, sy))]
        return locations

    def check_zcut(self, zcut):
        if zcut > 0:
            self.app.inform.emit('[WARNING] %s' %
                                 _("The Cut Z parameter has positive value. "
                                   "It is the depth value to drill into material.\n"
                                   "The Cut Z parameter needs to have a negative value, assuming it is a typo "
                                   "therefore the app will convert the value to negative. "
                                   "Check the resulting CNC code (Gcode etc)."))
            return -zcut
        elif zcut == 0:
            self.app.inform.emit('[WARNING] %s.' % _("The Cut Z parameter is zero. There will be no cut, aborting"))

            return 'fail'
        else:
            return zcut

    def check_drill_cutz(self, zcut):
        """
        The drill Cut Z for the job: a positive value is made negative (with a warning), as for the milling. A zero
        Cut Z fails (nothing is cut), except for a laser where Z is not a depth.

        :return: the Cut Z or 'fail'
        """
        try:
            zcut = float(zcut)
        except (TypeError, ValueError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The Cut Z parameter is not a number."))
            return 'fail'
        if zcut == 0 and 'laser' in str(self.pp_excellon_name).lower():
            return zcut
        return self.check_zcut(zcut)

    def laser_min_power_warning(self, gcode):
        """
        Warn when the laser is not turned off between the cuts: with a laser minimum power above zero the 'laser'
        preprocessors write the 'laser OFF' lines with this power (e.g. 'M03 S250 (laser OFF)') instead of 'M5'.

        :param gcode:   the generated GCode
        :type gcode:    str
        :return:        None
        """
        if 'laser OFF' in gcode:
            self.app.inform.emit('[WARNING] %s' %
                                 (_("Laser Min Power is %s: the laser is not turned off between the cuts. "
                                    "The 'laser OFF' lines set this power instead, so the laser can burn along the "
                                    "travel moves and stay on if the job is interrupted. "
                                    "Set Min Power to 0 to turn the laser off between the cuts.") %
                                  str(self.laser_min_power)))

    def calculate_depths(self, step):
        if self.multidepth and abs(self.z_cut) > abs(self.z_depthpercut):
            if self.z_cut > 0:
                top = deepcopy(self.z_cut)
                bottom = 0
            elif self.z_cut < 0:
                top = 0.0
                bottom = deepcopy(self.z_cut)
            else:
                top = 0.0
                bottom = 0.0

            depths = []
            top_val = top
            while top_val > bottom:
                top_val -= step
                if top_val > bottom:
                    depths.append(top_val)
                else:
                    depths.append(bottom)
        else:
            depths = [self.z_cut]

        return depths

    def drill_depths(self, is_laser=False):
        """
        The depths of one hole: the peck steps when the peck drilling is on (it replaces the multidepth), else the
        multidepth steps. Uses self.z_cut, self.peck*, self.multidepth, self.z_depthpercut.

        :return:    (list of depths, True if the list is made of pecks)
        """
        try:
            step = abs(float(self.peck_depth))
        except (TypeError, ValueError):
            step = 0.0
        if self.peck and not is_laser and step > 0 and self.z_cut < 0:
            if self.multidepth:
                self.app.inform.emit('[WARNING] %s' % _("Peck drilling is on: it replaces the multidepth."))
            depths = []
            depth = -step
            while depth > self.z_cut:
                depths.append(depth)
                depth -= step
            depths.append(self.z_cut)
            return depths, len(depths) > 1
        return self.calculate_depths(self.z_depthpercut), False

    def drill_hole_parts(self, p, locx, locy, depths, counters, pecks=False, up_to_zero=True, is_laser=False):
        """
        The G-code lines of drilling one hole at the depths. Used by the Drilling plugin and the Tcl drillcncjob.

        Peck drilling: after each peck the tool goes up to the travel Z (it clears the chips), then it goes down fast
        to the clearance (Peck Retract) above the previous depth and feeds on.
        The dwell at the bottom is done once, at the final depth.

        :param counters:    dict with 'down', 'up_to_zero' and 'lift' that receive the distances
        :return:            list of str
        """
        parts = []
        base_z_move = self.z_move
        last = len(depths) - 1
        for i, depth in enumerate(depths):
            if pecks and i > 0:
                try:
                    clearance = float(self.peck_retract)
                except (TypeError, ValueError):
                    clearance = 0.0
                # never above the travel Z
                self.z_move = min(depths[i - 1] + abs(clearance), base_z_move)
                parts.append(self.doformat(p.lift_code, x=locx, y=locy))
                self.z_move = base_z_move

            self.z_cut = depth
            parts.append(self.doformat(p.down_code, x=locx, y=locy))
            counters['down'] += abs(self.z_cut) + abs(self.z_move)

            if i == last and self.dwell_bottom and not is_laser:
                parts.append(self.doformat(p.dwell_code, x=locx, y=locy))

            if self.f_retract is False and up_to_zero:
                parts.append(self.doformat(p.up_to_zero_code, x=locx, y=locy))
                counters['up_to_zero'] += abs(self.z_cut)
                counters['lift'] += abs(self.z_move)
            else:
                counters['lift'] += abs(self.z_cut) + abs(self.z_move)

            parts.append(self.doformat(p.lift_code, x=locx, y=locy))
        return parts

    def excellon_tool_gcode_gen(self, tool, points, tools, first_pt, is_first=False, is_last=False, opt_type='T',
                                toolchange=False, exclusion_owner=None):
        """
        Used in Tool Drilling

        Creates Gcode for this object from an Excellon object
        for the specified tools.

        exclusion_owner: only the exclusion areas of this owner steer the travels ('drilling' for the Drilling
        plugin: the areas of the Milling plugin do not apply); None = all the areas.



        :return:            A tuple made from tool_gcode,  another tuple holding the coordinates of the last point
                            and the start gcode
        :rtype:             tuple
        """
        self.app.log.debug("camlib.CNCJob.excellon_tool_gcode_gen() -> Generating GCode for tool: %s" % str(tool))

        # detect if GCode is segmented for autolevelling or not
        # it does not matter for the Excellon codes because we are not going to autolevel GCode out of Excellon,
        # but it is here for uniformity between the Geometry and Excellon objects
        if self.seg_x > 0 and self.seg_y > 0 and self.is_segmented_gcode is False:
            self.is_segmented_gcode = True

        # read only here (the parsing of the G-code looks for the drills in it): no copy for each tool
        self.exc_tools = tools
        self.tool = str(tool)
        self.is_first_tool = bool(is_first)

        t_gcode = ''

        # holds the temporary coordinates of the processed drill point
        locx, locy = first_pt

        # #############################################################################################################
        # #############################################################################################################
        # ##################################   DRILLING !!!   #########################################################
        # #############################################################################################################
        # #############################################################################################################
        if not HAS_ORTOOLS:
            if opt_type in ['M', 'B']:
                opt_type = 'R'

        if opt_type == 'M':
            self.app.log.debug("Using OR-Tools Metaheuristic Guided Local Search drill path optimization.")
        elif opt_type == 'B':
            self.app.log.debug("Using OR-Tools Basic drill path optimization.")
        elif opt_type == 'T':
            self.app.log.debug("Using Travelling Salesman drill path optimization.")
        elif opt_type == 'R':
            self.app.log.debug("Using RTree path optimization.")
        else:
            self.app.log.debug("Using no path optimization.")

        tool_dict = tools[tool]['data']
        # check if it has drills
        if not points:
            self.app.log.debug("Failed. No drills for tool: %s" % str(tool))
            return 'fail', None, ''

        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        # #########################################################################################################
        # #########################################################################################################
        # ############# PARAMETERS used in PREPROCESSORS so they need to be updated ###############################
        # #########################################################################################################
        # #########################################################################################################
        # Preprocessor
        p = self.pp_excellon

        # Depth parameters
        old_zcut = self.check_drill_cutz(tool_dict['tools_drill_cutz'])      # multidepth use this
        if old_zcut == 'fail':
            return 'fail', None, ''
        self.z_cut = old_zcut
        self.multidepth = tool_dict['tools_drill_multidepth']
        self.z_depthpercut = tool_dict['tools_drill_depthperpass']
        self.z_move = tool_dict['tools_drill_travelz']
        base_z_move = self.z_move
        self.f_plunge = tool_dict["tools_drill_f_plunge"]       # used directly in the preprocessor Toolchange method
        self.f_retract = tool_dict["tools_drill_f_retract"]     # used in the current method

        # Feedrate parameters
        self.z_feedrate = tool_dict['tools_drill_feedrate_z']
        self.feedrate = tool_dict['tools_drill_feedrate_z']
        self.feedrate_rapid = tool_dict['tools_drill_feedrate_rapid']

        # Spindle parameters
        self.spindlespeed = tool_dict['tools_drill_spindlespeed']
        self.dwell = tool_dict['tools_drill_dwell']
        self.dwelltime = tool_dict['tools_drill_dwelltime']
        self.spindledir = tool_dict['tools_drill_spindledir']
        self.peck = bool(tool_dict.get('tools_drill_peck', False))
        self.peck_depth = tool_dict.get('tools_drill_peck_depth', 0.0)
        self.peck_retract = tool_dict.get('tools_drill_peck_retract', 0.0)
        self.dwell_bottom = bool(tool_dict.get('tools_drill_dwell_bottom', False))
        is_laser = 'laser' in self.pp_excellon_name.lower()

        self.laser_min_power = tool_dict['tools_drill_min_power']
        self.laser_on_code = tool_dict['tools_drill_laser_on']

        self.tooldia = tools[tool]["tooldia"]
        self.postdata['toolC'] = tools[tool]["tooldia"]
        self.toolchange = toolchange
        # the travels go around the exclusion areas only if the tool uses them
        self.area_exclusion = bool(tool_dict.get('tools_drill_area_exclusion', True))

        # Z_toolchange parameter
        self.z_toolchange = tool_dict['tools_drill_toolchangez']
        # XY_toolchange parameter
        self.xy_toolchange = tool_dict["tools_drill_toolchangexy"]

        try:
            if self.xy_toolchange == '':
                self.xy_toolchange = None
            else:
                # either originally it was a string or not, xy_toolchange will be made string
                self.xy_toolchange = re.sub(r'[()\[\]]', '', str(self.xy_toolchange)) if self.xy_toolchange else None

                # and now, xy_toolchange is made into a list of floats in format [x, y]
                if self.xy_toolchange:
                    self.xy_toolchange = [float(safe_eval(a)) for a in self.xy_toolchange.split(",")]

                if self.xy_toolchange and len(self.xy_toolchange) != 2:
                    self.app.inform.emit('[ERROR] %s' % _("The Toolchange X,Y format has to be (x, y)."))
                    return 'fail', None, ''
        except Exception as e:
            self.app.log.error("camlib.CNCJob.tcl_gcode_from_excellon_by_tool() xy_toolchange --> %s" % str(e))
            self.xy_toolchange = [0, 0]

        # the machine is at the toolchange position after the toolchange: the travel starts from there
        if toolchange and self.xy_toolchange:
            temp_locx, temp_locy = self.xy_toolchange
        else:
            temp_locx, temp_locy = first_pt

        # End position parameters
        self.startz = tool_dict["tools_drill_startz"]
        if self.startz == '':
            self.startz = None
        self.z_end = tool_dict["tools_drill_endz"]
        self.xy_end = tool_dict["tools_drill_endxy"]

        try:
            if self.xy_end == '':
                self.xy_end = None
            else:
                # either originally it was a string or not, xy_end will be made string
                self.xy_end = re.sub(r'[()\[\]]', '', str(self.xy_end)) if self.xy_end else None

                # and now, xy_end is made into a list of floats in format [x, y]
                if self.xy_end:
                    self.xy_end = [float(safe_eval(a)) for a in self.xy_end.split(",")]

                if self.xy_end and len(self.xy_end) != 2:
                    self.app.inform.emit('[ERROR] %s' % _("The End X,Y format has to be (x, y)."))
                    return 'fail', None, ''
        except Exception as e:
            self.app.log.error("camlib.CNCJob.tcl_gcode_from_excellon_by_tool() xy_end --> %s" % str(e))
            self.xy_end = [0, 0]

        # Probe parameters
        self.z_p_depth = tool_dict["tools_drill_z_p_depth"]
        self.probe_plate = tool_dict.get("tools_drill_probe_plate", 0.0)
        self.feedrate_probe = tool_dict["tools_drill_feedrate_probe"]
        # #########################################################################################################
        # #########################################################################################################

        # #########################################################################################################
        # ############ Create the data. ###########################################################################
        # #########################################################################################################
        optimized_path = self.drill_order(points, opt_type, start=(temp_locx, temp_locy))
        # #########################################################################################################
        # #########################################################################################################

        # Only if there are locations to drill
        if not optimized_path:
            self.app.log.error("CNCJob.excellon_tool_gcode_gen() -> Optimized path is empty.")
            return 'fail', None, ''

        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        start_gcode = ''
        if is_first:
            start_gcode = self.doformat(p.start_code)
            # t_gcode += start_gcode

        # do the ToolChange event
        t_gcode += self.doformat(p.z_feedrate_code)
        if toolchange:
            t_gcode += self.doformat(p.toolchange_code, toolchangexy=(temp_locx, temp_locy))
            t_gcode += self.doformat(p.z_feedrate_code)
        else:
            if self.startz is None or 'laser' in self.pp_excellon_name.lower():
                t_gcode += self.doformat(p.lift_code)
            t_gcode += self.doformat(p.startz_code)

        if 'laser' not in self.pp_excellon_name.lower():
            # Spindle start
            t_gcode += self.doformat(p.spindle_code)
            # Dwell time
            if self.dwell is True:
                t_gcode += self.doformat(p.dwell_code)
        elif toolchange:
            # for laser the spindle code turns the laser ON: keep it OFF for the travel to the first hole, the down
            # code turns it ON at each hole (without toolchange the lift code above already turned it OFF)
            t_gcode += self.doformat(p.lift_code, x=temp_locx, y=temp_locy)

        current_tooldia = self.app.dec_format(float(tools[tool]["tooldia"]), self.decimals)
        self.app.inform.emit(
            '%s: %s%s.' % (_("Starting G-Code for tool with diameter"), str(current_tooldia), str(self.units))
        )

        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # APPLY Offset only when using the appGUI, for TclCommand this will create an error
        # because the values for Z offset are created in build_tool_ui()
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        try:
            z_offset = float(tool_dict['tools_drill_offset']) * (-1)
        except KeyError:
            z_offset = 0
        self.z_cut = z_offset + old_zcut

        depths_list, use_pecks = self.drill_depths(is_laser)

        self.coordinates_type = self._app_option("cncjob_coords_type")
        if self.coordinates_type == "G90":
            # Drilling! for Absolute coordinates type G90
            # variables to display the percentage of work done
            geo_len = len(optimized_path)

            old_disp_number = 0
            self.app.log.warning("Number of drills for which to generate GCode: %s" % str(geo_len))

            loc_nr = 0
            # the chunks are joined 1 time: a str += in a loop copies the whole string each time
            drill_parts = []
            for point in optimized_path:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                locx, locy = point

                if self.area_exclusion:
                    travels = self.app.exc_areas.travel_coordinates(start_point=(temp_locx, temp_locy),
                                                                    end_point=(locx, locy),
                                                                    tooldia=current_tooldia, owner=exclusion_owner,
                                                                    travel_z=base_z_move)
                else:
                    travels = [(None, (locx, locy))]
                prev_z = None
                for travel in travels:
                    locx = travel[1][0]
                    locy = travel[1][1]

                    if travel[0] is not None:
                        # move to next point
                        drill_parts.append(self.doformat(p.rapid_code, x=locx, y=locy))

                        # raise to safe Z (travel[0]) each time because safe Z may be different
                        self.z_move = travel[0]
                        drill_parts.append(self.doformat(p.lift_code, x=locx, y=locy))

                        # restore z_move
                        self.z_move = base_z_move
                    else:
                        if prev_z is not None:
                            # move to next point
                            drill_parts.append(self.doformat(p.rapid_code, x=locx, y=locy))

                            # we assume that previously the z_move was altered therefore raise to
                            # the travel_z (z_move)
                            self.z_move = base_z_move
                            drill_parts.append(self.doformat(p.lift_code, x=locx, y=locy))
                        else:
                            # move to next point
                            drill_parts.append(self.doformat(p.rapid_code, x=locx, y=locy))

                    # store prev_z
                    prev_z = travel[0]

                # t_gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                # test if the self.z_cut >= 0, in that case we do not use the up_to_zero feature
                cancel_up2zero = False
                if self.z_cut >= 0:
                    cancel_up2zero = True

                hole_counters = {'down': 0.0, 'up_to_zero': 0.0, 'lift': 0.0}
                drill_parts += self.drill_hole_parts(p, locx, locy, depths_list, hole_counters, pecks=use_pecks,
                                                     up_to_zero=not cancel_up2zero, is_laser=is_laser)
                self.measured_down_distance += hole_counters['down']
                self.measured_up_to_zero_distance += hole_counters['up_to_zero']
                self.measured_lift_distance += hole_counters['lift']

                # if self.multidepth and abs(self.z_cut) > abs(self.z_depthpercut):
                #     doc = deepcopy(self.z_cut)
                #     self.z_cut = 0.0
                #
                #     while abs(self.z_cut) < abs(doc):
                #
                #         self.z_cut -= self.z_depthpercut
                #         if abs(doc) < abs(self.z_cut) < (abs(doc) + self.z_depthpercut):
                #             self.z_cut = doc
                #         # Move down the drill bit
                #         t_gcode += self.doformat(p.down_code, x=locx, y=locy)
                #
                #         # Update the distance travelled down with the current one
                #         self.measured_down_distance += abs(self.z_cut) + abs(self.z_move)
                #
                #         if self.f_retract is False:
                #             t_gcode += self.doformat(p.up_to_zero_code, x=locx, y=locy)
                #             self.measured_up_to_zero_distance += abs(self.z_cut)
                #             self.measured_lift_distance += abs(self.z_move)
                #         else:
                #             self.measured_lift_distance += abs(self.z_cut) + abs(self.z_move)
                #
                #         t_gcode += self.doformat(p.lift_code, x=locx, y=locy)
                # else:
                #     t_gcode += self.doformat(p.down_code, x=locx, y=locy)
                #
                #     self.measured_down_distance += abs(self.z_cut) + abs(self.z_move)
                #
                #     if self.f_retract is False:
                #         t_gcode += self.doformat(p.up_to_zero_code, x=locx, y=locy)
                #         self.measured_up_to_zero_distance += abs(self.z_cut)
                #         self.measured_lift_distance += abs(self.z_move)
                #     else:
                #         self.measured_lift_distance += abs(self.z_cut) + abs(self.z_move)
                #
                #     t_gcode += self.doformat(p.lift_code, x=locx, y=locy)

                self.measured_distance += abs(distance_euclidian(locx, locy, temp_locx, temp_locy))
                temp_locx = locx
                temp_locy = locy
                self.oldx = locx
                self.oldy = locy

                loc_nr += 1
                disp_number = int(np.interp(loc_nr, [0, geo_len], [0, 100]))

                if old_disp_number < disp_number <= 100:
                    self.app.proc_container.update_view_text(' %d%%' % disp_number)
                    old_disp_number = disp_number
            t_gcode += ''.join(drill_parts)
            self.z_move = base_z_move
        else:
            self.app.inform.emit('[ERROR_NOTCL] %s...' % _('G91 coordinates not implemented'))
            return 'fail', None, ''
        self.z_cut = deepcopy(old_zcut)

        if is_last:
            t_gcode += self.doformat(p.spindle_stop_code)
            # Move to End position
            t_gcode += self.doformat(p.end_code, x=0, y=0)

        self.app.inform.emit('%s %s' % (_("Finished G-Code generation for tool:"), str(tool)))
        self.laser_min_power_warning(t_gcode)

        return t_gcode, (locx, locy), start_gcode

    # used in Geometry (and in Tool Milling)
    def geometry_tool_gcode_gen(self, tool, tools, first_pt, last_pt, tolerance, is_first=False, is_last=False,
                                toolchange=False, use_ui=True):
        """
        Algorithm to generate GCode from multitool Geometry.

        :param tool:        tool number for which to generate GCode
        :type tool:         int
        :param tools:       a dictionary holding all the tools and data
        :type tools:        dict
        :param first_pt:    a tuple of coordinates for the first point of the current tool
        :type first_pt:     tuple
        :param last_pt:     a tuple of coordinates for the last point of the current tool
                            (which is the same for all tools)
        :type last_pt:      tuple
        :param tolerance:   geometry tolerance
        :type tolerance:
        :param is_first:    if the current tool is the first tool (for this we need to add start GCode)
        :type is_first:     bool
        :param is_last:     if the current tool is the last tool (for this we need to add the end GCode)
        :type is_last:      bool
        :param toolchange:  add toolchange event
        :type toolchange:   bool
        :param use_ui:      if the method is called from the GUI
        :type use_ui:       bool
        :return:            GCode
        :rtype:             str
        """

        self.app.log.debug("camlib.CNCJob.geometry_tool_gcode_gen() -> Generating GCode for tool: %s" % str(tool))

        t_gcode = ''
        temp_solid_geometry = []

        # The Geometry from which we create GCode
        geometry = tools[tool]['solid_geometry']

        # #########################################################################################################
        # ############# PARAMETERS used in PREPROCESSORS so they need to be updated ###############################
        # #########################################################################################################
        self.tool = str(tool)
        self.is_first_tool = bool(is_first)
        tool_dict = tools[tool]['data']
        self.area_exclusion = bool(tool_dict.get('tools_mill_area_exclusion', True))
        # this is the tool diameter, it is used as such to accommodate the preprocessor who need the tool diameter
        # given under the name 'toolC'
        self.postdata['toolC'] = float(tool_dict['tools_mill_tooldia'])
        self.tooldia = float(tool_dict['tools_mill_tooldia'])
        self.use_ui = use_ui
        self.tolerance = tolerance

        # Optimization type. Can be: 'M', 'B', 'T', 'R', 'No'
        opt_type = tool_dict['tools_mill_optimization_type']
        if not HAS_ORTOOLS:
            opt_type = 'R'

        opt_time = tool_dict['tools_mill_search_time'] if 'tools_mill_search_time' in tool_dict else 1.0

        if opt_type == 'M':
            self.app.log.debug("Using OR-Tools Metaheuristic Guided Local Search path optimization.")
        elif opt_type == 'B':
            self.app.log.debug("Using OR-Tools Basic path optimization.")
        elif opt_type == 'T':
            self.app.log.debug("Using Travelling Salesman path optimization.")
        elif opt_type == 'R':
            self.app.log.debug("Using RTree path optimization.")
        else:
            self.app.log.debug("Using no path optimization.")

        # Preprocessor
        self.pp_geometry_name = tool_dict['tools_mill_ppname_g']
        self.pp_geometry = get_preprocessor(self.app, self.pp_geometry_name)
        p = self.pp_geometry

        # Offset the Geometry if it is the case
        # if tool_dict['tools_mill_offset_type'] == 1:  # 'in'
        #     tool_offset = -float(tool_dict['tools_mill_tooldia']) / 2.0
        # elif tool_dict['tools_mill_offset_type'] == 2: # 'out'
        #     tool_offset = float(tool_dict['tools_mill_tooldia']) / 2.0
        # elif tool_dict['tools_mill_offset_type'] == 3: # 'custom'
        #     tool_offset = tool_dict['tools_mill_offset_value']
        # else:
        #     tool_offset = 0.0
        tool_offset = tool_dict['tools_mill_offset_value']

        # #############################################################################################################
        # ## Flatten the geometry. Only linear elements (no polygons) remain.
        # #############################################################################################################
        flat_ext_geo, flat_ints_geo = self.flatten_exterior_interiors(geometry)
        flat_geometry = flat_ext_geo + flat_ints_geo
        # flat_geometry = self.flatten(geometry, reset=True, pathonly=True)
        self.app.log.debug("%d paths" % len(flat_geometry))

        if tool_offset != 0.0:
            # for it in flat_geometry:
            #     # if the geometry is a closed shape then create a Polygon out of it
            #     if isinstance(it, LineString):
            #         if it.is_ring:
            #             it = Polygon(it)
            #     temp_solid_geometry.append(it.buffer(tool_offset, join_style=2))
            for it in flat_ext_geo:
                # if the geometry is a closed shape then create a Polygon out of it
                if isinstance(it, LineString):
                    if it.is_ring:
                        it = Polygon(it)
                temp_solid_geometry.append(it.buffer(tool_offset, join_style="mitre"))

            for it in flat_ints_geo:
                # if the geometry is a closed shape then create a Polygon out of it
                if isinstance(it, (LineString, LinearRing)):
                    if it.is_ring:
                        it = Polygon(it)
                temp_solid_geometry.append(it.buffer(-tool_offset, join_style="mitre"))

            temp_solid_geometry = self.flatten(temp_solid_geometry, reset=True, pathonly=True)
        else:
            temp_solid_geometry = flat_geometry

        temp_solid_geometry = [t_geo for t_geo in temp_solid_geometry if not t_geo.is_empty]
        if not temp_solid_geometry:
            self.no_usable_geometry_notice(self.tooldia)
            return 'fail'

        # Depth parameters for the current tool must be loaded before validation.
        self.z_cut = float(tool_dict['tools_mill_cutz']) if tool_dict['tools_mill_cutz'] is not None else None
        # a laser cuts at one height: multi-depth does not apply (old data can still have it on)
        self.multidepth = tool_dict['tools_mill_multidepth'] and 'laser' not in str(self.pp_geometry_name).lower()
        self.z_depthpercut = float(tool_dict['tools_mill_depthperpass'])
        self.z_move = float(tool_dict['tools_mill_travelz']) if tool_dict['tools_mill_travelz'] is not None else None

        if self.z_cut is None:
            if 'laser' not in self.pp_geometry_name:
                self.app.inform.emit(
                    '[ERROR_NOTCL] %s' % _("Cut_Z parameter is None or zero. Most likely a bad combinations of "
                                           "other parameters."))
                return 'fail'
            else:
                self.z_cut = 0

        if self.z_cut > 0:
            self.app.inform.emit('[WARNING] %s' %
                                 _("The Cut Z parameter has positive value. "
                                   "It is the depth value to cut into material.\n"
                                   "The Cut Z parameter needs to have a negative value, assuming it is a typo "
                                   "therefore the app will convert the value to negative."
                                   "Check the resulting CNC code (Gcode etc)."))
            self.z_cut = -self.z_cut
        elif self.z_cut == 0 and 'laser' not in self.pp_geometry_name:
            self.app.inform.emit('[WARNING] %s: %s' %
                                 (_("The Cut Z parameter is zero. There will be no cut, skipping file"),
                                  self.obj_options['name']))
            return 'fail'

        if self.z_move is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Travel Z parameter is None or zero."))
            return 'fail'

        if self.z_move < 0:
            self.app.inform.emit('[WARNING] %s' %
                                 _("The Travel Z parameter has negative value. "
                                   "It is the height value to travel between cuts.\n"
                                   "The Z Travel parameter needs to have a positive value, assuming it is a typo "
                                   "therefore the app will convert the value to positive."
                                   "Check the resulting CNC code (Gcode etc)."))
            self.z_move = -self.z_move
        elif self.z_move == 0:
            self.app.inform.emit('[WARNING] %s: %s' %
                                 (_("The Z Travel parameter is zero. This is dangerous, skipping file"),
                                  self.obj_options['name']))
            return 'fail'

        # made sure that depth_per_cut is no more than the z_cut
        if abs(self.z_cut) < self.z_depthpercut:
            self.z_depthpercut = abs(self.z_cut)
        # a zero step would never reach the Cut Z: cut it in one pass
        if not self.z_depthpercut:
            self.z_depthpercut = abs(self.z_cut)

        self.f_plunge = self._app_option("tools_mill_f_plunge")

        self.feedrate = float(tool_dict['tools_mill_feedrate'])
        self.z_feedrate = float(tool_dict['tools_mill_feedrate_z'])
        self.feedrate_rapid = float(tool_dict['tools_mill_feedrate_rapid'])

        # this should not happen except for older projects
        try:
            self.laser_min_power = float(tool_dict['tools_mill_min_power'])
        except KeyError:
            self.laser_min_power = 0.0

        self.laser_on_code = tool_dict['tools_mill_laser_on']

        try:
            self.spindlespeed = float(tool_dict['tools_mill_spindlespeed'])
        except TypeError:
            self.spindlespeed = 0.0

        try:
            self.spindledir = tool_dict['tools_mill_spindledir']
        except KeyError:
            self.spindledir = self._app_option("tools_mill_spindledir")

        try:
            self.spindledir = tool_dict['tools_mill_spindledir']
        except KeyError:
            self.spindledir = self._app_option("tools_mill_spindledir")

        self.dwell = tool_dict['tools_mill_dwell']
        self.dwelltime = float(tool_dict['tools_mill_dwelltime'])

        self.startz = float(tool_dict['tools_mill_startz']) if tool_dict['tools_mill_startz'] else None
        if self.startz == '':
            self.startz = None

        self.z_end = float(tool_dict['tools_mill_endz'])
        self.xy_end = last_pt

        try:
            if self.xy_end == '' or self.xy_end is None:
                self.xy_end = None
            else:
                # either originally it was a string or not, xy_end will be made string
                self.xy_end = re.sub(r'[()\[\]]', '', str(self.xy_end)) if self.xy_end else None

                # and now, xy_end is made into a list of floats in format [x, y]
                if self.xy_end:
                    self.xy_end = [float(safe_eval(a)) for a in self.xy_end.split(",")]

                if self.xy_end and len(self.xy_end) != 2:
                    self.app.inform.emit('[ERROR] %s' % _("The End X,Y format has to be (x, y)."))
                    return 'fail'
        except Exception as e:
            self.app.log.error("camlib.CNCJob.geometry_tool_gcode_gen xy_end --> %s" % str(e))
            self.xy_end = [0, 0]

        self.z_toolchange = tool_dict['tools_mill_toolchangez']
        self.xy_toolchange = tool_dict["tools_mill_toolchangexy"]
        try:
            if self.xy_toolchange == '':
                self.xy_toolchange = None
            else:
                # either originally it was a string or not, xy_toolchange will be made string
                self.xy_toolchange = re.sub(r'[()\[\]]', '', str(self.xy_toolchange)) if self.xy_toolchange else None

                # and now, xy_toolchange is made into a list of floats in format [x, y]
                if self.xy_toolchange:
                    self.xy_toolchange = [float(safe_eval(a)) for a in self.xy_toolchange.split(",")]

                if self.xy_toolchange and len(self.xy_toolchange) != 2:
                    self.app.inform.emit('[ERROR] %s' % _("The Toolchange X,Y format has to be (x, y)."))
                    return 'fail'
        except Exception as e:
            self.app.log.error("camlib.CNCJob.geometry_from_excellon_by_tool() --> %s" % str(e))
            pass

        self.extracut = tool_dict['tools_mill_extracut']
        self.extracut_length = tool_dict['tools_mill_extracut_length']

        # Probe parameters
        # self.z_p_depth = tool_dict["tools_drill_z_p_depth"]
        # self.feedrate_probe = tool_dict["tools_drill_feedrate_probe"]

        # #########################################################################################################
        # ############ Create the data. ###########################################################################
        # #########################################################################################################
        optimized_path = []

        geo_storage = {}
        for geo in temp_solid_geometry:
            if geo is None:
                continue
            if isinstance(geo, (LineString, LinearRing)):
                try:
                    geo_storage.setdefault(geo.coords[0], []).append(geo)
                except Exception:
                    pass
            elif isinstance(geo, (MultiPolygon, MultiLineString)):
                for g in geo.geoms:
                    try:
                        geo_storage.setdefault(g.coords[0], []).append(g)
                    except Exception:
                        pass

        # several paths can start at the same point: they are all cut, one after the other
        locations = list(geo_storage.keys())

        if 'ncc_cleanup_start' in tools[tool] or 'paint_cleanup_start' in tools[tool]:
            # Final cleanup depends on the standard paths running first. Preserve
            # the validated sequence (including equal start points) and its geometry.
            optimized_path = [(geo.coords[0], geo) for geo in temp_solid_geometry]
            self.tolerance = 0.0
        elif opt_type == 'M':
            # if there are no locations then go to the next tool
            if not locations:
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'

            optimized_locations = self.optimized_ortools_meta(locations=locations, opt_time=opt_time)
            optimized_path = [(locations[loc], g) for loc in optimized_locations for g in geo_storage[locations[loc]]]
        elif opt_type == 'B':
            # if there are no locations then go to the next tool
            if not locations:
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'
            optimized_locations = self.optimized_ortools_basic(locations=locations, opt_time=opt_time)
            optimized_path = [(locations[loc], g) for loc in optimized_locations for g in geo_storage[locations[loc]]]
        elif opt_type == 'T':
            # if there are no locations then go to the next tool
            if not locations:
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'
            optimized_locations = self.optimized_travelling_salesman(locations)
            optimized_path = [(loc, g) for loc in optimized_locations for g in geo_storage[loc]]
        elif opt_type == 'R':
            optimized_path = self.geo_optimized_rtree(temp_solid_geometry)
            if optimized_path == 'fail':
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'
        elif opt_type == 'N':
            optimized_path = [(k, g) for k, v in geo_storage.items() for g in v]
            if not optimized_path:
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'
        else:
            # it's actually not optimized path but here we build a list of (x,y) coordinates
            # out of the tool
            for geo in temp_solid_geometry:
                optimized_path.append(geo.coords[0])
        # #########################################################################################################
        # #########################################################################################################

        # Only if there are locations to mill
        if not optimized_path:
            self.app.log.debug("camlib.CNCJob.geometry_tool_gcode_gen() -> Optimized path is empty.")
            self.no_usable_geometry_notice(self.tooldia)
            return 'fail'

        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        # #############################################################################################################
        # #############################################################################################################
        # ################# MILLING !!! ##############################################################################
        # #############################################################################################################
        # #############################################################################################################
        self.app.log.debug("Starting G-Code...")

        current_tooldia = float('%.*f' % (self.decimals, float(self.tooldia)))
        msg = '%s: %s%s.' % (_("Starting G-Code for tool with diameter"), str(current_tooldia), str(self.units))
        self.app.log.debug(msg)
        self.app.inform.emit(msg)
        if not self.multidepth:
            self.app.log.debug("camlib.CNCJob.geometry_tool_gcode_gen() -> Multidepth OFF -> Single Pass")
        else:
            self.app.log.debug("camlib.CNCJob.geometry_tool_gcode_gen() -> Multidepth ON -> Multiple Pass")

        # Measurements
        total_travel = 0.0
        total_cut = 0.0

        # Start GCode
        start_gcode = ''
        if is_first:
            start_gcode = self.doformat(p.start_code)
            # t_gcode += start_gcode

        # ToolChange code
        t_gcode += self.doformat(p.feedrate_code)  # sets the feed rate
        if toolchange:
            t_gcode += self.doformat(p.toolchange_code)
        else:
            if self.startz is None or 'laser' in self.pp_geometry_name.lower():
                t_gcode += self.doformat(p.lift_code, x=0, y=0)
            t_gcode += self.doformat(p.startz_code, x=0, y=0)

        # Spindle start
        if 'laser' not in self.pp_geometry_name.lower():
            t_gcode += self.doformat(p.spindle_code)
        else:
            # for laser this will disable the laser
            t_gcode += self.doformat(p.lift_code, x=self.oldx, y=self.oldy)  # Move (up) to travel height
        # Dwell time
        if self.dwell:
            t_gcode += self.doformat(p.dwell_code)

        # Feed rate set
        t_gcode += self.doformat(p.feedrate_code)

        # Iterate over geometry paths getting the nearest each time.
        path_count = 0

        # variables to display the percentage of work done
        geo_len = len(flat_geometry)
        self.app.log.warning("Number of paths for which to generate GCode: %s" % str(geo_len))
        old_disp_number = 0

        oriented_path = []
        for pt, geo in optimized_path:
            if self.app.abort_flag:
                raise grace
            # If last point in geometry is the nearest but prefer the first one if last point == first point
            # then reverse coordinates.
            if pt != geo.coords[0] and pt == geo.coords[-1]:
                geo = LineString(list(geo.coords)[::-1])
            oriented_path.append((pt, geo))

        # Single depth without extra cut: the oriented paths are simplified at once, on several threads, instead of
        # one by one in linear2gcode() (a large part of the time); the same paths. Multi depth simplifies each depth
        # in its own direction and the extra cut looks at the path before the simplification.
        simplified = None
        if not self.multidepth and self.extracut is False and self.tolerance > 0:
            simplified = simplify_paths([geo for __, geo in oriented_path], self.tolerance, app=self.app)

        self.warn_cut_through_exclusion([geo for __, geo in oriented_path], current_tooldia)

        # the G-code of the paths is joined at the end: added to the growing string, it was copied again and again
        gcode_parts = [t_gcode]
        current_pt = first_pt
        for path_idx, (pt, geo) in enumerate(oriented_path):
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            path_count += 1

            # ---------- Single depth/pass --------
            if not self.multidepth:
                # calculate the cut distance
                total_cut = total_cut + geo.length

                if simplified is None:
                    gcode_parts.append(self.create_gcode_single_pass(geo, current_tooldia, self.extracut,
                                                                     self.extracut_length, self.tolerance,
                                                                     z_move=self.z_move, old_point=current_pt))
                else:
                    gcode_parts.append(self.create_gcode_single_pass(simplified[path_idx], current_tooldia,
                                                                     self.extracut, self.extracut_length, 0,
                                                                     z_move=self.z_move, old_point=current_pt))

            # --------- Multi-pass ---------
            else:
                # calculate the cut distance
                # due of the number of cuts (multi depth) it has to multiplied by the number of cuts
                nr_cuts = self.count_depth_passes()

                total_cut += (geo.length * nr_cuts)

                gc, geo = self.create_gcode_multi_pass(geo, current_tooldia, self.extracut,
                                                       self.extracut_length, self.tolerance,
                                                       z_move=self.z_move, postproc=p, old_point=current_pt)
                gcode_parts.append(gc)

            # calculate the total distance
            total_travel = total_travel + abs(distance(pt1=current_pt, pt2=pt))
            current_pt = geo.coords[-1]

            disp_number = int(np.interp(path_count, [0, geo_len], [0, 100]))
            if old_disp_number < disp_number <= 100:
                self.app.proc_container.update_view_text(' %d%%' % disp_number)
                old_disp_number = disp_number

        t_gcode = ''.join(gcode_parts)
        self.app.log.debug("Finished G-Code... %s paths traced." % path_count)

        # add move to end position
        total_travel += abs(distance_euclidian(current_pt[0], current_pt[1], 0, 0))
        self.travel_distance += total_travel + total_cut
        self.routing_time += total_cut / self.feedrate

        # Finish
        if is_last:
            if 'laser' not in self.pp_geometry_name.lower():
                t_gcode += self.doformat(p.spindle_stop_code)
                t_gcode += self.doformat(p.lift_code, x=current_pt[0], y=current_pt[1])
            else:
                t_gcode += self.doformat(p.lift_code, x=current_pt[0], y=current_pt[1])
                t_gcode += self.doformat(p.spindle_stop_code)

            if isinstance(self.xy_end, (tuple, list)):
                endx = self.xy_end[0]
                endy = self.xy_end[1]
            else:
                try:
                    endx = current_pt[0]
                    endy = current_pt[1]
                except Exception:
                    endx = 0.0
                    endy = 0.0

            t_gcode += self.doformat(p.end_code, x=endx, y=endy)
            self.app.inform.emit(
                '%s... %s %s.' % (_("Finished G-Code generation"), str(path_count), _("paths traced"))
            )

        self.gcode = t_gcode
        self.laser_min_power_warning(self.gcode)
        return self.gcode, start_gcode

    def tcl_gcode_from_excellon_by_tool(self, exobj, tools="all", order='fwd', is_first=False, overrides=None,
                                        holes_area=None):
        """
        !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        Used by the Tcl command Drillcncjob
        !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

        Creates Gcode for this object from an Excellon object
        for the specified tools.

        :param exobj:       Excellon object to process
        :type exobj:        Excellon
        :param tools:       Comma separated tool names
        :type tools:        str
        :param order:       order of tools processing: "fwd", "rev" or "no"
        :type order:        str
        :param is_first:    if the tool is the first one should generate the start gcode (not that it matter much
                            which is the one doing it)
        :type is_first:     bool
        :param overrides:   job-wide values {tool data key: value} (the arguments of the Tcl command) that are used for
                            every tool; a value that is not here comes from the tool data of the Excellon object, and
                            when the tool has no such data, from the attribute of the job (set by the Tcl command)
        :type overrides:    dict
        :param holes_area:  a Shapely geometry: only the holes inside it are drilled (before the job transform);
                            None: all the holes
        :return:            None
        """
        overrides = overrides or {}
        job_zcut = self.z_cut

        # #############################################################################################################
        # #############################################################################################################
        # create a local copy of the exobj.tools so it can be used for creating drill CCode geometry
        # #############################################################################################################
        # #############################################################################################################
        # (the drills and the slots are not changed here: the lists are not copied, the data of each tool is)
        self.exc_tools = {tool_id: dict(tool_val, data=dict(tool_val.get('data') or {}))
                          for tool_id, tool_val in exobj.tools.items()}

        # the Excellon GCode preprocessor will use this info in the start_code() method
        self.use_ui = False

        # Z_cut parameter
        # defined in the Tcl command

        # XY_toolchange parameter
        try:
            if self.xy_toolchange == '':
                self.xy_toolchange = None
            else:
                self.xy_toolchange = re.sub(r'[()\[\]]', '', str(self.xy_toolchange)) if self.xy_toolchange else None

                if self.xy_toolchange:
                    self.xy_toolchange = [float(safe_eval(a)) for a in self.xy_toolchange.split(",")]

                if self.xy_toolchange and len(self.xy_toolchange) != 2:
                    self.app.inform.emit('[ERROR]%s' %
                                         _("The Toolchange X,Y field in Edit -> Preferences has to be "
                                           "in the format (x, y) \nbut now there is only one value, not two. "))
                    return 'fail'
        except Exception as e:
            self.app.log.error("camlib.CNCJob.tcl_gcode_from_excellon_by_tool() --> %s" % str(e))
            pass

        # XY_end parameter
        self.xy_end = re.sub(r'[()\[\]]', '', str(self.xy_end)) if self.xy_end else None
        if self.xy_end and self.xy_end != '':
            self.xy_end = [float(safe_eval(a)) for a in self.xy_end.split(",")]
        if self.xy_end and len(self.xy_end) < 2:
            self.app.inform.emit('[ERROR]  %s' % _("The End Move X,Y field in Edit -> Preferences has to be "
                                                   "in the format (x, y) but now there is only one value, not two."))
            return 'fail'

        # Pre-processor
        self.pp_excellon = get_preprocessor(self.app, self.pp_excellon_name)
        p = self.pp_excellon

        self.app.log.debug("Creating CNC Job from Excellon...")

        # #############################################################################################################
        # #############################################################################################################
        # TOOLS
        # sort the tools list by the second item in tuple (here we have a dict with diameter of the tool)
        # so we actually are sorting the tools by diameter
        # #############################################################################################################
        # #############################################################################################################
        all_tools = []
        for tool_as_key, v in list(self.exc_tools.items()):
            all_tools.append((tool_as_key, float(v['tooldia'])))

        if order == 'fwd':
            sorted_tools = sorted(all_tools, key=lambda t1: t1[1])
        elif order == 'rev':
            sorted_tools = sorted(all_tools, key=lambda t1: t1[1], reverse=True)
        else:
            sorted_tools = all_tools

        if tools == "all":
            selected_tools = [i[0] for i in all_tools]  # we get an array of ordered tools
        else:
            selected_tools = [item.strip(" '\"[]()") for item in tools.split(',')] if isinstance(tools, str) else tools
            if not isinstance(selected_tools, (list, tuple, set)):
                selected_tools = [selected_tools]

        # Create a sorted list of selected tools from the sorted_tools list
        selected_tools = {str(tool) for tool in selected_tools}
        tools = [tool for tool, diameter in sorted_tools if str(tool) in selected_tools]
        if not tools:
            return 'fail'

        self.app.log.debug("Tools sorted are: %s" % str(tools))

        # the slots are converted to drills for the tools that have the option in their data; a tool without the
        # option takes it from the job options
        for tool_dict in self.exc_tools.values():
            for key in ('tools_drill_drill_slots', 'tools_drill_drill_overlap', 'tools_drill_last_drill'):
                if key in overrides:
                    tool_dict['data'][key] = overrides[key]
                elif key not in tool_dict['data'] and key in self.obj_options:
                    tool_dict['data'][key] = self.obj_options[key]

        # the values of a tool: a job-wide argument, else the tool data, else the attribute of the job
        tool_values = (('z_move', 'tools_drill_travelz'), ('multidepth', 'tools_drill_multidepth'),
                       ('z_depthpercut', 'tools_drill_depthperpass'), ('f_plunge', 'tools_drill_f_plunge'),
                       ('f_retract', 'tools_drill_f_retract'), ('z_feedrate', 'tools_drill_feedrate_z'),
                       ('feedrate', 'tools_drill_feedrate_z'), ('feedrate_rapid', 'tools_drill_feedrate_rapid'),
                       ('spindlespeed', 'tools_drill_spindlespeed'), ('dwell', 'tools_drill_dwell'),
                       ('dwelltime', 'tools_drill_dwelltime'), ('spindledir', 'tools_drill_spindledir'),
                       ('laser_min_power', 'tools_drill_min_power'), ('laser_on_code', 'tools_drill_laser_on'),
                       ('z_p_depth', 'tools_drill_z_p_depth'), ('feedrate_probe', 'tools_drill_feedrate_probe'),
                       ('probe_plate', 'tools_drill_probe_plate'),
                       ('peck', 'tools_drill_peck'), ('peck_depth', 'tools_drill_peck_depth'),
                       ('peck_retract', 'tools_drill_peck_retract'), ('dwell_bottom', 'tools_drill_dwell_bottom'))

        # the id of the spot drilling tool (a tool that is not in the Excellon object), None without a spot pass
        spot_id = None
        self.spot_tool_id = None

        def tool_value(tool_id, key):
            if tool_id == spot_id:
                # the data of the spot tool already hold the job-wide values and the spot parameters
                return self.exc_tools[tool_id]['data'].get(key)
            if key in overrides:
                return overrides[key]
            return self.exc_tools[tool_id]['data'].get(key)

        def apply_tool_values(tool_id):
            for attr, key in tool_values:
                value = tool_value(tool_id, key)
                if value is not None and value != '':
                    setattr(self, attr, value)

        def tool_zcut(tool_id):
            # the Cut Z of the tool, a positive value is made negative (with a warning), as for the milling
            value = tool_value(tool_id, 'tools_drill_cutz')
            return self.check_drill_cutz(job_zcut if value is None or value == '' else value)

        # #############################################################################################################
        # #############################################################################################################
        # #############################################################################################################
        # fill the data into the self.tools dictionary attribute of Excellon object
        # #############################################################################################################
        # #############################################################################################################
        for it in all_tools:
            to_ol = it[0]
            if to_ol not in tools:
                continue

            radius = it[1] / 2.0
            sol_geo = []

            drills = exobj.tools[to_ol].get('drills') or []
            drill_no = len(drills)
            if drill_no:
                drill_arr = np.empty(drill_no, dtype=object)
                drill_arr[:] = drills
                sol_geo = list(shapely.buffer(drill_arr, radius, quad_segs=circle_quad_segs(self.geo_steps_per_circle)))

            slots = exobj.tools[to_ol].get('slots') or []
            slot_no = len(slots)
            for slot in slots:
                start = (slot[0].x, slot[0].y)
                stop = (slot[1].x, slot[1].y)
                sol_geo.append(LineString([start, stop]).buffer(radius, quad_segs=circle_quad_segs(self.geo_steps_per_circle)))

            default_data = {}
            for k, v in list(self.obj_options.items()):
                default_data[k] = deepcopy(v)

            # it[1] is the tool diameter
            self.tools[to_ol] = {
                'tooldia':          it[1],
                'nr_drills':        drill_no,
                'nr_slots':         slot_no,
                'offset_z':         0,
                'data':             default_data,
                'gcode':            '',
                'gcode_parsed':     [],
                'last_point':       (0, 0),
                'solid_geometry':   sol_geo
            }

        self.app.inform.emit(_("Creating a list of points to drill..."))

        # #############################################################################################################
        # #############################################################################################################
        # Points (Group by tool): a dictionary of shapely Point geo elements grouped by tool number. The slots are
        # converted to drills as in the Drilling plugin (per tool: Drill slots, Overlap, Last drill)
        # #############################################################################################################
        # #############################################################################################################
        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        from appPlugins.ToolDrilling.DrillingGen import create_drill_points
        points = create_drill_points(tools, sorted_tools, self.exc_tools)

        if holes_area is not None:
            from appPlugins.ToolDrilling.DrillingGen import points_in_areas
            points = points_in_areas(points, [holes_area])

        # the job XY transform (mirror, offset, array): the job-wide arguments, else the data of the first drilled
        # tool, else the application options. The spot pass below takes its points from these.
        from appPlugins.ToolDrilling.DrillingGen import transform_job_points
        transform_tool = next((t for t in tools if points.get(t)), None)
        transform_data = {}
        for key in ('tools_drill_job_offset_xy', 'tools_drill_mirror', 'tools_drill_mirror_point',
                    'tools_drill_array_columns', 'tools_drill_array_rows', 'tools_drill_array_pitch'):
            if key in overrides:
                transform_data[key] = overrides[key]
            elif transform_tool is not None and self.exc_tools[transform_tool]['data'].get(key) is not None:
                transform_data[key] = self.exc_tools[transform_tool]['data'][key]
            else:
                transform_data[key] = self._app_option(key)
        try:
            points = transform_job_points(points, transform_data)
        except ValueError as e:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(e))
            return 'fail'

        for tool in points:
            if tool in self.tools:
                # the toolchange message tells how many holes the tool drills
                self.tools[tool]['nr_drills'] = len(points[tool])
        self.app.log.debug("Found %d TOOLS with drill points." % len(points))

        # check if there are drill points in the exclusion areas.
        # If we find any within the exclusion areas return 'fail'
        # The areas are grown as for the travel lines, by the tool radius and a little more: a drill point inside a
        # grown area would start or end a travel inside it. Testing the point against the grown area checks the true
        # circle of the tool (a polygonal circle, a buffer, around the point is inside the true circle and can miss a
        # touch).
        for tool in points:
            grown_areas = [self.app.exc_areas.grown_shape(area['shape'], self.exc_tools[tool]['tooldia'])
                           for area in self.app.exc_areas.exclusion_areas_storage]
            for pt in points[tool]:
                for grown in grown_areas:
                    if grown.intersects(pt):
                        self.app.inform.emit("[ERROR_NOTCL] %s" % _("Failed. Drill points inside the exclusion zones."))
                        return 'fail'

        # this holds the resulting GCode
        self.gcode = ''
        # #############################################################################################################
        # #############################################################################################################
        # Initialization
        # #############################################################################################################
        # #############################################################################################################
        gcode = ''
        start_gcode = ''
        # the start code has the values of the first tool that is drilled
        first_drilled = next((t for t in tools if points.get(t)), None)
        if first_drilled is not None:
            apply_tool_values(first_drilled)

        # the spot drilling pass: one extra tool, drilled first, over all the holes of the drilled tools. It needs the
        # toolchange (the spot drill is another tool): without it the pass is left out. Laser: no tools, ignored.
        if first_drilled is not None and 'laser' not in self.pp_excellon_name.lower():
            first_data = dict(self.exc_tools[first_drilled]['data'])
            first_data.update(overrides)

            def spot_value(key):
                if first_data.get(key) is not None:
                    return first_data[key]
                return self._app_option(key)

            if spot_value('tools_drill_spot') and not self.toolchange:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "The spot drilling pass needs a toolchange: it is left out. Enable the toolchange to use it."))
            elif spot_value('tools_drill_spot'):
                from appPlugins.ToolDrilling.DrillingGen import next_tool_id, spot_tool
                spot_id = next_tool_id(exobj.tools)
                spot = spot_tool(first_data, spot_value('tools_drill_spot_dia'), spot_value('tools_drill_spot_cutz'),
                                 spot_value('tools_drill_spot_feedrate_z'))
                self.exc_tools[spot_id] = spot
                points[spot_id] = [pt for t_id in tools for pt in points.get(t_id, [])]
                spot_points = np.empty(len(points[spot_id]), dtype=object)
                spot_points[:] = points[spot_id]
                self.tools[spot_id] = {
                    'tooldia':          spot['tooldia'],
                    'nr_drills':        len(points[spot_id]),
                    'nr_slots':         0,
                    'offset_z':         0,
                    'data':             deepcopy(dict(self.obj_options)),
                    'gcode':            '',
                    'gcode_parsed':     [],
                    'last_point':       (0, 0),
                    'solid_geometry':   list(shapely.buffer(spot_points, spot['tooldia'] / 2.0,
                                                            quad_segs=circle_quad_segs(self.geo_steps_per_circle)))
                }
                tools = [spot_id] + tools
                self.spot_tool_id = spot_id

        if is_first:
            start_gcode = self.doformat(p.start_code)
            start_gcode += self.doformat(p.z_feedrate_code)

            if self.toolchange is False:
                if self.xy_toolchange is not None:
                    start_gcode += self.doformat(p.lift_code, x=self.xy_toolchange[0], y=self.xy_toolchange[1])
                    start_gcode += self.doformat(p.startz_code, x=self.xy_toolchange[0], y=self.xy_toolchange[1])
                else:
                    start_gcode += self.doformat(p.lift_code, x=0.0, y=0.0)
                    start_gcode += self.doformat(p.startz_code, x=0.0, y=0.0)

        if self.xy_toolchange is not None:
            self.oldx = self.xy_toolchange[0]
            self.oldy = self.xy_toolchange[1]
        else:
            self.oldx = 0.0
            self.oldy = 0.0

        # the distances of the job, to calculate the estimated time
        counters = {'distance': 0.0, 'down': 0.0, 'up_to_zero': 0.0, 'lift': 0.0}

        # #############################################################################################################
        # #############################################################################################################
        # GCODE creation
        # #############################################################################################################
        # #############################################################################################################
        self.app.inform.emit('%s...' % _("Starting G-Code"))

        if not points:
            self.app.log.debug("camlib.CNCJob.tcl_gcode_from_excellon_by_tool() --> "
                               "The loaded Excellon file has no drills ...")
            self.app.inform.emit('[ERROR_NOTCL] %s...' % _('The loaded Excellon file has no drills'))
            return 'fail'

        current_platform = platform.architecture()[0]
        if current_platform == '64bit':
            used_excellon_optimization_type = self.excellon_optimization_type
        else:
            used_excellon_optimization_type = 'R'

        if not HAS_ORTOOLS:
            used_excellon_optimization_type = 'R'

        # #############################################################################################################
        # #############################################################################################################
        # ##################################   DRILLING !!!   #########################################################
        # #############################################################################################################
        # #############################################################################################################
        if used_excellon_optimization_type == 'M':
            self.app.log.debug("Using OR-Tools Metaheuristic Guided Local Search drill path optimization.")
        elif used_excellon_optimization_type == 'B':
            self.app.log.debug("Using OR-Tools Basic drill path optimization.")
        elif used_excellon_optimization_type == 'T':
            self.app.log.debug("Using Travelling Salesman drill path optimization.")
        elif used_excellon_optimization_type == 'R':
            self.app.log.debug("Using RTree drill path optimization.")
        else:
            self.app.log.debug("Using no path optimization.")

        if self.toolchange is True:
            last_gcode_tool = None
            for tool in tools:
                tool_gcode = ''

                # check if it has drills
                if not points.get(tool):
                    self.app.log.warning("camlib.CNCJob.tcl_gcode_from_excellon_by_tool() --> "
                                         "No holes to drill for tool %s." % str(tool))
                    self.app.inform.emit('[WARNING_NOTCL] %s %s: %s' % (
                        _("No G-code for the tool"), str(tool), _("it has no holes to drill (check Drill slots "
                                                                  "if it has only slots)")))
                    continue
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                self.tool = tool
                self.tooldia = self.exc_tools[tool]["tooldia"]
                self.postdata['toolC'] = self.tooldia
                apply_tool_values(tool)
                old_zcut = tool_zcut(tool)
                if old_zcut == 'fail':
                    return 'fail'
                # the travel Z of the tool: the Z of a travel over an exclusion area is restored to it
                base_z_move = self.z_move

                # the machine goes to the toolchange position for the toolchange and drills from there
                if self.xy_toolchange is not None:
                    counters['distance'] += abs(distance_euclidian(self.xy_toolchange[0], self.xy_toolchange[1],
                                                                   self.oldx, self.oldy))
                    self.oldx = self.xy_toolchange[0]
                    self.oldy = self.xy_toolchange[1]

                optimized_path = self.drill_order(points[tool], used_excellon_optimization_type,
                                                  start=(self.oldx, self.oldy))

                # Only if there are locations to drill
                if not optimized_path:
                    self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("No G-code for the tool"), str(tool)))
                    continue

                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                # the first tool that has G-code is the first of the job
                self.is_first_tool = last_gcode_tool is None

                # Tool change sequence (optional)
                if self.toolchange:
                    tool_gcode += self.doformat(p.toolchange_code, toolchangexy=(self.oldx, self.oldy))

                tool_gcode += self.doformat(p.z_feedrate_code)

                if 'laser' not in self.pp_excellon_name.lower():
                    # Spindle start
                    tool_gcode += self.doformat(p.spindle_code)
                    # Dwell time
                    if self.dwell is True:
                        tool_gcode += self.doformat(p.dwell_code)
                else:
                    # Spindle stop
                    tool_gcode += self.doformat(p.lift_code, x=self.oldx, y=self.oldy)  # Move (up) to travel height

                current_tooldia = float('%.*f' % (self.decimals, float(self.exc_tools[tool]["tooldia"])))

                self.app.inform.emit(
                    '%s: %s%s.' % (_("Starting G-Code for tool with diameter"),
                                   str(current_tooldia),
                                   str(self.units))
                )

                # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                # APPLY Offset only when using the appGUI, for TclCommand this will create an error
                # because the values for Z offset are created in build_ui()
                # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                z_offset = -float(tool_value(tool, 'tools_drill_offset') or 0)
                self.z_cut = z_offset + old_zcut

                depths_list, use_pecks = self.drill_depths('laser' in self.pp_excellon_name.lower())

                self.coordinates_type = self._app_option("cncjob_coords_type")
                if self.coordinates_type == "G90":
                    # Drilling! for Absolute coordinates type G90
                    drill_parts, (locx, locy) = self._tcl_drill_parts(p, optimized_path, current_tooldia, depths_list,
                                                                      base_z_move, counters, use_pecks)
                    tool_gcode += drill_parts

                    self.tools[tool]['last_point'] = (locx, locy)
                    self.tools[tool]['gcode'] = tool_gcode
                    gcode += tool_gcode
                    last_gcode_tool = tool
                else:
                    self.app.inform.emit('[ERROR_NOTCL] %s...' % _('G91 coordinates not implemented'))
                    return 'fail'
                self.z_cut = deepcopy(old_zcut)

            if last_gcode_tool is None:
                self.app.inform.emit('[ERROR_NOTCL] %s...' % _('The loaded Excellon file has no drills'))
                return 'fail'

            # add to the last tool that has G-code the end_gcode
            end_gcode = self.doformat(p.spindle_stop_code)
            # Move to End position
            end_gcode += self.doformat(p.end_code, x=0, y=0)
            self.tools[last_gcode_tool]['gcode'] += end_gcode
        else:
            # We are not using Toolchange therefore we need to decide which tool properties to use
            one_tool = next(one for one in tools if points.get(one))
            self.is_first_tool = True

            all_points = []
            for tool in tools:
                all_points += points.get(tool, [])

            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            self.tool = one_tool
            self.tooldia = self.exc_tools[one_tool]["tooldia"]
            self.postdata['toolC'] = self.tooldia

            apply_tool_values(one_tool)
            old_zcut = tool_zcut(one_tool)
            if old_zcut == 'fail':
                return 'fail'
            base_z_move = self.z_move

            gcode += self.doformat(p.z_feedrate_code)

            optimized_path = self.drill_order(all_points, used_excellon_optimization_type,
                                              start=(self.oldx, self.oldy))

            # Only if there are locations to drill
            if not optimized_path:
                return 'fail'

            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            if 'laser' not in self.pp_excellon_name.lower():
                # Spindle start
                gcode += self.doformat(p.spindle_code)
                # Dwell time
                if self.dwell is True:
                    gcode += self.doformat(p.dwell_code)
            else:
                # Spindle stop
                gcode += self.doformat(p.lift_code, x=self.oldx, y=self.oldy)  # Move (up) to travel height

            current_tooldia = float('%.*f' % (self.decimals, float(self.exc_tools[one_tool]["tooldia"])))

            self.app.inform.emit(
                '%s: %s%s.' % (_("Starting G-Code for tool with diameter"),
                               str(current_tooldia),
                               str(self.units))
            )

            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            # APPLY Offset only when using the appGUI, for TclCommand this will create an error
            # because the values for Z offset are created in build_ui()
            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            z_offset = -float(tool_value(one_tool, 'tools_drill_offset') or 0)
            self.z_cut = z_offset + old_zcut

            depths_list, use_pecks = self.drill_depths('laser' in self.pp_excellon_name.lower())

            self.coordinates_type = self._app_option("cncjob_coords_type")
            if self.coordinates_type == "G90":
                # Drilling! for Absolute coordinates type G90
                drill_parts, __ = self._tcl_drill_parts(p, optimized_path, current_tooldia, depths_list,
                                                        base_z_move, counters, use_pecks)
                gcode += drill_parts
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s...' % _('G91 coordinates not implemented'))
                return 'fail'
            self.z_cut = deepcopy(old_zcut)
            self.tools[one_tool]['gcode'] = gcode

            # add the end_gcode
            end_gcode = self.doformat(p.spindle_stop_code)
            end_gcode += self.doformat(p.end_code, x=0, y=0)
            self.tools[one_tool]['gcode'] += end_gcode      # noqa

        measured_distance = counters['distance']
        if used_excellon_optimization_type == 'M':
            self.app.log.debug("The total travel distance with OR-TOOLS Metaheuristics is: %s" % str(measured_distance))
        elif used_excellon_optimization_type == 'B':
            self.app.log.debug(
                "The total travel distance with OR-TOOLS Basic Algorithm is: %s" % str(measured_distance))
        elif used_excellon_optimization_type == 'T':
            self.app.log.debug(
                "The total travel distance with Travelling Salesman Algorithm is: %s" % str(measured_distance))
        elif used_excellon_optimization_type == 'R':
            self.app.log.debug("The total travel distance with Rtree Algorithm is: %s" % str(measured_distance))
        else:
            self.app.log.debug("The total travel distance with with no optimization is: %s" % str(measured_distance))

        gcode += self.doformat(p.spindle_stop_code)
        # Move to End position
        gcode += self.doformat(p.end_code, x=0, y=0)

        # #############################################################################################################
        # ############################# Calculate DISTANCE and ESTIMATED TIME #########################################
        # #############################################################################################################
        measured_distance += abs(distance_euclidian(self.oldx, self.oldy, 0, 0))
        self.app.log.debug("The total travel distance including travel to end position is: %s" %
                           str(measured_distance) + '\n')
        self.travel_distance = measured_distance

        # I use the value of self.feedrate_rapid for the feadrate in case of the measure_lift_distance and for
        # traveled_time because it is not always possible to determine the feedrate that the CNC machine uses
        # for G0 move (the fastest speed available to the CNC router). Although self.feedrate_rapids is used only with
        # Marlin preprocessor and derivatives. A feedrate of zero adds no time (no division by zero).
        self.routing_time = 0.0
        if self.feedrate:
            self.routing_time += (counters['down'] + counters['up_to_zero']) / self.feedrate
        if self.feedrate_rapid:
            self.routing_time += (counters['lift'] + measured_distance) / self.feedrate_rapid

        # #############################################################################################################
        # ############################# Store the GCODE for further usage ############################################
        # #############################################################################################################
        self.gcode = gcode

        self.app.inform.emit('%s ...' % _("Finished G-Code generation"))
        self.laser_min_power_warning(gcode)
        return gcode, start_gcode

    def _tcl_drill_parts(self, p, optimized_path, tooldia, depths_list, base_z_move, counters, use_pecks=False):
        """
        The G-code of drilling the holes of a path (Tcl drillcncjob): the travel to each hole (around the exclusion
        areas), the depths, the lift. Adds the distances to counters.

        :param optimized_path:  list of (x, y) in the order of drilling
        :param base_z_move:     the travel Z of the job, restored after a travel over an exclusion area
        :return:                (G-code, (x, y) of the last hole)
        """
        geo_len = len(optimized_path)
        old_disp_number = 0
        self.app.log.warning("Number of drills for which to generate GCode: %s" % str(geo_len))

        loc_nr = 0
        locx = self.oldx
        locy = self.oldy
        # the chunks are joined 1 time: a str += in a loop copies the whole string each time
        drill_parts = []
        for point in optimized_path:
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            locx, locy = point

            # Tcl drillcncjob has no plugin that draws areas: it uses every stored area (owner None)
            travels = self.app.exc_areas.travel_coordinates(start_point=(self.oldx, self.oldy),
                                                            end_point=(locx, locy),
                                                            tooldia=tooldia, owner=None, travel_z=base_z_move)
            prev_z = None
            for travel in travels:
                locx = travel[1][0]
                locy = travel[1][1]

                if travel[0] is not None:
                    # move to next point
                    drill_parts.append(self.doformat(p.rapid_code, x=locx, y=locy))

                    # raise to safe Z (travel[0]) each time because safe Z may be different
                    self.z_move = travel[0]
                    drill_parts.append(self.doformat(p.lift_code, x=locx, y=locy))

                    # restore z_move
                    self.z_move = base_z_move
                else:
                    if prev_z is not None:
                        # move to next point
                        drill_parts.append(self.doformat(p.rapid_code, x=locx, y=locy))

                        # we assume that previously the z_move was altered therefore raise to
                        # the travel_z (z_move)
                        self.z_move = base_z_move
                        drill_parts.append(self.doformat(p.lift_code, x=locx, y=locy))
                    else:
                        # move to next point
                        drill_parts.append(self.doformat(p.rapid_code, x=locx, y=locy))

                # store prev_z
                prev_z = travel[0]

            drill_parts += self.drill_hole_parts(p, locx, locy, depths_list, counters, pecks=use_pecks,
                                                 is_laser='laser' in self.pp_excellon_name.lower())

            counters['distance'] += abs(distance_euclidian(locx, locy, self.oldx, self.oldy))
            self.oldx = locx
            self.oldy = locy

            loc_nr += 1
            disp_number = int(np.interp(loc_nr, [0, geo_len], [0, 100]))

            if old_disp_number < disp_number <= 100:
                self.app.proc_container.update_view_text(' %d%%' % disp_number)
                old_disp_number = disp_number
        self.z_move = base_z_move
        return ''.join(drill_parts), (locx, locy)

    # no longer used
    def generate_from_multitool_geometry(self, geometry, append=True, tooldia=None, offset=0.0, tolerance=0, z_cut=1.0,
                                         z_move=2.0, feedrate=2.0, feedrate_z=2.0, feedrate_rapid=30,
                                         spindlespeed=None, spindledir='CW', dwell=False, dwelltime=1.0,
                                         multidepth=False, depthpercut=None, toolchange=False, toolchangez=1.0,
                                         toolchangexy="0.0, 0.0", extracut=False, extracut_length=0.2,
                                         startz=None, endz=2.0, endxy='', pp_geometry_name=None, tool_no=1):
        """
        Algorithm to generate from multitool Geometry.

        Algorithm description:
        ----------------------
        Uses RTree to find the nearest path to follow.

        :param geometry:
        :param append:
        :param tooldia:
        :param offset:
        :param tolerance:
        :param z_cut:
        :param z_move:
        :param feedrate:
        :param feedrate_z:
        :param feedrate_rapid:
        :param spindlespeed:
        :param spindledir:          Direction of rotation for the spindle. If using GRBL laser mode will
        adjust the laser mode

        :param dwell:
        :param dwelltime:
        :param multidepth:          If True, use multiple passes to reach the desired depth.
        :param depthpercut:         Maximum depth in each pass.
        :param toolchange:
        :param toolchangez:
        :param toolchangexy:
        :param extracut:            Adds (or not) an extra cut at the end of each path overlapping the
                                    first point in path to ensure complete copper removal
        :param extracut_length:     Extra cut length at the end of the path
        :param startz:
        :param endz:
        :param endxy:
        :param pp_geometry_name:
        :param tool_no:
        :return:                    GCode - string
        """

        self.app.log.debug("generate_from_multitool_geometry()")

        temp_solid_geometry = []
        if offset != 0.0:
            for it in geometry:
                # if the geometry is a closed shape then create a Polygon out of it
                if isinstance(it, LineString):
                    c = it.coords
                    if c[0] == c[-1]:
                        it = Polygon(it)
                temp_solid_geometry.append(it.buffer(offset, join_style="mitre"))
        else:
            temp_solid_geometry = geometry

        # ## Flatten the geometry. Only linear elements (no polygons) remain.
        flat_geometry = self.flatten(temp_solid_geometry, pathonly=True)
        self.app.log.debug("%d paths" % len(flat_geometry))

        try:
            self.tooldia = float(tooldia)
        except Exception as e:
            self.app.inform.emit('[ERROR] %s\n%s' % (_("Failed."), str(e)))
            return 'fail'

        self.z_cut = float(z_cut) if z_cut else None
        self.z_move = float(z_move) if z_move is not None else None

        self.feedrate = float(feedrate) if feedrate else self._app_option(
            "geometry_feedrate", self._app_option("tools_mill_feedrate", feedrate))
        self.z_feedrate = float(feedrate_z) if feedrate_z is not None else self._app_option(
            "geometry_feedrate_z", self._app_option("tools_mill_feedrate_z", feedrate_z))
        self.feedrate_rapid = float(feedrate_rapid) if feedrate_rapid else self._app_option(
            "geometry_feedrate_rapid", self._app_option("tools_mill_feedrate_rapid", feedrate_rapid))

        self.spindlespeed = int(spindlespeed) if spindlespeed != 0 else None
        self.spindledir = spindledir
        self.dwell = dwell
        self.dwelltime = float(dwelltime) if dwelltime else self._app_option(
            "geometry_dwelltime", self._app_option("tools_mill_dwelltime", dwelltime))

        self.startz = float(startz) if startz is not None else self._app_option(
            "geometry_startz", self._app_option("tools_mill_startz", startz))
        self.z_end = float(endz) if endz is not None else self._app_option(
            "geometry_endz", self._app_option("tools_mill_endz", endz))

        self.xy_end = re.sub(r'[()\[\]]', '', str(endxy)) if endxy else self._app_option(
            "geometry_endxy", self._app_option("tools_mill_endxy", endxy))

        if self.xy_end and self.xy_end != '':
            self.xy_end = [float(safe_eval(a)) for a in self.xy_end.split(",")]

        if self.xy_end and len(self.xy_end) < 2:
            self.app.inform.emit('[ERROR]  %s' % _("The End Move X,Y field in Edit -> Preferences has to be "
                                                   "in the format (x, y) but now there is only one value, not two."))
            return 'fail'

        self.z_depthpercut = float(depthpercut) if depthpercut else self._app_option(
            "geometry_depthperpass", self._app_option("tools_mill_depthperpass", depthpercut))
        self.multidepth = multidepth

        self.z_toolchange = float(toolchangez) if toolchangez is not None else self._app_option(
            "geometry_toolchangez", self._app_option("tools_mill_toolchangez", toolchangez))

        # it servers in the preprocessor file
        self.tool = tool_no
        self.is_first_tool = True

        try:
            if toolchangexy == '':
                self.xy_toolchange = None
            else:
                self.xy_toolchange = re.sub(r'[()\[\]]', '', str(toolchangexy)) \
                    if toolchangexy else self._app_option(
                        "geometry_toolchangexy", self._app_option("tools_mill_toolchangexy", toolchangexy))

                if self.xy_toolchange and self.xy_toolchange != '':
                    self.xy_toolchange = [float(safe_eval(a)) for a in self.xy_toolchange.split(",")]

                if len(self.xy_toolchange) < 2:
                    self.app.inform.emit('[ERROR]  %s' % _("The Toolchange X,Y field in Edit -> Preferences has to be "
                                                           "in the format (x, y) \n"
                                                           "but now there is only one value, not two."))
                    return 'fail'
        except Exception as e:
            self.app.log.error("camlib.CNCJob.generate_from_multitool_geometry() --> %s" % str(e))
            pass

        self.pp_geometry_name = pp_geometry_name if pp_geometry_name else 'default'
        self.f_plunge = self._app_option("geometry_f_plunge", self._app_option("tools_mill_f_plunge"))

        if self.z_cut is None:
            if 'laser' not in self.pp_geometry_name:
                self.app.inform.emit(
                    '[ERROR_NOTCL] %s' % _("Cut_Z parameter is None or zero. Most likely a bad combinations of "
                                           "other parameters."))
                return 'fail'
            else:
                self.z_cut = 0

        if self.z_cut > 0:
            self.app.inform.emit('[WARNING] %s' %
                                 _("The Cut Z parameter has positive value. "
                                   "It is the depth value to cut into material.\n"
                                   "The Cut Z parameter needs to have a negative value, assuming it is a typo "
                                   "therefore the app will convert the value to negative."
                                   "Check the resulting CNC code (Gcode etc)."))
            self.z_cut = -self.z_cut
        elif self.z_cut == 0 and 'laser' not in self.pp_geometry_name:
            self.app.inform.emit('[WARNING] %s: %s' %
                                 (_("The Cut Z parameter is zero. There will be no cut, skipping file"),
                                  self.obj_options['name']))
            return 'fail'

        if self.z_move is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Travel Z parameter is None or zero."))
            return 'fail'

        if self.z_move < 0:
            self.app.inform.emit('[WARNING] %s' %
                                 _("The Travel Z parameter has negative value. "
                                   "It is the height value to travel between cuts.\n"
                                   "The Z Travel parameter needs to have a positive value, assuming it is a typo "
                                   "therefore the app will convert the value to positive."
                                   "Check the resulting CNC code (Gcode etc)."))
            self.z_move = -self.z_move
        elif self.z_move == 0:
            self.app.inform.emit('[WARNING] %s: %s' %
                                 (_("The Z Travel parameter is zero. This is dangerous, skipping file"),
                                  self.obj_options['name']))
            return 'fail'

        # made sure that depth_per_cut is no more than the z_cut
        if abs(self.z_cut) < self.z_depthpercut:
            self.z_depthpercut = abs(self.z_cut)

        # ## Index first and last points in paths
        # What points to index.
        def get_pts(o):
            return [o.coords[0], o.coords[-1]]

        # Create the indexed storage.
        storage = AppRTreeStorage()
        storage.get_points = get_pts

        # Store the geometry
        self.app.log.debug("Indexing geometry before generating G-Code...")
        self.app.inform.emit(_("Indexing geometry before generating G-Code..."))

        new_shapes = []
        for geo_shape in flat_geometry:
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            if geo_shape is not None:
                new_shapes.append(geo_shape)
        # 1 batch (bulk load)
        storage.insert_many(new_shapes)

        # self.input_geometry_bounds = geometry.bounds()

        if not append:
            self.gcode = ""

        # tell preprocessor the number of tool (for toolchange)
        self.tool = tool_no
        self.is_first_tool = True

        # this is the tool diameter, it is used as such to accommodate the preprocessor who need the tool diameter
        # given under the name 'toolC'
        self.postdata['toolC'] = self.tooldia

        # Initial G-Code
        self.pp_geometry = get_preprocessor(self.app, self.pp_geometry_name)
        p = self.pp_geometry

        self.gcode = self.doformat(p.start_code)

        self.gcode += self.doformat(p.feedrate_code)  # sets the feed rate

        if toolchange is False:
            self.gcode += self.doformat(p.lift_code, x=0, y=0)  # Move (up) to travel height
            self.gcode += self.doformat(p.startz_code, x=0, y=0)

        if toolchange:
            # if "line_xyz" in self.pp_geometry_name:
            #     self.gcode += self.doformat(p.toolchange_code, x=self.xy_toolchange[0], y=self.xy_toolchange[1])
            # else:
            #     self.gcode += self.doformat(p.toolchange_code)
            self.gcode += self.doformat(p.toolchange_code)

            if 'laser' not in self.pp_geometry_name:
                self.gcode += self.doformat(p.spindle_code)  # Spindle start
            else:
                # for laser this will disable the laser
                self.gcode += self.doformat(p.lift_code, x=self.oldx, y=self.oldy)  # Move (up) to travel height

            if self.dwell is True:
                self.gcode += self.doformat(p.dwell_code)  # Dwell time
        else:
            if 'laser' not in self.pp_geometry_name:
                self.gcode += self.doformat(p.spindle_code)  # Spindle start

            if self.dwell is True:
                self.gcode += self.doformat(p.dwell_code)  # Dwell time

        total_travel = 0.0
        total_cut = 0.0

        # ## Iterate over geometry paths getting the nearest each time.
        self.app.log.debug("Starting G-Code...")
        self.app.inform.emit('%s...' % _("Starting G-Code"))

        path_count = 0
        current_pt = (0, 0)

        # variables to display the percentage of work done
        geo_len = len(flat_geometry)

        old_disp_number = 0
        self.app.log.warning("Number of paths for which to generate GCode: %s" % str(geo_len))

        current_tooldia = float('%.*f' % (self.decimals, float(self.tooldia)))

        self.app.inform.emit('%s: %s%s.' % (_("Starting G-Code for tool with diameter"),
                                            str(current_tooldia),
                                            str(self.units)))

        pt, geo = storage.nearest(current_pt)

        # the chunks are joined 1 time: a str += in a loop copies the whole string each time
        path_parts = []
        try:
            while True:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                path_count += 1

                # Remove before modifying, otherwise deletion will fail.
                storage.remove(geo)

                # If last point in geometry is the nearest but prefer the first one if last point == first point
                # then reverse coordinates.
                if pt != geo.coords[0] and pt == geo.coords[-1]:
                    # geo.coords = list(geo.coords)[::-1] # Shapley 2.0
                    geo = LineString(list(geo.coords)[::-1])

                # ---------- Single depth/pass --------
                if not multidepth:
                    # calculate the cut distance
                    total_cut = total_cut + geo.length

                    path_parts.append(self.create_gcode_single_pass(geo, current_tooldia, extracut, extracut_length,
                                                                    tolerance, z_move=z_move, old_point=current_pt))

                # --------- Multi-pass ---------
                else:
                    # calculate the cut distance
                    # due of the number of cuts (multi depth) it has to multiplied by the number of cuts
                    nr_cuts = 0
                    depth = abs(self.z_cut)
                    while depth > 0:
                        nr_cuts += 1
                        depth -= float(self.z_depthpercut)

                    total_cut += (geo.length * nr_cuts)

                    gc, geo = self.create_gcode_multi_pass(geo, current_tooldia, extracut, extracut_length,
                                                           tolerance,  z_move=z_move, postproc=p,
                                                           old_point=current_pt)
                    path_parts.append(gc)

                # calculate the total distance
                total_travel = total_travel + abs(distance(pt1=current_pt, pt2=pt))
                current_pt = geo.coords[-1]

                pt, geo = storage.nearest(current_pt)  # Next

                disp_number = int(np.interp(path_count, [0, geo_len], [0, 100]))
                if old_disp_number < disp_number <= 100:
                    self.app.proc_container.update_view_text(' %d%%' % disp_number)
                    old_disp_number = disp_number
        except StopIteration:  # Nothing found in storage.
            pass
        self.gcode += ''.join(path_parts)

        self.app.log.debug("Finished G-Code... %s paths traced." % path_count)

        # add move to end position
        total_travel += abs(distance_euclidian(current_pt[0], current_pt[1], 0, 0))
        self.travel_distance += total_travel + total_cut
        self.routing_time += total_cut / self.feedrate

        # Finish
        self.gcode += self.doformat(p.spindle_stop_code)
        self.gcode += self.doformat(p.lift_code, x=current_pt[0], y=current_pt[1])
        self.gcode += self.doformat(p.end_code, x=0, y=0)
        self.app.inform.emit(
            '%s... %s %s.' % (_("Finished G-Code generation"), str(path_count), _("paths traced"))
        )
        return self.gcode

    def generate_from_geometry_2(
            self,
            geo_obj,
            append=True,
            tooldia=None,
            offset=0.0,
            tolerance=0,
            z_cut=None,
            z_move=None,
            feedrate=None,
            feedrate_z=None,
            feedrate_rapid=None,
            spindlespeed=None,
            spindle_dir='CW',
            dwell=False,
            dwelltime=None,
            laser_min_power=0.0,
            laser_on_code="M03",
            multidepth=False,
            depthpercut=None,
            toolchange=False,
            toolchangez=None,
            toolchangexy="0.0, 0.0",
            extracut=False,
            extracut_length=None,
            startz=None, endz=None,
            endxy='',
            pp_geometry_name=None,
            tool_no=1,
            is_first=False,
            area_exclusion=True,
            opt_type=None,
            opt_time=None,
    ):
        """
        Second algorithm to generate from Geometry.

        Algorithm description:
        ----------------------
        Uses RTree to find the nearest path to follow.

        :param geo_obj:
        :param append:
        :param tooldia:
        :param offset:
        :param tolerance:
        :param z_cut:
        :param z_move:
        :param feedrate:
        :param feedrate_z:
        :param feedrate_rapid:
        :param spindlespeed:
        :param spindle_dir:
        :param dwell:
        :param dwelltime:
        :param laser_min_power:     Float value. Used when the preprocessor contains 'laser' in its name. Control
                                    the power when the laser is `OFF`
        :param multidepth:          If True, use multiple passes to reach the desired depth.
        :param depthpercut:         Maximum depth in each pass.
        :param toolchange:
        :param toolchangez:
        :param toolchangexy:
        :param extracut:            Adds (or not) an extra cut at the end of each path overlapping the first point in
                                    path to ensure complete copper removal
        :param extracut_length:     The extra cut length
        :param startz:
        :param endz:
        :param endxy:
        :param pp_geometry_name:
        :param tool_no:
        :param is_first:            if the processed tool is the first one and if we should process the start gcode
        :param area_exclusion:      if True the travels are routed around the exclusion areas (the Area Exclusion
                                    option of the tool); if False they go straight
        :param opt_type:            the path optimization type of the tool; None = the application option
        :param opt_time:            the OR-Tools search time of the tool; None = the application option
        :return:                    None
        """
        self.app.log.debug("Executing camlib.CNCJob.generate_from_geometry_2()")
        self.area_exclusion = bool(area_exclusion)

        # if solid_geometry is empty raise an exception
        if not geo_obj.solid_geometry:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("Trying to generate a CNC Job from a Geometry object without solid_geometry.")
            )
            return 'fail'

        def bounds_rec(obj):
            if type(obj) is list:
                minx = np.inf
                miny = np.inf
                maxx = -np.inf
                maxy = -np.inf

                for k in obj:
                    if type(k) is dict:
                        for key in k:
                            minx_, miny_, maxx_, maxy_ = bounds_rec(k[key])
                            minx = min(minx, minx_)
                            miny = min(miny, miny_)
                            maxx = max(maxx, maxx_)
                            maxy = max(maxy, maxy_)
                    else:
                        minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                        minx = min(minx, minx_)
                        miny = min(miny, miny_)
                        maxx = max(maxx, maxx_)
                        maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's a Shapely object, return its bounds
                return obj.bounds

        # #############################################################################################################
        # ## Flatten the geometry. Only linear elements (no polygons) remain.
        # #############################################################################################################

        # flat_geometry = self.flatten(temp_solid_geometry, pathonly=True)
        flat_ext_geo, flat_ints_geo = self.flatten_exterior_interiors(geo_obj.solid_geometry)
        flat_geometry = flat_ext_geo + flat_ints_geo
        self.app.log.debug("%d paths" % len(flat_geometry))

        # Create the solid geometry which will be used to generate GCode
        temp_solid_geometry = []
        if offset != 0.0:
            offset_for_use = offset

            if offset < 0:
                a, b, c, d = bounds_rec(geo_obj.solid_geometry)
                # if the offset is less than half of the total length or less than half of the total width of the
                # solid geometry it's obvious we can't do the offset
                if -offset > ((c - a) / 2) or -offset > ((d - b) / 2):
                    self.app.inform.emit(
                        '[ERROR_NOTCL] %s' %
                        _("The Tool Offset value is too negative to use for the current_geometry.\n"
                          "Raise the value (in module) and try again.")
                    )
                    return 'fail'
                # hack: make offset smaller by 0.0000000001 which is insignificant difference but allow the job
                # to continue
                elif -offset == ((c - a) / 2) or -offset == ((d - b) / 2):
                    offset_for_use = offset - 0.0000000001

            # for it in flat_geometry:
            #     # if the geometry is a closed shape then create a Polygon out of it
            #     if isinstance(it, (LineString, LinearRing)):
            #         c = it.coords
            #         if c[0] == c[-1]:
            #             it = Polygon(it)
            #     temp_solid_geometry.append(it.buffer(offset_for_use, join_style=2))

            for it in flat_ext_geo:
                # if the geometry is a closed shape then create a Polygon out of it
                if isinstance(it, LineString):
                    if it.is_ring:
                        it = Polygon(it)
                temp_solid_geometry.append(it.buffer(offset_for_use, join_style="mitre"))

            for it in flat_ints_geo:
                # if the geometry is a closed shape then create a Polygon out of it
                if isinstance(it, (LineString, LinearRing)):
                    if it.is_ring:
                        it = Polygon(it)
                temp_solid_geometry.append(it.buffer(-offset_for_use, join_style="mitre"))

            temp_solid_geometry = self.flatten(temp_solid_geometry, reset=True, pathonly=True)
        else:
            temp_solid_geometry = flat_geometry

        temp_solid_geometry = [t_geo for t_geo in temp_solid_geometry if t_geo is not None and not t_geo.is_empty]
        if not temp_solid_geometry:
            self.no_usable_geometry_notice(tooldia)
            return 'fail'

        default_dia = None
        if isinstance(self._app_option("tools_mill_tooldia"), float):
            default_dia = self._app_option("tools_mill_tooldia")
        else:
            try:
                tools_string = self._app_option("tools_mill_tooldia").split(",")
                tools_diameters = [safe_eval(a) for a in tools_string if a != '']
                default_dia = tools_diameters[0] if tools_diameters else 0.0
            except Exception as e:
                self.app.log.error("camlib.CNCJob.generate_from_geometry_2() --> %s" % str(e))

        try:
            self.tooldia = float(tooldia) if tooldia else default_dia
        except ValueError:
            self.tooldia = [float(el) for el in tooldia.split(',') if el != ''] if tooldia is not None else default_dia

        if self.tooldia is None:
            self.app.inform.emit('[ERROR] %s' % _("Failed."))
            return 'fail'

        self.z_cut = float(z_cut) if z_cut is not None else self._app_option("tools_mill_cutz")
        self.z_move = float(z_move) if z_move is not None else self._app_option("tools_mill_travelz")

        self.feedrate = float(feedrate) if feedrate is not None else self._app_option("tools_mill_feedrate")
        self.z_feedrate = float(feedrate_z) if feedrate_z is not None else self._app_option("tools_mill_feedrate_z")
        self.feedrate_rapid = float(feedrate_rapid) if feedrate_rapid is not None else \
            self._app_option("tools_mill_feedrate_rapid")

        self.spindlespeed = int(spindlespeed) if spindlespeed != 0 and spindlespeed is not None else None
        self.spindledir = spindle_dir
        self.dwell = dwell
        self.dwelltime = float(dwelltime) if dwelltime is not None else self._app_option("tools_mill_dwelltime")

        self.laser_min_power = int(laser_min_power)
        self.laser_on_code = str(laser_on_code)

        self.startz = float(startz) if startz is not None and startz != '' else self._app_option("tools_mill_startz")

        self.z_end = float(endz) if endz is not None else self._app_option("tools_mill_endz")

        self.xy_end = endxy if endxy != '' and endxy else self._app_option("tools_mill_endxy")
        self.xy_end = re.sub(r'[()\[\]]', '', str(self.xy_end)) if self.xy_end else None

        if self.xy_end is not None and self.xy_end != '':
            self.xy_end = [float(safe_eval(a)) for a in self.xy_end.split(",")]

        if self.xy_end and len(self.xy_end) < 2:
            self.app.inform.emit('[ERROR]  %s' % _("The End Move X,Y field in Edit -> Preferences has to be "
                                                   "in the format (x, y) but now there is only one value, not two."))
            return 'fail'

        self.z_depthpercut = float(depthpercut) if depthpercut is not None and depthpercut != 0 else abs(self.z_cut)
        # must be a positive value
        self.z_depthpercut = abs(self.z_depthpercut)
        self.multidepth = multidepth
        self.z_toolchange = float(toolchangez) if toolchangez is not None else \
            self._app_option("tools_mill_toolchangez")
        self.extracut_length = float(extracut_length) if extracut_length is not None else \
            self._app_option("tools_mill_extracut_length")

        try:
            if toolchangexy == '':
                self.xy_toolchange = None
            else:
                self.xy_toolchange = (
                    re.sub(r'[()\[\]]', '', str(toolchangexy))
                    if self.xy_toolchange
                    else None
                )

                if self.xy_toolchange and self.xy_toolchange != '':
                    self.xy_toolchange = [float(safe_eval(a)) for a in self.xy_toolchange.split(",")]

                    if len(self.xy_toolchange) < 2:
                        self.app.inform.emit(
                            '[ERROR] %s' % _("The Toolchange X,Y format has to be (x, y).")
                        )
                        return 'fail'
        except Exception as e:
            self.app.log.error("camlib.CNCJob.generate_from_geometry_2() --> %s" % str(e))
            pass

        self.pp_geometry_name = pp_geometry_name if pp_geometry_name else 'default'
        self.f_plunge = self._app_option("tools_mill_f_plunge")

        if self.z_cut is None:
            if 'laser' not in self.pp_geometry_name:
                self.app.inform.emit(
                    '[ERROR_NOTCL] %s' % _("Cut_Z parameter is None or zero. Most likely a bad combinations of "
                                           "other parameters.")
                )
                return 'fail'
            else:
                self.z_cut = 0.0

        if self.z_cut > 0:
            self.app.inform.emit('[WARNING] %s' %
                                 _("The Cut Z parameter has positive value. "
                                   "It is the depth value to cut into material.\n"
                                   "The Cut Z parameter needs to have a negative value, assuming it is a typo "
                                   "therefore the app will convert the value to negative."
                                   "Check the resulting CNC code (Gcode etc)."))
            self.z_cut = -self.z_cut
        elif self.z_cut == 0 and 'laser' not in self.pp_geometry_name:
            self.app.inform.emit(
                '[WARNING] %s: %s' % (_("The Cut Z parameter is zero. There will be no cut, skipping file"),
                                      geo_obj.obj_options['name'])
            )
            return 'fail'

        if self.z_move is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Travel Z parameter is None or zero."))
            return 'fail'

        if 'laser' not in self.pp_geometry_name:
            if self.z_move < 0:
                self.app.inform.emit('[WARNING] %s' %
                                     _("The Travel Z parameter has negative value. "
                                       "It is the height value to travel between cuts.\n"
                                       "The Z Travel parameter needs to have a positive value, assuming it is a typo "
                                       "therefore the app will convert the value to positive."
                                       "Check the resulting CNC code (Gcode etc)."))
                self.z_move = -self.z_move
            elif self.z_move == 0:
                self.app.inform.emit(
                    '[WARNING] %s: %s' % (_("The Z Travel parameter is zero. This is dangerous, skipping file"),
                                          self.obj_options['name'])
                )
                return 'fail'

        # made sure that depth_per_cut is no more than the z_cut (travelz)
        try:
            if abs(self.z_cut) < self.z_depthpercut:
                self.z_depthpercut = abs(self.z_cut)
        except TypeError:
            self.z_depthpercut = abs(self.z_cut)

        # #########################################################################################################
        # ############ Create the data. ###########################################################################
        # #########################################################################################################
        if opt_type is None:
            opt_type = self._app_option("tools_mill_optimization_type")
        if not HAS_ORTOOLS:
            opt_type = 'R'

        opt_time = int(self._app_option('tools_mill_search_time') if opt_time is None else opt_time)

        if opt_type == 'M':
            self.app.log.debug("Using OR-Tools Metaheuristic Guided Local Search path optimization.")
        elif opt_type == 'B':
            self.app.log.debug("Using OR-Tools Basic path optimization.")
        elif opt_type == 'T':
            self.app.log.debug("Using Travelling Salesman path optimization.")
        elif opt_type == 'R':
            self.app.log.debug("Using RTree path optimization.")
        else:
            self.app.log.debug("Using no path optimization.")

        optimized_path = []

        geo_storage = {}
        for geo in temp_solid_geometry:
            if geo is not None and isinstance(geo, (LineString, LinearRing)):
                try:
                    geo_storage.setdefault(geo.coords[0], []).append(geo)
                except Exception:
                    pass
        # several paths can start at the same point: they are all cut, one after the other
        locations = list(geo_storage.keys())

        if opt_type == 'M':
            # if there are no locations then go to the next tool
            if not locations:
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'

            optimized_locations = self.optimized_ortools_meta(locations=locations, opt_time=opt_time)
            optimized_path = [(locations[loc], g) for loc in optimized_locations for g in geo_storage[locations[loc]]]
        elif opt_type == 'B':
            # if there are no locations then go to the next tool
            if not locations:
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'
            optimized_locations = self.optimized_ortools_basic(locations=locations, opt_time=opt_time)
            optimized_path = [(locations[loc], g) for loc in optimized_locations for g in geo_storage[locations[loc]]]
        elif opt_type == 'T':
            # if there are no locations then go to the next tool
            if not locations:
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'
            optimized_locations = self.optimized_travelling_salesman(locations)
            optimized_path = [(loc, g) for loc in optimized_locations for g in geo_storage[loc]]
        elif opt_type == 'R':
            optimized_path = self.geo_optimized_rtree(temp_solid_geometry)
            if optimized_path == 'fail':
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'
        elif opt_type == 'N':
            optimized_path = [(k, g) for k, v in geo_storage.items() for g in v]
            if not optimized_path:
                self.no_usable_geometry_notice(self.tooldia)
                return 'fail'
        else:
            # it's actually not optimized path but here we build a list of (x,y) coordinates
            # out of the tool
            for geo in temp_solid_geometry:
                optimized_path.append(geo.coords[0])
        # #########################################################################################################

        if not append:
            self.gcode = ""
        # with append, self.gcode starts with the G-code of the previous tools; the Min Power warning is for this one
        tool_gcode_start = len(self.gcode)

        # tell preprocessor the number of tool (for toolchange)
        self.tool = tool_no
        self.is_first_tool = bool(is_first)

        # this is the tool diameter, it is used as such to accommodate the preprocessor who need the tool diameter
        # given under the name 'toolC'
        # this is a fancy way of adding a class attribute (which should be added in the __init__ method) without doing
        # it there :)
        self.postdata['toolC'] = self.tooldia

        # Initial G-Code
        self.pp_geometry = get_preprocessor(self.app, self.pp_geometry_name)

        # the 'p' local attribute is a reference to the current preprocessor class
        p = self.pp_geometry

        self.oldx = 0.0
        self.oldy = 0.0

        start_gcode = ''
        if is_first:
            start_gcode = self.doformat(p.start_code)

        # self.gcode = self.doformat(p.start_code)
        self.gcode += self.doformat(p.feedrate_code)  # sets the feed rate

        if toolchange is False:
            # all the x and y parameters in self.doformat() are used only by some preprocessors not by all
            self.gcode += self.doformat(p.lift_code, x=self.oldx, y=self.oldy)  # Move (up) to travel height
            self.gcode += self.doformat(p.startz_code, x=self.oldx, y=self.oldy)

        if toolchange:
            # if "line_xyz" in self.pp_geometry_name:
            #     self.gcode += self.doformat(p.toolchange_code, x=self.xy_toolchange[0], y=self.xy_toolchange[1])
            # else:
            #     self.gcode += self.doformat(p.toolchange_code)
            self.gcode += self.doformat(p.toolchange_code)

            if 'laser' not in self.pp_geometry_name:
                self.gcode += self.doformat(p.spindle_code)  # Spindle start
                if self.dwell is True:
                    self.gcode += self.doformat(p.dwell_code)  # Dwell time
            else:
                # for laser this will disable the laser
                self.gcode += self.doformat(p.lift_code, x=self.oldx, y=self.oldy)  # Move (up) to travel height
        else:
            if 'laser' not in self.pp_geometry_name:
                self.gcode += self.doformat(p.spindle_code)  # Spindle start
                if self.dwell is True:
                    self.gcode += self.doformat(p.dwell_code)  # Dwell time
            else:
                # for laser this will disable the laser
                self.gcode += self.doformat(p.lift_code, x=self.oldx, y=self.oldy)  # Move (up) to travel height

        total_travel = 0.0
        total_cut = 0.0

        # Iterate over geometry paths getting the nearest each time.
        self.app.log.debug("Starting G-Code...")
        self.app.inform.emit('%s...' % _("Starting G-Code"))

        # variables to display the percentage of work done
        geo_len = len(temp_solid_geometry)

        old_disp_number = 0
        self.app.log.debug("Number of paths for which to generate GCode: %s" % str(geo_len))

        current_tooldia = float('%.*f' % (self.decimals, float(self.tooldia)))

        self.app.inform.emit(
            '%s: %s%s.' % (_("Starting G-Code for tool with diameter"), str(current_tooldia), str(self.units))
        )

        path_count = 0
        current_pt = (0, 0)

        self.warn_cut_through_exclusion([geo for __, geo in optimized_path], current_tooldia)

        # the chunks are joined 1 time: a str += in a loop copies the whole string each time
        path_parts = []
        for pt, geo in optimized_path:
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            path_count += 1

            # If last point in geometry is the nearest but prefer the first one if last point == first point
            # then reverse coordinates.
            if pt != geo.coords[0] and pt == geo.coords[-1]:
                # geo.coords = list(geo.coords)[::-1] # Shapely 2.0
                geo = LineString(list(geo.coords)[::-1])

            # ---------- Single depth/pass --------
            if not multidepth:
                # calculate the cut distance
                total_cut += geo.length
                path_parts.append(self.create_gcode_single_pass(geo, current_tooldia, extracut, self.extracut_length,
                                                                tolerance, z_move=self.z_move, old_point=current_pt))

            # --------- Multi-pass ---------
            else:
                # calculate the cut distance
                # due of the number of cuts (multi depth) it has to multiplied by the number of cuts
                nr_cuts = self.count_depth_passes()

                total_cut += (geo.length * nr_cuts)

                gc, geo = self.create_gcode_multi_pass(geo, current_tooldia, extracut, self.extracut_length,
                                                       tolerance, z_move=self.z_move, postproc=p,
                                                       old_point=current_pt)
                path_parts.append(gc)

            # calculate the travel distance
            total_travel += abs(distance(pt1=current_pt, pt2=pt))
            # the next travel starts where this path ends
            current_pt = geo.coords[-1]

            # update the activity counter (lower left side of the app, status bar)
            disp_number = int(np.interp(path_count, [0, geo_len], [0, 100]))
            if old_disp_number < disp_number <= 100:
                self.app.proc_container.update_view_text(' %d%%' % disp_number)
                old_disp_number = disp_number
        self.gcode += ''.join(path_parts)

        self.app.log.debug("Finishing G-Code... %s paths traced." % path_count)

        # add move to end position
        total_travel += abs(distance_euclidian(current_pt[0], current_pt[1], 0, 0))
        self.travel_distance += total_travel + total_cut
        self.routing_time += total_cut / self.feedrate

        # Finish
        if 'laser' not in self.pp_geometry_name:
            self.gcode += self.doformat(p.spindle_stop_code)
            self.gcode += self.doformat(p.lift_code, x=current_pt[0], y=current_pt[1])
        else:
            self.gcode += self.doformat(p.lift_code, x=current_pt[0], y=current_pt[1])
            self.gcode += self.doformat(p.spindle_stop_code)

        self.gcode += self.doformat(p.end_code, x=0, y=0)
        self.app.inform.emit(
            '%s... %s %s.' % (_("Finished G-Code generation"), str(path_count), _("paths traced"))
        )
        self.laser_min_power_warning(self.gcode[tool_gcode_start:])

        if self.gcode == '':
            return 'fail', start_gcode

        return self.gcode, start_gcode

    def generate_gcode_from_solder_paste_geo(self, is_first=False, **kwargs):
        """
               Algorithm to generate from multitool Geometry.

               Algorithm description:
               ----------------------
               Uses RTree to find the nearest path to follow.

               :return: Gcode string
               """

        self.app.log.debug("Generate_from_solderpaste_geometry()")

        if kwargs.get('data', {}).get('tools_solderpaste_volume_mode', 'Legacy') != 'Legacy':
            from appCommon.solderpaste import generate_program
            self.gcode = ''
            self.gcode = generate_program(self, kwargs, is_first=is_first)
            return self.gcode

        # ## Index first and last points in paths
        # What points to index.
        def get_pts(o):
            return [o.coords[0], o.coords[-1]]

        self.gcode = ""

        if not kwargs:
            self.app.log.debug("camlib.generate_from_solderpaste_geo() --> No tool in the solderpaste geometry.")
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("There is no tool data in the SolderPaste geometry."))
            return 'fail'

        # this is the tool diameter, it is used as such to accommodate the preprocessor who need the tool diameter
        # given under the name 'toolC'
        self.postdata['toolC'] = kwargs['tooldia']

        self.postdata['z_start'] = kwargs['data']['tools_solderpaste_z_start']
        self.postdata['z_dispense'] = kwargs['data']['tools_solderpaste_z_dispense']
        self.postdata['z_stop'] = kwargs['data']['tools_solderpaste_z_stop']
        self.postdata['z_travel'] = kwargs['data']['tools_solderpaste_z_travel']
        self.postdata['z_toolchange'] = kwargs['data']['tools_solderpaste_z_toolchange']
        self.postdata['xy_toolchange'] = kwargs['data']['tools_solderpaste_xy_toolchange']
        self.postdata['frxy'] = kwargs['data']['tools_solderpaste_frxy']
        self.postdata['fr_rapids'] = kwargs['data']['tools_solderpaste_fr_rapids']
        self.postdata['frz'] = kwargs['data']['tools_solderpaste_frz']
        self.postdata['frz_dispense'] = kwargs['data']['tools_solderpaste_frz_dispense']
        self.postdata['speedfwd'] = kwargs['data']['tools_solderpaste_speedfwd']
        self.postdata['dwellfwd'] = kwargs['data']['tools_solderpaste_dwellfwd']
        self.postdata['speedrev'] = kwargs['data']['tools_solderpaste_speedrev']
        self.postdata['dwellrev'] = kwargs['data']['tools_solderpaste_dwellrev']
        self.postdata['mach3_dwell'] = kwargs['data'].get('tools_solderpaste_mach3_dwell', 'Seconds')
        self.postdata['pp_solderpaste_name'] = kwargs['data']['tools_solderpaste_pp']

        self.pp_solderpaste_name = kwargs['data']['tools_solderpaste_pp'] if kwargs['data']['tools_solderpaste_pp'] \
            else self._app_option('tools_solderpaste_pp')
        p = get_preprocessor(self.app, self.pp_solderpaste_name)

        # ## Flatten the geometry. Only linear elements (no polygons) remain.
        flat_geometry = self.flatten(kwargs['solid_geometry'], pathonly=True)
        self.app.log.debug("%d paths" % len(flat_geometry))
        if not flat_geometry:
            return 'fail'

        # Create the indexed storage.
        storage = AppRTreeStorage()
        storage.get_points = get_pts

        # Store the geometry
        self.app.log.debug("Indexing geometry before generating G-Code...")
        new_shapes = []
        for geo_shape in flat_geometry:
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            if geo_shape is not None:
                new_shapes.append(geo_shape)
        # 1 batch (bulk load)
        storage.insert_many(new_shapes)

        # Initial G-Code
        gcode = []
        if is_first:
            gcode.append(self.doformat(p.start_code))
        gcode.append(self.doformat(p.spindle_off_code))
        gcode.append(self.doformat(p.toolchange_code))

        # ## Iterate over geometry paths getting the nearest each time.
        self.app.log.debug("Starting SolderPaste G-Code...")
        path_count = 0
        current_pt = (0, 0)

        # variables to display the percentage of work done
        geo_len = len(flat_geometry)
        old_disp_number = 0

        pt, geo = storage.nearest(current_pt)

        try:
            while True:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                path_count += 1

                # Remove before modifying, otherwise deletion will fail.
                storage.remove(geo)

                # If last point in geometry is the nearest but prefer the first one if last point == first point
                # then reverse coordinates.
                if pt != geo.coords[0] and pt == geo.coords[-1]:
                    # geo.coords = list(geo.coords)[::-1] # Shapely 2.0
                    geo = LineString(list(geo.coords)[::-1])

                gcode.append(self.create_repasted_gcode(geo, p=p, old_point=current_pt))
                current_pt = geo.coords[-1]

                disp_number = path_count * 100 // geo_len
                if old_disp_number < disp_number <= 100:
                    self.app.proc_container.update_view_text(' %d%%' % disp_number)
                    old_disp_number = disp_number
                pt, geo = storage.nearest(current_pt)  # Next
        except StopIteration:  # Nothing found in storage.
            pass

        self.app.log.debug("Finishing SolderPaste G-Code... %s paths traced." % path_count)
        self.app.inform.emit(
            '%s... %s %s.' % (_("Finished SolderPaste G-Code generation"), str(path_count), _("paths traced"))
        )

        # Finish
        gcode.append(self.doformat(p.lift_code))
        gcode.append(self.doformat(p.end_code))
        self.gcode = ''.join(gcode)

        return self.gcode

    def create_repasted_gcode(self, geometry, p, old_point=(0, 0)):
        gcode = ''
        path = geometry.coords

        # Paste preprocessors emit G90, independent of the milling coordinate preference.
        first_x = path[0][0]
        first_y = path[0][1]

        if isinstance(geometry, LineString) or isinstance(geometry, LinearRing):
            # Move fast to 1st point
            gcode += self.doformat(p.rapid_code, x=first_x, y=first_y)  # Move to first point

            # Move down to cutting depth
            gcode += self.doformat(p.z_feedrate_code)
            gcode += self.doformat(p.down_z_start_code)
            gcode += self.doformat(p.spindle_fwd_code)  # Start dispensing
            gcode += self.doformat(p.dwell_fwd_code)
            gcode += self.doformat(p.feedrate_z_dispense_code)
            gcode += self.doformat(p.lift_z_dispense_code)
            gcode += self.doformat(p.feedrate_xy_code)

            # Cutting...
            # the chunks are joined 1 time: a str += in a loop copies the whole string each time
            cut_parts = []
            for pt in path[1:]:
                next_x = pt[0]
                next_y = pt[1]
                cut_parts.append(self.doformat(p.linear_code, x=next_x, y=next_y))  # Linear motion to point
            gcode += ''.join(cut_parts)

            # Up to travelling height.
            gcode += self.doformat(p.spindle_off_code)  # Stop dispensing
            gcode += self.doformat(p.spindle_rev_code)
            gcode += self.doformat(p.down_z_stop_code)
            gcode += self.doformat(p.dwell_rev_code)
            gcode += self.doformat(p.spindle_off_code)
            gcode += self.doformat(p.z_feedrate_code)
            gcode += self.doformat(p.lift_code)
        elif isinstance(geometry, Point):
            gcode += self.doformat(p.rapid_code, x=first_x, y=first_y)  # Move to first point

            gcode += self.doformat(p.feedrate_z_dispense_code)
            gcode += self.doformat(p.down_z_start_code)
            gcode += self.doformat(p.spindle_fwd_code)  # Start dispensing
            gcode += self.doformat(p.dwell_fwd_code)
            gcode += self.doformat(p.lift_z_dispense_code)

            gcode += self.doformat(p.spindle_off_code)  # Stop dispensing
            gcode += self.doformat(p.spindle_rev_code)
            gcode += self.doformat(p.down_z_stop_code)
            gcode += self.doformat(p.dwell_rev_code)
            gcode += self.doformat(p.spindle_off_code)
            gcode += self.doformat(p.z_feedrate_code)
            gcode += self.doformat(p.lift_code)
        return gcode

    def create_gcode_single_pass(self, geometry, cdia, extracut, extracut_length, tolerance, z_move, old_point=(0, 0)):
        """
        # G-code. Note: self.linear2gcode() and self.point2gcode() will lower and raise the tool every time.

        :param geometry:            A Shapely Geometry (LineString or LinearRing) which is the path to be cut
        :type geometry:             LineString, LinearRing
        :param cdia:                Tool diameter
        :type cdia:                 float
        :param extracut:            Will add an extra cut over the point where start of the cut is met with the end cut
        :type extracut:             bool
        :param extracut_length:     The length of the extra cut: half before the meeting point, half after
        :type extracut_length:      float
        :param tolerance:           Tolerance used to simplify the paths (making them mre rough)
        :type tolerance:            float
        :param z_move:              Travel Z
        :type z_move:               float
        :param old_point:           Previous point
        :type old_point:            tuple
        :return:                    Gcode
        :rtype:                     str
        """
        # p = postproc

        if isinstance(geometry, LineString) or isinstance(geometry, LinearRing):
            if extracut is False or not geometry.is_ring:
                gcode_single_pass = self.linear2gcode(geometry, cdia, z_move=z_move, tolerance=tolerance,
                                                      old_point=old_point)
            else:
                gcode_single_pass = self.linear2gcode_extra(geometry, cdia, extracut_length, tolerance=tolerance,
                                                            z_move=z_move, old_point=old_point)

        elif isinstance(geometry, Point):
            gcode_single_pass = self.point2gcode(geometry, cdia, z_move=z_move, old_point=old_point)
        else:
            self.app.log.warning("G-code generation not implemented for %s" % (str(type(geometry))))
            return

        return gcode_single_pass

    def create_gcode_multi_pass(self, geometry, cdia, extracut, extracut_length, tolerance, postproc, z_move,
                                old_point=(0, 0)):
        """

        :param geometry:            A Shapely Geometry (LineString or LinearRing) which is the path to be cut
        :type geometry:             LineString, LinearRing
        :param cdia:                Tool diameter
        :type cdia:                 float
        :param extracut:            Will add an extra cut over the point where start of the cut is met with the end cut
        :type extracut:             bool
        :param extracut_length:     The length of the extra cut: half before the meeting point, half after
        :type extracut_length:      float
        :param tolerance:           Tolerance used to simplify the paths (making them mre rough)
        :type tolerance:            float
        :param postproc:            Preprocessor class
        :type postproc:             Callable
        :param z_move:              Travel Z
        :type z_move:               float
        :param old_point:           Previous point
        :type old_point:            tuple
        :return:                    Gcode
        :rtype:                     str
        """
        p = postproc

        gcode_multi_pass = ''

        if isinstance(self.z_cut, Decimal):
            z_cut = self.z_cut
        else:
            z_cut = Decimal(self.z_cut).quantize(Decimal('0.000000001'))

        if not self.z_depthpercut:
            # a zero (or missing) step would never reach the Cut Z: cut it in one pass
            self.z_depthpercut = abs(z_cut)
        elif not isinstance(self.z_depthpercut, Decimal):
            self.z_depthpercut = abs(Decimal(self.z_depthpercut).quantize(Decimal('0.000000001')))

        depth = 0
        reverse = False
        # the chunks are joined 1 time: a str += in a loop copies the whole string each time
        pass_parts = []
        while depth > z_cut:

            # Increase depth. Limit to z_cut.
            depth -= self.z_depthpercut
            if depth < z_cut:
                depth = z_cut

            # Cut at specific depth and do not lift the tool.
            # Note: linear2gcode() will use G00 to move to the first point in the path, but it should be already
            # at the first point if the tool is down (in the material).  So, an extra G00 should show up but
            # is inconsequential.
            # the preprocessors get the depth as a float: a Decimal fails in float arithmetic (Roland, ISEL ICP)
            if isinstance(geometry, LineString) or isinstance(geometry, LinearRing):
                if extracut is False or not geometry.is_ring:
                    pass_parts.append(self.linear2gcode(geometry, cdia, tolerance=tolerance, z_cut=float(depth),
                                                          up=False, z_move=z_move, old_point=old_point))
                else:
                    pass_parts.append(self.linear2gcode_extra(geometry, cdia, extracut_length, tolerance=tolerance,
                                                                z_move=z_move, z_cut=float(depth), up=False,
                                                                old_point=old_point))

            # Ignore multi-pass for points.
            elif isinstance(geometry, Point):
                pass_parts.append(self.point2gcode(geometry, cdia, z_move=z_move, old_point=old_point))
                break  # Ignoring ...
            else:
                self.app.log.warning("G-code generation not implemented for %s" % (str(type(geometry))))

            # Reverse coordinates if not a loop, so we can continue cutting without returning to the beginning.
            if isinstance(geometry, LineString):
                geometry = LineString(list(geometry.coords)[::-1])
                reverse = True
        gcode_multi_pass += ''.join(pass_parts)

        # If geometry is reversed, revert.
        if reverse:
            if isinstance(geometry, LineString):
                geometry = LineString(list(geometry.coords)[::-1])

        # Lift the tool
        gcode_multi_pass += self.doformat(p.lift_code, x=old_point[0], y=old_point[1])
        return gcode_multi_pass, geometry

    def codes_split(self, gline):
        """
        Parses a line of G-Code such as "G01 X1234 Y987" into
        a dictionary: {'G': 1.0, 'X': 1234.0, 'Y': 987.0}

        :param gline:       G-Code line string
        :type gline:        str
        :return:            Dictionary with parsed line.
        :rtype:             dict
        """

        command = {}

        if self.pp_solderpaste_name is not None:
            if gline.strip() in ('(PASTE_ON)', ';PASTE_ON'):
                return {'Z': 0}
            if gline.strip() in ('(PASTE_OFF)', ';PASTE_OFF'):
                return {'Z': 1}
            dot = re.match(r'^[;(]PASTE_DOT X([+\-\d.]+) Y([+\-\d.]+) D([+\-\d.]+)', gline)
            if dot:
                return {'X': float(dot[1]), 'Y': float(dot[2]), '_paste_dot': float(dot[3])}

        if 'Roland' in self.pp_excellon_name or 'Roland' in self.pp_geometry_name:
            match_z = GCODE_ROLAND_Z_RE.search(gline)
            if match_z:
                command['G'] = 0
                command['X'] = float(match_z.group(1).replace(" ", "")) * 0.01
                command['Y'] = float(match_z.group(2).replace(" ", "")) * 0.01
                command['Z'] = float(match_z.group(3).replace(" ", "")) * 0.025

        elif 'hpgl' in self.pp_excellon_name or 'hpgl' in self.pp_geometry_name:
            match_pa = GCODE_HPGL_PA_RE.search(gline)
            if match_pa:
                command['G'] = 0
                command['X'] = float(match_pa.group(1).replace(" ", "")) / 40
                command['Y'] = float(match_pa.group(2).replace(" ", "")) / 40
            match_pen = GCODE_HPGL_PEN_RE.search(gline)
            if match_pen:
                if match_pen.group(1) == 'PU':
                    # the value does not matter, only that it is positive so the gcode_parse() know it is > 0,
                    # therefore the move is of kind T (travel)
                    command['Z'] = 1
                else:
                    command['Z'] = 0
            match_toolchange = GCODE_HPGL_SP_RE.search(gline)
            if match_toolchange:
                command['Z'] = 1

        elif 'laser' in self.pp_excellon_name.lower() or 'laser' in self.pp_geometry_name.lower() or \
                (self.pp_solderpaste_name is not None and 'paste' in self.pp_solderpaste_name.lower()):
            # (the coordinates are searched only in a line that has an X)
            match_lsr = GCODE_XY_RE.search(gline) if 'X' in gline else None
            if match_lsr:
                command['X'] = float(match_lsr.group(1).replace(" ", ""))
                command['Y'] = float(match_lsr.group(2).replace(" ", ""))

            match_lsr_pos = GCODE_LASER_M345_RE.match(gline)
            if match_lsr_pos:
                if 'M05' in match_lsr_pos.group(1) or 'M5' in match_lsr_pos.group(1):
                    # the value does not matter, only that it is positive so the gcode_parse() know it is > 0,
                    # therefore the move is of kind T (travel)
                    command['Z'] = 1
                else:
                    command['Z'] = 0

            match_lsr_pos_2 = GCODE_LASER_M10X_RE.match(gline)
            if match_lsr_pos_2:
                if 'M107' in match_lsr_pos_2.group(1):
                    command['Z'] = 1
                else:
                    command['Z'] = 0

            if 'laser OFF' in gline:
                command['Z'] = 1

        elif self.pp_solderpaste_name is not None:
            if 'Paste' in self.pp_solderpaste_name:
                match_paste = GCODE_XY_RE.search(gline)
                if match_paste:
                    command['X'] = float(match_paste.group(1).replace(" ", ""))
                    command['Y'] = float(match_paste.group(2).replace(" ", ""))
        else:
            # the comments are not read and the words may be in lower case
            if '(' in gline:
                gline = GCODE_PAREN_COMMENT_RE.sub('', gline)
            if ';' in gline:
                gline = gline.split(';', 1)[0]
            # the words are matched one after the other, from the end of the previous one
            g_words = []
            match = GCODE_WORD_ANYCASE_RE.match(gline)
            while match:
                letter = match.group(1)
                if letter == 'e':
                    break       # the exponent of a number (1e3), not a word
                value = float(match.group(2).replace(" ", ""))
                letter = letter.upper()
                if letter == 'G':
                    g_words.append(value)
                command[letter] = value
                match = GCODE_WORD_ANYCASE_RE.match(gline, match.end())
            if len(g_words) > 1:
                # 'G' keeps the last one; the other G words of the same line are in '_G' (in order)
                command['_G'] = g_words
        return command

    @staticmethod
    def arc_centre_from_radius(x0, y0, x1, y1, radius, ccw):
        """
        The centre of an arc given by its radius (G2/G3 with R): R < 0 is the arc bigger than a half circle.

        :return:    (I, J): the centre relative to the start point (0, 0 if the start is the end)
        """
        dx, dy = x1 - x0, y1 - y0
        dist = float(np.hypot(dx, dy))
        if dist == 0.0:
            return 0.0, 0.0
        height = float(np.sqrt(max(radius * radius - dist * dist / 4.0, 0.0)))
        side = 1.0 if ccw else -1.0
        if radius < 0:
            side = -side
        return dx / 2.0 + side * height * (-dy / dist), dy / 2.0 + side * height * (dx / dist)

    def gcode_parse(self, force_parsing=None, tool_data=None):
        """
        G-Code parser (from self.gcode). Generates dictionary with
        single-segment LineString's and "kind" indicating cut or travel,
        fast or feedrate speed.

        Will return a list of dict in the format:
        {
            "geom": LineString(path),
            "kind": kind
        }
        where kind can be either ["C", "F"]  # T=travel, C=cut, F=fast, S=slow

        :param force_parsing:
        :type force_parsing:
        :param tool_data:       when dealing with multi tool objects we need the tool data
        :type tool_data:        dict
        :return:
        :rtype:                 list
        """

        if getattr(self, 'gcode_imported', False):
            # the G-code of an imported file is any G-code: the import parser reads it
            from appParsers.ParseGCode import parse_gcode
            result = parse_gcode(self.gcode, steps_per_circle=int(self.steps_per_circle),
                                 default_tooldia=self.tooldia or 0.4, target_units=self.units)
            flat = [item for tool_data_parsed in result['tools'].values() for item in tool_data_parsed['gcode_parsed']]
            self.gcode_parsed = flat
            return flat

        kind = ["C", "F"]  # T=travel, C=cut, F=fast, S=slow

        # Results go here
        geometry = []

        # Last known instruction
        current = {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'G': 0}

        # G90 / G91 (X, Y, Z) and G90.1 / G91.1 (arc centre)
        incremental = False
        arc_centre_absolute = False

        if tool_data is None:
            toolchange_xy_mill = self._app_option("tools_mill_toolchangexy")
            toolchange_xy_drill = self._app_option("tools_drill_toolchangexy")
        else:
            if tool_data.get("tools_mill_toolchange") is True:
                toolchange_xy_mill = tool_data.get("tools_mill_toolchangexy", (0, 0))
            else:
                toolchange_xy_mill = (0, 0)

            if "tools_drill_toolchange" in tool_data and tool_data["tools_drill_toolchange"] is True:
                toolchange_xy_drill = tool_data["tools_drill_toolchangexy"]
            else:
                toolchange_xy_drill = (0, 0)

        # Current path: temporary storage until tool is
        # lifted or lowered.
        if self.pp_solderpaste_name is not None:
            from appCommon.solderpaste import parse_numbers
            pos_xy = parse_numbers(tool_data.get('tools_solderpaste_xy_toolchange', self.xy_toolchange)
                                   if tool_data else self.xy_toolchange, count=2, allow_empty=True) or (0, 0)
        elif self.obj_options['type'].lower() == "excellon":
            if toolchange_xy_drill == '' or toolchange_xy_drill is None:
                pos_xy = (0, 0)
            else:
                pos_xy = toolchange_xy_drill
                # if it's a string
                try:
                    pos_xy = [float(safe_eval(a)) for a in pos_xy.split(",")]
                except Exception:
                    if len(pos_xy) != 2:
                        pos_xy = (0, 0)
        else:
            if toolchange_xy_mill == '' or toolchange_xy_mill is None:
                pos_xy = (0, 0)
            else:
                pos_xy = toolchange_xy_mill
                # if it's a string
                try:
                    pos_xy = [float(safe_eval(a)) for a in pos_xy.split(",")]
                except Exception:
                    if len(pos_xy) != 2:
                        pos_xy = (0, 0)

        path = [pos_xy]
        # path = [(0, 0)]

        gcode_lines_list = self.gcode.splitlines()
        self.app.inform.emit('%s: %d' % (_("Parsing GCode file. Number of lines"), len(gcode_lines_list)))

        # Excellon: the drill diameter of every drill position (the first tool that has the position wins)
        is_excellon = self.obj_options['type'].lower() == 'excellon'
        drill_diameters = {}
        if is_excellon:
            for tool_dict in self.exc_tools.values():
                if 'drills' in tool_dict and 'tooldia' in tool_dict:
                    for drill_pt in tool_dict['drills']:
                        drill_diameters.setdefault((
                            float('%.*f' % (self.decimals, drill_pt.x)),
                            float('%.*f' % (self.decimals, drill_pt.y))
                        ), tool_dict['tooldia'])

        # Process every instruction
        for line in gcode_lines_list:
            if force_parsing is False or force_parsing is None:
                if '%MO' in line or '%' in line or 'MOIN' in line or 'MOMM' in line:
                    return "fail"

            gobj = self.codes_split(line)

            if '_paste_dot' in gobj:
                geometry.append({'geom': Point(gobj['X'], gobj['Y']).buffer(gobj['_paste_dot'] / 2).exterior,
                                 'kind': ['C', 'S']})
                continue

            # ## G words: units, distance mode, arc centre mode, motion mode (a line can have more than one)
            motion = None
            skip_motion = False
            for g_word in (gobj['_G'] if '_G' in gobj else ((gobj['G'],) if 'G' in gobj else ())):
                if g_word == 20.0 or g_word == 21.0:
                    self.units = {20.0: "IN", 21.0: "MM"}[g_word]
                elif g_word == 90.0:
                    incremental = False
                elif g_word == 91.0:
                    incremental = True
                elif g_word == 90.1:
                    arc_centre_absolute = True
                elif g_word == 91.1:
                    arc_centre_absolute = False
                elif g_word in (0.0, 1.0, 2.0, 3.0):
                    motion = int(g_word)
                elif g_word in (28.0, 30.0, 53.0):
                    skip_motion = True
            if skip_motion:
                continue

            # TODO take into consideration the tools and update the travel line thickness
            if 'T' in gobj:
                pass

            # ## Changing height
            if 'Z' in gobj:
                new_z = current['Z'] + gobj['Z'] if incremental else gobj['Z']
                if 'Roland' in self.pp_excellon_name or 'Roland' in self.pp_geometry_name:
                    pass
                elif 'hpgl' in self.pp_excellon_name or 'hpgl' in self.pp_geometry_name:
                    pass
                elif 'laser' in self.pp_excellon_name or 'laser' in self.pp_geometry_name:
                    pass
                elif ('X' in gobj or 'Y' in gobj) and new_z != current['Z']:
                    if self.pp_geometry_name == 'Line_xyz' or self.pp_excellon_name == 'Line_xyz':
                        pass
                    else:
                        self.app.log.warning("Non-orthogonal motion: From %s" % str(current))
                        self.app.log.warning("  To: %s" % str(gobj))

                current['Z'] = new_z
                # Store the path into geometry and reset path
                if len(path) > 1:
                    geometry.append({"geom": LineString(path),
                                     "kind": kind})
                    path = [path[-1]]  # Start with the last point of last path.

                # create the geometry for the holes created when drilling Excellon drills
                if is_excellon and current['Z'] < 0:
                    current_drill_point_coords = (
                        float('%.*f' % (self.decimals, current['X'])),
                        float('%.*f' % (self.decimals, current['Y']))
                    )

                    # the drill diameter knowing the drill coordinates
                    dia = drill_diameters.get(current_drill_point_coords)
                    if dia is not None:
                        kind = ['C', 'F']
                        geometry.append(
                            {
                                "geom": Point(current_drill_point_coords).buffer(dia / 2.0).exterior,
                                "kind": kind
                            }
                        )

            # only G0 - G3 change the motion mode
            if motion is not None:
                current['G'] = motion

            if 'X' in gobj or 'Y' in gobj:
                if 'X' in gobj:
                    x = current['X'] + gobj['X'] if incremental else gobj['X']
                    # current['X'] = x
                else:
                    x = current['X']

                if 'Y' in gobj:
                    y = current['Y'] + gobj['Y'] if incremental else gobj['Y']
                else:
                    y = current['Y']

                kind = ["C", "F"]  # T=travel, C=cut, F=fast, S=slow

                if current['Z'] > 0:
                    kind[0] = 'T'
                if current['G'] > 0:
                    kind[1] = 'S'

                if current['G'] in [0, 1]:  # line
                    path.append((x, y))

                arcdir = [None, None, "cw", "ccw"]
                if current['G'] in [2, 3]:  # arc
                    # a missing I or J is 0; the centre can also come from the radius R
                    arc_i = gobj.get('I', 0.0)
                    arc_j = gobj.get('J', 0.0)
                    if arc_centre_absolute and ('I' in gobj or 'J' in gobj):
                        arc_i -= current['X']
                        arc_j -= current['Y']
                    if 'R' in gobj and 'I' not in gobj and 'J' not in gobj:
                        arc_i, arc_j = self.arc_centre_from_radius(
                            current['X'], current['Y'], x, y, gobj['R'], current['G'] == 3)
                    center = [arc_i + current['X'], arc_j + current['Y']]
                    radius = np.sqrt(arc_i ** 2 + arc_j ** 2)
                    start = np.arctan2(-arc_j + 0.0, -arc_i)    # (+ 0.0: no -0.0, it would turn pi into -pi)
                    stop = np.arctan2(-center[1] + y, -center[0] + x)
                    path += arc(center, radius, start, stop, arcdir[current['G']], int(self.steps_per_circle))

                current['X'] = x
                current['Y'] = y

            # Update current instruction (the position, the height and the motion mode are set above)
            for code in gobj:
                if code not in ('X', 'Y', 'Z', 'G', '_G'):
                    current[code] = gobj[code]

        self.app.inform.emit('%s...' % _("Creating Geometry from the parsed GCode file. "))
        # There might not be a change in height at the
        # end, therefore, see here too if there is
        # a final path.
        if len(path) > 1:
            geometry.append(
                {
                    "geom": LineString(path),
                    "kind": kind
                }
            )

        self.gcode_parsed = geometry
        return geometry

    def excellon_tool_gcode_parse(self, dia, gcode, start_pt=(0, 0), force_parsing=None):
        """
        G-Code parser (from "self.tools['tool_id']['gcode']"). For Excellon. Generates dictionary with
        single-segment LineString's and "kind" indicating cut or travel,
        fast or feedrate speed.

        Will return the Geometry as a list of dict in the format:
        {
            "geom": LineString(path),
            "kind": kind
        }
        where kind can be either ["C", "F"]  # T=travel, C=cut, F=fast, S=slow

        :param dia:             the dia is a tool diameter which is the key in self.tools dict attribute of Excellon
        :type dia:              float
        :param gcode:           Gcode to parse
        :type gcode:            str
        :param start_pt:        the point coordinates from where to start the parsing
        :type start_pt:         tuple
        :param force_parsing:
        :type force_parsing:    bool
        :return:                Geometry as a list of dictionaries
        :rtype:                 list
        """

        kind = ["C", "F"]  # T=travel, C=cut, F=fast, S=slow

        # Results go here
        geometry = []

        # Last known instruction
        current = {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'G': 0}

        # Current path: temporary storage until tool is
        # lifted or lowered.
        pos_xy = start_pt

        path = [pos_xy]
        # path = [(0, 0)]

        gcode_lines_list = gcode.splitlines()
        self.app.inform.emit(
            '%s: %s. %s: %d' % (_("Parsing GCode file for tool diameter"),
                                str(dia), _("Number of lines"),
                                len(gcode_lines_list))
        )

        # Process every instruction
        for line in gcode_lines_list:
            if force_parsing is False or force_parsing is None:
                if '%MO' in line or '%' in line or 'MOIN' in line or 'MOMM' in line:
                    return "fail"

            gobj = self.codes_split(line)

            # ## Units
            if 'G' in gobj and (gobj['G'] == 20.0 or gobj['G'] == 21.0):
                self.units = {20.0: "IN", 21.0: "MM"}[gobj['G']]
                continue

            # TODO take into consideration the tools and update the travel line thickness
            if 'T' in gobj:
                pass

            # ## Changing height
            if 'Z' in gobj:
                if 'Roland' in self.pp_excellon_name or 'Roland' in self.pp_geometry_name:
                    pass
                elif 'hpgl' in self.pp_excellon_name or 'hpgl' in self.pp_geometry_name:
                    pass
                elif 'laser' in self.pp_excellon_name or 'laser' in self.pp_geometry_name:
                    pass
                elif ('X' in gobj or 'Y' in gobj) and gobj['Z'] != current['Z']:
                    if self.pp_geometry_name == 'Line_xyz' or self.pp_excellon_name == 'Line_xyz':
                        pass
                    else:
                        self.app.log.warning("Non-orthogonal motion: From %s" % str(current))
                        self.app.log.warning("  To: %s" % str(gobj))

                current['Z'] = gobj['Z']
                # Store the path into geometry and reset path
                if len(path) > 1:
                    geometry.append({"geom": LineString(path),
                                     "kind": kind})
                    path = [path[-1]]  # Start with the last point of last path.

                # create the geometry for the holes created when drilling Excellon drills
                if current['Z'] < 0:
                    current_drill_point_coords = (
                        float('%.*f' % (self.decimals, current['X'])),
                        float('%.*f' % (self.decimals, current['Y']))
                    )

                    kind = ['C', 'F']
                    geometry.append(
                        {
                            "geom": Point(current_drill_point_coords).buffer(dia/2.0).exterior,
                            "kind": kind
                        }
                    )

            if 'G' in gobj:
                current['G'] = int(gobj['G'])

            if 'X' in gobj or 'Y' in gobj:
                x = gobj['X'] if 'X' in gobj else current['X']
                y = gobj['Y'] if 'Y' in gobj else current['Y']

                kind = ["C", "F"]  # T=travel, C=cut, F=fast, S=slow

                if current['Z'] > 0:
                    kind[0] = 'T'
                if current['G'] > 0:
                    kind[1] = 'S'
                if current['G'] in [0, 1]:  # line
                    path.append((x, y))

                arcdir = [None, None, "cw", "ccw"]
                if current['G'] in [2, 3]:  # arc
                    # a missing I or J is 0; the centre can also come from the radius R
                    arc_i = gobj.get('I', 0.0)
                    arc_j = gobj.get('J', 0.0)
                    if 'R' in gobj and 'I' not in gobj and 'J' not in gobj:
                        arc_i, arc_j = self.arc_centre_from_radius(
                            current['X'], current['Y'], x, y, gobj['R'], current['G'] == 3)
                    center = [arc_i + current['X'], arc_j + current['Y']]
                    radius = np.sqrt(arc_i ** 2 + arc_j ** 2)
                    start = np.arctan2(-arc_j + 0.0, -arc_i)    # (+ 0.0: no -0.0, it would turn pi into -pi)
                    stop = np.arctan2(-center[1] + y, -center[0] + x)
                    path += arc(center, radius, start, stop, arcdir[current['G']], int(self.steps_per_circle))

                current['X'] = x
                current['Y'] = y

            # Update current instruction
            for code in gobj:
                current[code] = gobj[code]

        self.app.inform.emit('%s: %s' % (_("Creating Geometry from the parsed GCode file for tool diameter"), str(dia)))
        # There might not be a change in height at the end, therefore, see here too if there is a final path.
        if len(path) > 1:
            geometry.append(
                {
                    "geom": LineString(path),
                    "kind": kind
                }
            )
        return geometry

    def plot2(self, tooldia=None, dpi=75, margin=0.1, gcode_parsed: str = None,
              color=None, alpha={"T": 0.3, "C": 1.0}, tool_tolerance=0.0005, obj=None, visible=False, kind='all'):
        """
        Plots the G-code job onto the given axes.

        :param tooldia:             Tool diameter.
        :type tooldia:              float
        :param dpi:                 Not used!
        :type dpi:                  float
        :param margin:              Not used!
        :type margin:               float
        :param gcode_parsed:        Parsed Gcode
        :type gcode_parsed:         str
        :param color:               Color specification.
        :type color:                str
        :param alpha:               Transparency specification.
        :type alpha:                dict
        :param tool_tolerance:      Tolerance when drawing the toolshape.
        :type tool_tolerance:       float
        :param obj:                 The object for whih to plot
        :type obj:                  class
        :param visible:             Visibility status
        :type visible:              bool
        :param kind:                Can be: "travel", "cut", "all"
        :type kind:                 str
        :return:                    None
        :rtype:
        """
        # units = self.app.ui.general_pref_form.general_app_group.units_radio.get_value().upper()

        if color is None:
            color = {
                "T": [self._app_option("cncjob_travel_fill"), self._app_option("cncjob_travel_line")],
                "C": [self._app_option("cncjob_plot_fill"), self._app_option("cncjob_plot_line")]
            }

        gcode_parsed = gcode_parsed if gcode_parsed else self.gcode_parsed

        if tooldia is None:
            tooldia = self.tooldia

        # this should be unlikely unless when upstream the tooldia is a tuple made by one dia and a comma like (2.4)
        if isinstance(tooldia, list):
            tooldia = tooldia[0] if tooldia[0] is not None else self.tooldia

        if tooldia == 0:
            batch = []
            for geo in gcode_parsed:
                if not geo:
                    continue
                if kind == 'all':
                    batch.append({'shape': geo['geom'], 'color': color[geo['kind'][0]][1]})     # noqa
                elif kind == 'travel':
                    if geo['kind'][0] == 'T':   # noqa
                        batch.append({'shape': geo['geom'], 'color': color['T'][1]})    # noqa
                elif kind == 'cut':
                    if geo['kind'][0] == 'C':   # noqa
                        batch.append({'shape': geo['geom'], 'color': color['C'][1]})    # noqa
            if batch:
                obj.add_shapes_batch(batch, visible=visible)
        else:
            path_num = 0

            self.coordinates_type = self._app_option("cncjob_coords_type")
            if self.coordinates_type == "G90":
                # For Absolute coordinates type G90
                batch = []
                annotations = obj.annotations_dict.get(tooldia)
                annotation_positions = set(annotations['pos']) if annotations else set()
                is_excellon = self.obj_options['type'].lower() == 'excellon'
                for geo in gcode_parsed:
                    if not geo:
                        continue

                    if geo['kind'][0] == 'T':   # noqa
                        coords = geo['geom'].coords   # noqa
                        if annotations is None:
                            annotations = obj.annotations_dict[tooldia] = {'pos': [], 'text': []}
                        for position in (coords[0], coords[-1]):
                            if position not in annotation_positions:
                                annotation_positions.add(position)
                                path_num += 1
                                annotations['pos'].append(position)
                                annotations['text'].append(str(path_num))

                    if (kind == 'travel' and geo['kind'][0] != 'T') or \
                            (kind == 'cut' and geo['kind'][0] != 'C'):
                        continue

                    # plot the geometry of Excellon objects
                    if is_excellon:
                        try:
                            # if the geos are travel lines
                            if geo['kind'][0] == 'T':   # noqa
                                poly = geo['geom'].buffer((tooldia / 1.99999999), circle_quad_segs(self.steps_per_circle))
                            else:
                                poly = Polygon(geo['geom'])     # noqa

                            poly = poly.simplify(tool_tolerance)
                        except Exception:
                            # deal here with unexpected plot errors due of LineStrings not valid
                            continue
                    else:
                        # Buffer the selected paths together below, avoiding per-path Python calls.
                        poly = geo['geom']

                    # Collect into batch instead of add_shape
                    if kind == 'all':
                        batch.append({
                            'shape': poly,
                            'color': color[geo['kind'][0]][1],  # noqa
                            'face_color': color[geo['kind'][0]][0], # noqa
                            'layer': 1 if geo['kind'][0] == 'C' else 2  # noqa
                        })
                    elif kind == 'travel':
                        if geo['kind'][0] == 'T':   # noqa
                            batch.append({
                                'shape': poly, 'color': color['T'][1],
                                'face_color': color['T'][0], 'layer': 2
                            })
                    elif kind == 'cut':
                        if geo['kind'][0] == 'C':   # noqa
                            batch.append({
                                'shape': poly, 'color': color['C'][1],
                                'face_color': color['C'][0], 'layer': 1
                            })

                if batch and not is_excellon:
                    # Bound temporary GEOS arrays without changing path order or plot resolution.
                    for start in range(0, len(batch), 1024):
                        chunk = batch[start:start + 1024]
                        polygons = shapely.buffer(
                            [item['shape'] for item in chunk], tooldia / 1.99999999,
                            quad_segs=circle_quad_segs(self.steps_per_circle))
                        polygons = shapely.simplify(polygons, tool_tolerance)
                        for item, polygon in zip(chunk, polygons):
                            item['shape'] = polygon

                if batch:
                    obj.add_shapes_batch(batch, visible=visible)
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s...' % _('G91 coordinates not implemented'))
                return 'fail'

    def plot_annotations(self, obj, visible=True):
        """
        Plot annotations.

        :param obj:         FlatCAM CNCJob object for which to plot the annotations
        :type obj:
        :param visible:     annotations visibility
        :type visible:      bool
        :return:            Nothing
        :rtype:
        """

        if not obj.annotations_dict:
            return

        if visible is True:
            obj.annotation.clear(update=True)
            obj.text_col.visible = True
        else:
            obj.text_col.visible = False
            return

        text = []
        pos = []
        for tooldia in obj.annotations_dict:
            pos += obj.annotations_dict[tooldia]['pos']
            text += obj.annotations_dict[tooldia]['text']

        if not text or not pos:
            return

        try:
            if self._app_option('global_theme') in ['default', 'light']:
                obj.annotation.set(text=text, pos=pos, visible=obj.obj_options['plot'],
                                   font_size=self._app_option("cncjob_annotation_fontsize"),
                                   color=self._app_option("cncjob_annotation_fontcolor"))
            else:
                # invert the color
                old_color = self._app_option("cncjob_annotation_fontcolor").lower()
                new_color = ''
                code = {}
                l1 = "#;0123456789abcdef"
                l2 = "#;fedcba9876543210"
                for i in range(len(l1)):
                    code[l1[i]] = l2[i]

                for x in range(len(old_color)):
                    new_color += code[old_color[x]]

                obj.annotation.set(text=text, pos=pos, visible=obj.obj_options['plot'],
                                   font_size=self._app_option("cncjob_annotation_fontsize"),
                                   color=new_color)
        except Exception as e:
            self.app.log.error("CNCJob.plot2() --> annotations --> %s" % str(e))
            obj.annotation.clear(update=True)

        obj.annotation.redraw()

    def create_geometry(self):
        """
        It is used by the Excellon objects. Will create the solid_geometry which will be an attribute of the
        Excellon object class.

        :return:    List of Shapely geometry elements
        :rtype:     list
        """

        # This takes forever. Too much data?
        # self.app.inform.emit('%s: %s' % (_("Unifying Geometry from parsed Geometry segments"),
        #                                  str(len(self.gcode_parsed))))
        # self.solid_geometry = unary_union([geo['geom'] for geo in self.gcode_parsed])

        # This is much faster but not so nice to look at as you can see different segments of the geometry
        self.solid_geometry = [geo['geom'] for geo in self.gcode_parsed]

        return self.solid_geometry

    def segment(self, coords):
        """
        Break long linear lines to make it more auto level friendly.
        Code snippet added by Lei Zheng in a rejected pull request on FlatCAM https://bitbucket.org/realthunder/

        :param coords:  List of coordinates tuples
        :type coords:   list
        :return:        A path; list with the multiple coordinates breaking a line.
        :rtype:         list
        """

        if len(coords) < 2:
            return list(coords)
        if self.seg_x <= 0 and self.seg_y <= 0:
            return list(coords)

        # flag that the generated gcode was segmented for autolevelling
        if self.is_segmented_gcode is False:
            self.is_segmented_gcode = True

        path = [coords[0]]

        # break the line in either x or y dimension only
        def linebreak_single(line, dim, dmax):
            if dmax <= 0:
                return None

            if line[1][dim] > line[0][dim]:
                sign = 1.0
                d = line[1][dim] - line[0][dim]
            else:
                sign = -1.0
                d = line[0][dim] - line[1][dim]
            if d > dmax:
                # make sure we don't make any new lines too short
                if d > dmax * 2:
                    dd = dmax
                else:
                    dd = d / 2
                other = dim ^ 1
                return line[0][dim] + dd * sign, line[0][other] + dd * (line[1][other] - line[0][other]) / d
            return None

        # recursively breaks down a given line until it is within the
        # required step size
        def linebreak(line):
            pt_new = linebreak_single(line, 0, self.seg_x)
            if pt_new is None:
                pt_new2 = linebreak_single(line, 1, self.seg_y)
            else:
                pt_new2 = linebreak_single((line[0], pt_new), 1, self.seg_y)
            if pt_new2 is not None:
                pt_new = pt_new2[::-1]

            if pt_new is None:
                path.append(line[1])
            else:
                path.append(pt_new)
                linebreak((pt_new, line[1]))

        for pt in coords[1:]:
            linebreak((path[-1], pt))

        return path

    def linear2gcode(self, linear, dia, tolerance=0, down=True, up=True, z_cut=None, z_move=None, zdownrate=None,
                     feedrate=None, feedrate_z=None, feedrate_rapid=None, cont=False, old_point=(0, 0)):
        """

        Generates G-code to cut along the linear feature.

        :param linear:          The path to cut along.
        :type:                  Shapely.LinearRing or Shapely.Linear String
        :param dia:             The tool diameter that is going on the path
        :type dia:              float
        :param tolerance:       All points in the simplified object will be within the
                                tolerance distance of the original geometry.
        :type tolerance:        float
        :param down:
        :param up:
        :param z_cut:
        :param z_move:
        :param zdownrate:
        :param feedrate:        speed for cut on X - Y plane
        :param feedrate_z:      speed for cut on Z plane
        :param feedrate_rapid:  speed to move between cuts; usually is G0 but some CNC require to specify it
        :param cont:
        :param old_point:
        :return:                G-code to cut along the linear feature.
        """

        if z_cut is None:
            z_cut = self.z_cut

        if z_move is None:
            z_move = self.z_move
        #
        # if zdownrate is None:
        #     zdownrate = self.zdownrate

        if feedrate is None:
            feedrate = self.feedrate

        if feedrate_z is None:
            feedrate_z = self.z_feedrate

        if feedrate_rapid is None:
            feedrate_rapid = self.feedrate_rapid

        # Simplify paths?
        if tolerance > 0:
            target_linear = linear.simplify(tolerance)
        else:
            target_linear = linear

        gcode = ""

        # path = list(target_linear.coords)
        path = self.segment(target_linear.coords)

        p = self.pp_geometry

        self.coordinates_type = self._app_option("cncjob_coords_type")
        if self.coordinates_type == "G90":
            # For Absolute coordinates type G90
            first_x = path[0][0]
            first_y = path[0][1]
        else:
            # For Incremental coordinates type G91
            first_x = path[0][0] - old_point[0]
            first_y = path[0][1] - old_point[1]

        # Move fast to 1st point
        if not cont:
            current_tooldia = dia
            travels = self.milling_travels((old_point[0], old_point[1]), (first_x, first_y), current_tooldia,
                                           travel_z=z_move)
            prev_z = None
            for travel in travels:
                locx = travel[1][0]
                locy = travel[1][1]

                if travel[0] is not None:
                    # move to next point
                    gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                    # raise to safe Z (travel[0]) each time because safe Z may be different
                    self.z_move = travel[0]
                    gcode += self.doformat(p.lift_code, x=locx, y=locy)

                    # restore z_move
                    self.z_move = z_move
                else:
                    if prev_z is not None:
                        # move to next point
                        gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                        # we assume that previously the z_move was altered therefore raise to
                        # the travel_z (z_move)
                        self.z_move = z_move
                        gcode += self.doformat(p.lift_code, x=locx, y=locy)
                    else:
                        # move to next point
                        gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                # store prev_z
                prev_z = travel[0]

            # gcode += self.doformat(p.rapid_code, x=first_x, y=first_y)  # Move to first point

        # Move down to cutting depth
        if down:
            # Different feedrate for vertical cut?
            gcode += self.doformat(p.z_feedrate_code)
            # gcode += self.doformat(p.feedrate_code)
            gcode += self.doformat(p.down_code, x=first_x, y=first_y, z_cut=z_cut)
            gcode += self.doformat(p.feedrate_code, feedrate=feedrate)

        # Cutting...
        prev_x = first_x
        prev_y = first_y
        # the attributes are copied once for the path and only x, y change from a point to the next one (doformat()
        # copies all the attributes of this object for every line)
        attributes = AttrDict()
        attributes.update(self.postdata)
        attributes['z_cut'] = z_cut
        # the chunks are joined 1 time: a str += in a loop copies the whole string each time
        cut_parts = []
        for pt in path[1:]:
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            if self.coordinates_type == "G90":
                # For Absolute coordinates type G90
                next_x = pt[0]
                next_y = pt[1]
            else:
                # For Incremental coordinates type G91
                # next_x = pt[0] - prev_x
                # next_y = pt[1] - prev_y
                self.app.inform.emit('[ERROR_NOTCL] %s...' % _('G91 coordinates not implemented'))
                next_x = pt[0]
                next_y = pt[1]

            attributes['x'] = next_x
            attributes['y'] = next_y
            line = self.doformat_attributes(p.linear_code, attributes)  # Linear motion to point
            cut_parts.append("" if line is None else line + "\n")
            prev_x = pt[0]
            prev_y = pt[1]
        gcode += ''.join(cut_parts)

        # Up to travelling height.
        if up:
            gcode += self.doformat(p.lift_code, x=prev_x, y=prev_y, z_move=z_move)  # Stop cutting
        return gcode

    def linear2gcode_extra(self, linear, dia, extracut_length, tolerance=0, down=True, up=True,
                           z_cut=None, z_move=None, zdownrate=None,
                           feedrate=None, feedrate_z=None, feedrate_rapid=None, cont=False, old_point=(0, 0)):
        """

        Generates G-code to cut along the linear feature.

        :param linear:              The path to cut along.
        :type:                      Shapely.LinearRing or Shapely.Linear String
        :param dia:                 The tool diameter that is going on the path
        :type dia:                  float
        :param extracut_length:     how much to cut extra over the first point at the end of the path
        :param tolerance:           All points in the simplified object will be within the
                                    tolerance distance of the original geometry.
        :type tolerance:            float
        :param down:
        :param up:
        :param z_cut:
        :param z_move:
        :param zdownrate:
        :param feedrate:            speed for cut on X - Y plane
        :param feedrate_z:          speed for cut on Z plane
        :param feedrate_rapid:      speed to move between cuts; usually is G0 but some CNC require to specify it
        :param cont:
        :param old_point:
        :return:                    G-code to cut along the linear feature.
        :rtype:                     str
        """

        if z_cut is None:
            z_cut = self.z_cut

        if z_move is None:
            z_move = self.z_move
        #
        # if zdownrate is None:
        #     zdownrate = self.zdownrate

        if feedrate is None:
            feedrate = self.feedrate

        if feedrate_z is None:
            feedrate_z = self.z_feedrate

        if feedrate_rapid is None:
            feedrate_rapid = self.feedrate_rapid

        # Simplify paths?
        if tolerance > 0:
            target_linear = linear.simplify(tolerance)
        else:
            target_linear = linear

        gcode = ""

        # path = list(target_linear.coords)
        path = self.segment(target_linear.coords)
        p = self.pp_geometry

        self.coordinates_type = self._app_option("cncjob_coords_type")
        if self.coordinates_type == "G90":
            # For Absolute coordinates type G90
            first_x = path[0][0]
            first_y = path[0][1]
        else:
            # For Incremental coordinates type G91
            first_x = path[0][0] - old_point[0]
            first_y = path[0][1] - old_point[1]

        # Move fast to 1st point
        if not cont:
            current_tooldia = dia
            travels = self.milling_travels((old_point[0], old_point[1]), (first_x, first_y), current_tooldia,
                                           travel_z=z_move)
            prev_z = None
            for travel in travels:
                locx = travel[1][0]
                locy = travel[1][1]

                if travel[0] is not None:
                    # move to next point
                    gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                    # raise to safe Z (travel[0]) each time because safe Z may be different
                    self.z_move = travel[0]
                    gcode += self.doformat(p.lift_code, x=locx, y=locy)

                    # restore z_move
                    self.z_move = z_move
                else:
                    if prev_z is not None:
                        # move to next point
                        gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                        # we assume that previously the z_move was altered therefore raise to
                        # the travel_z (z_move)
                        self.z_move = z_move
                        gcode += self.doformat(p.lift_code, x=locx, y=locy)
                    else:
                        # move to next point
                        gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                # store prev_z
                prev_z = travel[0]

            # gcode += self.doformat(p.rapid_code, x=first_x, y=first_y)  # Move to first point

        # Move down to cutting depth
        if down:
            # Different feedrate for vertical cut?
            if self.z_feedrate is not None:
                gcode += self.doformat(p.z_feedrate_code)
                # gcode += self.doformat(p.feedrate_code)
                gcode += self.doformat(p.down_code, x=first_x, y=first_y, z_cut=z_cut)
                gcode += self.doformat(p.feedrate_code, feedrate=feedrate)
            else:
                gcode += self.doformat(p.down_code, x=first_x, y=first_y, z_cut=z_cut)  # Start cutting

        # Cutting...
        prev_x = first_x
        prev_y = first_y
        # the chunks are joined 1 time: a str += in a loop copies the whole string each time
        cut_parts = []
        for pt in path[1:]:
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            if self.coordinates_type == "G90":
                # For Absolute coordinates type G90
                next_x = pt[0]
                next_y = pt[1]
            else:
                # For Incremental coordinates type G91
                # For Incremental coordinates type G91
                # next_x = pt[0] - prev_x
                # next_y = pt[1] - prev_y
                self.app.inform.emit('[ERROR_NOTCL] %s...' % _('G91 coordinates not implemented'))
                next_x = pt[0]
                next_y = pt[1]

            cut_parts.append(self.doformat(p.linear_code, x=next_x, y=next_y, z_cut=z_cut))  # Linear motion to point
            prev_x = next_x
            prev_y = next_y
        gcode += ''.join(cut_parts)

        # this line is added to create an extra cut over the first point in patch
        # to make sure that we remove the copper leftovers
        # Linear motion to the 1st point in the cut path
        # if self.coordinates_type == "G90":
        #     # For Absolute coordinates type G90
        #     last_x = path[1][0]
        #     last_y = path[1][1]
        # else:
        #     # For Incremental coordinates type G91
        #     last_x = path[1][0] - first_x
        #     last_y = path[1][1] - first_y
        # gcode += self.doformat(p.linear_code, x=last_x, y=last_y)

        # the first point for extracut is always mandatory if the extracut is enabled. But if the length of distance
        # between point 0 and point 1 is more than the distance we set for the extra cut then make an interpolation
        # along the path and find the point at the distance extracut_length

        if extracut_length == 0.0:
            extra_path = [path[-1], path[0], path[1]]
            new_x = extra_path[0][0]
            new_y = extra_path[0][1]

            # this is an extra line therefore lift the milling bit
            gcode += self.doformat(p.lift_code, x=prev_x, y=prev_y, z_move=z_move)  # lift

            # move fast to the new first point
            gcode += self.doformat(p.rapid_code, x=new_x, y=new_y)

            # lower the milling bit
            # Different feedrate for vertical cut?
            if self.z_feedrate is not None:
                gcode += self.doformat(p.z_feedrate_code)
                gcode += self.doformat(p.down_code, x=new_x, y=new_y, z_cut=z_cut)
                gcode += self.doformat(p.feedrate_code, feedrate=feedrate)
            else:
                gcode += self.doformat(p.down_code, x=new_x, y=new_y, z_cut=z_cut)  # Start cutting

            # start cutting the extra line
            last_pt = extra_path[0]
            for pt in extra_path[1:]:
                gcode += self.doformat(p.linear_code, x=pt[0], y=pt[1], z_cut=z_cut)
                last_pt = pt

            # go back to the original point
            gcode += self.doformat(p.linear_code, x=path[0][0], y=path[0][1], z_cut=z_cut)
            last_pt = path[0]
        else:
            # go to the point that is 5% in length before the end (therefore 95% length from start of the line),
            # along the line to be cut
            if extracut_length >= target_linear.length:
                extracut_length = target_linear.length

            # ---------------------------------------------
            # first half
            # ---------------------------------------------
            start_length = target_linear.length - (extracut_length * 0.5)
            extra_line = substring(target_linear, start_length, target_linear.length)
            extra_path = list(extra_line.coords)
            new_x = extra_path[0][0]
            new_y = extra_path[0][1]

            # this is an extra line therefore lift the milling bit
            gcode += self.doformat(p.lift_code, x=prev_x, y=prev_y, z_move=z_move)  # lift

            # move fast to the new first point
            gcode += self.doformat(p.rapid_code, x=new_x, y=new_y)

            # lower the milling bit
            # Different feedrate for vertical cut?
            if self.z_feedrate is not None:
                gcode += self.doformat(p.z_feedrate_code)
                gcode += self.doformat(p.down_code, x=new_x, y=new_y, z_cut=z_cut)
                gcode += self.doformat(p.feedrate_code, feedrate=feedrate)
            else:
                gcode += self.doformat(p.down_code, x=new_x, y=new_y, z_cut=z_cut)  # Start cutting

            # start cutting the extra line
            for pt in extra_path[1:]:
                gcode += self.doformat(p.linear_code, x=pt[0], y=pt[1], z_cut=z_cut)

            # ---------------------------------------------
            # second half
            # ---------------------------------------------
            extra_line = substring(target_linear, 0, (extracut_length * 0.5))
            extra_path = list(extra_line.coords)

            # ---------------------------------------------
            # back to original start point, cutting
            # ---------------------------------------------
            extra_line = substring(target_linear, 0, (extracut_length * 0.5))
            extra_path = list(extra_line.coords)[::-1]

            # start cutting the extra line
            last_pt = extra_path[0]
            for pt in extra_path[1:]:
                gcode += self.doformat(p.linear_code, x=pt[0], y=pt[1], z_cut=z_cut)
                last_pt = pt

        # Up to travelling height.
        if up:
            gcode += self.doformat(p.lift_code, x=last_pt[0], y=last_pt[1], z_move=z_move)  # Stop cutting

        return gcode

    def point2gcode(self, point, dia, z_move=None, old_point=(0, 0)):
        """

        :param point:               A Shapely point geometry element
        :type point:                Shapely Point
        :param dia:                 The tool diameter that is going on the path
        :type dia:                  float
        :param z_move:              Travel Z
        :type z_move:               float
        :param old_point:           Old point coordinates from which we moved to the 'point'
        :type old_point:            tuple
        :return:                    G-code to cut on the Point feature.
        :rtype:                     str
        """
        gcode = ""

        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        path = list(point.coords)
        p = self.pp_geometry

        self.coordinates_type = self._app_option("cncjob_coords_type")
        if self.coordinates_type == "G90":
            # For Absolute coordinates type G90
            first_x = path[0][0]
            first_y = path[0][1]
        else:
            # For Incremental coordinates type G91
            # first_x = path[0][0] - old_point[0]
            # first_y = path[0][1] - old_point[1]
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _('G91 coordinates not implemented ...'))
            first_x = path[0][0]
            first_y = path[0][1]

        current_tooldia = dia
        travels = self.milling_travels((old_point[0], old_point[1]), (first_x, first_y), current_tooldia,
                                       travel_z=z_move)
        prev_z = None
        for travel in travels:
            locx = travel[1][0]
            locy = travel[1][1]

            if travel[0] is not None:
                # move to next point
                gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                # raise to safe Z (travel[0]) each time because safe Z may be different
                self.z_move = travel[0]
                gcode += self.doformat(p.lift_code, x=locx, y=locy)

                # restore z_move
                self.z_move = z_move
            else:
                if prev_z is not None:
                    # move to next point
                    gcode += self.doformat(p.rapid_code, x=locx, y=locy)

                    # we assume that previously the z_move was altered therefore raise to
                    # the travel_z (z_move)
                    self.z_move = z_move
                    gcode += self.doformat(p.lift_code, x=locx, y=locy)
                else:
                    # move to next point
                    gcode += self.doformat(p.rapid_code, x=locx, y=locy)

            # store prev_z
            prev_z = travel[0]

        # gcode += self.doformat(p.linear_code, x=first_x, y=first_y)  # Move to first point

        if self.z_feedrate is not None:
            gcode += self.doformat(p.z_feedrate_code)
            gcode += self.doformat(p.down_code, x=first_x, y=first_y, z_cut=self.z_cut)
            gcode += self.doformat(p.feedrate_code)
        else:
            gcode += self.doformat(p.down_code, x=first_x, y=first_y, z_cut=self.z_cut)  # Start cutting

        gcode += self.doformat(p.lift_code, x=first_x, y=first_y)  # Stop cutting
        return gcode

    def export_svg(self, scale_stroke_factor=0.00,
                   scale_factor_x=None, scale_factor_y=None,
                   skew_factor_x=None, skew_factor_y=None,
                   skew_reference='center', scale_reference='center', mirror_reference='center',
                   mirror=None):

        """
        Exports the CNC Job as an SVG Element

        :param scale_stroke_factor:     A factor to scale the SVG geometry element outline
        :param scale_factor_x:          x factor for scale
        :param scale_factor_y:          y factor for scale
        :param skew_factor_x:           x factor for skew
        :param skew_factor_y:           y factor for skew
        :param skew_reference:          The reference point for skewing (str, tuple)
        :param scale_reference:         The reference point for scaling (str, tuple)
        :param mirror_reference:        The reference point for mirroring (str, tuple)
        :param mirror:                  Bool, if to mirror or not
        :return:                        SVG Element string
        """

        # scale_factor is a multiplication factor for the SVG stroke-width used within shapely's svg export
        # If not specified then try and use the tool diameter
        # This way what is on screen will match what is outputed for the svg
        # This is quite a useful feature for svg's used with visicut

        if scale_stroke_factor <= 0:
            scale_stroke_factor = self.obj_options['tooldia'] / 2

        # If still 0 then default to 0.05
        # This value appears to work for zooming, and getting the output svg line width
        # to match that viewed on screen with FlatCam
        if scale_stroke_factor == 0:
            scale_stroke_factor = 0.01

        # Separate the list of cuts and travels into 2 distinct lists
        # This way we can add different formatting / colors to both
        cuts = []
        travels = []
        cutsgeom: str = ''
        travelsgeom = ''

        for g in self.gcode_parsed:
            if self.app.abort_flag:
                # graceful abort requested by the user
                raise grace

            if g['kind'][0] == 'C':
                cuts.append(g)
            if g['kind'][0] == 'T':
                travels.append(g)

        # Used to determine the overall board size
        self.solid_geometry = unary_union([geo['geom'] for geo in self.gcode_parsed])

        # Convert the cuts and travels into single geometry objects we can render as svg xml
        if travels:
            travelsgeom = unary_union([geo['geom'] for geo in travels])

        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        if cuts:
            cutsgeom = unary_union([geo['geom'] for geo in cuts])

        # Render the SVG Xml
        # The scale factor affects the size of the lines, and the stroke color adds different formatting for each set
        # It's better to have the travels sitting underneath the cuts for visicut
        svg_elem = ""
        if travels:
            svg_elem = travelsgeom.svg(scale_factor=scale_stroke_factor, stroke_color="#F0E24D")
        if cuts:
            svg_elem += cutsgeom.svg(scale_factor=scale_stroke_factor, stroke_color="#5E6CFF")

        # if both are true then we need a root element <g>
        if travels and cuts:
            svg_elem = "<g>" + svg_elem + "</g>"

        return svg_elem

    def bounds(self, flatten=None):
        """
        Returns coordinates of rectangular bounds of geometry: (xmin, ymin, xmax, ymax).

        :param flatten:     Not used, it is here for compatibility with base class method
        :type flatten:      bool
        :return:            Bounding values in format (xmin, ymin, xmax, ymax)
        :rtype:             tuple
        """

        self.app.log.debug("camlib.CNCJob.bounds()")

        def bounds_rec(obj):
            if type(obj) is list:
                cminx = np.inf
                cminy = np.inf
                cmaxx = -np.inf
                cmaxy = -np.inf

                w_geo = obj.geoms if isinstance(obj, (MultiPolygon, MultiLineString)) else obj
                for oo in w_geo:
                    if type(oo) is dict:
                        for key in oo:
                            minx_, miny_, maxx_, maxy_ = bounds_rec(oo[key])
                            cminx = min(cminx, minx_)
                            cminy = min(cminy, miny_)
                            cmaxx = max(cmaxx, maxx_)
                            cmaxy = max(cmaxy, maxy_)
                    else:
                        minx_, miny_, maxx_, maxy_ = bounds_rec(oo)
                        cminx = min(cminx, minx_)
                        cminy = min(cminy, miny_)
                        cmaxx = max(cmaxx, maxx_)
                        cmaxy = max(cmaxy, maxy_)
                return cminx, cminy, cmaxx, cmaxy
            else:
                # it's a Shapely object, return its bounds
                return obj.bounds

        if self.multitool is False:
            self.app.log.debug("CNCJob->bounds()")
            if self.solid_geometry is None:
                self.app.log.debug("solid_geometry is None")
                return 0, 0, 0, 0

            bounds_coords = bounds_rec(self.solid_geometry)
        else:
            minx = np.inf
            miny = np.inf
            maxx = -np.inf
            maxy = -np.inf
            # for CNCJob objects made from Gerber or Geometry objects
            if self.obj_options['type'].lower() == 'geometry':
                for k, v in self.tools.items():
                    minx = np.inf
                    miny = np.inf
                    maxx = -np.inf
                    maxy = -np.inf
                    try:
                        work_geo = v['solid_geometry']
                        i_wg = work_geo.geoms if isinstance(work_geo, (MultiPolygon, MultiLineString)) else work_geo
                        for geo in i_wg:
                            if isinstance(geo, list):
                                geo = unary_union(geo)
                            if geo.is_empty:
                                continue
                            minx_, miny_, maxx_, maxy_ = bounds_rec(geo)
                            minx = min(minx, minx_)
                            miny = min(miny, miny_)
                            maxx = max(maxx, maxx_)
                            maxy = max(maxy, maxy_)
                    except TypeError:
                        minx_, miny_, maxx_, maxy_ = bounds_rec(v['solid_geometry'])
                        minx = min(minx, minx_)
                        miny = min(miny, miny_)
                        maxx = max(maxx, maxx_)
                        maxy = max(maxy, maxy_)

            if self.obj_options['type'].lower() == 'excellon':
                for k, v in self.tools.items():
                    minx = np.inf
                    miny = np.inf
                    maxx = -np.inf
                    maxy = -np.inf
                    try:
                        for geo in v['solid_geometry']:
                            minx_, miny_, maxx_, maxy_ = bounds_rec(geo)
                            minx = min(minx, minx_)
                            miny = min(miny, miny_)
                            maxx = max(maxx, maxx_)
                            maxy = max(maxy, maxy_)
                    except TypeError:
                        minx_, miny_, maxx_, maxy_ = bounds_rec(v['solid_geometry'])
                        minx = min(minx, minx_)
                        miny = min(miny, miny_)
                        maxx = max(maxx, maxx_)
                        maxy = max(maxy, maxy_)

            bounds_coords = minx, miny, maxx, maxy
        return bounds_coords

    # TODO This function should be replaced at some point with a "real" function. Until then it's an ugly hack ...
    @staticmethod
    def _affine_gcode_parsed(gcode_parsed, matrix):
        """
        Applies one affine matrix to the 'geom' of every dict of a gcode_parsed list in ONE vectorized call.
        """
        dicts = list(gcode_parsed)
        results = affine_geometry_many([d['geom'] for d in dicts], matrix)
        for d, geom in zip(dicts, results):
            d['geom'] = geom

    def _units_factor_to_file(self):
        """Factor from the job units to the units of the imported G-code file (1.0 when equal or unknown)."""
        fu = str((getattr(self, 'obj_options', None) or {}).get('gcode_units') or '').upper()
        ju = str(self.units or '').upper()
        if fu in ('MM', 'IN') and ju in ('MM', 'IN') and fu != ju:
            return 25.4 if fu == 'MM' else 1.0 / 25.4
        return 1.0

    def _transform_imported(self, matrix):
        """
        Applies the matrix (in job units) to the text of an imported G-code job and parses the new text again: the
        text of every tool, the parsed geometry of every tool and of the job follow the text.
        """
        from appParsers.ParseGCode import transform_gcode, split_tool_blocks, parse_gcode

        a, b, d, e, xoff, yoff = matrix
        k = self._units_factor_to_file()
        decimals = getattr(self, 'coords_decimals', None) or 4
        new, warns = transform_gcode(self.gcode, (a, b, d, e, xoff * k, yoff * k), decimals, self.steps_per_circle)
        for msg in warns:
            self.app.log.warning("CNCjob transform: %s" % msg)
        self.gcode = new

        blocks = split_tool_blocks(new)
        items = list(self.tools.items())
        if len(blocks) == len(items):
            mapping = list(range(len(blocks)))
        else:
            self.app.log.warning("CNCjob transform: the tool blocks of the new G-code differ from the tools of the "
                                 "job, the tools are matched by their T number.")
            used = set()
            mapping = []
            for _, tool in items:
                found = next((i for i, blk in enumerate(blocks) if i not in used and blk['tool'] == tool.get('tool')),
                             None)
                if found is not None:
                    used.add(found)
                mapping.append(found)

        dia = self.tooldia if self.tooldia else 0.4
        res = parse_gcode(new, self.steps_per_circle, default_tooldia=dia, target_units=self.units)
        total = []
        for (_, tool), bi in zip(items, mapping):
            if bi is None:
                continue
            parsed = res['tools'].get(bi + 1)
            tool['gcode'] = blocks[bi]['gcode']
            if parsed is not None:
                tool['gcode_parsed'] = parsed['gcode_parsed']
                tool['solid_geometry'] = parsed['solid_geometry']
                if 'drills' in tool:
                    tool['drills'] = [Point(x, y) for x, y in parsed['drills']]
                total += parsed['gcode_parsed']
        self.gcode_parsed = total
        self.travel_distance = res['travel_distance']
        if res['bounds']:
            (self.obj_options['xmin'], self.obj_options['ymin'],
             self.obj_options['xmax'], self.obj_options['ymax']) = res['bounds']

    def _transform_native(self, matrix):
        """Native job: the text of every tool (or of the job) and the geometry follow the matrix."""
        from appParsers.ParseGCode import transform_gcode

        pp_names = (str(getattr(self, 'pp_geometry_name', '')) + str(getattr(self, 'pp_excellon_name', ''))).lower()
        if 'roland' in pp_names or 'hpgl' in pp_names:
            # these preprocessors have no X/Y words: only the plot follows the matrix
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The machine code of the Roland/HPGL preprocessors is not "
                                                          "transformed, only the plot is. Generate the job again."))
        decimals = getattr(self, 'coords_decimals', None) or 4
        if self.multitool:
            texts = [v for v in self.tools.values() if isinstance(v.get('gcode'), str) and v['gcode']]
            for v in texts:
                v['gcode'], warns = transform_gcode(v['gcode'], matrix, decimals, self.steps_per_circle)
                for msg in warns:
                    self.app.log.warning("CNCjob transform: %s" % msg)
        elif isinstance(self.gcode, str) and self.gcode:
            self.gcode, warns = transform_gcode(self.gcode, matrix, decimals, self.steps_per_circle)
            for msg in warns:
                self.app.log.warning("CNCjob transform: %s" % msg)

        # the same dict can be in the list of the job and in the list of a tool: transform it once
        unique = {}
        for parsed in [self.gcode_parsed] + [v.get('gcode_parsed') or [] for v in (self.tools or {}).values()]:
            for item in parsed or ():
                unique[id(item)] = item
        CNCjob._affine_gcode_parsed(list(unique.values()), matrix)
        for v in (self.tools or {}).values():
            if v.get('gcode_parsed'):
                v['solid_geometry'] = unary_union([geo['geom'] for geo in v['gcode_parsed']])

    def gcode_flow_blocked(self, action):
        """
        A program with macro variables or subprograms can not be rewritten word by word: the same words run with other
        values or more than once.

        :param action:  'transform' or 'level': what can not be done
        :return:        the error message for the user if the text of the imported job has macros or subprograms,
                        else None
        """
        if not getattr(self, 'gcode_imported', False):
            return None
        from appParsers.ParseGCode import program_features

        features = program_features(self.gcode or '')
        if not (features['macros'] or features['subprograms']):
            return None
        if action == 'level':
            msg = _("The G-code has macro variables or subprograms: it can not be levelled. "
                    "Change the program in the software that made it.")
        else:
            msg = _("The G-code has macro variables or subprograms: it can not be moved or transformed. "
                    "Change the program in the software that made it.")
        name = (getattr(self, 'obj_options', None) or {}).get('name', '')
        return '[ERROR_NOTCL] %s: %s' % (name, msg)

    def _transform(self, matrix):
        """G-code text and geometry of the job under the affine matrix (a, b, d, e, xoff, yoff)."""
        if getattr(self, 'gcode_imported', False):
            blocked = self.gcode_flow_blocked('transform')
            if blocked:
                # the text and the plot stay as they are, together
                self.app.inform.emit(blocked)
                self.app.proc_container.new_text = ''
                return
            self._transform_imported(matrix)
        else:
            self._transform_native(matrix)
        self.create_geometry()
        self.app.proc_container.new_text = ''

    def scale(self, xfactor, yfactor=None, point=None):
        """
        Scales all the geometry on the XY plane in the object by the
        given factor. Tool sizes, feedrates, or Z-axis dimensions are
        not altered.


        :param yfactor: scale factor on the X axis; float
        :param xfactor: scale factor on the Y axis; float
        :param point:   the (x,y) coords for the point of origin of scale
        :type  point:   tuple
        :return:        None
        """
        self.app.log.debug("camlib.CNCJob.scale()")

        if yfactor is None:
            yfactor = xfactor

        if point is None:
            px = 0
            py = 0
        else:
            px, py = point

        if getattr(self, 'gcode_imported', False):
            self._transform(affine_scale_matrix(xfactor, yfactor, (px, py)))
            return

        def scale_g(g):
            """

            :param g: 'g' parameter it's a gcode string
            :return:  scaled gcode string
            """

            temp_gcode = ''
            header_start = False
            header_stop = False
            units = self.app.app_units.upper()

            lines = StringIO(g)
            for line in lines:

                # this changes the GCODE header ---- UGLY HACK
                if "TOOL DIAMETER" in line or "Feedrate:" in line:
                    header_start = True

                if "G20" in line or "G21" in line:
                    header_start = False
                    header_stop = True

                if header_start is True:
                    header_stop = False
                    if "in" in line:
                        if units == 'MM':
                            line = line.replace("in", "mm")
                    if "mm" in line:
                        if units == 'IN':
                            line = line.replace("mm", "in")

                    # find any float number in header (even multiple on the same line) and convert it
                    numbers_in_header = re.findall(self.g_nr_re, line)
                    if numbers_in_header:
                        for nr in numbers_in_header:
                            new_nr = float(nr) * xfactor
                            # replace the updated string
                            line = line.replace(nr, ('%.*f' % (self._app_option("cncjob_coords_decimals"), new_nr))
                                                )

                # this scales all the X and Y and Z and F values and also the Tool Dia in the toolchange message
                if header_stop is True:
                    if "G20" in line:
                        if units == 'MM':
                            line = line.replace("G20", "G21")
                    if "G21" in line:
                        if units == 'IN':
                            line = line.replace("G21", "G20")

                    # find the X group
                    match_x = self.g_x_re.search(line)
                    if match_x:
                        if match_x.group(1) is not None:
                            new_x = float(match_x.group(1)[1:]) * xfactor
                            # replace the updated string
                            line = line.replace(
                                match_x.group(1),
                                'X%.*f' % (self._app_option("cncjob_coords_decimals"), new_x)
                            )
                    # find the Y group
                    match_y = self.g_y_re.search(line)
                    if match_y:
                        if match_y.group(1) is not None:
                            new_y = float(match_y.group(1)[1:]) * yfactor
                            line = line.replace(
                                match_y.group(1),
                                'Y%.*f' % (self._app_option("cncjob_coords_decimals"), new_y)
                            )
                    # find the Z group
                    match_z = self.g_z_re.search(line)
                    if match_z:
                        if match_z.group(1) is not None:
                            new_z = float(match_z.group(1)[1:]) * xfactor
                            line = line.replace(
                                match_z.group(1),
                                'Z%.*f' % (self._app_option("cncjob_coords_decimals"), new_z)
                            )

                    # find the F group
                    match_f = self.g_f_re.search(line)
                    if match_f:
                        if match_f.group(1) is not None:
                            new_f = float(match_f.group(1)[1:]) * xfactor
                            line = line.replace(
                                match_f.group(1),
                                'F%.*f' % (self._app_option("cncjob_fr_decimals"), new_f)
                            )
                    # find the T group (tool dia on toolchange)
                    match_t = self.g_t_re.search(line)
                    if match_t:
                        if match_t.group(1) is not None:
                            new_t = float(match_t.group(1)[1:]) * xfactor
                            line = line.replace(
                                match_t.group(1),
                                '= %.*f' % (self._app_option("cncjob_coords_decimals"), new_t)
                            )

                temp_gcode += line
            lines.close()
            # header_stop = False
            return temp_gcode

        if self.multitool is False:
            # offset Gcode
            self.gcode = scale_g(self.gcode)

            # scale geometry
            CNCjob._affine_gcode_parsed(self.gcode_parsed, affine_scale_matrix(xfactor, yfactor, (px, py)))

            self.create_geometry()
        else:
            # for CNCJob objects made from Geometry objects
            for k, v in self.tools.items():
                # scale Gcode
                v['gcode'] = scale_g(v['gcode'])

                # scale gcode_parsed
                CNCjob._affine_gcode_parsed(v['gcode_parsed'], affine_scale_matrix(xfactor, yfactor, (px, py)))

                g_code_geos: list[BaseGeometry | MultiPolygon] = [geo['geom'] for geo in v['gcode_parsed']] # noqa
                v['solid_geometry'] = unary_union(g_code_geos)
        self.create_geometry()
        self.app.proc_container.new_text = ''

    def offset(self, vect):
        """
        Offsets all the geometry on the XY plane in the object by the
        given vector.
        Offsets all the GCODE on the XY plane in the object by the
        given vector.

        g_offsetx_re, g_offsety_re, multitool, cnnc_tools are attributes of FlatCAMCNCJob class in camlib

        :param vect:    (x, y) offset vector.
        :type vect:     tuple
        :return:        None
        """
        self.app.log.debug("camlib.CNCJob.offset()")

        dx, dy = vect

        if getattr(self, 'gcode_imported', False):
            self._transform(affine_translate_matrix(dx, dy))
            return

        def offset_g(g):
            """

            :param g: 'g' parameter it's a gcode string
            :return:  offseted gcode string
            """

            temp_gcode = ''
            lines = StringIO(g)
            for line in lines:
                # find the X group
                match_x = self.g_x_re.search(line)
                if match_x:
                    if match_x.group(1) is not None:
                        # get the coordinate and add X offset
                        new_x = float(match_x.group(1)[1:]) + dx
                        # replace the updated string
                        line = line.replace(
                            match_x.group(1),
                            'X%.*f' % (self._app_option("cncjob_coords_decimals"), new_x)
                        )
                match_y = self.g_y_re.search(line)
                if match_y:
                    if match_y.group(1) is not None:
                        new_y = float(match_y.group(1)[1:]) + dy
                        line = line.replace(
                            match_y.group(1),
                            'Y%.*f' % (self._app_option("cncjob_coords_decimals"), new_y)
                        )
                temp_gcode += line
            lines.close()
            return temp_gcode

        if self.multitool is False:
            # offset Gcode
            self.gcode = offset_g(self.gcode)

            # Vectorized geometry translation using Shapely 2.0+
            # Check for empty or None gcode_parsed
            if self.gcode_parsed:
                try:
                    geoms = np.array([g['geom'] for g in self.gcode_parsed], dtype=object)
                    coords = shapely.get_coordinates(geoms)
                    if coords.size > 0:
                        coords[:, 0] += dx
                        coords[:, 1] += dy
                        translated = shapely.set_coordinates(geoms, coords)
                        for g, new_geom in zip(self.gcode_parsed, translated):
                            g['geom'] = new_geom
                except Exception as e:
                    self.app.log.error('CNCjob.offset() vectorized translation failed: %s' % str(e))
                    self.app.proc_container.new_text = ''
                    return

            # Show completion
            self.app.proc_container.update_view_text(' %d%%' % 100)

            self.create_geometry()
        else:
            # for CNCJob objects made from Gerber or Geometry objects
            for k, v in self.tools.items():
                # offset Gcode
                v['gcode'] = offset_g(v['gcode'])

                # Vectorized geometry translation using Shapely 2.0+
                # Check for empty or None gcode_parsed
                if v.get('gcode_parsed'):
                    try:
                        geoms = np.array([g['geom'] for g in v['gcode_parsed']], dtype=object)
                        coords = shapely.get_coordinates(geoms)
                        if coords.size > 0:
                            coords[:, 0] += dx
                            coords[:, 1] += dy
                            translated = shapely.set_coordinates(geoms, coords)
                            for g, new_geom in zip(v['gcode_parsed'], translated):
                                g['geom'] = new_geom
                    except Exception as e:
                        self.app.log.error('CNCjob.offset() multitool vectorized translation failed: %s' % str(e))
                        self.app.proc_container.new_text = ''
                        return

                # for the bounding box
                if v.get('gcode_parsed'):
                    v['solid_geometry'] = unary_union([geo['geom'] for geo in v['gcode_parsed']])

                # Show completion per tool
                self.app.proc_container.update_view_text(' %d%%' % 100)

        self.app.proc_container.new_text = ''

    def mirror(self, axis, point):
        """
        Mirror the geometry of an object by a given axis around the coordinates of the 'point'

        :param axis:    Axis for Mirror
        :param point:   tuple of coordinates (x,y). Point of origin for Mirror
        :return:
        """
        self.app.log.debug("camlib.CNCJob.mirror()")

        px, py = point

        self._transform(affine_mirror_matrix(axis, (px, py)))

    def skew(self, angle_x, angle_y, point):
        """
        Shear/Skew the geometries of an object by angles along x and y dimensions.

        :param angle_x:
        :param angle_y:
        angle_x, angle_y : float, float
            The shear angle(s) for the x and y axes respectively. These can be
            specified in either degrees (default) or radians by setting
            use_radians=True.

        :param point:   tupple of coordinates (x,y)

        See shapely manual for more information: http://toblerity.org/shapely/manual.html#affine-transformations
        """
        self.app.log.debug("camlib.CNCJob.skew()")

        px, py = point

        self._transform(affine_skew_matrix(angle_x, angle_y, (px, py)))

    def rotate(self, angle, point):
        """
        Rotate the geometry of an object by a given angle around the coordinates of the 'point'

        :param angle:   Angle of Rotation
        :param point:   tuple of coordinates (x,y). Origin point for Rotation
        :return:
        """
        self.app.log.debug("camlib.CNCJob.rotate()")

        px, py = point

        self._transform(affine_rotate_matrix(angle, (px, py)))


def list_total_bounds(obj, nested=True):
    """
    Vectorized bounds of a plain ``list`` of geometries (lists nested, dicts inside lists contribute their values).
    Returns ``(minx, miny, maxx, maxy)`` as Python floats, ``(inf, inf, -inf, -inf)`` when there is no non empty leaf
    (the result of the recursive walkers), or ``None`` when the structure holds anything unexpected (non list top
    level, None, tuples, ...) so that the caller falls back to its own walker and keeps its exact behaviour.

    :param nested:  False: a list inside a list is unexpected (Gerber walker raises on it), lists as dict values are ok
    """
    if type(obj) is not list:
        return None
    leaves = []
    stack = [(obj, True)]
    while stack:
        cur, is_top = stack.pop()
        for k in cur:
            tk = type(k)
            if tk is list:
                if not nested:
                    return None
                stack.append((k, False))
            elif tk is dict:
                for v in k.values():
                    if type(v) is list:
                        stack.append((v, False))
                    elif isinstance(v, BaseGeometry):
                        leaves.append(v)
                    else:
                        return None
            elif isinstance(k, BaseGeometry):
                leaves.append(k)
            else:
                return None
    if not leaves:
        return np.inf, np.inf, -np.inf, -np.inf
    arr = np.empty(len(leaves), dtype=object)
    arr[:] = leaves
    b = shapely.bounds(arr)
    if np.isnan(b).all():
        return np.inf, np.inf, -np.inf, -np.inf
    with np.errstate(all='ignore'):
        return (float(np.nanmin(b[:, 0])), float(np.nanmin(b[:, 1])),
                float(np.nanmax(b[:, 2])), float(np.nanmax(b[:, 3])))


def map_geometry(obj, fn, on_leaf=None, any_iterable=False):
    """
    Shared coordinate walker of the Gerber / Excellon transforms. Applies ``fn`` to every leaf of ``obj`` and keeps the
    container shape: a list stays a list (nested lists are walked recursively), a single geometry (including Multi*
    and GeometryCollection) is a leaf and goes to ``fn`` whole, None is passed to ``fn`` like any leaf.

    An AttributeError raised for a leaf (None, or an object without geometry methods) returns that leaf unchanged,
    any other exception propagates. ``on_leaf`` (progress display) runs inside the same guard, before ``fn``.

    :param obj:          Geometry, list of geometries or nested lists
    :param fn:           callable(leaf) -> new leaf (one shapely.affinity call)
    :param on_leaf:      optional callable(leaf), called once per leaf before ``fn``
    :param any_iterable: True: any iterable (tuple, ...) is walked and returned as a list, not only a list
    :return:             the transformed object, same container shape
    """
    if type(obj) is list:
        return [map_geometry(g, fn, on_leaf, any_iterable) for g in obj]
    if any_iterable:
        try:
            items = iter(obj)
        except TypeError:
            pass
        else:
            return [map_geometry(g, fn, on_leaf, any_iterable) for g in items]
    try:
        if on_leaf is not None:
            on_leaf(obj)
        return fn(obj)
    except AttributeError:
        return obj


AFFINE_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def affine_translate_matrix(dx, dy):
    """Matrix (a, b, d, e, xoff, yoff) of shapely.affinity.translate."""
    return 1.0, 0.0, 0.0, 1.0, float(dx), float(dy)


def affine_rotate_matrix(angle_deg, origin=(0.0, 0.0)):
    """Matrix of shapely.affinity.rotate(geom, angle_deg, origin=(x, y)), angle in degrees, counter-clockwise."""
    x0, y0 = origin
    angle = math.radians(angle_deg)
    cosp = math.cos(angle)
    sinp = math.sin(angle)
    # same rounding of the exact cases as shapely.affinity.rotate
    if abs(cosp) < 2.5e-16:
        cosp = 0.0
    if abs(sinp) < 2.5e-16:
        sinp = 0.0
    return (cosp, -sinp, sinp, cosp,
            x0 - x0 * cosp + y0 * sinp,
            y0 - x0 * sinp - y0 * cosp)


def affine_scale_matrix(xfact=1.0, yfact=1.0, origin=(0.0, 0.0)):
    """Matrix of shapely.affinity.scale(geom, xfact, yfact, origin=(x, y))."""
    x0, y0 = origin
    return float(xfact), 0.0, 0.0, float(yfact), x0 - x0 * xfact, y0 - y0 * yfact


def affine_skew_matrix(ax_deg=0.0, ay_deg=0.0, origin=(0.0, 0.0)):
    """Matrix of shapely.affinity.skew(geom, ax_deg, ay_deg, origin=(x, y)), angles in degrees."""
    x0, y0 = origin
    tx = math.tan(math.radians(ax_deg))
    ty = math.tan(math.radians(ay_deg))
    return 1.0, tx, ty, 1.0, -y0 * tx, -x0 * ty


def affine_mirror_matrix(axis, origin=(0.0, 0.0)):
    """
    Matrix of the mirror of Gerber.mirror / Geometry.mirror: scale with ``{"X": (1, -1), "Y": (-1, 1)}[axis]`` around
    ``origin``. Axis "X" flips Y (mirror about a horizontal line), axis "Y" flips X.
    """
    xscale, yscale = {"X": (1.0, -1.0), "Y": (-1.0, 1.0)}[axis]
    return affine_scale_matrix(xscale, yscale, origin)


def _affine_collect(obj, leaves, any_iterable):
    if isinstance(obj, BaseGeometry):
        leaves.append(obj)
    elif type(obj) is list:
        for g in obj:
            _affine_collect(g, leaves, any_iterable)
    elif any_iterable and not isinstance(obj, (str, bytes)):
        try:
            items = iter(obj)
        except TypeError:
            return
        for g in items:
            _affine_collect(g, leaves, any_iterable)


def _affine_rebuild(obj, results, any_iterable):
    if isinstance(obj, BaseGeometry):
        return next(results)
    if type(obj) is list:
        return [_affine_rebuild(g, results, any_iterable) for g in obj]
    if any_iterable and not isinstance(obj, (str, bytes)):
        try:
            items = iter(obj)
        except TypeError:
            return obj
        return [_affine_rebuild(g, results, any_iterable) for g in items]
    return obj


def affine_geometry_many(objs, matrix, any_iterable=False):
    """
    Applies one affine matrix to several geometry structures with ONE shapely.transform call per dimensionality
    (2D / 3D). Same container rules as map_geometry: a list stays a list (nested lists are walked), a single geometry
    (including Multi* and GeometryCollection) is transformed whole, None and non-geometry leaves are returned unchanged.
    Z coordinates are kept.

    :param objs:         list/tuple of structures (each a geometry, a list, nested lists, None, ...)
    :param matrix:       (a, b, d, e, xoff, yoff) as in shapely.affinity.affine_transform:
                         x' = a*x + b*y + xoff, y' = d*x + e*y + yoff
    :param any_iterable: True: any iterable (tuple, ...) is walked and returned as a list, not only a list
    :return:             list of the transformed structures, in the same order
    """
    objs = list(objs)
    matrix = tuple(float(m) for m in matrix)
    if matrix == AFFINE_IDENTITY:
        return objs
    a, b, d, e, xoff, yoff = matrix

    leaves = []
    for o in objs:
        _affine_collect(o, leaves, any_iterable)
    if not leaves:
        return objs

    if b == 0.0 and d == 0.0:
        def fn(c):
            c[:, 0] *= a
            c[:, 0] += xoff
            c[:, 1] *= e
            c[:, 1] += yoff
            return c
    else:
        def fn(c):
            x = c[:, 0].copy()
            y = c[:, 1]
            c[:, 0] = a * x + b * y + xoff
            c[:, 1] = d * x + e * y + yoff
            return c

    arr = np.empty(len(leaves), dtype=object)
    arr[:] = leaves
    has_z = shapely.has_z(arr)
    if has_z.any():
        out = np.empty(len(leaves), dtype=object)
        if (~has_z).any():
            out[~has_z] = shapely.transform(arr[~has_z], fn, include_z=False)
        out[has_z] = shapely.transform(arr[has_z], fn, include_z=True)
    else:
        out = shapely.transform(arr, fn, include_z=False)

    results = iter(out)
    return [_affine_rebuild(o, results, any_iterable) for o in objs]


def affine_geometry(obj, matrix, any_iterable=False):
    """
    Vectorized replacement of ``map_geometry(obj, lambda g: affinity.<op>(g, ...))``: one shapely.transform call for
    the whole nested structure. See affine_geometry_many for the rules and the matrix format.
    """
    return affine_geometry_many([obj], matrix, any_iterable)[0]


def translate_geometry(obj, dx, dy):
    """
    Fast geometry translation using Shapely 2.0+ vectorized coordinate ops.

    Handles:
    - Flat list of geometries: batch translate all at once
    - Nested list: recurse, batching each flat sub-list
    - Single geometry (including Multi*): direct vectorized translation
    - Empty/non-geometry: return as-is

    :param obj: Geometry object or list of geometries
    :param dx: X offset
    :param dy: Y offset
    :return: Translated geometry
    """
    # Handle None or empty cases
    if obj is None:
        return None
    
    if isinstance(obj, list):
        if not obj:
            return []
        # Check for nested lists
        if any(isinstance(item, list) for item in obj):
            return [translate_geometry(item, dx, dy) for item in obj]
        # Flat list - vectorized batch operation
        # Filter out None values and empty geometries
        valid_geoms = [g for g in obj if g is not None]
        if not valid_geoms:
            return obj
        
        try:
            arr = np.array(valid_geoms, dtype=object)
            coords = shapely.get_coordinates(arr, include_z=bool(shapely.has_z(arr).any()))
            if coords.size == 0:
                return obj
            coords[:, 0] += dx
            coords[:, 1] += dy
            translated = shapely.set_coordinates(arr, coords)
            # Map back to original list, preserving None positions
            result = []
            idx = 0
            for g in obj:
                if g is None:
                    result.append(None)
                else:
                    result.append(translated[idx])
                    idx += 1
            return result
        except Exception:
            # Fallback to individual translation on error
            result = []
            for g in obj:
                if g is not None:
                    try:
                        result.append(translate_geometry(g, dx, dy))
                    except Exception:
                        result.append(g)
                else:
                    result.append(None)
            return result
    elif isinstance(obj, BaseGeometry):
        try:
            coords = shapely.get_coordinates(obj, include_z=obj.has_z)
            if coords.size == 0:
                return obj
            coords[:, 0] += dx
            coords[:, 1] += dy
            return shapely.set_coordinates(obj, coords)
        except Exception:
            # Fallback to affinity.translate
            return affinity.translate(obj, xoff=dx, yoff=dy)
    else:
        return obj


def flatten_shapely_geometry(
        geometry: BaseGeometry | BaseMultipartGeometry | Iterable[BaseGeometry] | GeometrySequence,
        simplify_tolerance: float = 0.0,
) -> list[BaseGeometry]:
    """

    :param geometry:
    :type geometry:
    :param simplify_tolerance:  if non-zero then simplify the geometry
    :type simplify_tolerance:   float
    :return:
    :rtype:
    """
    flat_list: list[BaseGeometry] = []

    def add_geometry(geo):
        if isinstance(geo, BaseMultipartGeometry):
            for child in geo.geoms:
                add_geometry(child)
        elif isinstance(geo, BaseGeometry) and not geo.is_empty:
            flat_list.append(geo)

    def flatten(geo):
        if isinstance(geo, BaseMultipartGeometry):
            for child in geo.geoms:
                flatten(child)
        elif isinstance(geo, BaseGeometry):
            if not geo.is_empty:
                simplified = geo.simplify(simplify_tolerance) if simplify_tolerance > 0.0 else geo
                if not simplified.is_empty:
                    add_geometry(simplified)
        elif isinstance(geo, Iterable) and not isinstance(geo, (str, bytes)):
            for child in geo:
                flatten(child)
        else:
            raise NotImplementedError(f"No implementation for flattening {type(geo)}")

    flatten(geometry)
    return flat_list


_GEOMETRY_LEAF = object()


def _flatten_geometry_tree(obj, leaves):
    """
    Descriptor of a nested geometry structure for the buffer helpers: the leaves go to ``leaves`` and the descriptor
    keeps the nested list shape. Multi-part geometries and GeometryCollections are flattened to their parts.
    """
    if obj is None:
        return None
    if isinstance(obj, BaseMultipartGeometry):
        return [_flatten_geometry_tree(g, leaves) for g in obj.geoms]
    if isinstance(obj, BaseGeometry):
        if obj.is_empty:
            return None
        leaves.append(obj)
        return _GEOMETRY_LEAF
    if isinstance(obj, Iterable) and not isinstance(obj, (str, bytes)):
        return [_flatten_geometry_tree(g, leaves) for g in obj]
    raise TypeError(f"No implementation for buffering {type(obj)}")


def _rebuild_geometry_tree(descriptor, results):
    if descriptor is None:
        return []
    if descriptor is _GEOMETRY_LEAF:
        return next(results)
    return [_rebuild_geometry_tree(d, results) for d in descriptor]


def _polygon_boundaries(geometry):
    """The exterior rings of the polygons of a buffer result (a closed contour can erode into several pieces)."""
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry.exterior]
    if isinstance(geometry, BaseMultipartGeometry):
        boundaries = []
        for part in geometry.geoms:
            boundaries.extend(_polygon_boundaries(part))
        return boundaries
    return []


def buffer_geometry_many(objs, distance, quad_segs, join, only_exterior=False, allow_empty=False):
    """
    Buffer nested geometry structures, in one vectorized Shapely call for the normal distance case.

    With ``allow_empty`` false (the Geometry buffer), a negative distance on an open line, or a buffer that would
    remove a non-empty geometry, raises ValueError: the caller leaves its object unchanged instead of losing that
    part. A negative distance on a closed contour erodes the area it encloses and returns the exterior ring of every
    remaining piece, so a split contour is not dropped. The Gerber buffer allows an empty result (an aperture that
    disappears is exported as regions).

    :param objs:            list of structures (geometry, nested lists, None, ...)
    :param distance:        the buffer distance
    :param quad_segs:       segments of a quarter circle, as given to buffer()
    :param join:            Shapely join style name or code
    :param only_exterior:   True: keep only the exterior of a buffered line
    :param allow_empty:     True: an empty result is kept (it is dropped by the caller's flatten later)
    :return:                list of the buffered structures, in the same order
    """
    objs = list(objs)
    leaves = []
    descriptors = [_flatten_geometry_tree(obj, leaves) for obj in objs]
    if not leaves:
        return [_rebuild_geometry_tree(d, iter(())) for d in descriptors]

    results = [None] * len(leaves)
    batch_indices = []
    batch_leaves = []
    for index, leaf in enumerate(leaves):
        if distance < 0 and isinstance(leaf, (LinearRing, LineString)) and not allow_empty:
            if not leaf.is_closed:
                raise ValueError(_("An open line has no width to erode."))
            eroded = Polygon(leaf).buffer(distance, quad_segs=quad_segs, join_style=join)
            boundaries = _polygon_boundaries(eroded)
            if not boundaries:
                raise ValueError(_("The buffer removed a closed contour."))
            results[index] = boundaries
        else:
            batch_indices.append(index)
            batch_leaves.append(leaf)

    if batch_leaves:
        flattened = np.empty(len(batch_leaves), dtype=object)
        flattened[:] = batch_leaves
        buffered = shapely.buffer(flattened, distance, quad_segs=quad_segs, join_style=join)
        for index, leaf, new_leaf in zip(batch_indices, batch_leaves, buffered):
            if only_exterior is True and isinstance(leaf, (LinearRing, LineString)):
                boundaries = _polygon_boundaries(new_leaf)
                if len(boundaries) == 1:
                    new_leaf = boundaries[0]
                else:
                    new_leaf = boundaries
            empty = not new_leaf if isinstance(new_leaf, list) else getattr(new_leaf, 'is_empty', True)
            if empty and not allow_empty:
                raise ValueError(_("The buffer removed a geometry."))
            results[index] = new_leaf

    rebuilt = iter(results)
    return [_rebuild_geometry_tree(d, rebuilt) for d in descriptors]


def scale_geometries_by_center(obj, factor):
    """Scale every leaf geometry by ``factor`` about its own center, keeping the nested list shape."""
    leaves = []
    descriptor = _flatten_geometry_tree(obj, leaves)
    results = [affinity.scale(leaf, xfact=factor, yfact=factor, origin='center') for leaf in leaves]
    return _rebuild_geometry_tree(descriptor, iter(results))


class PassBuffers:
    """
    The buffered geometry of the isolation passes: for each pass, the union of the features buffered by the offset of
    the pass, as Geometry.isolation_geometry() makes it.

    The passes are buffered ahead, a batch at a time, in parallel threads (the vectorized Shapely calls release the
    GIL). Each thread buffers its own copy of the geometry, made from WKB: GEOS geometries used by several threads at
    the same time can corrupt the heap, because GEOS caches data in a geometry (e.g. its envelope) when it is first
    needed.

    The passes are asked for in increasing order, each one once. When the caller stops early, at most one batch minus
    one pass was buffered for nothing.

        buffers = PassBuffers(polygons, offsets, quad_segs)
        for nr_pass in range(len(offsets)):
            buffered = buffers.get(nr_pass)
    """

    def __init__(self, geometry, offsets, quad_segs, abort=None, workers=None):
        """
        :param geometry:    geometry or (nested) list of geometries to buffer
        :param offsets:     the offset distance of each pass
        :type offsets:      list
        :param quad_segs:   the segments of a quarter circle, as given to buffer()
        :type quad_segs:    int
        :param abort:       callable that returns True when the user asked to stop (GracefulException is raised)
        :param workers:     the number of threads; the number of processors if None
        :type workers:      int
        """
        self._wkb = shapely.to_wkb(np.array(flatten_shapely_geometry(geometry), dtype=object))
        self._offsets = list(offsets)
        self._quad_segs = quad_segs
        self._abort = abort
        self._workers = workers or os.cpu_count() or 1
        self._buffered = {}

    def _buffer(self, nr_pass):
        if self._abort is not None and self._abort():
            # graceful abort requested by the user
            raise grace

        geoms = shapely.from_wkb(self._wkb)
        offset = self._offsets[nr_pass]
        if offset == 0:
            return unary_union(geoms)
        return unary_union(shapely.buffer(geoms, offset, quad_segs=self._quad_segs, join_style='round'))

    def get(self, nr_pass):
        """
        :param nr_pass:     the number of the pass, from 0
        :type nr_pass:      int
        :return:            the union of the geometry buffered by the offset of the pass
        """
        if nr_pass not in self._buffered:
            batch = list(range(nr_pass, min(nr_pass + self._workers, len(self._offsets))))
            if len(batch) > 1:
                with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                    self._buffered = dict(zip(batch, executor.map(self._buffer, batch)))
            else:
                self._buffered = {nr_pass: self._buffer(nr_pass)}
        return self._buffered.pop(nr_pass)


def trim_covered_ends(path, radius, parts, parts_tree, covered=None) -> tuple:
    """Return one cutting run and its stock-reaching segments, without adding a lift."""
    coords = np.asarray(path.coords)
    count = len(coords) - 1
    if count < 1:
        return path, None
    segments = shapely.linestrings(np.stack((coords[:-1], coords[1:]), axis=1))
    pairs = parts_tree.query(segments, predicate='dwithin', distance=radius)
    # A centerline inside an old footprint may still cut fresh stock at its sides.
    # Test the full cutter radius; mere tangency removes no area.
    distances = shapely.distance(segments[pairs[0]], [parts[i] for i in pairs[1]])
    active = np.unique(pairs[0][distances < radius])
    reaches_stock = np.zeros(count, dtype=bool)
    reaches_stock[active] = True
    if covered is not None and len(active):
        # Exact old-centerline coverage is sufficient only when the old cutter
        # is at least as wide. This also avoids polygonal round-cap artifacts
        # making an identical, already-cut segment appear productive.
        exact = covered[0].query(segments, predicate='covered_by')
        redundant = exact[0][np.asarray(covered[1])[exact[1]] >= radius]
        active = np.setdiff1d(active, redundant)
    if not len(active):
        return None, segments[:0]
    if len(active) == count:
        return path, segments[reaches_stock]
    if not np.array_equal(coords[0], coords[-1]):
        kept = np.arange(active[0], active[-1] + 1)
        return LineString(coords[active[0]:active[-1] + 2]), segments[kept[reaches_stock[kept]]]
    # Open a ring at its longest covered gap, retaining every productive segment
    # and the internal connectors instead of creating multiple cutting runs.
    cumulative = np.r_[0, np.cumsum(shapely.length(segments))]
    following = np.roll(active, -1)
    gaps = cumulative[following] - cumulative[active + 1]
    gaps[following <= active] += cumulative[-1]
    gap = int(np.argmax(gaps))
    start = following[gap]
    length = (active[gap] - start) % count + 1
    kept = np.arange(start, start + length) % count
    return (LineString(coords[np.arange(start, start + length + 1) % count]),
            segments[kept[reaches_stock[kept]]])


def order_cleanup_paths(paths, app=None):
    """Shorten rapids only; retain endpoints, direction, cutter paths and lift count."""
    if len(paths) < 4:
        return paths
    storage = AppRTreeStorage()
    # Index starts only: choosing a nearer end must not reverse cutting direction.
    storage.get_points = lambda path: [path.coords[0]]
    storage.insert_many(paths[1:-1])
    ordered = [paths[0]]
    current = paths[0].coords[-1]
    for _ in range(len(paths) - 2):
        if app is not None:
            QtWidgets.QApplication.processEvents()
            if app.abort_flag:
                raise grace
        _, path = storage.nearest(current)
        storage.remove(path)
        ordered.append(path)
        current = path.coords[-1]
    ordered.append(paths[-1])

    def travel(sequence):
        return sum(math.dist(a.coords[-1], b.coords[0]) for a, b in zip(sequence, sequence[1:]))

    # Greedy is not always better for directed paths. Fixed first/last paths
    # make the comparison valid for any preceding cut, toolchange or job end XY.
    return ordered if travel(ordered) < travel(paths) else paths


def residual_cleanup(app, domain, diameter, circle_steps, previous_tools, generate_paths, physical=False,
                     boundary_first=True):
    """Target residual stock while keeping the complete cutter in the original domain.

    Shared by NCC and Paint; callers own tool selection, settings and output storage.
    Work component-by-component and index previous paths rather than unioning a board
    of centerlines. A residual narrower than the cutter can use already-cleared room.
    """
    # Paint circle_steps counts a full circle; retain NCC's historical quadrant
    # count. At least 16 segments/quadrant keeps coverage estimates conservative.
    steps = max(16, circle_quad_segs(circle_steps) if physical else circle_steps)
    radius = diameter / 2
    tolerance = float(app.options.get('global_tolerance', 0.005))
    if app.units.upper() == 'IN':
        tolerance /= 25.4

    def sweep(path, distance, segments=None):
        # Preserve NCC's historical coverage accounting; Paint measures physical
        # segment capsules because GEOS can simplify a whole line before buffering.
        if physical and isinstance(path, (LineString, LinearRing)):
            if segments is None:
                coords = np.asarray(path.coords)
                segments = shapely.linestrings(np.stack((coords[:-1], coords[1:]), axis=1))
            return unary_union(shapely.buffer(segments, distance, quad_segs=steps))
        return path.buffer(distance, quad_segs=steps)

    previous = []
    radii = []
    for tool in previous_tools:
        for path in flatten_shapely_geometry(tool.get('solid_geometry', [])):
            previous.append(path)
            radii.append(float(tool['tooldia']) / 2)
    tree = shapely.STRtree(previous)
    max_radius = max(radii, default=0)
    paths = []
    residuals = []
    for component in flatten_shapely_geometry(domain):
        QtWidgets.QApplication.processEvents()
        if app.abort_flag:
            raise grace
        if not isinstance(component, Polygon) or component.is_empty:
            continue
        guard = max(diameter * 1e-9, np.finfo(float).eps * max(abs(v) for v in component.bounds) * 32)
        safe_radius = radius / math.cos(math.pi / (4 * steps)) + guard
        centers = component.buffer(-safe_radius, quad_segs=steps)
        sweeps = []
        for i in tree.query(component.buffer(max_radius, quad_segs=steps)):
            QtWidgets.QApplication.processEvents()
            if app.abort_flag:
                raise grace
            sweeps.append(sweep(previous[i], radii[i]))
        residual = component.difference(unary_union(sweeps))
        min_gain = max(guard * guard, tolerance * diameter)
        reachable = centers.buffer(radius, quad_segs=steps)
        target = residual.intersection(reachable)
        QtWidgets.QApplication.processEvents()
        if app.abort_flag:
            raise grace
        if target.area < min_gain:
            residuals.append(residual)
            continue
        parts = [part for part in flatten_shapely_geometry(target) if not part.is_empty]
        target_parts = parts
        reach = (unary_union(shapely.buffer(parts, radius, quad_segs=steps)) if physical
                 else target.buffer(radius, quad_segs=steps))
        candidates = flatten_shapely_geometry(target.boundary.intersection(centers))
        candidates.extend(flatten_shapely_geometry(centers.boundary.intersection(reach)))
        shapely.prepare(reach)
        shapely.prepare(component)
        boundary = component.boundary
        shapely.prepare(boundary)

        def safe(path):
            # Actual segment distance avoids both GEOS input simplification and
            # repeated large positive buffers. Holes belong to the boundary too.
            if physical:
                return component.covers(path) and not shapely.dwithin(path, boundary, radius + guard)
            return component.covers(path.buffer(safe_radius, quad_segs=steps))

        # Paint uses the full residual for physical gain and pruning, not only
        # the polygonal approximation of the reachable target. Reuse one mutable
        # index so accepting a path does not repeat every stock subtraction.
        if physical:
            parts = flatten_shapely_geometry(residual)
        parts_tree = shapely.STRtree(parts)
        part_areas = shapely.area(parts)

        def gain_and_remaining(swept):
            near = parts_tree.query(swept)
            if not physical:
                return sum(parts[i].intersection(swept).area for i in near), near, None, None
            # One local overlay serves both the gain test and the accepted stock
            # update. Rejected candidates never change the mutable residual.
            remaining = shapely.difference([parts[i] for i in near], swept)
            remaining_areas = shapely.area(remaining)
            gain = float(np.sum(part_areas[near] - remaining_areas))
            # Near the threshold, avoid cancellation in area(A) - area(A-B).
            # Re-evaluate the original intersection test, not a relaxed threshold.
            roundoff = np.finfo(float).eps * max(1.0, float(np.sum(part_areas[near]))) * 16
            if abs(gain - min_gain) <= roundoff:
                gain = sum(parts[i].intersection(swept).area for i in near)
            return gain, near, remaining, remaining_areas

        def candidate_paths():
            prefer_boundaries = physical and boundary_first and bool(previous)
            pending_tree = None
            if prefer_boundaries:
                # Straddle thin residuals directly before painting their larger
                # workspaces. Frequently these paths finish the useful work.
                yield from candidates
                QtWidgets.QApplication.processEvents()
                if app.abort_flag:
                    raise grace
                pending = unary_union(parts).intersection(reachable)
                if pending.area < min_gain:
                    return
                pending_tree = shapely.STRtree(flatten_shapely_geometry(pending))
            # Keep the original neighborhoods: shrinking them to tiny leftovers
            # can lose paths whose grouped gains meet the useful-gain threshold.
            # Dilation still distributes over the target parts, not one expensive
            # multipart buffer. Entire finished workspaces need no generation.
            workspace = (unary_union(shapely.buffer(target_parts, diameter, quad_segs=steps)) if physical
                         else target.buffer(diameter, quad_segs=steps)).intersection(component)
            for pol in flatten_shapely_geometry(workspace):
                QtWidgets.QApplication.processEvents()
                if app.abort_flag:
                    raise grace
                if not isinstance(pol, Polygon) or pol.is_empty:
                    continue
                if pending_tree is not None and not len(pending_tree.query(
                        pol, predicate='dwithin', distance=radius)):
                    continue
                result = generate_paths(pol, 2 * safe_radius)
                if result == 'fail':
                    raise grace
                if result:
                    yield from result
            if not prefer_boundaries:
                yield from candidates

        swept_paths = []
        for candidate in candidate_paths():
            QtWidgets.QApplication.processEvents()
            if app.abort_flag:
                raise grace
            if not reach.intersects(candidate):
                continue
            for path in flatten_shapely_geometry(candidate.intersection(centers)):
                if not isinstance(path, (LineString, LinearRing)) or path.is_empty:
                    continue
                near = parts_tree.query(path, predicate='dwithin', distance=radius)
                # Updated parts only shrink. Their total area is a cheap upper
                # bound on gain, before constructing any segment capsules.
                if (float(np.sum(part_areas[near])) if physical else
                        sum(parts[i].area for i in near)) < min_gain:
                    continue
                simplified = path.simplify(tolerance) if tolerance > 0 else path
                original = path
                if len(simplified.coords) < len(path.coords) and safe(simplified):
                    path = simplified
                used_simplification = path is not original
                stock_segments = None
                if physical:
                    path, stock_segments = trim_covered_ends(path, radius, parts, parts_tree, covered=(tree, radii))
                    # A simplified chord may be wholly covered while the small
                    # bend it replaced still paints a useful sliver.
                    if path is None and used_simplification and safe(original):
                        path, stock_segments = trim_covered_ends(
                            original, radius, parts, parts_tree, covered=(tree, radii))
                        used_simplification = False
                if path is None:
                    continue
                if not safe(path):
                    continue
                # Nonproductive internal connectors remain in the cutting run,
                # but their capsules cannot intersect current stock. Reusing the
                # segment mask avoids unioning them for gain or stock updates.
                swept = sweep(path, radius, stock_segments)
                gain, near, remaining, remaining_areas = gain_and_remaining(swept)
                if gain < min_gain and used_simplification and safe(original):
                    if physical:
                        path, stock_segments = trim_covered_ends(
                            original, radius, parts, parts_tree, covered=(tree, radii))
                    else:
                        path = original
                    if path is None:
                        continue
                    swept = sweep(path, radius, stock_segments)
                    gain, near, remaining, remaining_areas = gain_and_remaining(swept)
                if gain < min_gain:
                    continue
                paths.append(path)
                swept_paths.append(swept)
                if physical:
                    for i, part in zip(near, remaining):
                        parts[i] = part
                    part_areas[near] = remaining_areas
                else:
                    for i in near:
                        parts[i] = parts[i].difference(swept)
        if swept_paths:
            residual = residual.difference(unary_union(swept_paths))
        residuals.append(residual)
    if physical:
        paths = order_cleanup_paths(paths, app=app)
    if app.abort_flag:
        raise grace
    return paths, unary_union(residuals)


def polygon_gap_pairs(polygons, decimals, neighbors=False):
    """
    Distances between polygons, found with an STRtree instead of a check of every pair (O(n log n), not O(n^2)).
    The pairs at the minimum distance are exact. The distances are rounded to 'decimals' and the pairs keep the order of
    a check of every pair (first index, then second index).

    :param polygons:    list of polygons
    :param decimals:    number of decimals for the distances and the locations
    :param neighbors:   False: only the pairs at the minimum distance. True: also all the pairs that are closer than the
                        largest distance from a polygon to its nearest polygon, so each polygon has its nearest pair.
    :return:            dict {distance: [((x1, y1), (x2, y2)), ...]}, the locations are the nearest points of the pair
    """
    geoms = np.empty(len(polygons), dtype=object)
    geoms[:] = list(polygons)
    count = len(geoms)
    if count < 2:
        return {}

    tree = shapely.STRtree(geoms)
    # a pair whose distance rounds to the limit can be up to 1 unit of the last decimal farther away
    pad = 10 ** -decimals
    bounds = shapely.bounds(geoms)
    xmin, ymin, xmax, ymax = shapely.total_bounds(geoms)
    diagonal = np.hypot(xmax - xmin, ymax - ymin)
    # a prepared polygon tests 'dwithin' with an index of its edges; the large copper pours are the expensive side
    vertices = shapely.get_num_coordinates(geoms)
    shapely.prepare(geoms)

    def pairs_within(radius):
        # the pairs of expanded bounding boxes that intersect (cheap), then 1 'dwithin' test for each pair, with the
        # polygon that has more vertices as the prepared side
        boxes = shapely.box(bounds[:, 0] - radius, bounds[:, 1] - radius, bounds[:, 2] + radius, bounds[:, 3] + radius)
        first, second = tree.query(boxes)
        keep = first < second
        first, second = first[keep], second[keep]
        larger = vertices[first] >= vertices[second]
        close = shapely.dwithin(np.where(larger, geoms[first], geoms[second]),
                                np.where(larger, geoms[second], geoms[first]), radius)
        return first[close], second[close]

    # A check of every pair and query_nearest compute the full distance to the large copper pours (O(vertices^2) in
    # GEOS). Grow the radius until every polygon (neighbors) or one polygon (minimum) has a partner: then all the pairs
    # at the limit distance are in the result.
    radius = pad
    covered = np.empty(0, dtype=int)
    while True:
        first, second = pairs_within(radius)
        now_covered = np.union1d(first, second)
        found = now_covered.size == count if neighbors else first.size > 0
        if found or radius > diagonal:
            break
        covered = now_covered
        radius *= 4
    if first.size == 0:
        return {}

    def gaps(first, second, radius):
        # For a pair closer than 'radius', both nearest points are in the intersection of the 2 bounding boxes expanded
        # by 'radius'. A polygon is never cut by its own expanded box, and a point that the clip adds on the expanded
        # box of the other polygon is at least 'radius' away from it, so the distance of the clipped parts is the same.
        near_first = np.empty(len(first), dtype=object)
        near_second = np.empty(len(first), dtype=object)
        for k, (i, j) in enumerate(zip(first, second)):
            rect = (max(bounds[i][0], bounds[j][0]) - radius, max(bounds[i][1], bounds[j][1]) - radius,
                    min(bounds[i][2], bounds[j][2]) + radius, min(bounds[i][3], bounds[j][3]) + radius)
            near_first[k] = shapely.clip_by_rect(geoms[i], *rect)
            near_second[k] = shapely.clip_by_rect(geoms[j], *rect)
        return shapely.distance(near_first, near_second), near_first, near_second

    if neighbors:
        # a polygon with a partner at the previous radius has its nearest polygon closer than radius / 4: the largest
        # nearest distance belongs to a polygon that found its first partner at the last radius
        late = ~(np.isin(first, covered) & np.isin(second, covered))
        exact = gaps(first[late], second[late], radius)[0]
        nearest = np.full(count, np.inf)
        np.minimum.at(nearest, first[late], exact)
        np.minimum.at(nearest, second[late], exact)
        nearest[covered] = -np.inf
        limit = nearest.max() + pad
    else:
        limit = gaps(first, second, radius)[0].min() + pad

    # the exact distances and the nearest points only for the pairs within the limit, clipped with the limit radius
    first, second = pairs_within(limit)
    exact, near_first, near_second = gaps(first, second, limit)

    order = np.lexsort((second, first))
    first, second, exact = first[order], second[order], exact[order]
    near_first, near_second = near_first[order], near_second[order]
    distances = [float('%.*f' % (decimals, d)) for d in exact]
    selected = [k for k, d in enumerate(exact) if d <= limit]
    if not neighbors:
        min_dist = min(distances[k] for k in selected)
        selected = [k for k in selected if distances[k] == min_dist]
    distances = [distances[k] for k in selected]

    lines = shapely.shortest_line(near_first[selected], near_second[selected])
    points = shapely.get_coordinates(lines).reshape(-1, 2, 2)
    pairs = {}
    for dist, ((x1, y1), (x2, y2)) in zip(distances, points.tolist()):
        pairs.setdefault(dist, []).append(
            ((float('%.*f' % (decimals, x1)), float('%.*f' % (decimals, y1))),
             (float('%.*f' % (decimals, x2)), float('%.*f' % (decimals, y2)))))
    return pairs


def isolation_leftover_paths(inner_pass, outer_pass, burn_radius, quad_segs=16, invert=False):
    """
    Paths that clear the copper left between 2 consecutive isolation passes.

    Where the passes around 2 features meet, the band left between the last passes can be too narrow for one more
    pass and wider than what the last pass cuts: strips and triangles of copper stay, however many passes are made.
    The points cut by neither pass are

        buffer(outer_pass, -burn_radius) - buffer(inner_pass, +burn_radius)

    No point of them is farther than (pass step - burn_radius) from their outline, and with an overlap >= 0 that is
    not more than burn_radius, so one path along each outline clears them. The paths stay farther from the copper
    than the inner pass. Leftovers thinner than a tenth of burn_radius are dropped (arc approximation noise).

    :param inner_pass:  the isolation geometry (polygons) of a pass, as made by Geometry.isolation_geometry()
    :param outer_pass:  the isolation geometry (polygons) of the next pass
    :param burn_radius: half of the tool width: what a path cuts on each side; no paths when it is not positive
    :param quad_segs:   the segments of a quarter circle of the buffers
    :param invert:      False for climb milling, True for conventional milling (see milling_orient())
    :return:            list of LinearRing
    """
    if burn_radius <= 0:
        return []

    def polygons(geo):
        # the parts of 1 union: they do not overlap
        return MultiPolygon([p for p in flatten_shapely_geometry(geo) if isinstance(p, Polygon)])

    grown = shapely.buffer(polygons(inner_pass), burn_radius, quad_segs=quad_segs)
    shrunk = shapely.buffer(polygons(outer_pass), -burn_radius, quad_segs=quad_segs)

    paths = []
    for poly in flatten_shapely_geometry(shapely.difference(shrunk, grown)):
        if not isinstance(poly, Polygon) or shapely.buffer(poly, -burn_radius / 20.0).is_empty:
            continue
        # the copper to clear is inside the leftover polygon
        poly = milling_orient(poly, conventional=invert)
        paths.append(LinearRing(poly.exterior.coords))
        paths += [LinearRing(ring.coords) for ring in poly.interiors]
    return paths


def milling_orient(polygon, conventional=False):
    """
    Orient the rings of a polygon whose outline is milled, for a spindle that turns clockwise (M3).

    Climb milling keeps the material on the right of the tool: the exterior goes clockwise and the holes go
    counter-clockwise. Conventional milling reverses both.

    :param polygon:         a Polygon; the material that the cut leaves is on its inside
    :param conventional:    True for conventional milling, False for climb milling
    :return:                the oriented Polygon
    """
    return shapely.geometry.polygon.orient(polygon, sign=1.0 if conventional else -1.0)


def pass_covers_area(envelope, area):
    """
    True when an isolation pass that goes outwards from the copper encloses the whole area. The next passes are
    farther from the copper, so none of them is inside the area.

    :param envelope:    the isolation geometry of the pass (polygons)
    :param area:        the boundary area
    :return:            bool
    """
    polygons = [p for p in flatten_shapely_geometry(envelope) if isinstance(p, Polygon)]
    return bool(polygons) and shapely.covers(unary_union(polygons), area)


def app_option(app, key, fallback=None):
    """
    Look up an option: app.options first, then app.defaults, then the factory defaults, then ``fallback``.

    Shared by Geometry._app_option and FlatCAMObj._app_option.
    """
    defaults = getattr(app, "defaults", None)
    if defaults is None:
        return app.options.get(key, fallback)

    factory_defaults = getattr(defaults, "factory_defaults", {})
    return app.options.get(key, defaults.get(key, factory_defaults.get(key, fallback)))


def circle_quad_segs(steps_per_circle) -> int:
    """
    :param steps_per_circle:    the number of segments of a full circle
    :return:                    the Shapely buffer quad_segs (segments of a quarter circle)
    """
    return max(1, math.ceil(int(steps_per_circle) / 4))


ADAPTIVE_MIN_STEPS = 16
ADAPTIVE_MAX_STEPS = 1024


def adaptive_circle_steps(radius, tolerance, min_steps=ADAPTIVE_MIN_STEPS, max_steps=ADAPTIVE_MAX_STEPS) -> int:
    """
    The segments of a full circle that keep the chords within "tolerance" of the circle:
    N = pi / acos(1 - tolerance / radius), rounded up to a multiple of 4.

    :param radius:      the radius of the circle
    :param tolerance:   the largest distance between a chord and the circle, in the units of the radius
    :param min_steps:   the fewest segments of a circle
    :param max_steps:   the most segments of a circle
    :return:            the number of segments of the full circle
    """
    if radius <= 0 or tolerance <= 0:
        return min_steps
    if tolerance >= radius:
        return min_steps
    steps = math.pi / math.acos(1.0 - tolerance / radius)
    steps = 4 * math.ceil(steps / 4.0)
    return int(min(max(steps, min_steps), max_steps))


def object_circle_steps(options, kind, radius, units='MM') -> int:
    """
    The segments of a full circle of an object kind, as the preferences of that kind set them:
    - 'adaptive' mode: the steps that keep the circle within "<kind>_circle_tolerance" (micrometers),
    - 'fixed' mode: "<kind>_circle_steps" for every circle.

    :param options: the application options (app.options); a key that is missing takes its factory default
    :param kind:    'gerber', 'excellon' or 'geometry'
    :param radius:  the radius of the circle (for a buffer: the buffer distance), in "units"
    :param units:   'MM' or 'IN', the units of the radius
    :return:        the number of segments of the full circle
    """
    from defaults import AppDefaults

    def option(key):
        value = options.get(key) if options is not None else None
        return AppDefaults.factory_defaults.get(key) if value is None else value

    if option('%s_circle_steps_mode' % kind) != 'adaptive':
        return int(option('%s_circle_steps' % kind))
    tolerance = float(option('%s_circle_tolerance' % kind)) / 1000.0     # micrometers -> mm
    if str(units).upper() == 'IN':
        tolerance /= 25.4
    return adaptive_circle_steps(abs(float(radius)), tolerance)


def object_quad_segs(options, kind, radius, units='MM') -> int:
    """The Shapely buffer quad_segs of a circle of an object kind (see object_circle_steps())."""
    return circle_quad_segs(object_circle_steps(options, kind, radius, units))


def gerber_circle_steps(options, radius, units='MM') -> int:
    """The segments of a full circle of a Gerber geometry (see object_circle_steps())."""
    return object_circle_steps(options, 'gerber', radius, units)


def gerber_quad_segs(options, radius, units='MM') -> int:
    """The Shapely buffer quad_segs of a Gerber circle (see object_circle_steps())."""
    return object_quad_segs(options, 'gerber', radius, units)


def seed_band_margin(margin, center):
    """
    Prepares the cut of the rings around a center point from a large margin (clear_polygon_seed()).

    A ring does not touch a polygon or a hole that is entirely nearer to the center than the ring or entirely farther.
    The kept polygons and holes keep their coordinates and the polygons that hold the bounds of the margin are always
    kept (the same envelope): a ring cut from a band is the ring cut from the whole margin, bit for bit.

    :param margin:  MultiPolygon
    :param center:  Point
    :return:        function(near, far) that returns the margin without the polygons and holes that are entirely
                    nearer to the center than near or entirely farther than far
    """
    polygons = shapely.get_parts(margin)
    polygons = polygons[~shapely.is_empty(polygons)]
    rings, ring_polygon = shapely.get_rings(polygons, return_index=True)
    # the first ring of each polygon is its exterior
    is_exterior = np.ones(len(rings), dtype=bool)
    is_exterior[1:] = ring_polygon[1:] != ring_polygon[:-1]
    # the nearest point of the area inside each ring (0 when it holds the center) and its farthest vertex
    ring_near = shapely.distance(center, shapely.polygons(rings))
    center_xy = shapely.get_coordinates(center)[0]
    xy, xy_ring = shapely.get_coordinates(rings, return_index=True)
    ring_far = np.zeros(len(rings))
    np.maximum.at(ring_far, xy_ring, np.hypot(xy[:, 0] - center_xy[0], xy[:, 1] - center_xy[1]))
    bounds = shapely.bounds(polygons)
    bounds_polygons = np.zeros(len(polygons), dtype=bool)
    bounds_polygons[[np.argmin(bounds[:, 0]), np.argmin(bounds[:, 1]),
                     np.argmax(bounds[:, 2]), np.argmax(bounds[:, 3])]] = True

    def band(near, far):
        touch = (ring_far >= near) & (ring_near <= far)
        kept = (bounds_polygons | touch[is_exterior])[ring_polygon] & (is_exterior | touch)
        __, parts = np.unique(ring_polygon[kept], return_inverse=True)
        return shapely.multipolygons(shapely.polygons(rings[kept], indices=parts))

    return band


# a merged polygon without holes that covers less than this part of its bounding box is the band of an outline that
# is not closed (see outline_area())
OUTLINE_BAND_RATIO = 0.05


def outline_area(geometry):
    """
    The area enclosed by the outlines in a geometry: e.g. the board area from the outline in an Edge Cuts Gerber
    (the outline is drawn as a band, a polygon with a hole) or from a shape drawn in a Geometry object.
    Polygons are merged and filled (their holes are ignored) and closed lines (rings, or line pieces that close a
    loop together) give the area they enclose. Inner outlines (cutouts) are not taken out.
    A merged polygon without holes that covers less than OUTLINE_BAND_RATIO of its bounding box is taken as the band
    of an outline that is not closed (e.g. an Edge Cuts outline with a gap) and encloses nothing: as a boundary it
    would remove nearly all the isolation.

    :param geometry:    Shapely geometry or a (nested) list of them
    :return:            Polygon or MultiPolygon, or None if nothing in the geometry encloses an area
    """
    polygons = []
    lines = []
    for geo in flatten_shapely_geometry(geometry):
        if isinstance(geo, Polygon):
            polygons.append(geo if geo.is_valid else shapely.make_valid(geo))
        elif isinstance(geo, (LineString, LinearRing)):
            lines.append(geo)

    areas = []
    # merge the polygons first: a Gerber loaded without buffering keeps one polygon for each segment of the outline
    for geo in flatten_shapely_geometry(unary_union(polygons)):
        if not isinstance(geo, Polygon):
            continue
        minx, miny, maxx, maxy = geo.bounds
        if not geo.interiors and geo.area < OUTLINE_BAND_RATIO * (maxx - minx) * (maxy - miny):
            continue
        areas.append(Polygon(geo.exterior))

    if lines:
        # node the lines (the pieces may only touch) and take the faces they close
        areas += list(shapely.ops.polygonize(unary_union(lines)))

    areas = [a for a in areas if a.is_valid and not a.is_empty and a.area > 0]
    if not areas:
        return None

    merged = unary_union(areas)
    filled = [Polygon(p.exterior) for p in flatten_shapely_geometry(merged) if isinstance(p, Polygon)]
    return unary_union(filled) if filled else None


def clip_paths_to_area(geometry, area):
    """
    The parts of the tool paths of a geometry that lie inside an area. Polygons are taken as their outline rings (the
    path the tool follows around them). Paths completely inside the area are kept as they are.

    :param geometry:    Shapely geometry or a (nested) list of them (polygons and lines)
    :param area:        Polygon or MultiPolygon (e.g. from outline_area())
    :return:            list of LinearRing / LineString; empty if no path reaches into the area
    """
    shapely.prepare(area)
    paths = []
    for geo in flatten_shapely_geometry(geometry):
        if isinstance(geo, Polygon):
            rings = [geo.exterior] + list(geo.interiors)
        elif isinstance(geo, (LineString, LinearRing)):
            rings = [geo]
        else:
            continue

        for ring in rings:
            if area.contains(ring):
                paths.append(ring)
            elif area.intersects(ring):
                paths += [g for g in flatten_shapely_geometry(ring.intersection(area))
                          if isinstance(g, (LineString, LinearRing))]
    return paths


def area_outline_paths(area):
    """
    The outlines of an area (e.g. from outline_area()) as tool paths: one LinearRing per polygon exterior.
    """
    return [LinearRing(p.exterior.coords) for p in flatten_shapely_geometry(area) if isinstance(p, Polygon)]


def boundary_passes_message(name, passes_made, passes):
    """
    The report of an isolation limited to a boundary. The passes stop at the first one that is completely outside the
    boundary: then the copper-free area inside the boundary is cleared. If all the passes were made, it may not be.
    If no pass was made, not even the first pass reaches into the boundary (e.g. the outline of another board).

    :param name:        name of the isolation object (or the isolated object)
    :param passes_made: the number of passes made
    :param passes:      the number of passes requested
    :return:            message in the format of app.inform ('[success] ...', '[WARNING_NOTCL] ...' or
                        '[ERROR_NOTCL] ...')
    """
    if passes_made == 0:
        return '[ERROR_NOTCL] %s: %s' % (name, _("No isolation path is inside the boundary. Check that the boundary "
                                                 "object is the outline of this board."))
    if passes_made < passes:
        return '[success] %s: %s' % (name, _("%d of %d passes, the area inside the boundary is cleared.") %
                                     (passes_made, passes))
    return '[WARNING_NOTCL] %s: %s' % (name, _("all %d passes made; the area inside the boundary may not be cleared "
                                               "yet (more passes are needed for it).") % passes)


def get_bounds(geometry_list: list) -> list:
    """
    Will return limit values for a list of geometries

    :param geometry_list:   List of geometries for which to calculate the bounds limits
    :return:
    """
    xmin = np.inf
    ymin = np.inf
    xmax = -np.inf
    ymax = -np.inf

    for gs in geometry_list:
        try:
            gxmin, gymin, gxmax, gymax = gs.bounds()
            xmin = min([xmin, gxmin])
            ymin = min([ymin, gymin])
            xmax = max([xmax, gxmax])
            ymax = max([ymax, gymax])
        except Exception:
            # log.warning("DEVELOPMENT: Tried to get bounds of empty geometry.")
            pass

    return [xmin, ymin, xmax, ymax]


def arc(center, radius, start, stop, direction, steps_per_circ):
    """
    Creates a list of point along the specified arc.

    :param center:          Coordinates of the center [x, y]
    :type center:           list
    :param radius:          Radius of the arc.
    :type radius:           float
    :param start:           Starting angle in radians
    :type start:            float
    :param stop:            End angle in radians
    :type stop:             float
    :param direction:       Orientation of the arc, "CW" or "CCW"
    :type direction:        string
    :param steps_per_circ:  Number of straight line segments to
                            represent a circle.
    :type steps_per_circ:   int
    :return:                The desired arc, as list of tuples
    :rtype:                 list
    """
    # TODO: Resolution should be established by maximum error from the exact arc.

    da_sign = {"cw": -1.0, "ccw": 1.0}
    if direction == "ccw" and stop <= start:
        stop += 2 * np.pi
    if direction == "cw" and stop >= start:
        stop -= 2 * np.pi

    angle = abs(stop - start)

    # angle = stop-start
    steps = max([int(np.ceil(angle / (2 * np.pi) * steps_per_circ)), 2])
    delta_angle = da_sign[direction] * angle * 1.0 / steps
    thetas = start + delta_angle * np.arange(steps + 1)
    xs = center[0] + radius * np.cos(thetas)
    ys = center[1] + radius * np.sin(thetas)
    return list(zip(xs.tolist(), ys.tolist()))


def arc2(p1, p2, center, direction, steps_per_circ):
    r = np.sqrt((center[0] - p1[0]) ** 2 + (center[1] - p1[1]) ** 2)
    start = np.arctan2(p1[1] - center[1], p1[0] - center[0])
    stop = np.arctan2(p2[1] - center[1], p2[0] - center[0])
    return arc(center, r, start, stop, direction, steps_per_circ)


def arc_angle(start, stop, direction):
    if direction == "ccw" and stop <= start:
        stop += 2 * np.pi
    if direction == "cw" and stop >= start:
        stop -= 2 * np.pi

    angle = abs(stop - start)
    return angle


# def find_polygon(poly, point):
#     """
#     Find an object that object.contains(Point(point)) in
#     poly, which can can be iterable, contain iterable of, or
#     be itself an implementer of .contains().
#
#     :param poly: See description
#     :return: Polygon containing point or None.
#     """
#
#     if poly is None:
#         return None
#
#     try:
#         for sub_poly in poly:
#             p = find_polygon(sub_poly, point)
#             if p is not None:
#                 return p
#     except TypeError:
#         try:
#             if poly.contains(Point(point)):
#                 return poly
#         except AttributeError:
#             pass
#
#     return None


def simplify_paths(paths, tolerance, app=None):
    """
    The same as [path.simplify(tolerance) for path in paths], made with vectorized calls on several threads (GEOS
    releases the GIL). A geometry that is more than once in the list is simplified once, by one thread, and the same
    simplified geometry is returned for each of its places.

    :param paths:       list of Shapely geometries
    :type paths:        list
    :param tolerance:   simplification tolerance
    :type tolerance:    float
    :param app:         optional application for cancellation checks between simplification chunks
    :return:            the simplified geometries, in the same order
    :rtype:             list
    """
    def simplify_piece(piece):
        if app is not None and app.abort_flag:
            raise grace
        result = shapely.simplify(piece, tolerance)
        if app is not None and app.abort_flag:
            raise grace
        return result

    unique = {id(path): path for path in paths}
    geoms = np.empty(len(unique), dtype=object)
    geoms[:] = list(unique.values())

    workers = min(os.cpu_count() or 1, len(geoms) // 16)
    chunks = np.array_split(geoms, max(1, workers * 4))
    if workers < 2:
        simplified = np.concatenate([simplify_piece(piece) for piece in chunks])
    else:
        # more pieces than threads: the paths are not equally long
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pieces = pool.map(simplify_piece, chunks)
            simplified = np.concatenate(list(pieces))

    by_id = dict(zip(unique.keys(), simplified))
    return [by_id[id(path)] for path in paths]


def deepcopy_keep_geometry(obj):
    """
    Like deepcopy() but the Shapely geometries are shared instead of copied: they are immutable and the copy of a
    geometry goes through WKB (seconds for the tools of a big isolation). The dicts, lists and tuples that hold them
    are copied.

    :param obj:     dicts, lists and tuples with Shapely geometries in them (and anything else that deepcopy() copies)
    :return:        the copy
    """
    memo = {}
    seen = set()
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, BaseGeometry):
            memo[id(item)] = item
        elif isinstance(item, (dict, list, tuple)) and id(item) not in seen:
            seen.add(id(item))
            stack.extend(item.values() if isinstance(item, dict) else item)
    return deepcopy(obj, memo)


def to_dict(obj):
    """
    Makes the following types into serializable form:

    * ApertureMacro
    * BaseGeometry

    :param obj:     Shapely geometry.
    :type obj:      BaseGeometry
    :return:        Dictionary with serializable form if ``obj`` was
                    BaseGeometry or ApertureMacro, otherwise returns ``obj``.
    """
    if isinstance(obj, ApertureMacro):
        return {
            "__class__": "ApertureMacro",
            "__inst__": obj.to_dict()
        }
    if isinstance(obj, BaseGeometry):
        return {
            "__class__": "Shply",
            "__inst__": sdumps(obj, rounding_precision=20)
        }
    return obj


# the WKT of a project is made in pieces of about this many coordinates (see geometry_wkt_cache())
WKT_PIECE_COORDS = 10000


def geometry_wkt_cache(data):
    """
    Make the WKT of all the Shapely geometries in a project dictionary (in nested dicts, lists and tuples) with
    vectorized shapely.to_wkt() calls, instead of 1 call per geometry while the JSON is written.
    Use the result with project_json_default().

    The numbers get the shortest text that reads back to the same float (up to 20 decimals, trimmed). The 16 fixed
    decimals of shapely.wkt.dumps() are longer and can lose the last bit of a coordinate below 1.
    The format is still WKT, so older versions of the app read it.

    to_wkt() holds the GIL and the project is saved in a worker thread: the geometries are converted in pieces
    of about WKT_PIECE_COORDS coordinates, so the GUI thread runs between the pieces.

    The cache keeps a reference to each geometry: an id() is unique only among the objects that exist at the same
    time, and the project can change while it is saved.

    :param data:    the project dictionary
    :return:        dict: id(geometry) -> (geometry, WKT text)
    """
    geoms = {}
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, BaseGeometry):
            geoms[id(item)] = item
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)

    if not geoms:
        return {}

    arr = np.empty(len(geoms), dtype=object)
    arr[:] = list(geoms.values())
    ends = np.cumsum(shapely.get_num_coordinates(arr))
    cuts = np.unique(np.searchsorted(ends, np.arange(WKT_PIECE_COORDS, ends[-1], WKT_PIECE_COORDS), side='right'))
    texts = []
    for part in np.split(arr, cuts):
        if len(part):
            texts.extend(shapely.to_wkt(part, rounding_precision=20, trim=True))
    return {key: (geom, text) for (key, geom), text in zip(geoms.items(), texts)}


def project_json_default(wkt_cache):
    """
    The 'default' function of json.dump() for a project save: like to_dict(), but the WKT of a geometry comes
    from a geometry_wkt_cache() of the same data.

    :param wkt_cache:   the dict made by geometry_wkt_cache()
    :return:            a function for the 'default' parameter of json.dump()
    """
    def default(obj):
        if isinstance(obj, BaseGeometry):
            entry = wkt_cache.get(id(obj))
            # the cached text only for the same geometry: a geometry that replaced it can have the same id()
            if entry is not None and entry[0] is obj:
                return {
                    "__class__": "Shply",
                    "__inst__": entry[1]
                }
        return to_dict(obj)
    # save_project_file() writes each distinct geometry once, from this cache
    default.wkt_cache = wkt_cache
    return default


def dict2obj(d):
    """
    Default deserializer.

    :param d:   Serializable dictionary representation of an object
                to be reconstructed.
    :return:    Reconstructed object.
    """
    if '__class__' in d and '__inst__' in d:
        if d['__class__'] == "Shply":
            return sloads(d['__inst__'])
        if d['__class__'] == "ApertureMacro":
            am = ApertureMacro()
            am.from_dict(d['__inst__'])
            return am
        return d
    else:
        return d


# def plotg(geo, solid_poly=False, color="black"):
#     try:
#         __ = iter(geo)
#     except:
#         geo = [geo]
#
#     for g in geo:
#         if type(g) == Polygon:
#             if solid_poly:
#                 patch = PolygonPatch(g,
#                                      facecolor="#BBF268",
#                                      edgecolor="#006E20",
#                                      alpha=0.75,
#                                      zorder=2)
#                 ax = subplot(111)
#                 ax.add_patch(patch)
#             else:
#                 x, y = g.exterior.coords.xy
#                 plot(x, y, color=color)
#                 for ints in g.interiors:
#                     x, y = ints.coords.xy
#                     plot(x, y, color=color)
#                 continue
#
#         if type(g) == LineString or type(g) == LinearRing:
#             x, y = g.coords.xy
#             plot(x, y, color=color)
#             continue
#
#         if type(g) == Point:
#             x, y = g.coords.xy
#             plot(x, y, 'o')
#             continue
#
#         try:
#             __ = iter(g)
#             plotg(g, color=color)
#         except:
#             log.error("Cannot plot: " + str(type(g)))
#             continue

# def alpha_shape(points, alpha):
#     """
#     Compute the alpha shape (concave hull) of a set of points.
#
#     @param points: Iterable container of points.
#     @param alpha: alpha value to influence the gooeyness of the border. Smaller
#                   numbers don't fall inward as much as larger numbers. Too large,
#                   and you lose everything!
#     """
#     if len(points) < 4:
#         # When you have a triangle, there is no sense in computing an alpha
#         # shape.
#         return MultiPoint(list(points)).convex_hull
#
#     def add_edge(edges, edge_points, coords, i, j):
#         """Add a line between the i-th and j-th points, if not in the list already"""
#         if (i, j) in edges or (j, i) in edges:
#             # already added
#             return
#         edges.add( (i, j) )
#         edge_points.append(coords[ [i, j] ])
#
#     coords = np.array([point.coords[0] for point in points])
#
#     tri = Delaunay(coords)
#     edges = set()
#     edge_points = []
#     # loop over triangles:
#     # ia, ib, ic = indices of corner points of the triangle
#     for ia, ib, ic in tri.vertices:
#         pa = coords[ia]
#         pb = coords[ib]
#         pc = coords[ic]
#
#         # Lengths of sides of triangle
#         a = math.sqrt((pa[0]-pb[0])**2 + (pa[1]-pb[1])**2)
#         b = math.sqrt((pb[0]-pc[0])**2 + (pb[1]-pc[1])**2)
#         c = math.sqrt((pc[0]-pa[0])**2 + (pc[1]-pa[1])**2)
#
#         # Semiperimeter of triangle
#         s = (a + b + c)/2.0
#
#         # Area of triangle by Heron's formula
#         area = math.sqrt(s*(s-a)*(s-b)*(s-c))
#         circum_r = a*b*c/(4.0*area)
#
#         # Here's the radius filter.
#         #print circum_r
#         if circum_r < 1.0/alpha:
#             add_edge(edges, edge_points, coords, ia, ib)
#             add_edge(edges, edge_points, coords, ib, ic)
#             add_edge(edges, edge_points, coords, ic, ia)
#
#     m = MultiLineString(edge_points)
#     triangles = list(polygonize(m))
#     return unary_union(triangles), edge_points

# def voronoi(P):
#     """
#     Returns a list of all edges of the voronoi diagram for the given input points.
#     """
#     delauny = Delaunay(P)
#     triangles = delauny.points[delauny.vertices]
#
#     circum_centers = np.array([triangle_csc(tri) for tri in triangles])
#     long_lines_endpoints = []
#
#     lineIndices = []
#     for i, triangle in enumerate(triangles):
#         circum_center = circum_centers[i]
#         for j, neighbor in enumerate(delauny.neighbors[i]):
#             if neighbor != -1:
#                 lineIndices.append((i, neighbor))
#             else:
#                 ps = triangle[(j+1)%3] - triangle[(j-1)%3]
#                 ps = np.array((ps[1], -ps[0]))
#
#                 middle = (triangle[(j+1)%3] + triangle[(j-1)%3]) * 0.5
#                 di = middle - triangle[j]
#
#                 ps /= np.linalg.norm(ps)
#                 di /= np.linalg.norm(di)
#
#                 if np.dot(di, ps) < 0.0:
#                     ps *= -1000.0
#                 else:
#                     ps *= 1000.0
#
#                 long_lines_endpoints.append(circum_center + ps)
#                 lineIndices.append((i, len(circum_centers) + len(long_lines_endpoints)-1))
#
#     vertices = np.vstack((circum_centers, long_lines_endpoints))
#
#     # filter out any duplicate lines
#     lineIndicesSorted = np.sort(lineIndices) # make (1,2) and (2,1) both (1,2)
#     lineIndicesTupled = [tuple(row) for row in lineIndicesSorted]
#     lineIndicesUnique = np.unique(lineIndicesTupled)
#
#     return vertices, lineIndicesUnique
#
#
# def triangle_csc(pts):
#     rows, cols = pts.shape
#
#     A = np.bmat([[2 * np.dot(pts, pts.T), np.ones((rows, 1))],
#                  [np.ones((1, rows)), np.zeros((1, 1))]])
#
#     b = np.hstack((np.sum(pts * pts, axis=1), np.ones((1))))
#     x = np.linalg.solve(A,b)
#     bary_coords = x[:-1]
#     return np.sum(pts * np.tile(bary_coords.reshape((pts.shape[0], 1)), (1, pts.shape[1])), axis=0)
#
#
# def voronoi_cell_lines(points, vertices, lineIndices):
#     """
#     Returns a mapping from a voronoi cell to its edges.
#
#     :param points: shape (m,2)
#     :param vertices: shape (n,2)
#     :param lineIndices: shape (o,2)
#     :rtype: dict point index -> list of shape (n,2) with vertex indices
#     """
#     kd = KDTree(points)
#
#     cells = collections.defaultdict(list)
#     for i1, i2 in lineIndices:
#         v1, v2 = vertices[i1], vertices[i2]
#         mid = (v1+v2)/2
#         _, (p1Idx, p2Idx) = kd.query(mid, 2)
#         cells[p1Idx].append((i1, i2))
#         cells[p2Idx].append((i1, i2))
#
#     return cells
#
#
# def voronoi_edges2polygons(cells):
#     """
#     Transforms cell edges into polygons.
#
#     :param cells: as returned from voronoi_cell_lines
#     :rtype: dict point index -> list of vertex indices which form a polygon
#     """
#
#     # first, close the outer cells
#     for pIdx, lineIndices_ in cells.items():
#         dangling_lines = []
#         for i1, i2 in lineIndices_:
#             p = (i1, i2)
#             connections = filter(lambda k: p != k and
#             (p[0] == k[0] or p[0] == k[1] or p[1] == k[0] or p[1] == k[1]), lineIndices_)
#             # connections = filter(lambda (i1_, i2_): (i1, i2) != (i1_, i2_) and
#             (i1 == i1_ or i1 == i2_ or i2 == i1_ or i2 == i2_), lineIndices_)
#             assert 1 <= len(connections) <= 2
#             if len(connections) == 1:
#                 dangling_lines.append((i1, i2))
#         assert len(dangling_lines) in [0, 2]
#         if len(dangling_lines) == 2:
#             (i11, i12), (i21, i22) = dangling_lines
#             s = (i11, i12)
#             t = (i21, i22)
#
#             # determine which line ends are unconnected
#             connected = filter(lambda k: k != s and (k[0] == s[0] or k[1] == s[0]), lineIndices_)
#             # connected = filter(lambda (i1,i2): (i1,i2) != (i11,i12) and (i1 == i11 or i2 == i11), lineIndices_)
#             i11Unconnected = len(connected) == 0
#
#             connected = filter(lambda k: k != t and (k[0] == t[0] or k[1] == t[0]), lineIndices_)
#             # connected = filter(lambda (i1,i2): (i1,i2) != (i21,i22) and (i1 == i21 or i2 == i21), lineIndices_)
#             i21Unconnected = len(connected) == 0
#
#             startIdx = i11 if i11Unconnected else i12
#             endIdx = i21 if i21Unconnected else i22
#
#             cells[pIdx].append((startIdx, endIdx))
#
#     # then, form polygons by storing vertex indices in (counter-)clockwise order
#     polys = {}
#     for pIdx, lineIndices_ in cells.items():
#         # get a directed graph which contains both directions and arbitrarily follow one of both
#         directedGraph = lineIndices_ + [(i2, i1) for (i1, i2) in lineIndices_]
#         directedGraphMap = collections.defaultdict(list)
#         for (i1, i2) in directedGraph:
#             directedGraphMap[i1].append(i2)
#         orderedEdges = []
#         currentEdge = directedGraph[0]
#         while len(orderedEdges) < len(lineIndices_):
#             i1 = currentEdge[1]
#             i2 = directedGraphMap[i1][0] if directedGraphMap[i1][0] != currentEdge[0] else directedGraphMap[i1][1]
#             nextEdge = (i1, i2)
#             orderedEdges.append(nextEdge)
#             currentEdge = nextEdge
#
#         polys[pIdx] = [i1 for (i1, i2) in orderedEdges]
#
#     return polys
#
#
# def voronoi_polygons(points):
#     """
#     Returns the voronoi polygon for each input point.
#
#     :param points: shape (n,2)
#     :rtype: list of n polygons where each polygon is an array of vertices
#     """
#     vertices, lineIndices = voronoi(points)
#     cells = voronoi_cell_lines(points, vertices, lineIndices)
#     polys = voronoi_edges2polygons(cells)
#     polylist = []
#     for i in range(len(points)):
#         poly = vertices[np.asarray(polys[i])]
#         polylist.append(poly)
#     return polylist
#
#
# class Zprofile:
#     def __init__(self):
#
#         # data contains lists of [x, y, z]
#         self.data = []
#
#         # Computed voronoi polygons (shapely)
#         self.polygons = []
#         pass
#
#     # def plot_polygons(self):
#     #     axes = plt.subplot(1, 1, 1)
#     #
#     #     plt.axis([-0.05, 1.05, -0.05, 1.05])
#     #
#     #     for poly in self.polygons:
#     #         p = PolygonPatch(poly, facecolor=np.random.rand(3, 1), alpha=0.3)
#     #         axes.add_patch(p)
#
#     def init_from_csv(self, filename):
#         pass
#
#     def init_from_string(self, zpstring):
#         pass
#
#     def init_from_list(self, zplist):
#         self.data = zplist
#
#     def generate_polygons(self):
#         self.polygons = [Polygon(p) for p in voronoi_polygons(array([[x[0], x[1]] for x in self.data]))]
#
#     def normalize(self, origin):
#         pass
#
#     def paste(self, path):
#         """
#         Return a list of dictionaries containing the parts of the original
#         path and their z-axis offset.
#         """
#
#         # At most one region/polygon will contain the path
#         containing = [i for i in range(len(self.polygons)) if self.polygons[i].contains(path)]
#
#         if len(containing) > 0:
#             return [{"path": path, "z": self.data[containing[0]][2]}]
#
#         # All region indexes that intersect with the path
#         crossing = [i for i in range(len(self.polygons)) if self.polygons[i].intersects(path)]
#
#         return [{"path": path.intersection(self.polygons[i]),
#                  "z": self.data[i][2]} for i in crossing]


def autolist(obj):
    try:
        if isinstance(obj, (MultiPoint, MultiPolygon, MultiLineString)):
            return obj.geoms
        __ = iter(obj)
        return obj
    except TypeError:
        return [obj]


def three_point_circle(p1, p2, p3):
    """
    Computes the center and radius of a circle from
    3 points on its circumference.

    :param p1:  Point 1
    :param p2:  Point 2
    :param p3:  Point 3
    :return:    center, radius
    """
    # Midpoints
    a1 = (p1 + p2) / 2.0
    a2 = (p2 + p3) / 2.0

    # Normals
    b1 = np.dot((p2 - p1), np.array([[0, -1], [1, 0]], dtype=np.float32))
    b2 = np.dot((p3 - p2), np.array([[0, 1], [-1, 0]], dtype=np.float32))

    # Params
    try:
        T = solve(np.transpose(np.array([-b1, b2])), a1 - a2)
    except Exception:
        # log.error("camlib.three_point_circle() --> %s" % str(e))
        return

    # Center
    center = a1 + b1 * T[0]

    # Radius
    radius = np.linalg.norm(center - p1)

    return center, radius, T[0]


def distance(pt1, pt2):
    return np.sqrt((pt1[0] - pt2[0]) ** 2 + (pt1[1] - pt2[1]) ** 2)


def distance_euclidian(x1, y1, x2, y2):
    return np.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def new_rtree_index(entries=None, points=None, boxes=None):
    """
    Makes an rtree index with the quadratic split. The default R* variant reinserts entries after each delete:
    measured on 20000 points, the quadratic variant is 7.8x faster for a nearest + delete walk (paint_connect),
    2.6x faster for the inserts and also faster for the bulk load, the nearest and the window queries.

    :param entries: optional iterable of (id, bounds, obj) for a bulk load
    :param points:  optional (N, 2) array of points for a bulk load from arrays; the id of a point is its row and the
                    entries have no object (12x faster than a bulk load from a stream, on 55000 points)
    :param boxes:   optional (N, 4) array of (xmin, ymin, xmax, ymax) for a bulk load from arrays; the id of a box is
                    its row and the entries have no object
    :return:        rtree.index.Index
    """
    properties = rtindex.Property()
    properties.variant = rtindex.RT_Quadratic
    # the linear and quadratic variants need a fill factor below 0.5 (the default is 0.7, for R*)
    properties.fill_factor = 0.4
    if boxes is not None and len(boxes):
        boxes = np.ascontiguousarray(boxes, dtype=np.float64).reshape(-1, 4)
        return rtindex.Index((np.arange(len(boxes), dtype=np.int64), np.ascontiguousarray(boxes[:, :2]),
                              np.ascontiguousarray(boxes[:, 2:])), properties=properties)
    if points is not None and len(points):
        points = np.ascontiguousarray(points, dtype=np.float64).reshape(-1, 2)
        return rtindex.Index((np.arange(len(points), dtype=np.int64), points, points), properties=properties)
    if entries is None:
        return rtindex.Index(properties=properties)
    return rtindex.Index(iter(entries), properties=properties)


class AppRTree(object):
    """
    Indexes geometry (Any object with "coords" property containing
    a list of tuples with x, y values). Objects are indexed by
    all their points by default. To index by arbitrary points,
    override self.points2obj.
    """

    def __init__(self):
        # Python RTree Index
        self.rti = new_rtree_index()

        # ## Track object-point relationship
        # Each is list of points in object.
        self.obj2points = []

        # Index is index in rtree, value is index of
        # object in self.obj2points
        self.points2obj = []

        self.get_points = lambda go: go.coords

    def grow_obj2points(self, idx):
        """
        Increases the size of self.obj2points to fit
        idx + 1 items.

        :param idx: Index to fit into list.
        :return: None
        """
        if len(self.obj2points) > idx:
            # len == 2, idx == 1, ok.
            return
        else:
            # len == 2, idx == 2, need 1 more.
            # range(2, 3)
            for i in range(len(self.obj2points), idx + 1):
                self.obj2points.append([])

    def insert(self, objid, obj):
        self.grow_obj2points(objid)
        self.obj2points[objid] = []

        for pt in self.get_points(obj):
            self.rti.insert(len(self.points2obj), (pt[0], pt[1], pt[0], pt[1]), obj=objid)
            self.obj2points[objid].append(len(self.points2obj))
            self.points2obj.append(objid)

    def remove_obj(self, objid, obj):
        # Use all ptids to delete from index
        for i, pt in enumerate(self.get_points(obj)):
            try:
                self.rti.delete(self.obj2points[objid][i], (pt[0], pt[1], pt[0], pt[1]))
            except IndexError:
                pass

    def nearest(self, pt):
        """
        Will raise StopIteration if no items are found.

        :param pt:
        :return:
        """
        return next(self.rti.nearest(pt, objects=True))

    def intersection(self, pt):
        """
        Will raise StopIteration if no items are found.

        :param pt:
        :return:
        """
        return next(self.rti.intersection(pt, objects=True))


class AppRTreeStorage(AppRTree):
    """
    Just like AppRTree it indexes geometry, but also serves
    as storage for the geometry.
    """

    def __init__(self):
        # super(AppRTreeStorage, self).__init__()
        super().__init__()

        self.objects = []

        # Optimization attempt!
        self.indexes = {}

    def insert(self, obj):
        self.objects.append(obj)
        idx = len(self.objects) - 1

        # Note: Shapely objects are not hashable anymore, although
        # there seem to be plans to re-introduce the feature in
        # version 2.0. For now, we will index using the object's id,
        # but it's important to remember that shapely geometry is
        # mutable, i.e. it can be modified to a totally different shape
        # and continue to have the same id.
        # self.indexes[obj] = idx
        self.indexes[id(obj)] = idx

        # super(AppRTreeStorage, self).insert(idx, obj)
        super().insert(idx, obj)

    def insert_many(self, objs, skip_errors=False):
        """
        Inserts many objects in 1 call; the objects keep their order. An empty storage is bulk loaded from arrays
        (measured on 5000 to 50000 paths: about 3x faster than an insert() for each object); see replace().

        :param objs:        iterable of objects
        :param skip_errors: if True, an object whose points cannot be read is left out (see replace())
        :return:            None
        """
        self.replace(inserted=objs, skip_errors=skip_errors)

    # @profile
    def remove(self, obj):
        # See note about self.indexes in insert().
        # objidx = self.indexes[obj]
        objidx = self.indexes[id(obj)]

        # Remove from list
        self.objects[objidx] = None

        # Remove from index
        self.remove_obj(objidx, obj)

    # Cost of 1 point in the quadratic rtree index (new_rtree_index), relative to the bulk load of 1 point
    # (measured with rtree 1.x on 20000 and 100000 points): a delete and an insert cost about 3 times more.
    DELETE_COST = 3
    INSERT_COST = 3

    def replace(self, removed=(), inserted=(), skip_errors=False):
        """
        Removes and inserts many objects. When the deletes and inserts of their points cost more than a new index,
        the index is bulk loaded again from all the objects (the bulk load is about 3 times faster than the
        inserts and the deletes).

        :param removed:     objects that are in the storage
        :param inserted:    new objects
        :param skip_errors: if True, a new object whose points cannot be read (get_points() raises) is left out;
                            for the callers that ignored an exception of insert() for each object
        :return:            None
        """
        removed = list(removed)
        if skip_errors:
            pairs = []
            for obj in inserted:
                try:
                    pairs.append((obj, list(self.get_points(obj))))
                except Exception:
                    pass
            inserted = pairs
        else:
            inserted = [(obj, list(self.get_points(obj))) for obj in inserted]
        if not removed and not inserted:
            return

        removed_pts = sum(len(self.obj2points[self.indexes[id(obj)]]) for obj in removed)
        inserted_pts = sum(len(points) for __, points in inserted)
        live_pts = sum(len(self.obj2points[objid]) for objid, obj in enumerate(self.objects) if obj is not None)
        new_index_pts = live_pts - removed_pts + inserted_pts

        if removed_pts * self.DELETE_COST + inserted_pts * self.INSERT_COST <= new_index_pts:
            for obj in removed:
                self.remove(obj)
            for obj, __ in inserted:
                self.insert(obj)
            return

        for obj in removed:
            self.objects[self.indexes.pop(id(obj))] = None
        self.objects = [obj for obj in self.objects if obj is not None]
        self.objects += [obj for obj, __ in inserted]
        self.indexes = {id(obj): objid for objid, obj in enumerate(self.objects)}

        # the points of the inserted objects are already known
        inserted_points = {id(obj): points for obj, points in inserted}
        self.obj2points = []
        self.points2obj = []
        coords = []
        for objid, obj in enumerate(self.objects):
            start = len(coords)
            points = inserted_points.get(id(obj))
            # only x, y (a point can have a z)
            coords.extend((pt[0], pt[1]) for pt in (self.get_points(obj) if points is None else points))
            self.obj2points.append(list(range(start, len(coords))))
            self.points2obj.extend([objid] * (len(coords) - start))

        # the point ids are the positions in points2obj, so the index entries need no object (an object of an entry
        # is pickled, which is the most of the time of a bulk load from a stream)
        points = np.asarray(coords, dtype=np.float64).reshape(-1, 2)
        self.rti = new_rtree_index(points=points if len(points) else None)

    def get_objects(self):
        return (o for o in self.objects if o is not None)

    def nearest(self, pt):
        """
        Returns the nearest matching points and the object
        it belongs to.

        :param pt: Query point.
        :return: (match_x, match_y), Object owner of
          matching point.
        :rtype: tuple
        """
        # k=1 returns all the points at the smallest distance (ties); the lowest id is the first inserted point, so
        # the result does not depend on how the index was built (insert() or bulk load)
        tidx = min(self.rti.nearest(pt, 1, objects=True), key=lambda item: item.id, default=None)
        if tidx is None:
            raise StopIteration
        # the id of an entry is the point id (an entry of a bulk load has no object)
        return (tidx.bbox[0], tidx.bbox[1]), self.objects[self.points2obj[tidx.id]]

# class myO:
#     def __init__(self, coords):
#         self.coords = coords
#
#
# def test_rti():
#
#     o1 = myO([(0, 0), (0, 1), (1, 1)])
#     o2 = myO([(2, 0), (2, 1), (2, 1)])
#     o3 = myO([(2, 0), (2, 1), (3, 1)])
#
#     os = [o1, o2]
#
#     idx = AppRTree()
#
#     for o in range(len(os)):
#         idx.insert(o, os[o])
#
#     print [x.bbox for x in idx.rti.nearest((0, 0), num_results=20, objects=True)]
#
#     idx.remove_obj(0, o1)
#
#     print [x.bbox for x in idx.rti.nearest((0, 0), num_results=20, objects=True)]
#
#     idx.remove_obj(1, o2)
#
#     print [x.bbox for x in idx.rti.nearest((0, 0), num_results=20, objects=True)]
#
#
# def test_rtis():
#
#     o1 = myO([(0, 0), (0, 1), (1, 1)])
#     o2 = myO([(2, 0), (2, 1), (2, 1)])
#     o3 = myO([(2, 0), (2, 1), (3, 1)])
#
#     os = [o1, o2]
#
#     idx = AppRTreeStorage()
#
#     for o in range(len(os)):
#         idx.insert(os[o])
#
#     #os = None
#     #o1 = None
#     #o2 = None
#
#     print [x.bbox for x in idx.rti.nearest((0, 0), num_results=20, objects=True)]
#
#     idx.remove(idx.nearest((2,0))[1])
#
#     print [x.bbox for x in idx.rti.nearest((0, 0), num_results=20, objects=True)]
#
#     idx.remove(idx.nearest((0,0))[1])
#
#     print [x.bbox for x in idx.rti.nearest((0, 0), num_results=20, objects=True)]
