
from typing import Literal, Sequence, cast
from PyQt6 import QtWidgets
from camlib import list_total_bounds, Geometry, arc, arc_angle, ApertureMacro, grace, flatten_shapely_geometry, translate_geometry, \
    map_geometry, affine_geometry_many, affine_translate_matrix, affine_rotate_matrix, affine_scale_matrix, \
    affine_skew_matrix, affine_mirror_matrix, circle_quad_segs, buffer_geometry_many, gerber_circle_steps, \
    ADAPTIVE_MIN_STEPS

from appParsers.clipper_fuse import fuse_parts
from appParsers.DXF import dxf_to_shapes
from appParsers.SVG import svg_to_shapes, SvgImportError

import numpy as np
import traceback
from copy import deepcopy
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple, Any

from shapely.ops import unary_union, linemerge
import shapely
import shapely.affinity as affinity
from shapely import box as shply_box
from shapely import LinearRing, MultiLineString, LineString, Polygon, MultiPolygon, Point, prepare, is_prepared, simplify
from shapely import STRtree
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry

import ezdxf
from ezdxf import recover
import logging
import re
import sys

import gettext
import builtins

if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class Gerber(Geometry):
    """
    Here it is done all the Gerber parsing.

    **ATTRIBUTES**

    * ``tools`` (dict): The keys are names/identifiers of each aperture.
      The values are dictionaries key/value pairs which describe the aperture. The
      type key is always present and the rest depend on the key:

    +-----------+-----------------------------------+
    | Key       | Value                             |
    +===========+===================================+
    | type      | (str) "C", "R", "O", "P", or "AP" |
    +-----------+-----------------------------------+
    | others    | Depend on ``type``                |
    +-----------+-----------------------------------+
    | geometry      | (list)                  |
    +-----------+-----------------------------------+
    * ``aperture_macros`` (dictionary): Are predefined geometrical structures
      that can be instantiated with different parameters in an aperture
      definition. See ``apertures`` above. The key is the name of the macro,
      and the macro itself, the value, is a ``Aperture_Macro`` object.

    * ``flash_geometry`` (list): List of (Shapely) geometric object resulting
      from ``flashes``. These are generated from ``flashes`` in ``do_flashes()``.

    * ``buffered_paths`` (list): List of (Shapely) polygons resulting from
      *buffering* (or thickening) the ``paths`` with the aperture. These are
      generated from ``paths`` in ``buffer_paths()``.

    **USAGE**::

        g = Gerber()
        g.parse_file(filename)
        g.create_geometry()
        do_something(s.solid_geometry)

    """

    # Type annotations for instance attributes
    tools: Dict[int, Dict[str, Any]]
    aperture_macros: Dict[str, 'ApertureMacro']
    solid_geometry: Any  # Union[Polygon, MultiPolygon, List[Polygon]] - complex type
    follow_geometry: List[Any]
    is_lpc: bool
    source_file: str
    int_digits: int
    frac_digits: int
    gerber_zeros: str
    units: str
    conversion_done: bool
    defective_aperture_detected: bool
    use_buffer_for_union: bool
    steps_per_circle: int
    decimals: int
    ser_attrs: List[str]

    # Parser state of the features that touch the geometry of the objects (see _store()):
    # the load transformation (a, b, d, e) of LM / LR / LS, None while it is the identity
    _xform: Optional[Tuple[float, float, float, float]] = None
    # the records of the open step and repeat block, None when there is none
    _sr_rec: Optional[list] = None
    # the stack of the open aperture blocks (each a dict with its records), None when there is none
    _blocks: Optional[list] = None
    # True while parse_file() feeds the commands: the X2 attributes are then read as attributes, not reported
    _from_file: bool = False

    # defaults = {
    #     "steps_per_circle": 128,
    #     "use_buffer_for_union": True
    # }

    def __init__(self, app, steps_per_circle: int | None=None):
        """
        Use ``gerber.parse_files()`` or ``gerber.parse_lines()`` to populate the object from Gerber source.

        :return: Gerber object
        :rtype: Gerber
        """
        if self.__dict__.get('_Gerber_inited'):
            return

        # Import app type (for type hinting) here to avoid circular import.
        from appMain import App

        app = cast(App, app)
        self.app: App = app
        self.multigeo = None

        # How to approximate a circle with lines.
        # The segments of each circle follow the preferences (see _circle_steps_of()); steps_per_circle is the fixed
        # count: the one given here is used for every circle, the one of the preferences only in the fixed mode.
        self._fixed_steps = steps_per_circle
        if steps_per_circle is None:
            self.steps_per_circle = int(self._app_option("gerber_circle_steps", 64))
        else:
            self.steps_per_circle = steps_per_circle
        self.decimals = self.app.decimals

        # Initialize parent
        Geometry.__init__(self, geo_steps_per_circle=self.steps_per_circle, app=self.app)

        # Number format
        self.int_digits = 3
        """Number of integer digits in Gerber numbers. Used during parsing."""

        self.frac_digits = 4
        """Number of fraction digits in Gerber numbers. Used during parsing."""

        self.gerber_zeros: Literal["L", "T"] = self._app_option("gerber_def_zeros")
        """Zeros in Gerber numbers. If 'L' then remove leading zeros, if 'T' remove trailing zeros. Used during parsing.
        """

        # ## Gerber elements # ##
        '''
        tools = {
            aperture_id:{
                'type':string, 
                'size':float, 
                'width':float,
                'height':float,
                'geometry': [],
            }
        }
        tools[aperture_id]['geometry'] list elements are dicts
        dict = {
            'solid': [],
            'follow': [],
            'clear': []
        }
        '''

        # store the file units here:
        self.units: Literal["IN", "MM"] = self._app_option("gerber_def_units")

        # aperture storage
        self.tools: dict[int | None, dict] = {}

        # Aperture Macros
        self.aperture_macros: dict[str, ApertureMacro] = {}

        # will store the Gerber geometry's as solids
        self.solid_geometry: list[BaseGeometry] | BaseGeometry = Polygon()

        # will store the Gerber geometry's as paths
        self.follow_geometry = []

        # made True when the LPC command is encountered in Gerber parsing
        # it allows adding data into the clear_geometry key of the self.tools[aperture] dict
        self.is_lpc: bool = False

        self.source_file: str = ''

        # X2 / X3 attributes: the file attributes (TF) and the attributes that were set last (TA for the apertures,
        # TO for the objects), each {name: [values]}. The attributes of an aperture are in tools[id]['attributes'].
        self.attributes: dict[str, dict[str, list]] = {'TF': {}, 'TA': {}, 'TO': {}}

        # #############################################################################################################
        # ################################# Parser patterns ###########################################################
        # #############################################################################################################

        # Detect Gerber x2 format
        self.gx2_re = re.compile(r'%TF\.FileFunction.*')

        # FS - Format Specification
        # The format of X and Y must be the same!
        # L-omit leading zeros, T-omit trailing zeros, D-no zero suppression
        # A-absolute notation, I-incremental notation
        self.fmt_re = re.compile(r'%?FS([LTD])?([AI])X(\d)(\d)Y\d\d\*%?$')
        self.fmt_re_alt = re.compile(r'%FS([LTD])?([AI])X(\d)(\d)Y\d\d\*MO(IN|MM)\*%$')
        self.fmt_re_orcad = re.compile(r'(G\d+)*\**%FS([LTD])?([AI]).*X(\d)(\d)Y\d\d\*%$')

        # Mode (IN/MM)
        self.mode_re = re.compile(r'^%?MO(IN|MM)\*%?$')

        # Comment G04|G4
        self.comm_re = re.compile(r'^G0?4(.*)$')

        # AD - Aperture definition
        # Aperture Macro names: Name = [a-zA-Z_.$]{[a-zA-Z_.0-9]+}
        # NOTE: Adding "-" to support output from Upverter.
        self.ad_re = re.compile(r'^%ADD(\d\d+)([a-zA-Z_$.][a-zA-Z0-9_$.\-]*)(?:,(.*))?\*%$')

        # AM - Aperture Macro
        # Beginning of macro (Ends with *%):
        # self.am_re = re.compile(r'^%AM([a-zA-Z0-9]*)\*')

        # Tool change
        # May begin with G54 but that is deprecated
        self.tool_re = re.compile(r'^(?:G54)?D(\d\d+)\*$')

        # G01... - Linear interpolation plus flashes with coordinates
        # Operation code (D0x) missing is deprecated... oh well I will support it.
        # Added \s* to allow optional space after G01 (e.g., "G01 X0Y0D02*")
        self.lin_re = re.compile(r'^(?:G0?(1)\s*)?(?=.*X(%s))?(?=.*Y(%s))?[XY][^DIJ]*(?:D0?([123]))?\*$' %
                                (_COORD_PATTERN, _COORD_PATTERN))

        # Operation code alone, usually just D03 (Flash)
        # May begin with G55 but that is deprecated
        self.opcode_re = re.compile(r'^(?:G55)?D0?([123])\*$')

        # G02/3... - Circular interpolation with coordinates
        # 2-clockwise, 3-counterclockwise
        # Operation code (D0x) missing is deprecated... oh well I will support it.
        # Optional start with G02 or G03, optional end with D01 or D02 with
        # optional coordinates but at least one in any order.
        # Added \s* to allow optional space after G02/G03
        self.circ_re = re.compile(
            (r'^(?:G0?([23])\s*)?(?=.*X(%s))?(?=.*Y(%s))' +
             r'?(?=.*I(%s))?(?=.*J(%s))?[XYIJ][^D]*(?:D0([12]))?\*$') %
            (_COORD_PATTERN, _COORD_PATTERN, _COORD_PATTERN, _COORD_PATTERN)
        )

        # G01/2/3 Occurring without coordinates
        # Added \s* to allow optional space after G code
        self.interp_re = re.compile(r'^(?:G0?([123]))\s*\*')

        # Single G74 or multi G75 quadrant for circular interpolation
        self.quad_re = re.compile(r'^G7([45]).*\*$')

        # Region mode on
        # In region mode, D01 starts a region
        # and D02 ends it. A new region can be started again
        # with D01. All contours must be closed before
        # D02 or G37.
        self.regionon_re = re.compile(r'^G36\*$')

        # Region mode off
        # Will end a region and come off region mode.
        # All contours must be closed before D02 or G37.
        self.regionoff_re = re.compile(r'^G37\*$')

        # End of file
        self.eof_re = re.compile(r'^M02\*')

        # IP - Image polarity
        self.pol_re = re.compile(r'^%?IP(POS|NEG)\*%?$')

        # LP - Level polarity
        self.lpol_re = re.compile(r'^%LP([DC])\*%$')

        # Units (OBSOLETE)
        self.units_re = re.compile(r'^G7([01])\*$')

        # Absolute/Relative G90/1 (OBSOLETE)
        self.absrel_re = re.compile(r'^G9([01])\*$')

        # Aperture macros
        self.am1_re = re.compile(r'^%AM([^*]+)\*([^%]+)?(%)?$')
        self.am2_re = re.compile(r'(.*)%$')

        # flag to store if a conversion was done. It is needed because multiple units declarations can be found
        # in a Gerber file (normal or obsolete ones)
        self.conversion_done: bool = False

        # Flag to detect if an aperture is used without definition
        self.defective_aperture_detected: bool = False

        self.use_buffer_for_union = self._app_option("gerber_use_buffer_for_union")

        # Attributes to be included in serialization
        # Always append to it because it carries contents
        # from Geometry.
        # True when the native aperture metadata can no longer be trusted to describe the composed geometry.
        # The export then writes the composed solid_geometry as regions. A project without it loads as False.
        self.export_as_regions: bool = False

        self.ser_attrs += ['int_digits', 'frac_digits', 'aperture_macros', 'source_file', 'export_as_regions',
                           'attributes']

        self._Gerber_inited = True

    # Aperture types the native Gerber writer can declare (appIO.export_gerber).
    NATIVE_WRITER_APERTURE_TYPES = ('C', 'R', 'O')

    def needs_region_export(self) -> bool:
        """
        Decide if the Gerber export must write the composed solid_geometry as regions instead of
        the native aperture records. The result is True when any of these holds:

        * ``export_as_regions`` is set (the aperture metadata no longer matches the geometry);
        * an aperture is of a type the writer cannot declare (anything but C, R, O; the region
          'aperture' 0 of type 'REG' is written natively, so it does not count);
        * a record holds a non-empty 'clear' geometry. Records have no global order, so the native
          writer, which groups them per aperture, cannot keep a dark -> clear -> dark sequence;
        * a record of an 'R' aperture is not a flash of that aperture. The parser draws a track of an 'R'
          aperture as a box around its end points, and the native writer would flash the aperture at the
          end point instead;
        * the object comes from a PCB document import (``obj_options['pcbdoc_import']``).

        The method only reads the object.

        :return: True if the region export must be used
        """
        if getattr(self, 'export_as_regions', False):
            return True
        obj_options = getattr(self, 'obj_options', None) or {}
        if obj_options.get('pcbdoc_import', False):
            return True
        for apid, tool in (getattr(self, 'tools', None) or {}).items():
            if apid == 0 and tool.get('type') == 'REG':
                pass
            elif tool.get('type') not in Gerber.NATIVE_WRITER_APERTURE_TYPES:
                return True
            elif tool.get('hole'):
                # the native writer declares no hole
                return True
            for record in tool.get('geometry', ()):
                clear = record.get('clear')
                # None, or a non-geometry like the [] of older code, counts as no clear geometry
                if not getattr(clear, 'is_empty', True):
                    return True
                if tool.get('type') == 'R' and not _is_rectangle_flash(tool, record):
                    return True
        return False

    def region_export_tools(self) -> dict:
        """
        Build a tools dictionary that holds the composed ``solid_geometry`` (polarity already applied) as
        region records under the region 'aperture' 0, ready for ``GerberObject.export_gerber``.

        Method: ``feature_access.normalize_polygonal`` (flatten the geometry, repair every part with
        ``make_valid``, keep the polygonal parts, union them), so the result is a valid set of disjoint polygons
        with their holes. The polygons are sorted by the area of their exterior, largest first. The writer draws each polygon as a dark region
        for the exterior, followed by clear regions for its interior rings, then switches back to dark.
        A polygon that lies inside the hole of another one has a smaller exterior, so it is drawn after
        the clear ring and is not erased by it. That keeps holes and nested islands.

        The method does not change the object.

        :return: tools dictionary with 1 'REG' aperture, key 0
        """
        # imported here: appParsers.feature_access imports camlib, like this module
        from appParsers.feature_access import normalize_polygonal
        solid = normalize_polygonal(self.solid_geometry if self.solid_geometry is not None else [])
        polygons = [solid] if isinstance(solid, Polygon) else list(solid.geoms)
        polygons = [p for p in polygons if not p.is_empty]
        polygons.sort(key=lambda poly: Polygon(poly.exterior).area, reverse=True)
        return {0: {'type': 'REG', 'size': 0, 'geometry': [{'solid': poly} for poly in polygons]}}

    def aperture_parse(
        self,
        apertureId: str,
        apertureType: str,
        apParameters: Optional[str]
    ) -> Optional[int]:
        """
        Parse gerber aperture definition into dictionary of apertures.
        The following kinds and their attributes are supported:

        * *Circular (C)*: size (float)
        * *Rectangle (R)*: width (float), height (float)
        * *Obround (O)*: width (float), height (float).
        * *Polygon (P)*: diameter(float), vertices(int), [rotation(float)]
        * *Aperture Macro (AM)*: macro (ApertureMacro), modifiers (list)

        :param apertureId: The Id of the aperture being defined.
        :param apertureType: Type of the aperture.
        :param apParameters: Parameters of the aperture.
        :type apertureId: str
        :type apertureType: str
        :type apParameters: str
        :return: Identifier of the aperture.
        :rtype: str
        """
        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        # Found some Gerber with a leading zero in the aperture id and the
        # referenced it without the zero, so this is a hack to handle that.
        apid = int(apertureId)

        # Could be empty for aperture macros
        paramList = [] if apParameters is None or apParameters == "" else apParameters.split('X')

        # The hole of a standard aperture follows its parameters: a circular hole (its diameter) or, deprecated,
        # a rectangular one (width and height). A hole of size 0 is no hole.
        if apertureType == "C":  # Circle, example: %ADD11C,0.1*%  or with a hole  %ADD11C,1X0.5*%
            self.tools[apid] = {
                "type": "C",
                "size": float(paramList[0])
            }
            self._set_hole(apid, paramList[1:3])
            return apid

        if apertureType == "R":  # Rectangle, example: %ADD15R,0.05X0.12*%
            self.tools[apid] = {
                "type": "R",
                "width": float(paramList[0]),
                "height": float(paramList[1]),
                "size": np.sqrt(float(paramList[0]) ** 2 + float(paramList[1]) ** 2)}  # Hack
            self._set_hole(apid, paramList[2:4])
            return apid

        if apertureType == "O":  # Obround
            self.tools[apid] = {
                "type": "O",
                "width": float(paramList[0]),
                "height": float(paramList[1]),
                "size": np.sqrt(float(paramList[0]) ** 2 + float(paramList[1]) ** 2)}  # Hack
            self._set_hole(apid, paramList[2:4])
            return apid

        if apertureType == "P":  # Polygon (regular)
            self.tools[apid] = {
                "type": "P",
                "diam": float(paramList[0]),
                "nVertices": int(paramList[1]),
                "size": float(paramList[0])}  # Hack
            if len(paramList) >= 3:
                self.tools[apid]["rotation"] = float(paramList[2])
            self._set_hole(apid, paramList[3:5])
            return apid

        if apertureType in self.aperture_macros:
            self.tools[apid] = {
                "type": "AM",
                # "size": 0.0,
                "macro": self.aperture_macros[apertureType],
                "modifiers": paramList
            }
            return apid

        self.app.log.warning("Aperture not implemented: %s" % str(apertureType))
        return None

    def _set_hole(self, apid: int, values: List[str]) -> None:
        """Store the hole of the aperture ('hole': (diameter,) or (width, height)) when the definition has one."""
        try:
            hole = tuple(float(value) for value in values)
        except ValueError:
            self.app.log.warning("Aperture %d: the hole is not valid and it is ignored." % apid)
            return
        if hole and all(value > 0 for value in hole):
            self.tools[apid]["hole"] = hole

    @staticmethod
    def _read_file_function(text: str) -> list:
        """
        The value list of the first ``%TF.FileFunction,...*%`` attribute of a Gerber text, or an empty list.

        :param text:    the content of the Gerber file
        :return:        for example ['Profile', 'NP']
        """
        match = re.search(r'%TF\.FileFunction,([^*%]*)', text)
        if not match:
            return []
        return [item.strip() for item in match.group(1).split(',') if item.strip()]

    def parse_file(self, filename: str, follow: bool = False) -> Optional[str]:
        """
        Calls Gerber.parse_lines() with the commands of the given file, one command for each item (see
        split_gerber_commands()). A line with several commands is split and a command spread over several lines
        is joined.

        The following line is split into two::

            G54D11*G36*

        First is ``G54D11*`` and seconds is ``G36*``.

        :param filename:        Gerber file to parse.
        :type filename:         str
        :param follow:          If true, will not create polygons, just lines
                                following the gerber path.
        :type follow:           bool
        :return:                None
        """

        # a Gerber file is UTF-8 (the commands are ASCII): a byte that is not UTF-8, in a comment or an attribute
        # text, is replaced and does not stop the parsing
        with open(filename, 'r', encoding='utf-8-sig', errors='replace') as gfile:

            read_gfile = gfile.read()

            # the X2 file function (for example ['Profile', 'NP']) is kept: the %TF lines are removed below
            self.file_function = self._read_file_function(read_gfile)
            obj_options = getattr(self, 'obj_options', None)
            if isinstance(obj_options, dict):
                obj_options['file_function'] = list(self.file_function)

            # the attribute blocks (%TF, %TA, %TO, %TD) are commands of their own: the parser keeps them in
            # self.attributes
            processed_lines = split_gerber_commands(read_gfile)
            self._from_file = True
            try:
                ret_val = self.parse_lines(processed_lines)
            finally:
                self._from_file = False

            if ret_val == 'fail':
                return 'fail'
            elif ret_val == "defective":
                return "defective"
            elif ret_val == 'drill':
                return 'drill_gx2'
            else:
                return

    def _fold_layers(self, layers: list) -> BaseGeometry:
        """
        Compose the layers 1 by 1, in file order, starting from the current ``self.solid_geometry``.
        This is the sequential union / difference done at every polarity change.

        :param layers: list of (polarity, parts), polarity is 'D' (dark) or 'C' (clear), parts is a list of geometries
        :return: the composed geometry
        """
        result = self.solid_geometry
        for polarity, parts in layers:
            if polarity == 'D':
                result = result.union(unary_union(parts))
            else:
                result = result.difference(unary_union(parts))
        return result

    @staticmethod
    def _layer_parts(parts: list) -> np.ndarray:
        """
        The union of the parts of one layer as an array of disjoint polygons (GEOS: on a layer of thousands of
        parts it is faster than the Clipper2 fusion, which is used once for the final result).

        :param parts: list of geometries
        :return: numpy object array of non-empty Polygons
        """
        fused = [geo for geo in shapely.get_parts(unary_union(parts)) if isinstance(geo, Polygon)]
        polys = np.empty(len(fused), dtype=object)
        polys[:] = fused
        return polys[~shapely.is_empty(polys)] if len(polys) else polys

    def _compose_layers(self, layers: list) -> BaseGeometry:
        """
        Compose the layers of the file: the result is the union of every dark layer D(k) minus the union of all
        the clear layers C(j) with j > k. A STRtree finds, in one query for the whole layer, the dark polygons that
        a later clear layer touches: only those are cut, the other polygons pass through, and the surviving polygons
        are fused once. On a GEOS error it falls back to _fold_layers().

        :param layers: list of (polarity, parts), polarity is 'D' (dark) or 'C' (clear), parts is a list of geometries
        :return: the composed geometry
        """
        try:
            merged = [(polarity, self._layer_parts(parts)) for polarity, parts in layers]

            clear_polys = []
            clear_index = []
            for idx, (polarity, polys) in enumerate(merged):
                if polarity == 'C' and len(polys) > 0:
                    clear_polys.extend(polys)
                    clear_index.extend([idx] * len(polys))
            tree = None
            clear_arr = None
            if clear_polys:
                clear_arr = np.empty(len(clear_polys), dtype=object)
                clear_arr[:] = clear_polys
                tree = STRtree(clear_arr)
            clear_index = np.array(clear_index, dtype=int)

            pieces = []
            for idx, (polarity, polys) in enumerate(merged):
                if polarity != 'D' or len(polys) == 0:
                    continue
                if tree is None:
                    pieces.extend(polys)
                    continue

                src, hit = tree.query(polys, predicate='intersects')
                keep = clear_index[hit] > idx
                src, hit = src[keep], hit[keep]
                touched = np.unique(src)
                untouched = np.ones(len(polys), dtype=bool)
                untouched[touched] = False
                pieces.extend(polys[untouched])
                for part_idx in touched:
                    cutter = shapely.union_all(clear_arr[hit[src == part_idx]])
                    cut = polys[part_idx].difference(cutter)
                    if not cut.is_empty:
                        pieces.append(cut)

            if not pieces:
                return self.solid_geometry
            if isinstance(self.solid_geometry, BaseGeometry) and not self.solid_geometry.is_empty:
                pieces.append(self.solid_geometry)
            fused = fuse_parts(flatten_shapely_geometry(pieces))
            if fused:
                return MultiPolygon(fused)
            return unary_union(pieces)
        except shapely.errors.GEOSException as err:
            self.app.log.debug("Gerber polarity layers: fast composition failed (%s), using the sequential fold." %
                               str(err))
            return self._fold_layers(layers)

    def parse_lines(self, glines: Sequence[str]) -> Optional[str]:
        """
        Parse Gerber commands (see _parse_lines()). The shapes of the apertures are cached while the lines are parsed.

        :param glines: Gerber code as list of strings, one command or more in each element
        :return: only errors/warnings
        """
        self._shape_cache = {}
        try:
            return self._parse_lines(glines)
        finally:
            self._shape_cache = None

    # @profile
    def _parse_lines(self, glines: Sequence[str]) -> Optional[str]:
        """
        Main Gerber parser. Reads Gerber and populates ``self.paths``, ``self.tools``,
        ``self.flashes``, ``self.regions`` and ``self.units``.

        :param glines: Gerber code as list of strings, each element being
            one line of the source file.
        :type glines: list
        :return: only errors/warnings
        :rtype: str
        
        ================================================================
        ARCHITECTURE NOTES (2026-03-13)
        ================================================================
        This function uses scattered local variables (not a state class)
        for maximum stability. A ParserState dataclass was attempted but
        reverted after introducing edge-case bugs.
        
        WHY THIS APPROACH:
        - Scattered variables have implicit guards that prevent edge cases
        - A state class would require auditing all 1300+ lines for state access
        - Manual testing cannot catch all edge cases - automated tests needed first
        
        SAFE IMPROVEMENTS ALREADY MADE:
        - _add_path_geometry_to_buffers() - extracts repeated path->geometry pattern
        - _add_flash_to_buffers() - extracts flash geometry creation
        - Type hints on method signatures
        - Section header comments (the ### blocks)
        
        DO NOT refactor state management without:
        1. Comprehensive automated tests (see tests/test_gerber_parser.py)
        2. Full audit of all state interactions
        3. Testing with real Gerber files from multiple EDA tools
        
        See docs/plans/2026-03-13-gerber-parser-state-machine.md for full history.
        ================================================================
        """

        is_excellon_gx2 = False

        # ###############################################################
        # #########  PARSER STATE VARIABLES  ############################
        # #########  Grouped by function for maintainability  ###########
        # ###############################################################
        
        # Coordinates of the current path, each is [x, y]
        path = []

        # this is for temporary storage of solid geometry until it is added to poly_buffer
        geo_s = None

        # this is for temporary storage of follow geometry until it is added to follow_buffer
        geo_f = None

        # Polygons are stored here until there is a change in polarity.
        # Only then they are combined via unary_union and added or
        # subtracted from solid_geometry. This is ~100 times faster than
        # applying a union for every new polygon.
        poly_buffer: list[Polygon] = []

        # Finished layers of equal polarity as (polarity, poly_buffer). A layer ends at every %LP..*% command. They
        # are composed 1 time at the end of the file (see _compose_layers()), not 1 by 1 at every polarity change.
        earlier_layers: list[tuple[str, list]] = []

        # store here the follow geometry
        follow_buffer = []

        last_path_aperture: int | None = None
        current_aperture: int | None | Literal["failure"] = None

        # 1,2 or 3 from "G01", "G02" or "G03"
        current_interpolation_mode : int | None = None

        # 1 or 2 from "D01" or "D02"
        # Note this is to support deprecated Gerber not putting
        # an operation code at the end of every coordinate line.
        current_operation_code = None

        # Current coordinates
        current_x = 0
        current_y = 0

        current_d = None

        # Absolute (True) or Incremental (False) coordinates: FS...A / FS...I, G90 / G91
        abs_coords = True

        # How to interpret circular interpolation: SINGLE or MULTI
        quadrant_mode = None

        # Indicates we are parsing an aperture macro
        current_macro = None

        # Indicates the current polarity: D-Dark, C-Clear
        current_polarity = 'D'

        # If a region is being defined
        making_region: bool = False

        # ### Parsing starts here ## ##
        line_num: int = 0

        s_tol = float(self._app_option("gerber_simp_tolerance"))

        # Cache options lookups for performance
        use_simplification = self._app_option("gerber_simplification")
        use_buffering = self._app_option("gerber_buffering")
        extra_buffering = self._app_option("gerber_extra_buffering")
        clean_apertures = self._app_option("gerber_clean_apertures")

        # None: the segments of each circle follow its radius (see _circle_steps_of())
        steps = None

        # Source file accumulation (list + join is faster than string concatenation)
        source_lines = []

        # The state of the features that are rare in a file
        self.attributes = {'TF': {}, 'TA': {}, 'TO': {}}
        self.image_domain = None     # the bounds inside which a negative image (IPNEG) was inverted
        self._xform = None
        self._sr_rec = None
        self._blocks = None
        # the load transformation as [mirror (N, X, Y or XY), rotation in degrees, scale]
        load_xform: list = ['N', 0.0, 1.0]
        # the open step and repeat: (nx, ny, di, dj) or None
        sr_params: tuple | None = None
        # the deprecated image commands
        image_negative = False
        image_scale = (1.0, 1.0)
        image_mirror = (False, False)
        image_rotation = 0
        image_offset = (0.0, 0.0)
        # the warning about decimal points is made once
        decimal_warned = False

        def change_polarity(new_polarity: str) -> None:
            """Close the current layer and make new_polarity (D or C) the polarity of the next objects."""
            nonlocal path, poly_buffer, current_polarity
            if len(path) > 1 and current_polarity != new_polarity:
                # finish the current path and add it to the storage
                self._add_path_geometry_to_buffers(path, last_path_aperture, poly_buffer, follow_buffer, steps)
                path = [path[-1]]

            # the open path above was closed with the old polarity; from here on the new polarity is valid
            self.is_lpc = new_polarity == 'C'

            # --- Close the layer ---
            # The layer is not applied to solid_geometry here. All layers are composed at the end of the file.
            if len(poly_buffer) > 0:
                earlier_layers.append((current_polarity, poly_buffer))
                poly_buffer = []

            current_polarity = new_polarity

        def finish_step_repeat() -> None:
            """Close the open step and repeat: the objects made inside it are copied to the other positions."""
            nonlocal path, sr_params
            if sr_params is None:
                return
            if len(path) > 1:
                self._add_path_geometry_to_buffers(path, last_path_aperture, poly_buffer, follow_buffer, steps)
                path = [path[-1]]
            records = self._sr_rec
            nx, ny, di, dj = sr_params
            self._sr_rec = None
            sr_params = None
            end_polarity = current_polarity
            for iy in range(ny):
                for ix in range(nx):
                    if ix == 0 and iy == 0:
                        continue
                    dx, dy = ix * di, iy * dj
                    for aperture_id, geo_dict, is_clear in records:
                        polarity = 'C' if is_clear else 'D'
                        if polarity != current_polarity:
                            change_polarity(polarity)
                        moved = {key: _translate_shape(geo, dx, dy) for key, geo in geo_dict.items()}
                        if 'follow' in moved:
                            follow_buffer.append(moved['follow'])
                        poly = moved.get('clear') if is_clear else moved.get('solid')
                        if poly is not None:
                            poly_buffer.append(poly)
                        self._store(aperture_id, moved)
            if current_polarity != end_polarity:
                change_polarity(end_polarity)

        def set_load_transform() -> None:
            self._xform = _load_matrix(*load_xform)

        self.app.inform.emit('%s %d %s.' % (_("Gerber processing. Parsing"), len(glines), _("Lines").lower()))
        try:
            for gline in glines:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                line_num += 1
                source_lines.append(gline)

                # Cleanup #
                gline = gline.strip(' \r\n')
                # self.app.log.debug("Line=%3s %s" % (line_num, gline))

                # The commands are upper case (the format is case-sensitive, the lower case is accepted). Not changed
                # are the comments, the attributes (their values are text) and the aperture macros ('x' is the
                # multiplication in them).
                if not gline.isupper() and current_macro is None:
                    head = gline[:3].upper()
                    if head == '%AD':
                        # the name of an aperture macro keeps its case
                        name = _AD_NAME_RE.match(gline)
                        if name and name.group(1).upper() in ('C', 'R', 'O', 'P'):
                            gline = gline.upper()
                        else:
                            gline = gline[:4].upper() + gline[4:]
                    elif head != 'G04' and head != '%AM' and gline[:2].upper() != 'G4' and \
                            head[1:] not in _ATTRIBUTE_KEYS:
                        gline = gline.upper()

                # ###############################################################
                # ################   Ignored lines   ############################
                # ################     Comments      ############################
                # ###############################################################
                # first character and '%' presence select the patterns to try (most lines are coordinates)
                first_char = gline[:1]
                has_pct = '%' in gline
                match = self.comm_re.search(gline) if first_char == 'G' else None
                if match:
                    continue

                # ######################################################################################################
                # ######## Detect GERBER X2 format #####################################################################
                # ######################################################################################################
                match = self.gx2_re.search(gline) if has_pct and not self._from_file else None
                if match:
                    self.app.log.warning('Gerber X2 format detected !!!')
                    self.app.inform.emit(
                        '[WARNING] %s' % _('Gerber X2 format detected. Parsing may not be done correctly.'))
                    if 'Drill' in gline:
                        self.app.log.warning('Drill file in Gerber X2 format detected !!!')
                        self.app.inform.emit(
                            '[WARNING] %s' % _('Drill file Gerber X2 format detected. '
                                               'Parsing may not be done correctly.'))
                        is_excellon_gx2 = True
                    continue

                # ###############################################################
                # ################  Polarity change #############################
                # ########   Example: %LPD*% or %LPC*%        ###################
                # ########   If polarity changes, creates geometry from current #
                # ########    buffer, then adds or subtracts accordingly.       #
                # ###############################################################
                match = self.lpol_re.search(gline) if has_pct else None
                if match:
                    change_polarity(match.group(1))
                    continue

                # ################################################################
                # ###  Step and repeat, aperture blocks, load transformations,  ##
                # ###  deprecated image commands and attributes               ###
                # ################################################################
                if has_pct and current_macro is None and gline[1:3] in _EXTENDED_KEYS:
                    key = gline[1:3]
                    body = gline[3:-2] if gline.endswith('*%') else gline[3:]

                    if key in _ATTRIBUTE_KEYS:
                        self._read_attribute(key, body)
                        continue

                    elif key == 'SR':
                        # %SRX3Y2I5J4*% opens a block, %SR*% closes it
                        finish_step_repeat()
                        match = _SR_RE.match(body)
                        if match:
                            sr_params = (max(int(match.group(1)), 1), max(int(match.group(2)), 1),
                                         float(match.group(3)), float(match.group(4)))
                            if len(path) > 1:
                                self._add_path_geometry_to_buffers(
                                    path, last_path_aperture, poly_buffer, follow_buffer, steps)
                                path = [path[-1]]
                            self._sr_rec = []
                        elif body:
                            self.app.log.warning("Invalid step and repeat ignored. (%d): %s" % (line_num, gline))
                        continue

                    elif key == 'AB':
                        if len(path) > 1:
                            self._add_path_geometry_to_buffers(
                                path, last_path_aperture, poly_buffer, follow_buffer, steps)
                            path = [path[-1]]
                        if body.startswith('D') and body[1:].isdigit():
                            # a block is opened: it is made in a state of its own, it ends at %AB*%
                            block = {
                                'id': int(body[1:]), 'records': [],
                                'saved': (path, poly_buffer, follow_buffer, earlier_layers, current_polarity,
                                          self.is_lpc, current_x, current_y, current_aperture, last_path_aperture,
                                          list(load_xform), sr_params, self._sr_rec, making_region,
                                          current_operation_code, current_interpolation_mode)}
                            if self._blocks is None:
                                self._blocks = []
                            self._blocks.append(block)
                            path, poly_buffer, follow_buffer, earlier_layers = [], [], [], []
                            current_polarity, self.is_lpc = 'D', False
                            current_x, current_y = 0, 0
                            current_aperture, last_path_aperture = None, None
                            load_xform = ['N', 0.0, 1.0]
                            set_load_transform()
                            sr_params, self._sr_rec, making_region = None, None, False
                        elif self._blocks:
                            finish_step_repeat()
                            if len(path) > 1:
                                self._add_path_geometry_to_buffers(
                                    path, last_path_aperture, poly_buffer, follow_buffer, steps)
                            block = self._blocks.pop()
                            (path, poly_buffer, follow_buffer, earlier_layers, current_polarity, self.is_lpc,
                             current_x, current_y, current_aperture, last_path_aperture, load_xform, sr_params,
                             self._sr_rec, making_region, current_operation_code,
                             current_interpolation_mode) = block['saved']
                            set_load_transform()
                            shape = _compose_records(block['records'])
                            minx, miny, maxx, maxy = shape.bounds if not shape.is_empty else (0, 0, 0, 0)
                            self.tools[block['id']] = {
                                'type': 'AB', 'shape': shape,
                                'size': max(maxx - minx, maxy - miny, 10 ** -self.decimals)}
                            if not self._blocks:
                                self._blocks = None
                        else:
                            self.app.log.warning("Aperture block end without a start ignored. (%d)" % line_num)
                        continue

                    elif key in ('LM', 'LR', 'LS'):
                        try:
                            if key == 'LM':
                                if body not in ('N', 'X', 'Y', 'XY'):
                                    raise ValueError(body)
                                load_xform[0] = body
                            elif key == 'LR':
                                load_xform[1] = float(body)
                            else:
                                load_xform[2] = float(body)
                        except ValueError:
                            self.app.log.warning("Invalid load command ignored. (%d): %s" % (line_num, gline))
                        set_load_transform()
                        continue

                    elif key == 'IP':
                        image_negative = body == 'NEG'
                        if image_negative:
                            self.app.log.warning("Deprecated negative image polarity (IPNEG). (%d)" % line_num)
                        continue

                    elif key in ('OF', 'SF'):
                        match = _AXIS_VALUES_RE.match(body)
                        if match:
                            first = float(match.group(1)) if match.group(1) else None
                            second = float(match.group(2)) if match.group(2) else None
                            if key == 'OF':
                                image_offset = (first if first is not None else 0.0,
                                                second if second is not None else 0.0)
                            else:
                                image_scale = (first if first is not None else 1.0,
                                               second if second is not None else 1.0)
                            self.app.log.warning("Deprecated image command %s used. (%d)" % (key, line_num))
                        else:
                            self.app.log.warning("Invalid command ignored. (%d): %s" % (line_num, gline))
                        continue

                    elif key == 'MI':
                        match = _MI_RE.match(body)
                        if match:
                            image_mirror = (match.group(1) == '1', match.group(2) == '1')
                            self.app.log.warning("Deprecated image command MI used. (%d)" % line_num)
                        continue

                    elif key == 'IR':
                        if body in ('0', '90', '180', '270'):
                            image_rotation = int(body)
                            self.app.log.warning("Deprecated image command IR used. (%d)" % line_num)
                        else:
                            self.app.log.warning("Invalid image rotation ignored. (%d): %s" % (line_num, gline))
                        continue

                    elif key == 'AS':
                        self.app.log.warning("Deprecated axis select (AS) is not supported and it is ignored. (%d)" %
                                             line_num)
                        continue

                # ################################################################
                # #####################  Number format ###########################
                # #####################  Example: %FSLAX24Y24*%  #################
                # ################################################################

                match = self.fmt_re.search(gline) if has_pct else None
                if match:
                    abs_coords = match.group(2) == 'A'
                    if match.group(1) is not None:
                        self.gerber_zeros = cast(Literal["L", "T"], match.group(1))
                    self.int_digits = int(match.group(3))
                    self.frac_digits = int(match.group(4))
                    self.app.log.debug("Gerber format found. (%s) " % str(gline))

                    self.app.log.debug(
                        "Gerber format found. Gerber zeros = %s (L-omit leading zeros, T-omit trailing zeros, "
                        "D-no zero suppression)" % self.gerber_zeros)
                    self.app.log.debug("Gerber format found. Absolute coordinates = %s" % abs_coords)
                    continue

                # ################################################################
                # ######################## Mode (IN/MM)    #######################
                # #####################    Example: %MOIN*%  #####################
                # ################################################################
                match = self.mode_re.search(gline) if has_pct else None
                if match:
                    self.units = cast(Literal["IN", "MM"], match.group(1))
                    self.app.log.debug("Gerber units found = %s" % self.units)
                    # Changed for issue #80
                    # self.convert_units(match.group(1))
                    s_tol = float(self._app_option("gerber_simp_tolerance")) / 25.4 \
                        if self.units == 'IN' else s_tol

                    self.conversion_done = True
                    continue

                # ################################################################
                # Combined Number format and Mode --- Allegro does this ##########
                # ################################################################
                match = self.fmt_re_alt.search(gline) if has_pct else None
                if match:
                    abs_coords = match.group(2) == 'A'
                    if match.group(1) is not None:
                        self.gerber_zeros = cast(Literal["L", "T"], match.group(1))
                    self.int_digits = int(match.group(3))
                    self.frac_digits = int(match.group(4))
                    self.app.log.debug("Gerber format found. (%s) " % str(gline))
                    self.app.log.debug(
                        "Gerber format found. Gerber zeros = %s (L-omit leading zeros, T-omit trailing zeros, "
                        "D-no zero suppression)" % self.gerber_zeros)
                    self.app.log.debug("Gerber format found. Absolute coordinates = %s" % abs_coords)

                    self.units = cast(Literal["IN", "MM"], match.group(5))
                    s_tol = float(self._app_option("gerber_simp_tolerance")) / 25.4 \
                        if self.units == 'IN' else s_tol

                    self.app.log.debug("Gerber units found = %s" % self.units)
                    # Changed for issue #80
                    # self.convert_units(match.group(5))
                    self.conversion_done = True
                    continue

                # ################################################################
                # ####     Search for OrCAD way for having Number format  ########
                # ################################################################
                match = self.fmt_re_orcad.search(gline) if has_pct else None
                if match:
                    if match.group(1) == 'G74':
                        quadrant_mode = 'SINGLE'
                    elif match.group(1) == 'G75':
                        quadrant_mode = 'MULTI'
                    abs_coords = match.group(3) == 'A'
                    if match.group(2) is not None:
                        self.gerber_zeros = cast(Literal["L", "T"], match.group(2))

                    self.int_digits = int(match.group(4))
                    self.frac_digits = int(match.group(5))
                    self.app.log.debug("Gerber format found. (%s) " % str(gline))
                    self.app.log.debug(
                        "Gerber format found. Gerber zeros = %s (L-omit leading zeros, T-omit trailing zeros, "
                        "D-no zero suppression)" % self.gerber_zeros)
                    self.app.log.debug("Gerber format found. Absolute coordinates = %s" % abs_coords)
                    continue

                # ################################################################
                # ############     Units (G70/1) OBSOLETE   ######################
                # ################################################################
                match = self.units_re.search(gline) if first_char == 'G' else None
                if match:
                    if match.group(1) == "0":
                        obs_gerber_units = "IN"
                    elif match.group(1) == "1":
                        obs_gerber_units = "MM"
                    self.units = obs_gerber_units
                    s_tol = float(self._app_option("gerber_simp_tolerance")) / 25.4 \
                        if self.units == 'IN' else s_tol

                    self.app.log.warning("Gerber obsolete units found = %s" % obs_gerber_units)
                    # Changed for issue #80
                    # self.convert_units({'0': 'IN', '1': 'MM'}[match.group(1)])
                    self.conversion_done = True
                    continue

                # ################################################################
                # #####   Absolute/relative coordinates G90/1 OBSOLETE ###########
                # ################################################################
                match = self.absrel_re.search(gline) if first_char == 'G' else None
                if match:
                    abs_coords = match.group(1) == '0'
                    self.app.log.warning(
                        "Gerber obsolete coordinates type found, absolute coordinates = %s" % abs_coords)
                    continue

                # ################################################################
                # ######################## Aperture Macros #######################
                # Having this at the beginning will slow things down
                # but macros can have complicated statements than could
                # be caught by other patterns.
                # ################################################################
                # ################################################################
                if current_macro is None:  # No macro started yet
                    match = self.am1_re.search(gline) if has_pct else None
                    # Start macro if there is a match, else not an AM, carry on.
                    if match:
                        self.app.log.debug("Starting macro. Line %d: %s" % (line_num, gline))
                        current_macro = match.group(1)
                        self.aperture_macros[current_macro] = ApertureMacro(name=current_macro)
                        if match.group(2):  # Append
                            self.aperture_macros[current_macro].append(match.group(2))
                        if match.group(3):  # Finish macro
                            # self.aperture_macros[current_macro].parse_content()
                            current_macro = None
                            self.app.log.debug("Macro complete in 1 line.")
                        continue
                else:  # Continue macro
                    self.app.log.debug("Continuing macro. Line %d." % line_num)
                    match = self.am2_re.search(gline)
                    if match:  # Finish macro
                        self.app.log.debug("End of macro. Line %d." % line_num)
                        self.aperture_macros[current_macro].append(match.group(1))
                        # self.aperture_macros[current_macro].parse_content()
                        current_macro = None
                    else:  # Append
                        self.aperture_macros[current_macro].append(gline)
                    continue

                # ################################################################
                # ##############   Aperture definitions %ADD...  #################
                # ################################################################
                match = self.ad_re.search(gline) if has_pct else None
                if match:
                    # log.info("Found aperture definition. Line %d: %s" % (line_num, gline))
                    apid = self.aperture_parse(match.group(1), match.group(2), match.group(3))
                    if apid is not None and self.attributes['TA']:
                        self.tools[apid]['attributes'] = {name: list(values)
                                                          for name, values in self.attributes['TA'].items()}
                    continue

                # ################################################################
                # ################  Operation code alone #########################
                # ###########   Operation code alone, usually just D03 (Flash) ###
                # self.opcode_re = re.compile(r'^D0?([123])\*$')
                # ################################################################
                match = self.opcode_re.search(gline) if first_char in ('D', 'G') else None
                if match and current_aperture != "failure":
                    current_operation_code = int(match.group(1))
                    current_d = current_operation_code

                    if current_operation_code == 3:

                        # --- Buffered ---
                        try:
                            # self.app.log.debug("Bare op-code %d." % current_operation_code)
                            self._add_flash_to_buffers(
                                current_x, current_y, current_aperture,
                                poly_buffer, follow_buffer, steps
                            )

                        except IndexError:
                            self.app.log.warning("Line %d: %s -> Nothing there to flash!" % (line_num, gline))

                    elif current_operation_code == 2 and len(path) > 1:
                        # D02 alone: the contour or the stroke ends at the current point
                        if making_region:
                            self._add_region_contour(path, poly_buffer, follow_buffer, line_num)
                        else:
                            self._add_path_geometry_to_buffers(
                                path, last_path_aperture, poly_buffer, follow_buffer, steps)
                        path = [path[-1]]

                    continue

                # ################################################################
                # ################  Tool/aperture change  ########################
                # ################  Example: D12*         ########################
                # self.tool_re = re.compile(r'^(?:G54)?D(\d\d+)\*$')
                # ################################################################
                match = self.tool_re.search(gline) if first_char in ('D', 'G') else None
                if match:
                    current_aperture = int(match.group(1))

                    # self.app.log.debug("Line %d: Aperture change to (%s)" % (line_num, current_aperture))

                    # If the aperture value is zero then make it something quite small but with a non-zero value
                    # such that it can be processed by FlatCAM.
                    # But first test to see if the aperture type is "aperture macro". In that case
                    # we should not test for "size" key as it does not exist in this case.
                    if current_aperture in self.tools and self.tools[current_aperture]["type"] != "AM":
                        if self.tools[current_aperture]["size"] == 0:
                            self.tools[current_aperture]["size"] = 10 ** -self.decimals

                    # if the detected aperture is not detected already then it means that we have a
                    if current_aperture not in self.tools:
                        current_aperture = "failure"
                        self.defective_aperture_detected = True

                    # self.app.log.debug(self.tools[current_aperture])

                    # Take care of the current path with the previous tool
                    if len(path) > 1:
                        self._add_path_geometry_to_buffers(path, last_path_aperture, poly_buffer, follow_buffer, steps)
                        path = [path[-1]]
                    continue

                # ################################################################
                # ################  G36* - Begin region   ########################
                # ################################################################
                if first_char == 'G' and self.regionon_re.search(gline) and current_aperture != "failure":
                    path, last_path_aperture = self._handle_region_start(
                        path, last_path_aperture, poly_buffer, follow_buffer,
                        steps, line_num, current_operation_code
                    )
                    making_region = True
                    continue

                # ################################################################
                # ################  G37* - End region     ########################
                # ################################################################
                if first_char == 'G' and self.regionoff_re.search(gline) and current_aperture != "failure":
                    path, making_region = self._handle_region_end(
                        path, current_aperture, current_x, current_y,
                        poly_buffer, follow_buffer, steps, line_num,
                        current_operation_code
                    )
                    continue

                # ################################################################
                # ################  G01/2/3* - Interpolation mode change #########
                # ####  Can occur along with coordinates and operation code but ##
                # ####  sometimes by itself (handled here).  #####################
                # ####  Example: G01*                        #####################
                # ################################################################
                match = self.interp_re.search(gline) if first_char == 'G' else None
                if match:
                    current_interpolation_mode = int(match.group(1))
                    continue

                if not decimal_warned and '.' in gline and first_char in ('G', 'X', 'Y', 'I', 'J'):
                    decimal_warned = True
                    self.app.log.warning("Decimal point in the coordinates (not allowed by the format), the numbers "
                                         "are taken as they are. (%d)" % line_num)

                # I and J exist only in a circular move: in a linear move (G01, also when it is the mode that is
                # still active) they are ignored
                if first_char in ('G', 'X', 'Y') and ('I' in gline or 'J' in gline):
                    mode_match = _INTERPOLATION_WORD_RE.match(gline)
                    line_mode = int(mode_match.group(1)) if mode_match else current_interpolation_mode
                    if line_mode not in (2, 3):
                        gline = _IJ_WORD_RE.sub('', gline)

                # ################################################################
                # ######### G01 - Linear interpolation plus flashes  #############
                # ######### Operation code (D0x) missing is deprecated   #########
                # REGEX: r'^(?:G0?(1))?(?:X(-?\d+))?(?:Y(-?\d+))?(?:D0([123]))?\*$'
                # ################################################################
                match = self.lin_re.search(gline) if first_char in ('G', 'X', 'Y') else None
                if match and current_aperture != "failure":
                    # a G01 in the line makes the linear mode the active one
                    if match.group(1) is not None:
                        current_interpolation_mode = 1

                    # the point where the move starts
                    start_x, start_y = current_x, current_y

                    # Parse coordinates, incremental ones are added to the current point
                    if match.group(2) is not None:
                        linear_x = parse_gerber_number(match.group(2),
                                                       self.int_digits, self.frac_digits, self.gerber_zeros) * image_scale[0]
                        if not abs_coords:
                            linear_x += start_x
                        current_x = linear_x
                    else:
                        linear_x = current_x
                    if match.group(3) is not None:
                        linear_y = parse_gerber_number(match.group(3),
                                                       self.int_digits, self.frac_digits, self.gerber_zeros) * image_scale[1]
                        if not abs_coords:
                            linear_y += start_y
                        current_y = linear_y
                    else:
                        linear_y = current_y

                    # Parse operation code
                    if match.group(4) is not None:
                        current_operation_code = int(match.group(4))

                    # Pen down: add segment
                    if current_operation_code == 1:
                        # a draw before any move starts at the current point
                        if not path:
                            path = [[start_x, start_y]]

                        # only add the point if it's a new one otherwise skip it (harder to process)
                        if path[-1] != [current_x, current_y]:
                            path.append([current_x, current_y])
                        elif len(path) == 1:
                            # it's a flash that is done by moving with pen up D2 and then just a pen down D1
                            # Reset path starting point
                            path = [[current_x, current_y]]

                            # no flash inside a Gerber Region and none when there is no aperture
                            if current_aperture is not None and not making_region:
                                # --- BUFFERED ---
                                # Draw the flash using helper
                                self._add_flash_to_buffers(
                                    current_x, current_y, current_aperture,
                                    poly_buffer, follow_buffer, steps
                                )

                        if current_aperture != "failure":
                            last_path_aperture = current_aperture
                        # we do this for the case that a region is done without having defined any aperture
                        if last_path_aperture is None:
                            self._ensure_region_tool()
                            last_path_aperture = 0

                    elif current_operation_code == 2:
                        if len(path) > 1:
                            # --- BUFFERED ---
                            if making_region:
                                # the contour of a region ends here
                                self._add_region_contour(path, poly_buffer, follow_buffer, line_num)
                            else:
                                if last_path_aperture is None:
                                    self.app.log.warning("No aperture defined for current path. (%d)" % line_num)
                                self._add_path_geometry_to_buffers(
                                    path, last_path_aperture, poly_buffer, follow_buffer, steps)

                        path = [[linear_x, linear_y]]  # Start new path

                    # Flash
                    # Not allowed in region mode.
                    elif current_operation_code == 3:

                        # Create path draw so far.
                        if len(path) > 1:
                            self._add_path_geometry_to_buffers(
                                path, last_path_aperture, poly_buffer, follow_buffer, steps)

                        # Reset path starting point
                        path = [[linear_x, linear_y]]

                        # --- BUFFERED ---
                        # Draw the flash
                        self._add_flash_to_buffers(
                            linear_x, linear_y, current_aperture,
                            poly_buffer, follow_buffer, steps
                        )

                    # maybe those lines are not exactly needed,
                    # but it is easier to read the program as those coordinates
                    # are used in case that circular interpolation is encountered within the Gerber file
                    current_x = linear_x
                    current_y = linear_y

                    # self.app.log.debug("Line_number=%3s X=%s Y=%s (%s)" % (line_num, linear_x, linear_y, gline))
                    continue

                # ################################################################
                # ######### G74/75* - Single or multiple quadrant arcs  ##########
                # ################################################################
                match = self.quad_re.search(gline) if first_char == 'G' else None
                if match:
                    if match.group(1) == '4':
                        quadrant_mode = 'SINGLE'
                    else:
                        quadrant_mode = 'MULTI'
                    continue

                # ################################################################
                # ######### G02/3 - Circular interpolation   #####################
                # ######### 2-clockwise, 3-counterclockwise  #####################
                # ######### Ex. format: G03 X0 Y50 I-50 J0 where the     #########
                # ######### X, Y coords are the coords of the End Point  #########
                # ################################################################
                match = self.circ_re.search(gline) if first_char in ('G', 'X', 'Y', 'I', 'J') else None
                if match and current_aperture != "failure":
                    arcdir = [None, None, "cw", "ccw"]

                    mode, circular_x, circular_y, i, j, d = match.groups()

                    try:
                        circular_x = parse_gerber_number(circular_x,
                                                         self.int_digits, self.frac_digits, self.gerber_zeros) * image_scale[0]
                        if not abs_coords:
                            circular_x += current_x
                    except Exception:
                        circular_x = current_x

                    try:
                        circular_y = parse_gerber_number(circular_y,
                                                         self.int_digits, self.frac_digits, self.gerber_zeros) * image_scale[1]
                        if not abs_coords:
                            circular_y += current_y
                    except Exception:
                        circular_y = current_y

                    # According to Gerber specification i and j are not modal, which means that when i or j are missing,
                    # they are to be interpreted as being zero
                    try:
                        i = parse_gerber_number(i, self.int_digits, self.frac_digits, self.gerber_zeros) * image_scale[0]
                    except Exception:
                        i = 0

                    try:
                        j = parse_gerber_number(j, self.int_digits, self.frac_digits, self.gerber_zeros) * image_scale[1]
                    except Exception:
                        j = 0

                    if quadrant_mode is None:
                        # the files that do not set the quadrant mode are the old ones: single quadrant
                        self.app.log.warning(
                            "Found arc without preceding quadrant specification G74 or G75, "
                            "using single quadrant. (%d)" % line_num)
                        quadrant_mode = 'SINGLE'

                    if mode is None and current_interpolation_mode not in [2, 3]:
                        self.app.log.error("Found arc without circular interpolation mode defined. (%d)" % line_num)
                        self.app.log.error(gline)
                        continue
                    elif mode is not None:
                        current_interpolation_mode = int(mode)

                    # Set operation code if provided
                    if d is not None:
                        current_operation_code = int(d)

                    # Nothing created! Pen Up.
                    if current_operation_code == 2:
                        self.app.log.warning("Arc with D2. (%d)" % line_num)
                        if len(path) > 1:
                            if making_region:
                                self._add_region_contour(path, poly_buffer, follow_buffer, line_num)
                            else:
                                if last_path_aperture is None:
                                    self.app.log.warning("No aperture defined for current path. (%d)" % line_num)
                                self._add_path_geometry_to_buffers(
                                    path, last_path_aperture, poly_buffer, follow_buffer, steps)

                        current_x = circular_x
                        current_y = circular_y
                        path = [[current_x, current_y]]  # Start new path
                        continue

                    # Flash should not happen here
                    if current_operation_code == 3:
                        self.app.log.error("Trying to flash within arc. (%d)" % line_num)
                        continue

                    # an arc before any move starts at the current point
                    if not path:
                        path = [[current_x, current_y]]

                    if quadrant_mode == 'MULTI':
                        center = [i + current_x, j + current_y]
                        radius = np.sqrt(i ** 2 + j ** 2)
                        start = np.arctan2(-j, -i)  # Start angle
                        # Numerical errors might prevent start == stop therefore
                        # we check ahead of time. This should result in a
                        # 360 degree arc.
                        if current_x == circular_x and current_y == circular_y:
                            stop = start
                        else:
                            stop = np.arctan2(-center[1] + circular_y, -center[0] + circular_x)  # Stop angle

                        this_arc = arc(
                            center,
                            radius,
                            start,
                            stop,
                            arcdir[current_interpolation_mode],
                            _circle_steps_of(self, radius)
                        )

                        # The last point in the computed arc can have
                        # numerical errors. The exact final point is the
                        # specified (x, y). Replace.
                        this_arc[-1] = (circular_x, circular_y)

                        # Last point in path is current point
                        # current_x = this_arc[-1][0]
                        # current_y = this_arc[-1][1]
                        current_x, current_y = circular_x, circular_y

                        # Append
                        path += this_arc
                        last_path_aperture = current_aperture

                        continue

                    if quadrant_mode == 'SINGLE':

                        center_candidates = [
                            [i + current_x, j + current_y],
                            [-i + current_x, j + current_y],
                            [i + current_x, -j + current_y],
                            [-i + current_x, -j + current_y]
                        ]

                        # Calculate radius once, outside the loop
                        radius = np.sqrt(i ** 2 + j ** 2)

                        # The center is the candidate at the radius from both ends. The arc of a single quadrant
                        # is 90 degrees at most; a longer one is invalid, it is drawn with the shortest sweep and
                        # reported.
                        best = None
                        for center in center_candidates:
                            # Make sure radius to start is the same as radius to end.
                            radius2 = np.sqrt((center[0] - circular_x) ** 2 + (center[1] - circular_y) ** 2)
                            if radius2 < radius * 0.95 or radius2 > radius * 1.05:
                                continue  # Not a valid center.

                            start = np.arctan2(current_y - center[1], current_x - center[0])  # Start angle
                            stop = np.arctan2(-center[1] + circular_y, -center[0] + circular_x)  # Stop angle
                            angle = abs(arc_angle(start, stop, arcdir[current_interpolation_mode]))
                            if best is None or angle < best[0]:
                                best = (angle, center, start, stop)
                            if angle <= (np.pi + 1e-6) / 2:
                                break

                        valid = best is not None
                        if valid:
                            angle, center, start, stop = best
                            if angle > (np.pi + 1e-6) / 2:
                                self.app.log.warning(
                                    "Single quadrant arc of more than 90 degrees (%d degrees), drawn as it is. (%d)" %
                                    (round(np.degrees(angle)), line_num))
                            this_arc = arc(center, radius, start, stop, arcdir[current_interpolation_mode],
                                           _circle_steps_of(self, radius))

                            # Replace with exact values
                            this_arc[-1] = (circular_x, circular_y)
                            current_x, current_y = circular_x, circular_y

                            path += this_arc
                            last_path_aperture = current_aperture

                        if valid:
                            continue
                        else:
                            self.app.log.warning("Invalid arc in line %d." % line_num)

                # ################################################################
                # ######### EOF - END OF FILE ####################################
                # ################################################################
                match = self.eof_re.search(gline)
                if match:
                    continue

                # ################################################################
                # ######### Line did not match any pattern. Warn user.  ##########
                # ################################################################
                self.app.log.warning("Line ignored (%d): %s" % (line_num, gline))
                # provide the app with a way to process the GUI events when in a blocking loop
                QtWidgets.QApplication.processEvents()

            if len(path) > 1:
                # EOF, create the geometry of what is still in path
                # ## --- Buffered ---
                if making_region:
                    self._add_region_contour(path, poly_buffer, follow_buffer, line_num)
                else:
                    self._add_path_geometry_to_buffers(path, last_path_aperture, poly_buffer, follow_buffer, steps)
            if self._blocks:
                self.app.log.warning("Aperture block without an end at the end of the file.")
                self._blocks = None
            finish_step_repeat()

            # Build source_file from accumulated lines
            self.source_file = '\n'.join(source_lines) + '\n'

            # Compose the layers closed by the polarity changes. Without earlier layers nothing changes: the last layer
            # is applied directly to the (empty) solid_geometry below.
            if earlier_layers:
                self.solid_geometry = self._compose_layers(earlier_layers)

            # ##########################################################################################################
            #   Creating the FINAL GEOMETRY
            # ##########################################################################################################
            # --- Apply buffer ---
            # this treats the case when we are storing geometry as paths
            self.follow_geometry = flatten_shapely_geometry(follow_buffer)

            # this treats the case when we are storing geometry as solids
            # Some entries are MultiPolygons (macro flashes, regions repaired by buffer). MultiPolygon(poly_buffer)
            # below accepts only Polygons, so split every entry into its parts and drop empties and non-polygons.
            poly_buffer = [geo for geo in flatten_shapely_geometry(poly_buffer) if isinstance(geo, Polygon)]

            try:
                buff_length = len(poly_buffer)
            except TypeError:
                buff_length = 1

            if isinstance(self.solid_geometry, MultiPolygon):
                sol_geo_length = len(self.solid_geometry.geoms)
            elif isinstance(self.solid_geometry, list):
                sol_geo_length = len(self.solid_geometry)
            else:
                sol_geo_length = 1

            try:
                assert isinstance(self.solid_geometry, BaseGeometry)
                if buff_length == 0 and sol_geo_length in [0, 1] and self.solid_geometry.area == 0:
                    self.app.log.error("Object is not Gerber file or empty. Aborting Object creation.")
                    return 'fail'
            except TypeError as e:
                self.app.log.error("Object is not Gerber file or empty. Aborting Object creation. %s" % str(e))
                return 'fail'

            self.app.log.warning("Joining %d polygons." % buff_length)
            self.app.inform.emit('%s: %d.' % (_("Gerber processing. Joining polygons"), buff_length))

            if use_simplification:
                poly_buffer = list(simplify(np.array(poly_buffer, dtype=object), s_tol))

            # The Clipper2 fusion and the GEOS buffer(0) give valid geometry by construction; the plain MultiPolygon
            # of the parts is checked below
            new_poly_valid = True
            if self.use_buffer_for_union:
                self.app.log.debug("Union by buffer...")

                if use_buffering == 'full':
                    fused = fuse_parts(poly_buffer) if len(poly_buffer) > 0 else None
                    if fused:
                        new_poly = MultiPolygon(fused)
                        self.app.log.debug("Union by Clipper2 done.")
                    else:
                        new_poly = MultiPolygon(poly_buffer)
                        new_poly = new_poly.buffer(0.00000001)
                        new_poly = new_poly.buffer(-0.00000001)
                        self.app.log.debug("Union by GEOS buffer done (Clipper2 not used).")
                    self.app.log.warning("Union(buffer) done.")
                else:
                    new_poly = MultiPolygon(poly_buffer)
                    new_poly_valid = False

            else:
                self.app.log.debug("Union by union()...")
                new_poly = unary_union(poly_buffer)
                new_poly = new_poly.buffer(0)
                self.app.log.warning("Union done.")

            if current_polarity == 'D':
                self.app.inform.emit('%s' % _("Gerber processing. Applying Gerber polarity."))
                if new_poly_valid or new_poly.is_valid:
                    if isinstance(self.solid_geometry, BaseGeometry) and self.solid_geometry.is_empty:
                        # nothing to merge with: the union would only rebuild the geometry
                        self.solid_geometry = new_poly
                    else:
                        self.solid_geometry = self.solid_geometry.union(new_poly)
                else:
                    # I do this so whenever the parsed geometry of the file is not valid (intersections) it is still
                    # loaded. Instead of applying a union I add to a list of polygons.
                    final_poly = []
                    try:
                        for poly in new_poly:
                            final_poly.append(poly)
                    except TypeError:
                        final_poly.append(new_poly)

                    try:
                        for poly in self.solid_geometry:
                            final_poly.append(poly)
                    except TypeError:
                        final_poly.append(self.solid_geometry)

                    self.solid_geometry = final_poly

                # FIX for issue #347 - Sprint Layout generate Gerber files when the copper pour is enabled
                # it use a filled bounding box polygon to which add clear polygons (negative) to isolate the copper
                # features
                if extra_buffering:
                    candidate_geo = []
                    geo_to_buff: Sequence[BaseGeometry] | BaseGeometry
                    if isinstance(self.solid_geometry, MultiPolygon):
                        geo_to_buff = self.solid_geometry.geoms
                    else:
                        geo_to_buff = self.solid_geometry
                    if isinstance(geo_to_buff, BaseGeometry):
                        candidate_geo.append(geo_to_buff.buffer(-0.0000001))
                    else:
                        for p in geo_to_buff:
                            candidate_geo.append(p.buffer(-0.0000001))
                    self.solid_geometry = candidate_geo

            else:
                self.solid_geometry = self.solid_geometry.difference(new_poly)

            # flatten the solid geometry
            self.solid_geometry = flatten_shapely_geometry(self.solid_geometry)

            # The deprecated image commands: mirror, rotation and offset of the whole image (SF scaled only the
            # coordinates, when they were read), then the negative image
            image_matrix = _image_matrix(image_mirror, image_rotation, image_offset)
            if image_matrix is not None:
                self._affine_all(image_matrix)
                if image_mirror != (False, False) or image_rotation:
                    self.export_as_regions = True
            if image_negative:
                # IPNEG: the dark and the clear areas of the image are swapped. The specification does not limit the
                # image, so FlatCAM uses a domain: the bounding box of the positive image (the convention of other
                # readers, e.g. gerbv). The domain is kept in self.image_domain and the user is told.
                positive = unary_union(self.solid_geometry)
                if not positive.is_empty:
                    self.image_domain = positive.bounds
                    self.solid_geometry = flatten_shapely_geometry(shply_box(*positive.bounds).difference(positive))
                    self.app.inform.emit('[WARNING] %s' % _(
                        "Negative image (IPNEG): the image is inverted inside its bounding box."))
                self.export_as_regions = True

            # import time
            # start = time.time()
            # #########################################################################################################
            prepare(self.solid_geometry)
            # #########################################################################################################
            # print(f"Time elapsed: {time.time() - start}; Is prepared? {is_prepared(self.solid_geometry)}")

            if clean_apertures:
                # clean the Gerber file of apertures with no geometry
                for apid, apvalue in list(self.tools.items()):
                    if 'geometry' not in apvalue:
                        self.tools.pop(apid)

            # init this for the following operations
            self.conversion_done = False

            if self.defective_aperture_detected:
                return "defective"
        except Exception as err:
            ex_type, ex, tb = sys.exc_info()
            traceback.print_tb(tb)
            # print traceback.format_exc()

            self.app.log.error("Gerber PARSING FAILED. Line %d: %s" % (line_num, gline))
            self.app.log.error(str(traceback.format_exc()))

            loc = '%s #%d %s: %s\n' % (_("Gerber Line"), line_num, _("Gerber Line Content"), gline) + repr(err)
            self.app.inform.emit('[ERROR] %s\n%s:' % (_("Gerber Parser ERROR"), loc))
            return 'fail'

        if is_excellon_gx2 is True:
            return 'drill'

    def create_flash_geometry(
        self,
        location: Any,
        aperture: Dict[str, Any],
        steps_per_circle: Optional[int] = None
    ) -> Any:

        # self.app.log.debug('Flashing @%s, Aperture: %s' % (location, aperture))

        if isinstance(location, list):
            location = Point(location)

        shape = _flash_shape(self, aperture, steps_per_circle)
        if shape is None:
            return None
        loc = location.coords[0]
        return _translate_shape(shape, loc[0], loc[1])

    def _add_path_geometry_to_buffers(
        self,
        path: List[List[float]],
        aperture_id: Optional[int],
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        steps: int
    ) -> Tuple[Optional[Any], Optional[Any], Dict[str, Any]]:
        """
        Add the stroke of a path (a draw with an aperture) to poly_buffer and follow_buffer with proper geo_dict
        tracking. The solid geometry is the path buffered by the circle aperture, or the path swept by the R, O, P
        or macro aperture (see _stroke_geometry()).

        :param path: List of [x, y] coordinate pairs
        :param aperture_id: ID of the aperture that draws the path
        :param poly_buffer: List to append solid geometry to
        :param follow_buffer: List to append line geometry to
        :param steps: Number of steps per circle for arc approximation
        :return: Tuple of (geo_s, geo_f, geo_dict) or (None, None, {}) if path is empty
        """
        if len(path) < 2:
            return None, None, {}

        geo_dict = {}

        # Create follow geometry
        geo_f = LineString(path)

        # Add to follow buffer
        if not geo_f.is_empty:
            follow_buffer.append(geo_f)
            geo_dict['follow'] = geo_f

        # Create solid geometry
        geo_s = None
        aperture = self.tools.get(aperture_id)
        if aperture is not None:
            try:
                geo_s = _stroke_geometry(self, path, aperture, steps, self._xform)
            except Exception as e:
                self.app.log.warning("Stroke of aperture %s failed: %s" % (str(aperture_id), str(e)))
                geo_s = None

        # Add to poly buffer
        if geo_s is not None and not geo_s.is_empty:
            if not geo_s.is_valid:
                self.app.log.warning("Found invalid Gerber geometry. Attempting fix...")
                geo_s = geo_s.buffer(0.0000001, 1)

            if not geo_s.is_empty:
                if self._xform is not None:
                    self.export_as_regions = True
                poly_buffer.append(geo_s)
                if self.is_lpc is True:
                    geo_dict['clear'] = geo_s
                else:
                    geo_dict['solid'] = geo_s

        # Store in aperture geometry tracking
        if aperture_id is not None and geo_dict:
            self._store(aperture_id, geo_dict)

        return geo_s, geo_f, geo_dict

    def _add_flash_to_buffers(
        self,
        x: float,
        y: float,
        aperture_id: Optional[int],
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        steps: int
    ) -> Dict[str, Any]:
        """
        Create a flash at the given position and add to buffers.

        :param x: X coordinate for flash
        :param y: Y coordinate for flash
        :param aperture_id: Aperture ID to use for flash geometry
        :param poly_buffer: List to append solid geometry to
        :param follow_buffer: List to append point geometry to
        :param steps: Number of steps per circle for arc approximation
        :return: geo_dict with flash geometry, or {} on error
        """
        geo_dict = {}
        location = Point([x, y])

        # Add point to follow buffer
        follow_buffer.append(location)
        geo_dict['follow'] = location

        # Create flash geometry
        aperture = self.tools.get(aperture_id)
        if aperture is None:
            self.app.log.warning(f"Flash at ({x}, {y}) failed: undefined aperture {aperture_id}")
            return geo_dict

        try:
            if self._xform is None:
                flash = self.create_flash_geometry(location, aperture, steps)
            else:
                # the aperture is mirrored, rotated and scaled about its origin, then moved to the point
                shape = _flash_shape(self, aperture, steps)
                flash = None if shape is None else affinity.affine_transform(
                    shape, (self._xform[0], self._xform[1], self._xform[2], self._xform[3], x, y))
                self.export_as_regions = True

            if flash is not None and not flash.is_empty:
                poly_buffer.append(flash)

                if self.is_lpc is True:
                    geo_dict['clear'] = flash
                else:
                    geo_dict['solid'] = flash

                self._store(aperture_id, geo_dict)
        except Exception as e:
            # a flash that can not be made (a broken aperture macro) is skipped, the other flashes are kept
            self.app.log.warning(f"Flash at ({x}, {y}) of aperture {aperture_id} failed: {e}")
            self.defective_aperture_detected = True

        return geo_dict

    def _store(self, aperture_id: int, geo_dict: Dict[str, Any]) -> None:
        """
        Keep the record of an object in the geometry list of its aperture. The objects of an open step and repeat
        block are also kept in its list, for the copies. The objects of an aperture block belong to the block (they
        are its image) and not to the apertures that made them.
        """
        record = (aperture_id, geo_dict, self.is_lpc)
        if self._sr_rec is not None:
            self._sr_rec.append(record)
        if self._blocks:
            self._blocks[-1]['records'].append(record)
            return
        self.tools.setdefault(aperture_id, {}).setdefault('geometry', []).append(geo_dict)

    def _read_attribute(self, key: str, body: str) -> None:
        """
        Read an attribute command (TF, TA, TO, TD) in self.attributes. ``body`` is what follows the 2 letters up to
        the closing '*', for example ``.FileFunction,Copper,L1,Top``.
        """
        name, *values = [part.strip() for part in body.split(',')]
        if key == 'TD':
            # delete one aperture or object attribute, or all of them (the file attributes can not be deleted)
            for kind in ('TA', 'TO'):
                if name:
                    self.attributes[kind].pop(name, None)
                else:
                    self.attributes[kind].clear()
        elif name:
            self.attributes[key][name] = values

    def _ensure_region_tool(self) -> None:
        """Create the 'aperture' 0 (type REG) that holds the regions, if it does not exist."""
        if 0 not in self.tools:
            self.tools[0] = {
                'type': 'REG',
                'size': 0.0,
                'geometry': []
            }

    def _add_region_contour(
        self,
        path: List[List[float]],
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        line_num: int
    ) -> None:
        """
        Add the polygon of one closed contour of a region to the buffers and to the region 'aperture' 0.
        An invalid contour (a bowtie, a contour that touches itself) is repaired with make_valid: every polygonal
        part is kept, each as a record of its own.

        :param path: contour coordinates, the polygon closes itself
        :param poly_buffer: Polygon buffer (modified in place)
        :param follow_buffer: Follow geometry buffer (modified in place)
        :param line_num: Current line number for the log
        """
        if len(path) < 3:
            self.app.log.warning("Region contour with less than 3 points ignored. (%d)" % line_num)
            return

        self._ensure_region_tool()
        try:
            region = Polygon(path)
        except ValueError:
            self.app.log.warning("Region contour is not valid. (%d)" % line_num)
            return
        if region.is_empty:
            return

        polygons = [region]
        if not region.is_valid:
            self.app.log.warning("Found invalid Gerber geometry at line: %s. Fixing..." % str(line_num))
            polygons = _repair_polygon(region)
            if not polygons:
                self.app.log.warning("Failed to fix the invalid Geometry found at line: %s" % str(line_num))
                return

        for pol in polygons:
            geo_dict = {}
            exterior = pol.exterior
            if not exterior.is_empty:
                follow_buffer.append(exterior)
                geo_dict['follow'] = exterior

            poly_buffer.append(pol)
            if self.is_lpc is True:
                geo_dict['clear'] = pol
            else:
                geo_dict['solid'] = pol

            self._store(0, geo_dict)

    def _handle_region_start(
        self,
        path: List[List[float]],
        last_path_aperture: Optional[int],
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        steps: int,
        line_num: int,
        current_operation_code: Optional[int]
    ) -> Tuple[List[List[float]], Optional[int]]:
        """
        Handle G36* command (begin region mode).

        Finalizes any pending path geometry before entering region mode.

        :param path: Current path coordinates
        :param last_path_aperture: Last used aperture ID
        :param poly_buffer: Polygon buffer (modified in place)
        :param follow_buffer: Follow geometry buffer (modified in place)
        :param steps: Arc resolution
        :param line_num: Current line number for error reporting
        :param current_operation_code: Last operation code
        :return: Tuple of (updated_path, updated_last_path_aperture)
        """
        if len(path) > 1:
            # Finalize pending path before region
            # Ensure last_path_aperture is valid before accessing tools
            if last_path_aperture is None or last_path_aperture not in self.tools:
                self.app.inform.emit(
                    '[ERROR] %s' % (_(
                        "Gerber implementation format not supported. "
                        "Undefined aperture at line: %s. "
                        "Report to developers.") % str(line_num)))
                return [path[-1]], last_path_aperture

            self._add_path_geometry_to_buffers(path, last_path_aperture, poly_buffer, follow_buffer, steps)
            path = [path[-1]]

        return path, last_path_aperture

    def _handle_region_end(
        self,
        path: List[List[float]],
        current_aperture: Optional[int],
        current_x: float,
        current_y: float,
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        steps: int,
        line_num: int,
        current_operation_code: Optional[int]
    ) -> Tuple[List[List[float]], bool]:
        """
        Handle G37* command (end region mode).

        Creates region polygon from the pending contour (a contour closed by D02 was added already) and adds it
        to the buffers.

        :param path: Region path coordinates
        :param current_aperture: Current aperture ID (it may be None for regions)
        :param current_x: Current X coordinate
        :param current_y: Current Y coordinate
        :param poly_buffer: Polygon buffer (modified in place)
        :param follow_buffer: Follow geometry buffer (modified in place)
        :param steps: Arc resolution
        :param line_num: Current line number
        :param current_operation_code: Last operation code
        :return: Tuple of (new_path, success)
        """
        # Ensure aperture 0 exists for region storage
        self._ensure_region_tool()

        # a contour with less than 3 points is not a region
        if len(path) >= 3:
            self._add_region_contour(path, poly_buffer, follow_buffer, line_num)

        return [[current_x, current_y]], False  # Region ended (always False since G37 ends region)

    def create_geometry(self):
        """
        Geometry from a Gerber file is made up entirely of polygons.
        Every stroke (linear or circular) has an aperture which gives
        it thickness. Additionally, aperture strokes have non-zero area,
        and regions naturally do as well.

        :rtype : None
        :return: None
        """
        pass
        # self.buffer_paths()
        #
        # self.fix_regions()
        #
        # self.do_flashes()
        #
        # self.solid_geometry = unary_union(self.buffered_paths +
        #                                      [poly['polygon'] for poly in self.regions] +
        #                                      self.flash_geometry)

    def get_bounding_box(self, margin=0.0, rounded=False):
        """
        Creates and returns a rectangular polygon bounding at a distance of
        margin from the object's ``solid_geometry``. If margin > 0, the polygon
        can optionally have rounded corners of radius equal to margin.

        :param margin: Distance to enlarge the rectangular bounding
         box in both positive and negative, x and y axes.
        :type margin: float
        :param rounded: Whether to have rounded corners.
        :type rounded: bool
        :return: The bounding box.
        :rtype: Shapely.Polygon
        """

        assert isinstance(self.solid_geometry, BaseGeometry)
        bbox = self.solid_geometry.envelope.buffer(margin)
        if not rounded:
            bbox = bbox.envelope
        return bbox

    def bounds(self, flatten=None):
        """
        Returns coordinates of rectangular bounds
        of Gerber geometry: (xmin, ymin, xmax, ymax).

        :param flatten:     Not used, it is here for compatibility with base class method
        :return:            None
        """

        self.app.log.debug("parseGerber.Gerber.bounds()")

        if self.solid_geometry is None:
            self.app.log.debug("solid_geometry is None")
            return 0, 0, 0, 0

        def bounds_rec(obj):
            if type(obj) is list and type(obj) is not MultiPolygon:
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
                        if not k.is_empty:
                            try:
                                minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                            except Exception as e:
                                self.app.log.error("camlib.Gerber.bounds() --> %s" % str(e))
                                return

                            minx = min(minx, minx_)
                            miny = min(miny, miny_)
                            maxx = max(maxx, maxx_)
                            maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's a Shapely object, return its bounds
                return obj.bounds

        _bounds_rec_slow = bounds_rec

        def bounds_rec(obj):
            res = list_total_bounds(obj, nested=False)
            return _bounds_rec_slow(obj) if res is None else res

        bounds_coords = bounds_rec(self.solid_geometry)
        return bounds_coords

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
            self.app.log.debug("parseGerber.Gerber.convert_units() --> Factor: 1")
            return 1.0

        if obj_units.upper() == "MM":
            factor = 25.4
            self.app.log.debug("parseGerber.Gerber.convert_units() --> Factor: %s" % str(factor))
        elif obj_units.upper() == "IN":
            factor = 1 / 25.4
            self.app.log.debug("parseGerber.Gerber.convert_units() --> Factor: %s" % str(factor))
        else:
            self.app.log.error("Unsupported units: %s" % str(obj_units))
            self.app.log.debug("parseGerber.Gerber.convert_units() --> Factor: 1")
            return 1.0

        self.units = obj_units
        self.file_units_factor = factor
        self.scale(factor, factor)
        return factor

    def import_svg(self, filename, object_type='gerber', flip=True, units=None):
        """
        Imports shapes from an SVG file into the object's geometry.

        :param filename:        Path to the SVG file.
        :type filename:         str
        :param object_type:     parameter passed further along
        :param flip:            Flip vertically.
        :type flip:             bool
        :param units:           FlatCAM units
        :return: None
        """

        self.app.log.debug("appParsers.ParseGerber.Gerber.import_svg()")

        units = self.app.app_units if units is None else units
        # every circle gets the steps of its own radius (in the output units)
        options = getattr(self.app, 'options', None)
        res = lambda radius: gerber_circle_steps(options, radius, units)

        # Parse into a flat list of shapely Polygons (units conversion, flip and text are handled by the importer)
        try:
            geos = svg_to_shapes(filename, 'gerber', units=units, resolution=res, flip=flip, app=self.app)
        except SvgImportError as e:
            self.app.log.error("ParseGerber.import_svg(). %s" % str(e))
            return 'fail'

        geos = [g for g in geos if isinstance(g, Polygon)]

        self.app.log.debug("appParsers.ParseGerber.Gerber.import_svg(). Finished parsing the SVG geometry.")

        # Work with a local list first because it makes typing a lot easier
        solid_geometry: list[BaseGeometry] = []
        if isinstance(self.solid_geometry, BaseGeometry):
            solid_geometry.append(self.solid_geometry)
        elif isinstance(self.solid_geometry, list):
            solid_geometry += self.solid_geometry

        if type(geos) == list:
            # HACK for importing QRCODE exported by FlatCAM
            try:
                geos_length = len(geos)
            except TypeError:
                geos_length = 1

            if geos_length == 1:
                geo_qrcode = [Polygon(geos[0].exterior)]
                for i_el in geos[0].interiors:
                    geo_qrcode.append(Polygon(i_el).buffer(0, quad_segs=res))
                geos = [poly for poly in geo_qrcode]

            solid_geometry += geos
        elif isinstance(geos, BaseGeometry):
            solid_geometry.append(geos)

        # flatten the self.solid_geometry list for import_svg() to import SVG as Gerber
        solid_geometry = flatten_shapely_geometry(solid_geometry)
        if 0 not in self.tools:
            self.tools[0] = {
                'type':         'REG',
                'size':         0.0,
                'geometry':     []
            }

        for pol in solid_geometry:
            prepare(pol)
            new_el = {'solid': pol, 'follow': LineString(pol.exterior.coords)}
            self.tools[0]['geometry'].append(new_el)
        self.solid_geometry = solid_geometry

    def import_dxf_as_gerber(self, filename, units='MM', text_mode='stroke'):
        """
        Imports shapes from a DXF file into the Gerber object geometry.

        :param filename:    Path to the DXF file.
        :type filename:     str
        :param units:       Application units
        :param text_mode:   'stroke' for CNC paths, 'outline' for filled shapes, 'none' to skip
        :return: None
        """

        self.app.log.debug("Parsing DXF file geometry into a Gerber object geometry.")

        self.multigeo = True

        # Parse into list of shapely objects
        try:
            dxf = ezdxf.readfile(filename)
        except (KeyError, Exception) as e:
            self.app.log.warning("Standard DXF read failed (%s), trying recovery mode..." % str(e))
            dxf, auditor = recover.readfile(filename)
        geos = dxf_to_shapes(dxf, text_mode=text_mode, options=getattr(self.app, 'options', None), kind='gerber')

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

        # Work with a local list first because it makes typing a lot easier
        solid_geometry: list[BaseGeometry] = []
        if isinstance(self.solid_geometry, BaseGeometry):
            solid_geometry.append(self.solid_geometry)
        elif isinstance(self.solid_geometry, list):
            solid_geometry += self.solid_geometry

        if type(geos) is list:
            solid_geometry += geos
        elif isinstance(geos, BaseGeometry):
            solid_geometry.append(geos)

        # flatten the solid_geometry list for import_dxf() to import DXF as Gerber
        flat_geo = list(self.flatten_list(solid_geometry))
        if flat_geo:
            solid_geometry = unary_union(flat_geo)
            prepare(solid_geometry)
            self.solid_geometry = solid_geometry
            self.follow_geometry = solid_geometry
        else:
            return "fail"

        # create the self.tools data structure
        if 0 not in self.tools:
            self.tools[0] = {
                'type': 'REG',
                'size': 0.0,
                'geometry': []
            }

        for pol in flat_geo:
            new_el = {'solid': pol, 'follow': pol}
            self.tools[0]['geometry'].append(new_el)

    def _affine_all(self, matrix) -> None:
        """
        Applies one affine matrix to solid_geometry, follow_geometry and the 'solid', 'follow' and 'clear' geometry of
        every aperture geometry element, with ONE vectorized transform call. Container shapes are kept.

        :param matrix: (a, b, d, e, xoff, yoff), see camlib.affine_geometry_many
        """
        entries = []
        structures = [self.solid_geometry, self.follow_geometry]
        for tool in self.tools.values():
            for geo_el in tool.get('geometry', ()):
                for key in ('solid', 'follow', 'clear'):
                    if key in geo_el:
                        entries.append((geo_el, key))
                        structures.append(geo_el[key])

        result = affine_geometry_many(structures, matrix)
        self.solid_geometry, self.follow_geometry = result[0], result[1]
        for (geo_el, key), geo in zip(entries, result[2:]):
            geo_el[key] = geo

    def scale(
        self,
        xfactor: float,
        yfactor: Optional[float] = None,
        point: Optional[Tuple[float, float]] = None
    ) -> Optional[str]:
        """
        Scales the objects' geometry on the XY plane by a given factor.
        These are:

        * ``buffered_paths``
        * ``flash_geometry``
        * ``solid_geometry``
        * ``regions``

        NOTE:
        Does not modify the data used to create these elements. If these
        are recreated, the scaling will be lost. This behavior was modified
        because of the complexity reached in this class.

        :param xfactor: Number by which to scale on X axis.
        :type xfactor: float
        :param yfactor: Number by which to scale on Y axis.
        :type yfactor: float
        :param point: reference point for scaling operation
        :rtype : None
        """
        self.app.log.debug("parseGerber.Gerber.scale()")

        try:
            xfactor = float(xfactor)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Scale factor has to be a number: integer or float."))
            return

        if yfactor is None:
            yfactor = xfactor
        else:
            try:
                yfactor = float(yfactor)
            except Exception:
                self.app.inform.emit('[ERROR_NOTCL] %s' %
                                     _("Scale factor has to be a number: integer or float."))
                return

        if xfactor == 0 and yfactor == 0:
            return

        if point is None:
            px = 0
            py = 0
        else:
            px, py = point

        try:
            Gerber._affine_all(self, affine_scale_matrix(xfactor, yfactor, (px, py)))
            for apid in self.tools:
                self.tools[apid].setdefault('geometry', [])

            update_scale_metadata(self, xfactor, yfactor)
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.scale() Exception --> %s' % str(e))
            return 'fail'

        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

        # ## solid_geometry ???
        #  It's a cascaded union of objects.
        # self.solid_geometry = affinity.scale(self.solid_geometry, factor,
        #                                      factor, origin=(0, 0))

        # # Now buffered_paths, flash_geometry and solid_geometry
        # self.create_geometry()

    def offset(self, vect: Tuple[float, float]) -> Optional[str]:
        """
        Offsets the objects' geometry on the XY plane by a given vector.
        These are:

        * ``buffered_paths``
        * ``flash_geometry``
        * ``solid_geometry``
        * ``regions``

        NOTE:
        Does not modify the data used to create these elements. If these
        are recreated, the scaling will be lost. This behavior was modified
        because of the complexity reached in this class.

        :param vect: (x, y) offset vector.
        :type vect: tuple
        :return: None
        """
        self.app.log.debug("ParseGerber.Gerber.offset()")

        try:
            dx, dy = vect
        except TypeError:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("An (x,y) pair of values are needed. "
                                   "Probable you entered only one value in the Offset field."))
            return

        if dx == 0 and dy == 0:
            return

        try:
            Gerber._affine_all(self, affine_translate_matrix(dx, dy))
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.offset() Exception --> %s' % str(e))
            return 'fail'

        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

    def mirror(self, axis: str, point: List[float]) -> Optional[str]:
        """
        Mirrors the object around a specified axis passing through
        the given point. What is affected:

        * ``buffered_paths``
        * ``flash_geometry``
        * ``solid_geometry``
        * ``regions``

        NOTE:
        Does not modify the data used to create these elements. If these
        are recreated, the scaling will be lost. This behavior was modified
        because of the complexity reached in this class.

        :param axis: "X" or "Y" indicates around which axis to mirror.
        :type axis: str
        :param point: [x, y] point belonging to the mirror axis.
        :type point: list
        :return: None
        """
        self.app.log.debug("parseGerber.Gerber.mirror()")

        px, py = point
        xscale, yscale = {"X": (1.0, -1.0), "Y": (-1.0, 1.0)}[axis]

        try:
            Gerber._affine_all(self, affine_mirror_matrix(axis, (px, py)))
            update_mirror_metadata(self, axis)
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.mirror() Exception --> %s' % str(e))
            return 'fail'

        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

    def skew(self, angle_x: float, angle_y: float, point: List[float]) -> Optional[str]:
        """
        Shear/Skew the geometries of an object by angles along x and y dimensions.

        Parameters
        ----------
        angle_x, angle_y : float, float
            The shear angle(s) for the x and y axes respectively. These can be
            specified in either degrees (default) or radians by setting
            use_radians=True.

        See shapely manual for more information:
        http://toblerity.org/shapely/manual.html#affine-transformations
        :param angle_x: the angle on X axis for skewing
        :param angle_y: the angle on Y axis for skewing
        :param point: reference point for skewing operation
        :return None
        """
        self.app.log.debug("parseGerber.Gerber.skew()")

        px, py = point

        if angle_x == 0 and angle_y == 0:
            return

        try:
            Gerber._affine_all(self, affine_skew_matrix(angle_x, angle_y, (px, py)))
            update_skew_metadata(self)
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.skew() Exception --> %s' % str(e))
            return 'fail'

        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

    def rotate(self, angle: float, point: List[float]) -> Optional[str]:
        """
        Rotate an object by a given angle around given coords (point)
        :param angle:
        :param point:
        :return:
        """
        self.app.log.debug("parseGerber.Gerber.rotate()")

        px, py = point

        if angle == 0:
            return

        try:
            Gerber._affine_all(self, affine_rotate_matrix(angle, (px, py)))
            update_rotate_metadata(self, angle)
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.rotate() Exception --> %s' % str(e))
            return 'fail'
        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

    def buffer(
        self,
        distance: float,
        join: str = "mitre",
        factor: Optional[bool] = None,
        only_exterior: bool = False,
        muted: bool = False
    ) -> None | str:

        """

        :param distance:        If 'factor' is True then distance is the factor
        :param join:            The type of joining used by the Shapely buffer method. Can be: round, mitre and bevel
        :param factor:          truthy: multiplicative mode, distance is the multiplier; None or False: distance mode
        :param only_exterior:   Bool. If True, the LineStrings are buffered only on the outside
        :param muted:           Bool. If True, the buffer operation is muted
        :return:                None | str
        """
        self.app.log.debug("parseGerber.Gerber.buffer()")

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
                self.app.log.debug('ParseGerber.Gerber.buffer() rejected --> the factor is not positive')
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' %
                                     (_("Gerber buffer by a factor was not done"),
                                      _("The factor must be a positive number.")))
                self.app.proc_container.new_text = ''
                return 'fail'
        else:
            if not finite:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("The buffer distance is not a number."))
                self.app.proc_container.new_text = ''
                return 'fail'
            if distance == 0:
                return

        # variables to display the percentage of work done
        self.geo_len = 0
        try:
            if isinstance(self.solid_geometry, (MultiPolygon, MultiLineString)):
                self.geo_len = len(self.solid_geometry.geoms)
            if isinstance(self.solid_geometry, list):
                self.geo_len = len(self.solid_geometry)
        except (TypeError, ValueError, RuntimeError):
            self.geo_len = 1

        self.old_disp_number = 0
        self.el_count = 0

        if not factor:
            if self.app.abort_flag:
                if muted is False:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                self.app.proc_container.new_text = ''
                return 'fail'
            try:
                quad_segs = circle_quad_segs(_circle_steps_of(self, abs(distance)))
                structures = [self.solid_geometry]
                record_targets = []
                new_tools = {}
                for apid, tool in self.tools.items():
                    new_tool = dict(tool)
                    if 'geometry' in tool:
                        new_records = []
                        for geo_el in tool['geometry']:
                            new_geo_el = dict(geo_el)
                            for key in ('solid', 'clear'):
                                if key in geo_el:
                                    record_targets.append((new_geo_el, key))
                                    structures.append(geo_el[key])
                            new_records.append(new_geo_el)
                        new_tool['geometry'] = new_records
                    new_tools[apid] = new_tool

                # one vectorized buffer of the solid geometry and every aperture record
                buffered = buffer_geometry_many(structures, distance, quad_segs, join,
                                                only_exterior=only_exterior, allow_empty=True)
                new_solid = buffered[0]
                for (new_geo_el, key), geometry in zip(record_targets, buffered[1:]):
                    new_geo_el[key] = geometry

                target = SimpleNamespace(tools=new_tools,
                                         export_as_regions=bool(getattr(self, 'export_as_regions', False)))
                update_buffer_metadata(target, distance, join)
            except Exception as e:
                self.app.log.error('ParseGerber.Gerber.buffer() Exception --> %s' % str(e))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Gerber buffer failed"), str(e)))
                self.app.proc_container.new_text = ''
                return 'fail'

            if self.app.abort_flag:
                if muted is False:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                self.app.proc_container.new_text = ''
                return 'fail'

            # commit: plain assignments, together
            for apid, new_tool in new_tools.items():
                self.tools[apid] = new_tool
            self.solid_geometry = flatten_shapely_geometry(new_solid)
            self.export_as_regions = target.export_as_regions
        else:
            # Multiplicative buffer (Stage 3): see plan_multiplicative_buffer(). Nothing is assigned until every step
            # has succeeded, so a rejection or a failure leaves the object unchanged.
            try:
                if self.app.abort_flag:
                    if muted is False:
                        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                    self.app.proc_container.new_text = ''
                    return 'fail'
                new_tools, new_solid, new_flag = plan_multiplicative_buffer(self, float(distance))
            except MultiplicativeBufferRejected as e:
                self.app.log.debug('ParseGerber.Gerber.buffer() rejected --> %s' % str(e))
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("Gerber buffer by a factor was not done"), str(e)))
                self.app.proc_container.new_text = ''
                return 'fail'
            except Exception as e:
                self.app.log.error('ParseGerber.Gerber.buffer() Exception --> %s' % str(e))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Gerber buffer by a factor failed"), str(e)))
                self.app.proc_container.new_text = ''
                return 'fail'

            if self.app.abort_flag:
                if muted is False:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                self.app.proc_container.new_text = ''
                return 'fail'

            # commit: plain assignments, together
            for apid, new_tool in new_tools.items():
                self.tools[apid] = new_tool
            self.solid_geometry = new_solid
            self.export_as_regions = new_flag
        if muted is False:
            self.app.inform.emit('[success] %s' % _("Gerber Buffer done."))
        self.app.proc_container.new_text = ''


