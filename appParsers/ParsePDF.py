# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/13/2026                                         #
# MIT Licence                                              #
# ##########################################################

from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional, Callable

from appCommon.Common import GracefulException as grace

from shapely import Polygon, LineString, MultiPolygon
from shapely.affinity import affine_transform
from shapely.ops import polygonize, unary_union

from copy import copy, deepcopy
import numpy as np
from camlib import circle_quad_segs, gerber_quad_segs
import re
import logging

log = logging.getLogger('base')


@dataclass
class ParsingContext:
    """Holds shared state during PDF parsing."""
    object_dict: Dict[int, Any] = field(default_factory=dict)
    layer_nr: int = 1
    apertures_dict: Dict[str, Any] = field(default_factory=dict)
    clear_apertures_dict: Dict[int, Any] = field(default_factory=lambda: {0: {'size': 0.0, 'type': 'C', 'geometry': []}})
    # Color of the layer that is currently being built. Layers are routed at paint time,
    # not when a color operator is seen, so a color change without paint creates no layer.
    layer_color: Optional[Tuple[float, float, float]] = None
    # The layer number of each color, so a color painted again goes back to its layer
    color_layers: Dict[Tuple[float, float, float], int] = field(default_factory=dict)
    # Fill and stroke paint colors have independent polarity. A white fill is a clear fill while
    # the active stroke may still be black (and the other way around).
    stroke_clear: bool = False
    fill_clear: bool = False
    # Track if detection mode allows triggering clear geo
    detection_triggered: bool = False


