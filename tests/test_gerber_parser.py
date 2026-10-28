#!/usr/bin/env python
"""
Automated tests for Gerber parser parse_lines() method.

REQUIRED before any refactoring of the Gerber parser state management.
These tests validate the scattered variables approach.
(ParserState dataclass approach was abandoned - see docs/plans/)

Run: python tests/test_gerber_parser.py
"""

import math
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appParsers.ParseGerber import Gerber
from appParsers.ParseHPGL2 import HPGL2
from camlib import ApertureMacro, flatten_shapely_geometry
from unittest import mock

import pytest
import shapely
from shapely import MultiPolygon, Point, Polygon


class MockLog:
    """Mock logger for testing."""
    def debug(self, msg):
        pass
    
    def warning(self, msg):
        pass
    
    def error(self, msg):
        pass
    
    def info(self, msg):
        pass


class MockInform:
    """Mock inform emitter for testing."""
    def emit(self, msg):
        pass


class MockApp:
    """Mock application object for Gerber parser testing."""
    def __init__(self):
        self.options = {
            'gerber_def_units': 'MM',
            'gerber_def_zeros': 'L',
            'gerber_circle_steps': 64,
            'gerber_simp_tolerance': 0.001,
            'gerber_simplification': True,
            'gerber_buffering': True,
            'gerber_extra_buffering': 0.0,
            'gerber_clean_apertures': True,
            'gerber_use_buffer_for_union': True,
            'global_tolerance': 0.01,
        }
        self.decimals = 4
        self.abort_flag = False
        self.log = MockLog()
        self.inform = MockInform()
        self.app_units = 'MM'
        
        class MockPlotCanvas:
            class new_shape_collection:
                def __init__(self, layers=1):
                    pass
            def __init__(self):
                pass
        self.plotcanvas = MockPlotCanvas()
        
        class MockProcContainer:
            def update_view_text(self, text):
                pass
        self.proc_container = MockProcContainer()


def get_gerber_parser():
    """Create a Gerber parser with mock app for testing."""
    app = MockApp()
    return Gerber(app)