# ---------------------------------------------------------------------------------------------------------------
# Stage 0C: the aperture metadata after a transform.
#
# The native Gerber writer declares the apertures from the metadata (C: size; R, O: width, height) and writes the
# 'follow' geometry of the records. After a transform the metadata must still describe the geometry, or
# ``Gerber.export_as_regions`` is set and the export writes the composed ``solid_geometry`` as regions. The rules
# are in the Stage 0C table. The helpers below are called by Gerber.scale, rotate, mirror, skew and buffer once the
# geometry is done; they never change the geometry or the records. Offset changes no metadata.
#
# Aperture keys (the parser, ``Gerber.aperture_parse``):
#   C: size | R, O: width, height, size (= hypot(width, height), the width of a track) |
#   P: diam, nVertices, size (= diam), rotation (optional, degrees) | AM: macro, modifiers | REG: size, geometry
# ---------------------------------------------------------------------------------------------------------------

_QUARTER_TURN_TOLERANCE = 1e-9      # degrees
_RECTANGLE_FLASH_TOLERANCE = 1e-5   # the parser rounds the corners of a rectangle flash with a 1e-7 buffer
_RECTANGLE_AREA_TOLERANCE = 1e-4    # relative


def drop_placeholder_record_keys(tools: Dict[Any, Any]) -> Dict[Any, Any]:
    """
    Remove the 'solid', 'follow' and 'clear' keys whose value is not a geometry from every aperture record.
    Older copper thieving code stored [] for a missing key; the readers test the presence of a key ('clear' in
    record), so such a record counted as a clear feature. The tools are changed in place and returned.

    :param tools:   the apertures dictionary of a Gerber object
    :return:        the same dictionary
    """
    for tool in (tools or {}).values():
        for record in tool.get('geometry', None) or ():
            for key in ('solid', 'follow', 'clear'):
                if key in record and not isinstance(record[key], BaseGeometry):
                    del record[key]
    return tools