class PdfParser:

    WHITE_THRESHOLD = 1.0
    INITIAL_APERTURE = 10
    BUFFER_EPSILON = 0.0000001
    DEFAULT_SCALE = [1, 1]
    DEFAULT_OFFSET = [0, 0]

    # Hole detection modes
    DETECT_BOTH = 'both'           # Detect on both stroke and fill color change (default)
    DETECT_FILL_ONLY = 'fill_only' # Detect on fill color change only
    DETECT_STROKE_ONLY = 'stroke_only'  # Detect on stroke color change only

    def __init__(self, units: str, resolution: int, abort: bool | Callable[[], bool],
                 hole_detection_mode: str = 'both', options: dict | None = None) -> None:
        """
        Initialize PDF Parser.

        Args:
            units: 'MM' or 'INCH'
            resolution: Steps per circle for arc approximation (the points of a curve; the round ends of the
                        strokes when there are no options)
            abort: Abort flag for graceful termination; a function that returns the flag of the application is
                   read while the parsing runs (a bool is only the value it had when the parser was made)
            options: the application options: the round ends of the strokes follow the Gerber circle preference
            hole_detection_mode: How to detect excellon holes from PDF
                                 'both' (default) - detect on both stroke and fill color change
                                 'fill_only' - detect on fill color change only
                                 'stroke_only' - detect on stroke color change only
        """
        self.step_per_circles = resolution
        self.options = options
        self.units = units
        self.abort_flag = abort
        self.hole_detection_mode = hole_detection_mode

        # Validate detection mode
        if hole_detection_mode not in [self.DETECT_BOTH, self.DETECT_FILL_ONLY, self.DETECT_STROKE_ONLY]:
            raise ValueError(
                f"Invalid hole_detection_mode: {hole_detection_mode}. "
                f"Must be one of: '{self.DETECT_BOTH}', '{self.DETECT_FILL_ONLY}', '{self.DETECT_STROKE_ONLY}'"
            )

        self.stroke_color_re = re.compile(r'^\s*(\d+\.?\d*) (\d+\.?\d*) (\d+\.?\d*)\s*RG$')
        self.fill_color_re = re.compile(r'^\s*(\d+\.?\d*) (\d+\.?\d*) (\d+\.?\d*)\s*rg$')
        self.rect_re = re.compile(r'^(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s*re$')
        self.start_subpath_re = re.compile(r'^(-?\d+\.?\d*)\s(-?\d+\.?\d*)\sm$')
        self.draw_line_re = re.compile(r'^(-?\d+\.?\d*)\s(-?\d+\.?\d*)\sl')
        self.draw_arc_3pt_re = re.compile(r'^(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)'
                                          r'\s(-?\d+\.?\d*)\s*c$')
        self.draw_arc_2pt_c1start_re = re.compile(r'^(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s*v$')
        self.draw_arc_2pt_c2stop_re = re.compile(r'^(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s(-?\d+\.?\d*)\s*y$')
        self.end_subpath_re = re.compile(r'^h$')
        self.strokewidth_re = re.compile(r'^(\d+\.?\d*)\s*w$')
        self.stroke_path__re = re.compile(r'^S\s?Q?$')
        self.close_stroke_path__re = re.compile(r'^s$')
        self.fill_path_re = re.compile(r'^[f|F][*]?$')
        self.fill_stroke_path_re = re.compile(r'^B[*]?$')
        self.close_fill_stroke_path_re = re.compile(r'^b[*]?$')
        self.no_op_re = re.compile(r'^n$')
        self.combined_transform_re = re.compile(r'^(q)?\s*(-?\d+\.?\d*) (-?\d+\.?\d*) (-?\d+\.?\d*) (-?\d+\.?\d*) '
                                                r'(-?\d+\.?\d*) (-?\d+\.?\d*)\s+cm$')
        self.clip_path_re = re.compile(r'^W[*]? n?$')
        self.save_gs_re = re.compile(r'^q.*?$')
        self.restore_gs_re = re.compile(r'^.*Q.*$')

        self.gs = {'transform': [], 'line_width': []}
        self._geometry_order = 0
        self.unsupported_features = set()

        self.point_to_unit_factor = 0.01388888888

    def _aborted(self) -> bool:
        """True when the user asked to stop (the Stop button sets the abort flag of the application)."""
        return bool(self.abort_flag()) if callable(self.abort_flag) else bool(self.abort_flag)

    def _quad_segs(self, radius: float) -> int:
        """The Shapely quad_segs of a round stroke end of this radius (the Gerber circle preference)."""
        if self.options is None:
            return circle_quad_segs(self.step_per_circles)
        units = 'IN' if str(self.units).upper().startswith('IN') else 'MM'
        return gerber_quad_segs(self.options, radius, units)

    def _find_or_create_aperture(self, apertures_dict: dict, applied_size: float, aperture: int) -> Tuple[str, int]:
        """Find or create an aperture by size.

        Args:
            apertures_dict: Dictionary of apertures
            applied_size: The size to search for
            aperture: Default aperture number to use if creating new

        Returns:
            Tuple of (aperture_id_to_use, aperture_number)
        """
        found_aperture = None
        for apid in apertures_dict:
            if apertures_dict[apid]['size'] == round(applied_size, 5):
                found_aperture = apid
                break

        if found_aperture:
            ap_to_use = found_aperture
        else:
            ap_list = [int(k) for k in apertures_dict.keys()]
            if 0 in ap_list and len(ap_list) == 1:
                ap_list.remove(0)
            if not ap_list:
                aperture = self.INITIAL_APERTURE
            else:
                aperture = max(ap_list) + 1

            ap_to_use = str(aperture)
            apertures_dict[ap_to_use] = {
                'size': round(applied_size, 5),
                'type': 'C',
                'geometry': []
            }

        return ap_to_use, aperture

    def _store_geometry(self, apertures_dict: dict, path_geo: list, aperture_id: str, clear_geo: bool = False) -> None:
        """Store geometry in the aperture dictionary.

        Args:
            apertures_dict: Dictionary of apertures
            path_geo: List of geometries to store
            aperture_id: The aperture ID to store geometry in
            clear_geo: If True, store as clear geometry; otherwise store as solid with follow
        """
        for pdf_geo in path_geo:
            if isinstance(pdf_geo, MultiPolygon):
                for poly in pdf_geo.geoms:
                    if clear_geo:
                        new_el = {'clear': poly, 'order': self._geometry_order}
                    else:
                        new_el = {'solid': poly, 'follow': poly.exterior, 'order': self._geometry_order}
                    self._geometry_order += 1
                    apertures_dict[aperture_id]['geometry'].append(deepcopy(new_el))
            elif isinstance(pdf_geo, Polygon):
                if clear_geo:
                    new_el = {'clear': pdf_geo, 'order': self._geometry_order}
                else:
                    new_el = {'solid': pdf_geo, 'follow': pdf_geo.exterior, 'order': self._geometry_order}
                self._geometry_order += 1
                apertures_dict[aperture_id]['geometry'].append(deepcopy(new_el))
            else:
                if clear_geo:
                    new_el = {'clear': pdf_geo, 'order': self._geometry_order}
                else:
                    new_el = {'solid': pdf_geo, 'follow': pdf_geo, 'order': self._geometry_order}
                self._geometry_order += 1
                apertures_dict[aperture_id]['geometry'].append(deepcopy(new_el))

    @staticmethod
    def _has_solid_geometry(layer: dict) -> bool:
        return any('solid' in element for aperture in layer.values()
                   for element in aperture.get('geometry', []))

    def _route_dark_paint(self, ctx: ParsingContext, color) -> None:
        """Route a dark paint to the layer of its color; start a new layer for a new color.

        A color painted again goes back to its first layer, so alternating colors do not
        make duplicate layers. The painter order stays correct: the clears are stored in
        every layer at paint time, and each element keeps its global 'order'.
        """
        color = tuple(color)
        if ctx.apertures_dict and ctx.layer_color is not None and ctx.layer_color != color:
            # the layer dict itself goes in object_dict, so that later paint of this
            # color and later clears go to the same object
            ctx.object_dict[ctx.layer_nr] = ctx.apertures_dict
            ctx.layer_nr = ctx.color_layers.get(color, max(ctx.object_dict) + 1)
            ctx.apertures_dict = ctx.object_dict.setdefault(ctx.layer_nr, {})
            ctx.layer_color = None
        if ctx.layer_color is None:
            ctx.layer_color = color
            ctx.color_layers.setdefault(color, ctx.layer_nr)

    def _route_clear_paint(self, ctx: ParsingContext, geometries: list) -> None:
        """Apply white paint to the colored layers that exist below it, at paint time.

        White is an eraser, not a color layer. The clear records are appended to every
        existing layer that holds solids, in painter order, so a later dark paint of the
        same color can restore an island on top of the clear.
        """
        layers = []
        if ctx.apertures_dict and self._has_solid_geometry(ctx.apertures_dict):
            layers.append(ctx.apertures_dict)
        for layer_nr, layer in ctx.object_dict.items():
            if layer_nr != 0 and layer is not ctx.apertures_dict and self._has_solid_geometry(layer):
                layers.append(layer)

        for layer in layers:
            if '0' not in layer:
                layer['0'] = {'size': 0.0, 'type': 'C', 'geometry': []}
            self._store_geometry(layer, geometries, '0', clear_geo=True)

    def parse_pdf(self, pdf_content):

        if self.units.upper() == 'MM':
            self.point_to_unit_factor = 25.4 / 72
        else:
            self.point_to_unit_factor = 1 / 72

        ctx = ParsingContext()
        ctx.object_dict[ctx.layer_nr] = {}

        # PDF graphics state. The CTM is the full affine matrix [a b c d e f], not just
        # a scale/offset pair, so rotations and one-axis scales are preserved.
        state = {
            'ctm': (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            'line_width': 0.0,
            'stroke_color': (0.0, 0.0, 0.0),
            'fill_color': (0.0, 0.0, 0.0),
            'stroke_clear': False,
            'fill_clear': False,
        }
        state_stack = []

        # A PDF path is a list of subpaths. Each subpath keeps its own CTM because a path
        # may be built under one q/Q state and painted under another.
        path = []
        current = None
        current_point = None
        operands = []
        aperture = self.INITIAL_APERTURE
        self._geometry_order = 0
        self.unsupported_features = set()

        for token in self._tokenize_pdf(pdf_content):
            if self._aborted():
                raise grace

            if isinstance(token, float):
                operands.append(token)
                continue

            op = token

            if op == 'q':
                state_stack.append(deepcopy(state))
            elif op == 'Q':
                if state_stack:
                    state = state_stack.pop()
            elif op == 'cm':
                if len(operands) >= 6:
                    state['ctm'] = self._multiply_ctm(tuple(operands[-6:]), state['ctm'])
            elif op == 'w':
                if operands:
                    state['line_width'] = operands[-1]
            elif op == 'RG':
                if len(operands) >= 3:
                    color = tuple(operands[-3:])
                    ctx = self._handle_stroke_color_change(list(color), 0, ctx)
                    state['stroke_color'] = color
                    state['stroke_clear'] = ctx.stroke_clear
            elif op == 'rg':
                if len(operands) >= 3:
                    color = tuple(operands[-3:])
                    ctx = self._handle_fill_color_change(list(color), ctx, 0)
                    state['fill_color'] = color
                    state['fill_clear'] = ctx.fill_clear
            elif op == 'G':
                if operands:
                    gray = operands[-1]
                    color = (gray, gray, gray)
                    ctx = self._handle_stroke_color_change(list(color), 0, ctx)
                    state['stroke_color'] = color
                    state['stroke_clear'] = ctx.stroke_clear
            elif op == 'g':
                if operands:
                    gray = operands[-1]
                    color = (gray, gray, gray)
                    ctx = self._handle_fill_color_change(list(color), ctx, 0)
                    state['fill_color'] = color
                    state['fill_clear'] = ctx.fill_clear
            elif op == 'K':
                if len(operands) >= 4:
                    color = self._cmyk_to_rgb(operands[-4:])
                    ctx = self._handle_stroke_color_change(list(color), 0, ctx)
                    state['stroke_color'] = color
                    state['stroke_clear'] = ctx.stroke_clear
            elif op == 'k':
                if len(operands) >= 4:
                    color = self._cmyk_to_rgb(operands[-4:])
                    ctx = self._handle_fill_color_change(list(color), ctx, 0)
                    state['fill_color'] = color
                    state['fill_clear'] = ctx.fill_clear
            elif op == 'm':
                if len(operands) >= 2:
                    point = (operands[-2], operands[-1])
                    if current is not None:
                        path.append(current)
                    current = {'points': [point], 'ctm': state['ctm'], 'closed': False}
                    current_point = point
            elif op == 'l':
                if len(operands) >= 2:
                    point = (operands[-2], operands[-1])
                    if current is None:
                        current = {'points': [current_point or point], 'ctm': state['ctm'], 'closed': False}
                    current['points'].append(point)
                    current_point = point
            elif op == 'c':
                if len(operands) >= 6:
                    c1 = (operands[-6], operands[-5])
                    c2 = (operands[-4], operands[-3])
                    stop = (operands[-2], operands[-1])
                    start = current_point or c1
                    if current is None:
                        current = {'points': [start], 'ctm': state['ctm'], 'closed': False}
                    points = self.bezier_to_points(start, c1, c2, stop)
                    current['points'].extend(points[1:])
                    current['points'].append(stop)
                    current_point = stop
            elif op == 'v':
                if len(operands) >= 4:
                    c2 = (operands[-4], operands[-3])
                    stop = (operands[-2], operands[-1])
                    start = current_point or c2
                    if current is None:
                        current = {'points': [start], 'ctm': state['ctm'], 'closed': False}
                    points = self.bezier_to_points(start, start, c2, stop)
                    current['points'].extend(points[1:])
                    current['points'].append(stop)
                    current_point = stop
            elif op == 'y':
                if len(operands) >= 4:
                    c1 = (operands[-4], operands[-3])
                    stop = (operands[-2], operands[-1])
                    start = current_point or c1
                    if current is None:
                        current = {'points': [start], 'ctm': state['ctm'], 'closed': False}
                    points = self.bezier_to_points(start, c1, stop, stop)
                    current['points'].extend(points[1:])
                    current['points'].append(stop)
                    current_point = stop
            elif op == 're':
                if len(operands) >= 4:
                    x, y, width, height = operands[-4:]
                    points = [(x, y), (x + width, y), (x + width, y + height), (x, y + height), (x, y)]
                    if current is not None and current['points']:
                        path.append(current)
                    path.append({'points': points, 'ctm': state['ctm'], 'closed': True})
                    current = None
                    current_point = (x, y)
            elif op == 'h':
                if current is not None:
                    current['closed'] = True
                    path.append(current)
                    current_point = current['points'][0] if current['points'] else current_point
                    current = None
            elif op in ('S', 's', 'f', 'F', 'f*', 'F*', 'B', 'B*', 'b', 'b*'):
                subpaths = list(path)
                if current is not None and current['points']:
                    subpaths.append(current)
                path = []
                current = None

                if op in ('s', 'b', 'b*'):
                    for subpath in subpaths:
                        self._close_subpath_points(subpath)

                even_odd = op in ('f*', 'F*', 'B*', 'b*')
                if op in ('S', 's'):
                    aperture = self._stroke_path(
                        subpaths, state['line_width'], state['stroke_clear'],
                        state['stroke_color'], ctx, aperture
                    )
                elif op in ('f', 'F', 'f*', 'F*'):
                    self._fill_path(subpaths, even_odd, state['fill_clear'],
                                    state['fill_color'], ctx)
                else:
                    aperture = self._fill_stroke_path(
                        subpaths, even_odd, state['fill_clear'], state['stroke_clear'],
                        state['line_width'], state['fill_color'], state['stroke_color'],
                        ctx, aperture
                    )
            elif op in ('W', 'W*'):
                # Clipping is not represented by FlatCAM geometry. Report it instead of
                # silently pretending the clipped artwork was imported correctly.
                if 'clipping' not in self.unsupported_features:
                    log.warning("parse_pdf() --> clipping path (W/W*) is not supported; clip is ignored.")
                self.unsupported_features.add('clipping')
            elif op == 'n':
                path = []
                current = None
            elif op == 'Do':
                if 'XObject' not in self.unsupported_features:
                    log.warning("parse_pdf() --> Form/Image XObject (Do) is not supported; content is skipped.")
                self.unsupported_features.add('XObject')
            elif op == 'BT':
                # The stream parsers convert text to glyph outlines; a text object that is
                # left is text in a font they could not read (not embedded, Type1, CFF).
                if 'text' not in self.unsupported_features:
                    log.warning("parse_pdf() --> text in a font that is not embedded or not TrueType is skipped.")
                self.unsupported_features.add('text')
            elif op == 'BI':
                if 'inline image' not in self.unsupported_features:
                    log.warning("parse_pdf() --> inline image (BI) is not supported; content is skipped.")
                self.unsupported_features.add('inline image')

            operands = []

        return self._finalize_object_dict(
            ctx.object_dict, ctx.apertures_dict, ctx.clear_apertures_dict,
            ctx.layer_nr, self._aborted()
        )

    def _tokenize_pdf(self, content: str):
        """Yield PDF numbers as floats and operators/names as strings.

        Strings, arrays and dictionaries are skipped as opaque operands. This is what
        prevents text content such as ``(0 0 m) Tj`` from being interpreted as a path.
        """
        index = 0
        length = len(content)
        whitespace = ' \t\r\n\f\x00'

        while index < length:
            char = content[index]

            if char in whitespace:
                index += 1
                continue

            if char == '%':
                newline = content.find('\n', index)
                index = length if newline < 0 else newline + 1
                continue

            if char == '(':
                index = self._skip_literal_string(content, index)
                continue

            if char == '<':
                if content.startswith('<<', index):
                    index = self._skip_dict(content, index)
                else:
                    end = content.find('>', index + 1)
                    index = length if end < 0 else end + 1
                continue

            if char == '[':
                index = self._skip_array(content, index)
                continue

            if char in ']{}>)':
                # a stray delimiter; an empty token below would never advance the index
                index += 1
                continue

            if char == '/':
                end = index + 1
                while end < length and content[end] not in whitespace + '()<>[]{}/%':
                    end += 1
                index = end
                continue

            end = index
            while end < length and content[end] not in whitespace + '()<>[]{}/%':
                end += 1
            token = content[index:end]
            index = end

            try:
                yield float(token)
            except ValueError:
                yield token
                if token == 'BI':
                    # Inline image data is binary and must not be tokenized as path operators.
                    image_end = re.search(r'(?<!\S)EI(?!\S)', content[end:])
                    index = end + image_end.end() if image_end else length

    def _skip_literal_string(self, content: str, index: int) -> int:
        depth = 1
        index += 1
        while index < len(content):
            char = content[index]
            if char == '\\':
                index += 2
                continue
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return index

    def _skip_dict(self, content: str, index: int) -> int:
        depth = 0
        while index < len(content):
            if content.startswith('<<', index):
                depth += 1
                index += 2
                continue
            if content.startswith('>>', index):
                depth -= 1
                index += 2
                if depth == 0:
                    return index
                continue
            if content[index] == '(':
                index = self._skip_literal_string(content, index)
                continue
            index += 1
        return index

    def _skip_array(self, content: str, index: int) -> int:
        depth = 0
        while index < len(content):
            if content[index] == '[':
                depth += 1
            elif content[index] == ']':
                depth -= 1
                if depth == 0:
                    return index + 1
            elif content[index] == '(':
                index = self._skip_literal_string(content, index)
                continue
            index += 1
        return index

    @staticmethod
    def _cmyk_to_rgb(cmyk) -> Tuple[float, float, float]:
        c, m, y, k = cmyk
        return (1.0 - c) * (1.0 - k), (1.0 - m) * (1.0 - k), (1.0 - y) * (1.0 - k)

    @staticmethod
    def _multiply_ctm(first, second) -> Tuple[float, float, float, float, float, float]:
        """Return the row-vector product first x second for PDF [a b c d e f] matrices."""
        a1, b1, c1, d1, e1, f1 = first
        a2, b2, c2, d2, e2, f2 = second
        return (
            a1 * a2 + b1 * c2,
            a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2,
            c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2,
            e1 * b2 + f1 * d2 + f2,
        )

    def _transform_point(self, point: Tuple[float, float], ctm) -> Tuple[float, float]:
        x, y = point
        a, b, c, d, e, f = ctm
        return (
            (a * x + c * y + e) * self.point_to_unit_factor,
            (b * x + d * y + f) * self.point_to_unit_factor,
        )

    def _transform_points(self, points: List[Tuple[float, float]], ctm) -> List[Tuple[float, float]]:
        return [self._transform_point(point, ctm) for point in points]

    def _shapely_ctm(self, ctm) -> List[float]:
        a, b, c, d, e, f = ctm
        factor = self.point_to_unit_factor
        # shapely.affinity order is [a, b, d, e, xoff, yoff] for x' = a*x + b*y + xoff,
        # y' = d*x + e*y + yoff.
        return [a * factor, c * factor, b * factor, d * factor, e * factor, f * factor]

    @staticmethod
    def _close_subpath_points(subpath: dict) -> None:
        points = subpath['points']
        if len(points) > 1 and points[0] != points[-1]:
            points.append(points[0])
        subpath['closed'] = True

    @staticmethod
    def _winding_number(point, ring_coords) -> int:
        """Winding number of a point against one closed ring."""
        x, y = point
        winding = 0
        count = len(ring_coords)
        for index in range(count):
            x1, y1 = ring_coords[index]
            x2, y2 = ring_coords[(index + 1) % count]
            if y1 <= y:
                if y2 > y and (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1) > 0:
                    winding += 1
            else:
                if y2 <= y and (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1) < 0:
                    winding -= 1
        return winding

    def _build_fill_geometry(self, subpaths: List[dict], even_odd: bool):
        """Build one filled geometry from all subpaths using the PDF winding rule."""
        rings = []
        for subpath in subpaths:
            points = self._transform_points(subpath['points'], subpath['ctm'])
            if len(points) < 3:
                continue
            if points[0] != points[-1]:
                points.append(points[0])
            try:
                ring = LineString(points)
                if not ring.is_empty:
                    rings.append(ring)
            except (ValueError, TypeError):
                continue

        if not rings:
            return Polygon()

        try:
            faces = list(polygonize(unary_union(rings)))
        except Exception:
            faces = []

        if not faces:
            # Degenerate/self-intersecting linework: fall back to the per-contour rule.
            polygons = []
            for ring in rings:
                if len(ring.coords) < 4:
                    continue
                try:
                    polygon = Polygon(ring)
                    if not polygon.is_empty:
                        polygons.append(polygon)
                except ValueError:
                    continue
            if not polygons:
                return Polygon()
            result = polygons[0]
            for polygon in polygons[1:]:
                result = result.symmetric_difference(polygon) if even_odd else result.union(polygon)
            return result

        # Read the ring coordinates once: shapely coordinate access point by point was
        # most of the parse time of text-heavy pages (one glyph path = many faces x rings).
        ring_data = [(ring.bounds, list(ring.coords)) for ring in rings]
        selected = []
        for face in faces:
            point = face.representative_point()
            x, y = point.x, point.y
            winding = 0
            for (min_x, min_y, max_x, max_y), coords in ring_data:
                # a ring above, below or left of the point has no crossing to its right
                if y < min_y or y > max_y or x > max_x:
                    continue
                winding += self._winding_number((x, y), coords)
            if (winding % 2 != 0) if even_odd else (winding != 0):
                selected.append(face)

        return unary_union(selected) if selected else Polygon()

    def _build_stroke_geometry(self, subpaths: List[dict], line_width: float) -> List:
        """Build stroke polygons in user space, then apply each subpath's CTM."""
        geometries = []
        half_width = float(line_width) / 2.0
        if half_width <= 0:
            return geometries

        for subpath in subpaths:
            points = list(subpath['points'])
            if len(points) < 2:
                continue
            if subpath.get('closed') and points[0] != points[-1]:
                points.append(points[0])
            try:
                geometry = LineString(points).buffer(half_width, quad_segs=self._quad_segs(half_width))
                if geometry.is_empty:
                    continue
                geometries.append(affine_transform(geometry, self._shapely_ctm(subpath['ctm'])))
            except (ValueError, TypeError):
                continue

        return geometries

    def _aperture_size(self, line_width: float, ctm) -> float:
        a, b, c, d, _, _ = ctm
        determinant = abs(a * d - b * c)
        return float(line_width) * (determinant ** 0.5) * self.point_to_unit_factor

    def _store_stroke_geometry(self, geometries: List, clear_geo: bool, ctx: ParsingContext,
                               aperture: int, applied_size: float, color=None) -> int:
        if not geometries:
            return aperture

        if clear_geo:
            self._route_clear_paint(ctx, geometries)
            return aperture

        self._route_dark_paint(ctx, color if color is not None else (0.0, 0.0, 0.0))

        if not ctx.apertures_dict:
            ctx.apertures_dict[str(aperture)] = {
                'size': round(applied_size, 5),
                'type': 'C',
                'geometry': []
            }

        ap_to_use, aperture = self._find_or_create_aperture(
            ctx.apertures_dict, applied_size, aperture
        )
        self._store_geometry(ctx.apertures_dict, geometries, ap_to_use, clear_geo=clear_geo)
        return aperture

    def _store_fill_geometry(self, geometry, clear_geo: bool, ctx: ParsingContext,
                             applied_size: float, color=None) -> None:
        if geometry.is_empty:
            return

        if clear_geo:
            polygons = geometry.geoms if isinstance(geometry, MultiPolygon) else [geometry]
            for polygon in polygons:
                ctx.clear_apertures_dict[0]['geometry'].append({
                    'clear': deepcopy(polygon),
                    'order': self._geometry_order
                })
                self._geometry_order += 1
            self._route_clear_paint(ctx, [geometry])
            return

        self._route_dark_paint(ctx, color if color is not None else (0.0, 0.0, 0.0))

        if '0' not in ctx.apertures_dict:
            ctx.apertures_dict['0'] = {
                'size': round(applied_size, 5),
                'type': 'C',
                'geometry': []
            }
        self._store_geometry(ctx.apertures_dict, [geometry], '0', clear_geo=clear_geo)

    def _handle_combined_transform(self, match, line_nr: int,
                                    offset_geo: List[float], scale_geo: List[float],
                                    size: float) -> bool:
        """Handle combined transformation. Returns True if transformation detected.

        Args:
            match: Regex match object
            line_nr: Current line number for logging
            offset_geo: [offset_x, offset_y] to mutate
            scale_geo: [scale_x, scale_y] to mutate
            size: Current line width

        Returns:
            True if transformation was applied
        """
        transformed = False

        if match.group(1) == 'q':
            log.debug(
                "parse_pdf() --> Save to GS found on line: %s --> offset=[%f, %f] ||| scale=[%f, %f]" %
                (line_nr, offset_geo[0], offset_geo[1], scale_geo[0], scale_geo[1]))

            self.gs['transform'].append(deepcopy([offset_geo, scale_geo]))
            self.gs['line_width'].append(deepcopy(size))

        if (float(match.group(3)) == 0 and float(match.group(4)) == 0) and \
                (float(match.group(6)) != 0 or float(match.group(7)) != 0):
            log.debug(
                "parse_pdf() --> OFFSET transformation found on line: %s" % line_nr)

            offset_geo[0] += float(match.group(6))
            offset_geo[1] += float(match.group(7))
            transformed = True

        if float(match.group(2)) != 1 and float(match.group(5)) != 1:
            log.debug(
                "parse_pdf() --> SCALE transformation found on line: %s" % line_nr)

            scale_geo[0] *= float(match.group(2))
            scale_geo[1] *= float(match.group(5))
            transformed = True

        return transformed

    def _save_graphic_state(self, offset_geo: List[float], scale_geo: List[float],
                            size: float, line_nr: int) -> None:
        """Save current transform and line width to graphic state stack.

        Args:
            offset_geo: [offset_x, offset_y]
            scale_geo: [scale_x, scale_y]
            size: Current line width
            line_nr: Current line number for logging
        """
        log.debug(
            "parse_pdf() --> Save to GS found on line: %s --> offset=[%f, %f] ||| scale=[%f, %f]" %
            (line_nr, offset_geo[0], offset_geo[1], scale_geo[0], scale_geo[1]))
        self.gs['transform'].append(deepcopy([offset_geo, scale_geo]))
        self.gs['line_width'].append(deepcopy(size))

    def _restore_graphic_state(self) -> Tuple[List[float], List[float], float]:
        """Restore transform and line width from graphic state stack.

        Returns:
            Tuple of (offset_geo, scale_geo, size)
        """
        offset_geo: List[float] = [float(value) for value in self.DEFAULT_OFFSET]
        scale_geo: List[float] = [float(value) for value in self.DEFAULT_SCALE]
        size = 0

        try:
            restored_transform = self.gs['transform'].pop(-1)
            offset_geo = restored_transform[0]
            scale_geo = restored_transform[1]
        except IndexError:
            pass

        try:
            size = self.gs['line_width'].pop(-1)
        except IndexError:
            pass

        return offset_geo, scale_geo, size

    def _handle_stroke_color_change(self, color: List[float], line_nr: int,
                                     ctx: ParsingContext) -> ParsingContext:
        """Handle stroke color change. Returns updated context.

        Args:
            color: [r, g, b] color values
            line_nr: Current line number for logging
            ctx: ParsingContext to update

        Returns:
            Updated ParsingContext
        """
        log.debug(
            "parse_pdf() --> STROKE Color change on line: %s --> RED=%f GREEN=%f BLUE=%f" %
            (line_nr, color[0], color[1], color[2]))

        # Recompute polarity from the color every time. The graphics state stack already
        # restores the color through q/Q, so a cached "last color" can only go stale.
        if self.hole_detection_mode in [self.DETECT_BOTH, self.DETECT_STROKE_ONLY]:
            ctx.detection_triggered = True
            ctx.stroke_clear = self._is_white(color)
        else:
            ctx.detection_triggered = False
            ctx.stroke_clear = False

        return ctx

    def _is_white(self, color: List[float]) -> bool:
        return (color[0] == self.WHITE_THRESHOLD and
                color[1] == self.WHITE_THRESHOLD and
                color[2] == self.WHITE_THRESHOLD)

    def _apply_transform_to_point(self, x: float, y: float,
                                    offset_geo: List[float], scale_geo: List[float]) -> Tuple[float, float]:
        """Apply offset and scale transformation to a point.

        Args:
            x: Raw x coordinate
            y: Raw y coordinate
            offset_geo: [offset_x, offset_y]
            scale_geo: [scale_x, scale_y]

        Returns:
            Tuple of transformed (x, y) coordinates
        """
        x_transformed = (x + offset_geo[0]) * self.point_to_unit_factor * scale_geo[0]
        y_transformed = (y + offset_geo[1]) * self.point_to_unit_factor * scale_geo[1]
        return x_transformed, y_transformed

    def _start_subpath(self, match, offset_geo: List[float], scale_geo: List[float],
                       subpath: dict) -> Tuple[dict, Tuple]:
        """Handle 'm' command - Start a new subpath.

        Args:
            match: Regex match object
            offset_geo: [offset_x, offset_y]
            scale_geo: [scale_x, scale_y]
            subpath: Current subpath dict

        Returns:
            Tuple of (subpath, start_point)
        """
        subpath['lines'] = []
        subpath['bezier'] = []
        subpath['rectangle'] = []

        x = float(match.group(1))
        y = float(match.group(2))
        start_point = self._apply_transform_to_point(x, y, offset_geo, scale_geo)

        subpath['lines'].append(start_point)
        return subpath, start_point

    def _draw_line(self, match, offset_geo: List[float], scale_geo: List[float],
                   subpath: dict) -> Tuple[dict, Tuple, str]:
        """Handle 'l' command - Draw a line.

        Args:
            match: Regex match object
            offset_geo: [offset_x, offset_y]
            scale_geo: [scale_x, scale_y]
            subpath: Current subpath dict

        Returns:
            Tuple of (subpath, current_point, 'lines')
        """
        x = float(match.group(1))
        y = float(match.group(2))
        current_point = self._apply_transform_to_point(x, y, offset_geo, scale_geo)
        subpath['lines'].append(current_point)
        return subpath, current_point, 'lines'

    def _draw_bezier_c(self, match, offset_geo: List[float], scale_geo: List[float],
                       subpath: dict, current_point) -> Tuple[dict, Tuple]:
        """Handle 'c' command - Draw cubic Bezier's curve (3 control points).

        Args:
            match: Regex match object
            offset_geo: [offset_x, offset_y]
            scale_geo: [scale_x, scale_y]
            subpath: Current subpath dict
            current_point: Current point (will be updated)

        Returns:
            Tuple of (subpath, current_point)
        """
        start = current_point

        x = float(match.group(1))
        y = float(match.group(2))
        c1 = self._apply_transform_to_point(x, y, offset_geo, scale_geo)

        x = float(match.group(3))
        y = float(match.group(4))
        c2 = self._apply_transform_to_point(x, y, offset_geo, scale_geo)

        x = float(match.group(5))
        y = float(match.group(6))
        stop = self._apply_transform_to_point(x, y, offset_geo, scale_geo)

        subpath['bezier'].append([start, c1, c2, stop])
        current_point = stop
        return subpath, current_point

    def _draw_bezier_v(self, match, offset_geo: List[float], scale_geo: List[float],
                       subpath: dict, current_point) -> Tuple[dict, Tuple]:
        """Handle 'v' command - Draw cubic Bezier's curve with first control point = start.

        Args:
            match: Regex match object
            offset_geo: [offset_x, offset_y]
            scale_geo: [scale_x, scale_y]
            subpath: Current subpath dict
            current_point: Current point (will be updated)

        Returns:
            Tuple of (subpath, current_point)
        """
        start = current_point

        x = float(match.group(1))
        y = float(match.group(2))
        c2 = self._apply_transform_to_point(x, y, offset_geo, scale_geo)

        x = float(match.group(3))
        y = float(match.group(4))
        stop = self._apply_transform_to_point(x, y, offset_geo, scale_geo)

        subpath['bezier'].append([start, start, c2, stop])
        current_point = stop
        return subpath, current_point

    def _draw_bezier_y(self, match, offset_geo: List[float], scale_geo: List[float],
                       subpath: dict, current_point) -> Tuple[dict, Tuple]:
        """Handle 'y' command - Draw cubic Bezier's curve with second control point = stop.

        Args:
            match: Regex match object
            offset_geo: [offset_x, offset_y]
            scale_geo: [scale_x, scale_y]
            subpath: Current subpath dict
            current_point: Current point (will be updated)

        Returns:
            Tuple of (subpath, current_point)
        """
        start = current_point

        x = float(match.group(1))
        y = float(match.group(2))
        c1 = self._apply_transform_to_point(x, y, offset_geo, scale_geo)

        x = float(match.group(3))
        y = float(match.group(4))
        stop = self._apply_transform_to_point(x, y, offset_geo, scale_geo)

        subpath['bezier'].append([start, c1, stop, stop])
        current_point = stop
        return subpath, current_point

    def _draw_rectangle(self, match, offset_geo: List[float], scale_geo: List[float],
                        subpath: dict) -> Tuple[dict, Tuple]:
        """Handle 're' command - Draw rectangle.

        Args:
            match: Regex match object
            offset_geo: [offset_x, offset_y]
            scale_geo: [scale_x, scale_y]
            subpath: Current subpath dict

        Returns:
            Tuple of (subpath, current_point)
        """
        x = (float(match.group(1)) + offset_geo[0]) * self.point_to_unit_factor * scale_geo[0]
        y = (float(match.group(2)) + offset_geo[1]) * self.point_to_unit_factor * scale_geo[1]
        width = (float(match.group(3)) + offset_geo[0]) * self.point_to_unit_factor * scale_geo[0]
        height = (float(match.group(4)) + offset_geo[1]) * self.point_to_unit_factor * scale_geo[1]
        pt1 = (x, y)
        pt2 = (x + width, y)
        pt3 = (x + width, y + height)
        pt4 = (x, y + height)
        subpath['rectangle'] += [pt1, pt2, pt3, pt4, pt1]
        current_point = pt1
        return subpath, current_point

    def _close_subpath(self, subpath: dict, path: dict, current_subpath: str,
                       start_point: Tuple) -> Tuple[dict, bool]:
        """Handle 'h' command - Close current subpath.

        Args:
            subpath: Current subpath dict
            path: Path dict with lines/bezier/rectangle lists
            current_subpath: Current subpath type ('lines', 'bezier', 'rectangle')
            start_point: Start point of subpath

        Returns:
            Tuple of (subpath, close_subpath)
        """
        close_subpath = True
        if current_subpath == 'lines':
            subpath['lines'].append(start_point)
            path['lines'].append(copy(subpath['lines']))
            subpath['lines'] = []
        elif current_subpath == 'bezier':
            path['bezier'].append(copy(subpath['bezier']))
            subpath['bezier'] = []
        elif current_subpath == 'rectangle':
            path['rectangle'].append(copy(subpath['rectangle']))
            subpath['rectangle'] = []
        return subpath, close_subpath

    def _handle_clip_path(self, subpath: dict, path: dict, current_subpath: str,
                          close_subpath: bool) -> Tuple[dict, bool]:
        """Handle 'W' command - Handle clipping path (clear current subpath).

        Args:
            subpath: Current subpath dict
            path: Path dict with lines/bezier/rectangle lists
            current_subpath: Current subpath type
            close_subpath: Close flag (will be updated)

        Returns:
            Tuple of (subpath, close_subpath)
        """
        subpath['lines'] = []
        subpath['bezier'] = []
        subpath['rectangle'] = []
        if close_subpath is True:
            close_subpath = False
            if current_subpath == 'lines':
                path['lines'].pop(-1)
            if current_subpath == 'rectangle':
                path['rectangle'].pop(-1)
        return subpath, close_subpath

    def _handle_fill_color_change(self, fill_color: List[float], ctx: ParsingContext, line_nr: int) -> ParsingContext:
        """Handle fill color change. Returns updated context.

        Args:
            fill_color: [r, g, b] color values
            ctx: ParsingContext to update
            line_nr: Current line number for logging

        Returns:
            Updated ParsingContext
        """
        log.debug(
            "parse_pdf() --> FILL Color change on line: %s --> RED=%f GREEN=%f BLUE=%f" %
            (line_nr, fill_color[0], fill_color[1], fill_color[2]))

        # Fill polarity is independent from stroke polarity. A stroke color change
        # must not clear the active white fill state.
        if self.hole_detection_mode in [self.DETECT_BOTH, self.DETECT_FILL_ONLY]:
            ctx.detection_triggered = True
            ctx.fill_clear = self._is_white(fill_color)
        else:
            ctx.detection_triggered = False
            ctx.fill_clear = False
        return ctx

    def bezier_to_points(self, start, c1, c2, stop):
        """Convert Bezier curve to points.

        Args:
            start: Start point (x, y)
            c1: First control point (x, y)
            c2: Second control point (x, y)
            stop: End point (x, y)

        Returns:
            List of point coordinates tuples (x, y)
        """

        nr_points = np.linspace(0.0, 1.0, self.step_per_circles, endpoint=False)
        t = nr_points
        term_p0 = (1 - t) ** 3
        term_p1 = 3 * t * (1 - t) ** 2
        term_p2 = 3 * (1 - t) * t ** 2
        term_p3 = t ** 3
        x = start[0] * term_p0 + c1[0] * term_p1 + c2[0] * term_p2 + stop[0] * term_p3
        y = start[1] * term_p0 + c1[1] * term_p1 + c2[1] * term_p2 + stop[1] * term_p3
        points = np.column_stack((x, y))
        return points.tolist()

    def _apply_line_buffer_to_subpaths(self, subpaths: List, applied_size: float,
                                        subpath_type: str) -> List:
        """Apply line buffer to subpaths for stroke operations.

        Args:
            subpaths: List of subpaths (either from path dict or current subpath)
            applied_size: The stroke size (already scaled)
            subpath_type: Type of subpath ('lines', 'bezier', 'rectangle')

        Returns:
            List of buffered geometries
        """
        path_geo = []

        if subpath_type == 'lines' or subpath_type == 'rectangle':
            for subp in subpaths:
                geo = copy(subp)
                try:
                    geo = LineString(geo).buffer(
                        (float(applied_size) / 2),
                        quad_segs=self._quad_segs(float(applied_size) / 2)
                    )
                    path_geo.append(geo)
                except ValueError:
                    pass

        elif subpath_type == 'bezier':
            for subp in subpaths:
                geo = []
                for b in subp:
                    geo += self.bezier_to_points(start=b[0], c1=b[1], c2=b[2], stop=b[3])
                try:
                    geo = LineString(geo).buffer(
                        (float(applied_size) / 2),
                        quad_segs=self._quad_segs(float(applied_size) / 2)
                    )
                    path_geo.append(geo)
                except ValueError:
                    pass

        return path_geo

    def _apply_polygon_buffer_to_subpaths(self, subpaths: List, subpath_type: str,
                                          close_subpath: bool) -> List:
        """Apply polygon buffer to subpaths for fill operations.

        Args:
            subpaths: List of subpaths (either from path dict or current subpath)
            subpath_type: Type of subpath ('lines', 'bezier', 'rectangle')
            close_subpath: Whether subpath is already closed

        Returns:
            List of buffered polygon geometries
        """
        path_geo = []

        if subpath_type == 'lines':
            for subp in subpaths:
                geo = copy(subp)
                if close_subpath is False:
                    geo.append(geo[0])
                try:
                    geo_el = Polygon(geo).buffer(
                        self.BUFFER_EPSILON,
                        quad_segs=self._quad_segs(self.BUFFER_EPSILON)
                    )
                    path_geo.append(geo_el)
                except ValueError:
                    pass

        elif subpath_type == 'bezier':
            geo = []
            for subp in subpaths:
                for b in subp:
                    geo += self.bezier_to_points(start=b[0], c1=b[1], c2=b[2], stop=b[3])
                    if close_subpath is False:
                        geo.append(geo[0])
                    try:
                        geo_el = Polygon(geo).buffer(
                            self.BUFFER_EPSILON,
                            quad_segs=self._quad_segs(self.BUFFER_EPSILON)
                        )
                        path_geo.append(geo_el)
                    except ValueError:
                        pass

        elif subpath_type == 'rectangle':
            for subp in subpaths:
                geo = copy(subp)
                try:
                    geo_el = Polygon(geo).buffer(
                        self.BUFFER_EPSILON,
                        quad_segs=self._quad_segs(self.BUFFER_EPSILON)
                    )
                    path_geo.append(geo_el)
                except ValueError:
                    pass

        return path_geo

    def _stroke_path(self, subpaths: List[dict], line_width: float, clear_stroke: bool,
                     stroke_color, ctx: ParsingContext, aperture: int) -> int:
        """Handle 'S'/'s' - stroke every subpath of the current path."""
        geometries = self._build_stroke_geometry(subpaths, line_width)
        if not geometries:
            return aperture

        ctm = subpaths[0]['ctm'] if subpaths else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        applied_size = self._aperture_size(line_width, ctm)
        return self._store_stroke_geometry(
            geometries, clear_stroke, ctx, aperture, applied_size, stroke_color
        )

    def _fill_path(self, subpaths: List[dict], even_odd: bool, clear_fill: bool,
                   fill_color, ctx: ParsingContext) -> None:
        """Handle 'f'/'F'/'f*' - fill every subpath of the current path."""
        geometry = self._build_fill_geometry(subpaths, even_odd)
        if geometry.is_empty:
            return

        ctm = subpaths[0]['ctm'] if subpaths else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        applied_size = self._aperture_size(0.0, ctm)
        self._store_fill_geometry(geometry, clear_fill, ctx, applied_size, fill_color)

    def _fill_stroke_path(self, subpaths: List[dict], even_odd: bool, clear_fill: bool,
                          clear_stroke: bool, line_width: float, fill_color, stroke_color,
                          ctx: ParsingContext, aperture: int) -> int:
        """Handle 'B'/'B*'/'b'/'b*' - fill then stroke the current path.

        Fill and stroke colors are independent, and the fill is always painted first.
        """
        fill_geometry = self._build_fill_geometry(subpaths, even_odd)
        if not fill_geometry.is_empty:
            ctm = subpaths[0]['ctm'] if subpaths else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
            self._store_fill_geometry(
                fill_geometry, clear_fill, ctx, self._aperture_size(0.0, ctm), fill_color
            )

        stroke_geometry = self._build_stroke_geometry(subpaths, line_width)
        if stroke_geometry:
            ctm = subpaths[0]['ctm'] if subpaths else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
            aperture = self._store_stroke_geometry(
                stroke_geometry, clear_stroke, ctx, aperture,
                self._aperture_size(line_width, ctm), stroke_color
            )

        return aperture

    @staticmethod
    def _drop_repeated_solids(layer: dict) -> None:
        """Drop the solids that repeat an earlier equal solid of the same aperture.

        Some CAD programs draw the same item more than once (one pass for each
        layer set). A copy is kept if a clear between the 2 copies touches it:
        then the copy restores an island.
        """
        elements = sorted(((aperture_id, element) for aperture_id, aperture in layer.items()
                           for element in aperture['geometry']),
                          key=lambda item: item[1].get('order', 0))
        seen = {}
        clears = []
        repeated = set()
        for aperture_id, element in elements:
            if 'solid' not in element:
                clears.append(element['clear'])
                continue
            key = (aperture_id, element['solid'].wkb)
            if key in seen and not any(clear.intersects(element['solid'])
                                       for clear in clears[seen[key]:]):
                repeated.add(id(element))
                continue
            seen[key] = len(clears)
        if repeated:
            for aperture in layer.values():
                aperture['geometry'] = [element for element in aperture['geometry']
                                        if id(element) not in repeated]

    def _finalize_object_dict(self, object_dict: dict, apertures_dict: dict,
                               clear_apertures_dict: dict, layer_nr: int,
                               abort_flag: bool) -> dict:
        """Finalize the object dictionary by copying apertures and cleaning up empty layers.

        Args:
            object_dict: The object dictionary to finalize
            apertures_dict: Apertures dictionary to copy to object_dict
            clear_apertures_dict: Clear apertures dictionary
            layer_nr: Current layer number
            abort_flag: Abort flag from caller

        Returns:
            Finalized object_dict
        """
        if apertures_dict:
            object_dict[layer_nr] = deepcopy(apertures_dict)

        if clear_apertures_dict[0]['geometry']:
            object_dict[0] = deepcopy(clear_apertures_dict)

        for layer_nr, layer in object_dict.items():
            if layer_nr != 0:
                self._drop_repeated_solids(layer)

        empty_layers = [layer for layer in object_dict if not object_dict[layer]]
        for x in empty_layers:
            if x in object_dict:
                object_dict.pop(x)

        if abort_flag:
            raise grace

        return object_dict