class TestGerberParserBasic:
    """Basic Gerber parser tests."""
    
    def test_simple_line_d01(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOIN*%", "%ADD10C,10*%", "D10*", "G01 X0Y0D02*", "G01 X100Y100D01*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == ""
        print("[PASS] Simple line D01 test passed")
    
    def test_flash_d03(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOIN*%", "%ADD10C,10*%", "D10*", "X100Y100D03*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == ""
        print("[PASS] Flash D03 test passed")
    
    def test_region_g36_g37(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOIN*%", "%ADD10C,10*%", "D10*", "G01 X0Y0D02*", "G36*", "X100Y0D01*", "X100Y100D01*", "X0Y100D01*", "X0Y0D01*", "G37*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == ""
        print("[PASS] Region G36-G37 test passed")
    
    def test_d02_new_path(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOIN*%", "%ADD10C,10*%", "D10*", "G01 X0Y0D02*", "G01 X100Y0D01*", "G01 X50Y50D02*", "G01 X150Y150D01*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == ""
        print("[PASS] D02 new path test passed")
    
    def test_all_aperture_types(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOIN*%", "%ADD10C,10*%", "%ADD11R,10X20*%", "D10*", "G01 X0Y0D02*", "G01 X100Y0D01*", "D11*", "G01 X0Y50D02*", "G01 X100Y50D01*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == ""
        print("[PASS] All aperture types test passed")


class TestGerberParserEdgeCases:
    """Edge case tests for the Gerber parser."""
    
    def test_d02_before_g37(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOIN*%", "%ADD10C,10*%", "D10*", "G01 X0Y0D02*", "G36*", "X100Y0D01*", "X100Y100D01*", "G01 X0Y100D02*", "G37*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == ""
        print("[PASS] D02 before G37 edge case test passed")
    
    def test_empty_path_no_crash(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%ADD10C,10*%", "D10*", "G01 X0Y0D02*", "G01 X100Y100D02*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == "fail"
        print("[PASS] Empty path no crash test passed")
    
    def test_comments_ignored(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOIN*%", "%ADD10C,10*%", "D10*", "G04 This is a comment*", "G01 X0Y0D02*", "G04 Another comment*", "G01 X100Y100D01*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == ""
        print("[PASS] Comments ignored test passed")
    
    def test_polarity_change_with_pending_path(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX23Y23*%", "%ADD10C,10*%", "D10*", "G01 X0Y0D02*", "G01 X1000Y0D01*", "G01 X1000Y1000D01*", "%LPC*%", "G01 X2000Y0D01*", "M02*"]
        result = gbr.parse_lines(lines)
        assert result is None or result == ""
        print("[PASS] Polarity change with pending path test passed")


    def test_aperture_macro_without_modifiers(self):
        gbr = get_gerber_parser()
        macro = ApertureMacro('NO_MODIFIERS')
        macro.raw = '1,1,1,0,0*'
        gbr.aperture_macros['NO_MODIFIERS'] = macro

        gbr.aperture_parse('10', 'NO_MODIFIERS', None)

        assert gbr.tools[10]['modifiers'] == []
        assert not macro.make_geometry(gbr.tools[10]['modifiers']).is_empty

    def test_disjoint_aperture_macro_primitives_are_not_shifted(self):
        macro = ApertureMacro('DISJOINT')
        macro.raw = '1,1,1,0,0*1,1,1,10,0*'

        result = macro.make_geometry([])

        assert isinstance(result, MultiPolygon)
        assert result.bounds == (-0.5, -0.5, 10.5, 0.5)

    def test_flatten_shapely_geometry_is_recursive_and_ordered(self):
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        poly2 = Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])
        poly3 = Polygon([(4, 0), (5, 0), (5, 1), (4, 1)])
        multi = MultiPolygon([poly2, poly3])

        result = flatten_shapely_geometry([[[poly1]], [multi]])

        assert [geo.bounds for geo in result] == [
            poly1.bounds, poly2.bounds, poly3.bounds
        ]


class TestGerberParserUnits:
    """Test unit handling."""
    
    def test_inches_units(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOIN*%", "%ADD10C,10*%", "D10*", "G01X0Y0D02*", "G01X100Y100D01*", "M02*"]
        result = gbr.parse_lines(lines)
        assert gbr.units == 'IN'
        print("[PASS] Inches units test passed")
    
    def test_metric_units(self):
        gbr = get_gerber_parser()
        lines = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD10C,10*%", "D10*", "G01X0Y0D02*", "G01X10000Y10000D01*", "M02*"]
        result = gbr.parse_lines(lines)
        assert gbr.units == 'MM'
        print("[PASS] Metric units test passed")


class TestGerberPolarityLayers:
    """The polarity layers (%LPD*% / %LPC*%) are composed 1 time at the end of the file.
    The reference is the old sequential fold (Gerber._fold_layers)."""

    HEADER = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD10C,0.5*%", "%ADD11R,2X2*%", "%ADD12C,3*%",
              "%ADD13R,5X0.00000001*%", "G01*", "D10*"]

    @staticmethod
    def region(x0, y0, x1, y1):
        """A rectangular region, coordinates in 1e-4 mm."""
        return ["G36*", "X%dY%dD02*" % (x0, y0), "X%dY%dD01*" % (x1, y0), "X%dY%dD01*" % (x1, y1),
                "X%dY%dD01*" % (x0, y1), "X%dY%dD01*" % (x0, y0), "G37*"]

    @staticmethod
    def parser(**opts):
        app = MockApp()
        app.options.update({
            'gerber_buffering': 'full', 'gerber_use_buffer_for_union': True, 'gerber_circle_steps': 16,
            'gerber_simplification': False, 'gerber_extra_buffering': False,
        })
        app.options.update(opts)
        return Gerber(app)

    def parse(self, lines, legacy=False, **opts):
        gbr = self.parser(**opts)
        if legacy:
            with mock.patch.object(Gerber, '_compose_layers', lambda s, layers: s._fold_layers(layers)):
                ret = gbr.parse_lines(list(lines))
        else:
            ret = gbr.parse_lines(list(lines))
        return gbr, ret

    @staticmethod
    def parts(geo):
        if isinstance(geo, (list, tuple)):
            return list(geo)
        if hasattr(geo, 'geoms'):
            return list(geo.geoms)
        return [geo]

    def holes(self, geo):
        return sum(len(p.interiors) for p in self.parts(geo) if isinstance(p, Polygon))

    def union(self, geo):
        return shapely.unary_union([shapely.make_valid(p) for p in self.parts(geo)])

    def check(self, lines, **opts):
        """Parse with the composition and with the old fold; the results must be equal."""
        new, ret_new = self.parse(lines, **opts)
        old, ret_old = self.parse(lines, legacy=True, **opts)
        assert ret_new == ret_old, "return %r != %r" % (ret_new, ret_old)
        assert len(self.parts(new.solid_geometry)) == len(self.parts(old.solid_geometry)), "number of polygons"
        assert self.holes(new.solid_geometry) == self.holes(old.solid_geometry), "number of holes"
        diff = self.union(new.solid_geometry).symmetric_difference(self.union(old.solid_geometry)).area
        assert diff < 1e-9, "symmetric difference %g" % diff
        return new, old, ret_new

    def test_ends_in_lpc(self):
        lines = self.HEADER + self.region(0, 0, 100000, 100000) + ["%LPC*%"] + self.region(20000, 20000, 40000, 40000) + \
            ["M02*"]
        new, old, ret = self.check(lines)
        assert self.holes(new.solid_geometry) == 1
        print("[PASS] Polarity layers: ends in LPC")

    def test_ends_in_lpd(self):
        lines = self.HEADER + self.region(0, 0, 100000, 100000) + ["%LPC*%"] + self.region(20000, 20000, 60000, 60000) + \
            ["%LPD*%"] + self.region(30000, 30000, 40000, 40000) + ["M02*"]
        new, old, ret = self.check(lines)
        assert self.holes(new.solid_geometry) == 1
        assert len(self.parts(new.solid_geometry)) == 2
        print("[PASS] Polarity layers: ends in LPD")

    def test_repeated_lpd(self):
        lines = self.HEADER + ["%LPD*%"] + self.region(0, 0, 50000, 50000) + ["%LPD*%"] + \
            self.region(30000, 30000, 80000, 80000) + ["%LPD*%", "%LPC*%"] + self.region(40000, 40000, 45000, 45000) + \
            ["%LPD*%"] + self.region(90000, 0, 100000, 10000) + ["M02*"]
        self.check(lines)
        print("[PASS] Polarity layers: repeated LPD")


    def test_only_full_clear(self):
        lines = self.HEADER + self.region(10000, 10000, 20000, 20000) + ["%LPC*%"] + \
            self.region(0, 0, 100000, 100000) + ["%LPD*%", "M02*"]
        new, old, ret = self.check(lines)
        assert self.union(new.solid_geometry).area == 0
        print("[PASS] Polarity layers: only a full clear (return %r)" % (ret,))

    def test_leading_clear_layer(self):
        lines = self.HEADER + ["%LPC*%"] + self.region(0, 0, 30000, 30000) + ["%LPD*%"] + \
            self.region(20000, 20000, 80000, 80000) + ["M02*"]
        new, old, ret = self.check(lines)
        assert abs(self.union(new.solid_geometry).area - 6 * 6) < 1e-6
        print("[PASS] Polarity layers: leading clear layer")


    def test_dark_restores_cleared_copper(self):
        lines = self.HEADER + self.region(0, 0, 100000, 100000) + ["%LPC*%"] + \
            self.region(20000, 20000, 80000, 80000) + ["%LPD*%"] + self.region(40000, 40000, 60000, 60000) + \
            ["%LPC*%"] + self.region(45000, 45000, 55000, 55000) + ["%LPD*%", "D11*", "X50000Y50000D03*", "M02*"]
        new, old, ret = self.check(lines)
        print("[PASS] Polarity layers: a dark layer restores cleared copper")


GERBER_HEADER = "%FSLAX24Y24*%\n%MOMM*%\n"
# coordinates of FSLAX24: 10000 is 1 mm
ONE_MM = 10000


def parse_gerber_text(tmp_path, text, steps=128, encoding=None, **opts):
    """Parse a Gerber text as a file (the file path splits the text in commands). Returns (Gerber, ret, union)."""
    app = MockApp()
    app.options.update({
        'gerber_buffering': 'full', 'gerber_use_buffer_for_union': True, 'gerber_circle_steps': steps,
        'gerber_circle_steps_mode': 'fixed', 'gerber_simplification': False, 'gerber_extra_buffering': False,
    })
    app.options.update(opts)
    path = tmp_path / 'case.gbr'
    path.write_text(text, encoding=encoding)
    gbr = Gerber(app)
    ret = gbr.parse_file(str(path))
    solid = gbr.solid_geometry
    union = shapely.unary_union([shapely.make_valid(p) for p in (solid if isinstance(solid, list) else [solid])])
    return gbr, ret, union


def square_contour(x0, y0, x1, y1):
    return ["X%dY%dD02*" % (x0, y0), "X%dY%dD01*" % (x1, y0), "X%dY%dD01*" % (x1, y1), "X%dY%dD01*" % (x0, y1),
            "X%dY%dD01*" % (x0, y0)]


class TestGerberParserCorrectness:
    """Output of the parser for the inputs that were parsed wrongly (the numbers are in mm, FSLAX24)."""

    @staticmethod
    def check_area(union, expected, tolerance=1e-3):
        assert abs(union.area - expected) <= tolerance, "area %.6f, expected %.6f" % (union.area, expected)

    # a rectangular aperture sweeps its shape along the path, also along a bend and a diagonal
    @staticmethod
    def case_polyline():
        return ["X0Y0D02*", "X%dY0D01*" % (10 * ONE_MM), "X%dY%dD01*" % (10 * ONE_MM, 10 * ONE_MM)]

    @staticmethod
    def case_diagonal():
        return ["X0Y0D02*", "X%dY%dD01*" % (10 * ONE_MM, 10 * ONE_MM)]

    def test_tracks_and_strokes(self, tmp_path):
        # a rectangular aperture sweeps its shape along the path, also along a bend and a diagonal
        for track in (self.case_polyline(), self.case_diagonal()):
            text = GERBER_HEADER + "%ADD10R,1X1*%\nD10*\n" + "\n".join(track) + "\nM02*\n"
            gbr, ret, union = parse_gerber_text(tmp_path, text)
            assert ret is None
            self.check_area(union, 21.0, 1e-4)

        stroke = "D10*\nX0Y0D02*\nX%dY0D01*\n" % (10 * ONE_MM)
        circle = "%ADD10C,0.1*%\nD10*\n"
        cases = [
            # a macro stroke followed by D02, a polarity change and the end of the file does not fail
            ("%AMSQ*21,1,1,1,0,0,0*%\n%ADD10SQ*%\n%ADD11C,1*%\n" + stroke + "X0Y%dD02*\nD11*\nX0Y%dD03*\n"
             % (5 * ONE_MM, 5 * ONE_MM), 11.0 + 0.7854),
            ("%AMSQ*21,1,1,1,0,0,0*%\n%ADD10SQ*%\n" + stroke + "%LPC*%\n", 11.0),
            # an obround stroke is the sweep of the obround
            ("%ADD10O,1X2*%\n" + stroke, 20.0 + 0.7854 + 1.0),
            # a draw before any move starts at the origin
            ("%ADD10C,1*%\nD10*\nX100000Y0D01*\n", 10 + 0.7854),
            # I and J are ignored in a linear move
            ("%ADD10C,1*%\nD10*\nG01*\nX0Y0D02*\nX100000Y0I0J0D01*\n", 10 + 0.7854),
            # an arc without G74 or G75 is a single quadrant arc: quarter of a circle of radius 5
            (circle + "X50000Y0D02*\nG03X0Y50000I-50000J0D01*\n", 0.1 * 5 * 3.14159 / 2 + 0.00785),
        ]
        for body, expected in cases:
            gbr, ret, union = parse_gerber_text(tmp_path, GERBER_HEADER + body + "M02*\n")
            assert ret is None
            self.check_area(union, expected, 2e-3)

        # a single quadrant arc over 90 degrees (G74) is drawn
        text = GERBER_HEADER + "%ADD10C,0.1*%\nD10*\nG74*\nX50000Y0D02*\nG03X-50000Y0I50000J0D01*\nM02*\n"
        gbr, ret, union = parse_gerber_text(tmp_path, text)
        assert ret is None
        self.check_area(union, 3.14159 * 5 * 0.1 + 0.00785, 0.05)
        self.check_bounds(union, (-5.05, -0.05, 5.05, 5.05))

        # the track is exactly as wide as the aperture and 16 steps per circle make a 16 segment flash
        text = GERBER_HEADER + "%ADD10C,1*%\nD10*\nX0Y0D02*\nX100000Y0D01*\nX0Y50000D03*\nM02*\n"
        gbr, ret, union = parse_gerber_text(tmp_path, text, steps=16)
        assert ret is None
        assert union.bounds[1] == pytest.approx(-0.5, abs=1e-9)
        flash = parse_gerber_text(tmp_path, GERBER_HEADER + "%ADD10C,1*%\nD10*\nX0Y0D03*\nM02*\n", steps=16)[2]
        assert len(flash.exterior.coords) - 1 == 16

    def test_regions(self, tmp_path):
        # the contours of a region are closed by D02 (also one without coordinates) whatever the aperture is
        two_squares = square_contour(0, 0, ONE_MM, ONE_MM) + square_contour(5 * ONE_MM, 0, 6 * ONE_MM, ONE_MM)
        bare_d02 = square_contour(0, 0, 2 * ONE_MM, 2 * ONE_MM) + ["D02*", "X%dY0D01*" % ONE_MM,
                                                                    "X%dY%dD01*" % (ONE_MM, ONE_MM), "X0Y0D01*"]
        for aperture in ("C,1", "R,1X1"):
            for contours, expected in ((two_squares, 2.0), (bare_d02, 4.0)):
                text = GERBER_HEADER + "%%ADD10%s*%%\nD10*\nG36*\n" % aperture + "\n".join(contours) + "\nG37*\nM02*\n"
                gbr, ret, union = parse_gerber_text(tmp_path, text)
                assert ret is None
                self.check_area(union, expected, 1e-6)

        # a region that crosses itself keeps both parts
        bowtie = ["X0Y0D02*", "X%dY0D01*" % ONE_MM, "X0Y%dD01*" % ONE_MM, "X%dY%dD01*" % (ONE_MM, ONE_MM), "X0Y0D01*"]
        text = GERBER_HEADER + "G36*\n" + "\n".join(bowtie) + "\nG37*\nM02*\n"
        gbr, ret, union = parse_gerber_text(tmp_path, text)
        assert ret is None
        self.check_area(union, 0.5, 1e-5)
        assert all(polygon.is_valid for polygon in flatten_shapely_geometry(gbr.solid_geometry))

    def test_coordinate_formats_and_command_layout(self, tmp_path):
        flash = "%ADD10C,1*%\nD10*\n"
        cases = [
            # (header, body, expected bounds of the flashes)
            ("%FSLIX24Y24*%\n%MOMM*%\n", flash + "X100000Y0D03*\nX100000Y0D03*\n", (9.5, -0.5, 20.5, 0.5)),
            ("%FSLAX24Y24*%\n%MOMM*%\n", flash + "G91*\nX100000Y0D03*\nX100000Y0D03*\nG90*\nX0Y0D03*\n",
             (-0.5, -0.5, 20.5, 0.5)),
            ("%FSTAX24Y24*%\n%MOMM*%\n", flash + "X-1Y1D03*\n", (-10.5, 9.5, -9.5, 10.5)),
            (GERBER_HEADER, flash + "X1.5Y2.25D03*\n", (1, 1.75, 2, 2.75)),
        ]
        for header, body, bounds in cases:
            gbr, ret, union = parse_gerber_text(tmp_path, header + body + "M02*\n")
            assert ret is None
            assert union.bounds == pytest.approx(bounds, abs=1e-3)

        pad = "D10*\nX0Y0D03*\n"
        layouts = [
            # two extended commands on a line
            ("%ADD10C,1*%%ADD11C,2*%\nD11*\nX0Y0D03*\n", 3.1416),
            # an aperture definition and a polarity command split over lines
            ("%ADD10C,\n1*%\n%ADD11C,0.5*%\n" + pad + "%LPC\n*%\nD11*\nX0Y0D03*\n", 0.7854 - 0.1963),
            # the format and the units in one block
            ("%MOMM*FSLAX24Y24*%\n%ADD10C,1*%\n" + pad, 0.7854),
            # an attribute that shares its line with a flash
            ("%ADD10C,1*%\nD10*\n%TO.N,GND*%X0Y0D03*\n", 0.7854),
            # a word command split over lines
            ("%ADD10C,1*%\nD10*\nX0\nY0D03*\n", 0.7854),
        ]
        for body, expected in layouts:
            header = "" if body.startswith("%MOMM") else GERBER_HEADER
            gbr, ret, union = parse_gerber_text(tmp_path, header + body + "M02*\n")
            assert ret is None
            self.check_area(union, expected, 2e-3)

        # lower case commands
        text = ("%fslax24y24*%\n%momm*%\n%add10c,1*%\n%AMDot*1,1,0.5,0,0*%\n%add11Dot*%\nG04 Keep the Case*\nd10*\n"
                "x0y0d03*\nd11*\nx50000y0d03*\nm02*\n")
        gbr, ret, union = parse_gerber_text(tmp_path, text)
        assert ret is None
        self.check_area(union, 0.7854 + 0.19635, 5e-3)

    @staticmethod
    def check_bounds(union, bounds, tolerance=0.01):
        assert union.bounds == pytest.approx(bounds, abs=tolerance), "bounds %s, expected %s" % (union.bounds, bounds)

    def test_step_repeat_and_aperture_blocks(self, tmp_path):
        cases = [
            # step and repeat: 3 x 2 copies, 5 mm and 4 mm apart
            ("%ADD10C,1*%\nD10*\n%SRX3Y2I5J4*%\nX0Y0D03*\n%SR*%\n", 6 * 0.7854, (-0.5, -0.5, 10.5, 4.5)),
            # the polarity inside the block is repeated with the objects
            ("%ADD10R,2X2*%\n%ADD11C,0.5*%\nD10*\n%SRX2Y1I4J0*%\nX0Y0D03*\n%LPC*%\nD11*\nX0Y0D03*\n%LPD*%\n%SR*%\n",
             2 * (4 - 0.19635), (-1, -1, 5, 1)),
            # aperture block: only the flash of the block D-code draws it; a block can hold a block
            ("%ADD10C,1*%\n%ABD20*%\nD10*\nX0Y0D03*\nX20000Y0D03*\n%AB*%\nD20*\nX100000Y0D03*\n",
             2 * 0.7854, (9.5, -0.5, 12.5, 0.5)),
            ("%ADD10C,1*%\n%ABD20*%\nD10*\nX0Y0D03*\n%AB*%\n%ABD21*%\nD20*\nX0Y0D03*\nX20000Y0D03*\n%AB*%\n"
             "D21*\nX100000Y0D03*\n", 2 * 0.7854, (9.5, -0.5, 12.5, 0.5)),
            # a clear object in a block cuts the block only, not the objects outside of it
            ("%ADD10R,2X2*%\n%ADD11C,1*%\n%ABD20*%\nD10*\nX0Y0D03*\n%LPC*%\nD11*\nX0Y0D03*\n%AB*%\nD10*\nX0Y0D03*\n"
             "D20*\nX100000Y0D03*\n", 4 + 4 - 0.7854, (-1, -1, 11, 1)),
        ]
        for body, area, bounds in cases:
            gbr, ret, union = parse_gerber_text(tmp_path, GERBER_HEADER + body + "M02*\n")
            assert ret is None
            self.check_area(union, area, 5e-3)
            self.check_bounds(union, bounds)

    def test_load_mirror_rotate_scale(self, tmp_path):
        cases = [
            ("%ADD10C,1*%\nD10*\n%LS2.0*%\nX0Y0D03*\n", 4 * 0.7854, (-1, -1, 1, 1)),
            ("%ADD10R,2X1*%\nD10*\n%LR90*%\nX0Y0D03*\n", 2, (-0.5, -1, 0.5, 1)),
            # mirroring is done before the rotation
            ("%AMT*4,1,3,0,0,1,0,0,1,0,0,0*%\n%ADD10T*%\nD10*\n%LMX*%\n%LR90*%\nX0Y0D03*\n", 0.5, (-1, -1, 0, 0)),
            # the scale is also the width of a stroke
            ("%ADD10C,1*%\nD10*\n%LS2.0*%\nX0Y0D02*\nX100000Y0D01*\n", 20 + 3.1416, (-1, -1, 11, 1)),
            # a region is not scaled
            ("%LS2.0*%\nG36*\nX0Y0D02*\nX10000Y0D01*\nX10000Y10000D01*\nX0Y10000D01*\nX0Y0D01*\nG37*\n", 1,
             (0, 0, 1, 1)),
            # the load commands stay until they are changed
            ("%ADD10C,1*%\nD10*\n%LS2.0*%\nX0Y0D03*\n%LS1.0*%\nX100000Y0D03*\n", 5 * 0.7854, (-1, -1, 10.5, 1)),
        ]
        for body, area, bounds in cases:
            gbr, ret, union = parse_gerber_text(tmp_path, GERBER_HEADER + body + "M02*\n")
            assert ret is None
            self.check_area(union, area, 1e-2)
            self.check_bounds(union, bounds)

    def test_aperture_holes(self, tmp_path):
        for aperture, area in (("C,1X0.5", 0.7854 - 0.19635), ("R,2X2X1", 4 - 0.7854),
                               ("O,3X1X0.5", 2 + 0.7854 - 0.19635), ("P,2X4X0X0.5", 2 - 0.19635)):
            gbr, ret, union = parse_gerber_text(
                tmp_path, GERBER_HEADER + "%%ADD10%s*%%\nD10*\nX0Y0D03*\nM02*\n" % aperture)
            assert ret is None
            self.check_area(union, area, 1e-2)

    def test_deprecated_image_commands(self, tmp_path):
        for command, flash_x, area, bounds in (
                ("%OFA10B0*%", 0, 0.7854, (9.5, -0.5, 10.5, 0.5)),
                ("%IPNEG*%", 0, 1 - 0.7854, (-0.5, -0.5, 0.5, 0.5)),
                # SF scales the coordinates only: the flash moves, its diameter stays (spec 8.1.9)
                ("%SFA2B2*%", 50000, 0.7854, (9.5, -0.5, 10.5, 0.5)),
                ("%MIA1B0*%", 50000, 0.7854, (-5.5, -0.5, -4.5, 0.5)),
                ("%IR90*%", 50000, 0.7854, (-0.5, 4.5, 0.5, 5.5))):
            text = GERBER_HEADER + command + "\n%ADD10C,1*%\nD10*\nX" + str(flash_x) + "Y0D03*\nM02*\n"
            gbr, ret, union = parse_gerber_text(tmp_path, text)
            assert ret is None
            self.check_area(union, area, 1e-2)
            self.check_bounds(union, bounds)

    def test_attributes_are_kept(self, tmp_path):
        text = (GERBER_HEADER + "%TF.FileFunction,Copper,L1,Top*%\n%TF.Part,Single*%\n"
                "%TA.AperFunction,Conductor*%\n%ADD10C,1*%\n%TD*%\n%ADD11C,1*%\nD10*\n"
                "%TO.N,GND*%X0Y0D03*\nD11*\nX50000Y0D03*\nM02*\n")
        gbr, ret, union = parse_gerber_text(tmp_path, text)
        assert ret is None
        self.check_area(union, 2 * 0.7854, 5e-3)
        assert gbr.attributes['TF']['.FileFunction'] == ['Copper', 'L1', 'Top']
        assert gbr.attributes['TF']['.Part'] == ['Single']
        assert gbr.tools[10]['attributes']['.AperFunction'] == ['Conductor']
        assert not gbr.tools[11].get('attributes')
        assert gbr.file_function == ['Copper', 'L1', 'Top']

    def test_non_ascii_comment_and_attribute(self, tmp_path):
        # letters that are not ASCII in a comment or an attribute do not stop the parsing, whatever the code page of
        # the system is: UTF-8 is read as it is, a byte that is not UTF-8 (an older program) is replaced
        text = (GERBER_HEADER + "%TF.ProjectId,KARŞI_ŞASE,1,rev1*%\nG04 Katman: ÜST ŞASE, Ğ İ ı ü ç*\n"
                "%ADD10C,1*%\nD10*\nX0Y0D02*\nX100000Y0D01*\nM02*\n")
        for encoding in ('utf-8', 'cp1254'):
            gbr, ret, union = parse_gerber_text(tmp_path, text, encoding=encoding)
            assert ret is None
            self.check_area(union, 10 + 0.7854, 2e-3)
            project = gbr.attributes['TF']['.ProjectId']
            assert project[1:] == ['1', 'rev1'] and project[0].startswith('KAR')
            if encoding == 'utf-8':
                assert project[0] == 'KARŞI_ŞASE'

def run_macro(raw, modifiers=(), **kwargs):
    macro = ApertureMacro('M')
    macro.raw = raw
    return macro.make_geometry(list(modifiers), **kwargs)


# (macro, modifiers, area, (minx, miny, maxx, maxy)), the Ucamco Gerber spec section 4.12
MACRO_PRIMITIVES = [
    # rectangles: the optional rotation is omitted, 'x' is a multiplication, '/' a division
    ('21,1,1,2,0,0*', (), 2.0, (-0.5, -1.0, 0.5, 1.0)),
    ('21,1,1x2,1,0,0,0*', (), 2.0, (-1.0, -0.5, 1.0, 0.5)),
    ('21,1,$1x2,$2/4,0,0,0*', (1, 2), 1.0, (-1.0, -0.25, 1.0, 0.25)),
    ('22,1,2,1,-1,-0.5*', (), 2.0, (-1.0, -0.5, 1.0, 0.5)),
    ('21,1,2,1,0,0,90*', (), 2.0, (-0.5, -1.0, 0.5, 1.0)),
    # vector line, current (20) and deprecated (2) code, rotation about the macro origin
    ('20,1,0.2,0,0,1,0,90*', (), 0.2, (-0.1, 0.0, 0.1, 1.0)),
    ('2,1,0.2,0,0,1,0,90*', (), 0.2, (-0.1, 0.0, 0.1, 1.0)),
    # unary minus on a variable
    ('21,1,1,1,-$1,0,0*', (2,), 1.0, (-2.5, -0.5, -1.5, 0.5)),
    # outline with and without the optional rotation (n+1 points)
    ('4,1,3,0,0,1,0,1,1,0,0,0*', (), 0.5, (0.0, 0.0, 1.0, 1.0)),
    ('4,1,3,0,0,1,0,1,1,0,0,90*', (), 0.5, (-1.0, 0.0, 0.0, 1.0)),
    # polygon: the vertex count may be a float (it comes from a variable)
    ('5,1,$1,0,0,2,0*', (4,), 2.0, (-1.0, -1.0, 1.0, 1.0)),
    # a primitive that does not touch the others and has no area does not move or change the shape
    ('21,1,2,2,0,0,0*21,1,0,2,5,5,0*', (), 4.0, (-1.0, -1.0, 1.0, 1.0)),
    # exposure off cuts what is below
    ('21,1,4,4,0,0,0*21,0,2,2,0,0,0*', (), 12.0, (-2.0, -2.0, 2.0, 2.0)),
]


def test_aperture_macro_primitive_geometry():
    for raw, modifiers, area, bounds in MACRO_PRIMITIVES:
        geo = run_macro(raw, modifiers)
        assert geo.area == pytest.approx(area, abs=1e-9), raw
        assert geo.bounds == pytest.approx(bounds, abs=1e-9), raw


def test_aperture_macro_rotation_variables_and_circle_steps():
    # a circle is rotated about the macro origin
    centre = run_macro('1,1,0.5,1,0,90*').centroid
    assert (centre.x, centre.y) == pytest.approx((0.0, 1.0), abs=1e-9)

    # the circle follows the circle steps
    assert run_macro('1,1,2,0,0*').area == pytest.approx(math.pi, rel=0.005)
    assert run_macro('1,1,2,0,0*', steps_per_circle=8).area == pytest.approx(2 * math.sqrt(2), rel=1e-6)

    # $11 is not changed by the value of $1, a variable can be defined from another one
    mods = [1] + [0] * 8 + [3]
    geo = run_macro('$11=$10+1*$2=$11x2*$3=$2/4*1,1,$11,0,0*1,0,$3,0,0*', mods)
    assert geo.bounds == pytest.approx((-2.0, -2.0, 2.0, 2.0), abs=0.01)
    assert geo.area == pytest.approx(math.pi * (4 - 1), rel=0.01)

    # the rotation of a thermal turns the gaps
    unrotated, rotated = run_macro('7,0,0,1,0.6,0.2,0*'), run_macro('7,0,0,1,0.6,0.2,45*')
    on_axis = Point(0.4, 0)
    on_diagonal = Point(0.4 * math.cos(math.pi / 4), 0.4 * math.sin(math.pi / 4))
    assert not unrotated.contains(on_axis) and unrotated.contains(on_diagonal)
    assert rotated.contains(on_axis) and not rotated.contains(on_diagonal)

    # the rotation of a moire turns the crosshair
    unrotated = run_macro('6,0,0,1,0.1,0.1,1,0.05,1.2,0*')
    rotated = run_macro('6,0,0,1,0.1,0.1,1,0.05,1.2,45*')
    inside = Point(0.2, 0.2)
    assert not unrotated.contains(inside) and rotated.contains(inside)
    assert unrotated.bounds == pytest.approx((-0.6, -0.6, 0.6, 0.6), abs=1e-9)


def test_load_real_gerber_files():
    """Test loading actual Gerber files from test_files directory."""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    files_dir = os.path.join(test_dir, 'test_files')
    
    test_files = ['simple_line.gbr', 'region_test.gbr', 'flash_test.gbr']
    
    for filename in test_files:
        filepath = os.path.join(files_dir, filename)
        if os.path.exists(filepath):
            gbr = get_gerber_parser()
            result = gbr.parse_file(filepath)
            assert result is None or result != 'fail', f"{filename} failed to parse"
            print(f"[PASS] Loaded {filename}")
        else:
            print(f"[SKIP] {filename} not found")


def test_hpgl2_non_ascii_comment(tmp_path):
    # the HPGL2 parser reads its file like the Gerber parser: a comment that is not ASCII does not stop it
    text = 'IN;\nCO "ÜST ŞASE";\nSP1;\nPU;\nPA0,0;\nPD;\nPA4000,0;\nPA4000,4000;\nPU;\nPA0,0;\n'
    for encoding in ('utf-8', 'cp1254'):
        path = tmp_path / 'case.plt'
        path.write_text(text, encoding=encoding)
        app = MockApp()
        app.options['tools_mill_tooldia'] = 0.1
        hpgl = HPGL2(app)
        hpgl.parse_file(str(path))
        assert list(hpgl.solid_geometry.coords) == [(0, 0), (100, 0), (100, 100)]
        if encoding == 'utf-8':
            assert 'CO "ÜST ŞASE";' in hpgl.source_file


def run_all_tests():
    """Run all tests and report results."""
    import traceback
    
    test_classes = [TestGerberParserBasic, TestGerberParserEdgeCases, TestGerberParserUnits, TestGerberPolarityLayers]
    
    passed = 0
    failed = 0
    errors = []
    
    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith('test_'):
                test_name = f"{test_class.__name__}.{method_name}"
                try:
                    method = getattr(instance, method_name)
                    method()
                    passed += 1
                except AssertionError as e:
                    failed += 1
                    errors.append((test_name, str(e)))
                    print(f"[FAIL] {test_name}: {e}")
                except Exception as e:
                    failed += 1
                    errors.append((test_name, traceback.format_exc()))
                    print(f"[ERROR] {test_name}: {e}")
    
    print("\n--- Integration Tests ---")
    try:
        test_load_real_gerber_files()
        passed += 1
    except AssertionError as e:
        failed += 1
        errors.append(("test_load_real_gerber_files", str(e)))
        print(f"[FAIL] test_load_real_gerber_files: {e}")
    except Exception as e:
        failed += 1
        errors.append(("test_load_real_gerber_files", traceback.format_exc()))
        print(f"[ERROR] test_load_real_gerber_files: {e}")
    
    print(f"\n{'='*60}")
    print(f"Test Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    
    if errors:
        print("\nFailures:")
        for test_name, error in errors:
            print(f"  - {test_name}: {error}")
    
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