def _is_rectangle_flash(tool: Dict[str, Any], record: Dict[str, Any]) -> bool:
    """
    Tell if the solid geometry of a record of an 'R' aperture is a flash of that aperture, that is, its bounds
    are as wide and as high as the aperture. The parser makes a box around the end points for a track drawn with
    an 'R' aperture; the native writer can not write such a record (it flashes the aperture at the end point).

    :param tool:    the 'R' aperture
    :param record:  one record of the aperture
    :return:        False only when the solid geometry has other bounds than the aperture
    """
    solid = record.get('solid')
    bounds = getattr(solid, 'bounds', None)
    if solid is None or not bounds or len(bounds) != 4 or getattr(solid, 'is_empty', True):
        return True
    try:
        width = abs(bounds[2] - bounds[0])
        height = abs(bounds[3] - bounds[1])
        return abs(width - tool['width']) <= _RECTANGLE_FLASH_TOLERANCE and \
            abs(height - tool['height']) <= _RECTANGLE_FLASH_TOLERANCE
    except (KeyError, TypeError):
        return True


_REQUIRED_KEYS = {'R': ('width', 'height'), 'O': ('width', 'height'), 'P': ('diam',)}


def _is_well_formed(tool: Dict[str, Any]) -> bool:
    """
    Tell if an aperture has the keys its type needs ('R', 'O': width and height; 'P': diam). An aperture without
    them can not be updated, the callers send it to the regions export.
    """
    return all(key in tool for key in _REQUIRED_KEYS.get(str(tool.get('type')), ()))


def _flag_regions(gerber: Any, tool: Dict[str, Any]) -> None:
    """
    Mark that the aperture metadata can no longer describe the geometry, but only if the aperture has at least
    1 record (a flash or a track). The flag never goes back to False inside a transform.

    :param gerber:  the Gerber object
    :param tool:    the aperture
    :return:        None
    """
    if tool.get('geometry'):
        gerber.export_as_regions = True


def _has_track_records(tool: Dict[str, Any]) -> bool:
    """
    Tell if any record of the aperture is a track (its 'follow' is a line, not a flash point).

    :param tool:    the aperture
    :return:        True if there is a track record
    """
    for record in tool.get('geometry', ()):
        if getattr(record.get('follow'), 'geom_type', None) in ('LineString', 'LinearRing', 'MultiLineString'):
            return True
    return False


def _records_fill_rectangle(tool: Dict[str, Any]) -> bool:
    """
    Tell if the solid geometry of every record of an 'R' aperture is as large as the rectangle given by the
    width and height of the aperture. The parser rounds the corners of a rectangle flash with a 1e-7 buffer, and a
    mitre buffer of such a flash rounds the corners again (the many short edges of the tiny arcs get mitre joins
    that follow a circle), so the buffered flash is not a rectangle of the buffered width and height.

    :param tool:    the 'R' aperture, with its new width and height
    :return:        True if the records are rectangles of the aperture size
    """
    expected = tool['width'] * tool['height']
    for record in tool.get('geometry', ()):
        solid = record.get('solid')
        if solid is None:
            continue
        area = getattr(solid, 'area', None)
        if area is None or abs(area - expected) > _RECTANGLE_AREA_TOLERANCE * expected:
            return False
    return True


def _set_rectangle(tool: Dict[str, Any], width: float, height: float) -> None:
    """
    Store the width and height of an 'R' or 'O' aperture. 'size' is kept as the parser makes it: the diagonal.
    """
    tool['width'] = float(width)
    tool['height'] = float(height)
    tool['size'] = float(np.hypot(width, height))


def _set_polygon_rotation(tool: Dict[str, Any], rotation: float) -> None:
    """
    Store the rotation of a 'P' aperture, in degrees, reduced to [0, 360). The parser omits the key for 0, so it
    is left out when the aperture had none and the new value is 0.
    """
    rotation = float(rotation) % 360.0
    if 'rotation' in tool or rotation != 0.0:
        tool['rotation'] = rotation


def _scale_size(tool: Dict[str, Any], factor: float) -> None:
    """Multiply the generic 'size' by a (non-negative) factor, as the code did before Stage 0C."""
    if tool.get('size') is not None:
        tool['size'] = float(tool['size'] * factor)


def update_scale_metadata(gerber: Any, xfactor: float, yfactor: float) -> None:
    """
    Update the aperture metadata after ``Gerber.scale``. Sizes are always taken with ``abs()``.

    * ``s > 0`` uniform: C size, R and O width and height, P diam are multiplied; AM: regions.
    * ``s < 0`` uniform (a 180 degrees rotation): the same with ``abs(s)``; P rotation + 180.
    * non-uniform (``abs(sx) != abs(sy)``): R width * abs(sx), height * abs(sy); C, O, P and AM: regions.
    * ``scale(-s, s)`` / ``scale(s, -s)``: like the mirror rows, then the uniform ``abs(s)``. A negative
      ``yfactor`` negates Y (mirror X), a negative ``xfactor`` negates X (mirror Y).
    * a zero factor (the geometry collapses): every aperture is regions.

    :param gerber:  the Gerber object; its apertures are updated, the flag is set where needed
    :param xfactor: the scale factor on X
    :param yfactor: the scale factor on Y
    :return:        None
    """
    ax = abs(xfactor)
    ay = abs(yfactor)
    nonzero = ax > 0 and ay > 0
    uniform = nonzero and bool(np.isclose(ax, ay, rtol=1e-12, atol=0.0))

    for tool in gerber.tools.values():
        kind = str(tool.get('type'))
        representable = True
        if not _is_well_formed(tool):
            _flag_regions(gerber, tool)
            continue

        if kind == 'C':
            representable = uniform
            _scale_size(tool, ax)
        elif kind == 'R':
            representable = nonzero
            if representable:
                _set_rectangle(tool, tool['width'] * ax, tool['height'] * ay)
            else:
                tool['width'] *= ax
                tool['height'] *= ax
                _scale_size(tool, ax)
        elif kind == 'O':
            representable = uniform
            tool['width'] *= ax
            tool['height'] *= ax
            _scale_size(tool, ax)
        elif kind == 'P':
            representable = uniform
            tool['diam'] *= ax
            _scale_size(tool, ax)
            if representable:
                rotation = tool.get('rotation', 0.0)
                if xfactor < 0 and yfactor < 0:
                    rotation += 180.0
                elif yfactor < 0:
                    rotation = -rotation            # mirror X
                elif xfactor < 0:
                    rotation = 180.0 - rotation     # mirror Y
                _set_polygon_rotation(tool, rotation)
        elif kind == 'AM':
            representable = False
        else:
            _scale_size(tool, ax)

        if not representable:
            _flag_regions(gerber, tool)


def update_rotate_metadata(gerber: Any, angle: float) -> None:
    """
    Update the aperture metadata after ``Gerber.rotate`` by ``angle`` degrees.

    * C: unchanged.
    * R and O: an odd quarter turn swaps width and height, an even quarter turn changes nothing, any other
      angle is regions. A quarter turn is a multiple of 90 degrees within 1e-9 degrees, after reducing the
      angle modulo 360.
    * P: rotation + angle. AM: regions.

    :param gerber:  the Gerber object
    :param angle:   the rotation angle, in degrees
    :return:        None
    """
    reduced = float(angle) % 360.0
    quarters = round(reduced / 90.0)
    is_quarter_turn = abs(reduced - quarters * 90.0) <= _QUARTER_TURN_TOLERANCE
    is_odd_turn = is_quarter_turn and quarters % 2 == 1

    for tool in gerber.tools.values():
        kind = str(tool.get('type'))
        if not _is_well_formed(tool):
            _flag_regions(gerber, tool)
        elif kind in ('R', 'O'):
            if not is_quarter_turn:
                _flag_regions(gerber, tool)
            elif is_odd_turn:
                tool['width'], tool['height'] = tool['height'], tool['width']
        elif kind == 'P':
            _set_polygon_rotation(tool, tool.get('rotation', 0.0) + angle)
        elif kind == 'AM':
            _flag_regions(gerber, tool)


def update_mirror_metadata(gerber: Any, axis: str) -> None:
    """
    Update the aperture metadata after ``Gerber.mirror``. Mirror X negates Y and mirror Y negates X.

    * C, R, O: unchanged.
    * P: mirror X: rotation = -rotation; mirror Y: rotation = 180 - rotation.
    * AM: regions.

    :param gerber:  the Gerber object
    :param axis:    "X" or "Y"
    :return:        None
    """
    for tool in gerber.tools.values():
        kind = str(tool.get('type'))
        if kind == 'P':
            rotation = tool.get('rotation', 0.0)
            _set_polygon_rotation(tool, -rotation if axis == 'X' else 180.0 - rotation)
        elif kind == 'AM':
            _flag_regions(gerber, tool)


def update_skew_metadata(gerber: Any) -> None:
    """
    Update the aperture metadata after ``Gerber.skew`` with a non-zero angle: no aperture type survives a skew,
    so every aperture with records is regions. The metadata values are left as they are.

    :param gerber:  the Gerber object
    :return:        None
    """
    for tool in gerber.tools.values():
        if str(tool.get('type')) in ('C', 'R', 'O', 'P', 'AM'):
            _flag_regions(gerber, tool)


def _is_mitre_join(join: Any) -> bool:
    """
    Tell if the join style given to ``Gerber.buffer`` is the mitre join. Callers pass the Shapely name
    ('mitre', the default of the method, 'round', 'bevel') or the Shapely code (1 round, 2 mitre, 3 bevel).
    The plugin Transform passes 1 or 2, the Tcl command 'buffer' passes 1 or the names 'square' and 'bevel'.
    """
    if isinstance(join, str):
        return join.strip().lower() == 'mitre'
    try:
        return int(join) == 2
    except (TypeError, ValueError):
        return False


def update_buffer_metadata(gerber: Any, distance: float, join: Any) -> None:
    """
    Update the aperture metadata after ``Gerber.buffer`` by a distance (not by a factor).

    * C: size + 2d.
    * R: mitre join: width, height + 2d, if the records are exact rectangles of that size (see
      ``_records_fill_rectangle``: a parsed flash has corners rounded by the parser, so a positive mitre buffer
      rounds them and the result is regions); any other join rounds the corners: regions.
    * O: width, height + 2d. The parser draws a track of an O aperture with 'size' (the diagonal) as its width,
      and hypot(w + 2d, h + 2d) is not 'size' + 2d, so an O aperture with a track is regions.
    * P and AM: regions.
    * A size that is not positive after the buffer (a negative distance) is regions.

    Where the result is regions, the metadata keeps the values the code produced before Stage 0C.

    :param gerber:      the Gerber object
    :param distance:    the buffer distance
    :param join:        the join style: Shapely name or code
    :return:            None
    """
    grow = distance * 2
    mitre = _is_mitre_join(join)

    for tool in gerber.tools.values():
        kind = str(tool.get('type'))
        representable = True
        if not _is_well_formed(tool):
            _flag_regions(gerber, tool)
            continue

        if kind == 'C':
            _grow_size(tool, grow)
            representable = tool.get('size') is None or tool['size'] > 0
        elif kind in ('R', 'O'):
            tool['width'] += grow
            tool['height'] += grow
            representable = tool['width'] > 0 and tool['height'] > 0
            if kind == 'R':
                representable = representable and mitre and _records_fill_rectangle(tool)
            else:
                representable = representable and not _has_track_records(tool)
            if representable:
                _set_rectangle(tool, tool['width'], tool['height'])
            else:
                _grow_size(tool, grow)
        elif kind == 'P':
            tool['diam'] += grow
            _grow_size(tool, grow)
            representable = False
        elif kind == 'AM':
            representable = False
        else:
            _grow_size(tool, grow)

        if not representable:
            _flag_regions(gerber, tool)


class MultiplicativeBufferRejected(Exception):
    """The multiplicative buffer can not be done on this object; the message is for the user."""


def _scale_metadata_by_factor(tool: Dict[str, Any], factor: float) -> bool:
    """
    Update the aperture metadata of a COPY of an aperture after every flash was scaled by ``factor`` about its own
    point and every C track was rebuilt with the width ``size * factor``.

    * C: size * f.
    * R, O: width, height * f; size = hypot(width, height), as the parser makes it.
    * P: diam * f and size (= diam); nVertices and rotation are kept.
    * AM: not representable (the macro modifiers are not scaled): returns False.
    * REG: unchanged.

    :param tool:    the copy of the aperture, changed in place
    :param factor:  the positive factor
    :return:        False if the metadata can no longer describe the records (the regions export is needed)
    """
    kind = str(tool.get('type'))
    if kind == 'REG':
        return True
    if kind == 'AM':
        return False
    if not _is_well_formed(tool):
        return False
    if kind in ('R', 'O'):
        _set_rectangle(tool, tool['width'] * factor, tool['height'] * factor)
    elif kind == 'P':
        tool['diam'] = float(tool['diam'] * factor)
        tool['size'] = tool['diam']
    else:
        _scale_size(tool, factor)
    return True


def plan_multiplicative_buffer(gerber: Any, factor: float) -> Tuple[Dict[Any, Dict[str, Any]], BaseGeometry, bool]:
    """
    Plan the multiplicative buffer of a Gerber by ``factor`` (> 0) without changing the object.

    Supported: the flashes of every aperture type (the solid of the record is scaled about its 'follow' point), the
    tracks drawn with a C aperture (rebuilt from 'follow' with the width ``size * factor``, as the parser draws
    them: ``follow.buffer(width / 2, quad_segs of the circle)``) and the regions (aperture 0 / 'REG', unchanged).

    Rejected (MultiplicativeBufferRejected): a record with a non-empty 'clear' geometry (the records do not hold the
    global order of the file, so the polarity can not be composed again), a track drawn with another aperture than
    C (it has no defined width), a record that is not a flash or a track the parser makes, and an object whose
    composed geometry is not empty while its records hold no solid.

    :param gerber:  the Gerber object, not changed
    :param factor:  the positive factor
    :return:        (new apertures {apid: new dict}, new composed solid_geometry, new export_as_regions)
    """
    from appParsers.feature_access import normalize_polygonal    # imported here, like Gerber.needs_region_export


    # (a) scan: nothing is built before the whole object is known to be supported
    for apid, tool in gerber.tools.items():
        kind = str(tool.get('type'))
        is_region = apid == 0 or kind == 'REG'
        for record in tool.get('geometry', ()):
            clear = record.get('clear')
            # None, or a non-geometry like the [] of older code, counts as no clear geometry
            if not getattr(clear, 'is_empty', True):
                raise MultiplicativeBufferRejected(
                    _("the object has clear (negative polarity) features, the polarity can not be composed again"))
            if is_region:
                continue
            solid = record.get('solid')
            follow = record.get('follow')
            if follow is None:
                if solid is not None and not solid.is_empty:
                    raise MultiplicativeBufferRejected(
                        _("a feature of the aperture %s has no position to scale about") % str(apid))
            elif isinstance(follow, Point):
                if kind == 'R' and not _is_rectangle_flash(tool, record):
                    raise MultiplicativeBufferRejected(
                        _("the aperture %s is rectangular and draws a track, which has no defined width") % str(apid))
            elif kind != 'C':
                raise MultiplicativeBufferRejected(
                    _("the aperture %s draws a track and is not circular, a track has no defined width")
                    % str(apid))
            elif solid is not None:
                expected = follow.buffer(float(tool['size']) / 2,
                                         circle_quad_segs(_circle_steps_of(gerber, float(tool['size']) / 2)))
                if solid.symmetric_difference(expected).area > 1e-6 + 1e-4 * expected.area:
                    raise MultiplicativeBufferRejected(
                        _("a track of the aperture %s was not drawn by the Gerber parser") % str(apid))

    # (b) build the new apertures, records and metadata in copies
    new_tools = {}
    flag = bool(getattr(gerber, 'export_as_regions', False))
    dark = []
    for apid, tool in gerber.tools.items():
        new_tool = dict(tool)
        is_region = apid == 0 or str(tool.get('type')) == 'REG'
        new_records = []
        has_records = bool(tool.get('geometry'))
        if not is_region:
            if not _scale_metadata_by_factor(new_tool, factor) and has_records:
                flag = True
        for record in tool.get('geometry', ()):
            new_record = dict(record)
            solid = record.get('solid')
            follow = record.get('follow')
            if not is_region and solid is not None and follow is not None:
                if isinstance(follow, Point):
                    new_record['solid'] = affinity.scale(solid, factor, factor, origin=follow)
                else:
                    new_radius = float(tool['size']) * factor / 2
                    new_record['solid'] = follow.buffer(new_radius, circle_quad_segs(_circle_steps_of(gerber, new_radius)))
            new_records.append(new_record)
            if new_record.get('solid') is not None:
                dark.append(new_record['solid'])
        if 'geometry' in tool:
            new_tool['geometry'] = new_records
        new_tools[apid] = new_tool

    # (c) the candidate aggregate from the new dark footprints of ALL the records (the polarity is checked above)
    if not dark and any(not g.is_empty for g in flatten_shapely_geometry(gerber.solid_geometry or [])):
        raise MultiplicativeBufferRejected(_("the object has no aperture records to rebuild its geometry from"))
    candidate = normalize_polygonal(dark)

    return new_tools, candidate, flag


def _grow_size(tool: Dict[str, Any], grow: float) -> None:
    """Add the growth (2 * the buffer distance) to the generic 'size', as the code did before Stage 0C."""
    if tool.get('size') is not None:
        tool['size'] = float(tool['size'] + grow)


def parse_gerber_number(
    strnumber: str,
    int_digits: int,
    frac_digits: int,
    zeros: str
) -> Optional[float]:
    """
    Parse a single number of Gerber coordinates.

    :param strnumber:       String containing a number in decimal digits
                            from a coordinate data block, possibly with a leading sign.
    :type strnumber:        str
    :param int_digits:      Number of digits used for the integer
                            part of the number
    :type frac_digits:      int
    :param frac_digits:     Number of digits used for the fractional
                            part of the number
    :type frac_digits:      int
    :param zeros:           If 'L', leading zeros are removed and trailing zeros are kept. Same situation for 'D' when
                            no zero suppression is done. If 'T', is in reverse.
    :type zeros:            str
    :return:                The number in floating point.
    :rtype:                 float
    """

    ret_val = None

    # a decimal point is not allowed in the coordinates, the number is then the value itself
    if '.' in strnumber:
        return float(strnumber)

    if zeros == 'L' or zeros == 'D':
        ret_val = int(strnumber) * (10 ** (-frac_digits))

    if zeros == 'T':
        int_val = int(strnumber)
        # the sign is not a digit: the missing trailing zeros are counted from the digits only
        digits = len(strnumber.lstrip('+-'))
        ret_val = (int_val * (10 ** ((int_digits + frac_digits) - digits))) * (10 ** (-frac_digits))

        # if strnumber[0] == '-':
        #     int_val = strnumber[:(int_digits+1)]
        #     frac_val = strnumber[(int_digits+1):]
        # else:
        #     int_val = strnumber[:int_digits]
        #     frac_val = strnumber[(int_digits):]
        # ret_val = '%s.%s' % (int_val, frac_val)
        # ret_val = float(ret_val)

    return ret_val


def _circle_steps_of(owner: Any, radius: float, steps_override: Optional[int] = None) -> int:
    """
    The segments of a full circle of the given radius: the count that was given explicitly, the fixed count that the
    Gerber was created with, or what the preferences set (adaptive to the radius, or fixed) in the units of the file.

    :param owner:           the Gerber object (or the editor that borrows create_flash_geometry)
    :param radius:          the radius of the circle
    :param steps_override:  a count of segments given by the caller
    :return:                the segments of the full circle
    """
    if steps_override is not None:
        return int(steps_override)
    fixed = getattr(owner, '_fixed_steps', None)
    if fixed is not None:
        return int(fixed)
    units = getattr(owner, 'units', None) or getattr(owner.app, 'app_units', None) or 'MM'
    return gerber_circle_steps(owner.app.options, radius, units)


def _build_origin_shape(owner: Any, aperture: Dict[str, Any], steps_override: Optional[int]) -> Optional[BaseGeometry]:
    """
    The shape of an aperture centered on the origin, or None for an unknown aperture type. The circles of the shape
    have the segments that _circle_steps_of() gives for their radius: C its radius, O the radius of its short side.
    An aperture macro is made with the segments for its bounding radius (half of the larger side of its bounds),
    which is also enough for the smaller circles inside it.

    :param owner:           the Gerber object
    :param aperture:        the aperture dictionary (type C, R, O, P or AM)
    :param steps_override:  a count of segments given by the caller, or None
    :return:                Shapely geometry
    """
    kind = aperture['type']

    if kind == 'C':
        radius = aperture['size'] / 2
        return Point(0.0, 0.0).buffer(radius, circle_quad_segs(_circle_steps_of(owner, radius, steps_override)))

    if kind == 'R':
        width = aperture['width']
        height = aperture['height']
        return shply_box(-width / 2, -height / 2, width / 2, height / 2).buffer(0.0000001)

    if kind == 'O':
        width = aperture['width']
        height = aperture['height']
        quad_segs = circle_quad_segs(_circle_steps_of(owner, min(width, height) / 2, steps_override))
        if width > height:
            c1 = Point(0.5 * (width - height), 0.0).buffer(height * 0.5, quad_segs)
            c2 = Point(-0.5 * (width - height), 0.0).buffer(height * 0.5, quad_segs)
        else:
            c1 = Point(0.0, 0.5 * (height - width)).buffer(width * 0.5, quad_segs)
            c2 = Point(0.0, -0.5 * (height - width)).buffer(width * 0.5, quad_segs)
        return unary_union([c1, c2]).convex_hull

    if kind == 'P':
        diam = aperture['diam']
        n_vertices = aperture['nVertices']
        points = [(0.5 * diam * np.cos(2 * np.pi * i / n_vertices), 0.5 * diam * np.sin(2 * np.pi * i / n_vertices))
                  for i in range(0, n_vertices)]
        ply = Polygon(points)
        if 'rotation' in aperture:
            ply = affinity.rotate(ply, aperture['rotation'], origin=(0.0, 0.0))
        return ply

    if kind == 'AB':
        return aperture['shape']

    if kind == 'AM':
        macro = aperture['macro']
        modifiers = aperture['modifiers']
        # the first pass with the fewest segments gives the size of the macro
        provisional = steps_override if steps_override is not None else ADAPTIVE_MIN_STEPS
        shape = macro.make_geometry(modifiers, steps_per_circle=provisional)
        if shape is not None and not shape.is_empty:
            minx, miny, maxx, maxy = shape.bounds
            steps = _circle_steps_of(owner, max(maxx - minx, maxy - miny) / 2, steps_override)
            if steps != provisional:
                shape = macro.make_geometry(modifiers, steps_per_circle=steps)
        return shape

    return None


def _cut_hole(owner: Any, shape: BaseGeometry, hole: Tuple[float, ...], steps_override: Optional[int]) -> BaseGeometry:
    """The shape of a standard aperture without its hole: a circle (diameter) or a rectangle (width, height)."""
    if len(hole) == 1:
        radius = hole[0] / 2
        cutter = Point(0.0, 0.0).buffer(radius, circle_quad_segs(_circle_steps_of(owner, radius, steps_override)))
    else:
        cutter = shply_box(-hole[0] / 2, -hole[1] / 2, hole[0] / 2, hole[1] / 2)
    return shape.difference(cutter)


def _flash_shape(owner: Any, aperture: Dict[str, Any], steps_override: Optional[int] = None) -> Optional[BaseGeometry]:
    """
    The origin-centered shape of an aperture. While a file is parsed, the shape is made once per aperture definition
    and kept in ``owner._shape_cache``; a flash is then a translated copy (an aperture macro would be parsed again
    for every flash otherwise).

    :param owner:             the Gerber object (or the editor that borrows create_flash_geometry); owner.app.log
                              gets the warnings
    :param aperture:          the aperture dictionary
    :param steps_override:    a count of segments of a full circle given by the caller, or None
    :return:                  Shapely geometry, None for an unknown type
    """
    cache = getattr(owner, '_shape_cache', None)
    key = None
    if cache is not None:
        kind = aperture['type']
        # the segments depend on the radius, which is part of the key, and on the units of the file
        steps_key = (steps_override, getattr(owner, 'units', None))
        hole = aperture.get('hole')
        if kind == 'C':
            key = (kind, aperture['size'], steps_key, hole)
        elif kind == 'R':
            key = (kind, aperture['width'], aperture['height'], hole)
        elif kind == 'O':
            key = (kind, aperture['width'], aperture['height'], steps_key, hole)
        elif kind == 'P':
            key = (kind, aperture['diam'], aperture['nVertices'], aperture.get('rotation'), hole)
        elif kind == 'AM':
            key = (kind, id(aperture['macro']), tuple(aperture['modifiers']), steps_key)
        if key is not None and key in cache:
            return cache[key][0]

    shape = _build_origin_shape(owner, aperture, steps_override)
    if shape is not None and aperture.get('hole') and aperture['type'] in ('C', 'R', 'O', 'P'):
        shape = _cut_hole(owner, shape, aperture['hole'], steps_override)
    if shape is None:
        owner.app.log.warning("Unknown aperture type: %s" % aperture['type'])
    elif aperture['type'] == 'AM' and shape.is_empty:
        owner.app.log.warning("Empty geometry for Aperture Macro: %s" % str(aperture['macro'].name))

    if key is not None:
        # the macro is kept in the value: its id stays unique while the entry lives
        cache[key] = (shape, aperture.get('macro'))
    return shape


def _translate_shape(shape: BaseGeometry, x: float, y: float) -> BaseGeometry:
    """A copy of the shape moved by (x, y)."""
    if x == 0.0 and y == 0.0:
        return shape
    offset = (x, y)
    return shapely.transform(shape, lambda coords: coords + offset)


def _load_matrix(mirror: str, rotation: float, scale: float) -> Optional[Tuple[float, float, float, float]]:
    """
    The matrix (a, b, d, e) of the load commands LM, LR and LS: the aperture is mirrored (N, X: x -> -x, Y: y -> -y,
    XY: both), then rotated counter-clockwise about its origin, then scaled.

    :return: the matrix, None if it is the identity
    """
    if mirror == 'N' and rotation == 0.0 and scale == 1.0:
        return None
    mirror_x = -1.0 if mirror in ('X', 'XY') else 1.0
    mirror_y = -1.0 if mirror in ('Y', 'XY') else 1.0
    angle = np.radians(rotation)
    cosa, sina = np.cos(angle), np.sin(angle)
    # the exact cases
    if abs(cosa) < 2.5e-16:
        cosa = 0.0
    if abs(sina) < 2.5e-16:
        sina = 0.0
    return (scale * cosa * mirror_x, -scale * sina * mirror_y, scale * sina * mirror_x, scale * cosa * mirror_y)


def _image_matrix(mirror: Tuple[bool, bool], rotation: int,
                  offset: Tuple[float, float]) -> Optional[Tuple[float, float, float, float, float, float]]:
    """
    The matrix (a, b, d, e, xoff, yoff) of the deprecated image commands MI, IR and OF: the image is
    mirrored, rotated counter-clockwise about the origin and offset, in this order. None if there is no change.
    """
    if mirror == (False, False) and not rotation and offset == (0.0, 0.0):
        return None
    sx = -1.0 if mirror[0] else 1.0
    sy = -1.0 if mirror[1] else 1.0
    cosa, sina = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}[rotation]
    return cosa * sx, -sina * sy, sina * sx, cosa * sy, offset[0], offset[1]


def _compose_records(records: list) -> BaseGeometry:
    """
    The image of an aperture block: the objects made in it, in order, a dark one is added and a clear one is cut.

    :param records: (aperture id, geometry dict, clear?) of the objects of the block
    :return: Shapely geometry, centered on the origin of the block
    """
    shape = Polygon()
    batch = []
    batch_clear = False
    for _, geo_dict, is_clear in records + [(None, {}, None)]:
        geo = geo_dict.get('clear') if is_clear else geo_dict.get('solid')
        if is_clear != batch_clear and batch:
            joined = unary_union(batch)
            shape = shape.difference(joined) if batch_clear else shape.union(joined)
            batch = []
        batch_clear = bool(is_clear)
        if geo is not None and not geo.is_empty:
            batch.append(geo)
    return shape


# a coordinate: digits of the number format, or (not allowed by the format, but accepted) a number with a decimal point
_COORD_PATTERN = r'[+-]?(?:\d+\.?\d*|\.\d+)'

_ATTRIBUTE_KEYS = ('TF', 'TA', 'TO', 'TD')
# the first 2 letters of the extended commands that the parser reads from the stream
_EXTENDED_KEYS = frozenset(('SR', 'AB', 'LM', 'LR', 'LS', 'IP', 'OF', 'SF', 'MI', 'IR', 'AS') + _ATTRIBUTE_KEYS)
_SR_RE = re.compile(r'^X(\d+)Y(\d+)I([+-]?[\d.]+)J([+-]?[\d.]+)$')
_AXIS_VALUES_RE = re.compile(r'^(?:A([+-]?[\d.]+))?(?:B([+-]?[\d.]+))?$')
_MI_RE = re.compile(r'^(?:A([01]))?(?:B([01]))?$')
_AD_NAME_RE = re.compile(r'%ADD\d+([^,*]*)', re.I)
_COMMENT_WORD_RE = re.compile(r'G0?4')


def _split_extended_block(block: str, items: List[str]) -> None:
    """
    Add an extended command ``%...%`` to items. An aperture macro stays one item (its data blocks are the
    primitives). Any other block with several data blocks (``%MOMM*FSLAX24Y24*%``) becomes one item for each.
    """
    if block.startswith('%AM') or block.count('*') <= 1:
        items.append(block)
        return
    for part in block[1:-1].split('*'):
        if part:
            items.append('%' + part + '*%')


def split_gerber_commands(text: str) -> List[str]:
    """
    Split the text of a Gerber file in commands, one command for each item: an extended command ``%...%`` or a
    word command that ends with ``*``. A command can be spread over several lines and a line can hold several
    commands. The line breaks and the spaces around the lines are removed.

    :param text:    the content of a Gerber file
    :return:        list of commands, for example ['%ADD10C,1*%', 'D10*', 'X0Y0D03*']
    """
    items: List[str] = []
    buf = ''
    for line in text.splitlines():
        line = line.strip(' \t\r\n')
        if not line:
            continue
        buf += line
        while buf:
            if buf[0] == '%':
                end = buf.find('%', 1)
                if end < 0:
                    break  # the block goes on in the next line
                _split_extended_block(buf[:end + 1], items)
                buf = buf[end + 1:]
                continue
            star = buf.find('*')
            percent = buf.find('%')
            if star >= 0 and (percent < 0 or star < percent):
                items.append(buf[:star + 1])
                buf = buf[star + 1:]
            elif percent > 0:
                items.append(buf[:percent])
                buf = buf[percent:]
            else:
                break
        # a comment without the closing '*' ends with its line
        if buf and buf[0] != '%' and _COMMENT_WORD_RE.match(buf):
            items.append(buf)
            buf = ''
    if buf:
        items.append(buf)
    return items


_INTERPOLATION_WORD_RE = re.compile(r'G0?([123])(?!\d)')
_IJ_WORD_RE = re.compile(r'[IJ][+-]?[\d.]+')


def _repair_polygon(polygon: Polygon) -> List[Polygon]:
    """
    Repair an invalid polygon with make_valid and keep its polygonal parts (a bowtie gives its 2 lobes). Lobes
    that touch in a point are grown by 1e-7 so they join in one valid polygon: a union of polygons that touch in a
    point gives a polygon whose ring touches itself.

    :param polygon: invalid polygon
    :return: list of non-empty Polygons, empty if nothing is left
    """
    try:
        fixed = shapely.make_valid(polygon)
        parts = [geo for geo in flatten_shapely_geometry(fixed) if isinstance(geo, Polygon) and not geo.is_empty]
        if len(parts) > 1:
            parts = [geo for geo in flatten_shapely_geometry(unary_union([part.buffer(0.0000001) for part in parts]))
                     if isinstance(geo, Polygon) and not geo.is_empty]
    except shapely.errors.GEOSException:
        return []
    return parts


def _stroke_geometry(owner: Any, path: List[List[float]], aperture: Dict[str, Any],
                     steps_override: Optional[int] = None, xform: Optional[tuple] = None) -> Optional[BaseGeometry]:
    """
    The solid geometry of a draw (D01 moves) along a path.

    A circle aperture gives the path buffered by the radius. The other apertures (R, O, P and aperture macros)
    are swept: for each segment, the convex hull of the aperture shape at the start and at the end of the segment;
    the hulls are united. For a macro that is not convex the hull is larger than the exact sweep.

    :param owner: the Gerber object
    :param path: list of [x, y] points
    :param aperture: the aperture dictionary
    :param steps_override: a count of segments of a full circle given by the caller, or None
    :param xform: the load transformation (a, b, d, e) of the aperture, None for none
    :return: Shapely geometry, None if the aperture has no shape
    """
    if aperture['type'] == 'AB':
        owner.app.log.warning("An aperture block can not be used to draw, only to flash.")
        return None

    if aperture['type'] not in ('R', 'O', 'P', 'AM'):
        radius = aperture['size'] / 2
        if xform is not None:
            # a circle keeps its shape, only its size changes
            radius *= float(np.hypot(xform[0], xform[2]))
        return LineString(path).buffer(radius, circle_quad_segs(_circle_steps_of(owner, radius, steps_override)))

    shape = _flash_shape(owner, aperture, steps_override)
    if shape is None or shape.is_empty:
        return None
    if xform is not None:
        shape = affinity.affine_transform(shape, (xform[0], xform[1], xform[2], xform[3], 0.0, 0.0))

    shape_points = shapely.get_coordinates(shape)
    points = np.asarray(path, dtype=float)
    hulls = []
    for start, end in zip(points[:-1], points[1:]):
        cloud = np.vstack((shape_points + start, shape_points + end))
        hulls.append(shapely.convex_hull(shapely.multipoints(cloud)))
    return hulls[0] if len(hulls) == 1 else unary_union(hulls)
